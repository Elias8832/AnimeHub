"""Operating-system differences in one place (TODO 74, Decision 40: the
Windows port). The rest of the hub asks these helpers instead of checking
sys.platform itself; on Linux every helper does what the code did before."""

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

WINDOWS = sys.platform == "win32"

# Windows has no SIGKILL; Popen.send_signal(SIGTERM) is TerminateProcess
# there, which already is the hard stop.
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


# Windows: a console program started by a hub without a console (the
# desktop app) would flash a black console window — CREATE_NO_WINDOW.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def detached():
    """Popen keyword arguments for a child in its own process group: a new
    session on POSIX (stop_process_group signals the whole group); on
    Windows a new process group, so a Ctrl+C in the hub's console doesn't
    reach it, and no console window."""
    if WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200) | _NO_WINDOW}
    return {"start_new_session": True}


def quiet():
    """subprocess.run keyword arguments for a short helper run (a version
    check, taskkill): no console window on Windows, nothing on POSIX."""
    return {"creationflags": _NO_WINDOW} if WINDOWS else {}


def http_server_class():
    """The hub's ThreadingHTTPServer. Windows: without SO_REUSEADDR, which
    there lets a second program bind a port already in use — a second hub
    (or anything else on the port) "started" and shared its requests instead
    of failing with "cannot bind" as on Linux (found on Windows, TODO 74)."""
    from http.server import ThreadingHTTPServer
    if not WINDOWS:
        return ThreadingHTTPServer

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False

    return Server


def utf8_stdio():
    """Windows: print UTF-8 to a pipe or file too. Python writes those in the
    ANSI code page (cp1252) there, so the startup banner's "→" or a
    Japanese library path crashed the hub whenever its output was not a
    console (the desktop app). A console already gets Unicode; POSIX is
    left as it is."""
    if not WINDOWS:
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def open_pty():
    """(master, slave) fds of a new pty, or (None, None) where there is no
    pty module (Windows)."""
    if WINDOWS:
        return None, None
    import pty
    return pty.openpty()


def kill_tree(pid):
    """Windows only: end a process and every process it started (an
    ani-cli session is bash with mpv or yt-dlp below it). Raises OSError
    when taskkill fails, so callers can fall back to the process itself."""
    try:
        rc = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10, **quiet()).returncode
    except subprocess.SubprocessError as e:
        raise OSError(f"taskkill failed: {e}")
    if rc != 0:
        raise OSError(f"taskkill exited {rc}")


# ---------- mpv's IPC server (TODO 74 item 3) ----------
# POSIX: a Unix socket file. Windows: a named pipe (\\.\pipe\<name>) — no
# file on disk, and a new client connection per open().
PIPE_PREFIX = "\\\\.\\pipe\\"


def ipc_address(name):
    """Where mpv's --input-ipc-server listens for session `name`: a socket
    in the user's private runtime dir (TODO 52h; /tmp only when
    $XDG_RUNTIME_DIR is unset or missing), a named pipe on Windows."""
    if WINDOWS:
        return Path(PIPE_PREFIX + name)
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    base = Path(runtime) if runtime and Path(runtime).is_dir() else Path("/tmp")
    return base / f"{name}.sock"


def ipc_ready(address):
    """Whether mpv may be listening yet: the socket file exists. A named
    pipe has no file to look at — connecting is the only test there."""
    return True if WINDOWS else address.exists()


def ipc_marker(address):
    """The skip-credits lua's "finished by the hotkey" marker file: beside
    the socket on POSIX; a pipe can't have a file beside it, so on Windows
    it sits in the temp dir under the pipe's name."""
    if WINDOWS:
        name = str(address)
        name = name[len(PIPE_PREFIX):] if name.startswith(PIPE_PREFIX) else Path(name).name
        return Path(tempfile.gettempdir()) / (name + ".skip")
    return Path(str(address) + ".skip")


def ipc_remove(address):
    """Clean up after a session: the socket file (a pipe vanishes with mpv)."""
    if not WINDOWS:
        address.unlink(missing_ok=True)


def ipc_exchange(address, payload, timeout):
    """Send one request line to mpv and return the bytes it answered, up to
    the first newline. Every failure is an OSError (missing socket/pipe,
    refused, timed out), the one error the watcher treats as "not there"."""
    if WINDOWS:
        return _pipe_exchange(str(address), payload, timeout)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(address))
        s.sendall(payload)
        return _read_line(s.recv)
    finally:
        s.close()


