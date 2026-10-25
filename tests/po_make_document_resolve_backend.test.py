# -*- coding: utf-8 -*-
"""Resolving a PO's Arabic item names into English, for Make DN / Make Invoice
(2026-09-05).

What this feature needs from the resolver
-----------------------------------------
A PO is written in Arabic; a delivery note and an invoice are written in
English. `resolve_items` already builds its candidate list in the ASKING
document's language, so calling it with a PO's Arabic names and
`doc_type="dn"` returns the English side of every paired item. That is the
whole feature: most items already carry their English name, unit, sell rate and
cost, so the document fills itself.

The trap this file exists to pin
--------------------------------
**A tier is not a readiness signal.** `_items_from_records` names each record
with `master_items.prefer(name_en, name_ar, lang)`, and `prefer` FALLS BACK to
the other language when the wanted slot is empty. So a record known only in
Arabic is listed under its Arabic name even when the caller asked for English,
matches its own name, and comes back `tier: "exact"` - carrying Arabic text.
`name_pairs()` cannot rescue it either: it indexes only rows where BOTH names
are filled.

Anything that decides "this row is ready for an English document" by looking at
the tier will therefore put Arabic on a client's invoice. The check has to be a
SCRIPT test on the value itself. These tests prove the trap is real so that the
frontend's script test cannot be mistaken for belt-and-braces and removed.

Also pinned here: the identity keys added for write-back. `fill` says what to
put in the document; `item_id` and the raw name/unit slots say what may be
written BACK, and they are deliberately absent on a `similar` result - nothing
should be written to a record the user has not chosen.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown by PATTERN (not by name - a test that wipes only the three names it
knows leaves every earlier sentinel on Waleed's Master Data screen forever).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_data, master_items as mi  # noqa: E402

# One sentinel prefix, one wipe pattern, both languages.
PFX = "ZZ_POMK"
AR_ONLY = "ZZ_POMK لوح شمسي"        # Arabic name, no English side at all
PAIR_AR = "ZZ_POMK مفتاح ربط"       # Arabic half of a fully paired item
PAIR_EN = "ZZ_POMK Spanner"         # English half of the same item
RATE_ONLY_AR = "ZZ_POMK تين"         # known bilingual item, no selling rate
RATE_ONLY_EN = "ZZ_POMK Fig"
LIKE = ("name_en LIKE 'ZZ\\_POMK%' ESCAPE '\\' "
        "OR name_ar LIKE 'ZZ\\_POMK%' ESCAPE '\\'")


def _wipe():
    with database._connect_shared() as conn:
        conn.execute(f"DELETE FROM master_items WHERE {LIKE}")
        conn.commit()


class ResolveAPoIntoEnglish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _wipe()
        # A fully paired item: this is the common case the feature rides on.
        r = mi.create_record({
            "name_en": PAIR_EN, "name_ar": PAIR_AR,
            "unit_en": "PCS", "unit_ar": "قطعة",
            "description_en": "Adjustable spanner",
            "price_native": 12.5, "price_native_ccy": "USD",
            "cost_native": 9000, "cost_native_ccy": "IQD",
        })
        assert r.get("ok"), r
        cls.paired_id = r["item"]["item_id"]

        # An item the catalogue knows ONLY in Arabic - the population this
        # feature has to catch, and the one that wears a misleading tier.
        r = mi.create_record({"name_ar": AR_ONLY, "unit_ar": "قطعة"})
        assert r.get("ok"), r
        cls.ar_only_id = r["item"]["item_id"]

        # The A-88 shape: the item and both language slots are known, but an
        # invoice cannot be created until a selling rate is supplied.
        r = mi.create_record({
            "name_en": RATE_ONLY_EN, "name_ar": RATE_ONLY_AR,
            "unit_en": "KG", "unit_ar": "كيلو",
        })
        assert r.get("ok"), r
        cls.rate_only_id = r["item"]["item_id"]

    @classmethod
    def tearDownClass(cls):
        _wipe()

    def _one(self, name, doc_type="dn"):
        out = master_data.resolve_items([name], doc_type)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(out["results"]), 1, out)
        return out["results"][0]

    # ------------------------------------------------ the happy path
    def test_a_paired_item_resolves_from_arabic_to_english(self):
        """The PO says مفتاح ربط; the delivery note must say Spanner."""
        r = self._one(PAIR_AR)
        self.assertEqual(r["tier"], "exact", r)
        self.assertEqual(r["name"], PAIR_EN,
                         "the DN/INV side of a paired item must come back in English")
        self.assertEqual(r["fill"].get("unit"), "PCS",
                         "the ENGLISH unit, not the Arabic one the PO used")

    def test_the_english_name_is_on_result_name_not_on_fill(self):
        """`_fill_from` never sets `item` - every caller reads `result.name`.
        Worth pinning: a caller that reads `fill.item` gets None and silently
        writes an empty item onto the document."""
        r = self._one(PAIR_AR)
        self.assertIsNone(r["fill"].get("item"))
        self.assertTrue(r["name"])

    def test_money_arrives_with_its_currency_on_both_legs(self):
        """A number without its unit is the ~1400x error. Neither leg may
        arrive bare."""
        f = self._one(PAIR_AR)["fill"]
        self.assertIn("rate", f)
        self.assertIn("rate_ccy", f)
        self.assertIn("cost", f)
        self.assertIn("cost_ccy", f)
        # The cost is handed over in the currency it was SAVED in, never
        # converted (2026-08-20). 9000 dinars must not arrive as 9000 dollars.
        self.assertEqual(f["cost"], 9000)
        self.assertEqual(f["cost_ccy"], "IQD")

    def test_a_known_item_without_price_is_rate_only_not_new(self):
        """A-88's Fig/Red Plum/Low Fat Milk condition.

        The identity and English wording are complete. Only invoice pricing is
        absent, so the frontend must never present an empty English-name field
        or call this a new item.
        """
        r = self._one(RATE_ONLY_AR, doc_type="inv")
        self.assertEqual(r.get("item_id"), self.rate_only_id)
        self.assertEqual(r.get("name"), RATE_ONLY_EN)
        self.assertEqual(r.get("name_en"), RATE_ONLY_EN)
        self.assertEqual(r.get("fill", {}).get("unit"), "KG")
        self.assertNotIn("rate", r.get("fill", {}))

    # ------------------------------------- THE TRAP: tier lies, script tells
    def test_an_arabic_only_record_still_matches_and_carries_arabic(self):
        """The whole reason the review screen exists.

        This record has no English name, so `prefer` fell back and listed it
        under its Arabic one. Asking as a DN matches it anyway - and hands back
        Arabic text under a confident tier. A caller that trusts the tier puts
        this straight onto a client's invoice.
        """
        r = self._one(AR_ONLY)
        self.assertIn(r["tier"], ("exact", "contained", "similar"),
                      "the record must still be FOUND - it is the right item")
        name = r.get("name") or ""
        self.assertTrue(
            master_data._ARABIC_NAME.search(name),
            "an Arabic-only record must come back with its Arabic name - if "
            "this ever stops being true the frontend's script test can be "
            "relaxed, but not before")
        self.assertNotEqual(
            r["tier"], "new",
            "and it is NOT tier 'new', which is what makes the tier useless "
            "as a readiness signal")

    def test_the_arabic_unit_falls_back_the_same_way(self):
        """`unit` is `prefer(unit_en, unit_ar, lang)` too, so the same fallback
        puts قطعة on an English document. Readiness is per FIELD, not per row."""
        r = self._one(AR_ONLY)
        unit = (r.get("fill") or {}).get("unit") or ""
        if unit:
            self.assertTrue(master_data._ARABIC_NAME.search(unit),
                            "an Arabic-only record's unit falls back to Arabic")

    # ------------------------------------------------ identity for write-back
    def test_a_matched_result_carries_the_item_id_write_back_needs(self):
        """`save_record` takes the item_id primary key and nothing else. The
        payload used to carry only `id`, which is the ITEM NUMBER ('M-00123')
        - write-back was impossible without this."""
        r = self._one(PAIR_AR)
        self.assertEqual(r.get("item_id"), self.paired_id)
        self.assertTrue(str(r.get("item_no") or "").startswith("M-"))

    def test_a_matched_result_carries_the_raw_name_and_unit_slots(self):
        """The review screen must tell "this record's Arabic slot is empty, so
        recording the PO's name fills a blank" from "it already pairs with
        something else, so writing would merge two products"."""
        r = self._one(PAIR_AR)
        self.assertEqual(r.get("name_en"), PAIR_EN)
        self.assertEqual(r.get("name_ar"), PAIR_AR)
        self.assertEqual(r.get("unit_en"), "PCS")
        self.assertEqual(r.get("unit_ar"), "قطعة")

    def test_an_arabic_only_record_reports_an_empty_english_slot(self):
        """Which is what makes 'writing name_en here is safe' decidable."""
        r = self._one(AR_ONLY)
        if r.get("item_id") is not None:            # exact/contained branch
            self.assertEqual(r.get("name_en"), "")
        else:                                        # similar - check candidates
            cands = [c for c in (r.get("suggestions") or [])
                     if c.get("name_ar") == AR_ONLY]
            self.assertTrue(cands, "the record must be among the candidates")
            self.assertEqual(cands[0].get("name_en"), "")

    def test_every_suggestion_carries_its_own_identity(self):
        """A `similar` row is resolved by PICKING one, so the identity has to
        be on each candidate."""
        out = master_data.resolve_items(["ZZ_POMK Spann"], "dn")
        r = out["results"][0]
        for c in (r.get("suggestions") or []):
            self.assertIn("item_id", c,
                          "a candidate the user can accept must say which "
                          "record accepting it would write to")

    def test_a_similar_result_has_no_top_level_item_id(self):
        """'A tie is never a match' extends to write-back: nothing may be
        written to a record the user has not chosen. The id lives on the
        candidates, never on the guess."""
        out = master_data.resolve_items(["ZZ_POMK Spann"], "dn")
        r = out["results"][0]
        if r["tier"] == "similar":
            self.assertIsNone(r.get("item_id"),
                              "a similar result must not offer a write-back target")

    # ------------------------------------------------ the PO direction still works
    def test_asking_as_a_po_still_fills_arabic(self):
        """The existing paste path must be untouched by all of this."""
        r = self._one(PAIR_EN, doc_type="po")
        self.assertEqual(r["name"], PAIR_AR,
                         "a PO asking about an English name fills the Arabic side")
        self.assertEqual(r["fill"].get("unit"), "قطعة")


if __name__ == "__main__":
    unittest.main(verbosity=2)
