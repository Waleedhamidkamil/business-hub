"""Permanent document counters for profiles that use simple running numbers."""
from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterable

from . import config, sqlite_policy


_SPECS = {
    "po": ("PO", "pos", "po_no"),
    "quote": ("QT", "quotes", "quote_no"),
    "dn": ("DN", "dns", "dn_no"),
    "inv": ("IV", "invoices", "inv_no"),
}
_ALIASES = {
    "quotation": "quote",
    "invoice": "inv",
    "delivery": "dn",
    "delivery_note": "dn",
}
_LOCK = threading.RLock()


def enabled() -> bool:
    return config.ACTIVE_PROFILE == "eshraqat"


def _kind(value: str) -> str:
    normalized = _ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())
    if normalized not in _SPECS:
        raise ValueError(f"Unknown document kind: {value}")
    return normalized


def format_number(kind: str, value: int) -> str:
    normalized = _kind(kind)
    number = int(value)
    if number < 1:
        raise ValueError("Document numbers start at 1")
    return f"{_SPECS[normalized][0]}-{number:04d}"


def parse_number(kind: str, doc_no: str) -> int:
    normalized = _kind(kind)
    prefix = _SPECS[normalized][0]
    match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(doc_no or "").strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid {normalized} number: {doc_no}")
    value = int(match.group(1))
    if value < 1:
        raise ValueError(f"Invalid {normalized} number: {doc_no}")
    return value


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_policy.open_db(config.DB_PATH)
    return conn


def init_counters(conn: sqlite3.Connection | None = None) -> None:
    owns_connection = conn is None
    connection = conn or _connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_counters (
                kind       TEXT PRIMARY KEY,
                last_value INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _parsed_or_zero(kind: str, value: object) -> int:
    try:
        return parse_number(kind, str(value or ""))
    except (TypeError, ValueError):
        return 0


def _floor(connection: sqlite3.Connection, kind: str) -> int:
    normalized = _kind(kind)
    _, table, key = _SPECS[normalized]
    init_counters(connection)
    row = connection.execute(
        "SELECT last_value FROM document_counters WHERE kind = ?",
        (normalized,),
    ).fetchone()
    floor = int(row[0]) if row else 0

    try:
        rows = connection.execute(f'SELECT "{key}" FROM "{table}"').fetchall()
        floor = max([floor, *(_parsed_or_zero(normalized, row[0]) for row in rows)])
    except sqlite3.OperationalError:
        pass

    try:
        rows = connection.execute(
            "SELECT doc_no FROM deleted_docs WHERE kind = ?",
            (normalized,),
        ).fetchall()
        floor = max([floor, *(_parsed_or_zero(normalized, row[0]) for row in rows)])
    except sqlite3.OperationalError:
        pass
    return floor


def peek(kind: str) -> str:
    normalized = _kind(kind)
    with _LOCK:
        connection = _connect()
        try:
            return format_number(normalized, _floor(connection, normalized) + 1)
        finally:
            connection.close()


def next_value(kind: str) -> int:
    """The integer `peek` would format - what number_claims starts from."""
    normalized = _kind(kind)
    with _LOCK:
        connection = _connect()
        try:
            return _floor(connection, normalized) + 1
        finally:
            connection.close()


def peek_many(kind: str, count: int) -> list[str]:
    normalized = _kind(kind)
    size = int(count)
    if size < 1:
        raise ValueError("count must be at least 1")
    with _LOCK:
        connection = _connect()
        try:
            start = _floor(connection, normalized) + 1
            return [format_number(normalized, start + offset) for offset in range(size)]
        finally:
            connection.close()


def commit(kind: str, doc_numbers: str | Iterable[str]) -> dict:
    """Mark successfully saved numbers as consumed; repeated commits are safe."""
    normalized = _kind(kind)
    values = [doc_numbers] if isinstance(doc_numbers, str) else list(doc_numbers)
    if not values:
        raise ValueError("At least one document number is required")
    parsed = [parse_number(normalized, value) for value in values]
    if len(parsed) > 1 and sorted(parsed) != list(range(min(parsed), max(parsed) + 1)):
        raise ValueError("A document-number batch must be consecutive")

    with _LOCK:
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            init_counters(connection)
            last_value = max(_floor(connection, normalized), max(parsed))
            connection.execute(
                """
                INSERT INTO document_counters(kind, last_value)
                VALUES (?, ?)
                ON CONFLICT(kind) DO UPDATE SET
                    last_value = MAX(document_counters.last_value, excluded.last_value)
                """,
                (normalized, last_value),
            )
            connection.commit()
            return {"ok": True, "kind": normalized, "last_value": last_value}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
