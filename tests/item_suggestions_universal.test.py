# -*- coding: utf-8 -*-
"""Item autocomplete: ONE pool, WORD matching (2026-08-16).

Reported: "I tried to quote a Traffic Cone which I have invoiced before but it
didn't show up in the quotation suggestions... I tried writing 'cone' only or
'safety cone', I saw no suggestion."

Both halves were real. Every dropdown read only its OWN catalog, so an item
that had been delivered and invoiced but never quoted could not be offered by a
quotation. And every dropdown matched by PREFIX, so "cone" never reached
"Traffic Cone". The per-type filters were narrower still: an item quoted as
equipment was invisible in a materials quotation.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY - there is no isolation. Every
name is a sentinel no real item could collide with, and tearDownModule removes
exactly those rows. Never add an unqualified DELETE.
"""
import sys, os, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database

CONE = "ZZ_TEST_Traffic Cone Sentinel"
ARABIC = "ZZ_TEST_مخروط مروري"
ROPE = "ZZ_TEST_Sentinel Rope"
CO_ITEM = "ZZ_TEST_Company Sentinel"
ALL = (CONE, ARABIC, ROPE, CO_ITEM)


def names(rows):
    return [r["name"] for r in rows]


class UniversalPool(unittest.TestCase):
    """An item known to ANY catalog can be offered by ANY editor."""

    @classmethod
    def setUpClass(cls):
        # Lives ONLY in the invoice catalog, exactly like the reported item.
        database.remember_inv_item("materials", CONE, {
            "description": "", "unit": "EA", "item_no": "M-99001",
            "rate": "3.5", "col_7": "invoice-only custom column",
        })

    def test_a_quotation_can_offer_an_item_it_has_only_ever_invoiced(self):
        got = names(database.suggest_quote_items("materials", CONE))
        self.assertIn(CONE, got,
                      "an invoiced item must be quotable; reading only "
                      "quote_items is what hid it")

    def test_every_editor_reaches_it(self):
        for label, rows in (
            ("PO", database.suggest_items(CONE)),
            ("quotation", database.suggest_quote_items("materials", CONE)),
            ("delivery note", database.suggest_dn_items(CONE)),
            ("invoice", database.suggest_inv_items("materials", CONE)),
        ):
            self.assertIn(CONE, names(rows), f"{label} could not find it")

    def test_a_different_sub_type_still_reaches_it(self):
        self.assertIn(CONE, names(database.suggest_quote_items("equipment", CONE)),
                      "quote_type = ? made an item quoted as equipment invisible "
                      "in a materials quotation, and vice versa")
        self.assertIn(CONE, names(database.suggest_inv_items("service", CONE)),
                      "inv_type = ? did the same to invoices")

    def test_another_catalogs_custom_column_does_not_travel(self):
        row = next(r for r in database.suggest_quote_items("materials", CONE)
                   if r["name"] == CONE)
        self.assertNotIn("col_7", row["data"],
                         "a custom column belongs to the document layout that "
                         "defined it; autofilling it elsewhere puts junk in")
        self.assertEqual(row["data"].get("unit"), "EA",
                         "…but what describes the ITEM must travel")
        self.assertEqual(row["data"].get("item_no"), "M-99001")

    def test_a_custom_column_is_no_longer_remembered_ANYWHERE(self):
        """CHANGED 2026-08-19, deliberately. A suggestion used to replay the
        whole stored payload back into its own catalog, custom columns
        included. An item now holds its own details and nothing else - a
        column somebody added to one document layout is not a property of the
        product, and the values found in those columns were a rate, a
        quantity, and in one case an entire base64 photograph.

        What DOES travel is the item: its description, unit, price, cost and
        picture, in the asking document's language."""
        row = next(r for r in database.suggest_inv_items("materials", CONE)
                   if r["name"] == CONE)
        self.assertNotIn("col_7", row["data"])
        self.assertEqual(row["data"].get("unit"), "EA",
                         "…but what describes the ITEM must still travel")
        self.assertEqual(row["data"].get("item_no"), "M-99001")


