"""
`backend/insights.py` must never disagree with the costing engine.

Insights re-implements the *plumbing* around profit - it resolves every
per-document lookup once into an index instead of re-querying per document -
so that "every client over 12 months" is one pass rather than hundreds of
round trips. What it must NOT re-implement is the arithmetic. This suite is
the pin on that boundary:

  1. `_Ctx.peg` vs `database.doc_iqd_per_usd` for EVERY document in the DB.
     The peg picks the IQD→USD rate, so a single wrong branch silently
     divides a cost by 1400 (or fails to).
  2. `_Ctx.cost_currency` vs `database.get_cost_currency`, same coverage.
  3. `insights_overview(1, y, m)` totals vs `database.profit_summary(y, m)` -
     realized, pipeline and the bid tally, month by month.
  4. The window maths (1/3/6/12, clamping, year rollover).
  5. Internal consistency: per-client blocks sum to the company totals, the
     drill-down block equals the overview block, DNs contribute no money, and
     every month row carries the keys the charts read.

!! Read-only. Runs against the LIVE profile DB (see the warning in docs/11)
!! and writes nothing.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import config, database, insights  # noqa: E402


def _all_doc_nos():
    out = []
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for kind, table, pk in (("quote", "quotes", "quote_no"),
                                ("inv", "invoices", "inv_no"),
                                ("dn", "dns", "dn_no")):
            try:
                for r in conn.execute(f"SELECT {pk} FROM {table}"):
                    out.append((kind, r[pk]))
            except sqlite3.Error:
                pass
    return out


def _months_to_probe(n=9):
    """The last `n` months that actually contain documents, newest first."""
    seen = []
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for table in ("quotes", "invoices"):
            try:
                for r in conn.execute(
                        f"SELECT DISTINCT year, month FROM {table} "
                        f"ORDER BY year DESC, month DESC LIMIT ?", (n,)):
                    key = (r["year"], r["month"])
                    if key not in seen:
                        seen.append(key)
            except sqlite3.Error:
                pass
    return seen[:n]


class PegAndCurrencyIndex(unittest.TestCase):
    """The batched index must answer exactly what the real functions answer."""

    @classmethod
    def setUpClass(cls):
        cls.docs = _all_doc_nos()
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cls.ctx = insights._Ctx(conn)
        finally:
            conn.close()

    def test_there_is_something_to_check(self):
        self.assertTrue(self.docs, "no documents in the active profile to check against")

    def test_peg_matches_doc_iqd_per_usd(self):
        bad = []
        for kind, no in self.docs:
            a = database.doc_iqd_per_usd(kind, no)
            b = self.ctx.peg(kind, no)
            if abs(a - b) > 1e-9:
                bad.append((kind, no, a, b))
        self.assertEqual(bad, [], f"batched peg disagrees on {len(bad)} document(s)")

    def test_cost_currency_matches(self):
        bad = [(k, n, database.get_cost_currency(k, n), self.ctx.cost_currency(k, n))
               for k, n in self.docs
               if database.get_cost_currency(k, n) != self.ctx.cost_currency(k, n)]
        self.assertEqual(bad, [])

    def test_pinned_rate_beats_everything(self):
        """The precedence order is the whole point of `peg` - pin one and it wins.

        Uses a sentinel document number that cannot collide with a real one
        (the live DB has no isolation - see docs/11)."""
        ctx = self.ctx
        sentinel = "ZZ-INSIGHTS-TEST-0001"
        ctx.cost_meta[("inv", sentinel)] = ("IQD", 1234.0)
        ctx.inv_meta[sentinel] = {"sell_currency": "IQD", "sell_peg": 9999.0,
                                  "source_quote_no": "", "cash": True, "grand_total": 0.0}
        ctx.cash_invs.add(sentinel)
        try:
            self.assertEqual(ctx.peg("inv", sentinel), 1234.0,
                             "a pinned doc_cost_meta.rate must beat the stored "
                             "invoice rate, the cash-deal rule and the profile peg")
            del ctx.cost_meta[("inv", sentinel)]
            self.assertEqual(ctx.peg("inv", sentinel), 9999.0,
                             "with no pin, the invoice's own recorded rate wins")
        finally:
            ctx.cost_meta.pop(("inv", sentinel), None)
            ctx.inv_meta.pop(sentinel, None)
            ctx.cash_invs.discard(sentinel)

    def test_eshraqat_always_settles_at_the_market_rate(self):
        """Pinned directly, because the live constants cannot show it.

        `config.IQD_PER_USD` is 1500 under eshraqat and `CASH_IQD_PER_USD` is
        also 1500, so on real data the branch and its fallback return the same
        number - deleting it changes nothing a data-driven test can see. Give
        the index two DIFFERENT pegs and the branch has to speak for itself."""
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            ctx = insights._Ctx(conn)
        finally:
            conn.close()
        ctx.profile_peg, ctx.cash_peg = 1400.0, 1500.0
        ctx.eshraqat = True
        self.assertEqual(ctx.peg("quote", "ZZ-NOT-A-REAL-DOC"), 1500.0)
        ctx.eshraqat = False
        self.assertEqual(ctx.peg("quote", "ZZ-NOT-A-REAL-DOC"), 1400.0)


class DocumentEconomics(unittest.TestCase):
    """`_doc_econ` on hand-built documents.

    The parity suite above can only test what the live database happens to
    contain - it has no invoice with an extra cost, for example, so a mutation
    that drops `extra_total` from `spent` survives there. These cases pin the
    arithmetic itself, independent of the data."""

    @classmethod
    def setUpClass(cls):
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            cls.ctx = insights._Ctx(conn)
        finally:
            conn.close()
        # A sentinel doc number that cannot exist: keeps this off every real row.
        cls.no = "ZZ-INSIGHTS-ECON-0001"

    def econ(self, rows, extra="[]", currency="USD", rate=None, want_items=False):
        self.ctx.cost_meta[("inv", self.no)] = (currency, rate)
        try:
            return insights._doc_econ(self.ctx, "inv", self.no, "materials",
                                      rows, extra, want_items)
        finally:
            self.ctx.cost_meta.pop(("inv", self.no), None)

    def test_qty_times_rate_minus_qty_times_cost(self):
        e = self.econ('[{"item":"A","qty":"3","rate":"100","cost":"60"}]')
        self.assertAlmostEqual(e["charged"], 300.0)
        self.assertAlmostEqual(e["spent"], 180.0)
        self.assertAlmostEqual(e["profit"], 120.0)
        self.assertTrue(e["costed"])

    def test_extra_costs_are_part_of_spent(self):
        """Delivery, customs, a fixer's fee - money out that is not on a line."""
        rows = '[{"item":"A","qty":"1","rate":"500","cost":"200"}]'
        plain = self.econ(rows)
        with_extra = self.econ(rows, extra='[{"label":"Freight","amount":"75"}]')
        self.assertAlmostEqual(plain["spent"], 200.0)
        self.assertAlmostEqual(with_extra["spent"], 275.0,
                               msg="extra costs must count toward `spent`")
        self.assertAlmostEqual(with_extra["profit"], 225.0)

    def test_an_iqd_document_converts_costs_and_extras_at_its_rate(self):
        rows = '[{"item":"A","qty":"1","rate":"100","cost":"140000"}]'
        e = self.econ(rows, extra='[{"label":"Freight","amount":"14000"}]',
                      currency="IQD", rate=1400.0)
        self.assertAlmostEqual(e["spent"], 110.0, places=6,
                               msg="140000 + 14000 IQD at 1400 = $110")
        self.assertAlmostEqual(e["charged"], 100.0)

    def test_a_row_can_override_the_document_currency(self):
        """One table may hold a dollar-costed line beside a dinar-costed one."""
        rows = ('[{"item":"A","qty":"1","rate":"100","cost":"40","cost_ccy":"USD"},'
                ' {"item":"B","qty":"1","rate":"100","cost":"14000","cost_ccy":"IQD"}]')
        e = self.econ(rows, currency="IQD", rate=1400.0)
        self.assertAlmostEqual(e["spent"], 50.0, places=6,
                               msg="$40 stays $40; 14000 IQD becomes $10")

    def test_every_line_counts_not_just_the_first(self):
        e = self.econ('[{"item":"A","qty":"1","rate":"10","cost":"4"},'
                      ' {"item":"B","qty":"2","rate":"20","cost":"5"}]')
        self.assertAlmostEqual(e["charged"], 50.0)
        self.assertAlmostEqual(e["spent"], 14.0)

    def test_a_line_with_no_cost_marks_the_document_uncosted(self):
        e = self.econ('[{"item":"A","qty":"1","rate":"100","cost":"60"},'
                      ' {"item":"B","qty":"1","rate":"100"}]')
        self.assertFalse(e["costed"], "one costless billable line = not fully costed")
        # Changed 2026-10-21 (Waleed): the missing cost is no longer zero - it
        # is estimated at config.UNCOSTED_MARKUP (40% on cost), so B costs
        # 100 / 1.4 and the document is NOT reported at a 70% margin.
        self.assertAlmostEqual(e["profit"], 200.0 - 60.0 - 100.0 / 1.4, places=6,
                               msg="a costless line is estimated at a 40% markup, "
                                   "never counted as pure profit")
        self.assertAlmostEqual(e["estimated"], 100.0 / 1.4, places=6)

    def test_items_are_only_built_when_asked(self):
        rows = '[{"item":"Widget","qty":"2","rate":"50","cost":"20"}]'
        self.assertEqual(self.econ(rows)["items"], [])
        items = self.econ(rows, want_items=True)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Widget")
        self.assertAlmostEqual(items[0]["charged"], 100.0)
        self.assertAlmostEqual(items[0]["spent"], 40.0)

    def test_malformed_json_is_zero_not_a_crash(self):
        for bad in (None, "", "not json", "{}", "[1,2,3]"):
            e = self.econ(bad, extra="also not json")
            self.assertEqual((e["charged"], e["spent"]), (0.0, 0.0))


