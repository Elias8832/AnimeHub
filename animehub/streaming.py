"""Online episodes: matching releases to ani-cli and streaming them."""

import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import unicodedata
from urllib.error import HTTPError

from . import anicli, anilist, database, library, mapping, metadata, player, system, upscale, util
from .config import config, SKIP_CREDITS_LUA


STREAM_COLS = "media_id, query, title, idx, mode, episodes, episodes_ts, meta, updated_at, matched_by"
# A release ani-cli has no confident match for is not searched again on
# every visit; a manual pick (or this long) lifts it.
STREAM_MISS_TTL = 24 * 3600
STREAM_UNAVAILABLE = "This show isn't available to watch online."


def _stream_link(row):
    return {"media_id": row[0], "query": row[1], "title": row[2], "idx": row[3] or 1,
            "mode": row[4] if row[4] in anicli.STREAM_MODES else "sub",
            "episodes": json.loads(row[5]) if row[5] else [], "episodes_ts": row[6] or 0,
            "meta": json.loads(row[7]) if row[7] else {}, "updated_at": row[8],
            "matched_by": row[9] or "auto"}


def stream_link_get(media_id):
    with database.db() as c:
        row = c.execute(f"SELECT {STREAM_COLS} FROM stream_links WHERE media_id=?", (media_id,)).fetchone()
    return _stream_link(row) if row else None


def stream_links_all():
    with database.db() as c:
        return [_stream_link(r) for r in c.execute(f"SELECT {STREAM_COLS} FROM stream_links")]


def stream_missed_ids():
    with database.db() as c:
        return {r[0] for r in c.execute("SELECT media_id FROM stream_misses WHERE ts > ?",
                                        (time.time() - STREAM_MISS_TTL,))}


def _stream_miss(media_id, missed):
    with database.db() as c:
        if missed:
            c.execute("INSERT INTO stream_misses(media_id, ts) VALUES(?,?) ON CONFLICT(media_id)"
                      " DO UPDATE SET ts=excluded.ts", (media_id, time.time()))
        else:
            c.execute("DELETE FROM stream_misses WHERE media_id=?", (media_id,))


RELEASE_QUERY = """query($id:Int){Media(id:$id,type:ANIME){id title{romaji english native} synonyms
format status episodes averageScore seasonYear startDate{year} nextAiringEpisode{episode}
coverImage{extraLarge color} bannerImage description streamingEpisodes{title thumbnail}}}"""


def release_meta(media_id):
    """One AniList release with what matching needs (all titles, synonyms,
    aired count). Cached briefly: airing counts move weekly."""
    key = f"release:v1:{media_id}"
    cached = database.cache_get(key, 3 * 3600)
    if cached is not None:
        return cached.get("media")
    try:
        data = anilist.anilist_request({"query": RELEASE_QUERY, "variables": {"id": media_id}})
    except HTTPError as e:
        if e.code == 404:
            database.cache_put(key, {"media": None})
            return None
        raise
    media = (data.get("data") or {}).get("Media")
    database.cache_put(key, {"media": media})
    return media


def release_aired(m):
    """How many episodes of a release are out, as far as AniList knows."""
    nxt = (m.get("nextAiringEpisode") or {}).get("episode")
    if m.get("status") == "RELEASING" and nxt:
        return nxt - 1
    if m.get("status") == "NOT_YET_RELEASED":
        return 0
    return m.get("episodes") or 0


def _release_display(m, fallback_title, art):
    """Display metadata for a release: artwork cached locally once it is a
    linked show (art=True), AniList's own URLs for a page just browsed."""
    t = m.get("title") or {}
    cover = (m.get("coverImage") or {}).get("extraLarge")
    if art:
        name = f"stream-{m['id']}"
        poster = f"/img/{name}.jpg" if metadata.download(cover, config.POSTERS / f"{name}.jpg") else cover
        # A banner saved in the last 30 days stays: links are stored again
        # often, and each fresh copy undid its upscale (Settings → Sharper
        # banners) and had it redone.
        bpath = config.POSTERS / f"{name}.banner.jpg"
        if m.get("bannerImage") and upscale.banner_age(bpath) < 30 * 86400:
            banner = f"/img/{name}.banner.jpg"
        else:
            banner = (f"/img/{name}.banner.jpg" if metadata.download(m.get("bannerImage"), bpath)
                      else m.get("bannerImage"))
        if banner and banner.startswith("/img/"):
            upscale.queue_banner(bpath)
        eps_art = metadata.media_episode_art(m)
    else:
        poster, banner = cover, m.get("bannerImage")
        seps = m.get("streamingEpisodes") or []
        eps_art = (metadata.parse_seps(seps) if not (m.get("episodes") and len(seps) > m["episodes"])
                   else {"thumbs": None, "titles": None})
    return {"title": t.get("english") or t.get("romaji") or fallback_title,
            "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear"),
            "score": m.get("averageScore"), "overview": anilist.strip_html(m.get("description")),
            "format": m.get("format"), "episodes": m.get("episodes"),
            "poster": poster, "banner": banner,
            "thumbs": eps_art.get("thumbs"), "titles": eps_art.get("titles")}


