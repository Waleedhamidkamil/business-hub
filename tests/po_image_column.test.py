"""An image column on the Purchase Order (2026-08-05).

The PO gained the same per-row picture cell the quotation has. Two things about
it are dangerous enough to pin down, and neither is visible in the code:

1. PAGINATION. `estimate_row_height_mm` sizes a row from its TEXT. A row with a
   photo is ~22mm against a text row's 7mm, so without teaching the estimator
   the paginator packs a page full and the rendered table runs straight into
   the absolutely-positioned signature and stamp. That is not a cosmetic
   failure: the PO goes to a supplier with its signature printed over the last
   rows. Verified here by RENDERING and measuring, not by trusting the model -
   and there is a test below that deliberately breaks the estimator to prove
   the guard is what is holding the layout up.

2. THE COLUMN ONLY PRINTS WHEN USED. The Arabic PO table is already seven
   columns on A4 portrait. An always-on picture column would spend a fifth of
   it printing blanks on every ordinary order, so the column appears only when
   some row actually carries an image - and then it must appear on EVERY page
   of that PO, including pages whose own rows have no picture, or the pages
   print with different column counts.

Renders real PDFs into a temp dir; touches no live document.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# This module reads profile-dependent constants, so it is meaningless under the
# wrong profile - and it used to FAIL there, cryptically, which is how the board
# ended up warning readers not to believe it instead of fixing it (2026-09-05).
# Say so plainly instead. See tests/profile_guard.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_guard import require_profile  # noqa: E402

require_profile("abraj")


import fitz  # noqa: E402
from backend import config, pdf_generator as pg  # noqa: E402

# A 64x64 red square, small enough to keep the fixtures readable.
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAT0lEQVR4nO3QMQ0"
       "AAAjAMPybxjEAdCTAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
       "AAAAAAAAAADgxwUPAAF8jXlBAAAAAElFTkSuQmCC")

MM = 25.4 / 72.0
# The .po-page box sits 10mm..287mm on the sheet; the signature block occupies
# the last PO_SIGNATURE_MM of it.
SIG_TOP_MM = 10.0 + (277.0 - config.PO_SIGNATURE_MM)


def rows(n, image=False):
    return [{"name": f"ROWMARK{i}", "description": "وصف المادة", "unit": "عدد",
             "qty": "2", "company": "TDC", **({"image": PNG} if image else {})}
            for i in range(1, n + 1)]


def table_bottom_mm(page):
    """Lowest TABLE ink on the page, in mm.

    Found via the planted row label and the row pictures - deliberately NOT via
    page drawings. The signature block draws a box of its own near the foot of
    every page, and measuring that instead reports an overlap on every layout
    ever produced, including a three-row text PO. (It did. That is why this
    helper looks the way it does.)
    """
    best = 0.0
    for b in page.get_text("blocks"):
        if "ROWMARK" in b[4]:
            best = max(best, b[3])
    for info in page.get_image_info():
        if info["bbox"][3] * MM < 40:      # the header logo, not a row picture
            continue
        best = max(best, info["bbox"][3])
    return best * MM


class RowHeight(unittest.TestCase):
    def test_an_image_row_is_reserved_the_image_height(self):
        self.assertEqual(pg.estimate_row_height_mm({"name": "x", "image": PNG}),
                         pg.PO_IMAGE_ROW_MIN_MM)

    def test_a_text_row_is_unchanged(self):
        self.assertEqual(pg.estimate_row_height_mm({"name": "x"}),
                         config.TABLE_MIN_ROW_MM)

    def test_the_image_wins_over_short_text(self):
        tall = pg.estimate_row_height_mm({"name": "x", "image": PNG})
        self.assertGreater(tall, pg.estimate_row_height_mm({"name": "x"}))

    def test_long_text_still_wins_when_taller_than_the_image(self):
        # max(), not "image beats everything": a row with a photo AND ten lines
        # of description is as tall as the text.
        long_text = {"name": "x", "description": "و" * 400, "image": PNG}
        self.assertGreater(pg.estimate_row_height_mm(long_text),
                           pg.PO_IMAGE_ROW_MIN_MM)

    def test_blank_and_whitespace_images_do_not_reserve_space(self):
        for val in ("", "   ", None):
            self.assertEqual(pg.estimate_row_height_mm({"name": "x", "image": val}),
                             config.TABLE_MIN_ROW_MM, f"{val!r} is not an image")


class ColumnAppearsOnlyWhenUsed(unittest.TestCase):
    def test_detects_an_image_anywhere_in_the_document(self):
        self.assertFalse(pg.any_row_has_image(rows(5)))
        self.assertFalse(pg.any_row_has_image([]))
        self.assertTrue(pg.any_row_has_image(rows(5) + rows(1, image=True)))

    def test_every_page_agrees_about_the_column(self):
        # A photo on the last row only must still put the column on page 1, or
        # the two pages print with different numbers of columns.
        mixed = rows(40) + rows(1, image=True)
        pages = pg.paginate_po(mixed, "A-TEST")
        self.assertGreater(len(pages), 1, "expected this to paginate")
        self.assertTrue(all(p["has_images"] for p in pages),
                        "has_images must be a whole-document fact, not per page")

    def test_a_plain_po_does_not_get_the_column(self):
        pages = pg.paginate_po(rows(6), "A-TEST")
        self.assertTrue(all(not p["has_images"] for p in pages))


class RenderedLayout(unittest.TestCase):
    """The measurements that actually matter."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def render(self, r, name):
        out = self.dir / f"{name}.pdf"
        pg.render_pdf("A-IMGTEST", "5/8/2026", r, out)
        return out

    def test_the_table_never_reaches_the_signature(self):
        for n in (1, 3, 8, 9, 10, 12, 20, 30, 45):
            with self.subTest(image_rows=n):
                doc = fitz.open(self.render(rows(n, image=True), f"img{n}"))
                try:
                    bottom = table_bottom_mm(doc[len(doc) - 1])
                    self.assertLessEqual(
                        bottom, SIG_TOP_MM,
                        f"{n} image rows: the table reaches {bottom:.1f}mm but the "
                        f"signature block starts at {SIG_TOP_MM:.1f}mm - the PO would "
                        "print its signature over the last rows")
                finally:
                    doc.close()

    def test_text_only_purchase_orders_are_unaffected(self):
        for n in (1, 12, 30, 60):
            with self.subTest(text_rows=n):
                doc = fitz.open(self.render(rows(n), f"txt{n}"))
                try:
                    self.assertLessEqual(table_bottom_mm(doc[len(doc) - 1]), SIG_TOP_MM)
                finally:
                    doc.close()

    def test_images_actually_reach_the_pdf(self):
        doc = fitz.open(self.render(rows(3, image=True), "present"))
        try:
            pictures = [i for p in doc for i in p.get_image_info()
                        if i["bbox"][3] * MM >= 40]
            self.assertGreaterEqual(len(pictures), 3,
                                    "the row pictures are missing from the PDF")
        finally:
            doc.close()

    def test_no_row_is_lost_off_the_page(self):
        """Every row that goes in must come out on paper."""
        for n in (10, 12, 25):
            with self.subTest(image_rows=n):
                doc = fitz.open(self.render(rows(n, image=True), f"all{n}"))
                try:
                    found = sum(page.get_text().count("ROWMARK") for page in doc)
                finally:
                    doc.close()
                self.assertEqual(found, n,
                                 f"{n} rows went in, {found} came out of the PDF")

    def test_the_reserve_is_what_keeps_the_table_off_the_signature(self):
        """Blind the estimator to images and the overlap comes straight back.

        The row count where this bites is NOT fixed: `po_signature_mm` is a user
        setting, and moving it moves the whole window. An earlier version of
        this test hard-coded "10 rows overlaps", which was true at 54mm and
        false at 30mm - it started failing the moment the setting was changed in
        the app, which is a test bug, not a layout bug. So derive the case from
        the geometry instead of assuming it.

        Note WHAT is patched: the function, not PO_IMAGE_ROW_MIN_MM. That
        constant also feeds `--po-img-mm`, so zeroing it shrinks the pictures to
        nothing, rows go back to text height and nothing overflows - a mutation
        that proves the opposite of what it appears to.
        """
        real = pg.estimate_row_height_mm
        # The stub must take the real signature (2026-09-28: `col_widths` was
        # added so the planner uses the widths the renderer will draw with).
        # A one-argument stand-in raises TypeError inside paginate_po, which
        # looks like this suite failing rather than the swap being stale.
        blind = lambda row, autofit=False, col_widths=None: real(
            {k: v for k, v in row.items() if k != "image"},
            autofit=autofit, col_widths=col_widths)

        def bottom_of(n, estimator):
            pg.estimate_row_height_mm = estimator
            try:
                doc = fitz.open(self.render(rows(n, image=True), f"probe{n}"))
                try:
                    return table_bottom_mm(doc[len(doc) - 1]), len(doc)
                finally:
                    doc.close()
            finally:
                pg.estimate_row_height_mm = real

        # Rough starting point: the table opens ~71mm down and an image row
        # renders ~18mm, so the first row count that could reach the signature
        # is about this. Probe upward a few from there rather than rendering
        # twenty PDFs.
        start = max(2, int((SIG_TOP_MM - 71.0) / 18.2))
        overlap_at = None
        for n in range(start, start + 5):
            b, pages = bottom_of(n, blind)
            if pages == 1 and b > SIG_TOP_MM:
                overlap_at = n
                break

        self.assertIsNotNone(
            overlap_at,
            f"no row count between {start} and {start + 4} overflows the "
            f"signature (at {SIG_TOP_MM:.0f}mm) even with the estimator blind to "
            "images. This suite is therefore no longer proving the reserve does "
            "anything - widen the probe, or check whether po_signature_mm has "
            "moved far enough to change the window.")

        # ...and with the reserve in place, that exact case is safe.
        b, pages = bottom_of(overlap_at, real)
        self.assertLessEqual(
            b, SIG_TOP_MM,
            f"{overlap_at} image rows overflow the signature even WITH the "
            "reserve - the reserve is too small for the current geometry")

    def test_the_reserve_is_generous_not_tight(self):
        """The reserve deliberately over-books.

        A picture renders about 16.9mm tall (it is capped by the 11% column
        width long before the height cap bites), giving a row of roughly
        18.2mm against the 22mm booked. That slack is the house style for this
        module - see the note on _CHARS_PER_LINE - because under-estimating is
        what puts a table through a signature, while over-estimating only
        starts a new page a little early.
        """
        doc = fitz.open(self.render(rows(4, image=True), "measure"))
        try:
            pics = [i for p in doc for i in p.get_image_info()
                    if (i["bbox"][3] - i["bbox"][1]) * MM < 22]
            self.assertTrue(pics, "no row pictures found to measure")
            tallest = max((i["bbox"][3] - i["bbox"][1]) * MM for i in pics)
        finally:
            doc.close()
        self.assertLess(tallest, pg.PO_IMAGE_ROW_MIN_MM,
                        f"a picture renders {tallest:.1f}mm but only "
                        f"{pg.PO_IMAGE_ROW_MIN_MM}mm is reserved - the reserve must "
                        "not be smaller than what actually prints")


if __name__ == "__main__":
    unittest.main(verbosity=2)
