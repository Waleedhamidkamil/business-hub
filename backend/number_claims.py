"""The one place document numbers are handed out (multi-user, 2026-10-22).

Before this, a NEW document's number was "the newest PDF + 1" (Abraj) or a
counter read before the save and bumped after it (Eshraqat). Either way two
saves that start together read the same number, and the second one overwrites
the first one's file and record. Here the read and the take are ONE
`BEGIN IMMEDIATE` transaction - SQLite lets one process hold it at a time - so
a number can be held by one save only.

Life of a claim, in the profile database's `number_claims` table:
    held    a save is running with this number
    used    the save finished (confirm) - never handed out again
    (gone)  the save failed (release), or the holder died: a held claim older
            than STALE_AFTER is forgotten, so its number is reused, no gap.

`holder` says who holds a number - the user, once there are users; "" until
then, so the single-user app never refuses itself.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

from . import config, sqlite_policy

STALE_AFTER = timedelta(minutes=10)


class NumberTaken(Exception):
    """Another live save holds this number."""

    def __init__(self, doc_no: str, holder: str) -> None:
        super().__init__(f"{doc_no} is being saved by {holder or 'someone else'}")
        self.doc_no = doc_no
        self.holder = holder


def _trailing_seq(doc_no: str) -> int | None:
    """The running number at the end of any of our formats: INV-MT-TDC-124,
    QUT-EQ-HDEC-007, DN-HDEC-066, A-142, IV-0004."""
    m = re.search(r"(\d+)\s*$", str(doc_no or ""))
    return int(m.group(1)) if m else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _begin(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("""CREATE TABLE IF NOT EXISTS number_claims (
        kind TEXT NOT NULL,
        doc_no TEXT NOT NULL,
        seq INTEGER,
        holder TEXT NOT NULL DEFAULT '',
        claimed_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'held',
        PRIMARY KEY (kind, doc_no))""")
    cutoff = (datetime.now() - STALE_AFTER).isoformat(timespec="seconds")
    conn.execute("DELETE FROM number_claims WHERE state = 'held' AND claimed_at < ?", (cutoff,))


def claim(kind: str, doc_no: str, *, holder: str = "", seq: int | None = None) -> None:
    """Hold a number the editor proposed or the user typed.

    A number belongs to whoever took it. Raises NumberTaken when ANOTHER holder
    holds it or already saved it - Ahmed's editor showing -124 must not
    overwrite the -124 Waleed saved a second ago. The same holder re-saving
    their own number is allowed, as the editor allows it today. (Before users
    exist every holder is "", so nothing changes for a single user.)"""
    if seq is None:
        seq = _trailing_seq(doc_no)
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        _begin(conn)
        row = conn.execute(
            "SELECT holder, state FROM number_claims WHERE kind = ? AND doc_no = ?",
            (kind, doc_no)).fetchone()
        if row and row["holder"] != holder:
            conn.rollback()
            raise NumberTaken(doc_no, row["holder"])
        conn.execute(
            """INSERT INTO number_claims(kind, doc_no, seq, holder, claimed_at, state)
               VALUES (?, ?, ?, ?, ?, 'held')
               ON CONFLICT(kind, doc_no) DO UPDATE SET
                   holder = excluded.holder,
                   claimed_at = excluded.claimed_at,
                   state = CASE WHEN number_claims.state = 'used' THEN 'used' ELSE 'held' END""",
            (kind, doc_no, seq, holder, _now()))
        conn.commit()
    finally:
        conn.close()


def claim_next(kind: str, existing_next: int, fmt: Callable[[int], str], *,
               holder: str = "") -> str:
    """Hold the first number at or after `existing_next` (what the files and
    counters say) that is past every number already claimed for this kind."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        _begin(conn)
        top = conn.execute("SELECT MAX(seq) FROM number_claims WHERE kind = ?", (kind,)).fetchone()[0]
        n = max(int(existing_next), int(top or 0) + 1)
        doc_no = fmt(n)
        # a number typed with an odd format can hold a row without a seq
        while conn.execute("SELECT 1 FROM number_claims WHERE kind = ? AND doc_no = ?",
                           (kind, doc_no)).fetchone():
            n += 1
            doc_no = fmt(n)
        conn.execute(
            """INSERT INTO number_claims(kind, doc_no, seq, holder, claimed_at, state)
               VALUES (?, ?, ?, ?, ?, 'held')""",
            (kind, doc_no, n, holder, _now()))
        conn.commit()
        return doc_no
    finally:
        conn.close()


def peek_next(kind: str, existing_next: int) -> int:
    """The number the editor should SHOW: past every live or used claim.

    Read-only, holds nothing. "Newest PDF + 1" alone misses documents saved
    without a PDF and saves still running."""
    try:
        conn = sqlite_policy.open_db(config.DB_PATH)
    except sqlite3.Error:
        return int(existing_next)
    try:
        cutoff = (datetime.now() - STALE_AFTER).isoformat(timespec="seconds")
        top = conn.execute(
            "SELECT MAX(seq) FROM number_claims WHERE kind = ? AND NOT (state = 'held' AND claimed_at < ?)",
            (kind, cutoff)).fetchone()[0]
    except sqlite3.OperationalError:          # no claims table yet
        top = None
    finally:
        conn.close()
    return max(int(existing_next), int(top or 0) + 1)


def confirm(kind: str, doc_no: str) -> None:
    """The save finished: the number is used for good."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        conn.execute("UPDATE number_claims SET state = 'used' WHERE kind = ? AND doc_no = ?",
                     (kind, doc_no))
        conn.commit()
    finally:
        conn.close()


def release(kind: str, doc_no: str, *, holder: str = "") -> None:
    """The save failed: free the number (only the holder's own, only if held)."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        conn.execute(
            "DELETE FROM number_claims WHERE kind = ? AND doc_no = ? AND state = 'held' AND holder = ?",
            (kind, doc_no, holder))
        conn.commit()
    finally:
        conn.close()
