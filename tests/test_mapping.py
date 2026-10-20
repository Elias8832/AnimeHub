"""Local mapping (phase 3 of docs/franchise-page-plan.md): which franchise
entry every local episode is, and everything that follows from it —
per-entry AniList sync, completion, and each season's own episode art.

Contract: hub.map_folder(show, anchor_id, entries, pins) returns season
key → ranges ({"id", "first_ep", "last_ep", "offset", "total"}); merge_db
attaches them to every season as `releases`, plus the stills and titles of
the entries they cover."""

import unittest
from unittest import mock

from tests import support
from tests.support import hub


def mapped_cases():
    return [c for c in support.cases() if "mapping" in c]


class Mapping(unittest.TestCase):
    def test_folders_map_onto_entries(self):
        for c in mapped_cases():
            scan = support.use_library(c)
            for path, seasons in c["mapping"].items():
                show = support.folder(scan, path)
                anchor = c["match"].get(path)
                entries = hub.build_franchise(anchor, support.anilist) if anchor else []
                got = hub.map_folder(show, anchor, entries)
                for sea in show["seasons"]:
                    with self.subTest(case=c["name"], folder=path, season=sea["label"]):
                        self.assertEqual(support.ranges(got.get(hub.season_key(sea), [])),
                                         seasons.get(sea["label"], []))

    def test_before_the_franchise_is_built(self):
        """A fresh match whose franchise isn't stored yet: a one-season
        folder is its match; a multi-season one waits rather than guess."""
        scan = support.use_library(support.case("frieren"))
        got = hub.map_folder(scan[0], 154587, [])
        self.assertEqual([support.ranges(v) for v in got.values()], [[[1, None, 154587, 0]]])
        scan = support.use_library(support.case("aot"))
        self.assertEqual(hub.map_folder(scan[0], 16498, []), {})

    def test_pins_win(self):
        scan = support.use_library(support.case("aot"))
        show = scan[0]
        entries = hub.build_franchise(16498, support.anilist)
        specials = hub.season_key(support.season(show, "Specials"))
        season1 = hub.season_key(support.season(show, "Season 1"))
        pin = [{"id": 18397, "first_ep": 1, "last_ep": None, "offset": 0, "total": 3}]
        got = hub.map_folder(show, 16498, entries, {specials: pin, season1: []})
        self.assertEqual(got[specials], pin)
        self.assertNotIn(season1, got)

    def test_main_line(self):
        def line(anchor):
            return [m["id"] for m in hub.main_line(anchor, hub.build_franchise(anchor, support.anilist))]
        # The movie between the seasons is stepped through, not a season.
        self.assertEqual(line(97986), [97986, 114745])
        self.assertEqual(line(16498), [16498, 20958, 99147, 104578, 110277, 131681, 146984, 162314])
        self.assertEqual(line(120377), [120377, 195539])
        # From a later season onward only.
        self.assertEqual(line(112641), [112641, 125367, 151384, 194884])


class Library(unittest.TestCase):
    """The mapping as the app reads it: merge_db attaches it from the
    stored franchise, and progress per entry follows."""

    def setUp(self):
        self.case = support.case("aot")
        self.scan = support.match_library(self.case, support.use_library(self.case))
        self.show = self.scan[0]
        p = mock.patch.object(hub, "refresh_entry_art")  # a pin starts it in the background
        p.start()
        self.addCleanup(p.stop)

    def test_seasons_carry_their_ranges(self):
        for label, want in self.case["mapping"]["Attack on Titan"].items():
            with self.subTest(season=label):
                self.assertEqual(support.ranges(support.season(self.show, label)["releases"]), want)

    def test_units_count_in_each_entry_s_own_numbers(self):
        units = {u["media_id"]: [n for _, n in u["eps"]] for u in hub.release_units(self.show)}
        self.assertEqual(units, {16498: list(range(1, 26)), 20958: list(range(1, 13)),
                                 99147: list(range(1, 13)), 104578: list(range(1, 11)),
                                 99634: [1, 2, 3]})

    def test_list_cards_count_their_own_episodes(self):
        entries = [{"id": 16498, "episodes": 25}, {"id": 104578, "episodes": 10}, {"id": 110277, "episodes": 16}]
        cards = {e["id"]: (e["installed"], e["capability"], e["local_show_id"])
                 for e in hub.spine_build(entries, self.scan)["entries"]}
        self.assertEqual(cards, {16498: (25, "installed", self.show["id"]),
                                 104578: (10, "installed", self.show["id"]),
                                 110277: (0, "none", None)})

    def test_unmatched_folder_maps_nothing(self):
        c = support.case("unmatched")
        scan = support.match_library(c, support.use_library(c))
        self.assertEqual(hub.release_units(scan[0]), [])

    def test_season_pin(self):
        hub.store_franchise([support.anilist(18397)])
        key = hub.season_key(support.season(self.show, "Specials"))
        self.assertTrue(hub.set_season_pin(self.show["id"], key, 18397)["ok"])
        self.assertEqual(support.ranges(support.season(self.reload(), "Specials")["releases"]),
                         [[1, None, 18397, 0]])
        hub.set_season_pin(self.show["id"], key)
        self.assertEqual(support.season(self.reload(), "Specials")["releases"], [])
        s1 = hub.season_key(support.season(self.show, "Season 1"))
        hub.set_season_pin(self.show["id"], s1, none=True)
        self.assertEqual(support.season(self.reload(), "Season 1")["releases"], [])

    def test_absolute_pin_counts_from_the_first_file(self):
        key = hub.season_key(support.season(self.show, "Season 2"))
        hub.set_season_pin(self.show["id"], key, 20958)
        self.assertEqual(support.ranges(support.season(self.reload(), "Season 2")["releases"]),
                         [[1, None, 20958, -25]])

    def reload(self):
        scan = hub.scan_library()
        hub.merge_db(scan)
        return scan[0]


