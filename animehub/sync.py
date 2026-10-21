"""AniList write outbox and connect-time reconciliation."""

import json
import time

from . import anilist, database, library, mapping, progress, settings, streaming, util


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


def outbox_enqueue(media_id, patch=None, favourite=None, verify=None):
    """`verify` ({"add": bool}) marks a progress guess queued without a
    fresh AniList list — outbox_verify settles it before it is sent."""
    tok = anilist.load_token() or {}
    payload = {"patch": patch or {}, "account_id": tok.get("user_id")}
    if favourite is not None:
        payload["favourite"] = bool(favourite)
    if verify is not None:
        payload["verify"] = verify
    with database.db() as c:
        c.execute("INSERT INTO outbox(media_id,payload,account_id,created)"
                  " VALUES(?,?,?,?)",
                  (media_id, json.dumps(payload), tok.get("user_id"), util.utc_stamp()))


def outbox_defer(show_id, eps, manual):
    """TODO 59: watched episodes of a folder whose seasons can't be mapped
    to entries yet, kept as one row per folder (media_id 0: no entry known
    yet) until progress.replay_deferred can sync them. Later marks on the
    same folder join its row."""
    tok = anilist.load_token() or {}
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
                               "account_id": tok.get("user_id")}),
                   tok.get("user_id"), util.utc_stamp()))


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


def outbox_flush(limit=20):
    """Send due rows FIFO through the normal write path. Auth failure stops
    the whole pass WITHOUT consuming attempts — the queue goes dormant until
    a valid token exists again (Decision 24)."""
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
    tok = anilist.load_token() or {}
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
                payload["account_id"] != tok.get("user_id"):
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
            with database.db() as c:
                c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
            continue
        if payload.get("verify"):
            if remote is None:
                try:
                    data = anilist.anilist_cache_read() or anilist.anilist_user_data(force=True)
                    remote = {e["id"]: e for e in data.get("entries", [])}
                except Exception as e:
                    remote = e
            if isinstance(remote, Exception):
                outbox_fail(row_id, attempts, "AniList list unreachable: %s" % remote, transient=True)
                continue
            payload["patch"] = outbox_verify(payload, remote.get(media_id))
            if payload["patch"] is None:
                with database.db() as c:
                    c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
                continue
        try:
            if payload.get("patch"):
                anilist.anilist_entry_save(media_id, payload["patch"])
            if "favourite" in payload:
                anilist.anilist_favourite_set(media_id, payload["favourite"])
        except anilist.AniListTransientError as e:
            outbox_fail(row_id, attempts, e, transient=True)
            continue
        except anilist.AniListAuthError:
            # Dormant (Decision 24): stop the pass, keep attempts untouched,
            # retry when a valid token exists again.
            break
        except ValueError as e:
            outbox_fail(row_id, attempts, e)
            continue
        with database.db() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
        sent += 1
    return sent


def outbox_discard_other_accounts(new_account_id):
    """Decision 24: connecting a DIFFERENT account discards the held queue
    with a count (never send account A's activity to account B). Rows with
    no recorded account (defensive — unreachable via the routes) are kept
    and flushed under whoever connects."""
    with database.db() as c:
        n = c.execute("SELECT COUNT(*) FROM outbox WHERE account_id IS NOT NULL"
                      " AND account_id != ?", (new_account_id,)).fetchone()[0]
        if n:
            c.execute("DELETE FROM outbox WHERE account_id IS NOT NULL AND account_id != ?",
                      (new_account_id,))
    return n


def outbox_loop():
    """Background flush: shortly after boot, then every minute. Failures are
    invisible here by design — the chip carries the state (Decision 3)."""
    time.sleep(10)
    while True:
        try:
            if outbox_count():
                outbox_flush()
        except Exception as e:
            util.log("outbox loop: %s" % e)
        time.sleep(60)


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
            if anilist.anilist_connected() and anilist.load_anilist_username():
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


