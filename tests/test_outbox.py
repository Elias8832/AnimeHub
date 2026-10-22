"""Outbox retries (Decision 34): an AniList outage keeps queued writes until
AniList is back; only rows AniList rejects run out of attempts."""

import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import support
from animehub import anilist, database, library, player, progress, server, settings, sync
from animehub.config import config, CONFIG, apply_config


class Retries(unittest.TestCase):
    def setUp(self):
        base = support.TMP / "outbox"
        apply_config(dict(CONFIG, data_dir=base / "data"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        database.init_db()
        with database.db() as c:
            c.execute("DELETE FROM outbox")
        sync.outbox_enqueue(5114, patch={"progress": 3})
        stubs = (mock.patch.object(anilist, "auth_status", return_value={"connected": True}),
                 mock.patch.object(anilist, "load_token", return_value={}))
        for s in stubs:
            s.start()
            self.addCleanup(s.stop)

    def flush_until_done(self, error):
        """Flush as if every backoff had elapsed, well past the attempt cap."""
        with mock.patch.object(anilist, "anilist_entry_save", side_effect=error):
            for _ in range(sync.OUTBOX_MAX_ATTEMPTS * 2):
                with database.db() as c:
                    c.execute("UPDATE outbox SET next_try=0")
                sync.outbox_flush()

    def test_outage_keeps_the_row(self):
        self.flush_until_done(anilist.AniListTransientError("AniList unavailable (HTTP 403)", status=403))
        self.assertEqual(sync.outbox_count(), 1)
        with database.db() as c:
            attempts, next_try = c.execute("SELECT attempts, next_try FROM outbox").fetchone()
        self.assertEqual(attempts, sync.OUTBOX_MAX_ATTEMPTS * 2)
        # Waits at the backoff cap, never longer.
        self.assertAlmostEqual(next_try - time.time(), sync.OUTBOX_BACKOFF[-1], delta=5)
        # AniList is back: the write goes out and leaves the queue.
        with database.db() as c:
            c.execute("UPDATE outbox SET next_try=0")
        with mock.patch.object(anilist, "anilist_entry_save") as save:
            self.assertEqual(sync.outbox_flush(), 1)
        save.assert_called_once_with(5114, {"progress": 3})
        self.assertEqual(sync.outbox_count(), 0)

    def test_rejected_row_is_dropped(self):
        self.flush_until_done(ValueError("AniList mutation failed: bad progress"))
        self.assertEqual(sync.outbox_count(), 0)

    def test_mutation_403_is_transient(self):
        body = io.BytesIO(b'{"errors":[{"message":"The AniList API has been temporarily disabled"}]}')
        err = anilist.HTTPError(anilist.ANILIST_URL, 403, "Forbidden", None, body)
        self.addCleanup(err.close)
        with mock.patch.object(anilist, "load_token", return_value={"access_token": "t"}), \
                mock.patch.object(anilist, "anilist_request", side_effect=err):
            with self.assertRaises(anilist.AniListTransientError):
                anilist.anilist_mutation("mutation {}")


class Verify(unittest.TestCase):
    """Progress queued without a fresh AniList list is checked against the
    live list before it is sent."""

    def setUp(self):
        Retries.setUp(self)
        with database.db() as c:
            c.execute("DELETE FROM outbox")

    def flush(self, entries, add=False, progress=5):
        sync.outbox_enqueue(5114, patch={"progress": progress}, verify={"add": add})
        with mock.patch.object(anilist, "anilist_cache_read", return_value=None), \
                mock.patch.object(anilist, "anilist_user_data", return_value={"entries": entries}), \
                mock.patch.object(anilist, "anilist_entry_save") as save:
            sync.outbox_flush()
        return save

    def test_sends_when_ahead(self):
        self.flush([{"id": 5114, "progress": 3}]).assert_called_once_with(5114, {"progress": 5})
        self.assertEqual(sync.outbox_count(), 0)

    def test_never_lowers_progress(self):
        self.flush([{"id": 5114, "progress": 8}]).assert_not_called()
        self.assertEqual(sync.outbox_count(), 0)

    def test_unlisted_needs_auto_add(self):
        self.flush([]).assert_not_called()
        self.flush([], add=True).assert_called_once_with(5114, {"status": "CURRENT", "progress": 5})
        self.assertEqual(sync.outbox_count(), 0)

    def test_list_unreachable_keeps_the_row(self):
        sync.outbox_enqueue(5114, patch={"progress": 5}, verify={"add": False})
        with mock.patch.object(anilist, "anilist_cache_read", return_value=None), \
                mock.patch.object(anilist, "anilist_user_data", side_effect=OSError("403")), \
                mock.patch.object(anilist, "anilist_entry_save") as save:
            sync.outbox_flush()
        save.assert_not_called()
        self.assertEqual(sync.outbox_count(), 1)

    def test_track_without_list_queues(self):
        show = {"seasons": [{"releases": [{"id": 5114, "first_ep": 1, "last_ep": None, "offset": 0, "total": 12}],
                             "episodes": [{"id": "a", "number": 1, "watched": 1}, {"id": "b", "number": 2, "watched": 1},
                                          {"id": "c", "number": 3, "watched": 0}]}]}
        from animehub import progress
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
                mock.patch.object(anilist, "anilist_cache_read", return_value=None):
            self.assertEqual(progress.anilist_track_show(show, manual=True), {"progress": 2})
        with database.db() as c:
            payload = c.execute("SELECT payload FROM outbox").fetchone()[0]
        self.assertIn('"verify": {"add"', payload)


class Deferred(unittest.TestCase):
    """TODO 59: marks on a multi-season folder whose franchise isn't built
    yet are kept until its seasons map to entries."""

    def setUp(self):
        Retries.setUp(self)
        with database.db() as c:
            c.execute("DELETE FROM outbox")

    def show(self, mapped=False):
        rel = [{"id": 5114, "first_ep": 1, "last_ep": None, "offset": 0, "total": 12}] if mapped else []
        eps = lambda k: [{"id": f"{k}{n}", "number": n, "watched": int(n <= 2)} for n in (1, 2, 3)]
        return {"id": "fold", "meta": {"anilist_id": 100},
                "seasons": [{"label": "Season 1", "releases": rel, "episodes": eps("a")},
                            {"label": "Season 2", "releases": [], "episodes": eps("b")}]}

    def track(self, only, franchise=()):
        from animehub import mapping, progress
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
                mock.patch.object(anilist, "anilist_cache_read", return_value=None), \
                mock.patch.object(mapping, "franchise_entries", return_value={100: list(franchise)}):
            return progress.anilist_track_show(self.show(), manual=True, only=only)

    def rows(self):
        with database.db() as c:
            return [json.loads(p) for (p,) in c.execute("SELECT payload FROM outbox ORDER BY id")]

    def test_marks_wait_in_one_row(self):
        self.assertEqual(self.track({"a1"}), {"later": True})
        self.track({"b1", "b2"})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["resync"], rows[0]["eps"]), ("fold", ["a1", "b1", "b2"]))

    def test_nothing_deferred_once_mapped(self):
        # The franchise exists: an episode no entry holds is local by design.
        self.assertIsNone(self.track({"b1"}, franchise=[{"id": 100}]))
        self.assertEqual(self.rows(), [])

    def test_replay_once_seasons_map(self):
        from animehub import library, mapping
        self.track({"a1", "a2"})
        with database.db() as c:
            c.execute("UPDATE outbox SET next_try=0")
        with mock.patch.object(library, "media_by_id", return_value=self.show(mapped=True)), \
                mock.patch.object(anilist, "anilist_connected", return_value=True), \
                mock.patch.object(anilist, "anilist_cache_read", return_value=None):
            with mock.patch.object(mapping, "franchise_entries", return_value={100: []}):
                sync.outbox_flush()
            self.assertIn("resync", self.rows()[0])  # still waiting
            with database.db() as c:
                c.execute("UPDATE outbox SET next_try=0")
            with mock.patch.object(mapping, "franchise_entries", return_value={100: [{"id": 100}]}):
                sync.outbox_flush()
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["patch"], rows[0]["verify"]), ({"progress": 2}, {"add": settings.automation_enabled("auto_add_watch")}))


