"""Optional banner upscaling through Upscayl (Flatpak, a normal install, or
Windows): one banner at a time on a background worker."""

import json
import os
import shutil
import subprocess
import threading
import time
import zlib
from collections import deque
from pathlib import Path

from . import system
from .config import config


UPSCAYL_FLATPAK = "org.upscayl.Upscayl"
# Two looks, picked in Settings (setting 'upscale_style'): Natural =
# high-fidelity-4x, keeps soft shading as it is (~2 s a banner); Digital art
# = digital-art-4x with TTA (8 passes, ~6 s), crisper lines — Upscayl's own
# Digital Art + TTA Mode. upscayl-standard-4x (used until 2026-10-26)
# flattened dark gradients into blotches and shifted colours — Elias saw it
# on Home's up-next card and continue tiles.
STYLES = {"natural": ("high-fidelity-4x", False), "digital": ("digital-art-4x", True)}
DEFAULT_STYLE = "natural"
JPEG_QUALITY = "95"


def style():
    from . import settings  # settings imports this module
    v = settings.get_setting("upscale_style")
    return v if v in STYLES else DEFAULT_STYLE


def model_id(name=None):
    """What the record stores: the model, plus "+tta" for the 8-pass mode."""
    model, tta = STYLES[name or style()]
    return model + ("+tta" if tta else "")
# AniList banners are ~1900 px wide; a 4x run makes them ~7600. A banner
# narrower than this is an original straight from AniList.
UPSCALED_WIDTH = 3000


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


def jpeg_looks_blank(data):
    """True for a JPEG of one flat colour — what Upscayl writes when the GPU
    run fails (found again 2026-10-26: a Flatpak NVIDIA driver older than the
    system's gave all-black 7600×1600 banners while reporting success). A
    flat picture's compressed image data repeats the same few bits, so zlib
    shrinks it ~97×; real banners shrink ≤1.3×, real upscales ≤4.2× (a
    mostly plain banner; 34 older upscales ≤2.7×). Fast, stdlib-only."""
    i = data.find(b"\xff\xda")
    scan = data[i:] if i >= 0 else b""
    if len(scan) < 1024:
        return True
    return len(scan) / len(zlib.compress(scan, 1)) > 20


def upscale_looks_sane(src_bytes, out_bytes):
    """Guard for the banner upscaler (TODO 37: six real banners were once
    replaced by black voids, one batch even at a runaway 27440×5776): the
    output must be a 4x-reach of the source's size and not one flat colour."""
    src, out = jpeg_dimensions(src_bytes), jpeg_dimensions(out_bytes)
    if not src or not out:
        return False
    return (src[0] // 2 <= out[0] <= src[0] * 4.5 + 8 and
            src[1] // 2 <= out[1] <= src[1] * 4.5 + 8 and
            not jpeg_looks_blank(out_bytes))


def _install_dirs():
    """Where Upscayl's own installers put their `resources` folder."""
    if system.WINDOWS:
        dirs = [Path(os.environ[v]) / "Upscayl" / "resources"
                for v in ("PROGRAMFILES", "PROGRAMW6432") if os.environ.get(v)]
        if os.environ.get("LOCALAPPDATA"):
            dirs.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Upscayl" / "resources")
        return dirs
    # Linux packages (AUR, .deb, .rpm) keep Electron's layout under /opt or /usr/lib.
    return [Path(p) / "resources" for p in ("/opt/Upscayl", "/opt/upscayl", "/usr/lib/upscayl", "/usr/share/upscayl")]


def _find_native():
    exe = "upscayl-bin.exe" if system.WINDOWS else "upscayl-bin"
    for res in _install_dirs():
        if (res / "bin" / exe).is_file() and (res / "models").is_dir():
            return {"cmd": [str(res / "bin" / exe)], "models": str(res / "models"),
                    "where": str(res / "bin" / exe)}
    # A bare upscayl-bin on PATH, its models beside it or in share/.
    on_path = shutil.which("upscayl-bin")
    if on_path:
        base = Path(on_path).resolve().parent
        for models in (base / "models", base.parent / "models", base.parent / "share" / "upscayl" / "models"):
            if models.is_dir():
                return {"cmd": [on_path], "models": str(models), "where": on_path}
    return None


