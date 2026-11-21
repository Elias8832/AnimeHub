"""The Browse tab's data (TODO 260): AniList's public catalog — what's
trending, this season, next season, all-time popular, the top 100 — and any
of them narrowed by genre, year, season, format and score.

Read without a login (it is the catalog, not your lists), so it works with a
MyAnimeList account too (Decision 75). Answers are kept in memory for a few
minutes only, never on disk: they are read on demand for one person's own
screen (AniList's terms: no data storage, no hoarding).
"""

import threading
import time
from datetime import datetime

from . import anilist

TTL = 10 * 60            # a list answer is reused this long
PER_PAGE = 50            # AniList's largest page
ROW = 12                 # posters per row on the overview
TOP = 100                # the Top 100 stops here
SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
FORMATS = ("TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC")
SORTS = {"trending": "TRENDING_DESC", "popularity": "POPULARITY_DESC", "score": "SCORE_DESC",
         "newest": "START_DATE_DESC", "title": "TITLE_ROMAJI"}
# Each list: its own order, and what narrows it.
LISTS = {"trending": "trending", "season": "popularity", "next": "popularity",
         "popular": "popularity", "top": "score", "all": "popularity"}

FIELDS = """id title{english romaji} format episodes seasonYear season status averageScore popularity genres
coverImage{extraLarge color} bannerImage studios(isMain:true){nodes{name}}"""
PAGE = """query($page:Int,$per:Int,$sort:[MediaSort],$season:MediaSeason,$year:Int,$genres:[String],
$format:MediaFormat,$score:Int,$scoreMax:Int,$from:FuzzyDateInt,$to:FuzzyDateInt){
Page(page:$page,perPage:$per){ pageInfo{hasNextPage} media(type:ANIME,isAdult:false,sort:$sort,season:$season,
seasonYear:$year,genre_in:$genres,format:$format,averageScore_greater:$score,averageScore_lesser:$scoreMax,startDate_greater:$from,
startDate_lesser:$to){ %s } } }""" % FIELDS
GENRES = "query{ GenreCollection }"

_lock = threading.Lock()
_cache = {}      # key -> (time, answer)
_genres = None   # AniList's genre list, read once per run


