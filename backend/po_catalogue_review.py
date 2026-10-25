"""Persist which incomplete items have already been offered for review.

The state belongs to the DOCUMENT, so it lives in the existing per-profile
``doc_meta`` table and is removed automatically when that document is deleted.

Both save-time reviews use it (2026-09-10): a purchase order completing the
English half of its Arabic items, and a quotation completing the Arabic half of
its English ones. `doc_type` is the parameter that tells them apart - the rows
must not share a key, or reviewing a PO would silence a quotation.
"""
from __future__ import annotations

import json
from typing import Any

from . import database


_KEY = "catalogue_reviewed"


def _clean_keys(values: Any) -> list[str] | None:
    if not isinstance(values, list):
        return None
    return list(dict.fromkeys(str(value or "").strip() for value in values
                              if str(value or "").strip()))


def reviewed(po_no: str, doc_type: str = "po") -> list[str]:
    """Return stable row identities already handled for one document."""
    po_no = str(po_no or "").strip()
    doc_type = str(doc_type or "po").strip().lower() or "po"
    if not po_no:
        return []
    database._ensure_doc_meta_table()
    with database._connect() as conn:
        row = conn.execute(
            "SELECT value FROM doc_meta "
            "WHERE doc_type = ? AND doc_no = ? AND key = ?",
            (doc_type, po_no, _KEY),
        ).fetchone()
    if not row or not row["value"]:
        return []
    try:
        payload = json.loads(row["value"])
    except (TypeError, ValueError):
        return []
    return _clean_keys(payload) or []


def mark_reviewed(po_no: str, keys: Any, doc_type: str = "po") -> dict[str, Any]:
    """Remember presented rows, merging with earlier reviews for this document."""
    po_no = str(po_no or "").strip()
    doc_type = str(doc_type or "po").strip().lower() or "po"
    clean = _clean_keys(keys)
    if not po_no:
        return {"ok": False, "error": "no po_no"}
    if clean is None:
        return {"ok": False, "error": "keys must be a list"}

    database._ensure_doc_meta_table()
    with database._connect() as conn:
        row = conn.execute(
            "SELECT value FROM doc_meta "
            "WHERE doc_type = ? AND doc_no = ? AND key = ?",
            (doc_type, po_no, _KEY),
        ).fetchone()
        existing: list[str] = []
        if row and row["value"]:
            try:
                existing = _clean_keys(json.loads(row["value"])) or []
            except (TypeError, ValueError):
                existing = []
        merged = list(dict.fromkeys([*existing, *clean]))
        conn.execute(
            "INSERT INTO doc_meta(doc_type, doc_no, key, value) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(doc_type, doc_no, key) DO UPDATE SET value = excluded.value",
            (doc_type, po_no, _KEY, json.dumps(merged, ensure_ascii=False)),
        )
    return {"ok": True, "po_no": po_no, "doc_type": doc_type, "reviewed": merged}
