"""The catalogue review's findings (2026-09-08, backend/master_doctor.py).

The Master Data chips report what is MISSING. These are the problems that are
not a blank field, so no chip can ever show them, plus the two things the chips
could never do: fix in bulk, and remember a ruling.

Measured on the live catalogue the day this was written, and every number below
is one of those: `Round Table` on file twice at $300 and $400, `Common laborer`
priced $24 per MONTH and $24 per DAY, 24 descriptions carrying a date, 130
fields holding a newline.

Runs against a TEMPORARY shared library - it must never touch the real one.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_client_data import activate as _isolate

_isolate()

from backend import master_data, master_doctor, master_items  # noqa: E402


def make(**cols):
    base = {"name_en": "", "name_ar": "", "description_en": "", "description_ar": "",
            "unit_en": "", "unit_ar": "", "use_count": 0, "created_at": "2026-01-01"}
    base.update(cols)
    conn = master_items._connect()
    try:
        master_items.init(conn)
        names = ", ".join(base)
        marks = ", ".join("?" * len(base))
        cur = conn.execute(f"INSERT INTO master_items({names}) VALUES({marks})",
                           tuple(base.values()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def wipe():
    conn = master_items._connect()
    try:
        master_items.init(conn)
        for table in ("master_items", "master_aliases", "master_review"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def only(kind):
    return master_doctor.scan([kind])["findings"]


class Duplicates(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_the_same_product_twice_is_found_with_its_price_disagreement(self):
        """Round Table, the reason this exists: two records a week apart, $300
        and $400, and the catalogue could hand an invoice either one."""
        make(name_en="Round Table", description_en="H=80cm", unit_en="EA",
             price_native=300.0, use_count=3)
        make(name_en="Round Table", description_en="H=80cm", unit_en="EA",
             price_native=400.0, use_count=1)
        found = only("duplicate")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["evidence"]["price"], "far")
        self.assertEqual(found[0]["proposal"]["action"], "merge")

    def test_a_price_disagreement_is_ranked_above_a_tidy_duplicate(self):
        make(name_en="Chinese Cabbage", unit_en="KG", price_native=1.5, use_count=126)
        make(name_en="Chinese Cabbage", unit_en="KG", price_native=1.5, use_count=10)
        make(name_en="Round Table", unit_en="EA", price_native=300.0, use_count=3)
        make(name_en="Round Table", unit_en="EA", price_native=400.0, use_count=1)
        self.assertEqual(only("duplicate")[0]["title"], "Round Table",
                         "the one that can put a wrong price on an invoice leads")

    def test_one_product_under_two_ENGLISH_names_is_still_found(self):
        """The 2026-09-07 pair. Both are `سبلت جداري`; their English names differ,
        so an English-first key cannot see them."""
        make(name_en="Wall Split Unit", name_ar="سبلت جداري", unit_en="EA")
        make(name_en="Wall-Mounted Split AC", name_ar="سبلت جداري", unit_en="EA")
        self.assertEqual(len(only("duplicate")), 1)

    def test_a_record_matching_under_both_its_names_is_reported_ONCE(self):
        make(name_en="Ice Box", name_ar="صندوق ثلج", unit_en="EA")
        make(name_en="Ice Box", name_ar="صندوق ثلج", unit_en="EA")
        found = only("duplicate")
        self.assertEqual(len(found), 1, "one problem, reported once")

    def test_the_survivor_is_the_one_the_documents_already_point_at(self):
        low = make(name_en="Beef Ribs", unit_en="KG", price_native=14.0, use_count=2)
        high = make(name_en="Beef Ribs", unit_en="KG", price_native=13.65, use_count=36)
        proposal = only("duplicate")[0]["proposal"]
        self.assertEqual(proposal["keep"], high)
        self.assertEqual(proposal["drop"], [low])

    def test_applying_the_merge_keeps_the_losing_name_findable(self):
        keep = make(name_en="Wall-Mounted Split AC", name_ar="سبلت جداري",
                    unit_en="EA", use_count=5)
        make(name_en="Wall Split Unit", name_ar="سبلت جداري", unit_en="EA")
        finding = only("duplicate")[0]
        self.assertTrue(master_doctor.apply(finding["kind"], finding["key"],
                                            finding["proposal"])["ok"])
        hit = master_data.resolve_items(["Wall Split Unit"], "dn", "")["results"][0]
        self.assertEqual(hit["item_id"], keep,
                         "cleaning the catalogue must not feed it")

    def test_a_finding_is_gone_once_it_is_applied(self):
        make(name_en="Chinese Cabbage", unit_en="KG", use_count=126)
        make(name_en="Chinese Cabbage", unit_en="KG", use_count=10)
        finding = only("duplicate")[0]
        master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        self.assertEqual(only("duplicate"), [])


class VariantsAreNotDuplicates(unittest.TestCase):
    """Waleed's ruling, 2026-09-08: a unit disagreement is a VARIANT. FLOUR
    really is sold by the bag, the box and the kilo."""

    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_the_same_name_at_different_units_is_never_offered_as_a_merge(self):
        make(name_en="FLOUR", unit_en="BAG", price_native=100.0)
        make(name_en="FLOUR", unit_en="BOX", price_native=13.0)
        make(name_en="FLOUR", unit_en="KG", price_native=100.0)
        self.assertEqual(only("duplicate"), [],
                         "three real products must never be proposed for merging")
        found = only("variant_needs_description")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["proposal"]["action"], "describe")
        self.assertEqual(len(found[0]["proposal"]["item_ids"]), 3)

    def test_variants_that_ALREADY_say_what_they_are_are_left_alone(self):
        make(name_en="Welding Rod", description_en="6013, 3.2mm", unit_en="BOX")
        make(name_en="Welding Rod", description_en="7016, 4.0mm", unit_en="EA")
        self.assertEqual(only("variant_needs_description"), [],
                         "nothing to fix: they can already be told apart")

    def test_a_unit_that_means_the_same_thing_is_not_a_difference(self):
        make(name_en="Onion", name_ar="بصل", unit_en="KG")
        make(name_en="Onion", name_ar="بصل", unit_ar="كيلو")
        self.assertEqual(only("variant_needs_description"), [],
                         "كيلو IS KG - that is a duplicate, not a variant")
        self.assertEqual(len(only("duplicate")), 1)


class Money(unittest.TestCase):
    """What may be said about a price, and what may not."""

    def test_a_relation_is_a_word_never_an_amount(self):
        self.assertEqual(master_doctor.price_relation([300, 400]), "far")
        self.assertEqual(master_doctor.price_relation([13.65, 14.0]), "close")
        self.assertEqual(master_doctor.price_relation([1.5, 1.5]), "same")
        self.assertEqual(master_doctor.price_relation([8.0]), "unknown")
        self.assertEqual(master_doctor.price_relation([None, 0]), "unknown")

    def test_the_relation_carries_no_number_at_all(self):
        for value in (master_doctor.price_relation([300, 400]),
                      master_doctor.price_relation([13.65, 14.0])):
            self.assertFalse(any(ch.isdigit() for ch in value),
                             "this word is the ONLY thing about money that may "
                             "ever leave the machine")


class Junk(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_a_date_typed_into_a_description_is_offered_for_removal(self):
        iid = make(name_en="Common Laborer", unit_en="Per Day",
                   description_en="Skilled Date:18/07/2026")
        finding = only("junk_date")[0]
        self.assertTrue(master_doctor.apply(finding["kind"], finding["key"],
                                            finding["proposal"])["ok"])
        self.assertEqual(master_items.get_record(iid)["item"]["description_en"],
                         "Skilled", "the date goes, what it described stays")

    def test_a_description_that_is_only_a_date_becomes_blank(self):
        iid = make(name_en="Common Laborer", description_en="Date:30/07/2026")
        finding = only("junk_date")[0]
        master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        self.assertEqual(master_items.get_record(iid)["item"]["description_en"], "")

    def test_a_newline_inside_a_field_is_collapsed(self):
        iid = make(name_en="Ice Box / Cooler Box \n48 Liter", unit_en="EA")
        finding = only("junk_newline")[0]
        master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        self.assertEqual(master_items.get_record(iid)["item"]["name_en"],
                         "Ice Box / Cooler Box 48 Liter")

    def test_an_ordinary_description_is_not_touched(self):
        make(name_en="Cable", description_en="3x2.5 20mm M20", unit_en="M")
        self.assertEqual(only("junk_date"), [])
        self.assertEqual(only("junk_newline"), [])


class WhatTheSCREEN_Caught(unittest.TestCase):
    """Two detectors that looked right in a unit test and were wrong the moment
    real records were drawn on screen (2026-09-10)."""

    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_a_deliberate_multiline_DESCRIPTION_is_not_junk(self):
        """The 25-Seater Bus record holds a four-line route with departure
        times. Flattening it would destroy the only place those times are
        written down. The first version flagged 134 fields; almost all were
        descriptions doing their job."""
        route = ("Route: FCC Site - CSSP Site" + chr(10)
                 + "Departure 04:30 AM" + chr(10) + "Two trips per day")
        make(name_en="25-Seater Bus", unit_en="MONTH", description_en=route)
        self.assertEqual(only("junk_newline"), [],
                         "a description may legitimately have lines")

    def test_a_multiline_NAME_is_still_junk(self):
        make(name_en="Ice Box / Cooler Box " + chr(10) + "48 Liter", unit_en="EA")
        found = only("junk_newline")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["proposal"]["patch"]["name_en"],
                         "Ice Box / Cooler Box 48 Liter")

    def test_a_truck_hired_by_the_month_is_NOT_manpower(self):
        """The screen proposed `Manpower` for a 4WD Pickup Truck, a Boom Truck
        and a Cargo Crane. The unit says HIRED; only the name says whether the
        thing hired is a person."""
        make(name_en="4WD Pickup Truck", unit_en="MONTH", price_native=1800.0)
        make(name_en="Boom Truck (10Ton)", unit_en="Per Day", price_native=300.0)
        make(name_en="CARGO CRANE", unit_en="Per Hour", price_native=45.0)
        self.assertEqual(only("category"), [],
                         "a wrong category is worse than a blank one - nobody "
                         "re-checks a field that is already filled")

    def test_a_person_hired_by_the_month_still_is(self):
        for name in ("Common laborer", "Crane Operator", "Bus Driver", "عامل بناء"):
            wipe()
            make(name_en="" if name.startswith("ع") else name,
                 name_ar=name if name.startswith("ع") else "",
                 unit_en="Per Day", price_native=24.0)
            found = only("category")
            self.assertEqual(len(found), 1, name)
            self.assertEqual(found[0]["proposal"]["patch"]["category"], "Manpower", name)


class Categories(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_a_price_per_unit_of_TIME_is_manpower_without_asking_anyone(self):
        iid = make(name_en="Common Laborer", unit_en="Per Day", price_native=24.0)
        finding = only("category")[0]
        self.assertEqual(finding["proposal"]["patch"]["category"], "Manpower")
        master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        self.assertEqual(master_items.get_record(iid)["item"]["category"], "Manpower")

    def test_an_ordinary_product_is_left_for_a_person_or_the_model(self):
        make(name_en="Whiteboard Marker", unit_en="PACK")
        self.assertEqual(only("category"), [],
                         "guessing from a unit only works where the unit is time")


class Rulings(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_not_an_issue_is_never_asked_again(self):
        make(name_en="Post-it", unit_en="PACK", price_native=2.75)
        make(name_en="Post-it", unit_en="PACK", price_native=1.0)
        finding = only("duplicate")[0]
        self.assertTrue(master_doctor.dismiss(finding["kind"], finding["key"])["ok"])
        self.assertEqual(only("duplicate"), [])

    def test_a_ruling_survives_the_records_being_MERGED_and_rebuilt(self):
        """Keyed on words, never on `item_id`. A merge deletes ids, so an
        id-keyed ruling would be resurrected by the next scan - which is the
        nagging this replaces."""
        first = make(name_en="Trash Bin", unit_en="EA", price_native=6.0)
        second = make(name_en="Trash Bin", unit_en="EA", price_native=6.0)
        finding = only("duplicate")[0]
        master_doctor.dismiss(finding["kind"], finding["key"])
        master_items.delete_records([first, second])
        make(name_en="Trash Bin", unit_en="EA", price_native=6.0)
        make(name_en="Trash Bin", unit_en="EA", price_native=6.0)
        self.assertEqual(only("duplicate"), [],
                         "the same problem under new ids is the same problem")

    def test_a_dismissal_can_be_taken_back(self):
        make(name_en="Post-it", unit_en="PACK", price_native=2.75)
        make(name_en="Post-it", unit_en="PACK", price_native=1.0)
        finding = only("duplicate")[0]
        master_doctor.dismiss(finding["kind"], finding["key"])
        master_doctor.undismiss(finding["kind"], finding["key"])
        self.assertEqual(len(only("duplicate")), 1)

    def test_dismissing_changes_no_data(self):
        a = make(name_en="Post-it", unit_en="PACK", price_native=2.75, use_count=22)
        b = make(name_en="Post-it", unit_en="PACK", price_native=1.0, use_count=5)
        finding = only("duplicate")[0]
        master_doctor.dismiss(finding["kind"], finding["key"])
        for iid in (a, b):
            self.assertTrue(master_items.get_record(iid)["ok"],
                            "a ruling is a note, not an edit")


class NothingIsDeletedWithoutAWayBack(unittest.TestCase):
    """A merge DELETES rows and there is no row-level undo.

    The 2026-09-07 live cleanup was only safe to run because it took a restore
    point first; this does the same, once per sitting rather than once per
    finding - thirty merges would otherwise leave thirty copies of a 900-record
    database, and the useful thing to go back to is the catalogue as it was
    before you started.
    """

    def setUp(self):
        wipe()
        master_doctor._SESSION_BACKUP.clear()
        self.addCleanup(wipe)
        self.addCleanup(master_doctor._SESSION_BACKUP.clear)

    def test_a_merge_takes_a_restore_point_first(self):
        make(name_en="Post-it", unit_en="PACK", price_native=2.75, use_count=22)
        make(name_en="Post-it", unit_en="PACK", price_native=1.0, use_count=5)
        finding = only("duplicate")[0]
        self.assertFalse(master_doctor._SESSION_BACKUP.get("path"))
        self.assertTrue(master_doctor.apply(finding["kind"], finding["key"],
                                            finding["proposal"])["ok"])
        saved = master_doctor._SESSION_BACKUP.get("path")
        self.assertTrue(saved, "the catalogue must be copied aside before a delete")
        self.assertTrue(Path(saved).is_dir())
        self.assertTrue(any(Path(saved).iterdir()), "and the copy must contain something")

    def test_the_restore_point_is_a_READABLE_catalogue(self):
        make(name_en="Trash Bin", unit_en="EA", price_native=6.0, use_count=9)
        make(name_en="Trash Bin", unit_en="EA", price_native=6.0, use_count=2)
        finding = only("duplicate")[0]
        master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        copy = next(Path(master_doctor._SESSION_BACKUP["path"]).glob("*.db"), None)
        self.assertIsNotNone(copy, "a file copy of a live SQLite db is not enough - "
                                   "it must be a real snapshot")
        conn = sqlite3.connect(str(copy))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            before = conn.execute("SELECT COUNT(*) FROM master_items").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(before, 2, "the snapshot holds BOTH rows - the state to "
                                    "go back to, not the state after")

    def test_one_restore_point_per_sitting_not_per_finding(self):
        for name in ("Cable Tie", "Duct Tape"):
            make(name_en=name, unit_en="PACK", price_native=3.0, use_count=8)
            make(name_en=name, unit_en="PACK", price_native=3.0, use_count=1)
        first = only("duplicate")
        master_doctor.apply(first[0]["kind"], first[0]["key"], first[0]["proposal"])
        made = master_doctor._SESSION_BACKUP["path"]
        second = only("duplicate")
        master_doctor.apply(second[0]["kind"], second[0]["key"], second[0]["proposal"])
        self.assertEqual(master_doctor._SESSION_BACKUP["path"], made,
                         "thirty merges must not leave thirty copies")

    def test_a_failed_restore_point_STOPS_the_merge(self):
        make(name_en="Pen", unit_en="EA", price_native=1.0, use_count=5)
        make(name_en="Pen", unit_en="EA", price_native=1.0, use_count=1)
        finding = only("duplicate")[0]
        real = master_doctor.restore_point
        master_doctor.restore_point = lambda force=False: {"ok": False, "error": "disk full"}
        self.addCleanup(lambda: setattr(master_doctor, "restore_point", real))
        r = master_doctor.apply(finding["kind"], finding["key"], finding["proposal"])
        self.assertFalse(r["ok"], "no way back means no delete")
        self.assertIn("disk full", r["error"])
        self.assertEqual(len(only("duplicate")), 1, "and the records are untouched")

    def test_a_NON_deleting_fix_needs_no_restore_point(self):
        """Setting a field is undoable by hand; copying 900 records to fix one
        typed date would make the button feel expensive for no reason."""
        make(name_en="Laborer", unit_en="Per Day", description_en="Date:18/07/2026")
        finding = only("junk_date")[0]
        self.assertTrue(master_doctor.apply(finding["kind"], finding["key"],
                                            finding["proposal"])["ok"])
        self.assertFalse(master_doctor._SESSION_BACKUP.get("path"))


class ScanIsSafe(unittest.TestCase):
    def setUp(self):
        wipe()
        self.addCleanup(wipe)

    def test_scanning_writes_nothing(self):
        make(name_en="Round Table", unit_en="EA", price_native=300.0)
        make(name_en="Round Table", unit_en="EA", price_native=400.0)
        make(name_en="Common Laborer", unit_en="Per Day",
             description_en="Date:18/07/2026")
        before = master_items.list_records("", "used", 500)["rows"]
        master_doctor.scan()
        master_doctor.scan()
        after = master_items.list_records("", "used", 500)["rows"]
        self.assertEqual(before, after, "a scan is a read")

    def test_every_kind_is_counted_even_when_only_some_are_asked_for(self):
        make(name_en="Round Table", unit_en="EA", price_native=300.0)
        make(name_en="Round Table", unit_en="EA", price_native=400.0)
        make(name_en="Laborer", unit_en="Per Day", description_en="Date:18/07/2026")
        counts = master_doctor.scan()["counts"]
        self.assertEqual(counts.get("duplicate"), 1)
        self.assertEqual(counts.get("junk_date"), 1)
        self.assertEqual(counts.get("category"), 1)

    def test_an_empty_catalogue_is_not_an_error(self):
        result = master_doctor.scan()
        self.assertTrue(result["ok"])
        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
