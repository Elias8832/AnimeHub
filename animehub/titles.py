"""Every anime title, on this PC, for the search box (Decision 68, TODO 175).

AniList's search matches exact words only: one typo ("freiren") finds
nothing, and asking it again with guessed spellings is slow. So the hub keeps
a list of all anime titles (manami-project's anime-offline-database, free
under ODbL, new release every week) and searches it here, typo-tolerant and
at once. AniList is then asked only for the posters and details of the hits,
in one request.

The list is fetched the first time someone searches, and again when it is
two weeks old. It is kept small on disk: AniList id, titles in Latin script,
nothing else. Until it is there (or when the download fails) the search box
works as before, through AniList's own search."""

import bisect
import json
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from urllib.request import Request, urlopen

from . import VERSION, system
from .config import config

RELEASE = "https://github.com/manami-project/anime-offline-database/releases/latest/download/"
MAX_AGE = 14 * 86400
RETRY_AFTER = 3600  # a failed download is tried again an hour later
UA = {"User-Agent": "anime-hub/%s" % VERSION}

_lock = threading.Lock()
_index = None       # the loaded list, see _Index
_busy = False       # a download is running
_failed_at = 0.0


def fold(t):
    """Lowercase, accents and apostrophes off ("journey's" = "journeys"),
    every run of other characters one space."""
    t = unicodedata.normalize("NFKD", re.sub(r"['’`]", "", str(t or "").lower()))
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


def edits(a, b, limit):
    """Letter changes from a to b (a swap of two neighbours counts as one);
    anything above `limit` comes back as limit + 1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if prev2 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return min(prev[-1], limit + 1)


def slips(w):
    """How many slips a typed word may have: under 4 letters none, 4-5 one,
    6-8 two ("freirem" is Frieren), longer three."""
    return 0 if len(w) < 4 else 1 if len(w) < 6 else 2 if len(w) < 9 else 3


TYPES = {"TV": 0, "MOVIE": 1, "ONA": 1}  # everything else after these
MISSING = 0.6  # a typed word no title of the show comes near costs 1 + this per letter


def card(row):
    """A result row's stand-in until AniList fills it in: what the list knows."""
    mid, _, _, fmt, year, eps, title = row
    return {"id": mid, "title": {"romaji": title, "english": None}, "format": fmt, "seasonYear": year,
            "episodes": eps, "coverImage": {}, "stub": True}


class _Index:
    """rows: [anilist id, known, [folded titles], format, year, episodes,
    title] (known: how many names the show has in all languages — the list
    has no popularity, and well-known shows have many); words: word -> row
    numbers; by_len: the words by length, for the typo scan."""

    def __init__(self, rows):
        self.rows = rows
        self.words = {}
        for n, row in enumerate(rows):
            names = row[2]
            for name in names:
                for w in name.split():
                    self.words.setdefault(w, set()).add(n)
        self.words = {w: tuple(r) for w, r in self.words.items()}
        self.sorted = sorted(self.words)
        self.by_len = {}
        for w in self.sorted:
            self.by_len.setdefault(len(w), []).append(w)

    def word_hits(self, w, last):
        """Words in the list that `w` may mean, each with its slips."""
        hits = {w: 0} if w in self.words else {}
        if last and len(w) >= 2:
            # Still typing: the last word may be the start of a longer one.
            i = bisect.bisect_left(self.sorted, w)
            for v in self.sorted[i:i + 400]:
                if not v.startswith(w):
                    break
                hits.setdefault(v, 0.3)
        k = slips(w) if w not in self.words else 0  # a real word is taken as typed
        # A typo seldom hits both of the first two letters: checking only
        # words that keep one of them (or swap them) makes the scan ~10x quicker.
        a, b = w[0], w[1:2]
        for n in range(len(w) - k, len(w) + k + 1) if k else ():
            for v in self.by_len.get(n, ()):
                if (v[0] == a or v[1:2] == b or v[0] == b) and hits.get(v, 99) > 0:
                    d = edits(w, v, k)
                    if d <= k and d < hits.get(v, 99):
                        hits[v] = d
        return hits

    def search(self, query, limit):
        """[(row, slips)], closest first. A show may miss a typed word when
        the rest is close enough (1 + MISSING per letter, at most 40 % of
        what was typed), so a badly mangled word still finds something."""
        whole = fold(query)
        words = whole.split()
        if not words:
            return []
        per_word = []
        for n, w in enumerate(words):
            mine = {}
            for v, d in self.word_hits(w, n == len(words) - 1).items():
                for r in self.words[v]:
                    if d < mine.get(r, 99):
                        mine[r] = d
            per_word.append(mine)
        letters = sum(map(len, words))
        cap = max(0.99, 0.4 * letters)
        score = {}
        for r in set().union(*per_word):
            total = sum(mine.get(r, 1 + MISSING * len(w)) for w, mine in zip(words, per_word))
            if total <= cap or all(r in mine for mine in per_word):
                score[r] = total - (1 if whole in self.rows[r][2] else 0)  # the very title typed
        best = sorted(score, key=lambda r: (score[r], TYPES.get(self.rows[r][3], 2), -self.rows[r][1], r))[:limit]
        return [(self.rows[r], score[r]) for r in best]


