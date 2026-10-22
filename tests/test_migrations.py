"""Offline checks for the versioned migration framework (READINESS B3):
old-DB fixtures, preservation of facts and catalog cache, idempotence, and
the WAL-safe pre-migration snapshot."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, library
from animehub.config import config


def make_old_online_db(db_path, with_thumb_columns=False):
    """A pre-strip database: online_* tables exist, user_version=1, holding
    AniList catalog cache keys, provider rows, and online watch facts.
    `with_thumb_columns` models a database whose init_db added the
    episode_thumbs/episode_titles columns ad hoc (13 columns total)."""
    thumb_cols = ", episode_thumbs TEXT, episode_titles TEXT" if with_thumb_columns else ""
    with sqlite3.connect(str(db_path)) as c:
        c.execute("PRAGMA user_version = 1")
        c.execute("""CREATE TABLE episodes(
            id TEXT PRIMARY KEY, watched INTEGER DEFAULT 0, position REAL DEFAULT 0,
            duration REAL DEFAULT 0, last_played TEXT)""")
        c.execute("""CREATE TABLE shows_meta(
            show_id TEXT PRIMARY KEY, anilist_id INTEGER, title TEXT, year INTEGER,
            score REAL, overview TEXT, poster TEXT, banner TEXT, episodes_meta TEXT,
            format TEXT, fetched_at TEXT)""")
        c.execute("""CREATE TABLE season_thumbs(
            show_id TEXT, sea_key TEXT, anilist_id INTEGER, thumbs TEXT, titles TEXT,
            PRIMARY KEY(show_id, sea_key))""")
        c.execute("CREATE TABLE online_cache(key TEXT PRIMARY KEY, payload TEXT, ts REAL)")
        c.execute(f"""CREATE TABLE online_shows(
            slug TEXT PRIMARY KEY, title TEXT, poster TEXT, banner TEXT, anilist_id INTEGER,
            overview TEXT, year INTEGER, score REAL, format TEXT, episodes_meta TEXT, fetched_at TEXT{thumb_cols})""")
        c.execute("CREATE TABLE online_linkups(ref TEXT PRIMARY KEY, slug TEXT, title TEXT, ts REAL)")
        # The old ad-hoc init_db created the spine tables before stamping v1.
        c.execute("""CREATE TABLE curation(
            ref TEXT PRIMARY KEY, status TEXT, score REAL,
            favourite INTEGER DEFAULT 0, rewatches INTEGER DEFAULT 0,
            started_at TEXT, finished_at TEXT, prefs TEXT, updated_at TEXT)""")
        c.execute("CREATE TABLE tombstones(kind TEXT NOT NULL, key TEXT NOT NULL, ts TEXT,"
                  " PRIMARY KEY(kind, key))")
        c.execute("INSERT INTO online_cache VALUES(?,?,100.0)",
                  ("media:v1:id:1", json.dumps({"id": 1, "title": {"english": "Old"}})))
        c.execute("INSERT INTO online_cache VALUES(?,?,100.0)", ("search:v2:provider", "{}"))
        if with_thumb_columns:
            c.execute("INSERT INTO online_shows(slug,title,episode_thumbs,episode_titles)"
                      " VALUES('old-show','Old Show','{\"1\":\"/e/1.jpg\"}','{\"1\":\"One\"}')")
        else:
            c.execute("INSERT INTO online_shows(slug,title) VALUES('old-show','Old Show')")
        c.execute("INSERT INTO online_linkups VALUES('aid:1','old-show','Old Show',1.0)")
        c.execute("INSERT INTO episodes(id,watched,position,duration,last_played)"
                  " VALUES('online:old-show:3',1,50,1200,'2026-01-01T00:00:00+00:00')")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "hub.db"
        self.database = patch.object(config, "DB", self.db_path)
        self.database.start()
        self.addCleanup(self.database.stop)

    def test_old_db_migration_preserves_catalog_and_facts(self):
        make_old_online_db(self.db_path)
        database.init_db()
        with sqlite3.connect(str(self.db_path)) as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            media = c.execute("SELECT payload FROM catalog_cache WHERE key='media:v1:id:1'").fetchone()
            provider_key = c.execute("SELECT 1 FROM catalog_cache WHERE key='search:v2:provider'").fetchone()
            fact = c.execute("SELECT watched, position FROM episodes WHERE id='online:old-show:3'").fetchone()
            archived = c.execute("SELECT title FROM retired_online_shows WHERE slug='old-show'").fetchone()
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(version, len(database.MIGRATIONS))
        self.assertEqual(media[0], json.dumps({"id": 1, "title": {"english": "Old"}}))
        self.assertIsNone(provider_key)  # provider-side search cache is gone
        self.assertEqual(fact, (1, 50.0))  # viewing facts are never deleted
        self.assertEqual(archived[0], "Old Show")  # metadata archived, not lost
        self.assertFalse(tables & {"online_cache", "online_shows", "online_linkups"})

    def test_old_db_with_thumb_columns_archives_them(self):
        """The 13-column variant (old init_db added episode_thumbs/episode_titles
        ad hoc) must migrate too — the archive mirrors the source's columns."""
        make_old_online_db(self.db_path, with_thumb_columns=True)
        database.init_db()
        with sqlite3.connect(str(self.db_path)) as c:
            archived = c.execute("SELECT title, episode_thumbs, episode_titles"
                                 " FROM retired_online_shows WHERE slug='old-show'").fetchone()
            fact = c.execute("SELECT watched, position FROM episodes"
                             " WHERE id='online:old-show:3'").fetchone()
        self.assertEqual(archived, ("Old Show", '{"1":"/e/1.jpg"}', '{"1":"One"}'))
        self.assertEqual(fact, (1, 50.0))

    def test_migration_is_idempotent(self):
        make_old_online_db(self.db_path)
        database.init_db()
        database.init_db()
        database.init_db()
        with sqlite3.connect(str(self.db_path)) as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            archived = c.execute("SELECT COUNT(*) FROM retired_online_shows").fetchone()[0]
            cached = c.execute("SELECT COUNT(*) FROM catalog_cache").fetchone()[0]
        self.assertEqual(version, len(database.MIGRATIONS))
        self.assertEqual(archived, 1)
        self.assertEqual(cached, 1)

    def test_fresh_db_never_has_online_tables(self):
        database.init_db()
        with sqlite3.connect(str(self.db_path)) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = c.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, len(database.MIGRATIONS))
        self.assertNotIn("retired_online_shows", tables)  # nothing to archive
        self.assertFalse(tables & {"online_cache", "online_shows", "online_linkups"})
        self.assertIn("catalog_cache", tables)

    def test_snapshot_taken_before_migration(self):
        """A pre-migration backup exists and still contains the pre-strip
        state, so a failed migration is recoverable (READINESS B3)."""
        make_old_online_db(self.db_path)
        database.init_db()
        backup = Path(self.directory.name) / "hub.db.backup-v1"
        self.assertTrue(backup.exists())
        with sqlite3.connect(str(backup)) as c:
            self.assertIsNotNone(
                c.execute("SELECT 1 FROM sqlite_master WHERE name='online_shows'").fetchone())

    def test_no_snapshot_for_fresh_db(self):
        database.init_db()
        self.assertEqual(list(Path(self.directory.name).glob("hub.db.backup-*")), [])


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        database.init_db()

    def test_cache_roundtrip(self):
        database.cache_put("media:v1:id:9", {"id": 9})
        self.assertEqual(database.cache_get("media:v1:id:9", 60), {"id": 9})
        self.assertIsNone(database.cache_get("media:v1:id:8", 60))
        self.assertIsNone(database.cache_get("media:v1:id:9", -1))  # expired

    def test_library_payload_has_legacy_empty_online_fields(self):
        empty = Path(self.directory.name) / "empty-library"
        empty.mkdir()
        with patch.object(config, "LIBRARY", empty):
            data = library.full_library()
        self.assertEqual(data["continue_online"], [])
        self.assertEqual(data["history_online"], [])
        # An empty (missing-looking) library must not tombstone anything —
        # the MIN_VALID_SCAN floor in merge_db keeps rows intact.
        self.assertEqual(data["tombstones"], {"episode": 0, "show": 0, "season": 0})


