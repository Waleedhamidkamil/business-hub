import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, database, inv_numbering
from backend.api import Api


YEAR = 2026
MONTH = 7
INV_NO = "INV-MT-__PAID_STATE__-001"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM invoices WHERE inv_no = ?", (INV_NO,))
        try:
            conn.execute("DELETE FROM paid_invoices WHERE inv_no = ?", (INV_NO,))
        except sqlite3.OperationalError:
            pass
        conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", ("inv", INV_NO))


def seed_invoice():
    database.save_inv_record(
        inv_no=INV_NO,
        inv_date="7/7/2026",
        inv_type="materials",
        year=YEAR,
        month=MONTH,
        client_name="Paid State Client",
        lpo_no="LPO-PAID",
        client_address="CSSP Site",
        payment_bank_key="cash",
        withholding_included=True,
        grand_total=125.5,
        pdf_path=Path(__file__).with_name("paid-state-invoice.pdf"),
        rows=[{"item": "Pump", "description": "Temporary", "qty": "1", "rate": "125.5"}],
    )


def assert_database_paid_lifecycle():
    if database.is_invoice_paid(INV_NO):
        raise AssertionError("new invoice should not start as paid")

    res = database.set_invoice_paid(INV_NO, True)
    if not res.get("ok") or not res.get("paid"):
        raise AssertionError(f"mark paid should succeed, got {res}")
    if not database.is_invoice_paid(INV_NO):
        raise AssertionError("paid flag should be visible through is_invoice_paid")
    if INV_NO not in database.paid_invoice_set():
        raise AssertionError("paid_invoice_set should include paid invoice")

    rec = database.load_inv(INV_NO)
    if not rec or rec.get("paid") is not True:
        raise AssertionError("load_inv should include paid=True")
    listed = [r for r in database.list_invoices_in_db(YEAR, MONTH) if r.get("inv_no") == INV_NO]
    if not listed or listed[0].get("paid") is not True:
        raise AssertionError("list_invoices_in_db should include paid=True")

    res = database.set_invoice_paid(INV_NO, False)
    if not res.get("ok") or res.get("paid"):
        raise AssertionError(f"unmark paid should succeed, got {res}")
    if database.is_invoice_paid(INV_NO):
        raise AssertionError("paid flag should clear when unmarked")


def assert_api_paid_lifecycle_and_list_annotation():
    api = Api()
    res = api.set_invoice_paid(INV_NO, True)
    if not res.get("ok") or not res.get("paid"):
        raise AssertionError(f"API mark paid should succeed, got {res}")
    rec = api.load_invoice(INV_NO)
    if not rec or rec.get("paid") is not True:
        raise AssertionError("API load_invoice should include paid=True")

    original_list = inv_numbering.list_invoices_for_month
    try:
        inv_numbering.list_invoices_for_month = lambda y, m: [
            {"inv_no": INV_NO, "path": "D:/dummy/INV.pdf", "type_code": "MT"}
        ]
        rows = api.list_invoices_for_month(YEAR, MONTH)
    finally:
        inv_numbering.list_invoices_for_month = original_list

    match = [r for r in rows if r.get("inv_no") == INV_NO]
    if not match or match[0].get("paid") is not True:
        raise AssertionError("invoice list rows should include paid=True")


def assert_delete_cleans_paid_state():
    database.set_invoice_paid(INV_NO, True)
    res = database.delete_document_record("inv", INV_NO)
    if not res.get("ok") or res.get("deleted") != 1:
        raise AssertionError(f"delete_document_record should delete invoice, got {res}")
    if database.is_invoice_paid(INV_NO):
        raise AssertionError("deleting an invoice should clear its paid flag")


def main():
    try:
        database.init_db()
        cleanup()
        seed_invoice()
        assert_database_paid_lifecycle()
        assert_api_paid_lifecycle_and_list_annotation()
        assert_delete_cleans_paid_state()
    finally:
        cleanup()
    print("invoice paid backend tests passed")


if __name__ == "__main__":
    main()
