"""Franchises: AniList entries grouped by relation, cached in the database."""

import json
import re
import threading
import time
from urllib.error import HTTPError

from . import anilist, database, library, mapping, pages, streaming, util


# One franchise entry (docs/franchise-page-plan.md): everything the franchise
# page draws for it, plus the typed relation edges the franchise is built
# from. tests/record_fixtures.py records the test fixtures with this query.
FRANCHISE_FIELDS = """id title{romaji english native} synonyms format status episodes duration averageScore
season seasonYear startDate{year month day} nextAiringEpisode{episode airingAt}
coverImage{extraLarge color} bannerImage description
relations{edges{relationType node{id type format status startDate{year month day} title{romaji english}}}}
streamingEpisodes{title thumbnail}"""
FRANCHISE_QUERY_ID = "query($id:Int){Media(id:$id,type:ANIME){" + FRANCHISE_FIELDS + "}}"
# Many entries in one request (TODO 61): a franchise walk asks for every
# entry it just found at once instead of one request each — One Piece took
# two minutes at one request per entry under the rate limit.
FRANCHISE_QUERY_MANY = ("query($ids:[Int]){Page(perPage:50){media(id_in:$ids,type:ANIME){"
                        + FRANCHISE_FIELDS + "}}}")
# Relations that keep two entries on one franchise page. SPIN_OFF,
# ALTERNATIVE (FMA vs Brotherhood) and CHARACTER do not. OTHER joins only
# an entry of these formats whose title is this one's plus a season number
# (AniList links "Cyberpunk: Edgerunners 2" as OTHER, both ways).
FRANCHISE_RELATIONS = {"SEQUEL", "PREQUEL", "PARENT", "SIDE_STORY", "SUMMARY"}
# AniList's two sides of a link can disagree: Attack on Titan calls Junior
# High a SPIN_OFF, Junior High calls AoT its PARENT. Either side naming one
# of these keeps the two apart.
FRANCHISE_SEPARATE = {"SPIN_OFF", "ALTERNATIVE", "CHARACTER"}
FRANCHISE_OTHER_FORMATS = {"TV", "TV_SHORT", "ONA", "MOVIE"}
# What a franchise page holds: no music videos. None is a just-announced
# entry AniList hasn't given a format yet.
FRANCHISE_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "SPECIAL", "MOVIE", None}


# --- Franchises (docs/franchise-page-plan.md) -------------------------------
# A franchise is every AniList entry one show page holds: seasons, movies,
# specials, OVAs and shorts, connected by FRANCHISE_RELATIONS. It is built
# in the background from AniList and stored; pages only read the tables.

FRANCHISE_LIMIT = 60
MEDIA_TTL = 7 * 24 * 3600
MEDIA_TTL_CHANGING = 24 * 3600  # airing or announced: dates, counts and new seasons move
FRANCHISE_LOOP_EVERY = 6 * 3600
_franchise_lock = threading.Lock()
_SEASON_SUFFIX = re.compile(r"(?:season\s*)?\d+|\d+(?:st|nd|rd|th)\s+season"
                            r"|(?:second|third|fourth|fifth|sixth)\s+season", re.I)


def _media_ttl(m):
    finished = (m or {}).get("status") in ("FINISHED", "CANCELLED")
    return MEDIA_TTL if finished else MEDIA_TTL_CHANGING


def media_entry(mid, pause=0):
    """One AniList entry (FRANCHISE_QUERY_ID's shape) from the `media`
    table, fetched again once stale. None when AniList has no such entry.
    A failed fetch falls back to the stale copy. `pause` seconds follow a
    network fetch, for background passes that walk many entries."""
    with database.db() as c:
        row = c.execute("SELECT data, ts FROM media WHERE id=?", (mid,)).fetchone()
    stale = json.loads(row[0]) if row else None
    if stale and time.time() - row[1] < _media_ttl(stale):
        return stale
    try:
        m = (anilist.anilist_request({"query": FRANCHISE_QUERY_ID, "variables": {"id": mid}}).get("data") or {}).get("Media")
    except HTTPError as e:
        if e.code != 404:
            if stale:
                return stale
            raise
        m = None
    except Exception:
        if stale:
            return stale
        raise
    finally:
        time.sleep(pause)
    if m:
        with database.db() as c:
            c.execute("INSERT INTO media(id, data, ts) VALUES(?,?,?) ON CONFLICT(id)"
                      " DO UPDATE SET data=excluded.data, ts=excluded.ts", (mid, json.dumps(m), time.time()))
    return m


