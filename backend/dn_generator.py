"""
Delivery Note HTML renderer + PDF generator (English, LTR).

Mirrors DN REF.pdf: navy header, centered "Delivery Note" title, meta block
(Date / DN No. / LPO No. / Client Name / Client Address), bordered items
table (No-Item-Description-Unit/Size-Q'ty + optional Rate/Total via toggle),
"Thank You..." closing, Recipient | Supplier sign boxes pinned at the bottom.

Reuses the same layout philosophy as the quotation generator (footer always
pinned on the last page, natural row heights, content-aware pagination).
"""
from __future__ import annotations

import base64
import math
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, doc_verify, pdf_engine, pdf_lock
from . import doc_layout
from .quote_generator import amount_in_words


_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# --------------------------------------------------------------------------- #
# Layout constants (mm, A4 portrait) - tuned for the DN footer block which is
# smaller than the quotation's (no terms, just two sign boxes side by side).
# --------------------------------------------------------------------------- #
# Defaults - can be overridden per-deployment via config.DN_LAYOUT
# (Phase F.5). Override keys: budget_mm (sets USABLE), safe_gap_mm,
# min_row_mm (sets BASE_ROW), max_items (advisory; layout still paginates).
_DN_LAYOUT_DEFAULTS = {
    "usable_mm":     273.0,
    "top_mm":        75.0,
    "closing_mm":    8.0,
    "safe_gap_mm":   7.0,
    "sign_mm":       28.0,
    "header_row_mm": 9.0,
    # Phase L.3 - base row tightened from 14 to 8mm so 18+ short-text items
    # actually fit on one page (matches the new CSS variable default in
    # dn_styles.css). The pagination engine bumps the row height per-page
    # when there's room (so few-item DNs still look generous).
    "base_row_mm":   8.0,
    "line_mm":       5.0,
    "grand_row_mm":  11.0,
    "max_row_mm":    22.0,   # cap when stretching sparse pages
}

def _ov(key, default):
    o = getattr(config, "DN_LAYOUT", {}) or {}
    return float(o.get(key, default))

def _dn_autofit(layout: dict | None = None) -> bool:
    """When on, every row hugs its text (no fixed/stretched row height)."""
    if isinstance(layout, dict) and "autofit_rows" in layout:
        return bool(layout.get("autofit_rows"))
    o = getattr(config, "DN_LAYOUT", {}) or {}
    return bool(o.get("autofit_rows"))

DN_USABLE_MM      = _DN_LAYOUT_DEFAULTS["usable_mm"]
DN_TOP_MM         = _DN_LAYOUT_DEFAULTS["top_mm"]
DN_CLOSING_MM     = _DN_LAYOUT_DEFAULTS["closing_mm"]
DN_SAFE_GAP_MM    = _ov("safe_gap_mm",  _DN_LAYOUT_DEFAULTS["safe_gap_mm"])
DN_SIGN_MM        = _DN_LAYOUT_DEFAULTS["sign_mm"]
DN_HEADER_ROW_MM  = _DN_LAYOUT_DEFAULTS["header_row_mm"]
DN_BASE_ROW_MM    = _ov("min_row_mm",   _DN_LAYOUT_DEFAULTS["base_row_mm"])
DN_LINE_MM        = _DN_LAYOUT_DEFAULTS["line_mm"]
DN_GRAND_ROW_MM   = _DN_LAYOUT_DEFAULTS["grand_row_mm"]
DN_MAX_ROW_MM     = _ov("max_row_mm",   _DN_LAYOUT_DEFAULTS["max_row_mm"])
# Gap reserved at the bottom of every non-last page for the page-number footer.
DN_PAGENUM_MM     = 8.0
# Phase X.27 - small top margin on HEADERLESS pages (continuation + last pages
# of a multi-page DN). Only page 1 renders the ~75mm header block; the rest start
# at the top of the content box, so they must NOT reserve the full 75mm. Fixes
# the wasted band at the bottom of middle pages. Mirrors quote_generator.
DN_CONT_TOP_MM    = 2.0
DN_PAGE_SAFETY_MM = 4.0
# Phase Y.2 - page chrome the Abraj-tuned areas do NOT model, but the Eshraqat
# skin adds on EVERY page (~20mm .esh-page bottom padding + taller table header).
# Subtracted from every area under the Eshraqat profile so build_layout packs
# rows that fit and never spills. Abraj => 0 (unchanged). See inv_generator.
DN_ESH_PAGE_CHROME_MM = 34.0


