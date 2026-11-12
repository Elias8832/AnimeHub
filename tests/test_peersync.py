"""Sync between the Android app and a PC (Decision 91): episodes merge
three-way (a side that changed wins, both changed: further along), each
write checks the slot is still what the merge saw, playlists and list
entries go to the newer side, and pairing runs through the phone remote's
real listener."""
import json
import shutil
import unittest
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, library, peersync, playlists, remote, server, streaming
from animehub.config import config

PC_DB = support.TMP / "peersync-pc.db"
APP_DB = support.TMP / "peersync-app.db"


@contextmanager
def device(path):
    """Run as one device: its own database, no library folder."""
    with patch.object(config, "DB", path), patch.object(config, "LIBRARY", None):
        yield


def fresh():
    for p in (PC_DB, APP_DB):
        for f in p.parent.glob(p.name + "*"):
            f.unlink()
        with device(p):
            database.init_db()


def fake_call(address, path, token=None, body=None):
    """The PC's two routes, run against the PC's database."""
    assert token == "tok"
    with device(PC_DB):
        if path == "/api/sync/facts":
            return json.loads(json.dumps(peersync.facts_payload())), None
        if path == "/api/sync/apply":
            body = json.loads(json.dumps(body))
            assert peersync.check_apply(body) is None, peersync.check_apply(body)
            return peersync.apply_payload(body), None
    raise AssertionError(path)


def sync():
    with device(APP_DB), patch.object(peersync, "_call", fake_call):
        return peersync.sync_once()


def ep(path, eid, watched=0, position=0, duration=0, last=None):
    with device(path), database.db() as c:
        c.execute("INSERT INTO episodes(id, watched, position, duration, last_played) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET watched=excluded.watched, position=excluded.position, "
                  "duration=excluded.duration, last_played=excluded.last_played",
                  (eid, watched, position, duration, last))


def row(path, eid):
    with device(path), database.db() as c:
        r = c.execute("SELECT watched, position, duration, last_played FROM episodes WHERE id=?", (eid,)).fetchone()
    return r and (bool(r[0]), r[1], r[2], r[3])


def paired():
    with device(APP_DB), database.db() as c:
        c.execute("INSERT INTO sync_peer(id, address, token, paired) VALUES(1, 'http://pc:8788', 'tok', '2026-11-12 10:00:00')")


class Merge(unittest.TestCase):
    A = [True, 0.0, 1400.0, "2026-11-10 20:00:00"]
    B = [False, 600.0, 1400.0, "2026-11-11 20:00:00"]

    def test_only_one_side_changed_wins_even_backwards(self):
        base = {"1:1": self.A}
        unticked = [False, 0.0, 1400.0, "2026-11-10 20:00:00"]
        self.assertEqual(peersync.merge_episodes({"1:1": unticked}, {"1:1": self.A}, base), {"1:1": unticked})
        self.assertEqual(peersync.merge_episodes({"1:1": self.A}, {"1:1": unticked}, base), {"1:1": unticked})

    def test_both_changed_further_along_wins(self):
        got = peersync.merge_episodes({"1:1": self.A}, {"1:1": self.B}, None)
        self.assertEqual(got, {"1:1": [True, 600.0, 1400.0, "2026-11-11 20:00:00"]})

    def test_a_slot_only_one_side_has(self):
        self.assertEqual(peersync.merge_episodes({}, {"1:2": self.B}, {}), {"1:2": self.B})
        self.assertEqual(peersync.merge_episodes({}, {"1:2": self.B}, {"1:2": self.B}), {"1:2": peersync.EMPTY})

    def test_addresses(self):
        for raw in ("192.168.2.31:8789", " http://192.168.2.31:8789/ ", "HTTP://192.168.2.31:8789"):
            self.assertEqual(peersync.normal_address(raw), "http://192.168.2.31:8789")
        for raw in ("", "192.168.2.31", "http://:80", 5):
            with self.assertRaises(peersync.SyncError):
                peersync.normal_address(raw)

    def test_apply_shape_is_checked(self):
        ok = {"episodes": {"5:1": [peersync.EMPTY, self.A]}, "playlists": {}, "gone": {}, "curation": {}}
        self.assertIsNone(peersync.check_apply(ok))
        for bad in ({"episodes": {"x:1": [peersync.EMPTY, self.A]}},
                    {"episodes": {"5:1": [peersync.EMPTY, [True, -1, 0, None]]}},
                    {"playlists": {"abc": {"name": "x", "changed": "", "items": []}}},
                    {"playlists": {"0123456789abcdef": {"name": "x", "changed": "", "items": [{"ref": "sid:9", "title": "x"}]}}},
                    {"playlists": {"0123456789abcdef": {"name": "x", "changed": "", "items": [], "color": {}}}},
                    {"curation": {"aid:1": {"updated_at": "x", "status": ["no"]}}}):
            self.assertIsNotNone(peersync.check_apply(bad), bad)


