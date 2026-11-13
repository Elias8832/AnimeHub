"""MyAnimeList as the account (Decision 75).

One account at a time: AniList or MyAnimeList. With MAL connected, the
user's lists (the library's sections), tracking and list edits go to MAL;
AniList stays the catalog (show info, art, airing, seasons, search), read
without a login. Every MAL entry is turned into its AniList entry through
AniList's own `idMal`, so the rest of the app keeps keying by AniList id:
`user_data()` writes the same list cache `anilist.anilist_user_data()`
does, in the same shape.

MAL's API: OAuth 2 authorization code with PKCE (plain only), an app of
type "other" has no secret. Access tokens are refreshed with the refresh
token when they run out. Favourites have no API, so they are not offered.
"""

import json
import os
import secrets
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import database, system, util
from .config import config

AUTH_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
API_URL = "https://api.myanimelist.net/v2"
UA = "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"
NAME = "MyAnimeList"

# MAL list status <-> AniList's. MAL keeps a rewatch as its own flag
# (`is_rewatching`) on a Completed entry; AniList calls it REPEATING.
STATUS_FROM_MAL = {"watching": "CURRENT", "completed": "COMPLETED", "on_hold": "PAUSED",
                   "dropped": "DROPPED", "plan_to_watch": "PLANNING"}
STATUS_TO_MAL = {v: k for k, v in STATUS_FROM_MAL.items()}
STATUS_TO_MAL["REPEATING"] = "completed"

LIST_FIELDS = "list_status{status,score,num_episodes_watched,is_rewatching,updated_at,start_date,finish_date,num_times_rewatched}"
ENTRY_FIELDS = "my_list_status{status,score,num_episodes_watched,is_rewatching,updated_at,start_date,finish_date,num_times_rewatched},num_episodes"

# MAL states no rate limit; quick bursts get the address blocked for a
# while. Half a second between requests is what its staff suggested.
MIN_GAP = 0.5
_gate = threading.Lock()
_last = [0.0]
# When MAL last answered: the outbox loop pushes held writes at once after
# an outage, as it does for AniList (TODO 14).
last_ok = 0.0


def _errors():
    from . import anilist  # anilist imports this module
    return anilist.AniListAuthError, anilist.AniListTransientError


def _auth_error(reason):
    return _errors()[0](reason)


def _transient(reason, status=None):
    return _errors()[1](reason, status=status, service=NAME)


# --- the token -------------------------------------------------------------

def load_token():
    """The stored MAL token dict, or None (a corrupt file counts as absent)."""
    try:
        tok = json.loads(system.read_bytes(config.MAL_TOKEN_FILE))
        if isinstance(tok, dict) and isinstance(tok.get("access_token"), str) and tok["access_token"]:
            return tok
    except Exception:
        pass
    return None


def store_token(rec):
    """Owner-only and atomic, like the AniList token (Decision 20)."""
    tmp = config.MAL_TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec))
    os.chmod(tmp, 0o600)
    system.replace(tmp, config.MAL_TOKEN_FILE)
    os.chmod(config.MAL_TOKEN_FILE, 0o600)
    return rec


def clear_token():
    config.MAL_TOKEN_FILE.unlink(missing_ok=True)


def _drop_login():
    """MAL refused the login: it ends, and the write that failed keeps its
    account (Decision 24)."""
    from . import anilist  # anilist imports this module
    anilist.note_cleared(account_id())
    clear_token()


def connected():
    """A MAL login is kept. An access token that ran out still counts: the
    refresh token renews it on the next request, and a refused refresh
    removes the login (then this answers False)."""
    tok = load_token()
    return bool(tok) and (bool(tok.get("refresh_token")) or tok.get("expires_at") is None
                          or tok["expires_at"] >= time.time())


def account_id():
    """The outbox's account key: "mal:<id>", never equal to an AniList id."""
    tok = load_token() or {}
    return "mal:%s" % tok["user_id"] if tok.get("user_id") is not None else None


def account_name():
    return (load_token() or {}).get("user_name")


