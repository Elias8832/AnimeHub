"""Extra library folders (Decision 42): scanned beside the main folder with
"@<n>/" paths, the main folder's ids untouched, a missing one never
tombstones, the same show in two folders shares one page, Settings adds and
removes them, and the config file carries their fixed numbers."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from animehub import anicli, config as config_mod, database, downloader, library, mapping, pages, settings, streaming
from animehub.config import config, CONFIG


class ExtraTestCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, config, "EXTRA", ())
        self.addCleanup(CONFIG.__setitem__, "library_extra", [])
        self.case = support.case("apothecary")
        support.use_library(self.case)
        self.extra = Path(tempfile.mkdtemp(dir=support.TMP)) / "Anime2"
        self.extra.mkdir()

    def use_extra(self, *dirs):
        """Move these library folders into the extra folder (number 2) and rescan."""
        for d in dirs:
            (self.extra / d).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(config.LIBRARY / d), str(self.extra / d))
        config.EXTRA = ((2, self.extra),)
        return library.scan_library()


class Scan(ExtraTestCase):
    def test_extra_folder_shows_join_the_scan(self):
        before = {ep["rel"]: ep["id"] for s in library.scan_library() for ep in library.eps_of(s)}
        (self.extra / "Frieren" / "Season 1").mkdir(parents=True)
        for n in (1, 2):
            (self.extra / "Frieren" / "Season 1" / f"Frieren - E{n:02d}.mkv").touch()
        (self.extra / "Loose Movie.mkv").touch()
        scan = self.use_extra()
        fr = support.folder(scan, "@2/Frieren")
        self.assertEqual((fr["title"], fr["path_label"]), ("Frieren", "Anime2/Frieren"))
        self.assertEqual([ep["rel"] for ep in library.eps_of(fr)],
                         ["@2/Frieren/Season 1/Frieren - E01.mkv", "@2/Frieren/Season 1/Frieren - E02.mkv"])
        self.assertEqual(library.abs_path(next(library.eps_of(fr))["rel"]),
                         self.extra / "Frieren" / "Season 1" / "Frieren - E01.mkv")
        self.assertEqual(library.rel_of(self.extra / "Frieren"), "@2/Frieren")
        self.assertEqual(library.rel_of(config.LIBRARY / "X" / "E01.mkv"), "X/E01.mkv")
        self.assertIsNone(library.rel_of("/nowhere/E01.mkv"))
        self.assertEqual(support.folder(scan, "@2/Loose Movie.mkv")["path_label"], "Anime2/Loose Movie.mkv")
        # The main folder's shows keep their ids and carry no label.
        main = support.folder(scan, "The Apothecary Diaries")
        self.assertNotIn("path_label", main)
        self.assertEqual({ep["rel"]: ep["id"] for ep in library.eps_of(main)}, before)
        self.assertEqual(library.scan_state["extra"], {"2": "ok"})

    def test_a_missing_extra_folder_never_tombstones(self):
        scan = self.use_extra("The Apothecary Diaries/Season 2")
        moved = [ep["id"] for ep in library.eps_of(support.folder(scan, "@2/The Apothecary Diaries"))]
        with database.db() as c:
            c.executemany("INSERT INTO episodes(id, watched) VALUES(?, 1)", [(i,) for i in moved])
        library.merge_db(scan)
        shutil.rmtree(self.extra)
        scan = library.scan_library()
        self.assertEqual(library.scan_state["extra"], {"2": "missing"})
        library.merge_db(scan)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM episodes WHERE watched=1").fetchone()[0], len(moved))


class OnePage(ExtraTestCase):
    def test_the_same_show_in_two_folders_shares_its_page(self):
        scan = self.use_extra("The Apothecary Diaries/Season 2")
        case = dict(self.case, match={"The Apothecary Diaries": 161645, "@2/The Apothecary Diaries": 161645})
        support.match_library(case, scan)
        for mod, name in ((streaming, "stream_resolve_async"), (pages, "franchise_build_async"),
                          (mapping, "refresh_entry_art")):
            p = mock.patch.object(mod, name)
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(anicli, "anicli_path", return_value=None):
            page = pages.franchise_view(161645)
        slots = {e["id"]: e["slots"] for e in page["entries"]}
        disk2 = support.season(support.folder(scan, "@2/The Apothecary Diaries"), "Season 2")["episodes"]
        self.assertEqual([s["id"] for s in slots[176301]], [ep["id"] for ep in disk2])
        self.assertEqual(len(slots[161645]), 24)
        # Downloads for that entry go where it lives: the extra folder.
        link = {"media_id": 176301, "meta": {"title": "The Apothecary Diaries Season 2"}, "title": "", "query": ""}
        self.assertEqual(downloader.stream_download_dir(link, scan), self.extra / "The Apothecary Diaries" / "Season 2")


class Settings(ExtraTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = Path(tempfile.mkdtemp(dir=support.TMP)) / "config.json"
        self.cfg.write_text(json.dumps({"library_root": str(config.LIBRARY)}))
        p = mock.patch.object(settings, "CONFIG_PATH", self.cfg)
        p.start()
        self.addCleanup(p.stop)

    def written(self):
        return json.loads(self.cfg.read_text())["library_extra"]

    def test_add_and_remove_keep_their_numbers(self):
        b = self.extra.parent / "B"
        b.mkdir()
        self.assertEqual(settings.add_library_extra(str(self.extra))["id"], 2)
        self.assertEqual(settings.add_library_extra(str(b))["id"], 3)
        self.assertEqual(self.written(), [{"id": 2, "path": str(self.extra)}, {"id": 3, "path": str(b)}])
        self.assertEqual(config.EXTRA, ((2, self.extra), (3, b)))
        self.assertEqual([x["id"] for x in settings.settings_payload()["library_extra"]], [2, 3])
        settings.remove_library_extra(2)
        self.assertEqual(self.written(), [{"id": 3, "path": str(b)}])
        c = self.extra.parent / "C"
        c.mkdir()
        self.assertEqual(settings.add_library_extra(str(c))["id"], 4)  # 2 is never handed to another folder
        self.assertEqual(settings.add_library_extra(str(self.extra))["id"], 2)  # the same folder gets its number back
        with self.assertRaises(ValueError):
            settings.remove_library_extra(9)

    def test_refuses_folders_inside_each_other(self):
        for bad in (config.LIBRARY, config.LIBRARY / "The Apothecary Diaries", config.LIBRARY.parent, "relative/path"):
            with self.assertRaises(ValueError, msg=str(bad)):
                settings.add_library_extra(str(bad))
        settings.add_library_extra(str(self.extra))
        (self.extra / "inner").mkdir()
        with self.assertRaises(ValueError):
            settings.add_library_extra(str(self.extra / "inner"))
        with self.assertRaises(ValueError):
            settings.add_library_extra(str(self.extra))


class ConfigFile(unittest.TestCase):
    def load(self, value):
        path = Path(tempfile.mkdtemp(dir=support.TMP)) / "config.json"
        path.write_text(json.dumps({"library_extra": value}))
        cfg, warnings, _ = config_mod.read_config(path)
        return cfg["library_extra"], warnings

    def test_library_extra(self):
        self.assertEqual(self.load([{"id": 2, "path": "/mnt/B"}]), ([(2, Path("/mnt/B"))], []))
        for bad in (["/mnt/B"], [{"id": 1, "path": "/mnt/B"}], [{"id": 2, "path": "/a"}, {"id": 2, "path": "/b"}],
                    [{"id": True, "path": "/a"}], {"id": 2}):
            extra, warnings = self.load(bad)
            self.assertEqual(extra, [], bad)
            self.assertIn("library_extra", warnings[0])


if __name__ == "__main__":
    unittest.main()
