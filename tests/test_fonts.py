"""Imported fonts (TODO 122, Decision 69): storage, checks and the API."""
import base64
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support  # noqa: F401  (throwaway dirs, before animehub)
from animehub import database, fonts, remote, server
from animehub.config import config

TTF = (support.ROOT / "mpv" / "fonts" / "HankenGrotesk-Regular.ttf").read_bytes()
WOFF2 = (support.ROOT / "web" / "fonts" / "jetbrains-mono-latin.woff2").read_bytes()
b64 = lambda b: base64.b64encode(b).decode()


def clear():
    for f in fonts.all_fonts():
        fonts.delete_font(f["id"])


class Storage(unittest.TestCase):
    def setUp(self):
        clear()

    def test_a_ttf_is_named_by_its_own_family(self):
        f = fonts.import_font("whatever.ttf", b64(TTF))
        self.assertEqual(f, {"id": "Hanken_Grotesk.ttf", "name": "Hanken Grotesk", "url": "/user-fonts/Hanken_Grotesk.ttf", "variable": False})
        self.assertEqual(fonts.font_path(f["id"]).read_bytes(), TTF)

    def test_a_woff2_is_named_by_its_file(self):
        f = fonts.import_font("My Mono (v2).woff2", b64(WOFF2))
        self.assertEqual((f["id"], f["name"]), ("My_Mono_v2.woff2", "My Mono v2"))

    def test_the_same_file_twice_is_kept_once_another_gets_a_number(self):
        fonts.import_font("a.woff2", b64(WOFF2))
        fonts.import_font("a.woff2", b64(WOFF2))
        fonts.import_font("a.woff2", b64(WOFF2 + b"\0"))
        self.assertEqual([f["id"] for f in fonts.all_fonts()], ["a.woff2", "a_2.woff2"])

    def test_not_a_font(self):
        for bad in (None, "", "%%%", b64(b"<html>hello</html>"), b64(b"\x89PNG\r\n\x1a\n")):
            with self.assertRaises(fonts.FontError):
                fonts.import_font("x.ttf", bad)
        self.assertEqual(fonts.all_fonts(), [])

    def test_too_big(self):
        with mock.patch.object(fonts, "MAX_BYTES", 100), self.assertRaises(fonts.FontError):
            fonts.import_font("x.woff2", b64(WOFF2))

    def test_delete_and_no_path_walking(self):
        f = fonts.import_font("a.woff2", b64(WOFF2))
        for bad in ("../hub.db", "..\\hub.db", ".hidden.ttf", "hub.db", "D:x.ttf", None, 3):
            self.assertIsNone(fonts.font_path(bad))
            with self.assertRaises(fonts.FontError):
                fonts.delete_font(bad)
        fonts.delete_font(f["id"])
        self.assertEqual(fonts.all_fonts(), [])


