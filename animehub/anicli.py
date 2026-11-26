"""The ani-cli bridge: locating, spawning and probing ani-cli."""

import json
import os
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

from . import VERSION, android, settings, system, util
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

    def __init__(self, msg, needs_upgrade=False):
        super().__init__(msg)
        self.needs_upgrade = bool(needs_upgrade)


def error_dict(exc_or_msg, text=None, needs_upgrade=False):
    """Error payload for HTTP routes; adds anicli_update when the fix is
    upgrading ani-cli so the UI can open the upgrade dialog directly."""
    flag = bool(needs_upgrade or getattr(exc_or_msg, "needs_upgrade", False))
    msg = text if text is not None else str(exc_or_msg)
    return {"error": msg, "anicli_update": True} if flag else {"error": msg}


# Older releases can't reach the current provider (distro packages lag), so
# Settings and the tour offer the official script (Decision 47).
MIN_VERSION = (5, 2)
ANICLI_URL = "https://github.com/pystardust/ani-cli/releases/latest/download/ani-cli"
ANICLI_RELEASES_API = "https://api.github.com/repos/pystardust/ani-cli/releases/latest"
ANICLI_MAX = 2_000_000
ANICLI_API_MAX = 262_144
ANICLI_UPDATE_EVERY = 24 * 3600
ANICLI_TOOLS = ("curl", "sed", "grep")  # what it needs with the hub answering its menus
ANICLI_NO_VPN = "No VPN is on. Turn one on, or switch off Only with a VPN."
_upgrade_lock = threading.Lock()
_upgrade_job = {"state": "idle", "version": None, "changed": None, "error": None}


def parse_version(text):
    """Numeric (major, minor, patch) from a version or tag string, or None."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def _get_setting(key):
    try:
        return settings.get_setting(key)
    except sqlite3.OperationalError:
        return None


def _set_setting(key, value):
    try:
        settings.set_setting(key, value)
    except sqlite3.OperationalError:
        pass


def _vpn_missing():
    try:
        return settings.automation_enabled("update_vpn_only") and not settings.vpn_status().get("active")
    except sqlite3.OperationalError:
        return False


def own_anicli():
    return config.DATA / "bin" / "ani-cli"


def anicli_path():
    """The copy Get ani-cli fetched, else the installed one. Android: the
    copy of Termux's script (Decision 88); ani-cli itself runs in Termux."""
    if system.ANDROID:
        # No copy yet (first start, Termux set up later): ask Termux now. An
        # answer comes in about a second; a refusal at once; at most every 20 s.
        return str(android.anicli_copy()) if android.anicli_copy().is_file() or android.fetch_anicli() else None
    if not system.WINDOWS and own_anicli().is_file():
        return str(own_anicli())
    return system.find_script("ani-cli")


def anicli_outdated():
    v = parse_version(anicli_version())
    return bool(v) and v[:len(MIN_VERSION)] < MIN_VERSION


def anicli_too_old_message():
    how = ("update it with Scoop: scoop update ani-cli" if system.WINDOWS
           else "update it in Termux: pkg upgrade -y" if system.ANDROID
           else "update ani-cli in Settings → Online episodes")
    return (f"ani-cli {anicli_version()} is too old — Anime Hub needs {'.'.join(map(str, MIN_VERSION))} or newer; "
            f"{how}.")


def anicli_curl_needs():
    """["curl-impersonate"] when this ani-cli asks for it and none is installed.
    Since 5.1 the provider sits behind Cloudflare, which turns plain curl away
    ("Blocked by cloudflare. Try installing curl-impersonate"); the script
    names the builds it tries, so the list comes from its own text."""
    check = anicli_check() or {}
    curls = check.get("curls") or []
    if system.WINDOWS or system.ANDROID or not curls or any(system.find_program(c) for c in curls):
        return []
    return ["curl-impersonate"]


def upgrade_method():
    """How one-click upgrade works on this platform, or None for manual."""
    if system.WINDOWS:
        return "scoop" if system.powershell() and system.scoop_script() else None
    if system.ANDROID:
        return "termux" if anicli_path() or not android.problem() else None
    return "download"


