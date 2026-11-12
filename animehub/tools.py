"""Windows sets itself up (Decision 87). One click installs what Anime Hub
needs and the PC lacks: Scoop (scoop.sh, into the user's own folder, no
admin rights), then Git for Windows, mpv, ani-cli and yt-dlp through it.
Only on that click, never from a phone, and a step counts as done when the
program is really there afterwards (Scoop's exit codes don't say). The hub
holds no installer of its own: Scoop's official script does the work.

Also here: the Start menu shortcut a zip copy gives itself once."""

import subprocess
import threading
from pathlib import Path
from urllib.request import Request, urlopen

from . import anicli, settings, system, util
from .config import config

SCOOP_URL = "https://get.scoop.sh"
SCOOP_MAX = 1_000_000
STEP_LIMIT = 30 * 60  # one step: Git is the big download
TAIL = 12

# id, the name the page shows, what Scoop is asked for.
STEPS = (
    ("scoop", "Scoop", None),
    ("git", "Git for Windows", ("install", "git")),
    ("extras", "Scoop's extras list", ("bucket", "add", "extras")),
    ("mpv", "mpv", ("install", "extras/mpv")),
    ("ani-cli", "ani-cli", ("install", "ani-cli")),
    ("yt-dlp", "yt-dlp", ("install", "yt-dlp")),
)
NAMES = {i: name for i, name, _ in STEPS}
ASKS = {i: ask for i, _, ask in STEPS}
TOOLS = ("git", "mpv", "ani-cli", "yt-dlp")  # what the user is told about; the other two are the way there
# What the user picks (Elias: ani-cli and yt-dlp must be optional). Downloads
# go through ani-cli too, so they bring the online group along.
GROUPS = (
    ("play", "Playback", "mpv. Needed to play anything.", ("mpv",)),
    ("online", "Online episodes", "ani-cli, and Git for Windows to run it. Watch episodes you don't have as files.", ("git", "ani-cli")),
    ("download", "Downloads", "yt-dlp. Save online episodes into your library.", ("git", "ani-cli", "yt-dlp")),
)

_lock = threading.Lock()
_job = {"state": "idle", "steps": [], "error": None}


def log_path():
    return config.DATA / "tools-install.log"


def have(step):
    """Is this step's result on the PC already?"""
    if step == "scoop":
        return bool(system.scoop_script())
    if step == "git":  # ani-cli runs in its bash, and Scoop's lists come through git
        return bool(system.git_bash() and system.find_program("git"))
    if step == "extras":
        return (system.scoop_root() / "buckets" / "extras").is_dir()
    if step == "ani-cli":
        return bool(anicli.anicli_path())
    return bool(system.find_program(step))


def wanted(groups):
    """The tools the picked groups need, in TOOLS order. None = all."""
    if groups is None:
        return list(TOOLS)
    need = {tool for g, _, _, tools_ in GROUPS if g in groups for tool in tools_}
    return [t for t in TOOLS if t in need]


def plan(groups=None):
    """The steps a click would run, in order: every missing tool the picked
    groups need, and the way to it (Scoop, its extras list) only when
    something needs it."""
    missing = [t for t in wanted(groups) if not have(t)]
    if not missing:
        return []
    out = [] if have("scoop") else ["scoop"]
    if "git" in missing:
        out.append("git")
    if ("mpv" in missing or "ani-cli" in missing) and not have("extras"):
        out.append("extras")
    return out + [t for t in missing if t != "git"]


def can():
    """This PC can be set up with a click: Windows with PowerShell."""
    return system.WINDOWS and bool(system.powershell())


def payload():
    """Additive in /api/settings: tools_install {can, missing, job}."""
    ok = can()
    with _lock:
        job = {**_job, "steps": [dict(s) for s in _job["steps"]]}
    lacking = {t for t in TOOLS if not have(t)} if ok else set()
    groups = [{"id": g, "name": name, "what": what, "missing": True}
              for g, name, what, need in GROUPS if lacking & set(need)]
    return {"can": ok, "missing": [NAMES[t] for t in TOOLS if t in lacking], "groups": groups, "job": job}


