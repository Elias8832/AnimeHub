"""Opening/ending skipping (Decision 39): chapter names → skip ranges,
AniSkip times (opt-in) for what the chapters don't cover, the settings
that pick off/prompt/auto, and the watcher handing the ranges to mpv."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from tests import support  # noqa: F401  (isolated config/data, no network)
from animehub import database, player, settings, skips
from animehub.config import config


ASK = {"op": "prompt", "ed": "prompt"}


def ch(*marks):
    return [{"title": t, "time": s} for s, t in marks]


def aniskip(kind, start, end, length):
    return {"interval": {"startTime": start, "endTime": end}, "skipType": kind,
            "skipId": "x", "episodeLength": length}


class ChapterKindTests(unittest.TestCase):
    def test_names(self):
        for t in ("Opening", "OP", "OP2", "op - Kick Back", "Opening Song"):
            self.assertEqual(skips.chapter_kind(t), "op", t)
        for t in ("Ending", "ED", "ED1", "Credits", "End Credits"):
            self.assertEqual(skips.chapter_kind(t), "ed", t)

    def test_story_chapters_are_never_skippable(self):
        """"Intro" is often the cold open and "Outro" a post-credits scene."""
        for t in ("Intro", "Outro", "Prologue", "Epilogue", "Part A", "Introduction",
                  "Opportunity", "Edge", "Chapter 1", "Preview", "", None, 5):
            self.assertIsNone(skips.chapter_kind(t), t)


class ChapterSegmentTests(unittest.TestCase):
    def test_typical_tv_episode(self):
        """Cold open, OP, part A/B, ED, preview: the opening is a range to
        skip; skipping the ending jumps to the preview, which still plays."""
        chapters = ch((0, "Intro"), (95.5, "Opening"), (185.5, "Part A"), (800, "Part B"),
                      (1300, "Ending"), (1390, "Preview"))
        self.assertEqual(skips.chapter_segments(chapters, 1420.0, ASK), [
            {"kind": "op", "start": 95.5, "end": 185.5, "auto": False, "finish": False},
            {"kind": "ed", "start": 1300.0, "end": 1390.0, "auto": False, "finish": False},
        ])

    def test_post_credits_scene_is_kept(self):
        segs = skips.chapter_segments(ch((0, "Part A"), (1200, "ED"), (1290, "Epilogue")), 1420.0, ASK)
        self.assertEqual(segs, [{"kind": "ed", "start": 1200.0, "end": 1290.0, "auto": False, "finish": False}])

    def test_last_chapter_ending_runs_to_the_end(self):
        segs = skips.chapter_segments(ch((0, "Part A"), (1330, "Credits")), 1420.0, ASK)
        self.assertEqual((segs[0]["end"], segs[0]["finish"]), (1420.0, True))

    def test_implausible_lengths_are_ignored(self):
        """A 10 s or 10 min "opening" is a mislabelled chapter."""
        chapters = ch((0, "OP"), (10, "Part A"), (700, "Ending"), (1400, "x"))
        self.assertEqual(skips.chapter_segments(chapters, 1420.0, ASK), [])

    def test_prefs(self):
        chapters = ch((0, "OP"), (90, "Part A"), (1300, "ED"))
        segs = skips.chapter_segments(chapters, 1390.0, {"op": "auto", "ed": "off"})
        self.assertEqual([(s["kind"], s["auto"]) for s in segs], [("op", True)])
        self.assertEqual(skips.chapter_segments(chapters, 1390.0, {"op": "off", "ed": "off"}), [])

    def test_unsorted_and_junk_input(self):
        chapters = [{"title": "Part A", "time": 90}, {"title": "OP", "time": 0}, {"time": "x"},
                    {"title": "ED", "time": True}, {"title": "ED", "time": 5000}, "junk"]
        self.assertEqual([s["kind"] for s in skips.chapter_segments(chapters, 1400.0, ASK)], ["op"])
        for bad in (None, {}, "x"):
            self.assertEqual(skips.chapter_segments(bad, 1400.0, ASK), [])
        for dur in (0, -5, None, "1400"):
            self.assertEqual(skips.chapter_segments(chapters, dur, ASK), [])


class AniSkipSegmentTests(unittest.TestCase):
    def test_same_length_release(self):
        results = [aniskip("op", 153.346, 243.346, 1469.9685), aniskip("ed", 1370, 1460, 1471)]
        segs = skips.aniskip_segments(results, 1470.0, ASK)
        self.assertEqual([(s["kind"], round(s["start"], 1), round(s["end"], 1), s["finish"]) for s in segs],
                         [("op", 153.4, 243.3, False), ("ed", 1370.0, 1459.0, False)])

    def test_only_an_ending_to_the_very_end_finishes(self):
        segs = skips.aniskip_segments([aniskip("ed", 1340, 1420, 1420)], 1420.0, ASK)
        self.assertTrue(segs[0]["finish"])
        segs = skips.aniskip_segments([aniskip("ed", 1330, 1410, 1420)], 1420.0, ASK)
        self.assertFalse(segs[0]["finish"])

    def test_drift_narrows_the_range(self):
        """A file 10 s longer or shorter: only what is inside the range
        either way is skipped, so no story is lost."""
        segs = skips.aniskip_segments([aniskip("op", 100, 190, 1420)], 1430.0, ASK)
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (110.0, 190.0))
        segs = skips.aniskip_segments([aniskip("op", 100, 190, 1420)], 1410.0, ASK)
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (100.0, 180.0))

    def test_other_releases_are_ignored(self):
        self.assertEqual(skips.aniskip_segments([aniskip("op", 100, 190, 1420)], 1300.0, ASK), [])

    def test_closest_release_wins(self):
        results = [aniskip("op", 90, 180, 1432), aniskip("op", 100, 190, 1421)]
        self.assertEqual(skips.aniskip_segments(results, 1420.0, ASK)[0]["start"], 100.0)

    def test_kinds_prefs_and_junk(self):
        results = [aniskip("op", 100, 190, 1420), aniskip("ed", 1300, 1390, 1420),
                   aniskip("mixed-op", 0, 90, 1420), aniskip("op", "0", "90", 1420),
                   aniskip("op", 0, 90, 0), aniskip("op", 300, 100, 1420), {"skipType": "op"}, "junk"]
        self.assertEqual([s["kind"] for s in skips.aniskip_segments(results, 1420.0, ASK, ("ed",))], ["ed"])
        segs = skips.aniskip_segments(results, 1420.0, {"op": "auto", "ed": "off"})
        self.assertEqual([(s["kind"], s["auto"], s["start"]) for s in segs], [("op", True, 100.0)])
        self.assertEqual(skips.aniskip_segments(None, 1420.0, ASK), [])
        self.assertEqual(skips.aniskip_segments({"results": []}, 1420.0, ASK), [])

    def test_bad_submission_cannot_skip_half_the_episode(self):
        self.assertEqual(skips.aniskip_segments([aniskip("op", 0, 700, 1420)], 1420.0, ASK), [])


class DbTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        p = patch.object(config, "DB", self.dir / "hub.db")
        p.start()
        self.addCleanup(p.stop)
        database.init_db()


class PrefsTests(DbTests):
    def test_defaults(self):
        """Ask for both kinds; AniSkip off until the user switches it on."""
        self.assertEqual(settings.skip_prefs(), ASK)
        payload = settings.settings_payload()
        self.assertEqual(payload["skip"], ASK)
        self.assertIs(payload["aniskip"], False)

    def test_junk_falls_back(self):
        settings.set_setting("skip_op", "auto")
        settings.set_setting("skip_ed", "nonsense")
        self.assertEqual(settings.skip_prefs(), {"op": "auto", "ed": "prompt"})


class AniSkipFetchTests(DbTests):
    def reply(self, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return patch.object(skips, "urlopen", return_value=io.BytesIO(raw))

    def test_found_is_cached(self):
        body = {"found": True, "results": [aniskip("op", 100, 190, 1420)]}
        with self.reply(body) as op:
            self.assertEqual(skips.aniskip_times(52991, 5), body["results"])
            self.assertEqual(skips.aniskip_times(52991, 5), body["results"])
        self.assertEqual(op.call_count, 1)
        self.assertEqual(op.call_args[0][0].full_url,
                         "https://api.aniskip.com/v2/skip-times/52991/5?types[]=op&types[]=ed&episodeLength=0")

    def test_only_the_two_numbers_are_sent(self):
        with self.reply({"results": []}) as op:
            skips.aniskip_times(52991, 5)
        req = op.call_args[0][0]
        self.assertIsNone(req.data)
        self.assertEqual({k.lower() for k in req.headers}, {"user-agent"})

    def test_missing_is_cached_briefly(self):
        missing = HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(skips, "urlopen", side_effect=missing) as op:
            self.assertEqual(skips.aniskip_times(1, 1), [])
            self.assertEqual(skips.aniskip_times(1, 1), [])
        self.assertEqual(op.call_count, 1)
        with database.db() as c:  # three days later: asked again
            c.execute("UPDATE catalog_cache SET ts=?", (time.time() - 3 * 86400,))
        with self.reply({"results": []}) as op:
            skips.aniskip_times(1, 1)
        self.assertEqual(op.call_count, 1)

    def test_failures_are_not_cached(self):
        for fail in (OSError("down"), HTTPError("u", 500, "x", {}, None)):
            with patch.object(skips, "urlopen", side_effect=fail):
                self.assertIsNone(skips.aniskip_times(1, 1))
        for nonsense in (b"not json", b"[1, 2]", b"x" * (skips.MAX_BYTES + 1)):
            with self.reply(nonsense):
                self.assertIsNone(skips.aniskip_times(1, 1))
        with self.reply({"results": []}) as op:
            self.assertEqual(skips.aniskip_times(1, 1), [])
        self.assertEqual(op.call_count, 1)

    def test_bad_ids_never_reach_the_network(self):
        with patch.object(skips, "urlopen") as op:
            for mal, ep in ((None, 1), (1, None), (0, 1), (1, 0), (True, 1), ("1", 1), (1, 2.5)):
                self.assertIsNone(skips.aniskip_times(mal, ep))
        op.assert_not_called()


class MalIdTests(DbTests):
    def test_from_the_stored_entry(self):
        with patch("animehub.franchise.media_entry", return_value={"id": 7, "idMal": 70}):
            self.assertEqual(skips.mal_id(7), 70)
        with patch("animehub.franchise.media_entry", return_value={"id": 7, "idMal": None}):
            self.assertIsNone(skips.mal_id(7))

    def test_older_entries_ask_once(self):
        with patch("animehub.franchise.media_entry", return_value={"id": 7}), \
             patch("animehub.anilist.anilist_request", return_value={"data": {"Media": {"idMal": 70}}}) as req:
            self.assertEqual(skips.mal_id(7), 70)
            self.assertEqual(skips.mal_id(7), 70)
        self.assertEqual(req.call_count, 1)


NO_VPN = {"active": False, "iface": None, "known": True}
VPN = {"active": True, "iface": "wg0", "known": True}


class AllowedTests(DbTests):
    """Decision 39: AniSkip without a VPN only after a yes for that play;
    autoplay carries the answer on, or waits and asks when there is none."""

    def setUp(self):
        super().setUp()
        settings.set_automation("aniskip", True)
        self.addCleanup(player._aniskip_answers.clear)
        self.addCleanup(player.dismiss_next_prompt)

    def allowed(self, vpn, ep="ep1"):
        with patch.object(settings, "vpn_status", return_value=vpn):
            return player.aniskip_allowed(ep)

    def test_switched_off_never(self):
        settings.set_automation("aniskip", False)
        player.answer_aniskip("ep1", True)
        self.assertFalse(self.allowed(VPN))

    def test_with_vpn_or_unknown(self):
        self.assertTrue(self.allowed(VPN))
        self.assertTrue(self.allowed({"active": False, "iface": None, "known": False}))

    def test_no_vpn_needs_a_yes_for_that_episode(self):
        self.assertFalse(self.allowed(NO_VPN))
        player.answer_aniskip("ep2", True)
        self.assertFalse(self.allowed(NO_VPN))
        player.answer_aniskip("ep1", False)
        self.assertFalse(self.allowed(NO_VPN))
        player.answer_aniskip("ep1", True)
        self.assertTrue(self.allowed(NO_VPN))
        player._aniskip_answers["ep1"] = (True, time.time() - player.GRANT_TTL - 1)
        self.assertFalse(self.allowed(NO_VPN))

    def test_warning_switched_off(self):
        settings.set_automation("aniskip_vpn_warn", False)
        self.assertTrue(self.allowed(NO_VPN))

    def test_carry_passes_yes_and_no(self):
        for yes in (True, False):
            player.answer_aniskip("ep0", yes)
            player.carry_aniskip("ep0", "ep1")
            self.assertEqual(player._aniskip_answers["ep1"][0], yes)
        player._aniskip_answers.clear()
        player.carry_aniskip("ep0", "ep1")  # nothing to carry
        self.assertNotIn("ep1", player._aniskip_answers)
        player._aniskip_answers["ep0"] = (True, time.time() - player.CHAIN_TTL - 1)
        player.carry_aniskip("ep0", "ep1")
        self.assertNotIn("ep1", player._aniskip_answers)

    def autoplay(self, vpn):
        with patch.object(player, "next_up", return_value={"id": "ep1", "source": "local"}), \
             patch.object(settings, "vpn_status", return_value=vpn), \
             patch.object(player, "start_play") as start:
            player.autoplay_next("ep0")
        return start

    def test_autoplay_with_an_answer_plays_on(self):
        for yes in (True, False):
            player.answer_aniskip("ep0", yes)
            self.autoplay(NO_VPN).assert_called_once_with("ep1", 0)
            self.assertIsNone(player.next_prompt())
            self.assertEqual(self.allowed(NO_VPN), yes)

    def test_autoplay_without_an_answer_and_no_vpn_waits_and_asks(self):
        self.autoplay(NO_VPN).assert_not_called()
        self.assertEqual((player.next_prompt()["id"], player.next_prompt()["kind"]), ("ep1", "aniskip"))

    def test_autoplay_with_vpn_or_without_the_question_plays_on(self):
        self.autoplay(VPN).assert_called_once_with("ep1", 0)
        settings.set_automation("aniskip_vpn_warn", False)
        self.autoplay(NO_VPN).assert_called_once_with("ep1", 0)
        settings.set_automation("aniskip_vpn_warn", True)
        settings.set_automation("aniskip", False)
        self.autoplay(NO_VPN).assert_called_once_with("ep1", 0)
        self.assertIsNone(player.next_prompt())


class SendTests(DbTests):
    """send_skips: chapters first, AniSkip only when switched on and only
    for a kind the chapters lack, nothing sent to a replaced session."""

    def setUp(self):
        super().setUp()
        p = patch.object(settings, "vpn_status", return_value=VPN)
        p.start()
        self.addCleanup(p.stop)
        self.proc = object()
        player._active["ep1"] = {"proc": self.proc}
        self.addCleanup(player._active.pop, "ep1", None)

    def run_send(self, chapters, entry=(7, 5), results=()):
        sent = []

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["get_property", "chapter-list"]:
                return {"data": chapters}
            sent.append(json.loads(cmd[2])["segments"])
            return {}

        class Now:  # the thread, run in place
            def __init__(self, target, args, daemon):
                self.start = lambda: target(*args)

        with patch.object(player, "mpv_ipc", side_effect=fake_ipc), \
             patch.object(player.threading, "Thread", Now), \
             patch.object(player, "entry_of_episode", return_value=entry) as eoe, \
             patch.object(skips, "mal_id", return_value=70), \
             patch.object(skips, "aniskip_times", return_value=list(results)) as times:
            player.send_skips(self.proc, "ep1", Path("x.sock"), 1420.0)
        return sent, eoe, times

    def test_off_by_default(self):
        sent, eoe, times = self.run_send([], results=[aniskip("op", 5, 95, 1420)])
        eoe.assert_not_called()
        times.assert_not_called()
        self.assertEqual(sent, [])

    def test_chapters_without_aniskip(self):
        sent, _, times = self.run_send(ch((0, "OP"), (90, "Part A")))
        times.assert_not_called()
        self.assertEqual([s["kind"] for s in sent[0]], ["op"])

    def test_aniskip_fills_what_chapters_lack(self):
        settings.set_automation("aniskip", True)
        sent, _, times = self.run_send(ch((0, "OP"), (90, "Part A")),
                                       results=[aniskip("op", 5, 95, 1420), aniskip("ed", 1300, 1390, 1420)])
        times.assert_called_once_with(70, 5)
        self.assertEqual([(s["kind"], s["start"]) for s in sent[0]], [("op", 0.0), ("ed", 1300.0)])

    def test_chapters_cover_both_no_lookup(self):
        settings.set_automation("aniskip", True)
        _, eoe, times = self.run_send(ch((0, "OP"), (90, "Part A"), (1300, "ED")))
        eoe.assert_not_called()
        times.assert_not_called()

    def test_unmatched_file_and_half_episodes(self):
        settings.set_automation("aniskip", True)
        for entry in (None, (7, 5.5)):
            _, _, times = self.run_send([], entry=entry, results=[aniskip("op", 5, 95, 1420)])
            times.assert_not_called()

    def test_both_kinds_off_does_nothing(self):
        settings.set_setting("skip_op", "off")
        settings.set_setting("skip_ed", "off")
        with patch.object(player, "mpv_ipc") as ipc:
            player.send_skips(self.proc, "ep1", Path("x.sock"), 1420.0)
        ipc.assert_not_called()

    def test_no_vpn_without_a_yes_uses_chapters_only(self):
        settings.set_automation("aniskip", True)
        with patch.object(settings, "vpn_status", return_value=NO_VPN):
            sent, _, times = self.run_send(ch((0, "OP"), (90, "Part A")), results=[aniskip("ed", 1300, 1390, 1420)])
        times.assert_not_called()
        self.assertEqual([x["kind"] for x in sent[0]], ["op"])

    def test_replaced_session_gets_nothing(self):
        player._active["ep1"] = {"proc": object()}
        sent, _, _ = self.run_send(ch((0, "OP"), (90, "Part A")))
        self.assertEqual(sent, [])


class WatcherTests(DbTests):
    def test_ranges_are_sent_once(self):
        sock = self.dir / "fake.sock"
        sock.write_text("")
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.side_effect = [None, None, None, 0]
        proc.wait.side_effect = [player.subprocess.TimeoutExpired("mpv", 2), 0, 0]
        player._active["ep1"] = {"proc": proc, "ep_id": "ep1", "percent": 0, "version": 0}
        self.addCleanup(player._active.clear)
        sent = []

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["get_property", "time-pos"]:
                return {"data": 10.0}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": 1420.0}
            if cmd[:2] == ["get_property", "chapter-list"]:
                return {"data": ch((0, "Opening"), (90, "Part A"))}
            if cmd[0] == "script-message":
                sent.append(cmd)
            return {}

        class Now:
            def __init__(self, target, args, daemon):
                self.start = lambda: target(*args)

        with patch.object(player, "mpv_ipc", side_effect=fake_ipc), \
             patch.object(player.threading, "Thread", Now), \
             patch.object(player, "next_up", return_value={"source": None}):
            player.watch_process(proc, "ep1", sock)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "anime-hub-segments")
        self.assertEqual(json.loads(sent[0][2])["segments"][0]["kind"], "op")



if __name__ == "__main__":
    unittest.main()
