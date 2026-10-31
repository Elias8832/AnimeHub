"""Connect-time reconciliation (TODO 16, Decision 14): when an account is
(re)linked, facts (watched episodes) merge automatically — always the higher
count, both directions, guarded like the completion trigger — while
curation (status, score) becomes per-show decisions with bulk buttons.
The pending marker makes the dialog a once-per-connect event, never a
every-boot nag. Facts merge per release (release_units): each mapped season
of a multi-season folder against its own entry; unmapped seasons sit out."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, library, mapping, server, settings, sync
from animehub.config import config


def show_fixture(sid="hxh", aid=11061, n_eps=148, watched=(1, 2, 3),
                 title="Hunter x Hunter (2011)", seasons=None):
    """Given seasons carry their own `releases`; the default one-season
    folder gets the range merge_db's attach_releases gives it (its match)."""
    attach = seasons is None
    seasons = seasons or [{"key": 1, "season": 1, "label": "Season 1",
                           "episodes": [{"id": f"{sid}-ep{i}", "number": i,
                                         "watched": i in watched}
                                        for i in range(1, n_eps + 1)]}]
    s = {"id": sid, "title": title, "collection": None, "kind": "show",
         "meta": {"anilist_id": aid} if aid else None, "seasons": seasons}
    if attach:
        mapping.attach_releases([s])
    return s


def remote_fixture(entries):
    return {"user": {"name": "X"}, "score_format": "POINT_10_DECIMAL",
            "entries": entries, "_ts": 9999999999}


def entry_fixture(mid, status="CURRENT", progress=3, eps=148, score=8):
    return {"id": mid, "title": f"Show {mid}", "status": status,
            "progress": progress, "score": score, "episodes": eps,
            "format": "TV", "year": 2011, "airing": "FINISHED",
            "updated": 1700000000, "poster": None}


class ReconcileAnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", Path(self.directory.name) / "lib"),
            patch.object(anilist, "load_token",
                         lambda: {"access_token": "t", "expires_at": None,
                                  "user_id": 42, "user_name": "Elias"}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        sync.reconcile_mark_pending(42)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def analyze(self, scan, entries):
        # The fixtures arrive already merged (meta, season map, watch flags);
        # merge_db would re-read them from the empty test database.
        with patch.object(library, "scan_library", return_value=scan), \
             patch.object(library, "merge_db", lambda scan: None), \
             patch.object(anilist, "anilist_user_data",
                          return_value=remote_fixture(entries)):
            return sync.reconcile_analyze()

    def test_facts_push_local_watches_upward_via_outbox(self):
        """Local 12 watched vs AniList progress 3 → the higher count wins:
        a progress push is queued (Decision 21's highest-watched rule)."""
        res = self.analyze([show_fixture(watched=tuple(range(1, 13)))],
                           [entry_fixture(11061, progress=3)])
        self.assertEqual(len(res["facts"]), 1)
        self.assertEqual(res["facts"][0]["kind"], "progress")
        self.assertEqual(res["facts"][0]["local"], 12)
        with database.db() as c:
            payload = json.loads(c.execute(
                "SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"]["progress"], 12)

    def test_remote_higher_asks_before_marking(self):
        """AniList progress 50 vs local 3 → nothing changes until the user
        says yes (Decision 48), then episodes up to 50 become watched facts
        locally (in the DB — the source the scanner reads back); nothing
        above 50 is invented."""
        scan = show_fixture(n_eps=148, watched=(1, 2, 3))
        with database.db() as c:
            # production pre-state: eps 1-3 are watched facts in the DB
            c.executemany(
                "INSERT INTO episodes(id,watched) VALUES(?,1)",
                [(f"hxh-ep{i}",) for i in (1, 2, 3)])
        res = self.analyze([scan], [entry_fixture(11061, progress=50)])
        self.assertEqual(res["facts"], [])
        self.assertEqual(res["marks"], [{"show": res["marks"][0]["show"], "media_id": 11061,
                                         "remote": 50, "episodes": 47}])
        def watched():
            with database.db() as c:
                return {r[0] for r in c.execute("SELECT id FROM episodes WHERE watched=1")}
        self.assertEqual(watched(), {f"hxh-ep{i}" for i in (1, 2, 3)})
        with patch.object(library, "scan_library", return_value=[scan]), \
             patch.object(library, "merge_db", lambda scan: None), \
             patch.object(anilist, "anilist_user_data",
                          return_value=remote_fixture([entry_fixture(11061, progress=50)])):
            self.assertEqual(sync.reconcile_mark_watched(), 47)
        self.assertEqual(watched(), {f"hxh-ep{i}" for i in range(1, 51)})

    def test_mark_watched_needs_a_pending_connect(self):
        sync.reconcile_clear()
        with patch.object(anilist, "anilist_user_data") as data:
            self.assertEqual(sync.reconcile_mark_watched(), 0)
        data.assert_not_called()

    def test_converged_shows_produce_no_facts(self):
        res = self.analyze([show_fixture(watched=tuple(range(1, 13)))],
                           [entry_fixture(11061, progress=12)])
        self.assertEqual(res["facts"], [])

    def test_container_guard_keeps_multi_season_folders_out(self):
        """Two seasons in one folder matched to one release: episode numbers
        don't map 1:1 onto the release — facts stay out (the release
        -matcher work owns that mapping)."""
        seasons = [
            {"key": 1, "season": 1, "label": "Season 1",
             "episodes": [{"id": f"s1-{i}", "number": i, "watched": True}
                          for i in range(1, 29)]},
            {"key": 2, "season": 2, "label": "Season 2",
             "episodes": [{"id": f"s2-{i}", "number": i, "watched": False}
                          for i in range(1, 11)]},
        ]
        scan = show_fixture(seasons=seasons)  # installed 38 > release total 28
        res = self.analyze([scan], [entry_fixture(154587, progress=3, eps=28)])
        self.assertEqual(res["facts"], [])

    def test_mapped_seasons_reconcile_against_their_own_releases(self):
        """The same folder once its seasons are mapped (Frieren S1 154587,
        S2 182255): each season merges with its own entry — S1 pushes 28,
        S2 offers AniList's 4 to mark locally."""
        seasons = [
            {"key": 1, "season": 1, "label": "Season 1",
             "releases": [{"id": 154587, "first_ep": 1, "last_ep": None, "offset": 0}],
             "episodes": [{"id": f"s1-{i}", "number": i, "watched": True}
                          for i in range(1, 29)]},
            {"key": 2, "season": 2, "label": "Season 2",
             "releases": [{"id": 182255, "first_ep": 1, "last_ep": None, "offset": 0}],
             "episodes": [{"id": f"s2-{i}", "number": i, "watched": False}
                          for i in range(1, 11)]},
        ]
        scan = show_fixture(sid="frieren", aid=154587, seasons=seasons)
        res = self.analyze([scan], [entry_fixture(154587, progress=3, eps=28),
                                    entry_fixture(182255, progress=4, eps=10)])
        kinds = sorted((f["kind"], f["local"], f["remote"]) for f in res["facts"])
        self.assertEqual(kinds, [("progress", 28, 3)])
        self.assertEqual([(m["media_id"], m["episodes"]) for m in res["marks"]], [(182255, 4)])
        with database.db() as c:
            payload = json.loads(c.execute("SELECT payload FROM outbox").fetchone()[0])
            marked = {r[0] for r in c.execute("SELECT id FROM episodes WHERE watched=1")}
        self.assertEqual(payload["patch"]["progress"], 28)
        self.assertEqual(marked, set())  # asked first (Decision 48)

    def test_show_not_on_any_list_is_out_of_scope(self):
        """Pushing facts to an unlisted show would CREATE a list entry —
        that's the consent-gated auto-track (Decision 5, TODO 15)."""
        scan = show_fixture(watched=tuple(range(1, 13)))
        res = self.analyze([scan], [])  # nothing on the user's lists
        self.assertEqual(res["facts"], [])
        self.assertEqual(sync.outbox_count(), 0)

    def test_no_pending_marker_means_noop(self):
        sync.reconcile_clear()
        res = self.analyze([show_fixture(watched=tuple(range(1, 13)))],
                           [entry_fixture(11061, progress=3)])
        self.assertFalse(res["pending"])
        self.assertEqual(res["facts"], [])
        self.assertEqual(sync.outbox_count(), 0)

    def test_curation_conflict_detected_when_values_differ(self):
        library.curation_set("aid:11061", {"status": "COMPLETED", "score": 4})
        res = self.analyze([show_fixture()],
                           [entry_fixture(11061, status="CURRENT", score=8)])
        self.assertEqual(len(res["conflicts"]), 1)
        row = res["conflicts"][0]
        self.assertEqual(row["remote"]["status"], "CURRENT")
        self.assertEqual(row["local"]["status"], "COMPLETED")
        self.assertIn("status", row["diff"])

    def test_score_conflict_ignores_format_rounding(self):
        """Local 8 in a POINT_10_DECIMAL account vs a POINT_10-rounded read:
        same vote on the 100-scale — NOT a conflict (Decision 23.6)."""
        library.curation_set("aid:11061", {"score": 8})
        res = self.analyze([show_fixture()],
                           [entry_fixture(11061, score=8)])
        self.assertEqual(res["conflicts"], [])

    def test_no_local_curation_means_no_conflict(self):
        """A never-touched local show has no overlay values to defend — the
        AniList entry simply stands."""
        res = self.analyze([show_fixture()],
                           [entry_fixture(11061, status="COMPLETED", score=9)])
        self.assertEqual(res["conflicts"], [])

    def test_pending_flag_gates_everything(self):
        sync.reconcile_clear()
        library.curation_set("aid:11061", {"status": "COMPLETED"})
        res = self.analyze([show_fixture()],
                           [entry_fixture(11061, status="CURRENT")])
        self.assertFalse(res["pending"])
        self.assertEqual(res["conflicts"], [])


class ReconcileApplyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(anilist, "load_token",
                         lambda: {"access_token": "t", "expires_at": None,
                                  "user_id": 42, "user_name": "Elias"}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        sync.reconcile_mark_pending(42)
        library.curation_set("aid:11061", {"status": "COMPLETED", "score": 4})
        library.curation_set("aid:154587", {"status": "CURRENT", "score": 9})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def remote(self):
        return {"user": {"name": "X"}, "score_format": "POINT_10_DECIMAL",
                "entries": [entry_fixture(11061, status="CURRENT", score=8),
                            entry_fixture(154587, status="COMPLETED", score=10)],
                "_ts": 9999999999}

    def test_anilist_wins_adopts_remote_into_the_overlay(self):
        with patch.object(anilist, "anilist_user_data", return_value=self.remote()):
            applied = sync.reconcile_apply([
                {"media_id": 11061, "winner": "anilist"}])
        self.assertEqual(applied, 1)
        cur = library.curation_get("aid:11061")
        self.assertEqual(cur["status"], "CURRENT")
        self.assertEqual(cur["score"], 8)
        self.assertEqual(sync.outbox_count(), 0)  # remote already holds it
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), None)

    def test_local_wins_keeps_overlay_and_pushes_via_outbox(self):
        with patch.object(anilist, "anilist_user_data", return_value=self.remote()):
            applied = sync.reconcile_apply([
                {"media_id": 11061, "winner": "local"}])
        self.assertEqual(applied, 1)
        cur = library.curation_get("aid:11061")
        self.assertEqual(cur["status"], "COMPLETED")  # untouched
        with database.db() as c:
            payload = json.loads(c.execute(
                "SELECT payload FROM outbox").fetchone()[0])
        self.assertEqual(payload["patch"]["status"], "COMPLETED")
        self.assertEqual(payload["patch"]["score"], 4)

    def test_apply_clears_the_pending_marker(self):
        with patch.object(anilist, "anilist_user_data", return_value=self.remote()):
            sync.reconcile_apply([
                {"media_id": 11061, "winner": "anilist"},
                {"media_id": 154587, "winner": "local"}])
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), None)

    def test_bad_winner_is_ignored(self):
        """An unrecognized winner applies nothing — but the Apply press
        itself means the dialog was handled, so the marker clears (the
        frontend always sends the full decision map)."""
        with patch.object(anilist, "anilist_user_data", return_value=self.remote()):
            applied = sync.reconcile_apply([
                {"media_id": 11061, "winner": "coin-flip"}])
        self.assertEqual(applied, 0)
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), None)


