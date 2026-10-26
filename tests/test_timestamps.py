"""Offline checks for timestamp normalization (READINESS B5, TODO 3): the
progress loop wrote ISO strings while watched-completion wrote SQL
datetime('now') strings; mixed formats sort wrong on the same day and break
the this-week cutoff. Everything is canonical UTC 'YYYY-MM-DD HH:MM:SS'."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import support
from animehub import database, library, player, util
from animehub.config import config

CANON = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def make_mixed_ts_db(db_path):
    """A v3 database carrying the historical mix: ISO last_played from the
    progress loop, SQL datetime('now') strings from watched-completion —
    including the B5 repro pair on the same day."""
    with support.sqlite(db_path) as c:
        c.execute("PRAGMA user_version = 3")
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
        c.execute("""CREATE TABLE curation(
            ref TEXT PRIMARY KEY, status TEXT, score REAL,
            favourite INTEGER DEFAULT 0, rewatches INTEGER DEFAULT 0,
            started_at TEXT, finished_at TEXT, prefs TEXT, updated_at TEXT)""")
        c.execute("CREATE TABLE tombstones(kind TEXT NOT NULL, key TEXT NOT NULL, ts TEXT,"
                  " PRIMARY KEY(kind, key))")
        c.execute("""CREATE TABLE catalog_cache(
            key TEXT PRIMARY KEY, payload TEXT, ts REAL)""")
        c.execute("""CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("INSERT INTO settings VALUES('library_root', '/tmp/lib')")
        # Same-day repro: ISO 10:00 must not sort above SQL 11:00.
        c.execute("INSERT INTO episodes(id,watched,last_played) VALUES"
                  " ('iso-10', 1, '2026-10-14T10:00:00+00:00')")
        c.execute("INSERT INTO episodes(id,watched,last_played) VALUES"
                  " ('sql-11', 1, '2026-10-14 11:00:00')")
        # Offset form converts to UTC (12:00+02:00 == 10:00Z).
        c.execute("INSERT INTO episodes(id,watched,last_played) VALUES"
                  " ('offset', 1, '2026-10-13T12:00:00+02:00')")
        c.execute("INSERT INTO tombstones VALUES('episode', 'e-gone',"
                  " '2026-10-01T08:30:00.123456+00:00')")


class TimestampTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "hub.db"
        self.database = patch.object(config, "DB", self.db_path)
        self.database.start()
        self.addCleanup(self.database.stop)
        library.scan_state.update(state="ok", walk_errors=0, root="ok")

    def test_mixed_timestamps_normalize_and_sort_correctly(self):
        make_mixed_ts_db(self.db_path)
        database.init_db()
        with support.sqlite(self.db_path) as c:
            rows = c.execute("SELECT id, last_played FROM episodes"
                             " ORDER BY last_played DESC").fetchall()
            version = c.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, len(database.MIGRATIONS))
        self.assertEqual(rows, [
            ("sql-11", "2026-10-14 11:00:00"),
            ("iso-10", "2026-10-14 10:00:00"),
            ("offset", "2026-10-13 10:00:00"),
        ])
        for _, lp in rows:
            self.assertRegex(lp, CANON)

    def test_tombstone_timestamps_normalize(self):
        make_mixed_ts_db(self.db_path)
        database.init_db()
        with support.sqlite(self.db_path) as c:
            ts = c.execute("SELECT ts FROM tombstones").fetchone()[0]
        self.assertEqual(ts, "2026-10-01 08:30:00")

    def test_migration_is_idempotent(self):
        make_mixed_ts_db(self.db_path)
        database.init_db()
        database.init_db()
        with support.sqlite(self.db_path) as c:
            rows = c.execute("SELECT id, last_played FROM episodes ORDER BY id").fetchall()
        self.assertEqual(rows, [
            ("iso-10", "2026-10-14 10:00:00"),
            ("offset", "2026-10-13 10:00:00"),
            ("sql-11", "2026-10-14 11:00:00"),
        ])

    def test_unparseable_timestamp_is_kept_verbatim(self):
        """A migration must never silently drop data it cannot parse."""
        self.assertEqual(util.normalize_timestamp("yesterday-ish"),
                         ("yesterday-ish", False))
        self.assertEqual(util.normalize_timestamp(None), (None, False))

    def test_normalize_helpers(self):
        self.assertEqual(util.normalize_timestamp("2026-10-14 10:00:00"),
                         ("2026-10-14 10:00:00", False))  # already canonical
        self.assertEqual(util.normalize_timestamp("2026-10-14T10:00:00Z"),
                         ("2026-10-14 10:00:00", True))
        self.assertEqual(util.normalize_timestamp("2026-10-14T10:00:00.500+00:00"),
                         ("2026-10-14 10:00:00", True))


