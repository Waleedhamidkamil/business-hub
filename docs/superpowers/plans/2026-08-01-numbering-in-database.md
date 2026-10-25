# Document Numbering in the Database - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace filesystem-scanned document numbering with an atomic database counter, so two users can never receive the same document number.

**Architecture:** A `doc_counters` table holds the last issued integer per (profile, kind, sub_type). A migration seeds it from the current filesystem maximums so numbering continues exactly where it left off. `backend/database.py` gains three functions - peek (read), reserve (atomic increment), release (undo an unused reservation). `po_numbering.py` becomes the first consumer. Number *formatting* stays in each numbering module; only the integer moves.

**Tech Stack:** Python 3, SQLite (`sqlite3`), `unittest`. No new dependencies.

---

## Context an engineer needs before starting

**Read these first:**
- `docs/03-BACKEND-DATA.md` - owns `database.py` and `migrations.py`
- `docs/09-DATA-MODEL.md` - owns the schema documentation
- `docs/11-RUN-AND-MAINTAIN.md` - test protocol and the live-DB warning

**The problem being fixed.** All four numbering modules find the next number by
scanning PDF files on disk and adding one:

- `backend/po_numbering.py:44` - `folder.rglob("*.pdf")`, take max
- `backend/inv_numbering.py:68`, `backend/dn_numbering.py:71`,
  `backend/quote_numbering.py:70` - same shape

With one user this is fine. With two, both read the same maximum and both write
the same number.

**⚠️ TESTS RUN AGAINST THE LIVE DATABASE.** There is no test isolation in this
project. A test that ran `DELETE FROM invoices` once destroyed 17 real invoices.

Rules for every test in this plan:
- Use **sentinel** values that cannot collide with real data - this plan uses
  `kind="__test_counter__"`.
- Clean up by deleting **only** rows matching the sentinel.
- Never write a `DELETE`, `DROP` or `UPDATE` without a `WHERE` naming the sentinel.

**Running tests:** `python tests/<name>.test.py` - plain `unittest`, no pytest.
Test files are named `<name>.test.py` (a dot, not an underscore).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/database.py` (modify) | `doc_counters` schema + peek/reserve/release |
| `backend/migrations.py` (modify) | one-shot seed from filesystem maximums |
| `backend/po_numbering.py` (modify) | first consumer - reads the counter instead of scanning |
| `tests/doc_counters_backend.test.py` (create) | counter behaviour, including concurrency |
| `tests/doc_counters_seed_backend.test.py` (create) | migration seeds correctly and is idempotent |
| `docs/03-BACKEND-DATA.md`, `docs/09-DATA-MODEL.md` (modify) | same-change doc updates |

**Out of scope for this plan** (each gets its own plan):
- Converting `inv_numbering`, `dn_numbering`, `quote_numbering` - Plan 2
- Wiring `reserve_next_number` to editor-open in the web app - the web plan.
  This plan ships `reserve_next_number` fully tested but leaves the desktop app
  calling `peek_next_number`, so desktop behaviour is unchanged.

---

### Task 1: The `doc_counters` table

**Files:**
- Modify: `backend/database.py` (schema section, alongside the other `CREATE TABLE IF NOT EXISTS` statements)
- Test: `tests/doc_counters_backend.test.py`

- [ ] **Step 1: Write the failing test**

Create `tests/doc_counters_backend.test.py`:

```python
"""
Document numbers come from an atomic DB counter, not a folder scan (2026-08-01).

Every numbering module used to call `rglob("*.pdf")`, take the highest number
and add one. Two users doing that at the same moment both get the same number
and produce two different documents numbered alike. The counter makes the
increment atomic, so concurrent callers are guaranteed distinct numbers.

!! Runs against the LIVE profile DB. Every row it writes uses the sentinel kind
!! "__test_counter__" and only those rows are deleted. See the warning in
!! docs/11-RUN-AND-MAINTAIN.md.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import database  # noqa: E402

SENTINEL = "__test_counter__"


class DocCountersTable(unittest.TestCase):
    def tearDown(self):
        # ONLY the sentinel rows. Never widen this WHERE clause.
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_counters WHERE kind = ?", (SENTINEL,))
            conn.commit()

    def test_table_exists_with_expected_columns(self):
        with database._connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(doc_counters)")}
        self.assertEqual(
            cols,
            {"profile", "kind", "sub_type", "last_no", "updated_at"},
        )

    def test_primary_key_is_profile_kind_subtype(self):
        with database._connect() as conn:
            conn.execute(
                "INSERT INTO doc_counters(profile, kind, sub_type, last_no, updated_at)"
                " VALUES ('p', ?, '', 5, '2026-08-01')", (SENTINEL,))
            conn.commit()
            with self.assertRaises(Exception):
                conn.execute(
                    "INSERT INTO doc_counters(profile, kind, sub_type, last_no, updated_at)"
                    " VALUES ('p', ?, '', 9, '2026-08-01')", (SENTINEL,))
                conn.commit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/doc_counters_backend.test.py`
Expected: FAIL - `sqlite3.OperationalError: no such table: doc_counters`

- [ ] **Step 3: Add the table**

In `backend/database.py`, in the schema-creation function beside the other
`CREATE TABLE IF NOT EXISTS` statements, add:

```python
    # Document numbers (2026-08-01). The integer half of every document number
    # lives here so it can be handed out atomically; the *format* stays in each
    # numbering module. Replaces scanning folders for the highest PDF, which
    # gave two simultaneous users the same number.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS doc_counters (
            profile    TEXT    NOT NULL COLLATE NOCASE,
            kind       TEXT    NOT NULL COLLATE NOCASE,
            sub_type   TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
            last_no    INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT    NOT NULL,
            PRIMARY KEY (profile, kind, sub_type)
        )
    """)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/doc_counters_backend.test.py`
Expected: PASS - 2 tests

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/doc_counters_backend.test.py
git commit -m "feat(numbering): add doc_counters table"
```

