"""The show page's About extras (TODO 172): genres, tags and characters
from AniList's public data, through GET /api/about?id=."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError

from tests import support  # noqa: F401  (isolated dirs)
from animehub import anilist, database, server
from animehub.config import config

MEDIA = {"id": 16498, "genres": ["Action", "Drama"],
         "studios": {"nodes": [{"id": 858, "name": "Wit Studio"}]},
         "trailer": {"id": "MGRm4IzK1SQ", "site": "youtube", "thumbnail": "https://i.ytimg.com/vi/MGRm4IzK1SQ/hqdefault.jpg"},
         "rankings": [{"rank": 22, "type": "RATED", "allTime": True, "year": None, "season": None, "context": "highest rated all time"},
                      {"rank": 1, "type": "POPULAR", "allTime": False, "year": 2013, "season": "SPRING", "context": "most popular"},
                      {"rank": 150, "type": "POPULAR", "allTime": False, "year": 2013, "season": None, "context": "most popular"}],
         "externalLinks": [{"url": "https://www.crunchyroll.com/series/GR751KNZY", "site": "Crunchyroll", "type": "STREAMING"},
                           {"url": "https://www.crunchyroll.com/dup", "site": "Crunchyroll", "type": "STREAMING"},
                           {"url": "http://insecure.example/x", "site": "Bad", "type": "STREAMING"},
                           {"url": "https://shingeki.tv", "site": "Official Site", "type": "INFO"}],
         "relations": {"edges": [
             {"relationType": "SEQUEL", "node": {"id": 20958, "type": "ANIME", "format": "TV", "seasonYear": 2017, "averageScore": 84, "title": {"english": "Season 2"}, "coverImage": {"extraLarge": "https://x/s2.jpg"}}},
             {"relationType": "SPIN_OFF", "node": {"id": 21281, "type": "ANIME", "format": "TV", "seasonYear": 2015, "averageScore": 69, "title": {"english": "Junior High"}, "coverImage": {"extraLarge": "https://x/jh.jpg", "color": "#55aaee"}}},
             {"relationType": "ADAPTATION", "node": {"id": 53390, "type": "MANGA", "format": "MANGA", "seasonYear": 2009, "title": {"english": "Manga"}}}]},
         "tags": [{"name": "Kaiju", "rank": 93, "isMediaSpoiler": False, "isGeneralSpoiler": False, "isAdult": False},
                  {"name": "Tragedy", "rank": 89, "isMediaSpoiler": True, "isGeneralSpoiler": False, "isAdult": False},
                  {"name": "Weak vote", "rank": 31, "isMediaSpoiler": False, "isGeneralSpoiler": False, "isAdult": False},
                  {"name": "Nudity", "rank": 80, "isMediaSpoiler": False, "isGeneralSpoiler": False, "isAdult": True}],
         "characters": {"edges": [
             {"role": "MAIN", "node": {"id": 1, "name": {"full": "Eren Yeager"}, "image": {"large": "https://x/e.jpg"}},
              "voiceActors": [{"id": 9, "name": {"full": "Yuuki Kaji"}, "image": {"medium": "https://x/k.jpg"}, "languageV2": "Japanese"},
                              {"id": 8, "name": {"full": "Bryce Papenbrook"}, "languageV2": "English"},
                              {"id": 7, "name": {"full": "Someone Else"}, "languageV2": "Japanese"}]},
             {"role": "SUPPORTING", "node": {"id": 2, "name": {"full": "Levi"}, "image": None}, "voiceActors": []},
             {"role": "MAIN", "node": {"id": 3, "name": None}, "voiceActors": []}]}}


def reply(media=MEDIA):
    return {"data": {"Media": media}}


class About(unittest.TestCase):
    def setUp(self):
        database.init_db()
        database.cache_del("media-about:%s:16498" % anilist.ABOUT_CACHE_VER)

    def test_shape(self):
        with mock.patch.object(anilist, "anilist_request", return_value=reply()):
            a = anilist.media_about(16498)
        self.assertEqual(a["genres"], ["Action", "Drama"])
        # weak votes and adult tags stay out; spoilers are marked, not dropped
        self.assertEqual(a["tags"], [{"name": "Kaiju", "rank": 93, "spoiler": False},
                                     {"name": "Tragedy", "rank": 89, "spoiler": True}])
        self.assertEqual([c["name"] for c in a["characters"]], ["Eren Yeager", "Levi"])
        eren, levi = a["characters"]
        self.assertEqual((eren["ja"], eren["en"], eren["role"]), ("Yuuki Kaji", "Bryce Papenbrook", "MAIN"))
        self.assertEqual((eren["ja_id"], eren["en_id"]), (9, 8))
        self.assertEqual((eren["image"], eren["ja_image"], eren["en_image"]), ("https://x/e.jpg", "https://x/k.jpg", None))
        self.assertEqual((levi["ja"], levi["en"], levi["image"], levi["ja_id"]), (None, None, None, None))
        self.assertEqual(a["studios"], [{"id": 858, "name": "Wit Studio"}])
        self.assertEqual(a["trailer"]["url"], "https://www.youtube.com/watch?v=MGRm4IzK1SQ")
        self.assertEqual([r["rank"] for r in a["rankings"]], [22, 1])  # all-time first; rank > 100 seasonal dropped
        self.assertEqual(a["watch_on"], [{"site": "Crunchyroll", "url": "https://www.crunchyroll.com/series/GR751KNZY"}])
        self.assertEqual([(r["id"], r["relation"]) for r in a["related"]], [(21281, "SPIN_OFF")])

    def test_cached_then_stale_on_failure(self):
        with mock.patch.object(anilist, "anilist_request", return_value=reply()) as r:
            anilist.media_about(16498)
            anilist.media_about(16498)
        self.assertEqual(r.call_count, 1)
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts=0 WHERE key=?", ("media-about:%s:16498" % anilist.ABOUT_CACHE_VER,))
        down = HTTPError("https://graphql.anilist.co", 500, "down", None, None)
        with mock.patch.object(anilist, "anilist_request", side_effect=down):
            self.assertEqual(anilist.media_about(16498)["genres"], ["Action", "Drama"])

    def test_endpoint(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        p = mock.patch.object(config, "PORT", port)  # the Host check
        p.start()
        self.addCleanup(p.stop)
        base = "http://127.0.0.1:%d" % port
        with mock.patch.object(anilist, "anilist_request", return_value=reply()):
            with urllib.request.urlopen(base + "/api/about?id=16498") as r:
                self.assertEqual(json.loads(r.read())["genres"], ["Action", "Drama"])
        for q, code in (("x", 400), ("0", 400)):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/about?id=" + q)
            self.assertEqual(e.exception.code, code)
            e.exception.close()
        database.cache_del("media-about:%s:5" % anilist.ABOUT_CACHE_VER)
        with mock.patch.object(anilist, "anilist_request", return_value=reply(None)):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + "/api/about?id=5")
            self.assertEqual(e.exception.code, 404)
            e.exception.close()


if __name__ == "__main__":
    unittest.main()