def status():
    tok = load_token()
    return {"connected": connected(), "user": (tok or {}).get("user_name"),
            "user_id": (tok or {}).get("user_id"), "client_id_configured": bool(config.MAL_CLIENT_ID),
            "redirect_uri": f"http://localhost:{config.PORT}/auth/mal/callback"}


def _token_fields(data):
    if not isinstance(data, dict) or not data.get("access_token"):
        raise _auth_error("MyAnimeList sent no login")
    ei = data.get("expires_in")
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + ei if isinstance(ei, (int, float)) and ei > 0 else None}


def _token_call(fields):
    """POST to MAL's token endpoint. A refused grant is an auth error;
    anything else (offline, MAL down) is transient. Never logs the body."""
    global last_ok
    body = urlencode({"client_id": config.MAL_CLIENT_ID or "", **fields}).encode()
    req = Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": UA})
    _pace()
    try:
        with urlopen(req, timeout=20) as r:
            out = json.loads(r.read() or b"{}")
        last_ok = time.time()
        return out
    except HTTPError as e:
        # 403 is MAL blocking the address for a while (as in api()), not a
        # refused grant: it must not end the login.
        if e.code in (400, 401):
            raise _auth_error("MyAnimeList refused the login")
        raise _transient("MyAnimeList login server error (HTTP %d)" % e.code, status=e.code)
    except (URLError, OSError, TimeoutError, ValueError) as e:
        raise _transient("Could not reach MyAnimeList: %s" % e)


_refresh_lock = threading.Lock()


def _refresh(used):
    """Swap the refresh token for a new access token. `used` is the access
    token that ran out: when another thread already replaced it, that one
    is used. A refused refresh ends the login."""
    with _refresh_lock:
        cur = load_token()
        if not cur:
            raise _auth_error("not connected")
        if cur["access_token"] != used:
            return cur
        if not cur.get("refresh_token"):
            _drop_login()
            raise _auth_error("MyAnimeList login expired — reconnect")
        try:
            data = _token_call({"grant_type": "refresh_token", "refresh_token": cur["refresh_token"]})
            fresh = _token_fields(data)
        except _errors()[0]:
            _drop_login()
            raise _auth_error("MyAnimeList login expired — reconnect")
        rec = {**cur, **{k: v for k, v in fresh.items() if v is not None or k == "expires_at"}}
        return store_token(rec)


def _pace():
    with _gate:
        wait = _last[0] + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def _error_text(e):
    try:
        body = json.loads(e.read() or b"{}")
    except Exception:
        return ""
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or "")[:200]
    return ""


def api(method, path, params=None, form=None, token=None):
    """One MAL API request with the saved login (or `token`, for the
    connect step). Raises AniListAuthError (login gone), AniListTransientError
    (offline, MAL down or limiting) or ValueError (MAL refused the request)."""
    global last_ok
    tok = None
    if token is None:
        tok = load_token()
        if not tok:
            raise _auth_error("not connected")
        if tok.get("expires_at") is not None and tok["expires_at"] - 300 < time.time():
            tok = _refresh(tok["access_token"])
    for attempt in range(2):
        access = token or tok["access_token"]
        url = API_URL + path + ("?" + urlencode(params) if params else "")
        headers = {"Authorization": "Bearer " + access, "Accept": "application/json", "User-Agent": UA}
        data = None
        if form is not None:
            data = urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        _pace()
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=20) as r:
                raw = r.read()
            last_ok = time.time()
            return json.loads(raw) if raw else {}
        except HTTPError as e:
            if e.code == 401:
                if token is not None:
                    raise _auth_error("MyAnimeList refused the login")
                if attempt == 0:
                    tok = _refresh(access)
                    continue
                _drop_login()
                raise _auth_error("MyAnimeList rejected the saved login — reconnect")
            if e.code >= 500 or e.code in (403, 429):
                raise _transient("MyAnimeList unavailable (HTTP %d)" % e.code, status=e.code)
            msg = _error_text(e)
            if e.code == 404:
                raise ValueError("MyAnimeList has no such entry")
            raise ValueError("MyAnimeList refused the change%s" % (": " + msg if msg else ""))
        except (URLError, OSError, TimeoutError) as e:
            raise _transient("Could not reach MyAnimeList: %s" % e)
        except ValueError as e:
            raise _transient("MyAnimeList answered nonsense: %s" % e)
    raise _auth_error("MyAnimeList rejected the saved login — reconnect")


