#!/usr/bin/env python3
"""Record the AniList fixtures the tests run on (no network in tests).

Walks each case's franchise from its start id over franchise.FRANCHISE_RELATIONS
and OTHER links to franchise.FRANCHISE_OTHER_FORMATS (anime entries only; OTHER
neighbours are recorded so tests can check which of them join), and saves
every entry, as franchise.FRANCHISE_QUERY_ID
returns it, to tests/fixtures/anilist/<id>.json. Entries already recorded
are kept; pass --refresh to fetch them again.

    python tests/record_fixtures.py            # every case in cases.json
    python tests/record_fixtures.py aot fma    # some cases
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from animehub import anilist, franchise  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANILIST = FIXTURES / "anilist"
LIMIT = 60
PAUSE = 2.2  # AniList allows ~30 requests a minute while degraded


def fetch(mid, refresh):
    path = ANILIST / f"{mid}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    media = anilist.anilist_request({"query": franchise.FRANCHISE_QUERY_ID, "variables": {"id": mid}})["data"]["Media"]
    path.write_text(json.dumps(media, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  recorded {mid} {(media.get('title') or {}).get('romaji')}", flush=True)
    time.sleep(PAUSE)
    return media


def walk(start, refresh):
    seen, todo, others = set(), [start], set()
    while todo and len(seen) < LIMIT:
        mid = todo.pop(0)
        if mid in seen:
            continue
        seen.add(mid)
        media = fetch(mid, refresh)
        for e in (media.get("relations") or {}).get("edges") or []:
            node = e["node"]
            if node.get("type") != "ANIME" or node["id"] in seen:
                continue
            if e["relationType"] in franchise.FRANCHISE_RELATIONS:
                todo.append(node["id"])
            elif e["relationType"] == "OTHER" and node.get("format") in franchise.FRANCHISE_OTHER_FORMATS:
                others.add(node["id"])
    if todo:
        print(f"  stopped at {LIMIT} entries", flush=True)
    # Whether an OTHER neighbour joins is the franchise rule's call; it is
    # recorded (and walked when it is a case's own start), never walked here.
    for mid in others - seen:
        fetch(mid, refresh)
    return seen


def main(argv):
    refresh = "--refresh" in argv
    names = [a for a in argv if not a.startswith("--")]
    ANILIST.mkdir(parents=True, exist_ok=True)
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    for case in cases:
        if names and case["name"] not in names:
            continue
        for start in case.get("anilist", []):
            print(f"{case['name']}: walking from {start}", flush=True)
            print(f"  {len(walk(start, refresh))} entries", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
