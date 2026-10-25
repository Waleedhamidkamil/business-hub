"""Make Invoice from DN must create exact Workflow fulfillment links."""

from __future__ import annotations

import gc
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import api as api_module  # noqa: E402
from backend import config, database, inv_generator, inv_numbering, profile_numbering  # noqa: E402
from backend.api import Api  # noqa: E402


TEST_DIR = ROOT / ".test-work" / "dn-make-invoice-workflow"
TEST_DB = TEST_DIR / "test.db"


def reset_database() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    gc.collect()
    TEST_DB.unlink(missing_ok=True)
    config.DB_PATH = TEST_DB
    config.DATA_DIR = TEST_DIR
    with sqlite3.connect(TEST_DB) as conn:
        conn.executescript(database._SCHEMA)


def rows_json(count: int) -> str:
    return json.dumps([
        {"item": f"Item {index + 1}", "qty": "1", "company": "Client Co"}
        for index in range(count)
    ])


def insert_doc(conn: sqlite3.Connection, kind: str, no: str,
               item_count: int = 2) -> None:
    common = (no, "2026-07-18", "2026-07-18", "18/7/2026", 2026, 7)
    rows = rows_json(item_count)
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
            (no, "2026-07-18", "2026-07-18", "18/7/2026", 2026, 7,
             f"{no}.pdf", rows),
        )
    else:
        raise AssertionError(f"bad fixture kind: {kind}")


def assert_exact_linking_and_conflicts() -> None:
    reset_database()
    with sqlite3.connect(TEST_DB) as conn:
        for kind, no, count in (
            ("po", "PO-A", 2), ("po", "PO-B", 1),
            ("dn", "DN-SOURCE", 3), ("dn", "DN-NO-PO", 1),
            ("inv", "INV-NEW", 3), ("inv", "INV-OTHER", 1),
        ):
            insert_doc(conn, kind, no, count)
        conn.executemany(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            [
                ("PO-A", 0, "delivered", "dn", "DN-SOURCE", "2026-07-18"),
                ("PO-A", 1, "delivered", "dn", "DN-SOURCE", "2026-07-18"),
                ("PO-B", 0, "delivered", "dn", "DN-SOURCE", "2026-07-18"),
                ("PO-A", 1, "paid", "inv", "INV-OTHER", "2026-07-18"),
            ],
        )
        conn.commit()

    result = database.link_invoice_from_dn("DN-SOURCE", "INV-NEW")
    assert result["ok"] is True
    assert result["linked_item_count"] == 2
    assert result["already_linked_item_count"] == 0
    assert result["conflict_count"] == 1
    assert result["po_count"] == 2
    assert result["no_po_links"] is False
    assert result["conflicts"] == [
        {"po_no": "PO-A", "item_index": 1, "invoice_no": "INV-OTHER"},
    ]

    po_a_detail = database.po_fulfillment_detail("PO-A")
    po_b_detail = database.po_fulfillment_detail("PO-B")
    po_a = po_a_detail["invoiced"]
    po_b = po_b_detail["invoiced"]
    assert po_a[0]["source_no"] == "INV-NEW"
    assert po_a[1]["source_no"] == "INV-OTHER", (
        "automatic linking must never overwrite another invoice")
    assert po_b[0]["source_no"] == "INV-NEW"
    assert po_a_detail["paid"] == {} and po_b_detail["paid"] == {}, (
        "making an invoice must link it without marking the PO paid")

    workflow = database.document_workflow("inv", "INV-NEW")
    assert [lane["po"]["no"] for lane in workflow["lanes"]] == ["PO-A", "PO-B"]
    for lane in workflow["lanes"]:
        assert lane["invoices"][0]["delivery_notes"] == [
            {"no": "DN-SOURCE", "item_count": 1},
        ]

    repeated = database.link_invoice_from_dn("DN-SOURCE", "INV-NEW")
    assert repeated["linked_item_count"] == 0
    assert repeated["already_linked_item_count"] == 2
    assert repeated["conflict_count"] == 1

    no_po = database.link_invoice_from_dn("DN-NO-PO", "INV-NEW")
    assert no_po["ok"] is True and no_po["no_po_links"] is True
    assert database.link_invoice_from_dn("MISSING", "INV-NEW")["ok"] is False
    assert database.link_invoice_from_dn("DN-SOURCE", "MISSING")["ok"] is False


def assert_api_save_integrates_source_dn() -> None:
    reset_database()
    with sqlite3.connect(TEST_DB) as conn:
        insert_doc(conn, "po", "PO-SAVE", 1)
        insert_doc(conn, "dn", "DN-SAVE", 1)
        conn.execute(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            ("PO-SAVE", 0, "delivered", "dn", "DN-SAVE", "2026-07-18"),
        )
        conn.commit()

    old_render = inv_generator.render_inv_pdf
    old_folder = inv_numbering.inv_save_folder
    old_enabled = profile_numbering.enabled
    old_shared = database.best_effort_shared
    # This suite swaps config.DB_PATH but shares the LIVE client library, so it
    # cannot put "Client Co" on file without adding a fake client to Waleed's
    # real list. The rule that a document may only name a client on file
    # (2026-09-11) has its own suite, tests/client_must_be_on_file.test.py;
    # what is being checked here is the DN-to-invoice workflow link.
    old_client_rule = api_module._known_client_error
    try:
        api_module._known_client_error = lambda *args, **kwargs: None
        inv_generator.render_inv_pdf = lambda payload, out: Path(out).write_bytes(b"pdf")
        inv_numbering.inv_save_folder = lambda client, inv_type, target: TEST_DIR / "INVOICES"
        profile_numbering.enabled = lambda: False
        database.best_effort_shared = lambda operation, *args, **kwargs: True
        result = Api().save_inv({
            "inv_no": "INV-FROM-DN",
            "date": "18/7/2026",
            "inv_type": "materials",
            "client_name": "Client Co",
            "items": [{"item": "Item 1", "qty": "1", "rate": "10"}],
            "source_dn_no": "DN-SAVE",
        })
    finally:
        inv_generator.render_inv_pdf = old_render
        inv_numbering.inv_save_folder = old_folder
        profile_numbering.enabled = old_enabled
        database.best_effort_shared = old_shared
        api_module._known_client_error = old_client_rule

    assert result["ok"] is True, result
    assert result["workflow_link"]["linked_item_count"] == 1
    assert database.po_links_for_doc("inv", "INV-FROM-DN") == ["PO-SAVE"]
    workflow = database.document_workflow("inv", "INV-FROM-DN")
    assert workflow["lanes"][0]["invoices"][0]["delivery_notes"] == [
        {"no": "DN-SAVE", "item_count": 1},
    ]


def main() -> None:
    original_db = config.DB_PATH
    original_data = config.DATA_DIR
    try:
        assert_exact_linking_and_conflicts()
        assert_api_save_integrates_source_dn()
        print("DN Make Invoice workflow backend tests passed")
    finally:
        config.DB_PATH = original_db
        config.DATA_DIR = original_data
        gc.collect()
        TEST_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
