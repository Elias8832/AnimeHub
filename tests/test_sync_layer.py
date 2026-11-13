"""The sync and data layer's review fixes (2026-11-13): queued AniList writes
keep raised progress and their account, unreadable answers are an outage,
the MAL login outlives a 403, and stored data survives pruning and torn
writes."""

import http.client
import io
import json
import threading
import time
import unittest
import urllib.error
from unittest import mock

from tests import support
from animehub import anilist, database, library, mal, metadata, server, sync
from animehub.config import config, CONFIG, apply_config


class Base(unittest.TestCase):
    def setUp(self):
        apply_config(dict(CONFIG, data_dir=support.TMP / "sync-layer" / self._testMethodName / "data"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        database.init_db()
        anilist._cleared.update(id=None, at=0.0, data=None)
        self.addCleanup(anilist._cleared.update, id=None, at=0.0, data=None)

    def rows(self):
        with database.db() as c:
            return [(json.loads(p), a) for p, a in
                    c.execute("SELECT payload, account_id FROM outbox ORDER BY id")]


class Outbox(Base):
    def setUp(self):
        super().setUp()
        for s in (mock.patch.object(anilist, "auth_status", return_value={"connected": True}),
                  mock.patch.object(anilist, "load_token", return_value={})):
            s.start()
            self.addCleanup(s.stop)

    def test_progress_raised_during_the_send_is_not_deleted(self):
        sync.outbox_enqueue(5114, patch={"progress": 5})

        def save(media_id, patch):  # episode 6 ends while AniList is asked
            self.assertTrue(sync.outbox_raise_progress(5114, 6))

        with mock.patch.object(anilist, "anilist_entry_save", side_effect=save):
            sync.outbox_flush()
        self.assertEqual([r[0]["patch"] for r in self.rows()], [{"progress": 6}])
        with mock.patch.object(anilist, "anilist_entry_save") as save:
            sync.outbox_flush()
        save.assert_called_once_with(5114, {"progress": 6})
        self.assertEqual(sync.outbox_count(), 0)

    def test_progress_behind_the_users_own_edit_gets_its_own_row(self):
        sync.outbox_enqueue(5114, patch={"status": "PAUSED"})
        self.assertTrue(sync.outbox_queue_progress(5114, 3))
        self.assertEqual([r[0]["patch"] for r in self.rows()], [{"status": "PAUSED"}, {"progress": 3}])
        # Already on its way: nothing more.
        self.assertFalse(sync.outbox_queue_progress(5114, 2))
        self.assertTrue(sync.outbox_queue_progress(5114, 4))
        self.assertEqual([r[0]["patch"] for r in self.rows()], [{"status": "PAUSED"}, {"progress": 4}])


class Trim(Base):
    """A direct save is the newest word: queued rows don't send older values over it."""

    def enqueue(self, account, **kw):
        sync.outbox_enqueue(5114, account=account, **kw)

    def test_saved_fields_leave_queued_rows(self):
        self.enqueue(42, patch={"status": "PAUSED", "progress": 3})   # → empty: goes
        self.enqueue(42, patch={"progress": 8})                       # above the save: stays
        self.enqueue(42, patch={"score": 7}, favourite=True)          # untouched by a status save
        self.enqueue(99, patch={"status": "DROPPED"})                 # another account's
        self.assertEqual(sync.outbox_trim_saved(5114, {"status"}, 42, progress=5), 1)
        self.assertEqual([r[0]["patch"] for r in self.rows()],
                         [{"progress": 8}, {"score": 7}, {"status": "DROPPED"}])
        self.assertEqual(sync.outbox_trim_saved(5114, {"favourite"}, 42), 1)
        self.assertNotIn("favourite", self.rows()[1][0])
        self.assertEqual(sync.outbox_trim_saved(5114, {"score"}, 42), 1)  # left empty: goes
        self.assertEqual([r[0]["patch"] for r in self.rows()], [{"progress": 8}, {"status": "DROPPED"}])

    def test_a_row_changed_meanwhile_is_left(self):
        self.enqueue(42, patch={"status": "PAUSED"})
        real = database.db
        calls = []

        def racing():  # the flush raises the row between the read and the delete
            calls.append(1)
            if len(calls) == 2:
                with real() as c:
                    c.execute("UPDATE outbox SET payload=?", (json.dumps({"patch": {"status": "CURRENT"},
                                                                          "account_id": 42}),))
            return real()

        with mock.patch.object(database, "db", racing):
            self.assertEqual(sync.outbox_trim_saved(5114, {"status"}, 42), 0)
        self.assertEqual(self.rows()[0][0]["patch"], {"status": "CURRENT"})

    def test_the_entry_route_trims_after_its_save(self):
        self.enqueue(42, patch={"progress": 4})
        self.enqueue(42, patch={"status": "PAUSED"}, favourite=False)
        sent = []
        handler = object.__new__(server.Handler)
        handler.send_json = lambda *a, **k: sent.append(a)
        state = {"entry": {"status": "CURRENT", "progress": 6}, "is_favourite": True}
        with mock.patch.object(anilist, "account_present", return_value=True), \
                mock.patch.object(anilist, "account_id", return_value=42), \
                mock.patch.object(anilist, "anilist_entry_save", return_value=state), \
                mock.patch.object(anilist, "anilist_favourite_set", return_value=state), \
                mock.patch.object(library, "curation_set"):
            handler._entry_post({"media_id": 5114}, {"status": "CURRENT"}, True, "aid:5114",
                                {"status": "CURRENT"})
        self.assertEqual(sent[0][0], state)
        self.assertEqual(sync.outbox_count(), 0)


class Lists(Base):
    DATA = {"data": {"User": {"name": "new-name", "statistics": {"anime": {
        "episodesWatched": 0, "minutesWatched": 0, "meanScore": 0, "count": 0}}},
        "MediaListCollection": {"lists": []}}}

    def test_read_by_id_with_the_token(self):
        anilist.store_token("tok", user={"id": 42, "name": "old-name"})
        with mock.patch.object(anilist, "anilist_request", return_value=self.DATA) as req:
            anilist._anilist_lists()
        payload, token = req.call_args[0][0], req.call_args[1].get("token")
        self.assertEqual(payload["variables"], {"id": 42})
        self.assertIn("MediaListCollection(userId:$id", payload["query"])
        self.assertEqual(token, "tok")

    def test_a_refused_token_still_reads_the_public_lists(self):
        anilist.store_token("tok", user={"id": 42, "name": "a"})
        refused = urllib.error.HTTPError(anilist.ANILIST_URL, 400, "Bad Request", {}, io.BytesIO(b"{}"))
        with mock.patch.object(anilist, "anilist_request", side_effect=[refused, self.DATA]) as req:
            anilist._anilist_lists()
        self.assertIsNone(req.call_args[1].get("token"))

    def test_an_old_token_file_reads_by_name(self):
        config.ANILIST_TOKEN_FILE.write_text(json.dumps({"access_token": "tok", "user_name": "a"}), encoding="utf-8")
        with mock.patch.object(anilist, "anilist_request", return_value=self.DATA) as req:
            anilist._anilist_lists()
        self.assertEqual(req.call_args[0][0]["variables"], {"name": "a"})


class Account(Base):
    def test_a_write_whose_login_just_ended_keeps_its_account(self):
        anilist.store_token("tok", user={"id": 42, "name": "a"})
        refused = urllib.error.HTTPError(anilist.ANILIST_URL, 400, "Bad Request", {},
                                         io.BytesIO(b'{"errors":[{"message":"Invalid token"}]}'))
        with mock.patch.object(anilist, "anilist_request", side_effect=refused):
            with self.assertRaises(anilist.AniListAuthError):
                anilist.anilist_mutation("mutation{x}")
        self.assertIsNone(anilist.load_token())
        sync.outbox_enqueue(5114, patch={"progress": 3})  # what _entry_post then does
        self.assertEqual(self.rows()[0][1], 42)
        # Decision 24: another account's connect drops it, never sends it.
        self.assertEqual(sync.outbox_discard_other_accounts(99), 1)

    def test_a_disconnect_leaves_no_account_behind(self):
        anilist.store_token("tok", user={"id": 42, "name": "a"})
        anilist.auth_disconnect()
        self.assertIsNone(anilist.cleared_account_id())

    def test_the_account_read_before_the_call_wins(self):
        sync.outbox_enqueue(5114, patch={"progress": 3}, account="mal:7")
        self.assertEqual(self.rows()[0][1], "mal:7")


class Answers(Base):
    def test_unreadable_answers_are_an_outage(self):
        with mock.patch.object(anilist, "load_token", return_value={"access_token": "t"}):
            for error in (json.JSONDecodeError("x", "<html>", 0), http.client.IncompleteRead(b"")):
                with mock.patch.object(anilist, "anilist_request", side_effect=error):
                    with self.assertRaises(anilist.AniListTransientError):
                        anilist.anilist_mutation("mutation{x}")

    def test_a_403_on_refresh_keeps_the_mal_login(self):
        mal.store_token({"access_token": "old", "refresh_token": "r", "expires_at": 1, "user_id": 7})
        blocked = urllib.error.HTTPError(mal.TOKEN_URL, 403, "Forbidden", {}, None)
        with mock.patch.object(mal, "urlopen", side_effect=blocked):
            with self.assertRaises(anilist.AniListTransientError):
                mal._refresh("old")
        self.assertEqual(mal.load_token()["access_token"], "old")


class Stored(Base):
    def test_a_failed_refresh_keeps_the_stale_panel_read(self):
        key = anilist._state_key(5114)
        database.cache_put(key, {"media_id": 5114})
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts=0 WHERE key=?", (key,))
        with mock.patch.object(anilist, "anilist_entry_state", side_effect=OSError("offline")):
            anilist.entry_state_refresh_async(5114)
            for _ in range(200):
                if not anilist._entry_refreshing:
                    break
                time.sleep(0.01)
        self.assertEqual(database.cache_get_ts(key)[0], {"media_id": 5114})

    def test_a_cleared_score_reaches_the_cached_lists(self):
        config.ANILIST_CACHE.write_text(json.dumps({
            "user": {"name": "a"}, "_ts": time.time(),
            "entries": [{"id": 5114, "status": "CURRENT", "score": 8, "score100": 80}]}), encoding="utf-8")
        anilist.list_cache_apply(5114, {"score_format": "POINT_10",
                                        "entry": {"status": "CURRENT", "score": None}})
        row = json.loads(config.ANILIST_CACHE.read_text(encoding="utf-8"))["entries"][0]
        self.assertEqual((row["score"], row["score100"]), (0, 0))

    def test_prune_keeps_the_rows_read_at_any_age(self):
        for key in ("malcat:v1:1", "idmal:1", "media-about:v3:1", "media:v1:id:1"):
            database.cache_put(key, {})
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts=0")
        self.assertEqual(database.cache_prune(), 1)
        with database.db() as c:
            left = {r[0] for r in c.execute("SELECT key FROM catalog_cache")}
        self.assertEqual(left, {"malcat:v1:1", "idmal:1", "media-about:v3:1"})

    def test_overrides_written_at_once_keep_every_match(self):
        threads = [threading.Thread(target=metadata.pin_match, args=(f"/anime/{i}", i)) for i in range(1, 21)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(metadata.load_overrides()), 20)
        self.assertEqual([p.name for p in config.DATA.glob("*.tmp")], [])

    def test_dead_code_is_gone(self):
        self.assertFalse(hasattr(anilist, "_cache_del"))
        self.assertFalse(hasattr(metadata, "polluted_playlist"))
        with mock.patch.object(anilist, "load_token", return_value=None), \
                mock.patch.object(mal, "load_token", return_value=None):
            self.assertEqual(anilist.anilist_user_data(), {"user": None, "entries": []})


if __name__ == "__main__":
    unittest.main()
