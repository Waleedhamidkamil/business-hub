"""
HTML renderer + PDF generator.

The same Jinja template renders the on-screen preview AND the printed PDF, so
WYSIWYG is guaranteed. PDF is produced by headless Chrome/Edge (see pdf_engine).

Pagination (2026-07-29 - a PO is ONE document with N pages):
    The table is packed greedily by estimated row height (see
    `estimate_row_height_mm`) into pages of the SAME PO. The budget depends on
    the page's ROLE, because the chrome differs per page exactly as it does for
    quotations / delivery notes / invoices:

        page 1        carries the logo header + title + meta block
        continuation  carries neither (starts flush at the top)
        last page     reserves the bottom band for the manager signature

    Until 2026-07-29 an overflow instead started a brand-new PO NUMBER and each
    chunk became its own PDF, so a 40-item order became two unrelated POs. It now
    becomes one PO printed on two pages. See `paginate_po`.

This prevents long-description rows from pushing the table content over the
absolutely-positioned signature ("المدير العام") at the bottom of the page.
"""
from __future__ import annotations

import base64
import importlib
import math
import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, doc_verify, pdf_engine, pdf_lock


# ---------------------------------------------------------------------------
# Hot-reload of config.py - lets you tweak the row model, signatures, etc.
# while the app is running. We check the mtime of config.py before each render
# and reload the module if it changed. Overhead per render: one os.stat call.
# ---------------------------------------------------------------------------
_config_mtime: float | None = None


def _refresh_config_if_changed() -> None:
    global _config_mtime
    try:
        mtime = os.path.getmtime(config.__file__)
    except (AttributeError, OSError):
        return
    if _config_mtime is None:
        _config_mtime = mtime
        return
    if mtime > _config_mtime:
        importlib.reload(config)
        _config_mtime = mtime


_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _logo_data_uri() -> str | None:
    """Embed logo as a data URI so the HTML is self-contained."""
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        p = config.ASSETS_DIR / name
        if p.exists():
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return None


def _read_css() -> str:
    css_name = "eshraqat_print.css" if config.ACTIVE_PROFILE == "eshraqat" else "po_styles.css"
    return (config.STATIC_DIR / css_name).read_text(encoding="utf-8")


# How many characters fit on one line of a column, per PERCENT of table width.
#
# This used to be a hard-coded table (name 14, description 12, company 11,
# unit 6) "derived from each column's width in mm vs ~2.7mm per Arabic glyph at
# 12pt", rounded down on purpose so the estimate over-booked rather than
# under-booked. The glyph figure was wrong: measured through the real renderer
# an Arabic glyph at this size averages ~1.73mm, so every column's capacity was
# understated by 64-100% and single-line rows were booked as two.
#
# Measured 2026-09-28 at the default widths - the length at which each column
# first wraps, read back from the rendered PDF with fitz:
#
#     column        width %   really fits   chars per %
#     description      20.0      22 chars       1.100
#     name             23.9      25 chars       1.046
#     company          16.7      18 chars       1.078
#     unit             10.2      12 chars       1.176
#
# 0.9 keeps ~14% of headroom under the tightest of those, and is the SAME
# factor `quote_generator.estimate_row_height_mm` already uses (`c["width"] *
# 0.9`). Deriving from the width rather than fixing a number per column is the
# point: **PO column widths are per document** (`doc_columns` - 43 live POs
# carry a 21% description column, 13 carry 24%, three carry 28.39%), so no
# fixed table can be right for all of them.
PO_CHARS_PER_PCT = 0.9
PO_MIN_CPL = 6.0

# The fields that wrap, mapped to the column class whose width governs them.
_PO_WRAPPING_FIELDS = {
    "name":        "col-name",
    "description": "col-desc",
    "company":     "col-company",
    "unit":        "col-unit",
}


# Height to reserve for a row with a picture in it. Mirrors the quotation's
# `image_row_min_mm` (quote_generator.Q_IMAGE_ROW_MIN_MM, default 22mm) so both
# document types cap pictures the same way and a photo does not print at wildly
# different sizes depending on which document it lands in.
PO_IMAGE_ROW_MIN_MM = 22.0

