"""TODO 48: the Decision 28 palettes, the genre filter, the home airing
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
        self.assertIn('fsel("air", "Airing", [["all", "any"], ...Object.entries(AIR)])', APP)

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
        self.assertIn("airText(e.next_episode)", APP)

    def test_palettes(self):
        keys = run_js("return PALS.map(([k]) => k);")
        self.assertEqual(keys, ["graphite", "teal", "oxide"])
        for k in keys[1:]:
            self.assertRegex(CSS, r":root\[data-pal=%s\] \{[^}]*--accent:" % k)
        for k in keys:
            self.assertIn(f"i[data-sw={k}]", CSS)
        self.assertEqual(run_js("return palGet();", {"pal": "teal"}), "teal")
        self.assertEqual(run_js("return palGet();", {"pal": "nope"}), "graphite")
        self.assertIn('data-a="pal"', APP)
        # Applied before the stylesheet loads: no flash of the default palette.
        self.assertLess(HTML.index('dataset.pal'), HTML.index('style.css'))


class Discovery(unittest.TestCase):
    """TODO 11 (Decision 9): one search bar over library + AniList; a hit off
    the lists opens its page, which says so and offers the add."""
    def test_search_and_add(self):
        self.assertIn('api("/api/match-candidates", { query: mine })', APP)  # AniList half of the search bar
        self.assertIn("catpick: (v) => {", APP)
        self.assertIn("openFranchise(x.id)", APP)
        self.assertIn("Not on your list yet.", APP)
        self.assertIn('eadd: (v, t) => { t.disabled = true; entryPatch({ status: "PLANNING" }); }', APP)


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