def upgrade_cmd():
    """Manual fallback upgrade command for this OS (shown when needed)."""
    if system.WINDOWS:
        return "scoop update ani-cli"
    if system.ANDROID:
        return "pkg upgrade -y && pkg install -y ani-cli"
    return None


def update_stored():
    """Newer upstream ani-cli release found by the last check, while still
    newer than the installed copy (clears automatically after upgrading)."""
    if not anicli_path():
        return None
    try:
        found = json.loads(_get_setting("anicli_update_found") or "null")
    except ValueError:
        return None
    if not isinstance(found, dict) or not found.get("version"):
        return None
    want = parse_version(found["version"])
    cur = parse_version(anicli_version())
    if not want:
        return None
    if cur and want <= cur:
        return None
    return found


def update_due(now=None):
    """True when consented daily checks are on, ani-cli is installed, and a
    day has passed since the last check (respects update_vpn_only)."""
    try:
        on = settings.automation_enabled("update_check")
    except sqlite3.OperationalError:
        return False
    if not anicli_path() or not on or _vpn_missing():
        return False
    try:
        last = int(_get_setting("anicli_update_checked") or 0)
    except ValueError:
        last = 0
    return (now if now is not None else time.time()) - last >= ANICLI_UPDATE_EVERY


def check_update():
    """POST /api/anicli-check, and the daily loop: ask GitHub's latest
    pystardust/ani-cli release and remember it when newer than installed."""
    if _vpn_missing():
        return {"error": ANICLI_NO_VPN}
    if not anicli_path():
        return {"ok": True, "found": None, "current": None, "latest": None}
    try:
        req = Request(ANICLI_RELEASES_API,
                      headers={"User-Agent": f"anime-hub/{VERSION}", "Accept": "application/vnd.github+json"})
        with urlopen(req, timeout=15) as r:
            data = json.loads(r.read(ANICLI_API_MAX + 1))
    except (OSError, HTTPException, ValueError):
        return {"error": "Could not reach GitHub."}
    if not isinstance(data, dict):
        return {"error": "GitHub sent something unexpected."}
    tag = str(data.get("tag_name") or "").strip()
    latest = tag.lstrip("v")
    want = parse_version(latest)
    if not want:
        return {"error": "GitHub sent something unexpected."}
    current = anicli_version()
    cur = parse_version(current)
    found = None
    if (cur and want > cur) or (not cur and anicli_outdated()):
        found = {"version": latest, "url": str(data.get("html_url") or ""), "published": str(data.get("published_at") or "")}
    _set_setting("anicli_update_checked", str(int(time.time())))
    _set_setting("anicli_update_found", json.dumps(found))
    return {"ok": True, "found": found, "current": current, "latest": latest}


def upgrade_job():
    with _upgrade_lock:
        return dict(_upgrade_job)


def upgrade_start():
    """POST /api/anicli-upgrade: one-click upgrade on Linux (download),
    Windows (Scoop), or Android (Termux), only on the user's click."""
    method = upgrade_method()
    if not method:
        how = upgrade_cmd()
        return {"error": f"Update ani-cli by hand: {how}" if how else "This copy can't update ani-cli automatically."}
    if _vpn_missing():
        return {"error": ANICLI_NO_VPN}
    with _upgrade_lock:
        if _upgrade_job["state"] == "working":
            return {"ok": True, "method": method}
        _upgrade_job.update(state="working", version=None, changed=None, error=None)
    threading.Thread(target=_upgrade_work, args=(method,), daemon=True).start()
    return {"ok": True, "method": method}


def _clear_stream_backoffs():
    """Let shows that failed under an older ani-cli resolve immediately now."""
    try:
        from . import pages, streaming
        streaming._resolve_failed.clear()
        pages._freshen_failed.clear()
    except Exception:
        pass


