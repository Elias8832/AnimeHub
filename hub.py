#!/usr/bin/env python3

import hashlib
import json
import signal
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

import os
import pty
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import unicodedata


# Two location roots (PLAN v0.1: config replaces hardcoded constants):
# APP_DIR is where the code lives — bundled assets (web/, mpv/) resolve
# beside hub.py, so the install tree is self-contained. Per-user state
# (database, cache) lives in the XDG data dir, moved by config `data_dir`.
APP_DIR = Path(__file__).resolve().parent
WEB = APP_DIR / "web"
MPV = APP_DIR / "mpv"
SKIP_CREDITS_LUA = MPV / "skip-credits.lua"

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".m4v"}
MOVIE_RE = re.compile(r"^(?:movies?|films?)$", re.I)
SPECIAL_RE = re.compile(r"^(?:specials?|ova|oav)$", re.I)
PART_RE = re.compile(r"^part\s*(\d+)$", re.I)
EP_RE = re.compile(r"\bE(\d{1,4})\b", re.I)
SE_EP_RE = re.compile(r"\bS\d{1,3}\s*E(\d{1,4})\b", re.I)
SEP_NUM_RE = re.compile(r"^(?:ep(?:isode)?)\.?\s*(\d{1,4})", re.I)
# "<title> Episode 5.mp4" — the name ani-cli gives its downloads.
EPISODE_WORD_RE = re.compile(r"\bEpisode\s*(\d{1,4})\b", re.I)
DEFAULT_PORT = 8787

CONFIG_KEYS = ("library_root", "port", "data_dir", "window", "anilist_client_id", "upscale_banners")

# AnimeHub's public AniList OAuth client (redirect: http://localhost:8787/
# auth/callback). Client ids are public by design — no secret involved;
# users on a non-default port register their own and override via config.
DEFAULT_ANILIST_CLIENT_ID = 53857

# Chromium-family browser names for app-mode window launch (Decision 26),
# in preference order.
APP_BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable",
                "google-chrome", "brave", "microsoft-edge")


def _xdg_base(var, fallback):
    """The XDG base directory for the given variable; a relative or unset
    variable falls back to the standard home-relative location."""
    v = os.environ.get(var)
    if v and os.path.isabs(v):
        return Path(v)
    return Path.home() / fallback


def _default_data_dir():
    return _xdg_base("XDG_DATA_HOME", ".local/share") / "anime-hub"


def _default_config_path():
    return _xdg_base("XDG_CONFIG_HOME", ".config") / "anime-hub" / "config.json"


def config_path_from_argv(argv=None):
    """`--config PATH` on the command line, if given."""
    argv = sys.argv if argv is None else argv
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser()
    return None


def read_config(path=None):
    """Read the config file (PLAN v0.1: replaces the hardcoded constants).
    A missing file is normal — defaults keep the app usable with no library
    and no AniList account, both of which degrade to readable states
    (Decision 6). Unknown keys and invalid values warn and fall back to
    defaults instead of tracebacking on a stranger's config typo.

    Returns (cfg, warnings, path). cfg keys:
      library_root     Path|None   — the library folder to watch
      port             int         — server port
      anilist_client_id int|None  — OAuth client id (Decision 20 implicit grant)
      data_dir         Path|None   — where hub.db/cache live (default: XDG)
      window           str         — "app" | "browser" | "none" (Decision 26)

    anilist_username was removed (found live 2026-10-17: the README's
    example value pinned a stranger's public lists over Elias's connected
    account): the hub follows the connected token's account, period. A
    leftover key draws a deprecation warning and is ignored.
    """
    path = Path(path).expanduser() if path else _default_config_path()
    cfg = {"library_root": None, "port": DEFAULT_PORT,
           "data_dir": None, "window": "app", "anilist_client_id": None,
           "upscale_banners": False}
    warnings = []
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return cfg, warnings, path
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"Could not read config file {path}: {e} — using defaults.")
        return cfg, warnings, path
    if not isinstance(raw, dict):
        warnings.append(f"Config file {path} must contain a JSON object — using defaults.")
        return cfg, warnings, path

    def warn(key, expected):
        warnings.append(f"Config '{key}' ignored: expected {expected}, got {raw[key]!r}.")

    def path_key(key):
        if raw.get(key) in (None, ""):
            return
        if isinstance(raw[key], str):
            cfg[key] = Path(os.path.expanduser(raw[key]))
        else:
            warn(key, "a path string")

    path_key("library_root")
    path_key("data_dir")

    if raw.get("port") not in (None, ""):
        p = raw["port"]
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535:
            cfg["port"] = p
        else:
            warn("port", "an integer in 1–65535")

    if raw.get("window") not in (None, ""):
        w = raw["window"]
        if w in ("app", "browser", "none"):
            cfg["window"] = w
        else:
            warn("window", "'app', 'browser' or 'none'")

    if raw.get("anilist_client_id") not in (None, ""):
        cid = raw["anilist_client_id"]
        if isinstance(cid, int) and not isinstance(cid, bool) and cid > 0:
            cfg["anilist_client_id"] = cid
        else:
            warn("anilist_client_id", "a positive integer (the AniList client id)")

    if raw.get("upscale_banners") not in (None, ""):
        u = raw["upscale_banners"]
        if isinstance(u, bool):
            cfg["upscale_banners"] = u
        else:
            warn("upscale_banners", "true or false")
    if "open_browser" in raw:
        # TODO 34's boolean was replaced by 'window' the same day it was
        # added (never released) — point the user at the new key instead of
        # silently ignoring it.
        warnings.append("Config key 'open_browser' no longer exists — "
                        "use \"window\": \"app\" | \"browser\" | \"none\".")

    for key in raw:
        if key == "anilist_username":
            # Removed (found live 2026-10-17: its example value pinned a
            # stranger's public lists over the connected account). Warn with
            # the reason, not just "unknown key".
            warnings.append("Config key 'anilist_username' removed — the hub "
                            "follows your connected AniList account. Remove the key.")
        elif key not in CONFIG_KEYS and key != "open_browser":
            warnings.append(f"Unknown config key '{key}' ignored.")
    return cfg, warnings, path


def apply_config(cfg):
    """Bind the config to the module constants everything else reads.
    Re-derives the per-user paths from data_dir so a relocated data dir
    moves the database, cache and AniList state together (Decision 23.7:
    the identity model is root/config-aware from day one)."""
    global DATA, CACHE, POSTERS, EPIMGS, DB, LIBRARY, PORT
    global ANILIST_CACHE, WINDOW, ANILIST_TOKEN_FILE, ANILIST_CLIENT_ID, UPSCALE_BANNERS
    DATA = cfg["data_dir"] or _default_data_dir()
    CACHE = DATA / "cache"
    POSTERS = CACHE / "posters"
    EPIMGS = CACHE / "epimgs"
    DB = DATA / "hub.db"
    LIBRARY = cfg["library_root"]
    PORT = cfg["port"]
    WINDOW = cfg["window"]
    ANILIST_CACHE = DATA / "anilist_cache.json"
    # OAuth token lives with the other per-user state — outside the repo
    # (Decision 20 / READINESS A5); permissions tightened on every write.
    ANILIST_TOKEN_FILE = DATA / "anilist_token.json"
    # Opt-in banner upscaler (see store_show_meta): default off.
    UPSCALE_BANNERS = bool(cfg.get("upscale_banners"))
    # The bundled client id makes connect a two-click affair on a fresh
    # install (Decision 20's setup-tour shape). It is a public identifier,
    # not a secret — every authorize URL carries it anyway. Its registered
    # redirect is http://localhost:8787/auth/callback, so a non-default
    # port needs a config override with the user's own client id.
    ANILIST_CLIENT_ID = cfg.get("anilist_client_id") or DEFAULT_ANILIST_CLIENT_ID


CONFIG_PATH = config_path_from_argv()
CONFIG, CONFIG_WARNINGS, CONFIG_PATH = read_config(CONFIG_PATH)
apply_config(CONFIG)

ANILIST_URL = "https://graphql.anilist.co"
# The spine's cache query. genres + nextAiringEpisode landed with the
# round-4 UI rework (genre hints/tray, home airing panel, stats panels);
# old caches just lack the fields until the next refresh — additive.
ANILIST_USER_QUERY = """query($name:String){
User(name:$name){ name mediaListOptions{scoreFormat} statistics{anime{episodesWatched minutesWatched meanScore count}} favourites{anime(perPage:50){nodes{id}}} }
MediaListCollection(userName:$name, type:ANIME){
lists{ entries{ status progress score(format:POINT_10) score100: score(format:POINT_100) updatedAt media{ id status season episodes format duration startDate{year} genres nextAiringEpisode{ episode timeUntilAiring } coverImage{extraLarge} bannerImage title{english romaji} } } }
}}"""
MEDIA_FIELDS = """id title{romaji english native} format episodes duration averageScore seasonYear startDate{year}
coverImage{extraLarge} bannerImage description
relations{edges{relationType node{id format}}}
streamingEpisodes{title thumbnail}"""
MEDIA_QUERY_SEARCH = "query($search:String){Media(search:$search,type:ANIME){" + MEDIA_FIELDS + "}}"
MEDIA_QUERY_ID = "query($id:Int){Media(id:$id,type:ANIME){" + MEDIA_FIELDS + "}}"
MEDIA_QUERY_CANDIDATES = "query($search:String){Page(perPage:10){media(search:$search,type:ANIME){" + MEDIA_FIELDS + "}}}"
# One franchise entry (docs/franchise-page-plan.md): everything the franchise
# page draws for it, plus the typed relation edges the franchise is built
# from. tests/record_fixtures.py records the test fixtures with this query.
FRANCHISE_FIELDS = """id title{romaji english native} synonyms format status episodes duration averageScore
season seasonYear startDate{year month day} nextAiringEpisode{episode airingAt}
coverImage{extraLarge} bannerImage description
relations{edges{relationType node{id type format status startDate{year month day} title{romaji english}}}}
streamingEpisodes{title thumbnail}"""
FRANCHISE_QUERY_ID = "query($id:Int){Media(id:$id,type:ANIME){" + FRANCHISE_FIELDS + "}}"
# Relations that keep two entries on one franchise page. SPIN_OFF,
# ALTERNATIVE (FMA vs Brotherhood) and CHARACTER do not. OTHER joins only
# an entry of these formats whose title is this one's plus a season number
# (AniList links "Cyberpunk: Edgerunners 2" as OTHER, both ways).
FRANCHISE_RELATIONS = {"SEQUEL", "PREQUEL", "PARENT", "SIDE_STORY", "SUMMARY"}
# AniList's two sides of a link can disagree: Attack on Titan calls Junior
# High a SPIN_OFF, Junior High calls AoT its PARENT. Either side naming one
# of these keeps the two apart.
FRANCHISE_SEPARATE = {"SPIN_OFF", "ALTERNATIVE", "CHARACTER"}
FRANCHISE_OTHER_FORMATS = {"TV", "TV_SHORT", "ONA", "MOVIE"}
# What a franchise page holds: no music videos. None is a just-announced
# entry AniList hasn't given a format yet.
FRANCHISE_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "SPECIAL", "MOVIE", None}
EPISODE_META_TTL = 12 * 3600

_active = {}
_active_lock = threading.Lock()
# Launch/termination of playback sessions is one serialized transition
# (TODO 24): supersede the old mpv, spawn the new one, register the entry —
# never interleaved, so two concurrent play requests cannot leave two
# live mpv processes.
_launch_lock = threading.Lock()
# The most recent session that never started playing (died before the IPC
# socket appeared), surfaced additively in /api/now so the UI can explain
# a play click that produced nothing.
_session_error = {"ep_id": None, "error": None, "ts": 0.0}
# Metadata-fetch visibility (Elias, laptop-era finding): the UI must show
# what the background pass is doing — a guessing game between "not found"
# and "still loading" is not acceptable. pending/current/phase feed
# /api/library's meta_pending payload.
_meta_status = {"running": False, "pending": 0, "current": None, "phase": None}
MIN_VALID_SCAN = 10
# Last scan's health (TODO 23): "ok", "missing" (root not a directory),
# "partial" (some directories unreadable). "root" is "changed" when the configured
# root differs from the one the database was indexed under. Surfaced in the
# library payload so the UI can show readable states instead of nothing.
scan_state = {"state": "ok", "walk_errors": 0, "root": "ok"}


def get_setting(key):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# AniList automations (TODO 15, Decision 5): one key per automatic write,
# all settable in the settings tab and the setup tour's AniList step.
# Defaults follow Decision 5's consent shape: syncing progress of releases
# the user already tracks is the core promise of connecting (on), while
# creating NEW public list entries on the user's behalf (auto-add on first
# watch, Decision 5) needs an explicit opt-in (off).
AUTOMATION_DEFAULTS = {"auto_progress_sync": True, "auto_add_watch": False,
                       # Not writes, just switches kept beside them:
                       # watched marks read from AniList progress, and the
                       # VPN status icon / no-VPN warning for online episodes.
                       "anilist_watched": False, "vpn_icon": True, "vpn_warn": True}


def automation_enabled(key):
    default = AUTOMATION_DEFAULTS[key]
    raw = get_setting(key)
    return default if raw is None else raw == "1"


def set_automation(key, on):
    set_setting(key, "1" if on else "0")


VPN_IFACE_RE = re.compile(r"^(wg|tun|tap|ppp|ipsec|proton|pvpn|nordlynx|nordtun|mullvad|expressvpn|surfshark|cscotun|gpd|utun)", re.I)
VPN_IFACE_SKIP = re.compile(r"^(lo|tailscale|zt|docker|br-|veth|virbr|vboxnet|vmnet)", re.I)


def vpn_status():
    """Best-effort VPN check for the online-episode warning: an up network
    interface that is a tunnel — WireGuard/tun devices have no hardware
    address (ARPHRD_NONE, type 65534) or a VPN client's usual name. Mesh
    networks (Tailscale, ZeroTier) and container bridges don't count."""
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return {"active": False, "iface": None, "known": False}
    for n in names:
        if VPN_IFACE_SKIP.match(n):
            continue
        base = Path("/sys/class/net") / n
        try:
            state = (base / "operstate").read_text().strip()
            kind = (base / "type").read_text().strip()
        except OSError:
            continue
        if state == "down":
            continue
        if VPN_IFACE_RE.match(n) or kind == "65534":
            return {"active": True, "iface": n, "known": True}
    return {"active": False, "iface": None, "known": True}


def settings_payload():
    """TODO 15: the settings surface's read side — the automation toggles
    (Decision 5), the running config as plain values (paths as strings,
    never the token), scan health, and the detected tools the README
    promises. The library root is chosen in the UI's folder picker
    (Decision 32: /api/browse + /api/library-root, written back to the
    config file); adopting a changed root stays explicit (Decision 23.7)."""
    return {
        "auto_progress_sync": automation_enabled("auto_progress_sync"),
        "auto_add_watch": automation_enabled("auto_add_watch"),
        "anilist_watched": automation_enabled("anilist_watched"),
        "vpn_icon": automation_enabled("vpn_icon"),
        "vpn_warn": automation_enabled("vpn_warn"),
        "tour_done": get_setting("tour_done") == "1",
        "library_root": str(LIBRARY) if LIBRARY else None,
        "data_dir": str(DATA),
        "config_path": str(CONFIG_PATH),
        "port": PORT,
        "window": WINDOW,
        "scan": dict(scan_state),
        "tools": {t: shutil.which(t) for t in ("mpv", "ani-cli", "upscayl")},
        # ani-cli bridge preferences: quality + default audio for new links.
        "stream": {**stream_prefs(), "version": anicli_version(),
                   "qualities": list(STREAM_QUALITIES)},
    }


def adopt_root():
    """Decision 23.7: adopting a changed library root is an explicit step,
    never a silent scan side effect. Marks the configured root as the
    indexed one, which lifts the root-changed tombstone guard so the next
    scan (GET /api/library re-scans every call) records the old root's rows
    as gone."""
    if LIBRARY is None:
        return {"error": "No library folder is configured.", "ok": False}
    set_setting("library_root", str(LIBRARY))
    scan_state["root"] = "ok"
    return {"ok": True, "library_root": str(LIBRARY)}


def _user_dir(path):
    """An absolute, normalized folder path from user input. normpath keeps
    the user's own spelling (no symlink resolution: /mnt/Storage stays
    /mnt/Storage even if it links elsewhere). Raises ValueError with a
    message meant for the UI."""
    p = os.path.expanduser(path.strip())
    if not os.path.isabs(p):
        raise ValueError("Enter a full folder path, e.g. /home/you/Anime.")
    p = Path(os.path.normpath(p))
    if not p.is_dir():
        raise ValueError(f"Not a folder: {p}")
    return p


def browse_dirs(path=None):
    """The library folder picker's read side (Decision 32): one level of
    subfolders so the UI can walk to a folder. Folders only, hidden ones
    skipped. The hub only answers its own localhost page (check_local), so
    this lists nothing the user's file manager wouldn't. Places are the
    usual Linux mount spots plus home, for drives outside $HOME."""
    if path in (None, ""):
        base = LIBRARY if LIBRARY and LIBRARY.is_dir() else Path.home()
    else:
        base = _user_dir(path)
    try:
        with os.scandir(base) as it:
            dirs = sorted((e.name for e in it
                           if not e.name.startswith(".") and e.is_dir()),
                          key=str.casefold)
    except PermissionError:
        raise ValueError(f"Can't open {base}: permission denied.")
    except OSError as e:
        raise ValueError(f"Can't open {base}: {e.strerror or e}.")
    user = os.environ.get("USER") or Path.home().name
    places = [("Home", Path.home()), ("/mnt", Path("/mnt")), ("/media", Path("/media")),
              (f"/run/media/{user}", Path("/run/media") / user)]
    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base else None,
        "dirs": [{"name": d, "path": str(base / d)} for d in dirs],
        "places": [{"label": label, "path": str(p)} for label, p in places if p.is_dir()],
    }


def write_config_key(key, value):
    """Set one key in the config file, keeping every other key (unknown
    ones included) as the user wrote them. A missing file is created; an
    unreadable one is refused rather than overwritten — it is the user's
    file. Written via a temp file + rename so a crash never truncates it."""
    path = CONFIG_PATH
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Could not read the config file {path} ({e}) — fix or remove it first.")
    if not isinstance(raw, dict):
        raise ValueError(f"The config file {path} must contain a JSON object — fix or remove it first.")
    raw[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n")
    os.replace(tmp, path)


def set_library_root(path, adopt=False):
    """Decision 32: the library folder is chosen in the UI and written back
    to the config file's library_root, effective immediately (the next
    /api/library call scans it). Decision 23.7 still holds: switching away
    from an indexed root only tombstones the old one with adopt=True — the
    UI sends that after the user confirmed the switch; without it the scan
    flags the root as changed and the "Connect folder" step remains."""
    global LIBRARY
    p = _user_dir(path)
    write_config_key("library_root", str(p))
    LIBRARY = p
    CONFIG["library_root"] = p
    if adopt:
        set_setting("library_root", str(p))
        scan_state["root"] = "ok"
    return {"ok": True, "library_root": str(p)}


CANONICAL_TS = "%Y-%m-%d %H:%M:%S"


def utc_stamp():
    """The one timestamp format the app writes: UTC 'YYYY-MM-DD HH:MM:SS' —
    SQLite's own datetime('now') form, so every string comparison and sort in
    SQL and Python agrees (READINESS B5)."""
    return datetime.now(timezone.utc).strftime(CANONICAL_TS)


def normalize_timestamp(value):
    """Map any historical timestamp representation onto the canonical UTC
    form. ISO strings (T separator, optional offset/Z/microseconds) convert;
    values that parse as nothing recognizable are kept verbatim — a migration
    must never silently drop data. Returns (normalized, changed)."""
    if not value:
        return value, False
    v = value.strip()
    try:
        return datetime.strptime(v, CANONICAL_TS).strftime(CANONICAL_TS), v != value
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return value, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(CANONICAL_TS), True


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
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
            and LIBRARY is not None):
        c.execute("INSERT INTO settings(key,value) VALUES('library_root',?)", (str(LIBRARY),))


def migration_4(c):
    """Normalize timestamps (READINESS B5, TODO 3): the progress loop wrote
    ISO strings ('...T12:00:00+00:00') while watched-completion wrote SQL
    datetime('now') strings ('... 12:00:00'); mixed formats sort wrong on the
    same day ('T' > ' ') and break the this-week cutoff. All last_played (and
    tombstone) values move to the canonical UTC 'YYYY-MM-DD HH:MM:SS' form,
    which every writer emits from now on."""
    for eid, lp in c.execute(
            "SELECT id, last_played FROM episodes WHERE last_played IS NOT NULL").fetchall():
        norm, changed = normalize_timestamp(lp)
        if changed:
            c.execute("UPDATE episodes SET last_played=? WHERE id=?", (norm, eid))
    for kind, key, ts in c.execute(
            "SELECT kind, key, ts FROM tombstones WHERE ts IS NOT NULL").fetchall():
        norm, changed = normalize_timestamp(ts)
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


MIGRATIONS = [migration_1, migration_2, migration_3, migration_4, migration_5, migration_6, migration_7,
              migration_8, migration_9, migration_10, migration_reserved, migration_reserved,
              migration_reserved, migration_reserved, migration_15, migration_16, migration_17]


def snapshot_before(target_version):
    """WAL-safe backup (SQLite backup API) taken before applying a migration,
    so a failed migration is recoverable from the pre-change state. Skipped
    when nothing user-owned exists yet (fresh schema, zero rows)."""
    dest = DB.parent / f"hub.db.backup-v{target_version - 1}"
    if dest.exists():
        return
    src = sqlite3.connect(DB)
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
    DB.parent.mkdir(parents=True, exist_ok=True)
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


def load_overrides():
    f = DATA / "overrides.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    return {}


def parse_ep_number(name):
    """Episode number from a filename: S01E02-style first (more specific),
    then a standalone E-token, else None. Fixes the S01E02 renumbering trap
    (TODO 23): plain EP_RE never matched "S01E02" — no word boundary between
    the season digits and E — so files fell back to lexical order numbering."""
    m = SE_EP_RE.search(name)
    if m:
        return int(m.group(1))
    m = EP_RE.search(name)
    if m:
        return int(m.group(1))
    m = EPISODE_WORD_RE.search(name)
    if m:
        return int(m.group(1))
    return None