def _path():
    return config.CACHE / "anime-titles-v2.json"


def _fetch():
    """The newest list as JSON lines. The zstd copy is 6 MB, the plain one
    62 MB: zstd when this Python (3.14+) or the zstd program can unpack it."""
    try:
        from compression import zstd
    except ImportError:
        zstd = None
    tool = None if zstd else shutil.which("zstd")
    name = "anime-offline-database.jsonl" + (".zst" if zstd or tool else "")
    with urlopen(Request(RELEASE + name, headers=UA), timeout=120) as r:
        raw = r.read()
    if zstd:
        return zstd.decompress(raw)
    if tool:
        return subprocess.run([tool, "-dc"], input=raw, capture_output=True, check=True, timeout=120).stdout
    return raw


def parse(raw):
    """[anilist id, known, [folded titles], format, year, episodes, title]
    for every entry AniList knows."""
    rows = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if "anilist.co/anime/" not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        mid = next((int(m.group(1)) for s in entry.get("sources") or ()
                    for m in [re.search(r"anilist\.co/anime/(\d+)", s)] if m), None)
        names, synonyms = [], entry.get("synonyms") or []
        for t in [entry.get("title"), *synonyms]:
            f = fold(t)
            if f and f not in names:
                names.append(f)
        if mid and names:
            rows.append([mid, len(synonyms), names[:12], entry.get("type"),
                         (entry.get("animeSeason") or {}).get("year"), entry.get("episodes"), entry.get("title")])
    return rows


def _refresh():
    global _busy, _failed_at, _index
    try:
        rows = parse(_fetch())
        if len(rows) < 1000:
            raise ValueError("the title list came back nearly empty")
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps({"rows": rows}, separators=(",", ":")), encoding="utf-8")
        system.replace(tmp, path)  # Windows: a search may be reading it
        index = _Index(rows)
        with _lock:
            _index = index
    except Exception:
        _failed_at = time.time()
    finally:
        _busy = False


def _load():
    global _index
    try:
        rows = json.loads(system.read_bytes(_path()))["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    index = _Index(rows)
    with _lock:
        if _index is None:
            _index = index


def ensure(wait=False):
    """Load the list from disk, and fetch a new one in the background when
    it is missing or two weeks old."""
    global _busy
    path = _path()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = None
    if _index is None and age is not None:
        _load()
    stale = age is None or age > MAX_AGE
    with _lock:
        start = stale and not _busy and time.time() - _failed_at > RETRY_AFTER
        if start:
            _busy = True
    if start:
        t = threading.Thread(target=_refresh, daemon=True)
        t.start()
        if wait:
            t.join()


def search(query, limit=50):
    """[(row, slips)], closest first; None while the list isn't here yet."""
    ensure()
    index = _index
    return None if index is None else index.search(query, limit)
