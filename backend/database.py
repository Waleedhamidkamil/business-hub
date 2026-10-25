"""
SQLite layer.

Two tables:
- items: remembered item names with their last-used description/unit/company.
         Used to autocomplete when the user types an item name.
- pos:   archived POs so they can be reopened, edited, and re-saved.
         Each PO stores its number, file path, date, and JSON payload of rows.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Any

from . import config, shared_library, sqlite_policy


_SCHEMA = """
-- PO items autocomplete. Phase G.15: data_sig participates in the PK so
-- saving "Cement, RX, Bag" and "Cement, CY, Bag" creates TWO rows instead
-- of one overwriting the other.
-- Separate registry of company names. Independent of items so the user
-- can autocomplete the "لشركة" column without pulling other fields with it.
CREATE TABLE IF NOT EXISTS companies (
    name           TEXT PRIMARY KEY COLLATE NOCASE,
    use_count      INTEGER NOT NULL DEFAULT 0,
    last_used_at   TEXT
);

CREATE TABLE IF NOT EXISTS pos (
    po_no          TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    po_date        TEXT NOT NULL,    -- DD/M/YYYY as shown on the PO
    year           INTEGER NOT NULL,
    month          INTEGER NOT NULL,
    pdf_path       TEXT NOT NULL,
    notes          TEXT,
    rows_json      TEXT NOT NULL,    -- JSON list of {name,description,unit,qty,company}
    source_quote_no TEXT,             -- optional quotation reference for this PO
    reference      TEXT NOT NULL DEFAULT '' -- app-only, never printed
);

CREATE INDEX IF NOT EXISTS idx_pos_month ON pos(year, month);

-- Clients (for quotations). Seeded from config.QUOTE_CLIENTS_SEED on first run.
CREATE TABLE IF NOT EXISTS clients (
    name           TEXT PRIMARY KEY COLLATE NOCASE,
    use_count      INTEGER NOT NULL DEFAULT 0,
    last_used_at   TEXT
);

-- Saved quotations (reopen / edit / reprint).
CREATE TABLE IF NOT EXISTS quotes (
    quote_no       TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    quote_date     TEXT NOT NULL,
    quote_type     TEXT NOT NULL,    -- materials / manpower / equipment / service
    year           INTEGER NOT NULL,
    month          INTEGER NOT NULL,
    client_name    TEXT,
    rfq_no         TEXT,
    client_address TEXT,
    pdf_path       TEXT NOT NULL,
    terms_json     TEXT,             -- JSON list of term strings
    rows_json      TEXT NOT NULL,    -- JSON list of {item,description,unit,qty,rate,cost}
    -- Phase N.1 - costing/profit. cost rides per-row inside rows_json; these
    -- are the document-level costing fields. extra_costs_json = JSON list of
    -- {label, amount} (transport/labor/overhead). bid_status = open|won|lost
    -- (a quote with a linked invoice is treated as won regardless).
    extra_costs_json TEXT,
    bid_status       TEXT NOT NULL DEFAULT 'open',
    reference        TEXT NOT NULL DEFAULT '' -- app-only, never printed
);

CREATE INDEX IF NOT EXISTS idx_quotes_month ON quotes(year, month);

-- Per-type quotation item memory (autocomplete).
-- name = the "primary" column value for that type (item / equipment / role / description).
-- data_json holds the other column values to autofill on pick.
-- Phase G.15: data_sig in the PK preserves multiple variants of the same name.
-- Saved Delivery Notes.
CREATE TABLE IF NOT EXISTS dns (
    dn_no            TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    dn_date          TEXT NOT NULL,
    year             INTEGER NOT NULL,
    month            INTEGER NOT NULL,
    client_name      TEXT,
    lpo_no           TEXT,
    client_address   TEXT,
    include_prices   INTEGER NOT NULL DEFAULT 0,
    pdf_path         TEXT NOT NULL,
    rows_json        TEXT NOT NULL,  -- list of {item,description,unit,qty,rate,cost}
    -- Phase N.1 - costing/profit. source_quote_no threads this DN back to the
    -- quotation it came from (the "deal"); extra_costs_json = JSON list of
    -- {label, amount}.
    source_quote_no  TEXT,
    extra_costs_json TEXT,
    reference        TEXT NOT NULL DEFAULT '' -- app-only, never printed
);

CREATE INDEX IF NOT EXISTS idx_dns_month ON dns(year, month);

-- Delivery Note item memory (autocomplete on the Item column).
-- Phase G.15: data_sig in the PK preserves multiple variants.
-- Saved Invoices.
CREATE TABLE IF NOT EXISTS invoices (
    inv_no                TEXT PRIMARY KEY,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    inv_date              TEXT NOT NULL,
    inv_type              TEXT NOT NULL,    -- materials / equipment / manpower / service
    year                  INTEGER NOT NULL,
    month                 INTEGER NOT NULL,
    client_name           TEXT,
    lpo_no                TEXT,
    client_address        TEXT,
    payment_bank_key      TEXT,             -- INV_BANK_INFO[*].key
    withholding_included  INTEGER NOT NULL DEFAULT 1,
    -- ALWAYS USD. Every revenue figure in the app (analytics, the dashboard
    -- series, the clients page) is a plain SQL SUM over this column, so it has
    -- to stay single-currency. An invoice ISSUED in dinars keeps its native
    -- figure in sell_total/sell_currency below and stores the USD equivalent
    -- here, converted at the document's peg (doc_iqd_per_usd).
    grand_total           REAL NOT NULL DEFAULT 0,
    -- 2026-07-29 - invoices may be issued in Iraqi Dinar. sell_currency is what
    -- the client sees on the PDF; sell_total is the total in THAT currency
    -- (== grand_total when sell_currency is USD).
    sell_currency         TEXT NOT NULL DEFAULT 'USD',
    sell_total            REAL NOT NULL DEFAULT 0,
    -- 2026-08-04 - the IQD-per-USD rate this invoice was actually converted at,
    -- so grand_total above stays fixed at the rate that applied on the day
    -- instead of floating with whatever doc_iqd_per_usd would derive later.
    -- NULLable on purpose: NULL means "not recorded" (every invoice issued
    -- before the column existed) and falls back to the derived rate.
    sell_iqd_per_usd      REAL,
    pdf_path              TEXT NOT NULL,
    rows_json             TEXT NOT NULL,    -- per-type item rows (incl. per-row cost)
    -- Phase N.1 - costing/profit. source_quote_no threads this invoice back to
    -- the quotation (the "deal"); extra_costs_json = JSON list of {label, amount}.
    source_quote_no       TEXT,
    extra_costs_json      TEXT,
    -- 2026-07-24 - the delivery note this invoice was created from ("Make
    -- invoice" on a DN, or a DN inserted as an invoice line). Stored directly
    -- because the DN→INV relationship was previously only derived through
    -- po_fulfillment, so a DN with no linked PO recorded nothing at all.
    source_dn_no          TEXT,
    reference             TEXT NOT NULL DEFAULT '' -- app-only, never printed
);

CREATE INDEX IF NOT EXISTS idx_invoices_month ON invoices(year, month);

-- Per-type invoice item memory (autocomplete).
-- Phase G.15: data_sig in the PK preserves multiple variants.
-- Official Letters were RETIRED on 2026-08-27 - the type was never used
-- (zero rows in either profile). No `letters` table is created any more; an
-- existing empty one in an older DB is simply left alone.

-- Phase Q - "submitted" flag per document, kept in its own table so no
-- per-doc-table migration is needed. kind = 'po' | 'quote' | 'dn' | 'inv'.
CREATE TABLE IF NOT EXISTS submitted_docs (
    kind         TEXT NOT NULL,
    doc_no       TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    PRIMARY KEY (kind, doc_no)
);

-- Invoice payment state. Kept separate from invoices so it behaves like the
-- other document status flags and can be toggled without re-saving the PDF.
CREATE TABLE IF NOT EXISTS paid_invoices (
    inv_no  TEXT NOT NULL PRIMARY KEY,
    paid_at TEXT NOT NULL
);

-- 2026-07-24 - the CLIENT's signed/stamped copy scanned back to us, stored per
-- document. Distinct from our own digital signature (doc_meta key 'signing'):
-- this is the counter-signed paper coming back. Only DNs use it today; `kind`
-- keeps it open to invoices later. Decoupled satellite like submitted_docs.
CREATE TABLE IF NOT EXISTS signed_copies (
    kind        TEXT NOT NULL,
    doc_no      TEXT NOT NULL,
    path        TEXT NOT NULL,
    attached_at TEXT NOT NULL,
    PRIMARY KEY (kind, doc_no)
);

-- 2026-07-31 - LPOs RECEIVED FROM CLIENTS. The one document in the chain we do
-- not produce: quotation (ours) -> LPO (theirs) -> DN (ours) -> invoice (ours).
-- Deliberately NOT a sixth document type: there is no numbering (the client
-- assigns the number), no generator and no template, so three of the five
-- parallel layers would be empty. It is a RECEIVED-document store instead.
--
-- PK is (client_name, lpo_no): an LPO number belongs to the CLIENT's sequence,
-- so two clients can both send "PO-001". Keying on lpo_no alone would collide.
--
-- `total_usd` is the canonical value for every rollup, exactly as
-- invoices.grand_total is - `total` + `currency` keep what the client wrote.
CREATE TABLE IF NOT EXISTS client_lpos (
    client_name     TEXT NOT NULL COLLATE NOCASE,
    lpo_no          TEXT NOT NULL COLLATE NOCASE,
    lpo_date        TEXT,                       -- as written by the client
    year            INTEGER,
    month           INTEGER,
    total           REAL NOT NULL DEFAULT 0,    -- in `currency`
    currency        TEXT NOT NULL DEFAULT 'USD',
    total_usd       REAL NOT NULL DEFAULT 0,    -- ALWAYS USD - what rollups sum
    source_quote_no TEXT,                       -- the quotation it answers
    file_path       TEXT,                       -- the client's original, never edited
    ack_path        TEXT,                       -- our counter-signed copy, if made
    acknowledged_at TEXT,
    notes           TEXT,
    -- Which of the three kinds of PO this is (2026-09-11). One flat total
    -- describes a materials PO and lies about the other two: CSSP-OPEN-0001
    -- prints "Total Amount USD 0.77", which is the diesel UNIT price, so the
    -- old value rule retired the contract on its very first invoice.
    --   materials - a one-off order. Closes when delivered and invoiced.
    --   rental    - runs for a period at monthly_amount. Closes at period_end,
    --               or early: their PO says the term may be terminated.
    --   open      - a supply contract with no ceiling. Never auto-closes.
    kind            TEXT NOT NULL DEFAULT 'materials',
    -- What they ordered, inherited from the cited quotation (never retyped).
    -- Same shape as quotes.rows_json: [{item, description, unit, qty, rate}].
    -- NULL means the lines were never recorded, which is not the same as none.
    rows_json       TEXT,
    period_start    TEXT,                       -- rental / open
    period_end      TEXT,                       -- rental / open, blank = open ended
    monthly_amount  REAL,                       -- rental, in `currency`
    closed_at       TEXT,                       -- owned by close_lpo, not save_lpo
    close_reason    TEXT,                       -- e.g. "terminated early"
    erp_no          TEXT,                       -- printed on every HDEC PO
    project         TEXT,                       -- e.g. CSSP
    contact         TEXT,                       -- their contact point for this PO
    received_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (client_name, lpo_no)
);

CREATE INDEX IF NOT EXISTS idx_client_lpos_month ON client_lpos(year, month);

-- User-confirmed service coverage. Invoice/PDF contents stay intact.
CREATE TABLE IF NOT EXISTS lpo_invoice_periods (
    inv_no TEXT PRIMARY KEY,
    client_name TEXT NOT NULL DEFAULT '',
    lpo_no TEXT NOT NULL DEFAULT '',
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS clear_lpo_period_after_invoice_delete
AFTER DELETE ON invoices BEGIN
    DELETE FROM lpo_invoice_periods WHERE inv_no=OLD.inv_no;
END;

-- Phase X.30 - "this doc doesn't need attention": per-document, PER-CATEGORY
-- mute for the Needs-Attention feed. category = 'decide' | 'uncosted' |
-- 'unsubmitted' | 'unpaid', so silencing one concern (e.g. costing) keeps the
-- others (e.g. unpaid) nagging. Decoupled satellite like submitted_docs.
CREATE TABLE IF NOT EXISTS attention_ignores (
    kind       TEXT NOT NULL,
    doc_no     TEXT NOT NULL,
    category   TEXT NOT NULL,
    ignored_at TEXT NOT NULL,
    PRIMARY KEY (kind, doc_no, category)
);

-- Phase Y - PO fulfillment tracking. When a Delivery Note is submitted it can
-- mark PO items as "delivered"; when an Invoice is marked paid it can mark PO
-- items as "paid". Item-level (item_index into pos.rows_json) so a PO can be
-- partially delivered/paid and a single DN/INV can span several POs. status_kind
-- = 'delivered' | 'paid'; source_kind = 'dn' | 'inv' (provenance, used to revert
-- the marks if the source document is later deleted). Decoupled satellite.
CREATE TABLE IF NOT EXISTS po_fulfillment (
    po_no       TEXT    NOT NULL,
    item_index  INTEGER NOT NULL,
    status_kind TEXT    NOT NULL,
    source_kind TEXT    NOT NULL,
    source_no   TEXT    NOT NULL,
    marked_at   TEXT    NOT NULL,
    PRIMARY KEY (po_no, item_index, status_kind)
);

-- Phase R - per-document cost-entry currency (USD | IQD). Kept decoupled so no
-- per-doc-table migration is needed. Costs are STORED as entered in this
-- currency; profit math converts IQD -> USD at IQD_PER_USD. Absent row = USD.
CREATE TABLE IF NOT EXISTS doc_cost_meta (
    kind         TEXT NOT NULL,
    doc_no       TEXT NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'USD',
    -- 2026-08-05 - the IQD-per-USD rate this document's COSTS are converted
    -- at. NULL = not pinned, fall back to the derived rate. Declared here as
    -- well as in the migration: a NEW profile database never runs migrations.
    rate         REAL,
    -- 2026-09-21 - the payment ACCOUNT this document was costed against,
    -- and that account's IQD-per-USD rate at the moment it was saved. The
    -- rate is stamped so a figure cannot move when an account's rate is
    -- edited later, or when a client's receiving account changes.
    -- NULL = never stamped. Declared here as well as in the migration: a
    -- NEW profile database never runs migrations.
    account_key  TEXT,
    account_rate REAL,
    PRIMARY KEY (kind, doc_no)
);

-- Phase T - per-document table columns. The editor stores each document's own
-- column layout (custom columns + renamed headers) here, decoupled from the
-- per-type schema. Absent row = the document uses its type's columns (the old
-- behaviour), so existing documents render unchanged until re-saved.
CREATE TABLE IF NOT EXISTS doc_columns (
    kind         TEXT NOT NULL,
    doc_no       TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    PRIMARY KEY (kind, doc_no)
);

-- Generic per-document metadata. Signing already uses key='signing'; document
-- layout overrides use key='layout'. Kept key/value so PO/QUT/DN/INV can share
-- per-document settings without adding columns to four document tables.
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_type TEXT NOT NULL,
    doc_no   TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT,
    PRIMARY KEY (doc_type, doc_no, key)
);

-- Tombstone ledger for active documents that were deleted. The active row is
-- gone, so Costing/To-Do/editors ignore it, but numbering still treats the
-- number as consumed and will not reuse it.
CREATE TABLE IF NOT EXISTS deleted_docs (
    kind         TEXT NOT NULL,
    doc_no       TEXT NOT NULL,
    deleted_at   TEXT NOT NULL,
    pdf_path     TEXT,
    recycle_path TEXT,
    PRIMARY KEY (kind, doc_no)
);
CREATE INDEX IF NOT EXISTS idx_deleted_docs_kind
    ON deleted_docs(kind);

-- Permanent per-profile document sequences. Eshraqat uses these counters;
-- active rows and deletion tombstones remain a recovery floor.
CREATE TABLE IF NOT EXISTS document_counters (
    kind       TEXT PRIMARY KEY,
    last_value INTEGER NOT NULL DEFAULT 0
);

-- Phase G.14 - universal column memory.
-- Tracks every text value ever saved in any cell, segregated by doc type
-- and sub-type so memories don't bleed across sections:
--   doc_type = 'po' | 'quote' | 'dn' | 'inv'
--   sub_type = 'materials'/'equipment'/'manpower'/'service' for quote/inv,
--              '' (empty string) for po/dn
--   col_key  = the column's key (e.g. 'item', 'description', 'unit', 'role')
-- Each row counts how often the value's been used so popular values rank first.
CREATE TABLE IF NOT EXISTS column_memory (
    doc_type     TEXT NOT NULL,
    sub_type     TEXT NOT NULL DEFAULT '',
    col_key      TEXT NOT NULL,
    value        TEXT NOT NULL COLLATE NOCASE,
    use_count    INTEGER NOT NULL DEFAULT 1,
    last_used_at TEXT NOT NULL,
    PRIMARY KEY (doc_type, sub_type, col_key, value)
);
CREATE INDEX IF NOT EXISTS idx_column_memory_lookup
    ON column_memory(doc_type, sub_type, col_key, value);
