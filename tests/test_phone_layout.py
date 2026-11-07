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

    def test_discover_is_a_feed(self):
        # TODO 92: shuffled, no count, no next title; Library → Swipe keeps all three.
        self.assertIn('src === "recs" ? shuffled(deckItems(src)) : deckItems(src)', APP)
        self.assertIn('${feed ? "" : `<span class="dcount', APP)
        self.assertIn('(!feed || !k)', APP)
        self.assertIn('data-a="deckagain"', APP)  # the end card

    def test_show_page_stays_clean(self):
        # TODO 140: no logo over the art, one row of tabs, a ring that isn't cut off, a shade behind Play.
        self.assertIn("body:has(.frpage) #nav .brand{visibility:hidden}", CSS)
        self.assertIn(".frpage .tabs2{display:flex;flex-direction:row", CSS)
        self.assertIn(".sp2 .ep.resume .epa:after", CSS)
        self.assertIn(".pbar:before", CSS)

    def test_deck_art_keeps_its_size(self):
        # A zoom on the open card made pictures change size mid-swipe.
        self.assertNotIn("dzoom", CSS)

    def test_deck_swipe_keeps_the_fingers_speed(self):
        # The browser's snap ignores the flick; deckPager's spring starts at the finger's speed.
        self.assertIn("function deckPager(sc, land)", APP)
        self.assertIn("scroll-snap-type:none;touch-action:none;will-change:transform", CSS)
        self.assertIn("anim = sc.animate(frames", APP)  # the GPU plays the glide

    def test_floating_bars_do_not_blur_on_touch(self):
        # TODO 89, 91: blur over a moving page lags a phone's GPU.
        block = CSS[CSS.index("/* Blur over a moving page"):]
        for sel in ("body.sc #nav", "#tabbar .tb", ".rbar", ".pbar .btn"):
            self.assertIn(sel, block)
        self.assertIn("body.srch #app,body.srch #status{filter:brightness(.55)}", block)

    def test_back_swipe_rebuilds_after_the_slide(self):
        # TODO 89: rebuilding the page underneath froze the slide; popstate puts #app back.
        self.assertIn("if (swiped) unstyle(app, $(\"#portal\"));", APP)
        self.assertIn("#app{will-change:transform}", CSS)

    def test_swiping_the_deck_stays_cheap(self):
        self.assertIn(".deck,.deck *{-webkit-backdrop-filter:none!important", CSS)
        self.assertIn("body.deck-open:not(.peek) #canvas", CSS)

    def test_deck_loads_art_near_the_card_only(self):
        self.assertIn('data-art="${esc(x.art)}"', APP)
        self.assertNotIn('--art:${esc(cssUrl(x.art))}', APP)

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
        self.assertIn("const TAB_AT = { home: 0, library: 1, history: 2, stats: 2, week: 3 };", APP)
        self.assertIn("  tabSlide(from, view);\n}", APP)
        self.assertIn("#app.tab-l{animation:tabL .34s", CSS)
        self.assertNotIn("#app.tab-l{animation:tabL .34s cubic-bezier(.2,.8,.2,1) both", CSS)  # no fill: no transform left on #app

    def test_touch_search(self):
        self.assertIn('sr.innerHTML = S.menu === "sr" ? (touchSearch() ? srHTMLTouch() : srHTML()) : "";', APP)
        for a in ("srshow:", "srclr:", "srlist:", "srgo:"):
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
        self.assertIn("const top = OV.pop();\n  if (top) { top.close();", APP)
        self.assertIn("if (history.state?.ov) { OV.length = 0; history.replaceState(h, \"\", navUrl()); return; }", APP)

    def test_swipes(self):
        for sel in ('sel: ".rview"', 'sel: "#deck .deck"', 'sel: "#pal .pbox"', 'sel: ".fsheet .filters"'):
            self.assertIn(sel, APP)
        self.assertIn("if (y - p.y < -28) { pull = null; buzz(); A.rmtopen(); }", APP)
        self.assertIn("x > 24 && x < innerWidth - 24) { // the outer edges are the system's back gesture", APP)
        self.assertIn('else if (["show", "fr", "settings"].includes(S.view) && history.length > 1) pull = { kind: "back"', APP)
        self.assertIn("#swhint.armed i{background:var(--accent)", CSS)


