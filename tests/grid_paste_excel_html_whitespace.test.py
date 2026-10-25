"""Excel's pretty-printed clipboard HTML must not become cell newlines."""
from __future__ import annotations

import html
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


class GridPasteExcelHtmlWhitespaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            browser = find_browser()
        except PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))

        cls.work = Path(tempfile.mkdtemp(prefix="po_excel_clipboard_"))
        user_data = cls.work / "browser-profile"
        user_data.mkdir()
        grid_paste = (ROOT / "static" / "grid_paste.js").as_uri()
        page = cls.work / "fixture.html"
        page.write_text(
            f"""<!doctype html>
<html><body>
<pre id="result"></pre>
<script src="{grid_paste}"></script>
<script>
  const excelValues = [
    "Celery", "Eggplant", "Potato (Big size)", "Green Capsicum",
    "Carrot (Big size)", "Normal lettuce Romaine", "Sweet Melon",
    "White Radish (Made in china)", "Banana", "Chinese Cabbage",
    "Broccoli", "Red Onion", "Water Melon", "Zucchine (KOOSA)",
    "Cabbage", "White Mushroom", "Orange", "Cucumber", "Red Cabbage",
    "Green Chili", "Tomato", "Curly flower", "Long Green Bean",
    "Yellow Pumpkins", "Sweet Potato", "PEACH", "PEAR", "MANDARIN",
    "fresh BLACKPINK figs", "MELONE(muskmelon)", "Iraqi Yellow Plum",
    "（Black & Red Plums）",
  ];
  // Inner HTML captured from Excel's real A1:A32 CF_HTML payload. Excel adds
  // CRLF + indentation to several one-line values purely to format its source.
  const excelCells = [
    "Celery", "Eggplant", "Potato (Big\\r\\n  size)", "Green\\r\\n  Capsicum",
    "Carrot (Big\\r\\n  size)", "Normal\\r\\n  lettuce Romaine", "Sweet Melon",
    "White Radish\\r\\n  (Made in china)", "Banana", "Chinese\\r\\n  Cabbage",
    "Broccoli", "Red Onion", "Water Melon",
    "Zucchine\\r\\n  (KOOSA)<span style='mso-spacerun:yes'>&nbsp;</span>",
    "Cabbage", "White\\r\\n  Mushroom", "Orange", "Cucumber", "Red Cabbage",
    "Green Chili", "Tomato", "Curly flower", "Long Green\\r\\n  Bean",
    "Yellow\\r\\n  Pumpkins", "Sweet Potato", "PEACH", "PEAR", "MANDARIN",
    "fresh\\r\\n  BLACKPINK figs", "MELONE(muskmelon)",
    "Iraqi Yellow\\r\\n  Plum", "（Black &amp;\\r\\n  Red Plums）",
  ];
  const excelHtml = `<table>${{excelCells.map(v => `<tr><td>${{v}}</td></tr>`).join("")}}</table>`;
  const excelText = excelValues.join("\\r\\n") + "\\r\\n";
  const intentionalHtml = `<table><tr>
    <td>Line one<br>Line two</td>
    <td><div>Block one</div><div>Block two</div></td>
  </tr></table>`;
  const structuralEdges = `<table><tr>
    <td><div>First<div>Second</div></div></td>
    <td>A<br><br>B</td>
    <td>before<div>middle</div>after</td>
    <td>literal  marker</td>
  </tr></table>`;
  document.getElementById("result").textContent = JSON.stringify({{
    excel: window.parseClipboard(excelHtml, excelText),
    intentional: window.parseClipboardHtml(intentionalHtml),
    structuralEdges: window.parseClipboardHtml(structuralEdges),
    htmlPreferred: window.parseClipboard(
      "<table><tr><td>One<br>cell</td></tr></table>",
      "One\\r\\ncell\\r\\n",
    ),
  }});
</script>
</body></html>""",
            encoding="utf-8",
        )

        creationflags = 0x08000000 if sys.platform == "win32" else 0
        process = subprocess.run(
            [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--no-first-run",
                "--no-sandbox",
                f"--user-data-dir={user_data}",
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=creationflags,
        )
        match = re.search(r'<pre id="result">(.*?)</pre>', process.stdout, re.S)
        if process.returncode or not match:
            raise RuntimeError(
                "Chromium clipboard fixture failed: "
                + (process.stderr or process.stdout)[-1000:]
            )
        cls.result = json.loads(html.unescape(match.group(1)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "work", ""), ignore_errors=True)

    def test_excel_source_formatting_whitespace_is_collapsed_inside_each_cell(self):
        self.assertEqual(
            self.result["excel"],
            [
                ["Celery"], ["Eggplant"], ["Potato (Big size)"],
                ["Green Capsicum"], ["Carrot (Big size)"],
                ["Normal lettuce Romaine"], ["Sweet Melon"],
                ["White Radish (Made in china)"], ["Banana"],
                ["Chinese Cabbage"], ["Broccoli"], ["Red Onion"],
                ["Water Melon"], ["Zucchine (KOOSA)"], ["Cabbage"],
                ["White Mushroom"], ["Orange"], ["Cucumber"],
                ["Red Cabbage"], ["Green Chili"], ["Tomato"],
                ["Curly flower"], ["Long Green Bean"], ["Yellow Pumpkins"],
                ["Sweet Potato"], ["PEACH"], ["PEAR"], ["MANDARIN"],
                ["fresh BLACKPINK figs"], ["MELONE(muskmelon)"],
                ["Iraqi Yellow Plum"], ["（Black & Red Plums）"],
            ],
        )

    def test_real_html_breaks_remain_intentional_cell_newlines(self):
        self.assertEqual(
            self.result["intentional"],
            [["Line one\nLine two", "Block one\nBlock two"]],
        )

    def test_nested_repeated_and_literal_break_edges_are_preserved(self):
        self.assertEqual(
            self.result["structuralEdges"],
            [[
                "First\nSecond",
                "A\n\nB",
                "before\nmiddle\nafter",
                "literal \ue000 marker",
            ]],
        )

    def test_normal_parser_prefers_html_for_an_ambiguous_multiline_cell(self):
        self.assertEqual(self.result["htmlPreferred"], [["One\ncell"]])


if __name__ == "__main__":
    unittest.main()
