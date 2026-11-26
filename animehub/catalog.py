"""The Browse tab's data (TODO 260): AniList's public catalog — what's
trending, this season, next season, all-time popular, the top 100 — and any
of them narrowed by genre, year, season, format and score.

Read without a login (it is the catalog, not your lists), so it works with a
MyAnimeList account too (Decision 75). Answers are kept in memory for a few
minutes only, never on disk: they are read on demand for one person's own
screen (AniList's terms: no data storage, no hoarding).
"""

import re
import threading
import time
from datetime import datetime

from . import anilist, titles

TTL = 10 * 60            # a list answer is reused this long
PER_PAGE = 50            # AniList's largest page
ROW = 12                 # posters per row on the overview
TOP = 100                # the Top 100 stops here
SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
FORMATS = ("TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA", "MUSIC")
SORTS = {"trending": "TRENDING_DESC", "popularity": "POPULARITY_DESC", "score": "SCORE_DESC",
         "newest": "START_DATE_DESC", "title": "TITLE_ROMAJI", "relevance": "SEARCH_MATCH"}
# Each list: its own order, and what narrows it.
LISTS = {"trending": "trending", "season": "popularity", "next": "popularity",
         "popular": "popularity", "top": "score", "all": "popularity"}

FIELDS = """id title{english romaji} format episodes seasonYear season status averageScore popularity genres
coverImage{extraLarge color} bannerImage studios(isMain:true){nodes{name}}"""
PAGE = """query($page:Int,$per:Int,$sort:[MediaSort],$season:MediaSeason,$year:Int,$genres:[String],
$format:MediaFormat,$score:Int,$scoreMax:Int,$from:FuzzyDateInt,$to:FuzzyDateInt,$search:String,$ids:[Int]){
Page(page:$page,perPage:$per){ pageInfo{hasNextPage} media(type:ANIME,isAdult:false,sort:$sort,season:$season,
seasonYear:$year,genre_in:$genres,format:$format,averageScore_greater:$score,averageScore_lesser:$scoreMax,startDate_greater:$from,
startDate_lesser:$to,search:$search,id_in:$ids){ %s } } }""" % FIELDS
PERSON_QUERY = """query($id:Int,$page:Int,$per:Int,$sort:[MediaSort]){
Staff(id:$id){ id name{full native} image{large medium} description(asHtml:false) favourites
characterMedia(page:$page,perPage:$per,sort:$sort){ pageInfo{hasNextPage}
edges{ characterRole characters{ id name{full} } node{ type isAdult %s } } } } }""" % FIELDS
STUDIO_QUERY = """query($id:Int,$page:Int,$per:Int,$sort:[MediaSort]){
Studio(id:$id){ id name favourites isAnimationStudio
media(page:$page,perPage:$per,sort:$sort,isMain:true){ pageInfo{hasNextPage}
nodes{ type isAdult %s } } } }""" % FIELDS
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
    (ranges: (lo, hi), both ends counted), fseason, format, sort, q."""
    lst = spec.get("list") or "all"
    q = (spec.get("q") or "").strip()
    sort = spec.get("sort") or ("relevance" if q else LISTS.get(lst, "popularity"))
    v = {"page": page, "per": per, "sort": [SORTS.get(sort, "POPULARITY_DESC")]}
    if sort == "score":
        v["sort"].append("POPULARITY_DESC")  # ties: the better known first
    if q:
        v["search"] = q
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
    """One page of a list, filtered: {items, page, has_next}.
    When searching (`q`), check the local prefix/typo-tolerant title index
    (Decision 68) first so partial prefixes ("Hel" -> Hell's Paradise, Hellsing,
    Helck) and typos ("freiren") find matches instead of AniList's strict
    whole-word search returning nothing."""
    lst = spec.get("list") or "all"
    if lst in ("season", "next") and not spec.get("season"):
        spec = {**spec, **_seasons(now)[lst]}
    q = (spec.get("q") or "").strip()
    rank_by = None
    if q:
        hits = titles.search(q, limit=200)
        if hits:
            ids = list(dict.fromkeys(row[0] for row, _ in hits))
            rel = not spec.get("sort") or spec.get("sort") == "relevance"
            v = variables({**spec, "q": "", "sort": "popularity" if rel else spec.get("sort")}, n)
            v["ids"] = ids
            if rel:
                rank_by = {row[0]: round(slips, 2) for row, slips in hits}
        else:
            v = variables(spec, n)
    else:
        v = variables(spec, n)
    if lst == "top" and (n - 1) * PER_PAGE >= TOP:
        return {"items": [], "page": n, "has_next": False}
    key = ("page",) + tuple(sorted((k, tuple(x) if isinstance(x, list) else x) for k, x in v.items()))
    def build():
        p = _ask(PAGE, v).get("Page") or {}
        items = [item(m) for m in p.get("media") or []]
        if rank_by is not None:
            items.sort(key=lambda x: (rank_by.get(x["id"], 99), -(x.get("popularity") or 0)))
        return {"items": items, "has_next": bool((p.get("pageInfo") or {}).get("hasNextPage"))}
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


def _clean_bio(text):
    """Strip AniList markdown/spoiler markers into plain bio text (pure)."""
    if not text:
        return ""
    s = re.sub(r"~!.*?!~", "", text, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"__|\*\*|~~", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _media_sort(sort):
    s = SORTS.get(sort or "popularity", "POPULARITY_DESC")
    if s == "SEARCH_MATCH":
        s = "POPULARITY_DESC"
    return [s, "POPULARITY_DESC"] if s == "SCORE_DESC" else [s]


def person_page(staff_id, sort="popularity", n=1):
    """One page of a voice actor's anime roles (in memory only, TODO 260)."""
    srt = _media_sort(sort)
    key = ("person", staff_id, tuple(srt), n)
    def build():
        st = _ask(PERSON_QUERY, {"id": staff_id, "page": n, "per": PER_PAGE, "sort": srt}).get("Staff")
        if not st:
            return {"person": None, "items": [], "has_next": False}
        nm = st.get("name") or {}
        img = st.get("image") or {}
        person = {"id": st["id"], "name": nm.get("full") or nm.get("native") or "",
                  "native": nm.get("native"), "image": img.get("large") or img.get("medium"),
                  "description": _clean_bio(st.get("description")),
                  "favourites": st.get("favourites")}
        cm = st.get("characterMedia") or {}
        by_id, order = {}, []
        for edge in cm.get("edges") or []:
            node = edge.get("node") or {}
            if not node.get("id") or node.get("type") != "ANIME" or node.get("isAdult"):
                continue
            chars = [((c.get("name") or {}).get("full") or "").strip()
                     for c in (edge.get("characters") or []) if (c.get("name") or {}).get("full")]
            if node["id"] not in by_id:
                row = item(node)
                row["characters"] = list(dict.fromkeys(chars))[:2]
                row["role"] = edge.get("characterRole")
                by_id[node["id"]] = row
                order.append(node["id"])
            else:
                have = by_id[node["id"]]["characters"]
                for ch in chars:
                    if ch not in have and len(have) < 2:
                        have.append(ch)
        items = []
        for mid in order:
            row = by_id[mid]
            row["character"] = ", ".join(row.pop("characters")) or None
            items.append(row)
        return {"person": person, "items": items,
                "has_next": bool((cm.get("pageInfo") or {}).get("hasNextPage"))}
    return dict(_cached(key, build), page=n)


