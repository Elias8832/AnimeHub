"""TODO 12 (Decisions 7–8): the To match tab. Suggestions load for every
unmatched folder on each library load, not only the first five once the tab
opens (a); each folder can ask AniList again past the cache (b); "Not on
AniList — keep local" is remembered (c). Plus TODO 52's match leftovers:
/api/match honours `query` (e), matches are keyed by folder path (e), and
the desktop entry names the window class the wrapper sets (f)."""

import json
import re

import shutil
import unittest
from unittest import mock

from tests import support
from tests.test_web import APP, run_js
from animehub import anilist, database, library, metadata


class KeepLocal(unittest.TestCase):
    def setUp(self):
        self.scan = support.use_library(support.case("apothecary"))
        self.sid = self.scan[0]["id"]

    def test_kept_folder_is_remembered_and_flagged(self):
        self.assertEqual(metadata.set_kept_local(self.sid), {"ok": True, "kept_local": True})
        self.assertEqual(metadata.kept_local(), {self.sid})
        with mock.patch.object(anilist, "anilist_connected", return_value=False):
            show = next(s for s in library.full_library()["shows"] if s["id"] == self.sid)
        self.assertIs(show["kept_local"], True)
        metadata.set_kept_local(self.sid, False)
        self.assertEqual(metadata.kept_local(), set())

    def test_unknown_show_is_refused(self):
        self.assertIn("error", metadata.set_kept_local("nope"))
        self.assertEqual(metadata.kept_local(), set())

    def test_matcher_skips_kept_folders(self):
        metadata.set_kept_local(self.sid)
        with mock.patch.object(metadata, "fetch_show_meta") as fetch, \
             mock.patch.object(metadata.time, "sleep"), \
             mock.patch.object(metadata.franchise, "refresh_franchises"), \
             mock.patch.object(metadata.mapping, "refresh_entry_art"):
            metadata.refresh_meta()
        self.assertNotIn(self.sid, [c.args[0]["id"] for c in fetch.call_args_list])

    def test_matching_by_hand_ends_keep_local(self):
        metadata.set_kept_local(self.sid)
        media = {"id": 161645, "title": {"english": "The Apothecary Diaries"}}
        with mock.patch.object(metadata, "fetch_media_full", return_value=media), \
             mock.patch.object(metadata, "store_show_meta"):
            self.assertTrue(metadata.set_match(self.sid, 161645)["ok"])
        self.assertEqual(metadata.kept_local(), set())


class ManualMatch(unittest.TestCase):
    def setUp(self):
        self.show = support.use_library(support.case("apothecary"))[0]
        self.media = {"id": 161645, "title": {"english": "The Apothecary Diaries"}}

    def overrides(self):
        return json.loads((support.config.DATA / "overrides.json").read_text(encoding="utf-8"))

    def test_query_matches_the_top_hit(self):
        with mock.patch.object(metadata, "fetch_media_full", return_value=self.media) as fetch, \
             mock.patch.object(metadata, "store_show_meta"):
            out = metadata.set_match(self.show["id"], query="Kusuriya no Hitorigoto")
        fetch.assert_called_once_with(search="Kusuriya no Hitorigoto")
        self.assertEqual(out["anilist_id"], 161645)
        self.assertEqual(self.overrides(), {self.show["path"]: {"anilistId": 161645}})

    def test_query_without_hits_keeps_the_match(self):
        with mock.patch.object(metadata, "fetch_media_full", return_value=None), \
             mock.patch.object(metadata, "clear_match") as clear:
            self.assertIn("error", metadata.set_match(self.show["id"], query="zzz"))
        clear.assert_not_called()

    def test_path_override_beats_a_title_one(self):
        (support.config.DATA / "overrides.json").write_text(json.dumps(
            {self.show["title"]: {"anilistId": 1}, self.show["path"]: {"anilistId": 2}}))
        with mock.patch.object(metadata, "fetch_show_meta") as fetch, \
             mock.patch.object(metadata.time, "sleep"), \
             mock.patch.object(metadata.franchise, "refresh_franchises"), \
             mock.patch.object(metadata.mapping, "refresh_entry_art"):
            metadata.refresh_meta()
        self.assertEqual(fetch.call_args.args[1], {"anilistId": 2})


