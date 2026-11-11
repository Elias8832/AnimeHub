"""Remember the season before (TODO 133, Decision 85).

Pressing Play on the first episode of a season whose season before you
finished a while ago opens a popup: when you finished it, your score, its
last episode, AniList's recap entries and its characters. Only real data:
the finish date is your list's or your own watch marks', never guessed.
"""

import datetime
import json

from . import anilist, database, franchise, library, mapping, pages, settings

# How long after finishing the season before the popup shows, in months.
AFTER_CHOICES = ("1", "2", "3", "6", "12")
AFTER_DEFAULT = "2"


def after_months():
    v = settings.get_setting("recap_after")
    return int(v if v in AFTER_CHOICES else AFTER_DEFAULT)


def _done():
    try:
        return set(json.loads(settings.get_setting("recap_done") or "[]"))
    except (TypeError, ValueError):
        return set()


def mark_done(media_id):
    """The popup was answered for this entry: it doesn't come back for it."""
    done = _done() | {int(media_id)}
    settings.set_setting("recap_done", json.dumps(sorted(done)))


def _list_state(mid):
    """{status, score, score_max, progress, completed_at} from the list in
    use (cached, never the network), else the local overlay without one."""
    if anilist.account_present():
        st = anilist.entry_state_from_lists(mid)
        if not st:
            return {}
        e = st["entry"]
        return {"status": e.get("status"), "score": e.get("score"), "score_max": st.get("score_max") or 10,
                "progress": e.get("progress") or 0, "completed_at": e.get("completed_at")}
    c = library.curation_get("aid:%d" % mid) or {}
    return {"status": c.get("status"), "score": c.get("score"), "score_max": 10, "progress": 0,
            "completed_at": c.get("finished_at")}


def _date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def finished_on(entry, state):
    """When you finished an entry: your list's finish date, or the day its
    last episode was marked watched here when every episode is — the later
    of the two (a rewatch here counts). None when neither says it."""
    days = []
    if state.get("status") in ("COMPLETED", "REPEATING") and _date(state.get("completed_at")):
        days.append(_date(state["completed_at"]))
    slots = [x for x in entry["slots"] if isinstance(x["number"], int) and x["number"] >= 1]
    if slots and all(x["watched"] for x in slots) and len(slots) >= (entry.get("episodes") or 0):
        seen = [_date(x["last_played"]) for x in slots if _date(x["last_played"])]
        if seen:
            days.append(max(seen))
    return max(days) if days else None


def months_between(a, b):
    """Whole months from date a to date b."""
    return (b.year - a.year) * 12 + b.month - a.month - (b.day < a.day)


def _episode(ep_id):
    """(AniList id, episode number) of a file or online episode, or None."""
    if ep_id.startswith("stream:"):
        try:
            _, mid, n = ep_id.split(":", 2)
            return int(mid), int(n)
        except ValueError:
            return None
    scan = library.scan_library()
    library.merge_db(scan)
    for s in scan:
        for u in mapping.release_units(s):
            for ep, n in u["eps"]:
                if ep["id"] == ep_id:
                    return u["media_id"], n
    return None


def _recaps(prev_raw, page):
    """AniList's recap entries of the season (its SUMMARY links) that are
    on the page: what to play, and whether you watched it."""
    ids = [(e.get("node") or {}).get("id") for e in mapping._edges(prev_raw)
           if e.get("relationType") == "SUMMARY" and (e.get("node") or {}).get("type", "ANIME") == "ANIME"]
    by_id = {e["id"]: e for e in page["entries"]}
    out = []
    for rid in ids:
        e = by_id.get(rid)
        if not e:
            continue
        slots = [x for x in e["slots"] if isinstance(x["number"], int)]
        watched = (bool(slots) and all(x["watched"] for x in slots)) or \
            _list_state(rid).get("status") in ("COMPLETED", "REPEATING")
        out.append({"id": rid, "title": e["title"], "format": e["format"], "format_label": e["format_label"],
                    "episodes": e["episodes"], "duration": e["duration"], "poster": e["poster"],
                    "play": slots[0]["id"] if slots else None, "watched": watched})
    return out


def for_episode(ep_id, today=None):
    """GET /api/recap?id=<episode>: {"show": False} or the popup's data.
    Shows only for the first episode of a main season after the first,
    that season not started, the season before finished `after_months()`
    or more ago, and the popup on and not answered for it yet."""
    no = {"show": False}
    if not settings.automation_enabled("recap_ask"):
        return no
    where = _episode(ep_id)
    if not where or where[1] != 1:
        return no
    mid = where[0]
    if mid in _done():
        return no
    fr = franchise.franchise_of(mid)
    if not fr:
        return no
    page = pages.franchise_view(mid, background=False)
    entry = next((e for e in page["entries"] if e["id"] == mid), None)
    if not entry or entry["group"] != "main" or (entry["season"] or 0) < 2:
        return no
    prev = next((e for e in page["entries"] if e["group"] == "main" and e["season"] == entry["season"] - 1), None)
    if not prev:
        return no
    if any(x["watched"] or x["position"] for x in entry["slots"]) or _list_state(mid).get("progress"):
        return no
    state = _list_state(prev["id"])
    day = finished_on(prev, state)
    today = today or datetime.date.today()
    if not day or months_between(day, today) < after_months():
        return no
    slots = [x for x in prev["slots"] if isinstance(x["number"], int) and x["number"] >= 1]
    last = max(slots, key=lambda x: x["number"]) if slots else None
    prev_raw = next((m for m in fr[1] if m["id"] == prev["id"]), {})
    score = state.get("score") or None
    return {"show": True,
            "entry": {"id": mid, "label": entry["label"]},
            "prev": {"id": prev["id"], "label": prev["label"], "title": prev["title"], "banner": prev["banner"],
                     "poster": prev["poster"], "finished": day.isoformat(), "months": months_between(day, today),
                     "score": score, "score_max": state.get("score_max") or 10},
            "last": {"id": last["id"], "number": last["number"], "title": last["title"], "thumb": last["thumb"],
                     "episodes": len(slots)} if last else None,
            "recaps": _recaps(prev_raw, page),
            "franchise": page["title"]}
