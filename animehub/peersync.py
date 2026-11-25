"""Sync between the Android app and a PC (Decision 91).

The app pairs once with the PC's phone remote (Decision 43: its address and
6-digit code), then, whenever the two reach each other (home Wi-Fi or
Tailscale), the app's hub asks the PC for its facts, merges them with its own
and sends back what the PC should change. The PC only answers; it never
calls the phone.

What travels, all keyed so it means the same on both devices:
- **Episodes** as "<AniList id>:<episode>": watched, stop point, length, when
  last played. That is ticks, resume spots, Continue watching and History.
  A file and its online twin are one slot. Shows matched to no AniList entry
  stay on their device.
- **Playlists** by their `uid`; the list changed last wins whole. Items of
  shows matched to nothing ("sid:") stay on their device.
- **Your own list entries** (the curation overlay, "aid:" refs): the newer wins.

Episodes merge three-way: the app keeps what both sides agreed on last time
(`snapshot`), so a side that changed wins (an unticked episode stays
unticked); when both changed, the one further along wins (watched beats not
watched, the later play gives the stop point). Each write checks the slot is
still what the merge saw, so something played meanwhile is never overwritten;
the next sync picks it up. Nothing here talks to AniList: each device sends
its own watching there, as before."""

import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from . import VERSION as APP_VERSION, anicli, database, library, mapping, streaming, system, util

VERSION = 1
EVERY = 120          # seconds between syncs while nothing else asks
SETTLE = 1           # a burst of changes syncs once
TIMEOUT = 20
EMPTY = [False, 0.0, 0.0, None]
UA = f"AnimeHub-App/{APP_VERSION} (sync)"
LIST_FIELDS = ("name", "color", "note", "icon", "pinned", "head", "view", "dim", "pos")
CUR_FIELDS = ("status", "score", "favourite", "rewatches", "started_at", "finished_at", "prefs")

_run_lock = threading.Lock()   # one sync at a time
_wake = threading.Event()
_state = {"running": False, "stamp": 0, "last": None}

# Near-instant sync (Decision 99). On the PC: a count of its own changes a
# paired app cares about; GET /api/sync/wait holds until it moves (or
# WAIT seconds pass). The app keeps one such request open while its page is
# in front and syncs the moment the PC answers with a new count.
WAIT = 15            # seconds the PC holds a wait (under TIMEOUT)
_touch = {"n": 0, "boot": os.urandom(4).hex()}
_touch_cv = threading.Condition()
_watch = {"away": False, "wake": threading.Event()}


class SyncError(Exception):
    """A reason the user can read."""


# --- this device's facts ----------------------------------------------------

def _stream_slot(eid):
    """"stream:<id>:<n>" -> "<id>:<n>" for whole episode numbers from 1."""
    try:
        _, mid, token = eid.split(":", 2)
        mid, n = int(mid), float(token)
    except ValueError:
        return None
    return f"{mid}:{int(n)}" if n.is_integer() and n >= 1 else None


def slot_map():
    """{"<AniList id>:<n>": [episode ids]}: every file mapped to an entry
    (release_units, only units that fit their entry) and every online row."""
    scan = library.scan_library()
    library.merge_db(scan)
    slots = {}
    for s in scan:
        for u in mapping.release_units(s):
            if not mapping.unit_fits(u, u["total"]):
                continue
            for ep, n in u["eps"]:
                slots.setdefault(f"{u['media_id']}:{n}", []).append(ep["id"])
    with database.db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM episodes WHERE id LIKE 'stream:%'")]
    for eid in ids:
        key = _stream_slot(eid)
        if key and eid not in slots.get(key, ()):
            slots.setdefault(key, []).append(eid)
    return slots


def _fact(row):
    return [bool(row[0]), round(float(row[1] or 0), 1), round(float(row[2] or 0), 1), row[3] or None]


def _combine(rows):
    """One slot's facts from its sources' rows: watched when any is; the stop
    point, length and time from the one played last."""
    if not rows:
        return list(EMPTY)
    last = max(rows, key=lambda r: r[3] or "")
    f = _fact(last)
    f[0] = any(bool(r[0]) for r in rows)
    return f


def _meaningful(f):
    return bool(f[0] or f[1] or f[3])


def _rows(c, ids=None):
    q = "SELECT id, watched, position, duration, last_played FROM episodes"
    if ids is None:
        return {r[0]: r[1:] for r in c.execute(q)}
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out.update({r[0]: r[1:] for r in c.execute(q + " WHERE id IN (%s)" % ",".join("?" * len(part)), part)})
    return out


def episode_facts(slots=None):
    """Every slot this device has, untouched ones as EMPTY: a slot missing
    from the answer means "not here" (a deleted file, an unplugged drive),
    which the merge reads as unchanged, never as unwatched."""
    slots = slot_map() if slots is None else slots
    with database.db() as c:
        rows = _rows(c)
    out = {}
    for key, ids in slots.items():
        f = _combine([rows[i] for i in ids if i in rows])
        out[key] = f if _meaningful(f) else list(EMPTY)
    return out


def apply_episodes(changes, slots=None):
    """changes: {slot: [was, now]}. Writes `now` to every source of each slot
    whose facts are still `was` (an online row "stream:<id>:<n>" when the
    slot has none). Returns the slots written."""
    slots = slot_map() if slots is None else slots
    done = []
    with database.db() as c:
        for key, (was, now) in changes.items():
            ids = list(slots.get(key) or [])
            if not ids:
                mid, n = key.split(":")
                ids = [f"stream:{mid}:{n}"]
            if not slots.get(key) and not _meaningful(now):
                continue  # nothing to keep for an episode this device never had
            rows = _rows(c, ids)
            cur = _combine([rows[i] for i in ids if i in rows])
            if (cur if _meaningful(cur) else EMPTY) != (was if _meaningful(was) else EMPTY):
                continue
            c.executemany("""INSERT INTO episodes(id, watched, position, duration, last_played) VALUES(?,?,?,?,?)
                             ON CONFLICT(id) DO UPDATE SET watched=excluded.watched, position=excluded.position,
                             duration=excluded.duration, last_played=excluded.last_played""",
                          [(i, int(bool(now[0])), now[1], now[2], now[3]) for i in ids])
            done.append(key)
    if done:
        changed()
    return done


# --- playlists and list entries ---------------------------------------------

def playlist_facts():
    with database.db() as c:
        lists = c.execute("SELECT id, uid, changed, name, color, note, icon, pinned, head, view, dim, pos "
                          "FROM playlists ORDER BY pos, id").fetchall()
        items = c.execute("SELECT playlist_id, ref, title, poster FROM playlist_items "
                          "ORDER BY playlist_id, pos").fetchall()
        gone = dict(c.execute("SELECT key, ts FROM sync_gone WHERE kind='playlist'").fetchall())
    by_list = {}
    for pid, ref, title, poster in items:
        if ref.startswith("aid:"):  # a show matched to nothing means nothing elsewhere
            by_list.setdefault(pid, []).append({"ref": ref, "title": title, "poster": poster})
    out = {}
    for pid, uid, ch, *rest in lists:
        if uid:
            out[uid] = {"changed": ch or "", **dict(zip(LIST_FIELDS, rest)), "items": by_list.get(pid, [])}
    return {"lists": out, "gone": gone}


