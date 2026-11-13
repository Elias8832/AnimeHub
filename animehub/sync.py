"""AniList write outbox and connect-time reconciliation."""

import json
import threading
import time

from . import anicli, anilist, database, library, mapping, progress, settings, streaming, util


# --- Durable write outbox (TODO 14, Decision 18.4 + 24) ---------------------
# One row per unsent intent: {patch, favourite?} for a media. Enqueued when
# the connected write fails transiently (offline / AniList 5xx) or when auth
# is mid-recovery (held dormant — Decision 24). Validation failures are never
# queued (they would fail forever); the local curation overlay already holds
# the user's change either way, so nothing is lost while the row waits.
# Rows are account-bound at enqueue (Decision 24): reconnect with the same
# account flushes them, a different account discards them with a warning.

OUTBOX_BACKOFF = [30, 60, 120, 300, 900, 1800]
OUTBOX_MAX_ATTEMPTS = 8


def outbox_enqueue(media_id, patch=None, favourite=None, verify=None, account=None):
    """`verify` ({"add": bool}) marks a progress guess queued without a
    fresh AniList list — outbox_verify settles it before it is sent.
    `account` is who the change is for, read before the service was asked;
    without it, the account in use — or, when the failed call just ended
    that login, the account it belonged to (Decision 24: a row must never
    lose its account, or a different account's connect would send it)."""
    if account is None:
        account = anilist.account_id()
    if account is None:
        account = anilist.cleared_account_id()
    payload = {"patch": patch or {}, "account_id": account}
    if favourite is not None:
        payload["favourite"] = bool(favourite)
    if verify is not None:
        payload["verify"] = verify
    with database.db() as c:
        c.execute("INSERT INTO outbox(media_id,payload,account_id,created)"
                  " VALUES(?,?,?,?)",
                  (media_id, json.dumps(payload), account, util.utc_stamp()))


def outbox_defer(show_id, eps, manual):
    """TODO 59: watched episodes of a folder whose seasons can't be mapped
    to entries yet, kept as one row per folder (media_id 0: no entry known
    yet) until progress.replay_deferred can sync them. Later marks on the
    same folder join its row."""
    account = anilist.account_id()
    with database.db() as c:
        for row_id, payload_s in c.execute("SELECT id, payload FROM outbox WHERE media_id=0").fetchall():
            payload = json.loads(payload_s)
            if payload.get("resync") == show_id:
                payload["eps"] = sorted(set(payload["eps"]) | set(eps))
                payload["manual"] = payload.get("manual") or bool(manual)
                c.execute("UPDATE outbox SET payload=? WHERE id=?", (json.dumps(payload), row_id))
                return
        c.execute("INSERT INTO outbox(media_id,payload,account_id,created) VALUES(0,?,?,?)",
                  (json.dumps({"resync": show_id, "eps": sorted(eps), "manual": bool(manual),
                               "account_id": account}),
                   account, util.utc_stamp()))


def _delete_sent(row_id, payload_s):
    """Drop a row the flush settled — only while it still holds what the
    flush read: a mark that raised or joined it meanwhile goes next pass."""
    with database.db() as c:
        c.execute("DELETE FROM outbox WHERE id=? AND payload=?", (row_id, payload_s))


def outbox_count():
    with database.db() as c:
        return c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def outbox_fail(row_id, attempts, error, transient=False):
    """Backoff schedule for failed rows. A transient failure (offline,
    AniList down) retries forever at the backoff cap — an outage is not a
    poison row (Decision 34; the 2026-10-21 outage outlasted the 8 tries).
    A rejected row is dropped after the final attempt with a loud log — the
    local overlay keeps the user's change, and a poison row must not block
    the queue forever."""
    n = attempts + 1
    if not transient and n >= OUTBOX_MAX_ATTEMPTS:
        with database.db() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
        util.log("outbox: dropped entry %s after %d attempts — %s" % (row_id, n, error))
        return
    with database.db() as c:
        c.execute("UPDATE outbox SET attempts=?, next_try=?, last_error=? WHERE id=?",
                  (n, time.time() + OUTBOX_BACKOFF[min(n, len(OUTBOX_BACKOFF)) - 1],
                   str(error)[:300], row_id))