class ReconcileRouteTests(unittest.TestCase):
    """/api/reconcile over HTTP: pending gating, apply clearing the marker."""

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

    def test_analyze_without_connection_is_a_clean_noop(self):
        code, body = self.request("POST", "/api/reconcile")
        self.assertEqual(code, 200)
        self.assertFalse(body["pending"])

    def test_apply_is_a_noop_without_connection(self):
        """No token → reconcile_apply's remote fetch fails softly; the
        marker still clears (there is nothing to reconcile against)."""
        sync.reconcile_mark_pending(42)
        code, body = self.request("POST", "/api/reconcile/apply",
                                  {"decisions": []})
        self.assertEqual(code, 200)
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), None)

    def test_dismiss_clears_the_marker(self):
        sync.reconcile_mark_pending(42)
        code, _ = self.request("POST", "/api/reconcile/dismiss")
        self.assertEqual(code, 200)
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), None)

    def test_apply_rejects_non_list_decisions(self):
        sync.reconcile_mark_pending(42)
        code, body = self.request("POST", "/api/reconcile/apply",
                                  {"decisions": "all-anilist"})
        self.assertEqual(code, 400)

    def test_apply_rejects_a_non_boolean_mark_answer(self):
        code, body = self.request("POST", "/api/reconcile/apply",
                                  {"decisions": [], "mark_watched": "yes"})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
