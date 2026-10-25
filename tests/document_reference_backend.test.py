"""Internal document Reference persistence and search (isolated DB)."""
from __future__ import annotations

import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations


class DocumentReferenceBackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_db_path = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "reference.db"

    def tearDown(self):
        config.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _init_new_schema(self):
        with database._connect() as conn:
            conn.executescript(database._SCHEMA)

    def test_new_and_existing_databases_have_reference_columns(self):
        self._init_new_schema()
        with database._connect() as conn:
            for table in ("pos", "quotes", "dns", "invoices"):
                with self.subTest(table=table):
                    columns = {r["name"]: r for r in conn.execute(
                        f"PRAGMA table_info({table})").fetchall()}
                    self.assertIn("reference", columns)
                    self.assertEqual(columns["reference"]["dflt_value"], "''")

        migrate = getattr(migrations, "_document_references", None)
        self.assertTrue(callable(migrate), "document-reference migration is missing")
        legacy = sqlite3.connect(":memory:")
        legacy.row_factory = sqlite3.Row
        try:
            for table in ("pos", "quotes", "dns", "invoices"):
                legacy.execute(f"CREATE TABLE {table} (doc_no TEXT)")
            migrate(legacy)
            migrate(legacy)  # idempotent on a partially/currently migrated DB
            for table in ("pos", "quotes", "dns", "invoices"):
                columns = {r["name"]: r for r in legacy.execute(
                    f"PRAGMA table_info({table})").fetchall()}
                self.assertIn("reference", columns)
                self.assertEqual(columns["reference"]["dflt_value"], "''")
        finally:
            legacy.close()

    def test_reference_round_trips_lists_and_searches_for_all_four_documents(self):
        self._init_new_schema()
        for fn in (
            database.save_po_record,
            database.save_quote_record,
            database.save_dn_record,
            database.save_inv_record,
        ):
            self.assertIn("reference", inspect.signature(fn).parameters)

        with patch.object(database, "best_effort_shared", return_value=True):
            database.save_po_record(
                po_no="PO-REF", po_date="1/1/2099", year=2099, month=1,
                pdf_path="po.pdf", rows=[{"name": "Pipe"}],
                reference="Warehouse restock alpha",
            )
            database.save_quote_record(
                quote_no="QUT-REF", quote_date="1/1/2099",
                quote_type="materials", year=2099, month=1,
                client_name="Client", rfq_no="N/A", client_address="",
                pdf_path="quote.pdf", rows=[{"item": "Pipe"}], terms=[],
                reference="Basra tender beta",
            )
            database.save_dn_record(
                dn_no="DN-REF", dn_date="1/1/2099", year=2099, month=1,
                client_name="Client", lpo_no="N/A", client_address="",
                include_prices=False, pdf_path="dn.pdf",
                rows=[{"item": "Pipe"}], reference="Gate delivery gamma",
            )
            database.save_inv_record(
                inv_no="INV-REF", inv_date="1/1/2099", inv_type="materials",
                year=2099, month=1, client_name="Client", lpo_no="N/A",
                client_address="", payment_bank_key="", withholding_included=True,
                grand_total=10, pdf_path="inv.pdf", rows=[{"item": "Pipe"}],
                reference="August settlement delta",
            )

        cases = (
            (database.load_po, "PO-REF", "Warehouse restock alpha",
             database.list_pos_in_db, "alpha", "po"),
            (database.load_quote, "QUT-REF", "Basra tender beta",
             database.list_quotes_in_db, "beta", "quote"),
            (database.load_dn, "DN-REF", "Gate delivery gamma",
             database.list_dns_in_db, "gamma", "dn"),
            (database.load_inv, "INV-REF", "August settlement delta",
             database.list_invoices_in_db, "delta", "inv"),
        )
        for load, number, reference, listing, search_word, kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(load(number)["reference"], reference)
                listed = listing(2099, 1)
                self.assertEqual(listed[0]["reference"], reference)
                match = database.search_documents(search_word, 10)
                self.assertEqual(len(match), 1)
                self.assertEqual(match[0]["doc"], kind)
                self.assertEqual(match[0]["no"], number)
                self.assertEqual(match[0]["snippet"], reference)


if __name__ == "__main__":
    unittest.main()
