/* The connect dialog's episode line (TODO 191): only what the hub sent is
   drawn — marked, watched here, seen on the account with no file here. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const from = source.indexOf("@rec:start"), to = source.indexOf("/* @rec:end */");
assert.ok(from >= 0 && to > from, "Cannot find the @rec block");
const ctx = vm.createContext({});
vm.runInContext(source.slice(source.indexOf("*/", from) + 2, to) + ";this.recRange=recRange;this.recTicks=recTicks;this.recGap=recGap;", ctx);

test("episode runs read as ranges", () => {
  assert.equal(ctx.recRange([7, 6]), "E6–E7");
  assert.equal(ctx.recRange([1, 3, 4, 5, 9]), "E1, E3–E5, E9");
  assert.equal(ctx.recRange([1]), "E1");
});

test("Blue Box: files to E7, the list at E25", () => {
  const t = ctx.recTicks({ remote: 25, total: 25, numbers: [6, 7], watched: [1, 2, 3, 4, 5] });
  assert.equal(t.length, 25);
  assert.deepEqual([...t.slice(0, 8)], ["w", "w", "w", "w", "w", "n", "n", "x"]);
  assert.equal(t[24], "x");
});

test("never draws past what is known", () => {
  const t = ctx.recTicks({ remote: 6, total: 12, numbers: [1, 2, 3, 4, 5, 6], watched: [] });
  assert.deepEqual([...t.slice(5, 8)], ["n", "", ""]);
});

test("a long show folds, and one marked episode still shows", () => {
  const t = ctx.recTicks({ remote: 600, total: 1100, numbers: [600], watched: [] });
  assert.equal(t.length, 60);
  assert.ok(t.includes("n"));
  assert.equal(t[59], "");
});

test("the outline names the episodes with no file here", () => {
  const m = { remote: 25, numbers: [6, 7], watched: [1, 2, 3, 4, 5] };
  assert.equal(ctx.recRange(ctx.recGap(m)), "E8–E25");
  assert.equal(ctx.recGap({ remote: 10, numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], watched: [] }).length, 0);
});
