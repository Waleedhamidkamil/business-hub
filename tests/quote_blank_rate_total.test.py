"""A quotation line with a BLANK rate must print an empty TOTAL, not "$0.00".

Found while building QUT-MT-TDC-043 (2026-07-30): four items had no unit price
yet, and the TOTAL column rendered "$0.00" for each - which tells the client the
line is free rather than not-yet-priced. The old guard was `if (qty or rate)`,
and since `rate` reaches `_cell` already coerced to a float, a blank cell and a
typed 0 were indistinguishable; a quantity alone was enough to force "$0.00".

Renders no PDF and touches no database - pure cell-level assertions.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import quote_generator as qg  # noqa: E402


def cell(row):
    """The TOTAL (computed) cell for one row, as the template would see it."""
    col = {"kind": "computed", "key": "total", "align": "center"}
    qty = qg._num(row.get("qty") or 0)
    rate = qg._num(row.get("rate") or 0)
    return qg._cell(row, col, qty, rate)["val"]


class BlankRateGivesBlankTotal(unittest.TestCase):
    def test_quantity_but_blank_rate_is_empty(self):
        """The regression: 50 metres at no stated price is NOT $0.00."""
        self.assertEqual(cell({"qty": "50", "rate": ""}), "")

    def test_blank_rate_key_absent_entirely(self):
        self.assertEqual(cell({"qty": "50"}), "")

    def test_whitespace_rate_counts_as_blank(self):
        self.assertEqual(cell({"qty": "50", "rate": "   "}), "")

    def test_priced_line_still_totals(self):
        self.assertEqual(cell({"qty": "50", "rate": "17.5"}), "$875.00")
        self.assertEqual(cell({"qty": "1000", "rate": "1.75"}), "$1,750.00")

    def test_explicit_zero_rate_still_prints_money(self):
        """A deliberately typed 0 is a real price (free of charge) and must
        keep printing $0.00 - that is the distinction the fix restores."""
        self.assertEqual(cell({"qty": "50", "rate": "0"}), "$0.00")

    def test_fully_empty_row_is_empty(self):
        self.assertEqual(cell({}), "")
        self.assertEqual(cell({"qty": "", "rate": ""}), "")

    def test_rate_without_quantity_unchanged(self):
        """Pre-existing behaviour: a rate with no qty totals $0.00."""
        self.assertEqual(cell({"qty": "", "rate": "5"}), "$0.00")


class RenderedQuoteHasNoPhantomZeroTotals(unittest.TestCase):
    def test_unpriced_rows_render_blank_in_the_html(self):
        items = [
            {"item": "Priced", "unit": "meter", "qty": "10", "rate": "2.5"},
            {"item": "Unpriced", "unit": "meter", "qty": "50", "rate": ""},
        ]
        html = qg.render_quote_html({
            "quote_no": "QUT-BLANKRATE-TEST", "date": "30/7/2026",
            "quote_type": "materials", "client_name": "Test",
            "items": items, "terms": [],
        })
        self.assertIn("$25.00", html)          # the priced line
        # Exactly one money cell of $0.00 must NOT appear for the unpriced line.
        self.assertNotIn("$0.00", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
