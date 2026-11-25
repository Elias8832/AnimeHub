/* Run the real homepage functions without starting mpv or changing hub.db.
   app.js brackets its testable logic with "@scope:start"/"@scope:end" comments;
   the home view is a string builder, so render tests assert on returned HTML. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
function block(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `Cannot find production block: ${start}`);
  return source.slice(from, to);
}

function episode(id, last_played, watched = false, position = 120) {
  return { id, number: 1, last_played, watched, position, duration: 1440 };
}

function show(title, episodes, artwork = true) {
  return {
    id: title, title, collection: null, meta: {},
    banner: artwork ? `/img/${title}.banner.jpg` : null, poster: null,
    seasons: [{ key: 1, season: 1, label: "Season 1", episodes }],
  };
}

function homepage(data, o = {}) {
  const context = vm.createContext({
    S: { data, view: "home", showId: null, seasonIdx: 0, showBack: "home", cat: null, q: "", menu: null },
    aniMode: () => !!o.ani, // spine mode unless a test needs AniList's airing list
    inProg: (st) => ["CURRENT", "REPEATING", "PAUSED"].includes(st),
    api: () => new Promise(() => {}), // Recommended's fetch never answers here
    isPhone: () => false, isTabletP: () => false, // the PC layout (phones: test_phone_layout.py)
    fmtIn: () => "soon", airsIn: () => 0,
    plHomeHTML: () => "", // pinned playlists: tests/playlists.cjs
  });
  vm.runInContext(
    block("/* @pure:start */", "/* @pure:end */") +
    block("/* @home:start */", "/* @home:end */"),
    context,
  );
  return context;
}

const earlier = "2026-10-14 15:13:42", latest = "2026-10-14 15:16:13";

test("a short preview beats an older half-watched show", () => {
  const older = episode("older", earlier);
  const preview = episode("preview", latest, false, 13.6);
  const app = homepage({ shows: [show("Older", [older]), show("Chainsaw Man", [preview])],
    continue: [older], history: [preview, older] });
  const picked = app.nextUpInfo();
  assert.equal(picked.s.title, "Chainsaw Man");
  assert.equal(picked.ep.id, "preview");
  assert.equal(picked.label, "Continue watching");
});

test("a finished episode promotes that show's next episode", () => {
  const older = episode("older", earlier);
  const finished = episode("finished", latest, true);
  const next = { ...episode("next", null), number: 2 };
  const app = homepage({ shows: [show("Older", [older]), show("Blue Box", [finished, next])],
    continue: [older], history: [finished, older] });
  const picked = app.nextUpInfo();
  assert.equal(picked.s.title, "Blue Box");
  assert.equal(picked.ep.id, "next");
  assert.equal(picked.label, "Up next");
});

test("near-finished unwatched episodes remain hero candidates", () => {
  const older = episode("older", earlier);
  const late = episode("late", latest, false, 1400);
  const app = homepage({ shows: [show("Older", [older]), show("Recent", [late])],
    continue: [older], history: [late, older] });
  assert.equal(app.nextUpInfo().ep.id, "late");
});

test("missing artwork does not exclude the latest show", () => {
  const recent = episode("recent", latest);
  const app = homepage({ shows: [show("No artwork", [recent], false)],
    continue: [], history: [recent] });
  const html = app.Home();
  assert.match(html, /<h1>No artwork<\/h1>/);
  assert.match(html, /<div class="upn" data-a="open" data-v="[^"]*" style="--h:[^"]*"[^>]*><div class="upc">/); // hue placeholder, no art layer
});

test("empty activity and removed episodes do not crash the hero picker", () => {
  const app = homepage({ shows: [], continue: [], history: [] });
  assert.equal(app.nextUpInfo(), null);
  app.S.data.history = [episode("missing", latest)];
  assert.equal(app.nextUpInfo(), null);
});

test("the latest fully watched show gets a rewatch banner when nothing else is in progress", () => {
  const older = episode("older", earlier, true, 1430);
  const finished = episode("finished", latest, true, 1430);
  const app = homepage({ shows: [show("Older", [older]), show("Finished", [finished])],
    continue: [], history: [finished, older] });
  assert.equal(app.nextUpInfo().s.title, "Finished");
  assert.equal(app.nextUpInfo().label, "Rewatch");
});

