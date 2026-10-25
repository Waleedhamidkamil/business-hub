"""A cost is never stored, moved or re-saved without the unit it is written in.

Every per-row cost in this app is a bare number; `cost_ccy` is the only thing
that says whether it means dollars or dinars, and the profit engine divides by
the peg on the strength of it. Three paths wrote the number and left the stamp
alone, so a line could say IQD while holding a dollar figure - a factor-of-1400
error that shows up as a wrong profit, not as an error.

Verifies (active-profile DB, sentinel doc numbers):
  1. `save_doc_costs` stamps `cost_ccy` on every row whose cost it writes, from
     the per-row `cost_ccys` the Costing grid now sends;
  2. a row the grid re-costs in dollars stops claiming to be in dinars - the bug
     that made the Costing screen and the document disagree about one line;
  3. `cost_ccys` beats the document-level `currency`, and an unrecognised value
     falls back rather than guessing;
  4. with no `currency` and no `cost_ccys` (a pass-through caller), each row
     keeps the unit it already had;
  5. `load_doc_costing` hands every row a RESOLVED `cost_ccy`, so the screen can
     never print a number without knowing its unit;
  6. `pull_costs_from_quote` reads each SOURCE row in its own unit and stamps
     what it writes with the TARGET's.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, migrations
from backend.api import Api

DN = "DN-__CCYRIDE__-001"
QUOTE = "QUT-__CCYRIDE__-001"
DN_PULL = "DN-__CCYRIDE__-002"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        for d in (DN, DN_PULL):
            c.execute("DELETE FROM dns WHERE dn_no=?", (d,))
        c.execute("DELETE FROM quotes WHERE quote_no=?", (QUOTE,))
        for k, d in (("dn", DN), ("dn", DN_PULL), ("quote", QUOTE)):
            c.execute("DELETE FROM doc_cost_meta WHERE kind=? AND doc_no=?", (k, d))


def rows_of(dn_no):
    return database.load_dn(dn_no)["rows"]


def seed_dn(dn_no, rows, currency="USD"):
    database.save_dn_record(
        dn_no=dn_no, dn_date="26/8/2026", year=2026, month=8,
        client_name="C", lpo_no="", client_address="",
        include_prices=True, pdf_path=Path("x.pdf"),
        rows=rows, source_quote_no=None,
    )
    database.set_cost_currency("dn", dn_no, currency)


def main():
    database.init_db()
    migrations.run_all()
    cleanup()
    try:
        # ---- 1 + 2 --------------------------------------------------------
        # Two lines stamped IQD by the DN editor, inside an IQD document.
        seed_dn(DN, [
            {"item": "A", "qty": "1", "rate": "100", "cost": "84000", "cost_ccy": "IQD"},
            {"item": "B", "qty": "1", "rate": "100", "cost": "70000", "cost_ccy": "IQD"},
        ], currency="IQD")
        # The Costing grid re-costs line B in DOLLARS (its own chip flipped) and
        # leaves line A in dinars. Both units ride with the numbers.
        database.save_doc_costs("dn", DN, costs={"0": "84000", "1": "50"},
                                cost_ccys={"0": "IQD", "1": "USD"}, currency="IQD")
        r = rows_of(DN)
        assert r[0]["cost_ccy"] == "IQD", r[0]
        assert r[1]["cost_ccy"] == "USD", (
            "a line re-costed in dollars must stop claiming to be dinars - "
            "that mismatch is the whole bug: " + repr(r[1]))
        # ...and the profit engine now reads B as $50, not 50 dinars.
        pay = database.load_doc_costing("dn", DN)
        assert abs(pay["totals"]["line_cost"] - (84000 / pay["iqd_per_usd"] + 50)) < 0.02, pay["totals"]

        # ---- 3 ------------------------------------------------------------
        database.save_doc_costs("dn", DN, costs={"1": "50"},
                                cost_ccys={"1": "GBP"}, currency="IQD")
        assert rows_of(DN)[1]["cost_ccy"] == "IQD", (
            "an unrecognised unit falls back to the document's, never to a guess")

        # ---- 4 ------------------------------------------------------------
        # No `currency`, no `cost_ccys` - a pass-through caller. Row keeps its own.
        database.save_doc_costs("dn", DN, costs={"0": "84000"})
        assert rows_of(DN)[0]["cost_ccy"] == "IQD", rows_of(DN)[0]

        # ---- 5 ------------------------------------------------------------
        seed_dn(DN_PULL, [
            {"item": "A", "qty": "1", "rate": "100"},          # no stamp at all
        ], currency="IQD")
        got = database.load_doc_costing("dn", DN_PULL)["rows"][0]
        assert got["cost_ccy"] == "IQD", (
            "the Costing screen must be told each row's unit, resolved: " + repr(got))

        # ---- 6 ------------------------------------------------------------
        # A quotation costed in dollars overall, with ONE line typed in dinars.
        database.save_quote_record(
            quote_no=QUOTE, quote_date="26/8/2026", year=2026, month=8,
            client_name="C", rfq_no="", client_address="", quote_type="materials",
            rows=[{"item": "A", "qty": "1", "rate": "100", "cost": "14000", "cost_ccy": "IQD"}],
            terms=[], pdf_path=Path("x.pdf"),
        )
        database.set_cost_currency("quote", QUOTE, "USD")
        peg = database.doc_iqd_per_usd("quote", QUOTE)
        res = Api().pull_costs_from_quote("dn", DN_PULL, QUOTE)
        assert res.get("ok") and res.get("matched") == 1, res
        pulled = rows_of(DN_PULL)[0]
        # 14000 dinars is ~$10 - pulled into an IQD document it stays ~14000,
        # NOT 14000 read as dollars and multiplied by the peg.
        assert pulled["cost_ccy"] == "IQD", pulled
        assert abs(float(pulled["cost"]) - 14000) < 1.0, (
            "the source row's OWN unit must be read, not the quote's flag: "
            + repr(pulled) + f" (peg {peg})")

        print("cost_currency_rides_with_cost: passed")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
