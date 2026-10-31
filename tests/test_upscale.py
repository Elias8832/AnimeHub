"""Banner upscaling (opt-in): the blank-picture guard, finding Upscayl on
each platform, the one-at-a-time worker, and the Settings switch."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401
from animehub import server, settings, upscale
from animehub.config import config


def jpeg(w, h, scan):
    """A stand-in JPEG: just the markers the guard reads (SOF size, scan data)."""
    sof = b"\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + sof + b"\xff\xda\x00\x0c" + b"\x00" * 10 + scan + b"\xff\xd9"


def real(w, h):
    return jpeg(w, h, os.urandom(40000))


def blank(w, h):
    return jpeg(w, h, b"\xa2\x8a\x00" * 20000)


# A fake upscayl-bin: writes the picture named by $FAKE_OUT to the -o path.
FAKE = """import os, sys
out = sys.argv[sys.argv.index("-o") + 1]
open(out, "wb").write(open(os.environ["FAKE_OUT"], "rb").read())
"""


class Guard(unittest.TestCase):
    def test_blank_pictures_are_caught(self):
        """The 2026-10-26 failure: right size, all black, exit code 0."""
        self.assertTrue(upscale.jpeg_looks_blank(blank(7600, 1600)))
        self.assertFalse(upscale.jpeg_looks_blank(real(7600, 1600)))
        self.assertFalse(upscale.upscale_looks_sane(real(1900, 400), blank(7600, 1600)))
        self.assertTrue(upscale.upscale_looks_sane(real(1900, 400), real(7600, 1600)))
        self.assertFalse(upscale.upscale_looks_sane(real(1900, 400), real(27440, 5776)))


class Tool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        self.addCleanup(upscale._found.update, at=0.0, value=None)

    def test_finds_a_normal_install_first(self):
        res = self.tmp / "resources"
        (res / "bin").mkdir(parents=True)
        (res / "models").mkdir()
        exe = "upscayl-bin.exe" if sys.platform == "win32" else "upscayl-bin"
        (res / "bin" / exe).write_text("")
        with patch.object(upscale, "_install_dirs", return_value=[self.tmp / "nope", res]), \
             patch.object(upscale, "_find_flatpak", side_effect=AssertionError("asked the Flatpak")):
            tool = upscale.find_upscayl(fresh=True)
        self.assertEqual(tool["cmd"], [str(res / "bin" / exe)])
        self.assertEqual(tool["models"], str(res / "models"))

    def test_windows_looks_in_the_installers_folders(self):
        with patch.object(upscale.system, "WINDOWS", True), \
             patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/e/AppData/Local", "PROGRAMFILES": "C:/Program Files"}):
            dirs = upscale._install_dirs()
        self.assertEqual(dirs[0], Path("C:/Users/e/AppData/Local/Programs/Upscayl/resources"))
        self.assertIn(Path("C:/Program Files/Upscayl/resources"), dirs)
        with patch.object(upscale.system, "WINDOWS", True):
            self.assertIsNone(upscale._find_flatpak())

    def test_falls_back_to_the_flatpak(self):
        flat = {"cmd": ["flatpak", "run"], "models": "/app/m", "where": "Flatpak"}
        with patch.object(upscale, "_find_native", return_value=None), \
             patch.object(upscale, "_find_flatpak", return_value=flat):
            self.assertEqual(upscale.find_upscayl(fresh=True), flat)
            self.assertIs(upscale.find_upscayl(), flat)  # cached


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        script = self.tmp / "fake.py"
        script.write_text(FAKE)
        self.tool = {"cmd": [sys.executable, str(script)], "models": str(self.tmp), "where": "fake"}
        self.posters = self.tmp / "posters"
        self.posters.mkdir()
        for name, value in (("POSTERS", self.posters), ("DATA", self.tmp), ("UPSCALE_BANNERS", True)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(upscale.status.update, state="idle", done=0, left=0)
        self.addCleanup(upscale._skipped.clear)
        self.found = patch.object(upscale, "find_upscayl", return_value=self.tool)
        self.found.start()
        self.addCleanup(self.found.stop)

    def out(self, data):
        p = self.tmp / "result.jpg"
        p.write_bytes(data)
        patcher = patch.dict(os.environ, {"FAKE_OUT": str(p)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def wait(self):
        for _ in range(200):
            t = upscale._worker["thread"]
            if not (t and t.is_alive()):
                return
            time.sleep(0.05)
        self.fail("worker never finished")

    def test_upscales_only_banners_not_done_yet(self):
        self.out(real(7600, 1600))
        (self.posters / "a.banner.jpg").write_bytes(real(1900, 400))
        (self.posters / "b.banner.jpg").write_bytes(old := real(7600, 1600))
        (self.posters / "a.jpg").write_bytes(real(460, 650))  # a poster: never touched
        upscale.queue_all()
        self.wait()
        self.assertEqual(upscale.jpeg_dimensions((self.posters / "a.banner.jpg").read_bytes()), (7600, 1600))
        self.assertEqual((self.posters / "b.banner.jpg").read_bytes(), old)
        self.assertEqual(upscale.status["done"], 1)
        self.assertEqual(upscale.payload()["state"], "idle")
        self.assertEqual(list(self.posters.glob("*.upscayl.jpg")), [])

    def test_a_blank_result_keeps_the_banner_and_stops(self):
        self.out(blank(7600, 1600))
        originals = {}
        for n in "abc":
            originals[n] = real(1900, 400)
            (self.posters / f"{n}.banner.jpg").write_bytes(originals[n])
        with patch.object(upscale, "upscale_banner", wraps=upscale.upscale_banner) as run:
            upscale.queue_all()
            self.wait()
        self.assertEqual(run.call_count, 1)
        for n in "abc":
            self.assertEqual((self.posters / f"{n}.banner.jpg").read_bytes(), originals[n])
        self.assertEqual(upscale.payload()["state"], "broken")
        upscale.queue_banner(self.posters / "a.banner.jpg")  # stays stopped
        self.assertEqual(upscale.status["left"], 0)

    def test_keeps_the_original_and_redoes_after_a_model_change(self):
        """The original stays beside the upscale: a new model starts again
        from it, and switching off puts it back."""
        self.out(real(7600, 1600))
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(src := real(1900, 400))
        self.assertIs(upscale.upscale_banner(banner, self.tool), True)
        self.assertEqual(upscale.original_of(banner).read_bytes(), src)
        self.assertFalse(upscale.needs_upscale(banner))
        with patch.object(upscale, "style", return_value="digital"):
            self.assertTrue(upscale.needs_upscale(banner))
            self.out(newer := real(7600, 1600))
            self.assertIs(upscale.upscale_banner(banner, self.tool), True)
            self.assertEqual(banner.read_bytes(), newer)
            self.assertEqual(upscale.original_of(banner).read_bytes(), src)
            upscale.restore_originals()
        self.assertEqual(banner.read_bytes(), src)
        self.assertFalse(upscale.original_of(banner).exists())

    def test_a_fresh_download_counts_as_an_original(self):
        """AniList's banner saved again over an upscale: upscaled anew; the
        original kept from before is stale and never restored over it."""
        self.out(real(7600, 1600))
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(1900, 400))
        upscale.upscale_banner(banner, self.tool)
        banner.write_bytes(fresh := real(1900, 401))
        self.assertTrue(upscale.needs_upscale(banner))
        upscale.restore_originals()
        self.assertEqual(banner.read_bytes(), fresh)
        self.assertFalse(upscale.original_of(banner).exists())

    def test_switching_off_gives_the_page_a_new_stamp(self):
        """The page swaps a picture only when its ?v= changed; the original's
        mtime can equal the upscale's to the second."""
        self.addCleanup(upscale.art["changed"].clear)
        self.out(real(7600, 1600))
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(1900, 400))
        upscale.upscale_banner(banner, self.tool)
        first = upscale.art["changed"][banner.name]
        upscale.restore_originals()
        self.assertGreater(upscale.art["changed"][banner.name], first)

    def test_banner_age_follows_the_kept_original(self):
        """Stored links keep a banner younger than 30 days instead of
        fetching AniList's again (which undid the upscale every time)."""
        banner = self.posters / "a.banner.jpg"
        self.assertEqual(upscale.banner_age(banner), float("inf"))
        banner.write_bytes(real(7600, 1600))
        upscale.original_of(banner).write_bytes(real(1900, 400))
        old = time.time() - 40 * 86400
        os.utime(upscale.original_of(banner), (old, old))
        self.assertGreater(upscale.banner_age(banner), 39 * 86400)
        upscale.original_of(banner).unlink()
        self.assertLess(upscale.banner_age(banner), 60)

    def test_an_old_upscale_without_its_original_is_left_alone(self):
        (self.posters / "a.banner.jpg").write_bytes(real(7600, 1600))
        self.assertFalse(upscale.needs_upscale(self.posters / "a.banner.jpg"))

    def test_the_open_page_hears_of_each_swap(self):
        """/api/now's `art`: a counter and the new ?v= per banner name."""
        self.addCleanup(upscale.art.update, stamp=0, changed={})
        upscale.art.update(stamp=0, changed={})
        self.assertIsNone(upscale.art_payload())
        self.out(real(7600, 1600))
        banner = self.posters / "a.banner.jpg"
        banner.write_bytes(real(1900, 400))
        upscale.upscale_banner(banner, self.tool)
        art = upscale.art_payload()
        self.assertEqual(art["stamp"], 1)
        self.assertEqual(art["changed"], {"a.banner.jpg": int(banner.stat().st_mtime)})

    def test_off_queues_nothing(self):
        config.UPSCALE_BANNERS = False
        (self.posters / "a.banner.jpg").write_bytes(real(1900, 400))
        upscale.queue_all()
        self.assertEqual(upscale.status["left"], 0)
        self.assertEqual(upscale.payload()["state"], "off")


