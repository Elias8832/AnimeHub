"""Regressions from the 2026-10-24 review: reconcile scores on the real
cache shape (score + score100), the Track episodes switch with auto-add on,
completion popups across a restart, and required integer fields."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401  (isolated config, no network)
from tests.test_reconcile import entry_fixture, remote_fixture, show_fixture
from animehub import anilist, database, library, progress, server, settings, sync
from animehub.config import config


class _Db(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", Path(self.directory.name) / "lib"),
            patch.object(anilist, "load_token",
                         lambda: {"access_token": "t", "expires_at": None,
                                  "user_id": 42, "user_name": "X"}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()


class ReconcileScoreTests(_Db):
    def analyze(self, entries, fmt):
        sync.reconcile_mark_pending(42)
        with patch.object(library, "scan_library", return_value=[show_fixture()]), \
             patch.object(library, "merge_db", lambda scan: None), \
             patch.object(anilist, "anilist_user_data",
                          return_value=dict(remote_fixture(entries), score_format=fmt)):
            return sync.reconcile_analyze()

    def test_same_score_is_no_conflict(self):
        library.curation_set("aid:11061", {"score": 8})
        e = dict(entry_fixture(11061, score=8), score100=80)
        self.assertEqual(self.analyze([e], "POINT_10")["conflicts"], [])

    def test_different_score_shows_both_in_the_users_format(self):
        library.curation_set("aid:11061", {"score": 40})
        e = dict(entry_fixture(11061, score=9), score100=85)
        rows = self.analyze([e], "POINT_100")["conflicts"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["remote"]["score"], rows[0]["local"]["score"]), (85, 40))

    def test_anilist_wins_stores_the_users_format(self):
        library.curation_set("aid:11061", {"score": 40})
        e = dict(entry_fixture(11061, score=9), score100=85)
        data = dict(remote_fixture([e]), score_format="POINT_100")
        with patch.object(anilist, "anilist_user_data", return_value=data):
            sync.reconcile_apply([{"media_id": 11061, "winner": "anilist"}])
        self.assertEqual(library.curation_get("aid:11061")["score"], 85)


class TrackSwitchTests(_Db):
    def track(self):
        show = show_fixture(sid="b", n_eps=12, watched=(1, 2, 3, 4, 5))
        cache = {"entries": [{"id": 11061, "progress": 2, "episodes": 12}]}
        with patch.object(anilist, "anilist_cache_read", return_value=cache):
            return progress.anilist_track_show(show, only={"b-ep5"})

    def test_auto_add_does_not_override_track_episodes_off(self):
        settings.set_automation("auto_progress_sync", False)
        settings.set_automation("auto_add_watch", True)
        self.assertIsNone(self.track())
        self.assertEqual(sync.outbox_count(), 0)

    def test_track_episodes_on_still_syncs(self):
        self.assertEqual(self.track(), {"progress": 5})


class CompletionRestartTests(_Db):
    def setUp(self):
        super().setUp()
        with progress._completion_lock:
            progress._completion_pending.clear()

    def restart(self):
        with progress._completion_lock:
            progress._completion_pending.clear()
        progress._completion_loaded = False

    def test_unseen_popup_survives_a_restart(self):
        progress._completion_event(11061, "Hunter x Hunter")
        self.restart()
        self.assertEqual(progress._pending_completion(),
                         {"media_id": 11061, "count": 1, "title": "Hunter x Hunter"})

    def test_dismissed_popup_stays_gone(self):
        progress._completion_event(11061, "Hunter x Hunter")
        progress._dismiss_completion(11061, 1)
        self.restart()
        self.assertIsNone(progress._pending_completion())

    def test_old_unseen_events_stay_quiet(self):
        with database.db() as c:
            c.execute("INSERT INTO completion_events(media_id,count,seen,ts,title)"
                      " VALUES(1,1,0,datetime('now','-30 days'),'old')")
        self.restart()
        self.assertIsNone(progress._pending_completion())


class RequiredIntTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port_patch = patch.object(config, "PORT", cls.srv.server_address[1])
        cls.port_patch.start()
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.port_patch.stop()

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{config.PORT}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_missing_ints_are_structured_400s(self):
        for path, body in (("/api/download/cancel", {}), ("/api/outbox/cancel", {}),
                           ("/api/completion-seen", {}), ("/api/stream/mode", {"mode": "sub"}),
                           ("/api/download", {"episodes": ["1"]})):
            with self.subTest(path=path):
                status, payload = self.post(path, body)
                self.assertEqual(status, 400)
                self.assertIn("integer", payload["error"])


if __name__ == "__main__":
    unittest.main()
