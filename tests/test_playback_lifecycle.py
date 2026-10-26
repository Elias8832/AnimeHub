"""Playback session lifecycle (TODO 24, READINESS B4).

One session transition, one generation guard, one resume rule:

- a retired watcher (superseded by start_play's SIGTERM) must have NO
  effects: no DB writes, no percent overwrite, no completion bookkeeping;
- a session that ended naturally (EOF, user quit, skip marker) still
  completes its bookkeeping even if a same-episode replay raced it;
- start_play's supersede→spawn→register is serialized, one live mpv;
- resume follows the same >1 %/92 % rule the UI labels with;
- seasons order exactly as the UI ranks them, so autoplay's "next" is the
  section the user sees next;
- auto-next carries the chosen file version; missing mpv is an actionable
  error, not a traceback."""
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from tests import support
from animehub import anicli, database, library, player, system
from animehub.config import config


def row(ep_id, db):
    with support.sqlite(db) as c:
        return c.execute(
            "SELECT watched, position, duration, last_played FROM episodes WHERE id=?",
            (ep_id,)).fetchone()


class LifecycleBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "hub.db"
        self.database = patch.object(config, "DB", self.db)
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()
        # No signal ever leaves a test: a fake process's group is no real
        # group, and os.killpg(1) is kill(-1), which logs the user out (TODO 54).
        self.killpg = patch.object(os, "killpg", create=True)  # create: Windows has none
        self.killpg.start()
        self.addCleanup(self.killpg.stop)
        # No skip thread outlives the test: it reads the database, and
        # Windows can't delete a database that is still open.
        self.skips = patch.object(player, "send_skips")
        self.skips.start()
        self.addCleanup(self.skips.stop)

    def tearDown(self):
        with player._active_lock:
            player._active.clear()
        player._session_error.update(ep_id=None, error=None, ts=0.0)

    # Fake processes spell out the session flags hub sets on a real Popen:
    # a bare Mock would invent them — a pty fd for anicli_release to close,
    # and a truthy hub_retired that silences every watcher.
    def register(self, ep_id, proc, version=0):
        with player._active_lock:
            player._active[ep_id] = {"proc": proc, "ep_id": ep_id,
                                  "show_title": "Show", "label": "S1 · E1",
                                  "percent": 0, "version": version}

    def seed(self, ep_id, watched=0, position=0.0, duration=1440.0):
        with database.db() as c:
            c.execute("INSERT INTO episodes(id,watched,position,duration) VALUES(?,?,?,?)",
                      (ep_id, watched, position, duration))


@unittest.skipIf(system.WINDOWS, "POSIX only (TODO 74)")
class ProcessGroupGuardTests(LifecycleBase):
    """stop_process_group only signals a real child's group (TODO 54):
    os.killpg(1) is kill(-1) — every process the user owns — and a test's
    MagicMock pid reads as 1. os.killpg is mocked (LifecycleBase), so a
    regression fails here instead of logging the user out."""

    def stop(self, pid):
        proc = Mock(pid=pid)
        anicli.stop_process_group(proc)
        return proc

    def test_fake_pids_never_reach_killpg(self):
        for pid in (MagicMock(), Mock(), None, 1, 0, -1, os.getpgrp()):
            with self.subTest(pid=pid):
                os.killpg.reset_mock()
                proc = self.stop(pid)
                os.killpg.assert_not_called()
                proc.send_signal.assert_called_once_with(signal.SIGTERM)

    def test_a_real_child_group_is_signalled(self):
        pid = os.getpgrp() + 1000003
        proc = self.stop(pid)
        os.killpg.assert_called_once_with(pid, signal.SIGTERM)
        proc.send_signal.assert_not_called()


