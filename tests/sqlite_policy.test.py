"""Every SQLite file is opened ONE way (multi-user, 2026-10-22).

Several engines will write the same company database at once. SQLite's default
journal makes a second writer fail at once with "database is locked"; WAL lets
readers and one writer work side by side, and busy_timeout makes a second writer
WAIT (up to 30 s) instead of failing. A connection opened any other way would
quietly bring the failure back - so the third check forbids raw
sqlite3.connect() anywhere in backend/ except the helper itself.
"""
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import sqlite_policy  # noqa: E402


class SqlitePolicy(unittest.TestCase):
    def test_opens_in_wal_with_a_busy_wait(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite_policy.open_db(Path(td) / "x.db")
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                self.assertIs(conn.row_factory, sqlite3.Row)
            finally:
                conn.close()

    def test_read_only_uri_still_opens(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ro.db"
            sqlite_policy.open_db(path).close()
            conn = sqlite_policy.open_db(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
                self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            finally:
                conn.close()

    def test_maintenance_opens_keep_their_own_journal_and_wait(self):
        # Restore staging, migration locks and VACUUM work on files that are not
        # the live shared database; they must not be switched to WAL, and the
        # legacy-migration lock deliberately waits only 250 ms.
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite_policy.open_db(Path(td) / "m.db", wal=False, busy_ms=250)
            try:
                self.assertNotEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 250)
            finally:
                conn.close()

    def test_backup_sees_rows_still_in_the_wal(self):
        # A live WAL database is the .db file PLUS its -wal file. Zipping the
        # .db alone loses the newest saves - exactly what a backup is for.
        import zipfile
        from unittest.mock import patch
        from backend import config, data_manager
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            live, shared = td / "live.db", td / "library.db"
            held = []
            for db, value in ((live, 42), (shared, 7)):
                keep = sqlite_policy.open_db(db)       # left open: no checkpoint on close
                keep.execute("CREATE TABLE t(x)")
                keep.execute("INSERT INTO t VALUES (?)", (value,))
                keep.commit()
                held.append(keep)
            try:
                with patch.object(config, "DB_PATH", live), \
                     patch.object(config, "SETTINGS_PATH", td / "none.json"), \
                     patch.object(config, "ASSETS_DIR", td / "no-assets"), \
                     patch("backend.data_manager._doc_roots", return_value=[]), \
                     patch("backend.data_manager.profile_manager.shared_db_path", return_value=shared):
                    res = data_manager.create_backup(str(td))
            finally:
                for keep in held:
                    keep.close()
            self.assertTrue(res["ok"], res)
            with zipfile.ZipFile(res["path"]) as zf:
                for arc, value in (("profile/po_tool.db", 42), ("shared/library.db", 7)):
                    out = td / ("restored-" + arc.replace("/", "-"))
                    out.write_bytes(zf.read(arc))
                    c = sqlite3.connect(out)
                    try:
                        self.assertEqual(c.execute("SELECT x FROM t").fetchone()[0], value, arc)
                    finally:
                        c.close()

    def test_no_raw_connect_left_in_backend(self):
        offenders = []
        for py in sorted((ROOT / "backend").glob("*.py")):
            if py.name == "sqlite_policy.py":
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bsqlite3\.connect\(", line):
                    offenders.append(f"{py.name}:{i}")
        self.assertEqual(offenders, [], "open databases through sqlite_policy.open_db")


if __name__ == "__main__":
    unittest.main(verbosity=2)
