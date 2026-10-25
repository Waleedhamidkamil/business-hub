"""
Quotation HTML renderer + PDF generator (English, LTR) - schema-driven.

Each quotation TYPE (materials / equipment / manpower / service) defines its
own columns + terms in config.QUOTE_TYPES. This module renders any of them
through the same template, applying the same content-aware pagination:
  - rows estimated from their text content (long text -> taller)
  - footer (closing + terms + signature) pinned to the bottom with a small gap
  - overflow paginates to a header-less continuation page (never footer-only)
Only the materials type has a Grand Total (QTY x RATE).
"""
from __future__ import annotations

import base64
import math
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, doc_verify, pdf_engine, pdf_lock
from . import doc_layout


_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# --------------------------------------------------------------------------- #
# number -> words (USD)
# --------------------------------------------------------------------------- #
_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety"]
_SCALE = ["", "Thousand", "Million", "Billion"]


def _three_digits_to_words(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " Hundred"); n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10]); n %= 10
    if 1 <= n <= 19:
        parts.append(_ONES[n])
    return " ".join(parts).strip()


def _int_to_words(n: int) -> str:
    if n == 0:
        return "No"
    chunks, scale_idx = [], 0
    while n > 0:
        chunk = n % 1000
        if chunk:
            w = _three_digits_to_words(chunk)
            if _SCALE[scale_idx]:
                w += " " + _SCALE[scale_idx]
            chunks.append(w)
        n //= 1000
        scale_idx += 1
    return " ".join(reversed(chunks)).strip()


def amount_in_words(amount: float, currency: str = "USD") -> str:
    """Spell an amount out for the Total row.

    2026-07-29 - `currency` was added so invoices can be issued in Iraqi Dinar.
    The dinar has no subunit in practical use here, so the IQD wording is a whole
    number with no "and ... Cents" tail.
    """
    if (currency or "USD").upper() == "IQD":
        dinars = int(round(float(amount or 0)))
        return f"{_int_to_words(dinars)} Iraqi {'Dinar' if dinars == 1 else 'Dinars'}"
    amount = round(float(amount or 0), 2)
    dollars = int(amount)
    cents = int(round((amount - dollars) * 100))
    d = _int_to_words(dollars); c = _int_to_words(cents)
    return f"{d} {'Dollar' if dollars == 1 else 'Dollars'} and {c} {'Cent' if cents == 1 else 'Cents'}"


def _money(v, currency: str = "USD") -> str:
    """`$1,234.56` for USD, `IQD 1,234,568` for dinars (whole units)."""
    try:
        if (currency or "USD").upper() == "IQD":
            return "IQD {:,.0f}".format(round(float(v or 0)))
        return "${:,.2f}".format(round(float(v or 0), 2))
    except (TypeError, ValueError):
        return ""


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# layout constants (mm, A4 portrait)
# --------------------------------------------------------------------------- #
def _qov(key, default):
    o = getattr(config, "QUOTE_LAYOUT", {}) or {}
    return float(o.get(key, default))

def _q_autofit(layout: dict | None = None) -> bool:
    """When on, every row hugs its text (no fixed/stretched row height)."""
    if isinstance(layout, dict) and "autofit_rows" in layout:
        return bool(layout.get("autofit_rows"))
    o = getattr(config, "QUOTE_LAYOUT", {}) or {}
    return bool(o.get("autofit_rows"))

Q_USABLE_MM      = 273.0
Q_TOP_MM         = 70.0
# Phase X.27 - small top margin on HEADERLESS pages (continuation + last pages
# of a multi-page doc). Only page 1 (and a single-page doc) render the ~70mm
# header block; every other page's table starts at the top of the content box,
# so it must NOT reserve the full 70mm. This is the fix for the wasted ~70-90mm
# band at the bottom of middle pages.
Q_CONT_TOP_MM    = 2.0
Q_CLOSING_MM     = 8.0       # smaller closing line block
Q_SAFE_GAP_MM    = _qov("safe_gap_mm", 7.0)
Q_SIGN_MM        = 24.0
Q_TERMS_TITLE_MM = 6.0
Q_TERM_LINE_MM   = 5.5       # slightly tighter (matches 9pt term font)
Q_GRAND_MM       = 11.0
Q_HEADER_ROW_MM  = 9.0
# Gap reserved at the very bottom of every NON-last page so the stretched
# (or filled) table never collides with the "Page X of Y" number sitting there.
Q_PAGENUM_MM     = 8.0
# Phase Y.2 - page chrome the Abraj-tuned areas do NOT model, but the Eshraqat
# skin (eshraqat_print.css .esh-page / .esh-table) adds on EVERY page: ~20mm
# bottom padding + a taller table header. Subtracted from every page area under
# the Eshraqat profile so build_layout packs rows that fit and never spills onto
# a near-empty page. Abraj => 0 (areas byte-identical to before). See inv_generator.
Q_ESH_PAGE_CHROME_MM = 34.0


