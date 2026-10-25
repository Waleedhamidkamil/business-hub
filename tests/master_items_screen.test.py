# -*- coding: utf-8 -*-
"""The Master Data screen's backend: eight fields, one row per item.

Waleed's spec, in his words: "one place that holds every item that gets into
the app and stores its details - name in English, name in Arabic, description,
unit, price, cost and image - nothing more, all under one item number".

Description and unit exist ONCE PER LANGUAGE (2026-08-17), the same way the
names do - "for each arabic item there is description in arabic and unit in
arabic so that also needs to be included in each items details".

So: no folding, no variants, no per-document duplicates, and NO source pills.
An item belonged to four catalogs only because it was stored four times, and it
is not any more.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown, and the record is rebuilt afterwards.
"""
import json, sys, os, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import master_items as mi

EN = "ZZ_SCREEN_Widget"
AR = "ZZ_SCREEN_قطعة"
# Distinctive: searching "d" would match hundreds of real items and the
# sentinel would fall off the end of the page.
DESC = "ZZ_SCREEN_descriptive"
UNIT = "ZZUNIT"


def _wipe():
    conn = mi._connect()
    try:
        conn.execute("DELETE FROM master_items "
                     "WHERE name_en LIKE 'ZZ\\_SCREEN%' ESCAPE '\\'"
                     "   OR name_ar LIKE 'ZZ\\_SCREEN%' ESCAPE '\\'")
        conn.commit()
    finally:
        conn.close()


def _make(**over):
    conn = mi._connect()
    try:
        mi.init(conn)
        f = {"name_en": EN, "name_ar": AR,
             "description_en": DESC, "description_ar": "",
             "unit_en": UNIT, "unit_ar": "",
             "price_native": 5.0, "price_native_ccy": "USD",
             "cost_native": 7500.0, "cost_native_ccy": "IQD", "item_no": "M-99999"}
        f.update(over)
        cur = conn.execute(
            "INSERT INTO master_items(item_no, name_en, name_ar, description_en,"
            " description_ar, unit_en, unit_ar,"
            " price_native, price_native_ccy, cost_native, cost_native_ccy,"
            " use_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,3)",
            (f["item_no"], f["name_en"], f["name_ar"], f["description_en"],
             f["description_ar"], f["unit_en"], f["unit_ar"],
             f["price_native"], f["price_native_ccy"],
             f["cost_native"], f["cost_native_ccy"]))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


