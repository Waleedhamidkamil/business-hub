"""
Measure a document in the SAME browser that prints it (2026-10-11).

Why this exists
---------------
Every PO page-break bug so far had one cause in different clothes: the planner
GUESSED how tall a row would print. A-84 under-guessed typed line breaks, A-88
guessed auto-fit wrong, A-110 guessed Arabic glyph widths wrong, and A-129
booked every row with a picture at a flat 22mm while his wide, short sign
photos print at 10-13mm - seven over-booked rows split a half-empty page in two.
Each fix corrected one guess; the next document found the next one.

A browser does not guess. This module loads the finished HTML in headless
Chrome/Edge (the same engine and CSS as `pdf_engine.write_pdf`), waits for
fonts and pictures, and reads back, per page box:

    flow       the height content may use (min-height minus padding), mm
    header     content top -> top of the items table, mm
    thead      the table header row, mm
    rows       every body row's real height, mm
    closeout   content bottom -> top of the signature/closing block, INCLUDING
               any stamp or signature image that sits above it, mm
    pagenum    content bottom -> top of the page number (0 when it sits in the
               padding, below the content), mm
    grown      how far the box grew past its min-height (overflow), mm
    table_bottom / closeout_top / pagenum_top   for the overlap checks, mm

Everything is millimetres measured from the page box's content top, so a
caller can plan with the numbers and check a finished layout with the same
call. Returns None if no browser can be run - callers fall back to their
estimate rather than failing a save.
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from . import pdf_engine

# Runs inside the page once fonts and every picture are ready, then replaces the
# document with the JSON so --dump-dom prints a few hundred bytes instead of
# megabytes of base64 pictures.
_SCRIPT = r"""
<script>
(function () {
  var SPEC = __SPEC__;
  var MM = 25.4 / 96;
  function q(root, sels) {
    for (var i = 0; i < sels.length; i++) { var e = root.querySelector(sels[i]); if (e) return e; }
    return null;
  }
  function measure() {
    var out = [];
    var pages = document.querySelectorAll(SPEC.page);
    for (var p = 0; p < pages.length; p++) {
      var pg = pages[p], cs = getComputedStyle(pg), r = pg.getBoundingClientRect();
      var pt = parseFloat(cs.paddingTop) || 0, pb = parseFloat(cs.paddingBottom) || 0;
      var minH = parseFloat(cs.minHeight) || r.height;
      var contentTop = r.top + pt, contentBottom = r.bottom - pb;
      var res = {flow: (minH - pt - pb) * MM, grown: Math.max(0, r.height - minH) * MM,
                 rows: [], header: 0, thead: 0, table_bottom: 0,
                 closeout: null, closeout_top: null, pagenum: 0, pagenum_top: null};
      var t = pg.querySelector(SPEC.table);
      if (t) {
        var tr = t.getBoundingClientRect();
        res.header = (tr.top - contentTop) * MM;
        res.table_bottom = (tr.bottom - contentTop) * MM;
        if (t.tHead) res.thead = t.tHead.getBoundingClientRect().height * MM;
        var body = t.tBodies[0] ? t.tBodies[0].rows : [];
        for (var i = 0; i < body.length; i++) {
          if (getComputedStyle(body[i]).display === "none") continue;
          res.rows.push(body[i].getBoundingClientRect().height * MM);
        }
      }
      var c = q(pg, SPEC.closeout);
      if (c) {
        var top = c.getBoundingClientRect().top;
        var imgs = c.querySelectorAll("img");
        for (var k = 0; k < imgs.length; k++) {
          var ir = imgs[k].getBoundingClientRect();
          if (ir.height > 0) top = Math.min(top, ir.top);
        }
        res.closeout = (contentBottom - top) * MM;
        res.closeout_top = (top - contentTop) * MM;
      }
      var n = q(pg, SPEC.pagenum);
      if (n) {
        var nt = n.getBoundingClientRect().top;
        res.pagenum = Math.max(0, (contentBottom - nt) * MM);
        res.pagenum_top = (nt - contentTop) * MM;
      }
      out.push(res);
    }
    document.documentElement.innerHTML =
      '<body><pre id="__measure">' + JSON.stringify(out) + '</pre></body>';
  }
  /* Synchronously, inside the load event: by then every picture (they are all
     data URIs) has loaded, the print CSS uses installed fonts only, and
     getBoundingClientRect forces a full layout. NOT on an animation frame or a
     promise - --dump-dom can serialise the page before either runs, which made
     roughly half of all measurements come back empty (2026-10-11). */
  if (document.readyState === "complete") measure(); else addEventListener("load", measure);
})();
</script>
"""

_RESULT = re.compile(r'<pre id="__measure">(.*?)</pre>', re.S)


def measure(document_html: str, spec: dict, timeout: float = 45.0) -> list[dict] | None:
    """Lay `document_html` out and return one dict per page box, or None.

    Tries twice: a browser that is starting up or briefly starved of CPU can
    return nothing once, and a planner that silently falls back to the
    estimate is exactly what this module exists to avoid."""
    for _attempt in range(2):
        result = _measure_once(document_html, spec, timeout)
        if result is not None:
            return result
    return None


def _measure_once(document_html: str, spec: dict, timeout: float) -> list[dict] | None:
    """Lay `document_html` out and return one dict per page box (see the module
    docstring), or None when the browser cannot be run or reports nothing.

    `spec` = {"page": css selector for a page box,
              "table": selector for the items table inside it,
              "closeout": [selectors for the signature / closing block],
              "pagenum": [selectors for the page number]}
    """
    try:
        browser = pdf_engine.find_browser()
    except Exception:
        return None
    script = _SCRIPT.replace("__SPEC__", json.dumps(spec))
    page = (document_html.replace("</body>", script + "</body>", 1)
            if "</body>" in document_html else document_html + script)

    workdir = Path(tempfile.gettempdir()) / f"po_tool_measure_{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        src = workdir / "doc.html"
        src.write_text(page, encoding="utf-8")
        cmd = [str(browser), "--headless=new", "--disable-gpu", "--no-sandbox",
               "--hide-scrollbars", "--window-size=1400,2000",
               f"--user-data-dir={workdir / 'udd'}",
               "--virtual-time-budget=15000", "--dump-dom", src.as_uri()]
        flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout, creationflags=flags)
        except (subprocess.TimeoutExpired, OSError):
            return None
        m = _RESULT.search(res.stdout or "")
        if not m:
            return None
        try:
            data = json.loads(_html.unescape(m.group(1)))
        except ValueError:
            return None
        return data if isinstance(data, list) and data else None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
