"""Phase Y.2 - Eshraqat multi-page pagination must stay SOLID.

Guards the regression where the Eshraqat brand skin's unmodelled page chrome
(~20mm .esh-page bottom padding + a taller table header) made build_layout
force-fill tables past the printable box, spilling onto near-empty continuation
pages ("every other page is 3 rows"). See docs/12 §6.1.

For quote / DN / invoice at 90 wrapping rows we assert, on the rendered PDF:
  1. No near-empty spill page  - every non-last page has a text-line density
     within a healthy fraction of the median (a 3-row spill fails this).
  2. Every page carries a "Page X of Y" number (no blank/over-run pages).
  3. Sane page count - nowhere near the old ~2x doubling.
"""

import importlib
import shutil
import statistics
import sys
from pathlib import Path

import fitz  # PyMuPDF

APP_ROOT = Path(__file__).resolve().parents[1]
WORK = APP_ROOT / ".test-work" / "eshraqat-pagination-solidity"
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager

DESC = "Detailed supply and delivery line number {i} with enough text to wrap onto two lines"
N = 90


def _setup():
    mgr = ProfileManager(WORK / "data", APP_ROOT / "static")
    profile_manager._MANAGER = mgr
    mgr.create_registry("eshraqat")
    mgr.activate("eshraqat")
    mgr.ensure_seed_assets("eshraqat")
    mgr.assets_dir("eshraqat").mkdir(parents=True, exist_ok=True)
    mgr.signatures_dir("eshraqat").mkdir(parents=True, exist_ok=True)
    from backend import (brand_assets, config, eshraqat_render, signing,
                         quote_generator, dn_generator, inv_generator)
    for m in (config, brand_assets, eshraqat_render, signing,
              quote_generator, dn_generator, inv_generator):
        importlib.reload(m)
    return quote_generator, dn_generator, inv_generator


def _assert_solid(tag: str, pdf: Path) -> None:
    doc = fitz.open(pdf)
    line_counts, has_pageno = [], []
    for page in doc:
        text = page.get_text("text")
        lines = sum(len(b.get("lines", [])) for b in page.get_text("dict")["blocks"])
        line_counts.append(lines)
        has_pageno.append(("Page " in text) and (" of " in text))
    n = len(doc)
    doc.close()

    assert n >= 2, f"{tag}: expected a multi-page doc, got {n}"
    # 1. no near-empty spill page (non-last pages only)
    med = statistics.median(line_counts)
    spills = [i + 1 for i, c in enumerate(line_counts[:-1]) if med and c < med * 0.4]
    assert not spills, f"{tag}: near-empty spill page(s) {spills} (line-counts={line_counts})"
    # 2. every page numbered
    missing = [i + 1 for i, ok in enumerate(has_pageno) if not ok]
    assert not missing, f"{tag}: page(s) {missing} missing 'Page X of Y'"
    # 3. sane page count - old doubling for 90 items was ~18-19
    assert n <= 16, f"{tag}: {n} pages for {N} items looks like over-pagination"
    print(f"  {tag:8s}: {n} pages, line-counts={line_counts} -> SOLID")


def main() -> None:
    original = profile_manager._MANAGER
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    try:
        q, dn, inv = _setup()
        out = WORK / "out"
        out.mkdir(parents=True, exist_ok=True)

        quote = {"quote_no": "QT-0001", "date": "14/07/2026", "quote_type": "materials",
                 "client_name": "Horizon Energy", "rfq_no": "RFQ-1", "client_address": "Basra",
                 "currency": "USD", "terms": ["Currency: USD", "Validity: 30 days"],
                 "items": [{"item": f"Material {i}", "description": DESC.format(i=i),
                            "unit": "pcs", "qty": "10", "rate": "125.50"} for i in range(1, N + 1)],
                 "layout": {"autofit_rows": False}, "signing_overlay": {"slots": {}}}
        q.render_quote_pdf(quote, out / "quote.pdf")
        _assert_solid("quote", out / "quote.pdf")

        dnp = {"dn_no": "DN-0001", "date": "14/07/2026", "client_name": "Mesopotamia Eng",
               "lpo_no": "LPO-1", "client_address": "Basra", "include_prices": True,
               "items": [{"item": f"Part {i}", "description": DESC.format(i=i),
                          "unit": "pcs", "qty": "2", "rate": "7.5"} for i in range(1, N + 1)],
               "layout": {"autofit_rows": False}, "signing_overlay": {"slots": {}}}
        dn.render_dn_pdf(dnp, out / "dn.pdf")
        _assert_solid("dn", out / "dn.pdf")

        invp = {"inv_no": "IV-0001", "date": "14/07/2026", "inv_type": "materials",
                "client_name": "Client", "lpo_no": "LPO-1", "client_address": "Basra",
                "payment_bank_key": "cash", "withholding_included": True,
                "items": [{"item": f"Material {i}", "description": DESC.format(i=i),
                           "unit": "pcs", "qty": "10", "rate": "125.50"} for i in range(1, N + 1)],
                "layout": {"autofit_rows": False}, "signing_overlay": {}}
        inv.render_inv_pdf(invp, out / "inv.pdf")
        _assert_solid("invoice", out / "inv.pdf")
    finally:
        profile_manager._MANAGER = original
        shutil.rmtree(WORK, ignore_errors=True)

    print("Eshraqat pagination solidity tests passed")


if __name__ == "__main__":
    main()
