"""The To-Do table's numbers come from the feed (2026-10-21, Waleed).

The To-Do became tabs of tables, and a table is only worth reading if it
carries the figures each kind of work is judged by. The feed now stamps:

  - `amount` + `currency` on invoices, quotations and delivery notes - in the
    document's OWN currency (a dinar invoice says dinars; a money number never
    travels without its currency) - and `amount_usd` beside it for sorting;
  - `open_lines` / `client_lines` on every "Deliver & Invoice" PO.

Reads the live database and writes nothing (the auto-lose sweep inside the
feed aside, which the app runs on every dashboard load anyway).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import database  # noqa: E402


class AttentionAmounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.feed = database.dashboard_needs_attention()
        with database._connect() as conn:
            cls.invoices = {r["inv_no"]: r for r in conn.execute(
                "SELECT inv_no, grand_total, sell_currency, sell_total FROM invoices")}

    def test_every_unpaid_invoice_carries_its_money(self):
        unpaid = self.feed["unpaid"]
        if not unpaid:
            self.skipTest("nothing unpaid in this profile")
        for e in unpaid:
            self.assertIn("amount", e, e["doc_no"])
            self.assertIn(e["currency"], ("USD", "IQD"), e["doc_no"])
            self.assertIn("amount_usd", e, e["doc_no"])

    def test_the_amount_is_the_invoice_in_its_own_currency(self):
        for e in self.feed["unpaid"] + self.feed["unsubmitted"] + self.feed["ignored"]:
            if e.get("kind") != "inv" or "amount" not in e:
                continue
            row = self.invoices[e["doc_no"]]
            self.assertAlmostEqual(e["amount_usd"], round(float(row["grand_total"] or 0), 2),
                                   places=2, msg=e["doc_no"])
            if str(row["sell_currency"] or "").upper() == "IQD" and row["sell_total"] is not None:
                self.assertEqual(e["currency"], "IQD", e["doc_no"])
                self.assertAlmostEqual(e["amount"], round(float(row["sell_total"]), 2), places=2)
            else:
                self.assertEqual(e["currency"], "USD", e["doc_no"])
                self.assertAlmostEqual(e["amount"], e["amount_usd"], places=2)

    def test_a_dinar_invoice_is_shown_in_dinars(self):
        """Stamped directly: the live To-Do may hold no dinar invoice today."""
        iqd = [no for no, r in self.invoices.items()
               if str(r["sell_currency"] or "").upper() == "IQD" and r["sell_total"] is not None]
        if not iqd:
            self.skipTest("no dinar invoice in this profile")
        e = {"kind": "inv", "doc_no": iqd[0]}
        database._stamp_attention_amounts([e])
        self.assertEqual(e["currency"], "IQD")
        self.assertAlmostEqual(e["amount"], round(float(self.invoices[iqd[0]]["sell_total"]), 2), places=2)
        self.assertAlmostEqual(e["amount_usd"], round(float(self.invoices[iqd[0]]["grand_total"]), 2), places=2,
                               msg="sorting and totals must use the dollar figure")

    def test_a_po_says_how_many_lines_are_left(self):
        deliver = self.feed["deliver"]
        if not deliver:
            self.skipTest("no PO waiting to be invoiced in this profile")
        for e in deliver:
            self.assertGreaterEqual(e["open_lines"], 1, e["doc_no"])
            self.assertLessEqual(e["open_lines"], e["client_lines"], e["doc_no"])

    def test_the_stamp_is_pure(self):
        """A document the stamp cannot price is left alone, not given a zero."""
        entries = [{"kind": "po", "doc_no": "A-1"}, {"kind": "lpo", "doc_no": "X"},
                   {"kind": "inv", "doc_no": "__NO_SUCH_INVOICE__"}]
        database._stamp_attention_amounts(entries)
        for e in entries:
            self.assertNotIn("amount", e, e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
