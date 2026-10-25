"""The editor's live profit cards must agree with the Costing screen (2026-08-04).

The cards under the Quotation / DN / Invoice footers compute cost, sell, profit
and margin in JavaScript, live as you type. The Costing screen computes the same
figures in Python. Two numbers claiming to be "the profit on this document" that
disagree are worse than not showing one at all - the user cannot tell which to
believe, and the cheap one is on screen far more often.

`static/app.js` `docUnitEcon` / `docLineEcon` are a deliberate line-by-line port
of `database._detect_unit_econ` / `_line_econ`. This test is what keeps them a
port rather than a fork: it pushes identical fixtures through BOTH and fails on
any divergence.

The fixtures are chosen to hit every branch of `_detect_unit_econ`, which is
where the subtlety lives:
  1. an explicit `total` column           -> unit = total / qty
  2. a custom col_N holding the real total -> largest positive value wins,
     unless plain qty*rate already explains it
  3. plain qty * rate
  4. a flat-rate line (rate, no qty)      -> qty defaults to 1

If you change either implementation, add a fixture here.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import database  # noqa: E402


# (label, row, cost_currency, rate, sell_currency)
FIXTURES = [
    ("plain qty*rate",            {"qty": "3", "rate": "100", "cost": "60"}, "USD", 1400.0, "USD"),
    ("qty 1",                     {"qty": "1", "rate": "250", "cost": "200"}, "USD", 1400.0, "USD"),
    ("flat rate, no qty",         {"rate": "500", "cost": "300"}, "USD", 1400.0, "USD"),
    ("flat rate, qty 0",          {"qty": "0", "rate": "500", "cost": "300"}, "USD", 1400.0, "USD"),
    ("explicit total column",     {"qty": "4", "rate": "0", "total": "800", "cost": "150"}, "USD", 1400.0, "USD"),
    ("total with no qty",         {"rate": "0", "total": "900", "cost": "100"}, "USD", 1400.0, "USD"),
    ("custom col holds total",    {"qty": "2", "rate": "0", "col_6": "700", "cost": "120"}, "USD", 1400.0, "USD"),
    ("custom col explained",      {"qty": "5", "rate": "20", "col_3": "100", "cost": "8"}, "USD", 1400.0, "USD"),
    ("two custom cols",           {"qty": "2", "rate": "0", "col_3": "120", "col_6": "900", "cost": "50"}, "USD", 1400.0, "USD"),
    ("custom col below qty*rate", {"qty": "4", "rate": "50", "col_2": "30", "cost": "10"}, "USD", 1400.0, "USD"),
    ("custom col, image ignored", {"qty": "2", "rate": "30", "col_2": "data:image/png;base64,AAA", "cost": "10"}, "USD", 1400.0, "USD"),
    ("blank cost",                {"qty": "3", "rate": "100", "cost": ""}, "USD", 1400.0, "USD"),
    ("missing cost key",          {"qty": "3", "rate": "100"}, "USD", 1400.0, "USD"),
    ("cost with symbols",         {"qty": "2", "rate": "100", "cost": "$ 45.50"}, "USD", 1400.0, "USD"),
    ("cost with thousands sep",   {"qty": "2", "rate": "100", "cost": "1,250"}, "USD", 1400.0, "USD"),
    ("junk cost",                 {"qty": "2", "rate": "100", "cost": "n/a"}, "USD", 1400.0, "USD"),
    ("IQD costs",                 {"qty": "3", "rate": "100", "cost": "84000"}, "IQD", 1400.0, "USD"),
    ("IQD costs, cash peg",       {"qty": "3", "rate": "100", "cost": "90000"}, "IQD", 1500.0, "USD"),
    ("IQD sell (invoice)",        {"qty": "2", "rate": "150000", "cost": "40"}, "USD", 1500.0, "IQD"),
    ("IQD sell AND IQD cost",     {"qty": "2", "rate": "150000", "cost": "60000"}, "IQD", 1500.0, "IQD"),
    ("cost_qty override",         {"qty": "3", "rate": "100", "cost": "60", "cost_qty": "10"}, "USD", 1400.0, "USD"),
    ("cost_sell override",        {"qty": "3", "rate": "100", "cost": "60", "cost_sell": "130"}, "USD", 1400.0, "USD"),
    ("both overrides",            {"qty": "3", "rate": "100", "cost": "60", "cost_qty": "2", "cost_sell": "500"}, "USD", 1400.0, "USD"),
    ("cost_sell override on IQD sell", {"qty": "2", "rate": "150000", "cost": "40", "cost_sell": "90"}, "USD", 1500.0, "IQD"),
    ("empty row",                 {}, "USD", 1400.0, "USD"),
    ("negative cost",             {"qty": "2", "rate": "100", "cost": "-30"}, "USD", 1400.0, "USD"),
    # 2026-08-06 - per-ROW cost currency. The row's own `cost_ccy` overrides the
    # document's, so one table can mix them; these four pin every combination.
    ("row USD inside a USD doc",  {"qty": "1", "rate": "100", "cost": "60", "cost_ccy": "USD"}, "USD", 1400.0, "USD"),
    ("row IQD inside a USD doc",  {"qty": "1", "rate": "100", "cost": "84000", "cost_ccy": "IQD"}, "USD", 1400.0, "USD"),
    ("row USD inside an IQD doc", {"qty": "1", "rate": "100", "cost": "60", "cost_ccy": "USD"}, "IQD", 1400.0, "USD"),
    ("row IQD inside an IQD doc", {"qty": "1", "rate": "100", "cost": "84000", "cost_ccy": "IQD"}, "IQD", 1400.0, "USD"),
    ("junk row currency falls back to the doc", {"qty": "1", "rate": "100", "cost": "84000", "cost_ccy": "GBP"}, "IQD", 1400.0, "USD"),
    ("blank row currency inherits", {"qty": "1", "rate": "100", "cost": "84000", "cost_ccy": ""}, "IQD", 1400.0, "USD"),
    ("decimal qty",               {"qty": "2.5", "rate": "40", "cost": "12.5"}, "USD", 1400.0, "USD"),
]

JS_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const start = src.indexOf("function _econNum(");
const end = src.indexOf("window.docUnitEcon = docUnitEcon;");
if (start < 0 || end < start) { console.error("cannot slice econ block"); process.exit(2); }
// The block assigns its helpers onto `window`; give it one to write to.
const mod = new Function("window", src.slice(start, end) +
  "\n return { docUnitEcon, docLineEcon, computeDocEcon };")({});

const fixtures = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = fixtures.map(f => {
  const e = mod.docLineEcon(f.row, {
    costCurrency: f.cost_currency, costPeg: f.rate,
    sellCurrency: f.sell_currency, sellPeg: f.rate,
  });
  const u = mod.docUnitEcon(f.row);
  return { qty: e.qty, sell: e.sell, revenue: e.revenue,
           has_cost: e.hasCost, line_cost: e.lineCost, line_profit: e.lineProfit,
           billable: e.billable,
           unit_sell: u.unitSell, base_qty: u.baseQty, auto: u.auto };
});
console.log(JSON.stringify(out));
"""


class EconParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        fx = tmp / "fixtures.json"
        fx.write_text(json.dumps([
            {"row": row, "cost_currency": cur, "rate": rate, "sell_currency": sc}
            for _, row, cur, rate, sc in FIXTURES
        ]), encoding="utf8")
        harness = tmp / "harness.js"
        harness.write_text(JS_HARNESS, encoding="utf8")

        proc = subprocess.run(
            ["node", str(harness), str(fx)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise AssertionError("JS harness failed:\n" + proc.stdout + proc.stderr)
        cls.js = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_fixture_agrees(self):
        self.assertEqual(len(self.js), len(FIXTURES), "harness returned the wrong count")

        for (label, row, cur, rate, sell_cur), got in zip(FIXTURES, self.js):
            with self.subTest(fixture=label):
                want = database._line_econ(dict(row), "name", cur, rate, sell_cur)
                for key in ("qty", "sell", "revenue", "line_cost", "line_profit"):
                    self.assertAlmostEqual(
                        got[key], want[key], places=6,
                        msg=(f"[{label}] {key}: JS {got[key]!r} vs Python {want[key]!r}.\n"
                             f"    row={row} cost_currency={cur} rate={rate} sell_currency={sell_cur}\n"
                             "    static/app.js docLineEcon is a port of database._line_econ; "
                             "they have drifted."))
                self.assertEqual(
                    bool(got["has_cost"]), bool(want["has_cost"]),
                    f"[{label}] has_cost disagrees - this drives the 'not costed' warning")
                self.assertEqual(
                    bool(got["billable"]), bool(want["billable"]),
                    f"[{label}] billable disagrees - this is the denominator of that warning")

                # The raw detector too. When qty*rate explains a custom column
                # the unit price comes out the same down either branch and only
                # `auto` differs - so without this the branch is untested.
                w_unit, w_qty, w_auto = database._detect_unit_econ(dict(row))
                self.assertAlmostEqual(
                    got["unit_sell"], w_unit, places=6,
                    msg=f"[{label}] _detect_unit_econ unit_sell disagrees")
                self.assertAlmostEqual(
                    got["base_qty"], w_qty, places=6,
                    msg=f"[{label}] _detect_unit_econ base_qty disagrees")
                self.assertEqual(
                    bool(got["auto"]), bool(w_auto),
                    f"[{label}] the `auto` guess flag disagrees - it marks a value "
                    "the user should confirm, and is the ONLY difference between "
                    "the two custom-column branches")

    def test_the_fixtures_actually_exercise_every_branch(self):
        """A parity test over fixtures that all hit the same branch proves nothing."""
        seen = set()
        for label, row, cur, rate, sell_cur in FIXTURES:
            unit, qty, auto = database._detect_unit_econ(dict(row))
            if row.get("total"):
                seen.add("total")
            elif any(k.startswith("col_") for k in row):
                seen.add("custom")
            elif row.get("qty") and row.get("rate"):
                seen.add("qty_rate")
            elif row.get("rate"):
                seen.add("flat")
            else:
                seen.add("empty")
        for branch in ("total", "custom", "qty_rate", "flat", "empty"):
            self.assertIn(branch, seen,
                          f"no fixture exercises the '{branch}' branch of _detect_unit_econ")

    def test_currencies_are_actually_exercised(self):
        combos = {(cur, sc) for _, _, cur, _, sc in FIXTURES}
        for combo in (("USD", "USD"), ("IQD", "USD"), ("USD", "IQD"), ("IQD", "IQD")):
            self.assertIn(combo, combos,
                          f"no fixture covers cost={combo[0]} sell={combo[1]} - the invoice "
                          "editor can hit all four")


if __name__ == "__main__":
    unittest.main(verbosity=2)