# Slack left at the bottom of every PO page (2026-09-05). See _po_page_budget.
PO_PAGE_SAFETY_MM = 2.0

# The shortest a PO row can render, whatever the setting says (2026-09-05).
# `height` on a table cell is a MINIMUM: one line of 12pt text plus the 6px
# padding and the border needs 8.2mm (measured in Chrome by collapsing
# --po-row-mm to 0), and the browser will not go below it. Booking a smaller
# number would put the estimate back under the render - the exact drift that
# made A-84 overflow - so the estimate floors here even if the user asks for
# less. Changing the cell's font-size or padding changes this number.
PO_ROW_CONTENT_FLOOR_MM = 8.2

# Auto-fit uses the same 12pt table font, but its CSS drops the fixed height,
# trims vertical padding from 6px to 2px, and sets line-height: 1.25. Measured
# browser geometry is about 6.9mm for one line and 5.3mm for every additional
# line. These values must describe the auto-fit CSS, just as
# TABLE_MIN_ROW_MM/TABLE_LINE_HEIGHT_MM describe the fixed-row CSS.
PO_AF_BASE_MM = 7.0
PO_AF_LINE_MM = 5.3


def estimate_row_height_mm(row: dict, autofit: bool = False,
                           col_widths: dict | None = None) -> float:
    """
    Estimate how tall this row will render in mm.

    The row's visible height is driven by whichever column wraps to the most
    lines. For each text column, lines = ceil(len(text) / chars_per_line),
    minimum 1. Final height = min-row + (extra_lines * line_height).

    **Explicit newlines count (2026-07-29).** `po_styles.css` sets
    `white-space: pre-wrap` on table cells precisely so a newline typed into a
    PO row textarea is preserved, but this estimator only ever divided by
    chars-per-line - so a two-line typed cell was estimated at 9mm against a
    real 11.9mm (measured 0.76x, i.e. UNDER-estimating). Under-estimating is
    the dangerous direction: the table can then run into the absolutely
    positioned signature/stamp. Each segment between newlines is wrapped
    independently and the segments are summed, which is what the browser does.
    """
    line_count = 1
    for field, cpl in _po_chars_per_line(col_widths).items():
        text = str(row.get(field, "") or "").strip()
        if not text:
            continue
        lines = sum(max(1, math.ceil(len(seg) / cpl))
                    for seg in text.splitlines() or [""])
        if lines > line_count:
            line_count = lines
    extra_lines = line_count - 1
    if autofit:
        # `.items-table--autofit` removes the configured minimum and uses its
        # own tighter padding/line-height. The planner must use that geometry
        # too, otherwise Auto-fit only changes how rows DRAW while page breaks
        # keep using the larger fixed-row setting. A-88 exposed that mismatch:
        # 46 compact rows were planned as 20 + 23 + 3 over three sheets.
        text_h = PO_AF_BASE_MM + extra_lines * PO_AF_LINE_MM
    else:
        # The row draws at the configured height (po_styles.css `--po-row-mm`)
        # but never shorter than one line of text needs.
        row_mm = max(float(config.TABLE_MIN_ROW_MM), PO_ROW_CONTENT_FLOOR_MM)
        text_h = row_mm + extra_lines * config.TABLE_LINE_HEIGHT_MM

    # 2026-08-05 - a row carrying a PICTURE is as tall as the picture, which is
    # several times a line of text. Without this the paginator sizes it at 7mm,
    # packs a page full, and the rendered table runs straight into the
    # absolutely-positioned signature and stamp. `PO_IMAGE_ROW_MIN_MM` and the
    # `max-height` on `.col-image` in po_styles.css MUST agree: the estimate
    # reserves the space, the CSS is what actually stops the image growing past
    # it. Copied in spirit from quote_generator.Q_IMAGE_ROW_MIN_MM, which
    # learned this the same way.
    image_h = PO_IMAGE_ROW_MIN_MM if str(row.get("image", "") or "").strip() else 0.0
    return max(text_h, image_h)


