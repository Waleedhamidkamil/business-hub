"""Which saved documents hold an item (2026-08-14).

The Master Data panel listed "Stored rows", but those are CATALOGS - "Quotation
Materials", "Delivery Note" - not documents. The panel could say an item was
remembered without ever saying where it came from.

`item_documents` answers that, and is deliberately separate from `cost_targets`:
that one exists to price a change and refuses outright when the item has no
usable cost, which is most of this catalog. "Where have I used this?" has to
answer for any item, costed or not - which is exactly what this file pins.

⚠ LIVE DATABASE. Sentinel document numbers and item names only, far-future
month, every row deleted by exact number in tearDown. No unqualified DELETE.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import database, master_data, profile_manager  # noqa: E402

ITEM = "__USEDIN_TEST__ Widget"
QUOTE_NO = "QUT-__USEDIN__-001"
QUOTE_NO_OLD = "QUT-__USEDIN__-000"
PO_NO = "PO-__USEDIN__-001"


def _profile_db():
    return profile_manager.profile_db_path(profile_manager.active_profile_id())


def _cleanup():
    with sqlite3.connect(_profile_db()) as c:
        c.execute("DELETE FROM quotes WHERE quote_no IN (?, ?)",
                  (QUOTE_NO, QUOTE_NO_OLD))
        c.execute("DELETE FROM pos WHERE po_no = ?", (PO_NO,))
        c.commit()
    with sqlite3.connect(profile_manager.shared_db_path()) as c:
        for t in ("items", "quote_items"):
            try:
                c.execute(f"DELETE FROM {t} WHERE name = ?", (ITEM,))
            except sqlite3.OperationalError:
                pass
        # …and the item RECORD the save mirrored into, or the sentinel
        # outlives the catalog rows on the live Master Data screen. Matched by
        # INSTR rather than LIKE: the sentinel is full of underscores, which
        # LIKE treats as wildcards, and escaping them is how the last cleanup
        # of this kind silently missed its own rows.
        c.execute("DELETE FROM master_items "
                  "WHERE INSTR(COALESCE(name_en,''), '__USEDIN_TEST__') > 0"
                  "   OR INSTR(COALESCE(name_ar,''), '__USEDIN_TEST__') > 0")
        c.commit()


def tearDownModule():
    _cleanup()


class ItemDocuments(unittest.TestCase):

    def setUp(self):
        _cleanup()
        database.save_quote_record(
            quote_no=QUOTE_NO, quote_date="01/01/2099", quote_type="materials",
            year=2099, month=1, client_name="__USEDIN__ Client",
            rfq_no="", client_address="", pdf_path="x.pdf", terms=[],
            rows=[{"item": ITEM, "description": "Blue", "unit": "PACK",
                   "qty": "4", "rate": "2.5"},
                  {"item": ITEM, "description": "Red", "unit": "PACK",
                   "qty": "1", "rate": "2.5"},
                  {"item": "__USEDIN_TEST__ Other", "qty": "1", "rate": "1"}],
        )
        # A SECOND quotation, an earlier month - without it every fixture
        # sits in 2099-01 and the newest-first order cannot be observed at all
        # (the first version of this test passed against a reversed sort).
        database.save_quote_record(
            quote_no=QUOTE_NO_OLD, quote_date="01/01/2098", quote_type="materials",
            year=2098, month=1, client_name="__USEDIN__ Client",
            rfq_no="", client_address="", pdf_path="x.pdf", terms=[],
            rows=[{"item": ITEM, "unit": "PACK", "qty": "2", "rate": "2"}],
        )
        database.save_po_record(
            po_no=PO_NO, po_date="01/01/2099", year=2099, month=1,
            pdf_path="x.pdf",
            rows=[{"name": ITEM, "description": "", "unit": "PACK",
                   "qty": "9", "company": "__USEDIN__ Co"}],
        )

    def tearDown(self):
        _cleanup()

    def _docs(self):
        r = master_data.item_documents(ITEM)
        self.assertTrue(r.get("ok"), r.get("error"))
        return {d["no"]: d for d in r["documents"]}

    def test_it_finds_every_document_type_holding_the_item(self):
        docs = self._docs()
        self.assertIn(QUOTE_NO, docs)
        self.assertIn(PO_NO, docs)
        self.assertEqual(docs[QUOTE_NO]["kind"], "quote")
        self.assertEqual(docs[PO_NO]["kind"], "po")
        self.assertEqual(docs[QUOTE_NO]["kind_label"], "Quotation")

    def test_it_works_for_an_item_with_NO_cost(self):
        """The whole reason this is not `cost_targets`, which refuses an
        uncosted item outright - and most of the catalog is uncosted."""
        self.assertFalse(master_data.cost_targets(ITEM).get("ok"),
                         "fixture must be uncosted for this test to mean anything")
        self.assertTrue(master_data.item_documents(ITEM).get("ok"))

    def test_it_counts_every_line_not_just_the_first(self):
        """The item appears twice in that quotation, as Blue and as Red."""
        d = self._docs()[QUOTE_NO]
        self.assertEqual(d["line_count"], 2)
        self.assertEqual({l["description"] for l in d["lines"]}, {"Blue", "Red"})

    def test_a_line_carries_what_the_popup_shows(self):
        line = next(l for l in self._docs()[QUOTE_NO]["lines"]
                    if l["description"] == "Blue")
        self.assertEqual(line["unit"], "PACK")
        self.assertEqual(line["qty"], "4")
        self.assertEqual(line["rate"], "2.5")

    def test_other_items_in_the_same_document_are_not_reported(self):
        for d in self._docs().values():
            for line in d["lines"]:
                self.assertNotEqual(line.get("description"), "__USEDIN_TEST__ Other")

    def test_the_client_comes_through_for_a_quotation(self):
        self.assertEqual(self._docs()[QUOTE_NO]["client"], "__USEDIN__ Client")

    def test_a_PO_has_no_client_and_does_not_crash(self):
        """`pos` has no client_name column - the query must not select one."""
        self.assertEqual(self._docs()[PO_NO]["client"], "")

    def test_newest_first(self):
        r = master_data.item_documents(ITEM)
        keys = [(int(d["year"] or 0), int(d["month"] or 0))
                for d in r["documents"]]
        self.assertEqual(keys, sorted(keys, reverse=True),
                         "\"where did I last use this\" is the usual question")
        self.assertGreater(len(set(keys)), 1,
                           "the fixture must span more than one month or this "
                           "test cannot see the order at all")
        self.assertEqual(r["documents"][-1]["no"], QUOTE_NO_OLD,
                         "the oldest document must be last")

    def test_an_unknown_item_is_refused_not_invented(self):
        r = master_data.item_documents("__NO_SUCH_ITEM_ANYWHERE__")
        self.assertFalse(r.get("ok"))

    def test_the_result_crosses_the_bridge(self):
        """A self-referential result would surface as "maximum recursion depth
        exceeded" - see image_search's `_better`."""
        json.dumps(master_data.item_documents(ITEM))


if __name__ == "__main__":
    unittest.main(verbosity=2)
