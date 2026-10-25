# -*- coding: utf-8 -*-
"""PO page breaks come from MEASURED rows, and every page is checked (2026-10-11).

Waleed, on A-129: "I just can't believe we didn't find a fix for that after
everything we have done."  A-129 has eight lines, seven with a picture. Rows 1-7
ended at 167mm on page 1, the stamp starts at 241mm, and row 8 was still pushed
onto a page of its own with the signature.

The cause was the same KIND of mistake as A-84, A-88 and A-110 before it: the
planner GUESSED each row's height. A row with a picture was booked at a flat
22mm; his signs are wide and short and print at 10-13mm. Seven of them were
over-booked by ~70mm, the sum crossed the one-page budget, and the planner split
a table that filled barely half the page. Each earlier fix corrected one guess
(newlines, glyph width, column width, auto-fit geometry); the next document
found the next guess.

So the planner no longer guesses. It lays the PO out in the same Chrome that
prints it, reads every row's real height, the header, and the signature/stamp
area, and plans pages on those numbers. Then it checks the finished layout
before printing.

This file checks the PRINTED PDF, independently of the planner, with PyMuPDF:
  * where the table really ends on every page (its vertical rules),
  * where the page number and the signature/stamp really start,
  * how tall the first row of the next page really is.
From those it asserts the two things Waleed actually sees:
  1. nothing overlaps - the table stops above the page number on every page and
     above the stamp and signature on the last one;
  2. nothing is wasted - a page only ends when the next row genuinely did not
     fit on it.
A planner bug in either direction fails here, whatever caused it.
"""
from __future__ import annotations

import base64
import collections
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import config, pdf_engine, pdf_generator, signing  # noqa: E402

try:
    import fitz  # PyMuPDF
    from PIL import Image
except ImportError:  # pragma: no cover
    fitz = None

MM = 72 / 25.4
DOC = "ZZ-PO-MEASURE"


def _png(w, h, rgb):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), rgb).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# The three shapes A-129 actually carries (pixel sizes read from its PDF).
TALL = _png(393, 580, (200, 40, 40))       # the fire extinguisher
SIGN = _png(900, 540, (40, 90, 160))       # 45 x 60 cm sign photo
BANNER = _png(899, 300, (40, 140, 90))     # the 240 x 80 cm welcome sign

A129_WIDTHS = {"name": 20.0, "description": 21.0, "unit": 9.0, "qty": 7.0,
               "company": 16.0, "image": 11.0}


def a129_rows():
    r = lambda name, desc, qty, img: {"name": name, "description": desc, "unit": "عدد",
                                       "qty": qty, "company": "ابراج البدر", "image": img}
    return [
        r("طفاية حريق مع بوكس", "", "4", TALL),
        r("كيلون باب", "ذهبي حديد حجم كبير", "2", ""),
        r("لافتة رجوع عكسي", "45 * 60 سم", "10", SIGN),
        r("لافتة دخول", "50 * 30 سم", "1", SIGN),
        r("لافتة خروج", "50 * 30 سم", "1", SIGN),
        r("لافتة موقف المركبات الخفيفة", "45 * 60 سم", "1", SIGN),
        r("لافتة موقف المعدات الثقيلة", "45 * 60 سم", "1", SIGN),
        r("لافتة ترحيب", "240 * 80 سم", "1", BANNER),
    ]


LONG = ("توريد وتركيب مواد حسب المواصفات المطلوبة مع النقل إلى الموقع "
        "والتحميل والتنزيل وكل ما يلزم لإنجاز العمل بشكل كامل")


def mixed_rows(n):
    """Every shape a real PO mixes: short text, wrapping text, typed line
    breaks, wide pictures, tall pictures."""
    out = []
    for i in range(n):
        kind = i % 5
        out.append({
            "name": f"مادة رقم {i + 1}" + ("\nسطر ثان" if kind == 2 else ""),
            "description": LONG if kind in (1, 3) else "قياس 30 * 50",
            "unit": "عدد", "qty": str(i + 1), "company": "ابراج البدر",
            "image": TALL if kind == 3 else (SIGN if kind == 4 else ""),
        })
    return out


def _asset_sizes():
    """Pixel sizes of the stamp and signature images, to tell them apart from
    the pictures in the table."""
    sizes = set()
    try:
        assets = signing.get_assets()
    except Exception:
        return sizes
    uris = [assets.get("stamp")] + list(assets.get("signatures") or [])
    for u in uris:
        if isinstance(u, dict):
            u = u.get("data_uri") or u.get("uri")
        if isinstance(u, str) and u.startswith("data:image"):
            raw = base64.b64decode(u.split(",", 1)[1])
            with Image.open(io.BytesIO(raw)) as im:
                sizes.add(im.size)
    return sizes


