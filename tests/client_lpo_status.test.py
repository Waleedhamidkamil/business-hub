"""Where an LPO has reached - and who decides that it is finished.

Until 2026-10-06 `_lpo_status` closed an LPO by itself, per kind: a materials
order when its value was invoiced, a rental when its term ran out. Waleed's rule
replaced that: an LPO is DONE when everything it needs is invoiced and every
invoice is marked SENT - for every kind. That needs the month ledger and the
line fulfilment, so `list_lpos` closes the row when `lpo_next_step` says
`done` (pinned in tests/lpo_next_step.test.py and tests/lpo_month_ledger.test.py).

What stays here: the ladder below closed, and closing by hand.

Pure functions over dicts. Touches no database.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


def lpo(**kw):
    base = {"kind": "materials", "total_usd": 0.0, "invoiced_usd": 0.0,
            "dns": [], "invoices": [], "acknowledged": False,
            "period_start": "", "period_end": "", "closed_at": None}
    base.update(kw)
    return base


class LpoStatus(unittest.TestCase):
    """The status ladder never finishes an LPO on its own any more."""

    def test_a_fully_invoiced_materials_po_is_invoiced_not_closed(self):
        """Its invoice may still be unsent - that is for the next step to say."""
        self.assertEqual("invoiced", database._lpo_status(
            lpo(total_usd=18390, invoiced_usd=18390, invoices=["INV-MT-HDEC-043"])))

    def test_a_materials_po_short_of_its_value_is_only_invoiced(self):
        self.assertEqual("invoiced", database._lpo_status(
            lpo(total_usd=18390, invoiced_usd=8100, invoices=["INV-MT-HDEC-043"])))

    def test_an_open_contract_never_closes_on_value(self):
        """Invoicing one tank of diesel must not retire the whole contract."""
        self.assertNotEqual("closed", database._lpo_status(
            lpo(kind="open", total_usd=0.77, invoiced_usd=1240.0,
                invoices=["INV-MT-HDEC-070"])))

    def test_an_open_contract_closes_only_when_closed_by_hand(self):
        self.assertEqual("closed", database._lpo_status(
            lpo(kind="open", closed_at="2026-09-11T10:00:00")))

    def test_a_rental_past_its_end_is_not_closed_by_the_date(self):
        """A term that ran out may still have months uninvoiced or invoices
        unsent; the date alone used to call it finished."""
        self.assertNotEqual("closed", database._lpo_status(
            lpo(kind="rental", period_start="8/1/2026", period_end="8/2/2026")))

    def test_a_rental_with_no_period_recorded_does_not_close_itself(self):
        self.assertNotEqual("closed", database._lpo_status(
            lpo(kind="rental", period_start="", period_end="")))

    def test_an_early_termination_closes_a_running_rental(self):
        """Their PO says the term may be terminated earlier if necessary."""
        self.assertEqual("closed", database._lpo_status(
            lpo(kind="rental", period_start="8/6/2026", period_end="8/1/2027",
                closed_at="2026-09-11T10:00:00", close_reason="terminated early")))

    def test_the_ladder_below_closed_is_unchanged(self):
        self.assertEqual("received", database._lpo_status(lpo()))
        self.assertEqual("acknowledged", database._lpo_status(lpo(acknowledged=True)))
        self.assertEqual("delivered", database._lpo_status(
            lpo(acknowledged=True, dns=["DN-HDEC-046"])))
        self.assertEqual("invoiced", database._lpo_status(
            lpo(acknowledged=True, dns=["DN-HDEC-046"], invoices=["INV-1"])))


class OverValue(unittest.TestCase):
    def test_a_materials_po_over_its_total_is_flagged(self):
        self.assertTrue(database.lpo_is_over_value(
            lpo(total_usd=100, invoiced_usd=150)))

    def test_an_open_contract_has_no_ceiling_to_exceed(self):
        self.assertFalse(database.lpo_is_over_value(
            lpo(kind="open", total_usd=0.77, invoiced_usd=1240)))

    def test_an_unrecorded_total_is_not_a_ceiling_of_zero(self):
        self.assertFalse(database.lpo_is_over_value(
            lpo(total_usd=0, invoiced_usd=150)))

    def test_a_rounding_cent_is_not_an_overrun(self):
        self.assertFalse(database.lpo_is_over_value(
            lpo(total_usd=100, invoiced_usd=100.005)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