def apply_playlists(lists, gone):
    """Write lists ({uid: list}) as they are, keeping this device's own
    "sid:" items at their end, and delete the gone ones ({uid: when})."""
    if not lists and not gone:
        return
    with database.db() as c:
        for uid, ts in gone.items():
            c.execute("INSERT INTO sync_gone(kind, key, ts) VALUES('playlist', ?, ?) "
                      "ON CONFLICT(kind, key) DO UPDATE SET ts=MAX(ts, excluded.ts)", (uid, ts))
            row = c.execute("SELECT id FROM playlists WHERE uid=?", (uid,)).fetchone()
            if row:
                c.execute("DELETE FROM playlist_items WHERE playlist_id=?", (row[0],))
                c.execute("DELETE FROM playlists WHERE id=?", (row[0],))
        for uid, pl in lists.items():
            c.execute("DELETE FROM sync_gone WHERE kind='playlist' AND key=?", (uid,))
            row = c.execute("SELECT id FROM playlists WHERE uid=?", (uid,)).fetchone()
            if row:
                pid = row[0]
            else:
                pid = c.execute("INSERT INTO playlists(name, uid) VALUES(?, ?)", (pl["name"], uid)).lastrowid
            vals = [pl.get(k) for k in LIST_FIELDS]
            c.execute("UPDATE playlists SET %s, changed=? WHERE id=?" % ", ".join(f"{k}=?" for k in LIST_FIELDS),
                      (*vals, pl["changed"], pid))
            old = c.execute("SELECT ref, title, poster FROM playlist_items WHERE playlist_id=? ORDER BY pos",
                            (pid,)).fetchall()
            posters = {r: p for r, _, p in old}
            keep = [(r, t, p) for r, t, p in old if not r.startswith("aid:")]
            rows, seen = [], set()
            for it in pl["items"]:
                ref, poster = it["ref"], it.get("poster")
                if ref in seen:
                    continue
                seen.add(ref)
                if not (poster and re.match(r"^https?://", poster)):
                    poster = posters.get(ref)  # the other device's own picture path means nothing here
                rows.append((ref, it["title"], poster))
            c.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
            c.executemany("INSERT INTO playlist_items(playlist_id, ref, pos, title, poster) VALUES(?,?,?,?,?)",
                          [(pid, r, i, t, p) for i, (r, t, p) in enumerate(rows + keep)])
    changed()


def curation_facts():
    with database.db() as c:
        rows = c.execute("SELECT ref, updated_at, status, score, favourite, rewatches, started_at, finished_at, prefs "
                         "FROM curation WHERE ref LIKE 'aid:%'").fetchall()
    return {r[0]: {"updated_at": r[1] or "", **dict(zip(CUR_FIELDS, r[2:]))} for r in rows}


def apply_curation(rows):
    if not rows:
        return
    cols = ("ref", "updated_at") + CUR_FIELDS
    with database.db() as c:
        c.executemany("INSERT INTO curation(%s) VALUES(%s) ON CONFLICT(ref) DO UPDATE SET %s" % (
            ",".join(cols), ",".join("?" * len(cols)), ", ".join(f"{k}=excluded.{k}" for k in cols[1:])),
            [(ref, r["updated_at"], *(r.get(k) for k in CUR_FIELDS)) for ref, r in rows.items()])
    changed()


def drop_curation(refs):
    """Remove "aid:<id>" list entries (first sync with "Use the phone's")."""
    if not refs:
        return
    with database.db() as c:
        n = c.executemany("DELETE FROM curation WHERE ref=?", [(r,) for r in refs]).rowcount
    if n:
        changed()


# --- rewatch runs -------------------------------------------------------------

def rewatch_facts():
    with database.db() as c:
        rows = c.execute("SELECT ref, started_at, done, updated_at FROM rewatches"
                         " WHERE ref LIKE 'aid:%'").fetchall()
    out = {}
    for ref, started_at, done_s, updated_at in rows:
        try:
            done = json.loads(done_s) if done_s else []
        except ValueError:
            done = []
        out[ref] = {"started_at": started_at, "done": done, "updated_at": updated_at or ""}
    return out


def check_rewatches(v):
    """True when v isn't {aid:<id>: {started_at, done, updated_at}}."""
    if not isinstance(v, dict):
        return True
    for ref, row in v.items():
        if not (isinstance(ref, str) and AID_RE.match(ref) and isinstance(row, dict)):
            return True
        st, done, upd = row.get("started_at"), row.get("done"), row.get("updated_at")
        if st is not None and not (isinstance(st, str) and len(st) <= 32):
            return True
        if not (isinstance(upd, str) and len(upd) <= 32):
            return True
        if not (isinstance(done, list) and len(done) <= 2000 and all(
                isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0 for n in done)):
            return True
    return False


def merge_rewatches(mine, theirs):
    for_me = {r: v for r, v in theirs.items() if r not in mine or v["updated_at"] > mine[r]["updated_at"]}
    for_them = {r: v for r, v in mine.items() if r not in theirs or v["updated_at"] > theirs[r]["updated_at"]}
    return for_me, for_them


def apply_rewatches(rows):
    if not rows:
        return
    with database.db() as c:
        c.executemany(
            """INSERT INTO rewatches(ref, started_at, done, updated_at) VALUES(?,?,?,?)
               ON CONFLICT(ref) DO UPDATE SET started_at=excluded.started_at,
               done=excluded.done, updated_at=excluded.updated_at""",
            [(ref, r.get("started_at"), json.dumps(r.get("done") or []), r["updated_at"])
             for ref, r in rows.items()],
        )
    changed()


def drop_rewatches(refs):
    """Remove "aid:<id>" rewatch runs (first sync with "Use the phone's")."""
    if not refs:
        return
    with database.db() as c:
        n = c.executemany("DELETE FROM rewatches WHERE ref=?", [(r,) for r in refs]).rowcount
    if n:
        changed()


# --- "Remove from Home" -----------------------------------------------------
# Home's Continue watching keeps a show's removal by show id (Decision 81):
# "aid:<id>" for an online show, a folder's own id for files. Between devices
# it travels by AniList id: "hidden" {id: when removed}, "shown" {id: when an
# Undo put it back}. The later of the two wins; an Undo wins a tie.

AID_RE = re.compile(r"^aid:([1-9][0-9]{0,9})$")


def _show_mids():
    """{folder show id: {AniList ids}} for this device's files."""
    scan = library.scan_library()
    library.merge_db(scan)
    return {s["id"]: {u["media_id"] for u in mapping.release_units(s)} for s in scan}


def _by_mid(rows, folders):
    """{show id or "aid:<id>": when} -> {"<AniList id>": the latest when}."""
    out = {}
    for key, ts in rows:
        m = AID_RE.match(key)
        for mid in ({int(m.group(1))} if m else folders.get(key, ())):
            out[str(mid)] = max(ts, out.get(str(mid), ""))
    return out


def hidden_facts():
    with database.db() as c:
        hid = c.execute("SELECT show_id, at FROM continue_hidden").fetchall()
        shown = c.execute("SELECT key, ts FROM sync_gone WHERE kind='continue'").fetchall()
    folders = _show_mids() if any(not AID_RE.match(k) for k, _ in hid + shown) else {}
    return {"hidden": _by_mid(hid, folders), "shown": _by_mid(shown, folders)}


def _home_state(facts, mid):
    """(when, 1 shown / 0 hidden) or None."""
    h, s = facts["hidden"].get(mid), facts["shown"].get(mid)
    if s and (not h or s >= h):
        return (s, 1)
    return (h, 0) if h else None


def merge_hidden(mine, theirs):
    """(for me, for them), each {"hidden": {}, "shown": {}}: the later state
    of each show goes to the side that lacks it."""
    for_me, for_them = {"hidden": {}, "shown": {}}, {"hidden": {}, "shown": {}}
    for mid in set(mine["hidden"]) | set(mine["shown"]) | set(theirs["hidden"]) | set(theirs["shown"]):
        m, t = _home_state(mine, mid), _home_state(theirs, mid)
        win = max(x for x in (m, t) if x)
        for side, have in ((for_me, m), (for_them, t)):
            if have != win:
                side["shown" if win[1] else "hidden"][mid] = win[0]
    return for_me, for_them


def apply_hidden(got):
    """Write the other side's removals (and Undos) to this device's shows:
    the online one ("aid:<id>") and every folder holding that entry."""
    if not got or not (got.get("hidden") or got.get("shown")):
        return
    folders = _show_mids()
    owners = {}
    for sid, mids in folders.items():
        for mid in mids:
            owners.setdefault(str(mid), []).append(sid)
    with database.db() as c:
        for mid, at in got.get("hidden", {}).items():
            for sid in [f"aid:{mid}"] + owners.get(mid, []):
                c.execute("INSERT INTO continue_hidden(show_id, at) VALUES(?,?) "
                          "ON CONFLICT(show_id) DO UPDATE SET at=MAX(at, excluded.at)", (sid, at))
                c.execute("DELETE FROM sync_gone WHERE kind='continue' AND key=? AND ts<?", (sid, at))
        for mid, ts in got.get("shown", {}).items():
            c.execute("INSERT INTO sync_gone(kind, key, ts) VALUES('continue', ?, ?) "
                      "ON CONFLICT(kind, key) DO UPDATE SET ts=MAX(ts, excluded.ts)", (f"aid:{mid}", ts))
            for sid in [f"aid:{mid}"] + owners.get(mid, []):
                c.execute("DELETE FROM continue_hidden WHERE show_id=? AND at<=?", (sid, ts))
    changed()


