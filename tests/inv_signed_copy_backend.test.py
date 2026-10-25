"""Invoice signed copy - the CLIENT's counter-signed scan (2026-08-27).

The same feature the DN has had since 2026-07-24, now on invoices, sharing one
kind-generic implementation in `api` (`*_doc_signed_copy`). Distinct from our
own digital signature (doc_meta 'signing').

Guards the ways this could corrupt things:
  • an `<inv>.signed.pdf` sitting in the INVOICES tree must NOT appear as a
    phantom invoice in the month list, nor bump the running number - unlike DN
    numbers there is no stem parser to fall through, the month scan simply
    globs `INV-*.pdf`, so `inv_numbering._is_signed_copy` is the only thing
    standing between the scan and a ghost invoice,
  • an incomplete-price draft (no PDF yet) must refuse the attach rather than
    drop scans in the working directory,
  • the DB row must never claim a copy that failed to land,
  • deleting the invoice must not orphan the row.

The invoice tree is redirected to a scratch dir for the duration - this test
cannot touch a real document.
"""
import base64
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, inv_numbering, migrations

WORK = Path(__file__).resolve().parents[1] / ".test-work" / "inv-signed-copy"
INV = f"{config.INV_PREFIX}-MT-SIGNEDCOPY-901"
DRAFT = f"{config.INV_PREFIX}-MT-SIGNEDCOPY-902"
CLIENT = "Signed Copy Test Client"
FOLDER = WORK / "Materials" / CLIENT / "2026" / "AUG" / "27-08-2026"

PDF = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 signed scan").decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


def cleanup():
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        for no in (INV, DRAFT):
            c.execute("DELETE FROM invoices WHERE inv_no=?", (no,))
            c.execute("DELETE FROM paid_invoices WHERE inv_no=?", (no,))
            c.execute("DELETE FROM signed_copies WHERE doc_no=?", (no,))
            c.execute("DELETE FROM attention_ignores WHERE doc_no=?", (no,))
            c.execute("DELETE FROM deleted_docs WHERE kind='inv' AND doc_no=?", (no,))
        c.commit()
        residue = c.execute(
            "SELECT doc_no FROM deleted_docs WHERE kind='inv' AND doc_no IN (?,?)",
            (INV, DRAFT),
        ).fetchall()
        assert not residue, f"signed-copy test left invoice-number tombstones: {residue}"
    shutil.rmtree(WORK, ignore_errors=True)


