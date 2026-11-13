"""Symlinked folders in the library: a show linked in from another disk is
scanned under its link's name, a link loop or two links to one folder are
read once, a link back into the library isn't a second copy, and a broken
link is skipped quietly until its disk is back."""
import os
import threading
import unittest

from tests import support
from animehub import database, library
from animehub.config import config, CONFIG, apply_config


class Links(unittest.TestCase):
    def setUp(self):
        self.base = support.TMP / "links" / self.id()
        self.lib = self.base / "library"
        self.disk = self.base / "otherdisk"
        self.lib.mkdir(parents=True)
        self.disk.mkdir(parents=True)
        apply_config(dict(CONFIG, library_root=self.lib, data_dir=self.base / "data"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        database.init_db()
        library._scan_memo["sig"] = None

    def files(self, folder, *names):
        folder.mkdir(parents=True, exist_ok=True)
        for n in names:
            (folder / n).touch()

    def link(self, at, to):
        try:
            os.symlink(to, at, target_is_directory=True)
        except (OSError, NotImplementedError) as e:  # Windows without the right to make symlinks
            self.skipTest(f"no symlinks here: {e}")

    def scan(self):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("scan", library.scan_library()), daemon=True)
        t.start()
        t.join(30)
        self.assertFalse(t.is_alive(), "the scan hangs")
        return out["scan"]

    def shows(self, scan):
        return sorted((s["title"], [ep["rel"] for ep in library.eps_of(s)]) for s in scan)

    def test_a_linked_show_is_scanned_under_its_links_name(self):
        self.files(self.lib / "Local", "Local - 01.mkv")
        self.files(self.disk / "Frieren" / "Season 1", "Frieren - 01.mkv", "Frieren - 02.mkv")
        self.link(self.lib / "Frieren", self.disk / "Frieren")
        self.assertEqual(self.shows(self.scan()), [
            ("Frieren", ["Frieren/Season 1/Frieren - 01.mkv", "Frieren/Season 1/Frieren - 02.mkv"]),
            ("Local", ["Local/Local - 01.mkv"])])
        self.assertEqual(library.scan_state["state"], "ok")

    def test_a_linked_season_folder_joins_its_show(self):
        self.files(self.lib / "Show" / "Season 1", "Show - 01.mkv")
        self.files(self.disk / "S2", "Show - 01.mkv")
        self.link(self.lib / "Show" / "Season 2", self.disk / "S2")
        show, = self.scan()
        self.assertEqual([sea["label"] for sea in show["seasons"]], ["Season 1", "Season 2"])

    def test_a_loop_or_two_links_to_one_folder_are_read_once(self):
        self.files(self.disk / "Frieren", "Frieren - 01.mkv")
        self.link(self.disk / "Frieren" / "again", self.disk / "Frieren")  # a loop
        self.link(self.lib / "Frieren", self.disk / "Frieren")
        self.link(self.lib / "Frieren too", self.disk / "Frieren")
        self.files(self.lib / "Local", "Local - 01.mkv")
        self.link(self.lib / "Local alias", self.lib / "Local")  # back into the library
        self.assertEqual(self.shows(self.scan()), [
            ("Frieren", ["Frieren/Frieren - 01.mkv"]), ("Local", ["Local/Local - 01.mkv"])])
        self.assertEqual(self.shows(self.scan()), self.shows(library._scan_library()))  # the cached copy agrees

    def test_a_broken_link_is_skipped_until_its_disk_is_back(self):
        self.files(self.lib / "Show", "Show - 01.mkv")
        self.link(self.lib / "Gone", self.disk / "Gone")
        try:
            os.symlink(self.disk / "Show - 02.mkv", self.lib / "Show" / "Show - 02.mkv")
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"no symlinks here: {e}")
        self.assertEqual(self.shows(self.scan()), [("Show", ["Show/Show - 01.mkv"])])
        self.assertEqual(library.scan_state["state"], "ok")
        # The disk comes back: no folder in the library changed, and still
        # the next scan sees both.
        self.files(self.disk / "Gone", "Gone - 01.mkv")
        (self.disk / "Show - 02.mkv").touch()
        self.assertEqual(self.shows(self.scan()), [
            ("Gone", ["Gone/Gone - 01.mkv"]), ("Show", ["Show/Show - 01.mkv", "Show/Show - 02.mkv"])])


if __name__ == "__main__":
    unittest.main()
