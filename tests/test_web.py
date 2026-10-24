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
APP = (WEB / "app.js").read_text()
CSS = (WEB / "style.css").read_text()
HTML = (WEB / "index.html").read_text()


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
const srcLocked = () => false;
console.log(JSON.stringify((() => {{ {body} }})()));
"""
    out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=20)
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
                      f"return libFiltered({{ entries: {json.dumps(ENTRIES)} }}).map((e) => e.id).sort();")

    def test_genre_filter_needs_every_pick(self):
        self.assertEqual(self.filtered(genres=["Action"]), [1, 2])
        self.assertEqual(self.filtered(genres=["Action", "Drama"]), [1])
        self.assertEqual(self.filtered(genres=[]), [1, 2, 3, 4])

    def test_genre_tray_counts_the_open_tab(self):
        m = json.dumps({"entries": ENTRIES})
        self.assertEqual(run_js(f"return genreCounts({m});"), [["Action", 2], ["Drama", 2]])
        self.assertEqual(run_js(f"S.lib.tab = 'CURRENT'; return genreCounts({m});"), [["Action", 2], ["Drama", 1]])

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
        self.assertEqual(run_js("return LOOKS;"), {"motion": ["full", "calm", "off"], "art": ["full", "soft", "off"]})
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
        self.assertIn("coverImage{extraLarge color}", (support.ROOT / "animehub" / "anilist.py").read_text())

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
        self.assertIn('if (e.key === ",") { e.preventDefault(); nav("settings"); return; }', APP)
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
        self.assertIn('<div class="jall"><h2>All time</h2>${Stats(true)}</div>', APP)
        self.assertIn('const VIEWS = ["home", "library", "history", "stats", "settings"];', APP)


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
        self.assertIn('data-a="nxstop">Stop</button><button class="btn pri" data-a="nxgo">Continue</button>', APP)
        self.assertIn("play(id, 0, undefined, true)", APP)  # the popup already showed the VPN state


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
                out = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
