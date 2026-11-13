"""Downloads past the happy path. A film Elias downloaded (Demon Slayer:
Mugen Train, 2026-10-31) sat under Needs a look with 69 leftover files beside
it: yt-dlp gave up part way, ani-cli fetched the film again with ffmpeg, and
the one try at matching the new folder didn't land. Three things hold now:
the folder's release is written down before AniList is asked, a finished
episode keeps no pieces, and a download the last close cut off is tidied at
the next start."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from tests.test_real_tools import STAND_IN, wait_for
from animehub import anicli, database, downloader, library, metadata, settings, streaming, system
from animehub.config import apply_config, config, CONFIG

FILM = {"id": 112151, "format": "MOVIE", "episodes": 1,
        "title": {"english": "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train",
                  "romaji": "Kimetsu no Yaiba: Mugen Ressha-hen"}}
# yt-dlp's half, then the whole file: what ani-cli leaves when its second way works.
GAVE_UP = STAND_IN.replace(
    b'    cp "$HUB_TEST_FILE" "$ANI_CLI_DOWNLOAD_DIR/Test Show Episode $episode.mp4"\n',
    b'    f="$ANI_CLI_DOWNLOAD_DIR/Test Show Episode $episode"\n'
    b'    for piece in mp4.part mp4.part-Frag7 mp4.part-Frag8.part mp4.ytdl vtt; do echo x >"$f.$piece"; done\n'
    b'    cp "$HUB_TEST_FILE" "$f.mp4"\n')


class Downloads(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(GAVE_UP, STAND_IN)
        base = Path(tempfile.mkdtemp(dir=support.TMP))
        self.lib = base / "library"
        self.lib.mkdir()
        self.addCleanup(apply_config, dict(CONFIG))
        apply_config(dict(CONFIG, library_root=self.lib, data_dir=base / "data"))
        for d in (config.DATA, config.CACHE, config.POSTERS, config.EPIMGS):
            d.mkdir(parents=True, exist_ok=True)
        database.init_db()
        self.clear()
        self.addCleanup(self.clear)  # other suites read the list (/api/now)

    def clear(self):
        with downloader._dl_cv:
            downloader._downloads.clear()

    def folder(self, name, *files):
        d = self.lib / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b"x")
        return d

    def note(self, dest, token, **more):
        settings.set_setting(downloader.RUNNING_KEY, json.dumps(
            dict(media_id=900010, token=token, mode="sub", show="Test Show", dest=str(dest), **more)))


class Adopt(Downloads):
    def setUp(self):
        super().setUp()
        self.name = " ".join(system.folder_name(FILM["title"]["english"]).split())
        self.dest = self.folder(self.name, "Demon Slayer Movie Mugen Train Episode 1.mp4")
        self.show = library.scan_library()[0]

    def matched(self):
        with database.db() as c:
            row = c.execute("SELECT anilist_id FROM shows_meta WHERE show_id=?", (self.show["id"],)).fetchone()
        return row and row[0]

    def test_the_title_matcher_gives_a_show_no_film(self):
        """Why the download has to say which release it was: one file named
        "… Episode 1" reads as a show, and a show never gets a film."""
        self.assertEqual(self.show["kind"], "show")
        self.assertIsNone(metadata.pick_candidate([FILM], self.show, 1))

    def test_a_match_anilist_did_not_answer_is_made_on_the_next_pass(self):
        with mock.patch.object(metadata, "fetch_media_full", side_effect=OSError("AniList is busy")):
            downloader._dl_adopt(FILM["id"], self.dest)
        self.assertIsNone(self.matched())
        self.assertEqual(metadata.load_overrides().get(self.show["path"]), {"anilistId": FILM["id"]})
        with mock.patch.object(metadata, "fetch_media_full", return_value=FILM) as fetch, \
                mock.patch.object(metadata.time, "sleep"), \
                mock.patch.object(metadata.franchise, "refresh_franchises"), \
                mock.patch.object(metadata.mapping, "refresh_entry_art"), \
                mock.patch.object(metadata, "refresh_art"):
            metadata.refresh_meta()
        fetch.assert_called_with(id=FILM["id"])
        self.assertEqual(self.matched(), FILM["id"])

    def test_a_folder_that_has_a_match_keeps_it(self):
        with database.db() as c:
            c.execute("INSERT INTO shows_meta(show_id, anilist_id, title) VALUES(?,?,?)", (self.show["id"], 7, "Other"))
        with mock.patch.object(metadata, "fetch_media_full", return_value=FILM):
            downloader._dl_adopt(FILM["id"], self.dest)
        self.assertEqual(self.matched(), 7)
        self.assertEqual(metadata.load_overrides(), {})


class Pieces(Downloads):
    def test_a_finished_episode_keeps_no_pieces(self):
        """The whole run, through the shell: yt-dlp's half goes, the episode,
        its subtitle and another episode's pieces stay."""
        script = self.lib.parent / "ani-cli"
        script.write_bytes(GAVE_UP)
        script.chmod(0o755)
        clip = self.lib.parent / "clip.mp4"
        clip.write_bytes(b"x" * 2000)
        for patcher in (mock.patch.object(anicli, "anicli_path", return_value=str(script)),
                        mock.patch.dict(os.environ, {"HUB_TEST_FILE": system.shell_path(clip)}),
                        mock.patch.object(downloader, "_dl_adopt")):
            patcher.start()
            self.addCleanup(patcher.stop)
        other = self.folder("Test Show", "Test Show Episode 11.mp4.part", "Test Show Episode 11.mp4.ytdl")
        streaming.stream_link_set(900010, "Test Show", "Test Show", 1, None, "Test Show")
        queued = downloader.download_enqueue(900010, ["1"])
        self.assertEqual(queued.get("queued"), 1, queued)
        job = wait_for(lambda: next((j for j in downloader.downloads_payload() if j["status"] not in ("queued", "running")), None), 30)
        self.assertEqual(job and job["status"], "done", job)
        self.assertEqual(Path(queued["dest"]), other)
        self.assertEqual(sorted(os.listdir(other)), ["Test Show Episode 1.mp4", "Test Show Episode 1.vtt",
                                                     "Test Show Episode 11.mp4.part", "Test Show Episode 11.mp4.ytdl"])
        self.assertFalse(settings.get_setting(downloader.RUNNING_KEY))


