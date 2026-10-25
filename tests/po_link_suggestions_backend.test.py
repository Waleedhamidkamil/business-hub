"""Smart PO/DN/invoice link suggestions and linked-DN routing.

This suite uses an isolated SQLite file and direct SQL fixtures. It never calls
document save helpers, so fake test items cannot enter the shared autocomplete
library or either company's live database.
"""

from __future__ import annotations

import json
import gc
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config, database  # noqa: E402
from backend.api import Api  # noqa: E402


TEST_DIR = ROOT / ".test-work"
TEST_DB = TEST_DIR / "po-link-suggestions-test.db"


def rows(count: int, qtys: list[float] | None = None) -> list[dict]:
    qtys = qtys or [1] * count
    return [
        {"item": f"Item {index + 1}", "qty": str(qtys[index])}
        for index in range(count)
    ]


def insert_doc(conn: sqlite3.Connection, kind: str, no: str, doc_date: str,
               item_rows: list[dict]) -> None:
    payload = json.dumps(item_rows)
    common = (no, "2026-07-17", "2026-07-17", doc_date, 2026, 7)
    if kind == "po":
        conn.execute(
            "INSERT INTO pos(po_no,created_at,updated_at,po_date,year,month,"
            "pdf_path,notes,rows_json) VALUES(?,?,?,?,?,?,?,'',?)",
            (*common, f"{no}.pdf", payload),
        )
    elif kind == "dn":
        conn.execute(
            "INSERT INTO dns(dn_no,created_at,updated_at,dn_date,year,month,"
            "client_name,lpo_no,client_address,include_prices,pdf_path,rows_json) "
            "VALUES(?,?,?,?,?,?,'Client','','',0,?,?)",
            (*common, f"{no}.pdf", payload),
        )
    elif kind == "inv":
        conn.execute(
            "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,"
            "year,month,client_name,lpo_no,client_address,payment_bank_key,"
            "withholding_included,grand_total,pdf_path,rows_json) "
            "VALUES(?,?,?,?,'materials',?,?,'Client','','','cash',1,0,?,?)",
            (no, "2026-07-17", "2026-07-17", doc_date, 2026, 7,
             f"{no}.pdf", payload),
        )
    else:
        raise AssertionError(f"bad fixture kind: {kind}")


def reset_database() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    gc.collect()
    TEST_DB.unlink(missing_ok=True)
    config.DB_PATH = TEST_DB
    config.DATA_DIR = TEST_DIR
    conn = sqlite3.connect(TEST_DB)
    try:
        conn.executescript(database._SCHEMA)
    finally:
        conn.close()


def assert_scoring() -> None:
    source = rows(20, [1] * 20)
    exact = database._score_link_rows(source, "17/7/2026", rows(20, [1] * 20), "16/7/2026")
    one_off = database._score_link_rows(source, "17/7/2026", rows(19, [1] * 19), "17/7/2026")
    two_off = database._score_link_rows(source, "17/7/2026", rows(18, [1] * 18), "17/7/2026")
    outside = database._score_link_rows(source, "17/7/2026", rows(17, [1] * 17), "17/7/2026")

    assert exact["eligible"] is True
    assert exact["score"] > one_off["score"] > two_off["score"]
    assert outside["eligible"] is False
    assert "20 items" in exact["reasons"]
    assert "Quantities match" in exact["reasons"]

    same_qty = database._score_link_rows(rows(3, [2, 4, 8]), "17/7/2026",
                                         rows(3, [8, 2, 4]), "10/7/2026")
    other_qty = database._score_link_rows(rows(3, [2, 4, 8]), "17/7/2026",
                                          rows(3, [1, 1, 1]), "17/7/2026")
    assert same_qty["score"] > other_qty["score"]
    assert same_qty["quantity_similarity"] == "exact"

    custom = database._score_link_rows(
        [{"col_9": "Custom item A", "qty": "2"}], "17/7/2026",
        [{"col_9": "Custom item B", "qty": "2"}], "17/7/2026",
    )
    assert custom["eligible"] is True, "custom-column rows must count as items"
    assert custom["source_item_count"] == custom["item_count"] == 1
    assert database._link_primary({"qty": "1", "col_9": "Custom item"}) == "Custom item"
    assert database._link_primary({"qty": "1", "company": "Client Co"}) == "Client Co"


