"""
Invoice HTML renderer + PDF generator (English, LTR) - schema-driven.

Matches INV REF.pdf:
  - Logo + company-name lines header, navy "<Type> Invoice" title
  - Meta block: Date / Invoice No. / LPO No. / Client Name / Client Address
  - Items table (No / Item / Description / Unit/Size / Q'ty / Rate / Total)
    with two trailing rows INSIDE the table:
      * Withholding Tax (3.3 %)  ........ Included
      * Total | amount-in-words | $X.XX
  - Closing line "Thank You..."
  - Payment Info card (bottom-left) + General Manager signature (bottom-right),
    both pinned to the page bottom on the last page only.

Reuses amount_in_words from quote_generator (so a single source of truth).
"""
from __future__ import annotations

import base64
import math
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, doc_verify, pdf_engine, pdf_lock
from . import doc_layout
from .quote_generator import amount_in_words, _money, _num


_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# --------------------------------------------------------------------------- #
# Layout constants (mm, A4 portrait). Larger footer than DN because the
# Payment Info card + signature live on the last page.
# --------------------------------------------------------------------------- #
def _iov(key, default):
    o = getattr(config, "INV_LAYOUT", {}) or {}
    return float(o.get(key, default))

def _inv_show_withholding(doc: dict | None = None) -> bool:
    """Whether this invoice prints the "Withholding Tax (x %)" row (2026-10-11).

    Waleed asked for it off INV-EQ-JUNE-105 and nowhere else, so it is a
    PER-DOCUMENT choice carried in the document's own layout (`doc_layout`,
    which already travels with every invoice) - not a setting that would
    silently change the other 82 invoices on file. Default: printed.

    The row is also RESERVED in the page plan (`INV_TAX_ROW_MM`, inside the
    last page's table), so `build_layout` is told too. Hiding it in the
    template alone would leave 8mm booked and nothing drawn in it - the
    over-booking docs/12 SS4.6 is about.
    """
    layout = (doc or {}).get("layout") if isinstance(doc, dict) else None
    if isinstance(layout, dict) and "show_withholding" in layout:
        return bool(layout.get("show_withholding"))
    return True


def _inv_autofit(layout: dict | None = None) -> bool:
    """When on, every row hugs its text (no fixed/stretched row height)."""
    if isinstance(layout, dict) and "autofit_rows" in layout:
        return bool(layout.get("autofit_rows"))
    o = getattr(config, "INV_LAYOUT", {}) or {}
    return bool(o.get("autofit_rows"))

INV_USABLE_MM      = 273.0
# Phase M (item 12) - INV_TOP_MM bumped from 75 to 88 to reflect the real
# rendered header (logo ~21mm + title ~24mm + 5-row meta block ~32mm + rule ~1mm
# + padding ~10mm). Was undercounting and contributing to last-page overlap.
INV_TOP_MM         = _iov("top_mm", 88.0)
INV_TAX_ROW_MM     = 8.0
INV_TOTAL_ROW_MM   = 10.0
INV_CLOSING_MM     = 7.0
INV_PAYMENT_MM     = 42.0      # Payment Info card + signature block
INV_SAFE_GAP_MM    = _iov("safe_gap_mm", 6.0)
INV_HEADER_ROW_MM  = 9.0
# Gap reserved at the bottom of every non-last page for the page-number footer.
INV_PAGENUM_MM     = 8.0
# Phase X.27 - small top margin on HEADERLESS pages (continuation + last pages
# of a multi-page invoice). Only page 1 renders the ~88mm header block; the rest
# start at the top of the content box, so they must NOT reserve the full 88mm.
# Fixes the wasted band on middle pages. Mirrors quote/dn generators.
INV_CONT_TOP_MM    = 2.0
INV_PAGE_SAFETY_MM = 4.0
# Phase Y.2 - page chrome the Abraj-tuned areas above do NOT model, but the
# Eshraqat skin adds on EVERY page: ~20mm .esh-page bottom padding (footer/page-
# number band) + a taller table header than INV_HEADER_ROW_MM. Subtracted from
# every page area so build_layout packs rows that actually fit the Eshraqat page
# and never spills onto a near-empty continuation page. Abraj => 0 (unchanged).
INV_ESH_PAGE_CHROME_MM = 34.0


