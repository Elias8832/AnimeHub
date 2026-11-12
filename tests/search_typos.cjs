/* TODO 175: one typo in the search box still finds the show, setting or command. */
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
const shows = ["Naruto", "Frieren: Beyond Journey's End", "Attack on Titan", "One Piece", "Bleach"].map((title, id) => ({ id, title }));
const ctx = vm.createContext({ S: { data: { shows } }, SETTINGS_INDEX: [["Skip intros", "skipping", "opening op"], ["Subtitle language", "subs", "subs"]] });
vm.runInContext(block("const srRank = ", "\n") + "\n" + block("function typoEdits(", "/* Library hits") + block("const srLocal = ", "\n}).filter") + "\n}).filter(Boolean).sort((a, b) => a.r - b.r).slice(0, n);\nthis.api = { srLocal, srMatch, srSets };", ctx);
const { srLocal, srMatch, srSets } = ctx.api;
const titles = (q) => srLocal(q.toLowerCase(), 5).map((x) => x.s.title);

test("one slip still finds the show", () => {
  for (const [typed, want] of [["naurto", "Naruto"], ["narto", "Naruto"], ["freiren", "Frieren: Beyond Journey's End"], ["attak on titan", "Attack on Titan"], ["one pice", "One Piece"], ["bleahc", "Bleach"], ["freirem", "Frieren: Beyond Journey's End"]])
    assert.equal(titles(typed)[0], want, typed);
});

test("exact hits come before typo hits, and nonsense finds nothing", () => {
  assert.deepEqual(titles("titan"), ["Attack on Titan"]);
  assert.deepEqual(titles("xyzqw"), []);
  assert.deepEqual(titles("nar"), ["Naruto"]); // short words stay exact
  assert.deepEqual(titles("nra"), []);
});

test("settings and commands forgive a slip too", () => {
  assert.ok(srMatch("go to library library shows", "libary"));
  assert.deepEqual(srSets("subtitel", 5).map((x) => x[0]), ["Subtitle language"]);
});

test("typing and AniList's answers redraw only the results, not the whole bar", () => {
  const typing = block('q.addEventListener("input"', "\n});");
  assert.match(typing, /if \(open\) paintSr\(\); else paintNav\(\);/);
  const al = block("  alsearch: async () => {", "  catpick:");
  assert.doesNotMatch(al, /paintNav\(/);
  assert.match(block("function paintNav() {", "\n}"), /paintSr\(\);/);
});
