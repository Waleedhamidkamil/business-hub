# -*- coding: utf-8 -*-
"""Persistent dismissal state for the PO save-time Master Data review."""
import os
import sys
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from isolated_client_data import activate  # noqa: E402

activate()
sys.path.insert(0, os.path.dirname(HERE))


class PoCatalogueReviewState(unittest.TestCase):
    def _module(self):
        try:
            from backend import po_catalogue_review
        except ImportError as exc:
            self.fail(f"persistent PO catalogue review storage is missing: {exc}")
        return po_catalogue_review

    def test_marked_rows_survive_a_fresh_read_and_merge_with_later_rows(self):
        store = self._module()
        po_no = "PO-__CATALOGUE-REVIEW__-001"
        first = "ماء بطل\x1f500 مل\x1fسيت"
        later = "مادة جديدة\x1f\x1fعدد"

        self.assertEqual(store.reviewed(po_no), [])
        marked = store.mark_reviewed(po_no, [first, first, ""])
        self.assertTrue(marked.get("ok"), marked)
        self.assertEqual(store.reviewed(po_no), [first])

        marked = store.mark_reviewed(po_no, [later])
        self.assertTrue(marked.get("ok"), marked)
        self.assertEqual(store.reviewed(po_no), [first, later])

    def test_bad_document_numbers_and_non_lists_are_rejected(self):
        store = self._module()
        self.assertFalse(store.mark_reviewed("", ["x"]).get("ok"))
        self.assertFalse(store.mark_reviewed("A-86", "x").get("ok"))
        self.assertEqual(store.reviewed(""), [])

    def test_webview_bridge_round_trips_the_persistent_state(self):
        # Importing the full bridge loads legacy modules which leave temporary
        # SQLite handles for the process finalizer. They are unrelated to this
        # focused round trip and otherwise obscure its result on Python 3.13.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            from backend.api import Api

        po_no = "PO-__CATALOGUE-REVIEW__-BRIDGE"
        key = "ماء بطل\x1f500 مل\x1fسيت"
        api = Api()
        marked = api.po_catalogue_review_mark(po_no, [key])
        self.assertTrue(marked.get("ok"), marked)
        loaded = api.po_catalogue_review_get(po_no)
        self.assertTrue(loaded.get("ok"), loaded)
        self.assertEqual(loaded.get("reviewed"), [key])



class TwoDocumentTypes(unittest.TestCase):
    def setUp(self):
        from backend import po_catalogue_review as module
        self.store = module

    """A purchase order completes the English half; a quotation completes the
    Arabic half (2026-09-10). They must not share a key, or reviewing one would
    silence the other."""

    def test_a_quotation_ruling_does_not_silence_a_purchase_order(self):
        self.store.mark_reviewed("SAME-1", ["chairea"], "qut")
        self.assertEqual(self.store.reviewed("SAME-1", "qut"),
                         ["chairea"])
        self.assertEqual(self.store.reviewed("SAME-1", "po"), [],
                         "the purchase order of that number has been told nothing")

    def test_the_default_is_still_the_purchase_order(self):
        self.store.mark_reviewed("SAME-2", ["deskea"])
        self.assertEqual(self.store.reviewed("SAME-2"), ["deskea"])
        self.assertEqual(self.store.reviewed("SAME-2", "qut"), [],
                         "an unqualified call is the PO, exactly as before")

if __name__ == "__main__":
    unittest.main(verbosity=2)