class Episodes(unittest.TestCase):
    def setUp(self):
        fresh()
        paired()

    def test_first_sync_brings_the_pcs_watching_over_and_back(self):
        ep(PC_DB, "stream:5:1", 1, 0, 1420, "2026-11-10 20:00:00")
        ep(PC_DB, "stream:5:2", 0, 612.5, 1420, "2026-11-10 20:30:00")
        ep(APP_DB, "stream:9:3", 1, 0, 1400, "2026-11-11 08:00:00")
        sync()
        self.assertEqual(row(APP_DB, "stream:5:1"), (True, 0, 1420, "2026-11-10 20:00:00"))
        self.assertEqual(row(APP_DB, "stream:5:2"), (False, 612.5, 1420, "2026-11-10 20:30:00"))
        self.assertEqual(row(PC_DB, "stream:9:3"), (True, 0, 1400, "2026-11-11 08:00:00"))
        with device(APP_DB):
            self.assertEqual(peersync.peer()["snapshot"]["5:2"], [False, 612.5, 1420.0, "2026-11-10 20:30:00"])
            self.assertIsNotNone(peersync.peer()["last_ok"])
        self.assertEqual(sync(), {"pulled": 0, "pushed": 0})  # nothing left to do

    def test_an_untick_reaches_the_other_side(self):
        ep(PC_DB, "stream:5:1", 1, 0, 1420, "2026-11-10 20:00:00")
        sync()
        ep(APP_DB, "stream:5:1", 0, 0, 1420, "2026-11-10 20:00:00")
        sync()
        self.assertFalse(row(PC_DB, "stream:5:1")[0])

    def test_both_played_the_later_stop_point_wins(self):
        sync()
        ep(PC_DB, "stream:5:4", 0, 300, 1420, "2026-11-12 09:00:00")
        ep(APP_DB, "stream:5:4", 0, 900, 1420, "2026-11-12 10:00:00")
        sync()
        self.assertEqual(row(PC_DB, "stream:5:4"), (False, 900, 1420, "2026-11-12 10:00:00"))
        self.assertEqual(row(APP_DB, "stream:5:4"), (False, 900, 1420, "2026-11-12 10:00:00"))

    def test_a_slot_that_moved_meanwhile_is_left_for_next_time(self):
        ep(PC_DB, "stream:5:1", 0, 100, 1420, "2026-11-12 09:00:00")
        with device(APP_DB):
            done = peersync.apply_episodes({"5:1": [[False, 50.0, 1420.0, "2026-11-12 08:00:00"],
                                                    [False, 999.0, 1420.0, "2026-11-12 09:30:00"]]})
        self.assertEqual(done, [])  # the app had nothing there, not what the merge saw
        with device(PC_DB):
            done = peersync.apply_episodes({"5:1": [[False, 50.0, 1420.0, "2026-11-12 08:00:00"],
                                                    [False, 999.0, 1420.0, "2026-11-12 09:30:00"]]})
        self.assertEqual(done, [])
        self.assertEqual(row(PC_DB, "stream:5:1")[1], 100)

    def test_a_skipped_write_keeps_the_old_agreement(self):
        ep(PC_DB, "stream:5:1", 0, 100, 1420, "2026-11-12 09:00:00")
        real = peersync.apply_episodes
        with patch.object(peersync, "apply_episodes", lambda changes, slots=None: [] if changes else real(changes, slots)):
            sync()  # the app's write "lost the race"
        with device(APP_DB):
            self.assertNotIn("5:1", peersync.peer()["snapshot"])
        sync()
        self.assertEqual(row(APP_DB, "stream:5:1")[1], 100)

    def test_facts_go_to_the_files_of_the_slot(self):
        ep(PC_DB, "stream:7:3", 0, 400, 1400, "2026-11-12 09:00:00")
        with patch.object(peersync, "slot_map", lambda: {"7:3": ["file-abc"]} if config.DB == APP_DB else {"7:3": ["stream:7:3"]}):
            sync()
        self.assertEqual(row(APP_DB, "file-abc"), (False, 400, 1400, "2026-11-12 09:00:00"))
        self.assertIsNone(row(APP_DB, "stream:7:3"))

    def test_a_file_and_its_online_twin_are_one_slot(self):
        ep(APP_DB, "file-abc", 1, 0, 1400, "2026-11-12 08:00:00")
        ep(APP_DB, "stream:7:3", 0, 200, 1400, "2026-11-12 09:00:00")
        with device(APP_DB), patch.object(peersync, "slot_map", lambda: {"7:3": ["file-abc", "stream:7:3"]}):
            self.assertEqual(peersync.episode_facts(), {"7:3": [True, 200.0, 1400.0, "2026-11-12 09:00:00"]})

    def test_stream_ids_and_odd_numbers(self):
        self.assertEqual(peersync._stream_slot("stream:5:3"), "5:3")
        self.assertEqual(peersync._stream_slot("stream:5:03"), "5:3")
        self.assertIsNone(peersync._stream_slot("stream:5:12.5"))
        self.assertIsNone(peersync._stream_slot("stream:5:0"))
        self.assertIsNone(peersync._stream_slot("stream:x:1"))


