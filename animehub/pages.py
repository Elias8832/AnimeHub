"""The franchise page model the UI draws."""

import json
import threading
import time

from . import anicli, anilist, database, franchise, library, mapping, progress, streaming, util


# --- Franchise page (docs/franchise-page-plan.md, phase 4) ------------------
# One page per franchise: every entry with its facts and art, and each
# entry's episodes as one list of slots where a local file and an online
# episode are the same thing with a different source. Built from stored
# data only (franchise, mapping, links, watch facts, art); what is missing
# (the franchise itself, an entry's online episode list, its stills) is
# fetched in the background and the page is asked for again.

FORMAT_LABELS = {"TV": "TV", "TV_SHORT": "Short", "ONA": "ONA", "OVA": "OVA", "SPECIAL": "Special",
                 "MOVIE": "Movie", None: "Announced"}


def franchise_root(entries):
    """The entry the main seasons start from and the page is named after:
    the first-aired TV/ONA entry (Bakemonogatari, though AniList calls the
    later Nekomonogatari its prequel), else the oldest entry."""
    return next((m for m in entries if m.get("format") in mapping.LINE_FORMATS[0] and m.get("format")),
                entries[0] if entries else None)


def _title_rest(m, roots):
    """What an entry's title adds to the franchise's: "Season 3 Part 2" for
    "Attack on Titan Season 3 Part 2"; "" when that is nothing worth
    showing ("Kaguya-sama: Love is War?", "Cyberpunk: Edgerunners 2");
    None when the title isn't the franchise's at all (Nisemonogatari)."""
    found = None
    for title in franchise._titles(m):
        for root in roots:
            if title.lower().startswith(root.lower()):
                rest = title[len(root):].strip(" :-–—~!?.,")
                if sum(ch.isalnum() for ch in rest) >= 3:
                    return rest
                found = ""
    return found


def entry_label(m, roots, season_no):
    """A short display label: what the title adds to the franchise's
    ("Season 3 Part 2", "The Golden City of the Scorching Sun"); a main
    season adding nothing is "Season N" by its place in the line; an entry
    with a title of its own keeps it (Nisemonogatari). Display only
    (rule 4): a miss is a longer label, never a wrong page."""
    rest = _title_rest(m, roots)
    if rest:
        return rest
    if rest == "" and season_no:
        return f"Season {season_no}"
    return (franchise._titles(m) or [FORMAT_LABELS.get(m.get("format"), "Extra")])[0]


def _entry_facts(m):
    t = m.get("title") or {}
    nxt = m.get("nextAiringEpisode") or {}
    return {"id": m["id"], "title": t.get("english") or t.get("romaji"), "romaji": t.get("romaji"),
            "format": m.get("format"), "format_label": FORMAT_LABELS.get(m.get("format"), m.get("format")),
            "status": m.get("status"), "episodes": m.get("episodes"),
            "aired": streaming.release_aired(m) if m.get("status") else 0,
            "next_airing": {"episode": nxt.get("episode"), "at": nxt.get("airingAt")} if nxt else None,
            "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear"),
            "season": m.get("season"), "score": m.get("averageScore"),
            "duration": m.get("duration"), "overview": anilist.strip_html(m.get("description")),
            "poster": (m.get("coverImage") or {}).get("extraLarge"), "banner": m.get("bannerImage")}


def _watch(src):
    return {k: src.get(k) for k in ("watched", "position", "duration", "last_played")}


def entry_slots(m, local, link, rows, art, online_ok):
    """One entry's episodes, in order: a slot per episode number that is on
    disk (`local`: number → scanned episode), in the online episode list
    (`link`, once ani-cli linked the entry), or aired by AniList (until
    then: "stream:<id>:<n>" plays it and links on the way). A slot is
    {"number", "title", "thumb", "id" (what Play plays: the file if there
    is one), "source" ("local"/"online"/None), "local", "online", watch
    facts}. It is watched when any of its sources is; position and
    duration come from the source played last."""
    mid = m["id"]
    online = {}
    if link:
        for token in link["episodes"]:
            online[streaming._stream_num(token)] = token
    elif online_ok:
        online = {n: str(n) for n in range(1, (streaming.release_aired(m) if m.get("status") else 0) + 1)}
    thumbs, titles = (art or {}).get("thumbs") or {}, (art or {}).get("titles") or {}
    slots = []
    for n in sorted(set(local) | set(online)):
        ep, token = local.get(n), online.get(n)
        sid = f"stream:{mid}:{token}" if token is not None else None
        r = rows.get(sid) if sid else None
        srcs = [x for x in (_watch(ep) if ep else None,
                            {"watched": bool(r[0]), "position": r[1], "duration": r[2], "last_played": r[3]}
                            if r else None) if x]
        last = max(srcs, key=lambda x: x.get("last_played") or "", default={})
        slots.append({"number": n, "title": titles.get(str(n)), "thumb": thumbs.get(str(n)),
                      "id": ep["id"] if ep else sid, "source": "local" if ep else "online" if sid else None,
                      "local": {"id": ep["id"], "files": ep["files"], "show_id": ep.get("show_id"),
                                "season": ep.get("sea_key"), "folder": ep.get("folder"),
                                "season_label": ep.get("season_label")} if ep else None,
                      "online": {"id": sid, "token": token} if sid else None,
                      "watched": any(x.get("watched") for x in srcs),
                      "position": last.get("position") or 0, "duration": last.get("duration") or 0,
                      "last_played": last.get("last_played")})
    return slots