def season_of(now=None):
    """AniList's season for a date: December already belongs to next year's winter."""
    d = now or datetime.now()
    if d.month == 12:
        return "WINTER", d.year + 1
    return SEASONS[(d.month - 1) // 3], d.year


def step(season, year, n):
    """The season n steps away (n may be negative)."""
    i = SEASONS.index(season) + n
    return SEASONS[i % 4], year + i // 4


def label(season, year):
    return "%s %d" % (season.title(), year)


def item(m):
    """AniList's media → the card's shape (pure)."""
    t, c = m.get("title") or {}, m.get("coverImage") or {}
    studios = ((m.get("studios") or {}).get("nodes") or [])
    return {"id": m["id"], "title": t.get("english") or t.get("romaji"), "format": m.get("format"),
            "episodes": m.get("episodes"), "year": m.get("seasonYear"), "season": m.get("season"),
            "airing": m.get("status"), "average": m.get("averageScore"), "popularity": m.get("popularity"),
            "genres": m.get("genres") or [], "poster": c.get("extraLarge"), "color": c.get("color"),
            "banner": m.get("bannerImage"), "studio": studios[0].get("name") if studios else None}


def variables(spec, page=1, per=PER_PAGE):
    """A list + its filters → the query's variables (pure). spec keys: list,
    season/year (which season a season list shows), genres, fyear and score
    (ranges: (lo, hi), both ends counted), fseason, format, sort."""
    lst = spec.get("list") or "all"
    sort = spec.get("sort") or LISTS.get(lst, "popularity")
    v = {"page": page, "per": per, "sort": [SORTS.get(sort, "POPULARITY_DESC")]}
    if sort == "score":
        v["sort"].append("POPULARITY_DESC")  # ties: the better known first
    if lst in ("season", "next"):
        v["season"], v["year"] = spec["season"], spec["year"]
    if spec.get("fseason"):
        v["season"] = spec["fseason"]
    if spec.get("fyear") and lst not in ("season", "next"):  # a season list's year is the season's
        lo, hi = spec["fyear"]
        v["from"], v["to"] = lo * 10000, (hi + 1) * 10000  # whole years, movies with no season too
    if spec.get("genres"):
        v["genres"] = list(spec["genres"])
    if spec.get("format"):
        v["format"] = spec["format"]
    if spec.get("score"):
        lo, hi = spec["score"]
        if lo > 0:
            v["score"] = lo - 1  # AniList's filters are "greater than" / "less than"
        if hi < 100:
            v["scoreMax"] = hi + 1
    return v


def _ask(query, v=None):
    with anilist.quick():
        res = anilist.anilist_request({"query": query, "variables": v or {}})
    if res.get("errors") and not res.get("data"):
        raise RuntimeError(anilist._graphql_error_msg(res))
    return res.get("data") or {}


def _cached(key, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    out = fn()
    with _lock:
        for k in [k for k, (t, _) in _cache.items() if now - t >= TTL]:
            del _cache[k]
        _cache[key] = (now, out)
    return out


def _seasons(now=None):
    s, y = season_of(now)
    ns, ny = step(s, y, 1)
    return {"season": {"season": s, "year": y, "label": label(s, y)},
            "next": {"season": ns, "year": ny, "label": label(ns, ny)}}


ROWS = ("trending", "season", "next", "popular", "top")
OVERVIEW = """query($s:MediaSeason,$y:Int,$ns:MediaSeason,$ny:Int,$per:Int){
trending: Page(perPage:$per){ media(type:ANIME,isAdult:false,sort:TRENDING_DESC){ %(f)s } }
season: Page(perPage:$per){ media(type:ANIME,isAdult:false,sort:POPULARITY_DESC,season:$s,seasonYear:$y){ %(f)s } }
next: Page(perPage:$per){ media(type:ANIME,isAdult:false,sort:POPULARITY_DESC,season:$ns,seasonYear:$ny){ %(f)s } }
popular: Page(perPage:$per){ media(type:ANIME,isAdult:false,sort:POPULARITY_DESC){ %(f)s } }
top: Page(perPage:$per){ media(type:ANIME,isAdult:false,sort:[SCORE_DESC,POPULARITY_DESC]){ %(f)s } } }""" % {"f": FIELDS}


def overview(now=None):
    """Every row's first posters in one request (aliases), and the seasons they mean."""
    seas = _seasons(now)
    def build():
        data = _ask(OVERVIEW, {"s": seas["season"]["season"], "y": seas["season"]["year"],
                               "ns": seas["next"]["season"], "ny": seas["next"]["year"], "per": ROW})
        return {k: [item(m) for m in (data.get(k) or {}).get("media") or []] for k in ROWS}
    return {"seasons": seas, "rows": _cached(("overview", seas["season"]["label"]), build)}


def page(spec, n=1, now=None):
    """One page of a list, filtered: {items, page, has_next}."""
    lst = spec.get("list") or "all"
    if lst in ("season", "next") and not spec.get("season"):
        spec = {**spec, **_seasons(now)[lst]}
    v = variables(spec, n)
    if lst == "top" and (n - 1) * PER_PAGE >= TOP:
        return {"items": [], "page": n, "has_next": False}
    key = ("page",) + tuple(sorted((k, tuple(x) if isinstance(x, list) else x) for k, x in v.items()))
    def build():
        p = _ask(PAGE, v).get("Page") or {}
        return {"items": [item(m) for m in p.get("media") or []], "has_next": bool((p.get("pageInfo") or {}).get("hasNextPage"))}
    out = dict(_cached(key, build), page=n)
    if lst == "top" and n * PER_PAGE >= TOP:
        out["has_next"] = False
    return out


def genres():
    global _genres
    if _genres is None:
        g = _ask(GENRES).get("GenreCollection") or []
        _genres = [x for x in g if x != "Hentai"]
    return _genres


def _span(text, name, lo, hi):
    """"2010-2020" (or one value, "2015") → (2010, 2020), checked."""
    parts = text.split("-")
    if len(parts) > 2 or not all(p.isdigit() for p in parts):
        raise ValueError("'%s' must be a number or a range like %d-%d" % (name, lo, hi))
    a, b = int(parts[0]), int(parts[-1])
    if not lo <= a <= b <= hi:
        raise ValueError("'%s' must be within %d-%d, low end first" % (name, lo, hi))
    return a, b


def parse(qs):
    """The query string → a spec, checked. Raises ValueError with what's wrong."""
    one = lambda k: (qs.get(k) or [""])[0].strip()
    lst = one("list") or "overview"
    if lst not in LISTS and lst not in ("overview", "genres"):
        raise ValueError("unknown list %r" % lst)
    spec = {"list": lst}
    if one("season"):
        if one("season") not in SEASONS or not one("year").isdigit():
            raise ValueError("'season' needs one of %s and a 'year'" % ", ".join(SEASONS))
        spec["season"], spec["year"] = one("season"), int(one("year"))
    if one("genres"):
        spec["genres"] = [g for g in one("genres").split(",") if g][:10]
    if one("fyear"):
        spec["fyear"] = _span(one("fyear"), "fyear", 1900, 2100)
    if one("fseason"):
        if one("fseason") not in SEASONS:
            raise ValueError("'fseason' must be one of %s" % ", ".join(SEASONS))
        spec["fseason"] = one("fseason")
    if one("format"):
        if one("format") not in FORMATS:
            raise ValueError("'format' must be one of %s" % ", ".join(FORMATS))
        spec["format"] = one("format")
    if one("score"):
        spec["score"] = _span(one("score"), "score", 0, 100)
    if one("sort"):
        if one("sort") not in SORTS:
            raise ValueError("'sort' must be one of %s" % ", ".join(SORTS))
        spec["sort"] = one("sort")
    n = one("page") or "1"
    if not n.isdigit() or int(n) < 1:
        raise ValueError("'page' must be a positive integer")
    spec["page"] = int(n)
    return spec


def catalog(qs, now=None):
    """GET /api/catalog."""
    spec = parse(qs)
    if spec["list"] == "overview":
        return overview(now)
    if spec["list"] == "genres":
        return {"genres": genres()}
    n = spec.pop("page")
    return page(spec, n, now)
