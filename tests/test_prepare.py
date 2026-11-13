"""Online episodes made ready before the click (TODO 135, Decision 49): the
VPN rule, the one slot, and a run through the real wait script."""

import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tests.test_real_tools import STAND_IN
from animehub.config import config
from animehub import anicli, database, pages, player, prepare, settings, streaming, system

VPN = {"active": True, "iface": "wg0", "known": True}
NO_VPN = {"active": False, "iface": None, "known": True}
UNKNOWN = {"active": False, "iface": None, "known": False}


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


def left_over(folder):
    """What kept a run's folder from going, for the failure message."""
    lines = [f"{folder} is still there:"]
    for f in sorted(folder.rglob("*")) if folder.exists() else []:
        try:
            f.rename(f.with_name(f.name + ".probe"))
            f.with_name(f.name + ".probe").rename(f)
            state = "free"
        except OSError as e:
            state = f"held: {e}"
        lines.append(f"  {f.name}: {state}")
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/v", "/fo", "csv"], capture_output=True, text=True).stdout
        lines += ["  " + l for l in out.splitlines()
                  if any(n in l.lower() for n in ("sh.exe", "bash", "sleep", "ani-cli", "mpv"))]
    return "\n".join(lines)


class SiteRule(unittest.TestCase):
    def tearDown(self):
        settings.set_automation("vpn_warn", True)

    def ok(self, vpn):
        with mock.patch.object(settings, "vpn_status", return_value=vpn):
            return settings.site_ok()

    def test_vpn_on_or_unknown(self):
        self.assertTrue(self.ok(VPN))
        self.assertTrue(self.ok(UNKNOWN))

    def test_no_vpn_waits_for_a_click(self):
        self.assertFalse(self.ok(NO_VPN))

    def test_warning_off_means_the_user_does_not_mind(self):
        settings.set_automation("vpn_warn", False)
        self.assertTrue(self.ok(NO_VPN))

    def test_linking_and_refreshing_wait_too(self):
        with mock.patch.object(settings, "site_ok", return_value=False), \
                mock.patch.object(anicli, "anicli_path", return_value="/x/ani-cli"), \
                mock.patch.object(streaming, "stream_autolink") as link, \
                mock.patch.object(streaming, "stream_refresh") as refresh:
            streaming.stream_resolve_async(7001)
            pages.stream_freshen_async(7001)
            time.sleep(0.05)
        link.assert_not_called()
        refresh.assert_not_called()


class HeldBack(unittest.TestCase):
    """TODO 183: an old list behind AniList, with no VPN to look, is said
    on the page (vpn_held) instead of staying quietly behind."""
    AIRING = {"status": "RELEASING", "nextAiringEpisode": {"episode": 6}}

    def held(self, ok=False, age=7 * 3600, eps=("1", "2", "3"), m=None):
        link = {"episodes_ts": time.time() - age, "episodes": list(eps)}
        with mock.patch.object(settings, "site_ok", return_value=ok), \
                mock.patch.object(anicli, "anicli_path", return_value="/x/ani-cli"):
            return pages.vpn_held(m or self.AIRING, link)

    def test_old_list_behind_anilist_without_vpn(self):
        self.assertTrue(self.held())

    def test_vpn_on_refreshes_by_itself(self):
        self.assertFalse(self.held(ok=True))

    def test_fresh_or_complete_lists_say_nothing(self):
        self.assertFalse(self.held(age=60))
        self.assertFalse(self.held(eps=("1", "2", "3", "4", "5")))
        self.assertFalse(self.held(m={"status": "FINISHED", "episodes": 3}))

    def test_no_link_says_nothing(self):
        with mock.patch.object(settings, "site_ok", return_value=False):
            self.assertFalse(pages.vpn_held(self.AIRING, None))


