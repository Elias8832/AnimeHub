"""Decision 44: the phone and tablet layout — a floating bottom tab bar in
the One UI 9 style, the phone Home (up-next card, the deck, the week),
the show page's Play bar and the library's filter sheet drawn outside the
animated page, settings as a list, the tablet side list. The PC layout
must not change: everything here is behind a phone/tablet check."""
import re
import unittest

from tests import support

WEB = support.ROOT / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


class PhoneLayout(unittest.TestCase):
    def test_layers_exist(self):
        for el in ('<nav id="tabbar"', '<div id="deck">', '<div id="portal">'):
            self.assertIn(el, HTML)

    def test_tab_bar_is_phone_only(self):
        self.assertIn('if (!isPhone()) { if (el.firstChild) el.innerHTML = ""; return; }', APP)
        self.assertIn('<i class="ind"></i>', APP)  # the highlight glides between tabs
        self.assertIn("#tabbar,.fltb,.fltdone,.fltscrim,.sback,.fltrow,.disc{display:none}", CSS)
        phone = CSS[CSS.index("/* ───────── phone and tablet layout"):]
        self.assertIn("#tabbar{display:flex;position:fixed", phone)
        self.assertIn("backdrop-filter:blur(26px)", phone)  # glass, One UI 9

    def test_pc_paths_unchanged(self):
        """Phone pages branch off at the top; the PC builders keep running."""
        self.assertIn("function Home() {\n  if (isPhone()) return HomePhone();", APP)
        self.assertIn("if (isPhone() && !S.setOpen) return settingsList(tabs, s);", APP)
        self.assertIn("if (isTabletP()) body += homePair(L, airing);", APP)

    def test_week_is_a_view(self):
        self.assertIn('week: Week }[S.view]()', APP)
        self.assertIn('"week"]', APP)
        self.assertIn("(library|history|stats|settings|week)", APP)

    def test_fixed_bits_live_outside_the_animated_page(self):
        """The entrance animation leaves a transform on #app's children, so
        fixed elements inside it would scroll with the page."""
        self.assertIn('const b = $("#app .sp2 .cta .btn.pri"), more = $("#app .frpage .cta [data-a=frmore]");', APP)
        self.assertIn("paintPortals();\n}\n/* Background refreshes", APP)
        self.assertIn("body.has-pbar .sp2 .cta .btn.pri{display:none}", CSS)
        self.assertIn(".fsheet .filters{display:flex", CSS)

    def test_deck_and_actions(self):
        for a in ("deck:", "deckclose:", "deckplay:", "deckopen:", "setopen:", "setback:", "sidetab:", "fltopen:", "fltclose:"):
            self.assertIn("  " + a if a == "deck:" else a, APP)
        self.assertIn('data-a="deck" data-v="recs"', APP)  # Home → Recommended → Discover
        self.assertIn('data-a="deck" data-v="lib"', APP)   # Library → Swipe
        self.assertIn("deckadd:", APP)
        self.assertIn("scroll-snap-type:y mandatory", CSS)

    def test_breakpoints(self):
        self.assertIn('const isPhone = () => mq("(max-width:700px)");', APP)
        self.assertRegex(APP, re.escape('const isTabletL = () => mq("(min-width:901px) and (max-width:1400px) and (pointer:coarse)'))


class TouchSmoothness(unittest.TestCase):
    """Touch screens skip the costly effects (measured: page changes spent
    360–470 ms restyling for the accent fade; the library 1.9 s on 271 cards)."""

    def test_touch_rules(self):
        touch = CSS[CSS.index("/* ───────── touch screens: smoothness"):]
        for rule in ("html{transition:none}", ".cv-grain{display:none}", "#app *{-webkit-backdrop-filter:none!important",
                     "#app.nav-in *,.wall.fade .card,.wall.fade .prog i{animation:none!important}"):
            self.assertIn(rule, touch)
        self.assertTrue(touch.lstrip("/* ─").find("@media (pointer:coarse){") > 0)  # never the PC

    def test_library_builds_in_batches(self):
        self.assertIn('if ((fx || !w.firstChild) && cards.length > 36) {', APP)

    def test_nothing_wider_than_the_screen(self):
        self.assertIn("html,body{overflow-x:clip;overscroll-behavior-x:none}", CSS)
        self.assertIn(".jband .jchart{width:auto;min-width:0;", CSS)


