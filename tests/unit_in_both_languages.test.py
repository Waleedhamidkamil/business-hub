# -*- coding: utf-8 -*-
"""Every item's unit, in BOTH languages (2026-08-19).

Waleed: "fill every unit in both lang with whats right please".

An item that has a unit in one language has the SAME unit in the other - the PO
says كيلو where the invoice says KG - so a blank slot beside a filled one is a
gap, not a fact. `fill_missing_units` closes it from `UNIT_WORDS`, the one place
in the app where a unit word is invented.

Two things it must never do, because a unit is PRINTED on a document:
  · rewrite a unit somebody typed, and
  · invent one for an item that has no unit in either language - 50 of those
    are plant hire, and a crane is not billed per piece, per day and per month
    interchangeably.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import master_items as mi

PFX = "ZZ_UNIT_"
LIKE = (f"name_en LIKE '{PFX}%' OR name_ar LIKE '{PFX}%'")


def _wipe():
    conn = mi._connect()
    try:
        conn.execute(f"DELETE FROM master_items WHERE {LIKE}")
        conn.commit()
    finally:
        conn.close()


def _make(name, unit_en="", unit_ar=""):
    conn = mi._connect()
    try:
        mi.init(conn)
        cur = conn.execute(
            "INSERT INTO master_items(name_en, unit_en, unit_ar) VALUES(?,?,?)",
            (PFX + name, unit_en, unit_ar))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _units(iid):
    r = mi.get_record(iid)["item"]
    return r["unit_en"], r["unit_ar"]


class TheOtherLanguagesWord(unittest.TestCase):
    """`unit_counterpart` on its own."""

    def test_it_translates_both_ways(self):
        self.assertEqual(mi.unit_counterpart("KG", "ar"), "كيلو")
        self.assertEqual(mi.unit_counterpart("كيلو", "en"), "KG")
        self.assertEqual(mi.unit_counterpart("عدد", "en"), "EA")
        self.assertEqual(mi.unit_counterpart("EA", "ar"), "عدد")

    def test_it_goes_through_the_MEANING_not_the_spelling(self):
        """`PCS`, `piece` and `EA` are one unit, so all three answer عدد."""
        for w in ("PCS", "piece", "EA", "pcs", "UNIT"):
            self.assertEqual(mi.unit_counterpart(w, "ar"), "عدد", w)
        for w in ("KGS", "kilogram", "kg"):
            self.assertEqual(mi.unit_counterpart(w, "ar"), "كيلو", w)

    def test_a_unit_it_does_not_KNOW_gets_no_answer(self):
        """A unit is printed on a document. A guess is worse than a blank."""
        for w in ("blorp", "widgets", "per fortnight"):
            self.assertEqual(mi.unit_counterpart(w, "ar"), "", w)
            self.assertEqual(mi.unit_counterpart(w, "en"), "", w)

    def test_a_blank_stays_blank(self):
        self.assertEqual(mi.unit_counterpart("", "ar"), "")
        self.assertEqual(mi.unit_counterpart(None, "en"), "")

    def test_a_PHRASE_unit_keeps_its_phrase(self):
        """"Per Truck Load 20 m3" is billed by the truck load. Answering it
        with the class word (متر مكعب) would change what the quantity counts."""
        got = mi.unit_counterpart("Per Truck Load 20 m3", "ar")
        self.assertIn("شاحنة", got, "the truck load is gone from the Arabic")
        self.assertEqual(mi.unit_class(got), "m3",
                         f"{got!r} no longer reads as cubic metres")
        self.assertNotEqual(mi.unit_counterpart("Per Truck Load 20 m3", "ar"),
                            mi.UNIT_WORDS["m3"][1])

    def test_the_units_the_catalogue_actually_uses_are_all_known(self):
        """Every meaning in `_UNIT_CLASS` must have a word in BOTH languages, or
        a unit can be recognised for merging and still be unfillable."""
        for cls in set(mi._UNIT_CLASS.values()):
            self.assertIn(cls, mi.UNIT_WORDS, f"{cls} has no word to fill with")
            en, ar = mi.UNIT_WORDS[cls]
            self.assertTrue(en.strip(), f"{cls} has no English word")
            self.assertTrue(ar.strip(), f"{cls} has no Arabic word")

    def test_each_word_is_in_the_language_it_claims(self):
        for cls, (en, ar) in mi.UNIT_WORDS.items():
            self.assertTrue(mi.is_arabic(ar), f"{cls}: {ar!r} is not Arabic")
            self.assertFalse(mi.is_arabic(en), f"{cls}: {en!r} is not English")

    def test_a_filled_word_reads_back_as_the_same_unit(self):
        """The word we write must itself be recognised, or the next pass would
        not know what it means and the record could not merge on it."""
        for cls, (en, ar) in mi.UNIT_WORDS.items():
            self.assertEqual(mi.unit_class(en), cls, f"{cls}: {en!r}")
            self.assertEqual(mi.unit_class(ar), cls, f"{cls}: {ar!r}")
            self.assertTrue(mi.units_agree(en, ar), f"{cls}: {en} != {ar}")


class FillingTheGap(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_an_english_unit_gets_its_arabic_one(self):
        iid = _make("A", unit_en="KG")
        mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("KG", "كيلو"))

    def test_an_arabic_unit_gets_its_english_one(self):
        iid = _make("B", unit_ar="عدد")
        mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("EA", "عدد"))

    def test_a_unit_somebody_TYPED_is_never_rewritten(self):
        """`PCS` and `piece` mean EA but are not EA. Normalising them would
        change what is printed on a document nobody asked to change."""
        iid = _make("C", unit_en="PCS")
        mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("PCS", "عدد"))

    def test_an_item_with_both_units_is_left_completely_alone(self):
        iid = _make("D", unit_en="piece", unit_ar="قطعة")
        mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("piece", "قطعة"))

    def test_an_item_with_NO_unit_is_not_given_one(self):
        """The 50 plant-hire items. A crane is not billed per piece, per day and
        per month interchangeably, and no document ever said which."""
        iid = _make("E")
        out = mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("", ""))
        self.assertGreaterEqual(out["no_unit_at_all"], 1)

    def test_an_unknown_unit_is_reported_rather_than_guessed(self):
        iid = _make("F", unit_en="blorps")
        out = mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("blorps", ""))
        self.assertIn("blorps", out["unknown_unit"])

    def test_a_dry_run_writes_nothing(self):
        iid = _make("G", unit_en="KG")
        out = mi.fill_missing_units(apply=False)
        self.assertGreater(out["filled"], 0, "it found nothing to fill")
        self.assertEqual(out["applied"], 0)
        self.assertEqual(_units(iid), ("KG", ""), "a dry run wrote to the record")

    def test_running_it_twice_changes_nothing_the_second_time(self):
        _make("H", unit_ar="كيلو")
        mi.fill_missing_units(apply=True)
        before = mi.fill_missing_units(apply=False)["filled"]
        mi.fill_missing_units(apply=True)
        self.assertEqual(mi.fill_missing_units(apply=False)["filled"], before)

    def test_the_two_sides_it_writes_always_AGREE(self):
        """The whole point. A fill that produced disagreeing units would split
        the item in two the next time anything matched on unit."""
        for en, ar in (("KG", ""), ("", "عدد"), ("BOX", ""), ("", "متر"),
                       ("Per Truck Load 20 m3", ""),   # the PHRASE unit: its
                       ("m3", ""),                     # Arabic side must still
                       ("", "متر مكعب")):              # read as cubic metres

            iid = _make(f"I{en}{ar}", unit_en=en, unit_ar=ar)
            mi.fill_missing_units(apply=True)
            got_en, got_ar = _units(iid)
            self.assertTrue(got_en and got_ar, f"{en}/{ar} left a gap")
            self.assertTrue(mi.units_agree(got_en, got_ar),
                            f"{got_en!r} and {got_ar!r} do not agree")


class ItFixesAUnitInTheWrongSCRIPT(unittest.TestCase):
    """The one overwrite allowed. '箱' is not an English unit by any reading -
    four came off a Chinese supplier sheet - so rewriting it is not the same as
    rewriting somebody's `PCS`."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_a_chinese_unit_in_the_english_slot_is_replaced(self):
        iid = _make("CJK", unit_en="箱")
        out = mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("BOX", "كارتون"))
        self.assertIn(("箱", "BOX"), out["wrong_script"])

    def test_an_arabic_unit_in_the_english_slot_is_moved_to_its_own_word(self):
        iid = _make("ARinEN", unit_en="كيلو")
        mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("KG", "كيلو"))

    def test_an_ordinary_english_unit_is_NOT_touched(self):
        iid = _make("Plain", unit_en="PCS")
        out = mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid), ("PCS", "عدد"))
        self.assertEqual(out["wrong_script"], [])

    def test_a_wrong_script_unit_we_do_not_understand_is_left_alone(self):
        """Replacing it would DESTROY the only record of what the supplier
        said. It is reported instead."""
        iid = _make("Mystery", unit_en="漢字漢字")
        out = mi.fill_missing_units(apply=True)
        self.assertEqual(_units(iid)[0], "漢字漢字")
        self.assertIn("漢字漢字", out["unknown_unit"])


