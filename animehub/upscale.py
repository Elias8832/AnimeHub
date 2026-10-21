"""Optional banner upscaling through the Upscayl flatpak."""

import os
import subprocess
import threading


UPSCAYL_FLATPAK = "org.upscayl.Upscayl"
UPSCAYL_BIN = "/app/upscayl/resources/bin/upscayl-bin"
UPSCAYL_MODELS = "/app/upscayl/resources/models"
UPSCAYL_MODEL = "upscayl-standard-4x"
UPSCAYL_TTA = True


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


def upscale_looks_sane(src_bytes, out_bytes):
    """Dimension guard for the banner upscaler (found 2026-10-15: on this
    machine upscayl runs on software Vulkan and emits garbage — six real
    banners were replaced by black voids, one batch even at a runaway
    27440×5776). A 4x model's output must stay within a 4x reach of the
    source's dimensions. Pixel-level blackness can't be checked stdlib-only
    without decoding — the stronger protection is that the whole upscaler is
    opt-in (config 'upscale_banners', default off)."""
    src, out = jpeg_dimensions(src_bytes), jpeg_dimensions(out_bytes)
    if not src or not out:
        return False
    return (src[0] // 2 <= out[0] <= src[0] * 4.5 + 8 and
            src[1] // 2 <= out[1] <= src[1] * 4.5 + 8)


def upscale_banner(path):
    out = path.with_suffix(".upscayl.jpg")
    try:
        cmd = ["flatpak", "run", "--command=" + UPSCAYL_BIN, UPSCAYL_FLATPAK,
               "-i", str(path), "-o", str(out), "-m", UPSCAYL_MODELS,
               "-n", UPSCAYL_MODEL, "-f", "jpg", "-c", "90"]
        if UPSCAYL_TTA:
            cmd.append("-x")
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        src = path.read_bytes()
        if r.returncode == 0 and out.exists() and upscale_looks_sane(src, out.read_bytes()):
            os.replace(out, path)
            return True
    except Exception:
        pass
    out.unlink(missing_ok=True)
    return False


_upscale_lock = threading.Lock()
_upscale_inflight = set()


def upscale_banner_async(path):
    """Upscale in the background; the raw file keeps serving as placeholder."""
    with _upscale_lock:
        if path in _upscale_inflight:
            return
        _upscale_inflight.add(path)

    def run():
        try:
            upscale_banner(path)
        finally:
            with _upscale_lock:
                _upscale_inflight.discard(path)

    threading.Thread(target=run, daemon=True).start()
