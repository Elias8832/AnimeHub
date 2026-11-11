"""Decision 88: the Android app runs ani-cli in the user's Termux. A fake
Termux runs each job with the local bash, and a fake ani-cli calls the
hub's menu helper and player the way the real one does, so the whole trip
is checked: the hub's scripts go in, ani-cli's files come back."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import support  # noqa: F401
from animehub import android, anicli, downloader, phoneplay, prepare, settings, streaming, system, update

FAKE_ANICLI = r"""#!/bin/sh
# fake ani-cli for the tests: the settings and menus the hub checks for
# ANI_CLI_MENU ANI_CLI_PLAYER_FLAGS ANI_CLI_PLAYER ANI_CLI_MODE ANI_CLI_QUALITY
version_number="5.1.0"
printf '%s\n' "$*" >"$TMPDIR/args-seen"
env >"$TMPDIR/env-seen"
printf '1 Sousou no Frieren\n2 Sousou no Frieren Movie\n' | "$ANI_CLI_MENU" "Select anime: " >/dev/null \
  || { printf '\033[1;31mInvalid anime selection\033[0m\n' >&2; exit 1; }
printf '1\n2\n3\n' | "$ANI_CLI_MENU" "Select episode: " >/dev/null
case "$ANI_CLI_PLAYER" in
  debug) printf 'All links:\nhttps://video.invalid/1080/index.m3u8\n' ;;
  *mpv*) "$ANI_CLI_PLAYER" --referrer=https://ref.invalid/ --sub-file=https://video.invalid/en.vtt \
            --force-media-title="Frieren Episode 1" https://video.invalid/1080/index.m3u8 ;;
  *) echo "player picked by name: $ANI_CLI_PLAYER" >&2; exit 3 ;;
esac
"""


class FakeTermux:
    """Runs a job the way Termux would: bash, its own PATH and TMPDIR, none of
    the app's environment. `refuse` answers like Termux turning a job away."""

    def __init__(self, root, anicli=True):
        self.root = Path(root)
        self.bin = self.root / "usr" / "bin"
        self.tmp = self.root / "tmp"
        self.bin.mkdir(parents=True)
        self.tmp.mkdir()
        # Termux's own tools, not this machine's PATH (which may hold a real ani-cli)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        for tool in ("sh", "bash", "base64", "mktemp", "cat", "rm", "chmod", "mv", "env", "awk", "grep", "sed"):
            found = shutil.which(tool)
            if found:
                (self.tools / tool).symlink_to(found)
        if anicli:
            p = self.bin / "ani-cli"
            p.write_text(FAKE_ANICLI)
            p.chmod(0o755)
        self.refuse = None
        self.jobs = []

    def __call__(self, script, timeout):
        self.jobs.append(script)
        if self.refuse:
            return -1, "", "", self.refuse
        env = {"PATH": f"{self.bin}:{self.tools}", "TMPDIR": str(self.tmp), "HOME": str(self.root)}
        r = subprocess.run([str(self.tools / "bash"), "-c", script], env=env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr, ""


def setUpModule():
    support.use_library(support.case("aot"))  # a database for the settings ani-cli runs read


LINK = {"query": "frieren", "title": "Sousou no Frieren", "mode": "sub"}


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="termux-")
        self.addCleanup(shutil.rmtree, d, True)
        self.termux = FakeTermux(d)
        for target, attr, value in ((system, "ANDROID", True), (android, "transport", self.termux),
                                    (android, "TERMUX_SH", "/bin/sh")):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        android.anicli_copy().unlink(missing_ok=True)
        self.addCleanup(android.anicli_copy().unlink, True)
        android._last.update(ts=0.0, problem="")
        anicli._anicli_checks.clear()