class TheLiveCatalogue(unittest.TestCase):
    """What the fill actually achieved, so a regression is visible."""

    def test_no_item_is_left_with_exactly_one_side(self):
        conn = mi._connect()
        try:
            rows = conn.execute(
                "SELECT item_no, name_en, name_ar, unit_en, unit_ar "
                "FROM master_items "
                "WHERE (COALESCE(unit_en,'') = '') <> (COALESCE(unit_ar,'') = '')"
            ).fetchall()
        finally:
            conn.close()
        # Anything left has a unit whose meaning the app does not know. That is
        # allowed, but it must be a SHORT list and each one must really be
        # unrecognised - not a word `UNIT_WORDS` simply forgot.
        for r in rows:
            have = str(r["unit_en"] or r["unit_ar"] or "")
            self.assertEqual(
                mi.unit_class(have), "",
                f"{r['item_no']} has a KNOWN unit {have!r} and still one side "
                f"blank - the fill missed it")
        self.assertLessEqual(len(rows), 10,
                             f"{len(rows)} items are half-filled: "
                             f"{[dict(r) for r in rows][:5]}")

    def test_no_item_has_two_units_that_disagree(self):
        conn = mi._connect()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT item_no, unit_en, unit_ar FROM master_items "
                "WHERE COALESCE(unit_en,'') <> '' AND COALESCE(unit_ar,'') <> ''")]
        finally:
            conn.close()
        bad = [r for r in rows if not mi.units_agree(r["unit_en"], r["unit_ar"])]
        self.assertEqual(bad, [], "an item is stored with two different units")

    def test_no_unit_is_stored_in_the_wrong_script(self):
        """'箱' is not an English unit. Four came off a supplier sheet."""
        conn = mi._connect()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT item_no, unit_en, unit_ar FROM master_items")]
        finally:
            conn.close()
        cjk = [r for r in rows
               if any("一" <= ch <= "鿿"
                      for ch in f"{r['unit_en'] or ''}{r['unit_ar'] or ''}")]
        self.assertEqual(cjk, [], "a Chinese unit is stored as if it were ours")
        wrong = [r for r in rows if mi.is_arabic(r["unit_en"] or "")]
        self.assertEqual(wrong, [], "an Arabic unit is sitting in the English slot")