def franchise_page(entries, scan, links, rows, arts, online_ok=True, missed=frozenset()):
    """The page model (pure): `entries` in page order (franchise_of), `scan`
    after merge_db (season ranges, watch facts), `links` media id → ani-cli
    link, `rows` "stream:" id → (watched, position, duration,
    last_played), `arts` media id → stills/titles, `online_ok` ani-cli is
    there, `missed` entries ani-cli has no match for. Returns {"title",
    "poster", "banner", "entries": [facts + "group" ("main" seasons /
    "extra"), "label", "season", "slots", "online"], "other_local":
    [{"show_id", "season", "label", "episodes"}]}: files of the
    franchise's folders that are no entry, never hidden."""
    ids = {m["id"] for m in entries}
    root = franchise_root(entries)
    line = [m["id"] for m in mapping.main_line(root["id"], entries)] if root else []
    roots = franchise._titles(root) if root else []
    local, other = {}, []
    for s in scan:
        units = mapping.release_units(s)
        mine = [u for u in units if u["media_id"] in ids]
        if not mine and (s.get("meta") or {}).get("anilist_id") not in ids:
            continue
        for u in mine:
            held = local.setdefault(u["media_id"], {})
            for ep, n in u["eps"]:
                if streaming.disk_ep(ep):
                    held.setdefault(n, dict(ep, show_id=s["id"], folder=s["title"],
                                            sea_key=f"{ep['season']}|{ep['season_label']}"))
        mapped = {ep["id"] for u in units for ep, _ in u["eps"]}
        for sea in s["seasons"]:
            loose = [dict(ep, **_watch(ep)) for ep in sea["episodes"] if streaming.disk_ep(ep) and ep["id"] not in mapped]
            if loose:
                other.append({"show_id": s["id"], "season": mapping.season_key(sea), "label": sea["label"],
                              "folder": s["title"], "episodes": loose})
    out = []
    for m in entries:
        no = line.index(m["id"]) + 1 if m["id"] in line else None
        mid = m["id"]
        reachable = online_ok and mid not in missed
        out.append(dict(_entry_facts(m), group="main" if no else "extra", season=no,
                        label=entry_label(m, roots, no),
                        online={"linked": mid in links, "available": bool(links.get(mid)) or reachable,
                                "mode": (links.get(mid) or {}).get("mode")},
                        slots=entry_slots(m, local.get(mid, {}), links.get(mid), rows, arts.get(mid), reachable)))
    # Seasons named on their own (Monogatari: Nisemonogatari, Owarimonogatari)
    # leave the first one its own name too, not "Season 1".
    main = [(e, m) for e, m in zip(out, entries) if e["group"] == "main"]
    if len(main) == 1:
        # Nothing to number it against (or the franchise is still being
        # found: a "Season 2" opened from search must not read "Season 1").
        main[0][0]["label"] = (franchise._titles(main[0][1]) or [main[0][0]["label"]])[0]
    elif len(main) > 1 and all(_title_rest(m, roots) is None for _, m in main[1:]):
        main[0][0]["label"] = franchise._titles(main[0][1])[0]
    facts = _entry_facts(root) if root else {}
    return {"title": facts.get("title"), "poster": facts.get("poster"), "banner": facts.get("banner"),
            "entries": out, "other_local": other}


_building = set()
# Franchises whose last build failed (AniList unreachable): id -> (time,
# reason). For BUILD_RETRY seconds the page answers "unavailable" instead of
# "building" forever, so the UI can fall back to the folder's show page
# (TODO 57); opening it after that tries AniList again.
_build_failed = {}
BUILD_RETRY = 60


def franchise_build_async(mid):
    """Build and store a franchise nobody has opened yet (a search result),
    in the background; the page asks again until it is there."""
    if mid in _building or time.time() - _build_failed.get(mid, (0, None))[0] < BUILD_RETRY:
        return
    _building.add(mid)

    def run():
        try:
            entries = franchise.build_franchise(mid, franchise.media_entry)  # paced by _anilist_slot
            if entries:
                franchise.store_franchise(entries)
            _build_failed.pop(mid, None)
        except Exception as e:
            util.log("building franchise of %s failed: %s" % (mid, e))
            _build_failed[mid] = (time.time(), "Couldn't reach AniList (%s)." % e)
        finally:
            _building.discard(mid)

    threading.Thread(target=run, daemon=True).start()


