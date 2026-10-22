"""AniList API: requests, rate limiting, OAuth, entry state and user data."""

import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import database, sync, util
from .config import config


ANILIST_URL = "https://graphql.anilist.co"
# The spine's cache query. genres + nextAiringEpisode landed with the
# round-4 UI rework (genre hints/tray, home airing panel, stats panels);
# old caches just lack the fields until the next refresh — additive
# (airingAt joined for the home countdown, TODO 48).
ANILIST_USER_QUERY = """query($name:String){
User(name:$name){ name mediaListOptions{scoreFormat} statistics{anime{episodesWatched minutesWatched meanScore count}} favourites{anime(perPage:50){nodes{id}}} }
MediaListCollection(userName:$name, type:ANIME){
lists{ entries{ status progress score(format:POINT_10) score100: score(format:POINT_100) updatedAt media{ id status season episodes format duration startDate{year} genres nextAiringEpisode{ episode timeUntilAiring airingAt } coverImage{extraLarge} bannerImage title{english romaji} } } }
}}"""
MEDIA_FIELDS = """id title{romaji english native} format episodes duration averageScore seasonYear startDate{year}
coverImage{extraLarge} bannerImage description
relations{edges{relationType node{id format}}}
streamingEpisodes{title thumbnail}"""
MEDIA_QUERY_SEARCH = "query($search:String){Media(search:$search,type:ANIME){" + MEDIA_FIELDS + "}}"
MEDIA_QUERY_ID = "query($id:Int){Media(id:$id,type:ANIME){" + MEDIA_FIELDS + "}}"
MEDIA_QUERY_CANDIDATES = "query($search:String){Page(perPage:10){media(search:$search,type:ANIME){" + MEDIA_FIELDS + "}}}"


def anilist_cache_read():
    """The cached AniList list payload WITHOUT touching the network — the
    spine's data source. /api/library polls every few seconds and must never
    stall on a GraphQL round-trip; freshness comes from the background
    refresh loop and /api/anilist visits instead. Returns None when absent
    or stale."""
    cached = anilist_cache_stale()
    if cached and time.time() - cached.get("_ts", 0) < 900:
        return cached
    return None


def anilist_cache_stale():
    """The last-known AniList cache at ANY age — the spine's fallback when
    the fresh cache has expired but the account is connected (Decision 6:
    a failed warm-up, a long session without an AniList tab visit, or an
    outage must not silently flip the library into local mode). None only
    when nothing has ever been fetched."""
    try:
        cached = json.loads(config.ANILIST_CACHE.read_text())
        if cached.get("user"):
            return cached
    except Exception:
        pass
    return None


# --- AniList entry state (TODO 9): authenticated per-release reads + edits ---

ENTRY_STATE_QUERY = """query($id:Int,$fmt:ScoreFormat){
Media(id:$id,type:ANIME){
 id format episodes popularity favourites
 nextAiringEpisode{ airingAt episode timeUntilAiring }
 studios(isMain:true){ nodes{ name } }
 mediaListEntry{ status score(format:$fmt) progress repeat startedAt{year month day} completedAt{year month day} }
}
Viewer{ mediaListOptions{scoreFormat} favourites{ anime(perPage:50){ pageInfo{hasNextPage} nodes{id} } } }
}"""

# Media.isFavourite is unreliable: AniList keeps serving the old value for
# minutes after a ToggleFavourite (found live 2026-10-22), so the panel and
# the toggle's verify read it wrong. The Viewer's own favourites list is
# current — is_favourite is read from it instead.
FAVOURITES_PAGE_QUERY = """query($page:Int){ Viewer{ favourites{
 anime(page:$page,perPage:50){ pageInfo{hasNextPage} nodes{id} } } } }"""

ENTRY_STATUS = ("CURRENT", "PLANNING", "COMPLETED", "DROPPED", "PAUSED",
                "REPEATING")

_SCORE_MAX = {"POINT_100": 100, "POINT_10_DECIMAL": 10, "POINT_10": 10,
              "POINT_5": 5, "POINT_3": 3}


def _score_norm(value, fmt):
    """A user-format score → the 0-100 scale (Decision 23.6: precision
    -preserving comparisons — POINT_10's 8 and DECIMAL's 8.0 are the same
    vote, and 8.5 must not round-conflict against a stale POINT_10 read)."""
    if value is None or fmt not in _SCORE_MAX:
        return None
    return float(value) * 100.0 / _SCORE_MAX[fmt]


