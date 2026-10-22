"""Per-entry units of a folder (map_numbered_seasons → merge_db's season
`releases` → release_units): every per-entry feature (progress sync,
completion, AniList watched marks, spine counts) reads those instead of
counting seasons.

The matcher cases are the real library's failures (verified against the
live AniList API 2026-10-20): Attack on Titan's 22-file Season 3 is
AniList's 12 + 10 and its two Final Chapters files are two one-episode
specials; SAKAMOTO DAYS Part 1/2 are two ONAs; AniList's air order puts
Tsukimonogatari between Owarimonogatari S1 and its Second Season.

Moved here from Work/tests with the franchise model: the stored-map tests
(set_release_range / release_of), the folder-side franchise line
(franchise_line, add_line_seasons, release tabs), sequel_chain, the
season-art helpers and merge_streams' online folding went with it —
test_mapping, test_franchise and test_page cover what replaced them."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, library, mapping, progress, settings
from animehub.config import config


def ep(sid, n, watched=False):
    return {"id": f"{sid}-{n}", "number": n, "watched": watched, "files": ["f"], "rel": "r"}


def season(key, label, nums, sid=None, releases=None, watched=()):
    sid = sid or f"{key}{label}"
    s = {"key": key, "season": key, "label": label,
         "episodes": [ep(sid, n, n in watched) for n in nums]}
    if releases is not None:
        s["releases"] = releases
    return s


def rng(mid, first=1, last=None, offset=0, total=None):
    return {"id": mid, "first_ep": first, "last_ep": last, "offset": offset, "total": total}


def folder(seasons, aid=None, sid="f"):
    return {"id": sid, "title": sid, "kind": "show", "collection": None,
            "meta": {"anilist_id": aid} if aid else None, "seasons": seasons}


def media(mid, eps, fmt="TV", title=None, sequels=(), prequels=()):
    edges = [{"relationType": "SEQUEL", "node": {"id": i, "format": f}} for i, f in sequels]
    edges += [{"relationType": "PREQUEL", "node": {"id": i, "format": f}} for i, f in prequels]
    return {"id": mid, "episodes": eps, "format": fmt,
            "title": {"english": title or f"Show {mid}", "romaji": None},
            "relations": {"edges": edges}}


class ReleaseUnitsTests(unittest.TestCase):
    def test_unmapped_multi_season_folder_has_no_units(self):
        """The old guard, kept: never guess which release a season is."""
        f = folder([season(1, "Season 1", range(1, 7)), season(2, "Season 2", range(1, 7))], aid=99)
        self.assertEqual(mapping.release_units(f), [])

    def test_mapped_seasons_become_their_own_units(self):
        f = folder([season(1, "Season 1", range(1, 26), releases=[rng(16498)]),
                    season(3, "Season 3", range(1, 23),
                           releases=[rng(99147, 1, 12), rng(104578, 13, None, -12)])], aid=16498)
        units = {u["media_id"]: [n for _, n in u["eps"]] for u in mapping.release_units(f)}
        self.assertEqual(units[16498], list(range(1, 26)))
        self.assertEqual(units[99147], list(range(1, 13)))
        self.assertEqual(units[104578], list(range(1, 11)))

    def test_specials_numbered_zero_belong_to_no_release(self):
        f = folder([season(1, "Season 1", [0, 1, 2], releases=[rng(99)])], aid=99)
        self.assertEqual([n for _, n in mapping.release_units(f)[0]["eps"]], [1, 2])

    def test_unit_fits_the_release_total(self):
        u = {"eps": [({}, n) for n in range(1, 13)]}
        self.assertTrue(mapping.unit_fits(u, 12))
        self.assertTrue(mapping.unit_fits(u, None))  # airing: unknown total
        self.assertFalse(mapping.unit_fits(u, 10))


class SeasonMatcherTests(unittest.TestCase):
    """map_numbered_seasons over a main line (main_line, test_mapping, builds
    the line from the stored franchise; movies are stepped over there)."""

    def mapped(self, f, *items):
        line = [media(mid, eps, fmt, title) for mid, eps, fmt, title in items]
        return {k: [(r["first_ep"], r["last_ep"], r["id"], r["offset"]) for r in v]
                for k, v in mapping.map_numbered_seasons(f, line).items()}

    def test_attack_on_titan_split_season_and_final_chapters(self):
        f = folder([season(1, "Season 1", range(1, 26)), season(2, "Season 2", range(1, 13)),
                    season(3, "Season 3", range(1, 23)), season(4, "Part 1", range(1, 17)),
                    season(4, "Part 2", range(1, 13)), season(4, "Final Chapters", [1, 2]),
                    season(0, "Specials", [1, 2, 3])], aid=16498)
        self.assertEqual(self.mapped(f, (16498, 25, "TV", "Attack on Titan"),
                                     (20958, 12, "TV", "Attack on Titan Season 2"),
                                     (99147, 12, "TV", "Attack on Titan Season 3"),
                                     (104578, 10, "TV", "Attack on Titan Season 3 Part 2"),
                                     (110277, 16, "TV", "Attack on Titan Final Season"),
                                     (131681, 12, "TV", "Attack on Titan Final Season Part 2"),
                                     (146984, 1, "SPECIAL", "THE FINAL CHAPTERS Special 1"),
                                     (162314, 1, "SPECIAL", "THE FINAL CHAPTERS Special 2")), {
            "1|Season 1": [(1, None, 16498, 0)],
            "2|Season 2": [(1, None, 20958, 0)],
            "3|Season 3": [(1, 12, 99147, 0), (13, None, 104578, -12)],
            "4|Part 1": [(1, None, 110277, 0)],
            "4|Part 2": [(1, None, 131681, 0)],
            "4|Final Chapters": [(1, 1, 146984, 0), (2, None, 162314, -1)],
        })

    def test_parts_sharing_a_season_number(self):
        f = folder([season(1, "Part 1", range(1, 12)), season(1, "Part 2", range(1, 12))], aid=177709)
        self.assertEqual(self.mapped(f, (177709, 11, "ONA", "SAKAMOTO DAYS"),
                                     (184237, 11, "ONA", "SAKAMOTO DAYS Part 2")),
                         {"1|Part 1": [(1, None, 177709, 0)], "1|Part 2": [(1, None, 184237, 0)]})

    def test_interleaved_arc_is_skipped_when_a_later_release_names_the_season(self):
        f = folder([season(1, "Season 1", range(1, 13)), season(2, "Season 2", range(1, 8))])
        self.assertEqual(self.mapped(f, (21262, 12, "TV", "Owarimonogatari"),
                                     (20918, 4, "TV", "Tsukimonogatari"),
                                     (21745, 7, "TV", "Owarimonogatari Second Season"))["2|Season 2"],
                         [(1, None, 21745, 0)])

    def test_partial_season_does_not_shift_the_next(self):
        f = folder([season(1, "Season 1", [4, 6, 12, 22, 25]), season(2, "Season 2", [1])])
        self.assertEqual(self.mapped(f, (1, 25, "TV", "Blue Box"), (2, 12, "TV", "Blue Box Season 2")),
                         {"1|Season 1": [(1, None, 1, 0)], "2|Season 2": [(1, None, 2, 0)]})

    def test_absolute_numbering_is_recognised(self):
        f = folder([season(1, "Season 1", range(1, 26)), season(2, "Season 2", range(26, 38))])
        self.assertEqual(self.mapped(f, (1, 25, "TV", "A"), (2, 12, "TV", "A Season 2"))["2|Season 2"],
                         [(26, None, 2, -25)])

    def test_chain_running_out_leaves_the_season_unmapped(self):
        f = folder([season(1, "Season 1", range(1, 13)), season(2, "Season 2", range(1, 13))])
        self.assertEqual(self.mapped(f, (1, 12, "TV", "A")), {"1|Season 1": [(1, None, 1, 0)]})

    def test_airing_release_takes_the_rest_of_the_season(self):
        f = folder([season(1, "Season 1", range(1, 29)), season(2, "Season 2", range(1, 6))])
        self.assertEqual(self.mapped(f, (1, 28, "TV", "Frieren"), (2, None, "TV", "Frieren Season 2"))["2|Season 2"],
                         [(1, None, 2, 0)])

    def test_season_labels_are_named_in_titles(self):
        self.assertTrue(mapping.names_season(media(1, 7, title="Owarimonogatari Second Season"), "Season 2"))
        self.assertTrue(mapping.names_season(media(1, 7, title="Show 2nd Season"), "Season 2"))
        self.assertTrue(mapping.names_season(media(1, 7, title="SAKAMOTO DAYS Part 2"), "Part 2"))
        self.assertFalse(mapping.names_season(media(1, 7, title="Tsukimonogatari"), "Season 2"))
        self.assertFalse(mapping.names_season(media(1, 7, title="Show Season 12"), "Season 1"))


TOKEN = {"access_token": "tok", "expires_at": None, "user_id": 42, "user_name": "Elias"}


class PerReleaseFeatureTests(unittest.TestCase):
    """Frieren in one folder: S1 → 154587 (28), S2 → 182255 (10)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        for p in (patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
                  patch.object(config, "LIBRARY", None),
                  patch.object(anilist, "load_token", lambda: dict(TOKEN))):
            p.start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.directory.cleanup)
        database.init_db()

    def frieren(self, s1=(), s2=()):
        return folder([season(1, "Season 1", range(1, 29), "s1", [rng(154587, total=28)], s1),
                       season(2, "Season 2", range(1, 11), "s2", [rng(182255, total=10)], s2)],
                      aid=154587, sid="frieren")

    def cached(self, entries):
        return patch.object(anilist, "anilist_cache_read", lambda: {"user": {"name": "E"}, "entries": entries})

    def payloads(self):
        with database.db() as c:
            return [(r[0], json.loads(r[1])["patch"]) for r in
                    c.execute("SELECT media_id, payload FROM outbox ORDER BY id")]

    def test_bulk_marking_season_two_syncs_only_season_two(self):
        f = self.frieren(s1=range(1, 29))
        with self.cached([{"id": 154587, "progress": 28, "episodes": 28},
                          {"id": 182255, "progress": 0, "episodes": 10}]), \
                patch.object(library, "media_by_id", return_value=f):
            r = progress.bulk_watched("frieren", season=1)
        self.assertEqual(r["synced"], {"progress": 10})
        self.assertEqual(self.payloads(), [(182255, {"progress": 10})])

    def test_playing_an_episode_syncs_its_own_release(self):
        f = self.frieren(s1=range(1, 29), s2=(1, 2, 3))
        with self.cached([{"id": 154587, "progress": 20, "episodes": 28},
                          {"id": 182255, "progress": 1, "episodes": 10}]), \
                patch.object(library, "episode_by_id", return_value=(f, f["seasons"][1]["episodes"][2])):
            settings.set_automation("auto_progress_sync", True)
            progress.auto_track_after_play("s2-3")
        self.assertEqual(self.payloads(), [(182255, {"progress": 3})])

    def test_auto_add_adds_the_played_season_s_own_release(self):
        f = self.frieren(s2=(1,))
        settings.set_automation("auto_add_watch", True)
        with self.cached([]), \
                patch.object(library, "episode_by_id", return_value=(f, f["seasons"][1]["episodes"][0])):
            progress.auto_track_after_play("s2-1")
        self.assertEqual(self.payloads(), [(182255, {"status": "CURRENT", "progress": 1})])

    def test_season_two_finale_completes_season_two(self):
        f = self.frieren()
        fired = []
        with self.cached([{"id": 182255, "progress": 9, "episodes": 10, "title": "Frieren S2"}]), \
                patch.object(library, "episode_by_id", return_value=(f, f["seasons"][1]["episodes"][9])), \
                patch.object(progress, "_completion_event", lambda mid, title: fired.append((mid, title))):
            progress.completion_for_episode("s2-10")
        self.assertEqual(fired, [(182255, "Frieren S2")])

    def test_season_one_episode_ten_completes_nothing(self):
        f = self.frieren()
        fired = []
        with self.cached([{"id": 182255, "progress": 9, "episodes": 10}]), \
                patch.object(library, "episode_by_id", return_value=(f, f["seasons"][0]["episodes"][9])), \
                patch.object(progress, "_completion_event", lambda mid, title: fired.append(mid)):
            progress.completion_for_episode("s1-10")
        self.assertEqual(fired, [])

    def test_spine_counts_each_release(self):
        spine = library.spine_build([{"id": 154587, "episodes": 28}, {"id": 182255, "episodes": 10}],
                                [self.frieren()])
        by = {e["id"]: (e["installed"], e["capability"], e["local_show_id"]) for e in spine["entries"]}
        self.assertEqual(by, {154587: (28, "installed", "frieren"), 182255: (10, "installed", "frieren")})
        self.assertEqual(spine["untracked"], [])

    def test_anilist_watched_marks_follow_each_season(self):
        f = self.frieren()
        library.apply_anilist_watched([f], [{"id": 154587, "progress": 28, "episodes": 28},
                                        {"id": 182255, "progress": 2, "episodes": 10}])
        s1, s2 = f["seasons"]
        self.assertTrue(all(e["watched"] for e in s1["episodes"]))
        self.assertEqual([e["watched"] for e in s2["episodes"]][:3], [True, True, False])


if __name__ == "__main__":
    unittest.main()
