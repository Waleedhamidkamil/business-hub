"""A PO row must not be booked TALLER than it renders either (2026-09-28).

Reported from A-110: 22 rows printed as 16 + 6, with an obvious gap at the
bottom of page 1 and most of page 2 blank. Waleed's question was the right one -
why break there when there is clearly space?

`tests/po_row_height_matches_render.test.py` already guards the dangerous
direction: a row must never render TALLER than it was booked, or the table runs
into the signature. Nothing guarded the wasteful direction, so a row booked at
twice its height cost a page break and no test noticed.

Root cause: `_CHARS_PER_LINE` was a hard-coded table "derived from each column's
width in mm vs ~2.7mm per Arabic glyph at 12pt". Measured through the real
renderer, an Arabic glyph at this size averages ~1.73mm, so every column's
capacity was understated by 64-100%:

    column        booked capacity   real capacity   over-books by
    description        12 chars        22 chars          83%
    name               14 chars        25 chars          79%
    company            11 chars        18 chars          64%
    unit                6 chars        12 chars         100%

Four of A-110's sixteen page-1 rows were single-line rows booked as two.

Second defect, the reason a fixed table cannot be right at all: **PO column
widths are per document** (`doc_columns`; 43 live POs carry a 21% description
column, 13 carry 24%, three carry 28.39%) and `paginate_po` never received
them, so the planner and the renderer disagreed about how wide a column was.
`quote_generator.estimate_row_height_mm` already solved this - it derives
chars-per-line from the column's own width - and the PO now does the same.

Real Chromium: this is a question about what the browser actually draws.
"""
from __future__ import annotations

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

MM = 25.4 / 72.0
MARKER = "ZZMARKERZZ"
# Arabic letters of ordinary width, spaced the way real PO text is.
ALPHA = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"

# A-110's own page-1 rows that were booked at two lines and drew one.
A110_SINGLE_LINE = [
    {"name": "بوري مربع", "description": "4 انج 2 ملم 6 متر"},
    {"name": "حديد زاوية", "description": "2.5 انج 5 ملم"},
    {"name": "سندويش بنل جداري", "description": "220 سم"},
    {"name": "سندويش بنل سقفي", "description": "120 سم"},
]


def _probe(n):
    out = []
    while len("".join(out)) < n:
        out.append(ALPHA[len(out) % len(ALPHA)] * 3)
        out.append(" ")
    return "".join(out)[:n]


def _row(**kw):
    base = {"name": "ا", "description": "ا", "unit": "ا", "company": "ا",
            "qty": "1", "image": ""}
    base.update(kw)
    return base