# --- connect ----------------------------------------------------------------
# The hub opens MAL's authorize page; MAL sends the browser back to
# /auth/mal/callback?code=…&state=… on this server, which trades the code
# for a token. No redirect_uri is sent: MAL then uses the app's one
# registered address (port 8787). A hub on another port is still reached
# by pasting the address MAL sent the browser to (auth_paste).

PENDING_MAX = 900
_pending = {}  # state → (verifier, started)
_pending_lock = threading.Lock()


def auth_start():
    if not config.MAL_CLIENT_ID:
        return {"error": "No MyAnimeList app id yet — add \"mal_client_id\" to the config file."}
    verifier = secrets.token_urlsafe(96)[:128]  # PKCE: 43–128 unreserved characters
    state = secrets.token_urlsafe(16)
    now = time.time()
    with _pending_lock:
        for k in [k for k, (_, ts) in _pending.items() if now - ts > PENDING_MAX]:
            del _pending[k]
        _pending[state] = (verifier, now)
    return {"url": AUTH_URL + "?" + urlencode({
        "response_type": "code", "client_id": config.MAL_CLIENT_ID, "state": state,
        "code_challenge": verifier, "code_challenge_method": "plain"})}


def auth_finish(code, state):
    """Trade the code from MAL's redirect for a token, check whose it is,
    store it. "foreign": the state is not one this hub started (another
    hub on port 8787 got the redirect, or a stale page)."""
    with _pending_lock:
        p = _pending.pop(state, None) if isinstance(state, str) else None
    if not p or time.time() - p[1] > PENDING_MAX:
        return {"error": "This hub didn't start that MyAnimeList connection — start it from the app again.",
                "foreign": True}
    if not isinstance(code, str) or not code:
        return {"error": "MyAnimeList sent no code — try again."}
    try:
        rec = _token_fields(_token_call({"grant_type": "authorization_code", "code": code,
                                         "code_verifier": p[0]}))
        me = api("GET", "/users/@me", token=rec["access_token"])
    except _errors()[0]:
        return {"error": "MyAnimeList refused the login — try again."}
    except (_errors()[1], ValueError):
        return {"error": "Could not reach MyAnimeList — try again."}
    if not isinstance(me, dict) or me.get("id") is None or not me.get("name"):
        return {"error": "MyAnimeList didn't say whose account this is — try again."}
    rec.update(user_id=me["id"], user_name=me["name"])
    return _connected(rec)


def auth_paste(text):
    """The address MAL sent the browser to, pasted into the app: the way in
    when the redirect landed on another hub or a closed port."""
    q = parse_qs(urlparse((text or "").strip()).query)
    code, state = (q.get("code") or [None])[0], (q.get("state") or [None])[0]
    if not code or not state:
        return {"error": "That isn't the address MyAnimeList sent you to — it has code= and state= in it."}
    r = auth_finish(code, state)
    if r.get("foreign"):
        r = {"error": "That address belongs to an older try — connect again and paste the new one."}
    return r


def _connected(rec):
    """Store the login; MAL is put in use (Decision 75). An AniList login
    stays, paused, with its queued changes. Decision 24 within MAL: a
    different MAL account's queued changes are dropped, with a count."""
    from . import anilist, sync
    old = load_token()
    store_token(rec)
    cached = anilist._cache_raw("mal")
    if cached and (cached.get("user") or {}).get("name") != rec["user_name"]:
        anilist.cache_path("mal").unlink(missing_ok=True)
    if old and old.get("user_id") != rec["user_id"]:
        anilist.forget_entry_states()
    anilist._activated("mal")
    discarded = sync.outbox_discard_other_accounts("mal:%s" % rec["user_id"])
    return {"ok": True, "user": rec["user_name"], "service": "mal",
            **({"discarded": discarded} if discarded else {})}


