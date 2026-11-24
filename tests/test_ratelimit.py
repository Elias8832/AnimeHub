"""TODO 279: AniList counts requests per address, so every hub on one
computer (the app and review copies) shares one pace, kept in a small
locked file. A 429 stops all of them until its Retry-After is up, and what
AniList counted beyond this computer (a phone on the same network) holds
that many slots. Browse counts a busy answer's wait down and asks again."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from tests import support
from tests.test_web import APP
from animehub import anilist, system


def headers(**kw):
    m = Message()
    for k, v in kw.items():
        m[k.replace("_", "-")] = str(v)
    return m


class Shared(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(dir=support.TMP))
        self.file = d / "gate.json"
        p = mock.patch.object(anilist, "GATE_FILE", self.file)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(setattr, anilist._al_ctx, "deadline", None)
        self.fg, self.bg = anilist.ANILIST_PER_MIN, anilist.ANILIST_BACKGROUND_PER_MIN

    def other_hub(self, code):
        """Run `code` in a second process, as another hub on this computer would."""
        src = ("import sys, time; sys.path.insert(0, %r)\n"
               "from animehub import anilist; anilist.GATE_FILE = %r\n" % (str(support.ROOT), str(self.file))) + code
        out = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=60, env=dict(os.environ))
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_two_hubs_share_one_pace(self):
        now = time.time()
        for _ in range(self.fg - 1):
            self.assertEqual(anilist._anilist_try(self.fg, now), 0)
        # The other hub takes the last slot, then finds the minute full.
        self.assertEqual(self.other_hub("print(anilist._anilist_try(%d, %r))" % (self.fg, now)), "0")
        self.assertGreater(float(self.other_hub("print(anilist._anilist_try(%d, %r))" % (self.fg, now))), 50)
        self.assertGreater(anilist._anilist_try(self.fg, now), 50)

    def test_a_page_in_one_hub_holds_back_background_work_in_another(self):
        self.other_hub("print(anilist._anilist_try(28, time.time()))")  # the user opened Browse in the app
        self.assertGreater(anilist._anilist_try(self.bg, time.time(), background=True), 0)

    def test_a_429_stops_every_hub(self):
        busy = HTTPError(anilist.ANILIST_URL, 429, "Too Many Requests", headers(Retry_After=20), None)
        with mock.patch.object(anilist, "urlopen", side_effect=busy), anilist.quick(), \
                self.assertRaises(anilist.AniListTransientError) as e:
            anilist.anilist_request({"query": "{x}"})
        self.assertEqual(anilist.error_hint(e.exception)["retry_in"], 20)
        wait = float(self.other_hub("print(anilist._anilist_try(28, time.time()))"))
        self.assertTrue(15 < wait <= 20, wait)  # the other hub waits it out without asking AniList

    def test_anilists_own_count_holds_slots(self):
        """5 left of 30, none of them ours: the phone on the same network used them."""
        now = time.time()
        anilist._anilist_saw(headers(X_RateLimit_Limit=30, X_RateLimit_Remaining=5), now)
        self.assertGreater(anilist._anilist_try(self.bg, now, background=True), 0)
        for _ in range(self.fg - 25):
            self.assertEqual(anilist._anilist_try(self.fg, now), 0)
        self.assertGreater(anilist._anilist_try(self.fg, now), 50)
        self.assertEqual(anilist._anilist_try(self.fg, now + 61), 0)  # a minute on, they're gone

    def test_our_own_requests_arent_counted_twice(self):
        now = time.time()
        for _ in range(10):
            anilist._anilist_try(self.fg, now)
        anilist._anilist_saw(headers(X_RateLimit_Limit=30, X_RateLimit_Remaining=20), now)
        self.assertEqual(json.loads(self.file.read_text())["ext"][0], 0)

    def test_an_answer_is_read_for_the_count(self):
        class Reply:
            headers = headers(X_RateLimit_Limit=30, X_RateLimit_Remaining=2)
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return b'{"data": {}}'
        with mock.patch.object(anilist, "urlopen", return_value=Reply()):
            anilist.anilist_request({"query": "{x}"})
        self.assertEqual(json.loads(self.file.read_text())["ext"][0], 27)

    def test_a_damaged_record_starts_over(self):
        for junk in ("{not json", "[1, 2]", '{"t": "x", "fg": [], "pause": null, "ext": 5}'):
            self.file.write_text(junk)
            self.assertEqual(anilist._anilist_try(self.fg, time.time()), 0, junk)
            self.assertEqual(len(json.loads(self.file.read_text())["t"]), 1)

    def test_no_file_paces_this_hub_alone(self):
        self.addCleanup(anilist._mem.update, t=list(anilist._mem["t"]), fg=anilist._mem["fg"])
        with mock.patch.object(system, "locked_file", side_effect=PermissionError("read-only")):
            self.assertEqual(anilist._anilist_try(self.fg, time.time()), 0)
        self.assertFalse(self.file.exists())


class RuntimeFile(unittest.TestCase):
    def test_the_users_runtime_dir(self):
        d = tempfile.mkdtemp(dir=support.TMP)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}), mock.patch.object(system, "WINDOWS", False):
            self.assertEqual(system.runtime_file("x.json"), Path(d) / "x.json")

    def test_a_shared_temp_dir_names_the_user(self):
        if not hasattr(os, "getuid"):
            self.skipTest("POSIX only")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}), mock.patch.object(system, "WINDOWS", False):
            self.assertEqual(system.runtime_file("x.json").name, "%d-x.json" % os.getuid())


class BrowseHeals(unittest.TestCase):
    """The page side, pinned as source: a busy answer's wait is counted down
    and the list asked for again."""

    def test_browse_counts_the_wait_down(self):
        self.assertIn("o.due = e?.retry_in ? Date.now() + e.retry_in * 1000 : 0;", APP)
        self.assertIn("trying again in ${brLeft(o.due)} s", APP)
        self.assertIn("if (over) BR_A.bretryo(); else if (c.items.length) BR_A.bmore(); else BR_A.bretry();", APP)


if __name__ == "__main__":
    unittest.main()