def _score_format():
    # Any age: the user's scoring format rarely changes, and falling back to
    # POINT_10 after 15 min offline would save an 8/10 as 8/100.
    cached = anilist_cache_stale()
    return (cached or {}).get("score_format") or "POINT_10"


def _fuzzy_date_str(fd):
    """AniList FuzzyDate {year,month,day} → 'YYYY-MM-DD' (partial OK)."""
    if not fd or not fd.get("year"):
        return None
    out = str(fd["year"])
    if fd.get("month"):
        out += "-%02d" % fd["month"]
        if fd.get("day"):
            out += "-%02d" % fd["day"]
    return out


def _cache_del(key):
    with database.db() as c:
        c.execute("DELETE FROM catalog_cache WHERE key=?", (key,))


def anilist_entry_state(media_id):
    """Live AniList release state for the editing surface (TODO 9): the
    user's list entry, score in THEIR scoring format, favourite flag, plus
    the read-only info Decision 10 wants (popularity, studios, airing
    countdown). Authenticated — mediaListEntry/isFavourite need the Viewer.
    Cached ~30 s so panel re-renders don't hammer the API."""
    key = "entry-state:v1:%d" % media_id
    cached = database.cache_get(key, ttl=30)
    if cached:
        return cached
    fmt = _score_format()
    data = anilist_mutation(ENTRY_STATE_QUERY, {"id": media_id, "fmt": fmt})
    if not data:
        return None
    m = (data.get("Media") if isinstance(data, dict) else None)
    if not m:
        return None
    st = m.get("mediaListEntry") or {}
    out = {
        "media_id": m["id"],
        "format": m.get("format"),
        "episodes": m.get("episodes"),
        "popularity": m.get("popularity"),
        "favourites": m.get("favourites"),
        "is_favourite": _in_favourites(m["id"], ((data.get("Viewer") or {})
                                                 .get("favourites") or {}).get("anime")),
        "studios": [n.get("name") for n in
                    ((m.get("studios") or {}).get("nodes") or [])
                    if n.get("name")],
        "next_episode": ({"number": m["nextAiringEpisode"]["episode"],
                          "in_seconds": m["nextAiringEpisode"]["timeUntilAiring"]}
                         if m.get("nextAiringEpisode") else None),
        "score_format": fmt,
        "score_max": _SCORE_MAX.get(fmt, 10),
        "entry": {
            "status": st.get("status"),
            "score": st.get("score"),
            "progress": st.get("progress"),
            "repeat": st.get("repeat") or 0,
            "started_at": _fuzzy_date_str(st.get("startedAt")),
            "completed_at": _fuzzy_date_str(st.get("completedAt")),
        },
    }
    database.cache_put(key, out)
    return out


def _fav_page(conn):
    """A favourites page → (ids, has_next)."""
    conn = conn or {}
    return ({n.get("id") for n in conn.get("nodes") or []},
            bool((conn.get("pageInfo") or {}).get("hasNextPage")))


def _in_favourites(media_id, first):
    """Is media_id on the Viewer's favourites? Starts from the page the
    caller already has and walks the rest only while it is not found."""
    ids, more = _fav_page(first)
    page = 1
    while media_id not in ids and more and page < 20:
        page += 1
        data = anilist_mutation(FAVOURITES_PAGE_QUERY, {"page": page}) or {}
        ids, more = _fav_page(((data.get("Viewer") or {}).get("favourites") or {}).get("anime"))
    return media_id in ids


def _fuzzy_date_frag(s):
    """"YYYY-MM-DD"/"YYYY-MM"/"YYYY" → FuzzyDateInput fragment, or None."""
    if not s:
        return None
    parts = str(s).split("-")
    try:
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else None
        d = int(parts[2]) if len(parts) > 2 else None
    except (ValueError, TypeError):
        return None
    if not 1 <= y <= 2100:
        return None
    frag = "year: %d" % y
    if m:
        if not 1 <= m <= 12:
            return None
        frag += ", month: %d" % m
        if d:
            if not 1 <= d <= 31:
                return None
            frag += ", day: %d" % d
    return frag


