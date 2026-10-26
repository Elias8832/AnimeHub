"""The HTTP server, its API routes and app startup."""

import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import LOADED_AT, anicli, anilist, database, downloader, franchise, journal, lengths, library, mapping, metadata, pages, player, progress, recommend, settings, streaming, sync, system, upscale
from .config import config, APP_BROWSERS, CONFIG_PATH, CONFIG_WARNINGS, WEB


def open_show_folder(show_id):
    """Open a disk show's folder in the user's file manager."""
    show = next((s for s in library.scan_library() if s["id"] == show_id), None)
    if not show or not show.get("path"):
        raise ValueError("no such show on disk")
    path = (config.LIBRARY / show["path"]).resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir() or not path.is_relative_to(config.LIBRARY.resolve()):
        raise ValueError("folder not found")
    system.open_external(path)


def open_in_browser(url):
    if not re.fullmatch(r"https://anilist\.co/anime/\d+/?", url or ""):
        raise ValueError("untrusted url")
    system.open_external(url)


AUTH_CALLBACK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Anime Hub — AniList</title><style>
body{font:15px/1.5 system-ui,sans-serif;background:#0a0a0c;color:#f5f5f7;
display:grid;place-items:center;height:100vh;margin:0}
div{max-width:420px;text-align:center}b{color:#97b8ff}
</style></head><body><div id="m">Finishing AniList connection…</div>
<script>
"use strict";
const m = document.getElementById("m");
const h = new URLSearchParams(location.hash.slice(1));
// The token lives in the URL fragment only until now — strip it from the
// address bar (and thereby history/backups) before anything else.
history.replaceState(null, "", "/auth/callback");
const t = h.get("access_token");
if (!t) {
  m.textContent = "No token found in the redirect URL — start the connection from the app again.";
} else {
  fetch("/api/auth/callback", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: t, expires_in: Number(h.get("expires_in")) || null }) })
  .then((r) => r.json())
  .then((j) => {
    if (j.ok) { m.innerHTML = "AniList connected as <b></b> — you can close this tab."; m.querySelector("b").textContent = j.user; setTimeout(() => window.close(), 3000); }
    else m.textContent = "Connect failed: " + (j.error || "unknown reason");
  })
  .catch(() => { m.textContent = "Connect failed — could not reach the hub. Is it still running?"; });
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def check_local(self):
        """Browsers allow cross-site simple POSTs to loopback; a foreign Host
        header (DNS rebinding) or a non-JSON POST body is never ours. The
        accepted port follows the configured one — a hardcoded value rejects
        every request from an alternate-port instance (TODO 22, hit live
        during the config work). The JSON content-type gate lives in do_POST.
        Config `remote_hosts` adds names a proxy forwards to us, e.g.
        `tailscale serve` (Decision 36); those match on any port, since the
        proxy, not this server, owns it."""
        host = self.headers.get("Host", "")
        if host in (f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"):
            return True
        name = host.rsplit(":", 1)[0] if ":" in host else host
        if name and name.lower() in config.REMOTE_HOSTS:
            return True
        self.send_error(403)
        return False

    def send_json(self, obj, code=200, etag=False):
        """JSON out. Gzipped when the browser takes it (TODO 61: the library
        payload is ~800 KB raw, ~120 KB gzipped — it matters on the phone).
        `etag`: a body the browser already has is a bodiless 304."""
        body = json.dumps(obj).encode()
        tag = None
        if etag and code == 200:
            tag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
        zipped = len(body) > 2048 and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if zipped:
            body = gzip.compress(body, compresslevel=4)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if zipped:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if tag:
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, cache=False):
        try:
            body = Path(path).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_file_body(body, ctype, cache)

    def send_banner(self, path):
        """A banner can be swapped for its upscaled version under the same
        name, so the browser asks again each time (a bodiless 304 while
        unchanged) instead of keeping the old picture for a day."""
        try:
            st = Path(path).stat()
        except OSError:
            self.send_error(404)
            return
        tag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        try:
            body = Path(path).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", tag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file_body(self, body, ctype, cache=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.check_local():
            return
        route = urlparse(self.path).path
        if route == "/api/library":
            self.send_json(library.full_library(), etag=True)
        elif route == "/api/anilist":
            try:
                self.send_json(anilist.anilist_user_data())
            except Exception as e:
                if config.ANILIST_CACHE.exists():
                    try:
                        stale = json.loads(config.ANILIST_CACHE.read_text(encoding="utf-8"))
                        stale["stale"] = True
                        self.send_json(stale)
                        return
                    except Exception:
                        pass
                self.send_json({"error": anilist.friendly_error(e)}, 502)
        elif route == "/api/entry":
            qs = parse_qs(urlparse(self.path).query)
            try:
                mid = int(qs.get("id", ["0"])[0])
            except ValueError:
                self.send_json({"error": "'id' must be an integer"}, 400)
                return
            if mid <= 0:
                self._bad("'id' must be an integer")
                return
            if not anilist.load_token():
                # No-AniList mode (Decision 10's local fallback, TODO 17):
                # the editing surface renders from the curation overlay
                # instead of demanding a connection — same fields, local
                # store. A present-but-expired token still falls through to
                # the live read, which raises the re-auth prompt.
                self.send_json({"local": True,
                                "entry": library.curation_get("aid:%d" % mid)})
                return
            try:
                # pending (additive): a queued outbox intent for this media
                # drives the panel's "Waiting to sync" chip. A queued edit
                # isn't in any stored read yet, so that panel reads live.
                # Otherwise the panel never waits (TODO 61): a known state
                # comes back at once, "stale" (additive) while a live read
                # runs — the page asks again — and a live read waits at
                # most a few seconds for an AniList slot.
                pending = bool(sync.outbox_pending_for(mid))
                with anilist.quick():
                    state = anilist.anilist_entry_state(mid) if pending else anilist.anilist_entry_state_quick(mid)
                out = {"state": state, "score_format": anilist._score_format(), "pending": pending}
                if state and state.pop("stale", False):
                    out["stale"] = True
                self.send_json(out)
            except anilist.AniListAuthError as e:
                self.send_json({"error": e.reason, "auth": True}, 401)
            except Exception as e:
                self.send_json({"error": anilist.friendly_error(e)}, 502)
        elif route == "/api/journal":
            # Additive (Elias, 2026-10-24): the Journal from your AniList activity.
            try:
                since = int(parse_qs(urlparse(self.path).query).get("since", ["0"])[0])
            except ValueError:
                self._bad("'since' must be an integer (unix seconds)")
                return
            self.send_json(journal.journal(since))
        elif route == "/api/recommendations":
            # TODO 65 (additive): Home's "Recommended for you" row.
            self.send_json(recommend.recommendations())
        elif route == "/api/outbox":
            # TODO 14: the sync chip's list of queued changes (additive).
            self.send_json({"items": sync.outbox_items()})
        elif route == "/api/auth/status":
            self.send_json(anilist.auth_status())
        elif route == "/api/settings":
            # TODO 15: the settings surface's read side (Decision 5) —
            # toggles + running config + detected tools, never the token.
            self.send_json(settings.settings_payload())
        elif route == "/api/browse":
            # Decision 32: the library folder picker walks one level at a time.
            qs = parse_qs(urlparse(self.path).query)
            try:
                self.send_json(settings.browse_dirs(qs.get("path", [""])[0]))
            except ValueError as e:
                self._bad(str(e))
        elif route == "/auth/callback":
            self.send_file_body(AUTH_CALLBACK_PAGE.encode(), "text/html; charset=utf-8")
        elif route == "/api/now":
            outbox_n = sync.outbox_count()
            # ani-cli downloads (additive): the nav's downloads popover.
            downloads = downloader.downloads_payload()
            notice = (dict(streaming._session_notice) if streaming._session_notice["msg"]
                      and time.time() - streaming._session_notice["ts"] < 30 else None)
            vpn = settings.vpn_status()
            # Additive: banners swapped for their upscaled version (or back).
            art = upscale.art_payload()
            # Additive (TODO 61): the metadata pass's progress while it runs,
            # so the library note follows it without refetching the library.
            meta = ({"running": True, "pending": metadata._meta_status["pending"],
                     "current": metadata._meta_status["current"], "phase": metadata._meta_status["phase"]}
                    if metadata._meta_status["running"] else None)
            with player._active_lock:
                if not player._active:
                    payload = {"playing": False}
                    # Additive (TODO 24): a play click that produced nothing
                    # explains itself for a short window after the failure.
                    if player._session_error["error"] and time.time() - player._session_error["ts"] < 60:
                        payload["error"] = player._session_error["error"]
                    # Show-completion popup event (TODO 9, Decision 25):
                    # delivered until dismissed, then never again.
                    pending = progress._pending_completion()
                    if pending:
                        payload["completed"] = pending
                    # Additive (TODO 10): a file finished, the next episode
                    # is online only — the hub asks before ani-cli fetches.
                    ask = player.next_prompt()
                    if ask:
                        payload["next_prompt"] = {**{k: ask.get(k) for k in ("id", "label", "show")},
                                                 # additive (D39): "online" or "aniskip"
                                                 "kind": ask.get("kind") or "online"}
                    if outbox_n:
                        # Quiet sync chip (TODO 14, Decision 3).
                        payload["outbox"] = outbox_n
                    if downloads:
                        payload["downloads"] = downloads
                    if notice:
                        payload["notice"] = notice
                    # Additive: the VPN chip follows the connection live.
                    payload["vpn"] = vpn
                    if meta:
                        payload["meta"] = meta
                    if art:
                        payload["art"] = art
                    self.send_json(payload)
                    return
                a = next(iter(player._active.values()))
                payload = {"playing": True, "show": a["show_title"], "label": a["label"],
                           "percent": a.get("percent") or 0, "episode_id": a["ep_id"],
                           "loading": bool(a.get("loading")), "vpn": vpn}
                if meta:
                    payload["meta"] = meta
                # Additive (TODO 10): where the next episode comes from.
                if a.get("next"):
                    payload["next"] = a["next"]
                if notice:
                    payload["notice"] = notice
                if outbox_n:
                    payload["outbox"] = outbox_n
                if downloads:
                    payload["downloads"] = downloads
                if art:
                    payload["art"] = art
                self.send_json(payload)
        elif route == "/api/downloads":
            self.send_json({"jobs": downloader.downloads_payload()})
        elif route == "/api/franchise":
            # The franchise page holding an entry (phase 4): every season,
            # movie and special, local and online episodes as one list.
            try:
                mid = int(parse_qs(urlparse(self.path).query).get("id", ["0"])[0])
            except ValueError:
                mid = 0
            if mid <= 0:
                self._bad("'id' must be a positive integer")
            else:
                # peek (additive, TODO 61): the page as stored, starting no
                # background work — a card's hover prefetch.
                peek = parse_qs(urlparse(self.path).query).get("peek", ["0"])[0] == "1"
                self.send_json(pages.franchise_view(mid, peek=peek))
        elif route.startswith("/img/"):
            name = os.path.basename(route)
            p = config.POSTERS / name
            if name.endswith(".banner.jpg"):
                self.send_banner(p)
            else:
                self.send_file(p, "image/jpeg", cache=True)
        elif route.startswith("/epimg/"):
            self.send_file(config.EPIMGS / os.path.basename(route), "image/jpeg", cache=True)
        elif route in ("/", "/index.html"):
            self.send_file(WEB / "index.html", "text/html; charset=utf-8")
        elif route == "/style.css":
            self.send_file(WEB / "style.css", "text/css")
        elif route == "/app.js":
            self.send_file(WEB / "app.js", "text/javascript")
        elif route.startswith("/fonts/") and route.endswith(".woff2"):
            # TODO 60 (Decision 38): the UI fonts ship with the app, so it
            # looks the same offline. Basename only — no path walking.
            self.send_file(WEB / "fonts" / os.path.basename(route), "font/woff2", cache=True)
        else:
            self.send_error(404)

    def _drain(self):
        """Read the body of a refused POST: Windows resets a connection that
        closes with unread data, and the client loses the refusal itself
        (TODO 74)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return
        if 0 < length <= 1_000_000:
            self.rfile.read(length)

    def do_POST(self):
        if not self.check_local():
            self._drain()
            return
        # Media type parsed properly (TODO 22): a substring check let
        # "text/plain; note=application/json" through — the classic
        # cross-site "simple POST" trick.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_json({"error": "expected application/json"}, 415)
            self._drain()
            return
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_json({"error": "invalid Content-Length"}, 400)
            return
        if length < 0:
            # rfile.read(-1) would block the handler thread until EOF.
            self.send_json({"error": "invalid Content-Length"}, 400)
            return
        if length > 1_000_000:
            self.send_json({"error": "request body too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as e:
            # Structured 400 (TODO 22): malformed JSON used to become an
            # empty body, whose empty episode id was written into the DB.
            self.send_json({"error": f"invalid JSON body: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self.send_json({"error": "request body must be a JSON object"}, 400)
            return
        if route == "/api/play":
            if self._fields(body, strings=("id",), opt_ints=("version",)):
                mode = body.get("mode")
                start = body.get("from_start", False)  # additive: the episode menu's "Play from the start"
                # additive: the user's answer to AniSkip without a VPN (Decision 39)
                aniskip_ok = body.get("aniskip")
                if not isinstance(start, bool):
                    self._bad("'from_start' must be a boolean")
                elif aniskip_ok is not None and not isinstance(aniskip_ok, bool):
                    self._bad("'aniskip' must be a boolean")
                elif mode is not None and mode not in anicli.STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(anicli.STREAM_MODES))
                elif mode and body["id"].startswith("stream:"):
                    if aniskip_ok is not None:
                        player.answer_aniskip(body["id"], aniskip_ok)
                    # An online episode in the other language ("Play dubbed").
                    player.dismiss_next_prompt()
                    self.send_json(streaming.start_stream(body["id"], mode, from_start=start))
                else:
                    if aniskip_ok is not None:
                        player.answer_aniskip(body["id"], aniskip_ok)
                    self.send_json(player.start_play(body["id"], body.get("version", 0), from_start=start))
        elif route == "/api/settings":
            # TODO 15: automation toggles + tour dismissal (Decision 5).
            # Only known boolean keys; everything else is a structured 400.
            keys = ("auto_progress_sync", "auto_add_watch", "anilist_watched", "vpn_icon", "vpn_warn", "aniskip", "aniskip_vpn_warn", "tour_done",
                    "upscale_banners", "measure_lengths")
            # ani-cli bridge preferences: closed vocabularies.
            choices = {"stream_quality": anicli.STREAM_QUALITIES, "stream_mode": anicli.STREAM_MODES,
                       "skip_op": settings.SKIP_MODES, "skip_ed": settings.SKIP_MODES,
                       "upscale_style": tuple(upscale.STYLES)}
            for key in keys:
                if key in body and not isinstance(body[key], bool):
                    self._bad("'%s' must be a boolean" % key)
                    return
            for key, allowed in choices.items():
                if key in body and body[key] not in allowed:
                    self._bad("'%s' must be one of: %s" % (key, ", ".join(allowed)))
                    return
            if not any(key in body for key in (*keys, *choices)):
                self._bad("nothing to change — send one of: %s" % ", ".join((*keys, *choices)))
                return
            for key in choices:
                if key in body:
                    settings.set_setting(key, body[key])
            if "upscale_style" in body:
                upscale.style_changed()
            for key in keys:
                if key not in body:
                    continue
                if key == "tour_done":
                    settings.set_setting("tour_done", "1" if body[key] else "0")
                elif key == "upscale_banners":
                    try:
                        settings.set_upscale(body[key])
                    except ValueError as e:
                        self._bad(str(e))
                        return
                else:
                    settings.set_automation(key, body[key])
                    if key == "measure_lengths" and body[key]:
                        lengths.turned_on()
            self.send_json({"ok": True, **settings.settings_payload()})
        elif route == "/api/watched":
            if self._fields(body, strings=("id",), bools=("watched",)):
                progress.set_watched(body["id"], body["watched"])
                self.send_json({"ok": True})
        elif route == "/api/bulk-watched":
            # TODO 6 (Decision 27): one mark for a season or whole show —
            # bookkeeping, not playback; the Decision 27 sync rides the
            # outbox afterwards.
            if self._fields(body, strings=("show_id",), bools=("watched",)):
                season = body.get("season")
                if season is not None and (isinstance(season, bool)
                                           or not isinstance(season, int)
                                           or season < 0):
                    self._bad("'season' must be a non-negative integer or null")
                    return
                ids = body.get("ids")
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                    self._bad("'ids' must be a list of episode ids")
                    return
                r = progress.bulk_watched(body["show_id"], season, body["watched"], ids)
                self.send_json(r, 200 if r.get("ok") else 404)
        elif route == "/api/entry-watched":
            # The franchise page's marks: one entry's episodes (all, or the
            # listed numbers), file and online alike.
            if self._fields(body, ints=("media_id",), bools=("watched",)):
                nums = body.get("numbers")
                if not body.get("media_id") or body["media_id"] < 0:
                    self._bad("'media_id' must be a positive integer")
                elif nums is not None and (not isinstance(nums, list) or not all(
                        isinstance(n, (int, float)) and not isinstance(n, bool) for n in nums)):
                    self._bad("'numbers' must be a list of episode numbers")
                else:
                    self.send_json(pages.entry_watched(body["media_id"], body["watched"], nums))
        elif route == "/api/reset":
            if self._fields(body, strings=("id",)):
                progress.reset_progress(body["id"])
                self.send_json({"ok": True})
        elif route == "/api/open":
            if self._fields(body, strings=("url",)):
                try:
                    open_in_browser(body["url"])
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"error": str(e)}, 400)
        elif route == "/api/open-folder":
            if self._fields(body, strings=("show_id",)):
                try:
                    open_show_folder(body["show_id"])
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"error": str(e)}, 400)
        elif route == "/api/adopt-root":
            res = settings.adopt_root()
            self.send_json(res, 200 if res.get("ok") else 400)
        elif route == "/api/library-root":
            # Decision 32: choose the library folder from the UI; adopt is
            # the user's confirmed switch away from an indexed root (23.7).
            if self._fields(body, strings=("path",)):
                if "adopt" in body and not isinstance(body["adopt"], bool):
                    self._bad("'adopt' must be a boolean")
                    return
                try:
                    self.send_json(settings.set_library_root(body["path"], body.get("adopt", False)))
                except ValueError as e:
                    self._bad(str(e))
        elif route == "/api/match":
            if self._fields(body, strings=("show_id",)):
                aid = body.get("anilist_id")
                if aid is not None and (isinstance(aid, bool) or not isinstance(aid, int)):
                    self._bad("'anilist_id' must be an integer")
                elif "query" in body and (not isinstance(body["query"], str) or not body["query"].strip()):
                    self._bad("'query' must be a non-empty string")
                else:
                    self.send_json(metadata.set_match(body["show_id"], aid, body["query"].strip() if "query" in body else None))
        elif route == "/api/season-match":
            # One season of a folder → a chosen entry ("wrong entry?"),
            # none: true = no entry, neither = back to the automatic mapping.
            if self._fields(body, strings=("show_id", "season")):
                aid = body.get("anilist_id")
                if aid is not None and (isinstance(aid, bool) or not isinstance(aid, int)):
                    self._bad("'anilist_id' must be an integer")
                else:
                    self.send_json(mapping.set_season_pin(body["show_id"], body["season"], aid,
                                                  none=body.get("none") is True))
        elif route == "/api/episode-numbers":
            # Needs a look: the user's numbers for files their names give none
            # for ({episode id: number}; sending the shown ones confirms them).
            if self._fields(body, strings=("show_id",)):
                nums = body.get("numbers")
                if not isinstance(nums, dict) or not nums or not all(
                        isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 9999 for v in nums.values()):
                    self._bad("'numbers' must map episode ids to numbers 0–9999")
                else:
                    res = library.set_episode_numbers(body["show_id"], nums)
                    self.send_json(res, 200 if res.get("ok") else 400)
        elif route == "/api/keep-local":
            # TODO 12c: "Not on AniList — keep local" (keep: false undoes it).
            if self._fields(body, strings=("show_id",)):
                if "keep" in body and not isinstance(body["keep"], bool):
                    self._bad("'keep' must be a boolean")
                else:
                    res = metadata.set_kept_local(body["show_id"], body.get("keep", True))
                    self.send_json(res, 200 if res.get("ok") else 400)
        elif route == "/api/match-candidates":
            if self._fields(body, strings=("query",)):
                if "fresh" in body and not isinstance(body["fresh"], bool):
                    self._bad("'fresh' must be a boolean")
                    return
                try:
                    self.send_json(metadata.fetch_media_candidates(body["query"], body.get("fresh") is True)[:10])
                except Exception as e:
                    self.send_json({"error": anilist.friendly_error(e)}, 502)
        elif route == "/api/auth/start":
            r = anilist.auth_start()
            self.send_json(r, 200 if r.get("url") else 400)
        elif route == "/api/auth/callback":
            # The redirect page's one-shot POST; token came via the URL
            # fragment (never a server-visible URL), correlation is the
            # pending-flow check inside auth_finish.
            if self._fields(body, strings=("access_token",)):
                ei = body.get("expires_in")
                if ei is not None and (isinstance(ei, bool) or not isinstance(ei, int) or ei <= 0):
                    self._bad("'expires_in' must be a positive integer")
                else:
                    r = anilist.auth_finish(body["access_token"], ei, require_pending=True)
                    self.send_json(r, 200 if r.get("ok") else 400)
        elif route == "/api/auth/token":
            # Manual paste fallback (Decision 20): also the expiry recovery.
            if self._fields(body, strings=("token",)):
                r = anilist.auth_finish(body["token"], require_pending=False)
                self.send_json(r, 200 if r.get("ok") else 400)
        elif route == "/api/auth/logout":
            self.send_json(anilist.auth_disconnect())
        elif route == "/api/next-prompt/dismiss":
            player.dismiss_next_prompt()
            self.send_json({"ok": True})
        elif route == "/api/completion-seen":
            if self._fields(body, ints=("media_id", "count")):
                progress._dismiss_completion(body["media_id"], body["count"])
                self.send_json({"ok": True})
        elif route == "/api/reconcile":
            # TODO 16 (Decision 14): connect-time merge. Facts apply inside
            # (automatic — higher watched count wins, both directions);
            # curation conflicts come back for the user to decide.
            try:
                self.send_json(sync.reconcile_analyze())
            except Exception as e:
                self.send_json({"error": anilist.friendly_error(e)}, 502)
        elif route == "/api/reconcile/apply":
            # TODO 16: curation winners. Facts were already applied during
            # analyze and are not user-decidable (Decision 14).
            decisions = body.get("decisions")
            if not isinstance(decisions, list):
                self._bad("'decisions' must be a list")
                return
            try:
                applied = sync.reconcile_apply(decisions)
            except Exception as e:
                self.send_json({"error": anilist.friendly_error(e)}, 502)
                return
            threading.Thread(target=anilist.refresh_user_data, kwargs={"force": True},
                             daemon=True).start()
            self.send_json({"ok": True, "applied": applied})
        elif route == "/api/outbox/flush":
            # TODO 14: the sync chip's "try now" — every held row, this moment.
            before = sync.outbox_count()
            sync.outbox_retry_now()
            sent = sync.outbox_flush()
            self.send_json({"sent": sent, "before": before, "left": sync.outbox_count()})
        elif route == "/api/outbox/cancel":
            if self._fields(body, ints=("id",)):
                self.send_json({"ok": sync.outbox_cancel(body["id"]), "left": sync.outbox_count()})
        elif route == "/api/reconcile/dismiss":
            sync.reconcile_clear()
            self.send_json({"ok": True})
        elif route == "/api/entry":
            # TODO 9: one editing route — AniList writes through the
            # chokepoint when connected, local curation overlay otherwise
            # (Decision 10's no-AniList nuance, Decision 18.5).
            if not isinstance(body.get("media_id"), int) or isinstance(body.get("media_id"), bool) \
                    or body["media_id"] <= 0:
                self._bad("'media_id' must be an integer")
                return
            patch = body.get("patch") or {}
            fav = body.get("favourite")
            if not isinstance(patch, dict):
                self._bad("'patch' must be an object")
                return
            patch = {k: v for k, v in patch.items()
                     if k in ("status", "score", "progress", "repeat",
                              "started_at", "completed_at")}
            if "patch" in body and not patch and fav is None:
                self.send_json({"error": "empty patch"})
                return
            ref = "aid:%d" % body["media_id"]
            if "status" in patch and patch["status"] not in anilist.ENTRY_STATUS:
                self._bad("invalid status")
                return
            if "score" in patch:
                try:
                    patch["score"] = float(patch["score"])
                except (TypeError, ValueError):
                    self._bad("'score' must be a number")
                    return
                if not 0 <= patch["score"] <= 100:  # also rejects nan/inf
                    self._bad("'score' must be between 0 and 100")
                    return
            for k in ("progress", "repeat"):
                if k in patch:
                    if isinstance(patch[k], bool) or not isinstance(patch[k], int) or patch[k] < 0:
                        self._bad("'%s' must be a non-negative integer" % k)
                        return
            # Validate everything before the local write: a rejected field
            # must not leave the overlay disagreeing with AniList.
            for k in ("started_at", "completed_at"):
                if k in patch:
                    if patch[k] in ("", None):
                        patch[k] = None
                    elif not isinstance(patch[k], str) or anilist._fuzzy_date_frag(patch[k]) is None:
                        self._bad("'%s' must be a YYYY[-MM[-DD]] date" % k)
                        return
            if fav is not None and not isinstance(fav, bool):
                self._bad("'favourite' must be a boolean")
                return
            cpatch = {k: v for k, v in patch.items() if k in
                      ("status", "score", "rewatches", "started_at", "finished_at")}
            if "repeat" in patch:
                cpatch["rewatches"] = patch["repeat"]
            if "completed_at" in patch:
                cpatch["finished_at"] = patch["completed_at"]
            if fav is not None:
                cpatch["favourite"] = fav
            library.curation_set(ref, cpatch)
            fav_result = None
            if anilist.load_token():
                try:
                    if patch or fav is not None:
                        result = anilist.anilist_entry_save(body["media_id"], patch) \
                            if patch else anilist.anilist_entry_state(body["media_id"])
                        if fav is not None and not result.get("error"):
                            fav_result = anilist.anilist_favourite_set(body["media_id"], fav)
                            if fav_result and fav_result.get("error"):
                                self.send_json({"error": fav_result["error"]})
                                return
                            if fav_result:
                                result = fav_result
                        if result and not result.get("error"):
                            cpatch2 = {}
                            ent = result.get("entry") or {}
                            if ent.get("status"):
                                cpatch2["status"] = ent["status"]
                            if ent.get("score") is not None:
                                cpatch2["score"] = ent["score"]
                            if ent.get("repeat") is not None:
                                cpatch2["rewatches"] = ent["repeat"]
                            if ent.get("started_at"):
                                cpatch2["started_at"] = ent["started_at"]
                            if ent.get("completed_at"):
                                cpatch2["finished_at"] = ent["completed_at"]
                            if result.get("is_favourite") is not None:
                                # favourite lives top-level on the state, not
                                # inside entry — without this the local
                                # overlay never records AniList favourites.
                                cpatch2["favourite"] = bool(result["is_favourite"])
                            library.curation_set(ref, cpatch2)
                        self.send_json(result if result else {"error": "unknown failure"})
                        return
                except anilist.AniListAuthError as e:
                    # Dormant outbox (Decision 24): the intent is kept,
                    # held until a valid token exists again.
                    sync.outbox_enqueue(body["media_id"], patch=patch, favourite=fav)
                    self.send_json({"error": e.reason, "auth": True,
                                    "queued": True}, 401)
                    return
                except anilist.AniListTransientError as e:
                    # Offline / AniList down (TODO 14, Decision 3): the
                    # local change is already committed; the intent waits.
                    sync.outbox_enqueue(body["media_id"], patch=patch, favourite=fav)
                    cur = library.curation_get(ref) or {}
                    self.send_json({"queued": True, "local": True,
                                    "entry": cur, "reason": anilist.friendly_error(e)})
                    return
                except ValueError as e:
                    # AniList rejected the mutation itself (HTTP 400 with a
                    # non-auth GraphQL error). The local change stands; the
                    # message goes to the user (D18.4: never queue).
                    self.send_json({"error": str(e), "local": True})
                    return
                except Exception as e:
                    self.send_json({"error": anilist.friendly_error(e)}, 502)
                    return
            # No-AniList mode (Decision 10): the same fields store locally.
            out = library.curation_get(ref) or {}
            if fav is not None:
                library.curation_set(ref, {"favourite": fav})
                out = library.curation_get(ref)
            self.send_json({"local": True, "entry": out})
        elif route == "/api/refresh":
            self.send_json({"ok": True})
            threading.Thread(target=anilist.background_work(metadata.refresh_meta), daemon=True).start()
        elif route.startswith("/api/stream/") or route.startswith("/api/download"):
            self._stream_route(route, body)
        else:
            self.send_error(404)

    def _stream_route(self, route, body):
        """The ani-cli bridge's routes. AniCliError is ani-cli (or the site
        behind it) failing — a readable 502, never a traceback."""
        try:
            if route == "/api/stream/search":
                if self._fields(body, strings=("query",)):
                    self.send_json(anicli.anicli_search(body["query"]))
            elif route == "/api/stream/link":
                if not self._fields(body, strings=("query",), ints=("media_id",), opt_ints=("index",)):
                    return
                title, mode, display = body.get("title"), body.get("mode"), body.get("display")
                if body["media_id"] <= 0:
                    self._bad("'media_id' must be a positive integer")
                elif title is not None and not isinstance(title, str):
                    self._bad("'title' must be a string or null")
                elif mode is not None and mode not in anicli.STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(anicli.STREAM_MODES))
                elif display is not None and not isinstance(display, str):
                    self._bad("'display' must be a string")
                else:
                    self.send_json(streaming.stream_link_set(body["media_id"], body["query"], title,
                                                   body.get("index") or 1, mode, display))
            elif route == "/api/stream/mode":
                if not self._fields(body, strings=("mode",), ints=("media_id",)):
                    return
                if body["mode"] not in anicli.STREAM_MODES:
                    self._bad("'mode' must be one of: %s" % ", ".join(anicli.STREAM_MODES))
                else:
                    self.send_json(streaming.stream_switch_mode(body["media_id"], body["mode"]))
            elif route == "/api/download":
                eps = body.get("episodes")
                if not self._fields(body, ints=("media_id",)):
                    return
                if not isinstance(eps, list) or not eps or not all(isinstance(e, str) for e in eps):
                    self._bad("'episodes' must be a non-empty list of episode strings")
                else:
                    self.send_json(downloader.download_enqueue(body["media_id"], eps))
            elif route == "/api/download/cancel":
                if self._fields(body, ints=("id",)):
                    self.send_json(downloader.download_cancel(body["id"]))
            elif route == "/api/download/clear":
                self.send_json(downloader.download_clear())
            else:
                self.send_error(404)
        except anicli.AniCliError as e:
            self.send_json({"error": str(e)}, 502)

    def _bad(self, msg):
        self.send_json({"error": msg}, 400)
        return False

    def _fields(self, body, strings=(), ints=(), bools=(), opt_ints=()):
        """Typed request fields (TODO 22): structured 400s instead of
        silent empty-string writes. Strings must be non-empty; ints reject
        bools and must be present (opt_ints may be left out); bools must
        actually be booleans."""
        for name in strings:
            v = body.get(name)
            if not isinstance(v, str) or not v:
                return self._bad(f"missing or invalid '{name}'")
        for name in (*ints, *opt_ints):
            if name not in body and name in opt_ints:
                continue
            v = body.get(name)
            if isinstance(v, bool) or not isinstance(v, int):
                return self._bad(f"'{name}' must be an integer")
        for name in bools:
            if not isinstance(body.get(name), bool):
                return self._bad(f"'{name}' must be a boolean")
        return True


def open_ui():
    """How the user reaches the UI on startup (Decision 26). "app": a
    Chromium-family app-mode window — a standalone window with no tabs or
    address bar, so the hub feels like a desktop app; falls back to a plain
    browser tab when no such browser exists. "browser": always a plain tab.
    "none" (or --no-browser for one run): nothing opens; the server just
    runs. Closing the window never stops the server."""
    url = f"http://127.0.0.1:{config.PORT}"
    if config.WINDOW == "app":
        exe = next(iter(system.app_browsers(APP_BROWSERS)), None)
        if exe:
            subprocess.Popen([exe, f"--app={url}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **system.detached())
            return
    import webbrowser
    webbrowser.open(url)


def main():
    no_browser = "--no-browser" in sys.argv
    system.utf8_stdio()
    for w in CONFIG_WARNINGS:
        print(f"anime-hub: {w}", file=sys.stderr, flush=True)
    steps = [("loading", time.monotonic())]
    for d in (config.DATA, config.CACHE, config.POSTERS, config.EPIMGS):
        d.mkdir(parents=True, exist_ok=True)
    database.init_db()
    steps.append(("database", time.monotonic()))
    try:
        database.cache_prune()
    except Exception as e:
        print(f"anime-hub: cache prune skipped — {e}", file=sys.stderr, flush=True)
    steps.append(("cache prune", time.monotonic()))
    try:
        server = system.http_server_class()(("127.0.0.1", config.PORT), Handler)
        steps.append(("bind", time.monotonic()))
    except OSError as e:
        # A bind failure must be readable (found by the cold-machine
        # simulation): the default case is a second instance or a taken
        # configured port, not a Python mystery.
        print(f"anime-hub: cannot bind 127.0.0.1:{config.PORT} — {e}. "
              f"Is another instance already running, or is the configured 'port' taken?",
              file=sys.stderr, flush=True)
        sys.exit(1)
    # A slow start says where the time went (the desktop app waits on it; a
    # first start on Windows once took ~28 s that no test machine showed).
    if steps[-1][1] - LOADED_AT > 3:
        times, last = [], LOADED_AT
        for name, at in steps:
            times.append(f"{name} {at - last:.1f} s")
            last = at
        print("anime-hub: slow start — " + ", ".join(times), file=sys.stderr, flush=True)
    threading.Thread(target=anilist.background_work(metadata.refresh_meta), daemon=True).start()
    # Warm the AniList cache so the spine (TODO 8) is populated on the very
    # first library view; /api/anilist visits keep it fresh afterwards.
    if anilist.load_anilist_username():
        threading.Thread(target=anilist.refresh_user_data, daemon=True).start()
    # Durable-write flush loop (TODO 14): retries held intents with backoff.
    threading.Thread(target=sync.outbox_loop, daemon=True).start()
    # Spine freshness (TODO 8/17): keep the AniList cache warm in the
    # background — a stale cache must never read as a disconnect.
    threading.Thread(target=anilist.background_work(sync.anilist_refresh_loop), daemon=True).start()
    threading.Thread(target=anilist.background_work(franchise.franchise_refresh_loop), daemon=True).start()
    # Banner upscaling (opt-in): pick up banners saved while it was off.
    threading.Thread(target=upscale.queue_all, daemon=True).start()
    # Episode lengths: measure the files the scans find.
    lengths.start()
    print(f"Anime Hub → http://127.0.0.1:{config.PORT}", flush=True)
    print(f"  config   {CONFIG_PATH}{' (missing — defaults in use)' if not CONFIG_PATH.exists() else ''}", flush=True)
    print(f"  library  {config.LIBRARY or 'not configured — choose a folder in Settings (or set library_root in the config file)'}", flush=True)
    u = anilist.load_anilist_username()
    a = anilist.auth_status()
    print(f"  anilist  reads as {u or 'not configured (optional)'}; oauth "
          f"{'connected as ' + a['user'] if a.get('connected') else 'not connected'}", flush=True)
    if not no_browser and config.WINDOW != "none":
        open_ui()
    server.serve_forever()
