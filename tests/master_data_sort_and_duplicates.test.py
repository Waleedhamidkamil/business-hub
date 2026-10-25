"""Master Data: ordering by any column, and duplicates shown as pairs (2026-08-26).

Two things the screen could not do.

SORTING. Four of the eight columns were plain text, and the sort direction was
baked into each key - a first click on Price gave highest-first and there was no
second click. The dropdown also offered "Longest idle", for which no sort key
has ever existed, so it silently fell back to Most used.

DUPLICATES. The "Possible duplicates" chip filtered the 66 flagged records into
the normal list in use-count order, so the two `Chest Freezer` rows sat five
apart and the reader had to hunt for the pair.

What must hold:

  1. Every column offered is a real key, and every key sorts both ways.
  2. Blank values stay at the BOTTOM in both directions. Reversing a column must
     reorder the records that have a value, not parade the empty ones past the
     reader.
  3. A duplicate group has at least two records, and no record is in two groups
     - the screen would otherwise show it twice and lie about the count.
  4. The PRIMARY is the most-used, then the oldest: the record documents already
     point at, and the one a merge should keep.
  5. The list comes back group by group, each copy stamped with which record it
     duplicates.

Reads the LIVE shared library, but only reads.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import master_items as mi  # noqa: E402

HEADER_KEYS = ["number", "name", "name_ar", "desc", "unit", "price", "cost", "picture"]


class Sorting(unittest.TestCase):

    def test_every_column_the_screen_offers_is_a_real_key(self):
        for k in HEADER_KEYS + ["used", "recent"]:
            with self.subTest(key=k):
                self.assertIn(k, mi._SORT_SPEC,
                              f"the screen offers {k!r} with nothing behind it")

    def test_an_unknown_key_falls_back_rather_than_crashing(self):
        self.assertEqual(mi._order_by("idle"), mi._order_by("used"),
                         "an unknown sort must degrade to the default")

    def test_direction_flips_and_only_the_direction(self):
        for k in HEADER_KEYS:
            with self.subTest(key=k):
                down = mi._order_by(k, True)
                up = mi._order_by(k, False)
                self.assertNotEqual(down, up, "the direction must change")
                self.assertEqual(down.replace(" DESC", " ASC"), up,
                                 "nothing but the direction may change")

    def test_blanks_stay_last_in_both_directions(self):
        # The "empty" term is ordered first and never flips, which is what pins
        # blank rows to the bottom whichever way the arrow points.
        for k in ("price", "cost", "number", "unit", "desc", "name_ar"):
            with self.subTest(key=k):
                for desc in (True, False):
                    o = mi._order_by(k, desc)
                    self.assertTrue(o.startswith("("),
                                    f"{k} must lead with its empties test: {o}")
                    self.assertNotIn("DESC,", o.split(",")[0],
                                     "the empties test must never be reversed")

    def test_price_ascending_really_is_ascending_and_keeps_blanks_out(self):
        down = mi.list_records("", "price", 20, 0, "")
        up = mi.list_records("", "price", 20, 0, "", desc=False)
        d = [r["price"] for r in down["rows"] if r["price"] is not None]
        u = [r["price"] for r in up["rows"] if r["price"] is not None]
        if len(d) > 1 and len(u) > 1:
            self.assertEqual(d, sorted(d, reverse=True), "default price is highest first")
            self.assertEqual(u, sorted(u), "reversed price is lowest first")
            self.assertGreater(d[0], u[0], "the two ends must be different records")
        self.assertTrue(all(r["price"] is not None for r in up["rows"]),
                        "a priceless record must not be dragged to the top by "
                        "reversing the column")

    def test_the_reply_says_which_way_it_sorted(self):
        r = mi.list_records("", "name", 5, 0, "")
        self.assertEqual(r["sort"], "name")
        self.assertFalse(r["desc"], "a name column starts A→Z")
        self.assertTrue(mi.list_records("", "price", 5, 0, "")["desc"],
                        "a money column starts highest-first")


class Duplicates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.groups = mi.duplicate_groups()

    def test_a_group_is_at_least_a_pair(self):
        for g in self.groups:
            with self.subTest(group=g["name"]):
                self.assertGreaterEqual(len(g["ids"]), 2,
                                        "one record on its own is not a duplicate")

    def test_no_record_appears_in_two_groups(self):
        seen = set()
        for g in self.groups:
            for i in g["ids"]:
                self.assertNotIn(i, seen,
                                 f"record {i} is in two groups - the screen "
                                 "would show it twice and miscount the list")
                seen.add(i)

    def test_the_groups_cover_exactly_the_flagged_records(self):
        flat = {i for g in self.groups for i in g["ids"]}
        self.assertEqual(flat, mi.duplicate_ids(),
                         "grouping must not add or lose a flagged record")

    def test_the_primary_is_the_most_used_then_the_oldest(self):
        conn = mi._connect()
        try:
            meta = {r["item_id"]: (r["use_count"] or 0)
                    for r in conn.execute("SELECT item_id, use_count FROM master_items")}
        finally:
            conn.close()
        for g in self.groups:
            with self.subTest(group=g["name"]):
                best = max(meta.get(i, 0) for i in g["ids"])
                self.assertEqual(meta.get(g["primary"], 0), best,
                                 "the record documents already point at is the "
                                 "one the others are duplicates OF")
                ties = [i for i in g["ids"] if meta.get(i, 0) == best]
                self.assertEqual(g["primary"], min(ties),
                                 "on a tie the OLDER record wins")

    def test_the_list_comes_back_grouped_and_stamped(self):
        r = mi.list_records("", "used", 400, 0, "duplicate")
        rows = r["rows"]
        if not rows:
            self.skipTest("no duplicates in this library")
        self.assertEqual(r["groups"], len(self.groups))

        # group numbers must run in blocks, never interleave
        order = [x.get("dupe_group") for x in rows if x.get("dupe_group")]
        blocks = [g for i, g in enumerate(order) if i == 0 or g != order[i - 1]]
        self.assertEqual(len(blocks), len(set(blocks)),
                         "a group must be contiguous - that is the whole point "
                         "of grouping them")

        for x in rows:
            with self.subTest(item=x.get("item_no")):
                self.assertIn(x.get("dupe_role"), ("primary", "copy"))
                if x["dupe_role"] == "copy":
                    self.assertTrue(str(x.get("dupe_of") or "").strip(),
                                    "a copy must name what it is a copy OF")
                else:
                    self.assertEqual(x.get("dupe_of"), "",
                                     "the original is a copy of nothing")

        firsts = [x for x in rows if x.get("dupe_role") == "primary"]
        self.assertEqual(len(firsts), r["groups"],
                         "exactly one original per group")

    def test_the_normal_list_is_untouched(self):
        r = mi.list_records("", "used", 5, 0, "")
        self.assertEqual(r.get("groups", 0), 0)
        for x in r["rows"]:
            self.assertNotIn("dupe_role", x,
                             "only the duplicates view is grouped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