def outbox_verify(payload, remote):
    """The patch to send for a verify row, given its entry on the live list
    (None = not on the list), or None when there is nothing to send: AniList
    already has this progress or more, or the entry is unlisted and auto-add
    was off when the row was queued."""
    p = payload["patch"].get("progress")
    if remote is not None:
        return None if (remote.get("progress") or 0) >= p else {"progress": p}
    return {"status": "CURRENT", "progress": p} if payload["verify"].get("add") else None


_flush_lock = threading.Lock()


def outbox_items():
    """The sync chip's list (TODO 14): every queued change with a title, what
    it changes and why it is still waiting. `state` is "waiting" (not tried
    yet), "retrying" (AniList unreachable or limiting) or "seasons" (marks
    waiting for the folder's season map, TODO 59)."""
    active = anilist.account_id()
    with database.db() as c:
        rows = c.execute("SELECT id, media_id, payload, attempts, next_try, created, account_id"
                         " FROM outbox ORDER BY id").fetchall()
        if not rows:  # nothing queued (the usual case): no titles to look up
            return []
        media = c.execute("SELECT id, data FROM media").fetchall()
        titles = dict(c.execute("SELECT media_id, title FROM stream_links WHERE title IS NOT NULL"))
        titles.update(c.execute("SELECT anilist_id, title FROM shows_meta"
                                " WHERE anilist_id IS NOT NULL AND title IS NOT NULL"))
        folders = dict(c.execute("SELECT show_id, title FROM shows_meta WHERE title IS NOT NULL"))
    for mid, data in media:
        t = (json.loads(data).get("title") or {})
        if t.get("english") or t.get("romaji"):
            titles[mid] = t.get("english") or t.get("romaji")
    for e in (anilist.anilist_cache_stale() or {}).get("entries", []):
        if e.get("title"):
            titles[e["id"]] = e["title"]
    out = []
    for row_id, media_id, payload_s, attempts, next_try, created, account in rows:
        try:
            payload = json.loads(payload_s)
        except ValueError:
            payload = {}
        item = {"id": row_id, "media_id": media_id or None, "created": created,
                "state": "seasons" if "resync" in payload else "retrying" if attempts else "waiting",
                "retry_in": max(0, int(next_try - time.time())) if attempts else 0}
        if account is not None and str(account) != str(active) and not _paused_for(payload):
            # Decision 75 (additive): queued for the paused account; it
            # goes out when that one is in use again. Plain edits go out
            # to it now (Decision 83).
            item["state"], item["held_for"] = "account", "MyAnimeList" if _is_mal(account) else "AniList"
        if "resync" in payload:
            item["title"] = folders.get(payload["resync"]) or "A show in your library"
            item["eps"] = len(payload.get("eps") or [])
        else:
            item["title"] = titles.get(media_id) or "AniList entry %d" % media_id
            item["patch"] = payload.get("patch") or {}
            if "favourite" in payload:
                item["favourite"] = payload["favourite"]
        out.append(item)
    return out


def outbox_cancel(row_id):
    """Drop one queued change: AniList never hears of it. The local change
    (watch marks, the curation overlay) stays as it is. True when a row went."""
    with _flush_lock, database.db() as c:
        return c.execute("DELETE FROM outbox WHERE id=?", (row_id,)).rowcount > 0


def outbox_retry_now():
    """Make every held row due now (TODO 14: AniList is back, or the user
    asked the sync chip to try)."""
    with database.db() as c:
        c.execute("UPDATE outbox SET next_try=0")



def outbox_flush(limit=20):
    """One pass at a time: the loop and the sync chip's "try now" must never
    send the same row twice."""
    if not _flush_lock.acquire(blocking=False):
        return 0
    try:
        return _outbox_flush(limit)
    finally:
        _flush_lock.release()