def stream_meta(media_id, fallback_title):
    """Display metadata for a linked release. AniList being unreachable
    must not block a link."""
    try:
        m = release_meta(media_id)
    except Exception as e:
        util.log("release meta for %s failed: %s" % (media_id, e))
        m = None
    return _release_display(m, fallback_title, art=True) if m else {"title": fallback_title}


# ---- matching an AniList release to ani-cli's search results ----
_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
# Words that mark a different kind of release than the TV series.
_EXTRA_WORDS = {"movie", "film", "ova", "oad", "special", "specials", "recap", "mini",
                "chibi", "petit", "pv", "picture", "drama"}
_FORMAT_EXTRAS = {"MOVIE": {"movie", "film"}, "OVA": {"ova", "oad"}, "SPECIAL": {"special", "specials", "recap"}}
_SIG_DROP = {"the", "season", "part", "cour", "st", "nd", "rd", "th", "tv"}


def _title_words(title):
    t = unicodedata.normalize("NFKD", title or "").replace("×", "x").lower()
    return re.findall(r"[a-z0-9]+", t)


def _title_sig(title, explicit_only=False):
    """(season, part, extra words, base words) of a title. explicit_only
    reads only spelled-out seasons ("Season 2", "2nd Season"): synonyms like
    "SnK 4" number seasons in ways the release's own titles do not."""
    words = _title_words(title)
    text = " ".join(words)
    season = part = None
    m = re.search(r"\b(?:season|s)\s*(\d{1,2})\b", text) or re.search(r"\b(\d{1,2})(?:st|nd|rd|th) season\b", text)
    if m:
        season = int(m.group(1))
    m = re.search(r"\b(?:part|cour)\s*(\d{1,2})\b", text)
    if m:
        part = int(m.group(1))
    if season is None and not explicit_only and len(words) > 1:
        if words[-1] in _ROMAN:
            season = _ROMAN[words[-1]]
        elif re.fullmatch(r"[2-9]", words[-1]) and words[-2] not in ("part", "cour"):
            season = int(words[-1])
    extras = {w for w in words if w in _EXTRA_WORDS}
    marks = {str(season), str(part)} | ({k for k, v in _ROMAN.items() if v == season})
    base = [w for w in words if w not in _SIG_DROP and w not in extras and w not in marks]
    return season, part, extras, base


def _title_similarity(a, b):
    if "".join(a) == "".join(b):  # "Dan Da Dan" = "Dandadan"
        return 1.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def release_titles(m):
    t = m.get("title") or {}
    titles = [t.get("english"), t.get("romaji")] + list(m.get("synonyms") or [])
    out = []
    for x in titles:
        if x and x not in out and sum(ord(ch) < 128 for ch in x) >= 0.8 * len(x):
            out.append(x)
    return out


def anicli_query(title):
    """A search query the site accepts: ASCII words only — "×" fails with
    HTTP 400 and "[" sets off curl's URL globbing."""
    t = unicodedata.normalize("NFKD", title).replace("×", "x")
    t = t.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    t = "".join(ch if ch.isascii() else " " for ch in t)
    t = re.sub(r"[\[\]{}()<>\"?*#%^|\\~`]", " ", t)
    return " ".join(t.split()).strip(" -:")


def _match_score(cand, m):
    """How well an ani-cli result title fits the release: the best title
    similarity over the release's titles (English, romaji, synonyms) whose
    season/part agree with the candidate's; 0 when none agrees or the
    candidate is a different kind of release (movie, OVA…)."""
    c_season, c_part, c_extras, c_base = _title_sig(cand)
    allowed = _FORMAT_EXTRAS.get(m.get("format"), set())
    best = 0.0
    for t in release_titles(m):
        t_season, t_part, t_extras, t_base = _title_sig(t)
        if (c_season or 1) != (t_season or 1) or (c_part or 1) != (t_part or 1):
            continue
        if c_extras - t_extras - allowed:
            continue
        best = max(best, _title_similarity(c_base, t_base))
    return best


