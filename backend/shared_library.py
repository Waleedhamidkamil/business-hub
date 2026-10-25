"""SQLite schema and copy helpers for cross-profile suggestion catalogs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from . import profile_manager, sqlite_policy


# The four document catalogs (items / quote_items / dn_items / inv_items) were
# retired and dropped on 2026-08-20 - the item record (`master_items`) is the
# only store. They are intentionally absent here so a fresh shared DB is never
# recreated with them.
SHARED_TABLES = (
    "companies",
    "clients",
    "column_memory",
)

# Tables that existed before 2026-08-20 and were deliberately dropped. Migration
# integrity checks must treat their absence as intended, not as data loss.
RETIRED_CATALOGS = ("items", "quote_items", "dn_items", "inv_items")

SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    name           TEXT PRIMARY KEY COLLATE NOCASE,
    use_count      INTEGER NOT NULL DEFAULT 0,
    last_used_at   TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    name           TEXT PRIMARY KEY COLLATE NOCASE,
    use_count      INTEGER NOT NULL DEFAULT 0,
    last_used_at   TEXT
);

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


# Folders already known to exist. `connect` is called for every shared read
# (hundreds of times on one hub visit before 2026-10-01), and a mkdir is a
# filesystem call each time; database._connect already skips it the same way.
_ensured_dirs: set[str] = set()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else profile_manager.shared_db_path()
    parent = str(db_path.parent)
    if parent not in _ensured_dirs:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _ensured_dirs.add(parent)
    return sqlite_policy.open_db(db_path)


# Columns added to an EXISTING shared table after it shipped. `CREATE TABLE IF
# NOT EXISTS` cannot add one, and the profile migration ledger does not cover
# this database, so each is applied here - PRAGMA-guarded and therefore safe to
# run on every launch. Declare the column in SHARED_SCHEMA as well, or a brand
# new shared DB gets it while an upgraded one does not (or vice versa).
_ADDED_COLUMNS = (
    # (the `items` catalog was dropped 2026-08-20 - nothing to patch here now)
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if have and column not in have:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.Error:
                pass


def init_shared_db(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SHARED_SCHEMA)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _source_has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


# Must stay identical to database._SIG_IGNORE - see the note there. Two copies
# of this function exist; if they disagree, copying a catalog between profiles
# re-splits every item it touches.
_SIG_IGNORE = {
    "qty", "rate", "cost", "cost_usd", "cost_ccy", "cost_qty", "cost_sell",
    "total", "image_refs", "money_ccy", "price_ccy",
    "cost_native", "cost_native_ccy",
    "price_native", "price_native_ccy", "item_no", "_index", "_no",
    # `img_query` was added to database._SIG_IGNORE and never mirrored here -
    # the two had already drifted, which is precisely the re-split the note
    # above warns about. `name_ar`/`name_en` land in both from the start.
    "img_query", "name_ar", "name_en",
}


def _data_sig(data: dict) -> str:
    normalized = {}
    for key, value in data.items():
        if str(key).strip().lower() in _SIG_IGNORE:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[str(key).strip().lower()] = text
    if not normalized:
        return "empty"
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _normalized_catalog_row(table: str, row: sqlite3.Row) -> dict:
    data = dict(row)
    if table not in set(RETIRED_CATALOGS):
        return data
    if str(data.get("data_sig") or "").strip():
        return data
    if table == "items":
        signature_data = {
            "description": data.get("description"),
            "unit": data.get("unit"),
            "last_company": data.get("last_company"),
        }
    else:
        try:
            signature_data = json.loads(data.get("data_json") or "{}")
        except (TypeError, ValueError):
            signature_data = {}
        if not isinstance(signature_data, dict):
            signature_data = {}
    data["data_sig"] = _data_sig(signature_data)
    return data


def copy_catalogs(source_db: Path, target_db: Path) -> dict[str, int]:
    """Copy the approved shared catalogs and return source row counts."""

    source_db = Path(source_db)
    target_db = Path(target_db)
    init_shared_db(target_db)
    counts: dict[str, int] = {}
    source = sqlite_policy.open_db(source_db, wal=False, row_factory=False)
    target = connect(target_db)
    try:
        source.row_factory = sqlite3.Row
        for table in SHARED_TABLES:
            if not _source_has_table(source, table):
                counts[table] = 0
                continue
            rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
            counts[table] = len(rows)
            if not rows:
                continue
            normalized_rows = [_normalized_catalog_row(table, row) for row in rows]
            keys = list(normalized_rows[0].keys())
            columns = ", ".join(f'"{key}"' for key in keys)
            marks = ", ".join("?" for _ in keys)
            target.executemany(
                f'INSERT OR REPLACE INTO "{table}" ({columns}) VALUES ({marks})',
                [tuple(row.get(key) for key in keys) for row in normalized_rows],
            )
        target.commit()
    finally:
        target.close()
        source.close()
    return counts
