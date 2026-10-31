"""The real thing where other tests can only fake it (TODO 148). The v0.4
Windows pass found its bugs (TODO 144–147) in what a fake can't show: a busy
pipe, a locked file, a name the disk refuses. GitHub runs this on real Windows
on every push (.github/workflows/tests.yml). No network: online episodes run
through STAND_IN below, which starts the player on a local file.

RealDisk needs nothing. RealMpv needs mpv and ffmpeg and takes a minute, so it
runs only with ANIME_HUB_REAL_TOOLS=1. It plays with no window and no sound."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import support
from animehub import anicli, database, downloader, lengths, library, player, streaming, system
from animehub.config import apply_config, config, CONFIG

# ani-cli as the bridge sees it: its version, its two menus, its player and
# download settings. Nothing here knows a site. Written as bytes by the test:
# a checkout's CRLF line ends would break bash.
STAND_IN = b"""#!/bin/sh
# ANI_CLI_MENU ANI_CLI_PLAYER_FLAGS ANI_CLI_PLAYER ANI_CLI_MODE ANI_CLI_QUALITY
version_number="5.1.0"
episode="" download=""
while [ $# -gt 0 ]; do
    case "$1" in
        -V) echo "$version_number"; exit 0 ;;
        -d) download=1 ;;
        -e) episode=$2; shift ;;
    esac
    shift
done
printf '1 Test Show\\n' | "$ANI_CLI_MENU" "Select anime: " >/dev/null || exit 1
if [ -z "$episode" ]; then
    printf '1\\n2\\n3\\n' | "$ANI_CLI_MENU" "Select episode: "
    exit 0
fi
if [ -n "$download" ]; then
    cp "$HUB_TEST_FILE" "$ANI_CLI_DOWNLOAD_DIR/Test Show Episode $episode.mp4"
    exit
