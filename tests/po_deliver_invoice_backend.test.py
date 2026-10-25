"""PO "Deliver & Invoice" To-Do bucket + per-line company classification.

Verifies (against the active-profile DB, using a unique throwaway PO number):
  - a PO with an untagged/"client" line appears in the `deliver` bucket,
    but ONLY once it has been submitted (2026-08-10: a PO that was never
    issued to the supplier is not awaiting an invoice - it shows under
    `unsubmitted` instead, which is the action that actually applies);
  - office lines (company tagged "internal") and blank-company lines are ignored;
  - linking an invoice to the client line clears the PO even before payment;
  - a delivery note alone ('delivered') does NOT clear it - the invoice is the trigger;
  - an office-only PO never appears;
  - the bucket honours the shared attention-ignore mute.

Profile note: runs against whatever profile is active on disk (uses config.DB_PATH).
It only asserts on its own PO number, so it is profile-agnostic.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, database, migrations

YEAR, MONTH = 2026, 7
PO_NO = "A-__DELIV_TEST__-001"
CLIENT = "Deliver Test Client Co"
OFFICE = "Our Office Test Co"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM pos WHERE po_no=?", (PO_NO,))
        conn.execute("DELETE FROM po_fulfillment WHERE po_no=?", (PO_NO,))
        conn.execute("DELETE FROM attention_ignores WHERE kind='po' AND doc_no=?", (PO_NO,))
        conn.execute("DELETE FROM submitted_docs WHERE kind='po' AND doc_no=?", (PO_NO,))
        conn.execute("DELETE FROM deleted_docs WHERE kind='po' AND doc_no=?", (PO_NO,))


def seed():
    pdf = Path(__file__).with_name("deliver-test.pdf")
    database.save_po_record(
        po_no=PO_NO, po_date="14/7/2026", year=YEAR, month=MONTH, pdf_path=pdf,
        rows=[
            {"name": "Pump",  "description": "", "unit": "pcs", "qty": "1", "company": CLIENT},  # client (idx 0)
            {"name": "Paper", "description": "", "unit": "box", "qty": "5", "company": OFFICE},  # office (idx 1)
            {"name": "Misc",  "description": "", "unit": "pcs", "qty": "1", "company": ""},       # blank  (idx 2)
        ],
        source_quote_no=None,
    )


def in_deliver(feed):
    return any(d.get("doc_no") == PO_NO for d in feed.get("deliver", []))


def main():
    saved_tags = dict(getattr(config, "PO_COMPANY_TAGS", {}) or {})
    try:
        database.init_db()
        migrations.run_all()
        cleanup()
        seed()

        # OFFICE tagged internal; CLIENT left untagged (untagged ⇒ client).
        config.PO_COMPANY_TAGS = {OFFICE: "internal"}

        # 2026-08-10 - an UNSUBMITTED PO is a draft: nobody has been sent it,
        # so it cannot be awaiting an invoice.
        assert not in_deliver(database.dashboard_needs_attention(YEAR, MONTH)),             "an unsubmitted PO must not be chased for an invoice"
        assert any(d.get("doc_no") == PO_NO for d in
                   database.dashboard_needs_attention(YEAR, MONTH).get("unsubmitted", [])),             "...but it must still be reachable under `unsubmitted`"

        database.set_submitted("po", PO_NO, True)
        feed = database.dashboard_needs_attention(YEAR, MONTH)
        assert in_deliver(feed), "client PO should appear in the deliver bucket"

        # Invoicing the client line clears it.
        database.set_po_fulfillment(PO_NO, [0], "inv", "__T_INV__", True)
        assert not in_deliver(database.dashboard_needs_attention(YEAR, MONTH)), \
            "fully-invoiced client PO should leave the deliver bucket"
        database.set_po_fulfillment(PO_NO, [0], "inv", "__T_INV__", False)  # undo

        # A delivery note alone must NOT clear it (invoice is the removal trigger).
        database.set_po_fulfillment(PO_NO, [0], "dn", "__T_DN__", True)
        assert in_deliver(database.dashboard_needs_attention(YEAR, MONTH)), \
            "a DN alone must not clear the deliver bucket"
        database.set_po_fulfillment(PO_NO, [0], "dn", "__T_DN__", False)

        # Tag the client company internal too ⇒ office/blank-only ⇒ never shows.
        config.PO_COMPANY_TAGS = {OFFICE: "internal", CLIENT: "internal"}
        assert not in_deliver(database.dashboard_needs_attention(YEAR, MONTH)), \
            "office-only PO should never appear in the deliver bucket"

        # Ignore mutes it out of the live bucket.
        config.PO_COMPANY_TAGS = {OFFICE: "internal"}
        database.set_attention_ignore("po", PO_NO, "deliver", True)
        assert not in_deliver(database.dashboard_needs_attention(YEAR, MONTH)), \
            "ignored PO should leave the live deliver bucket"
        database.set_attention_ignore("po", PO_NO, "deliver", False)

        print("po_deliver_invoice_backend: all assertions passed")
    finally:
        cleanup()
        config.PO_COMPANY_TAGS = saved_tags


if __name__ == "__main__":
    main()
