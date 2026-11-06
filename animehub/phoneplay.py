"""Watching on the phone (Decision 71, step 1): the phone's own player for
files in the library. The PC sends the file as it is (the phone's <video>
asks for pieces with HTTP Range, so jumping is instant) and the phone
reports where it is; resume, the watched rule, the completion popup, AniList
and auto-next are mpv's (player.py), so both players count the same way.

Only files the library knows are served: the request names an episode id,
never a path. Playing on the phone stops mpv on the PC unless Settings →
Phone remote allows both at once."""

import json
import mimetypes
import subprocess
import threading
import time

from . import library, player, settings, skips, streaming, system, util

# A phone player still counts as playing this long after its last report
# (it reports every few seconds while playing).
ALIVE = 30
_lock = threading.Lock()
# The one phone session: {"ep_id", "version", "show", "label", "started",
# "seen", "pc"} — "pc" = the mpv session it found (and stopped), so a later
# one is known to be new.
_session = {}


def both_allowed():
    return settings.automation_enabled("phone_play_both")


def file_of(ep_id, version=0):
    """(path, show, episode) of a library file, or None. Only files inside
    a configured library folder."""
    if not isinstance(ep_id, str) or ep_id.startswith("stream:"):
        return None
    s, ep = library.episode_by_id(ep_id)
    if not ep or not ep.get("rel") and not ep.get("files"):
        return None
    files = ep.get("files") or [{"path": ep["rel"]}]
    version = max(0, min(version or 0, len(files) - 1))
    path = library.abs_path(files[version]["path"])
    if path is None or not path.is_file():
        return None
    return path, s, ep


def _pc_proc():
    with player._active_lock:
        a = next(iter(player._active.values()), None)
        return a.get("proc") if a else None


def start(ep_id, version=0, from_start=False):
    """The phone is about to play an episode: what its player needs."""
    hit = file_of(ep_id, version)
    if not hit:
        return {"error": "This episode's file isn't on the PC."}
    path, s, ep = hit
    player.dismiss_next_prompt()
    pc = _pc_proc()
    if pc and not both_allowed():
        # Like the remote's stop, but no auto-play of the next episode on the PC.
        player.control("stop_after", True)
        player.control("stop")
    label = _label(s, ep, ep_id)
    with _lock:
        _session.clear()
        _session.update(ep_id=ep_id, version=version, show=s["title"], label=label,
                        started=time.time(), seen=time.time(), pc=pc, percent=0)
    return {"ok": True, "id": ep_id, "src": f"/api/phone/video?id={_q(ep_id)}&v={int(version or 0)}",
            "type": mimetypes.guess_type(path.name)[0] or "video/mp4",
            "start": player.resume_seconds(ep_id, from_start), "show": s["title"], "label": label}


def _label(s, ep, ep_id):
    sea = next((x for x in s.get("seasons") or () if any(e["id"] == ep_id for e in x["episodes"])), None)
    return streaming.session_label(sea, ep) if sea else ep.get("label") or ""


def _q(x):
    from urllib.parse import quote
    return quote(x, safe="")


def report(ep_id, position, duration, ended=False, stop=False):
    """The phone's player says where it is. `ended`: it reached the end;
    `stop`: it was closed. Answers what comes next, and whether the PC took
    over meanwhile."""
    with _lock:
        mine = _session.get("ep_id") == ep_id
        pc = _session.get("pc") if mine else None
        if mine:
            _session["seen"] = time.time()
            _session["percent"] = position / duration * 100 if duration else 0
    out = {"ok": True}
    now_pc = _pc_proc()
    if mine and now_pc and now_pc is not pc and not getattr(now_pc, "hub_retired", False) and not both_allowed():
        # Something started on the PC after the phone did: the PC wins.
        out["stopped"] = "Playing on the PC now."
        stop = True
    if position > 0 or duration > 0:
        player.save_position(ep_id, position, duration)
    if (ended or stop) and mine:
        done = ended or bool(duration and duration - position <= 3)
        player.finish_episode(ep_id, position, duration, done)
        with _lock:
            if _session.get("ep_id") == ep_id:
                _session.clear()
        if done and not out.get("stopped"):
            try:
                nxt = player.next_up(ep_id)
            except Exception as exc:
                util.log("phone next episode: %s" % exc)
                nxt = None
            if nxt and nxt.get("id"):
                out["next"] = {k: nxt.get(k) for k in ("id", "source", "label", "show")}
                if nxt["source"] == "local":
                    s2, ep2 = library.episode_by_id(nxt["id"])
                    if ep2:
                        out["next"].update(show=s2["title"], label=_label(s2, ep2, nxt["id"]))
    return out


def now():
    """The phone session for /api/now (additive), or None."""
    with _lock:
        if not _session or time.time() - _session["seen"] > ALIVE:
            return None
        return {k: _session[k] for k in ("ep_id", "show", "label", "percent")}


def _chapters(path):
    """The file's chapters as mpv names them ([{title, time}]), via ffprobe."""
    exe = system.find_program("ffprobe")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-v", "error", "-show_chapters", "-of", "json", str(path)],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                             timeout=15, **system.quiet()).stdout
        data = json.loads(out or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [{"title": (c.get("tags") or {}).get("title"), "time": float(c["start_time"])}
            for c in data.get("chapters") or [] if c.get("start_time") is not None]


def segments(ep_id, duration, version=0):
    """Opening/ending ranges for the phone (Decision 39's rules: chapters,
    then AniSkip where it's allowed), the same ranges mpv gets."""
    prefs = settings.skip_prefs()
    if all(mode == "off" for mode in prefs.values()):
        return []
    hit = file_of(ep_id, version)
    if not hit or not duration:
        return []
    segs = skips.chapter_segments(_chapters(hit[0]), duration, prefs)
    missing = [k for k in ("op", "ed") if prefs[k] != "off" and all(x["kind"] != k for x in segs)]
    entry = player.entry_of_episode(ep_id) if missing and player.aniskip_allowed(ep_id) else None
    if entry and isinstance(entry[1], int):
        mal = skips.mal_id(entry[0])
        segs = segs + skips.aniskip_segments(skips.aniskip_times(mal, entry[1], duration) if mal else None,
                                             duration, prefs, missing)
    return [{k: x[k] for k in ("kind", "start", "end", "auto", "finish") if k in x} for x in segs]


def byte_range(header, size):
    """An HTTP Range header → (start, end) inclusive, or None for the whole
    file; "bad" when it can't be met (416)."""
    if not header:
        return None
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    a, _, b = spec.strip().partition("-")
    try:
        if not a:  # the last n bytes
            n = int(b)
            if n <= 0:
                return "bad"
            return max(0, size - n), size - 1
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    if start >= size or end < start:
        return "bad"
    return start, min(end, size - 1)
