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
import re
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import VERSION as APP_VERSION, anicli, database, library, mapping, streaming, system, util

VERSION = 1
EVERY = 120          # seconds between syncs while nothing else asks
SETTLE = 3           # a burst of changes syncs once
TIMEOUT = 20
EMPTY = [False, 0.0, 0.0, None]
UA = f"AnimeHub-App/{APP_VERSION} (sync)"
LIST_FIELDS = ("name", "color", "note", "icon", "pinned", "head", "view", "dim", "pos")
CUR_FIELDS = ("status", "score", "favourite", "rewatches", "started_at", "finished_at", "prefs")

_run_lock = threading.Lock()   # one sync at a time
_wake = threading.Event()
_state = {"running": False, "stamp": 0, "last": None}


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


# --- what the PC answers ----------------------------------------------------

def facts_payload():
    """GET /api/sync/facts."""
    # every_slot (additive): "episodes" lists every slot, so a missing one
    # is "not here", not "unwatched". An older PC's answer lacks it.
    return {"version": VERSION, "name": socket.gethostname(), "episodes": episode_facts(),
            "every_slot": True, "playlists": playlist_facts(), "curation": curation_facts()}


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
    return None


def apply_payload(body):
    """POST /api/sync/apply: the app's merge result for this device."""
    done = apply_episodes(body.get("episodes") or {})
    apply_playlists(body.get("playlists") or {}, body.get("gone") or {})
    apply_curation(body.get("curation") or {})
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
        if m and t and m["changed"] == t["changed"] and uid not in renames.values():
            continue  # unchanged since the last sync (pictures may differ: each keeps its own)
        newest = max(x["changed"] for x in (m, t) if x)
        if uid in gone and gone[uid] >= newest:
            continue  # deleted after its last change: gone everywhere
        gone_me.pop(uid, None)
        gone_them.pop(uid, None)
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


def merge_curation(mine, theirs):
    for_me = {r: v for r, v in theirs.items() if r not in mine or v["updated_at"] > mine[r]["updated_at"]}
    for_them = {r: v for r, v in mine.items() if r not in theirs or v["updated_at"] > theirs[r]["updated_at"]}
    return for_me, for_them


# --- the app's side: the PC it syncs with -------------------------------------

def peer():
    with database.db() as c:
        r = c.execute("SELECT address, token, pc, paired, snapshot, last_ok, last_try, last_error "
                      "FROM sync_peer WHERE id=1").fetchone()
    if not r:
        return None
    return {"address": r[0], "token": r[1], "pc": r[2], "paired": r[3],
            "snapshot": json.loads(r[4]) if r[4] else None, "last_ok": r[5], "last_try": r[6], "last_error": r[7]}


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
            "running": _state["running"], "last": _state["last"], "vpn_blocked": p["last_error"] == VPN_BLOCKS}


def normal_address(raw):
    """"192.168.2.31:8789" or a pasted link -> "http://192.168.2.31:8789"."""
    raw = (raw or "").strip().rstrip("/") if isinstance(raw, str) else ""
    if raw and not re.match(r"^https?://", raw, re.I):
        raw = "http://" + raw
    try:
        u = urlparse(raw)
        port = u.port
    except ValueError:
        raise SyncError("That isn't an address. Type it as the PC shows it, like 192.168.2.31:8788.")
    if not u.hostname:
        raise SyncError("Type the PC's address, as Settings → Phone on the PC shows it.")
    if not port:
        raise SyncError("Add the port, as the PC shows it: like 192.168.2.31:8788.")
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme.lower()}://{host}:{port}"


def _plain(url, data, headers):
    """(status, body, Set-Cookie lines) the normal way; OSError when unreachable."""
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=TIMEOUT) as r:
            return r.status, r.read(), r.headers.get_all("Set-Cookie") or []
    except HTTPError as e:
        try:
            return e.code, e.read(), []
        finally:
            e.close()
    except URLError as e:
        raise OSError(str(e.reason))


def _home_network(url, data, headers):
    """The Android app: straight over Wi-Fi, past a VPN that lets apps do
    that (Lan.java). Raises when there is no Wi-Fi or the VPN refuses."""
    from java import jclass  # Chaquopy, only inside the app
    got = jclass("io.github.elias8832.animehub.Lan").call(
        url, data.decode() if data is not None else None, headers.get("Cookie"), UA, TIMEOUT * 1000)
    return int(str(got[0])), str(got[1]).encode(), [x for x in str(got[2]).split("\n") if x]