def _count_fits(episodes, m):
    """ani-cli's episode list against AniList's count: a merged-seasons
    listing or a different show of the same name fails here."""
    n = sum(1 for e in episodes if re.fullmatch(r"\d+", e) and int(e) >= 1)
    if m.get("status") == "NOT_YET_RELEASED":
        # Nothing has aired: any listed episodes belong to another show
        # ("Chainsaw Man" for the announced "Chainsaw Man: Shikaku-hen").
        return False
    nxt = (m.get("nextAiringEpisode") or {}).get("episode")
    if m.get("status") == "RELEASING" and nxt:
        aired = nxt - 1
        return aired - max(4, aired * 0.1) <= n <= aired + 2
    if m.get("episodes"):
        total = m["episodes"]
        return abs(n - total) <= max(1, round(total * 0.1))
    return n > 0


def stream_match(m):
    """Find the release among ani-cli's search results: search its titles,
    rank results by title fit (season, part and kind must agree with one of
    the release's titles), and accept the best one whose episode list fits
    AniList's count. None when nothing fits — a wrong match plays the wrong
    episodes, so no guessing. Returns (query, title, index, episodes)."""
    titles = release_titles(m)
    t = m.get("title") or {}
    main = [x for x in (t.get("english"), t.get("romaji")) if x]
    # Search the main titles and the longer synonyms ("SnK 4" finds noise).
    queries = [x for x in titles if x in main or len(re.sub(r"[^A-Za-z]", "", x)) >= 8][:5]
    # The season a release's titles spell out: tie-breaks equal fits —
    # "Kaguya-sama: Love is War?" (S2) reads exactly like season 1's title,
    # its synonym "… Season 2" does not.
    spelled = [x for x in (_title_sig(x, explicit_only=True)[0] for x in titles) if x]
    hint = max(spelled) if spelled else next((x for x in (_title_sig(x)[0] for x in main) if x), 1)
    cands, tried, failures = {}, set(), []

    def search(q):
        if not q or q.lower() in tried:
            return None
        tried.add(q.lower())
        try:
            return anicli.anicli_search(q)["results"]
        except anicli.AniCliError as e:
            failures.append(str(e))
            return None

    def add(q, results):
        for r in results or []:
            if r["title"] is None:
                continue
            score = _match_score(r["title"], m)
            if score < 0.5:
                continue
            bonus = 1 if (_title_sig(r["title"])[0] or 1) == hint else 0
            if r["title"] not in cands or cands[r["title"]][:2] < (bonus, score):
                cands[r["title"]] = (bonus, score, q, r["title"], r["index"])

    for title in queries:
        q = anicli_query(title)
        results = search(q)
        if results and results[0]["title"] is None:
            # One hit: ani-cli takes it without listing it, so its title is
            # unknown. Shorter queries usually list it by name; the lone hit
            # itself stays a fallback, kept only if its episode count fits.
            cands.setdefault(f"?{q}", (0, 0.9, q, None, 1))
            words = q.split()
            for cut in range(1, min(3, len(words) - 1) + 1):
                listed = search(" ".join(words[:-cut]))
                if listed and listed[0]["title"] is not None:
                    add(" ".join(words[:-cut]), listed)
                    break
        else:
            add(q, results)
        if any(c[1] == 1.0 for c in cands.values()):
            break  # an exact title: no need to search the other names
    # The top candidates' episode lists are fetched side by side (TODO 61:
    # one after another they added ~0.3 s each); the best-ranked one that
    # fits wins, exactly as before.
    top = sorted(cands.values(), key=lambda c: (-c[0], -c[1]))[:4]

    def episodes_of(c):
        try:
            return anicli.anicli_episodes({"query": c[2], "title": c[3], "idx": c[4]})
        except anicli.AniCliError as e:
            failures.append(str(e))
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        lists = list(pool.map(episodes_of, top)) if len(top) > 1 else [episodes_of(c) for c in top]
    for (_, _, query, title, idx), episodes in zip(top, lists):
        if episodes is not None and _count_fits(episodes, m):
            return query, title, idx, episodes
    if not cands and failures and len(failures) == len(tried):
        # Every search failed (offline, site down): that is no verdict.
        raise anicli.AniCliError(failures[-1])
    return None


