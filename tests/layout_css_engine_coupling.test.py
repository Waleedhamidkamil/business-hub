# -*- coding: utf-8 -*-
"""Every millimetre the engine reserves must equal the millimetre the CSS draws.

docs/12 §4.7 - "every coupled number flows ONE way" (2026-09-05).

The bug class
-------------
A page-layout constant exists twice: once in the generator, which RESERVES the
space, and once in the print stylesheet, which DRAWS it. Nothing connects them,
so the two drift and the document plans N pages and prints N+1 - carrying
footers that still say "of N". That is A-84, and it is also what
`tests/layout_planned_matches_rendered.test.py` found in the invoice and the
delivery note the day it was written.

The fix is to have the template publish the engine's number as a CSS variable
so there is only ever ONE copy. Where that has been done, this file checks the
variable is really wired end to end - a `var(--x)` that no template sets is a
silent fallback to the literal, which is the same bug wearing a nicer face.

Where it has NOT been done - the Eshraqat skin, whose geometry is folded into a
single measured `*_ESH_PAGE_CHROME_MM` rather than modelled term by term - this
file pins the literals instead, so changing one fails here and the reader is
told to re-measure the chrome constant rather than discovering it in a PDF.

Cheap and fast: this parses text. The expensive end-to-end proof is the
planned-vs-rendered suite; this one exists to say WHICH number moved.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import dn_generator, inv_generator, quote_generator  # noqa: E402
from backend import config, pdf_generator  # noqa: E402


def read(rel: str) -> str:
    return (APP_ROOT / rel).read_text(encoding="utf-8")


def css_value(css: str, pattern: str) -> str | None:
    """The declaration text a selector/property pattern matches, comments out."""
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(pattern, stripped, re.S)
    return m.group(1).strip() if m else None


class EngineNumbersReachTheStylesheet(unittest.TestCase):
    """Where a CSS variable carries the number, prove the wiring is complete."""

    #: (label, css file, regex capturing the declared value, template, var, engine value)
    WIRED = [
        ("quote thead", "static/quote_styles.css",
         r"\.q-table thead th \{[^}]*?height:\s*([^;]+);",
         "templates/quote_page.html", "--q-head-mm",
         lambda: quote_generator.Q_HEADER_ROW_MM),
        ("dn thead", "static/dn_styles.css",
         r"\.d-table thead th \{[^}]*?height:\s*([^;]+);",
         "templates/dn_page.html", "--dn-head-mm",
         lambda: dn_generator.DN_HEADER_ROW_MM),
        ("inv thead", "static/inv_styles.css",
         r"\.i-table thead th \{[^}]*?height:\s*([^;]+);",
         "templates/inv_page.html", "--i-head-mm",
         lambda: inv_generator.INV_HEADER_ROW_MM),
        ("dn grand row", "static/dn_styles.css",
         r"\.d-table\.d-table--autofit tbody tr\.d-grand-row \{\s*height:\s*([^;]+);",
         "templates/dn_page.html", "--dn-grand-mm",
         lambda: dn_generator.DN_GRAND_ROW_MM),
        ("inv tax row", "static/inv_styles.css",
         r"\.i-table\.i-table--autofit tbody tr\.i-tax-row \{\s*height:\s*([^;]+);",
         "templates/inv_page.html", "--i-tax-mm",
         lambda: inv_generator.INV_TAX_ROW_MM),
        ("inv total row", "static/inv_styles.css",
         r"\.i-table\.i-table--autofit tbody tr\.i-total-row \{\s*height:\s*([^;]+);",
         "templates/inv_page.html", "--i-total-mm",
         lambda: inv_generator.INV_TOTAL_ROW_MM),
        ("po row", "static/po_styles.css",
         r"box-sizing: border-box;\s*height:\s*([^;]+);",
         "templates/po_page.html", "--po-row-mm",
         lambda: config.TABLE_MIN_ROW_MM),
        ("po thead", "static/po_styles.css",
         r"\.items-table thead th \{[^}]*?height:\s*([^;]+);",
         "templates/po_page.html", "--po-head-mm",
         lambda: config.TABLE_HEADER_MM),
    ]

    def test_the_css_reads_the_variable_not_a_literal(self):
        wrong = []
        for label, css_file, pattern, _tpl, var, _engine in self.WIRED:
            decl = css_value(read(css_file), pattern)
            if decl is None:
                wrong.append(f"{label}: could not find the rule in {css_file}")
            elif f"var({var}" not in decl:
                wrong.append(f"{label}: {css_file} declares {decl!r}, "
                             f"not var({var}, …) - the engine's number is a "
                             "second copy again")
        self.assertEqual(wrong, [], "\n  " + "\n  ".join(wrong))

    def test_a_template_actually_publishes_each_variable(self):
        """`var(--x, 9mm)` with nothing setting --x is not coupling - it is the
        literal with extra steps, and it fails silently."""
        missing = []
        for label, _css, _pat, tpl, var, _engine in self.WIRED:
            if var not in read(tpl):
                missing.append(f"{label}: {tpl} never sets {var}, so the CSS "
                               "always falls back to its literal")
        self.assertEqual(missing, [], "\n  " + "\n  ".join(missing))

    def test_the_css_fallback_equals_the_engine_default(self):
        """The fallback is for a render that forgot to publish, so it must not
        be a different number - otherwise it silently changes the layout."""
        wrong = []
        for label, css_file, pattern, _tpl, var, engine in self.WIRED:
            decl = css_value(read(css_file), pattern) or ""
            m = re.search(re.escape(var) + r"\s*,\s*([0-9.]+)mm", decl)
            if not m:
                wrong.append(f"{label}: no numeric fallback in {decl!r}")
                continue
            if abs(float(m.group(1)) - float(engine())) > 0.001:
                wrong.append(f"{label}: CSS falls back to {m.group(1)}mm but the "
                             f"engine reserves {engine()}mm")
        self.assertEqual(wrong, [], "\n  " + "\n  ".join(wrong))


class LiteralsThatAreStillLiterals(unittest.TestCase):
    """The Eshraqat skin folds its geometry into ONE measured reserve.

    `*_ESH_PAGE_CHROME_MM = 34` is documented as "~20mm bottom padding + ~14mm
    taller thead" (docs/12 §6.1). That is a DERIVED number, not a duplicated
    one, so it cannot be replaced by a variable without unpicking the
    measurement. Pin the inputs instead: if one of them moves, this fails and
    says to re-measure the chrome rather than letting a skin silently overflow.
    """

    ESH = "static/eshraqat_print.css"

    def _decl(self, pattern):
        return css_value(read(self.ESH), pattern)

    def test_the_eshraqat_thead_height_is_unchanged(self):
        got = self._decl(r"\.esh-table th \{\s*height:\s*([^;]+);")
        self.assertEqual(
            got, "7mm",
            "the Eshraqat table header height changed. It is one of the two "
            "inputs to *_ESH_PAGE_CHROME_MM (34mm, docs/12 §6.1) - re-measure "
            "that reserve and update this test, or the skin over/under-books "
            "every page.")

    def test_the_eshraqat_page_bottom_padding_is_unchanged(self):
        css = re.sub(r"/\*.*?\*/", "", read(self.ESH), flags=re.S)
        m = re.search(r"\.esh-page\s*\{(.*?)\}", css, re.S)
        self.assertIsNotNone(m, ".esh-page rule not found")
        self.assertIn(
            "20mm", m.group(1),
            "the .esh-page bottom padding changed; it is the other input to "
            "*_ESH_PAGE_CHROME_MM (34mm) - re-measure it.")

    def test_the_chrome_reserve_is_still_what_the_doc_says(self):
        for mod, name in ((quote_generator, "Q"), (dn_generator, "DN"),
                          (inv_generator, "INV")):
            const = next((getattr(mod, a) for a in dir(mod)
                          if a.endswith("_ESH_PAGE_CHROME_MM")), None)
            self.assertIsNotNone(const, f"{name}: no *_ESH_PAGE_CHROME_MM found")
            self.assertAlmostEqual(
                float(const), 34.0, places=3,
                msg=f"{name}'s Eshraqat chrome reserve moved off the measured "
                    "34mm - update docs/12 §6.1 and the two tests above with it")


class NoTypeSharesAnotherTypesConstant(unittest.TestCase):
    """docs/12 §6: 'do not copy a constant from one generator to another
    without measuring'. The three happening to agree today is fine; what is not
    fine is one of them importing another's."""

    def test_each_generator_owns_its_own_page_constants(self):
        borrowed = []
        for path, own in (("backend/quote_generator.py", "Q_"),
                          ("backend/dn_generator.py", "DN_"),
                          ("backend/inv_generator.py", "INV_")):
            src = read(path)
            for other in ("quote_generator.", "dn_generator.", "inv_generator."):
                if other.split("_")[0].upper() + "_" == own:
                    continue
                for m in re.finditer(re.escape(other) + r"([A-Z_]+_MM)", src):
                    borrowed.append(f"{path} reads {other}{m.group(1)}")
        self.assertEqual(
            borrowed, [],
            "a generator is reading another type's measured geometry:\n  "
            + "\n  ".join(borrowed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