# --- AniList as the catalog -------------------------------------------------

CATALOG_FIELDS = """id idMal status averageScore season episodes format duration startDate{year} genres
nextAiringEpisode{ episode timeUntilAiring airingAt } coverImage{extraLarge color} bannerImage title{english romaji}"""
CATALOG_QUERY = ("query($ids:[Int]){Page(page:1,perPage:50){media(idMal_in:$ids,type:ANIME){"
                 + CATALOG_FIELDS + "}}}")
CATALOG_BATCH = 50
# Airing shows change every week (next episode, totals); finished ones
# barely. A MAL id AniList doesn't know is asked again after a few days.
CATALOG_FRESH_AIRING = 600
CATALOG_FRESH = 3 * 86400


def _catalog_key(mal_id):
    return "malcat:v1:%d" % mal_id


def _catalog_fresh(row):
    payload, ts = row
    m = (payload or {}).get("media")
    age = time.time() - ts
    if m and m.get("status") in ("RELEASING", "NOT_YET_RELEASED", "HIATUS"):
        return age < CATALOG_FRESH_AIRING
    return age < CATALOG_FRESH


def catalog(mal_ids, report=None):
    """{mal id: AniList media or None} for every id, from the cache where
    fresh, else AniList in batches of 50. When AniList can't be asked, the
    last answer at any age stands in; an id never asked raises."""
    from . import anilist
    out, ask = {}, []
    cached = database.cache_get_ts_many(_catalog_key(mid) for mid in mal_ids)
    for mid in mal_ids:
        row = cached.get(_catalog_key(mid))
        if row and _catalog_fresh(row):
            out[mid] = row[0].get("media")
        else:
            ask.append(mid)
    for i in range(0, len(ask), CATALOG_BATCH):
        if report:
            report("match", len(mal_ids) - len(ask) + i, len(mal_ids))
        batch = ask[i:i + CATALOG_BATCH]
        try:
            data = anilist.anilist_request({"query": CATALOG_QUERY, "variables": {"ids": batch}})
            found = ((data.get("data") or {}).get("Page") or {}).get("media") or []
        except Exception:
            for mid in ask[i:]:
                row = cached.get(_catalog_key(mid))
                if row is None:
                    raise
                out[mid] = row[0].get("media")
            break
        by_mal = {}
        for m in found:
            if isinstance(m, dict) and m.get("idMal") in batch and m["idMal"] not in by_mal:
                by_mal[m["idMal"]] = m
        rows = []
        for mid in batch:
            m = by_mal.get(mid)
            rows.append((_catalog_key(mid), {"media": m}))
            if m:
                rows.append(("idmal:%d" % m["id"], {"idMal": mid}))
            out[mid] = m
        database.cache_put_many(rows)
    return out


def mal_id_of(media_id):
    """The MAL id of an AniList entry. Raises AniListTransientError when
    AniList can't be asked; None when AniList has no MAL id for it."""
    from . import anilist, franchise
    cached = database.cache_get("idmal:%d" % media_id, 365 * 86400)
    if isinstance(cached, dict):
        return cached.get("idMal") or None
    m = franchise.media_entry(media_id)
    if isinstance(m, dict) and "idMal" in m:
        return m["idMal"] or None
    try:
        data = anilist.anilist_request({"query": "query($id:Int){Media(id:$id,type:ANIME){idMal}}",
                                        "variables": {"id": media_id}})
    except Exception as e:
        raise _transient("AniList unreachable: %s" % e)
    m = (data.get("data") or {}).get("Media") or {}
    database.cache_put("idmal:%d" % media_id, {"idMal": m.get("idMal")})
    return m.get("idMal") or None


# --- the lists --------------------------------------------------------------

def _date(s):
    """MAL's "YYYY-MM-DD" (or shorter) as is; None for nothing."""
    return s if isinstance(s, str) and s[:4].isdigit() else None