def _page_chrome_mm() -> float:
    return INV_ESH_PAGE_CHROME_MM if config.ACTIVE_PROFILE == "eshraqat" else 0.0
# Phase L.3 - INV looks good visually per user, so we KEEP base=14mm
# (matches the CSS `.i-table tbody tr { height: 14mm; }` rule). Only the
# multi-page packing logic is upgraded - same balanced-tail behaviour as DN.
INV_BASE_ROW_MM    = _iov("min_row_mm", 14.0)
INV_LINE_MM        = 5.0
# Phase M (item 12) - MAX dropped 22 -> 17 so rows don't bloat aesthetically
# on filler (non-last) pages. Last pages no longer stretch at all (see
# build_layout).
INV_MAX_ROW_MM     = _iov("max_row_mm", 17.0)


def _rebalance_widths(cols: list[dict], target_total: float = 95.4) -> list[dict]:
    """Match the NO.-column-takes-the-rest convention used by quote_generator.

    Phase M (item 12) - also scale UP when the column total is under target.
    A stale user override that doesn't sum to 95.4 leaves columns artificially
    narrow → text wraps → row heights bloat → last-page footer overlap.
    """
    explicit = sum(c.get("width", 0) for c in cols if c.get("width"))
    missing  = [c for c in cols if not c.get("width")]
    remaining = max(0.0, target_total - explicit)
    per = (remaining / len(missing)) if missing else 0.0
    for c in missing:
        c["width"] = per
    actual = sum(c["width"] for c in cols)
    if actual > 0 and abs(actual - target_total) > 0.5:
        scale = target_total / actual
        for c in cols:
            c["width"] = round(c["width"] * scale, 2)
    return cols


def _schema_for(inv_type: str) -> dict:
    """Schema with per-sub-type overrides applied (columns / column_heads).
    Phase L.4 - override columns inherit width/kind from defaults if missing."""
    base = config.INV_TYPES.get(inv_type, config.INV_TYPES["materials"])
    overrides = (getattr(config, "INV_SUBTYPES_OVERRIDES", {}) or {}).get(inv_type) or {}
    custom_cols = overrides.get("columns")
    heads       = overrides.get("column_heads") or {}

    if custom_cols:
        cols = [dict(c) for c in custom_cols]
        # Phase L.4 - inherit width/kind from defaults when missing.
        default_by_key = {c["key"]: c for c in base["columns"]}
        for c in cols:
            d = default_by_key.get(c.get("key"))
            if not d:
                continue
            if not c.get("width"):
                c["width"] = d.get("width", 0)
            if not c.get("kind"):
                c["kind"] = d.get("kind", "text")
    elif heads:
        cols = [{**c} for c in base["columns"]]
    else:
        return base

    if heads:
        cols = [
            {**c, **({"head": heads[c["key"]]} if c["key"] in heads else {})}
            for c in cols
        ]
    cols = _rebalance_widths(cols)
    return {**base, "columns": cols}


def _schema_for_doc(inv: dict) -> dict:
    """Phase T - per-document columns: build the schema from the invoice's own
    `columns` when present, else fall back to `_schema_for` (existing invoices
    render unchanged). Missing width/kind inherit from the type default."""
    inv_type = inv.get("inv_type", "materials")
    doc_cols = inv.get("columns")
    if not (isinstance(doc_cols, list) and doc_cols):
        return _schema_for(inv_type)
    base = config.INV_TYPES.get(inv_type, config.INV_TYPES["materials"])
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
        return _schema_for(inv_type)
    cols = _rebalance_widths(cols)
    return {**base, "columns": cols}


def _logo_data_uri() -> str | None:
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        p = config.ASSETS_DIR / name
        if p.exists():
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return None


def _read_css() -> str:
    stylesheet = (
        "eshraqat_print.css"
        if config.ACTIVE_PROFILE == "eshraqat"
        else "inv_styles.css"
    )
    return (config.STATIC_DIR / stylesheet).read_text(encoding="utf-8")


def _columns(schema: dict) -> list[dict]:
    """Schema columns + leading NO. index column. Widths rebalanced to ~95.4."""
    cols = [{"key": "_no", "head": "NO.", "kind": "index", "width": 4.6, "align": "center"}]
    for c in schema["columns"]:
        cols.append({**c, "align": c.get("align", "center")})
    return cols


