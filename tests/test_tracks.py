"""Audio and subtitle tracks remembered per show (tracks.py): matching a
pick to a new file's tracks, what is stored, the player picking them at
the start and remembering only the user's own switch."""
import unittest
from unittest.mock import patch

from tests import support
from animehub import database, library, player, settings, tracks


def setUpModule():
    support.use_library(support.case("aot"))


def tl(*items):
    """A track-list like mpv's: (id, type, lang, title, selected)."""
    return [{"id": i, "type": t, "lang": lang, "title": title, "selected": sel}
            for i, t, lang, title, sel in items]


DUAL = tl((1, "video", None, None, True),
          (1, "audio", "jpn", "Japanese", True), (2, "audio", "eng", "English", False),
          (1, "sub", "eng", "Full Subtitles", True), (2, "sub", "eng", "Signs & Songs", False),
          (3, "sub", "ger", None, False))


class Matching(unittest.TestCase):
    def test_selection(self):
        self.assertEqual(tracks.selection(DUAL), {"audio": {"lang": "jpn", "title": "Japanese"},
                                                  "sub": {"lang": "eng", "title": "Full Subtitles"}})
        off = [dict(t, selected=False) if t["type"] == "sub" else t for t in DUAL]
        self.assertEqual(tracks.selection(off)["sub"], {"off": True})

    def test_language_then_title(self):
        self.assertEqual(tracks.choose(DUAL, "audio", {"lang": "eng", "title": "Other name"}), 2)
        self.assertEqual(tracks.choose(DUAL, "sub", {"lang": "eng", "title": "Signs & Songs"}), 2)
        self.assertEqual(tracks.choose(DUAL, "sub", {"lang": "ger", "title": "Deutsch"}), 3)
        # A language the file doesn't have: mpv's own choice stays.
        self.assertIsNone(tracks.choose(DUAL, "audio", {"lang": "fre", "title": "Japanese"}))
        self.assertEqual(tracks.choose(DUAL, "sub", {"off": True}), "no")
        self.assertIsNone(tracks.choose(DUAL, "audio", {"off": True}))
        # Untagged files: the title alone finds it.
        bare = tl((1, "sub", None, "Full", True), (2, "sub", None, "Signs", False))
        self.assertEqual(tracks.choose(bare, "sub", {"lang": "eng", "title": "signs"}), 2)
        self.assertIsNone(tracks.choose(bare, "sub", {"lang": "eng", "title": "Other"}))

    def test_changes_only_what_differs(self):
        self.assertEqual(tracks.changes(DUAL, {"audio": {"lang": "jpn", "title": "Japanese"}}), {})
        self.assertEqual(tracks.changes(DUAL, {"audio": {"lang": "eng", "title": "English"},
                                               "sub": {"off": True}}), {"aid": 2, "sid": "no"})
        self.assertEqual(tracks.changes(DUAL, {}), {})

    def test_store_keeps_each_kind_for_every_key(self):
        tracks.save(["media:1", "show:a"], "sub", {"off": True})
        tracks.save(["show:a"], "audio", {"lang": "eng", "title": "English"})
        self.assertEqual(tracks.load(["media:1"]), {"sub": {"off": True}})
        self.assertEqual(tracks.load(["media:2", "show:a"]),
                         {"sub": {"off": True}, "audio": {"lang": "eng", "title": "English"}})
        self.assertEqual(tracks.load(["media:404"]), {})
        forget()


class FakeMpv:
    def __init__(self, track_list):
        self.tracks = [dict(t) for t in track_list]
        self.sets = []

    def __call__(self, sock, command, timeout=2.0):
        if command == ["get_property", "track-list"]:
            return {"data": [dict(t) for t in self.tracks]}
        if command[0] == "set_property":
            self.sets.append((command[1], command[2]))
            self.pick({"aid": "audio", "sid": "sub"}[command[1]], command[2])
        return {"error": "success"}

    def pick(self, kind, tid):
        for t in self.tracks:
            if t["type"] == kind:
                t["selected"] = t["id"] == tid


def forget():
    with database.db() as c:
        c.execute("DELETE FROM track_prefs")


class Player(unittest.TestCase):
    def setUp(self):
        forget()
        self.addCleanup(forget)

    def play(self, mpv, polls=3, between=None):
        state = {}
        with patch.object(player, "mpv_ipc", mpv), patch.object(player, "track_shows", return_value=["media:7", "show:x"]):
            for i in range(polls):
                player.follow_tracks("sock", "ep", state, mpv(None, ["get_property", "track-list"])["data"])
                if between and i in between:
                    between[i]()

    def test_only_the_users_switch_is_remembered(self):
        mpv = FakeMpv(DUAL)
        self.play(mpv)
        self.assertEqual(tracks.load(["media:7"]), {})  # mpv's own choice: nothing stored
        mpv = FakeMpv(DUAL)
        self.play(mpv, between={0: lambda: mpv.pick("audio", 2), 1: lambda: mpv.pick("sub", 0)})
        self.assertEqual(tracks.load(["show:x"]), {"audio": {"lang": "eng", "title": "English"},
                                                   "sub": {"off": True}})

    def test_next_episode_starts_with_the_remembered_tracks(self):
        tracks.save(["media:7"], "audio", {"lang": "eng", "title": "English"})
        tracks.save(["media:7"], "sub", {"lang": "eng", "title": "Signs & Songs"})
        mpv = FakeMpv(DUAL)
        self.play(mpv)
        self.assertEqual(sorted(mpv.sets), [("aid", 2), ("sid", 2)])
        # Picking them was not a switch by the user: the store is unchanged.
        self.assertEqual(tracks.load(["show:x"]), {})

    def test_no_tracks_yet_waits(self):
        mpv = FakeMpv([])
        state = {}
        with patch.object(player, "mpv_ipc", mpv):
            player.follow_tracks("sock", "ep", state, [])
        self.assertEqual(state, {})

    def test_keys_name_the_entry_and_the_library_show(self):
        scan = support.use_library(support.case("aot"))
        s = scan[0]
        ep = next(library.eps_of(s))
        keys = player.track_shows(ep["id"])
        self.assertEqual(keys[-1], "show:%s" % s["id"])
        self.assertTrue(all(k.startswith(("media:", "show:")) for k in keys))

    def test_setting(self):
        self.assertTrue(tracks.enabled())
        settings.set_automation("remember_tracks", False)
        self.addCleanup(settings.set_automation, "remember_tracks", True)
        self.assertFalse(tracks.enabled())
        self.assertFalse(settings.settings_payload()["remember_tracks"])


if __name__ == "__main__":
    unittest.main()
