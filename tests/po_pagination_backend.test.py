"""
A PO is ONE document over N pages (2026-07-29).

Until now `pdf_generator.split_into_pos` turned an overflowing item list into
several *separate POs*, each with its own number and its own PDF - a 40-item
order became two unrelated purchase orders. It now paginates like every other
document type: one number, one PDF, page roles (header on page 1, signature on
the last page), and a per-page row budget derived from measured geometry.

Unit-level assertions always run. The rendered-PDF assertions need headless
Chrome/Edge + PyMuPDF and skip cleanly without them.

Covers:
  1. One PO number across every page; continuous row numbering; page roles.
  2. Per-page budgets differ by role, and packing reaches a fixed point.
  3. The row-height model counts explicit newlines (it used to under-estimate,
     the dangerous direction - the table could reach the signature).
  4. The retired split API and its config knobs are really gone.
  5. Rendered PDF: page count matches the estimate, every page is numbered,
     the header prints once, and no row overlaps the signature band.

!! Runs against the LIVE profile DB path only for `config` - it writes no rows.
!! PDFs go to the scratch .test-work tree. See the warning in docs/11.
"""
import shutil
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
WORK = APP_ROOT / ".test-work" / "po-pagination"
sys.path.insert(0, str(APP_ROOT))

# This module reads profile-dependent constants, so it is meaningless under the
# wrong profile - and it used to FAIL there, cryptically, which is how the board
# ended up warning readers not to believe it instead of fixing it (2026-09-05).
# Say so plainly instead. See tests/profile_guard.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_guard import require_profile  # noqa: E402

require_profile("abraj")


from backend import config, pdf_generator  # noqa: E402


def rows(n, desc="Steel pipe assembly", name_prefix="Item"):
    return [
        {"name": f"{name_prefix} {i}", "description": desc,
         "unit": "pcs", "qty": str(i), "company": "Vendor Co"}
        for i in range(1, n + 1)
    ]


class OnePoManyPages(unittest.TestCase):
    def test_single_page_is_untouched(self):
        pages = pdf_generator.paginate_po(rows(3), "A-10")
        self.assertEqual(len(pages), 1)
        p = pages[0]
        self.assertEqual(p["po_no"], "A-10")
        self.assertEqual((p["page_index"], p["total_pages"]), (1, 1))
        # A single page is BOTH the first and the last one.
        self.assertTrue(p["show_header"])
        self.assertTrue(p["is_last"])
        self.assertEqual([r["index"] for r in p["rows"]], [1, 2, 3])

    def test_overflow_paginates_instead_of_minting_numbers(self):
        many = rows(80)
        pages = pdf_generator.paginate_po(many, "A-10")
        self.assertGreater(len(pages), 1, "80 rows must need several pages")
        # The regression this whole change is about.
        self.assertEqual({p["po_no"] for p in pages}, {"A-10"})
        self.assertEqual([p["page_index"] for p in pages],
                         list(range(1, len(pages) + 1)))
        self.assertTrue(all(p["total_pages"] == len(pages) for p in pages))

    def test_page_roles(self):
        pages = pdf_generator.paginate_po(rows(80), "A-10")
        n = len(pages)
        self.assertEqual([p["show_header"] for p in pages],
                         [True] + [False] * (n - 1))
        self.assertEqual([p["is_last"] for p in pages],
                         [False] * (n - 1) + [True])

    def test_row_numbering_is_continuous_and_lossless(self):
        many = rows(80)
        pages = pdf_generator.paginate_po(many, "A-10")
        flat = [r for p in pages for r in p["rows"]]
        # Page 2 continues at 31-ish, it does NOT restart at 1.
        self.assertEqual([r["index"] for r in flat],
                         list(range(1, len(many) + 1)))
        self.assertEqual([r["name"] for r in flat], [r["name"] for r in many])
        self.assertTrue(all(r["filled"] for r in flat))
        self.assertGreater(pages[1]["rows"][0]["index"], 1)

    def test_no_page_is_empty(self):
        for n in (1, 2, 29, 30, 31, 45, 61, 80, 200):
            pages = pdf_generator.paginate_po(rows(n), "A-10")
            self.assertTrue(all(p["rows"] for p in pages),
                            f"{n} rows produced an empty page")

    def test_empty_row_list_still_renders_one_page(self):
        pages = pdf_generator.paginate_po([], "A-10")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["rows"], [])
        self.assertTrue(pages[0]["show_header"])
        self.assertTrue(pages[0]["is_last"])

    def test_page_count_helper_agrees(self):
        for n in (1, 40, 90):
            self.assertEqual(pdf_generator.po_page_count(rows(n), "A-10"),
                             len(pdf_generator.paginate_po(rows(n), "A-10")))

    def test_a88_autofit_layout_is_used_by_the_page_planner(self):
        """A-88 saved Auto-fit=true but the planner ignored document layout.

        Its 46 mostly one-line rows were drawn compactly, yet they were booked
        at the 10.3mm fixed-row setting. That produced 20 + 23 + 3 rows and a
        nearly empty third sheet even though the rendered rows fit on two.
        """
        sample = rows(46, desc="", name_prefix="مادة")
        old = (config.TABLE_MIN_ROW_MM, config.TABLE_LINE_HEIGHT_MM,
               config.TABLE_HEADER_MM, config.PO_SIGNATURE_MM)
        try:
            config.TABLE_MIN_ROW_MM = 10.3
            config.TABLE_LINE_HEIGHT_MM = 5.5
            config.TABLE_HEADER_MM = 12.0
            config.PO_SIGNATURE_MM = 54.0
            fixed = pdf_generator.paginate_po(
                sample, "A-88", layout={"autofit_rows": False})
            compact = pdf_generator.paginate_po(
                sample, "A-88", layout={"autofit_rows": True})
        finally:
            (config.TABLE_MIN_ROW_MM, config.TABLE_LINE_HEIGHT_MM,
             config.TABLE_HEADER_MM, config.PO_SIGNATURE_MM) = old

        self.assertEqual(len(fixed), 3)
        self.assertEqual(len(compact), 2)
        self.assertEqual([len(p["rows"]) for p in compact], [30, 16])
        self.assertEqual(
            pdf_generator.po_page_count(
                sample, "A-88", layout={"autofit_rows": True}),
            2,
        )


