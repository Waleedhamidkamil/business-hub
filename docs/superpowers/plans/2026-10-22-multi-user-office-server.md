# Business Hub for three people - the plan

Written 2026-10-22 from the conversation with Waleed and a read of the code.
**Part A** is for Waleed: what we build, in what order, how each step is proven.
**Part B** is the step-by-step build of Phase 1 for whoever writes the code.
Later phases each get their own Part-B plan when the phase before them is done,
because what Phase 1 and 2 teach us changes the details of 3 to 7.

---

# PART A - the roadmap (plain words)

## What we are building

```
   Office network (routers in access-point mode, one network)

   ┌──────────────────────────────┐
   │  SERVER LAPTOP (cable)       │
   │  Windows 10 IoT LTSC         │
   │                              │
   │  Gatekeeper ── login, who is who, starts one engine per person
   │    ├─ Waleed's engine        │
   │    ├─ Accountant's engine    │      each engine = today's app logic,
   │    └─ Procurement's engine   │      private to one person
   │                              │
   │  Shared: databases (Abraj, Eshraqat, master data), PDFs, settings
   │  Nightly backup → external drive                              │
   └──────────────────────────────┘
          ▲            ▲            ▲
   Launcher app   Launcher app   Launcher app      (Wi-Fi is fine)
   on Waleed's    on accountant's on procurement's
   PC             laptop          laptop
   - own window and icon, smooth animations (runs on their PC)
   - opens / copies / prints PDFs, file pickers - on their PC
   - keeps a copy of the PDFs that person creates
```

**Update once, on the server - everyone has it the next time they open the app.**

## Decisions already made

| Topic | Decision |
|---|---|
| Server | Spare laptop, i3 / 8 GB / 256 GB SSD, network cable, Windows 10 IoT LTSC (same as your PC, so PDFs come out identical). Its battery covers power cuts. |
| Access | Office only. Nothing opened to the internet. |
| How people use it | A small launcher app on each PC; the screens come from the server. |
| Inside the server | One private engine per logged-in person (no lag or crash spreading between people). |
| Accounts | Login per person; settings live in the account, on the server. |
| Roles | Admin (you), Accountant, Procurement. Everyone may see costs and profit. |
| Company | The selected company (Abraj / Eshraqat) is each person's own setting. |
| Name on documents | Each person's name and title, per company, Arabic and English. "Prepared by" = whoever made it; "Signed by" only people you allow, signature added on approval; frozen once issued. |
| Master data | Shared. Everyone can add; editing and deleting per permission. Changes reach everyone at once. |
| Editing each other's documents | Allowed. |
| Issued documents | Locked; a change makes a new revision with history. |
| Deleting | Goes to a recycle bin; only you delete for good. |
| Local copies | Server saves first (it is the master), then a copy goes to the creator's PC. "Created by me" filter. |
| Rollout | One person first (accountant) for a week, then procurement. One-page guide for each. |
| Second admin | None. |

## What the code review found (why the order below matters)

1. **Two people could get the same document number.** Abraj numbers are worked
   out by looking at the newest PDF; the editor shows that number and the save
   uses it. Two people opening "New invoice" at the same time both see -124, and
   the second save **overwrites the first one's file and record**. Eshraqat has
   a counter, but its lock only works inside one running copy of the app.
2. **The databases are not set up for several writers.** They use SQLite's
   default mode. Two engines writing at once can fail with "database is locked".
3. **The backup could miss the newest work** once the database is in its
   multi-writer mode (the backup zips the database file directly).
4. **"Active company" is one file for the whole app** (`profile-registry.json`),
   and 4 modules reload the whole configuration when it changes.
5. **Five functions only make sense on the person's own PC** - open a PDF, show
   it in its folder, copy it, print it, and the file pickers - and several
   import screens send a path from the user's PC that the server cannot see.
6. **Name and signature on documents come from company settings**, not from a person.
7. 216 server functions and 330 call sites in the screens - so the launcher
   must pass calls through **without rewriting the screens**.

## The phases

Every phase ends with something you can see working, and nothing goes to the
other two people until Phase 7.

### Phase 0 - Prepare the server laptop  *(together, about half a day)*
- Clean install or clean-up of Windows 10 IoT LTSC; remove leftovers; never
  sleep; lid closed = do nothing; battery charge limit about 80%; Windows
  updates only at night.
- Network cable; check every router is in access-point (bridge) mode by
  comparing IP addresses at each desk.
