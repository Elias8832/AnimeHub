"""Fonts for the app's text and titles (TODO 122, Decision 69). The user
adds one from the fonts installed on the PC or from a file; either way a
copy lives in <data>/fonts, so a phone gets it too. Which font each device
uses is a device preference (localStorage), like the rest of Appearance."""

import base64
import binascii
import io
import os
import re
import shutil
import struct
import sys
import threading
import time
from pathlib import Path

from . import system
from .config import config

MAX_BYTES = 16_000_000
# What the file starts with → its extension and served type.
KINDS = {b"\x00\x01\x00\x00": ("ttf", "font/ttf"), b"true": ("ttf", "font/ttf"), b"OTTO": ("otf", "font/otf"),
         b"wOFF": ("woff", "font/woff"), b"wOF2": ("woff2", "font/woff2")}
TYPES = {ext: ctype for ext, ctype in KINDS.values()}
# Fonts of icons and signs: they map letters, but to pictures (URW's D050000L is dingbats).
NOT_TEXT = re.compile(r"symbol|dingbat|emoji|icon|\bmath\b|^D050000L$", re.I)
BOLD = "__bold"  # a family's bold file sits beside it: Name.ttf + Name__bold.ttf


class FontError(ValueError):
    pass


def font_dir():
    return config.DATA / "fonts"


# --- reading a font file ---------------------------------------------------

