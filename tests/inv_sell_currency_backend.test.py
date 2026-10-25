"""
IQD invoices (2026-07-29).

The contract under test, in one line: an invoice may be ISSUED in dinars, but
`invoices.grand_total` is always USD - because analytics, the dashboard revenue
series and the clients page all reduce that column with a plain SQL SUM, and a
dinar figure landing there would silently inflate every revenue number in the
app by ~1400x.

Covers:
  1. USD invoices are unchanged (sell_total == grand_total, currency 'USD').
  2. An IQD invoice stores the dinar total in sell_total and the USD equivalent
     in grand_total.
  3. Analytics revenue stays in USD across a mixed-currency month.
  4. Costing converts the sell side too, so profit isn't IQD-revenue minus
     USD-cost.
  5. The PDF renderer formats dinars (money + amount-in-words).
  6. The migration backfills sell_total from grand_total for existing rows.

!! These tests run against the LIVE profile database (config.DB_PATH) - this
!! project has no test-DB isolation. Every row written here therefore uses the
!! __SELLCUR__ sentinel in its document number, sits in a far-future month so
!! it can never appear in a real report, and is removed by targeted DELETE ...
!! WHERE. NEVER write an unqualified `DELETE FROM <table>` in this file.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import clients, config, database, migrations  # noqa: E402
from backend import payment_accounts  # noqa: E402
from backend import quote_generator as qg  # noqa: E402

# The rate these fixtures are converted at. Was `config.IQD_PER_USD` until
# 2026-09-21, when the profile peg stopped being the answer: a document's rate
# now comes from the account the client is paid into, stamped when it is saved.
# These rows are written through `save_inv_record`, the storage layer, so they
# carry no stamp and no recorded billing rate - which lands them on the
# fallback. What this file actually pins is that the SELL and COST sides are
# converted symmetrically, and that holds at whatever the rate is.
PEG = payment_accounts.fallback_rate()
TAG = "__SELLCUR__"
YEAR, MONTH = 2099, 1               # far outside any real reporting window
CLIENT = f"Sell Currency Test Client {TAG}"

USD_NO = f"INV-{TAG}-USD"
IQD_NO = f"INV-{TAG}-IQD"
BAD_NO = f"INV-{TAG}-BAD"
FLIP_NO = f"INV-{TAG}-FLIP"
COST_IQD_NO = f"INV-{TAG}-CIQD"
COST_USD_NO = f"INV-{TAG}-CUSD"
ALL_NOS = (USD_NO, IQD_NO, BAD_NO, FLIP_NO, COST_IQD_NO, COST_USD_NO)

IQD_NATIVE = 1_176_000.0


def cleanup():
    """Targeted removal of this file's sentinel rows only."""
    with sqlite3.connect(config.DB_PATH) as c:
        marks = ",".join("?" * len(ALL_NOS))
        c.execute(f"DELETE FROM invoices WHERE inv_no IN ({marks})", ALL_NOS)
        c.execute(f"DELETE FROM doc_cost_meta WHERE doc_no IN ({marks})", ALL_NOS)


def save(inv_no, grand_total, sell_currency="USD", sell_total=None, rows=None):
    database.save_inv_record(
        inv_no=inv_no, inv_date=f"01/{MONTH:02d}/{YEAR}", inv_type="materials",
        year=YEAR, month=MONTH, client_name=CLIENT, lpo_no="N/A",
        client_address="", payment_bank_key="", withholding_included=True,
        grand_total=grand_total, pdf_path=Path("x.pdf"),
        rows=rows if rows is not None else [],
        sell_currency=sell_currency, sell_total=sell_total,
    )


def setUpModule():
    cleanup()


def tearDownModule():
    cleanup()


