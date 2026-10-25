import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# This module reads profile-dependent constants, so it is meaningless under the
# wrong profile - and it used to FAIL there, cryptically, which is how the board
# ended up warning readers not to believe it instead of fixing it (2026-09-05).
# Say so plainly instead. See tests/profile_guard.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_guard import require_profile  # noqa: E402

require_profile("abraj")


from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database


YEAR = 2026
MONTH = 7
QUOTE_NO = "QUT-MT-__DELETE_DOC__-001"
DN_NO = "DN-__DELETE_DOC__-001"
INV_NO = "INV-MT-__DELETE_DOC__-001"
TOMBSTONE_QUOTE_NO = "QUT-MT-__DELETE_DOC__-987654"
TOMBSTONE_PO_NO = "A-987654"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM quotes WHERE quote_no = ?", (QUOTE_NO,))
        conn.execute("DELETE FROM dns WHERE dn_no = ?", (DN_NO,))
        conn.execute("DELETE FROM invoices WHERE inv_no = ?", (INV_NO,))
        for kind, no in (("quote", QUOTE_NO), ("dn", DN_NO), ("inv", INV_NO)):
            conn.execute("DELETE FROM submitted_docs WHERE kind = ? AND doc_no = ?", (kind, no))
            conn.execute("DELETE FROM doc_cost_meta WHERE kind = ? AND doc_no = ?", (kind, no))
            conn.execute("DELETE FROM doc_columns WHERE kind = ? AND doc_no = ?", (kind, no))
        for doc_type, no in (("qut", QUOTE_NO), ("dn", DN_NO), ("inv", INV_NO)):
            conn.execute("DELETE FROM doc_meta WHERE doc_type = ? AND doc_no = ?", (doc_type, no))
        for kind, no in (("quote", QUOTE_NO), ("dn", DN_NO), ("inv", INV_NO)):
            conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", (kind, no))
        conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", ("quote", TOMBSTONE_QUOTE_NO))
        conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", ("po", TOMBSTONE_PO_NO))


def seed_quote_with_satellites():
    pdf_path = Path(__file__).with_name("missing-deleted-quote.pdf")
    row = {"item": "Delete Test Item", "description": "Temporary", "qty": "2", "rate": "5"}
    database.save_quote_record(
        quote_no=QUOTE_NO,
        quote_date="1/7/2026",
        quote_type="materials",
        year=YEAR,
        month=MONTH,
        client_name="Delete Test Client",
        rfq_no="RFQ-DEL",
        client_address="CSSP Site",
        pdf_path=pdf_path,
        rows=[dict(row)],
        terms=[],
    )
    database.save_dn_record(
        dn_no=DN_NO,
        dn_date="1/7/2026",
        year=YEAR,
        month=MONTH,
        client_name="Delete Test Client",
        lpo_no="LPO-DEL",
        client_address="CSSP Site",
        include_prices=True,
        pdf_path=pdf_path,
        rows=[dict(row)],
        source_quote_no=QUOTE_NO,
    )
    database.save_inv_record(
        inv_no=INV_NO,
        inv_date="1/7/2026",
        inv_type="materials",
        year=YEAR,
        month=MONTH,
        client_name="Delete Test Client",
        lpo_no="LPO-DEL",
        client_address="CSSP Site",
        payment_bank_key="cash",
        withholding_included=True,
        grand_total=10,
        pdf_path=pdf_path,
        rows=[dict(row)],
        source_quote_no=QUOTE_NO,
    )
    database.set_submitted("quote", QUOTE_NO, True)
    database.set_cost_currency("quote", QUOTE_NO, "IQD")
    database.set_doc_columns("quote", QUOTE_NO, [
        {"key": "item", "head": "Item", "kind": "text", "width": 40},
        {"key": "remarks", "head": "Remarks", "kind": "text", "width": 60},
    ])
    database.set_doc_layout("qut", QUOTE_NO, {"autofit_rows": True})
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute(
            "UPDATE submitted_docs SET submitted_at = ? WHERE kind = ? AND doc_no = ?",
            ("2026-06-20T00:00:00", "quote", QUOTE_NO),
        )
        conn.execute(
            "INSERT OR REPLACE INTO doc_meta(doc_type, doc_no, key, value) VALUES(?, ?, ?, ?)",
            ("qut", QUOTE_NO, "signing", json.dumps({"enabled": True})),
        )


