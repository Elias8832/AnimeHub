"""Show metadata: matching folders to AniList, posters, episode thumbnails."""

import hashlib
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import anilist, database, franchise, library, mapping, pages, settings, upscale, util
from .config import config


EPISODE_META_TTL = 12 * 3600
# Metadata-fetch visibility (Elias, laptop-era finding): the UI must show
# what the background pass is doing — a guessing game between "not found"
# and "still loading" is not acceptable. pending/current/phase feed
# /api/library's meta_pending payload.
_meta_status = {"running": False, "pending": 0, "current": None, "phase": None}


def load_overrides():
    f = config.DATA / "overrides.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    return {}


def fetch_media_full(id=None, search=None):
    key = f"media:v1:{'id:' + str(id) if id is not None else 'search:' + (search or '').lower()}"
    cached = database.cache_get(key, EPISODE_META_TTL)
    if cached is not None:
        return cached.get("media")
    if id is not None:
        payload = {"query": anilist.MEDIA_QUERY_ID, "variables": {"id": id}}
    else:
        payload = {"query": anilist.MEDIA_QUERY_SEARCH, "variables": {"search": search}}
    try:
        data = anilist.anilist_request(payload)
    except HTTPError as e:
        if e.code == 404:  # AniList signals "no match" with 404, not null data
            database.cache_put(key, {"media": None})
            return None
        raise
    media = (data.get("data") or {}).get("Media")
    database.cache_put(key, {"media": media})
    return media


def parse_seps(seps):
    """Parse streamingEpisodes without downloading: thumbs keyed by episode
    number (raw CDN urls) plus cleaned titles, both {str(n): value}."""
    thumbs, titles = {}, {}
    pos = 0
    for e in seps or []:
        pos += 1
        raw = anilist.strip_html(e.get("title") or "")
        m = library.SEP_NUM_RE.match(raw)
        n = int(m.group(1)) if m else pos
        if n >= 1 and str(n) not in titles:
            titles[str(n)] = raw.split(" - ", 1)[-1] or None
        url = e.get("thumbnail")
        if not url or n < 1 or str(n) in thumbs:
            continue
        thumbs[str(n)] = url
    return {"thumbs": thumbs or None, "titles": titles or None}


def commit_thumbs(anilist_id, parsed, prefix=""):
    """Download parsed thumbnail urls into EPIMGS in parallel; returns {int(n): "/epimg/..."}.
    `prefix` keeps a second source's files apart from AniList's."""
    items = list((parsed["thumbs"] or {}).items())
    if not items:
        return {}

    def fetch_one(kv):
        k, url = kv
        # The url is part of the name: a corrected listing must not reuse
        # the picture an earlier, wrong one left under the same number.
        fname = f"{prefix}{anilist_id}.{k}.{hashlib.sha1(url.encode()).hexdigest()[:8]}.jpg"
        dest = config.EPIMGS / fname
        if (dest.exists() and dest.stat().st_size) or download(url, dest):
            return int(k), f"/epimg/{fname}"
        return None

    if len(items) <= 2:
        results = (fetch_one(kv) for kv in items)
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
            results = pool.map(fetch_one, items)
    return dict(r for r in results if r)


def episode_thumbs_meta(anilist_id, seps):
    """Download per-episode thumbnails from AniList streamingEpisodes.
    Returns {"thumbs": {str(n): "/epimg/..."}, "titles": {str(n): ...}} (None values when empty)."""
    parsed = parse_seps(seps)
    if not parsed["thumbs"]:
        return {"thumbs": None, "titles": parsed["titles"]}
    got = commit_thumbs(anilist_id, parsed)
    thumbs = {str(n): u for n, u in got.items()}
    titles = parsed["titles"]
    return {"thumbs": thumbs or None, "titles": titles}


def media_episode_art(media):
    """Use only this entry's episode listing; mismatched franchise feeds use banners."""
    seps = media.get("streamingEpisodes") or []
    count = media.get("episodes")
    if count and len(seps) > count:
        return {"thumbs": None, "titles": None}
    return episode_thumbs_meta(media["id"], seps)