class Reconnect(unittest.TestCase):
    """TODO 14: held writes go out as soon as AniList answers again."""

    def setUp(self):
        Retries.setUp(self)
        with database.db() as c:
            c.execute("DELETE FROM outbox")
        sync.outbox_enqueue(5114, patch={"progress": 3})
        sync.outbox_enqueue(5115, patch={"progress": 4})

    def held(self):
        with database.db() as c:
            return c.execute("SELECT media_id, attempts, next_try > ? FROM outbox ORDER BY id", (time.time(),)).fetchall()

    def test_outage_ends_the_pass(self):
        down = anilist.AniListTransientError("AniList unavailable (HTTP 403)", status=403)
        with mock.patch.object(anilist, "anilist_entry_save", side_effect=down) as save:
            sync.outbox_flush()
        save.assert_called_once()  # the second row isn't tried against a dead API
        self.assertEqual(self.held(), [(5114, 1, 1), (5115, 0, 0)])

    def test_back_online_skips_the_backoff(self):
        down = anilist.AniListTransientError("x", status=403)
        with mock.patch.object(anilist, "anilist_entry_save", side_effect=down):
            seen = sync.outbox_tick(sync.outbox_tick(anilist.last_ok))  # one row per pass
        self.assertEqual(self.held(), [(5114, 1, 1), (5115, 1, 1)])
        with mock.patch.object(anilist, "anilist_entry_save") as save:
            sync.outbox_tick(seen)  # nothing answered yet: still backing off
            save.assert_not_called()
            with mock.patch.object(anilist, "last_ok", seen + 1):
                sync.outbox_tick(seen)  # AniList answered: everything goes now
        self.assertEqual(save.call_count, 2)
        self.assertEqual(sync.outbox_count(), 0)


