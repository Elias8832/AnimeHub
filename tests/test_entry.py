"""AniList entry editing (TODO 9, Decision 10): the detail-page fields write
through the chokepoint when AniList is connected and into the local curation
overlay otherwise (Decision 6/18.5). The completion popup fires from
playback only — manual marks never trigger it (Decision 25) — and each event
is dismissable exactly once, keyed (media_id, completion count)."""
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
from animehub import anilist, database, library, mapping, progress, server
from animehub.config import config


def show_fixture(sid="hxh", aid=11061, n_eps=148, total=148, title="Hunter x Hunter (2011)"):
    """A one-season folder as merge_db delivers it: attach_releases maps it
    wholly onto its match (no stored franchise needed)."""
    eps = [{"id": f"{sid}-ep{i}", "number": i} for i in range(1, n_eps + 1)]
    s = {"id": sid, "title": title, "collection": None, "kind": "show",
         "meta": {"anilist_id": aid} if aid else None,
         "seasons": [{"key": 1, "season": 1, "label": "Season 1", "episodes": eps}]}
    mapping.attach_releases([s])
    return s


def cache_fixture(total=148):
    return {"user": {"name": "X"}, "score_format": "POINT_10",
            "entries": [{"id": 11061, "episodes": total}], "_ts": 9999999999}


