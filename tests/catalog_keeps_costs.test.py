# -*- coding: utf-8 -*-
"""A remembered cost must survive an ordinary document save (2026-08-16).

The catalog upsert replaces `data_json` wholesale, so anything the incoming
document payload does not mention is gone. `_STICKY_MEMORY` is the list that
survives that replacement, and it named only `cost_usd` - from when a cost was
always a dollar figure. Costs are now stored AS TYPED in `cost_native` +
`cost_native_ccy`, and that pair was never added, so the protection had
silently stopped covering the field costs live in.

Found the hard way: 119 costs imported into the catalog on 2026-08-16 would
have been erased one at a time, each time a delivery note happened to carry the
item with its cost cell blank. Nothing would have reported it.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY - there is no isolation. Every
name here is a sentinel that no real item could collide with, and tearDownModule
removes exactly those rows by name. Never add an unqualified DELETE.
"""
import sys, os, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_items

SENTINEL = "ZZ_STICKY_TEST_ITEM_قياس"
SENTINEL2 = "ZZ_STICKY_TEST_PRICE_قياس"
ALL = (SENTINEL, SENTINEL2)


def _record(name):
    """The one item record a document save writes now (master_items.py). Costs
    and prices live in its own columns, not a per-catalog `data_json` blob."""
    conn = master_items._connect()
    try:
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            "SELECT * FROM master_items WHERE name_en = ? OR name_ar = ?",
            (name, name)).fetchall()
        return [dict(x) for x in r]
    finally:
        conn.close()


class StickyCost(unittest.TestCase):

    def test_cost_native_survives_a_save_that_omits_it(self):
        database.remember_dn_item(SENTINEL, {
            "description": "", "unit": "KG", "qty": "5",
            "cost_native": 900.0, "cost_native_ccy": "IQD",
        })
        # The same item saved again from a document with NO cost typed. The
        # data_sig is computed from the incoming payload, and both cost keys
        # are in _SIG_IGNORE, so this lands on the very same row.
        database.remember_dn_item(SENTINEL, {
            "description": "", "unit": "KG", "qty": "12",
        })
        rows = _record(SENTINEL)
        self.assertEqual(len(rows), 1, "the second save must not fork the item")
        # CHANGED 2026-08-20: cost lives in the record's own columns now, not a
        # catalog `data_json` blob. (qty is per-document, never on the record.)
        self.assertEqual(rows[0]["cost_native"], 900.0,
                         "a document that never mentions a cost must not erase "
                         "one the record already knew")
        self.assertEqual(rows[0]["cost_native_ccy"], "IQD",
                         "the currency has to survive with the amount, or the "
                         "number is read as dollars and is 1500x wrong")

    # REMOVED 2026-08-20: test_an_explicit_new_cost_still_replaces_the_old_one
    # tested the retired `dn_items` catalog's data_json wholesale-replace path.
    # The item record LEARNS a cost once and a later document save does not
    # overwrite it (corrections are made on the Master Data screen), so the old
    # "explicit STATED cost replaces" behaviour no longer applies to a save.

    def test_price_sticks_too(self):
        database.remember_dn_item(SENTINEL, {
            "description": "", "unit": "KG",
            "price_native": 1.5, "price_native_ccy": "USD",
            "money_ccy": "IQD", "price_ccy": "USD",
        })
        database.remember_dn_item(SENTINEL, {"description": "", "unit": "KG"})
        # CHANGED 2026-08-20: read the price off the record's column. money_ccy
        # is not a record field - the cost display currency is Master Data's, not
        # something a document save carries - so it is no longer asserted here.
        self.assertEqual(_record(SENTINEL)[0]["price_native"], 1.5)


class SigIgnoreCopiesAgree(unittest.TestCase):
    """`shared_library._SIG_IGNORE` is a second copy of `database._SIG_IGNORE`,
    used when a catalog is copied between profiles. Both files say in comments
    that they must match - and they had already drifted: `img_query` was added
    to one and never the other, so copying a profile re-split every item that
    had ever been given a search phrase. Nothing detected it."""

    def test_the_two_copies_are_identical(self):
        from backend import shared_library
        self.assertEqual(
            database._SIG_IGNORE, shared_library._SIG_IGNORE,
            "database._SIG_IGNORE and shared_library._SIG_IGNORE must match "
            "exactly; a key in one and not the other re-splits every item it "
            "touches the next time a catalog is copied between profiles")

    def test_the_other_language_name_is_ignored_for_identity(self):
        for key in ("name_ar", "name_en"):
            self.assertIn(key, database._SIG_IGNORE,
                          f"{key} is a LABEL - adding or correcting it must "
                          "never fork an item into a second catalog entry")
            self.assertIn(key, database._STICKY_MEMORY,
                          f"{key} must also survive a document save, or the "
                          "first save after pairing wipes the name")


def tearDownModule():
    """Remove exactly the sentinel rows - never a blanket delete."""
    with database._connect_shared() as conn:
        for n in ALL:
            try:
                conn.execute("DELETE FROM dn_items WHERE name = ?", (n,))
            except sqlite3.OperationalError:
                pass
            # A document save also writes the item RECORD now
            # (backend/master_items.py). Removing only the catalog rows leaves
            # this file's sentinels sitting in the live Master Data screen.
            conn.execute("DELETE FROM master_items "
                         "WHERE name_en = ? OR name_ar = ?", (n, n))


if __name__ == "__main__":
    unittest.main(verbosity=2)