def read_meta(f):
    """What a TTF/OTF says about itself, read from an open binary file
    without loading all of it: {family, style, full, weight, italic, latin,
    variable}, or None (a collection, WOFF, or not a font)."""
    try:
        head = f.read(12)
        if head[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
            return None
        count = struct.unpack(">H", head[4:6])[0]
        tables = {}
        raw = f.read(16 * count)
        for i in range(count):
            tag, _, off, length = struct.unpack(">4sIII", raw[16 * i:16 * i + 16])
            tables[tag] = (off, length)
        if b"name" not in tables:
            return None
        off, length = tables[b"name"]
        f.seek(off)
        name = f.read(min(length, 200_000))
        _, n, strings = struct.unpack(">HHH", name[:6])
        found = {}
        for j in range(n):
            plat, enc, lang, nid, ln, so = struct.unpack(">HHHHHH", name[6 + 12 * j:18 + 12 * j])
            if nid not in (1, 2, 4, 16, 17):
                continue
            text = name[strings + so:strings + so + ln].decode("utf-16-be" if plat in (0, 3) else "latin-1", "ignore").strip()
            rank = 2 if (plat == 3 and lang == 0x409) else 1 if plat in (0, 3) else 0
            if text and rank >= found.get(nid, ("", -1))[1]:
                found[nid] = (text, rank)
        get = lambda k: found.get(k, ("", 0))[0]
        meta = {"family": get(16) or get(1), "style": get(17) or get(2) or "Regular", "full": get(4),
                "weight": 400, "italic": False, "latin": True, "variable": b"fvar" in tables}
        if b"OS/2" in tables:
            f.seek(tables[b"OS/2"][0])
            os2 = f.read(64)
            if len(os2) >= 64:
                meta["weight"] = struct.unpack(">H", os2[4:6])[0]
                meta["latin"] = bool(struct.unpack(">I", os2[42:46])[0] & 1)  # Basic Latin
                meta["italic"] = bool(struct.unpack(">H", os2[62:64])[0] & 1)
        if b"cmap" in tables:
            f.seek(tables[b"cmap"][0])
            meta["latin"] = meta["latin"] and _has_letters(f, tables[b"cmap"][0])
        if re.search(r"italic|oblique", meta["style"], re.I):
            meta["italic"] = True
        return meta if meta["family"] else None
    except (struct.error, IndexError, OSError, ValueError):
        return None


def _has_letters(f, base, letters="AZaz"):
    """Whether the font draws plain Latin letters: a symbol font (dingbats,
    icons) or a font for another script only is no use for the app."""
    head = f.read(4)
    n = struct.unpack(">H", head[2:4])[0]
    subs = {}
    raw = f.read(8 * n)
    for i in range(n):
        plat, enc, off = struct.unpack(">HHI", raw[8 * i:8 * i + 8])
        subs[(plat, enc)] = off
    off = next((subs[k] for k in ((3, 10), (3, 1), (0, 4), (0, 3), (0, 6), (0, 1), (0, 0)) if k in subs), None)
    if off is None:
        return False
    f.seek(base + off)
    fmt = struct.unpack(">H", f.read(2))[0]
    codes = [ord(c) for c in letters]
    if fmt == 4:
        length = struct.unpack(">H", f.read(2))[0]
        data = f.read(length - 4)
        segs = struct.unpack(">H", data[2:4])[0] // 2
        ends = struct.unpack(f">{segs}H", data[10:10 + 2 * segs])
        starts = struct.unpack(f">{segs}H", data[12 + 2 * segs:12 + 4 * segs])
        return all(any(st <= c <= en for st, en in zip(starts, ends) if en != 0xFFFF) for c in codes)
    if fmt == 12:
        f.read(10)
        groups = struct.unpack(">I", f.read(4))[0]
        data = f.read(12 * min(groups, 100_000))
        ranges = [struct.unpack(">II", data[12 * i:12 * i + 8]) for i in range(len(data) // 12)]
        return all(any(st <= c <= en for st, en in ranges) for c in codes)
    return True  # an older table kind: trust the OS/2 bit


def _clean(name):
    name = re.sub(r"\.(ttf|otf|woff2?)$", "", str(name or ""), flags=re.I)
    name = re.sub(r"[^\w .-]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()[:60]


# --- fonts installed on this PC --------------------------------------------

def system_font_dirs():
    home = Path.home()
    if system.WINDOWS:
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Microsoft" / "Windows" / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    data = [os.environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")]
    data += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    return [Path(d) / "fonts" for d in data if d] + [home / ".fonts"]


_cache = {"at": 0.0, "families": None}
_lock = threading.Lock()


def _scan():
    """{family: [(meta, path)]} for every installed TTF/OTF with Latin letters."""
    fams, seen = {}, set()
    for d in system_font_dirs():
        if not d.is_dir():
            continue
        for root, _, files in os.walk(d):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf")):
                    continue
                p = Path(root) / name
                try:
                    key = p.resolve()
                    if key in seen:
                        continue
                    seen.add(key)
                    with open(p, "rb") as f:
                        meta = read_meta(f)
                except OSError:
                    continue
                if meta and meta["latin"] and not meta["family"].startswith(".") and not NOT_TEXT.search(meta["family"]):
                    fams.setdefault(meta["family"], []).append((meta, p))
    return fams


def _families():
    with _lock:
        if _cache["families"] is None or time.time() - _cache["at"] > 300:
            _cache["families"], _cache["at"] = _scan(), time.time()
        return _cache["families"]


def _pick(faces, weight):
    """The upright face nearest a weight (a variable font covers all)."""
    upright = [x for x in faces if not x[0]["italic"]] or faces
    return min(upright, key=lambda x: (0 if x[0]["variable"] else 1, abs(x[0]["weight"] - weight), len(x[0]["style"])))


def system_fonts():
    """[{family, styles}] of the installed fonts, by name."""
    return sorted(({"family": fam, "styles": len(faces)} for fam, faces in _families().items()),
                  key=lambda f: f["family"].lower())


# --- the app's own copies ---------------------------------------------------

def _entry(p):
    bold = p.with_name(p.stem + BOLD + p.suffix)
    meta = None
    if p.suffix.lower() in (".ttf", ".otf"):
        with open(p, "rb") as f:
            meta = read_meta(f)
    out = {"id": p.name, "name": p.stem.replace("_", " "), "url": f"/user-fonts/{p.name}",
           "variable": bool(meta and meta["variable"])}
    if bold.is_file():
        out["bold"] = f"/user-fonts/{bold.name}"
    return out


def all_fonts():
    """[{id, name, url, variable, bold?}] for every added font, by name."""
    out = []
    d = font_dir()
    if d.is_dir():
        for p in d.iterdir():
            if p.suffix.lstrip(".").lower() in TYPES and p.is_file() and not p.stem.endswith(BOLD):
                out.append(_entry(p))
    return sorted(out, key=lambda f: f["name"].lower())


def _save(stem, ext, data):
    """Write data as <stem>.<ext>, or find it already there; a different
    file under that name gets a number. Returns the path."""
    d = font_dir()
    d.mkdir(parents=True, exist_ok=True)
    path, n = d / f"{stem}.{ext}", 2
    while path.exists():
        if path.read_bytes() == data:
            return path  # the same file again: keep the one there
        path, n = d / f"{stem}_{n}.{ext}", n + 1
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    system.replace(tmp, path)
    return path


def import_font(name, data_b64):
    """Save one uploaded font; returns its entry."""
    if not isinstance(data_b64, str) or not data_b64:
        raise FontError("no font file was sent")
    try:
        data = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise FontError("the file didn't arrive whole — try again")
    if len(data) > MAX_BYTES:
        raise FontError("that font is over 16 MB")
    kind = KINDS.get(data[:4])
    if not kind:
        raise FontError("that isn't a font file — use .ttf, .otf, .woff or .woff2")
    meta = read_meta(io.BytesIO(data)) if kind[0] in ("ttf", "otf") else None
    family = _clean(meta and meta["family"]) or _clean(name) or "Font"
    return _entry(_save(family.replace(" ", "_"), kind[0], data))


def add_system_font(family):
    """Copy an installed font family (its regular face, and its bold one
    when that is a separate file) into the app's fonts."""
    faces = _families().get(family) if isinstance(family, str) else None
    if not faces:
        raise FontError("that font isn't installed on this PC")
    reg = _pick(faces, 400)
    if reg[1].stat().st_size > MAX_BYTES:
        raise FontError("that font is over 16 MB")
    stem = (_clean(family) or "Font").replace(" ", "_")
    ext = reg[1].suffix.lstrip(".").lower()
    path = _save(stem, ext, reg[1].read_bytes())
    bold = _pick(faces, 700)
    if not reg[0]["variable"] and bold[1] != reg[1] and bold[0]["weight"] >= 600 and bold[1].stat().st_size <= MAX_BYTES:
        bpath = path.with_name(path.stem + BOLD + "." + bold[1].suffix.lstrip(".").lower())
        if not bpath.exists():
            shutil.copyfile(bold[1], bpath)
    return _entry(path)


def font_path(fid):
    """The file of an added font, or None. Basename only."""
    # ":" too: on Windows "D:x.ttf" joined to a folder is a path on drive D:.
    if not isinstance(fid, str) or "/" in fid or "\\" in fid or ":" in fid or fid.startswith("."):
        return None
    p = font_dir() / fid
    return p if p.suffix.lstrip(".").lower() in TYPES and p.is_file() else None


def delete_font(fid):
    p = font_path(fid)
    if not p or p.stem.endswith(BOLD):
        raise FontError("no such font")
    for b in p.parent.glob(p.stem + BOLD + ".*"):
        b.unlink()
    p.unlink()
