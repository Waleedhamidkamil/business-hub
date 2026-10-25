"""The LPO record must be able to describe all three kinds of PO Waleed gets.

One flat total describes a materials PO and lies about the other two -
CSSP-OPEN-0001 prints "Total Amount USD 0.77", which is the UNIT price for
diesel, not a ceiling. Filing it as a total made the first invoice read
"Invoiced over LPO value".

Runs against temporary SQLite files, never the live database.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

NEW_COLUMNS = {
    "kind", "rows_json", "period_start", "period_end", "monthly_amount",
    "closed_at", "close_reason", "erp_no", "project", "contact",
}

# The shape the live Abraj database had before 2026-09-11, so the migration is
# tested against what it will actually meet.
OLD_TABLE = (
    "CREATE TABLE client_lpos ("
    " client_name TEXT NOT NULL COLLATE NOCASE,"
    " lpo_no TEXT NOT NULL COLLATE NOCASE, lpo_date TEXT,"
    " year INTEGER, month INTEGER, total REAL NOT NULL DEFAULT 0,"
    " currency TEXT NOT NULL DEFAULT 'USD',"
    " total_usd REAL NOT NULL DEFAULT 0, source_quote_no TEXT,"
    " file_path TEXT, ack_path TEXT, acknowledged_at TEXT,"
    " notes TEXT, received_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
    " PRIMARY KEY (client_name, lpo_no))"
)


class ClientLpoSchema(unittest.TestCase):
    def setUp(self):
        # Closed and removed by cleanup even when an assertion fails - an open
        # sqlite handle makes Windows refuse to delete the temp directory, which
        # buries the real failure under a PermissionError.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def connect(self, name):
        conn = sqlite3.connect(Path(self.tmp.name) / name)
        self.addCleanup(conn.close)
        return conn

    def columns(self, conn):
        return {r[1] for r in conn.execute("PRAGMA table_info(client_lpos)")}

    def test_a_fresh_database_has_every_new_column(self):
        from backend import database
        conn = self.connect("fresh.db")
        conn.executescript(database._SCHEMA)
        missing = NEW_COLUMNS - self.columns(conn)
        self.assertEqual(set(), missing,
                         f"a new database is missing {sorted(missing)}")

    def test_an_existing_database_is_migrated(self):
        """The live Abraj DB predates these columns and must gain them."""
        from backend import migrations
        conn = self.connect("old.db")
        conn.executescript(OLD_TABLE)
        conn.execute(
            "INSERT INTO client_lpos(client_name, lpo_no, total, currency,"
            " total_usd, received_at, updated_at)"
            " VALUES('HDEC','CSSP-ADM-0010',18390,'USD',18390,'x','x')")
        conn.commit()
        migrations._lpo_kinds_and_lines(conn)
        self.assertEqual(set(), NEW_COLUMNS - self.columns(conn))
        row = conn.execute("SELECT kind, rows_json FROM client_lpos").fetchone()
        self.assertEqual("materials", row[0],
                         "an LPO filed before kinds existed is a materials PO")
        self.assertIsNone(row[1], "no lines were known, so none are invented")

    def test_the_migration_is_idempotent(self):
        """run_all records applied keys, but the function itself must be safe."""
        from backend import database, migrations
        conn = self.connect("twice.db")
        conn.executescript(database._SCHEMA)
        migrations._lpo_kinds_and_lines(conn)
        migrations._lpo_kinds_and_lines(conn)     # must not raise

    def test_the_migration_is_registered_to_run(self):
        """A migration nobody runs is a comment."""
        from backend import migrations
        keys = [key for key, _ in migrations._REGISTRY]
        self.assertIn("2026-09-11_lpo_kinds_and_lines", keys)


if __name__ == "__main__":
    unittest.main(verbosity=2)