def _outbox_flush(limit):
    """Send due rows FIFO through the normal write path. Auth failure stops
    the whole pass WITHOUT consuming attempts — the queue goes dormant until
    a valid token exists again (Decision 24). A transient failure ends the
    pass too: AniList is down for the rows behind it as well."""
    try:
        state = anilist.auth_status()
    except Exception:
        return 0
    if not state.get("connected"):
        return 0
    with database.db() as c:
        due = c.execute("SELECT id, media_id, payload, attempts FROM outbox"
                        " WHERE next_try <= ? ORDER BY id LIMIT ?",
                        (time.time(), limit)).fetchall()
    sent = 0
    account = anilist.account_id()
    remote = None  # the live list, fetched once for the pass's verify rows
    for row_id, media_id, payload_s, attempts in due:
        try:
            payload = json.loads(payload_s)
        except ValueError as e:
            outbox_fail(row_id, attempts, "corrupt payload: %s" % e)
            continue
        # Decision 24's hard line, mid-flush edition: a row created under a
        # different account is skipped (held) — auth_finish discards it with
        # a warning at connect time; it is never sent across accounts.
        if payload.get("account_id") is not None and \
                payload["account_id"] != account:
            other = _paused_for(payload)
            if other:
                sent += _flush_paused(row_id, media_id, payload_s, payload, attempts, other)
            continue
        if "resync" in payload:
            try:
                settled = progress.replay_deferred(payload)
            except Exception as e:
                outbox_fail(row_id, attempts, e, transient=True)
                continue
            if not settled:
                outbox_fail(row_id, attempts, "seasons not mapped yet", transient=True)
                continue
            _delete_sent(row_id, payload_s)
            continue
        if payload.get("verify"):
            if remote is None:
                try:
                    data = anilist.anilist_cache_read() or anilist.anilist_user_data(force=True)
                except Exception as e:
                    outbox_fail(row_id, attempts, "list unreachable: %s" % e, transient=True)
                    break
                remote = {e["id"]: e for e in data.get("entries", [])}
            payload["patch"] = outbox_verify(payload, remote.get(media_id))
            if payload["patch"] is None:
                _delete_sent(row_id, payload_s)
                continue
        try:
            if payload.get("patch"):
                anilist.anilist_entry_save(media_id, payload["patch"])
            if "favourite" in payload:
                anilist.anilist_favourite_set(media_id, payload["favourite"])
        except anilist.AniListTransientError as e:
            outbox_fail(row_id, attempts, e, transient=True)
            break
        except anilist.AniListAuthError:
            # Dormant (Decision 24): stop the pass, keep attempts untouched,
            # retry when a valid token exists again.
            break
        except ValueError as e:
            outbox_fail(row_id, attempts, e)
            continue
        _delete_sent(row_id, payload_s)
        sent += 1
    return sent


def _is_mal(account_id):
    return str(account_id).startswith("mal:")


def _paused_for(payload):
    """The paused service a row can still go to (Decision 83): a plain
    edit (the entry editor's own pick, or one queued before a switch) for
    that service's current login. Watch tracking that still needs the
    lists — folder marks, unverified guesses — waits until it's in use."""
    acc = payload.get("account_id")
    which = "mal" if _is_mal(acc) else "anilist"
    if "resync" in payload or payload.get("verify") or which == anilist.service():
        return None
    return which if str(anilist.account_id(which)) == str(acc) else None


def _flush_paused(row_id, media_id, payload_s, payload, attempts, which):
    """Send one row to the paused service. Its trouble is its own: a
    failure backs this row off without stopping the in-use service's rows."""
    try:
        with anilist.using(which):
            if payload.get("patch"):
                anilist.anilist_entry_save(media_id, payload["patch"])
            if "favourite" in payload:
                anilist.anilist_favourite_set(media_id, payload["favourite"])
    except anilist.AniListTransientError as e:
        outbox_fail(row_id, attempts, e, transient=True)
        return 0
    except anilist.AniListAuthError:
        return 0  # dormant until that login works again (Decision 24)
    except ValueError as e:
        outbox_fail(row_id, attempts, e)
        return 0
    _delete_sent(row_id, payload_s)
    return 1


