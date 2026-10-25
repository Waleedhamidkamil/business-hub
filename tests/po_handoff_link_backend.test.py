# -*- coding: utf-8 -*-
"""Linking a PO to the DN / Invoice made from it (2026-09-05).

No new table, and no new bridge. `po_fulfillment` already records "these PO
lines were delivered / invoiced by that document", `set_po_fulfillment` already
accepts `source_kind` 'dn' and 'inv', and `document_workflow` reads that table
as its only general mechanism. Make DN / Make Invoice writes the same link the
manual PO linker writes - it just does it at creation time.

Two properties matter enough to pin:

* **The indexes are into the SAVED rows.** `savePO` persists `filledRows()`,
  so blank editor rows are dropped and the editor's index is NOT the stored
  index. That is why `po_handoff.js` reads the PO back with `load_po` after the
  save gate instead of using the editor's rows.

* **An unpriced invoice is REFUSED, and that refusal must not lose the link.**
  An invoice with no rates saves as an app-only draft with no PDF, and
  `set_po_fulfillment` declines to link one. The frontend keeps `sourcePo` and
  retries on the next save; the write is ON CONFLICT DO UPDATE, so retrying is
  free. This file proves the refusal, the retry and the idempotence.

LIVE DATABASE - sentinel document numbers only, and every row this file creates
is removed by number in teardown. Never an unqualified DELETE.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database  # noqa: E402

PO = "ZZ-POMK-PO-1"
DN = "ZZ-POMK-DN-1"
INV_DRAFT = "ZZ-POMK-INV-DRAFT"
INV_OK = "ZZ-POMK-INV-OK"

ROWS = [
    {"name": "مفتاح ربط", "unit": "قطعة", "qty": 2, "company": "TDC"},
    {"name": "مسمار", "unit": "علبة", "qty": 5, "company": "TDC"},
    {"name": "ورق طباعة", "unit": "ربطة", "qty": 1, "company": "ابراج البدر"},
]


def _cleanup():
    with database._connect() as conn:
        conn.execute("DELETE FROM po_fulfillment WHERE po_no = ?", (PO,))
        conn.execute("DELETE FROM pos WHERE po_no = ?", (PO,))
        for t, col in (("dns", "dn_no"), ("invoices", "inv_no")):
            for no in (DN, INV_DRAFT, INV_OK):
                try:
                    conn.execute(f"DELETE FROM {t} WHERE {col} = ?", (no,))
                except Exception:
                    pass


class LinkAPoToWhatWasMadeFromIt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _cleanup()
        with database._connect() as conn:
            now = "2026-09-05T00:00:00"
            conn.execute(
                "INSERT INTO pos(po_no, po_date, year, month, pdf_path, rows_json, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (PO, "5/9/2026", 2026, 9, "", json.dumps(ROWS, ensure_ascii=False),
                 now, now))

            # The DN itself has to EXIST. `load_po`'s fulfillment map and
            # `document_workflow` both join the link back to the real document,
            # so a link pointing at a number with no record behind it is
            # reported as nothing at all. In production that ordering is free -
            # the link is written after the save - but it means a test that
            # links to an invented number proves nothing.
            conn.execute(
                "INSERT INTO dns(dn_no, dn_date, year, month, pdf_path, rows_json, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (DN, "5/9/2026", 2026, 9, "C:/x/ZZ-POMK-DN-1.pdf", "[]", now, now))

            # A DRAFT invoice: the row exists with an EMPTY pdf_path, which is
            # exactly how an incomplete-price invoice is stored.
            for no, pdf in ((INV_DRAFT, ""), (INV_OK, "C:/x/ZZ-POMK-INV-OK.pdf")):
                conn.execute(
                    "INSERT INTO invoices(inv_no, inv_date, inv_type, year, month, "
                    "pdf_path, rows_json, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (no, "5/9/2026", "materials", 2026, 9, pdf, "[]", now, now))

    @classmethod
    def tearDownClass(cls):
        _cleanup()

    def setUp(self):
        with database._connect() as conn:
            conn.execute("DELETE FROM po_fulfillment WHERE po_no = ?", (PO,))

    def _marks(self, status_kind):
        with database._connect() as conn:
            return {r["item_index"]: r["source_no"] for r in conn.execute(
                "SELECT item_index, source_no FROM po_fulfillment "
                "WHERE po_no = ? AND status_kind = ?", (PO, status_kind))}

    # ------------------------------------------------------------ DN
    def test_a_dn_marks_exactly_the_lines_it_was_made_from(self):
        """The two client lines, not the internal one - `billableGroups` never
        offers an internal line, so index 2 must not be marked."""
        r = database.set_po_fulfillment(PO, [0, 1], "dn", DN)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self._marks("delivered"), {0: DN, 1: DN})

    def test_relinking_the_same_lines_is_idempotent(self):
        """The frontend retries on the next save when a link was refused, so a
        second identical call must not double up or fail."""
        database.set_po_fulfillment(PO, [0, 1], "dn", DN)
        r = database.set_po_fulfillment(PO, [0, 1], "dn", DN)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self._marks("delivered"), {0: DN, 1: DN})

    def test_the_indexes_are_into_the_stored_rows(self):
        """Marking index 1 must mean مسمار - the second SAVED row. If the
        frontend ever passes an editor index instead, this is the property that
        breaks, silently marking the wrong goods delivered."""
        database.set_po_fulfillment(PO, [1], "dn", DN)
        rec = database.load_po(PO)
        marked = list(self._marks("delivered"))
        self.assertEqual(rec["rows"][marked[0]]["name"], "مسمار")

    def test_the_po_reports_the_link_back_for_the_picker(self):
        """What the picker greys out and refuses to pre-tick.

        NOTE the two-step: `database.load_po` does NOT carry fulfillment -
        `api.load_po` bolts it on with `po_fulfillment_detail`. A test that
        reads the database function directly proves nothing about what the
        frontend sees.
        """
        database.set_po_fulfillment(PO, [0], "dn", DN)
        detail = database.po_fulfillment_detail(PO)
        got = detail["delivered"].get("0") or detail["delivered"].get(0)
        self.assertTrue(got, f"expected index 0 delivered: {detail['delivered']}")
        self.assertEqual(got.get("source_no"), DN)

    def test_an_invoice_link_is_reported_under_invoiced_not_paid(self):
        """The trap the frontend has to know about: an invoice link is STORED
        with `status_kind='paid'` (legacy), but `po_fulfillment_detail` exposes
        it under `invoiced`, reserving `paid` for invoices actually marked paid.
        `po_handoff.js` reads `.invoiced`; reading `.paid` would show every
        unpaid-but-invoiced line as still available."""
        database.set_po_fulfillment(PO, [0], "inv", INV_OK)
        detail = database.po_fulfillment_detail(PO)
        self.assertTrue(detail["invoiced"].get("0") or detail["invoiced"].get(0),
                        "an invoiced line must be reported under `invoiced`")
        self.assertFalse(detail["paid"].get("0") or detail["paid"].get(0),
                         "...and NOT under `paid` - nothing has been paid")

    def test_a_draft_invoices_link_is_hidden_from_the_picker(self):
        """Consistent with the refusal above: `po_fulfillment_detail` filters
        out invoice links whose invoice has no PDF, so a line whose only claim
        is an unpriced draft is still offered."""
        with database._connect() as conn:      # force the link past the guard
            conn.execute(
                "INSERT INTO po_fulfillment(po_no, item_index, status_kind, "
                "source_kind, source_no, marked_at) VALUES(?,?,?,?,?,?)",
                (PO, 0, "paid", "inv", INV_DRAFT, "2026-09-05T00:00:00"))
        detail = database.po_fulfillment_detail(PO)
        self.assertFalse(detail["invoiced"].get("0") or detail["invoiced"].get(0),
                         "a draft invoice must not make a line look invoiced")

    # ------------------------------------------------------------ invoice
    def test_an_unpriced_invoice_is_refused(self):
        """An invoice saved as a draft has no PDF and cannot be linked. The
        frontend must KEEP its sourcePo when this happens - clearing it would
        orphan the link with nothing to say so."""
        r = database.set_po_fulfillment(PO, [0, 1], "inv", INV_DRAFT)
        self.assertFalse(r.get("ok"), r)
        self.assertIn("pricing", str(r.get("error", "")).lower())
        self.assertEqual(self._marks("paid"), {}, "and nothing is written")

    def test_the_retry_succeeds_once_the_invoice_has_a_pdf(self):
        """The next save, after prices are filled in."""
        database.set_po_fulfillment(PO, [0, 1], "inv", INV_DRAFT)
        r = database.set_po_fulfillment(PO, [0, 1], "inv", INV_OK)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self._marks("paid"), {0: INV_OK, 1: INV_OK})

    def test_a_dn_link_and_an_invoice_link_coexist(self):
        """They are different status_kinds, so invoicing does not un-deliver."""
        database.set_po_fulfillment(PO, [0, 1], "dn", DN)
        database.set_po_fulfillment(PO, [0, 1], "inv", INV_OK)
        self.assertEqual(self._marks("delivered"), {0: DN, 1: DN})
        self.assertEqual(self._marks("paid"), {0: INV_OK, 1: INV_OK})

    # ------------------------------------------------------------ workflow
    def test_the_workflow_view_shows_the_document_in_the_po_lane(self):
        """The user-visible payoff: Workflow on the PO lists what came out of
        it, with no extra wiring."""
        database.set_po_fulfillment(PO, [0, 1], "dn", DN)
        wf = database.document_workflow("po", PO)
        blob = json.dumps(wf, ensure_ascii=False, default=str)
        self.assertIn(DN, blob,
                      "the DN made from this PO must appear in its workflow")

    def test_a_bad_source_kind_is_refused_rather_than_guessed(self):
        r = database.set_po_fulfillment(PO, [0], "quotation", "X")
        self.assertFalse(r.get("ok"), r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
