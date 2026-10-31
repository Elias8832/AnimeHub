"""User settings: stored preferences, AniList automations, VPN status, library folder."""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from . import anicli, background, database, lengths, library, system, upscale
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
                       "anilist_watched": False, "vpn_icon": True, "vpn_warn": True,
                       # Opening/ending times from AniSkip (Decision 39): it
                       # would learn what you watch and when, so opt-in; and
                       # without a VPN, ask before an episode uses it.
                       "aniskip": False, "aniskip_vpn_warn": True,
                       # Get the next online episode ready before the click (prepare.py).
                       "prepare_online": True,
                       # Read each episode's length from its file (lengths.py).
                       "measure_lengths": True,
                       # Pick the show's last audio/subtitle track again (tracks.py).
                       "remember_tracks": True,
                       # Openings/endings as marks on mpv's timeline (Decision 45).
                       "mpv_marks": True}


# Opening/ending skipping in mpv (Decision 39): off, a prompt (Enter skips)
# or automatic, one choice for openings and one for endings.
SKIP_MODES = ("off", "prompt", "auto")


# mpv options the hub can set (Decision 45): each is on, off, or "own" =
# left to the user's mpv.conf. Setting → (default, flag when on, flag when off).
MPV_MODES = ("on", "off", "own")
MPV_OPTIONS = {"mpv_fullscreen": ("on", "--fs", "--no-fs")}


def mpv_mode(key):
    v = get_setting(key)
    return v if v in MPV_MODES else MPV_OPTIONS[key][0]


def mpv_flags():
    """The hub's flags on top of the user's mpv.conf, one per option not
    left to it. Flags beat mpv.conf for that option only."""
    out = []
    for key, (_, on, off) in MPV_OPTIONS.items():
        mode = mpv_mode(key)
        if mode != "own":
            out.append(on if mode == "on" else off)
    return out


def skip_prefs():
    out = {}
    for kind in ("op", "ed"):
        v = get_setting("skip_" + kind)
        out[kind] = v if v in SKIP_MODES else "prompt"
    return out


def automation_enabled(key):
    default = AUTOMATION_DEFAULTS[key]
    raw = get_setting(key)
    return default if raw is None else raw == "1"


def set_automation(key, on):
    set_setting(key, "1" if on else "0")


VPN_IFACE_RE = re.compile(r"^(wg|tun|tap|ppp|ipsec|proton|pvpn|nordlynx|nordtun|mullvad|expressvpn|surfshark|cscotun|gpd|utun)", re.I)
VPN_IFACE_SKIP = re.compile(r"^(lo|tailscale|zt|docker|br-|veth|virbr|vboxnet|vmnet)", re.I)
# Windows names adapters by product ("ProtonVPN", "Mullvad"), and
# WireGuard/OpenVPN adapters after the user's tunnel, so the driver's
# description counts too. Tailscale rides Wintun as well: skip wins.
VPN_ADAPTER_RE = re.compile(r"vpn|wireguard|wintun|tap-windows|nordlynx|mullvad|proton|surfshark|cisco anyconnect|globalprotect|fortinet", re.I)
VPN_ADAPTER_SKIP = re.compile(r"tailscale|zerotier|loopback|hyper-v|virtualbox|vmware|wsl|docker|bluetooth", re.I)


def vpn_status():
    """Best-effort VPN check for the online-episode warning: an up network
    interface that is a tunnel — WireGuard/tun devices have no hardware
    address (ARPHRD_NONE, type 65534) or a VPN client's usual name. Mesh
    networks (Tailscale, ZeroTier) and container bridges don't count."""
    if system.WINDOWS:
        return _vpn_status_windows()
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


def site_ok():
    """May the hub reach the streaming site before anyone clicked (linking,
    list refreshes, preparing)? The same rule as the play click's warning:
    with a VPN up, on a system that can't tell, or with the warning off."""
    vpn = vpn_status()
    return vpn["active"] or not vpn["known"] or not automation_enabled("vpn_warn")


