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
                        ("Show - 1080p.mkv", None), ("Show 05.mkv", None),
                        # a recap between 12 and 13 is no episode 12
                        ("Show - 12.5.mkv", None), ("Show - 12.mkv", 12), ("Show - 12. Title.mkv", 12),
                        # an E-token inside a tag is no episode number
                        ("Show - 05 (BD 1080p DDP5.1 E2).mkv", 5), ("Show - 07 [E3].mkv", 7), ("Show E04 [1080p].mkv", 4)):
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

    def test_a_recap_half_episode_is_its_own_episode(self):
        """'12.5' used to merge into E12 as a version and play in its place."""
        scan = self.library(["Show - 12.mkv", "Show - 12.5.mkv", "Show - 13.mkv"])
        # No number read: a free one, flagged for Needs a look.
        self.assertEqual(self.eps(scan), [(1, "Show - 12.5.mkv", True), (12, "Show - 12.mkv", False),
                                          (13, "Show - 13.mkv", False)])

    def test_one_folder_of_two_seasons_is_two_seasons(self):
        """S02 files in a folder that names no season used to merge into
        S01's episodes as versions."""
        scan = self.library(["Show S01E01.mkv", "Show S01E02.mkv", "Show S02E01.mkv", "Show S02E02.mkv"],
                            folder="Show")
        seasons = [(sea["label"], [(e["number"], e["rel"].split("/")[-1], len(e["files"])) for e in sea["episodes"]])
                   for sea in scan[0]["seasons"]]
        self.assertEqual(seasons, [("Season 1", [(1, "Show S01E01.mkv", 1), (2, "Show S01E02.mkv", 1)]),
                                   ("Season 2", [(1, "Show S02E01.mkv", 1), (2, "Show S02E02.mkv", 1)])])
        self.assertEqual({e["season"] for e in scan[0]["seasons"][1]["episodes"]}, {2})

    def test_one_season_in_a_folder_stays_as_it_was(self):
        """Only S02 files, or a Season folder: the folder decides, as before."""
        scan = self.library(["Show S02E01.mkv", "Show S02E02.mkv"], folder="Show")
        self.assertEqual([sea["label"] for sea in scan[0]["seasons"]], ["Season 1"])
        scan = self.library(["Other S01E01.mkv", "Other S02E01.mkv"], folder="Other/Season 3")
        other = next(s for s in scan if s["title"] == "Other")
        self.assertEqual([sea["label"] for sea in other["seasons"]], ["Season 3"])

    def test_films_loose_in_a_movies_folder_are_one_movie_each(self):
        """Not one "Movies Movie" holding every film as an episode."""
        scan = self.library(["Your Name (2016).mkv", "A Silent Voice.mkv"], folder="Movies")
        self.assertEqual(sorted((s["title"], s["kind"], s["path"], len(list(library.eps_of(s)))) for s in scan),
                         [("A Silent Voice", "movie", "Movies/A Silent Voice.mkv", 1),
                          ("Your Name (2016)", "movie", "Movies/Your Name (2016).mkv", 1)])
        # A film in its own folder is as before.
        scan = self.library(["Film.mkv"], folder="Movies/Weathering With You")
        self.assertIn(("Weathering With You", "Movies/Weathering With You"), [(s["title"], s["path"]) for s in scan])

    def test_duplicate_numbers_are_refused(self):
        scan = self.library(["b.mkv", "a.mkv"])
        ids = [e["id"] for e in scan[0]["seasons"][0]["episodes"]]
        r = library.set_episode_numbers(scan[0]["id"], {ids[0]: 4, ids[1]: 4})
        self.assertIn("can't both be episode 4", r["error"])


if __name__ == "__main__":
    unittest.main()
