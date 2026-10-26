"""Mapping local seasons onto franchise entries, and per-entry art."""

import json
import re
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import anilist, database, franchise, library, metadata, util


# --- Local mapping (docs/franchise-page-plan.md, phase 3) -------------------
# Which franchise entry each local episode is. A folder's match (its first
# season's entry) anchors it in a franchise; its numbered seasons are laid
# over the franchise's line of seasons from there, other seasons are found
# among the franchise's entries by name. Worked out from the stored
# franchise on every read, never with a network call; only a user's own
# pin (set_season_pin) is stored.

SEASON_GENERIC_LABELS = {"specials", "special", "ova", "oav", "extras", "others", "other"}
# The line a folder's numbered seasons walk: TV seasons first, then
# specials/OVAs (AoT's final chapters); a movie between seasons is stepped
# through but is no season (Made in Abyss S1 → Dawn of the Deep Soul → S2).
# Format None is an entry AniList has only announced.
LINE_FORMATS = (("TV", "TV_SHORT", "ONA", None), ("SPECIAL", "OVA"), ("MOVIE",))
ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth"}


def season_key(sea):
    return f"{sea['key']}|{sea['label']}"


def _edges(m):
    return ((m or {}).get("relations") or {}).get("edges") or []


def main_line(anchor_id, entries):
    """The seasons from `anchor_id` onward, following SEQUEL links inside
    the franchise (LINE_FORMATS' order). AniList links some next seasons
    only as OTHER (Cyberpunk: Edgerunners 2): those count when the title
    says so (numbered_season_of). Pure: entries as build_franchise
    returns them."""
    by_id = {m["id"]: m for m in entries}
    cur = by_id.get(anchor_id)
    if not cur:
        return []
    line, seen = [cur], {cur["id"]}
    while True:
        near = [(e["relationType"], by_id[e["node"]["id"]]) for e in _edges(cur)
                if e["node"]["id"] in by_id and e["node"]["id"] not in seen]
        sequels = [m for rel, m in near if rel == "SEQUEL"]
        nxt = next((m for group in LINE_FORMATS for m in sequels if m.get("format") in group), None)
        if nxt is None:
            nxt = next((m for rel, m in near if rel == "OTHER" and m.get("format") in LINE_FORMATS[0]
                        and franchise.numbered_season_of(cur, m)), None)
        if nxt is None:
            return line
        seen.add(nxt["id"])
        cur = nxt
        if nxt.get("format") != "MOVIE":
            line.append(nxt)


def names_season(m, label):
    """True when an entry's title names the local season label: "Season 2"
    matches "… Season 2", "… 2nd Season" and "… Second Season"; "Part 2"
    matches "… Part 2"."""
    hay = " ".join(franchise._titles(m)).lower()
    lm = re.fullmatch(r"(season|part)\s+(\d+)", (label or "").strip().lower())
    if not lm:
        return False
    word, n = lm.group(1), int(lm.group(2))
    pats = [rf"\b{word}\s+{n}\b"]
    if word == "season":
        pats.append(rf"\b{n}(st|nd|rd|th)\s+season\b")
        if n in ORDINALS:
            pats.append(rf"\b{ORDINALS[n]}\s+season\b")
    return any(re.search(p, hay) for p in pats)


def numbered_seasons(show):
    """The seasons that walk the line, in watch order: Season N / Part N /
    Final … (season ≥ 1; scan order is season_rank). Specials, named arcs
    and movies are found by name instead (named_entry)."""
    if show.get("kind") != "show":
        return []
    return [sea for sea in show["seasons"] if sea["episodes"] and (sea.get("season") or 0) >= 1]


def _range(first, last, m, offset):
    return {"id": m["id"], "first_ep": first, "last_ep": last, "offset": offset, "total": m.get("episodes")}