def friendly_stream_error(msg, code="this episode"):
    """ani-cli's (and the site's) failures in the app's words."""
    m = (msg or "").lower()
    if "update ani-cli" in m:
        return msg  # anicli_check: the user has to act, keep the details
    if "not released" in m:
        return f"{code} isn't out yet."
    if "cloudflare" in m:
        return "The streaming site is blocking requests right now — try again later."
    if any(k in m for k in ("connection error", "request failed", "did not answer", "could not resolve")):
        return "Couldn't reach the streaming site — check your connection."
    if "no sources found" in m or "no valid sources" in m:
        return f"{code} can't be loaded right now — try again in a bit."
    if "invalid anime selection" in m or m == STREAM_UNAVAILABLE.lower():
        return STREAM_UNAVAILABLE
    if "not found" in m and "ani-cli" in m:
        return "Watching online needs ani-cli — install it and make sure it is on PATH."
    return f"Couldn't play {code}."


_link_locks = {}
_link_locks_guard = threading.Lock()
_resolving = set()


def _media_lock(media_id):
    with _link_locks_guard:
        return _link_locks.setdefault(media_id, threading.Lock())


def _store_link(media_id, query, title, idx, mode, episodes, matched_by, display):
    meta = stream_meta(media_id, display or title or query)
    with database.db() as c:
        c.execute("""INSERT INTO stream_links(media_id,query,title,idx,mode,episodes,episodes_ts,meta,updated_at,matched_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(media_id) DO UPDATE SET query=excluded.query, title=excluded.title,
                     idx=excluded.idx, mode=excluded.mode, episodes=excluded.episodes,
                     episodes_ts=excluded.episodes_ts, meta=excluded.meta, updated_at=excluded.updated_at,
                     matched_by=excluded.matched_by""",
                  (media_id, query, title, idx or 1, mode, json.dumps(episodes), time.time(),
                   json.dumps(meta), util.utc_stamp(), matched_by))
    _stream_miss(media_id, False)


def stream_autolink(media_id, rematch=False):
    """The ani-cli link for a release, made on first need — nobody picks a
    search result by hand. rematch=True replaces a link whose result ani-cli
    no longer lists. Raises AniCliError with a message for the UI."""
    with _media_lock(media_id):
        link = stream_link_get(media_id)
        if link and not rematch:
            return link
        if not rematch and media_id in stream_missed_ids():
            raise anicli.AniCliError(STREAM_UNAVAILABLE)
        if not anicli.anicli_path():
            raise anicli.AniCliError(friendly_stream_error("ani-cli not found"))
        _resolving.add(media_id)
        try:
            try:
                m = release_meta(media_id)
            except Exception:
                raise anicli.AniCliError("Couldn't reach AniList — check your connection.")
            if not m:
                raise anicli.AniCliError(STREAM_UNAVAILABLE)
            if m.get("status") == "NOT_YET_RELEASED":
                raise anicli.AniCliError("This isn't out yet.")  # no miss: it will be
            try:
                found = stream_match(m)
            except anicli.AniCliError as e:
                raise anicli.AniCliError(friendly_stream_error(str(e)))
            if not found:
                _stream_miss(media_id, True)
                util.log("no ani-cli match for AniList %s (%s)" % (media_id, release_titles(m)[:2]))
                raise anicli.AniCliError(STREAM_UNAVAILABLE)
            query, title, idx, episodes = found
            mode = link["mode"] if link else anicli.stream_prefs()["mode"]
            _store_link(media_id, query, title, idx, mode, episodes, "auto", None)
            return stream_link_get(media_id)
        finally:
            _resolving.discard(media_id)


def stream_resolve_async(media_id):
    """Start linking a release in the background (a page was opened), so
    its real episode list is there before anyone clicks."""
    if (media_id in _resolving or stream_link_get(media_id) or not anicli.anicli_path()
            or media_id in stream_missed_ids()):
        return
    _resolving.add(media_id)

    def run():
        try:
            stream_autolink(media_id)
        except anicli.AniCliError:
            pass
        except Exception as e:
            util.log("linking %s failed: %s" % (media_id, e))
        finally:
            _resolving.discard(media_id)

    threading.Thread(target=run, daemon=True).start()


