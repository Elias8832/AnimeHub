"""The server without the window (Decision 51): the two switches, what they
write for the wrapper, and the login entries."""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (sets up throwaway dirs first)
from animehub import background, config as config_module, settings, system
from animehub.config import config, CONFIG_PATH
from tests.support import TMP


def setUpModule():
    support.use_library(support.case("aot"))


class Switches(unittest.TestCase):
    def setUp(self):
        self.wrapper = TMP / "bin dir" / "anime-hub"
        self.wrapper.parent.mkdir(exist_ok=True)
        self.wrapper.write_text("")
        env = mock.patch.dict(os.environ, {"ANIME_HUB_WRAPPER": str(self.wrapper)})
        env.start()
        self.addCleanup(env.stop)
        self.calls = []
        run = mock.patch.object(background, "_systemctl", lambda *a: self.calls.append(a))
        run.start()
        self.addCleanup(run.stop)
        self.addCleanup(self._reset)

    def _reset(self):
        background.set_login(False)
        config.KEEP_RUNNING = config.START_AT_LOGIN = False
        CONFIG_PATH.unlink(missing_ok=True)

    def test_off_without_the_desktop_app(self):
        with mock.patch.dict(os.environ):
            del os.environ["ANIME_HUB_WRAPPER"]
            self.assertFalse(settings.settings_payload()["background_ok"])
            with self.assertRaises(ValueError):
                settings.set_background("keep_running", True)
        self.assertFalse(config.KEEP_RUNNING)

    def test_saved_where_the_wrapper_reads_it(self):
        settings.set_background("keep_running", True)
        self.assertIs(json.loads(CONFIG_PATH.read_text())["keep_running"], True)
        body = settings.settings_payload()
        self.assertEqual((body["background_ok"], body["keep_running"], body["start_at_login"]), (True, True, False))

    def test_config_refuses_a_non_boolean(self):
        path = TMP / "bad.json"
        path.write_text('{"keep_running": "yes", "start_at_login": true}')
        cfg, warnings, _ = config_module.read_config(path)
        self.assertEqual((cfg["keep_running"], cfg["start_at_login"]), (False, True))
        self.assertEqual(len(warnings), 1)

    @unittest.skipIf(system.WINDOWS, "Linux login entries")
    def test_linux_entries_come_and_go(self):
        settings.set_background("start_at_login", True)
        home = background._config_home()
        desktop, service = home / "autostart/anime-hub-server.desktop", home / "systemd/user/anime-hub-server.service"
        line = f'"{self.wrapper}" "--background"'
        self.assertIn(f"Exec={line}\n", desktop.read_text())
        self.assertIn(f"ExecStart={line}\n", service.read_text())
        self.assertIn("WantedBy=graphical-session.target", service.read_text())
        self.assertIn(("enable", "anime-hub-server.service"), self.calls)
        settings.set_background("start_at_login", False)
        self.assertFalse(desktop.exists() or service.exists())
        self.assertIn(("disable", "anime-hub-server.service"), self.calls)

    @unittest.skipIf(system.WINDOWS, "Linux login entries")
    def test_refresh_follows_a_moved_app_and_stays_quiet_otherwise(self):
        settings.set_background("start_at_login", True)
        self.calls.clear()
        background.refresh()
        self.assertEqual(self.calls, [])
        moved = TMP / "moved"
        moved.write_text("")
        with mock.patch.dict(os.environ, {"ANIME_HUB_WRAPPER": str(moved)}):
            background.refresh()
        self.assertIn(f'"{moved}" "--background"', (background._config_home() / "autostart/anime-hub-server.desktop").read_text())

    def test_odd_characters_are_quoted(self):
        self.assertEqual(background._quoted(['a "b" $c 100%']), r'"a \"b\" \$c 100%%"')

    def test_another_config_goes_along(self):
        with mock.patch.object(background, "CONFIG_PATH", Path("/x/other.json")):
            self.assertEqual(background._command()[1:], ["--background", "--config", "/x/other.json"])


class WindowsEntry(unittest.TestCase):
    """The real registry: GitHub runs this on real Windows."""

    @unittest.skipUnless(system.WINDOWS, "Windows registry")
    def test_run_value_comes_and_goes(self):
        import winreg
        wrapper = TMP / "Anime Hub" / "AnimeHub.exe"
        wrapper.parent.mkdir(exist_ok=True)
        wrapper.write_text("")

        def read():
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, background.WINDOWS_RUN_KEY) as key:
                return winreg.QueryValueEx(key, background.WINDOWS_VALUE)[0]

        with mock.patch.dict(os.environ, {"ANIME_HUB_WRAPPER": str(wrapper)}), \
                mock.patch.object(background, "WINDOWS_VALUE", "Anime Hub test"):
            self.addCleanup(background._windows_remove)
            background.set_login(True)
            self.assertEqual(read(), f'"{wrapper}" --background')
            background.set_login(False)
            with self.assertRaises(FileNotFoundError):
                read()
            background.set_login(False)  # already gone: no error


if __name__ == "__main__":
    unittest.main()
