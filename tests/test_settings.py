"""Settings tab + setup tour + the AniList automations (TODO 15, Decision 5):
the read/write settings surface, and `auto_track_after_play` — Decision 21's
highest-watched progress push plus Decision 5A's consent-gated auto-add, both
playback-driven, outbox-backed, and guarded by the same container rules as
reconciliation (one release ⇔ one season)."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, library, mapping, progress, server, settings, sync
from animehub.config import config


TOKEN = {"access_token": "tok", "expires_at": None, "user_id": 42, "user_name": "Elias"}


def show(sid, aid, seasons=1, eps=12, watched=(), airing_total=12):
    """A local show the way scan+merge deliver it: watched flags per episode,
    and the season ranges attach_releases gives it (a one-season folder is
    its match; several seasons map to nothing without a stored franchise)."""
    def season(i, n, watched_nums):
        return {"key": 1, "season": 1, "label": "Season 1",
                "episodes": [{"id": f"{sid}-{i}-{j}", "number": j + 1,
                              "watched": 1 if j in watched_nums else 0,
                              "last_played": "2026-10-16 10:00:00"}
                             for j in range(n)]}
    s = {"id": sid, "title": sid.title(), "collection": None, "kind": "show",
         "meta": {"anilist_id": aid} if aid else None,
         "seasons": [season(i, eps if i == 0 else eps, watched) for i in range(seasons)]}
    mapping.attach_releases([s])
    return s


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def test_defaults_progress_on_autoadd_off(self):
        self.assertTrue(settings.automation_enabled("auto_progress_sync"))
        self.assertFalse(settings.automation_enabled("auto_add_watch"))

    def test_toggles_persist(self):
        settings.set_automation("auto_add_watch", True)
        self.assertTrue(settings.automation_enabled("auto_add_watch"))
        settings.set_automation("auto_add_watch", False)
        self.assertFalse(settings.automation_enabled("auto_add_watch"))

    def test_settings_payload_carries_no_token(self):
        with patch.object(anilist, "load_token", lambda: {"access_token": "SECRET"}), \
             patch.object(config, "LIBRARY", Path("/media/anime")):
            payload = settings.settings_payload()
        self.assertNotIn("SECRET", json.dumps(payload))
        self.assertEqual(payload["library_root"], str(Path("/media/anime")))
        self.assertIn("mpv", payload["tools"])
        self.assertFalse(payload["tour_done"])


class AutoTrackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
            patch.object(anilist, "load_token", lambda: dict(TOKEN)),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def cache(self, entries):
        return patch.object(anilist, "anilist_cache_read",
                            lambda: {"user": {"name": "E"}, "entries": entries})

    def run_track(self, sh, ep_id="x-0-0"):
        with patch.object(library, "episode_by_id", return_value=(sh, sh["seasons"][0]["episodes"][0])):
            progress.auto_track_after_play(ep_id)

    def outbox_rows(self):
        with database.db() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT payload FROM outbox")]

    def assert_only_deferred(self):
        """No franchise stored: the played episode waits in the folder's
        deferred row (TODO 59) — no patch for any entry."""
        rows = self.outbox_rows()
        self.assertEqual([(r.get("resync"), "patch" in r) for r in rows], [("s", False)])

    def test_no_token_means_no_write(self):
        with patch.object(anilist, "load_token", lambda: None), \
             patch.object(library, "episode_by_id",
                          side_effect=AssertionError("must not even look")):
            progress.auto_track_after_play("x")
        self.assertEqual(sync.outbox_count(), 0)

    def test_cold_cache_skips_silently(self):
        with patch.object(anilist, "anilist_cache_read", lambda: None), \
             patch.object(library, "episode_by_id", return_value=(show("s", 1), {})):
            progress.auto_track_after_play("x")
        self.assertEqual(sync.outbox_count(), 0)

    def test_progress_push_when_playback_advances_past_remote(self):
        """Decision 21: the published number is the highest watched episode;
        playback pushing past the cached remote progress queues the patch."""
        with self.cache([{"id": 99, "progress": 3, "episodes": 12}]):
            self.run_track(show("s", 99, watched={0, 1, 2, 3, 4}))
        rows = self.outbox_rows()
        self.assertEqual(rows, [{"patch": {"progress": 5}, "account_id": 42}])

    def test_no_push_when_not_ahead(self):
        with self.cache([{"id": 99, "progress": 5, "episodes": 12}]):
            self.run_track(show("s", 99, watched={0, 1, 2}))
        self.assertEqual(sync.outbox_count(), 0)

    def test_autoadd_off_never_touches_unlisted_shows(self):
        with self.cache([]):
            self.run_track(show("s", 99, watched={0}))
        self.assertEqual(sync.outbox_count(), 0)

    def test_autoadd_on_adds_first_watch_to_watching(self):
        """Decision 5A, consent-gated: a first watch of a matched-but-unlisted
        show adds it to Watching with the mappable progress."""
        settings.set_automation("auto_add_watch", True)
        with self.cache([{"id": 8, "progress": 0, "episodes": 12}]):  # a DIFFERENT listed show
            self.run_track(show("s", 7, watched={0}))
        rows = self.outbox_rows()
        self.assertEqual(rows[0]["patch"], {"status": "CURRENT", "progress": 1})

    def test_autoadd_unmapped_container_adds_nothing(self):
        """Multi-season container whose seasons aren't mapped yet: the played
        episode belongs to no known release, so nothing is added — the old
        status-only add put the folder's first release on Watching even
        while a later season played (Decision 33). Once mapped, the
        season's own release is added (test_release_map)."""
        settings.set_automation("auto_add_watch", True)
        with self.cache([{"id": 8, "progress": 0, "episodes": 12}]):
            self.run_track(show("s", 7, seasons=2, eps=6, watched={0}))
        self.assert_only_deferred()

    def test_container_spanning_files_stay_silent(self):
        """Installed files exceeding the release total: the folder spans more
        releases than the matched one — no number may be published."""
        with self.cache([{"id": 99, "progress": 0, "episodes": 2}]):
            self.run_track(show("s", 99, eps=10, watched={0, 1, 2}))
        self.assertEqual(sync.outbox_count(), 0)

    def test_unknown_total_still_pushes(self):
        """Airing show (`episodes: null`): still one release, so Decision 21's
        highest-watched progress syncs — only the completion popup needs a
        known total (Decision 25)."""
        with self.cache([{"id": 99, "progress": 0, "episodes": None}]):
            self.run_track(show("s", 99, watched={0, 1}))
        self.assertEqual(self.outbox_rows(), [{"patch": {"progress": 2}, "account_id": 42}])

    def test_unknown_total_container_stays_silent(self):
        """An unknown total never opens the multi-season guard (Decision 19)."""
        with self.cache([{"id": 99, "progress": 0, "episodes": None}]):
            self.run_track(show("s", 99, seasons=2, eps=6, watched={0}))
        self.assert_only_deferred()

    def test_pending_intent_dedupes(self):
        """One pending intent per media — an offline episode end doesn't
        stack a queue of the same patch."""
        sync.outbox_enqueue(99, patch={"progress": 5})
        with self.cache([{"id": 99, "progress": 3, "episodes": 12}]):
            self.run_track(show("s", 99, watched={0, 1, 2, 3, 4}))
        self.assertEqual(sync.outbox_count(), 1)

    def test_toggles_off_means_nothing_runs(self):
        settings.set_automation("auto_progress_sync", False)
        with self.cache([{"id": 99, "progress": 0, "episodes": 12}]):
            self.run_track(show("s", 99, watched={0, 1}))
        self.assertEqual(sync.outbox_count(), 0)