def _epoch(iso):
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def status_in(ls):
    if not ls:
        return None
    if ls.get("is_rewatching"):
        return "REPEATING"
    return STATUS_FROM_MAL.get(ls.get("status"))


def _list_pages(report=None, total=None):
    """Every entry on the user's MAL anime list, following MAL's paging."""
    out, offset = [], 0
    for _ in range(50):
        if report:
            report("list", len(out), total)
        page = api("GET", "/users/@me/animelist",
                   {"fields": LIST_FIELDS, "limit": 1000, "offset": offset, "nsfw": "true"})
        rows = page.get("data") or []
        out += rows
        if not (page.get("paging") or {}).get("next") or not rows:
            break
        offset += len(rows)
    return out


def user_data(report=None):
    """The user's MAL lists in anilist_user_data()'s shape, keyed by AniList
    id. Entries AniList has no match for are listed in `unmatched`
    (additive). Totals come from MAL's own statistics."""
    me = api("GET", "/users/@me", {"fields": "anime_statistics"})
    rows = _list_pages(report, (me.get("anime_statistics") or {}).get("num_items"))
    ids = [r["node"]["id"] for r in rows if isinstance(r.get("node"), dict) and isinstance(r["node"].get("id"), int)]
    media = catalog(ids, report)
    entries, unmatched = {}, []
    for r in rows:
        node, ls = r.get("node") or {}, r.get("list_status") or {}
        st = status_in(ls)
        if not isinstance(node.get("id"), int) or not st:
            continue
        m = media.get(node["id"])
        if not m:
            unmatched.append({"mal_id": node["id"], "title": node.get("title"), "status": st})
            continue
        aid = m["id"]
        cur = entries.get(aid)
        if cur is not None and not (cur["status"] == "PLANNING" and st != "PLANNING"):
            continue
        t = m.get("title") or {}
        score = ls.get("score") or 0
        nxt = m.get("nextAiringEpisode")
        entries[aid] = {
            "id": aid,
            "mal_id": node["id"],
            "title": t.get("english") or t.get("romaji") or node.get("title"),
            "status": st,
            "progress": ls.get("num_episodes_watched") or 0,
            "score": score,
            "score100": score * 10 if score else 0,
            "average": m.get("averageScore"),
            "episodes": m.get("episodes"),
            "format": m.get("format"),
            "duration": m.get("duration"),
            "year": (m.get("startDate") or {}).get("year"),
            "genres": m.get("genres") or [],
            "next_episode": ({"number": nxt["episode"], "in_seconds": nxt["timeUntilAiring"],
                              "at": nxt.get("airingAt")} if nxt else None),
            "airing": m.get("status"),
            "season": m.get("season"),
            "poster": (m.get("coverImage") or {}).get("extraLarge"),
            "banner": m.get("bannerImage"),
            "color": (m.get("coverImage") or {}).get("color"),
            "updated": _epoch(ls.get("updated_at")),
            "repeat": ls.get("num_times_rewatched") or 0,
            "started_at": _date(ls.get("start_date")),
            "completed_at": _date(ls.get("finish_date")),
        }
    stats = me.get("anime_statistics") or {}
    days = stats.get("num_days_watched")
    mean = stats.get("mean_score")
    return {
        "service": "mal",
        "user": {
            "name": me.get("name"),
            "episodes_watched": stats.get("num_episodes"),
            # MAL states days; minutes are the same figure in other units.
            "minutes_watched": round(days * 1440) if isinstance(days, (int, float)) else None,
            # 0–100 like AniList's (MAL states 0–10).
            "mean_score": round(mean * 10, 1) if isinstance(mean, (int, float)) else None,
            "count": stats.get("num_items"),
        },
        "score_format": "POINT_10",
        "entries": list(entries.values()),
        "favourites": [],
        "unmatched": unmatched,
    }


# --- one entry --------------------------------------------------------------

EXTRAS_QUERY = """query($id:Int){Media(id:$id,type:ANIME){ id idMal format episodes popularity favourites
 nextAiringEpisode{ airingAt episode timeUntilAiring } studios(isMain:true){ nodes{ name } } }}"""


