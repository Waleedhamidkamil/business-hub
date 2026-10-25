"""A line with no cost entered is NOT 100% profit (2026-10-21, Waleed).

INV-SV-JUNE-059 billed $9,000 with no cost typed in, so every profit figure
counted the whole $9,000 as profit. The rule now: a billable line with no cost
is costed at config.UNCOSTED_MARKUP (40% on cost) - $9,000 -> ~$6,430 cost -
in database._line_econ, the one function every profit total is built from
(Costing payload, profit_summary, Insights), and in its JS mirror
static/app.js docLineEcon (the editors' live cards).

What must hold:
  1. the estimate is price / (1 + markup), and is reported as `est_cost`
  2. the line still counts as UNCOSTED - the real cost is still asked for
  3. a typed cost, even 0, is never replaced by the estimate
  4. a line that bills nothing gets no estimate
  5. the document totals carry it (`estimated_cost`) and the JS mirror
     uses the same rule and the same markup

Pure functions only - no database writes.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, database  # noqa: E402


class UncostedEstimate(unittest.TestCase):
    def econ(self, row):
        return database._line_econ(row, "item")

    def test_the_markup_is_forty_percent_on_cost(self):
        self.assertAlmostEqual(config.UNCOSTED_MARKUP, 0.40)

    def test_a_costless_line_is_estimated_not_free(self):
        e = self.econ({"item": "Service", "qty": "1", "rate": "9000"})
        self.assertAlmostEqual(e["line_cost"], 9000 / 1.4, places=6)
        self.assertAlmostEqual(e["est_cost"], 9000 / 1.4, places=6)
        self.assertAlmostEqual(e["line_profit"], 9000 - 9000 / 1.4, places=6,
                               msg="$9,000 with no cost must read ~$2,571 profit, not $9,000")

    def test_it_still_counts_as_uncosted(self):
        row = {"item": "Service", "qty": "1", "rate": "9000"}
        self.assertFalse(self.econ(row)["has_cost"])
        self.assertFalse(database._rows_fully_costed([row], "item"),
                         "the estimate must not clear the Uncosted flag")

    def test_a_typed_cost_is_never_replaced(self):
        e = self.econ({"item": "A", "qty": "2", "rate": "100", "cost": "30"})
        self.assertAlmostEqual(e["line_cost"], 60.0)
        self.assertEqual(e["est_cost"], 0.0)
        free = self.econ({"item": "Gift", "qty": "1", "rate": "100", "cost": "0"})
        self.assertEqual(free["line_cost"], 0.0, "a typed 0 is a real cost, not a blank")
        self.assertEqual(free["est_cost"], 0.0)

    def test_a_line_that_bills_nothing_gets_no_estimate(self):
        e = self.econ({"item": "Note", "qty": "", "rate": ""})
        self.assertEqual(e["line_cost"], 0.0)
        self.assertEqual(e["est_cost"], 0.0)

    def test_document_totals_carry_the_estimate(self):
        orig = database.get_cost_currency
        database.get_cost_currency = lambda kind, no: "USD"
        try:
            orig_rate = database.doc_iqd_per_usd
            database.doc_iqd_per_usd = lambda kind, no: 1400.0
            p = database._doc_costing_payload("quote", {
                "quote_no": "__EST_TEST__", "quote_type": "materials",
                "rows": [{"item": "A", "qty": "1", "rate": "100", "cost": "60"},
                         {"item": "B", "qty": "1", "rate": "140"}],
            })
        finally:
            database.get_cost_currency = orig
            database.doc_iqd_per_usd = orig_rate
        t = p["totals"]
        self.assertAlmostEqual(t["estimated_cost"], 100.0, places=2)
        self.assertAlmostEqual(t["cost"], 160.0, places=2)
        self.assertAlmostEqual(t["profit"], 80.0, places=2)
        self.assertFalse(p["costed"])

    def test_the_js_mirror_uses_the_same_rule(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        body = js[js.index("function docLineEcon("):js.index("function computeDocEcon(")]
        self.assertRegex(body, r"revenue / \(1 \+ uncostedMarkup\(\)\)",
                         "the editors' live cards must estimate exactly as _line_econ does")
        self.assertRegex(body, r"if \(billable && !hasCost && revenue > 0\)")
        fallback = re.search(r"function uncostedMarkup\(\)[\s\S]*?: ([0-9.]+);", js)
        self.assertIsNotNone(fallback)
        self.assertAlmostEqual(float(fallback.group(1)), config.UNCOSTED_MARKUP,
                               msg="the JS fallback markup drifted from config")
        api = (ROOT / "backend" / "api.py").read_text(encoding="utf-8")
        self.assertIn('"uncosted_markup": config.UNCOSTED_MARKUP', api,
                      "the profile metadata must hand the markup to the editors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