def fetch_media_candidates(title, fresh=False):
    """A bounded title search over the AniList catalog, cached. fresh=True
    skips the cache (the To match tab's per-show refresh, TODO 12b)."""
    query = re.sub(r"\s*\([^)]*\)", "", title).strip()
    query = re.sub(r"(\d+)(?:st|nd|rd|th)\s+Season", r"Season \1", query, flags=re.I)
    if not query:
        return []
    key = "media-candidates:v1:" + query.lower()
    cached = None if fresh else database.cache_get(key, EPISODE_META_TTL)
    if cached is not None:
        return cached
    data = anilist.anilist_request({"query": anilist.MEDIA_QUERY_CANDIDATES, "variables": {"search": query}})
    candidates = ((data.get("data") or {}).get("Page") or {}).get("media") or []
    database.cache_put(key, candidates)
    return candidates


def polluted_playlist(ref_thumbs, cand_thumbs):
    """True when a candidate streaming playlist carries the reference entry's
    (franchise S1) thumbnails — AniList's Attack-on-Titan-style data bug."""
    if not ref_thumbs or not cand_thumbs:
        return False
    shared = [k for k in ref_thumbs if k in cand_thumbs and k in ("1", "2", "3")]
    if len(shared) < 2:
        return False
    return all(ref_thumbs[k] == cand_thumbs[k] for k in shared)


def sequel_id(media_id):
    m = fetch_media_full(id=media_id)
    if not m:
        return None
    for e in m.get("relations", {}).get("edges", []):
        if e.get("relationType") == "SEQUEL":
            return e["node"]["id"]
    return None


def download(url, dest):
    if not url:
        return False
    dest = Path(dest)
    temporary = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"})
        with urlopen(req, timeout=30) as r:
            body = r.read()
        if not body:
            return False
        # Only accept real images (jpg/png/gif/webp) — a poisoned or spoofed
        # source must never be able to drop arbitrary bytes into POSTERS.
        if body[:3] != b"\xff\xd8\xff" and body[:4] not in (b"\x89PNG", b"GIF8", b"RIFF"):
            return False
        temporary.write_bytes(body)
        os.replace(temporary, dest)
        return True
    except Exception:
        return False
    finally:
        temporary.unlink(missing_ok=True)


def search_candidates(show, season):
    title = show["title"]
    if show["kind"] == "movie":
        cands = []
        if show.get("collection"):
            cands.append(f"{show['collection']} {title}")
        cands.append(title)
        return cands
    cands = []
    if show.get("collection"):
        cands.append(f"{show['collection']} {title}")
    cands.append(title)
    if season and season > 1:
        cands.append(f"{title} season {season}")
    return cands


def norm_match_title(s):
    """Title normalization for match comparisons: lowercase, parenthetical
    qualifiers dropped, punctuation to spaces."""
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def pick_candidate(candidates, show, installed):
    """Score AniList candidates for a local show; the best confident one
    wins, None = unmatched (TODO 28, Decision 18.4: no automatic write on
    an unreliable match — the old code accepted Media(search:)'s first hit,
    which is how Frieren S1 / HxH 1999 got matched wrong and compilation
    movies grabbed whatever shared their title).

    Hard gates (disqualify, never merely demote):
      format — a movie folder matches a MOVIE release, a show folder a
        non-MOVIE release; compilations can't steal show matches.
      count  — single-season shows only: a release with fewer episodes
        than installed files cannot be the match (HxH 1999's 92 eps
        against a 148-file library). Multi-season containers hold several
        releases' worth of files, so the gate would reject every real
        candidate — it stays off for them.
    Score: exact normalized title (english/romaji, with collection
    prefixed) > substring identity; exact episode count breaks ties.
    Ambiguous leftovers (partial installs with two same-titled releases)
    stay unmatched on purpose — the manual override resolves them."""
    want_movie = show["kind"] == "movie"
    single_season = len(show["seasons"]) == 1
    show_n = norm_match_title(show["title"])
    coll = show.get("collection")
    combined_n = norm_match_title(f"{coll} {show['title']}") if coll else show_n
    best, best_score = None, 0
    for m in candidates or []:
        fmt = m.get("format")
        if want_movie != (fmt == "MOVIE"):
            continue
        eps = m.get("episodes")
        if single_season and eps and installed and eps < installed:
            continue
        titles = {(m.get("title") or {}).get("english"),
                  (m.get("title") or {}).get("romaji")}
        normed = {norm_match_title(t) for t in titles if t}
        if show_n in normed or combined_n in normed:
            score = 100
        elif any(show_n in t or t in show_n for t in normed if t):
            score = 50
        else:
            continue
        if eps and installed and eps == installed:
            score += 5
        # Ties resolve on the candidate's own identity (lower AniList id),
        # never on search-result order — the same pool must always pick
        # the same release no matter which query surfaced it first.
        if score > best_score or (score == best_score and best is not None
                                  and m["id"] < best["id"]):
            best, best_score = m, score
    return best


