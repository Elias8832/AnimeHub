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

from . import anicli, database, library, mapping, pages, progress, settings, skips, streaming, system, tracks, util
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
    """A fresh mpv IPC address: a socket in the user's private runtime dir
    (TODO 52h), a named pipe on Windows (system.ipc_address)."""
    return system.ipc_address(f"anime-hub-{uuid.uuid4().hex[:8]}")


def mpv_env(sock_path):
    """mpv's environment: where skip-credits.lua leaves its "finished by
    the hotkey" marker (beside a socket, in the temp dir for a pipe)."""
    return {"ANIME_HUB_SKIP_MARKER": str(system.ipc_marker(sock_path))}


def mpv_ipc(sock_path, command, timeout=2.0):
    buf = system.ipc_exchange(sock_path, (json.dumps({"command": command}) + "\n").encode(), timeout)
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


def track_shows(ep_id):
    """Where an episode's remembered tracks live (tracks.py), first match
    wins: its AniList entry, so a file and the online episodes share them,
    then its library show, so a folder's next season starts with them."""
    try:
        hit = entry_of_episode(ep_id)
    except Exception:
        hit = None
    s, _ = library.episode_by_id(ep_id)
    keys = ["media:%s" % hit[0]] if hit else []
    if s and not str(s.get("id", "")).startswith("aid:"):
        keys.append("show:%s" % s["id"])
    return keys


def follow_tracks(sock_path, ep_id, state, data):
    """Called on every poll once mpv plays, with mpv's track-list: the
    first time, pick the show's remembered tracks; after that, remember a
    track the user switched to (in mpv or on the phone remote). state
    carries the show keys and what mpv played last poll."""
    if not isinstance(data, list) or not data:
        return
    if "shows" not in state:
        state["shows"] = track_shows(ep_id)
        if not state["shows"]:
            return
        for prop, val in tracks.changes(data, tracks.load(state["shows"])).items():
            mpv_ipc(sock_path, ["set_property", prop, val])
        data = (mpv_ipc(sock_path, ["get_property", "track-list"]) or {}).get("data") or data
    elif not state["shows"]:
        return
    now = tracks.selection(data)
    for kind in tracks.KINDS:
        if "seen" in state and now[kind] != state["seen"][kind] and now[kind]:
            tracks.save(state["shows"], kind, now[kind])
    state["seen"] = now


def track_menu(data):
    """The remote's track picker: {"audio": [...], "sub": [...]}, each
    {id, lang, title, selected}."""
    out = {"audio": [], "sub": []}
    for t in data if isinstance(data, list) else ():
        if t.get("type") in out and not t.get("image"):
            out[t["type"]].append({"id": t.get("id"), "lang": t.get("lang"), "title": t.get("title"),
                                   "selected": bool(t.get("selected"))})
    return out


def read_live(sock_path, position=True):
    """What the phone remote shows: position and length (unless the
    caller has them), paused, volume and the tracks."""
    get = lambda prop: (mpv_ipc(sock_path, ["get_property", prop]) or {}).get("data")
    live = {"paused": get("pause"), "volume": get("volume"), "speed": get("speed"), "sub_delay": get("sub-delay")}
    if position:
        live.update(position=get("time-pos"), duration=get("duration"))
    data = get("track-list")
    live = {k: v for k, v in live.items() if isinstance(v, (int, float, bool))}
    return live, data


# The phone remote's buttons (Decision 43): action → how it checks its value.
CONTROLS = {"toggle": None, "seek": "number", "seek_to": "number", "volume": "number",
            "audio": "track", "sub": "track", "stop": None, "next": None,
            "skip": None, "speed": "number", "sub_delay": "number", "stop_after": "bool"}
# The last speed picked on the remote: later episodes start with it until
# it is changed again (kept while the hub runs).
_speed = {"value": 1.0}


def _segment_now(a, sock):
    """The opening/ending the session is inside right now, or None."""
    pos = (mpv_ipc(sock, ["get_property", "time-pos"]) or {}).get("data")
    if not isinstance(pos, (int, float)):
        return None
    return next((x for x in a.get("segments") or () if x["start"] - 1 <= pos < x["end"] - 1), None)