class MoneyMatchesProfitSummary(unittest.TestCase):
    """A one-month window must equal what the costing engine reports."""

    def test_month_by_month(self):
        months = _months_to_probe()
        if not months:
            self.skipTest("no documents in the active profile")
        checked = 0
        for (y, m) in months:
            s = database.profit_summary(y, m)
            d = insights.insights_overview(1, y, m)
            T = d["totals"]
            # `profit_summary` sums each document's ALREADY-ROUNDED totals;
            # insights sums the raw figures and rounds once at the end. That is
            # worth at most a cent per document, and no more - a real
            # disagreement (a wrong peg, a missed extra cost) is dollars.
            docs = s["realized"]["count"] + s["pipeline"]["count"]
            tol = max(0.05, 0.01 * docs)
            for label, a, b in (
                ("charged", s["realized"]["revenue"], T["charged"]),
                ("spent", s["realized"]["cost"], T["spent"]),
                ("profit", s["realized"]["profit"], T["profit"]),
                ("pipeline value", s["pipeline"]["revenue"], T["pipeline_value"]),
                ("pipeline profit", s["pipeline"]["profit"], T["pipeline_profit"]),
            ):
                self.assertAlmostEqual(
                    float(a), float(b), delta=tol,
                    msg=f"{y}-{m:02d} {label}: profit_summary={a} insights={b}")
            for label in ("won", "lost", "open"):
                self.assertEqual(s["bids"][label], T[label],
                                 f"{y}-{m:02d} {label} bid count")
            checked += 1
        self.assertGreater(checked, 0)

    def test_a_longer_window_is_the_sum_of_its_months(self):
        months = _months_to_probe(3)
        if len(months) < 2:
            self.skipTest("need at least two months with documents")
        end_y, end_m = months[0]
        wide = insights.insights_overview(3, end_y, end_m)
        per_month = [insights.insights_overview(1, r["y"], r["m"])["totals"]["charged"]
                     for r in wide["series"]]
        self.assertAlmostEqual(sum(per_month), wide["totals"]["charged"], places=2)