class CompletionEventTests(unittest.TestCase):
    """Decision 25's trigger rules, evaluated per playback completion."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", Path(self.directory.name) / "lib"),
            patch.object(anilist, "anilist_cache_read", cache_fixture),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        with progress._completion_lock:
            progress._completion_pending.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def fired(self):
        with progress._completion_lock:
            return list(progress._completion_pending)

    def test_finishing_the_last_episode_fires_once(self):
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(),
                                        {"id": "hxh-ep148", "number": 148})):
            progress.completion_for_episode("hxh-ep148")
        fired = self.fired()
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0]["media_id"], 11061)
        self.assertEqual(fired[0]["count"], 1)
        self.assertEqual(fired[0]["title"], "Hunter x Hunter (2011)")

    def test_rewatch_finish_is_a_new_event(self):
        """Decision 25: rewatches trigger the popup again — each completion
        bumps the count, which is the event identity."""
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(),
                                        {"id": "hxh-ep148", "number": 148})):
            progress.completion_for_episode("hxh-ep148")
            progress.completion_for_episode("hxh-ep148")
        fired = self.fired()
        self.assertEqual([e["count"] for e in fired], [1, 2])

    def test_non_final_episode_never_fires(self):
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(),
                                        {"id": "hxh-ep5", "number": 5})):
            progress.completion_for_episode("hxh-ep5")
        self.assertEqual(self.fired(), [])

    def test_container_spanning_more_than_the_release_stays_silent(self):
        """A multi-season folder whose matched release covers only part of
        it: finishing 'the last file' is not finishing the release."""
        big = show_fixture(n_eps=200)
        big["meta"] = {"anilist_id": 11061}
        with patch.object(library, "episode_by_id",
                          return_value=(big, {"id": "hxh-ep148", "number": 148})):
            progress.completion_for_episode("hxh-ep148")
        self.assertEqual(self.fired(), [])

    def test_unknown_total_cannot_fire(self):
        """Airing/unknown AniList totals: no event until the total is known
        (Decision 25 — completion must not be guessed)."""
        with patch.object(anilist, "anilist_cache_read",
                          lambda: {"user": {"name": "X"}, "score_format": "POINT_10",
                                   "entries": [{"id": 11061, "episodes": None}],
                                   "_ts": 9999999999}):
            with patch.object(library, "episode_by_id",
                              return_value=(show_fixture(),
                                            {"id": "hxh-ep148", "number": 148})):
                progress.completion_for_episode("hxh-ep148")
        self.assertEqual(self.fired(), [])

    def test_unmatched_show_never_fires(self):
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(aid=None),
                                        {"id": "x-ep12", "number": 12})):
            progress.completion_for_episode("x-ep12")
        self.assertEqual(self.fired(), [])

    def test_dismissal_is_persisted_and_new_events_still_fire(self):
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(),
                                        {"id": "hxh-ep148", "number": 148})):
            progress.completion_for_episode("hxh-ep148")
        progress._dismiss_completion(11061, 1)
        self.assertEqual(self.fired(), [])
        with database.db() as c:
            seen = c.execute("SELECT seen FROM completion_events WHERE"
                             " media_id=11061 AND count=1").fetchone()[0]
        self.assertEqual(seen, 1)
        with patch.object(library, "episode_by_id",
                          return_value=(show_fixture(),
                                        {"id": "hxh-ep148", "number": 148})):
            progress.completion_for_episode("hxh-ep148")  # rewatch → new event
        self.assertEqual(self.fired()[0]["count"], 2)


class EntrySaveUnitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def canned_state(self):
        return {"media_id": 11061, "is_favourite": False, "score_format": "POINT_10",
                "entry": {"status": "CURRENT", "score": 8, "progress": 3,
                          "repeat": 0, "started_at": None, "completed_at": None}}

    def test_patch_builds_one_changed_fields_mutation(self):
        """Decision 18.4: send changed fields, not a stale full entry."""
        captured = {}
        def fake_mutation(query, variables):
            captured["q"], captured["v"] = query, variables
            return {"SaveMediaListEntry": {"id": 1}}
        state = self.canned_state()
        with patch.object(anilist, "anilist_mutation", side_effect=fake_mutation), \
             patch.object(anilist, "anilist_entry_state", return_value=state):
            out = anilist.anilist_entry_save(11061, {"status": "COMPLETED",
                                                 "score": 9.5, "progress": 148,
                                                 "repeat": 2,
                                                 "started_at": "2025-01-02",
                                                 "completed_at": "2025-03-04"})
        self.assertNotIn("error", out)
        q = captured["q"]
        self.assertIn("status: COMPLETED", q)
        self.assertIn("score: 9.5", q)
        self.assertIn("progress: 148", q)
        self.assertIn("repeat: 2", q)
        self.assertIn("startedAt: {year: 2025, month: 1, day: 2}", q)
        self.assertIn("completedAt: {year: 2025, month: 3, day: 4}", q)
        self.assertIn("mediaId:$id", q)

    def test_invalid_status_rejected_before_any_write(self):
        with patch.object(anilist, "anilist_mutation",
                          side_effect=AssertionError("must not write")):
            out = anilist.anilist_entry_save(11061, {"status": "BEST_FRIEND"})
        self.assertIn("error", out)

    def test_invalid_date_rejected_before_any_write(self):
        with patch.object(anilist, "anilist_mutation",
                          side_effect=AssertionError("must not write")):
            out = anilist.anilist_entry_save(11061, {"started_at": "not-a-date"})
        self.assertIn("error", out)

    def test_favourite_toggle_verifies_the_remote_state(self):
        """Decision 18.4: ToggleFavourite is not idempotent — read, toggle
        only when different, verify by re-reading."""
        calls = []
        def fake_mutation(query, variables):
            calls.append(query)
            return {"ToggleFavourite": {"code": 200}}
        before = self.canned_state()
        after = dict(before); after["is_favourite"] = True
        with patch.object(anilist, "anilist_mutation", side_effect=fake_mutation), \
             patch.object(anilist, "anilist_entry_state",
                          side_effect=[before, after]):
            out = anilist.anilist_favourite_set(11061, True)
        self.assertNotIn("error", out)
        self.assertTrue(out["is_favourite"])
        self.assertEqual(len(calls), 1)
        self.assertIn("ToggleFavourite", calls[0])

    def test_favourite_already_in_desired_state_never_toggles(self):
        before = self.canned_state()  # is_favourite False, want False
        with patch.object(anilist, "anilist_mutation",
                          side_effect=AssertionError("must not toggle")), \
             patch.object(anilist, "anilist_entry_state", return_value=before):
            out = anilist.anilist_favourite_set(11061, False)
        self.assertFalse(out["is_favourite"])


class EntryRouteTests(unittest.TestCase):
    """/api/entry over HTTP: local curation fallback without a token, 401
    shape with auth errors, validation errors."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.patches = [
            patch.object(config, "DB", Path(cls.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
            patch.object(config, "ANILIST_TOKEN_FILE",
                         Path(cls.directory.name) / "token.json"),
            patch.object(config, "ANILIST_CACHE",
                         Path(cls.directory.name) / "anilist_cache.json"),
            # GET /api/entry resolves the show for the popup context; the
            # route tests must not scan a real library.
            patch.object(library, "media_by_id", lambda ident: {
                "id": ident, "title": "T", "collection": None, "kind": "show",
                "meta": {"anilist_id": 11061}, "seasons": []}),
        ]
        for p in cls.patches:
            p.start()
        database.init_db()
        import threading
        from http.server import ThreadingHTTPServer
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
            f"http://127.0.0.1:{self.bound}/api/entry",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.bound}{path}",
                                     headers={"Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_local_fallback_stores_curation_without_a_token(self):
        code, body = self.post({"media_id": 11061,
                                "patch": {"status": "COMPLETED", "score": 9}})
        self.assertEqual(code, 200)
        self.assertTrue(body["local"])
        self.assertEqual(body["entry"]["status"], "COMPLETED")
        self.assertEqual(body["entry"]["score"], 9)
        cur = library.curation_get("aid:11061")
        self.assertEqual(cur["status"], "COMPLETED")
        self.assertEqual(cur["score"], 9)

    def test_favourite_only_patch_works(self):
        code, body = self.post({"media_id": 11061, "patch": {},
                                "favourite": True})
        self.assertEqual(code, 200)
        self.assertEqual(body["entry"]["favourite"], True)

    def test_invalid_status_is_a_structured_400(self):
        code, body = self.post({"media_id": 11061,
                                "patch": {"status": "BEST_FRIEND"}})
        self.assertEqual(code, 400)

    def test_get_without_auth_is_the_local_curation_fallback(self):
        """TODO 17 (Decision 10's local fallback, read half): with no token
        at all the editing surface renders from the curation overlay — a
        clean 200 local payload, not a Connect nag. A token that EXISTS but
        is expired still raises the re-auth prompt through the live read."""
        code, body = self.get("/api/entry?id=11061")
        self.assertEqual(code, 200)
        self.assertTrue(body["local"])
        self.assertIn("entry", body)

    def test_get_with_garbage_id_is_a_structured_400(self):
        code, body = self.get("/api/entry?id=potato")
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