# --- what the PC answers ----------------------------------------------------

def facts_payload():
    """GET /api/sync/facts."""
    # every_slot (additive): "episodes" lists every slot, so a missing one
    # is "not here", not "unwatched". An older PC's answer lacks it.
    # home_hidden (additive): "Remove from Home"; an older PC lacks it.
    # rewatches (additive): active/ended rewatch runs; an older PC lacks it.
    return {"version": VERSION, "name": socket.gethostname(), "episodes": episode_facts(),
            "every_slot": True, "playlists": playlist_facts(), "curation": curation_facts(),
            "home_hidden": hidden_facts(), "rewatches": rewatch_facts()}


def _check_fact(f):
    return (isinstance(f, list) and len(f) == 4 and isinstance(f[0], bool)
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in f[1:3])
            and (f[3] is None or isinstance(f[3], str) and len(f[3]) <= 32))


SLOT_RE = re.compile(r"^[1-9][0-9]{0,9}:[1-9][0-9]{0,5}$")
UID_RE = re.compile(r"^[0-9a-f]{16}$")


def _scalar(v):
    return v is None or isinstance(v, (str, int, float, bool)) and (not isinstance(v, str) or len(v) <= 4000)


def check_apply(body):
    """The shape of POST /api/sync/apply, or a reason it's wrong."""
    eps = body.get("episodes", {})
    if not isinstance(eps, dict) or not all(
            isinstance(k, str) and SLOT_RE.match(k) and isinstance(v, list) and len(v) == 2
            and all(_check_fact(f) for f in v) for k, v in eps.items()):
        return "'episodes' must map '<id>:<n>' to [was, now]"
    lists = body.get("playlists", {})
    if not isinstance(lists, dict) or not all(
            isinstance(k, str) and UID_RE.match(k) and isinstance(v, dict) and isinstance(v.get("name"), str)
            and isinstance(v.get("changed"), str) and isinstance(v.get("items"), list)
            and all(_scalar(v.get(f)) for f in LIST_FIELDS)
            and all(isinstance(i, dict) and isinstance(i.get("ref"), str) and i["ref"].startswith("aid:")
                    and isinstance(i.get("title"), str) and (i.get("poster") is None or isinstance(i["poster"], str))
                    for i in v["items"])
            for k, v in lists.items()):
        return "'playlists' must map a list's uid to the list"
    gone = body.get("gone", {})
    if not isinstance(gone, dict) or not all(isinstance(k, str) and UID_RE.match(k) and isinstance(v, str)
                                             for k, v in gone.items()):
        return "'gone' must map a list's uid to when it went"
    cur = body.get("curation", {})
    if not isinstance(cur, dict) or not all(isinstance(k, str) and re.match(r"^aid:[0-9]+$", k)
                                            and isinstance(v, dict) and isinstance(v.get("updated_at"), str)
                                            and all(_scalar(v.get(f)) for f in CUR_FIELDS)
                                            for k, v in cur.items()):
        return "'curation' must map 'aid:<id>' to an entry"
    drop_cur = body.get("drop_curation", [])
    if not isinstance(drop_cur, list) or not all(isinstance(r, str) and re.match(r"^aid:[0-9]+$", r) for r in drop_cur):
        return "'drop_curation' must be a list of 'aid:<id>' refs"
    if check_hidden(body.get("home_hidden", {"hidden": {}, "shown": {}})):
        return "'home_hidden' must hold 'hidden' and 'shown', each mapping an AniList id to when"
    if check_rewatches(body.get("rewatches", {})):
        return "'rewatches' must map 'aid:<id>' to {started_at, done, updated_at}"
    drop_rw = body.get("drop_rewatches", [])
    if not isinstance(drop_rw, list) or not all(isinstance(r, str) and AID_RE.match(r) for r in drop_rw):
        return "'drop_rewatches' must be a list of 'aid:<id>' refs"
    return None


def check_hidden(v):
    """True when v isn't {"hidden": {id: when}, "shown": {id: when}}."""
    return not (isinstance(v, dict) and all(
        isinstance(v.get(k, {}), dict) and all(
            isinstance(mid, str) and re.match(r"^[1-9][0-9]{0,9}$", mid) and isinstance(ts, str) and len(ts) <= 32
            for mid, ts in v.get(k, {}).items())
        for k in ("hidden", "shown")))


def apply_payload(body):
    """POST /api/sync/apply: the app's merge result for this device."""
    done = apply_episodes(body.get("episodes") or {})
    apply_playlists(body.get("playlists") or {}, body.get("gone") or {})
    apply_curation(body.get("curation") or {})
    drop_curation(body.get("drop_curation") or [])
    apply_hidden(body.get("home_hidden"))
    apply_rewatches(body.get("rewatches") or {})
    drop_rewatches(body.get("drop_rewatches") or [])
    return {"ok": True, "applied": done}


# --- merging ------------------------------------------------------------------

def further(a, b):
    """Both sides changed one slot: the one played later gives the stop point
    and time; watched on either side stays watched."""
    newer = a if (a[3] or "") >= (b[3] or "") else b
    return [bool(a[0] or b[0]), newer[1], newer[2], newer[3]]


def _side(facts, every_slot, other, base, key):
    """One side's value for a slot. A side that lists every slot and lacks
    this one doesn't have it (a deleted file, an unplugged drive): it stands
    as last agreed — or, never synced, as the other side has it — so the
    other side's facts are never wiped by it. An older peer's missing slot
    is EMPTY, as before."""
    if key in facts:
        return facts[key]
    if not every_slot:
        return EMPTY
    return other.get(key, EMPTY) if base is None else base.get(key, EMPTY)


def merge_episodes(mine, theirs, base, theirs_every_slot=True):
    """{slot: result} for every slot either side has. base None: never synced."""
    out = {}
    for key in set(mine) | set(theirs):
        m = _side(mine, True, theirs, base, key)
        t = _side(theirs, theirs_every_slot, mine, base, key)
        b = None if base is None else base.get(key, EMPTY)
        if m == t:
            out[key] = m
        elif b is not None and m == b:
            out[key] = t
        elif b is not None and t == b:
            out[key] = m
        else:
            out[key] = further(m, t)
    return out


def merge_playlists(mine, theirs):
    """(lists for me, gone for me, lists for them, gone for them). A list
    made on both before the first sync under the same name is one list."""
    mine_l, theirs_l = dict(mine["lists"]), theirs["lists"]
    renames = {}
    for uid, pl in list(mine_l.items()):
        if uid in theirs_l:
            continue
        twin = next((u for u, p in theirs_l.items() if p["name"] == pl["name"] and u not in mine_l), None)
        if twin:
            renames[uid] = twin
            mine_l[twin] = mine_l.pop(uid)
    gone = dict(theirs["gone"])
    for uid, ts in mine["gone"].items():
        gone[uid] = max(ts, gone.get(uid, ""))
    for_me, for_them = {}, {}
    gone_me = {u: ts for u, ts in gone.items() if u not in mine["gone"] or mine["gone"][u] < ts}
    gone_them = {u: ts for u, ts in gone.items() if u not in theirs["gone"] or theirs["gone"][u] < ts}
    for uid in set(mine_l) | set(theirs_l):
        m, t = mine_l.get(uid), theirs_l.get(uid)
        newest = max(x["changed"] for x in (m, t) if x)
        if uid in gone and gone[uid] >= newest:
            continue  # deleted after its last change: gone everywhere
        gone_me.pop(uid, None)  # an older delete never takes the list off either side (TODO 276)
        gone_them.pop(uid, None)
        if m and t and m["changed"] == t["changed"] and uid not in renames.values():
            continue  # unchanged since the last sync (pictures may differ: each keeps its own)
        win = t if not m or (t and t["changed"] > m["changed"]) else m
        if win is not m or uid in renames.values():
            for_me[uid] = win
        if win is not t:
            for_them[uid] = win
    return for_me, gone_me, for_them, gone_them, renames


