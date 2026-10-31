"""The SQLite database: connection, schema migrations, key/value cache."""

import json
import sqlite3
import time
from contextlib import contextmanager

from . import util
from .config import config


@contextmanager
def db():
    conn = sqlite3.connect(config.DB, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            yield conn
    finally:
        conn.close()


def _add_columns(c, table, columns):
    existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    for column, kind in columns.items():
        if column not in existing:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def migration_3(c):
    """Scanner robustness (TODO 23): record which library root the database
    was indexed from. Root-aware identity (Decision 23.7): show/episode ids
    stay path-relative to the root — a *moved* library keeps its watch state
    (same relative layout → same ids) — while a *changed* root is surfaced as
    a scan state, never silently tombstoned. Adopting a new root becomes an
    explicit step when the config flow lands (step 3)."""
    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT)""")
    if (c.execute("SELECT 1 FROM settings WHERE key='library_root'").fetchone() is None
            and config.LIBRARY is not None):
        c.execute("INSERT INTO settings(key,value) VALUES('library_root',?)", (str(config.LIBRARY),))


def migration_4(c):
    """Normalize timestamps (READINESS B5, TODO 3): the progress loop wrote
    ISO strings ('...T12:00:00+00:00') while watched-completion wrote SQL
    datetime('now') strings ('... 12:00:00'); mixed formats sort wrong on the
    same day ('T' > ' ') and break the this-week cutoff. All last_played (and
    tombstone) values move to the canonical UTC 'YYYY-MM-DD HH:MM:SS' form,
    which every writer emits from now on."""
    for eid, lp in c.execute(
            "SELECT id, last_played FROM episodes WHERE last_played IS NOT NULL").fetchall():
        norm, changed = util.normalize_timestamp(lp)
        if changed:
            c.execute("UPDATE episodes SET last_played=? WHERE id=?", (norm, eid))
    for kind, key, ts in c.execute(
            "SELECT kind, key, ts FROM tombstones WHERE ts IS NOT NULL").fetchall():
        norm, changed = util.normalize_timestamp(ts)
        if changed:
            c.execute("UPDATE tombstones SET ts=? WHERE kind=? AND key=?", (norm, kind, key))


def migration_1(c):
    """Base schema: facts, fetched metadata, season art, plus the spine
    storage (Decisions 17.2/17.3) — curation overlay and scan tombstones —
    and the generic AniList catalog cache (TODO 27)."""
    c.execute("""CREATE TABLE IF NOT EXISTS episodes(
        id TEXT PRIMARY KEY, watched INTEGER DEFAULT 0, position REAL DEFAULT 0,
        duration REAL DEFAULT 0, last_played TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS shows_meta(
        show_id TEXT PRIMARY KEY, anilist_id INTEGER, title TEXT, year INTEGER,
        score REAL, overview TEXT, poster TEXT, banner TEXT, episodes_meta TEXT,
        format TEXT, fetched_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS season_thumbs(
        show_id TEXT, sea_key TEXT, anilist_id INTEGER, thumbs TEXT, titles TEXT,
        PRIMARY KEY(show_id, sea_key))""")
    # Spine storage (Decision 17.2): curation is the local overlay —
    # status, user score, dates, rewatches, favourite, per-show prefs.
    # Keyed by ref ("aid:<anilist_id>" once matched, "sid:<show_id>"
    # before), never by fetched metadata; `shows_meta.score` stays
    # AniList's averageScore, untouched.
    c.execute("""CREATE TABLE IF NOT EXISTS curation(
        ref TEXT PRIMARY KEY, status TEXT, score REAL,
        favourite INTEGER DEFAULT 0, rewatches INTEGER DEFAULT 0,
        started_at TEXT, finished_at TEXT, prefs TEXT, updated_at TEXT)""")
    # Scan tombstones (Decision 17.3): records of rows that fell out of
    # the last valid scan. Rows are never deleted by a scan.
    c.execute("""CREATE TABLE IF NOT EXISTS tombstones(
        kind TEXT NOT NULL, key TEXT NOT NULL, ts TEXT,
        PRIMARY KEY(kind, key))""")
    # Generic AniList catalog cache (TODO 27): media payloads and candidate
    # lists for local metadata/matching. Formerly the online_cache table.
    c.execute("""CREATE TABLE IF NOT EXISTS catalog_cache(
        key TEXT PRIMARY KEY, payload TEXT, ts REAL)""")
    _add_columns(c, "shows_meta", {"episode_thumbs": "TEXT"})
    _add_columns(c, "season_thumbs", {"ts": "REAL"})


def migration_2(c):
    """Streaming strip (TODO 18 v0.1 half, Decision 16): the vendored provider
    port and its tables leave the app. Local AniList catalog cache rows move
    to `catalog_cache`; online show metadata is archived to
    `retired_online_shows` so the retained online viewing facts in `episodes`
    keep their titles/art for later export (READINESS B3). The online episode
    rows themselves are facts and are never deleted; the provider-side tables
    are dropped."""
    for table in ("online_cache", "online_shows", "online_linkups"):
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                     (table,)).fetchone() is None:
            continue
        if table == "online_cache":
            # Databases stamped v1 by the pre-migration init_db never ran
            # migration_1, so make sure the destination table exists here.
            c.execute("""CREATE TABLE IF NOT EXISTS catalog_cache(
                key TEXT PRIMARY KEY, payload TEXT, ts REAL)""")
            c.execute("""INSERT OR IGNORE INTO catalog_cache(key,payload,ts)
                         SELECT key,payload,ts FROM online_cache
                         WHERE key LIKE 'media:v1:%' OR key LIKE 'media-candidates:v1:%'""")
        elif table == "online_shows":
            # Archive with the source's actual columns: old init_db added the
            # episode_thumbs/episode_titles columns ad hoc, so a v1 database
            # may carry 11 or 13 columns. Keep whatever exists — the archived
            # metadata keeps the retained online viewing facts informative.
            base = "slug TEXT PRIMARY KEY, title TEXT, poster TEXT, banner TEXT, anilist_id INTEGER, overview TEXT, year INTEGER, score REAL, format TEXT, episodes_meta TEXT, fetched_at TEXT"
            declared = list(c.execute("PRAGMA table_info(online_shows)"))
            extra = [(r[1], r[2]) for r in declared if r[1] not in
                     {"slug", "title", "poster", "banner", "anilist_id", "overview",
                      "year", "score", "format", "episodes_meta", "fetched_at"}]
            for name, kind in extra:
                base += f", {name} {kind}"
            c.execute(f"CREATE TABLE retired_online_shows({base})")
            cols = ", ".join(r[1] for r in declared)
            c.execute(f"INSERT OR IGNORE INTO retired_online_shows({cols}) SELECT {cols} FROM online_shows")
        c.execute(f"DROP TABLE {table}")


def migration_5(c):
    """History retention (TODO 25, Decision 17.3): episodes learn which show
    and which label they belonged to. Episode ids are path hashes, so a
    removed file's row used to be an anonymous number — history had to drop
    it. With identity stored, removed files stay visible in history with
    their show, poster and episode label. Backfill happens on the next scan
    merge (only rows with watch facts get stamped)."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(episodes)")}
    if "show_id" not in cols:
        c.execute("ALTER TABLE episodes ADD COLUMN show_id TEXT")
    if "label" not in cols:
        c.execute("ALTER TABLE episodes ADD COLUMN label TEXT")


