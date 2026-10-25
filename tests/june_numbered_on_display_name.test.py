"""The four JUNE documents are numbered on the client's display name.

Waleed's ruling, 2026-09-11: "the full company name in the doc number ... is
messy and too much ... instead if we are using the display name which is just
JUNE it would be much better". `scripts/renumber_june_to_display_name_20260911.py`
moved the four documents that predated the rule.

This reads the LIVE Abraj database and changes nothing. It is here because the
renumber touched 29 stored values across 9 columns and two directory trees, and
a later restore, migration or re-save could quietly put the long form back.

What it does NOT assert: that every document in the app matches its client's
code. Three older Abraj quotations do not (`QUT-MT-Hanjun-041` for a client now
filed as "Hanjun Co.", and `QUT-MT-HDEC-041` / `QUT-MT-HDEC-059` whose client
names are "Hanjun Co." and "ABRAJ"), and Eshraqat numbers documents with no
client segment at all. Those predate the rule and Waleed has not asked for them.
"""
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.profile_guard import require_profile

APP_ROOT = Path(__file__).resolve().parents[1]
ABRAJ_DB = APP_ROOT / "data" / "profiles" / "abraj" / "po_tool.db"

LONG_FORM = "JUNE CONSTRUCTION CO."
RENUMBERED = (
    ("invoices", "inv_no", "INV-MT-JUNE-049"),
    ("quotes", "quote_no", "QUT-MT-JUNE-055"),
    ("quotes", "quote_no", "QUT-EQ-JUNE-065"),
    ("quotes", "quote_no", "QUT-MT-JUNE-066"),
)


class JuneNumberedOnDisplayName(unittest.TestCase):
    def setUp(self):
        require_profile("abraj")
        self.assertTrue(ABRAJ_DB.is_file(), f"Abraj database is missing: {ABRAJ_DB}")
        self.conn = sqlite3.connect(ABRAJ_DB.resolve().as_uri() + "?mode=ro", uri=True)
        self.addCleanup(self.conn.close)

    def _columns(self):
        """Every text column in the database, so nothing hides in a new table."""
        for (table,) in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"):
            for row in self.conn.execute(f'PRAGMA table_info("{table}")'):
                yield table, row[1]

    def test_no_document_number_carries_the_long_client_name(self):
        offenders = []
        for table, column in self._columns():
            # The PRINTED client name is a different thing and must not move -
            # see the test below.
            if column in ("client_name", "company", "name"):
                continue
            try:
                found = self.conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{column}" AS TEXT) LIKE ?',
                    (f"%{LONG_FORM}%",)).fetchone()[0]
            except sqlite3.Error:
                continue
            if found:
                offenders.append(f"{table}.{column} ({found})")
        self.assertEqual(offenders, [],
                         "these hold a document number built from the long company name, "
                         "which the renumber removed on 2026-09-11: " + ", ".join(offenders))

    def test_each_renumbered_document_is_there_with_its_file(self):
        for table, key, number in RENUMBERED:
            with self.subTest(number=number):
                row = self.conn.execute(
                    f'SELECT pdf_path FROM "{table}" WHERE "{key}"=?', (number,)).fetchone()
                self.assertIsNotNone(row, f"{number} is missing from {table}")
                path = Path(row[0])
                self.assertTrue(path.is_file(),
                                f"{number} points at a file that is not there: {path}")
                self.assertEqual(path.name, f"{number}.pdf")
                self.assertIn("JUNE", path.parts,
                              f"{number} is filed under {path.parent}, not the JUNE folder")
                self.assertNotIn(LONG_FORM, str(path))

    def test_the_printed_client_name_was_left_alone(self):
        """Renumbering is not renaming. The legal name still prints."""
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM invoices WHERE client_name=?", (LONG_FORM,)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM quotes WHERE client_name=?", (LONG_FORM,)).fetchone()[0], 3)

    def test_the_invoice_still_points_at_its_quotation(self):
        """Both sides moved in the same pass; the link has to have moved too."""
        link = self.conn.execute(
            "SELECT source_quote_no FROM invoices WHERE inv_no=?",
            ("INV-MT-JUNE-049",)).fetchone()
        self.assertIsNotNone(link, "INV-MT-JUNE-049 is missing")
        self.assertEqual(link[0], "QUT-MT-JUNE-066")
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM quotes WHERE quote_no=?", (link[0],)).fetchone(),
            "the invoice names a quotation that is not in the database")

    def test_a_new_june_document_would_be_numbered_the_same_way(self):
        """The rule, not just the repair: JUNE is what the next one gets too."""
        from backend import inv_numbering, dn_numbering, quote_numbering
        for module in (inv_numbering, dn_numbering, quote_numbering):
            with self.subTest(module=module.__name__):
                self.assertEqual(module._sanitize_client(LONG_FORM), "JUNE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