def _q_page_chrome_mm() -> float:
    return Q_ESH_PAGE_CHROME_MM if config.ACTIVE_PROFILE == "eshraqat" else 0.0
# Phase X.24 - every data row renders ~2.3mm TALLER than the height we set it
# to (cell padding 2px×2 + collapsed border + line leading - none of which the
# CSS `height` includes). A FILLED table therefore overshoots its allotted area
# by ~overhead×rows. Continuation pages have an empty header band to absorb it,
# but page 1 (header present → zero slack) spilled its tail onto a phantom page:
# the stranded "Page 1 of 9" orphan. We reserve this overhead (+ a safety gap)
# on the header page's fill so its table can never exceed the page. Measured
# ~2.3mm; rounded up to 2.6 for headroom.
Q_ROW_OVERHEAD_MM = 2.6
Q_PAGE_SAFETY_MM  = 4.0
# Phase L.3 - base row tightened (was 12 → 8) so 20+ short-text items fit
# one page instead of over-splitting. Matches the CSS variable default in
# quote_styles.css. The engine stretches rows per-page when there's room.
Q_BASE_ROW_MM    = _qov("min_row_mm", 8.0)
Q_LINE_MM        = 5.0
Q_TERM_CPL       = 48        # 9pt font fits a few more chars per line
Q_MAX_ROW_MM     = _qov("max_row_mm", 22.0)
# Phase L.5 - when a row has a populated image cell, the rendered height
# is dominated by the image, not the text. The CSS caps images at 20mm
# (with 2mm padding -> ~22mm row), so estimate that as the row floor for
# image-bearing rows. Without this, pagination thinks rows are 8mm and
# packs 18+ on one page, but the rendered table actually overflows.
Q_IMAGE_ROW_MIN_MM = _qov("image_row_min_mm", 22.0)


def _rebalance_widths(cols: list[dict], target_total: float = 96.4) -> list[dict]:
    """
    Make column widths sum to ~target_total (the NO. column takes the rest).
    Columns that didn't specify a width get an equal share of what's left
    after the explicit widths are subtracted. If explicit widths already
    exceed target_total, everything is scaled down proportionally.
    """
    explicit = sum(c.get("width", 0) for c in cols if c.get("width"))
    missing  = [c for c in cols if not c.get("width")]
    remaining = max(0.0, target_total - explicit)
    per = (remaining / len(missing)) if missing else 0.0
    for c in missing:
        c["width"] = per
    actual = sum(c["width"] for c in cols)
    if actual > target_total and actual > 0:
        scale = target_total / actual
        for c in cols:
            c["width"] = round(c["width"] * scale, 2)
    return cols


def _schema_for(quote_type: str) -> dict:
    """
    Return the type's schema with any per-sub-type overrides applied:
      1. `columns` override replaces the default column list entirely.
         Phase L.4: if an override column has no width (typically because the
         columns editor stripped it), inherit width AND kind from the default
         schema's column with the same key. This rescues users who saved per-
         sub-type settings without realising widths got stripped.
      2. `column_heads` overrides head text on top of either default or
         custom columns (backwards compatible with Phase G.2)
      3. Widths are auto-rebalanced so they sum to ~96.4%.
    Original config.QUOTE_TYPES isn't mutated.
    """
    base = config.QUOTE_TYPES.get(quote_type, config.QUOTE_TYPES["materials"])
    overrides = (getattr(config, "QUOTE_SUBTYPES_OVERRIDES", {}) or {}).get(quote_type) or {}
    custom_cols = overrides.get("columns")
    heads       = overrides.get("column_heads") or {}

    if custom_cols:
        cols = [dict(c) for c in custom_cols]
        # Phase L.4 - inherit width / kind from defaults when missing,
        # and auto-migrate stale head text that matches an OLD default
        # exactly (so renames in code propagate to users who never
        # customised that column's header).
        default_by_key = {c["key"]: c for c in base["columns"]}
        STALE_HEADS = {
            # column key -> list of OLD default head strings to migrate
            "image": ["IMAGE (IF ANY)"],
        }
        for c in cols:
            d = default_by_key.get(c.get("key"))
            if not d:
                continue
            if not c.get("width"):
                c["width"] = d.get("width", 0)
            if not c.get("kind"):
                c["kind"] = d.get("kind", "text")
            stale_list = STALE_HEADS.get(c["key"], [])
            if c.get("head") in stale_list and d.get("head"):
                c["head"] = d["head"]
    elif heads:
        cols = [{**c} for c in base["columns"]]
    else:
        return base

    # Apply head renames last so they trump whatever the columns override sets.
    if heads:
        cols = [
            {**c, **({"head": heads[c["key"]]} if c["key"] in heads else {})}
            for c in cols
        ]
    cols = _rebalance_widths(cols)
    return {**base, "columns": cols}


