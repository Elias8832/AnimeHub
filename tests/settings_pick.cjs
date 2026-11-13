/* Settings on a phone (Decision 94): pick() gives a switch for On/Off,
   buttons for two or three short choices, and the value with a list behind
   it for the rest. Off a phone it keeps the PC's ‹ value › (Decision 73). */
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
const ctx = vm.createContext({ ico: (n) => `<svg ${n}>` });
vm.runInContext(block("const esc =", "\n") + "\n" + block("function pick(", "\nconst pkdo") + "\nthis.setTouch = (v) => { pickTouch = v; };", ctx);

const ONOFF = [["on", "On"], ["off", "Off"]];
test("a phone gets a switch for On/Off, flipping to the other value", () => {
  ctx.setTouch(true);
  const h = ctx.pick("Hide spoilers", "advset", "hide_spoilers", ONOFF, "on");
  assert.match(h, /class="pk pksw"><button class="switch" role="switch" aria-checked="true"/);
  assert.match(h, /data-a="advset" data-k="hide_spoilers" data-v="off"/);
  assert.ok(!h.includes("<select"));
});
test("two or three short choices are buttons, the current one lit", () => {
  ctx.setTouch(true);
  const h = ctx.pick("Skip openings", "spref", "skip_op", [["off", "Off"], ["prompt", "Ask"], ["auto", "Auto"]], "prompt");
  assert.match(h, /pkseg/);
  assert.match(h, /<button class="on" aria-pressed="true" data-a="spref" data-k="skip_op" data-v="prompt">Ask<\/button>/);
  assert.equal((h.match(/<button/g) || []).length, 3);
});
test("longer lists keep the select, marked for the sheet", () => {
  ctx.setTouch(true);
  const h = ctx.pick("Starting volume", "spref", "mpv_start_volume", [["own", "My config"], ["last", "Where I left it"], ["25", "25"], ["50", "50"]], "last");
  assert.match(h, /class="pk pklist"/);
  assert.match(h, /<option value="last" selected>Where I left it<\/option>/);
});
test("a disabled setting stays disabled in every shape", () => {
  ctx.setTouch(true);
  assert.match(ctx.pick("Send", "advset", "x", ONOFF, "off", { off: true }), /class="pk off pksw"><button[^>]* disabled>/);
});
test("off a phone nothing changes", () => {
  ctx.setTouch(false);
  const h = ctx.pick("Hide spoilers", "advset", "hide_spoilers", ONOFF, "on");
  assert.match(h, /^<span class="pk"><button class="pka"/);
  assert.ok(!h.includes("pklist") && !h.includes("switch"));
});