class Prepared(unittest.TestCase):
    """The real wait script, on a stand-in ani-cli and a stand-in mpv."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="animehub-prepare-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(prepare.discard)
        self.args = self.dir / "mpv-args.txt"
        self.script = self.dir / "ani-cli"
        self.script.write_bytes(STAND_IN)
        self.mpv = self.dir / "mpv"
        # Started paused (ahead), it stays like a real one until the test lets it go.
        self.mpv.write_bytes(b'#!/bin/sh\nfor a; do printf "%s\\n" "$a"; done >"$HUB_TEST_ARGS.tmp"\n'
                             b'mv "$HUB_TEST_ARGS.tmp" "$HUB_TEST_ARGS"\n'
                             b'n=0\nfor a; do [ "$a" = "--pause" ] || continue\n'
                             b'    while [ ! -e "$HUB_TEST_ARGS.end" ] && [ $n -lt 300 ]; do sleep 0.05; n=$((n + 1)); done\ndone\n')
        self.addCleanup(lambda: Path(str(self.args) + ".end").touch())
        for f in (self.script, self.mpv):
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        for patcher in (mock.patch.object(anicli, "anicli_path", return_value=str(self.script)),
                        mock.patch.object(anicli, "anicli_check", return_value={"ok": True, "missing": []}),
                        mock.patch.object(settings, "vpn_status", return_value=VPN),
                        mock.patch.object(system, "find_program", return_value=str(self.mpv)),
                        mock.patch.dict(os.environ, {"HUB_TEST_FILE": "clip.mp4",
                                                     "HUB_TEST_ARGS": system.shell_path(self.args)}),
                        mock.patch.dict(settings.AUTOMATION_DEFAULTS, {"prepare_online": True})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.link = {"media_id": 900, "query": "Test Show", "title": "Test Show", "idx": 1,
                     "mode": "sub", "episodes": ["1", "2", "3"]}
        for patcher in (mock.patch.object(streaming, "stream_link_get", return_value=self.link),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def slot(self):
        wait_for(lambda: prepare._slot is not None)
        return prepare._slot

    def key(self, ep="stream:900:2"):
        return streaming._prepare_key(self.link, ep, "sub")

    def test_the_switch_s_run_waits_for_the_click_then_runs_mpv_with_the_click_flags(self):
        """The player's Sub/Dub switch (no `ahead`): mpv starts at the go."""
        sock = player.ipc_socket_path()
        taken = prepare.spawn(self.key(), self.link, "2", "sub", sock, player.mpv_env(sock))
        self.assertTrue(wait_for(lambda: prepare.ready(taken)))
        time.sleep(0.5)
        self.assertFalse(self.args.exists(), "mpv started before the click")
        self.assertTrue(prepare.go(taken, ["--start=1:40", "--input-ipc-server=/tmp/x y"]))
        self.assertTrue(wait_for(lambda: self.args.exists()))
        lines = self.args.read_text().splitlines()
        self.assertIn("--start=1:40", lines)
        self.assertIn("--input-ipc-server=/tmp/x y", lines)
        self.assertNotIn("--pause", lines)
        self.assertEqual(lines[-1], "clip.mp4")
        taken["proc"].wait(timeout=5)
        anicli.anicli_release(taken["proc"])
        prepare.clean(taken)

    def ahead(self, ep="stream:900:2"):
        """A prepared run whose mpv has started ahead, taken by its click."""
        streaming.prepare_episode(ep)
        slot = self.slot()
        self.assertTrue(wait_for(lambda: self.args.exists()), "mpv didn't start ahead")
        taken = prepare.take(self.key(ep))
        self.assertIs(taken, slot)
        self.addCleanup(prepare.end, taken)
        return taken

    def click(self, taken, flags, answers=()):
        """The click's go, mpv's answers given: the commands it got.
        Unless told otherwise mpv says yes, and it plays once told to."""
        sent, answers = [], list(answers)

        def ipc(sock, command):
            self.assertEqual(sock, taken["sock"])
            sent.append(command)
            if answers:
                return answers.pop(0)
            return (None, False) if command == ["get_property", "pause"] else (None, None)
        with mock.patch.object(prepare, "_ipc", side_effect=ipc), mock.patch.object(system, "ipc_ready", return_value=True):
            ok = prepare.go(taken, flags)
        return ok, sent

    def test_mpv_starts_ahead_paused_hidden_and_reading_little(self):
        taken = self.ahead()
        lines = self.args.read_text().splitlines()
        self.assertNotIn("--vid=no", lines)  # the picture is read ahead too (only shown nowhere: AHEAD_LUA)
        for flag in ("--pause", "--force-window=no", f"--input-ipc-server={taken['sock']}",
                     f"--script={config.DATA / 'anicli-mpv-ahead.lua'}", f"--script-opts-append=animehub-ahead-dir={taken['dir']}",
                     f"--script-opts-append=animehub-ahead-ttl={prepare.TTL + 60}", "--script-opts-append=animehub-ahead-ahead=30"):
            self.assertIn(flag, lines)
        self.assertEqual(lines[-1], "clip.mp4")  # ani-cli's own arguments still come last
        self.assertEqual((config.DATA / "anicli-mpv-ahead.lua").read_text(), prepare.AHEAD_LUA)
        self.assertEqual(taken["ahead"], streaming.player_flags("stream:900:2", taken["sock"]))
        self.assertTrue((taken["dir"] / "go.started").exists())

    def test_the_click_shows_it_and_plays(self):
        taken = self.ahead()
        ok, sent = self.click(taken, streaming.player_flags("stream:900:2", taken["sock"]))
        self.assertTrue(ok)
        self.assertEqual(sent, [["script-message", "animehub-ahead-show"], ["get_property", "pause"]])

    def test_a_click_its_script_missed_still_plays(self):
        """mpv without scripts stays paused after the message: the hub plays it."""
        taken = self.ahead()
        with mock.patch.object(prepare._playing, "__defaults__", (0.2,)):
            ok, sent = self.click(taken, taken["ahead"], answers=[(None, None)] + [(None, True)] * 100)
        self.assertTrue(ok)
        self.assertEqual(sent[-1], ["set_property", "pause", False])

    def test_a_click_elsewhere_in_the_episode_seeks_first(self):
        taken = self.ahead()
        flags = [*taken["ahead"], "--start=1:40"]
        ok, sent = self.click(taken, flags)
        self.assertTrue(ok)
        self.assertEqual(sent[:2], [["seek", 100.0, "absolute"], ["script-message", "animehub-ahead-show"]])
        # not open yet: it starts there instead
        ok, sent = self.click(taken, flags, answers=[("property unavailable", None)])
        self.assertEqual(sent[:2], [["seek", 100.0, "absolute"], ["set_property", "start", "100.00"]])

    def test_a_changed_setting_starts_afresh(self):
        taken = self.ahead()
        ok, sent = self.click(taken, [*taken["ahead"], "--fs"])
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertFalse((taken["dir"] / "go").exists())

    def test_a_click_before_mpv_started_uses_the_go_file(self):
        """The wait script itself: a go file it finds wins over go.ahead."""
        if not (shutil.which("sh") or shutil.which("bash")):
            self.skipTest("no sh")
        for go_first in (True, False):
            folder = Path(tempfile.mkdtemp(dir=support.TMP))
            out = folder / "args"
            (folder / "go.ahead").write_text("--pause\n--vid=no\n")
            if go_first:
                (folder / "go").write_text("--start=1:40\n")
            env = dict(os.environ, HUB_ANICLI_GO=system.shell_path(folder / "go"), HUB_ANICLI_MPV=system.shell_path(self.mpv),
                       HUB_ANICLI_WAIT="20", HUB_TEST_ARGS=system.shell_path(out))
            Path(str(out) + ".end").touch()
            import subprocess
            subprocess.run([shutil.which("sh") or shutil.which("bash"), system.shell_path(Path(prepare.hub_file("anicli-mpv-wait.sh", prepare.WAIT_SH))),
                            "clip.mp4"], env=env, timeout=10, check=False)
            lines = out.read_text().splitlines()
            self.assertEqual(lines, ["--start=1:40", "clip.mp4"] if go_first else ["--vid=no", "--pause", "clip.mp4"])
            self.assertEqual((folder / "go.started").exists(), not go_first)

    def test_the_lua_keeps_his_settings_for_after_the_click(self):
        lua = prepare.AHEAD_LUA
        for k in ('"cache-secs"', '"demuxer-readahead-secs"'):
            self.assertIn(k, lua)
        self.assertIn("mp.set_property_native(k, o.ahead)", lua)
        self.assertIn('local vo = mp.get_property_native("vo")\nmp.set_property("vo", "null")', lua)
        show = lua.split("local function show()", 1)[1].split("\nend\n", 1)[0]
        # his picture output first, then play: picture and sound start together
        self.assertLess(show.index('mp.set_property_native("vo", vo)'), show.index('mp.set_property_native("pause", false)'))
        self.assertIn("for k, v in pairs(keep) do mp.set_property_native(k, v) end", show)
        self.assertIn('mp.register_script_message("animehub-ahead-show", show)', lua)
        self.assertIn('utils.file_info(o.dir .. "/go.stop")', lua)  # ended by the hub, or forgotten: quits by itself
        self.assertIn("mp.get_time() - born > o.ttl", lua)

    def test_the_stop_file_ends_the_waiting_script_by_itself(self):
        """(an mpv started ahead quits on it by itself, AHEAD_LUA: RealMpv)"""
        sock = player.ipc_socket_path()
        slot = prepare.spawn(self.key(), self.link, "2", "sub", sock, player.mpv_env(sock))
        self.addCleanup(prepare.end, slot)
        self.assertTrue(wait_for(lambda: prepare.ready(slot)))
        (slot["dir"] / "go.stop").touch()
        self.assertTrue(wait_for(lambda: slot["proc"].poll() is not None))

    def test_no_vpn_prepares_nothing(self):
        with mock.patch.object(settings, "vpn_status", return_value=NO_VPN), \
                mock.patch.object(anicli, "anicli_spawn") as spawn:
            streaming.prepare_episode("stream:900:2")
            time.sleep(0.1)
        spawn.assert_not_called()

    def test_vpn_gone_at_the_click_throws_it_away(self):
        streaming.prepare_episode("stream:900:2")
        slot = self.slot()
        with mock.patch.object(settings, "vpn_status", return_value=NO_VPN):
            self.assertIsNone(prepare.take(self.key()))
        self.assertIsNotNone(slot["proc"].poll())
        self.assertTrue(wait_for(lambda: not slot["dir"].exists()), left_over(slot["dir"]))

    def test_a_failed_removal_leaves_the_stop_file(self):
        # Windows: the log is held open, everything else in the folder goes.
        folder = Path(tempfile.mkdtemp(dir=support.TMP))
        (folder / "go.stop").touch()
        with mock.patch.object(prepare.shutil, "rmtree", lambda p, **kw: (p / "go.stop").unlink(missing_ok=True)):
            prepare.clean({"dir": folder})
            self.assertTrue((folder / "go.stop").exists())

    def test_the_setting_turns_it_off(self):
        with mock.patch.dict(settings.AUTOMATION_DEFAULTS, {"prepare_online": False}), \
                mock.patch.object(anicli, "anicli_spawn") as spawn:
            streaming.prepare_episode("stream:900:2")
            time.sleep(0.1)
        spawn.assert_not_called()

    def test_asking_again_starts_nothing_new(self):
        streaming.prepare_episode("stream:900:2")
        slot = self.slot()
        streaming.prepare_episode("stream:900:2")
        time.sleep(0.1)
        self.assertIs(prepare._slot, slot)

    def test_another_episode_takes_the_slot(self):
        streaming.prepare_episode("stream:900:2")
        first = self.slot()
        streaming.prepare_episode("stream:900:3")
        self.assertTrue(wait_for(lambda: prepare._slot is not None and prepare._slot is not first))
        self.assertTrue(wait_for(lambda: first["proc"].poll() is not None))

    def test_a_click_on_something_else_ends_it(self):
        streaming.prepare_episode("stream:900:2")
        slot = self.slot()
        self.assertIsNone(prepare.take(self.key("stream:900:3")))
        self.assertIsNotNone(slot["proc"].poll())

    def test_a_changed_language_is_another_run(self):
        streaming.prepare_episode("stream:900:2")
        self.slot()
        self.assertIsNone(prepare.take(streaming._prepare_key(self.link, "stream:900:2", "dub")))

    def test_a_page_leaves_the_slot_alone_while_something_plays(self):
        with mock.patch.dict(player._active, {"x": {}}), mock.patch.object(prepare, "request") as req:
            streaming.prepare_page("stream:900:2")
        req.assert_not_called()

    def test_an_expired_run_ends(self):
        streaming.prepare_episode("stream:900:2")
        slot = self.slot()
        prepare._expire(slot)
        self.assertIsNone(prepare._slot)
        self.assertIsNotNone(slot["proc"].poll())