class TermuxAniCli(Base):
    def test_the_script_is_read_from_termux(self):
        self.assertTrue(android.fetch_anicli(force=True))
        self.assertEqual(anicli.anicli_path(), str(android.anicli_copy()))
        self.assertEqual(anicli.anicli_version(), "5.1.0")
        self.assertTrue(anicli.anicli_check()["ok"])
        st = anicli.anicli_status()
        self.assertFalse(st["can_get"])  # no "Get ani-cli" button: Termux updates it
        self.assertEqual(st["needs"], [])  # curl and friends come with Termux's package

    def test_menus_go_through_the_hubs_helper(self):
        found = anicli.anicli_search("frieren")
        self.assertEqual([r["title"] for r in found["results"]], ["Sousou no Frieren", "Sousou no Frieren Movie"])
        self.assertTrue(any('"$T/"' in job for job in self.termux.jobs))  # the helper went across

    def test_the_phone_player_gets_link_referrer_and_subtitles(self):
        got, msg = phoneplay._run_capture(LINK, "1", "sub")
        self.assertIsNone(msg)
        self.assertEqual(got, {"url": "https://video.invalid/1080/index.m3u8", "referrer": "https://ref.invalid/",
                               "subs": ["https://video.invalid/en.vtt"]})
        # ani-cli picks its player by name (*mpv*): the copy kept it
        self.assertIn("anicli-mpv-phone.sh", (self.termux.tmp / "env-seen").read_text())

    def test_only_ani_clis_settings_cross_over(self):
        with mock.patch.dict("os.environ", {"SECRET_APP_THING": "x", "PATH": "/app/bin"}):
            anicli.anicli_search("frieren")
        seen = (self.termux.tmp / "env-seen").read_text()
        self.assertNotIn("SECRET_APP_THING", seen)
        self.assertNotIn("/app/bin", seen)
        self.assertIn("ANI_CLI_MODE=", seen)

    def test_odd_titles_reach_ani_cli_as_one_argument(self):
        anicli.anicli_search("frieren's \"journey\" $(rm -rf ~)")
        self.assertEqual((self.termux.tmp / "args-seen").read_text().strip(), "frieren's \"journey\" $(rm -rf ~)")

    def test_nothing_is_left_in_termux(self):
        anicli.anicli_search("frieren")
        self.assertEqual(sorted(p.name for p in self.termux.tmp.iterdir()), ["args-seen", "env-seen"])

    def test_ani_clis_own_error_reaches_the_page(self):
        with self.assertRaises(anicli.AniCliError) as e:
            anicli.anicli_episodes({"query": "frieren", "title": "Not a real title"})
        self.assertIn("no longer finds", str(e.exception))


class TermuxSetup(Base):
    def test_termux_refusing_other_apps_names_the_fix(self):
        android.fetch_anicli(force=True)
        self.termux.refuse = ("Error: RunCommandService requires `allow-external-apps` property to be set "
                              "to `true` in `~/.termux/termux.properties` file.")
        got, msg = phoneplay._run_capture(LINK, "1", "sub")
        self.assertIsNone(got)
        self.assertEqual(msg, android.NOT_ALLOWED)
        self.assertEqual(streaming.friendly_stream_error(msg, "E1"), android.NOT_ALLOWED)

    def test_no_permission_yet(self):
        android.fetch_anicli(force=True)
        self.termux.refuse = "no-permission"
        with self.assertRaises(anicli.AniCliError) as e:
            anicli.anicli_search("frieren")
        self.assertIn(android.NO_PERMISSION, str(e.exception))

    def test_android_refusing_to_start_termux(self):
        self.termux.refuse = ("java.lang.IllegalStateException: Not allowed to start service Intent "
                              "{ act=com.termux.RUN_COMMAND }: app is in background")
        with self.assertRaises(anicli.AniCliError) as e:
            anicli.anicli_search("frieren")
        self.assertEqual(str(e.exception), android.NOT_STARTED)

    def test_no_termux_at_all(self):
        self.termux.refuse = "no-termux"
        with self.assertRaises(anicli.AniCliError) as e:
            anicli.anicli_search("frieren")
        self.assertEqual(str(e.exception), android.NO_TERMUX)

    def test_termux_without_ani_cli(self):
        (self.termux.bin / "ani-cli").unlink()
        self.assertFalse(android.fetch_anicli(force=True))
        self.assertEqual(android.problem(), android.NO_ANICLI)
        with self.assertRaises(anicli.AniCliError) as e:
            anicli.anicli_search("frieren")
        self.assertEqual(str(e.exception), android.NO_ANICLI)

    def test_ani_cli_removed_after_the_copy_was_made(self):
        android.fetch_anicli(force=True)
        (self.termux.bin / "ani-cli").unlink()
        got, msg = phoneplay._run_capture(LINK, "1", "sub")
        self.assertIsNone(got)
        self.assertEqual(msg, android.NO_ANICLI)

    def test_a_late_termux_is_found_without_a_restart(self):
        self.termux.refuse = "no-permission"  # first start: the permission prompt is still open
        self.assertIsNone(anicli.anicli_path())
        self.termux.refuse = None
        android._last["ts"] = 0.0  # 20 s later
        self.assertEqual(anicli.anicli_path(), str(android.anicli_copy()))

    def test_termux_is_not_asked_over_and_over(self):
        self.termux.refuse = "no-termux"
        for _ in range(5):
            anicli.anicli_path()
        self.assertEqual(len(self.termux.jobs), 1)

    def test_a_failed_fetch_keeps_the_old_copy(self):
        android.fetch_anicli(force=True)
        self.termux.refuse = "no-termux"
        self.assertTrue(android.fetch_anicli(force=True))
        self.assertEqual(anicli.anicli_version(), "5.1.0")