def reconcile_analyze():
    """Compute the connect-time merge. Facts apply immediately (both
    directions, guarded); curation conflicts come back as rows. Only shows
    whose matched release is ALSO on the user's lists participate — pushing
    facts to an unlisted show would create list entries, which is the
    consent-gated auto-track domain (Decision 5, TODO 15). Per entry
    (release_units): each mapped season of a folder reconciles against its
    own entry; unmapped seasons sit out."""
    tok = anilist.load_token()
    if not tok or not tok.get("user_id"):
        return {"pending": False, "facts": [], "conflicts": []}
    if settings.get_setting(RECONCILE_KEY) != str(tok.get("user_id")):
        return {"pending": False, "facts": [], "conflicts": []}
    try:
        data = anilist.anilist_user_data(force=True)
    except Exception as e:
        return {"pending": True, "facts": [], "conflicts": [],
                "error": anilist.friendly_error(e)}
    remote = {e["id"]: e for e in data.get("entries", [])}
    fmt = data.get("score_format") or "POINT_10"
    facts, conflicts = [], []
    scan = library.scan_library()
    library.merge_db(scan)  # the folder's match, season map and watch facts live in hub.db
    seen = set()
    for s in scan:
        for u in mapping.release_units(streaming.trackable(s)):
            aid = u["media_id"]
            if aid not in remote or aid in seen:
                continue
            seen.add(aid)
            e = remote[aid]
            label = e.get("title") or s["title"]
            # Facts (automatic). Both directions, per entry, in the entry's
            # own numbering; a folder that doesn't fit the entry is never
            # pushed (unit_fits). An unknown total (airing) can't rule it out.
            if mapping.unit_fits(u, e.get("episodes")):
                watched = [n for ep, n in u["eps"] if ep["watched"]]
                local_highest = max(watched) if watched else 0
                remote_progress = e.get("progress") or 0
                if local_highest > remote_progress:
                    if not outbox_pending_for(aid):
                        outbox_enqueue(aid, patch={"progress": local_highest})
                    facts.append({"show": label, "kind": "progress",
                                  "remote": remote_progress, "local": local_highest})
                elif remote_progress > local_highest:
                    marked = []
                    for ep, n in u["eps"]:
                        if not ep["watched"] and n <= remote_progress:
                            progress.set_watched(ep["id"], True)
                            marked.append(n)
                    if marked:
                        facts.append({"show": label, "kind": "watched",
                                      "remote": remote_progress, "local": local_highest})
            # Curation (user decides): only when the local overlay holds an
            # opinion — a never-touched show has no local values to defend.
            cur = library.curation_get("aid:%d" % aid) or {}
            r_status, l_status = e.get("status"), cur.get("status")
            r_score = anilist._score_norm(e.get("score100"), fmt)
            l_score = anilist._score_norm(cur.get("score"), fmt)
            status_diff = l_status and r_status and l_status != r_status
            score_diff = l_score is not None and r_score is not None and \
                abs(l_score - r_score) > 0.51
            if status_diff or score_diff:
                conflicts.append({
                    "media_id": aid, "show_id": s["id"], "title": label,
                    "remote": {"status": r_status, "score": e.get("score")},
                    "local": {"status": l_status, "score": cur.get("score")},
                    "diff": ("status" if status_diff else "") +
                            ("+score" if status_diff and score_diff else
                             ("score" if score_diff else "")),
                })
    return {"pending": True, "facts": facts, "conflicts": conflicts}


def outbox_pending_for(media_id):
    with database.db() as c:
        return c.execute("SELECT COUNT(*) FROM outbox WHERE media_id=?",
                         (media_id,)).fetchone()[0]


def outbox_raise_progress(media_id, progress):
    """Raise the newest queued row's progress when it is a plain
    progress-only intent (the auto-track/bulk shape) below `progress`.
    Rows carrying anything else are the user's own edits — left alone.
    Returns True when a row was raised."""
    with database.db() as c:
        row = c.execute("SELECT id, payload FROM outbox WHERE media_id=?"
                        " ORDER BY id DESC LIMIT 1", (media_id,)).fetchone()
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


def reconcile_apply(decisions):
    """Apply the user's curation choices. 'anilist' adopts the remote values
    into the local overlay (no write needed — already there); 'local' keeps
    the overlay and pushes its values to AniList through the outbox (rate
    -limit friendly, and it survives a post-connect connection drop)."""
    applied = 0
    remote_known = {}
    try:
        data = anilist.anilist_user_data()
        remote_known = {e["id"]: e for e in data.get("entries", [])}
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
                cpatch["score"] = e["score"]
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
