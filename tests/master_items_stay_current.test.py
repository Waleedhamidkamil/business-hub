# -*- coding: utf-8 -*-
"""Saving a document keeps the item record in step (2026-08-16).

Step 2 of the rebuild. `build()` is a full rebuild - far too slow to run on
every save - so `master_items.remember()` is the incremental half, called from
each of the four `remember_*_item` paths in database.py.

WRITES COME BEFORE READS, deliberately. Switching the app to read from
`master_items` first would mean an item saved on a fresh document did not
appear in the next document's suggestions until someone rebuilt - a regression
on exactly the workflow this rebuild exists to fix. So the record is kept
current FIRST, while the four catalogs still answer every question.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown, and the record is rebuilt afterwards so the app is left correct.
"""
import sys, os, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import api, config, database, master_items

EN = "ZZ_TEST_Sync Widget"
AR = "ZZ_TEST_قطعة مزامنة"
NEW = "ZZ_TEST_Brand New Thing"
NAMES = (EN, AR, NEW)


def _recs():
    conn = master_items._connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM master_items WHERE name_en LIKE 'ZZ\\_TEST%' ESCAPE '\\'"
            "   OR name_ar LIKE 'ZZ\\_TEST%' ESCAPE '\\'")]
    finally:
        conn.close()


def _one(**where):
    got = [r for r in _recs()
           if all(str(r.get(k) or "") == v for k, v in where.items())]
    assert len(got) == 1, f"expected exactly one record for {where}, got {len(got)}"
    return got[0]


#: The pattern `_recs()` counts by. `_wipe()` MUST use the same one.
#: It did not until 2026-09-05: `_recs()` matched every `ZZ_TEST%` name while
#: `_wipe()` deleted only the three in NAMES, so a sentinel left behind by any
#: OTHER test - including tests that no longer exist - was counted by this file
#: forever. Six such rows had accumulated in Waleed's live Master Data
#: (`ZZ_TEST_Round Onion`, `ZZ_TEST_Unpaired Widget`, two `ZZ_TEST_Sugar Box`,
#: `Delete Test Item`, `Zzql Cabbage`) and made these tests fail permanently,
#: which got the whole file written off as "known drift". It was not drift; it
#: was litter. Clean up by PATTERN, never by a hand-written list.
_WIPE_SQL = (r"DELETE FROM master_items "
             r"WHERE name_en LIKE 'ZZ\_TEST%' ESCAPE '\' "
             r"   OR name_ar LIKE 'ZZ\_TEST%' ESCAPE '\'")


def _wipe():
    with database._connect_shared() as conn:
        conn.execute(_WIPE_SQL)