def map_numbered_seasons(show, line):
    """season key → ranges: lay the folder's numbered seasons over the line
    in order. Each season starts at the next unused entry (a partly
    downloaded season doesn't shift the next one) and spans as many entries
    as its episodes need: AoT's 22-file Season 3 is AniList's 12 + 10.
    Absolute numbering (S2 as E26–E37) is recognised by the first number
    reaching the season's absolute start. A season that needs more entries
    than the line has is left unmapped from there on: never guess."""
    out, pos = {}, 0
    for sea in numbered_seasons(show):
        nums = sorted(e["number"] for e in sea["episodes"]
                      if isinstance(e.get("number"), int) and e["number"] >= 1)
        if not nums or pos >= len(line):
            break
        # A folder that starts past Season 1 (the rest on another drive,
        # Decision 42, or not downloaded): start at the entry whose title
        # names this season — only when one does, never by guessing.
        if not pos and (sea.get("season") or 0) > 1:
            pos = next((j for j, m in enumerate(line) if names_season(m, sea["label"])), 0)
        # AniList's air order can interleave a side arc (Owarimonogatari S1
        # → Tsukimonogatari → Owarimonogatari Second Season): when an entry
        # close ahead names this season and the next one doesn't, the
        # season starts there.
        if pos and not names_season(line[pos], sea["label"]):
            ahead = next((j for j in range(pos + 1, min(pos + 3, len(line)))
                          if names_season(line[j], sea["label"])), None)
            if ahead is not None:
                pos = ahead
        start = 1
        before = [m.get("episodes") for m in line[:pos]]
        if pos and all(before) and nums[0] >= 1 + sum(before):
            start = 1 + sum(before)
        ranges, left = [], nums
        while left and pos < len(line):
            m = line[pos]
            total = m.get("episodes")
            pos += 1
            if not total:
                ranges.append(_range(start, None, m, 1 - start))
                left = []
                break
            end = start + total - 1
            rest = [n for n in left if n > end]
            ranges.append(_range(start, end if rest else None, m, 1 - start))
            left, start = rest, end + 1
        if left:
            break  # the line ran out: this season (and the rest) stays unmapped
        out[season_key(sea)] = ranges
    return out


def named_entry(label, entries):
    """The one franchise entry whose title or alternative name contains a
    named season's label ("Specials/Lost Girls" → "Attack on Titan: Lost
    Girls"; "Together with Papa" is only a synonym of Made in Abyss' "Papa
    to Issho"), else None. Generic labels (Specials, OVA) and ambiguous
    names stay unmapped."""
    want = norm_title(label)
    if not want or label.strip().lower() in SEASON_GENERIC_LABELS:
        return None
    hits = [m for m in entries
            if any(want in norm_title(t) for t in franchise._titles(m) + (m.get("synonyms") or []))]
    return hits[0] if len(hits) == 1 else None


def map_folder(show, anchor_id, entries, pins=None):
    """season key → [range]: which entry each part of each season of one
    folder is. A range is {"id", "first_ep", "last_ep" (None = to the end),
    "offset" (local number + offset = the entry's own number), "total"}.
    `entries` is the anchor's franchise ([] while it isn't built yet:
    then only a single-season folder maps, wholly onto its match). `pins`
    (season key → ranges, [] = no entry) are the user's word and win.
    Seasons that map to nothing are absent: their files stay local only."""
    pins = pins or {}
    filled = [sea for sea in show["seasons"] if sea["episodes"]]
    out = {}
    by_id = {m["id"]: m for m in entries}
    if anchor_id:
        known = anchor_id in by_id
        if known:
            out.update(map_numbered_seasons(show, main_line(anchor_id, entries)))
        numbered = {season_key(x) for x in numbered_seasons(show)}
        for sea in filled:
            key = season_key(sea)
            if key in out:
                continue
            if len(filled) == 1 and not (known and key in numbered):
                out[key] = [_range(1, None, by_id.get(anchor_id) or {"id": anchor_id}, 0)]
            elif known and key not in numbered:
                m = named_entry(sea["label"], entries)
                if m:
                    out[key] = [_range(1, None, m, 0)]
    for key, ranges in pins.items():
        out[key] = ranges
    return {k: v for k, v in out.items() if v}


def release_units(s):
    """The one place a folder's episodes become entries: one unit per
    entry, {"media_id", "eps": [(episode, the entry's own number)],
    "seasons": [season index], "total": the entry's episode count or None},
    in season order. Reads the ranges merge_db attached
    (`season["releases"]`). Episodes outside every range (specials numbered
    0, unmapped seasons) belong to no unit, so nothing is ever synced for
    them. Progress sync, completion, reconcile, AniList watched marks and
    library counts all read this instead of counting seasons."""
    units = {}
    for i, sea in enumerate(s.get("seasons") or []):
        ranges = sea.get("releases") or []
        for ep in sea["episodes"]:
            n = ep.get("number")
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                continue
            r = next((r for r in reversed(ranges)
                      if r["first_ep"] <= n and (r["last_ep"] is None or n <= r["last_ep"])), None)
            if not r or n + r["offset"] < 1:
                continue
            u = units.setdefault(r["id"], {"media_id": r["id"], "eps": [], "seasons": [], "total": None})
            u["total"] = u["total"] or r.get("total")
            u["eps"].append((ep, n + r["offset"]))
            if i not in u["seasons"]:
                u["seasons"].append(i)
    return list(units.values())