class ScanStateTests(unittest.TestCase):
    """TODO 23: the scanner's health surface — missing root, unreadable
    directories (partial), loose root videos, and the root-changed guard."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        library.scan_state.update(state="ok", walk_errors=0, root="ok")
        database.init_db()

    def scan(self, **kw):
        lib = Path(self.directory.name) / "lib"
        return patch.object(config, "LIBRARY", lib, **kw)

    def test_missing_root_reports_state_not_crash(self):
        with self.scan():
            self.assertEqual(library.scan_library(), [])
        self.assertEqual(library.scan_state["state"], "missing")

    def test_unreadable_dir_reports_partial(self):
        lib = Path(self.directory.name) / "lib"
        show = lib / "Show" / "Season 1"
        show.mkdir(parents=True)
        (show / "E01.mp4").write_bytes(b"x")
        locked = lib / "Locked"
        locked.mkdir()
        os.chmod(locked, 0o000)
        try:
            with patch.object(config, "LIBRARY", lib):
                library.scan_library()
                titles = [s["title"] for s in library.scan_library()]
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual(library.scan_state["state"], "partial")
        self.assertGreaterEqual(library.scan_state["walk_errors"], 1)
        # The readable show is still indexed despite the locked sibling.
        self.assertIn("Show", titles)

    def test_root_level_video_becomes_one_file_show(self):
        """Was the IndexError crash (READINESS B2): a video loose in the root
        now indexes as a one-episode show with a namespaced id."""
        lib = Path(self.directory.name) / "lib"
        lib.mkdir(parents=True)
        (lib / "OVA Special.mp4").write_bytes(b"x")
        (lib / "Show").mkdir()
        (lib / "Show" / "E01.mp4").write_bytes(b"x")
        with patch.object(config, "LIBRARY", lib):
            scan = library.scan_library()
        loose = [s for s in scan if s["path"].startswith("OVA")]
        self.assertEqual(len(loose), 1)
        self.assertEqual(loose[0]["title"], "OVA Special")
        self.assertEqual(loose[0]["seasons"][0]["episodes"][0]["number"], 1)
        self.assertEqual(library.scan_state["state"], "ok")

    def test_loose_show_id_cannot_collide_with_folder_show(self):
        """A folder later claiming the loose file's stem must not reuse (or be
        shadowed by) the loose entry's id."""
        lib = Path(self.directory.name) / "lib"
        lib.mkdir(parents=True)
        (lib / "OVA Special.mp4").write_bytes(b"x")
        (lib / "OVA Special").mkdir()
        (lib / "OVA Special" / "E01.mp4").write_bytes(b"x")
        with patch.object(config, "LIBRARY", lib):
            scan = library.scan_library()
        loose = [s for s in scan if s["path"] == "OVA Special.mp4"]
        folder = [s for s in scan if s["path"] == "OVA Special"]
        self.assertEqual(len(loose), 1)
        self.assertEqual(len(folder), 1)
        self.assertNotEqual(loose[0]["id"], folder[0]["id"])

    def test_s01e02_filename_keeps_its_number(self):
        """Was the fallback renumbering trap (READINESS B2): 'S01E02.mkv' now
        parses as episode 2 instead of falling into lexical ordering."""
        lib = Path(self.directory.name) / "lib"
        show = lib / "Show"
        show.mkdir(parents=True)
        for name in ("Show S01E01.mkv", "Show S01E02.mkv", "Show S01E10.mkv"):
            (show / name).write_bytes(b"x")
        with patch.object(config, "LIBRARY", lib):
            scan = library.scan_library()
        eps = scan[0]["seasons"][0]["episodes"]
        self.assertEqual([e["number"] for e in eps], [1, 2, 10])

    def test_root_changed_is_detected_and_never_tombstones(self):
        lib = Path(self.directory.name) / "lib"
        show = lib / "Show"
        show.mkdir(parents=True)
        (show / "E01.mp4").write_bytes(b"x")
        with patch.object(config, "LIBRARY", lib):
            library.scan_library()
            library.merge_db(library.scan_library())
        with database.db() as c:
            c.execute("INSERT INTO shows_meta(show_id, title) VALUES ('gone', 'Gone')")
            c.execute("INSERT INTO episodes(id, watched, position) VALUES ('gone-e1', 1, 60)")
        # New root: same database, different library location → ids shift.
        other = Path(self.directory.name) / "other"
        other.mkdir()
        with patch.object(config, "LIBRARY", other):
            scan = library.scan_library()
            self.assertEqual(library.scan_state["root"], "changed")
            library.merge_db(scan)
        with database.db() as c:
            tombstones = c.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0]
            fact = c.execute("SELECT watched FROM episodes WHERE id='gone-e1'").fetchone()
            meta = c.execute("SELECT title FROM shows_meta WHERE show_id='gone'").fetchone()
        self.assertEqual(tombstones, 0)
        self.assertEqual(fact, (1,))    # facts survive a root change untouched
        self.assertEqual(meta, ("Gone",))
        # And the payload surfaces it for the UI/config flow.
        with patch.object(config, "LIBRARY", other):
            data = library.full_library()
        self.assertEqual(data["scan"]["root"], "changed")
        self.assertEqual(data["scan"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