class RetiredWatcherTests(LifecycleBase):
    """start_play terminates the replaced mpv with SIGTERM → returncode
    -SIGTERM marks the watcher retired: zero effects, the replacement owns
    tracking (READINESS B4's repro: the old session overwrote the new
    session's 5 % with its own 80 % and marked the episode watched)."""

    def test_retired_watcher_has_no_effects(self):
        old = Mock(hub_pty=None, hub_retired=False)
        old.poll.return_value = -signal.SIGTERM
        old.wait.return_value = -signal.SIGTERM
        new = Mock(hub_pty=None, hub_retired=False)
        self.register("ep1", new, version=0)
        self.seed("ep1", watched=0, position=5.0)  # new session's fresh write
        player.watch_process(old, "ep1", Path(self.directory.name) / "absent.sock")
        watched, position, duration, last_played = row("ep1", self.db)
        self.assertEqual(watched, 0)
        self.assertEqual(position, 5.0)
        self.assertIsNone(last_played)  # retired watcher wrote nothing
        with player._active_lock:
            self.assertIs(player._active.get("ep1", {}).get("proc"), new)  # not popped

    def test_superseded_mid_session_stops_writing(self):
        """The generation guard: an active watcher whose slot is taken over
        stops writing position/percent at the next poll, not at cleanup."""
        sock = Path(self.directory.name) / "fake.sock"
        sock.write_text("")
        old = Mock(hub_pty=None, hub_retired=False)
        old.poll.side_effect = [None, None, None]
        old.wait.side_effect = [subprocess.TimeoutExpired("mpv", 2), 0]
        self.register("ep1", old)
        self.seed("ep1", watched=0, position=0.0)

        reads = {"n": 0}

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["set_property", "pause"]:
                return {}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": 1440.0}
            if cmd[:2] != ["get_property", "time-pos"]:
                return {}  # the skip ranges' chapter-list read
            reads["n"] += 1
            if reads["n"] == 2:
                # The replacement takes the slot between the two polls.
                with player._active_lock:
                    player._active["ep1"]["proc"] = Mock(hub_pty=None, hub_retired=False)
                return {"data": 200.0}
            return {"data": 100.0}

        with patch.object(player, "mpv_ipc", side_effect=fake_ipc):
            player.watch_process(old, "ep1", sock)
        watched, position, duration, last_played = row("ep1", self.db)
        self.assertEqual(position, 100.0)  # only the owned poll wrote
        self.assertEqual(watched, 0)
        with player._active_lock:
            self.assertIsNot(player._active["ep1"]["proc"], old)
            # Not overwritten with the unowned poll's 200 s percent — the
            # last owned value (100 s) stands.
            self.assertAlmostEqual(player._active["ep1"]["percent"], 100 / 1440 * 100, places=5)


class NaturalEndTests(LifecycleBase):
    def test_eof_completion_survives_same_episode_replay(self):
        """A session that reached the end completes its bookkeeping even if
        the user immediately replayed the episode (replacement owns the
        slot, but the EOF was real)."""
        sock = Path(self.directory.name) / "fake.sock"
        sock.write_text("")
        old = Mock(hub_pty=None, hub_retired=False)
        old.poll.side_effect = [None, None]
        old.wait.return_value = 0
        self.register("ep1", Mock(hub_pty=None, hub_retired=False))  # already replaced
        self.seed("ep1", watched=0, position=0.0)

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["set_property", "pause"]:
                return {}
            if cmd[:2] == ["get_property", "time-pos"]:
                return {"data": 1438.0}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": 1440.0}
            return {}

        with patch.object(player, "mpv_ipc", side_effect=fake_ipc):
            player.watch_process(old, "ep1", sock)
        watched, position, duration, last_played = row("ep1", self.db)
        self.assertEqual(watched, 1)
        self.assertEqual(position, 1438.0)

    def test_mpv_dying_before_playback_records_session_error(self):
        """A play click that produced nothing explains itself: the watcher
        records the failure for /api/now instead of failing silently."""
        sock = Path(self.directory.name) / "fake.sock"
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.return_value = 1  # died before the socket appeared
        proc.wait.return_value = 3
        self.register("ep1", proc)
        player.watch_process(proc, "ep1", sock)
        self.assertIn("mpv", player._session_error["error"])
        self.assertEqual(player._session_error["ep_id"], "ep1")

    def test_session_error_not_recorded_for_retired_watcher(self):
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.return_value = -15
        proc.wait.return_value = -15
        self.register("ep1", Mock(hub_pty=None, hub_retired=False))  # someone else owns the slot
        player.watch_process(proc, "ep1", Path(self.directory.name) / "absent.sock")
        self.assertIsNone(player._session_error["error"])

    def test_autoplay_carries_chosen_version(self):
        sock = Path(self.directory.name) / "fake.sock"
        sock.write_text("")
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.side_effect = [None, None]
        proc.wait.return_value = 0
        self.register("ep1", proc, version=2)
        self.seed("ep1", watched=0, position=0.0)

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["set_property", "pause"]:
                return {}
            if cmd[:2] == ["get_property", "time-pos"]:
                return {"data": 1439.0}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": 1440.0}
            return {}

        with patch.object(player, "mpv_ipc", side_effect=fake_ipc), \
                patch.object(player, "autoplay_next") as auto:
            player.watch_process(proc, "ep1", sock)
        auto.assert_called_once_with("ep1", 2)


