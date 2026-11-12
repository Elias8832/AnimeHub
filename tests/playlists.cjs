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
    isPhone: () => !!context.phone,
    entryCard: (e) => `entry:${e.id}`, showCard: (s) => `show:${s.id}`, FMT: { TV: "TV" },
    LUCIDE: { "cloud-rain": "<path/>" }, lu: (n) => `<svg data-icon="${n}"></svg>`,
    esc: (x) => String(x), ico: (n) => `[${n}]`, poster: (t, u) => `<img alt="${t}" src="${u}">`,
    hl: (t, q) => String(t).replace(new RegExp(q, "i"), (m) => `<mark>${m}</mark>`),
    epTotal: (s) => s.seasons.reduce((a, x) => a + x.episodes.length, 0), epWatched: (s) => s.seasons.reduce((a, x) => a + x.episodes.filter((e) => e.watched).length, 0),
    pickTarget: (s) => { for (const sea of s.seasons) { const ep = sea.episodes.find((e) => !e.watched); if (ep) return { sea, ep, kind: ep.position ? "resume" : "play" }; } return null; },
    resumable: (ep) => !!ep.position, epCode: (sea, ep) => `E${ep.number}`, epName: () => null, cssUrl: (u) => `url(${u})`,
    statusLabel: (st) => ({ CURRENT: "Watching", COMPLETED: "Completed", PLANNING: "Planning" })[st] || st,
  });
  vm.runInContext(source.slice(from, to) + source.slice(source.indexOf("function plWall() {"), source.indexOf("function plResPaint()")) + ";this.api = { plEntryRef, plShowRef, plFind, plItemCard, plHead, plNext, plState, plFindGroups, plFindList, plFindHTML, plReorder, plSlotAt, plBox, plStack, plPos, plAddCands, plHomeHTML, plWall, plMenuRows };", ctx);
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

test("a show is one numbered row: its name, facts, progress and list; the row opens it", () => {
  const api = context("local", [{ id: "abc", media_id: 7, local_show_id: "abc", title: "Frieren", format: "TV", year: 2023, progress: 4, episodes: 28, status: "CURRENT" }], [{ id: "abc", seasons: [] }]);
  const row = api.plItemCard({ ref: "aid:7", title: "x" }, 1, [1, 2, 3], 1);
  assert.match(row, /class="plit plrow cur"/); // the up-next show is lit
  assert.match(row, /<span class="plno">2<\/span>/);
  assert.match(row, /Frieren/);
  assert.match(row, /TV · 2023/);
  assert.match(row, /4 \/ 28/);
  assert.match(row, /Watching/);
  assert.match(row, /data-a="plopen" data-v="aid:7"/);
  assert.match(row, /data-a="plrow"/); // ⋯
  const gone = api.plItemCard({ ref: "aid:9", title: "Gone show", poster: "g.jpg" }, 0, [1]);
  assert.match(gone, /Gone show/);
  assert.match(gone, /g\.jpg/);
  assert.doesNotMatch(gone, /undefined|NaN/);
});

test("a local folder's row counts its watched files", () => {
  const ep = (n, w) => ({ id: "e" + n, number: n, watched: w });
  const api = context("local", [], [{ id: "a", title: "Mushishi", seasons: [{ episodes: [ep(1, true), ep(2, true)] }] }, { id: "b", title: "Haibane", seasons: [{ episodes: [ep(1, true), ep(2, false)] }] }]);
  assert.match(api.plItemCard({ ref: "sid:a", title: "Mushishi" }, 0, [1]), /plrow fin[\s\S]*2 \/ 2[\s\S]*Completed/);
  assert.match(api.plItemCard({ ref: "sid:b", title: "Haibane" }, 0, [1]), /1 \/ 2[\s\S]*Watching/);
});

