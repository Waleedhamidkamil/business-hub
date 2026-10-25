"""PO/DN/invoice workflow lanes built from fulfillment links.

Uses an isolated SQLite database so workflow fixtures never touch live profiles.
"""

from __future__ import annotations

import gc
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config, database  # noqa: E402
from backend.api import Api  # noqa: E402


TEST_DIR = ROOT / ".test-work"
TEST_DB = TEST_DIR / "document-workflow-test.db"


def reset_database() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    gc.collect()
    TEST_DB.unlink(missing_ok=True)
    config.DB_PATH = TEST_DB
    config.DATA_DIR = TEST_DIR
    with sqlite3.connect(TEST_DB) as conn:
        conn.executescript(database._SCHEMA)


def insert_doc(conn: sqlite3.Connection, kind: str, no: str,
               item_count: int = 2) -> None:
    rows = json.dumps([
        {"item": f"Item {index + 1}", "qty": "1", "company": "Client Co"}
        for index in range(item_count)
    ])
    common = (no, "2026-07-17", "2026-07-17", "17/7/2026", 2026, 7)
    if kind == "po":
        conn.execute(
            "INSERT INTO pos(po_no,created_at,updated_at,po_date,year,month,"
            "pdf_path,notes,rows_json) VALUES(?,?,?,?,?,?,?,'',?)",
            (*common, f"{no}.pdf", rows),
        )
    elif kind == "dn":
        conn.execute(
            "INSERT INTO dns(dn_no,created_at,updated_at,dn_date,year,month,"
            "client_name,lpo_no,client_address,include_prices,pdf_path,rows_json) "
            "VALUES(?,?,?,?,?,?,'Client Co','','',0,?,?)",
            (*common, f"{no}.pdf", rows),
        )
    elif kind == "inv":
        conn.execute(
            "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,"
            "year,month,client_name,lpo_no,client_address,payment_bank_key,"
            "withholding_included,grand_total,pdf_path,rows_json) "
            "VALUES(?,?,?,?,'materials',?,?,'Client Co','','','cash',1,0,?,?)",
            (no, "2026-07-17", "2026-07-17", "17/7/2026", 2026, 7,
             f"{no}.pdf", rows),
        )
    else:
        raise AssertionError(f"bad fixture kind: {kind}")


def numbers(nodes: list[dict]) -> list[str]:
    return [node["no"] for node in nodes]


def assert_linked_lanes() -> None:
    reset_database()
    with sqlite3.connect(TEST_DB) as conn:
        for kind, no in (
            ("po", "PO-A"), ("po", "PO-B"),
            ("dn", "DN-A"), ("dn", "DN-B"),
            ("inv", "INV-A"), ("inv", "INV-B"),
        ):
            insert_doc(conn, kind, no)
        conn.executemany(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            [
                ("PO-A", 0, "delivered", "dn", "DN-A", "2026-07-17"),
                ("PO-A", 1, "delivered", "dn", "DN-A", "2026-07-17"),
                ("PO-A", 0, "paid", "inv", "INV-A", "2026-07-17"),
                ("PO-B", 0, "delivered", "dn", "DN-A", "2026-07-17"),
                ("PO-B", 1, "delivered", "dn", "DN-B", "2026-07-17"),
                ("PO-B", 0, "paid", "inv", "INV-B", "2026-07-17"),
            ],
        )
        conn.commit()

    from_dn = database.document_workflow("dn", "DN-A")
    assert from_dn["ok"] is True
    assert from_dn["current"] == {"kind": "dn", "no": "DN-A"}
    assert [lane["po"]["no"] for lane in from_dn["lanes"]] == ["PO-A", "PO-B"]
    lane_a, lane_b = from_dn["lanes"]
    assert numbers(lane_a["dns"]) == ["DN-A"], "duplicate item marks must deduplicate a DN"
    assert numbers(lane_a["invoices"]) == ["INV-A"]
    assert numbers(lane_b["dns"]) == ["DN-A", "DN-B"]
    assert numbers(lane_b["invoices"]) == ["INV-B"]
    assert lane_a["dns"][0]["current"] is True
    assert lane_a["po"]["item_count"] == 2
    assert lane_a["po"]["client"] == "Client Co"

    from_inv = database.document_workflow("inv", "INV-B")
    assert len(from_inv["lanes"]) == 1
    assert from_inv["lanes"][0]["po"]["no"] == "PO-B"
    assert numbers(from_inv["lanes"][0]["dns"]) == ["DN-A", "DN-B"]
    assert from_inv["lanes"][0]["invoices"][0]["current"] is True

    from_po = database.document_workflow("po", "PO-A")
    assert len(from_po["lanes"]) == 1
    assert from_po["lanes"][0]["po"]["current"] is True

    via_api = Api().document_workflow("po", "PO-A")
    assert via_api["ok"] is True and via_api["lanes"][0]["po"]["no"] == "PO-A"

    database.set_submitted("dn", "DN-A", True)
    database.set_invoice_paid("INV-A", True)
    cleared = database.clear_po_fulfillment_link("PO-A", "dn", "DN-A")
    assert cleared == {
        "ok": True, "po_no": "PO-A", "source_kind": "dn",
        "source_no": "DN-A", "removed": 2,
    }
    assert database.po_links_for_doc("dn", "DN-A") == ["PO-B"], (
        "unlinking one lane must preserve the same document's other PO links")
    assert database.po_links_for_doc("inv", "INV-A") == ["PO-A"], (
        "unlinking a DN must preserve invoice relationships")
    assert database.is_submitted("dn", "DN-A") is True
    assert database.is_invoice_paid("INV-A") is True
    assert Api().clear_po_fulfillment_link("PO-B", "dn", "DN-B")["removed"] == 1


