"""Get ani-cli (Decision 47): the official script, on request, checked
before it replaces anything."""

import io
import json
import time
import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import android, anicli, database, remote, settings, system
from animehub.config import config

GOOD =b"""#!/bin/sh
version_number="5.2.0"
ANI_CLI_MENU ANI_CLI_PLAYER_FLAGS ANI_CLI_PLAYER ANI_CLI_MODE ANI_CLI_QUALITY
Select anime
Select episode
"""


def reply(body):
    r = mock.MagicMock()
    r.__enter__.return_value = io.BytesIO(body)
    return r


class GetAniCli(unittest.TestCase):
    def setUp(self):
        anicli.own_anicli().unlink(missing_ok=True)
        self.addCleanup(lambda: anicli.own_anicli().unlink(missing_ok=True))
        anicli._anicli_versions.clear()

    @unittest.skipIf(system.WINDOWS, "Windows updates ani-cli with Scoop")
    def test_saves_a_runnable_copy_and_prefers_it(self):
        with mock.patch.object(anicli, "urlopen", return_value=reply(GOOD)), \
             mock.patch.object(anicli, "anicli_version", return_value="5.2.0"):
            self.assertEqual(anicli.fetch_anicli(), {"ok": True, "version": "5.2.0", "changed": True})
        with mock.patch.object(anicli, "urlopen", return_value=reply(GOOD)), \
             mock.patch.object(anicli, "anicli_version", return_value="5.2.0"):
            self.assertEqual(anicli.fetch_anicli()["changed"], False)  # the newest already
        self.assertEqual(anicli.anicli_path(), str(anicli.own_anicli()))
        self.assertTrue(anicli.own_anicli().stat().st_mode & 0o100)

    def test_refuses_what_is_not_ani_cli(self):
        for body in (b"<html>not found</html>", b"#!/bin/sh\necho hi\n", GOOD + b"x" * anicli.ANICLI_MAX):
            with mock.patch.object(anicli, "urlopen", return_value=reply(body)):
                self.assertIn("error", anicli.fetch_anicli())
        self.assertFalse(anicli.own_anicli().exists())

    @unittest.skipIf(system.WINDOWS, "Windows updates ani-cli with Scoop")
    def test_no_network_keeps_what_was_there(self):
        anicli.own_anicli().parent.mkdir(parents=True, exist_ok=True)
        anicli.own_anicli().write_bytes(GOOD)
        with mock.patch.object(anicli, "urlopen", side_effect=OSError):
            self.assertEqual(anicli.fetch_anicli(), {"error": "Could not reach GitHub."})
        self.assertEqual(anicli.own_anicli().read_bytes(), GOOD)

    def test_windows_gets_no_button(self):
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(anicli, "urlopen") as fetch:
            self.assertFalse(anicli.anicli_status()["can_get"])
            self.assertIn("Scoop", anicli.fetch_anicli()["error"])
        fetch.assert_not_called()

    def test_old_versions_are_outdated(self):
        for version, old in (("4.14.0", True), ("5.1.9", True), ("5.2.0", False), ("6.0", False), (None, False)):
            with mock.patch.object(anicli, "anicli_version", return_value=version):
                self.assertEqual(anicli.anicli_outdated(), old, version)

    def test_a_phone_cannot_fetch(self):
        from animehub import remote
        self.assertIn("/api/anicli-get", remote.BLOCKED)


