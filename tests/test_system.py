"""The OS seam (TODO 74, Decision 40: the Windows port). Step 0: the hub
imports on Windows at all, and the process helpers take the Windows road
there without touching the POSIX-only calls."""

import errno
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests import support  # noqa: F401  (isolated config/data, no network)
from animehub import anicli, settings, system

# A child Python that looks like Windows before animehub loads: no pty /
# fcntl / termios, no SIGKILL / killpg / getpgrp, sys.platform "win32".
# Stdlib modules are loaded first, as on a real Windows Python — only the
# hub's own code must cope with the fake platform.
FAKE_WINDOWS = textwrap.dedent("""
    import sys, os, signal
    import http.server, json, mimetypes, re, shutil, socket, sqlite3, subprocess
    import tempfile, threading, urllib.request, uuid, webbrowser
    for name in ("pty", "fcntl", "termios", "tty"):
        sys.modules[name] = None
    for name in ("SIGKILL",):
        if hasattr(signal, name):  # already gone on a real Windows
            delattr(signal, name)
    for name in ("killpg", "getpgrp", "setsid"):
        if hasattr(os, name):
            delattr(os, name)
    sys.platform = "win32"
    sys.path.insert(0, sys.argv[1])
    import animehub.server
    from animehub import system
    assert system.WINDOWS and system.SIGKILL == signal.SIGTERM
    assert system.open_pty() == (None, None)
    print("ok")
""")