def episode_writes(facts, every_slot, base, result):
    """{slot: [was, now]} to write on one side. A slot it lacks gets a write
    only when the result is new since the last agreement (never synced:
    anything worth keeping) — a deleted file never gets its old facts back
    as an online twin."""
    out = {}
    for key, v in result.items():
        if key in facts:
            if facts[key] != v:
                out[key] = [facts[key], v]
        elif _meaningful(v) and v != (base.get(key, EMPTY) if base is not None and every_slot else EMPTY):
            out[key] = [list(EMPTY), v]
    return out


def take_theirs(mine, theirs):
    """The first sync with "Use the PC's": every slot as the PC has it. A
    slot the PC hasn't touched is cleared here (its length stays)."""
    out = {}
    for key in set(mine) | set(theirs):
        t = theirs.get(key, EMPTY)
        if _meaningful(t):
            out[key] = list(t)
        else:
            m = mine.get(key, EMPTY)
            out[key] = [False, 0.0, m[2], None]
    return out


def since_pairing(lists, hidden, paired):
    """This device's own facts with the marks it made before pairing with
    this PC left out: deletes and Remove from Home / Undo made while synced
    with another PC (a test copy, an old PC) stay on this device and never
    remove anything on this one (TODO 248). Lists themselves still travel."""
    if not paired:
        return lists, hidden
    lists = {"lists": lists["lists"], "gone": {u: ts for u, ts in lists["gone"].items() if ts >= paired}}
    hidden = {k: {m: ts for m, ts in v.items() if ts >= paired} for k, v in hidden.items()}
    return lists, hidden


def take_their_playlists(mine, theirs):
    """(lists for me, gone for me) when overwriting `mine` with `theirs`:
    `theirs`'s lists as they are; `mine`'s other lists go."""
    lists = {u: p for u, p in theirs["lists"].items()
             if u not in mine["lists"] or mine["lists"][u]["changed"] != p["changed"] or u in mine["gone"]}
    gone = {u: ts for u, ts in theirs["gone"].items() if u not in theirs["lists"] and mine["gone"].get(u, "") < ts}
    now = util.utc_stamp()
    for u in mine["lists"]:
        if u not in theirs["lists"]:
            gone[u] = max(now, gone.get(u, ""))
    return lists, gone


def _next_stamp(ts):
    """One second after a canonical UTC timestamp, so apply_hidden's `ts < ?`
    clears a target `sync_gone` row from the same second."""
    try:
        return (datetime.strptime(ts, util.CANONICAL_TS) + timedelta(seconds=1)).strftime(util.CANONICAL_TS)
    except ValueError:
        return ts + "z"


def take_their_hidden(target, source):
    """{"hidden": ..., "shown": ...} to make target's Remove from Home match source."""
    out = {"hidden": {}, "shown": {}}
    now = util.utc_stamp()
    for mid in set(target["hidden"]) | set(target["shown"]) | set(source["hidden"]) | set(source["shown"]):
        s, t = _home_state(source, mid), _home_state(target, mid)
        if s and s[1] == 0:
            if not t or t[0] < s[0]:
                out["hidden"][mid] = s[0]
            elif t[1] == 1:
                out["hidden"][mid] = _next_stamp(t[0])
        elif t and t[1] == 0:
            out["shown"][mid] = max(now, t[0])
    return out if (out["hidden"] or out["shown"]) else {}


def _make_baseline(mine, theirs_eps, my_lists, theirs_lists, own_cur, theirs_cur, my_hidden, theirs_hid,
                   own_rw=None, theirs_rw=None):
    """First sync for "Only from now on": nothing changes on either side.
    Slots that already match go into the normal snapshot; everything that
    differs is recorded per-side in `baseline` so only later changes travel."""
    snap, ep_base = {}, {}
    for key in set(mine) | set(theirs_eps):
        m, t = mine.get(key, EMPTY), theirs_eps.get(key, EMPTY)
        if m == t:
            if _meaningful(m):
                snap[key] = list(m)
        else:
            ep_base[key] = [list(m), list(t)]
    pl_m, pl_t = {}, {}
    ml, tl = my_lists["lists"], theirs_lists["lists"]
    for uid in set(ml) | set(tl):
        m, t = ml.get(uid), tl.get(uid)
        if m and t and m["changed"] == t["changed"]:
            continue
        if m:
            pl_m[uid] = m["changed"]
        if t:
            pl_t[uid] = t["changed"]
    cur_m, cur_t = {}, {}
    for ref in set(own_cur) | set(theirs_cur):
        if own_cur.get(ref) == theirs_cur.get(ref):
            continue
        if ref in own_cur:
            cur_m[ref] = own_cur[ref]["updated_at"]
        if ref in theirs_cur:
            cur_t[ref] = theirs_cur[ref]["updated_at"]
    hid_m, hid_t = {}, {}
    if theirs_hid is not None:
        for mid in set(my_hidden["hidden"]) | set(my_hidden["shown"]) | set(theirs_hid["hidden"]) | set(theirs_hid["shown"]):
            m, t = _home_state(my_hidden, mid), _home_state(theirs_hid, mid)
            if m != t:
                if m:
                    hid_m[mid] = list(m)
                if t:
                    hid_t[mid] = list(t)
    rw_m, rw_t = {}, {}
    if theirs_rw is not None and own_rw is not None:
        for ref in set(own_rw) | set(theirs_rw):
            if own_rw.get(ref) == theirs_rw.get(ref):
                continue
            if ref in own_rw:
                rw_m[ref] = own_rw[ref]["updated_at"]
            if ref in theirs_rw:
                rw_t[ref] = theirs_rw[ref]["updated_at"]
    if not (ep_base or pl_m or pl_t or cur_m or cur_t or hid_m or hid_t or rw_m or rw_t):
        return snap, None
    return snap, {
        "since": util.utc_stamp(),
        "episodes": ep_base,
        "playlists": {"mine": pl_m, "theirs": pl_t},
        "curation": {"mine": cur_m, "theirs": cur_t},
        "hidden": {"mine": hid_m, "theirs": hid_t},
        "rewatches": {"mine": rw_m, "theirs": rw_t},
    }


def merge_curation(mine, theirs):
    for_me = {r: v for r, v in theirs.items() if r not in mine or v["updated_at"] > mine[r]["updated_at"]}
    for_them = {r: v for r, v in mine.items() if r not in theirs or v["updated_at"] > theirs[r]["updated_at"]}
    return for_me, for_them


# --- the app's side: the PC it syncs with -------------------------------------

def peer():
    with database.db() as c:
        r = c.execute("SELECT address, token, pc, paired, snapshot, last_ok, last_try, last_error, take, sync, baseline "
                      "FROM sync_peer WHERE id=1").fetchone()
    if not r:
        return None
    return {"address": r[0], "token": r[1], "pc": r[2], "paired": r[3],
            "snapshot": json.loads(r[4]) if r[4] else None, "last_ok": r[5], "last_try": r[6], "last_error": r[7],
            "take": r[8], "sync": bool(r[9]), "baseline": json.loads(r[10]) if r[10] else None}


def _note(**kw):
    with database.db() as c:
        c.execute("UPDATE sync_peer SET %s WHERE id=1" % ", ".join(f"{k}=?" for k in kw), list(kw.values()))


def status():
    """GET /api/peer: what Settings → PC shows (never the token)."""
    p = peer()
    if not p:
        return {"paired": False, "running": _state["running"]}
    return {"paired": True, "address": p["address"], "pc": p["pc"], "since": p["paired"],
            "last_ok": p["last_ok"], "last_try": p["last_try"], "error": p["last_error"],
            "running": _state["running"], "last": _state["last"], "vpn_blocked": p["last_error"] == VPN_BLOCKS,
            "sync": p["sync"]}


def normal_address(raw):
    """"192.168.1.20:8789" or a pasted link -> "http://192.168.1.20:8789"."""
    raw = (raw or "").strip().rstrip("/") if isinstance(raw, str) else ""
    if raw and not re.match(r"^https?://", raw, re.I):
        raw = "http://" + raw
    try:
        u = urlparse(raw)
        port = u.port
    except ValueError:
        raise SyncError("That isn't an address. Type it as the PC shows it, like 192.168.1.20:8788.")
    if not u.hostname:
        raise SyncError("Type the PC's address, as Settings → Phone on the PC shows it.")
    if not port:
        raise SyncError("Add the port, as the PC shows it: like 192.168.1.20:8788.")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme.lower()}://{host}:{port}"


