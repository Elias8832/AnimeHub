"""The franchise page (phase 4 of docs/franchise-page-plan.md): one page
per franchise, every entry on it, and each entry's episodes as one list of
slots where a file on disk and an online episode differ only by source.

Contract: pages.franchise_page(entries, scan, links, rows, arts, online_ok,
missed) builds the page from stored data (pure); pages.franchise_view(id)
gathers that data for GET /api/franchise?id= and never waits on the
network."""

import json
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError

from tests import support
from animehub import anicli, anilist, database, franchise, library, mapping, pages, player, server, settings, streaming, sync
from animehub.config import config


def page_of(start):
    return pages.franchise_page(franchise.build_franchise(start, support.anilist), [], {}, {}, {}, online_ok=False)


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

    def test_lone_entry_keeps_its_title(self):
        """Season 2 opened from search while its franchise is still being
        found is not "Season 1"."""
        page = pages.franchise_page([support.anilist(176301)], [], {}, {}, {}, online_ok=False)
        self.assertEqual(main_labels(page), [(176301, "The Apothecary Diaries Season 2")])

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
        for mod, name in ((streaming, "stream_resolve_async"), (pages, "franchise_build_async"),
                          (mapping, "refresh_entry_art")):
            p = mock.patch.object(mod, name, side_effect=lambda *a, _n=name, **k: self.bg.setdefault(_n, []).append(a or k))
            p.start()
            self.addCleanup(p.stop)
        self.anicli = mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli")
        self.anicli.start()
        self.addCleanup(self.anicli.stop)

    def entry(self, page, mid):
        return next(e for e in page["entries"] if e["id"] == mid)

    def test_local_and_online_in_one_list(self):
        page = pages.franchise_view(176301)
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
        a, b = pages.franchise_view(161645), pages.franchise_view(200929)
        self.assertEqual(a["entries"], b["entries"])
        self.assertEqual((a["selected"], b["selected"]), (161645, 200929))

    def test_watched_from_either_source(self):
        disk = support.season(support.folder(self.scan, "The Apothecary Diaries"), "Season 2")["episodes"]
        with database.db() as c:
            c.execute("INSERT INTO episodes(id, watched, position, duration, last_played)"
                      " VALUES('stream:176301:3', 1, 0, 1400, '2026-10-01 20:00:00')")
            c.execute("INSERT INTO episodes(id, watched, position, duration, last_played)"
                      " VALUES(?, 0, 300, 1400, '2026-10-02 20:00:00')", (disk[3]["id"],))
        slots = self.entry(pages.franchise_view(176301), 176301)["slots"]
        self.assertTrue(slots[2]["watched"])
        self.assertEqual((slots[3]["watched"], slots[3]["position"]), (False, 300))

    def test_opening_an_entry_fetches_in_the_background(self):
        pages.franchise_view(176301)
        self.assertEqual(self.bg["stream_resolve_async"], [(176301,)])
        self.assertEqual(self.bg["refresh_entry_art"], [{"ids": {176301}}])

    def test_without_ani_cli_only_files(self):
        self.anicli.stop()
        with mock.patch.object(anicli, "anicli_path", return_value=None):
            page = pages.franchise_view(176301)
        self.assertEqual([s["source"] for s in self.entry(page, 176301)["slots"]], ["local"] * 10)
        self.assertEqual(self.entry(page, 195516)["slots"], [])
        self.assertFalse(self.entry(page, 195516)["online"]["available"])
        self.anicli.start()

    def test_unknown_entry_is_built_in_the_background(self):
        page = pages.franchise_view(999999)
        self.assertEqual((page["building"], page["entries"]), (True, []))
        self.assertEqual(self.bg["franchise_build_async"], [(999999,)])

    def test_stored_entry_shows_while_its_franchise_builds(self):
        with database.db() as c:
            c.execute("INSERT INTO media(id, data, ts) VALUES(21281, ?, 0)", (json.dumps(support.anilist(21281)),))
        page = pages.franchise_view(21281)
        self.assertEqual((page["building"], [e["id"] for e in page["entries"]]), (True, [21281]))

    def test_tiles_know_their_folder_season(self):
        local = self.entry(pages.franchise_view(176301), 176301)["slots"][0]["local"]
        show = support.folder(self.scan, "The Apothecary Diaries")
        self.assertEqual((local["show_id"], local["season"], local["season_label"]),
                         (show["id"], mapping.season_key(support.season(show, "Season 2")), "Season 2"))

    def test_marking_writes_every_source(self):
        r = pages.entry_watched(176301, True, [3, 11])
        self.assertEqual(r["episodes"], 2)
        disk = support.season(support.folder(self.scan, "The Apothecary Diaries"), "Season 2")["episodes"]
        self.assertEqual(sorted(r["ids"]), sorted([disk[2]["id"], "stream:176301:3", "stream:176301:11"]))
        slots = self.entry(pages.franchise_view(176301), 176301)["slots"]
        self.assertEqual([x["number"] for x in slots if x["watched"]], [3, 11])
        pages.entry_watched(176301, False)
        self.assertFalse(any(x["watched"] for x in self.entry(pages.franchise_view(176301), 176301)["slots"]))

    def test_marking_syncs_the_entry(self):
        sent = []
        cache = {"entries": [{"id": 176301, "progress": 0, "episodes": 24}]}
        with mock.patch.object(anilist, "anilist_connected", return_value=True), \
                mock.patch.object(settings, "automation_enabled", return_value=False), \
                mock.patch.object(anilist, "anilist_cache_read", return_value=cache), \
                mock.patch.object(sync, "outbox_pending_for", return_value=0), \
                mock.patch.object(sync, "outbox_enqueue", side_effect=lambda mid, patch=None: sent.append((mid, patch))):
            self.assertEqual(pages.entry_watched(176301, True, [1, 2, 13])["synced"], {"progress": 13})
        self.assertEqual(sent, [(176301, {"progress": 13})])

    def test_library_cards_know_their_franchise(self):
        cards = franchise.tag_franchises([{"id": 200929}, {"id": "folder", "media_id": 176301}, {"id": "x", "media_id": None}])
        self.assertEqual([(c["media_id"], c["franchise_id"]) for c in cards],
                         [(200929, 161645), (176301, 161645), (None, None)])

    def test_autoplay_follows_the_page(self):
        """The last file of Season 1 → Season 2's first file; the last file
        of Season 2 → its first episode not on disk, online; the last of
        Season 2 → Season 3."""
        show = support.folder(self.scan, "The Apothecary Diaries")
        s1, s2 = (support.season(show, f"Season {i}")["episodes"] for i in (1, 2))
        self.assertEqual(player.next_episode(s1[-1]["id"]), s2[0]["id"])
        self.assertEqual(player.next_episode(s2[-1]["id"]), "stream:176301:11")
        self.assertEqual(player.next_episode("stream:176301:11"), "stream:176301:12")
        s3 = self.entry(pages.franchise_view(195516), 195516)["slots"]
        self.assertEqual(player.next_episode("stream:176301:24"), s3[0]["id"] if s3 else None)

    def test_autoplay_never_skips_ahead(self):
        """Without ani-cli, Season 2's missing episodes can't play: the
        chain stops instead of jumping to whatever comes next on disk."""
        show = support.folder(self.scan, "The Apothecary Diaries")
        with mock.patch.object(anicli, "anicli_path", return_value=None):
            self.assertIsNone(player.next_episode(support.season(show, "Season 2")["episodes"][-1]["id"]))

    def test_downloads_join_the_season_folder(self):
        eps, home = streaming.entry_files(self.scan, 176301)
        self.assertEqual(sorted(eps), list(range(1, 11)))
        self.assertEqual(home, config.LIBRARY / "The Apothecary Diaries" / "Season 2")
        self.assertEqual(streaming.entry_files(self.scan, 195516), ({}, None))

    def test_unaired_entry_gets_no_online_episodes(self):
        """"3rd Season Part 2" hasn't aired. An automatic link older
        versions made for it (to another show's episodes) is dropped, and
        no new one is looked for; a link the user chose stays."""
        def link(mid, by):
            with database.db() as c:
                c.execute("""INSERT INTO stream_links(media_id, query, title, idx, mode, episodes, episodes_ts,
                             meta, matched_by) VALUES(?,?,?,1,'sub',?,?, '{}', ?)""",
                          (mid, "x", "x", json.dumps([str(n) for n in range(1, 13)]), 9e9, by))
        link(200927, "auto")
        page = pages.franchise_view(200927)
        self.assertEqual(self.entry(page, 200927)["slots"], [])
        self.assertIsNone(streaming.stream_link_get(200927))
        self.assertNotIn("stream_resolve_async", self.bg)
        link(200929, "user")
        self.assertEqual(len(self.entry(pages.franchise_view(200929), 200929)["slots"]), 12)

    def test_unaired_entry_never_matches(self):
        m = support.anilist(200927)
        self.assertFalse(streaming._count_fits([str(n) for n in range(1, 13)], m))
        self.assertTrue(streaming._count_fits([str(n) for n in range(1, 25)], support.anilist(176301)))

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
        with urllib.request.urlopen(base + "/api/franchise?id=176301") as r:
            self.assertEqual(json.loads(r.read())["selected"], 176301)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/franchise?id=x")
        self.assertEqual(e.exception.code, 400)
        e.exception.close()


