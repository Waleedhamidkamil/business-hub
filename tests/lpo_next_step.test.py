"""Every LPO says what to do next - one rule, used by the list and the LPO screen.

Waleed asked for a simpler Client LPOs screen (2026-10-03). The list now carries
a "Next step" per row and the LPO screen leads with the same action, so the
decision lives here once instead of being re-derived in JavaScript.

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

TODAY = date(2026, 10, 3)


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


def line(ordered, delivered=0, invoiced=0):
    return {"item": "x", "ordered": ordered, "delivered": delivered, "invoiced": invoiced}


def step(row, fulfil=None):
    base = {"kind": "materials", "file_exists": True, "closed_at": None,
            "total_usd": 0, "invoiced_usd": 0, "dns": [], "invoices": []}
    base.update(row)
    return database.lpo_next_step(base, fulfil, today=TODAY)


class Materials(unittest.TestCase):
    def test_delivered_but_not_invoiced_asks_for_the_invoice(self):
        """LPO CSSP-ADM-0033: both items delivered on DN-HDEC-067, none invoiced."""
        s = step({"total_usd": 490}, {"lines": [line(1, 1), line(1, 1)]})
        self.assertEqual(("invoice", "Create invoice", True), (s["key"], s["text"], s["action"]))

    def test_nothing_delivered_asks_for_the_delivery_note(self):
        """CSSP-ADM-0017: twelve items ordered on 24 July, none delivered."""
        s = step({"total_usd": 273.4}, {"lines": [line(4), line(8)]})
        self.assertEqual("deliver", s["key"])
        self.assertTrue(s["action"])

    def test_money_comes_before_the_rest_of_the_delivery(self):
        s = step({"total_usd": 100}, {"lines": [line(10, 5, 0)]})
        self.assertEqual("invoice", s["key"])

    def test_invoiced_directly_still_owes_the_delivery(self):
        """The fully invoiced order whose goods have not all gone out yet."""
        s = step({"total_usd": 100, "invoiced_usd": 100}, {"lines": [line(10, 0, 10)]})
        self.assertEqual("deliver", s["key"])

    def test_fully_invoiced_value_never_asks_for_another_invoice(self):
        """Invoice rows that do not match the LPO's line names leave `invoiced` at
        0 per line; the money says it is billed, so no invoice is suggested."""
        s = step({"total_usd": 100, "invoiced_usd": 100}, {"lines": [line(10, 10, 0)]})
        self.assertEqual("done", s["key"])

    def test_everything_delivered_and_invoiced_and_sent_is_done(self):
        """CSSP-ADM-0010: its one invoice is marked sent."""
        s = step({"total_usd": 18390, "invoiced_usd": 18390, "invoices_not_sent": 0},
                 {"lines": [line(60, 60, 60), line(78, 78, 78)]})
        self.assertEqual(("done", "Done", False), (s["key"], s["text"], s["action"]))

    def test_fully_invoiced_but_unsent_is_not_done(self):
        """Waleed, 2026-10-06: done means SENT. Not an action button - the
        To-Do's Unsubmitted list already asks for it - but not Done either."""
        s = step({"total_usd": 18390, "invoiced_usd": 18390, "invoices_not_sent": 1},
                 {"lines": [line(60, 60, 60)]})
        self.assertEqual(("not_sent", "1 invoice not sent yet", False), (s["key"], s["text"], s["action"]))

    def test_no_lines_and_no_documents_asks_for_the_delivery_note(self):
        self.assertEqual("deliver", step({"total_usd": 50}, {"lines": []})["key"])

    def test_no_lines_but_delivered_asks_for_the_invoice(self):
        self.assertEqual("invoice", step({"total_usd": 50, "dns": ["DN-1"]})["key"])

    def test_no_lines_and_fully_invoiced_is_done_once_sent(self):
        s = step({"total_usd": 50, "invoiced_usd": 50, "dns": ["DN-1"], "invoices": ["INV-1"],
                  "invoices_not_sent": 0})
        self.assertEqual("done", s["key"])
        s = step({"total_usd": 50, "invoiced_usd": 50, "dns": ["DN-1"], "invoices": ["INV-1"],
                  "invoices_not_sent": 1})
        self.assertEqual("not_sent", s["key"])


class ItemCounts(unittest.TestCase):
    """The list's "Delivered" column counts LINES, and only finished ones."""

    def test_a_half_delivered_line_is_not_delivered(self):
        out = database._items_rollup({"lines": [line(10, 5), line(4, 4)], "complete": False})
        self.assertEqual((2, 1, 1), (out["lines"], out["delivered"], out["part_delivered"]))

    def test_nothing_delivered_is_nothing(self):
        out = database._items_rollup({"lines": [line(3)], "complete": False})
        self.assertEqual((0, 0), (out["delivered"], out["part_delivered"]))


