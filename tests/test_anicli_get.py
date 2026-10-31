"""Get ani-cli (Decision 47): the official script, on request, checked
before it replaces anything."""

import io
import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import anicli

GOOD = b"""#!/bin/sh
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

    def test_no_network_keeps_what_was_there(self):
        anicli.own_anicli().parent.mkdir(parents=True, exist_ok=True)
        anicli.own_anicli().write_bytes(GOOD)
        with mock.patch.object(anicli, "urlopen", side_effect=OSError):
            self.assertEqual(anicli.fetch_anicli(), {"error": "Could not reach GitHub."})
        self.assertEqual(anicli.own_anicli().read_bytes(), GOOD)

    def test_old_versions_are_outdated(self):
        for version, old in (("4.14.0", True), ("5.0.9", True), ("5.1.0", False), ("6.0", False), (None, False)):
            with mock.patch.object(anicli, "anicli_version", return_value=version):
                self.assertEqual(anicli.anicli_outdated(), old, version)

    def test_a_phone_cannot_fetch(self):
        from animehub import remote
        self.assertIn("/api/anicli-get", remote.BLOCKED)
