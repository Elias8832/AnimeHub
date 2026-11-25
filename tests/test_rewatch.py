"""Active rewatch runs (Decision 18.3): tracking rewatch progress separately
without erasing permanent watched facts, multi-season handoff, auto AniList/MAL
Completed -> Rewatching sync, API validation, and PC <-> Android peer sync."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support
from animehub import anilist, database, franchise, library, pages, peersync, player, progress, server, settings, sync
from animehub.config import CONFIG, apply_config, config


def _media(mid, title, episodes=3, sequel=None, prequel=None):
    edges = []
    if sequel:
        edges.append({"relationType": "SEQUEL", "node": {"id": sequel, "type": "ANIME", "format": "TV"}})
    if prequel:
        edges.append({"relationType": "PREQUEL", "node": {"id": prequel, "type": "ANIME", "format": "TV"}})
    return {
        "id": mid,
        "title": {"english": title, "romaji": title},
        "format": "TV",
        "status": "FINISHED",
        "episodes": episodes,
        "duration": 24,
        "startDate": {"year": 2024, "month": 1, "day": 1},
        "coverImage": {"extraLarge": None, "color": None},
        "bannerImage": None,
        "relations": {"edges": edges},
    }


def _link_stream(mid, title, count):
    with database.db() as c:
        c.execute(
            """INSERT OR REPLACE INTO stream_links(media_id, query, title, idx, mode, episodes, episodes_ts, meta, updated_at)
               VALUES(?,?,?,?,?,?,strftime('%s','now'),'{}',datetime('now'))""",
            (mid, title, title, 1, "sub", json.dumps([str(i) for i in range(1, count + 1)])),
        )


def _mark_stream_watched(mid, count):
    with database.db() as c:
        for i in range(1, count + 1):
            c.execute(
                "INSERT OR REPLACE INTO episodes(id, watched, position, duration, last_played)"
                " VALUES(?, 1, 1440, 1440, '2025-01-01 12:00:00')",
                (f"stream:{mid}:{i}",),
            )


class RewatchBackendTests(unittest.TestCase):
    def setUp(self):
        base = support.TMP / f"rewatch_{self._testMethodName}"
        apply_config(dict(CONFIG, data_dir=base / "data", library_root=base / "lib"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        config.LIBRARY.mkdir(parents=True, exist_ok=True)
        database.init_db()
        franchise._story_memo.update(sig=None, next={})
        with database.db() as c:
            for tbl in ("episodes", "rewatches", "outbox", "stream_links", "media", "franchises", "franchise_members", "completion_events"):
                c.execute(f"DELETE FROM {tbl}")
        self.s1 = _media(140960, "SPY x FAMILY", episodes=3, sequel=158927)
        self.s2 = _media(158927, "SPY x FAMILY Season 2", episodes=3, prequel=140960)
        franchise.store_franchise([self.s1, self.s2])
        _link_stream(140960, "SPY x FAMILY", 3)
        _link_stream(158927, "SPY x FAMILY Season 2", 3)
        _mark_stream_watched(140960, 3)
        _mark_stream_watched(158927, 3)

    def test_start_mark_end_and_restore_preserves_watched_facts(self):
        rw = progress.rewatch_start("aid:140960")
        self.assertEqual(rw["done"], [])
        self.assertIsNotNone(progress.rewatch_get("aid:140960"))

        updated = progress.rewatch_mark("aid:140960", [1, 2], watched=True)
        self.assertEqual(updated["done"], [1, 2])
        updated = progress.rewatch_mark("aid:140960", [2], watched=False)
        self.assertEqual(updated["done"], [1])
        # Simulate a mid-episode pause on E02 during the rewatch.
        player.save_position("stream:140960:2", 600, 1440)

        prev = progress.rewatch_end("aid:140960")
        self.assertEqual(prev["done"], [1])
        self.assertIsNone(progress.rewatch_get("aid:140960"))

        # Original watched facts were never erased, and E02's mid-rewatch pause cleared.
        with database.db() as c:
            rows = c.execute("SELECT id, watched, position FROM episodes WHERE id LIKE 'stream:140960:%' ORDER BY id").fetchall()
        self.assertEqual([r[1] for r in rows], [1, 1, 1])
        self.assertEqual(rows[1][2], 0)

        # Undo restores the exact previous run.
        restored = progress.rewatch_start("aid:140960", restore=prev)
        self.assertEqual(restored["done"], [1])
        self.assertEqual(restored["started_at"], prev["started_at"])

    def test_finish_episode_advances_rewatch_and_flips_completed_to_repeating(self):
        progress.rewatch_start("aid:140960")
        remote_cache = {"entries": [{"id": 140960, "status": "COMPLETED", "progress": 3, "episodes": 3, "title": "SPY x FAMILY"}]}
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
             mock.patch.object(anilist, "anilist_cache_read", return_value=remote_cache), \
             mock.patch.object(anilist, "account_id", return_value=42), \
             mock.patch.object(settings, "automation_enabled", side_effect=lambda k: k == "auto_progress_sync"):
            ok = player.finish_episode("stream:140960:1", 1400, 1440, True)
        self.assertTrue(ok)
        self.assertEqual(progress.rewatch_get("aid:140960")["done"], [1])
        with database.db() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox ORDER BY id")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patch"], {"status": "REPEATING", "progress": 1})

    def test_finish_episode_in_rewatch_syncs_repeating_progress_step_by_step(self):
        progress.rewatch_start("aid:140960")
        progress.rewatch_mark("aid:140960", [1], watched=True)
        remote_cache = {"entries": [{"id": 140960, "status": "REPEATING", "progress": 1, "episodes": 3, "title": "SPY x FAMILY"}]}
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
             mock.patch.object(anilist, "anilist_cache_read", return_value=remote_cache), \
             mock.patch.object(anilist, "account_id", return_value=42), \
             mock.patch.object(settings, "automation_enabled", side_effect=lambda k: k == "auto_progress_sync"):
            player.finish_episode("stream:140960:2", 1400, 1440, True)
        self.assertEqual(progress.rewatch_get("aid:140960")["done"], [1, 2])
        with database.db() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox ORDER BY id")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patch"], {"progress": 2})

    def test_finish_final_episode_ends_rewatch_and_chains_to_watched_sequel(self):
        progress.rewatch_start("aid:140960")
        progress.rewatch_mark("aid:140960", [1, 2], watched=True)
        remote_cache = {"entries": [{"id": 140960, "status": "REPEATING", "progress": 2, "episodes": 3, "title": "SPY x FAMILY"}]}
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
             mock.patch.object(anilist, "anilist_cache_read", return_value=remote_cache), \
             mock.patch.object(anilist, "account_id", return_value=42), \
             mock.patch.object(settings, "automation_enabled", side_effect=lambda k: k == "auto_progress_sync"):
            player.finish_episode("stream:140960:3", 1400, 1440, True)
        # Season 1 rewatch closed, Season 2 rewatch automatically started!
        self.assertIsNone(progress.rewatch_get("aid:140960"))
        s2_rw = progress.rewatch_get("aid:158927")
        self.assertIsNotNone(s2_rw)
        self.assertEqual(s2_rw["done"], [])
        # Finale progress 3 still synced to AniList before Season 1 closed.
        with database.db() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox ORDER BY id")]
        self.assertEqual(rows[0]["patch"], {"progress": 3})

    def test_finish_final_episode_does_not_chain_to_unwatched_sequel(self):
        # Mark Season 2 unwatched: TODO 275 already hands off to it naturally.
        with database.db() as c:
            c.execute("UPDATE episodes SET watched=0 WHERE id LIKE 'stream:158927:%'")
        progress.rewatch_start("aid:140960")
        progress.rewatch_mark("aid:140960", [1, 2], watched=True)
        with mock.patch.object(anilist, "anilist_connected", return_value=False):
            player.finish_episode("stream:140960:3", 1400, 1440, True)
        self.assertIsNone(progress.rewatch_get("aid:140960"))
        self.assertIsNone(progress.rewatch_get("aid:158927"))

    def test_manual_repeating_without_local_rewatch_does_not_jump_to_finale(self):
        """Even without clicking Rewatch locally, if remote is REPEATING at 0
        and all 3 episodes are watched=1 locally, finishing E01 sends 1, not 3."""
        remote_cache = {"entries": [{"id": 140960, "status": "REPEATING", "progress": 0, "episodes": 3, "title": "SPY x FAMILY"}]}
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
             mock.patch.object(anilist, "anilist_cache_read", return_value=remote_cache), \
             mock.patch.object(anilist, "account_id", return_value=42), \
             mock.patch.object(settings, "automation_enabled", side_effect=lambda k: k == "auto_progress_sync"):
            player.finish_episode("stream:140960:1", 1400, 1440, True)
        with database.db() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox ORDER BY id")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patch"], {"progress": 1})

    def test_offline_rewatch_verify_flips_completed_to_repeating(self):
        payload = {"patch": {"progress": 2}, "verify": {"add": False, "rewatch": True}}
        self.assertEqual(
            sync.outbox_verify(payload, {"status": "COMPLETED", "progress": 12, "episodes": 12}),
            {"status": "REPEATING", "progress": 2},
        )
        self.assertEqual(
            sync.outbox_verify(payload, {"status": "REPEATING", "progress": 1, "episodes": 12}),
            {"progress": 2},
        )
        self.assertIsNone(
            sync.outbox_verify(payload, {"status": "REPEATING", "progress": 4, "episodes": 12}),
        )
        # Never downgrade Completed -> Rewatching on the finale itself.
        self.assertIsNone(
            sync.outbox_verify({"patch": {"progress": 12}, "verify": {"add": False, "rewatch": True}},
                               {"status": "COMPLETED", "progress": 12, "episodes": 12}),
        )

    def test_entry_watched_during_rewatch_preserves_original_watched_facts(self):
        progress.rewatch_start("aid:140960")
        with mock.patch.object(anilist, "anilist_connected", return_value=False):
            r1 = pages.entry_watched(140960, True, [1, 2])
            self.assertEqual(r1["rewatch"]["done"], [1, 2])
            r2 = pages.entry_watched(140960, False, [2])
            self.assertEqual(r2["rewatch"]["done"], [1])
        # Permanent watched marks on all 3 episodes remain intact!
        with database.db() as c:
            watched = [r[0] for r in c.execute("SELECT watched FROM episodes WHERE id LIKE 'stream:140960:%'")]
        self.assertEqual(watched, [1, 1, 1])
        # Marking remaining episodes watched finishes and closes the rewatch.
        with mock.patch.object(anilist, "anilist_connected", return_value=False):
            r3 = pages.entry_watched(140960, True, [2, 3])
        self.assertIsNone(r3["rewatch"])
        self.assertIsNone(progress.rewatch_get("aid:140960"))

    def test_peersync_merges_active_and_ended_rewatches(self):
        mine = {
            "aid:140960": {"started_at": "2026-11-20 10:00:00", "done": [1, 2], "updated_at": "2026-11-20 11:00:00"},
            "aid:158927": {"started_at": "2026-11-19 10:00:00", "done": [1], "updated_at": "2026-11-19 12:00:00"},
        }
        theirs = {
            "aid:140960": {"started_at": "2026-11-20 10:00:00", "done": [1], "updated_at": "2026-11-20 10:30:00"},
            "aid:158927": {"started_at": None, "done": [], "updated_at": "2026-11-20 09:00:00"},
        }
        for_me, for_them = peersync.merge_rewatches(mine, theirs)
        # Their ended rewatch on 158927 is newer -> comes to me.
        self.assertEqual(for_me, {"aid:158927": theirs["aid:158927"]})
        # My further progress on 140960 is newer -> goes to them.
        self.assertEqual(for_them, {"aid:140960": mine["aid:140960"]})
        self.assertIsNone(peersync.check_apply({"rewatches": for_them}))
        self.assertIsNotNone(peersync.check_apply({"rewatches": {"bad": {}}}))
        peersync.apply_rewatches(for_them)
        self.assertEqual(progress.rewatch_get("aid:140960")["done"], [1, 2])

    def test_played_unlinked_shows_includes_active_rewatch_without_last_played(self):
        from animehub import streaming
        with database.db() as c:
            c.execute("DELETE FROM stream_links")
            c.execute("DELETE FROM episodes")
            for i in range(1, 14):
                c.execute(
                    "INSERT INTO episodes(id, watched, position, duration, last_played) VALUES(?, 1, 0, 1440, NULL)",
                    (f"stream:142838:{i}",),
                )
        progress.rewatch_start("aid:142838")
        database.cache_put("release:v1:142838", {"media": {
            "id": 142838, "title": {"english": "SPY x FAMILY Cour 2"},
            "status": "FINISHED", "episodes": 13, "format": "TV",
            "coverImage": {"extraLarge": "https://s4.anilist.co/c2.jpg"},
        }})
        with mock.patch.object(streaming.anicli, "anicli_path", return_value="/bin/ani-cli"):
            shows = streaming.played_unlinked_shows([], set())
            # Already on disk or already linked -> not duplicated as provisional.
            on_disk = streaming.played_unlinked_shows([], {142838})
            linked = streaming.played_unlinked_shows([{"media_id": 142838}], set())
        self.assertEqual([s["id"] for s in shows], ["aid:142838"])
        self.assertEqual(len(shows[0]["seasons"][0]["episodes"]), 13)
        self.assertTrue(shows[0]["stream"]["provisional"])
        self.assertEqual(on_disk, [])
        self.assertEqual(linked, [])
        # Ending the rewatch removes it from played_unlinked_shows since no episode has last_played.
        progress.rewatch_end("aid:142838")
        with mock.patch.object(streaming.anicli, "anicli_path", return_value="/bin/ani-cli"):
            self.assertEqual(streaming.played_unlinked_shows([], set()), [])


class RewatchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = support.TMP / "rewatch_api"
        apply_config(dict(CONFIG, data_dir=base / "data", library_root=base / "lib"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        config.LIBRARY.mkdir(parents=True, exist_ok=True)
        database.init_db()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        cls._old_port = config.PORT
        config.PORT = cls.port
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        config.PORT = cls._old_port
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_api_rewatch_start_end_restore_and_validation(self):
        code, body = self._post("/api/rewatch", {"ref": "bad", "active": True})
        self.assertEqual(code, 400)
        code, body = self._post("/api/rewatch", {"ref": "aid:140960", "active": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["rewatch"]["done"], [])
        progress.rewatch_mark("aid:140960", [1, 2], watched=True)
        code, body = self._post("/api/rewatch", {"ref": "aid:140960", "active": False})
        self.assertEqual(code, 200)
        self.assertIsNone(body["rewatch"])
        self.assertEqual(body["prev"]["done"], [1, 2])
        code, body = self._post("/api/rewatch", {"ref": "aid:140960", "active": True, "restore": body["prev"]})
        self.assertEqual(code, 200)
        self.assertEqual(body["rewatch"]["done"], [1, 2])

    def test_play_with_rewatch_starts_rewatch_and_from_start(self):
        progress.rewatch_end("aid:140960")
        with mock.patch.object(player, "start_play", return_value={"ok": True}) as sp:
            code, body = self._post("/api/play", {"id": "stream:140960:1", "rewatch": True})
        self.assertEqual(code, 200)
        self.assertIsNotNone(progress.rewatch_get("aid:140960"))
        sp.assert_called_once_with("stream:140960:1", 0, from_start=True)


if __name__ == "__main__":
    unittest.main()