"""


_ensured_data_dir: Path | None = None


def _connect() -> sqlite3.Connection:
    # Ensure the profile data dir exists, but skip the redundant mkdir syscall on
    # every open - only re-run it when the active dir actually changes (profile
    # switch, or a test repointing config.DATA_DIR). _connect is the hottest DB
    # primitive, so this trims a filesystem stat from the vast majority of calls.
    global _ensured_data_dir
    data_dir = config.DATA_DIR
    if _ensured_data_dir != data_dir:
        data_dir.mkdir(parents=True, exist_ok=True)
        _ensured_data_dir = data_dir
    return sqlite_policy.open_db(config.DB_PATH, foreign_keys=True)


@contextmanager
def _use(conn: sqlite3.Connection | None = None):
    """The caller's connection if it passed one, else a fresh one (2026-10-01).

    For READ helpers that a list calls once per row. `list_invoiceable_dns`
    asked four of them about each delivery note, each opening its own
    connection - 314 connections, plus a `PRAGMA foreign_keys` on each, to
    list 78 DNs. Passing one connection down keeps every helper's logic exactly
    as it was and removes only the opening and closing.

    With no connection passed it behaves as the old `with _connect() as conn:`
    did, so every other caller is untouched.
    """
    if conn is not None:
        yield conn
        return
    with _connect() as own:
        yield own


def _connect_shared() -> sqlite3.Connection:
    return shared_library.connect()


def best_effort_shared(operation, *args, **kwargs) -> bool:
    """Run optional autocomplete maintenance without failing a document save."""
    try:
        operation(*args, **kwargs)
        return True
    except (sqlite3.Error, OSError):
        return False


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    shared_library.init_shared_db()
    with _connect_shared() as conn:
        # One-time backfill: seed `companies` from the company names the item
        # RECORD remembers (`master_items.last_company`), so suggestions work on
        # first launch. Reads the record, not the retired `items` catalog which
        # was dropped 2026-08-20. Idempotent; guarded so a library predating
        # `master_items` still starts.
        try:
            conn.execute(
                """
                INSERT INTO companies(name, use_count, last_used_at)
                SELECT last_company, 1, datetime('now')
                FROM master_items
                WHERE TRIM(COALESCE(last_company, '')) <> ''
                ON CONFLICT(name) DO NOTHING
                """
            )
        except sqlite3.Error:
            pass
    # Presets are initial data, not a list to restore every startup.
    from . import client_master
    client_master.seed_once(getattr(config, "QUOTE_CLIENTS_SEED", []))


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #

def suggest_items(prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """
    PO item autocomplete. Each row carries `name`, `description`, `unit`,
    `last_company` and `data` - the JSON payload the fixed columns cannot hold
    (`image_refs`, so a PO item's picture comes back with it, and the costs).

    2026-08-16 - searches EVERY catalog, not just `items`, and matches on WORDS
    rather than only the start of the name. PO rows still rank first, so the
    familiar Arabic item stays the first suggestion. See `_suggest_across`.
    """
    return _suggest_across(prefix, limit, home_table="items")


def remember_item(name: str, description: str, unit: str, company: str,
                  data: dict[str, Any] | None = None) -> None:
    """
    Insert OR bump a PO item record. Phase G.15: same name + same
    (description, unit, last_company) → bump use_count. Different combo →
    new row, so multiple variants of one name coexist in autocomplete.

    `data` (2026-08-11) is the extra payload - `image_refs`, `cost_usd` - that
    the four fixed columns cannot hold. It is deliberately **outside the
    signature**: the sig still hashes only (description, unit, last_company),
    exactly as before, so remembering a picture does not fork a second
    autocomplete entry for the same item. Re-saving with a different picture
    updates the one row (last write wins), which is the same rule
    `_SIG_IGNORE` gives quotations.

    A row that already has a payload keeps it when the new save has none, so
    saving the item from a PO with the picture cell left blank does not erase
    a picture remembered earlier.
    """
    name = (name or "").strip()
    if not name:
        return
    sig = _data_sig({
        "description": description, "unit": unit, "last_company": company,
    })
    # The company travels with the item so the RECORD can answer the PO's
    # company column; it is not part of the item, but it is the one thing a PO
    # suggestion needs that the record alone could not say.
    _remember(name, description, unit,
              {**(data or {}), "last_company": company or ""},
              "items", "", sig)


# THE ITEM RECORD IS WHERE A SAVED ITEM GOES (2026-08-20). This used to mirror
# into `master_items` alongside a write to one of four catalog tables; those
# tables are gone and this is the only write. Imported lazily and swallowed
# whole: master_items imports master_data which imports this module, and a
# document save must never fail because the record could not be updated.
def _remember(name, description, unit, data, source, sub_type, sig) -> None:
    """Store what this document just saved, on the item's own record.

    `description or specs`: an equipment quotation calls the column
    "Specifications", and it is the same fact under another heading. Without
    this the record would never learn a spec block from a save - and worse,
    every equipment save would look like an item with NO description and open a
    second record beside the one that has one. The rebuild reads it the same
    way (`_rows`), so the two halves agree.
    """
    try:
        from . import master_items
        payload = dict(data or {})
        payload["_data_sig"] = sig
        if not str(description or "").strip():
            description = payload.get("specs") or description
        master_items.remember(name, description, unit, payload, source, sub_type)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# companies
# --------------------------------------------------------------------------- #

def suggest_companies(prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """
    Autocomplete: company names whose name starts with the prefix
    (case-insensitive). Most-used first. Returns [{"name": ...}, ...] so the
    frontend can treat it the same as item suggestions structurally.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return []
    with _connect_shared() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM companies
            WHERE name LIKE ? || '%'
            ORDER BY use_count DESC, last_used_at DESC
            LIMIT ?
            """,
            (prefix, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def remember_company(name: str) -> None:
    """Insert-or-update a company; bumps use_count and last_used_at."""
    name = (name or "").strip()
    if not name:
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _connect_shared() as conn:
        conn.execute(
            """
            INSERT INTO companies(name, use_count, last_used_at)
            VALUES(?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET
                use_count    = companies.use_count + 1,
                last_used_at = excluded.last_used_at
            """,
            (name, now),
        )


# --------------------------------------------------------------------------- #
# pos
# --------------------------------------------------------------------------- #

def save_po_record(
    po_no: str,
    po_date: str,
    year: int,
    month: int,
    pdf_path: str | Path,
    rows: list[dict[str, Any]],
    notes: str = "",
    source_quote_no: str | None = None,
    reference: str = "",
    item_payload=None,
) -> None:
    """Persist one PO and feed its items to the shared autocomplete.

    `item_payload(row) -> dict | None` (2026-08-11) is injected by the caller
    and returns the extra memory for that row - today a picture reference. It
    is a callback rather than data because building it means WRITING the image
    bytes to the content-addressed store, and this layer has no business
    knowing that; `api.save_po` supplies it, tests can leave it out.
    """
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(rows, ensure_ascii=False)
    reference = str(reference or "").strip()
    with _connect() as conn:
        if source_quote_no is None:
            prev = conn.execute(
                "SELECT source_quote_no FROM pos WHERE po_no = ?", (po_no,)
            ).fetchone()
            sq = (prev["source_quote_no"] if prev else None)
        else:
            sq = str(source_quote_no or "").strip() or None
        conn.execute(
            """
            INSERT INTO pos(po_no, created_at, updated_at, po_date, year, month, pdf_path, notes, rows_json, source_quote_no, reference)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(po_no) DO UPDATE SET
                updated_at      = excluded.updated_at,
                po_date         = excluded.po_date,
                year            = excluded.year,
                month           = excluded.month,
                pdf_path        = excluded.pdf_path,
                notes           = excluded.notes,
                rows_json       = excluded.rows_json,
                source_quote_no = excluded.source_quote_no,
                reference       = excluded.reference
            """,
            (po_no, now, now, po_date, year, month, str(pdf_path), notes, payload, sq, reference),
        )
    # Also remember each item, and the company independently.
    for r in rows:
        data = None
        if item_payload is not None:
            try:
                data = item_payload(r) or None
            except Exception:
                data = None          # a picture must never fail a PO save
        best_effort_shared(
            remember_item,
            r.get("name", ""), r.get("description", ""),
            r.get("unit", ""), r.get("company", ""), data,
        )
        best_effort_shared(remember_company, r.get("company", ""))


def load_po(po_no: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pos WHERE po_no = ?", (po_no,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rows"] = json.loads(d.pop("rows_json"))
    return d


def list_pos_in_db(year: int, month: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT po_no, po_date, pdf_path, created_at, updated_at, source_quote_no, reference "
            "FROM pos WHERE year=? AND month=? ORDER BY po_no",
            (year, month),
        ).fetchall()
    return [dict(r) for r in rows]


_CANDIDATE_SEARCH_SKIP = {"cost", "cost_qty", "cost_sell", "c_qty", "c_sell",
                          "c_auto", "_index", "image"}


def _candidate_search_blob(no: Any, items: list) -> str:
    """Lowercased search haystack for the link pickers: the doc number plus EVERY
    visible text value across ALL item rows (name, description, unit, company,
    …) - so the picker's search finds items/companies beyond the 3-item display
    `summary`. Skips embedded image data and internal cost keys."""
    parts = [str(no or "")]
    for it in items:
        if not isinstance(it, dict):
            continue
        for key, val in it.items():
            if key in _CANDIDATE_SEARCH_SKIP:
                continue
            text = str(val if val is not None else "").strip()
            if text and not text[:10].lower().startswith("data:image"):
                parts.append(text)
    return " ".join(parts).lower()


def list_po_candidates(limit: int = 300) -> list[dict[str, Any]]:
    """Recent POs for the fulfillment picker: number, date, item count and a
    short item/company summary. DB-backed (only editable POs can be linked)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT po_no, po_date, year, month, rows_json, source_quote_no "
            "FROM pos ORDER BY year DESC, month DESC, po_no DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            items = json.loads(r["rows_json"] or "[]")
        except (TypeError, ValueError):
            items = []
        names = [str(it.get("name") or it.get("company") or "").strip()
                 for it in items]
        names = [n for n in names if n]
        summary = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        out.append({
            "po_no": r["po_no"], "po_date": r["po_date"],
            "year": r["year"], "month": r["month"],
            "item_count": len(items), "summary": summary,
            "search": _candidate_search_blob(r["po_no"], items),
            "source_quote_no": r["source_quote_no"] or "",
        })
    return out


# --------------------------------------------------------------------------- #
# Smart fulfillment-link suggestions. Item COUNT is the dominant signal;
# quantity shape and date proximity only refine candidates within that band.
# --------------------------------------------------------------------------- #

_LINK_PRIMARY_KEYS = ("item", "equipment", "role", "name", "description")
_LINK_INTERNAL_KEYS = {"cost", "cost_qty", "cost_sell"}
_LINK_AUXILIARY_KEYS = {"qty", "quantity", "unit", "rate", "price", "total"}


def _link_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value).strip()
    return "" if text.lower().startswith("data:image") else text


def _link_primary(row: dict[str, Any] | None) -> str:
    """Best printable standard/custom identity for summaries and matching."""
    if not isinstance(row, dict):
        return ""
    for key in _LINK_PRIMARY_KEYS:
        text = _link_text(row.get(key))
        if text:
            return text
    for key, value in row.items():
        if (key in _LINK_PRIMARY_KEYS or key in _LINK_INTERNAL_KEYS or
                key in _LINK_AUXILIARY_KEYS or key in {"company", "remarks"} or
                str(key).startswith("_")):
            continue
        text = _link_text(value)
        if text:
            return text
    for key in ("company", "remarks"):
        text = _link_text(row.get(key))
        if text:
            return text
    return ""


def _link_row_has_content(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    return any(
        key not in _LINK_INTERNAL_KEYS and not str(key).startswith("_") and _link_text(value)
        for key, value in row.items()
    )


def _link_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [row for row in (rows or []) if _link_row_has_content(row)]


def _link_qtys(rows: list[dict[str, Any]] | None) -> list[float]:
    """Sorted positive quantities, independent of row order."""
    out: list[float] = []
    for row in _link_rows(rows):
        raw = row.get("qty", row.get("quantity"))
        if raw in (None, ""):
            continue
        try:
            value = float(str(raw).strip().replace(",", ""))
        except (TypeError, ValueError):
            continue
        if value > 0:
            out.append(round(value, 6))
    return sorted(out)


def _score_link_rows(source_rows: list[dict[str, Any]] | None,
                     source_date: str | None,
                     candidate_rows: list[dict[str, Any]] | None,
                     candidate_date: str | None) -> dict[str, Any]:
    """Score one possible fulfillment link without reading or writing storage."""
    source = _link_rows(source_rows)
    candidate = _link_rows(candidate_rows)
    source_count = len(source)
    candidate_count = len(candidate)
    item_diff = abs(source_count - candidate_count)
    eligible = bool(source_count and candidate_count and item_diff <= 2)
    count_score = {0: 300, 1: 200, 2: 100}.get(item_diff, 0)
    reasons = [f"{candidate_count} item{'s' if candidate_count != 1 else ''}"]

    source_qtys = _link_qtys(source)
    candidate_qtys = _link_qtys(candidate)
    qty_score = 0
    quantity_similarity = "none"
    if source_qtys and candidate_qtys:
        if source_qtys == candidate_qtys:
            quantity_similarity = "exact"
            qty_score = 40
            reasons.append("Quantities match")
        else:
            source_total = sum(source_qtys)
            candidate_total = sum(candidate_qtys)
            denom = max(source_total, candidate_total)
            closeness = 1.0 - (abs(source_total - candidate_total) / denom if denom else 1.0)
            if closeness >= 0.8:
                quantity_similarity = "close"
                qty_score = max(1, round(20 * closeness))
                reasons.append("Quantities close")

    days_apart = None
    date_score = 0
    source_day = _parse_doc_date(source_date)
    candidate_day = _parse_doc_date(candidate_date)
    if source_day and candidate_day:
        days_apart = abs((source_day - candidate_day).days)
        date_score = max(0, 9 - min(days_apart, 9))
        if days_apart <= 7:
            reasons.append("Same day" if days_apart == 0 else f"{days_apart} days apart")

    return {
        "eligible": eligible,
        "score": count_score + qty_score + date_score if eligible else 0,
        "source_item_count": source_count,
        "item_count": candidate_count,
        "item_diff": item_diff,
        "quantity_similarity": quantity_similarity,
        "days_apart": days_apart,
        "reasons": reasons,
    }


_LINK_DOC_SPECS = {
    "po": ("pos", "po_no", "po_date"),
    "dn": ("dns", "dn_no", "dn_date"),
    "inv": ("invoices", "inv_no", "inv_date"),
}


def _link_doc_row(conn: sqlite3.Connection, kind: str, doc_no: str):
    spec = _LINK_DOC_SPECS.get((kind or "").strip().lower())
    if not spec or not (doc_no or "").strip():
        return None
    table, no_col, date_col = spec
    return conn.execute(
        f"SELECT {no_col} AS no, {date_col} AS doc_date, rows_json "
        f"FROM {table} WHERE {no_col}=?",
        ((doc_no or "").strip(),),
    ).fetchone()


def _link_json_rows(raw: str | None) -> list[dict[str, Any]]:
    try:
        rows = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _link_summary(rows: list[dict[str, Any]]) -> str:
    names = [_link_primary(row) for row in _link_rows(rows)]
    names = [name for name in names if name]
    return ", ".join(names[:3]) + (" …" if len(names) > 3 else "")


def _existing_link_targets(conn: sqlite3.Connection, source_kind: str,
                           source_no: str, target_kind: str) -> set[str]:
    if target_kind == "po" and source_kind in ("dn", "inv"):
        rows = conn.execute(
            "SELECT DISTINCT po_no AS no FROM po_fulfillment "
            "WHERE source_kind=? AND source_no=?",
            (source_kind, source_no),
        ).fetchall()
    elif source_kind == "po" and target_kind in ("dn", "inv"):
        rows = conn.execute(
            "SELECT DISTINCT source_no AS no FROM po_fulfillment "
            "WHERE po_no=? AND source_kind=?",
            (source_no, target_kind),
        ).fetchall()
    else:
        rows = []
    return {row["no"] for row in rows}


def linked_source_set(source_kind: str) -> set[str]:
    """DN/invoice numbers that currently mark at least one PO item, plus those
    joined by a direct DN↔INV link (invoices.source_dn_no, 2026-07-24) so an
    invoice made from a PO-less DN still reads as linked in the panels."""
    source_kind = (source_kind or "").strip().lower()
    if source_kind not in ("dn", "inv"):
        return set()
    with _connect() as conn:
        if source_kind == "inv":
            rows = conn.execute(
                "SELECT DISTINCT f.source_no FROM po_fulfillment f "
                "LEFT JOIN invoices i ON i.inv_no=f.source_no "
                "WHERE f.source_kind='inv' AND TRIM(f.source_no) <> '' "
                "AND (i.inv_no IS NULL OR TRIM(COALESCE(i.pdf_path,'')) <> '')"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT source_no FROM po_fulfillment "
                "WHERE source_kind=? AND TRIM(source_no) <> ''",
                (source_kind,),
            ).fetchall()
        out = {row["source_no"] for row in rows}
        col = "inv_no" if source_kind == "inv" else "source_dn_no"
        out |= {r[col] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM invoices "
            f"WHERE source_dn_no IS NOT NULL AND TRIM(source_dn_no) <> '' "
            f"AND TRIM(COALESCE(pdf_path,'')) <> ''"
        ).fetchall() if r[col]}
    return out


def suggest_link_candidates(source_kind: str, source_no: str,
                            target_kind: str, limit: int = 5) -> list[dict[str, Any]]:
    """Best unlinked fulfillment candidates for one of the four picker flows."""
    source_kind = (source_kind or "").strip().lower()
    target_kind = (target_kind or "").strip().lower()
    source_no = (source_no or "").strip()
    source_spec = _LINK_DOC_SPECS.get(source_kind)
    target_spec = _LINK_DOC_SPECS.get(target_kind)
    valid_flow = ((source_kind in ("dn", "inv") and target_kind == "po") or
                  (source_kind == "po" and target_kind in ("dn", "inv")))
    if not valid_flow or not source_spec or not target_spec or not source_no:
        return []
    with _connect() as conn:
        source = _link_doc_row(conn, source_kind, source_no)
        if not source:
            return []
        table, no_col, date_col = target_spec
        candidates = conn.execute(
            f"SELECT {no_col} AS no, {date_col} AS doc_date, rows_json "
            f"FROM {table}"
        ).fetchall()
        linked = _existing_link_targets(conn, source_kind, source_no, target_kind)

    source_rows = _link_json_rows(source["rows_json"])
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["no"] in linked:
            continue
        candidate_rows = _link_json_rows(candidate["rows_json"])
        match = _score_link_rows(
            source_rows, source["doc_date"], candidate_rows, candidate["doc_date"])
        if not match["eligible"]:
            continue
        out.append({
            "kind": target_kind,
            "no": candidate["no"],
            "doc_date": candidate["doc_date"],
            "summary": _link_summary(candidate_rows),
            "search": _candidate_search_blob(candidate["no"], candidate_rows),
            **match,
        })
    out.sort(key=lambda row: (
        -int(row["score"]),
        row["days_apart"] if row["days_apart"] is not None else 10**9,
        row["no"],
    ))
    return out[:max(1, min(int(limit or 5), 20))]


def list_linked_dn_candidates(invoice_no: str, limit: int = 300) -> list[dict[str, Any]]:
    """Only DNs that have live delivered-item links to one or more POs."""
    invoice_no = (invoice_no or "").strip()
    if not invoice_no:
        return []
    with _connect() as conn:
        invoice = _link_doc_row(conn, "inv", invoice_no)
        if not invoice:
            return []
        rows = conn.execute(
            "SELECT d.dn_no AS no, d.dn_date AS doc_date, d.rows_json, "
            "COUNT(DISTINCT f.po_no) AS linked_po_count "
            "FROM dns d JOIN po_fulfillment f "
            "ON f.source_kind='dn' AND f.source_no=d.dn_no "
            "GROUP BY d.dn_no, d.dn_date, d.rows_json "
            "ORDER BY d.year DESC, d.month DESC, d.dn_no DESC LIMIT ?",
            (max(1, min(int(limit or 300), 1000)),),
        ).fetchall()
    invoice_rows = _link_json_rows(invoice["rows_json"])
    out: list[dict[str, Any]] = []
    for row in rows:
        dn_rows = _link_json_rows(row["rows_json"])
        match = _score_link_rows(
            invoice_rows, invoice["doc_date"], dn_rows, row["doc_date"])
        out.append({
            "kind": "dn",
            "no": row["no"],
            "doc_date": row["doc_date"],
            "item_count": len(_link_rows(dn_rows)),
            "summary": _link_summary(dn_rows),
            "search": _candidate_search_blob(row["no"], dn_rows),
            "linked_po_count": int(row["linked_po_count"] or 0),
            "suggested": bool(match["eligible"]),
            "score": match["score"],
            "reasons": match["reasons"],
            "days_apart": match["days_apart"],
        })
    out.sort(key=lambda row: (
        0 if row["suggested"] else 1,
        -int(row["score"]),
        row["days_apart"] if row["days_apart"] is not None else 10**9,
        row["no"],
    ))
    return out


def linked_dn_invoice_detail(dn_no: str, invoice_no: str) -> dict[str, Any]:
    """DN rows and exact PO item indexes available for invoice confirmation."""
    dn_no = (dn_no or "").strip()
    invoice_no = (invoice_no or "").strip()
    with _connect() as conn:
        dn = _link_doc_row(conn, "dn", dn_no)
        invoice = _link_doc_row(conn, "inv", invoice_no)
        if not dn or not invoice:
            return {"ok": False, "error": "document not found", "pos": []}
        delivered = conn.execute(
            "SELECT po_no, item_index FROM po_fulfillment "
            "WHERE source_kind='dn' AND source_no=? AND status_kind='delivered' "
            "ORDER BY po_no, item_index",
            (dn_no,),
        ).fetchall()
        by_po: dict[str, list[int]] = {}
        for row in delivered:
            by_po.setdefault(row["po_no"], []).append(int(row["item_index"]))
        groups: list[dict[str, Any]] = []
        for po_no in sorted(by_po):
            po = _link_doc_row(conn, "po", po_no)
            if not po:
                continue
            po_rows = _link_json_rows(po["rows_json"])
            paid_rows = conn.execute(
                "SELECT item_index, source_no FROM po_fulfillment "
                "WHERE po_no=? AND status_kind='paid'",
                (po_no,),
            ).fetchall()
            paid_by = {int(row["item_index"]): row["source_no"] for row in paid_rows}
            valid_indexes = sorted({
                index for index in by_po[po_no] if 0 <= index < len(po_rows)
            })
            items = [{
                "index": index,
                "row": po_rows[index],
                "paid_by": paid_by.get(index),
            } for index in valid_indexes]
            groups.append({
                "po_no": po_no,
                "po_date": po["doc_date"],
                "dn_item_indexes": valid_indexes,
                "proposed_indexes": [index for index in valid_indexes if index not in paid_by],
                "items": items,
            })
    return {
        "ok": True,
        "dn": {"no": dn_no, "date": dn["doc_date"],
               "rows": _link_json_rows(dn["rows_json"])},
        "invoice": {"no": invoice_no, "date": invoice["doc_date"],
                    "rows": _link_json_rows(invoice["rows_json"])},
        "pos": groups,
    }


def _doc_candidates(table: str, no_col: str, date_col: str,
                    name_keys: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    """Recent documents for a fulfillment picker: number, date, item count and a
    short item summary (first few item names). Mirrors `list_po_candidates` for
    the PO-side link buttons (pick a DN/Invoice to mark this PO's items)."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {no_col} AS no, {date_col} AS doc_date, year, month, rows_json "
            f"FROM {table} ORDER BY year DESC, month DESC, {no_col} DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            items = json.loads(r["rows_json"] or "[]")
        except (TypeError, ValueError):
            items = []
        names: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            for k in name_keys:
                v = str(it.get(k) or "").strip()
                if v:
                    names.append(v)
                    break
        summary = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        out.append({
            "no": r["no"], "doc_date": r["doc_date"],
            "year": r["year"], "month": r["month"],
            "item_count": len(items), "summary": summary,
            "search": _candidate_search_blob(r["no"], items),
        })
    return out


def list_dn_candidates(limit: int = 300) -> list[dict[str, Any]]:
    """Recent Delivery Notes for the PO-side "link a DN" picker."""
    return _doc_candidates("dns", "dn_no", "dn_date",
                           ("item", "name", "description"), limit)


def list_inv_candidates(limit: int = 300) -> list[dict[str, Any]]:
    """Recent Invoices for the PO-side "link an invoice" picker."""
    return _doc_candidates("invoices", "inv_no", "inv_date",
                           ("item", "equipment", "role", "description"), limit)


def dn_cost_summary(dn_no: str,
                    conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Sell total (Σ qty·rate) and cost total (Σ qty·cost) for a DN, currency-aware
    - feeds the invoice DN-line inheritance (2026-07-18). Uses the costing-override
    fields (`cost_qty`/`cost_sell`/`cost`) when present, else the document's own
    qty/rate. `sell_total` is 0 for a DN with no prices.

    **Every cost is read in the currency it was TYPED in (2026-08-25).** Since
    2026-08-06 each ROW carries its own `cost_ccy`, so one table can hold a
    dollar-costed line beside a dinar-costed one. This function summed the raw
    numbers - dinars added to dollars - and reported DN-TDC-032 as costing
    **856,961.40** when it really cost **$622.11**. 23 of 63 delivery notes were
    affected, and the figure feeds the invoice DN picker, so picking one of them
    dropped a six-figure "cost" onto the invoice line.

    `cost_total` stays in the DN's DOCUMENT cost currency, because that is what
    every caller reconciles from (`reconcileImportedCostCurrency`); the honest
    dollar figure rides alongside as `cost_total_usd`. `has_costs` says whether
    any line carries a cost at all - a total of 0 because nothing was ever
    entered is a different thing from a total of 0 because it is free.
    """
    dn_no = (dn_no or "").strip()
    if not dn_no:
        return {"ok": False, "error": "no dn_no"}
    with _use(conn) as c:
        row = c.execute(
            "SELECT rows_json, include_prices FROM dns WHERE dn_no=?", (dn_no,)
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"dn not found: {dn_no}"}
    try:
        items = json.loads(row["rows_json"] or "[]")
    except (TypeError, ValueError):
        items = []
    doc_ccy = get_cost_currency("dn", dn_no, conn)
    rate_peg = doc_iqd_per_usd("dn", dn_no, conn)
    sell_total = 0.0
    cost_usd = 0.0
    has_costs = False
    for it in items:
        if not isinstance(it, dict):
            continue
        qty = (_num(it.get("cost_qty")) if str(it.get("cost_qty") or "").strip()
               else _num(it.get("qty"))) or 0
        rate = (_num(it.get("cost_sell")) if str(it.get("cost_sell") or "").strip()
                else _num(it.get("rate"))) or 0
        sell_total += qty * rate
        cost_raw = _coerce_cost(it.get("cost"))
        if cost_raw == "":
            continue
        has_costs = True
        # Read in THIS row's currency, exactly as `_line_econ` does - a row
        # with no `cost_ccy` inherits the document's.
        cost_usd += qty * cost_to_usd(cost_raw, row_cost_currency(it, doc_ccy),
                                      rate_peg)
    # Back into the document's own unit for the callers that reconcile from it.
    cost_total = cost_usd * rate_peg if doc_ccy == "IQD" else cost_usd
    return {
        "ok": True, "dn_no": dn_no,
        "sell_total": round(sell_total, 2),
        "cost_total": round(cost_total, 2),
        "cost_total_usd": round(cost_usd, 2),
        "has_costs": has_costs,
        "currency": doc_ccy,
        "iqd_per_usd": rate_peg,
        "has_prices": bool(row["include_prices"]),
    }


def list_invoiceable_dns(limit: int = 300) -> list[dict[str, Any]]:
    """Priced DNs (with sell/cost totals) for the invoice item-field DN picker.
    Excludes DNs without prices or with a zero sell total - they have nothing to
    bill. Reuses `list_dn_candidates` for number/date/summary/search."""
    # 2026-07-30 - `client_signed` rides along so the picker can show which DNs
    # come with the client's returned copy. That copy is what the invoice will
    # actually attach, so it belongs in the choice, not just in a save-time toast.
    signed = signed_copy_set("dn")
    out: list[dict[str, Any]] = []
    # ONE connection for every DN's summary (2026-10-01) - it was four per DN.
    shared = _connect()
    try:
        summaries = [(c, dn_cost_summary(c["no"], shared))
                     for c in list_dn_candidates(limit)]
    finally:
        shared.close()
    for c, s in summaries:
        if not s.get("ok") or not s.get("has_prices") or s["sell_total"] <= 0:
            continue
        out.append({**c, "sell_total": s["sell_total"],
                    "cost_total": s["cost_total"],
                    "cost_total_usd": s["cost_total_usd"],
                    "has_costs": s["has_costs"], "currency": s["currency"],
                    "client_signed": c["no"] in signed})
    return out


def _match_item_snippet(rows_json: str | None, ql: str) -> str | None:
    """First item value inside a doc's rows_json that contains `ql` (lower-cased),
    trimmed to a short snippet - used to show WHY a document matched the search."""
    try:
        rows = json.loads(rows_json or "[]")
    except (TypeError, ValueError):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        for val in row.values():
            if not isinstance(val, str):
                continue
            # Ignore embedded image data (an image cell holds a base64 data URI,
            # e.g. "data:image/jpeg;base64,…"). Searching inside it produces
            # meaningless matches and would dump base64 as the snippet.
            if val.lstrip()[:10].lower() == "data:image":
                continue
            if ql in val.lower():
                s = " ".join(val.split())
                return s if len(s) <= 60 else s[:57] + "…"
    return None


def search_documents(query: str, limit: int = 24) -> list[dict[str, Any]]:
    """Dashboard search across DB-backed documents. Matches the document number,
    the client name, AND item content (each doc's rows_json). Returns a unified,
    most-recent-first list; when
    the hit came from inside the document, `snippet` names the matched item so the
    UI can show why. Scoped to the active profile DB. File-only docs with no DB
    record aren't item-searchable (they have no stored rows)."""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    like = f"%{q}%"
    out: list[dict[str, Any]] = []
    # (doc-kind, table, number column, client column | None) - all four carry
    # rows_json + pdf_path + updated_at.
    specs = [
        ("quote", "quotes",   "quote_no", "client_name"),
        ("inv",   "invoices", "inv_no",   "client_name"),
        ("dn",    "dns",      "dn_no",    "client_name"),
        ("po",    "pos",      "po_no",    None),
    ]
    with _connect() as conn:
        for doc, table, no_col, client_col in specs:
            client_expr = client_col if client_col else "''"
            sql = (
                f"SELECT {no_col} AS no, {client_expr} AS client, updated_at AS when_, "
                f"pdf_path AS path, rows_json, reference FROM {table} "
                f"WHERE {no_col} LIKE ? OR rows_json LIKE ? OR reference LIKE ?"
            )
            params: list[Any] = [like, like, like]
            if client_col:
                sql += f" OR {client_col} LIKE ?"
                params.append(like)
            for r in conn.execute(sql, params).fetchall():
                no_val = r["no"] or ""
                client_val = r["client"] or ""
                reference_val = r["reference"] or ""
                reference_hit = ql in reference_val.lower()
                if reference_hit:
                    compact = " ".join(reference_val.split())
                    snippet = compact if len(compact) <= 60 else compact[:57] + "…"
                else:
                    snippet = _match_item_snippet(r["rows_json"], ql)
                # SQL's `rows_json LIKE` also matches base64 image data. If the
                # query hit neither the number nor the client, and no real (non-
                # image) item cell matched either (snippet is None), the only hit
                # was inside embedded image data - drop this false positive.
                if (ql not in no_val.lower() and ql not in client_val.lower()
                        and not reference_hit
                        and snippet is None):
                    continue
                out.append({
                    "doc": doc, "no": no_val, "client": client_val,
                    "when": r["when_"] or "", "path": r["path"] or "",
                    "snippet": snippet,
                })
    out.sort(key=lambda x: x["when"], reverse=True)
    return out[: int(limit)]


def po_exists(po_no: str) -> bool:
    """True if a PO with this number is already on file (DB or filesystem)."""
    po_no = (po_no or "").strip()
    if not po_no:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM pos WHERE po_no = ? LIMIT 1", (po_no,)).fetchone()
    if row:
        return True
    # Fallback: scan the PO root in case a file exists without a DB record.
    from . import po_numbering
    root = config.PO_ROOT
    if root.exists():
        for f in root.rglob(f"{po_no}.pdf"):
            return True
    return False


def dn_exists(dn_no: str) -> bool:
    dn_no = (dn_no or "").strip()
    if not dn_no:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM dns WHERE dn_no = ? LIMIT 1", (dn_no,)).fetchone()
    if row:
        return True
    if config.DN_ROOT.exists():
        for f in config.DN_ROOT.rglob(f"{dn_no}.pdf"):
            return True
    return False


def save_dn_record(
    dn_no: str, dn_date: str, year: int, month: int,
    client_name: str, lpo_no: str, client_address: str,
    include_prices: bool, pdf_path: str | Path, rows: list[dict[str, Any]],
    source_quote_no: str | None = None,
    reference: str = "",
    item_payload=None,
) -> None:
    """Persist one DN and feed its items to the shared autocomplete.

    `item_payload(row) -> dict | None` (2026-08-13) is injected by the caller,
    exactly as `save_po_record` takes one. Without it this layer stored the row
    VERBATIM, so the remembered cost was a bare number in whichever currency
    the editor happened to be in - the catalog held 2002 for an item the
    document showed as 1.43. `api.save_dn` supplies a builder that records
    `cost_usd`; tests can leave it out and get the old raw payload.
    """
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(rows, ensure_ascii=False)
    reference = str(reference or "").strip()
    # Phase N.3 - preserve an existing source_quote_no link on re-save unless a
    # new one is supplied (None = leave whatever's already stored).
    with _connect() as conn:
        if source_quote_no is None:
            prev = conn.execute(
                "SELECT source_quote_no FROM dns WHERE dn_no = ?", (dn_no,)
            ).fetchone()
            sq = (prev["source_quote_no"] if prev else None)
        else:
            sq = source_quote_no or None
        conn.execute(
            """
            INSERT INTO dns(dn_no, created_at, updated_at, dn_date, year, month,
                            client_name, lpo_no, client_address, include_prices,
                            pdf_path, rows_json, source_quote_no, reference)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dn_no) DO UPDATE SET
                updated_at     = excluded.updated_at,
                dn_date        = excluded.dn_date,
                year           = excluded.year,
                month          = excluded.month,
                client_name    = excluded.client_name,
                lpo_no         = excluded.lpo_no,
                client_address = excluded.client_address,
                include_prices = excluded.include_prices,
                pdf_path       = excluded.pdf_path,
                rows_json      = excluded.rows_json,
                source_quote_no = excluded.source_quote_no,
                reference       = excluded.reference
            """,
            (dn_no, now, now, dn_date, year, month,
             client_name, lpo_no, client_address, 1 if include_prices else 0,
             str(pdf_path), payload, sq, reference),
        )
    # Remember client + each item for next-time autocomplete.
    best_effort_shared(remember_client, client_name)
    for r in rows:
        name = (r.get("item") or "").strip()
        if not name:
            continue
        data = None
        if item_payload is not None:
            try:
                data = item_payload(r)
            except Exception:
                data = None      # a memory payload must never fail a DN save
        if data is None:
            data = {k: v for k, v in r.items() if k != "item"}
        best_effort_shared(remember_dn_item, name, data)


def load_dn(dn_no: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM dns WHERE dn_no = ?", (dn_no,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rows"] = json.loads(d.pop("rows_json"))
    d["include_prices"] = bool(d.get("include_prices"))
    return d


def list_dns_in_db(year: int, month: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT dn_no, dn_date, pdf_path, created_at, updated_at, client_name, reference "
            "FROM dns WHERE year=? AND month=? ORDER BY dn_no",
            (year, month),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Item autocomplete - ONE pool, WORD matching (2026-08-16)
# --------------------------------------------------------------------------- #
#
# Every dropdown used to read only its own catalog, and only by PREFIX. Both
# halves failed the same real case: `Traffic Cone` had been delivered and
# invoiced, so it lived in `dn_items` and `inv_items` - a quotation could never
# offer it, because quotations read `quote_items`. Typing "cone" found nothing
# either, because "Traffic Cone" does not START with it.
#
# The per-type filters were narrower still: `quote_type = ?` meant an item
# quoted as equipment was invisible in a materials quotation, and `inv_type = ?`
# did the same to invoices. The catalog the Master Data screen presents as one
# thing was, to the editors, thirteen separate lists.
#
# So: search everything, match on words, and RANK rather than filter - the
# caller's own catalog and sub-type still sort first, so the familiar item is
# still the first suggestion.

_SUGGEST_TABLES = (
    ("items", ""),
    ("quote_items", "quote_type"),
    ("dn_items", ""),
    ("inv_items", "inv_type"),
)

# What may be carried over when a suggestion comes from a DIFFERENT catalog
# than the one being typed into. A quotation's `col_4` or an equipment row's
# `specs` mean nothing in an invoice, and `qty` is per-use noise; autofilling
# either would put junk in the document. Anything describing the ITEM travels.
_CROSS_CATALOG_KEYS = (
    "description", "unit", "item_no", "rate",
    "cost_usd", "cost_native", "cost_native_ccy",
    "price_native", "price_native_ccy", "money_ccy", "price_ccy",
    "image_refs", "img_query", "name_ar", "name_en",
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# Which language a NAME is in. A PO is written in Arabic, every other document
# in English, so this decides which half of a paired item a dropdown offers.
_ARABIC_NAME = re.compile(r"[؀-ۿ]")
# The document types written in Arabic. Only the PO, today.
_ARABIC_DOCUMENTS = frozenset({"items"})


def _match_score(name: str, query: str, tokens: list[str]) -> int:
    """How well `name` answers what was typed. 0 means "do not offer it".

    Deliberately generous about word ORDER and position but strict about the
    words themselves: "cone" and "safety cone" both have to reach
    "Traffic Cone", while "co" must not drag in every item containing those two
    letters ahead of a real hit.
    """
    n = (name or "").strip().lower()
    if not n:
        return 0
    q = (query or "").strip().lower()
    if n == q:
        return 1000
    if n.startswith(q):
        return 700
    if q and q in n:
        return 500
    if not tokens:
        return 0
    words = set(_WORD_RE.findall(n))
    whole = sum(1 for t in tokens if t in words)      # a token that IS a word
    partial = sum(1 for t in tokens if t not in words and t in n)
    if not (whole or partial):
        return 0
    return 100 + 150 * whole + 40 * partial


def _token_coverage(name: str, tokens: list[str]) -> int:
    """How many of the typed words appear in `name` (as a whole word or inside
    one). `_suggest_across` uses this to hide subset matches when a full one
    exists - "رف حديد" then keeps "رفوف حديد" and drops "حديد زاوية"."""
    n = (name or "").strip().lower()
    if not n or not tokens:
        return 0
    return sum(1 for t in tokens if t in n)


# WHICH COLUMN of the asking document holds an item's description.
#
# An equipment quotation calls it "Specifications"; everything else calls it
# "description". The record holds ONE description per language, and the fill
# has to land it in the column that document actually has - otherwise the spec
# text that used to come back on an equipment line simply stops appearing.
#
# A quotation or invoice of type `service` is the exception: its FIRST column is
# `description`, so the item's NAME goes there and its description has nowhere
# to go. Emitting one would overwrite the name.
def _desc_key(home_table: str, home_sub: str) -> str:
    types = {"quote_items": config.QUOTE_TYPES, "inv_items": config.INV_TYPES}
    spec = types.get(home_table, {}).get(home_sub or "")
    if not spec:
        return "description"
    keys = [c["key"] for c in spec.get("columns", [])]
    pk = spec.get("primary_key")
    if pk == "description":
        return ""                       # the name lives there; do not overwrite
    if "description" in keys:
        return "description"
    return "specs" if "specs" in keys else ""


# What the record can tell a document line. Only these ever reach a row - a
# quantity or a total is what ONE document ordered, never a property of the
# item.
_FILL_FROM_RECORD = ("cost_native", "cost_native_ccy",
                     "price_native", "price_native_ccy",
                     "image_refs", "img_query", "item_no")


def _suggest_across(query: str, limit: int, home_table: str,
                    home_sub_col: str = "", home_sub: str = "") -> list[dict[str, Any]]:
    """Rank every ITEM against `query` - ONE record, ONE suggestion.

    Reads `master_items` (2026-08-19, step 4b). It used to read all four
    document catalogs and fold the results, which is why one product could
    appear four times in a dropdown and why the same item had to be scored,
    de-duplicated and language-picked on every keystroke. There is one row per
    item now, so none of that is needed.

    The row it hands back speaks the ASKING DOCUMENT's language: a PO is
    written in Arabic and everything else in English, so a PO gets بصل and كيلو
    where an invoice gets `Onion` and `KG` - name, description and unit
    together, never a mixture.
    """
    query = (query or "").strip()
    if not query:
        return []
    tokens = [t for t in (w.lower() for w in _WORD_RE.findall(query)) if len(t) >= 2]
    needles = tokens or [query.lower()]
    cols = ("name_en", "name_ar", "description_en", "description_ar")
    like = " OR ".join(
        ["(" + " OR ".join(f"LOWER(COALESCE({c},'')) LIKE '%' || ? || '%'"
                           for c in cols) + ")"] * len(needles))
    params = tuple(n for n in needles for _ in cols)

    want_arabic = home_table in _ARABIC_DOCUMENTS
    lang = "ar" if want_arabic else "en"
    desc_key = _desc_key(home_table, home_sub)

    try:
        from . import master_items
    except Exception:
        return []
    conn = master_items._connect()
    try:
        master_items.init(conn)
        rows = conn.execute(
            f"SELECT * FROM master_items WHERE {like}", params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        # BOTH names are a way in. Writing an Arabic PO you may only know the
        # English word, and the point of pairing بصل with `Onion` is that
        # either one reaches the item - but the name actually typed leads, so
        # a hit on the other language scores a little lower.
        mine = r["name_ar"] if want_arabic else r["name_en"]
        other = r["name_en"] if want_arabic else r["name_ar"]
        score = _match_score(str(mine or ""), query, tokens)
        if other:
            score = max(score, int(_match_score(str(other), query, tokens) * 0.9))
        if not score:
            continue
        # An item that exists in only ONE language is still offered in that
        # language rather than hidden.
        name = str(mine or other or "").strip()
        if not name:
            continue
        # The description is STRICT to the document's language - no fallback.
        # An English item name carries its English description only; typing
        # "Carrot (Big size)" must never fill the Arabic وصف beside it. The
        # unit is shared (كيلو and KG are one thing), so it may fall back.
        desc = str((r["description_ar"] if want_arabic else r["description_en"]) or "")
        unit = master_items.prefer(r["unit_en"], r["unit_ar"], lang)

        data: dict[str, Any] = {}
        if desc and desc_key:
            data[desc_key] = desc
        if unit:
            data["unit"] = unit
        for k in _FILL_FROM_RECORD:
            v = r[k]
            if v in (None, ""):
                continue
            data[k] = json.loads(v) if k == "image_refs" else v
        # The other-language name travels with the item so a document save
        # teaches it back, and so the editor can show it.
        if mine and other:
            data["name_ar" if want_arabic else "name_en"] = str(mine)
            data["name_en" if want_arabic else "name_ar"] = str(other)
        # How many typed words this item covers, across BOTH its names - بصل
        # and Onion are one item, so a word in either counts.
        cover = max(_token_coverage(str(mine or ""), tokens),
                    _token_coverage(str(other or ""), tokens))
        rank = (score,
                1 if r["cost_native"] is not None else 0,
                1 if r["image_refs"] else 0,
                int(r["use_count"] or 0),
                str(r["last_used_at"] or ""))
        out.append({"name": name, "data": data, "description": desc,
                    "unit": unit, "last_company": "", "_rank": rank,
                    "_cover": cover})

    # When at least one item covers EVERY typed word, drop the ones that cover
    # only some. Two words typed ("رف حديد") and a full match present ("رفوف
    # حديد") means the material-only matches ("حديد زاوية") are noise; one word
    # typed, or no full match, changes nothing.
    if len(tokens) >= 2 and any(d["_cover"] >= len(tokens) for d in out):
        out = [d for d in out if d["_cover"] >= len(tokens)]

    out.sort(key=lambda d: d["_rank"], reverse=True)
    out = out[:limit]
    for d in out:
        d.pop("_cover", None)
    # Autofill never fills the company - it is a fact about the last purchase
    # order, not a property of the item, and repeating it onto a new PO is
    # usually wrong. `last_company` stays empty; the record still remembers it.
    for d in out:
        d.pop("_rank", None)
    return out


def _add_last_company(rows: list[dict[str, Any]]) -> None:
    """The company a PO item was last bought for.

    Not a property of the product - it is a fact about the last purchase order
    - but the PO editor fills its company column from it, so the record keeps
    it as internal memory beside `img_query`. Read from the record (2026-08-20);
    it used to be looked up in the PO catalog. One query for a whole dropdown.
    """
    names = [d["name"] for d in rows if d.get("name")]
    if not names:
        return
    marks = ",".join("?" * len(names))
    seen: dict[str, str] = {}
    conn = _connect_shared()
    try:
        for r in conn.execute(
                f"SELECT name_en, name_ar, last_company FROM master_items "
                f"WHERE COALESCE(last_company,'') <> '' "
                f"  AND (name_en COLLATE NOCASE IN ({marks})"
                f"    OR name_ar COLLATE NOCASE IN ({marks}))",
                (*names, *names)):
            for nm in (r["name_en"], r["name_ar"]):
                if nm:
                    seen[str(nm).strip().lower()] = r["last_company"]
    except sqlite3.Error:
        return
    finally:
        conn.close()
    for d in rows:
        d["last_company"] = seen.get(str(d["name"]).strip().lower(), "")


def suggest_dn_items(prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """Delivery-note autocomplete, over every catalog. DN rows rank first."""
    return _suggest_across(prefix, limit, home_table="dn_items")


# Details that STICK. The upsert below replaces `data_json` wholesale, so
# re-saving a document with the cost cell left blank used to wipe a cost this
# item had already taught us - and the user rightly expects a cost to behave
# "like price and unit", not to evaporate. These keys are carried over from the
# stored row when the incoming payload has nothing to say about them.
#
# `cost_usd` was the whole list when a cost was only ever a dollar figure.
# Costs are now stored AS TYPED in `cost_native` + `cost_native_ccy`, and that
# pair was never added here - so the protection above stopped covering the
# field costs actually live in. Saving a DN with the cost cell blank erased it
# again, silently, exactly as the comment says it must not. The price pair and
# the two display currencies have the same shape and the same problem: they are
# set from Master Data, and a document that never mentions them must not be
# able to blank them.
#
# `name_ar` / `name_en` are the item's OTHER-LANGUAGE label (2026-08-16). A PO
# is written in Arabic and the delivery note that fulfils it in English, so one
# item legitimately has two names; the second one is a label, never an
# identity, and no document payload ever carries it. Without stickiness it
# would be wiped by the first save after it was set.
_STICKY_MEMORY = ("cost_usd", "cost_native", "cost_native_ccy",
                  "price_native", "price_native_ccy",
                  "money_ccy", "price_ccy", "image_refs",
                  "name_ar", "name_en")


def _merge_sticky(conn: sqlite3.Connection, table: str, where: str,
                  params: tuple, data: dict[str, Any]) -> dict[str, Any]:
    """`data` plus any sticky detail it omits but the stored row still holds."""
    try:
        row = conn.execute(
            f'SELECT data_json FROM "{table}" WHERE {where}', params).fetchone()
    except sqlite3.Error:
        return data
    if not row:
        return data
    try:
        prev = json.loads(row["data_json"] or "{}")
    except (TypeError, ValueError):
        return data
    if not isinstance(prev, dict):
        return data
    out = dict(data)
    for k in _STICKY_MEMORY:
        if not str(out.get(k) or "").strip() and prev.get(k):
            out[k] = prev[k]
    return out


def remember_dn_item(name: str, data: dict[str, Any]) -> None:
    """Phase G.15: multi-variant - same name + different data = new row."""
    name = (name or "").strip()
    if not name:
        return
    data = data or {}
    _remember(name, data.get("description"), data.get("unit"), data,
              "dn_items", "", _data_sig(data))


def quote_exists(quote_no: str) -> bool:
    """True if a quotation with this number is already on file (DB or filesystem)."""
    quote_no = (quote_no or "").strip()
    if not quote_no:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM quotes WHERE quote_no = ? LIMIT 1", (quote_no,)).fetchone()
    if row:
        return True
    if config.QUOTE_ROOT.exists():
        for f in config.QUOTE_ROOT.rglob(f"{quote_no}.pdf"):
            return True
    return False


# --------------------------------------------------------------------------- #
# clients (for quotations)
# --------------------------------------------------------------------------- #

def suggest_clients(prefix: str, limit: int = 12) -> list[dict[str, Any]]:
    """
    Client autocomplete. With an empty prefix, returns the full list (so the
    dropdown can show everything when the field is focused). Most-used first,
    then alphabetical.
    """
    from . import client_master
    return client_master.suggestions(prefix or "", limit)


def remember_client(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    now = datetime.now().isoformat(timespec="seconds")
    with _connect_shared() as conn:
        conn.execute(
            """
            INSERT INTO clients(name, use_count, last_used_at)
            VALUES(?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET
                use_count    = clients.use_count + 1,
                last_used_at = excluded.last_used_at
            """,
            (name, now),
        )


# --------------------------------------------------------------------------- #
# quotes
# --------------------------------------------------------------------------- #

def save_quote_record(
    quote_no: str,
    quote_date: str,
    quote_type: str,
    year: int,
    month: int,
    client_name: str,
    rfq_no: str,
    client_address: str,
    pdf_path: str | Path,
    rows: list[dict[str, Any]],
    terms: list[str],
    reference: str = "",
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    rows_json = json.dumps(rows, ensure_ascii=False)
    terms_json = json.dumps(terms or [], ensure_ascii=False)
    reference = str(reference or "").strip()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO quotes(quote_no, created_at, updated_at, quote_date, quote_type,
                               year, month, client_name, rfq_no, client_address,
                               pdf_path, terms_json, rows_json, reference)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(quote_no) DO UPDATE SET
                updated_at     = excluded.updated_at,
                quote_date     = excluded.quote_date,
                quote_type     = excluded.quote_type,
                year           = excluded.year,
                month          = excluded.month,
                client_name    = excluded.client_name,
                rfq_no         = excluded.rfq_no,
                client_address = excluded.client_address,
                pdf_path       = excluded.pdf_path,
                terms_json     = excluded.terms_json,
                rows_json      = excluded.rows_json,
                reference      = excluded.reference
            """,
            (quote_no, now, now, quote_date, quote_type, year, month,
             client_name, rfq_no, client_address, str(pdf_path), terms_json, rows_json,
             reference),
        )
    # A quotation's items are remembered in `quote_items`, by `api.save_quote`.
    #
    # They used to ALSO be written here into `items` - the PO memory - under
    # the comment "items DB is shared across doc types". That was true when
    # `items` was the only memory there was (it dates from 2026-05-26;
    # `quote_items` arrived on 06-01), and it stopped being true the moment the
    # per-type tables existed. Nobody removed the old write, so every saved
    # quotation kept pouring its items into PO autocomplete.
    #
    # Measured on the live library before this was removed: 492 of the 815 PO
    # memory rows were English, and NOT ONE of them appeared in any of the 69
    # saved POs - 462 belonged to quotations, delivery notes and invoices. The
    # user types POs in Arabic and was rightly puzzled by an English dropdown.
    best_effort_shared(remember_client, client_name)


def load_quote(quote_no: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM quotes WHERE quote_no = ?", (quote_no,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rows"] = json.loads(d.pop("rows_json"))
    d["terms"] = json.loads(d.pop("terms_json") or "[]")
    return d


def list_quotes_in_db(year: int, month: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT quote_no, quote_date, quote_type, pdf_path, created_at, updated_at, client_name, reference "
            "FROM quotes WHERE year=? AND month=? ORDER BY quote_no",
            (year, month),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# quote_items (per-type autocomplete)
# --------------------------------------------------------------------------- #

def suggest_quote_items(quote_type: str, prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """
    Per-type autocomplete: return remembered primary-column values for this
    quote_type whose name starts with `prefix`. Each suggestion carries the
    `data` dict (other column values) so the picker can autofill.
    """
    return _suggest_across(prefix, limit, home_table="quote_items",
                           home_sub_col="quote_type", home_sub=quote_type or "")


def remember_quote_item(quote_type: str, name: str, data: dict[str, Any]) -> None:
    """
    Insert OR bump a quote item. Phase G.15: same (type, name, data) → bump.
    Same name with DIFFERENT data → new row, so multiple variants coexist
    in autocomplete (Cement + Portland Type I, Cement + OPC, etc.).
    """
    name = (name or "").strip()
    if not name or not quote_type:
        return
    data = data or {}
    _remember(name, data.get("description"), data.get("unit"), data,
              "quote_items", quote_type, _data_sig(data))


# --------------------------------------------------------------------------- #
# invoices
# --------------------------------------------------------------------------- #

def inv_exists(inv_no: str) -> bool:
    """True if an invoice with this number exists in the DB OR on disk."""
    inv_no = (inv_no or "").strip()
    if not inv_no:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM invoices WHERE inv_no = ? LIMIT 1", (inv_no,)
        ).fetchone()
    if row:
        return True
    # Filesystem fallback - recursive scan of INV root.
    root = config.INV_ROOT
    if root.exists():
        for p in root.rglob(f"{inv_no}.pdf"):
            return True
    return False


def save_inv_record(
    inv_no: str, inv_date: str, inv_type: str, year: int, month: int,
    client_name: str, lpo_no: str, client_address: str,
    payment_bank_key: str, withholding_included: bool,
    grand_total: float, pdf_path: str | Path, rows: list[dict[str, Any]],
    source_quote_no: str | None = None, source_dn_no: str | None = None,
    sell_currency: str = "USD", sell_total: float | None = None,
    sell_iqd_per_usd: float | None = None,
    reference: str = "",
) -> None:
    """`grand_total` is ALWAYS USD (see the invoices DDL). `sell_total` is the
    same total in `sell_currency` - the figure printed on the PDF. It defaults
    to grand_total, which is correct for the USD case.

    `sell_iqd_per_usd` is the rate this invoice was actually converted at, so
    grand_total stays fixed at the rate that applied on the day rather than
    floating with a later lookup. None means "not recorded" and leaves the
    column alone - see the 2026-08-04_inv_sell_peg migration."""
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(rows, ensure_ascii=False)
    reference = str(reference or "").strip()
    sell_currency = (sell_currency or "USD").strip().upper()
    if sell_currency not in _COST_CURRENCIES:
        sell_currency = "USD"
    sell_total = float(grand_total or 0) if sell_total is None else float(sell_total or 0)
    # Phase N.3 - keep an existing source_quote_no link on re-save unless a new
    # one is supplied (None = leave whatever's already stored). source_dn_no
    # (2026-07-24) follows the same preserve-on-re-save rule.
    with _connect() as conn:
        prev = None
        if source_quote_no is None or source_dn_no is None:
            prev = conn.execute(
                "SELECT source_quote_no, source_dn_no FROM invoices WHERE inv_no = ?",
                (inv_no,),
            ).fetchone()
        if source_quote_no is None:
            sq = (prev["source_quote_no"] if prev else None)
        else:
            sq = source_quote_no or None
        if source_dn_no is None:
            sdn = (prev["source_dn_no"] if prev else None)
        else:
            sdn = source_dn_no or None
        conn.execute(
            """
            INSERT INTO invoices(inv_no, created_at, updated_at, inv_date, inv_type,
                                 year, month, client_name, lpo_no, client_address,
                                 payment_bank_key, withholding_included, grand_total,
                                 sell_currency, sell_total, sell_iqd_per_usd,
                                 pdf_path, rows_json, source_quote_no, source_dn_no, reference)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(inv_no) DO UPDATE SET
                updated_at           = excluded.updated_at,
                inv_date             = excluded.inv_date,
                inv_type             = excluded.inv_type,
                year                 = excluded.year,
                month                = excluded.month,
                client_name          = excluded.client_name,
                lpo_no               = excluded.lpo_no,
                client_address       = excluded.client_address,
                payment_bank_key     = excluded.payment_bank_key,
                withholding_included = excluded.withholding_included,
                grand_total          = excluded.grand_total,
                sell_currency        = excluded.sell_currency,
                sell_total           = excluded.sell_total,
                -- COALESCE, not a plain overwrite: passing None means "not
                -- recorded", which must not wipe a rate an earlier save stored.
                sell_iqd_per_usd     = COALESCE(excluded.sell_iqd_per_usd,
                                                invoices.sell_iqd_per_usd),
                pdf_path             = excluded.pdf_path,
                rows_json            = excluded.rows_json,
                source_quote_no      = excluded.source_quote_no,
                source_dn_no         = excluded.source_dn_no,
                reference             = excluded.reference
            """,
            (
                inv_no, now, now, inv_date, inv_type, year, month,
                client_name or "", lpo_no or "", client_address or "",
                payment_bank_key or "", 1 if withholding_included else 0,
                float(grand_total or 0), sell_currency, sell_total,
                (float(sell_iqd_per_usd) if sell_iqd_per_usd else None),
                str(pdf_path), payload, sq, sdn, reference,
            ),
        )


def load_inv(inv_no: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM invoices WHERE inv_no = ?", (inv_no,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["rows"] = json.loads(d.pop("rows_json"))
    d["withholding_included"] = bool(d.get("withholding_included"))
    d["paid"] = is_invoice_paid(inv_no)
    return d


def list_invoices_in_db(year: int, month: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT inv_no, inv_date, inv_type, pdf_path, created_at, updated_at, client_name, "
            "grand_total, sell_currency, sell_total, reference "
            "FROM invoices WHERE year=? AND month=? ORDER BY inv_no",
            (year, month),
        ).fetchall()
    paid = paid_invoice_set()
    out = []
    for r in rows:
        d = dict(r)
        d["paid"] = d["inv_no"] in paid
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# inv_items (per-type autocomplete)
# --------------------------------------------------------------------------- #

def suggest_inv_items(inv_type: str, prefix: str, limit: int = 8) -> list[dict[str, Any]]:
    """Invoice autocomplete, over every catalog. Rows of this invoice type
    rank first, then other invoice types, then the rest."""
    return _suggest_across(prefix, limit, home_table="inv_items",
                           home_sub_col="inv_type", home_sub=inv_type or "")


def remember_inv_item(inv_type: str, name: str, data: dict[str, Any]) -> None:
    """Phase G.15: multi-variant - same (type, name, data) bumps; different data = new row."""
    name = (name or "").strip()
    if not name or not inv_type:
        return
    data = data or {}
    _remember(name, data.get("description"), data.get("unit"), data,
              "inv_items", inv_type, _data_sig(data))


# --------------------------------------------------------------------------- #
# submitted_docs (Phase Q) - mark PO / quote / DN / invoice docs as "submitted"
# --------------------------------------------------------------------------- #

_SUBMIT_KINDS = {"po", "quote", "dn", "inv"}


def set_submitted(kind: str, doc_no: str, value: bool = True) -> dict[str, Any]:
    """Mark (or unmark) a document as submitted. Decoupled from the doc tables."""
    if kind not in _SUBMIT_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        if value and kind in ("quote", "inv"):
            table, pk = (("quotes", "quote_no") if kind == "quote"
                         else ("invoices", "inv_no"))
            row = conn.execute(
                f"SELECT pdf_path FROM {table} WHERE {pk}=? LIMIT 1", (doc_no,)
            ).fetchone()
            if row is not None and not str(row["pdf_path"] or "").strip():
                return {"ok": False, "error":
                        "Complete the document pricing and save its PDF first."}
        if value:
            conn.execute(
                "INSERT INTO submitted_docs(kind, doc_no, submitted_at) VALUES(?, ?, ?) "
                "ON CONFLICT(kind, doc_no) DO UPDATE SET submitted_at = excluded.submitted_at",
                (kind, doc_no, now),
            )
        else:
            conn.execute("DELETE FROM submitted_docs WHERE kind = ? AND doc_no = ?", (kind, doc_no))
    return {"ok": True, "kind": kind, "doc_no": doc_no,
            "submitted": bool(value), "submitted_at": now if value else None}


def is_submitted(kind: str, doc_no: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM submitted_docs WHERE kind = ? AND doc_no = ? LIMIT 1",
            (kind, doc_no),
        ).fetchone()
    return row is not None


def submitted_set(kind: str) -> set[str]:
    """All submitted doc numbers for a kind (for cheap list annotation)."""
    with _connect() as conn:
        rows = conn.execute("SELECT doc_no FROM submitted_docs WHERE kind = ?", (kind,)).fetchall()
    return {r["doc_no"] for r in rows}


# --------------------------------------------------------------------------- #
# signed_copies (2026-07-24) - the CLIENT's counter-signed copy scanned back.
# NOT our own digital signature (that lives in doc_meta key 'signing').
# --------------------------------------------------------------------------- #

_SIGNED_COPY_KINDS = {"dn", "inv"}


def set_signed_copy(kind: str, doc_no: str, path: str | Path) -> dict[str, Any]:
    """Record the client's signed copy for a document. The caller copies the
    file into place FIRST - this only stores the pointer."""
    if kind not in _SIGNED_COPY_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signed_copies(kind, doc_no, path, attached_at) VALUES(?,?,?,?) "
            "ON CONFLICT(kind, doc_no) DO UPDATE SET "
            "path = excluded.path, attached_at = excluded.attached_at",
            (kind, doc_no, str(path), now),
        )
    return {"ok": True, "kind": kind, "doc_no": doc_no,
            "path": str(path), "attached_at": now}


def get_signed_copy(kind: str, doc_no: str) -> dict[str, Any] | None:
    """The stored signed copy for a document, or None. `exists` reports whether
    the file is still on disk - the row can outlive a file the user moved."""
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT path, attached_at FROM signed_copies WHERE kind = ? AND doc_no = ? LIMIT 1",
            (kind, doc_no),
        ).fetchone()
    if not row:
        return None
    p = row["path"] or ""
    return {"path": p, "attached_at": row["attached_at"],
            "filename": Path(p).name if p else "",
            "exists": bool(p) and Path(p).exists()}


def clear_signed_copy(kind: str, doc_no: str) -> dict[str, Any]:
    """Forget a document's signed copy. Does NOT delete the file - removing the
    record and deleting the user's scan are deliberately separate decisions."""
    doc_no = (doc_no or "").strip()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM signed_copies WHERE kind = ? AND doc_no = ?", (kind, doc_no))
    return {"ok": True, "kind": kind, "doc_no": doc_no, "removed": cur.rowcount}


def signed_copy_set(kind: str) -> set[str]:
    """Doc numbers of a kind that have a client-signed copy (cheap list badge)."""
    if kind not in _SIGNED_COPY_KINDS:
        return set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doc_no FROM signed_copies WHERE kind = ?", (kind,)).fetchall()
    return {r["doc_no"] for r in rows}


# --------------------------------------------------------------------------- #
# client_lpos (2026-07-31) - LPOs received FROM clients.
# The chain is: quotation (ours) -> LPO (theirs) -> DN (ours) -> invoice (ours).
# Keyed by (client_name, lpo_no); see the schema comment for why.
# --------------------------------------------------------------------------- #

def _lpo_key(client_name: str, lpo_no: str) -> tuple[str, str]:
    return (client_name or "").strip(), (lpo_no or "").strip()


# The three kinds of PO Waleed receives (2026-09-11). Read the `kind` column
# comment in the schema for what each one means; the difference matters to
# `_lpo_status`, `lpo_is_over_value` and the monthly billing reminder.
_LPO_KINDS = ("materials", "rental", "open")


def _lpo_kind(value: Any) -> str:
    """Fall back to `materials` rather than storing or acting on a kind nothing
    understands - an unreadable kind must not quietly stop an LPO closing."""
    kind = str(value or "").strip().lower()
    return kind if kind in _LPO_KINDS else "materials"


def save_lpo(client_name: str, lpo_no: str, lpo_date: str = "",
             total: float = 0.0, currency: str = "USD",
             source_quote_no: str | None = None, notes: str = "",
             kind: str = "materials", rows: list | None = None,
             period_start: str = "", period_end: str = "",
             monthly_amount: Any = None,
             erp_no: str = "", project: str = "", contact: str = "",
             year: int | None = None, month: int | None = None) -> dict[str, Any]:
    """Upsert one received LPO.

    Never touches `file_path`/`ack_path` - those are owned by the attach and
    acknowledge flows, so editing the header details can't drop the document.
    Since 2026-09-11 the same is true of `closed_at`/`close_reason`, which
    belong to `close_lpo`: editing a rental's monthly rate must not silently
    reopen a contract the client terminated.
    """
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    if not client_name:
        return {"ok": False, "error": "client is required"}
    if not lpo_no:
        return {"ok": False, "error": "LPO number is required"}
    # One client, one LPO number, one record (2026-09-19). The table is keyed
    # on (client_name, lpo_no), so re-filing CSSP-ADM-0010 as "Hyundai
    # Engineering" when it is already on file as "HDEC" forked one purchase
    # order into two rows - and now that the queries resolve the identity, the
    # picker would offer the same number twice. The spelling it was FIRST filed
    # under is kept, because nothing here rewrites a saved row's client name.
    already = load_lpo(client_name, lpo_no)
    if already:
        client_name = already.get("client_name") or client_name
        lpo_no = already.get("lpo_no") or lpo_no
    currency = (currency or "USD").strip().upper()
    if currency not in _COST_CURRENCIES:
        currency = "USD"
    try:
        total = float(total or 0)
    except (TypeError, ValueError):
        total = 0.0
    kind = _lpo_kind(kind)
    # NULL means "we never recorded what they ordered", which is a different
    # statement from "they ordered nothing" - only NULL can say the first.
    rows_json = (json.dumps(rows, ensure_ascii=False) if rows is not None
                 else already.get("rows_json") if already else None)
    # Nothing, not zero: a zero monthly rate would read as a free rental.
    try:
        monthly = float(monthly_amount) if monthly_amount not in (None, "") else None
    except (TypeError, ValueError):
        monthly = None
    # Same rule as invoices.grand_total: one canonical USD figure so every
    # rollup can SUM it, plus what the client actually wrote.
    total_usd = money_to_usd(total, currency, config.IQD_PER_USD)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO client_lpos(client_name, lpo_no, lpo_date, year, month,
                                    total, currency, total_usd, source_quote_no,
                                    notes, kind, rows_json, period_start,
                                    period_end, monthly_amount, erp_no, project,
                                    contact, received_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_name, lpo_no) DO UPDATE SET
                lpo_date        = excluded.lpo_date,
                year            = excluded.year,
                month           = excluded.month,
                total           = excluded.total,
                currency        = excluded.currency,
                total_usd       = excluded.total_usd,
                source_quote_no = excluded.source_quote_no,
                notes           = excluded.notes,
                kind            = excluded.kind,
                rows_json       = excluded.rows_json,
                period_start    = excluded.period_start,
                period_end      = excluded.period_end,
                monthly_amount  = excluded.monthly_amount,
                erp_no          = excluded.erp_no,
                project         = excluded.project,
                contact         = excluded.contact,
                updated_at      = excluded.updated_at
            """,
            (client_name, lpo_no, lpo_date or "", year, month,
             round(total, 2), currency, round(total_usd, 2),
             (source_quote_no or "").strip() or None, notes or "",
             kind, rows_json, period_start or "", period_end or "", monthly,
             erp_no or "", project or "", contact or "", now, now),
        )
    return {"ok": True, "client_name": client_name, "lpo_no": lpo_no}


def load_lpo(client_name: str, lpo_no: str) -> dict[str, Any] | None:
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    if not client_name or not lpo_no:
        return None
    where, args = _client_match("client_name", client_name)
    with _client_key_fn(_connect()) as conn:
        row = conn.execute(
            f"SELECT * FROM client_lpos WHERE {where} AND lpo_no = ? COLLATE NOCASE"
            " ORDER BY received_at DESC",
            (*args, lpo_no),
        ).fetchone()
    return _lpo_row(dict(row)) if row else None


def _lpo_row(d: dict[str, Any]) -> dict[str, Any]:
    """Annotate a stored row with what the UI needs: file presence and status."""
    try:
        decoded = json.loads(d.get("rows_json") or "null")
    except (TypeError, ValueError):
        decoded = None
    d["rows"] = decoded if isinstance(decoded, list) else None
    d["file_exists"] = bool(d.get("file_path")) and Path(d["file_path"]).exists()
    d["ack_exists"] = bool(d.get("ack_path")) and Path(d["ack_path"]).exists()
    d["acknowledged"] = bool(d["ack_exists"])
    d["filename"] = Path(d["file_path"]).name if d.get("file_path") else ""
    return d


def list_lpos(client_name: str = "", limit: int = 500) -> list[dict[str, Any]]:
    """Every received LPO, newest first, with delivered/invoiced rollups."""
    sql = "SELECT * FROM client_lpos"
    where, args = _client_match("client_name", client_name)
    args = list(args)
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY received_at DESC, lpo_no DESC LIMIT ?"
    args.append(int(limit))
    with _client_key_fn(_connect()) as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]
    # One alias map and one invoice scan for the whole list (2026-10-01) -
    # these used to be redone per LPO and per document row.
    identities = _identities()
    usage = lpo_usage_map(identities)
    from . import lpo_billing
    # Every issued invoice against an LPO, with its Submitted mark, read once.
    billing_index = lpo_billing.invoice_index() if rows else {}
    # Delivered / invoiced per line for every materials order, from ONE read of
    # the issued documents - the same index `lpo_fulfillment` uses (2026-10-03).
    docs = _fulfillment_docs(identities) if any(
        _lpo_kind(r.get("kind")) == "materials" for r in rows) else {}
    out = []
    for d in rows:
        d = _lpo_row(d)
        key = _usage_key(d["client_name"], d["lpo_no"], identities)
        d.update(usage.get(key, {"dns": [], "invoices": [], "invoiced_usd": 0.0}))
        d["remaining_usd"] = round(max(0.0, (d.get("total_usd") or 0) - d["invoiced_usd"]), 2)
        # One month ledger feeds this screen and the To-Do reminder
        # (2026-10-06): how many times each month the LPO was invoiced.
        ledger = lpo_billing.months(d, billing_index)
        d.update({k: v for k, v in ledger.items() if k not in ("ok", "months")})
        fulfil = None
        if _lpo_kind(d.get("kind")) == "materials":
            dn_rows, inv_rows = docs.get(key, ([], []))
            fulfil = _fulfillment_from_rows(d.get("rows"), dn_rows, inv_rows)
            d["items"] = _items_rollup(fulfil)
        d["status"] = _lpo_status(d)
        d["over_value"] = lpo_is_over_value(d)
        d["next_step"] = lpo_next_step(d, fulfil)
        # Done means everything invoiced AND sent - the one rule for every kind.
        if d["next_step"]["key"] == "done":
            d["status"] = "closed"
        out.append(d)
    return out


def _client_key_fn(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Teach a connection `client_key(x)` - the SAME normaliser the alias
    index is built with. `clients.py` and `insights.py` already register it;
    this is the third copy of two lines rather than a fourth idiom for
    "which rows belong to this client". Not registered in `_connect`, which
    is the hottest primitive here and should stay bare."""
    from . import client_master
    conn.create_function("client_key", 1, client_master.normalize, deterministic=True)
    return conn


def _client_match(column: str, client_name: str) -> tuple[str, list[str]]:
    """SQL matching EVERY spelling of one client, and the arguments for it.

    `HDEC` and `Hyundai Engineering and Construction Co., Ltd` are one client.
    Documents legitimately carry both - the legal name is what some of them
    PRINT - and renaming a client deliberately rewrites no saved row, so the
    stored name is history and the identity has to be resolved when it is
    read. `client_aliases` is that index; this is how a query asks it.

    Reported 2026-09-19: an LPO filed under HDEC was invisible to an invoice
    for the legal name, because these queries compared the typed string while
    `_usage_key` below already resolved the identity. The rollup had nothing
    to roll up.

    A client with no record falls back to its own spelling, and an unreadable
    client store falls back to the old `COLLATE NOCASE` behaviour - losing the
    screen is worse than losing an alias match.
    """
    name = (client_name or "").strip()
    if not name:
        return "", []
    try:
        from . import client_master
        keys = client_master.alias_keys_for(name) or [client_master.normalize(name)]
    except Exception:
        return f"{column} = ? COLLATE NOCASE", [name]
    return f"client_key({column}) IN ({','.join('?' * len(keys))})", list(keys)


def _identities() -> dict[str, str] | None:
    """The client alias map, read ONCE for a loop of `_usage_key` calls.

    Every helper below used to let `_usage_key` look the map up itself, once
    per document row - 359 shared-database connections on one visit to the hub
    (2026-10-01). `client_master.canonical` has always said "pass `identities`
    in a loop"; these callers now do. None means the store is unavailable, and
    `_usage_key` then falls back to the raw spelling exactly as before.
    """
    try:
        from . import client_master
        return client_master.canonical_map()
    except Exception:
        return None


def _usage_key(client_name: str, lpo_no: str,
               identities: dict[str, str] | None = None) -> tuple[str, str]:
    """The key an LPO and its documents meet on.

    Resolved through `client_aliases` (2026-09-11), not compared as typed: an
    LPO filed under `HDEC` and an invoice saved as `Hyundai Engineering` are one
    client, and the whole delivered / invoiced / closed ladder depended on those
    two strings happening to match.

    A broken or empty client store falls back to the raw spelling rather than
    raising - `list_lpos` runs on every visit to the screen, and losing the
    screen is worse than losing an alias match.
    """
    name = (client_name or "").strip()
    try:
        from . import client_master
        name = client_master.canonical(name, identities) or name
    except Exception:
        pass                          # never block the LPO list on the client store
    return name.strip().lower(), (lpo_no or "").strip().lower()


def lpo_usage_map(identities: dict[str, str] | None = None
                  ) -> dict[tuple[str, str], dict[str, Any]]:
    """Which DNs and invoices reference each LPO, and how much has been invoiced.

    Matched on the free-text `lpo_no` already carried by `dns` and `invoices`,
    case-insensitively and scoped to the same client - the number alone is not
    unique across clients.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if identities is None:
        identities = _identities()

    def slot(client: str, lpo: str) -> dict[str, Any]:
        k = _usage_key(client, lpo, identities)
        if k not in out:
            out[k] = {"dns": [], "invoices": [], "invoiced_usd": 0.0}
        return out[k]

    with _connect() as conn:
        for r in conn.execute(
                "SELECT dn_no, client_name, lpo_no FROM dns "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
            slot(r["client_name"], r["lpo_no"])["dns"].append(r["dn_no"])
        for r in conn.execute(
                "SELECT inv_no, client_name, lpo_no, grand_total FROM invoices "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
            s = slot(r["client_name"], r["lpo_no"])
            s["invoices"].append(r["inv_no"])
            # grand_total is USD-canonical for every invoice (2026-07-29).
            s["invoiced_usd"] = round(s["invoiced_usd"] + float(r["grand_total"] or 0), 2)
    return out


def _month_seq(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from start to end inclusive."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _term_months(start: date, end: date) -> int:
    """How many months a rental TERM runs for.

    Not the same as the number of calendar months it touches. CSSP-RENT-0002
    runs 8 June to 8 January, which touches eight calendar months but is a
    seven-month term - and their PO says so in as many words: "The contract term
    is seven months." The screen has to agree with the paper.

    A part month at the end still counts as one, because it is still billed.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end < start:
        return 0
    if end.day > start.day:
        months += 1                  # a partial final month is still a period
    return max(1, months)


def _month_label(year: int, month: int) -> str:
    """"August 2026" - for a reminder a person reads, not a sort key."""
    import calendar
    name = calendar.month_name[month] if 1 <= int(month or 0) <= 12 else "?"
    return f"{name} {year}"


def _invoice_months_index(identities: dict[str, str] | None = None
                          ) -> dict[tuple[str, str], set[tuple[int, int]]]:
    """Every LPO's invoiced months, from ONE scan of the invoices table.

    `_lpo_invoice_months` used to rescan every invoice once per LPO, and the
    hub asked for it twice per LPO (once in `list_lpos`, once more in
    `_lpo_billing_due`). Build this once and look each LPO up in it.

    Drafts are excluded: an invoice with no `pdf_path` is an editable draft, not
    a bill anyone sent, and that is the rule every other issued-document read in
    this file follows.
    """
    if identities is None:
        identities = _identities()
    index: dict[tuple[str, str], set[tuple[int, int]]] = {}
    with _connect() as conn:
        for r in conn.execute(
                "SELECT client_name, lpo_no, year, month FROM invoices "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
            if r["year"]:
                index.setdefault(_usage_key(r["client_name"], r["lpo_no"], identities),
                                 set()).add((int(r["year"]), int(r["month"] or 0)))
    return index


def _lpo_invoice_months(row: dict[str, Any],
                        index: dict[tuple[str, str], set[tuple[int, int]]] | None = None,
                        identities: dict[str, str] | None = None
                        ) -> set[tuple[int, int]]:
    """Which months already carry an invoice against this LPO.

    Pass `index` (from `_invoice_months_index`) when asking for many LPOs; on
    its own this builds one, which is one scan - never one per row.
    """
    if index is None:
        identities = identities if identities is not None else _identities()
        index = _invoice_months_index(identities)
    key = _usage_key(row.get("client_name", ""), row.get("lpo_no", ""), identities)
    return set(index.get(key, set()))


def _lpo_months_unbilled(d: dict[str, Any],
                         today: date | None = None) -> list[tuple[int, int]]:
    """Which months of a running contract have no invoice against them.

    Only rental and open contracts bill monthly; a materials PO is one order,
    delivered once. An open contract with no period recorded counts from its own
    date, which is all CSSP-OPEN-0001 gives us - it carries no period at all.

    Never reaches past today: the term may run to January, but nobody can
    invoice October yet, and a reminder nobody can act on is noise.
    """
    if _lpo_kind(d.get("kind")) not in ("rental", "open") or d.get("closed_at"):
        return []
    today = today or date.today()
    start = (_parse_doc_date(d.get("period_start") or "")
             or _parse_doc_date(d.get("lpo_date") or ""))
    if not start or start > today:
        return []
    end = min(_parse_doc_date(d.get("period_end") or "") or today, today)
    billed = set(d.get("invoice_months") or ())
    return [ym for ym in _month_seq(start, end) if ym not in billed]


def _squash(value: Any) -> str:
    """Whitespace squashed, case folded. Document text carries stray newlines -
    a real delivery note stores its unit as "EA
1650*850*800"."""
    return " ".join(str(value or "").split()).strip().lower()


def _line_key(row: dict[str, Any]) -> str:
    """How an ordered line is recognised on a delivery note or invoice.

    The line's NAME, and nothing else (corrected 2026-09-11 against the real
    documents). The first version keyed on item plus description, reasoning that
    both sides came from the same quotation - and every line of CSSP-ADM-0010
    then matched as "never ordered". The dimensions MOVE between columns:
    QUT-MT-HDEC-029 stores "1650*850*800" in `description`, while DN-HDEC-046
    and INV-MT-HDEC-043 store it inside `unit` as "EA
1650*850*800" and leave
    `description` empty.

    The name is read through **`_link_primary`**, the identity rule this file
    already uses for fulfilment links, because the column it lives in depends on
    the document type: a materials document calls it `item`, an equipment one
    calls it `equipment` (QUT-EQ-HDEC-013 does). Inventing a second rule here
    would mean equipment LPOs never matched anything.

    The specification is still used, but only to tell apart two lines that share
    a name - see `_line_spec`.
    """
    return _squash(_link_primary(row))


def _line_spec(row: dict[str, Any]) -> frozenset:
    """The words that could be carrying this line's specification.

    Description AND unit together, as a set of tokens, because the documents
    disagree about which column holds the dimensions and about what order they
    come in. Only consulted when one order has two lines of the same name.
    """
    # `specs` too: an equipment quotation calls the column "Specifications",
    # which is the same fact under another heading (see `_remember`).
    text = " ".join(str(row.get(k) or "") for k in ("description", "specs", "unit"))
    return frozenset(_squash(text).split())


def _fulfillment_from_rows(ordered: list | None,
                           delivered_docs: list[list],
                           invoiced_docs: list[list]) -> dict[str, Any]:
    """Per-line ordered / delivered / invoiced / remaining.

    Matching is by item NAME (`_line_key`). When one order carries the same name
    twice, the specification decides which of them a document row belongs to -
    two desks of different sizes are not one product, but the dimensions live in
    a different column on every document type, so the name has to lead.

    `ordered=None` means the order was never recorded, which is NOT the same as
    an empty order - so `complete` comes back None rather than True. Saying
    "0 of 0 delivered, complete" about an LPO nobody typed the lines for would
    be a confident lie.
    """
    if ordered is None:
        return {"lines": [], "extra": [], "complete": None}

    def qty_of(row: dict[str, Any]) -> float:
        try:
            return float(row.get("qty") or 0)
        except (TypeError, ValueError):
            return 0.0                    # unreadable is nothing, not a crash

    # Every ordered line, and where to find the ones sharing a name.
    lines: list[dict[str, Any]] = []
    by_name: dict[str, list[int]] = {}
    for row in ordered:
        key = _line_key(row)
        by_name.setdefault(key, []).append(len(lines))
        lines.append({
            "item": row.get("item") or "",
            "description": row.get("description") or "",
            "unit": row.get("unit") or "",
            "rate": row.get("rate") or "",
            "ordered": round(qty_of(row), 3),
            "delivered": 0.0, "invoiced": 0.0,
            "_spec": _line_spec(row),
        })

    def place(row: dict[str, Any]) -> int | None:
        """Which ordered line this document row belongs to, or None."""
        candidates = by_name.get(_line_key(row))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Two lines share this name: let the specification decide. Token sets,
        # because "EA 1650*850*800" and "1650*850*800 EA" are the same thing.
        spec = _line_spec(row)
        best, score = candidates[0], -1
        for i in candidates:
            overlap = len(spec & lines[i]["_spec"])
            if overlap > score:
                best, score = i, overlap
        return best

    extra: dict[str, dict[str, Any]] = {}

    def tally(docs: list[list], field: str) -> None:
        for rows in docs:
            for row in rows or []:
                if _line_key(row) == "" and not qty_of(row):
                    continue                      # a blank spacer row
                i = place(row)
                if i is None:
                    # Shipped but never ordered. Worth naming, not hiding -
                    # reported in the words the DOCUMENT used, not as a folded
                    # key, so it can be recognised.
                    key = _line_key(row)
                    e = extra.setdefault(key, {
                        "item": str(row.get("item") or "").strip(),
                        "description": str(row.get("description") or "").strip(),
                        "qty": 0.0,
                    })
                    e["qty"] = round(e["qty"] + qty_of(row), 3)
                    continue
                lines[i][field] = round(lines[i][field] + qty_of(row), 3)

    tally(delivered_docs, "delivered")
    tally(invoiced_docs, "invoiced")

    for line in lines:
        line.pop("_spec", None)
        # Never negative: owing minus five desks is not a thing to read. What
        # was actually shipped is still reported as `delivered`.
        line["remaining"] = round(max(0.0, line["ordered"] - line["delivered"]), 3)

    complete = bool(lines) and all(
        line["delivered"] >= line["ordered"] - 0.001
        and line["invoiced"] >= line["ordered"] - 0.001
        for line in lines)
    return {"lines": lines, "extra": list(extra.values()), "complete": complete}


def lpo_fulfillment(client_name: str, lpo_no: str) -> dict[str, Any]:
    """What is still owed on one LPO, read from the documents linked to it.

    `N/A` is skipped for the same reason `lpo_usage_map` skips it: 63 of the 64
    delivery notes in the live Abraj database carry it honestly, and counting
    those would make every LPO look delivered by documents that have nothing to
    do with it.
    """
    rec = load_lpo(client_name, lpo_no)
    if not rec:
        return {"lines": [], "extra": [], "complete": None}
    ordered = json.loads(rec["rows_json"]) if rec.get("rows_json") else None
    identities = _identities()
    key = _usage_key(rec["client_name"], rec["lpo_no"], identities)
    dn_rows, inv_rows = _fulfillment_docs(identities).get(key, ([], []))
    return _fulfillment_from_rows(ordered, dn_rows, inv_rows)


def _fulfillment_docs(identities: dict[str, str] | None = None
                      ) -> dict[tuple[str, str], tuple[list, list]]:
    """Every issued delivery note's and invoice's rows, grouped by LPO, in ONE pass.

    The list needs delivered/invoiced counts for every materials LPO, and the LPO
    screen for one; both read this, so they cannot disagree (2026-10-03). Same
    filters as before: `N/A` is not an LPO, and a draft (no PDF) was never sent.
    """
    out: dict[tuple[str, str], tuple[list, list]] = {}
    with _connect() as conn:
        for table, slot in (("dns", 0), ("invoices", 1)):
            for r in conn.execute(
                    f"SELECT client_name, lpo_no, rows_json FROM {table} "
                    "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                    "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
                key = _usage_key(r["client_name"], r["lpo_no"], identities)
                out.setdefault(key, ([], []))[slot].append(json.loads(r["rows_json"] or "[]"))
    return out


def _items_rollup(fulfil: dict[str, Any]) -> dict[str, Any]:
    """How many of an order's lines are delivered and invoiced - counts of LINES,
    because quantities in different units (EA, PACK) cannot be added up."""
    lines = fulfil.get("lines") or []
    eps = 0.001
    return {
        "lines": len(lines),
        "delivered": sum(1 for l in lines if l["delivered"] >= l["ordered"] - eps),
        "invoiced": sum(1 for l in lines if l["invoiced"] >= l["ordered"] - eps),
        "part_delivered": sum(1 for l in lines if eps < l["delivered"] < l["ordered"] - eps),
        "complete": fulfil.get("complete"),
    }


_ACTION_STEPS = frozenset({"invoice", "deliver", "invoice_period", "attach"})
# How early a contract's end is flagged - enough time to ask the client for the
# next LPO before the vehicles or generators run past their paperwork.
_ENDING_SOON_DAYS = 45


def lpo_next_step(d: dict[str, Any], fulfil: dict[str, Any] | None = None,
                  today: date | None = None) -> dict[str, Any]:
    """What to do next on one LPO: `{key, text, action[, period_start]}`.

    ONE rule for the list's "Next step" column and the LPO screen's main button.
    It reads only what `list_lpos` computed and never decides billing itself.

    Contracts follow the month ledger (`lpo_billing.months`, 2026-10-06): a
    month that ended with no invoice is something to do, oldest first; the
    current month uninvoiced is said but is not an alarm. Materials follow their
    lines: money first (something delivered and not invoiced, unless the value
    is already fully invoiced - invoice rows that miss the LPO's line names must
    not ask for a second invoice), then anything undelivered.

    For every kind, DONE means everything invoiced and every invoice SENT
    (marked submitted). Until then a fully invoiced LPO reads "N not sent yet".
    """
    import calendar
    today = today or date.today()
    kind = _lpo_kind(d.get("kind"))

    def step(key: str, text: str, **extra: Any) -> dict[str, Any]:
        return {"key": key, "text": text, "action": key in _ACTION_STEPS, **extra}

    def sent_or_done() -> dict[str, Any]:
        n = int(d.get("invoices_not_sent") or 0)
        if n:
            return step("not_sent", f"{n} invoice{'' if n == 1 else 's'} not sent yet")
        return step("done", "Done")

    if d.get("closed_at"):
        return step("closed", "Closed")

    if kind == "materials":
        total = float(d.get("total_usd") or 0)
        invoiced = float(d.get("invoiced_usd") or 0)
        value_used = total > 0 and invoiced >= total - 0.01
        lines = (fulfil or {}).get("lines") or []
        eps = 0.001
        if lines:
            if not value_used and any(l["delivered"] > l["invoiced"] + eps for l in lines):
                return step("invoice", "Create invoice")
            if any(l["delivered"] < l["ordered"] - eps for l in lines):
                return step("deliver", "Create delivery note")
            return sent_or_done()
        if not d.get("dns") and not d.get("invoices"):
            return step("deliver", "Create delivery note")
        if not value_used:
            return step("invoice", "Create invoice")
        return sent_or_done()

    due = int(d.get("months_unbilled") or 0)
    if due:
        oldest = _parse_doc_date(d.get("oldest_due_date") or "")
        text = f"Invoice {calendar.month_name[oldest.month]}" if oldest else "Invoice the missing month"
        return step("invoice_period", text, period_start=d.get("oldest_due_date") or "", missed=due)
    if d.get("billing_complete"):
        return sent_or_done()
    if d.get("this_month_due"):
        month = str(d.get("this_month_label") or "This month").split(" ")[0]
        return step("this_month", f"{month} not invoiced yet")
    end = _parse_doc_date(d.get("period_end") or "")
    if end and today <= end and (end - today).days <= _ENDING_SOON_DAYS:
        return step("ending", f"Ends {end.day} {calendar.month_abbr[end.month]}")
    if not d.get("file_exists"):
        return step("attach", "Attach their PO")
    return step("ok", "Up to date")


def _lpo_status(d: dict[str, Any]) -> str:
    """Where this LPO has reached: received -> acknowledged -> delivered ->
    invoiced, or closed by hand.

    Finishing on its own is no longer decided here (2026-10-06). Waleed: an LPO
    is done when everything it needs is invoiced and the invoices are SENT -
    for every kind. That needs the month ledger and the line fulfilment, so
    `list_lpos` sets `closed` when `lpo_next_step` says `done`. The old per-kind
    rules (a materials order closed when its value was invoiced; a rental at its
    end date) called an LPO finished while its invoices sat unsent.
    """
    if d.get("closed_at"):
        return "closed"                 # closed by hand, whatever the numbers say
    if d.get("invoices"):
        return "invoiced"
    if d.get("dns"):
        return "delivered"
    if d.get("acknowledged"):
        return "acknowledged"
    return "received"


def lpo_is_over_value(d: dict[str, Any]) -> bool:
    """Has more been invoiced than this LPO is worth?

    Only a materials PO carrying a real total has a ceiling. An open supply
    contract has none, and a total of 0 means "not recorded", not "zero" - the
    warning has to stay silent for both, or it cries wolf on every diesel
    invoice.
    """
    if _lpo_kind(d.get("kind")) != "materials":
        return False
    total = float(d.get("total_usd") or 0)
    if total <= 0:
        return False
    return float(d.get("invoiced_usd") or 0) > total + 0.01


def set_lpo_file(client_name: str, lpo_no: str, path: str | Path | None,
                 which: str = "file") -> dict[str, Any]:
    """Point an LPO at its stored original (`file`) or counter-signed copy (`ack`)."""
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    column = {"file": "file_path", "ack": "ack_path"}.get(which)
    if not column:
        return {"ok": False, "error": f"bad target: {which}"}
    now = datetime.now().isoformat(timespec="seconds")
    stamp = ", acknowledged_at = ?" if which == "ack" else ""
    args: list[Any] = [str(path) if path else None]
    if which == "ack":
        args.append(now if path else None)
    args += [now, client_name, lpo_no]
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE client_lpos SET {column} = ?{stamp}, updated_at = ? "
            f"WHERE client_name = ? AND lpo_no = ?", args)
        if not cur.rowcount:
            return {"ok": False, "error": f"LPO not found: {client_name} / {lpo_no}"}
    return {"ok": True}


def close_lpo(client_name: str, lpo_no: str, closed: bool = True,
              reason: str = "") -> dict[str, Any]:
    """Retire an LPO by hand, or put it back.

    Owned separately from `save_lpo` on purpose, the same way `file_path` and
    `ack_path` are: editing a rental's monthly rate must not silently reopen a
    contract the client terminated. CSSP-RENT-0002 says in so many words that
    the term "may be terminated earlier if necessary", and no rule the app can
    compute will ever know that happened.
    """
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    now = datetime.now().isoformat(timespec="seconds")
    # Reopening clears the reason too - a running contract must not carry an
    # explanation for an ending that was undone.
    reason_value = ((reason or "").strip() or None) if closed else None
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE client_lpos SET closed_at = ?, close_reason = ?, updated_at = ? "
            "WHERE client_name = ? AND lpo_no = ?",
            (now if closed else None, reason_value, now, client_name, lpo_no))
        if not cur.rowcount:
            return {"ok": False, "error": f"no LPO {lpo_no} on file for {client_name}"}
    return {"ok": True, "closed": bool(closed)}


def delete_lpo(client_name: str, lpo_no: str) -> dict[str, Any]:
    """Forget an LPO record. Does NOT delete the client's file - removing the
    record and deleting their document are separate decisions (same rule as
    `clear_signed_copy`)."""
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM client_lpos WHERE client_name = ? AND lpo_no = ?",
            (client_name, lpo_no))
    return {"ok": bool(cur.rowcount)}


def suggest_lpos(client_name: str = "", prefix: str = "", limit: int = 12) -> list[dict]:
    """LPO numbers for the DN/Invoice picker, newest first."""
    sql = "SELECT client_name, lpo_no, lpo_date, total, currency, source_quote_no FROM client_lpos"
    where, args = [], []
    client_sql, client_args = _client_match("client_name", client_name)
    if client_sql:
        where.append(client_sql); args.extend(client_args)
    if (prefix or "").strip():
        where.append("lpo_no LIKE ?"); args.append(f"%{prefix.strip()}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY received_at DESC LIMIT ?"
    args.append(int(limit))
    with _client_key_fn(_connect()) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def dn_paid_set() -> set[str]:
    """DNs whose work has actually been PAID for (2026-07-24).

    Once the money is in, chasing the client's signed copy is pointless, so the
    "Awaiting client signature" pill is suppressed for these. Payment reaches a
    DN two ways, and both count:
      A. an invoice created FROM the DN (`invoices.source_dn_no`) is marked paid;
      B. the DN delivered PO items that a paid invoice later billed - note the
         po_fulfillment row for an invoice is stored as status_kind='paid' but
         only means "invoiced", so real payment still comes from `paid_invoices`.
    """
    out: set[str] = set()
    with _connect() as conn:
        for r in conn.execute(
            "SELECT DISTINCT i.source_dn_no AS dn FROM invoices i "
            "JOIN paid_invoices p ON p.inv_no = i.inv_no "
            "WHERE i.source_dn_no IS NOT NULL AND TRIM(i.source_dn_no) <> '' "
            "AND TRIM(COALESCE(i.pdf_path,'')) <> ''"
        ):
            if r["dn"]:
                out.add(r["dn"])
        for r in conn.execute(
            "SELECT DISTINCT d.source_no AS dn "
            "FROM po_fulfillment d "
            "JOIN po_fulfillment inv "
            "  ON inv.po_no = d.po_no AND inv.item_index = d.item_index "
            " AND inv.status_kind = 'paid' AND inv.source_kind = 'inv' "
            "JOIN paid_invoices p ON p.inv_no = inv.source_no "
            "JOIN invoices ii ON ii.inv_no = inv.source_no "
            "WHERE d.source_kind = 'dn' AND d.status_kind = 'delivered'"
            " AND TRIM(COALESCE(ii.pdf_path,'')) <> ''"
        ):
            if r["dn"]:
                out.add(r["dn"])
    return out


# --------------------------------------------------------------------------- #
# paid_invoices - invoice-only paid marker used by the invoice right panel.
# --------------------------------------------------------------------------- #

def set_invoice_paid(inv_no: str, value: bool = True) -> dict[str, Any]:
    """Mark (or unmark) an invoice as paid. Decoupled from the invoice row."""
    inv_no = (inv_no or "").strip()
    if not inv_no:
        return {"ok": False, "error": "no inv_no"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            "SELECT pdf_path FROM invoices WHERE inv_no=? LIMIT 1", (inv_no,)
        ).fetchone()
        if value and row is not None and not str(row["pdf_path"] or "").strip():
            return {"ok": False, "error":
                    "Complete the invoice pricing and save its PDF first."}
        if value:
            conn.execute(
                "INSERT INTO paid_invoices(inv_no, paid_at) VALUES(?, ?) "
                "ON CONFLICT(inv_no) DO UPDATE SET paid_at = excluded.paid_at",
                (inv_no, now),
            )
        else:
            conn.execute("DELETE FROM paid_invoices WHERE inv_no = ?", (inv_no,))
    return {"ok": True, "inv_no": inv_no, "paid": bool(value),
            "paid_at": now if value else None}


def is_invoice_paid(inv_no: str) -> bool:
    inv_no = (inv_no or "").strip()
    if not inv_no:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM paid_invoices WHERE inv_no = ? LIMIT 1",
            (inv_no,),
        ).fetchone()
    return row is not None


def paid_invoice_set() -> set[str]:
    """All paid invoice numbers for cheap list annotation."""
    with _connect() as conn:
        rows = conn.execute("SELECT inv_no FROM paid_invoices").fetchall()
    return {r["inv_no"] for r in rows}


# --------------------------------------------------------------------------- #
# attention_ignores (Phase X.30) - per-doc, per-category Needs-Attention mute.
# --------------------------------------------------------------------------- #

_ATTENTION_CATEGORIES = {"decide", "uncosted", "unsubmitted", "unpaid", "deliver",
                         "billing",
                         # 2026-07-24 - "this DN will never get a signed copy back";
                         # silences the red "Awaiting client signature" pill.
                         "client_sign"}


def set_attention_ignore(kind: str, doc_no: str, category: str,
                         value: bool = True) -> dict[str, Any]:
    """Ignore (or restore) one document for ONE Needs-Attention category."""
    kind = (kind or "").strip().lower()
    doc_no = (doc_no or "").strip()
    category = (category or "").strip().lower()
    # "lpo" joined the list on 2026-09-11 with the `billing` bucket. A client
    # LPO is not one of our document types, but it IS something the To-Do
    # nags about - and that bucket fires every month, forever, so being
    # unmutable would make it the one reminder nobody can ever silence.
    if not doc_no or kind not in ("po", "quote", "dn", "inv", "lpo"):
        return {"ok": False, "error": f"bad kind/doc_no: {kind}/{doc_no}"}
    if category not in _ATTENTION_CATEGORIES:
        return {"ok": False, "error": f"bad category: {category}"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        if value:
            conn.execute(
                "INSERT INTO attention_ignores(kind, doc_no, category, ignored_at) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(kind, doc_no, category) DO UPDATE SET ignored_at = excluded.ignored_at",
                (kind, doc_no, category, now),
            )
        else:
            conn.execute(
                "DELETE FROM attention_ignores WHERE kind=? AND doc_no=? AND category=?",
                (kind, doc_no, category),
            )
    return {"ok": True, "kind": kind, "doc_no": doc_no,
            "category": category, "ignored": bool(value)}


def is_attention_ignored(kind: str, doc_no: str, category: str) -> bool:
    """Whether ONE document is ignored for ONE category (single-row check, so a
    dialog doesn't have to pull the whole ignore map)."""
    kind = (kind or "").strip().lower()
    doc_no = (doc_no or "").strip()
    category = (category or "").strip().lower()
    if not doc_no or category not in _ATTENTION_CATEGORIES:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM attention_ignores "
            "WHERE kind = ? AND doc_no = ? AND category = ? LIMIT 1",
            (kind, doc_no, category),
        ).fetchone()
    return row is not None


def attention_ignore_map() -> dict[str, set[tuple[str, str]]]:
    """{category: {(kind, doc_no), …}} for cheap feed filtering."""
    out: dict[str, set[tuple[str, str]]] = {c: set() for c in _ATTENTION_CATEGORIES}
    with _connect() as conn:
        for r in conn.execute("SELECT kind, doc_no, category FROM attention_ignores"):
            out.setdefault(r["category"], set()).add((r["kind"], r["doc_no"]))
    return out


# --------------------------------------------------------------------------- #
# po_fulfillment (Phase Y) - PO items linked to a DN or invoice.
# Item-level, so a PO can be partially delivered/invoiced and one DN/INV can span
# several POs. The stored invoice status remains "paid" for compatibility, but it
# represents the invoice link. Actual PO payment is derived from paid_invoices.
# --------------------------------------------------------------------------- #

_PO_FULFILL_SOURCES = {"dn": "delivered", "inv": "paid"}


def _fulfill_state(marked: int, total: int) -> str:
    """none / partial / full for `marked` of `total` items."""
    if marked <= 0:
        return "none"
    if total and marked >= total:
        return "full"
    return "partial"


def set_po_fulfillment(po_no: str, item_indexes, source_kind: str,
                       source_no: str, value: bool = True) -> dict[str, Any]:
    """Link (or unlink) PO items to a source DN/invoice.

    item_indexes - iterable of 0-based indexes into the PO's rows_json.
    source_kind  - 'dn' (=> delivered) or 'inv' (=> invoiced).

    Invoice rows retain the legacy stored status_kind ``paid``. They count as
    actually paid only while source_no is present in paid_invoices.
    """
    po_no = (po_no or "").strip()
    source_kind = (source_kind or "").strip().lower()
    source_no = (source_no or "").strip()
    status_kind = _PO_FULFILL_SOURCES.get(source_kind)
    if not po_no:
        return {"ok": False, "error": "no po_no"}
    if not status_kind:
        return {"ok": False, "error": f"bad source_kind: {source_kind}"}
    try:
        idxs = sorted({int(i) for i in (item_indexes or [])})
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad item_indexes"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        if value and source_kind == "inv":
            invoice = conn.execute(
                "SELECT pdf_path FROM invoices WHERE inv_no = ? LIMIT 1",
                (source_no,),
            ).fetchone()
            if invoice and not str(invoice["pdf_path"] or "").strip():
                return {
                    "ok": False,
                    "error": "complete invoice pricing before linking it to a PO",
                }
        if value:
            for idx in idxs:
                conn.execute(
                    "INSERT INTO po_fulfillment(po_no, item_index, status_kind, "
                    "source_kind, source_no, marked_at) VALUES(?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(po_no, item_index, status_kind) DO UPDATE SET "
                    "source_kind = excluded.source_kind, "
                    "source_no   = excluded.source_no, "
                    "marked_at   = excluded.marked_at",
                    (po_no, idx, status_kind, source_kind, source_no, now),
                )
        else:
            for idx in idxs:
                conn.execute(
                    "DELETE FROM po_fulfillment WHERE po_no=? AND item_index=? "
                    "AND status_kind=?",
                    (po_no, idx, status_kind),
                )
    return {"ok": True, "po_no": po_no, "status_kind": status_kind,
            "source_kind": source_kind, "source_no": source_no,
            "item_indexes": idxs, "value": bool(value)}


def link_invoice_from_dn(dn_no: str, invoice_no: str) -> dict[str, Any]:
    """Copy a DN's valid delivered PO indexes to one saved invoice.

    Existing marks from another invoice are reported and preserved. The
    relationship remains derived through po_fulfillment; no DN/INV link row is
    stored separately.
    """
    dn_no = (dn_no or "").strip()
    invoice_no = (invoice_no or "").strip()
    if not dn_no or not invoice_no:
        return {"ok": False, "error": "missing document number"}

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        if not conn.execute("SELECT 1 FROM dns WHERE dn_no=?", (dn_no,)).fetchone():
            return {"ok": False, "error": f"delivery note not found: {dn_no}"}
        if not conn.execute(
                "SELECT 1 FROM invoices WHERE inv_no=?", (invoice_no,)).fetchone():
            return {"ok": False, "error": f"invoice not found: {invoice_no}"}

        delivered = conn.execute(
            "SELECT f.po_no, f.item_index, p.rows_json "
            "FROM po_fulfillment f JOIN pos p ON p.po_no=f.po_no "
            "WHERE f.source_kind='dn' AND f.source_no=? "
            "AND f.status_kind='delivered' ORDER BY f.po_no, f.item_index",
            (dn_no,),
        ).fetchall()

        po_sizes: dict[str, int] = {}
        valid_rows: list[tuple[str, int]] = []
        invalid_item_count = 0
        for row in delivered:
            po_no = row["po_no"]
            if po_no not in po_sizes:
                po_sizes[po_no] = len(_link_json_rows(row["rows_json"]))
            item_index = int(row["item_index"])
            if 0 <= item_index < po_sizes[po_no]:
                valid_rows.append((po_no, item_index))
            else:
                invalid_item_count += 1

        linked = 0
        already_linked = 0
        conflicts: list[dict[str, Any]] = []
        for po_no, item_index in valid_rows:
            existing = conn.execute(
                "SELECT source_no FROM po_fulfillment "
                "WHERE po_no=? AND item_index=? AND status_kind='paid'",
                (po_no, item_index),
            ).fetchone()
            if existing:
                if existing["source_no"] == invoice_no:
                    already_linked += 1
                else:
                    conflicts.append({
                        "po_no": po_no,
                        "item_index": item_index,
                        "invoice_no": existing["source_no"],
                    })
                continue
            conn.execute(
                "INSERT INTO po_fulfillment(po_no,item_index,status_kind,"
                "source_kind,source_no,marked_at) VALUES(?,?,'paid','inv',?,?)",
                (po_no, item_index, invoice_no, now),
            )
            linked += 1

    return {
        "ok": True,
        "dn_no": dn_no,
        "invoice_no": invoice_no,
        "linked_item_count": linked,
        "already_linked_item_count": already_linked,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "po_count": len({po_no for po_no, _ in valid_rows}),
        "invalid_item_count": invalid_item_count,
        "no_po_links": not valid_rows,
    }


def invoices_referencing_dn(dn_no: str) -> list[str]:
    """Invoice numbers whose PDF should carry this delivery note's pages.

    Two links reach the same place (see `api._inv_attachment_dn_refs`):
    a `dn_ref` line the DN picker wrote into `rows_json`, or the direct
    `source_dn_no` the "Make invoice" workflow writes. Both are checked here so
    the caller never has to know which one produced the pairing.

    Invoices with no PDF yet (an incomplete-price draft) are left out - there is
    no file to refresh, and re-rendering one would raise.
    """
    dn_no = (dn_no or "").strip()
    if not dn_no:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT inv_no, source_dn_no, rows_json FROM invoices "
            "WHERE TRIM(COALESCE(pdf_path,'')) <> '' "
            "  AND (TRIM(COALESCE(source_dn_no,'')) = ? OR rows_json LIKE ?) "
            "ORDER BY inv_no",
            (dn_no, f"%{dn_no}%"),
        ).fetchall()
    out: list[str] = []
    for r in rows:
        if str(r["source_dn_no"] or "").strip() == dn_no:
            out.append(r["inv_no"])
            continue
        # The LIKE above is only a cheap pre-filter - the number could appear in
        # any free-text field. Confirm it is a real `dn_ref` before rewriting a
        # document's PDF.
        try:
            items = json.loads(r["rows_json"] or "[]")
        except Exception:
            continue
        if any(str((i or {}).get("dn_ref") or "").strip() == dn_no
               for i in (items or []) if isinstance(i, dict)):
            out.append(r["inv_no"])
    return out


def link_invoice_dn_refs(invoice_no: str, items: list) -> dict[str, Any]:
    """For each `dn_ref` line on an invoice, mark that DN's delivered PO items
    invoiced (via `link_invoice_from_dn`) - closes the 'Deliver & Invoice' loop
    when a DN is inserted as an invoice line (2026-07-18). Idempotent (conflict-
    guarded); blanks/dupes skipped. The invoice row must already exist."""
    invoice_no = (invoice_no or "").strip()
    refs: list[str] = []
    for r in (items or []):
        ref = str((r or {}).get("dn_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    linked = 0
    for ref in refs:
        try:
            res = link_invoice_from_dn(ref, invoice_no)
            if res.get("ok"):
                linked += int(res.get("linked_item_count") or 0)
        except Exception:
            pass
    return {"ok": True, "invoice_no": invoice_no, "dn_refs": refs,
            "linked_item_count": linked}


def po_fulfillment_detail(po_no: str) -> dict[str, Any]:
    """Per-item delivered/invoiced/paid state for one PO, keyed by item index.

    Invoice links always appear under ``invoiced`` and appear under ``paid`` only
    when their source invoice is explicitly marked paid.
    (JSON dict keys arrive as strings on the JS side.)
    """
    po_no = (po_no or "").strip()
    delivered: dict[int, dict] = {}
    invoiced: dict[int, dict] = {}
    paid: dict[int, dict] = {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT f.item_index, f.status_kind, f.source_kind, f.source_no "
            "FROM po_fulfillment f LEFT JOIN invoices i "
            "ON f.source_kind='inv' AND i.inv_no=f.source_no "
            "WHERE f.po_no = ? AND (f.source_kind <> 'inv' "
            "OR i.inv_no IS NULL OR TRIM(COALESCE(i.pdf_path,'')) <> '')",
            (po_no,),
        ).fetchall()
        paid_invoices = {
            row["inv_no"] for row in conn.execute(
                "SELECT p.inv_no FROM paid_invoices p JOIN invoices i ON i.inv_no=p.inv_no "
                "WHERE TRIM(COALESCE(i.pdf_path,'')) <> ''")
        }
    for r in rows:
        link = {"source_kind": r["source_kind"], "source_no": r["source_no"]}
        if r["status_kind"] == "delivered":
            delivered[r["item_index"]] = link
        elif r["source_kind"] == "inv":
            invoiced[r["item_index"]] = link
            if r["source_no"] in paid_invoices:
                paid[r["item_index"]] = link
    return {
        "po_no": po_no,
        "delivered": delivered,
        "invoiced": invoiced,
        "paid": paid,
    }


def po_fulfillment_map() -> dict[str, dict[str, str]]:
    """PO delivered/invoiced/paid states for cheap list pills.

    Only POs that have at least one fulfillment mark appear in the map.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT f.po_no, f.item_index, f.status_kind, f.source_kind, f.source_no "
            "FROM po_fulfillment f LEFT JOIN invoices i "
            "ON f.source_kind='inv' AND i.inv_no=f.source_no "
            "WHERE f.source_kind <> 'inv' OR i.inv_no IS NULL "
            "OR TRIM(COALESCE(i.pdf_path,'')) <> ''"
        ).fetchall()
        paid_invoices = {
            row["inv_no"] for row in conn.execute(
                "SELECT p.inv_no FROM paid_invoices p JOIN invoices i ON i.inv_no=p.inv_no "
                "WHERE TRIM(COALESCE(i.pdf_path,'')) <> ''")
        }
        agg: dict[str, dict[str, set]] = {}
        for r in rows:
            d = agg.setdefault(
                r["po_no"], {"delivered": set(), "invoiced": set(), "paid": set()})
            if r["status_kind"] == "delivered":
                d["delivered"].add(r["item_index"])
            elif r["source_kind"] == "inv":
                d["invoiced"].add(r["item_index"])
                if r["source_no"] in paid_invoices:
                    d["paid"].add(r["item_index"])
        if not agg:
            return {}
        marks = ",".join("?" * len(agg))
        totals = {
            row["po_no"]: len(json.loads(row["rows_json"] or "[]"))
            for row in conn.execute(
                f"SELECT po_no, rows_json FROM pos WHERE po_no IN ({marks})",
                tuple(agg),
            ).fetchall()
        }
    out: dict[str, dict[str, str]] = {}
    for po_no, sets in agg.items():
        total = totals.get(po_no, 0)
        out[po_no] = {
            "delivered": _fulfill_state(len(sets.get("delivered", ())), total),
            "invoiced": _fulfill_state(len(sets.get("invoiced", ())), total),
            "paid": _fulfill_state(len(sets.get("paid", ())), total),
        }
    return out


def po_links_for_doc(source_kind: str, source_no: str) -> list[str]:
    """PO numbers a given DN/INV has marked (for re-opening the picker)."""
    source_kind = (source_kind or "").strip().lower()
    source_no = (source_no or "").strip()
    if source_kind not in _PO_FULFILL_SOURCES or not source_no:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT po_no FROM po_fulfillment "
            "WHERE source_kind = ? AND source_no = ? ORDER BY po_no",
            (source_kind, source_no),
        ).fetchall()
    return [r["po_no"] for r in rows]


def clear_po_fulfillment_link(po_no: str, source_kind: str,
                              source_no: str) -> dict[str, Any]:
    """Remove exactly one PO-to-DN/invoice relationship across all its items."""
    po_no = (po_no or "").strip()
    source_kind = (source_kind or "").strip().lower()
    source_no = (source_no or "").strip()
    if not po_no:
        return {"ok": False, "error": "no po_no", "removed": 0}
    if source_kind not in _PO_FULFILL_SOURCES:
        return {"ok": False, "error": f"bad source_kind: {source_kind}", "removed": 0}
    if not source_no:
        return {"ok": False, "error": "no source_no", "removed": 0}
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM po_fulfillment WHERE po_no=? AND source_kind=? AND source_no=?",
            (po_no, source_kind, source_no),
        )
        removed = max(0, int(cursor.rowcount or 0))
    return {
        "ok": True,
        "po_no": po_no,
        "source_kind": source_kind,
        "source_no": source_no,
        "removed": removed,
    }


def _workflow_node(conn: sqlite3.Connection, kind: str, doc_no: str,
                   current_kind: str, current_no: str) -> dict[str, Any] | None:
    """Compact, display-ready node for the delivery/payment workflow modal."""
    spec = {
        "po": ("pos", "po_no", "po_date", "''"),
        "dn": ("dns", "dn_no", "dn_date", "client_name"),
        "inv": ("invoices", "inv_no", "inv_date", "client_name"),
    }.get(kind)
    if not spec:
        return None
    table, no_col, date_col, client_col = spec
    row = conn.execute(
        f"SELECT {no_col} AS no, {date_col} AS doc_date, "
        f"{client_col} AS client, rows_json FROM {table} WHERE {no_col}=?",
        (doc_no,),
    ).fetchone()
    if not row:
        return None
    item_rows = _link_rows(_link_json_rows(row["rows_json"]))
    client = str(row["client"] or "").strip()
    if kind == "po" and not client:
        companies = []
        for item in item_rows:
            company = _link_text(item.get("company"))
            if company and company not in companies:
                companies.append(company)
        client = ", ".join(companies[:2])
    return {
        "kind": kind,
        "no": row["no"],
        "date": row["doc_date"] or "",
        "client": client,
        "item_count": len(item_rows),
        "current": kind == current_kind and row["no"] == current_no,
    }


def document_workflow(kind: str, doc_no: str) -> dict[str, Any]:
    """Resolve PO-centered delivery/payment lanes for a PO, DN, or invoice.

    `po_fulfillment` remains the only relationship source. Repeated item marks
    collapse into one document node, while one DN/invoice spanning several POs
    appears in each relevant lane. Missing/deleted referenced docs are skipped.
    """
    kind = (kind or "").strip().lower()
    doc_no = (doc_no or "").strip()
    if kind not in ("po", "dn", "inv"):
        return {"ok": False, "error": f"bad kind: {kind}", "lanes": []}
    if not doc_no:
        return {"ok": False, "error": "missing document number", "lanes": []}

    with _connect() as conn:
        current = _workflow_node(conn, kind, doc_no, kind, doc_no)
        if not current:
            return {"ok": False, "error": f"document not found: {doc_no}", "lanes": []}

        if kind == "po":
            po_nos = [doc_no]
        else:
            po_nos = [row["po_no"] for row in conn.execute(
                "SELECT DISTINCT po_no FROM po_fulfillment "
                "WHERE source_kind=? AND source_no=? ORDER BY po_no",
                (kind, doc_no),
            ).fetchall()]

        lanes: list[dict[str, Any]] = []
        for po_no in po_nos:
            po = _workflow_node(conn, "po", po_no, kind, doc_no)
            if not po:
                continue
            source_rows = conn.execute(
                "SELECT item_index, source_kind, source_no FROM po_fulfillment "
                "WHERE po_no=? AND source_kind IN ('dn','inv') "
                "ORDER BY source_kind, source_no, item_index",
                (po_no,),
            ).fetchall()
            source_items: dict[str, dict[str, set[int]]] = {
                "dn": {}, "inv": {},
            }
            for source in source_rows:
                item_indexes = source_items[source["source_kind"]].setdefault(
                    source["source_no"], set())
                item_indexes.add(int(source["item_index"]))

            dns: list[dict[str, Any]] = []
            invoices: list[dict[str, Any]] = []
            active_dn_items: dict[str, set[int]] = {}
            for source_no in sorted(source_items["dn"]):
                node = _workflow_node(conn, "dn", source_no, kind, doc_no)
                if not node:
                    continue
                dns.append(node)
                active_dn_items[source_no] = source_items["dn"][source_no]

            for source_no in sorted(source_items["inv"]):
                node = _workflow_node(conn, "inv", source_no, kind, doc_no)
                if not node:
                    continue
                paid_indexes = source_items["inv"][source_no]
                covered_indexes: set[int] = set()
                delivery_notes: list[dict[str, Any]] = []
                for dn in dns:
                    overlap = paid_indexes & active_dn_items[dn["no"]]
                    if not overlap:
                        continue
                    covered_indexes.update(overlap)
                    delivery_notes.append({
                        "no": dn["no"],
                        "item_count": len(overlap),
                    })
                node["delivery_notes"] = delivery_notes
                node["direct_item_count"] = len(paid_indexes - covered_indexes)
                invoices.append(node)
            lanes.append({"po": po, "dns": dns, "invoices": invoices})

        # Direct DN↔INV link (2026-07-24). `po_fulfillment` only records a
        # relationship when the DN carries delivered PO items, so an invoice made
        # from a DN with no PO used to show nothing. invoices.source_dn_no stores
        # that link outright - surface it here so the pair is visible either way.
        direct_dns: list[dict[str, Any]] = []
        direct_invoices: list[dict[str, Any]] = []
        if kind == "inv":
            row = conn.execute(
                "SELECT source_dn_no FROM invoices WHERE inv_no=? "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''", (doc_no,)
            ).fetchone()
            dn_no = str((row["source_dn_no"] if row else "") or "").strip()
            if dn_no:
                node = _workflow_node(conn, "dn", dn_no, kind, doc_no)
                if node:
                    direct_dns.append(node)
        elif kind == "dn":
            for row in conn.execute(
                "SELECT inv_no FROM invoices WHERE source_dn_no=? "
                "AND TRIM(COALESCE(pdf_path,'')) <> '' ORDER BY inv_no",
                (doc_no,),
            ).fetchall():
                node = _workflow_node(conn, "inv", row["inv_no"], kind, doc_no)
                if node:
                    direct_invoices.append(node)

        # Never repeat a document that a PO lane already shows.
        shown_dns = {d["no"] for lane in lanes for d in lane["dns"]}
        shown_invoices = {i["no"] for lane in lanes for i in lane["invoices"]}
        extra_dns = [d for d in direct_dns if d["no"] not in shown_dns]
        extra_invoices = [i for i in direct_invoices if i["no"] not in shown_invoices]

        if extra_dns or extra_invoices or (not lanes and kind in ("dn", "inv")):
            lane_dns = list(extra_dns)
            lane_invoices = list(extra_invoices)
            if not lanes:
                if kind == "dn":
                    lane_dns = [current] + lane_dns
                elif kind == "inv":
                    lane_invoices = [current] + lane_invoices
            lanes.append({"po": None, "dns": lane_dns, "invoices": lane_invoices})

    linked = bool(direct_dns or direct_invoices) or any(
        lane["po"] and (kind != "po" or lane["dns"] or lane["invoices"])
        for lane in lanes
    )
    return {
        "ok": True,
        "current": {"kind": kind, "no": doc_no},
        "linked": bool(linked),
        "lanes": lanes,
    }


# --------------------------------------------------------------------------- #
# doc_cost_meta (Phase R) - per-document cost-entry currency.
# Costs are STORED in the chosen currency; profit is always reported in USD.
# --------------------------------------------------------------------------- #

# Profit-math USD peg - profile-specific (Abraj 1400, Eshraqat 1500). Sourced
# from config so it follows the active profile; database is reloaded right after
# config on every profile switch, so this stays in sync.
IQD_PER_USD = config.IQD_PER_USD
# Cash deals settle at the market rate in EVERY profile (see doc_iqd_per_usd).
CASH_IQD_PER_USD = config.CASH_IQD_PER_USD
_COST_CURRENCIES = {"USD", "IQD"}


def _cash_deal_index(conn) -> tuple[set, set]:
    """(cash_quote_roots, cash_inv_nos) - deals with an invoice paid in cash.
    A cash invoice taints its whole deal (its root quotation + that quote's DNs
    and invoices), so one deal converts at ONE rate end-to-end."""
    roots: set = set()
    invs: set = set()
    for r in conn.execute(
        "SELECT inv_no, source_quote_no FROM invoices "
        "WHERE LOWER(TRIM(COALESCE(payment_bank_key, ''))) = 'cash' "
        "AND TRIM(COALESCE(pdf_path,'')) <> ''"
    ).fetchall():
        invs.add(r["inv_no"])
        if r["source_quote_no"]:
            roots.add(r["source_quote_no"])
    return roots, invs


def _deal_root_quote(conn, kind: str, doc_no: str) -> str | None:
    """The quotation a document hangs off (itself for a quote)."""
    if kind == "quote":
        return doc_no
    table, pk = ("dns", "dn_no") if kind == "dn" else ("invoices", "inv_no")
    if kind not in ("dn", "inv"):
        return None
    r = conn.execute(
        f"SELECT source_quote_no FROM {table} WHERE {pk} = ? LIMIT 1", (doc_no,)
    ).fetchone()
    return (r["source_quote_no"] or None) if r else None


def _stored_inv_peg(inv_no: str,
                    conn: sqlite3.Connection | None = None) -> float | None:
    """The rate an IQD invoice was converted at, if one was recorded.
    NULL for every invoice issued before the column existed (2026-08-04), and
    for USD invoices, which have no rate to record."""
    try:
        with _use(conn) as conn:
            row = conn.execute(
                "SELECT sell_iqd_per_usd FROM invoices WHERE inv_no = ? LIMIT 1",
                (inv_no,),
            ).fetchone()
    except sqlite3.OperationalError:      # column not migrated in yet
        return None
    if not row:
        return None
    try:
        rate = float(row["sell_iqd_per_usd"] or 0)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def doc_iqd_per_usd(kind: str, doc_no: str,
                    conn: sqlite3.Connection | None = None) -> float:
    """The IQD-per-USD rate ONE document's costs convert at.

    The account the client is paid into decides it (2026-09-21): Bank of
    Baghdad at 1400, ADIB at 1500. In order:

      1. a rate PINNED by hand on the Costing screen - the user typed it while
         looking at the figure it produced, which is as explicit as it gets;
      2. the rate STAMPED on the document when it was saved - this is what
         keeps a June quotation on the rate its account carried in June after
         the client moves to another account in July;
      3. the fallback, for a document that has never been saved.

    Two rules were DELETED here on 2026-09-21: "Eshraqat is always 1500" and
    "a deal paid in cash is 1500". Both produced a number on the Costing
    screen with no way to see why, and cash is now simply a payment method
    with a rate anybody can read and edit. Every document that existed then
    carries a stamp written by `2026-09-21_stamp_document_cost_rates` at the
    rate those rules gave it - 171 Abraj and 16 Eshraqat documents - so
    deleting them moved no figure. `tests/rates_did_not_move.test.py` proves
    it against a snapshot taken while they were still live.

    `invoices.sell_iqd_per_usd` is deliberately NOT consulted: it is the rate
    the client was BILLED at, which a deal may legitimately differ from on the
    cost side. It was folded into the stamp for invoices that recorded one.

    The live account is NOT resolved here. A saved document is always stamped,
    and an unsaved one has no doc_no to look up: the editor asks
    `Api.rate_for_document` for that. Keeping the client-store lookup out of
    this function keeps it the light SQL call its callers depend on -
    `list_lpos` and the costing payload call it per document.
    """
    from . import payment_accounts
    doc_no = (doc_no or "").strip()
    if doc_no and kind in ("quote", "dn", "inv"):
        pinned = get_cost_rate(kind, doc_no, conn)
        if pinned:
            return pinned
        # An invoice that RECORDED the rate it was billed at keeps it. This
        # one figure converts the sell side as well as the cost side
        # (`_doc_costing_payload` passes one `rate` to `_line_econ`), and
        # `grand_total` on the printed PDF was computed from it. Deriving a
        # different number later would make the revenue on the Costing screen
        # disagree with the total on the document the client holds.
        #
        # It stays above the stamp for that reason, and only for invoices.
        # Waleed, 2026-09-21: the account's rate drives the profit maths;
        # what an invoice BILLS is untouched. Caught by
        # tests/inv_exchange_rate.test.py when an earlier draft dropped it.
        if kind == "inv":
            stored = _stored_inv_peg(doc_no, conn)
            if stored:
                return stored
        _account, stamped = get_account_stamp(kind, doc_no, conn)
        if stamped:
            return stamped
    return payment_accounts.fallback_rate()

def get_cost_currency(kind: str, doc_no: str,
                      conn: sqlite3.Connection | None = None) -> str:
    """The cost-entry currency for a document ('USD' default)."""
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return "USD"
    with _use(conn) as conn:
        row = conn.execute(
            "SELECT currency FROM doc_cost_meta WHERE kind = ? AND doc_no = ? LIMIT 1",
            (kind, doc_no),
        ).fetchone()
    cur = (row["currency"] if row else "USD") or "USD"
    return cur if cur in _COST_CURRENCIES else "USD"


def set_cost_currency(kind: str, doc_no: str, currency: str) -> dict[str, Any]:
    """Persist a document's cost-entry currency. 'USD' clears the row (default)."""
    if kind not in _SUBMIT_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    currency = (currency or "USD").strip().upper()
    if currency not in _COST_CURRENCIES:
        return {"ok": False, "error": f"bad currency: {currency}"}
    with _connect() as conn:
        if currency == "USD":
            # USD is the default, so the row is normally deleted rather than
            # stored. But it now also carries the pinned exchange rate, and
            # flipping IQD -> USD -> IQD must not silently discard that. Keep
            # the row when a rate is pinned; get_cost_currency returns "USD"
            # for a missing row either way.
            row = conn.execute(
                "SELECT rate FROM doc_cost_meta WHERE kind = ? AND doc_no = ? LIMIT 1",
                (kind, doc_no),
            ).fetchone()
            if row and row["rate"]:
                conn.execute(
                    "UPDATE doc_cost_meta SET currency = 'USD' WHERE kind = ? AND doc_no = ?",
                    (kind, doc_no),
                )
            else:
                conn.execute("DELETE FROM doc_cost_meta WHERE kind = ? AND doc_no = ?",
                             (kind, doc_no))
        else:
            conn.execute(
                "INSERT INTO doc_cost_meta(kind, doc_no, currency) VALUES(?, ?, ?) "
                "ON CONFLICT(kind, doc_no) DO UPDATE SET currency = excluded.currency",
                (kind, doc_no, currency),
            )
    return {"ok": True, "kind": kind, "doc_no": doc_no, "currency": currency}


def get_cost_rate(kind: str, doc_no: str,
                  conn: sqlite3.Connection | None = None) -> float | None:
    """The IQD-per-USD rate PINNED to this document's costs, or None.

    None means "not pinned" and the caller falls back to `doc_iqd_per_usd`.
    Every document created before 2026-08-05 is None, which is what keeps their
    profit figures exactly where they were.
    """
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return None
    try:
        with _use(conn) as conn:
            row = conn.execute(
                "SELECT rate FROM doc_cost_meta WHERE kind = ? AND doc_no = ? LIMIT 1",
                (kind, doc_no),
            ).fetchone()
    except sqlite3.OperationalError:        # column not migrated in yet
        return None
    if not row:
        return None
    try:
        rate = float(row["rate"] or 0)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def set_cost_rate(kind: str, doc_no: str, rate: float | None) -> dict[str, Any]:
    """Pin (or clear, with None/0) the rate this document's costs convert at."""
    if kind not in _SUBMIT_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    try:
        value = float(rate) if rate not in (None, "") else 0.0
    except (TypeError, ValueError):
        return {"ok": False, "error": f"bad rate: {rate!r}"}
    stored = value if value > 0 else None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO doc_cost_meta(kind, doc_no, currency, rate) "
            "VALUES(?, ?, COALESCE((SELECT currency FROM doc_cost_meta "
            "                       WHERE kind = ? AND doc_no = ?), 'USD'), ?) "
            "ON CONFLICT(kind, doc_no) DO UPDATE SET rate = excluded.rate",
            (kind, doc_no, kind, doc_no, stored),
        )
    return {"ok": True, "kind": kind, "doc_no": doc_no, "rate": stored}


def get_account_stamp(kind: str, doc_no: str,
                      conn: sqlite3.Connection | None = None) -> tuple[str, float | None]:
    """The account this document was costed against and the rate it used.

    `("", None)` means never stamped - every document that existed before
    2026-09-21 until `2026-09-21_stamp_document_cost_rates` runs, and any
    document not yet saved.
    """
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return "", None
    try:
        with _use(conn) as conn:
            row = conn.execute(
                "SELECT account_key, account_rate FROM doc_cost_meta "
                "WHERE kind = ? AND doc_no = ? LIMIT 1",
                (kind, doc_no),
            ).fetchone()
    except sqlite3.OperationalError:        # columns not migrated in yet
        return "", None
    if not row:
        return "", None
    try:
        rate = float(row["account_rate"] or 0)
    except (TypeError, ValueError):
        rate = 0.0
    return str(row["account_key"] or ""), (rate if rate > 0 else None)


def set_account_stamp(kind: str, doc_no: str, account_key: str,
                      rate: float | None) -> dict[str, Any]:
    """Freeze the account and rate this document was costed against.

    Written on every save. It is what makes a June quotation keep the rate its
    account carried in June after that account's rate, or the client's
    receiving account, changes in July.

    Touches only its own two columns: `rate` (pinned by hand) and `currency`
    are independent facts about the same costs and must survive a stamp.
    """
    if kind not in _SUBMIT_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    try:
        value = float(rate) if rate not in (None, "") else 0.0
    except (TypeError, ValueError):
        return {"ok": False, "error": f"bad rate: {rate!r}"}
    stored = value if value > 0 else None
    key = str(account_key or "").strip()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO doc_cost_meta(kind, doc_no, account_key, account_rate) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(kind, doc_no) DO UPDATE SET "
            "account_key = excluded.account_key, "
            "account_rate = excluded.account_rate",
            (kind, doc_no, key, stored),
        )
    return {"ok": True, "kind": kind, "doc_no": doc_no,
            "account_key": key, "account_rate": stored}


def get_inv_sell_currency(inv_no: str) -> str:
    """The currency an invoice was ISSUED in ('USD' default).

    Distinct from `get_cost_currency`, which is the private cost-entry currency:
    this one is what the client sees on the PDF. 2026-07-29.
    """
    inv_no = (inv_no or "").strip()
    if not inv_no:
        return "USD"
    with _connect() as conn:
        try:
            row = conn.execute(
                "SELECT sell_currency FROM invoices WHERE inv_no = ? LIMIT 1", (inv_no,)
            ).fetchone()
        except sqlite3.OperationalError:      # pre-migration DB
            return "USD"
    cur = ((row["sell_currency"] if row else "USD") or "USD").upper()
    return cur if cur in _COST_CURRENCIES else "USD"


def money_to_usd(value: Any, currency: str, rate: float | None = None) -> float:
    """Convert an amount expressed in `currency` into USD at `rate` (IQD per
    USD). `rate` defaults to the active profile's peg; pass the document's rate
    from `doc_iqd_per_usd` so cash deals convert at the market rate."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    if (currency or "USD").upper() != "IQD":
        return v
    try:
        peg = float(rate or config.IQD_PER_USD)
    except (TypeError, ValueError):
        peg = config.IQD_PER_USD
    if not math.isfinite(peg) or peg <= 0:
        peg = float(config.IQD_PER_USD)
    return v / peg if peg else v


def cost_to_usd(value: Any, currency: str, rate: float | None = None) -> float:
    """`money_to_usd` for the private cost-entry currency (`doc_cost_meta`)."""
    return money_to_usd(value, currency, rate)


# --------------------------------------------------------------------------- #
# doc_columns (Phase T) - per-document table column layout.
# --------------------------------------------------------------------------- #

def get_doc_columns(kind: str, doc_no: str) -> list[dict] | None:
    """A document's own column layout, or None when it has none (use the type's)."""
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT columns_json FROM doc_columns WHERE kind = ? AND doc_no = ? LIMIT 1",
            (kind, doc_no),
        ).fetchone()
    if not row:
        return None
    try:
        cols = json.loads(row["columns_json"] or "[]")
    except Exception:
        return None
    return cols if isinstance(cols, list) and cols else None


def set_doc_columns(kind: str, doc_no: str, columns: list[dict] | None) -> dict[str, Any]:
    """Persist a document's own column layout. Empty/None clears it (falls back
    to the type's columns)."""
    if kind not in _SUBMIT_KINDS:
        return {"ok": False, "error": f"bad kind: {kind}"}
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}
    # Keep only the lightweight shape fields - never store image data or values.
    clean: list[dict] = []
    if isinstance(columns, list):
        for c in columns:
            if not isinstance(c, dict) or not c.get("key"):
                continue
            cc = {"key": str(c["key"]), "head": str(c.get("head", "")),
                  "kind": str(c.get("kind", "text"))}
            if c.get("width") not in (None, ""):
                try:
                    cc["width"] = float(c["width"])
                except (TypeError, ValueError):
                    pass
            if c.get("align"):
                cc["align"] = str(c["align"])
            if c.get("default"):
                cc["default"] = str(c["default"])
            clean.append(cc)
    with _connect() as conn:
        if not clean:
            conn.execute("DELETE FROM doc_columns WHERE kind = ? AND doc_no = ?", (kind, doc_no))
        else:
            conn.execute(
                "INSERT INTO doc_columns(kind, doc_no, columns_json) VALUES(?, ?, ?) "
                "ON CONFLICT(kind, doc_no) DO UPDATE SET columns_json = excluded.columns_json",
                (kind, doc_no, json.dumps(clean, ensure_ascii=False)),
            )
    return {"ok": True, "kind": kind, "doc_no": doc_no, "n": len(clean)}


# --------------------------------------------------------------------------- #
# doc_meta layout - per-document PDF layout preferences.
# --------------------------------------------------------------------------- #

_LAYOUT_DOC_TYPES = {"po", "qut", "dn", "inv"}


def _ensure_doc_meta_table() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_meta (
                doc_type TEXT NOT NULL,
                doc_no   TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT,
                PRIMARY KEY (doc_type, doc_no, key)
            )
        """)


def get_doc_layout(doc_type: str, doc_no: str) -> dict[str, Any] | None:
    """Return a document's own layout metadata, or None when absent."""
    doc_type = (doc_type or "").strip()
    doc_no = (doc_no or "").strip()
    if doc_type not in _LAYOUT_DOC_TYPES or not doc_no:
        return None
    _ensure_doc_meta_table()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM doc_meta WHERE doc_type = ? AND doc_no = ? AND key = 'layout'",
            (doc_type, doc_no),
        ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        payload = json.loads(row["value"])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def set_doc_layout(doc_type: str, doc_no: str, layout: dict[str, Any] | None) -> dict[str, Any]:
    """Persist lightweight per-document layout metadata.

    `autofit_rows` is stored even when false so a document can explicitly
    override a type default that is currently enabled.
    """
    doc_type = (doc_type or "").strip()
    doc_no = (doc_no or "").strip()
    if doc_type not in _LAYOUT_DOC_TYPES:
        return {"ok": False, "error": f"bad doc_type: {doc_type}"}
    if not doc_no:
        return {"ok": False, "error": "no doc_no"}

    clean: dict[str, Any] = {}
    if isinstance(layout, dict) and "autofit_rows" in layout:
        clean["autofit_rows"] = bool(layout.get("autofit_rows"))

    _ensure_doc_meta_table()
    with _connect() as conn:
        if not clean:
            conn.execute(
                "DELETE FROM doc_meta WHERE doc_type = ? AND doc_no = ? AND key = 'layout'",
                (doc_type, doc_no),
            )
        else:
            conn.execute(
                "INSERT INTO doc_meta(doc_type, doc_no, key, value) VALUES(?, ?, 'layout', ?) "
                "ON CONFLICT(doc_type, doc_no, key) DO UPDATE SET value = excluded.value",
                (doc_type, doc_no, json.dumps(clean, ensure_ascii=False)),
            )
    return {"ok": True, "doc_type": doc_type, "doc_no": doc_no, "layout": clean}


# --------------------------------------------------------------------------- #
# active document deletion
# --------------------------------------------------------------------------- #

_DELETE_KIND_ALIASES = {
    "po": "po",
    "pos": "po",
    "purchase_order": "po",
    "purchase-orders": "po",
    "quote": "quote",
    "quotes": "quote",
    "qut": "quote",
    "quotation": "quote",
    "quotations": "quote",
    "dn": "dn",
    "dns": "dn",
    "delivery_note": "dn",
    "delivery-notes": "dn",
    "inv": "inv",
    "invoice": "inv",
    "invoices": "inv",
}

_DELETE_DOC_SPECS = {
    "po":    {"table": "pos",      "pk": "po_no",    "meta": "po",  "sat": "po"},
    "quote": {"table": "quotes",   "pk": "quote_no", "meta": "qut", "sat": "quote"},
    "dn":    {"table": "dns",      "pk": "dn_no",    "meta": "dn",  "sat": "dn"},
    "inv":   {"table": "invoices", "pk": "inv_no",   "meta": "inv", "sat": "inv"},
}


def normalize_document_kind(kind: str) -> str:
    """Return the canonical document kind used by app-wide delete operations."""
    key = str(kind or "").strip().lower().replace(" ", "_")
    return _DELETE_KIND_ALIASES.get(key, key)


def document_pdf_path(kind: str, doc_no: str) -> str:
    """Best-known pdf_path for one active document record, or empty if absent."""
    norm = normalize_document_kind(kind)
    spec = _DELETE_DOC_SPECS.get(norm)
    doc_no = (doc_no or "").strip()
    if not spec or not doc_no:
        return ""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT pdf_path FROM {spec['table']} WHERE {spec['pk']} = ? LIMIT 1",
            (doc_no,),
        ).fetchone()
    return (row["pdf_path"] if row else "") or ""


def deleted_document_numbers(kind: str) -> list[str]:
    """All tombstoned document numbers for a kind, newest tombstone first."""
    norm = normalize_document_kind(kind)
    if norm not in _DELETE_DOC_SPECS:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT doc_no FROM deleted_docs WHERE kind = ? ORDER BY deleted_at DESC",
                (norm,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [r["doc_no"] for r in rows]


def delete_document_record(kind: str, doc_no: str,
                           recycle_path: str = "",
                           pdf_path: str = "") -> dict[str, Any]:
    """
    Remove one active document from SQLite and clean the document-scoped satellite
    tables that make it appear in Costing, Needs Attention, editor settings, and
    signing/layout state. The PDF is intentionally handled by Api.delete_document
    so DB-only maintenance can still keep files on disk when needed.
    """
    norm = normalize_document_kind(kind)
    spec = _DELETE_DOC_SPECS.get(norm)
    doc_no = (doc_no or "").strip()
    if not spec:
        return {"ok": False, "error": f"unknown document kind: {kind}"}
    if not doc_no:
        return {"ok": False, "error": "missing document number"}

    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            f"SELECT pdf_path FROM {spec['table']} WHERE {spec['pk']} = ? LIMIT 1",
            (doc_no,),
        ).fetchone()
        pdf_path = pdf_path or (row["pdf_path"] if row else "") or ""
        cur = conn.execute(
            f"DELETE FROM {spec['table']} WHERE {spec['pk']} = ?",
            (doc_no,),
        )
        deleted = cur.rowcount
        conn.execute(
            """
            INSERT INTO deleted_docs(kind, doc_no, deleted_at, pdf_path, recycle_path)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(kind, doc_no) DO UPDATE SET
                deleted_at   = excluded.deleted_at,
                pdf_path     = excluded.pdf_path,
                recycle_path = excluded.recycle_path
            """,
            (norm, doc_no, now, pdf_path or "", recycle_path or ""),
        )

        satellite_deleted: dict[str, int] = {}
        sat = spec.get("sat")
        if sat:
            for table in ("submitted_docs", "doc_cost_meta", "doc_columns",
                          "attention_ignores", "signed_copies"):
                c = conn.execute(
                    f"DELETE FROM {table} WHERE kind = ? AND doc_no = ?",
                    (sat, doc_no),
                )
                satellite_deleted[table] = c.rowcount
            if norm == "inv":
                c = conn.execute("DELETE FROM paid_invoices WHERE inv_no = ?", (doc_no,))
                satellite_deleted["paid_invoices"] = c.rowcount

        meta_type = spec.get("meta")
        meta_deleted = 0
        if meta_type:
            c = conn.execute(
                "DELETE FROM doc_meta WHERE doc_type = ? AND doc_no = ?",
                (meta_type, doc_no),
            )
            meta_deleted = c.rowcount

        # Phase Y - revert PO fulfillment. Deleting a PO drops all its marks;
        # deleting a DN/INV drops only the marks it was the source of, so the
        # PO items it covered go back to "not delivered / not paid".
        if norm == "po":
            c = conn.execute("DELETE FROM po_fulfillment WHERE po_no = ?", (doc_no,))
            satellite_deleted["po_fulfillment"] = c.rowcount
        elif norm in ("dn", "inv"):
            c = conn.execute(
                "DELETE FROM po_fulfillment WHERE source_kind = ? AND source_no = ?",
                (norm, doc_no),
            )
            satellite_deleted["po_fulfillment"] = c.rowcount

        unlinked_pos = 0
        unlinked_dns = 0
        unlinked_invoices = 0
        if norm == "quote":
            c = conn.execute(
                "UPDATE pos SET source_quote_no = NULL, updated_at = ? "
                "WHERE source_quote_no = ?",
                (now, doc_no),
            )
            unlinked_pos = c.rowcount
            c = conn.execute(
                "UPDATE dns SET source_quote_no = NULL, updated_at = ? "
                "WHERE source_quote_no = ?",
                (now, doc_no),
            )
            unlinked_dns = c.rowcount
            c = conn.execute(
                "UPDATE invoices SET source_quote_no = NULL, updated_at = ? "
                "WHERE source_quote_no = ?",
                (now, doc_no),
            )
            unlinked_invoices = c.rowcount

    return {
        "ok": True,
        "kind": norm,
        "doc_no": doc_no,
        "deleted": deleted,
        "pdf_path": pdf_path,
        "recycle_path": recycle_path or "",
        "satellite_deleted": satellite_deleted,
        "doc_meta_deleted": meta_deleted,
        "unlinked_pos": unlinked_pos,
        "unlinked_dns": unlinked_dns,
        "unlinked_invoices": unlinked_invoices,
    }


# --------------------------------------------------------------------------- #
# column_memory (Phase G.14) - universal per-cell autocomplete
# --------------------------------------------------------------------------- #

_VALID_DOC_TYPES = {"po", "quote", "dn", "inv"}


# --------------------------------------------------------------------------- #
# Phase G.15 - data signature for multi-variant item memory
# --------------------------------------------------------------------------- #

# Values that describe THIS USE of an item, not the item itself. Including them
# in the signature is what turned one BUNKER into eighteen: every re-quote at a
# different price minted a new "variant", and the autocomplete filled up with
# the same item over and over.
#
# An item's identity is descriptive - description, unit, specs, custom columns.
# Its price and cost are the things you want RECALLED, so a newer save should
# update the existing row rather than sit beside it.
#
# NOTE: `shared_library._data_sig` is a second copy of this function used when
# copying catalogs between profiles. The two MUST agree or a copy re-splits
# every item it touches. Change both.
_SIG_IGNORE = {
    "qty", "rate", "cost", "cost_usd", "cost_ccy", "cost_qty", "cost_sell",
    "total", "image_refs", "money_ccy", "price_ccy",
    "cost_native", "cost_native_ccy",
    "price_native", "price_native_ccy", "item_no", "_index", "_no",
    # The wording the user gave for finding this item's picture. Volatile in
    # exactly the same way a cost is: correcting it must not fork the item
    # into a second catalog entry.
    "img_query",
    # The item's other-language name (2026-08-16). A label, not an identity:
    # بصل and Onion are one item, and adding or correcting either name must
    # not fork it. See `_STICKY_MEMORY` for why it also has to survive a save.
    "name_ar", "name_en",
}


def _data_sig(data: Any) -> str:
    """
    Deterministic short hash of the data dict so two items with the same
    `name` but different specs/description/etc. end up as distinct rows.

    - Keys are lower-cased and trimmed.
    - Values are coerced to stripped strings; empty/null entries are dropped.
    - Output: hex SHA1, first 16 chars (plenty of collision-resistance for
      our scale, keeps the PK index small).
    """
    import hashlib
    if not isinstance(data, dict):
        return ""
    norm = {}
    for k, v in data.items():
        if str(k).strip().lower() in _SIG_IGNORE: continue
        if v is None: continue
        s = str(v).strip()
        if not s: continue
        norm[str(k).strip().lower()] = s
    if not norm:
        return "empty"
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def remember_column_value(doc_type: str, sub_type: str,
                          col_key: str, value: str) -> None:
    """Bump or insert one (doc_type, sub_type, col_key, value) tuple."""
    doc_type = (doc_type or "").strip().lower()
    if doc_type not in _VALID_DOC_TYPES: return
    col_key = (col_key or "").strip()
    if not col_key: return
    value = (value or "").strip()
    if not value: return
    sub_type = (sub_type or "").strip()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect_shared() as conn:
        conn.execute(
            """
            INSERT INTO column_memory(doc_type, sub_type, col_key, value, use_count, last_used_at)
            VALUES(?, ?, ?, ?, 1, ?)
            ON CONFLICT(doc_type, sub_type, col_key, value) DO UPDATE SET
                use_count    = column_memory.use_count + 1,
                last_used_at = excluded.last_used_at
            """,
            (doc_type, sub_type, col_key, value, now),
        )


def suggest_column_values(doc_type: str, sub_type: str, col_key: str,
                          prefix: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Return up to `limit` saved values for this column that start with `prefix`.
    Sorted by use_count desc, then last_used_at desc.

    Each entry is {value, use_count, last_used_at}.
    """
    doc_type = (doc_type or "").strip().lower()
    if doc_type not in _VALID_DOC_TYPES: return []
    col_key = (col_key or "").strip()
    if not col_key: return []
    prefix = (prefix or "").strip()
    if not prefix: return []
    sub_type = (sub_type or "").strip()
    with _connect_shared() as conn:
        rows = conn.execute(
            """
            SELECT value, use_count, last_used_at FROM column_memory
            WHERE doc_type = ? AND sub_type = ? AND col_key = ?
              AND value LIKE ? || '%'
            ORDER BY use_count DESC, last_used_at DESC
            LIMIT ?
            """,
            (doc_type, sub_type, col_key, prefix, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def remember_doc_columns(doc_type: str, sub_type: str,
                         rows: list[dict[str, Any]],
                         columns: list[dict[str, Any]]) -> None:
    """
    Convenience: walk every saved row and remember every TEXT-ish cell value
    into column_memory. `columns` is the doc-type's schema columns list. Image
    cells are skipped (they're huge base64 URIs).
    """
    if not rows or not columns: return
    skip_kinds = {"image", "computed", "index"}
    for row in rows:
        for col in columns:
            if not isinstance(col, dict): continue
            kind = (col.get("kind") or "text").lower()
            if kind in skip_kinds: continue
            key = col.get("key")
            if not key: continue
            v = row.get(key)
            if v is None: continue
            s = str(v).strip()
            if s:
                remember_column_value(doc_type, sub_type, key, s)


# --------------------------------------------------------------------------- #
# Phase N.1 - Costing / profit
#
# Cost lives PER ROW inside rows_json (key "cost"), so it rides the existing
# import flow into DN/INV for free and stays attached to its row. The three
# document-level fields (source_quote_no, extra_costs_json, bid_status) are
# real columns. Profit is ALWAYS computed on read (revenue − cost), never
# stored, so a later rate change can't leave a stale profit behind.
# --------------------------------------------------------------------------- #

# kind -> (table, pk_column, rows_column, sub_type_column_or_None)
_DOC_COST_SPEC = {
    "quote": ("quotes",   "quote_no", "rows_json", "quote_type"),
    "dn":    ("dns",      "dn_no",    "rows_json", None),
    "inv":   ("invoices", "inv_no",   "rows_json", "inv_type"),
}


def _primary_key_for(kind: str, sub_type: str | None) -> str:
    """The schema column the row is anchored on (item/equipment/role/…).
    DN has no sub-types and is always anchored on 'item'."""
    if kind == "quote":
        return (config.QUOTE_TYPES.get(sub_type or "", {}) or {}).get("primary_key", "item")
    if kind == "inv":
        types = getattr(config, "INV_TYPES", {}) or {}
        return (types.get(sub_type or "", {}) or {}).get("primary_key", "item")
    return "item"


def _coerce_cost(v: Any) -> Any:
    """Normalise a cost cell to a float when possible, else '' (blank)."""
    if v is None:
        return ""
    s = str(v).strip().replace(",", "")
    if s == "":
        return ""
    # strip currency symbols / stray chars
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return ""


# Costing-only per-row fields that ride inside rows_json but never print.
# `cost_ccy` rides WITH `cost` (2026-08-13). Inheriting the number without the
# unit is worse than inheriting neither: a dinar cost carried into a re-save
# with no currency is then read as the document's, i.e. as dollars.
_COST_FIELDS = ("cost", "cost_ccy", "cost_qty", "cost_sell")


def inherit_costs(old_rows: list[dict] | None,
                  new_rows: list[dict] | None,
                  primary_key: str) -> list[dict]:
    """
    Carry the costing fields (cost + qty/sell overrides) from a previously-saved
    record into a re-saved one so a normal editor re-save never wipes data
    entered in the costing screen.

    Rule per field: if an incoming row already carries a non-empty value, keep
    it; else inherit it from the old row with the same primary-key value.
    """
    new_rows = new_rows or []
    if not old_rows:
        return new_rows
    by_pk: dict[str, dict] = {}
    for r in old_rows:
        if not isinstance(r, dict):
            continue
        pv = str(r.get(primary_key, "")).strip().lower()
        if not pv:
            continue
        keep = {f: r.get(f) for f in _COST_FIELDS if r.get(f) not in (None, "")}
        if keep:
            by_pk[pv] = keep
    if not by_pk:
        return new_rows
    for r in new_rows:
        if not isinstance(r, dict):
            continue
        pv = str(r.get(primary_key, "")).strip().lower()
        src = by_pk.get(pv)
        if not src:
            continue
        for f in _COST_FIELDS:
            if r.get(f) in (None, "") and src.get(f) not in (None, ""):
                r[f] = src[f]
    return new_rows


def _index_map(seq: list[Any] | dict[str, Any] | None) -> dict[int, Any]:
    """Normalise a list (by position) or dict (by stringified index) into an
    int-indexed map. None -> empty (so 'not provided' leaves rows untouched)."""
    out: dict[int, Any] = {}
    if isinstance(seq, dict):
        for k, v in seq.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
    elif isinstance(seq, list):
        for i, v in enumerate(seq):
            out[i] = v
    return out


def save_doc_costs(kind: str, doc_no: str,
                   costs: list[Any] | dict[str, Any] | None = None,
                   extra_costs: list[dict] | None = None,
                   bid_status: str | None = None,
                   source_quote_no: str | None = None,
                   qtys: list[Any] | dict[str, Any] | None = None,
                   sells: list[Any] | dict[str, Any] | None = None,
                   currency: str | None = None,
                   cost_ccys: list[Any] | dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Patch ONLY the costing data of an existing document - never touches the PDF.

    `costs`/`qtys`/`sells` align to the document's stored rows (list by position
    or dict by stringified index). `cost` is the per-unit cost; `qtys`/`sells`
    are the costing qty/sell OVERRIDES - they're persisted only when they differ
    from what the document already implies (or confirm an auto-detected guess),
    so standard rows stay in sync with later document edits. `currency` sets the
    doc's cost-entry currency. Doc-level fields write only when provided.

    **A cost is never written without its currency (2026-08-26).** `cost_ccys`
    aligns to the rows the same way `costs` does and carries the unit each
    number was typed in on the Costing screen. Before this, saving costs wrote
    the number and left whatever `cost_ccy` the row already had, so a line
    stamped IQD by the DN editor and then re-costed in dollars kept the IQD
    stamp - the profit engine divided that dollar figure by the peg. When
    `cost_ccys` has nothing for a row, the row is stamped with `currency` (the
    screen's entry currency), and failing that with what it already reads as.
    """
    spec = _DOC_COST_SPEC.get(kind)
    if not spec:
        return {"ok": False, "error": f"unknown doc kind: {kind}"}
    table, pk_col, rows_col, sub_col = spec
    now = datetime.now().isoformat(timespec="seconds")
    # Read BEFORE the write connection opens - its own connection must not be
    # asked for while this one is mid-transaction.
    stored_ccy = get_cost_currency(kind, doc_no)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {pk_col} = ?", (doc_no,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"{kind} not found: {doc_no}"}
        d = dict(row)
        rows = json.loads(d.get(rows_col) or "[]")

        cost_map = _index_map(costs)
        qty_map  = _index_map(qtys)
        sell_map = _index_map(sells)
        ccy_map  = _index_map(cost_ccys)
        # An explicit `currency` is the caller ASSERTING what unit its numbers
        # are typed in, so it stamps the rows it writes. Without one, each row
        # keeps the unit it already reads as (`pull_costs_from_quote` passes
        # some rows through untouched).
        entry_ccy = str(currency or "").strip().upper()
        entry_ccy = entry_ccy if entry_ccy in _COST_CURRENCIES else ""
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            if i in cost_map:
                r["cost"] = _coerce_cost(cost_map[i])
                # The number and its unit are written together, always - see
                # the docstring. An unrecognised value falls back rather than
                # guessing, because guessing wrong is a factor-of-1400 error.
                want = str(ccy_map.get(i) or "").strip().upper()
                if want not in _COST_CURRENCIES:
                    want = entry_ccy or row_cost_currency(r, stored_ccy)
                r["cost_ccy"] = want
            # qty/sell overrides: store only a genuine override; otherwise clear
            # so the row keeps tracking the document. _detect_unit_econ reads
            # only qty/rate/total/col_*, so it is unaffected by prior overrides.
            if i in qty_map or i in sell_map:
                unit_sell, base_qty, auto = _detect_unit_econ(r)
                if i in qty_map:
                    qv = _num(qty_map[i])
                    if qv is not None and abs(qv - base_qty) > 1e-9:
                        r["cost_qty"] = qv
                    else:
                        r.pop("cost_qty", None)
                if i in sell_map:
                    sv = _num(sell_map[i])
                    if sv is not None and (auto or abs(sv - unit_sell) > 1e-9):
                        r["cost_sell"] = sv
                    else:
                        r.pop("cost_sell", None)

        sets = [f"{rows_col} = ?", "updated_at = ?"]
        vals: list[Any] = [json.dumps(rows, ensure_ascii=False), now]
        if extra_costs is not None:
            sets.append("extra_costs_json = ?")
            vals.append(json.dumps(extra_costs or [], ensure_ascii=False))
        if bid_status is not None and kind == "quote":
            sets.append("bid_status = ?")
            vals.append(str(bid_status))
        # source_quote_no only exists on dn / invoices (a quote IS the source).
        if source_quote_no is not None and kind in ("dn", "inv"):
            sets.append("source_quote_no = ?")
            vals.append(str(source_quote_no or ""))
        vals.append(doc_no)
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {pk_col} = ?", vals)
    if currency is not None:
        set_cost_currency(kind, doc_no, currency)
    return {"ok": True, "kind": kind, "doc_no": doc_no}


def set_bid_status(quote_no: str, status: str) -> dict[str, Any]:
    """Mark a quotation open / won / lost (manual override)."""
    status = (status or "open").strip().lower()
    if status not in ("open", "won", "lost"):
        return {"ok": False, "error": f"bad status: {status}"}
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        quote = conn.execute(
            "SELECT pdf_path FROM quotes WHERE quote_no = ? LIMIT 1",
            (quote_no,),
        ).fetchone()
        if not quote:
            return {"ok": False, "error": f"quote not found: {quote_no}"}
        if not str(quote["pdf_path"] or "").strip():
            return {
                "ok": False,
                "error": "complete quotation pricing before changing bid status",
            }
        cur = conn.execute(
            "UPDATE quotes SET bid_status = ?, updated_at = ? WHERE quote_no = ?",
            (status, now, quote_no),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": f"quote not found: {quote_no}"}
    return {"ok": True, "quote_no": quote_no, "bid_status": status}


def _num(v: Any) -> float | None:
    """Parse a possibly-messy cell into a float, or None when not numeric."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
    if cleaned in ("", ".", "-", "--"):
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _detect_unit_econ(row: dict) -> tuple[float, float, bool]:
    """
    (unit_sell, base_qty, auto) for a row - its per-UNIT sell price and the
    quantity the document implies, plus an `auto` flag set when the value is a
    guess the user should confirm.

    Order:
      1. an explicit computed 'total' column (materials / DN / INV with prices)
      2. custom extra columns (col_2, col_3, …) - a table the user extended,
         where the real total usually lives in one of them (e.g. QUT-EQ-HDEC-013
         keeps it in col_6). We take the largest positive custom value as the
         guess unless plain qty*rate already explains it.
      3. standard qty*rate: rate IS the unit price.
      4. a flat-rate line (equipment monthly / service / manpower): the rate is
         the per-unit price and quantity defaults to 1 - so entering qty 10
         later gives revenue 10*rate, not rate/10.
    """
    qty = _num(row.get("qty"))
    rate = _num(row.get("rate"))
    t = _num(row.get("total"))
    if t is not None and t > 0:
        bq = qty if (qty and qty > 0) else 1.0
        return t / bq, bq, False              # explicit line total -> unit = total/qty
    custom_vals = [n for k, v in row.items()
                   if isinstance(k, str) and k.startswith("col_")
                   and not (isinstance(v, str)
                            and v.lstrip().lower().startswith("data:image/"))
                   for n in (_num(v),) if n is not None and n > 0]
    if custom_vals:
        cand = max(custom_vals)
        if qty and rate and abs(qty * rate - cand) < 0.005:
            return rate, qty, False           # qty*rate explains it -> trusted
        bq = qty if (qty and qty > 0) else 1.0
        return cand / bq, bq, True            # genuine guess -> flag for review
    if qty is not None and qty > 0 and rate is not None:
        return rate, qty, False
    if rate is not None and rate > 0:
        return rate, 1.0, False               # flat-rate: unit price, qty 1
    return 0.0, 1.0, False


def row_cost_currency(row: dict, doc_currency: str = "USD") -> str:
    """The currency THIS row's cost is typed in.

    Falls back to the document's cost currency, which is what every row saved
    before per-cell currencies existed relies on. Anything unrecognised falls
    back too rather than guessing - a bad value must not silently divide a
    dollar cost by 1400.
    """
    value = str((row or {}).get("cost_ccy", "") or "").strip().upper()
    if value in _COST_CURRENCIES:
        return value
    doc = str(doc_currency or "USD").strip().upper()
    return doc if doc in _COST_CURRENCIES else "USD"


def _line_econ(row: dict, pk: str, currency: str = "USD",
               rate: float | None = None, sell_currency: str = "USD") -> dict[str, Any]:
    """
    Per-line economics for the costing screen, in USD.

    Honors saved costing overrides (`cost_qty`, `cost_sell`) entered in the grid;
    otherwise seeds qty and a per-unit sell from the document. Cost is read in
    the document's entry currency and converted to USD at `rate` (the document's
    peg from `doc_iqd_per_usd`; profile default when omitted) for all profit math.

    `sell_currency` (2026-07-29) is the currency the DOCUMENT was issued in - an
    invoice issued in dinars carries dinar `rate` cells, so the seeded sell has
    to be converted too or revenue would be IQD while cost is USD. A `cost_sell`
    override is always USD: it is typed on the costing screen, which is USD.
    """
    name = str(row.get(pk) or row.get("item") or row.get("description") or "").strip()
    unit_sell, base_qty, auto = _detect_unit_econ(row)
    if (sell_currency or "USD").upper() == "IQD":
        unit_sell = money_to_usd(unit_sell, "IQD", rate)

    q_override = _num(row.get("cost_qty"))
    qty = q_override if q_override is not None else base_qty

    s_override = _num(row.get("cost_sell"))
    if s_override is not None:
        sell = s_override
        auto = False                          # user confirmed it
    else:
        sell = unit_sell

    revenue = qty * sell
    cost_raw = _coerce_cost(row.get("cost"))          # entry-currency, '' if blank
    has_cost = cost_raw != ""
    # 2026-08-06 - each ROW may carry its own cost currency, so one table can
    # hold a dollar-costed line beside a dinar-costed one and both convert
    # correctly. A row without `cost_ccy` inherits the document's currency,
    # which is every row saved before this existed.
    cost_unit_usd = (cost_to_usd(cost_raw, row_cost_currency(row, currency), rate)
                     if has_cost else 0.0)
    line_cost = qty * cost_unit_usd
    billable = revenue > 0 or sell > 0
    # 2026-10-21 (Waleed) - a billable line with no cost is NOT 100% profit.
    # Its cost is estimated at config.UNCOSTED_MARKUP on cost, so every total
    # built from this function (Costing, Insights, the dashboard, the monthly
    # report) stays close to real. `has_cost` stays False: the line is still
    # uncosted and still asked for; `est_cost` says how much of `line_cost`
    # is a guess.
    est_cost = 0.0
    if billable and not has_cost and revenue > 0:
        est_cost = revenue / (1.0 + config.UNCOSTED_MARKUP)
        line_cost = est_cost
    return {
        "name": name, "qty": qty, "sell": sell, "revenue": revenue,
        "cost_raw": cost_raw, "has_cost": has_cost,
        "line_cost": line_cost, "line_profit": revenue - line_cost,
        "est_cost": est_cost,
        "auto": bool(auto),
        "billable": billable,
    }


def _rows_fully_costed(rows: list, pk: str) -> bool:
    """
    Req 3 - a doc is "fully costed" only when EVERY billable line has a cost and
    a usable qty. Any billable line missing a cost (or with a cost but qty<=0)
    keeps the "Uncosted" flag on. Extra costs alone no longer count.
    """
    billable = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        e = _line_econ(r, pk)
        if not e["billable"]:
            continue
        billable += 1
        if not (e["has_cost"] and e["qty"] > 0):
            return False
    return billable > 0


def _doc_costing_payload(kind: str, d: dict[str, Any]) -> dict[str, Any]:
    """Shape a loaded doc record into the costing view's payload + totals (USD)."""
    sub_type = d.get("quote_type") or d.get("inv_type") or ""
    pk = _primary_key_for(kind, sub_type)
    rows = d.get("rows") or []
    doc_no = d.get("quote_no") or d.get("inv_no") or d.get("dn_no")
    currency = get_cost_currency(kind, doc_no) if doc_no else "USD"
    # One peg per DEAL: Eshraqat (or any profile when the deal's invoice is cash)
    # settles at the market rate. Resolved once here and used for every line.
    rate = doc_iqd_per_usd(kind, doc_no) if doc_no else config.IQD_PER_USD
    # 2026-07-29 - an invoice issued in dinars has dinar `rate` cells. The
    # costing screen is USD end to end, so convert the sell side as well.
    sell_currency = get_inv_sell_currency(doc_no) if (kind == "inv" and doc_no) else "USD"

    revenue = 0.0
    cost_total = 0.0
    est_total = 0.0
    billable = 0
    all_costed = True
    out_rows: list = []
    for r in rows:
        if not isinstance(r, dict):
            out_rows.append(r)
            continue
        e = _line_econ(r, pk, currency, rate, sell_currency)
        revenue += e["revenue"]
        cost_total += e["line_cost"]
        est_total += e["est_cost"]
        if e["billable"]:
            billable += 1
            if not (e["has_cost"] and e["qty"] > 0):
                all_costed = False
        # Annotate a COPY for the editor (seed qty/sell + auto flag); never
        # mutate the stored row here.
        rr = dict(r)
        rr["c_qty"] = round(e["qty"], 4)
        rr["c_sell"] = round(e["sell"], 4)
        rr["c_auto"] = e["auto"]
        # RESOLVED, never blank (2026-08-26): the Costing screen prints the raw
        # `cost` number, so it has to be told which unit that number is in. A
        # row with no stamp inherits the document's, exactly as `_line_econ`
        # reads it - otherwise the screen shows a dinar cost as dollars and
        # disagrees with the profit it is showing beside it.
        rr["cost_ccy"] = row_cost_currency(r, currency)
        out_rows.append(rr)

    extra = []
    try:
        extra = json.loads(d.get("extra_costs_json") or "[]")
    except Exception:
        extra = []
    extra_total = 0.0
    for e in extra:
        if isinstance(e, dict):
            extra_total += cost_to_usd(e.get("amount"), currency, rate)

    cost_all = cost_total + extra_total
    profit = revenue - cost_all
    margin = (profit / revenue * 100.0) if revenue else 0.0
    return {
        "kind": kind,
        "doc_no": doc_no,
        "sub_type": sub_type,
        "client_name": d.get("client_name", ""),
        "date": d.get("quote_date") or d.get("inv_date") or d.get("dn_date") or "",
        "rows": out_rows,
        "primary_key": pk,
        "cost_currency": currency,
        # The currency the doc was ISSUED in. Every number below is still USD -
        # this is here so the UI can say "invoiced in IQD" without re-querying.
        "sell_currency": sell_currency,
        # The peg this doc's profit was computed at, so the UI converts IQD the
        # same way (profile peg, or the market rate for a cash deal).
        "iqd_per_usd": rate,
        "source_quote_no": d.get("source_quote_no") or "",
        # bid_status is a quote-only concept; DN/INV never have it.
        "bid_status": (d.get("bid_status") or "open") if kind == "quote" else None,
        "extra_costs": extra,
        # "costed" now means FULLY costed (req 3): drives the Uncosted pill.
        "costed": bool(billable > 0 and all_costed),
        "totals": {
            "revenue": round(revenue, 2),
            "line_cost": round(cost_total, 2),
            # the part of `line_cost` that is the UNCOSTED_MARKUP estimate for
            # lines with no cost entered (2026-10-21); 0 when fully costed
            "estimated_cost": round(est_total, 2),
            "extra_cost": round(extra_total, 2),
            "cost": round(cost_all, 2),
            "profit": round(profit, 2),
            "margin": round(margin, 1),
        },
    }


# Phase V - an untouched (open) quotation older than this many days, with no
# invoice and no manual win/loss, is treated as a LOST bid (stale).
# Phase X.26 - this is now also PERSISTED by auto_lose_stale_quotes(): once a
# quote passes the cutoff it is stored 'lost' so it stops nagging in Needs
# Attention. An invoice still wins (see effective_bid_status), so an auto-lost
# quote that later gets invoiced correctly reads 'won' again.
QUOTE_STALE_DAYS = 14

# Phase X.26 - open quotes this old (but not yet auto-lost) surface in the
# "Decide" bucket so the user can mark Won/Lost before the 14-day auto-loss.
QUOTE_DECIDE_DAYS = 7


def _parse_doc_date(s: Any) -> date | None:
    """Parse a stored document date ('d/m/yyyy' or ISO 'yyyy-mm-dd') to a date."""
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def quote_is_stale(quote_date: Any, today: date | None = None) -> bool:
    """True when an open quote is older than QUOTE_STALE_DAYS."""
    d = _parse_doc_date(quote_date)
    if not d:
        return False
    return ((today or date.today()) - d).days >= QUOTE_STALE_DAYS


def effective_bid_status(stored: str | None, has_invoice: bool,
                         quote_date: Any = None, today: date | None = None) -> str:
    """
    The status the UI should show / insights should count.
      - a linked invoice (or a manual 'won') means 'won'. Phase X.26: an invoice
        ALWAYS wins, even over a stored 'lost' - so an auto-lost bid that later
        gets invoiced (or a manual-lost that turns out invoiced) reads 'won'.
      - a manual/auto 'lost' means 'lost'
      - Phase V: an open bid older than QUOTE_STALE_DAYS (no invoice) is 'lost'
      - else 'open'
    """
    s = (stored or "open").strip().lower()
    if has_invoice or s == "won":
        return "won"
    if s == "lost":
        return "lost"
    if quote_date is not None and quote_is_stale(quote_date, today):
        return "lost"
    return "open"


def quote_has_invoice(quote_no: str) -> bool:
    """True if any invoice links back to this quotation (source_quote_no)."""
    if not quote_no:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM invoices WHERE source_quote_no = ? "
            "AND TRIM(COALESCE(pdf_path,'')) <> '' LIMIT 1", (quote_no,)
        ).fetchone()
    return row is not None


def _won_quote_nos(conn) -> set[str]:
    """Set of quote numbers that have at least one linked invoice."""
    rows = conn.execute(
        "SELECT DISTINCT source_quote_no FROM invoices "
        "WHERE source_quote_no IS NOT NULL AND source_quote_no <> '' "
        "AND TRIM(COALESCE(pdf_path,'')) <> ''"
    ).fetchall()
    return {r["source_quote_no"] for r in rows}


def load_doc_costing(kind: str, doc_no: str) -> dict[str, Any] | None:
    """Load one document's costing payload (rows + cost + totals)."""
    if kind == "quote":
        d = load_quote(doc_no)
    elif kind == "dn":
        d = load_dn(doc_no)
    elif kind == "inv":
        d = load_inv(doc_no)
    else:
        return None
    if not d:
        return None
    payload = _doc_costing_payload(kind, d)
    if kind == "quote":
        has_inv = quote_has_invoice(doc_no)
        payload["has_invoice"] = has_inv
        payload["effective_status"] = effective_bid_status(
            payload.get("bid_status"), has_inv, payload.get("date"))
        payload["stale"] = (payload["effective_status"] == "lost"
                            and (payload.get("bid_status") or "open") != "lost"
                            and not has_inv)
    return payload


def _submitted_at_map(conn, kind: str) -> dict[str, str]:
    """Phase X.26 - {doc_no: submitted_at} for one kind (bid clock origin)."""
    return {r["doc_no"]: r["submitted_at"] for r in conn.execute(
        "SELECT doc_no, submitted_at FROM submitted_docs WHERE kind=?", (kind,)
    ).fetchall()}


def auto_lose_stale_quotes(today: date | None = None) -> int:
    """
    Phase X.26 - persist the 14-day rule. A quote is a BID only once it is
    SUBMITTED; the bid clock starts at its `submitted_at`. Any submitted, stored
    'open' quote that is >= QUOTE_STALE_DAYS old (since submission) with NO linked
    invoice is marked 'lost' so it stops nagging. Unsubmitted quotes are drafts -
    never auto-lost. Idempotent (only touches stored 'open'); skips invoiced
    quotes (effectively won). Returns the count newly marked lost.
    """
    today = today or date.today()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        won = _won_quote_nos(conn)
        sub = _submitted_at_map(conn, "quote")
        losers: list[str] = []
        for r in conn.execute(
            "SELECT quote_no FROM quotes "
            "WHERE COALESCE(bid_status,'open') = 'open'"
        ).fetchall():
            no = r["quote_no"]
            if no in won:
                continue
            sat = sub.get(no)
            if not sat:
                continue                      # not submitted → not a bid → never lost
            d = _parse_doc_date(str(sat)[:10])
            if d and (today - d).days >= QUOTE_STALE_DAYS:
                losers.append(no)
        if losers:
            conn.executemany(
                "UPDATE quotes SET bid_status='lost', updated_at=? WHERE quote_no=?",
                [(now, no) for no in losers],
            )
        return len(losers)


def _submitted_only(entries: list[dict[str, Any]],
                    submitted_by_kind: dict[str, set]) -> list[dict[str, Any]]:
    """Keep only entries whose document has been SUBMITTED.

    Matched on `(kind, doc_no)`, never on the number alone - that pair is the
    `submitted_docs` primary key for a reason, and a PO number is only unique
    among POs. See `dashboard_needs_attention` for why the gate exists.
    """
    return [e for e in entries
            if e["doc_no"] in submitted_by_kind.get(e["kind"], set())]


def _oldest_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp each attention entry with `age_days` and sort OLDEST first.

    Every Needs-Attention bucket spans all months now, so age is what ranks
    them: the thing that has been waiting longest is the thing most likely to
    be forgotten. Undated rows sort last rather than pretending to be ancient.
    """
    today = date.today()
    for e in entries:
        d = _parse_doc_date(e.get("date"))
        e["age_days"] = (today - d).days if d else None
    entries.sort(key=lambda e: (_parse_doc_date(e.get("date")) or date.max,
                                str(e.get("doc_no") or "")))
    return entries


def list_unsubmitted(year: int | None = None,
                     month: int | None = None) -> list[dict[str, Any]]:
    """
    Docs not yet marked submitted (submitted_docs). PO/QUT/DN/INV.

    2026-08-10 - **month scoping is now opt-in.** Pass nothing and this spans
    every month, because an unsubmitted document from June is still
    unsubmitted in August: scoping it to the open month meant the reminder
    silently vanished at the month boundary and the work was forgotten. Pass
    `year`+`month` to look at one month deliberately. Cheap - no rows_json parse.
    """
    scoped = year is not None and month is not None
    where = " WHERE year=? AND month=?" if scoped else ""
    args: tuple = (year, month) if scoped else ()
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        def _subset(kind: str) -> set[str]:
            return {r["doc_no"] for r in conn.execute(
                "SELECT doc_no FROM submitted_docs WHERE kind=?", (kind,))}
        sub_p, sub_q, sub_d, sub_i = (
            _subset("po"), _subset("quote"), _subset("dn"), _subset("inv"))
        for r in conn.execute(
            "SELECT po_no, po_date FROM pos" + where + " ORDER BY po_no", args,
        ).fetchall():
            if r["po_no"] in sub_p:
                continue
            out.append({"kind": "po", "doc_no": r["po_no"],
                        "client": "", "date": r["po_date"], "sub_type": ""})
        quote_where = ((where + " AND ") if where else " WHERE ") \
            + "TRIM(COALESCE(pdf_path,'')) <> ''"
        for r in conn.execute(
            "SELECT quote_no, quote_date, quote_type, client_name "
            "FROM quotes" + quote_where + " ORDER BY quote_no", args,
        ).fetchall():
            if r["quote_no"] in sub_q:
                continue
            out.append({"kind": "quote", "doc_no": r["quote_no"],
                        "client": r["client_name"] or "", "date": r["quote_date"],
                        "sub_type": r["quote_type"] or ""})
        for r in conn.execute(
            "SELECT dn_no, dn_date, client_name "
            "FROM dns" + where + " ORDER BY dn_no", args,
        ).fetchall():
            if r["dn_no"] in sub_d:
                continue
            out.append({"kind": "dn", "doc_no": r["dn_no"],
                        "client": r["client_name"] or "", "date": r["dn_date"],
                        "sub_type": ""})
        inv_where = ((where + " AND ") if where else " WHERE ") \
            + "TRIM(COALESCE(pdf_path,'')) <> ''"
        for r in conn.execute(
            "SELECT inv_no, inv_date, inv_type, client_name "
            "FROM invoices" + inv_where + " ORDER BY inv_no", args,
        ).fetchall():
            if r["inv_no"] in sub_i:
                continue
            out.append({"kind": "inv", "doc_no": r["inv_no"],
                        "client": r["client_name"] or "", "date": r["inv_date"],
                        "sub_type": r["inv_type"] or ""})
    return _oldest_first(out)


def list_unpaid() -> list[dict[str, Any]]:
    """
    Phase X.28 - ALL outstanding invoices (any month) not yet marked paid
    (paid_invoices). Invoice-only, mirroring the right-panel Paid button. An
    unpaid invoice is money owed regardless of age, so this is deliberately NOT
    month-scoped (like the `decide` bucket). Oldest first, so the most overdue
    sit at the top. Cheap - no rows_json parse.
    """
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        paid = {r["inv_no"] for r in conn.execute("SELECT inv_no FROM paid_invoices")}
        for r in conn.execute(
            "SELECT inv_no, inv_date, inv_type, client_name FROM invoices "
            "WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
        ).fetchall():
            if r["inv_no"] in paid:
                continue
            out.append({"kind": "inv", "doc_no": r["inv_no"],
                        "client": r["client_name"] or "", "date": r["inv_date"],
                        "sub_type": r["inv_type"] or ""})
    # Oldest first (most overdue at the top); undated rows sort last.
    return _oldest_first(out)


def read_po_company_tags(settings: dict) -> dict[str, str]:
    """The PO company classification as config reads it: the section
    `purchase_order.company_tags` first (where PO Settings saves it), the old
    top-level `po_company_tags` only when the section has none.

    2026-10-21 - there were TWO copies, and the PO editor's "client or our
    company?" prompt read and wrote the OLD top-level one, then replaced the
    in-memory map with it. On Abraj that copy still had ابراج البدر as
    "client" while PO Settings had it "internal" - so answering (or just
    cancelling) the prompt for a typo turned every Abraj-company PO into
    "to be invoiced" in the To-Do until the app restarted. Waleed saw it."""
    from . import doc_settings as _ds
    field = next(f for f in _ds.PO_FIELDS if f.target == "PO_COMPANY_TAGS")
    value = _ds.read(settings or {}, "po", field)
    return dict(value) if isinstance(value, dict) else {}


def write_po_company_tags(settings: dict, tags: dict[str, str]) -> None:
    """Store the map in the section ONLY, drop the old top-level copy so the
    two can never disagree again, save, and update the in-memory config."""
    section = settings.get("purchase_order")
    section = dict(section) if isinstance(section, dict) else {}
    section["company_tags"] = dict(tags)
    settings["purchase_order"] = section
    settings.pop("po_company_tags", None)
    config.save_user_settings(settings)
    config.PO_COMPANY_TAGS = {str(k).strip(): str(v).strip().lower()
                              for k, v in tags.items()
                              if str(k).strip() and str(v).strip().lower() in ("client", "internal")}


def seed_po_company_tags() -> dict[str, str]:
    """One-time: if settings.json has no company classification yet, populate it
    with every distinct company name currently used across saved POs, all tagged
    "client" (the user re-tags office names later in Settings). Idempotent - once
    a NON-empty map exists it is left alone. Returns the resulting map.
    Per active profile (settings.json is per profile)."""
    settings = config.load_user_settings() or {}
    existing = read_po_company_tags(settings)
    if existing:
        return existing
    names: set[str] = set()
    with _connect() as conn:
        for row in conn.execute("SELECT rows_json FROM pos"):
            try:
                items = json.loads(row["rows_json"] or "[]")
            except (TypeError, ValueError):
                continue
            for it in items:
                if isinstance(it, dict):
                    c = (it.get("company") or "").strip()
                    if c:
                        names.add(c)
    tags = {n: "client" for n in sorted(names)}
    write_po_company_tags(settings, tags)   # in-memory too, so no reload needed
    return tags


def pos_awaiting_invoice(year: int | None = None,
                         month: int | None = None) -> list[dict[str, Any]]:
    """POs that still have at least one CLIENT line not yet invoiced.

    2026-08-10 - **all months by default** (pass `year`+`month` to scope one).
    A PO raised in June whose client lines were never invoiced is still owed
    money in August; month-scoping it meant it dropped off the To-Do at the
    month boundary, which is exactly how one gets forgotten. A line is a client line when its company name is non-blank and NOT
    tagged "internal" (see config.po_company_is_internal - untagged counts as
    client). "Invoiced" = that item index carries the legacy stored 'paid' link
    mark in po_fulfillment (set when an invoice links the PO's items, regardless
    of payment). Office/blank lines are ignored; a
    PO with only office/blank lines, or whose client lines are all invoiced, never
    appears. Removal trigger is the INVOICE - a delivery note ('delivered') does
    NOT clear it. Shape matches the other Needs-Attention buckets so `_split`
    (ignore partition) and the To-Do renderer can consume it."""
    scoped = year is not None and month is not None
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        po_rows = conn.execute(
            "SELECT po_no, po_date, rows_json FROM pos"
            + (" WHERE year=? AND month=?" if scoped else "")
            + " ORDER BY po_no", ((year, month) if scoped else ()),
        ).fetchall()
        if not po_rows:
            return out
        invoiced: dict[str, set] = {}
        for r in conn.execute(
            "SELECT f.po_no, f.item_index FROM po_fulfillment f "
            "LEFT JOIN invoices i ON i.inv_no=f.source_no "
            "WHERE f.status_kind='paid' AND f.source_kind='inv' "
            "AND (i.inv_no IS NULL OR TRIM(COALESCE(i.pdf_path,'')) <> '')"
        ).fetchall():
            invoiced.setdefault(r["po_no"], set()).add(r["item_index"])
    for r in po_rows:
        try:
            items = json.loads(r["rows_json"] or "[]")
        except (TypeError, ValueError):
            items = []
        invoiced_idx = invoiced.get(r["po_no"], set())
        client_names: list[str] = []
        unpaid = 0
        lines = 0
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            comp = (it.get("company") or "").strip()
            if not comp or config.po_company_is_internal(comp):
                continue                          # office or blank → not billable
            if comp not in client_names:
                client_names.append(comp)
            lines += 1
            if i not in invoiced_idx:
                unpaid += 1
        if unpaid > 0:
            label = ", ".join(client_names)
            out.append({
                "kind": "po", "doc_no": r["po_no"],
                "client": label if len(label) <= 60 else label[:57] + "…",
                "date": r["po_date"], "sub_type": "",
                # the To-Do table's "Lines to invoice" column (2026-10-21)
                "open_lines": unpaid, "client_lines": lines,
            })
    return _oldest_first(out)


def dashboard_needs_attention(year: int | None = None,
                              month: int | None = None) -> dict[str, Any]:
    """
    The "Needs Attention" feed behind the dashboard card and the To-Do view.

    **2026-08-10 - every bucket now spans ALL months by default.** It used to
    scope `uncosted`, `unsubmitted` and `deliver` to one month, so a PO from
    June with uninvoiced client lines, or a document never marked submitted,
    quietly disappeared from the To-Do the moment the month rolled over - the
    one place whose whole job is to stop things being forgotten. Measured on
    the live Abraj data the day this changed: standing on August the list
    showed 15 items and hid 55. Pass `year`+`month` to scope one month on
    purpose (the To-Do view offers that as an explicit filter); omit them for
    everything outstanding.

    **A bucket that waits on the OTHER PARTY only lists SUBMITTED documents.**
    A draft nobody has been sent cannot be overdue or awaiting an invoice, so
    `decide` (always), and `unpaid` + `deliver` (since 2026-08-10), require the
    document to have gone out; it appears once under `unsubmitted`, which is
    the only action that applies to it. `uncosted` is deliberately EXEMPT -
    costing is internal and happens before you send a quotation, so gating it
    would remove the reminder exactly when it is most useful.

    Buckets, all sorted OLDEST FIRST and stamped with `age_days`:
      - decide:      SUBMITTED open quotes >= QUOTE_DECIDE_DAYS old (since
                     submission) but not yet auto-lost - mark Won/Lost before
                     the 14-day cutoff. Carries days_open + days_left.
                     Unsubmitted quotes are drafts, not bids, so they only
                     appear under `unsubmitted`. Never month-scoped: age is the
                     entire point.
      - uncosted:    QUT/INV docs lacking complete cost data. NOT gated on
                     submission (costing precedes sending).
      - unsubmitted: docs not marked submitted. Never gated - gating it would
                     hide the work entirely.
      - unpaid:      SUBMITTED invoices not marked paid.
      - deliver:     SUBMITTED POs with a CLIENT line not yet invoiced
                     (pos_awaiting_invoice). Office/blank lines are ignored; the
                     removal trigger is the invoice, not the DN.
      - ignored:     entries the user muted (attention_ignores), partitioned OUT
                     of the buckets above and stamped with their `category` so
                     the To-Do view can offer Restore. Only entries that would
                     currently need attention appear (a muted doc that gets
                     costed/paid/submitted drops out entirely). The mute is the
                     escape hatch for old items the user will never action -
                     do NOT re-introduce a date cap instead, that is the bug.

    Runs the 14-day auto-lose sweep FIRST, so quotes past the cutoff are already
    resolved to 'lost' and never appear here (they stop nagging).
    """
    auto_lose_stale_quotes()
    scoped = year is not None and month is not None
    month_where = " WHERE year=? AND month=?" if scoped else ""
    month_args: tuple = (year, month) if scoped else ()
    uncosted: list[dict[str, Any]] = []
    with _connect() as conn:
        # Uncosted in the requested month - quote estimates + invoice actuals.
        # Delivery Notes remain linkable in deal flow, but are operational docs
        # rather than active costing targets.
        for r in conn.execute(
            "SELECT quote_no, quote_date, quote_type, client_name, rows_json "
            "FROM quotes" + month_where, month_args,
        ).fetchall():
            if _costed_from_json(r["rows_json"], "quote", r["quote_type"] or ""):
                continue
            uncosted.append({
                "kind": "quote", "doc_no": r["quote_no"],
                "client": r["client_name"] or "",
                "date": r["quote_date"], "sub_type": r["quote_type"] or "",
            })
        for r in conn.execute(
            "SELECT inv_no, inv_date, inv_type, client_name, rows_json "
            "FROM invoices" + month_where, month_args,
        ).fetchall():
            if _costed_from_json(r["rows_json"], "inv", r["inv_type"] or ""):
                continue
            uncosted.append({
                "kind": "inv", "doc_no": r["inv_no"],
                "client": r["client_name"] or "",
                "date": r["inv_date"], "sub_type": r["inv_type"] or "",
            })
        _oldest_first(uncosted)

    b = _attention_buckets(date.today(), year, month, swept=True)
    # `uncosted` is deliberately NOT gated on submission: costing is an INTERNAL
    # step that happens BEFORE you send a quotation - the cost is how you price
    # it - so gating it would drop the reminder exactly when it is most useful.

    # Phase X.30 - partition out muted entries (per category) into `ignored`.
    ig = attention_ignore_map()
    ignored: list[dict[str, Any]] = []
    decide = _unmuted(b["decide"], "decide", ig, ignored)
    uncosted = _unmuted(uncosted, "uncosted", ig, ignored)
    unsubmitted = _unmuted(b["unsubmitted"], "unsubmitted", ig, ignored)
    unpaid = _unmuted(b["unpaid"], "unpaid", ig, ignored)
    deliver = _unmuted(b["deliver"], "deliver", ig, ignored)
    billing = _unmuted(_lpo_billing_due(), "billing", ig, ignored)
    # `decide` is ranked by how close it is to auto-loss, not by document date.
    decide.sort(key=lambda x: -x["days_open"])
    _stamp_attention_amounts(decide + uncosted + unsubmitted + unpaid + ignored)
    return {"ok": True, "scoped": scoped, "decide": decide, "uncosted": uncosted,
            "unsubmitted": unsubmitted, "unpaid": unpaid, "deliver": deliver,
            "billing": billing, "ignored": _oldest_first(ignored)}


def _stamp_attention_amounts(entries: list[dict[str, Any]]) -> None:
    """Give each invoice / quotation / delivery-note entry the money it is
    about (2026-10-21, Waleed) - the To-Do is a table now, and "which unpaid
    invoice is the big one" is the first question asked of it.

    `amount` + `currency` are what the document says, in ITS currency (an
    invoice issued in dinars shows dinars - a money number never travels
    without its currency); `amount_usd` is the same value in dollars, for
    sorting and for a tab's total. An invoice reads its stored totals; a
    quotation or delivery note sums what its lines bill (`_line_econ`
    revenue, USD). POs and LPOs carry no sell figure and get none. Reads only,
    one query per kind, and only for the documents in the feed."""
    want: dict[str, set[str]] = {}
    for e in entries:
        if e.get("kind") in ("inv", "quote", "dn"):
            want.setdefault(e["kind"], set()).add(e["doc_no"])
    if not want:
        return
    found: dict[tuple[str, str], tuple[float, str, float]] = {}

    def _marks(nos: set[str]) -> str:
        return ",".join("?" * len(nos))

    with _connect() as conn:
        if want.get("inv"):
            nos = want["inv"]
            for r in conn.execute(
                "SELECT inv_no, grand_total, sell_currency, sell_total FROM invoices "
                f"WHERE inv_no IN ({_marks(nos)})", tuple(nos)
            ).fetchall():
                usd = float(r["grand_total"] or 0)
                ccy = str(r["sell_currency"] or "USD").upper()
                native = r["sell_total"]
                if ccy == "IQD" and native is not None:
                    found[("inv", r["inv_no"])] = (float(native), "IQD", usd)
                else:
                    found[("inv", r["inv_no"])] = (usd, "USD", usd)
        for kind, table, no_col, type_col in (("quote", "quotes", "quote_no", "quote_type"),
                                              ("dn", "dns", "dn_no", None)):
            nos = want.get(kind)
            if not nos:
                continue
            cols = f"{no_col}, rows_json" + (f", {type_col}" if type_col else "")
            for r in conn.execute(
                f"SELECT {cols} FROM {table} WHERE {no_col} IN ({_marks(nos)})", tuple(nos)
            ).fetchall():
                try:
                    rows = json.loads(r["rows_json"] or "[]")
                except (TypeError, ValueError):
                    rows = []
                pk = _primary_key_for(kind, r[type_col] if type_col else "")
                total = sum(_line_econ(x, pk)["revenue"] for x in rows if isinstance(x, dict))
                if total > 0:
                    found[(kind, r[no_col])] = (total, "USD", total)
    for e in entries:
        hit = found.get((e.get("kind"), e.get("doc_no")))
        if hit:
            e["amount"], e["currency"], e["amount_usd"] = round(hit[0], 2), hit[1], round(hit[2], 2)


def agenda_feed() -> dict[str, Any]:
    """The launch card's agenda (2026-10-20): the four buckets that wait on
    someone - `unpaid`, `deliver`, `decide`, `unsubmitted` - exactly as
    `dashboard_needs_attention` returns them, but READ-ONLY and without the
    costly ones (`uncosted` parses every document's rows, `billing` every LPO).

    It runs in the launch card's own process while the app starts
    (backend/launch_gate.py), so it must never write: instead of running the
    14-day sweep it skips the quotes the sweep would mark lost. Measured on the
    live Abraj data: ~40 ms, against ~470 ms for the full feed. The buckets are
    built by the same `_attention_buckets` the dashboard uses, so the card and
    the To-Do cannot disagree; tests/gate_backend.test.py checks it on live data.
    """
    b = _attention_buckets(date.today(), swept=False)
    ig = attention_ignore_map()
    ignored: list[dict[str, Any]] = []
    out = {k: _unmuted(v, k, ig, ignored) for k, v in b.items()}
    out["decide"].sort(key=lambda x: -x["days_open"])
    return {"ok": True, **out}


def _decide_bucket(conn, today: date, *, swept: bool) -> list[dict[str, Any]]:
    """Bids to decide - SUBMITTED + open (not auto-lost yet), all months, aging
    from the submission date (that's when the bid clock starts).

    `swept`: the caller ran `auto_lose_stale_quotes` first, so no open quote
    is past the cutoff. A caller that must not write passes False and the
    quotes the sweep WOULD mark lost (stored 'open', >= QUOTE_STALE_DAYS) are
    skipped here instead - the same answer without the UPDATE."""
    won = _won_quote_nos(conn)
    sub = _submitted_at_map(conn, "quote")   # a quote is a bid only once submitted
    decide: list[dict[str, Any]] = []
    for r in conn.execute(
        "SELECT quote_no, quote_date, quote_type, client_name, "
        "COALESCE(bid_status,'open') AS status "
        "FROM quotes WHERE COALESCE(bid_status,'open') NOT IN ('won','lost') "
        "ORDER BY quote_date"
    ).fetchall():
        no = r["quote_no"]
        if no in won:
            continue                      # has invoice → effectively won
        sat = sub.get(no)
        if not sat:
            continue                      # unsubmitted → not a bid, not shown here
        d = _parse_doc_date(str(sat)[:10])
        if not d:
            continue
        days = (today - d).days
        if days < QUOTE_DECIDE_DAYS:
            continue                      # too fresh to nag
        if not swept and r["status"] == "open" and days >= QUOTE_STALE_DAYS:
            continue                      # the sweep would have marked it lost
        decide.append({
            "kind": "quote", "doc_no": no,
            "client": r["client_name"] or "",
            "date": r["quote_date"], "sub_type": r["quote_type"] or "",
            "days_open": days, "days_left": max(0, QUOTE_STALE_DAYS - days),
        })
    decide.sort(key=lambda x: -x["days_open"])
    return decide


def _attention_buckets(today: date, year: int | None = None, month: int | None = None,
                       *, swept: bool) -> dict[str, list[dict[str, Any]]]:
    """The four Needs-Attention buckets that wait on someone, before muting.
    Shared by `dashboard_needs_attention` and `agenda_feed`. Reads only."""
    with _connect() as conn:
        decide = _decide_bucket(conn, today, swept=swept)
    _oldest_first(decide)
    unsubmitted = list_unsubmitted(year, month)
    unpaid = list_unpaid()                        # money owed never expires
    deliver = pos_awaiting_invoice(year, month)   # client POs not yet invoiced

    # 2026-08-10 - a document that was never SUBMITTED is a draft, and a draft
    # cannot be overdue, awaiting an invoice, or distorting profit: nobody has
    # seen it. Chasing payment on an invoice that was never sent, or an invoice
    # for a PO that was never issued, is noise - and it was loud noise, because
    # drafts are old and every bucket sorts oldest-first, so they sat at the very
    # top. `decide` has always worked this way (an unsubmitted quote is not a
    # bid); these three now match it.
    #
    # Nothing disappears from the To-Do: each of these documents still appears
    # exactly once, under `unsubmitted`, which is the one action that actually
    # applies to it. THAT is what makes the filter safe - do not apply it to
    # `unsubmitted` itself, which would hide the work entirely.
    submitted_by_kind = {k: submitted_set(k) for k in _SUBMIT_KINDS}
    unpaid = _submitted_only(unpaid, submitted_by_kind)
    deliver = _submitted_only(deliver, submitted_by_kind)
    # The split is "does this wait on the other party?": unpaid and deliver do
    # and need the document to have gone out; uncosted (dashboard only) does not.
    return {"decide": decide, "unsubmitted": unsubmitted, "unpaid": unpaid, "deliver": deliver}


def _unmuted(entries: list[dict[str, Any]], category: str,
             ig: dict[str, set], ignored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phase X.30 - the entries NOT muted for `category`; muted ones go to
    `ignored`, stamped with their category so the To-Do can offer Restore."""
    muted = ig.get(category) or set()
    keep: list[dict[str, Any]] = []
    for e in entries:
        if (e["kind"], e["doc_no"]) in muted:
            ignored.append({**e, "category": category})
        else:
            keep.append(e)
    return keep


def _lpo_billing_due() -> list[dict[str, Any]]:
    """Rentals and supply contracts with a month that has gone unbilled.

    ONE entry per LPO, naming the oldest month and how many there are - a row
    per missing month would let a single forgotten contract fill the feed, and
    the mute key is (kind, doc_no), so muting would be all-or-nothing anyway.

    Never month-scoped: an unbilled month does not stop being unbilled when the
    calendar turns over, which is the lesson the other buckets already learned
    on 2026-08-10.

    Never raises. A broken LPO store must not take the whole To-Do down with it.
    """
    out: list[dict[str, Any]] = []
    try:
        rows = list_lpos("")
    except Exception:
        return out
    for row in rows:
        count = int(row.get("months_unbilled") or 0)
        if not count:
            continue
        oldest = row.get("oldest_due_month") or ""
        out.append({
            "kind": "lpo",
            "doc_no": row["lpo_no"],
            # `client`, not `client_name`: every other bucket uses that key and
            # the To-Do row reads `r.client`. Getting it wrong showed "-" where
            # the client should be (caught 2026-09-12).
            "client": row.get("client_name") or "",
            "client_name": row.get("client_name") or "",
            "date": row.get("lpo_date") or "",
            "lpo_kind": row.get("kind") or "materials",
            "months_due": count,
            "oldest_month": oldest,
            "note": (f"{count} month{'' if count == 1 else 's'} "
                     f"not invoiced, oldest {oldest}"),
        })
    return _oldest_first(out)


def list_docs_for_costing(year: int, month: int,
                          kinds: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Lightweight listing for the costing workspace: QUT estimates + INV actuals
    by default. DN costing data remains readable when explicitly requested so
    older records and deal-flow links keep working. Does NOT parse rows_json
    beyond the cheap costed flag - profit is computed when a doc is opened.
    """
    want = set(kinds or ["quote", "inv"])
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        won = _won_quote_nos(conn) if "quote" in want else set()
        if "quote" in want:
            for r in conn.execute(
                "SELECT quote_no, quote_date, quote_type, client_name, updated_at, "
                "bid_status, pdf_path, rows_json, extra_costs_json "
                "FROM quotes WHERE year=? AND month=? ORDER BY quote_no",
                (year, month),
            ).fetchall():
                e = dict(r)
                # Phase R - fully-costed flag (drives the Uncosted pill); drop
                # the heavy JSON from the returned entry.
                e.pop("extra_costs_json", None)
                e["costed"] = _costed_from_json(e.pop("rows_json", None),
                                                "quote", r["quote_type"])
                e["kind"] = "quote"; e["doc_no"] = e.pop("quote_no")
                e["date"] = e.pop("quote_date"); e["sub_type"] = e.pop("quote_type")
                # Phase N.3 - effective status folds in invoice linkage.
                # Phase V - and the 14-day stale-bid rule (open + old = lost).
                e["has_invoice"] = e["doc_no"] in won
                e["effective_status"] = effective_bid_status(
                    e.get("bid_status"), e["has_invoice"], e.get("date"))
                e["stale"] = (e["effective_status"] == "lost"
                              and (e.get("bid_status") or "open") != "lost"
                              and not e["has_invoice"])
                out.append(e)
        if "dn" in want:
            for r in conn.execute(
                "SELECT dn_no, dn_date, client_name, updated_at, source_quote_no, "
                "include_prices, pdf_path, rows_json, extra_costs_json "
                "FROM dns WHERE year=? AND month=? ORDER BY dn_no",
                (year, month),
            ).fetchall():
                e = dict(r)
                e.pop("extra_costs_json", None)
                e["costed"] = _costed_from_json(e.pop("rows_json", None), "dn", "")
                e["kind"] = "dn"; e["doc_no"] = e.pop("dn_no")
                e["date"] = e.pop("dn_date"); e["sub_type"] = ""
                out.append(e)
        if "inv" in want:
            for r in conn.execute(
                "SELECT inv_no, inv_date, inv_type, client_name, updated_at, "
                "source_quote_no, grand_total, pdf_path, rows_json, extra_costs_json "
                "FROM invoices WHERE year=? AND month=? ORDER BY inv_no",
                (year, month),
            ).fetchall():
                e = dict(r)
                e.pop("extra_costs_json", None)
                e["costed"] = _costed_from_json(e.pop("rows_json", None),
                                                "inv", r["inv_type"])
                e["kind"] = "inv"; e["doc_no"] = e.pop("inv_no")
                e["date"] = e.pop("inv_date"); e["sub_type"] = e.pop("inv_type")
                out.append(e)
    return out


def _costed_from_json(rows_json: str | None, kind: str = "quote",
                      sub_type: str = "") -> bool:
    """
    Phase R - whether a doc is FULLY costed (req 3): every billable line has a
    cost + usable qty. Drives the list/flow "Uncosted" pill, and matches
    _doc_costing_payload's rule so the pill never diverges from the detail view.
    """
    try:
        rows = json.loads(rows_json or "[]")
    except Exception:
        rows = []
    return _rows_fully_costed(rows, _primary_key_for(kind, sub_type))


# --------------------------------------------------------------------------- #
# Phase N.6 - deal linking. source_quote_no on dns/invoices is the SINGLE
# source of truth for a DN/INV's quote link (empty/NULL = unlinked). These
# helpers let the costing UI set that link and pick candidates to link.
# --------------------------------------------------------------------------- #

def set_doc_source_quote(kind: str, doc_no: str, quote_no: str) -> dict[str, Any]:
    """
    Set (or clear) a DN/INV's source_quote_no - the ONLY link state. An empty
    quote_no stores NULL (unlink). Returns {ok, kind, doc_no, source_quote_no}.
    """
    if kind not in ("dn", "inv"):
        return {"ok": False, "error": f"bad kind: {kind}"}
    sq = (quote_no or "").strip() or None
    table, pk_col = ("dns", "dn_no") if kind == "dn" else ("invoices", "inv_no")
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE {table} SET source_quote_no = ?, updated_at = ? WHERE {pk_col} = ?",
            (sq, now, doc_no),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": f"{kind} not found: {doc_no}"}
    return {"ok": True, "kind": kind, "doc_no": doc_no, "source_quote_no": (sq or "")}


def list_link_candidates(kind: str, client: str = "",
                         limit: int = 400) -> list[dict[str, Any]]:
    """
    Documents that can participate in a deal link. Scans ALL months (a long
    rental's invoices span months), newest doc number first.

      kind "dn"/"inv"  -> candidates to link UNDER a quotation (carry their
                          current source_quote_no, "" = unlinked).
      kind "quote"     -> candidates to link a DN/INV UP TO (Phase N.7 reverse
                          link); carry effective bid status instead.

    Each entry carries a `costed` flag; rows_json / extra_costs_json are
    consumed for the flag and never returned.
    """
    if kind not in ("dn", "inv", "quote"):
        return []
    client = (client or "").strip()
    if kind == "dn":
        sql = ("SELECT dn_no, dn_date, client_name, source_quote_no, pdf_path, "
               "rows_json, extra_costs_json FROM dns")
        no_col, date_col = "dn_no", "dn_date"
    elif kind == "inv":
        sql = ("SELECT inv_no, inv_date, inv_type, client_name, source_quote_no, "
               "pdf_path, rows_json, extra_costs_json FROM invoices")
        no_col, date_col = "inv_no", "inv_date"
    else:  # quote
        sql = ("SELECT quote_no, quote_date, quote_type, client_name, bid_status, "
               "pdf_path, rows_json, extra_costs_json FROM quotes")
        no_col, date_col = "quote_no", "quote_date"
    params: list[Any] = []
    if kind in ("inv", "quote"):
        sql += " WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
    client_sql, client_args = _client_match("client_name", client)
    if client_sql:
        sql += (" AND " if kind in ("inv", "quote") else " WHERE ")
        sql += client_sql
        params.extend(client_args)
    sql += f" ORDER BY {no_col} DESC LIMIT ?"
    params.append(limit)
    out: list[dict[str, Any]] = []
    with _client_key_fn(_connect()) as conn:
        won_set = _won_quote_nos(conn) if kind == "quote" else set()
        for r in conn.execute(sql, params).fetchall():
            entry = {
                "kind": kind,
                "doc_no": r[no_col],
                "date": r[date_col],
                "client_name": r["client_name"],
                "pdf_path": r["pdf_path"],
                "costed": _costed_from_json(
                    r["rows_json"], kind,
                    r["inv_type"] if kind == "inv"
                    else (r["quote_type"] if kind == "quote" else "")),
            }
            if kind == "quote":
                entry["bid_status"] = effective_bid_status(
                    r["bid_status"], r[no_col] in won_set, r["quote_date"])
            else:
                entry["source_quote_no"] = (r["source_quote_no"] or "")
            out.append(entry)
    return out


def list_quote_numbers(client: str = "", limit: int = 300) -> list[str]:
    """Quotation numbers (newest first), optionally filtered by client name."""
    client = (client or "").strip()
    sql = "SELECT quote_no FROM quotes WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
    params: list[Any] = []
    client_sql, client_args = _client_match("client_name", client)
    if client_sql:
        sql += f" AND {client_sql}"
        params.extend(client_args)
    sql += " ORDER BY quote_no DESC LIMIT ?"
    params.append(limit)
    with _client_key_fn(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [r["quote_no"] for r in rows]


def client_quotes(client_name: str = "", limit: int = 60) -> list[dict[str, Any]]:
    """One client's quotations, with enough on each to choose between them.

    `list_quote_numbers` answers a datalist and returns bare strings, which is
    no help when the question is "which of HDEC's eleven quotations is this PO
    answering?". This carries the date, the type, how many lines it has and
    what it adds up to, so the choice can be made from the list.

    The total is the SUM OF THE LINES, computed the way the quotation itself
    prints it, because `quotes` has no stored grand total - only `invoices`
    does. Rows whose quantities live in per-document custom columns (an
    equipment quotation such as QUT-EQ-HDEC-013) contribute 0 and are counted
    as unreadable, so a caller can say so instead of showing a confident 0.00.

    Identity-aware like every other client lookup here: an LPO typed under
    `HDEC` must find the quotations filed under the legal name.
    """
    client = (client_name or "").strip()
    if not client:
        return []
    sql = ("SELECT quote_no, quote_date, quote_type, client_name, rfq_no, rows_json "
           "FROM quotes WHERE TRIM(COALESCE(pdf_path,'')) <> ''")
    params: list[Any] = []
    client_sql, client_args = _client_match("client_name", client)
    if client_sql:
        sql += f" AND {client_sql}"
        params.extend(client_args)
    sql += " ORDER BY created_at DESC, quote_no DESC LIMIT ?"
    params.append(max(1, min(int(limit or 60), 300)))
    with _client_key_fn(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            lines = json.loads(r["rows_json"] or "[]")
        except Exception:
            lines = []
        total = 0.0
        readable = 0
        priced = 0
        for line in lines:
            if not isinstance(line, dict):
                continue
            if not (_link_primary(line) or line.get("qty")):
                continue
            readable += 1
            try:
                amount = float(line.get("qty") or 0) * float(
                    str(line.get("rate") or 0).replace(",", "") or 0)
            except (TypeError, ValueError):
                continue
            if amount:
                priced += 1
                total += amount
        out.append({
            "quote_no": r["quote_no"],
            "quote_date": r["quote_date"],
            "quote_type": r["quote_type"],
            "client_name": r["client_name"],
            "rfq_no": r["rfq_no"] or "",
            "lines": readable,
            # USD always: quotations sell in dollars (see `_fill_from`).
            "total": round(total, 2),
            "currency": "USD",
            # An equipment quotation keeps its quantities in per-document custom
            # columns, so nothing here can add it up. Say that, rather than
            # print a confident 0.00 next to a real quotation.
            "total_readable": priced > 0,
        })
    return out


def list_quote_refs(query: str = "", limit: int = 800) -> list[dict[str, Any]]:
    """Saved quotation references for picker UIs, newest first."""
    q = (query or "").strip()
    try:
        lim = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        lim = 800

    sql = (
        "SELECT quote_no, quote_date, quote_type, client_name, rfq_no, "
        "created_at, updated_at, pdf_path FROM quotes "
        "WHERE TRIM(COALESCE(pdf_path,'')) <> ''"
    )
    params: list[Any] = []
    if q:
        like = f"%{q}%"
        sql += (
            " AND (quote_no LIKE ? OR client_name LIKE ? OR rfq_no LIKE ? "
            "OR quote_type LIKE ?)"
        )
        params.extend([like, like, like, like])
    sql += " ORDER BY created_at DESC, quote_no DESC LIMIT ?"
    params.append(lim)

    type_codes = {"materials": "MT", "equipment": "EQ", "manpower": "MP", "service": "SV"}
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["type_code"] = type_codes.get((d.get("quote_type") or "").lower(), "")
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Phase R - cost memory: reuse a price you already paid for the same item.
# Indexes every previously-costed row across all documents and fuzzy-matches
# against the items in the doc you're costing now. Powers the "Fetch known
# costs" button and the inline "last costed" hint.
# --------------------------------------------------------------------------- #

def _norm_name(s: Any) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _tokens(s: Any) -> set[str]:
    """Alphanumeric word tokens of a string (for fuzzy overlap)."""
    out: set[str] = set()
    cur = ""
    for ch in str(s or "").lower():
        if ch.isalnum():
            cur += ch
        elif cur:
            out.add(cur); cur = ""
    if cur:
        out.add(cur)
    return out


def _all_cost_currencies(conn) -> dict[tuple[str, str], str]:
    rows = conn.execute("SELECT kind, doc_no, currency FROM doc_cost_meta").fetchall()
    return {(r["kind"], r["doc_no"]): (r["currency"] or "USD") for r in rows}


def _costed_catalog() -> list[dict[str, Any]]:
    """Every previously-costed line across all docs, with cost normalised to USD."""
    specs = [
        ("quote", "quotes",   "quote_no", "quote_date", "quote_type", None),
        ("dn",    "dns",      "dn_no",    "dn_date",    None,         "source_quote_no"),
        ("inv",   "invoices", "inv_no",   "inv_date",   "inv_type",   "source_quote_no"),
    ]
    out: list[dict[str, Any]] = []
    with _connect() as conn:
        cur_map = _all_cost_currencies(conn)
        # Resolve the cash deals once instead of per row (see doc_iqd_per_usd).
        eshraqat = config.ACTIVE_PROFILE == "eshraqat"
        roots, invs = (set(), set()) if eshraqat else _cash_deal_index(conn)
        for kind, table, no_col, date_col, sub_col, sq_col in specs:
            sub_sel = f", {sub_col}" if sub_col else ""
            sq_sel = f", {sq_col}" if sq_col else ""
            for r in conn.execute(
                f"SELECT {no_col}, {date_col}, rows_json{sub_sel}{sq_sel} FROM {table}"
            ).fetchall():
                sub = (r[sub_col] if sub_col else "") or ""
                pk = _primary_key_for(kind, sub)
                try:
                    rows = json.loads(r["rows_json"] or "[]")
                except Exception:
                    rows = []
                doc_no = r[no_col]
                currency = cur_map.get((kind, doc_no), "USD")
                if eshraqat:
                    rate = CASH_IQD_PER_USD
                else:
                    root = doc_no if kind == "quote" else (r[sq_col] if sq_col else None)
                    cash = (bool(root) and root in roots) or (kind == "inv" and doc_no in invs)
                    rate = CASH_IQD_PER_USD if cash else config.IQD_PER_USD
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    c = _coerce_cost(row.get("cost"))
                    if c == "":
                        continue
                    name = str(row.get(pk) or row.get("item")
                               or row.get("description") or "").strip()
                    if not name:
                        continue
                    out.append({
                        "kind": kind, "doc_no": doc_no, "date": r[date_col] or "",
                        "name": name,
                        "description": str(row.get("description") or "").strip(),
                        "cost_usd": round(cost_to_usd(c, currency, rate), 4),
                    })
    return out


def find_known_costs(items: list[dict] | None,
                     limit_per: int = 3) -> list[dict[str, Any]]:
    """
    For each input item {name, description}, find previously-costed lines that
    look like the same thing. Returns [{query, matches:[{name, description,
    cost_usd, kind, doc_no, date, score}]}]. Costs are USD; the caller converts
    to the current document's currency for display/fill.
    """
    items = items or []
    catalog = _costed_catalog()
    results: list[dict[str, Any]] = []
    for it in items:
        qname = _norm_name(it.get("name"))
        qn_toks = _tokens(it.get("name"))
        q_all = qn_toks | _tokens(it.get("description"))
        scored: list[tuple[float, dict]] = []
        if qname or q_all:
            for c in catalog:
                cn = _norm_name(c["name"])
                cn_toks = _tokens(c["name"])
                c_all = cn_toks | _tokens(c["description"])
                if qname and cn == qname:
                    score = 1.0
                else:
                    inter = len(q_all & c_all)
                    union = len(q_all | c_all)
                    score = (inter / union) if union else 0.0
                    if qn_toks and cn_toks and (qn_toks <= cn_toks or cn_toks <= qn_toks):
                        score = max(score, 0.7)   # one name contains the other
                if score >= 0.3:
                    scored.append((score, c))
        scored.sort(key=lambda x: (x[0], x[1]["date"]), reverse=True)
        seen: set = set()
        matches: list[dict] = []
        for sc, c in scored:
            key = (c["name"].lower(), c["cost_usd"])
            if key in seen:
                continue
            seen.add(key)
            matches.append({**c, "score": round(sc, 3)})
            if len(matches) >= max(1, limit_per):
                break
        results.append({
            "query": {"name": it.get("name", ""), "description": it.get("description", "")},
            "matches": matches,
        })
    return results


def deal_flow(kind: str, doc_no: str) -> dict[str, Any]:
    """
    Phase N.5 - resolve a document's "deal family" for the flow diagram:
    the root quotation plus every Delivery Note and Invoice that links back to
    it (source_quote_no). One quote can fan out to many DNs/INVs (e.g. a 6-month
    equipment rental → 6 invoices). Each node carries a `costed` flag and its
    profit totals. `linked` is False for an orphan DN/INV (no source quote).
    """
    # Resolve the root quote number.
    root_q = None
    if kind == "quote":
        root_q = doc_no
    elif kind == "dn":
        rec = load_dn(doc_no)
        root_q = (rec or {}).get("source_quote_no") or None
    elif kind == "inv":
        rec = load_inv(doc_no)
        root_q = (rec or {}).get("source_quote_no") or None

    def node(k: str, dno: str) -> dict[str, Any] | None:
        p = load_doc_costing(k, dno)
        if not p:
            return None
        return {
            "kind": k, "doc_no": dno,
            "client_name": p.get("client_name", ""),
            "date": p.get("date", ""),
            "sub_type": p.get("sub_type", ""),
            "costed": p.get("costed", False),
            "totals": p.get("totals", {}),
            "effective_status": p.get("effective_status"),
            "is_current": (k == kind and dno == doc_no),
        }

    if not root_q:
        # Orphan DN/INV (or a quote we couldn't load) - single, unlinked node.
        this = node(kind, doc_no)
        return {"linked": False, "root": this, "dns": [], "invs": [],
                "current": {"kind": kind, "doc_no": doc_no}}

    root = node("quote", root_q)
    dns, invs = [], []
    with _connect() as conn:
        for r in conn.execute(
            "SELECT dn_no FROM dns WHERE source_quote_no = ? ORDER BY dn_no", (root_q,)
        ).fetchall():
            n = node("dn", r["dn_no"])
            if n:
                dns.append(n)
        for r in conn.execute(
            "SELECT inv_no FROM invoices WHERE source_quote_no = ? "
            "AND TRIM(COALESCE(pdf_path,'')) <> '' ORDER BY inv_no", (root_q,)
        ).fetchall():
            n = node("inv", r["inv_no"])
            if n:
                invs.append(n)
    # "linked" means the deal actually branches (has at least one child doc).
    return {"linked": bool(dns or invs), "root": root, "dns": dns, "invs": invs,
            "current": {"kind": kind, "doc_no": doc_no}}


def profit_summary(year: int, month: int) -> dict[str, Any]:
    """
    Phase N.4 - aggregate one month's profit into REALIZED vs PIPELINE.

      realized = invoices (money actually billed)
      pipeline = OPEN quotes only - not yet invoiced and not marked lost.

    Won quotes are deliberately excluded from pipeline: their value is already
    counted via the linked invoice, so counting the quote too would double up.
    Lost quotes are excluded entirely. Returns grand totals + per-client and
    per-type breakdowns (each split realized/pipeline) + a win/lost tally.
    """
    docs = list_docs_for_costing(year, month, ["quote", "inv"])

    def blank() -> dict[str, float]:
        return {"revenue": 0.0, "cost": 0.0, "profit": 0.0, "count": 0}

    realized, pipeline = blank(), blank()
    by_client: dict[str, dict] = {}
    by_type:   dict[str, dict] = {}
    won = lost = open_ = 0

    for entry in docs:
        if not str(entry.get("pdf_path") or "").strip():
            continue                         # app-only pricing draft, not a deal yet
        kind = entry["kind"]
        if kind == "quote":
            eff = entry.get("effective_status", "open")
            if   eff == "won":  won  += 1
            elif eff == "lost": lost += 1
            else:               open_ += 1
            if eff != "open":
                continue                     # won → via invoice; lost → excluded
            bucket_name, grand = "pipeline", pipeline
        else:
            bucket_name, grand = "realized", realized

        payload = load_doc_costing(kind, entry["doc_no"])
        if not payload:
            continue
        t = payload["totals"]

        for b in (grand,):
            b["revenue"] += t["revenue"]; b["cost"] += t["cost"]
            b["profit"]  += t["profit"];  b["count"] += 1

        client = entry.get("client_name") or "-"
        sub    = entry.get("sub_type") or "-"
        for grpmap, key in ((by_client, client), (by_type, sub)):
            g  = grpmap.setdefault(key, {"realized": blank(), "pipeline": blank()})
            gb = g[bucket_name]
            gb["revenue"] += t["revenue"]; gb["cost"] += t["cost"]
            gb["profit"]  += t["profit"];  gb["count"] += 1

    def finalize(b: dict) -> dict:
        b["revenue"] = round(b["revenue"], 2)
        b["cost"]    = round(b["cost"], 2)
        b["profit"]  = round(b["profit"], 2)
        b["margin"]  = round((b["profit"] / b["revenue"] * 100.0) if b["revenue"] else 0.0, 1)
        return b

    finalize(realized); finalize(pipeline)

    def rows_from(grpmap: dict, key_name: str) -> list[dict]:
        rows = []
        for name, g in grpmap.items():
            finalize(g["realized"]); finalize(g["pipeline"])
            rows.append({key_name: name, "realized": g["realized"], "pipeline": g["pipeline"]})
        rows.sort(key=lambda x: -(x["realized"]["profit"] + x["pipeline"]["profit"]))
        return rows

    return {
        "year": year, "month": month,
        "realized": realized, "pipeline": pipeline,
        "by_client": rows_from(by_client, "client"),
        "by_type":   rows_from(by_type, "type"),
        "bids": {
            "won": won, "lost": lost, "open": open_,
            "total": won + lost + open_,
            "win_rate": round((won / (won + lost) * 100.0) if (won + lost) else 0.0, 1),
        },
    }


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _months_back(end_year: int, end_month: int, span: int) -> list[tuple[int, int]]:
    """The `span` (year, month) pairs ending at (end_year, end_month), oldest first."""
    y, m = int(end_year), int(end_month)
    seq: list[tuple[int, int]] = []
    for _ in range(max(1, int(span))):
        seq.append((y, m))
        m -= 1
        if m == 0:
            m = 12; y -= 1
    return list(reversed(seq))


def profit_trend(end_year: int, end_month: int, span: int = 12) -> dict[str, Any]:
    """
    Phase N.4 - profit across the last `span` months ending at (end_year,
    end_month). Per-month realized + pipeline profit for the trend chart, plus
    span totals, top clients by realized profit, per-type realized/pipeline,
    and a win/lost tally. Built by folding profit_summary over each month.
    """
    def blank() -> dict[str, float]:
        return {"revenue": 0.0, "cost": 0.0, "profit": 0.0, "count": 0}

    months: list[dict[str, Any]] = []
    tot_real, tot_pipe = blank(), blank()
    by_client: dict[str, float] = {}
    by_type: dict[str, dict[str, float]] = {}
    won = lost = open_ = 0

    for (y, m) in _months_back(end_year, end_month, span):
        s = profit_summary(y, m)
        r, p = s["realized"], s["pipeline"]
        months.append({
            "year": y, "month": m,
            "label": _MONTH_ABBR[m - 1] + (" '" + f"{y % 100:02d}" if m == 1 else ""),
            "realized_profit": r["profit"], "realized_revenue": r["revenue"],
            "realized_margin": r["margin"], "pipeline_profit": p["profit"],
        })
        for k in ("revenue", "cost", "profit", "count"):
            tot_real[k] += r[k]; tot_pipe[k] += p[k]
        b = s["bids"]; won += b["won"]; lost += b["lost"]; open_ += b["open"]
        for row in s["by_client"]:
            by_client[row["client"]] = by_client.get(row["client"], 0.0) + row["realized"]["profit"]
        for row in s["by_type"]:
            g = by_type.setdefault(row["type"], {"realized": 0.0, "pipeline": 0.0})
            g["realized"] += row["realized"]["profit"]
            g["pipeline"] += row["pipeline"]["profit"]

    def fin(b: dict) -> dict:
        b["revenue"] = round(b["revenue"], 2); b["cost"] = round(b["cost"], 2)
        b["profit"] = round(b["profit"], 2)
        b["margin"] = round((b["profit"] / b["revenue"] * 100.0) if b["revenue"] else 0.0, 1)
        return b
    fin(tot_real); fin(tot_pipe)

    clients = sorted(({"client": k, "profit": round(v, 2)} for k, v in by_client.items()),
                     key=lambda x: -x["profit"])[:8]
    types = [{"type": k, "realized": round(g["realized"], 2), "pipeline": round(g["pipeline"], 2)}
             for k, g in by_type.items()]
    types.sort(key=lambda x: -(x["realized"] + x["pipeline"]))

    return {
        "months": months,
        "realized": tot_real, "pipeline": tot_pipe,
        "by_client": clients, "by_type": types,
        "bids": {
            "won": won, "lost": lost, "open": open_,
            "win_rate": round((won / (won + lost) * 100.0) if (won + lost) else 0.0, 1),
        },
    }