def row_has_image(row: dict) -> bool:
    """True when this row carries a picture data URI."""
    return str(row.get("image", "") or "").strip() != ""


def any_row_has_image(rows) -> bool:
    """Whether the printed PO should carry an IMAGE column at all.

    Deliberately not always-on: the Arabic PO table is already 7 columns on A4
    portrait, and an always-present picture column would take a fifth of it to
    print blanks on every ordinary order."""
    return any(row_has_image(r) for r in (rows or []))


# --------------------------------------------------------------------------- #
# Page geometry (mm, A4 portrait) - MEASURED against the real renderer, not
# estimated. Method: render with natural browser pagination and read back the
# y-position of the first data row with fitz (see the pagination test).
#
#   .po-page box            10mm .. 287mm on the sheet  -> 277mm tall
#   page-1 first row top    71.3mm          |  difference = 50.0mm of header,
#   continuation first top  21.3mm          |  title and meta block
#   thead                   11.3mm  (config.TABLE_HEADER_MM = 12, counted in the
#                                    running total below, as it always was)
#   signature + stamp top   235.7mm on the sheet -> 51.3mm above the box bottom
#
# The signature reserve lives in `config` because a different stamp asset changes
# it; the rest is a property of po_page.html + po_styles.css and belongs here.
# Per docs/12 §6.1 these reserves are PER DOC TYPE on purpose - never share them
# with the quote/dn/inv constants even when the numbers happen to agree.
# --------------------------------------------------------------------------- #
PO_USABLE_MM   = 277.0   # .po-page min-height (A4 297 − 2×10mm @page margin)
PO_HEADER_MM   = 50.0    # logo header + title + date/PO-no meta row (page 1 only)
PO_CONT_TOP_MM = 0.0     # continuation pages start flush at the top (measured)

# The eshraqat skin is a different document: its own header + meta grid and an
# in-flow closing zone instead of the absolutely-placed signature.
# Measured separately - the abraj numbers above do NOT apply. Mirrors the
# `*_ESH_PAGE_CHROME_MM` / `*_ESH_CLOSEOUT_MM` pattern quote/dn/inv already use.
#
# 2026-08-09 - re-measured after the Arabic/RTL PO redesign. The PO skin is the
# only eshraqat document with NO footer band, so `.esh-po-document .esh-page`
# drops its bottom padding from 20mm to 10mm and the flow area grows 250 -> 260.
# The other four eshraqat skins are untouched and keep their 20mm.
#
#   .esh-page   min-height 270mm, padding-bottom 10mm (PO only) -> 260mm FLOW
#   page number position:absolute inside that padding  -> costs no flow
#   page-1 first row top   70.1mm on the sheet (box top 8.8mm) -> 61.3mm in
#   continuation first top 20.1mm -> 11.3mm in, i.e. thead only, header 0
#   header + meta grid     61.3 − 11.3 thead = 50.0mm
#   closing zone           32.9mm of flow (pinned to the bottom by the flex
#                          rule in eshraqat_print.css), plus ~13mm the stamp
#                          overhangs above the 16mm .esh-signature-space
PO_ESH_USABLE_MM   = 260.0
PO_ESH_HEADER_MM   = 50.0   # esh header + meta grid (measured 50.0)
PO_ESH_CONT_TOP_MM = 0.0    # continuation pages start flush at the box top
PO_ESH_CLOSEOUT_MM = 46.0   # closing zone (32.9) + stamp overhang headroom


def _po_geometry() -> tuple[float, float, float, float]:
    """(usable, header, cont_top, closeout) mm for the ACTIVE profile's skin."""
    if config.ACTIVE_PROFILE == "eshraqat":
        return (PO_ESH_USABLE_MM, PO_ESH_HEADER_MM,
                PO_ESH_CONT_TOP_MM, PO_ESH_CLOSEOUT_MM)
    return (PO_USABLE_MM, PO_HEADER_MM, PO_CONT_TOP_MM,
            float(getattr(config, "PO_SIGNATURE_MM", 54.0)))


