"""New versions of AnimeHub (Decision 58). A check asks GitHub's release
list, on a click or once a day with the switch on, and never unasked
(Decision 47). An AppImage replaces itself on a click, a git copy moves
ahead with git on a click (Decision 78), and a Windows zip copy swaps its
files beside the running app (Decision 79). Nothing installs without that
click, and every way leaves what was there when it fails.

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
import sys
import threading
import time
import uuid
import zipfile
from decimal import Decimal
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

from . import VERSION, settings, system, util
from .config import config

RELEASES = "https://api.github.com/repos/Elias8832/AnimeHub/releases?per_page=100"
DOWNLOADS = "https://github.com/Elias8832/AnimeHub/releases/download/"
# A commit's build number is its Tests run's number (CI's build.json stamp).
RUNS = "https://api.github.com/repos/Elias8832/AnimeHub/actions/workflows/tests.yml/runs?event=push&branch=master&head_sha="
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
# A Windows zip copy (Decision 79): AnimeHub\ holds AnimeHub.exe, python\
# and app\ (this code); the zip has that one folder at its top.
ROOT = REPO.parent
TOP = "AnimeHub/"
LEFT = ".update-"  # what an install leaves beside the app; gone at the next start

_job = {"state": "idle", "percent": 0, "error": None, "note": None}
STARTED = time.time()  # this run's start: the page knows a restart by it
_restart = {"wanted": False}


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
    if system.ANDROID:
        return "android"  # Decision 88: a new APK, which Android installs on a tap
    path = os.environ.get("APPIMAGE")
    if path and os.path.isfile(path):
        return "appimage"
    return "windows" if system.WINDOWS else "git"


def asset_name(how):
    return {"appimage": f"AnimeHub-{platform.machine()}.AppImage", "windows": "AnimeHub-windows-x64.zip",
            "android": "AnimeHub-android.apk"}.get(how)


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
    if how in ("appimage", "windows", "android") and digest.startswith("sha256:") and link.startswith(DOWNLOADS):
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
    if BUILD is None and git_copy():
        _learn_build(releases)
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
            "installable": installable(found, how), "head": head() if BUILD is None else None,
            "git_build": git_build() if BUILD is None else None,
            "started": STARTED}


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


def _remember_build(n, sha):
    settings.set_setting("update_git_build", json.dumps({"number": n, "sha": sha}))


def _learn_build(releases):
    """A git copy has no build stamp. Its commit's build is the newest one,
    or else the number of the Tests run GitHub made for that commit on
    master. Asked during a check only (same consent and VPN rule); a commit
    with no run (a docs-only push, own commits) keeps showing its commit."""
    ok, sha = _git("rev-parse", "HEAD")
    if not ok or not re.fullmatch(r"[0-9a-f]{40}", sha) or git_build() is not None:
        return
    m = next((MARK.search(r.get("body") or "") for r in releases if r.get("tag_name") == UNSTABLE), None)
    if m and sha == m[2]:
        _remember_build(int(m[1]), sha)
        return
    try:
        with urlopen(Request(RUNS + sha, headers={**UA, "Accept": "application/vnd.github+json"}), timeout=15) as r:
            runs = json.loads(r.read(MAX_LIST + 1)).get("workflow_runs") or []
    except (OSError, HTTPException, ValueError, AttributeError):
        return
    nums = [x["run_number"] for x in runs if isinstance(x, dict) and x.get("head_sha") == sha
            and isinstance(x.get("run_number"), int)]
    if nums:
        _remember_build(min(nums), sha)


def git_build():
    """A git copy's build number, only while it is on the commit that build
    was made from (an install of a build, or a check that saw it): a pull
    by hand moves it off, and then only its commit is known."""
    try:
        b = json.loads(settings.get_setting("update_git_build") or "null")
    except ValueError:
        return None
    if not isinstance(b, dict) or not git_copy():
        return None
    ok, sha = _git("rev-parse", "HEAD")
    return b.get("number") if ok and sha and sha == b.get("sha") else None


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
    if how == "windows":
        return bool(found.get("download")) and zip_copy()
    if how == "android":
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
    elif how == "windows":
        threading.Thread(target=_swap_zip, args=(found, ROOT), daemon=True).start()
    elif how == "android":
        threading.Thread(target=_apk, args=(found,), daemon=True).start()
    else:
        threading.Thread(target=_replace, args=(found, Path(os.environ.get("APPIMAGE") or "")), daemon=True).start()
    return {"ok": True}


def restart(busy=None):
    """POST /api/update-restart: start the version just installed. Only
    after an install, and not while something plays or downloads (busy says
    what). The server stops serving, then hands over (hand_over)."""
    with _lock:
        if _job["state"] != "done":
            return {"error": "Install the new version first."}
    if busy:
        return {"error": busy}
    _restart["wanted"] = True
    return {"ok": True}


def restart_wanted():
    return _restart["wanted"]


def restart_command(argv, how=None):
    """What starts this copy again, with no window of its own (the open page
    reloads itself). An AppImage starts its new self: the hub inside the old
    one is still the old code. The others run the hub.py that now holds the
    new code."""
    how = how or kind()
    args = [a for a in argv[1:] if a != "--no-browser"] + ["--no-browser"]
    if how == "appimage":
        return [os.environ["APPIMAGE"], "--hub", *args]
    return [sys.executable, str(REPO / "hub.py"), *args]


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
        else:
            if found.get("build") is not None:
                _remember_build(found["build"], _git("rev-parse", "HEAD")[1])
            if not _git("diff", "--quiet", old, "HEAD", "--", "desktop")[0]:
                note = "The window app changed too: in desktop/, run cargo build --release && ./install.sh."
    with _lock:
        _job.update(state="failed" if error else "done", percent=0 if error else 100, error=error, note=note)


def _fetch(found, path, share=99):
    """The download into path, checked against GitHub's sha256. The first
    bytes come back for a look at what it is. Progress up to share %."""
    sha = hashlib.sha256()
    head, done = b"", 0
    with urlopen(Request(found["download"], headers=UA), timeout=30) as r, open(path, "wb") as out:
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
                    _job["percent"] = min(share, done * share // size)
    if sha.hexdigest() != found["sha256"]:
        raise ValueError
    return head


def _replace(found, target):
    """Into a file beside the AppImage, then swap: a running AppImage keeps
    its old file open, and a failure leaves what was there."""
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    error = None
    try:
        head = _fetch(found, tmp)
        if head[:4] != b"\x7fELF" or head[8:10] != b"AI":
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


def apk_path():
    return config.DATA / "update" / "AnimeHub-android.apk"


# Hands the checked APK to Android's installer, which asks the user (Decision
# 88). The app replaces itself; tests swap it.
def _java_install(path):
    from java import jclass
    jclass("io.github.elias8832.animehub.Updater").install(str(path))


apk_installer = _java_install


def _apk(found):
    """The Android app: download the new APK, check it against GitHub's
    sha256 and that it is an APK (a zip), then Android asks to install it.
    A failure leaves the app as it is."""
    path = apk_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    error = None
    try:
        head = _fetch(found, path)
        if head[:4] != b"PK\x03\x04":
            raise ValueError
        apk_installer(path)
    except ValueError:
        error = "That download didn't look right, so nothing changed."
    except (OSError, HTTPException):
        error = "The download failed, so nothing changed."
    except Exception as e:  # Android's installer refused
        error = f"Android couldn't start the install: {e}"
    if error:
        path.unlink(missing_ok=True)
    with _lock:
        _job.update(state="failed" if error else "done", percent=0 if error else 100, error=error,
                    note=None if error else "Android asks you to install it.")


def zip_copy():
    """This copy came from the Windows zip: AnimeHub.exe and python\\ beside
    app\\, and no git (a Windows checkout is never swapped)."""
    return ((ROOT / "AnimeHub.exe").is_file() and (ROOT / "python").is_dir() and (REPO / "hub.py").is_file()
            and not (REPO / ".git").exists())


def _files(folder):
    """Every file under folder, as "a/b" paths."""
    return {p.relative_to(folder).as_posix(): p for p in folder.rglob("*") if p.is_file()}


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def _same(a, b):
    return a.stat().st_size == b.stat().st_size and _digest(a) == _digest(b)


def _move(src, dst):
    """A move that outlasts a moment's lock (a virus scanner on a new file)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    system.replace(src, dst)