test("a show in Continue watching leads instead of a rewatch banner", () => {
  const older = episode("older", earlier);
  const finished = episode("finished", latest, true, 1430);
  const app = homepage({ shows: [show("Older", [older]), show("Finished", [finished])],
    continue: [older], history: [finished, older] });
  const up = app.nextUpInfo();
  assert.equal(up.s.title, "Older");
  assert.equal(up.label, "Continue watching");
  const html = app.Home();
  assert.match(html, /<h1>Older<\/h1>/);
  assert.doesNotMatch(html, /Rewatch/);
  assert.doesNotMatch(html, /<h3>Older<\/h3>/); // not twice
});

test("a watched episode stopped halfway says Continue, not Rewatch", () => {
  const stopped = { ...episode("stopped", latest, true), position: 600, duration: 1440 };
  const app = homepage({ shows: [show("Stopped", [stopped])], continue: [stopped], history: [stopped] });
  assert.equal(app.nextUpInfo().label, "Continue watching");
});

test("the next airing shows even when it's past this week", () => {
  const app = homepage({ shows: [], continue: [], history: [] });
  const now = Date.UTC(2026, 9, 30, 12), e = { id: 1, title: "Blue Box", next_episode: { number: 5, at: now / 1000 + 8 * 86400 } };
  const html = app.weekHTML([e], now);
  assert.match(html, /Blue Box/);
  assert.match(html, /<h2>Coming up<\/h2>/);
});