---

### Task 2: peek, reserve, release

**Files:**
- Modify: `backend/database.py`
- Test: `tests/doc_counters_backend.test.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/doc_counters_backend.test.py`, above the `__main__` block:

```python
class CounterApi(unittest.TestCase):
    def tearDown(self):
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_counters WHERE kind = ?", (SENTINEL,))
            conn.commit()

    def test_peek_starts_at_one_and_does_not_consume(self):
        self.assertEqual(database.peek_next_number(SENTINEL), 1)
        self.assertEqual(database.peek_next_number(SENTINEL), 1,
                         "peek must not consume a number")

    def test_reserve_consumes(self):
        self.assertEqual(database.reserve_next_number(SENTINEL), 1)
        self.assertEqual(database.reserve_next_number(SENTINEL), 2)
        self.assertEqual(database.peek_next_number(SENTINEL), 3)

    def test_sub_types_count_separately(self):
        self.assertEqual(database.reserve_next_number(SENTINEL, "materials"), 1)
        self.assertEqual(database.reserve_next_number(SENTINEL, "services"), 1)
        self.assertEqual(database.reserve_next_number(SENTINEL, "materials"), 2)

    def test_release_undoes_the_highest_reservation(self):
        database.reserve_next_number(SENTINEL)          # 1
        n = database.reserve_next_number(SENTINEL)      # 2
        self.assertTrue(database.release_number(SENTINEL, "", n))
        self.assertEqual(database.peek_next_number(SENTINEL), 2,
                         "the released number must be handed out again")

    def test_release_refuses_when_not_the_highest(self):
        first = database.reserve_next_number(SENTINEL)  # 1
        database.reserve_next_number(SENTINEL)          # 2
        self.assertFalse(database.release_number(SENTINEL, "", first),
                         "releasing 1 while 2 exists would duplicate 2")
        self.assertEqual(database.peek_next_number(SENTINEL), 3)

    def test_concurrent_reservations_are_distinct(self):
        """The whole point. Twenty threads, twenty different numbers."""
        import threading
        got, errors, lock = [], [], threading.Lock()

        def grab():
            try:
                n = database.reserve_next_number(SENTINEL)
                with lock:
                    got.append(n)
            except Exception as e:            # noqa: BLE001 - reported below
                with lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=grab) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"reservations raised: {errors}")
        self.assertEqual(sorted(got), list(range(1, 21)),
                         f"duplicate or missing numbers: {sorted(got)}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/doc_counters_backend.test.py`
Expected: FAIL - `AttributeError: module 'backend.database' has no attribute 'peek_next_number'`

- [ ] **Step 3: Implement the three functions**

Add to `backend/database.py`, near the other document helpers:

