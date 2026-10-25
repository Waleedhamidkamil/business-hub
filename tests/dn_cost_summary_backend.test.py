"""DN cost summary + invoiceable-DN picker list (Phase 1 of DN-invoice feature).

Verifies (active-profile DB, throwaway DN numbers):
  - dn_cost_summary sums sell (Σ qty·rate) and cost (Σ qty·cost), currency-aware;
  - EVERY cost is read in the currency it was TYPED in (2026-08-25). Each ROW
    carries its own `cost_ccy` since 2026-08-06, and this function summed the
    raw numbers - dinars added to dollars. DN-TDC-032 reported a cost of
    856,961.40 against $703.60 of revenue; it really cost $622.11. 23 of 63
    delivery notes were wrong, and the figure feeds the invoice DN picker, so
    picking one dropped a six-figure "cost" onto the invoice line;
  - `has_costs` tells "nothing was ever entered" apart from "it is free";
  - list_invoiceable_dns includes a priced DN with its totals and EXCLUDES an
    unpriced DN.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations

DN = "DN-__COSTSUM__-001"
DN_UNPRICED = "DN-__COSTSUM__-002"
DN_MIXED = "DN-__COSTSUM__-003"        # one dollar line beside a dinar line
DN_NOCOST = "DN-__COSTSUM__-004"       # priced, never costed


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        for d in (DN, DN_UNPRICED, DN_MIXED, DN_NOCOST):
            c.execute("DELETE FROM dns WHERE dn_no=?", (d,))
            c.execute("DELETE FROM doc_cost_meta WHERE kind='dn' AND doc_no=?", (d,))


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    try:
        database.save_dn_record(
            dn_no=DN, dn_date="18/7/2026", year=2026, month=7,
            client_name="C", lpo_no="", client_address="",
            include_prices=True, pdf_path=Path("x.pdf"),
            rows=[{"item": "A", "qty": "20", "rate": "0.75"},   # sell 15.00
                  {"item": "B", "qty": "10", "rate": "2.00"}],  # sell 20.00
            source_quote_no=None,
        )
        database.save_doc_costs("dn", DN, costs={"0": "0.55", "1": "1.20"}, currency="USD")

        s = database.dn_cost_summary(DN)
        assert s["ok"] is True, s
        assert abs(s["sell_total"] - 35.00) < 1e-6, s        # 15 + 20
        assert abs(s["cost_total"] - 23.00) < 1e-6, s        # 20*.55 + 10*1.20 = 11 + 12
        assert s["currency"] == "USD", s
        assert s["has_prices"] is True, s
        assert database.dn_cost_summary("DN-__NOPE__")["ok"] is False

        # unpriced DN - excluded from the invoiceable picker
        database.save_dn_record(
            dn_no=DN_UNPRICED, dn_date="18/7/2026", year=2026, month=7,
            client_name="C", lpo_no="", client_address="",
            include_prices=False, pdf_path=Path("x.pdf"),
            rows=[{"item": "Z", "qty": "3"}],
            source_quote_no=None,
        )
        picker = database.list_invoiceable_dns(300)
        nums = {p["no"] for p in picker}
        assert DN in nums, "priced DN should be invoiceable"
        assert DN_UNPRICED not in nums, "unpriced DN must be excluded"
        me = [p for p in picker if p["no"] == DN][0]
        assert abs(me["sell_total"] - 35.00) < 1e-6, me
        assert abs(me["cost_total"] - 23.00) < 1e-6, me

        # ---- per-ROW cost currency (2026-08-25) ------------------------
        # A dollar-costed line beside a dinar-costed one, inside a USD
        # document. Summing the raw numbers gives 10 + 140,000; the truth is
        # $10 + $100 = $110 at the 1400 peg.
        database.save_dn_record(
            dn_no=DN_MIXED, dn_date="18/7/2026", year=2026, month=7,
            client_name="C", lpo_no="", client_address="",
            include_prices=True, pdf_path=Path("x.pdf"),
            rows=[{"item": "USD line", "qty": "1", "rate": "20",
                   "cost": "10", "cost_ccy": "USD"},
                  {"item": "IQD line", "qty": "1", "rate": "200",
                   "cost": "140000", "cost_ccy": "IQD"}],
            source_quote_no=None,
        )
        m = database.dn_cost_summary(DN_MIXED)
        peg = m["iqd_per_usd"]
        want_usd = 10.0 + 140000.0 / peg
        assert m["currency"] == "USD", m
        assert abs(m["cost_total_usd"] - want_usd) < 0.01, (
            "each row must be read in the currency it was TYPED in - got "
            f"{m['cost_total_usd']}, expected {want_usd:.2f}. Summing the raw "
            "numbers is what reported DN-TDC-032 at 856,961.40.")
        assert abs(m["cost_total"] - want_usd) < 0.01, (
            "in a USD document `cost_total` IS the dollar figure", m)
        assert m["cost_total"] < 1000, (
            "a five-figure cost on a $220 delivery note is the bug, not a total", m)
        assert m["has_costs"] is True, m
        # ...and it agrees with the costing engine, which is the whole point.
        eng = database.load_doc_costing("dn", DN_MIXED)["totals"]["cost"]
        assert abs(eng - m["cost_total_usd"]) < 0.02, (
            "dn_cost_summary and the costing engine must never disagree about "
            f"one delivery note: {m['cost_total_usd']} vs {eng}")

        # ---- priced but never costed -----------------------------------
        database.save_dn_record(
            dn_no=DN_NOCOST, dn_date="18/7/2026", year=2026, month=7,
            client_name="C", lpo_no="", client_address="",
            include_prices=True, pdf_path=Path("x.pdf"),
            rows=[{"item": "A", "qty": "2", "rate": "50"}],
            source_quote_no=None,
        )
        n = database.dn_cost_summary(DN_NOCOST)
        assert n["has_costs"] is False, (
            "nothing was ever entered - that is not the same as a cost of "
            "zero, and the invoice line must be left EMPTY rather than "
            "claiming to be costed", n)
        assert n["cost_total"] == 0.0, n

        print("dn_cost_summary_backend: passed")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
