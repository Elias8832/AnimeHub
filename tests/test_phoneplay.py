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

    def test_subtitle_files_beside_it(self):
        # Elias, 2026-11-10: own files had no subtitles on the phone; ani-cli
        # downloads keep them as E01.vtt beside E01.mp4.
        ep = self.eps[0]["id"]
        self.assertEqual(json.loads(self.req("POST", "/api/phone/play", {"id": ep})[2])["subs"], [])
        vtt = self.path.with_suffix(".vtt")
        srt = self.path.parent / (self.path.stem + ".en.srt")
        other = self.path.parent / (self.path.stem + "0.vtt")  # E010 is another episode's
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n", encoding="utf-8")
        srt.write_text("1\r\n00:00:03,000 --> 00:00:04,500\r\nBye\r\n", encoding="utf-8")
        other.write_text("WEBVTT\n", encoding="utf-8")
        for f in (vtt, srt, other):
            self.addCleanup(f.unlink, missing_ok=True)
        subs = json.loads(self.req("POST", "/api/phone/play", {"id": ep})[2])["subs"]
        self.assertEqual(subs, [f"/api/phone/filesub?id={ep}&v=0&n=0", f"/api/phone/filesub?id={ep}&v=0&n=1"])
        st, h, body = self.req("GET", subs[0])
        self.assertEqual((st, h["Content-Type"]), (200, "text/vtt; charset=utf-8"))
        self.assertIn(b"Hello", body)
        st, _, body = self.req("GET", subs[1])
        self.assertEqual(st, 200)
        self.assertTrue(body.startswith(b"WEBVTT"))
        self.assertIn(b"00:00:03.000 --> 00:00:04.500", body)
        for bad in (f"/api/phone/filesub?id={ep}&v=0&n=2", f"/api/phone/filesub?id={ep}&v=0&n=-1",
                    f"/api/phone/filesub?id={ep}&v=0&n=x", "/api/phone/filesub?id=nope&n=0",
                    "/api/phone/filesub?id=stream:1:1&n=0"):
            self.assertEqual(self.req("GET", bad)[0], 404, bad)

    def test_a_paired_phone_may_use_it(self):
        for route in ("/api/phone/video", "/api/phone/filesub", "/api/phone/play", "/api/phone/progress", "/api/phone/skips"):
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


# --- step 2: online episodes through the PC ---------------------------------

import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import tempfile  # noqa: E402
import urllib.request  # noqa: E402
from http.server import BaseHTTPRequestHandler  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

from animehub import anicli, streaming  # noqa: E402

REF = "https://ref.example/"
SEG = bytes(range(256)) * 8
MP4 = bytes(reversed(range(256))) * 16
SITE = {
    "/master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow/index.m3u8\n",
    "/low/index.m3u8": b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:6,\nseg0.ts\n#EXTINF:6,\nhttp://127.0.0.1:@PORT@/low/seg1.ts\n#EXT-X-ENDLIST\n',
    "/low/key.bin": b"k" * 16,
    "/low/seg0.ts": SEG,
    "/low/seg1.ts": SEG[::-1],
    "/subs.srt": b"1\r\n00:00:01,000 --> 00:00:02,500\r\nHello\r\n",
}