def outbox_discard_other_accounts(new_account_id):
    """Decision 24: connecting a DIFFERENT account discards the held queue
    with a count (never send account A's activity to account B). Rows with
    no recorded account (defensive — unreachable via the routes) are kept
    and flushed under whoever connects. Decision 75: only the same
    service's rows — a paused AniList (or MAL) login keeps its own."""
    with database.db() as c:
        rows = c.execute("SELECT id, account_id FROM outbox WHERE account_id IS NOT NULL").fetchall()
        drop = [rid for rid, acc in rows
                if _is_mal(acc) == _is_mal(new_account_id) and str(acc) != str(new_account_id)]
        for rid in drop:
            c.execute("DELETE FROM outbox WHERE id=?", (rid,))
    return len(drop)


def outbox_loop():
    """Background flush: shortly after boot, then every minute. Failures are
    invisible here by design — the chip carries the state (Decision 3)."""
    time.sleep(10)
    seen = _last_ok()
    while True:
        try:
            seen = outbox_tick(seen)
        except Exception as e:
            util.log("outbox loop: %s" % e)
        time.sleep(60)


def _last_ok():
    """When AniList or MyAnimeList last answered (Decision 75)."""
    return max(anilist.last_ok, anilist.mal.last_ok)


def outbox_tick(seen):
    """One loop pass. `seen` is when AniList last answered as of the previous
    pass; returns the new value."""
    if outbox_count():
        if _last_ok() > seen:
            # AniList answered something since the last pass (the list
            # refresh probes it every 5 min): back from an outage — push
            # now instead of waiting out the backoff (TODO 14).
            outbox_retry_now()
        outbox_flush()
    return _last_ok()


def anilist_refresh_loop():
    """Background freshness for the spine (TODO 8/17): the AniList cache used
    to refresh only when a UI visit hit /api/anilist, so on a long session
    without that tab the spine went stale and the library silently fell back
    to local mode. Every 5 minutes the non-forced fetch runs — it round-trips
    only when the cache is older than 10 minutes, so this costs at most one
    GraphQL call per ~10 minutes, and none at all in no-AniList mode."""
    time.sleep(30)
    while True:
        try:
            if anilist.anilist_connected() and anilist.account_name():
                anilist.anilist_user_data()
        except Exception as e:
            util.log("anilist refresh loop: %s" % e)
        time.sleep(300)



# --- Connect-time reconciliation (TODO 16, Decision 14) ---------------------
# When an account is (re)linked, two worlds meet: the local watch state and
# the AniList lists. Decision 14 splits the merge:
#   Facts (watched episodes): automatic, always the higher of the two — not
#     a user choice; the dialog informs.
#   Curation (status, score): the user decides per show, with bulk buttons.
# The pending marker (settings.reconcile_pending = viewer id) is set by
# auth_finish and cleared by apply/dismiss — so the dialog appears once per
# connect event, never on every boot.

RECONCILE_KEY = "reconcile_pending"


def reconcile_mark_pending(viewer_id):
    if viewer_id is not None:
        settings.set_setting(RECONCILE_KEY, str(viewer_id))


def reconcile_clear():
    with database.db() as c:
        c.execute("DELETE FROM settings WHERE key=?", (RECONCILE_KEY,))


def _reconcile_pending():
    account = anilist.account_id()
    return account is not None and settings.get_setting(RECONCILE_KEY) == str(account)


def _listed_units(remote):
    """(show, unit, list entry) once per entry on the user's lists. The
    connect-time reconcile runs on the merged library: an entry's files
    and its online episodes (ani-cli's linked list) are one unit, as on
    the show page (TODO 191): its linked list, else its aired episodes. `fits` says whether every source fits the
    entry (unit_fits); a source that doesn't brings no episodes, so a
    wrong match never pushes or marks anything — its show still takes
    part in the curation check. The show is the folder when there is one."""
    scan = library.scan_library()
    library.merge_db(scan)  # the folder's match, season map and watch facts live in hub.db
    links = streaming.stream_links_all()
    merged = {}
    for s in scan + streaming.stream_shows(scan, links) + _unlinked_online(remote, links):
        for u in mapping.release_units(streaming.trackable(s)):
            e = remote.get(u["media_id"])
            if e is None:
                continue
            fits = mapping.unit_fits(u, e.get("episodes"))
            have = merged.setdefault(u["media_id"], (s, {"media_id": u["media_id"], "eps": [], "fits": True}))[1]
            if fits:
                have["eps"] += u["eps"]
            else:
                have["fits"] = False
    for mid, (s, u) in merged.items():
        yield s, u, remote[mid]


