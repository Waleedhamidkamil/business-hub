# -*- coding: utf-8 -*-
"""A description and a unit PER LANGUAGE, exactly as the names are (2026-08-17).

Waleed: "for each arabic item there is description in arabic and unit in arabic
so that also needs to be included in each items details in master data".

He is describing a real loss. The PO is written in Arabic and the quotation,
delivery note and invoice in English, so the same product carries كيلو on one
document and KG on the other. The record had ONE `unit` column, kept whichever
it met first, and threw the other away: `Celery / كرفس` stored كيلو while the
`KG` its own delivery notes used was nowhere in the record.

The language of a value is decided by the SCRIPT OF THE TEXT, never by the
document that carried it - an Arabic PO line whose unit cell says "KG" really
does hold an English unit.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown. The migration test uses its own scratch database.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_items as mi, shared_library

EN = "ZZ_LANG_Widget"
AR = "ZZ_LANG_قطعة"
NAMES = (EN, AR)
LIKE = "name_en LIKE 'ZZ\\_LANG%' ESCAPE '\\' OR name_ar LIKE 'ZZ\\_LANG%' ESCAPE '\\'"


def _wipe():
    with database._connect_shared() as conn:
        for n in NAMES:
            for t in ("items", "quote_items", "dn_items", "inv_items"):
                try:
                    conn.execute(f"DELETE FROM {t} WHERE name = ?", (n,))
                except sqlite3.OperationalError:
                    pass
        conn.execute(f"DELETE FROM master_items WHERE {LIKE}")


def _recs():
    conn = mi._connect()
    try:
        return [dict(r) for r in
                conn.execute(f"SELECT * FROM master_items WHERE {LIKE}")]
    finally:
        conn.close()


def _rec():
    got = _recs()
    assert len(got) == 1, f"expected exactly one record, got {len(got)}"
    return got[0]


class WhichLanguageIsThis(unittest.TestCase):
    """The routing rule on its own, with no database in the way."""

    def test_arabic_text_goes_to_the_arabic_slot(self):
        for w in ("كيلو", "كارتون", "حجم كبير"):
            self.assertEqual(mi._slot("unit", w), "unit_ar", w)
            self.assertEqual(mi._slot("description", w), "description_ar", w)

    def test_english_text_goes_to_the_english_slot(self):
        for w in ("KG", "BOX", "Big size"):
            self.assertEqual(mi._slot("unit", w), "unit_en", w)

    def test_text_with_no_letters_counts_as_english(self):
        """"12mm" and "220V" belong to neither language. They go in the English
        slot, and `prefer` serves them to an Arabic caller anyway, so nothing is
        hidden from the side that has no value of its own."""
        self.assertEqual(mi._slot("description", "12mm"), "description_en")
        self.assertEqual(mi.prefer("12mm", "", "ar"), "12mm")

    def test_a_blank_is_not_arabic(self):
        self.assertFalse(mi.is_arabic(""))
        self.assertFalse(mi.is_arabic(None))


class PreferringOne(unittest.TestCase):

    def test_it_gives_back_the_language_asked_for(self):
        self.assertEqual(mi.prefer("KG", "كيلو", "ar"), "كيلو")
        self.assertEqual(mi.prefer("KG", "كيلو", "en"), "KG")
        self.assertEqual(mi.prefer("KG", "كيلو"), "KG", "English by default")

    def test_one_known_unit_beats_none(self):
        """A PO that finds only KG should print KG, not nothing."""
        self.assertEqual(mi.prefer("KG", "", "ar"), "KG")
        self.assertEqual(mi.prefer("", "كيلو", "en"), "كيلو")

    def test_nothing_stored_gives_nothing_back(self):
        self.assertEqual(mi.prefer("", "", "ar"), "")


class SavingDocumentsFillsBothSlots(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_the_english_document_and_the_arabic_PO_each_land_in_their_own_slot(self):
        """THE BUG, end to end. Two documents, one item, two units - and both
        survive. Before this the record kept KG and dropped كيلو."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_item(AR, "", "كيلو", "TDC", {"name_en": EN})
        r = _rec()
        self.assertEqual(r["unit_en"], "KG")
        self.assertEqual(r["unit_ar"], "كيلو")

    def test_the_arabic_side_arriving_first_works_the_same_way(self):
        """Order must not decide which language is kept."""
        database.remember_item(AR, "", "كيلو", "TDC", {})
        database.remember_dn_item(EN, {"description": "", "unit": "KG",
                                       "name_ar": AR})
        r = _rec()
        self.assertEqual((r["unit_en"], r["unit_ar"]), ("KG", "كيلو"))

    def test_the_DOCUMENT_does_not_decide_the_language___the_TEXT_does(self):
        """An Arabic PO line whose unit cell says KG holds an ENGLISH unit.
        Filing it under Arabic because the PO is Arabic would put KG and كيلو in
        the same slot and lose one of them."""
        database.remember_item(AR, "", "KG", "TDC", {})
        r = _rec()
        self.assertEqual(r["unit_en"], "KG",
                         "an English word on an Arabic document is still English")
        self.assertEqual(r["unit_ar"], "")

    def test_descriptions_split_the_same_way(self):
        database.remember_dn_item(EN, {"description": "Big size", "unit": "KG"})
        database.remember_item(AR, "حجم كبير", "كيلو", "TDC", {"name_en": EN})
        r = _rec()
        self.assertEqual(r["description_en"], "Big size")
        self.assertEqual(r["description_ar"], "حجم كبير")

    def test_a_second_save_never_overwrites_a_slot_that_is_filled(self):
        """The record LEARNS; it does not forget. Same rule as _STICKY_MEMORY."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_dn_item(EN, {"description": "", "unit": "KGS"})
        self.assertEqual(_rec()["unit_en"], "KG",
                         "the first English unit was overwritten")

    def test_a_save_that_says_nothing_does_not_blank_a_slot(self):
        database.remember_dn_item(EN, {"description": "Big size", "unit": "KG"})
        database.remember_dn_item(EN, {"description": "Big size", "unit": ""})
        self.assertEqual(_rec()["unit_en"], "KG")

    def test_a_new_record_is_numbered_by_the_time_the_screen_shows_it(self):
        """"All under one item number" cannot wait for the next rebuild - 9 live
        records were sitting unnumbered because it did. `remember()` itself must
        NOT number, or a document carrying its own item number could never teach
        it; the screen closes the gap instead."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        mi.list_records(search="ZZ_LANG", limit=5)
        self.assertRegex(_rec()["item_no"] or "", r"^M-\d{5}$")

    def test_a_document_can_still_teach_its_own_item_number(self):
        """The reason the numbering does not happen on the save."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_inv_item("materials", EN,
                                   {"description": "", "unit": "KG",
                                    "item_no": "ZZ-77123"})
        self.assertEqual(_rec()["item_no"], "ZZ-77123")


class VariantsStayApart(unittest.TestCase):
    """The language split must not loosen what keeps two products two records.
    A description is the discriminator between `Rope` and `Rope 12mm`."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_a_plain_item_does_not_absorb_a_described_one(self):
        database.remember_dn_item(EN, {"description": "", "unit": "EA"})
        database.remember_dn_item(EN, {"description": "12mm", "unit": "EA"})
        self.assertEqual(len(_recs()), 2,
                         "plain and 12mm are different products")

    def test_the_described_one_does_not_absorb_a_plain_one_either(self):
        database.remember_dn_item(EN, {"description": "12mm", "unit": "EA"})
        database.remember_dn_item(EN, {"description": "", "unit": "EA"})
        self.assertEqual(len(_recs()), 2)

    def test_two_sizes_in_the_same_language_stay_two_records(self):
        database.remember_dn_item(EN, {"description": "12mm", "unit": "EA"})
        database.remember_dn_item(EN, {"description": "20mm", "unit": "EA"})
        self.assertEqual(len(_recs()), 2)

    def test_the_right_size_is_picked_when_one_of_them_was_read_first(self):
        """A 12mm record read first has nothing in Arabic to disagree with, so
        it "fits" an Arabic 20mm row. The EXACT match has to win."""
        database.remember_dn_item(EN, {"description": "12mm", "unit": "EA"})
        database.remember_dn_item(EN, {"description": "20mm", "unit": "EA"})
        database.remember_dn_item(EN, {"description": "20mm", "unit": "EA",
                                       "cost_native": 90.0,
                                       "cost_native_ccy": "IQD"})
        got = {r["description_en"]: r["cost_native"] for r in _recs()}
        self.assertEqual(got.get("20mm"), 90.0, "the cost landed on 12mm")
        self.assertIsNone(got.get("12mm"))


