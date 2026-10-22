"""Library folder picker (Decision 32): /api/browse lists one level of
folders, /api/library-root writes library_root back to the config file and
takes effect at once — and Decision 23.7's changed-root guard still needs
the explicit adopt step before the old root's rows are tombstoned."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, library, server, settings
from animehub.config import config, CONFIG


class LibraryRootTests(unittest.TestCase):
    PORT = 8787  # hub's configured port; the server binds an ephemeral one

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.tmp = Path(self.directory.name)
        self.cfg = self.tmp / "conf" / "config.json"
        self.lib = self.tmp / "Anime"
        for d in ("Frieren", "Mushishi", ".hidden", "Bocchi the Rock"):
            (self.lib / d).mkdir(parents=True)
        (self.lib / "notes.txt").write_text("not a folder")
        self.patches = [
            patch.object(config, "DB", self.tmp / "hub.db"),
            patch.object(config, "LIBRARY", None),
            patch.object(settings, "CONFIG_PATH", self.cfg),
            patch.dict(CONFIG, {"library_root": None}),
            patch.dict(library.scan_state, {"state": "ok", "walk_errors": 0, "root": "ok"}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    # --- browse ---------------------------------------------------------

    def test_browse_lists_visible_folders_only_sorted(self):
        res = settings.browse_dirs(str(self.lib))
        self.assertEqual(res["path"], str(self.lib))
        self.assertEqual([d["name"] for d in res["dirs"]], ["Bocchi the Rock", "Frieren", "Mushishi"])
        self.assertEqual(res["dirs"][1]["path"], str(self.lib / "Frieren"))
        self.assertEqual(res["parent"], str(self.tmp))
        self.assertIn("Home", [p["label"] for p in res["places"]])

    def test_browse_defaults_to_library_then_home(self):
        self.assertEqual(settings.browse_dirs("")["path"], str(Path.home()))
        with patch.object(config, "LIBRARY", self.lib):
            self.assertEqual(settings.browse_dirs(None)["path"], str(self.lib))

    def test_browse_root_has_no_parent(self):
        self.assertIsNone(settings.browse_dirs("/")["parent"])

    def test_browse_rejects_relative_and_non_folders(self):
        with self.assertRaises(ValueError):
            settings.browse_dirs("Anime")
        with self.assertRaises(ValueError):
            settings.browse_dirs(str(self.lib / "notes.txt"))
        with self.assertRaises(ValueError):
            settings.browse_dirs(str(self.tmp / "nope"))

    def test_browse_normalizes_without_resolving_links(self):
        link = self.tmp / "link"
        os.symlink(self.lib, link)
        self.assertEqual(settings.browse_dirs(str(link) + "/Frieren/..")["path"], str(link))

    # --- set ------------------------------------------------------------

    def test_set_root_creates_config_and_applies_live(self):
        res = settings.set_library_root(str(self.lib) + "/")
        self.assertEqual(res, {"ok": True, "library_root": str(self.lib)})
        self.assertEqual(json.loads(self.cfg.read_text()), {"library_root": str(self.lib)})
        self.assertEqual(config.LIBRARY, self.lib)
        self.assertEqual(settings.settings_payload()["library_root"], str(self.lib))

    def test_set_root_keeps_other_config_keys(self):
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({"port": 9000, "library_root": "/old", "custom": [1]}))
        settings.set_library_root(str(self.lib))
        self.assertEqual(json.loads(self.cfg.read_text()),
                         {"port": 9000, "library_root": str(self.lib), "custom": [1]})
        self.assertFalse(self.cfg.with_name("config.json.tmp").exists())

    def test_set_root_refuses_to_overwrite_a_broken_config(self):
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("{ not json")
        with self.assertRaises(ValueError):
            settings.set_library_root(str(self.lib))
        self.assertEqual(self.cfg.read_text(), "{ not json")
        self.assertIsNone(config.LIBRARY)

    def test_set_root_rejects_missing_folder_without_writing(self):
        with self.assertRaises(ValueError):
            settings.set_library_root(str(self.tmp / "nope"))
        self.assertFalse(self.cfg.exists())

    def test_switch_without_adopt_keeps_changed_root_guard(self):
        """Decision 23.7: a switch away from an indexed root is flagged by
        the scan until adopted — never a silent tombstone."""
        settings.set_setting("library_root", "/old/root")
        settings.set_library_root(str(self.lib))
        self.assertEqual(settings.get_setting("library_root"), "/old/root")
        library.scan_library()
        self.assertEqual(library.scan_state["root"], "changed")

    def test_switch_with_adopt_is_the_confirmed_step(self):
        settings.set_setting("library_root", "/old/root")
        library.scan_state["root"] = "changed"
        settings.set_library_root(str(self.lib), adopt=True)
        self.assertEqual(settings.get_setting("library_root"), str(self.lib))
        self.assertEqual(library.scan_state["root"], "ok")
        library.scan_library()
        self.assertEqual(library.scan_state["root"], "ok")

    # --- routes ---------------------------------------------------------

    def request(self, path, body=None):
        url = f"http://127.0.0.1:{self.bound}{path}"
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{self.PORT}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def serve(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.bound = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

    def test_routes(self):
        self.serve()
        code, res = self.request("/api/browse?path=" + urllib.parse.quote(str(self.lib)))
        self.assertEqual(code, 200)
        self.assertEqual(len(res["dirs"]), 3)
        code, res = self.request("/api/browse?path=relative")
        self.assertEqual(code, 400)
        self.assertIn("full folder path", res["error"])
        self.assertEqual(self.request("/api/library-root", {})[0], 400)
        self.assertEqual(self.request("/api/library-root", {"path": str(self.lib), "adopt": "yes"})[0], 400)
        code, res = self.request("/api/library-root", {"path": str(self.tmp / "nope")})
        self.assertEqual(code, 400)
        self.assertIn("Not a folder", res["error"])
        code, res = self.request("/api/library-root", {"path": str(self.lib), "adopt": True})
        self.assertEqual((code, res["library_root"]), (200, str(self.lib)))
        self.assertEqual(self.request("/api/settings")[1]["library_root"], str(self.lib))


if __name__ == "__main__":
    unittest.main()
