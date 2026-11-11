"""Artwork sized for the phone that asks (TODO 218, Decision 86).

Upscaled banners are 7600 px wide (1-3 MB); a phone draws them at most
~1500 px wide. A phone's page tells the hub its screen in a cookie (`artw`,
its longest side in device pixels, set by index.html); a picture wider than
the size that screen needs is sent as a smaller copy, and a PNG (some
AniList covers, ~450 KB) as a JPEG. A PC or tablet sends no cookie and gets
every picture as it is: its screen shows the difference, and its link to
the hub is fast. mpv makes the copies: it is already needed for playback.
Copies live in cache/art-sized/, named after the picture and its date, so a
changed picture gets fresh ones. When anything fails, the full picture goes
out as before.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import system, upscale
from .config import config

COOKIE = "artw"
# Copy widths; a screen wider than the last gets the full picture.
TIERS = (2400, 3840)

_dims = {}       # (path, mtime_ns, size) -> (width, is_png) or None
_failed = set()  # copies mpv could not make: the full picture until a restart
_locks = {}
_guard = threading.Lock()
_slots = threading.Semaphore(2)  # a page asks for many at once: two mpv runs at a time
_swept = []


def folder():
    return config.CACHE / "art-sized"


def tier_of(cookie_header):
    """The copy width for the screen a request's cookie names, or None (no
    cookie, or a screen too big for any copy: the full picture)."""
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            try:
                need = int(v)
            except ValueError:
                return None
            if need <= 0:
                return None
            return next((t for t in TIERS if t >= need), None)
    return None


def _info(path, st):
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _dims:
        try:
            data = system.read_bytes(path)
        except OSError:
            return None
        dims = upscale.image_dimensions(data)
        _dims[key] = (dims[0], data[:8] == b"\x89PNG\r\n\x1a\n") if dims else None
        if len(_dims) > 4000:
            _dims.clear()
    return _dims[key]


# Part of every copy's name: raise it when the way copies are made changes,
# and the old ones are made again.
MADE = "b"


def _stamp(st):
    return "%x%x%s" % (st.st_mtime_ns, st.st_size, MADE)


def pick(path, tier):
    """The file to send for `path`: a smaller or JPEG copy when that helps,
    else `path` itself. No tier (a PC, a tablet): always `path`."""
    path = Path(path)
    if not tier:
        return path
    try:
        st = path.stat()
    except OSError:
        return path
    info = _info(path, st)
    if not info:
        return path
    width, png = info
    target = tier if width > tier else None
    if not target and not png:
        return path
    out = folder() / f"{path.name[:-4] if path.name.endswith('.jpg') else path.name}.{target or width}.{_stamp(st)}.jpg"
    if out.is_file():
        return out
    if out.name in _failed or not system.find_program("mpv"):
        return path
    with _guard:
        lock = _locks.setdefault(out.name, threading.Lock())
    with lock, _slots:
        if not out.is_file() and not _make(path, out, target, width):
            _failed.add(out.name)
            return path
    with _guard:
        _locks.pop(out.name, None)
    if not _swept:
        _swept.append(1)
        threading.Thread(target=sweep, daemon=True).start()
    return out


def _make(src, out, target, width):
    exe = system.find_program("mpv")
    if not exe:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name[:-4] + ".part.jpg")  # mpv picks the format by the ending
    # Full-range output: without it the JPEG comes out darker than the
    # picture (and mpv's JPEG encoder refuses some pictures outright).
    # 4:4:4 at about quality 94: full colour detail, where 4:2:0 at 86
    # showed on enlarged covers (Elias, 2026-11-11).
    # update=1: one picture, not a numbered series.
    scale = f"scale={target}:-2:flags=lanczos:" if target else "scale="
    cmd = [exe, "--no-config", "--load-scripts=no", "--really-quiet", "--msg-level=all=no",
           "--audio=no", "--frames=1", "--ovc=mjpeg", "--ovcopts=qmin=1,qmax=2", "--ofopts=update=1",
           f"--vf=lavfi=[{scale}out_range=pc:out_color_matrix=bt601,format=yuvj444p]",
           f"--o={tmp}", str(src)]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **system.quiet())
        data = tmp.read_bytes()
        dims = upscale.image_dimensions(data)
        if not dims or dims[0] != (target or width) or upscale.jpeg_looks_blank(data):
            return False
        system.replace(tmp, out)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def sweep():
    """Copies of pictures that changed or left since go (once a run, after
    the first copy is sent)."""
    try:
        names = os.listdir(folder())
    except OSError:
        return
    for name in names:
        if name.endswith(".part.jpg"):
            continue  # being made right now
        parts = name.rsplit(".", 3)  # <picture without .jpg>.<width>.<stamp>.jpg
        keep = False
        if len(parts) == 4 and parts[3] == "jpg":
            try:
                keep = _stamp((config.POSTERS / (parts[0] + ".jpg")).stat()) == parts[2]
            except OSError:
                pass
        if not keep:
            try:
                (folder() / name).unlink()
            except OSError:
                pass


def clear():
    """Every copy goes (Sharper banners switched off: the originals return)."""
    shutil.rmtree(folder(), ignore_errors=True)
