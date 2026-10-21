"""The ani-cli bridge: locating, spawning and probing ani-cli."""

import os
import pty
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import settings
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


def anicli_path():
    return shutil.which("ani-cli")


_anicli_versions = {}


def anicli_version():
    exe = anicli_path()
    if not exe:
        return None
    if exe not in _anicli_versions:
        try:
            out = subprocess.run([exe, "-V"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=10).stdout.strip()
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
    try:
        current = path.read_text()
    except OSError:
        current = None
    if current != ANICLI_MENU_SH:
        config.DATA.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(ANICLI_MENU_SH)
        tmp.chmod(0o755)
        os.replace(tmp, path)
    return path


def anicli_env(link=None, capture=None, **extra):
    """The environment every ani-cli run gets: the menu helper, the user's
    stream preferences, and the link's pick (exact title, else position)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_ANICLI_")}
    prefs = stream_prefs()
    env.update(ANI_CLI_MENU=str(anicli_menu_helper()), TERM="dumb",
               ANI_CLI_QUALITY=prefs["quality"],
               ANI_CLI_MODE=(link or {}).get("mode") or prefs["mode"])
    if capture:
        env["HUB_ANICLI_CAPTURE"] = str(capture)
    if link:
        if link.get("title"):
            env["HUB_ANICLI_TITLE"] = link["title"]
        else:
            env["HUB_ANICLI_INDEX"] = str(link.get("idx") or 1)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def anicli_spawn(args, env, stdout, stderr):
    """Start ani-cli in its own process group with a pty as stdin: without a
    terminal on stdin, ani-cli swaps ANI_CLI_MENU for rofi/dmenu whenever
    either is installed. The pty stays open until anicli_release."""
    exe = anicli_path()
    if not exe:
        raise AniCliError("ani-cli was not found — install ani-cli and make sure it is on PATH.")
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen([exe, *args], stdin=slave, stdout=stdout, stderr=stderr,
                                env=env, start_new_session=True)
    except OSError as e:
        os.close(master)
        raise AniCliError(f"ani-cli could not be started: {e}")
    finally:
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
    and a test's MagicMock pid reads as 1."""
    pid = proc.pid
    try:
        if type(pid) is not int or pid <= 1 or pid == os.getpgrp():
            raise OSError("not a child process group")
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
            stop_process_group(proc, signal.SIGKILL)
            proc.communicate()
            raise AniCliError(f"ani-cli did not answer within {timeout} s — is the site reachable?")
        finally:
            anicli_release(proc)
        text = Path(capture).read_text(errors="replace")
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
