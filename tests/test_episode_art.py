"""Offline regression checks for episode artwork and metadata cache fallbacks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import anilist, database, metadata
from animehub.config import config


class EpisodeArtTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()

    def test_duplicate_numbers_keep_first_thumbnail(self):
        parsed = metadata.parse_seps([
            {"title": "Episode 2 - First", "thumbnail": "first.jpg"},
            {"title": "Episode 2 - Duplicate", "thumbnail": "second.jpg"},
        ])
        self.assertEqual(parsed["thumbs"], {"2": "first.jpg"})
        self.assertEqual(parsed["titles"], {"2": "First"})

    def test_foreign_franchise_playlist_is_not_downloaded(self):
        media = {"id": 2, "episodes": 1, "streamingEpisodes": [
            {"title": "Episode 1", "thumbnail": "1.jpg"},
            {"title": "Episode 2", "thumbnail": "2.jpg"},
        ]}
        with patch.object(metadata, "episode_thumbs_meta") as download:
            self.assertEqual(metadata.media_episode_art(media), {"thumbs": None, "titles": None})
            download.assert_not_called()

    def test_partial_listing_keeps_available_episode_numbers(self):
        media = {"id": 1, "episodes": 12, "streamingEpisodes": [
            {"title": "Episode 2 - Available", "thumbnail": "2.jpg"},
        ]}
        with patch.object(metadata, "commit_thumbs", return_value={2: "/epimg/1.2.jpg"}):
            result = metadata.media_episode_art(media)
        self.assertEqual(result["thumbs"], {"2": "/epimg/1.2.jpg"})

    def test_failed_download_does_not_truncate_cached_art(self):
        dest = Path(self.directory.name) / "image.jpg"
        dest.write_bytes(b"cached image")
        with patch.object(metadata, "urlopen") as response:
            response.return_value.__enter__.return_value.read.side_effect = TimeoutError
            self.assertFalse(metadata.download("https://example.com/image.jpg", dest))
        self.assertEqual(dest.read_bytes(), b"cached image")

    def test_empty_search_result_is_cached(self):
        data = {"data": {"Page": {"media": []}}}
        with patch.object(anilist, "anilist_request", return_value=data) as request:
            self.assertEqual(metadata.fetch_media_candidates("Missing (TV)"), [])
            self.assertEqual(metadata.fetch_media_candidates("Missing (TV)"), [])
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