def _plain(url, data, headers, timeout=TIMEOUT):
    """(status, body, Set-Cookie lines) the normal way; OSError when unreachable."""
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as r:
            return r.status, r.read(), r.headers.get_all("Set-Cookie") or []
    except HTTPError as e:
        try:
            return e.code, e.read(), []
        finally:
            e.close()
    except URLError as e:
        raise OSError(str(e.reason))


def _home_network(url, data, headers, timeout=TIMEOUT):
    """The Android app: straight over Wi-Fi, past a VPN that lets apps do
    that (Lan.java). Raises when there is no Wi-Fi or the VPN refuses."""
    from java import jclass  # Chaquopy, only inside the app
    got = jclass("io.github.elias8832.animehub.Lan").call(
        url, data.decode() if data is not None else None, headers.get("Cookie"), UA, int(timeout * 1000))
    return int(str(got[0])), str(got[1]).encode(), [x for x in str(got[2]).split("\n") if x]


def _vpn_up():
    try:
        from java import jclass
        return bool(jclass("io.github.elias8832.animehub.Lan").behindVpn())
    except Exception:
        return False


VPN_BLOCKS = "Your VPN blocks it. It syncs once the VPN is off for a moment."


def _call(address, path, token=None, body=None, timeout=TIMEOUT):
    """One request to the PC: (JSON answer, Set-Cookie lines). In the Android
    app over the home network first, so a VPN doesn't stand in the way."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if token:
        headers["Cookie"] = f"animehub_remote={token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    got = None
    if system.ANDROID:
        try:
            got = _home_network(address + path, data, headers, timeout)
        except Exception as e:
            util.log("sync over Wi-Fi: %s" % e)
    if got is None:
        try:
            got = _plain(address + path, data, headers, timeout)
        except (OSError, ValueError):
            if system.ANDROID and _vpn_up():
                raise SyncError(VPN_BLOCKS)
            raise SyncError("Can't reach the PC. Is it on, with Phone remote on?")
    status, raw, cookies = got
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    if status == 401:
        raise SyncError("The PC doesn't know this phone any more. Pair again.")
    if status == 404:
        raise SyncError("The PC's Anime Hub is too old to sync. Update it there.")
    if status >= 400:
        raise SyncError((answer.get("error") if isinstance(answer, dict) else None) or f"The PC answered {status}.")
    return answer, cookies


SYNC_TAKES = ("combine", "pc", "phone", "new")
TAKES = SYNC_TAKES + ("none",)


def pair(address, code, take="combine"):
    """POST /api/peer {op: "pair"}: trade the PC's pairing code for a token,
    then sync at once (unless take="none"). take (Decision 98 amended):
    "combine" merges both sides, "pc" takes the PC's, "phone" pushes this
    phone's to the PC, "new" leaves existing data alone and syncs changes
    after pairing, "none" pairs for PC controls only."""
    if take not in TAKES:
        raise SyncError("Pick how to sync with the PC.")
    address = normal_address(address)
    code = re.sub(r"[^0-9]", "", code) if isinstance(code, str) else ""  # spaces, dashes, a pasted "123 456"
    if len(code) != 6:
        raise SyncError("Type the 6-digit code from the PC (under its QR code).")
    try:
        ans, cookies = _call(address, "/api/remote/pair", body={"code": code})
    except SyncError as e:
        if "answered 400" in str(e) or "code" in str(e).lower():
            raise SyncError("Wrong or expired code. Show a new one on the PC.")
        raise
    token = None
    for raw in cookies:
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except CookieError:
            continue
        if "animehub_remote" in jar:
            token = jar["animehub_remote"].value
    if not token:
        raise SyncError("The PC didn't hand over a key. Try again.")
    pc_name = ans.get("name") if isinstance(ans, dict) and isinstance(ans.get("name"), str) else None
    sync_on = take != "none"
    with database.db() as c:
        c.execute("DELETE FROM sync_peer")
        c.execute("INSERT INTO sync_peer(id, address, token, pc, paired, take, sync, baseline) VALUES(1, ?, ?, ?, ?, ?, ?, NULL)",
                  (address, token, pc_name, util.utc_stamp(), take if sync_on else None, int(sync_on)))
    _state["last"] = None
    if sync_on:
        soon(0)
        _watch["wake"].set()  # start waiting on the PC now, not at the next idle check
    return status()


def set_sync(on, take="combine"):
    """POST /api/peer {op: "sync"}: pause or resume data sync without unpairing."""
    p = peer()
    if not p:
        raise SyncError("Not paired with a PC.")
    if not on:
        with database.db() as c:
            c.execute("UPDATE sync_peer SET sync=0, take=NULL, snapshot=NULL, baseline=NULL, last_error=NULL WHERE id=1")
        _state["last"] = None
        _watch["wake"].set()
        return status()
    if take not in SYNC_TAKES:
        raise SyncError("Pick how to sync with the PC.")
    with database.db() as c:
        c.execute("UPDATE sync_peer SET sync=1, take=?, snapshot=NULL, baseline=NULL, last_error=NULL WHERE id=1", (take,))
    _state["last"] = None
    soon(0)
    _watch["wake"].set()
    return status()


def forget():
    with database.db() as c:
        c.execute("DELETE FROM sync_peer")
    _state["last"] = None


def changed():
    """Something on this device changed through a sync: open pages reload."""
    _state["stamp"] += 1


def stamp():
    return _state["stamp"]


def _in_progress_ep(f):
    if not f[1]:
        return False
    if not f[0]:
        return True
    return bool(f[2] > 0 and 0.01 < f[1] / f[2] < 0.92)


def _link_in_progress(for_me, slots, rw_me=None):
    """Episodes started or rewatches active on the other device that this one
    has neither as a file nor as a linked online episode: link the show with
    ani-cli, so Home shows them under Continue watching (only for a handful,
    never the whole list)."""
    if not anicli.anicli_path():
        return
    linked = {l["media_id"] for l in streaming.stream_links_all()}
    on_disk = {int(k.split(":")[0]) for k, ids in slots.items()
               if any(not i.startswith("stream:") for i in ids)}
    cands = []
    for key, f in for_me.items():
        mid = int(key.split(":")[0])
        if not _in_progress_ep(f) or mid in linked:
            continue
        if any(not i.startswith("stream:") for i in slots.get(key) or ()):
            continue
        cands.append((f[3] or "", mid))
    for ref, rw in (rw_me or {}).items():
        if not (isinstance(rw, dict) and rw.get("started_at")):
            continue
        m = AID_RE.match(ref)
        if not m:
            continue
        mid = int(m.group(1))
        if mid in linked or mid in on_disk:
            continue
        cands.append((rw.get("updated_at") or rw.get("started_at") or "", mid))
    cands.sort(key=lambda x: x[0], reverse=True)
    want = []
    for _, mid in cands:
        if mid not in want:
            want.append(mid)
    for mid in want[:5]:
        streaming.stream_resolve_async(mid)


def sync_once():
    """One sync with the paired PC. Returns what changed, raises SyncError."""
    p = peer()
    if not p:
        raise SyncError("Not paired with a PC.")
    if not p["sync"]:
        raise SyncError("Data sync is off.")
    with _run_lock:
        _state["running"] = True
        _note(last_try=util.utc_stamp())
        try:
            theirs, _ = _call(p["address"], "/api/sync/facts", p["token"])
            if not isinstance(theirs, dict) or theirs.get("version") != VERSION:
                raise SyncError("This app and the PC's Anime Hub are different versions. Update the older one.")
            slots = slot_map()
            mine = episode_facts(slots)
            base = p["snapshot"]
            baseline = p["baseline"]
            every = bool(theirs.get("every_slot"))
            take = p.get("take") if base is None and baseline is None else None
            take_pc = take == "pc"
            take_phone = take == "phone"
            take_new = take == "new"
            raw_my_hidden = hidden_facts()
            my_lists, my_hidden = since_pairing(playlist_facts(), raw_my_hidden, p["paired"])
            theirs_hid = theirs.get("home_hidden")
            if theirs_hid is not None and check_hidden(theirs_hid):
                theirs_hid = None
            theirs_rw = theirs.get("rewatches")
            if theirs_rw is not None and check_rewatches(theirs_rw):
                theirs_rw = None
            own_rw = rewatch_facts()
            if take_new:
                snap, next_baseline = _make_baseline(
                    mine, theirs["episodes"], my_lists, theirs["playlists"],
                    curation_facts(), theirs["curation"], my_hidden, theirs_hid, own_rw, theirs_rw)
                _state["last"] = {"pulled": 0, "pushed": 0}
                with database.db() as c:
                    c.execute("UPDATE sync_peer SET snapshot=?, baseline=?, last_ok=?, last_error=NULL, pc=?, take=NULL WHERE id=1",
                              (json.dumps(snap), json.dumps(next_baseline) if next_baseline else None,
                               util.utc_stamp(), theirs.get("name") or p["pc"]))
                return _state["last"]
            drop_cur, drop_cur_them = [], []
            keep_ep_base, pending_ep_base = {}, {}
            next_baseline = None
            if take_pc:  # the first sync, "Use the PC's": nothing goes to the PC
                result = take_theirs(mine, theirs["episodes"])
                to_me = episode_writes(mine, True, base, result)
                to_them = {}
                lists_me, gone_me = take_their_playlists(my_lists, theirs["playlists"])
                lists_them, gone_them, renames = {}, {}, {}
                own = curation_facts()
                cur_me = {r: v for r, v in theirs["curation"].items() if own.get(r) != v}
                cur_them = {}
                drop_cur = [r for r in own if r not in theirs["curation"]]
                hid_me = take_their_hidden(raw_my_hidden, theirs_hid) if theirs_hid is not None else {}
                hid_them = {}
            elif take_phone:  # the first sync, "Use the phone's": nothing changes on this phone
                result = take_theirs(theirs["episodes"], mine)
                to_me = {}
                to_them = episode_writes(theirs["episodes"], every, None, result)
                lists_me, gone_me = {}, {}
                lists_them, gone_them = take_their_playlists(theirs["playlists"], my_lists)
                renames = {}
                own = curation_facts()
                cur_me = {}
                cur_them = {r: v for r, v in own.items() if theirs["curation"].get(r) != v}
                drop_cur_them = [r for r in theirs["curation"] if r not in own]
                hid_me = {}
                hid_them = take_their_hidden(theirs_hid, raw_my_hidden) if theirs_hid is not None else {}
            elif baseline:
                ep_base = dict(baseline.get("episodes") or {})
                split = set(ep_base)
                mine_plain = {k: v for k, v in mine.items() if k not in split}
                theirs_plain = {k: v for k, v in theirs["episodes"].items() if k not in split}
                result = merge_episodes(mine_plain, theirs_plain, base, every)
                to_me = episode_writes(mine_plain, True, base, result)
                to_them = episode_writes(theirs_plain, every, base, result)
                for key, (bm, bt) in ep_base.items():
                    m = mine[key] if key in mine else bm
                    t = theirs["episodes"][key] if key in theirs["episodes"] else (bt if every else EMPTY)
                    if m == t:
                        result[key] = m
                    elif m == bm and t == bt:
                        keep_ep_base[key] = [bm, bt]
                    else:
                        win = m if (m != bm and t == bt) else t if (t != bt and m == bm) else further(m, t)
                        result[key] = win
                        if key in mine:
                            if mine[key] != win:
                                to_me[key] = [mine[key], win]
                        elif _meaningful(win):
                            to_me[key] = [list(EMPTY), win]
                        if key in theirs["episodes"]:
                            if theirs["episodes"][key] != win:
                                to_them[key] = [theirs["episodes"][key], win]
                        elif _meaningful(win):
                            to_them[key] = [list(EMPTY), win]
                        pending_ep_base[key] = [bm, bt]
                pl_bm = dict((baseline.get("playlists") or {}).get("mine") or {})
                pl_bt = dict((baseline.get("playlists") or {}).get("theirs") or {})
                since = baseline.get("since") or ""
                m_gone = {u: ts for u, ts in my_lists["gone"].items() if ts >= since}
                t_gone = {u: ts for u, ts in theirs["playlists"]["gone"].items() if ts >= since}
                skip_pl = set()
                for uid in set(pl_bm) | set(pl_bt):
                    m_cur, t_cur = my_lists["lists"].get(uid), theirs["playlists"]["lists"].get(uid)
                    m_ok = (uid in pl_bm and m_cur is not None and m_cur["changed"] == pl_bm[uid] and uid not in m_gone) \
                        or (uid not in pl_bm and m_cur is None and uid not in m_gone)
                    t_ok = (uid in pl_bt and t_cur is not None and t_cur["changed"] == pl_bt[uid] and uid not in t_gone) \
                        or (uid not in pl_bt and t_cur is None and uid not in t_gone)
                    if m_ok and t_ok:
                        skip_pl.add(uid)
                    else:
                        pl_bm.pop(uid, None)
                        pl_bt.pop(uid, None)
                lists_me, gone_me, lists_them, gone_them, renames = merge_playlists(
                    {"lists": {u: p for u, p in my_lists["lists"].items() if u not in skip_pl}, "gone": m_gone},
                    {"lists": {u: p for u, p in theirs["playlists"]["lists"].items() if u not in skip_pl}, "gone": t_gone})
                cur_bm = dict((baseline.get("curation") or {}).get("mine") or {})
                cur_bt = dict((baseline.get("curation") or {}).get("theirs") or {})
                own, t_cur = curation_facts(), theirs["curation"]
                own_in, t_cur_in = dict(own), dict(t_cur)
                cur_me, cur_them = {}, {}
                for ref in set(cur_bm) | set(cur_bt):
                    m_c, t_c = own.get(ref), t_cur.get(ref)
                    m_ok = (ref in cur_bm and m_c is not None and m_c["updated_at"] == cur_bm[ref]) or (ref not in cur_bm and m_c is None)
                    t_ok = (ref in cur_bt and t_c is not None and t_c["updated_at"] == cur_bt[ref]) or (ref not in cur_bt and t_c is None)
                    if m_ok and t_ok:
                        own_in.pop(ref, None)
                        t_cur_in.pop(ref, None)
                    else:
                        cur_bm.pop(ref, None)
                        cur_bt.pop(ref, None)
                        if not m_ok and t_ok and m_c is not None:
                            cur_them[ref] = m_c
                            own_in.pop(ref, None)
                            t_cur_in.pop(ref, None)
                        elif not t_ok and m_ok and t_c is not None:
                            cur_me[ref] = t_c
                            own_in.pop(ref, None)
                            t_cur_in.pop(ref, None)
                m2, t2 = merge_curation(own_in, t_cur_in)
                cur_me.update(m2)
                cur_them.update(t2)
                hid_bm = dict((baseline.get("hidden") or {}).get("mine") or {})
                hid_bt = dict((baseline.get("hidden") or {}).get("theirs") or {})
                if theirs_hid is not None:
                    m_h = {"hidden": dict(my_hidden["hidden"]), "shown": dict(my_hidden["shown"])}
                    t_h = {"hidden": dict(theirs_hid["hidden"]), "shown": dict(theirs_hid["shown"])}
                    for mid in set(hid_bm) | set(hid_bt):
                        m_st, t_st = _home_state(my_hidden, mid), _home_state(theirs_hid, mid)
                        bm_st = tuple(hid_bm[mid]) if mid in hid_bm else None
                        bt_st = tuple(hid_bt[mid]) if mid in hid_bt else None
                        if m_st == bm_st and t_st == bt_st:
                            m_h["hidden"].pop(mid, None)
                            m_h["shown"].pop(mid, None)
                            t_h["hidden"].pop(mid, None)
                            t_h["shown"].pop(mid, None)
                        else:
                            hid_bm.pop(mid, None)
                            hid_bt.pop(mid, None)
                    hid_me, hid_them = merge_hidden(m_h, t_h)
                else:
                    hid_me, hid_them = {}, {}
            else:
                result = merge_episodes(mine, theirs["episodes"], base, every)
                to_me = episode_writes(mine, True, base, result)
                to_them = episode_writes(theirs["episodes"], every, base, result)
                lists_me, gone_me, lists_them, gone_them, renames = merge_playlists(my_lists, theirs["playlists"])
                cur_me, cur_them = merge_curation(curation_facts(), theirs["curation"])
                hid_me, hid_them = merge_hidden(my_hidden, theirs_hid) if theirs_hid is not None else ({}, {})
            if not take_pc and hid_me:
                for mid in list(hid_me.get("hidden", {})):
                    cur = _home_state(raw_my_hidden, mid)
                    if cur and cur[1] == 0 and cur[0] >= hid_me["hidden"][mid]:
                        hid_me["hidden"].pop(mid)
                for mid in list(hid_me.get("shown", {})):
                    cur = _home_state(raw_my_hidden, mid)
                    if cur and cur[1] == 1 and cur[0] >= hid_me["shown"][mid]:
                        hid_me["shown"].pop(mid)
            if theirs_hid is None or not (hid_them.get("hidden") or hid_them.get("shown")):
                hid_them = {}  # an older PC has no "Remove from Home"
            n_hid_me = len(hid_me.get("hidden", {})) + len(hid_me.get("shown", {}))
            drop_rw, drop_rw_them = [], []
            rw_bm = dict((baseline.get("rewatches") or {}).get("mine") or {}) if baseline else {}
            rw_bt = dict((baseline.get("rewatches") or {}).get("theirs") or {}) if baseline else {}
            if theirs_rw is None:
                rw_me, rw_them = {}, {}
            elif take_pc:
                rw_me = {r: v for r, v in theirs_rw.items() if own_rw.get(r) != v}
                rw_them = {}
                drop_rw = [r for r in own_rw if r not in theirs_rw]
            elif take_phone:
                rw_me = {}
                rw_them = {r: v for r, v in own_rw.items() if theirs_rw.get(r) != v}
                drop_rw_them = [r for r in theirs_rw if r not in own_rw]
            elif baseline and (rw_bm or rw_bt):
                own_rw_in, t_rw_in = dict(own_rw), dict(theirs_rw)
                rw_me, rw_them = {}, {}
                for ref in set(rw_bm) | set(rw_bt):
                    m_r, t_r = own_rw.get(ref), theirs_rw.get(ref)
                    m_ok = (ref in rw_bm and m_r is not None and m_r["updated_at"] == rw_bm[ref]) or (ref not in rw_bm and m_r is None)
                    t_ok = (ref in rw_bt and t_r is not None and t_r["updated_at"] == rw_bt[ref]) or (ref not in rw_bt and t_r is None)
                    if m_ok and t_ok:
                        own_rw_in.pop(ref, None)
                        t_rw_in.pop(ref, None)
                    else:
                        rw_bm.pop(ref, None)
                        rw_bt.pop(ref, None)
                        if not m_ok and t_ok and m_r is not None:
                            rw_them[ref] = m_r
                            own_rw_in.pop(ref, None)
                            t_rw_in.pop(ref, None)
                        elif not t_ok and m_ok and t_r is not None:
                            rw_me[ref] = t_r
                            own_rw_in.pop(ref, None)
                            t_rw_in.pop(ref, None)
                rm2, rt2 = merge_rewatches(own_rw_in, t_rw_in)
                rw_me.update(rm2)
                rw_them.update(rt2)
            else:
                rw_me, rw_them = merge_rewatches(own_rw, theirs_rw)
            if renames:
                with database.db() as c:
                    c.executemany("UPDATE playlists SET uid=? WHERE uid=?", [(new, old) for old, new in renames.items()])
            sent = {"episodes": to_them, "playlists": lists_them, "gone": gone_them, "curation": cur_them}
            if drop_cur_them:
                sent["drop_curation"] = drop_cur_them
            if hid_them:
                sent["home_hidden"] = hid_them
            if rw_them:
                sent["rewatches"] = rw_them
            if drop_rw_them:
                sent["drop_rewatches"] = drop_rw_them
            got = _call(p["address"], "/api/sync/apply", p["token"], sent)[0] if any(sent.values()) else {"applied": []}
            there = set(got.get("applied") or [])
            here = set(apply_episodes(to_me, slots))
            apply_playlists(lists_me, gone_me)
            apply_curation(cur_me)
            if n_hid_me:
                apply_hidden(hid_me)
            if take_pc and n_hid_me and theirs_hid is not None:
                folders = _show_mids()
                owners = {}
                for sid, mids in folders.items():
                    for mid in mids:
                        owners.setdefault(str(mid), []).append(sid)
                with database.db() as c:
                    for mid in hid_me.get("shown", {}):
                        ts = theirs_hid["shown"].get(mid)
                        for sid in [f"aid:{mid}"] + owners.get(mid, []):
                            c.execute("DELETE FROM sync_gone WHERE kind='continue' AND key=?", (sid,))
                        if ts:
                            c.execute("INSERT INTO sync_gone(kind, key, ts) VALUES('continue', ?, ?)",
                                      (f"aid:{mid}", ts))
                    for mid in hid_me.get("hidden", {}):
                        at = theirs_hid["hidden"][mid]
                        for sid in [f"aid:{mid}"] + owners.get(mid, []):
                            c.execute("DELETE FROM sync_gone WHERE kind='continue' AND key=?", (sid,))
                            c.execute("UPDATE continue_hidden SET at=? WHERE show_id=?", (at, sid))
            apply_rewatches(rw_me)
            if take_phone and gone_them:
                with database.db() as c:
                    c.executemany("INSERT INTO sync_gone(kind, key, ts) VALUES('playlist', ?, ?) "
                                  "ON CONFLICT(kind, key) DO UPDATE SET ts=MAX(ts, excluded.ts)",
                                  list(gone_them.items()))
            if take_phone and hid_them.get("shown"):
                with database.db() as c:
                    c.executemany("INSERT INTO sync_gone(kind, key, ts) VALUES('continue', ?, ?) "
                                  "ON CONFLICT(kind, key) DO UPDATE SET ts=MAX(ts, excluded.ts)",
                                  [(f"aid:{mid}", ts) for mid, ts in hid_them["shown"].items()])
            if drop_cur:
                drop_curation(drop_cur)
            if drop_rw:
                drop_rewatches(drop_rw)
            _link_in_progress({k: v[1] for k, v in to_me.items() if k in here}, slots, rw_me)
            snap = {}
            for k, v in result.items():
                held = (k in to_them and k not in there) or (k in to_me and k not in here)
                if held and k in pending_ep_base:
                    keep_ep_base[k] = pending_ep_base[k]
                    continue
                keep = (base or {}).get(k) if held else v
                if keep and _meaningful(keep):
                    snap[k] = keep
            if baseline and (keep_ep_base or pl_bm or pl_bt or cur_bm or cur_bt or hid_bm or hid_bt or rw_bm or rw_bt):
                next_baseline = {
                    "since": baseline.get("since") or "",
                    "episodes": keep_ep_base,
                    "playlists": {"mine": pl_bm, "theirs": pl_bt},
                    "curation": {"mine": cur_bm, "theirs": cur_bt},
                    "hidden": {"mine": hid_bm, "theirs": hid_bt},
                    "rewatches": {"mine": rw_bm, "theirs": rw_bt},
                }
            pulled = len(here) + len(lists_me) + len(gone_me) + len(cur_me) + len(drop_cur) + n_hid_me + len(rw_me) + len(drop_rw)
            pushed = len(there) + len(lists_them) + len(gone_them) + len(cur_them) + len(drop_cur_them) + sum(map(len, hid_them.values())) + len(rw_them) + len(drop_rw_them)
            _state["last"] = {"pulled": pulled, "pushed": pushed}
            with database.db() as c:
                c.execute("UPDATE sync_peer SET snapshot=?, baseline=?, last_ok=?, last_error=NULL, pc=?, take=NULL WHERE id=1",
                          (json.dumps(snap), json.dumps(next_baseline) if next_baseline else None,
                           util.utc_stamp(), theirs.get("name") or p["pc"]))
            return _state["last"]
        except SyncError as e:
            _note(last_error=str(e))
            raise
        except Exception as e:
            util.log("sync with the PC failed: %r" % e)
            _note(last_error="Sync failed. It tries again in a moment.")
            raise SyncError("Sync failed. It tries again in a moment.")
        finally:
            _state["running"] = False


# --- what plays on the PC (TODO 263) -------------------------------------------
# The browser remote shows a bar while mpv plays on the PC (Decision 43). The
# app's page is served by its own hub, so its hub asks the paired PC and hands
# the remote's buttons on, with the token pairing gave it.

PC_NOW_KEYS = ("show", "label", "percent", "episode_id", "loading", "next", "slot", "cover", "banner", "position", "duration",
               "paused", "volume", "tracks", "speed", "sub_delay", "segments", "stop_after")
PC_TIMEOUT = 4       # a poll: a PC that's gone answers "nothing" quickly
_pc = {"at": 0.0, "now": None}
_pc_lock = threading.Lock()
_slots = {}


def slot_of(ep_id):
    """"<AniList id>:<n>" for an episode on this device (the sync's key), or
    None. Remembered per id: asked on every remote poll."""
    if ep_id in _slots:
        return _slots[ep_id]
    slot = _stream_slot(ep_id) if ep_id.startswith("stream:") else None
    if not ep_id.startswith("stream:"):
        try:
            s, _ = library.episode_by_id(ep_id)
            for u in mapping.release_units(s) if s else ():
                if mapping.unit_fits(u, u["total"]):
                    slot = next((f"{u['media_id']}:{n}" for ep, n in u["eps"] if ep["id"] == ep_id), slot)
        except Exception as e:  # a remote poll never fails on this
            util.log("slot_of %s: %r" % (ep_id, e))
    if len(_slots) > 500:
        _slots.clear()
    _slots[ep_id] = slot
    return slot


def now_extra(ep_id):
    """What a paired device needs to show the playing episode as its own:
    {"slot", "cover", "banner"} (AniList's picture links, as stored)."""
    slot = slot_of(ep_id)
    if not slot:
        return {}
    out = {"slot": slot}
    with database.db() as c:
        r = c.execute("SELECT data FROM media WHERE id=?", (int(slot.split(":")[0]),)).fetchone()
    try:
        m = json.loads(r[0]) if r else {}
    except ValueError:
        m = {}
    cover = (m.get("coverImage") or {}).get("extraLarge") if isinstance(m.get("coverImage"), dict) else None
    for k, v in (("cover", cover), ("banner", m.get("bannerImage"))):
        if isinstance(v, str) and v.startswith("https://"):
            out[k] = v
    return out


def episode_for_slot(slot, online=False):
    """The PC's side of "Play on the PC" from the app: its own episode for an
    AniList slot. A file on disk first, then its online episode; an entry it
    has neither of plays online (ani-cli links it on the way). online: the
    online episode even when a file is there (a Sub/Dub pick)."""
    if not isinstance(slot, str) or not SLOT_RE.match(slot):
        return None
    if online:
        return "stream:" + slot
    ids = slot_map().get(slot, [])
    return next((i for i in ids if not i.startswith("stream:")), None) or (ids[0] if ids else "stream:" + slot)


def pc_play(ep_id, from_start=False, mode=None, vpn_ok=None, aniskip=None, rewatch=False):
    """POST /api/peer {op: "play"}: start this phone's episode on the paired
    PC, found there by its AniList slot. mode ("sub"/"dub"): online in that
    language, as the menu's Play dubbed / Play subbed. Without a VPN on the
    PC it answers {"ask": "vpn"|"aniskip", ...} instead; vpn_ok and aniskip
    carry the app's answers (TODO 274)."""
    p = peer()
    if not p:
        return {"error": "Not paired with a PC."}
    slot = slot_of(ep_id) if isinstance(ep_id, str) else None
    if not slot:
        return {"error": "This show isn't matched to AniList, so the PC can't find it."}
    _pc["at"] = 0.0
    try:
        got, _ = _call(p["address"], "/api/play", p["token"],
                       {"slot": slot, "from_start": bool(from_start), **({"mode": mode} if mode else {}),
                        **({"vpn_ok": True} if vpn_ok else {}), **({"aniskip": aniskip} if aniskip is not None else {}),
                        **({"rewatch": True} if rewatch else {})},
                       timeout=15)
    except SyncError as e:
        if "'id'" in str(e):  # a PC from before "slot"
            return {"error": "Update Anime Hub on the PC to start episodes there from the app."}
        return {"error": str(e)}
    return got if isinstance(got, dict) else {"error": "The PC answered something odd."}


def pc_now():
    """GET /api/peer/now: what mpv plays on the paired PC, {"now": {...}} or
    {"now": None}, and whether a PC is paired. Pages share one answer per second."""
    p = peer()
    if not p:
        return {"now": None, "paired": False}
    with _pc_lock:
        if time.time() - _pc["at"] < 0.8:
            return {"now": _pc["now"], "paired": True}
        try:
            got, _ = _call(p["address"], "/api/now", p["token"], timeout=PC_TIMEOUT)
        except SyncError:
            got = {}
        now = ({k: got[k] for k in PC_NOW_KEYS if k in got}
               if isinstance(got, dict) and got.get("playing") is True and isinstance(got.get("episode_id"), str) else None)
        _pc.update(at=time.time(), now=now)
        return {"now": now, "paired": True}


def pc_control(action, value=None):
    """POST /api/peer {op: "control"}: a remote button, handed to the PC's
    /api/control. The PC's answer, or {"error": why}."""
    p = peer()
    if not p:
        return {"error": "Not paired with a PC."}
    _pc["at"] = 0.0  # the next poll asks the PC again
    try:
        got, _ = _call(p["address"], "/api/control", p["token"],
                       {"action": action} if value is None else {"action": action, "value": value}, timeout=8)
    except SyncError as e:
        return {"error": str(e)}
    return got if isinstance(got, dict) else {"error": "The PC answered something odd."}


def soon(delay=SETTLE):
    """Sync shortly (something changed here, or the app came back)."""
    _state["due"] = time.time() + delay
    _wake.set()
    touched()


def touched():
    """Something here changed that a paired device should hear of now."""
    with _touch_cv:
        _touch["n"] += 1
        _touch_cv.notify_all()


def _touch_mark():
    return f"{_touch['boot']}-{_touch['n']}"


def wait_payload(since, timeout=WAIT):
    """GET /api/sync/wait?since=<mark>: answers when this device changed
    after `since`, or after `timeout` seconds. {"mark": now}."""
    with _touch_cv:
        _touch_cv.wait_for(lambda: _touch_mark() != since, timeout if since else 0)
        return {"mark": _touch_mark()}


def away(gone):
    """The app's page went to the back (True) or came to the front."""
    _watch["away"] = bool(gone)
    if not gone:
        _watch["wake"].set()


def watch():
    """The app's side: one wait at a time on the paired PC while the page is
    in front; a new mark syncs at once. Backs off when the PC can't be
    reached, and for a while when it's too old to have the wait."""
    mark = None
    while True:
        p = peer()
        if not p or not p.get("sync", True) or _watch["away"]:
            mark = None
            _watch["wake"].wait(30)
            _watch["wake"].clear()
            continue
        try:
            got, _ = _call(p["address"], "/api/sync/wait?since=" + quote(mark or ""), p["token"])
        except SyncError as e:
            mark = None
            _watch["wake"].wait(300 if "too old" in str(e) else 15)
            _watch["wake"].clear()
            continue
        except Exception as e:  # never let it die
            util.log("sync watch: %r" % e)
            mark = None
            _watch["wake"].wait(15)
            _watch["wake"].clear()
            continue
        new = got.get("mark") if isinstance(got, dict) else None
        if not isinstance(new, str):
            _watch["wake"].wait(300)
            _watch["wake"].clear()
            continue
        if mark is not None and new != mark:
            soon(0)
        mark = new


def loop():
    """The app's background sync: at start, every EVERY seconds, and soon
    after a change here. Does nothing until paired."""
    _state["due"] = time.time() + 5
    while True:
        _wake.wait(max(0.5, min(EVERY, _state["due"] - time.time())))
        _wake.clear()
        if time.time() < _state["due"]:
            continue
        _state["due"] = time.time() + EVERY
        p = peer()
        if not p or not p.get("sync", True):
            continue
        try:
            sync_once()
        except SyncError:
            pass
        except Exception as e:  # never let the loop die
            util.log("sync loop: %r" % e)


def start():
    threading.Thread(target=loop, daemon=True).start()
    if system.ANDROID:
        threading.Thread(target=watch, daemon=True).start()