def _po_page_budget(is_first: bool, is_last: bool) -> float:
    """Vertical mm available to the table (thead included) for one page role.

    Abraj:  single 277−50−54 = 173 | first 227 | middle 277 | last 223
    """
    usable, header, cont_top, closeout = _po_geometry()
    budget = usable - (header if is_first else cont_top)
    if is_last:
        budget -= closeout
    # 2026-09-05 - a hair of slack. Now that a row's booked height EQUALS its
    # rendered height (po_styles.css `--po-row-mm`, border-box) the estimate is
    # exact rather than generous, and a page that fills to the last tenth of a
    # millimetre is one rounding away from spilling. Overflow costs a wrong
    # "Page 1 of 2" footer on a document already sent; the cost of this margin
    # is at most one row on a full page. Mirrors quote_generator's
    # Q_PAGE_SAFETY_MM, which learned the same lesson.
    return budget - PO_PAGE_SAFETY_MM


def paginate_po(rows: list[dict], po_no: str,
                layout: dict | None = None,
                col_widths: dict | None = None) -> list[dict]:
    """
    Lay one PO's rows out over as many pages as they need. Returns a list of
    page dicts - ONE PO number across all of them (2026-07-29; this used to
    mint a new PO number per chunk, see the module docstring).

    Each page has:
        po_no:        the SAME number on every page, e.g. "A-10"
        page_index:   1-based page number
        total_pages:  total pages in this PO
        show_header:  True only on page 1 (logo header + title + meta)
        is_last:      True only on the final page (carries the signature)
        rows:         the rows on this page (with `index` + `filled` added);
                      `index` is continuous across pages, so page 2 starts at 16
                      rather than restarting at 1

    Because only the LAST page reserves the signature band, its budget is
    smaller than a middle page's - and which page is last is not known until
    packing is finished. Guessing the page count and re-packing does NOT work:
    it oscillates. With 15 rows of 14mm, "assume 1 page" overflows the 173mm
    single-page budget and yields 2 pages, while "assume 2 pages" gives page 1
    the 227mm non-last budget, so all 15 rows fit again and it yields 1 page -
    forever. That non-convergence used to fall through to a single 222mm page,
    i.e. the table ran 49mm INTO the signature.

    So: pack greedily against the non-last budget, then spill the tail until the
    final page honours the last-page budget. Each spill either shortens the last
    page or adds one, so it terminates; and a page is never left empty.
    """
    _refresh_config_if_changed()
    autofit = _po_autofit(layout)
    # Keep the long-standing one-argument fixed-mode call shape. Besides being
    # the public default, the image-overlap regression intentionally swaps in
    # a one-argument estimator to prove why image height reservation matters.
    heights = ([estimate_row_height_mm(r, autofit=True, col_widths=col_widths)
                for r in rows]
               if autofit else
               [estimate_row_height_mm(r, col_widths=col_widths) for r in rows])
    thead = float(config.TABLE_HEADER_MM)

    def page_height(chunk: list[int]) -> float:
        return thead + sum(heights[i] for i in chunk)

    def budget_for(page_no: int, is_last: bool) -> float:
        return _po_page_budget(is_first=(page_no == 1), is_last=is_last)

    # Pass 1 - greedy, every page treated as NOT last (no signature reserve).
    chunks: list[list[int]] = []
    current: list[int] = []
    for i, row_h in enumerate(heights):
        page_no = len(chunks) + 1
        if current and (page_height(current) + row_h) > budget_for(page_no, False):
            chunks.append(current)
            current = []
        current.append(i)
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [[]]

    # Pass 2 - the real last page has less room. Move trailing rows onto a new
    # page until it fits (a single row that still overflows has nowhere to go).
    while True:
        last = chunks[-1]
        page_no = len(chunks)
        if len(last) <= 1 or page_height(last) <= budget_for(page_no, True):
            break
        spill: list[int] = []
        while len(last) > 1 and page_height(last) > budget_for(page_no, True):
            spill.insert(0, last.pop())
        chunks.append(spill)

    total = len(chunks)
    pages = []
    running = 0
    for idx, chunk in enumerate(chunks):
        rendered_rows = []
        for i in chunk:
            running += 1
            rendered_rows.append({**rows[i], "index": running, "filled": True})
        pages.append({
            "po_no": po_no,
            "page_index": idx + 1,
            "total_pages": total,
            "show_header": idx == 0,
            "is_last": idx == total - 1,
            "rows": rendered_rows,
            # Whole-document, not per-page: a 2-page PO with a photo only on
            # page 2 must still show the column on page 1, or the two pages
            # print with different column counts.
            "has_images": any_row_has_image(rows),
            "image_row_mm": round(PO_IMAGE_ROW_MIN_MM, 2),
        })
    return pages