def _schema_for_doc(quote: dict) -> dict:
    """
    Phase T - per-document columns. When the document carries its own `columns`
    list (the editor's per-doc layout), build the schema from THOSE columns
    instead of the type/settings schema; otherwise fall back to `_schema_for`
    (so documents without a per-doc layout render exactly as before).

    Missing width/kind on a doc column are inherited from the type default with
    the same key; widths are rebalanced to fill the table.
    """
    quote_type = quote.get("quote_type", "materials")
    doc_cols = quote.get("columns")
    if not (isinstance(doc_cols, list) and doc_cols):
        return _schema_for(quote_type)
    base = config.QUOTE_TYPES.get(quote_type, config.QUOTE_TYPES["materials"])
    default_by_key = {c["key"]: c for c in base["columns"]}
    cols: list[dict] = []
    for c in doc_cols:
        if not isinstance(c, dict) or not c.get("key"):
            continue
        cc = dict(c)
        d = default_by_key.get(cc["key"])
        if d:
            if not cc.get("kind"):
                cc["kind"] = d.get("kind", "text")
            if not cc.get("width"):
                cc["width"] = d.get("width", 0)
        else:
            cc.setdefault("kind", "text")
        cols.append(cc)
    if not cols:
        return _schema_for(quote_type)
    cols = _rebalance_widths(cols)
    return {**base, "columns": cols}


def _terms_for(quote_type: str) -> list[str] | None:
    """Per-sub-type terms override, or None when not configured (caller falls
    through to the schema's default terms). An explicitly EMPTY list is a real
    choice - "no terms" - and is returned as-is, NOT treated as unset."""
    overrides = (getattr(config, "QUOTE_SUBTYPES_OVERRIDES", {}) or {}).get(quote_type) or {}
    t = overrides.get("terms")
    return list(t) if isinstance(t, list) else None


def _footer_block_mm(terms: list[str]) -> float:
    total_lines = 0
    for t in (terms or []):
        total_lines += max(1, math.ceil(len(str(t or "")) / Q_TERM_CPL))
    total_lines = max(total_lines, 1)
    return max(Q_TERMS_TITLE_MM + total_lines * Q_TERM_LINE_MM, Q_SIGN_MM)


# Auto-fit measured row metrics (Bahnschrift 10pt, 1.18 line-height, 2px
# padding, 1px borders). Phase M.4 - bumped from 5.8 to 6.5mm after measuring
# the user's rendered output: actual single-line rows came out ~6.4mm tall,
# so 5.8 under-counted by ~0.6mm × 30 rows = 18mm cumulative, just enough
# to push one row off the bottom of page 1.
Q_AF_BASE_MM = 6.5
Q_AF_LINE_MM = 4.4


