"""How many times each month an LPO has been invoiced (2026-10-06).

Waleed: "the counting should be based on how many times that LPO has been
invoiced per month - it can help me know if I am forgetting an invoice for this
month or other month". This replaced a service-period ledger that matched
invoice descriptions to anniversary periods and asked for dates to be
confirmed; it read as a puzzle, and the question is simpler than that.

The rule, per kind:

* **A contract with an end date** (a rental, usually) needs one invoice per
  month of its term - `database._term_months(start, end)` invoices.
* **A contract with no end date** needs one invoice every month until it is
  closed by hand.
* Counting starts at the month of the FIRST invoice, or the month after the
  start, whichever is earlier. HDEC is billed after each month of service: a
  rental starting 24 July gets its first invoice in August, so starting the
  count in July would call July forgotten. CSSP-RENT-0002 is billed in the same
  month instead (starts 8 June, first invoice 30 June) - the first invoice
  settles which it is.
* A month is FORGOTTEN once it has ended with no invoice. The current month
  reads "not invoiced yet" and is never an alarm. A month with two invoices
  shows 2.
* An invoice counts in the month it is DATED, as soon as it exists (issued -
  drafts with no PDF never count). Whether it was sent is a separate question,
  answered by `invoices_not_sent`; the LPO as a whole is done only when every
  month is invoiced AND every invoice is marked sent.

A materials order is not monthly; it has no month ledger.
"""
from __future__ import annotations
import calendar
from collections import Counter
from datetime import date, timedelta
from typing import Any
from . import database


def _day(value):
    return database._parse_doc_date(value)


def _next(start: date, offset: int) -> date:
    """`start` moved by whole months, the day clamped to the month's length."""
    month = start.year * 12 + start.month - 1 + offset
    year, month = divmod(month, 12)
    return date(year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1]))