# PO PDF column classes and their reference widths (must match po_styles.css).
# The index + price columns are fixed; the 5 editable columns share the rest.
_PO_PDF_DEFAULTS = {
    "col-name": 23.9, "col-desc": 20.0, "col-unit": 10.2,
    "col-qty": 8.0, "col-company": 16.7,
}
_PO_FIXED_PCT = 4.7 + 16.6   # col-idx + col-price stay put


_PO_KEY_TO_CLASS = {
    "name": "col-name", "description": "col-desc", "unit": "col-unit",
    "qty": "col-qty", "company": "col-company", "image": "col-image",
}


def _po_effective_widths(col_widths: dict | None) -> dict[str, float]:
    """The width PERCENT each column will actually be drawn at.

    One function so the planner and the renderer cannot disagree: `_po_col_style`
    emits these as CSS and `_po_chars_per_line` turns the same numbers into a
    wrap budget. They used to be computed in one place and guessed in the other,
    which is how a resized description column moved the page breaks nowhere.
    """
    vals = dict(_PO_PDF_DEFAULTS)
    touched = False
    if isinstance(col_widths, dict) and col_widths:
        for key, cls in _PO_KEY_TO_CLASS.items():
            try:
                w = float(col_widths.get(key))
            except (TypeError, ValueError):
                w = 0.0
            if w > 0:
                vals[cls] = w
                touched = True
    if not touched:
        return dict(_PO_PDF_DEFAULTS)
    total = sum(vals.values())
    if total <= 0:
        return dict(_PO_PDF_DEFAULTS)
    scale = (100.0 - _PO_FIXED_PCT) / total
    return {cls: w * scale for cls, w in vals.items()}


def _po_chars_per_line(col_widths: dict | None = None) -> dict[str, float]:
    """How many characters each wrapping field fits on one line."""
    widths = _po_effective_widths(col_widths)
    return {
        field: max(PO_MIN_CPL, widths.get(cls, 0.0) * PO_CHARS_PER_PCT)
        for field, cls in _PO_WRAPPING_FIELDS.items()
    }


def _po_col_style(col_widths: dict | None) -> str:
    """Build a CSS override that maps the editor's live column widths
    (Excel-style resizing) onto the PO PDF's column classes. The index and
    price columns keep their reference widths; the 5 editable columns are
    scaled to fill the remainder while preserving the dragged proportions.
    Returns "" when no widths are supplied (PDF renders exactly as before)."""
    if not isinstance(col_widths, dict) or not col_widths:
        return ""
    if not any(_num_or_zero(col_widths.get(k)) > 0 for k in _PO_KEY_TO_CLASS):
        return ""
    return "\n".join(
        f".items-table .{cls} {{ width: {round(w, 2)}%; }}"
        for cls, w in _po_effective_widths(col_widths).items()
    )


def _num_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _po_autofit(layout: dict | None = None) -> bool:
    if isinstance(layout, dict) and "autofit_rows" in layout:
        return bool(layout.get("autofit_rows"))
    return bool(getattr(config, "PO_AUTOFIT", False))


def _po_source_quote(rows: list[dict]) -> str:
    for row in rows:
        for key in ("source_quote_no", "source_quotation_no", "quotation_no"):
            value = str(row.get(key, "") or "").strip()
            if value:
                return value
    return ""


