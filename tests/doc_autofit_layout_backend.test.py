import sqlite3
import sys
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


DOC_NOS = {
    "po": "A-__AUTOFIT_TEST__",
    "qut": "QUT-MT-__AUTOFIT_TEST__-001",
    "dn": "DN-__AUTOFIT_TEST__-001",
    "inv": "INV-MT-__AUTOFIT_TEST__-001",
}


def cleanup():
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM pos WHERE po_no = ?", (DOC_NOS["po"],))
        conn.execute("DELETE FROM quotes WHERE quote_no = ?", (DOC_NOS["qut"],))
        conn.execute("DELETE FROM dns WHERE dn_no = ?", (DOC_NOS["dn"],))
        conn.execute("DELETE FROM invoices WHERE inv_no = ?", (DOC_NOS["inv"],))
        try:
            for doc_type, doc_no in DOC_NOS.items():
                conn.execute(
                    "DELETE FROM doc_meta WHERE doc_type = ? AND doc_no = ? AND key = 'layout'",
                    (doc_type, doc_no),
                )
        except sqlite3.Error:
            pass


def assert_renderers_use_document_layout():
    from backend import dn_generator, inv_generator, pdf_generator, quote_generator

    old_po = config.PO_AUTOFIT
    old_quote = dict(getattr(config, "QUOTE_LAYOUT", {}) or {})
    old_dn = dict(getattr(config, "DN_LAYOUT", {}) or {})
    old_inv = dict(getattr(config, "INV_LAYOUT", {}) or {})
    try:
        config.PO_AUTOFIT = True
        po_html = pdf_generator.render_html(
            "A-1", "5/7/2026", [{"name": "Pipe", "description": "Steel", "unit": "pcs", "qty": "2"}],
            layout={"autofit_rows": False},
        )
        if "items-table--autofit" in po_html:
            raise AssertionError("PO render ignored explicit document Auto-fit false")

        config.QUOTE_LAYOUT = {"autofit_rows": True}
        quote_html = quote_generator.render_quote_html({
            "quote_no": "QUT-TEST",
            "date": "5/7/2026",
            "quote_type": "materials",
            "client_name": "Client",
            "rfq_no": "RFQ",
            "client_address": "CSSP Site",
            "items": [{"item": "Pipe", "description": "Steel"}],
            "terms": [],
            "layout": {"autofit_rows": False},
        })
        if "q-table--autofit" in quote_html:
            raise AssertionError("quotation render ignored explicit document Auto-fit false")

        config.DN_LAYOUT = {}
        dn_html = dn_generator.render_dn_html({
            "dn_no": "DN-TEST",
            "date": "5/7/2026",
            "client_name": "Client",
            "lpo_no": "N/A",
            "client_address": "CSSP Site",
            "include_prices": False,
            "items": [{"item": "Pipe", "description": "Steel"}],
            "layout": {"autofit_rows": True},
        })
        if "d-table--autofit" not in dn_html:
            raise AssertionError("DN render ignored explicit document Auto-fit true")

        config.INV_LAYOUT = {}
        inv_html = inv_generator.render_inv_html({
            "inv_no": "INV-TEST",
            "date": "5/7/2026",
            "inv_type": "materials",
            "client_name": "Client",
            "lpo_no": "N/A",
            "client_address": "CSSP Site",
            "payment_bank_key": "",
            "withholding_included": True,
            "items": [{"item": "Pipe", "description": "Steel", "qty": "1", "rate": "2"}],
            "layout": {"autofit_rows": True},
        })
        if "i-table--autofit" not in inv_html:
            raise AssertionError("invoice render ignored explicit document Auto-fit true")
    finally:
        config.PO_AUTOFIT = old_po
        config.QUOTE_LAYOUT = old_quote
        config.DN_LAYOUT = old_dn
        config.INV_LAYOUT = old_inv


def main():
    try:
        database.init_db()
        cleanup()

        from backend.api import Api

        api = Api()
        pdf_path = Path(__file__).with_name("dummy.pdf")

        database.save_po_record(
            po_no=DOC_NOS["po"],
            po_date="5/7/2026",
            year=2026,
            month=7,
            pdf_path=pdf_path,
            rows=[{"name": "Pipe", "description": "Steel", "unit": "pcs", "qty": "2"}],
        )
        database.save_quote_record(
            quote_no=DOC_NOS["qut"],
            quote_date="5/7/2026",
            quote_type="materials",
            year=2026,
            month=7,
            client_name="Client",
            rfq_no="RFQ",
            client_address="CSSP Site",
            pdf_path=pdf_path,
            rows=[{"item": "Pipe", "description": "Steel"}],
            terms=[],
        )
        database.save_dn_record(
            dn_no=DOC_NOS["dn"],
            dn_date="5/7/2026",
            year=2026,
            month=7,
            client_name="Client",
            lpo_no="N/A",
            client_address="CSSP Site",
            include_prices=False,
            pdf_path=pdf_path,
            rows=[{"item": "Pipe", "description": "Steel"}],
        )
        database.save_inv_record(
            inv_no=DOC_NOS["inv"],
            inv_date="5/7/2026",
            inv_type="materials",
            year=2026,
            month=7,
            client_name="Client",
            lpo_no="N/A",
            client_address="CSSP Site",
            payment_bank_key="cash",
            withholding_included=True,
            grand_total=0,
            pdf_path=pdf_path,
            rows=[{"item": "Pipe", "description": "Steel"}],
        )

        database.set_doc_layout("po", DOC_NOS["po"], {"autofit_rows": True})
        database.set_doc_layout("qut", DOC_NOS["qut"], {"autofit_rows": False})
        database.set_doc_layout("dn", DOC_NOS["dn"], {"autofit_rows": True})
        database.set_doc_layout("inv", DOC_NOS["inv"], {"autofit_rows": False})

        cases = [
            ("po", api.load_po(DOC_NOS["po"]), True),
            ("qut", api.load_quote(DOC_NOS["qut"]), False),
            ("dn", api.load_dn(DOC_NOS["dn"]), True),
            ("inv", api.load_invoice(DOC_NOS["inv"]), False),
        ]
        for label, rec, expected in cases:
            if not rec:
                raise AssertionError(f"{label} record should load")
            layout = rec.get("layout")
            if not isinstance(layout, dict):
                raise AssertionError(f"{label} should expose document layout")
            if layout.get("autofit_rows") is not expected:
                raise AssertionError(f"{label} autofit_rows should be {expected!r}")
        assert_renderers_use_document_layout()
    finally:
        cleanup()

    print("document autofit layout backend tests passed")


if __name__ == "__main__":
    main()