class Sync(unittest.TestCase):
    """Watching AoT's Season 3 E15 is episode 3 of "Season 3 Part 2"."""

    def setUp(self):
        c = support.case("aot")
        self.scan = support.match_library(c, support.use_library(c))
        self.s3 = support.season(self.scan[0], "Season 3")["episodes"]
        for ep in self.s3[:15]:
            hub.set_watched(ep["id"], True)
        self.sent = []
        cache = {"entries": [{"id": 99147, "progress": 12, "episodes": 12, "title": "AoT S3"},
                             {"id": 104578, "progress": 0, "episodes": 10, "title": "AoT S3 Part 2"},
                             {"id": 16498, "progress": 0, "episodes": 25, "title": "AoT"}]}
        for name, value in (("anilist_connected", True), ("automation_enabled", True),
                            ("anilist_cache_read", cache), ("outbox_pending_for", 0)):
            p = mock.patch.object(hub, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(hub, "outbox_enqueue", side_effect=lambda mid, patch=None: self.sent.append((mid, patch)))
        p.start()
        self.addCleanup(p.stop)

    def test_progress_goes_to_the_episode_s_own_entry(self):
        self.assertEqual(hub.auto_track_after_play(self.s3[14]["id"]), {"progress": 3})
        self.assertEqual(self.sent, [(104578, {"progress": 3})])

    def test_completion_is_the_entry_s_finale(self):
        hub._completion_pending.clear()
        hub.completion_for_episode(self.s3[11]["id"])  # S3E12: Season 3's finale
        hub.completion_for_episode(self.s3[12]["id"])  # S3E13: Part 2's first
        self.assertEqual([e["media_id"] for e in hub._completion_pending], [99147])

    def test_watched_marks_from_anilist(self):
        shows = [hub.trackable(self.scan[0])]
        hub.apply_anilist_watched(shows, [{"id": 104578, "progress": 4, "episodes": 10}])
        part2 = [ep["watched"] for ep in support.season(shows[0], "Season 3")["episodes"][12:]]
        self.assertEqual(part2, [True] * 4 + [False] * 6)


class Art(unittest.TestCase):
    def listing(self, mid, *earlier):
        return hub.own_listing(support.anilist(mid), [support.anilist(e) for e in earlier])

    def test_own_listing(self):
        self.assertIsNotNone(self.listing(16498))
        self.assertIsNotNone(self.listing(154587))

    def test_borrowed_listing_is_refused(self):
        # AoT and Kaguya-sama sequels carry their first season's stills.
        self.assertIsNone(self.listing(20958, 16498))
        self.assertIsNone(self.listing(112641, 101921))
        # Apothecary Season 1 carries Season 2's (numbered 25–48).
        self.assertIsNone(self.listing(161645))

    def test_anizip_fills_in(self):
        m = support.anilist(20958)
        got = hub.episode_listing(m, [support.anilist(16498)], lambda mid: {"1": {"image": "u1", "title": "t1"}})
        self.assertEqual(got, {"thumbs": {"1": "u1"}, "titles": {"1": "t1"}, "source": "anizip"})
        self.assertIsNone(hub.episode_listing(m, [support.anilist(16498)], lambda mid: None))

    def test_anizip_specials_numbering(self):
        """ani.zip as it answers for Kaguya-sama's "The First Kiss That
        Never Ends" (4 episodes): TVDB files it as specials, so "1" is the
        theatrical cut and the episodes are "O1".."O4"."""
        eps = {"1": {"image": "movie", "title": "Complete Movie"},
               **{f"O{n}": {"image": f"e{n}", "title": f"t{n}"} for n in range(1, 5)}}
        self.assertEqual({k: v["title"] for k, v in hub.anizip_pick(eps, 4).items()},
                         {"1": "t1", "2": "t2", "3": "t3", "4": "t4"})
        # A season with its own plain numbers keeps them.
        plain = {**{str(n): {"image": f"p{n}", "title": None} for n in range(1, 13)}, "O1": {"image": "x", "title": None}}
        self.assertEqual(sorted(hub.anizip_pick(plain, 12), key=int), [str(n) for n in range(1, 13)])

    def test_corrected_still_is_not_the_old_file(self):
        """A listing fixed later (Kaguya "First Kiss": the theatrical cut
        was episode 1) gets the new picture, not the file the wrong one
        left under the same episode number."""
        support.use_library(support.case("kaguya"))

        def fake_download(url, dest):
            dest.write_bytes(url.encode())
            return True

        with mock.patch.object(hub, "download", side_effect=fake_download):
            old = hub.commit_thumbs(151384, {"thumbs": {"1": "https://x/movie.jpg"}}, prefix="z")[1]
            new = hub.commit_thumbs(151384, {"thumbs": {"1": "https://x/episode1.jpg"}}, prefix="z")[1]
        self.assertNotEqual(old, new)
        self.assertEqual((hub.EPIMGS / new.rsplit("/", 1)[1]).read_bytes(), b"https://x/episode1.jpg")

    def test_entry_opened_during_a_pass_is_not_dropped(self):
        """Opening Season 3 while Season 2's stills are still downloading:
        both get them (the second open used to be skipped)."""
        c = support.case("kaguya")
        support.match_library(c, support.use_library(c))
        opened = []

        def anizip(mid):
            if mid == 112641 and not opened:
                opened.append(hub.refresh_entry_art(ids={125367}, anizip=anizip))  # finds the pass running
            return {"1": {"image": f"z{mid}", "title": f"t{mid}"}}

        with mock.patch.object(hub, "commit_thumbs", side_effect=lambda mid, raw, prefix="": {
                int(k): f"/epimg/{prefix}{mid}.{k}.jpg" for k in raw["thumbs"] or {}}):
            self.assertEqual(hub.refresh_entry_art(ids={112641}, anizip=anizip), 2)
        self.assertEqual(opened, [0])
        self.assertEqual(sorted(hub.entry_art_all([112641, 125367])), [112641, 125367])

    def test_season_art_spans_entries(self):
        rs = [{"id": 99147, "first_ep": 1, "last_ep": 12, "offset": 0},
              {"id": 104578, "first_ep": 13, "last_ep": None, "offset": -12}]
        arts = {99147: {"thumbs": {"1": "a1", "13": "wrong"}}, 104578: {"thumbs": {"1": "b1"}, "titles": {"2": "t"}}}
        self.assertEqual(hub.season_art(rs, arts), ({"1": "a1", "13": "b1"}, {"14": "t"}))

    def test_refresh_stores_and_merge_draws(self):
        c = support.case("kaguya")
        scan = support.match_library(c, support.use_library(c))
        with mock.patch.object(hub, "commit_thumbs", side_effect=lambda mid, raw, prefix="": {
                int(k): f"/epimg/{prefix}{mid}.{k}.jpg" for k in raw["thumbs"] or {}}):
            n = hub.refresh_entry_art(scan, anizip=lambda mid: {"1": {"image": f"z{mid}", "title": None}})
        self.assertEqual(n, 3)
        hub.merge_db(scan)
        thumbs = [support.season(scan[0], f"Season {i}")["thumbs"]["1"] for i in (1, 2, 3)]
        self.assertEqual(thumbs, ["/epimg/101921.1.jpg", "/epimg/z112641.1.jpg", "/epimg/z125367.1.jpg"])
        # Stored: the next pass has nothing to do.
        self.assertEqual(hub.refresh_entry_art(scan, anizip=lambda mid: self.fail("fetched again")), 0)


class Migration(unittest.TestCase):
    def test_only_pins_survive(self):
        support.use_library(support.case("frieren"))
        with hub.db() as c:
            c.execute("INSERT INTO season_releases(show_id, sea_key, first_ep, media_id, source) VALUES('a','1|Season 1',1,5,'auto')")
            c.execute("INSERT INTO season_releases(show_id, sea_key, first_ep, media_id, source) VALUES('b','1|Season 1',1,6,'manual')")
            c.execute("DROP TABLE entry_art")
            c.execute("PRAGMA user_version = 15")
        hub.init_db()
        with hub.db() as c:
            self.assertEqual(c.execute("SELECT show_id FROM season_releases").fetchall(), [("b",)])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM entry_art").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