def migration_6(c):
    """Completion-event memory (TODO 9, Decision 25): once-per-event
    dismissal for the show-completion popup. Event identity is
    (media_id, completion count) — a rewatch bump is a NEW event with its
    own one-shot popup; dismissed ones never re-appear."""
    c.execute("""CREATE TABLE IF NOT EXISTS completion_events(
        media_id TEXT NOT NULL, count INTEGER NOT NULL,
        seen INTEGER DEFAULT 0, ts TEXT,
        PRIMARY KEY(media_id, count))""")


def migration_7(c):
    """Durable write outbox (TODO 14, Decision 18.4): failed/ambiguous
    AniList writes keep their intent here — committed together with the
    local change, retried with backoff, account-bound (Decision 24). The
    payload is the intent itself: {patch, favourite?, account_id}."""
    c.execute("""CREATE TABLE IF NOT EXISTS outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media_id INTEGER NOT NULL,
        payload TEXT NOT NULL,
        account_id INTEGER,
        attempts INTEGER DEFAULT 0,
        next_try REAL DEFAULT 0,
        last_error TEXT,
        created TEXT)""")


def migration_8(c):
    """ani-cli links (v0.2 streaming, TODO 18 second half): which ani-cli
    search result streams a given AniList release. The hub stores only how
    to ask the user's ani-cli again — query, the result's exact title (the
    menu helper picks it by name) and its position as a fallback — plus the
    episode list ani-cli reported and AniList display metadata. Watch facts
    of streamed episodes live in `episodes` like every other episode, under
    ids "stream:<anilist_id>:<episode>"."""
    c.execute("""CREATE TABLE IF NOT EXISTS stream_links(
        media_id INTEGER PRIMARY KEY, query TEXT NOT NULL, title TEXT,
        idx INTEGER DEFAULT 1, mode TEXT DEFAULT 'sub', episodes TEXT,
        episodes_ts REAL, meta TEXT, updated_at TEXT)""")


