"""The Journal from AniList activity (Elias, 2026-10-24): the copy of the
activity log, its episode ranges, paging, and the read states."""

import time
import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import journal


def act(i, at, status="watched episode", progress="3 - 6", mid=7):
    return {"id": i, "createdAt": at, "status": status, "progress": progress,
            "media": {"id": mid, "title": {"romaji": "Show"}, "duration": 24, "genres": ["Drama"], "coverImage": {"extraLarge": "p"}}}


class Pieces(unittest.TestCase):
    def test_episode_ranges(self):
        self.assertEqual(journal.episodes_of("10 - 13"), (10, 13))
        self.assertEqual(journal.episodes_of("5"), (5, 5))
        self.assertEqual(journal.episodes_of(None), (None, None))

    def test_pages_merge_without_duplicates(self):
        log = {"items": [], "media": {}}
        self.assertEqual(journal.merge_page(log, [act(3, 300), act(2, 200)]), 2)
        self.assertEqual(journal.merge_page(log, [act(2, 200), act(1, 100, "completed", None)]), 1)
        self.assertEqual([x["id"] for x in log["items"]], [3, 2, 1])
        self.assertEqual(log["items"][0]["from"], 3)
        self.assertEqual(log["media"]["7"]["title"], "Show")


class Reads(unittest.TestCase):
    def setUp(self):
        journal._path().unlink(missing_ok=True)
        self.addCleanup(lambda: journal._path().unlink(missing_ok=True))

    def test_no_account(self):
        with mock.patch.object(journal.anilist, "load_anilist_username", return_value=None):
            self.assertEqual(journal.journal()["state"], "none")

    def test_first_read_copies_the_log_then_answers_from_it(self):
        pages = {1: [act(5, 500), act(4, 400)]}
        def request(payload):
            if "User(" in payload["query"]:
                return {"data": {"User": {"id": 99}}}
            v = payload["variables"]
            acts = [] if v.get("before") else pages.get(v["page"], [])
            return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "activities": acts}}}
        with mock.patch.object(journal.anilist, "load_anilist_username", return_value="me"), \
             mock.patch.object(journal.anilist, "anilist_request", side_effect=request), \
             mock.patch.object(journal.threading, "Thread") as th:
            th.return_value.start.side_effect = lambda: journal.refresh(*th.call_args.kwargs["args"])
            self.assertEqual(journal.journal()["state"], "loading")
            out = journal.journal(since=450)
        self.assertEqual(out["state"], "ready")
        self.assertTrue(out["complete"])
        self.assertEqual([x["id"] for x in out["items"]], [5])  # since filters
        self.assertIn("7", out["media"])

    def test_older_pages_keep_activities_of_the_oldest_ones_second(self):
        """createdAt_lesser is strict: asking for what came before the oldest
        held one's second skipped its neighbours in that second."""
        log = {"user": "me", "uid": 1, "items": [], "media": {}, "complete": False, "ts": 0}
        journal.merge_page(log, [act(9, 900), act(8, 500)])
        journal._write(log)
        held = [act(9, 900), act(8, 500), act(7, 500), act(6, 400)]  # 7 shares 8's second

        def page(uid, page=1, before=None):
            if before is None:
                return [act(9, 900)], True  # nothing new on top
            return [a for a in held if a["createdAt"] < before], False
        with mock.patch.object(journal, "_page", side_effect=page):
            journal.refresh("me")
        log = journal._read()
        self.assertEqual([x["id"] for x in log["items"]], [9, 8, 7, 6])  # held ones not twice
        self.assertTrue(log["complete"])

    def test_a_page_of_only_held_ones_moves_on(self):
        log = {"user": "me", "uid": 1, "items": [], "media": {}, "complete": False, "ts": 0}
        journal.merge_page(log, [act(9, 900), act(8, 500)])
        journal._write(log)
        asked = []

        def page(uid, page=1, before=None):
            asked.append(before)
            if before is None:
                return [act(9, 900)], True
            if before == 501:
                return [act(8, 500)], True  # a full page of that second, all held
            return [act(5, 300)], False
        with mock.patch.object(journal, "_page", side_effect=page):
            journal.refresh("me")
        self.assertEqual(asked, [None, 501, 500])
        self.assertEqual([x["id"] for x in journal._read()["items"]], [9, 8, 5])

    def test_fresh_copy_starts_nothing(self):
        journal._write({"user": "me", "uid": 1, "items": [], "media": {}, "complete": True, "ts": time.time()})
        with mock.patch.object(journal.anilist, "load_anilist_username", return_value="me"), \
             mock.patch.object(journal, "_start") as start:
            self.assertEqual(journal.journal()["state"], "ready")
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