- Python, Chrome/Edge, the Arabic fonts (copy from your PC so PDFs match).
- Antivirus: skip the data folder.
- External backup drive.
- **Proof:** a PDF made on the server is byte-for-byte the same layout as one
  made on your PC.

### Phase 1 - Make the app safe for several engines at once  *(your PC, 2-3 sessions)*
- Databases switched to multi-writer mode, with a wait instead of an error.
- **One place hands out document numbers** - no two saves can ever take the
  same number; if your number was taken a second ago, the app tells you and
  takes the next one.
- Backups take a proper snapshot, so they never miss the newest work.
- **Proof:** a test runs two engines saving invoices at the same instant, 100
  times, on a copy of your data - every number different, nothing lost.
  Today that test would fail.

### Phase 2 - Server, engines and the launcher  *(your PC, 4-6 sessions)*
- The gatekeeper (login screen for now with one account: you) and one engine per person.
- "Active company" becomes each person's own setting.
- The launcher: same screens, same animations, PDFs open / copy / print on
  your PC, file pickers send the file to the server.
- Updates: publish to the server; the launcher picks it up on next open.
- **Proof:** two launchers on your PC as two test users, one in Abraj and one
  in Eshraqat, working at the same time without touching each other.

### Phase 3 - Accounts, roles and the person on the document  *(3-4 sessions)*
- Users, passwords, the three roles; lock after inactivity; disable a user.
- Personal settings in the account (company, dark mode, To-Do, drafts).
- Name and title per company (Arabic + English), signature, "Prepared by" /
  "Signed by", approval before your signature appears, frozen once issued.
- **Proof:** the same invoice made by two test users shows each one's name;
  only you can add your signature.

### Phase 4 - Working together safely  *(4-5 sessions)*
- "Being edited by …" lock on open documents.
- Issued documents lock into revisions with history.
- Recycle bin; permanent delete for you only.
- "Created by" / "edited by" on every document; "Created by me" filter.
- Local copy of each person's PDFs on their own PC.
- Master data: who added / changed what; changes appear for everyone at once.
- Background jobs (the 14-day auto-lost rule, backups) run once on the server.
- Server clock decides every date and time.
- **Proof:** two test users try to edit the same invoice - the second sees who
  has it; an issued invoice edit creates revision 2 and keeps revision 1.

### Phase 5 - Your admin screen  *(2-3 sessions)*
- Who is online, which company, which screen or document.
- Activity log (who created, edited, deleted, marked paid - and when).
- Log someone out, restart their engine, lock a user.
- Errors with the person's name; optional "Report a problem" button.
- Publish an update; open sessions switch over only when they are done.

### Phase 6 - Move day  *(one evening + one morning)*
- Final backup of your PC; move all data to the server; old documents get
  "created by Waleed".
- Your PC keeps its current setup untouched for a few weeks as the way back.
- Nightly backup to the external drive, and **one real restore test**.
- Speed measured on the accountant's and procurement laptops at their desks.

### Phase 7 - Go live, one person at a time
- Week 1: you + accountant. Fix what comes up.
- Week 2: procurement joins.
- One-page guide for each: log in, switch company, what your role can do,
  where your PDFs are.

## Rough total

About **20-25 working sessions** before the accountant starts, plus the two
go-live weeks. The two biggest pieces are Phase 2 (the engines and launcher)
and Phase 4 (working together). The numbers move once Phase 1 is done - I will
re-estimate then rather than pretend to know now.

## Risks and how each is handled

| Risk | How it is handled |
|---|---|
| Two saves take one number | Phase 1, proven by the two-engine test before anything else is built. |
| Database corruption | Multi-writer mode + waits, laptop battery as UPS, antivirus exclusion, nightly backup, restore test. |
| An update breaks the app for everyone | Tested on your PC first (the tests never touch the server), published after hours, one-click way back to the previous version. |
| Server laptop off | Starts the engines by itself after a restart; the launcher shows "Server not reachable - Retry". Each person still has their own PDF copies. |
| A person's engine crashes | Only that person's engine restarts; you see the error with their name. |
| PDFs look different from the server | Phase 0 proof: same Windows edition, same fonts, compared before going further. |
| Somebody on the Wi-Fi who should not be | Login required for everything; locks after inactivity. |

## What I need from you, when we get there

- Phase 0: the laptop, the backup drive, and 30 minutes to check the routers.
- Phase 3: the names, titles (Arabic + English) and signatures for each person.
- Phase 7: the accountant's and procurement person's time for a short walk-through.

