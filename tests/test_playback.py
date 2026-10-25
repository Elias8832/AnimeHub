"""Offline regression checks for the playback watch-state bookkeeping."""
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import support
from animehub import database, library, player, system
from animehub.config import config


class ReplayRaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()

    def tearDown(self):
        with player._active_lock:
            player._active.clear()

    def test_replaying_same_episode_keeps_new_active_entry(self):
        """The old watcher winds down while a same-episode replay already
        inserted its replacement under the same id; popping unconditionally
        would make the now-playing pill vanish for the live mpv."""
        # A bare Mock would invent hub's session flags (a pty fd, a truthy hub_retired).
        old_proc = Mock(hub_pty=None, hub_retired=False)
        old_proc.poll.return_value = 0  # terminated: watcher skips its loops
        new_proc = Mock(hub_pty=None, hub_retired=False)
        with player._active_lock:
            player._active["ep1"] = {"proc": new_proc, "ep_id": "ep1",
                                  "show_title": "Show", "label": "S1 · E1", "percent": 0}
        player.watch_process(old_proc, "ep1", Path(self.directory.name) / "absent.sock")
        with player._active_lock:
            self.assertIs(player._active.get("ep1", {}).get("proc"), new_proc)

    def test_exiting_watcher_still_removes_its_own_entry(self):
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.return_value = 0
        with player._active_lock:
            player._active["ep1"] = {"proc": proc, "ep_id": "ep1",
                                  "show_title": "Show", "label": "S1 · E1", "percent": 0}
        player.watch_process(proc, "ep1", Path(self.directory.name) / "absent.sock")
        with player._active_lock:
            self.assertNotIn("ep1", player._active)


class DieBelowThresholdTests(unittest.TestCase):
    """Check 3 (tracking): a session that ends below the 90 % mark — a stream
    dying mid-play or a manual quit — must save its position but never mark
    the episode watched. There is one tracking handler, watch_process, for
    both local and online:... ids, so each case is run against both."""

    DURATION = 1440.0  # a 24-minute episode

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()

    def tearDown(self):
        with player._active_lock:
            player._active.clear()

    def run_session(self, ep_id, position, marker=False, sock=None):
        """Drive watch_process with a fake mpv: the IPC socket exists, the
        property reads report `position` of DURATION, then mpv exits. The
        session is registered in _active first — the generation guard
        (TODO 24) only lets a watcher write when it owns its slot."""
        if sock is None:
            sock = Path(self.directory.name) / "fake.sock"
            sock.write_text("")

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["get_property", "time-pos"]:
                return {"data": position}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": self.DURATION}
            return {}

        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.side_effect = [None, None]  # one connect pass, one watch pass
        proc.wait.return_value = 0
        with player._active_lock:
            player._active[ep_id] = {"proc": proc, "ep_id": ep_id,
                                  "show_title": "Show", "label": "S1 · E1",
                                  "percent": 0, "version": 0}
        if marker:
            system.ipc_marker(sock).write_text("")  # lua-style quit marker
        with patch.object(player, "mpv_ipc", side_effect=fake_ipc):
            player.watch_process(proc, ep_id, sock)
        return sock

    def episode_row(self, ep_id):
        with sqlite3.connect(str(config.DB)) as c:
            return c.execute(
                "SELECT watched, position, duration, last_played FROM episodes WHERE id=?",
                (ep_id,)).fetchone()

    def test_stream_dying_below_threshold_saves_position_not_watched(self):
        """A stream that dies at 40 % keeps its resumable position and stays
        unwatched — the network drop must look like a manual quit."""
        self.run_session("online:sole-eater:5", 576.0)
        watched, position, duration, last_played = self.episode_row("online:sole-eater:5")
        self.assertEqual(watched, 0)
        self.assertEqual(position, 576.0)
        self.assertEqual(duration, self.DURATION)
        self.assertIsNotNone(last_played)

    def test_local_quit_at_40_percent_behaves_identically(self):
        """The local path saves position and leaves watched=0 too — proving
        stream targets really do share the one handler, no second path."""
        self.run_session("ep1", 576.0)
        watched, position, duration, last_played = self.episode_row("ep1")
        self.assertEqual(watched, 0)
        self.assertEqual(position, 576.0)
        self.assertEqual(duration, self.DURATION)
        self.assertIsNotNone(last_played)

    def test_natural_end_marks_watched(self):
        """Reaching ~99.9 % flips watched=1 via the same handler."""
        self.run_session("online:sole-eater:5", self.DURATION - 2.0)
        watched, position, duration, last_played = self.episode_row("online:sole-eater:5")
        self.assertEqual(watched, 1)
        self.assertEqual(position, self.DURATION - 2.0)
        self.assertEqual(duration, self.DURATION)
        self.assertIsNotNone(last_played)

    def test_skip_marker_marks_watched_from_low_position(self):
        """A lua marker quit counts as done even mid-episode; position is
        stored as the full duration so the row never resurfaces as resumable."""
        self.run_session("online:sole-eater:5", 576.0, marker=True)
        watched, position, duration, last_played = self.episode_row("online:sole-eater:5")
        self.assertEqual(watched, 1)
        self.assertEqual(position, self.DURATION)
        self.assertEqual(duration, self.DURATION)
        self.assertIsNotNone(last_played)

    def test_skip_marker_on_windows_sits_in_temp_dir(self):
        """Windows (TODO 74): mpv listens on a named pipe, which has no
        folder beside it — the lua's marker goes to the temp dir, named
        after the pipe, and still counts as done."""
        with patch.object(system, "WINDOWS", True), \
                patch.object(system.tempfile, "tempdir", self.directory.name):
            sock = system.ipc_address("anime-hub-0badf00d")
            marker = system.ipc_marker(sock)
            self.assertEqual(marker, Path(self.directory.name) / "anime-hub-0badf00d.skip")
            self.assertEqual(player.mpv_env(sock), {"ANIME_HUB_SKIP_MARKER": str(marker)})
            self.run_session("online:sole-eater:6", 576.0, marker=True, sock=sock)
        self.assertEqual(self.episode_row("online:sole-eater:6")[0], 1)
        self.assertFalse(marker.exists())  # cleaned up with the session