def entry_watched(mid, watched, numbers=None):
    """The franchise page's watched marks: every source of the entry's
    slots (`numbers`, or all) flips at once — a slot is one episode, file
    or online. Marking watched then publishes the entry's progress like a
    bulk mark (Decision 27: a click is the user acting), with the same fit
    rule; unwatching stays local."""
    page = franchise_view(mid, background=False)
    entry = next((e for e in page["entries"] if e["id"] == mid), None)
    if not entry:
        return {"error": "unknown entry"}
    pick = set(numbers) if numbers is not None else None
    slots = [x for x in entry["slots"] if pick is None or x["number"] in pick]
    ids = [src["id"] for x in slots for src in (x["local"], x["online"]) if src]
    with database.db() as c:
        c.executemany("INSERT INTO episodes(id,watched) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET watched=excluded.watched",
                      [(i, int(bool(watched))) for i in ids])
    synced = None
    if watched:
        done = {x["number"] for x in slots}
        eps = [{"id": x["id"], "number": x["number"], "watched": x["watched"] or x["number"] in done}
               for x in entry["slots"] if isinstance(x["number"], int) and x["number"] >= 1]
        show = {"seasons": [{"episodes": eps, "releases": [
            {"id": mid, "first_ep": 1, "last_ep": None, "offset": 0, "total": entry["episodes"]}]}]}
        try:
            synced = progress.anilist_track_show(show, manual=True)
        except Exception as e:
            util.log("entry sync failed: %s" % e)
    return {"ok": True, "episodes": len(slots), "ids": ids, "watched": bool(watched), "synced": synced}


_freshening = set()


def drop_premature_links():
    """An automatic ani-cli link for an entry that hasn't aired is another
    show's episodes (older versions made them: the announced "Chainsaw Man:
    Shikaku-hen" played Chainsaw Man's season 1): drop them. Links the user
    chose stay."""
    with database.db() as c:
        c.execute("""DELETE FROM stream_links WHERE COALESCE(matched_by, 'auto') = 'auto' AND media_id IN
                     (SELECT id FROM media WHERE json_extract(data, '$.status') = 'NOT_YET_RELEASED')""")


def stream_freshen_async(media_id):
    """Ask ani-cli again for an entry's online episodes once its list is
    stale (airing entries gain episodes), in the background."""
    if media_id in _freshening or not anicli.anicli_path():
        return
    _freshening.add(media_id)

    def run():
        try:
            streaming.stream_refresh(media_id)
        except Exception as e:
            util.log("refreshing %s failed: %s" % (media_id, e))
        finally:
            _freshening.discard(media_id)

    threading.Thread(target=run, daemon=True).start()


def franchise_view(mid, background=True):
    """GET /api/franchise?id=<entry>: the page holding this entry, with it
    selected. Never waits on the network: a franchise not built yet comes
    back as the entry alone with "building" (stored facts, when there are
    any), and the selected entry's online episode list and stills are
    fetched in the background ("resolving"). An entry with nothing stored
    whose build just failed comes back "unavailable" (the reason)."""
    fr = franchise.franchise_of(mid)
    if fr:
        fid, entries = fr
    else:
        if background:
            franchise_build_async(mid)
        with database.db() as c:
            row = c.execute("SELECT data FROM media WHERE id=?", (mid,)).fetchone()
        fid, entries = None, [json.loads(row[0])] if row else []
    building = fr is None
    scan = library.scan_library()
    library.merge_db(scan)
    drop_premature_links()
    links = {link["media_id"]: link for link in streaming.stream_links_all()}
    ids = [m["id"] for m in entries]
    arts = mapping.entry_art_all(ids)
    online_ok = bool(anicli.anicli_path())
    page = franchise_page(entries, scan, links, streaming._stream_rows(), arts, online_ok, streaming.stream_missed_ids())
    if not background:
        return dict(page, id=fid, selected=mid)
    sel = next((m for m in entries if m["id"] == mid), None)
    if sel and mid not in links and sel.get("status") != "NOT_YET_RELEASED":
        streaming.stream_resolve_async(mid)
    elif mid in links and time.time() - links[mid]["episodes_ts"] > anicli.STREAM_EPISODES_TTL:
        stream_freshen_async(mid)
    if entries and mid not in arts:
        threading.Thread(target=mapping.refresh_entry_art, kwargs={"ids": {mid}}, daemon=True).start()
    failed = _build_failed.get(mid) if building and not entries and mid not in _building else None
    return dict(page, id=fid, selected=mid, building=building or mid in _building,
                resolving=sorted(i for i in ids if i in streaming._resolving or i in _freshening), stream_ready=online_ok,
                unavailable=failed[1] if failed else None)