class TheRebuildFilesByLanguageToo(unittest.TestCase):
    """`build()` is the full rebuild. It has to file by language exactly as a
    document save does, or one rebuild would quietly undo every second unit."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_a_rebuild_gives_a_paired_item_both_languages(self):
        database.remember_dn_item(EN, {"description": "Big size", "unit": "KG"})
        database.remember_item(AR, "حجم كبير", "كيلو", "TDC", {"name_en": EN})
        r = _rec()
        self.assertEqual((r["unit_en"], r["unit_ar"]), ("KG", "كيلو"))
        self.assertEqual((r["description_en"], r["description_ar"]),
                         ("Big size", "حجم كبير"))


class TheScreenShowsAndEditsBoth(unittest.TestCase):

    def setUp(self):
        _wipe()
        database.remember_dn_item(EN, {"description": "Big size", "unit": "KG"})
        database.remember_item(AR, "حجم كبير", "كيلو", "TDC", {"name_en": EN})
        self.iid = _rec()["item_id"]
        self.addCleanup(_wipe)

    def _item(self):
        return mi.get_record(self.iid)["item"]

    def test_the_panel_is_handed_all_four(self):
        it = self._item()
        self.assertEqual(it["unit_en"], "KG")
        self.assertEqual(it["unit_ar"], "كيلو")
        self.assertEqual(it["description_en"], "Big size")
        self.assertEqual(it["description_ar"], "حجم كبير")

    def test_the_plain_unit_is_DERIVED_not_a_third_copy(self):
        """A stored third copy drifts - the two `_SIG_IGNORE` lists proved it.
        `unit` is computed on the way out, so clearing a slot moves it."""
        self.assertEqual(self._item()["unit"], "KG")
        mi.save_record(self.iid, {"unit_en": ""})
        self.assertEqual(self._item()["unit"], "كيلو",
                         "with no English unit left, the Arabic one is the unit")
        conn = mi._connect()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(master_items)")}
        finally:
            conn.close()
        self.assertNotIn("unit", cols, "a third copy of the unit is stored")
        self.assertNotIn("description", cols)

    def test_each_slot_is_edited_on_its_own(self):
        mi.save_record(self.iid, {"unit_ar": "كارتون"})
        it = self._item()
        self.assertEqual(it["unit_ar"], "كارتون")
        self.assertEqual(it["unit_en"], "KG", "the other language was disturbed")

    def test_typing_arabic_into_the_english_box_is_left_where_it_was_typed(self):
        """The routing rule is for callers with ONE box. A field named
        `unit_en` is literal - second-guessing it would move the user's text
        out from under them."""
        mi.save_record(self.iid, {"unit_en": "كيلو"})
        self.assertEqual(self._item()["unit_en"], "كيلو")

    def test_a_caller_with_one_box_is_filed_by_script(self):
        mi.save_record(self.iid, {"unit_en": "", "unit_ar": ""})
        mi.save_record(self.iid, {"unit": "كارتون"})
        it = self._item()
        self.assertEqual(it["unit_ar"], "كارتون")
        self.assertEqual(it["unit_en"], "", "it was filed under the wrong language")

    def test_clearing_the_one_box_clears_BOTH(self):
        """"This item has no unit" cannot leave one behind - the caller
        believes it just removed the unit."""
        mi.save_record(self.iid, {"unit": ""})
        it = self._item()
        self.assertEqual((it["unit_en"], it["unit_ar"]), ("", ""))

    def test_search_finds_it_by_either_language(self):
        for q in ("KG", "كيلو", "Big size", "حجم كبير"):
            ids = [r["item_id"] for r in
                   mi.list_records(search=q, limit=400)["rows"]]
            self.assertIn(self.iid, ids, f"searching {q!r} missed it")


class WhatIsStillMissing(unittest.TestCase):
    """The chips that say what is left to fill in."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def _missing(self, key):
        return [r["item_id"] for r in
                mi.list_records(search="ZZ_LANG", missing=key, limit=400)["rows"]]

    def test_no_unit_means_neither_language(self):
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        iid = _rec()["item_id"]
        self.assertNotIn(iid, self._missing("unit"),
                         "it has an English unit, so it is not missing a unit")

    def test_no_arabic_unit_is_asked_only_of_items_that_HAVE_an_arabic_name(self):
        """An item that has never been on a PO does not need an Arabic unit
        yet. Counting all 500 of those would bury the ones that do.

        An UNRECOGNISED unit on purpose: a `KG` would be completed to كيلو the
        moment the screen opened, and the item would drop off this chip for a
        reason that has nothing to do with the rule under test."""
        database.remember_dn_item(EN, {"description": "", "unit": "blorps"})
        r = _rec()
        self.assertFalse(r["name_ar"], "this item is English-only")
        self.assertNotIn(r["item_id"], self._missing("unit_ar"))

    def test_an_item_that_HAS_an_arabic_name_and_no_arabic_unit_is_listed(self):
        """Only an item whose unit the app cannot TRANSLATE reaches this chip:
        opening the screen fills every unit it understands, so `KG` here would
        already have grown its كيلو (see unit_in_both_languages.test.py)."""
        database.remember_dn_item(EN, {"description": "", "unit": "blorps"})
        database.remember_item(AR, "", "", "TDC", {"name_en": EN})
        r = _rec()
        self.assertEqual(r["name_ar"], AR)
        self.assertIn(r["item_id"], self._missing("unit_ar"))

    def test_a_unit_the_app_UNDERSTANDS_never_reaches_that_chip(self):
        """Because the screen fills it on the way past."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_item(AR, "", "", "TDC", {"name_en": EN})
        self.assertNotIn(_rec()["item_id"], self._missing("unit_ar"))
        self.assertEqual(_rec()["unit_ar"], "كيلو")

    def test_an_item_with_both_units_is_missing_neither(self):
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        database.remember_item(AR, "", "كيلو", "TDC", {"name_en": EN})
        iid = _rec()["item_id"]
        for key in ("unit", "unit_ar", "unit_en"):
            self.assertNotIn(iid, self._missing(key), key)

    def test_the_count_and_the_list_agree(self):
        """A chip that says 12 and then shows 9 is worse than no chip."""
        database.remember_dn_item(EN, {"description": "", "unit": "KG"})
        for key in ("unit", "unit_ar", "name_ar", "cost"):
            r = mi.list_records(missing=key, limit=1)
            self.assertEqual(r["total"], r["missing_counts"][key],
                             f"{key}: the chip and the filter disagree")


class MergingKeepsBoth(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_the_survivor_takes_the_language_it_is_missing(self):
        conn = mi._connect()
        try:
            mi.init(conn)
            keep = conn.execute(
                "INSERT INTO master_items(name_en, unit_en, description_en) "
                "VALUES(?,?,?)", (EN, "KG", "Big size")).lastrowid
            drop = conn.execute(
                "INSERT INTO master_items(name_ar, unit_ar, description_ar) "
                "VALUES(?,?,?)", (AR, "كيلو", "حجم كبير")).lastrowid
            conn.commit()
        finally:
            conn.close()
        mi.merge_records(keep, [drop])
        r = _rec()
        self.assertEqual((r["unit_en"], r["unit_ar"]), ("KG", "كيلو"))
        self.assertEqual((r["description_en"], r["description_ar"]),
                         ("Big size", "حجم كبير"))


class TheMigration(unittest.TestCase):
    """Splitting the old single columns. Runs on its OWN scratch database -
    the live one has already been through it."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE master_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT, item_no TEXT,
                name_en TEXT, name_ar TEXT,
                description TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT '',
                cost_native REAL, cost_native_ccy TEXT,
                price_native REAL, price_native_ccy TEXT,
                image_refs TEXT, img_query TEXT, data_json TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT, created_at TEXT, updated_at TEXT);""")
        conn.executemany(
            "INSERT INTO master_items(item_no, name_en, name_ar, description, unit)"
            " VALUES(?,?,?,?,?)",
            [("M-00001", "Celery", "كرفس", "", "كيلو"),
             ("M-00002", "Rope", "", "12mm", "M"),
             ("M-00003", "", "حبل", "قطر ١٢", "متر")])
        conn.commit()
        conn.close()
        self._real = shared_library.connect
        shared_library.connect = lambda path=None, _r=self._real: _r(self.path)

    def tearDown(self):
        shared_library.connect = self._real
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _rows(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return {r["item_no"]: dict(r) for r in
                    conn.execute("SELECT * FROM master_items")}
        finally:
            conn.close()

    def test_every_value_moves_into_the_slot_for_its_own_script(self):
        mi.init()
        rows = self._rows()
        self.assertEqual(rows["M-00001"]["unit_ar"], "كيلو")
        self.assertEqual(rows["M-00001"]["unit_en"], "")
        self.assertEqual(rows["M-00002"]["unit_en"], "M")
        self.assertEqual(rows["M-00002"]["description_en"], "12mm")
        self.assertEqual(rows["M-00003"]["unit_ar"], "متر")
        self.assertEqual(rows["M-00003"]["description_ar"], "قطر ١٢")

    def test_the_old_columns_are_GONE_so_nothing_can_drift(self):
        mi.init()
        conn = sqlite3.connect(self.path)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(master_items)")}
        finally:
            conn.close()
        self.assertNotIn("unit", cols)
        self.assertNotIn("description", cols)

    def test_nothing_else_about_a_record_changes(self):
        mi.init()
        rows = self._rows()
        self.assertEqual(len(rows), 3, "a record was lost in the migration")
        self.assertEqual(rows["M-00001"]["name_en"], "Celery")
        self.assertEqual(rows["M-00001"]["name_ar"], "كرفس")

    def test_running_it_twice_is_harmless(self):
        """`init()` runs on every connection, so it must be safe to re-enter."""
        mi.init()
        mi.init()
        self.assertEqual(self._rows()["M-00001"]["unit_ar"], "كيلو")


def tearDownModule():
    _wipe()


if __name__ == "__main__":
    unittest.main(verbosity=2)
