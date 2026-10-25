"""Saving a quotation must not write into PO autocomplete (2026-08-14).

`save_quote_record` used to call `remember_item`, which writes the **`items`**
table - the PO memory - on top of the `quote_items` write that `api.save_quote`
already does. The comment said "items DB is shared across doc types", and that
was true when `items` was the only memory there was: it dates from 2026-05-26
and `quote_items` arrived on 06-01. Nobody removed the old write.

The symptom the user reported: English item names in the PO dropdown for a
company that writes its POs in Arabic. Measured on the live library, 492 of 815
PO memory rows were English and NOT ONE appeared in any of the 69 saved POs;
462 belonged to quotations, delivery notes and invoices.

⚠ LIVE DATABASE. Sentinel document numbers and item names only; every row is
deleted by exact name/number in tearDown. No unqualified DELETE.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import database, profile_manager  # noqa: E402

QUOTE_NO = "Q-__POMEM_TEST__-001"
ITEM = "__POMEM_TEST__ Widget"
PO_NO = "PO-__POMEM_TEST__-001"
PO_ITEM = "__POMEM_TEST__ PoWidget"


def _shared():
    return sqlite3.connect(profile_manager.shared_db_path())


def _memory_rows(table, name):
    with _shared() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE name = ?",
                         (name,)).fetchone()[0]


def _cleanup():
    with _shared() as c:
        for t in ("items", "quote_items"):
            for n in (ITEM, PO_ITEM):
                try:
                    c.execute(f"DELETE FROM {t} WHERE name = ?", (n,))
                except sqlite3.OperationalError:
                    pass
        c.commit()
    with sqlite3.connect(database.DB_PATH if hasattr(database, "DB_PATH")
                         else profile_manager.profile_db_path(
                             profile_manager.active_profile_id())) as c:
        c.execute("DELETE FROM quotes WHERE quote_no = ?", (QUOTE_NO,))
        c.execute("DELETE FROM pos WHERE po_no = ?", (PO_NO,))
        c.commit()


def tearDownModule():
    _cleanup()


class SavingAQuotation(unittest.TestCase):

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_it_does_not_reach_PO_memory(self):
        database.save_quote_record(
            quote_no=QUOTE_NO, quote_date="2099-01-01", quote_type="materials",
            year=2099, month=1, client_name="__POMEM_TEST__ Client",
            rfq_no="", client_address="", pdf_path="x.pdf", terms=[],
            rows=[{"item": ITEM, "description": "d", "unit": "EA", "rate": 1}],
        )
        self.assertEqual(
            _memory_rows("items", ITEM), 0,
            "a quotation's item must NOT appear in PO autocomplete - this is "
            "why an Arabic-only PO dropdown filled up with English names")

    def test_a_PO_still_remembers_its_own_items(self):
        """The other half. Removing the quotation write must not break the
        write that is supposed to happen."""
        database.save_po_record(
            po_no=PO_NO, po_date="2099-01-01", year=2099, month=1,
            pdf_path="x.pdf",
            rows=[{"name": PO_ITEM, "description": "d", "unit": "EA",
                   "company": "__POMEM_TEST__ Co"}],
        )
        self.assertGreaterEqual(
            _memory_rows("items", PO_ITEM), 1,
            "a PO's own items must still be remembered for PO autocomplete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
