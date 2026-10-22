"""History identity (TODO 25, revised after Elias's feedback 2026-10-15):
migration 5 gives episodes a stored show identity (`show_id` + human
`label` — ids are path hashes, so a removed file's row used to be an
anonymous number), and the scan merge stamps every row with watch facts.
The full history ships inline in /api/library (75 rows here — one payload
beats pagination at this size; server-side paging can return if libraries
grow into the thousands), and rows without live context are left out of
the History view rather than crash-rendered (Decision 17.3: the facts
stay in the database either way)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support
from animehub import database, library
from animehub.config import config


class StampingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(config, "DB", Path(self.directory.name) / "hub.db"),
            patch.object(config, "LIBRARY", None),
        ]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def test_merge_stamps_played_rows_only(self):
        """The scan stamps identity onto rows that carry watch facts; files
        nobody ever played get no row (history is about watching)."""
        scan = [{"id": "showX", "title": "Show X", "collection": None, "kind": "show",
                 "seasons": [{"key": 1, "season": 1, "label": "Season 1", "episodes": [
                     {"id": "played1", "number": 3, "watched": True, "position": 0,
                      "duration": 0, "last_played": "2026-01-01 00:00:00"},
                     {"id": "fresh1", "number": 4, "watched": False, "position": 0,
                      "duration": 0, "last_played": None}]}]}]
        with database.db() as c:
            c.execute("INSERT INTO episodes(id,watched,last_played) VALUES('played1',1,'2026-01-01 00:00:00')")
        library.merge_db(scan)
        with database.db() as c:
            row = c.execute("SELECT show_id, label FROM episodes WHERE id='played1'").fetchone()
            none = c.execute("SELECT COUNT(*) FROM episodes WHERE id='fresh1'").fetchone()[0]
        self.assertEqual(row, ("showX", "S1E3"))
        self.assertEqual(none, 0)


class Migration5Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "hub.db"
        self.patches = [patch.object(config, "DB", self.db)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def test_v4_database_gains_the_columns(self):
        """A pre-TODO-25 database (user_version=4, no identity columns)
        upgrades cleanly and keeps its rows."""
        conn = __import__("sqlite3").connect(self.db)
        for m in database.MIGRATIONS[:4]:
            m(conn.cursor())
        conn.execute("PRAGMA user_version = 4")
        conn.execute("INSERT INTO episodes(id,watched,last_played) VALUES('e1',1,'2026-01-01 00:00:00')")
        conn.commit()
        conn.close()
        database.init_db()
        conn = __import__("sqlite3").connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
        self.assertIn("show_id", cols)
        self.assertIn("label", cols)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         len(database.MIGRATIONS))
        self.assertEqual(conn.execute("SELECT id FROM episodes").fetchall(), [("e1",)])
        conn.close()

    def test_migration_is_idempotent(self):
        database.init_db()
        database.init_db()  # second run must not re-alter or traceback
        conn = __import__("sqlite3").connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
        self.assertIn("show_id", cols)
        conn.close()


if __name__ == "__main__":
    unittest.main()