class SettingsRouteTests(unittest.TestCase):
    """/api/settings over a real server: typed validation (TODO 22's rule)
    and the toggle/tour round-trip."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.patches = [
            patch.object(config, "DB", Path(cls.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
        ]
        for p in cls.patches:
            p.start()
        database.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.bound = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in reversed(cls.patches):
            p.stop()
        cls.directory.cleanup()

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.bound}{path}",
                                     headers={"Host": "127.0.0.1:8787"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    def post(self, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}/api/settings",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_get_settings(self):
        body = self.get("/api/settings")
        self.assertTrue(body["auto_progress_sync"])
        self.assertFalse(body["auto_add_watch"])
        self.assertFalse(body["tour_done"])
        self.assertIn("tools", body)

    def test_toggle_round_trip(self):
        code, body = self.post({"auto_add_watch": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["auto_add_watch"])
        code, body = self.post({"auto_add_watch": False, "tour_done": True})
        self.assertFalse(body["auto_add_watch"])
        self.assertTrue(body["tour_done"])
        self.assertTrue(self.get("/api/settings")["tour_done"])

    def test_non_boolean_is_a_structured_400(self):
        code, body = self.post({"auto_add_watch": "yes"})
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    def test_empty_patch_is_a_structured_400(self):
        code, body = self.post({})
        self.assertEqual(code, 400)

    def test_skip_settings(self):
        """Decision 39: skip modes are a closed vocabulary; AniSkip is a
        boolean and starts off."""
        self.assertFalse(self.get("/api/settings")["aniskip"])
        code, body = self.post({"skip_op": "auto", "skip_ed": "off", "aniskip": True})
        self.assertEqual(code, 200)
        self.assertEqual(body["skip"], {"op": "auto", "ed": "off"})
        self.assertTrue(body["aniskip"])
        for bad in ({"skip_op": "always"}, {"skip_ed": None}, {"aniskip": "yes"}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        self.assertTrue(self.get("/api/settings")["aniskip_vpn_warn"])
        code, body = self.post({"aniskip_vpn_warn": False})
        self.assertFalse(body["aniskip_vpn_warn"])
        self.post({"skip_op": "prompt", "skip_ed": "prompt", "aniskip": False, "aniskip_vpn_warn": True})

    def test_play_carries_the_aniskip_yes(self):
        """Decision 39: `aniskip` on /api/play is the user's answer for that
        episode; anything but a boolean is a 400."""
        from animehub import player

        def post_play(body):
            req = urllib.request.Request(f"http://127.0.0.1:{self.bound}/api/play", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Host": "127.0.0.1:8787"})
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status
            except urllib.error.HTTPError as e:
                return e.code

        self.addCleanup(player._aniskip_answers.clear)
        with patch.object(player, "start_play", return_value={"ok": True}):
            self.assertEqual(post_play({"id": "ep1"}), 200)
            self.assertNotIn("ep1", player._aniskip_answers)
            self.assertEqual(post_play({"id": "ep1", "aniskip": "yes"}), 400)
            self.assertNotIn("ep1", player._aniskip_answers)
            self.assertEqual(post_play({"id": "ep1", "aniskip": True}), 200)
            self.assertTrue(player._aniskip_answers["ep1"][0])
            self.assertEqual(post_play({"id": "ep1", "aniskip": False}), 200)
            self.assertFalse(player._aniskip_answers["ep1"][0])

    def test_unknown_keys_are_ignored(self):
        code, body = self.post({"tour_done": True, "evil": True})
        self.assertEqual(code, 200)
        self.assertTrue(body["tour_done"])


if __name__ == "__main__":
    unittest.main()


class MpvVersion(unittest.TestCase):
    """The tour's last page says "mpv 0.41.0 found" (additive `mpv_version`)."""
    def test_reads_the_first_line(self):
        from unittest import mock
        from animehub import settings as st
        st._mpv_versions.clear()
        out = mock.Mock(stdout="mpv v0.41.0 Copyright © 2000-2025 mpv/MPlayer/mplayer2 projects\n built on …")
        with mock.patch.object(st.system.shutil, "which", return_value="/usr/bin/mpv"), mock.patch.object(st.subprocess, "run", return_value=out):
            self.assertEqual(st.mpv_version(), "0.41.0")
        st._mpv_versions.clear()
        with mock.patch.object(st.system.shutil, "which", return_value=None):
            self.assertIsNone(st.mpv_version())
