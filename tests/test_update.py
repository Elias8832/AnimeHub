"""New versions (Decision 58): which release counts, never a check unasked,
and an AppImage is only replaced by what it was promised."""

import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import database, remote, settings, update
from animehub.config import config

ELF = b"\x7fELF\x02\x01\x01\x00AI\x02" + b"x" * 500
APP = update.asset_name("appimage")


def release(tag, *names, draft=False, digest="sha256:abc"):
    return {"tag_name": tag, "draft": draft, "html_url": f"https://github.com/x/{tag}",
            "assets": [{"name": n, "digest": digest, "browser_download_url": f"{update.DOWNLOADS}{tag}/{n}"} for n in names]}


def setUpModule():
    config.DATA.mkdir(parents=True, exist_ok=True)
    database.init_db()


class Reply(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Versions(unittest.TestCase):
    def test_this_projects_numbers_are_decimals(self):
        order = ["v0.1.0", "v0.2", "v0.25", "v0.3", "v0.31", "v0.4", "v0.5", "v1.0", "v1.0.1"]
        numbers = [update.number(t) for t in order]
        self.assertEqual(numbers, sorted(numbers))
        self.assertGreater(update.number("v0.4"), update.number("v0.25"))

    def test_odd_tags_are_no_version(self):
        for tag in ("nightly", "v0.5-rc1", "", None):
            self.assertIsNone(update.number(tag))

    def test_picks_the_newest_this_copy_can_use(self):
        got = update.pick([release("v0.4", APP), release("v0.6", APP), release("v0.5", APP)], "0.4", "appimage")
        self.assertEqual(got["version"], "0.6")
        self.assertEqual(got["sha256"], "abc")
        self.assertEqual(got["download"], f"{update.DOWNLOADS}v0.6/{APP}")

    def test_nothing_newer_says_nothing(self):
        self.assertIsNone(update.pick([release("v0.4", APP), release("v0.31", APP)], "0.4", "appimage"))

    def test_a_point_release_without_a_zip_skips_windows(self):
        rels = [release("v0.51", APP), release("v0.5", APP, "AnimeHub-windows-x64.zip")]
        self.assertEqual(update.pick(rels, "0.4", "windows")["version"], "0.5")
        self.assertEqual(update.pick(rels, "0.4", "appimage")["version"], "0.51")
        self.assertEqual(update.pick(rels, "0.4", "git")["version"], "0.51")

    def test_a_git_copy_needs_no_file(self):
        self.assertEqual(update.pick([release("v0.5")], "0.4", "git")["version"], "0.5")

    def test_drafts_are_skipped(self):
        self.assertIsNone(update.pick([release("v0.9", APP, draft=True)], "0.4", "appimage"))

    def test_no_checksum_or_foreign_link_means_no_one_click(self):
        got = update.pick([release("v0.5", APP, digest=None)], "0.4", "appimage")
        self.assertEqual((got["version"], got["download"]), ("0.5", None))
        odd = release("v0.5", APP)
        odd["assets"][0]["browser_download_url"] = "https://example.com/" + APP
        self.assertIsNone(update.pick([odd], "0.4", "appimage")["download"])


def rolling(n, when, *names, commit="ab12cd3"):
    r = release("unstable", *names)
    r["body"] = f"Notes.\n\n<!-- build {n} {commit} {when} -->"
    return r


def stable(tag, when, *names):
    return {**release(tag, *names), "published_at": when}


class Unstable(unittest.TestCase):
    """Decision 76: the rolling release is one build; a build beats a build
    by number and a release by time."""
    MINE = stable("v0.4", "2026-10-31T17:00:00Z", APP)

    def test_a_newer_build_is_offered(self):
        got = update.pick_unstable([rolling(12, "2026-11-09T10:00:00Z", APP), self.MINE], "0.4", {"number": 11, "commit": "x"}, "appimage")
        self.assertEqual((got["label"], got["build"], got["channel"]), ("Build 12", 12, "unstable"))
        self.assertEqual(got["download"], f"{update.DOWNLOADS}unstable/{APP}")

    def test_the_same_build_says_nothing(self):
        self.assertIsNone(update.pick_unstable([rolling(12, "2026-11-09T10:00:00Z", APP)], "0.4", {"number": 12, "commit": "x"}, "appimage"))

    def test_a_release_copy_gets_a_build_made_after_its_release(self):
        rels = [rolling(12, "2026-11-09T10:00:00Z", APP), self.MINE]
        self.assertEqual(update.pick_unstable(rels, "0.4", None, "appimage")["build"], 12)
        old = [rolling(9, "2026-10-30T10:00:00Z", APP), self.MINE]
        self.assertIsNone(update.pick_unstable(old, "0.4", None, "appimage"))

    def test_a_release_newer_than_the_build_wins(self):
        rels = [stable("v0.5", "2026-11-10T09:00:00Z", APP), rolling(12, "2026-11-09T10:00:00Z", APP), self.MINE]
        got = update.pick_unstable(rels, "0.4", {"number": 12, "commit": "x"}, "appimage")
        self.assertEqual((got["label"], got["channel"]), ("v0.5", "stable"))
        later = [rolling(14, "2026-11-11T10:00:00Z", APP), *rels[:1]]
        self.assertEqual(update.pick_unstable(later, "0.4", {"number": 12, "commit": "x"}, "appimage")["build"], 14)

    def test_without_the_build_line_or_this_copys_file_it_says_nothing(self):
        bare = release("unstable", APP)
        self.assertIsNone(update.pick_unstable([bare], "0.4", {"number": 1, "commit": "x"}, "appimage"))
        self.assertIsNone(update.pick_unstable([rolling(12, "2026-11-09T10:00:00Z", APP)], "0.4", {"number": 1, "commit": "x"}, "windows"))

    def test_stable_never_offers_the_rolling_release(self):
        self.assertIsNone(update.pick([rolling(99, "2030-01-01T00:00:00Z", APP)], "0.4", "appimage"))


class Channels(unittest.TestCase):
    def setUp(self):
        for key in ("update_channel", "update_found"):
            settings.set_setting(key, "")
        self.addCleanup(settings.set_setting, "update_channel", "")

    def test_stable_by_default(self):
        self.assertEqual(update.channel(), "stable")
        self.assertEqual(update.payload()["channel"], "stable")

    def test_what_was_found_belongs_to_its_channel(self):
        settings.set_setting("update_found", json.dumps({"version": "0.4", "build": 12, "channel": "unstable"}))
        self.assertIsNone(update.stored())
        settings.set_setting("update_channel", "unstable")
        with mock.patch.object(update, "BUILD", {"number": 11, "commit": "x"}):
            self.assertEqual(update.stored()["build"], 12)
        with mock.patch.object(update, "BUILD", {"number": 12, "commit": "x"}):
            self.assertIsNone(update.stored())  # this copy is that build now

    def test_check_reads_the_channel(self):
        settings.set_setting("update_channel", "unstable")
        body = json.dumps([rolling(12, "2026-11-09T10:00:00Z")]).encode()
        with mock.patch.object(update, "urlopen", return_value=Reply(body)), mock.patch.object(update, "kind", return_value="git"), \
                mock.patch.object(update, "BUILD", {"number": 3, "commit": "x"}):
            self.assertEqual(update.check()["found"]["build"], 12)

class Asking(unittest.TestCase):
    def setUp(self):
        for key in ("update_check", "update_checked", "update_found", "update_asked"):
            settings.set_setting(key, "")

    def test_off_means_no_check(self):
        settings.set_automation("update_check", False)
        self.assertFalse(update.due(time.time() + 10 * update.EVERY))

    def test_on_means_once_a_day(self):
        settings.set_automation("update_check", True)
        self.assertTrue(update.due())
        settings.set_setting("update_checked", str(int(time.time())))
        self.assertFalse(update.due())
        self.assertTrue(update.due(time.time() + update.EVERY + 1))

    def test_off_by_default_and_a_found_version_goes_stale(self):
        self.assertFalse(settings.AUTOMATION_DEFAULTS["update_check"])
        settings.set_setting("update_found", json.dumps({"version": "0.1"}))
        self.assertIsNone(update.stored())  # this copy is past it, e.g. after updating
        settings.set_setting("update_found", json.dumps({"version": "99.0"}))
        self.assertEqual(update.payload()["found"]["version"], "99.0")

    def test_check_keeps_what_it_found_and_a_failure_keeps_the_old_answer(self):
        body = json.dumps([release("v99.0")]).encode()
        with mock.patch.object(update, "urlopen", return_value=Reply(body)), mock.patch.object(update, "kind", return_value="git"):
            self.assertEqual(update.check()["found"]["version"], "99.0")
        with mock.patch.object(update, "urlopen", side_effect=OSError):
            self.assertEqual(update.check(), {"error": "Could not reach GitHub."})
        self.assertEqual(update.stored()["version"], "99.0")

    def test_only_with_a_vpn(self):
        settings.set_automation("update_check", True)
        settings.set_automation("update_vpn_only", True)
        self.addCleanup(settings.set_automation, "update_vpn_only", False)
        for state in ({"active": False, "known": True}, {"active": False, "known": False}):
            with mock.patch.object(settings, "vpn_status", return_value=state), \
                    mock.patch.object(update, "urlopen", side_effect=AssertionError("asked GitHub")):
                self.assertFalse(update.due())
                self.assertEqual(update.check(), {"error": update.NO_VPN})
                settings.set_setting("update_found", json.dumps({"version": "99.0", "download": "x"}))
                with mock.patch.object(update, "kind", return_value="appimage"):
                    self.assertEqual(update.install(), {"error": update.NO_VPN})
        with mock.patch.object(settings, "vpn_status", return_value={"active": True, "known": True}):
            self.assertTrue(update.due())

    def test_phones_cant_use_it(self):
        self.assertIn("/api/update-check", remote.BLOCKED)
        self.assertIn("/api/update-install", remote.BLOCKED)


class Restarting(unittest.TestCase):
    def setUp(self):
        self.addCleanup(update._job.update, dict(update._job))
        self.addCleanup(update._restart.update, wanted=False)

    def test_only_after_an_install_and_not_while_busy(self):
        update._job.update(state="idle")
        self.assertEqual(update.restart(), {"error": "Install the new version first."})
        update._job.update(state="done")
        self.assertEqual(update.restart("Something is playing."), {"error": "Something is playing."})
        self.assertFalse(update.restart_wanted())
        self.assertEqual(update.restart(), {"ok": True})
        self.assertTrue(update.restart_wanted())

    def test_what_starts_again(self):
        argv = ["/old/hub.py", "--config", "/c.json"]
        with mock.patch.dict(os.environ, {"APPIMAGE": "/apps/AnimeHub.AppImage"}):
            self.assertEqual(update.restart_command(argv, "appimage"),
                             ["/apps/AnimeHub.AppImage", "--hub", "--config", "/c.json", "--no-browser"])
        for how in ("git", "windows"):
            self.assertEqual(update.restart_command(argv + ["--no-browser"], how),
                             [__import__("sys").executable, str(update.REPO / "hub.py"), "--config", "/c.json", "--no-browser"])

    def test_the_page_can_tell_a_new_start(self):
        self.assertEqual(update.payload()["started"], update.STARTED)

    def test_busy_says_why(self):
        from animehub import downloader, player, server
        self.assertIsNone(server.restart_busy())
        with mock.patch.dict(player._active, {"x": object()}):
            self.assertIn("playing", server.restart_busy())
        with mock.patch.object(downloader, "_downloads", [{"status": "running"}]):
            self.assertIn("download", server.restart_busy())

    def test_phones_cant_restart(self):
        self.assertIn("/api/update-restart", remote.BLOCKED)


class Replacing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "AnimeHub.AppImage"
        self.app.write_bytes(b"old")
        self.app.chmod(0o755)
        update._job.update(state="idle", percent=0, error=None)

    def run_with(self, body, sha=None):
        found = {"download": update.DOWNLOADS + "x", "sha256": sha or hashlib.sha256(body).hexdigest()}
        with mock.patch.object(update, "urlopen", return_value=Reply(body)):
            update._replace(found, self.app)
        return update._job

    def left(self):
        return sorted(p.name for p in self.app.parent.iterdir())

    def test_a_good_download_replaces_it(self):
        self.assertEqual(self.run_with(ELF)["state"], "done")
        self.assertEqual(self.app.read_bytes(), ELF)
        self.assertTrue(os.access(self.app, os.X_OK))
        self.assertEqual(self.left(), [self.app.name])

    def test_a_wrong_checksum_changes_nothing(self):
        self.assertEqual(self.run_with(ELF, sha="0" * 64)["state"], "failed")
        self.assertEqual(self.app.read_bytes(), b"old")
        self.assertEqual(self.left(), [self.app.name])

    def test_something_else_than_an_appimage_changes_nothing(self):
        self.assertEqual(self.run_with(b"<html>not found</html>")["state"], "failed")
        self.assertEqual(self.app.read_bytes(), b"old")

    def test_a_cut_off_download_changes_nothing(self):
        with mock.patch.object(update, "urlopen", side_effect=OSError):
            update._replace({"download": "x", "sha256": "0"}, self.app)
        self.assertEqual(update._job["state"], "failed")
        self.assertEqual(self.app.read_bytes(), b"old")

    def test_only_an_appimage_or_a_git_copy_can(self):
        settings.set_setting("update_found", json.dumps({"version": "99.0", "download": "x"}))
        with mock.patch.dict(os.environ), mock.patch.object(update, "REPO", self.app.parent):  # no .git there
            os.environ.pop("APPIMAGE", None)
            self.assertEqual(update.install(), {"error": "This copy can't update itself."})


def git(cwd, *args):
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitCopy(unittest.TestCase):
    """Decision 78: a git copy installs with a click, by moving its master
    forward to the build's commit or the release's tag. Real git, a real
    "origin" folder, no network."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin, self.copy = root / "origin", root / "copy"
        self.origin.mkdir()
        git(self.origin, "init", "-q", "-b", "master")
        self.commit(self.origin, "hub.py", "one")
        git(root, "clone", "-q", str(self.origin), str(self.copy))
        self.new = self.commit(self.origin, "hub.py", "two")
        git(self.origin, "tag", "-a", "v99.0", "-m", "release")
        update._job.update(state="idle", percent=0, error=None, note=None)
        self.addCleanup(update._job.update, state="idle", percent=0, error=None, note=None)
        for patch in (mock.patch.object(update, "REPO", self.copy), mock.patch.object(update, "kind", return_value="git"),
                      mock.patch.object(update, "BUILD", None)):
            patch.start()
            self.addCleanup(patch.stop)
        settings.set_setting("update_channel", "unstable")
        self.addCleanup(settings.set_setting, "update_channel", "")
        self.addCleanup(settings.set_setting, "update_found", "")

    def commit(self, repo, name, text):
        (repo / name).write_text(text)
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", text)
        return git(repo, "rev-parse", "HEAD")

    def build(self, sha):
        return {"version": "0.4", "label": "Build 12", "channel": "unstable", "build": 12, "commit": sha[:7], "sha": sha}

    def test_a_build_is_new_until_the_copy_has_its_commit(self):
        rels = [rolling(12, "2026-11-09T10:00:00Z", commit=self.new)]
        self.assertEqual(update.pick_unstable(rels, "0.4", None, "git", update.has_commit)["sha"], self.new)
        old = git(self.copy, "rev-parse", "HEAD")
        self.assertIsNone(update.pick_unstable([rolling(11, "2026-11-09T10:00:00Z", commit=old)], "0.4", None, "git", update.has_commit))

    def test_install_moves_master_to_the_build(self):
        settings.set_setting("update_found", json.dumps(self.build(self.new)))
        self.assertTrue(update.payload()["installable"])
        update._pull(update.stored())
        self.assertEqual((update._job["state"], update._job["error"]), ("done", None))
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), self.new)
        self.assertEqual((self.copy / "hub.py").read_text(), "two")
        self.assertIsNotNone(update.stored())  # still says "open it again"
        update._job.update(state="idle")
        self.assertIsNone(update.stored())  # after the restart, it is that build

    def test_the_version_row_names_the_build_only_while_it_is_that_commit(self):
        self.addCleanup(settings.set_setting, "update_git_build", "")
        settings.set_setting("update_git_build", "")
        self.assertIsNone(update.payload()["git_build"])  # never told: the commit alone
        settings.set_setting("update_found", json.dumps(self.build(self.new)))
        update._pull(update.stored())
        self.assertEqual(update.payload()["git_build"], 12)  # installed that build
        self.commit(self.copy, "hub.py", "mine")
        self.assertIsNone(update.payload()["git_build"])  # moved off it by hand
        git(self.copy, "reset", "-q", "--hard", self.new)
        settings.set_setting("update_git_build", "")
        body = json.dumps([rolling(12, "2026-11-09T10:00:00Z", commit=self.new)]).encode()
        with mock.patch.object(update, "urlopen", return_value=Reply(body)):
            update.check()
        self.assertEqual(update.payload()["git_build"], 12)  # a check saw it is on the newest build

    def test_a_check_asks_github_for_an_older_commits_build(self):
        self.addCleanup(settings.set_setting, "update_git_build", "")
        settings.set_setting("update_git_build", "")
        head = git(self.copy, "rev-parse", "HEAD")
        asked = []

        def answer(req, timeout=None):
            asked.append(req.full_url)
            if req.full_url.startswith(update.RUNS):
                return Reply(json.dumps({"workflow_runs": [{"run_number": 205, "head_sha": head}]}).encode())
            return Reply(json.dumps([rolling(210, "2026-11-09T10:00:00Z", commit=self.new)]).encode())

        with mock.patch.object(update, "urlopen", side_effect=answer):
            update.check()
            self.assertEqual(update.payload()["git_build"], 205)
            update.check()  # known now: GitHub isn't asked again
        self.assertEqual(sum(u.startswith(update.RUNS) for u in asked), 1)
        self.assertTrue(asked[1].endswith(head))
        settings.set_setting("update_git_build", "")
        with mock.patch.object(update, "urlopen", side_effect=lambda req, timeout=None: Reply(
                b'{"workflow_runs": []}' if req.full_url.startswith(update.RUNS) else b"[]")):
            update.check()
        self.assertIsNone(update.payload()["git_build"])  # no run for it: the commit alone

    def test_a_build_found_before_shas_were_kept_whole_still_works(self):
        old = {k: v for k, v in self.build(self.new).items() if k != "sha"}
        settings.set_setting("update_found", json.dumps(old))
        self.assertTrue(update.payload()["installable"])
        update._pull(update.stored())
        self.assertEqual((update._job["state"], git(self.copy, "rev-parse", "HEAD")), ("done", self.new))
        update._job.update(state="idle")
        self.assertIsNone(update.stored())  # the copy has it: no "Build 12 is out"
        self.assertEqual(update.payload()["head"], self.new[:7])

    def test_install_moves_master_to_a_release(self):
        settings.set_setting("update_channel", "")
        settings.set_setting("update_found", json.dumps({"version": "99.0", "channel": "stable", "tag": "v99.0"}))
        self.assertTrue(update.installable(update.stored()))
        update._pull(update.stored())
        self.assertEqual(update._job["state"], "done")
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), self.new)
        self.assertIsNone(update._job["note"])

    def test_a_changed_window_app_is_named(self):
        (self.origin / "desktop").mkdir()
        sha = self.commit(self.origin, "desktop/main.rs", "fn main() {}")
        update._pull(self.build(sha))
        self.assertEqual(update._job["state"], "done")
        self.assertIn("cargo build", update._job["note"])

    def test_own_changes_or_commits_or_another_branch_change_nothing(self):
        before = git(self.copy, "rev-parse", "HEAD")
        (self.copy / "hub.py").write_text("mine")
        update._pull(self.build(self.new))
        self.assertIn("changes of its own", update._job["error"])
        git(self.copy, "checkout", "-q", "hub.py")
        (self.copy / "notes.txt").write_text("untracked is fine")
        self.commit(self.copy, "local.txt", "my commit")
        update._pull(self.build(self.new))
        self.assertIn("commits of its own", update._job["error"])
        git(self.copy, "reset", "-q", "--hard", before)
        git(self.copy, "checkout", "-q", "-b", "feature")
        update._pull(self.build(self.new))
        self.assertIn("isn't on master", update._job["error"])
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), before)
        self.assertTrue((self.copy / "notes.txt").exists())

    def test_an_unknown_commit_changes_nothing(self):
        before = git(self.copy, "rev-parse", "HEAD")
        update._pull(self.build("f" * 40))
        self.assertEqual(update._job["state"], "failed")
        self.assertEqual(git(self.copy, "rev-parse", "HEAD"), before)

    def test_install_starts_the_git_path(self):
        settings.set_setting("update_found", json.dumps(self.build(self.new)))
        with mock.patch.object(update, "vpn_missing", return_value=False), mock.patch.object(update, "_pull") as pull:
            self.assertEqual(update.install(), {"ok": True})
        for _ in range(50):
            if pull.called:
                break
            time.sleep(0.02)
        pull.assert_called_once()


def windows_zip(files):
    """A zip like build.ps1's: one AnimeHub/ folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name if name.startswith("../") else "AnimeHub/" + name, data)
    return buf.getvalue()


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


OLD = {"AnimeHub.exe": b"MZ old exe", "README.txt": b"readme", "python/python.exe": b"MZ python",
       "python/python312._pth": b"python312.zip\n.\n../app\n", "app/hub.py": b"old hub",
       "app/animehub/update.py": b"old update", "app/animehub/gone.py": b"dropped later", "app/web/app.js": b"same"}
NEW = {**{k: v for k, v in OLD.items() if k != "app/animehub/gone.py"}, "AnimeHub.exe": b"MZ new exe",
       "app/hub.py": b"new hub", "app/animehub/added.py": b"new module"}


class WindowsZip(unittest.TestCase):
    """Decision 79: a Windows zip copy swaps its files beside the running
    app. Every way it can fail leaves the folder as it was. (The running exe
    itself is tested on real Windows in test_real_tools.)"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "AnimeHub"
        for name, data in OLD.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(data)
        (self.root / "my notes.txt").write_bytes(b"not ours")
        self.before = snapshot(self.root)
        update._job.update(state="idle", percent=0, error=None, note=None)
        self.addCleanup(update._job.update, state="idle", percent=0, error=None, note=None)

    def swap(self, files, sha=None):
        body = windows_zip(files)
        found = {"download": update.DOWNLOADS + "x", "sha256": sha or hashlib.sha256(body).hexdigest()}
        with mock.patch.object(update, "urlopen", return_value=Reply(body)):
            update._swap_zip(found, self.root)
        return update._job

    def assertUntouched(self):
        self.assertEqual(snapshot(self.root), self.before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["AnimeHub.exe", "README.txt", "app", "my notes.txt", "python"])

    def test_a_good_zip_swaps_in(self):
        self.assertEqual((self.swap(NEW)["state"], update._job["error"]), ("done", None))
        after = snapshot(self.root)
        self.assertEqual(after, {**NEW, "my notes.txt": b"not ours"})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["AnimeHub.exe", "README.txt", "app", "my notes.txt", "python"])

    def test_tidy_takes_what_the_running_exe_left(self):
        old = self.root / ".update-abc-old"
        (old / "AnimeHub.exe").parent.mkdir(parents=True)
        (old / "AnimeHub.exe").write_bytes(b"MZ")
        kept = self.root / ".update-def-old"
        (kept / "app").mkdir(parents=True)
        (kept / "app" / "hub.py").write_bytes(b"a way back")
        (self.root / ".update-abc.zip").write_bytes(b"PK")
        with mock.patch.object(update, "ROOT", self.root), mock.patch.object(update, "REPO", self.root / "app"), \
                mock.patch.object(update, "kind", return_value="windows"):
            update.tidy()
        self.assertEqual(sorted(p.name for p in self.root.glob(".update-*")), [".update-def-old"])

    def test_a_wrong_checksum_changes_nothing(self):
        self.assertEqual(self.swap(NEW, sha="0" * 64)["state"], "failed")
        self.assertUntouched()

    def test_another_python_changes_nothing(self):
        self.assertIn("newer Python", self.swap({**NEW, "python/python313.dll": b"MZ"})["error"])
        self.assertUntouched()
        self.assertIn("newer Python", self.swap({**NEW, "python/python.exe": b"MZ other"})["error"])
        self.assertUntouched()

    def test_an_odd_zip_changes_nothing(self):
        for files in ({**NEW, "../evil.txt": b"x"}, {k: v for k, v in NEW.items() if k != "AnimeHub.exe"},
                      {**NEW, "app/../../evil.txt": b"x"}):
            self.assertIn("didn't look right", self.swap(files)["error"])
            self.assertUntouched()
        with mock.patch.object(update, "urlopen", return_value=Reply(b"<html>")):
            update._swap_zip({"download": "x", "sha256": hashlib.sha256(b"<html>").hexdigest()}, self.root)
        self.assertEqual(update._job["state"], "failed")
        self.assertUntouched()

    def test_a_file_windows_holds_puts_everything_back(self):
        real, calls = update._move, []

        def move(src, dst):
            calls.append(dst)
            if len(calls) == 6:
                raise PermissionError("in use")
            return real(src, dst)
        with mock.patch.object(update, "_move", move):
            self.assertIn("wouldn't let", self.swap(NEW)["error"])
        self.assertGreater(len(calls), 6)  # it had moved files before, and moved them back
        self.assertUntouched()

    def test_who_can(self):
        with mock.patch.object(update, "ROOT", self.root), mock.patch.object(update, "REPO", self.root / "app"):
            self.assertTrue(update.installable({"download": "x"}, "windows"))
            self.assertFalse(update.installable({"download": None}, "windows"))
            (self.root / "app" / ".git").mkdir()
            self.assertFalse(update.installable({"download": "x"}, "windows"))  # a checkout on Windows
        self.assertEqual(update.pick([release("v0.5", "AnimeHub-windows-x64.zip")], "0.4", "windows")["download"],
                         f"{update.DOWNLOADS}v0.5/AnimeHub-windows-x64.zip")

    def test_install_starts_the_swap(self):
        settings.set_setting("update_found", json.dumps({"version": "99.0", "download": "x", "sha256": "y"}))
        self.addCleanup(settings.set_setting, "update_found", "")
        with mock.patch.object(update, "ROOT", self.root), mock.patch.object(update, "REPO", self.root / "app"), \
                mock.patch.object(update, "kind", return_value="windows"), mock.patch.object(update, "vpn_missing", return_value=False), \
                mock.patch.object(update, "_swap_zip") as swap:
            self.assertEqual(update.install(), {"ok": True})
            for _ in range(50):
                if swap.called:
                    break
                time.sleep(0.02)
        swap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