class FreshSearch(unittest.TestCase):
    def setUp(self):
        support.use_library(support.case("apothecary"))

    def test_fresh_skips_the_cache(self):
        answer = lambda hits: {"data": {"Page": {"media": hits}}}
        with mock.patch.object(anilist, "anilist_request", return_value=answer([{"id": 1}])) as req:
            self.assertEqual(metadata.fetch_media_candidates("Frieren"), [{"id": 1}])
            req.return_value = answer([{"id": 2}])
            self.assertEqual(metadata.fetch_media_candidates("Frieren"), [{"id": 1}])  # cached
            self.assertEqual(metadata.fetch_media_candidates("Frieren", fresh=True), [{"id": 2}])
            self.assertEqual(metadata.fetch_media_candidates("Frieren"), [{"id": 2}])  # cache updated
        self.assertEqual(req.call_count, 2)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Tab(unittest.TestCase):
    def test_kept_folder_moves_to_untracked(self):
        shows = [{"id": "a", "meta": None}, {"id": "b", "meta": None, "kept_local": True},
                 {"id": "c", "meta": {"anilist_id": 9}}]
        out = run_js(f"""
            const allShows = () => S.data.shows;
            const showById = (id) => allShows().find((x) => x.id === id) || null;
            S.data = {{ shows: {__import__("json").dumps(shows)}, spine: {{ entries: [], untracked: ["a", "b", "c"] }} }};
            const m = libModel();
            return {{ match: m.unmatched.map((s) => s.id), untracked: m.untracked.map((s) => s.id) }};""")
        self.assertEqual(out, {"match": ["a"], "untracked": ["b", "c"]})

    def test_suggestions_load_ahead_for_every_folder(self):
        self.assertIn("for (const s of list) {", APP)  # no first-five cap
        self.assertIn("S.data = d; offlineNote(false);\n  autoSuggest(unmatchedShows());", APP)
        self.assertIn('data-a="mrefresh"', APP)
        self.assertIn("runSearch(v, m.q || showById(v)?.title || \"\", true)", APP)
        self.assertIn('api("/api/keep-local", { show_id: v, keep })', APP)


