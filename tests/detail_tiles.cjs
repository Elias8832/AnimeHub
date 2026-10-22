/* Episode-tile artwork is a pure function of the show/season/thumb data —
   never of the watch state. A mark used to rebuild the whole page, which
   re-created every image element (tiles blanked, the hero poster reloaded)
   and made the art look like it changed (Elias, 2026-10-17). These checks
   pin the mapping itself: watched flips may change class/tick/label only. */
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

function context() {
  // epLoading (the play spinner) lives outside the extracted blocks and
  // reads playback state; artwork never depends on it: nothing is loading.
  const ctx = vm.createContext({ S: { data: null }, epLoading: () => false });
  vm.runInContext(
    block("/* @pure:start */", "/* @pure:end */") +
    block("/* @detail:start */", "/* @detail:end */"),
    ctx,
  );
  return ctx;
}

function show(sea) {
  return {
    id: "s1", title: "Test Show", banner: "/img/s1.banner.jpg", poster: "/img/s1.jpg",
    meta: {}, seasons: [sea],
  };
}
function episode(number, watched = false) {
  return { id: "ep" + number, number, watched, position: 0, duration: 1440, files: [] };
}
const attr = (html, name) => (html.match(new RegExp(`${name}="([^"]*)"`)) || [])[1] ?? null;

test("thumb mapping follows the episode number", () => {
  const c = context();
  const sea = { label: "Season 1", season: 1, thumbs: { "2": "/epimg/x.2.jpg", "3": "/epimg/x.3.jpg" }, titles: null };
  const s = show(sea);
  assert.equal(attr(c.epTile(s, sea, episode(3), null), "data-bg"), "/epimg/x.3.jpg");
  assert.equal(attr(c.epTile(s, sea, episode(2), null), "data-bg"), "/epimg/x.2.jpg");
});

test("watched state never changes the artwork mapping", () => {
  const c = context();
  const sea = { label: "Season 1", season: 1, thumbs: { "3": "/epimg/x.3.jpg" }, titles: null };
  const s = show(sea);
  const unwatched = c.epTile(s, sea, episode(3, false), null);
  const watched = c.epTile(s, sea, episode(3, true), null);
  assert.equal(attr(watched, "data-bg"), attr(unwatched, "data-bg"));
  assert.equal(attr(watched, "data-bp"), attr(unwatched, "data-bp"));
  assert.match(unwatched, /class="ep"/);
  assert.match(watched, /class="ep watched"/);
  assert.match(watched, /class="tick"/);
  assert.ok(!/class="tick"/.test(unwatched));
});

test("no-thumb fallback is the show poster, same crop on every tile", () => {
  /* Slicing the banner per episode gave every tile a different, magnified
     crop — "the episodes get different images… it zooms in" (2026-10-17). */
  const c = context();
  const sea = { label: "Season 1", season: 1, thumbs: null, titles: null };
  const s = show(sea);
  const a = c.epTile(s, sea, episode(2, false), null);
  const b = c.epTile(s, sea, episode(6, true), null);
  assert.equal(attr(a, "data-bg"), "/img/s1.jpg", "poster, not the banner");
  assert.equal(attr(b, "data-bg"), "/img/s1.jpg");
  assert.equal(attr(a, "data-bp"), attr(b, "data-bp"), "one crop for all tiles");
  assert.equal(attr(a, "data-bp"), "50% 22%");

  const bannerOnly = show({ label: "Season 1", season: 1, thumbs: null, titles: null });
  bannerOnly.poster = null;
  const t = c.epTile(bannerOnly, bannerOnly.seasons[0], episode(2), null);
  assert.equal(attr(t, "data-bg"), "/img/s1.banner.jpg", "banner only when there is no poster");
  assert.equal(attr(t, "data-bp"), "50% 50%", "and never sliced per episode");
});

test("applyWatch moves only the marked episodes' watch facts", () => {
  const c = context();
  const eps = [episode(1), episode(2), episode(3)];
  eps[0].position = 300; eps[0].last_played = "2026-10-17 00:00:00";
  const shows = [{ seasons: [{ episodes: eps }, { episodes: [episode(4)] }] }];
  assert.equal(c.applyWatch(shows, ["ep2", "ep4"], true, false), 2);
  assert.equal(eps[1].watched, true);
  assert.equal(shows[0].seasons[1].episodes[0].watched, true);
  assert.equal(eps[0].watched, false);
  assert.equal(eps[2].watched, false);
  assert.equal(eps[0].position, 300, "unmarked progress untouched");

  assert.equal(c.applyWatch(shows, ["ep1"], false, true), 1);
  assert.equal(eps[0].watched, false);
  assert.equal(eps[0].position, 0, "reset clears the resume position");
  assert.equal(eps[0].last_played, null);
});