def _aired(e):
    """How many episodes of a list entry are out (streaming.release_aired
    for a list row)."""
    nxt = (e.get("next_episode") or {}).get("number")
    if e.get("airing") == "NOT_YET_RELEASED":
        return 0
    if e.get("airing") == "RELEASING" and nxt:
        return nxt - 1
    return e.get("episodes") or 0


def _unlinked_online(remote, links):
    """Entries ani-cli hasn't linked yet still have their aired episodes
    online, as on the show page ("stream:<id>:<n>" plays and links on the
    way): one online show each, while ani-cli is there and hasn't come up
    empty for the entry."""
    if not anicli.anicli_path():
        return []
    linked, missed, rows = {l["media_id"] for l in links}, streaming.stream_missed_ids(), streaming._stream_rows()
    out = []
    for mid, e in remote.items():
        if mid in linked or mid in missed or not _aired(e):
            continue
        eps = [{"id": f"stream:{mid}:{n}", "number": n, "watched": bool((rows.get(f"stream:{mid}:{n}") or (0,))[0])}
               for n in range(1, _aired(e) + 1)]
        out.append({"id": f"aid:{mid}", "title": e.get("title"), "seasons": [{"episodes": eps, "releases": [
            {"id": mid, "first_ep": 1, "last_ep": None, "offset": 0, "total": e.get("episodes")}]}]})
    return out


def _slots(u):
    """The entry's own episode numbers → that number's sources (file and/or
    online); a number is watched when any of its sources is."""
    by_n = {}
    for ep, n in u["eps"]:
        by_n.setdefault(n, []).append(ep)
    return by_n


def _behind_anilist(u, e):
    """Episode numbers AniList counts as seen and this PC doesn't, with the
    ids of every source of each (file and online), when AniList is ahead of
    the highest episode watched here: {number: [ids]}."""
    done = e.get("progress") or 0
    slots = _slots(u)
    seen = {n for n, eps in slots.items() if any(ep["watched"] for ep in eps)}
    if any(n >= done for n in seen):
        return {}
    return {n: [ep["id"] for ep in eps] for n, eps in sorted(slots.items()) if n not in seen and n <= done}


