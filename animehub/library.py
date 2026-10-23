"""The local library: folder scan, database merge and the library spine."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import anicli, anilist, database, franchise, mapping, metadata, player, settings, streaming, util
from .config import config


VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".m4v"}
MOVIE_RE = re.compile(r"^(?:movies?|films?)$", re.I)
SPECIAL_RE = re.compile(r"^(?:specials?|ova|oav)$", re.I)
PART_RE = re.compile(r"^part\s*(\d+)$", re.I)
EP_RE = re.compile(r"\bE(\d{1,4})\b", re.I)
SE_EP_RE = re.compile(r"\bS\d{1,3}\s*E(\d{1,4})\b", re.I)
SEP_NUM_RE = re.compile(r"^(?:ep(?:isode)?)\.?\s*(\d{1,4})", re.I)
# "<title> Episode 5.mp4" — the name ani-cli gives its downloads.
EPISODE_WORD_RE = re.compile(r"\bEpisode\s*(\d{1,4})\b", re.I)
MIN_VALID_SCAN = 10
# Last scan's health (TODO 23): "ok", "missing" (root not a directory),
# "partial" (some directories unreadable). "root" is "changed" when the configured
# root differs from the one the database was indexed under. Surfaced in the
# library payload so the UI can show readable states instead of nothing.
scan_state = {"state": "ok", "walk_errors": 0, "root": "ok"}


def parse_ep_number(name):
    """Episode number from a filename: S01E02-style first (more specific),
    then a standalone E-token, else None. Fixes the S01E02 renumbering trap
    (TODO 23): plain EP_RE never matched "S01E02" — no word boundary between
    the season digits and E — so files fell back to lexical order numbering."""
    m = SE_EP_RE.search(name)
    if m:
        return int(m.group(1))
    m = EP_RE.search(name)
    if m:
        return int(m.group(1))
    m = EPISODE_WORD_RE.search(name)
    if m:
        return int(m.group(1))
    return None


# The last scan, keyed by the library's folder layout (TODO 61): every
# library, franchise and episode lookup scans, and a folder's mtime moves
# whenever a file in it is added, removed or renamed — the only changes a
# scan reads. Callers mutate what they get, so each hit is a fresh copy.
_scan_memo = {"sig": None, "json": None, "state": None}


def _library_sig(root):
    """Every walked folder (os.walk's rules: no dot-folders, no symlinked
    folders) with its mtime; an unreadable folder signs as None."""
    sig, stack = [], [str(root)]
    while stack:
        d = stack.pop()
        try:
            mtime = os.stat(d).st_mtime_ns
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if not e.name.startswith(".") and e.is_dir() and not e.is_symlink():
                            stack.append(e.path)
                    except OSError:
                        pass
            sig.append((d, mtime))
        except OSError:
            sig.append((d, None))
    return (str(root), tuple(sorted(sig)))


def scan_library():
    if config.LIBRARY is not None and config.LIBRARY.is_dir():
        sig = _library_sig(config.LIBRARY)
        if sig == _scan_memo["sig"]:
            scan_state.update(_scan_memo["state"])
            stored_root = settings.get_setting("library_root")
            scan_state["root"] = "changed" if stored_root and stored_root != str(config.LIBRARY) else "ok"
            return json.loads(_scan_memo["json"])
        shows = _scan_library()
        _scan_memo.update(sig=sig, json=json.dumps(shows), state=dict(scan_state))
        return shows
    _scan_memo["sig"] = None
    return _scan_library()


def _scan_library():
    shows = {}
    scan_state.update(state="ok", walk_errors=0, root="ok")
    if config.LIBRARY is None:
        # No library configured (PLAN v0.1 config work): a readable state,
        # not a traceback — the UI explains how to configure one.
        scan_state["state"] = "unconfigured"
        return []
    if not config.LIBRARY.is_dir():
        scan_state["state"] = "missing"
        return []
    stored_root = settings.get_setting("library_root")
    if stored_root and stored_root != str(config.LIBRARY):
        # Root-aware identity (Decision 23.7): the database was indexed under
        # a different root. Flag it — the config flow (step 3) must adopt a
        # root explicitly; a scan never silently tombstones the old one.
        scan_state["root"] = "changed"

    def walk_error(err):
        scan_state["walk_errors"] += 1

    for dirpath, dirnames, filenames in os.walk(config.LIBRARY, onerror=walk_error):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        videos = sorted(f for f in filenames if Path(f).suffix.lower() in VIDEO_EXTS)
        if not videos:
            continue
        rel = Path(dirpath).relative_to(config.LIBRARY)
        if not rel.parts:
            # Videos loose in the root: used to raise IndexError (parts was
            # empty). Keep them watchable as one-file shows instead; the
            # show id is namespaced "loose:" so it can never collide with a
            # real folder show later claiming the same name.
            for v in videos:
                n = parse_ep_number(v) or 1
                ep_rel = str(Path(v))
                vm = re.search(r"\(([^)]+)\)", v)
                ver = {"path": ep_rel, "label": vm.group(1) if vm else "default"}
                show_id = hashlib.sha1(f"loose:{v}".encode()).hexdigest()[:12]
                entry = shows.setdefault(
                    show_id,
                    {"id": show_id, "path": ep_rel, "title": Path(v).stem,
                     "collection": None, "kind": "show", "seasons": []},
                )
                sea = entry["seasons"][0] if entry["seasons"] else None
                if sea is None:
                    sea = {"key": 1, "season": 1, "label": "Season 1", "episodes": []}
                    entry["seasons"].append(sea)
                sea["episodes"].append(
                    {"id": hashlib.sha1(ep_rel.encode()).hexdigest()[:16], "rel": ep_rel,
                     "number": n, "files": [ver], "season": 1, "season_label": "Season 1"}
                )
            continue
        parts = list(rel.parts)
        show_parts = []
        season = None
        label = None
        kind = "show"
        movie_leaf = None
        for p in parts:
            if MOVIE_RE.match(p):
                kind = "movie"
                continue
            if kind == "movie":
                movie_leaf = p
                continue
            m = re.match(r"^season\s*(\d+)$", p, re.I)
            if m:
                season = int(m.group(1))
                label = None
                continue
            m = PART_RE.match(p)
            if m:
                label = f"Part {m.group(1)}"
                continue
            if SPECIAL_RE.match(p):
                season = 0
                label = None
                continue
            if season is not None:
                label = p
            else:
                show_parts.append(p)
        if kind == "movie":
            title = movie_leaf or f"{parts[0]} Movie"
            collection = parts[0] if len(parts) > 2 and parts[0] != title else None
            show_rel = "/".join(parts)
            season_key = 0
            season = 0
            season_label = "Movies"
        else:
            if not show_parts:
                show_parts = parts[:1]
            show_rel = "/".join(show_parts)
            title = show_parts[-1]
            collection = parts[0] if len(parts) > 1 and parts[0] != title else None
            if season is None:
                season = 1
            season_key = season
            season_label = label if label else ("Specials" if season == 0 else f"Season {season}")
        show_id = hashlib.sha1(show_rel.encode()).hexdigest()[:12]
        entry = shows.setdefault(
            show_id,
            {"id": show_id, "path": show_rel, "title": title, "collection": collection, "kind": kind, "seasons": []},
        )
        sea = next((s for s in entry["seasons"] if s["key"] == season_key and s["label"] == season_label), None)
        if sea is None:
            sea = {"key": season_key, "season": season, "label": season_label, "episodes": []}
            entry["seasons"].append(sea)
        for idx, v in enumerate(videos):
            n = parse_ep_number(v)
            if n is None:
                n = idx + 1
            ep_rel = str(rel / v)
            vm = re.search(r"\(([^)]+)\)", v)
            ver = {"path": ep_rel, "label": vm.group(1) if vm else "default"}
            existing = next((e for e in sea["episodes"] if e["number"] == n), None)
            if existing is None:
                sea["episodes"].append(
                    {"id": hashlib.sha1(ep_rel.encode()).hexdigest()[:16], "rel": ep_rel, "number": n,
                     "files": [ver], "season": season, "season_label": season_label}
                )
            else:
                existing["files"].append(ver)
    show_list = list(shows.values())
    for s in show_list:
        # Authoritative season order (TODO 24): the same rank the UI shows,
        # so autoplay's "next" is the section the user sees next.
        s["seasons"].sort(key=player.season_rank)
        for sea in s["seasons"]:
            seen = set()
            for ep in sea["episodes"]:
                ep["files"] = sorted(
                    ep["files"],
                    key=lambda f: (bool(re.search(r"\([^)]+\)", Path(f["path"]).name)), f["path"]),
                )
                ep["rel"] = ep["files"][0]["path"]
                ep["id"] = hashlib.sha1(ep["rel"].encode()).hexdigest()[:16]
                while ep["number"] in seen:
                    ep["number"] += 1
                seen.add(ep["number"])
            sea["episodes"].sort(key=lambda x: x["number"])
    show_list.sort(key=lambda x: (x["collection"] or x["title"]).lower())
    if scan_state["walk_errors"]:
        scan_state["state"] = "partial"
    elif stored_root is None and show_list:
        # First clean scan of a root on a database that never recorded one
        # (fresh install configured after migration 3 ran): record it, or
        # the changed-root guard (Decision 23.7) could never arm.
        settings.set_setting("library_root", str(config.LIBRARY))
    return show_list


def eps_of(show):
    for sea in show["seasons"]:
        for ep in sea["episodes"]:
            yield ep


def img_url(name):
    if not name:
        return None
    p = config.POSTERS / os.path.basename(name)
    try:
        return f"{name}?v={int(p.stat().st_mtime)}"
    except OSError:
        return name


def tombstone_counts():
    """Counts per kind for the library payload (Decision 17.3 surfaced)."""
    with database.db() as c:
        counts = {kind: n for kind, n in
                  c.execute("SELECT kind, COUNT(*) FROM tombstones GROUP BY kind")}
    return {"episode": counts.get("episode", 0), "show": counts.get("show", 0),
            "season": counts.get("season", 0)}


def ep_label(sea, ep):
    """A stable human label for history rows that must survive the file's
    removal (TODO 25): 'S1E3', or 'OVA · E2' for non-season folders."""
    lab = sea.get("label") or ""
    n = ep.get("number")
    if n is None:
        return lab or None
    if lab == "Episodes":  # an online release's own page
        return f"E{n}"
    if lab.startswith("Season"):
        return f"S{sea['season']}E{n}"
    return f"{lab} · E{n}" if lab else f"E{n}"


def merge_db(scan):
    with database.db() as c:
        rows, stamped = {}, {}
        for r in c.execute("SELECT id, watched, position, duration, last_played, show_id, label FROM episodes"):
            rows[r[0]] = r[1:5]
            stamped[r[0]] = (r[5], r[6])
        meta = {r[0]: r for r in c.execute(
            "SELECT show_id, anilist_id, title, year, score, overview, poster, banner, episodes_meta, format, fetched_at, episode_thumbs FROM shows_meta")}
        season_rows = {r[0] for r in c.execute("SELECT DISTINCT show_id FROM season_thumbs")}
    known = {ep["id"] for s in scan for ep in eps_of(s)}
    stamps = []
    if len(scan) >= MIN_VALID_SCAN:
        # Root-changed guard (TODO 23, Decision 23.7): a scan under a root
        # different from the one the database was indexed under must not
        # tombstone anything — the id space just moved. The old root's facts
        # stay intact until the new root is adopted explicitly (config flow,
        # step 3). A missing root simply produces an empty scan, which the
        # MIN_VALID_SCAN floor already shields.
        if scan_state.get("root") != "changed":
            now = util.utc_stamp()
            gone_shows = set(meta) - {s["id"] for s in scan}
            with database.db() as c:
                # Tombstones, not deletes (Decision 17.3): rows that fell out of
                # a valid scan stay in place and are marked gone. A re-download
                # restores the watch state untouched, and a moved/renamed file
                # keeps its facts until re-matched.
                # Streamed episodes ("stream:") never come from a scan.
                stale = [eid for eid in set(rows) - known
                         if not eid.startswith(("online:", "stream:"))]
                # DO NOTHING keeps ts = when the row first went missing; this
                # runs on every library poll and must not keep re-stamping it.
                ins = ("INSERT INTO tombstones(kind,key,ts) VALUES(?,?,?) "
                       "ON CONFLICT(kind,key) DO NOTHING")
                scan_ids = {s["id"] for s in scan}
                # Only rows not tombstoned yet: an unchanged library writes
                # nothing (TODO 61 — this runs on every library read).
                have = set(c.execute("SELECT kind, key FROM tombstones").fetchall())
                new_marks = [(kind, key, now) for kind, keys in
                             (("episode", stale), ("show", gone_shows), ("season", set(season_rows) - scan_ids))
                             for key in keys if (kind, key) not in have]
                if new_marks:
                    c.executemany(ins, new_marks)
                # IDs that are back in the scan clear their tombstone again —
                # only the ones actually tombstoned, not one DELETE per episode.
                back = {("episode", eid) for eid in known & set(rows)} | \
                       {("show", sid) for sid in scan_ids & set(meta)}
                if have & back:
                    c.executemany("DELETE FROM tombstones WHERE kind=? AND key=?", have & back)
    for s in scan:
        m = meta.get(s["id"])
        if m:
            s["meta"] = {"anilist_id": m[1], "title": m[2], "year": m[3], "score": m[4], "overview": m[5],
                         "episodes_meta": json.loads(m[8]) if m[8] else None, "format": m[9],
                         "episode_thumbs": json.loads(m[11]) if m[11] else None}
            s["poster"] = img_url(m[6])
            s["banner"] = img_url(m[7])
        else:
            s["meta"] = None
            s["poster"] = None
            s["banner"] = None
        for sea in s["seasons"]:
            for ep in sea["episodes"]:
                r = rows.get(ep["id"])
                ep["watched"] = bool(r[0]) if r else False
                ep["position"] = r[1] if r else 0
                ep["duration"] = r[2] if r else 0
                ep["last_played"] = r[3] if r else None
                if r is not None:
                    # Identity stamp (TODO 25): a played episode learns which
                    # show/label it belongs to, so its history row stays
                    # human-readable even after the file leaves the disk.
                    stamp = (s["id"], ep_label(sea, ep))
                    if stamped.get(ep["id"]) != stamp:
                        stamps.append((ep["id"], *stamp))
    # Which entry each season is, and its stills (phase 3): needs the match
    # (meta) set above.
    mapping.attach_releases(scan)
    if stamps:
        with database.db() as c:
            c.executemany(
                """INSERT INTO episodes(id,show_id,label) VALUES(?,?,?)
                   ON CONFLICT(id) DO UPDATE SET show_id=excluded.show_id, label=excluded.label""",
                stamps)


def spine_build(entries, scan, streamed=frozenset(), online=False, missed=frozenset(), favs=frozenset()):
    """Merge the user's AniList lists with the local library — Decision 4's
    spine, release-first (Decision 19): one card per AniList entry, with a
    source-capability badge (installed / partial / not installed) derived
    from how much of that release exists on disk, and local shows that
    aren't on any list surfaced as `untracked` (matched-but-unlisted shows
    land there too — they are part of the universe without being tracked).
    With ani-cli (`online`) a release with nothing on disk is "online" —
    linked already (`streamed`) or matched on first need — unless ani-cli
    has no match for it (`missed`). Counts are of disk episodes only (scan
    may carry merged online episodes).
    Pure function: entries come from the cache, scan from scan_library()."""
    by_media = {}
    for s in scan:
        # Per entry (release_units): a multi-season folder feeds each entry
        # its seasons map to its own disk count, never the folder total.
        # A folder nothing maps for yet (its franchise isn't built) still
        # counts for its own match, as a whole.
        units = [(u["media_id"], [ep for ep, _ in u["eps"]]) for u in mapping.release_units(s)] or \
            [((s.get("meta") or {}).get("anilist_id"), list(eps_of(s)))]
        for aid, held in units:
            if not aid:
                continue
            eps = sum(1 for ep in held if streaming.disk_ep(ep))
            cur = by_media.get(aid)
            if cur is None or eps > cur[1]:
                by_media[aid] = (s, eps)
    listed = {e["id"] for e in entries}
    out = []
    for e in entries:
        loc = by_media.get(e["id"])
        installed = loc[1] if loc else 0
        if loc and e.get("episodes") and installed >= e["episodes"]:
            cap = "installed"
        elif installed:
            cap = "partial"
        elif e["id"] in streamed or (online and e["id"] not in missed):
            cap = "online"
        else:
            cap = "none"
        out.append({"id": e["id"], "title": e.get("title"), "status": e.get("status"),
                    "progress": e.get("progress") or 0, "score": e.get("score") or 0,
                    "episodes": e.get("episodes"), "format": e.get("format"),
                    "year": e.get("year"), "airing": e.get("airing"),
                    "genres": e.get("genres") or [],
                    "next_episode": e.get("next_episode"),
                    "poster": e.get("poster"), "banner": e.get("banner"),
                    "updated": e.get("updated"),
                    "favourite": e["id"] in favs,
                    "capability": cap,
                    "installed": installed,
                    "local_show_id": loc[0]["id"] if loc else None,
                    "stream_show_id": f"aid:{e['id']}" if e["id"] in streamed else None})
    # Untracked = not on any of the user's lists. A second folder matched to
    # an already-listed release is a fragment of a tracked release, not an
    # untracked show — the card deep-links to the fuller one.
    untracked = [s["id"] for s in scan
                 if (s.get("meta") or {}).get("anilist_id") not in listed
                 and not any(u["media_id"] in listed for u in mapping.release_units(s))]
    return {"entries": out, "untracked": untracked, "mode": "anilist"}


def apply_anilist_watched(shows, entries):
    """Settings → Advanced AniList → "Watched marks from AniList": episodes
    up to their entry's progress read as watched, later ones as unwatched.
    Display only — hub.db keeps the local marks, so switching back restores
    them. Per entry (release_units), with the same fit rule as progress
    sync, so each season of a folder follows its own entry."""
    prog = {e["id"]: (e.get("progress") or 0, e.get("episodes")) for e in entries}
    for s in shows:
        for u in mapping.release_units(s):
            if u["media_id"] not in prog:
                continue
            done, total = prog[u["media_id"]]
            if not mapping.unit_fits(u, total):
                continue
            for ep, n in u["eps"]:
                ep["watched"] = n <= done


def spine_favourites(cached):
    """AniList favourite ids for the spine: the cached list's favourites,
    overridden by in-app edits made after that cache was fetched, so a heart
    toggled on a show page shows on its card before the next list refresh."""
    favs = set(cached.get("favourites") or [])
    ts = cached.get("_ts")
    if not ts:
        return favs
    since = datetime.fromtimestamp(ts, timezone.utc).strftime(util.CANONICAL_TS)
    for ref, cur in curation_all().items():
        if not ref.startswith("aid:") or (cur.get("updated_at") or "") <= since:
            continue
        mid = int(ref[4:])
        if cur.get("favourite"):
            favs.add(mid)
        else:
            favs.discard(mid)
    return favs


def curation_all():
    """Every curation row in one read, keyed by ref — the local spine's
    overlay source (one query, not one per show)."""
    with database.db() as c:
        return {r[0]: {"status": r[1], "score": r[2], "favourite": r[3],
                       "rewatches": r[4], "started_at": r[5],
                       "finished_at": r[6], "updated_at": r[7]}
                for r in c.execute(
                    "SELECT ref,status,score,favourite,rewatches,"
                    "started_at,finished_at,updated_at FROM curation")}


def spine_build_local(scan, curations, streams=()):
    """Decision 6's no-AniList mode: the SAME spine shape AniList mode
    renders, driven purely by local watch state. Entries are disk shows
    only — sections derive from hub.db progress, with a curation status
    the user set explicitly winning over the derivation (Decision 18.5:
    no-AniList mode keeps manually-edited curation). Everything here is
    installed by definition, so the UI's source filter locks on — except
    for online episodes (ani-cli): disk shows may carry their release's
    online episodes ("partial" then), and online-only shows (`streams`)
    join once watched — browsing a page must not add it to the library.
    Pure function: scan from scan_library(), curations from curation_all()."""
    out = []
    online = [x for x in streams if any(ep.get("last_played") or ep.get("watched") for ep in eps_of(x))]
    for s in list(scan) + online:
        eps = [ep for sea in s["seasons"] for ep in sea["episodes"]]
        aid = (s.get("meta") or {}).get("anilist_id")
        cur = curations.get("aid:%d" % aid if aid else "sid:%s" % s["id"]) or {}
        watched = [ep for ep in eps if ep.get("watched")]
        started = bool(watched) or any(ep.get("last_played") for ep in eps)
        total = len(eps)
        status = cur.get("status")
        if status not in anilist.ENTRY_STATUS:
            status = ("COMPLETED" if total and len(watched) == total
                      else "CURRENT" if started else "PLANNING")
        # Recently-updated sort feeds off the newest activity: the last
        # playback stamp or the last curation edit, whichever is newer.
        last = max([ep["last_played"] for ep in eps if ep.get("last_played")]
                   + [cur.get("updated_at") or ""])
        try:
            updated = datetime.strptime(
                last, util.CANONICAL_TS).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            updated = 0
        meta = s.get("meta") or {}
        on_disk = sum(1 for ep in eps if streaming.disk_ep(ep))
        out.append({"id": s["id"], "title": s["title"],
                    "status": status, "progress": len(watched),
                    "score": cur.get("score") or 0, "episodes": total,
                    "format": meta.get("format") or
                              ("MOVIE" if s.get("kind") == "movie" else None),
                    "year": meta.get("year"), "airing": None,
                    "genres": [], "next_episode": None,
                    "poster": s.get("poster"), "banner": s.get("banner"),
                    "updated": updated,
                    "favourite": bool(cur.get("favourite")),
                    "capability": ("installed" if on_disk == total else
                                   "partial" if on_disk else "online"),
                    "installed": on_disk,
                    "media_id": aid,
                    "local_show_id": s["id"]})
    return {"entries": out, "untracked": [], "mode": "local"}


def full_library():
    scan = scan_library()
    merge_db(scan)
    kept = metadata.kept_local()
    for s in scan:
        if not s.get("meta"):
            s["kept_local"] = s["id"] in kept  # additive (TODO 12c)
    # ani-cli bridge: a linked release's online episodes join its disk
    # folder's show; releases without a folder are shows of their own
    # ("aid:<id>"), carried beside the scan. merge_db ran first, so scan
    # health, matching and tombstones stay about the disk.
    links = streaming.stream_links_all()
    streams = streaming.stream_shows(scan, links)
    # Spine (TODO 8, Decision 4): AniList lists merged with the library —
    # from the cache only, so the frequent library poll never waits on the
    # network. Absent until the first /api/anilist fetch or boot warm-up.
    # No-AniList mode (TODO 17, Decision 6): with no usable token the same
    # spine shape is built from local watch state instead — the mode switch
    # is in place, both directions, and costs no network at all.
    connected = anilist.anilist_connected()
    cached = anilist.anilist_cache_read() if connected else None
    if cached is None and connected:
        # Fresh cache expired (warm-up failed, long session without an
        # AniList tab visit, network down): last-known lists beat a silent
        # local mode (Decision 6). The background refresh loop and the next
        # /api/anilist visit re-freshen it.
        cached = anilist.anilist_cache_stale()
    if cached:
        spine = spine_build(cached["entries"], scan, {link["media_id"] for link in links},
                            bool(anicli.anicli_path()), streaming.stream_missed_ids(), spine_favourites(cached))
    elif not connected:
        spine = spine_build_local(scan, curation_all(), streams)
    else:
        # AniList mode with no cache at all (first ever connect): no spine
        # yet (the boot warm-up fills it); the UI renders the plain local
        # grid meanwhile.
        spine = None
    if spine:
        franchise.tag_franchises(spine["entries"])
    if cached and settings.automation_enabled("anilist_watched"):
        apply_anilist_watched(scan + streams, cached["entries"])
    continue_row = []
    history = []
    for s in scan + streams:
        for ep in eps_of(s):
            if ep["last_played"]:
                history.append(ep | {"show": s["title"], "show_id": s["id"], "poster": s["poster"]})
            if not ep["watched"] and ep["position"] > 30 and ep["duration"] and ep["position"] / ep["duration"] < 0.92:
                continue_row.append(ep | {"show": s["title"], "show_id": s["id"], "poster": s["poster"]})
    continue_row.sort(key=lambda e: e["last_played"] or "", reverse=True)
    history.sort(key=lambda e: e["last_played"] or "", reverse=True)
    unwatched = {s["id"]: sum(1 for ep in eps_of(s) if not ep["watched"]) for s in scan}
    with database.db() as c:
        mins_row = c.execute(
            """SELECT COALESCE(SUM(CASE WHEN watched=1 THEN duration ELSE 0 END),0),
                      COALESCE(SUM(CASE WHEN watched=0 AND last_played IS NOT NULL THEN position ELSE 0 END),0)
               FROM episodes"""
        ).fetchone()
        minutes = int((mins_row[0] + mins_row[1]) // 60)
        eps_watched = c.execute("SELECT COUNT(*) FROM episodes WHERE watched=1").fetchone()[0]
        this_week = c.execute(
            "SELECT COUNT(*) FROM episodes WHERE watched=1 AND last_played >= datetime('now','-7 days')"
        ).fetchone()[0]
    stats = {"episodes_watched": eps_watched, "minutes": minutes, "this_week": this_week}
    with player._active_lock:
        now = None
        if player._active:
            a = next(iter(player._active.values()))
            now = {"episode_id": a["ep_id"], "show": a["show_title"], "label": a["label"],
                   "percent": a.get("percent") or 0, "loading": bool(a.get("loading"))}
    return {"shows": scan, "continue": continue_row[:20], "history": history,
            "vpn": settings.vpn_status(),
            # Legacy fields kept as empty lists since the streaming strip
            # (TODO 18, Decision 18.5). Streamed episodes ride in
            # continue/history above; their shows are `streams`.
            "continue_online": [], "history_online": [],
            "streams": streams, "stream_ready": bool(anicli.anicli_path()),
            # Scan health (TODO 23): state = ok/partial/missing/unconfigured;
            # root = ok/changed (configured root differs from the indexed one).
            "scan": dict(scan_state),
            # Tombstones (Decision 17.3): counts so the UI can hint at
            # "returned" rows instead of silently losing history context.
            "tombstones": tombstone_counts(),
            # Unmatched shows (TODO 28 minimal repair): local shows with no
            # shows_meta row — the validated matcher could not confidently
            # match them (or the user cleared a wrong match). Surfaced in
            # the library, fixable via the detail page's manual match.
            "unmatched": [s["title"] for s in scan if not s.get("meta")],
            "spine": spine,
            "unwatched": unwatched, "stats": stats, "now": now,
            # Metadata-fetch visibility (Elias): additive — the UI shows
            # what the background pass is working on, or that it's idle.
            "meta_pending": metadata._meta_status["pending"],
            "meta_running": metadata._meta_status["running"],
            "meta_current": metadata._meta_status["current"],
            "meta_phase": metadata._meta_status["phase"]}


def episode_by_id(eid):
    """(show, episode) through the same merged view the UI gets: a disk
    show carries its online episodes, so autoplay walks from the last file
    into the first online episode. A "stream:" id may also name an episode
    on disk by now (that episode is returned) or one of a release nobody
    linked yet (its page's episode)."""
    scan, streams = streaming.library_shows()  # merge_db attaches the watch facts
    for s in scan + streams:
        for ep in eps_of(s):
            if ep["id"] == eid:
                return s, ep
    if eid.startswith("stream:"):
        s = streaming.release_show(streaming.stream_media_id(eid))
        for ep in eps_of(s) if s else ():
            if ep["id"] == eid:
                return s, ep
    return None, None


def media_by_id(show_id):
    """The local show container with this id (sibling of episode_by_id at
    the show level) — used by bulk marks (TODO 6); the watch facts ride
    along (merge_db) so AniList sync sees the real watched state.
    "aid:<id>" is a release's online episodes (the ani-cli bridge)."""
    scan, streams = streaming.library_shows()
    return next((s for s in scan + streams if s["id"] == show_id), None)


def curation_get(ref):
    """Local curation overlay for a spine ref ('aid:<anilist_id>' once
    matched, 'sid:<show_id>' before). Returns None when nothing is stored.
    This is the user's own state — `shows_meta.score` (averageScore) is
    fetched metadata and never read or written here."""
    with database.db() as c:
        row = c.execute(
            "SELECT status, score, favourite, rewatches, started_at, finished_at, prefs, updated_at"
            " FROM curation WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    return {"ref": ref, "status": row[0], "score": row[1], "favourite": bool(row[2]),
            "rewatches": row[3] or 0, "started_at": row[4], "finished_at": row[5],
            "prefs": json.loads(row[6]) if row[6] else {}, "updated_at": row[7]}


def curation_set(ref, patch):
    """Create-or-merge a curation row. Known fields change only when present
    in the patch; unknown keys are ignored. Facts stay in `episodes`."""
    updates = {}
    for field in ("status", "score", "favourite", "rewatches", "started_at", "finished_at"):
        if field in patch:
            updates[field] = patch[field]
    if isinstance(patch.get("prefs"), dict):
        updates["prefs"] = json.dumps(patch["prefs"])
    if not updates:
        return curation_get(ref)
    now = util.utc_stamp()
    cols = ["ref", "updated_at"] + list(updates)
    vals = [ref, now] + list(updates.values())
    sets = ", ".join(f"{col}=excluded.{col}" for col in cols[1:])
    with database.db() as c:
        c.execute(f"INSERT INTO curation({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
                  f" ON CONFLICT(ref) DO UPDATE SET {sets}", vals)
    return curation_get(ref)