class Fallback(unittest.TestCase):
    def test_a_prepared_run_that_fails_starts_the_normal_way_once(self):
        """Its link may have gone stale while it waited."""
        link = {"media_id": 900, "query": "Q", "title": "Q", "idx": 1, "mode": "sub", "episodes": ["2"],
                "episodes_ts": time.time()}
        ep = {"id": "stream:900:2", "number": 2}
        show = {"id": "aid:900", "title": "Q", "seasons": [{"label": "Episodes", "episodes": [ep]}]}
        err = Path(tempfile.mkdtemp(dir=support.TMP)) / "stderr.log"
        err.write_bytes(b"")
        slot = {"proc": mock.Mock(), "dir": err.parent, "err": err, "sock": "/tmp/s"}
        with mock.patch.object(system, "find_program", return_value="/x/mpv"), \
                mock.patch.object(streaming, "stream_autolink", return_value=link), \
                mock.patch("animehub.library.episode_by_id", return_value=(show, ep)), \
                mock.patch.object(prepare, "take", return_value=slot), \
                mock.patch.object(prepare, "go", return_value=True), \
                mock.patch.object(player, "watch_process") as watch:
            streaming.start_stream("stream:900:2")
            self.assertTrue(wait_for(lambda: watch.called))
            failure = watch.call_args.kwargs["failure"]
            with mock.patch.object(streaming, "start_stream", return_value={"ok": True}) as again:
                self.assertIsNone(failure(1))
        self.assertTrue(again.call_args.kwargs["cold"])
        player._active.clear()


class Next(unittest.TestCase):
    def test_near_the_end_only_an_online_next(self):
        for source, calls in (("online", 1), ("local", 0), ("missing", 0)):
            with mock.patch.object(player, "next_up", return_value={"id": "stream:1:2", "source": source}), \
                    mock.patch.object(streaming, "prepare_episode") as prep:
                streaming.prepare_after("stream:1:1")
            self.assertEqual(prep.call_count, calls)

    def test_a_page_prepares_its_next_online_episode(self):
        online = {"online": {"id": "stream:1:2"}, "local": None, "watched": False}
        page = {"entries": [{"id": 1, "slots": [dict(online, watched=True), online]}]}
        with mock.patch.object(streaming, "prepare_page") as prep:
            pages.prepare_next_slot(page, 1)
        prep.assert_called_once_with("stream:1:2")

    def test_a_next_episode_on_disk_prepares_nothing(self):
        page = {"entries": [{"id": 1, "slots": [{"online": {"id": "stream:1:2"}, "local": {"id": "a"}, "watched": False}]}]}
        with mock.patch.object(streaming, "prepare_page") as prep:
            pages.prepare_next_slot(page, 1)
        prep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
