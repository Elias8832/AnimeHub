"""AniList-spine library (TODO 8, Decision 4): the user's AniList lists are
the organizing structure, with local files integrated. spine_build is the
pure merge — one card per AniList entry (release-first, Decision 19), a
source-capability badge per entry, and local shows that aren't on any list
surfaced as untracked. The spine rides the AniList cache: /api/library must
never stall on the network."""
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anicli, anilist, database, library, server
from animehub.config import config, CONFIG


def entry(mid, status="CURRENT", eps=12, fmt="TV", year=2020, score=8,
          updated=1700000000, airing="FINISHED", title=None):
    return {"id": mid, "title": title or f"Show {mid}", "status": status,
            "progress": 3, "score": score, "episodes": eps, "format": fmt,
            "year": year, "airing": airing, "updated": updated,
            "poster": f"/img/{mid}.jpg"}


def local_show(sid, aid=None, n_eps=12, title=None):
    return {"id": sid, "title": title or sid, "collection": None, "kind": "show",
            "meta": {"anilist_id": aid} if aid else None,
            "seasons": [{"key": 1, "season": 1, "label": "Season 1",
                         "episodes": [{"id": f"{sid}-{i}", "number": i + 1} for i in range(n_eps)]}]}


class SpineBuildTests(unittest.TestCase):
    def test_merged_entry_reports_installed(self):
        """A list entry whose release is fully on disk: capability
        'installed', deep-linked to its local show."""
        spine = library.spine_build([entry(154587)], [local_show("frieren", 154587, 28)])
        e = spine["entries"][0]
        self.assertEqual(e["capability"], "installed")
        self.assertEqual(e["local_show_id"], "frieren")
        self.assertEqual(spine["untracked"], [])

    def test_partial_when_fewer_files_than_release(self):
        spine = library.spine_build([entry(11061, eps=148)], [local_show("hxh", 11061, 60)])
        self.assertEqual(spine["entries"][0]["capability"], "partial")

    def test_not_installed_when_nothing_on_disk(self):
        spine = library.spine_build([entry(209939)], [])
        self.assertEqual(spine["entries"][0]["capability"], "none")
        self.assertIsNone(spine["entries"][0]["local_show_id"])

    def test_airing_release_with_files_is_partial(self):
        """An airing release has no full episode count yet — files on disk
        can't prove completeness, so 'partial' is the honest ceiling."""
        spine = library.spine_build([entry(1, eps=None, airing="RELEASING")],
                                [local_show("air", 1, 5)])
        self.assertEqual(spine["entries"][0]["capability"], "partial")

    def test_local_show_not_on_any_list_is_untracked(self):
        spine = library.spine_build([], [local_show("solo", None, 12)])
        self.assertEqual(spine["entries"], [])
        self.assertEqual(spine["untracked"], ["solo"])

    def test_matched_but_unlisted_show_is_untracked_too(self):
        """Matched to a release the user hasn't listed: part of the universe,
        not tracked — it must not vanish."""
        spine = library.spine_build([entry(999)], [local_show("orph", 1234, 12)])
        self.assertEqual(spine["untracked"], ["orph"])

    def test_two_local_shows_on_one_release_take_the_largest(self):
        spine = library.spine_build([entry(16498, eps=25)],
                                [local_show("a", 16498, 10), local_show("b", 16498, 25)])
        e = spine["entries"][0]
        self.assertEqual(e["capability"], "installed")
        self.assertEqual(e["local_show_id"], "b")
        self.assertEqual(spine["untracked"], [])

    def test_unmatched_local_shows_never_claim_entries(self):
        spine = library.spine_build([entry(1), entry(2)],
                                [local_show("x", None, 12), local_show("y", None, 12)])
        self.assertEqual([e["capability"] for e in spine["entries"]], ["none", "none"])
        self.assertEqual(sorted(spine["untracked"]), ["x", "y"])


