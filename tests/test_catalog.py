"""The Browse tab's data (TODO 260): AniList's catalog, filtered, kept in memory only."""

import unittest
from datetime import datetime
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import catalog

M = {"id": 7, "title": {"english": None, "romaji": "Kusuriya"}, "format": "TV", "episodes": 12, "seasonYear": 2026,
     "season": "FALL", "status": "RELEASING", "averageScore": 85, "popularity": 9, "genres": ["Drama"],
     "coverImage": {"extraLarge": "p.jpg", "color": "#e4a128"}, "bannerImage": "b.jpg", "studios": {"nodes": [{"name": "OLM"}]}}


def page_answer(payload):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": True}, "media": [M]}}}


class Seasons(unittest.TestCase):
    def test_december_is_next_years_winter(self):
        self.assertEqual(catalog.season_of(datetime(2026, 12, 3)), ("WINTER", 2027))
        self.assertEqual(catalog.season_of(datetime(2026, 11, 21)), ("FALL", 2026))
        self.assertEqual(catalog.season_of(datetime(2027, 1, 2)), ("WINTER", 2027))

    def test_steps_cross_the_year(self):
        self.assertEqual(catalog.step("FALL", 2026, 1), ("WINTER", 2027))
        self.assertEqual(catalog.step("WINTER", 2027, -1), ("FALL", 2026))
        self.assertEqual(catalog.step("SPRING", 2026, -5), ("WINTER", 2025))


class Variables(unittest.TestCase):
    def test_each_list_has_its_own_order(self):
        self.assertEqual(catalog.variables({"list": "trending"})["sort"], ["TRENDING_DESC"])
        self.assertEqual(catalog.variables({"list": "top"})["sort"], ["SCORE_DESC", "POPULARITY_DESC"])
        self.assertEqual(catalog.variables({"list": "top", "sort": "title"})["sort"], ["TITLE_ROMAJI"])

    def test_season_list_is_that_season(self):
        v = catalog.variables({"list": "season", "season": "FALL", "year": 2026})
        self.assertEqual((v["season"], v["year"]), ("FALL", 2026))

    def test_years_are_whole_years_movies_included(self):
        v = catalog.variables({"list": "all", "fyear": (2010, 2019)})
        self.assertEqual((v["from"], v["to"]), (20100000, 20200000))
        self.assertNotIn("year", v)

    def test_a_season_list_keeps_its_year(self):
        v = catalog.variables({"list": "season", "season": "FALL", "year": 2026, "fyear": (2010, 2019)})
        self.assertEqual(v["year"], 2026)
        self.assertNotIn("from", v)

    def test_score_range_counts_both_ends(self):
        v = catalog.variables({"list": "all", "score": (70, 85)})
        self.assertEqual((v["score"], v["scoreMax"]), (69, 86))
        v = catalog.variables({"list": "all", "score": (0, 100)})
        self.assertNotIn("score", v); self.assertNotIn("scoreMax", v)

    def test_filters_pass_through(self):
        v = catalog.variables({"list": "popular", "genres": ["Action", "Drama"], "format": "MOVIE", "fseason": "SPRING"})
        self.assertEqual((v["genres"], v["format"], v["season"]), (["Action", "Drama"], "MOVIE", "SPRING"))


class Parse(unittest.TestCase):
    def test_defaults_to_the_overview(self):
        self.assertEqual(catalog.parse({}), {"list": "overview", "page": 1})

    def test_bad_values_are_refused(self):
        for qs in ({"list": ["nope"]}, {"format": ["CD"]}, {"score": ["101"]}, {"score": ["x"]}, {"score": ["90-70"]}, {"sort": ["random"]},
                   {"season": ["FALL"]}, {"season": ["AUTUMN"], "year": ["2026"]}, {"fyear": ["soon"]}, {"fyear": ["2010-2020-2030"]}, {"page": ["0"]}):
            with self.subTest(qs=qs), self.assertRaises(ValueError):
                catalog.parse(qs)

    def test_reads_everything(self):
        got = catalog.parse({"list": ["season"], "season": ["SUMMER"], "year": ["2026"], "genres": ["Action,Drama"],
                             "format": ["TV"], "score": ["70-90"], "fyear": ["2015"], "sort": ["score"], "page": ["2"]})
        self.assertEqual(got, {"list": "season", "season": "SUMMER", "year": 2026, "genres": ["Action", "Drama"],
                               "format": "TV", "score": (70, 90), "fyear": (2015, 2015), "sort": "score", "page": 2})


class Answers(unittest.TestCase):
    def setUp(self):
        catalog._cache.clear()
        self.addCleanup(catalog._cache.clear)

    def test_card_shape(self):
        self.assertEqual(catalog.item(M), {"id": 7, "title": "Kusuriya", "format": "TV", "episodes": 12, "year": 2026,
                                           "season": "FALL", "airing": "RELEASING", "average": 85, "popularity": 9,
                                           "genres": ["Drama"], "poster": "p.jpg", "color": "#e4a128", "banner": "b.jpg", "studio": "OLM"})

    def test_a_page_is_asked_once_then_reused(self):
        with mock.patch.object(catalog.anilist, "anilist_request", side_effect=page_answer) as req:
            a = catalog.catalog({"list": ["trending"]})
            b = catalog.catalog({"list": ["trending"]})
            catalog.catalog({"list": ["trending"], "page": ["2"]})
        self.assertEqual(a, b)
        self.assertEqual(a["items"][0]["id"], 7)
        self.assertTrue(a["has_next"])
        self.assertEqual(req.call_count, 2)
        self.assertNotIn("Authorization", str(req.call_args))  # the catalog needs no login

    def test_an_old_answer_is_asked_again(self):
        with mock.patch.object(catalog.anilist, "anilist_request", side_effect=page_answer) as req, \
             mock.patch.object(catalog.time, "time", side_effect=[0, 0, catalog.TTL + 1, catalog.TTL + 1]):
            catalog.catalog({"list": ["popular"]})
            catalog.catalog({"list": ["popular"]})
        self.assertEqual(req.call_count, 2)

    def test_top_100_stops_at_100(self):
        with mock.patch.object(catalog.anilist, "anilist_request", side_effect=page_answer) as req:
            self.assertFalse(catalog.catalog({"list": ["top"], "page": ["2"]})["has_next"])
            self.assertEqual(catalog.catalog({"list": ["top"], "page": ["3"]})["items"], [])
        self.assertEqual(req.call_count, 1)

    def test_season_lists_default_to_now(self):
        with mock.patch.object(catalog.anilist, "anilist_request", side_effect=page_answer) as req:
            catalog.catalog({"list": ["next"]}, now=datetime(2026, 11, 21))
        v = req.call_args.args[0]["variables"]
        self.assertEqual((v["season"], v["year"]), ("WINTER", 2027))

    def test_overview_is_one_request(self):
        rows = {k: {"media": [M]} for k in catalog.ROWS}
        with mock.patch.object(catalog.anilist, "anilist_request", return_value={"data": rows}) as req:
            out = catalog.catalog({}, now=datetime(2026, 11, 21))
            catalog.catalog({}, now=datetime(2026, 11, 21))
        self.assertEqual(req.call_count, 1)
        self.assertEqual(out["seasons"]["next"]["label"], "Winter 2027")
        self.assertEqual(set(out["rows"]), set(catalog.ROWS))

    def test_anilist_error_is_raised(self):
        with mock.patch.object(catalog.anilist, "anilist_request", return_value={"errors": [{"message": "bad"}]}):
            with self.assertRaises(RuntimeError):
                catalog.catalog({"list": ["popular"]})


if __name__ == "__main__":
    unittest.main()