def _vpn_status_windows():
    """vpn_status on Windows (TODO 74): an up adapter whose name or driver
    description reads as a VPN client's."""
    try:
        adapters = system.windows_adapters()
    except OSError:
        return {"active": False, "iface": None, "known": False}
    for name, desc, up in adapters:
        text = f"{name} {desc}"
        if up and not VPN_ADAPTER_SKIP.search(text) and VPN_ADAPTER_RE.search(text):
            return {"active": True, "iface": name, "known": True}
    return {"active": False, "iface": None, "known": True}


def settings_payload():
    """TODO 15: the settings surface's read side — the automation toggles
    (Decision 5), the running config as plain values (paths as strings,
    never the token), scan health, and the detected tools the README
    promises. The library root is chosen in the UI's folder picker
    (Decision 32: /api/browse + /api/library-root, written back to the
    config file); adopting a changed root stays explicit (Decision 23.7)."""
    up = upscale.payload()
    return {
        "auto_progress_sync": automation_enabled("auto_progress_sync"),
        "auto_add_watch": automation_enabled("auto_add_watch"),
        "anilist_watched": automation_enabled("anilist_watched"),
        "vpn_icon": automation_enabled("vpn_icon"),
        "vpn_warn": automation_enabled("vpn_warn"),
        "prepare_online": automation_enabled("prepare_online"),
        "aniskip": automation_enabled("aniskip"),
        "aniskip_vpn_warn": automation_enabled("aniskip_vpn_warn"),
        "upscale_banners": up["on"],
        "measure_lengths": automation_enabled("measure_lengths"),
        "remember_tracks": automation_enabled("remember_tracks"),
        # additive (Decision 45): mpv options, "on" / "off" / "own"
        "mpv": {**{key[4:]: mpv_mode(key) for key in MPV_OPTIONS}, "marks": automation_enabled("mpv_marks")},
        # additive (Decision 51): the server without the window; only the desktop app can
        "background_ok": background.launcher() is not None,
        "keep_running": config.KEEP_RUNNING,
        "start_at_login": config.START_AT_LOGIN,
        "tour_done": get_setting("tour_done") == "1",
        # additive: the first-play explainer has been seen
        "play_tip_done": get_setting("play_tip_done") == "1",
        # additive: opening/ending skip modes, {"op": …, "ed": …}
        "skip": skip_prefs(),
        "library_root": str(config.LIBRARY) if config.LIBRARY else None,
        # additive (Decision 42): extra folders, [{id, path, state ok/missing}]
        "library_extra": [{"id": n, "path": str(p), "state": "ok" if p.is_dir() else "missing"}
                          for n, p in config.EXTRA],
        "data_dir": str(config.DATA),
        "config_path": str(CONFIG_PATH),
        "port": config.PORT,
        "window": config.WINDOW,
        "scan": dict(library.scan_state),
        "tools": {"mpv": system.find_program("mpv"), "ani-cli": anicli.anicli_path(), "upscayl": up["found"]},
        # additive: banner upscaling — on/off, where Upscayl is, worker progress
        "upscale": up,
        # additive: episode lengths read from the files — on/off, tool, progress
        "lengths": lengths.payload(),
        # additive: the tour's last page says "mpv 0.39 found"
        "mpv_version": mpv_version(),
        # ani-cli bridge preferences: quality + default audio for new links.
        "stream": {**anicli.stream_prefs(), "version": anicli.anicli_version(),
                   "check": anicli.anicli_check(), **anicli.anicli_status(),
                   "qualities": list(anicli.STREAM_QUALITIES)},
    }


_mpv_versions = {}