class Served(unittest.TestCase):
    def test_an_upscaled_banner_reaches_the_browser(self):
        """Banners revalidate (same name, new picture); posters keep the day cache."""
        posters = Path(tempfile.mkdtemp(dir=support.TMP))
        self.addCleanup(setattr, config, "POSTERS", config.POSTERS)
        config.POSTERS = posters
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def get(name, tag=None):
            headers = {"Host": "127.0.0.1:8787", **({"If-None-Match": tag} if tag else {})}
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/img/{name}", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as res:
                    return res.status, res.headers, res.read()
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, e.headers, b""

        (posters / "a.banner.jpg").write_bytes(small := real(1900, 400))
        code, headers, body = get("a.banner.jpg")
        self.assertEqual((code, body, headers["Cache-Control"]), (200, small, "no-cache"))
        tag = headers["ETag"]
        self.assertEqual(get("a.banner.jpg", tag)[0], 304)
        (posters / "a.banner.jpg").write_bytes(big := real(7600, 1600))
        code, headers, body = get("a.banner.jpg", tag)
        self.assertEqual((code, body), (200, big))
        (posters / "a.jpg").write_bytes(real(460, 650))
        self.assertEqual(get("a.jpg")[1]["Cache-Control"], "max-age=86400")


class SwapInPage(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_swap_art_rewrites_data_and_screen(self):
        """The page swaps a finished banner in place: its data and what is
        on screen point at the new ?v=, other pictures stay as they are."""
        app = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
        fn = re.search(r"^function swapArt\(changed\) \{.*?^\}", app, re.S | re.M).group(0)
        src = """
const el = { style: { backgroundImage: 'url("/img/a.banner.jpg?v=1")' } };
const $$ = (q) => (q.startsWith("[style") ? [el] : []);
const S = { data: { shows: [{ banner: "/img/a.banner.jpg?v=1", poster: "/img/a.jpg?v=1" }, { banner: "/img/b.banner.jpg?v=1" }] },
            fr: { page: { banner: "/img/a.banner.jpg?v=1" } } };
const frMemo = new Map(), canvasNow = { "cv-blur": "/img/a.banner.jpg?v=1" }, swapped = [];
const swapLayer = (cls, url) => swapped.push([cls, url]);
%s
swapArt({ "a.banner.jpg": 9 });
console.log(JSON.stringify([S.data.shows, S.fr.page.banner, el.style.backgroundImage, swapped]));
""" % fn
        out = subprocess.run(["node", "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        shows, page, bg, swapped = json.loads(out.stdout)
        self.assertEqual(shows, [{"banner": "/img/a.banner.jpg?v=9", "poster": "/img/a.jpg?v=1"}, {"banner": "/img/b.banner.jpg?v=1"}])
        self.assertEqual(page, "/img/a.banner.jpg?v=9")
        self.assertEqual(bg, 'url("/img/a.banner.jpg?v=9")')
        self.assertEqual(swapped, [["cv-blur", "/img/a.banner.jpg?v=9"]])


class Switch(unittest.TestCase):
    def test_settings_switch_writes_the_config_file(self):
        cfg = Path(tempfile.mkdtemp(dir=support.TMP)) / "config.json"
        cfg.write_text(json.dumps({"port": 8787}))
        self.addCleanup(setattr, config, "UPSCALE_BANNERS", config.UPSCALE_BANNERS)
        with patch.object(settings, "CONFIG_PATH", cfg), patch.object(upscale, "turned_on") as on:
            settings.set_upscale(True)
            self.assertEqual(json.loads(cfg.read_text()), {"port": 8787, "upscale_banners": True})
            self.assertTrue(config.UPSCALE_BANNERS)
            on.assert_called_once()
            settings.set_upscale(False)
        self.assertFalse(json.loads(cfg.read_text())["upscale_banners"])
        self.assertFalse(config.UPSCALE_BANNERS)


if __name__ == "__main__":
    unittest.main()