def _dn_page_chrome_mm() -> float:
    return DN_ESH_PAGE_CHROME_MM if config.ACTIVE_PROFILE == "eshraqat" else 0.0


def _money(v) -> str:
    try:
        return "${:,.2f}".format(round(float(v or 0), 2))
    except (TypeError, ValueError):
        return ""


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _logo_data_uri() -> str | None:
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        p = config.ASSETS_DIR / name
        if p.exists():
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return None


def _read_css() -> str:
    stylesheet = "eshraqat_print.css" if config.ACTIVE_PROFILE == "eshraqat" else "dn_styles.css"
    return (config.STATIC_DIR / stylesheet).read_text(encoding="utf-8")


def _columns(include_prices: bool, base_override: list[dict] | None = None) -> list[dict]:
    """Return the column list (NO. prepended), optionally with Rate+Total.

    Phase G.7: when the user has saved a `delivery.columns` override, it
    replaces the built-in DN_BASE_COLUMNS. Custom columns that don't supply
    a width get an equal slice of whatever's left after the explicit widths.
    Phase T: a per-document `base_override` (the doc's own base columns) takes
    priority over the type-level override / defaults.
    """
    # "NO." like the quotation and invoice (2026-10-02) - it was the one
    # document type whose number column read "No".
    cols = [{"key": "_no", "head": "NO.", "kind": "index", "width": 4.6, "align": "center"}]
    if base_override:
        raw_base = list(base_override)
    else:
        override = getattr(config, "DN_BASE_COLUMNS_OVERRIDE", None)
        raw_base = list(override) if override else list(config.DN_BASE_COLUMNS)
    base = [dict(c) for c in raw_base]
    # Ensure every column has a `kind` (per-doc columns may omit it); inherit
    # from the built-in DN columns by key, else default to free text.
    _dn_default_kind = {c["key"]: c.get("kind", "text") for c in config.DN_BASE_COLUMNS}
    for c in base:
        if not c.get("kind"):
            c["kind"] = _dn_default_kind.get(c.get("key"), "text")
    # Distribute width across any column that didn't specify one (so user-added
    # custom columns just work even without typing a width).
    no_width = [c for c in base if not c.get("width")]
    explicit = sum(c.get("width", 0) for c in base if c.get("width"))
    remaining = max(0.0, 95.4 - explicit)
    if no_width:
        per = remaining / len(no_width)
        for c in no_width:
            c["width"] = per
    if include_prices:
        base = base + [dict(c) for c in config.DN_PRICE_COLUMNS]
    # Rebalance widths so they sum to ~95 (with NO=4.6 -> 99.6, leaves a hair).
    total_w = sum(c["width"] for c in base)
    target = 95.4
    if total_w > 0:
        scale = target / total_w
        base = [{**c, "width": round(c["width"] * scale, 2)} for c in base]
    for c in base:
        cols.append({**c, "align": c.get("align", "center")})
    return cols


# Auto-fit measured metrics (10pt font): single line ~5.5mm, +4.2mm per line.
DN_AF_BASE_MM = 5.8
DN_AF_LINE_MM = 4.3