def entry_state(media_id):
    """The editing panel's state (anilist_entry_state's shape): the list
    entry from MAL, the read-only extras from AniList's public data."""
    from . import anilist
    try:
        m = ((anilist.anilist_request({"query": EXTRAS_QUERY, "variables": {"id": media_id}})
              .get("data") or {}).get("Media"))
    except HTTPError as e:
        if e.code == 404:
            return None
        raise _transient("AniList unreachable (HTTP %d)" % e.code, status=e.code)
    except (URLError, OSError, TimeoutError) as e:
        raise _transient("AniList unreachable: %s" % e)
    if not m:
        return None
    if m.get("idMal") is not None:
        database.cache_put("idmal:%d" % media_id, {"idMal": m.get("idMal")})
    ls = {}
    if m.get("idMal"):
        ls = (api("GET", "/anime/%d" % m["idMal"], {"fields": ENTRY_FIELDS}) or {}).get("my_list_status") or {}
    return _state(m, ls)


def _state(m, ls):
    nxt = m.get("nextAiringEpisode")
    return {
        "media_id": m["id"],
        "mal_id": m.get("idMal"),
        "service": "mal",
        "format": m.get("format"),
        "episodes": m.get("episodes"),
        "popularity": m.get("popularity"),
        "favourites": m.get("favourites"),
        "is_favourite": None,  # MAL's API has no favourites: unknown, never "no"
        "studios": [n.get("name") for n in ((m.get("studios") or {}).get("nodes") or []) if n.get("name")],
        "next_episode": ({"number": nxt["episode"], "in_seconds": nxt["timeUntilAiring"]} if nxt else None),
        "score_format": "POINT_10",
        "score_max": 10,
        "entry": {
            "status": status_in(ls),
            "score": ls.get("score") if ls.get("score") else None,
            "progress": ls.get("num_episodes_watched") if ls else None,
            "repeat": ls.get("num_times_rewatched") or 0,
            "started_at": _date(ls.get("start_date")),
            "completed_at": _date(ls.get("finish_date")),
        },
    }


def save_form(patch):
    """An AniList-shaped patch → MAL's form fields. Raises ValueError for a
    value MAL can't hold."""
    form = {}
    if "status" in patch:
        if patch["status"] not in STATUS_TO_MAL:
            raise ValueError("invalid status")
        form["status"] = STATUS_TO_MAL[patch["status"]]
        form["is_rewatching"] = "true" if patch["status"] == "REPEATING" else "false"
    if "score" in patch:
        score = patch["score"]
        # MAL keeps whole points 0–10 (0 = no score).
        form["score"] = str(max(0, min(10, int(round(float(score or 0))))))
    if "progress" in patch:
        form["num_watched_episodes"] = str(max(0, int(patch["progress"])))
    if "repeat" in patch:
        form["num_times_rewatched"] = str(max(0, int(patch["repeat"])))
    for key, field in (("started_at", "start_date"), ("completed_at", "finish_date")):
        if key in patch:
            form[field] = patch[key] or ""
    return form


def entry_save(media_id, patch):
    """Save the changed fields to MAL (Decision 18.4: changed fields only).
    Returns the fresh state, or {"error": …}. Auth and transient errors
    raise, like anilist_entry_save, so the outbox sees them."""
    from . import anilist
    try:
        form = save_form(patch)
    except (TypeError, ValueError) as e:
        return {"error": str(e)}
    mal = mal_id_of(media_id)
    if not mal:
        raise ValueError("This show isn't on MyAnimeList, so it can't be saved there")
    if form:
        ls = api("PATCH", "/anime/%d/my_list_status" % mal, form=form)
        if not isinstance(ls, dict):
            return {"error": "MyAnimeList did not confirm the change"}
    anilist.forget_entry_state(media_id)
    fresh = anilist.anilist_entry_state(media_id)
    anilist.list_cache_apply(media_id, fresh)
    return fresh
