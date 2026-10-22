"""TODO 12 (Decisions 7–8): the To match tab. Suggestions load for every
unmatched folder on each library load, not only the first five once the tab
opens (a); each folder can ask AniList again past the cache (b); "Not on
AniList — keep local" is remembered (c)."""

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