# Auto-fit measured metrics (10pt font): single line ~5.5mm, +4.2mm per line.
INV_AF_BASE_MM = 5.8
INV_AF_LINE_MM = 4.3


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
        return INV_AF_BASE_MM + (max_lines - 1) * INV_AF_LINE_MM
    return INV_BASE_ROW_MM + (max_lines - 1) * INV_LINE_MM


# Eshraqat invoice closing block: amount-in-words + payment (left) and the
# Subtotal/Withholding/Grand totals (right), then the signature pushed to the
# bottom - taller than the Abraj payment card, and the tax/total rows are NOT in
# the table. Reserve enough vertical space so a full last page never overlaps.
INV_ESH_CLOSEOUT_MM = 58.0


def _footer_block_mm() -> float:
    return INV_ESH_CLOSEOUT_MM if config.ACTIVE_PROFILE == "eshraqat" else INV_PAYMENT_MM


def _i_stretched_row_mm(area_mm: float, header_mm: float, extra_fixed_mm: float,
                        items_count: int) -> float:
    if items_count <= 0:
        return INV_MAX_ROW_MM
    avail = max(0.0, area_mm - header_mm - extra_fixed_mm)
    natural = avail / items_count
    return max(INV_BASE_ROW_MM, min(INV_MAX_ROW_MM, natural))


