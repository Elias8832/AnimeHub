"""The franchise page (phase 4 of docs/franchise-page-plan.md): one page
per franchise, every entry on it, and each entry's episodes as one list of
slots where a file on disk and an online episode differ only by source.

Contract: hub.franchise_page(entries, scan, links, rows, arts, online_ok,
missed) builds the page from stored data (pure); hub.franchise_view(id)
gathers that data for GET /api/franchise?id= and never waits on the
network."""

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support
from tests.support import hub


def page_of(start):
    return hub.franchise_page(hub.build_franchise(start, support.anilist), [], {}, {}, {}, online_ok=False)


def main_labels(page):
    return [(e["id"], e["label"]) for e in page["entries"] if e["group"] == "main"]


class Entries(unittest.TestCase):
    def test_main_seasons_and_extras(self):
        page = page_of(161645)
        self.assertEqual(page["title"], "The Apothecary Diaries")
        self.assertEqual(main_labels(page), [(161645, "Season 1"), (176301, "Season 2"), (195516, "Season 3"),
                                             (200927, "3rd Season Part 2")])
        extras = [(e["id"], e["format_label"]) for e in page["entries"] if e["group"] == "extra"]
        self.assertEqual(extras, [(170508, "ONA"), (186148, "ONA"), (218532, "ONA"), (200929, "Movie")])

    def test_labels(self):
        self.assertEqual([label for _, label in main_labels(page_of(16498))][:6],
                         ["Season 1", "Season 2", "Season 3", "Season 3 Part 2", "Final Season", "Final Season Part 2"])
        # "Love is War?" adds nothing worth showing: its place in the line.
        self.assertEqual([label for _, label in main_labels(page_of(101921))][:3],
                         ["Season 1", "Season 2", "Ultra Romantic"])
        self.assertEqual(main_labels(page_of(120377)), [(120377, "Season 1"), (195539, "Season 2")])
        self.assertEqual(main_labels(page_of(97986)), [(97986, "Season 1"), (114745, "The Golden City of the Scorching Sun")])

    def test_seasons_with_names_of_their_own(self):
        page = page_of(5081)
        self.assertEqual(page["title"], "Bakemonogatari")
        self.assertEqual([label for _, label in main_labels(page)][:3],
                         ["Bakemonogatari", "Nisemonogatari", "Monogatari Series Second Season"])


