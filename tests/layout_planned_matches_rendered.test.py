# -*- coding: utf-8 -*-
"""THE constitution's one executable invariant: planned pages == rendered pages.

Every document type, both brand skins (2026-09-05).

Why this file exists
--------------------
`docs/12-PDF-LAYOUT-CONSTITUTION.md` has been right for a month and could not
enforce anything. §2 already said the engine constants and the print CSS are "a
coupled pair" and that changing a CSS row height "silently invalidates the
engine's mm math" - and on 2026-09-05 exactly that happened to the PO: A-84
planned two pages, printed three, and went to a supplier carrying footers that
read "Page 1 of 2" on a three-sheet document. The rule was written down,
correct, and ignored, because prose cannot check itself.

This is the check. It is deliberately the crudest, most end-to-end assertion
available, because it is the one the reader of the PDF actually experiences:

    the number of pages the engine BOOKED must equal the number the browser DREW

Everything the constitution protects reduces to that. A row that renders taller
than it was booked overflows and adds a page. A page area that forgets a skin's
chrome overflows and adds a page. A last page that stretches into the signature
band overflows and adds a page. Each of those shows up here as a mismatch, on a
real PDF, without this file needing to know which one went wrong.

How it measures
---------------
It does NOT rebuild the generator's arguments and call `build_layout` itself -
that would test this file's reconstruction of the call, and would keep passing
if the renderer started passing something different. Instead it **spies on the
generator's own `build_layout`** (and `paginate_po`), lets `render_*_pdf` run
completely normally, and reads back the page count that real call produced.

Real Chromium and real PyMuPDF. A stubbed layout would only re-assert the
arithmetic that was already wrong.

Cost: this renders real PDFs, so it is slower than the rest of the suite. Run it
whenever pagination, a print stylesheet, or a page-area constant changes - see
the protocol in docs/12 §8.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import pdf_engine  # noqa: E402

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None


# A description long enough to wrap. Wrapping is where an under-booked row turns
# into an overflowing page, so a fixture of short cells would prove very little.
DESC = ("Detailed supply and delivery of line item number {i}, with enough "
        "text on this line to wrap onto a second line in the printed table")


@contextmanager
def spy_pages(module, attr):
    """Record the page count the generator's OWN layout call produced."""
    seen = []
    original = getattr(module, attr)

    def wrapper(*a, **kw):
        pages = original(*a, **kw)
        seen.append(len(pages))
        return pages

    setattr(module, attr, wrapper)
    try:
        yield seen
    finally:
        setattr(module, attr, original)


# --------------------------------------------------------------------------- #
# One builder per document type: (module, spy attr, make doc, render fn name)
# --------------------------------------------------------------------------- #

def _quote_doc(n):
    from backend import config
    return {"quote_no": "ZZ-QUT-LAYOUT", "date": "5/9/2026", "quote_type": "materials",
            "client_name": "ZZ Layout Test", "rfq_no": "N/A",
            "client_address": "Site", "currency": "USD",
            "terms": config.QUOTE_TYPES["materials"]["terms"],
            "items": [{"item": f"Material {i}", "description": DESC.format(i=i),
                       "unit": "pcs", "qty": "10", "rate": "125.50"}
                      for i in range(1, n + 1)],
            "layout": {"autofit_rows": False}, "signing_overlay": {"slots": {}}}


def _dn_doc(n):
    return {"dn_no": "ZZ-DN-LAYOUT", "date": "5/9/2026",
            "client_name": "ZZ Layout Test", "lpo_no": "N/A",
            "client_address": "Site", "include_prices": True,
            "items": [{"item": f"Part {i}", "description": DESC.format(i=i),
                       "unit": "pcs", "qty": "2", "rate": "7.5"}
                      for i in range(1, n + 1)],
            "layout": {"autofit_rows": False}, "signing_overlay": {"slots": {}}}


def _inv_doc(n):
    return {"inv_no": "ZZ-INV-LAYOUT", "date": "5/9/2026", "inv_type": "materials",
            "client_name": "ZZ Layout Test", "lpo_no": "N/A",
            "client_address": "Site", "payment_bank_key": "cash",
            "withholding_included": True,
            "items": [{"item": f"Material {i}", "description": DESC.format(i=i),
                       "unit": "pcs", "qty": "10", "rate": "125.50"}
                      for i in range(1, n + 1)],
            "layout": {"autofit_rows": False}, "signing_overlay": {}}


def _po_rows(n):
    return [{"name": f"مادة {i}", "description": "وصف تفصيلي للمادة يمتد لسطرين",
             "unit": "كيلو", "qty": "10", "company": "TDC", "image": ""}
            for i in range(1, n + 1)]


TYPES = {
    "quote": {"mod": "quote_generator", "spy": "build_layout",
              "doc": _quote_doc, "render": "render_quote_pdf"},
    "dn":    {"mod": "dn_generator", "spy": "build_layout",
              "doc": _dn_doc, "render": "render_dn_pdf"},
    "inv":   {"mod": "inv_generator", "spy": "build_layout",
              "doc": _inv_doc, "render": "render_inv_pdf"},
    "po":    {"mod": "pdf_generator", "spy": "paginate_po",
              "doc": _po_rows, "render": "render_pdf"},
}