class WindowMath(unittest.TestCase):
    def test_lengths(self):
        for n in (1, 3, 6, 12):
            window, ey, em, got = insights.normalize_window(n, 2026, 8)
            self.assertEqual(len(window), n)
            self.assertEqual(got, n)
            self.assertEqual((ey, em), (2026, 8))
            self.assertEqual(window[-1], (2026, 8), "window ends at the chosen month")

    def test_oldest_first_and_contiguous(self):
        window, _, _, _ = insights.normalize_window(6, 2026, 3)
        self.assertEqual(window, [(2025, 10), (2025, 11), (2025, 12),
                                  (2026, 1), (2026, 2), (2026, 3)])

    def test_clamps(self):
        # 0 / None / "" are "no preference" -> the 12-month default, matching the
        # `x or default` idiom the rest of the backend uses. A negative is a
        # nonsense LENGTH, so it clamps to the shortest real window instead.
        self.assertEqual(len(insights.normalize_window(0, 2026, 8)[0]), 12)
        self.assertEqual(len(insights.normalize_window(-5, 2026, 8)[0]), 1)
        self.assertEqual(len(insights.normalize_window(999, 2026, 8)[0]),
                         max(insights.ALLOWED_WINDOWS))
        self.assertEqual(insights.normalize_window(3, 2026, 99)[2], 12)
        self.assertEqual(insights.normalize_window(3, 2026, -5)[2], 1)

    def test_garbage_falls_back_to_now(self):
        from datetime import datetime
        now = datetime.now()
        _, ey, em, n = insights.normalize_window("abc", None, None)
        self.assertEqual((ey, em), (now.year, now.month))
        self.assertEqual(n, 12)

    def test_period_label_collapses_for_one_month(self):
        one, _, _, _ = insights.normalize_window(1, 2026, 8)
        self.assertEqual(insights._period_label(one), "Aug 2026")
        many, _, _, _ = insights.normalize_window(3, 2026, 8)
        self.assertEqual(insights._period_label(many), "Jun 2026 – Aug 2026")


class Consistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        months = _months_to_probe(1)
        cls.end = months[0] if months else (2026, 8)
        cls.ov = insights.insights_overview(12, *cls.end)

    def test_client_blocks_sum_to_the_company(self):
        cl = self.ov["clients"]
        for key in ("charged", "spent", "profit", "outstanding", "collected",
                    "pipeline_value", "pipeline_profit"):
            self.assertAlmostEqual(
                round(sum(c[key] for c in cl), 2), self.ov["totals"][key], places=1,
                msg=f"clients' {key} does not add up to the company total")
        for key in ("invoices", "quotes", "dns", "won", "lost", "open", "uncosted"):
            self.assertEqual(sum(c[key] for c in cl), self.ov["totals"][key], key)

    def test_series_sums_to_the_company(self):
        for key in ("charged", "spent", "profit"):
            self.assertAlmostEqual(round(sum(r[key] for r in self.ov["series"]), 2),
                                   self.ov["totals"][key], places=1, msg=key)

    def test_every_month_row_carries_what_the_charts_read(self):
        """The margin chart reads `margin` straight off the row; a row without
        it plots NaN and prints 'average NaN%' (that shipped once)."""
        need = {"label", "charged", "spent", "profit", "margin"}
        for row in self.ov["series"]:
            self.assertTrue(need <= set(row), f"company month row missing {need - set(row)}")
        for c in self.ov["clients"]:
            for row in c["months"]:
                self.assertTrue(need <= set(row),
                                f"{c['name']} month row missing {need - set(row)}")

    def test_margin_is_profit_over_charged(self):
        for row in self.ov["series"]:
            expected = round(row["profit"] / row["charged"] * 100, 1) if row["charged"] else 0.0
            self.assertAlmostEqual(row["margin"], expected, places=1)

    def test_client_drilldown_matches_the_overview_block(self):
        if not self.ov["clients"]:
            self.skipTest("no clients in this window")
        top = self.ov["clients"][0]
        d = insights.client_insights(top["name"], 12, *self.end)
        self.assertTrue(d["ok"])
        for key in ("charged", "spent", "profit", "margin", "invoices",
                    "quotes", "dns", "outstanding", "won", "lost", "open"):
            self.assertEqual(d["client"][key], top[key],
                             f"drill-down disagrees with the leaderboard on {key}")

    def test_drilldown_documents_add_up_to_the_block(self):
        if not self.ov["clients"]:
            self.skipTest("no clients in this window")
        top = self.ov["clients"][0]
        d = insights.client_insights(top["name"], 12, *self.end)
        invs = [x for x in d["docs"] if x["kind"] == "inv"]
        self.assertAlmostEqual(round(sum(x["charged"] for x in invs), 2),
                               d["client"]["charged"], places=1)
        self.assertAlmostEqual(round(sum(x["spent"] for x in invs), 2),
                               d["client"]["spent"], places=1)
        # Paid / unpaid split, cross-checked against the per-document flag
        # rather than against itself - outstanding + collected = charged holds
        # even when every invoice is misclassified.
        paid = round(sum(x["charged"] for x in invs if x["paid"]), 2)
        unpaid = round(sum(x["charged"] for x in invs if not x["paid"]), 2)
        self.assertAlmostEqual(paid, d["client"]["collected"], places=1,
                               msg="collected must be the PAID invoices' money")
        self.assertAlmostEqual(unpaid, d["client"]["outstanding"], places=1,
                               msg="outstanding must be the UNPAID invoices' money")
        self.assertEqual(sum(1 for x in invs if not x["paid"]),
                         d["client"]["outstanding_count"])

    def test_delivery_notes_carry_no_money(self):
        if not self.ov["clients"]:
            self.skipTest("no clients in this window")
        top = self.ov["clients"][0]
        d = insights.client_insights(top["name"], 12, *self.end)
        for x in d["docs"]:
            if x["kind"] == "dn":
                self.assertEqual((x["charged"], x["spent"], x["profit"]), (0.0, 0.0, 0.0),
                                 "a delivery note must never add money - the invoice does")

    def test_pipeline_counts_open_quotes_only(self):
        """Won quotes are counted through their invoice; counting the quote too
        would double the money. Lost ones are not money at all."""
        self.assertGreaterEqual(self.ov["totals"]["pipeline_value"], 0.0)
        if self.ov["totals"]["open"] == 0:
            self.assertEqual(self.ov["totals"]["pipeline_value"], 0.0)

    def test_unknown_client_returns_an_empty_block_not_an_error(self):
        d = insights.client_insights("ZZ-No-Such-Client-0001", 6)
        self.assertTrue(d["ok"])
        self.assertEqual(d["client"]["charged"], 0.0)
        self.assertEqual(d["client"]["docs"], 0)
        self.assertIsNone(d["client"]["rank"])
        self.assertEqual(len(d["client"]["months"]), 6)

    def test_blank_client_name_is_rejected(self):
        self.assertFalse(insights.client_insights("   ")["ok"])

    def test_ranks_are_dense_and_ordered_by_profit(self):
        cl = self.ov["clients"]
        self.assertEqual([c["rank"] for c in cl], list(range(1, len(cl) + 1)))
        profits = [c["profit"] for c in cl]
        self.assertEqual(profits, sorted(profits, reverse=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