class PoRowHeightIsNotOverBooked(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            pdf_engine.find_browser()
        except pdf_engine.PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))
        if fitz is None:
            raise unittest.SkipTest("PyMuPDF not installed")
        cls.work = Path(tempfile.mkdtemp(prefix="po_over_book_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def _rendered_height(self, row, col_widths=None):
        """How tall `row` actually DRAWS, in mm.

        The probe row is followed by a marker row; the marker's y minus the
        first row's y is the probe's rendered height. Reading the gap between
        two extracted spans instead would confuse the two lines INSIDE a
        wrapped row with two rows.
        """
        from backend import pdf_generator as pg
        out = self.work / "probe.pdf"
        pg.render_pdf("A-TEST", "5/9/2026",
                      [dict(row), _row(name=MARKER)], out,
                      col_widths=col_widths)
        with fitz.open(out) as doc:
            page = doc[0]
            first = None
            marker = None
            for b in page.get_text("dict")["blocks"]:
                for line in b.get("lines", []):
                    for s in line["spans"]:
                        if not s["text"].strip():
                            continue
                        y = s["bbox"][1] * MM
                        if MARKER in s["text"]:
                            marker = y
                        elif y > 68 and (first is None or y < first):
                            first = y
        self.assertIsNotNone(marker, "the marker row never rendered")
        self.assertIsNotNone(first, "no data row rendered")
        return round(marker - first, 2)

    def _booked(self, row, col_widths=None):
        from backend import pdf_generator as pg
        return pg.estimate_row_height_mm(row, col_widths=col_widths)

    # ------------------------------------------------------------- the report
    def test_a110s_single_line_rows_are_booked_as_one_line(self):
        """The four rows that cost A-110 its page break."""
        one_line = self._rendered_height(_row(name="ا", description="ا"))
        over = []
        for shape in A110_SINGLE_LINE:
            row = _row(**shape)
            drew = self._rendered_height(row)
            booked = self._booked(row)
            if drew > one_line + 0.5:
                self.skipTest(f"{shape['name']} genuinely wraps; fixture is wrong")
            if booked > drew + 0.6:
                over.append(f"{shape['name']!r} / {shape['description']!r}: "
                            f"booked {booked}mm, drew {drew}mm")
        self.assertEqual([], over,
                         "single-line rows booked as two lines:\n  "
                         + "\n  ".join(over))

    def test_a_full_page_is_not_broken_early(self):
        """The consequence, stated as pages. The wasteful direction, which
        nothing tested.

        15 single-line rows need 12 + 15 x 10.3 = 166.5mm of the 171mm a
        single page has (it carries the header AND the signature). Booked as
        two lines each, as they were, they came to 249mm and took two sheets.
        """
        from backend import pdf_generator as pg
        rows = [_row(**A110_SINGLE_LINE[i % len(A110_SINGLE_LINE)])
                for i in range(15)]
        planned = len(pg.paginate_po(rows, "A-TEST"))
        out = self.work / "full.pdf"
        pg.render_pdf("A-TEST", "5/9/2026", rows, out)
        with fitz.open(out) as doc:
            actual = doc.page_count
        self.assertEqual(planned, actual,
                         f"planned {planned} pages, browser drew {actual}")
        self.assertEqual(1, actual,
                         f"15 single-line rows drew on {actual} pages; they "
                         "need 166.5mm of a 171mm single-page budget")

    def test_a110_itself_keeps_more_rows_on_page_one(self):
        """The document that was reported, by its own numbers.

        It printed 16 + 6 with four of those sixteen booked at two lines. Page
        1's budget is 225mm and it was packed to 215.3mm, so the 22mm those
        four wasted is three more rows.
        """
        from backend import pdf_generator as pg
        rows = [
            _row(name="بوري مربع", description="4 انج 2 ملم 6 متر", unit="عدد", qty="6"),
            _row(name="بوري شخاط", description="4 انج", unit="عدد", qty="12"),
            _row(name="بوري مربع", description="2 انج", unit="عدد", qty="22"),
            _row(name="جنل", description="3 انج", unit="عدد", qty="6"),
            _row(name="درج بايات حديد", description="90 سم", unit="عدد", qty="8"),
            _row(name="كيربي", description="6 متر", unit="عدد", qty="16"),
            _row(name="بيس بليت بدون ثقوب", description="20*20\n6 ملم", unit="عدد", qty="32"),
            _row(name="خشب بلايود", description="14 ملم", unit="عدد", qty="8"),
            _row(name="حديد زاوية", description="2.5 انج 5 ملم", unit="عدد", qty="10"),
            _row(name="سندويش بنل جداري", description="220 سم", unit="عدد", qty="9"),
            _row(name="سندويش بنل سقفي", description="120 سم", unit="عدد", qty="4"),
            _row(name="مقعد شرقي", description="", unit="عدد", qty="3"),
            _row(name="باب PVC", description="", unit="عدد", qty="3"),
            _row(name="تانكي مياه ثقيلة", description="90*95 ارتفاع 75", unit="عدد", qty="3"),
            _row(name="هوك حلقة", description="12 * 10\nفتحة 6 سم", unit="عدد", qty="25"),
            _row(name="سوكت حديد", description="4 انج", unit="عدد", qty="3"),
            _row(name="كب سندويش", description="20 سم", unit="متر", qty="10"),
            _row(name="صبغ بوية", description="لون فيلي\n5 كيلو", unit="عدد", qty="5"),
            _row(name="رولة صبغ", description="4 انج", unit="عدد", qty="10"),
            _row(name="بنزين", description="20 لتر", unit="عدد", qty="2"),
            _row(name="واير لحام", description="3.2", unit="باكيت", qty="1"),
            _row(name="فيتة اصلية", description="5 متر", unit="عدد", qty="2"),
        ]
        # A-110's own stored column widths, not the defaults.
        widths = {"name": 20.0, "description": 21.0, "unit": 9.0,
                  "qty": 7.0, "company": 16.0, "image": 11.0}
        pages = pg.paginate_po(rows, "A-110", col_widths=widths)
        on_page_1 = len(pages[0]["rows"])
        self.assertGreaterEqual(
            on_page_1, 19,
            f"page 1 took only {on_page_1} of the 22 rows; it took 16 before "
            "the wrap budget was corrected, and only three of these rows "
            "genuinely wrap")
        out = self.work / "a110.pdf"
        pg.render_pdf("A-110", "28/9/2026", rows, out, col_widths=widths)
        with fitz.open(out) as doc:
            actual = doc.page_count
        self.assertEqual(len(pages), actual,
                         f"planned {len(pages)} pages, browser drew {actual}")

    # -------------------------------------------- the estimate tracks reality
    def test_the_wrap_budget_is_close_to_what_the_column_really_fits(self):
        """Measured, not asserted against a constant - a constant is exactly
        what drifted here.

        The budget stays DELIBERATELY under the real capacity: under-booking
        runs the table into the signature, so a margin is right. What was wrong
        was its size. Booking two lines for text at half a column's capacity is
        a bug; booking two lines for text at 95% of it is prudence.

        The contract: the budget must be at least 70% of what the column really
        fits. The old table sat at 50-55% and cost A-110 a page break.
        """
        from backend import pdf_generator as pg
        one_line = self._rendered_height(_row())
        budgets = pg._po_chars_per_line()
        too_tight = []
        for field in ("description", "name", "company", "unit"):
            real_wrap = None
            for n in range(2, 40):
                if self._rendered_height(_row(**{field: _probe(n)})) > one_line + 0.5:
                    real_wrap = n
                    break
            self.assertIsNotNone(real_wrap, f"{field} never wrapped by 39 chars")
            fits = real_wrap - 1
            budget = budgets[field]
            ratio = budget / fits
            if ratio < 0.70:
                too_tight.append(
                    f"{field}: really fits {fits} chars, budget is "
                    f"{budget:.1f} ({ratio:.0%}) - rows are booked too tall")
            # And the margin must stay a margin: never book MORE than fits.
            self.assertLessEqual(
                budget, fits + 0.001,
                f"{field}: budget {budget:.1f} exceeds the {fits} chars that "
                "really fit - that is the direction that overflows the "
                "signature")
        self.assertEqual([], too_tight, "\n  ".join([""] + too_tight))

    # --------------------------------------------- the safe direction survives
    def test_a_row_that_really_wraps_is_still_booked_tall(self):
        """The guarantee this must not trade away. Under-booking is what runs
        the table into the signature."""
        from backend import pdf_generator as pg
        row = _row(description="20*20\n6 ملم")
        drew = self._rendered_height(row)
        booked = self._booked(row)
        self.assertGreaterEqual(
            booked + 0.6, drew,
            f"a two-line row drew {drew}mm but was booked {booked}mm - "
            "under-booking runs the table into the signature")

    def test_a_narrower_column_books_taller(self):
        """Why a fixed table cannot be right: 43 live POs carry a 21% wide
        description column, 13 carry 24%, three carry 28.39%. The planner has
        to read the width the renderer will use."""
        from backend import pdf_generator as pg
        text = _probe(20)
        row = _row(description=text)
        wide = pg.estimate_row_height_mm(
            row, col_widths={"name": 20, "description": 34, "unit": 9,
                             "qty": 7, "company": 16})
        narrow = pg.estimate_row_height_mm(
            row, col_widths={"name": 34, "description": 8, "unit": 9,
                             "qty": 7, "company": 16})
        self.assertGreater(
            narrow, wide,
            "the same text in an 8% column must book taller than in a 34% one; "
            f"got narrow={narrow}mm wide={wide}mm")

    def test_the_planner_is_given_the_widths_the_renderer_uses(self):
        """The wiring, not just the arithmetic. `render_html` holds
        `col_widths` and used to paginate without them."""
        from backend import pdf_generator as pg
        rows = [_row(description=_probe(20)) for _ in range(30)]
        narrow = {"name": 34, "description": 8, "unit": 9, "qty": 7, "company": 16}
        out = self.work / "narrow.pdf"
        pg.render_pdf("A-TEST", "5/9/2026", rows, out, col_widths=narrow)
        planned = len(pg.paginate_po(rows, "A-TEST", col_widths=narrow))
        with fitz.open(out) as doc:
            actual = doc.page_count
        self.assertEqual(planned, actual,
                         f"with a narrow description column the planner said "
                         f"{planned} pages and the browser drew {actual}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