---

# PART B - Phase 1 in detail: safe for several engines at once

> For the coder. House rules from `CLAUDE.md` apply: read `AGENT-BOARD.md`
> first and claim a row; hold the **test lock** before running Python suites
> (they write the live database); invoke `systematic-debugging` before fixing
> anything that goes red; `verification-before-completion` before saying done;
> mutation-check every new test; update the owning doc in the same change; never
> an em dash. Executed inline (this repo deliberately does not use
> subagent-driven development). Commit only when Waleed asks.

**Goal:** Two copies of the backend running at the same time can save documents
into the same company without "database is locked", without ever sharing a
document number, and without a backup missing the newest work.

**Architecture:** One helper opens every SQLite file the same way (WAL journal
+ a 30-second busy wait). One module hands out document numbers inside a single
`BEGIN IMMEDIATE` transaction, so the check-and-take is atomic across
processes; every save that creates a NEW number goes through it. Backups copy
the database through SQLite's backup API instead of zipping the file.

**Tech stack:** Python 3.13, sqlite3 (stdlib), existing `backend/` modules,
plain-script tests in `tests/` (`python tests/x.test.py`).

**Files:**
- Create `backend/sqlite_policy.py` - the one way to open a database.
- Create `backend/number_claims.py` - the one place numbers are handed out.
- Modify every `sqlite3.connect(` in `backend/` (31 sites in 14 files:
  analytics, client_master, clients, data_manager x4, database, doc_verify,
  gate_brief x2, insights x2, master_data x5, master_doctor, profile_manager x8,
  profile_numbering, shared_library x2, signing).
- Modify `backend/api.py` - `save_inv` (~1700-1922), `save_quote` (~1196-1310),
  `save_dn` (~1526-1595), `save_po` (~3013-3075).
- Modify `backend/data_manager.py:448-500` - `create_backup` snapshots the DB.
- Modify `.gitignore` - WAL side files.
- Create `tests/sqlite_policy.test.py`, `tests/number_claims.test.py`,
  `tests/two_engines_one_number.test.py`.
- Docs: `docs/03-BACKEND-DATA.md` (connections, numbering), `docs/02-BACKEND-CORE.md`
  (api save flows), `docs/07-FRONTEND-SUPPORT.md` (data manager backup), `AGENT-BOARD.md`.

---

### Task 1: One way to open a database

**Files:** Create `backend/sqlite_policy.py`; Test `tests/sqlite_policy.test.py`

- [ ] **Step 1: Write the failing test**

