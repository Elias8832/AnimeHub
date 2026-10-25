"""The Journal's AniList side (Elias, 2026-10-24): your AniList list
activity — "watched episode 10 - 13 of …", "completed …", with when — so
the Journal shows what you watched anywhere, not only on this device.

The activity log is copied into `DATA/anilist_activity.json` and kept up to
date in the background: new pages until a known activity shows up, then
older pages a few at a time until the start of the log (all time). Every
read answers from the copy at once and only starts that work. Public data
(no token needed); requests go through the shared limiter as background
work. No account → the Journal keeps the watch history on this device.
"""

import json
import os
import threading
import time

from . import anilist, util
from .config import config


PER_PAGE = 50
NEW_PAGES = 6        # newest pages per refresh, until a known activity shows up
BACK_PAGES = 4       # older pages per refresh, until the start of the log
FRESH = 120          # seconds a refresh holds
MEDIA_FIELDS = "id title{english romaji} format episodes duration genres coverImage{extraLarge color} bannerImage"
USER_QUERY = "query($name:String){User(name:$name){id}}"
PAGE_QUERY = ("query($uid:Int,$page:Int,$before:Int){Page(page:$page,perPage:%d){pageInfo{hasNextPage}"
              " activities(userId:$uid,type:ANIME_LIST,sort:ID_DESC,createdAt_lesser:$before){"
              " ... on ListActivity{id createdAt status progress media{%s}}}}}" % (PER_PAGE, MEDIA_FIELDS))

_lock = threading.Lock()
_running = False


def _path():
    return config.DATA / "anilist_activity.json"


def _read():
    try:
        return json.loads(_path().read_text())
    except Exception:
        return None


def _write(log):
    _path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(log))
    os.replace(tmp, _path())


def episodes_of(progress):
    """"10 - 13" → (10, 13); "5" → (5, 5); None → (None, None)."""
    if not progress:
        return None, None
    parts = [p.strip() for p in str(progress).split("-")]
    try:
        a, b = int(parts[0]), int(parts[-1])
    except ValueError:
        return None, None
    return (a, b) if a <= b else (b, a)


def compact(act):
    """One activity as the Journal needs it, with its media kept apart."""
    m = act.get("media") or {}
    a, b = episodes_of(act.get("progress"))
    return {"id": act["id"], "at": act.get("createdAt"), "status": act.get("status"), "from": a, "to": b, "mid": m.get("id")}


def media_of(m):
    t = m.get("title") or {}
    return {"id": m["id"], "title": t.get("english") or t.get("romaji"), "format": m.get("format"),
            "episodes": m.get("episodes"), "duration": m.get("duration"), "genres": m.get("genres") or [],
            "poster": (m.get("coverImage") or {}).get("extraLarge"), "color": (m.get("coverImage") or {}).get("color"),
            "banner": m.get("bannerImage")}


def merge_page(log, acts):
    """Add one page of activities; returns how many were new."""
    known = {x["id"] for x in log["items"]}
    added = 0
    for act in acts:
        if not act or "id" not in act or act["id"] in known or not act.get("media"):
            continue
        log["items"].append(compact(act))
        log["media"][str(act["media"]["id"])] = media_of(act["media"])
        known.add(act["id"])
        added += 1
    log["items"].sort(key=lambda x: -x["id"])
    return added


def _page(uid, page=1, before=None):
    variables = {"uid": uid, "page": page}
    if before:
        variables["before"] = before
    res = anilist.anilist_request({"query": PAGE_QUERY, "variables": variables})
    if res.get("errors") and not res.get("data"):
        raise RuntimeError(anilist._graphql_error_msg(res))
    p = (res.get("data") or {}).get("Page") or {}
    return p.get("activities") or [], bool((p.get("pageInfo") or {}).get("hasNextPage"))


@anilist.background_work
def refresh(name):
    """Bring the copy up to date: the newest pages, then a few older ones."""
    global _running
    try:
        log = _read()
        if not log or log.get("user") != name:
            res = anilist.anilist_request({"query": USER_QUERY, "variables": {"name": name}})
            uid = (((res.get("data") or {}).get("User")) or {}).get("id")
            if not uid:
                raise RuntimeError("AniList doesn't know the user %s" % name)
            log = {"user": name, "uid": uid, "items": [], "media": {}, "complete": False, "ts": 0}
        for page in range(1, NEW_PAGES + 1):
            acts, more = _page(log["uid"], page)
            new = merge_page(log, acts)
            if not more:
                log["complete"] = True
            if new < len(acts) or not more:
                break  # reached what we had
        for _ in range(BACK_PAGES):
            if log["complete"] or not log["items"]:
                break
            acts, more = _page(log["uid"], before=log["items"][-1]["at"])  # older than the oldest we have
            merge_page(log, acts)
            if not more or not acts:
                log["complete"] = True
        log["ts"] = time.time()
        _write(log)
    except Exception as e:
        util.log("AniList activity not refreshed: %s" % e)
    finally:
        with _lock:
            _running = False


def _start(name):
    global _running
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=refresh, args=(name,), daemon=True).start()


def journal(since=0):
    """GET /api/journal: {state none/loading/ready, complete, syncing, items,
    media}. `items` are the activities from `since` (unix seconds) on,
    newest first; `media` holds each one's show once."""
    name = anilist.load_anilist_username()
    if name is None:
        return {"state": "none", "items": [], "media": {}}
    log = _read()
    stale = not log or log.get("user") != name or time.time() - log.get("ts", 0) > FRESH or not log.get("complete")
    if stale:
        _start(name)
    if not log or log.get("user") != name:
        return {"state": "loading", "items": [], "media": {}, "complete": False, "syncing": True}
    items = [x for x in log["items"] if (x.get("at") or 0) >= since]
    mids = {str(x["mid"]) for x in items}
    return {"state": "ready", "complete": bool(log.get("complete")), "syncing": _running,
            "items": items, "media": {k: v for k, v in log["media"].items() if k in mids}}
