"""Franchise building (phase 2 of docs/franchise-page-plan.md): which
AniList entries share one page, and in which order. Skipped until
hub.build_franchise exists.

Contract: hub.build_franchise(start_id, fetch) returns the franchise's
entries (AniList media dicts) in page order; fetch(id) returns one entry
in hub.FRANCHISE_QUERY_ID's shape."""

import unittest

from tests import support
from tests.support import hub


@unittest.skipUnless(hasattr(hub, "build_franchise"), "phase 2: hub.build_franchise")
class Franchise(unittest.TestCase):
    def expected(self):
        for c in support.cases():
            for start, ids in (c.get("franchise") or {}).items():
                yield c["name"], int(start), ids

    def ids(self, start):
        return [m["id"] for m in hub.build_franchise(start, fetch=support.anilist)]

    def test_members_in_order(self):
        for name, start, ids in self.expected():
            with self.subTest(case=name, start=start):
                self.assertEqual(self.ids(start), ids)

    def test_same_page_from_every_entry(self):
        """Clicking any season, movie or special lands on the same page:
        the franchise is identical whichever entry it is built from."""
        for name, start, ids in self.expected():
            for mid in ids:
                with self.subTest(case=name, entry=mid):
                    self.assertEqual(self.ids(mid), ids)


if __name__ == "__main__":
    unittest.main()