def scan_library():
    shows = {}
    scan_state.update(state="ok", walk_errors=0, root="ok")
    if LIBRARY is None:
        # No library configured (PLAN v0.1 config work): a readable state,
        # not a traceback — the UI explains how to configure one.
        scan_state["state"] = "unconfigured"
        return []
    if not LIBRARY.is_dir():
        scan_state["state"] = "missing"
        return []
    stored_root = get_setting("library_root")
    if stored_root and stored_root != str(LIBRARY):
        # Root-aware identity (Decision 23.7): the database was indexed under
        # a different root. Flag it — the config flow (step 3) must adopt a
        # root explicitly; a scan never silently tombstones the old one.
        scan_state["root"] = "changed"

    def walk_error(err):
        scan_state["walk_errors"] += 1

    for dirpath, dirnames, filenames in os.walk(LIBRARY, onerror=walk_error):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        videos = sorted(f for f in filenames if Path(f).suffix.lower() in VIDEO_EXTS)
        if not videos:
            continue
        rel = Path(dirpath).relative_to(LIBRARY)
        if not rel.parts:
            # Videos loose in the root: used to raise IndexError (parts was
            # empty). Keep them watchable as one-file shows instead; the
            # show id is namespaced "loose:" so it can never collide with a
            # real folder show later claiming the same name.
            for v in videos:
                n = parse_ep_number(v) or 1
                ep_rel = str(Path(v))
                vm = re.search(r"\(([^)]+)\)", v)
                ver = {"path": ep_rel, "label": vm.group(1) if vm else "default"}
                show_id = hashlib.sha1(f"loose:{v}".encode()).hexdigest()[:12]
                entry = shows.setdefault(
                    show_id,
                    {"id": show_id, "path": ep_rel, "title": Path(v).stem,
                     "collection": None, "kind": "show", "seasons": []},
                )
                sea = entry["seasons"][0] if entry["seasons"] else None
                if sea is None:
                    sea = {"key": 1, "season": 1, "label": "Season 1", "episodes": []}
                    entry["seasons"].append(sea)
                sea["episodes"].append(
                    {"id": hashlib.sha1(ep_rel.encode()).hexdigest()[:16], "rel": ep_rel,
                     "number": n, "files": [ver], "season": 1, "season_label": "Season 1"}
                )
            continue
        parts = list(rel.parts)
        show_parts = []
        season = None
        label = None
        kind = "show"
        movie_leaf = None
        for p in parts:
            if MOVIE_RE.match(p):
                kind = "movie"
                continue
            if kind == "movie":
                movie_leaf = p
                continue
            m = re.match(r"^season\s*(\d+)$", p, re.I)
            if m:
                season = int(m.group(1))
                label = None
                continue
            m = PART_RE.match(p)
            if m:
                label = f"Part {m.group(1)}"
                continue
            if SPECIAL_RE.match(p):
                season = 0
                label = None
                continue
            if season is not None:
                label = p
            else:
                show_parts.append(p)
        if kind == "movie":
            title = movie_leaf or f"{parts[0]} Movie"
            collection = parts[0] if len(parts) > 2 and parts[0] != title else None
            show_rel = "/".join(parts)
            season_key = 0
            season = 0
            season_label = "Movies"
        else:
            if not show_parts:
                show_parts = parts[:1]
            show_rel = "/".join(show_parts)
            title = show_parts[-1]
            collection = parts[0] if len(parts) > 1 and parts[0] != title else None
            if season is None:
                season = 1
            season_key = season
            season_label = label if label else ("Specials" if season == 0 else f"Season {season}")
        show_id = hashlib.sha1(show_rel.encode()).hexdigest()[:12]
        entry = shows.setdefault(
            show_id,
            {"id": show_id, "path": show_rel, "title": title, "collection": collection, "kind": kind, "seasons": []},
        )
        sea = next((s for s in entry["seasons"] if s["key"] == season_key and s["label"] == season_label), None)
        if sea is None:
            sea = {"key": season_key, "season": season, "label": season_label, "episodes": []}
            entry["seasons"].append(sea)
        for idx, v in enumerate(videos):
            n = parse_ep_number(v)
            if n is None:
                n = idx + 1
            ep_rel = str(rel / v)
            vm = re.search(r"\(([^)]+)\)", v)
            ver = {"path": ep_rel, "label": vm.group(1) if vm else "default"}
            existing = next((e for e in sea["episodes"] if e["number"] == n), None)
            if existing is None:
                sea["episodes"].append(
                    {"id": hashlib.sha1(ep_rel.encode()).hexdigest()[:16], "rel": ep_rel, "number": n,
                     "files": [ver], "season": season, "season_label": season_label}
                )
            else:
                existing["files"].append(ver)
    show_list = list(shows.values())
    for s in show_list:
        # Authoritative season order (TODO 24): the same rank the UI shows,
        # so autoplay's "next" is the section the user sees next.
        s["seasons"].sort(key=season_rank)
        for sea in s["seasons"]:
            seen = set()
            for ep in sea["episodes"]:
                ep["files"] = sorted(
                    ep["files"],
                    key=lambda f: (bool(re.search(r"\([^)]+\)", Path(f["path"]).name)), f["path"]),
                )
                ep["rel"] = ep["files"][0]["path"]
                ep["id"] = hashlib.sha1(ep["rel"].encode()).hexdigest()[:16]
                while ep["number"] in seen:
                    ep["number"] += 1
                seen.add(ep["number"])
            sea["episodes"].sort(key=lambda x: x["number"])
    show_list.sort(key=lambda x: (x["collection"] or x["title"]).lower())
    if scan_state["walk_errors"]:
        scan_state["state"] = "partial"
    elif stored_root is None and show_list:
        # First clean scan of a root on a database that never recorded one
        # (fresh install configured after migration 3 ran): record it, or
        # the changed-root guard (Decision 23.7) could never arm.
        set_setting("library_root", str(LIBRARY))
    return show_list


def eps_of(show):
    for sea in show["seasons"]:
        for ep in sea["episodes"]:
            yield ep


def img_url(name):
    if not name:
        return None
    p = POSTERS / os.path.basename(name)
    try:
        return f"{name}?v={int(p.stat().st_mtime)}"
    except OSError:
        return name


def tombstone_counts():
    """Counts per kind for the library payload (Decision 17.3 surfaced)."""
    with db() as c:
        counts = {kind: n for kind, n in
                  c.execute("SELECT kind, COUNT(*) FROM tombstones GROUP BY kind")}
    return {"episode": counts.get("episode", 0), "show": counts.get("show", 0),
            "season": counts.get("season", 0)}


def ep_label(sea, ep):
    """A stable human label for history rows that must survive the file's
    removal (TODO 25): 'S1E3', or 'OVA · E2' for non-season folders."""
    lab = sea.get("label") or ""
    n = ep.get("number")
    if n is None:
        return lab or None
    if lab == "Episodes":  # an online release's own page
        return f"E{n}"
    if lab.startswith("Season"):
        return f"S{sea['season']}E{n}"
    return f"{lab} · E{n}" if lab else f"E{n}"


def merge_db(scan):
    with db() as c:
        rows = {r[0]: r[1:] for r in c.execute("SELECT id, watched, position, duration, last_played FROM episodes")}
        meta = {r[0]: r for r in c.execute(
            "SELECT show_id, anilist_id, title, year, score, overview, poster, banner, episodes_meta, format, fetched_at, episode_thumbs FROM shows_meta")}
        season_rows = {r[0] for r in c.execute("SELECT DISTINCT show_id FROM season_thumbs")}
    known = {ep["id"] for s in scan for ep in eps_of(s)}
    stamps = []
    if len(scan) >= MIN_VALID_SCAN:
        # Root-changed guard (TODO 23, Decision 23.7): a scan under a root
        # different from the one the database was indexed under must not
        # tombstone anything — the id space just moved. The old root's facts
        # stay intact until the new root is adopted explicitly (config flow,
        # step 3). A missing root simply produces an empty scan, which the
        # MIN_VALID_SCAN floor already shields.
        if scan_state.get("root") != "changed":
            now = utc_stamp()
            gone_shows = set(meta) - {s["id"] for s in scan}
            with db() as c:
                # Tombstones, not deletes (Decision 17.3): rows that fell out of
                # a valid scan stay in place and are marked gone. A re-download
                # restores the watch state untouched, and a moved/renamed file
                # keeps its facts until re-matched.
                # Streamed episodes ("stream:") never come from a scan.
                stale = [eid for eid in set(rows) - known
                         if not eid.startswith(("online:", "stream:"))]
                # DO NOTHING keeps ts = when the row first went missing; this
                # runs on every library poll and must not keep re-stamping it.
                ins = ("INSERT INTO tombstones(kind,key,ts) VALUES(?,?,?) "
                       "ON CONFLICT(kind,key) DO NOTHING")
                scan_ids = {s["id"] for s in scan}
                c.executemany(ins, [("episode", eid, now) for eid in stale])
                c.executemany(ins, [("show", sid, now) for sid in gone_shows])
                c.executemany(ins, [("season", sid, now) for sid in set(season_rows) - scan_ids])
                # IDs that are back in the scan clear their tombstone again —
                # only the ones actually tombstoned, not one DELETE per episode.
                back = {("episode", eid) for eid in known & set(rows)} | \
                       {("show", sid) for sid in scan_ids & set(meta)}
                have = set(c.execute("SELECT kind, key FROM tombstones "
                                     "WHERE kind IN ('episode','show')").fetchall())
                c.executemany("DELETE FROM tombstones WHERE kind=? AND key=?", have & back)
    for s in scan:
        m = meta.get(s["id"])
        if m:
            s["meta"] = {"anilist_id": m[1], "title": m[2], "year": m[3], "score": m[4], "overview": m[5],
                         "episodes_meta": json.loads(m[8]) if m[8] else None, "format": m[9],
                         "episode_thumbs": json.loads(m[11]) if m[11] else None}
            s["poster"] = img_url(m[6])
            s["banner"] = img_url(m[7])
        else:
            s["meta"] = None
            s["poster"] = None
            s["banner"] = None
        for sea in s["seasons"]:
            for ep in sea["episodes"]:
                r = rows.get(ep["id"])
                ep["watched"] = bool(r[0]) if r else False
                ep["position"] = r[1] if r else 0
                ep["duration"] = r[2] if r else 0
                ep["last_played"] = r[3] if r else None
                if r is not None:
                    # Identity stamp (TODO 25): a played episode learns which
                    # show/label it belongs to, so its history row stays
                    # human-readable even after the file leaves the disk.
                    stamps.append((ep["id"], s["id"], ep_label(sea, ep)))
    # Which entry each season is, and its stills (phase 3): needs the match
    # (meta) set above.
    attach_releases(scan)
    with db() as c:
        c.executemany(
            """INSERT INTO episodes(id,show_id,label) VALUES(?,?,?)
               ON CONFLICT(id) DO UPDATE SET show_id=excluded.show_id, label=excluded.label""",
            stamps)


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
        cached = json.loads(ANILIST_CACHE.read_text())
        if cached.get("user"):
            return cached
    except Exception:
        pass
    return None


# --- AniList entry state (TODO 9): authenticated per-release reads + edits ---

ENTRY_STATE_QUERY = """query($id:Int,$fmt:ScoreFormat){
Media(id:$id,type:ANIME){
 id format episodes popularity favourites isFavourite
 nextAiringEpisode{ airingAt episode timeUntilAiring }
 studios(isMain:true){ nodes{ name } }
 mediaListEntry{ status score(format:$fmt) progress repeat startedAt{year month day} completedAt{year month day} }
}
Viewer{ mediaListOptions{scoreFormat} }
}"""

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
    with db() as c:
        c.execute("DELETE FROM catalog_cache WHERE key=?", (key,))


