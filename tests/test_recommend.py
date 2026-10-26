"""Home's "Recommended for you" row (TODO 65): seeds, ranking, the
because-you-liked pick, listed shows left out, and the cache/build states."""

import json
import time
import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import recommend
from animehub.config import config


def rec(mid, title, rating):
    return {"rating": rating, "mediaRecommendation": {"id": mid, "title": {"english": title}, "format": "TV",
                                                      "seasonYear": 2020, "coverImage": {"extraLarge": "p%d" % mid}}}


LISTS = {"user": {"name": "me"}, "favourites": [5], "entries": [
    {"id": 1, "title": "Frieren", "status": "COMPLETED", "score": 10, "updated": 3},
    {"id": 2, "title": "Monster", "status": "COMPLETED", "score": 9, "updated": 9},
    {"id": 3, "title": "Meh", "status": "COMPLETED", "score": 5},
    {"id": 4, "title": "Planned", "status": "PLANNING", "score": 0},
    {"id": 5, "title": "Fav", "status": "CURRENT", "score": 0, "updated": 1},
]}


class Ranking(unittest.TestCase):
    def test_seeds_are_your_best_watched_shows(self):
        self.assertEqual([e["id"] for e in recommend._seeds(LISTS)], [1, 2, 5])

    def test_shows_liked_across_seeds_rank_first(self):
        seeds = recommend._seeds(LISTS)
        data = {"s0": {"recommendations": {"nodes": [rec(10, "Huge", 5000), rec(11, "Shared", 300), rec(12, "Down", -4)]}},
                "s1": {"recommendations": {"nodes": [rec(11, "Shared", 400)]}},
                "s2": {"recommendations": {"nodes": [{"rating": 50, "mediaRecommendation": None}]}}}
        items = recommend.merge(seeds, data)
        self.assertEqual([r["id"] for r in items], [11, 10])  # unrated/negative and empty nodes dropped
        self.assertEqual(items[0]["because"], {"id": 1, "title": "Frieren"})
        self.assertEqual(items[0]["poster"], "p11")

    def test_query_asks_once_per_seed(self):
        q = recommend.build_query(recommend._seeds(LISTS))
        self.assertEqual(q.count("Media(id:"), 3)
        self.assertIn("s2: Media(id:5,type:ANIME)", q)


class States(unittest.TestCase):
    def setUp(self):
        recommend._cache_path().unlink(missing_ok=True)
        self.addCleanup(lambda: recommend._cache_path().unlink(missing_ok=True))

    def test_no_account_means_none(self):
        with mock.patch.object(recommend.anilist, "load_anilist_username", return_value=None):
            self.assertEqual(recommend.recommendations(), {"state": "none", "items": []})

    def test_first_read_builds_then_leaves_out_listed_shows(self):
        answer = {"data": {"s0": {"recommendations": {"nodes": [rec(4, "Planned", 900), rec(20, "New", 100)]}}}}
        with mock.patch.object(recommend.anilist, "load_anilist_username", return_value="me"), \
             mock.patch.object(recommend.anilist, "anilist_cache_stale", return_value=LISTS), \
             mock.patch.object(recommend.anilist, "anilist_request", return_value=answer) as req, \
             mock.patch.object(recommend.threading, "Thread") as th:
            th.return_value.start.side_effect = lambda: recommend._build(*th.call_args.kwargs["args"])
            first = recommend.recommendations()
            self.assertEqual(first["state"], "loading")
            self.assertEqual(req.call_count, 1)
            out = recommend.recommendations()
        self.assertEqual(out["state"], "ready")
        self.assertEqual([r["id"] for r in out["items"]], [20])  # 4 is on Planning already
        self.assertEqual(req.call_count, 1)  # fresh cache: no second request

    def test_changed_seeds_rebuild_but_serve_the_old_set_meanwhile(self):
        recommend._write_cache({"key": "old", "ts": time.time(), "items": [{"id": 30, "title": "Old"}]})
        with mock.patch.object(recommend.anilist, "load_anilist_username", return_value="me"), \
             mock.patch.object(recommend.anilist, "anilist_cache_stale", return_value=LISTS), \
             mock.patch.object(recommend, "_start") as start:
            out = recommend.recommendations()
        start.assert_called_once()
        self.assertEqual(out, {"state": "ready", "items": [{"id": 30, "title": "Old"}]})
        self.assertTrue(str(recommend._cache_path()).startswith(str(config.DATA)))
        json.loads(recommend._cache_path().read_text(encoding="utf-8"))


class DownloadProgress(unittest.TestCase):
    def test_size_and_time_left(self):
        from animehub import anicli
        line = "[download]  64.2% of ~ 412.34MiB at  2.10MiB/s ETA 02:03 (frag 30/120)"
        self.assertEqual(anicli.DL_SIZE_RE.search(line).groups(), ("412.34", "M"))
        self.assertEqual(anicli.DL_ETA_RE.search(line).group(1), "02:03")


if __name__ == "__main__":
    unittest.main()
