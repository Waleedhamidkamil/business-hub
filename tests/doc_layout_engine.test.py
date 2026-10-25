# -*- coding: utf-8 -*-
"""The shared page-area engine (backend/doc_layout.py), 2026-09-05.

`docs/12-PDF-LAYOUT-CONSTITUTION.md` §2 describes four page ROLES and the area
each reserves. That model used to be written out three times - once per
generator - and all three had drifted into the SAME defect: the role selection
handled header / body / footer pages but not the **single** page, so a one-page
document that reached the multi-page path was handed the HEADERLESS area while
still rendering its header. A DN of 19 wrapping rows then built a 234mm table
under a 75mm header on a 273mm sheet and printed on two pages under a footer
that read "Page 1 of 1".

This file tests the model directly, in arithmetic, with no rendering - so it
runs in milliseconds and says exactly which rule broke. The end-to-end proof
that the model matches reality is `layout_planned_matches_rendered.test.py`;
the two are complements, not alternatives.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import doc_layout as dl  # noqa: E402


def geo(**kw):
    """A plausible A4 geometry; override one field per test."""
    base = dict(usable_mm=273.0, top_mm=75.0, cont_top_mm=2.0, head_row_mm=9.0,
                pagenum_mm=8.0, last_reserve_mm=37.0,
                last_fixed_in_table_mm=11.0, chrome_mm=0.0, safety_mm=4.0)
    base.update(kw)
    return dl.PageGeometry(**base)


class TheFourRoles(unittest.TestCase):
    def test_one_page_is_a_SINGLE_page_not_a_footer_page(self):
        """THE bug. A one-page document renders the header AND the closing
        block; calling it a footer page hands it the headerless area."""
        self.assertEqual(dl.role_for(0, 1), dl.SINGLE_PAGE)
        self.assertNotEqual(dl.role_for(0, 1), dl.FOOTER_PAGE)

    def test_the_roles_of_a_two_page_document(self):
        self.assertEqual([dl.role_for(i, 2) for i in range(2)],
                         [dl.HEADER_PAGE, dl.FOOTER_PAGE])

    def test_the_roles_of_a_long_document(self):
        self.assertEqual([dl.role_for(i, 5) for i in range(5)],
                         [dl.HEADER_PAGE, dl.BODY_PAGE, dl.BODY_PAGE,
                          dl.BODY_PAGE, dl.FOOTER_PAGE])

    def test_a_zero_page_document_still_answers(self):
        """`total_pages` can be 0 for an empty document; it must not raise."""
        self.assertEqual(dl.role_for(0, 0), dl.SINGLE_PAGE)


class TheFourAreas(unittest.TestCase):
    def test_a_headerless_page_never_reserves_the_header(self):
        """docs/12 §2's 'golden rule', and the Phase X.27 bug - the body page
        must be the biggest, because it reserves the least."""
        g = geo()
        self.assertGreater(g.body_area, g.header_area)
        self.assertGreater(g.body_area, g.footer_area)
        self.assertGreater(g.body_area, g.single_area)

    def test_the_single_page_is_the_smallest(self):
        """It is the only role that reserves BOTH the header and the closing
        block, so if it is not the smallest, one of them is missing."""
        g = geo()
        self.assertLessEqual(g.single_area, g.header_area)
        self.assertLessEqual(g.single_area, g.footer_area)

    def test_the_area_formulas(self):
        g = geo()
        self.assertAlmostEqual(g.header_area, 273 - 75 - 8)
        self.assertAlmostEqual(g.body_area, 273 - 2 - 8)
        self.assertAlmostEqual(g.footer_area, 273 - 2 - 37)
        self.assertAlmostEqual(g.single_area, 273 - 75 - 37)

    def test_a_skin_reserve_comes_off_EVERY_area(self):
        """docs/12 §6.1 - Eshraqat's measured chrome is subtracted from all
        four, never from some of them."""
        plain, skin = geo(), geo(chrome_mm=34.0)
        for role in dl.ROLES:
            self.assertAlmostEqual(plain.area(role) - skin.area(role), 34.0,
                                   msg=f"{role} did not lose the skin reserve")

    def test_area_for_routes_through_the_role(self):
        g = geo()
        self.assertAlmostEqual(g.area_for(0, 1), g.single_area)
        self.assertAlmostEqual(g.area_for(0, 3), g.header_area)
        self.assertAlmostEqual(g.area_for(1, 3), g.body_area)
        self.assertAlmostEqual(g.area_for(2, 3), g.footer_area)


class TheCapacities(unittest.TestCase):
    def test_only_the_closing_pages_reserve_the_in_table_totals(self):
        """The grand-total / tax+total rows exist on the LAST page only, so a
        header or body page must not lose height to them."""
        g = geo(last_fixed_in_table_mm=18.0)
        self.assertAlmostEqual(g.capacity(dl.HEADER_PAGE), g.header_area - 9 - 4)
        self.assertAlmostEqual(g.capacity(dl.BODY_PAGE), g.body_area - 9 - 4)
        self.assertAlmostEqual(g.capacity(dl.FOOTER_PAGE), g.footer_area - 9 - 18 - 4)
        self.assertAlmostEqual(g.capacity(dl.SINGLE_PAGE), g.single_area - 9 - 18)

    def test_every_capacity_reserves_the_thead(self):
        g = geo(head_row_mm=9.0)
        for role in dl.ROLES:
            self.assertLessEqual(g.capacity(role), g.area(role) - 9.0 + 1e-9,
                                 f"{role} did not reserve the table header")

    def test_the_single_page_takes_no_safety_margin(self):
        """It already reserves the whole closing block and never fills
        (docs/12 §4.3), so a second margin would only lose rows."""
        g = geo(safety_mm=4.0)
        self.assertAlmostEqual(g.capacity(dl.SINGLE_PAGE), g.single_area - 9 - 11)

    def test_fits_on_one_page_asks_the_SMALLEST_capacity(self):
        """Not 'do these fit the biggest page' - the single page is the one
        that also carries the header and the closing block."""
        g = geo()
        cap = g.capacity(dl.SINGLE_PAGE)
        self.assertTrue(dl.fits_on_one_page(g, [cap - 1]))
        self.assertFalse(dl.fits_on_one_page(g, [cap + 1]))
        self.assertTrue(dl.fits_on_one_page(g, []))
        # ...and it is stricter than the body capacity, which is the trap.
        self.assertFalse(dl.fits_on_one_page(g, [g.capacity(dl.BODY_PAGE)]))


class TheInvariantsAreChecked(unittest.TestCase):
    def test_a_healthy_geometry_reports_nothing(self):
        self.assertEqual(geo().problems(), [])
        geo().validate()          # must not raise

    def test_an_impossible_page_is_reported(self):
        g = geo(last_reserve_mm=260.0)      # nothing left for the table
        self.assertTrue(g.problems())
        with self.assertRaises(dl.LayoutError):
            g.validate()

    def test_a_headerless_page_still_reserving_the_header_is_reported(self):
        """The Phase X.27 defect, as a check rather than a paragraph."""
        g = geo(cont_top_mm=75.0)           # continuation pages reserve as much as page 1
        self.assertTrue(any("headerless" in p for p in g.problems()),
                        f"expected the golden-rule breach to be reported: {g.problems()}")

    def test_negative_geometry_is_refused_outright(self):
        with self.assertRaises(dl.LayoutError):
            geo(top_mm=-1.0)

    def test_an_unknown_role_is_refused_rather_than_guessed(self):
        with self.assertRaises(dl.LayoutError):
            geo().area("last")


class TheRealGeneratorsAgree(unittest.TestCase):
    """The three live geometries must satisfy the invariants - this is what
    turns docs/12 §4 from prose into something that runs."""

    def test_every_document_types_geometry_is_sound(self):
        from backend import config, dn_generator, inv_generator, quote_generator
        problems = []
        # quote
        q = quote_generator
        terms = config.QUOTE_TYPES["materials"]["terms"]
        specs = [
            ("quote", dl.PageGeometry(
                usable_mm=q.Q_USABLE_MM, top_mm=q.Q_TOP_MM, cont_top_mm=q.Q_CONT_TOP_MM,
                head_row_mm=q.Q_HEADER_ROW_MM, pagenum_mm=q.Q_PAGENUM_MM,
                last_reserve_mm=q.Q_CLOSING_MM + q._footer_block_mm(terms) + q.Q_SAFE_GAP_MM,
                last_fixed_in_table_mm=q.Q_GRAND_MM, safety_mm=q.Q_PAGE_SAFETY_MM)),
            ("dn", dl.PageGeometry(
                usable_mm=dn_generator.DN_USABLE_MM, top_mm=dn_generator.DN_TOP_MM,
                cont_top_mm=dn_generator.DN_CONT_TOP_MM,
                head_row_mm=dn_generator.DN_HEADER_ROW_MM,
                pagenum_mm=dn_generator.DN_PAGENUM_MM,
                last_reserve_mm=dn_generator.DN_CLOSING_MM
                                + dn_generator._footer_block_mm()
                                + dn_generator.DN_SAFE_GAP_MM,
                last_fixed_in_table_mm=dn_generator.DN_GRAND_ROW_MM,
                safety_mm=dn_generator.DN_PAGE_SAFETY_MM)),
            ("inv", dl.PageGeometry(
                usable_mm=inv_generator.INV_USABLE_MM, top_mm=inv_generator.INV_TOP_MM,
                cont_top_mm=inv_generator.INV_CONT_TOP_MM,
                head_row_mm=inv_generator.INV_HEADER_ROW_MM,
                pagenum_mm=inv_generator.INV_PAGENUM_MM,
                last_reserve_mm=inv_generator.INV_CLOSING_MM
                                + inv_generator._footer_block_mm()
                                + inv_generator.INV_SAFE_GAP_MM,
                last_fixed_in_table_mm=inv_generator.INV_TAX_ROW_MM
                                       + inv_generator.INV_TOTAL_ROW_MM,
                safety_mm=inv_generator.INV_PAGE_SAFETY_MM)),
        ]
        for name, g in specs:
            for p in g.problems():
                problems.append(f"{name}: {p}")
            # And every type must leave real room for rows on its tightest page.
            if g.capacity(dl.SINGLE_PAGE) < 20:
                problems.append(f"{name}: a single page holds only "
                                f"{g.capacity(dl.SINGLE_PAGE):.1f}mm of rows")
        self.assertEqual(problems, [], "\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
