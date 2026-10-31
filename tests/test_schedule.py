"""The airing calendar's data (TODO 106): which shows, stated dates only,
and the cache/build states."""

import unittest
from unittest import mock

from tests import support  # noqa: F401  (isolated config/data first)
from animehub import schedule

LISTS = {"user": {"name": "me"}, "entries": [
    {"id": 1, "status": "CURRENT", "airing": "RELEASING"},
    {"id": 2, "status": "PLANNING", "airing": "NOT_YET_RELEASED"},
    {"id": 3, "status": "PLANNING", "airing": "FINISHED"},
    {"id": 4, "status": "DROPPED", "airing": "RELEASING"},
    {"id": 5, "status": "COMPLETED", "airing": "RELEASING"}]}


def answer(payload):
    query = payload["query"]
    if "airingSchedules" in query:
        return {"data": {"Page": {"pageInfo": {"hasNextPage": False},
                                  "airingSchedules": [{"mediaId": 1, "episode": 5, "airingAt": 100}]}}}
    return {"data": {"Page": {"media": [{"id": 2, "startDate": {"year": 2027, "month": 4, "day": 3}}]}}}


class Schedule(unittest.TestCase):
    def setUp(self):
        schedule._cache_path().unlink(missing_ok=True)
        self.addCleanup(lambda: schedule._cache_path().unlink(missing_ok=True))

    def test_only_active_shows_on_active_lists(self):
        self.assertEqual(schedule.shows(LISTS), [1, 2])

    def test_a_start_needs_an_exact_day(self):
        got = schedule.merge([], [{"id": 1, "startDate": {"year": 2027, "month": 4, "day": None}},
                                  {"id": 2, "startDate": {"year": 2027}}])
        self.assertEqual(got, [])  # never a guessed date

    def test_first_episode_on_the_schedule_replaces_the_start_day(self):
        got = schedule.merge([{"mediaId": 2, "episode": 1, "airingAt": 9}],
                             [{"id": 2, "startDate": {"year": 2027, "month": 4, "day": 3}}])
        self.assertEqual(got, [{"id": 2, "ep": 1, "at": 9}])

    def test_builds_once_then_serves_the_cache(self):
        with mock.patch.object(schedule.anilist, "load_anilist_username", return_value="me"), \
             mock.patch.object(schedule.anilist, "anilist_cache_stale", return_value=LISTS), \
             mock.patch.object(schedule.anilist, "anilist_request", side_effect=answer) as req, \
             mock.patch.object(schedule.threading, "Thread") as th:
            th.return_value.start.side_effect = lambda: schedule._build(*th.call_args.kwargs["args"])
            self.assertEqual(schedule.schedule()["state"], "loading")
            out = schedule.schedule()
        self.assertEqual(out["state"], "ready")
        self.assertEqual(out["items"], [{"id": 1, "ep": 5, "at": 100}, {"id": 2, "date": "2027-04-03"}])
        self.assertEqual(req.call_count, 2)

    def test_no_account(self):
        with mock.patch.object(schedule.anilist, "load_anilist_username", return_value=None):
            self.assertEqual(schedule.schedule(), {"state": "none", "items": []})