class Listing(unittest.TestCase):
    """TODO 14: the sync chip lists queued changes and can cancel one."""

    def setUp(self):
        Retries.setUp(self)
        with database.db() as c:
            c.execute("DELETE FROM outbox")
            c.execute("DELETE FROM shows_meta")
            c.execute("INSERT INTO shows_meta(show_id, anilist_id, title) VALUES('fold', 5114, 'Frieren')")

    def test_items_say_what_and_why(self):
        sync.outbox_enqueue(5114, patch={"progress": 3})
        sync.outbox_enqueue(9999, patch={"status": "COMPLETED"}, favourite=True)
        sync.outbox_defer("fold", ["b1", "b2"], True)
        with mock.patch.object(anilist, "anilist_entry_save",
                               side_effect=anilist.AniListTransientError("x", status=403)):
            sync.outbox_flush()
        with mock.patch.object(anilist, "anilist_cache_stale", return_value=None):
            a, b, c = sync.outbox_items()
        self.assertEqual((a["title"], a["patch"], a["state"]), ("Frieren", {"progress": 3}, "retrying"))
        self.assertGreater(a["retry_in"], 0)
        self.assertEqual((b["title"], b["favourite"], b["state"]), ("AniList entry 9999", True, "waiting"))
        self.assertEqual((c["title"], c["eps"], c["state"], c["media_id"]), ("Frieren", 2, "seasons", None))

    def test_cancel_drops_only_that_row(self):
        sync.outbox_enqueue(5114, patch={"progress": 3})
        sync.outbox_enqueue(5115, patch={"progress": 4})
        first = sync.outbox_items()[0]["id"]
        self.assertTrue(sync.outbox_cancel(first))
        self.assertFalse(sync.outbox_cancel(first))
        self.assertEqual([it["media_id"] for it in sync.outbox_items()], [5115])


TOKEN = {"access_token": "tok", "expires_at": None, "user_id": 42, "user_name": "Elias"}


class OutboxFlushTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            mock.patch.object(config, "LIBRARY", None),
            mock.patch.object(anilist, "load_token", lambda: dict(TOKEN)),
            mock.patch.object(anilist, "auth_status",
                         lambda: {"connected": True, "user": "Elias", "user_id": 42}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def test_successful_flush_removes_the_row(self):
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        self.assertEqual(sync.outbox_count(), 1)
        with mock.patch.object(anilist, "anilist_entry_save",
                          return_value={"media_id": 11061, "entry": {}}) as sv:
            sent = sync.outbox_flush()
        self.assertEqual(sent, 1)
        self.assertEqual(sync.outbox_count(), 0)
        sv.assert_called_once_with(11061, {"status": "COMPLETED"})

    def test_newer_progress_folds_into_the_queued_row(self):
        """Offline E5 then E6: the queued progress-only row is raised, not
        left at 5 while 6 is dropped."""
        sync.outbox_enqueue(11061, patch={"progress": 5})
        self.assertTrue(sync.outbox_raise_progress(11061, 6))
        self.assertFalse(sync.outbox_raise_progress(11061, 4))  # never lowers
        self.assertEqual(sync.outbox_count(), 1)
        with database.db() as c:
            payload = json.loads(c.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"], {"progress": 6})

    def test_user_edits_in_the_queue_are_not_rewritten(self):
        sync.outbox_enqueue(11061, patch={"progress": 3, "status": "PAUSED"})
        self.assertFalse(sync.outbox_raise_progress(11061, 6))
        with database.db() as c:
            payload = json.loads(c.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"], {"progress": 3, "status": "PAUSED"})

    def test_favourite_intents_flush_through_the_favourite_path(self):
        sync.outbox_enqueue(11061, favourite=True)
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=AssertionError("entry path must not run")), \
             mock.patch.object(anilist, "anilist_favourite_set",
                          return_value={"media_id": 11061, "is_favourite": True}):
            sent = sync.outbox_flush()
        self.assertEqual(sent, 1)
        self.assertEqual(sync.outbox_count(), 0)

    def test_transient_failure_backs_off_and_keeps_the_row(self):
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListTransientError("offline")):
            sent = sync.outbox_flush()
        self.assertEqual(sent, 0)
        self.assertEqual(sync.outbox_count(), 1)
        with database.db() as c:
            row = c.execute("SELECT attempts, next_try, last_error FROM outbox").fetchone()
        self.assertEqual(row[0], 1)
        self.assertGreater(row[1], 0)  # next_try pushed into the future
        self.assertIn("offline", row[2])

    def test_fifo_order_is_preserved(self):
        """Two status flips queued offline must reach AniList in the order
        they happened locally — the later one is the final state."""
        order = []
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=lambda mid, p: order.append(p["status"]) or {}):
            sync.outbox_enqueue(11061, patch={"status": "CURRENT"})
            sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
            sync.outbox_flush()
        self.assertEqual(order, ["CURRENT", "COMPLETED"])

    def test_auth_failure_stops_the_pass_without_consuming_attempts(self):
        """Dormant (Decision 24): the queue holds; rows keep attempts=0 so
        the backoff ladder is untouched when a valid token returns."""
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        sync.outbox_enqueue(11061, favourite=True)
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListAuthError("not connected")):
            sent = sync.outbox_flush()
        self.assertEqual(sent, 0)
        self.assertEqual(sync.outbox_count(), 2)
        with database.db() as c:
            attempts = [r[0] for r in c.execute("SELECT attempts FROM outbox ORDER BY id")]
        self.assertEqual(attempts, [0, 0])

    def test_account_mismatch_rows_are_skipped_not_sent(self):
        """Decision 24's hard line: account A's activity never reaches
        account B — mid-flush mismatches are skipped (auth_finish discards
        them with a warning at connect time)."""
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})  # bound to 42
        other = dict(TOKEN); other["user_id"] = 777
        with mock.patch.object(anilist, "load_token", lambda: dict(other)), \
             mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=AssertionError("must not send")):
            sent = sync.outbox_flush()
        self.assertEqual(sent, 0)
        self.assertEqual(sync.outbox_count(), 1)  # held, not sent, not lost

    def test_rejected_mutation_backs_off_and_keeps_the_row(self):
        """Regression (found live 2026-10-16): anilist_entry_save used to
        swallow the chokepoint's rejection into an error dict, which the
        flush treated as success and DELETED the row — a silently lost
        intent. The rejection now propagates: the row backs off and
        retries (the local change stays either way)."""
        sync.outbox_enqueue(11061, patch={"progress": 148})
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=ValueError("AniList mutation failed: progress invalid")):
            sent = sync.outbox_flush()
        self.assertEqual(sent, 0)
        self.assertEqual(sync.outbox_count(), 1)
        with database.db() as c:
            attempts, error = c.execute(
                "SELECT attempts, last_error FROM outbox").fetchone()
        self.assertEqual(attempts, 1)
        self.assertIn("progress invalid", error)


