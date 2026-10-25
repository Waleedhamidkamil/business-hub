"""
Business & client insights - the aggregate engine behind the Insights view.

Two entry points, both windowed on (end month, N months back):

    insights_overview(months, end_year, end_month)  -> company + every client
    client_insights(name, months, ...)              -> one client, deep

**Why this module exists rather than more `analytics.py`.** `analytics.py`
counts DOCUMENTS (how many quotes, how many invoices, activity per day);
everything here is MONEY, and money in this app is not a stored column - profit
is recomputed on read from the per-row costs inside `rows_json` (see
[[09-DATA-MODEL]]). `database.profit_summary` already does that correctly, but
it re-opens the document one at a time (`load_doc_costing` -> `load_quote` ->
`get_cost_currency` -> `doc_iqd_per_usd` -> ...), which is fine for one month
and far too chatty for "every client over 12 months".

So this module keeps the SAME arithmetic - it calls `database._line_econ`, the
one function that turns a row into revenue/cost - but resolves every per-document
lookup ONCE into an index (`_Ctx`) and then does the whole window in a single
pass. `tests/insights_parity_backend.test.py` pins the two against each other:
if this file's numbers ever drift from `profit_summary` / `doc_iqd_per_usd`, that
test fails. Never "optimize" the arithmetic here - change `_line_econ` instead.

Vocabulary used throughout (and in the UI):
    charged   invoiced revenue, USD  - what we billed the client
    spent     line costs + extra costs, USD - what it cost us
    profit    charged - spent        (margin = profit / charged)
    pipeline  OPEN quotations only - won quotes are counted via their invoice,
              lost ones not at all, exactly as `profit_summary` does it.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from . import config, database, client_master, sqlite_policy

_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# The windows the UI offers. 1 = "this month only".
ALLOWED_WINDOWS = (1, 3, 6, 12, 24)

_CATEGORY_LABEL = {
    "materials": "Materials",
    "equipment": "Equipment",
    "manpower": "Manpower",
    "service": "Service",
}

UNNAMED_CLIENT = "(no client)"


@contextmanager
def _connect():
    """Read-only connection that is actually CLOSED afterwards.

    `with sqlite_policy.open_db(...)` - the idiom used elsewhere in this package -
    commits but does not close, and this module opens a connection per call
    while the user clicks through months and clients."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    # Client identity has ONE rule - client_master.normalize: Unicode casefold
    # plus collapsed inner whitespace. SQL's COLLATE NOCASE does neither, so an
    # alias resolved in Python while its documents stayed invisible to the
    # query. Teach the connection the real rule (see backend/clients.py).
    conn.create_function("client_key", 1, client_master.normalize, deterministic=True)
    try:
        yield conn
    finally:
        conn.close()


def _num(v: Any) -> float:
    try:
        f = float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0
    return f


def _round(v: float, nd: int = 2) -> float:
    return round(float(v or 0.0), nd)


def _pct(part: float, whole: float, nd: int = 1) -> float:
    return round((part / whole * 100.0), nd) if whole else 0.0


def _ym(year: int, month: int) -> int:
    return int(year) * 12 + (int(month) - 1)


def normalize_window(months: Any, end_year: Any = None,
                     end_month: Any = None) -> tuple[list[tuple[int, int]], int, int, int]:
    """(window, end_year, end_month, months) - window oldest-first.

    `months` is clamped to ALLOWED_WINDOWS' range but NOT snapped to one of the
    presets: a caller asking for 4 months gets 4. The UI only offers the presets.
    """
    now = datetime.now()
    try:
        ey = int(end_year) if end_year else now.year
    except (TypeError, ValueError):
        ey = now.year
    try:
        em = int(end_month) if end_month else now.month
    except (TypeError, ValueError):
        em = now.month
    em = min(12, max(1, em))
    try:
        n = int(months or 12)
    except (TypeError, ValueError):
        n = 12
    n = min(max(ALLOWED_WINDOWS), max(1, n))

    window: list[tuple[int, int]] = []
    y, m = ey, em
    for _ in range(n):
        window.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    window.reverse()
    return window, ey, em, n


def _month_label(y: int, m: int, first: bool = False) -> str:
    return _MONTH_ABBR[m] + (f" '{y % 100:02d}" if (m == 1 or first) else "")