class SellCurrencyStorage(unittest.TestCase):
    def tearDown(self):
        cleanup()

    def test_usd_invoice_is_unchanged(self):
        save(USD_NO, 840.0)
        rec = database.load_inv(USD_NO)
        self.assertEqual(rec["sell_currency"], "USD")
        self.assertAlmostEqual(rec["grand_total"], 840.0, places=2)
        # sell_total defaults to grand_total, so USD rows need no special case.
        self.assertAlmostEqual(rec["sell_total"], 840.0, places=2)
        self.assertEqual(database.get_inv_sell_currency(USD_NO), "USD")

    def test_iqd_invoice_keeps_native_total_and_usd_equivalent(self):
        save(IQD_NO, database.money_to_usd(IQD_NATIVE, "IQD", PEG),
             sell_currency="IQD", sell_total=IQD_NATIVE)
        rec = database.load_inv(IQD_NO)
        self.assertEqual(rec["sell_currency"], "IQD")
        self.assertAlmostEqual(rec["sell_total"], IQD_NATIVE, places=2)
        self.assertAlmostEqual(rec["grand_total"], IQD_NATIVE / PEG, places=2)
        # The whole point: the USD column holds a sane figure, not 1.1 million.
        self.assertLess(rec["grand_total"], 10_000)
        self.assertEqual(database.get_inv_sell_currency(IQD_NO), "IQD")

    def test_unknown_currency_falls_back_to_usd(self):
        save(BAD_NO, 100.0, sell_currency="EUR")
        self.assertEqual(database.load_inv(BAD_NO)["sell_currency"], "USD")

    def test_sell_currency_of_missing_invoice(self):
        self.assertEqual(database.get_inv_sell_currency("NO-SUCH-INVOICE"), "USD")
        self.assertEqual(database.get_inv_sell_currency(""), "USD")

    def test_resave_updates_currency(self):
        save(FLIP_NO, 840.0)
        save(FLIP_NO, 840.0, sell_currency="IQD", sell_total=840.0 * PEG)
        rec = database.load_inv(FLIP_NO)
        self.assertEqual(rec["sell_currency"], "IQD")
        self.assertAlmostEqual(rec["sell_total"], 840.0 * PEG, places=2)


class RevenueSumsStayUSD(unittest.TestCase):
    """A mixed-currency month must not blow up the revenue figures.

    Asserted as a DELTA against a baseline, since the live DB holds real rows.
    """

    def tearDown(self):
        cleanup()

    def _july_2099(self):
        from backend import analytics
        data = analytics.dashboard_analytics(months_back=3, ref_year=YEAR, ref_month=MONTH)
        month = [m for m in data["months"] if m["ym"] == f"{YEAR:04d}-{MONTH:02d}"]
        self.assertEqual(len(month), 1)
        return month[0]["invoiced"]

    def test_analytics_month_total_is_usd(self):
        before = self._july_2099()
        save(USD_NO, 840.0)
        save(IQD_NO, database.money_to_usd(IQD_NATIVE, "IQD", PEG),
             sell_currency="IQD", sell_total=IQD_NATIVE)
        delta = self._july_2099() - before
        self.assertAlmostEqual(delta, round(840.0 + IQD_NATIVE / PEG, 2), places=1)
        # Guard the actual regression: the raw dinar figure must not be summed.
        self.assertLess(delta, 100_000)

    def test_client_totals_are_usd(self):
        save(USD_NO, 840.0)
        save(IQD_NO, database.money_to_usd(IQD_NATIVE, "IQD", PEG),
             sell_currency="IQD", sell_total=IQD_NATIVE)
        entry = [c for c in clients.client_list()
                 if c["name"].strip().lower() == CLIENT.lower()]
        self.assertEqual(len(entry), 1)
        expected = 840.0 + IQD_NATIVE / PEG
        self.assertAlmostEqual(entry[0]["invoiced_total"], round(expected, 2), places=1)
        self.assertLess(entry[0]["invoiced_total"], 100_000)


class CostingConvertsSellSide(unittest.TestCase):
    """Revenue must not be IQD while cost is USD."""

    def tearDown(self):
        cleanup()

    def test_iqd_invoice_reports_usd_revenue(self):
        # One line: qty 1 @ 1,176,000 IQD sell, cost 700,000 IQD.
        rows = [{"item": "Widget", "qty": "1", "rate": "1176000", "cost": "700000"}]
        save(COST_IQD_NO, IQD_NATIVE / PEG, sell_currency="IQD",
             sell_total=IQD_NATIVE, rows=rows)
        database.set_cost_currency("inv", COST_IQD_NO, "IQD")

        payload = database.load_doc_costing("inv", COST_IQD_NO)
        self.assertEqual(payload["sell_currency"], "IQD")
        totals = payload["totals"]
        self.assertAlmostEqual(totals["revenue"], IQD_NATIVE / PEG, places=2)
        self.assertAlmostEqual(totals["cost"], 700_000.0 / PEG, places=2)
        self.assertAlmostEqual(totals["profit"], (IQD_NATIVE - 700_000.0) / PEG, places=2)
        # Margin is currency-free, so it pins the conversion being symmetric.
        self.assertAlmostEqual(
            totals["margin"], round((IQD_NATIVE - 700_000.0) / IQD_NATIVE * 100, 1),
            places=1)

    def test_usd_invoice_costing_unaffected(self):
        rows = [{"item": "Widget", "qty": "1", "rate": "840", "cost": "500"}]
        save(COST_USD_NO, 840.0, rows=rows)
        payload = database.load_doc_costing("inv", COST_USD_NO)
        self.assertEqual(payload["sell_currency"], "USD")
        self.assertAlmostEqual(payload["totals"]["revenue"], 840.0, places=2)
        self.assertAlmostEqual(payload["totals"]["profit"], 340.0, places=2)