test("an episode that's out and unwatched sits first in Continue watching; This week only counts down (TODO 236)", () => {
  const now = Date.now();
  const ep5 = { ...episode("bb5", null), number: 5 };
  const bb = { ...show("Blue Box Season 2", [ep5]), id: "bb" };
  const older = episode("older", earlier);
  const e = { id: 7, title: "Blue Box Season 2", airing: "RELEASING", status: "CURRENT", progress: 4, episodes: 12, local_show_id: "bb",
    next_episode: { number: 6, at: Math.floor(now / 1000) + 6 * 86400 } };
  const app = homepage({ shows: [show("Older", [older]), bb], continue: [older], history: [older], spine: { entries: [e] } }, { ani: true });
  const html = app.Home();
  assert.match(html, /<div class="strip hstrip"><div class="hc hcnew"[^>]*data-v="bb"/); // first in the row
  assert.match(html, /<span class="newtag">1 new<\/span><span class="enum">E5<\/span>/);
  assert.match(html, /data-a="play" data-v="bb5"/); // Play plays the episode you're missing
  assert.match(html, /\/img\/Blue%20Box%20Season%202\.banner\.jpg/); // the show page's saved banner, so Home and the show page match
  const week = app.weekHTML([e], now);
  assert.doesNotMatch(week, /behind|data-a="play"/);
  assert.match(week, /next episode/);
  assert.match(week, /class="twg solo"/); // nothing else airing: the banner takes the row
  assert.match(week, /data-cd="/); // the next one counts down
});

test("This week: no Play when the missing episode isn't on disk or online yet", () => {
  const now = Date.UTC(2026, 10, 8, 18);
  const app = homepage({ shows: [], continue: [], history: [] });
  const e = { id: 7, title: "Blue Box Season 2", progress: 4, next_episode: { number: 6, at: now / 1000 + 6 * 86400 } };
  const html = app.weekHTML([e], now);
  assert.doesNotMatch(html, /data-a="play"/);
  assert.doesNotMatch(html, /Open show/); // the card opens the show; no extra button
});

test("This week: the soonest show leads when you're caught up, one or two more get the same banner", () => {
  const now = Date.UTC(2026, 10, 8, 18);
  const app = homepage({ shows: [], continue: [], history: [] });
  const mk = (id, title, days) => ({ id, title, progress: 5, next_episode: { number: 6, at: now / 1000 + days * 86400 } });
  const html = app.weekHTML([mk(1, "Later", 5), mk(2, "Soon", 1), mk(3, "Middle", 3)], now);
  assert.match(html, /<h3>Soon<\/h3>/);
  assert.doesNotMatch(html, /behind/);
  assert.ok(html.indexOf("<h3>Middle</h3>") < html.indexOf("<h3>Later</h3>")); // in air order
  assert.equal((html.match(/class="twb side"/g) || []).length, 2); // the same look as the lead, not a near-empty list
  assert.doesNotMatch(html, /class="twr"/);
  assert.doesNotMatch(html, /more in the Calendar/); // everything fits: the header just says Calendar
});

test("This week: three or more beside the lead are rows", () => {
  const now = Date.UTC(2026, 10, 8, 18);
  const app = homepage({ shows: [], continue: [], history: [] });
  const mk = (id, title, days) => ({ id, title, progress: 5, next_episode: { number: 6, at: now / 1000 + days * 86400 } });
  const html = app.weekHTML([mk(1, "Later", 5), mk(2, "Soon", 1), mk(3, "Middle", 3), mk(4, "Last", 6)], now);
  assert.match(html, /<h3>Soon<\/h3>/);
  assert.ok(html.indexOf("<b>Middle</b>") < html.indexOf("<b>Later</b>")); // rows in air order
  assert.doesNotMatch(html, /twb side/);
});

test("homepage renders the new hero, excludes its duplicate tile, and targets its buttons", () => {
  const older = episode("older", earlier);
  const preview = episode("preview", latest, false, 13.6);
  const app = homepage({ shows: [show("Older", [older]), show("Recent", [preview])],
    continue: [older], history: [preview, older] });
  const html = app.Home();
  assert.match(html, /^<div class="home2 wide"><div class="upn"/); // the PC: no headline, the up-next card first (TODO 236)
  assert.match(html, /<h1>Recent<\/h1>/);
  assert.match(html, /\/img\/Recent\.banner\.jpg/);
  assert.doesNotMatch(html, /<h3>Recent<\/h3>/); // the up-next show is skipped in Continue watching
  assert.match(html, /<h3>Older<\/h3>/);
  assert.match(html, /data-a="play" data-v="preview"/); // hero button targets the hero episode
  assert.match(html, /<div class="upn" data-a="open" data-v="Recent"/); // the whole banner opens the show
  assert.doesNotMatch(html, /Open show/); // no separate Open show button
});

test("selection follows timestamps rather than input order", () => {
  const older = episode("older", earlier);
  const recent = episode("recent", latest);
  const app = homepage({ shows: [show("Older", [older]), show("Recent", [recent])],
    continue: [older, recent], history: [older, recent] });
  assert.equal(app.nextUpInfo().ep.id, "recent");
});

test("a show taken off Continue watching stays off until it plays again (Decision 81)", () => {
  const older = episode("older", earlier);
  const recent = episode("recent", latest);
  const third = episode("third", "2026-10-14 15:00:00");
  const data = { shows: [show("Older", [older]), show("Recent", [recent]), show("Third", [third])],
    continue: [older, recent, third], history: [recent, older, third], continue_hidden: { Older: "2026-10-14 15:20:00" } };
  const app = homepage(data);
  let html = app.Home();
  assert.doesNotMatch(html, /<h3>Older<\/h3>/); // gone from the row
  assert.match(html, /<h3>Third<\/h3>/);
  assert.match(html, /data-cw="Third" data-cwep="third"/); // right-click knows the show and its episode
  data.continue_hidden = { Recent: "2026-10-14 15:20:00" }; // the newest show: the big card moves on
  assert.equal(app.nextUpInfo().s.title, "Older");
  recent.last_played = "2026-10-14 15:30:00"; // played again after it was taken off: back
  assert.equal(app.nextUpInfo().s.title, "Recent");
});

test("the PC's banner: this episode's progress once started, a nameless episode said once (TODO 236)", () => {
  const started = episode("started", latest); // 120 s of 1440
  let app = homepage({ shows: [show("Started", [started])], continue: [started], history: [started] });
  let html = app.Home();
  assert.match(html, /<div class="sub">Episode 1<\/div>/); // not "E1 · Episode 1"
  assert.match(html, /<div class="hbar"><i style="width:8\.3%"><\/i><\/div>/);
  assert.doesNotMatch(html, /class="tbar"|<kbd/); // no season ticks, no key hints
  assert.match(app.heroHTML(app.nextUpInfo()), /<div class="sub">S1E1 · Episode 1<\/div>/); // the portrait tablet keeps its banner
  const fresh = episode("fresh", latest, false, 0);
  app = homepage({ shows: [show("Fresh", [fresh])], continue: [fresh], history: [fresh] });
  assert.doesNotMatch(app.Home(), /class="hbar"/); // not started: nothing to show

  // A watched episode paused halfway still shows its progress bar and time left on Home.
  const replayed = episode("replayed", latest, true, 685);
  const other = episode("other", earlier, true, 685);
  app = homepage({ shows: [show("Replayed", [replayed]), show("Other", [other])], continue: [], history: [replayed, other] });
  const rhtml = app.Home();
  assert.match(rhtml, /<div class="hbar"><i style="width:47\.6%"><\/i><\/div>/);
  assert.match(rhtml, /13 min left/);
  assert.match(rhtml, /<span>Resume<\/span>/);
  assert.match(rhtml, /<h3>Other<\/h3><small>13 min left · disk<\/small>/);
  assert.match(rhtml, /<i class="eprog"><i style="width:47\.56/);
});

test("Continue watching captions say only what's left and where (TODO 236)", () => {
  const older = episode("older", earlier);
  const recent = episode("recent", latest);
  older.title = "A long episode name that was cut off";
  const app = homepage({ shows: [show("Older", [older]), show("Recent", [recent])], continue: [older], history: [recent, older] });
  const html = app.Home();
  assert.match(html, /<h3>Older<\/h3><small>[^<·]+ left · disk<\/small>/);
  assert.doesNotMatch(html, /A long episode name/);
});

test("a show on disk and online is one tile in Continue watching (TODO 265)", () => {
  const disk = { ...episode("disk8", earlier), number: 8 };
  const online = { ...episode("stream:140960:9", latest), number: 9 };
  const other = episode("other", "2026-10-14 15:00:00");
  const folder = { ...show("SPY x FAMILY", [disk]), id: "45ff60cc03d1", meta: { anilist_id: 140960 } };
  const release = { ...show("SPY x FAMILY", [online]), id: "aid:140960", kind: "stream" };
  release.seasons[0].label = "Episodes";
  const data = { shows: [folder, show("Other", [other])], streams: [release],
    continue: [disk, online, other], history: [online, disk, other] };
  const app = homepage(data);
  const ids = (L) => [...L].map((x) => x.s.id); // arrays from the page's own realm
  const tiles = (html) => [...html.matchAll(/data-cw="([^"]+)"/g)].map((m) => m[1]);
  // the hero takes the newest copy, and its folder twin doesn't come back as a tile
  assert.equal(app.nextUpInfo().s.id, "aid:140960");
  assert.deepEqual(tiles(app.Home()), ["aid:140960", "Other"]); // the big card, then the row: no folder tile
  assert.deepEqual(ids(app.keepWatching()), ["aid:140960", "Other"]); // the newest played copy speaks for it
  // linked only through the list entry (no AniList id on the folder)
  folder.meta = {};
  data.spine = { entries: [{ id: 140960, local_show_id: "45ff60cc03d1", stream_show_id: "aid:140960" }] };
  data.shows = [...data.shows]; // a fresh library load
  assert.deepEqual(ids(app.keepWatching()), ["aid:140960", "Other"]);
  // Remove from Home on one copy takes the show off, until either copy plays again
  data.continue_hidden = { "45ff60cc03d1": "2026-10-14 15:20:00" };
  assert.deepEqual(ids(app.keepWatching()), ["Other"]);
  disk.last_played = "2026-10-14 15:30:00";
  assert.deepEqual(ids(app.keepWatching()), ["45ff60cc03d1", "Other"]);
});

test("online ticks count on every season of a folder, and back (TODO 265)", () => {
  const ep = (id, number, watched = false) => ({ ...episode(id, null, watched, 0), number });
  // a folder whose Season 3 holds two entries: E1–12 is 99147, E13 on is 104578 (offset −12)
  const s1 = [ep("s1e1", 1, true), ep("s1e2", 2, true)];
  const s3 = [ep("s3e12", 12, true), ep("s3e13", 13), ep("s3e14", 14)];
  const folder = { ...show("Attack on Titan", []), id: "aot", meta: { anilist_id: 16498 }, seasons: [
    { key: 1, season: 1, label: "Season 1", episodes: s1, releases: [{ id: 16498, first_ep: 1, last_ep: null, offset: 0 }] },
    { key: 3, season: 3, label: "Season 3", episodes: s3, releases: [{ id: 99147, first_ep: 1, last_ep: 12, offset: 0 }, { id: 104578, first_ep: 13, last_ep: null, offset: -12 }] }] };
  const on1 = ep("stream:104578:1", 1, true), on2 = ep("stream:104578:2", 2);
  const release = { ...show("Attack on Titan Season 3 Part 2", []), id: "aid:104578", kind: "stream", meta: { anilist_id: 104578 },
    seasons: [{ key: 1, season: 1, label: "Episodes", episodes: [on1, on2], releases: [{ id: 104578, first_ep: 1, last_ep: null, offset: 0 }] }] };
  const app = homepage({ shows: [folder], streams: [release], continue: [], history: [] });
  assert.equal(app.slotSeen(folder, s3[1]), true); // S3E13 = 104578 E1, watched online
  assert.equal(app.pickTarget(folder).ep.id, "s3e14");
  s3[2].watched = true; // S3E14 on disk = 104578 E2: the online release moves on too (marks are read live)
  assert.equal(app.slotSeen(release, on2), true);
  assert.equal(app.slotSeen(folder, s1[0]), true);
  assert.equal(app.slotSeen(release, ep("stray", 0)), false); // no number, no slot
  assert.equal(app.showKey(folder), app.showKey(release)); // and one tile on Home
});

test("a finished season hands Home to the next season you have (TODO 275)", () => {
  const plain = (v) => JSON.parse(JSON.stringify(v)); // the vm's arrays
  const ep = (id, number, watched, played = null) => ({ ...episode(id, played, watched, 0), number });
  const stream = (mid, title, eps) => ({ ...show(title, []), id: "aid:" + mid, kind: "stream", meta: { anilist_id: mid },
    seasons: [{ key: 1, season: 1, label: "Episodes", episodes: eps, releases: [{ id: mid, first_ep: 1, last_ep: null, offset: 0 }] }] });
  const s1 = stream(140960, "SPY x FAMILY", [ep("stream:140960:11", 11, true, earlier), ep("stream:140960:12", 12, true, latest)]);
  const cour2 = stream(142838, "SPY x FAMILY Cour 2", [ep("stream:142838:1", 1, true)]); // marked done, never played here
  const s2 = stream(158927, "SPY x FAMILY Season 2", [ep("stream:158927:1", 1, false), ep("stream:158927:2", 2, false)]);
  const other = show("Other", [episode("o1", "2026-10-13 10:00:00")]);
  const data = { shows: [other], streams: [s1, cour2, s2], continue: [], history: [s1.seasons[0].episodes[1], s1.seasons[0].episodes[0], other.seasons[0].episodes[0]],
    story_next: { 140960: 142838, 142838: 158927, 158927: 177937 } };
  let app = homepage(data);
  const up = app.nextUpInfo();
  assert.equal(up.s.id, "aid:158927"); // Cour 2 is done too: Season 2 E1
  assert.equal(up.ep.id, "stream:158927:1");
  assert.equal(up.label, "Up next");
  assert.deepEqual(plain(app.keepWatching().map((x) => [x.s.id, x.t.ep.id])), [["aid:158927", "stream:158927:1"], ["Other", "o1"]]);
  assert.deepEqual(plain(app.keepWatching(up.s.id).map((x) => x.s.id)), ["Other"]); // not twice beside the banner
  // Season 2 not here (not on disk, not linked): no tile, the old rewatch rule
  app = homepage({ ...data, streams: [s1, cour2] });
  assert.deepEqual(plain(app.keepWatching().map((x) => x.s.id)), ["Other"]);
  assert.equal(app.nextUpInfo().s.id, "Other");
  // no story known yet (franchise not built): as before
  app = homepage({ ...data, story_next: undefined });
  assert.deepEqual(plain(app.keepWatching().map((x) => x.s.id)), ["Other"]);
});

test("an active rewatch tracks progress on Home, Continue watching, and mid-episode resume", () => {
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const ep = (id, number, played = earlier, position = 1440) => ({ ...episode(id, played, true, position), number });
  const s1 = { ...show("SPY x FAMILY", []), id: "aid:140960", kind: "stream", meta: { anilist_id: 140960 },
    seasons: [{ key: 1, season: 1, label: "Episodes",
      episodes: [ep("stream:140960:1", 1, latest), ep("stream:140960:2", 2, earlier, 600), ep("stream:140960:3", 3)],
      releases: [{ id: 140960, first_ep: 1, last_ep: null, offset: 0 }] }] };
  const other = show("Other", [episode("o1", "2026-10-13 10:00:00")]);
  const data = { shows: [other], streams: [s1], continue: [], history: [s1.seasons[0].episodes[0], other.seasons[0].episodes[0]],
    rewatches: { "aid:140960": { started_at: latest, done: [1] } } };
  const app = homepage(data);
  // E1 done in rewatch, E2 next (and paused at 600s -> resumable!)
  assert.equal(app.slotSeen(s1, s1.seasons[0].episodes[0]), true);
  assert.equal(app.slotSeen(s1, s1.seasons[0].episodes[1]), false);
  const up = app.nextUpInfo();
  assert.equal(up.s.id, "aid:140960");
  assert.equal(up.ep.id, "stream:140960:2");
  assert.equal(up.label, "Rewatching");
  const html = app.Home();
  assert.match(html, /rewatching/);
  assert.match(html, /<span>Resume<\/span>/);
  assert.match(html, /class="hbar"/); // mid-episode progress bar shows on watched rewatch episode
  // Keep watching keeps the rewatching show when another show is newer
  other.seasons[0].episodes[0].last_played = "2026-10-15 10:00:00";
  assert.deepEqual(plain(app.keepWatching("Other").map((x) => [x.s.id, x.t.ep.id])), [["aid:140960", "stream:140960:2"]]);
  // Ending the rewatch restores normal watched state (and clears mid-rewatch pause) so it leaves Continue watching
  s1.seasons[0].episodes[1].position = 0;
  data.rewatches = {};
  assert.equal(app.slotSeen(s1, s1.seasons[0].episodes[1]), true);
  assert.deepEqual(plain(app.keepWatching("Other").map((x) => x.s.id)), []);
});

test("a chained rewatch on an already-watched sequel hands Home to Season 2 E1", () => {
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const ep = (id, number, played = null) => ({ ...episode(id, played, true, 0), number });
  const stream = (mid, title, eps) => ({ ...show(title, []), id: "aid:" + mid, kind: "stream", meta: { anilist_id: mid },
    seasons: [{ key: 1, season: 1, label: "Episodes", episodes: eps, releases: [{ id: mid, first_ep: 1, last_ep: null, offset: 0 }] }] });
  const s1 = stream(140960, "SPY x FAMILY", [ep("stream:140960:1", 1, earlier), ep("stream:140960:2", 2, latest)]);
  const s2 = stream(158927, "SPY x FAMILY Season 2", [ep("stream:158927:1", 1), ep("stream:158927:2", 2)]);
  const other = show("Other", [episode("o1", "2026-10-13 10:00:00")]);
  const app = homepage({
    shows: [other], streams: [s1, s2], continue: [], history: [s1.seasons[0].episodes[1], other.seasons[0].episodes[0]],
    story_next: { 140960: 158927 },
    rewatches: { "aid:158927": { started_at: latest, done: [] } },
  });
  const up = app.nextUpInfo();
  assert.equal(up.s.id, "aid:158927");
  assert.equal(up.ep.id, "stream:158927:1");
  assert.equal(up.label, "Rewatching");
  assert.deepEqual(plain(app.keepWatching().map((x) => [x.s.id, x.t.ep.id])), [["aid:158927", "stream:158927:1"], ["Other", "o1"]]);
});

test("a synced active rewatch with no last_played and no story_next shows on Home and Continue watching", () => {
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const ep = (id, number, played = null) => ({ ...episode(id, played, true, 0), number });
  const stream = (mid, title, eps) => ({ ...show(title, []), id: "aid:" + mid, kind: "stream", meta: { anilist_id: mid },
    seasons: [{ key: 1, season: 1, label: "Episodes", episodes: eps, releases: [{ id: mid, first_ep: 1, last_ep: null, offset: 0 }] }] });
  const cour2 = stream(142838, "SPY x FAMILY Cour 2", [ep("stream:142838:1", 1), ep("stream:142838:2", 2)]);
  const mdd = stream(132405, "My Dress-Up Darling", [{ ...episode("stream:132405:4", "2026-11-20 18:00:00", false, 500), number: 4 }]);
  const str = stream(129201, "Summer Time Rendering", [{ ...episode("stream:129201:14", "2026-11-22 18:00:00", false, 300), number: 14 }]);

  // 1. Active rewatch is the newest touch (and has no last_played or story_next) -> leads as hero, not duplicated in Continue watching.
  const data = {
    shows: [],
    streams: [cour2, mdd, str],
    continue: [str.seasons[0].episodes[0], mdd.seasons[0].episodes[0]],
    history: [str.seasons[0].episodes[0], mdd.seasons[0].episodes[0]],
    rewatches: { "aid:142838": { started_at: "2026-11-24 10:00:00", updated_at: "2026-11-24 10:00:00", done: [] } },
  };
  let app = homepage(data);
  let up = app.nextUpInfo();
  assert.equal(up.s.id, "aid:142838");
  assert.equal(up.ep.id, "stream:142838:1");
  assert.equal(up.label, "Rewatching");
  assert.deepEqual(plain(app.keepWatching(up.s.id).map((x) => x.s.id)), ["aid:129201", "aid:132405"]);
  assert.deepEqual(plain(app.keepWatching().map((x) => [x.s.id, x.t.ep.id])), [
    ["aid:142838", "stream:142838:1"],
    ["aid:129201", "stream:129201:14"],
    ["aid:132405", "stream:132405:4"],
  ]);

  // 2. When another show was played more recently, the active rewatch sits in Continue watching in timestamp order.
  data.rewatches["aid:142838"] = { started_at: "2026-11-21 10:00:00", updated_at: "2026-11-21 10:00:00", done: [] };
  up = app.nextUpInfo();
  assert.equal(up.s.id, "aid:129201");
  assert.deepEqual(plain(app.keepWatching(up.s.id).map((x) => [x.s.id, x.t.ep.id])), [
    ["aid:142838", "stream:142838:1"],
    ["aid:132405", "stream:132405:4"],
  ]);

  // 3. Works even when history and continue are completely empty.
  app = homepage({ shows: [], streams: [cour2], continue: [], history: [],
    rewatches: { "aid:142838": { started_at: "2026-11-24 10:00:00", done: [] } } });
  assert.equal(app.nextUpInfo().s.id, "aid:142838");
  assert.equal(app.nextUpInfo().ep.id, "stream:142838:1");
  assert.equal(app.nextUpInfo().label, "Rewatching");

  // 4. Respects continue_hidden: hidden after rewatch -> gone; rewatch updated/started after hide -> back.
  app = homepage({
    shows: [], streams: [cour2, str], continue: [str.seasons[0].episodes[0]], history: [str.seasons[0].episodes[0]],
    rewatches: { "aid:142838": { started_at: "2026-11-21 10:00:00", updated_at: "2026-11-21 10:00:00", done: [] } },
    continue_hidden: { "aid:142838": "2026-11-21 12:00:00" },
  });
  assert.deepEqual(plain(app.keepWatching("aid:129201").map((x) => x.s.id)), []);
  app.S.data.rewatches["aid:142838"].updated_at = "2026-11-21 13:00:00";
  assert.deepEqual(plain(app.keepWatching("aid:129201").map((x) => x.s.id)), ["aid:142838"]);

  // 5. All episodes done in the rewatch (pickTarget returns "rewatch") -> excluded.
  app.S.data.rewatches["aid:142838"].done = [1, 2];
  assert.deepEqual(plain(app.keepWatching("aid:129201").map((x) => x.s.id)), []);
});
