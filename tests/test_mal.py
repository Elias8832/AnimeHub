"""MyAnimeList as the account (Decision 75): one account at a time, the MAL
lists turned into AniList-keyed entries, tracking and edits saved to MAL,
the login refreshed by itself, and AniList kept as the catalog."""

import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from tests import support
from animehub import anilist, database, mal, remote, server, settings, sync
from animehub.config import config, CONFIG, apply_config


class FakeMal:
    """MyAnimeList's token endpoint and API, enough for the hub's calls."""

    def __init__(self):
        self.calls = []
        self.tokens = {"good": True}
        self.refresh_ok = True
        self.list = []
        self.status = {}  # mal id → list status
        self.me = {"id": 77, "name": "malfan", "anime_statistics": {
            "num_episodes": 1234, "num_days_watched": 20.5, "mean_score": 7.8, "num_items": 99}}
        self.fail = None  # an HTTP code every API call answers with

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        body = parse_qs(req.data.decode()) if req.data else {}
        self.calls.append((method, url, body, req.headers.get("Authorization")))
        if url == mal.TOKEN_URL:
            grant = body["grant_type"][0]
            if grant == "authorization_code":
                if body.get("code") != ["the-code"]:
                    raise self.error(400, {"error": "invalid_grant"})
                return self.answer({"access_token": "good", "refresh_token": "r1", "expires_in": 2678400})
            if grant == "refresh_token" and self.refresh_ok:
                self.tokens["fresh"] = True
                return self.answer({"access_token": "fresh", "refresh_token": "r2", "expires_in": 2678400})
            raise self.error(400, {"error": "invalid_grant"})
        if self.fail:
            raise self.error(self.fail, {"error": "down"})
        auth = (req.headers.get("Authorization") or "").replace("Bearer ", "")
        if not self.tokens.get(auth):
            raise self.error(401, {"error": "invalid_token"})
        path = urlparse(url).path.replace("/v2", "", 1)
        q = parse_qs(urlparse(url).query)
        if path == "/users/@me":
            return self.answer(self.me)
        if path == "/users/@me/animelist":
            off, lim = int(q.get("offset", ["0"])[0]), int(q["limit"][0])
            rows = self.list[off:off + lim]
            return self.answer({"data": rows, "paging": {"next": "more"} if off + lim < len(self.list) else {}})
        if path.startswith("/anime/") and path.endswith("/my_list_status") and method == "PATCH":
            mid = int(path.split("/")[2])
            ls = dict(self.status.get(mid) or {"status": "plan_to_watch", "score": 0, "num_episodes_watched": 0})
            for k, v in body.items():
                ls["num_episodes_watched" if k == "num_watched_episodes" else k] = v[0]
            for k in ("score", "num_episodes_watched", "num_times_rewatched"):
                if k in ls:
                    ls[k] = int(ls[k])
            if "is_rewatching" in ls:
                ls["is_rewatching"] = ls["is_rewatching"] in (True, "true")
            self.status[mid] = ls
            return self.answer(ls)
        if path.startswith("/anime/"):
            mid = int(path.split("/")[2])
            out = {"id": mid, "num_episodes": 12}
            if mid in self.status:
                out["my_list_status"] = self.status[mid]
            return self.answer(out)
        raise self.error(404, {"error": "not_found"})

    @staticmethod
    def answer(obj):
        r = io.BytesIO(json.dumps(obj).encode())
        r.__enter__ = lambda *a: r
        return _Ctx(r)

    @staticmethod
    def error(code, obj):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(json.dumps(obj).encode()))


class _Ctx:
    def __init__(self, r):
        self.r = r

    def __enter__(self):
        return self.r

    def __exit__(self, *a):
        return False


