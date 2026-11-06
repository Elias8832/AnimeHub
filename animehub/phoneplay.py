"""Watching on the phone (Decision 71): the phone's own player, for files in
the library (step 1) and online episodes through the PC (step 2, below). The PC sends the file as it is (the phone's <video>
asks for pieces with HTTP Range, so jumping is instant) and the phone
reports where it is; resume, the watched rule, the completion popup, AniList
and auto-next are mpv's (player.py), so both players count the same way.

Only files the library knows are served: the request names an episode id,
never a path. Playing on the phone stops mpv on the PC unless Settings →
Phone remote allows both at once."""

import json
import mimetypes
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from . import anicli, database, library, player, prepare, settings, skips, streaming, system, util
from .config import config

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


def _begin(ep_id, version, show, label):
    """The phone takes over: mpv on the PC stops (unless both may play),
    and this is the phone's one session."""
    player.dismiss_next_prompt()
    pc = _pc_proc()
    if pc and not both_allowed():
        # Like the remote's stop, but no auto-play of the next episode on the PC.
        player.control("stop_after", True)
        player.control("stop")
    with _lock:
        _session.clear()
        _session.update(ep_id=ep_id, version=version, show=show, label=label,
                        started=time.time(), seen=time.time(), pc=pc, percent=0)


def start(ep_id, version=0, from_start=False):
    """The phone is about to play a file: what its player needs."""
    hit = file_of(ep_id, version)
    if not hit:
        return {"error": "This episode's file isn't on the PC."}
    path, s, ep = hit
    label = _label(s, ep, ep_id)
    _begin(ep_id, version, s["title"], label)
    return {"ok": True, "id": ep_id, "src": f"/api/phone/video?id={_q(ep_id)}&v={int(version or 0)}",
            "type": mimetypes.guess_type(path.name)[0] or "video/mp4", "hls": False, "subs": [],
            "start": player.resume_seconds(ep_id, from_start), "show": s["title"], "label": label}


def _label(s, ep, ep_id):
    sea = next((x for x in s.get("seasons") or () if any(e["id"] == ep_id for e in x["episodes"])), None)
    return streaming.session_label(sea, ep) if sea else ep.get("label") or ""


def _q(x):
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
    if not duration:
        return []
    if ep_id.startswith("stream:"):
        segs = []  # an online episode has no file to read chapters from
    else:
        hit = file_of(ep_id, version)
        if not hit:
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


# --- step 2: online episodes through the PC --------------------------------
# ani-cli finds the episode as always, and hands its player the link, the
# referrer and the subtitle file. For the phone that player is this script:
# it writes them down and plays nothing. The PC then fetches the video with
# that referrer and passes it on (the relay), only for links handed over
# this way, only to a paired phone (Decision 71). No provider knowledge here.
CAPTURE_SH = r"""#!/bin/sh
# Written by Anime Hub (hub.py), regenerated on demand, edits are lost.
# ani-cli's player when an episode plays on the phone: keep what ani-cli
# hands mpv (referrer, subtitles, link) for the hub, and play nothing.
out="$HUB_ANICLI_PHONE_OUT"
: >"$out.tmp" || exit 1
for a in "$@"; do printf '%s\n' "$a" >>"$out.tmp"; done
mv -f "$out.tmp" "$out"
"""
LINK_TTL = 6 * 3600  # a long movie, paused for a while
PLAYLIST_MAX = 5_000_000
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
_links = {}  # token → {"ep_id", "url", "referrer", "subs", "allowed", "ts"}
_URL_RE = re.compile(r"https?://", re.I)
_URI_ATTR = re.compile(r'URI="([^"]+)"')


def player_args(lines):
    """What ani-cli handed its player → {"url", "referrer", "subs"}, or None."""
    lines = [x for x in lines if x]
    urls = [x for x in lines if _URL_RE.match(x)]
    if not urls:
        return None
    ref = next((x.split("=", 1)[1] for x in lines if x.startswith("--referrer=")), "")
    subs = []
    for x in lines:
        if x.startswith("--sub-file="):
            subs += [u for u in re.split(r"(?<!\\):(?=https?://)", x.split("=", 1)[1]) if u]
    return {"url": urls[-1], "referrer": ref, "subs": subs}