def assert_unlinked_and_invalid() -> None:
    reset_database()
    with sqlite3.connect(TEST_DB) as conn:
        insert_doc(conn, "dn", "DN-ORPHAN", 1)
        insert_doc(conn, "inv", "INV-ORPHAN", 1)
        conn.commit()

    dn = database.document_workflow("dn", "DN-ORPHAN")
    assert dn["ok"] is True and dn["linked"] is False
    assert dn["lanes"][0]["po"] is None
    assert numbers(dn["lanes"][0]["dns"]) == ["DN-ORPHAN"]
    assert dn["lanes"][0]["invoices"] == []

    inv = database.document_workflow("inv", "INV-ORPHAN")
    assert inv["lanes"][0]["po"] is None
    assert numbers(inv["lanes"][0]["invoices"]) == ["INV-ORPHAN"]

    assert database.document_workflow("quote", "Q-1")["ok"] is False
    assert database.document_workflow("po", "MISSING")["ok"] is False
    assert database.clear_po_fulfillment_link("", "dn", "DN-ORPHAN")["ok"] is False
    assert database.clear_po_fulfillment_link("PO-X", "quote", "Q-1")["ok"] is False


def assert_invoice_can_consume_multiple_dns() -> None:
    reset_database()
    with sqlite3.connect(TEST_DB) as conn:
        insert_doc(conn, "po", "PO-MULTI", 4)
        insert_doc(conn, "po", "PO-DIRECT", 2)
        insert_doc(conn, "dn", "DN-ONE", 2)
        insert_doc(conn, "dn", "DN-TWO", 1)
        insert_doc(conn, "inv", "INV-MIXED", 4)
        insert_doc(conn, "inv", "INV-DIRECT", 2)
        conn.executemany(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            [
                ("PO-MULTI", 0, "delivered", "dn", "DN-ONE", "2026-07-17"),
                ("PO-MULTI", 1, "delivered", "dn", "DN-ONE", "2026-07-17"),
                ("PO-MULTI", 2, "delivered", "dn", "DN-TWO", "2026-07-17"),
                ("PO-MULTI", 0, "paid", "inv", "INV-MIXED", "2026-07-17"),
                ("PO-MULTI", 1, "paid", "inv", "INV-MIXED", "2026-07-17"),
                ("PO-MULTI", 2, "paid", "inv", "INV-MIXED", "2026-07-17"),
                ("PO-MULTI", 3, "paid", "inv", "INV-MIXED", "2026-07-17"),
                ("PO-DIRECT", 0, "paid", "inv", "INV-DIRECT", "2026-07-17"),
                ("PO-DIRECT", 1, "paid", "inv", "INV-DIRECT", "2026-07-17"),
            ],
        )
        conn.commit()

    mixed = database.document_workflow("inv", "INV-MIXED")
    assert mixed["ok"] is True and len(mixed["lanes"]) == 1
    mixed_invoice = mixed["lanes"][0]["invoices"][0]
    assert mixed_invoice["delivery_notes"] == [
        {"no": "DN-ONE", "item_count": 2},
        {"no": "DN-TWO", "item_count": 1},
    ], "one invoice must expose every DN that supplied its paid PO lines"
    assert mixed_invoice["direct_item_count"] == 1, (
        "paid PO lines with no delivered DN must stay visible as direct")
    before_payment = database.po_fulfillment_map()["PO-MULTI"]
    assert before_payment["invoiced"] == "full" and before_payment["paid"] == "none", (
        "a multi-DN invoice link must remain invoiced until payment is explicit")
    database.set_invoice_paid("INV-MIXED", True)
    after_payment = database.po_fulfillment_map()["PO-MULTI"]
    assert after_payment["paid"] == "full", (
        "paying one invoice must update every PO item it covers across multiple DNs")

    direct = database.document_workflow("inv", "INV-DIRECT")
    direct_invoice = direct["lanes"][0]["invoices"][0]
    assert direct_invoice["delivery_notes"] == []
    assert direct_invoice["direct_item_count"] == 2


def main() -> None:
    original_db = config.DB_PATH
    original_data = config.DATA_DIR
    try:
        assert_linked_lanes()
        assert_unlinked_and_invalid()
        assert_invoice_can_consume_multiple_dns()
        print("document workflow backend tests passed")
    finally:
        config.DB_PATH = original_db
        config.DATA_DIR = original_data
        gc.collect()
        TEST_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