class View(unittest.TestCase):
    """The Apothecary Diaries: Season 1 and E1–E10 of Season 2 on disk."""

    def setUp(self):
        c = support.case("apothecary")
        self.scan = support.match_library(c, support.use_library(c))
        self.bg = {}
        for name in ("stream_resolve_async", "franchise_build_async", "refresh_entry_art"):
            p = mock.patch.object(hub, name, side_effect=lambda *a, _n=name, **k: self.bg.setdefault(_n, []).append(a or k))
            p.start()
            self.addCleanup(p.stop)
        self.anicli = mock.patch.object(hub, "anicli_path", return_value="/usr/bin/ani-cli")
        self.anicli.start()
        self.addCleanup(self.anicli.stop)

    def entry(self, page, mid):
        return next(e for e in page["entries"] if e["id"] == mid)

    def test_local_and_online_in_one_list(self):
        page = hub.franchise_view(176301)
        self.assertEqual((page["id"], page["selected"], page["building"]), (161645, 176301, False))
        slots = self.entry(page, 176301)["slots"]
        self.assertEqual([s["number"] for s in slots], list(range(1, 25)))
        self.assertEqual({s["source"] for s in slots[:10]}, {"local"})
        self.assertEqual({s["source"] for s in slots[10:]}, {"online"})
        disk = support.season(support.folder(self.scan, "The Apothecary Diaries"), "Season 2")["episodes"]
        self.assertEqual([s["id"] for s in slots[:10]], [ep["id"] for ep in disk])
        self.assertEqual(slots[10]["id"], "stream:176301:11")
        # A file on disk can still be streamed: the slot keeps both sources.
        self.assertEqual(slots[0]["online"]["id"], "stream:176301:1")

    def test_same_page_from_every_entry(self):
        a, b = hub.franchise_view(161645), hub.franchise_view(200929)
        self.assertEqual(a["entries"], b["entries"])
        self.assertEqual((a["selected"], b["selected"]), (161645, 200929))

    def test_watched_from_either_source(self):
        disk = support.season(support.folder(self.scan, "The Apothecary Diaries"), "Season 2")["episodes"]
        with hub.db() as c:
            c.execute("INSERT INTO episodes(id, watched, position, duration, last_played)"
                      " VALUES('stream:176301:3', 1, 0, 1400, '2026-10-01 20:00:00')")
            c.execute("INSERT INTO episodes(id, watched, position, duration, last_played)"
                      " VALUES(?, 0, 300, 1400, '2026-10-02 20:00:00')", (disk[3]["id"],))
        slots = self.entry(hub.franchise_view(176301), 176301)["slots"]
        self.assertTrue(slots[2]["watched"])
        self.assertEqual((slots[3]["watched"], slots[3]["position"]), (False, 300))

    def test_opening_an_entry_fetches_in_the_background(self):
        hub.franchise_view(176301)
        self.assertEqual(self.bg["stream_resolve_async"], [(176301,)])
        self.assertEqual(self.bg["refresh_entry_art"], [{"ids": {176301}}])

    def test_without_ani_cli_only_files(self):
        self.anicli.stop()
        with mock.patch.object(hub, "anicli_path", return_value=None):
            page = hub.franchise_view(176301)
        self.assertEqual([s["source"] for s in self.entry(page, 176301)["slots"]], ["local"] * 10)
        self.assertEqual(self.entry(page, 195516)["slots"], [])
        self.assertFalse(self.entry(page, 195516)["online"]["available"])
        self.anicli.start()

    def test_unknown_entry_is_built_in_the_background(self):
        page = hub.franchise_view(999999)
        self.assertEqual((page["building"], page["entries"]), (True, []))
        self.assertEqual(self.bg["franchise_build_async"], [(999999,)])

    def test_stored_entry_shows_while_its_franchise_builds(self):
        with hub.db() as c:
            c.execute("INSERT INTO media(id, data, ts) VALUES(21281, ?, 0)", (json.dumps(support.anilist(21281)),))
        page = hub.franchise_view(21281)
        self.assertEqual((page["building"], [e["id"] for e in page["entries"]]), (True, [21281]))

    def test_endpoint(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        p = mock.patch.object(hub, "PORT", port)  # the Host check
        p.start()
        self.addCleanup(p.stop)
        base = "http://127.0.0.1:%d" % port
        with urllib.request.urlopen(base + "/api/franchise?id=176301") as r:
            self.assertEqual(json.loads(r.read())["selected"], 176301)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/franchise?id=x")
        self.assertEqual(e.exception.code, 400)
        e.exception.close()


class Pure(unittest.TestCase):
    def setUp(self):
        c = support.case("apothecary")
        self.scan = support.match_library(c, support.use_library(c))
        self.entries = hub.franchise_of(161645)[1]

    def slots(self, mid, **kw):
        page = hub.franchise_page(self.entries, self.scan, kw.get("links", {}), {}, kw.get("arts", {}),
                                  True, kw.get("missed", frozenset()))
        return next(e for e in page["entries"] if e["id"] == mid)["slots"]

    def test_linked_episode_list_wins(self):
        link = {"media_id": 176301, "episodes": ["1", "2", "12.5"], "mode": "sub"}
        slots = self.slots(176301, links={176301: link})
        self.assertEqual([s["number"] for s in slots], list(range(1, 11)) + [12.5])
        self.assertEqual(slots[-1]["id"], "stream:176301:12.5")

    def test_no_online_match(self):
        self.assertEqual([s["source"] for s in self.slots(176301, missed={176301})], ["local"] * 10)

    def test_stills_and_titles(self):
        slots = self.slots(176301, arts={176301: {"thumbs": {"11": "/epimg/z176301.11.jpg"}, "titles": {"1": "t"}}})
        self.assertEqual((slots[0]["title"], slots[10]["thumb"]), ("t", "/epimg/z176301.11.jpg"))

    def test_other_local_files(self):
        c = support.case("aot")
        scan = support.match_library(c, support.use_library(c))
        page = hub.franchise_page(hub.franchise_of(16498)[1], scan, {}, {}, {}, False)
        self.assertEqual([(o["label"], len(o["episodes"])) for o in page["other_local"]], [("Specials", 3)])


if __name__ == "__main__":
    unittest.main()
