"""DN signed copy - the CLIENT's counter-signed scan (2026-07-24).

Distinct from our own digital signature (doc_meta 'signing'). The file is copied
into the DN's own folder and RENAMED to the DN number, so it travels with the
document and with backups.

Guards the ways this could corrupt things:
  • a `<dn>.signed.pdf` sitting in the DN tree must NOT appear as a phantom DN
    in the month list, nor bump the running number,
  • the DB row must never claim a copy that failed to land,
  • deleting the DN must not orphan the row.
Every path here is a scratch dir - this test cannot touch a real document.
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

from backend import api, config, database, dn_numbering, migrations

WORK = Path(__file__).resolve().parents[1] / ".test-work" / "dn-signed-copy"
DN = "DN-__SIGNEDCOPY__-001"
INV = "INV-__SIGNEDCOPY__-001"
CLIENT = "Signed Copy Test Client"

PDF = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 signed scan").decode()
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


def cleanup():
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM signed_copies WHERE doc_no=?", (DN,))
        c.execute("DELETE FROM invoices WHERE inv_no=?", (INV,))
        c.execute("DELETE FROM paid_invoices WHERE inv_no=?", (INV,))
        c.execute("DELETE FROM attention_ignores WHERE doc_no=?", (DN,))
        c.execute("DELETE FROM deleted_docs WHERE kind='dn' AND doc_no=?", (DN,))
        c.execute("DELETE FROM deleted_docs WHERE kind='inv' AND doc_no=?", (INV,))
        c.commit()
        residue = c.execute(
            "SELECT kind,doc_no FROM deleted_docs "
            "WHERE (kind='dn' AND doc_no=?) OR (kind='inv' AND doc_no=?)",
            (DN, INV),
        ).fetchall()
        assert not residue, f"signed-copy test left document-number tombstones: {residue}"
    shutil.rmtree(WORK, ignore_errors=True)


def make_dn():
    WORK.mkdir(parents=True, exist_ok=True)
    database.save_dn_record(dn_no=DN, dn_date="24/7/2026", year=2026, month=7,
                            client_name=CLIENT, lpo_no="", client_address="",
                            include_prices=False, pdf_path=WORK / f"{DN}.pdf",
                            rows=[{"item": "Pump", "qty": "1"}], source_quote_no=None)
    (WORK / f"{DN}.pdf").write_bytes(b"%PDF-1.4 the original")


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    a = api.Api()
    try:
        make_dn()
        assert a.get_dn_signed_copy(DN) is None, "starts with no signed copy"
        assert DN not in database.signed_copy_set("dn")

        # --- Attach by paste/drop, renamed to the DN number. -----------------
        r = a.attach_dn_signed_copy_data(DN, PDF, "client-scan-whatever.pdf")
        assert r["ok"], r
        p = Path(r["path"])
        assert p.name == f"{DN}.signed.pdf", f"not renamed to the DN number: {p.name}"
        assert p.parent == WORK, f"escaped the DN folder: {p}"
        assert p.read_bytes() == b"%PDF-1.4 signed scan"
        assert (WORK / f"{DN}.pdf").read_bytes() == b"%PDF-1.4 the original", \
            "the generated DN PDF must never be overwritten"
        assert DN in database.signed_copy_set("dn")

        # --- The signed copy must stay invisible to DN numbering/listing. ----
        assert dn_numbering._dn_number_from_stem(f"{DN}.signed") is None, \
            "a .signed file must not parse as a DN number"

        # --- Replacing with another format cleans up the old file. -----------
        r2 = a.attach_dn_signed_copy_data(DN, PNG, "phone-photo.png")
        assert r2["ok"] and Path(r2["path"]).name == f"{DN}.signed.png", r2
        assert not (WORK / f"{DN}.signed.pdf").exists(), \
            "replacing with a different extension orphaned the previous copy"

        # --- Rubbish input is refused and leaves no record. ------------------
        a.remove_dn_signed_copy(DN)
        for bad in ("", "not-a-data-uri", "data:application/zip;base64,QUJD"):
            res = a.attach_dn_signed_copy_data(DN, bad, "x.zip")
            assert res["ok"] is False, f"should reject {bad!r}: {res}"
        assert a.get_dn_signed_copy(DN) is None, \
            "a rejected attach must not leave a signed-copy record"

        # --- Un-attach removes row + our copy, never the original. -----------
        a.attach_dn_signed_copy_data(DN, PDF, "s.pdf")
        assert (WORK / f"{DN}.signed.pdf").exists()
        a.remove_dn_signed_copy(DN)
        assert a.get_dn_signed_copy(DN) is None
        assert not (WORK / f"{DN}.signed.pdf").exists(), "un-attach left the copy behind"
        assert (WORK / f"{DN}.pdf").exists(), "un-attach deleted the DN itself!"

        # --- A missing file is reported, not hidden. -------------------------
        a.attach_dn_signed_copy_data(DN, PDF, "s.pdf")
        (WORK / f"{DN}.signed.pdf").unlink()
        rec = a.get_dn_signed_copy(DN)
        assert rec and rec["exists"] is False, f"should flag the missing file: {rec}"

        # --- Waiving: "no signed copy expected for this one". ----------------
        a.remove_dn_signed_copy(DN)
        assert a.is_attention_ignored("dn", DN, "client_sign") is False
        assert a.set_attention_ignore("dn", DN, "client_sign", True)["ok"]
        assert a.is_attention_ignored("dn", DN, "client_sign") is True
        assert ("dn", DN) in database.attention_ignore_map().get("client_sign", set())
        a.set_attention_ignore("dn", DN, "client_sign", False)
        assert a.is_attention_ignored("dn", DN, "client_sign") is False, "un-waive failed"

        # --- Paid work stops asking for a signature. -------------------------
        # An invoice that merely EXISTS must not suppress it - only a paid one.
        database.save_inv_record(
            inv_no=INV, inv_date="24/7/2026", inv_type="materials", year=2026, month=7,
            client_name=CLIENT, lpo_no="", client_address="", payment_bank_key="",
            withholding_included=True, grand_total=10.0, pdf_path=WORK / f"{INV}.pdf",
            rows=[{"item": "Pump", "qty": "1", "rate": "10"}],
            source_quote_no=None, source_dn_no=DN)
        assert DN not in database.dn_paid_set(), \
            "an UNPAID invoice must not silence the awaiting-signature pill"
        database.set_invoice_paid(INV, True)
        assert DN in database.dn_paid_set(), "a paid invoice should silence it"
        database.set_invoice_paid(INV, False)
        assert DN not in database.dn_paid_set(), "un-paying must bring the pill back"

        # --- Deleting the DN clears the satellite row. -----------------------
        a.attach_dn_signed_copy_data(DN, PDF, "s.pdf")
        database.delete_document_record("dn", DN, pdf_path="", recycle_path="")
        assert database.get_signed_copy("dn", DN) is None, \
            "deleting the DN orphaned its signed_copies row"

        print("dn_signed_copy_backend: passed")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