class Unavailable(unittest.TestCase):
    """AniList unreachable and nothing stored for the entry (TODO 57): the
    page says so instead of building forever, so the UI can fall back to
    the folder; a later open tries AniList again."""

    def setUp(self):
        support.use_library(support.case("apothecary"))
        pages._build_failed.clear()
        self.addCleanup(pages._build_failed.clear)
        self.down = HTTPError("https://graphql.anilist.co", 403, "Forbidden", None, None)

    def build(self, mid):
        pages.franchise_build_async(mid)
        for _ in range(500):
            if mid not in pages._building:
                return
            time.sleep(0.01)
        self.fail("the build never finished")

    def test_failed_build_is_unavailable(self):
        with mock.patch.object(franchise, "build_franchise", side_effect=self.down):
            self.build(999998)
            page = pages.franchise_view(999998)
        self.assertEqual((page["building"], page["entries"]), (True, []))
        self.assertEqual(page["unavailable"], "AniList is unreachable right now — try again later.")

    def test_building_is_not_unavailable(self):
        with mock.patch.object(pages, "franchise_build_async"):
            self.assertIsNone(pages.franchise_view(999997)["unavailable"])

    def test_tries_again_after_a_while(self):
        with mock.patch.object(franchise, "build_franchise", side_effect=self.down):
            self.build(999996)
        with mock.patch.object(franchise, "build_franchise", return_value=[]) as fetch:
            pages.franchise_build_async(999996)
            fetch.assert_not_called()
            pages._build_failed[999996] = (time.time() - pages.BUILD_RETRY, "older")
            self.build(999996)
            fetch.assert_called_once()
        self.assertNotIn(999996, pages._build_failed)


