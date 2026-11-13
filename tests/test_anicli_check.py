"""TODO 18: the ani-cli feature check — an ani-cli without the settings and
menus the bridge relies on fails loud instead of half-working."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401
from animehub import anicli, streaming

GOOD = """#!/bin/sh
mode="${ANI_CLI_MODE:-sub}"
quality="${ANI_CLI_QUALITY:-best}"
player_function="${ANI_CLI_PLAYER:-mpv}"
player_extra_flags="${ANI_CLI_PLAYER_FLAGS:-""}"
menu_program="${ANI_CLI_MENU:-fzf}"
nth "Select anime: "
nth "Select episode: "
"""


class Check(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        anicli._anicli_checks.clear()

    def fake(self, text, binary=False):
        path = Path(self.dir.name) / "ani-cli"
        path.write_bytes(text if binary else text.encode())
        os.utime(path, ns=(len(anicli._anicli_checks) + 1, len(anicli._anicli_checks) + 1))
        patcher = mock.patch.object(anicli, "anicli_path", return_value=str(path))
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def test_supported(self):
        self.fake(GOOD)
        self.assertEqual(anicli.anicli_check(), {"ok": True, "missing": [], "curls": []})

    def test_missing_setting(self):
        # Only ANI_CLI_PLAYER_FLAGS left: a prefix must not count as ANI_CLI_PLAYER.
        self.fake(GOOD.replace('"${ANI_CLI_PLAYER:-mpv}"', '"mpv"').replace("ANI_CLI_MENU", "ANI_CLI_PICKER"))
        check = anicli.anicli_check()
        self.assertIs(check["ok"], False)
        self.assertEqual(check["missing"], ["ANI_CLI_MENU (menu helper)", "ANI_CLI_PLAYER (player choice)"])

    def test_renamed_menu(self):
        self.fake(GOOD.replace("Select episode", "Pick episode"))
        self.assertEqual(anicli.anicli_check()["missing"], ["the “Select episode” menu"])

    def test_not_a_script_is_no_verdict(self):
        self.fake(b"\x7fELF\x02\x01", binary=True)
        self.assertEqual(anicli.anicli_check(), {"ok": None, "missing": []})

    def test_no_anicli(self):
        with mock.patch.object(anicli, "anicli_path", return_value=None):
            self.assertIsNone(anicli.anicli_check())

    def test_spawn_refuses_and_message_reaches_ui(self):
        self.fake(GOOD.replace("ANI_CLI_MENU", "X"))
        with mock.patch.object(anicli.subprocess, "Popen") as popen:
            with self.assertRaises(anicli.AniCliError) as cm:
                anicli.anicli_spawn(["x"], {}, None, None)
        popen.assert_not_called()
        msg = str(cm.exception)
        self.assertIn("update ani-cli", msg)
        self.assertEqual(streaming.friendly_stream_error(msg), msg)


class QueryIsNoOption(unittest.TestCase):
    """The query is one argument to ani-cli: "-U" there updates ani-cli and
    "-D" wipes its history, so no query may become one (review, 2026-11)."""

    def test_search_strips_dashes_even_after_a_space(self):
        seen = []
        with mock.patch.object(anicli, "anicli_probe", side_effect=lambda args, *a, **k: seen.append(args) or (0, "", [])):
            anicli.anicli_search("- -U")
            anicli.anicli_search("--  Dan Da Dan")
        self.assertEqual(seen, [["U"], ["Dan Da Dan"]])

    def test_a_picked_result_is_cleaned_the_same_way(self):
        seen = []

        def episodes(link):
            seen.append(link["query"])
            raise anicli.AniCliError("stop here")

        with mock.patch.object(streaming, "stream_link_get", return_value=None), \
                mock.patch.object(anicli, "stream_prefs", return_value={"quality": "best", "mode": "sub"}), \
                mock.patch.object(anicli, "anicli_episodes", side_effect=episodes):
            with self.assertRaises(anicli.AniCliError):
                streaming.stream_link_set(1, "-D", None)
            with self.assertRaisesRegex(anicli.AniCliError, "Enter a title"):
                streaming.stream_link_set(1, " -- ", None)
        self.assertEqual(seen, ["D"])


if __name__ == "__main__":
    unittest.main()
