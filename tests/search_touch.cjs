/* The phone's search ranking (TODO 238): one top hit, exact commands and
   settings over AniList's loose guesses, better-known shows first. */
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

const SETTINGS = [["Rescan", "library", "rescan scan refresh metadata"], ["Library folder", "library", "folder path library directory browse rescan scan"],
  ["Season reminder", "player", "recap recaps season before previous remind remember new season popup last episode", "recap_ask"],
  ["Remember audio and subtitles", "subs", "audio subtitle subs track language dual remember", "remember_tracks"]];
function model(q, { shows = [], al = [], scope } = {}) {
  const ctx = vm.createContext({
    S: { alres: al }, alSearchable: () => true, candLabel: (x) => x.t,
    srLocal: (ql) => shows.filter((s) => s.title.toLowerCase().includes(ql)).map((s) => ({ r: s.title.toLowerCase().indexOf(ql) === 0 ? 0 : 2, s })),
    COMMANDS: [["Go to Home", "home start"], ["Open settings", "settings preferences options"], ["Rescan library", "rescan scan refresh"]],
  });
  vm.runInContext(block("const srRank = ", "\n"), ctx);
  vm.runInContext(block("function typoEdits(", "const srMatch"), ctx);
  vm.runInContext(`var srSets = (ql, n) => ${JSON.stringify(SETTINGS)}.map((x) => [x, srFuzzy(x[0] + " " + x[2] + " " + x[1], ql)]).filter(([, f]) => f !== null).sort((a, b) => a[1] - b[1]).slice(0, n).map(([x]) => x);`, ctx);
  vm.runInContext(block("/* @srtouch:start */", "/* @srtouch:end */"), ctx);
  ctx.q = q; ctx.scope = scope;
  return vm.runInContext("srModel(q, scope)", ctx);
}
const name = (y) => y && (y.s?.title || y.x?.t || y.x?.[0] || y.c?.[0]);

test("your own show is the top hit", () => {
  const m = model("frieren", { shows: [{ title: "Frieren: Beyond Journey's End" }], al: [{ id: 1, t: "Frieren: Beyond Journey's End Season 2" }] });
  assert.equal(m.top.k, "lib");
  assert.deepEqual([...m.groups.map((g) => g.k)], ["al"]);
});

test("a typo-only command doesn't show once something really matched", () => {
  const m = model("frieren", { shows: [{ title: "Frieren" }] });
  assert.ok(!m.groups.some((g) => g.k === "cmd"), "'frieren' found 'preferences'");
  assert.equal(m.n.set, 0);
});

test("an exact command beats AniList's guesses, which fold into one row", () => {
  const m = model("rescan", { al: [{ id: 1, t: "Flame of Recca" }, { id: 2, t: "Rascal Does Not Dream" }] });
  assert.equal(m.top.k, "cmd");
  assert.equal(name(m.top), "Rescan library");
  const al = m.groups.find((g) => g.k === "al");
  assert.equal(al.more, 2);
  assert.deepEqual([...m.groups.find((g) => g.k === "set").items.map(name)], ["Rescan", "Library folder"]);
});

test("same match on AniList: the better-known show first", () => {
  const al = [{ id: 1, t: "One: Kagayaku Kisetsu e", averageScore: 46 }, { id: 2, t: "001", averageScore: 31 }, { id: 3, t: "ONE PIECE", averageScore: 87, popularity: 600000 }, { id: 4, t: "One-Punch Man Season 2", averageScore: 74, popularity: 400000 }];
  const m = model("one", { al });
  assert.equal(name(m.top), "ONE PIECE");
  assert.deepEqual([...m.groups[0].items.map(name)], ["One-Punch Man Season 2", "One: Kagayaku Kisetsu e", "001"]);
  assert.equal(m.groups[0].more, undefined, "AniList is the top hit: its rows stay");
});

test("a scope shows one kind and keeps every count", () => {
  const m = model("rescan", { al: [{ id: 1, t: "Flame of Recca" }], scope: "al" });
  assert.equal(m.top.k, "al");
  assert.equal(m.groups.length, 0);
  assert.deepEqual({ ...m.n }, { lib: 0, al: 1, set: 3 });
});

test("a setting found by its own words is the top hit", () => {
  const m = model("subtitle", { al: [{ id: 1, t: "Prefectural Earth Defense Force" }] });
  assert.equal(name(m.top), "Remember audio and subtitles");
  assert.equal(m.groups.find((g) => g.k === "al").more, 1);
});

test("nothing found: no top hit", () => {
  const m = model("xqzvw");
  assert.equal(m.top, null);
  assert.equal(m.groups.length, 0);
});