class Site(BaseHTTPRequestHandler):
    """A stand-in streaming site: it answers only with ani-cli's referrer."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Referer") != REF:
            self.send_error(403)
            return
        path = urlparse(self.path).path
        if path == "/video.mp4":
            a, b = self.headers.get("Range", "bytes=0-").split("=")[1].split("-")
            a, b = int(a), int(b) if b else len(MP4) - 1
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(MP4)}")
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            self.wfile.write(MP4[a:b + 1])
            return
        body = SITE.get(path)
        if body is None:
            self.send_error(404)
            return
        body = body.replace(b"@PORT@", str(self.server.server_address[1]).encode())
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl" if path.endswith(".m3u8") else "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Relay(unittest.TestCase):
    def setUp(self):
        self.site = system.http_server_class()(("127.0.0.1", 0), Site)
        self.site.daemon_threads = True
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.addCleanup(self.site.server_close)
        self.addCleanup(self.site.shutdown)
        self.base = f"http://127.0.0.1:{self.site.server_address[1]}"
        self.hub = system.http_server_class()(("127.0.0.1", 0), server.Handler)
        self.hub.daemon_threads = True
        threading.Thread(target=self.hub.serve_forever, daemon=True).start()
        self.addCleanup(self.hub.server_close)
        self.addCleanup(self.hub.shutdown)
        p = patch.object(phoneplay, "urlopen", urllib.request.urlopen)  # support.py blocks the network
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(phoneplay._links.clear)

    def add(self, url, subs=()):
        tok = "t" + str(len(phoneplay._links))
        phoneplay._links[tok] = {"ep_id": "stream:1:1", "url": url, "referrer": REF, "subs": list(subs),
                                 "allowed": {url}, "ts": phoneplay.time.time()}
        return tok

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.hub.server_address[1], timeout=5)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{config.PORT}", **(headers or {})})
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def test_what_ani_cli_hands_its_player(self):
        got = phoneplay.player_args(["--referrer=" + REF, "--sub-file=https://s.example/a.vtt", "--input-ipc-server=/x",
                                     "--force-media-title=Show Episode 2", "https://v.example/x.m3u8", ""])
        self.assertEqual(got, {"url": "https://v.example/x.m3u8", "referrer": REF, "subs": ["https://s.example/a.vtt"]})
        self.assertIsNone(phoneplay.player_args(["--referrer=x", "--force-media-title=y"]))

    def test_hls_through_the_pc(self):
        tok = self.add(self.base + "/master.m3u8")
        st, h, body = self.get(phoneplay.relay_url(tok, self.base + "/master.m3u8"))
        self.assertEqual(st, 200)
        self.assertEqual(h["Content-Type"], "application/vnd.apple.mpegurl")
        low = body.decode().splitlines()[2]
        self.assertTrue(low.startswith(f"/api/phone/relay/{tok}?u="), low)
        st, _, body = self.get(low)
        lines = body.decode().splitlines()
        key = re.search(r'URI="([^"]+)"', lines[1]).group(1)
        self.assertEqual(self.get(key)[2], b"k" * 16)
        segs = [x for x in lines if x.startswith("/api/")]
        self.assertEqual([self.get(x)[2] for x in segs], [SEG, SEG[::-1]])  # relative and absolute addresses

    def test_it_is_not_an_open_proxy(self):
        tok = self.add(self.base + "/master.m3u8")
        self.assertEqual(self.get(phoneplay.relay_url(tok, self.base + "/low/seg0.ts"))[0], 404)  # not handed out yet
        self.assertEqual(self.get(phoneplay.relay_url(tok, "http://127.0.0.1:1/x"))[0], 404)
        self.assertEqual(self.get(phoneplay.relay_url("nope", self.base + "/master.m3u8"))[0], 404)
        phoneplay._links[tok]["ts"] -= phoneplay.LINK_TTL + 1
        self.assertEqual(self.get(phoneplay.relay_url(tok, self.base + "/master.m3u8"))[0], 404)

    def test_a_plain_video_keeps_its_ranges(self):
        tok = self.add(self.base + "/video.mp4")
        st, h, body = self.get(phoneplay.relay_url(tok, self.base + "/video.mp4"), {"Range": "bytes=10-19"})
        self.assertEqual((st, h["Content-Range"], body), (206, f"bytes 10-19/{len(MP4)}", MP4[10:20]))

    def test_the_site_saying_no_is_passed_on(self):
        tok = self.add(self.base + "/gone.m3u8")
        self.assertEqual(self.get(phoneplay.relay_url(tok, self.base + "/gone.m3u8"))[0], 404)

    def test_subtitles_become_webvtt(self):
        tok = self.add(self.base + "/master.m3u8", [self.base + "/subs.srt"])
        st, h, body = self.get(f"/api/phone/sub/{tok}/0")
        self.assertEqual(st, 200)
        self.assertTrue(h["Content-Type"].startswith("text/vtt"))
        self.assertEqual(body.decode(), "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\nHello\n")
        self.assertEqual(self.get(f"/api/phone/sub/{tok}/1")[0], 404)


# ani-cli as far as the phone needs it: it hands its player what the real
# one hands mpv (referrer, subtitle file, link).
FAKE_ANICLI = b"""#!/bin/sh
# ANI_CLI_MENU ANI_CLI_PLAYER_FLAGS ANI_CLI_PLAYER ANI_CLI_MODE ANI_CLI_QUALITY
version_number="5.1.0"
episode=""
while [ $# -gt 0 ]; do
    case "$1" in
        -V) echo "$version_number"; exit 0 ;;
        -e) episode=$2; shift ;;
    esac
    shift
