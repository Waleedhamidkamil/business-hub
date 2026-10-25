import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations


YEAR = 2026
MONTH = 7
QUOTE_NO = "QUT-MT-__PO_LINK__-001"
PO_NO = "A-__PO_LINK__-001"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM pos WHERE po_no = ?", (PO_NO,))
        conn.execute("DELETE FROM quotes WHERE quote_no = ?", (QUOTE_NO,))
        conn.execute("DELETE FROM submitted_docs WHERE kind = ? AND doc_no = ?", ("po", PO_NO))
        conn.execute("DELETE FROM attention_ignores WHERE kind = ? AND doc_no = ?", ("po", PO_NO))
        conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", ("quote", QUOTE_NO))
        conn.execute("DELETE FROM deleted_docs WHERE kind = ? AND doc_no = ?", ("po", PO_NO))


def seed_quote_and_po():
    pdf_path = Path(__file__).with_name("po-link-test.pdf")
    database.save_quote_record(
        quote_no=QUOTE_NO,
        quote_date="13/7/2026",
        quote_type="materials",
        year=YEAR,
        month=MONTH,
        client_name="PO Link Client",
        rfq_no="RFQ-PO-LINK",
        client_address="CSSP Site",
        pdf_path=pdf_path,
        rows=[{"item": "Pipe", "description": "Steel", "qty": "2", "rate": "4"}],
        terms=[],
    )
    database.save_po_record(
        po_no=PO_NO,
        po_date="13/7/2026",
        year=YEAR,
        month=MONTH,
        pdf_path=pdf_path,
        rows=[{"name": "Pipe", "description": "Steel", "unit": "pcs", "qty": "2", "company": "Vendor"}],
        source_quote_no=QUOTE_NO,
    )


def main():
    try:
        database.init_db()
        migrations.run_all()
        cleanup()
        seed_quote_and_po()

        feed = database.dashboard_needs_attention(YEAR, MONTH)
        if not any(d.get("kind") == "po" and d.get("doc_no") == PO_NO
                   for d in feed.get("unsubmitted", [])):
            raise AssertionError("unsubmitted POs should appear in Needs Attention")

        ignored = database.set_attention_ignore("po", PO_NO, "unsubmitted", True)
        if not ignored.get("ok"):
            raise AssertionError(f"PO unsubmitted reminders should be ignorable, got {ignored}")
        muted_feed = database.dashboard_needs_attention(YEAR, MONTH)
        if any(d.get("kind") == "po" and d.get("doc_no") == PO_NO
               for d in muted_feed.get("unsubmitted", [])):
            raise AssertionError("ignored PO should leave the live unsubmitted bucket")
        if not any(d.get("kind") == "po" and d.get("doc_no") == PO_NO and d.get("category") == "unsubmitted"
                   for d in muted_feed.get("ignored", [])):
            raise AssertionError("ignored PO should move into the ignored bucket")
        database.set_attention_ignore("po", PO_NO, "unsubmitted", False)

        res = database.set_submitted("po", PO_NO, True)
        if not res.get("ok") or not database.is_submitted("po", PO_NO):
            raise AssertionError(f"PO should support the shared submitted flag, got {res}")
        submitted_feed = database.dashboard_needs_attention(YEAR, MONTH)
        if any(d.get("kind") == "po" and d.get("doc_no") == PO_NO
               for d in submitted_feed.get("unsubmitted", [])):
            raise AssertionError("submitted POs should leave Needs Attention")

        rec = database.load_po(PO_NO)
        if not rec or rec.get("source_quote_no") != QUOTE_NO:
            raise AssertionError(f"PO should reload its quotation reference, got {rec}")

        listing = database.list_pos_in_db(YEAR, MONTH)
        listed = next((p for p in listing if p.get("po_no") == PO_NO), None)
        if not listed or listed.get("source_quote_no") != QUOTE_NO:
            raise AssertionError(f"PO month list should expose its quotation reference, got {listed}")

        database.delete_document_record("quote", QUOTE_NO)
        rec_after_quote_delete = database.load_po(PO_NO)
        if rec_after_quote_delete.get("source_quote_no"):
            raise AssertionError("deleting a linked quotation should clear the PO quotation reference")

        database.delete_document_record("po", PO_NO)
        if database.load_po(PO_NO):
            raise AssertionError("deleted PO row should be removed")
        if database.is_submitted("po", PO_NO):
            raise AssertionError("submitted flag should be removed when a PO is deleted")
    finally:
        cleanup()

    print("PO submitted and quote-link backend tests passed")


if __name__ == "__main__":
    main()