class AndroidRules(Base):
    def test_no_mpv_to_prepare(self):
        with mock.patch.object(prepare.threading, "Timer") as timer:
            prepare.request("k", LINK, "1", "sub", None, {})
        timer.assert_not_called()

    def test_no_downloads_yet(self):
        self.assertIn("error", downloader.download_enqueue(1, ["1"]))

    def test_get_ani_cli_points_to_termux(self):
        self.assertIn("Termux", anicli.fetch_anicli()["error"])


class AndroidVpn(Base):
    def test_no_vpn_means_the_warning_shows(self):
        with mock.patch.object(android, "vpn_probe", return_value=""):
            self.assertEqual(settings.vpn_status(), {"active": False, "iface": None, "known": True})
            self.assertFalse(settings.site_ok())  # nothing reaches the site before a click

    def test_a_vpn_is_seen(self):
        with mock.patch.object(android, "vpn_probe", return_value="tun0"):
            self.assertEqual(settings.vpn_status(), {"active": True, "iface": "tun0", "known": True})

    def test_a_failed_check_says_it_cannot_tell(self):
        with mock.patch.object(android, "vpn_probe", side_effect=RuntimeError("no context")):
            self.assertFalse(settings.vpn_status()["known"])


class AndroidFiles(Base):
    def test_the_picker_opens_on_the_phones_storage(self):
        self.assertEqual(system.browse_start(), system.ANDROID_STORAGE)
        with mock.patch.object(system.Path, "iterdir", return_value=iter([])), \
             mock.patch.object(system.Path, "is_dir", return_value=True):
            self.assertEqual(system.browse_places()[0], ("Phone", system.ANDROID_STORAGE))

    def test_file_access_comes_from_android(self):
        with mock.patch.object(android, "files_probe", return_value=False):
            self.assertFalse(settings.settings_payload()["files_ok"])
        with mock.patch.object(android, "files_probe", return_value=True):
            self.assertTrue(settings.settings_payload()["files_ok"])
        with mock.patch.object(android, "files_probe", side_effect=RuntimeError("no context")):
            self.assertFalse(android.files_ok())


class AndroidUpdates(Base):
    APK = {"name": "AnimeHub-android.apk", "digest": "sha256:" + "ab" * 32,
           "browser_download_url": update.DOWNLOADS + "unstable/AnimeHub-android.apk"}

    def releases(self):
        return [{"tag_name": "unstable", "body": "notes\n<!-- build 900 0123456789abcdef0123456789abcdef01234567 2026-11-12T10:00:00Z -->",
                 "html_url": "https://github.com/Elias8832/AnimeHub/releases/tag/unstable", "assets": [self.APK]},
                {"tag_name": "v0.4", "published_at": "2026-10-31T00:00:00Z", "assets": []}]

    def test_the_app_updates_with_its_apk(self):
        self.assertEqual(update.kind(), "android")
        found = update.pick_unstable(self.releases(), "0.4", {"number": 800, "commit": "x"}, "android")
        self.assertEqual(found["download"], self.APK["browser_download_url"])
        self.assertEqual(found["sha256"], "ab" * 32)
        self.assertTrue(update.installable(found, "android"))

    def test_a_release_without_an_apk_is_not_offered(self):
        rel = self.releases()
        rel[0]["assets"] = []
        self.assertIsNone(update.pick_unstable(rel, "0.4", {"number": 800, "commit": "x"}, "android"))

    def run_apk(self, head):
        handed = []

        def fetch(found, path, share=99):
            Path(path).write_bytes(head + b"rest")
            return head

        with mock.patch.object(update, "_fetch", fetch), mock.patch.object(update, "apk_installer", handed.append):
            update._apk({"download": "x", "sha256": "y"})
        return handed, dict(update._job)

    def test_a_checked_apk_goes_to_androids_installer(self):
        handed, job = self.run_apk(b"PK\x03\x04")
        self.assertEqual(handed, [update.apk_path()])
        self.assertEqual(job["state"], "done")

    def test_anything_else_is_refused(self):
        handed, job = self.run_apk(b"<html")
        self.assertEqual(handed, [])
        self.assertEqual(job["state"], "failed")
        self.assertFalse(update.apk_path().exists())


class NotAndroid(unittest.TestCase):
    def test_the_pc_never_asks_termux(self):
        self.assertFalse(system.ANDROID)
        with mock.patch.object(android, "transport", side_effect=AssertionError("Termux on the PC")):
            anicli.anicli_status()
            anicli.anicli_version()


if __name__ == "__main__":
    unittest.main()
