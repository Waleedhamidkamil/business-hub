"""Per-invoice exchange rate (2026-08-04).

The rate an IQD invoice is converted at used to be DERIVED at read time:
`doc_iqd_per_usd` returned the Abraj profile peg of 1400, or 1500 for a cash
deal / the Eshraqat profile. But the market rate moves, so the number that was
right when an invoice was issued is not the number a later lookup returns -
and `invoices.grand_total` (USD, summed by analytics, the dashboard revenue
series and the clients page) is computed from it. An invoice's dollar value
was therefore floating with a rule instead of being fixed on the day.

It is now RECORDED on the invoice. What these tests protect:

  - old invoices must not move. The column is NULL for every invoice issued
    before it existed, and NULL has to keep falling back to the old rule.
  - a recorded rate must beat every derived rule, including cash and Eshraqat,
    or the stored grand_total and a fresh lookup disagree.
  - a nonsense rate must not be storable. The damage is silent and wide: one
    bad number misprices an invoice in every report at once.

Runs against the LIVE profile database, like the rest of the suite, so it only
writes rows under a sentinel invoice number it cleans up itself.
"""
import sys
import sqlite3
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import api, config, database  # noqa: E402

SENTINEL = "INV-TEST-PEG-9901"
SENTINEL_2 = "INV-TEST-PEG-9902"


def _row(inv_no):
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM invoices WHERE inv_no = ?", (inv_no,)).fetchone()


def _forget(*inv_nos):
    with sqlite3.connect(config.DB_PATH) as conn:
        for no in inv_nos:
            # Sentinel numbers only - never a bare DELETE over the table.
            conn.execute("DELETE FROM invoices WHERE inv_no = ?", (no,))
        conn.commit()


class SanePeg(unittest.TestCase):
    """The guard that stands between a typo and every financial report."""

    def test_plain_and_formatted_numbers_pass(self):
        self.assertEqual(api._sane_peg("1500"), 1500.0)
        self.assertEqual(api._sane_peg(1480), 1480.0)
        self.assertEqual(api._sane_peg("1,480"), 1480.0)
        self.assertEqual(api._sane_peg(" 1500 "), 1500.0)

    def test_junk_is_rejected(self):
        for bad in (None, "", "abc", "1500 IQD", {}, []):
            self.assertIsNone(api._sane_peg(bad), f"{bad!r} should be rejected")

    def test_out_of_band_is_rejected(self):
        # A decimal slip (1.5) and an extra zero (15000) are the realistic
        # typos, and both are catastrophic: one values the invoice at a
        # thousand times its worth, the other at a tenth.
        for bad in (0, -1500, 1.5, 99, 100_001, 1_500_000):
            self.assertIsNone(api._sane_peg(bad), f"{bad!r} should be rejected")

    def test_band_edges_are_inclusive(self):
        self.assertEqual(api._sane_peg(api._PEG_MIN), api._PEG_MIN)
        self.assertEqual(api._sane_peg(api._PEG_MAX), api._PEG_MAX)

    def test_rejecting_means_falling_back_not_failing(self):
        # _sane_peg returns None rather than raising precisely so save_inv can
        # carry on with the derived rate. A raise here would block the save.
        self.assertIsNone(api._sane_peg("nonsense"))


