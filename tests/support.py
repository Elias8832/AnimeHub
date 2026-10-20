"""Shared test setup. Import this before anything else in a test module.

hub.py reads its config and opens its database at import time, so this
points XDG_CONFIG_HOME / XDG_DATA_HOME at a throwaway directory first: the
tests never see the user's library, database or AniList account. Network
access is blocked; AniList data comes from tests/fixtures/anilist
(recorded by tests/record_fixtures.py)."""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TMP = Path(tempfile.mkdtemp(prefix="animehub-tests-"))
atexit.register(shutil.rmtree, TMP, True)
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_DATA_HOME"] = str(TMP / "data")
sys.path.insert(0, str(ROOT))

import hub  # noqa: E402


def _offline(*args, **kwargs):
    raise AssertionError("tests must not use the network — record a fixture instead")


hub.urlopen = _offline


def cases():
    return json.loads((FIXTURES / "cases.json").read_text())


def case(name):
    return next(c for c in cases() if c["name"] == name)


def anilist(mid):
    """A recorded AniList entry (hub.FRANCHISE_QUERY_ID's shape) — the
    `fetch` every franchise function takes in tests."""
    path = FIXTURES / "anilist" / f"{mid}.json"
    if not path.exists():
        raise KeyError(f"no fixture for AniList {mid} — run tests/record_fixtures.py")
    return json.loads(path.read_text())


def use_library(c):
    """Build a case's folder layout (empty video files) in a fresh library
    with its own database, point hub at it, and return hub.scan_library()."""
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
    hub.apply_config(dict(hub.CONFIG, library_root=lib, data_dir=base / "data"))
    for d in (hub.DATA, hub.CACHE, hub.POSTERS, hub.EPIMGS):
        d.mkdir(parents=True, exist_ok=True)
    hub.init_db()
    return hub.scan_library()


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
    with hub.db() as db:
        for s in scan:
            mid = c.get("match", {}).get(s["path"])
            if mid:
                db.execute("INSERT OR REPLACE INTO shows_meta(show_id, anilist_id, title) VALUES(?,?,?)",
                           (s["id"], mid, s["title"]))
    for mid in set(c.get("match", {}).values()):
        if not hub.franchise_of(mid):
            hub.store_franchise(hub.build_franchise(mid, anilist))
    hub.merge_db(scan)
    return scan


def folder(scan, path):
    return next(s for s in scan if s["path"] == path)


def season(show, label):
    return next(x for x in show["seasons"] if x["label"] == label)


def ranges(rs):
    """Ranges as cases.json writes them: [first, last, media id, offset]."""
    return [[r["first_ep"], r["last_ep"], r["id"], r["offset"]] for r in rs]
