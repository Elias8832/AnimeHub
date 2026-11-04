"""Get ani-cli (Decision 47): the official script, on request, checked
before it replaces anything."""

import io
import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import anicli, system

GOOD =b"""#!/bin/sh
version_number="5.1.0"
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
             mock.patch.object(anicli, "anicli_version", return_value="5.1.0"):
            self.assertEqual(anicli.fetch_anicli(), {"ok": True, "version": "5.1.0", "changed": True})
        with mock.patch.object(anicli, "urlopen", return_value=reply(GOOD)), \
             mock.patch.object(anicli, "anicli_version", return_value="5.1.0"):
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
        for version, old in (("4.14.0", True), ("5.0.9", True), ("5.1.0", False), ("6.0", False), (None, False)):
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
