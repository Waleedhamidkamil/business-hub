# -*- coding: utf-8 -*-
"""A PO line names ONE variant, and the app must keep it that way (2026-10-03).

What happened
-------------
A-117 ordered دهن هيدروليك / 20 لتر. Master Data holds two hydraulic oils
under that Arabic name, 18 L (M-01572) and 20 L (M-02039), and the 20 L record
already carries the Arabic description "20 لتر". The PO save matched the line
to M-02039 correctly - `master_items._find` uses name + description + unit.
But Make Invoice asked `resolve_items` with the NAME ONLY, got two equal
candidates, and popped up "enter the item details" for an item the catalogue
knew exactly.

What this file pins
-------------------
1. `resolve_items(..., details=[{description, unit}])` narrows a name shared by
   several variants to the ONE whose description, in the line's own language,
   matches exactly and whose unit agrees - the same identity rule a document
   save uses. Anything less than one exact winner stays a choice.
2. A blank description narrows nothing. In an editor a blank cell means "not
   filled yet", not "this variant has no description".
3. A document save must not attach a line to an arbitrary variant. Two records
   that both "do not contradict" a described line (each known in the OTHER
   language only) are a tie, and the save used to teach the line's description
   to whichever was read first - after which (1) would pick that wrong variant
   automatically, with its price. 76 variant groups were exposed on 2026-10-03.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
by pattern.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_data, master_items as mi  # noqa: E402

AR = "ZZ_POVAR دهن"
EN = "ZZ_POVAR Oil"
TIE_AR = "ZZ_POVAR حبل"
TIE_EN = "ZZ_POVAR Rope"
LIKE = ("name_en LIKE 'ZZ\\_POVAR%' ESCAPE '\\' "
        "OR name_ar LIKE 'ZZ\\_POVAR%' ESCAPE '\\'")


def _wipe():
    with database._connect_shared() as conn:
        conn.execute(f"DELETE FROM master_items WHERE {LIKE}")
        conn.commit()


def _make(**fields):
    r = mi.create_record(fields)
    assert r.get("ok"), r
    return r["item"]["item_id"]


class ResolveTheLinesVariant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _wipe()
        common = {"name_en": EN, "name_ar": AR, "unit_en": "EA", "unit_ar": "عدد"}
        cls.small = _make(**common, description_en="18L", description_ar="18 لتر",
                          price_native=35, price_native_ccy="USD")
        cls.big = _make(**common, description_en="20 L", description_ar="20 لتر",
                        price_native=40, price_native_ccy="USD")
        # A third, undescribed variant: the one a blank description would
        # "match exactly" if blank were allowed to narrow.
        cls.plain = _make(**common)

    @classmethod
    def tearDownClass(cls):
        _wipe()

    def _one(self, details, doc_type="inv"):
        out = master_data.resolve_items([AR], doc_type, details=details)
        self.assertTrue(out.get("ok"), out)
        return out["results"][0]

    def test_without_details_the_two_variants_stay_a_choice(self):
        r = master_data.resolve_items([AR], "inv")["results"][0]
        self.assertTrue(r.get("ambiguous_variants"), r)
        self.assertEqual(r["fill"], {})

    def test_the_po_lines_description_picks_its_variant(self):
        """The A-117 case: the PO says 20 لتر, so the invoice gets the 20 L oil."""
        r = self._one([{"description": "20 لتر", "unit": "عدد"}])
        self.assertEqual(r["tier"], "exact", r)
        self.assertEqual(r["item_id"], self.big)
        self.assertEqual(r["name"], EN, "the invoice side must be English")
        self.assertEqual(r["fill"].get("description"), "20 L")
        self.assertEqual((r["fill"].get("rate"), r["fill"].get("rate_ccy")), (40.0, "USD"))
        self.assertFalse(r.get("ambiguous_variants"))

    def test_spacing_and_case_do_not_matter(self):
        r = self._one([{"description": "  20   لتر ", "unit": "عدد"}])
        self.assertEqual(r.get("item_id"), self.big, r)

    def test_an_english_description_works_from_an_english_document(self):
        out = master_data.resolve_items([EN], "inv", details=[{"description": "18l", "unit": "EA"}])
        self.assertEqual(out["results"][0].get("item_id"), self.small, out)

    def test_a_contradicting_unit_does_not_pick(self):
        r = self._one([{"description": "20 لتر", "unit": "متر"}])
        self.assertTrue(r.get("ambiguous_variants"), r)
        self.assertIsNone(r.get("item_id"))

    def test_a_description_no_variant_has_stays_a_choice(self):
        r = self._one([{"description": "25 لتر", "unit": "عدد"}])
        self.assertTrue(r.get("ambiguous_variants"), r)
        self.assertEqual(r["fill"], {})

    def test_a_blank_description_narrows_nothing(self):
        r = self._one([{"description": "", "unit": "عدد"}])
        self.assertTrue(r.get("ambiguous_variants"), r)
        self.assertIsNone(r.get("item_id"), "blank must not pick the undescribed variant")

    def test_details_follow_their_own_name(self):
        """One details entry per name, in order - the second name's description
        must not be applied to the first."""
        out = master_data.resolve_items(
            [AR, AR], "inv",
            details=[{"description": "18 لتر", "unit": "عدد"}, {"description": "20 لتر", "unit": "عدد"}])
        ids = [r.get("item_id") for r in out["results"]]
        self.assertEqual(ids, [self.small, self.big])

    def test_a_short_or_missing_details_list_is_harmless(self):
        out = master_data.resolve_items([AR, AR], "inv", details=[{"description": "20 لتر"}])
        self.assertEqual(out["results"][0].get("item_id"), self.big)
        self.assertTrue(out["results"][1].get("ambiguous_variants"))
        out = master_data.resolve_items([AR], "inv", details=[None])
        self.assertTrue(out["results"][0].get("ambiguous_variants"))

    def test_candidates_carry_their_description_slots(self):
        """The review writes the PO's description onto the variant the user
        picks, into a BLANK slot only - so it has to see both slots."""
        r = master_data.resolve_items([AR], "inv")["results"][0]
        by_id = {s["item_id"]: s for s in r["suggestions"]}
        self.assertEqual(by_id[self.big]["description_ar"], "20 لتر")
        self.assertEqual(by_id[self.big]["description_en"], "20 L")

    def test_the_bridge_passes_details_through(self):
        """Positional, the way pywebview calls it from po_handoff.js."""
        from backend.api import Api
        out = Api.__new__(Api).im_resolve(
            [AR], "inv", "", "USD", 0, False, "USD", 0,
            [{"description": "20 لتر", "unit": "عدد"}])
        self.assertEqual(out["results"][0].get("item_id"), self.big, out)


class ASaveNeverPicksAVariantAtRandom(unittest.TestCase):
    """Two rope records known only in English (12mm, 20mm). An Arabic PO line
    "حبل / 12 ملم" contradicts neither - the save cannot tell which it is."""

    @classmethod
    def setUpClass(cls):
        _wipe()
        common = {"name_en": TIE_EN, "name_ar": TIE_AR, "unit_en": "M", "unit_ar": "متر"}
        cls.a = _make(**common, description_en="12mm")
        cls.b = _make(**common, description_en="20mm")

    @classmethod
    def tearDownClass(cls):
        _wipe()

    def _row(self, iid):
        return mi.get_record(iid)["item"]

    def test_a_tied_line_teaches_no_variant(self):
        mi.remember(TIE_AR, "12 ملم", "متر", {}, "po")
        self.assertEqual(self._row(self.a)["description_ar"], "")
        self.assertEqual(self._row(self.b)["description_ar"], "")
        with database._connect_shared() as conn:
            n = conn.execute(f"SELECT COUNT(*) FROM master_items WHERE {LIKE}").fetchone()[0]
        self.assertEqual(n, 2, "a tie must not create a third record either")

    def test_an_exact_description_still_lands(self):
        mi.remember(TIE_EN, "20mm", "M", {}, "dn")
        self.assertEqual(self._row(self.b)["uses"], 1)
        self.assertEqual(self._row(self.a)["uses"], 0)

    def test_one_contradiction_leaves_one_fit(self):
        """When the other variant's own-language description CONTRADICTS the
        line, the remaining one is not a guess - it is the only fit."""
        with database._connect_shared() as conn:
            conn.execute("UPDATE master_items SET description_ar = '20 ملم' WHERE item_id = ?",
                         (self.b,))
            conn.commit()
        try:
            mi.remember(TIE_AR, "12 ملم", "متر", {}, "po")
            self.assertEqual(self._row(self.a)["description_ar"], "12 ملم")
        finally:
            with database._connect_shared() as conn:
                conn.execute("UPDATE master_items SET description_ar = '' WHERE item_id IN (?, ?)",
                             (self.a, self.b))
                conn.commit()

    def test_the_resolver_never_picks_by_elimination(self):
        """The save may file a line on the only variant left uncontradicted;
        the resolver may not FILL a document from it. Rope 20mm is described
        "20 ملم" in Arabic, Rope 12mm in English only - a line asking for
        "25 ملم" contradicts 20mm and not 12mm, and is still not 12mm."""
        with database._connect_shared() as conn:
            conn.execute("UPDATE master_items SET description_ar = '20 ملم' WHERE item_id = ?",
                         (self.b,))
            conn.commit()
        try:
            r = master_data.resolve_items([TIE_AR], "inv",
                                          details=[{"description": "25 ملم", "unit": "متر"}])
            self.assertTrue(r["results"][0].get("ambiguous_variants"), r)
            self.assertEqual(r["results"][0]["fill"], {})
        finally:
            with database._connect_shared() as conn:
                conn.execute("UPDATE master_items SET description_ar = '' WHERE item_id IN (?, ?)",
                             (self.a, self.b))
                conn.commit()

    def test_create_record_still_refuses_a_tied_duplicate(self):
        """Duplicate protection is a different question: either rope means the
        item is already on file, so a second record is still refused."""
        r = mi.create_record({"name_ar": TIE_AR, "description_ar": "12 ملم", "unit_ar": "متر"})
        self.assertFalse(r.get("ok"), r)
        self.assertIn(r["already"]["item_id"], (self.a, self.b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