def control(action, value=None):
    """One remote button on the playing session (a file or an online
    episode — the same mpv socket either way). "next" finishes the episode
    the way the skip-credits hotkey does: watched, then auto-play next."""
    if action not in CONTROLS:
        return {"error": "unknown action — one of: %s" % ", ".join(CONTROLS)}
    kind = CONTROLS[action]
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return {"error": "'value' must be a number"}
    if kind == "bool" and not isinstance(value, bool):
        return {"error": "'value' must be true or false"}
    if kind == "track" and not (value == "no" or (isinstance(value, int) and not isinstance(value, bool))):
        return {"error": "'value' must be a track id or \"no\""}
    with _active_lock:
        a = next(iter(_active.values()), None)
        sock = a.get("sock") if a else None
    if not sock:
        return {"error": "nothing is playing"}
    if action == "audio" and value == "no":
        return {"error": "audio can't be switched off"}
    if action == "stop_after":
        # Finish this episode, then no auto-play (checked when it ends).
        with _active_lock:
            a["stop_after"] = value
        return {"ok": True, "stop_after": value}
    if action == "skip":
        try:
            seg = _segment_now(a, sock)
        except OSError:
            return {"error": "mpv stopped answering"}
        if not seg:
            return {"error": "no opening or ending playing right now"}
        if seg.get("finish"):
            action, value = "next", None  # nothing after this ending: finish it like Enter does
        else:
            action, value, kind = "seek_to", seg["end"], "number"
    if action == "speed":
        value = _speed["value"] = round(max(0.25, min(4.0, value)), 2)
    if action == "sub_delay":
        value = round(max(-30.0, min(30.0, value)), 2)
    if kind == "number":
        value = max(0, min(130, value)) if action == "volume" else max(0, value) if action == "seek_to" else value
    cmd = {"toggle": ["cycle", "pause"],
           "seek": ["seek", value, "relative"],
           "seek_to": ["seek", value, "absolute"],
           "volume": ["set_property", "volume", value],
           "audio": ["set_property", "aid", value],
           "sub": ["set_property", "sid", value],
           "speed": ["set_property", "speed", value],
           "sub_delay": ["set_property", "sub-delay", value],
           "stop": ["quit"], "next": ["quit"]}[action]
    try:
        if action == "next":
            system.ipc_marker(sock).touch()
        mpv_ipc(sock, cmd)
        if action in ("stop", "next"):
            return {"ok": True}
        live, data = read_live(sock)
    except OSError:
        return {"error": "mpv stopped answering"}
    with _active_lock:
        if a is next(iter(_active.values()), None):
            a.update(live)
            a["tracks"] = track_menu(data)
    return {"ok": True, **live, "tracks": track_menu(data)}


def next_up(ep_id):
    """What plays after ep_id, on its franchise page: {"id", "source"} with
    source "local" / "online", "missing" when the next episode exists but
    can't play (not on disk, not available online — id None), or None at
    the end. The next episode of the same entry, else the first of the next
    main season. Files and online episodes chain into each other (the last
    file of a season → the first episode not on disk, played online). An
    episode that can't play ends the chain rather than skipping ahead. A
    file that maps to no entry walks its folder instead."""
    end = {"id": None, "source": None}
    at = entry_of_episode(ep_id)
    if at:
        mid, n = at
        page = pages.franchise_view(mid, background=False)
        entries = page["entries"]
        e = next((x for x in entries if x["id"] == mid), None)
        if e:
            missing = {"id": None, "source": "missing"}

            def playable(entry, x):
                if x["local"] and x["id"]:
                    return {"id": x["id"], "source": "local"}
                if x["online"] and entry["online"]["available"] and x["id"]:
                    return {"id": x["id"], "source": "online",
                            "show": page.get("title"), "label": f'{entry["label"]} · E{x["number"]}'}
                return missing

            # Aired on AniList but no slot (no ani-cli, nothing on disk):
            # it exists, it just can't play.
            nxt = next((x for x in e["slots"] if x["number"] > n), None)
            if nxt:
                return playable(e, nxt)
            if isinstance(n, int) and n < (e.get("aired") or 0):
                return missing
            if e["group"] != "main":
                return end
            mains = [x for x in entries if x["group"] == "main"]
            later = mains[mains.index(e) + 1:]
            if not later:
                return end
            if later[0]["slots"]:
                return playable(later[0], later[0]["slots"][0])
            return missing if later[0].get("aired") else end
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return end
    sea = next((x for x in s["seasons"] if any(e["id"] == ep_id for e in x["episodes"])), None)
    if not sea:
        return end
    after = [e for e in sea["episodes"] if e["number"] > ep["number"]]
    if after:
        return {"id": min(after, key=lambda e: e["number"])["id"], "source": "local"}
    seasons = ordered_seasons(s)
    for nxt in seasons[seasons.index(sea) + 1:]:
        eps = sorted(nxt["episodes"], key=lambda e: e["number"])
        if eps:
            return {"id": eps[0]["id"], "source": "local"}
    return end


def next_episode(ep_id):
    """The episode id to play after ep_id (next_up), None at the end or
    when the next episode can't play."""
    return next_up(ep_id)["id"]


