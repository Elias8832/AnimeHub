"""Config file reading and the live `config` values everything else reads."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from . import system


# Two location roots (PLAN v0.1: config replaces hardcoded constants):
# APP_DIR is where the code lives — bundled assets (web/, mpv/) resolve
# beside hub.py (the parent of this package), so the install tree is
# self-contained. Per-user state (database, cache) lives in the XDG data
# dir, moved by config `data_dir`.
APP_DIR = Path(__file__).resolve().parent.parent
WEB = APP_DIR / "web"
MPV = APP_DIR / "mpv"
SKIP_CREDITS_LUA = MPV / "skip-credits.lua"
DEFAULT_PORT = 8787

CONFIG_KEYS = ("library_root", "port", "data_dir", "window", "anilist_client_id", "upscale_banners",
               "remote_hosts", "library_extra")

# AnimeHub's public AniList OAuth client (redirect: http://localhost:8787/
# auth/callback). Client ids are public by design — no secret involved;
# users on a non-default port register their own and override via config.
DEFAULT_ANILIST_CLIENT_ID = 53857

# Chromium-family browser names for app-mode window launch (Decision 26),
# in preference order.
APP_BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable",
                "google-chrome", "brave", "microsoft-edge")


def _xdg_base(var, fallback, windows_var):
    """The XDG base directory for the given variable; a relative or unset
    variable falls back to the standard home-relative location. On Windows
    (TODO 74) an unset XDG variable means the usual Windows folder instead:
    %APPDATA% for the config, %LOCALAPPDATA% for data and caches."""
    for name in (var, windows_var) if system.WINDOWS else (var,):
        v = os.environ.get(name)
        if v and os.path.isabs(v):
            return Path(v)
    return Path.home() / fallback


def _default_data_dir():
    return _xdg_base("XDG_DATA_HOME", ".local/share", "LOCALAPPDATA") / "anime-hub"


def _default_config_path():
    return _xdg_base("XDG_CONFIG_HOME", ".config", "APPDATA") / "anime-hub" / "config.json"


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
      library_extra    list[(int, Path)] — extra folders with their fixed
                                     numbers (Decision 42): [{"id": 2, "path": …}]
      port             int         — server port
      anilist_client_id int|None  — OAuth client id (Decision 20 implicit grant)
      data_dir         Path|None   — where hub.db/cache live (default: XDG)
      window           str         — "app" | "browser" | "none" (Decision 26)
      remote_hosts     list[str]   — extra Host names the hub answers, e.g. a
                                     Tailscale name proxied by `tailscale
                                     serve` (Decision 36); default none

    anilist_username was removed (found live 2026-10-17: the README's
    example value pinned a stranger's public lists over Elias's connected
    account): the hub follows the connected token's account, period. A
    leftover key draws a deprecation warning and is ignored.
    """
    path = Path(path).expanduser() if path else _default_config_path()
    cfg = {"library_root": None, "port": DEFAULT_PORT,
           "data_dir": None, "window": "app", "anilist_client_id": None,
           "upscale_banners": False, "remote_hosts": [], "library_extra": []}
    warnings = []
    try:
        # utf-8-sig: Windows editors may start the file with a BOM.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return cfg, warnings, path
    except (OSError, ValueError) as e:  # ValueError: bad JSON or not UTF-8
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
    if raw.get("remote_hosts") not in (None, ""):
        h = raw["remote_hosts"]
        if isinstance(h, list) and all(isinstance(x, str) and x.strip() for x in h):
            cfg["remote_hosts"] = [x.strip().lower().rstrip(".") for x in h]
        else:
            warn("remote_hosts", "a list of host names")
    if raw.get("library_extra") not in (None, ""):
        x = raw["library_extra"]
        ok = isinstance(x, list) and all(
            isinstance(e, dict) and isinstance(e.get("id"), int) and not isinstance(e["id"], bool)
            and e["id"] >= 2 and isinstance(e.get("path"), str) and e["path"].strip() for e in x)
        if ok and len({e["id"] for e in x}) == len(x):
            cfg["library_extra"] = [(e["id"], Path(os.path.expanduser(e["path"].strip()))) for e in x]
        else:
            warn("library_extra", 'a list of {"id": 2, "path": "/folder"} with different ids (2 and up)')
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


# The live config values everything else reads. apply_config and
# set_library_root rebind them while the hub runs, so code reads them as
# config.LIBRARY (one shared object) rather than holding its own copy,
# which would go stale after a rebind.
config = SimpleNamespace()


def apply_config(cfg):
    """Bind the config to the `config` values everything else reads.
    Re-derives the per-user paths from data_dir so a relocated data dir
    moves the database, cache and AniList state together (Decision 23.7:
    the identity model is root/config-aware from day one)."""
    config.DATA = cfg["data_dir"] or _default_data_dir()
    config.CACHE = config.DATA / "cache"
    config.POSTERS = config.CACHE / "posters"
    config.EPIMGS = config.CACHE / "epimgs"
    config.DB = config.DATA / "hub.db"
    config.LIBRARY = cfg["library_root"]
    config.EXTRA = tuple(cfg.get("library_extra") or ())
    config.PORT = cfg["port"]
    config.WINDOW = cfg["window"]
    config.REMOTE_HOSTS = tuple(cfg.get("remote_hosts") or ())
    config.ANILIST_CACHE = config.DATA / "anilist_cache.json"
    # OAuth token lives with the other per-user state — outside the repo
    # (Decision 20 / READINESS A5); permissions tightened on every write.
    config.ANILIST_TOKEN_FILE = config.DATA / "anilist_token.json"
    # Opt-in banner upscaler (see store_show_meta): default off.
    config.UPSCALE_BANNERS = bool(cfg.get("upscale_banners"))
    # The bundled client id makes connect a two-click affair on a fresh
    # install (Decision 20's setup-tour shape). It is a public identifier,
    # not a secret — every authorize URL carries it anyway. Its registered
    # redirect is http://localhost:8787/auth/callback, so a non-default
    # port needs a config override with the user's own client id.
    config.ANILIST_CLIENT_ID = cfg.get("anilist_client_id") or DEFAULT_ANILIST_CLIENT_ID


CONFIG_PATH = config_path_from_argv()
CONFIG, CONFIG_WARNINGS, CONFIG_PATH = read_config(CONFIG_PATH)
apply_config(CONFIG)