class Lists(unittest.TestCase):
    def setUp(self):
        fresh()
        paired()

    def lists(self, path):
        with device(path):
            return playlists.all_playlists()

    def test_a_playlist_travels_and_changes_follow(self):
        with device(PC_DB):
            pid = playlists.create("Winter", {"ref": "aid:1", "title": "Frieren", "poster": "/img/1.jpg"})
            playlists.add(pid, {"ref": "sid:folder9", "title": "Home videos", "poster": None})
        sync()
        got = self.lists(APP_DB)
        self.assertEqual([(p["name"], p["items"]) for p in got],
                         [("Winter", [{"ref": "aid:1", "title": "Frieren", "poster": None}])])  # no PC paths, no PC folders
        with device(APP_DB):
            playlists.rename(got[0]["id"], "Winter nights")
            playlists.add(got[0]["id"], {"ref": "aid:2", "title": "Dandadan", "poster": "https://s4.anilist.co/x.jpg"})
        sync()
        pc = self.lists(PC_DB)[0]
        self.assertEqual(pc["name"], "Winter nights")
        self.assertEqual([i["ref"] for i in pc["items"]], ["aid:1", "aid:2", "sid:folder9"])  # its own folder stays
        self.assertEqual(pc["items"][0]["poster"], "/img/1.jpg")
        with device(PC_DB):
            playlists.delete(pc["id"])
        sync()
        self.assertEqual(self.lists(APP_DB), [])
        self.assertEqual(sync(), {"pulled": 0, "pushed": 0})

    def test_same_name_before_the_first_sync_is_one_list(self):
        for path, ref in ((PC_DB, "aid:1"), (APP_DB, "aid:2")):
            with device(path):
                playlists.create("Favourites", {"ref": ref, "title": ref})
        sync()
        self.assertEqual(len(self.lists(APP_DB)), 1)
        self.assertEqual(len(self.lists(PC_DB)), 1)
        with device(APP_DB), database.db() as c:
            app_uid = c.execute("SELECT uid FROM playlists").fetchone()[0]
        with device(PC_DB), database.db() as c:
            self.assertEqual(c.execute("SELECT uid FROM playlists").fetchone()[0], app_uid)

    def test_your_own_entries_newer_wins(self):
        for path, status, when in ((PC_DB, "COMPLETED", "2026-11-12 10:00:00"), (APP_DB, "CURRENT", "2026-11-11 10:00:00")):
            with device(path), database.db() as c:
                c.execute("INSERT INTO curation(ref, status, updated_at) VALUES('aid:5', ?, ?)", (status, when))
        with device(PC_DB), database.db() as c:
            c.execute("INSERT INTO curation(ref, status, updated_at) VALUES('sid:abc', 'CURRENT', '2026-11-12 10:00:00')")
        sync()
        with device(APP_DB), database.db() as c:
            self.assertEqual(c.execute("SELECT status FROM curation WHERE ref='aid:5'").fetchone()[0], "COMPLETED")
            self.assertIsNone(c.execute("SELECT 1 FROM curation WHERE ref='sid:abc'").fetchone())