def stream_link_set(media_id, query, title, idx=1, mode=None, display=None):
    """A user-picked result ("Wrong episodes?"): wins over the automatic
    match. The episode probe runs first, so only a working pick is kept."""
    link = stream_link_get(media_id)
    mode = mode if mode in anicli.STREAM_MODES else (link["mode"] if link else anicli.stream_prefs()["mode"])
    episodes = anicli.anicli_episodes({"query": query, "title": title or None, "idx": idx or 1, "mode": mode})
    _store_link(media_id, query, title or None, idx, mode, episodes, "user", display)
    return {"ok": True, "show_id": f"aid:{media_id}", "episodes": len(episodes)}


def stream_refresh(media_id):
    """Ask ani-cli for the episode list again (new episodes of airing shows)."""
    link = stream_link_get(media_id)
    if not link:
        return {"error": STREAM_UNAVAILABLE}
    episodes = anicli.anicli_episodes(link)
    meta = link["meta"]
    if not meta.get("poster"):
        meta = stream_meta(media_id, meta.get("title") or link["title"] or link["query"])
    with database.db() as c:
        c.execute("UPDATE stream_links SET episodes=?, episodes_ts=?, meta=? WHERE media_id=?",
                  (json.dumps(episodes), time.time(), json.dumps(meta), media_id))
    return {"ok": True, "episodes": len(episodes), "added": len(set(episodes) - set(link["episodes"]))}


def stream_set_mode(media_id, mode):
    with database.db() as c:
        n = c.execute("UPDATE stream_links SET mode=?, updated_at=? WHERE media_id=?",
                      (mode, util.utc_stamp(), media_id)).rowcount
    return {"ok": True, "mode": mode} if n else {"error": STREAM_UNAVAILABLE}


def stream_switch_mode(media_id, mode):
    """The show page's Sub/Dub toggle: every online episode follows, so the
    episode list is re-read in the new language. A language ani-cli has no
    episodes in keeps the old one."""
    link = stream_link_get(media_id)
    if not link:
        return {"error": STREAM_UNAVAILABLE}
    if link["mode"] == mode:
        return {"ok": True, "mode": mode}
    old = link["mode"]
    stream_set_mode(media_id, mode)
    try:
        r = stream_refresh(media_id)
    except anicli.AniCliError:
        r = None
    if not r or r.get("error") or not r.get("episodes"):
        stream_set_mode(media_id, old)
        return {"error": "No %s version found online." % ("dubbed" if mode == "dub" else "subbed")}
    return {"ok": True, "mode": mode, "episodes": r["episodes"]}


def _stream_num(token):
    f = float(token)
    return int(f) if f.is_integer() else f


def disk_ep(ep):
    return not ep["id"].startswith("stream:")


def entry_files(scan, media_id):
    """{the entry's own episode number: episode} of every file that maps to
    it (release_units), and the folder downloads for it go to: the folder
    season holding its files under its own numbers (files are saved as
    "… Episode N"), or None."""
    eps, homes = {}, {}
    for s in scan:
        for u in mapping.release_units(s):
            if u["media_id"] != media_id:
                continue
            for ep, n in u["eps"]:
                if disk_ep(ep):
                    eps.setdefault(n, ep)
            for i in u["seasons"]:
                sea = s["seasons"][i]
                if any(r["id"] == media_id and r["offset"] == 0 and r["last_ep"] is None for r in sea["releases"]):
                    folder = (config.LIBRARY / sea["episodes"][0]["rel"]).parent
                    homes[folder] = homes.get(folder, 0) + len(sea["episodes"])
    return eps, max(homes, key=homes.get) if homes else None