class WordMatching(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        database.remember_inv_item("materials", CONE, {"unit": "EA"})
        database.remember_item(ARABIC, "", "عدد", "TDC")
        database.remember_dn_item(ROPE, {"description": "12mm", "unit": "meter"})

    def test_a_word_from_the_middle_of_the_name_finds_it(self):
        self.assertIn(CONE, names(database.suggest_quote_items("materials", "Cone")),
                      "'cone' must reach 'Traffic Cone'; prefix matching is why "
                      "it did not")

    def test_a_query_whose_words_only_partly_match_still_finds_it(self):
        self.assertIn(CONE, names(database.suggest_quote_items("materials", "safety cone")),
                      "'safety cone' shares only one word with 'Traffic Cone' - "
                      "that is enough to offer it")

    def test_word_order_does_not_matter(self):
        self.assertIn(CONE, names(database.suggest_quote_items("materials", "Cone Traffic")))

    def test_several_truncated_words_still_find_it(self):
        """Typing fast: "traf con" for "Traffic Cone". Neither piece is a whole
        word and the phrase is not a substring of the name, so this only works
        if a token that merely OCCURS in the name still counts for something."""
        self.assertIn(CONE, names(database.suggest_quote_items("materials", "traf con")),
                      "two truncated words must still reach the item")
        self.assertIn(ROPE, names(database.suggest_dn_items("senti rop")),
                      "…and the same for a name the query never spells out")

    def test_arabic_matches_the_same_way(self):
        self.assertIn(ARABIC, names(database.suggest_items("مروري")),
                      "the word regex must be Unicode-aware, or Arabic falls "
                      "back to prefix-only matching")

    def test_an_unrelated_query_returns_nothing(self):
        self.assertNotIn(CONE, names(database.suggest_quote_items("materials", "refrigerator")))

    def test_an_empty_query_returns_nothing(self):
        for rows in (database.suggest_items(""),
                     database.suggest_quote_items("materials", "   "),
                     database.suggest_dn_items(""),
                     database.suggest_inv_items("materials", "")):
            self.assertEqual(rows, [], "an empty box must not list the catalog")


class RankingAndVariants(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Same name, genuinely different specs - these are NOT duplicates.
        database.remember_dn_item(ROPE, {"description": "12mm", "unit": "meter"})
        database.remember_dn_item(ROPE, {"description": "20mm", "unit": "meter"})
        # And the same item known to a second catalog: one suggestion, not two.
        database.remember_inv_item("materials", ROPE, {"description": "12mm",
                                                       "unit": "meter"})

    def test_real_variants_stay_separate(self):
        got = [r for r in database.suggest_dn_items(ROPE) if r["name"] == ROPE]
        descs = sorted(str(r["data"].get("description") or "") for r in got)
        self.assertEqual(descs, ["12mm", "20mm"],
                         "collapsing on the name alone hides real variants; "
                         "Hemp rope φ12/φ15/φ20 are three different products")

    def test_one_item_in_two_catalogs_is_offered_once(self):
        got = [r for r in database.suggest_dn_items(ROPE)
               if r["name"] == ROPE and r["data"].get("description") == "12mm"]
        self.assertEqual(len(got), 1,
                         "the same item found in the DN and invoice catalogs is "
                         "ONE item and must not appear twice")

    def test_ONE_item_has_ONE_price_whatever_document_asks(self):
        """CHANGED 2026-08-19, deliberately. The same item quoted under two
        types used to keep a separate rate for each, and the type being typed
        into supplied its own. Waleed's model is one item, one price - and on
        the live catalogue only 5 items out of 143 had ever disagreed across
        document types, all of them a quoted figure against a billed one.

        So both types are answered by the item's price. A price that really
        differs is a different offering and belongs to its own record."""
        database.remember_quote_item("equipment", ROPE,
                                     {"description": "12mm", "unit": "meter",
                                      "price_native": 99, "price_native_ccy": "USD"})
        a = next(r for r in database.suggest_quote_items("materials", ROPE)
                 if r["name"] == ROPE and r["description"] == "12mm")
        b = next(r for r in database.suggest_quote_items("equipment", ROPE)
                 if r["name"] == ROPE and r["description"] == "12mm")
        self.assertEqual(a["data"].get("price_native"),
                         b["data"].get("price_native"),
                         "one item cannot have two prices depending on who asks")

    def test_autofill_NEVER_offers_a_company(self):
        """CHANGED 2026-08-20, on Waleed's instruction: "auto fill should never
        fill company in po". The company is a fact about the last purchase
        order, not a property of the item, and repeating it onto a new PO is
        usually wrong. The record still remembers it; the suggestion just does
        not carry it, on a PO or anywhere else."""
        database.remember_item(CO_ITEM, "", "متر", "ZZ_TEST_CO")
        row = next(r for r in database.suggest_items(CO_ITEM)
                   if r["name"] == CO_ITEM)
        self.assertEqual(row["last_company"], "")
        row = next(r for r in database.suggest_dn_items(CO_ITEM)
                   if r["name"] == CO_ITEM)
        self.assertEqual(row["last_company"], "")

    def test_the_description_lands_in_the_column_that_document_HAS(self):
        """An equipment quotation calls it "Specifications", everything else
        calls it "description". The record holds one description; the fill has
        to put it where that document can print it, or the spec text that used
        to come back on an equipment line simply stops appearing."""
        database.remember_quote_item("materials", ROPE,
                                     {"description": "12mm", "unit": "meter"})
        mat = next(r for r in database.suggest_quote_items("materials", ROPE)
                   if r["name"] == ROPE and r["description"] == "12mm")
        eq = next(r for r in database.suggest_quote_items("equipment", ROPE)
                  if r["name"] == ROPE and r["description"] == "12mm")
        self.assertEqual(mat["data"].get("description"), "12mm")
        self.assertEqual(eq["data"].get("specs"), "12mm",
                         "an equipment quotation has no `description` column")
        self.assertNotIn("description", eq["data"])

    def test_a_service_line_is_not_overwritten_by_its_own_description(self):
        """A service quotation's FIRST column IS `description` - the item's
        name goes there. Emitting a description too would replace the name
        with it the moment the suggestion is picked."""
        database.remember_quote_item("materials", ROPE,
                                     {"description": "12mm", "unit": "meter"})
        row = next(r for r in database.suggest_quote_items("service", ROPE)
                   if r["name"] == ROPE)
        self.assertNotIn("description", row["data"])
        self.assertNotIn("specs", row["data"])

    def test_an_exact_match_outranks_a_partial_one(self):
        rows = names(database.suggest_dn_items(ROPE))
        self.assertEqual(rows[0], ROPE,
                         "the thing actually typed must come first")


def tearDownModule():
    with database._connect_shared() as conn:
        for n in ALL:
            for t in ("items", "dn_items", "inv_items", "quote_items"):
                try:
                    conn.execute(f"DELETE FROM {t} WHERE name = ?", (n,))
                except sqlite3.OperationalError:
                    pass
            # …and the item RECORD the save mirrored into
            # (backend/master_items.py), or the sentinel stays in Master Data.
            conn.execute("DELETE FROM master_items "
                         "WHERE name_en = ? OR name_ar = ?", (n, n))


if __name__ == "__main__":
    unittest.main(verbosity=2)
