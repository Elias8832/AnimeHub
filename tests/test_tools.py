"""Windows sets itself up (Decision 87): one click installs the missing
tools through Scoop, and a zip copy puts itself in the Start menu once.
Scoop and PowerShell are stand-ins here; nothing is installed."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import database, remote, settings, system, tools


def setUpModule():
    database.init_db()


def wait_done(limit=5):
    end = time.time() + limit
    while time.time() < end and tools._job["state"] == "working":
        time.sleep(0.02)


class Plan(unittest.TestCase):
    def have(self, present):
        return mock.patch.object(tools, "have", side_effect=lambda step: step in present)

    def test_a_bare_pc_gets_everything_in_order(self):
        with self.have(set()):
            self.assertEqual(tools.plan(), ["scoop", "git", "extras", "mpv", "ani-cli", "yt-dlp"])

    def test_only_what_is_missing(self):
        with self.have({"scoop", "git", "extras", "mpv", "ani-cli"}):
            self.assertEqual(tools.plan(), ["yt-dlp"])

    def test_nothing_missing_needs_no_scoop(self):
        with self.have({"git", "mpv", "ani-cli", "yt-dlp"}):
            self.assertEqual(tools.plan(), [])

    def test_only_playback_skips_ani_cli_git_and_yt_dlp(self):
        with self.have(set()):
            self.assertEqual(tools.plan(["play"]), ["scoop", "extras", "mpv"])
            self.assertEqual(tools.plan(["play", "online"]), ["scoop", "git", "extras", "mpv", "ani-cli"])
            self.assertEqual(tools.plan(["download"]), ["scoop", "git", "extras", "ani-cli", "yt-dlp"])  # downloads run through ani-cli

    def test_groups_show_only_what_is_missing(self):
        with self.have({"mpv"}), mock.patch.object(tools, "can", return_value=True):
            self.assertEqual([g["id"] for g in tools.payload()["groups"]], ["online", "download"])

    def test_extras_only_for_what_lives_there(self):
        with self.have({"scoop", "mpv", "ani-cli"}):
            self.assertEqual(tools.plan(), ["git", "yt-dlp"])


class Install(unittest.TestCase):
    def setUp(self):
        tools._job.update(state="idle", steps=[], error=None)

    def test_only_windows_with_powershell(self):
        with mock.patch.object(system, "WINDOWS", False):
            self.assertIn("error", tools.install())
            self.assertFalse(tools.payload()["can"])
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "powershell", return_value=None):
            self.assertIn("error", tools.install())

    def test_nothing_picked_is_refused(self):
        with mock.patch.object(tools, "can", return_value=True):
            self.assertIn("error", tools.install([]))
            self.assertIn("error", tools.install(["nonsense"]))

    def test_phones_cannot_ask(self):
        self.assertIn("/api/tools-install", remote.BLOCKED)

    def test_steps_run_in_order_and_count_only_what_is_there(self):
        there, ran = set(), []

        def run(argv):
            ran.append(argv[-1])
            there.add({"git": "git", "extras": "extras", "extras/mpv": "mpv", "ani-cli": "ani-cli", "yt-dlp": "yt-dlp"}[argv[-1]])
            return ""

        with mock.patch.object(tools, "can", return_value=True), \
             mock.patch.object(tools, "have", side_effect=lambda s: s == "scoop" or s in there), \
             mock.patch.object(system, "scoop_script", return_value="scoop.ps1"), \
             mock.patch.object(system, "powershell", return_value="powershell.exe"), \
             mock.patch.object(system, "refresh_path"), mock.patch.object(tools, "_run", side_effect=run):
            self.assertEqual(tools.install(), {"ok": True})
            wait_done()
        self.assertEqual(ran, ["git", "extras", "extras/mpv", "ani-cli", "yt-dlp"])
        self.assertEqual(tools._job["state"], "done")
        self.assertTrue(all(s["state"] == "ok" for s in tools._job["steps"]))

    def test_a_step_that_leaves_nothing_stops_and_says_so(self):
        with mock.patch.object(tools, "can", return_value=True), \
             mock.patch.object(tools, "have", side_effect=lambda s: s in ("scoop", "extras")), \
             mock.patch.object(system, "scoop_script", return_value="scoop.ps1"), \
             mock.patch.object(system, "powershell", return_value="powershell.exe"), \
             mock.patch.object(system, "refresh_path"), \
             mock.patch.object(tools, "_run", return_value="Last output: ERROR no space") as run:
            tools.install()
            wait_done()
        self.assertEqual(run.call_count, 1)  # Git failed, nothing after it ran
        self.assertEqual(tools._job["state"], "failed")
        self.assertIn("Git for Windows didn't install", tools._job["error"])
        self.assertIn("no space", tools._job["error"])
        self.assertEqual([s["state"] for s in tools._job["steps"]], ["failed", "wait", "wait", "wait"])

    def test_scoops_installer_is_looked_at_before_it_runs(self):
        class Reply:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return self.body[:n]

        with mock.patch.object(system, "powershell", return_value="powershell.exe"):
            with mock.patch.object(tools, "urlopen", return_value=Reply(b"<html>blocked</html>")):
                self.assertRaises(ValueError, tools._command, "scoop")
            with mock.patch.object(tools, "urlopen", side_effect=OSError):
                self.assertRaises(ValueError, tools._command, "scoop")
            with mock.patch.object(tools, "urlopen", return_value=Reply(b"# Scoop installer\nWrite-Host hi")), \
                 mock.patch.object(system, "is_admin", return_value=True):
                argv = tools._command("scoop")
        self.assertTrue(Path(argv[-2]).is_file())
        self.assertEqual(argv[-1], "-RunAsAdmin")

    def test_settings_carry_it(self):
        self.assertEqual(set(settings.settings_payload()["tools_install"]), {"can", "missing", "groups", "job"})


class Path_(unittest.TestCase):
    def test_new_folders_join_the_path_once(self):
        d = tempfile.mkdtemp(dir=support.TMP)
        old = os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertEqual(system.refresh_path([d, d, str(Path(d) / "missing")]), [d])
        self.assertEqual(system.refresh_path([d]), [])
        self.assertTrue(os.environ["PATH"].endswith(os.pathsep + d))


class StartMenu(unittest.TestCase):
    def setUp(self):
        settings.set_setting("start_menu_exe", "")
        self.folder = Path(tempfile.mkdtemp(dir=support.TMP))
        self.exe = str(self.folder.parent / "AnimeHub" / "AnimeHub.exe")

    def add(self, worked=True):
        def make(exe, folder):
            if worked:
                (Path(folder) / system.SHORTCUT).write_text(exe)
            return worked
        return mock.patch.object(system, "add_start_menu", side_effect=make)

    def test_once_then_kept(self):
        with mock.patch.object(system, "WINDOWS", True), self.add() as add:
            self.assertEqual(tools.start_menu_once(self.exe, self.folder), "added")
            self.assertEqual(tools.start_menu_once(self.exe, self.folder), "kept")
        self.assertEqual(add.call_count, 1)

    def test_a_deleted_shortcut_stays_deleted(self):
        with mock.patch.object(system, "WINDOWS", True), self.add() as add:
            tools.start_menu_once(self.exe, self.folder)
            (self.folder / system.SHORTCUT).unlink()
            self.assertEqual(tools.start_menu_once(self.exe + "x", self.folder), "kept")
        self.assertEqual(add.call_count, 1)

    def test_a_moved_folder_gets_a_new_one(self):
        with mock.patch.object(system, "WINDOWS", True), self.add() as add:
            tools.start_menu_once(self.exe, self.folder)
            self.assertEqual(tools.start_menu_once(self.exe + "x", self.folder), "added")
        self.assertEqual(add.call_count, 2)

    def test_a_failure_tries_again_next_start(self):
        with mock.patch.object(system, "WINDOWS", True), self.add(worked=False):
            self.assertIsNone(tools.start_menu_once(self.exe, self.folder))
        self.assertEqual(settings.get_setting("start_menu_exe"), "")

    def test_not_off_windows_nor_for_a_checkout(self):
        with mock.patch.object(system, "WINDOWS", False), self.add() as add:
            self.assertIsNone(tools.start_menu_once(self.exe, self.folder))
        with mock.patch.object(system, "WINDOWS", True), self.add() as add:
            self.assertIsNone(tools.start_menu_once(None, self.folder))  # this checkout isn't a zip copy
        add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
