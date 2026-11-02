"""New versions of AnimeHub (Decision 58). A check asks GitHub's release
list, on a click or once a day with the switch on, and never unasked
(Decision 47). An AppImage can replace itself on a click; a zip or a git
copy only gets told how. Nothing installs without that click."""

import hashlib
import json
import os
import platform
import re
import threading
import time
import uuid
from decimal import Decimal
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

from . import VERSION, settings, system, util

RELEASES = "https://api.github.com/repos/Elias8832/AnimeHub/releases?per_page=20"
DOWNLOADS = "https://github.com/Elias8832/AnimeHub/releases/download/"
EVERY = 24 * 3600
MAX_LIST = 2_000_000
MAX_APP = 200_000_000
NO_VPN = "No VPN is on. Turn one on, or switch off Only with a VPN."
UA = {"User-Agent": "anime-hub/%s" % VERSION}
TAG = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?$")

_job = {"state": "idle", "percent": 0, "error": None}
_lock = threading.Lock()


def number(tag):
    """A tag as something to compare, or None for anything else. Decimal:
    this project counts 0.2, 0.25, 0.3, 0.31, so 0.25 comes before 0.3."""
    m = TAG.match(tag or "")
    return (Decimal(f"{m[1]}.{m[2]}"), int(m[3] or 0)) if m else None


def kind():
    """How this copy got here, which is how it gets updated."""
    path = os.environ.get("APPIMAGE")
    if path and os.path.isfile(path):
        return "appimage"
    return "windows" if system.WINDOWS else "git"


def asset_name(how):
    return {"appimage": f"AnimeHub-{platform.machine()}.AppImage", "windows": "AnimeHub-windows-x64.zip"}.get(how)


def pick(releases, current, how):
    """The newest release this copy can use. A zip only comes with x.y
    releases (Decision 41), so a point release says nothing to Windows.
    All releases so far are pre-releases, which is why this reads the list:
    GitHub's "latest" skips them."""
    best = None
    want = asset_name(how)
    for r in releases:
        n = number(r.get("tag_name"))
        if r.get("draft") or n is None or n <= number(current) or (best and n <= best[0]):
            continue
        asset = next((a for a in r.get("assets") or [] if a.get("name") == want), None)
        if want and asset is None:
            continue
        best = (n, r, asset)
    if not best:
        return None
    n, r, asset = best
    found = {"version": r["tag_name"].lstrip("v"), "url": r.get("html_url") or "", "download": None, "sha256": None}
    digest = (asset or {}).get("digest") or ""
    link = (asset or {}).get("browser_download_url") or ""
    if how == "appimage" and digest.startswith("sha256:") and link.startswith(DOWNLOADS):
        found.update(download=link, sha256=digest[7:])
    return found


def stored():
    try:
        found = json.loads(settings.get_setting("update_found") or "null")
    except ValueError:
        return None
    return found if found and number(found.get("version")) and number(found["version"]) > number(VERSION) else None


def vpn_missing():
    """With "only with a VPN" on and none up, nothing goes to GitHub: not the
    daily check, not a click. An unknown VPN state counts as none."""
    return settings.automation_enabled("update_vpn_only") and not settings.vpn_status().get("active")


def check():
    """POST /api/update-check, and the daily loop."""
    if vpn_missing():
        return {"error": NO_VPN}
    try:
        with urlopen(Request(RELEASES, headers={**UA, "Accept": "application/vnd.github+json"}), timeout=15) as r:
            releases = json.loads(r.read(MAX_LIST + 1))
    except (OSError, HTTPException, ValueError):
        return {"error": "Could not reach GitHub."}
    if not isinstance(releases, list):
        return {"error": "GitHub sent something unexpected."}
    found = pick(releases, VERSION, kind())
    settings.set_setting("update_checked", str(int(time.time())))
    settings.set_setting("update_found", json.dumps(found))
    return {"ok": True, "found": found}


def due(now=None):
    if not settings.automation_enabled("update_check") or vpn_missing():
        return False
    try:
        last = int(settings.get_setting("update_checked") or 0)
    except ValueError:
        last = 0
    return (now if now is not None else time.time()) - last >= EVERY


def loop():
    time.sleep(60)
    while True:
        if due():
            result = check()
            if result.get("error"):
                util.log("update check: %s" % result["error"])
        time.sleep(3600)


def payload():
    """Additive in /api/settings."""
    how = kind()
    with _lock:
        job = dict(_job)
    return {"version": VERSION, "kind": how, "check": settings.automation_enabled("update_check"),
            "vpn_only": settings.automation_enabled("update_vpn_only"),
            "asked": settings.get_setting("update_asked") == "1", "found": stored(), "job": job}


def install():
    """POST /api/update-install: only the AppImage, only on a click."""
    found = stored()
    target = Path(os.environ.get("APPIMAGE") or "")
    if kind() != "appimage" or not found or not found.get("download"):
        return {"error": "This copy can't update itself."}
    if vpn_missing():
        return {"error": NO_VPN}
    with _lock:
        if _job["state"] == "working":
            return {"ok": True}
        _job.update(state="working", percent=0, error=None)
    threading.Thread(target=_replace, args=(found, target), daemon=True).start()
    return {"ok": True}


def _replace(found, target):
    """Into a file beside the AppImage, then swap: a running AppImage keeps
    its old file open, and a failure leaves what was there."""
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    error = None
    try:
        sha = hashlib.sha256()
        head, done = b"", 0
        with urlopen(Request(found["download"], headers=UA), timeout=30) as r, open(tmp, "wb") as out:
            size = int(r.headers.get("Content-Length") or 0)
            for chunk in iter(lambda: r.read(1 << 16), b""):
                head = head or chunk[:16]
                done += len(chunk)
                if done > MAX_APP:
                    raise ValueError
                sha.update(chunk)
                out.write(chunk)
                if size:
                    with _lock:
                        _job["percent"] = min(99, done * 100 // size)
        if sha.hexdigest() != found["sha256"] or head[:4] != b"\x7fELF" or head[8:10] != b"AI":
            raise ValueError
        tmp.chmod(0o755)
        os.replace(tmp, target)
    except ValueError:
        error = "That download didn't look right, so nothing changed."
    except PermissionError:
        error = f"AnimeHub can't write to {target.parent}. Download the new version by hand."
    except (OSError, HTTPException):
        error = "The download failed, so nothing changed."
    finally:
        tmp.unlink(missing_ok=True)
    with _lock:
        _job.update(state="failed" if error else "done", percent=0 if error else 100, error=error)
