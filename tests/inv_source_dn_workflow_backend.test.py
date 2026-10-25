"""Make-invoice-from-DN links the DN in the Workflow even with NO PO (2026-07-24).

Before this, the DN→INV relationship was only ever *derived* through
po_fulfillment, so an invoice made from a DN that has no linked PO recorded
nothing and its Workflow showed no delivery note at all. invoices.source_dn_no
now stores the link outright.

Covers both write paths:
  • "Make invoice" on a DN   → inv.source_dn_no
  • DN inserted as a line    → row dn_ref  (first ref wins)
and both read directions (invoice's workflow shows the DN; DN's shows the invoice).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations

DN = "DN-__SRCDN__-001"
INV = "INV-__SRCDN__-001"
INV2 = "INV-__SRCDN__-002"
CLIENT = "Source DN Test Client"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM invoices WHERE inv_no IN (?, ?)", (INV, INV2))
        c.execute("DELETE FROM po_fulfillment WHERE source_no IN (?, ?, ?)", (DN, INV, INV2))


def save_dn():
    database.save_dn_record(
        dn_no=DN, dn_date="24/7/2026", year=2026, month=7, client_name=CLIENT,
        lpo_no="", client_address="", include_prices=True, pdf_path=Path("x.pdf"),
        rows=[{"item": "Pump", "qty": "1", "rate": "100"}], source_quote_no=None)


def save_inv(inv_no, source_dn_no=None, rows=None):
    database.save_inv_record(
        inv_no=inv_no, inv_date="24/7/2026", inv_type="materials", year=2026, month=7,
        client_name=CLIENT, lpo_no="", client_address="", payment_bank_key="",
        withholding_included=True, grand_total=100.0, pdf_path=Path("x.pdf"),
        rows=rows if rows is not None else [{"item": "Pump", "qty": "1", "rate": "100"}],
        source_quote_no=None, source_dn_no=source_dn_no)


def dn_numbers(workflow):
    return {d["no"] for lane in workflow["lanes"] for d in lane["dns"]}


def inv_numbers(workflow):
    return {i["no"] for lane in workflow["lanes"] for i in lane["invoices"]}


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    try:
        # The column exists after migration.
        with sqlite3.connect(config.DB_PATH) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(invoices)")]
        assert "source_dn_no" in cols, f"source_dn_no column missing: {cols}"

        # --- Path 1: "Make invoice" on a DN, no PO anywhere. -----------------
        save_dn()
        save_inv(INV, source_dn_no=DN)

        assert database.load_inv(INV)["source_dn_no"] == DN, "source_dn_no not persisted"

        wf = database.document_workflow("inv", INV)
        assert wf["ok"], wf
        assert DN in dn_numbers(wf), f"invoice workflow should show its DN: {wf}"
        assert wf["linked"] is True, f"invoice with a source DN must count as linked: {wf}"

        # Reverse direction: the DN's own workflow shows the invoice.
        wf_dn = database.document_workflow("dn", DN)
        assert INV in inv_numbers(wf_dn), f"DN workflow should show its invoice: {wf_dn}"
        assert wf_dn["linked"] is True, wf_dn

        # --- Re-save must preserve the link (None = keep existing). ----------
        save_inv(INV, source_dn_no=None)
        assert database.load_inv(INV)["source_dn_no"] == DN, "re-save dropped source_dn_no"

        # --- Path 2: DN inserted as an invoice LINE (dn_ref), still no PO. ---
        save_inv(INV2, source_dn_no=DN,
                 rows=[{"item": DN, "unit": "EA", "qty": "1", "rate": "100", "dn_ref": DN}])
        wf2 = database.document_workflow("inv", INV2)
        assert DN in dn_numbers(wf2), f"dn_ref invoice workflow should show the DN: {wf2}"

        # The DN now lists BOTH invoices, without duplicates.
        wf_dn2 = database.document_workflow("dn", DN)
        assert {INV, INV2} <= inv_numbers(wf_dn2), wf_dn2
        all_invs = [i["no"] for lane in wf_dn2["lanes"] for i in lane["invoices"]]
        assert len(all_invs) == len(set(all_invs)), f"duplicate invoice nodes: {all_invs}"

        # The DN itself appears exactly once in its own workflow.
        all_dns = [d["no"] for lane in wf_dn2["lanes"] for d in lane["dns"]]
        assert all_dns.count(DN) == 1, f"DN duplicated in its own workflow: {all_dns}"

        # --- The panels' "LINKED" pill agrees with the Workflow. -------------
        assert INV in database.linked_source_set("inv"), \
            "invoice with a source DN should read as linked in the panel"
        assert DN in database.linked_source_set("dn"), \
            "DN referenced by an invoice should read as linked in the panel"

        # --- An unrelated invoice is unaffected. -----------------------------
        save_inv(INV2, source_dn_no="")          # explicit clear
        assert database.load_inv(INV2)["source_dn_no"] is None, "empty string should clear"
        assert INV2 not in database.linked_source_set("inv"), \
            "cleared invoice must not read as linked"

        print("inv_source_dn_workflow_backend: passed")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
