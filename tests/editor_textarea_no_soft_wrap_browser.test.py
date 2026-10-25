"""Rendered Chromium contract for non-wrapping document-editor textareas."""
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


class EditorTextareaNoSoftWrapBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            browser = find_browser()
        except PdfEngineNotFound as exc:
            raise unittest.SkipTest(str(exc))

        cls.work = Path(tempfile.mkdtemp(prefix="po_editor_wrap_"))
        user_data = cls.work / "browser-profile"
        user_data.mkdir()
        stylesheet = (ROOT / "static" / "app_styles.css").as_uri()
        long_value = "Industrial stainless steel pressure control assembly " * 4
        editors = (
            ("po", "items-table"),
            ("quote", "q-edit-table"),
            ("dn", "dn-edit-table"),
            ("invoice", "inv-edit-table"),
        )
        fixtures = "\n".join(
            f'<div class="fixture"><table class="{table_class}"><tbody><tr><td>'
            f'<textarea id="{editor_id}" rows="1">{html.escape(long_value)}</textarea>'
            "</td></tr></tbody></table></div>"
            for editor_id, table_class in editors
        )
        page = cls.work / "fixture.html"
        page.write_text(
            f"""<!doctype html>
<html><head>
<link rel="stylesheet" href="{stylesheet}">
<style>
  .fixture {{ width: 160px; }}
  .fixture table {{ width: 160px !important; table-layout: fixed; }}
  .fixture td {{ width: 160px !important; padding: 0 !important; }}
</style>
</head><body>
{fixtures}
<pre id="result"></pre>
<script>
  const result = {{}};
  for (const id of ["po", "quote", "dn", "invoice"]) {{
    const textarea = document.getElementById(id);
    const size = () => {{
      textarea.style.height = "auto";
      textarea.style.height = (textarea.scrollHeight + 1) + "px";
    }};
    size();
    const style = getComputedStyle(textarea);
    result[id] = {{
      whiteSpace: style.whiteSpace,
      overflowX: style.overflowX,
      oneLineClientHeight: textarea.clientHeight,
      oneLineScrollHeight: textarea.scrollHeight,
      clientWidth: textarea.clientWidth,
      scrollWidth: textarea.scrollWidth,
    }};
    textarea.value = "alpha\\nbeta";
    size();
    result[id].twoLineClientHeight = textarea.clientHeight;
  }}
  document.getElementById("result").textContent = JSON.stringify(result);
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
                "Chromium layout fixture failed: "
                + (process.stderr or process.stdout)[-1000:]
            )
        cls.metrics = json.loads(html.unescape(match.group(1)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "work", ""), ignore_errors=True)

    def test_all_four_editors_render_without_soft_wrap_or_vertical_clipping(self):
        for editor, metrics in self.metrics.items():
            with self.subTest(editor=editor):
                self.assertEqual(metrics["whiteSpace"], "pre")
                self.assertEqual(metrics["overflowX"], "auto")
                self.assertGreater(metrics["scrollWidth"], metrics["clientWidth"])
                self.assertLessEqual(
                    metrics["oneLineScrollHeight"],
                    metrics["oneLineClientHeight"] + 1,
                    f"{editor} loses vertical room to its horizontal scrollbar",
                )
                self.assertGreater(
                    metrics["twoLineClientHeight"],
                    metrics["oneLineClientHeight"],
                    f"{editor} must still grow for an explicit newline",
                )


if __name__ == "__main__":
    unittest.main()
