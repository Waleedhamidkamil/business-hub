"""A delivery note billed as ONE invoice line is revenue, not an item (2026-08-25).

The invoice can carry a whole DN as a single summary row - item = the DN number,
qty 1, rate = the DN's total - so the DN's pages ride along with the invoice.
That row is real money and belongs in charged / spent / profit. It is NOT
something the client buys, and Insights was ranking it as one: eight of TDC's
top twelve "items" were delivery-note numbers (DN-TDC-023 at $4,294 leading the
list), which pushed the actual items out of a twelve-row table and reported each
DN at 100% profit because those lines carry no cost.

What must hold:

  1. The three ways a document line is recognised - the `dn_ref` marker, a name
     that IS a delivery-note number, and a bare `DN` segment for the older
     numbering that no longer sits in the table.
  2. A real item name can NEVER be mistaken for one. An item name has words; a
     document number does not.
  3. The money does not move. Excluding them from the item table must not change
     charged / spent / profit by a cent - they are still invoiced revenue.
  4. Nothing disappears silently: what was set aside comes back as `dn_lines`
     so the UI can say so, including how many of them are uncosted.

Reads the LIVE database, but only reads.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import insights  # noqa: E402


KNOWN = {"DN-TDC-044", "DN-HDEC-047"}


class DocumentLineDetection(unittest.TestCase):

    def test_dn_ref_marker_is_enough(self):
        # The marker the picker writes since 2026-07-18. It is checked first
        # because it is the only one that cannot be wrong.
        self.assertTrue(insights._is_document_line(
            {"dn_ref": "DN-TDC-044"}, "Whatever the row is called", set()))
        self.assertFalse(insights._is_document_line(
            {"dn_ref": "   "}, "Scaffolding pipe", set()),
            "a blank marker is not a marker")

    def test_a_known_delivery_note_number(self):
        self.assertTrue(insights._is_document_line({}, "DN-TDC-044", KNOWN))
        self.assertTrue(insights._is_document_line({}, " dn-tdc-044 ", KNOWN),
                        "matching must survive case and stray spaces")

    def test_an_older_numbering_scheme_no_longer_in_the_table(self):
        # These exist on real invoices (INV-MT-TDC-006) and their DNs are gone
        # from `dns`, so rule 2 cannot see them.
        for name in ("TDC-DN-6", "DN-TDC-024", "dn-tdc-029"):
            with self.subTest(name=name):
                self.assertTrue(insights._is_document_line({}, name, set()))

    def test_a_real_item_is_never_a_document(self):
        for name in ("Drinking Water", "Scaffolding pipe", "Smoking Shelter",
                     "Xiaomi air purifier 4", "Hemp rope", "Angle iron",
                     "HIK-IPC-2CD1067G2-L(4MM)(6MP)", "", "   ",
                     "DN45", "GARDEN-LIGHT-12"):
            with self.subTest(name=name):
                self.assertFalse(
                    insights._is_document_line({}, name, KNOWN),
                    f"{name!r} is an item; only a bare DN segment counts")

    def test_a_multi_word_name_can_never_match(self):
        # The cheap guard that makes rule 3 safe: no document number has spaces.
        self.assertFalse(insights._is_document_line(
            {}, "DN-TDC-044 delivery charge", KNOWN))


class ClientInsightsSplit(unittest.TestCase):
    """End-to-end on the live data: the split changes the LIST, not the MONEY."""

    @classmethod
    def setUpClass(cls):
        cls.data = insights.client_insights("TDC", months=12)

    def test_no_top_item_is_a_delivery_note(self):
        names = [i["name"] for i in self.data["items"]]
        bad = [n for n in names
               if insights._is_document_line({}, n, set())]
        self.assertEqual(bad, [], f"delivery notes are still listed as items: {bad}")

    def test_the_excluded_lines_are_reported_not_dropped(self):
        dn = self.data["dn_lines"]
        self.assertIn("count", dn)
        self.assertIn("charged", dn)
        self.assertIn("uncosted", dn)
        if dn["count"]:
            self.assertGreater(dn["charged"], 0,
                               "a set-aside line with no money is not worth reporting")

    def test_the_money_still_includes_them(self):
        # The client's charged total must be at least what the set-aside lines
        # billed - if excluding them had removed the money this would fail.
        dn = self.data["dn_lines"]
        if dn["count"]:
            self.assertGreaterEqual(
                self.data["client"]["charged"], dn["charged"] - 0.01,
                "excluding a line from the ITEM table must never remove its "
                "revenue from the totals - it was really invoiced")

    def test_items_and_document_lines_do_not_double_count(self):
        listed = sum(i["charged"] for i in self.data["items"])
        dn = self.data["dn_lines"]["charged"]
        self.assertLessEqual(
            listed + dn, self.data["client"]["charged"] + 0.01,
            "top items are a TOP-12 subset, so together with the document "
            "lines they can never exceed what the client was charged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