def _is_document_line(row: dict[str, Any] | None, name: str,
                      known_dns: set[str]) -> bool:
    """True when an invoice line IS a whole delivery note billed as one line.

    The invoice editor can drop a DN in as a single summary row (item = the DN
    number, qty 1, rate = the DN's total) so its pages ride along with the
    invoice. That row is real revenue - it belongs in charged / spent / profit
    - but it is a DOCUMENT, not something this client buys, and letting it into
    the "Top items" roll-up pushed the actual items out of the list: eight of
    TDC's top twelve "items" were delivery-note numbers (2026-08-25).

    Three tests, cheapest first, and none of them guesses:
      1. `dn_ref` - the marker the picker writes. Only lines saved since
         2026-07-18 have it, which is why the other two exist.
      2. the name IS a delivery-note number in this profile.
      3. the name is a single token with a bare `DN` segment (`DN-TDC-024`,
         `TDC-DN-6`) - this catches DNs from an older numbering scheme that no
         longer sit in the table. An item name has words; a document number
         does not, so "Drinking Water" can never match.
    """
    if isinstance(row, dict) and str(row.get("dn_ref") or "").strip():
        return True
    n = (name or "").strip()
    if not n or len(n.split()) > 1:
        return False
    if n.upper() in known_dns:
        return True
    return any(seg.strip().upper() == "DN" for seg in n.split("-"))


# --------------------------------------------------------------------------- #
# The index - every per-document lookup resolved once
# --------------------------------------------------------------------------- #

