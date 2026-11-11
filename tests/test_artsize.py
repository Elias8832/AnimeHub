"""Artwork sized for the screen that asks (Decision 86): the cookie's size
tiers, mpv's copies (made once, remade when the picture changes, never
when it fails), and what /img/ sends."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401
from tests.test_upscale import png, real
from animehub import artsize, server
from animehub.config import config


class Tier(unittest.TestCase):
    def test_the_smallest_copy_that_covers_the_screen(self):
        for cookie, want in (("", None), ("artw=1071", 2400), ("artw=2340", 2400), ("artw=2400", 2400),
                             ("artw=2401", 3840), ("artw=3840", 3840), ("artw=5120", None),
                             ("artw=junk", None), ("artw=0", None), ("ah_remote=x; artw=1920", 2400)):
            self.assertEqual(artsize.tier_of(cookie), want, cookie)


class Cookie(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_only_a_phone_names_its_screen(self):
        """index.html: a phone sets `artw`; a PC or tablet removes it, so
        their pictures are never touched."""
        page = (Path(__file__).parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        script = re.search(r"<script>(try\{var ph=.*?)</script>", page).group(1)
        run = """const [coarse, w, h, dpr] = JSON.parse(process.argv[1]);
globalThis.matchMedia = () => ({ matches: coarse }); globalThis.screen = { width: w, height: h };
globalThis.devicePixelRatio = dpr; globalThis.document = {};
%s
console.log(document.cookie);""" % script
        for device, want in (([True, 412, 900, 2.6], "artw=2340; max-age=31536000; path=/; SameSite=Strict"),
                             ([False, 2048, 1152, 1.25], "artw=; max-age=0; path=/; SameSite=Strict"),
                             ([True, 1024, 1366, 2], "artw=; max-age=0; path=/; SameSite=Strict")):  # a tablet
            out = subprocess.run(["node", "-e", run, json.dumps(device)], capture_output=True, text=True, timeout=30)
            self.assertEqual(out.stdout.strip(), want, out.stderr)


class FakeMpv:
    """Stands in for mpv: writes a picture of the asked width to --o."""

    def __init__(self, result=real):
        self.runs, self.result = [], result

    def __call__(self, cmd, **kw):
        self.runs.append(cmd)
        out = next(a[4:] for a in cmd if a.startswith("--o="))
        vf = next(a for a in cmd if a.startswith("--vf="))
        width = int(vf.split("scale=")[1].split(":")[0]) if "scale=" in vf and vf.split("scale=")[1][0].isdigit() else 460
        if self.result:
            Path(out).write_bytes(self.result(width, 400))


class Copies(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        self.posters = self.tmp / "posters"
        self.posters.mkdir()
        for name, value in (("POSTERS", self.posters), ("CACHE", self.tmp)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(artsize._failed.clear)
        self.addCleanup(artsize._swept.clear)
        artsize._swept.append(1)  # no background sweep racing the test
        found = patch.object(artsize.system, "find_program", return_value="mpv")
        found.start()
        self.addCleanup(found.stop)

    def run_with(self, fake):
        p = patch.object(artsize.subprocess, "run", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def test_a_wide_banner_is_copied_once_at_the_screens_size(self):
        mpv = self.run_with(FakeMpv())
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(7600, 1600))
        got = artsize.pick(banner, 2400)
        self.assertEqual(got.parent, artsize.folder())
        self.assertTrue(got.name.startswith("a.banner.2400."))
        self.assertEqual(artsize.upscale.image_dimensions(got.read_bytes())[0], 2400)
        self.assertEqual(artsize.pick(banner, 2400), got)
        self.assertEqual(len(mpv.runs), 1)
        self.assertIn("--vf=lavfi=[scale=2400:-2:flags=lanczos:out_range=pc:out_color_matrix=bt601,format=yuvj444p]", mpv.runs[0])
        self.assertEqual(artsize.pick(banner, None), banner)  # a PC: the full picture
        self.assertEqual(len(mpv.runs), 1)

    def test_a_pc_gets_every_picture_untouched(self):
        """No cookie, no copy: not even a PNG turned into a JPEG (Elias saw
        the lower quality on the PC, 2026-11-11)."""
        mpv = self.run_with(FakeMpv())
        cover = self.posters / "c.jpg"
        cover.write_bytes(png(460, 650))
        self.assertEqual(artsize.pick(cover, None), cover)
        self.assertEqual(mpv.runs, [])
        self.assertFalse(artsize.folder().exists())

    def test_png_becomes_jpeg_and_small_jpegs_go_as_they_are(self):
        mpv = self.run_with(FakeMpv())
        cover = self.posters / "c.jpg"
        cover.write_bytes(png(460, 650))
        got = artsize.pick(cover, 2400)
        self.assertTrue(got.name.startswith("c.460."))
        self.assertIn("--vf=lavfi=[scale=out_range=pc:out_color_matrix=bt601,format=yuvj444p]", mpv.runs[0])
        plain = self.posters / "d.jpg"
        plain.write_bytes(real(460, 650))
        self.assertEqual(artsize.pick(plain, 2400), plain)
        orig = self.posters / "e.banner.jpg"
        orig.write_bytes(real(1900, 400))
        self.assertEqual(artsize.pick(orig, 2400), orig)
        self.assertEqual(len(mpv.runs), 1)

    def test_a_failed_copy_sends_the_picture_and_is_not_tried_again(self):
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(7600, 1600))
        for fake in (FakeMpv(result=None), FakeMpv(result=lambda w, h: real(w + 1, h)),
                     FakeMpv(result=lambda w, h: real(w, h)[:50])):
            artsize._failed.clear()
            mpv = self.run_with(fake)
            self.assertEqual(artsize.pick(banner, 2400), banner)
            self.assertEqual(artsize.pick(banner, 2400), banner)
            self.assertEqual(len(mpv.runs), 1)
        self.assertEqual([p for p in artsize.folder().iterdir()], [])  # no half-written files left

    def test_without_mpv_the_picture_goes_as_it_is(self):
        mpv = self.run_with(FakeMpv())
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(7600, 1600))
        with patch.object(artsize.system, "find_program", return_value=None):
            self.assertEqual(artsize.pick(banner, 2400), banner)
        self.assertEqual(mpv.runs, [])

    def test_a_changed_picture_gets_a_new_copy_and_old_ones_go(self):
        self.run_with(FakeMpv())
        banner, gone = self.posters / "a.banner.jpg", self.posters / "b.banner.jpg"
        banner.write_bytes(real(7600, 1600))
        gone.write_bytes(real(7600, 1600))
        first, other = artsize.pick(banner, 2400), artsize.pick(gone, 3840)
        banner.write_bytes(real(7600, 1601))
        second = artsize.pick(banner, 2400)
        self.assertNotEqual(first, second)
        gone.unlink()
        artsize.sweep()
        self.assertEqual(sorted(artsize.folder().iterdir()), [second])
        self.assertFalse(other.exists())

    def test_clear_removes_every_copy(self):
        self.run_with(FakeMpv())
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(7600, 1600))
        artsize.pick(banner, 2400)
        artsize.clear()
        self.assertFalse(artsize.folder().exists())


class Served(unittest.TestCase):
    def test_img_sends_the_copy_for_the_cookies_screen(self):
        tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        posters = tmp / "posters"
        posters.mkdir()
        for name, value in (("POSTERS", posters), ("CACHE", tmp)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(artsize._swept.clear)
        artsize._swept.append(1)
        for p in (patch.object(artsize.system, "find_program", return_value="mpv"),
                  patch.object(artsize.subprocess, "run", FakeMpv())):
            p.start()
            self.addCleanup(p.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def get(name, cookie="", tag=None):
            headers = {"Host": "127.0.0.1:8787", "Cookie": cookie, **({"If-None-Match": tag} if tag else {})}
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/img/{name}", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status, res.headers, res.read()
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, b""

        (posters / "a.banner.jpg").write_bytes(full := real(7600, 1600))
        v = int((posters / "a.banner.jpg").stat().st_mtime)
        code, headers, body = get(f"a.banner.jpg?v={v}", "artw=2340")
        self.assertEqual(code, 200)
        self.assertEqual(artsize.upscale.image_dimensions(body), (2400, 400))
        self.assertEqual(headers["Vary"], "Cookie")
        self.assertEqual(headers["Cache-Control"], "max-age=31536000, immutable")
        self.assertEqual(get(f"a.banner.jpg?v={v}", "artw=2340", headers["ETag"])[0], 304)
        # Another screen's copy is another answer, never the 304.
        code, _, body = get(f"a.banner.jpg?v={v}", "artw=3000", headers["ETag"])
        self.assertEqual((code, artsize.upscale.image_dimensions(body)), (200, (3840, 400)))
        self.assertEqual(get("a.banner.jpg")[2], full)  # no cookie: as before
        self.assertEqual(get("nope.jpg", "artw=2340")[0], 404)


if __name__ == "__main__":
    unittest.main()