def _find_flatpak():
    if system.WINDOWS or not shutil.which("flatpak"):
        return None
    try:
        r = subprocess.run(["flatpak", "info", UPSCAYL_FLATPAK], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {"cmd": ["flatpak", "run", "--command=/app/upscayl/resources/bin/upscayl-bin", UPSCAYL_FLATPAK],
            "models": "/app/upscayl/resources/models", "where": "Flatpak " + UPSCAYL_FLATPAK}


_found = {"at": 0.0, "value": None}


def find_upscayl(fresh=False):
    """{cmd, models, where} for the installed Upscayl, or None. A normal
    install first (no sandbox to start), then the Flatpak. Asked at most
    once a minute — Settings reads it on every open."""
    if fresh or time.time() - _found["at"] > 60:
        _found["value"] = _find_native() or _find_flatpak()
        _found["at"] = time.time()
    return _found["value"]


def original_of(path):
    """Where the AniList original is kept beside its upscaled banner, so a
    model change can start again from it and switching off restores it."""
    return path.with_name(path.name[:-len(".banner.jpg")] + ".banner.orig.jpg")


_records_lock = threading.Lock()


def _records():
    try:
        return json.loads(system.read_bytes(config.DATA / "upscaled.json"))
    except (OSError, ValueError):
        return {}


def _save_records(rec):
    path = config.DATA / "upscaled.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    system.replace(tmp, path)


def banner_age(path):
    """Seconds since AniList's banner was saved (its kept original counts
    for an upscaled one); infinite when there is none."""
    for p in (original_of(path), path):
        try:
            return time.time() - p.stat().st_mtime
        except OSError:
            continue
    return float("inf")


def _dims(path):
    try:
        with open(path, "rb") as f:
            return jpeg_dimensions(f.read(65536))
    except OSError:
        return None


def is_current(path):
    """This banner is the current model's upscale of its original (the
    record names the model and the file's size — a fresh download from
    AniList replaces the file and no longer matches)."""
    rec = _records().get(path.name)
    try:
        return bool(rec) and rec.get("model") == model_id() and rec.get("size") == path.stat().st_size
    except OSError:
        return False


def needs_upscale(path):
    """An AniList original, or an older model's upscale whose original was
    kept. An old upscale without its original is left alone."""
    dims = _dims(path)
    if not dims:
        return False
    if dims[0] < UPSCALED_WIDTH:
        return True
    return not is_current(path) and original_of(path).is_file()


def upscale_banner(path, tool=None):
    """Upscale one banner in place, keeping the original beside it. True
    when replaced, False when left alone; "blank" when Upscayl wrote a flat
    picture (a broken GPU run)."""
    tool = tool or find_upscayl()
    if not tool:
        return False
    dims, orig = _dims(path), original_of(path)
    fresh = bool(dims) and dims[0] < UPSCALED_WIDTH
    src = path if fresh else orig
    if not src.is_file():
        return False
    out = path.with_suffix(".upscayl.jpg")
    name = style()
    model, tta = STYLES[name]
    try:
        cmd = [*tool["cmd"], "-i", str(src), "-o", str(out), "-m", tool["models"],
               "-n", model, "-f", "jpg", "-c", JPEG_QUALITY]
        if tta:
            cmd.append("-x")
        r = subprocess.run(cmd, capture_output=True, timeout=600, **system.quiet())
        if r.returncode == 0 and out.exists():
            res = out.read_bytes()
            if upscale_looks_sane(src.read_bytes(), res):
                if fresh:
                    shutil.copyfile(path, orig)
                system.replace(out, path)
                with _records_lock:
                    rec = _records()
                    rec[path.name] = {"model": model_id(name), "size": len(res)}
                    _save_records(rec)
                _changed(path)
                return True
            if jpeg_looks_blank(res):
                return "blank"
    except Exception:
        pass
    finally:
        out.unlink(missing_ok=True)
    return False


def restore_originals():
    """Switching off: every upscaled banner goes back to its AniList
    original. An original left behind by a banner that was downloaded
    again since is stale and goes."""
    if not config.POSTERS.is_dir():
        return
    with _run_lock, _records_lock:
        rec = _records()
        for orig in config.POSTERS.glob("*.banner.orig.jpg"):
            banner = orig.with_name(orig.name[:-len(".banner.orig.jpg")] + ".banner.jpg")
            try:
                dims = _dims(banner)
                if (dims and dims[0] >= UPSCALED_WIDTH and banner.name in rec
                        and rec[banner.name].get("size") == banner.stat().st_size):
                    system.replace(orig, banner)
                    _changed(banner)
                else:
                    orig.unlink()
            except OSError:
                continue  # still upscaled: keep its record
            rec.pop(banner.name, None)
        _save_records(rec)


# The worker: one banner at a time (each run fills the GPU), paused while
# mpv plays so an episode never stutters. A blank result stops it — the GPU
# setup is broken and every further banner would come out blank too.
_lock = threading.Lock()
_queue = deque()
_queued = set()
_skipped = set()  # failed once this session: not retried until a restart
status = {"state": "idle", "done": 0, "left": 0}
# Banners swapped since start (name -> its new ?v= stamp) and a counter the
# open page watches through /api/now, so it shows the new picture in place.
art = {"stamp": 0, "changed": {}}


def _changed(path):
    try:
        v = int(path.stat().st_mtime)
    except OSError:
        return
    # A restored original can carry the upscale's mtime to the second, and
    # the page only swaps a picture whose ?v= changed.
    art["changed"][path.name] = v = max(v, int(time.time()), art["changed"].pop(path.name, 0) + 1)
    while len(art["changed"]) > 200:
        art["changed"].pop(next(iter(art["changed"])))
    art["stamp"] += 1


def art_payload():
    """For /api/now: None until a banner changed or one is being upscaled
    (the page then asks a little more often)."""
    working = status["state"] == "working"
    if not (art["stamp"] or working):
        return None
    return {"stamp": art["stamp"], "changed": dict(art["changed"]), "working": working}
_worker = {"thread": None}
_run_lock = threading.Lock()  # one Upscayl run; switching off waits for it


def queue_banner(path):
    """Upscale this banner later, if upscaling is on."""
    if not config.UPSCALE_BANNERS or status["state"] == "broken":
        return
    path = Path(path)
    with _lock:
        if path in _queued or path in _skipped:
            return
        _queued.add(path)
        _queue.append(path)
        status["left"] = len(_queue)
        if not (_worker["thread"] and _worker["thread"].is_alive()):
            _worker["thread"] = threading.Thread(target=_work, daemon=True)
            _worker["thread"].start()


def queue_all():
    """Every saved banner that is not upscaled yet."""
    if not config.UPSCALE_BANNERS or not config.POSTERS.is_dir():
        return
    for path in sorted(config.POSTERS.glob("*.banner.jpg")):
        if needs_upscale(path):
            queue_banner(path)


def turned_on():
    """Settings switched upscaling on: look for Upscayl again, clear an old
    stop, and work through the saved banners. Queued before it returns,
    so the switch's answer already says "working"."""
    find_upscayl(fresh=True)
    with _lock:
        _skipped.clear()
        status["state"] = "idle"
    queue_all()


def style_changed():
    """Settings picked another look: every banner whose original was kept
    is redone from it."""
    with _lock:
        _skipped.clear()
    queue_all()


def _playing():
    from . import player
    with player._active_lock:
        return bool(player._active)


def _work():
    while True:
        with _lock:
            if not _queue or not config.UPSCALE_BANNERS:
                _queue.clear()
                _queued.clear()
                status.update(left=0, state="broken" if status["state"] == "broken" else "idle")
                return
            path = _queue[0]
            status["state"] = "working"
        while _playing() and config.UPSCALE_BANNERS:
            time.sleep(5)
        tool = find_upscayl()
        with _run_lock:
            run = tool and config.UPSCALE_BANNERS and needs_upscale(path)
            result = upscale_banner(path, tool) if run else False
        with _lock:
            _queue.popleft()
            _queued.discard(path)
            status["left"] = len(_queue)
            if result is True:
                status["done"] += 1
            else:
                _skipped.add(path)
            if not tool or result == "blank":
                if result == "blank":
                    status["state"] = "broken"
                _queue.clear()
                _queued.clear()


def payload():
    """For Settings: whether it's on, where Upscayl is, and how far the
    worker got. state: off / missing / idle / working / broken."""
    tool = find_upscayl()
    state = status["state"]
    if not config.UPSCALE_BANNERS:
        state = "off"
    elif not tool and state != "working":
        state = "missing"
    return {"on": config.UPSCALE_BANNERS, "found": tool["where"] if tool else None,
            "flatpak": bool(tool) and tool["cmd"][0] == "flatpak",
            "state": state, "done": status["done"], "left": status["left"],
            "style": style(), "styles": list(STYLES)}
