"""Config file behavior (PLAN v0.1: the config file replaces hardcoded
constants; Decision 17.1 username removal; Decision 23.7 root adoption).

A missing or broken config must degrade to usable defaults — the app stays
readable, never tracebacks on a stranger's config typo."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, library, settings
from animehub.config import config, CONFIG, DEFAULT_PORT, apply_config, read_config


def write_config(tmp, obj):
    p = Path(tmp.name) / "config.json"
    p.write_text(json.dumps(obj))
    return p


class ReadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_missing_file_yields_defaults(self):
        cfg, warnings, path = read_config(Path(self.tmp.name) / "absent.json")
        self.assertEqual(cfg["port"], DEFAULT_PORT)
        self.assertIsNone(cfg["library_root"])
        self.assertNotIn("anilist_username", cfg)
        self.assertIsNone(cfg["data_dir"])
        self.assertEqual(warnings, [])

    def test_tilde_expands_and_paths_parse(self):
        p = write_config(self.tmp, {
            "library_root": "~/Anime",
            "data_dir": "~/.data/hub",
        })
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["library_root"], Path.home() / "Anime")
        self.assertEqual(cfg["data_dir"], Path.home() / ".data" / "hub")
        self.assertEqual(warnings, [])

    def test_invalid_port_falls_back_with_warning(self):
        for bad in ("8787", 99999, 0, True):
            with self.subTest(bad=bad):
                p = write_config(self.tmp, {"port": bad})
                cfg, warnings, _ = read_config(p)
                self.assertEqual(cfg["port"], DEFAULT_PORT)
                self.assertEqual(len(warnings), 1)
                self.assertIn("'port'", warnings[0])

    def test_valid_port_applies(self):
        p = write_config(self.tmp, {"port": 9000})
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(warnings, [])

    def test_open_browser_parsing(self):
        p = write_config(self.tmp, {"open_browser": False})
        _, warnings, _ = read_config(p)
        self.assertEqual(len(warnings), 1)
        self.assertIn("no longer exists", warnings[0])

    def test_window_parsing(self):
        p = write_config(self.tmp, {"window": "none"})
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["window"], "none")
        self.assertEqual(warnings, [])
        p = write_config(self.tmp, {"window": "kiosk"})
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["window"], "app")  # falls back to default
        self.assertEqual(len(warnings), 1)

    def test_non_string_path_warns(self):
        p = write_config(self.tmp, {"library_root": 42})
        cfg, warnings, _ = read_config(p)
        self.assertIsNone(cfg["library_root"])
        self.assertEqual(len(warnings), 1)

    def test_empty_value_means_default_without_warning(self):
        p = write_config(self.tmp, {"library_root": "", "data_dir": ""})
        cfg, warnings, _ = read_config(p)
        self.assertIsNone(cfg["library_root"])
        self.assertIsNone(cfg["data_dir"])
        self.assertEqual(warnings, [])

    def test_removed_username_key_warns_and_is_ignored(self):
        """Decision 31: a leftover anilist_username draws a deprecation
        warning and never reaches the config."""
        p = write_config(self.tmp, {"anilist_username": "Stranger"})
        cfg, warnings, _ = read_config(p)
        self.assertNotIn("anilist_username", cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("removed", warnings[0])

    def test_unknown_key_warns_but_rest_loads(self):
        p = write_config(self.tmp, {"library_root": "/a", "typo_key": "x"})
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["library_root"], Path("/a"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("typo_key", warnings[0])

    def test_non_object_config_degrades(self):
        p = write_config(self.tmp, ["not", "an", "object"])
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["port"], DEFAULT_PORT)
        self.assertEqual(len(warnings), 1)

    def test_broken_json_degrades(self):
        p = Path(self.tmp.name) / "config.json"
        p.write_text("{not json")
        cfg, warnings, _ = read_config(p)
        self.assertEqual(cfg["port"], DEFAULT_PORT)
        self.assertEqual(len(warnings), 1)


class ApplyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Snapshot the module constants the binding touches so the real
        # session state is unaffected by the test run.
        self.saved = {name: getattr(config, name) for name in
                      ("DATA", "CACHE", "POSTERS", "EPIMGS", "DB",
                       "LIBRARY", "PORT", "ANILIST_CACHE",
                       "ANILIST_TOKEN_FILE", "WINDOW", "ANILIST_CLIENT_ID",
                       "UPSCALE_BANNERS")}
        self.addCleanup(self.restore)

    def restore(self):
        for name, value in self.saved.items():
            setattr(config, name, value)

    def test_binding_moves_user_paths_and_library(self):
        tmp = Path(self.tmp.name)
        apply_config({"library_root": Path(tmp / "lib"), "port": 9000,
                          "data_dir": tmp / "data",
                          "window": "app"})
        self.assertEqual(config.LIBRARY, tmp / "lib")
        self.assertEqual(config.PORT, 9000)
        self.assertEqual(config.DB, tmp / "data" / "hub.db")
        self.assertEqual(config.CACHE, tmp / "data" / "cache")
        self.assertEqual(config.ANILIST_TOKEN_FILE, tmp / "data" / "anilist_token.json")
        self.assertEqual(config.ANILIST_CACHE, tmp / "data" / "anilist_cache.json")

    def test_none_data_dir_uses_xdg_default(self):
        with patch.dict("os.environ", {"XDG_DATA_HOME": self.tmp.name}):
            apply_config({"library_root": None, "port": 8787,
                              "data_dir": None,
                              "window": "app"})
            self.assertEqual(config.DATA, Path(self.tmp.name) / "anime-hub")


class UsernameTests(unittest.TestCase):
    """Decision 31: the token IS the account — the read username comes
    from the stored token and nowhere else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        for p in (patch.object(config, "ANILIST_TOKEN_FILE", tmp / "anilist_token.json"),
                  patch.object(config, "ANILIST_CACHE", tmp / "anilist_cache.json"),
                  patch.dict(CONFIG)):
            p.start()
            self.addCleanup(p.stop)

    def test_token_user_is_the_account(self):
        anilist.store_token("tok", 3600, {"id": 1, "name": "TokenUser"})
        self.assertEqual(anilist.load_anilist_username(), "TokenUser")

    def test_leftover_config_username_is_ignored(self):
        CONFIG["anilist_username"] = "ConfigUser"
        self.assertIsNone(anilist.load_anilist_username())
        anilist.store_token("tok", 3600, {"id": 1, "name": "TokenUser"})
        self.assertEqual(anilist.load_anilist_username(), "TokenUser")

    def test_legacy_binding_file_is_not_read(self):
        (Path(self.tmp.name) / "anilist.json").write_text(
            json.dumps({"username": "LegacyUser"}))
        self.assertIsNone(anilist.load_anilist_username())

    def test_no_account_payload_without_network(self):
        """No token → the readable no-AniList payload (Decision 6), and
        absolutely no request leaves the machine."""
        with patch.object(anilist, "urlopen") as urlopen:
            res = anilist.anilist_user_data()
            self.assertEqual(res, {"user": None, "entries": []})
            urlopen.assert_not_called()


class UnconfiguredScanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()

    def test_none_library_reports_unconfigured(self):
        with patch.object(config, "LIBRARY", None):
            shows = library.scan_library()
            self.assertEqual(shows, [])
            self.assertEqual(library.scan_state["state"], "unconfigured")

    def test_fresh_db_does_not_seed_none_root(self):
        with patch.object(config, "LIBRARY", None):
            with database.db() as c:
                c.execute("DELETE FROM settings WHERE key='library_root'")
            self.assertIsNone(settings.get_setting("library_root"))


class AdoptRootTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()
        self.saved_library = config.LIBRARY
        self.saved_state = dict(library.scan_state)
        self.addCleanup(self.restore)

    def restore(self):
        config.LIBRARY = self.saved_library
        library.scan_state.update(self.saved_state)

    def test_adopt_records_configured_root(self):
        lib = Path(self.directory.name) / "newroot"
        with patch.object(config, "LIBRARY", lib):
            library.scan_state["root"] = "changed"
            res = settings.adopt_root()
            self.assertTrue(res["ok"])
            self.assertEqual(settings.get_setting("library_root"), str(lib))
            self.assertEqual(library.scan_state["root"], "ok")

    def test_adopt_without_library_is_an_error(self):
        with patch.object(config, "LIBRARY", None):
            res = settings.adopt_root()
            self.assertFalse(res["ok"])
            self.assertIn("error", res)

    def test_adopted_root_lifts_tombstone_guard(self):
        """After adoption, the next merge under the new root records the old
        root's rows as gone (Decision 23.7) — the changed-root guard no
        longer suppresses tombstoning."""
        lib = Path(self.directory.name) / "lib"
        show = lib / "Some Show"
        (show / "Season 1").mkdir(parents=True)
        (show / "Season 1" / "Some Show E1.mkv").write_bytes(b"x")
        with sqlite3.connect(str(config.DB)) as c:
            # A fact from the previous root that the new root cannot find.
            c.execute("INSERT INTO episodes(id,watched,position,duration) "
                      "VALUES('oldroot0000000001',1,0,300)")
        with patch.object(config, "LIBRARY", lib), \
                patch.object(library, "MIN_VALID_SCAN", 0):
            library.scan_state["root"] = "changed"
            settings.adopt_root()
            scan = library.scan_library()
            library.merge_db(scan)
        with sqlite3.connect(str(config.DB)) as c:
            row = c.execute("SELECT 1 FROM tombstones WHERE kind='episode' "
                            "AND key='oldroot0000000001'").fetchone()
            self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
