"""The phone remote (Decision 43): the QR code, pairing (one use, wrong
tries void the code, only a hash stored), the Wi-Fi listener letting only
paired phones in and keeping them off the PC's own settings, and the
remote's buttons reaching mpv."""
import http.client
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, player, qr, remote, server
from animehub.config import config


def setUpModule():
    support.use_library(support.case("aot"))


def forget():
    remote.stop()
    remote.new_code()
    remote._code.update(code=None, until=0.0, tries=0)
    remote.settings.set_setting("phone_remote", "0")
    with database.db() as c:
        c.execute("DELETE FROM remote_devices")


class QrCode(unittest.TestCase):
    def test_sizes_and_finder_patterns(self):
        self.assertEqual(len(qr.matrix("hi")), 21)
        m = qr.matrix("http://192.168.178.23:8789/pair?c=482913")
        self.assertEqual(len(m), 29)  # version 3
        for x, y in ((0, 0), (len(m) - 7, 0), (0, len(m) - 7)):
            self.assertTrue(all(m[y][x + i] for i in range(7)))  # finder edge
            self.assertFalse(m[y + 1][x + 1])
        self.assertIn("<svg", qr.svg("hi"))
        with self.assertRaises(ValueError):
            qr.matrix("x" * 300)

    @unittest.skipUnless(shutil.which("zbarimg"), "zbarimg not installed")
    def test_a_scanner_reads_it(self):
        with tempfile.TemporaryDirectory() as d:
            for text in ("http://10.0.0.5:8788/pair?c=000123", "https://example.com/" + "x" * 150):
                m, s, b = qr.matrix(text), 6, 4
                n = (len(m) + 2 * b) * s
                rows = []
                for y in range(-b, len(m) + b):
                    row = " ".join("1" if 0 <= y < len(m) and 0 <= x < len(m) and m[y][x] else "0"
                                   for x in range(-b, len(m) + b) for _ in range(s))
                    rows += [row] * s
                f = Path(d) / "q.pbm"
                f.write_text(f"P1\n{n} {n}\n" + "\n".join(rows))
                out = subprocess.run(["zbarimg", "-q", "--raw", str(f)], capture_output=True, text=True)
                self.assertEqual(out.stdout.strip(), text)


class Pairing(unittest.TestCase):
    def setUp(self):
        forget()
        self.addCleanup(forget)

    def test_a_code_pairs_one_phone_once(self):
        code = remote.new_code()
        self.assertRegex(code, r"^\d{6}$")
        token = remote.pair(code, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)")
        self.assertTrue(token)
        self.assertIsNone(remote.pair(code, "x"))  # used up
        [dev] = remote.devices()
        self.assertEqual(dev["name"], "iPhone")
        self.assertEqual(remote.device_of(f"a=b; {remote.COOKIE}={token}"), dev["id"])
        self.assertIsNone(remote.device_of(f"{remote.COOKIE}=nope"))
        with database.db() as c:
            stored = c.execute("SELECT token_hash FROM remote_devices").fetchone()[0]
        self.assertNotEqual(stored, token)  # only the hash is kept
        self.assertTrue(remote.remove(dev["id"]))
        self.assertIsNone(remote.device_of(f"{remote.COOKIE}={token}"))

    def test_wrong_tries_void_the_code(self):
        code = remote.new_code()
        wrong = "%06d" % ((int(code) + 1) % 10 ** 6)
        for _ in range(remote.CODE_TRIES):
            self.assertIsNone(remote.pair(wrong, ""))
        self.assertIsNone(remote.pair(code, ""))
        self.assertIsNone(remote.code_info())

    def test_codes_run_out(self):
        code = remote.new_code()
        remote._code["until"] = 0
        self.assertIsNone(remote.pair(code, ""))

    def test_what_a_phone_may_use(self):
        self.assertTrue(remote.allowed("/pair", None))
        self.assertTrue(remote.allowed("/fonts/x.woff2", None))
        self.assertFalse(remote.allowed("/api/now", None))
        self.assertFalse(remote.allowed("/", None))
        self.assertTrue(remote.allowed("/api/now", 1))
        self.assertTrue(remote.allowed("/api/control", 1))
        for r in ("/api/browse", "/api/library-root", "/api/auth/logout", "/api/remote", "/api/open-folder"):
            self.assertFalse(remote.allowed(r, 1), r)

    def test_home_address_beats_vpn_and_tailscale(self):
        for found, want in ((["10.8.0.5", "100.101.102.103", "192.168.1.50"], "192.168.1.50"),
                            (["127.0.0.1", "169.254.3.3", "10.0.0.8"], "10.0.0.8"),
                            (["100.70.0.1", "8.8.8.8"], None)):
            with patch.object(remote, "_candidates", return_value=found):
                self.assertEqual(remote.lan_ip(), want, found)

    def test_payload(self):
        self.assertFalse(remote.payload()["on"])
        with patch.object(remote, "start", return_value=True), patch.object(remote, "lan_ip", return_value="192.168.1.20"):
            remote.set_enabled(True, server.RemoteHandler)
            p = remote.payload()
        self.assertTrue(p["on"])
        self.assertEqual(p["address"], f"http://192.168.1.20:{config.REMOTE_PORT}")
        self.assertTrue(p["code"]["link"].startswith(p["address"] + "/pair?c="))
        self.assertIn("<svg", p["code"]["qr"])


