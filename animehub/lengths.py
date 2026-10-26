"""Episode lengths read from the files themselves (ffprobe, else mpv), so a
show's episodes and its match card say "24 min" before anything is played.
Each file is measured once and remembered with its size and mtime; a
changed file is measured again. One file at a time on a background worker,
paused while mpv plays."""

import os
import re
import subprocess
import threading
import time

from . import database, settings, system
from .config import config


TIMEOUT = 20
_lock = threading.Lock()
_known = {}          # file path -> (size, mtime_ns, seconds or None)
_loaded = {"db": None}
_files = {"rels": [], "gen": 0}
_worker = {"thread": None, "started": False}
status = {"state": "idle", "done": 0, "left": 0}


def enabled():
    return settings.automation_enabled("measure_lengths")


def _key(rel):
    from . import library  # library imports this module
    p = library.abs_path(rel)
    return str(p) if p else None


def _load():
    """The remembered lengths, read once per database."""
    if _loaded["db"] == config.DB:
        return
    with _lock:
        _known.clear()
        try:
            with database.db() as c:
                for path, size, mtime, secs in c.execute("SELECT path, size, mtime, seconds FROM file_lengths"):
                    _known[path] = (size, mtime, secs)
        except Exception:
            pass
        _loaded["db"] = config.DB


def get(rel):
    """Seconds the file at `rel` (library-relative) runs, or None."""
    if config.LIBRARY is None or not rel:
        return None
    _load()
    key = _key(rel)
    hit = _known.get(key) if key else None
    return hit[2] if hit else None


def find_tool():
    """("ffprobe", path) or ("mpv", path), or None."""
    for name in ("ffprobe", "mpv"):
        exe = system.find_program(name)
        if exe:
            return name, exe
    return None


def measure(path, tool=None):
    """Seconds a video file runs, or None when it can't be read."""
    tool = tool or find_tool()
    if not tool:
        return None
    name, exe = tool
    if name == "ffprobe":
        argv = [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    else:
        # mpv prints the length once the first frame is decoded; nothing is shown or heard.
        argv = [exe, "--no-config", "--vo=null", "--ao=null", "--aid=no", "--sid=no", "--frames=1",
                "--term-playing-msg=LEN=${=duration}", str(path)]
    try:
        out = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             errors="replace", timeout=TIMEOUT, **system.quiet()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(?:^|LEN=)(\d+(?:\.\d+)?)\s*$", out, re.M)
    secs = float(m.group(1)) if m else 0
    return secs if secs > 0 else None


def scan_done(rels):
    """A fresh library scan: these files (library-relative) should have a
    length. Measured in the background once the hub has started."""
    with _lock:
        _files["rels"] = list(rels)
        _files["gen"] += 1
    _kick()


def start():
    """Called once by the server at startup: from now on scans get measured."""
    _worker["started"] = True
    _kick()


def turned_on():
    _kick()


def _kick():
    if not _worker["started"] or not enabled() or config.LIBRARY is None:
        return
    with _lock:
        if _worker["thread"] and _worker["thread"].is_alive():
            return
        _worker["thread"] = threading.Thread(target=_work, daemon=True)
        _worker["thread"].start()


def _playing():
    from . import player
    with player._active_lock:
        return bool(player._active)


def _todo(rels):
    _load()
    out = []
    for rel in rels:
        path = _key(rel)
        if not path:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        hit = _known.get(path)
        if not hit or hit[0] != st.st_size or hit[1] != st.st_mtime_ns:
            out.append((path, st.st_size, st.st_mtime_ns))
    return out


def _work():
    tool = find_tool()
    seen = -1
    while tool and enabled():
        with _lock:
            if _files["gen"] == seen:
                break
            seen, rels = _files["gen"], _files["rels"]
        todo = _todo(rels)
        status.update(state="working" if todo else "idle", left=len(todo))
        for path, size, mtime in todo:
            if _files["gen"] != seen or not enabled():
                break
            while _playing() and enabled():
                time.sleep(5)
            secs = measure(path, tool)
            _known[path] = (size, mtime, secs)
            try:
                with database.db() as c:
                    c.execute("INSERT OR REPLACE INTO file_lengths(path, size, mtime, seconds) VALUES(?,?,?,?)",
                              (path, size, mtime, secs))
            except Exception:
                pass
            status["done"] += 1
            status["left"] = max(0, status["left"] - 1)
    status.update(state="idle", left=0)


def payload():
    """For Settings: on/off, which program measures, and the worker's progress.
    state: off / missing / idle / working."""
    tool = find_tool()
    state = "off" if not enabled() else "missing" if not tool else status["state"]
    return {"on": enabled(), "tool": tool[0] if tool else None, "state": state,
            "done": status["done"], "left": status["left"]}
