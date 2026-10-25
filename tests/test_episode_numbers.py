"""Episode numbers from file names: the usual "[Group] Title - 05 (1080p)"
release naming, files with no number (numbered in file order, flagged for
Needs a look), and the numbers the user stores for them."""
import unittest

from tests import support
from animehub import database, library
from animehub.config import config, CONFIG, apply_config


class ParseTests(unittest.TestCase):
    def test_release_naming(self):
        for name, n in (("[SubsPlease] Frieren - 01 (1080p) [ABCD1234].mkv", 1),
                        ("86 - 01.mkv", 1), ("Mob Psycho 100 - 03.mkv", 3),
                        ("Frieren - 05v2.mkv", 5), ("Hunter x Hunter (2011) - 148.mkv", 148),
                        ("Re Zero - Starting Life - 05.mkv", 5), ("Show - 01-02.mkv", 1),
                        ("Show - 2nd Season - 03.mkv", 3), ("Frieren S01E05.mkv", 5),
                        ("Show - 1080p.mkv", None), ("Show 05.mkv", None)):
            with self.subTest(name=name):
                self.assertEqual(library.parse_ep_number(name), n)


class GuessTests(unittest.TestCase):
    def library(self, files, folder="Show/Season 1"):
        base = support.TMP / "epnums" / self.id()
        lib = base / "library"
        (lib / folder).mkdir(parents=True, exist_ok=True)
        for f in files:
            (lib / folder / f).touch()
        apply_config(dict(CONFIG, library_root=lib, data_dir=base / "data"))
        config.DATA.mkdir(parents=True, exist_ok=True)
        database.init_db()
        library._scan_memo["sig"] = None
        return library.scan_library()

    def eps(self, scan):
        return [(e["number"], e["rel"].split("/")[-1], bool(e.get("guessed")))
                for e in scan[0]["seasons"][0]["episodes"]]

    def test_named_files_are_not_guessed(self):
        scan = self.library(["[G] Show - 02.mkv", "[G] Show - 03.mkv"])
        self.assertEqual(self.eps(scan), [(2, "[G] Show - 02.mkv", False), (3, "[G] Show - 03.mkv", False)])

    def test_unnamed_files_take_free_numbers_and_are_flagged(self):
        """Mixed folder: the unnumbered file no longer merges into E1 as a
        'version' — it takes the next free number, flagged."""
        scan = self.library(["A.mkv", "Show - 01.mkv", "Show - 02.mkv"])
        self.assertEqual(self.eps(scan), [(1, "Show - 01.mkv", False), (2, "Show - 02.mkv", False), (3, "A.mkv", True)])

    def test_a_lone_file_is_not_flagged(self):
        scan = self.library(["The Movie.mkv"], folder="Show/Specials")
        self.assertFalse(self.eps(scan)[0][2])

    def test_stored_numbers_win_and_clear_the_flag(self):
        scan = self.library(["b.mkv", "a.mkv"])
        ids = {e["rel"].split("/")[-1]: e["id"] for e in scan[0]["seasons"][0]["episodes"]}
        r = library.set_episode_numbers(scan[0]["id"], {ids["a.mkv"]: 7, ids["b.mkv"]: 3})
        self.assertTrue(r["ok"])
        self.assertEqual(self.eps(library.scan_library()), [(3, "b.mkv", False), (7, "a.mkv", False)])

    def test_duplicate_numbers_are_refused(self):
        scan = self.library(["b.mkv", "a.mkv"])
        ids = [e["id"] for e in scan[0]["seasons"][0]["episodes"]]
        r = library.set_episode_numbers(scan[0]["id"], {ids[0]: 4, ids[1]: 4})
        self.assertIn("can't both be episode 4", r["error"])


if __name__ == "__main__":
    unittest.main()
