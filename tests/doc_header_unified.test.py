# -*- coding: utf-8 -*-
"""One header layout for every English document type (2026-10-02).

Waleed, on Eshraqat invoice IV-0005: "why is the address not below the linked
quotation? ... the pdf form layout and design should be united between the
types." Each generator built its own header list, so quotation, delivery note
and invoice put the same fields in three different places, and the invoice
alone spread Client and Address over full-width rows with a different indent.

Eshraqat now builds every header in ONE function, `eshraqat_render.meta_rows`,
laid out on the two-column grid as

    <Document> No.        | Date
    LPO No. / RFQ No.     | Linked Quotation / Attention
    Client                | Address

Abraj keeps its own centred-list design but uses one order and one spelling on
all three: Date, <Document> No., <reference>, Client Name, Client Address.

The checks are on what is RENDERED where that matters (the invoice, the case he
reported), and on the source where the rule is "build it in one place".
"""
import importlib
import re
import shutil
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import eshraqat_render  # noqa: E402

DOC = {"quote_no": "QT-0015", "dn_no": "DN-0002", "inv_no": "IV-0006",
       "date": "1/10/2026", "rfq_no": "RFQ-77", "lpo_no": "CSSP-OPEN-0006",
       "source_quote_no": "QT-0014", "client_name": "Hyundai Engineering",
       "client_address": "CSSP"}


class EshraqatMetaRows(unittest.TestCase):
    def rows(self, kind):
        return eshraqat_render.meta_rows(kind, DOC)

    def test_address_sits_under_the_linked_quotation(self):
        # The grid fills left, right, left, right... so index 3 is the right
        # column of row 2 and index 5 the right column of row 3 - directly
        # beneath it. This is the exact thing he asked for.
        for kind in ("dn", "inv"):
            labels = [r["label"] for r in self.rows(kind)]
            self.assertEqual(labels[3], "Linked Quotation", kind)
            self.assertEqual(labels[5], "Address", kind)

    def test_every_type_has_the_same_shape(self):
        shapes = {}
        for kind in ("quote", "dn", "inv"):
            rows = self.rows(kind)
            self.assertEqual(len(rows), 6, f"{kind}: three rows of two")
            self.assertFalse(any(r.get("wide") for r in rows),
                             f"{kind}: a full-width row breaks the two-column "
                             "grid - that is what pushed IV-0005's address out "
                             "from under Linked Quotation")
            labels = [r["label"] for r in rows]
            shapes[kind] = (labels[1], labels[4], labels[5])
            self.assertTrue(labels[0].endswith(" No."), f"{kind}: number first")
        self.assertEqual(len(set(shapes.values())), 1,
                         f"Date / Client / Address must sit in the same place on "
                         f"every type, got {shapes}")

    def test_values_come_from_the_document(self):
        inv = {r["label"]: r["value"] for r in self.rows("inv")}
        self.assertEqual(inv["Invoice No."], "IV-0006")
        self.assertEqual(inv["LPO No."], "CSSP-OPEN-0006")
        self.assertEqual(inv["Linked Quotation"], "QT-0014")
        self.assertEqual(inv["Client"], "Hyundai Engineering")
        self.assertEqual(inv["Address"], "CSSP")
        quote = {r["label"]: r["value"] for r in self.rows("quote")}
        self.assertEqual(quote["RFQ No."], "RFQ-77")
        self.assertEqual(quote["Attention"], "Admin Manager")

    def test_one_label_per_field(self):
        # "Source Quotation" on one type and "Linked Quotation" on another,
        # "Client Address" beside "Address" - the drift this replaced.
        all_labels = {r["label"] for k in ("quote", "dn", "inv") for r in self.rows(k)}
        for retired in ("Source Quotation", "Client Address"):
            self.assertNotIn(retired, all_labels)

    def test_every_generator_builds_its_header_in_the_one_place(self):
        for name, kind in (("quote_generator", "quote"), ("dn_generator", "dn"),
                           ("inv_generator", "inv")):
            src = (APP_ROOT / "backend" / f"{name}.py").read_text(encoding="utf-8")
            self.assertRegex(src, r'meta_rows\("%s",' % kind,
                             f"{name} must ask eshraqat_render.meta_rows for its header")
            self.assertNotRegex(src, r'\{"label":\s*"Date"',
                                f"{name} builds its own header list again - the "
                                "three types will drift apart")


class EshraqatInvoiceRendered(unittest.TestCase):
    """The reported case, on the rendered HTML, under a throwaway profile."""

    def test_invoice_header_order_on_the_page(self):
        from backend import profile_manager
        from backend.profile_manager import ProfileManager
        work = APP_ROOT / ".test-work" / "doc-header-unified"
        shutil.rmtree(work, ignore_errors=True)
        original = profile_manager._MANAGER
        try:
            mgr = ProfileManager(work / "data", APP_ROOT / "static")
            profile_manager._MANAGER = mgr
            mgr.create_registry("eshraqat")
            mgr.activate("eshraqat")
            mgr.ensure_seed_assets("eshraqat")
            from backend import brand_assets, config, signing, inv_generator
            for m in (config, brand_assets, eshraqat_render, signing, inv_generator):
                importlib.reload(m)
            html = inv_generator.render_inv_html(dict(
                DOC, inv_type="manpower", payment_bank_key="cash",
                withholding_included=True, signing_overlay={},
                items=[{"item": "Common laborer", "unit": "Per Day",
                        "qty": "26", "rate": "24"}]))
        finally:
            profile_manager._MANAGER = original
            shutil.rmtree(work, ignore_errors=True)
        meta = html[html.index('<div class="esh-meta">'):]
        meta = meta[:meta.index("</div>\n</div>") if "</div>\n</div>" in meta else 4000]
        labels = re.findall(r'class="esh-meta-label">([^<]*)<', meta)
        self.assertEqual(labels[:6], ["Invoice No.", "Date", "LPO No.",
                                      "Linked Quotation", "Client", "Address"])
        self.assertNotIn("esh-meta-wide", meta)


class AbrajOneOrder(unittest.TestCase):
    def test_same_order_and_spelling_on_all_three(self):
        want_tail = ["Client Name", "Client Address"]
        orders = {}
        for name, prefix in (("quote_page.html", "q"), ("dn_page.html", "d"),
                             ("inv_page.html", "i")):
            src = (APP_ROOT / "templates" / name).read_text(encoding="utf-8")
            labels = re.findall(r'class="%s-lbl">([^<]*)<' % prefix, src)[:5]
            orders[name] = labels
            self.assertEqual(labels[0], "Date", name)
            self.assertTrue(labels[1].endswith(" No."), f"{name}: {labels[1]!r}")
            self.assertTrue(labels[2].endswith(" No."), f"{name}: {labels[2]!r}")
            self.assertEqual(labels[3:], want_tail, f"{name}: {labels}")
            for bad in ("NO.", "D.N"):
                self.assertFalse(any(bad in l for l in labels), f"{name}: {labels}")
        self.assertEqual(len(orders), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