class ShowPagePhone(unittest.TestCase):
    """S2, redone as Show C (TODO 177): Play (+ ⋯) docked at the bottom; the
    page's other buttons are a row of icons under the title; ⋯ opens as a
    bottom sheet with the rare actions."""

    def test_s2(self):
        self.assertIn(".sp2.frpage .hero .cta{grid-column:1/-1;display:block!important", CSS)
        self.assertIn(".sp2.frpage .hero .cta>.btn.pri{display:none}", CSS)  # Play lives in the dock
        self.assertNotIn(".frpage .cta .grp .gb:not([data-a=frmore])", APP)  # no longer copied into ⋯
        self.assertIn("#cm{z-index:48;left:0!important;right:0;top:auto!important;bottom:0;", CSS)

    def test_show_c_poster_header(self):
        self.assertIn("const framed = isPhone() ||", APP)
        self.assertIn(".sp2.frpage .hero .hl2{display:contents}", CSS)
        # .hp is also the old 3 px progress bar class: the poster must not be clipped by it
        self.assertIn(".sp2.frpage .hp{display:block;grid-column:1;grid-row:1/3;align-self:end;width:auto;height:auto;overflow:visible", CSS)
        self.assertIn(".sp2.frpage .eps.strip{flex-direction:row", CSS)
        self.assertIn('descx: (v, t) => { if (isPhone()) t.classList.toggle("open"); }', APP)
        self.assertIn("body:has(.sp2.frpage) .cv-art{opacity:0!important}", CSS)


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


class AppIcon(unittest.TestCase):
    """One icon per accent colour; the default wears the default accent."""

    def test_every_accent_has_its_icon(self):
        table = re.search(r"const ACCENT_ICONS = \{(.*?)\};", APP).group(1)
        pairs = re.findall(r'"(#[0-9a-f]{6})": "(\w+)"', table)
        accents = re.search(r"const ACCENTS = \[(.*?)\];", APP).group(1)
        self.assertEqual({a for a, _ in pairs}, set(re.findall(r"#[0-9a-f]{6}", accents)))
        for accent, name in pairs:
            self.assertIn(f'fill="{accent}"', (WEB / "icons" / f"{name}.svg").read_text(encoding="utf-8"))

    def test_nav_bar_wears_the_app_icon(self):
        """Inline and drawn in the live accent, so it follows the show too."""
        self.assertIn('<svg class="bm"', HTML)
        self.assertIn("fill:var(--accent)", CSS)

    def test_default_icon_is_the_orange_one(self):
        self.assertEqual((WEB / "icon.svg").read_bytes(), (WEB / "icons" / "orange.svg").read_bytes())