def anilist_entry_state(media_id):
    """Live AniList release state for the editing surface (TODO 9): the
    user's list entry, score in THEIR scoring format, favourite flag, plus
    the read-only info Decision 10 wants (popularity, studios, airing
    countdown). Authenticated — mediaListEntry/isFavourite need the Viewer.
    Cached ~30 s so panel re-renders don't hammer the API."""
    key = "entry-state:v1:%d" % media_id
    cached = cache_get(key, ttl=30)
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
        "is_favourite": bool(m.get("isFavourite")),
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
    cache_put(key, out)
    return out


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
    flag, toggle only when it differs from the desired boolean, verify by
    re-reading. Returns fresh state, or an {"error": ...} dict."""
    try:
        state = anilist_entry_state(media_id)
    except AniListAuthError as e:
        return {"error": e.reason}
    if state is None:
        return {"error": "AniList has no such media"}
    if state["is_favourite"] == bool(want):
        return state
    q = "mutation($id:Int){ToggleFavourite(animeId:$id){anime(perPage:1){pageInfo{total}}}}"
    # Same contract as anilist_entry_save: auth/validation errors raise.
    if anilist_mutation(q, {"id": media_id}) is None:
        return {"error": "AniList rejected the update"}
    _cache_del("entry-state:v1:%d" % media_id)
    try:
        fresh = anilist_entry_state(media_id)
    except AniListAuthError as e:
        return {"error": e.reason}
    if fresh and fresh["is_favourite"] != bool(want):
        return {"error": "AniList did not confirm the change"}
    return fresh


def log(msg):
    print("anime-hub: %s" % msg, file=sys.stderr, flush=True)


def spine_build(entries, scan, streamed=frozenset(), online=False, missed=frozenset(), favs=frozenset()):
    """Merge the user's AniList lists with the local library — Decision 4's
    spine, release-first (Decision 19): one card per AniList entry, with a
    source-capability badge (installed / partial / not installed) derived
    from how much of that release exists on disk, and local shows that
    aren't on any list surfaced as `untracked` (matched-but-unlisted shows
    land there too — they are part of the universe without being tracked).
    With ani-cli (`online`) a release with nothing on disk is "online" —
    linked already (`streamed`) or matched on first need — unless ani-cli
    has no match for it (`missed`). Counts are of disk episodes only (scan
    may carry merged online episodes).
    Pure function: entries come from the cache, scan from scan_library()."""
    by_media = {}
    for s in scan:
        # Per entry (release_units): a multi-season folder feeds each entry
        # its seasons map to its own disk count, never the folder total.
        # A folder nothing maps for yet (its franchise isn't built) still
        # counts for its own match, as a whole.
        units = [(u["media_id"], [ep for ep, _ in u["eps"]]) for u in release_units(s)] or \
            [((s.get("meta") or {}).get("anilist_id"), list(eps_of(s)))]
        for aid, held in units:
            if not aid:
                continue
            eps = sum(1 for ep in held if disk_ep(ep))
            cur = by_media.get(aid)
            if cur is None or eps > cur[1]:
                by_media[aid] = (s, eps)
    listed = {e["id"] for e in entries}
    out = []
    for e in entries:
        loc = by_media.get(e["id"])
        installed = loc[1] if loc else 0
        if loc and e.get("episodes") and installed >= e["episodes"]:
            cap = "installed"
        elif installed:
            cap = "partial"
        elif e["id"] in streamed or (online and e["id"] not in missed):
            cap = "online"
        else:
            cap = "none"
        out.append({"id": e["id"], "title": e.get("title"), "status": e.get("status"),
                    "progress": e.get("progress") or 0, "score": e.get("score") or 0,
                    "episodes": e.get("episodes"), "format": e.get("format"),
                    "year": e.get("year"), "airing": e.get("airing"),
                    "genres": e.get("genres") or [],
                    "next_episode": e.get("next_episode"),
                    "poster": e.get("poster"), "banner": e.get("banner"),
                    "updated": e.get("updated"),
                    "favourite": e["id"] in favs,
                    "capability": cap,
                    "installed": installed,
                    "local_show_id": loc[0]["id"] if loc else None,
                    "stream_show_id": f"aid:{e['id']}" if e["id"] in streamed else None})
    # Untracked = not on any of the user's lists. A second folder matched to
    # an already-listed release is a fragment of a tracked release, not an
    # untracked show — the card deep-links to the fuller one.
    untracked = [s["id"] for s in scan
                 if (s.get("meta") or {}).get("anilist_id") not in listed
                 and not any(u["media_id"] in listed for u in release_units(s))]
    return {"entries": out, "untracked": untracked, "mode": "anilist"}


def apply_anilist_watched(shows, entries):
    """Settings → Advanced AniList → "Watched marks from AniList": episodes
    up to their entry's progress read as watched, later ones as unwatched.
    Display only — hub.db keeps the local marks, so switching back restores
    them. Per entry (release_units), with the same fit rule as progress
    sync, so each season of a folder follows its own entry."""
    prog = {e["id"]: (e.get("progress") or 0, e.get("episodes")) for e in entries}
    for s in shows:
        for u in release_units(s):
            if u["media_id"] not in prog:
                continue
            done, total = prog[u["media_id"]]
            if not unit_fits(u, total):
                continue
            for ep, n in u["eps"]:
                ep["watched"] = n <= done


def spine_favourites(cached):
    """AniList favourite ids for the spine: the cached list's favourites,
    overridden by in-app edits made after that cache was fetched, so a heart
    toggled on a show page shows on its card before the next list refresh."""
    favs = set(cached.get("favourites") or [])
    ts = cached.get("_ts")
    if not ts:
        return favs
    since = datetime.fromtimestamp(ts, timezone.utc).strftime(CANONICAL_TS)
    for ref, cur in curation_all().items():
        if not ref.startswith("aid:") or (cur.get("updated_at") or "") <= since:
            continue
        mid = int(ref[4:])
        if cur.get("favourite"):
            favs.add(mid)
        else:
            favs.discard(mid)
    return favs


def curation_all():
    """Every curation row in one read, keyed by ref — the local spine's
    overlay source (one query, not one per show)."""
    with db() as c:
        return {r[0]: {"status": r[1], "score": r[2], "favourite": r[3],
                       "rewatches": r[4], "started_at": r[5],
                       "finished_at": r[6], "updated_at": r[7]}
                for r in c.execute(
                    "SELECT ref,status,score,favourite,rewatches,"
                    "started_at,finished_at,updated_at FROM curation")}


def spine_build_local(scan, curations, streams=()):
    """Decision 6's no-AniList mode: the SAME spine shape AniList mode
    renders, driven purely by local watch state. Entries are disk shows
    only — sections derive from hub.db progress, with a curation status
    the user set explicitly winning over the derivation (Decision 18.5:
    no-AniList mode keeps manually-edited curation). Everything here is
    installed by definition, so the UI's source filter locks on — except
    for online episodes (ani-cli): disk shows may carry their release's
    online episodes ("partial" then), and online-only shows (`streams`)
    join once watched — browsing a page must not add it to the library.
    Pure function: scan from scan_library(), curations from curation_all()."""
    out = []
    online = [x for x in streams if any(ep.get("last_played") or ep.get("watched") for ep in eps_of(x))]
    for s in list(scan) + online:
        eps = [ep for sea in s["seasons"] for ep in sea["episodes"]]
        aid = (s.get("meta") or {}).get("anilist_id")
        cur = curations.get("aid:%d" % aid if aid else "sid:%s" % s["id"]) or {}
        watched = [ep for ep in eps if ep.get("watched")]
        started = bool(watched) or any(ep.get("last_played") for ep in eps)
        total = len(eps)
        status = cur.get("status")
        if status not in ENTRY_STATUS:
            status = ("COMPLETED" if total and len(watched) == total
                      else "CURRENT" if started else "PLANNING")
        # Recently-updated sort feeds off the newest activity: the last
        # playback stamp or the last curation edit, whichever is newer.
        last = max([ep["last_played"] for ep in eps if ep.get("last_played")]
                   + [cur.get("updated_at") or ""])
        try:
            updated = datetime.strptime(
                last, CANONICAL_TS).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            updated = 0
        meta = s.get("meta") or {}
        on_disk = sum(1 for ep in eps if disk_ep(ep))
        out.append({"id": s["id"], "title": s["title"],
                    "status": status, "progress": len(watched),
                    "score": cur.get("score") or 0, "episodes": total,
                    "format": meta.get("format") or
                              ("MOVIE" if s.get("kind") == "movie" else None),
                    "year": meta.get("year"), "airing": None,
                    "genres": [], "next_episode": None,
                    "poster": s.get("poster"), "banner": s.get("banner"),
                    "updated": updated,
                    "favourite": bool(cur.get("favourite")),
                    "capability": ("installed" if on_disk == total else
                                   "partial" if on_disk else "online"),
                    "installed": on_disk,
                    "media_id": aid,
                    "local_show_id": s["id"]})
    return {"entries": out, "untracked": [], "mode": "local"}


def full_library():
    scan = scan_library()
    merge_db(scan)
    # ani-cli bridge: a linked release's online episodes join its disk
    # folder's show; releases without a folder are shows of their own
    # ("aid:<id>"), carried beside the scan. merge_db ran first, so scan
    # health, matching and tombstones stay about the disk.
    links = stream_links_all()
    streams = stream_shows(scan, links)
    # Spine (TODO 8, Decision 4): AniList lists merged with the library —
    # from the cache only, so the frequent library poll never waits on the
    # network. Absent until the first /api/anilist fetch or boot warm-up.
    # No-AniList mode (TODO 17, Decision 6): with no usable token the same
    # spine shape is built from local watch state instead — the mode switch
    # is in place, both directions, and costs no network at all.
    connected = anilist_connected()
    cached = anilist_cache_read() if connected else None
    if cached is None and connected:
        # Fresh cache expired (warm-up failed, long session without an
        # AniList tab visit, network down): last-known lists beat a silent
        # local mode (Decision 6). The background refresh loop and the next
        # /api/anilist visit re-freshen it.
        cached = anilist_cache_stale()
    if cached:
        spine = spine_build(cached["entries"], scan, {link["media_id"] for link in links},
                            bool(anicli_path()), stream_missed_ids(), spine_favourites(cached))
    elif not connected:
        spine = spine_build_local(scan, curation_all(), streams)
    else:
        # AniList mode with no cache at all (first ever connect): no spine
        # yet (the boot warm-up fills it); the UI renders the plain local
        # grid meanwhile.
        spine = None
    if spine:
        tag_franchises(spine["entries"])
    if cached and automation_enabled("anilist_watched"):
        apply_anilist_watched(scan + streams, cached["entries"])
    continue_row = []
    history = []
    for s in scan + streams:
        for ep in eps_of(s):
            if ep["last_played"]:
                history.append(ep | {"show": s["title"], "show_id": s["id"], "poster": s["poster"]})
            if not ep["watched"] and ep["position"] > 30 and ep["duration"] and ep["position"] / ep["duration"] < 0.92:
                continue_row.append(ep | {"show": s["title"], "show_id": s["id"], "poster": s["poster"]})
    continue_row.sort(key=lambda e: e["last_played"] or "", reverse=True)
    history.sort(key=lambda e: e["last_played"] or "", reverse=True)
    unwatched = {s["id"]: sum(1 for ep in eps_of(s) if not ep["watched"]) for s in scan}
    with db() as c:
        mins_row = c.execute(
            """SELECT COALESCE(SUM(CASE WHEN watched=1 THEN duration ELSE 0 END),0),
                      COALESCE(SUM(CASE WHEN watched=0 AND last_played IS NOT NULL THEN position ELSE 0 END),0)
               FROM episodes"""
        ).fetchone()
        minutes = int((mins_row[0] + mins_row[1]) // 60)
        eps_watched = c.execute("SELECT COUNT(*) FROM episodes WHERE watched=1").fetchone()[0]
        this_week = c.execute(
            "SELECT COUNT(*) FROM episodes WHERE watched=1 AND last_played >= datetime('now','-7 days')"
        ).fetchone()[0]
    stats = {"episodes_watched": eps_watched, "minutes": minutes, "this_week": this_week}
    with _active_lock:
        now = None
        if _active:
            a = next(iter(_active.values()))
            now = {"episode_id": a["ep_id"], "show": a["show_title"], "label": a["label"],
                   "percent": a.get("percent") or 0, "loading": bool(a.get("loading"))}
    return {"shows": scan, "continue": continue_row[:20], "history": history,
            "vpn": vpn_status(),
            # Legacy fields kept as empty lists since the streaming strip
            # (TODO 18, Decision 18.5). Streamed episodes ride in
            # continue/history above; their shows are `streams`.
            "continue_online": [], "history_online": [],
            "streams": streams, "stream_ready": bool(anicli_path()),
            # Scan health (TODO 23): state = ok/partial/missing/unconfigured;
            # root = ok/changed (configured root differs from the indexed one).
            "scan": dict(scan_state),
            # Tombstones (Decision 17.3): counts so the UI can hint at
            # "returned" rows instead of silently losing history context.
            "tombstones": tombstone_counts(),
            # Unmatched shows (TODO 28 minimal repair): local shows with no
            # shows_meta row — the validated matcher could not confidently
            # match them (or the user cleared a wrong match). Surfaced in
            # the library, fixable via the detail page's manual match.
            "unmatched": [s["title"] for s in scan if not s.get("meta")],
            "spine": spine,
            "unwatched": unwatched, "stats": stats, "now": now,
            # Metadata-fetch visibility (Elias): additive — the UI shows
            # what the background pass is working on, or that it's idle.
            "meta_pending": _meta_status["pending"],
            "meta_running": _meta_status["running"],
            "meta_current": _meta_status["current"],
            "meta_phase": _meta_status["phase"]}


def episode_by_id(eid):
    """(show, episode) through the same merged view the UI gets: a disk
    show carries its online episodes, so autoplay walks from the last file
    into the first online episode. A "stream:" id may also name an episode
    on disk by now (that episode is returned) or one of a release nobody
    linked yet (its page's episode)."""
    scan, streams = library_shows()  # merge_db attaches the watch facts
    for s in scan + streams:
        for ep in eps_of(s):
            if ep["id"] == eid:
                return s, ep
    if eid.startswith("stream:"):
        s = release_show(stream_media_id(eid))
        for ep in eps_of(s) if s else ():
            if ep["id"] == eid:
                return s, ep
    return None, None


def media_by_id(show_id):
    """The local show container with this id (sibling of episode_by_id at
    the show level) — used by bulk marks (TODO 6); the watch facts ride
    along (merge_db) so AniList sync sees the real watched state.
    "aid:<id>" is a release's online episodes (the ani-cli bridge)."""
    scan, streams = library_shows()
    return next((s for s in scan + streams if s["id"] == show_id), None)


def _completion_event(media_id, title):
    """Record a show-completion event (TODO 9, Decision 25): one-shot popup
    keyed (media_id, completion count) — rewatches bump the count and are
    NEW events; dismissed ones never re-appear. The newest unseen event is
    served through /api/now."""
    with db() as c:
        row = c.execute("SELECT COALESCE(MAX(count),0) FROM completion_events"
                        " WHERE media_id=?", (media_id,)).fetchone()
        count = row[0] + 1
        c.execute("INSERT INTO completion_events(media_id,count,seen,ts)"
                  " VALUES(?,?,0,?)", (media_id, count, utc_stamp()))
    with _completion_lock:
        _completion_pending.append({"media_id": media_id, "count": count,
                                    "title": title})


def _pending_completion():
    with _completion_lock:
        return dict(_completion_pending[-1]) if _completion_pending else None


def _dismiss_completion(media_id, count):
    with db() as c:
        c.execute("UPDATE completion_events SET seen=1 WHERE media_id=? AND count=?",
                  (media_id, count))
    with _completion_lock:
        _completion_pending[:] = [e for e in _completion_pending
                                  if not (e["media_id"] == media_id
                                          and e["count"] == count)]


_completion_lock = threading.Lock()
_completion_pending = []


def anilist_track_show(s, manual=False, only=None):
    """Sync one local show against the cached AniList state — Decision 21's
    highest-watched progress plus Decision 5's consent-gated auto-add, per
    entry (release_units): each mapped season, or part of one, syncs to its
    own entry with the entry's own episode numbers; unmapped seasons stay
    local. All writes ride the outbox (TODO 14): offline/backoff/dedupe
    handled once. Compares against the cache only — never a synchronous
    read — and skips silently when it's cold; the next watched episode
    catches up. An entry's files may not exceed its total (unit_fits).
    manual=True (TODO 6 bulk marks): an explicit user click syncs even when
    the auto_progress_sync automation is off (Decision 27 — a button you
    pressed is you acting, not an automation); the auto-add consent toggle
    still gates unlisted entries. `only` (episode ids) limits the sync to
    the entries holding those episodes — playing S3E5 never touches S1's
    entry. Returns the enqueued patch (one entry), {media id: patch}
    (several), or None."""
    if not anilist_connected():
        return None
    add = automation_enabled("auto_add_watch")
    if not manual and not automation_enabled("auto_progress_sync") and not add:
        return None
    cached = anilist_cache_read()
    if not cached:
        return None
    entries = {e.get("id"): e for e in cached.get("entries", [])}
    patches = {}
    for u in release_units(s):
        if only is not None and not any(ep["id"] in only for ep, _ in u["eps"]):
            continue
        aid = u["media_id"]
        watched = [n for ep, n in u["eps"] if ep["watched"]]
        highest = max(watched) if watched else 0
        remote = entries.get(aid)
        if remote is not None:
            if not unit_fits(u, remote.get("episodes")) or highest <= (remote.get("progress") or 0):
                continue
            if outbox_pending_for(aid):
                # Offline E5 then E6: fold the newer count into the queued
                # progress-only row instead of dropping it.
                if outbox_raise_progress(aid, highest):
                    patches[aid] = {"progress": highest}
                continue
            patches[aid] = {"progress": highest}
            outbox_enqueue(aid, patch=patches[aid])
            continue
        # Unlisted: consent-gated auto-add, and only an entry the user
        # actually watched part of — never the whole franchise at once.
        if not add or not highest or outbox_pending_for(aid) or not unit_fits(u, u.get("total")):
            continue
        patches[aid] = {"status": "CURRENT", "progress": highest}
        outbox_enqueue(aid, patch=patches[aid])
    if not patches:
        return None
    return next(iter(patches.values())) if len(patches) == 1 else patches


def auto_track_after_play(ep_id):
    """The Decision 5 automations, evaluated after PLAYBACK marks an
    episode watched (manual marks use their own path — TODO 6). Delegates
    to anilist_track_show with the automations' consent rules, for the
    played episode's entry only."""
    if not anilist_connected():
        return None
    s, ep = episode_by_id(ep_id)
    if not s or not ep:
        return None
    return anilist_track_show(trackable(s), only={ep["id"]})


def bulk_watched(show_id, season=None, watched=True, ids=None):
    """TODO 6 (Decision 27): mark every episode of one season — or the whole
    container — watched (or unwatched) in one go. This is bookkeeping for
    shows watched before the app existed, NOT playback: no completion event
    fires (Decision 25), manual marks use this separate path. The local
    facts flip first, then Decision 27's sync: each touched entry's
    published progress becomes its highest watched episode (release_units —
    unmapped seasons stay local), and unwatching stays local (AniList
    corrections stay on the entry panel). `ids` (multi-select) limits the
    mark to those episodes of the show. Returns a small report for the UI."""
    s = media_by_id(show_id)
    if not s:
        return {"error": "unknown show"}
    pick = set(ids) if ids is not None else None
    if season is not None:
        if not 0 <= season < len(s["seasons"]):
            return {"error": "no such season"}
        targets = [s["seasons"][season]]
    else:
        targets = s["seasons"]
    eps = [ep for sea in targets for ep in sea["episodes"] if pick is None or ep["id"] in pick]
    with db() as c:
        for ep in eps:
            c.execute(
                "INSERT INTO episodes(id,watched) VALUES(?,?)"
                " ON CONFLICT(id) DO UPDATE SET watched=excluded.watched",
                (ep["id"], int(bool(watched))))
    synced = None
    if watched:
        # The in-memory show still carries the pre-mark flags (the scan
        # happened before the writes) — reflect them so the sync sees the
        # fresh state. Unwatching stays local (Decision 27).
        for ep in eps:
            ep["watched"] = 1
        try:
            synced = anilist_track_show(trackable(s), manual=True, only={ep["id"] for ep in eps})
        except Exception as exc:
            log("bulk sync failed: %s" % exc)
    return {"ok": True, "episodes": len(eps), "watched": bool(watched),
            "synced": synced}


def completion_for_episode(ep_id):
    """Decision 25's trigger, evaluated after a PLAYBACK session completes an
    episode (never after manual marking — separate code path). Fires when
    the finished episode is the final one of its own entry (release_units:
    S2's finale completes S2's entry, whatever folder it sits in) and the
    disk copy fits that entry. Unmapped seasons stay silent, and entries
    with an unknown total (airing) can't fire until the total is known."""
    s, ep = episode_by_id(ep_id)
    if not s or not ep:
        return
    unit, n = unit_of_episode(trackable(s), ep_id)
    if not unit:
        return
    aid = unit["media_id"]
    total, title = None, None
    cached = anilist_cache_read()
    if cached:
        for e in cached.get("entries", []):
            if e.get("id") == aid:
                total, title = e.get("episodes"), e.get("title")
                break
    total = total or unit.get("total")
    if not total or n != total or not unit_fits(unit, total):
        return
    _completion_event(aid, title or (s.get("meta") or {}).get("title") or s["title"])


def set_watched(eid, watched):
    with db() as c:
        c.execute("INSERT INTO episodes(id,watched) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET watched=excluded.watched",
                  (eid, int(watched)))


def reset_progress(eid):
    with db() as c:
        c.execute("UPDATE episodes SET position=0, watched=0, last_played=NULL WHERE id=?", (eid,))


def curation_get(ref):
    """Local curation overlay for a spine ref ('aid:<anilist_id>' once
    matched, 'sid:<show_id>' before). Returns None when nothing is stored.
    This is the user's own state — `shows_meta.score` (averageScore) is
    fetched metadata and never read or written here."""
    with db() as c:
        row = c.execute(
            "SELECT status, score, favourite, rewatches, started_at, finished_at, prefs, updated_at"
            " FROM curation WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    return {"ref": ref, "status": row[0], "score": row[1], "favourite": bool(row[2]),
            "rewatches": row[3] or 0, "started_at": row[4], "finished_at": row[5],
            "prefs": json.loads(row[6]) if row[6] else {}, "updated_at": row[7]}


def curation_set(ref, patch):
    """Create-or-merge a curation row. Known fields change only when present
    in the patch; unknown keys are ignored. Facts stay in `episodes`."""
    updates = {}
    for field in ("status", "score", "favourite", "rewatches", "started_at", "finished_at"):
        if field in patch:
            updates[field] = patch[field]
    if isinstance(patch.get("prefs"), dict):
        updates["prefs"] = json.dumps(patch["prefs"])
    if not updates:
        return curation_get(ref)
    now = utc_stamp()
    cols = ["ref", "updated_at"] + list(updates)
    vals = [ref, now] + list(updates.values())
    sets = ", ".join(f"{col}=excluded.{col}" for col in cols[1:])
    with db() as c:
        c.execute(f"INSERT INTO curation({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
                  f" ON CONFLICT(ref) DO UPDATE SET {sets}", vals)
    return curation_get(ref)


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


def cache_put(key, payload):
    with db() as c:
        c.execute("INSERT INTO catalog_cache(key,payload,ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
                  (key, json.dumps(payload), time.time()))


def mpv_ipc(sock_path, command, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps({"command": command}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    for line in buf.decode(errors="replace").splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "data" in msg or ("error" in msg and msg.get("error") != "success"):
            return msg
    return None


def season_rank(sea):
    """One authoritative season order (TODO 24) — the UI shows a folder's
    seasons in the order the scan gives them: main seasons first, parts
    continuing after them, then finales, movies, and everything else
    (Specials). The
    scanner's numeric key alone cannot express this — Part N without a
    Season parent shares Season 1's key, so autoplay chained parts before
    season 1 while the UI showed seasons first."""
    label = sea.get("label") or ""
    m = re.match(r"^Season (\d+)", label, re.I)
    if m:
        return (0, int(m.group(1)), 0)
    m = re.match(r"^Part (\d+)", label, re.I)
    if m:
        return (1, int(m.group(1)), 0)
    if re.search(r"final", label, re.I):
        return (2, 0, 0)
    if re.match(r"^movies?$", label, re.I):
        return (3, 0, 0)
    return (4, 0, 0)


def ordered_seasons(s):
    """Seasons in the authoritative order — the scanner now sorts them
    this way too, so autoplay and the UI agree by construction."""
    return sorted(s["seasons"], key=season_rank)


def entry_of_episode(ep_id):
    """(AniList entry, its own episode number) an episode is: an online id
    names it; a file is what its folder season maps to. None for a file
    that maps to no entry."""
    if ep_id.startswith("stream:"):
        mid = stream_media_id(ep_id)
        try:
            return (mid, _stream_num(ep_id.split(":", 2)[2])) if mid else None
        except ValueError:
            return None
    s, ep = episode_by_id(ep_id)
    if not ep:
        return None
    unit, n = unit_of_episode(trackable(s), ep_id)
    return (unit["media_id"], n) if unit else None


def next_episode(ep_id):
    """The episode id to play after ep_id, on its franchise page: the next
    episode of the same entry, else the first of the next main season.
    Files and online episodes chain into each other (the last file of a
    season → the first episode not on disk, played online). An episode that
    can't play (not available online) ends the chain rather than skipping
    ahead. A file that maps to no entry walks its folder instead. None at
    the end."""
    at = entry_of_episode(ep_id)
    if at:
        mid, n = at
        entries = franchise_view(mid, background=False)["entries"]
        e = next((x for x in entries if x["id"] == mid), None)
        if e:
            def playable(entry, x):
                return x and (x["local"] or (x["online"] and entry["online"]["available"])) and x["id"]

            nxt = next((x for x in e["slots"] if x["number"] > n), None)
            if nxt or e["group"] != "main":
                return playable(e, nxt) or None
            mains = [x for x in entries if x["group"] == "main"]
            later = mains[mains.index(e) + 1:]
            return (playable(later[0], later[0]["slots"][0]) or None) if later and later[0]["slots"] else None
    s, ep = episode_by_id(ep_id)
    if not ep:
        return None
    sea = next((x for x in s["seasons"] if any(e["id"] == ep_id for e in x["episodes"])), None)
    if not sea:
        return None
    after = [e for e in sea["episodes"] if e["number"] > ep["number"]]
    if after:
        return min(after, key=lambda e: e["number"])["id"]
    seasons = ordered_seasons(s)
    for nxt in seasons[seasons.index(sea) + 1:]:
        eps = sorted(nxt["episodes"], key=lambda e: e["number"])
        if eps:
            return eps[0]["id"]
    return None


def autoplay_next(ep_id, version=0):
    """Chain into the episode after ep_id, carrying the chosen file version
    (TODO 24): the same index into the next episode's parallel file list
    keeps the user's picked variant instead of resetting to the first one;
    start_play clamps it to what exists. Files and online episodes chain
    into each other along the franchise page (next_episode)."""
    try:
        nxt = next_episode(ep_id)
        if nxt:
            start_play(nxt, version)
    except Exception as e:
        print(f"Autoplay: {e}", file=sys.stderr, flush=True)


def _owns_session(ep_id, proc):
    """Whether proc is still the registered session for ep_id — the
    generation guard every watcher effect goes through (TODO 24)."""
    with _active_lock:
        return _active.get(ep_id, {}).get("proc") is proc


def _record_session_error(ep_id, msg):
    _session_error.update(ep_id=ep_id, error=msg, ts=time.time())


def watch_process(proc, ep_id, sock_path, startup=15, failure=None):
    """Track one playback session over mpv's IPC socket. `startup` bounds
    the wait for the socket; `failure(rc)` words a session that ended
    before playback (stream sessions report ani-cli's own error)."""
    last_time = 0.0
    duration = 0.0
    # No position write until mpv has reported one: a stream (or a slow
    # file) answers on the socket while still buffering, and writing the
    # initial 0 then would erase the resume point if the user quits early.
    seen = False
    deadline = time.time() + startup
    connected = False
    while time.time() < deadline and proc.poll() is None:
        if not sock_path.exists():
            time.sleep(0.25)
            continue
        try:
            mpv_ipc(sock_path, ["set_property", "pause", "false"])
            connected = True
            with _active_lock:
                entry = _active.get(ep_id)
                if entry is not None and entry.get("proc") is proc:
                    entry["loading"] = False
            break
        except (FileNotFoundError, ConnectionRefusedError, socket.error):
            time.sleep(0.25)
    if not connected and proc.poll() is not None and _owns_session(ep_id, proc):
        # mpv died before playback started (bad file, missing decoder…):
        # record it so the UI can explain the dead play click (TODO 24).
        rc = proc.wait()
        # A stream's failure handler may retry (dub → sub, re-match) and
        # answer None: the replacement session owns the outcome then.
        msg = failure(rc) if failure else (f"mpv exited before playback started "
                                           f"(exit code {rc}). See mpv-stderr.log in the data dir.")
        if msg:
            _record_session_error(ep_id, msg)
    if connected:
        while proc.poll() is None:
            try:
                t = mpv_ipc(sock_path, ["get_property", "time-pos"])
                d = mpv_ipc(sock_path, ["get_property", "duration"])
                if t and isinstance(t.get("data"), (int, float)):
                    last_time = t["data"]
                    seen = True
                if d and isinstance(d.get("data"), (int, float)):
                    duration = d["data"]
                # Generation guard (TODO 24): a retired watcher must not write
                # the DB or the replacement's percentage — check ownership
                # immediately before every effect, not only at cleanup.
                if not _owns_session(ep_id, proc):
                    break
                if not seen:
                    try:
                        proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                now = utc_stamp()
                with db() as c:
                    c.execute(
                        """INSERT INTO episodes(id,position,duration,last_played) VALUES(?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET position=excluded.position,
                           duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE episodes.duration END,
                           last_played=excluded.last_played""",
                        (ep_id, last_time, duration, now),
                    )
                with _active_lock:
                    entry = _active.get(ep_id)
                    if entry is not None and entry.get("proc") is proc:
                        entry["percent"] = (last_time / duration * 100) if duration else 0
            except (FileNotFoundError, ConnectionRefusedError, socket.error):
                break
            # Wait out the interval, but wake the instant mpv exits —
            # sleeping through it would delay the next episode by up to 2s.
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        stop_process_group(proc, signal.SIGKILL)
        rc = proc.wait()
    anicli_release(proc)
    # Supersession (TODO 24): start_play terminates the replaced mpv with
    # SIGTERM, so returncode -SIGTERM means this watcher was retired — its
    # replacement owns tracking now, and this session must have no effects
    # at all (the old bug: a retired watcher marked the episode watched at
    # its own 80 % and overwrote the new session's position).
    retired = rc == -signal.SIGTERM or getattr(proc, "hub_retired", False)
    # "Done with this episode": the last position we saw was within ~3s of
    # the end (the poll runs every 2s, so a natural EOF — or a seek to the
    # end — always qualifies), or the skip-credits marker (hotkey quit).
    # The old >=90% rule still marks watched for manual quits that get far
    # enough into the episode.
    marker = Path(str(sock_path) + ".skip")
    done = (duration and duration - last_time <= 3) or marker.exists()
    if not retired and duration and (done or last_time / duration >= 0.9):
        pos_save = duration if done and last_time / duration < 0.9 else last_time
        with db() as c:
            c.execute(
                """INSERT INTO episodes(id,watched,last_played,position,duration) VALUES(?,?,datetime('now'),?,?)
                   ON CONFLICT(id) DO UPDATE SET watched=1, position=excluded.position,
                   duration=excluded.duration, last_played=excluded.last_played""",
                (ep_id, 1, pos_save, duration),
            )
        # Show-completion event (TODO 9, Decision 25): playback finishing the
        # matched release's last episode — natural EOF or the skip-credits
        # gesture both land here; manual marks never do. Rewatch replays
        # fire again by design (each is a new popup event).
        try:
            completion_for_episode(ep_id)
        except Exception as exc:
            log("completion event failed: %s" % exc)
        # AniList automations (TODO 15, Decision 5): progress sync + optional
        # auto-add, playback-driven like the completion event, outbox-backed.
        try:
            auto_track_after_play(ep_id)
        except Exception as exc:
            log("auto-track failed: %s" % exc)
    autoplay = False
    version = 0
    with _active_lock:
        # A same-episode replay inserts a fresh entry under the same id while
        # this watcher is winding down; only remove our own generation.
        entry = _active.get(ep_id, {})
        if entry.get("proc") is proc:
            version = entry.get("version", 0)
            _active.pop(ep_id, None)
            autoplay = not _active and bool(done) and not retired
    if autoplay:
        autoplay_next(ep_id, version)
    try:
        sock_path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
    except OSError:
        pass


def resume_args(ep_id):
    """mpv's --start for an episode, or nothing. Resume rule (TODO 24), the
    one authoritative rule the UI mirrors: a saved position is a resume
    point whenever it is non-trivial (> 1 % of the episode — the frontend's
    resumable() uses exactly this), for unwatched rows including past the
    92 % strip cutoff, and for watched rows still below the cutoff; a
    finished row restarts. The old 60 s floor disagreed with the UI's
    Resume label in the 1–60 s zone."""
    with db() as c:
        row = c.execute("SELECT position, duration, watched FROM episodes WHERE id=?", (ep_id,)).fetchone()
    pos = row[0] if row else 0
    dur = row[1] if row else 0
    watched = bool(row[2]) if row else False
    if dur and pos / dur > 0.01 and (pos / dur < 0.92 or not watched):
        return [f"--start={int(pos // 60)}:{int(pos % 60)}"]
    return []


def retire_session(proc):
    """Supersede a playback session (TODO 24): flag it for its watcher —
    an ani-cli session exits with its own code, not -SIGTERM — then stop
    its whole process group."""
    proc.hub_retired = True
    stop_process_group(proc)


def start_play(ep_id, version=0):
    if ep_id.startswith("stream:"):
        return start_stream(ep_id)
    s, ep = episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    files = ep.get("files") or [{"path": ep["rel"], "label": "default"}]
    version = max(0, min(version, len(files) - 1))
    path = LIBRARY / files[version]["path"]
    if not path.exists():
        return {"error": "file missing"}
    sock = Path("/tmp") / f"anime-hub-{uuid.uuid4().hex[:8]}.sock"
    args = ["mpv", str(path), f"--input-ipc-server={sock}", *resume_args(ep_id)]
    # No --save-position-on-quit (TODO 24): the hub's IPC loop is the only
    # resume store. mpv's watch-later state would otherwise fight an
    # intentional restart/reset — a stale mpv-side position can override
    # the --start the hub decided on.
    if SKIP_CREDITS_LUA.exists():
        args.append(f"--script={SKIP_CREDITS_LUA}")
    # One serialized transition (TODO 24): supersede → spawn → register.
    # Without the lock, concurrent play requests spawn two live mpv
    # processes and a watcher can reach auto-next mid-replacement.
    with _launch_lock:
        with _active_lock:
            old = next(iter(_active.values()), None)
        if old:
            retire_session(old["proc"])

        def spawn():
            """mpv's stderr lands in mpv-stderr.log (best effort — a log
            problem must never look like an mpv problem), and the fd is
            closed right after spawn: the child keeps its inherited copy."""
            err_target = subprocess.DEVNULL
            log = None
            try:
                DATA.mkdir(parents=True, exist_ok=True)
                log = open(DATA / "mpv-stderr.log", "ab")
                log.write(f"\n=== {utc_stamp()}  {path}\n".encode())
                err_target = log
            except OSError:
                if log:
                    log.close()
                log = None
            try:
                return subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                        stderr=err_target, start_new_session=True)
            finally:
                if log:
                    log.close()

        try:
            proc = spawn()
        except FileNotFoundError:
            return {"error": "mpv was not found — install mpv and make sure it is on PATH."}
        except OSError as e:
            return {"error": f"mpv could not be started: {e}"}
        sea = next(x for x in s["seasons"] if any(e["id"] == ep_id for e in x["episodes"]))
        with _active_lock:
            _active.clear()
            _active[ep_id] = {"proc": proc, "ep_id": ep_id, "show_title": s["title"],
                              "label": session_label(sea, ep), "percent": 0, "version": version,
                              "loading": True}
    threading.Thread(target=watch_process, args=(proc, ep_id, sock), daemon=True).start()
    return {"ok": True}


# ---------- ani-cli bridge (v0.2 streaming, TODO 18 second half) ----------
# Streaming and downloads are delegated to the user's own ani-cli: the hub
# contains no provider code and fetches no video itself. It runs the ani-cli
# on PATH, answers its menus through a small helper (ANI_CLI_MENU) instead of
# fzf, and hands mpv the hub's IPC socket via ANI_CLI_PLAYER_FLAGS — so a
# streamed episode gets the same progress tracking, resume, skip-credits and
# autoplay as a file on disk.

# The helper ani-cli calls in place of fzf: `menu "<prompt>"`, choices on
# stdin ("N title" per line). Every list is recorded for the hub; "Select
# anime" is answered by exact title (or by position for links made without
# one); every other prompt is declined, which ends ani-cli instead of
# leaving it waiting on a menu nobody sees.
ANICLI_MENU_SH = r"""#!/bin/sh
# Written by Anime Hub (hub.py) — regenerated on demand, edits are lost.
for arg; do prompt=$arg; done
list=$(cat)
if [ -n "$HUB_ANICLI_CAPTURE" ]; then
    printf '\036%s\n%s\n' "$prompt" "$list" >>"$HUB_ANICLI_CAPTURE"
fi
case "$prompt" in
    "Select anime"*)
        if [ -n "$HUB_ANICLI_TITLE" ]; then
            printf '%s\n' "$list" | awk 'substr($0, length($1) + 2) == ENVIRON["HUB_ANICLI_TITLE"] { print; found = 1; exit } END { exit !found }'
            exit
        elif [ -n "$HUB_ANICLI_INDEX" ]; then
            printf '%s\n' "$list" | awk '$1 == ENVIRON["HUB_ANICLI_INDEX"] { print; found = 1; exit } END { exit !found }'
            exit
        fi
        ;;
esac
exit 1
"""
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# ani-cli's die() prints its message in bold red.
ANICLI_DIE_RE = re.compile(r"\x1b\[1;31m(.*?)\x1b\[0m")
STREAM_EP_RE = re.compile(r"^\d{1,4}(?:\.\d{1,2})?$")
DL_PERCENT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
STREAM_QUALITIES = ("best", "1080", "720", "480", "360", "worst")
STREAM_MODES = ("sub", "dub")
# Airing shows gain episodes; the show page re-asks ani-cli after this long.
STREAM_EPISODES_TTL = 6 * 3600
ANICLI_PROBE_TIMEOUT = 60
# ani-cli scrapes the episode's sources before mpv even starts, so a stream
# gets far longer than a local file to bring up its IPC socket.
STREAM_STARTUP_TIMEOUT = 90


class AniCliError(Exception):
    """An ani-cli failure, worded for the UI."""


def anicli_path():
    return shutil.which("ani-cli")


_anicli_versions = {}


def anicli_version():
    exe = anicli_path()
    if not exe:
        return None
    if exe not in _anicli_versions:
        try:
            out = subprocess.run([exe, "-V"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        _anicli_versions[exe] = out.splitlines()[-1] if out else None
    return _anicli_versions[exe]


def stream_prefs():
    q, m = get_setting("stream_quality"), get_setting("stream_mode")
    return {"quality": q if q in STREAM_QUALITIES else "best",
            "mode": m if m in STREAM_MODES else "sub"}


def anicli_menu_helper():
    path = DATA / "anicli-menu.sh"
    try:
        current = path.read_text()
    except OSError:
        current = None
    if current != ANICLI_MENU_SH:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(ANICLI_MENU_SH)
        tmp.chmod(0o755)
        os.replace(tmp, path)
    return path


def anicli_env(link=None, capture=None, **extra):
    """The environment every ani-cli run gets: the menu helper, the user's
    stream preferences, and the link's pick (exact title, else position)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUB_ANICLI_")}
    prefs = stream_prefs()
    env.update(ANI_CLI_MENU=str(anicli_menu_helper()), TERM="dumb",
               ANI_CLI_QUALITY=prefs["quality"],
               ANI_CLI_MODE=(link or {}).get("mode") or prefs["mode"])
    if capture:
        env["HUB_ANICLI_CAPTURE"] = str(capture)
    if link:
        if link.get("title"):
            env["HUB_ANICLI_TITLE"] = link["title"]
        else:
            env["HUB_ANICLI_INDEX"] = str(link.get("idx") or 1)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def anicli_spawn(args, env, stdout, stderr):
    """Start ani-cli in its own process group with a pty as stdin: without a
    terminal on stdin, ani-cli swaps ANI_CLI_MENU for rofi/dmenu whenever
    either is installed. The pty stays open until anicli_release."""
    exe = anicli_path()
    if not exe:
        raise AniCliError("ani-cli was not found — install ani-cli and make sure it is on PATH.")
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen([exe, *args], stdin=slave, stdout=stdout, stderr=stderr,
                                env=env, start_new_session=True)
    except OSError as e:
        os.close(master)
        raise AniCliError(f"ani-cli could not be started: {e}")
    finally:
        os.close(slave)
    proc.hub_pty = master
    return proc


def anicli_release(proc):
    fd = getattr(proc, "hub_pty", None)
    if fd is not None:
        proc.hub_pty = None
        try:
            os.close(fd)
        except OSError:
            pass


def stop_process_group(proc, sig=signal.SIGTERM):
    """Signal a session's whole process group (start_new_session: pgid ==
    pid) — an ani-cli session is a shell whose mpv / yt-dlp child would
    otherwise outlive it."""
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        try:
            proc.send_signal(sig)
        except OSError:
            pass


def anicli_message(text):
    """ani-cli's own last error (die prints it in red), else the last line."""
    found = ANICLI_DIE_RE.findall(text or "")
    if found:
        return found[-1].strip()
    lines = [line.strip() for line in ANSI_RE.sub("", text or "").replace("\r", "\n").splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


def anicli_probe(args, link=None, timeout=ANICLI_PROBE_TIMEOUT):
    """Run ani-cli once for the lists it offers — never for playback: the
    debug player only prints links, so even a show whose single result or
    single episode ani-cli picks by itself starts nothing.
    Returns (exit code, stderr, [(prompt, lines)])."""
    fd, capture = tempfile.mkstemp(prefix="anime-hub-anicli-", suffix=".txt")
    os.close(fd)
    try:
        proc = anicli_spawn(args, anicli_env(link, capture=capture, ANI_CLI_PLAYER="debug"),
                            subprocess.DEVNULL, subprocess.PIPE)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process_group(proc, signal.SIGKILL)
            proc.communicate()
            raise AniCliError(f"ani-cli did not answer within {timeout} s — is the site reachable?")
        finally:
            anicli_release(proc)
        text = Path(capture).read_text(errors="replace")
    finally:
        Path(capture).unlink(missing_ok=True)
    lists = []
    for chunk in text.split("\x1e")[1:]:
        prompt, _, body = chunk.partition("\n")
        lists.append((prompt.strip(), [line for line in body.splitlines() if line.strip()]))
    return proc.returncode, err.decode(errors="replace"), lists


def _anicli_failed(rc, err, link=None):
    msg = anicli_message(err)
    if link and "Invalid anime selection" in msg:
        return AniCliError(f"ani-cli no longer finds “{link.get('title') or link['query']}” when searching "
                           f"“{link['query']}” — choose the source again.")
    return AniCliError(f"ani-cli: {msg}" if msg else f"ani-cli failed (exit code {rc}).")


def anicli_search(query):
    """ani-cli's search results for a query: [{index, title}]. A single hit
    is taken by ani-cli without asking, so its title stays unknown (None)."""
    query = " ".join(query.split()).lstrip("-").strip()
    if not query:
        raise AniCliError("Enter a title to search for.")
    rc, err, lists = anicli_probe([query])
    for prompt, lines in lists:
        if prompt.startswith("Select anime"):
            results = []
            for line in lines:
                n, _, title = line.partition(" ")
                if n.isdigit() and title:
                    results.append({"index": int(n), "title": title})
            return {"query": query, "results": results}
    if lists or rc == 0:
        return {"query": query, "results": [{"index": 1, "title": None}]}
    if "No results" in anicli_message(err):
        return {"query": query, "results": []}
    raise _anicli_failed(rc, err)


def anicli_episodes(link):
    """The episode list ani-cli offers for a link, as it spells the numbers."""
    rc, err, lists = anicli_probe([link["query"]], link)
    for prompt, lines in lists:
        if prompt.startswith("Select episode"):
            eps = [line.strip() for line in lines if STREAM_EP_RE.match(line.strip())]
            if eps:
                return eps
    if rc == 0:
        # One episode (movies): ani-cli picks it without asking.
        return ["1"]
    raise _anicli_failed(rc, err, link)


STREAM_COLS = "media_id, query, title, idx, mode, episodes, episodes_ts, meta, updated_at, matched_by"
# A release ani-cli has no confident match for is not searched again on
# every visit; a manual pick (or this long) lifts it.
STREAM_MISS_TTL = 24 * 3600
STREAM_UNAVAILABLE = "This show isn't available to watch online."


def _stream_link(row):
    return {"media_id": row[0], "query": row[1], "title": row[2], "idx": row[3] or 1,
            "mode": row[4] if row[4] in STREAM_MODES else "sub",
            "episodes": json.loads(row[5]) if row[5] else [], "episodes_ts": row[6] or 0,
            "meta": json.loads(row[7]) if row[7] else {}, "updated_at": row[8],
            "matched_by": row[9] or "auto"}


def stream_link_get(media_id):
    with db() as c:
        row = c.execute(f"SELECT {STREAM_COLS} FROM stream_links WHERE media_id=?", (media_id,)).fetchone()
    return _stream_link(row) if row else None


def stream_links_all():
    with db() as c:
        return [_stream_link(r) for r in c.execute(f"SELECT {STREAM_COLS} FROM stream_links")]


def stream_missed_ids():
    with db() as c:
        return {r[0] for r in c.execute("SELECT media_id FROM stream_misses WHERE ts > ?",
                                        (time.time() - STREAM_MISS_TTL,))}


def _stream_miss(media_id, missed):
    with db() as c:
        if missed:
            c.execute("INSERT INTO stream_misses(media_id, ts) VALUES(?,?) ON CONFLICT(media_id)"
                      " DO UPDATE SET ts=excluded.ts", (media_id, time.time()))
        else:
            c.execute("DELETE FROM stream_misses WHERE media_id=?", (media_id,))


RELEASE_QUERY = """query($id:Int){Media(id:$id,type:ANIME){id title{romaji english native} synonyms
format status episodes averageScore seasonYear startDate{year} nextAiringEpisode{episode}
coverImage{extraLarge} bannerImage description streamingEpisodes{title thumbnail}}}"""


def release_meta(media_id):
    """One AniList release with what matching needs (all titles, synonyms,
    aired count). Cached briefly: airing counts move weekly."""
    key = f"release:v1:{media_id}"
    cached = cache_get(key, 3 * 3600)
    if cached is not None:
        return cached.get("media")
    try:
        data = anilist_request({"query": RELEASE_QUERY, "variables": {"id": media_id}})
    except HTTPError as e:
        if e.code == 404:
            cache_put(key, {"media": None})
            return None
        raise
    media = (data.get("data") or {}).get("Media")
    cache_put(key, {"media": media})
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
        poster = f"/img/{name}.jpg" if download(cover, POSTERS / f"{name}.jpg") else cover
        banner = (f"/img/{name}.banner.jpg" if download(m.get("bannerImage"), POSTERS / f"{name}.banner.jpg")
                  else m.get("bannerImage"))
        eps_art = media_episode_art(m)
    else:
        poster, banner = cover, m.get("bannerImage")
        seps = m.get("streamingEpisodes") or []
        eps_art = (parse_seps(seps) if not (m.get("episodes") and len(seps) > m["episodes"])
                   else {"thumbs": None, "titles": None})
    return {"title": t.get("english") or t.get("romaji") or fallback_title,
            "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear"),
            "score": m.get("averageScore"), "overview": strip_html(m.get("description")),
            "format": m.get("format"), "episodes": m.get("episodes"),
            "poster": poster, "banner": banner,
            "thumbs": eps_art.get("thumbs"), "titles": eps_art.get("titles")}


def stream_meta(media_id, fallback_title):
    """Display metadata for a linked release. AniList being unreachable
    must not block a link."""
    try:
        m = release_meta(media_id)
    except Exception as e:
        log("release meta for %s failed: %s" % (media_id, e))
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
            return anicli_search(q)["results"]
        except AniCliError as e:
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
    for _, _, query, title, idx in sorted(cands.values(), key=lambda c: (-c[0], -c[1]))[:4]:
        try:
            episodes = anicli_episodes({"query": query, "title": title, "idx": idx})
        except AniCliError as e:
            failures.append(str(e))
            continue
        if _count_fits(episodes, m):
            return query, title, idx, episodes
    if not cands and failures and len(failures) == len(tried):
        # Every search failed (offline, site down): that is no verdict.
        raise AniCliError(failures[-1])
    return None


def friendly_stream_error(msg, code="this episode"):
    """ani-cli's (and the site's) failures in the app's words."""
    m = (msg or "").lower()
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
    with db() as c:
        c.execute("""INSERT INTO stream_links(media_id,query,title,idx,mode,episodes,episodes_ts,meta,updated_at,matched_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(media_id) DO UPDATE SET query=excluded.query, title=excluded.title,
                     idx=excluded.idx, mode=excluded.mode, episodes=excluded.episodes,
                     episodes_ts=excluded.episodes_ts, meta=excluded.meta, updated_at=excluded.updated_at,
                     matched_by=excluded.matched_by""",
                  (media_id, query, title, idx or 1, mode, json.dumps(episodes), time.time(),
                   json.dumps(meta), utc_stamp(), matched_by))
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
            raise AniCliError(STREAM_UNAVAILABLE)
        if not anicli_path():
            raise AniCliError(friendly_stream_error("ani-cli not found"))
        _resolving.add(media_id)
        try:
            try:
                m = release_meta(media_id)
            except Exception:
                raise AniCliError("Couldn't reach AniList — check your connection.")
            if not m:
                raise AniCliError(STREAM_UNAVAILABLE)
            if m.get("status") == "NOT_YET_RELEASED":
                raise AniCliError("This isn't out yet.")  # no miss: it will be
            try:
                found = stream_match(m)
            except AniCliError as e:
                raise AniCliError(friendly_stream_error(str(e)))
            if not found:
                _stream_miss(media_id, True)
                log("no ani-cli match for AniList %s (%s)" % (media_id, release_titles(m)[:2]))
                raise AniCliError(STREAM_UNAVAILABLE)
            query, title, idx, episodes = found
            mode = link["mode"] if link else stream_prefs()["mode"]
            _store_link(media_id, query, title, idx, mode, episodes, "auto", None)
            return stream_link_get(media_id)
        finally:
            _resolving.discard(media_id)


def stream_resolve_async(media_id):
    """Start linking a release in the background (a page was opened), so
    its real episode list is there before anyone clicks."""
    if (media_id in _resolving or stream_link_get(media_id) or not anicli_path()
            or media_id in stream_missed_ids()):
        return
    _resolving.add(media_id)

    def run():
        try:
            stream_autolink(media_id)
        except AniCliError:
            pass
        except Exception as e:
            log("linking %s failed: %s" % (media_id, e))
        finally:
            _resolving.discard(media_id)

    threading.Thread(target=run, daemon=True).start()


def stream_link_set(media_id, query, title, idx=1, mode=None, display=None):
    """A user-picked result ("Wrong episodes?"): wins over the automatic
    match. The episode probe runs first, so only a working pick is kept."""
    link = stream_link_get(media_id)
    mode = mode if mode in STREAM_MODES else (link["mode"] if link else stream_prefs()["mode"])
    episodes = anicli_episodes({"query": query, "title": title or None, "idx": idx or 1, "mode": mode})
    _store_link(media_id, query, title or None, idx, mode, episodes, "user", display)
    return {"ok": True, "show_id": f"aid:{media_id}", "episodes": len(episodes)}


def stream_refresh(media_id):
    """Ask ani-cli for the episode list again (new episodes of airing shows)."""
    link = stream_link_get(media_id)
    if not link:
        return {"error": STREAM_UNAVAILABLE}
    episodes = anicli_episodes(link)
    meta = link["meta"]
    if not meta.get("poster"):
        meta = stream_meta(media_id, meta.get("title") or link["title"] or link["query"])
    with db() as c:
        c.execute("UPDATE stream_links SET episodes=?, episodes_ts=?, meta=? WHERE media_id=?",
                  (json.dumps(episodes), time.time(), json.dumps(meta), media_id))
    return {"ok": True, "episodes": len(episodes), "added": len(set(episodes) - set(link["episodes"]))}


def stream_set_mode(media_id, mode):
    with db() as c:
        n = c.execute("UPDATE stream_links SET mode=?, updated_at=? WHERE media_id=?",
                      (mode, utc_stamp(), media_id)).rowcount
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
    except AniCliError:
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
        for u in release_units(s):
            if u["media_id"] != media_id:
                continue
            for ep, n in u["eps"]:
                if disk_ep(ep):
                    eps.setdefault(n, ep)
            for i in u["seasons"]:
                sea = s["seasons"][i]
                if any(r["id"] == media_id and r["offset"] == 0 and r["last_ep"] is None for r in sea["releases"]):
                    folder = (LIBRARY / sea["episodes"][0]["rel"]).parent
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
            "poster": img_url(meta.get("poster")), "banner": img_url(meta.get("banner")),
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
                       "stale": time.time() - link["episodes_ts"] > STREAM_EPISODES_TTL, **(extra or {})}}


def _stream_rows():
    with db() as c:
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
    scan = scan_library()
    merge_db(scan)
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
    link = {"media_id": media_id, "query": None, "title": None, "mode": stream_prefs()["mode"],
            "episodes": [str(n) for n in range(1, release_aired(m) + 1)], "episodes_ts": time.time(),
            "meta": disp}
    unavailable = not anicli_path() or media_id in stream_missed_ids()
    return _stream_show(link, _stream_rows(),
                        {"provisional": True, "unavailable": unavailable, "ready": bool(anicli_path())})


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
    spaces goes through a symlink in the private runtime dir (or is left out)."""
    if not SKIP_CREDITS_LUA.exists():
        return None
    path = str(SKIP_CREDITS_LUA)
    if re.search(r"[\s*?\[]", path):
        run = os.environ.get("XDG_RUNTIME_DIR")
        if not run or re.search(r"[\s*?\[]", run):
            return None
        link = Path(run) / "anime-hub-skip-credits.lua"
        try:
            if not (link.is_symlink() and os.readlink(link) == path):
                link.unlink(missing_ok=True)
                link.symlink_to(path)
        except OSError:
            return None
        path = str(link)
    return f"--script={path}"


_session_notice = {"msg": None, "ts": 0.0}


def start_stream(ep_id, mode=None, sub_once=False, rematched=False):
    """Play an online episode: link the release if nobody has yet, then let
    ani-cli resolve the episode and run mpv in the foreground
    (--exit-after-play) with the hub's IPC socket, resume point and
    skip-credits script in ANI_CLI_PLAYER_FLAGS. watch_process tracks the
    ani-cli process like a local mpv. A dub that isn't out yet falls back to
    the sub once; a result ani-cli stopped listing is re-matched once."""
    mid = stream_media_id(ep_id)
    token = ep_id.split(":", 2)[2] if mid else ""
    if not mid or not STREAM_EP_RE.match(token):
        return {"error": "unknown episode"}
    if not shutil.which("mpv"):
        return {"error": "mpv was not found — install mpv and make sure it is on PATH."}
    try:
        link = stream_autolink(mid)
    except AniCliError as e:
        return {"error": str(e)}
    if mode in STREAM_MODES and mode != link["mode"]:
        stream_set_mode(mid, mode)
        link["mode"] = mode
    if token not in link["episodes"] and time.time() - link["episodes_ts"] > 60:
        try:
            stream_refresh(mid)
            link = stream_link_get(mid)
        except AniCliError:
            pass
    if token not in link["episodes"]:
        return {"error": f"E{token} isn't out yet."}
    s, ep = episode_by_id(ep_id)
    if not ep:
        return {"error": "unknown episode"}
    sea = next(x for x in s["seasons"] if any(e is ep for e in x["episodes"]))
    label = session_label(sea, ep)
    code = f"E{token}"
    play_mode = "sub" if sub_once else link["mode"]
    sock = Path("/tmp") / f"anime-hub-{uuid.uuid4().hex[:8]}.sock"
    flags = [f"--input-ipc-server={sock}", "--no-input-terminal", *resume_args(ep_id)]
    script = mpv_script_flag()
    if script:
        flags.append(script)
    user_flags = os.environ.get("ANI_CLI_PLAYER_FLAGS", "").strip()
    if user_flags:
        flags.append(user_flags)
    env = anicli_env(link, ANI_CLI_PLAYER="mpv", ANI_CLI_PLAYER_FLAGS=" ".join(flags), ANI_CLI_MODE=play_mode)
    with db() as c:
        # Identity stamp (TODO 25): history stays readable without the link.
        c.execute("INSERT INTO episodes(id,show_id,label) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE"
                  " SET show_id=excluded.show_id, label=excluded.label", (ep_id, s["id"], label))
    errlog = DATA / "anicli-stderr.log"
    with _launch_lock:
        with _active_lock:
            old = next(iter(_active.values()), None)
        if old:
            retire_session(old["proc"])
        err, offset = None, 0
        try:
            err = open(errlog, "ab")
            err.write(f"\n=== {utc_stamp()}  {s['title']} {code} ({play_mode})\n".encode())
            err.flush()
            offset = err.tell()
        except OSError:
            err = None
        try:
            proc = anicli_spawn(["--exit-after-play", "-e", token, link["query"]], env,
                                subprocess.DEVNULL, err or subprocess.DEVNULL)
        except AniCliError as e:
            return {"error": friendly_stream_error(str(e), code)}
        finally:
            if err:
                err.close()
        with _active_lock:
            _active.clear()
            _active[ep_id] = {"proc": proc, "ep_id": ep_id, "show_title": s["title"],
                              "label": label, "percent": 0, "version": 0, "loading": True}

    def failure(rc):
        try:
            with open(errlog, "rb") as f:
                f.seek(offset)
                text = f.read().decode(errors="replace")
        except OSError:
            text = ""
        msg = anicli_message(text)
        log("stream %s failed: %s" % (ep_id, msg or "exit code %s" % rc))
        if play_mode == "dub" and "no sources found for dub" in msg.lower():
            _session_notice.update(msg=f"{code} isn't dubbed yet — playing it subbed.", ts=time.time())
            r = start_stream(ep_id, sub_once=True)
            return r.get("error")
        if "invalid anime selection" in msg.lower() and not rematched:
            try:
                stream_autolink(mid, rematch=True)
            except AniCliError as e:
                return str(e)
            r = start_stream(ep_id, mode, sub_once, rematched=True)
            return r.get("error")
        return friendly_stream_error(msg, code)

    threading.Thread(target=watch_process, args=(proc, ep_id, sock),
                     kwargs={"startup": STREAM_STARTUP_TIMEOUT, "failure": failure},
                     daemon=True).start()
    return {"ok": True, "stream": True}


# Downloads: one ani-cli -d at a time, into the library folder, so a
# finished episode simply becomes a file the next scan picks up.
_dl_cv = threading.Condition()
_downloads = []
_dl_state = {"seq": 0, "worker": None}


def stream_download_dir(link, scan):
    """Where a release's downloads go: the folder season its files are in
    (entry_files), else a folder named after the release."""
    if LIBRARY is None:
        raise AniCliError("Choose a library folder in Settings first — downloads are saved there.")
    if not LIBRARY.is_dir():
        raise AniCliError(f"The library folder {LIBRARY} is not accessible right now.")
    home = entry_files(scan, link["media_id"])[1]
    if home and home != LIBRARY:
        return home
    name = link["meta"].get("title") or link["title"] or link["query"]
    # Plain quotes, as people type folder names (AniList titles use ’ “ ”).
    name = name.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    name = re.sub(r"[/\x00]", " ", name)
    name = " ".join(name.split()).lstrip(".") or f"anilist-{link['media_id']}"
    return LIBRARY / name


def downloads_payload():
    with _dl_cv:
        return [{k: v for k, v in j.items() if k not in ("proc", "cancel")} for j in _downloads]


def download_enqueue(media_id, tokens):
    try:
        link = stream_autolink(media_id)
    except AniCliError as e:
        return {"error": str(e)}
    scan = scan_library()
    merge_db(scan)
    try:
        dest = stream_download_dir(link, scan)
    except AniCliError as e:
        return {"error": str(e)}
    on_disk = set(entry_files(scan, media_id)[0])
    title = link["meta"].get("title") or link["title"] or link["query"]
    added = 0
    with _dl_cv:
        busy = {(j["media_id"], j["token"]) for j in _downloads if j["status"] in ("queued", "running")}
        for token in tokens:
            if token not in link["episodes"] or (media_id, token) in busy or _stream_num(token) in on_disk:
                continue
            busy.add((media_id, token))
            _dl_state["seq"] += 1
            _downloads.append({"id": _dl_state["seq"], "media_id": media_id, "token": token,
                               "mode": link["mode"],
                               "show": title, "show_id": f"aid:{media_id}", "dest": str(dest),
                               "status": "queued", "percent": 0, "error": None})
            added += 1
        if added and _dl_state["worker"] is None:
            _dl_state["worker"] = threading.Thread(target=_dl_loop, daemon=True)
            _dl_state["worker"].start()
        _dl_cv.notify_all()
    return {"ok": True, "queued": added, "dest": str(dest)}


def download_cancel(job_id):
    with _dl_cv:
        job = next((j for j in _downloads if j["id"] == job_id), None)
        if not job:
            return {"error": "unknown download"}
        if job["status"] == "queued":
            job["status"] = "cancelled"
        elif job["status"] == "running":
            job["cancel"] = True
            if job.get("proc"):
                stop_process_group(job["proc"])
    return {"ok": True}


def download_clear():
    with _dl_cv:
        _downloads[:] = [j for j in _downloads if j["status"] in ("queued", "running")]
    return {"ok": True}


def _dl_loop():
    while True:
        with _dl_cv:
            job = None
            while job is None:
                job = next((j for j in _downloads if j["status"] == "queued"), None)
                if job is None:
                    _dl_cv.wait()
            job["status"] = "running"
        try:
            _dl_run(job)
        except Exception as e:
            log("download failed: %s" % e)
            with _dl_cv:
                job.pop("proc", None)
                job.update(status="failed", error=str(e))


def _dl_has_file(dest, token):
    try:
        return any(n.endswith(f" Episode {token}.mp4") for n in os.listdir(dest))
    except OSError:
        return False


def _dl_mark_dub(dest, token):
    """ani-cli names sub and dub downloads alike; a "(Dub)" suffix makes the
    dub its own version of the episode (scan reads "(...)" as a version
    label) and keeps a later sub download from overwriting it."""
    for n in os.listdir(dest):
        if n.endswith(f" Episode {token}.mp4"):
            target = dest / (n[:-4] + " (Dub).mp4")
            if not target.exists():
                os.replace(dest / n, target)
            return


def _dl_cleanup(dest, token, had_file, created):
    """Remove what an unfinished download left: yt-dlp's .part/.ytdl
    fragments, the subtitle, and a partial .mp4 (ffmpeg writes in place)."""
    stem = f" Episode {token}"
    try:
        names = os.listdir(dest)
    except OSError:
        return
    for n in names:
        if (stem + ".mp4.part" in n or n.endswith(stem + ".mp4.ytdl") or n.endswith(stem + ".vtt")
                or (not had_file and n.endswith(stem + ".mp4"))):
            try:
                (dest / n).unlink()
            except OSError:
                pass
    if created:
        try:
            dest.rmdir()  # only succeeds when nothing else is in it
        except OSError:
            pass


def _dl_run(job):
    link = stream_link_get(job["media_id"])
    if not link:
        raise AniCliError(STREAM_UNAVAILABLE)
    dest = Path(job["dest"])
    created = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)
    had_file = _dl_has_file(dest, job["token"])
    # ani-cli hands ANI_CLI_PLAYER_FLAGS to yt-dlp/ffmpeg when downloading;
    # the user's (mpv) flags would break them.
    mode = job.get("mode") or link["mode"]
    env = anicli_env(link, ANI_CLI_DOWNLOAD_DIR=str(dest), ANI_CLI_PLAYER_FLAGS="", ANI_CLI_MODE=mode)
    proc = anicli_spawn(["-d", "-e", job["token"], link["query"]], env, subprocess.PIPE, subprocess.STDOUT)
    with _dl_cv:
        job["proc"] = proc
        if job.get("cancel"):
            stop_process_group(proc)
    tail, buf = [], b""
    try:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            *lines, buf = re.split(rb"[\r\n]", buf + chunk)
            for raw in lines:
                line = raw.decode(errors="replace")
                m = DL_PERCENT_RE.search(line)
                if m:
                    with _dl_cv:
                        job["percent"] = float(m.group(1))
                elif line.strip():
                    tail = (tail + [line])[-40:]
        rc = proc.wait()
    finally:
        proc.stdout.close()
        anicli_release(proc)
    with _dl_cv:
        job.pop("proc", None)
        cancelled = job.get("cancel")
    if not cancelled and rc == 0 and _dl_has_file(dest, job["token"]):
        if mode == "dub" and not had_file:
            _dl_mark_dub(dest, job["token"])
        with _dl_cv:
            job.update(status="done", percent=100)
        _dl_adopt(job["media_id"], dest)
        return
    _dl_cleanup(dest, job["token"], had_file, created)
    with _dl_cv:
        if cancelled:
            job["status"] = "cancelled"
        else:
            msg = anicli_message("\n".join(tail + [buf.decode(errors="replace")]))
            log("download %s E%s failed: %s" % (job["media_id"], job["token"], msg or rc))
            job.update(status="failed", error=friendly_stream_error(msg, f"E{job['token']}"))


def _dl_adopt(media_id, dest):
    """A download that started a new folder becomes a new disk show — match
    it to the release right away instead of waiting for the matcher."""
    try:
        rel = str(dest.relative_to(LIBRARY))
        show = next((s for s in scan_library() if s["path"] == rel), None)
        if not show:
            return
        with db() as c:
            known = c.execute("SELECT 1 FROM shows_meta WHERE show_id=?", (show["id"],)).fetchone()
        if not known:
            set_match(show["id"], media_id)
    except Exception as e:
        log("matching downloaded folder failed: %s" % e)


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


def anilist_request(payload, token=None):
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
                return json.loads(r.read())
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


def fetch_media_full(id=None, search=None):
    key = f"media:v1:{'id:' + str(id) if id is not None else 'search:' + (search or '').lower()}"
    cached = cache_get(key, EPISODE_META_TTL)
    if cached is not None:
        return cached.get("media")
    if id is not None:
        payload = {"query": MEDIA_QUERY_ID, "variables": {"id": id}}
    else:
        payload = {"query": MEDIA_QUERY_SEARCH, "variables": {"search": search}}
    try:
        data = anilist_request(payload)
    except HTTPError as e:
        if e.code == 404:  # AniList signals "no match" with 404, not null data
            cache_put(key, {"media": None})
            return None
        raise
    media = (data.get("data") or {}).get("Media")
    cache_put(key, {"media": media})
    return media


def parse_seps(seps):
    """Parse streamingEpisodes without downloading: thumbs keyed by episode
    number (raw CDN urls) plus cleaned titles, both {str(n): value}."""
    thumbs, titles = {}, {}
    pos = 0
    for e in seps or []:
        pos += 1
        raw = strip_html(e.get("title") or "")
        m = SEP_NUM_RE.match(raw)
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
        dest = EPIMGS / fname
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

def fetch_media_candidates(title):
    """A bounded title search over the AniList catalog, cached."""
    query = re.sub(r"\s*\([^)]*\)", "", title).strip()
    query = re.sub(r"(\d+)(?:st|nd|rd|th)\s+Season", r"Season \1", query, flags=re.I)
    if not query:
        return []
    key = "media-candidates:v1:" + query.lower()
    cached = cache_get(key, EPISODE_META_TTL)
    if cached is not None:
        return cached
    data = anilist_request({"query": MEDIA_QUERY_CANDIDATES, "variables": {"search": query}})
    candidates = ((data.get("data") or {}).get("Page") or {}).get("media") or []
    cache_put(key, candidates)
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


UPSCAYL_FLATPAK = "org.upscayl.Upscayl"
UPSCAYL_BIN = "/app/upscayl/resources/bin/upscayl-bin"
UPSCAYL_MODELS = "/app/upscayl/resources/models"
UPSCAYL_MODEL = "upscayl-standard-4x"
UPSCAYL_TTA = True


def jpeg_dimensions(data):
    """(width, height) straight from the JPEG's SOF marker, or None.
    Stdlib-only — enough for a sanity check, never a full decode."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h)
        if marker in (0xD9,) or marker == 0xDA:
            return None  # EOI / start-of-scan before any SOF: not a baseline we can measure
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def upscale_looks_sane(src_bytes, out_bytes):
    """Dimension guard for the banner upscaler (found 2026-10-15: on this
    machine upscayl runs on software Vulkan and emits garbage — six real
    banners were replaced by black voids, one batch even at a runaway
    27440×5776). A 4x model's output must stay within a 4x reach of the
    source's dimensions. Pixel-level blackness can't be checked stdlib-only
    without decoding — the stronger protection is that the whole upscaler is
    opt-in (config 'upscale_banners', default off)."""
    src, out = jpeg_dimensions(src_bytes), jpeg_dimensions(out_bytes)
    if not src or not out:
        return False
    return (src[0] // 2 <= out[0] <= src[0] * 4.5 + 8 and
            src[1] // 2 <= out[1] <= src[1] * 4.5 + 8)


def upscale_banner(path):
    out = path.with_suffix(".upscayl.jpg")
    try:
        cmd = ["flatpak", "run", "--command=" + UPSCAYL_BIN, UPSCAYL_FLATPAK,
               "-i", str(path), "-o", str(out), "-m", UPSCAYL_MODELS,
               "-n", UPSCAYL_MODEL, "-f", "jpg", "-c", "90"]
        if UPSCAYL_TTA:
            cmd.append("-x")
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        src = path.read_bytes()
        if r.returncode == 0 and out.exists() and upscale_looks_sane(src, out.read_bytes()):
            os.replace(out, path)
            return True
    except Exception:
        pass
    out.unlink(missing_ok=True)
    return False


_upscale_lock = threading.Lock()
_upscale_inflight = set()


def upscale_banner_async(path):
    """Upscale in the background; the raw file keeps serving as placeholder."""
    with _upscale_lock:
        if path in _upscale_inflight:
            return
        _upscale_inflight.add(path)

    def run():
        try:
            upscale_banner(path)
        finally:
            with _upscale_lock:
                _upscale_inflight.discard(path)

    threading.Thread(target=run, daemon=True).start()


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
    poster = f"/img/{poster_name}" if download((m.get("coverImage") or {}).get("extraLarge"), POSTERS / poster_name) else None
    banner = f"/img/{banner_name}" if download(m.get("bannerImage"), POSTERS / banner_name) else None
    if banner and UPSCALE_BANNERS:
        # In the background: keep the metadata queue moving
        # instead of blocking ~19s per banner on the Upscayl run.
        # Opt-in (config 'upscale_banners', default off): on machines without
        # working GPU Vulkan the upscaler emits all-black images — found
        # destroying real banners 2026-10-15 — and AniList's own banners are
        # already high-resolution.
        upscale_banner_async(POSTERS / banner_name)
    seps = m.get("streamingEpisodes") or []
    eps_meta = [strip_html(e["title"]).split(" - ", 1)[-1] for e in seps] if seps else None
    thumbs = media_episode_art(m)["thumbs"]
    title = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")
    with db() as c:
        c.execute(
            """INSERT INTO shows_meta(show_id,anilist_id,title,year,score,overview,poster,banner,episodes_meta,format,episode_thumbs,fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(show_id) DO UPDATE SET anilist_id=excluded.anilist_id,title=excluded.title,year=excluded.year,
               score=excluded.score,overview=excluded.overview,poster=excluded.poster,banner=excluded.banner,
               episodes_meta=excluded.episodes_meta,format=excluded.format,episode_thumbs=excluded.episode_thumbs,fetched_at=excluded.fetched_at""",
            (show["id"], m["id"], title, year, m.get("averageScore"), strip_html(m.get("description")),
             poster, banner, json.dumps(eps_meta) if eps_meta else None, m.get("format"),
             json.dumps(thumbs) if thumbs else None),
        )
    return True


def clear_match(show_id):
    """Remove a (wrong) match: the shows_meta row goes, the optional title
    override goes with it. Fetched metadata only — watch facts are in
    `episodes` and untouched (Decision 17.2). The next pass re-attempts."""
    with db() as c:
        c.execute("DELETE FROM shows_meta WHERE show_id=?", (show_id,))
        # Old per-season art (season_thumbs, no longer written) goes with it.
        # Season pins stay: they name entries, not the folder's match.
        c.execute("DELETE FROM season_thumbs WHERE show_id=?", (show_id,))
    overrides = load_overrides()
    changed = False
    for show in scan_library():
        if show["id"] != show_id:
            continue
        for key in (show["title"], show.get("path")):
            if key and overrides.pop(key, None) is not None:
                changed = True
    if changed:
        (DATA / "overrides.json").write_text(json.dumps(overrides, indent=1))


def set_match(show_id, anilist_id=None, query=None):
    """Manual match repair (TODO 28 minimal): commit a user-chosen AniList
    id (or raw search query) for a show and refetch its metadata. The
    manual answer bypasses confidence scoring (it IS the confidence), so
    the surface stays usable even where the automatic matcher stays silent.
    anilist_id=None clears the match."""
    show = next((s for s in scan_library() if s["id"] == show_id), None)
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
    (DATA / "overrides.json").write_text(json.dumps(overrides, indent=1))
    with db() as c:
        c.execute("DELETE FROM season_thumbs WHERE show_id=?", (show_id,))
    store_show_meta(show, m)
    return {"ok": True, "anilist_id": anilist_id,
            "title": (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji")}


def fetch_show_meta(show, override=None):
    with db() as c:
        row = c.execute("SELECT poster FROM shows_meta WHERE show_id=?", (show["id"],)).fetchone()
    if row and row[0] and not override:
        return True
    m = resolve_match(show, override)
    if not m:
        return False
    return store_show_meta(show, m)


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


def load_token():
    """The stored OAuth token dict, or None. A corrupt file counts as
    absent — auth state must degrade to 'not connected', never traceback."""
    try:
        tok = json.loads(ANILIST_TOKEN_FILE.read_text())
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
    tmp = ANILIST_TOKEN_FILE.with_suffix(".tmp")
    # Created 0600 — never briefly world-readable under the default umask.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(rec))
    os.chmod(tmp, 0o600)  # an existing .tmp keeps its old mode through O_CREAT
    os.replace(tmp, ANILIST_TOKEN_FILE)
    os.chmod(ANILIST_TOKEN_FILE, 0o600)
    return rec


def clear_token():
    ANILIST_TOKEN_FILE.unlink(missing_ok=True)


def auth_disconnect():
    """Symmetric disconnect (Decision 6, TODO 17): unlinking converts back
    to the local variant in place — the token goes, and with it the account
    (reads follow the token's account, so nothing can hold AniList mode),
    the pending reconcile marker goes. Curation and watch facts are
    untouched: no data is lost in either direction, AniList state is an
    overlay on top of local state."""
    clear_token()
    reconcile_clear()
    return {"ok": True}


def auth_status():
    """Connection state for the UI. The token itself never appears here —
    only who is connected and until when (Decision 20 redaction rule)."""
    tok = load_token()
    if not tok:
        return {"connected": False, "client_id_configured": bool(ANILIST_CLIENT_ID),
                "redirect_uri": f"http://localhost:{PORT}/auth/callback"}
    expired = tok.get("expires_at") is not None and tok["expires_at"] < time.time()
    return {"connected": not expired, "user": tok.get("user_name"), "user_id": tok.get("user_id"),
            "expires_at": tok.get("expires_at"), "expired": expired,
            "client_id_configured": bool(ANILIST_CLIENT_ID),
            "redirect_uri": f"http://localhost:{PORT}/auth/callback"}


_AUTH_PENDING_TS = 0.0


def auth_start():
    """Build the authorize URL for the configured client and mark a flow as
    pending — /auth/callback only accepts tokens while a started flow is
    fresh (login correlation, READINESS A5). Returns the URL for the UI to
    open in the user's browser (their AniList session lives there)."""
    global _AUTH_PENDING_TS
    if not ANILIST_CLIENT_ID:
        return {"error": "No AniList client id configured — add \"anilist_client_id\": <id> to the config file."}
    _AUTH_PENDING_TS = time.time()
    # No redirect_uri parameter: AniList's implicit grant (unlike its
    # authorization-code grant) takes the redirect from the client's
    # registered settings, and passing it anyway makes the authorize
    # endpoint answer unsupported_grant_type (found live 2026-10-15).
    return {"url": (f"https://anilist.co/api/v2/oauth/authorize"
                    f"?client_id={ANILIST_CLIENT_ID}&response_type=token")}


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
        ANILIST_CACHE.unlink(missing_ok=True)
    # Reads follow this token's account (load_anilist_username reads
    # user_name from the token file) — no separate binding to maintain.
    # Decision 24: reconnect with the SAME account flushes the held queue
    # (the 60 s loop picks it up now that a valid token exists); a
    # DIFFERENT account's intents are discarded — never sent across.
    discarded = outbox_discard_other_accounts(viewer.get("id"))
    # Decision 14: a (re)connect is a reconciliation event — the dialog
    # compares local watch state/curation against the account until the
    # user applies or dismisses.
    reconcile_mark_pending(viewer.get("id"))
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


def outbox_enqueue(media_id, patch=None, favourite=None):
    tok = load_token() or {}
    payload = {"patch": patch or {}, "account_id": tok.get("user_id")}
    if favourite is not None:
        payload["favourite"] = bool(favourite)
    with db() as c:
        c.execute("INSERT INTO outbox(media_id,payload,account_id,created)"
                  " VALUES(?,?,?,?)",
                  (media_id, json.dumps(payload), tok.get("user_id"), utc_stamp()))


def outbox_count():
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def outbox_fail(row_id, attempts, error):
    """Backoff schedule for transient failures; after the final attempt the
    row is dropped with a loud log — the local overlay keeps the user's
    change, and a poison row must not block the queue forever."""
    n = attempts + 1
    if n >= OUTBOX_MAX_ATTEMPTS:
        with db() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
        log("outbox: dropped entry %s after %d attempts — %s" % (row_id, n, error))
        return
    with db() as c:
        c.execute("UPDATE outbox SET attempts=?, next_try=?, last_error=? WHERE id=?",
                  (n, time.time() + OUTBOX_BACKOFF[min(n, len(OUTBOX_BACKOFF)) - 1],
                   str(error)[:300], row_id))


def outbox_flush(limit=20):
    """Send due rows FIFO through the normal write path. Auth failure stops
    the whole pass WITHOUT consuming attempts — the queue goes dormant until
    a valid token exists again (Decision 24)."""
    try:
        state = auth_status()
    except Exception:
        return 0
    if not state.get("connected"):
        return 0
    with db() as c:
        due = c.execute("SELECT id, media_id, payload, attempts FROM outbox"
                        " WHERE next_try <= ? ORDER BY id LIMIT ?",
                        (time.time(), limit)).fetchall()
    sent = 0
    tok = load_token() or {}
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
        try:
            if payload.get("patch"):
                anilist_entry_save(media_id, payload["patch"])
            if "favourite" in payload:
                anilist_favourite_set(media_id, payload["favourite"])
        except AniListTransientError as e:
            outbox_fail(row_id, attempts, e)
            continue
        except AniListAuthError:
            # Dormant (Decision 24): stop the pass, keep attempts untouched,
            # retry when a valid token exists again.
            break
        except ValueError as e:
            outbox_fail(row_id, attempts, e)
            continue
        with db() as c:
            c.execute("DELETE FROM outbox WHERE id=?", (row_id,))
        sent += 1
    return sent


def outbox_discard_other_accounts(new_account_id):
    """Decision 24: connecting a DIFFERENT account discards the held queue
    with a count (never send account A's activity to account B). Rows with
    no recorded account (defensive — unreachable via the routes) are kept
    and flushed under whoever connects."""
    with db() as c:
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
            log("outbox loop: %s" % e)
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
            if anilist_connected() and load_anilist_username():
                anilist_user_data()
        except Exception as e:
            log("anilist refresh loop: %s" % e)
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
        set_setting(RECONCILE_KEY, str(viewer_id))


def reconcile_clear():
    with db() as c:
        c.execute("DELETE FROM settings WHERE key=?", (RECONCILE_KEY,))


def reconcile_analyze():
    """Compute the connect-time merge. Facts apply immediately (both
    directions, guarded); curation conflicts come back as rows. Only shows
    whose matched release is ALSO on the user's lists participate — pushing
    facts to an unlisted show would create list entries, which is the
    consent-gated auto-track domain (Decision 5, TODO 15). Per entry
    (release_units): each mapped season of a folder reconciles against its
    own entry; unmapped seasons sit out."""
    tok = load_token()
    if not tok or not tok.get("user_id"):
        return {"pending": False, "facts": [], "conflicts": []}
    if get_setting(RECONCILE_KEY) != str(tok.get("user_id")):
        return {"pending": False, "facts": [], "conflicts": []}
    try:
        data = anilist_user_data(force=True)
    except Exception as e:
        return {"pending": True, "facts": [], "conflicts": [],
                "error": "Could not fetch AniList state: %s" % e}
    remote = {e["id"]: e for e in data.get("entries", [])}
    fmt = data.get("score_format") or "POINT_10"
    facts, conflicts = [], []
    scan = scan_library()
    merge_db(scan)  # the folder's match, season map and watch facts live in hub.db
    seen = set()
    for s in scan:
        for u in release_units(trackable(s)):
            aid = u["media_id"]
            if aid not in remote or aid in seen:
                continue
            seen.add(aid)
            e = remote[aid]
            label = e.get("title") or s["title"]
            # Facts (automatic). Both directions, per entry, in the entry's
            # own numbering; a folder that doesn't fit the entry is never
            # pushed (unit_fits). An unknown total (airing) can't rule it out.
            if unit_fits(u, e.get("episodes")):
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
                            set_watched(ep["id"], True)
                            marked.append(n)
                    if marked:
                        facts.append({"show": label, "kind": "watched",
                                      "remote": remote_progress, "local": local_highest})
            # Curation (user decides): only when the local overlay holds an
            # opinion — a never-touched show has no local values to defend.
            cur = curation_get("aid:%d" % aid) or {}
            r_status, l_status = e.get("status"), cur.get("status")
            r_score = _score_norm(e.get("score100"), fmt)
            l_score = _score_norm(cur.get("score"), fmt)
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
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM outbox WHERE media_id=?",
                         (media_id,)).fetchone()[0]


def outbox_raise_progress(media_id, progress):
    """Raise the newest queued row's progress when it is a plain
    progress-only intent (the auto-track/bulk shape) below `progress`.
    Rows carrying anything else are the user's own edits — left alone.
    Returns True when a row was raised."""
    with db() as c:
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
        data = anilist_user_data()
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
                curation_set(ref, cpatch)
                applied += 1
        elif winner == "local":
            cur = curation_get(ref) or {}
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


def anilist_user_data(force=False):
    if load_anilist_username() is None:
        # No-AniList mode (Decision 6): a readable payload shape, not a 502 —
        # the UI renders "not connected" rather than "unavailable".
        return {"user": None, "entries": []}
    if not force and ANILIST_CACHE.exists():
        try:
            cached = json.loads(ANILIST_CACHE.read_text())
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
                    "next_episode": ({"number": e["media"]["nextAiringEpisode"]["episode"],
                                      "in_seconds": e["media"]["nextAiringEpisode"]["timeUntilAiring"]}
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
    tmp = ANILIST_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out | {"_ts": time.time()}))
    os.replace(tmp, ANILIST_CACHE)
    return out


def refresh_meta():
    if _meta_status["running"]:
        return
    _meta_status["running"] = True
    try:
        scan = scan_library()
        merge_db(scan)
        overrides = load_overrides()
        missing = [s for s in scan if not s.get("poster")]
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
        merge_db(scan)
        _meta_status["phase"] = "franchises"
        _meta_status["current"] = None
        try:
            refresh_franchises(franchise_seeds(scan))
            drop_premature_links()
        except Exception as e:
            log("franchise refresh failed: %s" % e)
        # Missing episode art is a normal poster fallback, retried later.
        _meta_status["phase"] = "episode art"
        try:
            merge_db(scan)  # seasons map onto the franchises just built
            refresh_entry_art(scan)
        except Exception as e:
            log("episode art failed: %s" % e)
    finally:
        _meta_status["running"] = False
        _meta_status["pending"] = 0
        _meta_status["current"] = None
        _meta_status["phase"] = None


# --- Franchises (docs/franchise-page-plan.md) -------------------------------
# A franchise is every AniList entry one show page holds: seasons, movies,
# specials, OVAs and shorts, connected by FRANCHISE_RELATIONS. It is built
# in the background from AniList and stored; pages only read the tables.

FRANCHISE_LIMIT = 60
MEDIA_TTL = 7 * 24 * 3600
MEDIA_TTL_CHANGING = 24 * 3600  # airing or announced: dates, counts and new seasons move
FRANCHISE_LOOP_EVERY = 6 * 3600
_franchise_lock = threading.Lock()
_SEASON_SUFFIX = re.compile(r"(?:season\s*)?\d+|\d+(?:st|nd|rd|th)\s+season"
                            r"|(?:second|third|fourth|fifth|sixth)\s+season", re.I)


def _media_ttl(m):
    finished = (m or {}).get("status") in ("FINISHED", "CANCELLED")
    return MEDIA_TTL if finished else MEDIA_TTL_CHANGING


def media_entry(mid, pause=0):
    """One AniList entry (FRANCHISE_QUERY_ID's shape) from the `media`
    table, fetched again once stale. None when AniList has no such entry.
    A failed fetch falls back to the stale copy. `pause` seconds follow a
    network fetch, for background passes that walk many entries."""
    with db() as c:
        row = c.execute("SELECT data, ts FROM media WHERE id=?", (mid,)).fetchone()
    stale = json.loads(row[0]) if row else None
    if stale and time.time() - row[1] < _media_ttl(stale):
        return stale
    try:
        m = (anilist_request({"query": FRANCHISE_QUERY_ID, "variables": {"id": mid}}).get("data") or {}).get("Media")
    except HTTPError as e:
        if e.code != 404:
            if stale:
                return stale
            raise
        m = None
    except Exception:
        if stale:
            return stale
        raise
    finally:
        time.sleep(pause)
    if m:
        with db() as c:
            c.execute("INSERT INTO media(id, data, ts) VALUES(?,?,?) ON CONFLICT(id)"
                      " DO UPDATE SET data=excluded.data, ts=excluded.ts", (mid, json.dumps(m), time.time()))
    return m


def _titles(m):
    t = m.get("title") or {}
    return [x.strip() for x in (t.get("english"), t.get("romaji")) if x and x.strip()]


def numbered_season_of(a, b):
    """True when one entry's title is the other's plus a season number:
    "Cyberpunk: Edgerunners" / "Cyberpunk: Edgerunners 2" (or "... Season
    2", "... 2nd Season"). Lets an OTHER link join a franchise."""
    for x, y in ((a, b), (b, a)):
        for root in _titles(x):
            for t in _titles(y):
                if t.lower().startswith(root.lower()):
                    rest = t[len(root):].strip(" :-\u2013\u2014")
                    if rest and _SEASON_SUFFIX.fullmatch(rest):
                        return True
    return False


def _franchise_order(m):
    d = m.get("startDate") or {}
    return (d.get("year") or 9999, d.get("month") or 99, d.get("day") or 99, m["id"])


def build_franchise(start_id, fetch):
    """Every entry of start_id's franchise, in page order (release date;
    undated entries last). fetch(id) returns one entry or None. A link
    counts when neither side names it FRANCHISE_SEPARATE, so the same
    franchise comes back whichever of its entries it starts from."""
    start = fetch(start_id)
    if not start:
        return []
    found, todo, gone = {start["id"]: start}, [start], set()
    while todo and len(found) < FRANCHISE_LIMIT:
        m = todo.pop(0)
        for e in (m.get("relations") or {}).get("edges") or []:
            node, rel = e.get("node") or {}, e.get("relationType")
            nid = node.get("id")
            if node.get("type") != "ANIME" or node.get("format") not in FRANCHISE_FORMATS \
                    or nid in found or nid in gone:
                continue
            other = rel == "OTHER" and node.get("format") in FRANCHISE_OTHER_FORMATS
            if rel not in FRANCHISE_RELATIONS and not other:
                continue
            n = fetch(nid)
            if not n:
                gone.add(nid)
                continue
            if other and not numbered_season_of(m, n):
                continue
            if any(b.get("relationType") in FRANCHISE_SEPARATE and (b.get("node") or {}).get("id") == m["id"]
                   for b in (n.get("relations") or {}).get("edges") or []):
                continue
            found[nid] = n
            todo.append(n)
    if todo:
        log("franchise of %s stopped at %d entries" % (start_id, FRANCHISE_LIMIT))
    return sorted(found.values(), key=_franchise_order)


def store_franchise(entries):
    """Save one built franchise. Its id is its first entry's. Entries that
    belonged to another franchise move here; a franchise left without
    entries is dropped. Returns the franchise id."""
    if not entries:
        return None
    ids, fid, now = [m["id"] for m in entries], entries[0]["id"], time.time()
    q = ",".join("?" * len(ids))
    with db() as c:
        before = {r[0] for r in c.execute(
            f"SELECT DISTINCT franchise_id FROM franchise_members WHERE media_id IN ({q})", ids)}
        c.execute("DELETE FROM franchise_members WHERE franchise_id=?", (fid,))
        c.execute(f"DELETE FROM franchise_members WHERE media_id IN ({q})", ids)
        c.executemany("INSERT INTO franchise_members(media_id, franchise_id, pos) VALUES(?,?,?)",
                      [(mid, fid, pos) for pos, mid in enumerate(ids)])
        # The fetcher stores entries with their fetch time; this only fills
        # in ones it didn't (tests) and never makes a stale entry look fresh.
        c.executemany("INSERT INTO media(id, data, ts) VALUES(?,?,?) ON CONFLICT(id)"
                      " DO UPDATE SET data=excluded.data", [(m["id"], json.dumps(m), now) for m in entries])
        c.execute("INSERT INTO franchises(id, ts) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts",
                  (fid, now))
        for old in before - {fid}:
            if not c.execute("SELECT 1 FROM franchise_members WHERE franchise_id=?", (old,)).fetchone():
                c.execute("DELETE FROM franchises WHERE id=?", (old,))
    return fid


def tag_franchises(cards):
    """Library cards learn their entry (`media_id`: the AniList entry, or a
    local folder's match) and its `franchise_id`, so the library shows one
    card per franchise. None where no stored franchise holds the entry."""
    with db() as c:
        member = dict(c.execute("SELECT media_id, franchise_id FROM franchise_members"))
    for card in cards:
        mid = card["id"] if isinstance(card["id"], int) else card.get("media_id")
        card["media_id"] = mid
        card["franchise_id"] = member.get(mid)
    return cards


def franchise_of(mid):
    """(franchise id, entries in page order) for one entry, from the
    database only. None until the franchise has been built."""
    with db() as c:
        row = c.execute("SELECT franchise_id FROM franchise_members WHERE media_id=?", (mid,)).fetchone()
        if not row:
            return None
        data = c.execute("""SELECT m.data FROM franchise_members f JOIN media m ON m.id = f.media_id
                            WHERE f.franchise_id=? ORDER BY f.pos""", (row[0],)).fetchall()
    return row[0], [json.loads(d[0]) for d in data]


def _franchise_fresh(fid, entries):
    with db() as c:
        row = c.execute("SELECT ts FROM franchises WHERE id=?", (fid,)).fetchone()
    return bool(row) and time.time() - row[0] < min(_media_ttl(m) for m in entries)


def franchise_seeds(scan):
    """AniList entries the library knows of: matched folders and linked
    online releases. Their franchises are the ones kept up to date."""
    ids = {(s.get("meta") or {}).get("anilist_id") for s in scan}
    ids |= {link["media_id"] for link in stream_links_all()}
    ids.discard(None)
    return ids


def _background_fetch(mid):
    """media_entry for background walks: they wait while a page someone
    opened is being built (franchise_build_async), then keep a slow pace."""
    for _ in range(180):
        if not _building:
            break
        time.sleep(0.5)
    return media_entry(mid, pause=2.2)


def refresh_franchises(seeds, fetch=None):
    """Build and store the franchise of every seed whose stored one is
    missing or stale (a day while any entry airs or is announced, else a
    week). One build covers all seeds in that franchise. Returns how many
    franchises were built; 0 when another pass is already running."""
    fetch = fetch or _background_fetch
    if not _franchise_lock.acquire(blocking=False):
        return 0
    try:
        done, built = set(), 0
        for mid in sorted(seeds):
            if mid in done:
                continue
            known = franchise_of(mid)
            if known and _franchise_fresh(*known):
                done.update(m["id"] for m in known[1])
                continue
            try:
                entries = build_franchise(mid, fetch)
            except Exception as e:
                log("franchise of %s failed: %s" % (mid, e))
                continue
            store_franchise(entries)
            done.update(m["id"] for m in entries)
            built += 1
        return built
    finally:
        _franchise_lock.release()


def franchise_refresh_loop():
    """Keeps franchises current between metadata passes, so a newly
    announced season or movie shows up without a restart."""
    while True:
        time.sleep(FRANCHISE_LOOP_EVERY)
        try:
            scan = scan_library()
            merge_db(scan)
            if refresh_franchises(franchise_seeds(scan)):
                merge_db(scan)
            drop_premature_links()
            refresh_entry_art(scan)
        except Exception as e:
            log("franchise refresh loop: %s" % e)


# --- Local mapping (docs/franchise-page-plan.md, phase 3) -------------------
# Which franchise entry each local episode is. A folder's match (its first
# season's entry) anchors it in a franchise; its numbered seasons are laid
# over the franchise's line of seasons from there, other seasons are found
# among the franchise's entries by name. Worked out from the stored
# franchise on every read, never with a network call; only a user's own
# pin (set_season_pin) is stored.

SEASON_GENERIC_LABELS = {"specials", "special", "ova", "oav", "extras", "others", "other"}
# The line a folder's numbered seasons walk: TV seasons first, then
# specials/OVAs (AoT's final chapters); a movie between seasons is stepped
# through but is no season (Made in Abyss S1 → Dawn of the Deep Soul → S2).
# Format None is an entry AniList has only announced.
LINE_FORMATS = (("TV", "TV_SHORT", "ONA", None), ("SPECIAL", "OVA"), ("MOVIE",))
ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth"}


def season_key(sea):
    return f"{sea['key']}|{sea['label']}"


def _edges(m):
    return ((m or {}).get("relations") or {}).get("edges") or []


def main_line(anchor_id, entries):
    """The seasons from `anchor_id` onward, following SEQUEL links inside
    the franchise (LINE_FORMATS' order). AniList links some next seasons
    only as OTHER (Cyberpunk: Edgerunners 2): those count when the title
    says so (numbered_season_of). Pure: entries as build_franchise
    returns them."""
    by_id = {m["id"]: m for m in entries}
    cur = by_id.get(anchor_id)
    if not cur:
        return []
    line, seen = [cur], {cur["id"]}
    while True:
        near = [(e["relationType"], by_id[e["node"]["id"]]) for e in _edges(cur)
                if e["node"]["id"] in by_id and e["node"]["id"] not in seen]
        sequels = [m for rel, m in near if rel == "SEQUEL"]
        nxt = next((m for group in LINE_FORMATS for m in sequels if m.get("format") in group), None)
        if nxt is None:
            nxt = next((m for rel, m in near if rel == "OTHER" and m.get("format") in LINE_FORMATS[0]
                        and numbered_season_of(cur, m)), None)
        if nxt is None:
            return line
        seen.add(nxt["id"])
        cur = nxt
        if nxt.get("format") != "MOVIE":
            line.append(nxt)


def names_season(m, label):
    """True when an entry's title names the local season label: "Season 2"
    matches "… Season 2", "… 2nd Season" and "… Second Season"; "Part 2"
    matches "… Part 2"."""
    hay = " ".join(_titles(m)).lower()
    lm = re.fullmatch(r"(season|part)\s+(\d+)", (label or "").strip().lower())
    if not lm:
        return False
    word, n = lm.group(1), int(lm.group(2))
    pats = [rf"\b{word}\s+{n}\b"]
    if word == "season":
        pats.append(rf"\b{n}(st|nd|rd|th)\s+season\b")
        if n in ORDINALS:
            pats.append(rf"\b{ORDINALS[n]}\s+season\b")
    return any(re.search(p, hay) for p in pats)


def numbered_seasons(show):
    """The seasons that walk the line, in watch order: Season N / Part N /
    Final … (season ≥ 1; scan order is season_rank). Specials, named arcs
    and movies are found by name instead (named_entry)."""
    if show.get("kind") != "show":
        return []
    return [sea for sea in show["seasons"] if sea["episodes"] and (sea.get("season") or 0) >= 1]


def _range(first, last, m, offset):
    return {"id": m["id"], "first_ep": first, "last_ep": last, "offset": offset, "total": m.get("episodes")}


def map_numbered_seasons(show, line):
    """season key → ranges: lay the folder's numbered seasons over the line
    in order. Each season starts at the next unused entry (a partly
    downloaded season doesn't shift the next one) and spans as many entries
    as its episodes need: AoT's 22-file Season 3 is AniList's 12 + 10.
    Absolute numbering (S2 as E26–E37) is recognised by the first number
    reaching the season's absolute start. A season that needs more entries
    than the line has is left unmapped from there on: never guess."""
    out, pos = {}, 0
    for sea in numbered_seasons(show):
        nums = sorted(e["number"] for e in sea["episodes"]
                      if isinstance(e.get("number"), int) and e["number"] >= 1)
        if not nums or pos >= len(line):
            break
        # AniList's air order can interleave a side arc (Owarimonogatari S1
        # → Tsukimonogatari → Owarimonogatari Second Season): when an entry
        # close ahead names this season and the next one doesn't, the
        # season starts there.
        if pos and not names_season(line[pos], sea["label"]):
            ahead = next((j for j in range(pos + 1, min(pos + 3, len(line)))
                          if names_season(line[j], sea["label"])), None)
            if ahead is not None:
                pos = ahead
        start = 1
        before = [m.get("episodes") for m in line[:pos]]
        if pos and all(before) and nums[0] >= 1 + sum(before):
            start = 1 + sum(before)
        ranges, left = [], nums
        while left and pos < len(line):
            m = line[pos]
            total = m.get("episodes")
            pos += 1
            if not total:
                ranges.append(_range(start, None, m, 1 - start))
                left = []
                break
            end = start + total - 1
            rest = [n for n in left if n > end]
            ranges.append(_range(start, end if rest else None, m, 1 - start))
            left, start = rest, end + 1
        if left:
            break  # the line ran out: this season (and the rest) stays unmapped
        out[season_key(sea)] = ranges
    return out


def named_entry(label, entries):
    """The one franchise entry whose title or alternative name contains a
    named season's label ("Specials/Lost Girls" → "Attack on Titan: Lost
    Girls"; "Together with Papa" is only a synonym of Made in Abyss' "Papa
    to Issho"), else None. Generic labels (Specials, OVA) and ambiguous
    names stay unmapped."""
    want = norm_title(label)
    if not want or label.strip().lower() in SEASON_GENERIC_LABELS:
        return None
    hits = [m for m in entries
            if any(want in norm_title(t) for t in _titles(m) + (m.get("synonyms") or []))]
    return hits[0] if len(hits) == 1 else None


def map_folder(show, anchor_id, entries, pins=None):
    """season key → [range]: which entry each part of each season of one
    folder is. A range is {"id", "first_ep", "last_ep" (None = to the end),
    "offset" (local number + offset = the entry's own number), "total"}.
    `entries` is the anchor's franchise ([] while it isn't built yet:
    then only a single-season folder maps, wholly onto its match). `pins`
    (season key → ranges, [] = no entry) are the user's word and win.
    Seasons that map to nothing are absent: their files stay local only."""
    pins = pins or {}
    filled = [sea for sea in show["seasons"] if sea["episodes"]]
    out = {}
    by_id = {m["id"]: m for m in entries}
    if anchor_id:
        known = anchor_id in by_id
        if known:
            out.update(map_numbered_seasons(show, main_line(anchor_id, entries)))
        numbered = {season_key(x) for x in numbered_seasons(show)}
        for sea in filled:
            key = season_key(sea)
            if key in out:
                continue
            if len(filled) == 1 and not (known and key in numbered):
                out[key] = [_range(1, None, by_id.get(anchor_id) or {"id": anchor_id}, 0)]
            elif known and key not in numbered:
                m = named_entry(sea["label"], entries)
                if m:
                    out[key] = [_range(1, None, m, 0)]
    for key, ranges in pins.items():
        out[key] = ranges
    return {k: v for k, v in out.items() if v}


def release_units(s):
    """The one place a folder's episodes become entries: one unit per
    entry, {"media_id", "eps": [(episode, the entry's own number)],
    "seasons": [season index], "total": the entry's episode count or None},
    in season order. Reads the ranges merge_db attached
    (`season["releases"]`). Episodes outside every range (specials numbered
    0, unmapped seasons) belong to no unit, so nothing is ever synced for
    them. Progress sync, completion, reconcile, AniList watched marks and
    library counts all read this instead of counting seasons."""
    units = {}
    for i, sea in enumerate(s.get("seasons") or []):
        ranges = sea.get("releases") or []
        for ep in sea["episodes"]:
            n = ep.get("number")
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                continue
            r = next((r for r in reversed(ranges)
                      if r["first_ep"] <= n and (r["last_ep"] is None or n <= r["last_ep"])), None)
            if not r or n + r["offset"] < 1:
                continue
            u = units.setdefault(r["id"], {"media_id": r["id"], "eps": [], "seasons": [], "total": None})
            u["total"] = u["total"] or r.get("total")
            u["eps"].append((ep, n + r["offset"]))
            if i not in u["seasons"]:
                u["seasons"].append(i)
    return list(units.values())


def unit_fits(unit, total):
    """A unit may only speak for its entry when it fits inside it: a known
    total rules out more files, or higher numbers, than the entry has (a
    wrong mapping never pushes a too-big count). An unknown total (airing)
    can't rule anything out (Decision 21)."""
    if not total:
        return True
    return len(unit["eps"]) <= total and max(n for _, n in unit["eps"]) <= total


def unit_of_episode(s, ep_id):
    """(unit, the entry's own episode number) for one episode, or (None, None)."""
    for u in release_units(s):
        for ep, n in u["eps"]:
            if ep["id"] == ep_id:
                return u, n
    return None, None


def own_listing(m, earlier):
    """AniList's episode listing (stills, titles) for an entry, when it is
    the entry's own: every number inside the entry, and no still shared
    with an earlier entry of the franchise. AniList hands many sequels
    their first season's listing (Attack on Titan, Kaguya-sama) or the
    next season's (The Apothecary Diaries). None otherwise."""
    parsed = parse_seps(m.get("streamingEpisodes") or [])
    if not parsed["thumbs"]:
        return None
    count = m.get("episodes")
    if count and any(int(k) > count for k in parsed["thumbs"]):
        return None
    seen = {url for e in earlier for url in (parse_seps(e.get("streamingEpisodes") or [])["thumbs"] or {}).values()}
    if seen & set(parsed["thumbs"].values()):
        return None
    return parsed


def episode_listing(m, earlier, anizip):
    """An entry's episode stills and titles as remote urls, {"thumbs",
    "titles", "source"}: its own AniList listing (own_listing), else
    ani.zip's (`anizip(id)` → {str(n): {"image", "title"}}, None when
    unreachable: then None, to retry later)."""
    parsed = own_listing(m, earlier)
    if parsed:
        return dict(parsed, source="anilist")
    eps = anizip(m["id"])
    if eps is None:
        return None
    eps = anizip_pick(eps, m.get("episodes"))
    return {"thumbs": {k: v["image"] for k, v in eps.items() if v.get("image")} or None,
            "titles": {k: v["title"] for k, v in eps.items() if v.get("title")} or None,
            "source": "anizip"}


def anizip_pick(eps, count):
    """ani.zip's episodes of an entry by its own numbers. Plain keys ("1")
    are the episodes, except where TVDB files the entry as specials: then
    its episodes are "O1".."On" and "1" may be something else entirely
    (Kaguya-sama's "The First Kiss That Never Ends": "1" is the theatrical
    cut, "O1".."O4" the four episodes). The keyed run that covers the
    entry's episode count wins; plain keys when both or neither do."""
    plain = {k: v for k, v in eps.items() if k.isdigit()}
    other = {k[1:]: v for k, v in eps.items() if k[:1] == "O" and k[1:].isdigit()}
    if count and len(plain) < count <= len(other):
        return other
    return plain


def season_art(ranges, arts):
    """A season's stills and titles keyed by its own episode numbers,
    gathered from every entry it maps to (AoT S3's second entry, at offset
    −12, fills E13–E22). `arts`: media id → {"thumbs", "titles"}."""
    thumbs, titles = {}, {}
    for r in ranges:
        art = arts.get(r["id"]) or {}
        for got, dst in ((art.get("thumbs"), thumbs), (art.get("titles"), titles)):
            for k, v in (got or {}).items():
                n = int(k) - r["offset"]
                if v and n >= r["first_ep"] and (r["last_ep"] is None or n <= r["last_ep"]):
                    dst.setdefault(str(n), v)
    return thumbs or None, titles or None


def franchise_entries(ids):
    """media id → its franchise's entries in page order, for many ids in
    one read ([] for an id no stored franchise holds)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    with db() as c:
        member = dict(c.execute("SELECT media_id, franchise_id FROM franchise_members"))
        fids = {member[i] for i in ids if i in member}
        rows = c.execute(f"""SELECT f.franchise_id, m.data FROM franchise_members f
                             JOIN media m ON m.id = f.media_id
                             WHERE f.franchise_id IN ({",".join("?" * len(fids))})
                             ORDER BY f.franchise_id, f.pos""", tuple(fids)).fetchall() if fids else []
    by_fid = {}
    for fid, data in rows:
        by_fid.setdefault(fid, []).append(json.loads(data))
    return {i: by_fid.get(member.get(i), []) for i in ids}


def season_pins():
    """show id → season key → the user's ranges for it ([] = no entry)."""
    out = {}
    with db() as c:
        rows = c.execute("""SELECT r.show_id, r.sea_key, r.first_ep, r.last_ep, r.media_id, r.ep_offset, m.data
                            FROM season_releases r LEFT JOIN media m ON m.id = r.media_id
                            WHERE r.source = 'manual' ORDER BY r.show_id, r.sea_key, r.first_ep""").fetchall()
    for sid, key, first, last, mid, offset, data in rows:
        ranges = out.setdefault(sid, {}).setdefault(key, [])
        if mid is not None:
            total = json.loads(data).get("episodes") if data else None
            ranges.append({"id": mid, "first_ep": first, "last_ep": last, "offset": offset or 0, "total": total})
    return out


def entry_art_all(ids):
    """media id → {"thumbs", "titles"} (local /epimg/ urls) for stored entries."""
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    with db() as c:
        rows = c.execute(f"SELECT media_id, thumbs, titles FROM entry_art WHERE media_id IN ({','.join('?' * len(ids))})",
                         ids).fetchall()
    return {r[0]: {"thumbs": json.loads(r[1]) if r[1] else None, "titles": json.loads(r[2]) if r[2] else None}
            for r in rows}


def attach_releases(scan):
    """merge_db's part: every season of every folder gets its ranges
    (`releases`) and the stills/titles of the entries they cover."""
    anchors = {s["id"]: (s.get("meta") or {}).get("anilist_id") for s in scan}
    entries = franchise_entries(anchors.values())
    pins = season_pins()
    maps = {s["id"]: map_folder(s, anchors[s["id"]], entries.get(anchors[s["id"]]) or [], pins.get(s["id"]))
            for s in scan}
    arts = entry_art_all(r["id"] for m in maps.values() for ranges in m.values() for r in ranges)
    for s in scan:
        for sea in s["seasons"]:
            sea["releases"] = maps[s["id"]].get(season_key(sea), [])
            sea["thumbs"], sea["titles"] = season_art(sea["releases"], arts)


def set_season_pin(show_id, sea_key, anilist_id=None, none=False):
    """The "wrong entry?" repair for one season of a folder: pin it to a
    user-chosen AniList entry (`none` = it is no entry: its files stay
    local only), or with neither hand it back to the automatic mapping.
    Watch facts are untouched; only which entry they count for changes."""
    show = next((s for s in scan_library() if s["id"] == show_id), None)
    if not show:
        return {"error": "unknown show"}
    sea = next((x for x in show["seasons"] if season_key(x) == sea_key), None)
    if not sea:
        return {"error": "no such season"}
    m = None
    if anilist_id is not None:
        try:
            m = media_entry(anilist_id)
        except Exception as e:
            return {"error": f"Could not reach AniList: {e}"}
        if not m:
            return {"error": f"AniList has no media with id {anilist_id}"}
    nums = [e["number"] for e in sea["episodes"] if isinstance(e.get("number"), int) and e["number"] >= 1]
    # Absolute numbering (a "Season 2" folder holding E26–E37) counts from
    # its first file; otherwise the season's numbers are the entry's.
    offset = 1 - min(nums) if m and nums and m.get("episodes") and max(nums) > m["episodes"] else 0
    with db() as c:
        c.execute("DELETE FROM season_releases WHERE show_id=? AND sea_key=?", (show_id, sea_key))
        if m or none:
            c.execute("""INSERT INTO season_releases(show_id, sea_key, first_ep, last_ep, media_id, ep_offset, source)
                         VALUES(?,?,?,?,?,?,'manual')""", (show_id, sea_key, 1, None, anilist_id, offset))
    if m:
        threading.Thread(target=background_work(refresh_entry_art), daemon=True).start()
    title = m and ((m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji"))
    return {"ok": True, "anilist_id": anilist_id, "title": title, "cleared": not (m or none)}


ANIZIP_URL = "https://api.ani.zip/mappings?anilist_id=%d"


def anizip_episodes(anilist_id):
    """{key: {"image", "title"}} for one entry from ani.zip, the public
    AniList-id-keyed episode index (TVDB artwork, no API key); only the
    AniList id is sent. Keys are "n" and "On" (see anizip_pick). {} when it
    has nothing; None when it couldn't be reached (not cached, retried
    later)."""
    key = f"anizip:v2:{anilist_id}"
    cached = cache_get(key, EPISODE_META_TTL)
    if cached is not None:
        return cached
    try:
        req = Request(ANIZIP_URL % anilist_id,
                      headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) anime-hub/1.0"})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except HTTPError as e:
        if e.code != 404:
            return None
        data = {}
    except Exception:
        return None
    out = {}
    for k, v in ((data or {}).get("episodes") or {}).items():
        num = k[1:] if k[:1] == "O" else k
        if num.isdigit() and int(num) >= 1 and isinstance(v, dict):
            out[k] = {"image": v.get("image") or None, "title": (v.get("title") or {}).get("en") or None}
    cache_put(key, out)
    return out


_entry_art_lock = threading.Lock()
# Entries waiting for their stills: "now" = opened on a page (first),
# "later" = what local seasons map to. One pass at a time works the queue;
# a caller that finds a pass running leaves its entries to it.
_art_queue = {"now": set(), "later": set()}
_art_queue_lock = threading.Lock()


def _art_next():
    with _art_queue_lock:
        for k in ("now", "later"):
            if _art_queue[k]:
                mid = min(_art_queue[k])
                _art_queue[k].discard(mid)
                return mid
    return None


def _store_entry_art(mid, anizip):
    """Fetch and store one entry's stills unless it has them (an entry
    without stills is retried once EPISODE_META_TTL has passed). True when
    stored."""
    with db() as c:
        row = c.execute("SELECT thumbs, ts FROM entry_art WHERE media_id=?", (mid,)).fetchone()
    if row and (row[0] or row[1] >= time.time() - EPISODE_META_TTL):
        return False
    m = media_entry(mid, pause=2.2)
    if not m:
        return False
    fr = franchise_of(mid)
    earlier = []
    for e in (fr[1] if fr else []):
        if e["id"] == mid:
            break
        earlier.append(e)
    raw = episode_listing(m, earlier, anizip)
    if raw is None:
        return False
    got = commit_thumbs(mid, raw, prefix="z" if raw["source"] == "anizip" else "")
    thumbs = {str(n): u for n, u in got.items()} or None
    with db() as c:
        c.execute("""INSERT INTO entry_art(media_id, thumbs, titles, source, ts) VALUES(?,?,?,?,?)
                     ON CONFLICT(media_id) DO UPDATE SET thumbs=excluded.thumbs, titles=excluded.titles,
                     source=excluded.source, ts=excluded.ts""",
                  (mid, json.dumps(thumbs) if thumbs else None,
                   json.dumps(raw["titles"]) if raw["titles"] else None, raw["source"], time.time()))
    return True


def refresh_entry_art(scan=None, anizip=None, ids=None):
    """Background: fetch the episode stills of every entry a local season
    maps to (or of `ids`: entries opened on a franchise page, which go
    first) and store them (entry_art), so pages draw them without a
    network call. Entries asked for while another pass runs are left to
    that pass, never dropped. Returns the number of entries stored."""
    anizip = anizip or anizip_episodes
    if ids is None:
        if scan is None:
            scan = scan_library()
            merge_db(scan)
        ids, kind = {r["id"] for s in scan for sea in s["seasons"] for r in sea.get("releases") or []}, "later"
    else:
        kind = "now"
    with _art_queue_lock:
        _art_queue[kind].update(ids)
    did = 0
    while True:
        if not _entry_art_lock.acquire(blocking=False):
            return did  # the running pass takes our entries
        try:
            while (mid := _art_next()) is not None:
                try:
                    did += _store_entry_art(mid, anizip)
                except Exception as e:
                    log("stills for %s failed: %s" % (mid, e))
        finally:
            _entry_art_lock.release()
        with _art_queue_lock:
            # Asked for between our last look and the release: go again.
            if not (_art_queue["now"] or _art_queue["later"]):
                return did


# --- Franchise page (docs/franchise-page-plan.md, phase 4) ------------------
# One page per franchise: every entry with its facts and art, and each
# entry's episodes as one list of slots where a local file and an online
# episode are the same thing with a different source. Built from stored
# data only (franchise, mapping, links, watch facts, art); what is missing
# (the franchise itself, an entry's online episode list, its stills) is
# fetched in the background and the page is asked for again.

FORMAT_LABELS = {"TV": "TV", "TV_SHORT": "Short", "ONA": "ONA", "OVA": "OVA", "SPECIAL": "Special",
                 "MOVIE": "Movie", None: "Announced"}


def franchise_root(entries):
    """The entry the main seasons start from and the page is named after:
    the first-aired TV/ONA entry (Bakemonogatari, though AniList calls the
    later Nekomonogatari its prequel), else the oldest entry."""
    return next((m for m in entries if m.get("format") in LINE_FORMATS[0] and m.get("format")),
                entries[0] if entries else None)


def _title_rest(m, roots):
    """What an entry's title adds to the franchise's: "Season 3 Part 2" for
    "Attack on Titan Season 3 Part 2"; "" when that is nothing worth
    showing ("Kaguya-sama: Love is War?", "Cyberpunk: Edgerunners 2");
    None when the title isn't the franchise's at all (Nisemonogatari)."""
    found = None
    for title in _titles(m):
        for root in roots:
            if title.lower().startswith(root.lower()):
                rest = title[len(root):].strip(" :-–—~!?.,")
                if sum(ch.isalnum() for ch in rest) >= 3:
                    return rest
                found = ""
    return found


def entry_label(m, roots, season_no):
    """A short display label: what the title adds to the franchise's
    ("Season 3 Part 2", "The Golden City of the Scorching Sun"); a main
    season adding nothing is "Season N" by its place in the line; an entry
    with a title of its own keeps it (Nisemonogatari). Display only
    (rule 4): a miss is a longer label, never a wrong page."""
    rest = _title_rest(m, roots)
    if rest:
        return rest
    if rest == "" and season_no:
        return f"Season {season_no}"
    return (_titles(m) or [FORMAT_LABELS.get(m.get("format"), "Extra")])[0]


def _entry_facts(m):
    t = m.get("title") or {}
    nxt = m.get("nextAiringEpisode") or {}
    return {"id": m["id"], "title": t.get("english") or t.get("romaji"), "romaji": t.get("romaji"),
            "format": m.get("format"), "format_label": FORMAT_LABELS.get(m.get("format"), m.get("format")),
            "status": m.get("status"), "episodes": m.get("episodes"),
            "aired": release_aired(m) if m.get("status") else 0,
            "next_airing": {"episode": nxt.get("episode"), "at": nxt.get("airingAt")} if nxt else None,
            "year": (m.get("startDate") or {}).get("year") or m.get("seasonYear"),
            "season": m.get("season"), "score": m.get("averageScore"),
            "duration": m.get("duration"), "overview": strip_html(m.get("description")),
            "poster": (m.get("coverImage") or {}).get("extraLarge"), "banner": m.get("bannerImage")}


def _watch(src):
    return {k: src.get(k) for k in ("watched", "position", "duration", "last_played")}


def entry_slots(m, local, link, rows, art, online_ok):
    """One entry's episodes, in order: a slot per episode number that is on
    disk (`local`: number → scanned episode), in the online episode list
    (`link`, once ani-cli linked the entry), or aired by AniList (until
    then: "stream:<id>:<n>" plays it and links on the way). A slot is
    {"number", "title", "thumb", "id" (what Play plays: the file if there
    is one), "source" ("local"/"online"/None), "local", "online", watch
    facts}. It is watched when any of its sources is; position and
    duration come from the source played last."""
    mid = m["id"]
    online = {}
    if link:
        for token in link["episodes"]:
            online[_stream_num(token)] = token
    elif online_ok:
        online = {n: str(n) for n in range(1, (release_aired(m) if m.get("status") else 0) + 1)}
    thumbs, titles = (art or {}).get("thumbs") or {}, (art or {}).get("titles") or {}
    slots = []
    for n in sorted(set(local) | set(online)):
        ep, token = local.get(n), online.get(n)
        sid = f"stream:{mid}:{token}" if token is not None else None
        r = rows.get(sid) if sid else None
        srcs = [x for x in (_watch(ep) if ep else None,
                            {"watched": bool(r[0]), "position": r[1], "duration": r[2], "last_played": r[3]}
                            if r else None) if x]
        last = max(srcs, key=lambda x: x.get("last_played") or "", default={})
        slots.append({"number": n, "title": titles.get(str(n)), "thumb": thumbs.get(str(n)),
                      "id": ep["id"] if ep else sid, "source": "local" if ep else "online" if sid else None,
                      "local": {"id": ep["id"], "files": ep["files"], "show_id": ep.get("show_id"),
                                "season": ep.get("sea_key"), "folder": ep.get("folder"),
                                "season_label": ep.get("season_label")} if ep else None,
                      "online": {"id": sid, "token": token} if sid else None,
                      "watched": any(x.get("watched") for x in srcs),
                      "position": last.get("position") or 0, "duration": last.get("duration") or 0,
                      "last_played": last.get("last_played")})
    return slots


def franchise_page(entries, scan, links, rows, arts, online_ok=True, missed=frozenset()):
    """The page model (pure): `entries` in page order (franchise_of), `scan`
    after merge_db (season ranges, watch facts), `links` media id → ani-cli
    link, `rows` "stream:" id → (watched, position, duration,
    last_played), `arts` media id → stills/titles, `online_ok` ani-cli is
    there, `missed` entries ani-cli has no match for. Returns {"title",
    "poster", "banner", "entries": [facts + "group" ("main" seasons /
    "extra"), "label", "season", "slots", "online"], "other_local":
    [{"show_id", "season", "label", "episodes"}]}: files of the
    franchise's folders that are no entry, never hidden."""
    ids = {m["id"] for m in entries}
    root = franchise_root(entries)
    line = [m["id"] for m in main_line(root["id"], entries)] if root else []
    roots = _titles(root) if root else []
    local, other = {}, []
    for s in scan:
        units = release_units(s)
        mine = [u for u in units if u["media_id"] in ids]
        if not mine and (s.get("meta") or {}).get("anilist_id") not in ids:
            continue
        for u in mine:
            held = local.setdefault(u["media_id"], {})
            for ep, n in u["eps"]:
                if disk_ep(ep):
                    held.setdefault(n, dict(ep, show_id=s["id"], folder=s["title"],
                                            sea_key=f"{ep['season']}|{ep['season_label']}"))
        mapped = {ep["id"] for u in units for ep, _ in u["eps"]}
        for sea in s["seasons"]:
            loose = [dict(ep, **_watch(ep)) for ep in sea["episodes"] if disk_ep(ep) and ep["id"] not in mapped]
            if loose:
                other.append({"show_id": s["id"], "season": season_key(sea), "label": sea["label"],
                              "folder": s["title"], "episodes": loose})
    out = []
    for m in entries:
        no = line.index(m["id"]) + 1 if m["id"] in line else None
        mid = m["id"]
        reachable = online_ok and mid not in missed
        out.append(dict(_entry_facts(m), group="main" if no else "extra", season=no,
                        label=entry_label(m, roots, no),
                        online={"linked": mid in links, "available": bool(links.get(mid)) or reachable,
                                "mode": (links.get(mid) or {}).get("mode")},
                        slots=entry_slots(m, local.get(mid, {}), links.get(mid), rows, arts.get(mid), reachable)))
    # Seasons named on their own (Monogatari: Nisemonogatari, Owarimonogatari)
    # leave the first one its own name too, not "Season 1".
    main = [(e, m) for e, m in zip(out, entries) if e["group"] == "main"]
    if len(main) == 1:
        # Nothing to number it against (or the franchise is still being
        # found: a "Season 2" opened from search must not read "Season 1").
        main[0][0]["label"] = (_titles(main[0][1]) or [main[0][0]["label"]])[0]
    elif len(main) > 1 and all(_title_rest(m, roots) is None for _, m in main[1:]):
        main[0][0]["label"] = _titles(main[0][1])[0]
    facts = _entry_facts(root) if root else {}
    return {"title": facts.get("title"), "poster": facts.get("poster"), "banner": facts.get("banner"),
            "entries": out, "other_local": other}


_building = set()


def franchise_build_async(mid):
    """Build and store a franchise nobody has opened yet (a search result),
    in the background; the page asks again until it is there."""
    if mid in _building:
        return
    _building.add(mid)

    def run():
        try:
            entries = build_franchise(mid, media_entry)  # paced by _anilist_slot
            if entries:
                store_franchise(entries)
        except Exception as e:
            log("building franchise of %s failed: %s" % (mid, e))
        finally:
            _building.discard(mid)

    threading.Thread(target=run, daemon=True).start()


def entry_watched(mid, watched, numbers=None):
    """The franchise page's watched marks: every source of the entry's
    slots (`numbers`, or all) flips at once — a slot is one episode, file
    or online. Marking watched then publishes the entry's progress like a
    bulk mark (Decision 27: a click is the user acting), with the same fit
    rule; unwatching stays local."""
    page = franchise_view(mid, background=False)
    entry = next((e for e in page["entries"] if e["id"] == mid), None)
    if not entry:
        return {"error": "unknown entry"}
    pick = set(numbers) if numbers is not None else None
    slots = [x for x in entry["slots"] if pick is None or x["number"] in pick]
    ids = [src["id"] for x in slots for src in (x["local"], x["online"]) if src]
    with db() as c:
        c.executemany("INSERT INTO episodes(id,watched) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET watched=excluded.watched",
                      [(i, int(bool(watched))) for i in ids])
    synced = None
    if watched:
        done = {x["number"] for x in slots}
        eps = [{"id": x["id"], "number": x["number"], "watched": x["watched"] or x["number"] in done}
               for x in entry["slots"] if isinstance(x["number"], int) and x["number"] >= 1]
        show = {"seasons": [{"episodes": eps, "releases": [
            {"id": mid, "first_ep": 1, "last_ep": None, "offset": 0, "total": entry["episodes"]}]}]}
        try:
            synced = anilist_track_show(show, manual=True)
        except Exception as e:
            log("entry sync failed: %s" % e)
    return {"ok": True, "episodes": len(slots), "ids": ids, "watched": bool(watched), "synced": synced}


_freshening = set()


def drop_premature_links():
    """An automatic ani-cli link for an entry that hasn't aired is another
    show's episodes (older versions made them: the announced "Chainsaw Man:
    Shikaku-hen" played Chainsaw Man's season 1): drop them. Links the user
    chose stay."""
    with db() as c:
        c.execute("""DELETE FROM stream_links WHERE COALESCE(matched_by, 'auto') = 'auto' AND media_id IN
                     (SELECT id FROM media WHERE json_extract(data, '$.status') = 'NOT_YET_RELEASED')""")


def stream_freshen_async(media_id):
    """Ask ani-cli again for an entry's online episodes once its list is
    stale (airing entries gain episodes), in the background."""
    if media_id in _freshening or not anicli_path():
        return
    _freshening.add(media_id)

    def run():
        try:
            stream_refresh(media_id)
        except Exception as e:
            log("refreshing %s failed: %s" % (media_id, e))
        finally:
            _freshening.discard(media_id)

    threading.Thread(target=run, daemon=True).start()


def franchise_view(mid, background=True):
    """GET /api/franchise?id=<entry>: the page holding this entry, with it
    selected. Never waits on the network: a franchise not built yet comes
    back as the entry alone with "building" (stored facts, when there are
    any), and the selected entry's online episode list and stills are
    fetched in the background ("resolving")."""
    fr = franchise_of(mid)
    if fr:
        fid, entries = fr
    else:
        if background:
            franchise_build_async(mid)
        with db() as c:
            row = c.execute("SELECT data FROM media WHERE id=?", (mid,)).fetchone()
        fid, entries = None, [json.loads(row[0])] if row else []
    building = fr is None
    scan = scan_library()
    merge_db(scan)
    drop_premature_links()
    links = {link["media_id"]: link for link in stream_links_all()}
    ids = [m["id"] for m in entries]
    arts = entry_art_all(ids)
    online_ok = bool(anicli_path())
    page = franchise_page(entries, scan, links, _stream_rows(), arts, online_ok, stream_missed_ids())
    if not background:
        return dict(page, id=fid, selected=mid)
    sel = next((m for m in entries if m["id"] == mid), None)
    if sel and mid not in links and sel.get("status") != "NOT_YET_RELEASED":
        stream_resolve_async(mid)
    elif mid in links and time.time() - links[mid]["episodes_ts"] > STREAM_EPISODES_TTL:
        stream_freshen_async(mid)
    if entries and mid not in arts:
        threading.Thread(target=refresh_entry_art, kwargs={"ids": {mid}}, daemon=True).start()
    return dict(page, id=fid, selected=mid, building=building or mid in _building,
                resolving=sorted(i for i in ids if i in _resolving or i in _freshening), stream_ready=online_ok)


def open_show_folder(show_id):
    """Open a disk show's folder in the user's file manager."""
    show = next((s for s in scan_library() if s["id"] == show_id), None)
    if not show or not show.get("path"):
        raise ValueError("no such show on disk")
    path = (LIBRARY / show["path"]).resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir() or not path.is_relative_to(LIBRARY.resolve()):
        raise ValueError("folder not found")
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def open_in_browser(url):
    if not re.fullmatch(r"https://anilist\.co/anime/\d+/?", url or ""):
        raise ValueError("untrusted url")
    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


AUTH_CALLBACK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Anime Hub — AniList</title><style>
body{font:15px/1.5 system-ui,sans-serif;background:#0a0a0c;color:#f5f5f7;
display:grid;place-items:center;height:100vh;margin:0}
div{max-width:420px;text-align:center}b{color:#97b8ff}
</style></head><body><div id="m">Finishing AniList connection…</div>
<script>
"use strict";
const m = document.getElementById("m");
const h = new URLSearchParams(location.hash.slice(1));
// The token lives in the URL fragment only until now — strip it from the
// address bar (and thereby history/backups) before anything else.
history.replaceState(null, "", "/auth/callback");
const t = h.get("access_token");
if (!t) {
  m.textContent = "No token found in the redirect URL — start the connection from the app again.";
} else {
  fetch("/api/auth/callback", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: t, expires_in: Number(h.get("expires_in")) || null }) })
  .then((r) => r.json())
  .then((j) => {
    if (j.ok) { m.innerHTML = "AniList connected as <b></b> — you can close this tab."; m.querySelector("b").textContent = j.user; setTimeout(() => window.close(), 3000); }
    else m.textContent = "Connect failed: " + (j.error || "unknown reason");
  })
  .catch(() => { m.textContent = "Connect failed — could not reach the hub. Is it still running?"; });
}
</script></body></html>"""


def norm_title(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def check_local(self):
        """Browsers allow cross-site simple POSTs to loopback; a foreign Host
        header (DNS rebinding) or a non-JSON POST body is never ours. The
        accepted port follows the configured one — a hardcoded value rejects
        every request from an alternate-port instance (TODO 22, hit live
        during the config work). The JSON content-type gate lives in do_POST."""
        if self.headers.get("Host", "") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            self.send_error(403)
            return False
        return True

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, cache=False):
        try:
            body = Path(path).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_file_body(body, ctype, cache)

    def send_file_body(self, body, ctype, cache=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.check_local():
            return
        route = urlparse(self.path).path
        if route == "/api/library":
            self.send_json(full_library())
        elif route == "/api/anilist":
            try:
                self.send_json(anilist_user_data())
            except Exception as e:
                if ANILIST_CACHE.exists():
                    try:
                        stale = json.loads(ANILIST_CACHE.read_text())
                        stale["stale"] = True
                        self.send_json(stale)
                        return
                    except Exception:
                        pass
                self.send_json({"error": str(e)}, 502)
        elif route == "/api/entry":
            qs = parse_qs(urlparse(self.path).query)
            try:
                mid = int(qs.get("id", ["0"])[0])
            except ValueError:
                self.send_json({"error": "'id' must be an integer"}, 400)
                return
            if mid <= 0:
                self._bad("'id' must be an integer")
                return
            if not load_token():
                # No-AniList mode (Decision 10's local fallback, TODO 17):
                # the editing surface renders from the curation overlay
                # instead of demanding a connection — same fields, local
                # store. A present-but-expired token still falls through to
                # the live read, which raises the re-auth prompt.
                self.send_json({"local": True,
                                "entry": curation_get("aid:%d" % mid)})
                return
            try:
                # pending (additive): a queued outbox intent for this media
                # drives the panel's "Waiting to sync" chip.
                self.send_json({"state": anilist_entry_state(mid),
                                "score_format": _score_format(),
                                "pending": bool(outbox_pending_for(mid))})
            except AniListAuthError as e:
                self.send_json({"error": e.reason, "auth": True}, 401)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
        elif route == "/api/auth/status":
            self.send_json(auth_status())
        elif route == "/api/settings":
            # TODO 15: the settings surface's read side (Decision 5) —
            # toggles + running config + detected tools, never the token.
            self.send_json(settings_payload())
        elif route == "/api/browse":
            # Decision 32: the library folder picker walks one level at a time.
            qs = parse_qs(urlparse(self.path).query)
            try:
                self.send_json(browse_dirs(qs.get("path", [""])[0]))
            except ValueError as e:
                self._bad(str(e))
        elif route == "/auth/callback":
            self.send_file_body(AUTH_CALLBACK_PAGE.encode(), "text/html; charset=utf-8")
        elif route == "/api/now":
            outbox_n = outbox_count()
            # ani-cli downloads (additive): the nav's downloads popover.
            downloads = downloads_payload()
            notice = (dict(_session_notice) if _session_notice["msg"]
                      and time.time() - _session_notice["ts"] < 30 else None)
            with _active_lock:
                if not _active:
                    payload = {"playing": False}
                    # Additive (TODO 24): a play click that produced nothing
                    # explains itself for a short window after the failure.
                    if _session_error["error"] and time.time() - _session_error["ts"] < 60:
                        payload["error"] = _session_error["error"]
                    # Show-completion popup event (TODO 9, Decision 25):
                    # delivered until dismissed, then never again.
                    pending = _pending_completion()
                    if pending:
                        payload["completed"] = pending
                    if outbox_n:
                        # Quiet sync chip (TODO 14, Decision 3).
                        payload["outbox"] = outbox_n
                    if downloads:
                        payload["downloads"] = downloads
                    if notice:
                        payload["notice"] = notice
                    self.send_json(payload)
                    return
                a = next(iter(_active.values()))
                payload = {"playing": True, "show": a["show_title"], "label": a["label"],
                           "percent": a.get("percent") or 0, "episode_id": a["ep_id"],
                           "loading": bool(a.get("loading"))}
                if notice:
                    payload["notice"] = notice
                if outbox_n:
                    payload["outbox"] = outbox_n
                if downloads:
                    payload["downloads"] = downloads
                self.send_json(payload)
        elif route == "/api/downloads":
            self.send_json({"jobs": downloads_payload()})
        elif route == "/api/franchise":
            # The franchise page holding an entry (phase 4): every season,
            # movie and special, local and online episodes as one list.
            try:
                mid = int(parse_qs(urlparse(self.path).query).get("id", ["0"])[0])
            except ValueError:
                mid = 0
            if mid <= 0:
                self._bad("'id' must be a positive integer")
            else:
                self.send_json(franchise_view(mid))
        elif route.startswith("/img/"):
            name = os.path.basename(route)
            p = POSTERS / name
            self.send_file(p, "image/jpeg", cache=True)
        elif route.startswith("/epimg/"):
            self.send_file(EPIMGS / os.path.basename(route), "image/jpeg", cache=True)
        elif route in ("/", "/index.html"):
            self.send_file(WEB / "index.html", "text/html; charset=utf-8")
        elif route == "/style.css":
            self.send_file(WEB / "style.css", "text/css")
        elif route == "/app.js":
            self.send_file(WEB / "app.js", "text/javascript")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.check_local():
            return
        # Media type parsed properly (TODO 22): a substring check let
        # "text/plain; note=application/json" through — the classic
        # cross-site "simple POST" trick.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_json({"error": "expected application/json"}, 415)
            return
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_json({"error": "invalid Content-Length"}, 400)
            return
        if length < 0:
            # rfile.read(-1) would block the handler thread until EOF.
            self.send_json({"error": "invalid Content-Length"}, 400)
            return
        if length > 1_000_000:
            self.send_json({"error": "request body too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            # Structured 400 (TODO 22): malformed JSON used to become an
            # empty body, whose empty episode id was written into the DB.
            self.send_json({"error": f"invalid JSON body: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self.send_json({"error": "request body must be a JSON object"}, 400)
            return
        if route == "/api/play":
            if self._fields(body, strings=("id",), ints=("version",)):
                mode = body.get("mode")
                if mode is not None and mode not in STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(STREAM_MODES))
                elif mode and body["id"].startswith("stream:"):
                    # An online episode in the other language ("Play dubbed").
                    self.send_json(start_stream(body["id"], mode))
                else:
                    self.send_json(start_play(body["id"], body.get("version", 0)))
        elif route == "/api/settings":
            # TODO 15: automation toggles + tour dismissal (Decision 5).
            # Only known boolean keys; everything else is a structured 400.
            keys = ("auto_progress_sync", "auto_add_watch", "anilist_watched", "vpn_icon", "vpn_warn", "tour_done")
            # ani-cli bridge preferences: closed vocabularies.
            choices = {"stream_quality": STREAM_QUALITIES, "stream_mode": STREAM_MODES}
            for key in keys:
                if key in body and not isinstance(body[key], bool):
                    self._bad("'%s' must be a boolean" % key)
                    return
            for key, allowed in choices.items():
                if key in body and body[key] not in allowed:
                    self._bad("'%s' must be one of: %s" % (key, ", ".join(allowed)))
                    return
            if not any(key in body for key in (*keys, *choices)):
                self._bad("nothing to change — send one of: %s" % ", ".join((*keys, *choices)))
                return
            for key in choices:
                if key in body:
                    set_setting(key, body[key])
            for key in keys:
                if key not in body:
                    continue
                if key == "tour_done":
                    set_setting("tour_done", "1" if body[key] else "0")
                else:
                    set_automation(key, body[key])
            self.send_json({"ok": True, **settings_payload()})
        elif route == "/api/watched":
            if self._fields(body, strings=("id",), bools=("watched",)):
                set_watched(body["id"], body["watched"])
                self.send_json({"ok": True})
        elif route == "/api/bulk-watched":
            # TODO 6 (Decision 27): one mark for a season or whole show —
            # bookkeeping, not playback; the Decision 27 sync rides the
            # outbox afterwards.
            if self._fields(body, strings=("show_id",), bools=("watched",)):
                season = body.get("season")
                if season is not None and (isinstance(season, bool)
                                           or not isinstance(season, int)
                                           or season < 0):
                    self._bad("'season' must be a non-negative integer or null")
                    return
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    self._bad("'ids' must be a list of episode ids")
                    return
                r = bulk_watched(body["show_id"], season, body["watched"], ids)
                self.send_json(r, 200 if r.get("ok") else 404)
        elif route == "/api/entry-watched":
            # The franchise page's marks: one entry's episodes (all, or the
            # listed numbers), file and online alike.
            if self._fields(body, ints=("media_id",), bools=("watched",)):
                nums = body.get("numbers")
                if not body.get("media_id") or body["media_id"] < 0:
                    self._bad("'media_id' must be a positive integer")
                elif nums is not None and (not isinstance(nums, list) or not all(
                        isinstance(n, (int, float)) and not isinstance(n, bool) for n in nums)):
                    self._bad("'numbers' must be a list of episode numbers")
                else:
                    self.send_json(entry_watched(body["media_id"], body["watched"], nums))
        elif route == "/api/reset":
            if self._fields(body, strings=("id",)):
                reset_progress(body["id"])
                self.send_json({"ok": True})
        elif route == "/api/open":
            if self._fields(body, strings=("url",)):
                try:
                    open_in_browser(body["url"])
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"error": str(e)}, 400)
        elif route == "/api/open-folder":
            if self._fields(body, strings=("show_id",)):
                try:
                    open_show_folder(body["show_id"])
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"error": str(e)}, 400)
        elif route == "/api/adopt-root":
            res = adopt_root()
            self.send_json(res, 200 if res.get("ok") else 400)
        elif route == "/api/library-root":
            # Decision 32: choose the library folder from the UI; adopt is
            # the user's confirmed switch away from an indexed root (23.7).
            if self._fields(body, strings=("path",)):
                if "adopt" in body and not isinstance(body["adopt"], bool):
                    self._bad("'adopt' must be a boolean")
                    return
                try:
                    self.send_json(set_library_root(body["path"], body.get("adopt", False)))
                except ValueError as e:
                    self._bad(str(e))
        elif route == "/api/match":
            if self._fields(body, strings=("show_id",)):
                aid = body.get("anilist_id")
                if aid is not None and (isinstance(aid, bool) or not isinstance(aid, int)):
                    self._bad("'anilist_id' must be an integer")
                else:
                    self.send_json(set_match(body["show_id"], aid, body.get("query")))
        elif route == "/api/season-match":
            # One season of a folder → a chosen entry ("wrong entry?"),
            # none: true = no entry, neither = back to the automatic mapping.
            if self._fields(body, strings=("show_id", "season")):
                aid = body.get("anilist_id")
                if aid is not None and (isinstance(aid, bool) or not isinstance(aid, int)):
                    self._bad("'anilist_id' must be an integer")
                else:
                    self.send_json(set_season_pin(body["show_id"], body["season"], aid,
                                                  none=body.get("none") is True))
        elif route == "/api/match-candidates":
            if self._fields(body, strings=("query",)):
                try:
                    self.send_json(fetch_media_candidates(body["query"])[:10])
                except Exception as e:
                    self.send_json({"error": str(e)}, 502)
        elif route == "/api/auth/start":
            r = auth_start()
            self.send_json(r, 200 if r.get("url") else 400)
        elif route == "/api/auth/callback":
            # The redirect page's one-shot POST; token came via the URL
            # fragment (never a server-visible URL), correlation is the
            # pending-flow check inside auth_finish.
            if self._fields(body, strings=("access_token",)):
                ei = body.get("expires_in")
                if ei is not None and (isinstance(ei, bool) or not isinstance(ei, int) or ei <= 0):
                    self._bad("'expires_in' must be a positive integer")
                else:
                    r = auth_finish(body["access_token"], ei, require_pending=True)
                    self.send_json(r, 200 if r.get("ok") else 400)
        elif route == "/api/auth/token":
            # Manual paste fallback (Decision 20): also the expiry recovery.
            if self._fields(body, strings=("token",)):
                r = auth_finish(body["token"], require_pending=False)
                self.send_json(r, 200 if r.get("ok") else 400)
        elif route == "/api/auth/logout":
            self.send_json(auth_disconnect())
        elif route == "/api/completion-seen":
            if self._fields(body, ints=("media_id", "count")):
                _dismiss_completion(body["media_id"], body["count"])
                self.send_json({"ok": True})
        elif route == "/api/reconcile":
            # TODO 16 (Decision 14): connect-time merge. Facts apply inside
            # (automatic — higher watched count wins, both directions);
            # curation conflicts come back for the user to decide.
            try:
                self.send_json(reconcile_analyze())
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
        elif route == "/api/reconcile/apply":
            # TODO 16: curation winners. Facts were already applied during
            # analyze and are not user-decidable (Decision 14).
            decisions = body.get("decisions")
            if not isinstance(decisions, list):
                self._bad("'decisions' must be a list")
                return
            try:
                applied = reconcile_apply(decisions)
            except Exception as e:
                self.send_json({"error": str(e)}, 502)
                return
            threading.Thread(target=anilist_user_data, kwargs={"force": True},
                             daemon=True).start()
            self.send_json({"ok": True, "applied": applied})
        elif route == "/api/reconcile/dismiss":
            reconcile_clear()
            self.send_json({"ok": True})
        elif route == "/api/entry":
            # TODO 9: one editing route — AniList writes through the
            # chokepoint when connected, local curation overlay otherwise
            # (Decision 10's no-AniList nuance, Decision 18.5).
            if not isinstance(body.get("media_id"), int) or isinstance(body.get("media_id"), bool) \
                    or body["media_id"] <= 0:
                self._bad("'media_id' must be an integer")
                return
            patch = body.get("patch") or {}
            fav = body.get("favourite")
            if not isinstance(patch, dict):
                self._bad("'patch' must be an object")
                return
            patch = {k: v for k, v in patch.items()
                     if k in ("status", "score", "progress", "repeat",
                              "started_at", "completed_at")}
            if "patch" in body and not patch and fav is None:
                self.send_json({"error": "empty patch"})
                return
            ref = "aid:%d" % body["media_id"]
            if "status" in patch and patch["status"] not in ENTRY_STATUS:
                self._bad("invalid status")
                return
            if "score" in patch:
                try:
                    patch["score"] = float(patch["score"])
                except (TypeError, ValueError):
                    self._bad("'score' must be a number")
                    return
                if not 0 <= patch["score"] <= 100:  # also rejects nan/inf
                    self._bad("'score' must be between 0 and 100")
                    return
            for k in ("progress", "repeat"):
                if k in patch:
                    if isinstance(patch[k], bool) or not isinstance(patch[k], int) or patch[k] < 0:
                        self._bad("'%s' must be a non-negative integer" % k)
                        return
            # Validate everything before the local write: a rejected field
            # must not leave the overlay disagreeing with AniList.
            for k in ("started_at", "completed_at"):
                if k in patch:
                    if patch[k] in ("", None):
                        patch[k] = None
                    elif not isinstance(patch[k], str) or _fuzzy_date_frag(patch[k]) is None:
                        self._bad("'%s' must be a YYYY[-MM[-DD]] date" % k)
                        return
            if fav is not None and not isinstance(fav, bool):
                self._bad("'favourite' must be a boolean")
                return
            cpatch = {k: v for k, v in patch.items() if k in
                      ("status", "score", "rewatches", "started_at", "finished_at")}
            if "repeat" in patch:
                cpatch["rewatches"] = patch["repeat"]
            if "completed_at" in patch:
                cpatch["finished_at"] = patch["completed_at"]
            if fav is not None:
                cpatch["favourite"] = fav
            curation_set(ref, cpatch)
            fav_result = None
            if load_token():
                try:
                    if patch or fav is not None:
                        result = anilist_entry_save(body["media_id"], patch) \
                            if patch else anilist_entry_state(body["media_id"])
                        if fav is not None and not result.get("error"):
                            fav_result = anilist_favourite_set(body["media_id"], fav)
                            if fav_result and fav_result.get("error"):
                                self.send_json({"error": fav_result["error"]})
                                return
                            if fav_result:
                                result = fav_result
                        if result and not result.get("error"):
                            cpatch2 = {}
                            ent = result.get("entry") or {}
                            if ent.get("status"):
                                cpatch2["status"] = ent["status"]
                            if ent.get("score") is not None:
                                cpatch2["score"] = ent["score"]
                            if ent.get("repeat") is not None:
                                cpatch2["rewatches"] = ent["repeat"]
                            if ent.get("started_at"):
                                cpatch2["started_at"] = ent["started_at"]
                            if ent.get("completed_at"):
                                cpatch2["finished_at"] = ent["completed_at"]
                            if result.get("is_favourite") is not None:
                                # favourite lives top-level on the state, not
                                # inside entry — without this the local
                                # overlay never records AniList favourites.
                                cpatch2["favourite"] = bool(result["is_favourite"])
                            curation_set(ref, cpatch2)
                        self.send_json(result if result else {"error": "unknown failure"})
                        return
                except AniListAuthError as e:
                    # Dormant outbox (Decision 24): the intent is kept,
                    # held until a valid token exists again.
                    outbox_enqueue(body["media_id"], patch=patch, favourite=fav)
                    self.send_json({"error": e.reason, "auth": True,
                                    "queued": True}, 401)
                    return
                except AniListTransientError as e:
                    # Offline / AniList down (TODO 14, Decision 3): the
                    # local change is already committed; the intent waits.
                    outbox_enqueue(body["media_id"], patch=patch, favourite=fav)
                    cur = curation_get(ref) or {}
                    self.send_json({"queued": True, "local": True,
                                    "entry": cur, "reason": str(e)})
                    return
                except ValueError as e:
                    # AniList rejected the mutation itself (HTTP 400 with a
                    # non-auth GraphQL error). The local change stands; the
                    # message goes to the user (D18.4: never queue).
                    self.send_json({"error": str(e), "local": True})
                    return
                except Exception as e:
                    self.send_json({"error": str(e)}, 502)
                    return
            # No-AniList mode (Decision 10): the same fields store locally.
            out = curation_get(ref) or {}
            if fav is not None:
                curation_set(ref, {"favourite": fav})
                out = curation_get(ref)
            self.send_json({"local": True, "entry": out})
        elif route == "/api/refresh":
            self.send_json({"ok": True})
            threading.Thread(target=background_work(refresh_meta), daemon=True).start()
        elif route.startswith("/api/stream/") or route.startswith("/api/download"):
            self._stream_route(route, body)
        else:
            self.send_error(404)

    def _stream_route(self, route, body):
        """The ani-cli bridge's routes. AniCliError is ani-cli (or the site
        behind it) failing — a readable 502, never a traceback."""
        try:
            if route == "/api/stream/search":
                if self._fields(body, strings=("query",)):
                    self.send_json(anicli_search(body["query"]))
            elif route == "/api/stream/link":
                if not self._fields(body, strings=("query",), ints=("media_id", "index")):
                    return
                title, mode, display = body.get("title"), body.get("mode"), body.get("display")
                if body["media_id"] <= 0:
                    self._bad("'media_id' must be a positive integer")
                elif title is not None and not isinstance(title, str):
                    self._bad("'title' must be a string or null")
                elif mode is not None and mode not in STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(STREAM_MODES))
                elif display is not None and not isinstance(display, str):
                    self._bad("'display' must be a string")
                else:
                    self.send_json(stream_link_set(body["media_id"], body["query"], title,
                                                   body.get("index") or 1, mode, display))
            elif route == "/api/stream/mode":
                if not self._fields(body, strings=("mode",), ints=("media_id",)):
                    return
                if body["mode"] not in STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(STREAM_MODES))
                else:
                    self.send_json(stream_switch_mode(body["media_id"], body["mode"]))
            elif route == "/api/download":
                eps = body.get("episodes")
                if not self._fields(body, ints=("media_id",)):
                    return
                if not isinstance(eps, list) or not eps or not all(isinstance(e, str) for e in eps):
                    self._bad("'episodes' must be a non-empty list of episode strings")
                else:
                    self.send_json(download_enqueue(body["media_id"], eps))
            elif route == "/api/download/cancel":
                if self._fields(body, ints=("id",)):
                    self.send_json(download_cancel(body["id"]))
            elif route == "/api/download/clear":
                self.send_json(download_clear())
            else:
                self.send_error(404)
        except AniCliError as e:
            self.send_json({"error": str(e)}, 502)

    def _bad(self, msg):
        self.send_json({"error": msg}, 400)
        return False

    def _fields(self, body, strings=(), ints=(), bools=()):
        """Typed request fields (TODO 22): structured 400s instead of
        silent empty-string writes. Strings must be non-empty; ints reject
        bools; bools must actually be booleans."""
        for name in strings:
            v = body.get(name)
            if not isinstance(v, str) or not v:
                return self._bad(f"missing or invalid '{name}'")
        for name in ints:
            v = body.get(name, 0)
            if isinstance(v, bool) or not isinstance(v, int):
                return self._bad(f"'{name}' must be an integer")
        for name in bools:
            if not isinstance(body.get(name), bool):
                return self._bad(f"'{name}' must be a boolean")
        return True


def open_ui():
    """How the user reaches the UI on startup (Decision 26). "app": a
    Chromium-family app-mode window — a standalone window with no tabs or
    address bar, so the hub feels like a desktop app; falls back to a plain
    browser tab when no such browser exists. "browser": always a plain tab.
    "none" (or --no-browser for one run): nothing opens; the server just
    runs. Closing the window never stops the server."""
    url = f"http://127.0.0.1:{PORT}"
    if WINDOW == "app":
        for name in APP_BROWSERS:
            exe = shutil.which(name)
            if exe:
                subprocess.Popen([exe, f"--app={url}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
    import webbrowser
    webbrowser.open(url)


def main():
    no_browser = "--no-browser" in sys.argv
    for w in CONFIG_WARNINGS:
        print(f"anime-hub: {w}", file=sys.stderr, flush=True)
    for d in (DATA, CACHE, POSTERS, EPIMGS):
        d.mkdir(parents=True, exist_ok=True)
    init_db()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # A bind failure must be readable (found by the cold-machine
        # simulation): the default case is a second instance or a taken
        # configured port, not a Python mystery.
        print(f"anime-hub: cannot bind 127.0.0.1:{PORT} — {e}. "
              f"Is another instance already running, or is the configured 'port' taken?",
              file=sys.stderr, flush=True)
        sys.exit(1)
    threading.Thread(target=background_work(refresh_meta), daemon=True).start()
    # Warm the AniList cache so the spine (TODO 8) is populated on the very
    # first library view; /api/anilist visits keep it fresh afterwards.
    if load_anilist_username():
        threading.Thread(target=anilist_user_data, daemon=True).start()
    # Durable-write flush loop (TODO 14): retries held intents with backoff.
    threading.Thread(target=outbox_loop, daemon=True).start()
    # Spine freshness (TODO 8/17): keep the AniList cache warm in the
    # background — a stale cache must never read as a disconnect.
    threading.Thread(target=background_work(anilist_refresh_loop), daemon=True).start()
    threading.Thread(target=background_work(franchise_refresh_loop), daemon=True).start()
    print(f"Anime Hub → http://127.0.0.1:{PORT}", flush=True)
    print(f"  config   {CONFIG_PATH}{' (missing — defaults in use)' if not CONFIG_PATH.exists() else ''}", flush=True)
    print(f"  library  {LIBRARY or 'not configured — choose a folder in Settings (or set library_root in the config file)'}", flush=True)
    u = load_anilist_username()
    a = auth_status()
    print(f"  anilist  reads as {u or 'not configured (optional)'}; oauth "
          f"{'connected as ' + a['user'] if a.get('connected') else 'not connected'}", flush=True)
    if not no_browser and WINDOW != "none":
        open_ui()
    server.serve_forever()


if __name__ == "__main__":
    main()
