"""A DN's signed copy must reach the invoice it is linked to (2026-09-02).

The client counter-signs the delivery note and sends the scan back AFTER the
invoice has already been saved. An invoice PDF is written once, at save time,
and Open / Copy / Print all hand over that same file - so without this the
invoice the client receives still carries no proof of delivery.

Two halves, and both have to hold:
  1. `api._inv_attachment_dn_refs` - a workflow-linked DN (`source_dn_no`, what
     "Make invoice" writes) counts as an attachment ONLY once its signed copy
     exists. Gating on the copy is what stops every existing workflow-linked
     invoice silently growing a page.
  2. `api._refresh_invoice_attachments` - attaching or un-attaching the copy
     rewrites the linked invoice PDFs in place.

Sentinel numbers + a scratch dir: this test never touches a real document.
"""
import base64
import io
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, migrations

WORK = Path(__file__).resolve().parents[1] / ".test-work" / "dn-copy-to-inv"
DN = "DN-__COPYTOINV__-001"
INV_LINKED = "INV-__COPYTOINV__-001"      # linked by source_dn_no
INV_REF = "INV-__COPYTOINV__-002"         # linked by a dn_ref item line
INV_OTHER = "INV-__COPYTOINV__-003"       # mentions the number in free text only
CLIENT = "Copy To Invoice Test Client"

def _one_page_pdf() -> bytes:
    """A real, readable one-page PDF. `api._dn_signed_copy` OPENS the file to
    count its pages, so a fake `b"%PDF..."` string would be classed unreadable
    and never exercise the PDF branch this test is about."""
    from pypdf import PageObject, PdfWriter
    w = PdfWriter()
    w.add_page(PageObject.create_blank_page(width=595.276, height=841.89))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


PDF = "data:application/pdf;base64," + base64.b64encode(_one_page_pdf()).decode()


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM signed_copies WHERE doc_no=?", (DN,))
        for no in (INV_LINKED, INV_REF, INV_OTHER):
            c.execute("DELETE FROM invoices WHERE inv_no=?", (no,))
    shutil.rmtree(WORK, ignore_errors=True)


def make_docs():
    WORK.mkdir(parents=True, exist_ok=True)
    database.save_dn_record(dn_no=DN, dn_date="24/8/2026", year=2026, month=8,
                            client_name=CLIENT, lpo_no="", client_address="",
                            include_prices=False, pdf_path=WORK / f"{DN}.pdf",
                            rows=[{"item": "Pump", "qty": "1"}], source_quote_no=None)
    (WORK / f"{DN}.pdf").write_bytes(b"%PDF-1.4 the delivery note")
    common = dict(inv_date="24/8/2026", inv_type="materials", year=2026, month=8,
                  client_name=CLIENT, lpo_no="", client_address="",
                  payment_bank_key="", withholding_included=True,
                  grand_total=10.0, source_quote_no=None)
    database.save_inv_record(inv_no=INV_LINKED, pdf_path=WORK / f"{INV_LINKED}.pdf",
                             rows=[{"item": "Pump", "qty": "1", "rate": "10"}],
                             source_dn_no=DN, **common)
    database.save_inv_record(inv_no=INV_REF, pdf_path=WORK / f"{INV_REF}.pdf",
                             rows=[{"item": DN, "qty": "1", "rate": "10", "dn_ref": DN}],
                             source_dn_no=None, **common)
    # The number appears only as free text, so the cheap LIKE pre-filter in
    # database.invoices_referencing_dn hits it and the confirm step must not.
    database.save_inv_record(inv_no=INV_OTHER, pdf_path=WORK / f"{INV_OTHER}.pdf",
                             rows=[{"item": "Pump", "description": f"as per {DN}",
                                    "qty": "1", "rate": "10"}],
                             source_dn_no=None, **common)


def inv_doc(inv_no):
    rec = database.load_inv(inv_no)
    rec["items"] = rec.get("rows") or []
    return rec


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    a = api.Api()
    try:
        make_docs()

        # --- Before the copy arrives -----------------------------------------
        assert api._inv_attachment_dn_refs(inv_doc(INV_LINKED)) == [], \
            "a workflow link alone must NOT staple a re-rendered DN to an invoice"
        assert api._inv_attachment_dn_refs(inv_doc(INV_REF)) == [DN], \
            "a dn_ref line has always attached, signed copy or not"

        # --- Which invoices the DN reaches ------------------------------------
        found = database.invoices_referencing_dn(DN)
        assert INV_LINKED in found and INV_REF in found, found
        assert INV_OTHER not in found, \
            "a free-text mention of the DN number must not rewrite that invoice"

        # --- The copy arrives --------------------------------------------------
        r = a.attach_dn_signed_copy_data(DN, PDF, "client-scan.pdf")
        assert r["ok"], r
        assert "invoices_refreshed" in r, \
            "attaching must report which invoices it rewrote - the user is " \
            "standing in the DN and cannot otherwise know"

        refs = api._inv_attachment_dn_refs(inv_doc(INV_LINKED))
        assert refs == [DN], f"the signed copy should now travel with it: {refs}"

        copy = api._dn_signed_copy(DN)
        assert copy and copy["kind"] == "pdf", copy
        # A PDF copy rides as a placeholder page swapped in after rendering.
        docs = api._resolve_dn_docs(inv_doc(INV_LINKED))
        assert [d["dn_no"] for d in docs] == [DN], docs
        assert docs[0]["signed_copy_pdf"] and docs[0]["signed_copy_token"], docs[0]

        # --- The editor does NOT send the link back (2026-09-02 regression) ---
        # `invState.sourceDn` is transient: only "Make invoice" sets it and
        # `loadInvForEdit` clears it, so every preview / edit / re-save posts
        # `source_dn_no: null`. Reading the payload alone made the attached
        # delivery note appear once, at first save, and vanish from every render
        # after that. The stored link has to win.
        editor_payload = {"inv_no": INV_LINKED, "source_dn_no": None,
                          "items": database.load_inv(INV_LINKED)["rows"]}
        assert api._inv_attachment_dn_refs(editor_payload) == [DN],             "an edit/preview must still attach the DN the invoice is linked to"
        assert api._inv_attachment_dn_refs({"inv_no": "", "items": []}) == [],             "a brand-new invoice has no stored link to fall back to"
        assert api._inv_attachment_dn_refs(
            {"inv_no": "INV-__COPYTOINV__-nope", "items": []}) == [],             "an unknown invoice number must not raise"

        # No duplicate when BOTH links point at the same delivery note.
        both = inv_doc(INV_REF)
        both["source_dn_no"] = DN
        assert api._inv_attachment_dn_refs(both) == [DN], \
            "the same DN must not be appended twice"

        # --- Un-attaching takes the page away again ---------------------------
        rm = a.remove_dn_signed_copy(DN)
        assert "invoices_refreshed" in (rm or {}), rm
        assert api._inv_attachment_dn_refs(inv_doc(INV_LINKED)) == [], \
            "removing the copy must stop the invoice carrying that page"
        assert api._inv_attachment_dn_refs(editor_payload) == [], \
            "...on the editor's payload too"
        assert api._inv_attachment_dn_refs(inv_doc(INV_REF)) == [DN], \
            "a dn_ref line survives the copy being removed"

        # --- A broken invoice must never block attaching the copy -------------
        out = api._refresh_invoice_attachments("DN-__COPYTOINV__-nope")
        assert out == {"invoices_refreshed": [], "invoices_failed": []}, out

        print("dn_signed_copy_reaches_invoice: passed")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
