"""Watch progress: completion events, AniList tracking, watched marks."""

import threading

from . import anilist, database, library, mapping, settings, streaming, sync, util


def _completion_event(media_id, title):
    """Record a show-completion event (TODO 9, Decision 25): one-shot popup
    keyed (media_id, completion count) — rewatches bump the count and are
    NEW events; dismissed ones never re-appear. The newest unseen event is
    served through /api/now."""
    with database.db() as c:
        row = c.execute("SELECT COALESCE(MAX(count),0) FROM completion_events"
                        " WHERE media_id=?", (media_id,)).fetchone()
        count = row[0] + 1
        c.execute("INSERT INTO completion_events(media_id,count,seen,ts,title)"
                  " VALUES(?,?,0,?,?)", (media_id, count, util.utc_stamp(), title))
    with _completion_lock:
        _load_pending()
        _completion_pending.append({"media_id": media_id, "count": count,
                                    "title": title})


def _load_pending():
    """Once per run (under _completion_lock): unseen events of the last week
    from the database, so a restart before the popup was answered doesn't
    lose it. Older ones stay quiet — a month-old popup is noise."""
    global _completion_loaded
    if _completion_loaded:
        return
    _completion_loaded = True
    with database.db() as c:
        rows = c.execute("SELECT media_id, count, title FROM completion_events WHERE seen=0"
                         " AND ts >= datetime('now','-7 days') ORDER BY ts").fetchall()
    known = {(e["media_id"], e["count"]) for e in _completion_pending}
    _completion_pending[:0] = [{"media_id": int(m), "count": n, "title": t} for m, n, t in rows
                               if (int(m), n) not in known]


def _pending_completion():
    with _completion_lock:
        _load_pending()
        return dict(_completion_pending[-1]) if _completion_pending else None


def _dismiss_completion(media_id, count):
    with database.db() as c:
        c.execute("UPDATE completion_events SET seen=1 WHERE media_id=? AND count=?",
                  (media_id, count))
    with _completion_lock:
        _completion_pending[:] = [e for e in _completion_pending
                                  if not (e["media_id"] == media_id
                                          and e["count"] == count)]


_completion_lock = threading.Lock()
_completion_pending = []
_completion_loaded = False


def anilist_track_show(s, manual=False, only=None):
    """_track plus TODO 59: marks on seasons that can't be mapped to entries
    yet (the folder's franchise isn't built — AniList unreachable since the
    match) are deferred, not dropped (sync.outbox_defer). Returns _track's
    patches, {"later": True} when only a deferral was queued, or None."""
    deferred = False
    if anilist.anilist_connected() and (manual or settings.automation_enabled("auto_progress_sync")):
        deferred = _defer_unmapped(s, only, manual)
    return _track(s, manual, only) or ({"later": True} if deferred else None)


def _defer_unmapped(s, only, manual):
    """Queue the folder's watched episodes that no entry holds while its
    franchise isn't stored. Once it is, unmapped episodes are local by
    design (specials, seasons pinned to no entry) and nothing is deferred."""
    anchor = (s.get("meta") or {}).get("anilist_id")
    if not anchor or mapping.franchise_entries([anchor]).get(anchor):
        return False
    mapped = {ep["id"] for u in mapping.release_units(s) for ep, _ in u["eps"]}
    eps = [ep["id"] for sea in s["seasons"] for ep in sea["episodes"]
           if ep["watched"] and ep["id"] not in mapped and (only is None or ep["id"] in only)]
    if not eps:
        return False
    sync.outbox_defer(s["id"], eps, manual)
    return True


def replay_deferred(payload):
    """A deferred row's turn in the flush: True once settled (synced through
    the normal path, or the folder is gone), False while the franchise is
    still missing."""
    s = library.media_by_id(payload["resync"])
    if not s:
        return True
    anchor = (s.get("meta") or {}).get("anilist_id")
    if anchor and not mapping.franchise_entries([anchor]).get(anchor):
        return False
    _track(streaming.trackable(s), bool(payload.get("manual")), set(payload["eps"]))
    return True


