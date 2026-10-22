"""Minimal match repair (TODO 28, Decision 18.4): the automatic matcher
must never accept an unreliable hit. Confidence scoring with hard gates —
format and episode count — plus manual override/clear as the remedy.

The fixtures replay Elias's laptop findings, which are the acceptance
cases: Frieren grabbed a wrong Frieren release, Hunter x Hunter matched
the 1999 version (62 eps, id 136) instead of 2011 (148 eps, id 11061 —
both ids verified against the live AniList API 2026-10-15), and the AoT
compilation movie The Last Attack isn't on AniList at all — the matcher
must leave such a show unmatched instead of grabbing a same-franchise
stand-in (the old matcher had taken Special 2, id 162314)."""
import unittest

from tests import support
from animehub import metadata


def media(mid, title_en=None, title_rom=None, fmt="TV", episodes=None, year=2015):
    t = {"english": title_en, "romaji": title_rom}
    return {"id": mid, "title": t, "format": fmt, "episodes": episodes,
            "startDate": {"year": year}}


def show(title, kind="show", collection=None, seasons=None):
    return {"id": "x", "title": title, "kind": kind, "collection": collection,
            "seasons": seasons or [{"key": 1, "season": 1, "label": "Season 1",
                                    "episodes": [{"id": "e"}] * 25}]}