def _month(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _shift(ym: tuple[int, int], n: int) -> tuple[int, int]:
    y, m = divmod(ym[0] * 12 + ym[1] - 1 + n, 12)
    return (y, m + 1)


def _label(ym: tuple[int, int]) -> str:
    return f"{calendar.month_name[ym[1]]} {ym[0]}"


def invoice_index() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Every issued invoice against an LPO, grouped by LPO identity, read ONCE.

    Drafts (no PDF) are not bills anyone sent, and `N/A` is not an LPO.
    `sent` is the invoice's Submitted mark (`submitted_docs`)."""
    identities = database._identities()
    with database._connect() as conn:
        sent = {r["doc_no"] for r in conn.execute(
            "SELECT doc_no FROM submitted_docs WHERE kind = 'inv'")}
        rows = conn.execute(
            "SELECT inv_no, inv_date, client_name, lpo_no, grand_total FROM invoices "
            "WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
            "AND TRIM(COALESCE(lpo_no,'')) NOT IN ('','N/A','n/a')").fetchall()
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = database._usage_key(r["client_name"], r["lpo_no"], identities)
        index.setdefault(key, []).append({
            "inv_no": r["inv_no"], "inv_date": r["inv_date"],
            "invoiced_usd": r["grand_total"], "sent": r["inv_no"] in sent})
    return index


def invoices_for(lpo: dict[str, Any], index=None) -> list[dict[str, Any]]:
    index = invoice_index() if index is None else index
    return index.get(database._usage_key(lpo.get("client_name", ""), lpo.get("lpo_no", "")), [])


def months(lpo: dict[str, Any], index=None, today: date | None = None) -> dict[str, Any]:
    """The month ledger for one contract LPO - see the module docstring."""
    today = today or date.today()
    invoices = sorted(invoices_for(lpo, index),
                      key=lambda r: (_day(r["inv_date"]) or date.min, r["inv_no"]))
    not_sent = [r["inv_no"] for r in invoices if not r["sent"]]
    out: dict[str, Any] = {
        "ok": True, "months": [], "months_total": 0, "months_invoiced": 0,
        "months_unbilled": 0, "oldest_due_month": "", "oldest_due_date": "",
        "this_month_due": False, "this_month_label": "", "term_months": None,
        "billing_complete": False, "invoices_not_sent": len(not_sent),
        "not_sent": not_sent,
    }
    if database._lpo_kind(lpo.get("kind")) == "materials":
        return out

    start = _day(lpo.get("period_start")) or _day(lpo.get("lpo_date"))
    end = _day(lpo.get("period_end"))
    if end and start and end < start:
        end = None
    dated = [(r, _day(r["inv_date"])) for r in invoices]
    counts = Counter(_month(d) for _, d in dated if d)
    numbers: dict[tuple[int, int], list[str]] = {}
    for r, d in dated:
        if d:
            numbers.setdefault(_month(d), []).append(r["inv_no"])

    first = min(counts) if counts else None
    after_start = _shift(_month(start), 1) if start else None
    candidates = [m for m in (first, after_start) if m]
    if not candidates:
        return out
    anchor = min(candidates)
    now = _month(today)

    term = database._term_months(start, end) if (start and end) else None
    if term:
        expected = [_shift(anchor, k) for k in range(term)]
    else:
        closed = _day(str(lpo.get("closed_at") or "")[:10])
        last = min(now, _month(closed)) if closed else now
        expected = []
        m = anchor
        while m <= last:
            expected.append(m)
            m = _shift(m, 1)

    rows = []
    for ym in sorted(set(expected) | set(counts)):
        n = counts.get(ym, 0)
        is_expected = ym in expected
        if not is_expected:
            state = "extra"
        elif n:
            state = "invoiced"
        elif ym < now:
            state = "missed"
        elif ym == now:
            state = "current"
        else:
            state = "upcoming"
        row = {"month": f"{ym[0]:04d}-{ym[1]:02d}", "label": _label(ym), "count": n,
               "invoices": numbers.get(ym, []), "expected": is_expected, "state": state}
        if is_expected and start:
            # The service month this invoice covers - handed to the invoice
            # editor so its description carries the dates.
            k = expected.index(ym)
            a = _next(start, k)
            b = _next(start, k + 1) - timedelta(days=1)
            if end:
                b = min(b, end)
            row.update({"start": a.isoformat(), "end": b.isoformat(),
                        "covers": f"{a:%d %b} - {b:%d %b %Y}"})
        rows.append(row)

    missed = [r for r in rows if r["state"] == "missed"]
    current = next((r for r in rows if r["state"] == "current"), None)
    closed_by_hand = bool(lpo.get("closed_at"))
    out.update({
        "months": rows,
        "months_total": len(expected),
        "months_invoiced": sum(1 for r in rows if r["expected"] and r["count"]),
        # A contract closed by hand stops asking.
        "months_unbilled": 0 if closed_by_hand else len(missed),
        "oldest_due_month": "" if closed_by_hand or not missed else missed[0]["label"],
        # The INVOICE month (first of it), not the service dates: an in-arrears
        # rental's November invoice covers late October, and "Invoice October"
        # would name the wrong month.
        "oldest_due_date": "" if closed_by_hand or not missed else missed[0]["month"] + "-01",
        "this_month_due": bool(current) and not closed_by_hand,
        "this_month_label": current["label"] if current else "",
        "term_months": term,
        "billing_complete": bool(term) and all(r["count"] for r in rows if r["expected"]),
    })
    return out


def reference_gaps():
    """Issued document references with no same-client LPO. Never guess a match."""
    identities = {}
    def key(client, number):
        if client not in identities:
            identities[client] = database._usage_key(client, "")[0]
        return identities[client], str(number or "").strip().lower()
    result = []
    with database._connect() as conn:
        known = {key(r["client_name"],r["lpo_no"]) for r in conn.execute("SELECT client_name,lpo_no FROM client_lpos")}
        for kind,table,col in (("dn","dns","dn_no"),("inv","invoices","inv_no")):
            rows = conn.execute(f"SELECT {col} AS doc_no,client_name,lpo_no FROM {table} "
                "WHERE TRIM(COALESCE(pdf_path,''))<>'' AND TRIM(COALESCE(lpo_no,'')) NOT IN ('','N/A','n/a')")
            for r in rows:
                if key(r["client_name"],r["lpo_no"]) not in known:
                    result.append({"kind":kind,**dict(r)})
    return sorted(result,key=lambda r:(r["client_name"],r["doc_no"]))
