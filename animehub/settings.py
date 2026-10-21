"""User settings: stored preferences, AniList automations, VPN status, library folder."""

import json
import os
import re
import shutil
from pathlib import Path

from . import anicli, database, library
from .config import config, CONFIG, CONFIG_PATH


def get_setting(key):
    with database.db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(key, value):
    with database.db() as c:
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
        "library_root": str(config.LIBRARY) if config.LIBRARY else None,
        "data_dir": str(config.DATA),
        "config_path": str(CONFIG_PATH),
        "port": config.PORT,
        "window": config.WINDOW,
        "scan": dict(library.scan_state),
        "tools": {t: shutil.which(t) for t in ("mpv", "ani-cli", "upscayl")},
        # ani-cli bridge preferences: quality + default audio for new links.
        "stream": {**anicli.stream_prefs(), "version": anicli.anicli_version(),
                   "qualities": list(anicli.STREAM_QUALITIES)},
    }


def adopt_root():
    """Decision 23.7: adopting a changed library root is an explicit step,
    never a silent scan side effect. Marks the configured root as the
    indexed one, which lifts the root-changed tombstone guard so the next
    scan (GET /api/library re-scans every call) records the old root's rows
    as gone."""
    if config.LIBRARY is None:
        return {"error": "No library folder is configured.", "ok": False}
    set_setting("library_root", str(config.LIBRARY))
    library.scan_state["root"] = "ok"
    return {"ok": True, "library_root": str(config.LIBRARY)}


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
        base = config.LIBRARY if config.LIBRARY and config.LIBRARY.is_dir() else Path.home()
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
    p = _user_dir(path)
    write_config_key("library_root", str(p))
    config.LIBRARY = p
    CONFIG["library_root"] = p
    if adopt:
        set_setting("library_root", str(p))
        library.scan_state["root"] = "ok"
    return {"ok": True, "library_root": str(p)}
