"""TODO 222: a first scan put every show under "Needs a look" until the
background pass matched it. Shows the pass is still matching are named, so
the page counts only the ones it couldn't place."""

import unittest
from unittest import mock

from tests import support  # noqa: F401
from animehub import franchise, library, mapping, metadata, pages

APP = (support.ROOT / "web" / "app.js").read_text(encoding="utf-8")


def setUpModule():
    support.use_library(support.case("aot"))


class Matching(unittest.TestCase):
    def test_the_pass_names_the_shows_it_is_matching(self):
        seen = []

        def fetch(s, override):
            seen.append(list(metadata._meta_status["matching"]))

        with mock.patch.object(metadata, "fetch_show_meta", fetch), \
             mock.patch.object(metadata.time, "sleep"), \
             mock.patch.object(franchise, "refresh_franchises"), mock.patch.object(pages, "drop_premature_links"), \
             mock.patch.object(mapping, "refresh_entry_art"), mock.patch.object(metadata, "refresh_art"), \
             mock.patch.object(metadata, "kept_local", return_value=set()):
            metadata.refresh_meta()
        scan = library.scan_library()
        missing = [s["id"] for s in scan if not s.get("poster")]
        if not missing:
            self.skipTest("the test library has posters for every show")
        self.assertEqual(seen[0], missing)  # all of them, from the start of the pass
        self.assertEqual(metadata._meta_status["matching"], [])  # and none once it is over

    def test_the_library_and_now_carry_it(self):
        with mock.patch.dict(metadata._meta_status, {"matching": ["abc"]}):
            self.assertEqual(library.full_library()["meta_matching"], ["abc"])
        self.assertIn('"matching": list(metadata._meta_status["matching"])', (support.ROOT / "animehub" / "server.py").read_text())

    def test_a_new_folder_is_followed_until_matched(self):
        fn = APP[APP.index("async function refreshAndWatch()"):APP.index("async function extraAdd(")]
        self.assertLess(fn.index('api("/api/refresh"'), fn.index("await reload();"))  # the pass first, then the library
        self.assertIn("startMetaWatch();", fn)
        for caller in ("async function dirSet(", "async function extraAdd(", "  rescan: "):
            i = APP.index(caller)
            self.assertIn("refreshAndWatch()", APP[i:i + 700])

    def test_the_page_leaves_them_out_and_refetches_when_matching_ends(self):
        self.assertIn("!(S.data.meta_matching || []).includes(s.id)", APP)
        self.assertIn('const matched = S.data.meta_phase === "show metadata" && m.phase !== "show metadata";', APP)


if __name__ == "__main__":
    unittest.main()