def migration_9(c):
    """Automatic ani-cli matching: links are made by the hub on first need,
    so record who chose a link (an automatic match never overrides a user's
    pick), and remember releases ani-cli has no confident match for so
    pages don't re-search on every visit."""
    _add_columns(c, "stream_links", {"matched_by": "TEXT"})
    c.execute("""CREATE TABLE IF NOT EXISTS stream_misses(
        media_id INTEGER PRIMARY KEY, ts REAL)""")


def migration_10(c):
    """Season → release map (multi-season rework, step 1): one row says
    which AniList release a range of a folder's episodes belongs to.
    ep_offset turns a local episode into the release's own number
    (local E13 with offset -12 is release E1). Seeded from the per-season
    matches already stored in season_thumbs, so matched seasons keep working."""
    c.execute("""CREATE TABLE IF NOT EXISTS season_releases(
        show_id TEXT, sea_key TEXT, first_ep INTEGER, last_ep INTEGER,
        media_id INTEGER, ep_offset INTEGER DEFAULT 0, source TEXT,
        PRIMARY KEY(show_id, sea_key, first_ep))""")
    c.execute("""INSERT OR IGNORE INTO season_releases(show_id, sea_key, first_ep, last_ep,
                    media_id, ep_offset, source)
                 SELECT show_id, sea_key, 1, NULL, anilist_id, 0, 'auto'
                 FROM season_thumbs WHERE anilist_id IS NOT NULL""")


def migration_reserved(c):
    """Versions 11–14 were used by the abandoned multi-season rework
    (docs/franchise-page-plan.md): databases it ran on are already at 14
    and keep its extra tables, which nothing reads. Nothing to do here."""


migration_reserved.noop = True


def migration_15(c):
    """Franchise pages (docs/franchise-page-plan.md, phase 2): `media` keeps
    each AniList entry as FRANCHISE_QUERY_ID returned it, `franchises` /
    `franchise_members` which entries share one page and in which order.
    Filled in the background (refresh_franchises); pages only read them."""
    c.execute("""CREATE TABLE IF NOT EXISTS media(
        id INTEGER PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS franchises(
        id INTEGER PRIMARY KEY, ts REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS franchise_members(
        media_id INTEGER PRIMARY KEY, franchise_id INTEGER NOT NULL, pos INTEGER NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS franchise_members_fid ON franchise_members(franchise_id, pos)")


def migration_16(c):
    """Local mapping (docs/franchise-page-plan.md, phase 3): which entry a
    folder season is, is now worked out from the stored franchise on every
    read, so `season_releases` only keeps the user's own pins (source
    'manual'); the automatic rows older matchers wrote go. `entry_art` holds
    each entry's episode stills and titles, keyed by the entry's own
    episode numbers (season_thumbs keyed them per folder season)."""
    c.execute("DELETE FROM season_releases WHERE COALESCE(source, 'auto') != 'manual'")
    c.execute("""CREATE TABLE IF NOT EXISTS entry_art(
        media_id INTEGER PRIMARY KEY, thumbs TEXT, titles TEXT, source TEXT, ts REAL NOT NULL)""")


def migration_17(c):
    """ani.zip files some entries' episodes as "O1".."On" (anizip_pick):
    stills read from it before that was understood are fetched again."""
    c.execute("DELETE FROM entry_art WHERE source = 'anizip'")


def migration_18(c):
    """Completion popups survive a restart: the event keeps the title the
    popup shows (unseen events are read back on boot)."""
    _add_columns(c, "completion_events", {"title": "TEXT"})


def migration_19(c):
    """Episode numbers the user set or confirmed (Needs a look), keyed by
    the file's path in the library: files the name gives no number for."""
    c.execute("""CREATE TABLE IF NOT EXISTS episode_numbers(
        path TEXT PRIMARY KEY, number INTEGER NOT NULL)""")