class PhonePolish(unittest.TestCase):
    """TODO 177 (Elias picked mockup A): one glass dock, the Library's sort
    line and row sheet, and the small phone fixes."""

    def test_search_sits_inside_the_pill(self):
        self.assertIn('<i class="tsep"></i><button class="sq" data-a="sropen" aria-label="Search">', APP)
        self.assertIn("#tabbar{display:flex;position:fixed;left:12px;right:12px", CSS)

    def test_dock_shrinks_on_scroll_and_resets_on_navigation(self):
        self.assertIn("function dockScroll()", APP)
        self.assertIn("artDim(); dockScroll(); }", APP)
        self.assertIn('document.body.classList.remove("tb-min");', APP)
        self.assertIn("body.tb-min{--dock:46px}", CSS)

    def test_play_row_and_remote_dock_on_the_tabs(self):
        self.assertIn("bottom:calc(12px + var(--dock) + env(safe-area-inset-bottom))", CSS)
        self.assertIn("body.rmt-on #tabbar .tb,body.has-pbar #tabbar .tb{border-radius:0 0 26px 26px", CSS)

    def test_library_sort_line_and_row_sheet(self):
        self.assertIn('<div class="lsort"><label class="srt">', APP)
        self.assertIn('data-a="fltopen">${ico("filter")}Filter', APP)
        self.assertIn('<div class="filters"><div class="lflt">${filterCtl((t) => t, true)}${fchoice("group", "show", GROUP)}</div>', APP)
        self.assertIn(".fsheet .filters{gap:0;padding-top:12px;flex-wrap:nowrap}", CSS)  # a tall sheet wrapped into columns
        self.assertIn('if (k === "group" || isPhone()) return render();', APP)

    def test_small_fixes(self):
        self.assertIn("#totop{display:none}", CSS)  # the open tab again goes to the top
        self.assertIn(".match.mc .guess.none{display:block", CSS)
        self.assertIn(".ropt .seg{display:flex;flex-wrap:wrap", CSS)
        self.assertIn("#pal .plist{-webkit-mask:linear-gradient", CSS)

    def test_home_a_hero_and_rails(self):
        # Round 2 (Elias picked Home A): the up-next fills the top, the rest are sideways rails.
        self.assertIn("function upHero(up)", APP)
        self.assertIn("art = s.poster || safeThumb(sea, ep) || s.banner;", APP)  # a wide banner shows a sliver in a tall hero
        self.assertIn('<div class="crail">${L.map(contStill).join("")}</div>', APP)
        self.assertIn(".home2.phone .rdy{flex-wrap:nowrap;overflow-x:auto", CSS)
        self.assertIn("function Home() {\n  if (isPhone()) return HomePhone();", APP)  # the PC Home is untouched

    def test_sideways_rails_do_not_switch_pages(self):
        # Elias: swiping the show page's episodes went back / to another tab.
        self.assertIn('.looks, .rdy, .agenda, .crail")', APP)
        self.assertIn('!(t.closest(".strip") && !(isPhone() && t.closest(".eps") && !t.closest(".frpage")))', APP)

    def test_show_page_round_fixes(self):
        self.assertIn('f?.favourite && !isPhone() ? "Remove from favourites" : "Favourite"', APP)
        self.assertIn(".sp2.frpage .about:not(.tagsopen) dd.tags .tg:nth-child(n+7){display:none}", CSS)
        self.assertIn(".sp2.frpage .castrow .ot .pw{width:84px;margin-top:0", CSS)  # supporting faces sat lower
        self.assertIn(".sp2.frpage .eps-head .tord{display:none}", CSS)  # the Sub/Dub switch was cut off
        # Elias: thick gaps between episode tiles — the rows' 40 px for ⋯ stayed; ⋯ itself sat off screen
        self.assertIn(".sp2.frpage .eps.strip .ep{display:block;flex:none;width:228px;padding-right:0;", CSS)
        self.assertIn(".sp2.frpage .eps.strip .ep .dots{left:auto;right:-6px;top:calc(228px * 9 / 16 + 4px)", CSS)
        self.assertIn(".sp2.frpage .hero .grp [data-a=frmore]{display:none}", CSS)  # one ⋯ only: the dock's (Elias)

    def test_top_corners_are_bare_icons(self):
        # Elias picked B: no circle, a white icon with a soft shadow; back is a chevron on phones
        self.assertIn("#nav .setb,:root[data-bar] #nav .setb{width:44px;height:44px;margin:0 -10px 0 0;padding:0;gap:0;border-radius:50%;background:none!important", CSS)
        self.assertIn('${ico(isPhone() ? "chevl" : "back")}${backLabel()}', APP)

    def test_home_hero_meets_a_plain_background(self):
        # Elias: scrolling Home showed an edge under the hero — the blurred art and the
        # accent glow behind the page were lighter than the colour the hero fades to
        self.assertIn("body:has(.home2.has-uph) #canvas>i:not(.cv-grain){opacity:0!important}", CSS)
        self.assertIn("body:has(.home2.has-uph) #canvas{background:var(--bg)}", CSS)

    def test_rails_hide_the_previous_tile(self):
        # Elias: a snapped tile left 6 px of the one before showing; above the strip's own edge fades (.ml/.mr)
        self.assertIn("#app .sp2.frpage .eps.strip,#app .home2.phone .crail,#app .home2.phone .rdy{-webkit-mask:linear-gradient(90deg,transparent 0,transparent 4px,#000 16px)", CSS)


if __name__ == "__main__":
    unittest.main()