class TooOldOrBlocked(unittest.TestCase):
    """Fedora: a packaged ani-cli below 5.1 failed with no word why, and a
    fetched 5.1 still failed: its site turns plain curl away."""

    def setUp(self):
        anicli._anicli_checks.clear()
        self.addCleanup(anicli._anicli_checks.clear)

    def test_a_play_click_says_ani_cli_is_too_old(self):
        with mock.patch.object(anicli, "anicli_path", return_value="/x/ani-cli"), \
             mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}), \
             mock.patch.object(anicli, "anicli_version", return_value="4.8.0"), \
             mock.patch.object(anicli.subprocess, "Popen") as popen:
            with self.assertRaises(anicli.AniCliError) as e:
                anicli.anicli_spawn(["x"], {}, None, None)
        popen.assert_not_called()
        self.assertIn("4.8.0 is too old", str(e.exception))
        from animehub import streaming
        self.assertEqual(streaming.friendly_stream_error(str(e.exception)), str(e.exception))

    @unittest.skipIf(system.WINDOWS, "ani-cli runs in Git Bash there")
    def test_missing_curl_impersonate_is_named(self):
        script = support.TMP / "ani-cli-curls"
        script.write_bytes(GOOD + b'curl_exe=$(dep_ch_failover "curl_chrome136,curl_ff117,curl") || die\n')
        with mock.patch.object(anicli, "anicli_path", return_value=str(script)):
            self.assertEqual(anicli.anicli_check()["curls"], ["curl_chrome136", "curl_ff117"])
            with mock.patch.object(system, "find_program", side_effect=lambda n: None if n.startswith("curl_") else n):
                self.assertEqual(anicli.anicli_curl_needs(), ["curl-impersonate"])
            with mock.patch.object(system, "find_program", side_effect=lambda n: n):
                self.assertEqual(anicli.anicli_curl_needs(), [])

    def test_older_scripts_need_no_curl_impersonate(self):
        with mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}):
            self.assertEqual(anicli.anicli_curl_needs(), [])

    def test_the_cloudflare_block_names_the_fix(self):
        from animehub import streaming
        msg = streaming.friendly_stream_error("ani-cli: Blocked by cloudflare. Try installing curl-impersonate")
        self.assertIn("install curl-impersonate", msg)

    @unittest.skipIf(system.WINDOWS, "uses a POSIX shell script")
    def test_in_place_upgrade_refreshes_cached_version(self):
        """Upgrading /usr/bin/ani-cli while the hub runs must invalidate
        _anicli_versions without a restart (ani-cli 5.1 -> 5.2)."""
        import os
        script = support.TMP / "ani-cli-upgraded"
        script.write_bytes(b'#!/bin/sh\necho "5.1.0"\n')
        script.chmod(0o755)
        self.addCleanup(script.unlink, missing_ok=True)
        anicli._anicli_versions.clear()
        with mock.patch.object(anicli, "anicli_path", return_value=str(script)):
            self.assertEqual(anicli.anicli_version(), "5.1.0")
            self.assertTrue(anicli.anicli_outdated())
            script.write_bytes(b'#!/bin/sh\necho "5.2.0"\n')
            os.utime(script, ns=(script.stat().st_mtime_ns + 1_000_000_000, script.stat().st_mtime_ns + 1_000_000_000))
            self.assertEqual(anicli.anicli_version(), "5.2.0")
            self.assertFalse(anicli.anicli_outdated())

    def test_env_resets_user_exact_match_and_source(self):
        with mock.patch.dict("os.environ", {"ANI_CLI_EXACT": "1", "ANI_CLI_DEFAULT_SOURCE": "history"}), \
                mock.patch.object(anicli, "stream_prefs", return_value={"quality": "best", "mode": "sub"}):
            env = anicli.anicli_env()
        self.assertEqual(env["ANI_CLI_EXACT"], "0")
        self.assertEqual(env["ANI_CLI_DEFAULT_SOURCE"], "search")


