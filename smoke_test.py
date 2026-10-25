"""Quick offline check of the backend - runs without launching the UI.

This is the thing CLAUDE.md tells you to run before claiming a change is done,
so it has to be able to FAIL. Until 2026-09-05 it could not: it had no
assertions at all, and it wrapped the PDF step in a `try/except` that printed
the error and carried on, so a completely broken PDF renderer still exited 0.

It now checks, and exits non-zero on:
  - DB init
  - PO numbering scanning the right folder
  - HTML render producing a real document with the PO's own content in it
  - PDF render producing a file with pages that actually carry text
  - item memory answering a prefix search

It is still a SMOKE test - cheap, one document, "is the machine alive". It is
not a pagination check; that is `tests/layout_planned_matches_rendered.test.py`.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from backend import config, database, pdf_generator, po_numbering

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """Record a check. Never raises - we want the whole picture in one run,
    then a single non-zero exit at the end."""
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(' - ' + detail) if detail else ''}")
        FAILURES.append(label)


def main() -> int:
    print("== Smoke test ==")

    database.init_db()
    print(f"DB initialized at: {config.DB_PATH}")

    po_no, folder = po_numbering.get_next_po_number()
    print(f"Next PO: {po_no}  -> {folder}")
    check("PO numbering returns a number", bool(str(po_no).strip()), repr(po_no))
    check("PO numbering returns a folder", bool(str(folder).strip()), repr(folder))

    today = date.today()
    po_date = f"{today.day}/{today.month}/{today.year}"
    rows = [
        {"name": "سبلت جداري", "description": "طن ونص", "unit": "عدد", "qty": "1", "company": "TDC"},
        {"name": "ستاند خوذ سلامة", "description": "", "unit": "عدد", "qty": "4", "company": "TDC"},
        {"name": "رصيد زين", "description": "50الف", "unit": "عدد", "qty": "1", "company": "أبراج البدر"},
    ]

    # ---- HTML ----------------------------------------------------------
    html = pdf_generator.render_html(po_no, po_date, rows)
    out_html = config.DATA_DIR / "_smoke.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"HTML written: {out_html}")
    check("HTML is a real document", len(html) > 1000, f"{len(html)} chars")
    check("HTML carries the PO number", str(po_no) in html)
    check("HTML carries every item name",
          all(r["name"] in html for r in rows),
          "an item name is missing from the rendered table")

    # ---- PDF -----------------------------------------------------------
    # NOT wrapped in a bare except any more. A PDF failure is the single most
    # important thing this script exists to notice.
    out_pdf = config.DATA_DIR / "_smoke.pdf"
    try:
        if out_pdf.exists():
            out_pdf.unlink()
    except OSError as exc:                      # a viewer holding the old file
        print(f"  note: could not remove the previous {out_pdf.name}: {exc}")

    pdf_ok = True
    try:
        pdf_generator.render_pdf(po_no, po_date, rows, out_pdf)
    except Exception as exc:                    # noqa: BLE001 - reported, not swallowed
        pdf_ok = False
        check("PDF render", False, f"{type(exc).__name__}: {exc}")
    if pdf_ok:
        print(f"PDF written: {out_pdf}")
        check("PDF file exists", out_pdf.is_file(), str(out_pdf))
        size = out_pdf.stat().st_size if out_pdf.is_file() else 0
        check("PDF is not empty", size > 2000, f"{size} bytes")
        # A PDF can exist, be a plausible size and still be blank - so read it.
        try:
            import fitz  # PyMuPDF
        except ImportError:
            print("  note: PyMuPDF not installed - skipping the page/text check")
        else:
            with fitz.open(out_pdf) as doc:
                pages = doc.page_count
                text = "".join(p.get_text("text") for p in doc)
            check("PDF has at least one page", pages >= 1, f"{pages} pages")
            check("PDF pages carry text", len(text.strip()) > 50,
                  f"{len(text.strip())} characters of text")
            check("PDF carries the PO number", str(po_no) in text)

    # ---- item memory ---------------------------------------------------
    for r in rows:
        database.remember_item(r["name"], r["description"], r["unit"], r["company"])
    hits = database.suggest_items("س")
    check("item memory answers a prefix search", bool(hits), "no suggestions for 'س'")

    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED - {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
