# -*- coding: utf-8 -*-
"""One item, one suggestion, in the document's own language (2026-08-16).

Step 3b. A PO is written in Arabic and every other document in English, so a
paired item used to offer BOTH names in the same dropdown - and typing the
English word while writing a PO found nothing useful at all.

Two halves:

  * the item's OTHER-LANGUAGE name is a way IN. Writing an Arabic PO you may
    only know the English word; the whole point of pairing بصل with `Onion` is
    that either one reaches the item. It scores slightly lower than the name
    actually typed, so an exact hit still leads.
  * two results carrying the same `item_id` are the same product, and the one
    whose name is already in this document's language wins - the WHOLE ROW,
    not just the name, so a PO gets كيلو and an invoice gets KG rather than
    the two being mixed.

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
in teardown; the record is rebuilt afterwards.
"""
import sys, os, unittest, sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_items

# The two names must share NO word, or a query matches both by their common
# tokens and the cross-language lookup is never exercised - an earlier version
# of this file passed with that lookup deleted.
EN = "ZZ_TEST_Purpleberry"
AR = "ZZ_TEST_توتةبنفسجية"
LONE = "ZZ_TEST_Solitarygadget"
NAMES = (EN, AR, LONE)
# The distinctive half of each name: the only token that reaches ONE of them.
EN_WORD = "Purpleberry"
AR_WORD = "توتةبنفسجية"
LONE_WORD = "Solitarygadget"


def _wipe():
    with database._connect_shared() as conn:
        for n in NAMES:
            for t in ("items", "quote_items", "dn_items", "inv_items"):
                try:
                    conn.execute(f"DELETE FROM {t} WHERE name = ?", (n,))
                except sqlite3.OperationalError:
                    pass
            conn.execute("DELETE FROM master_items WHERE name_en = ? OR name_ar = ?",
                         (n, n))


def mine(rows):
    return [r for r in rows if str(r["name"]) in NAMES]


class DocumentLanguage(unittest.TestCase):

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)
        database.remember_item(AR, "", "كيلو", "TDC", {"name_en": EN})
        database.remember_dn_item(EN, {"description": "", "unit": "KG",
                                       "name_ar": AR})

    def test_a_PO_offers_the_ARABIC_name_for_an_english_query(self):
        got = mine(database.suggest_items(EN_WORD))
        self.assertTrue(got, "the English word must still find the item")
        self.assertEqual(got[0]["name"], AR,
                         "a PO is written in Arabic - offering the English "
                         "name puts the wrong text in the document")

    def test_a_quotation_offers_the_ENGLISH_name_for_an_arabic_query(self):
        got = mine(database.suggest_quote_items("materials", AR_WORD))
        self.assertTrue(got, "the Arabic word must still find the item")
        self.assertEqual(got[0]["name"], EN)

    def test_the_UNIT_comes_from_the_same_side_as_the_name(self):
        """The whole row is picked, not just the name - otherwise a PO would
        show the Arabic name beside KG."""
        po = mine(database.suggest_items(EN_WORD))[0]
        self.assertEqual(po["unit"], "كيلو")
        q = mine(database.suggest_quote_items("materials", AR_WORD))[0]
        self.assertEqual(q["data"].get("unit"), "KG")

    def test_the_item_is_offered_ONCE_not_once_per_language(self):
        for rows in (database.suggest_items(EN_WORD),
                     database.suggest_items(AR_WORD),
                     database.suggest_quote_items("materials", EN_WORD),
                     database.suggest_quote_items("materials", AR_WORD)):
            self.assertEqual(len(mine(rows)), 1,
                             f"offered twice: {[r['name'] for r in mine(rows)]}")

    def test_an_item_with_only_one_language_is_still_offered(self):
        """Preferring a language must never HIDE an item that has no name in
        it - most of the catalog is one-sided and always will be."""
        database.remember_inv_item("materials", LONE, {"unit": "EA"})
        got = [r["name"] for r in mine(database.suggest_items(LONE_WORD))]
        # Present, not necessarily alone: these sentinels share words, so word
        # matching legitimately offers the paired item as well.
        self.assertIn(LONE, got,
                      "an item with no Arabic name must still be offered in a "
                      "PO - most of the catalog is one-sided and always will be")
        self.assertEqual(got[0], LONE, "…and the exact hit must lead")

    def test_collapsing_the_two_languages_must_not_collapse_VARIANTS(self):
        """The record id is looked up by NAME, and one name covers several
        variants - "Hemp rope" is φ12, φ15 and φ20. Keying the collapse on it
        alone offered ONE rope. The description and the unit's meaning have to
        complete the key: the two languages of one item share both, φ12 and
        φ20 do not.
        """
        for desc in ("12mm", "20mm"):
            database.remember_dn_item(EN, {"description": desc, "unit": "KG",
                                           "name_ar": AR})
        got = [r["data"].get("description")
               for r in mine(database.suggest_quote_items("materials", EN_WORD))]
        self.assertIn("12mm", got, f"a variant went missing: {got}")
        self.assertIn("20mm", got, f"a variant went missing: {got}")

    def test_the_name_actually_typed_still_leads(self):
        """The paired name is a way in, not a promotion. An exact hit on a
        different item must not be pushed below a paired near-miss."""
        rows = database.suggest_quote_items("materials", EN_WORD)
        self.assertEqual(rows[0]["name"], EN)

    def test_no_internal_keys_reach_the_editor(self):
        for r in mine(database.suggest_items(EN_WORD)):
            leaked = [k for k in r if k.startswith("_")]
            self.assertEqual(leaked, [], f"internal keys leaked: {leaked}")