def _unpack(archive, stage):
    """The zip into stage, after a look at every name: one AnimeHub\\ folder
    holding the exe, Python and the hub, and nothing that climbs out."""
    with zipfile.ZipFile(archive) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        for n in names:
            parts = n.split("/")
            if not n.startswith(TOP) or "\\" in n or ":" in n or ".." in parts or "" in parts[1:]:
                raise ValueError
        for need in ("AnimeHub.exe", "app/hub.py", "app/animehub/update.py", "python/python.exe"):
            if TOP + need not in names:
                raise ValueError
        z.extractall(stage)
    return stage / TOP.rstrip("/")


def _swap_zip(found, root):
    """A Windows zip copy (Decision 79). Windows won't let a running program
    be overwritten, but it can be moved: every changed file goes aside into
    a backup folder and the new one takes its place, so the running app
    keeps going and the next start is the new version. python\\ stays; a
    version with another Python says to download it by hand. Any failure
    puts every moved file back."""
    tag = uuid.uuid4().hex[:12]
    archive, stage, aside = root / f"{LEFT}{tag}.zip", root / f"{LEFT}{tag}", root / f"{LEFT}{tag}-old"
    error, moved = None, []
    try:
        _fetch(found, archive, share=80)
        new = _unpack(archive, stage)
        with _lock:
            _job["percent"] = 90
        mine = _files(root / "python")
        for rel, path in _files(new / "python").items():
            if rel not in mine or not _same(path, mine[rel]):
                raise LookupError
        incoming = {rel: path for rel, path in _files(new).items() if not rel.startswith("python/")}
        for rel, path in sorted(incoming.items()):
            target = root / rel
            if target.is_file() and _same(path, target):
                continue
            if target.exists():
                _move(target, aside / rel)
                moved.append((target, aside / rel))
            else:
                moved.append((target, None))
            _move(path, target)
        # Code the new version dropped goes too, or Python could still import it.
        for rel, target in _files(root / "app").items():
            if "app/" + rel not in incoming and "__pycache__" not in rel:
                _move(target, aside / "app" / rel)
                moved.append((None, (target, aside / "app" / rel)))
    except LookupError:
        error = "This version comes with a newer Python, so it can't swap in. Download the zip by hand; your data stays."
    except (ValueError, zipfile.BadZipFile):
        error = "That download didn't look right, so nothing changed."
    except PermissionError:
        error = ("Windows wouldn't let AnimeHub change its files, so nothing changed. Close the player and try again;"
                 " if AnimeHub sits in Program Files, download the zip by hand.")
    except (OSError, HTTPException):
        error = "The download failed, so nothing changed."
    if error and not _put_back(moved):
        error += f" Some old files couldn't go back; they are in {aside}."
    else:
        shutil.rmtree(aside, ignore_errors=True)  # the running exe stays, tidy() takes it next start
    archive.unlink(missing_ok=True)
    shutil.rmtree(stage, ignore_errors=True)
    with _lock:
        _job.update(state="failed" if error else "done", percent=0 if error else 100, error=error)


def _put_back(moved):
    ok = True
    for target, back in reversed(moved):
        try:
            if target is None:  # a dropped file
                _move(back[1], back[0])
            elif back is None:  # a file that wasn't there before
                target.unlink(missing_ok=True)
            else:
                _move(back, target)
        except OSError as e:
            ok = False
            util.log("update: couldn't put back %s: %s" % (back, e))
    return ok


def tidy():
    """At start: what the last install left beside a zip copy (the old
    exe Windows wouldn't let go of while it ran)."""
    if kind() != "windows" or not zip_copy():
        return
    for p in ROOT.glob(LEFT + "*"):
        if not p.is_dir():
            p.unlink(missing_ok=True)
        elif not p.name.endswith("-old") or set(_files(p)) <= {"AnimeHub.exe"}:
            shutil.rmtree(p, ignore_errors=True)  # a backup that holds more is someone's way back
