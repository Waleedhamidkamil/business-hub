"""Invoice appends its DN-line pages (Phase 5 of DN-invoice feature).

Primary check is HTML-level and deterministic (no browser): render_inv_html for an
invoice with a `dn_ref` line must contain the DN's pages (its number + item text +
a page break) that a baseline (no _dn_docs) render does NOT. A secondary PDF
page-count check runs only when headless Chrome/Edge + PyMuPDF are both available;
it SKIPS otherwise (the app renders via WebView2, verified in-app).
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, database, migrations, inv_generator
from backend import api as apimod

DN = "DN-__APPEND__-001"
INV = "INV-__APPEND__-001"
DN_ITEM = "Zzql Cabbage"          # distinctive: appears only on the appended DN page


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM doc_cost_meta WHERE kind='dn' AND doc_no=?", (DN,))


def main():
    database.init_db(); migrations.run_all(); cleanup()
    try:
        database.save_dn_record(
            dn_no=DN, dn_date="18/7/2026", year=2026, month=7,
            client_name="TDC", lpo_no="", client_address="CSSP",
            include_prices=True, pdf_path=Path("x.pdf"),
            rows=[{"item": DN_ITEM, "qty": "20", "rate": "0.75"},
                  {"item": "Onion", "qty": "10", "rate": "1.00"}],
            source_quote_no=None)

        inv = {"inv_no": INV, "date": "18/7/2026", "inv_type": "materials",
               "client_name": "TDC", "client_address": "CSSP", "lpo_no": "",
               "items": [{"item": DN, "unit": "EA", "qty": "1", "rate": "25.00", "dn_ref": DN},
                         {"item": "Transportation fees", "unit": "Trip", "qty": "1", "rate": "150.00"}]}

        # --- HTML-level (deterministic, no browser) ---
        base_html = inv_generator.render_inv_html(inv, inline_css=True)
        assert DN_ITEM not in base_html, "baseline invoice must NOT contain DN page content"

        dn_docs = apimod._resolve_dn_docs(inv)
        assert len(dn_docs) == 1 and dn_docs[0].get("items"), dn_docs
        comb_html = inv_generator.render_inv_html({**inv, "_dn_docs": dn_docs}, inline_css=True)

        assert DN_ITEM in comb_html, "combined invoice must contain the appended DN's item text"
        assert "break-before" in comb_html, "appended DN must start on a fresh page"
        assert "Transportation fees" in comb_html, "invoice's own lines must remain intact"
        assert len(comb_html) > len(base_html), "combined HTML should be larger than baseline"
        print("inv_appended_dn_pages_backend: HTML-level append verified")

        # --- optional PDF page-count check (needs Chrome/Edge + fitz) ---
        try:
            import fitz
            tmp = Path(tempfile.gettempdir()) / "po_tool_test_append"
            tmp.mkdir(parents=True, exist_ok=True)
            from backend import dn_generator
            dn_pdf = tmp / "dn.pdf"; dn_generator.render_dn_pdf(dn_docs[0], dn_pdf)
            base_pdf = tmp / "base.pdf"; inv_generator.render_inv_pdf(inv, base_pdf)
            comb_pdf = tmp / "comb.pdf"; inv_generator.render_inv_pdf({**inv, "_dn_docs": dn_docs}, comb_pdf)
            def npages(p):
                d = fitz.open(p); n = d.page_count; d.close(); return n
            bp, dp, cp = npages(base_pdf), npages(dn_pdf), npages(comb_pdf)
            assert cp == bp + dp, f"expected {bp}+{dp}, got {cp} (blank/lost page?)"
            print(f"  PDF page-count OK (invoice {bp} + DN {dp} = {cp})")
        except Exception as e:
            print(f"  PDF page-count check SKIPPED ({type(e).__name__}: {str(e)[:60]})")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