class Contracts(unittest.TestCase):
    """Rentals and supply contracts follow the month ledger (lpo_billing.months,
    pinned in tests/lpo_month_ledger.test.py); this is what each state says."""

    def contract(self, **kw):
        base = {"kind": "rental", "period_start": "8/6/2026", "period_end": "8/1/2027",
                "months_unbilled": 0, "oldest_due_date": "", "billing_complete": False,
                "invoices_not_sent": 0, "this_month_due": False, "this_month_label": ""}
        base.update(kw)
        return step(base)

    def test_a_forgotten_month_names_the_oldest(self):
        """CSSP-OPEN-0001: June to September ended with no invoice."""
        s = self.contract(kind="open", period_start="15/5/2026", period_end="",
                          months_unbilled=4, oldest_due_date="2026-06-01")
        self.assertEqual(("invoice_period", "Invoice June", True), (s["key"], s["text"], s["action"]))
        self.assertEqual(("2026-06-01", 4), (s["period_start"], s["missed"]))

    def test_this_month_uninvoiced_is_said_but_is_no_alarm(self):
        """CSSP-RENT-0002 in October: its October invoice is not late yet."""
        s = self.contract(this_month_due=True, this_month_label="October 2026")
        self.assertEqual(("this_month", "October not invoiced yet", False), (s["key"], s["text"], s["action"]))

    def test_a_forgotten_month_outranks_this_month(self):
        s = self.contract(months_unbilled=1, oldest_due_date="2026-09-01",
                          this_month_due=True, this_month_label="October 2026")
        self.assertEqual("invoice_period", s["key"])

    def test_every_month_invoiced_and_sent_is_done(self):
        s = self.contract(billing_complete=True, invoices_not_sent=0)
        self.assertEqual(("done", "Done"), (s["key"], s["text"]))

    def test_every_month_invoiced_but_not_sent_says_so(self):
        """CSSP-RENT-0005(3): three months, three invoices, none marked sent."""
        s = self.contract(billing_complete=True, invoices_not_sent=3)
        self.assertEqual(("not_sent", "3 invoices not sent yet", False), (s["key"], s["text"], s["action"]))

    def test_amounts_no_longer_decide_anything(self):
        """CSSP-RENT-0004: $15,600 of $15,600 invoiced, no end date. Under the
        old amount rule it asked for an end date; now it simply needs an invoice
        every month until it is closed - and its months are all invoiced."""
        s = self.contract(period_start="24/7/2026", period_end="", total_usd=15600, invoiced_usd=15600)
        self.assertEqual("ok", s["key"])

    def test_a_contract_ending_within_forty_five_days_says_so(self):
        s = self.contract(period_end="20/10/2026")
        self.assertEqual(("ending", "Ends 20 Oct"), (s["key"], s["text"]))

    def test_nothing_due_is_up_to_date(self):
        self.assertEqual(("ok", "Up to date", False),
                         tuple(self.contract()[k] for k in ("key", "text", "action")))


class Everything(unittest.TestCase):
    def test_closed_by_hand_wins_over_everything(self):
        s = step({"closed_at": "2026-09-01T00:00:00", "total_usd": 100}, {"lines": [line(1, 1)]})
        self.assertEqual(("closed", "Closed", False), (s["key"], s["text"], s["action"]))

    def test_no_document_asks_for_it_when_nothing_else_is_due(self):
        s = step({"kind": "rental", "file_exists": False, "total_usd": 100,
                  "period_start": "1/9/2026", "period_end": "1/9/2027"})
        self.assertEqual(("attach", True), (s["key"], s["action"]))

    def test_money_outranks_a_missing_document(self):
        s = step({"file_exists": False, "total_usd": 100}, {"lines": [line(1, 1)]})
        self.assertEqual("invoice", s["key"])


class FromTheDatabase(unittest.TestCase):
    """`list_lpos` hands every row its next step, and materials rows their items."""

    ORDERED = [{"item": "Currency Counting Machine", "unit": "EA", "qty": 1, "rate": "250"},
               {"item": "High Security Steel Cash Locker", "unit": "EA", "qty": 1, "rate": "240"}]

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM dns")
            conn.execute("DELETE FROM invoices")
        self.assertTrue(database.save_lpo(
            client_name="HDEC", lpo_no="LPO CSSP-ADM-0033", kind="materials",
            total=490, currency="USD", rows=self.ORDERED)["ok"])

    def row(self):
        return next(r for r in database.list_lpos("") if r["lpo_no"] == "LPO CSSP-ADM-0033")

    def test_a_new_order_asks_for_its_delivery_note(self):
        r = self.row()
        self.assertEqual("deliver", r["next_step"]["key"])
        self.assertEqual({"lines": 2, "delivered": 0, "invoiced": 0,
                          "part_delivered": 0}, {k: r["items"][k] for k in
                          ("lines", "delivered", "invoiced", "part_delivered")})

    def test_a_delivery_note_moves_it_to_the_invoice(self):
        database.save_dn_record(dn_no="DN-HDEC-067", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0,
                                pdf_path="DN-HDEC-067.pdf", rows=self.ORDERED)
        r = self.row()
        self.assertEqual(2, r["items"]["delivered"])
        self.assertEqual("invoice", r["next_step"]["key"])

    def test_a_draft_delivery_note_does_not_count(self):
        database.save_dn_record(dn_no="DN-HDEC-068", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0, pdf_path="",
                                rows=self.ORDERED)
        self.assertEqual(0, self.row()["items"]["delivered"])

    def test_the_list_and_the_lpo_screen_agree(self):
        database.save_dn_record(dn_no="DN-HDEC-067", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0,
                                pdf_path="DN-HDEC-067.pdf", rows=self.ORDERED[:1])
        screen = database.lpo_fulfillment("HDEC", "LPO CSSP-ADM-0033")
        listed = self.row()["items"]
        self.assertEqual(sum(1 for l in screen["lines"] if l["delivered"] >= l["ordered"]),
                         listed["delivered"])
        self.assertEqual(1, listed["delivered"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