def reconcile_analyze():
    """Compute the connect-time merge. Progress this device is ahead on
    goes to AniList at once (guarded); episodes AniList is ahead on come
    back as `marks`, for the user to accept (Decision 48), and curation
    conflicts as rows. Only shows whose matched release is ALSO on the
    user's lists participate — pushing facts to an unlisted show would
    create list entries, which is the
    consent-gated auto-track domain (Decision 5, TODO 15). Per entry
    (release_units): each mapped season of a folder reconciles against its
    own entry; unmapped seasons sit out."""
    if not _reconcile_pending():
        return {"pending": False, "facts": [], "conflicts": [], "marks": []}
    try:
        data = anilist.anilist_user_data(force=True)
    except Exception as e:
        return {"pending": True, "facts": [], "conflicts": [], "marks": [],
                "error": anilist.friendly_error(e)}
    remote = {e["id"]: e for e in data.get("entries", [])}
    fmt = data.get("score_format") or "POINT_10"
    facts, conflicts, marks = [], [], []
    for s, u, e in _listed_units(remote):
        aid = u["media_id"]
        label = e.get("title") or s["title"]
        # Facts, per entry, in the entry's own numbering; a source that
        # doesn't fit the entry is never pushed (unit_fits, in
        # _listed_units). An unknown total (airing) can't rule it out.
        if u["eps"]:
            watched = [n for ep, n in u["eps"] if ep["watched"]]
            local_highest = max(watched) if watched else 0
            remote_progress = e.get("progress") or 0
            if local_highest > remote_progress:
                if not outbox_pending_for(aid):
                    outbox_enqueue(aid, patch={"progress": local_highest})
                facts.append({"show": label, "kind": "progress",
                              "remote": remote_progress, "local": local_highest,
                              "media_id": aid, "show_id": s["id"]})
            behind = _behind_anilist(u, e)
            if behind:
                # The dialog draws each show's real episodes (additive):
                # `numbers` get marked (a file or online), `watched` already
                # are; anything else up to `remote` is neither here nor online.
                marks.append({"show": label, "media_id": aid, "remote": remote_progress,
                              "episodes": len(behind), "show_id": s["id"],
                              "total": e.get("episodes"), "numbers": sorted(behind),
                              "watched": sorted(set(watched))})
        # Curation (user decides): only when the local overlay holds an
        # opinion — a never-touched show has no local values to defend.
        cur = library.curation_get("aid:%d" % aid) or {}
        r_status, l_status = e.get("status"), cur.get("status")
        # score100 is already on the 0-100 scale; `score` is POINT_10
        # (old caches carry only that one).
        r_score = (float(e["score100"]) if e.get("score100") is not None
                   else anilist._score_norm(e.get("score"), "POINT_10"))
        l_score = anilist._score_norm(cur.get("score"), fmt)
        status_diff = l_status and r_status and l_status != r_status
        score_diff = l_score is not None and r_score is not None and \
            abs(l_score - r_score) > 0.51
        if status_diff or score_diff:
            conflicts.append({
                "media_id": aid, "show_id": s["id"], "title": label,
                "remote": {"status": r_status, "score": _user_score(e, fmt)},
                "local": {"status": l_status, "score": cur.get("score")},
                "diff": ("status" if status_diff else "") +
                        ("+score" if status_diff and score_diff else
                         ("score" if score_diff else "")),
            })
    return {"pending": True, "facts": facts, "conflicts": conflicts, "marks": marks}


def reconcile_mark_watched():
    """The user's yes to `marks` (Decision 48): episodes up to AniList's
    progress become watched here, files and online episodes alike. Returns
    how many episode numbers were marked."""
    if not _reconcile_pending():
        return 0
    remote = {e["id"]: e for e in anilist.anilist_user_data().get("entries", [])}
    marked = 0
    for _, u, e in _listed_units(remote):
        for ids in _behind_anilist(u, e).values():
            for ep_id in ids:  # the file and the online episode alike
                progress.set_watched(ep_id, True)
            marked += 1
    return marked


def _user_score(e, fmt):
    """A cached list entry's score in the user's own format — the scale the
    local overlay keeps (the list's `score` is always POINT_10)."""
    s = anilist.score_from100(e.get("score100"), fmt)
    return e.get("score") if s is None else s


def outbox_pending_for(media_id):
    """Queued rows for this entry on the account being read (the one in
    use, or the editor's pick — Decision 83)."""
    account = anilist.account_id()
    with database.db() as c:
        rows = c.execute("SELECT account_id FROM outbox WHERE media_id=?", (media_id,)).fetchall()
    return sum(1 for (acc,) in rows if acc is None or str(acc) == str(account))


def outbox_raise_progress(media_id, progress):
    """Raise the newest queued row's progress when it is a plain
    progress-only intent (the auto-track/bulk shape) below `progress`.
    Rows carrying anything else are the user's own edits — left alone.
    Returns True when a row was raised."""
    account = anilist.account_id()
    with database.db() as c:
        row = next((r for r in c.execute("SELECT id, payload, account_id FROM outbox WHERE media_id=?"
                                         " ORDER BY id DESC", (media_id,)).fetchall()
                    if r[2] is None or str(r[2]) == str(account)), None)
        if not row:
            return False
        try:
            payload = json.loads(row[1])
        except ValueError:
            return False
        patch = payload.get("patch") or {}
        if set(patch) != {"progress"} or "favourite" in payload \
                or not isinstance(patch["progress"], int) or patch["progress"] >= progress:
            return False
        payload["patch"] = {"progress": progress}
        c.execute("UPDATE outbox SET payload=? WHERE id=?", (json.dumps(payload), row[0]))
        return True


