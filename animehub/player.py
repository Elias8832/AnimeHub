"""Local playback in mpv: sessions, resume, auto-play next."""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import anicli, database, library, mapping, pages, progress, streaming, util
from .config import config, SKIP_CREDITS_LUA


_active = {}
_active_lock = threading.Lock()
# Launch/termination of playback sessions is one serialized transition
# (TODO 24): supersede the old mpv, spawn the new one, register the entry —
# never interleaved, so two concurrent play requests cannot leave two
# live mpv processes.
_launch_lock = threading.Lock()
# The most recent session that never started playing (died before the IPC
# socket appeared), surfaced additively in /api/now so the UI can explain
# a play click that produced nothing.
_session_error = {"ep_id": None, "error": None, "ts": 0.0}


def ipc_socket_path():
    """A fresh mpv IPC socket path in the user's private runtime dir
    (TODO 52h); /tmp only when $XDG_RUNTIME_DIR is unset or missing."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    base = Path(runtime) if runtime and Path(runtime).is_dir() else Path("/tmp")
    return base / f"anime-hub-{uuid.uuid4().hex[:8]}.sock"


def mpv_ipc(sock_path, command, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps({"command": command}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    for line in buf.decode(errors="replace").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "data" in msg or ("error" in msg and msg.get("error") != "success"):
            return msg
    return None


def season_rank(sea):
    """One authoritative season order (TODO 24) — the UI shows a folder's
    seasons in the order the scan gives them: main seasons first, parts
    continuing after them, then finales, movies, and everything else
    (Specials). The
    scanner's numeric key alone cannot express this — Part N without a
    Season parent shares Season 1's key, so autoplay chained parts before
    season 1 while the UI showed seasons first."""
    label = sea.get("label") or ""
    m = re.match(r"^Season (\d+)", label, re.I)
    if m:
        return (0, int(m.group(1)), 0)
    m = re.match(r"^Part (\d+)", label, re.I)
    if m:
        return (1, int(m.group(1)), 0)
    if re.search(r"final", label, re.I):
        return (2, 0, 0)
    if re.match(r"^movies?$", label, re.I):
        return (3, 0, 0)
    return (4, 0, 0)


def ordered_seasons(s):
    """Seasons in the authoritative order — the scanner now sorts them
    this way too, so autoplay and the UI agree by construction."""
    return sorted(s["seasons"], key=season_rank)


def entry_of_episode(ep_id):
    """(AniList entry, its own episode number) an episode is: an online id
    names it; a file is what its folder season maps to. None for a file
    that maps to no entry."""
    if ep_id.startswith("stream:"):
        mid = streaming.stream_media_id(ep_id)
        try:
            return (mid, streaming._stream_num(ep_id.split(":", 2)[2])) if mid else None
        except ValueError:
            return None
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return None
    unit, n = mapping.unit_of_episode(streaming.trackable(s), ep_id)
    return (unit["media_id"], n) if unit else None


def next_episode(ep_id):
    """The episode id to play after ep_id, on its franchise page: the next
    episode of the same entry, else the first of the next main season.
    Files and online episodes chain into each other (the last file of a
    season → the first episode not on disk, played online). An episode that
    can't play (not available online) ends the chain rather than skipping
    ahead. A file that maps to no entry walks its folder instead. None at
    the end."""
    at = entry_of_episode(ep_id)
    if at:
        mid, n = at
        entries = pages.franchise_view(mid, background=False)["entries"]
        e = next((x for x in entries if x["id"] == mid), None)
        if e:
            def playable(entry, x):
                return x and (x["local"] or (x["online"] and entry["online"]["available"])) and x["id"]

            nxt = next((x for x in e["slots"] if x["number"] > n), None)
            if nxt or e["group"] != "main":
                return playable(e, nxt) or None
            mains = [x for x in entries if x["group"] == "main"]
            later = mains[mains.index(e) + 1:]
            return (playable(later[0], later[0]["slots"][0]) or None) if later and later[0]["slots"] else None
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return None
    sea = next((x for x in s["seasons"] if any(e["id"] == ep_id for e in x["episodes"])), None)
    if not sea:
        return None
    after = [e for e in sea["episodes"] if e["number"] > ep["number"]]
    if after:
        return min(after, key=lambda e: e["number"])["id"]
    seasons = ordered_seasons(s)
    for nxt in seasons[seasons.index(sea) + 1:]:
        eps = sorted(nxt["episodes"], key=lambda e: e["number"])
        if eps:
            return eps[0]["id"]
    return None


def autoplay_next(ep_id, version=0):
    """Chain into the episode after ep_id, carrying the chosen file version
    (TODO 24): the same index into the next episode's parallel file list
    keeps the user's picked variant instead of resetting to the first one;
    start_play clamps it to what exists. Files and online episodes chain
    into each other along the franchise page (next_episode)."""
    try:
        nxt = next_episode(ep_id)
        if nxt:
            start_play(nxt, version)
    except Exception as e:
        print(f"Autoplay: {e}", file=sys.stderr, flush=True)


def _owns_session(ep_id, proc):
    """Whether proc is still the registered session for ep_id — the
    generation guard every watcher effect goes through (TODO 24)."""
    with _active_lock:
        return _active.get(ep_id, {}).get("proc") is proc


def _record_session_error(ep_id, msg):
    _session_error.update(ep_id=ep_id, error=msg, ts=time.time())


def watch_process(proc, ep_id, sock_path, startup=15, failure=None):
    """Track one playback session over mpv's IPC socket. `startup` bounds
    the wait for the socket; `failure(rc)` words a session that ended
    before playback (stream sessions report ani-cli's own error)."""
    last_time = 0.0
    duration = 0.0
    # No position write until mpv has reported one: a stream (or a slow
    # file) answers on the socket while still buffering, and writing the
    # initial 0 then would erase the resume point if the user quits early.
    seen = False
    deadline = time.time() + startup
    connected = False
    while time.time() < deadline and proc.poll() is None:
        if not sock_path.exists():
            time.sleep(0.25)
            continue
        try:
            mpv_ipc(sock_path, ["set_property", "pause", "false"])
            connected = True
            with _active_lock:
                entry = _active.get(ep_id)
                if entry is not None and entry.get("proc") is proc:
                    entry["loading"] = False
            break
        except (FileNotFoundError, ConnectionRefusedError, socket.error):
            time.sleep(0.25)
    if not connected and proc.poll() is not None and _owns_session(ep_id, proc):
        # mpv died before playback started (bad file, missing decoder…):
        # record it so the UI can explain the dead play click (TODO 24).
        rc = proc.wait()
        # A stream's failure handler may retry (dub → sub, re-match) and
        # answer None: the replacement session owns the outcome then.
        msg = failure(rc) if failure else (f"mpv exited before playback started "
                                           f"(exit code {rc}). See mpv-stderr.log in the data dir.")
        if msg:
            _record_session_error(ep_id, msg)
    if connected:
        while proc.poll() is None:
            try:
                t = mpv_ipc(sock_path, ["get_property", "time-pos"])
                d = mpv_ipc(sock_path, ["get_property", "duration"])
                if t and isinstance(t.get("data"), (int, float)):
                    last_time = t["data"]
                    seen = True
                if d and isinstance(d.get("data"), (int, float)):
                    duration = d["data"]
                # Generation guard (TODO 24): a retired watcher must not write
                # the DB or the replacement's percentage — check ownership
                # immediately before every effect, not only at cleanup.
                if not _owns_session(ep_id, proc):
                    break
                if not seen:
                    try:
                        proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                now = util.utc_stamp()
                with database.db() as c:
                    c.execute(
                        """INSERT INTO episodes(id,position,duration,last_played) VALUES(?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET position=excluded.position,
                           duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE episodes.duration END,
                           last_played=excluded.last_played""",
                        (ep_id, last_time, duration, now),
                    )
                with _active_lock:
                    entry = _active.get(ep_id)
                    if entry is not None and entry.get("proc") is proc:
                        entry["percent"] = (last_time / duration * 100) if duration else 0
            except (FileNotFoundError, ConnectionRefusedError, socket.error):
                break
            # Wait out the interval, but wake the instant mpv exits —
            # sleeping through it would delay the next episode by up to 2s.
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        anicli.stop_process_group(proc, signal.SIGKILL)
        rc = proc.wait()
    anicli.anicli_release(proc)
    # Supersession (TODO 24): start_play terminates the replaced mpv with
    # SIGTERM, so returncode -SIGTERM means this watcher was retired — its
    # replacement owns tracking now, and this session must have no effects
    # at all (the old bug: a retired watcher marked the episode watched at
    # its own 80 % and overwrote the new session's position).
    retired = rc == -signal.SIGTERM or getattr(proc, "hub_retired", False)
    # "Done with this episode": the last position we saw was within ~3s of
    # the end (the poll runs every 2s, so a natural EOF — or a seek to the
    # end — always qualifies), or the skip-credits marker (hotkey quit).
    # The old >=90% rule still marks watched for manual quits that get far
    # enough into the episode.
    marker = Path(str(sock_path) + ".skip")
    done = (duration and duration - last_time <= 3) or marker.exists()
    if not retired and duration and (done or last_time / duration >= 0.9):
        pos_save = duration if done and last_time / duration < 0.9 else last_time
        with database.db() as c:
            c.execute(
                """INSERT INTO episodes(id,watched,last_played,position,duration) VALUES(?,?,datetime('now'),?,?)
                   ON CONFLICT(id) DO UPDATE SET watched=1, position=excluded.position,
                   duration=excluded.duration, last_played=excluded.last_played""",
                (ep_id, 1, pos_save, duration),
            )
        # Show-completion event (TODO 9, Decision 25): playback finishing the
        # matched release's last episode — natural EOF or the skip-credits
        # gesture both land here; manual marks never do. Rewatch replays
        # fire again by design (each is a new popup event).
        try:
            progress.completion_for_episode(ep_id)
        except Exception as exc:
            util.log("completion event failed: %s" % exc)
        # AniList automations (TODO 15, Decision 5): progress sync + optional
        # auto-add, playback-driven like the completion event, outbox-backed.
        try:
            progress.auto_track_after_play(ep_id)
        except Exception as exc:
            util.log("auto-track failed: %s" % exc)
    autoplay = False
    version = 0
    with _active_lock:
        # A same-episode replay inserts a fresh entry under the same id while
        # this watcher is winding down; only remove our own generation.
        entry = _active.get(ep_id, {})
        if entry.get("proc") is proc:
            version = entry.get("version", 0)
            _active.pop(ep_id, None)
            autoplay = not _active and bool(done) and not retired
    if autoplay:
        autoplay_next(ep_id, version)
    try:
        sock_path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
    except OSError:
        pass


def resume_args(ep_id):
    """mpv's --start for an episode, or nothing. Resume rule (TODO 24), the
    one authoritative rule the UI mirrors: a saved position is a resume
    point whenever it is non-trivial (> 1 % of the episode — the frontend's
    resumable() uses exactly this), for unwatched rows including past the
    92 % strip cutoff, and for watched rows still below the cutoff; a
    finished row restarts. The old 60 s floor disagreed with the UI's
    Resume label in the 1–60 s zone."""
    with database.db() as c:
        row = c.execute("SELECT position, duration, watched FROM episodes WHERE id=?", (ep_id,)).fetchone()
    pos = row[0] if row else 0
    dur = row[1] if row else 0
    watched = bool(row[2]) if row else False
    if dur and pos / dur > 0.01 and (pos / dur < 0.92 or not watched):
        return [f"--start={int(pos // 60)}:{int(pos % 60)}"]
    return []


def retire_session(proc):
    """Supersede a playback session (TODO 24): flag it for its watcher —
    an ani-cli session exits with its own code, not -SIGTERM — then stop
    its whole process group."""
    proc.hub_retired = True
    anicli.stop_process_group(proc)


def start_play(ep_id, version=0):
    if ep_id.startswith("stream:"):
        return streaming.start_stream(ep_id)
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    files = ep.get("files") or [{"path": ep["rel"], "label": "default"}]
    version = max(0, min(version, len(files) - 1))
    path = config.LIBRARY / files[version]["path"]
    if not path.exists():
        return {"error": "file missing"}
    sock = ipc_socket_path()
    args = ["mpv", str(path), f"--input-ipc-server={sock}", *resume_args(ep_id)]
    # No --save-position-on-quit (TODO 24): the hub's IPC loop is the only
    # resume store. mpv's watch-later state would otherwise fight an
    # intentional restart/reset — a stale mpv-side position can override
    # the --start the hub decided on.
    if SKIP_CREDITS_LUA.exists():
        args.append(f"--script={SKIP_CREDITS_LUA}")
    # One serialized transition (TODO 24): supersede → spawn → register.
    # Without the lock, concurrent play requests spawn two live mpv
    # processes and a watcher can reach auto-next mid-replacement.
    with _launch_lock:
        with _active_lock:
            old = next(iter(_active.values()), None)
        if old:
            retire_session(old["proc"])

        def spawn():
            """mpv's stderr lands in mpv-stderr.log (best effort — a log
            problem must never look like an mpv problem), and the fd is
            closed right after spawn: the child keeps its inherited copy."""
            err_target = subprocess.DEVNULL
            log = None
            try:
                config.DATA.mkdir(parents=True, exist_ok=True)
                log = open(config.DATA / "mpv-stderr.log", "ab")
                log.write(f"\n=== {util.utc_stamp()}  {path}\n".encode())
                err_target = log
            except OSError:
                if log:
                    log.close()
                log = None
            try:
                return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                        stderr=err_target, start_new_session=True)
            finally:
                if log:
                    log.close()

        try:
            proc = spawn()
        except FileNotFoundError:
            return {"error": "mpv was not found — install mpv and make sure it is on PATH."}
        except OSError as e:
            return {"error": f"mpv could not be started: {e}"}
        sea = next(x for x in s["seasons"] if any(e["id"] == ep_id for e in x["episodes"]))
        with _active_lock:
            _active.clear()
            _active[ep_id] = {"proc": proc, "ep_id": ep_id, "show_title": s["title"],
                              "label": streaming.session_label(sea, ep), "percent": 0, "version": version,
                              "loading": True}
    threading.Thread(target=watch_process, args=(proc, ep_id, sock), daemon=True).start()
    return {"ok": True}
