"""Home's "Recommended for you" row (TODO 65).

AniList's own recommendations of the shows you rated highest (or marked
favourite), merged: a show recommended for several of your shows ranks
higher (AniList's vote count counts on a log scale), weighted by how much
you liked the show it came from. Anything already on one of your lists is left out —
at read time, so adding one to Planning drops it at once.

One batched GraphQL request (aliases, one per seed show) builds the set
as background work under the shared limiter; the result is kept for a
day, or until the seed shows change. No account → no recommendations
(Home keeps "Ready to start", the unwatched folders on disk).
"""

import hashlib
import json
import math
import os
import threading
import time

from . import anilist, util
from .config import config


SEEDS = 10          # your best shows asked about, one alias each
PER_SEED = 8        # AniList recommendations read per show
KEEP = 24           # how many the row can show
TTL = 24 * 3600
REC_FIELDS = """id title{english romaji} format episodes seasonYear status averageScore genres
coverImage{extraLarge color} bannerImage"""

_lock = threading.Lock()
_building = False


def _cache_path():
    return config.DATA / "recommendations.json"


def _seeds(data):
    """Your best shows: watched or watching, scored 8+ (POINT_10) or favourite —
    highest score first, then most recently updated."""
    favs = set(data.get("favourites") or [])
    pool = [e for e in data.get("entries") or []
            if e.get("status") in ("COMPLETED", "CURRENT", "REPEATING")
            and ((e.get("score") or 0) >= 8 or e["id"] in favs)]
    pool.sort(key=lambda e: (-(e.get("score") or (9 if e["id"] in favs else 0)), -(e.get("updated") or 0)))
    return pool[:SEEDS]


def _seed_key(seeds):
    return hashlib.sha1(",".join(str(e["id"]) for e in seeds).encode()).hexdigest()[:16]


def build_query(seeds):
    parts = ["s%d: Media(id:%d,type:ANIME){ id recommendations(sort:RATING_DESC,perPage:%d){ nodes{ rating mediaRecommendation{ %s } } } }"
             % (i, int(e["id"]), PER_SEED, REC_FIELDS) for i, e in enumerate(seeds)]
    return "query{" + " ".join(parts) + "}"


def merge(seeds, data):
    """AniList's answer → ranked recommendations (pure). Each keeps the seed
    that contributed most as its "because you liked"."""
    out = {}
    for i, seed in enumerate(seeds):
        media = (data or {}).get("s%d" % i) or {}
        weight = max(seed.get("score") or 0, 7) / 10
        for node in ((media.get("recommendations") or {}).get("nodes") or []):
            m, rating = node.get("mediaRecommendation"), node.get("rating") or 0
            if not m or rating <= 0:
                continue
            gain = math.log1p(rating) * weight  # log: a show liked across several of yours beats one huge vote count
            r = out.get(m["id"])
            if r is None:
                t = m.get("title") or {}
                r = out[m["id"]] = {
                    "id": m["id"], "title": t.get("english") or t.get("romaji"),
                    "format": m.get("format"), "episodes": m.get("episodes"), "year": m.get("seasonYear"),
                    "airing": m.get("status"), "average": m.get("averageScore"), "genres": m.get("genres") or [],
                    "poster": (m.get("coverImage") or {}).get("extraLarge"), "color": (m.get("coverImage") or {}).get("color"),
                    "banner": m.get("bannerImage"), "rank": 0.0, "because": None, "_best": 0.0,
                }
            r["rank"] += gain
            if gain > r["_best"]:
                r["_best"], r["because"] = gain, {"id": seed["id"], "title": seed.get("title")}
    items = sorted(out.values(), key=lambda r: (-r["rank"], -(r["average"] or 0)))
    for r in items:
        r.pop("_best", None)
        r["rank"] = round(r["rank"], 1)
    return items


def _read_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(payload):
    _cache_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, _cache_path())


@anilist.background_work
def _build(seeds, key):
    global _building
    try:
        res = anilist.anilist_request({"query": build_query(seeds)})
        if res.get("errors") and not res.get("data"):
            raise RuntimeError(anilist._graphql_error_msg(res))
        _write_cache({"key": key, "ts": time.time(), "items": merge(seeds, res.get("data"))})
    except Exception as e:
        util.log("Recommendations not built: %s" % e)
    finally:
        with _lock:
            _building = False


def _start(seeds, key):
    global _building
    with _lock:
        if _building:
            return
        _building = True
    threading.Thread(target=_build, args=(seeds, key), daemon=True).start()


def recommendations(limit=KEEP):
    """GET /api/recommendations: {state, items}. state "none" (no account or
    nothing rated highly yet), "loading" (first build running), "ready".
    A stale set is served while a fresh one builds."""
    if anilist.load_anilist_username() is None:
        return {"state": "none", "items": []}
    data = anilist.anilist_cache_stale()
    if not data:
        return {"state": "loading", "items": []}
    seeds = _seeds(data)
    if not seeds:
        return {"state": "none", "items": []}
    key, cached = _seed_key(seeds), _read_cache()
    fresh = cached and cached.get("key") == key and time.time() - cached.get("ts", 0) < TTL
    if not fresh:
        _start(seeds, key)
    if not cached:
        return {"state": "loading", "items": []}
    listed = {e["id"] for e in data.get("entries") or []}
    items = [r for r in cached.get("items") or [] if r["id"] not in listed][:limit]
    return {"state": "ready", "items": items}
