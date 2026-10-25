"""Remembering the user's own wording for an item's picture (2026-08-14).

The image search is right about eight times in ten and cannot get the last two
by itself: "خيار" is both cucumber and stock option, "رقي" is both watermelon
and refinement, "Bleach" is a cartoon to the entire internet. The user resolves
it once and the wording is stored against the ITEM, so the correction applies
in every future document rather than to the row they happened to be looking at.

Stored on the item's own record (`master_items.img_query`) since 2026-08-20 -
it used to fan out over the four catalog tables, and the load-bearing property
then was that storing it must not FORK a catalog row. There is one row per item
now, so forking is impossible by construction; what still matters is that the
wording round-trips, stays with its own item, and an empty phrase clears it.

⚠ LIVE DATABASE. Sentinel names only, deleted by exact name in teardown.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import database, master_data, master_items  # noqa: E402

SENTINEL = "__IMGPHRASE_TEST__ Widget"
SENTINEL_2 = "__IMGPHRASE_TEST__ Second"


def _records(name):
    """The item record(s) for a name - reads master_items, not the catalogs."""
    conn = master_items._connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM master_items WHERE name_en = ? OR name_ar = ?",
            (name, name))]
    finally:
        conn.close()


def _cleanup():
    conn = master_items._connect()
    try:
        for n in (SENTINEL, SENTINEL_2):
            conn.execute("DELETE FROM master_items WHERE name_en = ? OR name_ar = ?",
                         (n, n))
        conn.commit()
    finally:
        conn.close()


def tearDownModule():
    _cleanup()


class RememberingAPhrase(unittest.TestCase):

    def setUp(self):
        _cleanup()
        database.remember_item(SENTINEL, "a test widget", "pcs", "__TESTCO__",
                               {"cost_native": 1.5, "cost_native_ccy": "USD"})
        self.assertEqual(len(_records(SENTINEL)), 1, "fixture did not land")

    def tearDown(self):
        _cleanup()

    def test_a_phrase_round_trips(self):
        r = master_data.remember_search_phrase(SENTINEL, "blue widget photo")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertGreaterEqual(r["written"], 1)
        self.assertEqual(master_data.item_search_phrase(SENTINEL),
                         "blue widget photo")

    def test_the_item_is_NOT_forked(self):
        """One record per item - a correction cannot mint a second."""
        master_data.remember_search_phrase(SENTINEL, "blue widget photo")
        self.assertEqual(len(_records(SENTINEL)), 1,
                         "a correction must not create a second record")

    def test_it_is_stored_on_the_record(self):
        master_data.remember_search_phrase(SENTINEL, "blue widget photo")
        rec = _records(SENTINEL)[0]
        self.assertEqual(rec["img_query"], "blue widget photo")
        self.assertEqual(rec["cost_native"], 1.5,
                         "writing the phrase must not drop what was already there")

    def test_an_empty_phrase_CLEARS_it(self):
        master_data.remember_search_phrase(SENTINEL, "blue widget photo")
        r = master_data.remember_search_phrase(SENTINEL, "")
        self.assertTrue(r["ok"])
        self.assertEqual(master_data.item_search_phrase(SENTINEL), "")
        self.assertFalse(_records(SENTINEL)[0]["img_query"],
                         "a cleared phrase should leave no trace")

    def test_an_unknown_item_is_refused_not_invented(self):
        r = master_data.remember_search_phrase("__NO_SUCH_ITEM_AT_ALL__", "x")
        self.assertFalse(r["ok"])

    def test_a_phrase_does_not_leak_to_a_different_item(self):
        database.remember_item(SENTINEL_2, "another", "pcs", "__TESTCO__", None)
        master_data.remember_search_phrase(SENTINEL, "blue widget photo")
        self.assertEqual(master_data.item_search_phrase(SENTINEL_2), "",
                         "each item keeps its own wording")

    def test_no_phrase_reads_as_empty(self):
        self.assertEqual(master_data.item_search_phrase(SENTINEL), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