def _upgrade_work(method):
    before = anicli_version()
    error = None
    changed = None
    version = None
    try:
        if method == "download":
            r = fetch_anicli()
            error = r.get("error")
            version = r.get("version")
            changed = r.get("changed")
        elif method == "scoop":
            r = _upgrade_scoop(before)
            error = r.get("error")
            version = r.get("version")
            changed = r.get("changed")
        elif method == "termux":
            r = android.upgrade_anicli()
            error = r.get("error")
            if not error:
                _anicli_checks.clear()
                version = anicli_version()
                changed = (version != before) if (version and before) else True
    except Exception as e:
        util.log("ani-cli upgrade: %r" % e)
        error = "Could not update ani-cli."
    if not error:
        if anicli_outdated() or (anicli_check() or {}).get("ok") is False:
            error = (f"Updated, but ani-cli is still {version or 'too old'}. "
                     + (f"Try running {upgrade_cmd()} yourself." if upgrade_cmd() else "Try again later."))
        elif not update_stored():
            _set_setting("anicli_update_found", "null")
        if not error:
            _clear_stream_backoffs()
    with _upgrade_lock:
        _upgrade_job.update(state="failed" if error else "done", version=version, changed=changed, error=error)


def _upgrade_scoop(before):
    """Windows (Decision 87): run `scoop update` + `scoop update ani-cli`
    through PowerShell, then refresh PATH and version caches."""
    ps, scoop = system.powershell(), system.scoop_script()
    if not ps or not scoop:
        return {"error": "Scoop was not found — update ani-cli with Scoop: scoop update ani-cli."}
    shell = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", scoop]
    log = config.DATA / "tools-install.log"
    lines = []
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    rc = 0
    for args in (["update"], ["update", "ani-cli"]):
        try:
            proc = subprocess.run(shell + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                                  timeout=180, **system.quiet())
            rc = proc.returncode
            out = proc.stdout or ""
            try:
                with open(log, "a", encoding="utf-8", errors="replace") as f:
                    f.write(f"\n> scoop {' '.join(args)}\n{out}")
            except OSError:
                pass
            for line in out.splitlines():
                line = line.strip()
                if line:
                    lines = (lines + [line])[-12:]
        except (OSError, subprocess.SubprocessError) as e:
            return {"error": f"Scoop could not run: {e}"}
    system.refresh_path()
    _anicli_versions.clear()
    _anicli_checks.clear()
    after = anicli_version()
    if rc != 0 and (not after or after == before):
        tail = " ".join(lines[-2:])[:300]
        return {"error": f"Scoop could not update ani-cli.{(' Last output: ' + tail) if tail else ''}"}
    return {"ok": True, "version": after, "changed": bool(after and after != before)}


def anicli_status():
    """Additive in /api/settings: stream.own / outdated / can_get / needs /
    min_version / update / upgrade_method / upgrade_cmd / upgrade_job."""
    return {"own": anicli_path() == str(own_anicli()), "outdated": anicli_outdated(), "can_get": not system.WINDOWS and not system.ANDROID,
            "needs": [] if system.ANDROID else system.script_needs(ANICLI_TOOLS) + anicli_curl_needs(),
            "min_version": ".".join(map(str, MIN_VERSION)),
            "update": update_stored(),
            "upgrade_method": upgrade_method(),
            "upgrade_cmd": upgrade_cmd(),
            "upgrade_job": upgrade_job()}


def fetch_anicli():
    """POST /api/anicli-get: the official script into the data folder, only
    on the user's click. Refused unless it reads as an ani-cli the bridge
    can drive; a failure leaves what was there."""
    if system.WINDOWS:
        return {"error": "On Windows, update ani-cli with Scoop."}
    if system.ANDROID:
        return {"error": "On Android, update ani-cli in Termux: pkg upgrade -y"}
    if _vpn_missing():
        return {"error": ANICLI_NO_VPN}
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
    _clear_version_cache(str(path))
    _anicli_checks.clear()
    _clear_stream_backoffs()
    if not update_stored():
        _set_setting("anicli_update_found", "null")
    return {"ok": True, "version": anicli_version(), "changed": True}


_anicli_versions = {}


