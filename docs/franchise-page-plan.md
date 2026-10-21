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

Stable ids everywhere: page route `#f=<entry id>` (the entry selected on its
franchise's page); endpoints take entry ids, never list positions.

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
3. **Local mapping.** *(done)* `map_folder(show, anchor, entries, pins)`:
   numbered seasons walk the franchise's line from the folder's match
   (`main_line`: SEQUEL links, TV first, movies stepped through, OTHER +
   numbered title), spanning entries by episode count (AoT S3 = 12 + 10,
   absolute numbering); named seasons ("Specials/Lost Girls") match one
   entry by title; generic Specials stay unmapped. Computed from the stored
   franchise on every read, so `season_releases` keeps only user pins
   (migration 16). `release_units` drives per-entry progress sync,
   completion, reconcile, AniList watched marks and library counts.
   Episode stills per entry (`entry_art`): AniList's listing only when it is
   the entry's own (numbers fit, no stills shared with an earlier entry),
   else ani.zip. The "wrong entry?" backend is `POST /api/season-match`;
   its dialog is drawn on the new page (phase 5).
4. **Page model.** *(done)* Pure `franchise_page(...)`; `franchise_view(id)`
   gathers the stored data for `GET /api/franchise?id=<entry>` (that entry
   selected). Entries in air order, each with facts, art, `group`
   (`main` = the line from the first-aired TV/ONA entry, else `extra`),
   a display `label` (what the title adds to the franchise's; "Season N"
   when that is nothing; its own title when unrelated, e.g.
   Nisemonogatari) and `slots`: one per episode number on disk, in the
   ani-cli list, or aired. A slot has `local` and/or `online` sources,
   `id` = what Play plays (the file when there is one), and is watched
   when either source is. `other_local` lists the franchise folders'
   files that are no entry. Not built yet → the entry alone with
   `building` while it builds in the background; the opened entry's
   ani-cli link and stills load in the background too.
5. **Frontend.** *(done)* `Fran()` in `app.js` draws the page model: entry
   switcher (main seasons first row, movies/specials/OVAs second row), one
   tile style with an "on disk" / "online" badge, "Other files" with
   "Which season is this?" and an "On disk" panel with "Wrong season?"
   (`/api/season-match`). Marks go through `POST /api/entry-watched`
   (every source of a slot; marking watched syncs the entry). Every way
   into a matched folder, an online show, a search result or a list card
   opens the franchise page; unmatched folders keep the old page. Library:
   one card per franchise (`franchise_id` on every card). All AniList
   requests share one pace under the rate limit, with a reserve for a page
   the user just opened (`_anilist_slot`).
6. **Cleanup.** *(done)* Autoplay follows the franchise page
   (`next_episode`: the next episode of the entry, file or online, then the
   next main season; an episode that can't play ends the chain instead of
   skipping ahead). Online episodes are no longer merged into folders
   (`merge_streams`, disk "twins") and the per-release page is gone
   (`/api/release`, `release_payload`, its frontend); `aid:` shows remain
   only as the online episodes history, continue watching and playback
   read. Downloads go to the folder season holding the entry's files
   (`entry_files`). The old show page is the unmatched-folder page;
   client-side season ordering is gone. A stale online list refreshes when
   its entry is opened. Unaired entries never link to ani-cli, and older
   automatic links for them are dropped.

## Decisions

- Library: one card per franchise. Search lists single entries.
- Spin-offs (`SPIN_OFF`) and `ALTERNATIVE` anime adaptations (FMA vs
  Brotherhood) get their own pages.
