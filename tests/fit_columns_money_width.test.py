"""Fit columns must never leave a money value wrapped (2026-09-02).

Reported from a real quotation (QUT-MT-HDEC-063): after pressing Fit, the RATE
column printed

    $4,300
    .00

while TOTAL, holding the larger $25,800.00, sat on one line.

The asymmetry is the whole clue. A computed TOTAL cell already contains the
FORMATTED string, so measuring the cell measured what prints. A RATE cell is an
<input> holding the RAW value the user typed - "4300" - so the fit reserved room
for four characters and the page then printed nine. The header floor that keeps
the word "RATE" whole did nothing for the number underneath it.

The fix gives money columns a VALUE floor: format the widest value the way the
generator does and measure THAT at the PRINT body font.

Real Chromium on purpose - the fit is canvas text measurement, so a stubbed
measureText would test arithmetic against invented font metrics and prove
nothing about whether $4,300.00 actually fits.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.pdf_engine import PdfEngineNotFound, find_browser  # noqa: E402

# The print geometry the quotation/DN/invoice editors pass to the fit, and the
# body font of `.q-table` in static/quote_styles.css.
WIDTH_MM = 186
HEADER_FONT = 'bold 10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif'
BODY_FONT = '10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif'


class FitColumnsMoneyWidthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            browser = find_browser()
        except PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))

        cls.work = Path(tempfile.mkdtemp(prefix="po_fit_money_"))
        user_data = cls.work / "browser-profile"
        user_data.mkdir()
        col_resize = (ROOT / "static" / "col_resize.js").as_uri()
        page = cls.work / "fixture.html"
        page.write_text(
            f"""<!doctype html>
<html><body>
<style>
 table{{width:1100px;table-layout:fixed;border-collapse:collapse;font:13px system-ui}}
 th,td{{border:1px solid #ccc;padding:2px 4px}}
 textarea,input{{width:100%;box-sizing:border-box;font:13px system-ui;border:0}}
</style>
<table id="t"><thead id="th"><tr>
 <th data-col-key="item" style="width:16%">ITEM</th>
 <th data-col-key="description" style="width:22%">DESCRIPTION</th>
 <th data-col-key="unit" style="width:6%">UNIT</th>
 <th data-col-key="qty" style="width:6%">QTY</th>
 <th data-col-key="rate" style="width:12%">RATE</th>
 <th data-col-key="total" style="width:12%">TOTAL</th>
</tr></thead><tbody id="tb">
<tr data-i="0">
 <td><textarea data-k="item">GUARD HOUSE</textarea></td>
 <td><textarea data-k="description">- Type : Portacabin type
- Height : 2,250mm
- Width : 2,000mm
- Length : 3,000mm
- Foundation block
- 4 Pcs per Guard House
- 400mm x 400mm x 200mm (Height)
- AC included</textarea></td>
 <td><textarea data-k="unit">EA</textarea></td>
 <td><input data-k="qty" value="6"></td>
 <td><input data-k="rate" value="4300"></td>
 <td class="qc-total" data-k="total">$25,800.00</td>
</tr>
</tbody></table>
<pre id="result"></pre>
<script src="{col_resize}"></script>
<script>
 const PDF = {{ widthMm: {WIDTH_MM}, headerFont: {HEADER_FONT!r}, bodyFont: {BODY_FONT!r} }};
 const START = ["16%","22%","6%","6%","12%","12%"];
 const printPx = {WIDTH_MM} / 25.4 * 96;

 function reset() {{
   document.querySelectorAll("#th th").forEach((th,i) => th.style.width = START[i]);
 }}
 function widths(opts) {{
   reset();
   window.fitEditorColumns("#th", opts);
   const o = {{}};
   document.querySelectorAll("#th th").forEach(th => o[th.dataset.colKey] = parseFloat(th.style.width));
   return o;
 }}
 // Would this string survive on ONE line at the given column %?
 function fits(pct, text, font) {{
   const c = document.createElement("canvas").getContext("2d");
   c.font = font;
   return (c.measureText(text).width + 10) <= (pct / 100 * printPx);
 }}

 const before = widths({{ pdf: PDF }});
 const after  = widths({{ pdf: PDF, money: new Set(["rate","total"]) }});
 // A single huge value must not be allowed to swallow the table.
 document.querySelector('[data-k="rate"]').value = "999999999999";
 const huge = widths({{ pdf: PDF, money: new Set(["rate","total"]) }});

 document.getElementById("result").textContent = JSON.stringify({{
   before, after, huge,
   rateFitsBefore: fits(before.rate, "$4,300.00", PDF.bodyFont),
   rateFitsAfter:  fits(after.rate,  "$4,300.00", PDF.bodyFont),
   totalFitsAfter: fits(after.total, "$25,800.00", PDF.bodyFont),
   sumBefore: Object.values(before).reduce((a,b)=>a+b,0),
   sumAfter:  Object.values(after).reduce((a,b)=>a+b,0),
 }});
</script>
</body></html>""",
            encoding="utf-8",
        )

        creationflags = 0x08000000 if sys.platform == "win32" else 0
        process = subprocess.run(
            [
                str(browser), "--headless=new", "--disable-gpu",
                "--disable-background-networking", "--no-first-run", "--no-sandbox",
                f"--user-data-dir={user_data}", "--dump-dom", page.as_uri(),
            ],
            capture_output=True, text=True, timeout=90, creationflags=creationflags,
        )
        match = re.search(r'<pre id="result">(.*?)</pre>', process.stdout, re.S)
        if process.returncode or not match:
            raise unittest.SkipTest(
                f"headless browser produced no result (rc={process.returncode})")
        import html as _html
        cls.out = json.loads(_html.unescape(match.group(1)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_the_reported_bug_reproduces_without_the_value_floor(self):
        """Without `money`, RATE is sized from the raw "4300" and $4,300.00 wraps."""
        self.assertFalse(
            self.out["rateFitsBefore"],
            "the reported bug no longer reproduces - this test would then pass "
            "for the wrong reason; check the fixture still mirrors the real table")

    def test_money_value_fits_on_one_line_after_the_fix(self):
        self.assertTrue(
            self.out["rateFitsAfter"],
            f"$4,300.00 still wraps: RATE got {self.out['after']['rate']}% "
            f"(was {self.out['before']['rate']}%)")

    def test_the_wider_total_also_fits(self):
        self.assertTrue(self.out["totalFitsAfter"],
                        "$25,800.00 must keep fitting - it did before the change")

    def test_rate_grew_and_the_text_columns_paid_for_it(self):
        before, after = self.out["before"], self.out["after"]
        self.assertGreater(after["rate"], before["rate"],
                           "the value floor must widen RATE")
        # Text wraps gracefully; money does not. The room must come from there.
        self.assertLess(after["description"], before["description"],
                        "DESCRIPTION should give up the width, not a money column")

    def test_the_table_still_sums_to_the_same_width(self):
        """The fit redistributes; it must never change the flex columns' total,
        or the table stops being 100% wide and the PDF layout shifts."""
        self.assertAlmostEqual(self.out["sumBefore"], self.out["sumAfter"], places=1)

    def test_one_absurd_number_cannot_swallow_the_table(self):
        """MONEY_CAP_FRAC - a 12-digit rate may claim more room, but not the page."""
        self.assertLessEqual(
            self.out["huge"]["rate"], 30.0,
            f"a 12-digit rate took {self.out['huge']['rate']}% of the table")
        self.assertGreater(self.out["huge"]["description"], 5.0,
                           "DESCRIPTION must survive an absurd money value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
