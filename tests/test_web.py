"""TODO 48 / 60: the Decision 38 appearance + shell, the genre filter, the home airing
countdown and the Hiatus/Cancelled airing filters stay in the UI. The pure
parts of web/app.js (the @pure / @lists / @lib regions) run under node, and
so do the node:test scripts next to this file (*.cjs)."""

import json
import re
import shutil
import subprocess
import unittest
from unittest import mock

from tests import support
from animehub import anilist

WEB = support.ROOT / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def region(name):
    m = re.search(r"/\* @%s:start \*/(.*?)/\* @%s:end \*/" % (name, name), APP, re.S)
    assert m, name
    return m.group(1)


def run_js(body, store=None):
    """Evaluate `body` after the app's pure regions; returns its JSON output."""
    src = f"""
const store = {json.dumps(store or {})};
const lsGet = (k, d) => (k in store ? store[k] : d);
const S = {{ lib: {{ tab: "ALL", src: "all", fmt: "all", air: "all", year: "all", score: "all", sort: "updated", genres: [] }}, data: null }};
{region("pure")}
{region("lists")}
{region("lib")}
{region("crumbs")}
const srcLocked = () => false;
console.log(JSON.stringify((() => {{ {body} }})()));
"""
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=20)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


ENTRIES = [
    {"id": 1, "status": "CURRENT", "airing": "RELEASING", "genres": ["Action", "Drama"]},
    {"id": 2, "status": "CURRENT", "airing": "FINISHED", "genres": ["Action"]},
    {"id": 3, "status": "COMPLETED", "airing": "HIATUS", "genres": ["Drama"]},
    {"id": 4, "status": "PLANNING", "airing": "CANCELLED", "genres": []},
]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Frontend(unittest.TestCase):
    def filtered(self, **lib):
        return run_js(f"Object.assign(S.lib, {json.dumps(lib)});"
                      f"return libList({{ entries: {json.dumps(ENTRIES)} }}).map((e) => e.id).sort();")

    def test_genre_filter_needs_every_pick(self):
        self.assertEqual(self.filtered(genres=["Action"]), [1, 2])
        self.assertEqual(self.filtered(genres=["Action", "Drama"]), [1])
        self.assertEqual(self.filtered(genres=[]), [1, 2, 3, 4])

    def test_genre_tray_counts_the_open_tab(self):
        m = json.dumps({"entries": ENTRIES})
        self.assertEqual(run_js(f"return genreCounts({m});"), [["Action", 2], ["Drama", 2]])
        self.assertEqual(run_js(f"S.lib.tab = 'CURRENT'; return genreCounts({m});"), [["Action", 2], ["Drama", 1]])

    def test_folder_crumbs_follow_the_os(self):
        """The folder picker's path buttons: from "/" on Linux, from the
        drive on Windows (TODO 74), each button walking to its folder."""
        crumbs = lambda path: run_js(
            f"return [...dirCrumbs({json.dumps(path)}).matchAll(/data-v=\"([^\"]*)\"|<b aria-current[^>]*>([^<]*)/g)]"
            ".map((m) => m[1] ?? '=' + m[2]);")
        self.assertEqual(crumbs("/home/max"), ["/", "/home", "=max"])
        self.assertEqual(crumbs(r"C:\Users\Max\Anime"), ["C:\\", r"C:\Users", r"C:\Users\Max", "=Anime"])
        self.assertEqual(crumbs("D:\\"), ["=D:"])

    def test_airing_filter_uses_anilist_values(self):
        self.assertEqual(run_js("return Object.keys(AIR);"),
                         ["RELEASING", "FINISHED", "NOT_YET_RELEASED", "HIATUS", "CANCELLED"])
        for air, ids in (("FINISHED", [2]), ("HIATUS", [3]), ("CANCELLED", [4])):
            self.assertEqual(self.filtered(air=air), ids, air)
        self.assertIn('fsel("air", "airing", [["all", "any"], ...Object.entries(AIR)])', APP)

    def test_season_tab_counts_every_episode(self):
        # TODO 7: the tab shows the entry's size, not what's left to watch
        slots = json.dumps([{"watched": True}, {"watched": False}, {"watched": False}])
        self.assertEqual(run_js(f"return entryCount({{ episodes: 12, slots: {slots} }});"), 12)
        self.assertEqual(run_js(f"return entryCount({{ episodes: null, aired: 5, slots: {slots} }});"), 5)
        self.assertEqual(run_js(f"return entryCount({{ episodes: null, aired: 0, slots: {slots} }});"), 3)
        self.assertEqual(run_js("return entryCount({ episodes: 1, slots: [{ watched: false }] });"), 0)
        self.assertIn("const n = entryCount(x);", APP)
        self.assertNotIn("!y.watched).length", APP)

    def test_countdown(self):
        now = 1_700_000_000_000
        self.assertEqual(run_js(f"return airText({{ number: 5, at: {now // 1000 + 90000} }}, {now});"), "E5 in 1d 1h")
        self.assertEqual(run_js(f"return airText({{ number: 5, in_seconds: 600 }}, {now});"), "E5 in 10 min")
        self.assertEqual(run_js(f"return airText({{ number: 5, at: {now // 1000 - 5} }}, {now});"), "E5 is out")
        self.assertIn('sec > 0 ? "in " + fmtIn(sec) : "out now"', APP)  # Home → This week (Decision 38)

    def test_appearance_prefs(self):
        # Decision 38 replaced the Decision 28 palettes with device preferences.
        self.assertEqual(run_js("return LOOKS;"), {"motion": ["full", "calm", "off"], "art": ["full", "soft", "off"], "bar": ["classic", "tabs", "line", "capsule", "island"]})
        self.assertEqual(run_js("return lookGet('bar');", {"bar": "island"}), "island")  # Settings → Appearance → Top bar
        self.assertIn('d.dataset.bar=g("bar")||"classic";', HTML)
        for bar in ("tabs", "line", "capsule", "island"):
            self.assertIn(":root[data-bar=%s]" % bar, CSS)
        self.assertEqual(run_js("return lookGet('motion');", {"motion": "calm"}), "calm")
        self.assertEqual(run_js("return lookGet('motion');", {"motion": "nope"}), "full")
        self.assertEqual(run_js("return lookGet('art');"), "full")
        self.assertEqual(run_js("return accentGet();"), "follow")
        self.assertEqual(run_js("return accentGet();", {"accent": "#9ccbe8"}), "#9ccbe8")
        self.assertEqual(run_js("return accentGet();", {"accent": "red"}), "follow")
        self.assertEqual(run_js("return grainGet();", {"grain": False}), False)
        self.assertNotIn("data-pal", CSS + HTML + APP)
        # Applied before the stylesheet loads: no flash of the wrong look.
        self.assertLess(HTML.index("dataset.motion"), HTML.index("style.css"))
        self.assertIn(":root[data-motion=off] *", CSS)
        self.assertIn(":root[data-art=off] .cv-blur", CSS)
        self.assertIn('data-a="look" data-k="accent"', APP)

    def test_accent_from_anilist_colour(self):
        # Privacy browsers block reading colours from images, so AniList's colour comes first.
        self.assertEqual(run_js("return hexAccent('#e4a15d');"), "hsl(30 72% 70%)")
        self.assertEqual(run_js("return hexAccent('#5d5d5d');"), None)  # grey: keep the default
        self.assertEqual(run_js("return hexAccent(null);"), None)
        self.assertIn("if (color) return set(hexAccent(color));", APP)
        self.assertIn("coverImage{extraLarge color}", (support.ROOT / "animehub" / "anilist.py").read_text(encoding="utf-8"))

    def test_fonts_ship_with_the_app(self):
        # No network at runtime: every @font-face points at /fonts/, and the files are there.
        urls = re.findall(r"url\((/fonts/[^)]+)\)", CSS)
        self.assertTrue(urls)
        for u in urls:
            self.assertTrue((WEB / u.lstrip("/")).is_file(), u)
        self.assertNotIn("fonts.googleapis", CSS + HTML)
        for fam in ("Hanken Grotesk", "JetBrains Mono", "Barlow Condensed"):
            self.assertIn(f'font-family:"{fam}"', CSS)
        for lic in ("OFL-hankengrotesk.txt", "OFL-jetbrainsmono.txt", "OFL-barlowcondensed.txt"):
            self.assertTrue((WEB / "fonts" / lic).is_file(), lic)

    def test_shell(self):
        # The art canvas, the top bar (tabs with number keys + settings icon) and the status line.
        for el in ('id="canvas"', 'class="cv-blur"', 'class="cv-art"', 'id="status"', 'id="nr"', 'id="setb"'):
            self.assertIn(el, HTML)
        self.assertEqual(run_js("return 1;"), 1)
        self.assertIn("const TABS = [", APP)
        self.assertIn("<kbd>${i + 1}</kbd>", APP)
        self.assertIn('if (e.key === ",") { e.preventDefault(); A.go("settings"); return; }', APP)
        self.assertIn("function paintCanvas()", APP)
        self.assertIn('swapLayer("cv-art", mode === "full" ? art?.sharp : null);', APP)