def tearDownModule():
    _wipe()



# Moved here on 2026-08-20 from tests/master_items_build.test.py, whose other
# tests were about folding the four catalogs into records. The catalogs are
# gone; what a unit MEANS is not.
class UnitMeaning(unittest.TestCase):
    """كيلو and KG are the same unit. Without that, no Arabic item ever joins
    its English twin and the whole fold is a no-op."""

    def test_the_two_languages_agree(self):
        for a, b in (("كيلو", "KG"), ("عدد", "EA"), ("كارتون", "BOX"),
                     ("سيت", "SET"), ("متر", "m"), ("طن", "TON")):
            self.assertTrue(mi.units_agree(a, b), f"{a} == {b}")

    def test_genuinely_different_units_do_not(self):
        for a, b in (("كيلو", "EA"), ("طن", "kilogram"), ("متر", "m3"),
                     ("باكيت", "KG")):
            self.assertFalse(mi.units_agree(a, b), f"{a} != {b}")

    def test_a_blank_unit_contradicts_nothing(self):
        self.assertTrue(mi.units_agree("", "KG"))
        self.assertTrue(mi.units_agree("KG", ""))

    def test_two_unknown_units_must_match_literally(self):
        """Guessing that two unrecognised words mean the same thing silently
        merges two different products."""
        self.assertTrue(mi.units_agree("blorp", "BLORP"))
        self.assertFalse(mi.units_agree("blorp", "zorp"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