def geometry(pdf_path):
    """Per page, in mm from the top of the sheet: where the table starts and
    ends, every full-width rule inside it, the page number, and the top of the
    signature block (its line and any stamp/signature image)."""
    stamp_sizes = _asset_sizes()
    pages = []
    with fitz.open(pdf_path) as doc:
        for pg in doc:
            width = pg.rect.width
            rules = collections.defaultdict(float)
            verticals = []
            for drawing in pg.get_drawings():
                for item in drawing["items"]:
                    if item[0] == "re":
                        r = fitz.Rect(item[1])
                    elif item[0] == "l":
                        r = fitz.Rect(item[1], item[2])
                        r.normalize()
                    else:
                        continue
                    if r.height <= 2.5 and r.width > 3:
                        rules[round((r.y0 + r.y1) / 2, 1)] += r.width
                    elif r.width <= 2.5 and r.height > 3:
                        verticals.append(r)
            top = min(v.y0 for v in verticals) / MM
            bottom = max(v.y1 for v in verticals) / MM
            full = sorted(y / MM for y, w in rules.items() if w >= 0.6 * width
                          and top - 0.5 <= y / MM <= bottom + 0.5)
            short = [y / MM for y, w in rules.items() if w < 0.6 * width and y / MM > bottom]
            stamps = [i["bbox"][1] / MM for i in pg.get_image_info()
                      if (i["width"], i["height"]) in stamp_sizes]
            sig_top = min(short + stamps) if (short or stamps) else None
            pagenum = [r.y0 / MM for r in pg.search_for("Page ")]
            footer = pg.get_text()
            pages.append({"top": top, "bottom": bottom, "rules": full,
                          "sig_top": sig_top, "pagenum_top": min(pagenum) if pagenum else None,
                          "text": footer})
    return pages


