import importlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import profile_manager
from backend.profile_manager import ProfileManager


YEAR = 2026
MONTH = 7


def quote_row() -> dict:
    return {
        "item": "Shared Pipe",
        "description": "Steel pipe",
        "unit": "m",
        "qty": "2",
        "rate": "5",
    }


def main() -> None:
    original_manager = profile_manager._MANAGER
    test_dir = Path(__file__).resolve().parent
    try:
        with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
            root = Path(td)
            manager = ProfileManager(root / "data", root / "static")
            profile_manager._MANAGER = manager
            manager.create_registry("abraj")

            from backend import (analytics, clients, config, data_manager,
                                 database, master_items)
            importlib.reload(config)
            importlib.reload(database)
            importlib.reload(clients)
            importlib.reload(analytics)
            importlib.reload(data_manager)

            def switch(profile_id: str) -> None:
                manager.activate(profile_id)
                importlib.reload(config)
                database.init_db()

            switch("abraj")
            database.remember_item("Shared Pipe", "Steel pipe", "m", "HDEC")
            added = clients.add_client("Shared Catalog Only")
            assert added["ok"] is True
            database.save_quote_record(
                quote_no="QUT-A",
                quote_date="1/7/2026",
                quote_type="materials",
                year=YEAR,
                month=MONTH,
                client_name="HDEC",
                rfq_no="RFQ-A",
                client_address="CSSP Site",
                pdf_path=root / "qut-a.pdf",
                rows=[quote_row()],
                terms=[],
            )
            database.set_submitted("quote", "QUT-A", True)
            database.save_doc_costs("quote", "QUT-A", costs=[3], currency="IQD")
            database.set_doc_columns("quote", "QUT-A", [
                {"key": "item", "head": "Abraj Item", "kind": "text", "width": 100},
            ])
            database.delete_document_record("quote", "QUT-DELETED-A")
            database.save_inv_record(
                inv_no="INV-A",
                inv_date="1/7/2026",
                inv_type="materials",
                year=YEAR,
                month=MONTH,
                client_name="HDEC",
                lpo_no="LPO-A",
                client_address="CSSP Site",
                payment_bank_key="cash",
                withholding_included=True,
                grand_total=10,
                pdf_path=root / "inv-a.pdf",
                rows=[quote_row()],
            )
            database.set_invoice_paid("INV-A", True)

            switch("eshraqat")
            assert any(row["name"] == "Shared Pipe" for row in database.suggest_items("Shared"))
            assert any(row["name"] == "Shared Catalog Only" for row in database.suggest_clients(""))
            # The SHARED item catalogue must be visible from the other profile.
            # This used to read the `cat_po_items` Data Manager section, whose
            # table (`items`) was dropped on 2026-08-20 - so this assertion had
            # been raising `no such table` ever since, and the whole file was
            # written off as a known failure. It was not noise: it was pointing
            # at ten dead Data Manager sections that were still listed on screen
            # and threw when opened. Repointed at `master_items`, which IS the
            # shared item catalogue now (2026-09-05).
            found = master_items.list_records(search="Shared Pipe")
            assert found["total"] >= 1, "the shared item record did not cross profiles"
            assert any("Shared Pipe" in (r.get("name_en") or "") or
                       "Shared Pipe" in (r.get("name") or "")
                       for r in found["rows"]), found["rows"][:3]

            assert database.list_quotes_in_db(YEAR, MONTH) == []
            assert database.is_submitted("quote", "QUT-A") is False
            assert database.load_doc_costing("quote", "QUT-A") is None
            assert database.get_cost_currency("quote", "QUT-A") == "USD"
            assert database.is_invoice_paid("INV-A") is False
            assert database.get_doc_columns("quote", "QUT-A") is None
            assert "QUT-DELETED-A" not in database.deleted_document_numbers("quote")

            database.save_quote_record(
                quote_no="QT-0001",
                quote_date="2/7/2026",
                quote_type="materials",
                year=YEAR,
                month=MONTH,
                client_name="HDEC",
                rfq_no="RFQ-E",
                client_address="CSSP Site",
                pdf_path=root / "qt-0001.pdf",
                rows=[quote_row()],
                terms=[],
            )
            database.save_doc_costs("quote", "QT-0001", costs=[2], currency="USD")
            database.save_inv_record(
                inv_no="IV-0001",
                inv_date="2/7/2026",
                inv_type="materials",
                year=YEAR,
                month=MONTH,
                client_name="HDEC",
                lpo_no="LPO-E",
                client_address="CSSP Site",
                payment_bank_key="cash",
                withholding_included=True,
                grand_total=10,
                pdf_path=root / "iv-0001.pdf",
                rows=[quote_row()],
            )
            database.set_invoice_paid("IV-0001", True)
            assert {q["quote_no"] for q in database.list_quotes_in_db(YEAR, MONTH)} == {"QT-0001"}
            assert clients.client_profile("HDEC")["summary"]["quotes"] == 1
            assert analytics.dashboard_analytics(3, YEAR, MONTH)["totals"]["quotes"] == 1
            assert database.load_doc_costing("quote", "QT-0001")["costed"] is True
            assert database.is_invoice_paid("IV-0001") is True
            costing = database.list_docs_for_costing(YEAR, MONTH)
            assert {row["doc_no"] for row in costing if row["kind"] == "quote"} == {"QT-0001"}
            attention = database.dashboard_needs_attention(YEAR, MONTH)
            assert all(
                row.get("doc_no") != "QUT-A"
                for bucket in attention.values() if isinstance(bucket, list)
                for row in bucket
            )

            switch("abraj")
            assert {q["quote_no"] for q in database.list_quotes_in_db(YEAR, MONTH)} == {"QUT-A"}
            assert database.is_submitted("quote", "QUT-A") is True
            assert database.load_doc_costing("quote", "QUT-A")["cost_currency"] == "IQD"
            assert database.is_invoice_paid("INV-A") is True
            assert database.is_invoice_paid("IV-0001") is False
            assert database.get_doc_columns("quote", "QUT-A")[0]["head"] == "Abraj Item"
            assert "QUT-DELETED-A" in database.deleted_document_numbers("quote")
            assert clients.client_profile("HDEC")["summary"]["quotes"] == 1
            assert analytics.dashboard_analytics(3, YEAR, MONTH)["totals"]["quotes"] == 1
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("profile data isolation backend tests passed")


if __name__ == "__main__":
    main()