def _stream_show(link, rows, extra=None):
    """A release's online episodes in the scanned-show shape, so history,
    continue watching, playback and progress sync read them like any other
    episodes. Files of the same release are their own episodes; the
    franchise page shows the two as one."""
    mid, meta = link["media_id"], link["meta"]
    eps = []
    for token in link["episodes"]:
        sid = f"stream:{mid}:{token}"
        num = _stream_num(token)
        r = rows.get(sid)
        eps.append({"id": sid, "stream_id": sid, "token": token, "number": num, "rel": None,
                    "files": [], "season": 1, "season_label": "Episodes", "installed": False,
                    "watched": bool(r[0]) if r else False, "position": r[1] if r else 0,
                    "duration": r[2] if r else 0, "last_played": r[3] if r else None})
    title = meta.get("title") or link["title"] or link["query"]
    return {"id": f"aid:{mid}", "kind": "stream", "path": None, "title": title, "collection": None,
            "poster": library.img_url(meta.get("poster")), "banner": library.img_url(meta.get("banner")),
            "movie": meta.get("format") == "MOVIE" and len(eps) <= 1,
            "meta": {"anilist_id": mid, "title": title, "year": meta.get("year"),
                     "score": meta.get("score"), "overview": meta.get("overview"),
                     "format": meta.get("format"), "episodes_meta": None, "episode_thumbs": None},
            # One season that is the release itself, so progress sync and
            # completion (release_units) count its episodes for it.
            "seasons": [{"key": 1, "season": 1, "label": "Episodes", "episodes": eps,
                         "thumbs": meta.get("thumbs"), "titles": meta.get("titles"),
                         "releases": [{"id": mid, "first_ep": 1, "last_ep": None, "offset": 0,
                                       "total": meta.get("episodes")}]}],
            "stream": {"mode": link["mode"], "total": meta.get("episodes"),
                       "matched_by": link.get("matched_by"),
                       "stale": time.time() - link["episodes_ts"] > anicli.STREAM_EPISODES_TTL, **(extra or {})}}


def _stream_rows():
    with database.db() as c:
        return {r[0]: r[1:] for r in c.execute(
            "SELECT id, watched, position, duration, last_played FROM episodes WHERE id LIKE 'stream:%'")}


def stream_shows(scan, links=None):
    """Every linked release as a show (scan: disk-only, with merge_db's facts)."""
    links = stream_links_all() if links is None else links
    if not links:
        return []
    rows = _stream_rows()
    return [_stream_show(link, rows) for link in links]


def library_shows():
    """(disk shows, online shows) — the one view of "a show" every episode
    lookup goes through."""
    scan = library.scan_library()
    library.merge_db(scan)
    return scan, stream_shows(scan)


def stream_media_id(ref):
    """The AniList id inside "stream:<id>:<ep>" / "aid:<id>", else None."""
    try:
        return int(ref.split(":")[1])
    except (IndexError, ValueError):
        return None


def release_show(media_id):
    """The page for a release nobody linked yet: AniList's episodes as
    online episodes, while the link is made in the background. Their ids
    are already the final "stream:" ids, so a click plays them directly."""
    m = release_meta(media_id)
    if not m:
        return None
    disp = _release_display(m, None, art=False)
    link = {"media_id": media_id, "query": None, "title": None, "mode": anicli.stream_prefs()["mode"],
            "episodes": [str(n) for n in range(1, release_aired(m) + 1)], "episodes_ts": time.time(),
            "meta": disp}
    unavailable = not anicli.anicli_path() or media_id in stream_missed_ids()
    return _stream_show(link, _stream_rows(),
                        {"provisional": True, "unavailable": unavailable, "ready": bool(anicli.anicli_path())})


def trackable(s):
    """A show as progress sync and the completion check read it: whole
    episode numbers from 1 (a streamed episode 0 or 12.5 recap is no
    AniList progress)."""
    return dict(s, seasons=[dict(sea, episodes=[e for e in sea["episodes"]
                                                if isinstance(e["number"], int) and e["number"] >= 1])
                            for sea in s["seasons"]])


def session_label(sea, ep):
    """The now-playing label: S1 · E3, SP · E2, or E3 for a release page."""
    label = sea.get("label") or ""
    if label == "Episodes":
        return f"E{ep['number']}"
    if label.startswith("Season"):
        short = f"S{sea['season']}"
    elif label == "Specials":
        short = "SP"
    else:
        short = label
    return f"{short} · E{ep['number']}"


def mpv_script_flag():
    """--script for the skip-credits hotkey, usable inside
    ANI_CLI_PLAYER_FLAGS: ani-cli splits that on whitespace, so a path with
    spaces goes through a symlink in the private runtime dir — the short
    8.3 name on Windows — or is left out (system.space_free_path)."""
    if not SKIP_CREDITS_LUA.exists():
        return None
    path = system.space_free_path(SKIP_CREDITS_LUA)
    return f"--script={path}" if path else None


_session_notice = {"msg": None, "ts": 0.0}