def _run_capture(link, token, mode):
    """Run ani-cli for one episode with the capture script as its player.
    (args dict, None) or (None, ani-cli's error text)."""
    script = prepare.player_script("anicli-mpv-phone.sh", CAPTURE_SH)
    if not script:
        return None, "ani-cli can't be given the hub's player here (a space in the data folder's path)."
    folder = config.DATA / "phone" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    out = folder / "args"
    env = anicli.anicli_env(link, ANI_CLI_PLAYER=script, ANI_CLI_MODE=mode, ANI_CLI_LOG="0",
                            HUB_ANICLI_PHONE_OUT=system.shell_path(out))
    err = folder / "stderr.log"
    try:
        try:
            with open(err, "ab") as f:
                proc = anicli.anicli_spawn(["--exit-after-play", "-e", token, link["query"]], env, subprocess.DEVNULL, f)
        except anicli.AniCliError as e:
            return None, str(e)
        deadline = time.time() + anicli.STREAM_STARTUP_TIMEOUT
        while time.time() < deadline and not out.exists() and proc.poll() is None:
            time.sleep(0.2)
        if proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                anicli.stop_process_group(proc, system.SIGKILL)
                proc.wait()
        anicli.anicli_release(proc)
        try:
            got = player_args(out.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            got = None
        if got:
            return got, None
        try:
            text = err.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return None, anicli.anicli_message(text) or "ani-cli found nothing to play"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def start_online(ep_id, mode=None, from_start=False):
    """The phone plays an online episode: ani-cli finds it (up to 90 s),
    then the phone's player gets the relay's address."""
    mid = streaming.stream_media_id(ep_id)
    token = ep_id.split(":", 2)[2] if mid else ""
    if not mid or not anicli.STREAM_EP_RE.match(token):
        return {"error": "unknown episode"}
    try:
        link = streaming.stream_autolink(mid)
    except anicli.AniCliError as e:
        return {"error": str(e)}
    if mode in anicli.STREAM_MODES and mode != link["mode"]:
        streaming.stream_set_mode(mid, mode)
        link["mode"] = mode
    if token not in link["episodes"] and time.time() - link["episodes_ts"] > 60:
        try:
            streaming.stream_refresh(mid)
            link = streaming.stream_link_get(mid)
        except anicli.AniCliError:
            pass
    code = f"E{token}"
    if token not in link["episodes"]:
        return {"error": f"{code} isn't out yet."}
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    label = _label(s, ep, ep_id)
    got, msg = _run_capture(link, token, link["mode"])
    note = None
    if not got and link["mode"] == "dub" and "no sources found for dub" in (msg or "").lower():
        got, msg = _run_capture(link, token, "sub")
        note = f"{code} isn't dubbed yet — playing it subbed."
    if not got:
        util.log("phone stream %s failed: %s" % (ep_id, msg))
        return {"error": streaming.friendly_stream_error(msg, code)}
    with database.db() as c:
        # Identity stamp (TODO 25), as start_stream does.
        c.execute("INSERT INTO episodes(id,show_id,label) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE"
                  " SET show_id=excluded.show_id, label=excluded.label", (ep_id, s["id"], label))
    tok = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, v in _links.items() if now - v["ts"] > LINK_TTL]:
            _links.pop(k, None)
        _links[tok] = {"ep_id": ep_id, "url": got["url"], "referrer": got["referrer"], "subs": got["subs"],
                       "allowed": {got["url"]}, "ts": now}
    _begin(ep_id, 0, s["title"], label)
    out = {"ok": True, "id": ep_id, "src": relay_url(tok, got["url"]), "hls": _looks_hls(got["url"]),
           "subs": [f"/api/phone/sub/{tok}/{i}" for i in range(len(got["subs"]))],
           "start": player.resume_seconds(ep_id, from_start), "show": s["title"], "label": label}
    if note:
        out["note"] = note
    return out


def _looks_hls(url):
    return urlparse(url).path.lower().endswith(".m3u8")


def relay_url(tok, url):
    return f"/api/phone/relay/{tok}?u={_q(url)}"


def _link(tok):
    with _lock:
        L = _links.get(tok)
        if L and time.time() - L["ts"] > LINK_TTL:
            _links.pop(tok, None)
            L = None
        return L


def rewrite_playlist(tok, base, text):
    """An HLS playlist with every address pointed back at the relay. Only
    addresses that appear here may be fetched through it later."""
    L = _link(tok)
    allowed = L["allowed"] if L else set()

    def to_relay(u):
        full = urljoin(base, u.strip())
        allowed.add(full)
        return relay_url(tok, full)

    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            line = _URI_ATTR.sub(lambda m: 'URI="%s"' % to_relay(m.group(1)), line)
        elif line.strip():
            line = to_relay(line)
        out.append(line)
    return "\n".join(out) + "\n"


class RelayRefused(Exception):
    """Not a link ani-cli handed over for the phone (or it ran out)."""


def relay_open(tok, url, rng=None):
    """Fetch one address through the relay, with ani-cli's referrer.
    ("playlist", text) for an HLS playlist (rewritten), else ("data",
    response) to pass on as it comes. HTTPError passes the site's answer on."""
    L = _link(tok)
    if not L or url not in L["allowed"]:
        raise RelayRefused(url)
    headers = {"User-Agent": UA}
    if L["referrer"]:
        headers["Referer"] = L["referrer"]
    if rng:
        headers["Range"] = rng
    resp = urlopen(Request(url, headers=headers), timeout=20)
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "mpegurl" in ctype or _looks_hls(url):
        try:
            body = resp.read(PLAYLIST_MAX)
        finally:
            resp.close()
        if body.lstrip().startswith(b"#EXTM3U"):
            return "playlist", rewrite_playlist(tok, resp.geturl() or url, body.decode("utf-8", "replace"))
        return "bytes", body
    return "data", resp


_SRT_TIME = re.compile(r"(\d\d:\d\d:\d\d),(\d\d\d)")


def subtitle(tok, n):
    """A subtitle file ani-cli handed over, as WebVTT (what <track> reads)."""
    L = _link(tok)
    if not L or not 0 <= n < len(L["subs"]):
        raise RelayRefused(n)
    headers = {"User-Agent": UA}
    if L["referrer"]:
        headers["Referer"] = L["referrer"]
    with urlopen(Request(L["subs"][n], headers=headers), timeout=20) as r:
        text = r.read(PLAYLIST_MAX).decode("utf-8-sig", "replace")
    return to_vtt(text)


def to_vtt(text):
    if text.lstrip().startswith("WEBVTT"):
        return text
    return "WEBVTT\n\n" + _SRT_TIME.sub(r"\1.\2", text.replace("\r\n", "\n"))