class PlannedMatchesRendered(unittest.TestCase):
    """The invariant, for every type, on the default (Abraj) skin."""

    #: Counts chosen to sit either side of the 1->2 and 2->3 page breaks, where
    #: a mis-booked row first becomes visible. A count in the middle of a page
    #: cannot fail, so testing many of those would only cost time.
    COUNTS = (1, 8, 12, 16, 20, 26, 40)

    @classmethod
    def setUpClass(cls):
        try:
            pdf_engine.find_browser()
        except pdf_engine.PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))
        if fitz is None:
            raise unittest.SkipTest("PyMuPDF not installed")
        cls.work = Path(tempfile.mkdtemp(prefix="layout_planned_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def _render(self, kind, n):
        """(planned, rendered) for `n` items of this type, via the REAL path."""
        spec = TYPES[kind]
        mod = importlib.import_module(f"backend.{spec['mod']}")
        out = self.work / f"{kind}-{n}.pdf"
        doc = spec["doc"](n)
        with spy_pages(mod, spec["spy"]) as seen:
            if kind == "po":
                # PO's renderer takes the rows positionally, not a doc dict.
                getattr(mod, spec["render"])("ZZ-PO-LAYOUT", "5/9/2026", doc, out)
            else:
                getattr(mod, spec["render"])(doc, out)
        self.assertTrue(seen, f"{kind}: the generator never called {spec['spy']} "
                              "- this test is measuring nothing")
        with fitz.open(out) as pdf:
            rendered = pdf.page_count
        return max(seen), rendered

    def _check(self, kind):
        bad = []
        for n in self.COUNTS:
            planned, rendered = self._render(kind, n)
            if planned != rendered:
                bad.append(f"{n} items: booked {planned} page(s), browser drew {rendered}")
        self.assertEqual(
            bad, [],
            f"\n{kind}: the engine and the browser disagree about the page count.\n"
            "  Booked < drawn means a row renders taller than it was reserved - the\n"
            "  document overflows and its own 'Page X of Y' footers are wrong (this is\n"
            "  the A-84 defect). Booked > drawn means space is reserved and wasted, and\n"
            "  the footers are wrong in the other direction.\n  "
            + "\n  ".join(bad))

    def test_quote(self):
        self._check("quote")

    def test_delivery_note(self):
        self._check("dn")

    def test_invoice(self):
        self._check("inv")

    def test_purchase_order(self):
        self._check("po")


class PlannedMatchesRenderedEshraqat(PlannedMatchesRendered):
    """The same invariant under the ESHRAQAT brand skin.

    This is where page geometry is most likely to drift, and where it has
    drifted before: the skin adds ~20mm of `.esh-page` bottom padding and a
    taller table header that the Abraj-tuned constants never modelled, which is
    what `*_ESH_PAGE_CHROME_MM = 34` absorbs (docs/12 §6.1). A single measured
    lump like that is exactly the kind of number that goes stale silently - the
    symptom was "every other page is 3 rows".

    `tests/eshraqat_pagination_solidity.test.py` already checks this skin, but
    with softer assertions (page density, "Page X of Y" present, page count
    under a ceiling). This asserts the hard thing: booked == drawn.

    The whole module is re-imported under a temporary eshraqat profile, because
    `config.ACTIVE_PROFILE` is read at import time by the generators' constants.
    """

    #: Fewer counts than the Abraj run - each one is a real PDF render, and the
    #: skin's failure mode (unmodelled per-page chrome) shows up on ANY
    #: multi-page document rather than only at a boundary.
    COUNTS = (1, 12, 30)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import importlib
        from backend import profile_manager
        from backend.profile_manager import ProfileManager

        cls._saved_manager = profile_manager._MANAGER
        cls._profile_root = cls.work / "eshraqat-profile"
        try:
            mgr = ProfileManager(cls._profile_root / "data", APP_ROOT / "static")
            profile_manager._MANAGER = mgr
            mgr.create_registry("eshraqat")
            mgr.activate("eshraqat")
            mgr.ensure_seed_assets("eshraqat")
            mgr.assets_dir("eshraqat").mkdir(parents=True, exist_ok=True)
            mgr.signatures_dir("eshraqat").mkdir(parents=True, exist_ok=True)
            from backend import (brand_assets, config, dn_generator,
                                 eshraqat_render, inv_generator, pdf_generator,
                                 quote_generator, signing)
            for m in (config, brand_assets, eshraqat_render, signing,
                      quote_generator, dn_generator, inv_generator, pdf_generator):
                importlib.reload(m)
            assert config.ACTIVE_PROFILE == "eshraqat", config.ACTIVE_PROFILE
        except Exception as exc:            # pragma: no cover
            profile_manager._MANAGER = cls._saved_manager
            raise unittest.SkipTest(f"could not activate an eshraqat profile: {exc}")

    @classmethod
    def tearDownClass(cls):
        """Put the real profile back - and RELOAD, or every test that runs
        after this one in the same process renders the wrong brand. The board
        records that a whole-suite run has left the registry on eshraqat before
        and six unrelated tests failed for that reason alone."""
        import importlib
        from backend import profile_manager
        profile_manager._MANAGER = cls._saved_manager
        try:
            from backend import (brand_assets, config, dn_generator,
                                 eshraqat_render, inv_generator, pdf_generator,
                                 quote_generator, signing)
            for m in (config, brand_assets, eshraqat_render, signing,
                      quote_generator, dn_generator, inv_generator, pdf_generator):
                importlib.reload(m)
        except Exception:
            pass
        super().tearDownClass()


if __name__ == "__main__":
    unittest.main(verbosity=2)