def _track(s, manual=False, only=None):
    """Sync one local show against the cached AniList state — Decision 21's
    highest-watched progress plus Decision 5's consent-gated auto-add, per
    entry (release_units): each mapped season, or part of one, syncs to its
    own entry with the entry's own episode numbers; unmapped seasons stay
    local. All writes ride the outbox (TODO 14): offline/backoff/dedupe
    handled once. Compares against the cache only — never a synchronous
    read — and skips silently when it's cold; the next watched episode
    catches up. An entry's files may not exceed its total (unit_fits).
    manual=True (TODO 6 bulk marks): an explicit user click syncs even when
    the auto_progress_sync automation is off (Decision 27 — a button you
    pressed is you acting, not an automation); the auto-add consent toggle
    still gates unlisted entries. `only` (episode ids) limits the sync to
    the entries holding those episodes — playing S3E5 never touches S1's
    entry. Returns the enqueued patch (one entry), {media id: patch}
    (several), or None."""
    if not anilist.anilist_connected():
        return None
    add = settings.automation_enabled("auto_add_watch")
    if not manual and not settings.automation_enabled("auto_progress_sync") and not add:
        return None
    cached = anilist.anilist_cache_read()
    if not cached:
        if manual or settings.automation_enabled("auto_progress_sync"):
            return _track_blind(s, only, add)
        return None
    entries = {e.get("id"): e for e in cached.get("entries", [])}
    # Progress of listed entries is the Track episodes switch's alone: auto
    # -add being on must not publish progress the user switched off.
    track = manual or settings.automation_enabled("auto_progress_sync")
    patches = {}
    for u in mapping.release_units(s):
        if only is not None and not any(ep["id"] in only for ep, _ in u["eps"]):
            continue
        aid = u["media_id"]
        watched = [n for ep, n in u["eps"] if ep["watched"]]
        highest = max(watched) if watched else 0
        remote = entries.get(aid)
        if remote is not None:
            if not track or not mapping.unit_fits(u, remote.get("episodes")) or highest <= (remote.get("progress") or 0):
                continue
            if sync.outbox_pending_for(aid):
                # Offline E5 then E6: fold the newer count into the queued
                # progress-only row instead of dropping it.
                if sync.outbox_raise_progress(aid, highest):
                    patches[aid] = {"progress": highest}
                continue
            patches[aid] = {"progress": highest}
            sync.outbox_enqueue(aid, patch=patches[aid])
            continue
        # Unlisted: consent-gated auto-add, and only an entry the user
        # actually watched part of — never the whole franchise at once.
        if not add or not highest or sync.outbox_pending_for(aid) or not mapping.unit_fits(u, u.get("total")):
            continue
        patches[aid] = {"status": "CURRENT", "progress": highest}
        sync.outbox_enqueue(aid, patch=patches[aid])
    if not patches:
        return None
    return next(iter(patches.values())) if len(patches) == 1 else patches


def _track_blind(s, only, add):
    """anilist_track_show without a fresh list (AniList unreachable, TODO
    58): queue each entry's highest watched episode as a verify row — the
    flush checks it against the live list before sending (sync.outbox_verify),
    so a guess never lowers progress or adds an entry unasked."""
    patches = {}
    for u in mapping.release_units(s):
        if only is not None and not any(ep["id"] in only for ep, _ in u["eps"]):
            continue
        watched = [n for ep, n in u["eps"] if ep["watched"]]
        if not watched or not mapping.unit_fits(u, u.get("total")):
            continue
        aid, highest = u["media_id"], max(watched)
        if sync.outbox_pending_for(aid):
            if sync.outbox_raise_progress(aid, highest):
                patches[aid] = {"progress": highest}
            continue
        patches[aid] = {"progress": highest}
        sync.outbox_enqueue(aid, patch=patches[aid], verify={"add": add})
    if not patches:
        return None
    return next(iter(patches.values())) if len(patches) == 1 else patches


