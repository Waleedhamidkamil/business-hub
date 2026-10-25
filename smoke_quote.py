"""Render one quotation of each type to verify the schema-driven generator.

Like `smoke_test.py`, this had no assertions until 2026-09-05 - it printed and
exited 0 whatever came out. It now checks that every quote type produces a real
PDF whose pages carry text, and exits non-zero when one does not.

It also no longer opens the PDFs in a viewer by default. That was how a previous
run wedged itself: the open `_q_equipment.pdf` could not be overwritten on the
next run (AGENT-BOARD, 2026-08-25). Pass `--open` if you want to eyeball them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import config, quote_generator as qg  # noqa: E402

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"    ok   {label}")
    else:
        print(f"    FAIL {label}{(' - ' + detail) if detail else ''}")
        FAILURES.append(label)


def base(qtype, code, items):
    return {"quote_no": f"QUT-HDEC-{code}-1", "date": "29/5/2026", "quote_type": qtype,
            "client_name": "HDEC", "rfq_no": "N/A", "client_address": "CSSP Site",
            "items": items, "terms": config.QUOTE_TYPES[qtype]["terms"]}


LONGSPEC = ("Caterpillar 320D hydraulic excavator, 20-ton class, with breaker "
            "attachment, GPS, and certified operator included for the full term.")

cases = {
    "_q_materials.pdf": base("materials", "MT", [
        {"item": "Cement Bag 50kg", "description": "Grade 42.5", "unit": "Bag", "qty": "100", "rate": "8.5"},
        {"item": "Steel Rebar 16mm", "description": "Deformed bar, grade 60", "unit": "Ton", "qty": "5", "rate": "720"},
    ]),
    "_q_equipment.pdf": base("equipment", "EQ", [
        {"equipment": "Excavator 20T", "specs": LONGSPEC, "rate": "4500"},
        {"equipment": "Dump Truck", "specs": "30-ton tipper, Euro 5, driver included", "rate": "2800"},
        {"equipment": "Mobile Crane", "specs": "50-ton telescopic boom", "rate": "6200"},
    ]),
    "_q_manpower.pdf": base("manpower", "MP", [
        {"role": "Site Civil Engineer (5+ yrs experience)", "unit": "Per Month", "rate": "2200"},
        {"role": "Skilled Steel Fixer", "unit": "Per Hour", "rate": "6.5"},
        {"role": "General Labourer", "unit": "Per Hour", "rate": "3.2"},
    ]),
    "_q_service.pdf": base("service", "SV", [
        {"description": "Geotechnical soil investigation, 10 boreholes to 15m depth", "rate": "8500", "remarks": "Report in 10 days"},
        {"description": "Topographic survey of 5-hectare site", "rate": "3200", "remarks": "Includes CAD drawings"},
    ]),
}


def main() -> int:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None
        print("note: PyMuPDF not installed - page/text checks will be skipped")

    for fname, quote in cases.items():
        out = config.DATA_DIR / fname
        schema = config.QUOTE_TYPES[quote["quote_type"]]
        pages = qg.build_layout(quote["items"], quote["terms"], schema)
        try:
            if out.exists():
                out.unlink()
        except OSError as exc:
            print(f"  note: could not remove the previous {fname}: {exc}")

        print(f"{fname}: type={quote['quote_type']:9} cols={len(schema['columns'])} "
              f"grand={schema['grand_total']} -> {len(pages)} page(s) planned")
        try:
            qg.render_quote_pdf(quote, out)
        except Exception as exc:                # noqa: BLE001 - reported, not swallowed
            check(f"{fname} renders", False, f"{type(exc).__name__}: {exc}")
            continue

        check(f"{fname} exists", out.is_file())
        size = out.stat().st_size if out.is_file() else 0
        check(f"{fname} is not empty", size > 2000, f"{size} bytes")
        if fitz and out.is_file():
            with fitz.open(out) as doc:
                drawn = doc.page_count
                text = "".join(p.get_text("text") for p in doc)
            check(f"{fname} has pages", drawn >= 1, f"{drawn}")
            check(f"{fname} carries text", len(text.strip()) > 50,
                  f"{len(text.strip())} chars")
            # The quote number is the one thing every type must print.
            check(f"{fname} carries the quote number", quote["quote_no"] in text)
            # And what was planned is what must be drawn - the same invariant
            # tests/layout_planned_matches_rendered.test.py checks in depth.
            check(f"{fname} drew the pages it planned", len(pages) == drawn,
                  f"planned {len(pages)}, drew {drawn}")

    if "--open" in sys.argv:
        print("\nOpening equipment + manpower for review…")
        for f in ("_q_equipment.pdf", "_q_manpower.pdf"):
            try:
                os.startfile(str(config.DATA_DIR / f))
            except Exception:
                pass

    print()
    if FAILURES:
        print(f"SMOKE QUOTE FAILED - {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("Smoke quote passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