def assert_seed_is_visible():
    if not database.load_quote(QUOTE_NO):
        raise AssertionError("seed quote should exist before deletion")
    if not database.is_submitted("quote", QUOTE_NO):
        raise AssertionError("seed submitted flag should exist before deletion")
    if database.get_cost_currency("quote", QUOTE_NO) != "IQD":
        raise AssertionError("seed cost currency should exist before deletion")
    if not database.get_doc_columns("quote", QUOTE_NO):
        raise AssertionError("seed custom columns should exist before deletion")
    docs = database.list_docs_for_costing(YEAR, MONTH)
    if not any(d.get("doc_no") == QUOTE_NO for d in docs):
        raise AssertionError("seed quote should be visible in costing before deletion")
    feed = database.dashboard_needs_attention(YEAR, MONTH)
    if not any(d.get("doc_no") == QUOTE_NO for d in feed.get("uncosted", [])):
        raise AssertionError("seed quote should be visible in Needs Attention before deletion")


def assert_quote_deleted_everywhere():
    if database.load_quote(QUOTE_NO):
        raise AssertionError("deleted quote row should be removed")
    if database.quote_exists(QUOTE_NO):
        raise AssertionError("deleted quote should not block existence/numbering checks")
    if database.is_submitted("quote", QUOTE_NO):
        raise AssertionError("submitted flag should be removed with deleted quote")
    if database.get_cost_currency("quote", QUOTE_NO) != "USD":
        raise AssertionError("cost currency satellite should be removed with deleted quote")
    if database.get_doc_columns("quote", QUOTE_NO) is not None:
        raise AssertionError("custom columns satellite should be removed with deleted quote")
    if database.get_doc_layout("qut", QUOTE_NO) is not None:
        raise AssertionError("layout metadata should be removed with deleted quote")
    if QUOTE_NO not in database.deleted_document_numbers("quote"):
        raise AssertionError("deleted quote number should be tombstoned for numbering")

    with sqlite3.connect(config.DB_PATH) as conn:
        meta = conn.execute(
            "SELECT COUNT(*) FROM doc_meta WHERE doc_type = ? AND doc_no = ?",
            ("qut", QUOTE_NO),
        ).fetchone()[0]
        dn_link = conn.execute(
            "SELECT source_quote_no FROM dns WHERE dn_no = ?",
            (DN_NO,),
        ).fetchone()[0]
        inv_link = conn.execute(
            "SELECT source_quote_no FROM invoices WHERE inv_no = ?",
            (INV_NO,),
        ).fetchone()[0]
    if meta:
        raise AssertionError("all doc_meta keys should be removed with deleted quote")
    if dn_link or inv_link:
        raise AssertionError("child DN/INV links should be cleared when their quote is deleted")

    docs = database.list_docs_for_costing(YEAR, MONTH)
    if any(d.get("doc_no") == QUOTE_NO for d in docs):
        raise AssertionError("deleted quote should not remain in costing list")
    feed = database.dashboard_needs_attention(YEAR, MONTH)
    for bucket in ("decide", "uncosted", "unsubmitted"):
        if any(d.get("doc_no") == QUOTE_NO for d in feed.get(bucket, [])):
            raise AssertionError(f"deleted quote should not remain in Needs Attention {bucket}")


def assert_deleted_numbers_are_not_reused():
    from backend import po_numbering, quote_numbering

    database.delete_document_record("quote", TOMBSTONE_QUOTE_NO)
    quote_no, _, n = quote_numbering.get_next_quote_number("Delete Test Client", "materials")
    if n <= 987654 or not quote_no.endswith(str(n)):
        raise AssertionError(f"next quote number should count deleted tombstones, got {quote_no}")

    database.delete_document_record("po", TOMBSTONE_PO_NO)
    po_no, _ = po_numbering.get_next_po_number(date(YEAR, MONTH, 6))
    po_n = int(po_no.rsplit("-", 1)[-1])
    if po_n <= 987654:
        raise AssertionError(f"next PO number should count deleted tombstones, got {po_no}")


def main():
    try:
        database.init_db()
        cleanup()
        seed_quote_with_satellites()
        assert_seed_is_visible()
        res = database.delete_document_record("quote", QUOTE_NO)
        if not res.get("ok") or res.get("deleted") != 1:
            raise AssertionError(f"delete_document_record should delete one quote, got {res}")
        assert_quote_deleted_everywhere()
        assert_deleted_numbers_are_not_reused()
    finally:
        cleanup()
    print("document delete backend tests passed")


if __name__ == "__main__":
    main()
