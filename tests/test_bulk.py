"""Bulk mark-as-watched (TODO 6, Decision 27): one mark for a season or a
whole show — bookkeeping, not playback (no completion events, Decision 25).
It syncs as the user acting (Decision 27): the matched release's progress
becomes the highest watched episode, even with the playback automation off;
multi-season containers stay local-only (Decision 19: never a container
sum), and bulk unwatching stays local (AniList corrections live on the
entry panel)."""
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


def show(sid, aid, seasons=1, eps=12):
    def season(i, n):
        return {"key": 1, "season": 1, "label": "Season 1",
                "episodes": [{"id": f"{sid}-{i}-{j}", "number": j + 1,
                              "watched": 0, "files": [], "rel": f"{sid}/{i}/{j}"}
                             for j in range(n)]}
    s = {"id": sid, "title": sid.title(), "collection": None, "kind": "show",
         "meta": {"anilist_id": aid} if aid else None,
         "seasons": [season(i, eps) for i in range(seasons)]}
    mapping.attach_releases([s])  # merge_db's season ranges: a one-season folder is its match
    return s


class BulkWatchedTests(unittest.TestCase):
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

    def cached(self, entries):
        return patch.object(anilist, "anilist_cache_read",
                            lambda: {"user": {"name": "E"}, "entries": entries})

    def mark(self, sid="s", aid=None, seasons=1, eps=12, **kwargs):
        """bulk_watched resolves the show through media_by_id — pin the
        fake library in place for the call."""
        sh = show(sid, aid, seasons=seasons, eps=eps)
        with patch.object(library, "media_by_id", return_value=sh):
            return progress.bulk_watched(sid, **kwargs)

    def watched_rows(self):
        with database.db() as c:
            return {r[0]: r[1] for r in c.execute("SELECT id, watched FROM episodes")}

    def test_whole_show_marks_every_episode(self):
        r = self.mark(eps=6)
        self.assertTrue(r["ok"])
        self.assertEqual(r["episodes"], 6)
        rows = self.watched_rows()
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(rows.values()))

    def test_single_season_marks_only_its_episodes(self):
        r = self.mark(seasons=2, eps=6, season=1)
        self.assertEqual(r["episodes"], 6)
        rows = self.watched_rows()
        self.assertEqual(sum(rows.values()), 6)
        self.assertTrue(all(v for k, v in rows.items() if "-1-" in k))
        self.assertFalse(any(v for k, v in rows.items() if "-0-" in k))

    def test_unwatch_is_the_correction(self):
        self.mark(eps=6)
        r = self.mark(eps=6, watched=False)
        self.assertEqual(r["episodes"], 6)
        rows = self.watched_rows()
        self.assertFalse(any(rows.values()))

    def test_unknown_show_is_a_clean_error(self):
        with patch.object(library, "media_by_id", return_value=None):
            self.assertEqual(progress.bulk_watched("nope").get("error"), "unknown show")
        r = self.mark()
        with patch.object(library, "media_by_id", return_value=show("s", None, seasons=2)):
            self.assertEqual(progress.bulk_watched("s", season=9).get("error"), "no such season")

    def test_a_mark_drops_the_stop_point(self):
        """Marking a half-watched episode watched means done: no progress bar,
        no "Reset progress" left (Elias, 2026-10-24). Unwatching keeps it."""
        sh = show("s", None, eps=2)
        e1, e2 = (ep["id"] for ep in sh["seasons"][0]["episodes"])
        with database.db() as c:
            c.executemany("INSERT INTO episodes(id,watched,position,duration) VALUES(?,0,?,1440)", [(e1, 600.0), (e2, 300.0)])
        with patch.object(library, "media_by_id", return_value=sh):
            progress.bulk_watched("s", ids=[e1])
        progress.set_watched(e2, False)
        with database.db() as c:
            rows = dict(c.execute("SELECT id, position FROM episodes").fetchall())
        self.assertEqual(rows[e1], 0)
        self.assertEqual(rows[e2], 300.0)
        progress.set_watched(e2, True)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT position FROM episodes WHERE id=?", (e2,)).fetchone()[0], 0)

    def test_no_completion_events_fire(self):
        """Decision 25: manual/bulk marking is bookkeeping — the completion
        popup never triggers from this path."""
        with self.cached([{"id": 99, "progress": 0, "episodes": 6}]), \
             patch.object(progress, "completion_for_episode",
                          side_effect=AssertionError("popup must not fire")):
            self.mark(aid=99, eps=6)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM completion_events").fetchone()[0], 0)

    def test_listed_show_syncs_even_with_automation_off(self):
        """Decision 27's key rule: an explicit click syncs even when
        auto_progress_sync is off — toggles govern automations, not the
        user acting."""
        settings.set_automation("auto_progress_sync", False)
        with self.cached([{"id": 99, "progress": 2, "episodes": 6}]):
            r = self.mark(aid=99, eps=6)
        self.assertEqual(r["synced"], {"progress": 6})
        with database.db() as c:
            payload = json.loads(c.execute("SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"], {"progress": 6})

    def test_no_regression_when_remote_is_ahead(self):
        with self.cached([{"id": 99, "progress": 9, "episodes": 6}]):
            r = self.mark(aid=99, eps=6)
        self.assertIsNone(r["synced"])
        self.assertEqual(sync.outbox_count(), 0)

    def test_multi_season_container_stays_local_only(self):
        """Decision 19: a folder spanning releases never publishes a sum.
        With no franchise stored yet, the marks wait in one deferred row
        (TODO 59) — no progress patch for the container's match."""
        with self.cached([{"id": 99, "progress": 0, "episodes": 6}]):
            r = self.mark(aid=99, seasons=2, eps=6)  # 2 × 6 = 12 installed
        self.assertEqual(r["synced"], {"later": True})
        with database.db() as c:
            rows = c.execute("SELECT media_id, payload FROM outbox").fetchall()
        self.assertEqual([(m, json.loads(p)["resync"]) for m, p in rows], [(0, "s")])

    def test_unlisted_show_needs_consent(self):
        with self.cached([]):
            r = self.mark(aid=99)  # auto_add_watch default off
        self.assertIsNone(r["synced"])
        self.assertEqual(sync.outbox_count(), 0)

    def test_unlisted_show_adds_when_consent_given(self):
        settings.set_automation("auto_add_watch", True)
        with self.cached([{"id": 7, "progress": 0, "episodes": 6}]):
            r = self.mark(aid=99, eps=6)  # matched, not on the list
        self.assertEqual(r["synced"], {"status": "CURRENT", "progress": 6})

    def test_bulk_unwatch_does_not_touch_anilist(self):
        """Decision 27: unwatching stays local — no remote regression."""
        self.mark()
        with self.cached([{"id": 99, "progress": 6, "episodes": 6}]):
            r = self.mark(aid=99, watched=False)
        self.assertIsNone(r["synced"])
        self.assertEqual(sync.outbox_count(), 0)

    def test_unmatched_show_syncs_nothing(self):
        with self.cached([{"id": 99, "progress": 0, "episodes": 6}]):
            r = self.mark()  # no anilist_id on the show
        self.assertIsNone(r["synced"])
        self.assertEqual(sync.outbox_count(), 0)


class BulkRouteTests(unittest.TestCase):
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

    def post(self, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}/api/bulk-watched",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_validation(self):
        self.assertEqual(self.post({})[0], 400)
        self.assertEqual(self.post({"show_id": "s"})[0], 400)          # no watched
        self.assertEqual(self.post({"watched": True})[0], 400)         # no show_id
        code, body = self.post({"show_id": "s", "watched": True, "season": -1})
        self.assertEqual(code, 400)
        code, body = self.post({"show_id": "s", "watched": True, "season": "1"})
        self.assertEqual(code, 400)

    def test_unknown_show_is_a_clean_404(self):
        code, body = self.post({"show_id": "nope", "watched": True})
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "unknown show")

    def test_mark_and_unmark_round_trip(self):
        with patch.object(library, "scan_library", return_value=[show("s", None, eps=3)]):
            code, body = self.post({"show_id": "s", "watched": True})
            self.assertEqual(code, 200)
            self.assertEqual(body["episodes"], 3)
            code, body = self.post({"show_id": "s", "watched": False, "season": 0})
            self.assertEqual(code, 200)
            self.assertEqual(body["episodes"], 3)


if __name__ == "__main__":
    unittest.main()
