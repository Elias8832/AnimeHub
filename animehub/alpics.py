"""AniList's own PNG pictures, as JPEGs for a phone (TODO 225, Decision 90).

Shows on your lists that aren't on the PC have no picture here: the page
loads their cover straight from AniList. About a third of those covers are
PNGs of ~450 KB (a JPEG cover is ~100 KB). A phone's page asks the hub for
such a PNG instead (`/alpic/<AniList's path>`): the hub fetches it once,
makes a JPEG the same size the way artsize does (same look as Decision 86's
copies) and keeps it in cache/al-pics/. A picture with see-through parts
stays a PNG: a JPEG has no such thing. Whenever a copy can't be had, the
answer is a redirect to AniList's own link, so the picture always shows.

Only AniList's picture paths are fetched, never a link the page hands in.
"""

import os
import re
import subprocess
import threading
import time
import uuid

from . import artsize, metadata, system, upscale
from .config import config

BASE = "https://s4.anilist.co/file/anilistcdn/"
PATH = re.compile(r"media/anime/(?:cover/(?:medium|large|extraLarge)|banner)/[A-Za-z0-9_-]{1,80}\.png")
KEEP_DAYS = 120  # a copy nobody asked for in this long goes; it is made again when asked
# The alpha check shrinks the picture to 32x32; a block this opaque or more
# has nothing to see through (edges of a few faint pixels still pass).
OPAQUE = 252

_failed = set()  # copies mpv could not make: AniList's own until a restart
_locks = {}
_guard = threading.Lock()
_slots = threading.Semaphore(3)
_swept = []


def folder():
    return config.CACHE / "al-pics"


def can():
    """Whether this hub can make the copies (it needs mpv)."""
    return bool(system.find_program("mpv"))


def source(rel):
    """AniList's link for a picture path, or None when it isn't one."""
    return BASE + rel if PATH.fullmatch(rel or "") else None


def kept(rel):
    """Whether this picture is known to stay AniList's PNG (see-through)."""
    return bool(source(rel)) and (folder() / (_name(rel) + ".keep")).is_file()


def _name(rel):
    return rel[:-4].replace("/", "_") + f".{artsize.MADE}"


def copy(rel):
    """The JPEG copy of AniList's PNG at `rel`, made on first ask. None:
    send AniList's own (not a picture path, see-through, or it failed)."""
    url = source(rel)
    if not url:
        return None
    name = _name(rel)
    out, keep = folder() / (name + ".jpg"), folder() / (name + ".keep")
    if out.is_file():
        return out
    if keep.is_file() or name in _failed or not can():
        return None
    with _guard:
        lock = _locks.setdefault(name, threading.Lock())
    try:
        with lock, _slots:
            if out.is_file():
                return out
            if keep.is_file():
                return None
            made = _make(url, out, keep)
            if made is False:
                _failed.add(name)
            return out if made else None
    finally:
        with _guard:
            _locks.pop(name, None)
        if not _swept:
            _swept.append(1)
            threading.Thread(target=sweep, daemon=True).start()


def _make(url, out, keep):
    """True: `out` is there. None: not this time (AniList unreachable) or
    never (see-through, `keep` written). False: mpv couldn't."""
    body = metadata._fetch_image(url)
    if not body or body[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    dims = upscale.image_dimensions(body)
    if not dims:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_name(f".{uuid.uuid4().hex}.png")
    try:
        src.write_bytes(body)
        opaque = _opaque(src)
        if opaque is None:
            return False
        if not opaque:
            keep.write_bytes(b"")
            return None
        return artsize._make(src, out, None, dims[0])
    except OSError:
        return False
    finally:
        try:
            src.unlink()
        except OSError:
            pass


def _opaque(src):
    """Whether nothing shows through the PNG at `src`; None when mpv can't
    say. Its alpha, shrunk to 32x32 grey: each byte is a block's average."""
    exe = system.find_program("mpv")
    raw = src.with_name(src.name[:-4] + ".alpha")
    cmd = [exe, "--no-config", "--load-scripts=no", "--really-quiet", "--msg-level=all=no", "--audio=no",
           "--frames=1", "--of=rawvideo", "--ovc=rawvideo",
           "--vf=lavfi=[format=rgba,alphaextract,scale=32:32:flags=area,format=gray]", f"--o={raw}", str(src)]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **system.quiet())
        data = raw.read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            raw.unlink()
        except OSError:
            pass
    return min(data) >= OPAQUE if len(data) == 32 * 32 else None


def sweep():
    """Old copies and anything a stopped run left go (once a run)."""
    try:
        names = os.listdir(folder())
    except OSError:
        return
    old = time.time() - KEEP_DAYS * 86400
    for name in names:
        path = folder() / name
        try:
            stray = name.startswith(".") or not name.endswith((f".{artsize.MADE}.jpg", f".{artsize.MADE}.keep"))
            if (stray and path.stat().st_mtime < time.time() - 600) or path.stat().st_mtime < old:
                path.unlink()
        except OSError:
            pass