class SpinePayloadTests(unittest.TestCase):
    """/api/library carries the spine when the AniList cache is warm — and
    nothing network-blocking ever: a stale/absent cache means no spine key,
    which the UI renders as the plain local library (Decision 6)."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.cache = Path(cls.directory.name) / "anilist_cache.json"
        cls.patches = [
            patch.object(config, "DB", Path(cls.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
            patch.object(config, "ANILIST_CACHE", cls.cache),
            # Machine-independent: the AniList-spine mode needs a usable
            # OAuth token, whatever the developer's own state happens to be.
            patch.object(anilist, "load_token",
                         lambda: {"access_token": "t", "user_id": 1}),
            # ...and without ani-cli, whether the developer has it or not
            # (with it, an uninstalled entry's capability is "online").
            patch.object(anicli, "anicli_path", lambda: None),
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

    def get_library(self):
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.bound}/api/library",
                                     headers={"Host": "127.0.0.1:8787"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    def test_no_cache_means_no_spine_key(self):
        self.cache.unlink(missing_ok=True)
        body = self.get_library()
        self.assertIsNone(body["spine"])

    def test_stale_cache_serves_last_known_lists(self):
        """A connected-but-stale cache is *not* a disconnect (Decision 6):
        the spine still carries mode "anilist" with the last-known lists,
        instead of silently dropping the UI into an empty local grid."""
        self.cache.write_text(json.dumps({"user": {"name": "X"}, "entries": [], "_ts": 1}))
        body = self.get_library()
        self.assertIsNotNone(body["spine"])
        self.assertEqual(body["spine"]["mode"], "anilist")

    def test_warm_cache_delivers_the_spine(self):
        import time as _time
        self.cache.write_text(json.dumps({
            "user": {"name": "X"},
            "entries": [{"id": 154587, "title": "Frieren", "status": "CURRENT",
                         "progress": 3, "score": 9, "episodes": 28, "format": "TV",
                         "year": 2023, "airing": "FINISHED", "updated": 1700000000,
                         "poster": None}],
            "_ts": _time.time(),
        }))
        body = self.get_library()
        self.assertIsNotNone(body["spine"])
        self.assertEqual(body["spine"]["entries"][0]["id"], 154587)
        self.assertEqual(body["spine"]["entries"][0]["capability"], "none")

    def test_anilist_spine_carries_its_mode(self):
        import time as _time
        self.cache.unlink(missing_ok=True)
        self.cache.write_text(json.dumps({
            "user": {"name": "X"}, "entries": [], "_ts": _time.time()}))
        self.assertEqual(self.get_library()["spine"]["mode"], "anilist")


def local_show_watched(sid, n, watched=(), last_played=None):
    """A local show whose episodes carry watch facts the way merge_db
    delivers them: watched flag + last_played stamp per episode."""
    return {"id": sid, "title": sid.title(), "collection": None, "kind": "show",
            "meta": None,
            "seasons": [{"key": 1, "season": 1, "label": "Season 1",
                         "episodes": [{"id": f"{sid}-{i}", "number": i + 1,
                                       "watched": 1 if i in watched else 0,
                                       "last_played": last_played}
                                      for i in range(n)]}]}


class LocalSpineTests(unittest.TestCase):
    """TODO 17 (Decision 6): no-AniList mode builds the SAME spine shape
    from local watch state — sections derived from database.db progress,
    curation status winning where the user set one, everything installed."""

    def test_never_touched_show_is_planning(self):
        spine = library.spine_build_local([local_show_watched("solo", 12)], {})
        e = spine["entries"][0]
        self.assertEqual(e["status"], "PLANNING")
        self.assertEqual(e["progress"], 0)
        self.assertEqual(spine["untracked"], [])

    def test_started_show_is_watching(self):
        spine = library.spine_build_local(
            [local_show_watched("started", 12, watched={0, 1, 4})], {})
        e = spine["entries"][0]
        self.assertEqual(e["status"], "CURRENT")
        # progress = episodes watched, container-honest (a multi-season
        # folder's per-season numbers can't map onto one integer)
        self.assertEqual(e["progress"], 3)

    def test_partially_played_but_unmarked_is_watching_too(self):
        """A resume position with no watched tick still reads as 'watching'
        — that is exactly the show the user is in the middle of."""
        spine = library.spine_build_local(
            [local_show_watched("resuming", 12, last_played="2026-10-16 10:00:00")],
            {})
        self.assertEqual(spine["entries"][0]["status"], "CURRENT")

    def test_all_watched_show_is_completed(self):
        spine = library.spine_build_local(
            [local_show_watched("done", 12, watched=set(range(12)))], {})
        self.assertEqual(spine["entries"][0]["status"], "COMPLETED")

    def test_curation_status_wins_over_derivation(self):
        """Decision 18.5: manually-edited curation survives — a paused show
        with watched episodes stays in Paused, not Watching."""
        scan = [local_show_watched("paused", 12, watched={0, 1})]
        spine = library.spine_build_local(scan, {"sid:paused": {"status": "PAUSED"}})
        self.assertEqual(spine["entries"][0]["status"], "PAUSED")

    def test_matched_curation_ref_is_used(self):
        scan = [{"id": "m", "title": "M", "collection": None, "kind": "show",
                 "meta": {"anilist_id": 99},
                 "seasons": [{"key": 1, "season": 1, "label": "Season 1",
                              "episodes": [{"id": "m-0", "number": 1,
                                            "watched": 0, "last_played": None}]}]}]
        spine = library.spine_build_local(scan, {"aid:99": {"score": 8.5}})
        e = spine["entries"][0]
        self.assertEqual(e["score"], 8.5)

    def test_everything_is_installed_and_deep_linked(self):
        spine = library.spine_build_local([local_show_watched("a", 5),
                                       local_show_watched("b", 7)], {})
        for e in spine["entries"]:
            self.assertEqual(e["capability"], "installed")
            self.assertEqual(e["local_show_id"], e["id"])

    def test_movie_kind_maps_to_movie_format(self):
        show = local_show_watched("film", 1)
        show["kind"] = "movie"
        spine = library.spine_build_local([show], {})
        self.assertEqual(spine["entries"][0]["format"], "MOVIE")

    def test_matched_metadata_surfaces_format_and_year(self):
        show = local_show_watched("m", 5)
        show["meta"] = {"anilist_id": 9, "format": "TV", "year": 2020}
        e = library.spine_build_local([show], {})["entries"][0]
        self.assertEqual(e["format"], "TV")
        self.assertEqual(e["year"], 2020)

    def test_updated_follows_newest_activity(self):
        """Sort 'recently updated' rides the newest playback or curation
        stamp, converted to epoch seconds like the AniList entries."""
        spine = library.spine_build_local(
            [local_show_watched("act", 4, watched={1},
                                last_played="2026-10-10 12:00:00")],
            {"sid:act": {"updated_at": "2026-10-14 08:00:00"}})
        import datetime as _dt
        want = _dt.datetime(2026, 10, 14, 8, 0, 0,
                            tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(spine["entries"][0]["updated"], want)

    @staticmethod
    def online_twin(media_id, n, watched=(), last_played=None):
        show = local_show_watched(f"aid:{media_id}", n, watched=set(watched), last_played=last_played)
        show["meta"] = {"anilist_id": media_id}
        return show

    @staticmethod
    def folder(media_id, n, watched=()):
        show = local_show_watched("disk", n, watched=set(watched))
        show["meta"] = {"anilist_id": media_id}
        return show

    def test_folder_and_online_show_of_one_entry_are_one_card(self):
        """TODO 152: Chainsaw Man, 12/12 on disk and 11/12 online."""
        spine = library.spine_build_local([self.folder(7, 12, range(12))], {},
                                          [self.online_twin(7, 12, range(11))])
        self.assertEqual(len(spine["entries"]), 1)
        e = spine["entries"][0]
        self.assertEqual((e["id"], e["progress"], e["episodes"]), ("disk", 12, 12))
        self.assertEqual((e["status"], e["capability"]), ("COMPLETED", "installed"))

    def test_card_takes_the_higher_count_from_online_too(self):
        """Blue Box: 7 files, 4 seen; all 25 seen online."""
        e = library.spine_build_local([self.folder(7, 7, range(4))], {},
                                      [self.online_twin(7, 25, range(25))])["entries"]
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["progress"], e[0]["episodes"]), (25, 25))
        self.assertEqual((e[0]["status"], e[0]["capability"]), ("COMPLETED", "partial"))
        self.assertEqual(e[0]["installed"], 7)

    def test_online_activity_counts_for_the_one_card(self):
        e = library.spine_build_local([self.folder(7, 12)], {},
                                      [self.online_twin(7, 12, (), "2026-10-10 12:00:00")])["entries"][0]
        self.assertEqual(e["status"], "CURRENT")
        self.assertGreater(e["updated"], 0)

    def test_online_only_show_keeps_its_card(self):
        entries = library.spine_build_local([self.folder(7, 12)], {},
                                            [self.online_twin(8, 12, range(3))])["entries"]
        self.assertEqual(sorted(str(e["id"]) for e in entries), ["aid:8", "disk"])

    def test_two_folders_of_one_entry_count_online_once(self):
        other = self.folder(7, 6)
        other["id"] = "disk2"
        entries = library.spine_build_local([self.folder(7, 12), other], {},
                                            [self.online_twin(7, 12, range(3))])["entries"]
        self.assertEqual([e["progress"] for e in entries], [3, 0])

    def test_online_season_counts_only_against_its_own_part_of_the_folder(self):
        """A folder of two entries: the online show of the first compares
        with those episodes, the second entry's stay on top."""
        show = self.folder(7, 20, range(12, 15))
        show["seasons"][0]["releases"] = [
            {"id": 7, "first_ep": 1, "last_ep": 12, "offset": 0, "total": 12},
            {"id": 8, "first_ep": 13, "last_ep": None, "offset": -12, "total": 8}]
        for i, ep in enumerate(show["seasons"][0]["episodes"]):
            ep["number"] = i + 1
        e = library.spine_build_local([show], {}, [self.online_twin(7, 12, range(5))])["entries"][0]
        self.assertEqual((e["progress"], e["episodes"]), (8, 20))

    def test_unknown_stamp_sorts_last(self):
        spine = library.spine_build_local([local_show_watched("fresh", 3)], {})
        self.assertEqual(spine["entries"][0]["updated"], 0)