```python
"""Every SQLite file is opened ONE way (multi-user, 2026-10-22).

Several engines will write the same company database at once. SQLite's default
journal makes a second writer fail at once with "database is locked"; WAL lets
readers and one writer work side by side, and busy_timeout makes a second writer
WAIT (up to 30 s) instead of failing. A connection opened any other way would
quietly bring the failure back - so the second check forbids raw
sqlite3.connect() anywhere in backend/ except this helper.
"""
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import sqlite_policy  # noqa: E402


class SqlitePolicy(unittest.TestCase):
    def test_opens_in_wal_with_a_busy_wait(self):
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite_policy.open_db(Path(td) / "x.db")
            try:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertGreaterEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
                self.assertIs(conn.row_factory, sqlite3.Row)
            finally:
                conn.close()

    def test_read_only_uri_still_opens(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ro.db"
            sqlite_policy.open_db(path).close()
            conn = sqlite_policy.open_db(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            finally:
                conn.close()

    def test_no_raw_connect_left_in_backend(self):
        offenders = []
        for py in (ROOT / "backend").glob("*.py"):
            if py.name == "sqlite_policy.py":
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bsqlite3\.connect\(", line):
                    offenders.append(f"{py.name}:{i}")
        self.assertEqual(offenders, [], "open databases through sqlite_policy.open_db")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it - expect ImportError (module missing)**

Run: `python tests/sqlite_policy.test.py`
Expected: `ModuleNotFoundError: No module named 'backend.sqlite_policy'`

- [ ] **Step 3: Write the helper**

```python
"""The one way this app opens a SQLite file (multi-user, 2026-10-22).

WAL: readers never block the writer and the writer never blocks readers, so
several engines can share one company database. busy_timeout: a second WRITER
waits up to 30 s for the first instead of failing with "database is locked"
(a save holds the write lock for milliseconds). Both are set on every
connection; WAL is persistent in the file once set. A read-only URI cannot
change the journal mode, so it only gets the wait.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BUSY_TIMEOUT_MS = 30_000


def open_db(path: str | Path, *, uri: bool = False, row_factory: bool = True,
            foreign_keys: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, uri=uri)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    read_only = uri and "mode=ro" in str(path)
    if not read_only:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
    if row_factory:
        conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


def snapshot(src: str | Path, dest: str | Path) -> None:
    """A consistent copy of a live WAL database (what backups must use).

    Zipping the .db file alone can miss writes still in the -wal file."""
    source = open_db(src)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
```

- [ ] **Step 4: Run - the first two pass, the third lists every raw connect**

Run: `python tests/sqlite_policy.test.py`
Expected: 2 OK, `test_no_raw_connect_left_in_backend` FAIL listing 31 sites.

- [ ] **Step 5: Replace each raw connect, keeping its exact behaviour**

For each site the test lists, swap the call and drop lines the helper now does.
Every variant that exists today, and its replacement:

```python
# database.py _connect (row_factory + foreign keys)
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
# becomes
conn = sqlite_policy.open_db(config.DB_PATH, foreign_keys=True)

# shared_library.connect (row_factory only)
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
# becomes
conn = sqlite_policy.open_db(db_path)

# a site that reads plain tuples (no row_factory line after it)
conn = sqlite3.connect(path)
# becomes
conn = sqlite_policy.open_db(path, row_factory=False)

# gate_brief read-only URI (keep uri=True)
sqlite3.connect(f"file:{p}?mode=ro", uri=True)
# becomes
sqlite_policy.open_db(f"file:{p}?mode=ro", uri=True, row_factory=False)
```

Add `from . import sqlite_policy` to each file's imports. Where a site later
sets `conn.row_factory = sqlite3.Row` itself, use the default (`row_factory=True`)
and delete that line. Read each site before changing it - several pass the
connection to helpers that index rows by NAME (need Row) and some by POSITION
(Row supports both, so `row_factory=True` is always safe; use `False` only to
keep a site byte-identical).

- [ ] **Step 6: Run - all three pass**

Run: `python tests/sqlite_policy.test.py` → `OK (3 tests)`

- [ ] **Step 7: Mutation check** - put one raw `sqlite3.connect(` back in
`backend/clients.py`, run, see the third test FAIL naming it; restore.

- [ ] **Step 8: WAL side files stay out of git** - append to `.gitignore`:

```
# SQLite WAL side files (multi-user, 2026-10-22). Checkpoint before committing data.
*.db-wal
*.db-shm
```

and add to `docs/11-RUN-AND-MAINTAIN.md` under data commits: run
`python -c "from backend import sqlite_policy as s, config; c=s.open_db(config.DB_PATH); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"`
for each `.db` before a "data:" commit, or the committed file lacks the newest rows.

### Task 2: Backups take a snapshot, not the raw file

**Files:** Modify `backend/data_manager.py:476-479`; Test: extend `tests/sqlite_policy.test.py`

- [ ] **Step 1: Failing test** - add to `SqlitePolicy`:

```python
    def test_backup_sees_rows_still_in_the_wal(self):
        import zipfile
        from backend import config, data_manager
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "live.db"
            keep = sqlite_policy.open_db(db)            # held open: no checkpoint on close
            keep.execute("CREATE TABLE t(x)")
            keep.execute("INSERT INTO t VALUES (42)")
            keep.commit()
            old = config.DB_PATH
            config.DB_PATH = db
            try:
                res = data_manager.create_backup(td)
            finally:
                config.DB_PATH = old
                keep.close()
            self.assertTrue(res["ok"], res)
            with zipfile.ZipFile(res["path"]) as zf:
                out = Path(td) / "restored.db"
                out.write_bytes(zf.read("profile/po_tool.db"))
            with sqlite3.connect(out) as c:
                self.assertEqual(c.execute("SELECT x FROM t").fetchone()[0], 42)
```

- [ ] **Step 2: Run - FAIL** (`no such table: t` - the zipped file lacks the WAL rows).
Check the key name `res["path"]` against `create_backup`'s return and adjust the test if it differs.

- [ ] **Step 3: Implement** - in `create_backup` replace

```python
            db_path = Path(config.DB_PATH)
            if db_path.exists():
                zf.write(db_path, arcname="profile/po_tool.db")
                files_added += 1
```

with

```python
            db_path = Path(config.DB_PATH)
            if db_path.exists():
                # A live WAL database is the .db file PLUS its -wal file; zipping
                # the .db alone can miss the newest saves (2026-10-22).
                snap = archive.with_suffix(".snapshot.db")
                sqlite_policy.snapshot(db_path, snap)
                try:
                    zf.write(snap, arcname="profile/po_tool.db")
                finally:
                    snap.unlink(missing_ok=True)
                files_added += 1
```

Apply the same to every other `.db` the backup zips (search `create_backup`
for `zf.write(` of a database path - the shared library).

- [ ] **Step 4: Run - PASS. Mutation:** restore the plain `zf.write(db_path, …)`, see FAIL, restore.

### Task 3: One place hands out document numbers

**Files:** Create `backend/number_claims.py`; Test `tests/number_claims.test.py`

- [ ] **Step 1: Failing test**

```python
"""One place hands out document numbers (multi-user, 2026-10-22).