def unit_fits(unit, total):
    """A unit may only speak for its entry when it fits inside it: a known
    total rules out more files, or higher numbers, than the entry has (a
    wrong mapping never pushes a too-big count). An unknown total (airing)
    can't rule anything out (Decision 21)."""
    if not total:
        return True
    return len(unit["eps"]) <= total and max(n for _, n in unit["eps"]) <= total


def unit_of_episode(s, ep_id):
    """(unit, the entry's own episode number) for one episode, or (None, None)."""
    for u in release_units(s):
        for ep, n in u["eps"]:
            if ep["id"] == ep_id:
                return u, n
    return None, None


def own_listing(m, earlier):
    """AniList's episode listing (stills, titles) for an entry, when it is
    the entry's own: every number inside the entry, and no still shared
    with an earlier entry of the franchise. AniList hands many sequels
    their first season's listing (Attack on Titan, Kaguya-sama) or the
    next season's (The Apothecary Diaries). None otherwise."""
    parsed = metadata.parse_seps(m.get("streamingEpisodes") or [])
    if not parsed["thumbs"]:
        return None
    count = m.get("episodes")
    if count and any(int(k) > count for k in parsed["thumbs"]):
        return None
    seen = {url for e in earlier for url in (metadata.parse_seps(e.get("streamingEpisodes") or [])["thumbs"] or {}).values()}
    if seen & set(parsed["thumbs"].values()):
        return None
    return parsed


def episode_listing(m, earlier, anizip):
    """An entry's episode stills and titles as remote urls, {"thumbs",
    "titles", "source"}: its own AniList listing (own_listing), else
    ani.zip's (`anizip(id)` → {str(n): {"image", "title"}}, None when
    unreachable: then None, to retry later)."""
    parsed = own_listing(m, earlier)
    if parsed:
        return dict(parsed, source="anilist")
    eps = anizip(m["id"])
    if eps is None:
        return None
    eps = anizip_pick(eps, m.get("episodes"))
    return {"thumbs": {k: v["image"] for k, v in eps.items() if v.get("image")} or None,
            "titles": {k: v["title"] for k, v in eps.items() if v.get("title")} or None,
            "source": "anizip"}


def anizip_pick(eps, count):
    """ani.zip's episodes of an entry by its own numbers. Plain keys ("1")
    are the episodes, except where TVDB files the entry as specials: then
    its episodes are "O1".."On" and "1" may be something else entirely
    (Kaguya-sama's "The First Kiss That Never Ends": "1" is the theatrical
    cut, "O1".."O4" the four episodes). The keyed run that covers the
    entry's episode count wins; plain keys when both or neither do."""
    plain = {k: v for k, v in eps.items() if k.isdigit()}
    other = {k[1:]: v for k, v in eps.items() if k[:1] == "O" and k[1:].isdigit()}
    if count and len(plain) < count <= len(other):
        return other
    return plain


def season_art(ranges, arts):
    """A season's stills and titles keyed by its own episode numbers,
    gathered from every entry it maps to (AoT S3's second entry, at offset
    −12, fills E13–E22). `arts`: media id → {"thumbs", "titles"}."""
    thumbs, titles = {}, {}
    for r in ranges:
        art = arts.get(r["id"]) or {}
        for got, dst in ((art.get("thumbs"), thumbs), (art.get("titles"), titles)):
            for k, v in (got or {}).items():
                n = int(k) - r["offset"]
                if v and n >= r["first_ep"] and (r["last_ep"] is None or n <= r["last_ep"]):
                    dst.setdefault(str(n), v)
    return thumbs or None, titles or None