def mpv_version():
    """"0.39.0" from `mpv --version` (asked once per mpv path), or None."""
    exe = system.find_program("mpv")
    if not exe:
        return None
    if exe not in _mpv_versions:
        try:
            out = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=10, **system.quiet()).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        m = re.match(r"mpv\s+v?(\d[\w.+-]*)", out.strip())
        _mpv_versions[exe] = m.group(1) if m else None
    return _mpv_versions[exe]


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
        raise ValueError("Enter a full folder path, e.g. %s." % (r"D:\Anime" if system.WINDOWS else "/home/you/Anime"))
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
    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base else None,
        "dirs": [{"name": d, "path": str(base / d)} for d in dirs],
        "places": [{"label": label, "path": str(p)} for label, p in system.browse_places()],
    }


def write_config_key(key, value):
    """Set one key in the config file, keeping every other key (unknown
    ones included) as the user wrote them. A missing file is created; an
    unreadable one is refused rather than overwritten — it is the user's
    file. Written via a temp file + rename so a crash never truncates it."""
    path = CONFIG_PATH
    try:
        # utf-8-sig: Windows editors may start the file with a BOM.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:  # ValueError: bad JSON or not UTF-8
        raise ValueError(f"Could not read the config file {path} ({e}) — fix or remove it first.")
    if not isinstance(raw, dict):
        raise ValueError(f"The config file {path} must contain a JSON object — fix or remove it first.")
    raw[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    system.replace(tmp, path)


def set_upscale(on):
    """Settings' banner-upscaling switch: saved as 'upscale_banners' in the
    config file (where it always lived), applied at once. Off puts the
    AniList originals back (after the run in progress, if any)."""
    write_config_key("upscale_banners", bool(on))
    config.UPSCALE_BANNERS = bool(on)
    if on:
        upscale.turned_on()
    else:
        threading.Thread(target=upscale.restore_originals, daemon=True).start()


def set_background(key, on):
    """Settings' two server switches (Decision 51). They live in the config
    file because the desktop wrapper reads them, and it can't read the
    database. The wrapper picks up a change within a second."""
    if not background.launcher():
        raise ValueError("Only the desktop app can do this.")
    if key == "start_at_login":
        try:
            background.set_login(on)
        except (OSError, subprocess.SubprocessError) as e:
            raise ValueError(f"Could not change the login entry: {e}")
    write_config_key(key, bool(on))
    setattr(config, key.upper(), bool(on))


def _write_extra(extra):
    write_config_key("library_extra", [{"id": n, "path": str(p)} for n, p in extra])
    config.EXTRA = tuple(extra)
    CONFIG["library_extra"] = list(extra)


def add_library_extra(path):
    """Decision 42: one more library folder. It gets the next unused
    number — or its old one, when the same folder was added before — so
    its shows keep their history. Folders inside each other are refused
    (their files would show up twice)."""
    if config.LIBRARY is None:
        raise ValueError("Choose the main library folder first.")
    p = _user_dir(path)
    for _, other in library.roots():
        a, b = p.resolve(), other.resolve()
        if a == b:
            raise ValueError("That folder is already in your library.")
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError(f"That folder is inside {other} or holds it — pick one that isn't.")
    try:
        known = json.loads(get_setting("library_extra_known") or "{}")
    except ValueError:
        known = {}
    used = {n for n, _ in config.EXTRA} | set(known.values())
    n = known.get(str(p)) if known.get(str(p)) not in {m for m, _ in config.EXTRA} else None
    n = n or max(used | {1}) + 1
    known[str(p)] = n
    set_setting("library_extra_known", json.dumps(known))
    _write_extra([*config.EXTRA, (n, p)])
    return {"ok": True, "id": n, "path": str(p)}


def remove_library_extra(n):
    """Stop scanning an extra folder. Its watch history stays (17.3) and
    comes back if the folder is added again."""
    if n not in {m for m, _ in config.EXTRA}:
        raise ValueError("No such extra folder.")
    _write_extra([(m, p) for m, p in config.EXTRA if m != n])
    return {"ok": True}


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