def prefetch_media(ids):
    """Fetch the entries of `ids` the `media` table lacks (or holds stale)
    in batches of 50 and store them, so media_entry answers from the table.
    Best effort: whatever this misses, media_entry fetches on its own."""
    ids = sorted(set(ids))
    if not ids:
        return
    q = ",".join("?" * len(ids))
    with database.db() as c:
        rows = c.execute(f"SELECT id, data, ts FROM media WHERE id IN ({q})", ids).fetchall()
    fresh = {r[0] for r in rows if time.time() - r[2] < _media_ttl(json.loads(r[1]))}
    want = [i for i in ids if i not in fresh]
    for k in range(0, len(want), 50):
        try:
            data = anilist.anilist_request({"query": FRANCHISE_QUERY_MANY, "variables": {"ids": want[k:k + 50]}})
        except Exception as e:
            util.log("franchise prefetch failed: %s" % e)
            return
        got = (((data or {}).get("data") or {}).get("Page") or {}).get("media") or []
        now = time.time()
        with database.db() as c:
            c.executemany("INSERT INTO media(id, data, ts) VALUES(?,?,?) ON CONFLICT(id)"
                          " DO UPDATE SET data=excluded.data, ts=excluded.ts",
                          [(m["id"], json.dumps(m), now) for m in got if m and m.get("id")])


def _titles(m):
    t = m.get("title") or {}
    return [x.strip() for x in (t.get("english"), t.get("romaji")) if x and x.strip()]


def numbered_season_of(a, b):
    """True when one entry's title is the other's plus a season number:
    "Cyberpunk: Edgerunners" / "Cyberpunk: Edgerunners 2" (or "... Season
    2", "... 2nd Season"). Lets an OTHER link join a franchise."""
    for x, y in ((a, b), (b, a)):
        for root in _titles(x):
            for t in _titles(y):
                if t.lower().startswith(root.lower()):
                    rest = t[len(root):].strip(" :-\u2013\u2014")
                    if rest and _SEASON_SUFFIX.fullmatch(rest):
                        return True
    return False


def _franchise_order(m):
    d = m.get("startDate") or {}
    return (d.get("year") or 9999, d.get("month") or 99, d.get("day") or 99, m["id"])


def _candidates(m, skip):
    """The related entries of m a franchise walk would follow: (id, OTHER?)."""
    for e in (m.get("relations") or {}).get("edges") or []:
        node, rel = e.get("node") or {}, e.get("relationType")
        nid = node.get("id")
        if node.get("type") != "ANIME" or node.get("format") not in FRANCHISE_FORMATS or nid in skip:
            continue
        other = rel == "OTHER" and node.get("format") in FRANCHISE_OTHER_FORMATS
        if rel in FRANCHISE_RELATIONS or other:
            yield nid, other


def build_franchise(start_id, fetch, prefetch=None):
    """Every entry of start_id's franchise, in page order (release date;
    undated entries last). fetch(id) returns one entry or None. A link
    counts when neither side names it FRANCHISE_SEPARATE, so the same
    franchise comes back whichever of its entries it starts from.
    `prefetch(ids)`, when given, is handed every entry the walk is about to
    fetch, ahead of time (prefetch_media: one request for all of them)."""
    start = fetch(start_id)
    if not start:
        return []
    found, todo, gone = {start["id"]: start}, [start], set()
    while todo and len(found) < FRANCHISE_LIMIT:
        if prefetch:
            skip = set(found) | gone
            prefetch({nid for t in todo for nid, _ in _candidates(t, skip)})
        m = todo.pop(0)
        for nid, other in _candidates(m, ()):
            if nid in found or nid in gone:
                continue
            n = fetch(nid)
            if not n:
                gone.add(nid)
                continue
            if other and not numbered_season_of(m, n):
                continue
            if any(b.get("relationType") in FRANCHISE_SEPARATE and (b.get("node") or {}).get("id") == m["id"]
                   for b in (n.get("relations") or {}).get("edges") or []):
                continue
            found[nid] = n
            todo.append(n)
    if todo:
        util.log("franchise of %s stopped at %d entries" % (start_id, FRANCHISE_LIMIT))
    return sorted(found.values(), key=_franchise_order)


