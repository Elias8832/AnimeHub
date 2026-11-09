/* Playlists in the Library: how a show is named, and that it gets the Library's own card. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const from = source.indexOf("/* @pl:start */"), to = source.indexOf("/* @pl:end */");
assert.ok(from >= 0 && to > from);

function context(mode, entries, shows, playlists = [], untracked = []) {
  const show = (id) => shows.find((x) => x.id === id) || null;
  const ctx = vm.createContext({
    S: { lib: { tab: "PLAYLISTS", pl: 1 }, data: { spine: { mode, entries, untracked }, playlists, shows, streams: [] } },
    aniMode: () => mode === "anilist", showById: show, allShows: () => shows,
    entryCard: (e) => `entry:${e.id}`, showCard: (s) => `show:${s.id}`, FMT: { TV: "TV" },
    LUCIDE: { "cloud-rain": "<path/>" }, lu: (n) => `<svg data-icon="${n}"></svg>`,
    esc: (x) => String(x), ico: (n) => `[${n}]`, poster: (t, u) => `<img alt="${t}" src="${u}">`,
    hl: (t, q) => String(t).replace(new RegExp(q, "i"), (m) => `<mark>${m}</mark>`),
  });
  vm.runInContext(source.slice(from, to) + source.slice(source.indexOf("function plWall() {"), source.indexOf("function plResPaint()")) + ";this.api = { plEntryRef, plShowRef, plFind, plItemCard, plHead, plReorder, plSlotAt, plBox, plStack, plPos, plAddCands, plHomeHTML, plWall, plMenuRows };", ctx);
  ctx.api.ctx = ctx;
  return ctx.api;
}

test("a show is the same on a list with or without AniList", () => {
  assert.equal(context("anilist", [], []).plEntryRef({ id: 5 }), "aid:5");
  const local = context("local", [], []);
  assert.equal(local.plEntryRef({ id: "abc", media_id: 5 }), "aid:5");
  assert.equal(local.plEntryRef({ id: "abc", media_id: null, local_show_id: "abc" }), "sid:abc");
  assert.equal(local.plShowRef({ id: "abc", meta: { anilist_id: 5 } }), "aid:5");
  assert.equal(local.plShowRef({ id: "abc", meta: null }), "sid:abc");
  assert.equal(local.plShowRef({ id: "aid:5" }), "aid:5");
});

test("a show gets the Library's own card, with no finished marks", () => {
  const api = context("local", [{ id: "abc", media_id: 7, local_show_id: "abc" }], [{ id: "abc" }, { id: "loose" }]);
  assert.match(api.plItemCard({ ref: "aid:7", title: "x" }, 0, [1, 2]), /entry:abc/);
  assert.match(api.plItemCard({ ref: "sid:loose", title: "x" }, 1, [1, 2]), /show:loose/);
  const gone = api.plItemCard({ ref: "aid:9", title: "Gone show", poster: "g.jpg" }, 0, [1]);
  assert.match(gone, /data-a="plopen"/);
  assert.doesNotMatch(gone, /tick|done/);
});

test("a local entry is found by its AniList id, not its folder id", () => {
  const api = context("local", [{ id: "abc", media_id: 7, local_show_id: "abc" }], [{ id: "abc" }]);
  assert.equal(api.plFind("aid:7").show.id, "abc");
  assert.equal(api.plFind("aid:7").entry.id, "abc");
});

test("the playlist header has no Play button", () => {
  const { plHead } = context("local", [], [{ id: "a" }]);
  assert.doesNotMatch(plHead({ name: "x", items: [{ ref: "sid:a", title: "Frieren" }] }), /plplay|Play /);
});

test("a drop moves one show and keeps the rest in order", () => {
  const { plReorder } = context("local", [], []);
  assert.deepEqual([...plReorder(["a", "b", "c", "d"], 0, 2)], ["b", "c", "a", "d"]);
  assert.deepEqual([...plReorder(["a", "b", "c", "d"], 3, 0)], ["d", "a", "b", "c"]);
});

test("the cell under the mouse is the nearest one", () => {
  const { plSlotAt } = context("local", [], []);
  const cell = (left, top) => ({ left, top, width: 100, height: 150 });
  const slots = [cell(0, 0), cell(120, 0), cell(0, 200), cell(120, 200)];
  assert.equal(plSlotAt(slots, 10, 10), 0);
  assert.equal(plSlotAt(slots, 200, 260), 3);
  assert.equal(plSlotAt(slots, 500, 0), 1);
});