def resolve_match(show, override=None):
    """The media anilist_id for a local show, validated; None = unmatched.
    Explicit overrides (anilistId/query) bypass scoring — they are the
    user's manual answer. The automatic path gathers candidates from every
    query variant and picks by confidence, never by search order."""
    seasons = show.get("seasons") or []
    season = next((x.get("season") for x in seasons), 1)
    if override and override.get("anilistId"):
        return fetch_media_full(id=override["anilistId"])
    if override and override.get("query"):
        return fetch_media_full(search=override["query"])
    installed = sum(len(x.get("episodes") or []) for x in seasons)
    seen, pool = set(), []
    for cand in search_candidates(show, season):
        # One query failing (network hiccup, rate limit) must not abort the
        # whole pool — the remaining variants still get their say.
        try:
            found = fetch_media_candidates(cand)
        except Exception:
            continue
        for m in found:
            if m["id"] not in seen:
                seen.add(m["id"])
                pool.append(m)
    m = pick_candidate(pool, show, installed)
    if m and show["kind"] == "show":
        hops = max(0, (season or 1) - 1)
        for _ in range(hops):
            nxt = sequel_id(m["id"])
            if not nxt:
                break
            m = fetch_media_full(id=nxt)
            time.sleep(2)
    return m


def store_show_meta(show, m):
    """Write a resolved match: artwork downloads + shows_meta upsert."""
    year = (m.get("startDate") or {}).get("year")
    poster_name = f"{show['id']}.jpg"
    banner_name = f"{show['id']}.banner.jpg"
    poster = f"/img/{poster_name}" if download((m.get("coverImage") or {}).get("extraLarge"), config.POSTERS / poster_name) else None
    banner = f"/img/{banner_name}" if download(m.get("bannerImage"), config.POSTERS / banner_name) else None
    if banner and config.UPSCALE_BANNERS:
        # In the background: keep the metadata queue moving
        # instead of blocking ~19s per banner on the Upscayl run.
        # Opt-in (config 'upscale_banners', default off): on machines without
        # working GPU Vulkan the upscaler emits all-black images — found
        # destroying real banners 2026-10-15 — and AniList's own banners are
        # already high-resolution.
        upscale.upscale_banner_async(config.POSTERS / banner_name)
    seps = m.get("streamingEpisodes") or []
    eps_meta = [anilist.strip_html(e["title"]).split(" - ", 1)[-1] for e in seps] if seps else None
    thumbs = media_episode_art(m)["thumbs"]
    title = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
    with database.db() as c:
        c.execute(
            """INSERT INTO shows_meta(show_id,anilist_id,title,year,score,overview,poster,banner,episodes_meta,format,episode_thumbs,fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(show_id) DO UPDATE SET anilist_id=excluded.anilist_id,title=excluded.title,year=excluded.year,
               score=excluded.score,overview=excluded.overview,poster=excluded.poster,banner=excluded.banner,
               episodes_meta=excluded.episodes_meta,format=excluded.format,episode_thumbs=excluded.episode_thumbs,fetched_at=excluded.fetched_at""",
            (show["id"], m["id"], title, year, m.get("averageScore"), anilist.strip_html(m.get("description")),
             poster, banner, json.dumps(eps_meta) if eps_meta else None, m.get("format"),
             json.dumps(thumbs) if thumbs else None),
        )
    return True