fi
"$ANI_CLI_PLAYER" $ANI_CLI_PLAYER_FLAGS "--force-media-title=Test Show Episode $episode" "$HUB_TEST_FILE"
"""
TITLES = ("Kaguya-sama: Love is War?", "Re:ZERO", 'A "quoted" title', "Fate/stay night", "Trailing dots...", "Show. ")


def wait_for(check, limit=20):
    end = time.time() + limit
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.1)
    return check()


class RealDisk(unittest.TestCase):
    """What this system's disk and shell really do."""

    def test_replace_outlasts_readers(self):
        """Windows refuses to replace an open file: a banner being served
        stayed upscaled with Sharper banners off (TODO 145). Readers come and
        go, as page requests do; with no gap at all a file can't be replaced."""
        d = Path(tempfile.mkdtemp(dir=support.TMP))
        target = d / "banner.jpg"
        target.write_bytes(b"x" * 500_000)
        stop = threading.Event()
        errors = []

        def read():
            while not stop.is_set():
                try:
                    system.read_bytes(target)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    errors.append(e)
                time.sleep(0.002)

        readers = [threading.Thread(target=read, daemon=True) for _ in range(3)]
        for t in readers:
            t.start()
        try:
            for n in range(100):
                tmp = d / f"{n}.tmp"
                tmp.write_bytes(b"y" * 500_000)
                system.replace(tmp, target)
        finally:
            stop.set()
            for t in readers:
                t.join()
        self.assertEqual(errors, [])

    def test_title_folders_can_be_made(self):
        """A new show's folder comes from its title (TODO 146)."""
        base = Path(tempfile.mkdtemp(dir=support.TMP))
        for title in TITLES:
            folder = base / " ".join(system.folder_name(title).split())
            folder.mkdir(exist_ok=True)
            (folder / "E01.mp4").write_bytes(b"x")
            self.assertTrue((folder / "E01.mp4").is_file(), title)

    @unittest.skipUnless(system.WINDOWS and system.git_bash(), "Windows with Git for Windows")
    def test_git_bash_brings_what_ani_cli_needs(self):
        """curl, sed and grep aren't on a normal Windows PATH (TODO 147)."""
        path = os.path.join(os.environ["SystemRoot"], "System32")
        with mock.patch.dict(os.environ, {"PATH": path}):
            self.assertEqual(system.script_needs(anicli.ANICLI_TOOLS), [])
            r = subprocess.run([system.git_bash(), "-c", "command -v curl && command -v sed && command -v grep"],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(os.environ.get("ANIME_HUB_REAL_TOOLS"), "set ANIME_HUB_REAL_TOOLS=1 to play for real")
class RealMpv(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mpv, cls.ffmpeg = system.find_program("mpv"), system.find_program("ffmpeg")
        if not (cls.mpv and cls.ffmpeg):
            raise AssertionError("ANIME_HUB_REAL_TOOLS is set but mpv or ffmpeg is missing")  # never a quiet skip
        base = Path(tempfile.mkdtemp(dir=support.TMP))
        home = base / "mpv-home"
        home.mkdir()
        (home / "mpv.conf").write_text("vo=null\nao=null\n", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"MPV_HOME": str(home)})
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.clip = base / "clip.mkv"
        cls.make_clip(cls.clip)
        cls.show = base / "library" / "Test Show 日本語"
        cls.show.mkdir(parents=True)
        for n in (1, 2, 3):
            shutil.copyfile(cls.clip, cls.show / f"Test Show - {n:02d}.mkv")
        saved = dict(CONFIG)
        cls.addClassCleanup(apply_config, saved)
        apply_config(dict(CONFIG, library_root=base / "library", data_dir=base / "data"))
        for d in (config.DATA, config.CACHE, config.POSTERS, config.EPIMGS):
            d.mkdir(parents=True, exist_ok=True)
        database.init_db()
        library.merge_db(library.scan_library())
        cls.script = base / "ani-cli"
        cls.script.write_bytes(STAND_IN)
        cls.script.chmod(0o755)

    @classmethod
    def make_clip(cls, path):
        """30 s, two audio tracks, two subtitle tracks: nothing to keep in the repo."""
        subs = path.with_suffix(".srt")
        subs.write_text("1\n00:00:00,000 --> 00:00:30,000\nHi\n", encoding="utf-8")
        tracks = [("a:0", "jpn", "Japanese"), ("a:1", "eng", "English"), ("s:0", "eng", "Full"), ("s:1", "eng", "Signs")]
        meta = [x for s, lang, title in tracks for x in (f"-metadata:s:{s}", f"language={lang}", f"-metadata:s:{s}", f"title={title}")]
        subprocess.run([cls.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=30",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-f", "lavfi", "-i", "sine=frequency=880:duration=30",
                        "-i", str(subs), "-i", str(subs), *[x for n in range(5) for x in ("-map", str(n))],
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt", *meta, str(path)], check=True)

    def setUp(self):
        with database.db() as c:
            c.execute("DELETE FROM episodes")
            c.execute("DELETE FROM track_prefs")
        self.addCleanup(self.end_session)

    def end_session(self):
        session = self.session()
        if session:
            player.control("stop")
            if not wait_for(lambda: not self.session(), 12):
                anicli.stop_process_group(session["proc"], system.SIGKILL)

    def session(self):
        with player._active_lock:
            return next(iter(player._active.values()), None)

    def episodes(self):
        show = next(s for s in library.scan_library() if s["title"].startswith("Test Show"))
        return sorted((e for sea in show["seasons"] for e in sea["episodes"]), key=lambda e: e["number"])

    def playing(self, ep_id=None):
        """The session once mpv reports a position, on `ep_id` if given."""
        def ready():
            a = self.session()
            return a if a and a.get("position") is not None and (ep_id is None or a["ep_id"] == ep_id) else None
        a = wait_for(ready, 30)
        self.assertIsNotNone(a, "mpv never reported a position")
        return a

    def press(self, action, value=None):
        self.assertNotIn("error", player.control(action, value))

    def see(self, name, value, near=0.0):
        """mpv itself says so soon. Read here, not from the button's reply:
        an mpv event can arrive ahead of a reply's value (TODO 149)."""
        sock = self.session()["sock"]

        def now():
            got = (player.mpv_ipc(sock, ["get_property", name]) or {}).get("data")
            return got is not None and abs(got - value) <= near

        self.assertTrue(wait_for(now, 8), f"{name} never became {value}")

    def picks(self):
        tracks = self.session()["tracks"]
        return {kind: [t["title"] for t in tracks[kind] if t["selected"]] for kind in tracks}

    def test_a_session_outlives_its_first_seconds(self):
        """A busy pipe counted as "mpv is gone" and the hub killed it 10 s
        later: most episodes stopped there on Windows (TODO 144)."""
        self.assertEqual(player.start_play(self.episodes()[0]["id"]), {"ok": True})
        self.playing()
        time.sleep(12)
        a = self.session()
        self.assertIsNotNone(a, "the session ended")
        self.assertIsNone(a["proc"].poll())
        self.assertGreater(a["position"], 7)

    def test_many_clients_at_once(self):
        """The watcher, the skip ranges and phone buttons share mpv's pipe."""
        player.start_play(self.episodes()[0]["id"])
        sock = self.playing()["sock"]
        errors = []

        def ask():
            for _ in range(40):
                try:
                    player.mpv_ipc(sock, ["get_property", "volume"])
                except OSError as e:
                    errors.append(repr(e))

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_the_remote_buttons(self):
        player.start_play(self.episodes()[0]["id"])
        self.playing()
        self.press("toggle")
        self.see("pause", True)
        self.press("toggle")
        self.see("pause", False)
        self.press("seek_to", 20)
        self.see("time-pos", 20, near=3)
        self.press("volume", 55)
        self.see("volume", 55)
        self.press("speed", 1.5)
        self.addCleanup(player.control, "speed", 1.0)
        self.see("speed", 1.5)
        self.press("sub_delay", 0.5)
        self.see("sub-delay", 0.5)

    def test_next_marks_watched_and_keeps_the_tracks(self):
        eps = self.episodes()
        player.start_play(eps[0]["id"])
        self.playing()
        tracks = wait_for(lambda: self.session().get("tracks"))
        self.assertEqual(self.picks(), {"audio": ["Japanese"], "sub": ["Full"]})
        self.press("audio", next(t["id"] for t in tracks["audio"] if t["title"] == "English"))
        self.press("sub", next(t["id"] for t in tracks["sub"] if t["title"] == "Signs"))

        def remembered():
            with database.db() as c:
                saved = str(c.execute("SELECT audio, sub FROM track_prefs").fetchall())
            return "English" in saved and "Signs" in saved

        self.assertTrue(wait_for(remembered, 10))
        self.press("next")
        self.playing(eps[1]["id"])
        self.assertTrue(wait_for(lambda: self.session() and self.picks() == {"audio": ["English"], "sub": ["Signs"]}, 15))
        with database.db() as c:
            self.assertEqual(c.execute("SELECT watched FROM episodes WHERE id=?", (eps[0]["id"],)).fetchone(), (1,))

    def test_marks_land_on_the_seek_bar(self):
        player.start_play(self.episodes()[0]["id"])
        sock = self.playing()["sock"]
        segments = [{"kind": "op", "start": 5.0, "end": 10.0}, {"kind": "ed", "start": 20.0, "end": 25.0, "finish": True}]
        player.mpv_ipc(sock, ["script-message", "anime-hub-segments", json.dumps({"segments": segments, "marks": True})])

        def titles():
            data = (player.mpv_ipc(sock, ["get_property", "chapter-list"]) or {}).get("data") or []
            return [c.get("title") for c in data]
        self.assertTrue(wait_for(lambda: {"Opening", "Ending"} <= set(titles()), 5), titles())

    def test_lengths_with_mpv_and_ffprobe(self):
        self.assertAlmostEqual(lengths.measure(self.clip, ("mpv", self.mpv)), 30, delta=1)
        ffprobe = system.find_program("ffprobe")
        if ffprobe:
            self.assertAlmostEqual(lengths.measure(self.clip, ("ffprobe", ffprobe)), 30, delta=1)

    def online(self, media_id, display="Test Show"):
        """A linked release, played and downloaded through STAND_IN."""
        for patcher in (mock.patch.object(anicli, "anicli_path", return_value=str(self.script)),
                        mock.patch.dict(os.environ, {"HUB_TEST_FILE": system.shell_path(self.clip)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        streaming.stream_link_set(media_id, "Test Show", "Test Show", 1, None, display)

    def test_an_online_episode_through_the_shell(self):
        """bash, ani-cli and mpv, with the hub's flags surviving Git Bash, the
        pipe working through all of that, and Stop ending the whole tree."""
        self.online(900001)
        self.assertEqual(streaming.start_stream("stream:900001:2", from_start=True).get("error"), None)
        a = self.playing("stream:900001:2")
        self.press("seek_to", 15)
        self.see("time-pos", 15, near=3)
        proc = a["proc"]
        self.press("stop")
        self.assertTrue(wait_for(lambda: not self.session(), 12))
        self.assertIsNotNone(proc.poll())

    def test_a_download_for_a_title_windows_refuses(self):
        self.online(900002, "Kaguya-sama: Love is War?")
        queued = downloader.download_enqueue(900002, ["2"])
        self.assertEqual(queued.get("queued"), 1, queued)
        job = wait_for(lambda: next((j for j in downloader.downloads_payload()
                                     if j["media_id"] == 900002 and j["status"] not in ("queued", "running")), None), 30)
        self.assertEqual(job and job["status"], "done", job)
        self.assertTrue((Path(queued["dest"]) / "Test Show Episode 2.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