```python
def _counter_key(kind: str, sub_type: str = "") -> tuple[str, str, str]:
    """Counters are per profile - Abraj and Eshraqat never share a sequence."""
    return (
        str(config.ACTIVE_PROFILE or "").strip().lower(),
        str(kind or "").strip().lower(),
        str(sub_type or "").strip().lower(),
    )


def peek_next_number(kind: str, sub_type: str = "") -> int:
    """The number that WOULD be issued next. Does not consume it.

    Safe for display. Never use this to actually number a saved document when
    more than one user is connected - use reserve_next_number.
    """
    profile, k, s = _counter_key(kind, sub_type)
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_no FROM doc_counters"
            " WHERE profile = ? AND kind = ? AND sub_type = ?",
            (profile, k, s),
        ).fetchone()
    return (int(row[0]) if row else 0) + 1


def reserve_next_number(kind: str, sub_type: str = "") -> int:
    """Atomically consume and return the next number.

    BEGIN IMMEDIATE takes the write lock before the SELECT, so two callers
    cannot both read the same last_no. Without it the read and the write are
    separate steps and the race the counter exists to prevent comes straight
    back.
    """
    profile, k, s = _counter_key(kind, sub_type)
    stamp = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_no FROM doc_counters"
            " WHERE profile = ? AND kind = ? AND sub_type = ?",
            (profile, k, s),
        ).fetchone()
        nxt = (int(row[0]) if row else 0) + 1
        conn.execute(
            "INSERT INTO doc_counters(profile, kind, sub_type, last_no, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(profile, kind, sub_type)"
            " DO UPDATE SET last_no = excluded.last_no,"
            "               updated_at = excluded.updated_at",
            (profile, k, s, nxt, stamp),
        )
        conn.commit()
    return nxt


def release_number(kind: str, sub_type: str, number: int) -> bool:
    """Give back a reserved number that was never used.

    Only succeeds if it is still the highest one issued. Releasing a number
    with others above it would hand the next caller a number already in use,
    so that case is refused and the number simply becomes a gap.
    """
    profile, k, s = _counter_key(kind, sub_type)
    stamp = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE doc_counters SET last_no = last_no - 1, updated_at = ?"
            " WHERE profile = ? AND kind = ? AND sub_type = ? AND last_no = ?",
            (stamp, profile, k, s, int(number)),
        )
        conn.commit()
        return cur.rowcount > 0
```

If `datetime` is not already imported in `database.py`, add `from datetime import datetime` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/doc_counters_backend.test.py`
Expected: PASS - 8 tests

If `test_concurrent_reservations_are_distinct` fails with
`sqlite3.OperationalError: database is locked`, raise the connection timeout in
`_connect()` (e.g. `sqlite3.connect(path, timeout=30)`). That is a real finding,
not a flaky test - it means writers queue rather than corrupt, which is correct.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/doc_counters_backend.test.py
git commit -m "feat(numbering): atomic peek/reserve/release on doc_counters"
```

---

### Task 3: Seed the counter from the current filesystem maximums

**Files:**
- Modify: `backend/migrations.py`
- Test: `tests/doc_counters_seed_backend.test.py`

The counter must start at the true current maximum, or the first document
issued after this change reuses an existing number.

- [ ] **Step 1: Write the failing test**

Create `tests/doc_counters_seed_backend.test.py`:

```python
"""
The counter must continue from the real last number, not restart at 1 (2026-08-01).

Seeding is the dangerous half of moving numbering into the database: if the
counter starts below the highest PO already on disk, the next PO reuses a number
that is already on a client's desk.

!! Runs against the LIVE profile DB. Read-only apart from the migration itself,
!! which is idempotent by design.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import config, database, migrations, po_numbering  # noqa: E402


class SeedsFromFilesystem(unittest.TestCase):
    def test_seed_matches_the_number_the_old_scan_would_give(self):
        """After seeding, peek must equal what the folder scan produced.

        Deliberately computed from the private scan helpers, NOT from
        get_next_po_number() - Task 4 makes that function read the counter, and
        comparing the counter to itself would pass no matter what the seed did.
        """
        from datetime import date
        today = date.today()
        expected_next = max(
            po_numbering._max_po_number_in_folder(po_numbering._month_folder(today)),
            po_numbering._max_po_number_in_folder(
                po_numbering._previous_month_folder(today)),
            po_numbering._max_deleted_po_number(),
        ) + 1
        migrations.run_all()
        self.assertEqual(database.peek_next_number("po"), expected_next)

    def test_running_twice_does_not_advance_the_counter(self):
        migrations.run_all()
        first = database.peek_next_number("po")
        migrations.run_all()
        self.assertEqual(database.peek_next_number("po"), first,
                         "the migration must be idempotent")

    def test_seed_is_recorded(self):
        migrations.run_all()
        with database._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM _migrations_applied WHERE key = ?",
                ("2026-08-01_doc_counters_seed",),
            ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/doc_counters_seed_backend.test.py`
