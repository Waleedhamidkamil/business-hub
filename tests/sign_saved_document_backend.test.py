"""Apply sign & stamp to an already-saved document (2026-07-24).

Backs the Copy warning's "Apply sign & stamp". Verifies the guards rather than
the PDF bytes: rendering needs headless Chrome, which isn't reliable in a test
runner, so the render itself is exercised only opportunistically and SKIPPED on
failure. Every record written here points at a scratch path - this test must
never be able to overwrite a real document.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, migrations, signing

WORK = Path(__file__).resolve().parents[1] / ".test-work" / "sign-saved-doc"
PO = "A-__RESIGN__-001"
DN = "DN-__RESIGN__-001"
CLIENT = "Re-sign Test Client"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM pos WHERE po_no=?", (PO,))
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM doc_columns WHERE doc_no IN (?, ?)", (PO, DN))
        c.execute("DELETE FROM doc_meta WHERE doc_no IN (?, ?)", (PO, DN))
    shutil.rmtree(WORK, ignore_errors=True)


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    WORK.mkdir(parents=True, exist_ok=True)
    a = api.Api()
    try:
        # --- Input guards. ---------------------------------------------------
        assert a.sign_saved_document("bogus", "X")["ok"] is False
        assert a.sign_saved_document("dn", "")["ok"] is False
        r = a.sign_saved_document("dn", "DN-does-not-exist-999")
        assert r["ok"] is False and "error" in r, r

        # --- doc_is_signed reads the stored overlay. -------------------------
        assert a.doc_is_signed("dn", "DN-does-not-exist-999") is False
        assert a.doc_is_signed("bogus", DN) is False

        # --- A legacy PO (no stored widths) must ASK before re-rendering. ----
        # Re-rendering it would silently fall back to template column widths.
        database.save_po_record(po_no=PO, po_date="24/7/2026", year=2026, month=7,
                                pdf_path=WORK / f"{PO}.pdf",
                                rows=[{"name": "Pump", "unit": "pcs", "qty": "1"}],
                                source_quote_no=None)
        from backend.api import _po_stored_col_widths
        assert _po_stored_col_widths(PO) is None, "fixture should have no stored widths"
        r = a.sign_saved_document("po", PO)
        assert r["ok"] is False and r.get("needs_confirm") == "layout", r

        # Once widths ARE stored, no confirmation is needed.
        database.set_doc_columns("po", PO, [{"key": "name", "width": 40}])
        assert _po_stored_col_widths(PO) == {"name": 40}, _po_stored_col_widths(PO)

        # --- Nothing to sign with ⇒ a clear error, never a false "signed". ---
        s = signing.get_settings()
        has_assets = s["stamp"]["has_image"] or any(
            x["has_image"] for x in s["signatures"])
        if not has_assets:
            r = a.sign_saved_document("po", PO, allow_layout_reset=True)
            assert r["ok"] is False and "Settings" in r.get("error", ""), r
            print("sign_saved_document_backend: passed (no signing assets installed)")
            return

        # --- force=True signs even with auto-sign switched OFF. --------------
        saved_auto = dict(signing.get_settings()["auto"])
        signing.save_settings({"auto": {**saved_auto, "enabled": False}})
        try:
            assert signing.overlay_for_save("dn", None) is None, "auto off ⇒ no auto-sign"
            forced = signing.overlay_for_save("dn", None, force=True)
            assert signing.overlay_is_signed(forced), \
                "explicit Apply must sign even when auto-sign is off"
        finally:
            signing.save_settings({"auto": saved_auto})

        # --- Opportunistic real re-render (needs headless Chrome). -----------
        database.save_dn_record(dn_no=DN, dn_date="24/7/2026", year=2026, month=7,
                                client_name=CLIENT, lpo_no="", client_address="",
                                include_prices=False, pdf_path=WORK / f"{DN}.pdf",
                                rows=[{"item": "Pump", "unit": "pcs", "qty": "1"}],
                                source_quote_no=None)
        assert a.doc_is_signed("dn", DN) is False, "fixture starts unsigned"
        r = a.sign_saved_document("dn", DN)
        if r.get("ok"):
            assert Path(r["path"]).exists(), r
            assert Path(r["path"]).parent == WORK, \
                f"re-render escaped the scratch dir: {r['path']}"
            assert a.doc_is_signed("dn", DN) is True, "overlay not persisted after signing"
            print("sign_saved_document_backend: passed (incl. live re-render)")
        else:
            # Chrome unavailable - the guards above are still verified.
            assert a.doc_is_signed("dn", DN) is False, \
                "a FAILED re-render must not leave the document marked signed"
            print(f"sign_saved_document_backend: passed (render skipped: {r.get('error')})")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
