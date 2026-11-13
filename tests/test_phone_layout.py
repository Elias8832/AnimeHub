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
        self.assertIn("paintPortals();\n  pagerStale();\n}\n/* Background refreshes", APP)
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
        self.assertIn("body.pg-fr #nav .brand{visibility:hidden}", CSS)
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
        for rule in ("html{transition:none}", ".cv-grain{display:none}", "#app *,.tpeek *{-webkit-backdrop-filter:none!important",
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
        self.assertIn('#pal .pclose:after{content:"";display:block;width:18px', CSS)  # a round close button (round 4, Search B)

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

    def test_back_from_a_playlist_lands_on_the_list(self):
        """Elias: back inside a playlist left the Library. Opening one is its own
        step and so is the Playlists tab (always the overview); landing inside one
        from elsewhere, back goes where you came from."""
        self.assertIn('history.pushState({ ...navSnap(), inApp: true, plStep: true }, "", navUrl()); },', APP)
        self.assertIn('if (step) history.pushState({ ...navSnap(), inApp: true }, "", navUrl()); else navKeep();', APP)
        self.assertIn('if (S.view === "library" && plCurrent() && history.state?.plStep) pull = { kind: "back"', APP)
        self.assertIn('S.lib.tab = h.lt; if (h.pl !== undefined) S.lib.pl = h.pl;', APP)

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
        self.assertIn("body.pg-sfr .cv-art{opacity:0}", CSS)

    def test_select_bar_sits_on_the_dock(self):
        # Elias, 2026-11-10: the select bar was drawn behind the dock on phones
        self.assertIn('document.body.classList.toggle("sel-on", !!on)', APP)
        self.assertIn('<span class="fill"></span><i class="selbr"></i>', APP)
        self.assertIn("#selbar{left:12px;right:12px;width:auto;bottom:calc(12px + var(--dock) + env(safe-area-inset-bottom))", CSS)
        self.assertIn("body.sel-on .pbar{visibility:hidden}", CSS)
        self.assertIn("body.rmt-on #selbar{bottom:calc(72px + var(--dock)", CSS)
        self.assertIn("#selbar .selbr{display:none}", CSS)  # one row on the PC


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
        self.assertIn('<div class="filters"><div class="lflt">${filterCtl()}${fchoice("group", "show", GROUP)}</div>', APP)
        self.assertIn(".fsheet .filters{gap:0;padding-top:12px;flex-wrap:nowrap}", CSS)  # a tall sheet wrapped into columns
        self.assertIn('if (k === "group" || isPhone()) { // phones: the sheet and the Filter count follow', APP)

    def test_small_fixes(self):
        self.assertIn("#totop{display:none}", CSS)  # the open tab again goes to the top
        self.assertIn(".dialog.match>*{min-width:0}", CSS)  # the match sheet's guesses row doesn't widen it past the screen
        self.assertIn(".rview .rrb .seg{display:flex;flex-wrap:wrap", CSS)  # remote choices wrap, not run off the edge
        self.assertIn("#pal .plist{-webkit-mask:linear-gradient", CSS)

    def test_home_a_hero_and_rails(self):
        # Round 2 (Elias picked Home A): the up-next fills the top, the rest are sideways rails.
        self.assertIn("function upHero(up)", APP)
        self.assertIn('<div class="uph-in" data-a="open" data-v="${esc(s.id)}">', APP)  # the text opens the show too (Elias)
        self.assertNotIn('aria-label="Open ${esc(s.title)}">${ico("chevr")}</button>', APP)  # no › beside Play
        self.assertIn("art = s.poster || safeThumb(sea, ep) || s.banner;", APP)  # a wide banner shows a sliver in a tall hero
        self.assertIn('<div class="crail">${L.map(contStill).join("")}</div>', APP)
        self.assertIn(".home2.phone .rdy{flex-wrap:nowrap;overflow-x:auto", CSS)
        self.assertIn("function Home() {\n  if (isPhone()) return HomePhone();", APP)  # the PC Home is untouched

    def test_sideways_rails_do_not_switch_pages(self):
        # Elias: swiping the show page's episodes went back / to another tab.
        self.assertIn('.looks, .rdy, .agenda, .crail")', APP)
        self.assertIn('!(t.closest(".strip") && !(isPhone() && t.closest(".eps") && !t.closest(".frpage")))', APP)

    def test_show_page_round_fixes(self):
        self.assertIn(".sp2.frpage .about:not(.tagsopen) dd.tags .tg:nth-child(n+7){display:none}", CSS)
        self.assertIn(".sp2.frpage .castrow .ot .pw{width:84px;margin-top:0", CSS)  # supporting faces sat lower
        self.assertNotIn(".tord", CSS)  # the watch-order link is gone (Decision 66: the season dropdown); Sub/Dub was cut off by it
        # Elias: thick gaps between episode tiles — the rows' 40 px for ⋯ stayed; ⋯ itself sat off screen
        self.assertIn(".sp2.frpage .eps.strip .ep{display:block;flex:none;width:228px;padding-right:0;", CSS)
        self.assertIn(".sp2.frpage .eps.strip .ep .dots{left:auto;right:-6px;top:calc(228px * 9 / 16 + 4px)", CSS)
        self.assertIn(".sp2.frpage .hero .grp [data-a=frmore]{display:none}", CSS)  # one ⋯ only: the dock's (Elias)

    def test_top_corners_are_bare_icons(self):
        # Elias picked B: no circle, a white icon with a soft shadow; back is a chevron on phones
        self.assertIn("#nav .setb,:root[data-bar] #nav .setb{width:44px;height:44px;margin:0 -10px 0 0;padding:0;gap:0;border-radius:50%;background:none!important", CSS)
        self.assertIn('<button class="backbtn" data-a="back">${ico("chevl")}${backLabel()}', APP)

    def test_desktop_back_is_the_action_bar_pill(self):
        # Elias picked H: the Download bar's glass pill, its height and font, no esc chip
        self.assertIn(".sp2 .backbtn{display:flex;width:fit-content;align-items:center;gap:8px;height:42px;padding:0 18px 0 12px;border-radius:999px;font:600 14.5px var(--ui);color:var(--ink);background:var(--glass);box-shadow:var(--edge);backdrop-filter:blur(14px);margin:18px 0 -16px!important", CSS)
        self.assertNotIn(".backbtn:hover .ico{transform:translateX", CSS)  # Elias: the arrow's hover slide looked odd
        self.assertIn(".sp2 .backbtn:hover::before{background:rgba(255,255,255,.1)}", CSS)
        self.assertIn(".sp2 .backbtn::before{content:\"\";position:absolute;inset:4px;", CSS)  # the wash sits inside the glass, like a button in the action bar  # Elias: the Download button's hover (its .1 white wash)

    def test_home_hero_meets_a_plain_background(self):
        # Elias: scrolling Home showed an edge under the hero — the blurred art and the
        # accent glow behind the page were lighter than the colour the hero fades to
        self.assertIn("body.pg-uph #canvas>i:not(.cv-grain){opacity:0}", CSS)
        self.assertIn("body.pg-uph #canvas>i:not(.cv-grain){transition:opacity .25s}", CSS)  # art and wash together: the art outlived its wash
        self.assertIn("body.pg-uph #canvas{background:var(--bg)}", CSS)

    def test_rails_hide_the_previous_tile(self):
        # Elias: a snapped tile left 6 px of the one before showing; above the strip's own edge fades (.ml/.mr)
        self.assertIn("#app .sp2.frpage .eps.strip,#app .home2.phone .crail,#app .home2.phone .rdy,.tpeek .home2.phone .crail,.tpeek .home2.phone .rdy{-webkit-mask:linear-gradient(90deg,transparent 0,transparent 8px,#000 16px)", CSS)

    def test_episode_tile_art_stays_in_its_ring(self):
        # Elias: hovering zoomed the art past the ring, and the progress bar ran past the
        # rounded corners and under the ring
        self.assertIn(".sp2 .eps.strip .ep:hover .epimg{transform:none}", CSS)
        self.assertIn(".sp2 .eps.strip .ep .eprog{height:16px;border-radius:0 0 16px 16px;overflow:hidden", CSS)
        self.assertIn(".sp2 .eps.strip .ep.resume .eprog,.sp2 .eps.strip .ep.sel .eprog{left:2px;right:2px;bottom:2px", CSS)


class PhonePlayerDeck(unittest.TestCase):
    """TODO 177 round 2: Elias picked D1 in the app's own look — one deck made like the
    dock (Play on the timeline, labelled buttons under it), Skip as a dock-material pill,
    one sheet with tabs, double-tap jumps, a lock."""

    def test_the_deck_replaces_the_middle_buttons(self):
        self.assertIn('<div class="pp-deck">', APP)
        self.assertIn('<button class="pp-pb" data-a="pptoggle"', APP)
        self.assertNotIn("pp-mid", APP)
        self.assertNotIn("pp-big", APP)
        for v in ('data-v="eps"', 'data-v="audio"', 'data-v="menu"', 'data-a="ppnextep"'):
            self.assertIn(v, APP)
        self.assertIn("#pp .pp-deck{position:absolute;z-index:2;", CSS)
        self.assertIn("--pdock:#1c1d22", CSS)  # the dock's colour; no blur in the player (it lags phones)

    def test_skip_rides_above_the_deck(self):
        self.assertIn('<i class="dot ${seg.kind === "op" ? "op" : "ed"}"></i>', APP)
        self.assertIn("#pp.ui .pp-skip{bottom:calc(var(--pbot) + var(--deck) + 10px)}", CSS)

    def test_one_sheet_three_tabs(self):
        self.assertIn('const PP_TABS = [["eps", "Episodes"], ["audio", "Audio"], ["menu", "Playback"]];', APP)
        self.assertIn("function ppAudioHTML()", APP)
        self.assertIn("safeThumb(c.sea, c.ep)", APP)  # stills, spoiler-safe

    def test_gestures_and_lock(self):
        self.assertIn("function ppTap(e)", APP)
        self.assertIn("now - T.t < (T.run ? 700 : 300)", APP)  # two taps jump, each tap after adds 10 s
        # Elias: two taps in the middle play/pause, and a double tap never brings the controls up
        self.assertIn("if (!side) { PP.tap = {}; if (v) v.paused ? v.play()", APP)
        self.assertIn("Date.now() - (PP.quiet || 0) < 1500) return;", APP)
        self.assertIn('el.addEventListener("touchend"', APP)  # a scrolling list cancels pointer events
        self.assertIn("#pp.locked .pp-top,#pp.locked .pp-deck,#pp.locked .pp-skip{opacity:0!important", CSS)

    def test_the_sheet_follows_the_finger(self):
        # Elias: swipe animations, above all between Episodes · Audio · Playback
        self.assertIn('<div class="pp-pages">', APP)  # the three tabs side by side
        self.assertIn("function ppPage(i, anim = true, f = 0)", APP)  # slides, or follows a finger part way
        self.assertIn('el.addEventListener("touchmove"', APP)
        self.assertIn('g.axis = g.sheet ? (ax > ay ? "x" : g.dy > 0 && g.top ? "down" : "scroll")', APP)
        self.assertIn("#pp .pp-panel.out{transform:translateY(100%)!important", CSS)  # closing slides away
        self.assertIn("#pp .pp-ind{position:absolute", CSS)  # the tab highlight moves

    def test_the_dot_sits_on_top_of_the_marks(self):
        # Elias: the opening's mark ran over the timeline's dot — the bar is drawn under the slider now
        self.assertIn("#pp .pp-seek::-webkit-slider-runnable-track{height:4px;border-radius:2px;background:transparent}", CSS)
        self.assertIn("z-index:0;pointer-events:none;border-radius:2px;background:rgba(255,255,255,.18)}", CSS)
        self.assertIn('r.parentElement.style.setProperty("--p"', APP)



class PhoneRound3(unittest.TestCase):
    """TODO 177 round 3: Elias picked Calendar A (agenda), Journal A (tabs), Settings B (inline rows)."""

    def test_calendar_is_an_agenda_on_phones(self):
        self.assertIn("if (isPhone() && aniMode()) return CalPhone();", APP)
        self.assertIn('<div class="agstrip">', APP)
        self.assertIn('data-a="agjump"', APP)
        self.assertIn("agjump: (v) =>", APP)
        self.assertIn(".agday{scroll-margin-top:76px}", CSS)

    def test_the_tablet_timeline_is_not_squeezed_by_the_progress_bar(self):
        # the download bar's .tline (6 px tall) hid the calendar's airing list
        self.assertIn("span.tline{flex:1;height:6px;", CSS)
        self.assertNotIn("\n.tline{flex:1;height:6px;", CSS)

    def test_journal_has_history_and_stats_tabs_on_phones(self):
        self.assertIn('jTab: lsGet("jTab", "history")', APP)
        self.assertIn('data-a="jtab"', APP)
        self.assertIn('<div class="jchips">', APP)
        self.assertIn("jtab: (v) =>", APP)

    def test_journal_tabs_follow_a_sideways_finger(self):
        self.assertIn("function jPaint(p, live)", APP)
        self.assertIn('<div class="jpages"><div class="jtrack">', APP)
        self.assertIn("jp: !!t.closest(\".jr2.phone .jpages\")", APP)  # the page swipe yields while the tabs can move
        self.assertIn(".jr2.phone:not(.jmove) .jpane:not(.on){height:0;overflow:hidden}", CSS)

    def test_settings_rows_put_the_choice_beside_the_name(self):
        self.assertIn(".set2:not(.phone) .srow>div:first-child{display:contents}", CSS)
        self.assertIn(".set2:not(.phone) .srow{flex-direction:row!important;flex-wrap:wrap;", CSS)



class PhoneRound4(unittest.TestCase):
    """TODO 177 round 4: Elias picked Search B (field at the thumb, tidied) and Remote B (big controls on the art)."""

    def test_search_library_hits_are_wide_cards(self):
        self.assertIn('"srwide", `<span class="tx"><b>${hl(s.title, qq)}</b>', APP)
        self.assertIn("sec(`On AniList <small>${remote.length}</small>`)", APP)
        self.assertIn('.pal2.touch .mi.srwide:before{content:"";display:block;position:absolute;inset:0;width:auto;', CSS)  # beats the PC list's 3 px selection bar

    def test_search_field_is_a_dock_pill(self):
        self.assertIn("#pal .pin{height:62px;padding:0 9px 0 18px;gap:12px;border-radius:26px;background:rgba(28,29,34,.96)", CSS)
        self.assertNotIn('content:"Cancel"', CSS)

    def test_remote_has_art_big_controls_and_rows(self):
        self.assertIn('<div class="rart" style="background-image:', APP)
        self.assertIn('data-a="rmt" data-v="back" aria-label="Back 10 seconds" ${busy}>−10</button>', APP)
        self.assertIn("rmtrow: (v) =>", APP)
        self.assertIn("S.rmtRow]);", APP)  # opening a row redraws the remote
        self.assertNotIn("rsleep", APP)
        self.assertIn(".sp2.rv .eps.strip{flex-direction:row", CSS.replace(".sp2.frpage .eps.strip,", ""))


if __name__ == "__main__":
    unittest.main()


class PlayOnTheOtherSide(unittest.TestCase):
    """Elias, 2026-11-10: a long-press on an episode also offers the place the
    phone's Play button doesn't use (Decision 71). Checked live with
    Work/phone-player/longpress.cjs at 390×844."""

    def test_the_menu_offers_the_other_place(self):
        i = APP.index("function cmOther(")
        body = APP[i:APP.index("\n}\n", i)]
        self.assertIn("awayDevice() && !S.settings?.android ? playWhere() : null", body)  # no PC in the Android app
        self.assertIn('to === "pc" ? "Play on the PC" : "Play on this phone"', body)
        self.assertIn('`data-where="${to}"`', body)

    def test_the_android_app_always_plays_here(self):
        # Decision 88: no PC to ask about, and no Play button choice in Settings.
        self.assertIn('const playWhere = () => { if (S.settings?.android) return "phone";', APP)
        self.assertIn("const here = awayDevice() && !S.settings?.android ? ", APP)

    def test_every_place_an_episode_starts(self):
        # Episode menus, Home's Continue watching, and every other Play button.
        self.assertIn("if (playable) rows.push(cmOther(id));", APP)
        self.assertIn('"↵") + cmOther(ep.id) : ""}${cmRow("open"', APP)
        self.assertIn('const pb = e.target.closest("[data-a=play][data-v], [data-a=deckplay][data-v]");', APP)
        self.assertIn("${cmOther(id)}`;", APP[APP.index("function playMenu("):])

    def test_it_plays_there_without_asking(self):
        self.assertIn("playon: (v, t) => play(v, 0, undefined, false, undefined, undefined, t.dataset.where),", APP)
        self.assertRegex(APP, r"\n  phone: \['<rect ")

    def test_a_phone_with_a_pen_is_still_a_phone(self):
        # S23 Ultra: the S Pen makes (any-pointer:fine) true; only the main pointer counts.
        i = APP.index("const awayDevice = ")
        line = APP[i:APP.index("\n", i)]
        self.assertNotIn("any-pointer", line)
        self.assertIn('matchMedia("(pointer:coarse)").matches && matchMedia("(hover:none)").matches', line)

    def test_a_finger_hold_on_any_play_button(self):
        # Real phones send no contextmenu on a hold here: the touch long-press does it.
        i = APP.index('document.addEventListener("touchstart", (e) => {\n  clearTimeout(lp?.timer)')
        body = APP[i:APP.index("}, 480) };", i)]
        self.assertIn('const pb = !ep && !cw && t.closest?.("[data-a=play][data-v], [data-a=deckplay][data-v]");', body)
        self.assertIn("playMenu(pb, x, y)", body)


class TabPager(unittest.TestCase):
    """The sideways swipe between Home · Library · Journal · Calendar (Elias,
    2026-11-12: "laggy, slow, doesn't look good"): the next tab is a copy
    beside this one, both follow the finger one to one, then the tab is built
    for real with no entrance."""

    def body(self, name):
        i = APP.index(f"function {name}(")
        return APP[i:APP.index("\n}\n", i)]

    def test_follows_the_finger_one_to_one(self):
        move = self.body("pagerMove")
        self.assertIn("const tx = to ? d :", move)  # no more half speed
        self.assertIn("pk.style.transform = `translate3d(${(tx - dir * W).toFixed(1)}px,0,0)`; pk.style.opacity = \"1\";", move)
        self.assertIn("pagerInd(p, k)", move)  # the dock's highlight moves with it
        self.assertIn("swSet(", move)  # one style write per frame

    def test_lands_without_an_entrance(self):
        end = self.body("pagerEnd")
        self.assertIn("nav(to, null, true)", end)
        self.assertNotIn("opacity .", end)  # nothing fades a whole page
        self.assertIn('S.navQuiet = !!quiet', self.body("nav"))
        self.assertIn('if (quiet) { go(); return calmIn($("#app")); }', self.body("nav"))
        self.assertIn('app.classList.toggle("nav-in", fresh && !S.navQuiet && !reduceMotion())', APP)
        # its own entrance animations jump to their end (the copy showed it finished)
        self.assertIn("a.finish()", self.body("calmIn"))
        # the backdrop and colour change with the slide, not after it
        self.assertIn("ambient();", end)

    def test_lands_under_its_copy_until_drawn(self):
        # right after a swipe Chrome shows layers it hasn't drawn: the new tab
        # is built unseen under the copy, and shown only after it is drawn
        end = self.body("pagerEnd")
        self.assertIn('app.style.opacity = "0"; app.style.transform = "";\n    nav(to, null, true);', end)
        self.assertIn("requestAnimationFrame(() => setTimeout(show, PAGER_DRAW))", end)
        self.assertIn('addEventListener("touchstart", show, { capture: true, passive: true });', end)  # a touch shows it at once
        self.assertIn('app.style.opacity = ""; getComputedStyle(app).opacity; app.style.transition = "";', end)  # no fade
        # the landing's own scroll back to where the tab was doesn't count as a scroll
        self.assertIn("Math.abs(scrollY - y0) > 2 && show()", end)

    def test_body_marks_change_with_the_slide(self):
        # Home's art hides the backdrop and the top bar darkens once scrolled:
        # set at the release from the copy, not after the landing (a white flash on Home)
        end = self.body("pagerEnd")
        self.assertIn("pageMarks(pk.firstChild)", end)
        self.assertIn('b.classList.toggle("sc", sy > 8); b.classList.toggle("sc2", sy > 84);', end)
        self.assertIn("el._sy = sy;", self.body("peekFresh"))
        self.assertIn('function pageMarks(app = $("#app"))', APP)

    def test_copy_loads_its_first_screen(self):
        # its posters load before the swipe, not during it
        self.assertIn('i.loading = "eager"', self.body("peekFresh"))

    def test_copies_are_built_ahead_and_parked(self):
        self.assertIn("peekFresh(to);", self.body("pagerStale"))
        self.assertIn("document.hidden", self.body("pagerStale"))  # never while the app is away
        self.assertIn("if (el._sig !== sig)", self.body("peekFresh"))  # rebuilt only when it changed
        self.assertIn(".tpeek{position:fixed;inset:0;overflow:hidden;pointer-events:none;will-change:transform,opacity;contain:strict;opacity:.004}", CSS)  # kept drawn: at 0 or off screen it came in as holes
        self.assertIn(".tpeek *,.tpeek *::before,.tpeek *::after{animation:none!important;transition:none!important}", CSS)
        self.assertIn('el.inert = true', self.body("peekFresh"))

    def test_copies_do_not_count_as_the_page(self):
        self.assertIn("app.querySelector(sel)", self.body("pageMarks"))
        self.assertIn("#app .lib2 .filters,.tpeek .lib2 .filters{display:none}", CSS)
        self.assertIn('if (still) { w.innerHTML = cards.slice(0, 24).join("") || empty; return; }', APP)