def build_layout(items: list[dict], cols: list[dict],
                 autofit: bool | None = None,
                 show_withholding: bool = True) -> list[dict]:
    """
    Phase L.3 - multi-page pagination with balanced tail (same shape as DN).
    Tax / Total rows still only appear on the last page; payment + signature
    block stays pinned to the bottom of the last page.
    """
    if autofit is None:
        autofit = _inv_autofit()
    footer = _footer_block_mm()
    last_reserve = INV_CLOSING_MM + footer + INV_SAFE_GAP_MM
    # Eshraqat renders the tax + total OUTSIDE the table (in the closing block,
    # like the quotation), so nothing is reserved inside the last page's table.
    last_fixed_in_table = 0.0 if config.ACTIVE_PROFILE == "eshraqat" \
        else ((INV_TAX_ROW_MM if show_withholding else 0.0)
              + INV_TOTAL_ROW_MM)   # tax (when printed) + total rows

    # Phase X.27 - page-role areas (headerless pages don't reserve the ~88mm
    # header). Mirrors quote/dn generators. INV keeps its own specifics: the
    # tax+total rows are reserved inside the LAST page's table, and the last
    # page NEVER stretches (Phase M item 12 anti-overlap rule).
    # ONE page-area model for all four document types (2026-09-05).
    # These four areas and four capacities were written out three times, in
    # three generators, and all three had drifted into the same defect - see
    # `backend/doc_layout.py`. The per-type numbers below are still measured
    # per type (docs/12 SS6 forbids copying one type's constant to another);
    # only the arithmetic over them is shared.
    geo = doc_layout.PageGeometry(
        usable_mm=INV_USABLE_MM,
        top_mm=INV_TOP_MM,
        cont_top_mm=INV_CONT_TOP_MM,
        head_row_mm=INV_HEADER_ROW_MM,
        pagenum_mm=INV_PAGENUM_MM,
        last_reserve_mm=last_reserve,
        last_fixed_in_table_mm=last_fixed_in_table,
        chrome_mm=_page_chrome_mm(),
        safety_mm=INV_PAGE_SAFETY_MM,
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

    # ---- Single page ----
    # Phase M (item 12) - don't stretch rows on a last/single page. With only
    # 5 items the old code stretched rows to 22mm each (110mm), leaving the
    # bank/sig footer ~6mm of breathing room and visually overlapping. Now
    # rows use natural BASE (or content height) so the table ends where it
    # naturally ends.
    if not items or sum(heights) <= single_cap:
        row_min = max(INV_BASE_ROW_MM, max(heights) if heights else INV_BASE_ROW_MM)
        return [{
            "show_header": True, "show_footer": True, "is_last": True,
            "fill_table": False,
            "table_area_mm": round(single_area, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": INV_HEADER_ROW_MM,
            # The tax and total rows live INSIDE <tbody>, so the data-row
            # height variable reaches them unless they are given their own
            # (2026-09-05). A wrapping description that pushed row_min to
            # 69mm made each of these 69mm too - against the 8mm and 10mm
            # the engine reserved - and shoved the payment/signature block
            # onto a second page. Publishing the ENGINE's own reserve means
            # they render at exactly what was booked. See docs/12 SS4.6.
            "tax_row_mm": INV_TAX_ROW_MM if show_withholding else 0.0,
            "total_row_mm": INV_TOTAL_ROW_MM,
            "rows": idx(items),
            "page_index": 1, "total_pages": 1,
        }]

    # ---- Multi-page: fill each page to its OWN capacity (page 1 = header_cap,
    # every later page = body_cap; headerless middle pages no longer waste the
    # 88mm header band), then make the last page fit the footer + tax/total. ----
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

    # Ensure the LAST chunk fits footer_cap (payment/sig + tax/total reserved).
    while True:
        ls, le = chunks[-1]
        if sum(heights[ls:le]) <= footer_cap or le - ls <= 1:
            break
        chunks[-1] = (ls, le - 1)
        chunks.append((le - 1, le))

    # Light balancing - only move items from prev to last if the last page
    # has way fewer items than prev (trailing 1-item page).
    while len(chunks) >= 2:
        s_prev, e_prev = chunks[-2]
        s_last, e_last = chunks[-1]
        if (e_last - s_last) >= max(1, (e_prev - s_prev) // 3):
            break
        new_e_prev = e_prev - 1
        new_s_last = s_last - 1
        if new_e_prev <= s_prev:
            break
        prev_cap = header_cap if len(chunks) == 2 else body_cap
        if sum(heights[s_prev:new_e_prev]) > prev_cap:
            break
        if sum(heights[new_s_last:e_last]) > footer_cap:
            break
        chunks[-2] = (s_prev, new_e_prev)
        chunks[-1] = (new_s_last, e_last)

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
        # Tax + Total rows live on the LAST page only - reserve them in the
        # row-height calculation for that page so the row stretching doesn't
        # leave them no room.
        extra_fixed = last_fixed_in_table if is_last else 0.0
        count = e - s
        # Phase M (item 12) - last page never stretches (avoids the 5-item
        # bloat-to-22mm bug). Filler pages still stretch so the table fills.
        page_heights = heights[s:e]
        if is_last:
            row_min = max(INV_BASE_ROW_MM,
                          max(page_heights) if page_heights else INV_BASE_ROW_MM)
        else:
            row_min = _i_stretched_row_mm(page_area, INV_HEADER_ROW_MM,
                                          extra_fixed, count)
        pages.append({
            "show_header": p_i == 0,
            "show_footer": is_last,
            "is_last": is_last,
            "fill_table": (not is_last) and not autofit,
            "table_area_mm": round(page_area, 1),
            "row_min_mm": round(row_min, 2),
            # The thead has its OWN reserve, separate from the data rows
            # (docs/12 SS4.6-4.7). Published so the print CSS consumes the
            # engine's number instead of keeping a second copy of it.
            "head_row_mm": INV_HEADER_ROW_MM,
            "tax_row_mm": INV_TAX_ROW_MM if show_withholding else 0.0,
            "total_row_mm": INV_TOTAL_ROW_MM,
            "rows": idx(items[s:e], start=s + 1),
            "page_index": p_i + 1,
            "total_pages": total,
        })
    return pages


def _cell(row: dict, col: dict, qty: float, rate: float, currency: str = "USD") -> dict:
    kind, k = col["kind"], col["key"]
    if kind == "index":
        val = str(row.get("_index", ""))
    elif kind == "computed":
        val = _money(qty * rate, currency) if (qty or rate) else _money(0, currency)
    elif kind == "money":
        raw = row.get(k, "")
        val = _money(_num(raw), currency) if str(raw).strip() != "" else ""
    else:
        val = str(row.get(k, "") or "")
    return {"val": val, "align": col["align"]}


def _bank_lookup(key: str) -> dict | None:
    """
    Return the INV_BANK_INFO entry with this key, or the first entry.
    Phase G.12: the special "cash" key returns None so the template knows
    to omit the Payment Info card.
    """
    if (key or "").lower() == "cash":
        return None
    banks = list(config.INV_BANK_INFO or [])
    if not banks:
        return None
    if key:
        for b in banks:
            if b.get("key") == key:
                return b
    return banks[0]


def _apply_col_widths(schema: dict, doc: dict) -> dict:
    """Override schema column widths with the live editor widths (Excel-style
    column resizing) in doc["col_widths"] = {key: pct}. No-op when absent, so
    older saved invoices are unaffected. Widths are rebalanced to ~95.4 the
    same way the fitting function does."""
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


def render_inv_html(inv: dict, inline_css: bool = False) -> str:
    inv_type = inv.get("inv_type", "materials")
    schema = _schema_for_doc(inv)                # Phase T - per-document columns
    schema = _apply_col_widths(schema, inv)
    cols = _columns(schema)
    items = inv.get("items", [])
    autofit = _inv_autofit(inv.get("layout"))
    show_withholding = _inv_show_withholding(inv)
    pages = build_layout(items, cols, autofit=autofit,
                         show_withholding=show_withholding)

    # Compute Grand Total (Q'ty x Rate sum). Withholding is INCLUDED in this
    # value when withholding_included=True (matches the user's template usage).
    grand = round(sum(_num(it.get("qty")) * _num(it.get("rate")) for it in items), 2)

    # 2026-07-29 - the currency this invoice is ISSUED in. Rates and totals are
    # already stored in it (the editor converts the cells when you flip the
    # switch), so rendering is purely a formatting concern. The USD equivalent
    # for analytics is computed in api.save_inv, not here.
    sell_currency = (inv.get("sell_currency") or "USD").upper()
    if sell_currency not in ("USD", "IQD"):
        sell_currency = "USD"

    for page in pages:
        disp = []
        for row in page["rows"]:
            qty, rate = _num(row.get("qty")), _num(row.get("rate"))
            disp.append([_cell(row, c, qty, rate, sell_currency) for c in cols])
        page["display_rows"] = disp

    # Column spans for the footer rows inside the table.
    n_cols = len(cols)
    # Tax row: everything except the final "Total" column spans the label.
    tax_label_span = n_cols - 1
    # Total row: "Total" spans the first 2 columns (No + Item),
    # words span the middle, $ amount sits in the last column.
    total_label_span  = 2
    total_amount_span = 1
    total_words_span  = max(1, n_cols - total_label_span - total_amount_span)

    bank = _bank_lookup(inv.get("payment_bank_key") or "")

    # Phase K.1 - digital signature overlay. The frontend passes
    # `signing_overlay` directly when previewing/saving so we render with
    # exactly what's on screen; on a cold load (reopen + re-render) we
    # fall back to whatever is persisted in doc_meta.
    from . import signing as _signing
    sig_ctx = _signing.build_template_context(
        "inv", inv.get("signing_overlay"), inv.get("inv_no"),
    )
    is_eshraqat = config.ACTIVE_PROFILE == "eshraqat"
    template_name = "eshraqat/inv_page.html" if is_eshraqat else "inv_page.html"
    css_href = (
        "../static/eshraqat_print.css"
        if is_eshraqat
        else "../static/inv_styles.css"
    )
    context = {
        "inv": inv,
        "columns": cols,
        "pages": pages,
        "autofit": autofit,
        "company": config.INV_COMPANY,
        "brand": config.INV_BRAND_COLOR,
        "logo_data_uri": _logo_data_uri(),
        "schema_label": schema["label"],
        "bank": bank,
        "grand_amount": _money(grand, sell_currency),
        "grand_words": amount_in_words(grand, sell_currency),
        "sell_currency": sell_currency,
        "tax_label_span": tax_label_span,
        "total_label_span": total_label_span,
        "total_words_span": total_words_span,
        "total_amount_span": total_amount_span,
        "show_withholding": show_withholding,
        "withholding_status": (
            config.INV_COMPANY.get("tax_status_label", "Included")
            if inv.get("withholding_included", True)
            else "Not Included"
        ),
        "inline_css": inline_css,
        "css_text": _read_css() if inline_css else "",
        "css_href": css_href,
        **sig_ctx,
    }
    if is_eshraqat:
        from . import eshraqat_render

        # Eshraqat invoices are a single "Invoice" family - header shows the
        # document type, not the sub-type (materials/equipment/…). (User ask.)
        context.update(eshraqat_render.common_context(
            title="Invoice",
            doc_no=inv.get("inv_no", ""),
            pages=len(pages),
        ))
        # The same header as the quotation and delivery note - one layout
        # for all three, built in ONE place (eshraqat_render.meta_rows). Client
        # and Address used to be full-width rows here, which is why the address
        # was not under Linked Quotation and sat indented differently.
        context["meta_rows"] = eshraqat_render.meta_rows("inv", inv)
    template = _env.get_template(template_name)
    html = template.render(**context)
    # Invoice-from-DNs (2026-07-18): when the caller resolved DN documents for the
    # invoice's `dn_ref` lines (Api.preview_inv / save_inv set inv["_dn_docs"]),
    # append each DN's pages so ONE headless render produces invoice + DN pages.
    return _append_dn_pages(html, inv.get("_dn_docs") or [])


# One A4 sheet holding nothing but the client's returned scan, sized to fit
# inside the same 12mm @page margin the invoice and DN already use.
_SIGNED_COPY_STYLE = """<style>
.dn-signed-copy-page {
    break-before: page;
    width: 186mm;            /* A4 210 - 2x12mm @page margin */
    height: 273mm;           /* A4 297 - 2x12mm */
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}
.dn-signed-copy-page img {
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;     /* never crop or stretch the client's document */
}
/* The placeholder token only has to survive into the PDF's extractable text -
   the page is replaced by the signed copy (or dropped) before anyone sees it. */
.dn-signed-copy-token { font-size: 6pt; color: #fdfdfd; }
</style>"""


def _signed_copy_page_html(dn: dict) -> str:
    """A full-page sheet carrying the client's signed scan for this DN."""
    return ('<div class="dn-signed-copy-page">'
            f'<img src="{dn["signed_copy_image"]}" alt="">'
            "</div>")


def _signed_copy_placeholder_html(dn: dict) -> str:
    """A one-page marker that `_merge_signed_pdfs` swaps for a PDF signed copy.

    The token is rendered as ordinary (tiny, faint) text so it survives into the
    PDF's extractable text and the merge can LOCATE this page rather than
    computing its index - page-count estimates drift, a search does not. The page
    never reaches the user: it is either replaced by the signed copy or removed.
    """
    return ('<div class="dn-signed-copy-page">'
            f'<span class="dn-signed-copy-token">{dn["signed_copy_token"]}</span>'
            "</div>")


def _append_dn_pages(invoice_html: str, dn_docs: list) -> str:
    """Splice each DN after the invoice pages into ONE document.

    Invoice and DN share the same A4 @page geometry, and their page content is
    scoped by distinct classes (`.inv-*` vs `.dn-*`; under the eshraqat skin both
    use one shared stylesheet), so their pages can coexist. Each DN stylesheet is
    included once and only if the invoice doesn't already carry it. A `break-
    before: page` wrapper starts each DN on a fresh page. Missing DN docs (deleted
    after invoicing) are skipped.

    2026-07-30 - a DN carrying `signed_copy_image` (the client's returned scan,
    resolved by `api._resolve_dn_docs`) contributes THAT single page instead of a
    re-render. The re-render only ever shows our own supplier signature, so it
    reads as an unacknowledged delivery; the scan proves the delivery. It is
    embedded as a data URI so the existing single-pass render still produces the
    whole document - no PDF library involved. A PDF signed copy cannot be spliced
    into HTML, so those still fall back to the re-render (save_inv says so).
    """
    docs = [d for d in (dn_docs or []) if d]
    if not docs:
        return invoice_html
    import re
    from . import dn_generator
    extra_styles: list[str] = []
    extra_body: list[str] = []
    seen: set[str] = set()
    for dn in docs:
        if dn.get("signed_copy_image") or dn.get("signed_copy_pdf"):
            if _SIGNED_COPY_STYLE not in extra_styles:
                extra_styles.append(_SIGNED_COPY_STYLE)
            extra_body.append(
                _signed_copy_page_html(dn) if dn.get("signed_copy_image")
                else _signed_copy_placeholder_html(dn))
            continue
        try:
            dn_html = dn_generator.render_dn_html(dn, inline_css=True)
        except Exception:
            continue
        for st in re.findall(r"<style[^>]*>.*?</style>", dn_html, re.S):
            if st not in seen and st not in invoice_html:
                seen.add(st)
                extra_styles.append(st)
        m = re.search(r"<body[^>]*>(.*)</body>", dn_html, re.S)
        body = (m.group(1) if m else "").strip()
        if body:
            extra_body.append(
                '<div style="break-before:page;">' + body + "</div>")
    out = invoice_html
    if extra_styles:
        out = out.replace("</head>", "".join(extra_styles) + "</head>", 1)
    if extra_body:
        out = out.replace("</body>", "".join(extra_body) + "</body>", 1)
    return out


_A4_PT = (595.276, 841.890)      # A4 in PDF points (210 x 297 mm)


def _place_on_a4(page, writer):
    """Return `page` centred on an A4 sheet, scaled to fit without distortion.

    A client's scan can be Letter, A5 or a phone-camera aspect ratio; dropping
    those into the invoice as-is gives a document whose sheets change size in the
    printer tray. Aspect ratio is preserved - a signed document must never be
    stretched.
    """
    from pypdf import PageObject, Transformation

    # A scanner very often writes a page with a LANDSCAPE mediabox plus a
    # /Rotate flag telling the viewer to stand it up - which is why the scan
    # looks portrait in a PDF reader. `merge_transformed_page` copies the
    # CONTENT and drops that flag, so the page below would silently come out
    # sideways in the invoice (2026-09-02, DN-HDEC-047: 842x596 with /Rotate
    # 270). Bake the rotation into the content stream first, so the geometry we
    # then measure is the geometry the reader actually shows.
    if int(page.get("/Rotate") or 0) % 360:
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass                                      # better sideways than lost

    box = page.mediabox
    w, h = float(box.width), float(box.height)
    if w <= 0 or h <= 0:
        return page
    aw, ah = _A4_PT
    if abs(w - aw) < 1 and abs(h - ah) < 1:
        return page                                   # already A4, leave it alone
    scale = min(aw / w, ah / h)
    sheet = PageObject.create_blank_page(width=aw, height=ah)
    sheet.merge_transformed_page(
        page,
        Transformation()
        .scale(scale)
        .translate((aw - w * scale) / 2, (ah - h * scale) / 2),
    )
    return sheet


def _merge_signed_pdfs(pdf_path: Path, dn_docs: list) -> Path:
    """Swap each placeholder page for the client's signed-copy PDF pages.

    2026-07-30. Images ride inline in the HTML render; PDFs cannot, so the HTML
    leaves a one-page placeholder carrying `signed_copy_token` and this step
    replaces it. The placeholder is found by SEARCHING the rendered text for that
    token rather than by computing a page index - the invoice's own page count
    and each DN's page count are both estimates, and the PO pagination work
    already showed those drift. Searching cannot drift.

    Failure is contained: a signed copy that cannot be read leaves its
    placeholder removed rather than shipping a page of stray marker text.
    """
    pending = [d for d in (dn_docs or []) if d.get("signed_copy_pdf")]
    if not pending:
        return pdf_path

    from pypdf import PdfReader, PdfWriter

    base = PdfReader(str(pdf_path))
    by_token = {d["signed_copy_token"]: d for d in pending}
    writer = PdfWriter()

    for page in base.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        hit = next((tok for tok in by_token if tok in text), None)
        if hit is None:
            writer.add_page(page)
            continue
        doc = by_token.pop(hit)               # each token appears once
        try:
            src = PdfReader(doc["signed_copy_pdf"])
            if src.is_encrypted:
                src.decrypt("")
            for sp in src.pages:
                writer.add_page(_place_on_a4(sp, writer))
        except Exception:
            # Validated at resolve time, so this is a genuine surprise. Drop the
            # placeholder rather than emit a page of marker text; save_inv's
            # notes tell the user the copy did not travel.
            doc["signed_copy_merge_failed"] = True

    writer.write(str(pdf_path))
    writer.close()
    return pdf_path


def render_inv_pdf(inv: dict, out_path: Path) -> Path:
    html = render_inv_html(inv, inline_css=True)
    written = pdf_engine.write_pdf(html, out_path)
    # Post-render because a PDF signed copy cannot be spliced into HTML. Runs on
    # EVERY path that produces an invoice PDF - preview, save and re-sign all
    # come through here - so what you preview is what you send.
    merged = _merge_signed_pdfs(Path(written), inv.get("_dn_docs") or [])
    # Locked LAST - backend/pdf_lock.py. Opens with no password, prints, cannot be edited.
    return pdf_lock.lock(doc_verify.stamp_pdf(merged, "inv", inv))