def estimate_row_height_mm(row: dict, cols: list[dict], autofit: bool = False) -> float:
    max_lines = 1
    for c in cols:
        if c["kind"] != "text":
            continue
        v = str(row.get(c["key"], "") or "")
        if v:
            cpl = max(6.0, c["width"] * 0.9)
            lines = sum(max(1, math.ceil(len(seg) / cpl)) for seg in v.split("\n"))
            max_lines = max(max_lines, lines)
    if autofit:
        return DN_AF_BASE_MM + (max_lines - 1) * DN_AF_LINE_MM
    return DN_BASE_ROW_MM + (max_lines - 1) * DN_LINE_MM


def _footer_block_mm() -> float:
    return DN_SIGN_MM       # two sign boxes side by side, no terms


def _stretched_row_mm(area_mm: float, header_mm: float, extra_fixed_mm: float,
                      items_count: int) -> float:
    """
    Pick a per-row height that:
      - never goes below DN_BASE_ROW_MM (so dense pages stay compact and fit
        as many items as possible),
      - never goes above DN_MAX_ROW_MM (so sparse pages don't look absurd),
      - otherwise spreads available area evenly across items so few-item
        pages still fill the printable area.
    """
    if items_count <= 0:
        return DN_MAX_ROW_MM
    avail = max(0.0, area_mm - header_mm - extra_fixed_mm)
    natural = avail / items_count
    return max(DN_BASE_ROW_MM, min(DN_MAX_ROW_MM, natural))