def store_franchise(entries):
    """Save one built franchise. Its id is its first entry's. Entries that
    belonged to another franchise move here; a franchise left without
    entries is dropped. Returns the franchise id."""
    if not entries:
        return None
    ids, fid, now = [m["id"] for m in entries], entries[0]["id"], time.time()
    q = ",".join("?" * len(ids))
    with database.db() as c:
        before = {r[0] for r in c.execute(
            f"SELECT DISTINCT franchise_id FROM franchise_members WHERE media_id IN ({q})", ids)}
        c.execute("DELETE FROM franchise_members WHERE franchise_id=?", (fid,))
        c.execute(f"DELETE FROM franchise_members WHERE media_id IN ({q})", ids)
        c.executemany("INSERT INTO franchise_members(media_id, franchise_id, pos) VALUES(?,?,?)",
                      [(mid, fid, pos) for pos, mid in enumerate(ids)])
        # The fetcher stores entries with their fetch time; this only fills
        # in ones it didn't (tests) and never makes a stale entry look fresh.
        c.executemany("INSERT INTO media(id, data, ts) VALUES(?,?,?) ON CONFLICT(id)"
                      " DO UPDATE SET data=excluded.data", [(m["id"], json.dumps(m), now) for m in entries])
        c.execute("INSERT INTO franchises(id, ts) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts",
                  (fid, now))
        for old in before - {fid}:
            if not c.execute("SELECT 1 FROM franchise_members WHERE franchise_id=?", (old,)).fetchone():
                c.execute("DELETE FROM franchises WHERE id=?", (old,))
    return fid


def tag_franchises(cards):
    """Library cards learn their entry (`media_id`: the AniList entry, or a
    local folder's match) and its `franchise_id`, so the library shows one
    card per franchise. None where no stored franchise holds the entry."""
    with database.db() as c:
        member = dict(c.execute("SELECT media_id, franchise_id FROM franchise_members"))
    for card in cards:
        mid = card["id"] if isinstance(card["id"], int) else card.get("media_id")
        card["media_id"] = mid
        card["franchise_id"] = member.get(mid)
    return cards


def franchise_of(mid):
    """(franchise id, entries in page order) for one entry, from the
    database only. None until the franchise has been built."""
    with database.db() as c:
        row = c.execute("SELECT franchise_id FROM franchise_members WHERE media_id=?", (mid,)).fetchone()
        if not row:
            return None
        data = c.execute("""SELECT m.data FROM franchise_members f JOIN media m ON m.id = f.media_id
                            WHERE f.franchise_id=? ORDER BY f.pos""", (row[0],)).fetchall()
    return row[0], [json.loads(d[0]) for d in data]


def _franchise_fresh(fid, entries):
    with database.db() as c:
        row = c.execute("SELECT ts FROM franchises WHERE id=?", (fid,)).fetchone()
    return bool(row) and time.time() - row[0] < min(_media_ttl(m) for m in entries)


def franchise_seeds(scan):
    """AniList entries the library knows of: matched folders and linked
    online releases. Their franchises are the ones kept up to date."""
    ids = {(s.get("meta") or {}).get("anilist_id") for s in scan}
    ids |= {link["media_id"] for link in streaming.stream_links_all()}
    ids.discard(None)
    return ids


def _background_fetch(mid):
    """media_entry for background walks: they wait while a page someone
    opened is being built (franchise_build_async), then keep a slow pace."""
    for _ in range(180):
        if not pages._building:
            break
        time.sleep(0.5)
    return media_entry(mid, pause=2.2)


def _background_prefetch(ids):
    """prefetch_media for background walks, with _background_fetch's manners:
    a page someone opened is built first. One request per walk step instead
    of one per entry (TODO 61) leaves the rate limit to the user's clicks."""
    for _ in range(180):
        if not pages._building:
            break
        time.sleep(0.5)
    prefetch_media(ids)


def refresh_franchises(seeds, fetch=None):
    """Build and store the franchise of every seed whose stored one is
    missing or stale (a day while any entry airs or is announced, else a
    week). One build covers all seeds in that franchise. Returns how many
    franchises were built; 0 when another pass is already running."""
    prefetch = None if fetch else _background_prefetch
    fetch = fetch or _background_fetch
    if not _franchise_lock.acquire(blocking=False):
        return 0
    try:
        done, built, failed, last = set(), 0, 0, None
        for mid in sorted(seeds):
            if mid in done:
                continue
            known = franchise_of(mid)
            if known and _franchise_fresh(*known):
                done.update(m["id"] for m in known[1])
                continue
            try:
                entries = build_franchise(mid, fetch, prefetch)
            except Exception as e:
                failed, last = failed + 1, e
                continue
            store_franchise(entries)
            done.update(m["id"] for m in entries)
            built += 1
        if failed:
            # One line per pass: an outage fails every franchise the same way.
            util.log("franchise refresh: %d failed: %s" % (failed, last))
        return built
    finally:
        _franchise_lock.release()


def franchise_refresh_loop():
    """Keeps franchises current between metadata passes, so a newly
    announced season or movie shows up without a restart."""
    while True:
        time.sleep(FRANCHISE_LOOP_EVERY)
        try:
            scan = library.scan_library()
            library.merge_db(scan)
            if refresh_franchises(franchise_seeds(scan)):
                library.merge_db(scan)
            pages.drop_premature_links(force=True)
            mapping.refresh_entry_art(scan)
        except Exception as e:
            util.log("franchise refresh loop: %s" % e)