class TheEightFields(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.iid = _make()
        self.addCleanup(_wipe)

    def test_a_row_carries_exactly_what_the_screen_shows(self):
        r = mi.get_record(self.iid)["item"]
        for k in ("item_no", "name_en", "name_ar",
                  "description_en", "description_ar", "unit_en", "unit_ar",
                  "description", "unit",
                  "price", "price_ccy", "cost", "cost_ccy", "image_ref"):
            self.assertIn(k, r, f"the screen needs {k}")
        self.assertEqual(r["name_en"], EN)
        self.assertEqual(r["name_ar"], AR)

    def test_money_keeps_its_own_currency(self):
        """Price in dollars while cost is in dinars is ordinary here. An
        amount without its currency is the 1500x mistake."""
        r = mi.get_record(self.iid)["item"]
        self.assertEqual((r["price"], r["price_ccy"]), (5.0, "USD"))
        self.assertEqual((r["cost"], r["cost_ccy"]), (7500.0, "IQD"))

    def test_a_missing_amount_reports_no_currency(self):
        mi.save_record(self.iid, {"cost_native": ""})
        r = mi.get_record(self.iid)["item"]
        self.assertIsNone(r["cost"])
        self.assertEqual(r["cost_ccy"], "",
                         "a currency with no amount is noise on the screen")
        # …and it is not STORED either. `_row_out` hides it on the way out, so
        # checking only the API response cannot tell the two apart.
        conn = mi._connect()
        try:
            got = conn.execute("SELECT cost_native_ccy FROM master_items "
                               "WHERE item_id = ?", (self.iid,)).fetchone()[0]
        finally:
            conn.close()
        self.assertIsNone(got, "a currency was stored with no amount beside it")


class Searching(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.iid = _make()
        self.addCleanup(_wipe)

    def _found(self, q):
        return [r["item_id"] for r in mi.list_records(search=q, limit=50)["rows"]]

    def test_one_box_searches_every_field(self):
        for q in ("M-99999", EN, AR, DESC, UNIT):
            self.assertIn(self.iid, self._found(q), f"searching {q!r} missed it")

    def test_either_language_finds_it(self):
        self.assertIn(self.iid, self._found("ZZ_SCREEN_قطعة"))
        self.assertIn(self.iid, self._found("ZZ_SCREEN_Widget"))

    def test_the_item_appears_ONCE(self):
        """The whole point: one item, one row. No folding, no variants."""
        self.assertEqual(self._found(EN).count(self.iid), 1)


class Editing(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.iid = _make()
        self.addCleanup(_wipe)

    def test_every_field_is_editable(self):
        mi.save_record(self.iid, {
            "name_en": "ZZ_SCREEN_Renamed", "name_ar": "ZZ_SCREEN_مسمى",
            "description_en": "new d", "description_ar": "وصف جديد",
            "unit_en": "BOX", "unit_ar": "كارتون",
            "price_native": 9.5, "price_native_ccy": "USD",
            "cost_native": 12000, "cost_native_ccy": "IQD"})
        r = mi.get_record(self.iid)["item"]
        self.assertEqual(r["name_en"], "ZZ_SCREEN_Renamed")
        self.assertEqual(r["name_ar"], "ZZ_SCREEN_مسمى")
        self.assertEqual(r["description_en"], "new d")
        self.assertEqual(r["description_ar"], "وصف جديد")
        self.assertEqual(r["unit_en"], "BOX")
        self.assertEqual(r["unit_ar"], "كارتون")
        self.assertEqual((r["price"], r["price_ccy"]), (9.5, "USD"))
        self.assertEqual((r["cost"], r["cost_ccy"]), (12000.0, "IQD"))

    def test_it_is_a_patch_not_a_replace(self):
        mi.save_record(self.iid, {"unit_en": "BOX"})
        r = mi.get_record(self.iid)["item"]
        self.assertEqual(r["unit_en"], "BOX")
        self.assertEqual(r["name_en"], EN, "an omitted field must be left alone")
        self.assertEqual(r["price"], 5.0)

    def test_an_item_must_keep_a_name_in_ONE_language(self):
        """Clearing both leaves a row nobody can ever find again."""
        r = mi.save_record(self.iid, {"name_en": "", "name_ar": ""})
        self.assertFalse(r.get("ok"))
        self.assertEqual(mi.get_record(self.iid)["item"]["name_en"], EN)

    def test_clearing_ONE_name_is_allowed(self):
        self.assertTrue(mi.save_record(self.iid, {"name_ar": ""}).get("ok"))
        self.assertEqual(mi.get_record(self.iid)["item"]["name_ar"], "")

    def test_an_unknown_field_is_ignored_not_stored(self):
        mi.save_record(self.iid, {"unit": "BOX", "use_count": 9999,
                                  "item_no": "M-00001"})
        r = mi.get_record(self.iid)["item"]
        self.assertEqual(r["uses"], 3, "use count is not the user's to edit")
        self.assertEqual(r["item_no"], "M-99999",
                         "the material number is the one thing that never moves")

    def test_an_unknown_item_is_refused(self):
        self.assertFalse(mi.save_record(999999999, {"unit": "X"}).get("ok"))
        self.assertFalse(mi.get_record(999999999).get("ok"))


class AddingOneByHand(unittest.TestCase):
    """Waleed: "we should also be able to fill it by hand by the way, its a
    master data at the end." Until now a record could only appear as a side
    effect of saving a document, which made this screen a report, not a master.
    """

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_an_item_can_be_created_from_nothing(self):
        r = mi.create_record({"name_en": EN, "name_ar": AR,
                              "description_en": DESC, "unit_en": UNIT,
                              "price_native": 4, "price_native_ccy": "USD",
                              "cost_native": 6000, "cost_native_ccy": "IQD"})
        self.assertTrue(r.get("ok"), r)
        it = r["item"]
        self.assertEqual(it["name_en"], EN)
        self.assertEqual(it["name_ar"], AR)
        self.assertEqual((it["price"], it["price_ccy"]), (4.0, "USD"))
        self.assertEqual((it["cost"], it["cost_ccy"]), (6000.0, "IQD"))

    def test_it_is_numbered_immediately(self):
        """An item with no number is not "all under one item number"."""
        it = mi.create_record({"name_en": EN})["item"]
        self.assertRegex(it["item_no"], r"^M-\d{5}$")

    def test_it_appears_on_the_screen_at_once(self):
        iid = mi.create_record({"name_en": EN})["item"]["item_id"]
        ids = [r["item_id"] for r in mi.list_records(search=EN, limit=50)["rows"]]
        self.assertIn(iid, ids)

    def test_a_name_in_one_language_is_enough(self):
        self.assertTrue(mi.create_record({"name_ar": AR}).get("ok"))

    def test_an_item_with_no_name_at_all_is_refused(self):
        r = mi.create_record({"unit_en": "KG", "price_native": 5})
        self.assertFalse(r.get("ok"))

    def test_a_language_free_box_is_filed_by_script(self):
        it = mi.create_record({"name_en": EN, "unit": "كيلو"})["item"]
        self.assertEqual(it["unit_ar"], "كيلو")
        self.assertEqual(it["unit_en"], "")

    def test_entering_an_item_that_is_ALREADY_on_file_is_refused(self):
        """Two records for one product is the whole problem being fixed. The
        refusal names the item so the screen can go there instead."""
        first = mi.create_record({"name_en": EN, "description_en": DESC,
                                  "unit_en": UNIT})["item"]
        again = mi.create_record({"name_en": EN, "description_en": DESC,
                                  "unit_en": UNIT})
        self.assertFalse(again.get("ok"))
        self.assertEqual(again["already"]["item_id"], first["item_id"],
                         "the refusal must say WHICH item it already is")

    def test_the_same_name_with_a_DIFFERENT_description_is_a_new_item(self):
        """Bus at 2150 and Bus at 2650 are two products, not one."""
        a = mi.create_record({"name_en": EN, "description_en": "shuttle only",
                              "unit_en": UNIT})
        b = mi.create_record({"name_en": EN, "description_en": "with transport",
                              "unit_en": UNIT})
        self.assertTrue(a.get("ok"))
        self.assertTrue(b.get("ok"), b)
        self.assertNotEqual(a["item"]["item_id"], b["item"]["item_id"])
        self.assertNotEqual(a["item"]["item_no"], b["item"]["item_no"])

    def test_a_hand_made_item_can_then_be_edited_like_any_other(self):
        iid = mi.create_record({"name_en": EN})["item"]["item_id"]
        self.assertTrue(mi.save_record(iid, {"unit_en": "BOX"}).get("ok"))
        self.assertEqual(mi.get_record(iid)["item"]["unit_en"], "BOX")

    def test_a_currency_is_not_stored_without_an_amount(self):
        it = mi.create_record({"name_en": EN, "cost_native_ccy": "IQD"})["item"]
        self.assertIsNone(it["cost"])
        self.assertEqual(it["cost_ccy"], "")


class FindingDuplicates(unittest.TestCase):
    """The Master Data screen's own duplicate finder. It replaced a separate
    review panel built around CATALOG ROWS, which could only look inside one
    document type at a time and described a storage shape that is gone."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def _found(self):
        return {r["item_id"] for r in
                mi.list_records(search="ZZ_SCREEN", missing="duplicate",
                                limit=200)["rows"]}

    def test_the_same_name_typed_with_a_LINE_BREAK_is_a_duplicate(self):
        """The live case: "BOX ,<newline>CAMERA" beside "BOX , CAMERA" - one
        item saved twice because a line break was typed into the cell."""
        a = _make(name_en="ZZ_SCREEN_BOX , CAMERA", name_ar="", item_no="")
        b = _make(name_en="ZZ_SCREEN_BOX ,\nCAMERA", name_ar="", item_no="")
        self.assertEqual(self._found(), {a, b})

    def test_a_lone_item_is_not_a_duplicate(self):
        _make(item_no="")
        self.assertEqual(self._found(), set())

    def test_REAL_VARIANTS_are_not_duplicates(self):
        """`Hemp rope` φ12 / φ15 / φ20 share a name and are three products.
        Matching on the name alone flagged 254 records on the live catalogue;
        the description and the unit complete the key."""
        _make(name_ar="", description_en="12mm", item_no="")
        _make(name_ar="", description_en="20mm", item_no="")
        self.assertEqual(self._found(), set(),
                         "two sizes of one product are not a duplicate")

    def test_it_matches_on_the_ARABIC_name_too(self):
        a = _make(name_en="", name_ar="ZZ_SCREEN_قطعة كبيرة", item_no="")
        b = _make(name_en="", name_ar="ZZ_SCREEN_قطعةكبيرة", item_no="")
        self.assertEqual(self._found(), {a, b})

    def test_the_chip_count_matches_what_the_filter_shows(self):
        _make(name_en="ZZ_SCREEN_Twin", name_ar="", item_no="")
        _make(name_en="ZZ_SCREEN_Twin", name_ar="", item_no="")
        r = mi.list_records(missing="duplicate", limit=1)
        self.assertEqual(r["total"], r["missing_counts"]["duplicate"],
                         "a chip that says 12 and then shows 9 is worse than none")


class Numbering(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_every_item_gets_a_number(self):
        iid = _make(item_no="")
        self.assertEqual(mi.get_record(iid)["item"]["item_no"], "")
        mi.assign_numbers(apply=True)
        no = mi.get_record(iid)["item"]["item_no"]
        self.assertTrue(no.startswith("M-"), f"expected an M- number, got {no!r}")

    def test_a_number_is_never_reused(self):
        a, b = _make(item_no=""), _make(item_no="")
        mi.assign_numbers(apply=True)
        na = mi.get_record(a)["item"]["item_no"]
        nb = mi.get_record(b)["item"]["item_no"]
        self.assertNotEqual(na, nb)

    def test_the_number_SURVIVES_a_rebuild(self):
        """`build()` clears the table and re-inserts, so a number assigned here
        rather than carried in from a catalog row used to vanish on the next
        rebuild - 250 of 821 records lost theirs that way. That breaks the one
        promise the number makes: all under ONE item number, forever."""
        from backend import database
        # A record the rebuild will reproduce, so it survives to be compared.
        database.remember_dn_item(EN, {"description": DESC, "unit": UNIT})
        rows = [r for r in mi.list_records(search=EN, limit=9)["rows"]
                if r["name_en"] == EN]
        self.assertTrue(rows, "the record should exist after a build")
        # Stamp a number the numbering sequence would never produce. Without
        # the carry-over the rebuild hands out the NEXT M-xxxxx instead, and
        # because that sequence is deterministic a plain before/after compare
        # passes either way - an earlier version of this test did.
        before = "M-90001"
        conn = mi._connect()
        try:
            conn.execute("UPDATE master_items SET item_no = ? WHERE item_id = ?",
                         (before, rows[0]["item_id"]))
            conn.commit()
        finally:
            conn.close()
        after = [r for r in mi.list_records(search=EN, limit=9)["rows"]
                 if r["name_en"] == EN][0]["item_no"]
        self.assertEqual(after, before, "the number changed across a rebuild")
        with database._connect_shared() as c:
            try:
                c.execute("DELETE FROM dn_items WHERE name = ?", (EN,))
            except sqlite3.OperationalError:
                pass

    def test_a_build_leaves_nothing_unnumbered(self):
        conn = mi._connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM master_items "
                             "WHERE item_no IS NULL OR item_no = ''").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_a_STRAY_HIGH_number_does_not_drag_the_sequence_up(self):
        """The sequence runs "one past the highest", so one stray high number
        drags every later item with it. This file's own sentinel is M-99999,
        and on the live catalogue that pushed 51 REAL items into the M-99xxx
        range - which reads as junk beside 800 items numbered in the hundreds.

        Numbers at or above `_RESERVED_FROM` belong to somebody else - a test,
        or a number typed by hand. They stay where they are and are still
        refused as duplicates; they simply do not move the sequence."""
        _make(item_no="M-99999")                       # the stray
        iid = _make(item_no="", name_en=EN + " Next")
        mi.assign_numbers(apply=True)
        got = mi.get_record(iid)["item"]["item_no"]
        n = int(got.split("-")[1])
        self.assertLess(n, mi._RESERVED_FROM,
                        f"a sentinel number dragged the sequence to {got}")

    def test_a_hand_typed_number_in_that_band_is_left_alone(self):
        iid = _make(item_no="M-99999")
        mi.assign_numbers(apply=True)
        self.assertEqual(mi.get_record(iid)["item"]["item_no"], "M-99999",
                         "a number the app did not issue is still the item's")

    def test_a_dry_run_assigns_nothing(self):
        iid = _make(item_no="")
        out = mi.assign_numbers(apply=False)
        self.assertGreater(out["assigned"], 0)
        self.assertEqual(out["applied"], 0)
        self.assertEqual(mi.get_record(iid)["item"]["item_no"], "")


class MissingFilter(unittest.TestCase):
    """How the catalog gets finished off: show me what still has no Arabic
    name, no cost, no picture."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_it_finds_records_lacking_a_field(self):
        iid = _make(name_ar="", cost_native=None, cost_native_ccy=None)
        for f in ("name_ar", "cost"):
            ids = [r["item_id"] for r in
                   mi.list_records(search="ZZ_SCREEN", missing=f, limit=50)["rows"]]
            self.assertIn(iid, ids, f"missing={f} did not find it")

    def test_a_complete_record_is_not_listed_as_missing(self):
        iid = _make()
        for f in ("name_ar", "cost", "price", "unit"):
            ids = [r["item_id"] for r in
                   mi.list_records(search="ZZ_SCREEN", missing=f, limit=50)["rows"]]
            self.assertNotIn(iid, ids, f"missing={f} wrongly included a full record")


def tearDownModule():
    _wipe()


if __name__ == "__main__":
    unittest.main(verbosity=2)
