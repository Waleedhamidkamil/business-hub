"""A PO row must RENDER at the height the paginator BOOKED (2026-09-05).

Reported from A-84: a 29-row PO printed on three sheets while its own footers
read "Page 1 of 2" and "Page 2 of 2" - the paginator had planned two pages and
the browser produced three, so a document already sent to a supplier carried
page numbers that did not match its pages.

Root cause, and the reason it hid for so long: `po_styles.css` set a hard
`height: 26px` on the item cells with the default content-box, so the 6px
padding and the border were ADDED to it and a row drew 10.32mm - while
`estimate_row_height_mm` booked `config.TABLE_MIN_ROW_MM`, which defaulted to 7.
Every row was under-booked by a third, which only shows up once a PO is long
enough to need a page break.

The same defect is why "the PO settings don't save": `table_row_mm` was passed
into po_page.html and never used, so the row-height box moved the page breaks
and nothing you could see on the page.

The contract now: the setting is the rendered height (border-box), the estimate
reads the same number, and neither can move without the other.

Real Chromium - this is a question about what the browser actually draws, and a
stubbed layout would only re-assert the arithmetic that was already wrong.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend import pdf_engine  # noqa: E402

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None


def _rows(n, name="مادة", extra=""):
    return [{"name": f"{name} {i + 1}{extra}", "description": "", "unit": "كيلو",
             "qty": "10", "company": "TDC", "image": ""} for i in range(n)]


class PoRowHeightMatchesRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            pdf_engine.find_browser()
        except pdf_engine.PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))
        if fitz is None:
            raise unittest.SkipTest("PyMuPDF not installed")
        cls.work = Path(tempfile.mkdtemp(prefix="po_row_height_"))
        cls._saved = config.TABLE_MIN_ROW_MM

    @classmethod
    def tearDownClass(cls):
        config.TABLE_MIN_ROW_MM = cls._saved
        shutil.rmtree(cls.work, ignore_errors=True)

    def _pages(self, rows, row_mm=None):
        """(planned, actually rendered) page counts for these rows."""
        from backend import pdf_generator as pg
        if row_mm is not None:
            config.TABLE_MIN_ROW_MM = row_mm
            importlib.reload(pg)
        out = self.work / "po.pdf"
        pg.render_pdf("A-TEST", "5/9/2026", rows, out)
        planned = len(pg.paginate_po(rows, "A-TEST"))
        with fitz.open(out) as doc:
            return planned, doc.page_count

    # ---------------------------------------------------------------- the bug
    def test_the_reported_shape_no_longer_overflows(self):
        """29 rows: A-84's shape. Planned pages must equal rendered pages."""
        planned, actual = self._pages(_rows(29), row_mm=config.TABLE_MIN_ROW_MM)
        self.assertEqual(planned, actual,
                         f"planned {planned} pages, browser rendered {actual}")

    def test_no_row_count_overflows(self):
        """The break can only be wrong at the boundary, so walk across it."""
        bad = []
        for n in (1, 5, 10, 15, 19, 20, 21, 22, 23, 25, 30, 40):
            planned, actual = self._pages(_rows(n))
            if planned < actual:
                bad.append((n, planned, actual))
        self.assertEqual(bad, [], f"rows that overflowed (n, planned, actual): {bad}")

    # ------------------------------------------------- the setting is not inert
    def test_the_row_setting_changes_the_rendered_page(self):
        """The complaint behind the bug: this box used to do nothing visible.

        It moved the page breaks (the estimate) while every row drew at the same
        hard 26px, so the PO looked identical whatever you typed."""
        from backend import pdf_generator as pg
        heights = {}
        for row_mm in (8.0, 14.0):
            config.TABLE_MIN_ROW_MM = row_mm
            importlib.reload(pg)
            out = self.work / f"h{row_mm}.pdf"
            pg.render_pdf("A-TEST", "5/9/2026", _rows(6), out)
            with fitz.open(out) as doc:
                page = doc[0]
                pix = page.get_pixmap(dpi=300)
                w, h = pix.width, pix.height
                dark = [y for y in range(h)
                        if sum(1 for x in range(0, w, 6)
                               if sum(pix.pixel(x, y)[:3]) < 330) > (w / 6) / 3 * 0.7]
                groups = []
                for y in dark:
                    if groups and y - groups[-1][-1] <= 2:
                        groups[-1].append(y)
                    else:
                        groups.append([y])
                mids = [sum(g) / len(g) for g in groups]
                gaps = [(mids[i + 1] - mids[i]) / 300 * 25.4 for i in range(len(mids) - 1)]
                # the repeated gap is the data-row pitch
                rows_gaps = [g for g in gaps if row_mm - 1.5 < g < row_mm + 2.0]
                heights[row_mm] = round(sum(rows_gaps) / len(rows_gaps), 2) if rows_gaps else None

        self.assertIsNotNone(heights[8.0], "could not measure the 8mm row pitch")
        self.assertIsNotNone(heights[14.0], "could not measure the 14mm row pitch")
        self.assertNotAlmostEqual(
            heights[8.0], heights[14.0], places=1,
            msg=f"the row-height setting did not change the page: {heights}")
        # And each renders at what it asked for (within a rounding of a pixel).
        self.assertAlmostEqual(heights[14.0], 14.0, delta=0.3,
                               msg=f"14mm setting rendered {heights[14.0]}mm")

    def test_estimate_never_books_below_what_a_row_can_render(self):
        """`height` on a table cell is a MINIMUM. Asking for less than one line
        of text needs does not shrink the row, so booking that smaller number
        would put the estimate back under the render."""
        from backend import pdf_generator as pg
        config.TABLE_MIN_ROW_MM = 2.0
        importlib.reload(pg)
        booked = pg.estimate_row_height_mm(_rows(1)[0])
        self.assertGreaterEqual(
            booked, pg.PO_ROW_CONTENT_FLOOR_MM,
            "the estimate dropped below the row's own content floor")
        planned, actual = self._pages(_rows(30))
        self.assertGreaterEqual(planned, actual,
                                "a tiny row setting overflowed the page")

    def test_a_tall_setting_does_not_silently_grow_the_header(self):
        """`.items-table th, td` matches the <thead> too. Before the thead got
        its own height, a 20mm row setting grew the header with it while the
        paginator still booked TABLE_HEADER_MM - and 20mm POs overflowed."""
        planned, actual = self._pages(_rows(30), row_mm=20.0)
        self.assertGreaterEqual(planned, actual,
                                f"20mm rows overflowed: planned {planned}, got {actual}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