# Next-episode prompt (TODO 10, Decision 2): when a file finishes and the
# next episode is online only, autoplay waits — the hub asks (with the VPN
# state) instead of silently fetching through ani-cli. Answered by playing
# it (start_play clears it) or POST /api/next-prompt/dismiss; served
# additively in /api/now for NEXT_PROMPT_TTL seconds.
NEXT_PROMPT_TTL = 1800
_next_prompt = {"id": None}


def next_prompt():
    p = dict(_next_prompt)
    return p if p["id"] and time.time() - p["ts"] < NEXT_PROMPT_TTL else None


def dismiss_next_prompt():
    _next_prompt["id"] = None


def autoplay_next(ep_id, version=0):
    """Chain into the episode after ep_id, carrying the chosen file version
    (TODO 24): the same index into the next episode's parallel file list
    keeps the user's picked variant instead of resetting to the first one;
    start_play clamps it to what exists. Files and online episodes chain
    into each other along the franchise page (next_up) — but a file
    chaining into an online episode waits for the user (next-episode
    prompt)."""
    try:
        nxt = next_up(ep_id)
        if nxt["source"] == "online" and not ep_id.startswith("stream:"):
            _next_prompt.update(id=nxt["id"], label=nxt.get("label"), show=nxt.get("show"),
                                kind="online", ts=time.time())
        elif nxt["id"]:
            carry_aniskip(ep_id, nxt["id"])
            if aniskip_question(nxt["id"]):
                # No VPN and nobody answered yet: wait and ask, as above.
                _next_prompt.update(id=nxt["id"], label=nxt.get("label"), show=nxt.get("show"),
                                    kind="aniskip", ts=time.time())
            else:
                start_play(nxt["id"], version)
    except Exception as e:
        print(f"Autoplay: {e}", file=sys.stderr, flush=True)


def _owns_session(ep_id, proc):
    """Whether proc is still the registered session for ep_id — the
    generation guard every watcher effect goes through (TODO 24)."""
    with _active_lock:
        return _active.get(ep_id, {}).get("proc") is proc


def _record_session_error(ep_id, msg):
    _session_error.update(ep_id=ep_id, error=msg, ts=time.time())


# AniSkip without a VPN (Decision 39): the hub asks before a play click
# uses it. The answer is kept per episode: a yes for a while at the start
# (an online episode can take 90 s to start), and autoplay passes it on
# down the chain, yes or no. A chain with no answer yet that finds no VPN
# waits for one (the next-episode prompt, kind "aniskip").
_aniskip_answers = {}
GRANT_TTL = 600
# How old an answer may be when its episode ends and autoplay carries it
# on (a long movie still counts).
CHAIN_TTL = 4 * 3600


def answer_aniskip(ep_id, yes):
    _aniskip_answers[ep_id] = (bool(yes), time.time())


def carry_aniskip(from_id, to_id):
    yes, ts = _aniskip_answers.get(from_id, (None, 0))
    if yes is not None and time.time() - ts < CHAIN_TTL:
        answer_aniskip(to_id, yes)


def _vpn_missing():
    vpn = settings.vpn_status()
    return vpn["known"] and not vpn["active"]


def aniskip_question(ep_id):
    """Whether playing ep_id now needs the user's AniSkip answer first: on,
    the question switched on, no VPN, and no answer for this episode."""
    if not settings.automation_enabled("aniskip") or not settings.automation_enabled("aniskip_vpn_warn"):
        return False
    yes, ts = _aniskip_answers.get(ep_id, (None, 0))
    return (yes is None or time.time() - ts >= GRANT_TTL) and _vpn_missing()


def aniskip_allowed(ep_id):
    if not settings.automation_enabled("aniskip"):
        return False
    if not settings.automation_enabled("aniskip_vpn_warn") or not _vpn_missing():
        return True
    yes, ts = _aniskip_answers.get(ep_id, (False, 0))
    return yes and time.time() - ts < GRANT_TTL


def send_skips(proc, ep_id, sock_path, duration):
    """Hand mpv's skip script this episode's opening/ending ranges
    (Decision 39). Runs on its own thread: reading chapters and asking
    AniSkip must never hold up tracking. Best effort: a failure only means
    no ranges, and Enter keeps finishing the episode."""
    prefs = settings.skip_prefs()
    if all(mode == "off" for mode in prefs.values()):
        return
    threading.Thread(target=_send_skips, args=(proc, ep_id, sock_path, duration, prefs), daemon=True).start()