class CutOff(Downloads):
    PIECES = ("Test Show Episode 2.mp4.part", "Test Show Episode 2.mp4.part-Frag7", "Test Show Episode 2.mp4.part-Frag8.part",
              "Test Show Episode 2.mp4.ytdl", "Test Show Episode 2.vtt")

    def test_the_next_start_tidies_and_says_so(self):
        d = self.folder("Test Show", "Test Show Episode 1.mp4", "Test Show Episode 2.mp4", *self.PIECES)
        self.note(d, "2", had_file=False, created=False)
        downloader.recover()
        self.assertEqual(os.listdir(d), ["Test Show Episode 1.mp4"])
        self.assertFalse(settings.get_setting(downloader.RUNNING_KEY))
        job, = downloader.downloads_payload()
        self.assertEqual((job["status"], job["error"], job["show"], job["token"], job["media_id"]),
                         ("failed", downloader.CLOSED, "Test Show", "2", 900010))
        downloader.recover()  # once
        self.assertEqual(len(downloader.downloads_payload()), 1)

    def test_a_folder_the_download_made_goes_with_it(self):
        d = self.folder("Test Show", "Test Show Episode 2.mp4", *self.PIECES)
        self.note(d, "2", had_file=False, created=True)
        downloader.recover()
        self.assertFalse(d.exists())

    def test_an_episode_that_was_there_before_stays(self):
        d = self.folder("Test Show", "Test Show Episode 2.mp4", *self.PIECES)
        self.note(d, "2", had_file=True, created=False)
        downloader.recover()
        self.assertEqual(os.listdir(d), ["Test Show Episode 2.mp4"])

    def test_a_folder_made_for_a_download_that_never_started_goes(self):
        """ani-cli refusing to start (too old, missing) leaves no empty show folder."""
        dest = self.lib / "New Show"
        job = {"media_id": 900010, "token": "1", "mode": "sub", "show": "New Show", "dest": str(dest)}
        link = {"media_id": 900010, "query": "New Show", "title": "New Show", "idx": 1, "mode": "sub",
                "episodes": ["1"], "meta": {}}
        with mock.patch.object(streaming, "stream_link_get", return_value=link), \
                mock.patch.object(anicli, "anicli_spawn", side_effect=anicli.AniCliError("too old")):
            with self.assertRaises(anicli.AniCliError):
                downloader._dl_run(job)
        self.assertFalse(dest.exists())
        self.assertFalse(settings.get_setting(downloader.RUNNING_KEY))

    def test_nothing_was_running(self):
        for value in ("", "not json", "[]", "{}"):
            settings.set_setting(downloader.RUNNING_KEY, value)
            downloader.recover()
        self.assertEqual(downloader.downloads_payload(), [])


if __name__ == "__main__":
    unittest.main()