The check ("is this number free?") and the take ("it is mine") happen in ONE
BEGIN IMMEDIATE transaction, which SQLite serialises across processes - so two
engines can never leave with the same number. A claim is 'held' while its save
runs and 'used' after; a held claim older than 10 minutes (an engine that died
mid-save) is forgotten so its number can be reused without a gap.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, number_claims, sqlite_policy  # noqa: E402


class NumberClaims(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.old = config.DB_PATH
        config.DB_PATH = Path(self.td.name) / "t.db"

    def tearDown(self):
        config.DB_PATH = self.old
        self.td.cleanup()

    def fmt(self, n):
        return f"INV-MT-TDC-{n:03d}"

    def test_next_goes_past_every_live_claim(self):
        a = number_claims.claim_next("inv", 124, self.fmt, holder="w")
        b = number_claims.claim_next("inv", 124, self.fmt, holder="a")
        self.assertEqual((a, b), ("INV-MT-TDC-124", "INV-MT-TDC-125"))

    def test_a_typed_number_held_by_someone_else_is_refused(self):
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")
        with self.assertRaises(number_claims.NumberTaken) as cm:
            number_claims.claim("inv", "INV-MT-TDC-124", holder="a")
        self.assertEqual(cm.exception.holder, "w")
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")   # same holder: fine

    def test_release_frees_the_number_without_a_gap(self):
        n = number_claims.claim_next("inv", 124, self.fmt, holder="w")
        number_claims.release("inv", n, holder="w")
        self.assertEqual(number_claims.claim_next("inv", 124, self.fmt, holder="a"), n)

    def test_a_dead_holders_claim_expires(self):
        n = number_claims.claim_next("inv", 124, self.fmt, holder="w")
        old = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
        with sqlite_policy.open_db(config.DB_PATH) as c:
            c.execute("UPDATE number_claims SET claimed_at=?", (old,))
        self.assertEqual(number_claims.claim_next("inv", 124, self.fmt, holder="a"), n)

    def test_used_numbers_are_never_handed_out_again(self):
        n = number_claims.claim_next("inv", 124, self.fmt, holder="w")
        number_claims.confirm("inv", n)
        self.assertEqual(number_claims.claim_next("inv", 124, self.fmt, holder="a"), self.fmt(125))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run - ImportError.** `python tests/number_claims.test.py`

- [ ] **Step 3: Implement**

```python
"""The one place document numbers are handed out (multi-user, 2026-10-22).

Before this, a NEW document's number was "the newest PDF + 1" (Abraj) or a
counter read before the save and bumped after it (Eshraqat). Either way two
saves that start together read the same number, and the second overwrites the
first. Here the read and the take are one BEGIN IMMEDIATE transaction - SQLite
lets only one process hold it at a time - so a number can be held by one save
only. `holder` names who holds it (the user, once there are users; "" until
then, so a single-user app never refuses itself).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from . import config, sqlite_policy

STALE_AFTER = timedelta(minutes=10)


class NumberTaken(Exception):
    def __init__(self, doc_no: str, holder: str) -> None:
        super().__init__(f"{doc_no} is being saved by {holder or 'someone else'}")
        self.doc_no = doc_no
        self.holder = holder


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _begin(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("""CREATE TABLE IF NOT EXISTS number_claims (
        kind TEXT NOT NULL, doc_no TEXT NOT NULL, seq INTEGER,
        holder TEXT NOT NULL DEFAULT '', claimed_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'held',
        PRIMARY KEY (kind, doc_no))""")
    cutoff = (datetime.now() - STALE_AFTER).isoformat(timespec="seconds")
    conn.execute("DELETE FROM number_claims WHERE state='held' AND claimed_at < ?", (cutoff,))


def claim(kind: str, doc_no: str, *, holder: str = "", seq: int | None = None) -> None:
    """Hold a number the editor proposed (or the user typed)."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        _begin(conn)
        row = conn.execute("SELECT holder, state FROM number_claims WHERE kind=? AND doc_no=?",
                           (kind, doc_no)).fetchone()
        if row and row["state"] == "held" and row["holder"] != holder:
            conn.rollback()
            raise NumberTaken(doc_no, row["holder"])
        conn.execute("""INSERT INTO number_claims(kind, doc_no, seq, holder, claimed_at, state)
                        VALUES (?,?,?,?,?,'held')
                        ON CONFLICT(kind, doc_no) DO UPDATE SET
                          holder=excluded.holder, claimed_at=excluded.claimed_at,
                          state=CASE WHEN number_claims.state='used' THEN 'used' ELSE 'held' END""",
                     (kind, doc_no, seq, holder, _now()))
        conn.commit()
    finally:
        conn.close()


def claim_next(kind: str, existing_next: int, fmt: Callable[[int], str], *,
               holder: str = "") -> str:
    """Hold the first number at or after `existing_next` that no live claim has."""
    conn = sqlite_policy.open_db(config.DB_PATH)
    try:
        _begin(conn)
        top = conn.execute("SELECT MAX(seq) FROM number_claims WHERE kind=?", (kind,)).fetchone()[0]
        n = max(int(existing_next), int(top or 0) + 1)
        doc_no = fmt(n)
        conn.execute("""INSERT INTO number_claims(kind, doc_no, seq, holder, claimed_at, state)
                        VALUES (?,?,?,?,?,'held')""", (kind, doc_no, n, holder, _now()))
        conn.commit()
        return doc_no
    finally:
        conn.close()


def confirm(kind: str, doc_no: str) -> None:
    with sqlite_policy.open_db(config.DB_PATH) as conn:
        conn.execute("UPDATE number_claims SET state='used' WHERE kind=? AND doc_no=?", (kind, doc_no))


def release(kind: str, doc_no: str, *, holder: str = "") -> None:
    with sqlite_policy.open_db(config.DB_PATH) as conn:
        conn.execute("DELETE FROM number_claims WHERE kind=? AND doc_no=? AND state='held' AND holder=?",
                     (kind, doc_no, holder))
```

- [ ] **Step 4: Run - 5 OK.** Then mutation: replace `BEGIN IMMEDIATE` with
`BEGIN` and run Task 6's two-engine test - it must go red (DEFERRED lets two
readers both see the same MAX); restore.