class ShowPage(unittest.TestCase):
    """TODO 60 step 2 (Decision 38): the show page — your entry as a row
    (score · watched · status · ♥), Edit entry as a dialog, actions with
    their keys, rare ones behind ⋯, the episode strip."""
    def test_layout_and_keys(self):
        fran = APP[APP.index("function Fran() {"):APP.index("function frMore(")]
        for bit in ('id="entrystats"', 'data-a="edit"', 'data-a="frmore"', 'kb("↵")', 'kb("E")', 'kb("D")', 'kb("⇧W")', 'class="eps strip"', 'id="airline"'):
            self.assertIn(bit, fran)
        self.assertNotIn('class="banner"', fran)  # the canvas shows the art now
        self.assertIn("nxt: NextDlg, entry: EntryDlg }[m.t]();", APP)
        self.assertIn("if (e.key === \"e\" && hit('[data-a=\"edit\"]')) return;", APP)
        self.assertIn('const entryHost = () => $("#entrybox") || $("#entrystats");', APP)

    def test_tile_keeps_art_node(self):
        # The art node lives inside .epa now; a watch patch must still keep it (no blanking).
        self.assertIn('const art = tile.querySelector(".epimg");', APP)
        self.assertIn('<div class="epa"><span class="epimg" data-bg=', APP)


