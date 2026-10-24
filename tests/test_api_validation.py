"""Localhost API protection (TODO 22, READINESS B2 probes): bound, typed
request bodies with structured errors — never silent empty-string writes,
no content-type hole, no garbage-header tracebacks."""
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, player, server, streaming
from animehub.config import config


class ApiValidationTests(unittest.TestCase):
    PORT = 8787  # hub's configured port; the server binds an ephemeral one

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.db = Path(cls.directory.name) / "hub.db"
        cls.patches = [
            patch.object(config, "DB", cls.db),
            patch.object(config, "LIBRARY", None),
            patch.object(player, "SKIP_CREDITS_LUA", Path(cls.directory.name) / "absent.lua"),
            patch.object(streaming, "SKIP_CREDITS_LUA", Path(cls.directory.name) / "absent.lua"),
            patch.object(player, "watch_process", lambda *a, **k: None),
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

    def post(self, path, body=b"", ctype="application/json", host=None):
        """A probe POST; returns (status, parsed-or-raw)."""
        url = f"http://127.0.0.1:{self.bound}{path}"
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": ctype,
                                              "Host": host or f"127.0.0.1:{self.PORT}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return e.code, raw

    def episode_rows(self):
        with sqlite3.connect(str(self.db)) as c:
            return c.execute("SELECT id, watched FROM episodes").fetchall()

    # --- the two reproduced probes -------------------------------------

    def test_text_plain_with_json_note_is_rejected(self):
        """The cross-site simple-POST trick: a foreign-origin form can send
        text/plain; note=application/json. The media type is parsed, not
        substring-matched."""
        status, payload = self.post(
            "/api/watched", b'{"id":"x","watched":true}',
            ctype="text/plain; note=application/json")
        self.assertEqual(status, 415)
        self.assertIn("error", payload)
        self.assertEqual(self.episode_rows(), [])

    def test_malformed_json_is_a_structured_400(self):
        """Malformed JSON used to become body={}, whose empty episode id
        was then written into the DB."""
        status, payload = self.post("/api/watched", b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON", payload["error"])
        self.assertEqual(self.episode_rows(), [])

    # --- bounds and typing ----------------------------------------------

    def test_garbage_content_length_is_a_400_not_a_traceback(self):
        url = f"http://127.0.0.1:{self.bound}/api/watched"
        req = urllib.request.Request(url, data=b'{"id":"x","watched":true}',
                                     method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Host": f"127.0.0.1:{self.PORT}",
                                              "Content-Length": "abc"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 400)

    def test_oversized_body_is_413(self):
        """The Content-Length header is bounded before the body is read —
        a declared-but-not-sent oversized body gets a clean 413."""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.bound, timeout=5)
        conn.putrequest("POST", "/api/watched", skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{self.PORT}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(1_100_000))
        conn.endheaders()
        conn.send(b'{"id":"x"}')
        res = conn.getresponse()
        self.assertEqual(res.status, 413)
        self.assertIn("large", json.loads(res.read())["error"])
        conn.close()

    def test_non_object_body_is_400(self):
        status, payload = self.post("/api/refresh", b"[1,2,3]")
        self.assertEqual(status, 400)
        self.assertIn("JSON object", payload["error"])

    def test_empty_and_non_string_ids_are_rejected(self):
        for bad in ('{"id":"","watched":true}', '{"id":5,"watched":true}'):
            with self.subTest(bad=bad):
                status, payload = self.post("/api/watched", bad.encode())
                self.assertEqual(status, 400)
                self.assertIn("id", payload["error"])
        self.assertEqual(self.episode_rows(), [])

    def test_watched_must_be_a_real_boolean(self):
        status, payload = self.post("/api/watched", b'{"id":"x","watched":"yes"}')
        self.assertEqual(status, 400)
        self.assertIn("boolean", payload["error"])

    def test_match_flags_must_be_real_booleans(self):
        """TODO 12: keep-local's `keep` and the suggestion refresh's `fresh`."""
        for route, body in (("/api/keep-local", b'{"show_id":"x","keep":"yes"}'),
                            ("/api/match-candidates", b'{"query":"Frieren","fresh":1}')):
            with self.subTest(route=route):
                status, payload = self.post(route, body)
                self.assertEqual(status, 400)
                self.assertIn("boolean", payload["error"])

    def test_match_query_must_be_text(self):
        status, payload = self.post("/api/match", b'{"show_id":"x","query":"  "}')
        self.assertEqual(status, 400)
        self.assertIn("query", payload["error"])

    def test_play_version_must_be_an_integer(self):
        status, payload = self.post("/api/play", b'{"id":"x","version":"2"}')
        self.assertEqual(status, 400)
        self.assertIn("integer", payload["error"])

    def test_reset_with_empty_id_is_rejected(self):
        status, payload = self.post("/api/reset", b'{"id":""}')
        self.assertEqual(status, 400)

    def test_foreign_host_is_403(self):
        status, _ = self.post("/api/refresh", b"{}", host="evil.example:8787")
        self.assertEqual(status, 403)

    # --- the happy path still works -------------------------------------

    def test_valid_watched_post_writes_and_unmarks(self):
        status, payload = self.post("/api/watched", b'{"id":"ep1","watched":true}')
        self.assertEqual(status, 200)
        self.assertEqual(self.episode_rows(), [("ep1", 1)])
        status, _ = self.post("/api/watched", b'{"id":"ep1","watched":false}')
        self.assertEqual(status, 200)
        self.assertEqual(self.episode_rows(), [("ep1", 0)])


    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.bound}{path}", headers={"Host": f"127.0.0.1:{self.PORT}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.headers.get("Content-Type"), res.read()
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None, b""

    def test_fonts_are_served_from_the_app(self):
        # TODO 60: the UI fonts ship in web/fonts; only .woff2 basenames, no path walking.
        status, ctype, body = self.get("/fonts/hanken-grotesk-latin.woff2")
        self.assertEqual((status, ctype), (200, "font/woff2"))
        self.assertEqual(body[:4], b"wOF2")
        self.assertEqual(self.get("/fonts/OFL-hankengrotesk.txt")[0], 404)
        self.assertEqual(self.get("/fonts/../app.js")[0], 404)
        self.assertEqual(self.get("/fonts/%2e%2e%2fapp.woff2")[0], 404)


if __name__ == "__main__":
    unittest.main()