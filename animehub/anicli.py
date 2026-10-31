"""The ani-cli bridge: locating, spawning and probing ani-cli."""

import os
import re
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from . import settings, system
from .config import config


# ---------- ani-cli bridge (v0.2 streaming, TODO 18 second half) ----------
# Streaming and downloads are delegated to the user's own ani-cli: the hub
# contains no provider code and fetches no video itself. It runs the ani-cli
# on PATH, answers its menus through a small helper (ANI_CLI_MENU) instead of
# fzf, and hands mpv the hub's IPC socket via ANI_CLI_PLAYER_FLAGS — so a
# streamed episode gets the same progress tracking, resume, skip-credits and
# autoplay as a file on disk.

# The helper ani-cli calls in place of fzf: `menu "<prompt>"`, choices on
# stdin ("N title" per line). Every list is recorded for the hub; "Select
# anime" is answered by exact title (or by position for links made without
# one); every other prompt is declined, which ends ani-cli instead of
# leaving it waiting on a menu nobody sees.
ANICLI_MENU_SH = r"""#!/bin/sh
# Written by Anime Hub (hub.py) — regenerated on demand, edits are lost.
for arg; do prompt=$arg; done
list=$(cat)
if [ -n "$HUB_ANICLI_CAPTURE" ]; then
    printf '\036%s\n%s\n' "$prompt" "$list" >>"$HUB_ANICLI_CAPTURE"
fi
case "$prompt" in
    "Select anime"*)
        if [ -n "$HUB_ANICLI_TITLE" ]; then
            printf '%s\n' "$list" | awk 'substr($0, length($1) + 2) == ENVIRON["HUB_ANICLI_TITLE"] { print; found = 1; exit } END { exit !found }'
            exit
        elif [ -n "$HUB_ANICLI_INDEX" ]; then
            printf '%s\n' "$list" | awk '$1 == ENVIRON["HUB_ANICLI_INDEX"] { print; found = 1; exit } END { exit !found }'
            exit
        fi
        ;;
esac
exit 1
"""
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# ani-cli's die() prints its message in bold red.
ANICLI_DIE_RE = re.compile(r"\x1b\[1;31m(.*?)\x1b\[0m")
STREAM_EP_RE = re.compile(r"^\d{1,4}(?:\.\d{1,2})?$")
DL_PERCENT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
# The same line's size and time left ("of ~ 412.34MiB … ETA 02:03"): the downloads list shows them.
DL_SIZE_RE = re.compile(r"\bof\s+~?\s*(\d+(?:\.\d+)?)\s*([KMGT])i?B")
DL_ETA_RE = re.compile(r"\bETA\s+(\d+(?::\d+)+)")
STREAM_QUALITIES = ("best", "1080", "720", "480", "360", "worst")
STREAM_MODES = ("sub", "dub")
# Airing shows gain episodes; the show page re-asks ani-cli after this long.
STREAM_EPISODES_TTL = 6 * 3600
ANICLI_PROBE_TIMEOUT = 60
# ani-cli scrapes the episode's sources before mpv even starts, so a stream
# gets far longer than a local file to bring up its IPC socket.
STREAM_STARTUP_TIMEOUT = 90


class AniCliError(Exception):
    """An ani-cli failure, worded for the UI."""


# Older releases can't reach the current provider (distro packages lag), so
# Settings and the tour offer the official script (Decision 47).
MIN_VERSION = (5, 1)
ANICLI_URL = "https://github.com/pystardust/ani-cli/releases/latest/download/ani-cli"
ANICLI_MAX = 2_000_000
ANICLI_TOOLS = ("curl", "sed", "grep")  # what it needs with the hub answering its menus


def own_anicli():
    return config.DATA / "bin" / "ani-cli"


def anicli_path():
    """The copy Get ani-cli fetched, else the installed one."""
    if not system.WINDOWS and own_anicli().is_file():
        return str(own_anicli())
    return system.find_script("ani-cli")


def anicli_outdated():
    m = re.search(r"(\d+)\.(\d+)", anicli_version() or "")
    return bool(m) and (int(m[1]), int(m[2])) < MIN_VERSION


def anicli_status():
    """Additive in /api/settings: stream.own / outdated / can_get / needs."""
    return {"own": anicli_path() == str(own_anicli()), "outdated": anicli_outdated(), "can_get": not system.WINDOWS,
            "needs": [t for t in ANICLI_TOOLS if not system.find_program(t)]}


