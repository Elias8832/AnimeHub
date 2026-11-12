"""Decision 90: a phone gets AniList's PNG covers from the hub as JPEG copies
(a quarter the size). Only AniList's picture paths, only for a phone; a
see-through picture stays a PNG; whenever there is no copy the answer leads
on to AniList's own link, so the picture always shows."""
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from tests.test_upscale import png, real
from tests.test_web import APP
from animehub import alpics, server
from animehub.config import config

REL = "media/anime/cover/large/bx13125-2EDZb8ahshQc.png"


class FakeMpv:
    """Stands in for mpv's two jobs: the alpha check (32x32 grey bytes) and
    the JPEG copy."""

    def __init__(self, alpha=255, jpeg=real):
        self.checks = self.copies = 0
        self.alpha, self.jpeg = alpha, jpeg

    def __call__(self, cmd, **kw):
        out = next(a[4:] for a in cmd if a.startswith("--o="))
        if "--of=rawvideo" in cmd:
            self.checks += 1
            if self.alpha is not None:
                Path(out).write_bytes(bytes([255] * 1023 + [self.alpha]))
        else:
            self.copies += 1
            if self.jpeg:
                Path(out).write_bytes(self.jpeg(460, 650))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        self.addCleanup(setattr, config, "CACHE", config.CACHE)
        config.CACHE = self.tmp
        self.addCleanup(alpics._swept.clear)
        alpics._swept.append(1)  # no sweep thread in the way
        self.addCleanup(alpics._failed.clear)
        self.fetched = []

        def fetch(url):
            self.fetched.append(url)
            return self.body
        self.body = png(460, 650)
        self.mpv = FakeMpv()
        for p in (patch.object(alpics.system, "find_program", return_value="mpv"),
                  patch.object(alpics.subprocess, "run", self.use_mpv),
                  patch.object(alpics.metadata, "_fetch_image", fetch)):
            p.start()
            self.addCleanup(p.stop)

    def use_mpv(self, cmd, **kw):
        return self.mpv(cmd, **kw)


class Paths(unittest.TestCase):
    def test_only_anilists_png_pictures(self):
        ok = ("media/anime/cover/large/bx1-a_B.png", "media/anime/cover/medium/b2.png",
              "media/anime/cover/extraLarge/bx3.png", "media/anime/banner/4-x.png")
        for rel in ok:
            self.assertEqual(alpics.source(rel), "https://s4.anilist.co/file/anilistcdn/" + rel)
        for rel in ("", None, "media/anime/cover/large/bx1.jpg", "media/anime/cover/large/../../x.png",
                    "media/anime/cover/large/a/b.png", "media/user/avatar/x.png", "https://evil.example/x.png",
                    "media/anime/cover/large/x.png?u=https://evil.example", "media/anime/cover/large/x%2Fy.png",
                    "/media/anime/cover/large/x.png", "media/anime/cover/large/x.png\n"):
            self.assertIsNone(alpics.source(rel), rel)

    def test_the_page_names_the_same_pictures(self):
        """app.js maps exactly what the hub accepts, and only on a phone."""
        self.assertIn(r"const AL_PNG = /^https:\/\/s4\.anilist\.co\/file\/anilistcdn\/(media\/anime\/(?:cover\/(?:medium|large|extraLarge)|banner)\/[\w-]+\.png)$/;", APP)
        self.assertIn('artPhone ??= typeof document !== "undefined" && /(?:^|;\\s*)artw=\\d/.test(document.cookie);', APP)
        self.assertIn('const m = artPhone && S.data?.alpic && typeof u === "string" && AL_PNG.exec(u);', APP)
        self.assertIn('return m ? "/alpic/" + m[1] : u;', APP)
        # every place a picture goes on screen: CSS backgrounds, posters, the two bare <img>, the preloads
        self.assertIn('const cssUrl = (u) => `url("${String(pic(u)).replace(', APP)
        self.assertIn("  url = pic(url);\n  const seen = !!url && imgSeen.has(url);", APP)
        self.assertIn('<img src="${esc(pic(item.poster))}" alt="">', APP)
        self.assertIn('<img alt="" src="${esc(pic(s.poster))}">', APP)
        self.assertIn("im.src = pic(u);", APP)
        # the accent's colour is read from the copy; one passed on to AniList is read there, as before
        self.assertIn("read(url, false, url !== own ? () => direct(own) : null);", APP)