class StoredPegWins(unittest.TestCase):
    def setUp(self):
        _forget(SENTINEL, SENTINEL_2)
        self.addCleanup(_forget, SENTINEL, SENTINEL_2)

    def _save(self, inv_no, peg, sell_total=1_500_000.0, grand_usd=1000.0):
        database.save_inv_record(
            inv_no=inv_no, inv_date="4/8/2026", inv_type="materials",
            year=2026, month=8, client_name="TEST", lpo_no="", client_address="",
            payment_bank_key="", withholding_included=True,
            grand_total=grand_usd, pdf_path="C:/nowhere/test.pdf", rows=[],
            sell_currency="IQD", sell_total=sell_total, sell_iqd_per_usd=peg,
        )

    def test_the_rate_is_persisted(self):
        self._save(SENTINEL, 1480.0)
        self.assertAlmostEqual(_row(SENTINEL)["sell_iqd_per_usd"], 1480.0)

    def test_stored_rate_beats_the_derived_rule(self):
        self._save(SENTINEL, 1480.0)
        self.assertAlmostEqual(database.doc_iqd_per_usd("inv", SENTINEL), 1480.0)
        # ...and specifically is NOT the profile peg it would otherwise be.
        self.assertNotAlmostEqual(database.doc_iqd_per_usd("inv", SENTINEL),
                                  config.IQD_PER_USD)

    def test_stored_rate_beats_the_cash_rule(self):
        # A cash deal forces 1500. An invoice explicitly converted at 1480 must
        # still report 1480, or its stored grand_total and a later lookup
        # disagree and the profit figure moves on its own.
        database.save_inv_record(
            inv_no=SENTINEL, inv_date="4/8/2026", inv_type="materials",
            year=2026, month=8, client_name="TEST", lpo_no="", client_address="",
            payment_bank_key="cash", withholding_included=True,
            grand_total=1000.0, pdf_path="C:/nowhere/test.pdf", rows=[],
            sell_currency="IQD", sell_total=1_480_000.0, sell_iqd_per_usd=1480.0,
        )
        self.assertAlmostEqual(database.doc_iqd_per_usd("inv", SENTINEL), 1480.0)

    def test_no_stored_rate_and_no_stamp_falls_back(self):
        """Rewritten 2026-09-21. This used to assert the PROFILE peg - 1400 in
        Abraj, 1500 in Eshraqat. Those two rules are gone: the rate now comes
        from the account the client is paid into, stamped on the document when
        it is saved, and a document with neither falls back to one visible
        number in Settings > Payments.

        This row is written through `save_inv_record`, the storage layer, so it
        carries no stamp - which is exactly the case under test.
        """
        from backend import payment_accounts
        self._save(SENTINEL, None)
        self.assertIsNone(_row(SENTINEL)["sell_iqd_per_usd"])
        self.assertEqual("", database.get_account_stamp("inv", SENTINEL)[0])
        self.assertAlmostEqual(database.doc_iqd_per_usd("inv", SENTINEL),
                               payment_accounts.fallback_rate())

    def test_resave_without_a_rate_does_not_wipe_one(self):
        # save_inv_record is an UPSERT. A later save that happens not to carry
        # the rate (an older code path, a partial payload) must not silently
        # erase it and send the invoice back to the derived rule.
        self._save(SENTINEL, 1480.0)
        self._save(SENTINEL, None)
        self.assertAlmostEqual(_row(SENTINEL)["sell_iqd_per_usd"], 1480.0)

    def test_a_new_rate_does_replace_the_old_one(self):
        self._save(SENTINEL, 1480.0)
        self._save(SENTINEL, 1520.0)
        self.assertAlmostEqual(_row(SENTINEL)["sell_iqd_per_usd"], 1520.0)

    def test_zero_is_not_a_rate(self):
        self._save(SENTINEL, 0)
        self.assertIsNone(_row(SENTINEL)["sell_iqd_per_usd"],
                          "0 must store as NULL, not as a rate - dividing by it "
                          "would produce an infinite dollar value")

    def test_the_stored_rate_reconciles_the_two_totals(self):
        # The invariant that makes the whole feature worth having:
        # sell_total (what the client owes, IQD) / rate == grand_total (USD).
        self._save(SENTINEL, 1480.0, sell_total=1_480_000.0, grand_usd=1000.0)
        row = _row(SENTINEL)
        self.assertAlmostEqual(
            row["sell_total"] / row["sell_iqd_per_usd"], row["grand_total"], places=6,
            msg="the printed dinar total and the USD total in every report must "
                "reconcile through the stored rate")

    def test_other_invoices_are_untouched(self):
        self._save(SENTINEL, 1480.0)
        self._save(SENTINEL_2, None)
        self.assertAlmostEqual(_row(SENTINEL)["sell_iqd_per_usd"], 1480.0)
        self.assertIsNone(_row(SENTINEL_2)["sell_iqd_per_usd"])


