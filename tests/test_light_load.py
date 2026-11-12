"""The second speed pass (2026-11-12): nothing looks different, less is sent
and less is drawn. The page's own files travel gzipped and stay in the
browser until they change; answers say HTTP/1.1, so a browser asks "has this
changed?" at all (Chrome never did of a 1.0 server); day-cached files answer
that with a bodiless 304; an idle page comes to rest (no shimmer or drift
that nobody can see). The page side is pinned as source."""

import gzip
import shutil
import unittest
import unittest.mock

from tests import support
from tests.test_speed import Isolated, ServerMixin
from tests.test_web import APP, CSS, HTML, run_js
from animehub import server, sync

WEB = support.ROOT / "web"
GZ = {"Accept-Encoding": "gzip, deflate, br"}


class PageFiles(Isolated, ServerMixin):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_the_big_files_travel_gzipped(self):
        for name in ("app.js", "style.css"):
            raw = (WEB / name).read_bytes()
            code, headers, body = self.get("/" + name, GZ)
            self.assertEqual((code, headers["Content-Encoding"]), (200, "gzip"))
            self.assertEqual(gzip.decompress(body), raw)
            self.assertLess(len(body), len(raw) / 2)
            self.assertIn("Accept-Encoding", headers["Vary"])

    def test_a_browser_without_gzip_gets_the_file_as_it_is(self):
        code, headers, body = self.get("/app.js")
        self.assertEqual((code, body), (200, (WEB / "app.js").read_bytes()))
        self.assertNotIn("Content-Encoding", headers)

    def test_the_stamped_link_is_kept_for_good(self):
        """index.html's ?v= is the content's code (Decision 72): that link
        can never mean another file, so the browser keeps it."""
        code = server.asset_code((WEB / "app.js").read_bytes())
        self.assertIn(f"/app.js?v={code}", server.index_html().decode())
        self.assertEqual(self.get(f"/app.js?v={code}", GZ)[1]["Cache-Control"], "max-age=31536000, immutable")

    def test_any_other_link_asks_each_time(self):
        for path in ("/app.js", "/app.js?v=0000000000", "/style.css?v=auto", "/", "/sw.js"):
            self.assertEqual(self.get(path, GZ)[1]["Cache-Control"], "no-cache", path)

    def test_an_unchanged_file_is_a_304(self):
        for path in ("/app.js", "/style.css", "/", "/sw.js"):
            for accept in (GZ, {}):
                tag = self.get(path, accept)[1]["ETag"]
                code, headers, body = self.get(path, {**accept, "If-None-Match": tag})
                self.assertEqual((code, body, headers["ETag"]), (304, b"", tag), path)

    def test_a_changed_file_is_sent_again(self):
        tag = self.get("/app.js", GZ)[1]["ETag"]
        real = server.system.read_bytes
        with unittest.mock.patch.object(server.system, "read_bytes", side_effect=lambda p: real(p) + b"\n/* changed */"):
            code, headers, body = self.get("/app.js", {**GZ, "If-None-Match": tag})
        self.assertEqual(code, 200)
        self.assertNotEqual(headers["ETag"], tag)
        self.assertTrue(gzip.decompress(body).endswith(b"/* changed */"))

    def test_the_page_still_carries_its_stamps_when_gzipped(self):
        code, headers, body = self.get("/", GZ)
        self.assertEqual(gzip.decompress(body), server.index_html())

    def test_packed_copies_dont_pile_up(self):
        for n in range(30):
            server.packed("code%d" % n, b"x" * 2000)
        self.assertLessEqual(len(server._packed), 8)


class Http11(Isolated, ServerMixin):
    def setUp(self):
        super().setUp()
        self.serve()

    def raw(self, path):
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{server.config.PORT}\r\n\r\n".encode())
            data = b""
            while True:  # the server must end the answer itself: it closes
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return data

    def test_answers_say_1_1_and_close(self):
        """Chrome takes an ETag only from an HTTP/1.1 answer. Each answer
        still closes its connection, so nothing waits on a kept-open one."""
        for path in ("/api/now", "/app.js", "/nope"):
            head = self.raw(path).split(b"\r\n\r\n", 1)[0].decode()
            self.assertTrue(head.startswith("HTTP/1.1 "), head[:40])
            self.assertIn("Connection: close", head)

    def test_the_phone_listener_too(self):
        self.assertEqual(server.RemoteHandler.protocol_version, "HTTP/1.1")