class Installed(unittest.TestCase):
    """The fonts on this PC, faked with the app's own TTFs in a folder."""
    def setUp(self):
        clear()
        self.dir = support.TMP / "sysfonts"
        (self.dir / "sub").mkdir(parents=True, exist_ok=True)
        for name in ("HankenGrotesk-Regular.ttf", "HankenGrotesk-Bold.ttf", "BarlowCondensed-Bold.ttf"):
            (self.dir / "sub" / name).write_bytes((support.ROOT / "mpv" / "fonts" / name).read_bytes())
        (self.dir / "broken.ttf").write_bytes(b"\0\1\0\0 not really")
        (self.dir / "notes.txt").write_text("x")
        self.dirs = mock.patch.object(fonts, "system_font_dirs", lambda: [self.dir, self.dir / "gone"])
        self.dirs.start()
        fonts._cache["families"] = None

    def tearDown(self):
        self.dirs.stop()
        fonts._cache["families"] = None

    def test_listed_by_family(self):
        self.assertEqual(fonts.system_fonts(), [{"family": "Barlow Condensed", "styles": 1}, {"family": "Hanken Grotesk", "styles": 2}])

    def test_adding_copies_regular_and_bold(self):
        f = fonts.add_system_font("Hanken Grotesk")
        self.assertEqual((f["id"], f["bold"]), ("Hanken_Grotesk.ttf", "/user-fonts/Hanken_Grotesk__bold.ttf"))
        self.assertEqual(fonts.font_path("Hanken_Grotesk.ttf").read_bytes(), (self.dir / "sub" / "HankenGrotesk-Regular.ttf").read_bytes())
        self.assertEqual([x["id"] for x in fonts.all_fonts()], ["Hanken_Grotesk.ttf"])  # the bold file isn't a font of its own
        fonts.add_system_font("Hanken Grotesk")  # twice is once
        self.assertEqual(len(list(fonts.font_dir().iterdir())), 2)
        with self.assertRaises(fonts.FontError):
            fonts.delete_font("Hanken_Grotesk__bold.ttf")
        fonts.delete_font("Hanken_Grotesk.ttf")
        self.assertEqual(list(fonts.font_dir().iterdir()), [])

    def test_a_bold_only_family_is_one_file(self):
        f = fonts.add_system_font("Barlow Condensed")
        self.assertNotIn("bold", f)

    def test_not_installed(self):
        for bad in ("Comic Sans", "", None, 3):
            with self.assertRaises(fonts.FontError):
                fonts.add_system_font(bad)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = mock.patch.object(config, "PORT", cls.srv.server_port)  # the Host check
        cls.port.start()

    @classmethod
    def tearDownClass(cls):
        cls.port.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        clear()

    def call(self, path, body=None):
        url = f"http://127.0.0.1:{self.srv.server_port}{path}"
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}) if body is not None else url
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get("Content-Type"), e.read()

    def test_import_list_serve_delete(self):
        code, _, raw = self.call("/api/fonts/import", {"name": "x.ttf", "data": b64(TTF)})
        self.assertEqual(code, 200)
        r = json.loads(raw)
        self.assertEqual(r["font"]["name"], "Hanken Grotesk")
        self.assertEqual(json.loads(self.call("/api/fonts")[2])["fonts"], r["fonts"])
        self.assertEqual(json.loads(self.call("/api/settings")[2])["fonts"], r["fonts"])
        code, ctype, body = self.call(r["font"]["url"])
        self.assertEqual((code, ctype, body), (200, "font/ttf", TTF))
        code, _, raw = self.call("/api/fonts/delete", {"id": r["font"]["id"]})
        self.assertEqual((code, json.loads(raw)["fonts"]), (200, []))
        self.assertEqual(self.call(r["font"]["url"])[0], 404)

    def test_a_font_over_a_megabyte_gets_through(self):
        big = WOFF2 + b"\0" * 2_000_000
        code, _, raw = self.call("/api/fonts/import", {"name": "big.woff2", "data": b64(big)})
        self.assertEqual(code, 200, raw)

    def test_refusals(self):
        self.assertEqual(self.call("/api/fonts/import", {"name": "x", "data": b64(b"nope")})[0], 400)
        self.assertEqual(self.call("/api/fonts/delete", {"id": "../hub.db"})[0], 400)
        self.assertEqual(self.call("/user-fonts/..%2Fhub.db")[0], 404)

    def test_a_phone_can_use_fonts_but_not_change_them(self):
        self.assertTrue(remote.allowed("/user-fonts/a.ttf", "phone"))
        self.assertTrue(remote.allowed("/api/fonts", "phone"))
        self.assertFalse(remote.allowed("/api/fonts/import", "phone"))
        self.assertFalse(remote.allowed("/api/fonts/delete", "phone"))
        self.assertFalse(remote.allowed("/api/fonts/add", "phone"))
        self.assertFalse(remote.allowed("/api/fonts/system", "phone"))

    def test_installed_fonts_route(self):
        with mock.patch.object(fonts, "system_fonts", lambda: [{"family": "X", "styles": 1}]):
            self.assertEqual(json.loads(self.call("/api/fonts/system")[2]), {"fonts": [{"family": "X", "styles": 1}]})
        self.assertEqual(self.call("/api/fonts/add", {"family": "Not installed at all"})[0], 400)


if __name__ == "__main__":
    unittest.main()
