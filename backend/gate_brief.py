"""What the launch card and the switch gate say about the day.

`card()` (2026-10-20) - the launch card's two rows, computed in the card's
OWN process while the app starts (backend/launch_gate.py), so it costs the app
nothing. Read-only, and quick: imports ~0.2 s cold, queries ~50 ms.

  this month  how many of each document were made this month
  agenda      the ONE most pressing thing, in Waleed's order: collect money
              owed, then deliver & invoice client POs, then follow up bids
              before they auto-lose, then submit unsent documents

(A third row, "last edited", was built and dropped the same day - Waleed
found it useless.)

The agenda is read from `database.agenda_feed()`, built by the same code as
the dashboard's Needs Attention, so the card and the To-Do never disagree.

`brief()` (2026-10-02) - the three lines on the page gate's Welcome door, shown
after a company switch. Waleed picked three facts to read while the app opens:

  2. money waiting for payment  - submitted invoices not marked paid
  3. documents not submitted    - invoices and delivery notes never sent
  7. this month so far          - documents made, and what was invoiced

Lines 2 and 3 are the dashboard's own Needs Attention buckets
(`database.dashboard_needs_attention`, muted entries already removed), so the
gate and the dashboard can never disagree about them.

Money always travels with its currency (house rule): a total is summed PER
`sell_currency` and printed as "USD 17,697" or "USD 1,200 + IQD 2,500,000",
never as a bare number and never by adding dinars to dollars.

A line whose count is zero is left out. If all three are zero the brief is a
single "all clear" line rather than three zeros.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from . import config, database, sqlite_policy

_KIND_NOUN = {"inv": ("invoice", "invoices"), "dn": ("delivery note", "delivery notes"),
              "quote": ("quotation", "quotations"), "po": ("purchase order", "purchase orders")}

# The launch card's tally, in the order the app lists the documents. Short
# nouns: it is one line on a small card. The tables are analytics._DOC_TABLES
# (pinned by tests/gate_backend.test.py) - not imported, to keep the card's
# process from loading analytics and everything it imports.
_TALLY = (("quotes", "quotation", "quotations"), ("pos", "PO", "POs"),
          ("dns", "DN", "DNs"), ("invoices", "invoice", "invoices"))
SEP = " · "                       # a middle dot between items


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _days(n: int) -> str:
    return _plural(int(n), "day", "days")


def money(totals: dict[str, float]) -> str:
    """'USD 17,697' / 'USD 1,200 + IQD 2,500,000'. Whole units: it is a glance."""
    parts = []
    for ccy in sorted(totals, key=lambda c: (c != "USD", c)):
        amount = totals[ccy]
        if round(amount) == 0:
            continue
        parts.append(f"{ccy} {round(amount):,.0f}")
    return " + ".join(parts)


def _currency_totals(conn: sqlite3.Connection, sql: str, args: tuple) -> dict[str, float]:
    out: dict[str, float] = {}
    for ccy, total in conn.execute(sql, args):
        key = str(ccy or "USD").strip().upper() or "USD"
        out[key] = out.get(key, 0.0) + float(total or 0)
    return out


def _unpaid_line(conn, items: list[dict]) -> dict | None:
    if not items:
        return None
    nos = [str(i["doc_no"]) for i in items]
    marks = ",".join("?" * len(nos))
    totals = _currency_totals(
        conn, f"SELECT sell_currency, SUM(sell_total) FROM invoices "
              f"WHERE inv_no IN ({marks}) GROUP BY sell_currency", tuple(nos))
    oldest = max(int(i.get("age_days") or 0) for i in items)
    value = money(totals) or _plural(len(items), "invoice", "invoices")
    return {"key": "unpaid", "value": value, "text": "waiting for payment",
            "detail": f"{_plural(len(items), 'invoice', 'invoices')}, oldest {_days(oldest)}"}


def _unsubmitted_line(items: list[dict]) -> dict | None:
    if not items:
        return None
    counts: dict[str, int] = {}
    for i in items:
        counts[i.get("kind") or ""] = counts.get(i.get("kind") or "", 0) + 1
    kinds = [k for k in ("inv", "dn", "quote", "po") if counts.get(k)]
    oldest = max(int(i.get("age_days") or 0) for i in items)
    if len(kinds) == 1:
        one, many = _KIND_NOUN[kinds[0]]
        value, split = _plural(len(items), one, many), ""
    else:
        value = _plural(len(items), "document", "documents")
        split = ", ".join(_plural(counts[k], *_KIND_NOUN[k]) for k in kinds) + ", "
    return {"key": "unsubmitted", "value": value, "text": "not submitted yet",
            "detail": f"{split}oldest {_days(oldest)}"}


def _month_line(conn, today: date) -> dict | None:
    from . import analytics
    # The same four tables the dashboard's "This Month" card counts. A missing
    # table is a real error, not a zero: let it raise.
    docs = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE year=? AND month=?",
                            (today.year, today.month)).fetchone()[0]
               for table in analytics._DOC_TABLES.values())
    if not docs:
        return None
    totals = _currency_totals(
        conn, "SELECT sell_currency, SUM(sell_total) FROM invoices "
              "WHERE year=? AND month=? GROUP BY sell_currency", (today.year, today.month))
    invoiced = money(totals)
    return {"key": "month", "value": _plural(docs, "document", "documents"),
            "text": "so far this month",
            "detail": f"{invoiced} invoiced" if invoiced else "nothing invoiced yet"}


def brief(today: date | None = None, feed: dict | None = None) -> dict[str, Any]:
    """{"ok": True, "lines": [...], "all_clear": bool} for the active company.

    `feed` is a Needs Attention result already in hand (start-up computes it
    once, in backend/boot_cache.py, and the brief reuses it)."""
    today = today or date.today()
    if feed is None:
        feed = database.dashboard_needs_attention()
    # closing(): sqlite3's own context manager only commits; it would leave
    # the file open (the scratch-database test caught that on Windows).
    with closing(sqlite_policy.open_db(config.DB_PATH, row_factory=False)) as conn:
        lines = [_unpaid_line(conn, feed.get("unpaid") or []),
                 _unsubmitted_line(feed.get("unsubmitted") or []),
                 _month_line(conn, today)]
    lines = [line for line in lines if line]
    return {"ok": True, "lines": lines, "all_clear": not lines}


# ======================================================================
#  The launch card (2026-10-20)
# ======================================================================
def _oldest(items: list[dict]) -> int:
    return max((int(i.get("age_days") or 0) for i in items), default=0)


def tally(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    """'4 quotations · 26 POs · 1 DN · 30 invoices' - made this month. All four
    always show, zeros too, so the line reads the same way every day."""
    counts = {table: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE year=? AND month=?",
                                      (today.year, today.month)).fetchone()[0])
              for table, _, _ in _TALLY}
    text = SEP.join(_plural(counts[t], one, many) for t, one, many in _TALLY)
    return {"text": text, "counts": counts}


def agenda(conn: sqlite3.Connection, feed: dict) -> dict[str, str]:
    """The ONE most pressing thing, in Waleed's order (2026-10-20): money owed,
    then client POs to deliver and invoice, then bids about to auto-lose, then
    documents never submitted. Each bucket is Needs Attention's own, so the
    numbers match the To-Do. {"key", "verb", "text", "more"}."""
    unpaid = feed.get("unpaid") or []
    if unpaid:
        nos = [str(i["doc_no"]) for i in unpaid]
        totals = _currency_totals(
            conn, f"SELECT sell_currency, SUM(sell_total) FROM invoices "
                  f"WHERE inv_no IN ({','.join('?' * len(nos))}) GROUP BY sell_currency", tuple(nos))
        owed = money(totals)
        count = _plural(len(unpaid), "invoice", "invoices")
        return {"key": "unpaid", "verb": "Collect",
                "text": f"{owed} from {count}" if owed else f"{count} not paid",
                "more": f"oldest {_days(_oldest(unpaid))}"}
    deliver = feed.get("deliver") or []
    if deliver:
        return {"key": "deliver", "verb": "Deliver & invoice",
                "text": _plural(len(deliver), "client PO", "client POs"),
                "more": f"oldest {_days(_oldest(deliver))}"}
    decide = feed.get("decide") or []
    if decide:
        left = min(int(i.get("days_left") or 0) for i in decide)
        return {"key": "decide", "verb": "Follow up",
                "text": _plural(len(decide), "quotation", "quotations"),
                "more": (f"{_days(left)} left to mark Won or Lost" if left
                         else "mark Won or Lost today")}
    unsent = feed.get("unsubmitted") or []
    if unsent:
        kinds = {i.get("kind") for i in unsent}
        one, many = (_KIND_NOUN.get(next(iter(kinds)), ("document", "documents"))
                     if len(kinds) == 1 else ("document", "documents"))
        return {"key": "unsubmitted", "verb": "Submit", "text": _plural(len(unsent), one, many),
                "more": f"oldest {_days(_oldest(unsent))}"}
    return {"key": "clear", "verb": "All caught up", "text": "", "more": "nothing outstanding"}


def card(today: date | None = None) -> dict[str, Any]:
    """Everything the launch card shows under the greeting. Each part is
    worked out on its own, so one that fails leaves the others standing; a
    part that failed is simply absent. Never writes."""
    today = today or date.today()
    out: dict[str, Any] = {"ok": True}
    uri = Path(config.DB_PATH).resolve().as_uri() + "?mode=ro"
    with closing(sqlite_policy.open_db(uri, uri=True, row_factory=False)) as conn:
        for key, work in (("tally", lambda: tally(conn, today)),
                          ("agenda", lambda: agenda(conn, database.agenda_feed()))):
            try:
                out[key] = work()
            except Exception as e:                     # noqa: BLE001 - shown as absent
                out.setdefault("errors", {})[key] = repr(e)[:200]
    return out