class OutboxAccountBindingTests(unittest.TestCase):
    """Decision 24: reconnect same account → flush; different → discard."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def enqueue_for(self, account_id):
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        with database.db() as c:
            c.execute("UPDATE outbox SET account_id=?", (account_id,))

    def test_same_account_keeps_the_queue(self):
        self.enqueue_for(42)
        discarded = sync.outbox_discard_other_accounts(42)
        self.assertEqual(discarded, 0)
        self.assertEqual(sync.outbox_count(), 1)

    def test_different_account_discards_with_a_count(self):
        self.enqueue_for(42)
        discarded = sync.outbox_discard_other_accounts(777)
        self.assertEqual(discarded, 1)
        self.assertEqual(sync.outbox_count(), 0)


class OutboxRouteTests(unittest.TestCase):
    """/api/entry queues on transient/auth failures and answers queued-shape;
    /api/now carries the pending count for the chip."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.patches = [
            mock.patch.object(config, "DB", Path(cls.directory.name) / "hub.db"),
            mock.patch.object(config, "LIBRARY", None),
            mock.patch.object(config, "ANILIST_TOKEN_FILE",
                         Path(cls.directory.name) / "token.json"),
            mock.patch.object(config, "ANILIST_CACHE",
                         Path(cls.directory.name) / "anilist_cache.json"),
            # Connected state: the queue path only exists when a token is
            # present (without one the route takes the local fallback).
            mock.patch.object(anilist, "load_token", lambda: dict(TOKEN)),
            mock.patch.object(anilist, "auth_status",
                         lambda: {"connected": True, "user": "Elias",
                                  "user_id": 42}),
        ]
        for p in cls.patches:
            p.start()
        database.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.bound = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    def setUp(self):
        with database.db() as c:
            c.execute("DELETE FROM outbox")
            c.execute("DELETE FROM curation")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in reversed(cls.patches):
            p.stop()
        cls.directory.cleanup()

    def request(self, method, path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers={"Content-Type": "application/json",
                                    "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_transient_write_failure_queues_the_intent(self):
        """Offline simulation: the write fails transiently → the row is
        stored AND the local curation change is already committed (Decision
        3: nothing lost, no user action needed)."""
        self.assertTrue(anilist.load_token())  # connected in this test
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListTransientError("offline")):
            code, body = self.request("POST", "/api/entry", {
                "media_id": 11061, "patch": {"status": "COMPLETED", "score": 9}})
        self.assertEqual(code, 200)
        self.assertTrue(body["queued"])
        self.assertEqual(body["entry"]["status"], "COMPLETED")  # local overlay
        self.assertEqual(sync.outbox_count(), 1)
        with database.db() as c:
            payload = json.loads(c.execute(
                "SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"]["status"], "COMPLETED")
        self.assertEqual(payload["patch"]["score"], 9)
        self.assertEqual(payload["account_id"], 42)

    def test_auth_failure_queues_dormant(self):
        self.assertTrue(anilist.load_token())
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListAuthError("token expired")):
            code, body = self.request("POST", "/api/entry", {
                "media_id": 11061, "patch": {"score": 7}})
        self.assertEqual(code, 401)
        self.assertTrue(body["queued"])
        self.assertEqual(sync.outbox_count(), 1)

    def test_validation_failure_never_queues(self):
        self.assertTrue(anilist.load_token())
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListTransientError("offline")):
            code, body = self.request("POST", "/api/entry", {
                "media_id": 11061, "patch": {"status": "BEST_FRIEND"}})
        self.assertEqual(code, 400)
        self.assertEqual(sync.outbox_count(), 0)

    def test_now_carries_the_pending_count(self):
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        code, body = self.request("GET", "/api/now")
        self.assertEqual(body.get("outbox"), 1)
        progress._dismiss_completion  # noqa — existence guard for the pair route
        sync.outbox_flush  # noqa
        # cleanup for other tests
        with database.db() as c:
            c.execute("DELETE FROM outbox")

    def test_now_carries_the_next_prompt(self):
        """TODO 10: the online-only next episode waits in /api/now until
        dismissed."""
        player._next_prompt.update(id="stream:1:2", label="Season 1 · E2", show="X", ts=time.time())
        code, body = self.request("GET", "/api/now")
        self.assertEqual(body.get("next_prompt"), {"id": "stream:1:2", "label": "Season 1 · E2", "show": "X"})
        self.request("POST", "/api/next-prompt/dismiss", {})
        code, body = self.request("GET", "/api/now")
        self.assertNotIn("next_prompt", body)

    def test_now_while_playing_reports_the_session(self):
        """Regression: the playing branch read an unbound `a` and every
        /api/now poll during playback died with a NameError."""
        sync.outbox_enqueue(11061, patch={"status": "COMPLETED"})
        session = {"ep_id": "e1", "show_title": "Frieren", "label": "E3",
                   "percent": 42}
        vpn = {"active": False, "iface": None, "known": True}
        with mock.patch.dict(player._active, {"e1": session}, clear=True), \
                mock.patch.object(settings, "vpn_status", return_value=vpn):
            code, body = self.request("GET", "/api/now")
        with database.db() as c:
            c.execute("DELETE FROM outbox")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"playing": True, "show": "Frieren", "label": "E3",
                                "percent": 42, "episode_id": "e1", "loading": False,
                                "vpn": vpn, "outbox": 1})

    def test_completion_popup_writes_queue_too(self):
        """The completion popup saves through the same route — an offline
        'how was it?' must not lose the rating (Decision 3)."""
        self.assertTrue(anilist.load_token())
        with mock.patch.object(anilist, "anilist_entry_save",
                          side_effect=anilist.AniListTransientError("offline")):
            code, body = self.request("POST", "/api/entry", {
                "media_id": 11061, "patch": {"status": "COMPLETED", "score": 10}})
        self.assertTrue(body["queued"])
        cur = library.curation_get("aid:11061")
        self.assertEqual(cur["status"], "COMPLETED")
        self.assertEqual(cur["score"], 10)


if __name__ == "__main__":
    unittest.main()
