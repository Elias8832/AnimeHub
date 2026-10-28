/* Anime Hub's service worker. It exists so phones install the app as an
   app (Samsung Internet wants one) and caches nothing: every request goes
   to the PC as before. Only when the PC can't be reached does a page open
   that says so, instead of the browser's error. */
"use strict";
const OFFLINE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#101114"><title>Anime Hub</title>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#101114;color:#f3f3f1;font:16px/1.5 system-ui,sans-serif;text-align:center;padding:0 28px">
<div><h1 style="font-size:24px;margin:0 0 8px">Can't reach your PC</h1><p style="color:#8c8c92;margin:0 0 22px">Is the PC on, Anime Hub running, and the phone on the same Wi-Fi (or Tailscale)?</p>
<button onclick="location.reload()" style="border:0;border-radius:999px;padding:13px 26px;font:600 16px system-ui;background:#f3f3f1;color:#111">Try again</button></div></body>`;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