class Unmapped(unittest.TestCase):
    def test_autoplay_walks_an_unmatched_folder(self):
        scan = support.use_library(support.case("unmatched"))
        library.merge_db(scan)
        eps = scan[0]["seasons"][0]["episodes"]
        self.assertEqual(player.next_episode(eps[0]["id"]), eps[1]["id"])
        self.assertIsNone(player.next_episode(eps[1]["id"]))

    def test_absolute_numbered_season_is_no_download_folder(self):
        """AoT's Season 2 folder counts 26–37: a download saved as
        "Episode 1" there would read as season 1."""
        c = support.case("aot")
        scan = support.match_library(c, support.use_library(c))
        self.assertEqual(sorted(streaming.entry_files(scan, 20958)[0]), list(range(1, 13)))
        self.assertIsNone(streaming.entry_files(scan, 20958)[1])


class OnlineOnly(unittest.TestCase):
    def test_online_only_show_syncs_its_release(self):
        """A release watched online only (no folder) is its own entry for
        progress sync and completion."""
        link = {"media_id": 5, "episodes": ["1", "2", "2.5"], "meta": {"episodes": 12}, "title": "x",
                "query": "x", "mode": "sub", "episodes_ts": 0}
        show = streaming._stream_show(link, {}, None)
        units = mapping.release_units(streaming.trackable(show))
        self.assertEqual([(u["media_id"], [n for _, n in u["eps"]], u["total"]) for u in units], [(5, [1, 2], 12)])


class Pure(unittest.TestCase):
    def setUp(self):
        c = support.case("apothecary")
        self.scan = support.match_library(c, support.use_library(c))
        self.entries = franchise.franchise_of(161645)[1]

    def slots(self, mid, **kw):
        page = pages.franchise_page(self.entries, self.scan, kw.get("links", {}), {}, kw.get("arts", {}),
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
        page = pages.franchise_page(franchise.franchise_of(16498)[1], scan, {}, {}, {}, False)
        self.assertEqual([(o["label"], len(o["episodes"])) for o in page["other_local"]], [("Specials", 3)])


if __name__ == "__main__":
    unittest.main()
