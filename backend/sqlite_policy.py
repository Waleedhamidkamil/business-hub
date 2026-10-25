"""The one way this app opens a SQLite file (multi-user, 2026-10-22).

WAL: readers never block the writer and the writer never blocks readers, so
several engines can share one company database. busy_timeout: a second WRITER
waits up to 30 s for the first instead of failing with "database is locked"
(a save holds the write lock for milliseconds). Both are set on every
connection; WAL is persistent in the file once set. A read-only URI cannot
change the journal mode, so it only gets the wait.

tests/sqlite_policy.test.py fails if any module in backend/ opens a database
with a raw sqlite3.connect() - that one connection would bring the error back.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 30_000


def open_db(path: str | Path, *, uri: bool = False, row_factory: bool = True,
            foreign_keys: bool = False, wal: bool = True,
            busy_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """`wal=False` and a short `busy_ms` are for MAINTENANCE opens only -
    restore staging files, migration locks, VACUUM - never for the live
    databases the engines share."""
    conn = sqlite3.connect(str(path), timeout=busy_ms / 1000, uri=uri)
    conn.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
    read_only = uri and "mode=ro" in str(path)
    if wal and not read_only:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
    if row_factory:
        conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


def snapshot(src: str | Path, dest: str | Path) -> None:
    """A consistent copy of a live WAL database - what backups must use.

    Zipping or copying the .db file alone can miss writes still in its -wal
    file."""
    source = open_db(src)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
