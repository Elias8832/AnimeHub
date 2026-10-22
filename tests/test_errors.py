"""Failure messages: AniList errors reach the UI in the app's words, and an
outage costs one log line per background pass, not one per show."""

import io
import json
import socket
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from tests import support
from animehub import anilist, franchise


def http(code):
    return HTTPError("https://graphql.anilist.co", code, "Forbidden", None, None)


class FriendlyError(unittest.TestCase):
    def test_outage(self):
        down = "AniList is unreachable right now — try again later."
        for e in (http(403), http(500), http(503), URLError("no route"), socket.timeout("timed out"),
                  ConnectionResetError(), anilist.AniListTransientError("Could not reach AniList: x")):
            self.assertEqual(anilist.friendly_error(e), down, e)

    def test_rate_limit_and_missing(self):
        self.assertEqual(anilist.friendly_error(http(429)), "AniList is limiting requests — try again in a minute.")
        self.assertEqual(anilist.friendly_error(anilist.AniListTransientError("x", status=429)),
                         "AniList is limiting requests — try again in a minute.")
        self.assertEqual(anilist.friendly_error(http(404)), "AniList has no entry with this id.")

    def test_auth_keeps_its_reason(self):
        self.assertEqual(anilist.friendly_error(anilist.AniListAuthError("token expired — reconnect AniList")),
                         "token expired — reconnect AniList")

    def test_unexpected_is_logged(self):
        with mock.patch.object(anilist.util, "log") as log:
            self.assertEqual(anilist.friendly_error(KeyError("User")), "Something went wrong talking to AniList.")
        log.assert_called_once()


class Mutation(unittest.TestCase):
    """anilist_mutation's classification: an outage is transient (the
    outbox retries it), a rejected request is not."""

    def fail_with(self, code, message):
        body = io.BytesIO(json.dumps({"errors": [{"message": message}]}).encode())
        e = HTTPError("https://graphql.anilist.co", code, "x", None, body)
        with mock.patch.object(anilist, "load_token", return_value={"access_token": "t"}), \
                mock.patch.object(anilist, "anilist_request", side_effect=e):
            anilist.anilist_mutation("mutation{x}")

    def test_outage_is_transient(self):
        for code in (403, 429, 503):
            with self.assertRaises(anilist.AniListTransientError) as cm:
                self.fail_with(code, "The AniList API has been temporarily disabled due to severe stability issues.")
            self.assertEqual(cm.exception.status, code)
            self.assertNotEqual(anilist.friendly_error(cm.exception), "Something went wrong talking to AniList.")

    def test_bad_request_is_not(self):
        with self.assertRaises(ValueError):
            self.fail_with(400, "Validation error")


class Background(unittest.TestCase):
    def setUp(self):
        support.use_library(support.case("apothecary"))

    def test_user_data_refresh_survives_an_outage(self):
        with mock.patch.object(anilist, "anilist_user_data", side_effect=http(403)), \
                mock.patch.object(anilist.util, "log") as log:
            anilist.refresh_user_data()
        log.assert_called_once_with("AniList lists not refreshed: HTTP Error 403: Forbidden")

    def test_franchise_refresh_logs_once(self):
        def down(mid):
            raise http(403)
        with mock.patch.object(franchise.util, "log") as log:
            self.assertEqual(franchise.refresh_franchises({999991, 999992, 999993}, fetch=down), 0)
        log.assert_called_once_with("franchise refresh: 3 failed: HTTP Error 403: Forbidden")


if __name__ == "__main__":
    unittest.main()
