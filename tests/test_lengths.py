"""Episode lengths read from the files: the ffprobe / mpv output parse, the
worker measuring each file once (again when it changes), the library and
show-page payloads carrying `length`, and the Settings switch."""
import os
import subprocess
import unittest
from unittest.mock import patch

from tests import support
from animehub import database, lengths, library, pages, settings
from animehub.config import config


def ran(stdout):
    return subprocess.CompletedProcess([], 0, stdout=stdout)


class Measure(unittest.TestCase):
    def test_reads_ffprobe_and_mpv(self):
        with patch.object(lengths.subprocess, "run", return_value=ran("1463.587122\n")):
            self.assertAlmostEqual(lengths.measure("a.mkv", ("ffprobe", "ffprobe")), 1463.587122)
        mpv = "● Video  --vid=1  (h264 1920x1080 23.976 fps)\nVO: [null] 1920x1080 yuv420p\nLEN=1463.530500\nV: 00:00:00 / 00:24:23 (0%)\nExiting... (End of file)\n"
        with patch.object(lengths.subprocess, "run", return_value=ran(mpv)) as run:
            self.assertAlmostEqual(lengths.measure("a.mkv", ("mpv", "mpv")), 1463.5305)
        self.assertIn("--term-playing-msg=LEN=${=duration}", run.call_args[0][0])

    def test_unreadable_files_have_no_length(self):
        for out in ("N/A\n", "", "LEN=0.000000\n"):
            with patch.object(lengths.subprocess, "run", return_value=ran(out)):
                self.assertIsNone(lengths.measure("a.mkv", ("ffprobe", "ffprobe")), out)
        with patch.object(lengths.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 20)):
            self.assertIsNone(lengths.measure("a.mkv", ("ffprobe", "ffprobe")))
        with patch.object(lengths.subprocess, "run", side_effect=OSError):
            self.assertIsNone(lengths.measure("a.mkv", ("ffprobe", "ffprobe")))


class Worker(unittest.TestCase):
    def setUp(self):
        lengths._loaded["db"] = None
        self.scan = support.use_library(support.case("aot"))
        self.rels = [ep["rel"] for s in self.scan for ep in library.eps_of(s)]
        lengths.scan_done(self.rels)  # not started in tests: nothing runs yet
        self.addCleanup(settings.set_automation, "measure_lengths", True)
        tool = patch.object(lengths, "find_tool", return_value=("ffprobe", "ffprobe"))
        tool.start()
        self.addCleanup(tool.stop)

    def test_each_file_is_measured_once_and_again_when_it_changes(self):
        with patch.object(lengths, "measure", return_value=1440.0) as m:
            lengths._work()
        self.assertEqual(m.call_count, len(self.rels))
        with database.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM file_lengths WHERE seconds=1440").fetchone()[0],
                             len(self.rels))
        library.merge_db(self.scan)
        self.assertTrue(all(ep["length"] == 1440.0 for s in self.scan for ep in library.eps_of(s)))
        # A restart reads them back from the database.
        lengths._loaded["db"] = None
        self.assertEqual(lengths.get(self.rels[0]), 1440.0)

        lengths.scan_done(self.rels)
        with patch.object(lengths, "measure", return_value=1440.0) as m:
            lengths._work()
        m.assert_not_called()
        path = os.path.join(str(config.LIBRARY), self.rels[0])
        with open(path, "wb") as f:
            f.write(b"new file")
        lengths.scan_done(self.rels)
        with patch.object(lengths, "measure", return_value=1500.0) as m:
            lengths._work()
        m.assert_called_once()
        self.assertEqual(lengths.get(self.rels[0]), 1500.0)

    def test_unread_files_carry_no_length(self):
        with patch.object(lengths, "measure", return_value=None):
            lengths._work()
        library.merge_db(self.scan)
        self.assertFalse(any("length" in ep for s in self.scan for ep in library.eps_of(s)))

    def test_switched_off_measures_nothing(self):
        settings.set_automation("measure_lengths", False)
        with patch.object(lengths, "measure") as m:
            lengths._work()
        m.assert_not_called()
        self.assertEqual(lengths.payload()["state"], "off")


class Slots(unittest.TestCase):
    def test_a_show_page_slot_carries_its_files_length(self):
        ep = {"id": "e1", "files": [{"path": "a/E01.mkv", "label": "default"}], "length": 1420.0,
              "watched": False, "position": 0, "duration": 0, "last_played": None}
        slot, = pages.entry_slots({"id": 1}, {1: ep}, None, {}, None, False)
        self.assertEqual(slot["length"], 1420.0)
        slot, = pages.entry_slots({"id": 1}, {1: {**ep, "length": None}}, None, {}, None, False)
        self.assertNotIn("length", slot)


if __name__ == "__main__":
    unittest.main()