def estimate_row_height_mm(row: dict, schema: dict, autofit: bool = False) -> float:
    """
    Estimate the rendered row height. Three drivers:
      1. tallest wrapping text column (text → wraps to N lines)
      2. populated image cell (CSS caps it at Q_IMAGE_ROW_MIN_MM)
      3. base row minimum
    The MAX of those is the row's effective floor. In auto-fit mode the text
    rows hug their content, so we estimate with the measured tight metrics.
    """
    max_lines = 1
    has_image = False
    for c in schema["columns"]:
        kind = c["kind"]
        if kind == "text":
            v = str(row.get(c["key"], "") or "")
            if v:
                cpl = max(6.0, c["width"] * 0.9)
                # Count wrapped lines PER explicit newline so "a\nb\nc" is 3
                # lines, not 1 - otherwise auto-fit rows overflow the estimate.
                lines = sum(max(1, math.ceil(len(seg) / cpl)) for seg in v.split("\n"))
                max_lines = max(max_lines, lines)
        elif kind == "image":
            if row.get(c["key"]):
                has_image = True
    if autofit:
        text_h = Q_AF_BASE_MM + (max_lines - 1) * Q_AF_LINE_MM
    else:
        text_h = Q_BASE_ROW_MM + (max_lines - 1) * Q_LINE_MM
    image_h = Q_IMAGE_ROW_MIN_MM if has_image else 0.0
    return max(text_h, image_h)


def _q_stretched_row_mm(area_mm: float, header_mm: float, extra_fixed_mm: float,
                        items_count: int) -> float:
    """Per-page row height: bounded by Q_BASE_ROW_MM / Q_MAX_ROW_MM."""
    if items_count <= 0:
        return Q_MAX_ROW_MM
    avail = max(0.0, area_mm - header_mm - extra_fixed_mm)
    natural = avail / items_count
    return max(Q_BASE_ROW_MM, min(Q_MAX_ROW_MM, natural))