### Task 4: Every NEW-number save goes through the claims

**Files:** Modify `backend/api.py` (save_inv, save_quote, save_dn, save_po); `backend/profile_numbering.py`

- [ ] **Step 1: Helpers** - in `profile_numbering.py` add after `peek`:

```python
def next_value(kind: str) -> int:
    """The integer `peek` would format - for number_claims.claim_next."""
    normalized = _kind(kind)
    with _LOCK:
        connection = _connect()
        try:
            return _floor(connection, normalized) + 1
        finally:
            connection.close()
```

In `api.py`, near the other module helpers:

```python
def _claim_holder() -> str:
    """Who holds a number. '' until users exist (Phase 3 sets the user id)."""
    return os.environ.get("BUSINESSHUB_USER", "")


def _number_taken(exc: "number_claims.NumberTaken") -> dict:
    return {"ok": False, "number_taken": True, "doc_no": exc.doc_no,
            "error": f"{exc.doc_no} was just taken by {exc.holder or 'another save'}. "
                     "Save again to take the next number."}
```

and `from . import number_claims` in the imports.

- [ ] **Step 2: save_inv** - replace the three NEW branches (`elif profile_numbering.enabled():`,
`elif user_no:`, `else:`) shown at ~1716-1724:

```python
            elif profile_numbering.enabled():
                inv_no = number_claims.claim_next(
                    "inv", profile_numbering.next_value("inv"),
                    lambda n: profile_numbering.format_number("inv", n), holder=_claim_holder())
                claimed = inv_no
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
            elif user_no:
                try:
                    number_claims.claim("inv", user_no, holder=_claim_holder())
                except number_claims.NumberTaken as exc:
                    return _number_taken(exc)
                inv_no = claimed = user_no
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
            else:
                code = inv_numbering._type_code(inv_type)
                client = inv_numbering._sanitize_client(client_name)
                inv_no = number_claims.claim_next(
                    "inv", inv_numbering._global_max_number() + 1,
                    lambda n: f"{config.INV_PREFIX}-{code}-{client}-{n:03d}",
                    holder=_claim_holder())
                claimed = inv_no
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
```

