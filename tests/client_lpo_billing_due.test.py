"""Rentals and supply contracts bill every month, and the app should say so.

CSSP-RENT-0002 runs seven months from 2026-06-08. CSSP-OPEN-0001 supplies diesel
every month with no period recorded at all. Nothing in the app notices a month
going by unbilled, so the reminder is computed from the period and the invoices
already linked to the LPO.

One entry per LPO, not per missing month: a per-month row would let a single
forgotten contract fill the feed, and the mute key is (kind, doc_no), so muting
would be all-or-nothing anyway.

Isolation: see the note in `client_lpo_save.test.py`.
"""
import sys
import unittest
from datetime import date
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, profile_manager  # noqa: E402

TODAY = date(2026, 9, 11)


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


class MonthSequence(unittest.TestCase):
    def test_a_single_month(self):
        self.assertEqual([(2026, 6)],
                         database._month_seq(date(2026, 6, 8), date(2026, 6, 30)))

    def test_it_crosses_a_year_boundary(self):
        self.assertEqual([(2026, 11), (2026, 12), (2027, 1)],
                         database._month_seq(date(2026, 11, 5), date(2027, 1, 20)))

    def test_an_end_before_the_start_yields_nothing(self):
        self.assertEqual([], database._month_seq(date(2026, 6, 1), date(2026, 5, 1)))


class TermLength(unittest.TestCase):
    """How long a rental runs for, as the PO states it.

    NOT the number of calendar months it touches. CSSP-RENT-0002 runs 8 June to
    8 January - eight calendar months, but the PO says "The contract term is
    seven months". The screen has to agree with the paper the client signed.
    """

    def months(self, start, end):
        return database._term_months(date(*start), date(*end))

    def test_their_seven_month_term_reads_as_seven(self):
        self.assertEqual(7, self.months((2026, 6, 8), (2027, 1, 8)),
                         "CSSP-RENT-0002 prints 'the contract term is seven months'")

    def test_it_is_not_the_count_of_calendar_months_touched(self):
        """The mistake this replaced: June through January is eight names."""
        self.assertEqual(
            8, len(database._month_seq(date(2026, 6, 8), date(2027, 1, 8))),
            "precondition: the month walk still counts eight, and should")

    def test_one_month(self):
        self.assertEqual(1, self.months((2026, 6, 8), (2026, 7, 8)))

    def test_a_full_year(self):
        self.assertEqual(12, self.months((2026, 6, 8), (2027, 6, 8)))

    def test_a_partial_final_month_still_counts_as_a_period(self):
        # The final 20 Dec - 5 Jan service period must not disappear.
        self.assertEqual(7, self.months((2026, 6, 20), (2027, 1, 5)))

    def test_under_a_month_still_counts_as_one(self):
        """It is still a month that gets billed."""
        self.assertEqual(1, self.months((2026, 6, 1), (2026, 6, 30)))


class MonthsUnbilled(unittest.TestCase):
    def months(self, today=TODAY, **kw):
        base = {"kind": "rental", "client_name": "HDEC", "lpo_no": "CSSP-RENT-0002",
                "period_start": "8/6/2026", "period_end": "8/1/2027",
                "lpo_date": "4/6/2026", "closed_at": None, "invoice_months": set()}
        base.update(kw)
        return database._lpo_months_unbilled(base, today=today)

    def test_a_materials_po_never_raises_a_billing_reminder(self):
        """A one-off order is delivered once, not billed monthly."""
        self.assertEqual([], self.months(kind="materials"))

    def test_every_month_since_the_period_started_is_due(self):
        """Started 8 June, today is 11 September: June, July, August, September."""
        self.assertEqual([(2026, 6), (2026, 7), (2026, 8), (2026, 9)], self.months())

    def test_an_invoiced_month_drops_out(self):
        self.assertEqual([(2026, 6), (2026, 8), (2026, 9)],
                         self.months(invoice_months={(2026, 7)}))

    def test_a_fully_invoiced_run_asks_for_nothing(self):
        self.assertEqual([], self.months(invoice_months={
            (2026, 6), (2026, 7), (2026, 8), (2026, 9)}))

    def test_nothing_is_due_past_the_period_end(self):
        """A rental that ended in July must not ask about August."""
        self.assertEqual([], self.months(period_end="8/7/2026",
                                         invoice_months={(2026, 6), (2026, 7)}))

    def test_the_period_end_does_not_reach_into_the_future(self):
        """The term runs to January, but you cannot invoice October yet."""
        self.assertEqual([(2026, 6), (2026, 7), (2026, 8), (2026, 9)],
                         self.months(period_end="8/1/2027"))

    def test_a_closed_contract_stops_asking(self):
        self.assertEqual([], self.months(closed_at="2026-07-01T00:00:00"))

    def test_an_open_contract_counts_from_its_lpo_date_when_no_period_was_given(self):
        """CSSP-OPEN-0001 carries no period at all, only its own date."""
        self.assertEqual([(2026, 8), (2026, 9)],
                         self.months(kind="open", period_start="", period_end="",
                                     lpo_date="15/8/2026"))

    def test_a_period_start_wins_over_the_lpo_date(self):
        """The PO is dated before the rental begins - RENT-0002 is dated 4 June
        and starts on the 8th. Billing follows the period, not the paperwork."""
        self.assertEqual([(2026, 8), (2026, 9)],
                         self.months(period_start="1/8/2026", lpo_date="4/6/2026"))

    def test_a_contract_that_has_not_started_is_not_overdue(self):
        self.assertEqual([], self.months(period_start="1/12/2026"))

    def test_no_date_at_all_asks_for_nothing(self):
        """Without a start there is no month to name, and inventing one would
        put a reminder on screen that nobody can act on."""
        self.assertEqual([], self.months(period_start="", lpo_date=""))