class DinarFormatting(unittest.TestCase):
    def test_money(self):
        self.assertEqual(qg._money(1176000, "IQD"), "IQD 1,176,000")
        self.assertEqual(qg._money(840, "USD"), "$840.00")
        self.assertEqual(qg._money(840), "$840.00")          # default unchanged
        # Dinars are whole units - a fractional value rounds, never shows .00.
        self.assertEqual(qg._money(1176000.4, "IQD"), "IQD 1,176,000")

    def test_amount_in_words(self):
        self.assertEqual(qg.amount_in_words(1, "IQD"), "One Iraqi Dinar")
        self.assertEqual(qg.amount_in_words(2, "IQD"), "Two Iraqi Dinars")
        self.assertEqual(qg.amount_in_words(0, "IQD"), "No Iraqi Dinars")
        self.assertEqual(
            qg.amount_in_words(1176000, "IQD"),
            "One Million One Hundred Seventy Six Thousand Iraqi Dinars")
        # USD wording (and the default) is untouched.
        self.assertEqual(qg.amount_in_words(840.5),
                         "Eight Hundred Forty Dollars and Fifty Cents")
        self.assertEqual(qg.amount_in_words(840.5, "USD"),
                         "Eight Hundred Forty Dollars and Fifty Cents")

    def test_renderer_uses_the_invoice_currency(self):
        from backend import inv_generator
        ctx = {}
        real = inv_generator._env.get_template

        class _Fake:
            def render(self, **kw):
                ctx.update(kw)
                return "<html></html>"

        inv_generator._env.get_template = lambda *a, **k: _Fake()
        try:
            inv_generator.render_inv_html({
                "inv_no": f"INV-{TAG}-R", "date": "01/07/2026",
                "inv_type": "materials", "client_name": CLIENT,
                "sell_currency": "IQD",
                "items": [{"item": "Widget", "qty": "1", "rate": "1176000"}],
            })
        finally:
            inv_generator._env.get_template = real
        self.assertEqual(ctx["grand_amount"], "IQD 1,176,000")
        self.assertIn("Iraqi Dinars", ctx["grand_words"])

    def test_usd_renderer_unchanged(self):
        from backend import inv_generator
        ctx = {}
        real = inv_generator._env.get_template

        class _Fake:
            def render(self, **kw):
                ctx.update(kw)
                return "<html></html>"

        inv_generator._env.get_template = lambda *a, **k: _Fake()
        try:
            inv_generator.render_inv_html({
                "inv_no": f"INV-{TAG}-R2", "date": "01/07/2026",
                "inv_type": "materials", "client_name": CLIENT,
                "items": [{"item": "Widget", "qty": "1", "rate": "840"}],
            })
        finally:
            inv_generator._env.get_template = real
        self.assertEqual(ctx["grand_amount"], "$840.00")
        self.assertEqual(ctx["sell_currency"], "USD")


class MigrationBackfill(unittest.TestCase):
    def test_backfills_sell_total_from_grand_total(self):
        """A pre-2026-07-29 invoices table gains both columns, USD-correct.

        Runs against a scratch DB, never the profile one.
        """
        db = Path(tempfile.mkdtemp(prefix="sellcur_mig_")) / "legacy.db"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("""
                CREATE TABLE invoices (
                    inv_no TEXT PRIMARY KEY,
                    grand_total REAL NOT NULL DEFAULT 0
                )""")
            conn.execute("INSERT INTO invoices(inv_no, grand_total) VALUES('OLD-1', 840.0)")
            conn.commit()

            migrations._inv_sell_currency(conn)

            row = conn.execute("SELECT * FROM invoices WHERE inv_no='OLD-1'").fetchone()
            self.assertEqual(row["sell_currency"], "USD")
            self.assertAlmostEqual(row["sell_total"], 840.0, places=2)
            # Re-running is a no-op, not an error.
            self.assertIn("already present", migrations._inv_sell_currency(conn))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
