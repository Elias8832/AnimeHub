"""TODO 175: AniList matches exact words only, so one typo in the search box
("freiren", "one pice") found nothing. The search box now searches a local
list of every anime title (Decision 68) and asks AniList only for the hits'
details; until the list is there, the hub asks AniList for likely spellings
in one request and keeps only shows within a slip or two."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (throwaway config/data dirs)
from animehub import anilist, database, metadata, titles
from animehub.config import config


def catalog(query_payload, shows):
    """A fake AniList: each aliased Page answers the shows whose titles hold
    every word of its search exactly, like the real one; id_in fetches."""
    q = query_payload["query"]
    if "id_in" in q:
        ids = query_payload["variables"]["ids"]
        return {"data": {"Page": {"media": [dict(s, full=True) for s in shows if s["id"] in ids]}}}
    data = {}
    for alias, search in re.findall(r'(v\d+):Page\(perPage:5\)\{media\(search:("(?:[^"\\]|\\.)*")', q):
        words = json.loads(search).split()
        data[alias] = {"media": [s for s in shows if all(w in s["title"]["romaji"].lower().split() for w in words)]}
    return {"data": data}


SHOWS = [{"id": 1, "title": {"romaji": "Naruto", "english": None}, "synonyms": []},
         {"id": 2, "title": {"romaji": "Sousou no Frieren", "english": "Frieren"}, "synonyms": []},
         {"id": 3, "title": {"romaji": "One Piece", "english": None}, "synonyms": []},
         {"id": 4, "title": {"romaji": "One Punch Man", "english": None}, "synonyms": []}]


class TypoSearch(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db = mock.patch.object(config, "DB", Path(folder.name) / "hub.db")
        db.start()
        self.addCleanup(db.stop)
        database.init_db()

    def search(self, typed):
        with mock.patch.object(anilist, "anilist_request", side_effect=lambda p: catalog(p, SHOWS)) as req:
            return [m["id"] for m in metadata.fuzzy_media_candidates(typed)], req

    def test_one_slip_finds_the_show(self):
        for typed, want in (("naurto", 1), ("narto", 1), ("freiren", 2), ("one pice", 3)):
            with self.subTest(typed=typed):
                self.assertEqual(self.search(typed)[0][:1], [want])

    def test_far_off_shows_are_dropped(self):
        found, _ = self.search("one pice")
        self.assertNotIn(4, found)  # "one" alone finds One Punch Man; "pice" is nowhere near it
        self.assertEqual(self.search("xyzqw")[0], [])

    def test_two_requests_then_cached(self):
        _, req = self.search("freiren")
        self.assertEqual(req.call_count, 2)  # every spelling in one go, then the full fields
        _, req = self.search("freiren")
        self.assertEqual(req.call_count, 0)

    def test_scores(self):
        self.assertEqual(metadata.typo_score("attak on titan", "Attack on Titan"), 1)
        self.assertEqual(metadata.typo_score("frieren", "Sousou no Frieren"), 0)
        self.assertIsNone(metadata.typo_score("nra", "Naruto"))  # short words stay exact
        self.assertEqual(metadata.typo_score("mna", "Chainsaw Man", 3), 1)
        self.assertLessEqual(len(metadata._typo_variants("a very long title with many words")), 40)



def line(mid, title, synonyms=(), anilist_known=True, **more):
    sources = [f"https://anilist.co/anime/{mid}"] if anilist_known else ["https://kitsu.app/anime/1"]
    return json.dumps({"sources": sources, "title": title, "synonyms": list(synonyms), **more})


LIST = "\n".join([
    '{"$schema":"x","license":{}}',
    line(20, "Naruto", ["NARUTO", "Наруто", "naruto tv"], type="TV", episodes=220, animeSeason={"year": 2002}),
    line(1735, "Naruto: Shippuuden", ["Naruto Shippuden"]),
    line(154587, "Sousou no Frieren", ["Frieren: Beyond Journey's End", "Frieren", "葬送のフリーレン", "Frieren Beyond"]),
    line(21, "One Piece", ["ONE PIECE", "Wan Pīsu"]),
    line(30, "One Punch Man", []),
    line(9, "Not On AniList", [], anilist_known=False),
]).encode()


class TitleList(unittest.TestCase):
    def setUp(self):
        self.index = titles._Index(titles.parse(LIST))

    def ids(self, typed):
        return [row[0] for row, _ in self.index.search(typed, 50)]

    def test_parse_keeps_anilist_entries_and_latin_titles(self):
        rows = titles.parse(LIST)
        self.assertEqual([r[0] for r in rows], [20, 1735, 154587, 21, 30])
        self.assertEqual(rows[0][2], ["naruto", "naruto tv"])  # folded, deduped, Cyrillic dropped
        self.assertEqual(rows[0][3:], ["TV", 2002, 220, "Naruto"])  # format, year, episodes, title
        self.assertEqual(rows[3][2], ["one piece", "wan pisu"])  # accents off

    def test_typos_and_half_typed_words(self):
        for typed, want in (("naurto", 20), ("nsruto", 20), ("freiren", 154587), ("freirem", 154587), ("one pice", 21),
                            ("frie", 154587), ("one pi", 21), ("frieren beyond journys end", 154587),
                            ("sousou no frirn", 154587)):  # a mangled word left out, the rest close
            with self.subTest(typed=typed):
                self.assertEqual(self.ids(typed)[:1], [want])

    def test_the_very_title_comes_first(self):
        self.assertEqual(self.ids("naruto")[:2], [20, 1735])

    def test_far_off_finds_nothing(self):
        self.assertEqual(self.ids("xyzqw"), [])
        self.assertEqual(self.ids("one pxxxx"), [])
        self.assertEqual(self.ids("nra"), [])  # short words stay exact

    def test_downloaded_once_then_from_disk(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        big = LIST + b"\n" + b"\n".join(line(100000 + n, f"Filler {n}").encode() for n in range(1000))
        with mock.patch.object(config, "CACHE", Path(folder.name)), mock.patch.object(titles, "_index", None), \
                mock.patch.object(titles, "_fetch", return_value=big) as fetch:
            titles.ensure(wait=True)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(titles.search("freiren")[0][0][0], 154587)
            titles._index = None
            titles.ensure(wait=True)  # fresh on disk: loaded, not fetched
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(titles.search("naurto")[0][0][0], 20)

    def test_a_failed_download_leaves_the_old_search(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with mock.patch.object(config, "CACHE", Path(folder.name)), mock.patch.object(titles, "_index", None), \
                mock.patch.object(titles, "_failed_at", 0.0), mock.patch.object(titles, "_fetch", side_effect=OSError("offline")):
            titles.ensure(wait=True)
            self.assertIsNone(titles.search("naruto"))
            self.assertGreater(titles._failed_at, 0)


class SearchBox(unittest.TestCase):
    """metadata.local_media_candidates: the list's hits, AniList's details,
    closest first, then the best-known show."""
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db = mock.patch.object(config, "DB", Path(folder.name) / "hub.db")
        db.start()
        self.addCleanup(db.stop)
        database.init_db()
        for name, value in (("_index", titles._Index(titles.parse(LIST))), ("ensure", lambda wait=False: None)):
            patch = mock.patch.object(titles, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def details(self, payload):
        pop = {20: 500, 1735: 900, 154587: 300, 21: 800, 30: 700}
        ids = payload["variables"]["ids"]
        self.assertIn("id_in", payload["query"])
        return {"data": {"Page": {"media": [{"id": i, "popularity": pop[i], "title": {}} for i in ids]}}}

    def test_one_request_closest_then_best_known(self):
        with mock.patch.object(anilist, "anilist_request", side_effect=self.details) as req:
            found = metadata.local_media_candidates("naruto")
            self.assertEqual([m["id"] for m in found], [20, 1735])  # the very title beats the better-known sequel
            self.assertEqual(req.call_count, 1)
            metadata.local_media_candidates("naruto")
            self.assertEqual(req.call_count, 1)  # cached
            self.assertEqual([m["id"] for m in metadata.local_media_candidates("one")], [21, 30])

    def test_instant_rows_need_no_anilist(self):
        with mock.patch.object(anilist, "anilist_request", side_effect=AssertionError("no AniList")):
            first = metadata.instant_media_candidates("naurto")
        self.assertEqual(first[0], {"id": 20, "title": {"romaji": "Naruto", "english": None}, "format": "TV",
                                    "seasonYear": 2002, "episodes": 220, "coverImage": {}, "stub": True})
        with mock.patch.object(anilist, "anilist_request", side_effect=self.details):
            metadata.local_media_candidates("naurto")
        with mock.patch.object(anilist, "anilist_request", side_effect=AssertionError("no AniList")):
            again = metadata.instant_media_candidates("naruto")
        self.assertEqual(again[0]["popularity"], 500)  # filled in once, whole from then on
        self.assertNotIn("stub", again[0])

    def test_only_missing_shows_are_asked_for(self):
        with mock.patch.object(anilist, "anilist_request", side_effect=self.details) as req:
            metadata.local_media_candidates("naruto")
            metadata.local_media_candidates("naruto shippuden")
            self.assertEqual(req.call_count, 1)

    def test_too_short_or_no_list_falls_back(self):
        self.assertIsNone(metadata.local_media_candidates("on"))
        with mock.patch.object(titles, "_index", None):
            self.assertIsNone(metadata.local_media_candidates("naruto"))
        self.assertEqual(metadata.local_media_candidates("xyzqw"), [])


if __name__ == "__main__":
    unittest.main()