def build_layout(items: list[dict], terms: list[str], schema: dict,
                 autofit: bool | None = None) -> list[dict]:
    """
    Phase L.3 / X.27 - adaptive multi-page packing (same shape as dn/inv).

    Page-ROLE packing (see docs/12-PDF-LAYOUT-CONSTITUTION): each page fills to
    its OWN capacity - page 1 at header_cap (reserves the ~70mm header), every
    later page at body_cap (headerless, so it does NOT reserve the header and
    fills to its true height), the last page peeled to footer_cap (footer +
    grand-total reserved). Each page gets its own row_min_mm.
    """
    has_grand = bool(schema.get("grand_total"))
    # Auto-fit: rows hug their text. We never stretch the table to fill the page
    # (fill_table stays False on every page) and the CSS sets row height: auto.
    if autofit is None:
        autofit = _q_autofit()
    footer_block = _footer_block_mm(terms)
    last_reserve = Q_CLOSING_MM + footer_block + Q_SAFE_GAP_MM
    grand_h = Q_GRAND_MM if has_grand else 0.0

    # Phase X.27 - page-role areas. Each page reserves ONLY what it actually
    # renders, so every page fills to its true height:
    #   • header page (page 1 of a multi-page doc): ~70mm header + page number
    #   • body page   (middle pages): tiny top margin + page number - the biggest
    #   • footer page (last page of a multi-page doc): tiny top margin + footer
    #   • single page (the only page): header + footer
    # The old code subtracted the 70mm header from EVERY page, wasting ~70mm on
    # the headerless middle/last pages, and packed them all at the smallest
    # (footer) capacity - hence 6 rows/page when ~11 fit.
    # ONE page-area model for all four document types (2026-09-05).
    # These four areas and four capacities were written out three times, in
    # three generators, and all three had drifted into the same defect - see
    # `backend/doc_layout.py`. The per-type numbers below are still measured
    # per type (docs/12 SS6 forbids copying one type's constant to another);
    # only the arithmetic over them is shared.
    geo = doc_layout.PageGeometry(
        usable_mm=Q_USABLE_MM,
        top_mm=Q_TOP_MM,
        cont_top_mm=Q_CONT_TOP_MM,
        head_row_mm=Q_HEADER_ROW_MM,
        pagenum_mm=Q_PAGENUM_MM,
        last_reserve_mm=last_reserve,
        last_fixed_in_table_mm=grand_h,
        chrome_mm=_q_page_chrome_mm(),
        safety_mm=Q_PAGE_SAFETY_MM,
    )
    header_area, body_area = geo.header_area, geo.body_area
    footer_area, single_area = geo.footer_area, geo.single_area
    header_cap = geo.capacity(doc_layout.HEADER_PAGE)
    body_cap   = geo.capacity(doc_layout.BODY_PAGE)
    footer_cap = geo.capacity(doc_layout.FOOTER_PAGE)
    single_cap = geo.capacity(doc_layout.SINGLE_PAGE)

    heights = [estimate_row_height_mm(it, schema, autofit) for it in items]

    def idx(rows, start=1):
        out = []
        for i, it in enumerate(rows, start=start):
            r = dict(it); r["_index"] = i
            out.append(r)
        return out

    # ---- Single page ----
    if not items or sum(heights) <= single_cap:
        row_min = _q_stretched_row_mm(single_area, Q_HEADER_ROW_MM, grand_h, len(items))
        return [{
            "show_header": True, "show_footer": True, "is_last": True,
            "fill_table": False,
            "table_area_mm": round(single_area, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": Q_HEADER_ROW_MM,
            "rows": idx(items),
            "page_index": 1, "total_pages": 1,
        }]

    # ---- Multi-page: fill each page to its OWN capacity (page 1 = header_cap,
    # every later page = body_cap). No even-balancing - that was what spread the
    # rows thin and wasted the bottom of every page.
    chunks: list[tuple[int, int]] = []
    start = 0
    current_h = 0.0
    page_i = 0
    for i, h in enumerate(heights):
        cap = header_cap if page_i == 0 else body_cap
        if current_h > 0 and current_h + h > cap:
            chunks.append((start, i))
            start = i
            current_h = 0.0
            page_i += 1
        current_h += h
    chunks.append((start, len(items)))

    # The last page also carries the footer (footer_cap < body_cap). If it was
    # packed at body_cap and now overflows the footer capacity, peel trailing
    # rows onto a new final page so the footer never collides with the table.
    while (chunks[-1][1] - chunks[-1][0]) > 1 and \
            sum(heights[chunks[-1][0]:chunks[-1][1]]) > footer_cap:
        s_last, e_last = chunks[-1]
        chunks[-1] = (s_last, e_last - 1)
        chunks.append((e_last - 1, e_last))

    # Light orphan guard - avoid a lonely 1-row last page by pulling one row down
    # from the previous page, provided both pages still fit their capacities.
    if len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) == 1:
        s_prev, e_prev = chunks[-2]
        s_last, e_last = chunks[-1]
        prev_cap = header_cap if len(chunks) == 2 else body_cap
        if (e_prev - s_prev) >= 3 and \
                sum(heights[s_prev:e_prev - 1]) <= prev_cap and \
                sum(heights[s_last - 1:e_last]) <= footer_cap:
            chunks[-2] = (s_prev, e_prev - 1)
            chunks[-1] = (s_last - 1, e_last)

    pages = []
    total = len(chunks)
    for p_i, (s, e) in enumerate(chunks):
        is_last = p_i == total - 1
        # The FOUR page roles of docs/12 SS2. The `p_i == 0 and is_last` case
        # was missing here in all three generators until 2026-09-05: when the
        # greedy packer happens to fit every row into one chunk, that page still
        # renders the header (show_header is `p_i == 0`) AND the footer, but it
        # was handed `footer_area` - the HEADERLESS area. The rows were then
        # stretched to fill it, so a DN of 19 wrapping rows built a 234mm table
        # under a 75mm header on a 273mm page and printed on two sheets while
        # its own footer said "Page 1 of 1".
        # Reachable in quote/inv too; their single-page guard just caught the
        # cases first. Guarded by tests/layout_planned_matches_rendered.test.py.
        # The four roles of docs/12 SS2, decided in ONE place. The `single`
        # case (a page that is both first and last, so renders the header AND
        # the closing block) was missing from all three generators until
        # 2026-09-05; see backend/doc_layout.role_for.
        page_area = geo.area_for(p_i, total)
        extra_fixed = grand_h if is_last else 0.0
        count = e - s
        # Phase M.4 - autofit mode must NOT use the classic 8mm clamp as the
        # CSS row min (see estimate). Phase X.24/X.27 - a FILLED table renders
        # ~Q_ROW_OVERHEAD_MM taller per row than the height we set; since EVERY
        # non-last page now packs full (no header-band slack to absorb it),
        # reserve that overhead + a safety gap in the fill height on every
        # filled page so the table can never spill onto a phantom page.
        fill_mm = page_area
        if (not is_last) and (not autofit):
            fill_mm = max(Q_HEADER_ROW_MM + count * Q_BASE_ROW_MM,
                          page_area - count * Q_ROW_OVERHEAD_MM - Q_PAGE_SAFETY_MM)
        if autofit:
            row_min = Q_AF_BASE_MM
        else:
            row_min = _q_stretched_row_mm(fill_mm, Q_HEADER_ROW_MM, extra_fixed, count)
        pages.append({
            "show_header": p_i == 0,
            "show_footer": is_last,
            "is_last": is_last,
            "fill_table": (not is_last) and not autofit,
            "table_area_mm": round(fill_mm, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": Q_HEADER_ROW_MM,
            "rows": idx(items[s:e], start=s + 1),
            "page_index": p_i + 1,
            "total_pages": total,
        })
    return pages


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _logo_data_uri() -> str | None:
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        p = config.ASSETS_DIR / name
        if p.exists():
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return None


def _read_css() -> str:
    return (config.STATIC_DIR / "quote_styles.css").read_text(encoding="utf-8")


def _build_columns(schema: dict) -> list[dict]:
    cols = [{"key": "_no", "head": "NO.", "kind": "index", "width": 3.6, "align": "center"}]
    for c in schema["columns"]:
        cols.append({**c, "align": c.get("align", "center")})
    return cols


def _cell(row: dict, col: dict, qty: float, rate: float) -> dict:
    kind, k = col["kind"], col["key"]
    if kind == "index":
        val = str(row.get("_index", ""))
    elif kind == "image":
        # Row holds a data URI (e.g. "data:image/jpeg;base64,...") or empty.
        return {"val": str(row.get(k, "") or ""), "align": col["align"], "is_image": True}
    elif kind == "computed":
        # 2026-07-30 - the TOTAL needs a RATE to mean anything. The old test was
        # `if (qty or rate)`, so a row with a quantity but a BLANK rate printed
        # "$0.00" - which tells the client the line is free, when in fact it is
        # simply not priced yet. `rate` arrives here already coerced to a float,
        # so blank and a typed 0 look identical; read the raw cell instead.
        rate_raw = str(row.get("rate", "") or "").strip()
        val = _money(qty * rate) if rate_raw != "" else ""
    elif kind == "money":
        raw = row.get(k, "")
        val = _money(_num(raw)) if str(raw).strip() != "" else ""
    else:
        val = str(row.get(k, "") or "")
    return {"val": val, "align": col["align"]}


def _apply_col_widths(schema: dict, doc: dict) -> dict:
    """Override the schema's per-column widths with the live editor widths
    (Excel-style column resizing) carried in doc["col_widths"] = {key: pct}.

    Returns the schema unchanged when no override is present, so saved
    documents without col_widths render exactly as before. Widths are
    rebalanced just like the fitting function, keeping the table proportions
    the user dragged on screen.
    """
    cw = doc.get("col_widths")
    if not isinstance(cw, dict) or not cw:
        return schema
    cols, changed = [], False
    for c in schema["columns"]:
        c = {**c}
        try:
            w = float(cw.get(c["key"]))
        except (TypeError, ValueError):
            w = 0.0
        if w and w > 0:
            c["width"] = w
            changed = True
        cols.append(c)
    if not changed:
        return schema
    return {**schema, "columns": _rebalance_widths(cols)}


def _strip_image_columns(schema: dict) -> dict:
    """Drop any image-kind columns and rebalance widths to fill the freed space.
    Eshraqat quotations use a text-only table (NO/ITEM/DESCRIPTION/UNIT/QTY/RATE/
    TOTAL) - no product-image column."""
    cols = [c for c in schema["columns"] if c.get("kind") != "image"]
    if len(cols) == len(schema["columns"]):
        return schema
    return {**schema, "columns": _rebalance_widths(cols)}


def render_quote_html(quote: dict, inline_css: bool = False) -> str:
    quote_type = quote.get("quote_type", "materials")
    schema = _schema_for_doc(quote)              # Phase T - per-document columns
    schema = _apply_col_widths(schema, quote)
    if config.ACTIVE_PROFILE == "eshraqat":
        schema = _strip_image_columns(schema)    # Eshraqat quote table has no IMAGE column
    columns = _build_columns(schema)
    items = quote.get("items", [])
    # Resolution order for the terms block:
    #   1. terms saved inside the quote (user-typed for this quote)
    #   2. per-sub-type override from settings (Phase G.2)
    #   3. schema default terms
    # An EMPTY list at any level is a deliberate "no terms" and STOPS the chain -
    # only an absent/non-list value falls through. (A plain `or` chain read
    # "the user deleted every term" as unset and silently restored the defaults,
    # so deleting terms appeared to do nothing to the document.)
    if isinstance(quote.get("terms"), list):
        terms = list(quote["terms"])
    else:
        sub_terms = _terms_for(quote_type)
        terms = sub_terms if sub_terms is not None else schema["terms"]
    has_grand = bool(schema.get("grand_total"))
    autofit = _q_autofit(quote.get("layout"))

    grand = round(sum(_num(it.get("qty")) * _num(it.get("rate")) for it in items), 2) if has_grand else 0.0
    pages = build_layout(items, terms, schema, autofit=autofit)

    # Attach display cells to every row.
    for page in pages:
        disp = []
        for row in page["rows"]:
            qty, rate = _num(row.get("qty")), _num(row.get("rate"))
            disp.append([_cell(row, c, qty, rate) for c in columns])
        page["display_rows"] = disp

    total_cols = len(columns)
    grand_total = None
    if has_grand:
        grand_total = {
            "label_span": 2,
            "words_span": max(1, total_cols - 3),
            "amount_span": 1,
            "words": amount_in_words(grand),
            "amount": _money(grand),
        }

    from . import signing as _signing
    sig_ctx = _signing.build_template_context(
        "qut", quote.get("signing_overlay"), quote.get("quote_no"),
    )

    if config.ACTIVE_PROFILE == "eshraqat":
        from .eshraqat_render import common_context
        from .eshraqat_render import meta_rows as eshraqat_meta_rows

        template = _env.get_template("eshraqat/quote_page.html")
        # One header layout for quotation, delivery note and invoice; the fixed
        # "Attention: Admin Manager" line lives there too.
        meta_rows = eshraqat_meta_rows("quote", quote)
        # Totals block: Subtotal / Withholding Tax (3.3%) / Grand Total, matching
        # the Eshraqat quotation design. Withholding is shown as a status, not a
        # deduction (the price is quoted tax-inclusive), so Subtotal == Grand Total.
        withholding_status = ("Included" if quote.get("withholding_included", True)
                              else "Not Included")
        total_rows = []
        if grand_total:
            total_rows = [
                {"label": "Subtotal", "value": grand_total["amount"]},
                {"label": "Withholding Tax (3.3%)", "value": withholding_status},
                {"label": "Grand Total", "value": grand_total["amount"], "grand": True},
            ]
        # Eshraqat quotations are a single "Quotation" family - the header shows
        # the document type, NOT the sub-type (materials/equipment/…). (User ask.)
        eshraqat_ctx = common_context(
            title="Quotation",
            doc_no=quote.get("quote_no", ""),
            pages=len(pages),
        )
        return template.render(
            quote=quote,
            columns=columns,
            pages=pages,
            autofit=autofit,
            grand_total=grand_total,
            total_rows=total_rows,
            terms=terms,
            meta_rows=meta_rows,
            inline_css=inline_css,
            css_text=(config.STATIC_DIR / "eshraqat_print.css").read_text(encoding="utf-8")
            if inline_css else "",
            css_href="../../static/eshraqat_print.css",
            **eshraqat_ctx,
            **sig_ctx,
        )

    template = _env.get_template("quote_page.html")
    return template.render(
        quote=quote,
        columns=columns,
        pages=pages,
        autofit=autofit,
        grand_total=grand_total,
        company=config.QUOTE_COMPANY,
        brand=config.QUOTE_BRAND_COLOR,
        logo_data_uri=_logo_data_uri(),
        terms=terms,
        # 2026-07-30 - the IMAGE cell's CSS cap must match what the paginator
        # reserved per image row, or raising `image_row_min_mm` just adds blank
        # space. The autofit variant keeps its historical +2mm slack.
        image_row_mm=round(Q_IMAGE_ROW_MIN_MM, 2),
        image_row_autofit_mm=round(Q_IMAGE_ROW_MIN_MM + 2.0, 2),
        inline_css=inline_css,
        css_text=_read_css() if inline_css else "",
        css_href="../static/quote_styles.css",
        **sig_ctx,
    )


def render_quote_pdf(quote: dict, out_path: Path) -> Path:
    html = render_quote_html(quote, inline_css=True)
    # Locked LAST - backend/pdf_lock.py. Opens with no password, prints, cannot be edited.
    written = doc_verify.stamp_pdf(pdf_engine.write_pdf(html, out_path), "qut", quote)
    return pdf_lock.lock(written)
