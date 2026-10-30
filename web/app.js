"use strict";
/* Anime Hub UI — round 4 design wired to the real hub API.
   Views are string builders (Home(), Lib(), Det(), …) painted into #app; all
   interaction goes through one delegated handler keyed on data-a (table A). */
const $ = (q) => document.querySelector(q);
const $$ = (q) => [...document.querySelectorAll(q)];
const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };

const S = {
  data: null, view: "home", showId: null, seasonIdx: 0, showBack: "home", cat: null,
  q: "", menu: null, alres: null, alq: null, alBusy: false,
  ani: null, settings: null, alData: null, modal: null, entry: null,
  lib: (({ year, score, genres, air, ...l }) => ({ tab: null, src: "all", fmt: "all", sort: "updated", group: "all", ...l,
         air: air === "FINISHED_AIRING" ? "FINISHED" : air || "all", genres: Array.isArray(genres) ? genres : [],
         year: Array.isArray(year) ? year : "all", score: Array.isArray(score) ? score : "all" }))(lsGet("hubLib", {})),
  histRange: lsGet("histRange", "7d"), histMax: 120,
  match: {}, tour: { step: 0, choice: "connect" }, rec: null, navIn: true, opener: null,
  slink: {}, dl: [], loadingEp: null, frDown: {},
};

/* @pure:start */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Inline SVG icons: unicode glyphs (▶ ⋮ ✓ ♥) render as emoji or fall back unpredictably on Linux. [paths, filled?] */
const ICONS = {
  play: ['<path d="M7 4.8v14.4a.8.8 0 0 0 1.2.7l12-7.2a.8.8 0 0 0 0-1.4l-12-7.2A.8.8 0 0 0 7 4.8z"/>', 1],
  dots: ['<circle cx="12" cy="5.5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="18.5" r="1.7"/>', 1],
  star: ['<path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>', 1],
  check: ['<path d="m5 12.5 4.5 4.5L19 7.5"/>'],
  checkc: ['<circle cx="12" cy="12" r="8.5"/><path d="m8.3 12.3 2.6 2.6 4.8-5.2"/>'],
  heart: ['<path d="M12 20.5s-7.5-4.6-9.2-9.3C1.6 7.7 3.7 4.5 7 4.5c2 0 3.7 1.1 5 3 1.3-1.9 3-3 5-3 3.3 0 5.4 3.2 4.2 6.7-1.7 4.7-9.2 9.3-9.2 9.3z"/>'],
  back: ['<path d="M19 12H5m6-6-6 6 6 6"/>'],
  arrowr: ['<path d="M5 12h14m-6-6 6 6-6 6"/>'],
  chevl: ['<path d="m15 6-6 6 6 6"/>'],
  edit: ['<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'],
  chevr: ['<path d="m9 6 6 6-6 6"/>'],
  ext: ['<path d="M7 17 17 7m0 0H9m8 0v8"/>'],
  download: ['<path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14"/>'],
  close: ['<path d="M6 6l12 12M18 6 6 18"/>'],
  plus: ['<path d="M12 5v14M5 12h14"/>'],
  minus: ['<path d="M5 12h14"/>'],
  clock: ['<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'],
  tv: ['<rect x="3" y="5.5" width="18" height="12" rx="2.5"/><path d="M8 21h8"/>'],
  search: ['<circle cx="11" cy="11" r="6.5"/><path d="m20 20-3.6-3.6"/>'],
  refresh: ['<path d="M20 11a8 8 0 0 0-14.3-4.6L4 8.5M4 4v4.5h4.5M4 13a8 8 0 0 0 14.3 4.6L20 15.5M20 20v-4.5h-4.5"/>'],
  undo: ['<path d="M4 12a8 8 0 1 0 2.5-5.8L4 8.5M4 4v4.5h4.5"/>'],
  folder: ['<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2.2 2.2h7a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'],
  up: ['<path d="M12 19V5m-6 6 6-6 6 6"/>'],
  shield: ['<path d="M12 3.5 5 6.3v5.4c0 4.3 3 7.6 7 8.8 4-1.2 7-4.5 7-8.8V6.3z"/>'],
  pause: ['<rect x="6.5" y="5" width="3.8" height="14" rx="1"/><rect x="13.7" y="5" width="3.8" height="14" rx="1"/>', 1],
  rew: ['<path d="M4 12a8 8 0 1 0 2.5-5.8L4 8.5M4 4v4.5h4.5"/><text x="12" y="15.3" font-size="7.5" font-weight="700" text-anchor="middle" fill="currentColor" stroke="none">10</text>'],
  fwd: ['<path d="M20 12a8 8 0 1 1-2.5-5.8L20 8.5M20 4v4.5h-4.5"/><text x="12" y="15.3" font-size="7.5" font-weight="700" text-anchor="middle" fill="currentColor" stroke="none">10</text>'],
  next: ['<path d="M6 5.5v13l9.5-6.5z" fill="currentColor"/><path d="M18 5.5v13"/>'],
  stop: ['<rect x="6" y="6" width="12" height="12" rx="2"/>', 1],
  vol: ['<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>'],
  chevd: ['<path d="m6 9 6 6 6-6"/>'],
  home: ['<path d="M4 11 12 4l8 7v8.5a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z"/>'],
  grid: ['<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>'],
  sliders: ['<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>'],
  cal: ['<rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M4 10h16M8.5 3.5v4m7-4v4"/>'],
};
const ico = (n, c = "") => `<svg class="ico${ICONS[n][1] ? " ico-f" : ""}${c ? " " + c : ""}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n][0]}</svg>`;
const FMT = { TV: "TV", TV_SHORT: "TV short", MOVIE: "Movie", OVA: "OVA", ONA: "ONA", SPECIAL: "Special", MUSIC: "Music" };
const fmtName = (f) => FMT[f] || f;
/* Escape text and wrap the first case-insensitive match of q in <mark>. */
const hl = (t, q) => {
  const s = String(t ?? ""), i = q ? s.toLowerCase().indexOf(q.toLowerCase()) : -1;
  return i < 0 ? esc(s) : esc(s.slice(0, i)) + "<mark>" + esc(s.slice(i, i + q.length)) + "</mark>" + esc(s.slice(i + q.length));
};

/* Stable per-title hue: the colour of a poster/banner placeholder, so a show
   without artwork still has a recognisable tile. */
function hueOf(str) {
  let h = 7;
  for (const c of String(str || "")) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0) % 360;
}
const cssUrl = (u) => `url("${String(u).replace(/[\s"'()\\]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase().padStart(2, "0"))}")`;
const bgStyle = (url, hue) => `--h:${hue};` + (url ? `background-image:${cssUrl(url)}` : "");
/* Posters shown before are drawn ready (TODO 61): their fade-in plays once,
   not again on every page that shows them. */
const imgSeen = new Set();
const poster = (title, url, o = {}) => {
  const seen = !!url && imgSeen.has(url);
  return `<div class="poster${url ? " hasimg" : ""}${seen ? " ready" : ""}${o.c ? " " + o.c : ""}${o.wait ? " wait" : ""}" style="--h:${hueOf(title)}">${url ? `<img loading="lazy"${seen ? ' class="ready"' : ' decoding="async"'} alt="" src="${esc(url)}">` : ""}<span>${esc(title)}</span></div>`;
};

function fmtTime(s) {
  if (!s) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}
/* Time remaining in an episode, e.g. "24 min" or "1h 12m". */
function fmtLeft(duration, position) {
  const rem = Math.max(0, (duration || 0) - (position || 0));
  return rem >= 3600 ? `${Math.floor(rem / 3600)}h ${Math.round((rem % 3600) / 60)}m` : `${Math.max(1, Math.round(rem / 60))} min`;
}
const parseStamp = (iso) => new Date(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");

function showShortLabel(sea) {
  return sea.label.startsWith("Season") ? `S${sea.season}` : sea.label === "Specials" ? "SP" : sea.label;
}
function epCode(sea, ep) {
  if (sea.label === "Episodes") return `E${ep.number}`; // an online release's own page
  return sea.label.startsWith("Season") ? `S${sea.season}E${ep.number}` : `${showShortLabel(sea)} · E${ep.number}`;
}
function epTitle(show, sea, ep) {
  const st = sea?.titles;
  if (st) {
    const t = Array.isArray(st) ? st[ep.number - 1] : st[String(ep.number)];
    if (t) return t;
  }
  const meta = show.meta?.episodes_meta;
  if (meta && sea && meta.length === sea.episodes.length) return meta[ep.number - 1] || null;
  return null;
}
/* Mirrors the hub's resume rule (player.resume_args): a position past 1 % resumes
   — on a watched episode too while it's under 92 % (a rewatch you stopped). */
const resumable = (ep) => !!ep.duration && ep.position / ep.duration > 0.01 && (!ep.watched || ep.position / ep.duration < 0.92);

/* What a show's big button plays. Seasons come in the hub's order
   (season_rank): main seasons first, extras last. */
function pickTarget(s) {
  const ord = s.seasons.map((sea) => ({ sea }));
  let resume = null;
  /* A stopped rewatch (watched, under 92 %) leads only once nothing is left unwatched. */
  const allW = s.seasons.every((sea) => sea.episodes.every((e) => e.watched));
  for (const { sea } of ord) for (const ep of sea.episodes) {
    if (resumable(ep) && (!ep.watched || allW) && (!resume || (ep.last_played || "") > (resume.ep.last_played || ""))) resume = { sea, ep, kind: "resume" };
  }
  if (resume) return resume;
  for (const { sea } of ord) { const ep = sea.episodes.find((e) => !e.watched); if (ep) return { sea, ep, kind: "play" }; }
  const first = ord.find((x) => x.sea.episodes.length);
  return first ? { sea: first.sea, ep: first.sea.episodes[0], kind: "rewatch" } : null;
}
const KIND_WORD = { resume: "Resume", play: "Play", rewatch: "Rewatch" };
const epTotal = (s) => s.seasons.reduce((a, x) => a + x.episodes.length, 0);
const epWatched = (s) => s.seasons.reduce((a, x) => a + x.episodes.filter((e) => e.watched).length, 0);
/* Disk shows, then releases' online episodes ("aid:<id>", same shape). */
const allShows = () => (S.data ? [...S.data.shows, ...(S.data.streams || [])] : []);
const showById = (id) => allShows().find((x) => x.id === id) || null;
const isStream = (s) => s?.kind === "stream";
const streamEp = (ep) => ep.id.startsWith("stream:");

function findEpContext(id) {
  for (const s of allShows()) {
    for (const sea of s.seasons) {
      const ep = sea.episodes.find((e) => e.id === id);
      if (ep) return { s, sea, ep };
    }
  }
  return null;
}

/* The hero follows the most recently touched show, period. Continue rows
   (position > 30 s) and history rows overlap — either may hold the true
   newest touch, so merge both and sort by last_played. */
function nextUpInfo() {
  const d = S.data;
  const newest = [...(d.continue || []), ...(d.history || [])]
    .filter((ep) => findEpContext(ep.id))
    .sort((a, b) => (b.last_played || "").localeCompare(a.last_played || ""))[0];
  if (!newest) return null;
  const ctx = findEpContext(newest.id);
  if (!ctx.ep.watched) return { ...ctx, label: "Continue watching", ep: ctx.ep };
  const after = ctx.sea.episodes.filter((e) => !e.watched && e.number > ctx.ep.number)[0];
  const anyUn = ctx.s.seasons.map((sea) => ({ sea, ep: sea.episodes.find((e) => !e.watched) })).filter((x) => x.ep)[0];
  if (after) return { s: ctx.s, sea: ctx.sea, ep: after, label: "Up next" };
  if (anyUn) return { s: ctx.s, sea: anyUn.sea, ep: anyUn.ep, label: "Up next" };
  return { ...ctx, label: resumable(ctx.ep) ? "Continue watching" : "Rewatch", ep: ctx.ep };
}
/* Watch facts on the model, in place (marked ids only, artwork untouched):
   the show page patches itself after a mark instead of rebuilding — see
   patchShow. `reset` also clears the resume position/last-played stamp. */
function applyWatch(shows, ids, watched, reset) {
  const set = new Set(ids);
  let n = 0;
  for (const s of shows) for (const sea of s.seasons) for (const ep of sea.episodes) {
    if (!set.has(ep.id)) continue;
    ep.watched = watched;
    if (reset) { ep.position = 0; ep.last_played = null; }
    n++;
  }
  return n;
}
/* UI zoom: a client-side, per-device preference (same class as the spine
   view state), 50–200 % in steps of 10. Applied on <html> via CSS zoom;
   in shell mode ctrl+scroll / ctrl± / ctrl+0 drive it. */
const ZOOM_MIN = 50, ZOOM_MAX = 200, ZOOM_STEP = 10;
function zoomStep(cur, dir) { return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, cur + dir * ZOOM_STEP)); }
/* A season tab's count (TODO 7): how many episodes the entry has — its
   AniList total, else the aired ones, else what the page lists — never
   what's left to watch. One-episode entries (movies) show none. */
function entryCount(x) { const n = x.episodes || x.aired || x.slots.length; return n > 1 ? n : 0; }
/* The accent from AniList's colour for a show (TODO 60): its hue at a fixed
   light, soft tone so every show reads well on dark; near-grey → none. */
function hexAccent(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return null;
  const n = parseInt(m[1], 16), r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
  if (!d || d / (1 - Math.abs(2 * l - 1)) < 0.12) return null;
  const h = (mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60;
  return `hsl(${Math.round(h)} 72% 70%)`;
}
/* @pure:end */

/* @lists:start */
const LISTS = [["CURRENT", "Watching"], ["REPEATING", "Rewatching"], ["PAUSED", "Paused"],
               ["DROPPED", "Dropped"], ["COMPLETED", "Completed"], ["PLANNING", "Planning"]];
const statusLabel = (st) => (LISTS.find(([v]) => v === st) || [0, st || "—"])[1];
/* AniList MediaStatus values; Hiatus/Cancelled stay reachable (Decision 13). */
const AIR = { RELEASING: "Airing", FINISHED: "Finished", NOT_YET_RELEASED: "Upcoming", HIATUS: "Hiatus", CANCELLED: "Cancelled" };
const CAP = { installed: "installed", partial: "partial", none: "no source", online: "online" };
const capLabel = (c) => (c === "none" && S.data?.stream_ready ? "not available" : CAP[c] || "");
const aniMode = () => S.data?.spine?.mode === "anilist";
const localMode = () => S.data?.spine?.mode === "local";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/* Appearance (Decision 38): device preferences in localStorage, put on
   <html> by index.html before first paint. The accent follows the show on
   screen unless one colour is picked. */
const LOOKS = { motion: ["full", "calm", "off"], art: ["full", "soft", "off"], bar: ["classic", "tabs", "line", "capsule", "island"] };
const ACCENTS = ["#f2a46f", "#b8d7c6", "#9ccbe8", "#e58ccf", "#e6c27c", "#c3a6f2"];
const lookGet = (k) => { const v = lsGet(k, LOOKS[k][0]); return LOOKS[k].includes(v) ? v : LOOKS[k][0]; };
const accentGet = () => { const v = lsGet("accent", "follow"); return ACCENTS.includes(v) ? v : "follow"; };
const grainGet = () => lsGet("grain", true) !== false;
function fmtIn(sec) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${Math.max(1, m)} min`;
}
/* Seconds until the next episode airs; `at` is absolute, older caches only carry in_seconds. */
const airsIn = (n, now = Date.now()) => !n ? null : n.at ? n.at - now / 1000 : n.in_seconds ?? null;
const airText = (n, now) => { const s = airsIn(n, now); return s == null ? "" : s > 0 ? `E${n.number} in ${fmtIn(s)}` : `E${n.number} is out`; };
/* @lists:end */

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  try { return await res.json(); } catch { throw new Error(`HTTP ${res.status} from ${path}`); }
}
/* The bottom popup. kind "busy" stays with a spinner until toastDone or the next popup. */
function toast(m, kind) {
  const t = $("#toast"), k = kind || (/could not|couldn't|failed|error|can't|cannot|not accessible|unreachable|went wrong/i.test(m) ? "err" : "ok");
  t.dataset.k = k;
  t.innerHTML = `<span class="ti">${k === "busy" ? '<i class="tsp"></i>' : ico(k === "err" ? "close" : "check")}</span><span class="tm"></span>`;
  t.querySelector(".tm").textContent = m;
  t.classList.add("on");
  clearTimeout(toast.h);
  if (k !== "busy") toast.h = setTimeout(() => t.classList.remove("on"), 2800);
}
function toastDone() { const t = $("#toast"); if (t.dataset.k === "busy") t.classList.remove("on"); }
/* An episode on its way: from the click (or autoplay) until mpv answers. */
let loadShown = null;
function loadToast(now) {
  const id = now.playing && now.loading ? now.episode_id : null;
  if (id && id !== loadShown) { loadShown = id; toast(`Loading ${now.show} · ${now.label}`, "busy"); }
  else if (!id && loadShown && !S.playBusy) { loadShown = null; toastDone(); }
}
const reduceMotion = () => document.documentElement.dataset.motion === "off" || matchMedia("(prefers-reduced-motion:reduce)").matches;

/* ---------- home ---------- */
/* @home:start */
function isPlayingShow(id) {
  const n = S.data.now, c = n && findEpContext(n.episode_id);
  return !!c && c.s.id === id;
}
/* Home (Decision 38): an overview, one section per row with room —
   headline + a one-line summary, the up-next show as one big card (its art
   inside), continue watching, this week's airings, ready to start. */
const kbH = (k) => `<kbd class="kb">${k}</kbd>`;
const homeThumb = (sea, ep) => { const t = sea?.thumbs; return !t ? null : Array.isArray(t) ? t[ep.number - 1] || null : t[String(ep.number)] || null; };
/* One tick per episode of the season: seen · partly seen · on disk · online. */
function seasonTicks(sea) {
  const eps = (sea?.episodes || []).slice(0, 60);
  return `<div class="ticks">${eps.map((x, i) => { const p = x.duration ? x.position / x.duration : 0;
    return `<i style="--i:${i};--pp:${Math.round(p * 100)}%" class="${x.watched ? "s" : p > 0.01 ? "p" : streamEp(x) ? "o" : "d"}"></i>`; }).join("")}</div>`;
}
function dayWord(h) { return h < 5 ? "Tonight" : h < 12 ? "This morning" : h < 18 ? "This afternoon" : "Tonight"; }
function heroHTML(up) {
  const s = up.s, ep = up.ep, sea = up.sea, art = homeThumb(sea, ep) && !s.banner ? homeThumb(sea, ep) : s.banner || s.poster;
  const pct = ep.duration ? Math.min(100, (ep.position / ep.duration) * 100) : 0;
  const len = ep.duration || ep.length, left = pct >= 1 && !ep.watched ? `${fmtLeft(ep.duration, ep.position)} left` : len ? `${Math.round(len / 60)} min` : "";
  const src = streamEp(ep) ? "online" : "on disk";
  const seen = (sea?.episodes || []).filter((x) => x.watched).length, tot = sea?.episodes.length || 0;
  const verb = up.label === "Rewatch" ? "Rewatch" : resumable(ep) ? "Resume" : "Play";
  return `<div class="upn" data-a="open" data-v="${esc(s.id)}" style="--h:${hueOf(s.title)}">${art ? `<i class="upa" style="background-image:${esc(cssUrl(art))}"></i>` : ""}<div class="upc">
    <div class="eye"><i class="mdot"></i>${esc(up.label.toLowerCase())}${sea?.label && s.seasons.length > 1 ? " · " + esc(sea.label.toLowerCase()) : ""}${isPlayingShow(s.id) ? ' · <span class="ok">playing now</span>' : ""}</div>
    <h1>${esc(s.title)}</h1>
    <div class="sub">${esc(epCode(sea, ep))} · ${esc(epTitle(s, sea, ep) || "Episode " + ep.number)}</div>
    <div class="facts">${[left, src].filter(Boolean).map(esc).join('<i class="sep"></i>')}</div>
    ${tot > 1 ? `<div class="tbar">${seasonTicks(sea)}<span>${seen}/${tot}</span></div>` : ""}
    <div class="cta"><button class="btn pri big" data-a="play" data-v="${esc(ep.id)}">${ico("play")}<span>${verb}</span>${kbH("↵")}</button><button class="btn big" data-a="open" data-v="${esc(s.id)}">Open show${kbH("O")}</button></div>
  </div></div>`;
}
/* One tile per show you actually played, newest first, each pointing at the
   next relevant episode. The show the hero already features is skipped. */
function keepWatching(skipId) {
  const seen = new Set(skipId ? [skipId] : []), out = [];
  const hist = (S.data.history || []).map((ep) => ({ ep, ctx: findEpContext(ep.id) })).filter((x) => x.ctx)
    .sort((a, b) => (b.ep.last_played || "").localeCompare(a.ep.last_played || ""));
  for (const x of hist) {
    if (seen.has(x.ctx.s.id)) continue;
    seen.add(x.ctx.s.id);
    const t = pickTarget(x.ctx.s);
    if (t && t.kind !== "rewatch") out.push({ s: x.ctx.s, t });
    if (out.length >= 8) break;
  }
  return out;
}
function contCard({ s, t }, k) {
  const ep = t.ep, p = ep.duration ? Math.min(100, (ep.position / ep.duration) * 100) : 0;
  const art = homeThumb(t.sea, ep) || s.banner || s.poster;
  const meta = [epTitle(s, t.sea, ep), p > 1 && !ep.watched ? `${fmtLeft(ep.duration, ep.position)} left` : "", streamEp(ep) ? "online" : "disk"].filter(Boolean).join(" · ");
  return `<div class="hc${isPlayingShow(s.id) ? " now" : ""}" style="--i:${k}" data-a="open" data-v="${esc(s.id)}" tabindex="0">
    <div class="epa">${art ? `<span class="epimg" style="background-image:${esc(cssUrl(art))};background-position:${homeThumb(t.sea, ep) ? "50% 50%" : "50% 30%"}"></span>` : ""}
    <span class="enum">${esc(epCode(t.sea, ep))}</span>${p > 1 && !ep.watched ? `<i class="eprog"><i style="width:${p}%"></i></i>` : ""}
    <span class="eplay" data-a="play" data-v="${esc(ep.id)}" title="${esc(KIND_WORD[t.kind])}">${ico("play")}</span></div>
    <h3>${esc(s.title)}</h3><small>${esc(meta)}</small></div>`;
}
/* This week: the next airings on your lists, in order, as cards; a Mon–Sun ruler marks the days. */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function weekHTML(list, now = Date.now()) {
  const at = (e) => e.next_episode?.at ? e.next_episode.at * 1000 : now + (airsIn(e.next_episode, now) || 0) * 1000;
  const soon = list.filter((e) => at(e) > now - 3600e3).slice(0, 3); // the next three, however far off (Blue Box's next was 8 days away)
  if (!soon.length) return "";
  const today = new Date(now), dow = (today.getDay() + 6) % 7, monday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - dow).getTime();
  const has = new Set(list.map((e) => Math.floor((at(e) - monday) / 864e5)).filter((d) => d >= 0 && d < 7));
  const when = (t) => { const d = new Date(t), same = d.toDateString() === today.toDateString(), tm = d.toTimeString().slice(0, 5);
    return [same ? "Today" : DAYS[(d.getDay() + 6) % 7] + (t - now > 6 * 864e5 ? " " + d.getDate() : ""), tm]; };
  return `<section class="hsec"><div class="sh"><h2>${at(soon[0]) < now + 7 * 864e5 ? "This week" : "Coming up"}</h2><span class="mut">airing on your lists</span><button class="link-btn" data-a="go" data-v="week">Calendar${ico("arrowr")}</button><span class="ruler">${DAYS.map((d, i) => `<span class="${i === dow ? "today" : ""}">${i === dow ? "today" : d}<i class="${has.has(i) ? "has" : ""}"></i></span>`).join("")}</span></div>
    <div class="agenda">${soon.map((e) => { const t = at(e), [d, tm] = when(t), sec = (t - now) / 1000;
      return `<button class="ag${d === "Today" ? " today" : ""}" data-a="entry" data-v="${esc(e.id)}">${e.poster ? `<i class="agb" style="background-image:${esc(cssUrl(e.poster))}"></i><i class="agp" style="background-image:${esc(cssUrl(e.poster))}"></i>` : ""}
        <span class="agt"><span class="when">${d} <small>${tm}</small></span><b>${esc(e.title)}</b><span class="m">episode ${esc(e.next_episode?.number ?? "")}</span></span><span class="cdn">${sec > 0 ? "in " + fmtIn(sec) : "out now"}</span></button>`; }).join("")}</div></section>`;
}
function Home() {
  if (isPhone()) return HomePhone();
  const d = S.data, up = nextUpInfo();
  const L = keepWatching(up && up.s.id);
  const touched = new Set((d.history || []).map((e) => e.show_id));
  const fresh = d.shows.filter((s) => !touched.has(s.id) && s.poster && epTotal(s) > 0).slice(0, 7);
  const airing = aniMode() ? d.spine.entries.filter((e) => e.airing === "RELEASING" && e.next_episode && inProg(e.status))
    .sort((a, b) => (airsIn(a.next_episode) ?? Infinity) - (airsIn(b.next_episode) ?? Infinity)) : [];
  const next = airing[0], inprog = L.length + (up ? 1 : 0);
  const sum = [inprog ? `<span><b>${inprog}</b> in progress</span>` : "", next ? `<span><b>${esc(next.title)}</b> ${(airsIn(next.next_episode) ?? 0) > 0 ? "airs in " + esc(fmtIn(airsIn(next.next_episode))) : "is out"}</span>` : "",
    fresh.length ? `<span><b>${fresh.length}</b> ready to start</span>` : ""].filter(Boolean).join('<i class="sep"></i>');
  let body = "";
  if (isTabletP()) body += homePair(L, airing);
  else {
    if (L.length) body += `<section class="hsec"><div class="sh"><h2>Continue watching</h2><span class="mut">${L.length}</span><span class="fill"></span><button class="arr" data-a="strip" data-d="-1" aria-label="Scroll left" disabled>${ico("chevl")}</button><button class="arr" data-a="strip" data-d="1" aria-label="Scroll right">${ico("chevr")}</button></div><div class="strip hstrip">${L.map(contCard).join("")}</div></section>`;
    body += weekHTML(airing);
  }
  const recs = recsFor();
  if (recs.length) body += `<section class="hsec"><div class="sh"><h2>Recommended for you</h2><span class="mut">from AniList, based on what you rated highly</span><span class="fill"></span><button class="arr" data-a="strip" data-d="-1" aria-label="Scroll left" disabled>${ico("chevl")}</button><button class="arr" data-a="strip" data-d="1" aria-label="Scroll right">${ico("chevr")}</button></div><div class="strip hstrip recs">${recs.map(recCard).join("")}</div></section>`;
  else if (fresh.length) body += `<section class="hsec"><div class="sh"><h2>Ready to start</h2><span class="mut">on disk, not started</span><span class="fill"></span><button class="link-btn" data-a="go" data-v="library">All shows${ico("arrowr")}</button></div><div class="rdy">${fresh.map((s) =>
    `<button class="rc" data-a="open" data-v="${esc(s.id)}"><span class="pw">${poster(s.title, s.poster)}</span><b>${esc(s.title)}</b><small>${epTotal(s)} episode${epTotal(s) === 1 ? "" : "s"} · disk</small></button>`).join("")}</div></section>`;
  if (!up && !L.length) {
    body = `<div class="emptyx"><div class="eico">${ico("tv")}</div>${d.shows.length
      ? `<b>Nothing watched yet</b><span>Your library is waiting.</span><button class="btn pri" data-a="go" data-v="library">Browse library</button>`
      : d.stream_ready ? `<b>Nothing watched yet</b><span>Search for a show to start watching — or add your anime folder in Settings → Library.</span>`
      : `<b>Your library is empty</b><span>Choose your anime folder in Settings → Library.</span>`}</div>` + body;
  }
  return `<div class="home2"><div class="hello"><h1 class="hd">${dayWord(new Date().getHours())}</h1>${sum ? `<div class="sum">${sum}</div>` : ""}</div>${up ? heroHTML(up) : ""}${body}</div>`;
}
/* TODO 65: AniList's recommendations of your best-rated shows, minus what's on
   your lists (the hub ranks them); a poster opens its page, + Planning adds it. */
function recsFor() {
  if (!aniMode()) return [];
  if (!S.recs || Date.now() - S.recs.at > 10 * 60e3) loadRecs();
  const listed = new Set(S.data.spine.entries.filter((e) => e.status).map((e) => e.id));
  return (S.recs?.items || []).filter((r) => !listed.has(r.id) && !S.recsAdded?.has(r.id)).slice(0, 30);
}
function recCard(r) {
  const disk = (S.data.shows || []).some((s) => s.meta?.anilist_id === r.id);
  const facts = [FMT[r.format] || r.format, r.year].filter(Boolean).join(" · ");
  return `<div class="rec"><button class="rcv" data-a="recopen" data-v="${r.id}" aria-label="${esc(r.title)}"><span class="pw">${poster(r.title, r.poster)}</span>${disk ? '<span class="tg src">on disk</span>' : ""}</button>
    <button class="radd" data-a="recadd" data-v="${r.id}" title="Add to Planning">${ico("plus")}Planning</button>
    <b>${esc(r.title)}</b><small>${r.because ? `because you liked <em>${esc(r.because.title)}</em>` : esc(facts)}</small></div>`;
}
let recsBusy = false;
function loadRecs(tries = 0) {
  if (recsBusy) return;
  recsBusy = true;
  api("/api/recommendations").then((r) => {
    recsBusy = false;
    S.recs = { at: r.state === "loading" ? 0 : Date.now(), items: r.items || [] };
    if (r.state === "loading" && tries < 6) setTimeout(() => loadRecs(tries + 1), 3000);
    if (S.view === "home" && r.items?.length) render();
  }).catch(() => { recsBusy = false; S.recs = { at: Date.now(), items: S.recs?.items || [] }; });
}
/* @home:end */

/* ---------- library ---------- */
/* @lib:start */
/* Year and score filters are ranges: "all" or [lo, hi] (older saved
   bucket names read as "all"). */
const inRange = (r, v) => !Array.isArray(r) || (v != null && v >= r[0] && v <= r[1]);
function yearBounds() {
  const ys = (S.data.spine?.entries || []).map((e) => e.year).filter((y) => y != null);
  return ys.length ? [Math.min(...ys), Math.max(...ys)] : [1960, new Date().getFullYear() + 1];
}
const SORT = {
  updated: (a, b) => (b.updated || 0) - (a.updated || 0), score: (a, b) => (b.score || 0) - (a.score || 0),
  year: (a, b) => (b.year || 0) - (a.year || 0), title: (a, b) => (a.title || "").localeCompare(b.title || ""),
};
const inProg = (st) => ["CURRENT", "REPEATING", "PAUSED"].includes(st);
/* "Keep local" (TODO 12c) moves a folder from To match to Untracked. */
const unmatchedShows = () => S.data.shows.filter((s) => !s.meta?.anilist_id && !s.kept_local);
/* Folders with files their names give no episode number for: numbered in
   file order until you check them (Needs a look). */
const unclearShows = () => S.data.shows.filter((s) => (s.seasons || []).some((sea) => sea.episodes.some((e) => e.guessed)));

/* The Library lists each show; "Show: franchises" folds them into one card
   per franchise, the rest counted on it ("+2"). That card shows the entry
   last active in, else the series itself before its movies and extras,
   oldest first (Attack on Titan, not one of its films). */
const franchiseKey = (e) => (e.franchise_id ? "f" + e.franchise_id : "e" + e.id);
const SERIES = new Set(["TV", "TV_SHORT", "ONA"]);
const cardRank = (e) => [-(e.updated || 0), SERIES.has(e.format) ? 0 : 1, e.year || 9999];
const before = (a, b) => { const x = cardRank(a), y = cardRank(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i]; return false; };
function oneCardPerFranchise(list) {
  const groups = new Map();
  for (const e of list) {
    const g = groups.get(franchiseKey(e));
    if (g) { g.more++; if (before(e, g.best)) g.best = e; } else groups.set(franchiseKey(e), { best: e, more: 0 });
  }
  return [...groups.values()].map((g) => ({ ...g.best, more: g.more }));
}
const byFranchise = () => S.lib.group === "fr";
const libKey = (e) => (byFranchise() ? franchiseKey(e) : "e" + e.id);
const libCards = (list) => (byFranchise() ? oneCardPerFranchise(list) : list);
function libModel() {
  const d = S.data, sp = d.spine;
  if (!sp) return { cold: true, tabs: [], entries: [], untracked: [], unmatched: unmatchedShows(), unclear: unclearShows() };
  const counts = {};
  for (const e of sp.entries) (counts[e.status] ||= new Set()).add(libKey(e));
  const tabs = LISTS.filter(([k]) => counts[k]).map(([k, l]) => ({ k, l, n: counts[k].size }));
  if (sp.entries.length) tabs.unshift({ k: "ALL", l: "All", n: new Set(sp.entries.map(libKey)).size });
  const untracked = sp.untracked.map(showById).filter((s) => s && (s.meta?.anilist_id || s.kept_local));
  const unmatched = unmatchedShows(), unclear = unclearShows(), look = new Set([...unmatched, ...unclear]).size;
  if (untracked.length) tabs.push({ k: "UNTRACKED", l: "On disk only", n: untracked.length, side: true });
  if (look) tabs.push({ k: "MATCH", l: "Needs a look", n: look, attn: true, side: true });
  return { tabs, entries: sp.entries, untracked, unmatched, unclear };
}
function fixTab(m) {
  if (m.cold || !m.tabs.length) return;
  if (!m.tabs.some((t) => t.k === S.lib.tab)) S.lib.tab = m.tabs[0].k;
}
/* Sorted after the fold: a franchise card sits where the entry it shows sorts. */
function libList(m) {
  const f = S.lib;
  return libCards(m.entries.filter((e) => (f.tab === "ALL" || e.status === f.tab)
    && (srcLocked() || f.src === "all" || e.capability === f.src)
    && (f.fmt === "all" || e.format === f.fmt)
    && (f.air === "all" || e.airing === f.air)
    && inRange(f.year, e.year)
    && (!Array.isArray(f.score) || (e.score > 0 && inRange(f.score, e.score)))
    && hasGenres(e, f.genres))).sort(SORT[f.sort] || SORT.updated);
}
/* Genre filter (Decisions 13/28): an entry must carry every picked genre. */
const hasGenres = (e, picked) => !picked?.length || picked.every((g) => (e.genres || []).includes(g));
/* The tray: genres in the open tab, most cards first (one per franchise). */
function genreCounts(m) {
  const c = new Map();
  for (const e of m.entries) if (S.lib.tab === "ALL" || e.status === S.lib.tab)
    for (const g of e.genres || []) (c.get(g) || c.set(g, new Set()).get(g)).add(libKey(e));
  return [...c].map(([g, set]) => [g, set.size]).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}
/* @lib:end */
/* A poster card (Decision 38): source + score tags on the art, progress at
   its foot, the title and a mono fact line below. */
const CAPW = { installed: "disk", partial: "partial", online: "online", none: "" };
function entryCard(e, i) {
  const s = e.local_show_id ? showById(e.local_show_id) : null;
  const tg = s ? pickTarget(s) : null;
  const res = tg && tg.kind === "resume" && inProg(e.status) ? tg : null;
  const prog = e.progress || 0, pct = e.episodes ? Math.min(100, Math.round((100 * prog) / e.episodes)) : 0;
  const facts = [e.episodes ? `${prog} / ${e.episodes}` : prog ? `${prog} seen` : "", FMT[e.format] || e.format, e.airing === "RELEASING" ? "airing" : e.year].filter(Boolean).join(" · ");
  const src = e.capability === "none" ? capLabel("none") : CAPW[e.capability] || "";
  return `<button class="card" style="--i:${Math.min(i, 24)}" data-a="entry" data-v="${esc(e.id)}" data-poster="${esc(e.poster || "")}" data-color="${esc(e.color || "")}">
    <div class="pw">${poster(e.title, e.poster, { wait: S.data.meta_running && !e.poster })}${src ? `<span class="tg src ${esc(e.capability)}">${esc(src)}</span>` : ""}${e.score ? `<span class="tg sc">★ ${Math.round(e.score * 10) / 10}</span>` : ""}${e.favourite ? `<span class="fav" title="Favourite">${ico("heart")}</span>` : ""}
    ${res ? `<span class="resume-tag">${ico("play")}${esc(epCode(res.sea, res.ep))}${res.ep.position ? " · " + fmtTime(res.ep.position) : ""}</span>` : ""}
    ${inProg(e.status) && pct ? `<div class="prog"><i style="width:${pct}%"></i></div>` : ""}</div>
    <h3>${esc(e.title)}</h3>
    <div class="m">${esc(facts)}${e.more ? ` <span class="more" title="${e.more} more season${e.more === 1 ? "" : "s"} or movie${e.more === 1 ? "" : "s"} on its page">+${e.more}</span>` : ""}</div></button>`;
}
function showCard(s, i) {
  const total = epTotal(s), w = epWatched(s), pct = total ? Math.round((100 * w) / total) : 0;
  return `<button class="card" style="--i:${Math.min(i, 24)}" data-a="open" data-v="${esc(s.id)}" data-poster="${esc(s.poster || "")}">
    <div class="pw">${poster(s.title, s.poster, { wait: S.data.meta_running && !s.poster })}<span class="tg src installed">disk</span>
    ${pct ? `<div class="prog"><i style="width:${pct}%"></i></div>` : ""}</div>
    <h3>${esc(s.title)}</h3><div class="m">${w} / ${total}${s.collection ? " · " + esc(s.collection) : ""}</div></button>`;
}

/* AniList match repair (TODO 28): suggestions come from the same search the
   manual field uses; picking one writes the match. */
const mst = (id) => (S.match[id] ||= { cands: null, pick: 0, busy: false, err: "", q: "", tried: false });
const candLabel = (x) => x.title?.english || x.title?.romaji || `#${x.id}`;
/* The files' own lengths (played, else measured): "24 min each" only when
   every file is known and they agree, else the range of the known ones. */
function lensText(eps) {
  const mins = eps.map((x) => x.duration || x.length).filter(Boolean).map((v) => Math.round(v / 60));
  if (!mins.length) return "";
  const lo = Math.min(...mins), hi = Math.max(...mins), hm = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);
  if (eps.length === 1) return hm(lo);
  return lo === hi ? `${hm(lo)}${mins.length === eps.length ? " each" : ""}` : hi < 60 ? `${lo}–${hi} min` : `${hm(lo)}–${hm(hi)}`;
}
function matchUI(s) {
  const m = mst(s.id), c = (m.cands || []).slice(0, 5), n = epTotal(s), best = c[m.pick] || c[0];
  const files = s.seasons.flatMap((x) => x.episodes);
  const facts = [n > 1 ? `${n} episodes` : n ? "1 file" : "", lensText(files), s.seasons.length > 1 ? `${s.seasons.length} seasons` : ""].filter(Boolean);
  const meta = (x) => [x.seasonYear || x.startDate?.year, FMT[x.format] || x.format, x.episodes ? x.episodes + " ep" : "", x.duration ? x.duration + " min" : ""].filter(Boolean).join(" · ");
  return `<div class="match mc" data-show="${esc(s.id)}"><div class="mcl">
      <span class="cap2">${ico("folder")} folder on disk</span>
      <h3>${esc(s.title)}</h3>${s.path && s.path !== s.title ? `<div class="raw">${esc(s.path_label || s.path)}</div>` : ""}
      ${facts.length ? `<div class="facts">${facts.map((f, i) => `<span${i === 1 ? ' class="len"' : ""}>${esc(f)}</span>`).join("")}</div>` : ""}
      ${m.busy ? '<div class="found"><i class="spin"></i>Searching AniList…</div>' : ""}
      ${c.length > 1 ? `<div class="others"><span class="cap2">other guesses</span>${c.map((x, i) => i === m.pick ? "" : `<button class="oth" data-a="pick" data-s="${esc(s.id)}" data-v="${i}">${poster(candLabel(x), x.coverImage?.extraLarge || null, { c: "t" })}<span>${esc(candLabel(x))}</span><small>${esc(meta(x))}</small></button>`).join("")}</div>` : ""}
      <div class="mrow"><input id="mq-${esc(s.id)}" aria-label="Search AniList" placeholder="search AniList, or paste an ID / anilist.co link" value="${esc(m.q)}"><button class="btn sm" data-a="msearch" data-v="${esc(s.id)}">Search</button><button class="btn sm ic" data-a="mrefresh" data-v="${esc(s.id)}" aria-label="Ask AniList again" title="Ask AniList again instead of using the saved suggestions"${m.busy ? " disabled" : ""}>${ico("refresh")}</button></div>
      ${m.err ? `<div class="msg">${esc(m.err)}</div>` : ""}
      <div class="mrow"><button class="btn sm" data-a="keeplocal" data-v="${esc(s.id)}" data-k="${!s.kept_local}">${s.kept_local ? "Back to Needs a look" : "Not on AniList — keep local"}</button>${s.path ? `<button class="btn sm ic" data-a="folder" data-v="${esc(s.id)}" aria-label="Open the folder" title="Open the folder">${ico("folder")}</button>` : ""}</div>
    </div>
    ${best ? `<div class="guess">${poster(candLabel(best), best.coverImage?.extraLarge || null)}<b>${esc(candLabel(best))}</b><small>${esc(meta(best))}</small><button class="btn pri" data-a="match" data-v="${esc(s.id)}">This one</button></div>`
      : `<div class="guess none"><span>${m.cands && !m.busy ? "No AniList results for that title — search with another name, or keep it local." : m.busy ? "" : "Looking for a match…"}</span></div>`}</div>`;
}
/* Needs a look: a folder's guessed episode numbers, one field per file. */
function numUI(s) {
  const seas = s.seasons.filter((sea) => sea.episodes.some((e) => e.guessed));
  const name = (e) => (e.rel || "").split("/").pop();
  return `<div class="match mc nums" data-show="${esc(s.id)}"><div class="mcl">
      <span class="cap2">${ico("folder")} episode numbers</span><h3>${esc(s.title)}</h3>${s.path && s.path !== s.title ? `<div class="raw">${esc(s.path_label || s.path)}</div>` : ""}
      ${seas.map((sea) => `${s.seasons.length > 1 ? `<span class="cap2 nsea">${esc(sea.label)}</span>` : ""}<div class="nlist">${sea.episodes.map((e) =>
        `<label class="nrow${e.guessed ? " guess" : ""}"><input type="number" min="0" max="9999" value="${e.number}" data-ep="${esc(e.id)}" aria-label="Episode number for ${esc(name(e))}"><span title="${esc(name(e))}">${esc(name(e))}</span>${e.guessed ? '<small>guessed</small>' : ""}</label>`).join("")}</div>`).join("")}
      <div class="msg"></div>
      <div class="mrow"><button class="btn pri sm" data-a="numsave" data-v="${esc(s.id)}">Save numbers</button>${s.path ? `<button class="btn sm ic" data-a="folder" data-v="${esc(s.id)}" aria-label="Open the folder" title="Open the folder">${ico("folder")}</button>` : ""}</div>
    </div></div>`;
}
function paintMatch(id) {
  const el = document.querySelector(`.match[data-show="${CSS.escape(id)}"]`), s = showById(id);
  if (el && s) el.outerHTML = matchUI(s);
}
let candQ = Promise.resolve();
async function runSearch(id, text, fresh = false) {
  const m = mst(id);
  m.busy = true; m.err = ""; paintMatch(id);
  try {
    const r = await api("/api/match-candidates", fresh ? { query: text, fresh } : { query: text });
    if (r && r.error) { m.err = r.error; m.cands = []; } else { m.cands = r || []; m.pick = 0; }
  } catch { m.err = "Search failed — is the hub running?"; m.cands = []; }
  m.busy = false; paintMatch(id);
}
/* Every unmatched folder, queued one search at a time — started on each
   library load, so the To match tab opens with its suggestions ready
   (TODO 12a, Decision 8). The metadata pass already searched these titles,
   so most answers come from the cache. */
function autoSuggest(list) {
  for (const s of list) {
    const m = mst(s.id);
    if (m.tried || m.cands || m.busy) continue;
    m.tried = true;
    candQ = candQ.then(() => runSearch(s.id, s.title));
  }
}

function scanNote() {
  const scan = S.data.scan;
  if (!scan) return null;
  const bits = [];
  if (scan.root === "changed") bits.push("Your library folder changed — reconnect the new folder to pick your shows back up.");
  if (scan.state === "unconfigured") bits.push("No library folder is configured yet — choose one in Settings.");
  if (scan.state === "missing") bits.push("The library folder is not accessible right now.");
  else if (scan.state === "partial") bits.push("Some folders could not be read — part of the library may be missing.");
  return bits.length ? bits.join(" ") : null;
}
const metaText = (d) => `Fetching ${d.meta_phase || "metadata"} · ${d.meta_pending} left${d.meta_current ? ` · ${d.meta_current}` : ""}`;
/* Local mode is disk-only (source filter locked) until ani-cli streams add "online" entries. */
const srcLocked = () => localMode() && !S.data.spine.entries.some((e) => e.capability === "online");
const filtersActive = () => ["fmt", "air"].some((k) => S.lib[k] !== "all") || S.lib.genres.length > 0 || ["year", "score"].some((k) => Array.isArray(S.lib[k])) || (!srcLocked() && S.lib.src !== "all");
const fsel = (k, label, opts, locked) => {
  const set = !locked && k !== "sort" && S.lib[k] !== "all";
  return `<label class="fl">${label}<select data-a="filt" data-k="${k}" class="${set ? "set" : ""}" ${locked ? "disabled" : ""}>${(locked ? [opts[1]] : opts).map(([v, l]) => `<option value="${esc(v)}" ${v === S.lib[k] || locked ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
};
/* Two-handle range: two stacked range inputs over one painted track. */
function frng(k, label, lo, hi, step, fmt) {
  const v = Array.isArray(S.lib[k]) ? S.lib[k] : [lo, hi];
  const a = Math.max(lo, Math.min(hi, v[0])), b = Math.max(a, Math.min(hi, v[1]));
  const pct = (x) => ((x - lo) / (hi - lo || 1)) * 100;
  const inp = (n, val, l) => `<input type="range" aria-label="${label} ${l}" min="${lo}" max="${hi}" step="${step}" value="${val}" data-r="${n}">`;
  return `<div class="fl rng${Array.isArray(S.lib[k]) ? " set" : ""}" data-k="${k}" style="--a:${pct(a)}%;--b:${pct(b)}%"><span>${label}</span>
    <div class="rt">${inp(0, a, "from")}${inp(1, b, "to")}</div><b class="rv"><i>${a <= lo && b >= hi ? "any" : `${fmt(a)} – ${fmt(b)}`}</i></b></div>`;
}
let rngFrame = 0;
document.addEventListener("input", (e) => {
  const r = e.target.closest?.(".rng input");
  if (!r) return;
  const box = r.closest(".rng"), k = box.dataset.k, [i0, i1] = box.querySelectorAll("input");
  let a = +i0.value, b = +i1.value;
  if (a > b) { if (r === i0) i0.value = a = b; else i1.value = b = a; }
  const lo = +i0.min, hi = +i0.max, all = a <= lo && b >= hi;
  S.lib[k] = all ? "all" : [a, b];
  box.style.setProperty("--a", ((a - lo) / (hi - lo || 1)) * 100 + "%");
  box.style.setProperty("--b", ((b - lo) / (hi - lo || 1)) * 100 + "%");
  box.querySelector(".rv i").textContent = all ? "any" : `${a} – ${b}`;
  box.classList.toggle("set", !all);
  clearTimeout(rngFrame);
  rngFrame = setTimeout(() => { paintWall(); syncFilters(); saveLib(); }, 30);
});
/* Keep the "set" tint and the Clear button in step with the selects without rebuilding them (focus stays put). */
function syncFilters() {
  const f = $(".filters");
  if (!f) return;
  $$(".filters select[data-k]").forEach((s) => s.classList.toggle("set", s.dataset.k !== "sort" && !s.disabled && S.lib[s.dataset.k] !== "all"));
  const any = filtersActive(), b = f.querySelector(".clr");
  if (any && !b) {
    const n = document.createElement("button");
    n.className = "btn clr"; n.dataset.a = "fclear"; n.innerHTML = ico("close") + "Clear";
    f.insertBefore(n, f.querySelector(".fill"));
  } else if (!any && b) b.remove();
}

function Lib() {
  const d = S.data, m = libModel();
  fixTab(m);
  if (srcLocked() && S.lib.src !== "installed") S.lib.src = "installed";
  const note = scanNote();
  const notes = [];
  if (note) notes.push(`<div class="note-bar">${esc(note)}${d.scan?.root === "changed" ? ' <button class="btn" data-a="adopt">Connect folder</button>' : ""}</div>`);
  else if (d.meta_running && d.meta_pending > 0) notes.push(`<div class="note-bar metanote"><i class="spin"></i><span>${esc(metaText(d))}</span></div>`);
  if (m.cold && S.ani?.connected) notes.push('<div class="note-bar">Your AniList lists are still loading — showing what is on disk.</div>');
  const isMatch = S.lib.tab === "MATCH" && !m.cold, isUn = S.lib.tab === "UNTRACKED" && !m.cold;
  const files = d.shows.reduce((a, x) => a + epTotal(x), 0);
  const sum = [`<span><b>${m.entries.length ? new Set(m.entries.map(libKey)).size : d.shows.length}</b> shows</span>`, files ? `<span><b>${files.toLocaleString()}</b> files on disk</span>` : "",
    (() => { const n = new Set([...m.unmatched, ...m.unclear]).size; return n ? `<span><b>${n}</b> folder${n === 1 ? "" : "s"} need a look</span>` : ""; })()].filter(Boolean).join('<i class="sep"></i>');
  const tab = (t) => `<button class="${S.lib.tab === t.k ? "on" : ""}${t.attn ? " attn" : ""}" data-a="tab" data-v="${t.k}">${t.l}${t.attn ? '<i class="att"></i>' : ""}<span class="n">${t.n}</span></button>`;
  const tabs = m.tabs.length ? `<div class="tabs" data-glide="libtabs">${m.tabs.filter((t) => !t.side).map(tab).join("")}${m.tabs.some((t) => t.side) ? '<i class="vs"></i>' : ""}${m.tabs.filter((t) => t.side).map(tab).join("")}</div>` : "";
  let main;
  if (isMatch) {
    autoSuggest(m.unmatched);
    main = `${m.unmatched.length ? `<p class="intro">On disk, but not matched to AniList yet. Pick the right show and it joins your library — or keep it local.</p><div class="mgrid">${m.unmatched.map(matchUI).join("")}</div>` : ""}
      ${m.unclear.length ? `<p class="intro">Episode numbers to check: these file names don't say which episode they are, so they're numbered in file order. Fix any that are wrong, then save. Your files aren't renamed.</p><div class="mgrid">${m.unclear.map(numUI).join("")}</div>` : ""}`;
  } else {
    const filters = (m.cold || isUn) ? "" : `<div class="filters">
      ${fsel("src", "source", [["all", "any"], ["installed", "disk"], ["partial", "partial"], ["online", "online"], ["none", capLabel("none")]], srcLocked())}
      ${fsel("fmt", "format", [["all", "any"], ["TV", "TV"], ["MOVIE", "Movie"], ["OVA", "OVA"], ["ONA", "ONA"], ["SPECIAL", "Special"]])}
      ${fsel("air", "airing", [["all", "any"], ...Object.entries(AIR)])}
      ${frng("year", "year", ...yearBounds(), 1, String)}
      ${frng("score", "score", 0, 10, 0.5, (x) => String(x))}
      ${filtersActive() ? `<button class="btn clr" data-a="fclear">${ico("close")}Clear</button>` : ""}
      <span class="fill"></span>${fsel("group", "show", [["all", "each show"], ["fr", "franchises"]])}${fsel("sort", "sort", [["updated", "recently updated"], ["score", "score"], ["year", "year"], ["title", "title"]])}
      <button class="btn ic" data-a="rescan" aria-label="Rescan" title="Rescan library & fetch missing metadata">${ico("refresh")}</button><button class="btn pri fltdone" data-a="fltclose">Done</button></div>`;
    const fltb = filters ? `<div class="fltrow"><button class="btn fltb${filtersActive() ? " on" : ""}" data-a="fltopen">${ico("sliders")}Sort & filter${filtersActive() ? '<i class="att"></i>' : ""}</button><button class="btn fltb swb" data-a="deck" data-v="lib">${ico("up")}Swipe</button></div><i class="fltscrim" data-a="fltclose"></i>` : "";
    main = `${fltb}${filters}${(m.cold || isUn) ? "" : genreTray(m)}<div id="wall"></div>`;
  }
  return `<div class="lib2">${notes.join("")}<div class="hello"><h1 class="hd">Library</h1><div class="sum">${sum}</div></div>${tabs}${main}</div>`;
}
function genreTray(m) {
  const counts = genreCounts(m), have = new Set(counts.map(([g]) => g));
  const gone = S.lib.genres.filter((g) => !have.has(g)).map((g) => [g, 0]); // picked, but not in this tab: still unpickable
  const on1 = (g) => (S.lib.genres.includes(g) ? 0 : 1);
  const all = [...counts, ...gone].sort((a, b) => on1(a[0]) - on1(b[0])); // picked ones stay in the one visible row
  if (!all.length) return "";
  return `<div class="gwrap"><div class="gtray${S.genOpen ? " open" : ""}" role="group" aria-label="Genres">${all.map(([g, n]) => {
    const on = S.lib.genres.includes(g);
    return `<button class="gchip${on ? " on" : ""}" aria-pressed="${on}" data-a="genre" data-v="${esc(g)}">${esc(g)}<span class="n">${n}</span></button>`;
  }).join("")}</div><button class="gmore" data-a="gmore" hidden>${S.genOpen ? "Less" : "More"}</button></div>`;
}
/* "More" only when the genres really run past one row. */
function gtrayFit() {
  const t = $(".gtray"), b = $(".gmore");
  if (t && b) b.hidden = !S.genOpen && t.scrollHeight <= t.clientHeight + 2;
}
function paintWall(fade) {
  const w = $("#wall");
  if (!w) return;
  const m = libModel();
  const cards = m.cold ? S.data.shows.map(showCard) : S.lib.tab === "UNTRACKED" ? m.untracked.map(showCard) : libList(m).map(entryCard);
  let html = cards.join("");
  const empty = `<div class="emptyx" style="grid-column:1/-1"><div class="eico">${ico("search")}</div><b>${m.entries.length || S.data.shows.length ? "Nothing matches these filters." : "No shows found."}</b>${m.entries.length || S.data.shows.length ? "" : "<span>Check your library folder, then rescan.</span>"}</div>`;
  const fx = fade && !reduceMotion();
  w.className = "wall" + (fx ? " fade" : "");
  clearTimeout(paintWall.more);
  /* Touch screens (Decision 44): a fresh wall shows its first screenful at
     once and the rest in batches over the next frames — building all ~270
     cards in one go froze the page change on a phone. */
  if ((fx || !w.firstChild) && cards.length > 36 && mq("(pointer:coarse)")) {
    w.innerHTML = cards.slice(0, 24).join("");
    let i = 24;
    const more = () => { if (!w.isConnected) return; w.insertAdjacentHTML("beforeend", cards.slice(i, i + 30).join("")); i += 30; if (i < cards.length) paintWall.more = setTimeout(more, 24); };
    paintWall.more = setTimeout(more, 380); // after the page has slid in
  } else if (fx || !w.firstChild) w.innerHTML = html || empty; else morphHTML(w, html || empty);
  gtrayFit();
}

/* ---------- detail ---------- */
const alUrl = (id) => `https://anilist.co/anime/${id}`;
const alChip = (id) => id ? `<a class="mchip al" href="${alUrl(id)}" data-a="ext" data-v="${alUrl(id)}" title="Open on AniList">AniList${ico("ext")}</a>` : "";
/* @detail:start */
const epThumbOf = (thumbs, ep) => !thumbs ? null : Array.isArray(thumbs) ? (thumbs[ep.number - 1] || null) : (thumbs[String(ep.number)] || null);

/* Audio of an episode's files: a "(Dub)"-style version label means dub,
   anything else counts as sub. */
function fileLangs(ep) {
  const files = ep.files?.length ? ep.files : [{ path: ep.rel || "", label: "" }];
  const dub = files.some((f) => /\bdub/i.test(f.label || "")), sub = files.some((f) => !/\bdub/i.test(f.label || ""));
  return sub && dub ? "sub+dub" : dub ? "dub" : "sub";
}
/* One episode tile (Decision 38): the art card on top — number, progress,
   ✓, play on hover, the ⋯ menu — and the title + a mono fact line below. */
const minsOf = (sec, fallbackMin) => (sec ? Math.round(sec / 60) : fallbackMin || 0);
function tileHTML({ cls, a, v, i, art, pos, num, pct, dlPct, loading, watched, picking, picked, title, meta, menu }) {
  return `<div class="${cls}" tabindex="0" data-a="${a}" data-v="${esc(v)}" style="--i:${Math.min(i, 16)}">
    <div class="epa"><span class="epimg" data-bg="${esc(art || "")}" data-bp="${pos}"></span>
    ${pct ? `<i class="eprog"><i style="width:${pct}%"></i></i>` : ""}${dlPct != null ? `<i class="eprog dl"><i style="width:${dlPct}%"></i></i>` : ""}
    <span class="eplay">${loading ? '<i class="spin"></i>' : ico("play")}</span><span class="enum">${esc(num)}</span>
    ${watched ? `<i class="tick">${ico("check")}</i>` : ""}${picking ? `<i class="selbox">${picked ? ico("check") : ""}</i>` : ""}
    <button class="dots" aria-label="${esc(menu)}" data-a="cm" data-v="${esc(v)}">${ico("dots")}</button></div>
    <div class="et"><b>${esc(title)}</b><small>${esc(meta)}</small></div></div>`;
}
function epTile(s, sea, ep, tgId, i = 0) {
  /* Art order (Elias, 2026-10-17): the matched per-episode still when the
     listing has one, otherwise the show poster on EVERY tile — slicing the
     banner per episode made each tile a different, magnified crop ("the
     episodes get different images… it zooms in"). */
  const rt = epThumbOf(sea.thumbs, ep), art = rt || s.poster || s.banner;
  const pos = rt ? "50% 50%" : s.poster ? "50% 22%" : "50% 50%";
  const pct = ep.duration ? Math.min(100, (100 * ep.position) / ep.duration) : 0;
  const part = resumable(ep);
  const files = ep.files || [], loading = epLoading(ep.id);
  const picking = S.sel?.show === s.id, picked = picking && S.sel.ids.has(ep.id);
  const cls = ["ep", picking ? "pick" : "", picked ? "sel" : "", ep.watched ? "watched" : "", ep.id === tgId && resumable(ep) ? "resume" : "", art ? "" : "noart", loading ? "loading" : ""].filter(Boolean).join(" ");
  const mins = minsOf(ep.duration || ep.length), lang = fileLangs(ep);
  const meta = loading ? "loading…" : part ? `${fmtLeft(ep.duration, ep.position)} left · disk · ${lang}` : `${mins ? mins + " min · " : ""}disk · ${lang}${files.length > 1 ? ` · ${files.length} versions` : ""}`;
  return tileHTML({ cls, a: "playep", v: ep.id, i, art, pos, num: "E" + String(ep.number).padStart(2, "0"), pct: part ? pct : 0, loading, watched: ep.watched && !part,
    picking, picked, title: epTitle(s, sea, ep) || "Episode " + ep.number, meta, menu: `Episode ${ep.number} menu` });
}
/* Multi-select (S.sel = { show, ids, anchor }): tiles toggle instead of
   playing; shift extends a range within the season, the bar acts on all. */
const selKey = () => (S.view === "fr" ? frSelKey() : S.showId);
function selToggle(id, range) {
  const s = S.view === "fr" ? null : showById(S.showId), fe = S.view === "fr" ? frEntry() : null;
  if (!s && !fe) return;
  if (S.sel?.show !== selKey()) S.sel = { show: selKey(), ids: new Set(), anchor: null };
  const eps = fe ? fe.slots.filter((x) => playableSlot(fe, x)).map((x) => x.id) : (s.seasons[S.seasonIdx] || s.seasons[0]).episodes.map((e) => e.id);
  const a = eps.indexOf(S.sel.anchor), b = eps.indexOf(id);
  if (range && a >= 0 && b >= 0) for (const x of eps.slice(Math.min(a, b), Math.max(a, b) + 1)) S.sel.ids.add(x);
  else if (S.sel.ids.has(id)) S.sel.ids.delete(id);
  else S.sel.ids.add(id);
  S.sel.anchor = id;
  pagePatch(); paintSel();
}
function selEnd() { if (!S.sel) return; S.sel = null; pagePatch(); paintSel(); }
function selEps() {
  if (S.view === "fr") { const e = frEntry(); return e && S.sel?.show === frSelKey() ? e.slots.filter((x) => S.sel.ids.has(x.id)) : []; }
  const s = showById(S.showId);
  return s && S.sel?.show === s.id ? s.seasons.flatMap((x) => x.episodes).filter((e) => S.sel.ids.has(e.id)) : [];
}
function paintSel() {
  let bar = $("#selbar");
  const on = (S.view === "show" || S.view === "fr") && S.sel && S.sel.show === selKey();
  if (!on) { bar?.remove(); return; }
  const eps = selEps(), n = eps.length;
  const fe = S.view === "fr" ? frEntry() : null, ok = !!(fe && S.data.stream_ready && fe.online.available);
  const dl = ok ? eps.filter((x) => x.online && !x.local && !dlJob(x.online.id)) : [];
  if (!bar) { bar = document.createElement("div"); bar.id = "selbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Selected episodes"); document.body.append(bar); }
  bar.innerHTML = `<b>${n} selected</b><button class="link-btn" data-a="selall">Select season</button><span class="fill"></span>
    <button class="btn" data-a="selmark" data-w="true" ${n ? "" : "disabled"}>${ico("check")}Mark watched</button>
    <button class="btn" data-a="selmark" data-w="false" ${n ? "" : "disabled"}>${ico("undo")}Mark unwatched</button>
    ${ok ? `<button class="btn" data-a="seldl" ${dl.length ? "" : "disabled"}>${ico("download")}Download${dl.length ? " " + dl.length : ""}</button>` : ""}
    <button class="btn" data-a="selmode" aria-label="Stop selecting">${ico("close")}</button>`;
}
/* @detail:end */
/* ---------- online episodes (ani-cli, v0.2) ----------
   ani-cli works out of sight: the hub matches every release to it by
   itself, and online episodes are just episodes. The only way a source
   ever surfaces is "Wrong episodes?", for the rare bad match. */
const SLMODES = [["sub", "Sub"], ["dub", "Dub"]];
const dlJob = (id) => S.dl.find((j) => `stream:${j.media_id}:${j.token}` === id && (j.status === "running" || j.status === "queued"));
const epLoading = (id) => id === S.loadingEp || (!!S.data.now?.loading && S.data.now.episode_id === id);
const dlBtn = (mid, eps, label) => `<button class="dl-btn" data-a="dlmany" data-v="${mid}" data-eps="${esc(JSON.stringify(eps))}">${ico("download")}${label}</button>`;

/* "Wrong episodes?": the manual override for a bad automatic match. */
const slst = (mid) => (S.slink[mid] ||= { q: "", res: null, pick: 0, busy: "", err: "" });
const normT = (t) => String(t || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "");
function SrcDlg() {
  const { mid, title } = S.modal, st = slst(mid), res = st.res || [], cur = res[st.pick];
  return `<h2 id="dt">Wrong episodes?</h2>
  <p class="sub">Pick the search result that is “${esc(title)}”. Your choice is kept.</p>
  <div class="row" style="margin-top:14px"><input id="slq" aria-label="Search" placeholder="Search for the show" value="${esc(st.q)}"><button class="btn" data-a="slsearch">${ico("search")}Search</button></div>
  ${st.busy ? `<div class="found"><i class="spin"></i>${esc(st.busy)}</div>` : ""}
  ${res.length ? `<div class="slres" style="margin-top:12px">${res.map((r, i) => `<button class="mi${i === st.pick ? " on" : ""}" data-a="slpick" data-v="${i}">${ico(i === st.pick ? "checkc" : "tv")}<span><b>${esc(r.title || st.q)}</b>${r.title ? "" : "<small>The only result for this search</small>"}</span></button>`).join("")}</div>`
    : st.res && !st.busy ? '<p class="sub" style="margin-top:12px">Nothing found — try another spelling.</p>' : ""}
  <div class="msg">${esc(st.err)}</div>
  <div class="actions"><button class="btn" data-a="close">Cancel</button>${cur ? `<button class="btn pri" data-a="sllink" ${st.busy ? "disabled" : ""}>Use this one</button>` : "<span></span>"}</div>`;
}
async function slSearch(mid, text, titles) {
  const st = slst(mid);
  st.q = text; st.busy = "Searching…"; st.err = ""; paintModal();
  try {
    const r = await api("/api/stream/search", { query: text });
    if (r && r.error) { st.err = r.error; st.res = []; }
    else {
      st.res = r.results || []; st.q = r.query || text;
      const want = new Set((titles || []).map(normT).filter(Boolean));
      st.pick = Math.max(0, st.res.findIndex((x) => want.has(normT(x.title))));
    }
  } catch { st.err = "Search failed — is the hub running?"; st.res = []; }
  st.busy = "";
  if (S.modal?.t === "src") paintModal();
}
/* ---------- franchise page (docs/franchise-page-plan.md) ----------
   One page per anime: every season, movie and special of it, and each
   one's episodes as a single list where a file on disk and an online
   episode are the same tile with a different badge. The hub builds the
   page (GET /api/franchise); this only draws it. S.fr = { eid: the
   selected entry, page, err }. */
const MEDIA_STATUS = { RELEASING: "Airing", FINISHED: "Finished", NOT_YET_RELEASED: "Upcoming", HIATUS: "Hiatus", CANCELLED: "Cancelled" };
const frEntry = () => S.fr?.page?.entries.find((e) => e.id === S.fr.eid) || null;
const frSelKey = () => "fr:" + S.fr?.eid;
const slotIds = (x) => [x.local?.id, x.online?.id].filter(Boolean);
const playableSlot = (e, x) => !!x.local || (!!x.online && !!S.data.stream_ready && e.online.available);
/* A slot of the open page by any of its ids (file or online). */
function frFind(id) {
  for (const e of S.fr?.page?.entries || []) for (const x of e.slots) if (x.id === id || x.local?.id === id || x.online?.id === id) return { e, x };
  return null;
}
/* What the big button plays: the episode left half-watched most recently,
   else the first unwatched one, else the first (rewatch). */
function frTarget(e) {
  const slots = e.slots.filter((x) => playableSlot(e, x));
  let resume = null;
  const allW = slots.every((x) => x.watched);
  for (const x of slots) if (resumable(x) && (!x.watched || allW) && (!resume || (x.last_played || "") > (resume.last_played || ""))) resume = x;
  if (resume) return { x: resume, kind: "resume" };
  const next = slots.find((x) => !x.watched);
  if (next) return { x: next, kind: "play" };
  return slots[0] ? { x: slots[0], kind: "rewatch" } : null;
}
/* The entry a folder or online show opens at: the season of the episode
   it would play next (its mapped range), else its own match. */
function frEntryOfShow(s) {
  if (isStream(s)) return s.meta?.anilist_id || null;
  if (!s?.meta?.anilist_id) return null;
  const t = pickTarget(s), n = t?.ep.number;
  const r = t && (t.sea.releases || []).find((r) => r.first_ep <= n && (r.last_ep == null || n <= r.last_ep));
  return r ? r.id : s.meta.anilist_id;
}
/* Pages seen or prefetched (TODO 61), by every entry they hold: a page
   opens drawn from here at once, and frLoad brings it up to date in place. */
const frMemo = new Map();
function frRemember(p) { if (p && !p.error && p.entries?.length) for (const e of p.entries) frMemo.set(e.id, p); }
const frKnown = (mid) => (S.fr?.page?.entries.some((e) => e.id === mid) ? S.fr.page : frMemo.get(mid) || null);
const frPeeking = new Set();
function frPeek(mid) {
  if (!mid || frMemo.has(mid) || frPeeking.has(mid)) return Promise.resolve();
  frPeeking.add(mid);
  return api(`/api/franchise?id=${mid}&peek=1`).then(frRemember).catch(() => {}).finally(() => frPeeking.delete(mid));
}
let frOpenTok = 0;
function openFranchise(mid, fromShow = null) {
  const tok = ++frOpenTok;
  const go = () => tok === frOpenTok && nav("fr", (prev) => {
    if (prev !== "fr" && prev !== "show") S.showBack = prev;
    S.fr = { eid: mid, page: frKnown(mid), err: null, fromShow };
    frLoad(mid); // after the view switched: an answer for another view is dropped
  });
  /* Not seen yet: wait a moment for it, so the page opens drawn instead of on a spinner. */
  if (frKnown(mid)) go(); else Promise.race([frPeek(mid), sleep(250)]).then(go);
}
/* Library (Decision 38): the accent follows the poster you point at at once;
   the background only after you rest on it (~0.45 s), so sweeping is calm. */
let libRest = 0;
document.addEventListener("pointerover", (e) => {
  const c = e.target.closest?.("#app .lib2 .card");
  if (!c || e.pointerType === "touch" || c === libRest.el) return;
  clearTimeout(libRest.t); clearTimeout(libRest.a);
  const art = { blur: c.dataset.poster || null, sharp: null, color: c.dataset.color || colorOfId(+c.dataset.v) || null };
  /* A colour change restyles the whole page (~15 ms with a full wall), so here it
     swaps without the fade and skips posters the pointer only sweeps across. */
  libRest = { el: c, a: setTimeout(() => { document.documentElement.classList.add("amb-now"); accentFrom(art.blur, art.color); }, 140), t: setTimeout(() => { if (S.view === "library") { S.libHover = art; swapLayer("cv-blur", lookGet("art") === "off" ? null : art.blur); } }, 450) };
}, { passive: true });
/* Hovering a card prefetches its page (stored data only — the hub starts nothing). */
let peekTimer = 0;
document.addEventListener("pointerover", (e) => {
  const t = e.target.closest?.("[data-a=entry],[data-a=open],[data-a=catpick]");
  if (!t || e.pointerType === "touch") return;
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => {
    const v = t.dataset.v, a = t.dataset.a;
    const mid = a === "entry" ? (aniMode() ? +v : S.data?.spine?.entries.find((x) => String(x.id) === v)?.media_id)
      : a === "catpick" ? S.alres?.[+v]?.id : frEntryOfShow(showById(v));
    if (mid) frPeek(mid);
  }, 60);
}, { passive: true });
/* Fetch the page; while the hub is still building the franchise or
   finding the selected entry's online episodes, ask again. Episode stills
   of an entry nobody opened before arrive a moment later: one more look. */
let frTok = 0;
async function frLoad(mid) {
  const tok = ++frTok;
  let artLook = true;
  for (let i = 0; i < 80; i++) {
    const p = await api("/api/franchise?id=" + mid).catch(() => null);
    if (tok !== frTok || S.view !== "fr" || !S.fr) return;
    if (!p || p.error) { if (!S.fr.page) { S.fr.err = p?.error || "Could not reach the hub."; softRender(); } return; }
    if (p.unavailable && !p.entries.length) return frUnavailable(p.unavailable);
    const first = !S.fr.page;
    S.fr.page = p; S.fr.err = null; frRemember(p);
    first ? softRender() : frRepaint();
    const e = p.entries.find((x) => x.id === S.fr.eid);
    let wait = p.building || p.resolving.includes(S.fr.eid);
    if (!wait && artLook && e && e.slots.length && !e.slots.some((x) => x.thumb)) { artLook = false; wait = true; await sleep(3500); }
    if (!wait) return;
    await sleep(400); // building (batched) and finding online episodes both take a second or so now
  }
}
/* AniList unreachable and nothing stored for this entry (TODO 57): a page
   opened from a show falls back to that show's own page, which needs no
   AniList; any other open says why instead of loading forever. */
function frUnavailable(msg) {
  const sid = S.fr.fromShow, s = sid && showById(sid);
  if (!s) { S.fr.err = msg; softRender(); return; }
  S.frDown[sid] = true;
  const t = pickTarget(s);
  S.view = "show"; S.showId = sid; S.seasonIdx = t ? Math.max(0, s.seasons.indexOf(t.sea)) : 0;
  history.replaceState(navSnap(), "", navUrl());
  render();
  toast(msg + " Showing the folder for now.");
}
/* Watch state and loading badges move without rebuilding the page's
   artwork (patchShow); a different set of tiles is a full paint. */
const frSig = () => { const e = frEntry(), p = S.fr?.page; return p ? JSON.stringify([S.fr.eid, p.building, p.entries.map((x) => [x.id, x.slots.length, x.slots.filter((y) => y.thumb).length]), p.other_local.length, e?.online]) : ""; };
let frLastSig = "";
function frRepaint() {
  if (S.view !== "fr") return;
  const sig = frSig();
  if (sig !== frLastSig || !$("#app .frpage")) { softRender(); return; }
  patchShow(Fran);
}
function frApplyWatch(e, numbers, watched, reset) {
  const pick = numbers ? new Set(numbers) : null;
  for (const x of e.slots) {
    if (pick && !pick.has(x.number)) continue;
    x.watched = watched;
    if (watched) x.position = 0; // a mark means done: no stop point left
    if (reset) { x.position = 0; x.last_played = null; }
  }
  frRepaint();
  loadEntry(e.id);
  refreshData();
}
function frMark(e, numbers, watched) {
  return api("/api/entry-watched", { media_id: e.id, watched, ...(numbers ? { numbers } : {}) }).then((r) => {
    if (!r || r.error) { toast(r?.error || "Could not save — is the hub running?"); return false; }
    toast(watched ? `Marked ${r.episodes} episode${r.episodes === 1 ? "" : "s"} watched${r.synced ? " — will sync" : ""}`
      : `Removed watched marks from ${r.episodes} episode${r.episodes === 1 ? "" : "s"}`);
    frApplyWatch(e, numbers, watched, false);
    return true;
  }).catch(() => { toast("Could not save — is the hub running?"); return false; });
}
function slotTile(p, e, x, tgId, i = 0) {
  const art = x.thumb || e.poster || p.poster || e.banner;
  const pos = x.thumb ? "50% 50%" : e.poster || p.poster ? "50% 22%" : "50% 50%";
  const pct = x.duration ? Math.min(100, (100 * x.position) / x.duration) : 0, part = resumable(x);
  const ok = playableSlot(e, x), loading = slotIds(x).some(epLoading), job = x.online && !x.local && dlJob(x.online.id);
  const picking = S.sel?.show === frSelKey(), picked = picking && S.sel.ids.has(x.id);
  const cls = ["ep", picking ? "pick" : "", picked ? "sel" : "", x.watched ? "watched" : "", x.id === tgId ? "resume" : "", art ? "" : "noart", loading ? "loading" : "", ok ? "" : "off"].filter(Boolean).join(" ");
  const files = x.local?.files || [], mins = minsOf(x.duration || x.length, e.duration);
  const src = x.local ? `disk · ${fileLangs({ files })}` : ok ? `online · ${e.online.mode || S.settings?.stream?.mode || "sub"}` : "not available";
  const meta = loading ? "loading…" : job ? (job.status === "running" ? `downloading · ${Math.floor(job.percent)}%` : "waiting to download")
    : part ? `${fmtLeft(x.duration, x.position)} left · ${src}` : `${mins ? mins + " min · " : ""}${src}`;
  const movie = e.format === "MOVIE" && e.slots.length <= 1;
  return tileHTML({ cls, a: ok ? "playep" : "unavail", v: x.id, i, art, pos, num: movie ? "Movie" : "E" + String(x.number).padStart(2, "0"),
    pct: part ? pct : 0, dlPct: job?.status === "running" ? job.percent : null, loading, watched: x.watched && !part, picking, picked,
    title: x.title || (movie ? e.label : "Episode " + x.number), meta, menu: `Episode ${x.number} menu` });
}
/* Files of the franchise's folders that are no entry ("Specials"): plain
   tiles, and the way to say which entry they are. */
function otherLocal(p, e) {
  return p.other_local.map((o) => `<div class="eps-head frother"><h3>${esc(o.folder)} · ${esc(o.label)}</h3><button class="link-btn fix" data-a="pinopen" data-s="${esc(o.show_id)}" data-k="${esc(o.season)}" data-l="${esc(o.folder + " · " + o.label)}">Which season is this?</button></div>
    <div class="eps">${o.episodes.map((ep, i) => slotTile(p, { ...e, slots: [], format: null, poster: p.poster }, { id: ep.id, number: ep.number, title: null, thumb: null,
      local: { id: ep.id, files: ep.files }, online: null, watched: ep.watched, position: ep.position, duration: ep.duration, last_played: ep.last_played }, null, i)).join("")}</div>`).join("");
}
function frWhen(at) { const s = at - Date.now() / 1000; return s > 0 ? `in ${fmtIn(s)}` : "now"; }
/* The show page (Decision 38): the show's art is the canvas behind it;
   title, your entry as a row of numbers, actions with their keys, then the
   season tabs and the episode strip. Rare actions sit behind ⋯. */
const kb = (k) => `<kbd class="kb">${k}</kbd>`;
const backBtn = () => `<button class="backbtn" data-a="back">${ico("back")}${backLabel()}</button>`;
function Fran() {
  const fr = S.fr, p = fr.page;
  if (!p || (!p.entries.length && p.building)) return `<div class="sp2 frpage">${backBtn()}<p class="empty" style="padding-top:120px">${fr.err ? esc(fr.err) : '<i class="spin"></i> Loading…'}</p></div>`;
  if (!p.entries.length) return `<div class="sp2 frpage">${backBtn()}<p class="empty" style="padding-top:120px">AniList has no entry with this id.</p></div>`;
  const e = frEntry() || p.entries[0];
  if (e.id !== fr.eid) fr.eid = e.id;
  const tg = frTarget(e), movie = e.format === "MOVIE" && e.slots.length <= 1;
  const goBusy = tg && slotIds(tg.x).some(epLoading);
  const goLabel = tg ? `${KIND_WORD[tg.kind]}${movie || tg.kind === "rewatch" ? "" : " E" + String(tg.x.number).padStart(2, "0")}` : "";
  const facts = [p.title && p.title !== e.title ? p.title : "", e.format_label, e.year, e.episodes ? `${e.episodes} episodes` : e.aired ? `${e.aired} so far` : "",
    MEDIA_STATUS[e.status] && e.status !== "FINISHED" ? MEDIA_STATUS[e.status].toLowerCase() : "", e.score ? `★ ${e.score}` : ""].filter(Boolean);
  const airing = e.next_airing?.at ? `E${e.next_airing.episode} airs ${frWhen(e.next_airing.at)}` : "";
  const nowId = S.data.now?.episode_id;
  const playing = nowId && e.slots.some((x) => slotIds(x).includes(nowId)) ? `<span class="playing"><i class="led"></i>${S.data.now.loading ? "loading" : "playing"} · ${esc(S.data.now.label)}</span>` : "";
  const main = p.entries.filter((x) => x.group === "main"), extra = p.entries.filter((x) => x.group === "extra");
  const chip = (x, fmt) => { const n = entryCount(x);
    return `<button class="${x.id === e.id ? "on" : ""}" data-a="frsel" data-v="${x.id}" title="${esc(x.title || x.label)}">${fmt ? `<small>${esc(x.format_label)}</small>` : ""}${esc(x.label)}${n ? `<span class="n">· ${n}</span>` : ""}</button>`; };
  const tabs = `${main.length ? `<div class="rels" data-glide="frmain">${main.map((x) => chip(x)).join("")}</div>` : ""}${extra.length ? `<div class="rels extra" data-glide="frextra">${extra.map((x) => chip(x, true)).join("")}</div>` : ""}`;
  const ready = !!S.data.stream_ready, linked = e.online.linked;
  const seen = e.slots.filter((x) => playableSlot(e, x)), allW = seen.length > 0 && seen.every((x) => x.watched);
  const missing = ready && e.online.available ? e.slots.filter((x) => x.online && !x.local && !dlJob(x.online.id)) : [];
  const mBusy = S.modeBusy === e.id, curMode = e.online.mode || S.settings?.stream?.mode || "sub";
  const modeSeg = ready && linked ? `<span class="seg${mBusy ? " busy" : ""}" role="group" aria-label="Audio for online episodes">${SLMODES.map(([m, l]) => `<button class="${curMode === m ? "on" : ""}" aria-pressed="${curMode === m}" data-a="smode" data-v="${m}" ${mBusy ? "disabled" : ""}>${mBusy && curMode !== m ? '<i class="spin"></i>' : ""}${l}</button>`).join("")}</span>` : "";
  const local = e.slots.filter((x) => x.local).map((x) => x.number), online = e.slots.filter((x) => !x.local && x.online).map((x) => x.number);
  const span = (ns) => !ns.length ? "" : ns.length === 1 ? String(ns[0]) : `${ns[0]}–${ns[ns.length - 1]}`;
  const allDisk = local.length > 0 && local.length === e.slots.length;
  const srcLine = [allDisk ? (local.length === 1 ? "on disk" : `all ${local.length} on disk`) : local.length ? `${span(local)} on disk` : "", online.length && ready && e.online.available ? `${span(online)} online` : ""].filter(Boolean).join(" · ");
  const vpn = S.data?.vpn, vpnTag = !vpn?.known ? "" : vpn.active ? '<span class="v ok">vpn ✓</span>' : '<span class="v warn">no vpn</span>';
  const dlLabel = movie ? "Download" : missing.length === e.slots.length ? "Download all" : `Download ${span(missing.map((x) => x.number))}`;
  const resolving = p.resolving.includes(e.id);
  const note = e.slots.length ? (!e.slots.some((x) => x.local) && !ready ? '<p class="sub eps-note">Install ani-cli to watch these online.</p>'
      : ready && !e.online.available && e.slots.some((x) => !x.local) ? `<p class="sub eps-note">Not available to watch online. <button class="link-btn fix" data-a="srcfix">Find it manually</button></p>` : "")
    : `<p class="sub eps-note">${resolving ? '<i class="spin"></i> Looking for episodes…' : e.status === "NOT_YET_RELEASED" ? `Not out yet${airing ? " — " + esc(airing) : ""}.` : !ready ? "Install ani-cli to watch this online." : !e.online.available ? `Not available to watch online. <button class="link-btn fix" data-a="srcfix">Find it manually</button>` : "No episodes yet."}</p>`;
  const framed = !(e.banner || p.banner) || lookGet("art") !== "full";
  return `<div class="sp2 frpage">${backBtn()}
  <div class="hero${framed ? " framed" : ""}"><div class="hl2">
    <div class="meta"><i class="mdot"></i><span>${esc(facts.join(" · "))}</span><span class="sep">·</span>${alChip(e.id)}<span class="live-t" id="airline">${esc(airing)}</span>${playing}</div>
    <h1>${esc(e.title || p.title)}</h1>
    ${e.overview ? `<p class="desc">${esc(e.overview)}</p>` : ""}
    <p class="mfacts" id="mediafacts"></p>
    <div class="stats2" id="entrystats"></div>
    <div class="cta">${tg ? `<button class="btn pri big${goBusy ? " loading" : ""}" data-a="play" data-v="${esc(tg.x.id)}">${goBusy ? '<i class="spin"></i>' : ico("play")}<span>${goBusy ? "Loading…" : esc(goLabel)}</span>${kb("↵")}</button>` : ""}
      ${missing.length ? `<button class="btn big" data-a="dlmany" data-v="${e.id}" data-eps="${esc(JSON.stringify(missing.map((x) => x.online.token)))}">${ico("download")}${esc(dlLabel)}${vpnTag}${kb("D")}</button>` : ""}
      ${seen.length ? `<button class="btn big" data-a="frbulk" data-w="${!allW}">${ico(allW ? "undo" : "check")}${allW ? "Unwatch all" : movie ? "Mark watched" : "Mark all watched"}${kb("⇧W")}</button>` : ""}
      <button class="btn big" data-a="edit">${ico("edit")}Edit entry${kb("E")}</button>
      <button class="btn big ic" aria-label="More" title="More" data-a="frmore">${ico("dots")}</button></div>
  </div>${framed ? `<div class="hp">${poster(e.title || p.title, e.poster || p.poster)}</div>` : ""}</div>
  ${p.building ? '<p class="sub frbuild"><i class="spin"></i> Finding the other seasons and movies…</p>' : ""}
  <div class="eps-head"><div class="tabs2">${tabs || `<h2>${movie ? "Movie" : "Episodes"}</h2>`}</div><div class="ctl"><span class="srcl">${esc(srcLine)}</span>${modeSeg}${e.slots.length > 4 ? `<button class="arr" data-a="strip" data-d="-1" aria-label="Scroll left">${ico("chevl")}</button><button class="arr" data-a="strip" data-d="1" aria-label="Scroll right">${ico("chevr")}</button>` : ""}</div></div>
  ${e.slots.length ? `<div class="eps strip">${e.slots.map((x, i) => slotTile(p, e, x, tg && tg.x.id, i)).join("")}</div>` : ""}${note}
  ${p.other_local.length ? `<h2 class="frother-h">Other files</h2>${otherLocal(p, e)}` : ""}
  </div>`;
}
/* ⋯ on the show page: the rare actions, in the episode-menu box. */
function frMore(x0, y0) {
  const e = frEntry(), p = S.fr?.page;
  if (!e) return;
  const folders = [...new Map(e.slots.filter((x) => x.local).map((x) => [x.local.show_id + "|" + x.local.season, x.local])).values()];
  const ready = !!S.data.stream_ready, picking = S.sel?.show === frSelKey();
  const m = $("#cm");
  clearTimeout(cmHide.t); m.classList.remove("closing");
  m.innerHTML = `<div class="cap">${esc(e.title || p.title)}</div>
    ${e.slots.some((x) => playableSlot(e, x)) ? `<button class="mi" data-a="selmode">${ico("checkc")}${picking ? "Stop selecting" : "Select episodes"}</button>` : ""}
    ${folders.length ? `<button class="mi" data-a="folder" data-v="${esc(folders[0].show_id)}">${ico("folder")}Open the folder</button>` : ""}
    ${folders.map((l) => `<button class="mi" data-a="pinopen" data-s="${esc(l.show_id)}" data-k="${esc(l.season)}" data-l="${esc(l.folder + " · " + l.season_label)}">${ico("tv")}Wrong season? <small>${esc(l.season_label)}</small></button>`).join("")}
    ${ready && e.online.linked ? `<button class="mi" data-a="srcfix">${ico("search")}Wrong online episodes?</button>` : ""}
    <a class="mi" href="${alUrl(e.id)}" data-a="ext" data-v="${alUrl(e.id)}">${ico("ext")}Open on AniList</a>`;
  /* Phones (S2): the page shows only Play; its other buttons (download,
     mark all watched, edit entry) open from here, first. */
  if (isPhone()) {
    const cap = m.querySelector(".cap");
    for (const b of [...$$("#app .frpage .cta .btn:not(.pri):not(.ic)")].reverse()) {
      const c = b.cloneNode(true);
      c.className = "mi"; c.querySelectorAll(".kb").forEach((k) => k.remove());
      cap.after(c);
    }
    m.insertAdjacentHTML("afterbegin", grab);
  }
  m.style.animation = "none"; void m.offsetWidth; m.style.animation = "";
  const z = zoomGet() / 100;
  m.style.cssText += (isPhone() && ovOpen("cm", () => { cmHide(); }), "") + `;display:block;left:${Math.min(x0 / z, innerWidth / z - 270)}px;top:${Math.min(y0 / z, innerHeight / z - 280)}px`;
}
/* "Wrong season?": say which entry a folder season is (or none of them),
   or hand it back to the automatic mapping. */
function PinDlg() {
  const m = S.modal, p = S.fr?.page, opts = [...(p?.entries || []).map((x) => [String(x.id), x.label, [x.format_label, x.year].filter(Boolean).join(" · ")]),
    ["none", "None of these", "Keep the files as other files"], ["auto", "Decide automatically", "The hub matches the folder by itself"]];
  return `<h2 id="dt">Which season is this?</h2><p class="sub">${esc(m.label)}</p>
  <div class="slres" style="margin-top:12px">${opts.map(([v, l, s]) => `<button class="mi${m.pick === v ? " on" : ""}" data-a="pinpick" data-v="${esc(v)}">${ico(m.pick === v ? "checkc" : "tv")}<span><b>${esc(l)}</b>${s ? `<small>${esc(s)}</small>` : ""}</span></button>`).join("")}</div>
  <div class="msg">${esc(m.err || "")}</div>
  <div class="actions"><button class="btn" data-a="close">Cancel</button><button class="btn pri" data-a="pinsave" ${m.pick && !m.busy ? "" : "disabled"}>Save</button></div>`;
}
/* The episode menu (right-click or ⋯, Decision 38): play / resume at the
   saved time / from the start / the other language · watched · reset ·
   download with the VPN state · wrong episode. Letter keys work while open. */
function cmRow(a, v, icon, label, key, extra = "", attrs = "") {
  return `<button class="mi" data-a="${a}" data-v="${esc(v)}" ${attrs}>${ico(icon)}<span class="ml">${label}</span>${extra}${key ? `<kbd class="kb">${key}</kbd>` : ""}</button>`;
}
/* The menu closes with a slide down on phones (it used to vanish). */
function cmHide() {
  const m = $("#cm");
  if (m.style.display !== "block" || m.classList.contains("closing")) return;
  if (!isPhone() || reduceMotion()) { m.style.display = "none"; return; }
  m.classList.add("closing");
  cmHide.t = setTimeout(() => { m.classList.remove("closing"); m.style.display = "none"; }, 190);
}
function cmShow(m, x, y) {
  clearTimeout(cmHide.t); m.classList.remove("closing");
  if (isPhone() && !m.querySelector(".grabber")) m.insertAdjacentHTML("afterbegin", grab);
  m.style.animation = "none"; void m.offsetWidth; m.style.animation = "";
  const z = zoomGet() / 100;
  m.style.cssText += (isPhone() && ovOpen("cm", () => { cmHide(); }), "") + `;display:block;left:0;top:0`;
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.max(8, Math.min(x / z, innerWidth / z - w - 8)) + "px";
  m.style.top = Math.max(8, Math.min(y / z, innerHeight / z - h - 8)) + "px";
  m.querySelector(".mi")?.classList.add("sel");
}
function cmItems({ id, ep, files, verId, online, onlineId, other, local, dl, fix, playable = true }) {
  const vpn = S.data?.vpn, vpnTag = !vpn?.known ? "" : vpn.active ? '<span class="v ok">vpn ✓</span>' : '<span class="v warn">no vpn</span>';
  const res = resumable(ep), rows = [];
  if (playable) rows.push(cmRow("playep", id, "play", res ? `Resume at ${fmtTime(ep.position)}` : "Play", "↵"));
  if (playable && res) rows.push(cmRow("playstart", id, "undo", "Play from the start"));
  if (files.length > 1) files.forEach((fl, i) => rows.push(cmRow("playep", verId, "play", `Play ${esc(fl.label)}`, "", "", `data-ver="${i}"`)));
  if (local && online) rows.push(cmRow("playep", onlineId, "play", "Play online"));
  if (online) rows.push(cmRow("playmode", onlineId, "play", `Play ${other === "dub" ? "dubbed" : "subbed"}`, "A", "", `data-m="${other}"`));
  const mid = [cmRow("mark", id, ep.watched ? "undo" : "check", ep.watched ? "Mark as unwatched" : "Mark as watched", "W", "", `data-w="${!ep.watched}"`)];
  if (ep.position) mid.push(cmRow("reset", id, "refresh", "Reset progress"));
  const end = [];
  if (dl) end.push(cmRow("dlep", onlineId, "download", "Download", "D", vpnTag));
  if (fix) end.push(cmRow("srcfix", "", "search", "Wrong episode? Find it"));
  return [rows, mid, end].filter((g) => g.length).map((g) => g.join("")).join('<div class="sep"></div>');
}
function frCm(id, x0, y0) {
  const f = frFind(id);
  if (!f) return false;
  const { e, x } = f, m = $("#cm"), files = x.local?.files || [];
  const online = !!(x.online && S.data.stream_ready && e.online.available);
  const mode = e.online.mode || S.settings?.stream?.mode || "sub", other = mode === "dub" ? "sub" : "dub";
  const movie = e.format === "MOVIE" && e.slots.length <= 1;
  m.innerHTML = `<div class="cap">${esc(movie ? e.label : "E" + String(x.number).padStart(2, "0"))}${x.title ? " · " + esc(x.title) : ""}</div>`
    + cmItems({ id: x.id, ep: x, files, verId: x.local?.id, online, onlineId: x.online?.id, other, local: !!x.local, playable: playableSlot(e, x),
      dl: online && !x.local && !dlJob(x.online.id), fix: !!S.data.stream_ready && !!x.online });
  cmShow(m, x0, y0);
  return true;
}

function backLabel() { return S.showBack === "library" ? "Library" : S.showBack === "history" || S.showBack === "stats" ? "Journal" : "Home"; }

/* A folder with no AniList match yet: its files, and the way to match it.
   Matched folders and online episodes open their franchise page (Fran),
   unless AniList can't deliver it right now (frUnavailable). */
function Det() {
  const s = showById(S.showId), tg = pickTarget(s);
  const sea = s.seasons[S.seasonIdx] || s.seasons[0];
  const movie = s.kind === "movie";
  /* A matched show lands here only while AniList can't deliver its
     franchise page (frUnavailable): no re-match offer for it. */
  const matched = !!s.meta?.anilist_id;
  const goLabel = tg ? `${KIND_WORD[tg.kind]}${movie || tg.kind === "rewatch" ? "" : " " + epCode(tg.sea, tg.ep)}` : "";
  const goBusy = tg && epLoading(tg.ep.id);
  const flat = s.seasons.flatMap((x) => x.episodes);
  const seaAll = sea && sea.episodes.length > 0 && sea.episodes.every((e) => e.watched), showAll = flat.length > 0 && flat.every((e) => e.watched);
  const playing = isPlayingShow(s.id) ? `<span class="playing"><i class="led"></i>${S.data.now.loading ? "loading" : "playing"} · ${esc(S.data.now.label)}</span>` : "";
  const facts = [s.path_label || s.path || s.title, `${epTotal(s)} episodes`, matched ? "AniList unreachable — showing the folder" : s.kept_local ? "kept local" : "not matched yet"];
  return `<div class="sp2">${backBtn()}
  <div class="hero${s.poster ? " framed" : " bare"}"><div class="hl2">
    <div class="meta"><i class="mdot"></i><span>${esc(facts.join(" · "))}</span>${playing}</div>
    <h1>${esc(s.title)}</h1>
    <div class="cta">${tg ? `<button class="btn pri big${goBusy ? " loading" : ""}" data-a="play" data-v="${esc(tg.ep.id)}">${goBusy ? '<i class="spin"></i>' : ico("play")}<span>${goBusy ? "Loading…" : esc(goLabel)}</span>${kb("↵")}</button>` : ""}
      ${sea && sea.episodes.length ? `<button class="btn big" data-a="bulk" data-s="${S.seasonIdx}" data-w="${!seaAll}">${ico(seaAll ? "undo" : "check")}${seaAll ? "Unwatch season" : "Mark season watched"}</button>` : ""}
      ${s.seasons.length > 1 && flat.length ? `<button class="btn big" data-a="bulk" data-s="" data-w="${!showAll}">${ico(showAll ? "undo" : "check")}${showAll ? "Unwatch all" : "Mark all watched"}</button>` : ""}
      ${flat.length ? `<button class="btn big${S.sel?.show === s.id ? " on" : ""}" data-a="selmode">${ico("checkc")}${S.sel?.show === s.id ? "Done" : "Select"}</button>` : ""}
      ${s.path && flat.length ? `<button class="btn big ic" aria-label="Open the folder" title="Open the folder" data-a="folder" data-v="${esc(s.id)}">${ico("folder")}</button>` : ""}</div>
  </div>${s.poster ? `<div class="hp">${poster(s.title, s.poster)}</div>` : ""}</div>
  ${matched ? "" : `<div class="matchbox"><h2>Match this show</h2>${(autoSuggest([s]), matchUI(s))}</div>`}
  <div class="eps-head"><div class="tabs2">${s.seasons.length > 1 ? `<div class="rels" data-glide="seasons">${s.seasons.map((x, i) => `<button class="${i === S.seasonIdx ? "on" : ""}" data-a="season" data-v="${i}">${esc(x.label)}${x.episodes.length ? `<span class="n">· ${x.episodes.length}</span>` : ""}</button>`).join("")}</div>` : `<h2>${movie ? "Movie" : "Episodes"}</h2>`}</div>
    <div class="ctl">${sea && sea.episodes.length > 4 ? `<button class="arr" data-a="strip" data-d="-1" aria-label="Scroll left">${ico("chevl")}</button><button class="arr" data-a="strip" data-d="1" aria-label="Scroll right">${ico("chevr")}</button>` : ""}</div></div>
  ${sea && sea.episodes.length ? `<div class="eps strip">${sea.episodes.map((ep, i) => epTile(s, sea, ep, tg && tg.ep.id, i)).join("")}</div>` : '<p class="sub eps-note">No episodes.</p>'}
  </div>`;
}
const infoDl = (rows) => rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");

/* ---------- AniList entry panel (TODO 9, Decision 10) ---------- */
function entryFields(res) {
  const prev = S.entry && S.entry.f;
  if (res.queued && prev && !prev.local) {
    /* Queued while connected: the reply carries the local overlay. Keep the
       connected layout and fold the saved values into what was shown. */
    const e = res.entry || {}, p = res.patch || {};
    return { ...prev, status: e.status ?? prev.status, score: e.score ?? prev.score, progress: p.progress ?? prev.progress,
             repeat: e.rewatches ?? prev.repeat, started_at: "started_at" in p ? p.started_at || null : prev.started_at,
             completed_at: "completed_at" in p ? p.completed_at || null : prev.completed_at,
             favourite: e.favourite != null ? !!e.favourite : prev.favourite };
  }
  if (res.local) {
    const e = res.entry || {};
    return { status: e.status || null, score: e.score ?? null, progress: null, repeat: e.rewatches || 0,
             started_at: e.started_at || null, completed_at: e.finished_at || null, favourite: !!e.favourite, local: true, score_max: 10, media: {} };
  }
  const st = res.state || (res.entry && res.media_id ? res : {}), e = st.entry || {};
  return { status: e.status || null, score: e.score ?? null, progress: e.progress ?? 0, repeat: e.repeat ?? 0,
           started_at: e.started_at || null, completed_at: e.completed_at || null, favourite: !!st.is_favourite, local: false,
           score_format: st.score_format, score_max: st.score_max, media: st };
}
const entryHost = () => $("#entrybox") || $("#entrystats");
/* The editor (Edit entry, E): status pills, score 1–10 pills with ♥ beside
   (other AniList score formats keep a number field), progress + rewatches,
   started + finished. Every change saves as you go. */
function entryHTML(f, pending) {
  const max = f.score_max || 10, dec = (f.score_format || "").includes("DECIMAL");
  const media = f.media || {}, show = S.view === "show" ? showById(S.showId) : null, fe = S.view === "fr" ? frEntry() : null;
  const stp = (k, v, lab, cap) => `<span class="stepper"><button aria-label="Decrease ${lab}" data-a="ebump" data-k="${k}" data-d="-1">${ico("minus")}</button><b data-k="${k}">${v}${cap ? `<small> / ${cap}</small>` : ""}</b><button aria-label="Increase ${lab}" data-a="ebump" data-k="${k}" data-d="1">${ico("plus")}</button></span>`;
  const pills = `<div class="pills" role="radiogroup" aria-label="Status">${LISTS.map(([v, l]) => `<button class="${f.status === v ? "on" : ""}" role="radio" aria-checked="${f.status === v}" data-a="eset" data-k="status" data-v="${v}">${l}</button>`).join("")}</div>`;
  const heart = `<button class="heart ${f.favourite ? "on" : ""}" aria-label="Favourite" aria-pressed="${f.favourite}" data-a="efav">${ico("heart")}</button>`;
  const cur = f.score ? Math.round(f.score) : 0;
  const score = max === 10 && (!dec || !f.score || f.score === Math.round(f.score)) // 1–10 pills (decimal 10-point too, unless a .5 is set)
    ? `<div class="scrow"><div class="score" role="radiogroup" aria-label="Score">${Array.from({ length: 10 }, (_, i) => `<button class="${i + 1 < cur ? "lo" : i + 1 === cur ? "pick" : ""}" role="radio" aria-checked="${i + 1 === cur}" data-a="eset" data-k="score" data-v="${i + 1}">${i + 1}</button>`).join("")}</div><i class="vs"></i>${heart}</div>`
    : `<div class="scrow"><input type="number" aria-label="Score" min="0" max="${max}" step="${dec ? 0.1 : 1}" value="${f.score ?? ""}" placeholder="–" data-a="eset" data-k="score" style="width:96px"><span class="dim">/ ${max}</span><i class="vs"></i>${heart}</div>`;
  /* Discovery (Decision 9): a show off the lists says so and offers the one-click add. */
  const off = !f.status && !pending ? `<p class="note offlist">Not on your list yet. <button class="btn pri" data-a="eadd">${ico("plus")}Add to Planning</button></p>` : "";
  const prog = f.local ? `<b class="flat">${fe ? fe.slots.filter((x) => x.watched).length + " / " + (fe.episodes || fe.slots.length) : show ? epWatched(show) + " / " + epTotal(show) : "—"}</b>` : stp("progress", f.progress ?? 0, "progress", media.episodes || "");
  return `${off}${f.local ? '<p class="note">Saved on this device only — syncs when AniList connects.</p>' : ""}${pending ? '<p class="note"><span class="chip warn">Waiting to sync</span></p>' : ""}
  <div class="ef"><span class="cap">status</span>${pills}</div>
  <div class="ef"><span class="cap">score${cur ? ` <b class="sv">${esc(f.score)}</b><small>/${max}</small>` : ""}</span>${score}</div>
  <div class="ef two"><div><span class="cap">progress</span>${prog}</div><div><span class="cap">rewatches</span>${stp("repeat", f.repeat ?? 0, "rewatches")}</div></div>
  <div class="ef two"><div><span class="cap">started</span>${dateField("started_at", "Start date", f.started_at)}</div><div><span class="cap">finished</span>${dateField("completed_at", "Finish date", f.completed_at)}</div></div>`;
}
/* The row on the page: score · watched · status · ♥ (Decision 38). */
function entryStats(f, pending) {
  if (!f.status && !pending) return `<div class="st2 unl"><span class="k">not on your list yet</span><button class="btn pri" data-a="eadd">${ico("plus")}Add to Planning</button></div>`;
  const max = f.score_max || 10, show = S.view === "show" ? showById(S.showId) : null, fe = S.view === "fr" ? frEntry() : null;
  const total = f.media?.episodes || fe?.episodes || fe?.slots.length || (show ? epTotal(show) : 0);
  const got = f.local || f.progress == null ? (fe ? fe.slots.filter((x) => x.watched).length : show ? epWatched(show) : 0) : f.progress;
  const since = f.completed_at ? "until " + f.completed_at : f.started_at ? "since " + f.started_at : "";
  const n = (v, sm) => `<b class="v">${v}${sm ? `<small>${sm}</small>` : ""}</b>`;
  return `<div class="st2"><button data-a="edit">${n(f.score ? esc(f.score) : "–", "/" + max)}<span class="k">your score</span></button>
    <button data-a="edit">${n(got, total ? "/" + total : "")}<span class="k">watched</span></button>
    <button data-a="edit">${n(esc(statusLabel(f.status)))}<span class="k">${esc(since || (pending ? "waiting to sync" : f.local ? "on this device" : "on AniList"))}</span></button>
    <button class="fvb${f.favourite ? " on" : ""}" data-a="efav" aria-label="Favourite" aria-pressed="${f.favourite}">${n(ico("heart"))}<span class="k">${f.favourite ? "favourite" : "not a favourite"}</span></button></div>`;
}
/* Paint the editor and/or the row from fields; steppers that changed get their bump. */
function entryPaint(f, pending, prev) {
  const st = $("#entrystats");
  if (st) { st.innerHTML = entryStats(f, pending); st._own = true; }
  const box = $("#entrybox");
  if (!box) return;
  box.innerHTML = entryHTML(f, pending);
  if (prev && !reduceMotion()) for (const k of ["progress", "repeat"]) if (prev[k] !== f[k]) box.querySelector(`.stepper b[data-k="${k}"]`)?.classList.add("pop2");
}
function entryDraw(res) {
  if (!entryHost() || !S.entry) return;
  const prev = S.entry.f, f = entryFields(res);
  S.entry.f = f; S.entry.res = res;
  entryPaint(f, !!(res.pending || res.queued), prev);
  const box = $("#entrybox"); if (box) box._own = true; // filled here: an in-place page update leaves it alone
  const m = f.media || {}, next = m.next_episode;
  const air = $("#airline");
  if (air) air._own = true;
  if (air && (next || S.view !== "fr")) air.textContent = next ? (next.in_seconds > 0 ? `E${next.number} airs in ${fmtIn(next.in_seconds)}` : `E${next.number} has aired`) : "";
  /* AniList's read-only extras (Decision 10): studio, popularity, favourites — the live read has them, the cached lists don't. */
  const mf = $("#mediafacts");
  if (mf) {
    mf._own = true;
    const bits = [m.studios?.length ? ["Studio", m.studios.join(", ")] : null, m.popularity != null ? ["On lists", m.popularity.toLocaleString()] : null,
      m.favourites != null ? ["Favourites", m.favourites.toLocaleString()] : null].filter(Boolean);
    mf.innerHTML = bits.map(([k, v]) => `<span><small>${k}</small>${esc(v)}</span>`).join("");
  }
}
/* Edit entry (E): the editor as a dialog over the page, its art on top. */
function EntryDlg() {
  const fe = S.view === "fr" ? frEntry() : null, s = S.view === "show" ? showById(S.showId) : null;
  const title = fe?.title || S.fr?.page?.title || s?.title || "", art = fe?.banner || S.fr?.page?.banner || s?.banner, pst = fe?.poster || s?.poster;
  const facts = fe ? [fe.format_label, fe.year, fe.episodes ? fe.episodes + " episodes" : ""].filter(Boolean).join(" · ") : "";
  return `<div class="edh">${art ? `<i class="eda" style="background-image:${esc(cssUrl(art))}"></i>` : ""}${pst ? `<i class="edp" style="background-image:${esc(cssUrl(pst))}"></i>` : ""}
    <div class="edt"><span class="cap">your entry</span><h2 id="dt">${esc(title)}</h2>${facts ? `<span class="mono">${esc(facts)}</span>` : ""}</div></div>
  <div class="edb"><div id="entrybox"><p class="note">Loading…</p></div></div>
  <div class="actions"><span class="mono note2">saves as you go · if AniList is offline, it waits and retries</span><button class="btn pri" data-a="close">Done${kb("Esc")}</button></div>`;
}
/* The panel never sits on "Loading…" for a page it has shown before
   (TODO 61): the last answer per entry draws at once, the fresh one
   replaces it when it differs. A "stale" answer (the hub's stored read
   while it asks AniList) is asked for again shortly. */
let entryTok = 0;
const entryMemo = {};
function loadEntry(mid, tries = 0) {
  const tok = ++entryTok, box = entryHost();
  if (S.entry?.mid !== mid || !S.entry.f) {
    S.entry = { mid, f: null, res: null };
    if (entryMemo[mid]) entryDraw(entryMemo[mid]);
  } else if (box && !box._own && S.entry.res) entryDraw(S.entry.res); // a fresh page: same entry, new box
  api("/api/entry?id=" + mid).then((res) => {
    if (tok !== entryTok || !entryHost()) return;
    if (res && res.error) {
      if (S.entry?.f) return; // keep what is on screen
      entryHost().innerHTML = `<span class="chip warn">${esc(res.error)}</span>${res.auth ? ' <button class="btn" data-a="ani" style="margin-left:8px">Connect</button>' : ""}`;
      return;
    }
    entryMemo[mid] = res;
    if (res.stale && tries < 4) setTimeout(() => { if (tok === entryTok && S.entry?.mid === mid) loadEntry(mid, tries + 1); }, 900);
    if (entryPend || entryFlight) return; // the user's edits are on screen and on their way
    if (document.activeElement?.matches?.("#entrybox input")) return; // typing: don't redraw under the cursor
    if (S.entry?.f && JSON.stringify(res) === JSON.stringify(S.entry.res)) return;
    entryDraw(res);
  }).catch(() => { if (tok === entryTok && entryHost() && !S.entry?.f) entryHost().innerHTML = '<span class="chip warn">Could not reach the hub.</span>'; });
}
/* One write path for every edit. Favourite travels beside the patch — never
   instead of it — so a combined save (the completion popup) keeps both. */
function entrySave(mid, patch, fav) {
  const body = { media_id: mid };
  if (patch && Object.keys(patch).length) body.patch = patch;
  if (fav !== undefined) body.favourite = fav;
  return api("/api/entry", body);
}
/* Edits show at once; saves run one at a time behind them. Edits made while
   a save is out (or within `delay`, for stepper clicks) fold into the next
   save, so rapid +/− costs one round-trip. A failed save reloads the entry. */
let entryPend = null, entryTimer = 0, entryFlight = false;
function entryPatch(patch, fav, delay = 0) {
  const ent = S.entry;
  if (!ent?.f) return;
  const prev = ent.f;
  ent.f = { ...prev, ...(patch || {}), ...(fav !== undefined ? { favourite: fav } : {}) };
  entryPaint(ent.f, false, prev);
  if (fav !== undefined) for (const h of $$("#entrybox .heart, #entrystats .fvb")) { h.classList.add("pop1"); if (fav) burstAt(h); }
  const p = entryPend && entryPend.mid === ent.mid ? entryPend : { mid: ent.mid, patch: {}, fav: undefined };
  Object.assign(p.patch, patch || {});
  if (fav !== undefined) p.fav = fav;
  entryPend = p;
  clearTimeout(entryTimer);
  entryTimer = setTimeout(entryFlush, delay);
}
function entryFlush() {
  if (entryFlight || !entryPend) return;
  const job = entryPend, patch = job.patch;
  entryPend = null; entryFlight = true;
  entrySave(job.mid, patch, job.fav).then((res) => {
    const here = S.entry?.mid === job.mid;
    if (res && res.error) { toast(res.error); if (here) loadEntry(job.mid); return; }
    toast(res && res.queued ? "Saved — will sync when AniList is reachable" : patch.status ? `Listed: ${statusLabel(patch.status)}` : "Saved");
    if (res && res.queued) res.patch = patch;
    if (!res.queued) entryMemo[job.mid] = res; else delete entryMemo[job.mid];
    if (here && !entryPend) entryDraw(res); // newer edits are still on screen: keep them
    if (patch.status || job.fav !== undefined) refreshData();
  }).catch(() => { toast("Could not save — is the hub running?"); if (S.entry?.mid === job.mid) loadEntry(job.mid); })
    .finally(() => { entryFlight = false; if (entryPend) entryFlush(); });
}

/* Date fields: typed (YYYY-MM-DD, YYYY-MM, YYYY or DD.MM.YYYY) or picked
   from a small in-app calendar (S.cal = { k, y, m }). */
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
function parseDateIn(v) {
  const t = String(v || "").trim();
  if (!t) return "";
  let m = t.match(/^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$/), y, mo, d;
  if (m) [, y, mo, d] = m;
  else if ((m = t.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/))) [, d, mo, y] = m;
  else return null;
  if (mo && (+mo < 1 || +mo > 12)) return null;
  if (d && (+d < 1 || +d > new Date(+y, +mo, 0).getDate())) return null;
  return [y, mo && mo.padStart(2, "0"), d && d.padStart(2, "0")].filter(Boolean).join("-");
}
function dateField(k, label, v) {
  const c = S.cal?.k === k ? S.cal : null;
  return `<span class="datef"><input aria-label="${label}" value="${esc(v || "")}" placeholder="YYYY-MM-DD" autocomplete="off" spellcheck="false" data-a="eset" data-k="${k}">
    <button class="dpk" aria-label="Pick ${label.toLowerCase()}" aria-expanded="${!!c}" data-a="calopen" data-v="${k}">${ico("cal")}</button>${c ? calHTML(c, v) : ""}</span>`;
}
function calHTML(c, v) {
  const first = new Date(c.y, c.m, 1), days = new Date(c.y, c.m + 1, 0).getDate(), lead = (first.getDay() + 6) % 7;
  const iso = (d) => `${c.y}-${String(c.m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  const today = new Date(), tIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const cells = Array.from({ length: lead }, () => "<i></i>").join("") + Array.from({ length: days }, (_, i) => {
    const d = iso(i + 1);
    return `<button class="${d === v ? "on" : ""}${d === tIso ? " now" : ""}" data-a="calday" data-v="${d}">${i + 1}</button>`;
  }).join("");
  return `<div class="cal" role="dialog" aria-label="Choose a date"><div class="cal-h"><button data-a="calnav" data-v="-1" aria-label="Previous month">${ico("chevl")}</button><b>${MONTHS[c.m]} ${c.y}</b><button data-a="calnav" data-v="1" aria-label="Next month">${ico("chevr")}</button></div>
    <div class="cal-w">${["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((x) => `<span>${x}</span>`).join("")}</div><div class="cal-g">${cells}</div>
    <div class="cal-f"><button class="link-btn" data-a="calday" data-v="${tIso}">Today</button><button class="link-btn" data-a="calday" data-v="">Clear</button></div></div>`;
}
const calClose = () => { if (S.cal) { S.cal = null; if (S.entry?.f) entryPaint(S.entry.f, false); } };

/* ---------- history ---------- */
const HIST_RANGES = [
  ["24h", "Last 24 hours", 86400000], ["3d", "Last 3 days", 3 * 86400000], ["7d", "Last 7 days", 7 * 86400000],
  ["1mo", "Last month", 30 * 86400000], ["6mo", "Last 6 months", 182 * 86400000], ["1y", "Last year", 365 * 86400000], ["all", "Unlimited", null],
];
function dayLabel(d) {
  const t0 = new Date(); t0.setHours(0, 0, 0, 0);
  const d0 = new Date(d); d0.setHours(0, 0, 0, 0);
  const days = Math.round((t0 - d0) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: d.getFullYear() === t0.getFullYear() ? undefined : "numeric" });
}
/* Journal (Decision 38): History + Stats on one page — a range, your
   numbers for it as a band, the timeline by day, and on the side a month
   heat map, top genres and most watched. The all-time charts follow below. */
const J_RANGES = [["7d", "This week"], ["1mo", "Month"], ["1y", "Year"], ["all", "All time"]];
const calKey = (d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const spineOf = (s) => (s?.meta?.anilist_id && S.data?.spine?.entries?.find((e) => e.id === s.meta.anilist_id)) || null;
/* Journal charts (Decision 38): one bar look and one row look for every panel. */
function jBars(items, cls = "", val) {
  const mx = Math.max(val ? 1e-9 : 1, ...items.map((b) => b[1])), many = items.length > 12, step = items.length > 24 ? 5 : 3;
  return `<div class="jbars${many ? " many" : ""}${cls ? " " + cls : ""}">${items.map(([l, v, on], i) => `<div title="${esc(l)}: ${esc(val ? val(v) || "0" : String(v))}">${val && !many ? `<em class="${on ? "on" : ""}">${esc(val(v))}</em>` : ""}<i style="--i:${i};--h:${v / mx || 0}" class="${on ? "on" : ""}${v ? "" : " z"}"></i><span class="${on ? "on" : ""}">${many && i % step && !on ? "" : esc(l)}</span></div>`).join("")}</div>`;
}
const jRows = (items, fmt = String) => { const mx = Math.max(1, ...items.map((x) => x[1]));
  return items.map(([l, n], i) => `<div class="r"><span>${esc(l)}</span><span class="bar"><i style="--i:${i};width:${(n / mx) * 100}%"></i></span><span class="c">${esc(fmt(n))}</span></div>`).join(""); };
/* The Journal (Elias, 2026-10-24): built on your AniList activity when
   connected — what you watched anywhere, and when — else on this device's
   watch history. Both become the same events. */
const AL_KIND = { "watched episode": "watch", "rewatched episode": "watch", completed: "finish", rewatched: "finish", "plans to watch": "plan", "paused watching": "pause", dropped: "drop" };
const watchKind = (k) => k === "watch" || k === "finish";
let jrBusy = false, jrTries = 0;
function loadJournal() {
  if (jrBusy || !aniMode()) return;
  jrBusy = true;
  api("/api/journal").then((r) => {
    jrBusy = false; S.jr = { ...r, at: Date.now() };
    const more = r.state === "loading" || r.syncing || r.complete === false;
    if (more && jrTries++ < 40) setTimeout(() => { if (S.view === "history" || S.view === "stats") loadJournal(); }, 3000);
    else jrTries = 0;
    if (S.view === "history" || S.view === "stats") render(true);
  }).catch(() => { jrBusy = false; });
}
function jEvents() {
  if (aniMode()) {
    if (!S.jr || Date.now() - S.jr.at > 120e3) loadJournal();
    if (S.jr?.state !== "ready") return null;
    return S.jr.items.map((a) => {
      const m = S.jr.media[a.mid] || {}, sp = S.data.spine?.entries?.find((e) => e.id === a.mid), kind = AL_KIND[a.status] || "other";
      /* only what the activity says (Elias: never estimate): episodes from "watched episode 10 - 13"; a "completed" names no episode */
      const eps = kind === "watch" && a.from != null ? a.to - a.from + 1 : 0;
      return { at: new Date(a.at * 1000), key: "m" + a.mid, fkey: sp ? franchiseKey(sp) : "e" + a.mid, mid: a.mid, title: m.title || sp?.title || "", kind, eps,
        from: a.from, to: a.to, genres: m.genres?.length ? m.genres : sp?.genres || [], art: m.banner || m.poster || sp?.banner,
        poster: m.poster || sp?.poster, open: ["recopen", a.mid], score: sp?.score, total: m.episodes, rewatch: a.status?.startsWith("rewatch") };
    });
  }
  return (S.data.history || []).map((ep) => ({ ep, ctx: findEpContext(ep.id) })).filter((x) => x.ctx && x.ep.last_played).map(({ ep, ctx }) => {
    const all = ctx.s.seasons.flatMap((x) => x.episodes), fin = ep.watched && all[all.length - 1]?.id === ep.id && all.every((x) => x.watched), sp = spineOf(ctx.s);
    return { at: parseStamp(ep.last_played), key: "s" + ctx.s.id, fkey: sp ? franchiseKey(sp) : ctx.s.id, mid: sp?.id, title: ctx.s.title, kind: fin ? "finish" : "watch", eps: 1,
      code: epCode(ctx.sea, ep), name: epTitle(ctx.s, ctx.sea, ep), genres: sp?.genres || [],
      art: homeThumb(ctx.sea, ep) || ctx.s.banner || ctx.s.poster, poster: ctx.s.poster, open: ["open", ctx.s.id], score: sp?.score, total: all.length,
      stopped: !ep.watched && ep.position ? ep.position : 0, online: streamEp(ep) };
  }).sort((a, b) => b.at - a.at);
}
const weekStart = () => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d; };
const jCutoff = () => { if (S.histRange === "7d") return weekStart().getTime(); const r = HIST_RANGES.find((x) => x[0] === S.histRange); return r?.[2] ? Date.now() - r[2] : 0; };
function streaksOf(dates) {
  const days = new Set(dates.map(dayKey));
  let cur = 0, best = 0, run = 0;
  const d = new Date(); d.setHours(12, 0, 0, 0);
  if (!days.has(dayKey(d))) d.setDate(d.getDate() - 1); // today not watched yet: the streak runs to yesterday
  while (days.has(dayKey(d))) { cur++; d.setDate(d.getDate() - 1); }
  const sorted = [...days].map((k) => { const [y, m, dd] = k.split("-").map(Number); return new Date(y, m, dd).getTime(); }).sort((a, b) => a - b);
  for (let i = 0; i < sorted.length; i++) { run = i && Math.round((sorted[i] - sorted[i - 1]) / 864e5) === 1 ? run + 1 : 1; best = Math.max(best, run); }
  return { cur, best };
}
/* The range's top show: the Journal's background. */
function jTop() {
  const ev = jEvents();
  if (!ev) return null;
  const cut = jCutoff(), n = new Map();
  for (const e of ev) if (e.at >= cut && e.eps) { const g = n.get(e.fkey) || { e, n: 0 }; g.n += e.eps; n.set(e.fkey, g); }
  return [...n.values()].sort((a, b) => b.n - a.n)[0]?.e || null;
}
function Hist() {
  if (!J_RANGES.some(([k]) => k === S.histRange)) S.histRange = "7d";
  const all = jEvents(), al = aniMode();
  const tabs = `<div class="jrange"><div class="tabs" data-glide="jrange">${J_RANGES.map(([k, l]) => `<button class="${k === S.histRange ? "on" : ""}" data-a="hrange" data-v="${k}">${l}</button>`).join("")}</div></div>`;
  const head = `<div class="hello"><h1 class="hd">Journal</h1>${tabs}</div>`;
  if (!all) return `<div class="jr2">${head}<div class="jcard"><p class="empty"><i class="spin"></i> Reading your AniList activity…</p></div></div>`;
  const cut = jCutoff(), ev = all.filter((e) => e.at >= cut), seen = ev.filter((e) => watchKind(e.kind));
  const eps = seen.reduce((a, e) => a + e.eps, 0);
  const { cur, best } = streaksOf(all.filter((e) => watchKind(e.kind)).map((e) => e.at));
  /* episodes per day (this week Mon–Sun, the last 30 days), per month for a year, per year for all time */
  const now = new Date(), bars = [], today = dayKey(now);
  const sum = (from, to) => seen.reduce((a, e) => (e.at >= from && e.at < to ? a + e.eps : a), 0);
  if (S.histRange === "7d") {
    const w = weekStart();
    for (let i = 0; i < 7; i++) { const d = new Date(w.getFullYear(), w.getMonth(), w.getDate() + i), on = dayKey(d) === today; bars.push([on ? "today" : DAYS[i], sum(d, new Date(d.getTime() + 864e5)), on]); }
  } else if (S.histRange === "1mo") {
    for (let i = 29; i >= 0; i--) { const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i); bars.push([i ? String(d.getDate()) : "today", sum(d, new Date(d.getTime() + 864e5)), !i]); }
  } else if (S.histRange === "1y") {
    for (let i = 11; i >= 0; i--) { const d = new Date(now.getFullYear(), now.getMonth() - i, 1); bars.push([d.toLocaleDateString(undefined, { month: "short" }), sum(d, new Date(d.getFullYear(), d.getMonth() + 1, 1)), !i]); }
  } else {
    const y0 = all.length ? Math.min(...all.map((e) => e.at.getFullYear())) : now.getFullYear();
    for (let y = y0; y <= now.getFullYear(); y++) bars.push([String(y), sum(new Date(y, 0, 1), new Date(y + 1, 0, 1)), y === now.getFullYear()]);
  }
  /* An AniList journal first (Elias): the big numbers are your AniList totals; the range lives in the chart. */
  const T = jTotals(), rl = (J_RANGES.find(([k]) => k === S.histRange) || [0, ""])[1].toLowerCase();
  const band = `<div class="jband"><div class="n"><b class="v">${T.hours.toLocaleString()}<small>h</small></b><span class="k">watched</span></div>
    <div class="n"><b class="v">${T.eps.toLocaleString()}</b><span class="k">episodes</span></div>
    <div class="n"><b class="v">${T.done}</b><span class="k">completed</span></div>
    <div class="n"><b class="v">${T.mean}</b><span class="k">mean score</span></div>
    <div class="n"><b class="v">${cur}<small>day${cur === 1 ? "" : "s"}</small></b><span class="k">streak · <em>best ${best}</em></span></div>
    <div class="jchart" title="${esc(rl)}: ${eps} episode${eps === 1 ? "" : "s"}">${jBars(bars, "days")}</div></div>`;
  /* history: one row per show per day (its episodes merged); a finished show stands out */
  const groups = [];
  for (const e of ev.slice(0, S.histMax)) {
    const key = dayLabel(e.at), g = groups[groups.length - 1];
    if (e.kind === "watch" && g && g.kind === "watch" && g.day === key && g.key === e.key) { g.more.push(e); continue; }
    groups.push({ ...e, day: key, more: [] });
  }
  const tm = (x) => x.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const epc = (n) => "E" + String(n).padStart(2, "0");
  let last = null, out = "";
  for (const [idx, g] of groups.entries()) {
    if (g.day !== last) { out += `<div class="dh"><b>${esc(g.day)}</b>${g.day === "Today" || g.day === "Yesterday" ? `<span>${esc(g.at.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }))}</span>` : ""}<i></i></div>`; last = g.day; }
    const oldest = g.more.length ? g.more[g.more.length - 1] : null, t0 = oldest ? tm(oldest.at) : "", when = t0 && t0 !== tm(g.at) ? `${t0}–${tm(g.at)}` : tm(g.at);
    const lo = oldest ? (oldest.from ?? oldest.code) : g.from ?? g.code, hi = g.to ?? g.code, n = g.eps + g.more.reduce((a, x) => a + x.eps, 0);
    const span = typeof lo === "number" ? (lo === hi ? epc(lo) : `${epc(lo)} → ${epc(hi)}`) : lo === hi ? esc(lo) : `${esc(lo)} → ${esc(hi)}`;
    const attrs = `style="--i:${Math.min(idx, 12)}" data-a="${g.open[0]}" data-v="${esc(String(g.open[1]))}"`;
    if (g.kind === "finish") out += `<button class="ev fin" ${attrs}><span class="th p" ${g.poster ? `style="background-image:${esc(cssUrl(g.poster))}"` : ""}></span><span class="tx"><b><span class="nm">${g.rewatch ? "Rewatched" : "Finished"} ${esc(g.title)}</span>${g.score ? `<span class="badge">★ ${esc(g.score)}</span>` : ""}</b><small>${g.total ? `${g.total} episode${g.total === 1 ? "" : "s"}` : "done"}</small></span><span class="t">${esc(when)}</span></button>`;
    else if (g.kind === "watch") { const what = n > 1 ? `${span} · ${n} episodes` : `${span}${g.name ? " " + esc(g.name) : ""}${g.stopped ? " · stopped at " + fmtTime(g.stopped) : ""}${g.online ? " · online" : ""}`;
      out += `<button class="ev" ${attrs}><span class="th" ${g.art ? `style="background-image:${esc(cssUrl(g.art))}"` : ""}></span><span class="tx"><b><span class="nm">${esc(g.title)}</span></b><small>${g.rewatch ? "rewatch · " : ""}${what}</small></span><span class="t">${esc(when)}</span></button>`; }
    else out += `<button class="ev q" ${attrs}><span class="th p" ${g.poster ? `style="background-image:${esc(cssUrl(g.poster))}"` : ""}></span><span class="tx"><b><span class="nm">${esc(g.title)}</span></b><small>${{ plan: "added to Planning", pause: "paused", drop: "dropped" }[g.kind] || "list change"}</small></span><span class="t">${esc(when)}</span></button>`;
  }
  /* side: most watched, top genres, this month */
  const fr = new Map(), gen = {};
  for (const e of seen) { const f = fr.get(e.fkey) || { e, n: 0 }; f.n += e.eps; fr.set(e.fkey, f); for (const g of e.genres) gen[g] = (gen[g] || 0) + e.eps; }
  const top = [...fr.values()].filter((x) => x.n).sort((a, b) => b.n - a.n).slice(0, 3);
  const most = top.length ? `<div class="jcard"><div class="sh"><b>Most watched</b></div><div class="tops">${top.map(({ e, n }) => `<button data-a="${e.open[0]}" data-v="${esc(String(e.open[1]))}"><span class="cv" ${e.poster ? `style="background-image:${esc(cssUrl(e.poster))}"` : ""}><span>${n} ep</span></span><b>${esc(e.title)}</b></button>`).join("")}</div></div>` : "";
  const gtop = Object.entries(gen).filter(([, n]) => n).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const genres = gtop.length ? `<div class="jcard gen"><div class="sh"><b>Top genres</b><span>${esc((J_RANGES.find(([k]) => k === S.histRange) || [0, ""])[1].toLowerCase())}</span></div>${jRows(gtop)}</div>` : "";
  const first = new Date(now.getFullYear(), now.getMonth(), 1), lead = (first.getDay() + 6) % 7, dim = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate(), perDay = {};
  for (const e of all) if (watchKind(e.kind) && e.at >= first) perDay[e.at.getDate()] = (perDay[e.at.getDate()] || 0) + e.eps;
  const hmax = Math.max(1, ...Object.values(perDay)), lvl = (n) => !n ? "" : n / hmax > 0.66 ? "l3" : n / hmax > 0.33 ? "l2" : "l1";
  const heat = `<div class="jcard"><div class="sh"><b>${esc(now.toLocaleDateString(undefined, { month: "long" }))}</b><span>episodes per day</span></div><div class="heat">${["M", "T", "W", "T", "F", "S", "S"].map((x) => `<span class="h">${x}</span>`).join("")}${"<i class=\"pad\"></i>".repeat(lead)}${Array.from({ length: dim }, (_, i) => `<i class="${lvl(perDay[i + 1])}${i + 1 === now.getDate() ? " today" : ""}" title="${i + 1}: ${perDay[i + 1] || 0}"></i>`).join("")}</div>
    <div class="legend">less <i></i><i class="l1"></i><i class="l2"></i><i class="l3"></i> more</div></div>`;
  const st = Stats(true); // all time, in the same two columns (no gaps: each column stacks on its own)
  const src = al ? `from AniList${S.jr && !S.jr.complete ? " · reading older activity…" : ""}` : "on this device";
  return `<div class="jr2">${head}
    ${band}
    <div class="jcols"><div class="jmain"><div class="jcard tl"><div class="sh"><b>History</b><span>${src}</span></div><div class="tlg">${out || `<p class="empty">Nothing watched ${S.histRange === "all" ? "yet" : "in this range"}.</p>`}</div>
      ${ev.length > S.histMax ? `<div class="more"><button class="btn sm" data-a="histmore">Show earlier (${ev.length - S.histMax} left)</button></div>` : ""}</div>
      ${st.main}</div>
      <div class="jside">${most}${genres}${heat}${st.side}</div></div></div>`;
}

/* ---------- stats ---------- */
function statsEntries() {
  if (aniMode() && S.alData?.entries) return S.alData.entries;
  return S.data.spine?.entries || [];
}
/* Your totals (AniList's when connected): the Journal's big numbers. */
function jTotals() {
  const d = S.data, E = statsEntries(), u = aniMode() ? S.alData?.user : null;
  const minutes = (u ? u.minutes_watched : d.stats.minutes) || 0, eps = (u ? u.episodes_watched : d.stats.episodes_watched) || 0;
  const scored = E.filter((e) => e.score > 0);
  return { hours: Math.round(minutes / 60), eps, done: E.filter((e) => e.status === "COMPLETED").length,
    mean: scored.length ? (scored.reduce((a, e) => a + e.score, 0) / scored.length).toFixed(1) : "—" };
}
function Stats(embed) {
  if (!embed) return Hist();
  const E = statsEntries(), u = aniMode() ? S.alData?.user : null;
  const scored = E.filter((e) => e.score > 0), done = E.filter((e) => e.status === "COMPLETED").length;
  /* from the list scores (1–10), not AniList's meanScore: that one comes in the account's own score format (showed "1.8") */
  const mean = scored.length ? (scored.reduce((a, e) => a + e.score, 0) / scored.length).toFixed(1) : "—";
  /* episodes per month: AniList activity when connected, else this device's history */
  const MN = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" "), month = {};
  for (const e of jEvents() || []) if (e.eps) { // AniList activity when connected, else this device
    const k = e.at.getFullYear() * 12 + e.at.getMonth();
    month[k] = (month[k] || 0) + e.eps;
  }
  const now = new Date(), nk = now.getFullYear() * 12 + now.getMonth();
  const months = Array.from({ length: 12 }, (_, i) => { const k = nk - 11 + i; return [`${MN[((k % 12) + 12) % 12]} ${String(Math.floor(k / 12)).slice(2)}`, Math.round((month[k] || 0) * 10) / 10]; });
  const byList = LISTS.map(([k, l]) => [l, E.filter((e) => e.status === k).length]).filter((x) => x[1]);
  const fmts = {};
  for (const e of E) { const k = FMT[e.format] || e.format || "Unknown"; fmts[k] = (fmts[k] || 0) + 1; }
  const years = {};
  for (const e of E) if (e.year) years[e.year] = (years[e.year] || 0) + 1;
  const ys = Object.keys(years).map(Number), yItems = ys.length ? Array.from({ length: Math.max(...ys) - Math.min(...ys) + 1 }, (_, i) => [String(Math.min(...ys) + i), years[Math.min(...ys) + i] || 0]) : [];
  const dist = Array.from({ length: 10 }, (_, i) => [String(i + 1), scored.filter((e) => Math.round(e.score) === i + 1).length]);
  const note = aniMode() && !u ? '<p class="stat-note">Loading your full AniList totals…</p>' : "";
  const fmts2 = Object.entries(fmts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const curY = new Date().getFullYear();
  const card = (cls, head, body) => `<div class="jcard${cls ? " " + cls : ""}"><div class="sh">${head}</div>${body}</div>`;
  return {
    main: note + `<div class="jpair">${card("", `<b>Episodes per month</b><span>${aniMode() ? "from AniList activity" : "on this device"}</span>`, jBars(months.map(([l, v], i) => [l, v, i === months.length - 1]), "full vals", (v) => (v ? String(v) : "")))}`
      + `${card("", "<b>Release years</b>", yItems.length ? jBars(yItems.map(([l, v]) => [l, v, +l === curY]), "full") : '<p class="empty">No year data yet.</p>')}</div>`
      + `<div class="jpair">${card("", `<b>Scores you give</b>${scored.length ? `<span>mean ${mean}</span>` : ""}`, scored.length ? jBars(dist.map(([l, v]) => [l, v, false]), "full") : '<p class="empty">No scores yet.</p>')}`
      + `${card("gen", "<b>Formats</b>", fmts2.length ? jRows(fmts2) : '<p class="empty">No data yet.</p>')}</div>`,
    side: card("gen", "<b>Your lists</b>", byList.length ? jRows(byList) : '<p class="empty">No list data yet.</p>'),
  };
}
function loadAlData() {
  if (!aniMode() || S.alData || loadAlData.busy) return;
  loadAlData.busy = true;
  api("/api/anilist").then((res) => {
    if (res && res.user) { S.alData = res; if (S.view === "stats" || S.view === "history") render(true); }
  }).catch(() => {}).finally(() => { loadAlData.busy = false; });
}

/* ---------- settings ---------- */
const srow = (t, desc, ctl) => `<div class="srow"><div><b>${t}</b><p>${desc}</p></div><div class="ctl">${ctl}</div></div>`;
const tgl = (key, label, on, off) => `<button class="switch" role="switch" aria-checked="${!!on}" aria-label="${esc(label)}" data-a="adv" data-v="${key}" ${off ? "disabled" : ""}></button>`;
const toolRow = (name, path, hint) => srow(name, path ? `<code>${esc(path)}</code>` : esc(hint), `<span class="chip"><i class="dot ${path ? "ok" : ""}"></i>${path ? "found" : "not found"}</span>`);
function Sett() {
  const s = S.settings, ani = S.ani || {};
  if (!s) { loadSettings().then(() => S.view === "settings" && render(true)); return '<div class="wrap page"><p class="empty">Could not reach the hub — retrying…</p></div>'; }
  const stateTxt = { ok: "Watching this folder.", unconfigured: "No library folder is configured yet.", missing: "The folder is not accessible right now.", partial: "Some folders could not be read — part of the library may be missing." };
  const changed = s.scan?.root === "changed", conn = !!ani.connected, off = !conn;
  /* Settings (Decision 38): one section at a time; every setting is also in the search. */
  const tabs = SET_TABS.filter(([k]) => k !== "phone" || !s.phone); // a paired phone can't change the remote (Decision 43)
  if (isPhone() && !S.setOpen) return settingsList(tabs, s);
  const tab = tabs.some(([k]) => k === S.setTab) ? S.setTab : "appearance";
  const sec = {
    appearance: `<div class="group"><h3>Appearance</h3>${srow("Accent colour", "Follow the show on screen, or keep one colour everywhere.", `<span class="looks" role="radiogroup" aria-label="Accent colour"><button class="follow${accentGet() === "follow" ? " on" : ""}" role="radio" aria-checked="${accentGet() === "follow"}" data-a="look" data-k="accent" data-v="follow">Follow the show</button>${ACCENTS.map((c) => `<button class="swatch${accentGet() === c ? " on" : ""}" role="radio" aria-checked="${accentGet() === c}" aria-label="Accent ${c}" style="background:${c}" data-a="look" data-k="accent" data-v="${c}"></button>`).join("")}</span>`)}${srow("Background art", "Full: the show's picture behind the page. Soft: only a dim haze of its colours. Off: plain dark.", lookSeg("art", [["full", "Full"], ["soft", "Soft"], ["off", "Off"]]))}${upscaleRow(s)}${srow("Top bar", "How search and settings sit in the top bar. Island: the whole bar is one floating pill.", lookSeg("bar", [["classic", "Classic"], ["tabs", "Tabs"], ["line", "Line"], ["capsule", "Capsule"], ["island", "Island"]]))}${srow("Film grain", "A light texture over the background.", lookSeg("grain", [["on", "On"], ["off", "Off"]]))}${srow("Motion", "Calm keeps the small animations but drops drifting and pulsing. Off turns them all off.", lookSeg("motion", [["full", "Full"], ["calm", "Calm"], ["off", "Off"]]))}${srow("Zoom", "Scales the whole interface on this device.", `<span class="zrow"><button class="btn" data-a="zoomout" aria-label="Zoom out">−</button><span class="zval" id="zoomval">${zoomGet()}%</span><button class="btn" data-a="zoomin" aria-label="Zoom in">+</button></span>`)}</div>`,
    library: `<div class="group"><h3>Library</h3>${srow("Library folder", `${esc(stateTxt[s.scan?.state] || "")} ${s.scan?.state === "unconfigured" ? "Choose the folder that holds your anime." : "Browse to pick another one."}${changed ? " The configured folder differs from the indexed one — connect it to pick your shows back up." : ""}`,
      `<div class="path">${libPath("settings", s)}${dirBtn("settings")}<button class="btn" data-a="rescan">Rescan</button>${changed ? '<button class="btn pri" data-a="adopt">Connect folder</button>' : ""}</div>`)}${DirPick("settings")}${lengthRow(s)}</div>${s.library_root ? extraGroup(s) : ""}<div class="group"><h3>Detected tools</h3>${toolRow("mpv", s.tools?.mpv, "Required for playback — install mpv.")}${toolRow("ani-cli", s.tools?.["ani-cli"] && s.tools["ani-cli"] + (s.stream?.version ? " · " + s.stream.version : ""), "Optional — lets you watch and download episodes you don't have.")}${toolRow("upscayl", s.tools?.upscayl, "Optional — makes banners sharper (Settings → Appearance).")}</div>`,
    playback: `${skipGroup(s)}`,
    mpv: mpvGroup(s),
    online: `${streamGroup(s)}`,
    phone: s.phone ? "" : phoneGroup(),
    anilist: `<div class="group"><h3>AniList</h3>${srow("Account", conn ? `Your AniList lists are the sections of your library.${ani.expires_at ? " Token expires " + new Date(ani.expires_at * 1000).toLocaleDateString() + "." : ""}` : ani.expired ? "The saved token has expired — reconnect." : "Not connected. Everything runs on local watch state.",
      conn ? `<span><i class="avatar">${esc((ani.user || "?")[0].toUpperCase())}</i>${esc(ani.user || "")}</span><button class="btn" data-a="ani">Manage</button>` : '<button class="btn pri" data-a="ani">Connect AniList</button>')}
  <div class="adv2${off ? " dis" : ""}">${off ? '<p class="sub">Connect AniList to use these.</p>' : ""}
    ${srow("Track episodes", "After playback marks an episode watched, publish the highest watched episode to AniList.", tgl("auto_progress_sync", "Track episodes", s.auto_progress_sync))}
    ${srow("Watched marks from AniList", "Episodes up to your AniList progress show as watched, the rest as unwatched. Off: the marks saved on this device. Switching back loses nothing.", tgl("anilist_watched", "Watched marks from AniList", s.anilist_watched))}
    ${srow("Add shows automatically", "A first watch of a matched show that isn't on your lists adds it to Watching. Off by default.", tgl("auto_add_watch", "Add shows automatically", s.auto_add_watch))}
    ${srow(`Sync ratings<span class="soon">not built yet</span>`, "Keep scores in step between AniList and this device.", tgl("ratings", "Sync ratings", false, true))}</div></div>`,
    keys: `<div class="group"><h3>Keys</h3><p class="sub">Press <kbd class="kb">?</kbd> anywhere for this list.</p><div class="keys big">${KEYS.map(([n, k]) => `<span>${esc(n)}</span><kbd class="kb">${esc(k)}</kbd>`).join("")}</div></div>`,
    data: `<div class="group"><h3>Data</h3>${srow("Config file", `<code>${esc(s.config_path)}</code>`, "")}${srow("Data folder", `<code>${esc(s.data_dir)}</code>`, "")}${srow("Server", `Port ${esc(String(s.port))} · window mode “${esc(s.window)}”.`, "")}</div><div class="group"><h3>Setup</h3>${srow("First-run tour", "Walk through the library folder and AniList again.", '<button class="btn" data-a="tour">Replay tour</button>')}</div>`,
  };
  return `<div class="set2">${isPhone() ? `<button class="backbtn sback" data-a="setback">${ico("back")}Settings</button>` : ""}<div class="hello"><h1 class="hd">${isPhone() ? esc((tabs.find(([k]) => k === tab) || [0, "Settings"])[1]) : "Settings"}</h1><div class="sum"><span>saves on its own</span><i class="sep"></i><span>${esc(s.config_path || "")}</span></div></div>
  <div class="setg"><nav class="snav">${tabs.map(([k, l]) => `<button class="${k === tab ? "on" : ""}" data-a="settab" data-v="${k}">${l}</button>`).join("")}<p class="hint">every setting is also in the search <kbd class="kb">/</kbd> — try “vpn” or “motion”</p></nav>
  <div class="settings scol" data-sec="${tab}">${sec[tab]}</div></div></div>`;
}
/* Episode lengths read from the files (ffprobe, else mpv), once per file.
   While it works, Settings re-reads its progress every few seconds. */
function lengthRow(s) {
  const l = s.lengths || {};
  const txt = {
    off: "Read how long each episode is from its file, so episodes show their length before you play them.",
    missing: "Install ffprobe (part of FFmpeg) or mpv to use this.",
    idle: `On. New files are measured as they arrive${l.tool ? ` (with ${esc(l.tool)})` : ""}.`,
    working: `Working: ${l.done || 0} done, ${l.left || 0} left. Pauses while an episode plays.`,
  }[l.state] || "";
  if (l.state === "working" && !lengthRow.t) lengthRow.t = setTimeout(() => {
    lengthRow.t = 0;
    if (S.view === "settings") loadSettings().then(() => S.view === "settings" && render(true));
  }, 4000);
  return srow("Measure episode lengths", txt, tgl("measure_lengths", "Measure episode lengths", s.measure_lengths));
}
/* Banner upscaling (opt-in): Upscayl redraws banners on the graphics card.
   While it works, Settings re-reads its progress every few seconds. */
function upscaleRow(s) {
  const u = s.upscale || {}, missing = !u.found;
  const txt = {
    off: "Upscayl redraws show banners 4× bigger on your graphics card.",
    missing: "Install Upscayl to use this. It redraws show banners 4× bigger on your graphics card.",
    idle: `On. New banners are redrawn as they arrive.${u.done ? ` ${u.done} done.` : ""}`,
    working: `Working: ${u.done || 0} done, ${u.left || 0} left. Pauses while an episode plays.`,
    broken: `<span class="chip warn">Stopped</span> Upscayl gave blank pictures, so your banners stay as they are. Update the graphics driver${u.flatpak ? " (for the Flatpak: run <code>flatpak update</code>)" : ""}, then switch this off and on.`,
  }[u.state] || "";
  if (u.state === "working" && !upscaleRow.t) upscaleRow.t = setTimeout(() => {
    upscaleRow.t = 0;
    if (S.view === "settings") loadSettings().then(() => S.view === "settings" && render(true));
  }, 4000);
  const look = [["natural", "Natural"], ["digital", "Digital art"]].map(([v, l]) => `<button class="${u.style === v ? "on" : ""}" aria-pressed="${u.style === v}" data-a="spref" data-k="upscale_style" data-v="${v}" ${u.on ? "" : "disabled"}>${l}</button>`).join("");
  return srow("Sharper banners", txt, tgl("upscale_banners", "Sharper banners", u.on, missing && !u.on))
    + srow("Banner look", "Natural keeps soft shading as it is. Digital art draws crisper lines and takes about 3× longer (Upscayl's Digital Art with TTA Mode).", `<span class="seg" role="group" aria-label="Banner look">${look}</span>`);
}
/* A segmented control for one Appearance preference. */
function lookSeg(k, opts) {
  const cur = k === "grain" ? (grainGet() ? "on" : "off") : lookGet(k);
  return `<span class="seg" role="group" aria-label="${k}">${opts.map(([v, l]) => `<button class="${cur === v ? "on" : ""}" aria-pressed="${cur === v}" data-a="look" data-k="${k}" data-v="${v}">${l}</button>`).join("")}</span>`;
}
/* Opening/ending skipping in mpv (Decision 39). AniSkip is opt-in: it would learn what you watch. */
const SKIPMODES = [["off", "Off"], ["prompt", "Ask"], ["auto", "Auto"]];
/* mpv's own section (Decision 45): each option is on, off, or left to your mpv.conf. */
const MPVMODES = [["on", "On"], ["off", "Off"], ["own", "My config"]];
function mpvGroup(s) {
  const m = s.mpv || {};
  const seg = (k, label) => `<span class="seg" role="group" aria-label="${label}">${MPVMODES.map(([v, l]) => `<button class="${m[k] === v ? "on" : ""}" aria-pressed="${m[k] === v}" data-a="spref" data-k="mpv_${k}" data-v="${v}">${l}</button>`).join("")}</span>`;
  return `<div class="group"><h3>mpv</h3><p class="sub">Your own mpv.conf always loads. The hub only overrides what's set to On or Off here.</p>
    ${srow("Fullscreen", "How episodes start.", seg("fullscreen", "Fullscreen"))}
    ${srow("Opening and ending on the timeline", "Marks them on mpv's seek bar and names them under the mouse. Needs skipping on (Playback).", tgl("mpv_marks", "Opening and ending on the timeline", m.marks !== false))}</div>`;
}
function skipGroup(s) {
  const sk = s.skip || {};
  const seg = (k, label) => `<span class="seg" role="group" aria-label="${label}">${SKIPMODES.map(([m, l]) => `<button class="${sk[k] === m ? "on" : ""}" aria-pressed="${sk[k] === m}" data-a="spref" data-k="skip_${k}" data-v="${m}">${l}</button>`).join("")}</span>`;
  return `<div class="group"><h3>Playback</h3>
    ${srow("Skip openings", "Ask: shows a skip button. Auto: skips right away.", seg("op", "Skip openings"))}
    ${srow("Skip endings", "Anything after the ending still plays.", seg("ed", "Skip endings"))}
    ${srow("Times from AniSkip", "For files without chapters and online episodes. AniSkip sees which episode you start and your IP address.", tgl("aniskip", "Times from AniSkip", s.aniskip === true))}
    ${srow("Ask without VPN", "Asks before AniSkip is used without a VPN.", tgl("aniskip_vpn_warn", "Ask without VPN", s.aniskip_vpn_warn !== false, s.aniskip !== true))}
    ${srow("Remember audio and subtitles", "Switch tracks in mpv once, and the show's next episodes start with the same audio and subtitles.", tgl("remember_tracks", "Remember audio and subtitles", s.remember_tracks !== false))}</div>`;
}
/* Phone remote (Decision 43): a second door on the home Wi-Fi, off by
   default; phones pair with the QR code (or the address and code). While a
   code is up, Settings re-reads it every few seconds to see the phone arrive. */
function phoneGroup() {
  const r = S.remote;
  if (!r) { api("/api/remote").then((x) => { S.remote = x; if (S.view === "settings") render(true); }).catch(() => {}); return '<div class="group"><h3>Phone remote</h3><p class="sub">Loading…</p></div>'; }
  const when = (t) => t ? new Date(t.replace(" ", "T") + (/[zZ+]/.test(t) ? "" : "Z")).toLocaleDateString() : "";
  const code = r.code, mins = code ? Math.max(1, Math.ceil(code.left / 60)) : 0;
  if (code && !phoneGroup.t) phoneGroup.t = setTimeout(phoneWatch, 3000);
  const pair = !r.on ? "" : r.error ? srow("Pair a phone", `<span class="chip warn">Not reachable</span> ${esc(r.error)}`, "")
    : !r.address ? srow("Pair a phone", "This PC isn't on a network right now.", "")
    : code ? `<div class="pairbox"><div class="qr" aria-label="QR code with the pairing link">${code.qr}</div><div class="pairtxt"><b>Scan this with the phone's camera</b>
        <p>Or open <code>${esc(r.address)}</code> on the phone and type</p><p class="pcode mono">${esc(code.code.replace(/(\d{3})(\d{3})/, "$1 $2"))}</p>
        <p class="sub">Works for ${mins} more minute${mins === 1 ? "" : "s"}, for one phone.</p><button class="btn" data-a="rmtcode">New code</button></div></div>`
    : srow("Pair a phone", "Shows a QR code to scan with the phone.", '<button class="btn pri" data-a="rmtcode">Pair a phone</button>');
  const devs = r.devices.length ? r.devices.map((d) => srow(esc(d.name), `Paired ${esc(when(d.created))} · last used ${esc(when(d.last_seen))}`, `<button class="btn" data-a="rmtdel" data-v="${d.id}">Remove</button>`)).join("")
    : r.on ? '<p class="sub">No phones paired yet.</p>' : "";
  return `<div class="group"><h3>Phone remote</h3>
    ${srow("Phone remote", "Pause, skip and pick tracks from your phone while an episode plays here. Phone and PC need the same Wi-Fi. No app to install.", `<button class="switch" role="switch" aria-checked="${r.on}" aria-label="Phone remote" data-a="rmton"></button>`)}
    ${pair}</div>${r.on || r.devices.length ? `<div class="group"><h3>Paired phones</h3>${devs}</div>` : ""}`;
}
function phoneWatch() {
  phoneGroup.t = 0;
  if (S.view !== "settings" || S.setTab !== "phone") return;
  const had = S.remote?.devices.length || 0;
  api("/api/remote").then((x) => {
    S.remote = x;
    if (x.devices.length > had) toast(`${x.devices[x.devices.length - 1].name} paired`);
    if (S.view === "settings" && S.setTab === "phone") render(true);
  }).catch(() => {});
}
async function remoteSet(body) {
  try {
    const r = await api("/api/remote", body);
    if (r.error) toast(r.error); else S.remote = r;
  } catch { toast("Could not reach the hub."); }
  render(true);
}
const QLABEL = { best: "Best available", worst: "Smallest" };
function streamGroup(s) {
  const st = s.stream || {}, off = !s.tools?.["ani-cli"];
  const bad = !off && st.check?.ok === false; // anicli_check: this ani-cli lacks a setting/menu the bridge uses
  return `<div class="group ${off ? "off" : ""}"><h3>Online episodes</h3>${off ? '<p class="sub">Install ani-cli to watch and download episodes you don\'t have.</p>' : ""}${bad ? `<p class="note"><span class="chip warn">Update ani-cli</span> This version${st.version ? " (" + esc(st.version) + ")" : ""} is missing ${esc(st.check.missing.join(", "))} — online episodes and downloads won't work until it's updated.</p>` : ""}
    ${srow("Quality", "For watching and downloading online episodes.", `<select aria-label="Stream quality" data-a="spref" data-k="stream_quality" ${off ? "disabled" : ""}>${(st.qualities || []).map((q) => `<option value="${q}" ${q === st.quality ? "selected" : ""}>${QLABEL[q] || q + "p"}</option>`).join("")}</select>`)}
    ${srow("Audio", "Sub or dub for online episodes. Any episode's menu plays the other one.", `<span class="seg" role="group" aria-label="Default audio">${SLMODES.map(([m, l]) => `<button class="${st.mode === m ? "on" : ""}" aria-pressed="${st.mode === m}" data-a="spref" data-k="stream_mode" data-v="${m}" ${off ? "disabled" : ""}>${l}</button>`).join("")}</span>`)}
    ${srow("Downloads", "Saved into your library folder, one folder per show.", "")}
    ${srow("VPN in the status line", "Shows in the line at the bottom whether a VPN connection is up.", tgl("vpn_icon", "VPN in the status line", s.vpn_icon !== false, off))}
    ${srow("Warn without VPN", "Asks before an online episode or download starts while no VPN is up.", tgl("vpn_warn", "Warn without VPN", s.vpn_warn !== false, off))}</div>`;
}
/* Library folder picker (Decision 32): one inline folder browser shared by
   Settings and the tour's first step (ctx). It renders from S.dir and paints
   into its own slot, so walking folders never rebuilds the page or replays the
   tour dialog's swap. It unfolds from the folder field: the field follows the
   browsed folder, Browse toggles it like Cancel. Choosing writes library_root
   to the config file. */
const libPath = (ctx, s) => `<input id="libpath-${ctx}" aria-label="Library folder" value="${esc(s.library_root || "")}" placeholder="Choose a folder…" autocomplete="off" spellcheck="false">`;
const dirBtn = (ctx) => `<button class="btn" id="dirbtn-${ctx}" data-a="dirbrowse" aria-expanded="${S.dir?.ctx === ctx}" aria-controls="dirpick-${ctx}">Browse…</button>`;
/* @crumbs:start */
/* A Windows path (C:\Users\…, TODO 74) starts at its drive and splits on "\". */
function dirCrumbs(path) {
  const win = /^[A-Za-z]:[\\/]/.test(path), sep = win ? "\\" : "/";
  const parts = path.split(win ? /[\\/]/ : "/").filter(Boolean), last = parts.length - 1;
  const seg = (label, to, cur) => cur ? `<b aria-current="location">${esc(label)}</b>` : `<button data-a="dirgo" data-v="${esc(to)}">${esc(label)}</button>`;
  const to = (i) => win && !i ? parts[0] + sep : (win ? "" : sep) + parts.slice(0, i + 1).join(sep);
  return (win ? "" : seg("/", "/", last < 0)) + parts.map((x, i) => `${i ? `<i>${sep}</i>` : ""}${seg(x, to(i), i === last)}`).join("");
}
/* @crumbs:end */
function DirPick(ctx) {
  const p = S.dir;
  if (!p || p.ctx !== ctx) return `<div id="dirpick-${ctx}" hidden></div>`;
  const cur = S.settings?.library_root, fresh = !p.shown; p.shown = true;
  const n = p.dirs?.length, place = p.path && [...(p.places || [])].sort((x, y) => y.path.length - x.path.length).find((x) => p.path === x.path || p.path.startsWith(x.path + "/"));
  const note = p.err ? `<span class="dp-note err">${esc(p.err)}</span>`
    : p.confirm ? `<span class="dp-note">Switch from <code>${esc(cur)}</code>? Watch history and AniList data are kept; shows that aren't in the new folder leave the library until their files are back.</span>`
    : `<span class="dp-note">${n ? `${n} folder${n === 1 ? "" : "s"}` : ""}</span>`;
  return `<div id="dirpick-${ctx}" class="dirpick${fresh ? " in" : ""}" role="group" aria-label="Choose your library folder"><div class="dp-well">
    <div class="dp-bar"><button class="btn dp-up" data-a="dirgo" data-v="${esc(p.parent || "")}" ${p.parent ? "" : "disabled"} aria-label="Parent folder" title="Parent folder">${ico("up")}</button>
      <nav class="dp-crumbs" aria-label="Folder path">${p.path ? dirCrumbs(p.path) : ""}</nav>
      ${p.places?.length ? `<div class="dp-places">${p.places.map((x) => `<button class="chip ${x === place ? "on" : ""}" data-a="dirgo" data-v="${esc(x.path)}">${esc(x.label)}</button>`).join("")}</div>` : ""}</div>
    <div class="dp-list">${p.dirs == null ? '<p class="dim">Loading…</p>' : !p.dirs.length ? `<p class="dp-empty">${ico("folder")}<span>No subfolders here — you can still use this folder.</span></p>` : p.dirs.map((d) => `<button class="dp-dir" data-a="dirgo" data-v="${esc(d.path)}">${ico("folder")}<span>${esc(d.name)}</span></button>`).join("")}</div>
    <div class="dp-foot ${p.confirm ? "ask" : ""}">${note}${p.confirm
      ? '<button class="btn" data-a="dirconfirm" data-v="0">Back</button><button class="btn pri" data-a="dirconfirm" data-v="1">Switch folder</button>'
      : `<button class="btn" data-a="dirclose">Cancel</button><button class="btn pri" data-a="diruse" ${p.path ? "" : "disabled"}>Use this folder</button>`}</div></div></div>`;
}
/* Edge fades replace the list's scrollbar: shown only where more folders hide. */
function dirFade(l) { l.classList.toggle("ft", l.scrollTop > 2); l.classList.toggle("fb", l.scrollTop + l.clientHeight < l.scrollHeight - 2); }
document.addEventListener("scroll", (e) => e.target.classList?.contains("dp-list") && dirFade(e.target), true);
function paintDir(ctx = S.dir?.ctx) {
  const el = $("#dirpick-" + ctx);
  if (!el) return;
  const had = el.contains(document.activeElement);
  el.outerHTML = DirPick(ctx);
  $("#dirbtn-" + ctx)?.setAttribute("aria-expanded", String(S.dir?.ctx === ctx));
  const box = $("#dirpick-" + ctx), cr = box.querySelector(".dp-crumbs"), l = box.querySelector(".dp-list");
  if (cr) cr.scrollLeft = cr.scrollWidth; // the deepest folder stays in view
  if (l) dirFade(l);
  if (had) box.querySelector(":is(.dp-dir,[data-a=diruse],[data-a=dirconfirm][data-v='1']):not(:disabled)")?.focus();
}
async function dirGo(path) {
  const p = S.dir;
  if (!p) return;
  p.confirm = false; p.err = "";
  let r;
  try { r = await api("/api/browse" + (path ? "?path=" + encodeURIComponent(path) : "")); } catch { r = { error: "Could not reach the hub." }; }
  if (S.dir !== p) return; // closed meanwhile
  if (r.error && !p.path && path) { await dirGo(""); p.err = r.error; } // a bad typed path still opens a listing
  else if (r.error) p.err = r.error;
  else { Object.assign(p, r); const f = $("#libpath-" + p.ctx); if (f) f.value = p.path; }
  paintDir();
}
function dirClose() {
  const ctx = S.dir?.ctx;
  S.dir = null;
  const f = $("#libpath-" + ctx);
  if (f) f.value = ctx === "extra" ? "" : S.settings?.library_root || "";
  paintDir(ctx);
}
async function dirSet(path) {
  let r;
  try { r = await api("/api/library-root", { path, adopt: true }); } catch { r = { error: "Could not reach the hub." }; }
  if (!S.dir) return;
  if (!r.ok) { S.dir.confirm = false; S.dir.err = r.error || "Could not use this folder."; paintDir(); return; }
  S.dir = null;
  toast("Library folder set — scanning…");
  await loadSettings();
  await reload();
  api("/api/refresh", {}).catch(() => {});
  if (S.modal?.t === "tour") paintModal(); else if (S.view === "settings") render();
}
/* Extra library folders (Decision 42): added through the same browser, removed from their row. */
async function extraAdd(path) {
  let r;
  try { r = await api("/api/library-extra", { path }); } catch { r = { error: "Could not reach the hub." }; }
  if (!S.dir) return;
  if (!r.ok) { S.dir.err = r.error || "Could not use this folder."; paintDir(); return; }
  S.dir = null;
  toast("Folder added — scanning…");
  await loadSettings();
  await reload();
  api("/api/refresh", {}).catch(() => {});
  if (S.view === "settings") render();
}
async function extraRemove(id) {
  let r;
  try { r = await api("/api/library-extra", { remove: id }); } catch { r = { error: "Could not reach the hub." }; }
  if (!r.ok) return toast(r.error || "Could not remove the folder.");
  toast("Folder removed — its watch history is kept");
  await loadSettings();
  await reload();
  if (S.view === "settings") render();
}
function extraGroup(s) {
  const xs = s.library_extra || [], name = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;
  const rows = xs.map((x) => srow(esc(name(x.path)), `<code>${esc(x.path)}</code>${x.state === "missing" ? ' <span class="chip warn">not reachable</span>' : ""}`,
    `<button class="btn" data-a="xremove" data-v="${x.id}">Remove</button>`)).join("");
  return `<div class="group"><h3>Extra folders</h3><p class="sub">More folders with anime, e.g. on another drive. Their shows join your library; downloads go where a show already is.</p>${rows}
    ${srow("Add a folder", xs.length ? "Another folder with anime." : "No extra folders yet.", `<div class="path">${libPath("extra", {})}${dirBtn("extra")}</div>`)}${DirPick("extra")}</div>`;
}
async function loadSettings() {
  try { S.settings = await api("/api/settings"); } catch { S.settings = null; }
  return S.settings;
}

/* ---------- nav, search, downloads ---------- */
const VIEWS = ["home", "library", "history", "stats", "settings", "week"];
/* One list for library and AniList hits: ranked by how well the title
   matches, library first on ties; an AniList hit that is a library show
   shows once, as the library show. AniList results follow the typing
   (debounced) and the last ones stay up while the next search runs. */
const srRank = (t, ql) => { const l = String(t || "").toLowerCase(), i = l.indexOf(ql); return i === 0 ? 0 : i > 0 && /[^a-z0-9]/.test(l[i - 1]) ? 1 : i > 0 ? 2 : 3; };
/* The search is a command line (Decision 38): your library, AniList,
   every setting (switches flip right here) and commands — grouped, with a
   preview of the picked row and its keys. */
const SETTINGS_INDEX = [
  ["Accent colour", "appearance", "colour color accent theme follow"], ["Background art", "appearance", "background art blur wallpaper image"], ["Sharper banners", "appearance", "upscayl upscale banner sharper quality gpu", "upscale_banners"], ["Banner look", "appearance", "upscayl upscale banner natural digital art tta model"], ["Top bar", "appearance", "top bar nav search settings button style island capsule"],
  ["Film grain", "appearance", "grain noise texture"], ["Motion", "appearance", "motion animation animations calm reduce"], ["Zoom", "appearance", "zoom size scale bigger smaller"],
  ["Library folder", "library", "folder path library directory browse"], ["Extra folders", "library", "extra second another folder drive library add"], ["Rescan", "library", "rescan scan refresh metadata"], ["Measure episode lengths", "library", "episode length duration minutes runtime ffprobe measure", "measure_lengths"], ["Detected tools", "library", "mpv ani-cli upscayl tools player"],
  ["Skip openings", "playback", "skip opening op chapter mpv"], ["Skip endings", "playback", "skip ending credits ed chapter mpv"], ["Times from AniSkip", "playback", "aniskip skip times opening ending privacy", "aniskip"], ["Ask without VPN", "playback", "aniskip vpn warn ask privacy", "aniskip_vpn_warn"], ["Remember audio and subtitles", "playback", "audio subtitle subs track language dub dual remember", "remember_tracks"],
  ["Fullscreen", "mpv", "mpv fullscreen window player config"], ["Opening and ending on the timeline", "mpv", "mpv timeline chapters marks opening ending seek bar"],
  ["Quality", "online", "quality resolution 1080 720 stream download"], ["Audio", "online", "sub dub audio language"],
  ["VPN in the status line", "online", "vpn status icon", "vpn_icon"], ["Warn without VPN", "online", "vpn warn ask", "vpn_warn"],
  ["Account", "anilist", "anilist account connect token login disconnect"], ["Track episodes", "anilist", "anilist progress sync track", "auto_progress_sync"],
  ["Watched marks from AniList", "anilist", "anilist watched marks", "anilist_watched"], ["Add shows automatically", "anilist", "anilist add automatically watching", "auto_add_watch"],
  ["Phone remote", "phone", "phone remote mobile wifi pair qr android iphone ios control"],
  ["Keys", "keys", "keys keyboard shortcuts hotkeys"], ["Config file", "data", "config file data folder server port"], ["First-run tour", "data", "tour setup welcome"],
];
const SET_TABS = [["appearance", "Appearance"], ["library", "Library"], ["playback", "Playback"], ["mpv", "mpv"], ["online", "Online episodes"], ["anilist", "AniList"], ["phone", "Phone remote"], ["keys", "Keys"], ["data", "Data & setup"]];
const COMMANDS = [
  ["Go to Home", "home start", () => nav("home"), "1"], ["Go to Library", "library shows", () => nav("library"), "2"], ["Go to Journal", "journal history stats", () => nav("history"), "3"],
  ["Open settings", "settings preferences options", () => nav("settings"), ","], ["Rescan library", "rescan scan refresh", () => { api("/api/refresh", {}).catch(() => {}); toast("Rescanning + fetching metadata…"); setTimeout(reload, 2500); }, ""],
  ["Show downloads", "downloads queue", () => { S.menu = "dl"; paintNav(); }, ""], ["Show keys", "keys keyboard shortcuts", () => { S.menu = "keys"; paintNav(); }, "?"],
];
const srMatch = (hay, ql) => ql.split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
let srPv = [];
function srHTML() {
  const q = S.q.trim(), ql = q.toLowerCase();
  srPv = [];
  let i = 0, h = "";
  const row = (attrs, inner, pv) => { srPv.push(pv); return `<div class="mi" style="--i:${i}" data-i="${i++}" ${attrs}>${inner}</div>`; };
  if (q) {
    const local = (S.data?.shows || []).filter((s) => `${s.collection || ""} ${s.title}`.toLowerCase().includes(ql))
      .map((s) => ({ r: Math.min(srRank(s.title, ql), srRank(s.collection, ql)), local: true, s }));
    const have = new Set(local.map((x) => x.s.meta?.anilist_id).filter(Boolean));
    const remote = (alSearchable() && S.alres || []).map((x, k) => ({ r: srRank(candLabel(x), ql), x, i: k })).filter((y) => !have.has(y.x.id));
    const L = local.sort((a, b) => a.r - b.r).slice(0, 5), R = remote.sort((a, b) => a.r - b.r).slice(0, 5);
    if (L.length) h += `<div class="psec">your library</div>` + L.map(({ s }) => { const sp = spineOf(s), t = pickTarget(s);
      return row(`data-a="open" data-v="${esc(s.id)}"`, `${poster(s.title, s.poster, { c: "t" })}<span class="tx"><b>${hl(s.title, q)}</b><small>${esc(sp ? statusLabel(sp.status) : `${epTotal(s)} episodes`)}${t ? " · " + esc(KIND_WORD[t.kind].toLowerCase() + " " + epCode(t.sea, t.ep)) : ""}</small></span><span class="r"><kbd class="kb">↵</kbd></span>`,
        { art: s.banner || s.poster, t: s.title, m: [sp ? statusLabel(sp.status) : "", `${epWatched(s)} / ${epTotal(s)} watched`, t ? `up next ${epCode(t.sea, t.ep)}` : ""].filter(Boolean).join(" · "), k: [["Open", "↵"]] }); }).join("");
    if (R.length) h += `<div class="psec">on anilist</div>` + R.map(({ x, i: k }) => { const e = aniMode() ? S.data.spine.entries.find((z) => z.id === x.id) : null;
      const sub = e ? "on your list · " + statusLabel(e.status) : [x.seasonYear || x.startDate?.year, FMT[x.format] || x.format].filter(Boolean).join(" · ") + " · not on your list";
      return row(`data-a="catpick" data-v="${k}"`, `${poster(candLabel(x), x.coverImage?.extraLarge, { c: "t" })}<span class="tx"><b>${hl(candLabel(x), q)}</b><small>${esc(sub)}</small></span>${e ? "" : '<span class="r"><span class="tag">+ planning</span></span>'}`,
        { art: x.bannerImage || x.coverImage?.extraLarge, t: candLabel(x), m: sub, k: [["Open", "↵"]] }); }).join("");
  }
  const sets = q ? SETTINGS_INDEX.filter(([t, tab, w]) => srMatch(`${t} ${w} ${tab}`.toLowerCase(), ql)).slice(0, 5) : [];
  if (sets.length) h += `<div class="psec">settings</div>` + sets.map(([t, tab, , key]) => { const on = key && !!S.settings?.[key], tl = SET_TABS.find(([k]) => k === tab)[1];
    return row(`data-a="setgo" data-v="${esc(t)}" data-t="${tab}"`, `<span class="ic">⚙</span><span class="tx"><b>${esc(t)}</b><small>${esc(tl)}</small></span>${key && S.settings ? `<span class="r"><button class="switch" role="switch" aria-checked="${on}" aria-label="${esc(t)}" data-a="srsw" data-v="${key}"></button></span>` : ""}`,
      { t, m: `Settings → ${tl}`, k: key ? [["Switch", "click"], ["Open in Settings", "↵"]] : [["Open in Settings", "↵"]] }); }).join("");
  const cmds = COMMANDS.map((c, k) => [c, k]).filter(([[t, w]]) => !q || srMatch(`${t} ${w}`.toLowerCase(), ql)).slice(0, q ? 4 : 7);
  if (cmds.length) h += `<div class="psec">commands</div>` + cmds.map(([[t, , , key], k]) => row(`data-a="cmd" data-v="${k}"`, `<span class="ic">›</span><span class="tx"><b>${esc(t)}</b></span>${key ? `<span class="r"><kbd class="kb">${key}</kbd></span>` : ""}`, { t, m: "command", k: [["Run", "↵"]] })).join("");
  const busy = q && alSearchable() && (S.alBusy || (S.alq !== q && q.length >= 2));
  if (busy) h += '<div class="psec"><i class="spin"></i> searching AniList…</div>';
  if (!h) h = `<p class="cap">No matches${alSearchable() ? " in your library, on AniList or in settings" : " in your library or settings"}</p>`;
  return `<div class="pal2"><div class="plist">${h}</div><div class="pview" id="srpv"></div>
    <div class="pfoot"><span>open <kbd class="kb">↵</kbd></span><span>move <kbd class="kb">↑</kbd><kbd class="kb">↓</kbd></span><span class="fill"></span><span>library · anilist · settings · commands</span><span>close <kbd class="kb">esc</kbd></span></div></div>`;
}
/* The preview pane follows the picked row (arrows or pointer). */
function srPreview(i) {
  const pv = $("#srpv"), x = srPv[+i];
  $$("#sr .mi").forEach((m) => m.classList.toggle("sel", m.dataset.i === String(i)));
  if (!pv) return;
  pv.innerHTML = !x ? "" : `${x.art ? `<span class="pa" style="background-image:${esc(cssUrl(x.art))}"></span>` : ""}<h3>${esc(x.t)}</h3><p>${esc(x.m || "")}</p><div class="pk">${(x.k || []).map(([l, k]) => `<span>${esc(l)} <kbd class="kb">${esc(k)}</kbd></span>`).join("")}</div>`;
}
/* AniList's catalog search needs no account: offered in AniList mode, and to
   anyone with ani-cli (finding something to stream). */
const alSearchable = () => aniMode() || !!S.data?.stream_ready;
const DL_STATE = { queued: "Waiting", running: "Downloading", done: "Downloaded", failed: "Failed", cancelled: "Cancelled" };
/* Downloads (status line, Decision 38 mockup): art · "Show · E09" · what is
   happening (online → disk · size · time left) · bar · cancel, or open when done. */
function dlPop() {
  const rows = S.dl.slice().reverse().map((j) => {
    const live = j.status === "running" || j.status === "queued", e = S.data?.spine?.entries?.find((x) => x.id === j.media_id);
    const art = e?.banner || e?.poster, ep = /^\d+$/.test(j.token) ? "E" + String(j.token).padStart(2, "0") : "E" + j.token;
    const meta = j.status === "running" ? ["online → disk", j.size, j.eta ? j.eta + " left" : `${Math.floor(j.percent)}%`].filter(Boolean).join(" · ")
      : j.status === "queued" ? "waiting · next in line" : j.status === "done" ? "✓ done · added to your library" : j.status === "failed" ? "failed" + (j.error ? " — " + j.error : "") : "cancelled";
    return `<div class="dlj ${j.status}"><span class="dlth"${art ? ` style="background-image:${esc(cssUrl(art))}"` : ""}></span><div class="dlj-t"><b>${esc(j.show)} · ${esc(ep)}</b><small>${esc(meta)}</small>
      ${live ? `<div class="bar"><i style="width:${j.percent || 0}%"></i></div>` : ""}</div>${live ? `<button class="dlc" aria-label="Cancel download" title="Cancel" data-a="dlcancel" data-v="${j.id}">${ico("close")}</button>`
      : j.status === "done" ? `<button class="dlc" aria-label="Open ${esc(j.show)}" title="Open" data-a="dlopen" data-v="${j.media_id}">${ico("play")}</button>` : ""}</div>`;
  }).join("");
  const over = S.dl.some((j) => j.status !== "running" && j.status !== "queued");
  return `<div class="pop dl"><div class="dlh"><span>downloads</span>${over ? '<button class="link-btn" data-a="dlclear">clear finished</button>' : ""}</div>${rows ? `<div class="dlist">${rows}</div>` : '<p class="dl-empty">Nothing downloading.</p>'}
    ${S.data?.stream_ready ? "" : '<p class="dl-empty">Install ani-cli to download episodes.</p>'}</div>`;
}
/* The sync chip's list (TODO 14): what is queued for AniList, why it is
   still waiting, and a cancel per change — the local change stays. */
const LIST_WORD = Object.fromEntries(LISTS);
function obChange(it) {
  if (it.eps) return `Watched marks on ${it.eps} episode${it.eps === 1 ? "" : "s"}`;
  const p = it.patch || {}, out = [];
  if (p.status) out.push(LIST_WORD[p.status] || p.status);
  if ("progress" in p) out.push(`Progress ${p.progress}`);
  if ("score" in p) out.push(p.score ? `Score ${p.score}` : "Score cleared");
  if ("repeat" in p) out.push(`Rewatches ${p.repeat}`);
  if ("started_at" in p) out.push(p.started_at ? `Started ${p.started_at}` : "Start date cleared");
  if ("completed_at" in p) out.push(p.completed_at ? `Finished ${p.completed_at}` : "Finish date cleared");
  if ("favourite" in it) out.push(it.favourite ? "Add to favourites" : "Remove from favourites");
  return out.join(" · ") || "Update";
}
function obState(it) {
  if (it.state === "seasons") return "Waits for AniList's season info";
  if (it.state === "retrying") return `AniList unreachable — next try in ${Math.max(1, Math.round(it.retry_in / 60))} min`;
  return "Waiting to send";
}
function obPop() {
  const items = S.obItems;
  const rows = (items || []).map((it) => `<div class="dlj"><div class="dlj-t"><b>${esc(it.title)}</b><small class="obc">${esc(obChange(it))}</small><small>${esc(obState(it))}</small></div>
    <button class="chip ib" aria-label="Cancel this change" title="Don't send this to AniList" data-a="obcancel" data-v="${it.id}">${ico("close")}</button></div>`).join("");
  return `<div class="pop dl ob"><div class="cap">Waiting to sync with AniList</div>
    ${!items ? '<p class="dl-empty"><i class="spin"></i></p>' : rows ? `<div class="dlist">${rows}</div>` : '<p class="dl-empty">Nothing waiting.</p>'}
    <p class="dl-empty">Cancelling keeps the change in the app — only AniList won't get it.</p>
    <div class="row" style="padding:6px 8px 0"><button class="btn" data-a="sync">${ico("refresh")}Sync now</button></div></div>`;
}
const obLoad = () => api("/api/outbox").then((r) => { S.obItems = r.items || []; if (S.menu === "ob") paintNav(); }).catch(() => {});

let navSig = "", nrLast = "";
/* VPN status (E5): a chip in the top bar and a question before online
   episodes or downloads start without one — both switchable in Settings. */
const vpnOn = () => !!S.data?.vpn?.active;
function vpnChip() {
  const v = S.data?.vpn;
  if (!v?.known || !S.data.stream_ready || S.settings?.vpn_icon === false) return "";
  return `<span class="st vpn ${v.active ? "ok" : "warn"}" title="${v.active ? `VPN active (${esc(v.iface)})` : "No VPN detected"}">${v.active ? "vpn on" : "no vpn"}</span>`;
}
/* Next-episode source warning (TODO 10, Decision 2): beside the
   now-playing chip when the next episode comes from somewhere else (or
   can't play) — online carries the VPN state, since autoplay won't ask. */
function nextChip(now) {
  const src = now?.next?.source, here = now?.episode_id?.startsWith("stream:") ? "online" : "local";
  if (!src || now.loading || (src !== "missing" && src === here)) return "";
  if (src === "missing") return `<span class="st nx warn" title="The next episode isn't on disk or online — autoplay stops here">next: not available</span>`;
  if (src === "local") return `<span class="st nx" title="The next episode plays from your files">next: from disk</span>`;
  const v = S.data?.vpn, vpn = !v?.known ? "" : v.active ? " · vpn" : " · no vpn";
  return `<span class="st nx${v?.known && !v.active ? " warn" : ""}" title="The next episode plays online through ani-cli">next: online${vpn}</span>`;
}
/* skp: the play would also use AniSkip (Decision 39), so the dialog asks
   that too; go(true/false) carries the answer. */
function vpnGate(go, skp = false) {
  if (vpnOn() || !S.data?.vpn?.known || S.settings?.vpn_warn === false) return go();
  openModal({ t: "vpn", go, skp });
}
/* AniSkip without a VPN (Decision 39): asked on play like online episodes.
   The server only uses AniSkip then if the play says yes (`aniskip`). */
/* Who would see the IP address without a VPN: the streaming site
   (ani-cli fetches online episodes and downloads), AniSkip, or both. */
const ipLine = (online, skp) => `Without a VPN, ${online && skp ? "the streaming site and AniSkip see" : online ? "the streaming site sees" : "AniSkip sees"} your IP address.`;
/* Use AniSkip / Without AniSkip in place of one Continue button. */
const skpBtns = (yes, no, key = "") => `<button class="btn" data-a="${no}">Without AniSkip</button><button class="btn pri" data-a="${yes}">Use AniSkip${key}</button>`;
const aniskipAsk = () => S.settings?.aniskip === true && S.settings?.aniskip_vpn_warn !== false && !!S.data?.vpn?.known && !vpnOn();
/* Once, before the very first play: the two things Enter does in mpv. */
function TipDlg() {
  return `<h2 id="dt">While you watch</h2>
  <p class="sub">Press Enter in mpv:</p>
  <div class="ptip"><div><kbd class="kb">↵</kbd><span><b>In an opening or ending</b>Skips it. A button shows up in the corner too.</span></div>
  <div><kbd class="kb">↵</kbd><span><b>Anywhere else</b>Finishes the episode, marks it watched and plays the next one.</span></div></div>
  <div class="actions"><span class="note2 mono">change this in Settings → Playback</span><button class="btn pri" data-a="tipgo">Got it, play</button></div>`;
}
function SkpDlg() {
  return `<h2 id="dt">No VPN detected</h2>
  <p class="sub">${ipLine(false, true)}</p>${vpnWait()}
  <div class="actions three"><span class="note2 mono">switch this off in Settings → Playback</span><button class="btn" data-a="close">Cancel<kbd class="kb">esc</kbd></button>${skpBtns("skpgo", "skpno")}</div>`;
}
const SHIELD = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z"/><path d="M12 8v4M12 15.5v.01"/></svg>';
const vpnWait = () => `<div class="warn2"><span class="wi">${SHIELD}</span><div><b>No VPN is on</b><span>Turn it on and this starts by itself.</span><div class="watching"><i class="spin"></i>watching for a VPN…</div></div></div>`;
function VpnDlg() {
  const skp = S.modal.skp;
  return `<h2 id="dt">No VPN detected</h2>
  <p class="sub">${ipLine(true, skp)}</p>${vpnWait()}
  <div class="actions${skp ? " three" : ""}"><span class="note2 mono">switch this off in Settings → Online episodes${skp ? " / Playback" : ""}</span><button class="btn" data-a="close">Cancel<kbd class="kb">esc</kbd></button>${skp ? skpBtns("vpngo", "vpnno") : '<button class="btn pri" data-a="vpngo">Continue anyway</button>'}</div>`;
}
/* Next-episode prompt (TODO 10): the last file finished and the next
   episode is online only — autoplay waits for Continue / Stop here. */
let lastNextPrompt = null;
function maybeNextPrompt(p) {
  if (p.id === lastNextPrompt || S.modal) return;
  lastNextPrompt = p.id;
  openModal({ t: "nxt", p });
}
function nxVpn() {
  const v = S.data?.vpn;
  if (!v?.known) return `<span class="dim mono">vpn status unknown</span>`;
  return v.active ? `<span class="okv mono">vpn on · ${esc(v.iface)}</span>` : vpnWait();
}
function NextDlg() {
  const p = S.modal.p, c = p.id && findEpContext(p.id), art = c ? (homeThumb(c.sea, c.ep) || c.s.banner || c.s.poster) : null, v = S.data?.vpn;
  /* kind "aniskip" (Decision 39): autoplay found no VPN and has no AniSkip
     answer for this chain yet; an online next episode asks it too. */
  const ask = p.kind === "aniskip", online = String(p.id).startsWith("stream:"), noVpn = v?.known && !v.active, skp = noVpn && (ask || aniskipAsk());
  const label = p.label || (c ? epCode(c.sea, c.ep) : "Next episode"), show = p.show || c?.s?.title;
  const go = skp ? skpBtns("nxgo", "nxno", '<kbd class="kb">↵</kbd>') : `<button class="btn pri" data-a="nxgo">${noVpn ? "Stream anyway" : "Continue"}<kbd class="kb">↵</kbd></button>`;
  return `<div class="nx2">${art ? `<span class="nxa" style="background-image:${esc(cssUrl(art))}"><span class="lab">${esc(label)}${online ? " · online" : ""}</span></span>` : ""}<div>
    <h2 id="dt">${esc(label)}${show ? ` <small>${esc(show)}</small>` : ""}</h2>
    <p class="sub">${online ? "Not on your disk, so it streams online. " : ""}${noVpn ? ipLine(online, skp) : ""}</p>
    <div id="nx-vpn">${nxVpn()}</div></div></div>
  <div class="actions${skp ? " three" : ""}"><span class="note2 mono">${noVpn ? "starts by itself when a VPN comes on" : ""}</span><button class="btn" data-a="nxstop">Stop here<kbd class="kb">esc</kbd></button>${go}</div>`;
}
/* The three places + their number keys (Decision 38; History and Stats
   become Journal in step 5); Settings lives behind the icon (,). */
const TABS = [["home", "Home"], ["library", "Library"], ["history", "Journal"]];
const KEYS = [["Search or run a command", "/"], ["Home · Library · Journal", "1 2 3"], ["Settings", ","], ["Move around", "← → ↑ ↓"], ["Open", "↵"], ["Back", "Esc"], ["Show page: play · edit entry", "↵ · E"], ["Show page: download · mark all", "D · ⇧W"], ["These keys", "?"]];
const keysPop = () => `<div class="pop keyp"><div class="cap">Keys</div><div class="keys">${KEYS.map(([n, k]) => `<span>${n}</span><kbd class="kb">${k}</kbd>`).join("")}</div></div>`;
function paintNav() {
  paintTabbar(); paintSide(); paintTop();
  const cur = S.view === "show" || S.view === "fr" ? "library" : S.view === "stats" ? "history" : S.view;
  const links = $("#links");
  links.innerHTML = TABS.map(([k, n], i) => `<button class="link ${cur === k ? "on" : ""}" ${cur === k ? 'aria-current="page"' : ""} data-a="go" data-v="${k}">${n}<kbd>${i + 1}</kbd></button>`).join("");
  glide(links);
  $("#setb").classList.toggle("on", cur === "settings");
  const ph = alSearchable() ? "search library or AniList" : "search your library";
  $("#q").placeholder = ph; $("#srph").textContent = ph;
  const sr = $("#sr"), srHad = !!sr.firstElementChild;
  const keep = $("#sr .mi.sel")?.dataset.i;
  sr.innerHTML = S.menu === "sr" ? (touchSearch() ? srHTMLTouch() : srHTML()) : "";
  if (!srHad && sr.firstElementChild) sr.firstElementChild.classList.add("in"); // entrance only on open, not on every keystroke
  if (S.menu === "sr") srPreview(keep != null && srPv[keep] ? keep : 0);
  const wasOpen = document.body.classList.contains("srch");
  document.body.classList.toggle("srch", S.menu === "sr");
  fitPal();
  if (!wasOpen && S.menu === "sr") { const bx = $("#pal .pbox"); bx.classList.remove("in"); void bx.offsetWidth; bx.classList.add("in"); }
  const now = S.data?.now, ctx = now && findEpContext(now.episode_id), pct = now ? Math.round(now.percent) : 0;
  const nr = $("#nr"), popHad = !!nr.querySelector(".pop"), dlActive = S.dl.filter((j) => j.status === "running" || j.status === "queued");
  const dlPct = dlActive.find((j) => j.status === "running")?.percent;
  /* The status line (Decision 38): playing · next source · downloads · VPN · AniList sync · keys. */
  const html = `${now ? `<button class="st np" ${ctx ? `data-a="open" data-v="${esc(ctx.s.id)}"` : ""}><i class="led"></i><span class="npt">playing <b>${esc(now.show)} · ${esc(now.label)}</b></span><span class="pc">${now.loading ? "loading" : "@P@%"}</span></button>${nextChip(now)}` : ""}
    <div class="rel"><button class="st dlb${dlActive.length ? " on" : ""}" aria-label="Downloads" aria-expanded="${S.menu === "dl"}" data-a="menu" data-v="dl">${ico("download")}${dlActive.length ? `<b>${dlActive.length}</b>${dlPct != null ? ` · ${Math.floor(dlPct)}%` : ""}` : "downloads"}</button>${S.menu === "dl" ? dlPop() : ""}</div>
    ${vpnChip()}
    ${S.outbox ? `<div class="rel"><button class="st sync warn" aria-expanded="${S.menu === "ob"}" data-a="obox">${ico("refresh")}${S.outbox} change${S.outbox === 1 ? "" : "s"} waiting to sync</button>${S.menu === "ob" ? obPop() : ""}</div>` : S.ani?.connected ? '<span class="st">anilist synced</span>' : ""}
    <div class="rel rgt"><button class="st" aria-expanded="${S.menu === "keys"}" data-a="menu" data-v="keys">keys <kbd class="kb">?</kbd></button>${S.menu === "keys" ? keysPop() : ""}</div>`;
  if (html === nrLast) {
    /* Only the playback percent moved: patch in place. */
    const pc = nr.querySelector(".np .pc");
    if (pc && !now.loading) pc.textContent = pct + "%";
  } else {
    const dlScroll = nr.querySelector(".dlist")?.scrollTop || 0;
    nr.innerHTML = html.replaceAll("@P@", pct); nrLast = html;
    const dlist = nr.querySelector(".dlist");
    if (dlist) dlist.scrollTop = dlScroll;
    if (!popHad) nr.querySelector(".pop")?.classList.add("in");
  }
}

/* ---------- phone and tablet layout (Decision 44) ---------- */
/* Phones (≤ 700 px): a floating bottom tab bar in the One UI 9 style, Home
   as one up-next card plus a compact list (the card opens a full-screen
   deck of what you're watching), the week as its own timeline, episode
   lists as rows with the Play button at the thumb. Tablets: a show list
   beside the show page (landscape), Home in two columns (portrait). The
   PC layout is untouched. */
const mq = (q) => matchMedia(q).matches;
const isPhone = () => mq("(max-width:700px)");
const isTabletL = () => mq("(min-width:901px) and (max-width:1400px) and (pointer:coarse) and (orientation:landscape)");
const isTabletP = () => mq("(min-width:701px) and (max-width:1100px) and (orientation:portrait)");
const layoutKey = () => (isPhone() ? "phone" : isTabletL() ? "tabL" : isTabletP() ? "tabP" : "pc");
let layoutNow = layoutKey();
addEventListener("resize", () => { const k = layoutKey(); if (k !== layoutNow) { layoutNow = k; if (S.data) render(); } });
/* Phones and tablets: a service worker, so "Install app" makes a real app
   (sw.js caches nothing). Needs https — the Tailscale address. */
if ("serviceWorker" in navigator && isSecureContext && mq("(pointer:coarse)")) navigator.serviceWorker.register("/sw.js").catch(() => {});
const PHONE_TABS = [["home", "Home", "home"], ["library", "Library", "grid"], ["history", "Journal", "cal"]];
function paintTabbar() {
  const el = $("#tabbar");
  if (!el) return;
  if (!isPhone()) { if (el.firstChild) el.innerHTML = ""; return; }
  const cur = S.view === "show" || S.view === "fr" ? "library" : S.view === "stats" ? "history" : S.view === "week" ? "home" : S.view;
  if (!el.firstChild) el.innerHTML = `<div class="tb" role="tablist"><i class="ind"></i>${PHONE_TABS.map(([k, l, i]) => `<button role="tab" data-a="go" data-v="${k}">${ico(i)}<span>${l}</span></button>`).join("")}</div>
    <button class="sq" data-a="sropen" aria-label="Search">${ico("search")}</button>`;
  /* The highlight glides to the open tab (One UI 9); none on Settings. */
  let on = null;
  for (const b of el.querySelectorAll(".tb button")) { const y = b.dataset.v === cur; b.classList.toggle("on", y); b.setAttribute("aria-selected", y); if (y) on = b; }
  el.querySelector(".sq").classList.toggle("on", S.menu === "sr");
  const ind = el.querySelector(".ind");
  ind.style.opacity = on ? "1" : "0";
  if (on) { ind.style.width = on.offsetWidth + "px"; ind.style.transform = `translateX(${on.offsetLeft}px)`; }
}
/* Airing entries on your lists, soonest first (Home and the week share it). */
function airingList() {
  return aniMode() ? S.data.spine.entries.filter((e) => e.airing === "RELEASING" && e.next_episode && inProg(e.status))
    .sort((a, b) => (airsIn(a.next_episode) ?? Infinity) - (airsIn(b.next_episode) ?? Infinity)) : [];
}
const airAt = (e, now = Date.now()) => (e.next_episode?.at ? e.next_episode.at * 1000 : now + (airsIn(e.next_episode, now) || 0) * 1000);
const whenTxt = (t, now = Date.now()) => { const d = new Date(t), today = new Date(now), tm = d.toTimeString().slice(0, 5);
  const tom = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  return d.toDateString() === today.toDateString() ? `today ${tm}` : d.toDateString() === tom.toDateString() ? `tomorrow ${tm}` : `${DAYS[(d.getDay() + 6) % 7]} ${d.getDate()} · ${tm}`; };
function airRow(e, now = Date.now()) {
  const sec = (airAt(e, now) - now) / 1000;
  return `<button class="prow" data-a="entry" data-v="${esc(e.id)}">${poster(e.title, e.poster, { c: "pp" })}<span class="pt"><b>${esc(e.title)}</b><small>episode ${esc(e.next_episode?.number ?? "")} · ${esc(whenTxt(airAt(e, now), now))}</small></span><span class="cdn">${sec > 0 ? "in " + esc(fmtIn(sec)) : "out now"}</span></button>`;
}
/* A show you're watching as one row: poster, title, what's next, play. */
function watchRow({ s, t }) {
  const sea = t.sea, seen = sea.episodes.filter((x) => x.watched).length;
  return `<div class="prow" data-a="open" data-v="${esc(s.id)}" tabindex="0">${poster(s.title, s.poster, { c: "pp" })}<span class="pt"><b>${esc(s.title)}</b><small>${esc(epCode(sea, t.ep))} ${t.kind === "resume" ? "· " + esc(fmtLeft(t.ep.duration, t.ep.position)) + " left" : "next"} · ${seen}/${sea.episodes.length}</small></span>
    <button class="btn ic" data-a="play" data-v="${esc(t.ep.id)}" aria-label="${esc(KIND_WORD[t.kind])} ${esc(s.title)}">${ico("play")}</button></div>`;
}
function upPhone(up) {
  const s = up.s, ep = up.ep, sea = up.sea, art = s.banner || homeThumb(sea, ep) || s.poster;
  const seen = (sea?.episodes || []).filter((x) => x.watched).length, tot = sea?.episodes.length || 0;
  const verb = up.label === "Rewatch" ? "Rewatch" : resumable(ep) ? "Resume" : "Play";
  return `<div class="upp"><button class="upa2" data-a="open" data-v="${esc(s.id)}" aria-label="Open ${esc(s.title)}" style="${art ? `background-image:${esc(cssUrl(art))}` : `--h:${hueOf(s.title)}`}"></button>
    <div class="upb"><div class="eye"><i class="mdot"></i>${esc(up.label.toLowerCase())} · ${streamEp(ep) ? "online" : "on disk"}${isPlayingShow(s.id) ? ' · <span class="ok">playing now</span>' : ""}</div>
    <h2>${esc(s.title)}</h2><div class="sub">${esc(epCode(sea, ep))} · ${esc(epTitle(s, sea, ep) || "Episode " + ep.number)}</div>
    ${tot > 1 ? `<div class="tbar">${seasonTicks(sea)}<span>${seen}/${tot}</span></div>` : ""}
    <button class="btn pri big wide" data-a="play" data-v="${esc(ep.id)}">${ico("play")}<span>${verb} ${esc(epCode(sea, ep))}</span></button></div></div>`;
}
function HomePhone() {
  const d = S.data, up = nextUpInfo(), L = keepWatching(up && up.s.id), airing = airingList(), now = Date.now();
  const soon = airing.filter((e) => airAt(e, now) > now - 3600e3).slice(0, 3);
  const touched = new Set((d.history || []).map((e) => e.show_id));
  const fresh = d.shows.filter((s) => !touched.has(s.id) && s.poster && epTotal(s) > 0).slice(0, 7);
  const sum = [L.length + (up ? 1 : 0) ? `<span><b>${L.length + (up ? 1 : 0)}</b> in progress</span>` : "", airing.length ? `<span><b>${airing.length}</b> airing</span>` : ""].filter(Boolean).join('<i class="sep"></i>');
  let body = up ? upPhone(up) : "";
  if (L.length) body += `<section class="psec"><div class="sh"><h2>Also watching</h2><span class="mut">${L.length}</span></div>${L.map(watchRow).join("")}</section>`;
  if (soon.length) body += `<section class="psec"><div class="sh"><h2>This week</h2><span class="fill"></span><button class="link-btn" data-a="go" data-v="week">Whole week${ico("arrowr")}</button></div>${soon.map((e) => airRow(e, now)).join("")}</section>`;
  const recs = recsFor();
  if (recs.length) body += `<section class="hsec"><div class="sh"><h2>Recommended for you</h2><span class="fill"></span><button class="btn sm disc" data-a="deck" data-v="recs">${ico("up")}Discover</button></div><div class="strip hstrip recs">${recs.map(recCard).join("")}</div></section>`;
  else if (fresh.length) body += `<section class="hsec"><div class="sh"><h2>Ready to start</h2><span class="fill"></span><button class="link-btn" data-a="go" data-v="library">All shows${ico("arrowr")}</button></div><div class="rdy">${fresh.map((s) =>
    `<button class="rc" data-a="open" data-v="${esc(s.id)}"><span class="pw">${poster(s.title, s.poster)}</span><b>${esc(s.title)}</b><small>${epTotal(s)} episode${epTotal(s) === 1 ? "" : "s"} · disk</small></button>`).join("")}</div></section>`;
  if (!up && !L.length) body = `<div class="emptyx"><div class="eico">${ico("tv")}</div><b>Nothing watched yet</b><span>${d.shows.length ? "Your library is waiting." : "Choose your anime folder in Settings → Library."}</span>${d.shows.length ? '<button class="btn pri" data-a="go" data-v="library">Browse library</button>' : ""}</div>` + body;
  return `<div class="home2 phone"><div class="hello"><h1 class="hd">${dayWord(new Date().getHours())}</h1>${sum ? `<div class="sum">${sum}</div>` : ""}</div>${body}</div>`;
}
/* Tablet portrait: what you're watching and the week side by side. */
function homePair(L, airing) {
  const now = Date.now(), soon = airing.filter((e) => airAt(e, now) > now - 3600e3).slice(0, 5);
  if (!L.length && !soon.length) return "";
  return `<div class="hpair"><section class="psec"><div class="sh"><h2>Continue watching</h2><span class="mut">${L.length}</span></div>${L.slice(0, 5).map(watchRow).join("") || '<p class="empty">Nothing else in progress.</p>'}</section>
    <section class="psec"><div class="sh"><h2>This week</h2><span class="fill"></span><button class="link-btn" data-a="go" data-v="week">Whole week${ico("arrowr")}</button></div>${soon.map((e) => airRow(e, now)).join("") || '<p class="empty">Nothing airing this week.</p>'}</section></div>`;
}

/* The deck (E1, reworked — Elias 2026-10-27: "it should be a discovery
   like tiktok"): one show per screen, swipe up for the next. Two sources:
   "recs" — AniList's recommendations (Home → Recommended → Discover), with
   + Planning; "lib" — the Library list you're on, as filtered (Library →
   Swipe), with Play when it's on disk. Esc or the cross closes it. */
function deckItems(src) {
  if (src === "recs") {
    const listed = new Set((S.data.spine?.entries || []).filter((e) => e.status).map((e) => e.id));
    return (S.recs?.items || []).filter((r) => !listed.has(r.id)).map((r) => ({
      id: r.id, title: r.title, art: r.poster || r.banner, color: r.color,
      eye: r.because ? `because you liked ${r.because.title}` : "recommended for you",
      facts: [FMT[r.format] || r.format, r.year, r.average ? `★ ${r.average}` : ""].filter(Boolean).join(" · "),
      genres: (r.genres || []).slice(0, 3), add: !S.recsAdded?.has(r.id), open: ["recopen", r.id] }));
  }
  const m = libModel();
  return libList(m).map((e) => {
    const sh = e.local_show_id ? showById(e.local_show_id) : null, t = sh ? pickTarget(sh) : null;
    return { id: e.id, title: e.title, art: e.poster || e.banner, color: e.color, eye: statusLabel(e.status).toLowerCase(),
      facts: [FMT[e.format] || e.format, e.year, e.episodes ? `${e.progress || 0} / ${e.episodes}` : ""].filter(Boolean).join(" · "),
      genres: (e.genres || []).slice(0, 3), play: t && { id: t.ep.id, label: `${KIND_WORD[t.kind]} ${epCode(t.sea, t.ep)}` }, open: ["entry", e.id] };
  });
}
function openDeck(src = "recs", i = 0) {
  const items = deckItems(src);
  if (!items.length) { toast(src === "recs" ? "No recommendations yet — rate a few shows on AniList." : "Nothing in this list."); return; }
  S.deck = { src, i: Math.min(i, items.length - 1) };
  const el = $("#deck");
  el.innerHTML = `<div class="deck" role="dialog" aria-modal="true" aria-label="${src === "recs" ? "Discover" : "Swipe through your list"}">
    ${grab}<div class="dtop"><button class="btn ic" data-a="deckclose" aria-label="Close">${ico("close")}</button><b class="dttl">${src === "recs" ? "Discover" : esc(statusLabel(S.lib.tab) || "Library")}</b><span class="dcount mono"><b>${S.deck.i + 1}</b> / ${items.length}</span></div>
    <div class="dscroll">${items.map((x, k) => `<section class="dpage" style="${x.art ? `--art:${esc(cssUrl(x.art))}` : `--h:${hueOf(x.title)}`}"><i class="dart"></i>
        <div class="dbody"><div class="eye"><i class="mdot"${x.color ? ` style="background:${esc(hexAccent(x.color) || "var(--accent)")}"` : ""}></i>${esc(x.eye)}</div><h2>${esc(x.title)}</h2>
        <div class="sub mono">${esc(x.facts)}</div>${x.genres.length ? `<div class="dgen">${x.genres.map((g) => `<span class="chip">${esc(g)}</span>`).join("")}</div>` : ""}
        <div class="cta">${x.play ? `<button class="btn pri big" data-a="deckplay" data-v="${esc(x.play.id)}">${ico("play")}<span>${esc(x.play.label)}</span></button>` : ""}${x.add != null ? `<button class="btn ${x.play ? "" : "pri "}big" data-a="deckadd" data-v="${x.id}" ${x.add ? "" : "disabled"}>${ico(x.add ? "plus" : "check")}<span>${x.add ? "Planning" : "On Planning"}</span></button>` : ""}<button class="btn big" data-a="deckopen" data-v="${esc(x.open.join(":"))}">Details</button></div></div>
        ${k < items.length - 1 ? `<span class="dnext">${ico("up")}${esc(items[k + 1].title)}</span>` : ""}</section>`).join("")}</div></div>`;
  document.body.classList.add("deck-open");
  ovOpen("deck", () => closeDeck());
  const sc = el.querySelector(".dscroll");
  sc.scrollTop = S.deck.i * sc.clientHeight;
  sc.onscroll = () => {
    const k = Math.round(sc.scrollTop / sc.clientHeight);
    if (k === S.deck?.i) return;
    S.deck.i = k;
    el.querySelector(".dcount b").textContent = k + 1;
  };
  el.querySelector(".dtop .btn").focus({ preventScroll: true });
}
function closeDeck(silent) { if (!S.deck) return; S.deck = null; $("#deck").innerHTML = ""; document.body.classList.remove("deck-open"); if (silent) ovDrop("deck"); else ovClosed("deck"); }
document.addEventListener("keydown", (e) => {
  if (!S.deck) return;
  const sc = $("#deck .dscroll");
  if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closeDeck(); }
  else if (sc && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); sc.scrollBy({ top: (e.key === "ArrowDown" ? 1 : -1) * sc.clientHeight, behavior: reduceMotion() ? "auto" : "smooth" }); }
}, true);

/* The week (E2): every next episode on your lists as a timeline with
   countdowns, then the month with a dot on each day one airs. */
/* The desktop calendar (TODO 106): a real month you can page through, with
   every episode AniList has scheduled for what's on your lists. */
const monthName = (d) => d.toLocaleDateString(undefined, { month: "long" });
const airKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function loadSched() {
  if (S.schedBusy) return;
  S.schedBusy = true;
  api("/api/schedule").then((r) => {
    S.schedBusy = false; S.sched = { at: r.state === "loading" ? 0 : Date.now(), state: r.state, items: r.items || [] };
    if (S.view === "week") render(true);
    if (r.state === "loading") setTimeout(() => S.view === "week" && loadSched(), 2500);
  }).catch(() => { S.schedBusy = false; });
}
/* Date key → the day's events, earliest first. The list's own next episodes
   fill in until the schedule has loaded. */
function calEvents() {
  const byId = new Map(S.data.spine.entries.map((e) => [e.id, e])), mine = S.airCal.scope === "watching", days = new Map();
  const add = (x) => {
    const e = byId.get(x.id);
    if (!e || (mine && !inProg(e.status))) return;
    const d = x.at ? new Date(x.at * 1000) : new Date(`${x.date}T00:00:00`), k = airKey(d);
    (days.get(k) || days.set(k, []).get(k)).push({ e, ep: x.ep, t: d.getTime(), start: !x.at });
  };
  if (S.sched?.items.length) S.sched.items.forEach(add);
  else for (const e of airingList()) add({ id: e.id, ep: e.next_episode?.number, at: Math.floor(airAt(e) / 1000) });
  for (const l of days.values()) l.sort((a, b) => a.t - b.t);
  return days;
}
/* Context, from what AniList states: first and last episode, what you've seen. */
const airTag = (x) => (x.start || x.ep === 1 ? "Premiere" : x.ep && x.ep === x.e.episodes ? "Finale" : "");
const airSeen = (x) => !x.start && (x.e.progress || 0) >= x.ep;
const airTime = (x) => new Date(x.t).toTimeString().slice(0, 5);
const airStyle = (x) => (x.e.poster ? ` style="background-image:${esc(cssUrl(x.e.poster))}"` : "");
const airTile = (x) => { const t = airTag(x); return `<i class="airt${airSeen(x) ? " is-seen" : ""}"${airStyle(x)}>${t ? `<em class="${t === "Finale" ? "is-end" : ""}">${t}</em>` : ""}</i>`; };
/* What the schedule says about each show: aired so far, its last date. */
function airFacts(days, now) {
  const f = new Map();
  for (const x of [...days.values()].flat()) {
    const r = f.get(x.e.id) || f.set(x.e.id, { aired: 0, last: null }).get(x.e.id);
    if (x.t <= now && x.ep > r.aired) r.aired = x.ep;
    if (x.ep && x.ep === x.e.episodes) r.last = x.t;
  }
  return f;
}
const airBehind = (e, f) => (inProg(e.status) && f ? Math.max(0, f.aired - (e.progress || 0)) : 0);
function airCard(x, facts, now) {
  const e = x.e, f = facts.get(e.id), behind = airBehind(e, f), sec = (x.t - now) / 1000;
  const tags = [statusLabel(e.status), e.episodes ? `${e.progress || 0} / ${e.episodes} seen` : e.progress ? `${e.progress} seen` : "", ({ installed: "on disk", partial: "partly on disk", online: "online" })[e.capability]].filter(Boolean);
  const ends = f?.last && f.last > x.t ? `Finale ${new Date(f.last).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "";
  return `<button class="aircard" data-a="entry" data-v="${esc(e.id)}"><i class="airp"${airStyle(x)}></i><span class="airx"><b>${esc(e.title)}</b>
    <small>${x.start ? "Starts" : `Episode ${esc(x.ep)}${e.episodes ? " of " + e.episodes : ""} · ${airTime(x)}`}${sec > 0 ? ` · <span class="ac">in ${esc(fmtIn(sec))}</span>` : ""}</small>
    <span class="airtags">${airTag(x) ? `<u class="is-hot">${airTag(x)}</u>` : ""}${behind ? `<u class="is-hot">${behind} behind</u>` : ""}${tags.map((t) => `<u>${esc(t)}</u>`).join("")}${ends ? `<u>${esc(ends)}</u>` : ""}</span></span></button>`;
}
function airMonth(c, days, today) {
  const first = new Date(c.y, c.m, 1), lead = (first.getDay() + 6) % 7, count = new Date(c.y, c.m + 1, 0).getDate(), rows = Math.ceil((lead + count) / 7);
  const cells = Array.from({ length: rows * 7 }, (_, i) => {
    const d = new Date(c.y, c.m, i - lead + 1), k = airKey(d), l = days.get(k) || [], shown = l.length > 3 ? l.slice(0, 2) : l;
    const cls = [d.getMonth() !== c.m ? "is-out" : "", k === airKey(today) ? "is-today" : "", k === c.sel ? "is-sel" : ""].filter(Boolean).join(" ");
    return `<button class="aird ${cls}" data-a="airday" data-v="${k}" aria-label="${esc(d.toDateString())}, ${l.length} episode${l.length === 1 ? "" : "s"}"><span class="airn">${d.getDate()}</span><span class="airts">${shown.map(airTile).join("")}${l.length > 3 ? `<i class="airt airplus">+${l.length - 2}</i>` : ""}</span></button>`; }).join("");
  return `<div class="airgrid" style="--rows:${rows}">${DAYS.map((d) => `<b class="airw">${d}</b>`).join("")}${cells}</div>`;
}
function airWeek(c, days, today) {
  const sel = new Date(`${c.sel}T00:00:00`), mon = new Date(sel.getFullYear(), sel.getMonth(), sel.getDate() - (sel.getDay() + 6) % 7);
  return `<div class="airwk">${Array.from({ length: 7 }, (_, i) => { const d = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + i), k = airKey(d), l = days.get(k) || [];
    return `<div class="aircol${k === airKey(today) ? " is-today" : ""}"><div class="aircolh"><span>${DAYS[i]}</span><b>${d.getDate()}</b></div>${l.map((x) => `<button class="airbig${airSeen(x) ? " is-seen" : ""}" data-a="entry" data-v="${esc(x.e.id)}" title="${esc(x.e.title)}">${airTile(x)}<b>${esc(x.e.title)}</b><small>${x.start ? "Starts" : `E${esc(x.ep)} · ${airTime(x)}`}</small></button>`).join("")}</div>`; }).join("")}</div>`;
}
function CalDesk() {
  const now = Date.now(), today = new Date(now), c = (S.airCal ||= { y: today.getFullYear(), m: today.getMonth(), sel: airKey(today), scope: "all", mode: lsGet("airMode", "month") });
  if (!S.sched || (S.sched.at && now - S.sched.at > 30 * 60e3)) loadSched();
  const days = calEvents(), facts = airFacts(days, now), week = c.mode === "week", first = new Date(c.y, c.m, 1);
  const inMonth = [...days.entries()].filter(([k]) => k.startsWith(airKey(first).slice(0, 7))).flatMap(([, l]) => l);
  const prem = inMonth.filter((x) => airTag(x) === "Premiere").length, fin = inMonth.filter((x) => airTag(x) === "Finale").length;
  const shows = new Map(S.data.spine.entries.map((e) => [e.id, e])), behind = [...facts].reduce((n, [id, f]) => n + airBehind(shows.get(id), f), 0);
  const sum = [`<span><b>${inMonth.filter((x) => !x.start).length}</b> episodes this month</span>`, prem ? `<span><b>${prem}</b> premiere${prem === 1 ? "" : "s"}</span>` : "", fin ? `<span><b>${fin}</b> finale${fin === 1 ? "" : "s"}</span>` : "", behind ? `<span><b>${behind}</b> to catch up</span>` : ""].filter(Boolean).join('<i class="sep"></i>');
  const sel = new Date(`${c.sel}T00:00:00`), picked = days.get(c.sel) || [], seen = new Set();
  const next = [...days.values()].flat().filter((x) => x.t > now).sort((a, b) => a.t - b.t).filter((x) => !seen.has(x.e.id) && seen.add(x.e.id)).slice(0, 4);
  const seg = (a, cur, opts) => opts.map(([v, l]) => `<button class="${cur === v ? "on" : ""}" aria-pressed="${cur === v}" data-a="${a}" data-v="${v}">${l}</button>`).join("");
  const mon = new Date(sel.getFullYear(), sel.getMonth(), sel.getDate() - (sel.getDay() + 6) % 7), sun = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate() + 6), md = (d) => d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const title = week ? `${esc(md(mon))} <span>– ${esc(md(sun))}</span>` : `${esc(monthName(first))} <span>${c.y}</span>`;
  const panel = `<aside class="airside"><h2>${esc(sel.toLocaleDateString(undefined, { weekday: "long" }))} ${sel.getDate()}</h2>${picked.length ? picked.map((x) => airCard(x, facts, now)).join("") : `<p class="sub">Nothing airs this day.</p>${next.length ? `<h3>Next up</h3>${next.map((x) => airCard(x, facts, now)).join("")}` : ""}`}</aside>`;
  return `<div class="airp2"><div class="airhead"><h1 class="hd">${title}</h1>
    <button class="arr" data-a="airgo" data-v="-1" aria-label="Back">${ico("chevl")}</button><button class="arr" data-a="airgo" data-v="1" aria-label="Forward">${ico("chevr")}</button><button class="btn sm" data-a="airtoday">Today</button>
    ${S.sched?.state === "loading" ? '<span class="mut airload"><i class="spin"></i>getting the schedule</span>' : ""}<span class="fill"></span>
    <span class="seg" role="group" aria-label="View">${seg("airmode", c.mode, [["month", "Month"], ["week", "Week"]])}</span><span class="seg" role="group" aria-label="Which shows">${seg("airscope", c.scope, [["all", "All lists"], ["watching", "Watching"]])}</span></div>
    ${week ? "" : `<div class="sum airsum">${sum}</div>`}
    ${week ? airWeek(c, days, today) : `<div class="airbody">${airMonth(c, days, today)}${panel}</div>`}</div>`;
}
function Week() {
  if (!isPhone() && mq("(min-width:1001px)") && aniMode()) return CalDesk();
  const now = Date.now(), list = airingList();
  const head = `<div class="hello"><h1 class="hd">Airing</h1><div class="sum">${list.length ? `<span><b>${list.length}</b> airing on your lists</span>` : "<span>nothing airing on your lists</span>"}</div></div>`;
  if (!aniMode()) return `<div class="wk2">${head}<p class="empty">Connect AniList to see what airs next.</p></div>`;
  const tl = list.map((e, k) => { const t = airAt(e, now), sec = (t - now) / 1000;
    return `<div class="tli${k === 0 ? " first" : ""}"><i class="tdot"></i><div class="tw">${sec > 0 ? "in " + esc(fmtIn(sec)) : "out now"} · ${esc(whenTxt(t, now))}</div>
      <button class="prow" data-a="entry" data-v="${esc(e.id)}">${poster(e.title, e.poster, { c: "pp" })}<span class="pt"><b>${esc(e.title)}</b><small>episode ${esc(e.next_episode?.number ?? "")}</small></span></button></div>`; }).join("");
  const today = new Date(now), airs = list.map((e) => new Date(airAt(e, now)));
  const month = (first, note) => { const y = first.getFullYear(), mo = first.getMonth(), lead = (first.getDay() + 6) % 7, days = new Date(y, mo + 1, 0).getDate();
    const marks = new Set(airs.filter((d) => d.getFullYear() === y && d.getMonth() === mo).map((d) => d.getDate())), thisMo = y === today.getFullYear() && mo === today.getMonth();
    const cells = Array.from({ length: Math.ceil((lead + days) / 7) * 7 }, (_, i) => { const d = i - lead + 1, inM = d >= 1 && d <= days;
      return `<span class="${!inM ? "out" : ""}${thisMo && d === today.getDate() ? " today" : ""}${inM && marks.has(d) ? " has" : ""}">${inM ? d : ""}</span>`; }).join("");
    return `<section class="psec"><div class="sh"><h2>${esc(first.toLocaleDateString(undefined, { month: "long" }))}</h2>${note ? '<span class="mut">a dot: an episode airs</span>' : ""}</div>
      <div class="cal2">${DAYS.map((d) => `<b>${d.slice(0, 2)}</b>`).join("")}${cells}</div></section>`; };
  const y = today.getFullYear(), mo = today.getMonth(), nextM = new Date(y, mo + 1, 1);
  /* The next month joins once something airs in it. */
  const cal = month(new Date(y, mo, 1), true) + (airs.some((d) => d >= nextM) ? month(nextM) : "");
  return `<div class="wk2">${head}<div class="wkgrid"><div class="tline">${tl || '<p class="empty">Nothing airing right now.</p>'}</div><div class="wkcal">${cal}</div></div></div>`;
}

/* Tablet landscape: your lists beside the show page, so picking another
   show only changes the right side. */
const SIDE_TABS = [["watching", "Watching", (e) => inProg(e.status)], ["planning", "Planning", (e) => e.status === "PLANNING"], ["done", "Done", (e) => e.status === "COMPLETED"]];
function paintSide() {
  let el = $("#tside");
  const on = isTabletL() && (S.view === "show" || S.view === "fr") && S.data?.spine?.entries?.length;
  document.body.classList.toggle("has-side", !!on);
  if (!on) { if (el) el.remove(); return; }
  if (!el) { el = document.createElement("aside"); el.id = "tside"; document.body.append(el); }
  const tab = SIDE_TABS.find(([k]) => k === S.sideTab) || SIDE_TABS[0];
  const cur = S.view === "fr" ? frEntry()?.id : showById(S.showId)?.meta?.anilist_id;
  const rows = libCards(S.data.spine.entries.filter(tab[2])).sort(SORT.updated).slice(0, 60);
  const html = `<div class="tsh"><span class="seg" role="group" aria-label="List">${SIDE_TABS.map(([k, l]) => `<button class="${k === tab[0] ? "on" : ""}" data-a="sidetab" data-v="${k}">${l}</button>`).join("")}</span></div>
    <div class="tsl">${rows.map((e) => `<button class="prow${e.id === cur || e.franchise_id === cur ? " on" : ""}" data-a="entry" data-v="${esc(e.id)}">${poster(e.title, e.poster, { c: "pp" })}<span class="pt"><b>${esc(e.title)}</b><small>${e.episodes ? `${e.progress || 0} / ${e.episodes}` : esc(FMT[e.format] || e.format || "")}</small></span></button>`).join("") || '<p class="empty">Nothing here.</p>'}</div>`;
  if (el._html !== html) { el._html = html; el.innerHTML = html; }
}

/* Phones: the show page's Play button and the library's filter sheet sit
   fixed at the bottom. The page's entrance animation leaves a transform on
   their ancestors (fixed would then mean "fixed to the page"), so both are
   drawn in their own layer outside #app: copies whose data-a actions work
   the same. Redrawn after every render and patch. */
function paintPortals() {
  const el = $("#portal");
  if (!el) return;
  let html = "";
  if (isPhone() && (S.view === "show" || S.view === "fr")) {
    const b = $("#app .sp2 .cta .btn.pri"), more = $("#app .frpage .cta [data-a=frmore]");
    if (b) html += `<div class="pbar">${b.outerHTML}${more ? `<button class="btn ic" aria-label="More" data-a="frmore">${ico("dots")}</button>` : ""}</div>`;
  }
  const flt = isPhone() && S.view === "library" && document.body.classList.contains("flt-open") && $("#app .lib2 .filters");
  if (flt) html += `<i class="fltscrim" data-a="fltclose"></i><div class="lib2 fsheet">${flt.outerHTML.replace('<div class="filters">', '<div class="filters">' + grab + '<b class="fsh">Sort & filter</b>')}</div>`;
  else document.body.classList.remove("flt-open");
  if (el._html !== html) { el._html = html; el.innerHTML = html; }
  document.body.classList.toggle("has-pbar", html.includes('class="pbar"'));
}
/* Search on touch screens (Elias, 2026-10-27: "rework the design of
   search"): a full page over the art with the field at the thumb.
   Before typing: recent searches, your lists, what you're watching.
   Typing: library rows, AniList as a poster grid, matching settings. Same
   rows (.mi, data-i) as the PC list, so Enter and the arrows still work. */
const touchSearch = () => isPhone() || mq("(pointer:coarse)");
const SR_RECENT = "srRecent";
function srRemember(qq) {
  qq = String(qq || "").trim();
  if (qq.length < 2) return;
  lsSet(SR_RECENT, [qq, ...lsGet(SR_RECENT, []).filter((x) => x.toLowerCase() !== qq.toLowerCase())].slice(0, 6));
}
function srHTMLTouch() {
  const qq = S.q.trim(), ql = qq.toLowerCase();
  srPv = [];
  let i = 0, h = "";
  const mi = (attrs, cls, inner) => { srPv.push(null); return `<div class="mi ${cls}" tabindex="-1" data-i="${i++}" ${attrs}>${inner}</div>`; };
  const sec = (t, extra = "") => `<div class="srsec"><h2>${t}</h2><span class="fill"></span>${extra}</div>`;
  if (!qq) {
    const recent = lsGet(SR_RECENT, []);
    if (recent.length) h += sec("Recent", '<button class="link-btn" data-a="srclr">Clear</button>') + `<div class="srchips">${recent.map((r) => `<button class="chip" data-a="srq" data-v="${esc(r)}">${ico("clock")}${esc(r)}</button>`).join("")}</div>`;
    const sp = S.data?.spine;
    if (aniMode() && sp?.entries?.length) {
      const n = (k) => new Set(sp.entries.filter((e) => e.status === k).map(libKey)).size;
      const lists = LISTS.filter(([k]) => n(k)).map(([k, l]) => `<button class="chip" data-a="srlist" data-v="${k}">${esc(l)}<span class="n">${n(k)}</span></button>`).join("");
      h += sec("Your lists") + `<div class="srchips">${lists}<button class="chip" data-a="srgo" data-v="week">${ico("cal")}This week</button></div>`;
    }
    const up = S.data && nextUpInfo(), shows = S.data ? [up?.s, ...keepWatching(up && up.s.id).map((x) => x.s)].filter(Boolean).slice(0, 8) : [];
    if (shows.length) h += sec("Watching") + `<div class="srposters">${shows.map((s) => mi(`data-a="open" data-v="${esc(s.id)}"`, "sp", `${poster(s.title, s.poster)}<b>${esc(s.title)}</b>`)).join("")}</div>`;
    if (!h) h = '<p class="srnone">Type a show, a setting or a command.</p>';
  } else {
    const local = (S.data?.shows || []).filter((s) => `${s.collection || ""} ${s.title}`.toLowerCase().includes(ql))
      .map((s) => ({ r: Math.min(srRank(s.title, ql), srRank(s.collection, ql)), s })).sort((a, b) => a.r - b.r).slice(0, 6);
    const have = new Set(local.map((x) => x.s.meta?.anilist_id).filter(Boolean));
    const remote = (alSearchable() && S.alres || []).map((x, k) => ({ r: srRank(candLabel(x), ql), x, k })).filter((y) => !have.has(y.x.id)).sort((a, b) => a.r - b.r).slice(0, 9);
    if (local.length) h += sec("Your library") + local.map(({ s }) => { const sp = spineOf(s), t = pickTarget(s);
      return mi(`data-a="open" data-v="${esc(s.id)}"`, "srrow", `${poster(s.title, s.poster, { c: "t" })}<span class="tx"><b>${hl(s.title, qq)}</b><small>${esc(sp ? statusLabel(sp.status) : `${epTotal(s)} episodes`)}${t ? " · " + esc(KIND_WORD[t.kind].toLowerCase() + " " + epCode(t.sea, t.ep)) : ""}</small></span>${ico("chevr")}`); }).join("");
    if (remote.length) h += sec("On AniList") + `<div class="srgrid">${remote.map(({ x, k }) => { const e = aniMode() ? S.data.spine.entries.find((z) => z.id === x.id) : null;
      return mi(`data-a="catpick" data-v="${k}"`, "srcard", `<span class="pw">${poster(candLabel(x), x.coverImage?.extraLarge)}${e ? `<span class="tg">${esc(statusLabel(e.status))}</span>` : '<span class="tg">not listed</span>'}</span><b>${hl(candLabel(x), qq)}</b><small>${esc([x.seasonYear || x.startDate?.year, FMT[x.format] || x.format].filter(Boolean).join(" · "))}</small>`); }).join("")}</div>`;
    const sets = SETTINGS_INDEX.filter(([t, tab, w]) => srMatch(`${t} ${w} ${tab}`.toLowerCase(), ql)).slice(0, 4);
    if (sets.length) h += sec("Settings") + `<div class="srbox">${sets.map(([t, tab, , key]) => { const on = key && !!S.settings?.[key], tl = SET_TABS.find(([k]) => k === tab)[1];
      return mi(`data-a="setgo" data-v="${esc(t)}" data-t="${tab}"`, "srrow set", `<i class="sic">${ico(SET_ICONS[tab] || "sliders")}</i><span class="tx"><b>${esc(t)}</b><small>${esc(tl)}</small></span>${key && S.settings ? `<button class="switch" role="switch" aria-checked="${on}" aria-label="${esc(t)}" data-a="srsw" data-v="${key}"></button>` : ico("chevr")}`); }).join("")}</div>`;
    const cmds = COMMANDS.map((c, k) => [c, k]).filter(([[t, w]]) => srMatch(`${t} ${w}`.toLowerCase(), ql)).slice(0, 3);
    if (cmds.length) h += sec("Do") + `<div class="srchips">${cmds.map(([[t], k]) => `<button class="chip" data-a="cmd" data-v="${k}">${ico("arrowr")}${esc(t)}</button>`).join("")}</div>`;
    if (alSearchable() && (S.alBusy || (S.alq !== qq && qq.length >= 2))) h += '<p class="srnone"><i class="spin"></i> searching AniList…</p>';
    if (!h) h = `<p class="srnone">Nothing for “${esc(qq)}”${alSearchable() ? " in your library, on AniList or in settings" : " in your library or settings"}.</p>`;
  }
  return `<div class="pal2 touch"><div class="plist">${grab}<h1 class="hd srhd">Search</h1>${h}</div></div>`;
}
/* The keyboard (Elias: "the entire page goes up when I open my keyboard").
   index.html asks Chrome/Samsung Internet to shrink the page instead of
   pushing it up (interactive-widget=resizes-content). Browsers that ignore
   that (iPhone) still push: there, while search is open, the search page is
   fitted to the part of the screen above the keyboard (visualViewport). */
const vv = window.visualViewport;
function fitPal() {
  const p = $("#pal");
  if (!p) return;
  const on = vv && S.menu === "sr" && touchSearch() && Math.round(vv.height) < innerHeight;
  p.style.height = on ? vv.height + "px" : "";
  p.style.transform = on && vv.offsetTop ? `translateY(${vv.offsetTop}px)` : "";
}
vv?.addEventListener("resize", fitPal);
vv?.addEventListener("scroll", fitPal);
/* Opening a result remembers what was typed (Recent). */
document.addEventListener("click", (e) => { if (S.menu === "sr" && S.q.trim() && e.target.closest?.("#sr .mi, #sr [data-a=cmd]")) srRemember(S.q); }, true);

/* Settings on a phone (S5): a grouped list; a section opens as its own page. */
function setSummary(k, s) {
  const ani = S.ani || {}, st = s.stream || {}, sk = s.skip || {};
  return {
    appearance: "accent, background art, motion, zoom",
    library: s.library_root || "no folder chosen yet",
    mpv: `fullscreen: ${({ on: "on", off: "off", own: "my config" })[s.mpv?.fullscreen] || "on"}`,
    playback: `openings: ${({ off: "off", prompt: "ask", auto: "auto" })[sk.op] || "ask"} · endings: ${({ off: "off", prompt: "ask", auto: "auto" })[sk.ed] || "ask"}`,
    online: s.tools?.["ani-cli"] ? `${st.mode === "dub" ? "dub" : "sub"} · ${st.quality ? QLABEL[st.quality] || st.quality + "p" : "best"}` : "ani-cli not installed",
    anilist: ani.connected ? `connected as ${ani.user || "you"}` : "not connected",
    phone: "pair a phone with a QR code",
    keys: "for a keyboard",
    data: "config file, data folder",
  }[k] || "";
}
const SET_ICONS = { appearance: "edit", library: "folder", playback: "play", mpv: "tv", online: "download", anilist: "star", phone: "tv", keys: "search", data: "clock" };
function settingsList(tabs, s) {
  return `<div class="set2 phone"><div class="hello"><h1 class="hd">Settings</h1></div>
    <button class="slsearch" data-a="sropen">${ico("search")}<span>search settings — try “vpn”</span></button>
    <div class="slist">${tabs.filter(([k]) => k !== "keys").map(([k, l]) => `<button class="sli" data-a="setopen" data-v="${k}"><i class="sic">${ico(SET_ICONS[k] || "chevr")}</i><span><b>${l}</b><small>${esc(setSummary(k, s))}</small></span>${ico("chevr")}</button>`).join("")}</div></div>`;
}

/* ---------- phone remote (Decision 43) ---------- */
/* On phones (and narrow or touch screens) a mini player sits above the
   status line while mpv plays on the PC; tapping it opens the remote, a
   full page in the show page's style over the playing show's art. The
   page is built once per state change and patched in place (rebuilding it
   replayed its entrance); the clock and progress tick between polls. */
const phoneUI = () => !!S.settings?.phone || matchMedia("(max-width:700px), (pointer:coarse)").matches;
const tclock = (x) => { x = Math.max(0, Math.floor(x || 0)); const h = Math.floor(x / 3600), m = Math.floor(x / 60) % 60, sec = String(x % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`; };
const LANGS = { jpn: "Japanese", ja: "Japanese", eng: "English", en: "English", ger: "German", deu: "German", de: "German", spa: "Spanish", es: "Spanish", fre: "French", fra: "French", fr: "French", ita: "Italian", it: "Italian", por: "Portuguese", pt: "Portuguese", rus: "Russian", ru: "Russian", chi: "Chinese", zho: "Chinese", zh: "Chinese", kor: "Korean", ko: "Korean", ara: "Arabic", ar: "Arabic", und: "" };
const langName = (c) => { if (!c) return ""; const k = c.toLowerCase(); if (k in LANGS) return LANGS[k]; try { return new Intl.DisplayNames(["en"], { type: "language" }).of(k) || c; } catch { return c; } };
const trackName = (t, i) => { const l = langName(t.lang), ti = t.title || ""; return ti && l && !ti.toLowerCase().includes(l.toLowerCase()) ? `${ti} · ${l}` : ti || l || `Track ${i + 1}`; };
const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
const SEGNAME = { op: "opening", ed: "ending" };
const LIVE_KEYS = ["position", "duration", "paused", "volume", "tracks", "speed", "sub_delay", "segments", "stop_after"];
function livePos() {
  const l = S.live;
  if (!l || typeof l.position !== "number") return 0;
  const p = l.position + (l.paused || S.data?.now?.loading ? 0 : ((Date.now() - l.at) / 1000) * (l.speed || 1));
  return l.duration ? Math.min(p, l.duration) : p;
}
/* The opening/ending playing right now (the same window mpv's own skip prompt uses). */
const segNow = () => { const p = livePos(); return (S.live?.segments || []).find((x) => p >= x.start - 1 && p < x.end - 1) || null; };
function liveFrom(now) {
  if (!now.playing) { S.live = null; return; }
  if (S.live?.hold > Date.now() && S.live.ep === now.episode_id) return; // a button just answered: keep its fresher state
  S.live = { ep: now.episode_id, at: Date.now(), ...Object.fromEntries(LIVE_KEYS.map((k) => [k, now[k]])) };
  S.live.paused = !!S.live.paused;
}
let rmtSig = "";
function rmtEpisodes(ctx, nowId) {
  if (!ctx) return "";
  const { s, sea } = ctx, eps = [...sea.episodes].sort((x, y) => x.number - y.number);
  const at = Math.max(0, eps.findIndex((e) => e.id === nowId));
  const tiles = eps.map((ep, i) => {
    const rt = epThumbOf(sea.thumbs, ep), art = rt || s.poster || s.banner, cur = ep.id === nowId;
    const online = String(ep.id).startsWith("stream:"), mins = minsOf(ep.duration || ep.length);
    const meta = cur ? "playing now" : `${mins ? mins + " min · " : ""}${online ? "online" : "disk"}`;
    return tileHTML({ cls: ["ep", cur ? "resume" : "", ep.watched && !cur ? "watched" : ""].filter(Boolean).join(" "), a: cur ? "rmtclose" : "playep", v: ep.id, i,
      art, pos: rt ? "50% 50%" : "50% 22%", num: "E" + String(ep.number).padStart(2, "0"), watched: ep.watched && !cur, title: epTitle(s, sea, ep) || "Episode " + ep.number, meta, menu: "" })
      .replace(/<button class="dots"[\s\S]*?<\/button>/, "");
  }).join("");
  return `<div class="eps-head"><h2>${esc(sea.label || sea.name || "Episodes")}</h2><span class="rk">${at + 1} of ${eps.length}</span></div><div class="eps strip mr" data-at="${at}">${tiles}</div>`;
}
function paintRemote() {
  const el = $("#rmt"), now = S.data?.now;
  if (!now || !phoneUI()) {
    if (rmtSig) { el.innerHTML = ""; rmtSig = ""; document.body.classList.remove("rmt-on", "rmt-open"); if (S.rmtOpen) { S.rmtOpen = false; paintCanvas(); ovClosed("rmt"); } }
    S.rmtOpen = false;
    return;
  }
  const ctx = findEpContext(now.episode_id), s = ctx?.s, l = S.live || {}, tr = l.tracks || { audio: [], sub: [] };
  const hasNext = !!now.next && (now.next.source === "local" || now.next.source === "online");
  const sig = JSON.stringify([now.episode_id, now.show, now.label, !!now.loading, !!l.paused, Math.round(l.duration || 0), l.volume, tr, l.speed, l.sub_delay, !!l.stop_after, l.segments, S.rmtOpen, hasNext, s?.poster]);
  if (sig !== rmtSig && !S.rmtDrag) { // a held slider keeps its place; the next tick redraws
    rmtSig = sig;
    document.body.classList.add("rmt-on");
    const paused = !!l.paused, busy = now.loading ? "disabled" : "", epNo = ctx ? "E" + String(ctx.ep.number).padStart(2, "0") : now.label;
    const pp = (c) => `<button class="btn pri ic ${c}" data-a="rmt" data-v="toggle" aria-label="${paused ? "Play" : "Pause"}" ${busy}>${ico(paused ? "play" : "pause")}</button>`;
    const skip = (c) => `<button class="btn ${c}" data-a="rmt" data-v="skip" hidden>${ico("next")}<span>Skip opening</span></button>`;
    const thumb = s?.poster ? `<img alt="" src="${esc(s.poster)}">` : `<i class="rph" style="--h:${hueOf(now.show)}"></i>`;
    const seg = (k, opts, label) => `<span class="seg" role="group" aria-label="${label}">${opts.map(([v, t, on]) => `<button class="${on ? "on" : ""}" aria-pressed="${!!on}" data-a="rmttrack" data-k="${k}" data-v="${v}">${esc(t)}</button>`).join("")}</span>`;
    const subOn = tr.sub.some((t) => t.selected), delay = Number(l.sub_delay || 0), speed = Number(l.speed || 1);
    const opts = [
      tr.audio.length > 1 ? `<div class="ropt"><span class="rk">audio</span>${seg("audio", tr.audio.map((t, i) => [t.id, trackName(t, i), t.selected]), "Audio")}</div>` : "",
      tr.sub.length ? `<div class="ropt"><span class="rk">subtitles</span>${seg("sub", [["no", "Off", !subOn], ...tr.sub.map((t, i) => [t.id, trackName(t, i), t.selected])], "Subtitles")}</div>` : "",
      subOn ? `<div class="ropt"><span class="rk">subtitle timing · this episode</span><div class="rstep"><button class="btn ic" data-a="rmtdelay" data-v="-0.1" aria-label="Subtitles 0.1 s earlier">${ico("minus")}</button><b>${delay > 0 ? "+" : delay < 0 ? "−" : ""}${Math.abs(delay).toFixed(1)} s</b><button class="btn ic" data-a="rmtdelay" data-v="0.1" aria-label="Subtitles 0.1 s later">${ico("plus")}</button>${delay ? '<button class="btn" data-a="rmtdelay" data-v="0">Reset</button>' : `<span class="rk">${"earlier ← → later"}</span>`}</div></div>` : "",
      typeof l.speed === "number" ? `<div class="ropt"><span class="rk">speed</span>${seg("speed", SPEEDS.map((v) => [v, v + "×", Math.abs(v - speed) < 0.01]), "Speed")}</div>` : "",
      typeof l.volume === "number" ? `<div class="ropt"><span class="rk">volume</span><label class="rvol">${ico("vol")}<input class="rrange" type="range" min="0" max="130" step="5" value="${Math.round(l.volume)}" data-a="rmtvol" aria-label="Volume"><span>${Math.round(l.volume)}%</span></label></div>` : "",
    ].join("");
    const segBand = (l.segments || []).filter(() => l.duration).map((x) => `<i class="rsegb" style="left:${(x.start / l.duration) * 100}%;width:${((x.end - x.start) / l.duration) * 100}%"></i>`).join("");
    const view = `<div class="sp2 rv">
        ${grab}<div class="rtop"><button class="backbtn" data-a="rmtclose">${ico("chevd")}Close</button><span class="rpc"><i class="led"></i>${now.loading ? "starting on the PC…" : "playing on the PC"}</span></div>
        <div class="rhero"><div class="meta"><i class="mdot"></i><span>${esc(now.label)}${ctx?.ep && (ctx.ep.duration || ctx.ep.length) ? ` · ${minsOf(ctx.ep.duration || ctx.ep.length)} min` : ""}</span></div>
          <h1>${esc(s?.title || now.show)}</h1>${ctx ? `<p class="eptitle">${esc(epTitle(s, ctx.sea, ctx.ep) || "Episode " + ctx.ep.number)}</p>` : ""}</div>
        <div class="rseek"><div class="rtrack">${segBand}<input class="rrange" type="range" min="0" max="${Math.max(1, Math.round(l.duration || 0))}" step="1" value="${Math.round(livePos())}" data-a="rmtseek" aria-label="Position" ${l.duration && !now.loading ? "" : "disabled"}></div>
          <div class="rtimes"><span class="rcur">${tclock(livePos())}</span><span class="rmid"></span><span class="rleft">-${tclock((l.duration || 0) - livePos())}</span></div></div>
        <div class="rskip">${skip("big")}</div>
        <div class="cta rctl"><button class="btn big ic" data-a="rmt" data-v="stop" aria-label="Stop">${ico("stop")}</button><button class="btn big ic" data-a="rmt" data-v="back" aria-label="Back 10 seconds" ${busy}>${ico("rew")}</button>${pp("big rpp")}<button class="btn big ic" data-a="rmt" data-v="fwd" aria-label="Forward 10 seconds" ${busy}>${ico("fwd")}</button><button class="btn big ic" data-a="rmt" data-v="next" aria-label="Next episode" ${hasNext && !now.loading ? "" : "disabled"}>${ico("next")}</button></div>
        ${opts ? `<div class="ropts">${opts}</div>` : ""}
        <div class="rsleep"><div><b>Stop after this episode</b><p>${l.stop_after ? `mpv closes when ${esc(epNo)} ends. No auto-play.` : "Otherwise the next episode starts by itself."}</p></div><button class="switch" role="switch" aria-checked="${!!l.stop_after}" aria-label="Stop after this episode" data-a="rmtsleep"></button></div>
        ${rmtEpisodes(ctx, now.episode_id)}
      </div>`;
    const barIn = `${thumb}<div class="rt"><b>${esc(s?.title || now.show)}</b><span><i class="led"></i>${now.loading ? "starting on the PC…" : esc(epNo)}<span class="rbt"></span></span></div>${skip("rbskip")}${pp("rp")}<i class="rprog"><i></i></i>`;
    /* Patch in place: rebuilding the bar or the page would replay their entrance. */
    const focus = document.activeElement?.closest?.("#rmt [data-a]"), fk = focus && [focus.dataset.a, focus.dataset.v, focus.dataset.k];
    let bar = el.querySelector(".rbar"), vw = el.querySelector(".rview");
    if (!bar) { el.innerHTML = '<div class="rbar" data-a="rmtopen" role="button" tabindex="0" aria-label="Open the remote"></div>'; bar = el.firstChild; vw = null; }
    bar.innerHTML = barIn;
    if (!S.rmtOpen) vw?.remove();
    else {
      const fresh = !vw;
      if (fresh) { bar.insertAdjacentHTML("afterend", '<div class="rview" role="dialog" aria-modal="true" aria-label="Remote"></div>'); vw = el.querySelector(".rview"); }
      const strip = vw.querySelector(".eps.strip"), sl = strip?.scrollLeft;
      vw.innerHTML = view;
      for (const x of vw.querySelectorAll("[data-bg]")) { if (x.dataset.bg) { x.style.backgroundImage = cssUrl(x.dataset.bg); x.style.backgroundPosition = x.dataset.bp || "50% 50%"; } }
      const st = vw.querySelector(".eps.strip");
      if (st) st.scrollLeft = fresh || sl == null ? Math.max(0, Number(st.dataset.at) * 234 - 40) : sl;
    }
    if (fk) [...el.querySelectorAll("[data-a]")].find((x) => x.dataset.a === fk[0] && x.dataset.v === fk[1] && x.dataset.k === fk[2])?.focus({ preventScroll: true });
    document.body.classList.toggle("rmt-open", !!S.rmtOpen);
  }
  rmtTick();
}
/* The clock between polls: progress, slider, times and the skip button. */
function rmtTick() {
  const el = $("#rmt"), l = S.live;
  if (!rmtSig || !l) return;
  const pos = livePos(), dur = l.duration || 0, pct = dur ? (pos / dur) * 100 : S.data?.now?.percent || 0, sg = segNow();
  const bar = el.querySelector(".rprog i");
  if (bar) bar.style.width = pct + "%";
  const bt = el.querySelector(".rbt");
  if (bt) bt.textContent = dur ? ` · ${tclock(pos)}` : "";
  for (const b of el.querySelectorAll('[data-v="skip"]')) { b.hidden = !sg; if (sg) b.querySelector("span").textContent = `Skip ${SEGNAME[sg.kind] || "this"}`; }
  const mid = el.querySelector(".rmid");
  if (mid) mid.textContent = sg ? SEGNAME[sg.kind] || "" : "";
  if (S.rmtDrag) return;
  const r = el.querySelector('.rseek input');
  if (r) { r.value = Math.round(pos); r.style.setProperty("--p", pct + "%"); el.querySelector(".rcur").textContent = tclock(pos); el.querySelector(".rleft").textContent = "-" + tclock(dur - pos); }
  const v = el.querySelector('.rvol input');
  if (v) v.style.setProperty("--p", (v.value / 130) * 100 + "%");
}
setInterval(() => { if (rmtSig && !S.live?.paused) rmtTick(); }, 500);
async function remoteSend(action, value) {
  const l = S.live;
  if (action === "toggle" && l) { l.position = livePos(); l.at = Date.now(); l.paused = !l.paused; l.hold = Date.now() + 1500; paintRemote(); }
  let r;
  try { r = await api("/api/control", value === undefined ? { action } : { action, value }); } catch { r = { error: "Could not reach the PC." }; }
  if (r.error) { toast(r.error); pollSoon(200); return; }
  if (action === "stop" || action === "next") { toast(action === "next" ? "Starting the next episode…" : "Stopped"); if (action === "stop") { S.rmtOpen = false; paintCanvas(); ovClosed("rmt"); } pollSoon(800); return; }
  if (action === "stop_after") { if (l) { l.stop_after = r.stop_after; l.hold = Date.now() + 1500; } toast(r.stop_after ? "Stops after this episode" : "Auto-play is back on"); paintRemote(); return; }
  if (r.position == null) { pollSoon(300); return; } // skipped an ending: the episode finished
  S.live = { ...(l || {}), ep: S.data?.now?.episode_id, at: Date.now(), hold: Date.now() + 1500, ...Object.fromEntries(LIVE_KEYS.filter((k) => k in r).map((k) => [k, r[k]])) };
  S.live.paused = !!S.live.paused;
  paintRemote();
}
/* Dragging a slider: the numbers follow the finger, the change is sent on release. */
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t.dataset?.a === "rmtseek") {
    S.rmtDrag = true;
    const dur = S.live?.duration || 0, v = Number(t.value);
    t.style.setProperty("--p", (dur ? (v / dur) * 100 : 0) + "%");
    $("#rmt .rcur").textContent = tclock(v); $("#rmt .rleft").textContent = "-" + tclock(dur - v);
  } else if (t.dataset?.a === "rmtvol") { S.rmtDrag = true; t.style.setProperty("--p", (t.value / 130) * 100 + "%"); t.nextElementSibling.textContent = t.value + "%"; }
});
document.addEventListener("keydown", (e) => {
  if (S.rmtOpen && e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); A.rmtclose(); }
  else if (e.target.closest?.(".rbar") && (e.key === "Enter" || e.key === " ") && !e.target.closest("button")) { e.preventDefault(); A.rmtopen(); }
}, true);
/* A slider let go without moving sends no change: still end the hold. */
for (const ev of ["pointerup", "touchend", "pointercancel"]) document.addEventListener(ev, () => { if (S.rmtDrag) setTimeout(() => { S.rmtDrag = false; paintRemote(); }, 50); }, true);
addEventListener("resize", () => paintRemote());

/* ---------- context menu ---------- */
function openCm(id, x, y) {
  if (S.view === "fr" && frCm(id, x, y)) return;
  const ctx = findEpContext(id);
  if (!ctx) return;
  const { s, ep, sea } = ctx, m = $("#cm"), title = epTitle(s, sea, ep);
  m.innerHTML = `<div class="cap">${esc(epCode(sea, ep))}${title ? " · " + esc(title) : ""}</div>` + cmItems({ id, ep, files: ep.files || [], verId: id, online: false });
  cmShow(m, x, y);
}

/* ---------- overlays ---------- */
const recVal = (side) => { const b = []; if (side?.status) b.push(statusLabel(side.status)); if (side && side.score != null && side.score !== 0) b.push("Score " + side.score); return b.length ? b.join(" · ") : "—"; };
function AniDlg() {
  const a = S.ani || {}, exp = a.expires_at ? new Date(a.expires_at * 1000).toLocaleDateString() : null;
  return `<h2 id="dt">AniList account</h2>
  <p class="sub">${a.connected ? `Connected as <b>${esc(a.user)}</b>${exp ? ` · token expires ${exp}` : ""}` : a.expired ? "The saved token has expired — reconnect below." : "Not connected. Your library runs on local watch state."}</p>
  ${a.connected ? '<div class="bulk"><button class="btn" data-a="aniout">Disconnect</button></div>' : ""}
  ${!a.connected && a.client_id_configured ? '<div class="bulk"><button class="btn pri" data-a="anioauth">Connect via AniList</button></div>' : ""}
  ${!a.connected && location.port !== "8787" ? `<p class="hint-line">The hub runs on port ${esc(location.port || "80")}, but the bundled AniList client redirects to port 8787 — one-click connect needs the hub on its default port, or your own client registered for this port (add <code>anilist_client_id</code> to the config file).</p>` : ""}
  ${!a.connected ? `<p class="hint-line">The browser flow opens anilist.co for one Authorize click. Prefer a token? AniList → Settings → Apps → Create New Token, then paste it below.</p>
    <div class="row" style="margin-top:12px"><input id="tok" type="text" placeholder="…or paste an AniList token" autocomplete="off" spellcheck="false"><button class="btn" data-a="anitoken">Use token</button></div>` : ""}
  <div class="msg" id="animsg"></div>
  <div class="actions"><span></span><button class="btn" data-a="close">Close</button></div>`;
}
/* The tour's last page (mockup): what the hub found — ✓ found, ! needed, – optional. */
function tourFound(s, a, n, ok) {
  const row = (st, k, v) => `<div class="${st}"><span class="okc">${st === "ok" ? ico("check") : st === "bad" ? "!" : "–"}</span><b>${k}</b><span class="v">${esc(v)}</span></div>`;
  return [
    row(ok ? "ok" : "bad", "Library", ok ? `${s.library_root} · ${n} show${n === 1 ? "" : "s"}` : s.library_root ? `${s.library_root} · can't be read` : "no folder chosen yet"),
    row(s.tools?.mpv ? "ok" : "bad", "Player", s.tools?.mpv ? `mpv${s.mpv_version ? " " + s.mpv_version : ""} found` : "mpv not found — install it to play files"),
    row(s.tools?.["ani-cli"] ? "ok" : "opt", "Online episodes", s.tools?.["ani-cli"] ? `ani-cli${s.stream?.version ? " " + s.stream.version : ""} found` : "ani-cli not installed — optional"),
    row(a.connected ? "ok" : "opt", "AniList", a.connected ? `connected${a.user ? " as " + a.user : ""}` : "not connected"),
  ].join("");
}
function Tour() {
  const T = S.tour, s = S.settings || {}, a = S.ani || {}, n = S.data.shows.length;
  const ok = s.library_root && s.scan?.state === "ok", eps = S.data.shows.reduce((x, y) => x + epTotal(y), 0);
  const b = [
    `<h2 id="dt">Where's your anime?</h2><p class="sub">Anime Hub plays what is in your library folder, in mpv.</p><div class="path">${libPath("tour", s)}${dirBtn("tour")}</div>${DirPick("tour")}
     <div class="found"><i class="dot ${ok ? "ok" : ""}"></i>${ok ? `Found ${n} show${n === 1 ? "" : "s"} · ${eps} episodes` : s.library_root ? "There is a problem with this folder — try another one." : "Choose the folder that holds your anime."}</div>`,
    `<h2 id="dt">Connect AniList?</h2><p class="sub">Your AniList lists become the sections of your library. You can change this later in Settings.</p><div class="choices">
      <button class="choice ${T.choice === "connect" ? "on" : ""}" data-a="tourpick" data-v="connect"><b>Connect AniList</b><span>Sync progress and scores with your account.</span></button>
      <button class="choice ${T.choice === "skip" ? "on" : ""}" data-a="tourpick" data-v="skip"><b>Skip for now</b><span>Everything runs on what is on this device.</span></button></div>`,
    `<h2 id="dt">You're set</h2><p class="sub">Here's what Anime Hub found.${a.connected ? " Two last switches — both can change later." : " Connect AniList below, or any time from Settings."}</p>
     <div class="sum2">${tourFound(s, a, n, ok)}</div>
     ${!a.connected ? '<button class="btn pri" data-a="tourconnect" style="margin-top:14px">Connect AniList</button>' : ""}
     <div class="${a.connected ? "" : "off"}">${srow("Track episodes", "After you watch an episode, update your AniList progress.", tgl("auto_progress_sync", "Track episodes", s.auto_progress_sync))}${srow("Add shows automatically", "Watching a show that isn't on your lists adds it to Watching.", tgl("auto_add_watch", "Add shows automatically", s.auto_add_watch))}</div>`,
  ][T.step];
  return `<div class="steps">${[0, 1, 2].map((i) => `<i class="${i === T.step ? "on" : i < T.step ? "done" : ""}"></i>`).join("")}</div>${b}
  <div class="actions">${T.step ? '<button class="btn" data-a="tourback">Back</button>' : '<button class="btn" data-a="tourskip">Skip</button>'}<button class="btn pri" data-a="tournext">${T.step === 2 ? "Open library" : "Continue"}</button></div>`;
}
function Rec() {
  const { res, pick } = S.rec, n = res.conflicts.length;
  const facts = res.facts.map((f) => f.kind === "progress" ? `<li>${esc(f.show)} — watch progress synced to EP ${f.local} (was ${f.remote})</li>` : `<li>${esc(f.show)} — episodes up to E${f.remote} marked watched locally</li>`).join("");
  return `<h2 id="dt">${n ? `${n} difference${n === 1 ? "" : "s"} to review` : "AniList connected"}</h2>
  <p class="sub">Watched-episode counts always keep the higher of the two — that part is automatic${facts ? ":" : "."}</p>${facts ? `<ul class="summary">${facts}</ul>` : ""}
  ${n ? `<p class="sub" style="margin-top:14px">These shows differ in list status or score — choose what to keep.</p>
    <div class="bulk"><button class="btn" data-a="recall" data-v="anilist">AniList wins everywhere</button><button class="btn" data-a="recall" data-v="local">Local wins everywhere</button></div>
    <div class="rh"><span>Show</span><span>AniList</span><span>Local</span><span>Keep</span></div>
    ${res.conflicts.map((c) => { const k = pick[c.media_id]; return `<div class="rr"><span class="who"><span><b>${esc(c.title)}</b><small>${esc(c.diff || "")}</small></span></span><span class="v ${k === "anilist" ? "keep" : ""}">${esc(recVal(c.remote))}</span><span class="v ${k === "local" ? "keep" : ""}">${esc(recVal(c.local))}</span>
      <span class="seg" role="group" aria-label="Keep for ${esc(c.title)}"><button class="${k === "anilist" ? "on" : ""}" aria-pressed="${k === "anilist"}" data-a="recset" data-m="${c.media_id}" data-v="anilist">AniList</button><button class="${k === "local" ? "on" : ""}" aria-pressed="${k === "local"}" data-a="recset" data-m="${c.media_id}" data-v="local">Local</button></span></div>`; }).join("")}` : ""}
  <div class="actions"><button class="btn" data-a="recclose">Skip</button><button class="btn pri" data-a="recapply">${n ? `Apply ${n} choice${n === 1 ? "" : "s"}` : "Close"}</button></div>`;
}
/* You finished a show (Decision 25 → 38): poster, the big title, rate it
   (1–10 pills) with ♥ beside, and what comes next in its franchise. */
function Done() {
  const c = S.modal.c, e = S.data?.spine?.entries?.find((x) => x.id === c.media_id) || null;
  const p = frMemo.get(c.media_id), nx = p ? p.entries.filter((x) => x.group === "main").find((x, i, all) => all[i - 1]?.id === c.media_id) || p.entries.find((x) => x.group === "extra" && !S.data.spine?.entries?.some((y) => y.id === x.id)) : null;
  const nxOn = nx && S.data.spine?.entries?.find((y) => y.id === nx.id);
  return `<div class="dn"><div class="dnp">${poster(c.title || "", e?.poster || null)}</div><div class="dnb">
    <span class="cap">you finished</span><h2 id="dt">${esc(c.title || "a show")}</h2>
    <div class="facts">${[e?.episodes ? `${e.episodes} of ${e.episodes}` : "", e?.format ? FMT[e.format] || e.format : "", S.ani?.connected ? "marked Completed on AniList" : "saved on this device"].filter(Boolean).map(esc).join('<i class="sep"></i>')}</div>
    <span class="cap" style="margin-top:22px">how was it?</span>
    <div class="scrow"><div class="score" id="dn-pills" role="radiogroup" aria-label="Score">${Array.from({ length: 10 }, (_, i) => `<button role="radio" aria-checked="false" data-a="dnscore" data-v="${i + 1}">${i + 1}</button>`).join("")}</div><i class="vs"></i>
      <button class="heart" id="dn-fav" aria-label="Favourite" aria-pressed="false" data-a="dnfav">${ico("heart")}</button></div>
    <input type="hidden" id="dn-score" value=""><input type="hidden" id="dn-status" value="COMPLETED"><span id="dn-max" hidden></span>
    ${nx ? `<div class="dnnext">${poster(nx.title || nx.label, nx.poster || null, { c: "t" })}<span class="tx"><b>${esc(nx.title || nx.label)}</b><small>${esc([nx.format_label, "next in this franchise", nxOn ? "on your list" : "not on your list"].filter(Boolean).join(" · "))}</small></span>${nxOn ? "" : `<button class="btn sm" data-a="dnplan" data-v="${nx.id}">+ Planning</button>`}</div>` : ""}
  </div></div>
  <div class="actions"><span class="note2 mono">the score saves when you close</span><button class="btn" data-a="dnskip">Skip</button><button class="btn pri" data-a="dnsave">Done<kbd class="kb">↵</kbd></button></div>`;
}
/* The overlay is built once per dialog; later paints (tour steps, option picks) swap only the dialog body
   so the backdrop and entrance don't replay. Closing fades out before the node is removed. */
function paintModal(focus) {
  const m = S.modal, el = $("#modal");
  if (!m) {
    const o = el.querySelector(".overlay:not(.leave)");
    if (o) { o.classList.add("leave"); setTimeout(() => { if (!S.modal) el.innerHTML = ""; }, 200); }
    return;
  }
  const body = (isPhone() && m.t !== "tour" ? grab : "") + { ani: AniDlg, tour: Tour, rec: Rec, done: Done, src: SrcDlg, vpn: VpnDlg, skp: SkpDlg, tip: TipDlg, pin: PinDlg, nxt: NextDlg, entry: EntryDlg }[m.t]();
  const cur = el.querySelector(".overlay:not(.leave)");
  if (cur && cur.dataset.t === m.t) {
    const dlg = cur.querySelector(".dialog");
    dlg.innerHTML = body;
    dlg.classList.remove("swap"); void dlg.offsetWidth;
    if (!reduceMotion()) dlg.classList.add("swap");
  } else {
    el.innerHTML = `<div class="overlay" data-t="${m.t}" ${m.t === "tour" ? "" : 'data-a="scrim"'}><div class="dialog ${m.t === "rec" || m.t === "entry" ? m.t : ""}" role="dialog" aria-modal="true" aria-labelledby="dt" tabindex="-1">${body}</div></div>`;
  }
  if (m.t === "entry" && S.entry?.f) entryPaint(S.entry.f, !!(S.entry.res?.pending || S.entry.res?.queued));
  /* Touch: open at the top with nothing focused (focusing a field would
     scroll the sheet and pop the keyboard up). */
  if (focus) mq("(pointer:coarse)") ? el.querySelector(".dialog")?.focus({ preventScroll: true }) : (el.querySelector(".pri") || el.querySelector("input") || el.querySelector(".dialog")).focus();
}
function openModal(m) { S.menu = null; S.opener = document.activeElement; S.modal = m; if (isPhone() && m.t !== "tour") ovOpen("modal", () => { if (S.modal) modalDismiss(); }); paintNav(); paintModal(true); }
/* Back gesture / swipe down on a dialog: the same as tapping beside it. */
const modalDismiss = () => (S.modal?.t === "done" ? A.dnskip() : closeModal());
function closeModal() { S.modal = null; paintModal(); ovClosed("modal"); S.opener && S.opener.isConnected && S.opener.focus(); }
function confetti() {
  if (reduceMotion()) return;
  const f = $("#fx"), c = ["var(--accent)", "var(--accent-hi)", "var(--ok)", "var(--warn)", "var(--ink)"];
  f.innerHTML = Array.from({ length: 34 }, (_, i) => `<i class="cf" style="left:${35 + Math.random() * 30}%;top:30%;background:${c[i % 5]};border-radius:${i % 3 === 0 ? "50%" : "2px"};--dx:${(Math.random() - 0.5) * 680}px;--rot:${Math.random() * 720}deg;animation-delay:${Math.random() * 0.2}s"></i>`).join("");
  setTimeout(() => (f.innerHTML = ""), 2400);
}
/* Small radial pop of dots from an element (favourite hearts). */
function burstAt(el) {
  if (!el || reduceMotion()) return;
  const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2, f = $("#fx");
  for (let i = 0; i < 10; i++) {
    const p = document.createElement("i"), a = (i / 10) * Math.PI * 2, d = 26 + Math.random() * 16;
    p.className = "bp";
    p.style.cssText = `left:${cx - 3}px;top:${cy - 3}px;background:${i % 2 ? "#e0788a" : "var(--accent-hi)"}`;
    f.appendChild(p);
    p.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 }, { transform: `translate(${Math.cos(a) * d}px,${Math.sin(a) * d}px) scale(0)`, opacity: 0 }],
      { duration: 600 + Math.random() * 200, easing: "cubic-bezier(.2,.8,.2,1)" }).onfinish = () => p.remove();
  }
}

/* ---------- AniList account, reconcile, completion, tour ---------- */
async function aniStatusRefresh() {
  try { S.ani = await api("/api/auth/status"); } catch { S.ani = { connected: false }; }
  paintNav();
  if (S.ani.connected) maybeReconcile();
}
const setMsg = (t) => { const m = $("#animsg"); if (m) m.textContent = t; };
function maybeReconcile() {
  if (S.rec) return;
  api("/api/reconcile", {}).then((res) => { /* POST-only route: a GET is a 404 */
    if (!res || !res.pending) return;
    if (!res.conflicts.length && !res.facts.length) { api("/api/reconcile/dismiss", {}).catch(() => {}); return; }
    if (S.modal) return; // retried on the next refresh
    S.rec = { res, pick: Object.fromEntries(res.conflicts.map((c) => [c.media_id, "anilist"])) };
    openModal({ t: "rec" });
  }).catch(() => {});
}
function recDone() { api("/api/reconcile/dismiss", {}).catch(() => {}); S.rec = null; closeModal(); reload(); }
let lastCompletionKey = null;
function dnPills(n) { $$("#dn-pills button").forEach((b, i) => { b.className = i + 1 < n ? "lo" : i + 1 === n ? "pick" : ""; b.setAttribute("aria-checked", i + 1 === n); }); }
function maybeCompletion(c) {
  const key = `${c.media_id}:${c.count}`;
  if (key === lastCompletionKey || S.modal) return;
  lastCompletionKey = key;
  openModal({ t: "done", c });
  confetti();
  api("/api/entry?id=" + c.media_id).then((res) => {
    if (!res || res.error || !res.state || S.modal?.t !== "done") return;
    const st = res.state, e = st.entry || {}, max = st.score_max || 10;
    const sc = $("#dn-score");
    if (e.score != null && e.score > 0) { sc.value = max === 10 ? Math.round(e.score) : e.score; dnPills(max === 10 ? Math.round(e.score) : 0); }
    if (st.is_favourite) { const f = $("#dn-fav"); f.classList.add("on"); f.setAttribute("aria-pressed", "true"); }
  }).catch(() => {});
}
function finishTour(skipped) {
  api("/api/settings", { tour_done: true }).catch(() => {});
  if (S.settings) S.settings.tour_done = true;
  closeModal();
  if (!skipped) nav("library");
}
function maybeTour() {
  if (!S.settings || S.settings.tour_done || location.hash.includes("#show=") || location.hash.includes("#f=") || S.modal) return;
  S.tour = { step: 0, choice: "connect" };
  openModal({ t: "tour" });
}
async function openExternal(url) {
  try { const r = await api("/api/open", { url }); if (!r.ok) throw 0; } catch { window.open(url, "_blank", "noopener"); }
}

/* Wait for the OAuth callback bridge to report a connection (both the
   browser-tab flow and the shell flow's "approve in your browser" path). */
async function pollOauth() {
  for (let i = 0; i < 150 && S.modal?.t === "ani"; i++) {
    await sleep(2000);
    if (S.modal?.t !== "ani") return;
    const st = await api("/api/auth/status").catch(() => null);
    if (st?.connected) { S.ani = st; S.alData = null; toast(`AniList connected as ${st.user}`); closeModal(); aniStatusRefresh(); setTimeout(reload, 2500); return; }
  }
  if (S.modal?.t === "ani") setMsg("Still not connected — the AniList tab may have been closed. Try again, or paste a token below.");
}

/* ---------- render ---------- */
let io = null;
function applyBg(el) {
  const u = el.dataset.bg;
  delete el.dataset.bg;
  if (!u) return;
  el.style.backgroundImage = cssUrl(u);
  el.style.backgroundPosition = el.dataset.bp || "center";
  el._bg = u; bgSeen.add(u);
}
/* Art the page has shown before goes on at once (it is in the browser's
   memory); new art waits until its tile is near the screen. */
const bgSeen = new Set();
function lazyBg() {
  const els = $$("[data-bg]").filter((el) => el._bg !== el.dataset.bg);
  if (!("IntersectionObserver" in window)) { els.forEach(applyBg); return; }
  io ||= new IntersectionObserver((es) => es.forEach((en) => { if (en.isIntersecting) { io.unobserve(en.target); applyBg(en.target); } }), { rootMargin: "300px 0px" });
  els.forEach((el) => (bgSeen.has(el.dataset.bg) ? applyBg(el) : io.observe(el)));
}
const saveLib = () => lsSet("hubLib", S.lib);
let fIdx = -1;
const glideMem = {};
/* A single indicator element that slides between the active items of a [data-glide] box.
   The box is rebuilt on every paint, so the indicator is re-created at the remembered
   position first and then moved: the transition runs even though the DOM is new. */
function glide(box) {
  const on = box.querySelector(".on,[aria-current]");
  if (!on) return;
  const key = box.dataset.glide;
  let g = box.querySelector(":scope > .glide");
  if (!g) { g = document.createElement("i"); g.className = "glide"; box.prepend(g); }
  const to = { x: on.offsetLeft, y: on.offsetTop, w: on.offsetWidth, h: on.offsetHeight };
  const set = (r) => { g.style.setProperty("--gx", r.x + "px"); g.style.setProperty("--gy", r.y + "px"); g.style.setProperty("--gw", r.w + "px"); g.style.setProperty("--gh", r.h + "px"); };
  const from = glideMem[key];
  if (from && !reduceMotion()) { g.style.transition = "none"; set(from); void g.offsetWidth; g.style.transition = ""; }
  set(to); glideMem[key] = to;
}
/* Edge fades + arrow state for horizontally scrolling strips. */
function strips() {
  $$("#app .strip").forEach((st) => {
    const arrows = st.previousElementSibling ? st.previousElementSibling.querySelectorAll(".arr") : [];
    const upd = () => {
      const max = st.scrollWidth - st.clientWidth, x = st.scrollLeft;
      st.classList.toggle("ml", x > 4); st.classList.toggle("mr", x < max - 4);
      if (arrows[0]) { arrows[0].hidden = arrows[1].hidden = max <= 4; arrows[0].disabled = x <= 4; arrows[1].disabled = x >= max - 4; }
    };
    if (!st._strip) { st._strip = true; st.addEventListener("scroll", upd, { passive: true }); }
    upd();
  });
}
/* The accent follows the show on screen (Decision 38): a hue sampled from its
   art, at a fixed light, soft saturation so every show reads well on dark.
   Grey art gives no hue — the default accent stays. */
const ambMemo = {};
let ambWant = null;
function sampleColor(img) {
  const w = 24, h = 36, c = document.createElement("canvas");
  c.width = w; c.height = h;
  const x = c.getContext("2d", { willReadFrequently: true });
  x.drawImage(img, 0, 0, w, h);
  const d = x.getImageData(0, 0, w, h).data;
  let R = 0, G = 0, B = 0, W = 0;
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i] / 255, g = d[i + 1] / 255, b = d[i + 2] / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    const s = mx === mn ? 0 : (mx - mn) / (1 - Math.abs(2 * l - 1)), wt = s * s * (1 - Math.abs(2 * l - 1)) + 0.001;
    R += r * wt; G += g * wt; B += b * wt; W += wt;
  }
  if (W / (w * h) < 0.004) return null; // greyscale art: no colour to follow
  R /= W; G /= W; B /= W;
  const mx = Math.max(R, G, B), dl = mx - Math.min(R, G, B);
  let hh = 0;
  if (dl) hh = (mx === R ? (G - B) / dl + (G < B ? 6 : 0) : mx === G ? (B - R) / dl + 2 : (R - G) / dl + 4) * 60;
  return `hsl(${Math.round(hh)} 72% 70%)`;
}
/* The page's art (Decision 38): show page → that show (sharp banner +
   blurred poster); Home → the up-next show; Library/History/Stats → the last
   watched show (blur only); Settings → none, a neutral glow. */
const colorOfId = (id) => (id && S.data?.spine?.entries?.find((e) => e.id === id)?.color) || null;
const showColor = (s) => colorOfId(s?.meta?.anilist_id);
const lastShow = () => { for (const h of S.data?.history || []) { const c = findEpContext(h.id); if (c) return c.s; } return null; };
function canvasArt() {
  if (S.rmtOpen && S.data?.now) { const c = findEpContext(S.data.now.episode_id); if (c) return { blur: c.s.banner || c.s.poster, sharp: c.s.banner, color: showColor(c.s) }; }
  if (!S.data || S.view === "settings") return null;
  if (S.view === "fr") { const e = frEntry(), p = S.fr?.page; return { blur: e?.banner || p?.banner || e?.poster || p?.poster, sharp: e?.banner || p?.banner, color: e?.color || colorOfId(e?.id) }; }
  if (S.view === "show") { const s = showById(S.showId); return s && { blur: s.banner || s.poster, sharp: s.banner, color: showColor(s) }; }
  if (S.view === "home") { const s = nextUpInfo()?.s; return s && { blur: s.banner || s.poster, sharp: null, color: showColor(s) }; } // Home: a full blur of the up-next show
  if (S.view === "library" && S.libHover) return S.libHover;
  if (S.view === "history" || S.view === "stats") {
    const top = jTop();
    if (top) return { blur: top.poster || top.art, sharp: null, color: colorOfId(top.mid) || S.jr?.media?.[top.mid]?.color || null };
  }
  const s = lastShow();
  return s && { blur: s.poster || s.banner, sharp: null, color: showColor(s) };
}
/* Two layers per kind crossfade: the new art fades in over the old one. */
const canvasNow = {};
function swapLayer(cls, url) {
  url = url || null;
  if (canvasNow[cls] === url) return;
  canvasNow[cls] = url;
  const [a, b] = $$("#canvas ." + cls), on = a.classList.contains("out") ? b : a, off = on === a ? b : a;
  if (url) { off.style.backgroundImage = cssUrl(url); off.classList.remove("out"); }
  on.classList.add("out");
}
function paintCanvas() {
  const art = canvasArt(), mode = lookGet("art");
  if (!(S.view === "library" && S.libHover)) document.documentElement.classList.remove("amb-now"); // page changes fade the accent
  swapLayer("cv-blur", mode === "off" ? null : art?.blur);
  swapLayer("cv-art", mode === "full" ? art?.sharp : null);
  accentFrom(art?.blur || art?.sharp, art?.color);
}
/* AniList's colour first (works in every browser); else a hue read from the
   art — privacy browsers (Helium, Brave…) block that read, so it's the fallback. */
/* Library hover (html.amb-now): the CSS fade would restyle the whole page on
   every frame (~15 ms with a full wall), so the colour steps there instead:
   5 steps over 0.4 s, still a fade, a fraction of the work. */
let ambStep = 0;
function ambFade(to) {
  const root = document.documentElement, from = getComputedStyle(root).getPropertyValue("--amb").trim();
  clearInterval(ambStep);
  if (!to || !from || reduceMotion()) return to ? root.style.setProperty("--amb", to) : root.style.removeProperty("--amb");
  let k = 0;
  ambStep = setInterval(() => {
    k++;
    root.style.setProperty("--amb", k >= 5 ? to : `color-mix(in oklch, ${to} ${k * 20}%, ${from})`);
    if (k >= 5) clearInterval(ambStep);
  }, 80);
}
/* The phone's status bar (theme-color) takes a deep tone of the page's
   colour, so the top of the screen reads as one surface with the art. */
function barTint(c) {
  const m = document.querySelector('meta[name="theme-color"]');
  if (!m) return;
  let hue = /hsl\((\d+)/.exec(c || "")?.[1];
  const rgb = /rgb\((\d+),\s*(\d+),\s*(\d+)/.exec(c || "");
  if (hue == null && rgb) {
    const [r, g, b] = rgb.slice(1).map((v) => v / 255), mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
    hue = !d ? null : Math.round((mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60);
  }
  m.content = hue != null ? `hsl(${hue} 28% 13%)` : "#101114";
}
function accentFrom(url, color) {
  const root = document.documentElement;
  ambWant = url || null;
  const set = (c) => { barTint(c); if (root.classList.contains("amb-now")) return ambFade(c); clearInterval(ambStep); c ? root.style.setProperty("--amb", c) : root.style.removeProperty("--amb"); };
  if (color) return set(hexAccent(color));
  if (!url) return set(null);
  if (url in ambMemo) return set(ambMemo[url]);
  const img = new Image();
  /* AniList's CDN allows CORS; without it the canvas can't read the colour. Its own
     URL key, so the copy the page already loaded (no CORS) isn't reused. */
  const remote = /^https?:/.test(url);
  if (remote) img.crossOrigin = "anonymous";
  img.onload = () => { let c = null; try { c = sampleColor(img); } catch {} ambMemo[url] = c; if (ambWant === url) set(c); };
  img.src = remote ? url + (url.includes("?") ? "&" : "?") + "cors=1" : url;
}
const ambient = paintCanvas;
const VIEW_TITLE = { home: "", library: "Library", history: "Journal", stats: "Journal", settings: "Settings", week: "Airing" };
/* ---------- in-place updates (TODO 61) ----------
   A background refresh brings the open page up to date without rebuilding
   it: the new HTML is matched against the live nodes (by data-a/data-v or
   id, else by position) and only what really changed moves. Images keep
   their element (no reload, no fade replay), tiles keep their art, strips
   their scroll. Runtime state the HTML doesn't carry survives: the image
   "ready" classes, applied tile art, sheen/tilt styles, glide indicators,
   and the boxes the page fills itself (#entrybox, #infobox, #airline, #wall). */
const LIVE_CLASSES = ["ready", "focused", "tilting", "ml", "mr"];
const morphKey = (n) => (n.nodeType !== 1 ? null : n.id ? "#" + n.id : n.dataset?.a && n.dataset.v != null ? n.dataset.a + "|" + n.dataset.v : null);
const liveOnly = (n) => n.nodeType === 1 && (n.classList.contains("glide") || n.classList.contains("rpl"));
function morphAttrs(a, b) {
  for (const { name } of [...a.attributes]) if (name !== "style" && !b.hasAttribute(name)) a.removeAttribute(name);
  for (const { name, value } of [...b.attributes]) {
    if (name === "class") {
      const keep = LIVE_CLASSES.filter((c) => a.classList.contains(c) && !b.classList.contains(c));
      const want = keep.length ? value + " " + keep.join(" ") : value;
      if (a.getAttribute("class") !== want) a.setAttribute("class", want);
    } else if (name === "style") {
      if (a.classList.contains("tilting")) continue;
      if (a.getAttribute("style") !== value) a.setAttribute("style", value);
    } else if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  }
}
function morphNode(a, b) {
  if (a.nodeType !== 1) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
  if (a.dataset.bg !== undefined && a.dataset.bg !== b.dataset.bg) { a.removeAttribute("style"); a._bg = null; }
  morphAttrs(a, b);
  if (a._own || a.id === "wall") return;
  morphChildren(a, b);
  if ((a.tagName === "INPUT" || a.tagName === "SELECT") && a !== document.activeElement && a.value !== b.value) a.value = b.value;
}
function morphChildren(a, b) {
  const old = [...a.childNodes], byKey = new Map(), used = new Set();
  for (const n of old) { const k = morphKey(n); if (k && !byKey.has(k)) byKey.set(k, n); }
  const newKeys = new Set([...b.childNodes].map(morphKey).filter(Boolean));
  let cur = a.firstChild;
  for (const nb of [...b.childNodes]) {
    while (cur && (liveOnly(cur) || used.has(cur))) cur = cur.nextSibling;
    const k = morphKey(nb);
    let m = k ? byKey.get(k) : cur && !morphKey(cur) ? cur : null;
    if (m && (used.has(m) || m.nodeName !== nb.nodeName)) m = null;
    /* A control whose target changed (Play E3 → Rewatch) is the same node
       with new attributes, not a new one — a new one replays its entrance. */
    if (!m && k && cur && cur.nodeName === nb.nodeName && !newKeys.has(morphKey(cur))) m = cur;
    if (!m) { a.insertBefore(nb, cur); continue; }
    used.add(m);
    if (m === cur) cur = cur.nextSibling; else a.insertBefore(m, cur);
    morphNode(m, nb);
  }
  for (const n of old) if (!used.has(n) && !liveOnly(n) && n.parentNode === a) a.removeChild(n);
}
function morphHTML(el, html) {
  const t = document.createElement("template"); // inert: nothing loads until a node joins the page
  t.innerHTML = html;
  morphChildren(el, t.content);
}
const viewKey = () => S.view + "|" + (S.view === "fr" ? S.fr?.eid : S.view === "show" ? S.showId : "");
function render(inPlace = false) {
  if (S.view === "show" && !showById(S.showId)) S.view = "home";
  if (S.view === "show") {
    const mid = !S.frDown[S.showId] && frEntryOfShow(showById(S.showId));
    if (mid) { S.view = "fr"; S.fr = { eid: mid, page: null, err: null, fromShow: S.showId }; history.replaceState(navSnap(), "", navUrl()); setTimeout(() => frLoad(mid)); }
  }
  const fresh = S.navIn; S.navIn = false; // entrance choreography belongs to navigation, not to every re-render
  if (fresh) for (const k in glideMem) if (k !== "nav") delete glideMem[k];
  paintNav();
  io && io.disconnect();
  const app = $("#app");
  const html = { home: Home, library: Lib, history: Hist, stats: Stats, settings: Sett, show: Det, fr: Fran, week: Week }[S.view]();
  const morph = inPlace === true && !fresh && app._view === viewKey();
  /* An in-place update leaves the entrance animation running. */
  if (morph) morphHTML(app, html); else { app.classList.toggle("nav-in", fresh && !reduceMotion()); app.innerHTML = html; }
  app._view = viewKey();
  if (S.view === "fr") frLastSig = frSig();
  if (S.view === "library") { paintWall(!morph && S.fadeNext); S.fadeNext = false; startMetaWatch(); }
  lazyBg();
  $$("#app [data-glide]").forEach(glide);
  strips(); ambient();
  if (S.view === "settings" && S.setFlash) { const t = S.setFlash; S.setFlash = null; const r = $$("#app .srow").find((x) => x.querySelector("b")?.textContent.startsWith(t)); if (r) { r.scrollIntoView({ block: "center" }); r.classList.add("flash"); } }
  /* The strip opens on the next episode, with the one before it in view — whole tiles, never a cut one. */
  if (fresh && (S.view === "fr" || S.view === "show")) { const st = $("#app .eps.strip"), t = st?.querySelector(".ep.resume"), b = t?.previousElementSibling; if (st && t) st.scrollLeft = b ? b.offsetLeft - st.firstElementChild.offsetLeft : 0; }
  const t = S.view === "show" ? showById(S.showId)?.title : S.view === "fr" ? S.fr?.page?.title || frEntry()?.title : VIEW_TITLE[S.view];
  document.title = t ? `${t} · Anime Hub` : "Anime Hub";
  paintPtitle(S.view === "home" ? dayWord(new Date().getHours()) : S.view === "week" ? "Airing" : t);
  if (S.view === "show") { const id = showById(S.showId).meta?.anilist_id; if (id) loadEntry(id); }
  if (S.view === "fr" && frEntry()) loadEntry(frEntry().id);
  if (S.sel && S.sel.show !== selKey()) S.sel = null;
  paintSel();
  if (S.view === "stats" || S.view === "history") loadAlData();
  applyFocus();
  paintPortals();
}
/* Background refreshes must not clobber a field the user is typing in. */
function softRender() {
  const a = document.activeElement;
  if (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.closest("#app")) { S.stale = true; return; }
  render(true);
}
document.addEventListener("focusout", () => setTimeout(() => {
  const a = document.activeElement;
  if (S.stale && !(a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.closest("#app"))) { S.stale = false; render(true); }
}, 80));
/* Page switch (TODO 61): the new page draws at once and fades in — no
   fade-out wait first (that was 200 ms on every click). */
function dip(f) {
  const a = $("#app");
  f();
  if (reduceMotion()) return;
  a.classList.remove("enter"); void a.offsetWidth; a.classList.add("enter");
}
function clearSearch() { if (S.menu === "sr") ovDrop("sr"); S.q = ""; S.alres = null; S.alq = null; S.alBusy = false; S.menu = null; $("#q").value = ""; }
/* Switching Home · Library · Journal on a touch screen slides the page in
   from the side of the tab you went to (one transform on #app, no fill,
   so nothing stays transformed afterwards). */
const TAB_AT = { home: 0, week: 0, library: 1, history: 2, stats: 2 };
function tabSlide(from, to) {
  if (reduceMotion() || !mq("(pointer:coarse), (max-width:700px)")) return;
  const a = TAB_AT[from], b = TAB_AT[to], app = $("#app"), page = (v) => v === "show" || v === "fr";
  /* Opening a show pushes it in from the right, like a native stack. */
  const cls = page(to) && !page(from) ? "tab-l" : a == null || b == null || a === b ? null : b > a ? "tab-l" : "tab-r";
  if (!cls) return;
  app.classList.remove("enter", "tab-l", "tab-r"); void app.offsetWidth; app.classList.add(cls);
}
const tabScroll = {};
function nav(view, fn) {
  const from = S.view;
  if (TAB_AT[from] != null) tabScroll[from] = scrollY;
  dip(() => {
    const prev = S.view;
    S.navIn = true; S.view = view; clearSearch(); fIdx = -1;
    if (view === "library") S.fadeNext = true;
    if (fn) fn(prev);
    render(); scrollTo(0, TAB_AT[view] != null && TAB_AT[from] != null && isPhone() ? tabScroll[view] || 0 : 0); pushNav();
  });
  tabSlide(from, view);
}
function openShow(id) {
  /* A matched folder or an online show is one franchise page; only
     unmatched folders keep the plain show page (and any show whose
     franchise AniList can't deliver right now: frUnavailable). Each open
     tries the franchise again. */
  const s = showById(id), mid = s && frEntryOfShow(s);
  delete S.frDown[id];
  if (mid) return openFranchise(mid, id);
  nav("show", (prev) => {
    if (prev !== "show") S.showBack = prev;
    S.showId = id;
    const s = showById(id), t = s && pickTarget(s);
    S.seasonIdx = t ? Math.max(0, s.seasons.indexOf(t.sea)) : 0;
  });
}

/* ---------- data refresh + polling ---------- */
let reloading = false, metaWatch = null, nowTimer = null, lastNowError = null, lastNotice = null;
function offlineNote(on, msg) {
  let n = $(".offlinenote");
  if (!on) { n && n.remove(); return; }
  if (!n) { n = document.createElement("div"); n.className = "offlinenote"; document.body.appendChild(n); }
  n.textContent = msg;
}
/* Fetching and drawing fail for different reasons, so they are reported
   separately: only a failed fetch means "can't reach the hub". */
/* The library payload is big: an unchanged one comes back as a bodiless
   304 (TODO 61) and the copy in hand stays. */
let libTag = null;
async function libFetch() {
  const res = await fetch("/api/library", { cache: "no-store", headers: libTag && S.data ? { "If-None-Match": libTag } : {} });
  if (res.status === 304 && S.data) return S.data;
  let d;
  try { d = await res.json(); } catch { throw new Error(`HTTP ${res.status} from /api/library`); }
  libTag = res.headers.get("ETag");
  return d;
}
async function reload() {
  if (reloading) return;
  reloading = true;
  let d;
  try { d = await libFetch(); }
  catch (e) { return retryLoad(`Can't reach the hub (${e.message}) — retrying…`); }
  if (!d || d.error || !Array.isArray(d.shows)) return retryLoad(`The hub answered, but not with a library${d && d.error ? ": " + d.error : ""} — retrying…`);
  S.data = d; offlineNote(false);
  autoSuggest(unmatchedShows());
  if (S.view === "fr" && S.fr) frLoad(S.fr.eid);
  try { softRender(); }
  catch (e) { console.error(e); offlineNote(true, `Display error: ${e.message} (see the browser console)`); }
  reloading = false;
}
function retryLoad(msg) { offlineNote(true, msg); setTimeout(() => { reloading = false; reload(); }, 4000); }
/* Watch-state changes (mark / bulk / reset) patch the show page in place
   instead of rebuilding #app: a rebuild recreates every image element — the
   episode tiles lose their background for a frame or two and the hero poster
   <img> reloads — just to flip a tick (Elias, 2026-10-17). Only text/state
   bits move; artwork DOM nodes stay where they are. */
function patchShow(build = Det) {
  const cur = $("#app"), tpl = document.createElement("template"); // inert: no image loads for the throwaway copy
  tpl.innerHTML = build();
  const fresh = tpl.content;
  /* State-bearing, image-free subtrees update in place: a swapped-in
     button would replay its entrance animation (Play → Rewatch after
     marking a season, Elias 2026-10-22). */
  for (const sel of [".hero .cta", ".eps-head .ctl", ".rels"]) {
    const as = cur.querySelectorAll(sel), bs = fresh.querySelectorAll(sel);
    as.forEach((a, k) => (bs[k] ? morphNode(a, bs[k]) : a.remove()));
  }
  cur.querySelectorAll(".rels").forEach(glide); // the glide indicator follows the new active chip
  /* The now-playing badge (Loading → Playing) sits beside the art-free
     chips; only it moves — #airline keeps the text entryDraw put there. */
  const pa = cur.querySelector(".hero .meta .playing"), pb = fresh.querySelector(".hero .meta .playing");
  if (pa && pb) pa.replaceWith(pb); else if (pa) pa.remove(); else if (pb) cur.querySelector(".hero .meta")?.prepend(pb);
  /* Episode tiles: the art-bearing .epimg child is watch-independent, so the
     node itself survives — only the class and the state children follow the
     watch state. Swapping the art node out would blank the tile until the
     lazy observer re-applies the background. */
  const next = new Map([...fresh.querySelectorAll(".ep[data-v]")].map((el) => [el.dataset.v, el]));
  for (const tile of cur.querySelectorAll(".ep[data-v]")) {
    const el = next.get(tile.dataset.v);
    if (!el) continue;
    const art = tile.querySelector(".epimg");
    tile.className = el.className;
    tile.innerHTML = el.innerHTML;
    if (art) tile.querySelector(".epimg")?.replaceWith(art);
  }
  /* Keep keyboard focus on the control that acted (its node may be new). */
  const ae = document.activeElement;
  const key = ae && ae.closest ? ae.closest("#app [data-a]") : null;
  if (key) {
    const q = [`[data-a="${key.dataset.a}"]`];
    for (const k of ["v", "s"]) if (key.dataset[k] != null) q.push(`[data-${k}="${key.dataset[k].replace(/"/g, '\\"')}"]`);
    const again = cur.querySelector(q.join(""));
    if (again) again.focus({ preventScroll: true });
  }
  applyFocus();
}
function watchPatch(ids, watched, reset) {
  applyWatch(allShows(), ids, watched, reset);
  if (S.view === "show" && showById(S.showId)) {
    patchShow();
    const id = showById(S.showId).meta?.anilist_id;
    if (id) loadEntry(id);
    /* Other views read continue/history/stats off S.data — refresh it in the
       background without touching this page's DOM. */
    refreshData();
    return;
  }
  reload();
}
async function refreshData() { try { S.data = await libFetch(); paintNav(); } catch {} }
function startMetaWatch() {
  if (metaWatch || !S.data?.meta_running) return;
  metaWatch = setTimeout(metaTick, 2500);
}
/* The pass's progress rides /api/now (TODO 61: it used to refetch the
   whole library every 2.5 s); the library is fetched once it is done. */
let metaFold = 0;
function metaTick() {
  api("/api/now").then((now) => {
    const m = now && now.meta;
    if (!m) { metaWatch = null; reload(); return; } // final settle: posters flip in
    Object.assign(S.data, { meta_running: true, meta_pending: m.pending, meta_current: m.current, meta_phase: m.phase });
    if (S.view === "library") { const n = $(".metanote"); if (n && S.data.meta_pending > 0) n.lastChild.textContent = metaText(S.data); else n?.remove(); }
    /* Franchise cards fold together as the background build reaches them. */
    if (m.phase === "franchises" && S.view === "library" && byFranchise() && Date.now() - metaFold > 15e3) { metaFold = Date.now(); reload(); }
    metaWatch = setTimeout(metaTick, 2500);
  }).catch(() => { metaWatch = null; });
}
function pollSoon(ms) { if (nowTimer) clearTimeout(nowTimer); nowTimer = setTimeout(pollNow, ms); }
async function pollNow() {
  nowTimer = null;
  try {
    const now = await api("/api/now");
    if (now.error && now.error !== lastNowError) { lastNowError = now.error; toast(now.error); } else if (!now.error) lastNowError = null;
    const was = !!S.data?.now, prevEp = S.data?.now?.episode_id;
    const outbox = typeof now.outbox === "number" ? now.outbox : 0;
    if (now.vpn && S.data) {
      const vpnMoved = JSON.stringify(now.vpn) !== JSON.stringify(S.data.vpn);
      S.data.vpn = now.vpn; // the VPN chip follows the connection, not only reloads
      if (vpnMoved && S.modal?.t === "nxt") { const el = $("#nx-vpn"); if (el) el.innerHTML = nxVpn(); }
      /* "Turn it on and this starts by itself": a VPN coming up continues what waited for it. */
      if (vpnMoved && now.vpn.active && S.modal?.t === "nxt") A.nxgo();
      else if (vpnMoved && now.vpn.active && S.modal?.t === "vpn") A.vpngo();
      else if (vpnMoved && now.vpn.active && S.modal?.t === "skp") A.skpgo();
    }
    if (now.notice && now.notice.ts !== lastNotice) { lastNotice = now.notice.ts; toast(now.notice.msg); }
    loadToast(now);
    if (!S.playBusy) {
      const nl = now.playing && now.loading ? now.episode_id : null;
      if (nl !== S.loadingEp) { S.loadingEp = nl; if (was === now.playing) pagePatch(); }
    }
    if (was !== now.playing) await reload();
    else if (now.playing) {
      const wasLoading = !!S.data.now?.loading;
      S.data.now = { episode_id: now.episode_id, show: now.show, label: now.label, percent: now.percent, loading: !!now.loading, next: now.next };
      liveFrom(now);
      if (wasLoading !== !!now.loading) pagePatch();
      if (prevEp && prevEp !== now.episode_id) await reload(); // autoplay moved on: refresh ticks / next-up
    }
    if (S.outbox > 0 && outbox === 0) toast("Waiting changes synced to AniList");
    if (outbox !== S.outbox) { S.outbox = outbox; if (S.menu === "ob") outbox ? obLoad() : (S.menu = null); }
    S.artWorking = !!now.art?.working;
    if (now.art && now.art.stamp !== S.artStamp) { S.artStamp = now.art.stamp; swapArt(now.art.changed); }
    const dls = now.downloads || [], done = (l) => l.filter((j) => j.status === "done").length;
    const finished = done(dls) > done(S.dl);
    const dlChanged = JSON.stringify(dls) !== JSON.stringify(S.dl);
    S.dl = dls;
    if (finished) await reload(); // a finished download is a new file on disk
    else if (dlChanged) pagePatch(); // progress on the tiles
    const sig = JSON.stringify([S.data?.now, S.outbox, S.dl, S.data?.vpn]);
    if (sig !== navSig) { navSig = sig; paintNav(); }
    if (!now.playing) S.live = null;
    else if (!S.live || S.live.ep !== now.episode_id) liveFrom(now);
    paintRemote();
    if (now.completed) maybeCompletion(now.completed);
    if (now.next_prompt) maybeNextPrompt(now.next_prompt);
    else if (S.modal?.t === "nxt") closeModal(); // answered elsewhere
  } catch {}
  pollSoon(S.rmtOpen ? 1000 : S.artWorking ? 1500 : (S.data?.now && rmtSig) || S.dl.some((j) => j.status === "running") ? 2000 : 4000);
}
/* A banner was upscaled (or put back) under the same name: point every
   copy of its URL at the new ?v= stamp — the data the page draws from and
   what is on screen — so it shows without a reload. */
function swapArt(changed) {
  const fix = (u) => typeof u === "string" && u.includes(".banner.jpg")
    ? u.replace(/\/img\/([\w.-]+?\.banner\.jpg)(\?v=\d+)?/g, (m, n) => (changed[n] ? `/img/${n}?v=${changed[n]}` : m)) : u;
  const walk = (o, seen) => {
    if (!o || typeof o !== "object" || seen.has(o)) return;
    seen.add(o);
    for (const k in o) { const v = o[k]; if (typeof v === "string") { const f = fix(v); if (f !== v) o[k] = f; } else walk(v, seen); }
  };
  const seen = new Set();
  walk(S.data, seen); walk(S.fr, seen);
  for (const p of frMemo.values()) walk(p, seen);
  for (const el of $$('[style*=".banner.jpg"]')) { const b = fix(el.style.backgroundImage); if (b !== el.style.backgroundImage) el.style.backgroundImage = b; }
  for (const el of $$('[data-bg*=".banner.jpg"]')) el.dataset.bg = fix(el.dataset.bg);
  for (const el of $$('img[src*=".banner.jpg"]')) { const b = fix(el.getAttribute("src")); if (b !== el.getAttribute("src")) el.src = b; }
  for (const cls in canvasNow) if (canvasNow[cls] && fix(canvasNow[cls]) !== canvasNow[cls]) swapLayer(cls, fix(canvasNow[cls]));
}
/* The open page's tiles follow loading / download / watch state. */
function pagePatch() {
  if (S.view === "show" && showById(S.showId)) patchShow();
  else if (S.view === "fr") frRepaint();
  paintPortals();
}
/* Online episodes take a few seconds to start: the tile (and the hero
   button) show it loading until mpv reports playback. */
function markLoading(id) {
  S.loadingEp = id;
  pagePatch();
}
/* skipOk: undefined = not asked yet, true/false = the AniSkip answer
   (yes to the online VPN dialog counts as yes). */
async function play(id, version = 0, mode, asked, fromStart, skipOk) {
  if (S.settings?.play_tip_done === false && !mq("(pointer:coarse)")) return openModal({ t: "tip", go: () => play(id, version, mode, asked, fromStart, skipOk) });
  const online = id.startsWith("stream:");
  if (online && !asked) return vpnGate((ok) => play(id, version, mode, true, fromStart, ok), aniskipAsk());
  if (skipOk === undefined && aniskipAsk()) return openModal({ t: "skp", go: (ok) => play(id, version, mode, true, fromStart, ok) });
  S.playBusy = true;
  if (online) markLoading(id);
  const ctx = findEpContext(id);
  loadShown = id; toast(online ? `Finding ${ctx ? ctx.s.title + " · " + epCode(ctx.sea, ctx.ep) : "the episode"} online…` : `Loading ${ctx ? ctx.s.title + " · " + epCode(ctx.sea, ctx.ep) : "the episode"}`, "busy");
  const body = { id, version };
  if (mode) body.mode = mode;
  if (fromStart) body.from_start = true;
  if (skipOk !== undefined) body.aniskip = skipOk;
  const res = await api("/api/play", body).catch(() => ({ error: "Could not reach the hub." }));
  S.playBusy = false;
  if (res.error) { markLoading(null); loadShown = null; toast(res.error, "err"); }
  pollSoon(600);
}
/* Queue ani-cli downloads; they land in the library folder as ordinary files. */
async function download(mid, eps, asked) {
  if (!asked) return vpnGate(() => download(mid, eps, true));
  const r = await api("/api/download", { media_id: mid, episodes: eps }).catch(() => null);
  if (!r || r.error) return toast(r?.error || "Could not reach the hub.");
  toast(r.queued ? `Downloading ${r.queued} episode${r.queued === 1 ? "" : "s"}` : "Already downloaded or downloading");
  pollSoon(300);
}
async function setToggle(key, on) {
  const r = await api("/api/settings", { [key]: on }).catch(() => null);
  if (!r || r.error) { toast(r?.error || "Could not save — is the hub running?"); return; }
  S.settings = r;
  if (key === "anilist_watched") await refreshData();
  if (S.view === "settings") render();
  if (S.modal?.t === "tour") paintModal();
}
function commitMatch(id, aid) {
  const m = mst(id);
  m.busy = true; m.err = ""; paintMatch(id);
  api("/api/match", { show_id: id, anilist_id: aid }).then((r) => {
    m.busy = false;
    if (r && r.error) { m.err = r.error; paintMatch(id); return; }
    delete S.match[id];
    toast(`Matched to AniList #${aid}`);
    reload();
  }).catch(() => { m.busy = false; m.err = "Could not save — is the hub running?"; paintMatch(id); });
}

/* ---------- actions ---------- */
const A = {
  strip: (v, t) => { const st = t.closest("section")?.querySelector(".strip") || $("#app .strip"); st && st.scrollBy({ left: +t.dataset.d * st.clientWidth * 0.8, behavior: reduceMotion() ? "auto" : "smooth" }); },
  fclear: () => { for (const k of ["fmt", "air", "year", "score"]) S.lib[k] = "all"; S.lib.genres = []; if (!srcLocked()) S.lib.src = "all"; saveLib(); S.fadeNext = true; render(); },
  airgo: (v) => {
    const c = S.airCal;
    if (c.mode === "week") { const d = new Date(`${c.sel}T00:00:00`); d.setDate(d.getDate() + 7 * v); c.sel = airKey(d); c.y = d.getFullYear(); c.m = d.getMonth(); }
    else { const d = new Date(c.y, c.m + +v, 1); c.y = d.getFullYear(); c.m = d.getMonth(); }
    render(true);
  },
  airtoday: () => { const d = new Date(); Object.assign(S.airCal, { y: d.getFullYear(), m: d.getMonth(), sel: airKey(d) }); render(true); },
  airday: (v) => { S.airCal.sel = v; render(true); },
  airscope: (v) => { S.airCal.scope = v; render(true); },
  airmode: (v) => { S.airCal.mode = v; lsSet("airMode", v); render(true); },
  gmore: () => { S.genOpen = !S.genOpen; render(); },
  totop: () => scrollTo({ top: 0, behavior: reduceMotion() ? "auto" : "smooth" }),
  genre: (v, t) => {
    const on = !S.lib.genres.includes(v);
    S.lib.genres = on ? [...S.lib.genres, v] : S.lib.genres.filter((g) => g !== v);
    t.classList.toggle("on", on); t.setAttribute("aria-pressed", on);
    saveLib(); paintWall(); syncFilters();
  },
  go: (v, t) => {
    /* The settings icon again acts as back. */
    if (v === "settings" && S.view === "settings") {
      if (isPhone() && S.setOpen) A.setback(); else if (history.state?.inApp) history.back(); else nav("home");
      return;
    }
    if (v === "settings") S.setOpen = false;
    const same = v === S.view || (v === "history" && S.view === "stats");
    /* The bottom bar (phones): the open tab again scrolls to the top; another
       tab comes back where you left it, with a light tap. */
    if (t?.closest?.("#tabbar")) {
      if (same) { scrollTo({ top: 0, behavior: reduceMotion() ? "auto" : "smooth" }); return; }
      buzz();
    }
    nav(v);
  }, open: (v) => openShow(v), back: () => nav(VIEWS.includes(S.showBack) ? S.showBack : "home"),
  entry: (v) => {
    const e = S.data.spine?.entries.find((x) => String(x.id) === v);
    if (!e) return;
    /* AniList mode: the card is an entry — its franchise page, at it. */
    if (aniMode()) return openFranchise(e.id);
    const sid = e.local_show_id || (showById(e.stream_show_id) ? e.stream_show_id : null);
    if (sid) openShow(sid); else if (e.media_id) openFranchise(e.media_id);
  },
  tab: (v) => { S.lib.tab = v; saveLib(); S.fadeNext = true; render(); },
  filt: (v, t) => { S.lib[t.dataset.k] = v; saveLib(); if (t.dataset.k === "group") return render(); paintWall(); syncFilters(); },
  hrange: (v) => { S.histRange = v; S.histMax = 120; lsSet("histRange", v); render(); },
  histmore: () => { S.histMax += 200; render(); },
  play: (v) => play(v), playep: (v, t, e) => (t.classList.contains("ep") && (S.sel?.show === selKey() || e?.ctrlKey || e?.metaKey)) ? selToggle(v, e?.shiftKey) : play(v, +t.dataset.ver || 0),
  cm: (v, t, e) => openCm(v, e.clientX, e.clientY),
  mark: (v, t) => {
    const w = t.dataset.w === "true", f = S.view === "fr" && frFind(v);
    if (f) return frMark(f.e, [f.x.number], w);
    /* A show page marks through bulk-watched, like its buttons: the mark
       syncs to AniList (queued while it's down), as on the franchise page. */
    const own = S.view === "show" && showById(S.showId)?.seasons.some((x) => x.episodes.some((e) => e.id === v));
    if (own) return api("/api/bulk-watched", { show_id: S.showId, watched: w, ids: [v] }).then((r) => {
      if (r && r.error) return toast(r.error);
      toast(w ? `Marked as watched${r.synced ? " — will sync" : ""}` : "Marked as unwatched");
      watchPatch([v], w, false);
    }).catch(() => toast("Could not save — is the hub running?"));
    api("/api/watched", { id: v, watched: w }).then((r) => { if (r && r.error) return toast(r.error); toast(w ? "Marked as watched" : "Marked as unwatched"); watchPatch([v], w, false); }).catch(() => toast("Could not save — is the hub running?"));
  },
  reset: (v) => {
    const f = S.view === "fr" && frFind(v);
    if (f) return Promise.all(slotIds(f.x).map((id) => api("/api/reset", { id }))).then(() => { toast("Progress reset"); frApplyWatch(f.e, [f.x.number], false, true); }).catch(() => toast("Could not save — is the hub running?"));
    api("/api/reset", { id: v }).then(() => { toast("Progress reset"); watchPatch([v], false, true); }).catch(() => toast("Could not save — is the hub running?")); },
  bulk: (v, t) => {
    const w = t.dataset.w === "true", body = { show_id: S.showId, watched: w };
    if (t.dataset.s !== "") body.season = +t.dataset.s;
    api("/api/bulk-watched", body).then((r) => {
      if (r && r.error) return toast(r.error);
      toast(w ? `Marked ${r.episodes} episode${r.episodes === 1 ? "" : "s"} watched${r.synced ? " — will sync" : ""}` : `Removed watched marks from ${r.episodes} episode${r.episodes === 1 ? "" : "s"}`);
      const s = showById(S.showId);
      const seas = t.dataset.s === "" ? s.seasons : [s.seasons[+t.dataset.s]];
      watchPatch(seas.flatMap((x) => x.episodes.map((e) => e.id)), w, false);
    }).catch(() => toast("Could not save — is the hub running?"));
  },
  season: (v) => { S.seasonIdx = +v; render(); },
  look: (v, t) => {
    const k = t.dataset.k;
    lsSet(k, k === "grain" ? v === "on" : v); applyLook();
    if (k === "bar") requestAnimationFrame(() => $$("[data-glide]").forEach(glide)); // the tabs moved
    $$(`[data-a=look][data-k="${k}"]`).forEach((b) => { const on = b.dataset.v === v; b.classList.toggle("on", on); b.setAttribute(b.getAttribute("role") === "radio" ? "aria-checked" : "aria-pressed", on); });
    paintCanvas();
  },
  zoomin: () => zoomSet(zoomStep(zoomGet(), 1)),
  zoomout: () => zoomSet(zoomStep(zoomGet(), -1)),
  /* online episodes (ani-cli) */
  srcfix: () => {
    const e = frEntry();
    if (e) { openModal({ t: "src", mid: e.id, title: e.title }); if (!slst(e.id).res) slSearch(e.id, e.title, [e.title, e.romaji]); }
  },
  slsearch: () => { const text = ($("#slq")?.value || "").trim(); if (text && S.modal?.t === "src") slSearch(S.modal.mid, text, []); },
  slpick: (v) => { const st = slst(S.modal.mid); st.pick = +v; paintModal(); },
  sllink: async () => {
    const mid = S.modal.mid, st = slst(mid), r = st.res?.[st.pick];
    if (!r || st.busy) return;
    st.busy = "Loading its episodes…"; st.err = ""; paintModal();
    const res = await api("/api/stream/link", { media_id: mid, query: st.q, title: r.title, index: r.index, display: S.modal.title }).catch(() => null);
    st.busy = "";
    if (!res || res.error) { st.err = res?.error || "Could not reach the hub."; paintModal(); return; }
    delete S.slink[mid];
    closeModal();
    toast("Episodes updated");
    await refreshData();
    if (S.view === "fr") frLoad(S.fr.eid);
  },
  playmode: (v, t) => play(v, 0, t.dataset.m),
  playstart: (v) => play(v, 0, undefined, false, true),
  unavail: (v, t, e) => (S.sel?.show === selKey() || e?.ctrlKey || e?.metaKey) ? selToggle(v, e?.shiftKey) : toast(S.data.stream_ready ? "This episode isn't available to watch online." : "Install ani-cli to watch this online."),
  dlep: (v) => { const [, mid, ep] = v.split(":"); download(+mid, [ep]); },
  dlmany: (v, t) => download(+v, JSON.parse(t.dataset.eps || "[]")),
  obox: () => { S.menu = S.menu === "ob" ? null : "ob"; S.obItems = null; paintNav(); if (S.menu) obLoad(); },
  obcancel: (v) => {
    api("/api/outbox/cancel", { id: +v }).then((r) => {
      if (r && r.error) return toast(r.error);
      S.outbox = r.left; toast("Cancelled — AniList won't get this change");
      if (!r.left) S.menu = null;
      S.obItems = (S.obItems || []).filter((it) => it.id !== +v); paintNav();
    }).catch(() => toast("Could not reach the hub — is it running?"));
  },
  dlcancel: (v) => { api("/api/download/cancel", { id: +v }).then(() => pollSoon(200)).catch(() => {}); },
  dlclear: () => { api("/api/download/clear", {}).then(() => { S.dl = S.dl.filter((j) => j.status === "running" || j.status === "queued"); paintNav(); }).catch(() => {}); },
  tipgo: () => { const go = S.modal?.go; S.settings.play_tip_done = true; api("/api/settings", { play_tip_done: true }).catch(() => {}); closeModal(); go?.(); },
  spref: (v, t) => {
    api("/api/settings", { [t.dataset.k]: v }).then((r) => {
      if (!r || r.error) return toast(r?.error || "Could not save — is the hub running?");
      S.settings = r; if (S.view === "settings") render();
    }).catch(() => toast("Could not save — is the hub running?"));
  },
  sync: () => {
    toast("Syncing with AniList…");
    api("/api/outbox/flush", {}).then((r) => {
      S.outbox = r.left; if (S.menu === "ob") obLoad(); paintNav();
      toast(!r.left ? "All changes synced to AniList" : r.left < r.before ? `Synced ${r.before - r.left} — ${r.left} still waiting`
        : "AniList is still unreachable — your changes are saved and will sync when it's back.");
    }).catch(() => toast("Could not reach the hub — is it running?"));
  },
  ext: (v, t, e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return; e.preventDefault(); openExternal(v); },
  menu: (v) => { S.menu = S.menu === v ? null : v; paintNav(); },
  rescan: (v, t) => { if (t.disabled) return; t.disabled = true; t.classList.add("busy"); api("/api/refresh", {}).catch(() => {}); toast("Rescanning + fetching metadata…"); setTimeout(() => { reload(); }, 2500); },
  adopt: (v, t) => { t.disabled = true; api("/api/adopt-root", {}).then((r) => { if (!r.ok) { toast(r.error || "Could not connect the folder."); t.disabled = false; return; } toast("Library folder connected."); loadSettings().then(reload); }).catch(() => { t.disabled = false; }); },
  alsearch: async () => {
    const mine = S.alq = S.q.trim(); S.alBusy = true; paintNav();
    let res;
    try { const r = await api("/api/match-candidates", { query: mine }); res = r && r.error ? [] : r || []; }
    catch { res = []; }
    if (S.alq !== mine) return; // a newer search took over
    S.alres = res; S.alBusy = false;
    if (S.q.trim() === mine) paintNav();
  },
  catpick: (v) => {
    const x = S.alres[+v];
    if (!x) return;
    openFranchise(x.id);
  },
  /* AniList entry panel */
  eadd: (v, t) => { t.disabled = true; entryPatch({ status: "PLANNING" }); },
  eset: (v, t) => {
    const k = t.dataset.k;
    if (k === "status") { if (v) entryPatch({ status: v }); return; }
    if (k === "score") { const n = parseFloat(v), max = S.entry?.f?.score_max || 10; if (Number.isNaN(n)) return; entryPatch({ score: Math.max(0, Math.min(max, n)) }); return; }
    const d = parseDateIn(v);
    if (d === null) { toast("Couldn't read that date — try YYYY-MM-DD"); t.value = S.entry?.f?.[k] || ""; return; }
    if (d !== (S.entry?.f?.[k] || "")) entryPatch({ [k]: d });
    else t.value = d;
  },
  calopen: (v) => {
    if (S.cal?.k === v) return calClose();
    const cur = (S.entry?.f?.[v] || "").split("-"), now = new Date();
    S.cal = { k: v, y: +cur[0] || now.getFullYear(), m: cur[1] ? +cur[1] - 1 : cur[0] ? 0 : now.getMonth() };
    if (S.entry?.f) entryPaint(S.entry.f, false);
  },
  calnav: (v) => { if (!S.cal) return; const d = new Date(S.cal.y, S.cal.m + +v, 1); S.cal.y = d.getFullYear(); S.cal.m = d.getMonth(); entryPaint(S.entry.f, false); },
  calday: (v) => { const k = S.cal?.k; S.cal = null; if (k && v !== (S.entry?.f?.[k] || "")) entryPatch({ [k]: v }); else calClose(); },
  ebump: (v, t) => {
    const k = t.dataset.k, f = S.entry?.f;
    if (!f) return;
    let n = Math.max(0, (f[k === "progress" ? "progress" : "repeat"] ?? 0) + +t.dataset.d);
    const cap = f.media?.episodes;
    if (k === "progress" && cap) n = Math.min(n, cap);
    entryPatch({ [k === "progress" ? "progress" : "repeat"]: n }, undefined, 450);
  },
  smode: (v) => {
    const e = frEntry();
    if (!e || S.modeBusy || (e.online.mode || "sub") === v) return;
    S.modeBusy = e.id; pagePatch();
    api("/api/stream/mode", { media_id: e.id, mode: v }).then((r) => {
      if (r.error) toast(r.error); else toast(v === "dub" ? "Online episodes are dubbed now" : "Online episodes are subbed now");
    }).catch(() => toast("Could not switch — is the hub running?")).finally(() => { S.modeBusy = null; frLoad(e.id); });
  },
  selmode: () => { if (S.sel?.show === selKey()) return selEnd(); S.sel = { show: selKey(), ids: new Set(), anchor: null }; pagePatch(); paintSel(); },
  selall: () => {
    if (S.view === "fr") {
      const e = frEntry(), ids = e ? e.slots.filter((x) => playableSlot(e, x)).map((x) => x.id) : [];
      if (!ids.length || S.sel?.show !== frSelKey()) return;
      const all = ids.every((id) => S.sel.ids.has(id));
      for (const id of ids) all ? S.sel.ids.delete(id) : S.sel.ids.add(id);
      pagePatch(); paintSel(); return;
    }
    const s = showById(S.showId), sea = s && (s.seasons[S.seasonIdx] || s.seasons[0]);
    if (!sea || S.sel?.show !== s.id) return;
    const all = sea.episodes.every((e) => S.sel.ids.has(e.id));
    for (const e of sea.episodes) all ? S.sel.ids.delete(e.id) : S.sel.ids.add(e.id);
    patchShow(); paintSel();
  },
  selmark: (v, t) => {
    const w = t.dataset.w === "true", ids = selEps().map((e) => e.id);
    if (!ids.length) return;
    if (S.view === "fr") { const nums = selEps().map((x) => x.number); S.sel = null; paintSel(); return frMark(frEntry(), nums, w); }
    api("/api/bulk-watched", { show_id: S.showId, watched: w, ids }).then((r) => {
      if (r && r.error) return toast(r.error);
      toast(w ? `Marked ${r.episodes} episode${r.episodes === 1 ? "" : "s"} watched${r.synced ? " — will sync" : ""}` : `Removed watched marks from ${r.episodes} episode${r.episodes === 1 ? "" : "s"}`);
      S.sel = null; paintSel();
      watchPatch(ids, w, false);
    }).catch(() => toast("Could not save — is the hub running?"));
  },
  seldl: () => {
    const e = frEntry(), xs = selEps().filter((x) => x.online && !x.local && !dlJob(x.online.id));
    if (e && xs.length) download(e.id, xs.map((x) => x.online.token));
    selEnd();
  },
  nxgo: () => { const id = S.modal?.p?.id; closeModal(); if (id) play(id, 0, undefined, true, undefined, true); },
  nxno: () => { const id = S.modal?.p?.id; closeModal(); if (id) play(id, 0, undefined, true, undefined, false); },
  nxstop: () => { closeModal(); api("/api/next-prompt/dismiss", {}).catch(() => {}); },
  vpngo: () => { const go = S.modal?.go; closeModal(); go?.(true); },
  vpnno: () => { const go = S.modal?.go; closeModal(); go?.(false); },
  skpgo: () => { const go = S.modal?.go; closeModal(); go?.(true); },
  skpno: () => { const go = S.modal?.go; closeModal(); go?.(false); },
  folder: (v) => api("/api/open-folder", { show_id: v }).then((r) => { if (!r.ok) toast(r.error || "Could not open the folder"); }).catch(() => toast("Could not open the folder — is the hub running?")),
  edit: () => { if (S.view === "fr" || S.view === "show") openModal({ t: "entry" }); },
  frmore: (v, t) => { const r = t.getBoundingClientRect(); frMore(r.left, r.bottom + 6); },
  efav: () => { if (S.entry?.f) entryPatch(undefined, !S.entry.f.favourite); },
  /* match repair */
  pick: (v, t) => { const id = t.dataset.s; mst(id).pick = +v; paintMatch(id); },
  msearch: (v) => {
    const input = $(`#mq-${CSS.escape(v)}`), text = (input?.value || "").trim(), m = mst(v);
    m.q = text;
    if (!text) return;
    const hit = text.match(/anilist\.co\/anime\/(\d+)/) || text.match(/^(\d+)$/);
    hit ? commitMatch(v, +hit[1]) : runSearch(v, text);
  },
  match: (v) => { const m = mst(v), c = (m.cands || [])[m.pick]; if (c) commitMatch(v, c.id); },
  numsave: (v, t) => {
    const card = t.closest(".nums"), numbers = {};
    for (const i of card.querySelectorAll("input[data-ep]")) {
      const n = Number(i.value);
      if (!Number.isInteger(n) || n < 0 || n > 9999) { card.querySelector(".msg").textContent = "Episode numbers are whole numbers from 0."; i.focus(); return; }
      numbers[i.dataset.ep] = n;
    }
    t.disabled = true;
    api("/api/episode-numbers", { show_id: v, numbers }).then((r) => {
      if (!r || r.error) { t.disabled = false; card.querySelector(".msg").textContent = r?.error || "Could not save — is the hub running?"; return; }
      toast("Episode numbers saved"); reload();
    }).catch(() => { t.disabled = false; card.querySelector(".msg").textContent = "Could not save — is the hub running?"; });
  },
  mrefresh: (v) => { const m = mst(v); if (!m.busy) runSearch(v, m.q || showById(v)?.title || "", true); },
  keeplocal: (v, t) => {
    const keep = t.dataset.k === "true";
    t.disabled = true;
    api("/api/keep-local", { show_id: v, keep }).then((r) => {
      if (r && r.error) { t.disabled = false; toast(r.error); return; }
      toast(keep ? "Kept local — you can still match it from its page" : "Back in To match");
      reload();
    }).catch(() => { t.disabled = false; toast("Could not save — is the hub running?"); });
  },
  /* settings + account */
  adv: (v) => { if (S.settings) setToggle(v, !S.settings[v]); },
  srsw: async (v, t, e) => { e?.stopPropagation(); if (!S.settings) return; await setToggle(v, !S.settings[v]); paintNav(); q.focus(); },
  setgo: (v, t) => { const tab = t.dataset.t; clearSearch(); q.blur(); S.setTab = tab; S.setOpen = true; S.setFlash = v; if (S.view === "settings") render(); else nav("settings"); },
  sropen: () => openSearch(),
  srclose: () => { ovClosed("sr"); clearSearch(); paintNav(); q.blur(); },
  srq: (v) => { q.value = v; q.dispatchEvent(new Event("input")); q.focus(); },
  srclr: () => { lsSet(SR_RECENT, []); paintNav(); },
  srlist: (v) => { clearSearch(); q.blur(); S.lib.tab = v; saveLib(); nav("library"); },
  srgo: (v) => { clearSearch(); q.blur(); nav(v); },
  cmd: (v) => { const c = COMMANDS[+v]; clearSearch(); q.blur(); paintNav(); c && c[2](); },
  settab: (v) => { S.setTab = v; render(true); },
  setopen: (v) => { S.setTab = v; S.setOpen = true; S.navIn = true; ovOpen("set", () => A.setback()); render(); scrollTo(0, 0); },
  setback: () => { S.setOpen = false; S.navIn = true; render(); scrollTo(0, 0); ovClosed("set"); },
  deck: (v) => openDeck(v === "lib" ? "lib" : "recs"), deckclose: () => closeDeck(),
  deckplay: (v) => { closeDeck(); play(v); },
  deckopen: (v) => { const [a, id] = v.split(":"); closeDeck(true); A[a](id); }, // the page takes the deck's history entry
  deckadd: (v, t) => {
    t.disabled = true;
    entrySave(+v, { status: "PLANNING" }).then((r) => {
      if (!r || r.error) throw 0;
      (S.recsAdded ||= new Set()).add(+v); toast("Added to Planning");
      t.innerHTML = `${ico("check")}<span>On Planning</span>`;
      refreshData();
    }).catch(() => { t.disabled = false; toast("Could not add it — try again"); });
  },
  sidetab: (v) => { S.sideTab = v; paintSide(); },
  fltopen: () => { document.body.classList.add("flt-open"); ovOpen("flt", () => A.fltclose()); paintPortals(); },
  fltclose: () => { document.body.classList.remove("flt-open"); paintPortals(); ovClosed("flt"); },
  rmton: () => remoteSet({ on: !S.remote?.on }),
  rmtcode: () => remoteSet({ new_code: true }),
  rmtdel: (v) => remoteSet({ remove: Number(v) }),
  rmtopen: () => { if (S.rmtOpen) return; S.rmtOpen = true; ovOpen("rmt", () => A.rmtclose()); paintRemote(); paintCanvas(); pollSoon(300); $("#rmt .rview")?.scrollTo(0, 0); $("#rmt .rview .rpp")?.focus({ preventScroll: true }); },
  rmtclose: () => { S.rmtOpen = false; paintRemote(); paintCanvas(); ovClosed("rmt"); },
  rmtsleep: () => remoteSend("stop_after", !S.live?.stop_after),
  rmtdelay: (v) => remoteSend("sub_delay", Number(v) === 0 ? 0 : Math.round(((S.live?.sub_delay || 0) + Number(v)) * 10) / 10),
  rmt: (v) => v === "back" ? remoteSend("seek", -10) : v === "fwd" ? remoteSend("seek", 10) : remoteSend(v),
  rmtseek: (v) => { S.rmtDrag = false; remoteSend("seek_to", Number(v)); },
  rmtvol: (v) => { S.rmtDrag = false; remoteSend("volume", Number(v)); },
  rmttrack: (v, t) => remoteSend(t.dataset.k, v === "no" ? "no" : Number(v)),

  ani: () => openModal({ t: "ani" }),
  aniout: async () => {
    await api("/api/auth/logout", {}).catch(() => {});
    toast("AniList disconnected"); S.alData = null; closeModal(); await aniStatusRefresh(); reload();
  },
  anitoken: async (v, t) => {
    const tok = ($("#tok")?.value || "").trim();
    if (!tok) return;
    t.disabled = true;
    const r = await api("/api/auth/token", { token: tok }).catch(() => null);
    t.disabled = false;
    if (!r || r.error) return setMsg(r?.error || "Could not reach the hub.");
    toast(`AniList connected as ${r.user}`); S.alData = null; closeModal(); aniStatusRefresh(); setTimeout(reload, 2500);
  },
  anioauth: async () => {
    /* Open the tab inside the click so the browser keeps popup permission.
       In shell mode window.open would spawn a SECOND app window showing
       anilist.co — send the authorize URL to the system browser instead. */
    if (SHELL) {
      const r0 = await api("/api/auth/start", {}).catch(() => null);
      if (!r0 || r0.error) return setMsg(r0?.error || "Could not reach the hub.");
      openExternal(r0.url);
      setMsg("Waiting for AniList — approve in your browser…");
      return pollOauth();
    }
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null; // anilist.co gets no handle on this window
    let r = null;
    try { r = await api("/api/auth/start", {}); } catch {}
    if (!r || r.error) { win?.close(); return setMsg(r?.error || "Could not reach the hub."); }
    if (win) { win.location = r.url; setMsg("Waiting for AniList — approve in the tab that opened…"); }
    else { const m = $("#animsg"); if (m) m.innerHTML = `Popup blocked — <a href="${esc(r.url)}" target="_blank" rel="noopener">open AniList</a> to continue.`; }
    return pollOauth();
  },
  tour: async () => { if (!S.settings) await loadSettings(); S.tour = { step: 0, choice: "connect" }; S.dir = null; openModal({ t: "tour" }); },
  dirbrowse: (v, t) => {
    const ctx = (t.id || "").replace(/^(dirbtn|libpath)-/, "") || (t.closest(".dialog") ? "tour" : "settings"), typed = ($("#libpath-" + ctx)?.value || "").trim(), field = t.tagName === "INPUT";
    if (S.dir?.ctx === ctx) { if (field) dirGo(typed); else dirClose(); return; } // Browse again = Cancel; Enter in the field walks there
    S.dir = { ctx, dirs: null };
    paintDir(ctx);
    dirGo(typed || (ctx === "extra" ? "" : S.settings?.library_root) || "");
  },
  dirgo: (v) => dirGo(v),
  xremove: (v) => extraRemove(+v),
  dirclose: () => dirClose(),
  diruse: () => {
    const p = S.dir, cur = S.settings?.library_root;
    if (!p?.path) return;
    if (p.ctx === "extra") return extraAdd(p.path);
    if (cur && cur !== p.path) { p.confirm = true; paintDir(); return; } // switching an indexed root is a confirmed step (Decision 23.7)
    dirSet(p.path);
  },
  dirconfirm: (v) => { if (v === "1") dirSet(S.dir.path); else { S.dir.confirm = false; paintDir(); } },
  tourpick: (v) => { S.tour.choice = v; paintModal(); }, tourback: () => { S.tour.step--; paintModal(true); },
  tourskip: () => finishTour(true),
  tournext: () => {
    const T = S.tour;
    if (T.step < 2) { T.step++; return paintModal(true); }
    finishTour(false);
  },
  /* Page 3's Connect: finish the tour first (so bailing out of the AniList
     dialog never re-nags), then hand off to the connect dialog. Found by
     Elias: choosing connect on page 2 skipped page 3 entirely — the old
     handoff closed the tour before the summary ever showed. */
  tourconnect: () => { api("/api/settings", { tour_done: true }).catch(() => {}); if (S.settings) S.settings.tour_done = true; closeModal(); openModal({ t: "ani" }); },
  recset: (v, t) => { S.rec.pick[t.dataset.m] = v; paintModal(); },
  recall: (v) => { for (const k of Object.keys(S.rec.pick)) S.rec.pick[k] = v; paintModal(); },
  recclose: () => recDone(),
  recapply: () => {
    const list = Object.entries(S.rec.pick).map(([m, w]) => ({ media_id: Number(m), winner: w }));
    api("/api/reconcile/apply", { decisions: list }).then((r) => {
      if (r && r.error) return toast(r.error);
      toast(`Reconciled — ${r.applied ?? list.length} shows updated`);
      S.rec = null; S.alData = null; closeModal(); reload(); setTimeout(reload, 3000);
    }).catch(() => toast("Could not apply — is the hub running?"));
  },
  dnscore: (v) => { $("#dn-score").value = v; dnPills(+v); },
  recopen: (v) => openFranchise(+v),
  dlopen: (v) => { S.menu = null; paintNav(); openFranchise(+v); },
  recadd: (v, t) => {
    t.disabled = true; t.classList.add("busy");
    entrySave(+v, { status: "PLANNING" }).then((r) => {
      if (!r || r.error) throw 0;
      (S.recsAdded ||= new Set()).add(+v); toast("Added to Planning");
      const card = t.closest(".rec"); card?.classList.add("gone"); setTimeout(() => { refreshData(); if (S.view === "home") render(); }, reduceMotion() ? 0 : 320);
    }).catch(() => { t.disabled = false; t.classList.remove("busy"); toast("Could not add it — try again"); });
  },
  dnplan: (v, t) => { t.disabled = true; entrySave(+v, { status: "PLANNING" }).then((r) => { t.textContent = r && !r.error ? "✓ On Planning" : "Could not add"; refreshData(); }).catch(() => { t.textContent = "Could not add"; }); },
  dnfav: (v, t) => { const on = !t.classList.contains("on"); t.classList.toggle("on", on); t.classList.add("pop1"); if (on) burstAt(t); t.setAttribute("aria-pressed", on); },
  dnskip: () => { const c = S.modal.c; api("/api/completion-seen", { media_id: c.media_id, count: c.count }).catch(() => {}); closeModal(); },
  dnsave: (v, t) => {
    const c = S.modal.c, patch = { status: $("#dn-status").value }, sc = $("#dn-score").value;
    delete entryMemo[c.media_id];
    if (sc !== "") patch.score = parseFloat(sc);
    t.disabled = true;
    entrySave(c.media_id, patch, $("#dn-fav").classList.contains("on")).then((res) => {
      if (res && res.error) { toast(res.error); t.disabled = false; return; }
      toast(res && res.queued ? "Saved — will sync when AniList is reachable" : "Saved");
      api("/api/completion-seen", { media_id: c.media_id, count: c.count }).catch(() => {});
      closeModal(); refreshData();
    }).catch(() => { toast("Could not save — is the hub running?"); t.disabled = false; });
  },
  /* franchise page */
  frsel: (v) => {
    const mid = +v;
    if (!S.fr || mid === S.fr.eid) return;
    S.fr.eid = mid; S.sel = null; paintSel();
    history.replaceState(navSnap(), "", navUrl());
    render();
    frLoad(mid); // the hub starts this entry's online list and stills
  },
  frbulk: (v, t) => { const e = frEntry(); if (e) frMark(e, null, t.dataset.w === "true"); },
  pinopen: (v, t) => openModal({ t: "pin", show: t.dataset.s, season: t.dataset.k, label: t.dataset.l, pick: null }),
  pinpick: (v) => { S.modal.pick = v; paintModal(); },
  pinsave: async () => {
    const m = S.modal, body = { show_id: m.show, season: m.season };
    if (m.pick === "none") body.none = true; else if (m.pick !== "auto") body.anilist_id = +m.pick;
    m.busy = true; paintModal();
    const r = await api("/api/season-match", body).catch(() => null);
    m.busy = false;
    if (!r || r.error) { m.err = r?.error || "Could not reach the hub."; paintModal(); return; }
    closeModal();
    toast(r.cleared ? "The hub matches this folder by itself again" : r.anilist_id ? `Now counted as ${r.title || "that entry"}` : "Kept as other files");
    await refreshData();
    if (S.view === "fr") frLoad(S.fr.eid);
  },
  close: () => closeModal(), scrim: (v, t, e) => { if (e.target === t) S.modal?.t === "done" ? A.dnskip() : closeModal(); }, /* a backdrop dismissal is a Skip (Decision 25: once per event) */
};

/* ---------- events ---------- */
const isField = (t) => /^(SELECT|INPUT)$/.test(t.tagName);
document.addEventListener("click", (e) => {
  if ($("#cm").style.display === "block" && !$("#cm").classList.contains("closing")) { cmHide(); ovClosed("cm"); }
  if (S.menu && !e.target.closest(".rel,#srb,#pal .pbox")) { S.menu = null; paintNav(); }
  if (S.cal && !e.target.closest(".datef")) calClose();
  const t = e.target.closest("[data-a]");
  if (t && !t.disabled && !isField(t) && A[t.dataset.a]) A[t.dataset.a](t.dataset.v, t, e);
});
document.addEventListener("change", (e) => {
  const t = e.target.closest("[data-a]");
  if (t && isField(t) && A[t.dataset.a]) A[t.dataset.a](t.value, t, e);
});
/* Dropdowns (Decision 38): a mouse click opens the app's own list instead of
   the browser's grey one; the <select> stays the real control (value, change
   event, keyboard), so every action keeps working. Touch keeps the phone's picker. */
let ddPtr = "mouse", ddSel = null;
document.addEventListener("pointerdown", (e) => { ddPtr = e.pointerType; }, { capture: true, passive: true });
function ddClose(refocus) {
  const m = $("#ddm");
  if (!m || !ddSel) return;
  m.classList.remove("open"); m.innerHTML = "";
  if (refocus) ddSel.focus({ preventScroll: true });
  ddSel = null;
}
function ddOpen(sel) {
  const m = $("#ddm"), r = sel.getBoundingClientRect(), box = sel.closest(".fl") || sel;
  const br = box.getBoundingClientRect();
  ddSel = sel;
  m.innerHTML = [...sel.options].map((o, i) => `<button type="button" class="${o.selected ? "on" : ""}" data-i="${i}" ${o.disabled ? "disabled" : ""}>${esc(o.textContent)}</button>`).join("");
  const w = Math.max(br.width, 170), left = Math.min(br.left, innerWidth - w - 10);
  m.style.minWidth = w + "px"; m.style.left = Math.max(10, left) + "px";
  const below = innerHeight - r.bottom - 12, h = Math.min(m.scrollHeight || 320, 320);
  if (below < Math.min(h, 220) && br.top > below) { m.style.top = ""; m.style.bottom = innerHeight - br.top + 6 + "px"; m.style.transformOrigin = "bottom left"; }
  else { m.style.bottom = ""; m.style.top = br.bottom + 6 + "px"; m.style.transformOrigin = "top left"; }
  m.classList.remove("open"); void m.offsetWidth; m.classList.add("open");
  (m.querySelector("button.on") || m.querySelector("button:not(:disabled)"))?.focus({ preventScroll: true });
}
function ddPick(i) {
  const sel = ddSel;
  ddClose(true);
  if (!sel || sel.selectedIndex === +i) return;
  sel.selectedIndex = +i;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
}
document.addEventListener("mousedown", (e) => {
  const sel = e.target.closest?.("select") || e.target.closest?.("label.fl")?.querySelector("select"); // the chip's label opens it too
  if (!sel || sel.disabled || ddPtr !== "mouse" || e.button !== 0) return;
  e.preventDefault();
  if (ddSel === sel) return ddClose(true);
  sel.focus({ preventScroll: true }); ddOpen(sel);
});
document.addEventListener("click", (e) => {
  const b = e.target.closest?.("#ddm button");
  if (b) { ddPick(b.dataset.i); return; }
  if (ddSel && !e.target.closest?.("select")) ddClose(false);
}, true);
document.addEventListener("keydown", (e) => {
  const t = e.target;
  if (ddSel && t.closest?.("#ddm")) {
    const bs = $$("#ddm button:not(:disabled)"), i = bs.indexOf(t);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); bs[(i + (e.key === "ArrowDown" ? 1 : -1) + bs.length) % bs.length]?.focus(); }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); bs[e.key === "Home" ? 0 : bs.length - 1]?.focus(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ddPick(t.dataset.i); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopImmediatePropagation(); ddClose(true); }
    return;
  }
  if (t.tagName === "SELECT" && !t.disabled && (e.key === "Enter" || e.key === " " || (e.altKey && e.key === "ArrowDown"))) { e.preventDefault(); ddOpen(t); }
}, true);
addEventListener("scroll", (e) => { if (ddSel && !e.target.closest?.("#ddm")) ddClose(false); }, { capture: true, passive: true });
addEventListener("resize", () => ddClose(false));
/* The episode menu's keys while it is open: ↑↓ ↵ · A W D (shown on its rows) · Esc. */
document.addEventListener("keydown", (e) => {
  const m = $("#cm");
  if (m.style.display !== "block" || e.ctrlKey || e.metaKey || e.altKey) return;
  const rows = [...m.querySelectorAll(".mi")], cur = rows.findIndex((r) => r.classList.contains("sel"));
  const pick = (r) => { e.preventDefault(); e.stopImmediatePropagation(); if (r) { m.style.display = "none"; r.click(); } };
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault(); e.stopImmediatePropagation();
    const n = (cur + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
    rows.forEach((r, k) => r.classList.toggle("sel", k === n));
  } else if (e.key === "Enter") pick(rows[cur] || rows[0]);
  else if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); m.style.display = "none"; }
  else { const k = e.key.toUpperCase(), r = rows.find((x) => x.querySelector("kbd")?.textContent === k); if (r) pick(r); }
}, true);
document.addEventListener("pointerover", (e) => { const r = e.target.closest?.("#cm .mi"); if (r) $$("#cm .mi").forEach((x) => x.classList.toggle("sel", x === r)); }, { passive: true });
document.addEventListener("contextmenu", (e) => {
  const ep = e.target.closest(".ep[data-a=playep]");
  if (ep) { e.preventDefault(); openCm(ep.dataset.v, e.clientX, e.clientY); }
});
/* Desktop-shell mode (the anime-hub wrapper opens the UI with ?shell=1):
   suppress the browser's own chrome so the window reads as an app, not a
   browser — no page zoom (ctrl+wheel / ctrl±) and no default context menu
   outside the app's own episode menu. A plain browser tab (no ?shell) keeps
   every normal browser behavior. */
const SHELL = new URLSearchParams(location.search).has("shell");
/* UI zoom preference: applied at boot (before first paint of the UI), shown
   and changed only in Settings → Appearance (Elias: no zoom shortcuts —
   ctrl+scroll / ctrl± stay dead in shell mode, they just must not trigger
   browser zoom). */
const zoomGet = () => { const v = lsGet("hubZoom", 100); return typeof v === "number" && v >= ZOOM_MIN && v <= ZOOM_MAX ? Math.round(v) : 100; };
function applyLook() {
  const d = document.documentElement, a = accentGet();
  d.dataset.motion = lookGet("motion"); d.dataset.art = lookGet("art"); d.dataset.bar = lookGet("bar"); d.dataset.grain = grainGet() ? "on" : "off";
  a === "follow" ? d.style.removeProperty("--fixed") : d.style.setProperty("--fixed", a);
}
function applyZoom() { document.documentElement.style.zoom = zoomGet() / 100; }
function zoomSet(z) { lsSet("hubZoom", z); applyZoom(); const el = $("#zoomval"); if (el) el.textContent = z + "%"; }
if (SHELL) {
  /* Browser zoom stays suppressed; it does not drive the setting. */
  addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
  /* Chromium's --app windows do NOT reserve chrome-level keys (see
     BrowserCommandController::IsReservedCommandOrKey — "In Apps mode, no
     keys are reserved"), so a page handler can swallow them before the
     browser acts. Block the set that would surface browser chrome or
     dialogs: new tab/window/incognito, close, quit, print, save,
     view-source, bookmark, history, downloads, devtools — and the zoom
     keys, which stay dead. Text-editing combos (ctrl+c/v/x/a) and the
     app's own keys pass. */
  addEventListener("keydown", (e) => {
    if (e.key === "F12") { e.preventDefault(); return; }
    if (!e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (e.key.length === 1 && "+-=0".includes(e.key)) { e.preventDefault(); return; } // zoom keys: dead
    if ("tnwqsudhjp".includes(k) && k.length === 1) e.preventDefault(); // chrome commands (ctrl / ctrl+shift alike)
    if (e.shiftKey && "ijc".includes(k) && k.length === 1) e.preventDefault(); // devtools
  });
  document.addEventListener("contextmenu", (e) => e.preventDefault(), true); // capture: default menu gone everywhere; the episode listener above still opens the app's own menu
  /* The window shows only the hub — nothing navigates in-window (what
     Electron/Tauri wrappers do). Plain anchors and middle/ctrl/shift clicks
     route to the system browser through the app's own openExternal path;
     drops can't navigate either. Left clicks on the app's own AniList chip
     (data-a=ext, no modifiers) stay with the delegated action handler. */
  const shellNav = (e) => {
    const a = e.target.closest("a[href]");
    if (!a || e.button === 2) return;
    if (a.dataset.a === "ext" && e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) return;
    e.preventDefault();
    openExternal(a.href);
  };
  document.addEventListener("click", shellNav, true);
  document.addEventListener("auxclick", shellNav, true); // middle click
  addEventListener("dragover", (e) => e.preventDefault());
  addEventListener("drop", (e) => e.preventDefault());
}
/* Images fade in once decoded; a 404 keeps the title placeholder. */
document.addEventListener("load", (e) => { const t = e.target; if (t.tagName === "IMG") { t.classList.add("ready"); t.parentElement?.classList.add("ready"); imgSeen.add(t.getAttribute("src")); } }, true);
// Back on the window (e.g. after toggling the VPN elsewhere): refresh now.
addEventListener("focus", () => pollSoon(0));
addEventListener("error", (e) => { const t = e.target; if (t.tagName === "IMG" && t.parentElement?.classList.contains("poster")) { t.parentElement.classList.remove("hasimg"); t.remove(); } }, true);
$("#brand").addEventListener("click", () => nav("home"));
/* ripple on primary controls */
document.addEventListener("pointerdown", (e) => {
  if (reduceMotion() || e.button) return;
  const t = e.target.closest(".btn,.pill,.dl-btn,.choice");
  if (!t || t.disabled) return;
  const r = t.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2.2, s = document.createElement("span");
  s.className = "rpl";
  s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
  t.appendChild(s); s.addEventListener("animationend", () => s.remove());
}, { passive: true });
/* cursor-following sheen on cards; subtle 3D tilt on the detail poster */
let pmRaf = 0;
document.addEventListener("pointermove", (e) => {
  if (reduceMotion() || e.pointerType === "touch") return;
  const t = e.target.closest && e.target.closest(".pw,.cont,.ep,.hero .poster");
  if (!t) return;
  cancelAnimationFrame(pmRaf);
  pmRaf = requestAnimationFrame(() => {
    const r = t.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (t.matches(".hero .poster")) {
      t.classList.add("tilting");
      t.style.setProperty("--ry", (x / r.width - 0.5) * 9 + "deg"); t.style.setProperty("--rx", -(y / r.height - 0.5) * 9 + "deg");
      t.style.setProperty("--gx", x + "px"); t.style.setProperty("--gy", y + "px");
    } else { t.style.setProperty("--mx", x + "px"); t.style.setProperty("--my", y + "px"); }
  });
}, { passive: true });
document.addEventListener("pointerout", (e) => {
  const t = e.target.closest && e.target.closest(".hero .poster");
  if (t && !t.contains(e.relatedTarget)) { t.classList.remove("tilting"); t.style.setProperty("--rx", "0deg"); t.style.setProperty("--ry", "0deg"); }
});
addEventListener("resize", () => $$("[data-glide]").forEach(glide));
document.addEventListener("pointerover", (e) => { const m = e.target.closest?.("#sr .mi[data-i]"); if (m && !m.classList.contains("sel")) srPreview(m.dataset.i); }, { passive: true });
const q = $("#q");
let alTimer = 0;
q.addEventListener("input", () => {
  S.q = q.value; S.menu = "sr";
  if (!S.q.trim()) { S.alres = null; S.alq = null; }
  paintNav();
  clearTimeout(alTimer);
  if (alSearchable() && S.q.trim().length >= 2) alTimer = setTimeout(() => { if (S.q.trim() !== S.alq) A.alsearch(); }, 350);
});
q.addEventListener("focus", () => { if (S.menu !== "sr") { S.menu = "sr"; paintNav(); } });
/* The palette opens centred over the page with its own line; the top bar only holds the button. */
function openSearch() { if (S.menu !== "sr") { S.menu = "sr"; ovOpen("sr", () => A.srclose()); paintNav(); } q.focus(); }
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const items = $$("#sr .mi");
    if (!items.length) return;
    e.preventDefault();
    let i = items.findIndex((x) => x.classList.contains("sel"));
    i = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    srPreview(items[i].dataset.i); items[i].scrollIntoView({ block: "nearest" });
    return;
  }
  if (e.key !== "Enter") return;
  const picked = $("#sr .mi.sel");
  if (picked) { e.preventDefault(); q.blur(); picked.click(); return; }
  const first = $("#sr .mi");
  if (first) { e.preventDefault(); q.blur(); first.click(); }
});
/* The Library's go-to-top button, once you've scrolled past the first row or two. */
function paintTop() {
  let el = $("#totop");
  if (!el) { el = document.createElement("button"); el.id = "totop"; el.dataset.a = "totop"; el.setAttribute("aria-label", "Back to top"); el.innerHTML = ico("up"); document.body.append(el); }
  el.classList.toggle("on", S.view === "library" && scrollY > 500);
}
addEventListener("scroll", () => { document.body.classList.toggle("sc", scrollY > 8); document.body.classList.toggle("sc2", scrollY > 84); paintTop(); }, { passive: true });
addEventListener("resize", gtrayFit, { passive: true });
/* Phones: the page's big title shrinks into the top bar as it scrolls away. */
function paintPtitle(t) {
  let el = $("#ptl");
  if (!el) { el = document.createElement("b"); el.id = "ptl"; $("#nav").insertBefore(el, $("#links")); }
  el.textContent = t || "";
}

/* keyboard: / search · Esc back · j/k/h/l or arrows move focus · Enter opens */
const focusables = () => $$("#app .hc, #app .ag, #app .rc, #app .rcv, #app .card, #app .ep, #app .ev").filter((el) => el.offsetParent);
function applyFocus() {
  const els = focusables();
  if (fIdx >= 0 && els[fIdx]) { els.forEach((e, i) => e.classList.toggle("focused", i === fIdx)); els[fIdx].scrollIntoView({ block: "nearest" }); } else fIdx = -1;
}
function moveFocus(dir) {
  const els = focusables().map((el) => { const r = el.getBoundingClientRect(); return { cx: r.left + r.width / 2, cy: r.top + r.height / 2 }; });
  if (!els.length || fIdx >= els.length) { fIdx = 0; return applyFocus(); }
  const cur = els[fIdx];
  let best = -1, bs = Infinity;
  els.forEach((o, i) => {
    if (i === fIdx) return;
    const dx = o.cx - cur.cx, dy = o.cy - cur.cy;
    const [ok, sc] = dir === "down" ? [dy > 4, Math.abs(dx) * 2.5 + dy] : dir === "up" ? [dy < -4, Math.abs(dx) * 2.5 - dy] : dir === "right" ? [dx > 4, Math.abs(dy) * 2.5 + dx] : [dx < -4, Math.abs(dy) * 2.5 - dx];
    if (ok && sc < bs) { bs = sc; best = i; }
  });
  if (best >= 0) { fIdx = best; applyFocus(); }
}
document.addEventListener("keydown", (e) => {
  /* e.target too: search blurs its field on Enter before this runs. */
  const inInput = [document.activeElement, e.target].some((x) => /^(INPUT|TEXTAREA|SELECT)$/.test(x.tagName));
  if (S.modal) {
    if (e.key === "Escape") { e.preventDefault(); const t = S.modal.t; t === "tour" ? finishTour(true) : t === "rec" ? recDone() : t === "done" ? A.dnskip() : closeModal(); }
    else if (e.key === "Tab") {
      const f = $$("#modal :is(button,select,input):not(:disabled)"), i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { f[f.length - 1].focus(); e.preventDefault(); } else if (!e.shiftKey && i === f.length - 1) { f[0].focus(); e.preventDefault(); }
    } else if (e.key === "Enter" && document.activeElement.id === "tok") { e.preventDefault(); $("[data-a=anitoken]")?.click(); }
    else if (e.key === "Enter" && document.activeElement.id === "libpath-tour") { e.preventDefault(); A.dirbrowse(null, document.activeElement); }
    else if (e.key === "Enter" && document.activeElement.id === "slq") { e.preventDefault(); A.slsearch(); }
    return;
  }
  if (e.key === "Enter" && /^libpath-(settings|extra)$/.test(document.activeElement.id)) { e.preventDefault(); A.dirbrowse(null, document.activeElement); return; }
  if (e.key === "Enter" && document.activeElement.id?.startsWith("mq-")) { e.preventDefault(); A.msearch(document.activeElement.id.slice(3)); return; }
  if (e.key === "/" && !inInput) { e.preventDefault(); openSearch(); return; }
  if (!inInput && !e.ctrlKey && !e.altKey && !e.metaKey) {
    const tab = TABS[+e.key - 1];
    if (tab && e.key !== "0") { e.preventDefault(); nav(tab[0]); return; }
    if (e.key === ",") { e.preventDefault(); A.go("settings"); return; }
    if (e.key === "?") { e.preventDefault(); S.menu = S.menu === "keys" ? null : "keys"; paintNav(); return; }
    /* The show page's keys (shown on its buttons): E edit, D download, ⇧W mark all, ↵ play. */
    if (S.view === "home") {
      const b = e.key === "o" ? $('#app .upn [data-a="open"]') : e.key === "Enter" && fIdx < 0 && !document.activeElement.closest?.("#app [data-a]") ? $('#app .upn [data-a="play"]') : null;
      if (b) { e.preventDefault(); b.click(); return; }
    }
    if (S.view === "fr" || S.view === "show") {
      const hit = (sel) => { const b = $(`#app ${sel}`); if (!b || b.disabled) return false; e.preventDefault(); b.click(); return true; };
      if (e.key === "e" && hit('[data-a="edit"]')) return;
      if (e.key === "d" && hit('.cta [data-a="dlmany"]')) return;
      if (e.key === "W" && hit('[data-a="frbulk"]')) return;
      if (e.key === "Enter" && fIdx < 0 && !document.activeElement.closest?.("#app [data-a]") && hit('.cta [data-a="play"]')) return;
    }
  }
  if (e.key === "Escape") {
    if (S.menu === "sr") { clearSearch(); paintNav(); q.blur(); return; }
    if (S.menu) { S.menu = null; paintNav(); return; }
    if (S.sel && !inInput) { selEnd(); return; }
    if (S.q) { clearSearch(); paintNav(); q.blur(); return; }
    if (inInput) { document.activeElement.blur(); return; }
    if (S.view === "show" || S.view === "fr") A.back(); else if (S.view !== "home") nav("home");
    return;
  }
  if (inInput) return;
  const dirs = { j: "down", k: "up", h: "left", l: "right", ArrowDown: "down", ArrowUp: "up", ArrowLeft: "left", ArrowRight: "right" }, dir = dirs[e.key];
  if (dir) { e.preventDefault(); if (fIdx < 0) { fIdx = 0; applyFocus(); } else moveFocus(dir); }
  else if (e.key === "Enter" && fIdx >= 0) focusables()[fIdx]?.click();
  else if (e.key === "Enter" && document.activeElement.classList.contains("ep")) document.activeElement.click();
});

/* ---------- back gesture and swipes (Decision 44; Elias 2026-10-27: "a
   universal back button… swipe from top to bottom so it closes… more of
   these") ---------- */
/* Layers (the remote, the deck, search, the filter sheet, a settings
   section on a phone) put one entry on the browser history when they open,
   so Android's back gesture / button closes the top layer first instead of
   leaving the page. Closing one by hand takes its entry back off. */
const OV = [];
let ovSkip = 0;
/* A layer closed and opened again in the same moment (one dialog handing
   over to the next) keeps its history entry instead of stepping back. */
const ovLeaving = new Set();
function ovOpen(name, close) {
  if (OV.some((o) => o.name === name)) return;
  OV.push({ name, close });
  if (ovLeaving.delete(name)) return;
  history.pushState({ ...navSnap(), ov: name }, "", location.href);
}
function ovClosed(name) {
  const k = OV.findIndex((o) => o.name === name);
  if (k < 0) return;
  OV.splice(k, 1); ovLeaving.add(name);
  setTimeout(() => { if (ovLeaving.delete(name)) { ovSkip++; history.back(); } }, 0);
}
function ovDrop(name) { const k = OV.findIndex((o) => o.name === name); if (k >= 0) OV.splice(k, 1); }
/* Swipe down to close: grab a layer at its top and pull. Past ~120 px (or a
   quick flick) it slides away; otherwise it springs back. Horizontal
   controls (sliders, segments, strips) keep their own drag. */
const PULL = [
  { sel: ".rview", scroller: (el) => el, close: () => A.rmtclose(), fade: () => ({ style: {} }) }, // stays solid, only shrinks
  { sel: "#deck .deck", scroller: (el) => el.querySelector(".dscroll"), close: () => closeDeck() },
  { sel: "#pal .pbox", scroller: (el) => el.querySelector(".plist"), close: () => A.srclose(), when: () => touchSearch(), fade: () => $("#pal") },
  { sel: ".fsheet .filters", scroller: (el) => el, close: () => A.fltclose() },
  { sel: "#modal .dialog", scroller: (el) => el, close: () => modalDismiss(), when: () => isPhone() && S.modal && S.modal.t !== "tour" },
  { sel: "#cm", scroller: (el) => el, close: () => { cmHide(); ovClosed("cm"); }, when: () => isPhone() },
];
const NO_PULL = "input[type=range], .seg, .strip, .eps.strip, .srposters, .hstrip, .srchips, .dgen, .gtray, .switch";
/* Pull to refresh (Home, Library, Journal, the week): installed web apps
   get no browser pull-to-refresh, so the app draws its own — a circle that
   drops in with the pull, turns with it, and spins while the hub reloads. */
let ptrBusy = false;
function ptrEl() {
  let el = $("#ptr");
  if (!el) { el = document.createElement("div"); el.id = "ptr"; el.setAttribute("aria-hidden", "true"); el.innerHTML = `<i>${ico("refresh")}</i>`; document.body.append(el); }
  return el;
}
function ptrShow(dist) {
  const el = ptrEl(), k = Math.min(dist / 70, 1);
  el.className = "on" + (k >= 1 ? " armed" : "");
  el.style.transform = `translate3d(-50%,${Math.round(dist - 56)}px,0)`;
  el.style.opacity = String(Math.min(1, k * 1.4));
  el.firstChild.style.transform = `rotate(${Math.round(dist * 3.2)}deg)`;
}
function ptrHide() { const el = $("#ptr"); if (!el) return; el.className = "out"; el.style.transform = "translate3d(-50%,-56px,0)"; el.style.opacity = "0"; }
async function ptrRun() {
  ptrBusy = true;
  const el = ptrEl();
  el.className = "on busy"; el.style.transform = "translate3d(-50%,18px,0)"; el.style.opacity = "1"; el.firstChild.style.transform = "";
  const t0 = Date.now();
  try {
    if (S.view === "history" || S.view === "stats") S.alData = null;
    S.recs = null;
    await reload();
  } catch {}
  await sleep(Math.max(0, 600 - (Date.now() - t0))); // a moment of spin even when it was instant
  ptrHide(); ptrBusy = false;
}
let pull = null;
const buzz = () => { try { navigator.vibrate?.(8); } catch {} };
/* The hint beside the screen edge while you swipe sideways: an arrow in a
   glass circle that grows with the swipe and fills once letting go counts,
   with the name of where you're going. */
let swHint = null;
function hintShow(side, label, prog) {
  if (!swHint) { swHint = document.createElement("div"); swHint.id = "swhint"; swHint.setAttribute("aria-hidden", "true"); document.body.append(swHint); }
  const armed = prog >= 1, k = Math.min(prog, 1);
  if (swHint.dataset.k !== side + label) { swHint.dataset.k = side + label; swHint.innerHTML = `<i>${ico(side === "left" ? "chevl" : "chevr")}</i><span>${esc(label)}</span>`; }
  const cls = `on ${side}${armed ? " armed" : ""}`;
  if (swHint.className !== cls) swHint.className = cls;
  swHint.style.opacity = (0.25 + k * 0.75).toFixed(3);
  swHint.style.transform = `translateY(-50%) scale(${(0.6 + k * 0.4).toFixed(3)})`;
}
function hintHide() { if (swHint) { swHint.className = ""; swHint.style.opacity = swHint.style.transform = ""; } }
/* Smoothness (Elias: "often the animation is a bit laggy"): while a finger
   drags, the frosted glass on the bars goes plain (redrawing a blur under
   a moving page is a phone GPU's most expensive job), the moving part is
   told ahead (will-change), and styles are written once per frame. */
let swFrame = 0, swWrite = null;
function swSet(fn) { swWrite = fn; if (!swFrame) swFrame = requestAnimationFrame(() => { swFrame = 0; const f = swWrite; swWrite = null; f && f(); }); }
function swBegin(el) { document.body.classList.add("swiping"); el.style.willChange = "transform"; }
function swEnd(el) { document.body.classList.remove("swiping"); if (el) el.style.willChange = ""; }
const TAB_NAME = { home: "Home", library: "Library", history: "Journal" };
document.addEventListener("touchstart", (e) => {
  pull = null;
  if (e.touches.length !== 1 || !mq("(pointer:coarse)")) return;
  const t = e.target, L = PULL.find((p) => t.closest?.(p.sel) && (!p.when || p.when()));
  /* Sideways swipes leave real sideways scrollers alone; the phone's
     episode list is vertical (it only keeps the PC strip's class). */
  const x = e.touches[0].clientX, y = e.touches[0].clientY;
  const inApp = t.closest?.("#app") && !t.closest("input[type=range], .seg, .switch, .hstrip, .srposters, .srchips, .dgen, .gtray, .eps-head, .tabs, .tabs2, .rels, .looks, .rdy, .agenda")
    && !(t.closest(".strip") && !(isPhone() && t.closest(".eps")));
  if (L) {
    const el = t.closest(L.sel), sc = L.scroller(el);
    if ((sc && sc.scrollTop > 2) || t.closest(NO_PULL)) return;
    pull = { kind: "close", L, el, x, y, t: Date.now(), dy: 0, live: false };
  } else if (t.closest?.(".rbar") && !t.closest("button")) pull = { kind: "rbar", y };
  else if (isPhone() && inApp && x > 24 && x < innerWidth - 24) { // the outer edges are the system's back gesture
    const ptrOK = scrollY <= 0 && ["home", "library", "history", "stats", "week"].includes(S.view) && !ptrBusy;
    if (["home", "library", "history", "stats"].includes(S.view)) pull = { kind: "tab", x, y, t: Date.now(), ptrOK };
    else if (["show", "fr", "week", "settings"].includes(S.view) && history.length > 1) pull = { kind: "back", x, y, t: Date.now(), ptrOK };
  }
}, { passive: true });
/* Sideways: Home ⇄ Library ⇄ Journal, or back from a page. The page
   follows the finger; past ~30 % of the width (or a flick) it goes. */
function sideTarget(p, dx) {
  if (p.kind === "back") return dx > 0 ? { label: "Back", side: "left" } : null;
  const order = ["home", "library", "history"], at = order.indexOf(S.view === "stats" ? "history" : S.view), to = order[at + (dx < 0 ? 1 : -1)];
  return to ? { to, label: TAB_NAME[to], side: dx < 0 ? "right" : "left" } : null;
}
document.addEventListener("touchmove", (e) => {
  if (!pull) return;
  const p = pull, x = e.touches[0].clientX, y = e.touches[0].clientY;
  if (p.kind === "rbar") { if (y - p.y < -28) { pull = null; buzz(); A.rmtopen(); } return; }
  if (p.kind === "ptr") {
    e.preventDefault();
    const dist = Math.min(Math.max(0, (y - p.y0) * 0.5), 110);
    p.dist = dist;
    swSet(() => ptrShow(dist));
    if (dist >= 70 && !p.armed) { p.armed = true; buzz(); } else if (dist < 70) p.armed = false;
    return;
  }
  if (p.kind === "tab" || p.kind === "back") {
    const dx = x - p.x, dy = y - p.y;
    /* Clearly sideways: the swipe is ours (else the browser takes it and
       cancels the touch). Clearly up/down: it's a scroll, let it be. */
    if (!p.side) {
      if (p.ptrOK && dy > 10 && dy > Math.abs(dx) && scrollY <= 0) { p.kind = "ptr"; p.y0 = y; e.preventDefault(); return; }
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { pull = null; return; }
      if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.5) { p.side = true; p.x0 = x; $("#app").style.transition = "none"; swBegin($("#app")); }
      else return;
    }
    e.preventDefault();
    const d = x - p.x0, tg = sideTarget(p, d), lim = innerWidth * 0.3;
    p.lx = x; p.ly = y; p.tg = tg;
    /* Nothing that way (first/last tab): only a little give. */
    const tx = tg ? d * (p.kind === "back" ? 1 : 0.55) : d * 0.12;
    const prog = tg ? Math.abs(d) / lim : 0;
    /* Back (Android's predictive back): the page shrinks toward 90 % and
       shifts a little, rather than sliding off with the finger. */
    const sc = p.kind === "back" && tg ? 1 - 0.1 * Math.min(prog, 1) : 1, bx = p.kind === "back" && tg ? Math.min(d * 0.3, innerWidth * 0.1) : tx;
    swSet(() => {
      if (p.kind === "back") $("#app").style.transformOrigin = `50% ${Math.round(scrollY + innerHeight / 2)}px`;
      $("#app").style.transform = `translate3d(${bx}px,0,0)${sc < 1 ? ` scale(${sc.toFixed(4)})` : ""}`;
      if (p.kind === "back") $("#portal").style.opacity = String(1 - Math.min(Math.abs(d) / lim, 1));
      if (tg) hintShow(tg.side, tg.label, prog); else hintHide();
    });
    if (prog >= 1 && !p.armed) { p.armed = true; buzz(); } else if (prog < 1) p.armed = false;
    return;
  }
  if (p.kind !== "close") return;
  const d = y - p.y, dx = x - p.x;
  if (!p.live) {
    if (Math.abs(dx) > Math.abs(d) || d < 0) { if (Math.abs(dx) > 10 || d < -6) pull = null; return; }
    if (d < 8) return;
    p.live = true; p.y = y; p.el.style.transition = "none"; swBegin(p.el);
    p.el.style.transformOrigin = "50% 0";
    document.body.classList.add("peek"); // what's behind the layer shows while you pull
    if (document.activeElement?.blur && p.L.sel.startsWith("#pal")) document.activeElement.blur();
  }
  p.dy = Math.max(0, y - p.y);
  e.preventDefault();
  const k = Math.min(p.dy / 320, 1), fade = p.L.fade ? p.L.fade(p.el) : p.el, dy = p.dy;
  /* It becomes a card: a little smaller, rounded, sliding down. */
  swSet(() => {
    p.el.style.transform = `translate3d(0,${dy}px,0) scale(${1 - k * 0.07})`;
    p.el.style.borderRadius = `${Math.round(k * 28)}px`;
    fade.style.opacity = String(1 - k * 0.45);
  });
  if (p.dy > 120 && !p.armed) { p.armed = true; buzz(); } else if (p.dy <= 120) p.armed = false;
}, { passive: false });
const unstyle = (...els) => { for (const el of els) if (el) { el.style.transform = el.style.opacity = el.style.transition = el.style.borderRadius = el.style.transformOrigin = ""; } };
document.addEventListener("touchcancel", () => { if (pull?.kind === "ptr" && !ptrBusy) ptrHide(); cancelAnimationFrame(swFrame); swFrame = 0; swEnd(pull?.side ? $("#app") : pull?.el); if (pull?.side) { unstyle($("#app"), $("#portal")); hintHide(); } if (pull?.live) { unstyle(pull.el, pull.L.fade?.(pull.el)); document.body.classList.remove("peek"); } pull = null; });
document.addEventListener("touchend", () => {
  const p = pull;
  pull = null;
  if (!p) return;
  cancelAnimationFrame(swFrame); swFrame = 0; swWrite = null;
  if (p.side || p.live) swEnd(p.side ? $("#app") : p.el);
  if (p.kind === "ptr") { if ((p.dist || 0) >= 70) ptrRun(); else ptrHide(); return; }
  if (p.kind === "tab" || p.kind === "back") {
    if (!p.side) return;
    const app = $("#app"), d = (p.lx ?? p.x0) - p.x0, v = Math.abs(d) / Math.max(1, Date.now() - p.t), go = p.tg && (Math.abs(d) > innerWidth * 0.3 || (Math.abs(d) > 60 && v > 0.45));
    hintHide();
    const ease = "transform .22s cubic-bezier(.2,.8,.2,1), opacity .22s";
    if (!go) { app.style.transition = ease.replace(".22s cubic-bezier(.2,.8,.2,1)", ".38s cubic-bezier(.3,1.35,.5,1)"); app.style.transform = ""; app.style.opacity = ""; $("#portal").style.opacity = ""; setTimeout(() => unstyle(app), 400); return; }
    app.style.transition = ease; app.style.transform = p.kind === "back" ? `translate3d(${Math.round(innerWidth * 0.35)}px,0,0) scale(.88)` : `translateX(${Math.sign(d) * innerWidth * 0.5}px)`; app.style.opacity = "0";
    setTimeout(() => {
      unstyle(app, $("#portal"));
      if (p.kind === "tab") nav(p.tg.to);
      else { backSwiped = true; history.back(); }
    }, 170);
    return;
  }
  if (p.kind !== "close" || !p.live) return;
  const v = p.dy / Math.max(1, Date.now() - p.t), el = p.el, fade = p.L.fade ? p.L.fade(el) : el;
  el.style.transition = fade.style.transition = "transform .26s cubic-bezier(.2,.8,.2,1), opacity .26s, border-radius .26s";
  if (p.dy > 120 || (p.dy > 50 && v > 0.5)) {
    el.style.transform = "translateY(100%) scale(.93)"; fade.style.opacity = "0";
    /* the inline offset stays until the layer is gone: clearing it first made
       a sheet's own close animation start from the top again */
    setTimeout(() => { p.L.close(); setTimeout(() => { unstyle(el, fade); document.body.classList.remove("peek"); }, 280); }, 240);
  } else {
    el.style.transition = fade.style.transition = "transform .38s cubic-bezier(.3,1.35,.5,1), opacity .3s, border-radius .3s";
    el.style.transform = ""; el.style.borderRadius = ""; fade.style.opacity = "";
    setTimeout(() => { unstyle(el, fade); document.body.classList.remove("peek"); }, 400);
  }
});
/* Long-press (touch): on an episode it opens the episode menu with a light
   tap, like right-click on the PC. A hold never turns into a tap when the
   finger lifts (it used to start the episode), and the browser's own
   long-press menu stays away from the app. */
let lp = null, lpUntil = 0;
document.addEventListener("touchstart", (e) => {
  clearTimeout(lp?.timer); lp = null;
  if (e.touches.length !== 1 || !mq("(pointer:coarse)")) return;
  const t = e.target, x = e.touches[0].clientX, y = e.touches[0].clientY;
  lp = { x, y, timer: setTimeout(() => {
    if (!lp) return;
    lp.fired = true;
    const ep = t.closest?.(".ep[data-a=playep]");
    if (ep && !pull?.side && !pull?.live && $("#cm").style.display !== "block") { buzz(); openCm(ep.dataset.v, x, y); }
  }, 480) };
}, { passive: true, capture: true });
document.addEventListener("touchmove", (e) => { if (lp && Math.hypot(e.touches[0].clientX - lp.x, e.touches[0].clientY - lp.y) > 10) { clearTimeout(lp.timer); lp = null; } }, { passive: true, capture: true });
document.addEventListener("touchend", () => { if (!lp) return; clearTimeout(lp.timer); if (lp.fired) lpUntil = Date.now() + 700; lp = null; }, { capture: true });
document.addEventListener("click", (e) => { if (Date.now() < lpUntil) { e.preventDefault(); e.stopImmediatePropagation(); lpUntil = 0; } }, true);
document.addEventListener("contextmenu", (e) => { if (mq("(pointer:coarse)") && !e.target.closest?.("input, textarea")) { e.preventDefault(); } }, true);
/* Switches answer with a light tap (Android). */
document.addEventListener("click", (e) => { if (e.target.closest?.(".switch") && mq("(pointer:coarse)")) buzz(); }, true);
/* A back swipe lands on the page before with a slide in from the left. */
let backSwiped = false;
/* A grab handle shows that a layer can be pulled down. */
const grab = '<i class="grabber" aria-hidden="true"></i>';

/* ---------- browser history (back/forward, mouse side buttons, #deep links) ---------- */
let navSeeded = false;
const navSnap = () => ({ view: S.view, showId: S.showId, seasonIdx: S.seasonIdx, back: S.showBack, fr: S.fr?.eid ?? null, scroll: scrollY });
const navUrl = () => location.pathname + location.search + (S.view === "show" ? "#show=" + encodeURIComponent(S.showId) : S.view === "fr" ? "#f=" + S.fr.eid : VIEWS.includes(S.view) && S.view !== "home" ? "#" + S.view : "");
function pushNav() {
  const h = navSnap();
  /* Going somewhere from inside a layer (a result in search, Details in the
     deck): the new page takes the layer's history entry. */
  if (history.state?.ov) { OV.length = 0; history.replaceState(h, "", navUrl()); return; }
  if (navSeeded) history.pushState({ ...h, inApp: true }, "", navUrl()); else { navSeeded = true; history.replaceState(h, "", navUrl()); }
}
function applyNav(h) {
  S.navIn = true; S.view = VIEWS.includes(h.view) || h.view === "show" || (h.view === "fr" && h.fr) ? h.view : "home"; S.showId = h.showId ?? null; S.seasonIdx = h.seasonIdx || 0; S.showBack = h.back || "home";
  if (S.view === "fr") S.fr = { eid: h.fr, page: frKnown(h.fr), err: null };
  clearSearch(); render();
  if (S.view === "fr") frLoad(h.fr);
  history.replaceState(h, "", navUrl());
  scrollTo(0, h.scroll || 0);
}
addEventListener("popstate", (e) => {
  if (ovSkip) { ovSkip--; return; } // a layer closed by hand took its own entry back
  const top = OV.pop();
  if (top) { top.close(); return; } // back closes the top layer first
  applyNav(e.state || {});
  if (backSwiped) { backSwiped = false; const app = $("#app"); app.classList.remove("enter", "tab-l", "tab-r"); void app.offsetWidth; app.classList.add("tab-r"); }
});
history.replaceState(navSnap(), "", location.href);
navSeeded = true;

/* ---------- boot ---------- */
aniStatusRefresh();
const hashShow = location.hash.match(/^#show=(.+)$/), hashFr = location.hash.match(/^#f=(\d+)$/), hashView = location.hash.match(/^#(library|history|stats|settings|week)$/);
applyLook(); // index.html already did; this drops an unknown saved value
applyZoom(); // before first paint, so the UI never flashes at 100 %
reload().then(() => {
  if (!S.data) return;
  if (hashFr) { S.view = "fr"; S.fr = { eid: +hashFr[1], page: null, err: null }; S.navIn = true; render(); frLoad(+hashFr[1]); return; }
  const hashRel = hashShow && decodeURIComponent(hashShow[1]).match(/^aid:(\d+)$/);
  if (hashRel) { openFranchise(+hashRel[1]); return; } // an old #show=aid:<id> link
  if (hashShow && showById(decodeURIComponent(hashShow[1]))) { S.showId = decodeURIComponent(hashShow[1]); S.view = "show"; const s = showById(S.showId), t = pickTarget(s); S.seasonIdx = t ? Math.max(0, s.seasons.indexOf(t.sea)) : 0; S.navIn = true; render(); }
  else if (hashView) { S.view = hashView[1]; S.navIn = true; render(); }
}).then(pollNow).then(loadSettings).then(maybeTour);

addEventListener("error", (e) => { if (e.target === window) toast("UI error: " + e.message); });
addEventListener("unhandledrejection", (e) => toast("UI error: " + (e.reason?.message || e.reason)));
