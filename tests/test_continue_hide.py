"""Taking a show off Home's Continue watching (Decision 81): the hub keeps
when; the page hides it until the show plays again (tests/home_hero.cjs)."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support  # noqa: F401  (throwaway dirs, before animehub)
from animehub import database, library, server
from animehub.config import config


def hidden():
    with database.db() as c:
        return dict(c.execute("SELECT show_id, at FROM continue_hidden").fetchall())


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = mock.patch.object(config, "PORT", cls.srv.server_port)  # the Host check
        cls.port.start()

    @classmethod
    def tearDownClass(cls):
        cls.port.stop()
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/api/continue-hide",
                                     json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_hide_then_undo(self):
        self.assertEqual(self.post({"show_id": "show-a"}), (200, {"ok": True}))
        at = hidden()["show-a"]
        self.assertRegex(at, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")  # last_played's own form, so they compare
        self.assertEqual(self.post({"show_id": "show-a", "hide": False}), (200, {"ok": True}))
        self.assertNotIn("show-a", hidden())

    def test_bad_bodies(self):
        self.assertEqual(self.post({})[0], 400)
        self.assertEqual(self.post({"show_id": "x", "hide": "yes"})[0], 400)

    def test_library_payload_carries_it(self):
        library.hide_continue("show-b")
        with mock.patch.object(config, "LIBRARY", None):
            self.assertIn("show-b", library.full_library()["continue_hidden"])