test("a local entry is found by its AniList id, not its folder id", () => {
  const api = context("local", [{ id: "abc", media_id: 7, local_show_id: "abc" }], [{ id: "abc" }]);
  assert.equal(api.plFind("aid:7").show.id, "abc");
  assert.equal(api.plFind("aid:7").entry.id, "abc");
});

test("the banner's up next is the first show not finished, with its next episode", () => {
  const ep = (n, w, pos = 0) => ({ id: "e" + n, number: n, watched: w, position: pos });
  const api = context("local", [], [{ id: "a", title: "Mushishi", seasons: [{ episodes: [ep(1, true)] }] }, { id: "b", title: "Haibane", seasons: [{ episodes: [ep(1, true), ep(2, false, 300)] }] }]);
  const p = { id: 1, name: "x", items: [{ ref: "sid:a", title: "Mushishi" }, { ref: "sid:b", title: "Haibane" }] };
  assert.equal(api.plNext(p).i, 1);
  const html = api.plHead(p);
  assert.match(html, /2 shows · 1 finished/);
  assert.match(html, /up next<\/small><b>Haibane<\/b><span>E2<\/span>/);
  assert.match(html, /data-a="play" data-v="e2"[^>]*>.*Resume/);
  assert.match(html, /data-a="plopen" data-v="sid:b"/); // the banner itself opens the show
});