def _send_skips(proc, ep_id, sock_path, duration, prefs):
    try:
        chapters = (mpv_ipc(sock_path, ["get_property", "chapter-list"]) or {}).get("data")
        segs = skips.chapter_segments(chapters, duration, prefs)
        missing = [k for k in ("op", "ed") if prefs[k] != "off" and all(x["kind"] != k for x in segs)]
        # AniSkip only when the user switched it on (and, without a VPN, said
        # yes for this play), and only for a kind the chapters don't cover.
        hit = entry_of_episode(ep_id) if missing and aniskip_allowed(ep_id) else None
        if hit and isinstance(hit[1], int):
            mal = skips.mal_id(hit[0])
            segs = segs + skips.aniskip_segments(skips.aniskip_times(mal, hit[1], duration) if mal else None,
                                                 duration, prefs, missing)
        # The session may have ended or been replaced while AniSkip answered.
        if segs and _owns_session(ep_id, proc):
            mpv_ipc(sock_path, ["script-message", "anime-hub-segments", json.dumps({"segments": segs, "marks": settings.automation_enabled("mpv_marks")})])
            with _active_lock:  # the phone remote's "Skip opening" (Decision 43)
                entry = _active.get(ep_id)
                if entry is not None and entry.get("proc") is proc:
                    entry["segments"] = [{k: x[k] for k in ("kind", "start", "end", "finish") if k in x} for x in segs]
    except Exception as exc:
        util.log("skip ranges not sent: %s" % exc)


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
    skips_sent = False
    picks = {} if tracks.enabled() else None
    speed_set = False
    deadline = time.time() + startup
    connected = False
    while time.time() < deadline and proc.poll() is None:
        if not system.ipc_ready(sock_path):
            time.sleep(0.25)
            continue
        try:
            mpv_ipc(sock_path, ["set_property", "pause", "false"])
            connected = True
            with _active_lock:
                entry = _active.get(ep_id)
                if entry is not None and entry.get("proc") is proc:
                    entry["loading"] = False
                    entry["sock"] = sock_path  # the phone remote's way in
            break
        except (FileNotFoundError, ConnectionRefusedError, socket.error):
            time.sleep(0.25)
    if connected:
        try:
            nxt = next_up(ep_id)
            with _active_lock:
                entry = _active.get(ep_id)
                if entry is not None and entry.get("proc") is proc:
                    entry["next"] = {"source": nxt["source"]}
        except Exception as exc:
            util.log("next-episode check failed: %s" % exc)
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
                if not skips_sent and duration:
                    skips_sent = True
                    send_skips(proc, ep_id, sock_path, duration)
                # Paused, volume and tracks for the phone remote; the
                # track-list also feeds the track memory.
                live, track_list = read_live(sock_path, position=False)
                live.update(position=last_time, duration=duration)
                if not speed_set:
                    speed_set = True
                    if _speed["value"] != 1.0 and live.get("speed") == 1.0:
                        mpv_ipc(sock_path, ["set_property", "speed", _speed["value"]])
                        live["speed"] = _speed["value"]
                if picks is not None:
                    try:
                        follow_tracks(sock_path, ep_id, picks, track_list)
                    except Exception as exc:
                        util.log("track memory: %s" % exc)
                        picks = None
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
                        entry.update(live)
                        entry["tracks"] = track_menu(track_list)
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
        anicli.stop_process_group(proc, system.SIGKILL)
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
    marker = system.ipc_marker(sock_path)
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
            autoplay = not _active and bool(done) and not retired and not entry.get("stop_after")
    if autoplay:
        autoplay_next(ep_id, version)
    try:
        system.ipc_remove(sock_path)
        marker.unlink(missing_ok=True)
    except OSError:
        pass


def resume_args(ep_id, from_start=False):
    """mpv's --start for an episode, or nothing. Resume rule (TODO 24), the
    one authoritative rule the UI mirrors: a saved position is a resume
    point whenever it is non-trivial (> 1 % of the episode — the frontend's
    resumable() uses exactly this), for unwatched rows including past the
    92 % strip cutoff, and for watched rows still below the cutoff; a
    finished row restarts. The old 60 s floor disagreed with the UI's
    Resume label in the 1–60 s zone."""
    if from_start:  # the episode menu's "Play from the start"; the saved point stays until playback moves past it
        return []
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


def start_play(ep_id, version=0, from_start=False):
    dismiss_next_prompt()  # playing anything answers the prompt
    if ep_id.startswith("stream:"):
        return streaming.start_stream(ep_id, from_start=from_start)
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    files = ep.get("files") or [{"path": ep["rel"], "label": "default"}]
    version = max(0, min(version, len(files) - 1))
    path = library.abs_path(files[version]["path"])
    if path is None or not path.exists():
        return {"error": "file missing"}
    sock = ipc_socket_path()
    args = ["mpv", str(path), f"--input-ipc-server={sock}", *resume_args(ep_id, from_start), *settings.mpv_flags()]
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
                return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err_target,
                                        env={**os.environ, **mpv_env(sock)}, **system.detached())
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
