"""Opening/ending skip ranges for mpv's skip script (mpv/skip-credits.lua),
Decision 39.

The file's own chapters come first ("Opening", "OP2", "Ending", "ED",
"Credits"…): they never leave the PC. A kind the chapters don't cover can
come from AniSkip, the crowd-sourced times keyed by MyAnimeList id and
episode number, only while the user has switched it on (`aniskip`, off by
default: AniSkip would learn what is watched and when). Every range is
checked so that a bad chapter name or a bad submission can't skip story."""

import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import database


# "Intro" and "Outro" are left out on purpose: many releases call the cold
# open "Intro" and a post-credits scene "Outro", and both are story.
OP_RE = re.compile(r"^(?:op\d*|opening)\b", re.I)
ED_RE = re.compile(r"^(?:ed\d*|ending|end credits|credits)\b", re.I)

# A range shorter or longer than any real opening/ending is a mislabelled
# chapter (or half the episode): skipping it would cost story.
MIN_LEN, MAX_LEN = 20.0, 240.0
# Skipping an ending jumps to whatever follows it (a post-credits scene, a
# preview). Only an ending that runs to the end of the file, with nothing
# after it, finishes the episode when skipped, the same as Enter outside a
# range: a seek to the very end could stop mpv before the watcher sees it.
END_SLACK = 1.0


def chapter_kind(title):
    t = (title or "").strip() if isinstance(title, str) else ""
    if OP_RE.match(t):
        return "op"
    if ED_RE.match(t):
        return "ed"
    return None


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _segment(kind, start, end, prefs, duration):
    return {"kind": kind, "start": float(start), "end": float(end), "auto": prefs[kind] == "auto",
            "finish": kind == "ed" and end >= duration - END_SLACK}


def chapter_segments(chapters, duration, prefs):
    """mpv's chapter-list ([{title, time}]) and the file's duration → the
    ranges to offer: [{kind, start, end, auto, finish}]. `prefs` maps
    "op"/"ed" to "off", "prompt" or "auto"."""
    if not _num(duration) or duration <= 0 or not isinstance(chapters, list):
        return []
    marks = sorted((float(c["time"]), chapter_kind(c.get("title")))
                   for c in chapters if isinstance(c, dict) and _num(c.get("time")) and 0 <= c["time"] < duration)
    out = []
    for i, (start, kind) in enumerate(marks):
        if kind not in ("op", "ed") or prefs.get(kind, "off") == "off":
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else float(duration)
        if not MIN_LEN <= end - start <= MAX_LEN:
            continue
        out.append(_segment(kind, start, end, prefs, duration))
    return out


ANISKIP_URL = "https://api.aniskip.com/v2/skip-times/%d/%d?types[]=op&types[]=ed&episodeLength=0"
FOUND_TTL = 30 * 86400
# Times get submitted after an episode airs: look again soon.
MISSING_TTL = 2 * 86400
# AniSkip's answer is a short JSON list; anything bigger is not it.
MAX_BYTES = 256 * 1024
# AniSkip's times fit the release they were taken from. A file up to this
# much longer or shorter is the same episode with a few seconds more or less
# (logos, a recap card) somewhere; past it, the times can't be trusted.
MAX_DRIFT = 15.0


def aniskip_segments(results, duration, prefs, kinds=("op", "ed")):
    """AniSkip results → the same ranges chapter_segments gives, for the
    kinds asked. Where a release differs in length by d seconds, the extra
    time may sit before or after the range, so only the part that is inside
    it either way is kept (each end moves in by up to d): a few seconds of
    the opening may still play, but no story is skipped. Of several
    submissions, the one from the closest-length release wins."""
    if not _num(duration) or duration <= 0 or not isinstance(results, list):
        return []
    best = {}
    for r in results:
        if not isinstance(r, dict) or not isinstance(r.get("interval"), dict):
            continue
        kind, iv = r.get("skipType"), r["interval"]
        if kind not in kinds or prefs.get(kind, "off") == "off":
            continue
        length, start, end = r.get("episodeLength"), iv.get("startTime"), iv.get("endTime")
        if not all(_num(x) for x in (length, start, end)) or length <= 0:
            continue
        d = duration - length
        if abs(d) > MAX_DRIFT:
            continue
        start, end = max(0.0, start + max(0.0, d)), min(end + min(0.0, d), float(duration))
        if not MIN_LEN <= end - start <= MAX_LEN:
            continue
        if kind not in best or abs(d) < best[kind][0]:
            best[kind] = (abs(d), start, end)
    return [_segment(kind, best[kind][1], best[kind][2], prefs, duration)
            for kind in kinds if kind in best]


def _positive_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def aniskip_times(mal_id, episode):
    """AniSkip's results for one episode, cached. [] when it has none;
    None when it couldn't be asked or answered nonsense (not cached, tried
    again next time). Sends only the two numbers in the URL."""
    if not _positive_int(mal_id) or not _positive_int(episode):
        return None
    key = f"aniskip:{mal_id}:{episode}"
    cached = database.cache_get(key, FOUND_TTL)
    if isinstance(cached, list) and (cached or database.cache_get(key, MISSING_TTL) is not None):
        return cached
    try:
        req = Request(ANISKIP_URL % (mal_id, episode),
                      headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"})
        with urlopen(req, timeout=10) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        data = json.loads(raw)
    except HTTPError as e:
        # 404 is AniSkip's "no times for this episode".
        if e.code != 404:
            return None
        data = {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    found = data.get("results")
    results = [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []
    database.cache_put(key, results)
    return results


MAL_QUERY = "query($id:Int){Media(id:$id,type:ANIME){idMal}}"


def mal_id(media_id):
    """The MyAnimeList id AniSkip is keyed by, from the stored AniList entry;
    entries stored before it was asked for get it from one small AniList
    request, kept. None when AniList has none."""
    from . import anilist, franchise
    m = franchise.media_entry(media_id)
    if isinstance(m, dict) and "idMal" in m:
        return m["idMal"] if _positive_int(m["idMal"]) else None
    key = f"idmal:{media_id}"
    cached = database.cache_get(key, 365 * 86400)
    if isinstance(cached, dict):
        return cached["idMal"] if _positive_int(cached.get("idMal")) else None
    try:
        m = (anilist.anilist_request({"query": MAL_QUERY, "variables": {"id": media_id}}).get("data") or {}).get("Media")
    except Exception:
        return None
    if not isinstance(m, dict):
        return None
    database.cache_put(key, {"idMal": m.get("idMal")})
    return m["idMal"] if _positive_int(m.get("idMal")) else None