def _read_line(read):
    buf = b""
    while b"\n" not in buf:
        chunk = read(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _pipe_exchange(path, payload, timeout):
    """A pipe read has no timeout, so the exchange runs on its own thread,
    which also owns (and closes) the handle: if mpv never answers, the
    caller gets TimeoutError and the stuck thread ends with mpv."""
    f = open(path, "r+b", buffering=0)  # FileNotFoundError until mpv made the pipe
    result = {}

    def run():
        try:
            f.write(payload)
            result["buf"] = _read_line(f.read)
        except OSError as e:
            result["err"] = e
        finally:
            f.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"mpv did not answer on {path} within {timeout} s")
    if "err" in result:
        raise result["err"]
    return result["buf"]


# ---------- the desktop around the hub (TODO 74 items 7, 9, 10, 12) ----------

def open_external(target):
    """Open a folder in the file manager or a URL in the default browser."""
    if WINDOWS:
        os.startfile(target)
        return
    subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **detached())


# Where Chromium-family browsers install on Windows (they're rarely on
# PATH there), in the same preference order as config.APP_BROWSERS; Edge
# comes with Windows, so an app window is almost always possible.
_WINDOWS_BROWSERS = (
    ("LOCALAPPDATA", "Chromium", "Application", "chrome.exe"),
    ("PROGRAMFILES", "Google", "Chrome", "Application", "chrome.exe"),
    ("PROGRAMFILES(X86)", "Google", "Chrome", "Application", "chrome.exe"),
    ("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
    ("PROGRAMFILES", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    ("PROGRAMFILES(X86)", "Microsoft", "Edge", "Application", "msedge.exe"),
    ("PROGRAMFILES", "Microsoft", "Edge", "Application", "msedge.exe"),
)


def app_browsers(names):
    """Chromium-family browsers that can open an app-mode window, best
    first: the given names on PATH, then (Windows) the usual install dirs."""
    found = [exe for exe in map(shutil.which, names) if exe]
    if WINDOWS:
        for var, *rel in _WINDOWS_BROWSERS:
            exe = os.path.join(os.environ.get(var) or "", *rel)
            if os.path.isabs(exe) and os.path.isfile(exe):
                found.append(exe)
    return list(dict.fromkeys(found))


def browse_places():
    """The folder picker's shortcuts (Decision 32): Home plus where other
    disks show up — /mnt, /media, /run/media/$USER on Linux, the drive
    letters on Windows. Only folders that exist are offered."""
    home = Path.home()
    if WINDOWS:
        places = [("Home", home)] + [(f"{d}:", Path(f"{d}:\\")) for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    else:
        user = os.environ.get("USER") or home.name
        places = [("Home", home), ("/mnt", Path("/mnt")), ("/media", Path("/media")),
                  (f"/run/media/{user}", Path("/run/media") / user)]
    return [(label, p) for label, p in places if p.is_dir()]


def space_free_path(path):
    """`path` spelled without whitespace or glob characters, for flags that
    get split on whitespace (ANI_CLI_PLAYER_FLAGS), or None. POSIX: a
    symlink in the private runtime dir. Windows: the 8.3 short name
    (symlinks need admin rights there); None when the disk has no short
    names."""
    bad = re.compile(r"[\s*?\[]")
    path = str(path)
    if not bad.search(path):
        return path
    if WINDOWS:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        short = buf.value if 0 < n < len(buf) else ""
        return short if short and not bad.search(short) else None
    run = os.environ.get("XDG_RUNTIME_DIR")
    link = Path(run or "") / ("anime-hub-" + Path(path).name)
    if not run or bad.search(str(link)):
        return None
    try:
        if not (link.is_symlink() and os.readlink(link) == path):
            link.unlink(missing_ok=True)
            link.symlink_to(path)
    except OSError:
        return None
    return str(link)


# ---------- shell scripts: ani-cli (TODO 74 items 4 and 6) ----------
# ani-cli is a POSIX shell script. Windows runs it in Git for Windows' bash
# (Scoop's ani-cli does the same); WSL's C:\Windows\System32\bash.exe would
# run it inside Linux, away from the Windows mpv, so it is never used.

def find_script(name):
    """A program on PATH by its bare name. Windows: a shell script has no
    .exe/.cmd extension, which shutil.which doesn't look for there, so
    PATH and Scoop's app folder are searched for the bare name first; a
    shim (ani-cli.cmd / .exe) is the fallback. Scoop's own sh shim
    (<scoop>\\shims\\ani-cli) is followed to the script it starts, which is
    the file the hub reads for its feature check."""
    if not WINDOWS:
        return shutil.which(name)
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    dirs.append(str(Path.home() / "scoop" / "apps" / name / "current"))
    for d in dirs:
        p = os.path.join(d, name)
        if d and os.path.isfile(p):
            real = Path(d).parent / "apps" / name / "current" / name
            if Path(d).name.lower() == "shims" and real.is_file():
                return str(real)
            return p
    return shutil.which(name)


def find_program(name):
    """A program on PATH. Windows: the .exe first — PATHEXT puts .COM ahead,
    and mpv ships an mpv.com console stub beside mpv.exe, which Settings
    then showed (playback itself always started mpv.exe)."""
    if WINDOWS:
        return shutil.which(name + ".exe") or shutil.which(name)
    return shutil.which(name)


def git_bash():
    """Git for Windows' bash.exe, or None."""
    cands = []
    git = shutil.which("git")
    if git:
        cands.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")  # <Git>\cmd\git.exe
    for var, *rel in (("PROGRAMFILES", "Git"), ("PROGRAMW6432", "Git"), ("LOCALAPPDATA", "Programs", "Git")):
        if os.environ.get(var):
            cands.append(Path(os.environ[var], *rel, "bin", "bash.exe"))
    cands.append(Path.home() / "scoop" / "apps" / "git" / "current" / "bin" / "bash.exe")
    return next((str(c) for c in cands if c.is_file()), None)


def script_argv(exe):
    """How to start `exe`: by itself, or on Windows a shell script through
    Git for Windows' bash (FileNotFoundError, worded for the user, when
    there is none)."""
    if not WINDOWS or os.path.splitext(exe)[1].lower() in (".exe", ".cmd", ".bat", ".com"):
        return [exe]
    bash = git_bash()
    if not bash:
        raise FileNotFoundError("Git for Windows was not found — ani-cli runs in its bash. "
                                "Install Git for Windows, then try again.")
    return [bash, exe]


def shell_path(path):
    """A path as a shell script gets it: unchanged on POSIX; with forward
    slashes on Windows (C:/Users/…), which Git Bash and Windows programs
    both read."""
    return str(path).replace("\\", "/") if WINDOWS else str(path)


def script_env():
    """Extra environment for a shell script run. Windows: Git Bash rewrites
    arguments that look like POSIX paths before a Windows program (mpv)
    gets them; the hub's mpv flags are Windows paths already and must
    reach mpv untouched."""
    return {"MSYS2_ARG_CONV_EXCL": "--input-ipc-server=;--script="} if WINDOWS else {}


# ---------- network adapters: the VPN check (TODO 74 item 8) ----------

def windows_adapters():
    """Windows only: [(friendly name, description, up)] for every network
    adapter, from GetAdaptersAddresses (iphlpapi) — no PowerShell, which
    takes about a second per call. Raises OSError when the call fails."""
    import ctypes
    from ctypes import wintypes

    class Adapter(ctypes.Structure):
        pass

    # The leading fields of IP_ADAPTER_ADDRESSES — the struct only grows at
    # the end, so reading this prefix through the pointer is safe.
    Adapter._fields_ = [
        ("Length", wintypes.ULONG), ("IfIndex", wintypes.DWORD),
        ("Next", ctypes.POINTER(Adapter)), ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.c_void_p), ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p), ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p), ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p), ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", wintypes.ULONG), ("Flags", wintypes.ULONG),
        ("Mtu", wintypes.ULONG), ("IfType", wintypes.ULONG), ("OperStatus", ctypes.c_int),
    ]
    skip = 0x2 | 0x4 | 0x8  # GAA_FLAG_SKIP_ANYCAST | _MULTICAST | _DNS_SERVER
    size = wintypes.ULONG(16384)
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        rc = ctypes.windll.iphlpapi.GetAdaptersAddresses(0, skip, None, buf, ctypes.byref(size))
        if rc != 111:  # ERROR_BUFFER_OVERFLOW: size now holds what it needs
            break
    if rc != 0:
        raise OSError(f"GetAdaptersAddresses failed ({rc})")
    out = []
    p = ctypes.cast(buf, ctypes.POINTER(Adapter))
    while p:
        a = p.contents
        out.append((a.FriendlyName or "", a.Description or "", a.OperStatus == 1))  # IfOperStatusUp
        p = a.Next
    return out
