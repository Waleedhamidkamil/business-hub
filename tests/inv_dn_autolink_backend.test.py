"""Invoice DN-lines auto-close the PO loop (Phase 4 of DN-invoice feature).

An invoice with `dn_ref` lines, on save, marks each referenced DN's delivered PO
items invoiced (via link_invoice_dn_refs → link_invoice_from_dn), so the PO
leaves the 'Deliver & Invoice' To-Do. Tested at the helper level (no PDF render).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations

PO = "A-__DNAUTOLINK__-001"
DN = "DN-__DNAUTOLINK__-001"
INV = "INV-__DNAUTOLINK__-001"
CLIENT = "Autolink Test Client"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM pos WHERE po_no=?", (PO,))
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM invoices WHERE inv_no=?", (INV,))
        c.execute("DELETE FROM po_fulfillment WHERE po_no=?", (PO,))
        c.execute("DELETE FROM po_fulfillment WHERE source_no IN (?, ?)", (DN, INV))


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    saved_tags = dict(getattr(config, "PO_COMPANY_TAGS", {}) or {})
    config.PO_COMPANY_TAGS = {}          # CLIENT untagged ⇒ client line
    try:
        database.save_po_record(po_no=PO, po_date="14/7/2026", year=2026, month=7,
            pdf_path=Path("x.pdf"),
            rows=[{"name": "Pump", "unit": "pcs", "qty": "1", "company": CLIENT}],
            source_quote_no=None)
        database.save_dn_record(dn_no=DN, dn_date="18/7/2026", year=2026, month=7,
            client_name=CLIENT, lpo_no="", client_address="", include_prices=True,
            pdf_path=Path("x.pdf"), rows=[{"item": "Pump", "qty": "1", "rate": "135.82"}],
            source_quote_no=None)
        database.save_inv_record(inv_no=INV, inv_date="18/7/2026", inv_type="materials",
            year=2026, month=7, client_name=CLIENT, lpo_no="", client_address="",
            payment_bank_key="", withholding_included=True, grand_total=135.82,
            pdf_path=Path("x.pdf"),
            rows=[{"item": DN, "unit": "EA", "qty": "1", "rate": "135.82", "dn_ref": DN}],
            source_quote_no=None)

        # DN delivered PO item 0; PO is awaiting invoice.
        database.set_po_fulfillment(PO, [0], "dn", DN, True)
        assert any(d["doc_no"] == PO for d in database.pos_awaiting_invoice(2026, 7)), \
            "PO should await invoice before the DN line is billed"

        # The invoice's dn_ref line links → PO item invoiced.
        res = database.link_invoice_dn_refs(INV, [{"dn_ref": DN}])
        assert res["ok"] and DN in res["dn_refs"], res
        detail = database.po_fulfillment_detail(PO)
        assert 0 in detail["paid"] or "0" in detail["paid"] or 0 in detail["invoiced"], detail
        assert not any(d["doc_no"] == PO for d in database.pos_awaiting_invoice(2026, 7)), \
            "invoiced PO should leave the deliver bucket"
        print("inv_dn_autolink_backend: passed")
    finally:
        cleanup()
        config.PO_COMPANY_TAGS = saved_tags


if __name__ == "__main__":
    main()