class _Ctx:
    """Batched replacements for the per-document lookups in `database`.

    Each attribute mirrors exactly one function there; the mirror is what
    `tests/insights_parity_backend.test.py` asserts against.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.cost_meta: dict[tuple[str, str], tuple[str, float | None]] = {}
        try:
            for r in conn.execute("SELECT kind, doc_no, currency, rate FROM doc_cost_meta"):
                cur = (r["currency"] or "USD").upper()
                if cur not in database._COST_CURRENCIES:
                    cur = "USD"
                try:
                    rate = float(r["rate"]) if r["rate"] is not None else None
                except (TypeError, ValueError):
                    rate = None
                self.cost_meta[(r["kind"], r["doc_no"])] = (cur, rate if (rate or 0) > 0 else None)
        except sqlite3.Error:
            pass

        # invoices: sell currency, the rate actually used, the deal root, cash flag
        self.inv_meta: dict[str, dict[str, Any]] = {}
        cols = "inv_no, source_quote_no, payment_bank_key, grand_total"
        for extra in ("sell_currency", "sell_iqd_per_usd"):
            try:
                conn.execute(f"SELECT {extra} FROM invoices LIMIT 1").fetchone()
                cols += f", {extra}"
            except sqlite3.Error:               # pre-migration DB
                pass
        try:
            for r in conn.execute(
                    f"SELECT {cols} FROM invoices "
                    "WHERE TRIM(COALESCE(pdf_path,'')) <> ''"):
                d = dict(r)
                sell = (d.get("sell_currency") or "USD").upper()
                try:
                    peg = float(d.get("sell_iqd_per_usd") or 0)
                except (TypeError, ValueError):
                    peg = 0.0
                self.inv_meta[d["inv_no"]] = {
                    "sell_currency": sell if sell in database._COST_CURRENCIES else "USD",
                    "sell_peg": peg if peg > 0 else None,
                    "source_quote_no": (d.get("source_quote_no") or "").strip(),
                    "cash": str(d.get("payment_bank_key") or "").strip().lower() == "cash",
                    "grand_total": _num(d.get("grand_total")),
                }
        except sqlite3.Error:
            pass

        # the cash-deal index (mirrors database._cash_deal_index)
        self.cash_invs = {no for no, m in self.inv_meta.items() if m["cash"]}
        self.cash_roots = {m["source_quote_no"] for no, m in self.inv_meta.items()
                           if m["cash"] and m["source_quote_no"]}
        # a DN's root quote, so a cash deal re-pegs its delivery notes too
        self.dn_root: dict[str, str] = {}
        try:
            for r in conn.execute("SELECT dn_no, source_quote_no FROM dns"):
                self.dn_root[r["dn_no"]] = (r["source_quote_no"] or "").strip()
        except sqlite3.Error:
            pass

        # Every delivery-note number in this profile, for `_is_document_line`.
        # `dn_root` is already the full DN index, so this costs one set build.
        self.dn_numbers = {str(no).strip().upper() for no in self.dn_root}

        self.won_quotes = {m["source_quote_no"] for m in self.inv_meta.values()
                           if m["source_quote_no"]}
        # quote -> the earliest linked invoice date (speed-to-invoice)
        self.quote_invoiced_on: dict[str, str] = {}
        try:
            for r in conn.execute(
                    "SELECT source_quote_no, inv_date FROM invoices "
                    "WHERE TRIM(COALESCE(source_quote_no,'')) <> '' "
                    "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
                q = (r["source_quote_no"] or "").strip()
                prev = self.quote_invoiced_on.get(q)
                cur = r["inv_date"] or ""
                if q and cur and (not prev or cur < prev):
                    self.quote_invoiced_on[q] = cur
        except sqlite3.Error:
            pass

        try:
            self.paid_invs = database.paid_invoice_set()
        except Exception:
            self.paid_invs = set()
        try:
            self.submitted = {k: database.submitted_set(k) for k in ("quote", "inv", "dn")}
        except Exception:
            self.submitted = {"quote": set(), "inv": set(), "dn": set()}

        self.eshraqat = (config.ACTIVE_PROFILE == "eshraqat")
        self.profile_peg = float(config.IQD_PER_USD)
        self.cash_peg = float(database.CASH_IQD_PER_USD)

    # -- mirrors database.get_cost_currency -------------------------------- #
    def cost_currency(self, kind: str, doc_no: str) -> str:
        return self.cost_meta.get((kind, doc_no), ("USD", None))[0]

    # -- mirrors database.doc_iqd_per_usd ---------------------------------- #
    def peg(self, kind: str, doc_no: str) -> float:
        """Same precedence as `database.doc_iqd_per_usd`, from the index.

        pinned rate > invoice's recorded rate > eshraqat > cash deal > profile.
        Keep these branches in the same order - the parity test compares this
        against the real function for every document in the database.
        """
        doc_no = (doc_no or "").strip()
        if doc_no and kind in ("quote", "dn", "inv"):
            pinned = self.cost_meta.get((kind, doc_no), ("USD", None))[1]
            if pinned:
                return pinned
        if kind == "inv" and doc_no:
            stored = (self.inv_meta.get(doc_no) or {}).get("sell_peg")
            if stored:
                return stored
        if self.eshraqat:
            return self.cash_peg
        if not doc_no or kind not in ("quote", "dn", "inv"):
            return self.profile_peg
        if not self.cash_roots and not self.cash_invs:
            return self.profile_peg
        if kind == "inv" and doc_no in self.cash_invs:
            return self.cash_peg
        root = (doc_no if kind == "quote"
                else (self.inv_meta.get(doc_no) or {}).get("source_quote_no")
                if kind == "inv" else self.dn_root.get(doc_no))
        if root and root in self.cash_roots:
            return self.cash_peg
        return self.profile_peg

    def sell_currency(self, kind: str, doc_no: str) -> str:
        if kind != "inv":
            return "USD"
        return (self.inv_meta.get(doc_no) or {}).get("sell_currency", "USD")


# --------------------------------------------------------------------------- #
# One document -> its economics
# --------------------------------------------------------------------------- #

def _doc_econ(ctx: _Ctx, kind: str, doc_no: str, sub_type: str,
              rows_json: str | None, extra_json: str | None,
              want_items: bool = False) -> dict[str, Any]:
    """revenue / cost / profit for one document, in USD.

    Deliberately delegates the per-row arithmetic to `database._line_econ` so
    the Insights numbers and the Costing screen's numbers can never disagree.
    """
    try:
        rows = json.loads(rows_json or "[]")
    except (TypeError, ValueError):
        rows = []
    pk = database._primary_key_for(kind, sub_type)
    currency = ctx.cost_currency(kind, doc_no)
    rate = ctx.peg(kind, doc_no)
    sell_ccy = ctx.sell_currency(kind, doc_no)

    revenue = cost = est = 0.0
    billable = 0
    all_costed = True
    uncosted_lines = 0
    items: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        e = database._line_econ(r, pk, currency, rate, sell_ccy)
        revenue += e["revenue"]
        cost += e["line_cost"]
        est += e["est_cost"]
        if e["billable"]:
            billable += 1
            if not (e["has_cost"] and e["qty"] > 0):
                all_costed = False
                uncosted_lines += 1
        if want_items and (e["revenue"] or e["line_cost"]):
            items.append({
                "name": e["name"] or "-",
                "qty": _round(e["qty"], 3),
                "charged": _round(e["revenue"]),
                "spent": _round(e["line_cost"]),
                "profit": _round(e["line_profit"]),
                # its `spent` is the UNCOSTED_MARKUP estimate, not a real cost
                "estimated": e["est_cost"] > 0,
                # A whole delivery note billed as one line is revenue, not an
                # item - kept in the totals, kept OUT of "Top items".
                "is_doc": _is_document_line(r, e["name"], ctx.dn_numbers),
            })

    extra_total = 0.0
    try:
        for x in json.loads(extra_json or "[]"):
            if isinstance(x, dict):
                extra_total += database.cost_to_usd(x.get("amount"), currency, rate)
    except (TypeError, ValueError):
        pass

    spent = cost + extra_total
    return {
        "charged": revenue,
        "spent": spent,
        "profit": revenue - spent,
        "lines": billable,
        "costed": bool(billable > 0 and all_costed),
        # WHY a document lost money needs more than the total (2026-08-26):
        # `uncosted_lines` says how much of it is unknown rather than known,
        # and `extra` is the document-level cost that is in `spent` but in no
        # line, so a document can be in the red with every line healthy.
        "uncosted_lines": uncosted_lines,
        # how much of `spent` is the UNCOSTED_MARKUP estimate (2026-10-21)
        "estimated": est,
        "extra": extra_total,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# The windowed pass
# --------------------------------------------------------------------------- #

def _collect(window: list[tuple[int, int]], client: str | None = None,
             want_items: bool = False) -> tuple[list[dict[str, Any]], _Ctx]:
    """Every quote / invoice / DN in the window, with its economics attached."""
    lo, hi = _ym(*window[0]), _ym(*window[-1])
    like = (client or "").strip()
    out: list[dict[str, Any]] = []

    with _connect() as conn:
        ctx = _Ctx(conn)
        args_tail = tuple(client_master.alias_keys_for(like)) if like else ()
        where_client = (" AND client_key(client_name) IN (" +
                        ",".join("?" for _ in args_tail) + ")") if like else ""

        for r in conn.execute(
            "SELECT quote_no, quote_date, quote_type, client_name, year, month, "
            "bid_status, rows_json, extra_costs_json, pdf_path, updated_at "
            "FROM quotes WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
            "AND (year*12 + (month-1)) BETWEEN ? AND ?" + where_client,
            (lo, hi) + args_tail,
        ):
            no = r["quote_no"]
            econ = _doc_econ(ctx, "quote", no, r["quote_type"],
                             r["rows_json"], r["extra_costs_json"], want_items)
            status = database.effective_bid_status(
                r["bid_status"], no in ctx.won_quotes, r["quote_date"])
            out.append({**econ, "kind": "quote", "no": no, "date": r["quote_date"] or "",
                        "sub_type": (r["quote_type"] or ""), "client": r["client_name"] or "",
                        "y": r["year"], "m": r["month"], "status": status,
                        "submitted": no in ctx.submitted.get("quote", set()),
                        "paid": None, "pdf": r["pdf_path"] or "",
                        "updated_at": r["updated_at"] or ""})

        for r in conn.execute(
            "SELECT inv_no, inv_date, inv_type, client_name, year, month, "
            "rows_json, extra_costs_json, grand_total, source_quote_no, pdf_path, updated_at "
            "FROM invoices WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
            "AND (year*12 + (month-1)) BETWEEN ? AND ?" + where_client,
            (lo, hi) + args_tail,
        ):
            no = r["inv_no"]
            econ = _doc_econ(ctx, "inv", no, r["inv_type"],
                             r["rows_json"], r["extra_costs_json"], want_items)
            out.append({**econ, "kind": "inv", "no": no, "date": r["inv_date"] or "",
                        "sub_type": (r["inv_type"] or ""), "client": r["client_name"] or "",
                        "y": r["year"], "m": r["month"], "status": None,
                        "submitted": no in ctx.submitted.get("inv", set()),
                        "paid": no in ctx.paid_invs,
                        "grand_total": _num(r["grand_total"]),
                        "source_quote_no": (r["source_quote_no"] or "").strip(),
                        "pdf": r["pdf_path"] or "", "updated_at": r["updated_at"] or ""})

        for r in conn.execute(
            "SELECT dn_no, dn_date, client_name, year, month, pdf_path, updated_at "
            "FROM dns WHERE (year*12 + (month-1)) BETWEEN ? AND ?" + where_client,
            (lo, hi) + args_tail,
        ):
            # DNs move goods, not money - counted, never summed into charged.
            out.append({"charged": 0.0, "spent": 0.0, "profit": 0.0, "lines": 0,
                        "costed": True, "items": [], "kind": "dn", "no": r["dn_no"],
                        "date": r["dn_date"] or "", "sub_type": "",
                        "client": r["client_name"] or "", "y": r["year"], "m": r["month"],
                        "status": None, "paid": None,
                        "submitted": r["dn_no"] in ctx.submitted.get("dn", set()),
                        "pdf": r["pdf_path"] or "", "updated_at": r["updated_at"] or ""})
    return out, ctx


def _blank_client(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "charged": 0.0, "spent": 0.0, "profit": 0.0,
        "invoiced_pdf": 0.0,
        "invoices": 0, "quotes": 0, "dns": 0,
        "biggest_invoice": 0.0,
        "outstanding": 0.0, "outstanding_count": 0, "collected": 0.0,
        "pipeline_value": 0.0, "pipeline_profit": 0.0,
        "won": 0, "lost": 0, "open": 0,
        "uncosted": 0, "uncosted_charged": 0.0,
        "_months": {}, "_cats": {}, "_convert_days": [],
    }


def _finish_client(c: dict[str, Any], window: list[tuple[int, int]],
                   totals: dict[str, Any]) -> dict[str, Any]:
    charged, spent = c["charged"], c["spent"]
    profit = charged - spent
    months = []
    for i, (y, m) in enumerate(window):
        cell = c["_months"].get((y, m)) or {}
        mc, ms = cell.get("charged", 0.0), cell.get("spent", 0.0)
        months.append({
            "ym": f"{y:04d}-{m:02d}", "label": _month_label(y, m, i == 0),
            "charged": _round(mc), "spent": _round(ms), "profit": _round(mc - ms),
            # The client series feeds the SAME charts as the company series, so
            # it has to carry `margin` too - the margin chart reads it directly
            # and silently plots NaN if it is missing.
            "margin": _pct(mc - ms, mc),
            "docs": int(cell.get("docs", 0)),
        })
    active = sum(1 for m in months if m["docs"])
    best = max(months, key=lambda m: m["profit"]) if months else None
    decided = c["won"] + c["lost"]
    convert = c.pop("_convert_days", [])
    cats = [{
        "type": k, "label": _CATEGORY_LABEL.get(k, (k or "other").title() or "Other"),
        "charged": _round(v["charged"]), "spent": _round(v["spent"]),
        "profit": _round(v["charged"] - v["spent"]), "count": v["count"],
    } for k, v in c.pop("_cats", {}).items()]
    cats.sort(key=lambda x: -x["charged"])

    c.pop("_months", None)
    c.update({
        "charged": _round(charged), "spent": _round(spent), "profit": _round(profit),
        "invoiced_pdf": _round(c["invoiced_pdf"]),
        "margin": _pct(profit, charged),
        # How many dollars we charge per dollar spent. 1.0 = at cost.
        "markup": round(charged / spent, 2) if spent > 0 else None,
        "avg_invoice": _round(charged / c["invoices"]) if c["invoices"] else 0.0,
        "biggest_invoice": _round(c["biggest_invoice"]),
        "outstanding": _round(c["outstanding"]), "collected": _round(c["collected"]),
        "pipeline_value": _round(c["pipeline_value"]),
        "pipeline_profit": _round(c["pipeline_profit"]),
        "win_rate": _pct(c["won"], decided) if decided else None,
        "uncosted_charged": _round(c["uncosted_charged"]),
        "docs": c["invoices"] + c["quotes"] + c["dns"],
        "months": months,
        "active_months": active,
        "best_month": (best["label"] if best and best["profit"] > 0 else ""),
        "categories": cats,
        "avg_days_to_invoice": (round(sum(convert) / len(convert)) if convert else None),
        "share_charged": _pct(charged, totals["charged"]),
        "share_profit": _pct(profit, totals["profit"]) if totals["profit"] > 0 else 0.0,
    })
    return c


def _all_time_activity(names: set[str]) -> dict[str, dict[str, str]]:
    """first_seen / last_activity per client, ALL-TIME (not window-scoped) -
    "we have worked with them since…" is context the window must not hide."""
    out: dict[str, dict[str, str]] = {}
    if not names:
        return out
    # Aliases share one history: an identity is "since 2026-06" because of the
    # oldest document under ANY of its names, so group by the identity rather
    # than by the spelling that happens to sit on the document.
    identities = client_master.canonical_map()

    def key_of(value: str) -> str:
        return client_master.normalize(client_master.canonical(value, identities))

    lookup = {key_of(n): n for n in names}
    with _connect() as conn:
        for table in ("quotes", "invoices", "dns"):
            try:
                rows = conn.execute(
                    f"SELECT client_name AS n, MIN(created_at) AS f, MAX(updated_at) AS l "
                    f"FROM {table} WHERE TRIM(COALESCE(client_name,'')) <> '' "
                    f"GROUP BY client_key(client_name)").fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                key = key_of(r["n"] or "")
                if key not in lookup:
                    continue
                slot = out.setdefault(lookup[key], {"first_seen": "", "last_activity": ""})
                if r["f"] and (not slot["first_seen"] or r["f"] < slot["first_seen"]):
                    slot["first_seen"] = r["f"]
                if r["l"] and r["l"] > slot["last_activity"]:
                    slot["last_activity"] = r["l"]
    return out


def _days_between(a: str, b: str) -> int | None:
    da, db = database._parse_doc_date(a), database._parse_doc_date(b)
    if not da or not db:
        return None
    return (db - da).days


# --------------------------------------------------------------------------- #
# Public - overview
# --------------------------------------------------------------------------- #

def insights_overview(months: Any = 12, end_year: Any = None,
                      end_month: Any = None) -> dict[str, Any]:
    """Company money + a full metric block for EVERY client in the window."""
    window, ey, em, n = normalize_window(months, end_year, end_month)
    docs, _ctx = _collect(window)

    totals = {
        "charged": 0.0, "spent": 0.0, "invoiced_pdf": 0.0,
        "invoices": 0, "quotes": 0, "dns": 0,
        "outstanding": 0.0, "outstanding_count": 0, "collected": 0.0,
        "pipeline_value": 0.0, "pipeline_profit": 0.0,
        "won": 0, "lost": 0, "open": 0,
        "uncosted": 0, "uncosted_charged": 0.0, "estimated_spent": 0.0,
        "unsubmitted": 0,
    }
    by_month: dict[tuple[int, int], dict[str, float]] = {}
    by_client: dict[str, dict[str, Any]] = {}
    by_cat: dict[str, dict[str, float]] = {}

    def month_cell(y: int, m: int) -> dict[str, float]:
        return by_month.setdefault((y, m), {
            "charged": 0.0, "spent": 0.0, "pipeline": 0.0,
            "invoices": 0, "quotes": 0, "docs": 0})

    # quote_no -> its date, so the quote→invoice gap is a lookup, not a scan
    # (this used to be a linear search inside the invoice loop, i.e. O(docs²)).
    quote_dates = {d["no"]: d["date"] for d in docs if d["kind"] == "quote"}

    client_identities = client_master.canonical_map()
    for d in docs:
        name = (d["client"] or "").strip() or UNNAMED_CLIENT
        name = client_master.canonical(name, client_identities)
        c = by_client.setdefault(client_master.normalize(name), _blank_client(name))
        cell = month_cell(d["y"], d["m"])
        cell["docs"] += 1
        cm = c["_months"].setdefault((d["y"], d["m"]),
                                     {"charged": 0.0, "spent": 0.0, "docs": 0})
        cm["docs"] += 1

        if d["kind"] == "inv":
            totals["invoices"] += 1
            cell["invoices"] += 1
            c["invoices"] += 1
            totals["charged"] += d["charged"]
            totals["spent"] += d["spent"]
            totals["invoiced_pdf"] += d.get("grand_total", 0.0)
            cell["charged"] += d["charged"]
            cell["spent"] += d["spent"]
            c["charged"] += d["charged"]
            c["spent"] += d["spent"]
            c["invoiced_pdf"] += d.get("grand_total", 0.0)
            cm["charged"] += d["charged"]
            cm["spent"] += d["spent"]
            c["biggest_invoice"] = max(c["biggest_invoice"], d["charged"])
            if d["paid"]:
                totals["collected"] += d["charged"]
                c["collected"] += d["charged"]
            else:
                totals["outstanding"] += d["charged"]
                totals["outstanding_count"] += 1
                c["outstanding"] += d["charged"]
                c["outstanding_count"] += 1
            if not d["costed"]:
                totals["uncosted"] += 1
                totals["uncosted_charged"] += d["charged"]
                totals["estimated_spent"] += d.get("estimated", 0.0)
                c["uncosted"] += 1
                c["uncosted_charged"] += d["charged"]
            cat = by_cat.setdefault((d["sub_type"] or "").lower(),
                                    {"charged": 0.0, "spent": 0.0, "count": 0})
            cat["charged"] += d["charged"]
            cat["spent"] += d["spent"]
            cat["count"] += 1
            ccat = c["_cats"].setdefault((d["sub_type"] or "").lower(),
                                         {"charged": 0.0, "spent": 0.0, "count": 0})
            ccat["charged"] += d["charged"]
            ccat["spent"] += d["spent"]
            ccat["count"] += 1
            src = d.get("source_quote_no") or ""
            if src in quote_dates:
                gap = _days_between(quote_dates[src], d["date"])
                if gap is not None and gap >= 0:
                    c["_convert_days"].append(gap)
        elif d["kind"] == "quote":
            totals["quotes"] += 1
            cell["quotes"] += 1
            c["quotes"] += 1
            st = d["status"]
            totals[st] = totals.get(st, 0) + 1
            c[st] = c.get(st, 0) + 1
            if st == "open":
                # Pipeline = OPEN quotes only. A won quote's money is already
                # counted through its invoice; a lost one is not money at all.
                totals["pipeline_value"] += d["charged"]
                totals["pipeline_profit"] += d["profit"]
                cell["pipeline"] += d["profit"]
                c["pipeline_value"] += d["charged"]
                c["pipeline_profit"] += d["profit"]
            if not d["submitted"]:
                totals["unsubmitted"] += 1
        else:
            totals["dns"] += 1
            c["dns"] += 1

    profit = totals["charged"] - totals["spent"]
    totals_out = {
        "charged": _round(totals["charged"]), "spent": _round(totals["spent"]),
        "profit": _round(profit), "margin": _pct(profit, totals["charged"]),
        "invoiced_pdf": _round(totals["invoiced_pdf"]),
        "invoices": totals["invoices"], "quotes": totals["quotes"], "dns": totals["dns"],
        "docs": totals["invoices"] + totals["quotes"] + totals["dns"],
        "outstanding": _round(totals["outstanding"]),
        "outstanding_count": totals["outstanding_count"],
        "collected": _round(totals["collected"]),
        "collected_pct": _pct(totals["collected"], totals["charged"]),
        "pipeline_value": _round(totals["pipeline_value"]),
        "pipeline_profit": _round(totals["pipeline_profit"]),
        "won": totals["won"], "lost": totals["lost"], "open": totals["open"],
        "win_rate": (_pct(totals["won"], totals["won"] + totals["lost"])
                     if (totals["won"] + totals["lost"]) else None),
        "uncosted": totals["uncosted"], "uncosted_charged": _round(totals["uncosted_charged"]),
        # the part of `spent` that is the estimate for lines with no cost, and
        # the markup it assumed (2026-10-21) - the UI says so beside the note
        "estimated_spent": _round(totals["estimated_spent"]),
        "estimate_markup_pct": round(config.UNCOSTED_MARKUP * 100),
        "unsubmitted": totals["unsubmitted"],
        "avg_invoice": _round(totals["charged"] / totals["invoices"]) if totals["invoices"] else 0.0,
        "clients": sum(1 for c in by_client.values() if c["invoices"] or c["quotes"] or c["dns"]),
    }

    month_rows = []
    for i, (y, m) in enumerate(window):
        cell = by_month.get((y, m)) or {"charged": 0.0, "spent": 0.0, "pipeline": 0.0,
                                        "invoices": 0, "quotes": 0, "docs": 0}
        p = cell["charged"] - cell["spent"]
        month_rows.append({
            "y": y, "m": m, "ym": f"{y:04d}-{m:02d}",
            "label": _month_label(y, m, i == 0),
            "charged": _round(cell["charged"]), "spent": _round(cell["spent"]),
            "profit": _round(p), "margin": _pct(p, cell["charged"]),
            "pipeline": _round(cell["pipeline"]),
            "invoices": cell["invoices"], "quotes": cell["quotes"], "docs": cell["docs"],
        })

    clients = [_finish_client(c, window, {"charged": totals["charged"], "profit": profit})
               for c in by_client.values()]
    activity = _all_time_activity({c["name"] for c in clients})
    for c in clients:
        a = activity.get(c["name"], {})
        c["first_seen"] = a.get("first_seen", "")
        c["last_activity"] = a.get("last_activity", "")
    clients.sort(key=lambda c: (-c["profit"], -c["charged"], c["name"].lower()))
    for i, c in enumerate(clients, start=1):
        c["rank"] = i

    cats = [{
        "type": k, "label": _CATEGORY_LABEL.get(k, (k or "other").title() or "Other"),
        "charged": _round(v["charged"]), "spent": _round(v["spent"]),
        "profit": _round(v["charged"] - v["spent"]),
        "margin": _pct(v["charged"] - v["spent"], v["charged"]), "count": v["count"],
    } for k, v in by_cat.items()]
    cats.sort(key=lambda x: -x["charged"])

    # Concentration - how much of the profit rides on the few biggest clients.
    earners = [c for c in clients if c["profit"] > 0]
    top_profit = sum(c["profit"] for c in earners[:3])
    concentration = {
        "top1": _pct(earners[0]["profit"], profit) if (earners and profit > 0) else 0.0,
        "top3": _pct(top_profit, profit) if profit > 0 else 0.0,
        "top1_name": earners[0]["name"] if earners else "",
        "earning_clients": len(earners),
    }

    return {
        "ok": True,
        "scope": "all",
        "months": n, "end_year": ey, "end_month": em,
        "period_label": _period_label(window),
        "totals": totals_out,
        "series": month_rows,
        "clients": clients,
        "categories": cats,
        "concentration": concentration,
    }


def _period_label(window: list[tuple[int, int]]) -> str:
    a, b = window[0], window[-1]
    if a == b:
        return f"{_MONTH_ABBR[a[1]]} {a[0]}"
    return f"{_MONTH_ABBR[a[1]]} {a[0]} – {_MONTH_ABBR[b[1]]} {b[0]}"


# --------------------------------------------------------------------------- #
# Public - one client
# --------------------------------------------------------------------------- #

def client_insights(name: str, months: Any = 12, end_year: Any = None,
                    end_month: Any = None) -> dict[str, Any]:
    """One client's full picture: the same metric block as the overview, plus
    their documents, their items, and the ranking context."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "No client selected."}

    # The overview reports each identity under its canonical display name, so
    # an alias request has to be resolved before it can find its own block -
    # otherwise it falls through to the empty one and reports zero.
    name = client_master.canonical(name)
    overview = insights_overview(months, end_year, end_month)
    key = client_master.normalize(name)
    block = next((c for c in overview["clients"]
                  if client_master.normalize(c["name"]) == key), None)
    window, ey, em, n = normalize_window(months, end_year, end_month)
    if block is None:
        block = _finish_client(_blank_client(name), window,
                               {"charged": 0.0, "profit": 0.0})
        block["rank"] = None
        act = _all_time_activity({name}).get(name, {})
        block["first_seen"] = act.get("first_seen", "")
        block["last_activity"] = act.get("last_activity", "")

    docs, _ctx = _collect(window, client=name, want_items=True)

    items: dict[str, dict[str, Any]] = {}
    # Delivery notes billed as a single invoice line: counted in the money,
    # summarised separately, never listed as items (see _is_document_line).
    dn_lines = {"count": 0, "charged": 0.0, "spent": 0.0, "profit": 0.0,
                "uncosted": 0}
    # Every invoice, split the two ways that actually get asked about
    # (2026-08-26): has it been PAID, and is it LOSING money - and if it is,
    # WHY. A total in the red says nothing you can act on.
    inv_rows: list[dict[str, Any]] = []
    inv_sum = {"count": 0, "charged": 0.0, "profit": 0.0,
               "paid": 0, "paid_charged": 0.0,
               "unpaid": 0, "unpaid_charged": 0.0,
               "losing": 0, "losing_loss": 0.0,
               "uncosted": 0, "uncosted_charged": 0.0}
    doc_rows: list[dict[str, Any]] = []
    for d in sorted(docs, key=lambda x: (x["date"] or "", x["no"]), reverse=True):
        doc_rows.append({
            "kind": d["kind"], "no": d["no"], "date": d["date"],
            "sub_type": d["sub_type"],
            "label": _CATEGORY_LABEL.get((d["sub_type"] or "").lower(), d["sub_type"] or ""),
            "charged": _round(d["charged"]), "spent": _round(d["spent"]),
            "profit": _round(d["profit"]),
            "margin": _pct(d["profit"], d["charged"]),
            "costed": d["costed"], "status": d["status"], "paid": d["paid"],
            "submitted": d["submitted"], "pdf": d["pdf"],
        })
        if d["kind"] != "inv":
            continue                      # item economics come from what we BILLED

        # ---- the invoice's own row + diagnosis -----------------------------
        losers = sorted((it for it in d["items"] if it["profit"] < -0.005),
                        key=lambda x: x["profit"])
        inv_sum["count"] += 1
        inv_sum["charged"] += d["charged"]
        inv_sum["profit"] += d["profit"]
        if d["paid"]:
            inv_sum["paid"] += 1
            inv_sum["paid_charged"] += d["charged"]
        else:
            inv_sum["unpaid"] += 1
            inv_sum["unpaid_charged"] += d["charged"]
        if d["profit"] < -0.005:
            inv_sum["losing"] += 1
            inv_sum["losing_loss"] += -d["profit"]
        if not d["costed"]:
            inv_sum["uncosted"] += 1
            inv_sum["uncosted_charged"] += d["charged"]
        inv_rows.append({
            "no": d["no"], "date": d["date"],
            "label": _CATEGORY_LABEL.get((d["sub_type"] or "").lower(),
                                         d["sub_type"] or ""),
            "charged": _round(d["charged"]), "spent": _round(d["spent"]),
            "profit": _round(d["profit"]), "margin": _pct(d["profit"], d["charged"]),
            "paid": d["paid"], "costed": d["costed"],
            "submitted": d["submitted"],
            # The three things that make an invoice lose money, in the order a
            # reader can act on them.
            "losing": d["profit"] < -0.005,
            "loss": _round(max(0.0, -d["profit"])),
            "losing_lines": [{"name": it["name"], "loss": _round(-it["profit"]),
                              "charged": it["charged"], "spent": it["spent"],
                              "is_doc": it.get("is_doc", False)}
                             for it in losers[:4]],
            "losing_line_count": len(losers),
            "losing_line_total": _round(sum(-it["profit"] for it in losers)),
            "uncosted_lines": d.get("uncosted_lines", 0),
            "extra": _round(d.get("extra", 0.0)),
            "lines": d.get("lines", 0),
        })
        for it in d["items"]:
            if it.get("is_doc"):
                dn_lines["count"] += 1
                dn_lines["charged"] += it["charged"]
                dn_lines["spent"] += it["spent"]
                dn_lines["profit"] += it["profit"]
                # the estimate fills `spent` now, so ask the flag, not the number
                if it.get("estimated") or not it["spent"]:
                    dn_lines["uncosted"] += 1
                continue
            key = it["name"].strip().lower()[:120]
            slot = items.setdefault(key, {"name": it["name"], "qty": 0.0, "charged": 0.0,
                                          "spent": 0.0, "profit": 0.0, "count": 0})
            slot["qty"] += it["qty"]
            slot["charged"] += it["charged"]
            slot["spent"] += it["spent"]
            slot["profit"] += it["profit"]
            slot["count"] += 1
    for k in ("charged", "spent", "profit"):
        dn_lines[k] = _round(dn_lines[k])
    for k in ("charged", "profit", "paid_charged", "unpaid_charged",
              "losing_loss", "uncosted_charged"):
        inv_sum[k] = _round(inv_sum[k])
    top_items = sorted(items.values(), key=lambda x: -x["charged"])[:12]
    for it in top_items:
        it["qty"] = _round(it["qty"], 3)
        it["charged"] = _round(it["charged"])
        it["spent"] = _round(it["spent"])
        it["profit"] = _round(it["profit"])
        it["margin"] = _pct(it["profit"], it["charged"])

    return {
        "ok": True,
        "scope": "client",
        "months": n, "end_year": ey, "end_month": em,
        "period_label": _period_label(window),
        "client": block,
        "company": overview["totals"],
        "client_count": len(overview["clients"]),
        "docs": doc_rows,
        # Invoices on their own: paid vs not, losing vs not, and why.
        "invoices": inv_rows,
        "invoice_summary": inv_sum,
        "items": top_items,
        # What was set aside, so nothing disappears silently. The UI says so
        # under the table - a filter the reader cannot see is a lie.
        "dn_lines": dn_lines,
    }