class BudgetMath(unittest.TestCase):
    def test_roles_get_different_budgets(self):
        single = pdf_generator._po_page_budget(is_first=True, is_last=True)
        first = pdf_generator._po_page_budget(is_first=True, is_last=False)
        middle = pdf_generator._po_page_budget(is_first=False, is_last=False)
        last = pdf_generator._po_page_budget(is_first=False, is_last=True)
        # A middle page has neither header nor signature → the most room.
        self.assertGreater(middle, first)
        self.assertGreater(middle, last)
        self.assertGreater(first, single)
        self.assertGreater(last, single)
        # Nothing may exceed the printable box.
        usable = pdf_generator._po_geometry()[0]
        for b in (single, first, middle, last):
            self.assertGreater(b, 0)
            self.assertLessEqual(b, usable)

    def test_last_page_reserves_the_signature_band(self):
        _, _, _, closeout = pdf_generator._po_geometry()
        middle = pdf_generator._po_page_budget(is_first=False, is_last=False)
        last = pdf_generator._po_page_budget(is_first=False, is_last=True)
        self.assertAlmostEqual(middle - last, closeout, places=6)

    def test_first_page_reserves_the_header(self):
        _, header, cont_top, _ = pdf_generator._po_geometry()
        first = pdf_generator._po_page_budget(is_first=True, is_last=False)
        middle = pdf_generator._po_page_budget(is_first=False, is_last=False)
        self.assertAlmostEqual(middle - first, header - cont_top, places=6)

    def test_packing_respects_the_budget(self):
        """No page may be packed past its own role's budget."""
        many = rows(120, desc="A much longer description that will wrap lines")
        pages = pdf_generator.paginate_po(many, "A-10")
        thead = float(config.TABLE_HEADER_MM)
        for p in pages:
            used = thead + sum(pdf_generator.estimate_row_height_mm(r)
                               for r in p["rows"])
            budget = pdf_generator._po_page_budget(
                is_first=p["page_index"] == 1, is_last=p["is_last"])
            # One row may straddle the budget only if it is alone on the page.
            if len(p["rows"]) > 1:
                self.assertLessEqual(round(used, 3), round(budget, 3),
                                     f"page {p['page_index']} overpacked")

    def test_packing_reaches_a_fixed_point(self):
        """The assumed page count must equal what packing actually produces.

        The last page reserves the signature band, so rows that fit while a page
        is still 'middle' may not fit once it becomes the last one. A wrong fixed
        point shows up as the final page exceeding its budget.
        """
        for n in range(1, 130, 7):
            pages = pdf_generator.paginate_po(rows(n), "A-10")
            last = pages[-1]
            used = float(config.TABLE_HEADER_MM) + sum(
                pdf_generator.estimate_row_height_mm(r) for r in last["rows"])
            budget = pdf_generator._po_page_budget(
                is_first=last["page_index"] == 1, is_last=True)
            if len(last["rows"]) > 1:
                self.assertLessEqual(round(used, 3), round(budget, 3),
                                     f"{n} rows: last page exceeds its budget")

    def test_taller_rows_need_more_pages(self):
        short = pdf_generator.paginate_po(rows(60, desc="x"), "A-10")
        tall = pdf_generator.paginate_po(
            rows(60, desc="wrap " * 40), "A-10")
        self.assertGreater(len(tall), len(short))


