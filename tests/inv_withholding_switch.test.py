# -*- coding: utf-8 -*-
"""The per-invoice "Withholding line" switch (2026-10-11).

Waleed wanted the "Withholding Tax (3.3 %) - Included" row off INV-EQ-JUNE-105
and nowhere else. It is a per-document choice carried in the invoice's layout
(`show_withholding`), set by a small checkbox in the editor footer.

Three things have to agree, and each is pinned here:
  1. the printed page drops the row (both skins' templates);
  2. the page plan stops RESERVING the row's 8mm on the last page - a row
     hidden in the template but still booked is over-booking (docs/12 SS4.6);
  3. the choice survives a save: `database.set_doc_layout` allow-lists its
     keys, and it used to keep only `autofit_rows`, silently dropping this.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import database, inv_generator  # noqa: E402


def _doc(show=None):
    layout = {"autofit_rows": False}
    if show is not None:
        layout["show_withholding"] = show
    return {"inv_no": "ZZ-INV-WH-1", "date": "11/10/2026", "inv_type": "equipment",
            "client_name": "TDC", "lpo_no": "N/A", "client_address": "CSSP Site",
            "payment_bank_key": "adib", "withholding_included": True,
            "sell_currency": "USD", "layout": layout,
            "items": [{"equipment": "Hydraulic Excavator", "description": "210",
                       "unit": "Per Month", "qty": "3", "rate": "10400"}]}


class WithholdingSwitch(unittest.TestCase):

    def test_default_prints_the_row(self):
        html = inv_generator.render_inv_html(_doc(), inline_css=True)
        self.assertIn("Withholding Tax", html)

    def test_switched_off_the_row_is_gone(self):
        html = inv_generator.render_inv_html(_doc(False), inline_css=True)
        self.assertNotIn("Withholding Tax", html)
        self.assertIn("31,200.00", html, "the total must still print")

    def test_switched_on_explicitly_prints_it(self):
        self.assertIn("Withholding Tax",
                      inv_generator.render_inv_html(_doc(True), inline_css=True))

    def test_the_page_plan_stops_reserving_the_row(self):
        cols = inv_generator._columns(inv_generator._schema_for_doc(_doc()))
        items = _doc()["items"]
        on = inv_generator.build_layout(items, cols, autofit=False, show_withholding=True)
        off = inv_generator.build_layout(items, cols, autofit=False, show_withholding=False)
        self.assertGreater(on[-1]["tax_row_mm"], 0)
        self.assertEqual(off[-1]["tax_row_mm"], 0.0,
                         "a hidden row must not keep its height booked on the last page")
        # And the ROOM it frees is really used: somewhere the line count tips
        # from two pages to one only because the 8mm row is gone. Auto-fit rows
        # (~7mm) so a freed 8mm always holds one more; 14mm fixed rows never would.
        row = items[0]
        tipped = [n for n in range(1, 80)
                  if len(inv_generator.build_layout([row] * n, cols, autofit=True,
                                                    show_withholding=False))
                  < len(inv_generator.build_layout([row] * n, cols, autofit=True,
                                                   show_withholding=True))]
        self.assertTrue(tipped, "hiding the row never frees a line's worth of room on the "
                                "last page - the plan is still reserving it")

    def test_the_choice_survives_a_save(self):
        no = "ZZ-INV-WH-SENTINEL"
        try:
            database.set_doc_layout("inv", no, {"autofit_rows": False, "show_withholding": False})
            self.assertEqual(database.get_doc_layout("inv", no),
                             {"autofit_rows": False, "show_withholding": False})
        finally:
            database.set_doc_layout("inv", no, {})
        self.assertIsNone(database.get_doc_layout("inv", no))


if __name__ == "__main__":
    unittest.main(verbosity=2)
