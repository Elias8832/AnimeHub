"""Outbox retries (Decision 34): an AniList outage keeps queued writes until
AniList is back; only rows AniList rejects run out of attempts."""

import io
import json
import time
import unittest
from unittest import mock

from tests import support
from animehub import anilist, database, settings, sync
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


if __name__ == "__main__":
    unittest.main()