def anilist_entry_save(media_id, patch):
    """SaveMediaListEntry through the chokepoint — only fields present in
    the patch are sent (Decision 18.4: changed fields, not stale entries).
    Returns the fresh entry state, or an {"error": ...} dict on failure."""
    fields = []
    if "status" in patch:
        if patch["status"] not in ENTRY_STATUS:
            return {"error": "invalid status"}
        fields.append("status: %s" % patch["status"])
    if "score" in patch:
        fields.append("score: %s" % repr(float(patch["score"])))
    if "progress" in patch:
        fields.append("progress: %d" % int(patch["progress"]))
    if "repeat" in patch:
        fields.append("repeat: %d" % int(patch["repeat"]))
    for name, key in (("startedAt", "started_at"), ("completedAt", "completed_at")):
        if key in patch:
            if patch[key] in ("", None):
                # Cleared in the panel: AniList clears a FuzzyDate on all-null.
                frag = "year: null, month: null, day: null"
            else:
                frag = _fuzzy_date_frag(patch[key])
                if frag is None:
                    return {"error": "invalid date"}
            fields.append("%s: {%s}" % (name, frag))
    if not fields:
        return anilist_entry_state(media_id)
    q = ("mutation($id:Int){SaveMediaListEntry(mediaId:$id,"
         "%s){id}}") % ", ".join(fields)
    # AniListAuthError (dead token → outbox dormancy, TODO 14) and
    # ValueError (mutation rejected → backoff) propagate to the caller:
    # outbox_flush must SEE them — swallowing them into an error dict made
    # the flush delete rejected rows as if sent (found live 2026-10-16).
    if anilist_mutation(q, {"id": media_id}) is None:
        return {"error": "AniList rejected the update"}
    _cache_del("entry-state:v1:%d" % media_id)
    try:
        return anilist_entry_state(media_id)
    except AniListAuthError as e:
        return {"error": e.reason}


def anilist_favourite_set(media_id, want):
    """ToggleFavourite is not idempotent (Decision 18.4): read the current
    flag, toggle only when it differs from the desired boolean, verify.
    The toggle answers with the favourites list itself — that is the
    verify when it is decisive; a fresh read covers the rest. Returns
    fresh state, or an {"error": ...} dict."""
    _cache_del("entry-state:v1:%d" % media_id)  # never decide on a 30 s old flag
    try:
        state = anilist_entry_state(media_id)
    except AniListAuthError as e:
        return {"error": e.reason}
    if state is None:
        return {"error": "AniList has no such media"}
    if state["is_favourite"] == bool(want):
        return state
    q = ("mutation($id:Int){ToggleFavourite(animeId:$id){"
         "anime(perPage:50){pageInfo{hasNextPage} nodes{id}}}}")
    # Same contract as anilist_entry_save: auth/validation errors raise.
    res = anilist_mutation(q, {"id": media_id})
    if res is None:
        return {"error": "AniList rejected the update"}
    toggled = (res.get("ToggleFavourite") or {}) if isinstance(res, dict) else {}
    now = None  # undecided: the id may sit on a later page
    if "anime" in toggled:
        ids, more = _fav_page(toggled["anime"])
        if media_id in ids or not more:
            now = media_id in ids
    _cache_del("entry-state:v1:%d" % media_id)
    try:
        fresh = anilist_entry_state(media_id)
    except AniListAuthError as e:
        return {"error": e.reason}
    if fresh and now is not None and fresh["is_favourite"] != now:
        fresh = {**fresh, "is_favourite": now}
        database.cache_put("entry-state:v1:%d" % media_id, fresh)
    if fresh and fresh["is_favourite"] != bool(want):
        return {"error": "AniList did not confirm the change"}
    return fresh


# One pace for every AniList request, so none of them hits the rate limit
# (each 429 parks a thread for up to a minute). AniList allows ~30 a minute
# while degraded; background passes (`_al_ctx.background`) leave a reserve,
# so a page someone just opened is fetched at once.
ANILIST_PER_MIN = 28
ANILIST_BACKGROUND_PER_MIN = 20
_al_gate = threading.Lock()
_al_times = []
_al_ctx = threading.local()