Expected: FAIL - `peek_next_number("po")` returns 1, not the real next PO number

- [ ] **Step 3: Write the migration**

Append to `backend/migrations.py`:

```python
@migration("2026-08-01_doc_counters_seed")
def _seed_doc_counters(conn: sqlite3.Connection) -> str:
    """Seed doc_counters from the highest number currently on disk.

    Numbering moved from scanning PDF folders to an atomic DB counter. The
    counter has to start at the true maximum or the next document reuses a
    number already issued to a client. Deleted (tombstoned) numbers are
    included, because the old code refused to reuse them and so must this.

    Only PO is seeded here - the other three numbering modules move over in
    their own migration, and seeding a counter nothing reads yet would be
    misleading.
    """
    from datetime import date

    from . import po_numbering

    today = date.today()
    highest = max(
        po_numbering._max_po_number_in_folder(po_numbering._month_folder(today)),
        po_numbering._max_po_number_in_folder(
            po_numbering._previous_month_folder(today)),
        po_numbering._max_deleted_po_number(),
    )
    profile = str(config.ACTIVE_PROFILE or "").strip().lower()
    conn.execute(
        "INSERT INTO doc_counters(profile, kind, sub_type, last_no, updated_at)"
        " VALUES (?, 'po', '', ?, ?)"
        " ON CONFLICT(profile, kind, sub_type) DO NOTHING",
        (profile, highest, datetime.now().isoformat(timespec="seconds")),
    )
    return f"po counter seeded at {highest} for profile {profile!r}"
```

`ON CONFLICT ... DO NOTHING` is what makes a second run harmless even if the
`_migrations_applied` row were lost.

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/doc_counters_seed_backend.test.py`
Expected: PASS - 3 tests

- [ ] **Step 5: Commit**

```bash
git add backend/migrations.py tests/doc_counters_seed_backend.test.py
git commit -m "feat(numbering): seed doc_counters from filesystem maximums"
```

---

### Task 4: PO numbering reads the counter

**Files:**
- Modify: `backend/po_numbering.py:71-91` (`get_next_po_number`)
- Test: `tests/doc_counters_seed_backend.test.py`

Desktop behaviour must not change: `get_next_po_number` still *peeks*. Reserving
at editor-open belongs to the web plan, where the editor lifecycle can release
an abandoned number.

- [ ] **Step 1: Write the failing test**

Append to `tests/doc_counters_seed_backend.test.py`, above `__main__`:

```python
class PoNumberingUsesTheCounter(unittest.TestCase):
    def setUp(self):
        migrations.run_all()
        if config.ACTIVE_PROFILE == "eshraqat":
            self.skipTest("Eshraqat numbers come from profile_numbering")

    def test_po_number_matches_the_counter(self):
        po_no, _folder = po_numbering.get_next_po_number()
        n = int(po_no.rsplit("-", 1)[1])
        self.assertEqual(n, database.peek_next_number("po"))

    def test_asking_twice_gives_the_same_number(self):
        """peek semantics: looking does not consume."""
        first, _ = po_numbering.get_next_po_number()
        second, _ = po_numbering.get_next_po_number()
        self.assertEqual(first, second)

    def test_no_filesystem_scan_remains_in_the_number_path(self):
        """The scan is what made two users collide. It must be gone."""
        import inspect
        src = inspect.getsource(po_numbering.get_next_po_number)
        self.assertNotIn("_max_po_number_in_folder", src)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/doc_counters_seed_backend.test.py`
Expected: FAIL on `test_no_filesystem_scan_remains_in_the_number_path` -
`_max_po_number_in_folder` is still called

- [ ] **Step 3: Rewrite `get_next_po_number`**

Replace the body of `get_next_po_number` in `backend/po_numbering.py`:

```python
def get_next_po_number(today: date | None = None) -> tuple[str, Path]:
    """
    Return (po_no_str, target_folder).
    Target folder is the DAY folder <PO_ROOT>/<Year>/<MONTH>/<DD-MM-YYYY>/,
    created if missing.

    2026-08-01: the number now comes from the `doc_counters` table rather than
    scanning PDF folders for the highest one. The scan gave two simultaneous
    users the same number. `_max_po_number_in_folder` is kept - the seed
    migration and `list_pos_for_month` still use it - but it is no longer on
    the numbering path.
    """
    today = today or date.today()
    current_folder = folder_for_date(today)
    current_folder.mkdir(parents=True, exist_ok=True)

    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        return profile_numbering.peek("po"), current_folder

    from . import database
    next_n = database.peek_next_number("po")
    return f"{config.PO_PREFIX}-{next_n}", current_folder