def auto_track_after_play(ep_id):
    """The Decision 5 automations, evaluated after PLAYBACK marks an
    episode watched (manual marks use their own path — TODO 6). Delegates
    to anilist_track_show with the automations' consent rules, for the
    played episode's entry only."""
    if not anilist.anilist_connected():
        return None
    s, ep = library.episode_by_id(ep_id)
    if not s or not ep:
        return None
    return anilist_track_show(streaming.trackable(s), only={ep["id"]})


def bulk_watched(show_id, season=None, watched=True, ids=None):
    """TODO 6 (Decision 27): mark every episode of one season — or the whole
    container — watched (or unwatched) in one go. This is bookkeeping for
    shows watched before the app existed, NOT playback: no completion event
    fires (Decision 25), manual marks use this separate path. The local
    facts flip first, then Decision 27's sync: each touched entry's
    published progress becomes its highest watched episode (release_units —
    unmapped seasons stay local), and unwatching stays local (AniList
    corrections stay on the entry panel). `ids` (multi-select) limits the
    mark to those episodes of the show. Returns a small report for the UI."""
    s = library.media_by_id(show_id)
    if not s:
        return {"error": "unknown show"}
    pick = set(ids) if ids is not None else None
    if season is not None:
        if not 0 <= season < len(s["seasons"]):
            return {"error": "no such season"}
        targets = [s["seasons"][season]]
    else:
        targets = s["seasons"]
    eps = [ep for sea in targets for ep in sea["episodes"] if pick is None or ep["id"] in pick]
    with database.db() as c:
        for ep in eps:
            c.execute(
                # a mark means done: a half-watched episode drops its stop point
                "INSERT INTO episodes(id,watched) VALUES(?,?)"
                " ON CONFLICT(id) DO UPDATE SET watched=excluded.watched, position=CASE WHEN excluded.watched=1 THEN 0 ELSE position END",
                (ep["id"], int(bool(watched))))
    synced = None
    if watched:
        # The in-memory show still carries the pre-mark flags (the scan
        # happened before the writes) — reflect them so the sync sees the
        # fresh state. Unwatching stays local (Decision 27).
        for ep in eps:
            ep["watched"] = 1
        try:
            synced = anilist_track_show(streaming.trackable(s), manual=True, only={ep["id"] for ep in eps})
        except Exception as exc:
            util.log("bulk sync failed: %s" % exc)
    return {"ok": True, "episodes": len(eps), "watched": bool(watched),
            "synced": synced}


def completion_for_episode(ep_id):
    """Decision 25's trigger, evaluated after a PLAYBACK session completes an
    episode (never after manual marking — separate code path). Fires when
    the finished episode is the final one of its own entry (release_units:
    S2's finale completes S2's entry, whatever folder it sits in) and the
    disk copy fits that entry. Unmapped seasons stay silent, and entries
    with an unknown total (airing) can't fire until the total is known."""
    s, ep = library.episode_by_id(ep_id)
    if not s or not ep:
        return
    unit, n = mapping.unit_of_episode(streaming.trackable(s), ep_id)
    if not unit:
        return
    aid = unit["media_id"]
    total, title = None, None
    cached = anilist.anilist_cache_read()
    if cached:
        for e in cached.get("entries", []):
            if e.get("id") == aid:
                total, title = e.get("episodes"), e.get("title")
                break
    total = total or unit.get("total")
    if not total or n != total or not mapping.unit_fits(unit, total):
        return
    _completion_event(aid, title or (s.get("meta") or {}).get("title") or s["title"])


def set_watched(eid, watched):
    with database.db() as c:
        c.execute("INSERT INTO episodes(id,watched) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET watched=excluded.watched, position=CASE WHEN excluded.watched=1 THEN 0 ELSE position END",
                  (eid, int(watched)))


def reset_progress(eid):
    with database.db() as c:
        c.execute("UPDATE episodes SET position=0, watched=0, last_played=NULL WHERE id=?", (eid,))
