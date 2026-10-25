"""How many times each month an LPO was invoiced - and which months were forgotten.

Waleed, 2026-10-06: "the counting should be based on how many times that LPO
has been invoiced per month - it can help me know if I am forgetting an invoice
for this month or other month". `lpo_billing.months` is that ledger; these are
his real LPOs' shapes, on isolated data, on a fixed day (6 October 2026).

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

from backend import config, database, lpo_billing, profile_manager  # noqa: E402

TODAY = date(2026, 10, 6)


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


class Months(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            for t in ("client_lpos", "invoices", "submitted_docs"):
                conn.execute(f"DELETE FROM {t}")

    def lpo(self, no, **kw):
        base = dict(client_name="HDEC", lpo_no=no, kind="rental", currency="USD",
                    lpo_date=kw.get("period_start", "1/1/2026"))
        base.update(kw)
        self.assertTrue(database.save_lpo(**base)["ok"])
        return database.load_lpo("HDEC", no)

    def invoice(self, no, date_text, lpo_no, pdf="x.pdf", client="HDEC"):
        d, m, y = (int(x) for x in date_text.split("/"))
        database.save_inv_record(
            inv_no=no, inv_date=date_text, inv_type="equipment", year=y, month=m,
            client_name=client, lpo_no=lpo_no, client_address="", payment_bank_key="",
            withholding_included=True, grand_total=1700.0, pdf_path=pdf, rows=[])

    def ledger(self, rec):
        return lpo_billing.months(rec, today=TODAY)

    def counts(self, got):
        return [(m["month"], m["count"], m["state"]) for m in got["months"]]

    # ---------------------------------------------------- the real shapes
    def test_billed_in_the_same_month_counts_from_the_start_month(self):
        """CSSP-RENT-0002: starts 8 June, invoiced on the 30th of each month."""
        rec = self.lpo("RENT-0002", period_start="8/6/2026", period_end="8/1/2027")
        for no, d in (("069", "30/6/2026"), ("070", "31/7/2026"), ("071", "31/8/2026"), ("072", "30/9/2026")):
            self.invoice("INV-" + no, d, "RENT-0002")
        got = self.ledger(rec)
        self.assertEqual((7, 4, 0), (got["months_total"], got["months_invoiced"], got["months_unbilled"]))
        self.assertEqual(self.counts(got)[:5], [
            ("2026-06", 1, "invoiced"), ("2026-07", 1, "invoiced"), ("2026-08", 1, "invoiced"),
            ("2026-09", 1, "invoiced"), ("2026-10", 0, "current")])
        self.assertTrue(got["this_month_due"])
        self.assertEqual("October 2026", got["this_month_label"])

    def test_billed_after_the_month_starts_counting_the_month_after(self):
        """CSSP-RENT-0004 starts 24 July and is first invoiced 17 August -
        starting the count in July would call July forgotten."""
        rec = self.lpo("RENT-0004", period_start="24/7/2026")
        for no, d in (("093", "17/8/2026"), ("094", "17/9/2026"), ("095", "17/10/2026")):
            self.invoice("INV-" + no, d, "RENT-0004")
        got = self.ledger(rec)
        self.assertEqual(got["months"][0]["month"], "2026-08")
        self.assertEqual(0, got["months_unbilled"])
        self.assertFalse(got["this_month_due"], "October is invoiced (dated 17/10)")
        self.assertIsNone(got["term_months"], "no end date: monthly until closed")
        self.assertFalse(got["billing_complete"], "an open-ended contract is never complete")

    def test_a_three_month_term_needs_three_invoices(self):
        """CSSP-RENT-0005(3): 12 Aug - 11 Nov, invoiced Sep, Oct, Nov (dated ahead)."""
        rec = self.lpo("RENT-0005", period_start="12/8/2026", period_end="11/11/2026")
        for no, d in (("096", "11/9/2026"), ("097", "11/10/2026"), ("098", "11/11/2026")):
            self.invoice("INV-" + no, d, "RENT-0005")
        got = self.ledger(rec)
        self.assertEqual(3, got["months_total"])
        self.assertTrue(got["billing_complete"])
        self.assertEqual(3, got["invoices_not_sent"])
        self.assertEqual(self.counts(got), [
            ("2026-09", 1, "invoiced"), ("2026-10", 1, "invoiced"), ("2026-11", 1, "invoiced")],
            "an invoice dated ahead still counts in its own month")

    def test_no_invoice_yet_expects_the_first_one_the_month_after_the_start(self):
        """CSSP-OPEN-0001: diesel since 15 May, never invoiced."""
        rec = self.lpo("OPEN-0001", kind="open", period_start="15/5/2026")
        got = self.ledger(rec)
        self.assertEqual(self.counts(got), [
            ("2026-06", 0, "missed"), ("2026-07", 0, "missed"), ("2026-08", 0, "missed"),
            ("2026-09", 0, "missed"), ("2026-10", 0, "current")])
        self.assertEqual(4, got["months_unbilled"])
        self.assertEqual(("June 2026", "2026-06-01"), (got["oldest_due_month"], got["oldest_due_date"]))

    def test_a_gap_between_invoices_is_a_forgotten_month(self):
        rec = self.lpo("GAP", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-A", "30/6/2026", "GAP")
        self.invoice("INV-C", "31/8/2026", "GAP")
        got = self.ledger(rec)
        self.assertIn(("2026-07", 0, "missed"), self.counts(got))
        self.assertEqual(("July 2026", 2), (got["oldest_due_month"], got["months_unbilled"]))

    def test_a_late_first_invoice_does_not_hide_the_months_before_it(self):
        """Started 8 June, first invoiced 30 September: July and August were
        forgotten, even though counting starts no later than the month after
        the start."""
        rec = self.lpo("LATE", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-L", "30/9/2026", "LATE")
        got = self.ledger(rec)
        self.assertEqual(("July 2026", 2), (got["oldest_due_month"], got["months_unbilled"]))

    def test_two_invoices_in_one_month_show_two(self):
        rec = self.lpo("TWICE", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-1", "30/6/2026", "TWICE")
        self.invoice("INV-2", "30/6/2026", "TWICE")
        june = self.ledger(rec)["months"][0]
        self.assertEqual((2, ["INV-1", "INV-2"]), (june["count"], june["invoices"]))

    # ---------------------------------------------------- what counts
    def test_a_draft_invoice_does_not_count(self):
        rec = self.lpo("DRAFT", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-D", "30/6/2026", "DRAFT", pdf="")
        self.assertEqual(0, self.ledger(rec)["months_invoiced"])

    def test_another_lpos_invoices_do_not_count(self):
        rec = self.lpo("MINE", period_start="8/6/2026", period_end="8/1/2027")
        self.lpo("THEIRS", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-T", "30/6/2026", "THEIRS")
        self.invoice("INV-NA", "30/6/2026", "N/A")
        self.assertEqual(0, self.ledger(rec)["months_invoiced"])

    def test_an_invoice_counts_before_it_is_sent_and_sent_is_tracked(self):
        rec = self.lpo("SENT", period_start="8/6/2026", period_end="8/1/2027")
        self.invoice("INV-S", "30/6/2026", "SENT")
        self.assertEqual((1, 1), (self.ledger(rec)["months_invoiced"], self.ledger(rec)["invoices_not_sent"]))
        database.set_submitted("inv", "INV-S", True)
        self.assertEqual((1, 0), (self.ledger(rec)["months_invoiced"], self.ledger(rec)["invoices_not_sent"]))

    def test_closing_by_hand_stops_the_asking(self):
        rec = self.lpo("CLOSED", kind="open", period_start="15/5/2026")
        database.close_lpo("HDEC", "CLOSED", True, "finished")
        got = self.ledger(database.load_lpo("HDEC", "CLOSED"))
        self.assertEqual(0, got["months_unbilled"])
        self.assertFalse(got["this_month_due"])

    def test_a_materials_order_has_no_months_but_tracks_sending(self):
        rec = self.lpo("MAT", kind="materials", total=100)
        self.invoice("INV-M", "30/6/2026", "MAT")
        got = self.ledger(rec)
        self.assertEqual(([], 1), (got["months"], got["invoices_not_sent"]))

    def test_each_expected_month_carries_the_service_dates_it_covers(self):
        """Handed to the invoice editor so the description says which month."""
        rec = self.lpo("COVER", period_start="12/8/2026", period_end="11/11/2026")
        self.invoice("INV-1", "11/9/2026", "COVER")
        first = self.ledger(rec)["months"][0]
        self.assertEqual(("2026-08-12", "2026-09-11"), (first["start"], first["end"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