def build_layout(items: list[dict], cols: list[dict],
                 include_prices: bool = False,
                 autofit: bool | None = None) -> list[dict]:
    """
    Phase L.3 - adaptive multi-page packing.

    Replaces the old hardcoded "2-page" logic that under-counted row heights
    (Python said 14mm, CSS forced 22mm) - which caused 20-item DNs to spill
    onto a third page with just 1-2 items. Now:

      1. Estimate each row's height using DN_BASE_ROW_MM as the minimum.
      2. Phase X.27 page-ROLE packing (see docs/12-PDF-LAYOUT-CONSTITUTION):
         page 1 at header_cap, every later page at body_cap (headerless pages
         no longer reserve the 75mm header, so middle pages fill), last page
         peeled to footer_cap.
      3. Per page: compute a `row_min_mm` that stretches rows to fill the
         available area, capped at DN_MAX_ROW_MM. Pass that to the template
         via the --dn-row-mm CSS variable so the rendered PDF matches the
         space the engine reserved.
    """
    if autofit is None:
        autofit = _dn_autofit()
    footer = _footer_block_mm()
    last_reserve = DN_CLOSING_MM + footer + DN_SAFE_GAP_MM
    grand_h = DN_GRAND_ROW_MM if include_prices else 0.0

    # Phase X.27 - page-role areas (headerless pages don't reserve the ~75mm
    # header; each page fills to its true height). Mirrors quote_generator.
    # ONE page-area model for all four document types (2026-09-05).
    # These four areas and four capacities were written out three times, in
    # three generators, and all three had drifted into the same defect - see
    # `backend/doc_layout.py`. The per-type numbers below are still measured
    # per type (docs/12 SS6 forbids copying one type's constant to another);
    # only the arithmetic over them is shared.
    geo = doc_layout.PageGeometry(
        usable_mm=DN_USABLE_MM,
        top_mm=DN_TOP_MM,
        cont_top_mm=DN_CONT_TOP_MM,
        head_row_mm=DN_HEADER_ROW_MM,
        pagenum_mm=DN_PAGENUM_MM,
        last_reserve_mm=last_reserve,
        last_fixed_in_table_mm=grand_h,
        chrome_mm=_dn_page_chrome_mm(),
        safety_mm=DN_PAGE_SAFETY_MM,
    )
    header_area, body_area = geo.header_area, geo.body_area
    footer_area, single_area = geo.footer_area, geo.single_area
    header_cap = geo.capacity(doc_layout.HEADER_PAGE)
    body_cap   = geo.capacity(doc_layout.BODY_PAGE)
    footer_cap = geo.capacity(doc_layout.FOOTER_PAGE)
    single_cap = geo.capacity(doc_layout.SINGLE_PAGE)

    heights = [estimate_row_height_mm(it, cols, autofit) for it in items]

    def idx(rows, start=1):
        return [{**r, "_index": i} for i, r in enumerate(rows, start=start)]

    # ---- Single page: everything fits in the single-page area (header+footer) ----
    if not items or sum(heights) <= single_cap:
        row_min = _stretched_row_mm(single_area, DN_HEADER_ROW_MM, grand_h, len(items))
        return [{
            "show_header": True, "show_footer": True, "is_last": True,
            "fill_table": False,
            "table_area_mm": round(single_area, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": DN_HEADER_ROW_MM,
            # The grand-total row lives INSIDE <tbody>, so the data-row
            # height variable reaches it unless it is given its own
            # (2026-09-05). Publishing the ENGINE's reserve means the row
            # renders at exactly what was booked for it, and the two cannot
            # drift. See docs/12 SS4.6.
            "grand_row_mm": DN_GRAND_ROW_MM,
            "rows": idx(items),
            "page_index": 1,
            "total_pages": 1,
        }]

    # ---- Multi-page: fill each page to its OWN capacity (page 1 = header_cap,
    # every later page = body_cap). No even-balancing (that spread rows thin). ----
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

    # The last page also carries the footer (footer_cap < body_cap): peel any
    # overflow onto a new final page so the sign boxes never collide.
    while (chunks[-1][1] - chunks[-1][0]) > 1 and \
            sum(heights[chunks[-1][0]:chunks[-1][1]]) > footer_cap:
        s_last, e_last = chunks[-1]
        chunks[-1] = (s_last, e_last - 1)
        chunks.append((e_last - 1, e_last))

    # Light orphan guard - avoid a lonely 1-row last page.
    if len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) == 1:
        s_prev, e_prev = chunks[-2]
        s_last, e_last = chunks[-1]
        prev_cap = header_cap if len(chunks) == 2 else body_cap
        if (e_prev - s_prev) >= 3 and \
                sum(heights[s_prev:e_prev - 1]) <= prev_cap and \
                sum(heights[s_last - 1:e_last]) <= footer_cap:
            chunks[-2] = (s_prev, e_prev - 1)
            chunks[-1] = (s_last - 1, e_last)

    # Build the page dicts
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
        row_min = _stretched_row_mm(page_area, DN_HEADER_ROW_MM, extra_fixed, count)
        pages.append({
            "show_header": p_i == 0,            # header only on first page
            "show_footer": is_last,             # closing+signs only on last
            "is_last": is_last,
            "fill_table": (not is_last) and not autofit,
            "table_area_mm": round(page_area, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": DN_HEADER_ROW_MM,
            "grand_row_mm": DN_GRAND_ROW_MM,
            "rows": idx(items[s:e], start=s + 1),
            "page_index": p_i + 1,
            "total_pages": total,
        })
    return pages


def _cell(row: dict, col: dict, qty: float, rate: float) -> dict:
    kind, k = col["kind"], col["key"]
    if kind == "index":
        val = str(row.get("_index", ""))
    elif kind == "computed":
        val = _money(qty * rate) if (qty or rate) else ""
    elif kind == "money":
        raw = row.get(k, "")
        val = _money(_num(raw)) if str(raw).strip() != "" else ""
    else:
        val = str(row.get(k, "") or "")
    return {"val": val, "align": col["align"]}


def _apply_col_widths(cols: list[dict], dn: dict) -> list[dict]:
    """Override the data columns' widths with the live editor widths (Excel-
    style column resizing) in dn["col_widths"] = {key: pct}, then renormalize
    so the data columns keep their original combined width (the NO. column
    share is untouched). No-op when col_widths is absent - older delivery
    notes render exactly as before."""
    cw = dn.get("col_widths")
    if not isinstance(cw, dict) or not cw:
        return cols
    data = [c for c in cols if c.get("key") != "_no"]
    orig_sum = sum(c["width"] for c in data)
    changed = False
    for c in data:
        try:
            w = float(cw.get(c["key"]))
        except (TypeError, ValueError):
            w = 0.0
        if w and w > 0:
            c["width"] = w
            changed = True
    if not changed:
        return cols
    new_sum = sum(c["width"] for c in data)
    if new_sum > 0 and orig_sum > 0:
        scale = orig_sum / new_sum
        for c in data:
            c["width"] = round(c["width"] * scale, 2)
    return cols


def render_dn_html(dn: dict, inline_css: bool = False) -> str:
    include_prices = bool(dn.get("include_prices"))
    # Phase T - a DN's own base columns (per document) when present.
    doc_cols = dn.get("columns")
    cols = _columns(include_prices, doc_cols if (isinstance(doc_cols, list) and doc_cols) else None)
    cols = _apply_col_widths(cols, dn)
    items = dn.get("items", [])
    autofit = _dn_autofit(dn.get("layout"))
    pages = build_layout(items, cols, include_prices=include_prices, autofit=autofit)

    for page in pages:
        disp = []
        for row in page["rows"]:
            qty = _num(row.get("qty"))
            rate = _num(row.get("rate"))
            disp.append([_cell(row, c, qty, rate) for c in cols])
        page["display_rows"] = disp

    # Grand-total row spans (only used when include_prices). Layout:
    #   "Total" (NO + Item)  |  words (description..qty)  |  amount (rate + total)
    grand_total = None
    if include_prices:
        grand = round(sum(_num(it.get("qty")) * _num(it.get("rate")) for it in items), 2)
        n_cols = len(cols)
        label_span  = 2                          # NO + Item
        amount_span = 2                          # Rate + Total
        words_span  = max(1, n_cols - label_span - amount_span)
        grand_total = {
            "label_span":  label_span,
            "words_span":  words_span,
            "amount_span": amount_span,
            "words":       amount_in_words(grand),
            "amount":      "${:,.2f}".format(grand),
        }

    from . import signing as _signing
    sig_ctx = _signing.build_template_context(
        "dn", dn.get("signing_overlay"), dn.get("dn_no"),
    )
    render_ctx = {
        "dn": dn,
        "columns": cols,
        "pages": pages,
        "autofit": autofit,
        "company": config.DN_COMPANY,
        "brand": config.DN_BRAND_COLOR,
        "logo_data_uri": _logo_data_uri(),
        "include_prices": include_prices,
        "grand_total": grand_total,
        "inline_css": inline_css,
        "css_text": _read_css() if inline_css else "",
        "css_href": "../static/dn_styles.css",
        **sig_ctx,
    }

    template_name = "dn_page.html"
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import eshraqat_render

        template_name = "eshraqat/dn_page.html"
        render_ctx.update(
            eshraqat_render.common_context(
                title=config.DN_COMPANY.get("title", "Delivery Note"),
                doc_no=dn.get("dn_no", ""),
                pages=len(pages),
            )
        )
        render_ctx["css_href"] = "../static/eshraqat_print.css"
        # One header layout for quotation, delivery note and invoice - see
        # eshraqat_render.meta_rows for the arrangement and why.
        render_ctx["meta_rows"] = eshraqat_render.meta_rows("dn", dn)

    template = _env.get_template(template_name)
    return template.render(
        **render_ctx,
    )


def render_dn_pdf(dn: dict, out_path: Path) -> Path:
    html = render_dn_html(dn, inline_css=True)
    # Locked LAST - backend/pdf_lock.py. Opens with no password, prints, cannot be edited.
    written = doc_verify.stamp_pdf(pdf_engine.write_pdf(html, out_path), "dn", dn)
    return pdf_lock.lock(written)