class FreshDatabase(unittest.TestCase):
    """A migration alone is not enough - the base DDL has to declare it too.

    Migrations patch databases that already exist. A brand-new one (a fresh
    install, or adding a company profile) is built from `_SCHEMA` and never
    sees them, so a column added only by migration is simply absent there and
    the first invoice save dies with "table invoices has no column named ...".

    That is exactly what happened when this column was added, and it surfaced
    only as a confusing failure in an unrelated profile-isolation test. This
    names it.
    """

    def test_the_base_schema_declares_the_column(self):
        self.assertIn(
            "sell_iqd_per_usd", database._SCHEMA,
            "invoices.sell_iqd_per_usd is missing from the base DDL - every "
            "NEW profile database would lack it and fail on the first invoice")

    def test_a_fresh_database_can_store_a_rate(self):
        import tempfile
        # `with sqlite3.connect(...)` manages the TRANSACTION, not the
        # connection - the file stays open, and on Windows an open file cannot
        # be deleted, so TemporaryDirectory cleanup raises. Close it explicitly.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fresh.db"
            conn = sqlite3.connect(path)
            try:
                conn.executescript(database._SCHEMA)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
                self.assertIn("sell_iqd_per_usd", cols)
                # And it really accepts a write, not just a declaration.
                conn.execute(
                    "INSERT INTO invoices(inv_no, created_at, updated_at, inv_date,"
                    " inv_type, year, month, grand_total, sell_currency, sell_total,"
                    " sell_iqd_per_usd, pdf_path, rows_json)"
                    " VALUES('X-1','n','n','4/8/2026','materials',2026,8,"
                    " 1000,'IQD',1480000,1480,'p','[]')")
                got = conn.execute(
                    "SELECT sell_iqd_per_usd FROM invoices WHERE inv_no='X-1'"
                ).fetchone()[0]
                self.assertAlmostEqual(got, 1480.0)
            finally:
                conn.close()


class BadStoredValues(unittest.TestCase):
    """`_stored_inv_peg` guards the READ side independently of the write side.

    save_inv_record already refuses to store 0, so nothing in normal use can
    produce these rows - which is exactly why the read guard needs its own
    test. Without one, deleting it looks harmless. A 0 returned from
    doc_iqd_per_usd reaches money_to_usd as a divisor, and the invoice's
    dollar value becomes infinity or a crash, depending on the path.

    These write straight to SQL to build the row the write path won't.
    """

    def setUp(self):
        _forget(SENTINEL)
        self.addCleanup(_forget, SENTINEL)
        database.save_inv_record(
            inv_no=SENTINEL, inv_date="4/8/2026", inv_type="materials",
            year=2026, month=8, client_name="TEST", lpo_no="", client_address="",
            payment_bank_key="", withholding_included=True,
            grand_total=1000.0, pdf_path="C:/nowhere/test.pdf", rows=[],
            sell_currency="IQD", sell_total=1_500_000.0, sell_iqd_per_usd=1500.0,
        )

    def _force(self, value):
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("UPDATE invoices SET sell_iqd_per_usd = ? WHERE inv_no = ?",
                         (value, SENTINEL))
            conn.commit()

    def test_zero_and_negative_are_not_returned_as_rates(self):
        for bad in (0, 0.0, -1500):
            self._force(bad)
            self.assertIsNone(
                database._stored_inv_peg(SENTINEL),
                f"a stored {bad!r} must read as 'no rate', not as a rate")
            self.assertGreater(
                database.doc_iqd_per_usd("inv", SENTINEL), 0,
                f"a stored {bad!r} must fall back to a usable rate - it is used "
                "as a DIVISOR when converting the dinar total to USD")

    def test_text_in_the_column_does_not_crash(self):
        # SQLite is dynamically typed: nothing stops a string landing here.
        self._force("not a number")
        self.assertIsNone(database._stored_inv_peg(SENTINEL))
        self.assertGreater(database.doc_iqd_per_usd("inv", SENTINEL), 0)


class UnknownDocs(unittest.TestCase):
    def test_a_missing_invoice_falls_back(self):
        self.assertIsNone(database._stored_inv_peg("INV-DOES-NOT-EXIST-0"))
        self.assertTrue(database.doc_iqd_per_usd("inv", "INV-DOES-NOT-EXIST-0") > 0)

    def test_other_kinds_are_unaffected(self):
        # Only invoices carry a recorded rate; quotes and DNs still derive it.
        for kind in ("quote", "dn"):
            self.assertTrue(database.doc_iqd_per_usd(kind, "WHATEVER-1") > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
