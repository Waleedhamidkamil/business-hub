"""
Dashboard analytics - aggregate queries that power the "This Month" card and
the full Insights view. All figures come from the saved-document tables
(quotes / invoices / dns / pos); invoices carry the money
(grand_total), so revenue metrics are invoice-based.

Everything is computed with a handful of GROUP BY queries (cheap even with
thousands of rows). No external dependencies - the frontend draws the charts
as inline SVG.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta as _td
from typing import Any

from . import config, clients, signing, sqlite_policy

_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Doc tables that carry a (year, month); invoices also carry grand_total.
_DOC_TABLES = {
    "quotes":   "quotes",
    "invoices": "invoices",
    "dns":      "dns",
    "pos":      "pos",
}

# inv_type / quote_type -> display label (mirrors the frontend sub-type names).
_CATEGORY_LABEL = {
    "materials": "Materials",
    "equipment": "Equipment",
    "manpower":  "Manpower",
    "service":   "Service",
}


def _connect() -> sqlite3.Connection:
    return sqlite_policy.open_db(config.DB_PATH)


def _month_window(months_back: int, ry: int, rm: int) -> list[tuple[int, int]]:
    """The last `months_back` (year, month) pairs ending at (ry, rm), oldest first."""
    keys: list[tuple[int, int]] = []
    y, m = ry, rm
    for _ in range(max(1, months_back)):
        keys.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    return keys


def _ym(year: int, month: int) -> int:
    """A sortable/compareable integer key for a (year, month)."""
    return year * 12 + (month - 1)


def _grouped_counts(conn: sqlite3.Connection, table: str,
                    with_money: bool = False) -> dict[tuple[int, int], dict]:
    """{ (year, month): {"c": count, "s": sum_grand_total} } for one table."""
    sel = "year, month, COUNT(*) AS c"
    if with_money:
        sel += ", COALESCE(SUM(grand_total), 0) AS s"
    where = (" WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
             if table in ("quotes", "invoices") else "")
    out: dict[tuple[int, int], dict] = {}
    try:
        for r in conn.execute(
                f"SELECT {sel} FROM {table}{where} GROUP BY year, month"):
            out[(r["year"], r["month"])] = {
                "c": r["c"],
                "s": (r["s"] if with_money else 0.0),
            }
    except sqlite3.Error:
        pass
    return out


def _signed_ratio(conn: sqlite3.Connection) -> dict[str, int]:
    """Signed vs unsigned across every saved document (all types, all-time).
    Intersects the stored overlays with rows that still exist."""
    pairs = [("quotes", "quote_no", "qut"), ("invoices", "inv_no", "inv"),
             ("dns", "dn_no", "dn"), ("pos", "po_no", "po")]
    total = 0
    signed = 0
    for table, pk, dtype in pairs:
        try:
            where = (" WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
                     if table in ("quotes", "invoices") else "")
            nos = {(r[pk] or "").strip()
                   for r in conn.execute(f"SELECT {pk} FROM {table}{where}")}
        except sqlite3.Error:
            nos = set()
        total += len(nos)
        signed += len(signing.signed_doc_nos(dtype) & nos)
    return {"signed": signed, "unsigned": max(0, total - signed), "total": total}


def dashboard_analytics(months_back: int = 12,
                        ref_year: int | None = None,
                        ref_month: int | None = None) -> dict[str, Any]:
    """One call → everything the Insights view + enriched card need."""
    now = datetime.now()
    ry = ref_year or now.year
    rm = ref_month or now.month
    months_back = min(24, max(3, int(months_back or 12)))
    window = _month_window(months_back, ry, rm)
    lo, hi = _ym(*window[0]), _ym(*window[-1])

    with _connect() as conn:
        grouped = {name: _grouped_counts(conn, tbl, with_money=(name == "invoices"))
                   for name, tbl in _DOC_TABLES.items()}

        # ---- monthly series over the window ----
        months: list[dict] = []
        totals = {"quotes": 0, "invoices": 0, "dns": 0, "pos": 0,
                  "docs": 0, "invoiced": 0.0}
        for (y, m) in window:
            row = {
                "y": y, "m": m,
                "label": _MONTH_ABBR[m] + (f" '{str(y)[2:]}" if m == 1 or (y, m) == window[0] else ""),
                "ym": f"{y:04d}-{m:02d}",
            }
            month_docs = 0
            for name in _DOC_TABLES:
                cell = grouped[name].get((y, m))
                c = cell["c"] if cell else 0
                row[name] = c
                month_docs += c
                totals[name] += c
            inv_cell = grouped["invoices"].get((y, m))
            row["invoiced"] = round(inv_cell["s"], 2) if inv_cell else 0.0
            row["docs"] = month_docs
            totals["docs"] += month_docs
            totals["invoiced"] += row["invoiced"]
            months.append(row)
        totals["invoiced"] = round(totals["invoiced"], 2)

        # ---- invoice value by category (within the window) ----
        inv_by_cat: list[dict] = []
        try:
            rows = conn.execute(
                "SELECT inv_type, COUNT(*) c, COALESCE(SUM(grand_total),0) s "
                "FROM invoices WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
                "AND (year*12 + (month-1)) BETWEEN ? AND ? "
                "GROUP BY inv_type", (lo, hi)).fetchall()
            for r in rows:
                t = (r["inv_type"] or "").lower()
                inv_by_cat.append({
                    "type": t,
                    "label": _CATEGORY_LABEL.get(t, t.title() or "Other"),
                    "count": r["c"],
                    "total": round(r["s"], 2),
                })
            inv_by_cat.sort(key=lambda x: x["total"], reverse=True)
        except sqlite3.Error:
            pass

        # ---- quoted value over the window (for the funnel: pipeline vs realized) ----
        quoted_value = 0.0
        try:
            for r in conn.execute(
                    "SELECT rows_json FROM quotes "
                    "WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
                    "AND (year*12 + (month-1)) BETWEEN ? AND ?",
                    (lo, hi)):
                quoted_value += clients._quote_value(r["rows_json"])
        except sqlite3.Error:
            pass
        totals["quoted"] = round(quoted_value, 2)

        # ---- daily document counts (created_at) for the activity heatmap ----
        # 18 full weeks back from today, so the grid always starts on a Sunday.
        heat_start = (now - _td(days=18 * 7 - 1)).strftime("%Y-%m-%d")
        daily: dict[str, int] = {}
        for tbl in _DOC_TABLES.values():
            try:
                issued_only = (
                    " AND TRIM(COALESCE(pdf_path,'')) <> ''"
                    if tbl in ("quotes", "invoices") else ""
                )
                for r in conn.execute(
                        f"SELECT substr(created_at,1,10) d, COUNT(*) c FROM {tbl} "
                        f"WHERE substr(created_at,1,10) >= ?{issued_only} GROUP BY d",
                        (heat_start,)):
                    if r["d"]:
                        daily[r["d"]] = daily.get(r["d"], 0) + r["c"]
            except sqlite3.Error:
                pass

        signed = _signed_ratio(conn)

    # ---- top clients (all-time, by invoiced business) ----
    try:
        cl = clients.client_list() or []
    except Exception:
        cl = []
    top_clients = sorted(
        cl, key=lambda c: (c.get("invoiced_total") or 0, c.get("doc_count") or 0),
        reverse=True)[:8]
    top_clients = [{
        "name": c.get("name", ""),
        "invoiced": round(c.get("invoiced_total") or 0, 2),
        "quotes": c.get("quotes") or 0,
        "invoices": c.get("invoices") or 0,
        "dns": c.get("dns") or 0,
        "docs": c.get("doc_count") or 0,
    } for c in top_clients]

    # ---- KPIs: this month vs last month ----
    this_m = months[-1] if months else {"docs": 0, "invoiced": 0.0}
    last_m = months[-2] if len(months) >= 2 else {"docs": 0, "invoiced": 0.0}

    def _delta(cur: float, prev: float):
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)

    # Quote -> invoice ratio over the window (a useful proxy, clearly labelled).
    conv = None
    if totals["quotes"]:
        conv = round(totals["invoices"] / totals["quotes"] * 100, 1)

    period_label = f"{_MONTH_ABBR[window[0][1]]} {window[0][0]} – {_MONTH_ABBR[window[-1][1]]} {window[-1][0]}"

    return {
        "ok": True,
        "period_label": period_label,
        "months": months,
        "totals": totals,
        "inv_by_category": inv_by_cat,
        "top_clients": top_clients,
        "signed": signed,
        "kpis": {
            "this_month": {"docs": this_m["docs"], "invoiced": this_m.get("invoiced", 0.0)},
            "last_month": {"docs": last_m["docs"], "invoiced": last_m.get("invoiced", 0.0)},
            "docs_delta_pct": _delta(this_m["docs"], last_m["docs"]),
            "invoiced_delta_pct": _delta(this_m.get("invoiced", 0.0), last_m.get("invoiced", 0.0)),
            "quote_to_invoice_pct": conv,
        },
        "daily": daily,
        "daily_max": max(daily.values()) if daily else 0,
    }