class Copies(Base):
    def test_made_once_then_kept(self):
        out = alpics.copy(REL)
        self.assertEqual(out.parent, alpics.folder())
        self.assertEqual(out.read_bytes()[:2], b"\xff\xd8")
        self.assertEqual(alpics.copy(REL), out)
        self.assertEqual((len(self.fetched), self.mpv.checks, self.mpv.copies), (1, 1, 1))
        self.assertEqual(self.fetched, ["https://s4.anilist.co/file/anilistcdn/" + REL])
        self.assertEqual([p.name for p in alpics.folder().iterdir()], [out.name])  # the PNG and mpv's pieces are gone

    def test_a_see_through_picture_stays_a_png(self):
        self.mpv = FakeMpv(alpha=120)
        self.assertIsNone(alpics.copy(REL))
        self.assertTrue(alpics.kept(REL))
        self.assertIsNone(alpics.copy(REL))
        self.assertEqual((len(self.fetched), self.mpv.checks, self.mpv.copies), (1, 1, 0))  # remembered: not fetched again

    def test_a_few_faint_pixels_dont_count(self):
        self.mpv = FakeMpv(alpha=alpics.OPAQUE)
        self.assertIsNotNone(alpics.copy(REL))

    def test_anilist_unreachable_is_tried_again(self):
        self.body = None
        self.assertIsNone(alpics.copy(REL))
        self.body = png(460, 650)
        self.assertIsNotNone(alpics.copy(REL))

    def test_not_a_png_after_all(self):
        self.body = real(460, 650)
        self.assertIsNone(alpics.copy(REL))
        self.assertEqual(self.mpv.checks, 0)

    def test_mpv_failing_isnt_retried_until_a_restart(self):
        self.mpv = FakeMpv(jpeg=None)
        self.assertIsNone(alpics.copy(REL))
        self.assertIsNone(alpics.copy(REL))
        self.assertEqual(len(self.fetched), 1)
        self.assertFalse(alpics.kept(REL))

    def test_a_check_mpv_cant_do_makes_no_copy(self):
        self.mpv = FakeMpv(alpha=None)
        self.assertIsNone(alpics.copy(REL))
        self.assertEqual(self.mpv.copies, 0)

    def test_no_mpv_no_copy(self):
        with patch.object(alpics.system, "find_program", return_value=None):
            self.assertFalse(alpics.can())
            self.assertIsNone(alpics.copy(REL))
        self.assertEqual(self.fetched, [])

    def test_a_path_that_isnt_anilists_fetches_nothing(self):
        self.assertIsNone(alpics.copy("media/anime/cover/large/../x.png"))
        self.assertEqual(self.fetched, [])

    def test_old_copies_and_leftovers_go(self):
        out = alpics.copy(REL)
        old = alpics.folder() / ("media_anime_cover_large_old." + alpics.artsize.MADE + ".jpg")
        stray = alpics.folder() / ".abc.png"
        fresh_stray = alpics.folder() / ".new.png"
        for p in (old, stray, fresh_stray):
            p.write_bytes(b"x")
        import os
        long_ago = time.time() - (alpics.KEEP_DAYS + 1) * 86400
        os.utime(old, (long_ago, long_ago))
        os.utime(stray, (time.time() - 3600,) * 2)
        alpics.sweep()
        self.assertEqual(sorted(p.name for p in alpics.folder().iterdir()), sorted([out.name, fresh_stray.name]))


class Route(Base):
    def setUp(self):
        super().setUp()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.port = httpd.server_address[1]

    def get(self, rel, cookie=""):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/alpic/{rel}",
                                     headers={"Host": f"127.0.0.1:{config.PORT}", "Cookie": cookie})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, b""

    def test_a_phone_gets_the_copy_for_good(self):
        code, headers, body = self.get(REL, "artw=2340")
        self.assertEqual((code, headers["Content-Type"], body[:2]), (200, "image/jpeg", b"\xff\xd8"))
        self.assertEqual(headers["Cache-Control"], "max-age=31536000, immutable")

    def test_a_pc_is_sent_on_to_anilist(self):
        code, headers, _ = self.get(REL)
        self.assertEqual((code, headers["Location"]), (302, "https://s4.anilist.co/file/anilistcdn/" + REL))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.fetched, [])  # and nothing is made for it

    def test_no_copy_leads_on_to_anilist(self):
        self.body = None
        code, headers, _ = self.get(REL, "artw=2340")
        self.assertEqual((code, headers["Location"], headers["Cache-Control"]),
                         (302, "https://s4.anilist.co/file/anilistcdn/" + REL, "no-store"))

    def test_a_see_through_one_is_remembered_by_the_phone_for_a_day(self):
        self.mpv = FakeMpv(alpha=0)
        code, headers, _ = self.get(REL, "artw=2340")
        self.assertEqual((code, headers["Cache-Control"]), (302, "max-age=86400"))

    def test_anything_else_is_a_404(self):
        for rel in ("media/anime/cover/large/bx1.jpg", "x.png", "media/anime/cover/large/..%2Fx.png", ""):
            self.assertEqual(self.get(rel, "artw=2340")[0], 404, rel)
        self.assertEqual(self.fetched, [])


class Payload(unittest.TestCase):
    def test_the_library_says_whether_copies_can_be_made(self):
        src = (support.ROOT / "animehub" / "library.py").read_text(encoding="utf-8")
        self.assertIn('"alpic": alpics.can(),', src)


if __name__ == "__main__":
    unittest.main()