class DayCachedFiles(Isolated, ServerMixin):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_a_font_is_a_304_after_its_day(self):
        code, headers, body = self.get("/fonts/hanken-grotesk-latin.woff2")
        self.assertEqual((code, headers["Cache-Control"]), (200, "max-age=86400"))
        self.assertEqual(body, (WEB / "fonts" / "hanken-grotesk-latin.woff2").read_bytes())
        again = self.get("/fonts/hanken-grotesk-latin.woff2", {"If-None-Match": headers["ETag"]})
        self.assertEqual((again[0], again[2], again[1]["Cache-Control"]), (304, b"", "max-age=86400"))

    def test_a_missing_one_is_still_a_404(self):
        self.assertEqual(self.get("/fonts/nope.woff2")[0], 404)
        self.assertEqual(self.get("/epimg/nope.jpg")[0], 404)


class EmptyQueue(Isolated):
    def test_nothing_queued_looks_nothing_up(self):
        with unittest.mock.patch.object(sync.anilist, "anilist_cache_stale") as cache:
            self.assertEqual(sync.outbox_items(), [])
        cache.assert_not_called()


class PagePins(unittest.TestCase):
    def test_the_library_is_asked_for_before_the_script(self):
        self.assertLess(HTML.index('window.libEarly=fetch("/api/library")'), HTML.index('src="/app.js?v=auto"'))
        self.assertIn("const early = window.libEarly;", APP)
        self.assertIn("window.libEarly = null;", APP)  # used once: later fetches ask the hub again

    def test_a_poster_off_the_screen_holds_its_shimmer(self):
        self.assertIn(".poster.shoff{animation-play-state:paused!important}", CSS)
        self.assertIn('e.animationName === "shimmer" && e.target.classList?.contains("poster")) shimIO.observe(e.target)', APP)
        self.assertIn('el.classList.toggle("shoff", !en.isIntersecting);', APP)
        self.assertIn('"shoff"', APP.split("const LIVE_CLASSES", 1)[1].split("\n", 1)[0])  # an in-place update keeps it
        # a loaded (or failed) picture drops the mark at once
        self.assertIn('t.parentElement.classList.remove("shoff");', APP)
        self.assertIn('t.parentElement.classList.remove("hasimg", "shoff");', APP)

    def test_art_nobody_sees_holds_still(self):
        self.assertIn("#canvas .rest{animation-play-state:paused}", CSS)
        self.assertIn('el.classList.toggle("rest", getComputedStyle(el).opacity === "0");', APP)
        # it moves again as its fade-in starts, and a layer that gets a picture is never left resting
        self.assertIn('cv.addEventListener("transitionrun", (e) => { if (e.propertyName === "opacity") e.target.classList.remove("rest"); });', APP)
        self.assertIn('off.classList.remove("out", "rest");', APP)

    def test_strips_are_measured_together(self):
        body = APP.split("function strips() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("for (const mark of marks) mark();", body)
        self.assertEqual(body.count("marks.push(read());"), 2)

    def test_no_card_is_measured_while_no_key_picked_one(self):
        body = APP.split("function applyFocus() {", 1)[1]
        self.assertLess(body.index("if (fIdx < 0) return;"), body.index("focusables()"))


class TabSwitch(unittest.TestCase):
    """What a tab switch on a phone no longer does twice, or at all (TODO 224)."""

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_an_episode_is_found_without_walking_the_library_each_time(self):
        out = run_js("""
          const show = (id, eps) => ({ id, seasons: [{ episodes: eps.map((e) => ({ id: e })) }] });
          const a = { shows: [show("s1", ["e1", "e2"]), show("s2", ["e3", "e2"])], streams: [show("aid:9", ["stream:1"])] };
          S.data = a;
          const one = findEpContext("e2"), again = findEpContext("e2");
          const r = { first: one.s.id, same: one.ep === a.shows[0].seasons[0].episodes[1], fresh: one !== again,
                      stream: findEpContext("stream:1").s.id, miss: findEpContext("nope") };
          a.shows.push(show("s3", ["e9"]));            // a show added in place is seen
          r.added = findEpContext("e9")?.s.id;
          S.data = { shows: [show("s4", ["e1"])] };    // the next library answer replaces everything
          r.next = [findEpContext("e1").s.id, findEpContext("e3")];
          S.data = null;
          r.none = findEpContext("e1");
          return r;
        """)
        self.assertEqual(out, {"first": "s1", "same": True, "fresh": True, "stream": "aid:9", "miss": None,
                               "added": "s3", "next": ["s4", None], "none": None})

    def test_the_whole_page_restyles_before_it_is_measured(self):
        body = APP.split("function render(inPlace = false) {", 1)[1].split("\n}\n", 1)[0]
        marks = body.index("ambient(); pageMarks();")
        self.assertLess(body.index("app.innerHTML = html;"), marks)
        for measures in ("lazyBg();", '$$("#app [data-glide]").forEach(glide);', "strips();", "paintWall("):
            self.assertLess(marks, body.index(measures), measures)
        self.assertEqual(body.count("ambient()"), 1)

    def test_the_accent_on_a_touch_screen_asks_for_no_style(self):
        body = APP.split("function ambFade(to) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('const from = !to || reduceMotion() || mq("(pointer:coarse)") ? "" : getComputedStyle(root).getPropertyValue("--amb").trim();', body)
        self.assertEqual(body.count("getComputedStyle"), 1)

    def test_recommendations_off_the_screen_are_skipped_on_touch(self):
        touch = CSS.split("@media (pointer:coarse){\n  html{transition:none}", 1)[1].split("\n}\n", 1)[0]
        self.assertIn(".hstrip.recs .rec:nth-child(n+4){content-visibility:auto;contain-intrinsic-size:auto 150px auto 120px;overflow-clip-margin:64px}", touch)
        # the card's width is its own, and the shadow (14 + 34 px) fits inside the 64 px margin
        self.assertIn(".rec{position:relative;flex:none;width:150px;", CSS)
        self.assertIn(".rec .rcv .poster{width:150px;border-radius:18px;box-shadow:0 14px 34px", CSS)

    def test_the_art_fades_without_being_redrawn(self):
        """A fade to an !important value can't be blended by the graphics
        chip: the whole background was drawn again on every frame of it
        (Home, and the show page on a phone). Nothing else sets the art's
        opacity above 0, so the rules win without the mark."""
        import re
        self.assertIn("body.pg-uph #canvas>i:not(.cv-grain){opacity:0}", CSS)
        self.assertIn("body.pg-sfr .cv-art{opacity:0}", CSS)
        for rule in re.findall(r"[^{}]*(?:#canvas|\.cv-)[^{}]*\{[^{}]*\}", CSS):
            self.assertNotRegex(rule, r"opacity:[^;}]*!important", rule)
        self.assertIn(".cv-blur,.cv-art{transition-duration:.5s}", CSS)  # the fade itself stays

    def test_the_blurred_art_is_blurred_once_per_picture_on_touch(self):
        """On a touch screen the blurred art is drawn once per picture into a
        small canvas in its layer; the screen only scales and fades it
        (Elias's phone: 30-100 ms frames on tab switches while the browser
        blurred it again at full sharpness). Soft keeps the old way: its look
        would change."""
        touch = CSS.split("@media (pointer:coarse){\n  html{transition:none}", 1)[1].split("\n}\n", 1)[0]
        # the old copy stays as it was: Soft uses it, and so does a browser without the canvas
        self.assertIn("  .cv-blur{inset:auto;left:50%;top:50%;width:34%;height:34%;transform:translate(-50%,-50%) scale(3.6);filter:blur(15px) saturate(1.5) brightness(.85)}", touch)
        self.assertIn("  :root[data-art=soft] .cv-blur{inset:auto;filter:blur(35px) saturate(1.3) brightness(.5)}", touch)
        # the canvas takes the same box, plus the blur's reach around it, and the same filter
        self.assertIn(":root:not([data-art=soft]) #canvas.cvc{--cvf:blur(15px) saturate(1.5) brightness(.85);--cvp:45px}", touch)
        layer = touch.split(":root:not([data-art=soft]) #canvas.cvc .cv-blur{", 1)[1].split("}", 1)[0]
        for part in ("inset:0", "transform:none", "filter:none", "background-image:none!important", "will-change:opacity"):
            self.assertIn(part, layer)
        copy = touch.split(":root:not([data-art=soft]) #canvas.cvc .cv-blur>canvas{", 1)[1].split("}", 1)[0]
        for part in ("display:block", "left:50%;top:50%", "width:calc(34% + 2*var(--cvp));height:calc(34% + 2*var(--cvp))",
                     "transform:translate(-50%,-50%) scale(3.6)"):
            self.assertIn(part, copy)
        self.assertIn(".cv-blur>canvas{display:none}", CSS)  # anywhere else
        # the show page's stronger blur, its reach 3x its radius
        self.assertIn("body.pg-sfr .cv-blur{filter:blur(28px) saturate(1.3) brightness(.55)}", CSS)
        self.assertIn("@media (pointer:coarse){:root:not([data-art=soft]) body.pg-sfr #canvas.cvc{--cvf:blur(28px) saturate(1.3) brightness(.55);--cvp:84px}}", CSS)
        # the picture is still set on the layer (Soft, the banner fix), and handed to the canvas
        self.assertIn('if (url) { off.style.backgroundImage = cssUrl(url); off.classList.remove("out", "rest"); cvPicture(off, url); }', APP)
        self.assertIn('if (mq("(pointer:coarse)") && typeof ResizeObserver === "function") {', APP)
        # the CSS is read a frame after the look changed, never while a page builds
        look = APP.split("function cvLook() {", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(look.index("if (mark === cvc.mark) return;"), look.index("requestAnimationFrame("))
        self.assertLess(look.index("requestAnimationFrame("), look.index("getComputedStyle("))
        self.assertIn("  cvLook();\n  const art = canvasArt(), mode = lookGet(\"art\");", APP)
        self.assertIn("  cvLook(); // and a show page on a phone blurs it more (pg-sfr)", APP)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_canvas_copy_fills_its_box_like_cover(self):
        import json, re, subprocess
        fns = "\n".join(re.search(r"^function %s\(.*?^\}" % n, APP, re.S | re.M).group(0) for n in ("cvPicture", "cvDraw"))
        src = """
const cvc = { on: true, f: "blur(15px) saturate(1.5) brightness(.85)", p: 45, mark: "" };
const pic = (u) => "/copy" + u;
const loads = [], drawn = [];
class Image { set src(v) { this.s = v; loads.push(this); } decode() { return new Promise((r) => { this.go = (w, h) => { this.naturalWidth = w; this.naturalHeight = h; r(); }; }); } }
let clears = 0, w = 300;
const c = { _w: 230, _h: 396, height: 150, get width() { return w; }, set width(v) { w = v; clears++; },
  getContext() { const x = { drawImage: (...a) => drawn.push({ f: x.filter, q: x.imageSmoothingQuality, size: [c.width, c.height], img: a[0].s, a: a.slice(1).map((n) => +n.toFixed(2)) }) }; return x; } };
const el = { firstElementChild: c };
%s
const tick = () => new Promise((r) => setTimeout(r));
(async () => {
  cvPicture(el, "/a.jpg");                 // load 0
  cvPicture(el, "/b.jpg");                 // load 1: a's answer comes too late
  const cleared = clears;
  loads[0].go(1000, 500); await tick();
  const afterLate = drawn.length;
  loads[1].go(1000, 500); await tick();
  cvPicture(el, "/b.jpg");                 // the same picture again: nothing
  const again = loads.length;
  cvc.f = "blur(28px) saturate(1.3) brightness(.55)"; cvc.p = 84; c._w = 308; c._h = 474;
  const before = clears; cvDraw(el);       // the look changes: drawn again, the old picture stays meanwhile
  const kept = clears === before;
  loads[2].go(460, 650); await tick();
  console.log(JSON.stringify({ src: loads.map((l) => l.s), cleared, afterLate, again, kept, drawn }));
})();
""" % fns
        out = subprocess.run(["node", "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["src"], ["/copy/a.jpg", "/copy/b.jpg", "/copy/b.jpg"])  # the phone's copy, as cssUrl uses
        self.assertEqual((r["cleared"], r["afterLate"], r["again"], r["kept"]), (2, 0, 2, True))
        one, two = r["drawn"]
        # 140x306 box (230x396 minus 45 each side), a 1000x500 picture: its middle 228.76 columns, all rows
        self.assertEqual(one, {"f": "blur(15px) saturate(1.5) brightness(.85)", "q": "high", "size": [230, 396], "img": "/copy/b.jpg",
                               "a": [385.62, 0, 228.76, 500, 45, 45, 140, 306]})
        # the show page's look, a poster: still the middle columns (the box is narrower than a poster)
        self.assertEqual(two, {"f": "blur(28px) saturate(1.3) brightness(.55)", "q": "high", "size": [308, 474], "img": "/copy/b.jpg",
                               "a": [81.31, 0, 297.39, 650, 84, 84, 140, 306]})


if __name__ == "__main__":
    unittest.main()