class WholeScript(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_app_js_parses(self):
        # The other checks load regions of it; one bad line elsewhere blanks the app.
        out = subprocess.run(["node", "--check", str(support.ROOT / "web" / "app.js")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)


class WeekSection(unittest.TestCase):
    def test_calendar_context_only_states_facts(self):
        self.assertIn('const airTag = (x) => (x.start || x.ep === 1 ? "Premiere" : x.ep && x.ep === x.e.episodes ? "Finale" : "");', APP)
        self.assertIn("const airSeen = (x) => !x.start && (x.e.progress || 0) >= x.ep;", APP)
        self.assertIn('"Month"], ["week", "Week"]', APP)

    def test_week_page_is_reachable_from_home(self):
        self.assertIn('data-a="go" data-v="week">Calendar', APP)


class Toast(unittest.TestCase):
    def test_loading_popup_stays_until_mpv_answers(self):
        self.assertIn('toast(`Loading ${now.show} · ${now.label}`, "busy")', APP)
        self.assertIn('if (k !== "busy") toast.h = setTimeout', APP)
        self.assertIn("loadToast(now);", APP)

    def test_first_play_explains_enter(self):
        self.assertIn('S.settings?.play_tip_done === false', APP)
        self.assertIn('tip: TipDlg', APP)


class LibraryCards(unittest.TestCase):
    """TODO 128: each show gets its own card; franchises are a choice, and
    their cards sort by the entry they show."""
    ENTRIES = [{"id": 1, "franchise_id": 7, "title": "Chainsaw Man", "format": "TV", "score": 9, "updated": 5, "status": "COMPLETED"},
               {"id": 2, "franchise_id": 7, "title": "Reze Arc", "format": "MOVIE", "score": 10, "updated": 1, "status": "COMPLETED"},
               {"id": 3, "franchise_id": 8, "title": "Frieren", "format": "TV", "score": 9.5, "updated": 2, "status": "COMPLETED"}]

    def cards(self, group):
        return run_js(f"""
            S.data = {{ shows: [], spine: {{ entries: {json.dumps(self.ENTRIES)}, untracked: [] }} }};
            Object.assign(S.lib, {{ tab: "ALL", sort: "score", group: {json.dumps(group)} }});
            const m = libModel();
            return {{ cards: libList(m).map((e) => e.id), all: m.tabs.find((t) => t.k === "ALL").n }};""")

    def test_each_show_has_its_own_card(self):
        self.assertEqual(self.cards("all"), {"cards": [2, 3, 1], "all": 3})

    def test_franchise_card_sorts_by_the_entry_it_shows(self):
        # Chainsaw Man shows the series (9), so it can't sit before Frieren's 9.5.
        self.assertEqual(self.cards("fr"), {"cards": [3, 1], "all": 2})


class Library(unittest.TestCase):
    """TODO 60 step 4: list tabs + side places, glass filter chips, poster
    cards with source/score tags, Needs a look match cards, calm hover art."""
    def test_library_shape(self):
        self.assertIn('l: "On disk only"', APP)
        self.assertIn('l: "Needs a look"', APP)
        self.assertIn('<div class="match mc" data-show=', APP)
        self.assertIn('data-a="match" data-v="${esc(s.id)}">This one</button>', APP)
        # The background waits for a rest on the poster; the accent follows at once.
        self.assertIn('accentFrom(art.blur, art.color);', APP)
        self.assertIn('}, 450) };', APP)


class Journal(unittest.TestCase):
    """TODO 60 step 5: History + Stats are one page (Journal, key 3); the old
    #history / #stats links still open it."""
    def test_journal(self):
        self.assertIn('const TABS = [["home", "Home"], ["library", "Library"], ["history", "Journal"]];', APP)
        self.assertIn('const J_RANGES = [["7d", "This week"], ["1mo", "Month"], ["1y", "Year"], ["all", "All time"]];', APP)
        self.assertIn('if (!embed) return Hist();', APP)  # #stats lands on the Journal
        self.assertIn('<div class="jside">${most}${genres}${heat}${st.side}</div>', APP)  # all time in the same two columns
        self.assertIn('const VIEWS = ["home", "library", "history", "stats", "settings", "week"];', APP)


class SearchPalette(unittest.TestCase):
    """TODO 60 step 6: one search for library, AniList, settings (switch
    right there) and commands; Settings show one section at a time."""
    def test_palette(self):
        self.assertIn('["VPN in the status line", "online", "vpn status icon", "vpn_icon"]', APP)
        self.assertIn('data-a="srsw" data-v="${key}"', APP)
        self.assertIn('if (S.menu === "sr") srPreview(', APP)
        # the palette opens centred with its own line; the top bar only holds the button
        self.assertIn('function openSearch() { if (S.menu !== "sr") { S.menu = "sr"; ovOpen("sr", () => A.srclose()); paintNav(); } q.focus(); }', APP)
        self.assertIn('if (e.key === "/" && !inInput) { e.preventDefault(); openSearch(); return; }', APP)
        self.assertIn('<div id="pal" role="dialog" aria-label="Search">', HTML)
        self.assertIn('<button class="search" id="srb" data-a="sropen"', HTML)
        self.assertIn('const tabs = SET_TABS.filter(([k]) => k !== "phone" || !s.phone);', APP)
        self.assertIn('<nav class="snav">${tabs.map(', APP)
        self.assertIn("r.classList.add(\"flash\")", APP)


class Polish(unittest.TestCase):
    """Elias's notes on the redesign (2026-10-24)."""
    def test_dropdowns_use_the_apps_list(self):
        self.assertIn('<div id="ddm" role="listbox"></div>', HTML)
        self.assertIn('sel.dispatchEvent(new Event("change", { bubbles: true }));', APP)  # actions stay on the <select>

    def test_match_card_lengths(self):
        """The files' own lengths (played, else measured); "each" only when every file is known and agrees."""
        fn = re.search(r"function lensText\(eps\) \{.*?\n\}\n", APP, re.S).group(0)
        out = run_js(fn + """return [lensText([{duration: 1440}, {length: 1430}]), lensText([{length: 1440}, {}]),
            lensText([{length: 1320}, {length: 1440}]), lensText([{length: 6300}]), lensText([{}, {}])];""")
        self.assertEqual(out, ["24 min each", "24 min", "22–24 min", "1 h 45 min", ""])
        for spot in ("minsOf(ep.duration || ep.length)", "minsOf(x.duration || x.length, e.duration)", "const len = ep.duration || ep.length"):
            self.assertIn(spot, APP)

    def test_show_page_lists_studio_and_popularity(self):
        """Decision 10: AniList's read-only extras, a quiet line under the summary."""
        self.assertIn('<p class="mfacts" id="mediafacts"></p>', APP)
        for bit in ('["Studio", m.studios.join(", ")]', '["On lists", m.popularity.toLocaleString()]', '["Favourites", m.favourites.toLocaleString()]'):
            self.assertIn(bit, APP)

    def test_show_page_says_all_on_disk(self):
        self.assertIn('allDisk ? (local.length === 1 ? "on disk" : `all ${local.length} on disk`)', APP)

    def test_show_page_blurs_its_banner(self):
        self.assertIn("blur: e?.banner || p?.banner || e?.poster || p?.poster", APP)

    def test_range_value_sits_after_the_track(self):
        # A pill sizes to its value; the slider stays put under the finger.
        self.assertIn('<div class="rt">${inp(0, a, "from")}${inp(1, b, "to")}</div><b class="rv">', APP)
        self.assertIn("field-sizing:content", CSS)

    def test_library_hover_skips_the_colour_fade(self):
        self.assertIn('document.documentElement.classList.add("amb-now")', APP)
        self.assertIn("html.amb-now{transition:none}", CSS)

    def test_episode_menu(self):
        self.assertIn('res ? `Resume at ${fmtTime(ep.position)}` : "Play", "↵"', APP)
        self.assertIn('cmRow("playstart", id, "undo", "Play from the start")', APP)
        self.assertIn('cmRow("srcfix", "", "search", "Wrong episode? Find it")', APP)
        self.assertIn("if (fromStart) body.from_start = true;", APP)

    def test_rewatch_tiles_show_the_stop_point(self):
        self.assertIn("(!ep.watched || ep.position / ep.duration < 0.92)", APP)  # mirrors player.resume_args
        self.assertIn("watched: x.watched && !part", APP)

    def test_downloads_list(self):
        self.assertIn('j.status === "queued" ? "waiting · next in line"', APP)
        self.assertIn('data-a="dlclear">clear finished</button>', APP)

    def test_journal(self):
        # built on AniList activity when connected (what you watched anywhere), else this device
        self.assertIn('api("/api/journal")', APP)
        self.assertIn('"watched episode": "watch", "rewatched episode": "watch", completed: "finish"', APP)
        self.assertIn('if (e.kind === "watch" && g && g.kind === "watch" && g.day === key && g.key === e.key) { g.more.push(e); continue; }', APP)
        self.assertIn('${jBars(bars, "days")}', APP)  # the band's day bars, as before
        self.assertIn('const eps = kind === "watch" && a.from != null ? a.to - a.from + 1 : 0;', APP)  # only what AniList says, never estimated
        self.assertNotIn("e.mins", APP)
        self.assertIn('<div class="tlg">', APP)  # history as a two-column grid (uses the width)
        self.assertIn("const T = jTotals()", APP)  # an AniList journal first: the big numbers are your AniList totals
        self.assertIn("#status:before{", CSS)  # the bar isn't a backdrop root: its pop-ups blur what's behind them
        self.assertIn("align-items:center}", CSS.split(".jr2 .tabs button{", 1)[1].split("\n", 1)[0])

    def test_library_hover_fades_in_steps(self):
        self.assertIn("if (root.classList.contains(\"amb-now\")) return ambFade(c);", APP)

    def test_tour_lists_what_it_found(self):
        self.assertIn('<div class="sum2">${tourFound(s, a, n, ok)}</div>', APP)
        self.assertIn('`mpv${s.mpv_version ? " " + s.mpv_version : ""} found`', APP)

    def test_home_recommendations(self):
        self.assertIn('api("/api/recommendations")', APP)
        self.assertIn('<h2>Recommended for you</h2>', APP)
        self.assertIn('entrySave(+v, { status: "PLANNING" })', APP)


class Discovery(unittest.TestCase):
    """TODO 11 (Decision 9): one search bar over library + AniList; a hit off
    the lists opens its page, which says so and offers the add."""
    def test_search_and_add(self):
        self.assertIn('api("/api/match-candidates", { query: mine })', APP)  # AniList half of the search bar
        self.assertIn("catpick: (v) => {", APP)
        self.assertIn("openFranchise(x.id)", APP)
        self.assertIn("Not on your list yet.", APP)
        self.assertIn('eadd: (v, t) => { t.disabled = true; entryPatch({ status: "PLANNING" }); }', APP)


class NextSource(unittest.TestCase):
    """TODO 10: the now-playing chip names where the next episode comes
    from; the VPN chip follows /api/now's live status, not only reloads."""
    def test_next_chip_and_live_vpn(self):
        self.assertIn("${nextChip(now)}", APP)
        self.assertIn("next: now.next", APP)
        self.assertIn("S.data.vpn = now.vpn;", APP)
        self.assertIn("S.data?.vpn]);", APP)  # a VPN change repaints the nav
        self.assertIn('addEventListener("focus", () => pollSoon(0));', APP)
        self.assertIn("if (now.next_prompt) maybeNextPrompt(now.next_prompt);", APP)
        self.assertIn('data-a="nxstop">Stop here<kbd class="kb">esc</kbd></button>${go}', APP)
        self.assertIn('<button class="btn pri" data-a="nxgo">${noVpn ? "Stream anyway" : "Continue"}', APP)
        self.assertIn('skpBtns("nxgo", "nxno"', APP)  # with AniSkip on: Use / Without AniSkip (D39)
        self.assertIn('if (vpnMoved && now.vpn.active && S.modal?.t === "nxt") A.nxgo();', APP)  # starts by itself once a VPN is up
        self.assertIn("play(id, 0, undefined, true, undefined, true)", APP)  # the popup already showed the VPN state (also the AniSkip yes, D39)


class NextAiringAt(unittest.TestCase):
    def test_cache_keeps_airing_at(self):
        media = {"id": 7, "status": "RELEASING", "episodes": 12, "format": "TV", "genres": ["Drama"],
                 "startDate": {"year": 2026}, "title": {"english": "X"},
                 "nextAiringEpisode": {"episode": 5, "timeUntilAiring": 3600, "airingAt": 1_800_000_000}}
        data = {"data": {"User": {"name": "u", "statistics": {"anime": {"episodesWatched": 0, "minutesWatched": 0,
                                                                         "meanScore": 0, "count": 1}}},
                         "MediaListCollection": {"lists": [{"entries": [{"status": "CURRENT", "progress": 4,
                                                                         "media": media}]}]}}}
        cache = anilist.config.ANILIST_CACHE
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(cache.unlink, missing_ok=True)
        with mock.patch.object(anilist, "load_anilist_username", return_value="u"), \
             mock.patch.object(anilist, "anilist_request", return_value=data):
            out = anilist.anilist_user_data(force=True)
        self.assertEqual(out["entries"][0]["next_episode"], {"number": 5, "in_seconds": 3600, "at": 1_800_000_000})
        self.assertEqual(out["entries"][0]["genres"], ["Drama"])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class NodeChecks(unittest.TestCase):
    """The node:test scripts next to this file (*.cjs: home hero, episode
    tiles, shell zoom) — one subtest per script, so a new .cjs needs no
    wrapper."""
    def test_scripts(self):
        scripts = sorted((support.ROOT / "tests").glob("*.cjs"))
        self.assertTrue(scripts, "no .cjs checks found")
        for script in scripts:
            with self.subTest(script=script.name):
                out = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
