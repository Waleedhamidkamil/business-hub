"""Has this SQLite database changed since I last looked? (2026-10-01)

WHY THIS EXISTS
---------------
Returning to the hub took about two seconds, and Waleed asked for it to be
fast WITHOUT ever showing out-of-date numbers. Measured on the live data:
`dashboard_needs_attention` took 1.4 s, and 1.3 s of that was
`client_master._connection()` re-running its whole setup routine (schema
script, legacy-name import scan, identity re-index, commit) on EVERY one of
359 client-name lookups. Nothing had changed between any of them.

The honest fix is not a timer ("cache for 30 seconds") - a timer is either too
short to help or long enough to show a stale number. It is to ask the database
file whether anything was committed since the answer was computed, and
recompute only when it was.

HOW
---
`version(path)` returns a fingerprint of the file, built from:
  * SQLite's own FILE CHANGE COUNTER - 4 bytes at offset 24 of the header,
    which SQLite increments on every committed write in rollback-journal mode
    (this app's mode: `journal_mode=delete`). It is the mechanism SQLite itself
    uses to tell whether its page cache is still valid;
  * the file's size and modification time, as a second, independent signal;
  * the `-wal` file's SIZE and header (checkpoint sequence + salts) - NOT its
    timestamp. The databases run in WAL mode since 2026-10-22 (multi-user,
    `sqlite_policy`): commits land in the -wal and the header counter is not
    maintained per commit. Every commit appends frames (the size grows), and
    when SQLite restarts the -wal from the top it changes the salts. SQLite
    also CREATES an empty -wal on every open and deletes it on the last
    close, with a new timestamp each time and nothing written - fingerprinting
    the timestamp turned every open into "changed" and every cache into a
    recompute (measured: 50 lookups, 50 setups). An empty or header-only
    -wal holds no commits and reads the same as no -wal.

A commit by ANY connection - this process, another window, a test run, the
other agent - changes the fingerprint. A read changes nothing.

It holds NO handle open. The first version kept one idle SQLite connection
per database (`PRAGMA data_version`); Windows will not delete or replace a
file another handle has open, and the test suite's scratch databases could no
longer be cleaned up. The same lock would have stopped anyone replacing a
database file while the app ran. Opening, reading 28 bytes and closing costs
tens of microseconds.

`version(path)` returns None when the file cannot be read. **None means
"unknown" and callers must treat it as "changed"**: a cache that cannot prove
it is current must recompute, never serve.

Callers must read the fingerprint BEFORE reading the data it guards. Then a
commit that lands in between can only cost one extra recompute next time; it
can never leave old data filed under the new fingerprint.
"""
from __future__ import annotations

import os
from typing import Optional

_MAGIC = b"SQLite format 3\x00"


def _stamp(path: str) -> Optional[str]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return "%d.%d" % (st.st_size, st.st_mtime_ns)


def version(path) -> Optional[str]:
    """A fingerprint that changes whenever the database file is committed to,
    or None if it cannot be read."""
    if path is None:
        return None
    path = str(path)
    stamp = _stamp(path)
    if stamp is None:
        return None
    try:
        with open(path, "rb") as f:
            header = f.read(28)
    except OSError:
        return None
    if len(header) >= 28 and header.startswith(_MAGIC):
        counter = int.from_bytes(header[24:28], "big")
    elif len(header) == 0:
        counter = 0               # created, nothing written yet - still a valid state
    else:
        return None               # not a database we can read: unknown, never "same"
    wal = _wal_mark(path + "-wal")
    return "%d|%s|%s" % (counter, stamp, wal)


def _wal_mark(wal_path: str) -> str:
    """What the -wal holds: its size and header, never its timestamp."""
    try:
        size = os.stat(wal_path).st_size
    except OSError:
        return "-"
    if size <= 32:                # no frames: nothing committed in it
        return "-"
    try:
        with open(wal_path, "rb") as f:
            header = f.read(32)
    except OSError:
        return "?%d" % size       # unreadable: still moves when the size does
    return "%d.%s" % (size, header[12:24].hex())   # checkpoint seq + salt-1 + salt-2