class StartPlayTransitionTests(LifecycleBase):
    def setUp(self):
        super().setUp()
        self.popen = patch.object(subprocess, "Popen")
        self.popen_mock = self.popen.start()
        self.addCleanup(self.popen.stop)
        self.watcher = patch.object(player, "watch_process", Mock())
        self.watcher.start()
        self.addCleanup(self.watcher.stop)
        self.lua = patch.object(player, "SKIP_CREDITS_LUA",
                                Path(self.directory.name) / "absent.lua")
        self.lua.start()
        self.addCleanup(self.lua.stop)
        self.data = patch.object(config, "DATA", Path(self.directory.name) / "data")
        self.data.start()
        self.addCleanup(self.data.stop)
        (Path(self.directory.name) / "lib" / "Show").mkdir(parents=True)
        (Path(self.directory.name) / "lib" / "Show" / "E01.dub.mkv").write_bytes(b"")
        (Path(self.directory.name) / "lib" / "Show" / "E01.sub.mkv").write_bytes(b"")
        self.library = patch.object(config, "LIBRARY", Path(self.directory.name) / "lib")
        self.library.start()
        self.addCleanup(self.library.stop)
        self.ep = {"id": "ep1", "number": 1,
                   "files": [{"path": "Show/E01.sub.mkv", "label": "sub"},
                             {"path": "Show/E01.dub.mkv", "label": "dub"}]}
        self.show = {"title": "Show", "seasons": [
            {"label": "Season 1", "season": 1, "episodes": [self.ep]}]}
        self.lookup = patch.object(library, "episode_by_id", lambda eid: (self.show, self.ep))
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def args(self):
        return self.popen_mock.call_args[0][0]

    def test_version_reaches_mpv_and_active_entry(self):
        res = player.start_play("ep1", version=1)
        self.assertTrue(res["ok"])
        self.assertEqual(self.args()[1], str(Path(self.directory.name) / "lib" / "Show" / "E01.dub.mkv"))
        with player._active_lock:
            self.assertEqual(player._active["ep1"]["version"], 1)

    def test_second_play_supersedes_the_first(self):
        """One live session: the second play terminates the first process
        and owns the single slot (serialized transition). A fake process is
        no real process group, so stop_process_group signals the process
        itself (TODO 54) — never os.killpg."""
        player.start_play("ep1")
        first = self.popen_mock.return_value
        self.popen_mock.return_value = Mock()
        player.start_play("ep1", version=0)
        first.send_signal.assert_called_once_with(signal.SIGTERM)
        os.killpg.assert_not_called()
        with player._active_lock:
            self.assertEqual(len(player._active), 1)
            self.assertIs(player._active["ep1"]["proc"], self.popen_mock.return_value)

    def test_no_mpv_watch_later_state_is_created(self):
        """hub.db is the only resume store (TODO 24): mpv's own
        --save-position-on-quit would fight an intentional restart/reset."""
        player.start_play("ep1")
        self.assertFalse(any(a.startswith("--save-position-on-quit") for a in self.args()))

    def test_missing_mpv_is_an_actionable_error(self):
        self.popen_mock.side_effect = FileNotFoundError()
        res = player.start_play("ep1")
        self.assertIn("error", res)
        self.assertIn("mpv", res["error"])
        with player._active_lock:
            self.assertEqual(player._active, {})
        self.popen_mock.side_effect = None

    def test_mpv_stderr_is_captured_not_discarded(self):
        player.start_play("ep1")
        log = Path(self.directory.name) / "data" / "mpv-stderr.log"
        self.assertTrue(log.exists())
        self.assertIn(b"===", log.read_bytes())