def _stat_key(exe):
    try:
        st = os.stat(exe)
        return (exe, st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _clear_version_cache(exe):
    for k in [k for k in _anicli_versions if k == exe or (isinstance(k, tuple) and k[0] == exe)]:
        _anicli_versions.pop(k, None)


def anicli_version():
    if system.ANDROID:
        return android.anicli_version()
    exe = anicli_path()
    if not exe:
        return None
    key = _stat_key(exe) or exe
    if key not in _anicli_versions:
        try:
            out = subprocess.run([*system.script_argv(exe), "-V"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=10, **system.quiet()).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        _anicli_versions[key] = out.splitlines()[-1] if out else None
    return _anicli_versions[key]


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
        system.replace(tmp, path)
    return path


def anicli_env(link=None, capture=None, **extra):
    """The environment every ani-cli run gets: the menu helper, the user's
    stream preferences, and the link's pick (exact title, else position)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_ANICLI_")}
    prefs = stream_prefs()
    env.update(system.script_env())
    env.update(ANI_CLI_MENU=system.shell_path(anicli_menu_helper()), TERM="dumb",
               ANI_CLI_QUALITY=prefs["quality"],
               ANI_CLI_MODE=(link or {}).get("mode") or prefs["mode"],
               ANI_CLI_EXACT="0", ANI_CLI_DEFAULT_SOURCE="search")
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
# The curl builds the script tries in turn: dep_ch_failover "curl_chrome136,…,curl".
ANICLI_CURLS_RE = re.compile(r'curl_exe=\$\(dep_ch_failover "([^"]+)"')


def anicli_check():
    """{"ok": True/False, "missing": [...]} for the ani-cli on PATH; ok is
    None when it can't be read as a script (no verdict). None without ani-cli."""
    exe = anicli_path()
    if not exe:
        return None
    key = _stat_key(exe)
    if key is None:
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
            m = ANICLI_CURLS_RE.search(text)
            curls = [c.strip() for c in m[1].split(",") if c.strip().startswith("curl_")] if m else []
            result = {"ok": not missing, "missing": missing, "curls": curls}
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
    if not exe and system.ANDROID:
        if not android.fetch_anicli(force=True):
            raise AniCliError(android.problem() or android.NO_TERMUX)
        exe = anicli_path()
    if not exe:
        raise AniCliError("ani-cli was not found — install ani-cli and make sure it is on PATH.")
    check = anicli_check()
    if system.ANDROID and ((check and check["ok"] is False) or anicli_outdated()):
        if android.fetch_anicli():
            check = anicli_check()
    if check and check["ok"] is False:
        raise AniCliError(anicli_unsupported_message(check), needs_upgrade=True)
    if anicli_outdated():
        # Settings and the tour say so too, but a play click is where it's felt.
        raise AniCliError(anicli_too_old_message(), needs_upgrade=True)
    if system.ANDROID:
        return android.TermuxRun(args, env, stdout, stderr)  # one Termux job, Popen-shaped
    try:
        argv = [*system.script_argv(exe), *args]
    except FileNotFoundError as e:
        raise AniCliError(str(e))
    master, slave = system.open_pty()
    spawn = system.detached()
    if system.WINDOWS:
        spawn["creationflags"] |= system.SUSPENDED
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL if slave is None else slave,
                                stdout=stdout, stderr=stderr, env=env, **spawn)
    except OSError as e:
        if master is not None:
            os.close(master)
        raise AniCliError(f"ani-cli could not be started: {e}")
    finally:
        if slave is not None:
            os.close(slave)
    proc.hub_pty = master
    proc.hub_job = system.job_start(proc) if system.WINDOWS else None
    return proc


def _job(proc):
    """The run's Windows job: a handle, so a stand-in process's attribute
    is never handed to Windows."""
    job = getattr(proc, "hub_job", None)
    return job if type(job) is int and job else None


def anicli_release(proc):
    job = _job(proc)
    if job:
        proc.hub_job = None
        system.job_close(job)
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
    to signal: the run's job ends everything it started (TODO 158), and
    taskkill the process tree, for a run without a job."""
    pid = proc.pid
    try:
        if type(pid) is not int or pid <= 1 or (not system.WINDOWS and pid == os.getpgrp()):
            raise OSError("not a child process group")
        if system.WINDOWS:
            job = _job(proc)
            if job:
                system.job_end(job)
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


def clean_query(query):
    """A search query as ani-cli gets it, one argument: never one of its
    options ("-U" updates it, "-D" wipes its history), so no leading
    dashes, not even after a space ("- -U")."""
    return " ".join(query.split()).lstrip("- ")


def anicli_search(query):
    """ani-cli's search results for a query: [{index, title}]. A single hit
    is taken by ani-cli without asking, so its title stays unknown (None)."""
    query = clean_query(query)
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