def _anilist_try(limit, now):
    """Take a request slot in the last minute's window: 0 when taken, else
    the seconds until one frees up."""
    with _al_gate:
        _al_times[:] = [t for t in _al_times if now - t < 60]
        if len(_al_times) < limit:
            _al_times.append(now)
            return 0
        return 60 - (now - _al_times[0])


def _anilist_slot():
    limit = ANILIST_BACKGROUND_PER_MIN if getattr(_al_ctx, "background", False) else ANILIST_PER_MIN
    while True:
        wait = _anilist_try(limit, time.time())
        if not wait:
            return
        time.sleep(min(max(wait, 0.05), 2))


def background_work(fn):
    """Run `fn` as background AniList work: it keeps a reserve free for
    what the user is waiting on."""
    def run(*args, **kwargs):
        _al_ctx.background = True
        return fn(*args, **kwargs)
    return run


# When AniList last answered a request: the outbox loop pushes held writes
# right away once AniList is back after an outage (TODO 14).
last_ok = 0.0


def anilist_request(payload, token=None):
    global last_ok
    for attempt in range(4):
        _anilist_slot()
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json",
                       "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"}
            if token:
                # The token rides only in this header — never in a URL, a
                # log line or an error payload (Decision 20 redaction rule).
                headers["Authorization"] = "Bearer " + token
            req = Request(ANILIST_URL, data=json.dumps(payload).encode(), headers=headers)
            with urlopen(req, timeout=20) as r:
                out = json.loads(r.read())
            last_ok = time.time()
            return out
        except Exception as e:
            if getattr(e, "code", None) == 429 and attempt < 3:
                # Honour AniList's Retry-After (usually well under a minute)
                # instead of always parking the handler thread for 62 s.
                try:
                    wait = int(e.headers.get("Retry-After"))
                except (AttributeError, TypeError, ValueError):
                    wait = 62
                time.sleep(min(max(wait, 1), 62))
                continue
            raise


def strip_html(text):
    t = re.sub(r"<[^>]+>", "", text or "")
    for a, b in (("&quot;", '"'), ("&#039;", "'"), ("&amp;", "&"), ("&mdash;", "—"), ("&ndash;", "–"), ("&hellip;", "…")):
        t = t.replace(a, b)
    return t.strip()


def load_anilist_username():
    """Which AniList account to READ: the connected token's account — the
    token IS the account (Decision 20; store_token persists user_name from
    the verified Viewer). A config username can no longer override it
    (found live 2026-10-17: the README's example value pinned a stranger's
    public lists over Elias's connected account on the laptop). Returns
    None in no-AniList mode."""
    return (load_token() or {}).get("user_name")


def anilist_connected():
    """Whether the app is in AniList mode (Decision 6's switch, TODO 17):
    a usable OAuth token defines the connection. Nothing else — not the
    config username, not the cache — may hold the library in AniList mode
    once the user disconnects."""
    tok = load_token()
    return bool(tok) and (tok.get("expires_at") is None
                          or tok["expires_at"] >= time.time())


# --- AniList OAuth + the single write chokepoint (TODO 19, Decisions 17.1+20) ---
#
# Implicit grant: the hub UI opens AniList's authorize page; AniList redirects
# to /auth/callback#access_token=… on this server. The fragment never reaches
# a server — the callback page's own JS reads it, strips it from the address
# bar, and POSTs it to the hub once. Manual token paste is the fallback and
# the ~1-year-expiry recovery path. No client secret is bundled (Decision 20:
# authorization-code flow never built).

VIEWER_QUERY = "query{Viewer{id name}}"