def franchise_entries(ids):
    """media id → its franchise's entries in page order, for many ids in
    one read ([] for an id no stored franchise holds)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    with database.db() as c:
        member = dict(c.execute("SELECT media_id, franchise_id FROM franchise_members"))
        fids = {member[i] for i in ids if i in member}
        rows = c.execute(f"""SELECT f.franchise_id, m.data FROM franchise_members f
                             JOIN media m ON m.id = f.media_id
                             WHERE f.franchise_id IN ({",".join("?" * len(fids))})
                             ORDER BY f.franchise_id, f.pos""", tuple(fids)).fetchall() if fids else []
    by_fid = {}
    for fid, data in rows:
        by_fid.setdefault(fid, []).append(json.loads(data))
    return {i: by_fid.get(member.get(i), []) for i in ids}


def season_pins():
    """show id → season key → the user's ranges for it ([] = no entry)."""
    out = {}
    with database.db() as c:
        rows = c.execute("""SELECT r.show_id, r.sea_key, r.first_ep, r.last_ep, r.media_id, r.ep_offset, m.data
                            FROM season_releases r LEFT JOIN media m ON m.id = r.media_id
                            WHERE r.source = 'manual' ORDER BY r.show_id, r.sea_key, r.first_ep""").fetchall()
    for sid, key, first, last, mid, offset, data in rows:
        ranges = out.setdefault(sid, {}).setdefault(key, [])
        if mid is not None:
            total = json.loads(data).get("episodes") if data else None
            ranges.append({"id": mid, "first_ep": first, "last_ep": last, "offset": offset or 0, "total": total})
    return out


def entry_art_all(ids):
    """media id → {"thumbs", "titles"} (local /epimg/ urls) for stored entries."""
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    with database.db() as c:
        rows = c.execute(f"SELECT media_id, thumbs, titles FROM entry_art WHERE media_id IN ({','.join('?' * len(ids))})",
                         ids).fetchall()
    return {r[0]: {"thumbs": json.loads(r[1]) if r[1] else None, "titles": json.loads(r[2]) if r[2] else None}
            for r in rows}


def attach_releases(scan):
    """merge_db's part: every season of every folder gets its ranges
    (`releases`) and the stills/titles of the entries they cover."""
    anchors = {s["id"]: (s.get("meta") or {}).get("anilist_id") for s in scan}
    entries = franchise_entries(anchors.values())
    pins = season_pins()
    maps = {s["id"]: map_folder(s, anchors[s["id"]], entries.get(anchors[s["id"]]) or [], pins.get(s["id"]))
            for s in scan}
    arts = entry_art_all(r["id"] for m in maps.values() for ranges in m.values() for r in ranges)
    for s in scan:
        for sea in s["seasons"]:
            sea["releases"] = maps[s["id"]].get(season_key(sea), [])
            sea["thumbs"], sea["titles"] = season_art(sea["releases"], arts)


