"""Playlists (Decision 64): storage, order, the API and the library payload."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support  # noqa: F401  (throwaway dirs, before animehub)
from animehub import database, library, playlists, server
from animehub.config import config


def show(ref="aid:1", title="Frieren", poster="p.jpg"):
    return {"ref": ref, "title": title, "poster": poster}


class Storage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        for p in playlists.all_playlists():
            playlists.delete(p["id"])

    def test_create_with_a_first_show(self):
        pid = playlists.create("  Winter   nights ", show())
        self.assertEqual(playlists.all_playlists(), [{"id": pid, "name": "Winter nights", "color": None, "note": "",
                                                      "icon": "", "pinned": False, "items": [show()]}])

    def test_a_bad_first_show_makes_no_playlist(self):
        with self.assertRaises(playlists.PlaylistError):
            playlists.create("x", {"ref": "nonsense", "title": "t"})
        self.assertEqual(playlists.all_playlists(), [])

    def test_a_name_is_needed(self):
        for bad in ("", "   ", None, 3, "x" * 61):
            with self.assertRaises(playlists.PlaylistError):
                playlists.create(bad)

    def test_a_show_is_on_a_list_once(self):
        pid = playlists.create("a")
        playlists.add(pid, show())
        playlists.add(pid, show("sid:abc", "Other"))
        playlists.add(pid, show(title="Changed"))
        items = playlists.all_playlists()[0]["items"]
        self.assertEqual([x["ref"] for x in items], ["aid:1", "sid:abc"])
        self.assertEqual(items[0]["title"], "Frieren")

    def test_move_keeps_the_order_and_stops_at_the_ends(self):
        pid = playlists.create("a")
        for n in (1, 2, 3):
            playlists.add(pid, show(f"aid:{n}"))
        refs = lambda: [x["ref"] for x in playlists.all_playlists()[0]["items"]]
        playlists.move(pid, "aid:3", -1)
        self.assertEqual(refs(), ["aid:1", "aid:3", "aid:2"])
        playlists.move(pid, "aid:1", -5)
        self.assertEqual(refs(), ["aid:1", "aid:3", "aid:2"])
        playlists.move(pid, "aid:1", 9)
        self.assertEqual(refs(), ["aid:3", "aid:2", "aid:1"])

    def test_add_after_remove_goes_last(self):
        pid = playlists.create("a")
        for n in (1, 2, 3):
            playlists.add(pid, show(f"aid:{n}"))
        playlists.remove(pid, "aid:3")
        playlists.add(pid, show("aid:4"))
        self.assertEqual([x["ref"] for x in playlists.all_playlists()[0]["items"]], ["aid:1", "aid:2", "aid:4"])

    def test_delete_takes_its_items(self):
        pid = playlists.create("a", show())
        playlists.delete(pid)
        with database.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM playlist_items").fetchone()[0], 0)

    def test_a_gone_playlist_says_so(self):
        for call in (lambda: playlists.rename(999, "x"), lambda: playlists.delete(True), lambda: playlists.add("1", show())):
            with self.assertRaises(playlists.PlaylistError):
                call()

    def test_looks_are_set_and_cleared(self):
        pid = playlists.create("a")
        playlists.edit(pid, {"color": "#e58ccf", "note": "  rainy   days ", "icon": "cloud-rain", "pinned": True, "name": "b"})
        p = playlists.all_playlists()[0]
        self.assertEqual((p["name"], p["color"], p["note"], p["icon"], p["pinned"]), ("b", "#e58ccf", "rainy days", "cloud-rain", True))
        playlists.edit(pid, {"color": None, "note": "", "icon": "", "pinned": False})
        p = playlists.all_playlists()[0]
        self.assertEqual((p["color"], p["note"], p["icon"], p["pinned"]), (None, "", "", False))

    def test_bad_looks_are_refused(self):
        pid = playlists.create("a")
        for bad in ({"color": "red"}, {"note": "x" * 141}, {"icon": "a b"}, {"icon": "🌧️"}, {"icon": "x" * 33}, {"pinned": 1}, {"name": ""}):
            with self.assertRaises(playlists.PlaylistError, msg=bad):
                playlists.edit(pid, bad)

    def test_playlists_keep_their_own_order(self):
        a, b, c = (playlists.create(n) for n in "abc")
        names = lambda: [p["name"] for p in playlists.all_playlists()]
        self.assertEqual(names(), ["a", "b", "c"])
        playlists.move_list(c, -2)
        self.assertEqual(names(), ["c", "a", "b"])
        playlists.create("d", at=1)
        self.assertEqual(names(), ["c", "d", "a", "b"])

    def test_undo_puts_a_show_back_in_its_place(self):
        pid = playlists.create("a")
        for n in (1, 2, 3):
            playlists.add(pid, show(f"aid:{n}"))
        playlists.remove(pid, "aid:2")
        playlists.add(pid, show("aid:2"), at=1)
        self.assertEqual([x["ref"] for x in playlists.all_playlists()[0]["items"]], ["aid:1", "aid:2", "aid:3"])

    def test_undo_brings_a_deleted_playlist_back_whole(self):
        playlists.create("first")
        pid = playlists.create("gone", looks={"color": "#9ccbe8", "icon": "star", "note": "n", "pinned": True})
        for n in (1, 2):
            playlists.add(pid, show(f"aid:{n}"))
        old = playlists.all_playlists()[1]
        playlists.delete(pid)
        playlists.create(old["name"], looks={k: old[k] for k in ("color", "note", "icon", "pinned")}, items=old["items"], at=1)
        back = playlists.all_playlists()[1]
        self.assertEqual({k: v for k, v in back.items() if k != "id"}, {k: v for k, v in old.items() if k != "id"})

    def test_the_library_payload_carries_them(self):
        playlists.create("a", show())
        self.assertEqual(library.full_library()["playlists"][0]["name"], "a")


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
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/api/playlists", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_create_then_unknown_action(self):
        code, r = self.post({"op": "create", "name": "Api list", "item": show()})
        self.assertEqual(code, 200)
        self.assertTrue(any(p["id"] == r["created"] for p in r["playlists"]))
        code, r = self.post({"op": "explode"})
        self.assertEqual(code, 400)
        self.assertIn("error", r)


class EmojiBecameIcon(unittest.TestCase):
    """A review database that ran migration 24 when it still made `emoji`."""

    def test_the_column_is_renamed_and_emoji_dropped(self):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE playlists(id INTEGER PRIMARY KEY, name TEXT, emoji TEXT)")
        c.execute("INSERT INTO playlists(name, emoji) VALUES('a', '🌧️')")
        database.migration_25(c)
        self.assertEqual(c.execute("SELECT icon FROM playlists").fetchone(), (None,))
        database.migration_25(c)  # twice is harmless
