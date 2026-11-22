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
  assert.match(html, /data-a="play" data-v="preview"/); // hero buttons target the hero episode
  assert.match(html, /data-a="open" data-v="Recent"/);
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