class ResumeRuleTests(LifecycleBase):
    """The 1 % rule is shared with the UI's resumable() (TODO 24): a
    'Resume' label always actually resumes — the old 60 s floor disagreed
    in the 1–60 s zone."""

    def setUp(self):
        super().setUp()
        self.popen = patch.object(subprocess, "Popen")
        self.popen_mock = self.popen.start()
        self.addCleanup(self.popen.stop)
        self.watcher = patch.object(player, "watch_process", Mock())
        self.watcher.start()
        self.addCleanup(self.watcher.stop)
        self.lua = patch.object(player, "SKIP_CREDITS_LUA",
                                Path(self.directory.name) / "absent.lua")
        self.lua.start()
        self.addCleanup(self.lua.stop)
        (Path(self.directory.name) / "lib" / "Show").mkdir(parents=True)
        (Path(self.directory.name) / "lib" / "Show" / "E01.mp4").write_bytes(b"")
        self.library = patch.object(config, "LIBRARY", Path(self.directory.name) / "lib")
        self.library.start()
        self.addCleanup(self.library.stop)
        self.ep = {"id": "ep1", "number": 1, "files": [{"path": "Show/E01.mp4", "label": "default"}]}
        self.show = {"title": "Show", "seasons": [
            {"label": "Season 1", "season": 1, "episodes": [self.ep]}]}
        self.lookup = patch.object(library, "episode_by_id", lambda eid: (self.show, self.ep))
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    def start_arg(self):
        starts = [a for a in self.popen_mock.call_args[0][0] if a.startswith("--start=")]
        return starts[0] if starts else None

    def test_small_position_resumes(self):
        """28.8 s of a 24-minute episode (2 %): the UI says Resume, so the
        backend must resume — the old 60 s floor restarted instead."""
        self.seed("ep1", watched=0, position=28.8)
        player.start_play("ep1")
        self.assertEqual(self.start_arg(), "--start=0:28")

    def test_sub_percent_position_restarts(self):
        self.seed("ep1", watched=0, position=7.2)  # 0.5 %
        player.start_play("ep1")
        self.assertIsNone(self.start_arg())

    def test_unwatched_past_cutoff_still_resumes(self):
        """Acceptance check 2: pos >= 92 % without watched=1 is resumable."""
        self.seed("ep1", watched=0, position=1350.0)
        player.start_play("ep1")
        self.assertEqual(self.start_arg(), "--start=22:30")

    def test_play_from_the_start_skips_the_saved_point(self):
        """The episode menu's "Play from the start" (Elias, 2026-10-24)."""
        self.seed("ep1", watched=0, position=600.0)
        player.start_play("ep1", from_start=True)
        self.assertIsNone(self.start_arg())

    def test_stopped_rewatch_resumes(self):
        """A watched episode stopped under 92 % resumes — the tile shows that point too."""
        self.seed("ep1", watched=1, position=185.0)
        player.start_play("ep1")
        self.assertEqual(self.start_arg(), "--start=3:5")


class SeasonOrderTests(LifecycleBase):
    """One authoritative order (TODO 24): backend seasons sort exactly as
    the UI's seasonRank — seasons first, then parts, then the rest — so
    autoplay chains Season 2 after Season 1, not Part 1."""

    def build(self):
        lib = Path(self.directory.name) / "lib"
        for d in ("Show/Season 1", "Show/Season 2", "Show/Part 1", "Show/Part 2"):
            (lib / d).mkdir(parents=True, exist_ok=True)
        (lib / "Show/Season 1/Show E01.mkv").write_bytes(b"")
        (lib / "Show/Season 1/Show E02.mkv").write_bytes(b"")
        (lib / "Show/Season 2/Show E01.mkv").write_bytes(b"")
        (lib / "Show/Part 1/Show E01.mkv").write_bytes(b"")
        (lib / "Show/Part 2/Show E01.mkv").write_bytes(b"")
        self.library = patch.object(config, "LIBRARY", lib)
        self.library.start()
        self.addCleanup(self.library.stop)

    def test_scan_orders_seasons_like_the_ui(self):
        self.build()
        scan = library.scan_library()
        show = next(s for s in scan if s["title"] == "Show")
        self.assertEqual([sea["label"] for sea in show["seasons"]],
                         ["Season 1", "Season 2", "Part 1", "Part 2"])

    def test_autoplay_next_after_season_is_next_season(self):
        """The old backend order (parts first, sharing Season 1's key) made
        the last Season 1 episode chain into Part 1."""
        self.build()
        scan = library.scan_library()
        show = next(s for s in scan if s["title"] == "Show")
        s1 = next(sea for sea in show["seasons"] if sea["label"] == "Season 1")
        nxt = player.next_episode(s1["episodes"][-1]["id"])  # an episode id
        s2 = next(sea for sea in show["seasons"] if sea["label"] == "Season 2")
        self.assertEqual(nxt, s2["episodes"][0]["id"])


if __name__ == "__main__":
    unittest.main()