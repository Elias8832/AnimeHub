"""Is the built zip sound? GitHub runs this after build.ps1 (TODO 148), and it
works by hand too: python desktop\\windows\\smoke.py
Reads the zip's raw entries, unpacks it into a folder with a space, and starts
the hub on the bundled Python. The app window itself isn't tested here."""

import json
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ZIP = Path(__file__).resolve().parents[2] / "desktop" / "target" / "AnimeHub-windows-x64.zip"
NEEDED = ("AnimeHub.exe", "README.txt", "Add to Start menu.cmd", "python/python.exe", "app/hub.py",
          "app/animehub/server.py", "app/web/index.html", "app/mpv/skip-credits.lua", "app/mpv/player-soft.lua", "app/mpv/player-deck.lua", "app/mpv/player-mouse.lua", "app/mpv/fonts/HankenGrotesk-Regular.ttf", "app/LICENSE")
ICONS = 3


def raw_names(data):
    """Entry names as stored: zipfile turns "\\" into "/" when it reads them."""
    names, i = [], 0
    while (i := data.find(b"PK\x01\x02", i)) >= 0:
        size, extra, comment = struct.unpack("<HHH", data[i + 28:i + 34])
        names.append(data[i + 46:i + 46 + size].decode())
        i += 46 + size + extra + comment
    return names


def check_entries(z, data):
    names = raw_names(data)
    assert names and all("\\" not in n for n in names), "backslashes in the zip's paths"
    assert all(n.startswith("AnimeHub/") for n in names), "not one AnimeHub folder"
    missing = [n for n in NEEDED if "AnimeHub/" + n not in names]
    assert not missing, f"missing: {missing}"
    junk = [n for n in names if "__pycache__" in n or "/tests/" in n or "Work/" in n]
    assert not junk, f"doesn't belong in the zip: {junk[:5]}"
    assert z.read("AnimeHub/AnimeHub.exe").count(b"\x89PNG\r\n\x1a\n") >= ICONS, "the exe lost its icon"
    code = [n for n in names if n.endswith((".py", ".js", ".lua"))]
    provider = [n for n in code if "/app/" in n and re.search(rb"allanime|hianime", z.read(n))]
    assert not provider, f"provider code: {provider}"  # Decision 1
    print(f"entries ok: {len(names)}, no backslashes, exe has its icon, no provider code")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.read()


def check_running(z, base):
    z.extractall(base)
    app = base / "AnimeHub"
    python = app / "python" / "python.exe"
    # The modules the hub can't work without; an embeddable Python leaves some out.
    subprocess.run([python, "-c", "import ctypes, sqlite3, ssl, zlib"], check=True)
    show = base / "Anime 日本語" / "Show"
    show.mkdir(parents=True)
    (show / "Show - 01.mkv").touch()
    port = free_port()
    config = base / "config.json"
    config.write_text(json.dumps({"library_root": str(show.parent), "port": port, "window": "none",
                                  "data_dir": str(base / "data")}), encoding="utf-8")
    # A file, as the wrapper does: Python printing "→" to one crashed the hub once.
    with open(base / "hub.log", "wb") as log:
        hub = subprocess.Popen([python, app / "app" / "hub.py", "--no-browser", "--config", config], stdout=log, stderr=log)
    try:
        for _ in range(60):
            assert hub.poll() is None, "the hub stopped:\n" + (base / "hub.log").read_text(encoding="utf-8", errors="replace")
            try:
                page = get(port, "/")
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise AssertionError("the hub never answered")
        assert b"app.js" in page, "no app page"
        s = json.loads(get(port, "/api/settings"))
        assert s["port"] == port and s["stream"]["can_get"] is False, s
        shows = [x["title"] for x in json.loads(get(port, "/api/library"))["shows"]]
        assert shows == ["Show"], shows
        print("bundled Python ok: hub answers, a Japanese folder name scans")
    finally:
        hub.kill()
        hub.wait()


def main():
    data = ZIP.read_bytes()
    print(f"{ZIP.name}: {len(data) / 1e6:.1f} MB")
    with zipfile.ZipFile(ZIP) as z:
        assert z.testzip() is None, "damaged zip"
        check_entries(z, data)
        base = Path(tempfile.mkdtemp(prefix="anime hub zip "))
        try:
            check_running(z, base)
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