class FillFromMasterData(unittest.TestCase):
    """The same rule for the paste resolver behind "Fill from Master Data".

    Pasting an English list into an Arabic PO used to fill the English name.
    Choosing between the two LANGUAGES of one product is not the kind of guess
    the resolver's "a tie is never a match" rule forbids - the pairing is 1:1
    and both sides are single items, so nothing is decided on the user's
    behalf. Choosing between φ12 and φ20 still is, and still fills nothing.
    """

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)
        database.remember_item(AR, "", "كيلو", "TDC", {"name_en": EN})
        database.remember_dn_item(EN, {"description": "", "unit": "KG",
                                       "name_ar": AR})

    def _resolve(self, name, doc_type):
        from backend import master_data
        return master_data.resolve_items([name], doc_type=doc_type,
                                         sub_type="materials")["results"][0]

    def test_an_english_paste_fills_the_ARABIC_name_in_a_PO(self):
        r = self._resolve(EN, "po")
        self.assertEqual(r["name"], AR)
        self.assertEqual(r["fill"].get("unit"), "كيلو",
                         "the unit must come from the same side as the name")

    def test_an_arabic_paste_fills_the_ENGLISH_name_in_a_quotation(self):
        r = self._resolve(AR, "quote")
        self.assertEqual(r["name"], EN)
        self.assertEqual(r["fill"].get("unit"), "KG")

    def test_a_paste_already_in_the_right_language_is_left_alone(self):
        self.assertEqual(self._resolve(AR, "po")["name"], AR)
        self.assertEqual(self._resolve(EN, "inv")["name"], EN)

    def test_an_unknown_name_is_still_NEW(self):
        r = self._resolve("ZZ_TEST_Nothing Like This", "po")
        self.assertEqual(r["tier"], "new")
        self.assertEqual(r["fill"], {})

    def test_a_name_that_covers_SEVERAL_variants_fills_NOTHING(self):
        """CHANGED 2026-08-19 - the rule got stricter, not looser.

        There is one record per item now and BOTH its names find it, so an
        English name shared by three Arabic variants is simply an ambiguous
        name: three real records answer to it. The resolver's standing rule
        applies - a tie is never a match. It offers them and fills nothing,
        rather than picking one and quietly attaching the wrong unit or price
        to a purchase order.

        (It used to keep the English row, because the English side was a
        separate item of its own. It is not one any more.)"""
        for desc in ("شكل اول", "شكل ثاني"):
            database.remember_item(AR, desc, "كيلو", "TDC", {"name_en": EN})
        r = self._resolve(EN, "po")
        self.assertNotEqual(r["tier"], "exact",
                            "three records answer to that name - it is not exact")
        self.assertEqual(r["fill"], {},
                         "filling one of three variants is exactly the guess "
                         "the resolver forbids")
        self.assertTrue(r.get("suggestions") or r.get("alternatives"),
                        "…but it must still OFFER them, or the paste is a dead end")

    def test_a_name_covering_several_VARIANTS_still_fills_nothing(self):
        """The standing rule survives: silently picking one of several
        plausible items is how a wrong price reaches an issued document."""
        for desc in ("12mm", "20mm"):
            database.remember_dn_item(EN, {"description": desc, "unit": "KG",
                                           "name_ar": AR})
        r = self._resolve(EN, "inv")
        self.assertNotEqual(r["tier"], "exact",
                            "three variants of one name is an ambiguity")
        self.assertEqual(r["fill"], {}, "an ambiguity must fill nothing")


def tearDownModule():
    _wipe()


if __name__ == "__main__":
    unittest.main(verbosity=2)