def start_stream(ep_id, mode=None, sub_once=False, rematched=False, from_start=False):
    """Play an online episode: link the release if nobody has yet, then let
    ani-cli resolve the episode and run mpv in the foreground
    (--exit-after-play) with the hub's IPC socket, resume point and
    skip-credits script in ANI_CLI_PLAYER_FLAGS. watch_process tracks the
    ani-cli process like a local mpv. A dub that isn't out yet falls back to
    the sub once; a result ani-cli stopped listing is re-matched once."""
    mid = stream_media_id(ep_id)
    token = ep_id.split(":", 2)[2] if mid else ""
    if not mid or not anicli.STREAM_EP_RE.match(token):
        return {"error": "unknown episode"}
    if not system.find_program("mpv"):
        return {"error": "mpv was not found — install mpv and make sure it is on PATH."}
    try:
        link = stream_autolink(mid)
    except anicli.AniCliError as e:
        return {"error": str(e)}
    if mode in anicli.STREAM_MODES and mode != link["mode"]:
        stream_set_mode(mid, mode)
        link["mode"] = mode
    if token not in link["episodes"] and time.time() - link["episodes_ts"] > 60:
        try:
            stream_refresh(mid)
            link = stream_link_get(mid)
        except anicli.AniCliError:
            pass
    if token not in link["episodes"]:
        return {"error": f"E{token} isn't out yet."}
    s, ep = library.episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    sea = next(x for x in s["seasons"] if any(e is ep for e in x["episodes"]))
    label = session_label(sea, ep)
    code = f"E{token}"
    play_mode = "sub" if sub_once else link["mode"]
    sock = player.ipc_socket_path()
    flags = [f"--input-ipc-server={sock}", "--no-input-terminal", *player.resume_args(ep_id, from_start)]
    script = mpv_script_flag()
    if script:
        flags.append(script)
    user_flags = os.environ.get("ANI_CLI_PLAYER_FLAGS", "").strip()
    if user_flags:
        flags.append(user_flags)
    env = anicli.anicli_env(link, ANI_CLI_PLAYER="mpv", ANI_CLI_PLAYER_FLAGS=" ".join(flags), ANI_CLI_MODE=play_mode,
                            **player.mpv_env(sock))
    with database.db() as c:
        # Identity stamp (TODO 25): history stays readable without the link.
        c.execute("INSERT INTO episodes(id,show_id,label) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE"
                  " SET show_id=excluded.show_id, label=excluded.label", (ep_id, s["id"], label))
    errlog = config.DATA / "anicli-stderr.log"
    with player._launch_lock:
        with player._active_lock:
            old = next(iter(player._active.values()), None)
        if old:
            player.retire_session(old["proc"])
        err, offset = None, 0
        try:
            err = open(errlog, "ab")
            err.write(f"\n=== {util.utc_stamp()}  {s['title']} {code} ({play_mode})\n".encode())
            err.flush()
            offset = err.tell()
        except OSError:
            err = None
        try:
            proc = anicli.anicli_spawn(["--exit-after-play", "-e", token, link["query"]], env,
                                subprocess.DEVNULL, err or subprocess.DEVNULL)
        except anicli.AniCliError as e:
            return {"error": friendly_stream_error(str(e), code)}
        finally:
            if err:
                err.close()
        with player._active_lock:
            player._active.clear()
            player._active[ep_id] = {"proc": proc, "ep_id": ep_id, "show_title": s["title"],
                              "label": label, "percent": 0, "version": 0, "loading": True}

    def failure(rc):
        try:
            with open(errlog, "rb") as f:
                f.seek(offset)
                text = f.read().decode(errors="replace")
        except OSError:
            text = ""
        msg = anicli.anicli_message(text)
        util.log("stream %s failed: %s" % (ep_id, msg or "exit code %s" % rc))
        if play_mode == "dub" and "no sources found for dub" in msg.lower():
            _session_notice.update(msg=f"{code} isn't dubbed yet — playing it subbed.", ts=time.time())
            r = start_stream(ep_id, sub_once=True, from_start=from_start)
            return r.get("error")
        if "invalid anime selection" in msg.lower() and not rematched:
            try:
                stream_autolink(mid, rematch=True)
            except anicli.AniCliError as e:
                return str(e)
            r = start_stream(ep_id, mode, sub_once, rematched=True, from_start=from_start)
            return r.get("error")
        return friendly_stream_error(msg, code)

    threading.Thread(target=player.watch_process, args=(proc, ep_id, sock),
                     kwargs={"startup": anicli.STREAM_STARTUP_TIMEOUT, "failure": failure},
                     daemon=True).start()
    return {"ok": True, "stream": True}
