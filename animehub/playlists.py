"""Playlists: the user's own named, ordered lists of shows (Decision 64).

Kept in hub.db only, never written to AniList. An item's ref says which show
it is, the same way curation does: "aid:<AniList id>", else "sid:<library
show id>", so it survives connecting or leaving AniList. Each item keeps its
title and poster, so a show that is on no list and on no disk still has a card.
A playlist has a name and, if the user sets them, a colour, a short note, an
icon (a Lucide icon's name, drawn by the page), a pin to Home and how its
page looks (head: "banner" or "cover", view: "list" or "posters"); the lists
themselves are in the user's order too. Each list has a `uid` the Android
app and the PC share and the time it last `changed`; a delete is kept in
`sync_gone` so it reaches the other device (peersync.py, Decision 91)."""

import re
import secrets
from datetime import datetime, timezone

from . import database

REF_RE = re.compile(r"^(aid:[0-9]+|sid:.+)$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON_RE = re.compile(r"^[a-z0-9-]{1,32}$")
NAME_MAX, NOTE_MAX = 60, 140
HEADS, VIEWS = ("banner", "cover"), ("list", "posters")  # the first is the default


class PlaylistError(ValueError):
    """A request the user can read the reason for."""


def all_playlists():
    with database.db() as c:
        lists = c.execute("SELECT id, name, color, note, icon, pinned, head, view, dim FROM playlists "
                          "ORDER BY pos, id").fetchall()
        items = c.execute("SELECT playlist_id, ref, title, poster FROM playlist_items "
                          "ORDER BY playlist_id, pos").fetchall()
    by_list = {}
    for pid, ref, title, poster in items:
        by_list.setdefault(pid, []).append({"ref": ref, "title": title, "poster": poster})
    return [{"id": pid, "name": name, "color": color, "note": note or "", "icon": icon or "",
             "pinned": bool(pinned), "head": head if head in HEADS else HEADS[0],
             "view": view if view in VIEWS else VIEWS[0], "dim": bool(dim), "items": by_list.get(pid, [])}
            for pid, name, color, note, icon, pinned, head, view, dim in lists]


def _name(raw):
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name:
        raise PlaylistError("Give the playlist a name.")
    if len(name) > NAME_MAX:
        raise PlaylistError(f"That name is too long ({NAME_MAX} characters at most).")
    return name


def _looks(body):
    """The optional fields of an edit, checked: {column: value}."""
    out = {}
    if "color" in body:
        v = body["color"]
        if v not in (None, "") and not (isinstance(v, str) and COLOR_RE.match(v)):
            raise PlaylistError("A colour is #rrggbb.")
        out["color"] = v or None
    if "note" in body:
        v = body["note"]
        if v is not None and not isinstance(v, str):
            raise PlaylistError("The description must be text.")
        v = " ".join((v or "").split())
        if len(v) > NOTE_MAX:
            raise PlaylistError(f"That description is too long ({NOTE_MAX} characters at most).")
        out["note"] = v
    if "icon" in body:
        v = body["icon"]
        if v not in (None, "") and not (isinstance(v, str) and ICON_RE.match(v)):
            raise PlaylistError("That isn't an icon.")
        out["icon"] = v or ""
    for key in ("pinned", "dim"):
        if key in body:
            if not isinstance(body[key], bool):
                raise PlaylistError(f"'{key}' must be true or false.")
            out[key] = int(body[key])
    for key, ok in (("head", HEADS), ("view", VIEWS)):
        if key in body:
            if body[key] not in ok:
                raise PlaylistError(f"'{key}' is one of: {', '.join(ok)}.")
            out[key] = body[key]
    return out


def _must_exist(c, pid):
    if isinstance(pid, bool) or not isinstance(pid, int) \
            or not c.execute("SELECT 1 FROM playlists WHERE id=?", (pid,)).fetchone():
        raise PlaylistError("That playlist is gone.")


def stamp():
    """When a list changed, to the microsecond: two edits on two devices in
    the same second still have an order (the newer list wins a sync)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def touch(c, pid):
    c.execute("UPDATE playlists SET changed=? WHERE id=?", (stamp(), pid))


def _refs(c, pid):
    return [r[0] for r in c.execute("SELECT ref FROM playlist_items WHERE playlist_id=? ORDER BY pos", (pid,))]


def _ref(raw):
    if not isinstance(raw, str) or not REF_RE.match(raw):
        raise PlaylistError("That show can't go on a playlist.")
    return raw


def _item(raw):
    bad = PlaylistError("That show can't go on a playlist.")
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str) or not raw["title"]:
        raise bad
    if raw.get("poster") is not None and not isinstance(raw["poster"], str):
        raise bad
    return _ref(raw.get("ref")), raw["title"], raw.get("poster")


def _int(v, what):
    if isinstance(v, bool) or not isinstance(v, int):
        raise PlaylistError(f"'{what}' must be a whole number.")
    return v


def _place(order, key, at):
    """order with key moved to index at (clamped)."""
    order = [k for k in order if k != key]
    order.insert(max(0, min(len(order), at)), key)
    return order


def create(name, item=None, looks=None, items=(), at=None):
    """A new playlist, with its first show when one is given. Undoing a
    delete brings one back whole: looks, items and its place (at)."""
    name = _name(name)
    looks = _looks(looks or {})
    if item is not None:
        items = [item]
    rows = [_item(x) for x in items]
    if at is not None:
        _int(at, "at")
    with database.db() as c:
        pid = c.execute("INSERT INTO playlists(name, pos, uid, changed) VALUES(?, "
                        "(SELECT COALESCE(MAX(pos), 0) + 1 FROM playlists), ?, ?)",
                        (name, secrets.token_hex(8), stamp())).lastrowid
        for col, v in looks.items():
            c.execute(f"UPDATE playlists SET {col}=? WHERE id=?", (v, pid))
        seen = set()
        for ref, title, poster in rows:
            if ref not in seen:
                seen.add(ref)
                c.execute("INSERT INTO playlist_items(playlist_id, ref, pos, title, poster) VALUES(?,?,?,?,?)",
                          (pid, ref, len(seen) - 1, title, poster))
        if at is not None:
            _set_list_order(c, _place(_list_ids(c), pid, at))
    return pid


def edit(pid, body):
    """Name, colour, note, icon, pin, head, view, dim: whichever the body carries."""
    looks = _looks(body)
    if "name" in body:
        looks["name"] = _name(body["name"])
    with database.db() as c:
        _must_exist(c, pid)
        for col, v in looks.items():
            c.execute(f"UPDATE playlists SET {col}=? WHERE id=?", (v, pid))
        touch(c, pid)


def rename(pid, name):
    edit(pid, {"name": name})


def delete(pid):
    with database.db() as c:
        _must_exist(c, pid)
        uid = c.execute("SELECT uid FROM playlists WHERE id=?", (pid,)).fetchone()[0]
        if uid:
            c.execute("INSERT INTO sync_gone(kind, key, ts) VALUES('playlist', ?, ?) "
                      "ON CONFLICT(kind, key) DO UPDATE SET ts=excluded.ts", (uid, stamp()))
        c.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        c.execute("DELETE FROM playlists WHERE id=?", (pid,))


def add(pid, item, at=None):
    """Adds a show at the end, or at index `at` (Undo puts a removed show
    back where it was). A show already on the list stays where it is."""
    ref, title, poster = _item(item)
    if at is not None:
        _int(at, "at")
    with database.db() as c:
        _must_exist(c, pid)
        refs = _refs(c, pid)
        if ref in refs:
            return
        c.execute("INSERT INTO playlist_items(playlist_id, ref, pos, title, poster) VALUES(?,?,?,?,?)",
                  (pid, ref, len(refs), title, poster))
        if at is not None:
            _set_order(c, pid, _place(refs + [ref], ref, at))
        touch(c, pid)


def remove(pid, ref):
    with database.db() as c:
        _must_exist(c, pid)
        c.execute("DELETE FROM playlist_items WHERE playlist_id=? AND ref=?", (pid, _ref(ref)))
        touch(c, pid)


def _set_order(c, pid, refs):
    c.executemany("UPDATE playlist_items SET pos=? WHERE playlist_id=? AND ref=?",
                  [(i, pid, r) for i, r in enumerate(refs)])


def _list_ids(c):
    return [r[0] for r in c.execute("SELECT id FROM playlists ORDER BY pos, id")]


def _set_list_order(c, ids):
    c.executemany("UPDATE playlists SET pos=? WHERE id=?", [(i, pid) for i, pid in enumerate(ids)])


def move(pid, ref, by):
    """Moves a show by one or more places; past either end it stops there."""
    by = _int(by, "by")
    with database.db() as c:
        _must_exist(c, pid)
        refs = _refs(c, pid)
        if ref not in refs:
            raise PlaylistError("That show isn't on this playlist.")
        _set_order(c, pid, _place(refs, ref, refs.index(ref) + by))
        touch(c, pid)


def move_list(pid, by):
    """Moves a whole playlist among the others."""
    by = _int(by, "by")
    with database.db() as c:
        _must_exist(c, pid)
        ids = _list_ids(c)
        _set_list_order(c, _place(ids, pid, ids.index(pid) + by))
        touch(c, pid)


def apply(body):
    """One POST /api/playlists: {op, ...}. Answers with every playlist."""
    op, pid = body.get("op"), body.get("id")
    created = None
    if op == "create":
        created = create(body.get("name"), body.get("item"), body.get("looks"),
                         body.get("items") or (), body.get("at"))
    elif op == "edit":
        edit(pid, {k: v for k, v in body.items() if k not in ("op", "id")})
    elif op == "rename":
        rename(pid, body.get("name"))
    elif op == "delete":
        delete(pid)
    elif op == "add":
        add(pid, body.get("item"), body.get("at"))
    elif op == "remove":
        remove(pid, body.get("ref"))
    elif op == "move":
        move(pid, body.get("ref"), body.get("by"))
    elif op == "move_list":
        move_list(pid, body.get("by"))
    else:
        raise PlaylistError("Unknown playlist action.")
    return {"ok": True, "created": created, "playlists": all_playlists()}