class MonthLabel(unittest.TestCase):
    def test_it_reads_like_a_person_wrote_it(self):
        self.assertEqual("August 2026", database._month_label(2026, 8))

    def test_a_nonsense_month_does_not_crash_the_feed(self):
        self.assertIn("2026", database._month_label(2026, 0))


class Bucket(unittest.TestCase):
    def test_billing_can_be_muted(self):
        """A bucket with no Ignore button can never be silenced, and this one
        will fire every month forever."""
        self.assertIn("billing", database._ATTENTION_CATEGORIES)


class InvoiceMonths(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM invoices")
        self.assertTrue(database.save_lpo(
            client_name="HDEC", lpo_no="CSSP-RENT-0002", kind="rental",
            total=11900, currency="USD", monthly_amount=850,
            period_start="8/6/2026", period_end="8/1/2027")["ok"])
        self.row = {"client_name": "HDEC", "lpo_no": "CSSP-RENT-0002"}

    def bill(self, inv_no, year, month, lpo_no="CSSP-RENT-0002", pdf="x.pdf"):
        database.save_inv_record(
            inv_no=inv_no, inv_date=f"1/{month}/{year}", inv_type="MT",
            year=year, month=month, client_name="HDEC", lpo_no=lpo_no,
            client_address="", payment_bank_key="", withholding_included=1,
            grand_total=850, pdf_path=pdf, rows=[])

    def test_an_invoice_against_the_lpo_marks_its_month(self):
        self.bill("INV-MT-HDEC-071", 2026, 7)
        self.assertEqual({(2026, 7)}, database._lpo_invoice_months(self.row))

    def test_an_invoice_for_another_lpo_marks_nothing(self):
        self.bill("INV-MT-HDEC-072", 2026, 7, lpo_no="CSSP-ADM-0010")
        self.assertEqual(set(), database._lpo_invoice_months(self.row))

    def test_a_draft_invoice_does_not_count_as_billed(self):
        """An invoice with no PDF is an editable draft, not a bill you sent -
        the same rule every other issued-document read follows."""
        self.bill("INV-MT-HDEC-073", 2026, 8, pdf="")
        self.assertEqual(set(), database._lpo_invoice_months(self.row))

    def test_the_months_feed_straight_into_the_reminder(self):
        self.bill("INV-MT-HDEC-071", 2026, 6)
        self.bill("INV-MT-HDEC-074", 2026, 7)
        due = database._lpo_months_unbilled(
            {**database.load_lpo("HDEC", "CSSP-RENT-0002"),
             "invoice_months": database._lpo_invoice_months(self.row)}, today=TODAY)
        self.assertEqual([(2026, 8), (2026, 9)], due)


class TheBucketReachesTheFeed(unittest.TestCase):
    """Wiring, not arithmetic: the reminder is worthless if it never surfaces."""

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM invoices")
            conn.execute("DELETE FROM attention_ignores")

    def billing(self):
        return database.dashboard_needs_attention().get("billing", [])

    def test_a_running_rental_with_an_unbilled_month_appears(self):
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-RENT-0002",
                          kind="rental", total=11900, currency="USD",
                          monthly_amount=850, lpo_date="4/6/2026",
                          period_start="8/6/2026", period_end="8/1/2027")
        rows = self.billing()
        self.assertEqual(1, len(rows), rows)
        entry = rows[0]
        self.assertEqual("CSSP-RENT-0002", entry["doc_no"])
        self.assertEqual("lpo", entry["kind"], "the To-Do routes on kind")
        self.assertIn("not invoiced", entry["note"])
        # With no invoice yet, the first one is expected the month AFTER the
        # start - HDEC is billed after each month of service (2026-10-06).
        self.assertIn("July 2026", entry["note"],
                      "the note must name the oldest month a person can act on")

    def test_a_materials_po_never_appears(self):
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-ADM-0010",
                          kind="materials", total=18390, currency="USD",
                          lpo_date="9/7/2026")
        self.assertEqual([], self.billing())

    def test_one_entry_per_lpo_not_per_month(self):
        """Four months owing is one row saying four, not four rows."""
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-OPEN-0001",
                          kind="open", currency="USD", lpo_date="15/5/2026")
        rows = self.billing()
        self.assertEqual(1, len(rows))
        self.assertGreater(rows[0]["months_due"], 1)

    def test_a_muted_contract_drops_out_and_is_listed_as_ignored(self):
        """This fires every month forever, so it MUST be silenceable."""
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-RENT-0002",
                          kind="rental", total=11900, currency="USD",
                          lpo_date="4/6/2026", period_start="8/6/2026",
                          period_end="8/1/2027")
        self.assertEqual(1, len(self.billing()))
        self.assertTrue(database.set_attention_ignore(
            "lpo", "CSSP-RENT-0002", "billing", True).get("ok"))
        feed = database.dashboard_needs_attention()
        self.assertEqual([], feed["billing"], "a muted contract must leave the bucket")
        self.assertIn("CSSP-RENT-0002", [e["doc_no"] for e in feed["ignored"]],
                      "and must stay restorable from Ignored")

    def test_a_broken_lpo_store_does_not_take_the_todo_down(self):
        """Every other bucket still has to render."""
        from unittest.mock import patch
        with patch.object(database, "list_lpos", side_effect=RuntimeError("gone")):
            feed = database.dashboard_needs_attention()
        self.assertTrue(feed["ok"])
        self.assertEqual([], feed["billing"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