class RowHeightModel(unittest.TestCase):
    def test_single_line_row_is_the_minimum(self):
        h = pdf_generator.estimate_row_height_mm({"name": "Pipe"})
        self.assertAlmostEqual(h, config.TABLE_MIN_ROW_MM, places=6)

    def test_blank_row_is_the_minimum(self):
        self.assertAlmostEqual(pdf_generator.estimate_row_height_mm({}),
                               config.TABLE_MIN_ROW_MM, places=6)

    def test_explicit_newlines_add_height(self):
        """The bug: a typed newline was ignored, under-estimating the row.

        po_styles.css sets `white-space: pre-wrap` precisely so typed newlines
        survive into the PDF, so the estimator has to count them - otherwise the
        table can grow into the absolutely-positioned signature.
        """
        one = pdf_generator.estimate_row_height_mm({"name": "Pipe"})
        two = pdf_generator.estimate_row_height_mm({"name": "Pipe\nSteel"})
        three = pdf_generator.estimate_row_height_mm({"name": "A\nB\nC"})
        self.assertAlmostEqual(two - one, config.TABLE_LINE_HEIGHT_MM, places=6)
        self.assertAlmostEqual(three - one, 2 * config.TABLE_LINE_HEIGHT_MM,
                               places=6)

    def test_newlines_and_wrapping_compose(self):
        """Each segment wraps independently, then the segments are summed."""
        # `description` allows 12 chars per line: a 24-char segment is 2 lines,
        # so "24 chars\n24 chars" is 4 lines total.
        seg = "x" * 24
        h = pdf_generator.estimate_row_height_mm({"description": f"{seg}\n{seg}"})
        expected = config.TABLE_MIN_ROW_MM + 3 * config.TABLE_LINE_HEIGHT_MM
        self.assertAlmostEqual(h, expected, places=6)

    def test_tallest_column_wins(self):
        row = {"name": "short", "description": "y" * 60, "unit": "pcs"}
        self.assertAlmostEqual(
            pdf_generator.estimate_row_height_mm(row),
            pdf_generator.estimate_row_height_mm({"description": "y" * 60}),
            places=6)

    def test_estimate_never_underestimates_a_wrapped_cell(self):
        """Rounding must go UP: overflowing into the signature is unrecoverable,
        breaking a page slightly early is merely cosmetic.

        Derived from the live budget, not a hard-coded 13 (2026-09-28). This
        used to read "13 chars in `description` (12 per line) is 2 lines" - and
        12 per line was the bug: measured through the real renderer that column
        fits 22. Pinning the number here meant the stale budget had a test
        agreeing with it. One character past the budget is the contract; what
        the budget IS belongs to `po_row_height_is_not_over_booked`, which
        measures it.
        """
        cpl = pdf_generator._po_chars_per_line()["description"]
        just_over = int(cpl) + 1
        h = pdf_generator.estimate_row_height_mm({"description": "z" * just_over})
        self.assertAlmostEqual(
            h, config.TABLE_MIN_ROW_MM + config.TABLE_LINE_HEIGHT_MM, places=6,
            msg=f"{just_over} characters exceed the {cpl:.1f}-character budget "
                "and must book two lines")


class RetiredApi(unittest.TestCase):
    def test_split_api_is_gone(self):
        self.assertFalse(hasattr(pdf_generator, "split_into_pos"))
        self.assertFalse(hasattr(pdf_generator, "page_po_numbers"))

    def test_split_only_config_knobs_are_gone(self):
        """`MAX_ITEMS_PER_PO` existed only to force the split; `TABLE_BUDGET_MM`
        was a single flat budget that cannot describe per-role pages."""
        self.assertFalse(hasattr(config, "MAX_ITEMS_PER_PO"))
        self.assertFalse(hasattr(config, "TABLE_BUDGET_MM"))

    def test_stale_settings_keys_are_ignored_not_fatal(self):
        """Existing settings.json files still carry the retired keys."""
        snapshot = config.current_effective_settings()
        self.assertNotIn("max_items_per_po", snapshot)
        self.assertNotIn("table_budget_mm", snapshot)
        self.assertIn("po_signature_mm", snapshot)