class WindowsImportTests(unittest.TestCase):
    def test_hub_imports_on_fake_windows(self):
        r = subprocess.run([sys.executable, "-c", FAKE_WINDOWS, str(support.ROOT)],
                           capture_output=True, text=True, env=os.environ, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ok")


class WindowsProcessTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(system, "WINDOWS", True), patch.object(os, "killpg", create=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_stop_uses_taskkill_tree_not_killpg(self):
        proc = MagicMock(pid=4242)
        with patch.object(system.subprocess, "run", return_value=MagicMock(returncode=0)) as run:
            anicli.stop_process_group(proc)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        os.killpg.assert_not_called()
        proc.send_signal.assert_not_called()

    def test_failed_taskkill_falls_back_to_the_process(self):
        proc = MagicMock(pid=4242)
        with patch.object(system.subprocess, "run", return_value=MagicMock(returncode=128)):
            anicli.stop_process_group(proc, system.SIGKILL)
        proc.send_signal.assert_called_once_with(system.SIGKILL)

    def test_fake_pids_never_reach_taskkill(self):
        for pid in (MagicMock(), None, 1, 0, -1):
            with self.subTest(pid=pid), patch.object(system.subprocess, "run") as run:
                anicli.stop_process_group(MagicMock(pid=pid))
                run.assert_not_called()

    def test_spawn_runs_the_script_in_git_bash_without_pty(self):
        with patch.object(anicli, "anicli_path", return_value=r"C:\scoop\apps\ani-cli\current\ani-cli"), \
                patch.object(anicli, "anicli_check", return_value=None), \
                patch.object(system, "git_bash", return_value=r"C:\Git\bin\bash.exe"), \
                patch.object(system, "detached", return_value={"creationflags": 512}), \
                patch.object(anicli.subprocess, "Popen") as popen:
            proc = anicli.anicli_spawn(["-V"], {}, None, None)
        self.assertEqual(popen.call_args.args[0],
                         [r"C:\Git\bin\bash.exe", r"C:\scoop\apps\ani-cli\current\ani-cli", "-V"])
        kw = popen.call_args.kwargs
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["creationflags"], 512)
        self.assertNotIn("start_new_session", kw)
        self.assertIsNone(proc.hub_pty)
        anicli.anicli_release(proc)  # no fd to close

    def test_no_git_bash_is_a_readable_error(self):
        with patch.object(anicli, "anicli_path", return_value=r"C:\bin\ani-cli"), \
                patch.object(anicli, "anicli_check", return_value=None), \
                patch.object(system, "git_bash", return_value=None), \
                patch.object(anicli.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(anicli.AniCliError, "Git for Windows"):
                anicli.anicli_spawn(["-V"], {}, None, None)
        popen.assert_not_called()

    def test_shims_run_directly(self):
        for exe in (r"C:\scoop\shims\ani-cli.exe", r"C:\bin\ani-cli.CMD"):
            self.assertEqual(system.script_argv(exe), [exe])

    def test_bare_script_on_path_wins_over_shim(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ani-cli").write_text("#!/bin/sh\n")
            with patch.dict(os.environ, {"PATH": d}):
                self.assertEqual(system.find_script("ani-cli"), os.path.join(d, "ani-cli"))

    def test_scoop_sh_shim_leads_to_the_real_script(self):
        """Scoop puts a tiny sh shim on PATH; the feature check must read
        the script it starts (seen on Windows, ani-cli 5.1 from extras)."""
        with tempfile.TemporaryDirectory() as d:
            shims, app = Path(d) / "shims", Path(d) / "apps" / "ani-cli" / "current"
            shims.mkdir()
            app.mkdir(parents=True)
            (shims / "ani-cli").write_text("#!/bin/sh\n")
            (app / "ani-cli").write_text("#!/bin/sh\n")
            with patch.dict(os.environ, {"PATH": str(shims)}):
                self.assertEqual(system.find_script("ani-cli"), str(app / "ani-cli"))

    def test_env_paths_use_forward_slashes(self):
        with patch.object(anicli, "anicli_menu_helper", return_value=Path(r"C:\Users\Max Muster\anicli-menu.sh")), \
                patch.object(anicli, "stream_prefs", return_value={"quality": "best", "mode": "sub"}):
            env = anicli.anicli_env(capture=r"C:\Temp\cap.txt")
        self.assertEqual(env["ANI_CLI_MENU"], "C:/Users/Max Muster/anicli-menu.sh")
        self.assertEqual(env["HUB_ANICLI_CAPTURE"], "C:/Temp/cap.txt")
        self.assertIn("--input-ipc-server=", env["MSYS2_ARG_CONV_EXCL"])


class WindowsIpcTests(unittest.TestCase):
    """mpv's IPC on Windows is a named pipe (TODO 74 item 3)."""

    def setUp(self):
        p = patch.object(system, "WINDOWS", True)
        p.start()
        self.addCleanup(p.stop)

    def test_pipe_address(self):
        sock = system.ipc_address("anime-hub-12345678")
        self.assertEqual(str(sock).replace("/", "\\"), r"\\.\pipe\anime-hub-12345678")
        self.assertTrue(system.ipc_ready(sock))  # no file to wait for: connecting tells
        system.ipc_remove(sock)  # nothing to unlink, never raises

    def fake_pipe(self, answer=None, block=None):
        f = MagicMock()
        f.read.side_effect = (lambda n: (block.wait(5), b"")[1]) if block else [answer, b""]
        return f

    def test_exchange_reads_one_line(self):
        f = self.fake_pipe(b'{"data":12.5,"error":"success"}\n')
        with patch("builtins.open", return_value=f) as op:
            buf = system.ipc_exchange(system.ipc_address("x"), b"req\n", 2.0)
        self.assertEqual(buf, b'{"data":12.5,"error":"success"}\n')
        self.assertEqual(op.call_args.args[1:], ("r+b",))
        f.write.assert_called_once_with(b"req\n")
        f.close.assert_called_once()

    def test_silent_mpv_times_out_as_oserror(self):
        import threading
        block = threading.Event()
        self.addCleanup(block.set)
        with patch("builtins.open", return_value=self.fake_pipe(block=block)):
            with self.assertRaises(OSError):  # the watcher's "not there" error
                system.ipc_exchange(system.ipc_address("x"), b"req\n", 0.2)

    def test_missing_pipe_is_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError(2, "no pipe")):
            with self.assertRaises(FileNotFoundError):
                system.ipc_exchange(system.ipc_address("x"), b"req\n", 1.0)

    def test_busy_pipe_is_tried_again(self):
        """Python reports ERROR_PIPE_BUSY as EINVAL. Two clients at once (the
        watcher and the skip ranges) ended the session on Windows."""
        busy = OSError(errno.EINVAL, "Invalid argument")
        good = self.fake_pipe(b'{"data":1,"error":"success"}\n')
        with patch("builtins.open", side_effect=[busy, FileNotFoundError(2, "gap"), good]):
            self.assertIn(b'"data":1', system.ipc_exchange(system.ipc_address("x"), b"req\n", 2.0))

    def test_pipe_stays_busy_until_the_timeout(self):
        with patch("builtins.open", side_effect=OSError(errno.EINVAL, "Invalid argument")):
            with self.assertRaises(OSError):
                system.ipc_exchange(system.ipc_address("x"), b"req\n", 0.05)


class WindowsFileTests(unittest.TestCase):
    """Windows refuses more than POSIX does."""

    def setUp(self):
        p = patch.object(system, "WINDOWS", True)
        p.start()
        self.addCleanup(p.stop)

    def test_replace_waits_out_a_reader(self):
        """A banner the hub is serving can't be replaced until it's closed:
        one banner in 14 stayed upscaled with Sharper banners off."""
        locked = PermissionError(13, "Access is denied")
        with patch.object(system.os, "replace", side_effect=[locked, locked, None]) as replace, \
                patch.object(system.time, "sleep"):
            system.replace("a", "b")
        self.assertEqual(replace.call_count, 3)

    def test_replace_gives_up_in_the_end(self):
        with patch.object(system.os, "replace", side_effect=PermissionError(13, "Access is denied")), \
                patch.object(system.time, "sleep"):
            with self.assertRaises(PermissionError):
                system.replace("a", "b")

    def test_folder_names_windows_can_make(self):
        for title, name in (("Kaguya-sama: Love is War?", "Kaguya-sama  Love is War"), ("Re:ZERO", "Re ZERO"),
                            ('A "quoted" title', "A  quoted  title"), ("Fate/stay night", "Fate stay night"),
                            ("Trailing dots...", "Trailing dots"), ("Show. ", "Show")):
            self.assertEqual(system.folder_name(title), name)

    def test_only_git_for_windows_can_be_missing(self):
        """curl, sed and grep come with Git for Windows' bash; they aren't on
        PATH, and the tour asked people to install them."""
        with patch.object(system, "git_bash", return_value=r"C:\Git\bin\bash.exe"):
            self.assertEqual(system.script_needs(("curl", "sed", "grep")), [])
        with patch.object(system, "git_bash", return_value=None):
            self.assertEqual(system.script_needs(("curl", "sed", "grep")), ["Git for Windows"])


class WindowsDesktopTests(unittest.TestCase):
    """Folders, VPN, file manager, browsers on Windows (TODO 74 items 1,
    7–10, 12). Windows paths are faked with POSIX ones where the code
    checks them on disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        p = patch.object(system, "WINDOWS", True)
        p.start()
        self.addCleanup(p.stop)

    def test_appdata_folders_unless_xdg_is_set(self):
        from animehub import config as cfg
        env = {"APPDATA": str(self.d / "Roaming"), "LOCALAPPDATA": str(self.d / "Local")}
        with patch.dict(os.environ, env), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_CONFIG_HOME", None)
            os.environ.pop("XDG_DATA_HOME", None)
            self.assertEqual(cfg._default_config_path(), self.d / "Roaming" / "anime-hub" / "config.json")
            self.assertEqual(cfg._default_data_dir(), self.d / "Local" / "anime-hub")
        # support.py's XDG dirs still win, so the suite stays isolated on Windows.
        self.assertTrue(str(cfg._default_data_dir()).startswith(str(support.TMP)))

    def test_vpn_by_adapter_name_or_driver(self):
        cases = [
            ([("Ethernet", "Intel(R) Ethernet", True), ("home", "WireGuard Tunnel", True)], "home"),
            ([("ProtonVPN", "ProtonVPN Tunnel", True)], "ProtonVPN"),
            ([("Tailscale", "Tailscale Tunnel (Wintun)", True)], None),  # mesh, not a VPN
            ([("Mullvad", "Mullvad Tunnel", False)], None),  # installed, not connected
            ([("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter", True)], None),
        ]
        for adapters, iface in cases:
            with self.subTest(adapters=adapters), patch.object(system, "windows_adapters", return_value=adapters):
                v = settings.vpn_status()
                self.assertEqual((v["active"], v["iface"], v["known"]), (iface is not None, iface, True))
        with patch.object(system, "windows_adapters", side_effect=OSError("no iphlpapi")):
            self.assertFalse(settings.vpn_status()["known"])

    def test_programs_are_found_as_exe_first(self):
        """PATHEXT puts .COM ahead: Settings showed mpv's console stub mpv.com."""
        found = {"mpv.exe": r"C:\mpv\mpv.exe", "mpv": r"C:\mpv\mpv.COM"}
        with patch.object(system.shutil, "which", side_effect=found.get):
            self.assertEqual(system.find_program("mpv"), r"C:\mpv\mpv.exe")
        with patch.object(system.shutil, "which", side_effect={"upscayl": r"C:\u\upscayl.cmd"}.get):
            self.assertEqual(system.find_program("upscayl"), r"C:\u\upscayl.cmd")

    def test_open_external_uses_startfile(self):
        with patch.object(system.os, "startfile", create=True) as start, \
                patch.object(system.subprocess, "Popen") as popen:
            system.open_external("https://anilist.co/anime/1")
        start.assert_called_once_with("https://anilist.co/anime/1")
        popen.assert_not_called()

    def test_drive_letters_as_places(self):
        real = Path.is_dir
        with patch.object(Path, "is_dir", lambda p: str(p) in ("C:\\", "D:\\") or real(p)):
            labels = [label for label, _ in system.browse_places()]
        self.assertEqual(labels, ["Home", "C:", "D:"])

    def test_browsers_from_install_dirs(self):
        edge = self.d / "x86" / "Microsoft" / "Edge" / "Application"
        edge.mkdir(parents=True)
        (edge / "msedge.exe").write_text("")
        with patch.dict(os.environ, {"PROGRAMFILES(X86)": str(self.d / "x86"), "PROGRAMFILES": str(self.d / "none"),
                                     "LOCALAPPDATA": str(self.d / "none")}), \
                patch.object(system.shutil, "which", return_value=None):
            found = system.app_browsers(("chromium", "msedge"))
        self.assertEqual([Path(p).name for p in found], ["msedge.exe"])

    def test_short_name_or_nothing_for_paths_with_spaces(self):
        self.assertEqual(system.space_free_path(r"C:\AnimeHub\mpv\skip-credits.lua"), r"C:\AnimeHub\mpv\skip-credits.lua")
        import ctypes
        for short, want in ((r"C:\Users\MAXMUS~1\skip.lua", r"C:\Users\MAXMUS~1\skip.lua"), ("", None),
                            (r"C:\Users\Max Muster\skip.lua", None)):
            def fake(path, buf, n, short=short):
                buf.value = short
                return len(short)
            k32 = MagicMock(GetShortPathNameW=fake)
            with self.subTest(short=short), patch.object(ctypes, "windll", MagicMock(kernel32=k32), create=True):
                self.assertEqual(system.space_free_path(r"C:\Users\Max Muster\skip.lua"), want)


class MenuHelperTests(unittest.TestCase):
    def test_written_as_bytes_with_lf_line_ends(self):
        """Text mode on Windows would write CRLF, which bash can't run."""
        with tempfile.TemporaryDirectory() as d, patch.object(anicli.config, "DATA", Path(d)):
            raw = anicli.anicli_menu_helper().read_bytes()
        self.assertEqual(raw, anicli.ANICLI_MENU_SH.encode())
        self.assertNotIn(b"\r", raw)


class Utf8OutputTests(unittest.TestCase):
    """Windows writes a piped stdout in cp1252: the banner's "→" crashed the
    hub whenever it didn't run in a console (seen on Windows, TODO 74)."""

    def streams(self):
        return (io.TextIOWrapper(io.BytesIO(), encoding="cp1252"),
                io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

    def test_windows_prints_utf8_to_a_pipe(self):
        out, err = self.streams()
        with patch.object(system, "WINDOWS", True), patch.object(sys, "stdout", out), \
                patch.object(sys, "stderr", err):
            system.utf8_stdio()
            print("Anime Hub → 葬送のフリーレン")
        out.flush()
        self.assertEqual(out.buffer.getvalue().decode().rstrip(), "Anime Hub → 葬送のフリーレン")

    def test_posix_streams_untouched(self):
        out, err = self.streams()
        with patch.object(system, "WINDOWS", False), patch.object(sys, "stdout", out), \
                patch.object(sys, "stderr", err):
            system.utf8_stdio()
        self.assertEqual(out.encoding, "cp1252")


class ServerBindTests(unittest.TestCase):
    """Windows let the hub bind a port another program held (SO_REUSEADDR
    means "share" there), so "cannot bind" never fired (TODO 74)."""

    def test_windows_server_class_does_not_reuse(self):
        with patch.object(system, "WINDOWS", True):
            self.assertFalse(system.http_server_class().allow_reuse_address)
        with patch.object(system, "WINDOWS", False):
            from http.server import ThreadingHTTPServer
            self.assertIs(system.http_server_class(), ThreadingHTTPServer)

    @unittest.skipUnless(system.WINDOWS, "Windows only")
    def test_a_taken_port_fails_the_bind(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        other = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)  # SO_REUSEADDR, like python -m http.server
        self.addCleanup(other.server_close)
        with self.assertRaises(OSError):  # the error server.main words as "cannot bind"
            system.http_server_class()(("127.0.0.1", other.server_address[1]), BaseHTTPRequestHandler)


@unittest.skipIf(system.WINDOWS, "the XDG file is Linux only")
class PicturesDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        (self.home / ".config").mkdir()
        env = patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def dirs(self, text):
        (self.home / ".config" / "user-dirs.dirs").write_text(text, encoding="utf-8")

    def test_the_folder_keeps_its_language(self):
        self.dirs('# comment\nXDG_MUSIC_DIR="$HOME/Musik"\nXDG_PICTURES_DIR="$HOME/Bilder"\n')
        self.assertEqual(system.pictures_dir(), self.home / "Bilder")

    def test_a_full_path_counts(self):
        self.dirs('XDG_PICTURES_DIR="/mnt/data/Pics"\n')
        self.assertEqual(system.pictures_dir(), Path("/mnt/data/Pics"))

    def test_switched_off_or_missing_means_pictures(self):
        pictures = self.home / "Pictures"
        self.assertEqual(system.pictures_dir(), pictures)  # no file at all
        for text in ('XDG_PICTURES_DIR="$HOME/"\n', 'XDG_PICTURES_DIR="${HOME}"\n', 'XDG_MUSIC_DIR="$HOME/Musik"\n'):
            self.dirs(text)
            self.assertEqual(system.pictures_dir(), pictures, text)


class PosixUnchangedTests(unittest.TestCase):
    @unittest.skipIf(system.WINDOWS, "POSIX only")
    def test_posix_keeps_new_session_and_pty(self):
        self.assertEqual(system.detached(), {"start_new_session": True})
        master, slave = system.open_pty()
        os.close(master)
        os.close(slave)


if __name__ == "__main__":
    unittest.main()
