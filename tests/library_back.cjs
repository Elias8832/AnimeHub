/* Back from a show returns to the Library list you picked on the phone (its list menu is a layer). */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const cut = (a, b) => { const i = source.indexOf(a), j = source.indexOf(b, i); assert.ok(i >= 0 && j > i, a); return source.slice(i, j); };
const layers = cut("const OV = [];", "function ovDrop(");
const hist = cut("let navSeeded = false;", "/* ---------- boot ---------- */");

function page() {
  let popped = null;
  const stack = [{}];
  let at = 0;
  const history = {
    get state() { return stack[at]; },
    pushState(s) { stack.length = at + 1; stack.push(s); at++; },
    replaceState(s) { stack[at] = s; },
    back() { if (at > 0) { at--; popped({ state: stack[at] }); } },
  };
  const S = { view: "library", lib: { tab: "ALL", pl: null }, data: { playlists: [] } };
  const ctx = vm.createContext({
    S, history, location: { pathname: "/", search: "", href: "/" }, scrollY: 0, VIEWS: ["home", "library", "history", "stats", "settings", "week"],
    addEventListener: (name, fn) => { if (name === "popstate") popped = fn; },
    setTimeout: (fn) => fn(), $: () => ({ classList: { remove() {}, add() {} } }),
    render() {}, clearSearch() {}, saveLib() {}, scrollTo() {}, plCurrent: () => null, plSlide() {}, frKnown() {}, frLoad() {}, backLand() {}, unstyle() {},
    backs: new Map(), frOpenTok: 0, backSwiped: false,
  });
  vm.runInContext(`${layers}\n${hist}\nthis.api = { ovOpen, ovClosed, navKeep, pushNav };`, ctx);
  return { S, history, ...ctx.api };
}

test("a list picked in the phone's list menu is where back from a show lands", () => {
  const p = page();
  p.ovOpen("cm", () => {});                       // the list menu opens
  p.S.lib.tab = "PLANNING"; p.navKeep();          // Planning picked (A.tab)
  p.ovClosed("cm");                               // the menu closes: its entry comes off
  p.S.view = "show"; p.pushNav();                 // a show opens
  p.history.back();                               // back
  assert.equal(p.S.view, "library");
  assert.equal(p.S.lib.tab, "PLANNING");
});