class Listener(unittest.TestCase):
    """The Wi-Fi listener over a real socket (port picked by the OS)."""

    def setUp(self):
        forget()
        self.addCleanup(forget)
        with patch.object(config, "REMOTE_PORT", 0):
            self.assertTrue(remote.start(server.RemoteHandler))
        self.port = remote._listener["server"].server_address[1]

    def req(self, method, path, body=None, cookie=None, host="192.168.1.20:8788"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host}
        if cookie:
            h["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = (r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def test_unpaired_phones_only_see_the_pairing_page(self):
        st, h, _ = self.req("GET", "/")
        self.assertEqual((st, h.get("Location")), (302, "/pair"))
        self.assertEqual(self.req("GET", "/api/library")[0], 401)
        self.assertEqual(self.req("POST", "/api/play", {"id": "x"})[0], 401)
        st, _, body = self.req("GET", "/pair")
        self.assertEqual(st, 200)
        self.assertIn(b"Pair this phone", body)
        self.assertEqual(self.req("GET", "/pair?c=123")[0], 200)  # a bad link: the page again

    def test_qr_link_pairs_and_the_cookie_opens_the_app(self):
        code = remote.new_code()
        st, h, _ = self.req("GET", f"/pair?c={code}")
        self.assertEqual((st, h["Location"]), (302, "/"))
        cookie = h["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["Set-Cookie"])
        self.assertIn("SameSite=Strict", h["Set-Cookie"])
        self.assertEqual(self.req("GET", "/", cookie=cookie)[0], 200)
        st, _, body = self.req("GET", "/api/now", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertIn("playing", json.loads(body))
        settings = json.loads(self.req("GET", "/api/settings", cookie=cookie)[2])
        self.assertTrue(settings["phone"])
        # Things that change the PC stay on the PC.
        self.assertEqual(self.req("GET", "/api/browse", cookie=cookie)[0], 403)
        self.assertEqual(self.req("GET", "/api/remote", cookie=cookie)[0], 403)
        self.assertEqual(self.req("POST", "/api/remote", {"on": False}, cookie=cookie)[0], 403)
        self.assertEqual(self.req("POST", "/api/auth/logout", {}, cookie=cookie)[0], 403)

    def test_typed_code_pairs(self):
        code = remote.new_code()
        st, _, body = self.req("POST", "/api/remote/pair", {"code": "000000" if code != "000000" else "111111"})
        self.assertEqual(st, 400)
        st, h, body = self.req("POST", "/api/remote/pair", {"code": code})
        self.assertEqual((st, json.loads(body)), (200, {"ok": True}))
        self.assertTrue(h["Set-Cookie"].startswith(remote.COOKIE + "="))

    def test_main_server_has_no_pairing_back_door(self):
        """The 127.0.0.1 server never sets phone cookies or reports a phone."""
        self.assertIsNone(server.Handler.remote_device)


class Controls(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.marker = Path(tempfile.mkdtemp()) / "sock"

        def ipc(sock, cmd, timeout=2.0):
            self.sent.append(cmd)
            data = {"time-pos": 61.0, "duration": 1440.0, "pause": True, "volume": 80.0,
                    "track-list": [{"id": 1, "type": "audio", "lang": "jpn", "selected": True}]}
            return {"data": data.get(cmd[1])} if cmd[0] == "get_property" else {"error": "success"}

        patcher = patch.object(player, "mpv_ipc", ipc)
        patcher.start()
        self.addCleanup(patcher.stop)
        with player._active_lock:
            player._active.clear()
        self.addCleanup(player._active.clear)

    def play(self):
        player._active["ep"] = {"proc": None, "ep_id": "ep", "sock": str(self.marker)}

    def test_nothing_playing(self):
        self.assertEqual(player.control("toggle"), {"error": "nothing is playing"})

    def test_values_are_checked(self):
        self.play()
        self.assertIn("error", player.control("dance"))
        self.assertIn("error", player.control("seek", "10"))
        self.assertIn("error", player.control("seek", True))
        self.assertIn("error", player.control("sub", "1"))
        self.assertIn("error", player.control("audio", "no"))
        self.assertEqual(self.sent, [])

    def test_buttons_reach_mpv(self):
        self.play()
        r = player.control("toggle")
        self.assertEqual(self.sent[0], ["cycle", "pause"])
        self.assertEqual((r["position"], r["paused"], r["volume"]), (61.0, True, 80.0))
        self.assertEqual(r["tracks"]["audio"][0]["lang"], "jpn")
        player.control("seek", -10)
        player.control("seek_to", -5)
        player.control("volume", 400)
        player.control("sub", "no")
        player.control("audio", 2)
        cmds = [c for c in self.sent if c[0] != "get_property"]
        self.assertEqual(cmds[1:], [["seek", -10, "relative"], ["seek", 0, "absolute"],
                                    ["set_property", "volume", 130], ["set_property", "sid", "no"],
                                    ["set_property", "aid", 2]])
        self.assertEqual(player._active["ep"]["paused"], True)  # /api/now shows it

    def test_speed_timing_and_stop_after(self):
        self.play()
        player.control("speed", 9)
        player.control("sub_delay", -0.44)
        cmds = [c for c in self.sent if c[0] != "get_property"]
        self.assertEqual(cmds, [["set_property", "speed", 4.0], ["set_property", "sub-delay", -0.44]])
        self.assertEqual(player._speed["value"], 4.0)  # later episodes start with it
        player._speed["value"] = 1.0
        self.assertIn("error", player.control("stop_after", "yes"))
        self.assertEqual(player.control("stop_after", True), {"ok": True, "stop_after": True})
        self.assertTrue(player._active["ep"]["stop_after"])

    def test_skip_jumps_past_the_opening(self):
        self.play()
        self.assertIn("error", player.control("skip"))  # no ranges known
        player._active["ep"]["segments"] = [{"kind": "op", "start": 50.0, "end": 140.0}]
        player.control("skip")  # time-pos 61 is inside
        self.assertIn(["seek", 140.0, "absolute"], self.sent)
        player._active["ep"]["segments"] = [{"kind": "ed", "start": 30.0, "end": 1440.0, "finish": True}]
        self.sent.clear()
        self.assertEqual(player.control("skip"), {"ok": True})  # nothing after it: finishes like Enter
        self.assertIn(["quit"], self.sent)
        self.assertTrue(player.system.ipc_marker(str(self.marker)).exists())

    def test_next_finishes_the_episode(self):
        self.play()
        self.assertEqual(player.control("next"), {"ok": True})
        self.assertEqual(self.sent, [["quit"]])
        self.assertTrue(player.system.ipc_marker(str(self.marker)).exists())


if __name__ == "__main__":
    unittest.main()
