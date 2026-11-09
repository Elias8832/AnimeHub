"""New versions of AnimeHub (Decision 58). A check asks GitHub's release
list, on a click or once a day with the switch on, and never unasked
(Decision 47). An AppImage replaces itself on a click, a git copy moves
ahead with git on a click (Decision 78); a zip only gets told how.
Nothing installs without that click.

Two channels (Decision 76): Stable is every numbered release, Unstable is
the one rolling "unstable" release CI rebuilds after each green push to
master. CI stamps its builds with build.json (number, commit)."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from decimal import Decimal
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

from . import VERSION, settings, system, util

RELEASES = "https://api.github.com/repos/Elias8832/AnimeHub/releases?per_page=100"
DOWNLOADS = "https://github.com/Elias8832/AnimeHub/releases/download/"
EVERY = 24 * 3600
MAX_LIST = 2_000_000
MAX_APP = 200_000_000
NO_VPN = "No VPN is on. Turn one on, or switch off Only with a VPN."
UA = {"User-Agent": "anime-hub/%s" % VERSION}
TAG = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?$")
CHANNELS = ("stable", "unstable")
UNSTABLE = "unstable"
# The rolling release's notes end with this line, written by CI last.
MARK = re.compile(r"<!--\s*build (\d+) ([0-9a-f]{7,40}) (\S+)\s*-->")
# A git copy (Decision 78): this checkout, which follows origin's master.
REPO = Path(__file__).resolve().parent.parent
BRANCH = "master"

_job = {"state": "idle", "percent": 0, "error": None, "note": None}


def _build():
    """This copy's CI stamp, or None for a copy no CI built (git)."""
    try:
        b = json.loads((Path(__file__).parent / "build.json").read_text())
        return {"number": int(b["number"]), "commit": str(b["commit"])[:40]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


BUILD = _build()
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
    version = r["tag_name"].lstrip("v")
    found = {"version": version, "label": "v" + version, "channel": "stable", "published": r.get("published_at") or "",
             "tag": r["tag_name"]}
    return _offer(found, r, asset, how)


def _offer(found, r, asset, how):
    found.update(url=r.get("html_url") or "", download=None, sha256=None)
    digest = (asset or {}).get("digest") or ""
    link = (asset or {}).get("browser_download_url") or ""
    if how == "appimage" and digest.startswith("sha256:") and link.startswith(DOWNLOADS):
        found.update(download=link, sha256=digest[7:])
    return found


def pick_unstable(releases, current, build, how, has=None):
    """The newest build for the Unstable channel: the rolling release when
    it is newer than this copy, or a numbered release made after it (a
    release can land while master has no new build yet). A build is newer
    than a build by its number, and than a release by its time. A git copy
    passes has(commit), and a build is new when its commit isn't in it."""
    want = asset_name(how)
    own = build["number"] if build else None
    mine = next((r.get("published_at") or "" for r in releases if number(r.get("tag_name")) == number(current)), "")
    roll = None
    for r in releases:
        m = MARK.search(r.get("body") or "")
        if r.get("tag_name") != UNSTABLE or r.get("draft") or not m:
            continue
        asset = next((a for a in r.get("assets") or [] if a.get("name") == want), None)
        if want and asset is None:
            continue
        n, commit, when = int(m[1]), m[2], m[3]
        newer = (not has(commit)) if has else (n > own) if own is not None else (when > mine)
        if newer:
            found = {"version": current, "label": f"Build {n}", "channel": UNSTABLE, "build": n,
                     "commit": commit[:7], "sha": commit, "published": when}
            roll = _offer(found, r, asset, how)
    stable = pick(releases, current, how)
    if stable and (not roll or stable["published"] > roll["published"]):
        return stable
    return roll


def _commit(found):
    """A found build's commit: the whole sha, or the short one a check made
    before Decision 78 kept it whole (git finds a commit by either)."""
    return found.get("sha") or found.get("commit")


def channel():
    c = settings.get_setting("update_channel")
    return c if c in CHANNELS else "stable"


def stored():
    """What the last check found, while it is still news: this channel's,
    and newer than this copy (which it isn't after updating)."""
    try:
        found = json.loads(settings.get_setting("update_found") or "null")
    except ValueError:
        return None
    if not isinstance(found, dict) or found.get("channel", "stable") != channel():
        return None
    if found.get("build") is not None:
        if BUILD is not None:
            return found if found["build"] > BUILD["number"] else None
        # A git copy that has the commit is that build, unless it got it by
        # the click just now: then the row stays to say "open it again".
        with _lock:
            just = _job["state"] == "done"
        return None if _commit(found) and not just and git_copy() and has_commit(_commit(found)) else found
    n = number(found.get("version"))
    return found if n and n > number(VERSION) else None


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
    how = kind()
    if channel() == UNSTABLE:
        found = pick_unstable(releases, VERSION, BUILD, how, has_commit if BUILD is None and git_copy() else None)
    else:
        found = pick(releases, VERSION, how)
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
    found = stored()
    return {"version": VERSION, "kind": how, "check": settings.automation_enabled("update_check"),
            "channel": channel(), "build": BUILD,
            "vpn_only": settings.automation_enabled("update_vpn_only"),
            "asked": settings.get_setting("update_asked") == "1", "found": found, "job": job,
            "installable": installable(found, how), "head": head() if BUILD is None else None}


def _git(*args, timeout=15):
    """git in this copy: (worked, what it printed)."""
    exe = shutil.which("git")
    if not exe:
        return False, ""
    try:
        r = subprocess.run([exe, "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **system.quiet())
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()


def git_copy():
    """A copy run from a git checkout with git at hand (Decision 78)."""
    return kind() == "git" and (REPO / ".git").exists() and shutil.which("git") is not None


def head():
    """A git copy's own commit, short, so the Version row can name it."""
    if not git_copy():
        return None
    ok, out = _git("rev-parse", "--short=7", "HEAD")
    return out if ok and out else None


def has_commit(sha):
    """This copy has that commit already. A commit it hasn't fetched yet
    isn't in it, which is the usual case for a new build."""
    return _git("merge-base", "--is-ancestor", sha, "HEAD")[0]


def installable(found, how=None):
    """One click can install it: an AppImage with a checked download, or a
    git copy (a build by its commit, a release by its tag)."""
    how = how or kind()
    if not found:
        return False
    if how == "appimage":
        return bool(found.get("download"))
    return how == "git" and git_copy() and (found.get("build") is None or bool(_commit(found)))


def install():
    """POST /api/update-install: an AppImage or a git copy, only on a click."""
    found = stored()
    how = kind()
    if not installable(found, how):
        return {"error": "This copy can't update itself."}
    if vpn_missing():
        return {"error": NO_VPN}
    with _lock:
        if _job["state"] == "working":
            return {"ok": True}
        _job.update(state="working", percent=0, error=None, note=None)
    if how == "git":
        threading.Thread(target=_pull, args=(found,), daemon=True).start()
    else:
        threading.Thread(target=_replace, args=(found, Path(os.environ.get("APPIMAGE") or "")), daemon=True).start()
    return {"ok": True}


def _pull(found):
    """A git copy moves ahead (Decision 78): only master, only with no
    changes of its own, and only forward (--ff-only), so nothing of yours is
    ever overwritten or merged. Untracked files stay as they are."""
    error = note = None
    ok, branch = _git("symbolic-ref", "--quiet", "--short", "HEAD")
    ok_st, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if not ok or branch != BRANCH:
        error = f"This copy isn't on {BRANCH}, so it can't update itself. Switch to {BRANCH} first."
    elif not ok_st or dirty:
        error = "This copy has changes of its own (git status shows them). Commit or undo them, then try again."
    else:
        old = _git("rev-parse", "HEAD")[1]
        if _commit(found):
            fetch, target = ["origin", BRANCH], _commit(found)
        else:
            tag = found.get("tag") or "v" + found["version"]
            fetch, target = ["--no-tags", "origin", f"+refs/tags/{tag}:refs/tags/{tag}"], f"refs/tags/{tag}"
        with _lock:
            _job["percent"] = 10
        if not _git("fetch", "--quiet", *fetch, timeout=300)[0]:
            error = "git couldn't download the new version, so nothing changed. Check your connection and try again."
        elif not _git("rev-parse", "--verify", "--quiet", target + "^{commit}")[0]:
            error = "git couldn't find that version on GitHub, so nothing changed."
        elif not _git("merge", "--ff-only", "--quiet", target + "^{commit}", timeout=120)[0]:
            error = "This copy has commits of its own, so git can't simply move it ahead. Run git pull yourself."
        elif not _git("diff", "--quiet", old, "HEAD", "--", "desktop")[0]:
            note = "The window app changed too: in desktop/, run cargo build --release && ./install.sh."
    with _lock:
        _job.update(state="failed" if error else "done", percent=0 if error else 100, error=error, note=note)


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