def outbox_trim_saved(media_id, fields, account, progress=None):
    """A direct save of `fields` (patch keys, and "favourite") just went
    through for this entry and account: queued rows would only send older
    values over it later. Those fields leave them, a plain progress row at
    or below the saved `progress` goes, and rows left empty go. Guarded like
    the flush: a row that changed meanwhile is left as it is. Never raises
    (the save itself succeeded). Returns how many rows changed."""
    fields, changed = set(fields), 0
    try:
        with database.db() as c:
            rows = c.execute("SELECT id, payload, account_id FROM outbox WHERE media_id=?",
                             (media_id,)).fetchall()
        for row_id, payload_s, acc in rows:
            if acc is not None and str(acc) != str(account):
                continue
            try:
                payload = json.loads(payload_s)
            except ValueError:
                continue
            if not isinstance(payload, dict) or "resync" in payload:
                continue
            patch = {k: v for k, v in (payload.get("patch") or {}).items() if k not in fields}
            if set(patch) == {"progress"} and isinstance(progress, int) \
                    and isinstance(patch["progress"], int) and patch["progress"] <= progress:
                patch = {}
            new = dict(payload, patch=patch)
            if "favourite" in fields:
                new.pop("favourite", None)
            if new == payload:
                continue
            with database.db() as c:
                if not patch and "favourite" not in new:
                    n = c.execute("DELETE FROM outbox WHERE id=? AND payload=?", (row_id, payload_s)).rowcount
                else:
                    n = c.execute("UPDATE outbox SET payload=? WHERE id=? AND payload=?",
                                  (json.dumps(new), row_id, payload_s)).rowcount
            changed += n
    except Exception as e:
        util.log("outbox: queued rows for %s not trimmed: %s" % (media_id, e))
    return changed


def outbox_queue_progress(media_id, progress, verify=None):
    """Watched up to `progress` while rows for the entry wait: raise the
    newest plain progress row, else queue the progress as its own row
    (behind the user's own edit, which stays as it is) — unless a waiting
    row already sends this much. True when queued or raised."""
    if outbox_raise_progress(media_id, progress):
        return True
    account = anilist.account_id()
    with database.db() as c:
        rows = c.execute("SELECT payload, account_id FROM outbox WHERE media_id=?", (media_id,)).fetchall()
    for payload_s, acc in rows:
        if acc is not None and str(acc) != str(account):
            continue
        try:
            sent = (json.loads(payload_s).get("patch") or {}).get("progress")
        except (ValueError, AttributeError):
            continue
        if isinstance(sent, int) and sent >= progress:
            return False
    outbox_enqueue(media_id, patch={"progress": progress}, verify=verify)
    return True


def reconcile_apply(decisions):
    """Apply the user's curation choices. 'anilist' adopts the remote values
    into the local overlay (no write needed — already there); 'local' keeps
    the overlay and pushes its values to AniList through the outbox (rate
    -limit friendly, and it survives a post-connect connection drop)."""
    applied = 0
    remote_known, fmt = {}, "POINT_10"
    try:
        data = anilist.anilist_user_data()
        remote_known = {e["id"]: e for e in data.get("entries", [])}
        fmt = data.get("score_format") or fmt
    except Exception:
        pass
    for d in decisions or []:
        try:
            media_id = int(d.get("media_id"))
        except (TypeError, ValueError):
            continue
        winner = d.get("winner")
        ref = "aid:%d" % media_id
        if winner == "anilist":
            e = remote_known.get(media_id) or {}
            cpatch = {}
            if e.get("status"):
                cpatch["status"] = e["status"]
            if e.get("score") is not None:
                cpatch["score"] = _user_score(e, fmt)
            if cpatch:
                library.curation_set(ref, cpatch)
                applied += 1
        elif winner == "local":
            cur = library.curation_get(ref) or {}
            patch = {}
            if cur.get("status"):
                patch["status"] = cur["status"]
            if cur.get("score") is not None:
                patch["score"] = cur["score"]
            if patch:
                outbox_enqueue(media_id, patch=patch)
                applied += 1
    reconcile_clear()
    return applied
