"""Watching on the phone (Decision 71): the file in pieces, progress counted
like mpv's, and the PC and the phone not playing at once by default."""

import http.client
import json
import threading
import unittest
from unittest.mock import patch

from tests import support
from animehub import database, library, phoneplay, player, remote, server, settings, system
from animehub.config import config


def setUpModule():
    support.use_library(support.case("aot"))


def episodes():
    scan, _ = library.streaming.library_shows()
    s = scan[0]
    eps = sorted(s["seasons"][0]["episodes"], key=lambda e: e["number"])
    return s, eps


class FakeProc:
    def __init__(self):
        self.hub_retired = False


class Ranges(unittest.TestCase):
    def test_byte_ranges(self):
        self.assertIsNone(phoneplay.byte_range(None, 100))
        self.assertEqual(phoneplay.byte_range("bytes=0-", 100), (0, 99))
        self.assertEqual(phoneplay.byte_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(phoneplay.byte_range("bytes=90-500", 100), (90, 99))
        self.assertEqual(phoneplay.byte_range("bytes=-10", 100), (90, 99))
        self.assertEqual(phoneplay.byte_range("bytes=100-", 100), "bad")
        self.assertEqual(phoneplay.byte_range("bytes=5-2", 100), "bad")
        self.assertIsNone(phoneplay.byte_range("bytes=0-1,5-6", 100))  # several pieces: the whole file
        self.assertIsNone(phoneplay.byte_range("items=0-1", 100))


class Serving(unittest.TestCase):
    """The main server over a real socket."""

    def setUp(self):
        self.srv = system.http_server_class()(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.show, self.eps = episodes()
        self.path = library.abs_path(self.eps[0]["rel"])
        self.data = bytes(range(256)) * 40
        self.path.write_bytes(self.data)
        self.addCleanup(self.path.write_bytes, b"")
        phoneplay._session.clear()
        self.addCleanup(phoneplay._session.clear)
        with database.db() as c:
            c.execute("DELETE FROM episodes")

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        h = {"Host": f"127.0.0.1:{config.PORT}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def test_the_file_in_pieces(self):
        url = f"/api/phone/video?id={self.eps[0]['id']}&v=0"
        st, h, body = self.req("GET", url, headers={"Range": "bytes=100-199"})
        self.assertEqual((st, h["Content-Range"], body), (206, f"bytes 100-199/{len(self.data)}", self.data[100:200]))
        self.assertEqual(h["Accept-Ranges"], "bytes")
        st, _, body = self.req("GET", url)
        self.assertEqual((st, body), (200, self.data))
        self.assertEqual(self.req("GET", url, headers={"Range": f"bytes={len(self.data)}-"})[0], 416)

    def test_only_library_files(self):
        for bad in ("nope", "stream:1:1", "../../etc/passwd", ""):
            self.assertEqual(self.req("GET", f"/api/phone/video?id={bad}")[0], 404, bad)
        self.assertIn("error", phoneplay.start("stream:16498:1"))

    def test_a_paired_phone_may_use_it(self):
        for route in ("/api/phone/video", "/api/phone/play", "/api/phone/progress", "/api/phone/skips"):
            self.assertTrue(remote.allowed(route, 1), route)
            self.assertFalse(remote.allowed(route, None), route)

    def test_bad_reports_are_refused(self):
        for body in ({}, {"id": "x", "position": "1", "duration": 2}, {"id": "x", "position": -1, "duration": 2},
                     {"id": "x", "position": 1, "duration": 2, "ended": "yes"}):
            self.assertEqual(self.req("POST", "/api/phone/progress", body)[0], 400, body)

    def test_resume_watched_and_next_like_mpv(self):
        ep = self.eps[0]["id"]
        player.save_position(ep, 600.0, 1400.0)
        st, _, body = self.req("POST", "/api/phone/play", {"id": ep})
        r = json.loads(body)
        self.assertEqual((st, r["start"], r["show"]), (200, 600.0, self.show["title"]))
        self.assertTrue(r["src"].startswith("/api/phone/video?id="))
        self.assertEqual(json.loads(self.req("POST", "/api/phone/play", {"id": ep, "from_start": True})[2])["start"], 0)
        self.assertEqual(phoneplay.now()["ep_id"], ep)
        r = json.loads(self.req("POST", "/api/phone/progress", {"id": ep, "position": 700, "duration": 1400})[2])
        self.assertEqual(r, {"ok": True})
        with database.db() as c:
            self.assertEqual(c.execute("SELECT position, watched FROM episodes WHERE id=?", (ep,)).fetchone(), (700.0, 0))
        r = json.loads(self.req("POST", "/api/phone/progress", {"id": ep, "position": 1400, "duration": 1400, "ended": True})[2])
        with database.db() as c:
            self.assertEqual(c.execute("SELECT watched FROM episodes WHERE id=?", (ep,)).fetchone(), (1,))
        self.assertEqual((r["next"]["id"], r["next"]["source"]), (self.eps[1]["id"], "local"))
        self.assertIsNone(phoneplay.now())

    def test_closing_early_keeps_the_resume_point(self):
        ep = self.eps[1]["id"]
        phoneplay.start(ep)
        r = phoneplay.report(ep, 300.0, 1400.0, stop=True)
        self.assertNotIn("next", r)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT position, watched FROM episodes WHERE id=?", (ep,)).fetchone(), (300.0, 0))

    def test_a_late_report_marks_nothing_twice(self):
        ep = self.eps[0]["id"]
        phoneplay.start(ep)
        with patch.object(player, "finish_episode", wraps=player.finish_episode) as fin:
            phoneplay.report(ep, 1400.0, 1400.0, ended=True)
            phoneplay.report(ep, 1400.0, 1400.0, stop=True)
        self.assertEqual(fin.call_count, 1)


class OneAtATime(unittest.TestCase):
    def setUp(self):
        self.show, self.eps = episodes()
        self.calls = []
        patcher = patch.object(player, "control", lambda a, v=None: self.calls.append(a) or {"ok": True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pc = FakeProc()
        player._active.clear()
        player._active["x"] = {"proc": self.pc}
        self.addCleanup(player._active.clear)
        self.addCleanup(phoneplay._session.clear)
        self.addCleanup(settings.set_automation, "phone_play_both", False)

    def test_playing_on_the_phone_stops_the_pc(self):
        phoneplay.start(self.eps[0]["id"])
        self.assertEqual(self.calls, ["stop_after", "stop"])

    def test_unless_both_are_allowed(self):
        settings.set_automation("phone_play_both", True)
        phoneplay.start(self.eps[0]["id"])
        self.assertEqual(self.calls, [])

    def test_the_pc_starting_later_stops_the_phone(self):
        ep = self.eps[0]["id"]
        phoneplay.start(ep)
        self.assertNotIn("stopped", phoneplay.report(ep, 10.0, 1400.0))  # the old one, still winding down
        player._active["x"] = {"proc": FakeProc()}
        self.assertIn("stopped", phoneplay.report(ep, 20.0, 1400.0))
        self.assertIsNone(phoneplay.now())


if __name__ == "__main__":
    unittest.main()