def install(groups=None):
    """POST /api/tools-install {groups?}: only on the user's click, only the
    picked groups (all when none are named)."""
    if not can():
        return {"error": "Only the Windows app installs its tools. Use your package manager."}
    if groups is not None:
        groups = [g for g in groups if g in {x[0] for x in GROUPS}]
        if not groups:
            return {"error": "Pick at least one thing to install."}
    steps = plan(groups)
    with _lock:
        if _job["state"] == "working":
            return {"ok": True}
        if not steps:
            _job.update(state="done", steps=[], error=None)
            return {"ok": True}
        _job.update(state="working", error=None, steps=[{"id": s, "name": NAMES[s], "state": "wait"} for s in steps])
    threading.Thread(target=_work, args=(steps,), daemon=True).start()
    return {"ok": True}


def _mark(step, state):
    with _lock:
        for s in _job["steps"]:
            if s["id"] == step:
                s["state"] = state


def _work(steps):
    error = None
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        log_path().write_text("", encoding="utf-8")
    except OSError:
        pass
    for step in steps:
        _mark(step, "working")
        try:
            # An earlier step can bring this one along (ani-cli brings mpv).
            tail = "" if have(step) else _run(_command(step))
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            tail = str(e) or type(e).__name__
        system.refresh_path()
        if have(step):
            _mark(step, "ok")
            continue
        _mark(step, "failed")
        error = "%s didn't install.%s" % (NAMES[step], " " + tail if tail else "")
        util.log("tools install: %s" % error)
        break
    with _lock:
        _job.update(state="failed" if error else "done", error=error)


def _shell():
    return [system.powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]


def _command(step):
    """What to start for a step. Scoop itself: its official installer,
    fetched first and looked at, then run from a file."""
    if step != "scoop":
        return _shell() + [system.scoop_script() or ""] + list(ASKS[step])
    try:
        with urlopen(Request(SCOOP_URL, headers={"User-Agent": "anime-hub/1.0"}), timeout=30) as r:
            raw = r.read(SCOOP_MAX + 1)
    except OSError:
        raise ValueError("Could not reach scoop.sh. Check the internet connection.")
    if len(raw) > SCOOP_MAX or b"Scoop" not in raw or b"<html" in raw[:300].lower():
        raise ValueError("The download from scoop.sh didn't look like Scoop's installer.")
    script = config.DATA / "scoop-install.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(raw)
    # Scoop's installer stops under admin rights unless it is told.
    return _shell() + [str(script)] + (["-RunAsAdmin"] if system.is_admin() else [])


def _run(argv):
    """Run one step to its end, its output into the log. Returns the last
    lines it printed, as one line (what the page shows when it failed)."""
    lines = []
    with open(log_path(), "a", encoding="utf-8", errors="replace") as log:
        log.write("\n> %s\n" % " ".join(argv[6:] or argv))
        log.flush()
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", **system.quiet())
        stop = threading.Timer(STEP_LIMIT, proc.kill)
        stop.start()
        try:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                line = line.strip()
                if line:
                    lines = (lines + [line])[-TAIL:]
            proc.wait()
        finally:
            stop.cancel()
    return ("Last output: " + " ".join(lines[-3:])[:400]) if lines else ""


# ---------- the Start menu (TODO 220) ----------

def start_menu_once(exe=None, folder=None):
    """A copy from the Windows zip puts "Anime Hub" into the Start menu the
    first time it runs, and again when its folder moved. A shortcut the user
    deleted stays deleted. Returns what it did: "added", "kept" or None."""
    from . import update  # update.py needs settings; keep this module light to import
    if not system.WINDOWS or not (exe or update.zip_copy()):
        return None
    exe = str(exe or update.ROOT / "AnimeHub.exe")
    folder = Path(folder) if folder else system.start_menu_folder()
    was = settings.get_setting("start_menu_exe")
    if was == exe or not folder:
        return "kept"
    if was and not (folder / system.SHORTCUT).is_file():
        settings.set_setting("start_menu_exe", exe)  # deleted by the user: not our place to bring it back
        return "kept"
    if not system.add_start_menu(exe, folder):
        return None
    settings.set_setting("start_menu_exe", exe)
    return "added"