Set `claimed = None` just before the `if existing_rec:` branch. Right before the
success `return {"ok": True, "inv_no": inv_no, …}` (~1912) add
`if claimed: number_claims.confirm("inv", claimed)`, and in the final
`except Exception as e:` (~1921) add, before its return:

```python
            if locals().get("claimed"):
                number_claims.release("inv", claimed, holder=_claim_holder())
```

- [ ] **Step 3: save_quote** - at ~1209 the number is recomputed on every save
and kept only for edits. Replace

```python
            quote_no, _, _ = quote_numbering.get_next_quote_number(client_name, quote_type)
```

with

```python
            claimed = None
            if not (quote.get("is_edit") and quote.get("quote_no")
                    and database.load_quote(quote["quote_no"])):
                if profile_numbering.enabled():
                    quote_no = number_claims.claim_next(
                        "quote", profile_numbering.next_value("quote"),
                        lambda n: profile_numbering.format_number("quote", n), holder=_claim_holder())
                else:
                    proto, _, nxt = quote_numbering.get_next_quote_number(client_name, quote_type)
                    stem = proto.rsplit("-", 1)[0]
                    quote_no = number_claims.claim_next(
                        "quote", nxt, lambda n: f"{stem}-{n:03d}", holder=_claim_holder())
                claimed = quote_no
            else:
                quote_no = quote["quote_no"]
```

**Read `quote_numbering.get_next_quote_number` first** and build `stem` the
way it builds the number (prefix, type code, client) - the two lines above
assume the trailing `-NNN` is the running number, which is true for the
current format `QUT-<TYPE>-<CLIENT>-<NNN>`; check the edit branch below still
assigns `quote_no` from the record exactly as it does today. Confirm before the
success return, release in the `except`, as in Step 2.

- [ ] **Step 4: save_dn** - same three-branch replacement as Step 2 at ~1535-1543,
kind `"dn"`; the Abraj branch uses `dn_numbering.get_next_dn_number(client_name)`
for `(proto, _, n)` and formats `f"{proto.rsplit('-', 1)[0]}-{n:03d}"` after
reading `dn_numbering` to confirm the trailing-number format. Confirm / release
as in Step 2.

- [ ] **Step 5: save_po** - the number comes from the editor. Before
`signing_overlay = signing.overlay_for_save(...)` add:

```python
            claimed = None
            if not database.load_po(po_no):            # a NEW number, not an overwrite
                try:
                    number_claims.claim("po", po_no, holder=_claim_holder())
                except number_claims.NumberTaken as exc:
                    return _number_taken(exc)
                claimed = po_no
```

(and for Eshraqat replace `po_no = profile_numbering.peek("po")` with a
`claim_next("po", profile_numbering.next_value("po"), …)` exactly as in Step 2).
Check `database.load_po` exists by that name (`grep -n "def load_po" backend/database.py`)
and use the real loader. Confirm / release as in Step 2. Apply to the second
PO save path at ~3140-3150 the same way.

- [ ] **Step 6: The editors handle `number_taken`.** In each editor's save
handler (`grep -n "api().save_inv\|api().save_quote\|api().save_dn\|api().save_po" static/*.js`),
before the generic error toast add:

```js
if (res && res.number_taken) {
    toast(res.error, true);
    await refreshNextNumber();   // the editor's existing "get next number" call
    return;
}
```

using each editor's existing next-number function (inv.js calls
`api().get_next_inv_number`, quote.js `get_next_quote_number`, dn.js
`get_next_dn_number`, app.js `get_next_po_number`). Pin it with a JS text test
that each of the four files handles `number_taken`.

- [ ] **Step 7: Run the existing numbering suites** under the test lock:
`python tests/eshraqat_numbering_backend.test.py`, `tests/incomplete_price_save_backend.test.py`,
`tests/client_must_be_on_file.test.py`, `python smoke_test.py`, `python smoke_quote.py`.
All must stay green. Any red: `systematic-debugging` first.

### Task 5: The two-engines test (the proof)

