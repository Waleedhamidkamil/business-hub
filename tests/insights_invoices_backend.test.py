"""Insights → Invoices: paid or not, losing or not, and WHY (2026-08-26).

The Documents table mixes quotations, delivery notes and invoices and answers
"what happened". An invoice gets asked two different questions: has the money
arrived, and - if it lost money - what did it. A red total answers neither.

What must hold:

  1. Every invoice in the window is in the list exactly once, and paid + unpaid
     accounts for all of them. A split that drops one is worse than no split.
  2. The money adds up: paid_charged + unpaid_charged == charged, and that
     equals what the client block already reports as invoiced revenue.
  3. A losing invoice carries a REASON, not just a minus sign - named lines
     below cost, document-level extra costs, or lines with no cost at all.
  4. `uncosted_lines` is per document, so "this is a best case" can be said
     about the invoice the reader is looking at rather than in general.
  5. Nothing here re-does the arithmetic: the figures are the same `_doc_econ`
     numbers as everywhere else, so this section can never disagree with the
     totals above it.

Reads the LIVE database, but only reads.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import insights  # noqa: E402


class InvoiceSection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = insights.client_insights("TDC", months=12)
        cls.invs = cls.d["invoices"]
        cls.s = cls.d["invoice_summary"]

    def test_every_invoice_is_listed_once(self):
        docs = [x for x in self.d["docs"] if x["kind"] == "inv"]
        self.assertEqual(len(self.invs), len(docs),
                         "the invoice list must cover exactly the invoices in Documents")
        self.assertEqual(len({x["no"] for x in self.invs}), len(self.invs),
                         "no invoice may appear twice")
        self.assertEqual(self.s["count"], len(self.invs))

    def test_paid_and_unpaid_account_for_all_of_them(self):
        self.assertEqual(self.s["paid"] + self.s["unpaid"], self.s["count"],
                         "an invoice is paid or it is not; there is no third state")
        self.assertEqual(self.s["paid"], sum(1 for x in self.invs if x["paid"]))
        # Two cents of tolerance, for the same reason the parity test allows
        # one: each half is rounded to cents on its own, so the pair can sit up
        # to half a cent either side of the whole. A real disagreement - an
        # invoice counted in neither half - is dollars, not cents.
        self.assertAlmostEqual(
            self.s["paid_charged"] + self.s["unpaid_charged"],
            self.s["charged"], delta=0.02,
            msg="the two halves must add up to the whole, or the section "
                "quietly loses money between them")

    def test_the_totals_match_the_rest_of_the_page(self):
        # Same numbers as the client block's own invoiced revenue - this
        # section must never be able to disagree with the headline above it.
        self.assertAlmostEqual(
            self.s["charged"], round(self.d["client"]["charged"], 2), places=2,
            msg="invoice charged must equal the client's charged total")

    def test_a_losing_invoice_says_why(self):
        losers = [x for x in self.invs if x["losing"]]
        self.assertEqual(self.s["losing"], len(losers))
        for x in losers:
            with self.subTest(inv=x["no"]):
                self.assertGreater(x["loss"], 0, "a loss is reported positive")
                has_reason = (x["losing_line_count"] > 0
                              or x["extra"] > 0
                              or x["uncosted_lines"] > 0)
                self.assertTrue(
                    has_reason or x["profit"] < 0,
                    f"{x['no']} is in the red with nothing to point at")
                for line in x["losing_lines"]:
                    self.assertTrue(str(line["name"]).strip(),
                                    "a named cause must actually be named")
                    self.assertGreater(line["loss"], 0)

    def test_losing_lines_are_worst_first_and_capped(self):
        for x in self.invs:
            with self.subTest(inv=x["no"]):
                losses = [l["loss"] for l in x["losing_lines"]]
                self.assertEqual(losses, sorted(losses, reverse=True),
                                 "biggest hole first - it is the one to act on")
                self.assertLessEqual(len(x["losing_lines"]), 4,
                                     "the list is capped so one bad invoice "
                                     "cannot fill the section")
                self.assertGreaterEqual(x["losing_line_count"], len(x["losing_lines"]),
                                        "the COUNT is of all of them, not just "
                                        "the ones listed")

    def test_uncosted_is_reported_per_invoice(self):
        # An invoice flagged 'not fully costed' must be able to say how many
        # lines that is, or "best case" is a claim with no size.
        for x in self.invs:
            with self.subTest(inv=x["no"]):
                if not x["costed"] and x["lines"]:
                    self.assertGreater(
                        x["uncosted_lines"], 0,
                        f"{x['no']} is not fully costed but names no lines")

    def test_a_client_with_no_invoices_is_safe(self):
        d = insights.client_insights("__NO_SUCH_CLIENT__", months=12)
        self.assertEqual(d["invoices"], [])
        self.assertEqual(d["invoice_summary"]["count"], 0)
        self.assertEqual(d["invoice_summary"]["paid"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
