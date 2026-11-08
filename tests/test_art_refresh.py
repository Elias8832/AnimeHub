"""TODO 179: a saved banner follows AniList when AniList swaps it."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401
from animehub import anilist, database, metadata, upscale
from animehub.config import config

OLD, NEW = "https://s4.anilist.co/banner/1-old.jpg", "https://s4.anilist.co/banner/1-new.jpg"
COVER = "https://s4.anilist.co/cover/1.jpg"


def pic(tag):
    return b"\xff\xd8\xff" + tag.encode() * 50


class ArtRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=support.TMP))
        self.posters = self.tmp / "posters"
        self.posters.mkdir()
        for name, value in (("POSTERS", self.posters), ("DB", self.tmp / "hub.db"), ("UPSCALE_BANNERS", False)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        database.init_db()
        art = dict(upscale.art, changed=dict(upscale.art["changed"]))
        self.addCleanup(lambda: upscale.art.update(art))
        self.web = {OLD: pic("old"), NEW: pic("new"), COVER: pic("cover")}
        self.fetched = []

        def fetch(url):
            self.fetched.append(url)
            return self.web.get(url)
        p = patch.object(metadata, "_fetch_image", side_effect=fetch)
        p.start()
        self.addCleanup(p.stop)
        self.banner = self.posters / "s1.banner.jpg"

    def anilist_says(self, banner, mid=1):
        media = [{"id": mid, "bannerImage": banner, "coverImage": {"extraLarge": COVER}}]
        return patch.object(anilist, "anilist_request", return_value={"data": {"Page": {"media": media}}})

    def test_the_same_link_is_not_fetched_again(self):
        self.assertTrue(metadata.save_art(OLD, self.banner))
        self.assertTrue(metadata.save_art(OLD, self.banner))
        self.assertEqual(self.fetched, [OLD])

    def test_a_new_link_brings_the_new_picture(self):
        """Blue Box Season 2 kept its old banner after AniList changed it."""
        metadata.save_art(OLD, self.banner)
        with database.db() as c:
            c.execute("INSERT INTO shows_meta(show_id, anilist_id, banner) VALUES('s1', 1, '/img/s1.banner.jpg')")
        with self.anilist_says(NEW):
            metadata.refresh_art()
        self.assertEqual(self.banner.read_bytes(), pic("new"))

    def test_a_show_that_had_no_banner_gets_one(self):
        with database.db() as c:
            c.execute("INSERT INTO shows_meta(show_id, anilist_id, banner) VALUES('s1', 1, NULL)")
        with self.anilist_says(NEW):
            metadata.refresh_art()
        with database.db() as c:
            self.assertEqual(c.execute("SELECT banner FROM shows_meta").fetchone()[0], "/img/s1.banner.jpg")

    def test_a_linked_release_follows_too(self):
        meta = {"poster": "/img/stream-7.jpg", "banner": "/img/stream-7.banner.jpg"}
        with database.db() as c:
            c.execute("INSERT INTO stream_links(media_id, query, meta) VALUES(7, 'q', ?)", (json.dumps(meta),))
        (self.posters / "stream-7.banner.jpg").write_bytes(pic("old"))
        with self.anilist_says(NEW, mid=7):
            metadata.refresh_art()
        self.assertEqual((self.posters / "stream-7.banner.jpg").read_bytes(), pic("new"))

    def test_an_upscaled_banner_keeps_its_upscale_when_the_picture_is_the_same(self):
        """Saved before links were remembered: fetched once, compared, kept."""
        self.banner.write_bytes(pic("upscaled"))
        upscale.original_of(self.banner).write_bytes(pic("old"))
        self.assertTrue(metadata.save_art(OLD, self.banner))
        self.assertEqual(self.banner.read_bytes(), pic("upscaled"))

    def test_a_new_picture_drops_the_old_original(self):
        """Else switching upscaling off would bring the old banner back."""
        self.banner.write_bytes(pic("upscaled"))
        upscale.original_of(self.banner).write_bytes(pic("old"))
        self.assertTrue(metadata.save_art(NEW, self.banner))
        self.assertEqual(self.banner.read_bytes(), pic("new"))
        self.assertFalse(upscale.original_of(self.banner).exists())
        self.assertIn("s1.banner.jpg", upscale.art["changed"])

    def test_anilist_unreachable_keeps_the_saved_picture(self):
        self.banner.write_bytes(pic("old"))
        self.web.clear()
        self.assertTrue(metadata.save_art(NEW, self.banner))
        self.assertEqual(self.banner.read_bytes(), pic("old"))


if __name__ == "__main__":
    unittest.main()