**Files:** Create `tests/two_engines_one_number.test.py`

- [ ] **Step 1: Write it** - two separate Python processes, each pointed at the
SAME isolated copy of the data (the `.test-work` pattern from
`tests/client_rename_isolation_backend.test.py`), each saving 50 app-only
invoices as fast as it can:

```python
"""Two engines, one company, one number each (multi-user, 2026-10-22).

The proof for Phase 1: two processes save 50 invoices each into ONE isolated
copy of the data at the same time. Every number must be different and all 100
records must exist. Before number_claims this failed - both processes read the
same "newest + 1" and the second save overwrote the first.
Never touches the live data: everything lives under .test-work/.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".test-work" / "two-engines"

WORKER = r'''
import importlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend import profile_manager
from backend.profile_manager import ProfileManager
m = ProfileManager(Path(sys.argv[2]), Path(sys.argv[1]) / "static")
profile_manager._MANAGER = m
from backend import config, database
importlib.reload(config); importlib.reload(database)
database.init_db()
from backend.api import Api
api, out = Api(), []
for i in range(50):
    r = api.save_inv({"is_edit": False, "date": "22/10/2026", "inv_type": "materials",
                      "client_name": "Two Engines", "items": [{"item": "Cement", "qty": 1}],
                      "save_without_pdf": True})
    out.append(r.get("inv_no") if r.get("ok") else "ERR:" + str(r.get("error")))
print(json.dumps(out))
'''


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    data = WORK / "data"
    sys.path.insert(0, str(ROOT))
    from backend.profile_manager import ProfileManager
    m = ProfileManager(data, ROOT / "static")
    m.create_registry("abraj")
    # seed: schema + the client the saves use (same setup the isolation suite does)
    seed = subprocess.run([sys.executable, "-c", WORKER.replace("range(50)", "range(0)") +
                           "\nfrom backend import clients; clients.add_client('Two Engines')",
                           str(ROOT), str(data)], capture_output=True, text=True)
    assert seed.returncode == 0, seed.stderr
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(ROOT), str(data)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=600)
        assert p.returncode == 0, err
        results += json.loads(out.strip().splitlines()[-1])
    errors = [r for r in results if r.startswith("ERR:")]
    assert not errors, f"{len(errors)} saves failed, first: {errors[0]}"
    assert len(set(results)) == 100, f"only {len(set(results))} distinct numbers for 100 saves"
    print("two engines: 100 saves, 100 distinct numbers, 0 errors")


if __name__ == "__main__":
    main()
```

Adjust the payload only if `save_inv` refuses it for a reason unrelated to
numbering (read the refusal; `_known_client_error` needs the client on file -
the seed adds it; check `clients.add_client`'s real name with
`grep -n "^def add_client" backend/clients.py`).

- [ ] **Step 2: Prove it fails on the OLD code** - `git stash` the Task 3-4
changes (keep Task 1-2), run, expect "only N distinct numbers" or overwrite
errors; `git stash pop`. Write the failing output into the AGENT-BOARD entry.

- [ ] **Step 3: Run on the new code** - expect
`two engines: 100 saves, 100 distinct numbers, 0 errors`. Run it 5 times; one
pass is not proof (CLAUDE.md, systematic debugging).

- [ ] **Step 4: Repeat for Eshraqat** - `m.create_registry("eshraqat")` in a
second run of the same test (parametrise `main(profile)` and call both).

### Task 6: Close the phase

- [ ] Full JS suite `for t in tests/*.test.js; do node "$t" || echo FAIL $t; done`
- [ ] Full PY suite under the test lock; every red compared against the known
list in `AGENT-BOARD.md` and, if new, debugged before anything else.
- [ ] `python smoke_test.py` and `python smoke_quote.py`.
- [ ] Docs: `docs/03-BACKEND-DATA.md` (sqlite_policy, number_claims, the
held/used/expired life of a claim), `docs/02-BACKEND-CORE.md` (the four save
flows, `number_taken` contract), `docs/07-FRONTEND-SUPPORT.md` (backup snapshot),
`docs/11-RUN-AND-MAINTAIN.md` (checkpoint before data commits), Impact Map row
in `docs/01-ARCHITECTURE.md`: "anything that creates a document number → goes
through number_claims".
- [ ] `AGENT-BOARD.md` entry with the before/after two-engine numbers; release the test lock.
- [ ] Show Waleed the two-engine proof, then re-estimate Phases 2-7.
