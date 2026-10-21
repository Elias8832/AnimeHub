"""Franchises (phase 2 of docs/franchise-page-plan.md): which AniList
entries share one page and in which order, how that is stored, and how it
is kept up to date.

Contract: franchise.build_franchise(start_id, fetch) returns the franchise's
entries (AniList media dicts) in page order; fetch(id) returns one entry
in franchise.FRANCHISE_QUERY_ID's shape."""

import unittest

from tests import support
from animehub import anilist, database, franchise


class Franchise(unittest.TestCase):
    def expected(self):
        for c in support.cases():
            for start, ids in (c.get("franchise") or {}).items():
                yield c["name"], int(start), ids

    def ids(self, start):
        return [m["id"] for m in franchise.build_franchise(start, fetch=support.anilist)]

    def test_members_in_order(self):
        for name, start, ids in self.expected():
            with self.subTest(case=name, start=start):
                self.assertEqual(self.ids(start), ids)

    def test_same_page_from_every_entry(self):
        """Clicking any season, movie or special lands on the same page:
        the franchise is identical whichever entry it is built from."""
        for name, start, ids in self.expected():
            for mid in ids:
                with self.subTest(case=name, entry=mid):
                    self.assertEqual(self.ids(mid), ids)


class OtherLinks(unittest.TestCase):
    """An OTHER link joins only a numbered season of the same title."""

    def test_numbered_season_joins(self):
        self.assertTrue(franchise.numbered_season_of(support.anilist(120377), support.anilist(195539)))
        self.assertTrue(franchise.numbered_season_of(support.anilist(195539), support.anilist(120377)))

    def test_crossover_stays_out(self):
        # Bakemonogatari's OTHER link is the Nisekoi crossover short.
        self.assertFalse(franchise.numbered_season_of(support.anilist(5081), support.anilist(106495)))
        # AoT's is a collab clip.
        self.assertFalse(franchise.numbered_season_of(support.anilist(20692), support.anilist(123078)))

    def test_similar_title_is_not_a_season(self):
        # "Kaguya-sama: Love is War?" is season 2 by SEQUEL, not by title.
        self.assertFalse(franchise.numbered_season_of(support.anilist(101921), support.anilist(112641)))


def entry(mid, *edges, year=2020, status="FINISHED"):
    """A minimal AniList entry for storage tests."""
    return {"id": mid, "title": {"romaji": f"Show {mid}"}, "status": status, "format": "TV",
            "startDate": {"year": year},
            "relations": {"edges": [{"relationType": "SEQUEL", "node": {"id": n, "type": "ANIME", "format": "TV"}}
                                    for n in edges]}}


class Formats(unittest.TestCase):
    def test_music_videos_stay_out(self):
        show = entry(1, 2)
        show["relations"]["edges"].append(
            {"relationType": "SIDE_STORY", "node": {"id": 3, "type": "ANIME", "format": "MUSIC"}})
        catalog = {1: show, 2: entry(2, year=2021), 3: dict(entry(3), format="MUSIC")}
        self.assertEqual([m["id"] for m in franchise.build_franchise(1, catalog.get)], [1, 2])


class Storage(unittest.TestCase):
    def setUp(self):
        support.use_library(support.case("apothecary"))

    def test_any_entry_finds_its_page(self):
        fid = franchise.store_franchise(franchise.build_franchise(161645, support.anilist))
        expected = support.case("apothecary")["franchise"]["161645"]
        self.assertEqual(fid, 161645)
        for mid in expected:
            got = franchise.franchise_of(mid)
            self.assertEqual(got[0], 161645)
            self.assertEqual([m["id"] for m in got[1]], expected)

    def test_unknown_entry(self):
        self.assertIsNone(franchise.franchise_of(161645))

    def test_rebuild_moves_entries(self):
        """AniList relinks entry 2 from franchise 1 to franchise 3: it is on
        exactly one page afterwards, and an emptied franchise disappears."""
        franchise.store_franchise([entry(1, 2), entry(2, year=2021)])
        franchise.store_franchise([entry(2, year=2019), entry(3, year=2022)])
        self.assertEqual([m["id"] for m in franchise.franchise_of(1)[1]], [1])
        self.assertEqual(franchise.franchise_of(3)[0], 2)
        franchise.store_franchise([entry(1, year=2018), entry(2, year=2019), entry(3, year=2022)])
        with database.db() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT id FROM franchises")], [1])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM franchise_members").fetchone()[0], 3)


class Refresh(unittest.TestCase):
    def setUp(self):
        self.scan = support.use_library(support.case("apothecary"))
        self.fetched = []

    def fetch(self, mid):
        self.fetched.append(mid)
        return support.anilist(mid)

    def test_seeds_are_matched_folders(self):
        self.assertEqual(franchise.franchise_seeds(self.scan), set())
        self.scan[0]["meta"] = {"anilist_id": 161645}
        self.assertEqual(franchise.franchise_seeds(self.scan), {161645})

    def test_one_build_per_franchise_then_fresh(self):
        self.assertEqual(franchise.refresh_franchises({161645, 176301}, self.fetch), 1)
        self.assertEqual(len(franchise.franchise_of(176301)[1]), 8)
        self.fetched.clear()
        self.assertEqual(franchise.refresh_franchises({161645, 176301}, self.fetch), 0)
        self.assertEqual(self.fetched, [])

    def test_stale_franchise_is_rebuilt(self):
        franchise.refresh_franchises({161645}, self.fetch)
        with database.db() as c:
            c.execute("UPDATE franchises SET ts = ts - ?", (franchise.MEDIA_TTL_CHANGING + 1,))
        # Season 3 is airing, so a day is stale.
        self.assertEqual(franchise.refresh_franchises({161645}, self.fetch), 1)


class Pace(unittest.TestCase):
    """Every AniList request shares one pace below the rate limit; the
    background leaves a reserve for a page someone is waiting on."""

    def setUp(self):
        anilist._al_times.clear()
        self.addCleanup(anilist._al_times.clear)

    def test_background_leaves_a_reserve(self):
        bg, fg = anilist.ANILIST_BACKGROUND_PER_MIN, anilist.ANILIST_PER_MIN
        for i in range(bg):
            self.assertEqual(anilist._anilist_try(bg, 100 + i), 0)
        self.assertGreater(anilist._anilist_try(bg, 130), 0)       # background waits
        for i in range(fg - bg):
            self.assertEqual(anilist._anilist_try(fg, 130), 0)      # an opened page doesn't
        self.assertAlmostEqual(anilist._anilist_try(fg, 130), 30)   # until the oldest is a minute old
        self.assertGreater(anilist._anilist_try(bg, 161), 0)        # the page's requests count too
        self.assertEqual(anilist._anilist_try(bg, 180), 0)


class Migration(unittest.TestCase):
    def test_database_from_the_abandoned_rework(self):
        """A database the rework left at version 14 still gets the tables."""
        support.use_library(support.case("frieren"))
        with database.db() as c:
            for t in ("media", "franchises", "franchise_members"):
                c.execute(f"DROP TABLE {t}")
            c.execute("PRAGMA user_version = 14")
        database.init_db()
        with database.db() as c:
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], len(database.MIGRATIONS))
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"media", "franchises", "franchise_members"}, tables)


if __name__ == "__main__":
    unittest.main()
