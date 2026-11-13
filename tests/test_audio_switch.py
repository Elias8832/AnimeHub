"""Sub/Dub from inside the player (TODO 153): the other language gets ready
while the episode plays, then takes over at the same spot."""

import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tests.test_real_tools import STAND_IN
from animehub.config import config
from animehub import anicli, database, player, prepare, settings, streaming, system

_patches = []


def setUpModule():
    data = Path(tempfile.mkdtemp(dir=support.TMP))
    _patches.extend([mock.patch.object(config, "DATA", data), mock.patch.object(config, "DB", data / "hub.db")])
    for p in _patches:
        p.start()
    database.init_db()


def tearDownModule():
    for p in _patches:
        p.stop()


def wait_for(check, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


NO_DUB = b"""#!/bin/sh
printf '\\033[1;31mNo sources found for dub\\033[0m\\n' >&2
exit 1
"""


@unittest.skipIf(os.name == "nt", "the stand-in ani-cli is a shell script")
class ReadyLanguage(unittest.TestCase):
    """The real wait script, on a stand-in ani-cli."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="animehub-switch-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.script = self.dir / "ani-cli"
        self.mpv = self.dir / "mpv"
        self.mpv.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.link = {"media_id": 900, "query": "Test Show", "title": "Test Show", "idx": 1,
                     "mode": "sub", "episodes": ["1", "2", "3"]}
        for patcher in (mock.patch.object(anicli, "anicli_path", return_value=str(self.script)),
                        mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}),
                        mock.patch.object(system, "find_program", return_value=str(self.mpv)),
                        mock.patch.object(streaming, "stream_link_get", return_value=self.link),
                        mock.patch.dict(os.environ, {"HUB_TEST_FILE": "clip.mp4"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def ani_cli(self, body):
        self.script.write_bytes(body)
        for f in (self.script, self.mpv):
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def test_ready_once_ani_cli_has_the_link(self):
        self.ani_cli(STAND_IN)
        got = streaming.ready_language("stream:900:2", "dub", lambda: True)
        slot = got["slot"]
        self.addCleanup(prepare.end, slot)
        self.assertTrue(prepare.ready(slot))
        self.assertIsNone(slot["proc"].poll(), "it waits for the go")
        self.assertIsNot(prepare._slot, slot, "the one prepare slot stays free")

    def test_no_dub_says_so(self):
        self.ani_cli(NO_DUB)
        got = streaming.ready_language("stream:900:2", "dub", lambda: True)
        self.assertEqual(got, {"error": "E2 has no English dub yet."})

    def test_called_off(self):
        self.ani_cli(STAND_IN)
        started = []
        real = prepare.spawn

        def spawn(*a):
            slot = real(*a)
            started.append(slot)
            return slot

        with mock.patch.object(prepare, "spawn", spawn), mock.patch.object(prepare, "ready", return_value=False):
            self.assertIsNone(streaming.ready_language("stream:900:2", "dub", lambda: False))
        self.assertIsNotNone(started[0]["proc"].poll())


class Choice(unittest.TestCase):
    def test_online_offers_both(self):
        self.assertEqual(player.audio_choice("stream:1:2", {"mode": "dub"}), {"current": "dub", "options": ["sub", "dub"]})

    def test_files_need_both_versions(self):
        both = {"files": [{"label": "default"}, {"label": "Dub"}]}
        with mock.patch("animehub.library.episode_by_id", return_value=({}, both)):
            self.assertEqual(player.audio_choice("e1", {"version": 1}), {"current": "dub", "options": ["sub", "dub"]})
            self.assertEqual(player.audio_choice("e1", {}), {"current": "sub", "options": ["sub", "dub"]})
        with mock.patch("animehub.library.episode_by_id", return_value=({}, {"files": [{"label": "default"}]})):
            self.assertIsNone(player.audio_choice("e1", {}))


class Pick(unittest.TestCase):
    def setUp(self):
        self.proc = mock.Mock()
        player._active.clear()
        player._active["stream:1:2"] = {"proc": self.proc, "audio": {"current": "sub", "options": ["sub", "dub"]}}
        self.addCleanup(player._active.clear)

    def test_the_other_language_starts_a_switch(self):
        with mock.patch.object(player, "_switch") as run:
            player.switch_audio("stream:1:2", self.proc, "/s", "dub")
            self.assertTrue(wait_for(lambda: run.called))
            player.switch_audio("stream:1:2", self.proc, "/s", "dub")  # picked again: still one
        self.assertEqual(run.call_count, 1)
        self.assertEqual(player._active["stream:1:2"]["switch"], "dub")

    def test_the_playing_one_calls_it_off(self):
        player._active["stream:1:2"]["switch"] = "dub"
        with mock.patch.object(player, "_tell_switch") as tell, mock.patch.object(player, "_switch") as run:
            player.switch_audio("stream:1:2", self.proc, "/s", "sub")
        run.assert_not_called()
        tell.assert_called_once_with("/s", "off")
        self.assertIsNone(player._active["stream:1:2"]["switch"])

    def test_another_session_is_ignored(self):
        with mock.patch.object(player, "_switch") as run:
            player.switch_audio("stream:1:2", mock.Mock(), "/s", "dub")
            time.sleep(0.05)
        run.assert_not_called()


class Switch(unittest.TestCase):
    def setUp(self):
        self.proc = mock.Mock()
        player._active.clear()
        player._active["stream:1:2"] = {"proc": self.proc, "switch": "dub"}
        self.addCleanup(player._active.clear)
        self.live = {"time-pos": {"data": 123.4}, "fullscreen": {"data": True}}
        self.sent = []

        def ipc(sock, cmd, timeout=2.0):
            self.sent.append(cmd)
            return self.live.get(cmd[1]) if cmd[0] == "get_property" else None

        p = mock.patch.object(player, "mpv_ipc", ipc)
        p.start()
        self.addCleanup(p.stop)

    def takes(self, ep_id, mode, handoff):
        self.taken = handoff.pop("slot")  # start_stream uses the prepared run
        return {"ok": True}

    def test_it_takes_over_at_the_same_spot(self):
        slot = {"proc": mock.Mock()}
        with mock.patch.object(streaming, "ready_language", return_value={"slot": slot}), \
                mock.patch.object(streaming, "start_stream", side_effect=self.takes) as start, \
                mock.patch.object(player, "_await_takeover") as takeover, \
                mock.patch.object(streaming, "relist") as relist:
            player._switch("stream:1:2", self.proc, "/s", "dub")
        handoff = start.call_args.kwargs["handoff"]
        self.assertEqual(start.call_args.args, ("stream:1:2", "dub"))
        self.assertEqual((self.taken, handoff["start"], handoff["fullscreen"], handoff["old"]),
                         (slot, 118.4, True, self.proc))
        self.assertIn(["set_property", "pause", True], self.sent)
        takeover.assert_called_once_with("stream:1:2", self.proc, {"proc": self.proc, "switch": "dub"})
        relist.assert_called_once_with(1)

    def test_near_the_start_it_starts_at_zero(self):
        self.live["time-pos"] = {"data": 2.0}
        with mock.patch.object(streaming, "ready_language", return_value={"slot": None}), \
                mock.patch.object(streaming, "start_stream", return_value={"ok": True}) as start, \
                mock.patch.object(player, "_await_takeover"), mock.patch.object(streaming, "relist"):
            player._switch("stream:1:2", self.proc, "/s", "dub")
        self.assertEqual(start.call_args.kwargs["handoff"]["start"], 0.0)

    def test_no_dub_keeps_playing(self):
        with mock.patch.object(streaming, "ready_language", return_value={"error": "E2 has no English dub yet."}), \
                mock.patch.object(streaming, "start_stream") as start:
            player._switch("stream:1:2", self.proc, "/s", "dub")
        start.assert_not_called()
        self.assertNotIn(["set_property", "pause", True], self.sent)
        self.assertIn("E2 has no English dub yet.", self.sent[-1][2])
        self.assertIsNone(player._active["stream:1:2"]["switch"])

    def test_a_file_switches_to_its_dub_version(self):
        player._active["e1"] = {"proc": self.proc, "switch": "dub"}
        ep = {"files": [{"label": "default"}, {"label": "Dub"}]}
        with mock.patch("animehub.library.episode_by_id", return_value=({}, ep)), \
                mock.patch.object(player, "start_play", return_value={"ok": True}) as start, \
                mock.patch.object(player, "_await_takeover"):
            player._switch("e1", self.proc, "/s", "dub")
        self.assertEqual(start.call_args.args, ("e1", 1))
        self.assertEqual(start.call_args.kwargs["handoff"]["start"], 118.4)


class Handoff(unittest.TestCase):
    def test_start_args(self):
        self.assertEqual(player.start_args("x", handoff={"start": 83.456, "fullscreen": True}),
                         ["--start=83.46", "--fullscreen"])

    def test_the_old_player_stays_until_the_new_one_plays(self):
        old = mock.Mock()
        with mock.patch.object(anicli, "stop_process_group") as stop:
            player.retire_session(old, {"old": old})
            stop.assert_not_called()
            self.assertTrue(old.hub_retired and old.hub_handoff)
            player.retire_session(mock.Mock(), {"old": old})
            stop.assert_called_once()

    def test_the_old_one_ends_once_the_new_one_plays(self):
        old, new = mock.Mock(), mock.Mock()
        old.poll.return_value = new.poll.return_value = None
        player._active.clear()
        player._active["e"] = {"proc": new, "sock": "/new"}
        self.addCleanup(player._active.clear)
        with mock.patch.object(player, "mpv_ipc", return_value={"data": 5.0}), \
                mock.patch.object(anicli, "stop_process_group") as stop:
            player._await_takeover("e", old)
        stop.assert_called_once_with(old)


if __name__ == "__main__":
    unittest.main()