def fetch_anicli():
    """POST /api/anicli-get: the official script into the data folder, only
    on the user's click. Refused unless it reads as an ani-cli the bridge
    can drive; a failure leaves what was there."""
    if system.WINDOWS:
        return {"error": "On Windows, update ani-cli with Scoop."}
    try:
        req = Request(ANICLI_URL, headers={"User-Agent": "anime-hub/1.0"})
        with urlopen(req, timeout=20) as r:
            raw = r.read(ANICLI_MAX + 1)
    except OSError:
        return {"error": "Could not reach GitHub."}
    text = raw.decode(errors="replace")
    if len(raw) > ANICLI_MAX or not raw.startswith(b"#!") or "version_number=" not in text \
            or any(not re.search(pat, text) for pat, _ in ANICLI_NEEDS):
        return {"error": "That download didn't look like ani-cli, so nothing changed."}
    path = own_anicli()
    if path.is_file() and path.read_bytes() == raw:
        return {"ok": True, "version": anicli_version(), "changed": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(raw)
    tmp.chmod(0o755)
    os.replace(tmp, path)
    _anicli_versions.pop(str(path), None)
    return {"ok": True, "version": anicli_version(), "changed": True}


_anicli_versions = {}


def anicli_version():
    exe = anicli_path()
    if not exe:
        return None
    if exe not in _anicli_versions:
        try:
            out = subprocess.run([*system.script_argv(exe), "-V"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=10, **system.quiet()).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        _anicli_versions[exe] = out.splitlines()[-1] if out else None
    return _anicli_versions[exe]


def stream_prefs():
    q, m = settings.get_setting("stream_quality"), settings.get_setting("stream_mode")
    return {"quality": q if q in STREAM_QUALITIES else "best",
            "mode": m if m in STREAM_MODES else "sub"}


def anicli_menu_helper():
    path = config.DATA / "anicli-menu.sh"
    # Bytes, not text: on Windows text mode would write CRLF line ends,
    # which bash (Git Bash runs ani-cli there) can't run.
    script = ANICLI_MENU_SH.encode()
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current != script:
        config.DATA.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(script)
        tmp.chmod(0o755)
        os.replace(tmp, path)
    return path


def anicli_env(link=None, capture=None, **extra):
    """The environment every ani-cli run gets: the menu helper, the user's
    stream preferences, and the link's pick (exact title, else position)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_ANICLI_")}
    prefs = stream_prefs()
    env.update(system.script_env())
    env.update(ANI_CLI_MENU=system.shell_path(anicli_menu_helper()), TERM="dumb",
               ANI_CLI_QUALITY=prefs["quality"],
               ANI_CLI_MODE=(link or {}).get("mode") or prefs["mode"])
    if capture:
        env["HUB_ANICLI_CAPTURE"] = system.shell_path(capture)
    if link:
        if link.get("title"):
            env["HUB_ANICLI_TITLE"] = link["title"]
        else:
            env["HUB_ANICLI_INDEX"] = str(link.get("idx") or 1)
    env.update({k: str(v) for k, v in extra.items()})
    return env


# What the bridge needs from ani-cli (TODO 18): its settings and the two
# menu prompts the helper answers. ani-cli is a shell script, so the check
# reads its text instead of trusting a version number — a release that
# renames a setting fails here, not as an empty search or an untracked mpv.
ANICLI_NEEDS = (
    (r"\bANI_CLI_MENU\b", "ANI_CLI_MENU (menu helper)"),
    (r"\bANI_CLI_PLAYER_FLAGS\b", "ANI_CLI_PLAYER_FLAGS (the hub's mpv flags)"),
    (r"\bANI_CLI_PLAYER\b", "ANI_CLI_PLAYER (player choice)"),
    (r"\bANI_CLI_MODE\b", "ANI_CLI_MODE (sub/dub)"),
    (r"\bANI_CLI_QUALITY\b", "ANI_CLI_QUALITY (quality)"),
    (r"Select anime", "the “Select anime” menu"),
    (r"Select episode", "the “Select episode” menu"),
)
_anicli_checks = {}


def anicli_check():
    """{"ok": True/False, "missing": [...]} for the ani-cli on PATH; ok is
    None when it can't be read as a script (no verdict). None without ani-cli."""
    exe = anicli_path()
    if not exe:
        return None
    try:
        st = os.stat(exe)
        key = (exe, st.st_mtime_ns, st.st_size)
    except OSError:
        return {"ok": None, "missing": []}
    if key not in _anicli_checks:
        try:
            raw = Path(exe).read_bytes()[:4_000_000]
        except OSError:
            raw = b""
        if not raw.startswith(b"#!"):
            result = {"ok": None, "missing": []}
        else:
            text = raw.decode(errors="replace")
            missing = [label for pat, label in ANICLI_NEEDS if not re.search(pat, text)]
            result = {"ok": not missing, "missing": missing}
        _anicli_checks[key] = result
    return _anicli_checks[key]


def anicli_unsupported_message(check):
    return ("This ani-cli doesn't support what Anime Hub needs (" + ", ".join(check["missing"])
            + ") — update ani-cli.")


def anicli_spawn(args, env, stdout, stderr):
    """Start ani-cli in its own process group with a pty as stdin: without a
    terminal on stdin, ani-cli swaps ANI_CLI_MENU for rofi/dmenu whenever
    either is installed. The pty stays open until anicli_release. Windows
    has no pty (and no rofi/dmenu): stdin is empty there."""
    exe = anicli_path()
    if not exe:
        raise AniCliError("ani-cli was not found — install ani-cli and make sure it is on PATH.")
    check = anicli_check()
    if check and check["ok"] is False:
        raise AniCliError(anicli_unsupported_message(check))
    try:
        argv = [*system.script_argv(exe), *args]
    except FileNotFoundError as e:
        raise AniCliError(str(e))
    master, slave = system.open_pty()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL if slave is None else slave,
                                stdout=stdout, stderr=stderr, env=env, **system.detached())
    except OSError as e:
        if master is not None:
            os.close(master)
        raise AniCliError(f"ani-cli could not be started: {e}")
    finally:
        if slave is not None:
            os.close(slave)
    proc.hub_pty = master
    return proc


def anicli_release(proc):
    fd = getattr(proc, "hub_pty", None)
    if fd is not None:
        proc.hub_pty = None
        try:
            os.close(fd)
        except OSError:
            pass


def stop_process_group(proc, sig=signal.SIGTERM):
    """Signal a session's whole process group (start_new_session: pgid ==
    pid) — an ani-cli session is a shell whose mpv / yt-dlp child would
    otherwise outlive it. Only a real child's group: os.killpg(1) is
    kill(-1), which signals every process the user owns (logs them out),
    and a test's MagicMock pid reads as 1. Windows has no process groups
    to signal: taskkill ends the process tree instead."""
    pid = proc.pid
    try:
        if type(pid) is not int or pid <= 1 or (not system.WINDOWS and pid == os.getpgrp()):
            raise OSError("not a child process group")
        if system.WINDOWS:
            system.kill_tree(pid)
        else:
            os.killpg(pid, sig)
    except OSError:
        try:
            proc.send_signal(sig)
        except OSError:
            pass


def anicli_message(text):
    """ani-cli's own last error (die prints it in red), else the last line."""
    found = ANICLI_DIE_RE.findall(text or "")
    if found:
        return found[-1].strip()
    lines = [line.strip() for line in ANSI_RE.sub("", text or "").replace("\r", "\n").splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


def anicli_probe(args, link=None, timeout=ANICLI_PROBE_TIMEOUT):
    """Run ani-cli once for the lists it offers — never for playback: the
    debug player only prints links, so even a show whose single result or
    single episode ani-cli picks by itself starts nothing.
    Returns (exit code, stderr, [(prompt, lines)])."""
    fd, capture = tempfile.mkstemp(prefix="anime-hub-anicli-", suffix=".txt")
    os.close(fd)
    try:
        proc = anicli_spawn(args, anicli_env(link, capture=capture, ANI_CLI_PLAYER="debug"),
                            subprocess.DEVNULL, subprocess.PIPE)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process_group(proc, system.SIGKILL)
            proc.communicate()
            raise AniCliError(f"ani-cli did not answer within {timeout} s — is the site reachable?")
        finally:
            anicli_release(proc)
        text = Path(capture).read_text(encoding="utf-8", errors="replace")
    finally:
        Path(capture).unlink(missing_ok=True)
    lists = []
    for chunk in text.split("\x1e")[1:]:
        prompt, _, body = chunk.partition("\n")
        lists.append((prompt.strip(), [line for line in body.splitlines() if line.strip()]))
    return proc.returncode, err.decode(errors="replace"), lists


def _anicli_failed(rc, err, link=None):
    msg = anicli_message(err)
    if link and "Invalid anime selection" in msg:
        return AniCliError(f"ani-cli no longer finds “{link.get('title') or link['query']}” when searching "
                           f"“{link['query']}” — choose the source again.")
    return AniCliError(f"ani-cli: {msg}" if msg else f"ani-cli failed (exit code {rc}).")


def anicli_search(query):
    """ani-cli's search results for a query: [{index, title}]. A single hit
    is taken by ani-cli without asking, so its title stays unknown (None)."""
    query = " ".join(query.split()).lstrip("-").strip()
    if not query:
        raise AniCliError("Enter a title to search for.")
    rc, err, lists = anicli_probe([query])
    for prompt, lines in lists:
        if prompt.startswith("Select anime"):
            results = []
            for line in lines:
                n, _, title = line.partition(" ")
                if n.isdigit() and title:
                    results.append({"index": int(n), "title": title})
            return {"query": query, "results": results}
    if lists or rc == 0:
        return {"query": query, "results": [{"index": 1, "title": None}]}
    if "No results" in anicli_message(err):
        return {"query": query, "results": []}
    raise _anicli_failed(rc, err)


def anicli_episodes(link):
    """The episode list ani-cli offers for a link, as it spells the numbers."""
    rc, err, lists = anicli_probe([link["query"]], link)
    for prompt, lines in lists:
        if prompt.startswith("Select episode"):
            eps = [line.strip() for line in lines if STREAM_EP_RE.match(line.strip())]
            if eps:
                return eps
    if rc == 0:
        # One episode (movies): ani-cli picks it without asking.
        return ["1"]
    raise _anicli_failed(rc, err, link)