def migration_20(c):
    """Episode lengths measured from the files (lengths.py), keyed by the
    file's full path; size + mtime say when a file changed and needs
    measuring again. seconds is NULL when the file could not be read."""
    c.execute("""CREATE TABLE IF NOT EXISTS file_lengths(
        path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, seconds REAL)""")


def migration_21(c):
    """Audio and subtitle tracks remembered per show (tracks.py): show is
    "media:<AniList id>" or "show:<library show id>", each pick JSON."""
    c.execute("""CREATE TABLE IF NOT EXISTS track_prefs(
        show TEXT PRIMARY KEY, audio TEXT, sub TEXT)""")


def migration_22(c):
    """Phones paired with the Wi-Fi remote (remote.py, Decision 43): only
    the SHA-256 of each phone's token is kept."""
    c.execute("""CREATE TABLE IF NOT EXISTS remote_devices(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, token_hash TEXT UNIQUE NOT NULL,
        created TEXT, last_seen TEXT)""")


MIGRATIONS = [migration_1, migration_2, migration_3, migration_4, migration_5, migration_6, migration_7,
              migration_8, migration_9, migration_10, migration_reserved, migration_reserved,
              migration_reserved, migration_reserved, migration_15, migration_16, migration_17,
              migration_18, migration_19, migration_20, migration_21, migration_22]


def snapshot_before(target_version):
    """WAL-safe backup (SQLite backup API) taken before applying a migration,
    so a failed migration is recoverable from the pre-change state. Skipped
    when nothing user-owned exists yet (fresh schema, zero rows)."""
    dest = config.DB.parent / f"hub.db.backup-v{target_version - 1}"
    if dest.exists():
        return
    src = sqlite3.connect(config.DB)
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not any(t in tables and src.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                   for t in ("episodes", "shows_meta", "curation", "tombstones", "catalog_cache")):
            return
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def init_db():
    config.DB.parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        version = c.execute("PRAGMA user_version").fetchone()[0]
    for target, step in enumerate(MIGRATIONS, start=1):
        if target <= version:
            continue
        if not getattr(step, "noop", False):
            snapshot_before(target)
        with db() as c:
            # Re-check under the write lock: two processes may race startup.
            if c.execute("PRAGMA user_version").fetchone()[0] >= target:
                continue
            step(c)
            c.execute(f"PRAGMA user_version = {target}")


def cache_get(key, ttl):
    """Generic AniList catalog cache (TODO 27): media payloads and candidate
    lists used by local metadata/matching. Lives in `catalog_cache` since the
    streaming strip renamed it out of `online_cache`."""
    with db() as c:
        row = c.execute("SELECT payload, ts FROM catalog_cache WHERE key=?", (key,)).fetchone()
    if row and time.time() - row[1] < ttl:
        try:
            return json.loads(row[0])
        except Exception:
            return None
    return None


def cache_get_ts(key):
    """(payload, ts) at any age, or None — for stale-while-refreshing reads."""
    with db() as c:
        row = c.execute("SELECT payload, ts FROM catalog_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0]), row[1]
    except Exception:
        return None


def cache_del(key):
    with db() as c:
        c.execute("DELETE FROM catalog_cache WHERE key=?", (key,))


# Longest any reader keeps a row is 12 h (EPISODE_META_TTL); stale panel
# reads (anilist_entry_state_quick) take any age, so a week is plenty.
CACHE_KEEP = 7 * 24 * 3600


def cache_prune(now=None):
    """Drop catalog rows nobody reads any more (TODO 61: it never pruned)."""
    with db() as c:
        return c.execute("DELETE FROM catalog_cache WHERE ts < ?",
                         ((now or time.time()) - CACHE_KEEP,)).rowcount


def cache_put(key, payload):
    with db() as c:
        c.execute("INSERT INTO catalog_cache(key,payload,ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
                  (key, json.dumps(payload), time.time()))