class AniListAuthError(Exception):
    """The saved AniList token is missing, expired or rejected — the caller
    (and through it the UI) must prompt for re-auth, not retry blindly."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class AniListTransientError(Exception):
    """Network/server-side failure — not the caller's fault, worth retrying.
    The outbox queues writes that fail with this (TODO 14)."""
    def __init__(self, reason, status=None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def friendly_error(e):
    """An AniList failure in the app's words, for the UI (the counterpart
    of streaming.friendly_stream_error): never "HTTP Error 403: Forbidden".
    An unexpected failure is logged with its details."""
    if isinstance(e, AniListAuthError):
        return e.reason
    code = e.status if isinstance(e, AniListTransientError) else getattr(e, "code", None)
    if code == 429:
        return "AniList is limiting requests — try again in a minute."
    if code == 404:
        return "AniList has no entry with this id."
    if code == 403 or (isinstance(code, int) and code >= 500) \
            or isinstance(e, (URLError, TimeoutError, ConnectionError, AniListTransientError)):
        return "AniList is unreachable right now — try again later."
    util.log("AniList: %s" % e)
    return "Something went wrong talking to AniList."


def load_token():
    """The stored OAuth token dict, or None. A corrupt file counts as
    absent — auth state must degrade to 'not connected', never traceback."""
    try:
        tok = json.loads(config.ANILIST_TOKEN_FILE.read_text())
        if isinstance(tok, dict) and isinstance(tok.get("access_token"), str) and tok["access_token"]:
            return tok
    except Exception:
        pass
    return None


def store_token(access_token, expires_in=None, user=None):
    """Persist the token with owner-only permissions, atomically, outside
    the repo. expires_at (epoch seconds) is kept when the grant reported
    one — AniList tokens live ~1 year and expiry is routine (Decision 20)."""
    rec = {"access_token": access_token,
           "expires_at": (time.time() + expires_in) if expires_in else None,
           "user_id": (user or {}).get("id"), "user_name": (user or {}).get("name")}
    tmp = config.ANILIST_TOKEN_FILE.with_suffix(".tmp")
    # Created 0600 — never briefly world-readable under the default umask.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(rec))
    os.chmod(tmp, 0o600)  # an existing .tmp keeps its old mode through O_CREAT
    os.replace(tmp, config.ANILIST_TOKEN_FILE)
    os.chmod(config.ANILIST_TOKEN_FILE, 0o600)
    return rec


def clear_token():
    config.ANILIST_TOKEN_FILE.unlink(missing_ok=True)


def auth_disconnect():
    """Symmetric disconnect (Decision 6, TODO 17): unlinking converts back
    to the local variant in place — the token goes, and with it the account
    (reads follow the token's account, so nothing can hold AniList mode),
    the pending reconcile marker goes. Curation and watch facts are
    untouched: no data is lost in either direction, AniList state is an
    overlay on top of local state."""
    clear_token()
    sync.reconcile_clear()
    return {"ok": True}


def auth_status():
    """Connection state for the UI. The token itself never appears here —
    only who is connected and until when (Decision 20 redaction rule)."""
    tok = load_token()
    if not tok:
        return {"connected": False, "client_id_configured": bool(config.ANILIST_CLIENT_ID),
                "redirect_uri": f"http://localhost:{config.PORT}/auth/callback"}
    expired = tok.get("expires_at") is not None and tok["expires_at"] < time.time()
    return {"connected": not expired, "user": tok.get("user_name"), "user_id": tok.get("user_id"),
            "expires_at": tok.get("expires_at"), "expired": expired,
            "client_id_configured": bool(config.ANILIST_CLIENT_ID),
            "redirect_uri": f"http://localhost:{config.PORT}/auth/callback"}


_AUTH_PENDING_TS = 0.0


def auth_start():
    """Build the authorize URL for the configured client and mark a flow as
    pending — /auth/callback only accepts tokens while a started flow is
    fresh (login correlation, READINESS A5). Returns the URL for the UI to
    open in the user's browser (their AniList session lives there)."""
    global _AUTH_PENDING_TS
    if not config.ANILIST_CLIENT_ID:
        return {"error": "No AniList client id configured — add \"anilist_client_id\": <id> to the config file."}
    _AUTH_PENDING_TS = time.time()
    # No redirect_uri parameter: AniList's implicit grant (unlike its
    # authorization-code grant) takes the redirect from the client's
    # registered settings, and passing it anyway makes the authorize
    # endpoint answer unsupported_grant_type (found live 2026-10-15).
    return {"url": (f"https://anilist.co/api/v2/oauth/authorize"
                    f"?client_id={config.ANILIST_CLIENT_ID}&response_type=token")}


def auth_finish(access_token, expires_in=None, require_pending=False):
    """Validate a presented token against AniList's Viewer and store it.
    This is the ONLY code path that persists a token — both the OAuth
    callback and the manual paste land here, so validation can't be skipped.
    The presented token never enters an error message or a log line."""
    global _AUTH_PENDING_TS
    if require_pending:
        # A callback POST is only meaningful minutes after a deliberate
        # start; anything else is a stale page or a drive-by POST.
        if not _AUTH_PENDING_TS or time.time() - _AUTH_PENDING_TS > 900:
            return {"error": "No AniList connection attempt in progress — start it from the app."}
        _AUTH_PENDING_TS = 0.0
    try:
        data = anilist_request({"query": VIEWER_QUERY}, token=access_token)
    except HTTPError as e:
        # AniList answers a bad token with HTTP 400 + GraphQL errors — that's
        # a rejection, not an outage, and the two must not sound alike.
        try:
            msg = _graphql_error_msg(json.loads(e.read() or b"{}")) or ""
        except Exception:
            msg = ""
        if re.search(r"invalid|unauthenticated|bad auth", msg, re.I):
            return {"error": "AniList rejected that token — create a fresh one."}
        return {"error": f"AniList refused to validate that token (HTTP {e.code})."}
    except Exception:
        return {"error": "Could not reach AniList to verify that token."}
    viewer = ((data or {}).get("data") or {}).get("Viewer")
    if not viewer or not viewer.get("name"):
        # AniList rejected it. Say so without echoing the token back.
        return {"error": "AniList rejected that token — create a fresh one."}
    store_token(access_token, expires_in, viewer)
    cached = anilist_cache_stale()
    if cached and (cached.get("user") or {}).get("name") != viewer["name"]:
        # A different account: the cached lists are someone else's.
        config.ANILIST_CACHE.unlink(missing_ok=True)
    # Reads follow this token's account (load_anilist_username reads
    # user_name from the token file) — no separate binding to maintain.
    # Decision 24: reconnect with the SAME account flushes the held queue
    # (the 60 s loop picks it up now that a valid token exists); a
    # DIFFERENT account's intents are discarded — never sent across.
    discarded = sync.outbox_discard_other_accounts(viewer.get("id"))
    # Decision 14: a (re)connect is a reconciliation event — the dialog
    # compares local watch state/curation against the account until the
    # user applies or dismisses.
    sync.reconcile_mark_pending(viewer.get("id"))
    return {"ok": True, "user": viewer["name"],
            **({"discarded": discarded} if discarded else {})}


def _graphql_error_msg(data):
    """AniList's GraphQL error messages from a response body, or None when
    the body carries no errors. Never contains the request's token."""
    errors = (data or {}).get("errors")
    if not errors:
        return None
    msgs = [str(e.get("message") or "unknown error") for e in errors if isinstance(e, dict)]
    return "; ".join(msgs) or "unknown error"


def anilist_mutation(query, variables=None):
    """THE single write chokepoint (Decision 17.1): every AniList mutation
    in the app goes through this one function, so v0.2's offline queue
    (Decision 18.4 / TODO 14) becomes a wrapper around it instead of a
    rewrite, and rate limits, retries and auth handling exist exactly once.
    Reads that need authentication may also use it; public reads stay on
    anilist_request. Raises AniListAuthError when there is no usable token —
    callers surface that as a re-auth prompt, never as a silent skip."""
    tok = load_token()
    if not tok:
        raise AniListAuthError("not connected")
    if tok.get("expires_at") is not None and tok["expires_at"] < time.time():
        raise AniListAuthError("token expired — reconnect AniList")
    try:
        data = anilist_request({"query": query, "variables": variables or {}},
                               token=tok["access_token"])
    except HTTPError as e:
        # AniList answers validation failures with HTTP 400 + a GraphQL
        # errors body (found live: a bad mutation argument). Surface the
        # real message; token-death is still signalled the same way; server
        #-side trouble (5xx) is transient — the outbox's domain, not the
        # user's.
        try:
            body = json.loads(e.read() or b"{}")
        except Exception:
            body = {}
        msg = _graphql_error_msg(body) or f"HTTP {e.code}"
        if re.search(r"invalid token|unauthenticated|bad auth", msg, re.I):
            clear_token()
            raise AniListAuthError("AniList rejected the saved token — reconnect")
        if e.code >= 500:
            raise AniListTransientError(f"AniList server error (HTTP {e.code})",
                                        status=e.code)
        if e.code in (403, 429):
            # An outage, not a bad request (found live 2026-10-21: "The
            # AniList API has been temporarily disabled" as HTTP 403): queue
            # and retry the write instead of dropping it as a validation error.
            raise AniListTransientError(f"AniList unavailable (HTTP {e.code}): {msg}", status=e.code)
        raise ValueError(f"AniList mutation failed: {msg}")
    except (URLError, OSError, TimeoutError) as e:
        # Connection-level failure: the definition of "offline" (Decision 3).
        raise AniListTransientError(f"Could not reach AniList: {e}")
    msg = _graphql_error_msg(data)
    if msg:
        # AniList signals dead tokens as a GraphQL error, not an HTTP one.
        if re.search(r"invalid token|unauthenticated|bad auth", msg, re.I):
            clear_token()
            raise AniListAuthError("AniList rejected the saved token — reconnect")
        raise ValueError(f"AniList mutation failed: {msg}")
    return (data or {}).get("data")


def refresh_user_data(force=False):
    """anilist_user_data for background threads: a failure is one log line
    and the cached lists stay in use, not a thread traceback."""
    try:
        anilist_user_data(force=force)
    except Exception as e:
        util.log("AniList lists not refreshed: %s" % e)


def anilist_user_data(force=False):
    if load_anilist_username() is None:
        # No-AniList mode (Decision 6): a readable payload shape, not a 502 —
        # the UI renders "not connected" rather than "unavailable".
        return {"user": None, "entries": []}
    if not force and config.ANILIST_CACHE.exists():
        try:
            cached = json.loads(config.ANILIST_CACHE.read_text())
            if time.time() - cached.get("_ts", 0) < 600:
                return {k: v for k, v in cached.items() if k != "_ts"}
        except Exception:
            pass
    data = anilist_request({"query": ANILIST_USER_QUERY, "variables": {"name": load_anilist_username()}})
    u = data["data"]["User"]
    entries = {}
    for lst in (data["data"].get("MediaListCollection") or {}).get("lists", []):
        for e in lst.get("entries", []):
            mid = e["media"]["id"]
            cur = entries.get(mid)
            if cur is None or (cur["status"] == "PLANNING" and e["status"] != "PLANNING"):
                t = e["media"].get("title") or {}
                entries[mid] = {
                    "id": mid,
                    "title": t.get("english") or t.get("romaji"),
                    "status": e["status"],
                    "progress": e.get("progress") or 0,
                    "score": e.get("score") or 0,
                    "score100": e.get("score100"),
                    "episodes": e["media"].get("episodes"),
                    "format": e["media"].get("format"),
                    "duration": e["media"].get("duration"),
                    "year": (e["media"].get("startDate") or {}).get("year"),
                    "genres": e["media"].get("genres") or [],
                    # `at` (airingAt, unix seconds) is additive: the home
                    # countdown stays right however old the cache is.
                    "next_episode": ({"number": e["media"]["nextAiringEpisode"]["episode"],
                                      "in_seconds": e["media"]["nextAiringEpisode"]["timeUntilAiring"],
                                      "at": e["media"]["nextAiringEpisode"].get("airingAt")}
                                     if e["media"].get("nextAiringEpisode") else None),
                    "airing": e["media"].get("status"),
                    "season": e["media"].get("season"),
                    "poster": (e["media"].get("coverImage") or {}).get("extraLarge"),
                    "banner": e["media"].get("bannerImage"),
                    "updated": e.get("updatedAt"),
                }
    out = {
        "user": {
            "name": u["name"],
            "episodes_watched": u["statistics"]["anime"]["episodesWatched"],
            "minutes_watched": u["statistics"]["anime"]["minutesWatched"],
            "mean_score": u["statistics"]["anime"]["meanScore"],
            "count": u["statistics"]["anime"]["count"],
        },
        "score_format": ((u.get("mediaListOptions") or {}).get("scoreFormat")
                         or "POINT_10"),
        "entries": list(entries.values()),
        "favourites": [n["id"] for n in (((u.get("favourites") or {}).get("anime") or {}).get("nodes") or [])],
    }
    # Atomic: request threads read this file concurrently, and a torn read
    # would briefly flip the spine into local mode.
    tmp = config.ANILIST_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out | {"_ts": time.time()}))
    os.replace(tmp, config.ANILIST_CACHE)
    return out