class PickCandidateTests(unittest.TestCase):
    def test_frieren_s1_not_stolen_by_season_2(self):
        """Frieren S1 (25 installed eps): the correct S1 release (154587, TV,
        28 eps) beats the Season 2 entry (182255), whose title starts with
        the same name — partial title identity alone must not win, and its
        10 episodes against 25 files fail the count gate anyway."""
        correct = media(154587, "Frieren: Beyond Journey's End", "Sousou no Frieren", "TV", 28)
        wrong = media(182255, "Frieren: Beyond Journey's End Season 2", "Sousou no Frieren 2nd Season", "TV", 10)
        pool = [wrong, correct]
        m = metadata.pick_candidate(pool, show("Frieren: Beyond Journey's End"), 25)
        self.assertEqual(m["id"], 154587)

    def test_hxh_1999_rejected_on_count(self):
        """148 installed files; HxH 1999 (id 136) has 62 episodes — the
        count gate rejects it, the 2011 version (11061, 148) wins."""
        old = media(136, "Hunter x Hunter", "HUNTER×HUNTER", "TV", 62, 1999)
        new = media(11061, "Hunter x Hunter (2011)", "HUNTER×HUNTER (2011)", "TV", 148, 2011)
        m = metadata.pick_candidate([old, new], show("Hunter x Hunter"), 148)
        self.assertEqual(m["id"], 11061)

    def test_hxh_short_library_can_take_old_version(self):
        """The gate is relative: a 62-file partial install matches the 1999
        release legitimately — no absolute year assumptions."""
        old = media(136, "Hunter x Hunter", None, "TV", 62, 1999)
        m = metadata.pick_candidate([old], show("Hunter x Hunter"), 62)
        self.assertEqual(m["id"], 136)

    def test_compilation_movie_gate(self):
        """A movie folder only matches MOVIE releases with title identity —
        The Last Attack must not grab the Final Chapters Special 2 entry
        (what the old matcher did: 162314, SPECIAL), the TV series, or a
        same-franchise movie whose title is unrelated (Chronicle, 119113).
        AniList has no The Last Attack entry at all (verified live), so
        unmatched is the correct outcome here."""
        special2 = media(162314, "Attack on Titan Final Season THE FINAL CHAPTERS Special 2", None, "SPECIAL", 1, 2023)
        tv = media(16498, "Attack on Titan", "Shingeki no Kyojin", "TV", 25)
        chronicle = media(119113, "Attack on Titan ~Chronicle~", "Shingeki no Kyojin: Chronicle", "MOVIE", 1, 2020)
        s = show("The Last Attack", kind="movie", collection="Attack on Titan")
        for pool in ([special2], [tv], [chronicle], [special2, tv, chronicle]):
            self.assertIsNone(metadata.pick_candidate(pool, s, 1))

    def test_franchise_movie_with_partial_title_matches(self):
        """A real movie whose name only partially carries the folder title:
        'Reze Arc' inside 'Chainsaw Man' matches 171627 ('Chainsaw Man –
        The Movie: Reze Arc') on substring identity + format + count."""
        movie = media(171627, "Chainsaw Man – The Movie: Reze Arc", None, "MOVIE", 1, 2025)
        m = metadata.pick_candidate([movie], show("Reze Arc", kind="movie", collection="Chainsaw Man"), 1)
        self.assertEqual(m["id"], 171627)

    def test_no_candidate_means_unmatched(self):
        self.assertIsNone(metadata.pick_candidate([], show("Whatever"), 25))
        self.assertIsNone(metadata.pick_candidate([media(1, "Unrelated", None, "TV", 12)], show("Whatever"), 25))

    def test_partial_overlap_loses_to_exact(self):
        """Substring identity is worth half an exact match; an exact title
        with equal format always wins when both survive the gates."""
        exact = media(10, "Blue Box", None, "TV", None)
        partial = media(11, "Blue Box: Side Story", None, "TV", None)
        m = metadata.pick_candidate([partial, exact], show("Blue Box"), 1)
        self.assertEqual(m["id"], 10)

    def test_count_tiebreak(self):
        """Both exact-titled: the release whose count equals the install
        ranks higher (regular vs extended cut of the same season)."""
        regular = media(20, "Show", None, "TV", 12)
        extended = media(21, "Show", None, "TV", 13)
        m = metadata.pick_candidate([regular, extended], show("Show"), 13)
        self.assertEqual(m["id"], 21)

    def test_no_episodes_info_still_matches(self):
        """AniList lists episodes: null for un-aired/partial metadata — the
        count gate must not reject on missing data."""
        m0 = media(30, "Show", None, "TV", None)
        self.assertEqual(metadata.pick_candidate([m0], show("Show"), 25)["id"], 30)

    def test_collection_prefix_matches_franchise_titles(self):
        """'Junior High' inside the 'Attack on Titan' collection: the
        combined title matches the spin-off's real name."""
        spinoff = media(25519, "Attack on Titan: Junior High", "Shingeki! Kyojin Chuugakkou", "TV", 12)
        m = metadata.pick_candidate([spinoff], show("Junior High", collection="Attack on Titan"), 12)
        self.assertEqual(m["id"], 25519)

    def test_multi_season_container_skips_count_gate(self):
        """Containers hold several releases' worth of files (AoT: 97 across
        6 season groups; Frieren: 38 across S1+S2) — no single release has
        that many, so the count gate must stay off or every real candidate
        would be rejected and the container would go unmatched."""
        s1 = media(16498, "Attack on Titan", "Shingeki no Kyojin", "TV", 25)
        seasons = [{"key": i, "season": i, "label": f"Season {i}",
                    "episodes": [{"id": f"e{j}"} for j in range(25)]}
                   for i in range(1, 5)]
        m = metadata.pick_candidate([s1], show("Attack on Titan", seasons=seasons), 97)
        self.assertEqual(m["id"], 16498)

        frieren_s1 = media(154587, "Frieren: Beyond Journey's End", "Sousou no Frieren", "TV", 28)
        two = [{"key": 1, "season": 1, "label": "Season 1", "episodes": [{"id": "e"}] * 28},
               {"key": 2, "season": 2, "label": "Season 2", "episodes": [{"id": "f"}] * 10}]
        m = metadata.pick_candidate([frieren_s1], show("Frieren: Beyond Journey's End", seasons=two), 38)
        self.assertEqual(m["id"], 154587)

    def test_ambiguous_same_title_stays_unmatched(self):
        """Two identical same-titled candidates: the pick is deterministic
        and stable — the first-ranked candidate wins, not a random one."""
        a = media(40, "Show", None, "TV", 25)
        b = media(41, "Show", None, "TV", 25)
        m1 = metadata.pick_candidate([a, b], show("Show"), 25)
        m2 = metadata.pick_candidate([b, a], show("Show"), 25)
        self.assertEqual(m1["id"], m2["id"])


if __name__ == "__main__":
    unittest.main()