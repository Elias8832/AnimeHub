# Franchise page: plan

Replaces the uncommitted `multi-season-rework` (main checkout). Built fresh
from `master`; only the parts of the rework that worked get ported.

## Goal

One page per anime franchise. Every season, movie, special and OVA of it is
on that page, and each one's episodes are a single list where a local file
and an online episode look and behave the same. Search still finds each
season or movie on its own; clicking any of them opens the franchise page
with that entry selected.

Example: searching "Apothecary Diaries" lists Season 1, 2nd Season, Season 3
and the "Maomao no Hitorigoto" ONA. Clicking "2nd Season" opens the
Apothecary Diaries page with 2nd Season selected. Its 24 episodes are one
list: E1–E10 are files on disk, E11–E24 play online, and nothing about the
page changes between them except a small source badge.

## Why the rework broke

- Seasons were identified by their position in `s.seasons`; online
  "virtual" seasons were appended at runtime, so positions shifted.
- A tab could switch the season, open another folder, or open a release
  page. Each page built its own tab list from a different starting point.
- The browser re-derived which AniList entry a season was, with rules that
  differed from the server's.
- Many one-show title heuristics, no tests.

## Model

- **Entry**: one AniList media (a season, movie, special, OVA, ONA). The
  unit of AniList tracking, art, description and episode numbering.
- **Franchise**: the anime entries connected by AniList relations
  `SEQUEL`, `PREQUEL`, `PARENT`, `SIDE_STORY`, `SUMMARY` (anime formats
  only: `TV`, `TV_SHORT`, `ONA`, `OVA`, `SPECIAL`, `MOVIE`; no manga/novel
  `ALTERNATIVE`/`ADAPTATION` edges). A link only counts when neither side
  calls it `SPIN_OFF`, `ALTERNATIVE` or `CHARACTER`: AniList's two sides can
  disagree (AoT calls Junior High a spin-off, Junior High calls AoT its
  parent), and the page must not depend on where the walk starts. Capped
  at ~60 entries. Franchise id = the oldest entry's id, so it stays stable
  when new seasons are announced. Entries are ordered by start date,
  undated ones last.
- **Episode slot**: `(entry id, episode number)`. A slot has zero or more
  sources: local file(s), an online token. Play uses the local file when
  there is one, otherwise online. Download turns an online slot into a local
  one; the tile stays where it is.
- **Local mapping**: a folder season maps onto entry ranges with an offset
  (the rework's `season_releases` / `release_units` /
  `map_numbered_seasons`, kept). Files that map to nothing appear in a
  "Other local files" section of the franchise page, never hidden.
  Unmatched folders are their own page, as today.
- **Watch facts**: keep the existing ids (disk episode id, `stream:mid:n`).
  A slot is watched if any of its sources is; marking writes all of them.

Stable ids everywhere: page route `#/f/<franchise id>/<entry id>`; endpoints
take entry ids, never list positions.

## Rules

1. The server builds the page; the browser only draws it. No season or
   entry logic in `app.js`.
2. Loading a page or the library never calls the network. Franchise graphs,
   entry facts, art and mappings are built in the background and stored.
3. Switching entries only switches the selected entry on the same page.
4. Title heuristics may only affect display labels (worst case: a longer
   label). Matching uses AniList ids and relations. One exception: an
   `OTHER` link joins an entry whose title is this one's plus a season
   number (AniList links "Cyberpunk: Edgerunners 2" only as `OTHER`); a
   miss just leaves it on its own page.
5. Every mapping function is pure (AniList data in, result out) and has
   fixture tests.

## Phases

Each phase ends with the test suite green.

1. **Test harness.** *(done)* `tests/` with `python -m unittest`, fixtures of
   recorded AniList media JSON plus fake folder scans for: Apothecary
   Diaries, Attack on Titan (S3 split 12+10, wrong stills), Made in Abyss
   (movie between seasons), Monogatari (side arcs), Cyberpunk: Edgerunners,
   Blue Box (S2 in its own folder), Kaguya-sama, absolute numbering,
   Specials/OVA folders, a single-season show, an unmatched folder.
2. **Data layer.** *(done)* Tables `media` (entry facts + art, refreshed by TTL;
   airing entries daily) and `franchise_members(media_id, franchise_id,
   pos)`. Pure `build_franchise(start_id, fetch)` + background job.
   Schema versions 11–14 are reserved no-ops: the abandoned rework already
   moved real databases to 14, so new tables start at migration 15 and any
   ported rework columns must be added idempotently (`_add_columns`).
3. **Local mapping.** Port from the rework: migrations for
   `season_releases` facts, `release_units`, `map_numbered_seasons` (run
   against the franchise's TV line), `season_art` + ani.zip stills,
   per-entry progress sync / completion / reconcile, per-season "wrong
   entry?" dialog.
4. **Page model.** `franchise_view(fid)` returns entries in order, each
   with facts, art, and its merged episode slots (local + online +
   placeholders up to the aired count). `GET /api/franchise?id=`.
   Online links resolve in the background when an entry is opened.
5. **Frontend.** New show page on the page model; entry switcher (main
   seasons first row, movies/specials/OVAs second row); one tile style
   with a source badge; search results are entries that open their
   franchise page; library cards per the decision below.
6. **Cleanup.** Remove `aid:` standalone shows, `merge_streams`,
   client-side season ordering, and the old season art paths.

## Decisions

- Library: one card per franchise. Search lists single entries.
- Spin-offs (`SPIN_OFF`) and `ALTERNATIVE` anime adaptations (FMA vs
  Brotherhood) get their own pages.