```

- [ ] **Step 4: Run the full affected suite**

Run:
```bash
python tests/doc_counters_seed_backend.test.py
python tests/doc_counters_backend.test.py
python tests/po_pagination_backend.test.py
python tests/eshraqat_numbering_backend.test.py
```
Expected: all PASS. The last two guard against breaking existing numbering.

- [ ] **Step 5: Commit**

```bash
git add backend/po_numbering.py tests/doc_counters_seed_backend.test.py
git commit -m "feat(numbering): PO numbers come from doc_counters, not a folder scan"
```

---

### Task 5: Update the owning docs

**Files:**
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/09-DATA-MODEL.md`

Doc updates ship in the same change as the code - see the protocol in
`docs/11-RUN-AND-MAINTAIN.md`.

- [ ] **Step 1: Document the table**

In `docs/09-DATA-MODEL.md`, in the table list, add:

```markdown
### `doc_counters`

The integer half of every document number, one row per
(profile, kind, sub_type). Replaces scanning PDF folders for the highest
number, which handed two simultaneous users the same one.

| Column | Meaning |
|---|---|
| `profile` | `abraj` or `eshraqat` - the two never share a sequence |
| `kind` | `po`, `quote`, `dn`, `inv`, `ltr` |
| `sub_type` | e.g. `materials` / `services`, `''` when the kind has none |
| `last_no` | highest number issued so far |
| `updated_at` | ISO timestamp of the last change |

Seeded by migration `2026-08-01_doc_counters_seed` from the filesystem
maximums, including tombstoned numbers, so numbering continues unbroken.

**Currently seeded and read for `po` only.** The other kinds still scan the
filesystem until their own migration lands.
```

- [ ] **Step 2: Document the functions**

In `docs/03-BACKEND-DATA.md`, under the `database.py` function list, add:

```markdown
- `peek_next_number(kind, sub_type='')` - the number that would be issued next.
  Does not consume it. Safe for display; not safe as the number of a saved
  document once more than one user is connected.
- `reserve_next_number(kind, sub_type='')` - atomically consumes and returns the
  next number. Uses `BEGIN IMMEDIATE`, so concurrent callers are guaranteed
  distinct numbers.
- `release_number(kind, sub_type, number)` - gives back an unused reservation.
  Returns `False` and changes nothing if the number is no longer the highest,
  because releasing it would duplicate a number already issued.
```

- [ ] **Step 3: Note the impact**

In `docs/01-ARCHITECTURE.md`, in the "Impact Map - touch X, check Y" table, add:

```markdown
| `doc_counters` / any numbering module | Check the seed migration still covers every kind that reads the counter; a kind that reads it unseeded restarts at 1 and reuses issued numbers |
```

- [ ] **Step 4: Commit**

```bash
git add docs/01-ARCHITECTURE.md docs/03-BACKEND-DATA.md docs/09-DATA-MODEL.md
git commit -m "docs: doc_counters table and the numbering functions"
```

---

## Verification when the plan is complete

Run the whole suite, clearing the scratch tree first - several tests are
order-dependent and fail in back-to-back sweeps unless it is cleared:

```bash
rm -rf .test-work && for f in tests/*.test.py; do python "$f" || echo "FAILED $f"; done
```

Expected: no `FAILED` lines. Then launch the app (`python app.py`), open the PO
editor, and confirm the number shown matches the last PO on disk plus one.

## What this plan deliberately does not do

- **Does not convert invoice, DN or quotation numbering.** Plan 2. They still
  scan the filesystem, which is safe while only one user exists.
- **Does not reserve at editor-open.** `reserve_next_number` is built and tested
  but not yet called by the app; wiring it belongs with the web editor
  lifecycle, which needs a place to release an abandoned number.
- **Does not change Eshraqat numbering,** which routes through
  `profile_numbering` and is untouched.
