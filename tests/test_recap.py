"""Remember the season before (TODO 133, Decision 85): Play on the first
episode of a new season asks first when the season before was finished a
while ago. Made in Abyss: Season 1 (13 files) and The Golden City (Season 2,
12 files) on disk; AniList lists two recap movies of Season 1."""

import datetime
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tests import support
from animehub import anicli, anilist, database, library, pages, recap, server, settings

TODAY = datetime.date(2026, 11, 10)


class Recap(unittest.TestCase):
    def setUp(self):
        c = support.case("made-in-abyss")
        self.scan = support.match_library(c, support.use_library(c))
        p = mock.patch.object(anicli, "anicli_path", return_value=None)  # files only: no online slots
        p.start()
        self.addCleanup(p.stop)
        page = pages.franchise_view(97986, background=False)
        self.s1 = next(e for e in page["entries"] if e["id"] == 97986)
        self.s2 = next(e for e in page["entries"] if e["id"] == 114745)
        self.s2e1 = self.s2["slots"][0]["id"]

    def watch(self, slots, day):
        with database.db() as c:
            c.executemany("INSERT INTO episodes(id, watched, position, duration, last_played) VALUES(?, 1, 0, 1400, ?)"
                          " ON CONFLICT(id) DO UPDATE SET watched=1, last_played=excluded.last_played",
                          [(x["id"], f"{day} 20:00:00") for x in slots])

    def ask(self, ep=None, today=TODAY):
        return recap.for_episode(ep or self.s2e1, today=today)

    def test_season_watched_here_months_ago(self):
        self.watch(self.s1["slots"], "2026-06-07")
        r = self.ask()
        self.assertTrue(r["show"])
        self.assertEqual((r["entry"]["id"], r["prev"]["id"], r["prev"]["label"]), (114745, 97986, "Season 1"))
        self.assertEqual((r["prev"]["finished"], r["prev"]["months"]), ("2026-06-07", 5))
        self.assertEqual((r["last"]["number"], r["last"]["id"]), (13, self.s1["slots"][-1]["id"]))
        self.assertIsNone(r["prev"]["score"])

    def test_recap_movies_from_anilist(self):
        self.watch(self.s1["slots"], "2026-06-07")
        recaps = self.ask()["recaps"]
        self.assertEqual([x["id"] for x in recaps], [101343, 101344])
        self.assertEqual({x["watched"] for x in recaps}, {False})

    def test_not_long_enough_ago(self):
        self.watch(self.s1["slots"], "2026-09-11")  # 1 month 30 days
        self.assertFalse(self.ask()["show"])
        settings.set_setting("recap_after", "1")
        self.assertTrue(self.ask()["show"])

    def test_season_not_finished(self):
        self.watch(self.s1["slots"][:-1], "2026-01-01")
        self.assertFalse(self.ask()["show"])

    def test_only_the_first_episode_of_a_later_season(self):
        self.watch(self.s1["slots"], "2026-01-01")
        self.assertFalse(self.ask(self.s2["slots"][1]["id"])["show"])
        self.assertFalse(self.ask(self.s1["slots"][0]["id"])["show"])
        self.assertTrue(self.ask("stream:114745:1")["show"])  # the online episode is the same one

    def test_season_already_started(self):
        self.watch(self.s1["slots"], "2026-01-01")
        with database.db() as c:
            c.execute("INSERT INTO episodes(id, watched, position) VALUES(?, 0, 300)", (self.s2["slots"][2]["id"],))
        self.assertFalse(self.ask()["show"])

    def test_answered_once(self):
        self.watch(self.s1["slots"], "2026-01-01")
        recap.mark_done(114745)
        self.assertFalse(self.ask()["show"])

    def test_turned_off(self):
        self.watch(self.s1["slots"], "2026-01-01")
        settings.set_automation("recap_ask", False)
        self.assertFalse(self.ask()["show"])

    def test_list_finish_date_and_score(self):
        """Finished and scored on the list (AniList or MyAnimeList), nothing
        watched here: the list's date counts."""
        state = {"score_max": 10, "entry": {"status": "COMPLETED", "score": 8, "progress": 13, "completed_at": "2026-04-25"}}
        with mock.patch.object(anilist, "account_present", return_value=True), \
                mock.patch.object(anilist, "entry_state_from_lists", side_effect=lambda mid: state if mid == 97986 else None):
            r = self.ask()
        self.assertEqual((r["prev"]["finished"], r["prev"]["months"], r["prev"]["score"]), ("2026-04-25", 6, 8))

    def test_rewatched_here_since_the_list_date(self):
        """The later of the two: a rewatch here last month is fresh."""
        self.watch(self.s1["slots"], "2026-10-20")
        state = {"score_max": 10, "entry": {"status": "COMPLETED", "score": 8, "progress": 13, "completed_at": "2025-01-01"}}
        with mock.patch.object(anilist, "account_present", return_value=True), \
                mock.patch.object(anilist, "entry_state_from_lists", side_effect=lambda mid: state if mid == 97986 else None):
            self.assertFalse(self.ask()["show"])

    def test_without_an_account_the_local_list(self):
        library.curation_set("aid:97986", {"status": "COMPLETED", "score": 7, "finished_at": "2026-03-02"})
        r = self.ask()
        self.assertEqual((r["prev"]["finished"], r["prev"]["score"]), ("2026-03-02", 7))

    def test_months(self):
        d = datetime.date
        self.assertEqual(recap.months_between(d(2026, 6, 7), d(2026, 11, 10)), 5)
        self.assertEqual(recap.months_between(d(2026, 6, 11), d(2026, 11, 10)), 4)
        self.assertEqual(recap.months_between(d(2025, 11, 10), d(2026, 11, 10)), 12)


