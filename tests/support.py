"""Shared test setup. Import this before anything else in a test module.

The animehub package reads its config at import time, so this
points XDG_CONFIG_HOME / XDG_DATA_HOME at a throwaway directory first: the
tests never see the user's library, database or AniList account. Network
access is blocked; AniList data comes from tests/fixtures/anilist
(recorded by tests/record_fixtures.py)."""

import atexit
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TMP = Path(tempfile.mkdtemp(prefix="animehub-tests-"))
atexit.register(shutil.rmtree, TMP, True)
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_DATA_HOME"] = str(TMP / "data")
sys.path.insert(0, str(ROOT))

import animehub.server  # noqa: E402  (imports every module)
from animehub import database, franchise, library  # noqa: E402
from animehub.config import config, CONFIG, apply_config  # noqa: E402


def _offline(*args, **kwargs):
    raise AssertionError("tests must not use the network — record a fixture instead")


for _mod in list(sys.modules.values()):
    if _mod and _mod.__name__.startswith("animehub.") and hasattr(_mod, "urlopen"):
        _mod.urlopen = _offline


@contextmanager
def sqlite(path):
    """sqlite3.connect for a `with` block that also closes the file: sqlite's
    own `with` only commits, and Windows can't delete an open database."""
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def cases():
    return json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


def case(name):
    return next(c for c in cases() if c["name"] == name)


def anilist(mid):
    """A recorded AniList entry (franchise.FRANCHISE_QUERY_ID's shape) — the
    `fetch` every franchise function takes in tests."""
    path = FIXTURES / "anilist" / f"{mid}.json"
    if not path.exists():
        raise KeyError(f"no fixture for AniList {mid} — run tests/record_fixtures.py")
    return json.loads(path.read_text(encoding="utf-8"))


def use_library(c):
    """Build a case's folder layout (empty video files) in a fresh library
    with its own database, point the hub at it, and return library.scan_library()."""
    base = TMP / "cases" / c["name"]
    shutil.rmtree(base, ignore_errors=True)
    lib = base / "library"
    for spec in c["library"]:
        d = lib / spec["dir"]
        d.mkdir(parents=True, exist_ok=True)
        name = Path(spec["dir"]).parts[0]
        files = spec.get("files") or [f"{name} - E{n:02d}.mkv" for n in range(spec["eps"][0], spec["eps"][1] + 1)]
        for f in files:
            (d / f).touch()
    apply_config(dict(CONFIG, library_root=lib, data_dir=base / "data"))
    for d in (config.DATA, config.CACHE, config.POSTERS, config.EPIMGS):
        d.mkdir(parents=True, exist_ok=True)
    database.init_db()
    return library.scan_library()


def numbers(nums):
    """[1, 2, 3, 5] → "1-3,5": episode numbers as a short, readable string."""
    out, run = [], []
    for n in sorted(nums):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [n]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ",".join(out)


def scan_summary(shows):
    """{show path: {"kind", "seasons": {label: episode numbers}}}."""
    return {s["path"]: {"kind": s["kind"],
                        "seasons": {sea["label"]: numbers(e["number"] for e in sea["episodes"])
                                    for sea in s["seasons"]}}
            for s in shows}


def match_library(c, scan):
    """Give a case's folders their matches ("match": folder → AniList id)
    and store each match's franchise, as the background passes would; then
    merge_db, so the scan carries meta, season ranges and watch facts."""
    with database.db() as db:
        for s in scan:
            mid = c.get("match", {}).get(s["path"])
            if mid:
                db.execute("INSERT OR REPLACE INTO shows_meta(show_id, anilist_id, title) VALUES(?,?,?)",
                           (s["id"], mid, s["title"]))
    for mid in set(c.get("match", {}).values()):
        if not franchise.franchise_of(mid):
            franchise.store_franchise(franchise.build_franchise(mid, anilist))
    library.merge_db(scan)
    return scan


def folder(scan, path):
    return next(s for s in scan if s["path"] == path)


def season(show, label):
    return next(x for x in show["seasons"] if x["label"] == label)


def ranges(rs):
    """Ranges as cases.json writes them: [first, last, media id, offset]."""
    return [[r["first_ep"], r["last_ep"], r["id"], r["offset"]] for r in rs]