def _vpn_up():
    try:
        from java import jclass
        return bool(jclass("io.github.elias8832.animehub.Lan").behindVpn())
    except Exception:
        return False


VPN_BLOCKS = "Your VPN blocks it. It syncs once the VPN is off for a moment."


def _call(address, path, token=None, body=None):
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
            got = _home_network(address + path, data, headers)
        except Exception as e:
            util.log("sync over Wi-Fi: %s" % e)
    if got is None:
        try:
            got = _plain(address + path, data, headers)
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


def pair(address, code):
    """POST /api/peer {op: "pair"}: trade the PC's pairing code for a token,
    then sync at once."""
    address = normal_address(address)
    code = re.sub(r"[^0-9]", "", code) if isinstance(code, str) else ""  # spaces, dashes, a pasted "123 456"
    if len(code) != 6:
        raise SyncError("Type the 6-digit code from the PC (under its QR code).")
    try:
        _, cookies = _call(address, "/api/remote/pair", body={"code": code})
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
    with database.db() as c:
        c.execute("DELETE FROM sync_peer")
        c.execute("INSERT INTO sync_peer(id, address, token, paired) VALUES(1, ?, ?, ?)",
                  (address, token, util.utc_stamp()))
    soon(0)
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


def _link_in_progress(for_me, slots):
    """Episodes started on the other device that this one has neither as a
    file nor as a linked online episode: link the show with ani-cli, so Home
    shows them under Continue watching (only for a handful, never the whole list)."""
    if not anicli.anicli_path():
        return
    linked = {l["media_id"] for l in streaming.stream_links_all()}
    want = []
    for key, f in for_me.items():
        mid = int(key.split(":")[0])
        if f[0] or not f[1] or mid in linked or mid in want:
            continue
        if any(not i.startswith("stream:") for i in slots.get(key) or ()):
            continue
        want.append(mid)
    for mid in want[:5]:
        streaming.stream_resolve_async(mid)


def sync_once():
    """One sync with the paired PC. Returns what changed, raises SyncError."""
    p = peer()
    if not p:
        raise SyncError("Not paired with a PC.")
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
            every = bool(theirs.get("every_slot"))
            result = merge_episodes(mine, theirs["episodes"], base, every)
            to_them = episode_writes(theirs["episodes"], every, base, result)
            to_me = episode_writes(mine, True, base, result)
            lists_me, gone_me, lists_them, gone_them, renames = merge_playlists(playlist_facts(), theirs["playlists"])
            if renames:
                with database.db() as c:
                    c.executemany("UPDATE playlists SET uid=? WHERE uid=?", [(new, old) for old, new in renames.items()])
            cur_me, cur_them = merge_curation(curation_facts(), theirs["curation"])
            sent = {"episodes": to_them, "playlists": lists_them, "gone": gone_them, "curation": cur_them}
            got = _call(p["address"], "/api/sync/apply", p["token"], sent)[0] if any(sent.values()) else {"applied": []}
            there = set(got.get("applied") or [])
            here = set(apply_episodes(to_me, slots))
            apply_playlists(lists_me, gone_me)
            apply_curation(cur_me)
            _link_in_progress({k: v[1] for k, v in to_me.items() if k in here}, slots)
            snap = {}
            for k, v in result.items():
                held = (k in to_them and k not in there) or (k in to_me and k not in here)
                keep = (base or {}).get(k) if held else v
                if keep and _meaningful(keep):
                    snap[k] = keep
            pulled = len(here) + len(lists_me) + len(gone_me) + len(cur_me)
            pushed = len(there) + len(lists_them) + len(gone_them) + len(cur_them)
            _state["last"] = {"pulled": pulled, "pushed": pushed}
            with database.db() as c:
                c.execute("UPDATE sync_peer SET snapshot=?, last_ok=?, last_error=NULL, pc=? WHERE id=1",
                          (json.dumps(snap), util.utc_stamp(), theirs.get("name") or p["pc"]))
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


def soon(delay=SETTLE):
    """Sync shortly (something changed here, or the app came back)."""
    _state["due"] = time.time() + delay
    _wake.set()


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
        if not peer():
            continue
        try:
            sync_once()
        except SyncError:
            pass
        except Exception as e:  # never let the loop die
            util.log("sync loop: %r" % e)


def start():
    threading.Thread(target=loop, daemon=True).start()
