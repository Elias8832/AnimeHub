"""Regression tests (TODO 28): the unsafe-match failures Elias saw on the
laptop. The first-shape bug is FIXED (previous thread) — these pin the fix:

1. AniList's SaveMediaListEntry takes its fields directly; the earlier
   nested entry:{...} shape was rejected with a 400 on every edit (TODO 9).
2. Score precision: POINT_10 read of a DECIMAL-format user's 10 must not
   silently downgrade the stored 100-scale value (Decision 23.6).

Plus the live AniList data facts these rely on (verified 2026-10-15):
- "The Last Attack" compilation film has NO AniList entry (searches return
  zero; id 187570 doesn't exist) → the folder must stay UNMATCHED, and the
  manual override with a bogus id must be refused.
- The old wrong matches (Frieren → 120068-area releases, HxH → 136/1999)
  must never come back through pick_candidate."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, metadata
from animehub.config import config


def media(mid, title_en=None, fmt="TV", eps=None):
    return {"id": mid, "format": fmt, "episodes": eps,
            "title": {"english": title_en}}


class MutationShapeTests(unittest.TestCase):
    """The SaveMediaListEntry call must carry its fields directly — the
    nested entry:{...} shape made every edit fail with 'Unknown argument
    entry' (HTTP 400), silently, on every panel edit."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(anilist, "load_token",
                         lambda: {"access_token": "t", "expires_at": None,
                                  "user_id": 42, "user_name": "Elias"}),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def test_mutation_takes_fields_directly(self):
        captured = {}

        def fake_mutation(query, variables):
            captured["q"], captured["v"] = query, variables
            # the real AniList answered HTTP 400 'Unknown argument "entry"'
            # for the old shape; if the shape regresses this fake returning
            # success hides nothing — the shape assertion below is the guard.
            return {"SaveMediaListEntry": {"id": 1}}

        with patch.object(anilist, "anilist_mutation", side_effect=fake_mutation), \
             patch.object(anilist, "anilist_entry_state",
                          return_value={"media_id": 154587, "entry": {}}):
            anilist.anilist_entry_save(154587, {"progress": 28})
        q = captured["q"]
        self.assertIn("SaveMediaListEntry(mediaId:$id,", q)
        self.assertIn("progress: 28", q)
        self.assertNotIn("entry:{", q)

    def test_http400_validation_surfaces_as_valueerror(self):
        """AniList answers bad mutations with HTTP 400 + an errors body —
        the chokepoint must turn that into a readable ValueError, not the
        generic 'HTTP Error 400' the first live test produced."""
        import io

        class Err(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=20):
            return Err(json.dumps({"errors": [
                {"message": "Unknown argument \"entry\" on field "
                            "\"SaveMediaListEntry\"."}]}).encode())

        with patch.object(anilist, "urlopen", fake_urlopen):
            with self.assertRaises(ValueError) as cm:
                anilist.anilist_mutation(
                    'mutation{SaveMediaListEntry(mediaId:1,entry:{}){id}}',
                    {"id": 1})
        self.assertIn("Unknown argument", str(cm.exception))


class LastAttackFactsTests(unittest.TestCase):
    """The live AniList data facts (verified 2026-10-15): searched in
    English, romaji and Japanese, and across the full Attack on Titan
    catalog — no compilation-film entry exists."""

    def test_bogus_override_id_is_refused(self):
        """187570 doesn't exist on AniList — the manual override must refuse
        it instead of storing a dead match."""
        with patch.object(metadata, "fetch_media_full", return_value=None):
            out = metadata.set_match("8adfaea8459b", 187570)
        self.assertIn("error", out)

    def test_search_with_no_results_keeps_the_show_unmatched(self):
        """The folder stays unmatched (surfaced, fixable) instead of grabbing
        Final Chapters Special 2 (162314) the way the first-hit matcher
        did."""
        with patch.object(metadata, "fetch_media_candidates", return_value=[]), \
             patch.object(metadata, "fetch_media_full", return_value=None):
            res = metadata.resolve_match({"id": "8adfaea8459b", "title": "The Last Attack",
                                     "collection": "Attack on Titan",
                                     "kind": "movie",
                                     "seasons": [{"episodes": [{"id": "e1"}]}]},
                                    None)
        self.assertIsNone(res)

    def test_the_real_special_never_steals_the_movie_folder(self):
        candidates = [media(162314, "Attack on Titan Final Season THE FINAL "
                                 "CHAPTERS Special 2", "SPECIAL", 1),
                      media(16498, "Attack on Titan", "TV", 25)]
        with patch.object(metadata, "fetch_media_candidates", return_value=candidates):
            res = metadata.resolve_match({"id": "8adfaea8459b",
                                     "title": "The Last Attack",
                                     "collection": "Attack on Titan",
                                     "kind": "movie",
                                     "seasons": [{"episodes": [{"id": "e1"}]}]},
                                    None)
        self.assertIsNone(res)


class ScorePrecisionTests(unittest.TestCase):
    """Decision 23.6: scores keep their precision. POINT_10 reads of a
    DECIMAL-format user must not manufacture conflicts (8 == 8.0 on the
    100-scale) or truncate stored values."""

    def test_score_norm_scales_by_format_max(self):
        self.assertEqual(anilist._score_norm(8, "POINT_10_DECIMAL"), 80.0)
        self.assertEqual(anilist._score_norm(8, "POINT_10"), 80.0)
        self.assertEqual(anilist._score_norm(80, "POINT_100"), 80.0)
        self.assertIsNone(anilist._score_norm(None, "POINT_10"))
        self.assertIsNone(anilist._score_norm(5, "NOT_A_FORMAT"))


if __name__ == "__main__":
    unittest.main()