test("the Playlists intro is a real sentence, not code", () => {
  const line = source.slice(source.indexOf("if (isPl) main ="), source.indexOf("<div id=\"wall\"></div>`;", source.indexOf("if (isPl) main =")));
  assert.doesNotMatch(line, /'<p class="intro">[^']*\$\{/);
});

const list = (over = {}) => ({ id: 3, name: "Rainy <days>", color: "#e58ccf", note: "slow & cosy", icon: "cloud-rain", pinned: false,
  items: [{ ref: "aid:1", title: "Frieren", poster: "f.jpg" }, { ref: "sid:x", title: "Blue Box", poster: null }], ...over });

test("a playlist box: icon, name, note, colour and its shows as a stack of cards", () => {
  const html = context("local", [], []).plBox(list(), 0);
  assert.match(html, /--plc:#e58ccf/);
  assert.match(html, /data-icon="cloud-rain"/);
  assert.match(html, /Rainy/);
  assert.match(html, /slow & cosy/);
  assert.equal((html.match(/class="plcard/g) || []).length, 2);
  assert.match(html, /data-a="plgo" data-v="3"/);
});

test("an unknown icon name draws nothing", () => {
  const html = context("local", [], []).plStack(list({ icon: "not-an-icon", items: [] }));
  assert.doesNotMatch(html, /data-icon/);
  assert.match(html, /No shows yet/);
});

test("a stack: the front card first, two peeking behind, the rest hidden, a counter", () => {
  const items = Array.from({ length: 5 }, (_, i) => ({ ref: `aid:${i + 1}`, title: `S${i}`, poster: `p${i}` }));
  const api = context("local", [], []);
  const pos = (html) => [...html.matchAll(/data-pos="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(pos(api.plStack(list({ items }))), ["0", "1", "2", "hide", "hide"]);
  assert.match(api.plStack(list({ items })), /<b>1<\/b> \/ 5/);
  api.ctx.S.plFlip = { 3: 3 }; // flipped to the fourth show: it is in front, the first two wrap round
  assert.deepEqual(pos(api.plStack(list({ items }))), ["2", "hide", "hide", "0", "1"]);
  assert.equal(api.plPos(0, 4, 5), "1");
});

test("the front card shows the show's banner when it has one", () => {
  const entries = [{ id: "a", media_id: 1, title: "F", poster: "poster.jpg", banner: "banner.jpg", local_show_id: null }];
  const html = context("local", entries, []).plStack(list({ items: [{ ref: "aid:1", title: "F", poster: "old.jpg" }] }));
  assert.match(html, /class="plcard wide"/);
  assert.match(html, /src="banner.jpg"/);
  assert.doesNotMatch(html, /plctr/); // one show: nothing to flip
});

test("a box opens its playlist; the front show's name chip opens the show", () => {
  const api = context("local", [], []);
  const box = api.plBox(list(), 0);
  assert.match(box, /class="plbox"[^>]*data-a="plgo" data-v="3"/);
  assert.doesNotMatch(box, /class="plcard[^"]*" data-pos="0" data-a/);
  assert.match(box, /class="fname" data-a="plopen" data-v="aid:1"/);
});

test("Add a show finds library shows by every typed word, minus the ones on the list", () => {
  const entries = [{ id: "a", media_id: 1, title: "Frieren: Beyond Journey's End", poster: "f", format: "TV", year: 2023 },
    { id: "b", media_id: 2, title: "Frieren Mini", poster: "m" }, { id: "c", media_id: 3, title: "Blue Box", poster: "b" }];
  const api = context("local", entries, []);
  const got = api.plAddCands("frie end", { items: [] });
  assert.deepEqual([...got.map((x) => x.ref)], ["aid:1"]);
  assert.equal(got[0].sub, "TV · 2023");
  assert.deepEqual([...api.plAddCands("frieren", { items: [{ ref: "aid:1" }] }).map((x) => x.ref)], ["aid:2"]);
  assert.equal(api.plAddCands("  ", { items: [] }).length, 0);
});

test("only pinned playlists get a row on Home", () => {
  const api = context("local", [], [], [list({ id: 1, name: "Pinned one", pinned: true }), list({ id: 2, name: "Not pinned" })]);
  const html = api.plHomeHTML();
  assert.match(html, /Pinned one/);
  assert.doesNotMatch(html, /Not pinned/);
  assert.match(html, /data-a="plgoto" data-v="1"/);
  assert.equal((html.match(/class="plbox"/g) || []).length, 1);
});

test("a stack holds thirty shows at most; the box still counts them all", () => {
  const items = Array.from({ length: 35 }, (_, i) => ({ ref: `aid:${i + 1}`, title: `S${i}`, poster: null }));
  const html = context("local", [], []).plBox(list({ items }), 0);
  assert.equal((html.match(/class="plcard/g) || []).length, 30);
  assert.match(html, /35 shows/);
});

test("every box on the Playlists tab opens its playlist", () => {
  const api = context("local", [], [], [list({ id: 1 }), list({ id: 2 })]);
  api.ctx.S.lib.pl = null; // the overview, no playlist open
  const html = api.plWall().join("");
  assert.equal((html.match(/data-a="plgo"/g) || []).length, 2);
  assert.doesNotMatch(html, /object Object/);
});

test("an open playlist's header is one slim bar: name, count, add box, buttons; no cover", () => {
  const html = context("local", [], []).plHead(list());
  assert.match(html, /Rainy/);
  assert.match(html, /2 shows · slow & cosy/);
  assert.match(html, /id="plq"/);
  assert.doesNotMatch(html, /plstack|plcover/);
});

test("Settings: four ways to open a stack, a ‹ value › to pick one, the chosen one shown open", () => {
  const block = source.slice(source.indexOf("const PL_FANS = "), source.indexOf("/* A segmented control for one Appearance preference. */"));
  const row_ = source.slice(source.indexOf("const srow = "), source.indexOf("const onoff = "));
  const ctx = vm.createContext({ S: { data: { spine: { entries: [{ title: "F", banner: "f.jpg" }] } } }, lookGet: () => "cascade",
    esc: (x) => String(x), ico: (n) => `<svg data-i="${n}"></svg>`, poster: (t, u) => `<img alt="${t}" src="${u}">` });
  vm.runInContext(row_ + block + ";this.row = plFanRow();", ctx);
  const row = ctx.row;
  assert.equal((row.match(/data-k="fan"/g) || []).length, 3); // ‹, the list, ›
  for (const m of ["fan", "wide", "cascade", "peek"]) assert.match(row, new RegExp(`<option value="${m}"`));
  assert.match(row, /<option value="cascade" selected>Cascade<\/option>/);
  assert.match(row, /data-a="look" data-k="fan" data-v="cascade"|data-a="look" data-k="fan" data-v="peek"/);
  assert.match(row, /class="plpv" data-fan="cascade"/); // the picture shows the chosen one
  assert.match(row, /src="f.jpg"/);
  assert.match(source, /fan: \["fan", "wide", "cascade", "peek"\]/);
});

/* The add menu (TODO 189, mockup A): ticks, find as you type, make one by name. */
test("the add menu ticks the playlists a show is on and counts them", () => {
  const { plMenuRows } = context("local", [], []);
  const all = [{ id: 1, name: "Interested", items: [] }, { id: 2, name: "Weekend binge", color: "#e5737a", items: [{ ref: "aid:7" }] }];
  const r = plMenuRows(all, "aid:7", "", null);
  assert.match(r.rows, /data-v="2" role="menuitemcheckbox" aria-checked="true"/);
  assert.match(r.rows, /data-v="1" role="menuitemcheckbox" aria-checked="false"/);
  assert.equal(r.foot, "on 1 playlist");
  assert.doesNotMatch(r.rows, /plmake/);
  assert.match(plMenuRows(all, "aid:7", "", 2).rows, /Added · /);
  assert.equal(plMenuRows(all, "aid:9", "", null).foot, "on no playlist yet");
});

test("typing filters the playlists and offers to make one by that name", () => {
  const { plMenuRows } = context("local", [], []);
  const all = [{ id: 1, name: "Interested", items: [] }, { id: 2, name: "Weekend binge", items: [] }];
  const r = plMenuRows(all, "aid:7", "week", null);
  assert.match(r.rows, /<mark>Week<\/mark>end binge/);
  assert.doesNotMatch(r.rows, /Interested/);
  assert.match(r.rows, /data-a="plmake".*Make “week” and add/);
  assert.doesNotMatch(plMenuRows(all, "aid:7", " interested ", null).rows, /plmake/); // that name exists already
});

test("with no playlists the menu says how to make the first", () => {
  const { plMenuRows } = context("local", [], []);
  const r = plMenuRows([], "aid:7", "", null);
  assert.equal(r.rows, "");
  assert.match(r.note, /Type a name/);
  assert.equal(r.foot, "");
  assert.match(plMenuRows([], "aid:7", "Isekai", null).rows, /Make “Isekai” and add/);
});
