/* Search history (localStorage srRecent), and the PC list showing it. */
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

function context(stored = [], shows = []) {
  const ls = { srShows: stored };
  const ctx = vm.createContext({
    ls, S: { q: "" }, esc: (x) => String(x).replace(/"/g, "&quot;"), ico: (n) => `[${n}]`, alSearchable: () => false,
    lsGet: (k, d) => ls[k] ?? d, lsSet: (k, v) => { ls[k] = v; },
    showById: (id) => shows.find((x) => x.id === id) || null, spineOf: () => null, statusLabel: (x) => x, epTotal: () => 12,
    poster: (t, u) => `<img alt="${t}" src="${u}">`,
  });
  vm.runInContext(block("/* @srhist:start */", "/* @srhist:end */"), ctx);
  vm.runInContext(`var COMMANDS = [["Go to Home", "home", null, "1"]];
    this.list = () => { ${block("function srHTML() {", "/* The preview pane")} return srHTML(); };`, ctx);
  return ctx;
}
const keys = (c) => [...c.ls.srShows].map((x) => x.k);

test("a show goes first, once, and only six are kept", () => {
  const c = context(["s:1", "s:2", "a:3", "s:4", "s:5", "s:6"].map((k) => ({ k })));
  c.srAdd({ k: "a:3", t: "Three" });
  assert.deepEqual(keys(c), ["a:3", "s:1", "s:2", "s:4", "s:5", "s:6"]);
  c.srAdd({ k: "s:7" });
  assert.equal(c.ls.srShows.length, 6);
  assert.equal(keys(c)[0], "s:7");
});

test("one show can be removed", () => {
  const c = context([{ k: "s:1" }, { k: "a:2", t: "Two" }]);
  c.srForget("s:1");
  assert.deepEqual(keys(c), ["a:2"]);
});

test("library shows read the library; one that left is hidden, an AniList one stays", () => {
  const c = context([{ k: "s:gone" }, { k: "s:1" }, { k: "a:2", t: "Two", p: "two.jpg" }], [{ id: "1", title: "Frieren", poster: "f.jpg" }]);
  assert.deepEqual([...c.srShows()].map((x) => x.t), ["Frieren", "Two"]);
  assert.equal(c.ls.srShows.length, 3, "hidden, not forgotten: it comes back with its files");
});

test("the PC list starts with the shows, as rows the arrows reach", () => {
  const html = context([{ k: "s:1" }, { k: "a:2", t: 'Say "hi"', p: "two.jpg" }], [{ id: "1", title: "Frieren", poster: "f.jpg" }]).list();
  assert.ok(html.indexOf(">recent<") < html.indexOf(">commands<"));
  assert.match(html, /class="mi"[^>]*data-a="srshow" data-v="s:1"/);
  assert.match(html, /<b>Frieren<\/b><small>12 episodes<\/small>/);
  assert.match(html, /<small>on anilist<\/small>/);
  assert.match(html, /data-a="srforget" data-v="a:2"/);
  assert.match(html, /data-a="srclr"/);
});

test("no history, no section", () => {
  assert.doesNotMatch(context([]).list(), /recent/);
});