test("a show with no episodes here gets no button (the banner opens it); all finished gets no play", () => {
  const api = context("local", [{ id: "x", media_id: 5, title: "Blue Box", progress: 5, episodes: 12, status: "CURRENT" }], []);
  const html = api.plHead({ id: 1, name: "x", items: [{ ref: "aid:5", title: "Blue Box" }] });
  assert.doesNotMatch(html, /data-a="play"/);
  assert.match(html, /5 of 12 watched/);
  assert.doesNotMatch(html, /Open show|data-a="play"/); // the whole banner opens the show
  const done = context("local", [{ id: "y", media_id: 6, title: "Done", progress: 3, episodes: 3, status: "COMPLETED" }], []);
  const h2 = done.plHead({ id: 1, name: "x", items: [{ ref: "aid:6", title: "Done" }] });
  assert.match(h2, /finished/);
  assert.doesNotMatch(h2, /data-a="play"/);
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
  assert.match(html, /no shows yet/i);
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

test("an open playlist's banner: back, icon, name, count and note, pin and ⋯, then the add box", () => {
  const html = context("local", [], []).plHead(list());
  assert.match(html, /data-a="plback"/);
  assert.match(html, /<h1><span class="plico"><svg data-icon="cloud-rain"><\/svg><\/span>Rainy <days><\/h1>/);
  assert.match(html, /<p class="plnote2">slow & cosy<\/p>/);
  assert.match(html, /2 shows/);
  assert.match(html, /In this order/);
  assert.match(html, /data-icon="cloud-rain"/);
  assert.match(html, /data-a="plpin"/);
  assert.match(html, /data-a="plmenu" data-v="3"/);
  assert.match(html, /data-a="plfocus">[^]*?Add a show<\/button>/); // opens the Add shows popup
  assert.doesNotMatch(html, /id="plq"/);
  assert.match(html, /In this order/);
  assert.doesNotMatch(html, /plstack|data-a="pledit"|data-a="pldelete"/); // edit and delete live in ⋯
});

test("an empty playlist says so once, in the banner, with Add a show", () => {
  const html = context("local", [], []).plHead(list({ items: [] }));
  assert.match(html, /no shows yet/i);
  assert.match(html, /data-a="plfocus"/);
  assert.doesNotMatch(html, /In this order|class="plhd"/);
});

test("each playlist picks its layout: banner or cover on top, a list or posters below", () => {
  const api = context("local", [], []);
  const banner = api.plHead(list()), cover = api.plHead(list({ head: "cover", view: "posters" }));
  assert.match(banner, /class="plhero/);
  assert.match(banner, /data-a="pllayout"/);
  assert.match(banner, /class="plhd"/);
  assert.match(cover, /class="plcover2"[\s\S]*class="plcov/);
  assert.doesNotMatch(cover, /class="plhero|class="plhd"/); // posters have no column heads
  assert.match(cover, /drag a poster to move it/);
  const post = api.plItemCard({ ref: "aid:7", title: "x" }, 9, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], -1, "posters");
  assert.match(post, /class="plit plpost"[\s\S]*class="plbig two"[^>]*>10<[\s\S]*data-a="plrow"[\s\S]*data-a="plmove"/);
  assert.doesNotMatch(post, /class="plr"|class="plit plrow/);
});

test("a phone gets the panel: cover, name, one wide Play for the up-next show, Pin · Layout · ⋯", () => {
  context.phone = true;
  try {
    const api = context("local", [], []);
    const html = api.plHead(list({ head: "banner" }));
    assert.match(html, /class="plpanelh"[\s\S]*data-a="plback"[\s\S]*class="plcov[\s\S]*Playlist<\/small><h1>Rainy <days><\/h1>/); // esc is a stub here
    assert.match(html, /<div class="plbtns">[\s\S]*data-a="plpin"[\s\S]*data-a="pllayout"[\s\S]*data-a="plmenu"/);
    assert.doesNotMatch(html, /class="plhero|class="plsec|class="plhd"/); // no banner, no PC heading: the shows follow in the panel
  } finally { context.phone = false; }
});

test("find shows: before typing, your Watching and Planning shows not on the list; typing searches", () => {
  const e = (id, title, status) => ({ id, media_id: id, title, status, format: "TV", year: 2020 });
  const api = context("local", [e(1, "Frieren", "CURRENT"), e(2, "Mushishi", "PLANNING"), e(3, "Haibane", "CURRENT"), e(4, "Done", "COMPLETED")], []);
  const p = { id: 1, name: "x", items: [{ ref: "aid:3", title: "Haibane" }] };
  const g = api.plFindGroups("", p);
  assert.equal(JSON.stringify(g.map((x) => [x.cap, x.rows.map((r) => r.title)])), JSON.stringify([["Suggestions", ["Frieren", "Mushishi"]]]));
  assert.equal(api.plFindGroups("mush", p)[0].rows.map((r) => r.title).join(), "Mushishi");
  const html = api.plFindHTML(p);
  assert.match(html, /<h2 id="dt">Add shows<\/h2>[\s\S]*id="plq"[\s\S]*data-a="close"/);
  assert.match(html, /<button class="plfr" data-a="plfadd" data-v="aid:1"[\s\S]*?<i>Add<\/i><\/span><\/button>/); // the whole row adds
  /* a show added here keeps its row, marked, so a second click takes it off */
  api.ctx.S.plJust2 = new Set(["aid:3"]);
  assert.match(api.plFindList(p), /plfr added[^>]*data-v="aid:3"[\s\S]*Added/);
  assert.doesNotMatch(api.plFindList({ ...p, items: [] }), /plfr added/); // taken off from its row's ⋯ menu: Add again
  assert.match(api.plFindList({ ...p, items: [] }), /Type a name|plfr/);
  api.ctx.S.plq = "zzz";
  assert.match(api.plFindList(p), /Nothing in your library called “zzz”/);
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

test("the icon picker: every icon has a drawing, none twice, old icons still drawn", () => {
  const a = source.indexOf("const LUCIDE = {"), b = source.indexOf("const lu = ");
  const ctx = vm.createContext({});
  vm.runInContext(source.slice(a, b) + ";this.L = LUCIDE; this.G = LU_GROUPS;", ctx);
  const names = ctx.G.flatMap(([, g]) => g.split(" "));
  assert.ok(names.length >= 100);
  assert.equal(new Set(names).size, names.length);
  for (const n of names) assert.ok(ctx.L[n], n);
  for (const n of ["folder-code", "terminal", "git-branch", "cloud-rain"]) assert.ok(ctx.L[n], n); // saved on playlists before
});
