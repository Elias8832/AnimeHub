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
  lib: (({ year, score, genres, air, ...l }) => ({ tab: null, src: "all", fmt: "all", sort: "updated", ...l,
         air: air === "FINISHED_AIRING" ? "FINISHED" : air || "all", genres: Array.isArray(genres) ? genres : [],
         year: Array.isArray(year) ? year : "all", score: Array.isArray(score) ? score : "all" }))(lsGet("hubLib", {})),
  histRange: lsGet("histRange", "7d"), histMax: 120,
  match: {}, advOpen: {}, tour: { step: 0, choice: "connect" }, rec: null, navIn: true, countNext: false, opener: null,
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
const resumable = (ep) => !ep.watched && ep.duration && ep.position / ep.duration > 0.01;

/* What a show's big button plays. Seasons come in the hub's order
   (season_rank): main seasons first, extras last. */
function pickTarget(s) {
  const ord = s.seasons.map((sea) => ({ sea }));
  let resume = null;
  for (const { sea } of ord) for (const ep of sea.episodes) {
    if (resumable(ep) && (!resume || (ep.last_played || "") > (resume.ep.last_played || ""))) resume = { sea, ep, kind: "resume" };
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
  return { ...ctx, label: "Rewatch", ep: ctx.ep };
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
const LOOKS = { motion: ["full", "calm", "off"], art: ["full", "soft", "off"] };
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
function toast(m) {
  const t = $("#toast");
  t.innerHTML = '<i class="tdot"></i><span></span><b class="ttime"></b>'; // fresh timer bar each time
  t.querySelector("span").textContent = m;
  t.classList.toggle("err", /could not|couldn't|failed|error|can't|cannot|not accessible|unreachable|went wrong/i.test(m));
  t.classList.add("on");
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove("on"), 2400);
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
  const left = pct >= 1 && !ep.watched ? `${fmtLeft(ep.duration, ep.position)} left` : ep.duration ? `${Math.round(ep.duration / 60)} min` : "";
  const src = streamEp(ep) ? "online" : "on disk";
  const seen = (sea?.episodes || []).filter((x) => x.watched).length, tot = sea?.episodes.length || 0;
  const verb = up.label === "Rewatch" ? "Rewatch" : resumable(ep) ? "Resume" : "Play";
  return `<div class="upn" style="--h:${hueOf(s.title)}">${art ? `<i class="upa" style="background-image:${esc(cssUrl(art))}"></i>` : ""}<div class="upc">
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
    if (t) out.push({ s: x.ctx.s, t });
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
  const soon = list.filter((e) => { const t = at(e); return t > now - 3600e3 && t < now + 7 * 864e5; }).slice(0, 3);
  if (!soon.length) return "";
  const today = new Date(now), dow = (today.getDay() + 6) % 7, monday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - dow).getTime();
  const has = new Set(list.map((e) => Math.floor((at(e) - monday) / 864e5)).filter((d) => d >= 0 && d < 7));
  const when = (t) => { const d = new Date(t), same = d.toDateString() === today.toDateString(), tm = d.toTimeString().slice(0, 5);
    return [same ? "Today" : DAYS[(d.getDay() + 6) % 7], tm]; };
  return `<section class="hsec"><div class="sh"><h2>This week</h2><span class="mut">airing on your lists</span><span class="ruler">${DAYS.map((d, i) => `<span class="${i === dow ? "today" : ""}">${i === dow ? "today" : d}<i class="${has.has(i) ? "has" : ""}"></i></span>`).join("")}</span></div>
    <div class="agenda">${soon.map((e) => { const t = at(e), [d, tm] = when(t), sec = (t - now) / 1000;
      return `<button class="ag${d === "Today" ? " today" : ""}" data-a="entry" data-v="${esc(e.id)}">${e.poster ? `<i class="agb" style="background-image:${esc(cssUrl(e.poster))}"></i><i class="agp" style="background-image:${esc(cssUrl(e.poster))}"></i>` : ""}
        <span class="agt"><span class="when">${d} <small>${tm}</small></span><b>${esc(e.title)}</b><span class="m">episode ${esc(e.next_episode?.number ?? "")}</span></span><span class="cdn">${sec > 0 ? "in " + fmtIn(sec) : "out now"}</span></button>`; }).join("")}</div></section>`;
}
function Home() {
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
  if (L.length) body += `<section class="hsec"><div class="sh"><h2>Continue watching</h2><span class="mut">${L.length}</span><span class="fill"></span><button class="arr" data-a="strip" data-d="-1" aria-label="Scroll left" disabled>${ico("chevl")}</button><button class="arr" data-a="strip" data-d="1" aria-label="Scroll right">${ico("chevr")}</button></div><div class="strip hstrip">${L.map(contCard).join("")}</div></section>`;
  body += weekHTML(airing);
  if (fresh.length) body += `<section class="hsec"><div class="sh"><h2>Ready to start</h2><span class="mut">on disk, not started</span><span class="fill"></span><button class="link-btn" data-a="go" data-v="library">All shows${ico("arrowr")}</button></div><div class="rdy">${fresh.map((s) =>
    `<button class="rc" data-a="open" data-v="${esc(s.id)}"><span class="pw">${poster(s.title, s.poster)}</span><b>${esc(s.title)}</b><small>${epTotal(s)} episode${epTotal(s) === 1 ? "" : "s"} · disk</small></button>`).join("")}</div></section>`;
  if (!up && !L.length) {
    body = `<div class="emptyx"><div class="eico">${ico("tv")}</div>${d.shows.length
      ? `<b>Nothing watched yet</b><span>Your library is waiting.</span><button class="btn pri" data-a="go" data-v="library">Browse library</button>`
      : d.stream_ready ? `<b>Nothing watched yet</b><span>Search for a show to start watching — or add your anime folder in Settings → Library.</span>`
      : `<b>Your library is empty</b><span>Choose your anime folder in Settings → Library.</span>`}</div>` + body;
  }
  return `<div class="home2"><div class="hello"><h1 class="hd">${dayWord(new Date().getHours())}</h1>${sum ? `<div class="sum">${sum}</div>` : ""}</div>${up ? heroHTML(up) : ""}${body}</div>`;
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

/* One card per franchise, where its first entry sorts; the rest are
   counted on it ("+2"). It shows the entry last active in, else the
   series itself before its movies and extras, oldest first (Attack on
   Titan, not one of its films). */
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
function libModel() {
  const d = S.data, sp = d.spine;
  if (!sp) return { cold: true, tabs: [], entries: [], untracked: [], unmatched: unmatchedShows() };
  const counts = {};
  for (const e of sp.entries) (counts[e.status] ||= new Set()).add(franchiseKey(e));
  const tabs = LISTS.filter(([k]) => counts[k]).map(([k, l]) => ({ k, l, n: counts[k].size }));
  if (sp.entries.length) tabs.unshift({ k: "ALL", l: "All", n: new Set(sp.entries.map(franchiseKey)).size });
  const untracked = sp.untracked.map(showById).filter((s) => s && (s.meta?.anilist_id || s.kept_local));
  const unmatched = unmatchedShows();
  if (untracked.length) tabs.push({ k: "UNTRACKED", l: "On disk only", n: untracked.length, side: true });
  if (unmatched.length) tabs.push({ k: "MATCH", l: "Needs a look", n: unmatched.length, attn: true, side: true });
  return { tabs, entries: sp.entries, untracked, unmatched };
}
function fixTab(m) {
  if (m.cold || !m.tabs.length) return;
  if (!m.tabs.some((t) => t.k === S.lib.tab)) S.lib.tab = m.tabs[0].k;
}
function libFiltered(m) {
  const f = S.lib;
  return m.entries.filter((e) => (f.tab === "ALL" || e.status === f.tab)
    && (srcLocked() || f.src === "all" || e.capability === f.src)
    && (f.fmt === "all" || e.format === f.fmt)
    && (f.air === "all" || e.airing === f.air)
    && inRange(f.year, e.year)
    && (!Array.isArray(f.score) || (e.score > 0 && inRange(f.score, e.score)))
    && hasGenres(e, f.genres)).sort(SORT[f.sort] || SORT.updated);
}
/* Genre filter (Decisions 13/28): an entry must carry every picked genre. */
const hasGenres = (e, picked) => !picked?.length || picked.every((g) => (e.genres || []).includes(g));
/* The tray: genres in the open tab, most cards first (one per franchise). */
function genreCounts(m) {
  const c = new Map();
  for (const e of m.entries) if (S.lib.tab === "ALL" || e.status === S.lib.tab)
    for (const g of e.genres || []) (c.get(g) || c.set(g, new Set()).get(g)).add(franchiseKey(e));
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
function matchUI(s) {
  const m = mst(s.id), c = (m.cands || []).slice(0, 5), n = epTotal(s), best = c[m.pick] || c[0];
  const files = s.seasons.flatMap((x) => x.episodes), mins = files.find((x) => x.duration)?.duration;
  const facts = [n > 1 ? `${n} episodes` : n ? "1 file" : "", mins ? `${Math.round(mins / 60)} min${n > 1 ? " each" : ""}` : "", s.seasons.length > 1 ? `${s.seasons.length} seasons` : ""].filter(Boolean);
  const meta = (x) => [x.seasonYear || x.startDate?.year, FMT[x.format] || x.format, x.episodes ? x.episodes + " ep" : "", x.duration ? x.duration + " min" : ""].filter(Boolean).join(" · ");
  return `<div class="match mc" data-show="${esc(s.id)}"><div class="mcl">
      <span class="cap2">${ico("folder")} folder on disk</span>
      <h3>${esc(s.title)}</h3>${s.path && s.path !== s.title ? `<div class="raw">${esc(s.path)}</div>` : ""}
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
  return `<div class="fl rng${Array.isArray(S.lib[k]) ? " set" : ""}" data-k="${k}" style="--a:${pct(a)}%;--b:${pct(b)}%"><span>${label} <b class="rv">${a <= lo && b >= hi ? "any" : `${fmt(a)} – ${fmt(b)}`}</b></span>
    <div class="rt">${inp(0, a, "from")}${inp(1, b, "to")}</div></div>`;
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
  box.querySelector(".rv").textContent = all ? "any" : `${a} – ${b}`;
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
  const sum = [`<span><b>${m.entries.length ? new Set(m.entries.map(franchiseKey)).size : d.shows.length}</b> shows</span>`, files ? `<span><b>${files.toLocaleString()}</b> files on disk</span>` : "",
    m.unmatched.length ? `<span><b>${m.unmatched.length}</b> folder${m.unmatched.length === 1 ? "" : "s"} need a look</span>` : ""].filter(Boolean).join('<i class="sep"></i>');
  const tab = (t) => `<button class="${S.lib.tab === t.k ? "on" : ""}${t.attn ? " attn" : ""}" data-a="tab" data-v="${t.k}">${t.l}${t.attn ? '<i class="att"></i>' : ""}<span class="n">${t.n}</span></button>`;
  const tabs = m.tabs.length ? `<div class="tabs" data-glide="libtabs">${m.tabs.filter((t) => !t.side).map(tab).join("")}${m.tabs.some((t) => t.side) ? '<i class="vs"></i>' : ""}${m.tabs.filter((t) => t.side).map(tab).join("")}</div>` : "";
  let main;
  if (isMatch) {
    autoSuggest(m.unmatched);
    main = `<p class="intro">On disk, but not matched to AniList yet. Pick the right show and it joins your library — or keep it local.</p><div class="mgrid">${m.unmatched.map(matchUI).join("")}</div>`;
  } else {
    const filters = (m.cold || isUn) ? "" : `<div class="filters">
      ${fsel("src", "source", [["all", "any"], ["installed", "disk"], ["partial", "partial"], ["online", "online"], ["none", capLabel("none")]], srcLocked())}
      ${fsel("fmt", "format", [["all", "any"], ["TV", "TV"], ["MOVIE", "Movie"], ["OVA", "OVA"], ["ONA", "ONA"], ["SPECIAL", "Special"]])}
      ${fsel("air", "airing", [["all", "any"], ...Object.entries(AIR)])}
      ${frng("year", "year", ...yearBounds(), 1, String)}
      ${frng("score", "score", 0, 10, 0.5, (x) => String(x))}
      ${filtersActive() ? `<button class="btn clr" data-a="fclear">${ico("close")}Clear</button>` : ""}
      <span class="fill"></span>${fsel("sort", "sort", [["updated", "recently updated"], ["score", "score"], ["year", "year"], ["title", "title"]])}
      <button class="btn ic" data-a="rescan" aria-label="Rescan" title="Rescan library & fetch missing metadata">${ico("refresh")}</button></div>`;
    main = `${filters}${(m.cold || isUn) ? "" : genreTray(m)}<div id="wall"></div>`;
  }
  return `<div class="lib2">${notes.join("")}<div class="hello"><h1 class="hd">Library</h1><div class="sum">${sum}</div></div>${tabs}${main}</div>`;
}
function genreTray(m) {
  const counts = genreCounts(m), have = new Set(counts.map(([g]) => g));
  const gone = S.lib.genres.filter((g) => !have.has(g)).map((g) => [g, 0]); // picked, but not in this tab: still unpickable
  const all = [...counts, ...gone];
  if (!all.length) return "";
  return `<div class="gtray" role="group" aria-label="Genres">${all.map(([g, n]) => {
    const on = S.lib.genres.includes(g);
    return `<button class="gchip${on ? " on" : ""}" aria-pressed="${on}" data-a="genre" data-v="${esc(g)}">${esc(g)}<span class="n">${n}</span></button>`;
  }).join("")}</div>`;
}
function paintWall(fade) {
  const w = $("#wall");
  if (!w) return;
  const m = libModel();
  let html;
  if (m.cold) html = S.data.shows.map(showCard).join("");
  else if (S.lib.tab === "UNTRACKED") html = m.untracked.map(showCard).join("");
  else html = oneCardPerFranchise(libFiltered(m)).map(entryCard).join("");
  const empty = `<div class="emptyx" style="grid-column:1/-1"><div class="eico">${ico("search")}</div><b>${m.entries.length || S.data.shows.length ? "Nothing matches these filters." : "No shows found."}</b>${m.entries.length || S.data.shows.length ? "" : "<span>Check your library folder, then rescan.</span>"}</div>`;
  const fx = fade && !reduceMotion();
  w.className = "wall" + (fx ? " fade" : "");
  if (fx || !w.firstChild) w.innerHTML = html || empty; else morphHTML(w, html || empty);
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
  const part = !ep.watched && pct > 1;
  const files = ep.files || [], loading = epLoading(ep.id);
  const picking = S.sel?.show === s.id, picked = picking && S.sel.ids.has(ep.id);
  const cls = ["ep", picking ? "pick" : "", picked ? "sel" : "", ep.watched ? "watched" : "", ep.id === tgId && resumable(ep) ? "resume" : "", art ? "" : "noart", loading ? "loading" : ""].filter(Boolean).join(" ");
  const mins = minsOf(ep.duration), lang = fileLangs(ep);
  const meta = loading ? "loading…" : part ? `${fmtLeft(ep.duration, ep.position)} left · disk · ${lang}` : `${mins ? mins + " min · " : ""}disk · ${lang}${files.length > 1 ? ` · ${files.length} versions` : ""}`;
  return tileHTML({ cls, a: "playep", v: ep.id, i, art, pos, num: "E" + String(ep.number).padStart(2, "0"), pct: part ? pct : 0, loading, watched: ep.watched,
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
  for (const x of slots) if (resumable(x) && (!resume || (x.last_played || "") > (resume.last_played || ""))) resume = x;
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
  clearTimeout(libRest.t);
  const art = { blur: c.dataset.poster || null, sharp: null, color: c.dataset.color || colorOfId(+c.dataset.v) || null };
  accentFrom(art.blur, art.color);
  libRest = { el: c, t: setTimeout(() => { if (S.view === "library") { S.libHover = art; swapLayer("cv-blur", lookGet("art") === "off" ? null : art.blur); } }, 450) };
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
  const pct = x.duration ? Math.min(100, (100 * x.position) / x.duration) : 0, part = !x.watched && pct > 1;
  const ok = playableSlot(e, x), loading = slotIds(x).some(epLoading), job = x.online && !x.local && dlJob(x.online.id);
  const picking = S.sel?.show === frSelKey(), picked = picking && S.sel.ids.has(x.id);
  const cls = ["ep", picking ? "pick" : "", picked ? "sel" : "", x.watched ? "watched" : "", x.id === tgId ? "resume" : "", art ? "" : "noart", loading ? "loading" : "", ok ? "" : "off"].filter(Boolean).join(" ");
  const files = x.local?.files || [], mins = minsOf(x.duration, e.duration);
  const src = x.local ? `disk · ${fileLangs({ files })}` : ok ? `online · ${e.online.mode || S.settings?.stream?.mode || "sub"}` : "not available";
  const meta = loading ? "loading…" : job ? (job.status === "running" ? `downloading · ${Math.floor(job.percent)}%` : "waiting to download")
    : part ? `${fmtLeft(x.duration, x.position)} left · ${src}` : `${mins ? mins + " min · " : ""}${src}`;
  const movie = e.format === "MOVIE" && e.slots.length <= 1;
  return tileHTML({ cls, a: ok ? "playep" : "unavail", v: x.id, i, art, pos, num: movie ? "Movie" : "E" + String(x.number).padStart(2, "0"),
    pct: part ? pct : 0, dlPct: job?.status === "running" ? job.percent : null, loading, watched: x.watched, picking, picked,
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
  const srcLine = [local.length ? `${span(local)} on disk` : "", online.length && ready && e.online.available ? `${span(online)} online` : ""].filter(Boolean).join(" · ");
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
  m.innerHTML = `<div class="cap">${esc(e.title || p.title)}</div>
    ${e.slots.some((x) => playableSlot(e, x)) ? `<button class="mi" data-a="selmode">${ico("checkc")}${picking ? "Stop selecting" : "Select episodes"}</button>` : ""}
    ${folders.length ? `<button class="mi" data-a="folder" data-v="${esc(folders[0].show_id)}">${ico("folder")}Open the folder</button>` : ""}
    ${folders.map((l) => `<button class="mi" data-a="pinopen" data-s="${esc(l.show_id)}" data-k="${esc(l.season)}" data-l="${esc(l.folder + " · " + l.season_label)}">${ico("tv")}Wrong season? <small>${esc(l.season_label)}</small></button>`).join("")}
    ${ready && e.online.linked ? `<button class="mi" data-a="srcfix">${ico("search")}Wrong online episodes?</button>` : ""}
    <a class="mi" href="${alUrl(e.id)}" data-a="ext" data-v="${alUrl(e.id)}">${ico("ext")}Open on AniList</a>`;
  m.style.animation = "none"; void m.offsetWidth; m.style.animation = "";
  const z = zoomGet() / 100;
  m.style.cssText += `;display:block;left:${Math.min(x0 / z, innerWidth / z - 270)}px;top:${Math.min(y0 / z, innerHeight / z - 280)}px`;
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
function frCm(id, x0, y0) {
  const f = frFind(id);
  if (!f) return false;
  const { e, x } = f, m = $("#cm"), ok = playableSlot(e, x), files = x.local?.files || [];
  const online = x.online && S.data.stream_ready && e.online.available;
  const mode = e.online.mode || S.settings?.stream?.mode || "sub", other = mode === "dub" ? "sub" : "dub";
  m.innerHTML = `<div class="cap">${esc(e.label)} · E${esc(x.number)}</div>
    ${ok ? `<button class="mi" data-a="playep" data-v="${esc(x.id)}">${ico("play")}${resumable(x) ? "Resume" : "Play"}</button>` : ""}
    ${files.length > 1 ? files.map((fl, i) => `<button class="mi" data-a="playep" data-v="${esc(x.local.id)}" data-ver="${i}">${ico("play")}Play ${esc(fl.label)}</button>`).join("") : ""}
    ${x.local && online ? `<button class="mi" data-a="playep" data-v="${esc(x.online.id)}">${ico("play")}Play online</button>` : ""}
    <button class="mi" data-a="mark" data-v="${esc(x.id)}" data-w="${!x.watched}">${ico(x.watched ? "undo" : "check")}${x.watched ? "Mark as unwatched" : "Mark as watched"}</button>
    ${x.position ? `<button class="mi" data-a="reset" data-v="${esc(x.id)}">${ico("undo")}Reset progress</button>` : ""}
    ${online ? `<button class="mi" data-a="playmode" data-v="${esc(x.online.id)}" data-m="${other}">${ico("play")}Play ${other === "dub" ? "dubbed" : "subbed"}</button>` : ""}
    ${online && !x.local && !dlJob(x.online.id) ? `<button class="mi" data-a="dlep" data-v="${esc(x.online.id)}">${ico("download")}Download</button>` : ""}`;
  m.style.animation = "none"; void m.offsetWidth; m.style.animation = "";
  const z = zoomGet() / 100;
  m.style.cssText += `;display:block;left:${Math.min(x0 / z, innerWidth / z - 250)}px;top:${Math.min(y0 / z, innerHeight / z - 240)}px`;
  return true;
}

function backLabel() { return S.showBack === "library" ? "Library" : S.showBack === "history" ? "History" : S.showBack === "stats" ? "Stats" : "Home"; }

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
  const facts = [s.path || s.title, `${epTotal(s)} episodes`, matched ? "AniList unreachable — showing the folder" : s.kept_local ? "kept local" : "not matched yet"];
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
  if (!f.status && !pending) return `<div class="st2 off"><span class="k">not on your list yet</span><button class="btn" data-a="eadd">${ico("plus")}Add to Planning</button></div>`;
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
function historyRows() {
  const range = HIST_RANGES.find((r) => r[0] === S.histRange) || HIST_RANGES[2];
  const cutoff = range[2] ? Date.now() - range[2] : null;
  return (S.data.history || []).map((ep) => ({ ep, ctx: findEpContext(ep.id) })).filter((x) => x.ctx)
    .filter((x) => cutoff === null || (x.ep.last_played && parseStamp(x.ep.last_played).getTime() >= cutoff))
    .sort((a, b) => (b.ep.last_played || "").localeCompare(a.ep.last_played || ""));
}
function dayLabel(d) {
  const t0 = new Date(); t0.setHours(0, 0, 0, 0);
  const d0 = new Date(d); d0.setHours(0, 0, 0, 0);
  const days = Math.round((t0 - d0) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: d.getFullYear() === t0.getFullYear() ? undefined : "numeric" });
}
function Hist() {
  const rows = historyRows(), shown = rows.slice(0, S.histMax);
  let last = null, out = "";
  for (const [idx, { ep, ctx }] of shown.entries()) {
    const d = parseStamp(ep.last_played), key = dayLabel(d);
    if (key !== last) { out += `<h3 class="day">${esc(key)}</h3>`; last = key; }
    const st = ep.watched ? "Finished" : ep.position ? `Stopped at ${fmtTime(ep.position)}` : "Started";
    const prg = !ep.watched && ep.duration && ep.position ? Math.min(100, (ep.position / ep.duration) * 100) : 0;
    const t = epTitle(ctx.s, ctx.sea, ep);
    out += `<button class="hr" style="--i:${Math.min(idx, 12)}" data-a="open" data-v="${esc(ctx.s.id)}">${poster(ctx.s.title, ctx.s.poster, { c: "t" })}<span><b>${esc(ctx.s.title)}</b><br><span class="sub">${esc(epCode(ctx.sea, ep))}${t ? " · " + esc(t) : ""}</span></span><span class="hs sub"><span>${ep.watched ? ico("check") : ""}${esc(st)}</span>${prg ? `<span class="hp"><i style="width:${prg}%"></i></span>` : ""}</span><span class="src ${streamEp(ep) ? "online" : "installed"}" style="justify-self:start">${streamEp(ep) ? "online" : "installed"}</span><span class="sub" style="text-align:right">${esc(d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span></button>`;
  }
  return `<div class="wrap page"><div class="sec first"><h2>History</h2><label class="fl" style="display:inline-grid">Show<select data-a="hrange">${HIST_RANGES.map(([k, l]) => `<option value="${k}" ${k === S.histRange ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    ${out || `<p class="empty" style="padding:40px 0">Nothing watched ${S.histRange === "all" ? "yet" : "in this range"}.</p>`}
    ${rows.length > shown.length ? `<div class="eps-more"><button class="btn" data-a="histmore">Show more (${rows.length - shown.length} left)</button></div>` : ""}</div>`;
}

/* ---------- stats ---------- */
function statsEntries() {
  if (aniMode() && S.alData?.entries) return S.alData.entries;
  return S.data.spine?.entries || [];
}
function barCols(items, fmt) {
  const mx = Math.max(...items.map((x) => x[1]), 1);
  return `<div class="cols-bar${items.length > 14 ? " many" : ""}">${items.map(([l, v], i) =>
    `<div tabindex="0" data-v="${esc(fmt(v))}" title="${esc(l)}: ${esc(fmt(v))}" style="--p:${(v / mx) * 100}"><i style="height:${(v / mx) * 100}%;animation-delay:${i * 40}ms"></i><span>${items.length > 20 && i % 3 ? "" : esc(l)}</span></div>`).join("")}</div>`;
}
const DONUT_COLORS = ["var(--accent)", "var(--online)", "var(--ok)", "var(--warn)", "var(--mute)", "var(--line)"];
function donut(segs) {
  const total = segs.reduce((a, s) => a + s.v, 0) || 1;
  let off = 0, arcs = "";
  let ki = 0;
  for (const s of segs) {
    const len = (s.v / total) * 100;
    arcs += `<circle cx="60" cy="60" r="46" fill="none" stroke="${s.c}" stroke-width="16" pathLength="100" stroke-dasharray="${Math.max(len - 1, 0.4)} 100" stroke-dashoffset="${-off}" data-k="${ki}" style="--k:${ki++}" transform="rotate(-90 60 60)"><title>${esc(s.l)} · ${s.v}</title></circle>`;
    off += len;
  }
  return `<svg class="donut" viewBox="0 0 120 120" role="img" aria-label="Distribution"><circle cx="60" cy="60" r="46" fill="none" stroke="var(--s2)" stroke-width="16"/>${arcs}<text x="60" y="66" text-anchor="middle" fill="var(--ink)" font-size="20" font-weight="700">${segs.reduce((a, s) => a + s.v, 0)}</text></svg>`;
}
function Stats() {
  const d = S.data, E = statsEntries(), u = aniMode() ? S.alData?.user : null;
  const minutes = u ? u.minutes_watched : d.stats.minutes, eps = u ? u.episodes_watched : d.stats.episodes_watched;
  const scored = E.filter((e) => e.score > 0), done = E.filter((e) => e.status === "COMPLETED").length;
  const mean = u && u.mean_score ? (u.mean_score / 10).toFixed(1) : scored.length ? (scored.reduce((a, e) => a + e.score, 0) / scored.length).toFixed(1) : "—";
  /* hours per month: from the real watch history (episodes finished on this device) */
  const MN = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" "), month = {};
  for (const ep of d.history || []) if (ep.watched && ep.last_played && ep.duration) {
    const t = parseStamp(ep.last_played), k = t.getFullYear() * 12 + t.getMonth();
    month[k] = (month[k] || 0) + ep.duration / 3600;
  }
  const now = new Date(), nk = now.getFullYear() * 12 + now.getMonth();
  const months = Array.from({ length: 12 }, (_, i) => { const k = nk - 11 + i; return [`${MN[((k % 12) + 12) % 12]} ${String(Math.floor(k / 12)).slice(2)}`, Math.round((month[k] || 0) * 10) / 10]; });
  const byList = LISTS.map(([k, l]) => [l, E.filter((e) => e.status === k).length]).filter((x) => x[1]);
  const fmts = {};
  for (const e of E) { const k = FMT[e.format] || e.format || "Unknown"; fmts[k] = (fmts[k] || 0) + 1; }
  const fseg = Object.entries(fmts).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([l, v], i) => ({ l, v, c: DONUT_COLORS[i] }));
  const years = {};
  for (const e of E) if (e.year) years[e.year] = (years[e.year] || 0) + 1;
  const ys = Object.keys(years).map(Number), yItems = ys.length ? Array.from({ length: Math.max(...ys) - Math.min(...ys) + 1 }, (_, i) => [String(Math.min(...ys) + i), years[Math.min(...ys) + i] || 0]) : [];
  const dist = Array.from({ length: 10 }, (_, i) => [String(i + 1), scored.filter((e) => Math.round(e.score) === i + 1).length]);
  const lmax = Math.max(...byList.map((x) => x[1]), 1), ftot = fseg.reduce((a, s) => a + s.v, 0) || 1;
  const hb = (k, c, i, mx) => `<div class="hb" title="${esc(k)}" tabindex="0"><span>${esc(k)}</span><div><i style="width:${(c / mx) * 100}%;animation-delay:${i * 60}ms"></i></div><span class="sub">${c}</span></div>`;
  const note = aniMode() && !u ? '<p class="stat-note">Loading your full AniList totals…</p>' : "";
  return `<div class="wrap page"><div class="sec first"><h2>Stats</h2><span class="sub">${aniMode() ? `AniList${u ? " · " + esc(u.name) : ""}` : "Based on progress stored on this device"}</span></div>
  <div class="tiles"><div class="tile">${ico("clock")}<b data-n="${Math.round(minutes / 60)}" data-s=" h">${Math.round(minutes / 60)} h</b><span>Time watched</span></div><div class="tile">${ico("tv")}<b data-n="${eps}">${eps}</b><span>Episodes</span></div><div class="tile">${ico("checkc")}<b data-n="${done}">${done}</b><span>Shows completed</span></div><div class="tile">${ico("star")}<b ${mean !== "—" ? `data-n="${mean}" data-d="1"` : ""}>${mean}</b><span>Mean score</span></div></div>${note}
  <div class="pgrid"><div class="panel wide2"><h3>Hours watched per month <span class="sub" style="font-weight:400;font-size:13px">on this device</span></h3>${barCols(months, (v) => v + " h")}</div>
  <div class="panel"><h3>Shows by list</h3>${byList.map(([k, c], i) => hb(k, c, i, lmax)).join("") || '<p class="sub">No list data yet.</p>'}</div>
  <div class="panel"><h3>Formats</h3><div class="fmt-split">${donut(fseg)}<div class="legend">${fseg.map((s, i) => `<span data-k="${i}"><i style="background:${s.c}"></i>${esc(s.l)} · ${s.v} (${Math.round((s.v / ftot) * 100)}%)</span>`).join("")}</div></div></div>
  <div class="panel"><h3>Release year</h3>${yItems.length ? barCols(yItems, (v) => v + " shows") : '<p class="sub">No year data yet.</p>'}</div>
  <div class="panel"><h3>Scores you give</h3>${scored.length ? barCols(dist, (v) => v + " shows") : '<p class="sub">No scores yet.</p>'}</div></div></div>`;
}
function loadAlData() {
  if (!aniMode() || S.alData || loadAlData.busy) return;
  loadAlData.busy = true;
  api("/api/anilist").then((res) => {
    if (res && res.user) { S.alData = res; if (S.view === "stats") { S.countNext = true; render(true); } }
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
  return `<div class="wrap page"><div class="settings"><div class="sec first"><h2>Settings</h2></div>
  <div class="group"><h3>Library</h3>${srow("Library folder", `${esc(stateTxt[s.scan?.state] || "")} ${s.scan?.state === "unconfigured" ? "Choose the folder that holds your anime." : "Browse to pick another one."}${changed ? " The configured folder differs from the indexed one — connect it to pick your shows back up." : ""}`,
      `<div class="path">${libPath("settings", s)}${dirBtn("settings")}<button class="btn" data-a="rescan">Rescan</button>${changed ? '<button class="btn pri" data-a="adopt">Connect folder</button>' : ""}</div>`)}${DirPick("settings")}</div>
  <div class="group"><h3>Detected tools</h3>${toolRow("mpv", s.tools?.mpv, "Required for playback — install mpv.")}${toolRow("ani-cli", s.tools?.["ani-cli"] && s.tools["ani-cli"] + (s.stream?.version ? " · " + s.stream.version : ""), "Optional — lets you watch and download episodes you don't have.")}${toolRow("upscayl", s.tools?.upscayl, "Optional — upscales banners when the config enables it.")}</div>
  ${streamGroup(s)}
  <div class="group"><h3>AniList</h3>${srow("Account", conn ? `Your AniList lists are the sections of your library.${ani.expires_at ? " Token expires " + new Date(ani.expires_at * 1000).toLocaleDateString() + "." : ""}` : ani.expired ? "The saved token has expired — reconnect." : "Not connected. Everything runs on local watch state.",
      conn ? `<span><i class="avatar">${esc((ani.user || "?")[0].toUpperCase())}</i>${esc(ani.user || "")}</span><button class="btn" data-a="ani">Manage</button>` : '<button class="btn pri" data-a="ani">Connect AniList</button>')}
  <details class="adv${off ? " dis" : ""}" data-k="anilist" ${S.advOpen.anilist ? "open" : ""}><summary>${ico("chevr")}Advanced</summary>${off ? '<p class="sub">Connect AniList to use these.</p>' : ""}
    ${srow("Track episodes", "After playback marks an episode watched, publish the highest watched episode to AniList (single-season releases).", tgl("auto_progress_sync", "Track episodes", s.auto_progress_sync))}
    ${srow("Watched marks from AniList", "Episodes up to your AniList progress show as watched, the rest as unwatched. Off: the marks saved on this device. Switching back loses nothing.", tgl("anilist_watched", "Watched marks from AniList", s.anilist_watched))}
    ${srow("Add shows automatically", "A first watch of a matched show that isn't on your lists adds it to Watching. Off by default.", tgl("auto_add_watch", "Add shows automatically", s.auto_add_watch))}
    ${srow(`Sync ratings<span class="soon">not built yet</span>`, "Keep scores in step between AniList and this device.", tgl("ratings", "Sync ratings", false, true))}</details></div>
  <div class="group"><h3>Appearance</h3>${srow("Accent colour", "Follow the show on screen, or keep one colour everywhere.", `<span class="looks" role="radiogroup" aria-label="Accent colour"><button class="follow${accentGet() === "follow" ? " on" : ""}" role="radio" aria-checked="${accentGet() === "follow"}" data-a="look" data-k="accent" data-v="follow">Follow the show</button>${ACCENTS.map((c) => `<button class="swatch${accentGet() === c ? " on" : ""}" role="radio" aria-checked="${accentGet() === c}" aria-label="Accent ${c}" style="background:${c}" data-a="look" data-k="accent" data-v="${c}"></button>`).join("")}</span>`)}${srow("Background art", "How much of the show's art fills the screen. Off: a calm dark background.", lookSeg("art", [["full", "Full"], ["soft", "Soft"], ["off", "Off"]]))}${srow("Film grain", "A light texture over the background.", lookSeg("grain", [["on", "On"], ["off", "Off"]]))}${srow("Motion", "Calm keeps the small animations but drops drifting and pulsing. Off turns them all off.", lookSeg("motion", [["full", "Full"], ["calm", "Calm"], ["off", "Off"]]))}${srow("Zoom", "Scales the whole interface on this device.", `<span class="zrow"><button class="btn" data-a="zoomout" aria-label="Zoom out">−</button><span class="zval" id="zoomval">${zoomGet()}%</span><button class="btn" data-a="zoomin" aria-label="Zoom in">+</button></span>`)}</div>
  <div class="group"><h3>Data</h3>${srow("Config file", `<code>${esc(s.config_path)}</code>`, "")}${srow("Data folder", `<code>${esc(s.data_dir)}</code>`, "")}${srow("Server", `Port ${esc(String(s.port))} · window mode “${esc(s.window)}”.`, "")}</div>
  <div class="group"><h3>Setup</h3>${srow("First-run tour", "Walk through the library folder and AniList again.", '<button class="btn" data-a="tour">Replay tour</button>')}</div></div></div>`;
}
/* A segmented control for one Appearance preference. */
function lookSeg(k, opts) {
  const cur = k === "grain" ? (grainGet() ? "on" : "off") : lookGet(k);
  return `<span class="seg" role="group" aria-label="${k}">${opts.map(([v, l]) => `<button class="${cur === v ? "on" : ""}" aria-pressed="${cur === v}" data-a="look" data-k="${k}" data-v="${v}">${l}</button>`).join("")}</span>`;
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
function dirCrumbs(path) {
  const parts = path.split("/").filter(Boolean), last = parts.length - 1;
  const seg = (label, to, cur) => cur ? `<b aria-current="location">${esc(label)}</b>` : `<button data-a="dirgo" data-v="${esc(to)}">${esc(label)}</button>`;
  return seg("/", "/", last < 0) + parts.map((x, i) => `${i ? "<i>/</i>" : ""}${seg(x, "/" + parts.slice(0, i + 1).join("/"), i === last)}`).join("");
}
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
/* Settings fold-outs stay open across re-renders. */
document.addEventListener("toggle", (e) => { if (e.target.matches?.("details.adv")) S.advOpen[e.target.dataset.k] = e.target.open; }, true);
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
  if (f) f.value = S.settings?.library_root || "";
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
async function loadSettings() {
  try { S.settings = await api("/api/settings"); } catch { S.settings = null; }
  return S.settings;
}

/* ---------- nav, search, downloads ---------- */
const VIEWS = ["home", "library", "history", "stats", "settings"];
/* One list for library and AniList hits: ranked by how well the title
   matches, library first on ties; an AniList hit that is a library show
   shows once, as the library show. AniList results follow the typing
   (debounced) and the last ones stay up while the next search runs. */
const srRank = (t, ql) => { const l = String(t || "").toLowerCase(), i = l.indexOf(ql); return i === 0 ? 0 : i > 0 && /[^a-z0-9]/.test(l[i - 1]) ? 1 : i > 0 ? 2 : 3; };
function srHTML() {
  const q = S.q.trim(), ql = q.toLowerCase();
  if (!q) return "";
  const local = (S.data?.shows || []).filter((s) => `${s.collection || ""} ${s.title}`.toLowerCase().includes(ql))
    .map((s) => ({ r: Math.min(srRank(s.title, ql), srRank(s.collection, ql)), local: true, s }));
  const have = new Set(local.map((x) => x.s.meta?.anilist_id).filter(Boolean));
  const remote = (alSearchable() && S.alres || []).map((x, i) => ({ r: srRank(candLabel(x), ql), x, i })).filter((y) => !have.has(y.x.id));
  const rows = [...local, ...remote].sort((a, b) => a.r - b.r || (b.local ? 1 : 0) - (a.local ? 1 : 0)).slice(0, 8);
  let h = rows.map((y, i) => {
    if (y.local) return `<button style="--i:${i}" class="mi" data-a="open" data-v="${esc(y.s.id)}">${poster(y.s.title, y.s.poster, { c: "t" })}<span><b>${hl(y.s.title, q)}</b><small>${esc(y.s.collection || `${epTotal(y.s)} episodes`)}</small></span><em class="src installed">library</em></button>`;
    const x = y.x, e = aniMode() ? S.data.spine.entries.find((z) => z.id === x.id) : null;
    const sub = e ? "On your list · " + statusLabel(e.status) : [x.seasonYear || x.startDate?.year, FMT[x.format] || x.format].filter(Boolean).join(" · ");
    return `<button style="--i:${i}" class="mi" data-a="catpick" data-v="${y.i}">${poster(candLabel(x), x.coverImage?.extraLarge, { c: "t" })}<span><b>${hl(candLabel(x), q)}</b><small>${esc(sub)}</small></span><em class="src online">AniList</em></button>`;
  }).join("");
  const busy = alSearchable() && (S.alBusy || (S.alq !== q && q.length >= 2));
  if (!rows.length && !busy) h += `<p class="cap">No matches${alSearchable() ? " in your library or on AniList" : " in your library"}</p>`;
  if (busy) h += '<div class="cap"><i class="spin"></i> Searching AniList…</div>';
  return `<div class="pop sr">${h}</div>`;
}
/* AniList's catalog search needs no account: offered in AniList mode, and to
   anyone with ani-cli (finding something to stream). */
const alSearchable = () => aniMode() || !!S.data?.stream_ready;
const DL_STATE = { queued: "Waiting", running: "Downloading", done: "Downloaded", failed: "Failed", cancelled: "Cancelled" };
function dlPop() {
  const rows = S.dl.slice().reverse().map((j) => {
    const live = j.status === "running" || j.status === "queued";
    return `<div class="dlj ${j.status}"><div class="dlj-t"><b>${esc(j.show)} · E${esc(j.token)}</b><small>${esc(DL_STATE[j.status] || j.status)}${j.status === "running" ? ` · ${Math.floor(j.percent)}%` : ""}${j.error ? " — " + esc(j.error) : ""}</small>
      ${j.status === "running" ? `<div class="bar"><i style="width:${j.percent}%"></i></div>` : ""}</div>${live ? `<button class="chip ib" aria-label="Cancel download" title="Cancel" data-a="dlcancel" data-v="${j.id}">${ico("close")}</button>` : ""}</div>`;
  }).join("");
  const over = S.dl.some((j) => j.status !== "running" && j.status !== "queued");
  return `<div class="pop dl"><div class="cap">Downloads</div>${rows ? `<div class="dlist">${rows}</div>` : '<p class="dl-empty">Nothing downloading.</p>'}
    ${over ? '<div class="row" style="padding:6px 8px 0"><button class="btn" data-a="dlclear">Clear finished</button></div>' : ""}
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
function vpnGate(go) {
  if (vpnOn() || !S.data?.vpn?.known || S.settings?.vpn_warn === false) return go();
  openModal({ t: "vpn", go });
}
function VpnDlg() {
  return `<h2 id="dt">No VPN detected</h2>
  <p class="sub">Online episodes and downloads are fetched by ani-cli over your normal connection. Turn on your VPN first, or continue anyway.</p>
  <p class="sub" style="margin-top:10px;font-size:13px">You can turn this question off in Settings → Online episodes.</p>
  <div class="actions"><button class="btn" data-a="close">Cancel</button><button class="btn pri" data-a="vpngo">Continue anyway</button></div>`;
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
  if (!v?.known) return `<span class="dim">VPN status unknown</span>`;
  return `<span class="chip vpn ${v.active ? "on" : "off"}">${ico("shield")}${v.active ? `VPN on (${esc(v.iface)})` : "No VPN detected"}</span>`;
}
function NextDlg() {
  const p = S.modal.p;
  return `<h2 id="dt">Next episode is online only</h2>
  <p class="sub">${p.show ? `${esc(p.show)} · ` : ""}${esc(p.label || "The next episode")} isn't on disk. ani-cli streams it over your connection.</p>
  <p id="nx-vpn" style="margin-top:12px">${nxVpn()}</p>
  <div class="actions"><button class="btn" data-a="nxstop">Stop</button><button class="btn pri" data-a="nxgo">Continue</button></div>`;
}
/* The three places + their number keys (Decision 38; History and Stats
   become Journal in step 5); Settings lives behind the icon (,). */
const TABS = [["home", "Home"], ["library", "Library"], ["history", "History"], ["stats", "Stats"]];
const KEYS = [["Search or run a command", "/"], ["Home · Library · History · Stats", "1 2 3 4"], ["Settings", ","], ["Move around", "← → ↑ ↓"], ["Open", "↵"], ["Back", "Esc"], ["Show page: play · edit entry", "↵ · E"], ["Show page: download · mark all", "D · ⇧W"], ["These keys", "?"]];
const keysPop = () => `<div class="pop keyp"><div class="cap">Keys</div><div class="keys">${KEYS.map(([n, k]) => `<span>${n}</span><kbd class="kb">${k}</kbd>`).join("")}</div></div>`;
function paintNav() {
  const cur = S.view === "show" || S.view === "fr" ? "library" : S.view;
  const links = $("#links");
  links.innerHTML = TABS.map(([k, n], i) => `<button class="link ${cur === k ? "on" : ""}" ${cur === k ? 'aria-current="page"' : ""} data-a="go" data-v="${k}">${n}<kbd>${i + 1}</kbd></button>`).join("");
  glide(links);
  $("#setb").classList.toggle("on", cur === "settings");
  $("#q").placeholder = alSearchable() ? "search library or AniList" : "search your library";
  const sr = $("#sr"), srHad = !!sr.firstElementChild;
  sr.innerHTML = S.menu === "sr" ? srHTML() : "";
  if (!srHad && sr.firstElementChild) sr.firstElementChild.classList.add("in"); // entrance only on open, not on every keystroke
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

/* ---------- context menu ---------- */
function openCm(id, x, y) {
  if (S.view === "fr" && frCm(id, x, y)) return;
  const ctx = findEpContext(id);
  if (!ctx) return;
  const { ep, sea } = ctx, files = ep.files || [], m = $("#cm");
  m.innerHTML = `<div class="cap">${esc(epCode(sea, ep))}</div>
    <button class="mi" data-a="playep" data-v="${esc(id)}">${ico("play")}${resumable(ep) ? "Resume" : "Play"}</button>
    ${files.length > 1 ? files.map((f, i) => `<button class="mi" data-a="playep" data-v="${esc(id)}" data-ver="${i}">${ico("play")}Play ${esc(f.label)}</button>`).join("") : ""}
    <button class="mi" data-a="mark" data-v="${esc(id)}" data-w="${!ep.watched}">${ico(ep.watched ? "undo" : "check")}${ep.watched ? "Mark as unwatched" : "Mark as watched"}</button>
    ${ep.position ? `<button class="mi" data-a="reset" data-v="${esc(id)}">${ico("undo")}Reset progress</button>` : ""}`;
  m.style.animation = "none"; void m.offsetWidth; m.style.animation = "";
  const z = zoomGet() / 100;
  m.style.cssText += `;display:block;left:${Math.min(x / z, innerWidth / z - 250)}px;top:${Math.min(y / z, innerHeight / z - 240)}px`;
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
function Tour() {
  const T = S.tour, s = S.settings || {}, a = S.ani || {}, n = S.data.shows.length;
  const ok = s.library_root && s.scan?.state === "ok", eps = S.data.shows.reduce((x, y) => x + epTotal(y), 0);
  const b = [
    `<h2 id="dt">Where's your anime?</h2><p class="sub">Anime Hub plays what is in your library folder, in mpv.</p><div class="path">${libPath("tour", s)}${dirBtn("tour")}</div>${DirPick("tour")}
     <div class="found"><i class="dot ${ok ? "ok" : ""}"></i>${ok ? `Found ${n} show${n === 1 ? "" : "s"} · ${eps} episodes` : s.library_root ? "There is a problem with this folder — try another one." : "Choose the folder that holds your anime."}</div>`,
    `<h2 id="dt">Connect AniList?</h2><p class="sub">Your AniList lists become the sections of your library. You can change this later in Settings.</p><div class="choices">
      <button class="choice ${T.choice === "connect" ? "on" : ""}" data-a="tourpick" data-v="connect"><b>Connect AniList</b><span>Sync progress and scores with your account.</span></button>
      <button class="choice ${T.choice === "skip" ? "on" : ""}" data-a="tourpick" data-v="skip"><b>Skip for now</b><span>Everything runs on what is on this device.</span></button></div>`,
    `<h2 id="dt">You're set</h2><p class="sub">${a.connected ? "AniList is connected." : "Running in local mode — connect below, or any time from Settings."}</p>
     <ul class="summary"><li>Library: ${esc(s.library_root || "not configured")}</li><li>mpv ${s.tools?.mpv ? "found" : "not found — install it for playback"}</li><li>AniList: ${a.connected ? "connected" : "not connected"}</li></ul>
     ${!a.connected ? '<button class="btn pri" data-a="tourconnect" style="margin-top:14px">Connect AniList</button>' : ""}
     <div class="${a.connected ? "" : "off"}">${srow("Track episodes", "Publish your highest watched episode after playback.", tgl("auto_progress_sync", "Track episodes", s.auto_progress_sync))}${srow("Add shows automatically", "Add a first-watched show to your list. Off by default.", tgl("auto_add_watch", "Add shows automatically", s.auto_add_watch))}</div>`,
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
function Done() {
  const c = S.modal.c;
  return `<div class="doneico"><svg viewBox="0 0 68 68" aria-hidden="true"><circle class="ring" cx="34" cy="34" r="28"/><path class="tick2" d="M22 35l8 8 16-17"/></svg></div>
  <h2 id="dt">You finished ${esc(c.title || "a show")}</h2><p class="sub">That was the last episode. ${S.ani?.connected ? "This updates your AniList entry." : "Saved on this device only."}</p>
  <dl class="form big"><dt>Status</dt><dd><select id="dn-status" aria-label="Status">${LISTS.map(([v, l]) => `<option value="${v}" ${v === "COMPLETED" ? "selected" : ""}>${l}</option>`).join("")}</select></dd>
  <dt>Score</dt><dd><input id="dn-score" type="number" min="0" max="10" step="1" placeholder="–" aria-label="Score" style="width:90px"> <span class="dim" id="dn-max">/ 10</span></dd>
  <dt>Favourite</dt><dd><button class="heart" id="dn-fav" aria-label="Favourite" aria-pressed="false" data-a="dnfav">${ico("heart")}</button></dd></dl>
  <div class="actions"><button class="btn" data-a="dnskip">Skip</button><button class="btn pri" data-a="dnsave">Save</button></div>`;
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
  const body = { ani: AniDlg, tour: Tour, rec: Rec, done: Done, src: SrcDlg, vpn: VpnDlg, pin: PinDlg, nxt: NextDlg, entry: EntryDlg }[m.t]();
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
  if (focus) (el.querySelector(".pri") || el.querySelector("input") || el.querySelector(".dialog")).focus();
}
function openModal(m) { S.menu = null; S.opener = document.activeElement; S.modal = m; paintNav(); paintModal(true); }
function closeModal() { S.modal = null; paintModal(); S.opener && S.opener.isConnected && S.opener.focus(); }
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
    sc.max = max; sc.step = (st.score_format || "").includes("DECIMAL") ? 0.1 : 1; $("#dn-max").textContent = "/ " + max;
    if (e.score != null) sc.value = e.score;
    if (LISTS.some(([v]) => v === e.status)) $("#dn-status").value = e.status;
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
  if (!S.data || S.view === "settings") return null;
  if (S.view === "fr") { const e = frEntry(), p = S.fr?.page; return { blur: e?.poster || p?.poster, sharp: e?.banner || p?.banner, color: e?.color || colorOfId(e?.id) }; }
  if (S.view === "show") { const s = showById(S.showId); return s && { blur: s.poster || s.banner, sharp: s.banner, color: showColor(s) }; }
  if (S.view === "home") { const s = nextUpInfo()?.s; return s && { blur: s.banner || s.poster, sharp: null, color: showColor(s) }; } // Home: a full blur of the up-next show
  if (S.view === "library" && S.libHover) return S.libHover;
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
  swapLayer("cv-blur", mode === "off" ? null : art?.blur);
  swapLayer("cv-art", mode === "full" ? art?.sharp : null);
  accentFrom(art?.blur || art?.sharp, art?.color);
}
/* AniList's colour first (works in every browser); else a hue read from the
   art — privacy browsers (Helium, Brave…) block that read, so it's the fallback. */
function accentFrom(url, color) {
  const root = document.documentElement;
  ambWant = url || null;
  const set = (c) => (c ? root.style.setProperty("--amb", c) : root.style.removeProperty("--amb"));
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
function countUp() {
  if (reduceMotion()) return;
  $$("#app .tile b[data-n]").forEach((b, k) => {
    const to = +b.dataset.n, dec = +b.dataset.d || 0, suf = b.dataset.s || "", t0 = performance.now() + k * 90;
    const step = (now) => {
      const p = Math.min(1, Math.max(0, (now - t0) / 1000));
      b.textContent = (to * (1 - Math.pow(1 - p, 4))).toFixed(dec) + suf;
      if (p < 1) requestAnimationFrame(step);
    };
    b.textContent = (0).toFixed(dec) + suf; requestAnimationFrame(step);
  });
}
const VIEW_TITLE = { home: "", library: "Library", history: "History", stats: "Stats", settings: "Settings" };
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
  const html = { home: Home, library: Lib, history: Hist, stats: Stats, settings: Sett, show: Det, fr: Fran }[S.view]();
  const morph = inPlace === true && !fresh && app._view === viewKey();
  /* An in-place update leaves the entrance animation running. */
  if (morph) morphHTML(app, html); else { app.classList.toggle("nav-in", fresh && !reduceMotion()); app.innerHTML = html; }
  app._view = viewKey();
  if (S.view === "fr") frLastSig = frSig();
  if (S.view === "library") { paintWall(!morph && S.fadeNext); S.fadeNext = false; startMetaWatch(); }
  lazyBg();
  $$("#app [data-glide]").forEach(glide);
  strips(); ambient();
  /* The strip opens on the next episode, with the one before it in view — whole tiles, never a cut one. */
  if (fresh && (S.view === "fr" || S.view === "show")) { const st = $("#app .eps.strip"), t = st?.querySelector(".ep.resume"), b = t?.previousElementSibling; if (st && t) st.scrollLeft = b ? b.offsetLeft - st.firstElementChild.offsetLeft : 0; }
  if (fresh || S.countNext) { S.countNext = false; countUp(); }
  const t = S.view === "show" ? showById(S.showId)?.title : S.view === "fr" ? S.fr?.page?.title || frEntry()?.title : VIEW_TITLE[S.view];
  document.title = t ? `${t} · Anime Hub` : "Anime Hub";
  if (S.view === "show") { const id = showById(S.showId).meta?.anilist_id; if (id) loadEntry(id); }
  if (S.view === "fr" && frEntry()) loadEntry(frEntry().id);
  if (S.sel && S.sel.show !== selKey()) S.sel = null;
  paintSel();
  if (S.view === "stats") loadAlData();
  applyFocus();
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
function clearSearch() { S.q = ""; S.alres = null; S.alq = null; S.alBusy = false; S.menu = null; $("#q").value = ""; }
function nav(view, fn) {
  dip(() => {
    const prev = S.view;
    S.navIn = true; S.view = view; clearSearch(); fIdx = -1;
    if (view === "library") S.fadeNext = true;
    if (fn) fn(prev);
    render(); scrollTo(0, 0); pushNav();
  });
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
function metaTick() {
  api("/api/now").then((now) => {
    const m = now && now.meta;
    if (!m) { metaWatch = null; reload(); return; } // final settle: posters flip in
    Object.assign(S.data, { meta_running: true, meta_pending: m.pending, meta_current: m.current, meta_phase: m.phase });
    if (S.view === "library") { const n = $(".metanote span"); if (n) n.textContent = metaText(S.data); }
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
    }
    if (now.notice && now.notice.ts !== lastNotice) { lastNotice = now.notice.ts; toast(now.notice.msg); }
    if (!S.playBusy) {
      const nl = now.playing && now.loading ? now.episode_id : null;
      if (nl !== S.loadingEp) { S.loadingEp = nl; if (was === now.playing) pagePatch(); }
    }
    if (was !== now.playing) await reload();
    else if (now.playing) {
      const wasLoading = !!S.data.now?.loading;
      S.data.now = { episode_id: now.episode_id, show: now.show, label: now.label, percent: now.percent, loading: !!now.loading, next: now.next };
      if (wasLoading !== !!now.loading) pagePatch();
      if (prevEp && prevEp !== now.episode_id) await reload(); // autoplay moved on: refresh ticks / next-up
    }
    if (S.outbox > 0 && outbox === 0) toast("Waiting changes synced to AniList");
    if (outbox !== S.outbox) { S.outbox = outbox; if (S.menu === "ob") outbox ? obLoad() : (S.menu = null); }
    const dls = now.downloads || [], done = (l) => l.filter((j) => j.status === "done").length;
    const finished = done(dls) > done(S.dl);
    const dlChanged = JSON.stringify(dls) !== JSON.stringify(S.dl);
    S.dl = dls;
    if (finished) await reload(); // a finished download is a new file on disk
    else if (dlChanged) pagePatch(); // progress on the tiles
    const sig = JSON.stringify([S.data?.now, S.outbox, S.dl, S.data?.vpn]);
    if (sig !== navSig) { navSig = sig; paintNav(); }
    if (now.completed) maybeCompletion(now.completed);
    if (now.next_prompt) maybeNextPrompt(now.next_prompt);
    else if (S.modal?.t === "nxt") closeModal(); // answered elsewhere
  } catch {}
  pollSoon(S.dl.some((j) => j.status === "running") ? 2000 : 4000);
}
/* The open page's tiles follow loading / download / watch state. */
function pagePatch() {
  if (S.view === "show" && showById(S.showId)) patchShow();
  else if (S.view === "fr") frRepaint();
}
/* Online episodes take a few seconds to start: the tile (and the hero
   button) show it loading until mpv reports playback. */
function markLoading(id) {
  S.loadingEp = id;
  pagePatch();
}
async function play(id, version = 0, mode, asked) {
  const online = id.startsWith("stream:");
  if (online && !asked) return vpnGate(() => play(id, version, mode, true));
  S.playBusy = true;
  if (online) markLoading(id);
  const body = { id, version };
  if (mode) body.mode = mode;
  const res = await api("/api/play", body).catch(() => ({ error: "Could not reach the hub." }));
  S.playBusy = false;
  if (res.error) { markLoading(null); toast(res.error); }
  else if (!online) toast("Launching mpv…");
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
  strip: (v, t) => { const st = $("#app .strip"); st && st.scrollBy({ left: +t.dataset.d * st.clientWidth * 0.8, behavior: reduceMotion() ? "auto" : "smooth" }); },
  fclear: () => { for (const k of ["fmt", "air", "year", "score"]) S.lib[k] = "all"; S.lib.genres = []; if (!srcLocked()) S.lib.src = "all"; saveLib(); S.fadeNext = true; render(); },
  genre: (v, t) => {
    const on = !S.lib.genres.includes(v);
    S.lib.genres = on ? [...S.lib.genres, v] : S.lib.genres.filter((g) => g !== v);
    t.classList.toggle("on", on); t.setAttribute("aria-pressed", on);
    saveLib(); paintWall(); syncFilters();
  },
  go: (v) => nav(v), open: (v) => openShow(v), back: () => nav(VIEWS.includes(S.showBack) ? S.showBack : "home"),
  entry: (v) => {
    const e = S.data.spine?.entries.find((x) => String(x.id) === v);
    if (!e) return;
    /* AniList mode: the card is an entry — its franchise page, at it. */
    if (aniMode()) return openFranchise(e.id);
    const sid = e.local_show_id || (showById(e.stream_show_id) ? e.stream_show_id : null);
    if (sid) openShow(sid); else if (e.media_id) openFranchise(e.media_id);
  },
  tab: (v) => { S.lib.tab = v; saveLib(); S.fadeNext = true; render(); },
  filt: (v, t) => { S.lib[t.dataset.k] = v; saveLib(); paintWall(); syncFilters(); },
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
  nxgo: () => { const id = S.modal?.p?.id; closeModal(); if (id) play(id, 0, undefined, true); },
  nxstop: () => { closeModal(); api("/api/next-prompt/dismiss", {}).catch(() => {}); },
  vpngo: () => { const go = S.modal?.go; closeModal(); go?.(); },
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
    const ctx = t.closest(".dialog") ? "tour" : "settings", typed = ($("#libpath-" + ctx)?.value || "").trim(), field = t.tagName === "INPUT";
    if (S.dir?.ctx === ctx) { if (field) dirGo(typed); else dirClose(); return; } // Browse again = Cancel; Enter in the field walks there
    S.dir = { ctx, dirs: null };
    paintDir(ctx);
    dirGo(typed || S.settings?.library_root || "");
  },
  dirgo: (v) => dirGo(v),
  dirclose: () => dirClose(),
  diruse: () => {
    const p = S.dir, cur = S.settings?.library_root;
    if (!p?.path) return;
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
  $("#cm").style.display = "none";
  if (S.menu && !e.target.closest(".rel,.search")) { S.menu = null; paintNav(); }
  if (S.cal && !e.target.closest(".datef")) calClose();
  const t = e.target.closest("[data-a]");
  if (t && !t.disabled && !isField(t) && A[t.dataset.a]) A[t.dataset.a](t.dataset.v, t, e);
});
document.addEventListener("change", (e) => {
  const t = e.target.closest("[data-a]");
  if (t && isField(t) && A[t.dataset.a]) A[t.dataset.a](t.value, t, e);
});
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
  d.dataset.motion = lookGet("motion"); d.dataset.art = lookGet("art"); d.dataset.grain = grainGet() ? "on" : "off";
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
document.// Back on the window (e.g. after toggling the VPN elsewhere): refresh now.
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
const q = $("#q");
let alTimer = 0;
q.addEventListener("input", () => {
  S.q = q.value; S.menu = S.q.trim() ? "sr" : null;
  if (!S.q.trim()) { S.alres = null; S.alq = null; }
  paintNav();
  clearTimeout(alTimer);
  if (alSearchable() && S.q.trim().length >= 2) alTimer = setTimeout(() => { if (S.q.trim() !== S.alq) A.alsearch(); }, 350);
});
q.addEventListener("focus", () => { if (S.q.trim()) { S.menu = "sr"; paintNav(); } });
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const items = $$("#sr .mi");
    if (!items.length) return;
    e.preventDefault();
    let i = items.findIndex((x) => x.classList.contains("sel"));
    items.forEach((x) => x.classList.remove("sel"));
    i = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items[i].classList.add("sel"); items[i].scrollIntoView({ block: "nearest" });
    return;
  }
  if (e.key !== "Enter") return;
  const picked = $("#sr .mi.sel");
  if (picked) { e.preventDefault(); q.blur(); picked.click(); return; }
  const t = S.q.trim().toLowerCase();
  if (!t) return;
  const first = $("#sr .mi");
  if (first) { e.preventDefault(); q.blur(); first.click(); }
});
addEventListener("scroll", () => document.body.classList.toggle("sc", scrollY > 8), { passive: true });

/* keyboard: / search · Esc back · j/k/h/l or arrows move focus · Enter opens */
const focusables = () => $$("#app .hhero .pill, #app .cont, #app .card, #app .ep, #app .hr").filter((el) => el.offsetParent);
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
  const inInput = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
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
  if (e.key === "Enter" && document.activeElement.id === "libpath-settings") { e.preventDefault(); A.dirbrowse(null, document.activeElement); return; }
  if (e.key === "Enter" && document.activeElement.id?.startsWith("mq-")) { e.preventDefault(); A.msearch(document.activeElement.id.slice(3)); return; }
  if (e.key === "/" && !inInput) { e.preventDefault(); q.focus(); return; }
  if (!inInput && !e.ctrlKey && !e.altKey && !e.metaKey) {
    const tab = TABS[+e.key - 1];
    if (tab && e.key !== "0") { e.preventDefault(); nav(tab[0]); return; }
    if (e.key === ",") { e.preventDefault(); nav("settings"); return; }
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

/* ---------- browser history (back/forward, mouse side buttons, #deep links) ---------- */
let navSeeded = false;
const navSnap = () => ({ view: S.view, showId: S.showId, seasonIdx: S.seasonIdx, back: S.showBack, fr: S.fr?.eid ?? null, scroll: scrollY });
const navUrl = () => location.pathname + location.search + (S.view === "show" ? "#show=" + encodeURIComponent(S.showId) : S.view === "fr" ? "#f=" + S.fr.eid : VIEWS.includes(S.view) && S.view !== "home" ? "#" + S.view : "");
function pushNav() {
  const h = navSnap();
  if (navSeeded) history.pushState(h, "", navUrl()); else { navSeeded = true; history.replaceState(h, "", navUrl()); }
}
function applyNav(h) {
  S.navIn = true; S.view = VIEWS.includes(h.view) || h.view === "show" || (h.view === "fr" && h.fr) ? h.view : "home"; S.showId = h.showId ?? null; S.seasonIdx = h.seasonIdx || 0; S.showBack = h.back || "home";
  if (S.view === "fr") S.fr = { eid: h.fr, page: frKnown(h.fr), err: null };
  clearSearch(); render();
  if (S.view === "fr") frLoad(h.fr);
  history.replaceState(h, "", navUrl());
  scrollTo(0, h.scroll || 0);
}
addEventListener("popstate", (e) => applyNav(e.state || {}));
history.replaceState(navSnap(), "", location.href);
navSeeded = true;

/* ---------- boot ---------- */
aniStatusRefresh();
const hashShow = location.hash.match(/^#show=(.+)$/), hashFr = location.hash.match(/^#f=(\d+)$/), hashView = location.hash.match(/^#(library|history|stats|settings)$/);
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
