"""AniList OAuth + the single write chokepoint (TODO 19, Decisions 17.1+20).

The token is owner-only, atomically stored outside the repo, and never
appears in an API payload, an error message or a log line. Every AniList
mutation must go through anilist_mutation() — the one function the v0.2
offline queue will wrap. The OAuth callback only accepts tokens while a
started flow is fresh (login correlation); manual paste is the fallback.
"""
import json
import io
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, player, server, settings, streaming
from animehub.config import config, DEFAULT_ANILIST_CLIENT_ID, apply_config


def httpd_json(handler_probe):
    return handler_probe


class AuthHTTPBase(unittest.TestCase):
    """A real hub server on an ephemeral port with a sandboxed DATA dir."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls.directory.name)
        cls.patches = [
            patch.object(config, "DB", cls.tmp / "hub.db"),
            patch.object(config, "LIBRARY", None),
            patch.object(config, "ANILIST_TOKEN_FILE", cls.tmp / "anilist_token.json"),
            patch.object(config, "ANILIST_CLIENT_ID", 9876),
            patch.object(settings, "CONFIG", {}),
            patch.object(player, "SKIP_CREDITS_LUA", cls.tmp / "absent.lua"),
            patch.object(streaming, "SKIP_CREDITS_LUA", cls.tmp / "absent.lua"),
        ]
        for p in cls.patches:
            p.start()
        database.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.bound = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in reversed(cls.patches):
            p.stop()
        cls.directory.cleanup()

    def post(self, path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}{path}",
            data=json.dumps(body or {}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return e.code, raw

    def get(self, path):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bound}{path}",
            headers={"Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


class TokenStoreTests(AuthHTTPBase):
    def setUp(self):
        anilist.clear_token()

    def tearDown(self):
        anilist.clear_token()

    def test_store_is_owner_only_and_roundtrips(self):
        anilist.store_token("sekrit", 3600, {"id": 7, "name": "Bob"})
        mode = stat.S_IMODE(os.stat(config.ANILIST_TOKEN_FILE).st_mode)
        self.assertEqual(mode, 0o600)
        tok = anilist.load_token()
        self.assertEqual(tok["access_token"], "sekrit")
        self.assertEqual(tok["user_name"], "Bob")
        self.assertGreater(tok["expires_at"], 0)

    def test_corrupt_file_counts_as_absent(self):
        config.ANILIST_TOKEN_FILE.write_text("{not json")
        self.assertIsNone(anilist.load_token())
        config.ANILIST_TOKEN_FILE.unlink()

    def test_status_never_contains_the_token(self):
        anilist.store_token("sekrit-token-value", 3600, {"id": 7, "name": "Bob"})
        code, raw = self.get("/api/auth/status")
        self.assertEqual(code, 200)
        self.assertNotIn("sekrit-token-value", raw.decode())
        body = json.loads(raw)
        self.assertTrue(body["connected"])
        self.assertEqual(body["user"], "Bob")

    def test_expired_token_reports_disconnected(self):
        anilist.store_token("sekrit", -10, {"id": 7, "name": "Bob"})  # already expired
        body = json.loads(self.get("/api/auth/status")[1])
        self.assertFalse(body["connected"])
        self.assertTrue(body["expired"])

    def test_bundled_client_id_is_the_fallback(self):
        """A fresh install configures nothing and still connects in two
        clicks: apply_config falls back to the bundled public client id.
        Every global apply_config touches is saved and restored."""
        names = ("DATA", "CACHE", "POSTERS", "EPIMGS", "DB", "LIBRARY", "PORT",
                 "ANILIST_CACHE", "WINDOW",
                 "ANILIST_TOKEN_FILE", "ANILIST_CLIENT_ID")
        saved = {n: getattr(config, n) for n in names}
        try:
            apply_config({"library_root": None, "port": 8787,
                              "data_dir": self.tmp, "window": "app"})
            self.assertEqual(config.ANILIST_CLIENT_ID, DEFAULT_ANILIST_CLIENT_ID)
            self.assertEqual(DEFAULT_ANILIST_CLIENT_ID, 53857)
        finally:
            for n, v in saved.items():
                setattr(config, n, v)

    def test_logout_removes_the_token(self):
        anilist.store_token("sekrit", 3600, {"id": 7, "name": "Bob"})
        code, body = self.post("/api/auth/logout")
        self.assertEqual(code, 200)
        self.assertFalse(anilist.load_token())
        self.assertFalse(json.loads(self.get("/api/auth/status")[1])["connected"])


class MutationChokepointTests(AuthHTTPBase):
    """Every AniList write goes through anilist_mutation — and it refuses
    to send anything without a usable token."""

    def setUp(self):
        anilist.clear_token()

    def tearDown(self):
        anilist.clear_token()

    def test_refuses_without_a_token_and_never_touches_the_transport(self):
        with patch.object(anilist, "anilist_request") as req:
            with self.assertRaises(anilist.AniListAuthError) as cm:
                anilist.anilist_mutation("mutation{DoThing{id}}")
            self.assertEqual(cm.exception.reason, "not connected")
            req.assert_not_called()

    def test_refuses_an_expired_token_before_the_transport(self):
        anilist.store_token("old", -10, {"id": 7, "name": "Bob"})
        with patch.object(anilist, "anilist_request") as req:
            with self.assertRaises(anilist.AniListAuthError) as cm:
                anilist.anilist_mutation("mutation{DoThing{id}}")
            self.assertIn("expired", cm.exception.reason)
            req.assert_not_called()

    def test_sends_the_token_with_the_operation(self):
        anilist.store_token("good-token", 3600, {"id": 7, "name": "Bob"})
        with patch.object(anilist, "anilist_request", return_value={"data": {"ok": 1}}) as req:
            out = anilist.anilist_mutation("mutation($id:Int){DoThing(id:$id){id}}", {"id": 5})
        self.assertEqual(out, {"ok": 1})
        args, kwargs = req.call_args
        self.assertEqual(kwargs.get("token"), "good-token")
        self.assertEqual(args[0]["variables"], {"id": 5})

    def test_graphql_errors_surface_without_the_token(self):
        anilist.store_token("good-token", 3600, {"id": 7, "name": "Bob"})
        with patch.object(anilist, "anilist_request",
                          return_value={"errors": [{"message": "Nested fragments may not be cycles"}]}):
            with self.assertRaises(ValueError) as cm:
                anilist.anilist_mutation("mutation{DoThing{id}}")
        self.assertNotIn("good-token", str(cm.exception))

    def test_a_rejected_token_is_removed_not_kept(self):
        anilist.store_token("dead-token", 3600, {"id": 7, "name": "Bob"})
        with patch.object(anilist, "anilist_request",
                          return_value={"errors": [{"message": "Invalid token"}]}):
            with self.assertRaises(anilist.AniListAuthError) as cm:
                anilist.anilist_mutation("mutation{DoThing{id}}")
        self.assertIn("rejected", cm.exception.reason)
        self.assertIsNone(anilist.load_token())


class AuthFlowTests(AuthHTTPBase):
    """The HTTP flows: start → callback, manual paste, failure shapes."""

    def setUp(self):
        anilist.clear_token()
        anilist._AUTH_PENDING_TS = 0.0

    def tearDown(self):
        anilist.clear_token()
        anilist._AUTH_PENDING_TS = 0.0

    def test_status_of_a_fresh_hub(self):
        code, body = json.loads(self.get("/api/auth/status")[1]), None
        self.assertEqual(code["connected"], False)
        self.assertTrue(code["client_id_configured"])
        self.assertIn("/auth/callback", code["redirect_uri"])

    def test_start_builds_the_authorize_url_and_opens_correlation(self):
        code, body = self.post("/api/auth/start")
        self.assertEqual(code, 200)
        self.assertTrue(body["url"].startswith("https://anilist.co/api/v2/oauth/authorize"))
        self.assertIn("client_id=9876", body["url"])
        self.assertIn("response_type=token", body["url"])
        # No redirect_uri parameter: AniList's implicit grant takes the
        # redirect from the registered client settings, and passing one
        # anyway earns unsupported_grant_type (found live 2026-10-15).
        self.assertNotIn("redirect_uri", body["url"])

    def test_callback_without_a_started_flow_is_refused(self):
        code, body = self.post("/api/auth/callback", {"access_token": "tok", "expires_in": 100})
        self.assertEqual(code, 400)
        self.assertIn("No AniList connection attempt", body["error"])
        self.assertIsNone(anilist.load_token())

    def test_stale_callback_is_refused(self):
        anilist._AUTH_PENDING_TS = 1  # 1970 — long stale
        code, body = self.post("/api/auth/callback", {"access_token": "tok"})
        self.assertEqual(code, 400)
        self.assertIsNone(anilist.load_token())

    def test_callback_validates_via_viewer_then_stores(self):
        with patch.object(anilist, "anilist_request",
                          return_value={"data": {"Viewer": {"id": 42, "name": "Elias"}}}) as req:
            self.post("/api/auth/start")
            code, body = self.post("/api/auth/callback",
                                   {"access_token": "web-token", "expires_in": 3600})
        self.assertEqual(code, 200)
        self.assertEqual(body["user"], "Elias")
        req.assert_called_once()
        self.assertEqual(req.call_args.kwargs.get("token"), "web-token")
        tok = anilist.load_token()
        self.assertEqual(tok["access_token"], "web-token")
        self.assertEqual(tok["user_id"], 42)
        # Reads follow the authenticated account (Decision 31).
        self.assertEqual(anilist.load_anilist_username(), "Elias")
        # And the token is never reflected back by any endpoint.
        self.assertNotIn("web-token", self.get("/api/auth/status")[1].decode())

    def test_callback_remembered_once(self):
        """The pending flow is consumed: replaying the callback POST can't
        store a second (attacker- or retry-supplied) token."""
        with patch.object(anilist, "anilist_request",
                          return_value={"data": {"Viewer": {"id": 42, "name": "Elias"}}}):
            self.post("/api/auth/start")
            self.post("/api/auth/callback", {"access_token": "first", "expires_in": 100})
            anilist.clear_token()
            code, body = self.post("/api/auth/callback", {"access_token": "second"})
        self.assertEqual(code, 400)
        self.assertIsNone(anilist.load_token())

    def test_manual_paste_needs_no_pending_flow(self):
        with patch.object(anilist, "anilist_request",
                          return_value={"data": {"Viewer": {"id": 9, "name": "Pasted"}}}):
            code, body = self.post("/api/auth/token", {"token": "paste-token"})
        self.assertEqual(code, 200)
        self.assertEqual(body["user"], "Pasted")
        self.assertEqual(anilist.load_token()["access_token"], "paste-token")

    def test_paste_of_a_token_anilist_rejects(self):
        """Real AniList shape: HTTP 400 carrying GraphQL errors."""
        def reject(payload, token=None):
            raise urllib.error.HTTPError(
                "https://graphql.anilist.co", 400, "Bad Request", {},
                io.BytesIO(b'{"errors":[{"message":"Invalid token"}]}'))
        with patch.object(anilist, "anilist_request", side_effect=reject):
            code, body = self.post("/api/auth/token", {"token": "junk"})
        self.assertEqual(code, 400)
        self.assertIn("rejected", body["error"])
        self.assertNotIn("junk", json.dumps(body))
        self.assertIsNone(anilist.load_token())

    def test_paste_when_anilist_is_unreachable(self):
        with patch.object(anilist, "anilist_request",
                          side_effect=OSError("connection refused")):
            code, body = self.post("/api/auth/token", {"token": "tok"})
        self.assertEqual(code, 400)
        self.assertIn("reach AniList", body["error"])
        self.assertIsNone(anilist.load_token())

    def test_callback_page_is_served_and_strips_the_fragment_itself(self):
        code, raw = self.get("/auth/callback")
        self.assertEqual(code, 200)
        page = raw.decode()
        self.assertIn("history.replaceState", page)
        self.assertIn("/api/auth/callback", page)

    def test_leftover_config_username_cannot_pin_the_account(self):
        """Decision 31: the token IS the account — a leftover config key
        never overrides who the reads follow."""
        with patch.object(anilist, "anilist_request",
                          return_value={"data": {"Viewer": {"id": 1, "name": "Other"}}}):
            with patch.object(settings, "CONFIG", {"anilist_username": "TestUser"}):
                self.post("/api/auth/token", {"token": "tok"})
                self.assertEqual(anilist.load_anilist_username(), "Other")
        self.assertEqual(json.loads(self.get("/api/auth/status")[1])["user"], "Other")


if __name__ == "__main__":
    unittest.main()