class AniCliUpdateNoticesAndUpgrade(unittest.TestCase):
    """Consented upstream update checks, upgrade error hints, and one-click
    upgrades across Linux (download), Windows (Scoop), and Android (Termux)."""

    def setUp(self):
        config.DATA.mkdir(parents=True, exist_ok=True)
        database.init_db()
        for k in ("update_check", "update_vpn_only", "anicli_update_found", "anicli_update_checked"):
            settings.set_setting(k, "")
        anicli._upgrade_job.update(state="idle", version=None, changed=None, error=None)
        anicli._anicli_versions.clear()
        anicli._anicli_checks.clear()
        self.addCleanup(anicli._upgrade_job.update, state="idle", version=None, changed=None, error=None)
        self.addCleanup(anicli.own_anicli().unlink, True)

    def test_parse_version_and_stored_clears_after_upgrade(self):
        self.assertEqual(anicli.parse_version("v5.2.1"), (5, 2, 1))
        self.assertEqual(anicli.parse_version("5.2"), (5, 2, 0))
        self.assertIsNone(anicli.parse_version("nightly"))
        settings.set_setting("anicli_update_found", json.dumps({"version": "5.2.1", "url": "https://github.com/pystardust/ani-cli/releases/tag/v5.2.1"}))
        with mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli"), \
                mock.patch.object(anicli, "anicli_version", return_value="5.2.0"):
            self.assertEqual(anicli.update_stored()["version"], "5.2.1")
        with mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli"), \
                mock.patch.object(anicli, "anicli_version", return_value="5.2.1"):
            self.assertIsNone(anicli.update_stored())

    def test_check_update_stores_newer_release_and_respects_vpn_only(self):
        body = json.dumps({"tag_name": "v5.3.0", "html_url": "https://github.com/pystardust/ani-cli/releases/tag/v5.3.0", "published_at": "2026-11-26T00:00:00Z"}).encode()
        with mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli"), \
                mock.patch.object(anicli, "anicli_version", return_value="5.2.0"), \
                mock.patch.object(anicli, "urlopen", return_value=reply(body)):
            r = anicli.check_update()
        self.assertTrue(r["ok"])
        self.assertEqual(r["found"]["version"], "5.3.0")
        with mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli"), \
                mock.patch.object(anicli, "anicli_version", return_value="5.2.0"):
            self.assertEqual(anicli.update_stored()["version"], "5.3.0")

        settings.set_automation("update_vpn_only", True)
        with mock.patch.object(settings, "vpn_status", return_value={"active": False, "known": True}), \
                mock.patch.object(anicli, "upgrade_method", return_value="download"), \
                mock.patch.object(anicli, "urlopen", side_effect=AssertionError("asked GitHub")):
            self.assertEqual(anicli.check_update(), {"error": anicli.ANICLI_NO_VPN})
            self.assertEqual(anicli.upgrade_start(), {"error": anicli.ANICLI_NO_VPN})
            self.assertFalse(anicli.update_due(time.time() + 2 * anicli.ANICLI_UPDATE_EVERY))

    def test_update_due_follows_consented_daily_schedule(self):
        with mock.patch.object(anicli, "anicli_path", return_value="/usr/bin/ani-cli"):
            settings.set_automation("update_check", False)
            self.assertFalse(anicli.update_due(time.time() + 10 * anicli.ANICLI_UPDATE_EVERY))
            settings.set_automation("update_check", True)
            self.assertTrue(anicli.update_due())
            settings.set_setting("anicli_update_checked", str(int(time.time())))
            self.assertFalse(anicli.update_due())
            self.assertTrue(anicli.update_due(time.time() + anicli.ANICLI_UPDATE_EVERY + 1))

    def test_spawn_marks_outdated_and_unsupported_errors_for_upgrade_dialog(self):
        with mock.patch.object(anicli, "anicli_path", return_value="/x/ani-cli"), \
                mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}), \
                mock.patch.object(anicli, "anicli_version", return_value="5.1.0"):
            with self.assertRaises(anicli.AniCliError) as e:
                anicli.anicli_spawn(["x"], {}, None, None)
        self.assertTrue(e.exception.needs_upgrade)
        self.assertEqual(anicli.error_dict(e.exception)["anicli_update"], True)

        with mock.patch.object(anicli, "anicli_path", return_value="/x/ani-cli"), \
                mock.patch.object(anicli, "anicli_check", return_value={"ok": False, "missing": ["ANI_CLI_MENU"]}):
            with self.assertRaises(anicli.AniCliError) as e2:
                anicli.anicli_spawn(["x"], {}, None, None)
        self.assertTrue(e2.exception.needs_upgrade)

    def test_upgrade_methods_and_remote_blocks(self):
        self.assertIn("/api/anicli-check", remote.BLOCKED)
        self.assertIn("/api/anicli-upgrade", remote.BLOCKED)
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "ANDROID", False), \
                mock.patch.object(system, "powershell", return_value="powershell.exe"), \
                mock.patch.object(system, "scoop_script", return_value="C:\\scoop\\scoop.ps1"):
            self.assertEqual(anicli.upgrade_method(), "scoop")
            self.assertEqual(anicli.upgrade_cmd(), "scoop update ani-cli")
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "ANDROID", False), \
                mock.patch.object(system, "powershell", return_value=None):
            self.assertIsNone(anicli.upgrade_method())

    def test_scoop_upgrade_runs_update_and_clears_caches(self):
        calls = []
        vers = iter(["5.1.0", "5.2.0", "5.2.0", "5.2.0"])

        def fake_run(argv, **kw):
            calls.append(argv)
            m = mock.MagicMock(returncode=0, stdout="Updated ani-cli 5.2.0\n")
            return m

        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "ANDROID", False), \
                mock.patch.object(system, "powershell", return_value="pwsh.exe"), \
                mock.patch.object(system, "scoop_script", return_value=r"C:\scoop\scoop.ps1"), \
                mock.patch.object(system, "refresh_path") as ref, \
                mock.patch.object(anicli.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(anicli, "anicli_version", side_effect=lambda: next(vers, "5.2.0")), \
                mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}), \
                mock.patch.object(anicli, "anicli_path", return_value=r"C:\scoop\apps\ani-cli\current\ani-cli"):
            anicli._upgrade_work("scoop")
        self.assertEqual([c[-2:] for c in calls], [["C:\\scoop\\scoop.ps1", "update"], ["update", "ani-cli"]])
        ref.assert_called_once()
        self.assertEqual(anicli.upgrade_job()["state"], "done")
        self.assertEqual(anicli.upgrade_job()["version"], "5.2.0")

    def test_termux_upgrade_runs_noninteractive_pkg_and_refreshes(self):
        seen = []

        def fake_transport(script, timeout):
            seen.append(script)
            return 0, "Setting up ani-cli\n@@UPRC 0\n", "", ""

        with mock.patch.object(android, "transport", side_effect=fake_transport), \
                mock.patch.object(android, "fetch_anicli", return_value=True) as ref, \
                mock.patch.object(android, "anicli_version", return_value="5.2.0"):
            r = android.upgrade_anicli()
        self.assertEqual(r, {"ok": True, "version": "5.2.0"})
        ref.assert_called_once_with(force=True)
        self.assertIn("DEBIAN_FRONTEND=noninteractive", seen[0])
        self.assertIn("pkg install -y ani-cli", seen[0])
