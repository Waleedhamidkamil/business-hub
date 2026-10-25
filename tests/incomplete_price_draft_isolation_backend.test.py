"""App-only pricing drafts stay out of financial and workflow state."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import analytics, clients, config, database, insights


def main() -> None:
    old_db, old_data = config.DB_PATH, config.DATA_DIR
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        config.DATA_DIR = root
        config.DB_PATH = root / "draft-isolation.db"
        try:
            database.init_db()
            now = "2026-08-24T12:00:00"
            conn = sqlite3.connect(config.DB_PATH)
            try:
                conn.execute(
                    "INSERT INTO quotes(quote_no,created_at,updated_at,quote_date,quote_type,"
                    "year,month,client_name,rfq_no,client_address,pdf_path,terms_json,rows_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("QUT-DRAFT", now, now, "24/8/2026", "materials", 2026, 8,
                     "DRAFT CLIENT", "RFQ", "", "", "[]",
                     '[{"item":"Cement","qty":2,"rate":100},'
                     '{"item":"Sand","qty":1}]'),
                )
                conn.execute(
                    "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,year,month,"
                    "client_name,lpo_no,client_address,payment_bank_key,withholding_included,"
                    "grand_total,sell_currency,sell_total,pdf_path,rows_json,source_quote_no,source_dn_no) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("INV-DRAFT", now, now, "24/8/2026", "materials", 2026, 8,
                     "DRAFT CLIENT", "LPO-DRAFT", "", "cash", 1, 999.0, "USD", 999.0,
                     "", '[{"item":"Cement","qty":1}]', "QUT-DRAFT", "DN-DRAFT"),
                )
                conn.execute(
                    "INSERT INTO pos(po_no,created_at,updated_at,po_date,year,month,pdf_path,rows_json) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    ("PO-DRAFT", now, now, "24/8/2026", 2026, 8, "D:/PO-DRAFT.pdf",
                     '[{"name":"Cement","qty":1,"company":"CLIENT CO"}]'),
                )
                conn.execute(
                    "INSERT INTO po_fulfillment(po_no,item_index,status_kind,source_kind,source_no,marked_at) "
                    "VALUES(?,?,?,?,?,?)",
                    ("PO-DRAFT", 0, "paid", "inv", "INV-DRAFT", now),
                )
                conn.execute(
                    "INSERT INTO paid_invoices(inv_no,paid_at) VALUES(?,?)",
                    ("INV-DRAFT", now),
                )
                conn.commit()
            finally:
                conn.close()

            # Still available to the app editor/list.
            assert database.load_quote("QUT-DRAFT") is not None
            assert database.load_inv("INV-DRAFT") is not None
            assert database.list_quotes_in_db(2026, 8)[0]["pdf_path"] == ""
            assert database.list_invoices_in_db(2026, 8)[0]["pdf_path"] == ""

            # Not an official/sent/billed document anywhere else.
            assert database.quote_has_invoice("QUT-DRAFT") is False
            assert "INV-DRAFT" not in database.linked_source_set("inv")
            assert "DN-DRAFT" not in database.linked_source_set("dn")
            assert "PO-DRAFT" not in database.po_fulfillment_map()
            assert database.po_fulfillment_detail("PO-DRAFT")["invoiced"] == {}
            assert any(r["doc_no"] == "PO-DRAFT" for r in database.pos_awaiting_invoice())
            assert "DN-DRAFT" not in database.dn_paid_set()
            assert all(r["doc_no"] not in {"QUT-DRAFT", "INV-DRAFT"}
                       for r in database.list_unsubmitted())
            assert all(r["doc_no"] != "INV-DRAFT" for r in database.list_unpaid())

            usage = database.lpo_usage_map()
            assert ("draft client", "lpo-draft") not in usage

            dash = analytics.dashboard_analytics(3, 2026, 8)
            assert dash["totals"]["quotes"] == 0
            assert dash["totals"]["invoices"] == 0
            assert dash["totals"]["invoiced"] == 0
            assert dash["totals"]["quoted"] == 0
            assert dash["daily"].get("2026-08-24", 0) == 1

            money = insights.insights_overview(3, 2026, 8)
            assert money["totals"]["quotes"] == 0
            assert money["totals"]["invoices"] == 0
            assert money["totals"]["invoiced_pdf"] == 0

            client = next((r for r in clients.client_list()
                           if r["name"].lower() == "draft client"), None)
            assert client is None or (
                client["quotes"] == 0 and client["invoices"] == 0
                and client["invoiced_total"] == 0)
            profile = clients.client_profile("DRAFT CLIENT")
            assert profile["summary"]["invoiced_total"] == 0
            assert profile["summary"]["quotes"] == 0
            assert profile["summary"]["invoices"] == 0
            assert profile["docs"]["quotes"][0]["incomplete_price"] is True
            assert profile["docs"]["invoices"][0]["incomplete_price"] is True

            profit = database.profit_summary(2026, 8)
            assert profit["realized"]["count"] == 0
            assert profit["realized"]["revenue"] == 0
            assert profit["bids"]["total"] == 0

            assert database.set_submitted("quote", "QUT-DRAFT", True)["ok"] is False
            assert database.set_submitted("inv", "INV-DRAFT", True)["ok"] is False
            assert database.set_invoice_paid("INV-DRAFT", True)["ok"] is False
            assert database.set_bid_status("QUT-DRAFT", "won")["ok"] is False
            assert database.set_po_fulfillment(
                "PO-DRAFT", [0], "inv", "INV-DRAFT", True
            )["ok"] is False
        finally:
            config.DB_PATH, config.DATA_DIR = old_db, old_data

    print("incomplete price draft isolation backend tests passed")


if __name__ == "__main__":
    main()