class Routes(unittest.TestCase):
    def setUp(self):
        support.use_library(support.case("made-in-abyss"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_address[1]}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Host": "127.0.0.1:8787"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        self.assertEqual(self.call("/api/recap?id=stream:114745:1"), (200, {"show": False}))
        self.assertEqual(self.call("/api/recap")[0], 400)
        self.assertEqual(self.call("/api/recap", {"media_id": 114745}), (200, {"ok": True}))
        self.assertEqual(self.call("/api/recap", {"media_id": "x"})[0], 400)
        self.assertEqual(json.loads(settings.get_setting("recap_done")), [114745])
        code, body = self.call("/api/settings", {"recap_after": "3", "recap_ask": False})
        self.assertEqual((code, body["recap_after"], body["recap_ask"]), (200, "3", False))
        self.assertEqual(self.call("/api/settings", {"recap_after": "4"})[0], 400)

    def test_settings_keys(self):
        p = settings.settings_payload()
        self.assertEqual((p["recap_ask"], p["recap_after"]), (True, "2"))
        settings.set_setting("recap_after", "6")
        self.assertEqual(settings.settings_payload()["recap_after"], "6")
        settings.set_setting("recap_after", "nonsense")
        self.assertEqual(settings.settings_payload()["recap_after"], "2")

    def test_done_is_kept(self):
        recap.mark_done(5)
        recap.mark_done(7)
        self.assertEqual(json.loads(settings.get_setting("recap_done")), [5, 7])


if __name__ == "__main__":
    unittest.main()


class Web(unittest.TestCase):
    """The page side: Play asks the hub first, every answer counts once."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        web = Path(__file__).resolve().parent.parent / "web"
        cls.app = (web / "app.js").read_text(encoding="utf-8")
        cls.css = (web / "style.css").read_text(encoding="utf-8")

    def test_play_asks_first(self):
        start = self.app.index("async function play(id, version = 0, mode, asked, fromStart, skipOk, where) {")
        body = self.app[start:start + 600]
        self.assertIn("recapCandidate(id)", body)
        self.assertIn('openModal({ t: "recap"', body)
        self.assertIn("S.recapPass ||= new Set()", body)  # the next Play of it goes straight through

    def test_answers_mark_the_season(self):
        for act in ("recapgo:", "recapplay:"):
            line = next(x for x in self.app.splitlines() if x.strip().startswith(act))
            self.assertIn('api("/api/recap", { media_id:', line)
        self.assertIn('setToggle("recap_ask", false)', self.app)
        self.assertIn("recap: RecapDlg", self.app)

    def test_settings_rows(self):
        self.assertIn('onoff("recap_ask", "Season reminder"', self.app)
        self.assertIn('"spref", "recap_after"', self.app)
        self.assertIn('["Season reminder", "player",', self.app)
        rows = self.app[self.app.index("function playerGroup("):self.app.index("function subsGroup(")]
        self.assertIn("${rc}${m.keys && !pickTouch ? hkGroup(m)", rows)  # above Keys (Elias, 2026-11-10); no Keys on a phone (Decision 94)

    def test_quiet_button_has_no_icon(self):
        """In the pop-up frame since TODO 226: the opt-out is the footer's link, Start the accent text."""
        self.assertIn('<button class="lnk" data-a="recapoff">Don’t show this again</button>', self.app)
        self.assertIn('data-a="recapgo">${start}${kb("↵")}</button>', self.app)