def make_invoice(inv_no, pdf_path):
    FOLDER.mkdir(parents=True, exist_ok=True)
    database.save_inv_record(
        inv_no=inv_no, inv_date="27/8/2026", inv_type="materials",
        year=2026, month=8, client_name=CLIENT, lpo_no="", client_address="",
        payment_bank_key="", withholding_included=True, grand_total=10.0,
        pdf_path=pdf_path,
        rows=[{"item": "Pump", "qty": "1", "rate": "10"}],
        source_quote_no=None)
    if pdf_path:
        Path(pdf_path).write_bytes(b"%PDF-1.4 the original")


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    a = api.Api()
    real_root = config.INV_ROOT
    config.INV_ROOT = WORK          # keep every scan off the real invoice tree
    try:
        make_invoice(INV, FOLDER / f"{INV}.pdf")
        base_max = inv_numbering._global_max_number()
        assert a.get_inv_signed_copy(INV) is None, "starts with no signed copy"
        assert INV not in database.signed_copy_set("inv")

        # --- Attach by paste/drop, renamed to the invoice number. ------------
        r = a.attach_inv_signed_copy_data(INV, PDF, "client-scan-whatever.pdf")
        assert r["ok"], r
        p = Path(r["path"])
        assert p.name == f"{INV}.signed.pdf", f"not renamed to the invoice number: {p.name}"
        assert p.parent == FOLDER, f"escaped the invoice folder: {p}"
        assert p.read_bytes() == b"%PDF-1.4 signed scan"
        assert (FOLDER / f"{INV}.pdf").read_bytes() == b"%PDF-1.4 the original", \
            "the generated invoice PDF must never be overwritten"
        assert INV in database.signed_copy_set("inv")

        # --- The signed copy must stay invisible to invoice numbering/listing.
        assert inv_numbering._is_signed_copy(f"{INV}.signed"), \
            "a .signed stem must be recognised as the client's copy"
        listed = [e["inv_no"] for e in inv_numbering.list_invoices_for_month(2026, 8)]
        assert INV in listed, f"the real invoice went missing: {listed}"
        assert f"{INV}.signed" not in listed, \
            f"the signed copy surfaced as a phantom invoice: {listed}"
        assert inv_numbering._global_max_number() == base_max, \
            "the signed copy must not take part in the running number"

        # --- The generic entry points and the wrappers agree. ----------------
        assert a.get_doc_signed_copy("inv", INV) == a.get_inv_signed_copy(INV)
        assert a.get_doc_signed_copy("qut", INV) is None, \
            "signed copies are only kept for dn/inv"
        bad = a.attach_doc_signed_copy_data("po", INV, PDF, "x.pdf")
        assert bad["ok"] is False, f"an unsupported kind must be refused: {bad}"

        # --- Replacing with another format cleans up the old file. -----------
        r2 = a.attach_inv_signed_copy_data(INV, PNG, "phone-photo.png")
        assert r2["ok"] and Path(r2["path"]).name == f"{INV}.signed.png", r2
        assert not (FOLDER / f"{INV}.signed.pdf").exists(), \
            "replacing with a different extension orphaned the previous copy"

        # --- Rubbish input is refused and leaves no record. ------------------
        a.remove_inv_signed_copy(INV)
        for bad_uri in ("", "not-a-data-uri", "data:application/zip;base64,QUJD"):
            res = a.attach_inv_signed_copy_data(INV, bad_uri, "x.zip")
            assert res["ok"] is False, f"should reject {bad_uri!r}: {res}"
        assert a.get_inv_signed_copy(INV) is None, \
            "a rejected attach must not leave a signed-copy record"

        # --- An incomplete-price draft has no PDF to sit beside. -------------
        make_invoice(DRAFT, "")
        res = a.attach_inv_signed_copy_data(DRAFT, PDF, "s.pdf")
        assert res["ok"] is False and "pricing" in res["error"].lower(), \
            f"a draft with no PDF must refuse the attach: {res}"
        assert a.get_inv_signed_copy(DRAFT) is None
        assert not (Path.cwd() / f"{DRAFT}.signed.pdf").exists(), \
            "a draft attach dropped the scan in the working directory"

        # --- Un-attach removes row + our copy, never the original. -----------
        a.attach_inv_signed_copy_data(INV, PDF, "s.pdf")
        assert (FOLDER / f"{INV}.signed.pdf").exists()
        a.remove_inv_signed_copy(INV)
        assert a.get_inv_signed_copy(INV) is None
        assert not (FOLDER / f"{INV}.signed.pdf").exists(), "un-attach left the copy behind"
        assert (FOLDER / f"{INV}.pdf").exists(), "un-attach deleted the invoice itself!"

        # --- A missing file is reported, not hidden. -------------------------
        a.attach_inv_signed_copy_data(INV, PDF, "s.pdf")
        (FOLDER / f"{INV}.signed.pdf").unlink()
        rec = a.get_inv_signed_copy(INV)
        assert rec and rec["exists"] is False, f"should flag the missing file: {rec}"

        # --- Waiving: "no signed copy expected for this one". ----------------
        a.remove_inv_signed_copy(INV)
        assert a.is_attention_ignored("inv", INV, "client_sign") is False
        assert a.set_attention_ignore("inv", INV, "client_sign", True)["ok"]
        assert a.is_attention_ignored("inv", INV, "client_sign") is True
        assert ("inv", INV) in database.attention_ignore_map().get("client_sign", set())
        a.set_attention_ignore("inv", INV, "client_sign", False)
        assert a.is_attention_ignored("inv", INV, "client_sign") is False, "un-waive failed"

        # --- The month list carries what the panel pill reads. ---------------
        a.attach_inv_signed_copy_data(INV, PDF, "s.pdf")
        a.set_attention_ignore("inv", DRAFT, "client_sign", True)
        rows = {e["inv_no"]: e for e in a.list_invoices_for_month(2026, 8)}
        assert rows[INV]["client_signed"] is True, rows[INV]
        assert rows[INV]["client_sign_waived"] is False, rows[INV]
        assert DRAFT in rows and rows[DRAFT]["client_sign_waived"] is True, \
            "a waived invoice must say so, or the pill can never be silenced"
        assert rows[DRAFT]["client_signed"] is False
        assert f"{INV}.signed" not in rows, "phantom invoice reached the panel"

        # --- Paid work stops asking for a signature. -------------------------
        assert INV not in database.paid_invoice_set()
        database.set_invoice_paid(INV, True)
        assert INV in database.paid_invoice_set(), \
            "paid drives the awaiting-signature pill off"
        database.set_invoice_paid(INV, False)

        # --- Deleting the invoice clears the satellite row. ------------------
        database.delete_document_record("inv", INV, pdf_path="", recycle_path="")
        assert database.get_signed_copy("inv", INV) is None, \
            "deleting the invoice orphaned its signed_copies row"

        print("inv_signed_copy_backend: passed")
    finally:
        config.INV_ROOT = real_root
        cleanup()


if __name__ == "__main__":
    main()