def _po_suppliers(rows: list[dict]) -> str:
    suppliers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = str(
            row.get("supplier_name")
            or row.get("supplier")
            or row.get("company")
            or ""
        ).strip()
        marker = value.casefold()
        if value and marker not in seen:
            suppliers.append(value)
            seen.add(marker)
    return ", ".join(suppliers)


def render_html(po_no: str, po_date: str, rows: list[dict],
                inline_css: bool = False,
                signing_overlay: dict | None = None,
                col_widths: dict | None = None,
                layout: dict | None = None) -> str:
    """
    Render the PO HTML.
    - inline_css=False: link to ../static/po_styles.css (in-app preview)
    - inline_css=True:  embed the CSS in a <style> tag (PDF printing)
    """
    # The planner must use the SAME column widths the CSS below will draw
    # with. Paginating at the defaults while rendering at 21% is how a
    # resized column moved the page breaks nowhere (2026-09-28).
    pages = paginate_po(rows, po_no, layout=layout, col_widths=col_widths)
    from . import signing as _signing
    sig_ctx = _signing.build_template_context("po", signing_overlay, po_no)

    if config.ACTIVE_PROFILE == "eshraqat":
        from . import eshraqat_render

        template = _env.get_template("eshraqat/po_page.html")
        return template.render(
            **eshraqat_render.common_context(
                # 2026-08-09 - the Eshraqat PO skin is Arabic/RTL, so its
                # `document_title` is the Arabic one. The other four Eshraqat
                # documents stay English.
                title=config.ESHRAQAT_IDENTITY.get("po_title_ar", "طلب شراء"),
                doc_no=po_no,
                pages=len(pages),
            ),
            po_date=po_date,
            pages=pages,
            source_quote_no=_po_source_quote(rows),
            supplier_name=_po_suppliers(rows),
            autofit=_po_autofit(layout),
            table_row_mm=config.TABLE_MIN_ROW_MM,
            table_header_mm=config.TABLE_HEADER_MM,
            css_href="../static/eshraqat_print.css",
            inline_css=inline_css,
            css_text=_read_css() if inline_css else "",
            col_style=_po_col_style(col_widths),
            **sig_ctx,
        )

    template = _env.get_template("po_page.html")
    return template.render(
        po_no=po_no,
        po_date=po_date,
        pages=pages,
        autofit=_po_autofit(layout),
        # The row height the paginator booked, handed to the CSS so the page
        # draws exactly that (po_page.html `--po-row-mm`). The eshraqat branch
        # above already passed it; this one did not, which is why the setting
        # moved the page breaks and never the page.
        table_row_mm=config.TABLE_MIN_ROW_MM,
        table_header_mm=config.TABLE_HEADER_MM,
        company=config.COMPANY,
        logo_data_uri=_logo_data_uri(),
        css_href="../static/po_styles.css",
        inline_css=inline_css,
        css_text=_read_css() if inline_css else "",
        col_style=_po_col_style(col_widths),
        **sig_ctx,
    )


def render_pdf(po_no: str, po_date: str, rows: list[dict], out_path: Path,
               signing_overlay: dict | None = None,
               col_widths: dict | None = None,
               layout: dict | None = None) -> Path:
    """Render the PO to PDF and write to out_path. Returns the path written."""
    html = render_html(po_no, po_date, rows, inline_css=True,
                       signing_overlay=signing_overlay,
                       col_widths=col_widths,
                       layout=layout)
    # Locked LAST - backend/pdf_lock.py. Opens with no password, prints, cannot be edited.
    written = doc_verify.stamp_pdf(pdf_engine.write_pdf(html, out_path), "po",
                                   {"po_no": po_no, "date": po_date, "items": rows})
    return pdf_lock.lock(written)


def po_page_count(rows: list[dict], po_no: str = "",
                  layout: dict | None = None,
                  col_widths: dict | None = None) -> int:
    """How many PAGES this set of rows will print on (always one PO).

    Replaces the old `page_po_numbers`, which returned one number per chunk back
    when overflow minted extra POs (2026-07-29).
    """
    return len(paginate_po(rows, po_no or "-", layout=layout,
                           col_widths=col_widths))
