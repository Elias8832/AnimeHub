"""The airing calendar's data (TODO 106): AniList's published schedule for
what's airing or announced on your lists, Dropped and Completed left out.

Only dates AniList states: every scheduled episode with its time, and the
exact start day of a show not out yet (nothing for one with only a month or
a year). The list cache knows one next episode per show, so this asks
AniList as background work under the shared limiter; the result is kept for
six hours, or until the shows change. A stale set is served while a fresh
one builds.
"""

import hashlib
import json
import threading
import time

from . import anilist, system, util
from .config import config

TTL = 6 * 3600
BACK = 92 * 86400   # how far the calendar pages back
CHUNK = 50          # ids per request
MAX_PAGES = 30      # requests per chunk at most
ACTIVE = ("RELEASING", "NOT_YET_RELEASED", "HIATUS")
AIRINGS = """query($ids:[Int],$page:Int,$after:Int){ Page(page:$page,perPage:50){ pageInfo{hasNextPage}
airingSchedules(mediaId_in:$ids,airingAt_greater:$after,sort:TIME){ mediaId episode airingAt } } }"""
STARTS = """query($ids:[Int]){ Page(perPage:50){ media(id_in:$ids,status:NOT_YET_RELEASED,type:ANIME){
id startDate{year month day} } } }"""

_lock = threading.Lock()
_building = False


def _cache_path():
    return config.DATA / "schedule.json"


def shows(data):
    return sorted(e["id"] for e in data.get("entries") or []
                  if e.get("airing") in ACTIVE and e.get("status") not in ("DROPPED", "COMPLETED"))


def _key(ids):
    return hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:16]


def _ask(query, variables):
    res = anilist.anilist_request({"query": query, "variables": variables})
    if res.get("errors") and not res.get("data"):
        raise RuntimeError(anilist._graphql_error_msg(res))
    return (res.get("data") or {}).get("Page") or {}


def merge(airings, starts):
    """AniList's answers → calendar items (pure). A start day is dropped
    when the schedule already holds that show's first episode."""
    items = [{"id": a["mediaId"], "ep": a["episode"], "at": a["airingAt"]} for a in airings]
    scheduled = {a["mediaId"] for a in airings if a.get("episode") == 1}
    for m in starts:
        d = m.get("startDate") or {}
        if m["id"] not in scheduled and d.get("year") and d.get("month") and d.get("day"):
            items.append({"id": m["id"], "date": "%04d-%02d-%02d" % (d["year"], d["month"], d["day"])})
    return items


def _write(payload):
    _cache_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    system.replace(tmp, _cache_path())


def _read():
    try:
        return json.loads(system.read_bytes(_cache_path()))
    except Exception:
        return None


@anilist.background_work
def _build(ids, key):
    global _building
    try:
        airings, starts, after = [], [], int(time.time()) - BACK
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            for page in range(1, MAX_PAGES + 1):
                got = _ask(AIRINGS, {"ids": part, "page": page, "after": after})
                airings += got.get("airingSchedules") or []
                if not (got.get("pageInfo") or {}).get("hasNextPage"):
                    break
            starts += _ask(STARTS, {"ids": part}).get("media") or []
        _write({"key": key, "ts": time.time(), "items": merge(airings, starts)})
    except Exception as e:
        util.log("Airing calendar not built: %s" % e)
    finally:
        with _lock:
            _building = False


def _start(ids, key):
    global _building
    with _lock:
        if _building:
            return
        _building = True
    threading.Thread(target=_build, args=(ids, key), daemon=True).start()


def schedule():
    """GET /api/schedule: {state, items}. state "none" (no account or nothing
    airing), "loading" (first build running), "ready". Each item is
    {id, ep, at} or, for a show's start day, {id, date}."""
    if anilist.load_anilist_username() is None:
        return {"state": "none", "items": []}
    data = anilist.anilist_cache_stale()
    if not data:
        return {"state": "loading", "items": []}
    ids = shows(data)
    if not ids:
        return {"state": "none", "items": []}
    key, cached = _key(ids), _read()
    if not (cached and cached.get("key") == key and time.time() - cached.get("ts", 0) < TTL):
        _start(ids, key)
    if not cached:
        return {"state": "loading", "items": []}
    return {"state": "ready", "items": cached.get("items") or []}