class ResumeThresholdTests(unittest.TestCase):
    """A stored position is a resume point, not a strip-filter verdict. The
    92 % cutoff exists to keep near-finished rows out of continue-watching;
    a re-watch or unmark can legitimately leave pos >= 92 % with watched=0,
    and starting that episode over would silently throw the position away.
    So: watched rows past the cutoff restart, unwatched ones always resume."""

    DURATION = 1440.0
    PAST_CUTOFF = 1350.0  # 93.75 % of DURATION
    UNDER_CUTOFF = 576.0  # 40 % of DURATION

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()
        # No signal ever leaves a test (TODO 54): a superseded fake process
        # has no real process group.
        killpg = patch.object(os, "killpg", create=True)
        killpg.start()
        self.addCleanup(killpg.stop)
        self.popen = patch.object(subprocess, "Popen")
        self.addCleanup(self.popen.stop)
        self.popen_mock = self.popen.start()
        self.watcher = patch.object(player, "watch_process", Mock())
        self.watcher.start()
        self.addCleanup(self.watcher.stop)
        self.lua = patch.object(player, "SKIP_CREDITS_LUA",
                                Path(self.directory.name) / "absent.lua")
        self.lua.start()
        self.addCleanup(self.lua.stop)

    def tearDown(self):
        with player._active_lock:
            player._active.clear()

    def seed(self, ep_id, watched, position):
        with database.db() as c:
            c.execute("INSERT INTO episodes(id, watched, position, duration) VALUES (?,?,?,?)",
                      (ep_id, watched, position, self.DURATION))

    def mpv_args(self):
        args = self.popen_mock.call_args[0][0]
        starts = [a for a in args if a.startswith("--start=")]
        self.assertLessEqual(len(starts), 1, f"more than one resume flag: {args}")
        return starts[0].split("=", 1)[1] if starts else None

    def fake_local(self, ep_id):
        (Path(self.directory.name) / "lib" / "Show").mkdir(parents=True, exist_ok=True)
        (Path(self.directory.name) / "lib" / "Show" / "E01.mp4").write_bytes(b"")
        root = patch.object(config, "LIBRARY", Path(self.directory.name) / "lib")
        root.start()
        self.addCleanup(root.stop)
        show = {"title": "Show", "seasons": [{"label": "Season 1", "season": 1,
                                              "episodes": [{"id": ep_id}]}]}
        ep = {"id": ep_id, "number": 1, "files": [{"path": "Show/E01.mp4",
                                                   "label": "default"}]}
        lookup = patch.object(library, "episode_by_id", lambda eid: (show, ep))
        lookup.start()
        self.addCleanup(lookup.stop)

    def assert_resume(self, minutes, seconds):
        self.assertEqual(self.mpv_args(), f"{minutes}:{seconds}")

    def test_local_unwatched_past_cutoff_resumes(self):
        """The fix: pos >= 92 % with watched=0 must not lose its position."""
        self.seed("show:s1:e1", 0, self.PAST_CUTOFF)
        self.fake_local("show:s1:e1")
        player.start_play("show:s1:e1")
        # 1350 s -> 22 min 30 s
        self.assert_resume(22, 30)

    def test_local_watched_past_cutoff_restarts(self):
        """A finished episode's stored ~duration tail is not a resume point."""
        self.seed("show:s1:e1", 1, self.PAST_CUTOFF)
        self.fake_local("show:s1:e1")
        player.start_play("show:s1:e1")
        self.assertIsNone(self.mpv_args())

    def test_local_watched_under_cutoff_resumes(self):
        """Guard: watched rows below the cutoff keep their normal resume."""
        self.seed("show:s1:e1", 1, self.UNDER_CUTOFF)
        self.fake_local("show:s1:e1")
        player.start_play("show:s1:e1")
        # 576 s -> 9 min 36 s
        self.assert_resume(9, 36)


if __name__ == "__main__":
    unittest.main()