done
[ "$ANI_CLI_MODE" = dub ] && [ -n "$HUB_TEST_NODUB" ] && { printf '\\033[1;31mNo sources found for dub\\033[0m\\n' >&2; exit 1; }
"$ANI_CLI_PLAYER" --referrer="$HUB_TEST_REF" --sub-file="$HUB_TEST_SUB" "--force-media-title=Test Show Episode $episode" "$HUB_TEST_URL#$ANI_CLI_MODE"
"""


class OnlineStart(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(dir=support.TMP))
        self.addCleanup(shutil.rmtree, d, True)
        exe = d / "ani-cli"
        exe.write_bytes(FAKE_ANICLI)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.link = {"media_id": 900, "query": "Test Show", "title": "Test Show", "idx": 1, "mode": "sub",
                     "episodes": ["1", "2"], "episodes_ts": phoneplay.time.time()}
        sea = {"label": "Episodes", "season": 1, "episodes": [{"id": "stream:900:2", "number": 2}]}
        show = {"id": "aid:900", "title": "Test Show", "seasons": [sea]}
        for p in (patch.object(anicli, "anicli_path", return_value=str(exe)),
                  patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}),
                  patch.object(anicli, "anicli_outdated", return_value=False),
                  patch.object(streaming, "stream_autolink", return_value=self.link),
                  patch.object(streaming, "stream_set_mode", lambda mid, m: None),
                  patch.object(library, "episode_by_id", return_value=(show, sea["episodes"][0])),
                  patch.dict(os.environ, {"HUB_TEST_REF": REF, "HUB_TEST_SUB": "https://s.example/a.vtt",
                                          "HUB_TEST_URL": "https://v.example/ep2/master.m3u8"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(phoneplay._links.clear)
        self.addCleanup(phoneplay._session.clear)

    def test_ani_cli_finds_it_and_the_phone_gets_the_relay(self):
        r = phoneplay.start_online("stream:900:2")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r["hls"])
        tok = r["src"].split("/")[4].split("?")[0]
        L = phoneplay._links[tok]
        self.assertEqual((L["url"], L["referrer"], L["subs"]), ("https://v.example/ep2/master.m3u8#sub", REF, ["https://s.example/a.vtt"]))
        self.assertEqual(r["subs"], [f"/api/phone/sub/{tok}/0"])
        self.assertEqual(parse_qs(urlparse(r["src"]).query)["u"], [L["url"]])
        self.assertEqual(phoneplay.now()["ep_id"], "stream:900:2")
        self.assertFalse(list((config.DATA / "phone").glob("*")), "its folder stays behind")

    def test_no_dub_yet_plays_the_sub(self):
        self.link["mode"] = "dub"
        with patch.dict(os.environ, {"HUB_TEST_NODUB": "1"}):
            r = phoneplay.start_online("stream:900:2")
        self.assertTrue(r["src"].endswith(phoneplay._q("#sub")), r["src"])
        self.assertIn("isn't dubbed yet", r["note"])

    def test_not_out_yet(self):
        self.assertEqual(phoneplay.start_online("stream:900:3"), {"error": "E3 isn't out yet."})


class PlayerMenu(unittest.TestCase):
    """What the phone player's menu needs from the hub (Elias, 2026-11-07)."""

    def setUp(self):
        self.show, self.eps = episodes()
        self.addCleanup(phoneplay._session.clear)

    def test_a_switch_starts_where_it_was(self):
        r = phoneplay.start(self.eps[0]["id"], at=42.5)
        self.assertEqual(r["start"], 42.5)
        self.assertEqual(r["versions"], ["sub"])
        self.assertIsNone(r["audio"])  # one version: nothing to switch to

    def test_episodes_list_is_mpvs(self):
        with patch.object(player, "episode_menu", return_value=[{"id": "x", "number": 1}]) as menu:
            hub = system.http_server_class()(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=hub.serve_forever, daemon=True).start()
            self.addCleanup(hub.server_close)
            self.addCleanup(hub.shutdown)
            c = http.client.HTTPConnection("127.0.0.1", hub.server_address[1], timeout=5)
            c.request("GET", "/api/phone/episodes?id=abc", headers={"Host": f"127.0.0.1:{config.PORT}"})
            self.assertEqual(json.loads(c.getresponse().read()), {"episodes": [{"id": "x", "number": 1}]})
            c.request("POST", "/api/phone/play", body=json.dumps({"id": "abc", "at": -1}),
                      headers={"Host": f"127.0.0.1:{config.PORT}", "Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 400)
            c.close()
        menu.assert_called_once_with("abc")


class OnlineSwitch(OnlineStart):
    def test_the_dub_is_found_and_sticks(self):
        with patch.object(streaming, "stream_set_mode") as setmode:
            r = phoneplay.start_online("stream:900:2", "dub", at=95.0, switch=True)
        self.assertEqual((r["start"], r["audio"]["current"]), (95.0, "dub"))
        self.assertTrue(r["src"].endswith(phoneplay._q("#dub")))
        setmode.assert_called_once_with(900, "dub")

    def test_no_dub_says_so_and_changes_nothing(self):
        with patch.dict(os.environ, {"HUB_TEST_NODUB": "1"}), patch.object(streaming, "stream_set_mode") as setmode:
            r = phoneplay.start_online("stream:900:2", "dub", at=95.0, switch=True)
        self.assertEqual(r, {"error": "E2 has no English dub yet."})
        setmode.assert_not_called()
