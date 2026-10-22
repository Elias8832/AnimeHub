/* The UI-zoom preference is a client-side setting (localStorage hubZoom,
   50–200 % in steps of 10) applied via CSS zoom on <html>, set from the
   Settings row only. Shell mode suppresses browser zoom and keeps
   ctrl+scroll / ctrl± / ctrl+0 dead — so the stepping function is the
   behavior that matters. */
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
  const ctx = vm.createContext({});
  vm.runInContext(block("/* @pure:start */", "/* @pure:end */"), ctx);
  return ctx;
}

test("zoom steps move by 10 and clamp to the 50–200 range", () => {
  const c = context();
  assert.equal(c.zoomStep(100, 1), 110);
  assert.equal(c.zoomStep(100, -1), 90);
  assert.equal(c.zoomStep(100, -3), 70, "multi-step from ctrl+scroll bursts");
  assert.equal(c.zoomStep(200, 1), 200, "max clamp");
  assert.equal(c.zoomStep(50, -1), 50, "min clamp");
  assert.equal(c.zoomStep(110, -1), 100, "steps are symmetric");
});

test("shell mode suppresses browser zoom but never drives the setting", () => {
  /* Elias: no zoom shortcuts — the Settings row is the only control. The
     shell handlers keep ctrl+wheel / ctrl± dead (no browser zoom) without
     touching the preference. */
  assert.match(source, /hubZoom/, "preference key exists");
  assert.match(source, /applyZoom\(\); \/\/ before first paint/, "applied before first paint");
  assert.ok(!/wheelAcc/.test(source), "no ctrl+scroll driving the setting");
  assert.ok(!/zoomreset/.test(source), "no reset action");
  assert.ok(!/zoomSet\(zoomStep\(zoomGet\(\), e\.key/.test(source), "no ctrl± driving the setting");
  // suppression stays: ctrl+wheel and the zoom keys are prevented in shell mode
  assert.match(source, /wheel", \(e\) => \{ if \(e\.ctrlKey\) e\.preventDefault\(\); \}/);
  assert.match(source, /"\+-=0"\.includes\(e\.key\)/, "zoom keys swallowed, dead");
  // the Settings row is compact (no Reset button inside it)
  const row = source.match(/<span class="zrow">.*?<\/span>/s);
  assert.ok(row, "zrow markup exists");
  assert.ok(!row[0].includes("zoomreset"), "no reset in the row");
});