class HomeShowsWhatCameOver(unittest.TestCase):
    """An episode played on the other device, of a show this one has neither
    on disk nor linked online, is on Home and in History (Decision 91)."""

    def setUp(self):
        fresh()

    def library(self):
        with device(APP_DB), patch.object(library, "scan_library", lambda: []), \
                patch.object(streaming.anicli, "anicli_path", lambda: "/bin/ani-cli"):
            return library.full_library()

    def test_played_elsewhere_is_on_home(self):
        ep(APP_DB, "stream:777:3", 0, 400, 1400, "2026-11-12 09:00:00")
        ep(APP_DB, "stream:888:1", 1, 0, 0, None)  # a mark, never played: not a show
        with device(APP_DB):
            database.cache_put("release:v1:777", {"media": {
                "id": 777, "title": {"english": "Dandadan"}, "status": "FINISHED", "episodes": 12, "format": "TV",
                "coverImage": {"extraLarge": "https://s4.anilist.co/c.jpg"}}})
        d = self.library()
        show = next(s for s in d["streams"] if s["id"] == "aid:777")
        self.assertTrue(show["stream"]["provisional"])
        self.assertEqual(len(show["seasons"][0]["episodes"]), 12)
        self.assertEqual([e["id"] for e in d["continue"]], ["stream:777:3"])
        self.assertEqual([e["show"] for e in d["history"]], ["Dandadan"])
        self.assertFalse(any(s["id"] == "aid:888" for s in d["streams"]))

    def test_without_anilist_info_it_is_fetched_once(self):
        ep(APP_DB, "stream:999:1", 0, 400, 1400, "2026-11-12 09:00:00")
        asked = []
        with patch.object(streaming, "_fetch_meta_later", asked.append):
            d = self.library()
        self.assertEqual(asked, [999])
        self.assertFalse(any(s["id"] == "aid:999" for s in d["streams"]))