def media(aid, mal_id, title, status="FINISHED", episodes=12, nxt=None):
    return {"id": aid, "idMal": mal_id, "status": status, "averageScore": 80, "season": "SPRING",
            "episodes": episodes, "format": "TV", "duration": 24, "startDate": {"year": 2020},
            "genres": ["Action"], "nextAiringEpisode": nxt,
            "coverImage": {"extraLarge": "p.jpg", "color": "#fff"}, "bannerImage": None,
            "title": {"english": title, "romaji": title}}


CATALOG = {1: media(101, 1, "One"), 2: media(102, 2, "Two", "RELEASING", None,
                                              {"episode": 5, "timeUntilAiring": 3600, "airingAt": 2000000000}),
           3: media(103, 3, "Three")}


class FakeAniList:
    """AniList's public reads in MAL mode: the catalog and the panel extras."""

    def __init__(self):
        self.catalog_calls = 0
        self.down = False

    def __call__(self, payload, token=None):
        assert token is None, "MAL mode never sends an AniList login"
        if self.down:
            raise urllib.error.URLError("offline")
        q, v = payload["query"], payload.get("variables") or {}
        if "idMal_in" in q:
            self.catalog_calls += 1
            return {"data": {"Page": {"media": [CATALOG[i] for i in v["ids"] if i in CATALOG]}}}
        if "studios" in q:
            m = next(x for x in CATALOG.values() if x["id"] == v["id"])
            return {"data": {"Media": {"id": m["id"], "idMal": m["idMal"], "format": "TV", "episodes": 12,
                                       "popularity": 5, "favourites": 1, "nextAiringEpisode": None,
                                       "studios": {"nodes": [{"name": "Studio"}]}}}}
        if "idMal" in q:
            m = next((x for x in CATALOG.values() if x["id"] == v["id"]), None)
            return {"data": {"Media": {"idMal": m and m["idMal"]}}}
        raise AssertionError("unexpected AniList query: " + q[:60])


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        apply_config(dict(CONFIG, data_dir=Path(self.dir.name), mal_client_id="abcdef123456"))
        self.addCleanup(apply_config, CONFIG)
        database.init_db()
        anilist._stamps.clear()  # per data folder; tests swap folders
        self.mal = FakeMal()
        self.al = FakeAniList()
        for p in (mock.patch.object(mal, "urlopen", self.mal),
                  mock.patch.object(anilist, "anilist_request", self.al),
                  mock.patch.object(mal, "MIN_GAP", 0)):
            p.start()
            self.addCleanup(p.stop)

    def login(self, **extra):
        mal.store_token({"access_token": "good", "refresh_token": "r1", "expires_at": time.time() + 3600,
                         "user_id": 77, "user_name": "malfan", **extra})