class TabSlideAndSearch(unittest.TestCase):
    def test_tab_slide(self):
        self.assertIn("const TAB_AT = { home: 0, week: 0, library: 1, history: 2, stats: 2 };", APP)
        self.assertIn("  tabSlide(from, view);\n}", APP)
        self.assertIn("#app.tab-l{animation:tabL .34s", CSS)
        self.assertNotIn("#app.tab-l{animation:tabL .34s cubic-bezier(.2,.8,.2,1) both", CSS)  # no fill: no transform left on #app

    def test_touch_search(self):
        self.assertIn('sr.innerHTML = S.menu === "sr" ? (touchSearch() ? srHTMLTouch() : srHTML()) : "";', APP)
        for a in ("srq:", "srclr:", "srlist:", "srgo:"):
            self.assertIn(a, APP)
        self.assertIn('#pal .pclose:after{content:"Cancel"', CSS)

    def test_keyboard_does_not_push_the_page(self):
        self.assertIn("interactive-widget=resizes-content", HTML)
        self.assertIn('vv?.addEventListener("resize", fitPal);', APP)
        self.assertIn("body.srch{overflow:hidden}", CSS)


class BackAndSwipes(unittest.TestCase):
    """Android's back gesture closes the top layer first; layers close with a
    swipe down; the mini player opens with a swipe up; tabs switch with a
    sideways swipe (not from the screen edges — those are the system's)."""

    def test_layers_take_a_history_entry(self):
        for name in ('ovOpen("rmt"', 'ovOpen("deck"', 'ovOpen("sr"', 'ovOpen("flt"', 'ovOpen("set"'):
            self.assertIn(name, APP)
        self.assertIn("const top = OV.pop();\n  if (top) { top.close(); return; }", APP)
        self.assertIn("if (history.state?.ov) { OV.length = 0; history.replaceState(h, \"\", navUrl()); return; }", APP)

    def test_swipes(self):
        for sel in ('sel: ".rview"', 'sel: "#deck .deck"', 'sel: "#pal .pbox"', 'sel: ".fsheet .filters"'):
            self.assertIn(sel, APP)
        self.assertIn("if (y - p.y < -28) { pull = null; buzz(); A.rmtopen(); }", APP)
        self.assertIn("x > 24 && x < innerWidth - 24) { // the outer edges are the system's back gesture", APP)
        self.assertIn('else if (["show", "fr", "week", "settings"].includes(S.view) && history.length > 1) pull = { kind: "back"', APP)
        self.assertIn("#swhint.armed i{background:var(--accent)", CSS)


class ShowPagePhone(unittest.TestCase):
    """S2: only Play (+ ⋯) at the bottom; the page's other buttons open from
    ⋯ as a bottom sheet; no studio line, no big stats."""

    def test_s2(self):
        self.assertIn(".frpage .cta,.frpage .mfacts,.frpage .st2 .fvb,.frpage .srcl{display:none!important}", CSS)
        self.assertIn('for (const b of [...$$("#app .frpage .cta .btn:not(.pri):not(.ic)")].reverse()) {', APP)
        self.assertIn("#cm{z-index:48;left:0!important;right:0;top:auto!important;bottom:0;", CSS)


class InstallAsApp(unittest.TestCase):
    """Phones install the app as an app: manifest icons Android asks for, a
    service worker that caches nothing (only an offline notice)."""

    def test_manifest_and_worker(self):
        from animehub import server
        sizes = {i["sizes"] for i in server.MANIFEST["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertEqual(server.MANIFEST["display"], "standalone")
        for f in ("icon-192.png", "icon-512.png", "sw.js"):
            self.assertTrue((WEB / f).exists(), f)
        sw = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn('if (e.request.mode !== "navigate") return;', sw)
        self.assertNotIn("caches.", sw)  # nothing cached: the PC stays the source
        self.assertIn('navigator.serviceWorker.register("/sw.js")', APP)


if __name__ == "__main__":
    unittest.main()