class SavingKeepsItCurrent(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_a_brand_new_item_appears_immediately(self):
        """No rebuild, no restart. This is the whole reason writes come first."""
        self.assertEqual([r for r in _recs() if r["name_en"] == NEW], [])
        database.remember_inv_item("materials", NEW,
                                   {"description": "", "unit": "EA"})
        r = _one(name_en=NEW)
        self.assertEqual(r["unit_en"], "EA")
        self.assertEqual(r["use_count"], 1)

    def test_a_brand_new_QUOTE_item_learns_its_cost_and_price(self):
        """The editor saves `cost` and `rate`, while the item record stores
        native cost/price pairs. The save payload must bridge those two shapes
        on the FIRST save, not only after somebody repairs Master Data."""
        primary = config.QUOTE_TYPES["materials"]["primary_key"]
        payload = api._item_memory_payload(
            {primary: NEW, "description": "16mm x 10m", "unit": "EA",
             "rate": "150", "cost": "180000", "cost_ccy": "IQD"},
            primary, set(), "USD", 1500,
        )
        database.remember_quote_item("materials", NEW, payload)
        r = _one(name_en=NEW)
        self.assertEqual(r["price_native"], 150.0)
        self.assertEqual(r["price_native_ccy"], "USD")
        self.assertEqual(r["cost_native"], 180000.0)
        self.assertEqual(r["cost_native_ccy"], "IQD")

    def test_the_same_item_from_a_second_catalog_updates_ONE_record(self):
        """A quotation and an invoice naming the same thing must not create two
        records - that is the double-counting the rebuild is for."""
        database.remember_quote_item("materials", EN,
                                     {"description": "12mm", "unit": "EA",
                                      "price_native": 5.0,
                                      "price_native_ccy": "USD"})
        database.remember_inv_item("materials", EN,
                                   {"description": "12mm", "unit": "EA",
                                    "item_no": "M-99123"})
        r = _one(name_en=EN)
        self.assertEqual(r["price_native"], 5.0, "learned from the quotation")
        self.assertEqual(r["item_no"], "M-99123", "learned from the invoice")
        self.assertEqual(r["use_count"], 2, "both saves counted")

    def test_the_arabic_side_joins_the_SAME_record_by_its_paired_name(self):
        """A PO line in Arabic finds the record its English delivery note made,
        because the row carries the other-language name."""
        database.remember_dn_item(EN, {"description": "", "unit": "EA"})
        database.remember_item(AR, "", "عدد", "TDC", {"name_en": EN})
        recs = _recs()
        self.assertEqual(len(recs), 1,
                         f"one product, one record - got {[r['name_en'] for r in recs]}")
        self.assertEqual(recs[0]["name_en"], EN)
        self.assertEqual(recs[0]["name_ar"], AR)

    def test_units_that_disagree_stay_two_records(self):
        """عدد and KG are not the same pack, whatever the names say."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_item(AR, "", "عدد", "TDC", {"name_en": EN})
        self.assertEqual(len(_recs()), 2)

    def test_a_save_that_says_nothing_does_not_blank_what_is_known(self):
        """Same rule as _STICKY_MEMORY: the record LEARNS here, it never
        forgets. Re-saving a delivery note with the cost cell empty used to
        erase a cost the catalog already had."""
        database.remember_dn_item(EN, {"description": "", "unit": "EA",
                                       "cost_native": 900.0,
                                       "cost_native_ccy": "IQD"})
        database.remember_dn_item(EN, {"description": "", "unit": "EA"})
        r = _one(name_en=EN)
        self.assertEqual(r["cost_native"], 900.0)
        self.assertEqual(r["cost_native_ccy"], "IQD")

    def test_a_quotation_of_a_type_the_item_is_new_to_still_updates_it(self):
        """The record no longer notes WHICH catalog row fed it - that side
        table is gone. What has to hold is that a save from ANY document type
        reaches the one record rather than making a second."""
        database.remember_quote_item("equipment", EN, {"unit": "EA"})
        database.remember_inv_item("materials", EN, {"unit": "EA"})
        r = _one(name_en=EN)
        self.assertEqual(r["use_count"], 2, "both saves reached the same record")

    def test_a_broken_record_never_fails_the_document_save(self):
        """A save must not fail because the record could not be written.

        CHANGED 2026-08-20. This used to assert that the CATALOG row was
        written anyway - the catalogs were the system of record and the record
        was a mirror, so a failure there cost nothing. The catalogs are gone
        and the record is the only copy, so there is nothing to fall back to.
        What still holds, and is what this really guards, is that the document
        itself saves: an item that cannot be remembered must never stop a
        purchase order being issued.
        """
        import backend.master_items as real
        broken = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        original, real.remember = real.remember, broken
        try:
            database.remember_dn_item(NEW, {"description": "", "unit": "EA"})
        finally:
            real.remember = original
        # No exception reached the caller. Nothing was recorded, which is the
        # honest outcome - and the next save of the same item will teach it.
        self.assertEqual([r for r in _recs() if r["name_en"] == NEW], [])

def tearDownModule():
    _wipe()


if __name__ == "__main__":
    unittest.main(verbosity=2)