def set_season_pin(show_id, sea_key, anilist_id=None, none=False):
    """The "wrong entry?" repair for one season of a folder: pin it to a
    user-chosen AniList entry (`none` = it is no entry: its files stay
    local only), or with neither hand it back to the automatic mapping.
    Watch facts are untouched; only which entry they count for changes."""
    show = next((s for s in library.scan_library() if s["id"] == show_id), None)
    if not show:
        return {"error": "unknown show"}
    sea = next((x for x in show["seasons"] if season_key(x) == sea_key), None)
    if not sea:
        return {"error": "no such season"}
    m = None
    if anilist_id is not None:
        try:
            m = franchise.media_entry(anilist_id)
        except Exception as e:
            return {"error": anilist.friendly_error(e)}
        if not m:
            return {"error": f"AniList has no media with id {anilist_id}"}
    nums = [e["number"] for e in sea["episodes"] if isinstance(e.get("number"), int) and e["number"] >= 1]
    # Absolute numbering (a "Season 2" folder holding E26–E37) counts from
    # its first file; otherwise the season's numbers are the entry's.
    offset = 1 - min(nums) if m and nums and m.get("episodes") and max(nums) > m["episodes"] else 0
    with database.db() as c:
        c.execute("DELETE FROM season_releases WHERE show_id=? AND sea_key=?", (show_id, sea_key))
        if m or none:
            c.execute("""INSERT INTO season_releases(show_id, sea_key, first_ep, last_ep, media_id, ep_offset, source)
                         VALUES(?,?,?,?,?,?,'manual')""", (show_id, sea_key, 1, None, anilist_id, offset))
    if m:
        threading.Thread(target=anilist.background_work(refresh_entry_art), daemon=True).start()
    title = m and ((m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji"))
    return {"ok": True, "anilist_id": anilist_id, "title": title, "cleared": not (m or none)}


ANIZIP_URL = "https://api.ani.zip/mappings?anilist_id=%d"


def anizip_episodes(anilist_id):
    """{key: {"image", "title"}} for one entry from ani.zip, the public
    AniList-id-keyed episode index (TVDB artwork, no API key); only the
    AniList id is sent. Keys are "n" and "On" (see anizip_pick). {} when it
    has nothing; None when it couldn't be reached (not cached, retried
    later)."""
    key = f"anizip:v2:{anilist_id}"
    cached = database.cache_get(key, metadata.EPISODE_META_TTL)
    if cached is not None:
        return cached
    try:
        req = Request(ANIZIP_URL % anilist_id,
                      headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except HTTPError as e:
        if e.code != 404:
            return None
        data = {}
    except Exception:
        return None
    out = {}
    for k, v in ((data or {}).get("episodes") or {}).items():
        num = k[1:] if k[:1] == "O" else k
        if num.isdigit() and int(num) >= 1 and isinstance(v, dict):
            out[k] = {"image": v.get("image") or None, "title": (v.get("title") or {}).get("en") or None}
    database.cache_put(key, out)
    return out


_entry_art_lock = threading.Lock()
# Entries waiting for their stills: "now" = opened on a page (first),
# "later" = what local seasons map to. One pass at a time works the queue;
# a caller that finds a pass running leaves its entries to it.
_art_queue = {"now": set(), "later": set()}
_art_queue_lock = threading.Lock()


def _art_next():
    with _art_queue_lock:
        for k in ("now", "later"):
            if _art_queue[k]:
                mid = min(_art_queue[k])
                _art_queue[k].discard(mid)
                return mid
    return None


def _store_entry_art(mid, anizip):
    """Fetch and store one entry's stills unless it has them (an entry
    without stills is retried once EPISODE_META_TTL has passed). True when
    stored."""
    with database.db() as c:
        row = c.execute("SELECT thumbs, ts FROM entry_art WHERE media_id=?", (mid,)).fetchone()
    if row and (row[0] or row[1] >= time.time() - metadata.EPISODE_META_TTL):
        return False
    m = franchise.media_entry(mid, pause=2.2)
    if not m:
        return False
    fr = franchise.franchise_of(mid)
    earlier = []
    for e in (fr[1] if fr else []):
        if e["id"] == mid:
            break
        earlier.append(e)
    raw = episode_listing(m, earlier, anizip)
    if raw is None:
        return False
    got = metadata.commit_thumbs(mid, raw, prefix="z" if raw["source"] == "anizip" else "")
    thumbs = {str(n): u for n, u in got.items()} or None
    with database.db() as c:
        c.execute("""INSERT INTO entry_art(media_id, thumbs, titles, source, ts) VALUES(?,?,?,?,?)
                     ON CONFLICT(media_id) DO UPDATE SET thumbs=excluded.thumbs, titles=excluded.titles,
                     source=excluded.source, ts=excluded.ts""",
                  (mid, json.dumps(thumbs) if thumbs else None,
                   json.dumps(raw["titles"]) if raw["titles"] else None, raw["source"], time.time()))
    return True


def refresh_entry_art(scan=None, anizip=None, ids=None):
    """Background: fetch the episode stills of every entry a local season
    maps to (or of `ids`: entries opened on a franchise page, which go
    first) and store them (entry_art), so pages draw them without a
    network call. Entries asked for while another pass runs are left to
    that pass, never dropped. Returns the number of entries stored."""
    anizip = anizip or anizip_episodes
    if ids is None:
        if scan is None:
            scan = library.scan_library()
            library.merge_db(scan)
        ids, kind = {r["id"] for s in scan for sea in s["seasons"] for r in sea.get("releases") or []}, "later"
    else:
        kind = "now"
    with _art_queue_lock:
        _art_queue[kind].update(ids)
    did = 0
    while True:
        if not _entry_art_lock.acquire(blocking=False):
            return did  # the running pass takes our entries
        try:
            failed, last = 0, None
            while (mid := _art_next()) is not None:
                try:
                    did += _store_entry_art(mid, anizip)
                except Exception as e:
                    failed, last = failed + 1, e
            if failed:
                # One line per pass: an outage fails every entry the same way.
                util.log("stills for %d entr%s failed: %s" % (failed, "y" if failed == 1 else "ies", last))
        finally:
            _entry_art_lock.release()
        with _art_queue_lock:
            # Asked for between our last look and the release: go again.
            if not (_art_queue["now"] or _art_queue["later"]):
                return did


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
