"""TODO 61: the speed update. Pages never wait on what the hub already
knows — the AniList panel reads stale-while-refreshing, a page read waits
seconds (not a minute) for an AniList slot and background work yields to
it; the library payload is gzipped and an unchanged one is a bodiless 304;
the scan is reused while no folder changed and an unchanged library
writes nothing; a hover prefetch (peek) starts no background work; the
metadata pass's progress rides /api/now; old catalog rows are pruned. The
UI side is pinned as source: no fade-out wait before a page draws,
background refreshes update the page in place."""

import gzip
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from tests import support
from tests.test_web import APP, CSS
from animehub import anilist, database, library, metadata, pages, server, streaming
from animehub.config import config


class Isolated(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tmp = Path(self.dir.name)
        for p in (patch.object(config, "DB", self.tmp / "hub.db"),
                  patch.object(config, "ANILIST_CACHE", self.tmp / "anilist_cache.json")):
            p.start()
            self.addCleanup(p.stop)
        database.init_db()


class ServerMixin:
    def serve(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.port = httpd.server_address[1]

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"Host": f"127.0.0.1:{config.PORT}", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()


LIST_ENTRY = {"id": 7, "title": "X", "status": "CURRENT", "progress": 3, "score": 8, "score100": 80,
              "episodes": 12, "format": "TV", "repeat": 1, "started_at": "2026-01-02", "completed_at": None,
              "next_episode": {"number": 4, "in_seconds": 10, "at": time.time() + 3600}}
LIVE = {"media_id": 7, "format": "TV", "episodes": 12, "popularity": 5, "favourites": 1, "is_favourite": True,
        "studios": ["S"], "next_episode": None, "score_format": "POINT_10", "score_max": 10,
        "entry": {"status": "CURRENT", "score": 8, "progress": 3, "repeat": 1,
                  "started_at": "2026-01-02", "completed_at": None}}


class EntryPanelNeverWaits(Isolated):
    def lists(self, **entry):
        config.ANILIST_CACHE.write_text(json.dumps({"user": {"name": "u"}, "score_format": "POINT_10",
                                                    "entries": [dict(LIST_ENTRY, **entry)], "favourites": [7],
                                                    "_ts": time.time()}))

    def test_fresh_read_is_served_as_is(self):
        database.cache_put("entry-state:v1:7", LIVE)
        with patch.object(anilist, "entry_state_refresh_async") as bg:
            self.assertEqual(anilist.anilist_entry_state_quick(7), LIVE)
        bg.assert_not_called()

    def test_old_read_is_served_stale_and_refreshed(self):
        database.cache_put("entry-state:v1:7", LIVE)
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts=? WHERE key=?", (time.time() - 3600, "entry-state:v1:7"))
        with patch.object(anilist, "entry_state_refresh_async") as bg, \
             patch.object(anilist, "anilist_mutation", side_effect=AssertionError("waited on AniList")):
            out = anilist.anilist_entry_state_quick(7)
        self.assertTrue(out["stale"])
        self.assertEqual(out["entry"], LIVE["entry"])
        bg.assert_called_once_with(7)

    def test_first_open_draws_from_the_lists(self):
        self.lists()
        with patch.object(anilist, "entry_state_refresh_async") as bg, \
             patch.object(anilist, "anilist_mutation", side_effect=AssertionError("waited on AniList")):
            out = anilist.anilist_entry_state_quick(7)
        self.assertTrue(out["stale"])
        self.assertEqual(out["entry"], {"status": "CURRENT", "score": 8, "progress": 3, "repeat": 1,
                                        "started_at": "2026-01-02", "completed_at": None})
        self.assertTrue(out["is_favourite"])
        self.assertEqual(out["next_episode"]["number"], 4)
        self.assertGreater(out["next_episode"]["in_seconds"], 3000)  # from airingAt, not the stale countdown
        bg.assert_called_once_with(7)

    def test_score_follows_the_users_format(self):
        self.lists(score100=85)
        cached = json.loads(config.ANILIST_CACHE.read_text(encoding="utf-8"))
        for fmt, want in (("POINT_100", 85), ("POINT_10_DECIMAL", 8.5), ("POINT_5", 4)):
            config.ANILIST_CACHE.write_text(json.dumps(dict(cached, score_format=fmt)))
            self.assertEqual(anilist.entry_state_from_lists(7)["entry"]["score"], want, fmt)

    def test_unknown_entry_reads_live(self):
        self.lists()
        with patch.object(anilist, "anilist_entry_state", return_value=LIVE) as live:
            self.assertEqual(anilist.anilist_entry_state_quick(99), LIVE)
        live.assert_called_once_with(99)

    def test_old_list_cache_without_the_fields_reads_live(self):
        entry = {k: v for k, v in LIST_ENTRY.items() if k not in ("repeat", "started_at", "completed_at")}
        config.ANILIST_CACHE.write_text(json.dumps({"user": {"name": "u"}, "entries": [entry], "_ts": time.time()}))
        self.assertIsNone(anilist.entry_state_from_lists(7))


class EntryRoute(Isolated, ServerMixin):
    def setUp(self):
        super().setUp()
        p = patch.object(anilist, "load_token", lambda: {"access_token": "t"})
        p.start()
        self.addCleanup(p.stop)
        self.serve()

    def test_stale_flag_is_additive(self):
        with patch.object(anilist, "anilist_entry_state_quick", return_value=dict(LIVE, stale=True)):
            code, _, body = self.get("/api/entry?id=7")
        out = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(out["stale"])
        self.assertNotIn("stale", out["state"])
        self.assertEqual(out["state"]["entry"], LIVE["entry"])

    def test_a_queued_edit_reads_live(self):
        with patch.object(server.sync, "outbox_pending_for", return_value=1), \
             patch.object(anilist, "anilist_entry_state", return_value=LIVE) as live, \
             patch.object(anilist, "anilist_entry_state_quick", side_effect=AssertionError("stale read")):
            out = json.loads(self.get("/api/entry?id=7")[2])
        live.assert_called_once_with(7)
        self.assertTrue(out["pending"])
        self.assertNotIn("stale", out)


class Limiter(unittest.TestCase):
    def setUp(self):
        self.times = list(anilist._al_times)
        self.last = anilist._last_foreground
        self.addCleanup(self.restore)

    def restore(self):
        anilist._al_times[:] = self.times
        anilist._last_foreground = self.last
        anilist._al_ctx.background = False
        anilist._al_ctx.deadline = None

    def test_a_page_read_waits_seconds_not_a_minute(self):
        anilist._al_times[:] = [time.time()] * anilist.ANILIST_PER_MIN  # the window is full
        start = time.time()
        with patch.object(anilist, "QUICK_WAIT", 0.3), anilist.quick(), \
                self.assertRaises(anilist.AniListTransientError) as e:
            anilist._anilist_slot()
        self.assertLess(time.time() - start, 1)
        self.assertEqual(e.exception.status, 429)
        self.assertIsNone(anilist._al_ctx.deadline)

    def test_background_work_yields_to_the_user(self):
        anilist._al_times[:] = []
        anilist._last_foreground = time.time()
        anilist._al_ctx.background = True
        slept = []
        with patch.object(anilist.time, "sleep", side_effect=lambda s: (slept.append(s), setattr(anilist, "_last_foreground", 0))):
            anilist._anilist_slot()
        self.assertTrue(slept, "background work went ahead while the user was clicking")

    def test_the_user_marks_the_window(self):
        anilist._al_times[:] = []
        anilist._last_foreground = 0
        anilist._anilist_slot()
        self.assertGreater(anilist._last_foreground, time.time() - 5)


class Payload(Isolated, ServerMixin):
    def setUp(self):
        super().setUp()
        for p in (patch.object(config, "LIBRARY", None), patch.object(anilist, "load_token", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.serve()

    def test_gzip_when_accepted(self):
        big = {"shows": [{"title": "show %d" % i} for i in range(500)]}
        with patch.object(library, "full_library", return_value=big):
            code, headers, raw = self.get("/api/library", {"Accept-Encoding": "gzip"})
            self.assertEqual(code, 200)
            self.assertEqual(headers.get("Content-Encoding"), "gzip")
            self.assertLess(len(raw), len(json.dumps(big)) / 4)
            self.assertEqual(json.loads(gzip.decompress(raw)), big)
            self.assertEqual(json.loads(self.get("/api/library")[2]), big)  # plain for clients that don't ask

    def test_unchanged_library_is_a_304(self):
        code, headers, _ = self.get("/api/library")
        tag = headers["ETag"]
        code, headers, body = self.get("/api/library", {"If-None-Match": tag})
        self.assertEqual((code, body), (304, b""))
        self.assertEqual(headers["ETag"], tag)
        self.assertEqual(self.get("/api/library", {"If-None-Match": '"other"'})[0], 200)

    def test_small_answers_stay_plain(self):
        headers = self.get("/api/now", {"Accept-Encoding": "gzip"})[1]
        self.assertNotIn("Content-Encoding", headers)

    def test_meta_progress_rides_now_while_it_runs(self):
        self.assertNotIn("meta", json.loads(self.get("/api/now")[2]))
        with patch.dict(metadata._meta_status, running=True, pending=4, current="X", phase="posters"):
            meta = json.loads(self.get("/api/now")[2])["meta"]
        self.assertEqual(meta, {"running": True, "pending": 4, "current": "X", "phase": "posters"})


class ScanReuse(unittest.TestCase):
    def setUp(self):
        self.lib = support.TMP / "scan-reuse"
        (self.lib / "Show A" / "Season 1").mkdir(parents=True, exist_ok=True)
        (self.lib / "Show A" / "Season 1" / "Show A - E01.mkv").touch()
        support.apply_config(dict(support.CONFIG, library_root=self.lib, data_dir=support.TMP / "scan-reuse-data"))
        database.init_db()

    def test_unchanged_folders_reuse_the_scan(self):
        first = library.scan_library()
        with patch.object(library, "_scan_library", side_effect=AssertionError("rescanned")):
            again = library.scan_library()
        self.assertEqual(again, first)
        again[0]["title"] = "mutated"
        self.assertEqual(library.scan_library()[0]["title"], "Show A")  # every caller gets its own copy

    def test_a_new_file_rescans(self):
        library.scan_library()
        (self.lib / "Show A" / "Season 1" / "Show A - E02.mkv").touch()
        eps = library.scan_library()[0]["seasons"][0]["episodes"]
        self.assertEqual([e["number"] for e in eps], [1, 2])

    def test_unchanged_library_writes_nothing(self):
        scan = library.scan_library()
        with database.db() as c:
            c.execute("INSERT INTO episodes(id, watched) VALUES(?, 1)", (scan[0]["seasons"][0]["episodes"][0]["id"],))
        library.merge_db(library.scan_library())  # first pass stamps the played episode
        writes = []
        real = database.db

        class Spy:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, *a):
                if not sql.lstrip().upper().startswith(("SELECT", "PRAGMA")):
                    writes.append(sql)
                return self.conn.execute(sql, *a)

            def executemany(self, sql, *a):
                writes.append(sql)
                return self.conn.executemany(sql, *a)

        from contextlib import contextmanager

        @contextmanager
        def spy_db():
            with real() as c:
                yield Spy(c)

        with patch.object(database, "db", spy_db):
            library.merge_db(library.scan_library())
        self.assertEqual(writes, [])


class Peek(Isolated):
    def test_peek_starts_nothing(self):
        with patch.object(config, "LIBRARY", None), \
             patch.object(pages, "franchise_build_async") as build, \
             patch.object(streaming, "stream_resolve_async") as resolve, \
             patch.object(pages.threading, "Thread") as thread:
            page = pages.franchise_view(123, peek=True)
        build.assert_not_called()
        resolve.assert_not_called()
        thread.assert_not_called()
        for key in ("building", "resolving", "stream_ready", "entries"):
            self.assertIn(key, page)


class BatchedFranchiseBuild(Isolated):
    """A new franchise used to take one AniList request per entry (One
    Piece: 48 requests, two minutes under the rate limit); now each step of
    the walk prefetches everything it is about to look at in one go."""
    def test_same_franchise_fewer_requests(self):
        from animehub import franchise
        start = 101921  # a recorded franchise (tests/fixtures/anilist)
        plain = [m["id"] for m in franchise.build_franchise(start, support.anilist)]
        asked, fetched = [], []

        def prefetch(ids):
            asked.append(set(ids))

        def fetch(mid):
            fetched.append(mid)
            return support.anilist(mid)
        batched = [m["id"] for m in franchise.build_franchise(start, fetch, prefetch)]
        self.assertEqual(batched, plain)
        self.assertTrue(asked)
        self.assertTrue(set(fetched) - {start} <= set().union(*asked))  # nothing fetched unannounced

    def test_prefetch_stores_one_batch(self):
        from animehub import franchise
        ids = [101921, 104578]
        page = {"data": {"Page": {"media": [support.anilist(i) for i in ids]}}}
        with patch.object(anilist, "anilist_request", return_value=page) as req:
            franchise.prefetch_media(ids)
            franchise.prefetch_media(ids)  # fresh in the table: no second request
        req.assert_called_once()
        self.assertEqual(req.call_args[0][0]["variables"], {"ids": ids})
        with patch.object(anilist, "anilist_request", side_effect=AssertionError("refetched")):
            self.assertEqual(franchise.media_entry(101921)["id"], 101921)

    def test_a_page_being_built_is_asked_for_often(self):
        self.assertIn("await sleep(400);", APP)


class ParallelEpisodeProbes(unittest.TestCase):
    """stream_match fetches its top candidates' episode lists side by side;
    the best-ranked one that fits still wins, whichever answers first."""
    def test_rank_wins_not_speed(self):
        m = {"title": {"english": "Show", "romaji": "Show"}, "synonyms": [], "format": "TV",
             "status": "FINISHED", "episodes": 12}
        results = {"results": [{"index": 1, "title": "Show"}, {"index": 2, "title": "Show!"}]}

        def episodes(link):
            if link["title"] == "Show":
                time.sleep(0.3)  # the best match answers last
            return [str(n) for n in range(1, 13)]
        with patch.object(streaming.anicli, "anicli_search", return_value=results), \
             patch.object(streaming.anicli, "anicli_episodes", side_effect=episodes) as eps:
            start = time.time()
            found = streaming.stream_match(m)
        self.assertEqual(found[1], "Show")
        self.assertEqual(eps.call_count, 2)
        self.assertLess(time.time() - start, 0.55)


class BackgroundRefreshBatches(Isolated):
    def test_the_refresh_walk_prefetches(self):
        from animehub import franchise
        with patch.object(franchise, "build_franchise", return_value=[]) as build:
            franchise.refresh_franchises({1})
        self.assertIs(build.call_args[0][2], franchise._background_prefetch)
        with patch.object(franchise, "build_franchise", return_value=[]) as build:
            franchise.refresh_franchises({1}, fetch=support.anilist)  # a caller's own fetch: no prefetch
        self.assertIsNone(build.call_args[0][2])


class Prune(Isolated):
    def test_old_catalog_rows_go(self):
        database.cache_put("media:v1:1", {"a": 1})
        database.cache_put("media:v1:2", {"a": 2})
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts=? WHERE key='media:v1:1'", (time.time() - database.CACHE_KEEP - 60,))
        self.assertEqual(database.cache_prune(), 1)
        self.assertIsNone(database.cache_get_ts("media:v1:1"))
        self.assertEqual(database.cache_get_ts("media:v1:2")[0], {"a": 2})


class FrontendPins(unittest.TestCase):
    def test_pages_draw_without_a_wait(self):
        dip = APP[APP.index("function dip(f)"):APP.index("function clearSearch")]
        self.assertNotIn("setTimeout", dip)
        self.assertNotIn('"out"', dip)

    def test_background_refreshes_update_in_place(self):
        self.assertIn("render(true);\n}", APP[APP.index("function softRender"):])  # softRender morphs
        self.assertIn("if (morph) morphHTML(app, html); else", APP)
        self.assertIn('document.createElement("template")', APP[APP.index("function morphHTML"):])
        for kept in ('"ready"', '"focused"', "a._own", 'a.id === "wall"', "liveOnly"):
            self.assertIn(kept, APP[APP.index("const LIVE_CLASSES"):APP.index("function morphHTML")])

    def test_changed_buttons_update_in_place(self):
        """Marking a season turned Play into Rewatch by swapping the button
        in, which replayed its entrance animation (Elias, 2026-10-22)."""
        self.assertIn("if (!m && k && cur && cur.nodeName === nb.nodeName && !newKeys.has(morphKey(cur))) m = cur;", APP)
        self.assertIn("as.forEach((a, k) => (bs[k] ? morphNode(a, bs[k]) : a.remove()));", APP)

    def test_show_pages_open_from_memory(self):
        self.assertIn("&peek=1", APP)
        self.assertIn('document.addEventListener("pointerover"', APP)
        self.assertIn("if (frKnown(mid)) go(); else Promise.race([frPeek(mid), sleep(250)]).then(go);", APP)
        self.assertIn("if (entryMemo[mid]) entryDraw(entryMemo[mid]);", APP)
        self.assertIn("if (res.stale && tries < 4)", APP)

    def test_library_fetch_uses_the_etag(self):
        self.assertIn('"If-None-Match": libTag', APP)
        self.assertNotIn('api("/api/library")', APP)

    def test_poster_shimmer_without_has(self):
        self.assertIn(".poster.hasimg:not(.ready){", CSS)
        self.assertNotIn(".poster:has(img)", CSS)


if __name__ == "__main__":
    unittest.main()