def clear_match(show_id):
    """Remove a (wrong) match: the shows_meta row goes, the optional title
    override goes with it. Fetched metadata only — watch facts are in
    `episodes` and untouched (Decision 17.2). The next pass re-attempts."""
    with database.db() as c:
        c.execute("DELETE FROM shows_meta WHERE show_id=?", (show_id,))
        # Old per-season art (season_thumbs, no longer written) goes with it.
        # Season pins stay: they name entries, not the folder's match.
        c.execute("DELETE FROM season_thumbs WHERE show_id=?", (show_id,))
    overrides = load_overrides()
    changed = False
    for show in library.scan_library():
        if show["id"] != show_id:
            continue
        for key in (show["title"], show.get("path")):
            if key and overrides.pop(key, None) is not None:
                changed = True
    if changed:
        (config.DATA / "overrides.json").write_text(json.dumps(overrides, indent=1))


def kept_local():
    """Show ids the user marked "Not on AniList — keep local" (TODO 12c):
    they leave the To match tab and the automatic matcher stops asking
    AniList about them. Matching one by hand ends it."""
    try:
        return set(json.loads(settings.get_setting("kept_local") or "[]"))
    except ValueError:
        return set()


def set_kept_local(show_id, keep=True):
    if not any(s["id"] == show_id for s in library.scan_library()):
        return {"error": "unknown show"}
    ids = kept_local()
    if keep:
        ids.add(show_id)
    else:
        ids.discard(show_id)
    settings.set_setting("kept_local", json.dumps(sorted(ids)))
    return {"ok": True, "kept_local": keep}


def set_match(show_id, anilist_id=None, query=None):
    """Manual match repair (TODO 28 minimal): commit a user-chosen AniList
    id (or raw search query) for a show and refetch its metadata. The
    manual answer bypasses confidence scoring (it IS the confidence), so
    the surface stays usable even where the automatic matcher stays silent.
    anilist_id=None clears the match."""
    show = next((s for s in library.scan_library() if s["id"] == show_id), None)
    if not show:
        return {"error": "unknown show"}
    if anilist_id is None:
        clear_match(show_id)
        return {"ok": True, "cleared": True}
    m = fetch_media_full(id=anilist_id)
    if not m:
        return {"error": f"AniList has no media with id {anilist_id}"}
    overrides = load_overrides()
    overrides[show["title"]] = {"anilistId": anilist_id}
    (config.DATA / "overrides.json").write_text(json.dumps(overrides, indent=1))
    with database.db() as c:
        c.execute("DELETE FROM season_thumbs WHERE show_id=?", (show_id,))
    store_show_meta(show, m)
    if show_id in kept_local():
        set_kept_local(show_id, False)
    return {"ok": True, "anilist_id": anilist_id,
            "title": (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")}


def fetch_show_meta(show, override=None):
    with database.db() as c:
        row = c.execute("SELECT poster FROM shows_meta WHERE show_id=?", (show["id"],)).fetchone()
    if row and row[0] and not override:
        return True
    m = resolve_match(show, override)
    if not m:
        return False
    return store_show_meta(show, m)


def refresh_meta():
    if _meta_status["running"]:
        return
    _meta_status["running"] = True
    try:
        scan = library.scan_library()
        library.merge_db(scan)
        overrides = load_overrides()
        kept = kept_local()
        missing = [s for s in scan if not s.get("poster") and s["id"] not in kept]
        _meta_status["pending"] = len(missing)
        _meta_status["phase"] = "show metadata"
        for s in missing:
            try:
                _meta_status["current"] = s["title"]
                fetch_show_meta(s, overrides.get(s["title"]) or overrides.get(s["path"]))
            except Exception:
                pass
            _meta_status["pending"] -= 1
            time.sleep(2.2)
        library.merge_db(scan)
        _meta_status["phase"] = "franchises"
        _meta_status["current"] = None
        try:
            franchise.refresh_franchises(franchise.franchise_seeds(scan))
            pages.drop_premature_links()
        except Exception as e:
            util.log("franchise refresh failed: %s" % e)
        # Missing episode art is a normal poster fallback, retried later.
        _meta_status["phase"] = "episode art"
        try:
            library.merge_db(scan)  # seasons map onto the franchises just built
            mapping.refresh_entry_art(scan)
        except Exception as e:
            util.log("episode art failed: %s" % e)
    finally:
        _meta_status["running"] = False
        _meta_status["pending"] = 0
        _meta_status["current"] = None
        _meta_status["phase"] = None