class MeasuredPoPagination(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if fitz is None:
            raise unittest.SkipTest("PyMuPDF / Pillow not installed")
        try:
            pdf_engine.find_browser()
        except pdf_engine.PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))
        cls.work = Path(tempfile.mkdtemp(prefix="po_measured_"))
        # A real save signs and stamps (auto-sign); the stamp is what the table
        # must clear, so render the way a save does.
        cls.overlay = signing.overlay_for_save("po", None, force=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def render(self, name, rows, layout=None, col_widths=None):
        out = self.work / f"{name}.pdf"
        pdf_generator.render_pdf(DOC, "11/10/2026", rows, out,
                                 signing_overlay=self.overlay,
                                 col_widths=col_widths, layout=layout)
        return geometry(out)

    def assert_sound(self, name, pages):
        n = len(pages)
        for i, p in enumerate(pages, start=1):
            self.assertIn(f"Page {i} of {n}", p["text"],
                          f"{name}: page {i} must say 'Page {i} of {n}' - the "
                          "planner and the printed PDF disagree on the page count")
            if p["pagenum_top"] is not None:
                self.assertLessEqual(
                    p["bottom"], p["pagenum_top"] - 0.5,
                    f"{name}: page {i}'s table runs into the page number "
                    f"({p['bottom']:.1f}mm vs {p['pagenum_top']:.1f}mm)")
        last = pages[-1]
        self.assertIsNotNone(last["sig_top"], f"{name}: no signature found on the last page")
        # The last page's limit: the signature/stamp as printed, or the
        # signature reserve SETTING (room for a hand stamp), whichever is higher.
        reserve_top = 10.0 + 277.0 - float(config.PO_SIGNATURE_MM)
        last_limit = min(last["sig_top"], reserve_top)
        self.assertLessEqual(
            last["bottom"], last_limit - 0.5,
            f"{name}: the table on the last page runs into the signature/stamp "
            f"or its reserved space ({last['bottom']:.1f}mm vs {last_limit:.1f}mm)")
        # Nothing wasted. `rules` on a page are: table top, thead bottom, then
        # the bottom of each row. 3mm covers the planner's safety margin.
        SLACK = 3.0
        if n > 1:
            rows_total = sum(p["bottom"] - p["rules"][1] for p in pages)
            self.assertGreater(
                pages[0]["rules"][1] + rows_total, last_limit - 0.5 - SLACK,
                f"{name}: all the rows ({rows_total:.1f}mm) and the signature fit on "
                f"page 1, but it printed on {n} pages")
        for i in range(n - 1):
            here, nxt = pages[i], pages[i + 1]
            next_rows = len(nxt["rules"]) - 2
            if i + 1 == n - 1 and next_rows <= 2:
                continue        # the signature page keeps two lines with it, on purpose
            first_row = nxt["rules"][2] - nxt["rules"][1]
            limit = here["pagenum_top"] - 0.5
            self.assertGreater(
                here["bottom"] + first_row, limit - SLACK,
                f"{name}: page {i + 1} ended at {here['bottom']:.1f}mm, but the next "
                f"row ({first_row:.1f}mm) fitted under {limit:.1f}mm - space was "
                "wasted and the page break was avoidable")
        if n > 1:
            last_rows = len(last["rules"]) - 2
            total_rows = sum(len(p["rules"]) - 2 for p in pages)
            self.assertGreaterEqual(
                last_rows, min(2, total_rows),
                f"{name}: the signature page carries a single line on its own")

    # ------------------------------------------------------------------ #
    def test_a129_prints_on_one_page(self):
        pages = self.render("a129", a129_rows(), layout={"autofit_rows": True},
                            col_widths=A129_WIDTHS)
        self.assertEqual(
            len(pages), 1,
            "A-129's eight lines fill about half a page; they must print on ONE page "
            "with the signature, not 7 + 1")
        self.assert_sound("A-129", pages)

    def test_a129_fixed_row_height_too(self):
        pages = self.render("a129-fixed", a129_rows(), layout={"autofit_rows": False},
                            col_widths=A129_WIDTHS)
        self.assertEqual(len(pages), 1)
        self.assert_sound("A-129 fixed rows", pages)

    def test_mixed_documents_never_overlap_and_never_waste(self):
        for n, autofit in ((14, True), (26, True), (40, False), (60, True)):
            with self.subTest(rows=n, autofit=autofit):
                pages = self.render(f"mixed-{n}-{autofit}", mixed_rows(n),
                                    layout={"autofit_rows": autofit})
                self.assert_sound(f"{n} mixed rows (autofit={autofit})", pages)

    def test_measuring_never_quietly_falls_back(self):
        """Every plan must come from a MEASUREMENT. The first version measured on
        an animation frame that --dump-dom sometimes never waited for: 4 of 6
        plans of an 80-line PO fell back to the estimate without a word, and the
        PDFs still looked fine most of the time. Single shots - no retry to hide
        behind - on a text-only PO and a mixed one."""
        from backend import pdf_measure
        text_only = [{"name": f"Item {i}", "description": "Steel pipe assembly",
                      "unit": "pcs", "qty": "2", "company": "TDC"} for i in range(80)]
        for label, rows in (("80 text lines", text_only), ("30 mixed lines", mixed_rows(30))):
            probe = pdf_generator._po_render_pages(
                pdf_generator._po_pages_from_chunks(rows, DOC, [list(range(len(rows)))]),
                DOC, "11/10/2026", rows, True)
            for i in range(8):
                got = pdf_measure._measure_once(probe, pdf_generator._PO_MEASURE_SPEC, 45)
                self.assertIsNotNone(got, f"{label}: measurement {i + 1} of 8 came back empty")
                self.assertEqual(len(got[0]["rows"]), len(rows))

    def test_tall_pictures_only(self):
        rows = [{"name": f"صورة {i}", "description": "", "unit": "عدد", "qty": "1",
                 "company": "ابراج البدر", "image": TALL} for i in range(18)]
        pages = self.render("tall", rows, layout={"autofit_rows": True})
        self.assert_sound("18 tall pictures", pages)


class PackerRules(unittest.TestCase):
    """The page-packing rules on their own, with exact numbers and no browser.

    The rendered tests above cannot see a rule that the final layout check
    later rescues by re-planning with more room - the PDF still comes out
    right, just less full. These pin each rule directly."""

    def m(self, rows, usable=100.0, header=0.0, closeout=40.0, pagenum=0.0, thead=10.0):
        return {"usable": usable, "header": header, "cont_top": 0.0, "closeout": closeout,
                "pagenum": pagenum, "thead": thead, "rows": rows, "safety": 0.0}

    def test_one_page_when_everything_fits_with_the_signature(self):
        self.assertEqual(pdf_generator._pack_po(self.m([10.0] * 5)), [[0, 1, 2, 3, 4]])

    def test_the_page_number_is_kept_clear(self):
        # 100mm page, 6mm for the page number: thead + 8 rows = 90 fits, 9 does not.
        chunks = pdf_generator._pack_po(self.m([10.0] * 12, pagenum=6.0, closeout=6.0))
        self.assertEqual(len(chunks[0]), 8, "the first page must stop before the page number")

    def test_the_page_before_the_signature_is_refilled(self):
        # All 9 rows fit a non-last page (100mm) but not with the 40mm signature.
        # Spilling alone gives 5 + 4 and leaves page 1 30mm short; refilled it is
        # 7 + 2: page 1 full, two lines kept with the signature.
        self.assertEqual([len(c) for c in pdf_generator._pack_po(self.m([10.0] * 9))], [7, 2])

    def test_the_signature_page_never_holds_one_line_alone(self):
        chunks = pdf_generator._pack_po(self.m([10.0] * 8 + [10.0], closeout=45.0))
        self.assertGreaterEqual(len(chunks[-1]), 2)

    def test_the_signature_reserve_setting_is_a_floor(self):
        """A small measured signature must not shrink the reserve below the
        po_signature_mm setting - it keeps room for a hand stamp."""
        rows = [{"name": f"r{i}", "description": "", "unit": "", "qty": "", "company": ""}
                for i in range(20)]
        floor = pdf_generator._po_geometry()[3]
        flow, header, thead, row_h = 277.0, 50.0, 12.0, 9.0
        real = pdf_generator.pdf_measure.measure

        def fake(html, spec, timeout=45.0):
            n = html.count('<section class="po-page') or html.count('<section class="esh-page')
            if n == 1 and "Page 1 of 1" in html:
                return [{"flow": flow, "header": header, "thead": thead,
                         "rows": [row_h] * len(rows), "closeout": 10.0, "pagenum": 0.0,
                         "grown": 0, "table_bottom": 0, "closeout_top": None,
                         "pagenum_top": None}]
            return [{"flow": flow, "grown": 0, "table_bottom": 0, "closeout_top": None,
                     "pagenum_top": None} for _ in range(n)]
        pdf_generator.pdf_measure.measure = fake
        try:
            pages, _ = pdf_generator.plan_po(rows, "ZZ-FLOOR", "1/1/2026")
        finally:
            pdf_generator.pdf_measure.measure = real
        last_h = thead + row_h * len(pages[-1]["rows"]) + (header if len(pages) == 1 else 0)
        self.assertLessEqual(last_h, flow - floor,
                             f"the last page's table reaches into the {floor:.0f}mm "
                             "signature reserve because the measured stamp was small")


class MeasuredPoPaginationEshraqat(unittest.TestCase):
    """The same planner under the Eshraqat PO skin: its own header, an in-flow
    closing zone, and the page number inside the bottom padding. A table that
    over-runs there pushes the closing zone onto an extra sheet, so the check
    is: the sheets printed == the pages planned, and A-129 still fits one."""

    @classmethod
    def setUpClass(cls):
        if fitz is None:
            raise unittest.SkipTest("PyMuPDF / Pillow not installed")
        try:
            pdf_engine.find_browser()
        except pdf_engine.PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))
        import importlib
        from backend import profile_manager
        from backend.profile_manager import ProfileManager
        cls.work = Path(tempfile.mkdtemp(prefix="po_measured_esh_"))
        cls._saved_manager = profile_manager._MANAGER
        try:
            mgr = ProfileManager(cls.work / "data", APP_ROOT / "static")
            profile_manager._MANAGER = mgr
            mgr.create_registry("eshraqat")
            mgr.activate("eshraqat")
            mgr.ensure_seed_assets("eshraqat")
            mgr.assets_dir("eshraqat").mkdir(parents=True, exist_ok=True)
            mgr.signatures_dir("eshraqat").mkdir(parents=True, exist_ok=True)
            from backend import brand_assets, eshraqat_render
            for m in (config, brand_assets, eshraqat_render, signing, pdf_generator):
                importlib.reload(m)
            assert config.ACTIVE_PROFILE == "eshraqat", config.ACTIVE_PROFILE
        except Exception as exc:            # pragma: no cover
            profile_manager._MANAGER = cls._saved_manager
            raise unittest.SkipTest(f"could not activate an eshraqat profile: {exc}")

    @classmethod
    def tearDownClass(cls):
        import importlib
        from backend import profile_manager
        profile_manager._MANAGER = cls._saved_manager
        try:
            from backend import brand_assets, eshraqat_render
            for m in (config, brand_assets, eshraqat_render, signing, pdf_generator):
                importlib.reload(m)
        except Exception:
            pass
        shutil.rmtree(cls.work, ignore_errors=True)

    def render(self, name, rows, layout=None, col_widths=None):
        out = self.work / f"{name}.pdf"
        pdf_generator.render_pdf("PO-0099", "11/10/2026", rows, out,
                                 col_widths=col_widths, layout=layout)
        with fitz.open(out) as pdf:
            drawn = pdf.page_count
        planned = pdf_generator.po_page_count(rows, "PO-0099", layout=layout,
                                              col_widths=col_widths)
        return planned, drawn

    def test_a129_prints_on_one_page(self):
        planned, drawn = self.render("a129", a129_rows(), layout={"autofit_rows": True},
                                     col_widths=A129_WIDTHS)
        self.assertEqual((planned, drawn), (1, 1))

    def test_planned_pages_are_printed_pages(self):
        for n, autofit in ((14, True), (30, False), (55, True)):
            with self.subTest(rows=n, autofit=autofit):
                planned, drawn = self.render(f"mixed-{n}", mixed_rows(n),
                                             layout={"autofit_rows": autofit})
                self.assertEqual(planned, drawn,
                                 f"{n} rows: planned {planned} pages, printed {drawn}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