def _js_fn(start, end_marker="\n}\n"):
    """One top-level piece of app.js, from `start` to the end of its block."""
    i = APP.index(start)
    return APP[i:APP.index(end_marker, i) + len(end_marker)]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class MatchDialog(unittest.TestCase):
    """Needs a look polish (Elias, 2026-11-09: cards like mockup C, a centred
    dialog asking A's "Is this …?"): each folder is a poster card, one dialog
    goes through them in turn."""

    def node(self, src):
        import subprocess
        out = subprocess.run(["node", "-"], input=src + "\n", capture_output=True, text=True, encoding="utf-8", timeout=20)
        if out.returncode:
            raise AssertionError(out.stderr)
        return json.loads(out.stdout)

    def test_parent_folder_joins_the_second_search(self):
        got = self.node(_js_fn("const GENERIC = ") + """console.log(JSON.stringify([
            altQuery({ title: "The Last Attack", path_label: "Attack on Titan/Movies/The Last Attack" }),
            altQuery({ title: "Frieren", path_label: "Frieren" }),
            altQuery({ title: "Movie 2", path_label: "Movies/Specials/Movie 2" }),
            altQuery({ title: "Attack on Titan Season 2", path_label: "Attack on Titan/Attack on Titan Season 2" })]));""")
        self.assertEqual(got, ["Attack on Titan The Last Attack", "", "", ""])

    def test_dialog_moves_on_after_a_match_then_closes(self):
        got = self.node("""const S = { modal: { t: "match", id: "b", list: ["a", "b", "c"] } }; const seen = [];
            const closeModal = () => { S.modal = null; seen.push("closed"); }; const paintModal = () => seen.push(S.modal.id);
            """ + _js_fn("function matchAdvance(done) {") + """matchAdvance("b"); matchAdvance("c"); matchAdvance("a");
            console.log(JSON.stringify(seen));""")
        self.assertEqual(got, ["c", "a", "closed"])  # the next one in place, wrapping back, then done

    def test_shape(self):
        self.assertIn("entry: EntryDlg, match: MatchDlg }[m.t]();", APP)
        # keys pick the guesses by their place in the row, the number on each poster
        self.assertIn('const b = $$("#modal .moth")[+e.key - 1];', APP)
        self.assertIn('<kbd class="kb">${k + 1}</kbd>', APP)
        # an unmatched folder's own page asks the same question and opens the dialog for it alone
        self.assertIn('<div class="matchbox">${(autoSuggest([s]), matchTease(s))}</div>', APP)
        self.assertIn('!t?.closest?.(".mtease") ? all : [v]', APP)
        # the file count against AniList's episode count, coloured only when AniList states one
        self.assertIn('best.episodes === n ? "ok" : "warn"', APP)
        # no "is it …?" tag on the cards (Elias, 2026-11-09): the poster says it
        self.assertIn('searching</span>` : best ? "" : `<span class="mtag none">', APP)

    def test_search_is_live_and_a_pick_becomes_the_question(self):
        # as you type, through the search box's path (local title list, then AniList)
        self.assertIn('document.addEventListener("input", (e) => { if (e.target.id === "mdq") mdqInput(e.target.value); });', APP)
        self.assertIn('api("/api/match-candidates", { query: t, fuzzy: true })', APP)
        self.assertIn("if ((m.sq || \"\").trim() !== t) return; // a newer search took over", APP)
        # a pick goes in front of the guesses and the search empties
        self.assertIn("m.cands = [x, ...(m.cands || []).filter((y) => y.id !== x.id)]; m.pick = 0;", APP)
        # ↑ ↓ ↵ Esc in the field; Esc on an empty field closes the dialog
        self.assertIn('if (e.key === "Escape" && !m.sq) { e.preventDefault(); closeModal(); return; }', APP)

    def test_a_pasted_link_or_id_matches(self):
        got = self.node(_js_fn("const ANILINK = ", ";\n") + """console.log(JSON.stringify(
            ["https://anilist.co/anime/5081/Bakemonogatari", "5081", "monogatari 2"].map((t) => (ANILINK(t) || [])[1] || null)));""")
        self.assertEqual(got, ["5081", "5081", None])

    def test_episode_count_is_green_only_when_it_fits(self):
        # search rows and the guess rail: green when it equals the file count, plain otherwise
        got = self.node("const esc = (x) => x; const FMT = { TV: \"TV\" }; const fmtName = (f) => FMT[f] || f;\n" + _js_fn("const candMeta = ", ";\n")
                        + _js_fn("const candFits = ", ";\n") + """console.log(JSON.stringify(
            [11, 12].map((n) => candFits({ seasonYear: 2013, format: "TV", episodes: 11 }, n))));""")
        self.assertEqual(got, ['2013 · TV · <em class="mok">11 ep</em>', "2013 · TV · 11 ep"])
        self.assertIn("<small>${candFits(x, n)}</small>", APP)


class Desktop(unittest.TestCase):
    def test_desktop_entry_names_the_window_class(self):
        """X11 honours --class; native Wayland (TODO 61) names the window
        WAYLAND_CLASS — install.sh writes whichever the session gets."""
        rs = (support.ROOT / "desktop/src/main.rs").read_text(encoding="utf-8")
        sh = (support.ROOT / "desktop/install.sh").read_text(encoding="utf-8")
        cls = re.search(r'"--class=(\w+)"', rs).group(1)
        wl = re.search(r'WAYLAND_CLASS: &str = "([^"]+)"', rs).group(1)
        self.assertIn('WMCLASS="%s"; else WMCLASS="%s"' % (wl, cls), sh)
        self.assertIn("StartupWMClass=$WMCLASS\n", sh)
        self.assertIn('if std::env::var_os("WAYLAND_DISPLAY").is_some() { "wayland" } else { "x11" }', rs)