class PastTheVpn(unittest.TestCase):
    """In the Android app, sync goes straight over Wi-Fi first (Lan.java), so a
    VPN on the phone doesn't stop it; when the VPN won't allow that, the
    normal way, and a clear reason when that fails too (Decision 91)."""

    def call(self, home, plain, vpn):
        with patch.object(peersync.system, "ANDROID", True), patch.object(peersync, "_home_network", home), \
                patch.object(peersync, "_plain", plain), patch.object(peersync, "_vpn_up", lambda: vpn):
            return peersync._call("http://pc:8788", "/api/sync/facts", "tok")

    def test_over_wifi_first(self):
        seen = []
        got = self.call(lambda url, data, h: seen.append(h["Cookie"]) or (200, b'{"version": 1}', ["a=b"]),
                        lambda *a: self.fail("not needed"), True)
        self.assertEqual(got, ({"version": 1}, ["a=b"]))
        self.assertEqual(seen, ["animehub_remote=tok"])

    def test_vpn_refuses_then_the_normal_way(self):
        def refused(*a):
            raise OSError("EPERM")
        self.assertEqual(self.call(refused, lambda *a: (200, b"{}", []), True), ({}, []))

    def test_nothing_gets_through_with_a_vpn_says_so(self):
        def refused(*a):
            raise OSError("EPERM")
        with self.assertRaises(peersync.SyncError) as e:
            self.call(refused, refused, True)
        self.assertEqual(str(e.exception), peersync.VPN_BLOCKS)
        with self.assertRaises(peersync.SyncError) as e:
            self.call(refused, refused, False)
        self.assertIn("Can't reach the PC", str(e.exception))

    def test_errors_read_the_same_both_ways(self):
        with self.assertRaises(peersync.SyncError) as e:
            self.call(lambda *a: (401, b"{}", []), None, False)
        self.assertIn("Pair again", str(e.exception))


class Pairing(unittest.TestCase):
    """Through the phone remote's real Wi-Fi listener (same database here)."""

    def setUp(self):
        database.init_db()
        with database.db() as c:
            c.execute("DELETE FROM sync_peer")
            c.execute("DELETE FROM remote_devices")
        with patch.object(config, "REMOTE_PORT", 0):
            self.assertTrue(remote.start(server.RemoteHandler))
        self.addCleanup(remote.stop)
        self.addr = "127.0.0.1:%d" % remote._listener["server"].server_address[1]
        p = patch.object(peersync, "urlopen", urllib.request.urlopen)  # support blocks the network
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.unpair)

    @staticmethod
    def unpair():
        with database.db() as c:
            c.execute("DELETE FROM sync_peer")

    def test_pair_then_sync(self):
        code = remote.new_code()
        with patch.object(peersync, "soon", lambda *a: None):
            st = peersync.pair(self.addr, " " + code[:3] + "-" + code[3:] + "\n")
        self.assertTrue(st["paired"])
        self.assertNotIn("token", st)
        self.assertEqual([d["name"] for d in remote.devices()], ["Android app"])
        self.assertEqual(peersync.sync_once(), {"pulled": 0, "pushed": 0})
        self.assertIsNotNone(peersync.status()["last_ok"])
        # The app's own pairing route is not for a paired phone to use.
        with self.assertRaises(peersync.SyncError) as e:
            peersync._call("http://" + self.addr, "/api/peer", peersync.peer()["token"], {"op": "forget"})
        self.assertIn("can't change", str(e.exception))

    def test_wrong_code_and_a_forgotten_phone(self):
        remote.new_code()
        with self.assertRaises(peersync.SyncError) as e:
            peersync.pair(self.addr, "000000" if remote.code_info()["code"] != "000000" else "111111")
        self.assertIn("Wrong or expired code", str(e.exception))
        with database.db() as c:
            c.execute("INSERT INTO sync_peer(id, address, token) VALUES(1, ?, 'nope')", ("http://" + self.addr,))
        with self.assertRaises(peersync.SyncError) as e:
            peersync.sync_once()
        self.assertIn("Pair again", str(e.exception))
        self.assertIn("Pair again", peersync.status()["error"])

    def test_no_code_never_reaches_the_pc(self):
        for bad in ("", "12345", "1234567", None, "abcdef"):
            with self.assertRaises(peersync.SyncError) as e:
                peersync.pair(self.addr, bad)
            self.assertIn("6-digit code", str(e.exception))

    def test_pc_not_there(self):
        with database.db() as c:
            c.execute("INSERT INTO sync_peer(id, address, token) VALUES(1, 'http://127.0.0.1:9', 'x')")
        with self.assertRaises(peersync.SyncError) as e:
            peersync.sync_once()
        self.assertIn("Can't reach the PC", str(e.exception))


if __name__ == "__main__":
    unittest.main()