class Connect(Base):
    def test_start_uses_pkce_plain_and_a_state(self):
        r = mal.auth_start()
        q = parse_qs(urlparse(r["url"]).query)
        self.assertTrue(r["url"].startswith(mal.AUTH_URL))
        self.assertEqual(q["code_challenge_method"], ["plain"])
        self.assertTrue(43 <= len(q["code_challenge"][0]) <= 128)
        self.assertEqual(q["client_id"], ["abcdef123456"])
        self.assertNotIn("redirect_uri", q)  # MAL uses the app's one registered address
        self.assertTrue(q["state"][0])

    def test_start_without_an_app_id(self):
        with mock.patch("animehub.config.DEFAULT_MAL_CLIENT_ID", None):
            apply_config(dict(CONFIG, data_dir=Path(self.dir.name)))
        self.assertIn("mal_client_id", mal.auth_start()["error"])

    def test_the_bundled_app_id_is_the_fallback(self):
        apply_config(dict(CONFIG, data_dir=Path(self.dir.name)))
        self.assertEqual(config.MAL_CLIENT_ID, "e84657a8feb4042cea3acd38968e21bd")

    def test_finish_trades_the_code_and_puts_mal_in_use(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        sync.outbox_enqueue(9, patch={"progress": 1})  # queued under the AniList account
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        r = mal.auth_finish("the-code", state)
        self.assertEqual(r["user"], "malfan")
        self.assertNotIn("discarded", r)  # AniList's queued change waits for AniList
        self.assertEqual(sync.outbox_count(), 1)
        tok = mal.load_token()
        self.assertEqual((tok["access_token"], tok["refresh_token"], tok["user_id"]), ("good", "r1", 77))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(config.MAL_TOKEN_FILE.stat().st_mode), 0o600)
        self.assertEqual(anilist.load_token()["user_name"], "alfan")  # kept, paused
        self.assertEqual(anilist.service(), "mal")
        self.assertEqual(anilist.account_id(), "mal:77")
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), "mal:77")
        # The code goes with the verifier from the start, and the client id.
        token_call = next(c for c in self.mal.calls if c[1] == mal.TOKEN_URL)
        self.assertEqual(token_call[2]["client_id"], ["abcdef123456"])
        self.assertEqual(len(token_call[2]["code_verifier"][0]), 128)

    def test_a_state_this_hub_never_started_is_foreign(self):
        r = mal.auth_finish("the-code", "nope")
        self.assertTrue(r.get("foreign"))
        self.assertIsNone(mal.load_token())
        self.assertFalse(any(c[1] == mal.TOKEN_URL for c in self.mal.calls))

    def test_a_state_is_used_once(self):
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        self.assertTrue(mal.auth_finish("the-code", state).get("ok"))
        self.assertTrue(mal.auth_finish("the-code", state).get("foreign"))

    def test_paste_of_the_landing_address(self):
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        r = mal.auth_paste(f"http://localhost:8787/auth/mal/callback?code=the-code&state={state}")
        self.assertTrue(r.get("ok"))
        self.assertIn("code=", mal.auth_paste("http://localhost:8787/auth/mal/callback")["error"])

    def test_a_refused_code(self):
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        self.assertIn("refused", mal.auth_finish("bad-code", state)["error"])
        self.assertIsNone(mal.load_token())

    def test_connecting_anilist_keeps_the_mal_login_paused(self):
        self.login()
        with mock.patch.object(anilist, "anilist_request",
                               return_value={"data": {"Viewer": {"id": 5, "name": "alfan"}}}):
            self.assertTrue(anilist.auth_finish("al-token").get("ok"))
        self.assertEqual(mal.load_token()["user_name"], "malfan")
        self.assertEqual(anilist.service(), "anilist")
        st = anilist.auth_status()
        self.assertEqual(st["user"], "alfan")
        self.assertEqual(st["accounts"]["mal"], {"connected": True, "user": "malfan", "user_id": 77})

    def test_another_mal_account_drops_only_mal_rows(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        sync.outbox_enqueue(9, patch={"progress": 1})  # AniList's
        self.login(user_id=12, user_name="someone")
        settings.set_setting(anilist.ACCOUNT_KEY, "mal")
        sync.outbox_enqueue(9, patch={"progress": 2})  # MAL account 12's
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        self.assertEqual(mal.auth_finish("the-code", state)["discarded"], 1)
        with database.db() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT account_id FROM outbox")], [5])

    def test_switching_between_two_logins(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        self.login()
        self.assertEqual(anilist.service(), "anilist")  # both kept, nothing picked: AniList
        config.ANILIST_CACHE.write_text(json.dumps({"user": {"name": "alfan"}, "entries": [], "_ts": time.time()}))
        with mock.patch.object(anilist, "refresh_user_data"):
            r = anilist.use_service("mal")
        self.assertEqual((r["ok"], r["service"], r["user"]), (True, "mal", "malfan"))
        self.assertEqual(anilist.account_id(), "mal:77")
        self.assertIsNone(anilist.anilist_cache_stale())  # AniList's lists never show as MAL's
        self.assertTrue(config.ANILIST_CACHE.exists())  # kept for switching back
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), "mal:77")
        # Queued AniList tracking that needs its lists waits, named, and
        # isn't sent to MAL (a plain edit goes to AniList: Decision 83).
        with database.db() as c:
            c.execute("INSERT INTO outbox(media_id,payload,account_id,created) VALUES(101,?,5,'x')",
                      (json.dumps({"patch": {"progress": 3}, "verify": {"add": False}, "account_id": 5}),))
        item = sync.outbox_items()[0]
        self.assertEqual((item["state"], item["held_for"]), ("account", "AniList"))
        self.assertEqual(sync.outbox_flush(), 0)
        self.assertFalse(any(c[0] == "PATCH" for c in self.mal.calls))
        with mock.patch.object(anilist, "refresh_user_data"):
            self.assertEqual(anilist.use_service("anilist")["service"], "anilist")
        self.assertEqual(anilist.account_id(), 5)

    def test_switching_needs_that_login(self):
        self.assertIn("error", anilist.use_service("mal"))
        self.assertIn("error", anilist.use_service("kitsu"))

    def test_a_cache_of_the_other_service_is_never_read(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        config.ANILIST_CACHE.write_text(json.dumps({"service": "mal", "user": {"name": "malfan"},
                                                    "entries": [], "_ts": time.time()}))
        self.assertIsNone(anilist.anilist_cache_stale())

    def test_disconnecting_the_one_in_use_hands_over(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        self.login()
        settings.set_setting(anilist.ACCOUNT_KEY, "mal")
        with mock.patch.object(anilist, "refresh_user_data"):
            anilist.auth_disconnect("mal")
        self.assertIsNone(mal.load_token())
        self.assertEqual(anilist.service(), "anilist")
        self.assertEqual(settings.get_setting(sync.RECONCILE_KEY), "5")
        anilist.auth_disconnect()
        self.assertIsNone(anilist.service())

    def test_status_and_disconnect(self):
        self.login()
        st = anilist.auth_status()
        self.assertEqual((st["connected"], st["service"], st["user"]), (True, "mal", "malfan"))
        self.assertNotIn("good", json.dumps(st))  # never the token
        self.assertTrue(st["mal"]["client_id_configured"])
        anilist.auth_disconnect()
        self.assertIsNone(mal.load_token())
        self.assertEqual(anilist.auth_status()["service"], None)
        self.assertFalse(anilist.anilist_connected())


class Refresh(Base):
    def test_a_run_out_token_is_refreshed_before_the_request(self):
        self.login(expires_at=time.time() - 10)
        self.mal.tokens = {"fresh": True}
        self.assertEqual(mal.api("GET", "/users/@me")["name"], "malfan")
        tok = mal.load_token()
        self.assertEqual((tok["access_token"], tok["refresh_token"]), ("fresh", "r2"))
        self.assertEqual(tok["user_name"], "malfan")

    def test_a_401_refreshes_once_and_retries(self):
        self.login()
        self.mal.tokens = {}  # MAL dropped "good" early
        self.assertEqual(mal.api("GET", "/users/@me")["name"], "malfan")
        self.assertEqual(mal.load_token()["access_token"], "fresh")

    def test_a_refused_refresh_ends_the_login(self):
        self.login()
        self.mal.tokens, self.mal.refresh_ok = {}, False
        with self.assertRaises(anilist.AniListAuthError):
            mal.api("GET", "/users/@me")
        self.assertIsNone(mal.load_token())
        self.assertFalse(anilist.anilist_connected())

    def test_mal_down_is_transient_and_named(self):
        self.login()
        self.mal.fail = 503
        with self.assertRaises(anilist.AniListTransientError) as cm:
            mal.api("GET", "/users/@me")
        self.assertIn("MyAnimeList", anilist.friendly_error(cm.exception))


class Lists(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.mal.list = [
            {"node": {"id": 1, "title": "One"}, "list_status": {
                "status": "completed", "score": 8, "num_episodes_watched": 12, "is_rewatching": False,
                "updated_at": "2026-01-02T03:04:05+00:00", "start_date": "2025-12-01", "finish_date": "2026-01-02",
                "num_times_rewatched": 1}},
            {"node": {"id": 2, "title": "Two"}, "list_status": {"status": "watching", "score": 0, "num_episodes_watched": 4}},
            {"node": {"id": 3, "title": "Three"}, "list_status": {"status": "completed", "is_rewatching": True,
                                                                 "score": 9, "num_episodes_watched": 2}},
            {"node": {"id": 4, "title": "Not on AniList"}, "list_status": {"status": "plan_to_watch"}},
        ]

    def test_entries_come_keyed_by_anilist_id(self):
        d = anilist.anilist_user_data(force=True)
        self.assertEqual(d["service"], "mal")
        by = {e["id"]: e for e in d["entries"]}
        self.assertEqual(set(by), {101, 102, 103})
        one = by[101]
        self.assertEqual((one["status"], one["score"], one["score100"], one["progress"]), ("COMPLETED", 8, 80, 12))
        self.assertEqual((one["started_at"], one["completed_at"], one["repeat"]), ("2025-12-01", "2026-01-02", 1))
        self.assertEqual(one["updated"], 1767323045)
        self.assertEqual((one["title"], one["mal_id"], one["poster"]), ("One", 1, "p.jpg"))
        self.assertEqual(by[102]["next_episode"], {"number": 5, "in_seconds": 3600, "at": 2000000000})
        self.assertEqual(by[103]["status"], "REPEATING")
        self.assertEqual(d["unmatched"], [{"mal_id": 4, "title": "Not on AniList", "status": "PLANNING"}])
        self.assertEqual(d["favourites"], [])
        self.assertEqual(d["score_format"], "POINT_10")
        u = d["user"]
        self.assertEqual((u["name"], u["episodes_watched"], u["minutes_watched"], u["count"]),
                         ("malfan", 1234, 29520, 99))
        # The spine reads the same cache the AniList account fills.
        self.assertEqual(anilist.anilist_cache_read()["service"], "mal")

    def test_paging_follows_next(self):
        self.mal.list = self.mal.list * 300  # 1200 rows: two pages of 1000
        anilist.anilist_user_data(force=True)
        pages = [c for c in self.mal.calls if "/animelist" in c[1]]
        self.assertEqual(len(pages), 2)

    def test_the_catalog_is_kept_finished_shows_for_days(self):
        anilist.anilist_user_data(force=True)
        self.assertEqual(self.al.catalog_calls, 1)
        anilist.anilist_user_data(force=True)
        self.assertEqual(self.al.catalog_calls, 1)  # all fresh
        with database.db() as c:  # 11 minutes on: only the airing one is asked again
            c.execute("UPDATE catalog_cache SET ts = ts - 660 WHERE key LIKE 'malcat:%'")
        calls = []
        real = self.al.__call__

        def spy(payload, token=None):
            calls.append((payload.get("variables") or {}).get("ids"))
            return real(payload, token)
        with mock.patch.object(anilist, "anilist_request", spy):
            anilist.anilist_user_data(force=True)
        self.assertEqual(calls, [[2]])

    def test_anilist_down_uses_the_last_catalog(self):
        anilist.anilist_user_data(force=True)
        with database.db() as c:
            c.execute("UPDATE catalog_cache SET ts = ts - 999999 WHERE key LIKE 'malcat:%'")
        self.al.down = True
        d = anilist.anilist_user_data(force=True)
        self.assertEqual(len(d["entries"]), 3)

    def test_anilist_down_with_nothing_kept_keeps_the_old_lists(self):
        self.al.down = True
        with self.assertRaises(Exception):
            anilist.anilist_user_data(force=True)

    def test_progress_is_reported_while_reading(self):
        steps = []
        mal.user_data(lambda step, done=None, total=None: steps.append((step, done, total)))
        self.assertEqual(steps[0], ("list", 0, 99))  # MAL's own count of the list
        self.assertIn(("match", 0, 4), steps)

    def test_lists_status_carries_the_stamp_and_progress(self):
        before = anilist.lists_status()
        self.assertEqual((before["service"], before["stamp"]), ("mal", None))
        seen = []
        real = mal.catalog

        def spy(ids, report=None):
            seen.append(anilist.lists_status().get("loading"))
            return real(ids, report)
        with mock.patch.object(mal, "catalog", spy):
            anilist.anilist_user_data(force=True)
        self.assertEqual(seen[0]["step"], "list")
        after = anilist.lists_status()
        self.assertNotIn("loading", after)
        self.assertGreater(after["stamp"], 0)

    def test_two_readers_share_one_read(self):
        started, go = threading.Event(), threading.Event()
        real = mal.user_data
        calls = []

        def slow(report=None):
            calls.append(1)
            started.set()
            go.wait(5)
            return real(report)
        with mock.patch.object(mal, "user_data", slow):
            t = threading.Thread(target=anilist.anilist_user_data, kwargs={"force": True})
            t.start()
            started.wait(5)
            out = []
            t2 = threading.Thread(target=lambda: out.append(anilist.anilist_user_data(force=True)))
            t2.start()
            go.set()
            t.join(5)
            t2.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(out[0]["entries"]), 3)

    def test_each_service_keeps_its_own_lists(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        settings.set_setting(anilist.ACCOUNT_KEY, "mal")
        anilist.anilist_user_data(force=True)
        config.ANILIST_CACHE.write_text(json.dumps({"user": {"name": "alfan"}, "entries": [{"id": 1}],
                                                    "_ts": time.time()}))
        self.assertEqual(anilist.anilist_cache_stale()["service"], "mal")
        with mock.patch.object(anilist, "refresh_user_data"):
            anilist.use_service("anilist")
        self.assertEqual(anilist.anilist_cache_stale()["entries"], [{"id": 1}])  # at once, no read
        with mock.patch.object(anilist, "refresh_user_data"):
            anilist.use_service("mal")
        self.assertEqual(len(anilist.anilist_cache_stale()["entries"]), 3)

    def test_recommendations_and_calendar_count_a_mal_account(self):
        anilist.anilist_user_data(force=True)
        self.assertEqual(anilist.account_name(), "malfan")


class Edits(Base):
    def setUp(self):
        super().setUp()
        self.login()
        self.mal.status[1] = {"status": "watching", "score": 6, "num_episodes_watched": 3}

    def test_status_and_form_mapping(self):
        f = mal.save_form({"status": "REPEATING", "score": 7.6, "progress": 5, "repeat": 2,
                           "started_at": "2026-01-01", "completed_at": None})
        self.assertEqual(f, {"status": "completed", "is_rewatching": "true", "score": "8",
                             "num_watched_episodes": "5", "num_times_rewatched": "2",
                             "start_date": "2026-01-01", "finish_date": ""})
        self.assertEqual(mal.save_form({"status": "PAUSED"}), {"status": "on_hold", "is_rewatching": "false"})
        self.assertEqual(mal.save_form({"score": 0})["score"], "0")
        with self.assertRaises(ValueError):
            mal.save_form({"status": "NOPE"})
        for s in ("CURRENT", "PLANNING", "COMPLETED", "DROPPED", "PAUSED"):
            self.assertEqual(mal.status_in({"status": mal.STATUS_TO_MAL[s]}), s)

    def test_entry_state_reads_mal_and_anilist_extras(self):
        st = anilist.anilist_entry_state(101)
        self.assertEqual(st["service"], "mal")
        self.assertEqual(st["entry"]["status"], "CURRENT")
        self.assertEqual((st["entry"]["score"], st["entry"]["progress"]), (6, 3))
        self.assertEqual(st["studios"], ["Studio"])
        self.assertEqual((st["score_format"], st["score_max"], st["is_favourite"]), ("POINT_10", 10, None))

    def test_save_patches_only_changed_fields(self):
        st = anilist.anilist_entry_save(101, {"progress": 4})
        patch_call = next(c for c in self.mal.calls if c[0] == "PATCH")
        self.assertTrue(patch_call[1].endswith("/anime/1/my_list_status"))
        self.assertEqual(patch_call[2], {"num_watched_episodes": ["4"]})
        self.assertEqual(st["entry"]["progress"], 4)

    def test_a_show_without_a_mal_id_is_refused(self):
        CATALOG[9] = media(109, None, "Only on AniList")
        self.addCleanup(CATALOG.pop, 9)
        with self.assertRaises(ValueError):
            anilist.anilist_entry_save(109, {"progress": 1})
        self.assertFalse(any(c[0] == "PATCH" for c in self.mal.calls))

    def test_favourites_are_not_offered(self):
        self.assertIn("error", anilist.anilist_favourite_set(101, True))

    def test_the_outbox_sends_through_mal(self):
        with database.db() as c:
            c.execute("DELETE FROM outbox")
        sync.outbox_enqueue(101, patch={"status": "COMPLETED", "progress": 12})
        with database.db() as c:
            self.assertEqual(c.execute("SELECT account_id FROM outbox").fetchone()[0], "mal:77")
        self.mal.fail = 503
        self.assertEqual(sync.outbox_flush(), 0)
        self.assertEqual(sync.outbox_count(), 1)  # MAL down: kept
        self.mal.fail = None
        sync.outbox_retry_now()
        self.assertEqual(sync.outbox_flush(), 1)
        self.assertEqual(self.mal.status[1]["status"], "completed")
        self.assertEqual(self.mal.status[1]["num_episodes_watched"], 12)


class EditorPick(Base):
    """Decision 83: the entry editor reads and saves on the service it picks,
    while the lists stay on the one in use."""

    def setUp(self):
        super().setUp()
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        self.login()
        self.assertEqual(anilist.service(), "anilist")  # AniList in use, MAL paused
        self.mal.status[1] = {"status": "watching", "score": 6, "num_episodes_watched": 3}

    def test_using_picks_a_logged_in_service_only(self):
        with anilist.using("mal"):
            self.assertEqual((anilist.service(), anilist.account_id()), ("mal", "mal:77"))
            self.assertTrue(anilist.picked())
        self.assertEqual(anilist.service(), "anilist")
        mal.clear_token()
        with anilist.using("mal"):
            self.assertEqual(anilist.service(), "anilist")  # no login: the one in use
        with anilist.using(None):
            self.assertFalse(anilist.picked())

    def test_read_and_save_on_the_paused_service(self):
        with anilist.using("mal"):
            st = anilist.anilist_entry_state(101)
            self.assertEqual((st["service"], st["entry"]["progress"]), ("mal", 3))
            anilist.anilist_entry_save(101, {"progress": 4})
        self.assertEqual(self.mal.status[1]["num_episodes_watched"], 4)

    def test_each_service_keeps_its_own_stored_read(self):
        al = {"media_id": 101, "entry": {"status": "COMPLETED"}}
        database.cache_put("entry-state:v1:101", al)
        with anilist.using("mal"):
            self.assertEqual(anilist.anilist_entry_state(101)["service"], "mal")
        self.assertEqual(database.cache_get("entry-state:v1:101", ttl=30), al)
        self.assertEqual(database.cache_get("entry-state:v1:mal:101", ttl=30)["service"], "mal")
        # A MAL read stored under the shared key (before Decision 83) isn't AniList's.
        database.cache_put("entry-state:v1:102", {"service": "mal", "entry": {}})
        with mock.patch.object(anilist, "anilist_mutation", return_value=None):
            self.assertIsNone(anilist.anilist_entry_state(102))

    def test_a_queued_edit_goes_to_the_paused_service(self):
        with anilist.using("mal"):
            sync.outbox_enqueue(101, patch={"progress": 7})
            self.assertEqual(sync.outbox_pending_for(101), 1)
        self.assertEqual(sync.outbox_pending_for(101), 0)  # not AniList's
        self.assertNotIn("held_for", sync.outbox_items()[0])
        with mock.patch.object(anilist, "auth_status", return_value={"connected": True}):
            self.assertEqual(sync.outbox_flush(), 1)
        self.assertEqual(self.mal.status[1]["num_episodes_watched"], 7)
        self.assertEqual(sync.outbox_count(), 0)

    def test_the_paused_service_down_backs_off_alone(self):
        with anilist.using("mal"):
            sync.outbox_enqueue(101, patch={"progress": 7})
        self.mal.fail = 503
        with mock.patch.object(anilist, "auth_status", return_value={"connected": True}):
            self.assertEqual(sync.outbox_flush(), 0)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT attempts FROM outbox").fetchone()[0], 1)


class Routes(Base):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def req(self, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_address[1]}{path}",
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Host": "127.0.0.1:8787", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_callback_finishes_a_started_connection(self):
        state = parse_qs(urlparse(mal.auth_start()["url"]).query)["state"][0]
        code, page = self.req(f"/auth/mal/callback?code=the-code&state={state}")
        self.assertEqual(code, 200)
        self.assertIn(b"MyAnimeList connected as <b>malfan</b>", page)
        self.assertEqual(anilist.service(), "mal")

    def test_callback_of_another_hub_keeps_the_address(self):
        code, page = self.req("/auth/mal/callback?code=x&state=y")
        self.assertIn(b"paste it there", page)
        self.assertNotIn(b"replaceState", page)

    def test_callback_escapes_what_mal_says(self):
        code, page = self.req("/auth/mal/callback?error=%3Cscript%3E")
        self.assertNotIn(b"<script>alert", page)
        self.assertIn(b"&lt;script&gt;", page)

    def test_use_and_logout_routes(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        self.login()
        with mock.patch.object(anilist, "refresh_user_data"):
            code, body = self.req("/api/auth/use", {"service": "mal"})
            self.assertEqual((code, json.loads(body)["service"]), (200, "mal"))
            self.assertEqual(self.req("/api/auth/use", {"service": "x"})[0], 400)
            self.assertEqual(self.req("/api/auth/logout", {"service": "x"})[0], 400)
            self.assertEqual(self.req("/api/auth/logout", {"service": "anilist"})[0], 200)
        self.assertIsNone(anilist.load_token())
        self.assertEqual(anilist.service(), "mal")

    def test_start_and_paste_routes(self):
        code, body = self.req("/api/auth/mal/start", {})
        self.assertEqual(code, 200)
        self.assertIn("myanimelist.net", json.loads(body)["url"])
        code, body = self.req("/api/auth/mal/paste", {"url": "nothing"})
        self.assertEqual(code, 400)

    def test_the_editor_picks_its_service(self):
        anilist.store_token("al-token", None, {"id": 5, "name": "alfan"})
        self.login()
        self.mal.status[1] = {"status": "watching", "score": 6, "num_episodes_watched": 3}
        code, body = self.req("/api/entry?id=101&service=mal")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["state"]["service"], "mal")
        code, body = self.req("/api/entry", {"media_id": 101, "service": "mal", "patch": {"score": 9}})
        self.assertEqual((code, json.loads(body)["entry"]["score"]), (200, 9))
        self.assertEqual(self.mal.status[1]["score"], 9)
        self.assertEqual(anilist.service(), "anilist")  # the lists stay where they were
        self.assertEqual(self.req("/api/entry?id=101&service=kitsu")[0], 400)
        self.assertEqual(self.req("/api/entry", {"media_id": 101, "service": "x", "patch": {"score": 1}})[0], 400)

    def test_a_phone_cannot_change_the_account(self):
        for path in ("/api/auth/mal/start", "/api/auth/mal/paste", "/auth/mal/callback", "/api/auth/use"):
            self.assertIn(path, remote.BLOCKED)


if __name__ == "__main__":
    unittest.main()