class LocalModePayloadTests(unittest.TestCase):
    """No-AniList mode at the payload level (Decision 6): with no account
    anywhere, /api/library carries the local spine — same UI, in place —
    and disconnect flips back to it symmetrically."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.cache = root / "anilist_cache.json"
        cls.token_file = root / "anilist_token.json"
        lib = root / "library"
        show = lib / "Show One"
        show.mkdir(parents=True)
        for n in (1, 2):
            (show / f"ep{n:02d}.mkv").write_bytes(b"")
        cls.patches = [
            patch.object(config, "DB", root / "hub.db"),
            patch.object(config, "LIBRARY", lib),
            patch.object(config, "ANILIST_CACHE", cls.cache),
            patch.object(config, "ANILIST_TOKEN_FILE", cls.token_file),
            patch.object(anilist, "load_token", lambda: None),
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

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.bound}{path}",
                                     headers={"Host": "127.0.0.1:8787"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    def post(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Host": "127.0.0.1:8787"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read())

    def first_ep_id(self):
        return self.get("/api/library")["shows"][0]["seasons"][0]["episodes"][0]["id"]

    def test_local_spine_serves_without_any_account(self):
        body = self.get("/api/library")
        self.assertEqual(body["spine"]["mode"], "local")
        self.assertEqual(len(body["spine"]["entries"]), 1)
        self.assertEqual(body["spine"]["entries"][0]["status"], "PLANNING")

    def test_config_pinned_username_cannot_hold_anilist_mode(self):
        """Found live 2026-10-16 (Elias): after disconnect the library
        stayed on the AniList spine because the config file pinned
        anilist_username. The token is the connection (Decision 6) — a
        config username is a read hint only and must never keep the spine
        in AniList mode once the user disconnects."""
        with patch.dict(CONFIG, anilist_username="TestUser"):
            body = self.get("/api/library")
        self.assertEqual(body["spine"]["mode"], "local")

    def test_watch_state_moves_the_section(self):
        eid = self.first_ep_id()
        self.post("/api/watched", {"id": eid, "watched": True})
        body = self.get("/api/library")
        self.assertEqual(body["spine"]["entries"][0]["status"], "CURRENT")

    def test_entry_panel_reads_local_curation_without_a_token(self):
        """Decision 10's fallback at the read half: the editing surface
        renders from curation when no token exists — no Connect nag."""
        r = self.post("/api/entry", {"media_id": 123, "patch": {"status": "PAUSED"}})
        self.assertTrue(r["local"])
        r = self.get("/api/entry?id=123")
        self.assertTrue(r["local"])
        self.assertEqual(r["entry"]["status"], "PAUSED")

    def test_disconnect_removes_the_account_binding(self):
        """Symmetric disconnect (Decision 6): the token is the account
        (Decision 31), so unlinking deletes it and the local spine takes
        over in place."""
        anilist.store_token("tok", user={"id": 1, "name": "Someone"})
        r = anilist.auth_disconnect()
        self.assertTrue(r["ok"])
        self.assertFalse(self.token_file.exists())

    def test_leftover_config_username_cannot_survive_disconnect(self):
        """Decision 31: a leftover config key is ignored — after disconnect
        nothing names an account any more."""
        anilist.store_token("tok", user={"id": 1, "name": "Someone"})
        with patch.dict(CONFIG, anilist_username="Someone"):
            anilist.auth_disconnect()
            self.assertIsNone(anilist.load_anilist_username())
        self.assertFalse(self.token_file.exists())


if __name__ == "__main__":
    unittest.main()
