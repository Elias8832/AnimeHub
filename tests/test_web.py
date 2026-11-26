"""TODO 48 / 60: the Decision 38 appearance + shell, the genre filter, the home airing
countdown and the Hiatus/Cancelled airing filters stay in the UI. The pure
parts of web/app.js (the @pure / @lists / @lib regions) run under node, and
so do the node:test scripts next to this file (*.cjs)."""

import json
import re
import shutil
import subprocess
import time
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
    # Fed on stdin: as a `-e` argument the regions outgrow Windows' 32K command line.
    out = subprocess.run(["node", "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=20)
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

    def test_source_format_airing_take_several_picks(self):
        """Elias, 2026-11-12: two formats or two airing states at once; a show
        passes when it matches any pick. A single pick saved before still works."""
        self.assertEqual(self.filtered(air=["RELEASING", "HIATUS"]), [1, 3])
        self.assertEqual(self.filtered(air="FINISHED"), [2])
        self.assertEqual(self.filtered(air="all"), [1, 2, 3, 4])
        self.assertEqual(run_js("S.lib.fmt = ['TV', 'MOVIE']; return [fhas('fmt', 'TV'), fhas('fmt', 'OVA'), fval('fmt', FOPTS_T)];"
                                .replace("FOPTS_T", '[["all", "any"], ["TV", "TV"], ["MOVIE", "Movie"], ["OVA", "OVA"]]')),
                         [True, False, "TV, Movie"])
        self.assertEqual(run_js("S.lib.fmt = ['TV', 'MOVIE', 'OVA']; return fval('fmt', [['all', 'any']]);"), "3 picked")

    def test_phone_card_says_one_fact_for_its_list(self):
        """TODO 239: a phone card's line fits its list, from data only. Watching:
        what's next and how many are left (or behind, or the next airing);
        Planning: what it is; Completed: what and when; Dropped: where you stopped."""
        fact = lambda e, air=None: run_js(
            f"globalThis.airState = () => ({json.dumps(air or {})}); return phoneFact({json.dumps(e)});")
        self.assertEqual(fact({"status": "CURRENT", "progress": 4, "episodes": 12, "airing": "FINISHED"}), "E5 next · 8 left")
        self.assertEqual(fact({"status": "PAUSED", "progress": 6, "episodes": None}), "E7 next")
        self.assertEqual(fact({"status": "CURRENT", "progress": 12, "episodes": 12}), "all seen")
        airing = {"status": "CURRENT", "progress": 4, "episodes": 12, "airing": "RELEASING", "next_episode": {"number": 8, "in_seconds": 3600}}
        self.assertEqual(fact(airing, {"behind": 3, "next": {"ep": 8}}), "E5 next · 3 behind")
        self.assertEqual(fact(airing, {"behind": 0, "next": {"ep": 8}}), "E8 in 1h 0m")
        self.assertEqual(fact({"status": "PLANNING", "format": "TV", "episodes": 12}), "TV · 12 ep")
        self.assertEqual(fact({"status": "PLANNING", "format": "MOVIE", "episodes": 1}), "Movie")
        self.assertEqual(fact({"status": "PLANNING", "format": "ONA", "episodes": None}), "ONA")  # no count yet: none guessed
        self.assertEqual(fact({"status": "COMPLETED", "format": "TV", "year": 2019, "episodes": 12, "progress": 12}), "TV · 2019")
        self.assertEqual(fact({"status": "DROPPED", "progress": 3, "episodes": 12, "format": "TV"}), "stopped after E3")

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
        self.assertIn('air: [["all", "any"], ...Object.entries(AIR)] });', APP)

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
        self.assertEqual(run_js(f"return fmtIn(airsIn({{ number: 5, at: {now // 1000 + 90000} }}, {now}));"), "1d 1h")
        self.assertEqual(run_js(f"return fmtIn(airsIn({{ number: 5, in_seconds: 600 }}, {now}));"), "10 min")
        self.assertLess(run_js(f"return airsIn({{ number: 5, at: {now // 1000 - 5} }}, {now});"), 0)
        self.assertIn('t - now > 0 ? pre + fmtIn((t - now) / 1000) : "out now"', APP)  # Home → This week (TODO 178; tests/home_hero.cjs)

    def test_appearance_prefs(self):
        # Decision 38 replaced the Decision 28 palettes with device preferences.
        self.assertEqual(run_js("return LOOKS;"), {"motion": ["full", "calm", "off", "always"], "art": ["full", "soft", "off"], "bar": ["tabs", "classic", "line", "capsule", "island"],
                                                    "fan": ["cascade", "fan", "wide", "peek"], "glow": ["on", "pictures", "bars", "off"]})  # Playlist stacks (Decision 64); Glow (TODO 236)
        self.assertEqual(run_js("return lookGet('glow');"), "on")
        self.assertIn('d.dataset.glow=g("glow")||"on";', HTML)
        self.assertIn(':root:is([data-glow=bars],[data-glow=off]) .lib2 .card::before', CSS)
        self.assertEqual(run_js("return lookGet('fan');"), "cascade")  # Cascade is the default (Elias, 2026-11-13)
        self.assertEqual(run_js("return lookGet('fan');", {"fan": "peek"}), "peek")
        for fan in ("fan", "wide", "cascade", "peek"):
            self.assertIn('[data-plfan="%s"] .plbox:hover' % fan, CSS)
        self.assertEqual(run_js("return lookGet('bar');"), "tabs")  # Tabs is the default top bar
        self.assertEqual(run_js("return lookGet('bar');", {"bar": "island"}), "island")  # Settings → Appearance → Top bar
        self.assertIn('d.dataset.bar=g("bar")||"tabs";', HTML)
        for bar in ("tabs", "line", "capsule", "island"):
            self.assertIn(":root[data-bar=%s]" % bar, CSS)
        self.assertEqual(run_js("return lookGet('motion');", {"motion": "calm"}), "calm")
        self.assertEqual(run_js("return lookGet('motion');", {"motion": "nope"}), "full")
        self.assertEqual(run_js("return lookGet('motion');", {"motion": "always"}), "always")
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
        self.assertIn('pick("Accent colour", "look", "accent"', APP)  # ‹ value ›, saved by A.look (Decision 73)

    def test_accent_from_anilist_colour(self):
        # Privacy browsers block reading colours from images, so AniList's colour comes first.
        self.assertEqual(run_js("return hexAccent('#e4a15d');"), "hsl(30 72% 70%)")
        self.assertEqual(run_js("return hexAccent('#5d5d5d');"), None)  # grey: keep the default
        self.assertEqual(run_js("return hexAccent(null);"), None)
        self.assertIn("if (color) return set(hexAccent(color));", APP)
        self.assertIn("coverImage{extraLarge color}", (support.ROOT / "animehub" / "anilist.py").read_text(encoding="utf-8"))

    def test_mpv_accent_is_the_played_show(self):
        # Home's backdrop follows the up-next show; a Continue tile of another
        # show handed mpv the backdrop's colour instead of its own.
        self.assertIn("const accent = accentHex(ctx?.s);", APP)
        self.assertIn("hexAccent(showColor(show))", APP)

    def test_fonts_ship_with_the_app(self):
        # No network at runtime: every @font-face points at /fonts/, and the files are there.
        urls = re.findall(r"url\((/fonts/[^)]+)\)", CSS)
        self.assertTrue(urls)
        for u in urls:
            self.assertTrue((WEB / u.lstrip("/")).is_file(), u)
        self.assertNotIn("fonts.googleapis", CSS + HTML)
        for fam in ("Hanken Grotesk", "JetBrains Mono", "Barlow Condensed", "Anime Hub Digits"):
            self.assertIn(f'font-family:"{fam}"', CSS)
        for lic in ("OFL-hankengrotesk.txt", "OFL-jetbrainsmono.txt", "OFL-barlowcondensed.txt", "OFL-inter.txt"):
            self.assertTrue((WEB / "fonts" / lic).is_file(), lic)

    def test_number_font(self):
        # Settings → Appearance → Numbers (Decision 100): a digits-only family in front of the text font.
        out = run_js("""return [uiStack("default", "default"), uiStack("default", "text"), uiStack("default", "mono"),
            uiStack("serif", "default"), uiStack("system", "text"), uiStack("u:My.ttf", "u:Nums.ttf"),
            fontGet("num"), fontFace({id: "Nums.ttf", url: "/user-fonts/Nums.ttf"}, true)];""", {"font_num": "nope"})
        self.assertEqual(out[0], "")  # both default: the stylesheet's --ui (Inter's digits, Hanken's letters)
        self.assertEqual(out[1], '"Hanken Grotesk", system-ui, sans-serif')
        self.assertEqual(out[2], '"Anime Hub Digits Mono", "Hanken Grotesk", system-ui, sans-serif')
        self.assertTrue(out[3].startswith('"Anime Hub Digits", "Iowan Old Style"'))
        self.assertEqual(out[4], "system-ui, sans-serif")
        self.assertEqual(out[5], '"hubu-Nums_ttf-n", "hubu-My_ttf", "Hanken Grotesk", sans-serif')
        self.assertEqual(out[6], "default")  # an unknown pick falls back
        self.assertIn('font-family:"hubu-Nums_ttf-n"', out[7])
        self.assertIn("unicode-range:U+0030-0039", out[7])
        for fam in ("Anime Hub Digits", "Anime Hub Digits Mono", "Anime Hub Digits Condensed"):
            self.assertIn(f'font-family:"{fam}"', CSS)
        self.assertIn('--ui: "Anime Hub Digits", "Hanken Grotesk"', CSS)

    def test_shell(self):
        # The art canvas, the top bar (tabs, number keys, settings icon) and the status line.
        for el in ('id="canvas"', 'class="cv-blur"', 'class="cv-art"', 'id="status"', 'id="nr"', 'id="setb"'):
            self.assertIn(el, HTML)
        self.assertEqual(run_js("return 1;"), 1)
        self.assertIn("const TABS = [", APP)
        self.assertNotIn("<kbd>${i + 1}</kbd>", APP)  # no key hints in the top bar (TODO 236)
        self.assertIn("const tab = TABS[+e.key - 1];", APP)  # 1–4 still switch tabs
        self.assertIn('if (e.key === ",") { e.preventDefault(); A.go("settings"); return; }', APP)
        self.assertIn("function paintCanvas(art = canvasArt())", APP)
        self.assertIn('swapLayer("cv-art", mode === "full" ? art?.sharp : null);', APP)


class ShowPage(unittest.TestCase):
    """TODO 60 step 2 (Decision 38): the show page — your entry as a row
    (score · watched · status · ♥), Edit entry as a dialog, actions with
    their keys, rare ones behind ⋯, the episode strip."""
    def test_layout_and_keys(self):
        fran = APP[APP.index("function Fran() {"):APP.index("function frMore(")]
        for bit in ('id="entrystats"', 'id="entryacts"', 'data-a="frmore"', 'class="eps strip"', 'id="airline"', 'class="grp"'):
            self.assertIn(bit, fran)
        self.assertIn('data-a="edit"', APP[APP.index("function entryActs("):APP.index("function aheadLine(")])
        # Decision 66: no key hints on the buttons, the keys themselves stay.
        for key in ("if (e.key === \"d\" && hit('.cta [data-a=\"dlmany\"]')) return;", "if (e.key === \"W\" && hit('[data-a=\"frbulk\"]')) return;",
                    "hit('.cta [data-a=\"play\"]')) return;"):
            self.assertIn(key, APP)
        self.assertNotIn('class="banner"', fran)  # the canvas shows the art now
        self.assertIn("nxt: NextDlg, entry: EntryDlg, match: MatchDlg }[m.t]();", APP)
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

    def test_no_function_declared_twice(self):
        # A second top-level `function x` silently replaces the first everywhere:
        # mpv's hexAccent() did that to the show colour (AniList's hex → accent).
        names = re.findall(r"^(?:async )?function\*? ?(\w+)", APP, re.M)
        twice = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(twice, [])


class JournalKeys(unittest.TestCase):
    def test_streak_day_key_exists(self):
        # A rename took this away and the Journal hung on "Reading…", no error shown.
        self.assertRegex(APP, r"const dayKey = \(d\) =>")
        self.assertIn("dates.map(dayKey)", APP)

    def test_format_name_helper_exists(self):
        # An "unused code" cleanup took this away while a dozen views still called it:
        # Settings and other pages threw "fmtName is not defined" and some tabs stayed shut.
        self.assertRegex(APP, r"const fmtName = \(f\) =>")


class BrowseTab(unittest.TestCase):
    """TODO 260: AniList's catalog as a tab, in the Library's layout."""

    def test_five_lists_the_rest_are_sorts(self):
        # All-time popular and Top 100 were All anime in another order; Trending now stays a list (Elias)
        self.assertIn('const BR_LISTS = [["over", "Overview"], ["all", "All anime"], ["trending", "Trending now"], ["season", "This season"], ["next", "Next season"]];', APP)
        self.assertIn('const BR_OLD = { popular: "", top: "score" };', APP)  # an older save opens All anime in that order
        self.assertIn('const see = { trending: "trending", season: "season", top: "all:score" };', APP)
        self.assertIn('bs: S.view === "browse" ? S.br.sort : undefined,', APP)  # Back returns to the order you had

    def test_a_season_list_changes_with_its_filters(self):
        # Year and Season are the season list's own filters, not locked (Elias)
        self.assertIn('brRange("fyear", "Year", sea && [sea.year, sea.year]), brDrop("fseason", "Season", BR_SEASONS.map((s) => [s, cap(s)]), false, sea && [sea.season])', APP)
        self.assertIn('if (brSeasonList() && (k === "fyear" || k === "fseason")) return brSeaFiltered(k);', APP)
        self.assertIn("if (f.fseason && one) {", APP)  # one season of one year stays a season list; wider is All anime

    def test_a_range_takes_the_whole_track(self):
        # the 14px handles alone were too hard to catch: a press anywhere brings the nearer one (Library too)
        self.assertIn('const t = e.target.closest?.(".rng .rt");', APP)
        self.assertIn('d.ins[d.i].dispatchEvent(new Event("change", { bubbles: true }))', APP)
        self.assertIn(".rng .rt input::-webkit-slider-thumb{pointer-events:none}", CSS)
        self.assertIn(".brclr{margin:-6px 0 -6px auto;", CSS)  # Clear showing up doesn't push the sliders down

    def test_a_tab_between_library_and_journal(self):
        self.assertIn('["library", "Library"], ["browse", "Browse"], ["history", "Journal"]', APP)
        self.assertIn('browse: Browse,', APP)
        self.assertIn("#(library|browse|history|stats|settings|week)$", APP)  # #browse opens it

    def test_reads_the_catalog_route(self):
        self.assertIn('api("/api/catalog")', APP)
        self.assertIn('"/api/catalog?" + new URLSearchParams', APP)

    def test_filters_fold_down_in_the_side_list(self):
        # the Library's own fold-down filters (fdrop), never a popup (Elias, Library polish)
        self.assertIn('return fdrop("br" + k, label,', APP)
        self.assertIn('<aside class="lnav brnav">', APP)

    def test_a_failed_read_waits_for_try_again(self):
        # no retry loop: a failure stays until the button asks again
        self.assertIn("(!more && (c.page || c.err))", APP)
        self.assertIn("(o?.err && !o.data)", APP)

    def test_back_lands_where_you_were(self):
        self.assertIn('if (from === "browse") navKeep();', APP)
        self.assertIn('history.scrollRestoration = "manual"', APP)

    def test_browse_gaps_search_entities_show_extras_and_phone(self):
        # Search -> Browse (TODO 260)
        self.assertIn('data-a="brsearch"', APP)
        self.assertIn('function brOpenSearch(query)', APP)
        self.assertIn('id="brq"', APP)
        # Clickable voice actors, studios, genres, rankings & show-page extras
        self.assertIn('data-a="brperson"', APP)
        self.assertIn('data-a="brstudio"', APP)
        self.assertIn('data-a="brgenre"', APP)
        self.assertIn('data-a="brrank"', APP)
        self.assertIn('function aboutTrailer(mid)', APP)
        self.assertIn('function relatedHTML(p, e)', APP)
        self.assertIn('data-a="brsea"', APP)
        # Phone Browse bottom bar, sheets and responsive strips
        self.assertIn('function brBar()', APP)
        self.assertIn('function brFltSheet()', APP)
        self.assertIn('.brw.brphone .brrow', CSS)
        self.assertIn('.brw.brphone .wall.brwall', CSS)


class WeekSection(unittest.TestCase):
    def test_calendar_context_only_states_facts(self):
        self.assertIn('const airTag = (x) => (x.start || x.ep === 1 ? "Premiere" : x.ep && x.ep === x.e.episodes ? "Finale" : "");', APP)
        self.assertIn("const airSeen = (x) => !x.start && (x.e.progress || 0) >= x.ep;", APP)
        self.assertIn('"Month"], ["week", "Week"]', APP)

    def test_calendar_week_and_month_polish(self):
        """Empty days still read as days, today's rail marks now, one summary for both views."""
        self.assertIn('`<div class="airnone"><i></i></div>`', APP)  # a quiet empty slot, no plain text (a calendar mark drawn in CSS: sharp at 125 %)
        self.assertIn('<div class="airnow"><span>now ', APP)
        self.assertIn('<div class="sum airsum">${sum}</div>', APP)  # month and week alike
        self.assertIn('l.length ? "has" : ""', APP)  # empty month days sit back
        self.assertIn('${l.length === 1 ? air1(l[0]) : ""}', APP)  # one show: name and time beside the poster
        self.assertIn("@container airts (max-width:150px){", (support.ROOT / "web" / "style.css").read_text(encoding="utf-8"))  # narrow days drop the text
        css = (support.ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn("@container (max-width:58px){.airt em{", css)  # no cut "Premiere" on small posters

    def test_calendar_quieter_polish(self):
        """TODO 208: the arrows stay put, past days step back, the day panel sits on the glow."""
        css = (support.ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn('<h1 class="hd"><span>${title}</span>${sizers}</h1>', APP)  # hidden widest titles keep its width
        self.assertIn(".airhead .hd>[aria-hidden]{visibility:hidden}", css)
        self.assertIn(".aird.is-past:not(.is-out,.is-sel,:hover){opacity:", css)
        self.assertIn(".aircol.is-past:not(:hover){opacity:", css)
        side = css.split(".airside{", 1)[1].split("}", 1)[0]
        self.assertNotIn("backdrop-filter", side)  # no box behind the panel
        self.assertIn('<span class="airnav"><button class="i" data-a="airgo" data-v="-1"', APP)  # ‹ Today › in one pill
        self.assertIn('<span class="airtabs" role="group" aria-label="View">', APP)  # views look like the top bar's tabs
        self.assertIn(".airtabs button.on{color:var(--ink);background:var(--pick)}", css)

    def test_keys_sheet(self):
        """? opens a full-screen keys sheet (Decision 84): this page first, then everywhere, then the player."""
        self.assertIn('<div id="keys" role="dialog" aria-label="Keys"></div>', HTML)
        self.assertIn("#keys{position:fixed;inset:0;", CSS)
        self.assertIn('keyGroup(`here · ${esc(where)}`, here, " acc")', APP)
        # this page's keys come from the buttons really on screen, not a fixed list
        self.assertIn("const on = (sel) => { const b = $(`#app ${sel}`); return b && !b.disabled ? b : null; };", APP)
        # the player column shows your own mpv keys, as caps you press
        self.assertIn("[l, mpvCaps(m.keys[a])]", APP)
        self.assertIn('data-a="setgo" data-t="player"', APP)
        self.assertIn('if (inInput || S.menu === "keys") return;', APP)  # no moving around behind the sheet
        self.assertNotIn("keysPop", APP)

    def test_week_page_is_reachable_from_home(self):
        self.assertIn('data-a="go" data-v="week">${more ? `${more} more in the Calendar` : "Calendar"}', APP)

    def test_calendar_is_a_tab_everywhere(self):
        """Easy to miss behind a Home link, so it sits in both tab bars."""
        self.assertIn('["week", "Calendar"]];', APP)
        self.assertIn('["week", "Calendar", "cal"]];', APP)
        self.assertIn('["Home, Library, Browse, Journal, Calendar", ["1", "2", "3", "4", "5"]]', APP)
        self.assertIn('const order = ["home", "library", "history", "week"]', APP)
        self.assertIn('<b>See when your shows air</b>', APP)  # logged out: one clear way in
        self.assertNotIn('Connect AniList to see what airs next.', APP)


class Toast(unittest.TestCase):
    def test_loading_popup_stays_until_mpv_answers(self):
        self.assertIn('toast(`Loading ${now.show} · ${now.label}`, "busy")', APP)
        self.assertIn('if (k !== "busy") toast.h = setTimeout', APP)
        self.assertIn("loadToast(now);", APP)

    def test_first_play_explains_enter(self):
        self.assertIn('S.settings?.play_tip_done === false', APP)
        self.assertIn('tip: TipDlg', APP)


@unittest.skipUnless(shutil.which("node"), "node not installed")
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

    def test_average_score_sorts_and_filters(self):
        entries = [{"id": 1, "title": "A", "average": 70, "status": "CURRENT"},
                   {"id": 2, "title": "B", "average": 88, "status": "CURRENT"},
                   {"id": 3, "title": "No votes yet", "average": None, "status": "CURRENT"}]
        ids = lambda lib: run_js(f"""
            S.data = {{ shows: [], spine: {{ entries: {json.dumps(entries)}, untracked: [] }} }};
            Object.assign(S.lib, {{ tab: "ALL", group: "all" }}, {json.dumps(lib)});
            return libList(libModel()).map((e) => e.id);""")
        self.assertEqual(ids({"sort": "average"}), [2, 1, 3])  # no average goes last
        self.assertEqual(ids({"sort": "average", "average": [80, 100]}), [2])
        self.assertEqual(ids({"sort": "average", "average": [0, 75]}), [1])  # no average isn't a low one
        # The reverse button: lowest first, and no average still goes last.
        self.assertEqual(ids({"sort": "average", "average": "all", "rev": True}), [1, 2, 3])
        self.assertEqual(ids({"sort": "title", "rev": True}), [3, 2, 1])

    def test_popularity_sorts_and_formats(self):
        entries = [{"id": 1, "title": "A", "popularity": 120000, "status": "PLANNING"},
                   {"id": 2, "title": "B", "popularity": 950000, "status": "PLANNING"},
                   {"id": 3, "title": "No pop", "popularity": None, "status": "PLANNING"}]
        res = run_js(f"""
            S.data = {{ shows: [], spine: {{ entries: {json.dumps(entries)}, untracked: [] }} }};
            const ids = (lib) => {{ Object.assign(S.lib, {{ tab: "ALL", group: "all", rev: false }}, lib); return libList(libModel()).map((e) => e.id); }};
            return {{
                desc: ids({{ sort: "popularity" }}),
                asc: ids({{ sort: "popularity", rev: true }}),
                fmt: [850, 1000, 1540, 99950, 584123, 999500, 1240000].map(fmtPop),
            }};""")
        self.assertEqual(res["desc"], [2, 1, 3])  # most popular first, blanks last
        self.assertEqual(res["asc"], [1, 2, 3])  # least popular first, blanks still last
        self.assertEqual(res["fmt"], ["850", "1k", "1.5k", "100k", "584k", "1M", "1.2M"])
        self.assertIn('["popularity", "Popularity"]', APP)
        self.assertIn('popularity: ["Most popular first", "Least popular first"]', APP)

    def test_a_filter_change_replays_the_cards_rise_in(self):
        """TODO 210: every filter, the sort and the reverse button paint the
        wall with its opening animation; a range only once it's let go."""
        self.assertIn("filt: (v, t) => { S.lib[t.dataset.k] = v; saveLib(); if (t.dataset.k === \"group\") { S.fadeNext = true; return render(); } paintWall(true);", APP)
        self.assertIn("srev: () => { S.lib.rev = !S.lib.rev; saveLib(); paintWall(true);", APP)
        self.assertIn('if (e.target.closest?.(".rng input")) { clearTimeout(rngFrame); saveLib(); if (e.target.closest(".fsheet")) return fltRepaint(); paintWall(true);', APP)
        self.assertNotIn("saveLib(); paintWall(); syncFilters();", APP)


class Library(unittest.TestCase):
    """TODO 60 step 4: list tabs + side places, glass filter chips, poster
    cards with source/score tags, Needs a look match cards, calm hover art."""
    def test_library_shape(self):
        self.assertIn('l: "On disk only"', APP)
        self.assertIn('l: "Needs a look"', APP)
        self.assertIn('<button class="card mcard" style="--i:${Math.min(i, 24)}" data-a="mopen"', APP)
        self.assertIn('data-a="match" data-v="${esc(s.id)}"${m.busy ? " disabled" : ""}>Yes, that\'s it', APP)
        # The background waits for a rest on the poster; the accent follows at once.
        self.assertIn('accentFrom(art.blur, art.color);', APP)
        self.assertIn('}, 450) };', APP)


class LibrarySideList(unittest.TestCase):
    """Library polish (Elias picked mockup B, 2026-11-06): on a PC the lists,
    chores and genres sit in a side list; ranges hide behind chips; narrow
    windows and touch keep the tabs and the filter sheet."""
    def test_side_list(self):
        self.assertIn('<div class="libg">${isPhone() ? "" : libRail(m, sum, loading)}<div class="lmain">', APP)
        self.assertIn('head("On this PC")', APP)
        self.assertIn('data-a="rescan" title="Look for new files and fetch missing metadata">', APP)
        self.assertIn('${fdrop("year", cap("year"), frng("year"', APP)
        # the filters sit in the side list on a PC; genres open in place like them (Elias, 2026-11-08)
        self.assertIn('${rail ? "" : filterCtl()}', APP)
        self.assertIn('head("Filter") + `<div class="lflt"', APP)
        self.assertIn('return fdrop("genres", "Genres", `<div class="fchips lgen"', APP)
        # a genre picked in one place shows as picked in the other, and the row says how many
        self.assertIn('for (const b of $$(`[data-a=genre][data-v="${CSS.escape(v)}"]`))', APP)
        self.assertIn('gd.querySelector(".fdb b").textContent = genLabel();', APP)
        block = CSS[CSS.index("/* ───────── Library side list"):]
        self.assertIn("@media (min-width:1001px) and (not (pointer:coarse)){", block)
        # show/sort stay lit while their list is open, not by the focus a closing click leaves (Elias, 2026-11-07)
        self.assertIn('sel.closest(".fl")?.classList.add("ddon");', APP)
        self.assertIn('ddSel.closest(".fl")?.classList.toggle("ddon", false);', APP)
        self.assertIn(".lib2 .lmain .filters .fl.ddon{background:rgba(255,255,255,.06)}", block)
        self.assertNotIn(".lmain .filters .fl:focus-within", block)
        # a click in the side list neither replays the heading's rise nor scrolls the list back up
        self.assertIn(".lib2 .lmain .filters{padding:0;animation:none}", block)
        self.assertIn("#app.nav-in .lib2 .lmain .filters{animation:rise", block)
        self.assertIn("if (railTop) { const r = $(\".lnav\"); if (r) r.scrollTop = railTop; }", APP)
        # source / format / airing open in place as chips in the side list, no popup
        self.assertIn('const filterCtl = (cap = (t) => t) => { const o = FOPTS(); return `${fchoice("src", cap("source"), o.src, srcLocked())}', APP)
        # several can be picked (Elias, 2026-11-12); the list stays open on a phone too
        self.assertIn('const FMULTI = new Set(["src", "fmt", "air"]);', APP)
        self.assertIn('S.lib[k] = next.length ? next : "all";', APP)
        self.assertIn('(multi ? opts.filter(([v]) => v !== "all") : opts)', APP)  # nothing ticked = any, no "any" row
        self.assertIn('if (k === "group" || isPhone()) { fltRepaint(); return; }', APP)  # phones: the sheet stays on its page (TODO 239)
        self.assertIn(".lib2 .filters>.fdrop .fpanel{display:none;position:absolute;", CSS)
        self.assertIn('data-a="fpick" data-k="${k}"', APP)
        self.assertIn('<button class="fopt${on ? " on" : ""}"', APP)  # one option per row, not pills
        # the drop-down list: unified glass card (no redundant ddcap header), hung from a right-hand chip's right edge
        self.assertNotIn('<div class="ddcap">', APP)
        self.assertIn('if (right && !first) m.style.left = Math.max(10, box.right / z - m.offsetWidth) + "px";', APP)
        self.assertIn(".fdrop,.fpanel{display:contents}", block)  # the phone sheet shows the sliders themselves


class Journal(unittest.TestCase):
    """TODO 60 step 5: History + Stats are one page (Journal, key 3); the old
    #history / #stats links still open it."""
    def test_journal(self):
        self.assertIn('const TABS = [["home", "Home"], ["library", "Library"], ["browse", "Browse"], ["history", "Journal"], ["week", "Calendar"]];', APP)
        self.assertIn('const J_RANGES = [["7d", "This week"], ["1mo", "Month"], ["1y", "Year"], ["all", "All time"]];', APP)
        self.assertIn('if (!embed) return Hist();', APP)  # #stats lands on the Journal
        self.assertIn('<div class="jside">${most}${genres}${heat}${st.side}</div>', APP)  # all time in the same two columns
        self.assertIn('const VIEWS = ["home", "library", "browse", "history", "stats", "settings", "week"];', APP)


class SearchPalette(unittest.TestCase):
    """TODO 60 step 6: one search for library, AniList, settings (switch
    right there) and commands; Settings show one section at a time."""
    def test_palette(self):
        self.assertIn('["VPN in the status line", "privacy", "vpn status icon", "vpn_icon"]', APP)
        self.assertIn('data-a="srsw" data-v="${key}"', APP)
        self.assertIn('if (S.menu === "sr" && (keep != null || !touchSearch())) srPreview(', APP)  # touch: no keyboard ring until the arrows are used (TODO 238)
        # the palette opens centred with its own line; the top bar only holds the button
        self.assertIn('function openSearch() { if (S.menu !== "sr") { S.menu = "sr"; ovOpen("sr", () => A.srclose()); paintNav(); } q.focus(); }', APP)
        self.assertIn('if (e.key === "/" && !inInput) { e.preventDefault(); openSearch(); return; }', APP)
        self.assertIn('<div id="pal" role="dialog" aria-label="Search">', HTML)
        self.assertIn('<button class="search" id="srb" data-a="sropen"', HTML)
        self.assertIn('const tabs = SET_TABS.filter(([k]) => k !== "phone" || !s.phone || s.android)', APP)
        self.assertIn('<nav class="snav" aria-label="Settings sections">${isPhone() ? "" : setSearch()}${tabs.map(', APP)
        self.assertIn("r.classList.add(\"flash\")", APP)


class SettingsLayout(unittest.TestCase):
    """Decision 61, re-sorted in 73: ten plain sections, a find box on top."""
    def test_every_setting_points_at_a_section_that_exists(self):
        tabs = set(re.findall(r'\["(\w+)", "[^"]+", "(?:Watching|Your hub|Accounts|This PC)"\]', APP))
        self.assertEqual(tabs, {"player", "subs", "skipping", "online", "appearance", "library", "privacy", "anilist", "phone", "data"})
        index = APP[APP.index("const SETTINGS_INDEX = ["):APP.index("const SET_TABS")]
        used = set(re.findall(r'\["[^"]+", "(\w+)", "', index))
        self.assertTrue(used)
        self.assertEqual(used - tabs, set())

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_a_value_steps_both_ways_and_opens_a_list(self):
        # Decision 73: ‹ value › — the arrows wrap around, the middle is a <select> (the app's own list).
        code = APP[APP.index("function pick(label"):APP.index("const onoff = ")]
        esc = re.search(r"const esc = .*", APP).group(0)
        js = esc + "\nconst ico = (n) => n;\n" + code + '\nconsole.log(pick("Motion", "look", "motion", [["full", "Full"], ["calm", "Calm"], ["off", "Off"]], "full"));'
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20).stdout
        self.assertIn('data-a="look" data-k="motion" data-v="off" aria-label="Motion: previous"', out)
        self.assertIn('data-a="look" data-k="motion" data-v="calm" aria-label="Motion: next"', out)
        self.assertIn('<select class="pkv" data-a="look" data-k="motion" aria-label="Motion">', out)
        self.assertIn('<option value="full" selected>Full</option>', out)
        # an On/Off value saves through the same switch path as before
        self.assertIn('advset: (v, t) => { if (S.settings) setToggle(t.dataset.k, v === "on"); }', APP)
        # the list hangs under the whole ‹ value ›
        self.assertIn('(sel.closest(".fl") || sel.closest(".pk") || sel).getBoundingClientRect()', APP)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_folder_picker_finds_as_you_type(self):
        # Decision 73: the Settings picker narrows its folders as you type and says how many it shows.
        code = APP[APP.index("const dirName = "):APP.index("function dirRows(p)")]
        js = code + '\nconst p = { dirs: [{ name: "Blue Box" }, { name: "Attack on Titan" }, { name: "Perfect Blue" }], q: " blue " };'
        js += '\nconsole.log(JSON.stringify([dirShown(p).map((d) => d.name), dirCount(p), dirCount({ dirs: p.dirs }), dirName("/mnt/Storage/Anime/"), dirName("C:\\\\Anime\\\\Shows")]));'
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20).stdout
        self.assertEqual(json.loads(out), [["Blue Box", "Perfect Blue"], "2 of 3 folders", "3 folders", "Anime", "Shows"])
        self.assertIn('if (ctx !== "tour") return DirPickBig(ctx, p);', APP)  # the tour keeps its small picker
        for key in ('e.key === "Escape"', 'e.key === "ArrowDown"', 'e.key === "Enter" && inQ', 'e.key === "Backspace" && (!inQ || !t.value)'):
            self.assertIn(key, APP)

    def test_every_indexed_setting_is_a_real_row(self):
        # The find box and the command line point at rows by name: a renamed row must be renamed here too.
        index = APP[APP.index("const SETTINGS_INDEX = ["):APP.index("const SET_TABS")]
        rest = APP.replace(index, "")
        for name in re.findall(r'\["([^"]+)", "\w+", "', index):
            self.assertTrue(f'"{name}"' in rest or f">{name}<" in rest, name)  # a row's name, a group heading or a button
        self.assertNotIn("Sync ratings", APP)  # a row for something not built yet

    def test_no_notice_names_an_old_section(self):
        for old in ("Settings → Playback", "/ Playback", "Settings → mpv", "Data &amp; setup", "Data & setup"):
            self.assertNotIn(old, APP)

    def test_changing_a_setting_keeps_the_page(self):
        # A plain render() rebuilt the page and replayed its entrance on every switch.
        self.assertEqual(re.findall(r'S\.settings = r;[^\n]*render\(\)', APP), [])
        self.assertIn('  if (S.view === "settings") render(true);\n  if (S.modal?.t === "tour") paintModal();', APP)

    def test_a_switch_flips_from_what_it_shows(self):
        # mpv_marks lives in s.mpv.marks, so !S.settings[key] could only ever turn it on.
        self.assertIn('const on = t.getAttribute("aria-checked") !== "true";', APP)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_find_box(self):
        self.assertIn('id="setq"', APP)
        self.assertIn('if (e.key === "/" && !inInput && $("#setq"))', APP)
        # every word must match, in any order
        code = re.search(r"const setHit = .*", APP).group(0)
        out = subprocess.run(["node", "-e", code + 'console.log(JSON.stringify([setHit(["vpn","warn"],"Warn without VPN"),setHit(["vpn","zzz"],"Warn without VPN")]))'], capture_output=True, text=True, timeout=20)
        self.assertEqual(json.loads(out.stdout), [True, False])


class UsageNotices(unittest.TestCase):
    def test_hidden_buttons_stay_hidden(self):
        # .btn's display beat [hidden]: the remote showed Skip opening all episode (TODO 99).
        self.assertIn(".btn[hidden]{display:none}", CSS)

    def test_every_settings_section_opens_the_same(self):
        # TODO 105: one entrance on the section; none on rows, which only some sections have.
        self.assertIn('$("#app .scol")?.remove()', APP)
        self.assertNotIn("#app.nav-in .srow", CSS)

    def test_connect_asks_before_marking_watched(self):
        # TODO 117, Decision 48.
        self.assertIn("mark_watched: S.rec.mark && recEps() > 0", APP)


class Polish(unittest.TestCase):
    """Elias's notes on the redesign (2026-10-24)."""
    def test_dropdowns_use_the_apps_list(self):
        self.assertIn('<div id="ddm" role="listbox"></div>', HTML)
        self.assertIn('sel.dispatchEvent(new Event("change", { bubbles: true }));', APP)  # actions stay on the <select>

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_match_card_lengths(self):
        """The files' own lengths (played, else measured); "each" only when every file is known and agrees."""
        fn = re.search(r"function lensText\(eps\) \{.*?\n\}\n", APP, re.S).group(0)
        out = run_js(fn + """return [lensText([{duration: 1440}, {length: 1430}]), lensText([{length: 1440}, {}]),
            lensText([{length: 1320}, {length: 1440}]), lensText([{length: 6300}]), lensText([{}, {}])];""")
        self.assertEqual(out, ["24 min each", "24 min", "22–24 min", "1 h 45 min", ""])
        for spot in ("minsOf(ep.duration || ep.length)", "minsOf(x.duration || x.length, e.duration)", "const len = ep.duration || ep.length"):
            self.assertIn(spot, APP)

    def test_show_page_has_no_studio_line(self):
        """Decision 66 (Elias, 2026-11-04: the page looked overloaded): the
        Studio · On lists · Favourites line of Decision 10 is gone."""
        self.assertNotIn("mediafacts", APP)
        self.assertNotIn(".mfacts", CSS)

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
        self.assertIn('cmRow("playstart", id, "undo", "Play from the start", "B")', APP)
        self.assertIn('cmRow("srcfix", "", "search", local ? "Wrong online episodes?" : "Wrong episode?")', APP)
        self.assertIn("if (fromStart) body.from_start = true;", APP)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_episode_menu_disk_and_online_labels(self):
        """A slot on disk + online names the language on both online rows and
        clarifies that Wrong online episodes? fixes the online match, not disk."""
        cm_items = re.search(r"function cmItems\([^)]*\) \{.*?\n\}\n", APP, re.S).group(0)
        out = run_js("""
            const S = { data: {}, sel: null, settings: {} };
            const resumable = (ep) => !!ep.duration && ep.position / ep.duration > 0.01 && (!ep.watched || ep.position / ep.duration < 0.92);
            const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
            const esc = (s) => String(s);
            const selKey = () => "fr:1";
            const cmOther = () => "";
            const cmRow = (a, v, icon, label, key = "", extra = "", attrs = "") => ({ a, v, icon, label, key, attrs });
        """ + cm_items.replace('.map((g) => g.join("")).join(\'<div class="sep"></div>\')', '.flat().filter((r) => typeof r === "object")') + """
            const ep = { watched: true, position: 945, duration: 1485 };
            return {
                diskDub: cmItems({ id: "loc:11", ep, files: [{ label: "default" }], verId: "loc:11", online: true, onlineId: "stream:1:11", mode: "dub", other: "sub", local: true, dl: false, fix: true }),
                diskSub: cmItems({ id: "loc:11", ep, files: [{ label: "default" }], verId: "loc:11", online: true, onlineId: "stream:1:11", mode: "sub", other: "dub", local: true, dl: false, fix: true }),
                onlineOnly: cmItems({ id: "stream:1:11", ep, files: [], verId: null, online: true, onlineId: "stream:1:11", mode: "dub", other: "sub", local: false, dl: true, fix: true }),
            };
        """)
        labels = lambda rows: [(r["label"], r["key"], r["a"], r["attrs"]) for r in rows]
        self.assertIn(("Play dubbed online", "O", "playmode", 'data-m="dub"'), labels(out["diskDub"]))
        self.assertIn(("Play subbed online", "A", "playmode", 'data-m="sub"'), labels(out["diskDub"]))
        self.assertIn(("Wrong online episodes?", "", "srcfix", ""), labels(out["diskDub"]))
        self.assertIn(("Play subbed online", "O", "playmode", 'data-m="sub"'), labels(out["diskSub"]))
        self.assertIn(("Play dubbed online", "A", "playmode", 'data-m="dub"'), labels(out["diskSub"]))
        self.assertNotIn("Play online", [r["label"] for r in out["diskDub"]])
        self.assertIn(("Play subbed", "A", "playmode", 'data-m="sub"'), labels(out["onlineOnly"]))
        self.assertIn(("Wrong episode?", "", "srcfix", ""), labels(out["onlineOnly"]))

    def test_rewatch_tiles_show_the_stop_point(self):
        self.assertIn("(!ep.watched || ep.position / ep.duration < 0.92)", APP)  # mirrors player.resume_args
        self.assertIn("watched: done && !part", APP)
        self.assertEqual(APP.count('done && !part ? "watched" : ""'), 2)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_episode_menu_watched_and_progress_states(self):
        """A watched episode paused halfway behaves as in-progress in the menu:
        Mark as watched (clears the stop point back to watched) + Reset progress
        (unwatches), never Mark as unwatched; a finished episode hides Reset progress."""
        cm_items = re.search(r"function cmItems\([^)]*\) \{.*?\n\}\n", APP, re.S).group(0)
        out = run_js("""
            const S = { data: {}, sel: null, settings: {} };
            const resumable = (ep) => !!ep.duration && ep.position / ep.duration > 0.01 && (!ep.watched || ep.position / ep.duration < 0.92);
            const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
            const esc = (s) => String(s);
            const selKey = () => "fr:1";
            const cmOther = () => "";
            const cmRow = (a, v, icon, label, key = "", extra = "", attrs = "") => ({ a, v, icon, label, key, attrs });
        """ + cm_items.replace('.map((g) => g.join("")).join(\'<div class="sep"></div>\')', '.flat().filter((r) => typeof r === "object")') + """
            const call = (ep, extra = {}) => cmItems({ id: "loc:1", ep, files: [{ label: "default" }], verId: "loc:1", online: false, onlineId: null, mode: "sub", other: "dub", local: true, dl: false, fix: false, ...extra });
            return {
                watchedHalf: call({ watched: true, position: 685, duration: 1485 }),
                watchedDone: call({ watched: true, position: 1485, duration: 1485 }),
                watchedZero: call({ watched: true, position: 0, duration: 1485 }),
                unwatchedHalf: call({ watched: false, position: 685, duration: 1485 }),
                unwatchedZero: call({ watched: false, position: 0, duration: 1485 }),
                rewatchPending: call({ watched: true, position: 0, duration: 1485 }, { watched: false }),
            };
        """)
        labels = lambda rows: [(r["label"], r["a"], r["attrs"]) for r in rows]
        self.assertIn(("Resume at 11:25", "playep", ""), labels(out["watchedHalf"]))
        self.assertIn(("Mark as watched", "mark", 'data-w="true" data-warn="1"'), labels(out["watchedHalf"]))
        self.assertIn(("Reset progress", "reset", 'data-warn="1"'), labels(out["watchedHalf"]))
        self.assertNotIn("Mark as unwatched", [r["label"] for r in out["watchedHalf"]])
        for key in ("watchedDone", "watchedZero"):
            self.assertIn(("Mark as unwatched", "mark", 'data-w="false" data-warn="1"'), labels(out[key]))
            self.assertNotIn("Reset progress", [r["label"] for r in out[key]])
            self.assertNotIn("Mark as watched", [r["label"] for r in out[key]])
        self.assertIn(("Mark as watched", "mark", 'data-w="true" data-warn="1"'), labels(out["unwatchedHalf"]))
        self.assertIn(("Reset progress", "reset", 'data-warn="1"'), labels(out["unwatchedHalf"]))
        self.assertIn(("Mark as watched", "mark", 'data-w="true" data-warn="1"'), labels(out["unwatchedZero"]))
        self.assertNotIn("Reset progress", [r["label"] for r in out["unwatchedZero"]])
        self.assertIn(("Mark as watched", "mark", 'data-w="true" data-warn="1"'), labels(out["rewatchPending"]))
        self.assertNotIn("Reset progress", [r["label"] for r in out["rewatchPending"]])

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

    def test_tour_lets_you_pick_the_player(self):
        """Every look the hub offers has a drawing in the tour, and the phone
        page's filter still points at the phone page."""
        from animehub import settings
        for look in settings.PLAYER_LOOKS:
            self.assertIn(f"  {look}: () => `<div class=\"lkv {look}\">", APP)
        self.assertIn('["Pick your player", "", tourLooks(s)]', APP)
        self.assertIn("pages.filter((_, i) => i !== 5)", APP)
        self.assertIn('{ mpv_design: "on", mpv_look: v }', APP)
        # Elias (v0.5 Windows pass): no fullscreen/smooth page in the tour; Settings → mpv keeps both
        self.assertNotIn("tourMpv(", APP)
        self.assertNotIn('["Your mpv settings"', APP)

    def test_motion_always_ignores_the_system(self):
        """Elias (v0.5 Windows pass): Windows had Animation effects off, so
        nothing moved. Motion "Always" moves anyway: every reduced-motion rule
        and reduceMotion() leave it alone."""
        import re
        blocks = re.findall(r"@media \(prefers-reduced-motion:reduce\)\{(.*?)\}\}", CSS)
        self.assertEqual(len(blocks), 7)
        for block in blocks:
            for rule in block.split("}"):
                for sel in rule.split("{")[0].split(","):
                    if sel.strip():
                        self.assertTrue(sel.strip().startswith(":root:not([data-motion=always]) "), sel)
        self.assertIn('return m === "off" || (m !== "always" && matchMedia("(prefers-reduced-motion:reduce)").matches);', APP)
        self.assertEqual(APP.count("prefers-reduced-motion"), 2)  # reduceMotion() and the Motion row's words
        self.assertIn('["off", "Off"], ["always", "Always"]], "Motion")', APP)

    def test_tour_picks_the_banner_look(self):
        """Elias: Sharper banners in the tour is Off, Natural or Digital art, one pick."""
        fn = APP[APP.index("function tourTools("):APP.index("function tourFound(")]
        self.assertIn('pick("Sharper banners", "tourup", null, [["off", "Off"], ["natural", "Natural"], ["digital", "Digital art"]]', fn)
        self.assertIn('v === "off" ? { upscale_banners: false } : { upscale_banners: true, upscale_style: v }', APP)

    def test_tour_folder_says_what_it_found(self):
        """An empty folder isn't "a problem" (Elias, v0.5 Windows pass), and the
        state is read again after the library was: right after a pick it
        still said "unconfigured"."""
        fn = APP[APP.index("function folderLine("):APP.index("function tourLooks(")]
        self.assertIn('"No shows in it yet. New ones show up by themselves."', fn)
        self.assertIn('if (st !== "ok") return "Looking in this folder…";', fn)
        self.assertIn('${eps} episode${eps === 1 ? "" : "s"}', fn)  # "1 episodes" (v0.5 cold run)
        self.assertNotIn("There is a problem with this folder", APP)
        self.assertIn('srow("Library folder", folderLine(s, n, eps),', APP)
        self.assertIn('srow("Your files", folderLine(s, n),', APP)  # the Android tour
        for name in ("async function dirSet(", "async function extraAdd("):
            body = APP[APP.index(name):APP.index("\n}\n", APP.index(name))]
            self.assertLess(body.index("await refreshAndWatch();"), body.rindex("await loadSettings();"))

    def test_wide_settings_centring_leaves_dialogs_alone(self):
        """From 2400 px the Settings page centres; a dialog's body is .set2 too
        and shrank to its rows, centred (the tour on QHD at 100 %)."""
        self.assertIn("@media (min-width:2400px){\n  .set2:not(.dlb){max-width:1248px;margin-inline:auto}\n}", CSS)

    def test_a_tour_setting_does_not_reload_the_dialog(self):
        """A switch or card updates in place; only a new page animates."""
        self.assertIn('if (m.t === "tour" && !focus) morphHTML(dlg, body);', APP)
        self.assertIn("tourback: () => { S.tour.step--; paintModal(true); }", APP)
        self.assertIn("if (T.step < tourBlocks().length - 1) { T.step++; return paintModal(true); }", APP)

    def test_android_settings_have_no_pc_parts(self):
        """Decision 88: no mpv, library folder, desktop updates or tools on Android."""
        self.assertIn("if (s.android) return here + rc; // the Android app has no mpv", APP)  # here: Play button, once paired (TODO 263)
        self.assertIn('${s.android ? "" : `<div class="group"><h3>Subtitle look</h3>', APP)
        self.assertIn('${s.android ? "" : srow("Mark them on the seek bar"', APP)
        self.assertIn('if (s.android) return `<div class="group"><h3>Termux</h3>', APP)
        self.assertIn('library: s.android && !s.files_ok ? g("Your files", filesAsk())', APP)
        self.assertIn('DirPick("settings") + (s.android ? "" : lengthRow(s))', APP)  # no mpv to measure with
        self.assertIn('data: s.android ? g("Updates", updateRows(s))', APP)  # updates in the app (Decision 88)
        self.assertIn("if (S.settings?.android) { location.href = url; return; }", APP)

    def test_the_android_tour(self):
        """Decision 88: no folder, mpv or phone remote pages; Termux setup instead."""
        fn = APP[APP.index("function tourAndroid("):APP.index("function Tour() {")]
        self.assertIn("${termuxBox(s)}", fn)
        self.assertIn("tourSearch(),", fn)
        for pc_only in ("tourTools(", "tourLooks(", "bgRows("):
            self.assertNotIn(pc_only, fn)
        # own files: optional, and only after Android's file access
        self.assertIn('`<div class="path">${libPath("tour", s)}${dirBtn("tour")}</div>`, "wide") + DirPick("tour") : filesAsk()}', fn)
        self.assertIn(": filesAsk()}", fn)
        self.assertIn("if (s.android) return tourAndroid(T, s, a);", APP)

    def test_a_tour_pick_shows_before_it_saves(self):
        """The outline moves on click; a failed save puts the old pick back."""
        fn = APP[APP.index("function tourSave("):APP.index("function finishTour(")]
        self.assertLess(fn.index("apply(S.settings.mpv); paintModal();"), fn.index('api("/api/settings", body)'))
        self.assertIn("S.settings = was; paintModal();", fn)
        self.assertIn('t.setAttribute("aria-checked", String(on)); setToggle(v, on);', APP)

    def test_a_new_tour_page_eases_to_its_size(self):
        self.assertIn('dlg.animate([{ width: w0 + "px", height: h0 + "px" }, { width: w1 + "px", height: h1 + "px" }]', APP)

    def test_tour_explains_search(self):
        """Its own page, before the last one, and the phone page keeps its slot."""
        self.assertIn("    tourSearch(),\n    [\"You're set\",", APP)
        pages = APP[APP.index("  return tourPages(["):APP.index("tourSearch(),\n")]
        self.assertEqual(pages.count('\n    ["'), 6)  # TODO 226: a page is [title, note, body]
        self.assertTrue(pages.rstrip().endswith("${bgRows(s)}`],"))

    def test_tour_lists_what_it_found(self):
        self.assertIn('<div class="group"><h3>Found</h3>${tourFound(s, a, n, ok)}</div>', APP)
        self.assertIn('`mpv${s.mpv_version ? " " + s.mpv_version : ""} found`', APP)

    def test_home_recommendations(self):
        self.assertIn('api("/api/recommendations")', APP)
        self.assertIn('<h2>Recommended for you</h2>', APP)
        self.assertIn('entrySave(+v, { status: "PLANNING" })', APP)


class Discovery(unittest.TestCase):
    """TODO 11 (Decision 9): one search bar over library + AniList; a hit off
    the lists opens its page, which says so and offers the add."""
    def test_search_and_add(self):
        self.assertIn('api("/api/match-candidates", { query: mine, fuzzy: true })', APP)  # AniList half of the search bar
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
        self.assertIn('escBtn("nxstop", "Stop here") + go', APP)
        self.assertIn('<button class="btn pri" data-a="nxgo">${noVpn ? "Stream anyway" : "Continue"}', APP)
        self.assertIn('skpBtns("nxgo", "nxno"', APP)  # with AniSkip on: Use / Without AniSkip (D39)
        self.assertIn('if (vpnMoved && now.vpn.active && S.modal?.t === "nxt") A.nxgo();', APP)  # starts by itself once a VPN is up
        self.assertIn("play(id, 0, undefined, true, undefined, true)", APP)  # the popup already showed the VPN state (also the AniSkip yes, D39)


class NextAiringAt(unittest.TestCase):
    def test_cache_keeps_airing_at(self):
        media = {"id": 7, "status": "RELEASING", "averageScore": 81, "popularity": 250000, "episodes": 12,
                 "format": "TV", "genres": ["Drama"], "startDate": {"year": 2026}, "title": {"english": "X"},
                 "nextAiringEpisode": {"episode": 5, "timeUntilAiring": 3600, "airingAt": 1_800_000_000}}
        data = {"data": {"User": {"name": "u", "statistics": {"anime": {"episodesWatched": 0, "minutesWatched": 0,
                                                                         "meanScore": 0, "count": 1}}},
                         "MediaListCollection": {"lists": [{"entries": [{"status": "CURRENT", "progress": 4,
                                                                         "media": media}]}]}}}
        cache = anilist.config.ANILIST_CACHE
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(cache.unlink, missing_ok=True)
        with mock.patch.object(anilist, "load_anilist_username", return_value="u"), \
             mock.patch.object(anilist, "service", return_value="anilist"), \
             mock.patch.object(anilist, "anilist_request", return_value=data):
            out = anilist.anilist_user_data(force=True)
        self.assertEqual(out["entries"][0]["next_episode"], {"number": 5, "in_seconds": 3600, "at": 1_800_000_000})
        self.assertEqual(out["entries"][0]["genres"], ["Drama"])
        self.assertEqual(out["entries"][0]["average"], 81)
        self.assertEqual(out["entries"][0]["popularity"], 250000)
        # An older list cache whose entries lack popularity is not treated as fresh, so it refreshes right away.
        cache.write_text(json.dumps({"user": {"name": "u"}, "entries": [{"id": 7, "average": 81}], "_ts": time.time()}),
                         encoding="utf-8")
        self.assertIsNone(anilist._list_cache_fresh("anilist"))


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


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DeviceMarks(unittest.TestCase):
    """TODO 166: episode ticks are this device's own; AniList being further
    along is a small offer on the show page, not a setting."""
    def ahead(self, f, slots, pending=False, fmt="TV", service="AniList"):
        a = APP.index("function aheadLine(")
        code = APP[a:APP.index("\n}\n", a) + 3]
        js = ('const esc = (x) => String(x); const ico = (n) => `<i data-ico="${n}"></i>`;'
              + "const acct = () => %s;" % json.dumps(service) + code + 'console.log(JSON.stringify(aheadLine('
              + json.dumps(f) + "," + json.dumps(pending) + "," + json.dumps({"format": fmt, "slots": slots}) + ")))")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def slots(self, watched, n=6):
        return [{"number": i, "watched": i <= watched, "local": {"id": f"f{i}"} if i != 5 else None,
                 "online": None} for i in range(1, n + 1)]

    def test_offers_the_episodes_anilist_is_ahead_by(self):
        out = self.ahead({"progress": 4}, self.slots(1))
        self.assertIn("AniList is at ep 4", out)
        self.assertIn('data-v="[2,3,4]"', out)
        self.assertIn('class="lnk" data-a="frahead"', out)  # Decision 67: the line's own link, in the accent
        self.assertIn("catch up to ep 4", out)

    def test_an_episode_with_no_source_is_left_out(self):
        self.assertIn('data-v="[4,6]"', self.ahead({"progress": 6}, self.slots(3)))

    def test_quiet_when_in_step_or_not_from_anilist(self):
        self.assertEqual(self.ahead({"progress": 3}, self.slots(3)), "")
        self.assertEqual(self.ahead({"progress": 0}, self.slots(0)), "")
        self.assertEqual(self.ahead({"progress": 4, "local": True}, self.slots(0)), "")
        self.assertEqual(self.ahead({"progress": 4}, self.slots(0), pending=True), "")

    def test_movie(self):
        out = self.ahead({"progress": 1}, self.slots(0, 1), fmt="MOVIE")
        self.assertIn("watched on AniList", out)

    def test_lists_reload_the_page_when_they_arrive(self):
        """Decision 75 (Elias: switching left "still loading" up until a
        manual reload): /api/now's list stamp reloads the page, and the
        library's note shows the steps with a bar."""
        self.assertIn("if (!now.playing) listsWatch(now.lists);", APP)

    def load_state(self, loading, service="mal"):
        fn = re.search(r"function listLoadState\(\) \{.*?\n\}\n", APP, re.S).group(0)
        return run_js(f"S.ani = {{ service: {json.dumps(service)} }}; S.lists = {{ loading: {json.dumps(loading)} }};\n{fn}\nreturn listLoadState();")

    def test_list_loading_line_shows_the_real_count(self):
        """TODO 191 (Elias picked mockup A): while the lists load, the side
        list holds their place with a quiet line — the real count, no guesses."""
        t = self.load_state({"step": "match", "done": 100, "total": 200})
        self.assertEqual((t["what"], t["count"], t["pct"]), ("matching shows", "100 / 200", 50))
        self.assertIn("MyAnimeList", t["tip"])
        t = self.load_state({"step": "list", "done": None, "total": None}, service="anilist")
        self.assertEqual((t["what"], t["count"], t["pct"]), ("reading list", "", None))
        self.assertEqual(self.load_state(None)["what"], "loading")

    def test_list_loading_sits_where_the_lists_land(self):
        """TODO 191: placeholder rows in the side list, not a box over the page."""
        self.assertIn('<div class="lghost"', APP)
        self.assertNotIn("note-bar listnote", APP)
        self.assertIn("for (const n of $$(\".lload:not(.mload)\")) loadPatch(n, listLoadState());", APP)
        self.assertIn(".lnav>button[data-land]", CSS)

    def meta_state(self, d):
        fn = re.search(r"function metaLoadState\(d = S.data\) \{.*?\n\}\n", APP, re.S).group(0)
        return run_js(f"{fn}\nreturn metaLoadState({json.dumps(d)});")

    def test_metadata_line_shows_the_real_count(self):
        """TODO 199 (Elias: same as the lists' line): the metadata pass shows
        done / total with a bar, and the show it is on under the bar."""
        t = self.meta_state({"meta_pending": 1, "meta_total": 4, "meta_current": "Mugen Train"})
        self.assertEqual((t["count"], t["pct"], t["now"]), ("3 / 4", 75, "Mugen Train"))
        t = self.meta_state({"meta_pending": 2})  # an older hub without the total
        self.assertEqual((t["count"], t["pct"]), ("0 / 2", 0))

    def test_metadata_line_sits_in_the_side_list(self):
        """TODO 199: no bar over the page; on the PC it sits beside the grid's title."""
        self.assertNotIn("note-bar metanote", APP)
        self.assertIn('`<div class="filters">${lhead}${mrow}', APP)  # beside the grid's title: the side list no longer jumps (Elias)
        self.assertNotIn("libRail(m, sum, loading, fetching)", APP)
        self.assertIn('<div class="lload mload inl">', APP)

    def test_names_the_account_service(self):
        """Decision 75: with MyAnimeList as the account, the line names it."""
        self.assertIn("MyAnimeList is at ep 4", self.ahead({"progress": 4}, self.slots(1), service="MyAnimeList"))

    def test_movie_button(self):
        self.assertIn("mark watched here", self.ahead({"progress": 1}, self.slots(0, 1), fmt="MOVIE"))

    def test_watched_marks_setting_is_gone(self):
        self.assertNotIn("Watched marks from AniList", APP)
        self.assertNotIn("apply_anilist_watched", (support.ROOT / "animehub" / "library.py").read_text(encoding="utf-8"))

    def test_one_word_for_where_data_lives(self):
        # TODO 166: "on this PC" / "from AniList", not device/local variants.
        for old in ("this device", "kept local", "keep local", "keep it local", "Local wins", "local watch state", "<span>Local</span>"):
            self.assertNotIn(old, APP)

    def test_history_is_always_this_device(self):
        self.assertIn('<b>History</b><span>on this PC</span>', APP)
        self.assertIn("const hev = jDevEvents()", APP)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ShowPagePolish(unittest.TestCase):
    """The show page rating pass (2026-11-04): movie facts, one entry with
    no tab, season tabs on one row, specials named and matched."""
    def fn(self, name):
        a = APP.index(f"function {name}(")
        return APP[a:APP.index("\n}\n", a) + 3]

    def guess(self, page, other):
        a = APP.index("const subDir = ")
        js = (APP[a:APP.index("\n", a) + 1] + self.fn("otherGuess")
              + f"const g = otherGuess({json.dumps(page)}, {json.dumps(other)}); console.log(JSON.stringify(g && g.id));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def other(self, sub, n=3):
        return {"episodes": [{"rel": f"Attack on Titan/Specials/{sub}E0{i}.mp4"} for i in range(1, n + 1)]}

    def test_specials_subfolder_names_one_entry(self):
        page = {"entries": [
            {"id": 1, "label": "Season 1", "format_label": "TV", "episodes": 25, "slots": []},
            {"id": 2, "label": "OVA", "format_label": "OVA", "episodes": 3, "slots": []},
            {"id": 3, "label": "Lost Girls", "format_label": "OVA", "episodes": 3, "slots": []}]}
        self.assertEqual(self.guess(page, self.other("OVA/")), 2)  # the label wins over two OVA formats
        self.assertIsNone(self.guess(page, self.other("")))  # no subfolder: no guess
        self.assertIsNone(self.guess(page, self.other("OVA/", 2)))  # the count must fit
        del page["entries"][1]
        self.assertIsNone(self.guess(page, {"episodes": [{"rel": "A/Specials/Extras/E01.mp4"}]}))

    def fran(self):
        a = APP.index("function Fran()")
        return APP[a:APP.index("\n}\n", a)]

    def test_movie_states_its_length(self):
        body = self.fran()
        self.assertIn('movie ? (e.duration ? `${e.duration} min` : "")', body)
        self.assertIn('episode${e.episodes === 1 ? "" : "s"}', body)
        self.assertIn('p.entries.length < 2 || byOrder ? ""', body)  # one entry: a heading, no lone tab

    def test_watch_order_picks_the_season(self):
        """Decision 66: the seasons showed twice (tabs and the watch order);
        the watch order picks, tabs only when it can't reach every entry."""
        body = self.fran()
        self.assertIn("p.entries.every((x) => ordIds.has(x.id))", body)
        self.assertNotIn('data-a="frorder"', body)  # the season dropdown replaced the "watch order" link
        self.assertIn('$("#app .frpage > .eps-head")?.scrollIntoView(', APP)
        self.assertNotIn("${kb(", body[body.index('<div class="cta">'):])  # no key hints on the buttons

    def test_season_heading_is_a_dropdown(self):
        """Decision 66 (2026-11-08): the episode row's heading opens a list of
        every entry in watch order; the real select lies invisible over it."""
        body = self.fran()
        self.assertIn("byOrder ? frPick(p, e)", body)
        a = APP.index("function frPick(")
        pick = APP[a:APP.index("\n}\n", a)]
        self.assertIn('<select data-a="frsel" aria-label="Season">', pick)
        self.assertIn('<span aria-hidden="true">${esc(e.label)}</span>', pick)
        self.assertIn("(at.get(a.id) ?? 1e9) - (at.get(b.id) ?? 1e9)", pick)  # watch order first
        self.assertIn('o.dataset.r ? `<small class="r">', APP)  # the count on the right of the app's own list
        self.assertIn(".frpick select{position:absolute;inset:0;width:100%;height:100%;opacity:0", CSS)
        self.assertNotIn(".frpick select option", CSS)  # an option's own font made Chrome cut the name ("Seas…")

    def test_dropdown_rides_along_and_fits(self):
        """Elias (2026-11-08): the list closed on scroll, and Attack on Titan's
        15 entries squashed the rows and ran off the screen."""
        self.assertIn("if (ddSel.isConnected) ddPos(ddSel, $(\"#ddm\")); else ddClose(false);", APP)
        self.assertIn("maxHeight: Math.max(120, Math.min(340, room / z))", APP)
        self.assertIn("#ddm button{flex:none;", CSS)  # rows keep their height; the list scrolls
        self.assertIn("#ddm.open{display:flex;overscroll-behavior:contain;", CSS)

    def test_season_tabs_stay_one_row(self):
        self.assertIn(".sp2 .rels{flex-wrap:nowrap;max-width:100%;overflow-x:auto", CSS)
        self.assertIn('root.querySelectorAll(".sp2 .rels")', APP)

    def test_art_dims_when_scrolled(self):
        self.assertIn("body.pg-fr .cv-wash:after{opacity:var(--sd,0)}", CSS)  # Decision 67: fades with the scroll
        self.assertIn('w.style.setProperty("--sd", S.view === "fr"', APP)
        self.assertIn("strips(); artDim();", APP)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ShowPageRedo(unittest.TestCase):
    """Decision 67: the show page redo — top line, your list as a line + bar,
    Play and one action pill, About at the end."""
    def run_fn(self, name, call, pre=""):
        a = APP.index(f"function {name}(")
        js = ('const esc = (x) => String(x); const ico = (n) => `<i data-ico="${n}"></i>`; const S = { view: "fr" };'
              'const statusLabel = (s) => ({ CURRENT: "Watching", COMPLETED: "Completed", PLANNING: "Planning" })[s] || s;'
              + pre + APP[a:APP.index("\n}\n", a) + 3] + f"console.log(JSON.stringify({call}));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_actions_follow_the_entry(self):
        pc, phone = "const isPhone = () => false;", "const isPhone = () => true;"
        off = self.run_fn("entryActs", 'entryActs({ status: null }, false)', pc)
        self.assertIn('data-a="eadd"', off)
        self.assertIn("Add to Planning", off)
        self.assertNotIn('data-a="edit"', off)
        self.assertIn("<span>Planning</span>", self.run_fn("entryActs", 'entryActs({ status: null }, false)', phone))  # the phone's row of short words (TODO 177)
        on = self.run_fn("entryActs", 'entryActs({ status: "CURRENT", favourite: true }, false)', pc)
        self.assertIn('data-a="edit"', on)
        self.assertNotIn('data-a="efav"', on)  # Elias 2026-11-08: no heart on the page; it lives in the editor
        self.assertIn('data-a="edit"', self.run_fn("entryActs", "entryActs(null, false)", pc))  # before the read: Edit

    def test_favourite_pill(self):
        """The heart is a pill that says what it is (Elias picked B's pill in A's place, 2026-11-11)."""
        a = APP.index("const favWord")
        js = ('const ico = (n) => `<i data-ico="${n}"></i>`;' + APP[a:APP.index("function favSet", a)]
              + 'console.log(JSON.stringify([favPill(true, \'data-a="efav"\'), favPill(false, \'id="dn-fav"\')]));')
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        on, off = json.loads(out.stdout)
        self.assertIn('class="heart on"', on)
        self.assertIn('aria-pressed="true"', on)
        self.assertIn("<span>Favourite</span>", on)
        self.assertIn('data-a="efav"', on)
        self.assertIn("<span>Add to favourites</span>", off)
        self.assertIn('aria-pressed="false"', off)
        self.assertIn("favPill(false, 'id=\"dn-fav\" data-a=\"dnfav\"')", APP)  # the "you finished" dialog uses it too

    def test_tenths_score_slider(self):
        """Decimal 10-point: pills plus a slider in tenths, so 1.7 can be scored (Elias 2026-11-10)."""
        pre = ('const scoreTxt = (v) => v ? (Math.round(v * 10) / 10).toFixed(1) : "–";'
               'const sslFill = (v) => v ? `${Math.round(((v - 1) / 9) * 10000) / 100}%` : "0%";')
        sl = lambda sc: self.run_fn("scoreSlider", f'scoreSlider("escore", {sc})', pre)
        out = sl(1.7)
        self.assertIn('step="0.1"', out)
        self.assertIn('min="1" max="10"', out)  # 1 sits under pill 1, 10 under pill 10
        self.assertIn('value="1.7"', out)
        self.assertIn("--b:7.78%", out)  # the filled part of the track
        self.assertIn("--b:100%", sl(10))
        self.assertIn('<b class="ssv">1.7</b>', out)
        self.assertIn('<b class="ssv">8.0</b>', sl(8))
        self.assertIn('<b class="ssv">–</b>', sl(0))  # no score yet
        self.assertIn('scoreSlider("escore", f.score)', APP)  # the editor shows it
        self.assertIn('scoreSlider("dnfine", 0)', APP)  # and the "you finished" dialog
        self.assertNotIn("unless a .5 is set", APP)  # a .5 no longer swaps the pills for a number field

    def stats(self, f, fe, tablet=True):
        pre = ("const frEntry = () => (" + json.dumps(fe) + "); const showById = () => null; const epTotal = () => 0; const epWatched = () => 0;"
               f"function aheadLine() {{ return ''; }} const isPhone = () => false; const isTabletP = () => {str(tablet).lower()};")
        return self.run_fn("entryStats", "entryStats(" + json.dumps(f) + ", false)", pre)

    def test_your_list_line_and_bar(self):
        fe = {"episodes": 12, "slots": [{"watched": False}] * 12}
        out = self.stats({"status": "CURRENT", "score": 8, "progress": 5, "started_at": "2026-10-11", "media": {"episodes": 12}}, fe)
        self.assertIn('<button class="mline" data-a="edit"', out)
        self.assertIn("<b>watching</b>", out)
        self.assertIn("since 2026-10-11", out)
        self.assertEqual(out.count('class="s"'), 5)  # portrait tablets: Home's ticks up to 40 episodes
        self.assertIn("5/12", out)
        pc = self.stats({"status": "CURRENT", "progress": 5, "media": {"episodes": 12}}, fe, tablet=False)
        self.assertIn('<span class="tline"><i style="width:41.7%">', pc)  # PC and phones: one plain bar like Home's banner (TODO 236)
        self.assertIn("5/12", pc)
        long = self.stats({"status": "CURRENT", "progress": 20, "media": {"episodes": 148}}, {"episodes": 148, "slots": []})
        self.assertIn('class="tline"', long)
        movie = self.stats({"status": "PLANNING", "progress": 0, "media": {"episodes": 1}}, {"episodes": 1, "slots": []})
        self.assertNotIn("tbar", movie)  # no "0/1" bar for a movie
        off = self.stats({"status": None}, {"episodes": 1, "slots": []})
        self.assertIn("not on your list yet", off)
        self.assertIn('data-a="eadd"', off)  # phones: the action pill is hidden, the add stays

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_progress_bar_node_survives_repaints_and_updates(self):
        """Background polls, frLoad ticks, downloads, and entry updates must not
        recreate #entrystats' progress bar node and restart its growx/tick animation."""
        self.assertIn("span.tline i{display:block;height:100%;border-radius:3px;background:var(--accent);animation:growx .6s .15s var(--ease) both;transform-origin:left;transition:width .35s var(--ease)}", CSS)
        self.assertIn("#app span.tline i,#app .ticks i{animation:none!important}", CSS)
        self.assertIn("else if (pj !== prev) frRepaint();", APP)
        # Extract morphNode/morphChildren/morphHTML + entryStats/entryActs/entryPaint
        morph_src = APP[APP.index("const LIVE_CLASSES ="):APP.index("/* A Settings section")]
        entry_src = APP[APP.index("function entryStats("):APP.index("function entryDraw(")]
        script = r"""
const assert = require("node:assert/strict");
class AttrList {
  constructor(map) { this._m = map; }
  *[Symbol.iterator]() { for (const [name, value] of this._m) yield { name, value }; }
}
class ClassList {
  constructor(el) { this.el = el; }
  _set() { return new Set((this.el._attrs.get("class") || "").split(/\s+/).filter(Boolean)); }
  contains(c) { return this._set().has(c); }
}
class Node {
  constructor(type, name, text = "") {
    this.nodeType = type; this.nodeName = name; this.tagName = name;
    this.nodeValue = text; this.childNodes = []; this.parentNode = null;
    this._attrs = new Map(); this.attributes = new AttrList(this._attrs);
    this.classList = new ClassList(this);
    this.dataset = new Proxy({}, {
      get: (_, k) => this._attrs.get("data-" + k),
      set: (_, k, v) => { this._attrs.set("data-" + k, String(v)); return true; },
    });
  }
  get id() { return this._attrs.get("id") || ""; }
  get firstChild() { return this.childNodes[0] || null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.childNodes.indexOf(this);
    return i >= 0 ? this.parentNode.childNodes[i + 1] || null : null;
  }
  hasAttribute(k) { return this._attrs.has(k); }
  getAttribute(k) { return this._attrs.has(k) ? this._attrs.get(k) : null; }
  setAttribute(k, v) { this._attrs.set(k, String(v)); }
  removeAttribute(k) { this._attrs.delete(k); }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this; this.childNodes.push(c); return c;
  }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) { this.childNodes.splice(i, 1); c.parentNode = null; }
    return c;
  }
  find(pred) {
    for (const c of this.childNodes) {
      if (pred(c)) return c;
      const d = c.find?.(pred);
      if (d) return d;
    }
    return null;
  }
  findAll(pred, out = []) {
    for (const c of this.childNodes) {
      if (pred(c)) out.push(c);
      c.findAll?.(pred, out);
    }
    return out;
  }
}
function parseFragment(html) {
  const root = new Node(11, "#fragment"), stack = [root];
  const re = /<\/([a-z0-9]+)>|<([a-z0-9]+)([^>]*)>|([^<]+)/gi;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) { if (stack.length > 1) stack.pop(); }
    else if (m[2]) {
      const el = new Node(1, m[2].toUpperCase());
      const ar = /([a-z0-9:-]+)(?:="([^"]*)")?/gi;
      let am;
      while ((am = ar.exec(m[3] || ""))) el.setAttribute(am[1], am[2] ?? "");
      stack[stack.length - 1].appendChild(el);
      if (!/^(INPUT|IMG|BR|HR)$/i.test(m[2])) stack.push(el);
    } else if (m[4]) {
      stack[stack.length - 1].appendChild(new Node(3, "#text", m[4]));
    }
  }
  return root;
}
const document = {
  createElement: () => ({ set innerHTML(h) { this.content = parseFragment(h); } }),
  activeElement: null,
};
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const ico = (n) => `<svg data-i="${n}"></svg>`;
const statusLabel = (s) => ({ CURRENT: "Watching", COMPLETED: "Completed", REPEATING: "Rewatching", PLANNING: "Planning" }[s] || s);
const acct = () => "AniList";
const reduceMotion = () => false;
let tablet = false;
const isPhone = () => false, isTabletP = () => tablet;
let fe = { id: 161645, episodes: 24, rewatch: { done: [1,2,3,4,5,6,7,8] }, slots: Array.from({ length: 24 }, (_, i) => ({ number: i + 1, watched: true, local: { id: "ep" + i } })) };
const S = { view: "fr", data: { rewatches: {} }, entry: { mid: 161645, svc: null } };
const frEntry = () => fe, showById = () => null, epTotal = () => 0, epWatched = () => 0;
const st = new Node(1, "DIV"), acts = new Node(1, "SPAN");
st.setAttribute("id", "entrystats"); acts.setAttribute("id", "entryacts");
const $ = (sel) => sel === "#entrystats" ? st : sel === "#app #entryacts" ? acts : null;
""" + morph_src + "\n" + entry_src + r"""
const f = { status: "COMPLETED", score: 9, progress: 24, media: { episodes: 24 } };
entryPaint(f, false);
const bar1 = st.find((n) => n.nodeType === 1 && n.nodeName === "I" && n.parentNode?.classList.contains("tline"));
assert.ok(bar1, "first paint creates span.tline > i");
assert.equal(bar1.getAttribute("style"), "width:33.3%");

// 1. Background poll / frRepaint with identical state: exact same node, no DOM work
entryPaint(f, false);
const bar2 = st.find((n) => n.nodeType === 1 && n.nodeName === "I" && n.parentNode?.classList.contains("tline"));
assert.strictEqual(bar2, bar1, "identical repaint keeps the exact same bar element");

// 2. Score / pending / progress update: bar node is still reused, width updates in place
fe.rewatch.done.push(9);
entryPaint({ ...f, score: 10 }, true);
const bar3 = st.find((n) => n.nodeType === 1 && n.nodeName === "I" && n.parentNode?.classList.contains("tline"));
assert.strictEqual(bar3, bar1, "progress and score update reuses the same bar element instead of restarting growx");
assert.equal(bar3.getAttribute("style"), "width:37.5%");

// 3. Portrait tablet ticks: updating progress reuses all tick nodes
tablet = true;
st.childNodes = []; st._html = undefined;
entryPaint(f, false);
const ticks1 = st.findAll((n) => n.nodeType === 1 && n.nodeName === "I" && n.parentNode?.classList.contains("ticks"));
assert.equal(ticks1.length, 24);
fe.rewatch.done.push(10);
entryPaint(f, false);
const ticks2 = st.findAll((n) => n.nodeType === 1 && n.nodeName === "I" && n.parentNode?.classList.contains("ticks"));
assert.equal(ticks2.length, 24);
for (let i = 0; i < 24; i++) assert.strictEqual(ticks2[i], ticks1[i], "tick " + i + " node reused");
assert.equal(ticks2[9].getAttribute("class"), "s");
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_about_and_top_line(self):
        a = APP.index("function Fran()")
        fran = APP[a:APP.index("\nfunction frOrder(", a)]
        self.assertIn('<span class="fmt">', fran)
        self.assertIn('class="alk"', fran)
        self.assertIn("${frAbout(e)}", fran)
        self.assertIn('<div class="fold">', fran)
        self.assertIn('const al = $("#aboutlive");', APP)
        self.assertIn(".frpage .fold{min-height:calc(100vh - 160px)}", CSS)
        self.assertNotIn(".st2", CSS)  # the old stats row is gone


class TabEntrances(unittest.TestCase):
    """Home, Library and Settings build in like the Journal (Elias, 2026-11-04)."""

    def test_entrances_leave_nothing_behind(self):
        block = CSS[CSS.index("/* Home · Library · Settings build in"):CSS.index("/* ───", CSS.index("/* Home · Library · Settings build in"))]
        # hover zooms and fixed children need no transform left after the entrance
        self.assertNotIn(" both}", block.replace(".settings.scol{animation:soft .25s var(--ease) both}", ""))
        for part in (".upn .upa{animation:artIn", ":is(.hc,.twb,.twr,.rec,.rc)", ".snav>*", ".scol.setin>.group>"):
            self.assertIn(part, block)

    def test_settings_section_builds_in_once(self):
        # in-place updates keep the class until the build-in ends; a search never builds in
        self.assertIn('"setin"]', APP.split("const LIVE_CLASSES", 1)[1].split("\n", 1)[0])
        enter = APP[APP.index("function setEnter()"):APP.index("const viewKey")]
        self.assertIn('c.dataset.sec !== "find"', enter)
        self.assertIn('!mq("(pointer:coarse)")', enter)  # Decision 44: no per-row entrances on touch
        self.assertIn("setEnter();", APP[APP.index("function render("):APP.index("paintPortals();\n}")])

    def test_library_bars_finish_their_fill(self):
        # the later batches of cards used to drop .fade at 650 ms and cut the bars mid-fill
        wall = APP[APP.index("function paintWall("):APP.index("/* ---------- detail ---------- */")]
        self.assertNotIn('classList.remove("fade")', wall)
        self.assertIn(".wall.fade .card:nth-child(-n+36){", CSS)  # only the first screenful animates

    def test_show_page_builds_in(self):
        for part in ("#app.nav-in :is(.frpage,.sp2) .eps-head{", "#app.nav-in .frorder>.ot{", "#app.nav-in .sp2>.about{"):
            self.assertIn(part, CSS)

    def test_calendar_builds_in(self):
        self.assertIn('style="--i:${Math.floor(i / 7) + i % 7}"', APP)  # month: a wave from the top left
        self.assertIn('class="aircol${k === tk ? " is-today" : k < tk ? " is-past" : ""}" style="--i:${i}"', APP)
        for part in ("#app.nav-in .aird{", "#app.nav-in .aircol{", "#app.nav-in .airside{", "#app.nav-in .tline .tli{"):
            self.assertIn(part, CSS)
        self.assertNotIn("#app.nav-in .airside>", CSS)  # a click on a day swaps its cards at once

    def test_journal_builds_in(self):
        """Elias, 2026-11-20: the Journal's opening wasn't on par: cards popped in, bars crawled for ~2.4 s."""
        for part in ("#app.nav-in .jr2 .jband>.n{", "#app.nav-in .jr2 .jcols .jcard{", "#app.nav-in .jr2 .heat i{", "#app.nav-in .jr2 .tl .ev{"):
            self.assertIn(part, CSS)
        # a chart's wave lasts as long whatever its number of bars: by place (--t), not by index × step
        self.assertIn("--t:${items.length > 1 ?", APP)
        self.assertNotIn("animation-delay:calc(var(--i) * 40ms + .15s)", CSS)
        self.assertIn('style="--c:${', APP)  # every card has its place in the wave
        self.assertIn("jCount();", APP[APP.index("function render("):APP.index("paintPortals();\n}")])
        count = APP[APP.index("function jCount()"):APP.index("/* The phone Journal's two tabs slide")]
        self.assertIn('mq("(pointer:coarse)")', count)  # Decision 44: no entrances on touch
        self.assertIn("t.data !== last", count)  # a redraw mid-count keeps its own text
        # a range switch redraws in place, so kept bars glide instead of all regrowing
        self.assertIn('lsSet("histRange", v); render(true);', APP)


class NoPageWideHas(unittest.TestCase):
    def test_no_body_has_rules(self):
        """TODO 213: a body:has(...) rule made every change on the page
        re-check the whole page's styles (~300 ms of a phone's Library
        switch). The page's state goes on <body> as pg-* classes instead,
        set by pageMarks() in app.js."""
        self.assertNotRegex(CSS, r"(^|[\s,}])(body|html|:root):has\(")
        for cls in re.findall(r"body\.(pg-[a-z]+)", CSS):
            self.assertIn(f'["{cls}", ', APP, cls)


class PopupFrame(unittest.TestCase):
    """TODO 226: the pop-ups are built from Settings' parts (Decision 73):
    a head with art or a glow, Settings rows, quiet buttons with ›, and the
    "change this" notes point at where the switch lives now."""
    def test_popups_use_the_settings_frame(self):
        for t in ("vpn", "skp", "nxt", "tip", "up", "where", "src", "pin", "ani", "mal", "tour", "pln", "fonts", "done"):
            self.assertIn(f'"{t}"', APP[APP.index("const DL2 = new Set(["):APP.index("function vpnGate(")])
        self.assertIn('DL2.has(m.t) ? "dl2" : ""', APP)
        self.assertIn(":is(.dl2,.match,.entry) .actions .btn::after{content:\"›\"", CSS)
        self.assertIn(":is(.dl2,.match,.entry) .actions .btn.pri{color:var(--accent-hi)", CSS)  # the main button is accent text, not a pill

    def test_vpn_notes_point_at_privacy(self):
        """D73 moved the ask-without-a-VPN switches to Privacy & VPN; the pop-ups said Online / Skipping."""
        self.assertIn('setLink("privacy", "Online episodes and downloads", "Privacy & VPN")', APP)
        self.assertIn('setLink("privacy", "AniSkip", "Privacy & VPN")', APP)
        self.assertNotIn("switch this off in Settings → Online episodes", APP)
        self.assertNotIn("switch this off in Settings → Skipping", APP)
        self.assertIn("setgo: (v, t) => { const tab = t.dataset.t; if (S.modal) closeModal();", APP)

    def test_vpn_is_a_settings_row(self):
        self.assertNotIn("vpnWait", APP)
        self.assertIn('srow("VPN", "", pkdo(dotTxt("Off", "live"))) + vpnList([...pts, "Turn a VPN on to start automatically"])', APP)
        self.assertIn('<b>Streaming site</b> sees ${ip}', APP)
        self.assertIn('<b>AniSkip</b> sees ${ip}', APP)
        self.assertIn('<ul class="vpn-pts">', APP)
        self.assertIn('.dl2 .vpn-pts{', CSS)
        self.assertIn('if (vpnMoved && S.modal?.t === "nxt") paintModal();', APP)  # still repainted live while the prompt is open

    def test_part_two(self):
        """The tour, playlists, fonts, the connect check and You finished: no
        switches or choice cards left, ‹ On › and Pick › like Settings."""
        self.assertIn('m.t === "rec" ? "recon dl2"', APP)
        self.assertIn('pick("Mark watched here", "recmark", null, [["1", "On"], ["0", "Off"]]', APP)
        self.assertIn('pick("Player layout", "tourlook", null,', APP)
        self.assertIn('pick(`Install ${g.name}`, "toolpick", g.id, [["on", "Install"], ["off", "Skip"]]', APP)
        self.assertIn('onoff("auto_progress_sync", "Send watched episodes", s.auto_progress_sync, !a.connected)', APP)
        self.assertNotIn('<button class="choice', APP)  # the old cards
        self.assertNotIn('class="sum2', APP)
        self.assertIn(".pkprev .lkv{width:280px", CSS)  # the player drawing has a size (it collapsed in Settings too)
        self.assertIn('T.choice === svc ? " pri" : ""}" data-a="tourconnect" data-v="${svc}"', APP)  # page 3's pick decides the last page's button

    def test_tour_offers_myanimelist(self):
        """Elias: the tour's account page offers MyAnimeList next to AniList (Decision 75)."""
        fn = APP[APP.index("const tourPickAcct ="):APP.index("const tourSwitches =")]
        for v in ('["anilist", "AniList",', '["mal", "MyAnimeList",', '["skip", "Skip for now",'):
            self.assertIn(v, fn)
        self.assertEqual(APP.count('["Connect an account?", ""'), 2)  # PC and Android
        self.assertNotIn("Connect AniList?", APP)
        self.assertIn('openModal({ t: v === "mal" ? "mal" : "ani" })', APP)  # the last page's Connect opens the picked one
        # Elias: logged in already, it still showed only AniList; now both services, Connect for the other
        self.assertIn("const tourPickAcct = (T, a, skipLine) => a.connected ? tourSvcRows(a)", APP)
        self.assertIn("if (a.connected) return tourSvcRows(a);", APP)

    def test_no_text_twice(self):
        """Elias: label + placeholder + help said the same thing; notes that add nothing are gone."""
        fn = APP[APP.index("function PlNameDlg() {"):APP.index("/* The icon picker:")]
        self.assertIn('placeholder="Name" aria-label="Name"', fn)
        self.assertNotIn('srow("Name"', fn)
        self.assertIn("@property --glow", CSS)  # a picked colour fades over
        self.assertIn("transition:--glow .6s", CSS)
        self.assertNotIn('"kept on this pc"', APP)
        self.assertNotIn("of ${b.length}`", APP)  # the step bar already counts

    def test_part_three(self):
        """Elias picked Edit, Remember the season before, Add shows and Which show is this (TODO 226)."""
        self.assertIn('m.t === "recap" || m.t === "plfd" ? m.t + " dl2"', APP)
        rc = APP[APP.index("function RecapDlg() {"):APP.index("const ppVideo =")]
        self.assertIn("dlHead(`Remember ${esc(p.label)}?`", rc)
        self.assertIn('data-a="recapoff">Don’t show this again</button>`, escBtn()', rc)
        self.assertNotIn("rcpf", APP + CSS)
        self.assertIn('dlHead("Add shows", `to ${esc(p.name)}`', APP)
        self.assertIn('dotTxt("Added") : "Add<b>›</b>"', APP)
        self.assertIn(':is(.dl2,.match,.entry) .actions .btn.pri', CSS)
        self.assertNotIn('<span class="cap">your entry</span>', APP)
        self.assertNotIn("AniList has nothing under that name", APP)  # the field and the footer say it


class ContextMenuPolish(unittest.TestCase):
    """Tight Context Card across every right-click & #cm menu (2026-11-22)."""

    def test_shared_card_header_and_gliding_highlight(self):
        for fn in ("function cmHead(", "function cmMoveHl(", "function cmFire(", "function plMenuPatchRow(", "function cmRollFoot("):
            self.assertIn(fn, APP)
        # Every menu routes through cmShow with ARIA roles, UI-font keycaps, and backwards-only row entrance
        self.assertIn('m.setAttribute("role", "menu")', APP)
        self.assertIn('m.setAttribute("role", "dialog")', APP)
        self.assertIn('role="menuitem"', APP)
        self.assertIn("font:500 12px/1 var(--ui);color:rgba(243,243,241,.38)", CSS)  # plain letters in the UI font: O and 0 stay distinct
        self.assertIn("animation:cmRowIn .15s cubic-bezier(.22,1,.36,1) backwards", CSS)  # never blocks :active scale
        self.assertIn("z-index:35;", CSS)  # desktop #cm sits below modal .overlay (40); 61 only while no dialog is open (above the toast)
        self.assertIn("#cm .plseg-gl{", CSS)  # gliding segment pill in Layout popover
        self.assertIn('<div class="plml-in" id="plml-in"></div>', APP)  # persistent .cmhl across filter keystrokes
        self.assertNotIn("cm-target", CSS + APP)  # no accent ring outline on menu triggers or right-clicked tiles
        self.assertNotIn("--cm-ring", CSS + APP)
        self.assertIn('.ep[data-a=playep], .ep[data-a=unavail]', APP)  # unavailable episodes right-click too

    def test_option_3_thumbnail_card(self):
        # Elias picked mockup 3 (2026-11-22): the first A's thumbnail card, sized for a mouse
        self.assertIn("#cm .cmha.still{width:80px;height:45px}", CSS)
        self.assertIn("position:relative;z-index:1;height:34px;padding:0 9px;gap:9px;border-radius:8px;", CSS)  # 30px felt small (feel panel)
        self.assertIn("font:650 15px/1.2 var(--ui);color:var(--ink)", CSS)  # header title
        self.assertIn("width:272px;padding:5px;border-radius:14px;", CSS)
        self.assertIn("background:none;box-shadow:none;", CSS)  # keys are plain letters, no boxes

    def test_tight_for_mouse_and_keyboard(self):
        # The menu's corner sits at the pointer, as on every desktop (Elias, feel panel 2026-11-22)
        self.assertIn("Math.min(flipY ? py - h - yGap : py + yGap, botMax - h)", APP)
        self.assertNotIn("hOff", APP)
        self.assertNotIn("cmlab", APP + CSS)  # the temporary feel panel is gone
        # Only a keyboard opening lights a row; a mouse lights one after it moves
        self.assertIn('if (ddPtr !== "touch" && kb) m.querySelector(".mi")?.classList.add("sel");', APP)
        self.assertIn("if (r && cmShow.still)", APP)
        # Esc puts focus back on the tile only after a keyboard opening
        self.assertIn("if (restoreFocus && cmShow.kb) {", APP)
        # Close waits for its animation instead of a same-length timer
        self.assertIn('m.addEventListener("animationend", onEnd);', APP)
        # Destructive rows answer Delete / Backspace
        self.assertIn('e.key === "Delete" || e.key === "Backspace" ? "DEL"', APP)
        self.assertIn('"Remove from Home", "Del"', APP)
        # Add to another playlist grows out of the row menu; filtering resizes smoothly
        self.assertIn("cmShow(m, x, y, targetEl, cmAccent(item.color), exactPos, from);", APP)
        self.assertIn("cmMenuGrow.a = m.animate(", APP)
        # Layout switch never wraps; no repeated card on Layout or the show page ⋯
        self.assertIn("if (next) { next.focus(); cmFire(next); }", APP)
        self.assertNotIn('eyebrow: "Layout"', APP)
        # Very subtle: no icon spins or tilts, no accent fill on the primary highlight


class ArrowButtonsPolish(unittest.TestCase):
    """Soft filled squircle arrows across Home, Show, Browse, Calendar and the date picker (2026-11-22)."""

    def test_soft_squircle_arrows_everywhere(self):
        # One .arr: no outline circles, no double borders, both arrows the same (no grey disc on > alone)
        self.assertIn(".arr{width:34px;height:34px;border:0;border-radius:10px;background:rgba(255,255,255,.06);box-shadow:none;", CSS)
        self.assertNotIn('.sp2 .eps-head .ctl .arr[data-d="1"]{background:rgba(255,255,255,.1)}', CSS)
        self.assertNotIn(".home2.wide .hsec .arr:disabled{opacity:0", CSS)
        self.assertNotIn(".hsec .arr{width:32px;height:32px;border-radius:999px;", CSS)
        # Directional hover nudge and press response on .arr, Calendar .airnav, and date picker
        self.assertIn('.arr:is([data-d="-1"],[data-v="-1"]):not(.plarr):hover:not(:disabled) .ico{transform:translateX(-2px)}', CSS)
        self.assertIn('.arr:is([data-d="1"],[data-v="1"]):not(.plarr):hover:not(:disabled) .ico{transform:translateX(2px)}', CSS)
        self.assertIn(".airnav{display:inline-flex;align-items:center;gap:2px;height:34px}", CSS)
        # Browse season and Calendar titles slide in the step direction (including return via Today & async repaint)
        self.assertIn("S.br.stepDir = +v; S.br.stepAt = performance.now();", APP)
        self.assertIn("c.stepDir = +v;", APP)
        self.assertIn("c.stepDir = tgt === cur ? 0 : tgt > cur ? 1 : -1;", APP)
        self.assertIn("@keyframes stepInR{from{opacity:.45;transform:translateX(8px)}}", CSS)  # never blinks out on fast clicks
        self.assertIn(".sp2 .eps-head .ctl .arr{color:rgba(243,243,241,.92);background:var(--glass);", CSS)  # readable on bright art
        # Date picker: fixed 6-row height (0px jump), 34px targets, in-place month patch preserves focus
        self.assertIn(".cal-g{grid-auto-rows:32px;min-height:202px;align-content:start}", CSS)
        self.assertIn(".cal-h button{width:34px;height:34px;border-radius:10px;", CSS)
        self.assertIn('n.classList?.contains("pka") ? "pka|" + n.dataset.d', APP)
        self.assertNotIn(".cm-i-close{transform:rotate", CSS)
        self.assertNotIn("#cm .cmhl.pri{", CSS)
        # No bare .warn callout rule leaking a curved border-left onto #cm .cmhl.warn
        self.assertNotRegex(CSS, r"(?m)^\.warn\{")
        # Caution hover applies consistently to state-clearing/unpinning rows too
        self.assertIn('data-w="${!done}" data-warn="1"', APP)
        self.assertIn('p.pinned ? \'data-warn="1"\' : ""', APP)


class ScrollbarPolish(unittest.TestCase):
    """Floating overlay scrollbar on desktop and arrow-free inner scrollbars."""

    def test_chromium_webkit_scrollbars_are_not_overridden_by_standard_thin(self):
        # In Chromium 121+, scrollbar-width:thin disables ::-webkit-scrollbar and draws native arrows.
        # Every scrollbar-width:thin must stay inside the Firefox-only @supports block.
        without_supports = re.sub(r"@supports not selector\(::-webkit-scrollbar\)\{[^}]*\{[^}]*\}\}", "", CSS)
        self.assertNotIn("scrollbar-width:thin", without_supports)
        self.assertIn("@supports not selector(::-webkit-scrollbar){", CSS)
        self.assertIn("::-webkit-scrollbar-button{display:none;width:0;height:0}", CSS)
        self.assertIn("background-clip:content-box", CSS)

    def test_desktop_overlay_scrollbar_markup_and_rules(self):
        self.assertIn('<div id="sbar" aria-hidden="true"><i></i></div>', HTML)
        self.assertIn("@media not (pointer:coarse){", CSS)
        self.assertIn("html{scrollbar-width:none}html::-webkit-scrollbar{display:none}", CSS)
        self.assertIn("#sbar{display:block;position:fixed;right:0;top:74px;bottom:48px;", CSS)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_overlay_scrollbar_metrics(self):
        fn = re.search(r"function sbarMetrics\([^)]*\) \{.*?\n\}\n", APP, re.S).group(0)
        out = run_js(fn + """return [
            sbarMetrics(900, 900, 0, 800),
            sbarMetrics(2000, 1000, 0, 800),
            sbarMetrics(2000, 1000, 500, 800),
            sbarMetrics(2000, 1000, 1000, 800),
            sbarMetrics(50000, 1000, 49000, 800),
            sbarMetrics(2000, 1000, 0, 20),
        ];""")
        self.assertEqual(out[0], {"show": False, "h": 0, "y": 0})
        self.assertEqual(out[1], {"show": True, "h": 400, "y": 0})
        self.assertEqual(out[2], {"show": True, "h": 400, "y": 200})
        self.assertEqual(out[3], {"show": True, "h": 400, "y": 400})
        self.assertEqual(out[4], {"show": True, "h": 36, "y": 764})  # min thumb height clamped
        self.assertEqual(out[5], {"show": True, "h": 20, "y": 0})    # never taller than track



class DropdownMenuPolish(unittest.TestCase):
    """Unified Glass Card across every desktop #ddm dropdown (2026-11-22)."""

    def test_glass_card_gliding_highlight_and_scroll_fades(self):
        for fn in ("function ddScrollFade(", "function ddMoveHl(", "function ddOpen(", "function ddClose(", "function ddPick("):
            self.assertIn(fn, APP)
        # Matches #cm glass surface, tight 34px rows, capped width, thin scrollbar & deep fades, no left bar
        self.assertIn("max-width:min(320px,calc(100vw - 20px));", CSS)
        self.assertIn("#ddm:not(.sheet) .ddhl{", CSS)
        self.assertIn("#ddm:not(.sheet) .ddl.fade-b{", CSS)
        self.assertIn("scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.2) transparent", CSS)
        self.assertIn("#ddm:not(.sheet) .ddl::-webkit-scrollbar{width:4px}", CSS)
        self.assertNotIn("#ddm:not(.sheet) button.on::before{", CSS)
        self.assertIn("@keyframes ddCardIn{", CSS)
        self.assertIn("@keyframes ddCardOut{", CSS)
        self.assertIn("@keyframes ddCkDraw{", CSS)
        self.assertIn("@keyframes ddChevBack{", CSS)
        # Active season keeps its episode count alongside the check; Settings parent row stays lit & matches .pkm width
        self.assertIn('<small class="r">${esc(o.dataset.r)}</small>', APP)
        self.assertIn('<span class="ddck" aria-hidden="true">${ico("check")}</span>', APP)
        self.assertIn("#ddm.pkdd:not(.sheet) button{justify-content:center;", CSS)  # Settings values centred (TODO 273)
        self.assertIn(".set2 .srow:is(:hover,:focus-within,:has(.pk.ddon))", CSS)
        self.assertIn("pk?.querySelector(\".pkm\")", APP)
        # Commits at 0ms, restores focus across async render(), keeps zoompick animation, cleans ddEat on pointerup
        self.assertIn("function ddReattach()", APP)
        self.assertIn("if (e.button === 0 || ddPtr !== \"mouse\") ddEat = Date.now(); ddClose(false);", APP)
        self.assertIn("ddToggledFl = sel.closest(\"label.fl\")", APP)
        self.assertIn('document.addEventListener("pointerup", ddClearPress, true);', APP)
        self.assertIn("Math.hypot(e.clientX - ddPtr0.x, e.clientY - ddPtr0.y) < 4", APP)
        # Narrow-desktop .fdrop filter popovers match with gliding highlight & keyboard nav
        self.assertIn("function fdropMoveHl(", APP)
        self.assertIn(".lib2 .filters>.fdrop .fdhl{", CSS)
        # Keyboard: blocks ArrowLeft/Right & PageUp/Down, first/repeated type-ahead cycles immediately, mouse open starts unhighlighted
        self.assertIn("ddKb = !!kb; ddStill = !kb;", APP)
        self.assertIn('e.key === "ArrowLeft" || e.key === "ArrowRight"', APP)
        self.assertIn('e.key === "PageDown" || e.key === "PageUp"', APP)
        self.assertIn("lbl.scrollWidth > lbl.clientWidth + 1", APP)
        # Close waits on animationend, measures real #nav/#status, and .fl chevron rotates while open
        self.assertIn('m.addEventListener("animationend", ddClose.end);', APP)
        self.assertIn(".lib2 .fl.ddon:has(select)::after{transform:rotate(180deg)}", CSS)
        self.assertIn('"ddon"', APP.split("const LIVE_CLASSES", 1)[1].split("\n", 1)[0])
        # Sonnet R1: chip width glides on pick, chip lists keep one width, arrows stop at the ends, focus ring, aria-expanded
        self.assertIn('trig.animate([{ width: w0 / z + "px", overflow: "hidden" }', APP)
        self.assertIn("ddRestore.w0", APP)
        self.assertIn('sel.closest(".fl") ? 0 : p.width', APP)
        self.assertIn('const libChip = sel.dataset.a === "filt" && sel.closest(".fl");', APP)
        self.assertIn('m.style.minWidth = (libChip ? box.width / z :', APP)
        self.assertNotIn("#ddm:not(.sheet) button.on{color:#fff;font-weight:600}", CSS)
        self.assertNotIn("% bs.length]", APP)
        self.assertIn(".lib2 .fl:has(select:focus-visible),.pk .pkm:has(select:focus-visible){", CSS)
        self.assertIn('sel.setAttribute("aria-expanded", "true");', APP)


class SubtitleSettingsUI(unittest.TestCase):
    """Expanded Subtitle look rows, ASS modes, and live preview in Settings -> Subtitles."""

    def test_subtitle_look_rows_and_live_preview(self):
        fn = APP[APP.index("function subsGroup("):APP.index("function skipGroup(")]
        for row in ('"Preferred audio"', '"Preferred subtitles"', '"Hide [SDH] cues"',
                    '"Profile"', '"Styled subtitles"', '"Font"', '"Size"', '"Bold"', '"Colour"',
                    '"Outline"', '"Outline colour"', '"Edge softness"', '"Line spacing"',
                    '"Background"', '"Position"'):
            self.assertIn(f"srow({row},", fn)
        self.assertIn("${subPreview(m)}", fn)
        self.assertIn('data-a="subreset"', fn)
        self.assertIn('data-a="fontpick"', fn)
        self.assertIn("function subProfileSavePop(", APP)
        self.assertIn('"__custom__", "Save current…"', APP)
        self.assertIn('"__custom__", "Custom colour…"', APP)
        self.assertIn('"__custom__", "Custom size…"', APP)
        self.assertIn("function subColorPick(", APP)
        self.assertIn("function subSizePick(", APP)
        self.assertIn("loadUserFonts(mine);", fn)
        self.assertIn(".set2 .subprev{position:sticky;top:68px;", CSS)
        self.assertIn(".subpop{position:fixed;z-index:85;", CSS)
        self.assertIn(".subpop input.cp-range,.subpop input.cp-range:hover:not(:disabled){", CSS)
        self.assertIn('data-custom="1"', APP)
        self.assertIn(".subprev .sp-frame{", CSS)
        self.assertIn(".subprev .sp-frame.bg-check{", CSS)
        self.assertIn(".subprev .sp-bgs{", CSS)
        self.assertIn('data-a="subbg"', APP)
        self.assertIn('g.querySelectorAll(".sub,.pairbox,.dirpick,.subprev")', APP)
