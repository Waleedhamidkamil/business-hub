"""Other spellings that FIND a record, and the category (2026-09-08).

Cleaning the catalogue was feeding it. A document stores an item's WORDS, not
its id, and the only way to find a record was its own `name_en` / `name_ar`. So
`merge_records`, which DELETES the losing rows, deleted the only route to their
words: merge "Wall Split Unit" into "Wall-Mounted Split AC" and those words stop
matching anything, so the next delivery note that uses them makes a fresh
record. That is how M-01848 came to sit beside M-01499, and how Round Table
ended up on file twice at $300 and $400.

An alias is a way IN and never a value. It cannot reach a document, it cannot
overwrite a name, and when one collides with a real name the resolver's standing
rule applies unchanged: a tie is never a match.

Runs against a TEMPORARY shared library - it must never touch the real one.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_client_data import activate as _isolate

_isolate()

from backend import master_data, master_items  # noqa: E402


def make(name_en="", name_ar="", **rest):
    """Put a record on disk exactly as it would BE on disk.

    Deliberately a direct insert rather than `create_record`, which refuses a
    second record under one name - the duplicates this feature exists for got
    there through document saves and imports, which have no such rule. The test
    has to be able to describe the mess, not only the tidy state.
    """
    cols = {"name_en": name_en, "name_ar": name_ar, "description_en": "",
            "description_ar": "", "unit_en": "", "unit_ar": "", "use_count": 0}
    cols.update(rest)
    conn = master_items._connect()
    try:
        master_items.init(conn)
        names = ", ".join(cols)
        marks = ", ".join("?" * len(cols))
        cur = conn.execute(
            f"INSERT INTO master_items({names}) VALUES({marks})", tuple(cols.values()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def category_of(item_id):
    return master_items.get_record(item_id)["item"]["category"]


def wipe():
    conn = master_items._connect()
    try:
        master_items.init(conn)
        conn.execute("DELETE FROM master_items")
        conn.execute("DELETE FROM master_aliases")
        conn.commit()
    finally:
        conn.close()


def found(name, lang="dn"):
    """What the resolver makes of a typed name."""
    return master_data.resolve_items([name], lang, "")["results"][0]


class Aliases(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_a_merge_keeps_the_losing_name_findable(self):
        keep = make("Wall-Mounted Split AC", "سبلت جداري", unit_en="EA")
        drop = make("Wall Split Unit", unit_en="EA")
        self.assertTrue(master_items.merge_records(keep, [drop])["ok"])

        hit = found("Wall Split Unit")
        self.assertEqual(hit["tier"], "exact",
                         "the words a merge folded away must still find the survivor")
        self.assertEqual(hit["item_id"], keep)
        # And it is a way IN, not a value: the document gets the SURVIVOR's name.
        self.assertEqual(hit["name"], "Wall-Mounted Split AC")

    def test_a_merged_record_does_not_come_back_as_a_new_item(self):
        """The whole point. Before this, the next document that used the losing
        words made a second record and the duplicate returned."""
        keep = make("Chinese Cabbage", "لهانة صيني", unit_en="KG")
        drop = make("Chinese Cabbage", "خس صيني", unit_en="KG")
        master_items.merge_records(keep, [drop])
        self.assertNotEqual(found("خس صيني", "po")["tier"], "new",
                            "the other Arabic word must not read as a new item")

    def test_a_chain_of_merges_does_not_lose_the_first_ones_spellings(self):
        a = make("Round Table", "طاولة دائرية")
        b = make("Circular Table")
        c = make("Dining Round Table")
        master_items.merge_records(a, [b])
        master_items.merge_records(a, [c])
        for spelling in ("Circular Table", "Dining Round Table", "طاولة دائرية"):
            self.assertEqual(found(spelling)["item_id"], a,
                             f"{spelling} must still reach the survivor")

    def test_an_alias_that_collides_with_a_real_name_is_a_TIE_not_a_match(self):
        keep = make("Bottled Water 1.5L", unit_en="EA")
        drop = make("Water Bottle", unit_en="EA")
        master_items.merge_records(keep, [drop])
        # Someone later creates a genuinely different product under the folded name.
        make("Water Bottle", "ماء بطل", unit_en="BOX", description_en="crate of 12")
        hit = found("Water Bottle")
        self.assertNotEqual(hit["tier"], "exact",
                            "two records answering to one name is an ambiguity")
        self.assertTrue(hit.get("ambiguous_variants"))
        self.assertFalse(hit.get("fill"), "a tie fills nothing")

    def test_a_record_is_not_its_own_alias(self):
        keep = make("Ice Box", "صندوق ثلج")
        master_items.remember_aliases(keep, ["Ice Box", "صندوق ثلج", "  ICE   BOX "])
        self.assertEqual(master_items.aliases_by_item().get(keep, []), [],
                         "its own names are already a way in; storing them again "
                         "would make the record look ambiguous against itself")

    def test_the_first_claim_on_a_spelling_wins(self):
        first = make("Safety Vest")
        second = make("Reflective Jacket")
        master_items.remember_aliases(first, ["يلك سلامة"])
        master_items.remember_aliases(second, ["يلك سلامة"])
        self.assertEqual(found("يلك سلامة", "po")["item_id"], first,
                         "a word that means two things is left to the tie rule, "
                         "never silently reassigned")

    def test_deleting_a_record_forgets_its_spellings(self):
        keep = make("Cooler Box")
        drop = make("Ice Chest")
        master_items.merge_records(keep, [drop])
        master_items.delete_records([keep])
        self.assertEqual(master_items.aliases_by_item(), {},
                         "an alias pointing at nothing would make the resolver "
                         "find a hole")
        self.assertEqual(found("Ice Chest")["tier"], "new")

    def test_an_alias_never_becomes_the_printed_name(self):
        keep = make("Long Green Bean", "لوبياء", unit_en="KG")
        master_items.remember_aliases(keep, ["فاصولياء خضراء"], "review")
        hit = found("فاصولياء خضراء", "po")
        self.assertEqual(hit["item_id"], keep)
        self.assertEqual(hit["name"], "لوبياء",
                         "a PO fills the Arabic NAME, never the spelling typed")


class Category(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_only_a_category_from_the_list_can_be_stored(self):
        iid = make("Safety Helmet")
        self.assertTrue(master_items.save_record(iid, {"category": "Safety & PPE"})["ok"])
        self.assertEqual(category_of(iid), "Safety & PPE")

        bad = master_items.save_record(iid, {"category": "safety stuff"})
        self.assertFalse(bad["ok"], "free text is how you end up with 400 categories")
        self.assertEqual(category_of(iid), "Safety & PPE",
                         "a refused category must not clear the good one")

    def test_the_list_is_matched_without_caring_about_case(self):
        iid = make("Diesel")
        self.assertTrue(master_items.save_record(iid, {"category": "fuel & lubricants"})["ok"])
        self.assertEqual(category_of(iid), "Fuel & Lubricants",
                         "stored in the list's own spelling, so filtering works")

    def test_an_explicit_blank_still_clears_it(self):
        iid = make("Mystery Item")
        master_items.save_record(iid, {"category": "Other"})
        self.assertTrue(master_items.save_record(iid, {"category": ""})["ok"])
        self.assertEqual(category_of(iid), "")

    def test_the_screen_can_count_and_filter_by_a_missing_category(self):
        make("Uncategorised One")
        iid = make("Categorised One")
        master_items.save_record(iid, {"category": "Tools & Hardware"})
        listing = master_items.list_records("", "used", 50)
        self.assertEqual(listing["missing_counts"]["category"], 1)
        only = master_items.list_records("", "used", 50, missing="category")
        self.assertEqual([r["name_en"] for r in only["rows"]], ["Uncategorised One"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
