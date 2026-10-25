"""Invoice links make POs invoiced; invoice payment alone makes them paid."""

from __future__ import annotations

import gc
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config, database  # noqa: E402


TEST_DIR = ROOT / ".test-work" / "po-invoice-payment-semantics"
TEST_DB = TEST_DIR / "test.db"


def reset_database() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    gc.collect()
    TEST_DB.unlink(missing_ok=True)
    config.DB_PATH = TEST_DB
    config.DATA_DIR = TEST_DIR
    with sqlite3.connect(TEST_DB) as conn:
        conn.executescript(database._SCHEMA)


def insert_documents() -> None:
    rows = json.dumps([
        {"item": "Item 1", "qty": "1", "company": "Client Co"},
        {"item": "Item 2", "qty": "1", "company": "Client Co"},
    ])
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute(
            "INSERT INTO pos(po_no,created_at,updated_at,po_date,year,month,"
            "pdf_path,notes,rows_json) VALUES('PO-A','2026-07-18','2026-07-18',"
            "'18/7/2026',2026,7,'PO-A.pdf','',?)",
            (rows,),
        )
        for inv_no in ("INV-A", "INV-B"):
            conn.execute(
                "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,"
                "year,month,client_name,lpo_no,client_address,payment_bank_key,"
                "withholding_included,grand_total,pdf_path,rows_json) "
                "VALUES(?,'2026-07-18','2026-07-18','18/7/2026','materials',"
                "2026,7,'Client Co','','','cash',1,0,?,?)",
                (inv_no, f"{inv_no}.pdf", rows),
            )
        conn.commit()


def assert_invoice_link_and_payment_are_separate() -> None:
    reset_database()
    insert_documents()

    database.set_po_fulfillment("PO-A", [0], "inv", "INV-A", True)
    detail = database.po_fulfillment_detail("PO-A")
    assert detail["invoiced"][0]["source_no"] == "INV-A"
    assert detail["paid"] == {}, "linking an unpaid invoice must not pay the PO"
    assert database.po_fulfillment_map()["PO-A"] == {
        "delivered": "none", "invoiced": "partial", "paid": "none",
    }

    database.set_invoice_paid("INV-A", True)
    detail = database.po_fulfillment_detail("PO-A")
    assert detail["paid"][0]["source_no"] == "INV-A"
    assert database.po_fulfillment_map()["PO-A"]["paid"] == "partial"

    database.set_po_fulfillment("PO-A", [1], "inv", "INV-B", True)
    states = database.po_fulfillment_map()["PO-A"]
    assert states["invoiced"] == "full"
    assert states["paid"] == "partial", "an unpaid second invoice must stay unpaid"

    database.set_invoice_paid("INV-B", True)
    assert database.po_fulfillment_map()["PO-A"]["paid"] == "full"

    database.set_invoice_paid("INV-A", False)
    states = database.po_fulfillment_map()["PO-A"]
    assert states["invoiced"] == "full", "unpaying must preserve invoice links"
    assert states["paid"] == "partial"
    assert database.po_links_for_doc("inv", "INV-A") == ["PO-A"]


def main() -> None:
    original_db = config.DB_PATH
    original_data = config.DATA_DIR
    try:
        assert_invoice_link_and_payment_are_separate()
        print("PO invoice/payment semantics backend tests passed")
    finally:
        config.DB_PATH = original_db
        config.DATA_DIR = original_data
        gc.collect()
        TEST_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