def assert_candidate_queries() -> None:
    reset_database()
    conn = sqlite3.connect(TEST_DB)
    try:
        insert_doc(conn, "po", "PO-SRC", "17/7/2026", rows(20, [1] * 20))
        insert_doc(conn, "po", "PO-ALT", "17/7/2026", rows(19, [1] * 19))
        insert_doc(conn, "dn", "DN-EXACT", "16/7/2026", rows(20, [1] * 20))
        insert_doc(conn, "dn", "DN-ONE", "17/7/2026", rows(19, [1] * 19))
        insert_doc(conn, "dn", "DN-TWO", "17/7/2026", rows(18, [1] * 18))
        insert_doc(conn, "dn", "DN-OUTSIDE", "17/7/2026", rows(17, [1] * 17))
        insert_doc(conn, "inv", "INV-EXACT", "17/7/2026", rows(20, [1] * 20))
        insert_doc(conn, "inv", "INV-ONE", "17/7/2026", rows(19, [1] * 19))
        conn.execute(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            ("PO-SRC", 0, "delivered", "dn", "DN-EXACT", "2026-07-17"),
        )
        conn.execute(
            "INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)",
            ("PO-SRC", 1, "paid", "inv", "INV-EXACT", "2026-07-17"),
        )
        conn.commit()
    finally:
        conn.close()

    suggestions = database.suggest_link_candidates("po", "PO-SRC", "dn", 5)
    assert [row["no"] for row in suggestions] == ["DN-ONE", "DN-TWO"]
    assert "DN-EXACT" not in {row["no"] for row in suggestions}, "existing link must be excluded"
    assert "DN-OUTSIDE" not in {row["no"] for row in suggestions}
    po_inv = database.suggest_link_candidates("po", "PO-SRC", "inv", 5)
    assert po_inv[0]["no"] == "INV-ONE"
    assert "INV-EXACT" not in {row["no"] for row in po_inv}
    assert database.suggest_link_candidates("dn", "DN-ONE", "po", 5)[0]["no"] == "PO-ALT"
    assert "PO-SRC" not in {
        row["no"] for row in database.suggest_link_candidates("dn", "DN-EXACT", "po", 5)
    }
    inv_po = database.suggest_link_candidates("inv", "INV-EXACT", "po", 5)
    assert inv_po[0]["no"] == "PO-ALT"
    assert "PO-SRC" not in {row["no"] for row in inv_po}


def assert_linked_dn_route() -> None:
    reset_database()
    conn = sqlite3.connect(TEST_DB)
    try:
        insert_doc(conn, "inv", "INV-SRC", "17/7/2026", rows(3, [2, 4, 8]))
        insert_doc(conn, "dn", "DN-LINKED", "16/7/2026", rows(3, [8, 2, 4]))
        insert_doc(conn, "dn", "DN-UNLINKED", "17/7/2026", rows(3, [2, 4, 8]))
        insert_doc(conn, "po", "PO-A", "15/7/2026", rows(3, [2, 4, 8]))
        insert_doc(conn, "po", "PO-B", "15/7/2026", rows(2, [1, 1]))
        marks = [
            ("PO-A", 0, "delivered", "dn", "DN-LINKED", "2026-07-17"),
            ("PO-A", 2, "delivered", "dn", "DN-LINKED", "2026-07-17"),
            ("PO-B", 1, "delivered", "dn", "DN-LINKED", "2026-07-17"),
            ("PO-A", 0, "paid", "inv", "INV-OLD", "2026-07-17"),
        ]
        conn.executemany("INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)", marks)
        conn.commit()
    finally:
        conn.close()

    candidates = database.list_linked_dn_candidates("INV-SRC")
    assert [row["no"] for row in candidates] == ["DN-LINKED"]
    assert candidates[0]["linked_po_count"] == 2
    assert candidates[0]["suggested"] is True

    detail = database.linked_dn_invoice_detail("DN-LINKED", "INV-SRC")
    assert detail["ok"] is True
    groups = {group["po_no"]: group for group in detail["pos"]}
    assert set(groups) == {"PO-A", "PO-B"}
    assert groups["PO-A"]["dn_item_indexes"] == [0, 2]
    assert groups["PO-A"]["proposed_indexes"] == [2]
    assert groups["PO-B"]["proposed_indexes"] == [1]
    assert groups["PO-A"]["items"][0]["paid_by"] == "INV-OLD"

    api = Api()
    assert api.list_linked_dn_candidates("INV-SRC")[0]["no"] == "DN-LINKED"
    assert api.linked_dn_invoice_detail("DN-LINKED", "INV-SRC")["ok"] is True
    assert isinstance(api.suggest_fulfillment_links("inv", "INV-SRC", "po", 5), list)


def assert_frontend_contract() -> None:
    source = (ROOT / "static" / "po_link.js").read_text(encoding="utf-8")
    assert source.count("suggest_fulfillment_links") >= 2
    assert "Suggested" in source and "All documents" in source
    assert "Available POs" in source and "Linked POs" in source
    assert "Linked delivery notes" in source
    assert "Available DNs" in source and "Linked DNs" in source
    assert "Available Invoices" in source and "Linked Invoices" in source
    assert "list_linked_dn_candidates" in source
    assert "linked_dn_invoice_detail" in source


def main() -> None:
    original_db = config.DB_PATH
    original_data = config.DATA_DIR
    try:
        assert_scoring()
        assert_candidate_queries()
        assert_linked_dn_route()
        assert_frontend_contract()
        print("po_link_suggestions_backend: all assertions passed")
    finally:
        config.DB_PATH = original_db
        config.DATA_DIR = original_data
        gc.collect()
        TEST_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