class RenderedHtml(unittest.TestCase):
    def test_header_once_signature_once(self):
        html = pdf_generator.render_html("A-10", "29/07/2026", rows(80))
        # The logo header block and the title print on page 1 only.
        self.assertEqual(html.count('class="po-header"'), 1)
        self.assertEqual(html.count('class="po-title"'), 1)
        self.assertEqual(html.count('class="po-meta"'), 1)
        # Signature on the last page only.
        self.assertEqual(html.count('class="signature"'), 1)
        pages = pdf_generator.po_page_count(rows(80), "A-10")
        # Every page carries its own table header and a page number.
        self.assertEqual(html.count("<thead>"), pages)
        self.assertEqual(html.count('class="page-number"'), pages)
        self.assertIn(f"Page 1 of {pages}", html)
        self.assertIn(f"Page {pages} of {pages}", html)
        # Continuation pages are marked so the CSS can drop the top spacing.
        self.assertEqual(html.count("po-page--cont"), pages - 1)

    def test_no_invented_successor_numbers(self):
        html = pdf_generator.render_html("A-10", "29/07/2026", rows(80))
        for ghost in ("A-11", "A-12", "A-13"):
            self.assertNotIn(ghost, html,
                             f"paginated PO leaked a split number: {ghost}")

    def test_single_page_has_no_continuation_marker(self):
        html = pdf_generator.render_html("A-10", "29/07/2026", rows(3))
        self.assertNotIn("po-page--cont", html)
        self.assertIn("Page 1 of 1", html)
        self.assertEqual(html.count('class="signature"'), 1)


class RenderedPdf(unittest.TestCase):
    """Real geometry: needs headless Chrome/Edge + PyMuPDF."""

    @classmethod
    def setUpClass(cls):
        try:
            import fitz  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("PyMuPDF (fitz) not installed")
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)
        cls.rows = rows(80)
        try:
            cls.pdf = pdf_generator.render_pdf(
                "A-10", "29/07/2026", cls.rows, WORK / "A-10.pdf")
        except Exception as e:                       # no Chrome/Edge on this box
            raise unittest.SkipTest(f"PDF engine unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_page_count_matches_the_estimate(self):
        import fitz
        doc = fitz.open(self.pdf)
        try:
            actual = doc.page_count
        finally:
            doc.close()
        expected = pdf_generator.po_page_count(self.rows, "A-10")
        # The estimator rounds row heights up, so it may reserve one page more
        # than the browser needs - but it must never under-count (that is the
        # overflow direction).
        self.assertLessEqual(actual, expected,
                             "browser needed MORE pages than estimated")
        self.assertGreaterEqual(actual, expected - 1)

    def test_every_page_is_numbered(self):
        import fitz
        doc = fitz.open(self.pdf)
        try:
            total = doc.page_count
            for i, page in enumerate(doc, start=1):
                text = page.get_text()
                self.assertIn(f"Page {i} of {total}", text,
                              f"page {i} has no page number")
        finally:
            doc.close()

    def test_no_row_overlaps_the_signature(self):
        """The reason the reserve exists at all."""
        import fitz
        doc = fitz.open(self.pdf)
        try:
            last = doc[doc.page_count - 1]
            hits = last.search_for(config.COMPANY["manager_label_ar"])
            if not hits:
                self.skipTest("manager label not extractable from this PDF")
            sig_top = min(r.y0 for r in hits)
            # Every data row on the last page must end above the signature.
            row_bottoms = []
            for row in self.rows:
                for hit in last.search_for(row["name"]):
                    row_bottoms.append(hit.y1)
            if not row_bottoms:
                self.skipTest("row text not extractable from this PDF")
            self.assertLess(max(row_bottoms), sig_top,
                            "a table row overlaps the signature block")
        finally:
            doc.close()

    def test_header_appears_only_on_page_one(self):
        import fitz
        doc = fitz.open(self.pdf)
        try:
            title = config.COMPANY["title_ar"]
            pages_with_title = [
                i for i, page in enumerate(doc, start=1)
                if page.search_for(title)
            ]
        finally:
            doc.close()
        if not pages_with_title:
            self.skipTest("Arabic title not extractable from this PDF")
        self.assertEqual(pages_with_title, [1])

    def test_a88_autofit_plan_matches_the_two_rendered_pages(self):
        """The reported A-88 shape must not return to a nearly empty page 3."""
        import fitz
        sample = rows(46, desc="", name_prefix="مادة")
        out = WORK / "A-88-autofit.pdf"
        old_sig = config.PO_SIGNATURE_MM
        try:
            config.PO_SIGNATURE_MM = 54.0
            layout = {"autofit_rows": True}
            pdf_generator.render_pdf(
                "A-88", "7/9/2026", sample, out, layout=layout)
            planned = pdf_generator.po_page_count(
                sample, "A-88", layout=layout)
        finally:
            config.PO_SIGNATURE_MM = old_sig

        with fitz.open(out) as doc:
            self.assertEqual(planned, 2)
            self.assertEqual(doc.page_count, 2)
            self.assertIn("Page 1 of 2", doc[0].get_text())
            self.assertIn("Page 2 of 2", doc[1].get_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