class WriterFormatTests(unittest.TestCase):
    """Both last_played writers must emit the canonical form (READINESS B5:
    'a consistent UTC representation thereafter')."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = patch.object(config, "DB", Path(self.directory.name) / "hub.db")
        self.database.start()
        self.addCleanup(self.database.stop)
        library.scan_state.update(state="ok", walk_errors=0, root="ok")
        database.init_db()
        # No skip thread outlives the test: it reads the database, and
        # Windows can't delete a database that is still open.
        self.skips = patch.object(player, "send_skips")
        self.skips.start()
        self.addCleanup(self.skips.stop)

    def run_session(self, ep_id, position):
        """Drive watch_process with a fake mpv (same pattern as
        test_playback.DieBelowThresholdTests)."""
        sock = Path(self.directory.name) / "fake.sock"
        sock.write_text("")

        def fake_ipc(sock_path, cmd):
            if cmd[:2] == ["get_property", "time-pos"]:
                return {"data": position}
            if cmd[:2] == ["get_property", "duration"]:
                return {"data": 1440.0}
            return {}

        # A bare Mock would invent hub's session flags (a pty fd, a truthy hub_retired).
        proc = Mock(hub_pty=None, hub_retired=False)
        proc.poll.side_effect = [None, None]
        proc.wait.return_value = 0
        # The generation guard (TODO 24) only lets a watcher write when it
        # owns its slot — register the fake session like production does.
        with player._active_lock:
            player._active[ep_id] = {"proc": proc, "ep_id": ep_id,
                                  "show_title": "Show", "label": "S1 · E1",
                                  "percent": 0, "version": 0}
        with patch.object(player, "mpv_ipc", side_effect=fake_ipc):
            player.watch_process(proc, ep_id, sock)
        return sock

    def row(self, ep_id):
        with support.sqlite(config.DB) as c:
            return c.execute("SELECT watched, last_played FROM episodes WHERE id=?",
                             (ep_id,)).fetchone()

    def test_progress_loop_writes_canonical_timestamp(self):
        self.run_session("ep1", 100.0)  # dies below threshold: progress write only
        watched, lp = self.row("ep1")
        self.assertEqual(watched, 0)
        self.assertRegex(lp, CANON)

    def test_watched_write_matches_progress_format(self):
        """The B5 repro: the watched upsert (datetime('now')) and the progress
        loop (was isoformat) must produce the same shape, or same-day sorts
        break."""
        self.run_session("ep2", 1439.0)  # ~100%: watched write fires
        watched, lp = self.row("ep2")
        self.assertEqual(watched, 1)
        self.assertRegex(lp, CANON)

    def test_tombstones_written_in_canonical_format(self):
        scan = [{"id": f"s{i}", "title": f"S{i}", "collection": None, "kind": "show",
                 "seasons": [{"key": 1, "season": 1, "label": "Season 1",
                              "episodes": [{"id": f"e{i}"}]}]} for i in range(12)]
        library.merge_db(scan)
        with database.db() as c:
            c.execute("INSERT INTO episodes(id, watched) VALUES ('gone', 1)")
        library.merge_db(scan)
        with support.sqlite(config.DB) as c:
            ts = c.execute("SELECT ts FROM tombstones WHERE key='gone'").fetchone()[0]
        self.assertRegex(ts, CANON)


if __name__ == "__main__":
    unittest.main()
