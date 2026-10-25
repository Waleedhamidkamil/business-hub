# -*- coding: utf-8 -*-
"""Writing the English side back into a master record (2026-09-05).

Why this file exists
--------------------
Make DN / Make Invoice fills a document from master data, and when master data
does not yet know an item's English name the user types it once - into the
review screen - and it is recorded, so the next PO never asks again. That
write-back goes through `master_items.save_record` / `create_record`, and those
take a PATCH whose semantics have three edges sharp enough to destroy data that
took months to collect:

1. **A money key present with an empty value NULLs the amount AND its
   currency.** `save_record` does `sets[money] = _num(patch[money])` and then
   sets the currency to None when the amount is None. So a review screen that
   helpfully sends every field it rendered would wipe the price of every item
   whose price box the user left alone.

2. **A blank language-free `unit` clears BOTH `unit_en` and `unit_ar`.** That is
   deliberate and documented ("this item has no unit"), which is exactly why
   this path must always send `unit_en` explicitly and never the bare key.

3. **`name` is not language-free.** Only `description` and `unit` are. A patch
   carrying `name` is filtered out by `_EDITABLE_FIELDS` and the call returns
   ok - a write that silently does nothing.

The rule that follows from all three: **build the patch by INCLUSION.** A field
the user did not fill is absent, never empty. These tests pin each edge, so the
next person to touch the review screen finds the reason written down rather
than rediscovering it on Waleed's catalogue.

LIVE SHARED LIBRARY. Sentinel names only, wiped by pattern in teardown.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_items as mi  # noqa: E402

AR = "ZZ_POWB مسمار"
EN = "ZZ_POWB Nail"
LIKE = ("name_en LIKE 'ZZ\\_POWB%' ESCAPE '\\' "
        "OR name_ar LIKE 'ZZ\\_POWB%' ESCAPE '\\'")


def _wipe():
    with database._connect_shared() as conn:
        conn.execute(f"DELETE FROM master_items WHERE {LIKE}")
        conn.commit()


class WriteBackIntoAMasterRecord(unittest.TestCase):
    def setUp(self):
        _wipe()

    @classmethod
    def tearDownClass(cls):
        _wipe()

    def _arabic_only(self, **extra):
        """The record shape this feature meets: known in Arabic, blank English."""
        patch = {"name_ar": AR, "unit_ar": "قطعة"}
        patch.update(extra)
        r = mi.create_record(patch)
        self.assertTrue(r.get("ok"), r)
        return r["item"]["item_id"]

    def _row(self, iid):
        conn = mi._connect()
        try:
            return conn.execute(
                "SELECT * FROM master_items WHERE item_id = ?", (iid,)).fetchone()
        finally:
            conn.close()

    # ------------------------------------------------ the intended write
    def test_an_english_name_fills_the_blank_slot_and_keeps_the_arabic(self):
        iid = self._arabic_only()
        r = mi.save_record(iid, {"name_en": EN, "unit_en": "PCS"})
        self.assertTrue(r.get("ok"), r)
        row = self._row(iid)
        self.assertEqual(row["name_en"], EN)
        self.assertEqual(row["name_ar"], AR, "the Arabic name must survive")
        self.assertEqual(row["unit_en"], "PCS")
        self.assertEqual(row["unit_ar"], "قطعة", "the Arabic unit must survive")

    def test_money_lands_with_its_currency(self):
        iid = self._arabic_only()
        r = mi.save_record(iid, {
            "name_en": EN,
            "cost_native": 2500, "cost_native_ccy": "IQD",
            "price_native": 3.5, "price_native_ccy": "USD",
        })
        self.assertTrue(r.get("ok"), r)
        row = self._row(iid)
        self.assertEqual(row["cost_native"], 2500)
        self.assertEqual(row["cost_native_ccy"], "IQD")
        self.assertEqual(row["price_native"], 3.5)
        self.assertEqual(row["price_native_ccy"], "USD")

    # ------------------------------------------------ edge 1: empty money
    def test_a_patch_that_omits_money_leaves_the_stored_money_alone(self):
        """The rule the review screen must follow."""
        iid = self._arabic_only(cost_native=2500, cost_native_ccy="IQD")
        r = mi.save_record(iid, {"name_en": EN})
        self.assertTrue(r.get("ok"), r)
        row = self._row(iid)
        self.assertEqual(row["cost_native"], 2500, "an omitted key is untouched")
        self.assertEqual(row["cost_native_ccy"], "IQD")

    def test_a_patch_with_an_EMPTY_money_value_wipes_the_amount_and_currency(self):
        """The footgun, pinned. This is why the patch is built by inclusion -
        sending every rendered field would blank a price the user never saw."""
        iid = self._arabic_only(cost_native=2500, cost_native_ccy="IQD")
        r = mi.save_record(iid, {"name_en": EN, "cost_native": "",
                                 "cost_native_ccy": "IQD"})
        self.assertTrue(r.get("ok"), r)
        row = self._row(iid)
        self.assertIsNone(row["cost_native"],
                          "an empty money value is destructive - never send one")
        self.assertIsNone(row["cost_native_ccy"],
                          "and it takes the currency with it")

    # ------------------------------------------------ edge 2: language-free unit
    def test_a_blank_language_free_unit_clears_BOTH_units(self):
        """Documented behaviour, pinned so nobody 'simplifies' the review
        screen into sending `unit` instead of `unit_en`."""
        iid = self._arabic_only()
        r = mi.save_record(iid, {"name_en": EN, "unit": ""})
        self.assertTrue(r.get("ok"), r)
        row = self._row(iid)
        self.assertEqual(row["unit_en"], "")
        self.assertEqual(row["unit_ar"], "",
                         "the Arabic unit is collateral - send unit_en instead")

    def test_unit_en_never_disturbs_unit_ar(self):
        iid = self._arabic_only()
        mi.save_record(iid, {"name_en": EN, "unit_en": "PCS"})
        self.assertEqual(self._row(iid)["unit_ar"], "قطعة")

    # ------------------------------------------------ edge 3: bare `name`
    def test_a_bare_name_key_is_silently_dropped(self):
        """`name` is not in _LANGUAGE_FREE and not in _EDITABLE_FIELDS, so this
        patch changes nothing while looking like it worked. An English name
        must always be sent as `name_en`."""
        iid = self._arabic_only()
        r = mi.save_record(iid, {"name": EN})
        self.assertFalse(
            r.get("ok"),
            "a patch with only a bare `name` has nothing to change - if this "
            "ever starts succeeding, check WHAT it wrote before trusting it")
        self.assertEqual(self._row(iid)["name_en"], "",
                         "the English slot must still be empty")

    # ------------------------------------------------ create + the `already` path
    def test_creating_a_duplicate_hands_back_the_record_to_write_to_instead(self):
        """`create_record` refuses a second record for one product and says
        which item it collided with. The review screen turns that refusal into
        a `save_record` on the existing item rather than reporting a failure."""
        iid = self._arabic_only()
        r = mi.create_record({"name_ar": AR, "name_en": EN})
        self.assertFalse(r.get("ok"), r)
        already = r.get("already") or {}
        self.assertEqual(already.get("item_id"), iid,
                         "the collision must name the record, with the id "
                         "save_record needs")
        # ...and that id really is writable.
        ok = mi.save_record(already["item_id"], {"name_en": EN})
        self.assertTrue(ok.get("ok"), ok)
        self.assertEqual(self._row(iid)["name_en"], EN)

    def test_a_brand_new_item_can_be_created_with_both_names(self):
        r = mi.create_record({
            "name_en": EN, "name_ar": AR, "unit_en": "PCS",
            "cost_native": 400, "cost_native_ccy": "IQD",
        })
        self.assertTrue(r.get("ok"), r)
        row = self._row(r["item"]["item_id"])
        self.assertEqual(row["name_en"], EN)
        self.assertEqual(row["name_ar"], AR)
        self.assertEqual(row["cost_native_ccy"], "IQD")
        self.assertTrue(str(row["item_no"] or "").startswith("M-"),
                        "a created record is numbered immediately")

    # ------------------------------------------------ the pairing hazard
    def test_writing_name_ar_over_a_full_slot_would_merge_two_products(self):
        """Not a bug in save_record - a rule for the CALLER. Recording the PO's
        Arabic name against a record that already has a different one silently
        re-labels somebody else's item, so the review screen writes `name_ar`
        only into a blank slot. This test states the consequence."""
        iid = self._arabic_only()                       # name_ar = مسمار
        mi.save_record(iid, {"name_en": EN})
        mi.save_record(iid, {"name_ar": "ZZ_POWB برغي"})
        self.assertEqual(self._row(iid)["name_ar"], "ZZ_POWB برغي",
                         "save_record obeys - which is exactly why the caller "
                         "must check the slot is empty first")

    # ------------------------------------------------ the forbidden path
    def test_the_retired_catalog_writer_is_not_used_by_this_feature(self):
        """`master_data.save_item` writes the four catalogs dropped 2026-08-20,
        is delete-then-upsert (blanking anything not re-stated) and cannot store
        a cost on a PO item. Stated as a test so it is enforced, not just
        commented."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        target = os.path.join(root, "static", "po_handoff.js")
        if not os.path.isfile(target):
            self.skipTest("po_handoff.js not written yet")
        src = open(target, encoding="utf-8").read()
        for forbidden in ("save_item", "im_save("):
            self.assertNotIn(forbidden, src,
                             f"{forbidden} writes the retired catalogs - use "
                             "mi_save / mi_create")


if __name__ == "__main__":
    unittest.main(verbosity=2)
