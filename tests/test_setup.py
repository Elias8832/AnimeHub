"""The ground the franchise work stands on: tests run isolated and offline,
the scanner reads every case's layout as expected, and the AniList
fixtures cover every franchise the cases walk."""

import unittest

from tests import support
from tests.support import hub


class Isolation(unittest.TestCase):
    def test_database_is_temporary(self):
        support.use_library(support.case("frieren"))
        self.assertTrue(hub.config.DB.is_relative_to(support.TMP))
        self.assertTrue(hub.config.LIBRARY.is_relative_to(support.TMP))

    def test_network_is_blocked(self):
        with self.assertRaises(AssertionError):
            hub.anilist_request({"query": "{Viewer{id}}"})


class Scan(unittest.TestCase):
    """What the scanner makes of each layout: the input the local mapping
    (phase 3) maps onto franchise entries."""

    def test_layouts(self):
        for c in support.cases():
            with self.subTest(case=c["name"]):
                self.assertEqual(support.scan_summary(support.use_library(c)), c["scan"])


class Fixtures(unittest.TestCase):
    def test_franchise_walks_are_recorded(self):
        """Every entry reachable over FRANCHISE_RELATIONS has a fixture, so
        franchise tests never need the network."""
        for c in support.cases():
            for start in c["anilist"]:
                seen, todo = set(), [start]
                while todo:
                    mid = todo.pop()
                    if mid in seen:
                        continue
                    seen.add(mid)
                    with self.subTest(case=c["name"], media=mid):
                        media = support.anilist(mid)
                        self.assertEqual(media["id"], mid)
                        for e in media["relations"]["edges"]:
                            node = e["node"]
                            if node["type"] != "ANIME":
                                continue
                            if e["relationType"] in hub.FRANCHISE_RELATIONS:
                                todo.append(node["id"])
                            elif e["relationType"] == "OTHER" and node["format"] in hub.FRANCHISE_OTHER_FORMATS:
                                self.assertEqual(support.anilist(node["id"])["id"], node["id"])


if __name__ == "__main__":
    unittest.main()