def studio_page(studio_id, sort="popularity", n=1):
    """One page of a studio's main anime credits (in memory only, TODO 260)."""
    srt = _media_sort(sort)
    key = ("studio", studio_id, tuple(srt), n)
    def build():
        st = _ask(STUDIO_QUERY, {"id": studio_id, "page": n, "per": PER_PAGE, "sort": srt}).get("Studio")
        if not st:
            return {"studio": None, "items": [], "has_next": False}
        studio = {"id": st["id"], "name": st.get("name") or "",
                  "favourites": st.get("favourites"),
                  "is_animation_studio": bool(st.get("isAnimationStudio"))}
        md = st.get("media") or {}
        seen, items = set(), []
        for node in md.get("nodes") or []:
            if not node.get("id") or node["id"] in seen or node.get("type") != "ANIME" or node.get("isAdult"):
                continue
            seen.add(node["id"])
            items.append(item(node))
        return {"studio": studio, "items": items,
                "has_next": bool((md.get("pageInfo") or {}).get("hasNextPage"))}
    return dict(_cached(key, build), page=n)


def parse(qs):
    """The query string → a spec, checked. Raises ValueError with what's wrong."""
    one = lambda k: (qs.get(k) or [""])[0].strip()
    lst = one("list") or "overview"
    if lst not in LISTS and lst not in ("overview", "genres", "person", "studio"):
        raise ValueError("unknown list %r" % lst)
    spec = {"list": lst}
    if lst in ("person", "studio"):
        raw_id = one("id")
        if not raw_id.isdigit() or int(raw_id) < 1:
            raise ValueError("'id' must be a positive integer")
        spec["id"] = int(raw_id)
    if one("q"):
        spec["q"] = one("q")[:120]
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
    if spec["list"] == "person":
        return person_page(spec["id"], spec.get("sort") or "popularity", n)
    if spec["list"] == "studio":
        return studio_page(spec["id"], spec.get("sort") or "popularity", n)
    return page(spec, n, now)
