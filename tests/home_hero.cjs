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

function homepage(data) {
  const context = vm.createContext({
    S: { data, view: "home", showId: null, seasonIdx: 0, showBack: "home", cat: null, q: "", menu: null },
    aniMode: () => false, // spine mode; the airing panel is covered elsewhere
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
  assert.match(html, /<div class="upn" style="--h:[^"]*"><div class="upc">/); // hue placeholder, no art layer
});

test("empty activity and removed episodes do not crash the hero picker", () => {
  const app = homepage({ shows: [], continue: [], history: [] });
  assert.equal(app.nextUpInfo(), null);
  app.S.data.history = [episode("missing", latest)];
  assert.equal(app.nextUpInfo(), null);
});

test("the latest fully watched show gets a rewatch banner", () => {
  const older = episode("older", earlier);
  const finished = episode("finished", latest, true);
  const app = homepage({ shows: [show("Older", [older]), show("Finished", [finished])],
    continue: [older], history: [finished, older] });
  assert.equal(app.nextUpInfo().s.title, "Finished");
  assert.equal(app.nextUpInfo().label, "Rewatch");
});

test("homepage renders the new hero, excludes its duplicate tile, and targets its buttons", () => {
  const older = episode("older", earlier);
  const preview = episode("preview", latest, false, 13.6);
  const app = homepage({ shows: [show("Older", [older]), show("Recent", [preview])],
    continue: [older], history: [preview, older] });
  const html = app.Home();
  assert.match(html, /^<div class="home2"><div class="hello">.*?<\/div><div class="upn"/s); // headline, then the up-next card
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
