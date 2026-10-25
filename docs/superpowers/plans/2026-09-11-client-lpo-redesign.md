# Client LPO Redesign Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan
> task by task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Do NOT use subagent-driven-development** - parallel agents against this repo's
> single live SQLite database is explicitly not adopted (see `CLAUDE.md`).

**Goal:** Turn the Client LPO screen from an unused filing cabinet into the
working centre of the quotation -> LPO -> DN -> invoice chain: it knows what the
client ordered, what is still owed, and what has to be billed this month.

**Architecture:** `client_lpos` grows a `kind` (`materials` | `rental` | `open`)
and a `rows_json` of ordered lines. Lines are INHERITED from the quotation the
LPO cites - never retyped - with manual entry as the fallback for a PO that
cites the client's own reference. DN and invoice attach to an LPO through a
picker instead of a free-typed string, and fulfilment is computed per line.

**Tech Stack:** Python 3 + SQLite (`backend/database.py`, `backend/migrations.py`,
`backend/api.py`), vanilla JS (`static/lpo.js`, `static/dn.js`, `static/inv.js`),
`unittest` for Python tests and node `vm.runInNewContext` harnesses for JS.

---

## Why this is being rebuilt

Measured on the live Abraj database on 2026-09-11:

- `client_lpos` holds **zero rows**. The feature shipped 2026-07-31 and has
  never been used once.
- Of 64 delivery notes, **63 carry `lpo_no = 'N/A'`**. One carries
  `CSSP-ADM-0010`. Two invoices carry `20260905 SHIP WATER` and
  `20260905 SHIP FOOD`, which are not LPO numbers at all.
- The LPO and the documents that consume it are joined by a string somebody has
  to retype identically. Nobody does, so `delivered` / `invoiced` / `closed`
  never light up.

And the single-total model fits only one of Waleed's three kinds of PO. From his
own files in `D:\ABRAJ\CSSP\LPO`:

| File | What it really is | What the app stores today |
|---|---|---|
| `LPO CSSP-ADM-0017` | 12 lines, fixed 273.40, one delivery | 273.40 - correct |
| `CSSP-RENT-0002` | 14 set-month @ 850, period from 2026-06-08, 7-month term, "may be terminated earlier" | 11,900 with no period and no monthly rate |
| `CSSP-OPEN-0001` | "Supply of Diesel (2,000 L/Month)" at unit price 0.77. **The printed "Total Amount USD 0.77" IS the unit price** | total = 0.77 |

That last row is a live defect: file `OPEN-0001` as written and the first diesel
invoice immediately shows **"Invoiced over LPO value"**, and `remaining_usd` is
nonsense from the first day.

## The decisions behind this plan (Waleed, 2026-09-11)

1. **Lines are inherited, not typed.** `CSSP-ADM-0010` cites
   `QTN Ref. No: QUT-MT-HDEC-029`, which we hold. Its four lines are our
   quotation's lines verbatim - same wording, same dimensions, same quantity,
   same rate - with our fifth line (3-drawer enclosed type, 7 @ 128) dropped.
   Their total 18,390 = our 19,286 minus exactly 896, the dropped line. Their
   "Certification" column says "Specifications identical to those in the
   attached quotation". So filing an LPO is: pick the quotation, untick what
   they dropped. Manual typing stays available for a PO that cites the client's
   own reference (`CSSP-RENT-0002` cites `QUT-146`, `CSSP-OPEN-0001` cites
   `QUT-03`; neither is ours, and both are one line anyway).
2. **A lines/total mismatch is reported in words and does not block the save.**
3. **Filing an LPO marks the cited quotation Won.** Today a quote only counts as
   won once invoiced, so the Decide list nags about orders already placed.
4. **DN and invoice pick their LPO from the client's open LPOs.** The number is
   still printed on the document; the link is no longer a hopeful string match.
5. **Closing is per kind, plus a manual close.** Materials close on quantity and
   value. Rental closes at period end or early when the client terminates. An
   open contract never auto-closes.
6. **Rental and open contracts raise a monthly billing reminder** in the existing
   Needs Attention feed. The app does not create the invoice itself.
7. **No per-LPO bank or withholding override.** Client master data already holds
   the receiving account and Waleed has one account per client (YAGNI).

## Reading order before starting

- `docs/03-BACKEND-DATA.md` - the `client_lpos` section and
  `dashboard_needs_attention`.
- `docs/07-FRONTEND-SUPPORT.md` - `static/lpo.js` and `static/todo.js`.
- `docs/14-UI-CONTRACTS.md` - the `Popup` contract. Every dialog here uses it.
- `backend/database.py:2122-2320` - the whole existing LPO block.
- `AGENT-BOARD.md` - claim a row before starting; hold the test lock.

## House rules that apply to every task

- **Never use an em dash (U+2014) anywhere.** `tests/no_em_dash.test.js` walks
  the repo and fails with file and line. Use a plain hyphen.
- **A test that cannot fail is not a test.** After writing one, break the code it
  covers and confirm it goes red before moving on. Each task below has an
  explicit mutation step.
- **Waleed is colour blind.** No status may be signalled by colour alone; every
  chip and warning carries a word.
- **Tests write to the LIVE shared library.** Never run two Python suites at
  once. Delete a stale `.test-work/` before a full run.

## File structure

| File | Responsibility after this plan |
|---|---|
| `backend/database.py` | `client_lpos` schema, `save_lpo`, `list_lpos`, `lpo_usage_map`, `_lpo_status`, new `lpo_fulfillment`, `close_lpo`, `_lpo_months_unbilled` |
| `backend/migrations.py` | one migration adding the new columns |
| `backend/api.py` | bridge methods, the client-on-file guard on `save_lpo`, `lpo_from_quote`, the `billing` bucket |
| `static/lpo.js` | the redesigned screen: list, per-kind rows, the new form, the viewer (unchanged) |
| `static/lpo_lines.js` (new) | the ordered-lines editor - tick, adjust qty, live total, mismatch note |
| `static/lpo_picker.js` (new) | the shared "attach this document to an LPO" picker |
| `static/dn.js` / `static/inv.js` | wire `#dnLpo` / `#invLpo` to the picker |
| `templates/index.html` | `lpoView` markup, the two new script tags |
| `static/app_styles.css` | styles for the new row layout and lines editor |

---

## Task 1: Schema and migration for the new LPO shape

**Files:**
- Modify: `backend/database.py:206-225` (the `client_lpos` CREATE TABLE)
- Modify: `backend/migrations.py` (append a new migration at the end)
- Test: `tests/client_lpo_schema.test.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""The LPO record must be able to describe all three kinds of PO Waleed gets.

One flat total describes a materials PO and lies about the other two -
CSSP-OPEN-0001 prints "Total Amount USD 0.77", which is the UNIT price for
diesel, not a ceiling. Filing it as a total made the first invoice read
"Invoiced over LPO value".
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

NEW_COLUMNS = {
    "kind", "rows_json", "period_start", "period_end", "monthly_amount",
    "closed_at", "close_reason", "erp_no", "project", "contact",
}


class ClientLpoSchema(unittest.TestCase):
    def columns(self, conn):
        return {r[1] for r in conn.execute("PRAGMA table_info(client_lpos)")}

    def test_a_fresh_database_has_every_new_column(self):
        from backend import database
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "fresh.db")
            conn.executescript(database._SCHEMA)
            missing = NEW_COLUMNS - self.columns(conn)
            self.assertEqual(set(), missing,
                             f"a new database is missing {sorted(missing)}")
            conn.close()

    def test_an_existing_database_is_migrated(self):
        """The live Abraj DB predates these columns and must gain them."""
        from backend import migrations
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "old.db")
            conn.executescript(
                "CREATE TABLE client_lpos ("
                " client_name TEXT NOT NULL COLLATE NOCASE,"
                " lpo_no TEXT NOT NULL COLLATE NOCASE, lpo_date TEXT,"
                " year INTEGER, month INTEGER, total REAL NOT NULL DEFAULT 0,"
                " currency TEXT NOT NULL DEFAULT 'USD',"
                " total_usd REAL NOT NULL DEFAULT 0, source_quote_no TEXT,"
                " file_path TEXT, ack_path TEXT, acknowledged_at TEXT,"
                " notes TEXT, received_at TEXT NOT NULL,"
                " updated_at TEXT NOT NULL,"
                " PRIMARY KEY (client_name, lpo_no))")
            conn.execute(
                "INSERT INTO client_lpos(client_name, lpo_no, total, currency,"
                " total_usd, received_at, updated_at)"
                " VALUES('HDEC','CSSP-ADM-0010',18390,'USD',18390,'x','x')")
            conn.commit()
            migrations._lpo_kinds_and_lines(conn)
            self.assertEqual(set(), NEW_COLUMNS - self.columns(conn))
            row = conn.execute("SELECT kind, rows_json FROM client_lpos").fetchone()
            self.assertEqual("materials", row[0],
                             "an LPO filed before kinds existed is a materials PO")
            self.assertIsNone(row[1], "no lines were known, so none are invented")
            conn.close()

    def test_the_migration_is_idempotent(self):
        """run_all records applied keys, but the function itself must be safe."""
        from backend import database, migrations
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "twice.db")
            conn.executescript(database._SCHEMA)
            migrations._lpo_kinds_and_lines(conn)
            migrations._lpo_kinds_and_lines(conn)     # must not raise
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_schema.test.py
```

Expected: FAIL - `module 'backend.migrations' has no attribute
'_lpo_kinds_and_lines'`, and the fresh-database case reports the missing columns.

`database._SCHEMA` is the schema string (`backend/database.py:23`, applied at
`:368`). It is private by name; the test reads it deliberately, because the
whole point is to check what a NEW database gets.

- [ ] **Step 3: Add the columns to the CREATE TABLE**

In `backend/database.py`, inside the `client_lpos` CREATE TABLE, after `notes TEXT,`:

```sql
    -- Which of the three kinds of PO this is (2026-09-11). One flat total
    -- describes a materials PO and lies about the other two: CSSP-OPEN-0001
    -- prints "Total Amount USD 0.77", which is the diesel UNIT price.
    --   materials - a one-off order. Closes when delivered and invoiced.
    --   rental    - runs for a period at monthly_amount. Closes at period_end
    --               or early, because their PO says it may be terminated.
    --   open      - a supply contract with no ceiling. Never auto-closes.
    kind            TEXT NOT NULL DEFAULT 'materials',
    -- What they ordered, inherited from the cited quotation (never retyped).
    -- Same shape as quotes.rows_json: [{item, description, unit, qty, rate}].
    -- NULL means the lines were never recorded, which is not the same as none.
    rows_json       TEXT,
    period_start    TEXT,                       -- rental / open
    period_end      TEXT,                       -- rental / open, blank = open ended
    monthly_amount  REAL,                       -- rental, in `currency`
    closed_at       TEXT,                       -- set by a manual close
    close_reason    TEXT,                       -- e.g. "terminated early"
    erp_no          TEXT,                       -- printed on every HDEC PO
    project         TEXT,                       -- e.g. CSSP
    contact         TEXT,                       -- their contact point for this PO
```

- [ ] **Step 4: Add the migration**

At the END of `backend/migrations.py`:

```python
@migration("2026-09-11_lpo_kinds_and_lines")
def _lpo_kinds_and_lines(conn: sqlite3.Connection) -> str:
    """Give `client_lpos` a kind, ordered lines, a period and a manual close.

    Every LPO filed before today is a materials PO by definition - kinds did not
    exist, so nobody could have filed a rental or an open contract as one. Lines
    stay NULL rather than empty: "we never recorded what they ordered" is not
    the same statement as "they ordered nothing".
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(client_lpos)")}
    added = []
    for name, decl in (
            ("kind", "TEXT NOT NULL DEFAULT 'materials'"),
            ("rows_json", "TEXT"),
            ("period_start", "TEXT"),
            ("period_end", "TEXT"),
            ("monthly_amount", "REAL"),
            ("closed_at", "TEXT"),
            ("close_reason", "TEXT"),
            ("erp_no", "TEXT"),
            ("project", "TEXT"),
            ("contact", "TEXT")):
        if name not in existing:
            conn.execute(f"ALTER TABLE client_lpos ADD COLUMN {name} {decl}")
            added.append(name)
    return f"added {len(added)} columns: {', '.join(added)}" if added else "already present"
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
python tests/client_lpo_schema.test.py
```

Expected: `Ran 3 tests ... OK`

- [ ] **Step 6: Mutate to prove the test can fail**

Temporarily remove `("rows_json", "TEXT"),` from the migration tuple, rerun, and
confirm `test_an_existing_database_is_migrated` goes red naming `rows_json`.
Put it back and rerun to green.

- [ ] **Step 7: Apply the migration to the real databases**

```bash
python -c "import sys; sys.path.insert(0,'.'); from backend import migrations; [print(r) for r in migrations.run_all() if r['status'] != 'skipped']"
```

Expected: one line reporting `2026-09-11_lpo_kinds_and_lines` `applied` with
`added 10 columns`.

- [ ] **Step 8: Commit**

```bash
git add backend/database.py backend/migrations.py tests/client_lpo_schema.test.py
git commit -m "feat: client_lpos can describe rentals and open supply contracts

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Per-kind status, manual close, and no false over-value warning

**Files:**
- Modify: `backend/database.py:2258` (`_lpo_status`) and `list_lpos`
- Test: `tests/client_lpo_status.test.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Where an LPO has reached depends on what kind of PO it is.

The old rule was one line: invoiced >= total means closed. That is right for a
materials PO and wrong for both others. A rental runs to its period end whatever
has been invoiced, and an open supply contract has no ceiling to reach at all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


def lpo(**kw):
    base = {"kind": "materials", "total_usd": 0.0, "invoiced_usd": 0.0,
            "dns": [], "invoices": [], "acknowledged": False,
            "period_start": "", "period_end": "", "closed_at": None}
    base.update(kw)
    return base


class LpoStatus(unittest.TestCase):
    def test_a_materials_po_still_closes_on_value(self):
        self.assertEqual("closed", database._lpo_status(
            lpo(total_usd=18390, invoiced_usd=18390, invoices=["INV-MT-HDEC-043"])))

    def test_a_materials_po_short_of_its_value_is_only_invoiced(self):
        self.assertEqual("invoiced", database._lpo_status(
            lpo(total_usd=18390, invoiced_usd=8100, invoices=["INV-MT-HDEC-043"])))

    def test_an_open_contract_never_closes_on_value(self):
        """CSSP-OPEN-0001's printed total is the unit price for diesel: 0.77.
        Invoicing one tank must not retire the contract."""
        self.assertNotEqual("closed", database._lpo_status(
            lpo(kind="open", total_usd=0.77, invoiced_usd=1240.0,
                invoices=["INV-MT-HDEC-070"])))

    def test_an_open_contract_closes_only_when_closed_by_hand(self):
        self.assertEqual("closed", database._lpo_status(
            lpo(kind="open", closed_at="2026-09-11T10:00:00")))

    def test_a_rental_runs_to_its_period_end(self):
        self.assertNotEqual("closed", database._lpo_status(
            lpo(kind="rental", total_usd=11900, invoiced_usd=11900,
                period_start="8/6/2026", period_end="8/1/2027",
                invoices=["INV-MT-HDEC-071"])),
            "a fully invoiced rental is still running until its period ends")

    def test_a_rental_past_its_period_end_is_closed(self):
        self.assertEqual("closed", database._lpo_status(
            lpo(kind="rental", period_start="8/1/2026", period_end="8/2/2026")))

    def test_an_early_termination_closes_a_running_rental(self):
        """Their PO says the term may be terminated earlier if necessary."""
        self.assertEqual("closed", database._lpo_status(
            lpo(kind="rental", period_start="8/6/2026", period_end="8/1/2027",
                closed_at="2026-09-11T10:00:00", close_reason="terminated early")))

    def test_the_ladder_below_closed_is_unchanged(self):
        self.assertEqual("received", database._lpo_status(lpo()))
        self.assertEqual("acknowledged", database._lpo_status(lpo(acknowledged=True)))
        self.assertEqual("delivered", database._lpo_status(
            lpo(acknowledged=True, dns=["DN-HDEC-046"])))


class OverValue(unittest.TestCase):
    def test_only_a_materials_po_with_a_real_total_can_be_over(self):
        self.assertTrue(database.lpo_is_over_value(
            lpo(total_usd=100, invoiced_usd=150)))
        self.assertFalse(database.lpo_is_over_value(
            lpo(kind="open", total_usd=0.77, invoiced_usd=1240)),
            "an open contract has no ceiling to exceed")
        self.assertFalse(database.lpo_is_over_value(
            lpo(total_usd=0, invoiced_usd=150)),
            "an unknown total is not a ceiling of zero")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_status.test.py
```

Expected: FAIL - `module 'backend.database' has no attribute 'lpo_is_over_value'`
plus assertion failures on the open and rental cases.

- [ ] **Step 3: Replace `_lpo_status` and add `lpo_is_over_value`**

```python
def _lpo_status(d: dict[str, Any]) -> str:
    """Where this LPO has reached: received -> acknowledged -> delivered ->
    invoiced -> closed. Highest reached wins.

    `closed` is per KIND (2026-09-11). It used to be one rule - invoiced value
    reached the total - which is right for a materials PO and wrong for the
    other two. CSSP-OPEN-0001 prints a total of 0.77, the unit price of a litre
    of diesel, so the old rule retired the contract on its first invoice. A
    rental runs to its period end however much has been billed, because the
    months are what was bought.
    """
    if d.get("closed_at"):
        return "closed"                 # closed by hand, whatever the numbers say
    kind = (d.get("kind") or "materials").strip().lower()
    if kind == "materials":
        total = float(d.get("total_usd") or 0)
        invoiced = float(d.get("invoiced_usd") or 0)
        if total > 0 and invoiced >= total - 0.01:
            return "closed"
    elif kind == "rental":
        end = _parse_doc_date(d.get("period_end") or "")
        if end and end < date.today():
            return "closed"
    # `open` never closes on its own - only the manual close above retires it.
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
    contract has none, and a total of 0 means "not recorded", not "zero".
    """
    if (d.get("kind") or "materials").strip().lower() != "materials":
        return False
    total = float(d.get("total_usd") or 0)
    if total <= 0:
        return False
    return float(d.get("invoiced_usd") or 0) > total + 0.01
```

Confirm `_parse_doc_date` and `date` are already imported in `database.py`
(`_parse_doc_date` is used by the stale-bid logic - see `docs/09-DATA-MODEL.md`).

- [ ] **Step 4: Have `list_lpos` carry the flag**

In `list_lpos`, after the line that sets `d["status"]`:

```python
        d["over_value"] = lpo_is_over_value(d)
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
python tests/client_lpo_status.test.py
```

Expected: `Ran 11 tests ... OK`

- [ ] **Step 6: Mutate**

Change `if kind == "materials":` to `if True:` and rerun. Confirm
`test_an_open_contract_never_closes_on_value` goes red. Revert and rerun green.

- [ ] **Step 7: Commit**

```bash
git add backend/database.py tests/client_lpo_status.test.py
git commit -m "fix: an open supply contract no longer closes on its first invoice

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Match an LPO to its documents by canonical client, not by spelling

**Files:**
- Modify: `backend/database.py:2223` (`_usage_key`)
- Test: `tests/client_lpo_usage_match.test.py` (create)

Today `_usage_key` lowercases the client name and compares strings.
`client_master` already resolves every spelling of a client to one canonical name
- that is the whole point of `client_aliases` - so an LPO filed under `HDEC` and
an invoice saved as `Hyundai Engineering` never meet.

- [ ] **Step 1: Write the failing test**

```python
"""An LPO and its invoices must meet even when the client was typed differently.

`client_aliases` is the single index that says HDEC and Hyundai Engineering are
one client. The LPO rollups were the last place still comparing raw strings.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


class UsageKey(unittest.TestCase):
    def test_two_spellings_of_one_client_share_a_key(self):
        def resolve(n):
            return "HDEC" if "hyundai" in n.lower() or n.strip().upper() == "HDEC" else n
        with patch("backend.client_master.canonical", side_effect=resolve):
            a = database._usage_key("HDEC", "CSSP-ADM-0010")
            b = database._usage_key("Hyundai Engineering", "CSSP-ADM-0010")
        self.assertEqual(a, b, "the alias index says these are the same client")

    def test_the_lpo_number_is_still_matched_case_insensitively(self):
        self.assertEqual(database._usage_key("TDC", "po-001"),
                         database._usage_key("TDC", "PO-001"))

    def test_two_clients_sending_the_same_number_stay_apart(self):
        self.assertNotEqual(database._usage_key("TDC", "PO-001"),
                            database._usage_key("HDEC", "PO-001"))

    def test_an_unreadable_client_store_falls_back_to_the_spelling(self):
        """A broken index must degrade to the old behaviour, never raise -
        `list_lpos` runs on every visit to the screen."""
        with patch("backend.client_master.canonical",
                   side_effect=RuntimeError("db is gone")):
            self.assertEqual(("hdec", "cssp-adm-0010"),
                             database._usage_key("HDEC", "CSSP-ADM-0010"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_usage_match.test.py
```

Expected: FAIL on `test_two_spellings_of_one_client_share_a_key`.

- [ ] **Step 3: Resolve through the alias index**

```python
def _usage_key(client_name: str, lpo_no: str) -> tuple[str, str]:
    """The key an LPO and its documents meet on.

    Resolved through `client_aliases` (2026-09-11), not compared as typed: an
    LPO filed under HDEC and an invoice saved as "Hyundai Engineering" are the
    same client, and the whole delivered/invoiced ladder depended on them
    matching. A broken client store falls back to the raw spelling rather than
    breaking the screen.
    """
    name = (client_name or "").strip()
    try:
        from . import client_master
        name = client_master.canonical(name) or name
    except Exception:
        pass                          # never block the LPO list on the client store
    return name.strip().lower(), (lpo_no or "").strip().lower()
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
python tests/client_lpo_usage_match.test.py
```

Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: Mutate**

Delete the body of the `try` block (leave `name` as typed), rerun, confirm the
first test goes red. Restore and rerun green.

- [ ] **Step 6: Check nothing else regressed**

```bash
python tests/client_lpo_backend.test.py
```

Expected: OK. If it fails, that suite compares raw names somewhere - read the
failure and fix the suite's expectation, do not weaken `_usage_key`.

- [ ] **Step 7: Commit**

```bash
git add backend/database.py tests/client_lpo_usage_match.test.py
git commit -m "fix: an LPO now finds its documents through the client alias index

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: `save_lpo` accepts the new fields and demands a client on file

**Files:**
- Modify: `backend/database.py:2131` (`save_lpo`)
- Modify: `backend/api.py:3418` (`Api.save_lpo`)
- Test: `tests/client_lpo_save.test.py` (create)

`api.save_quote` / `save_dn` / `save_inv` all call `_known_client_error` as their
first statement (see `tests/client_must_be_on_file.test.py`). `save_lpo` does
not, so an LPO can name a client no document is allowed to name. Its form also
feeds its datalist from the OLD `backend/clients.py`, not `client_master`.

- [ ] **Step 1: Write the failing test**

```python
"""Filing an LPO obeys the same client rule as every other save.

"no new clients shall be accepted when typing in client name in all editors"
(Waleed, 2026-09-11). The LPO form was the one save that still took any name.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))


class SaveLpoAsksAboutTheClient(unittest.TestCase):
    def test_save_lpo_calls_the_guard_and_acts_on_it(self):
        """Checked in the SOURCE, the way the three document saves are: other
        suites patch `_known_client_error` out, so removing the call could
        otherwise pass quietly."""
        source = (APP_ROOT / "backend" / "api.py").read_text(encoding="utf-8")
        start = source.index("def save_lpo(self")
        end = source.index("\n    def ", start + 10)
        body = source[start:end]
        self.assertIn("_known_client_error(", body,
                      "an LPO must not name a client that is not on file")
        self.assertIn("return refusal", body,
                      "save_lpo asks but does not act on the answer")


class SaveLpoStoresTheNewFields(unittest.TestCase):
    def test_the_signature_carries_every_new_field(self):
        import inspect
        from backend import database
        params = set(inspect.signature(database.save_lpo).parameters)
        for name in ("kind", "rows", "period_start", "period_end",
                     "monthly_amount", "erp_no", "project", "contact"):
            self.assertIn(name, params, f"save_lpo cannot store {name}")

    def test_an_unknown_kind_falls_back_to_materials(self):
        from backend import database
        self.assertEqual("materials", database._lpo_kind("nonsense"))
        self.assertEqual("rental", database._lpo_kind("RENTAL"))
        self.assertEqual("open", database._lpo_kind(" open "))
        self.assertEqual("materials", database._lpo_kind(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_save.test.py
```

Expected: FAIL on all three.

- [ ] **Step 3: Widen `database.save_lpo`**

Add the kind helper above `save_lpo`:

```python
_LPO_KINDS = ("materials", "rental", "open")


def _lpo_kind(value: Any) -> str:
    """Fall back to `materials` rather than storing a kind nothing understands."""
    kind = str(value or "").strip().lower()
    return kind if kind in _LPO_KINDS else "materials"
```

Extend `save_lpo`'s signature with these parameters after `notes`:

```python
             kind: str = "materials", rows: list | None = None,
             period_start: str = "", period_end: str = "",
             monthly_amount: float | None = None,
             erp_no: str = "", project: str = "", contact: str = "",
```

Inside, before the `with _connect()` block:

```python
    kind = _lpo_kind(kind)
    # NULL means "we never recorded what they ordered", which is a different
    # statement from "they ordered nothing" - keep them distinguishable.
    rows_json = json.dumps(rows, ensure_ascii=False) if rows is not None else None
    try:
        monthly = float(monthly_amount) if monthly_amount not in (None, "") else None
    except (TypeError, ValueError):
        monthly = None
```

Add the new columns to the INSERT column list, the VALUES placeholders, the
`DO UPDATE SET` list, and the argument tuple. The `DO UPDATE SET` additions:

```sql
                kind            = excluded.kind,
                rows_json       = excluded.rows_json,
                period_start    = excluded.period_start,
                period_end      = excluded.period_end,
                monthly_amount  = excluded.monthly_amount,
                erp_no          = excluded.erp_no,
                project         = excluded.project,
                contact         = excluded.contact,
```

`closed_at` and `close_reason` are deliberately NOT in the update list - they are
owned by `close_lpo` (Task 5), exactly as `file_path`/`ack_path` are owned by the
attach and acknowledge flows. Editing the header must never silently reopen a
closed contract.

- [ ] **Step 4: Wire the guard and the new fields in `Api.save_lpo`**

```python
    def save_lpo(self, lpo: dict) -> dict:
        """Create or update one received LPO (header details and ordered lines).

        Same client rule as save_quote / save_dn / save_inv: the name must be on
        file. An LPO naming a client no document may name would file the client's
        own paperwork under a client that does not exist.
        """
        refusal = _known_client_error(
            lpo.get("client_name", ""),
            database.load_lpo(lpo.get("client_name", ""), lpo.get("lpo_no", "")))
        if refusal:
            return refusal
        try:
            target = _parse_display_date(lpo.get("lpo_date")) or date.today()
            result = database.save_lpo(
                client_name=lpo.get("client_name", ""),
                lpo_no=lpo.get("lpo_no", ""),
                lpo_date=lpo.get("lpo_date") or _format_date(target),
                total=lpo.get("total") or 0,
                currency=lpo.get("currency") or "USD",
                source_quote_no=lpo.get("source_quote_no"),
                notes=lpo.get("notes") or "",
                kind=lpo.get("kind") or "materials",
                rows=lpo.get("rows"),
                period_start=lpo.get("period_start") or "",
                period_end=lpo.get("period_end") or "",
                monthly_amount=lpo.get("monthly_amount"),
                erp_no=lpo.get("erp_no") or "",
                project=lpo.get("project") or "",
                contact=lpo.get("contact") or "",
                year=target.year, month=target.month,
            )
            if result.get("ok"):
                self._win_quote_for_lpo(lpo.get("source_quote_no"))   # Task 10
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

`_win_quote_for_lpo` arrives in Task 10. Until then, add a temporary stub
beneath `save_lpo` so this task's tests run:

```python
    @staticmethod
    def _win_quote_for_lpo(quote_no: str | None) -> None:
        return                                    # implemented in Task 10
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
python tests/client_lpo_save.test.py
```

Expected: `Ran 3 tests ... OK`

- [ ] **Step 6: Mutate**

Comment out `if refusal: return refusal`, rerun, confirm
`test_save_lpo_calls_the_guard_and_acts_on_it` goes red. Restore.

- [ ] **Step 7: Confirm the existing LPO suite still passes**

```bash
python tests/client_lpo_backend.test.py
```

If it fails on the new client guard, that suite files LPOs for clients it never
registered. Register them in its `setUp` via `client_master.save_record(...)` -
the fix `shared_library_save_resilience_backend` took. Do NOT patch the guard
out: this suite's whole subject is the LPO save path.

- [ ] **Step 8: Commit**

```bash
git add backend/database.py backend/api.py tests/client_lpo_save.test.py tests/client_lpo_backend.test.py
git commit -m "feat: an LPO stores its kind, period and ordered lines, and its client must be on file

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Closing an LPO by hand

**Files:**
- Modify: `backend/database.py` (add `close_lpo` near `set_lpo_file`)
- Modify: `backend/api.py` (bridge method near `delete_lpo`)
- Test: `tests/client_lpo_close.test.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""A contract ends when the client says it ends.

CSSP-RENT-0002 carries "The contract term is seven months; however, please note
that it may be terminated earlier if necessary." Nothing the app can compute
tells it that happened, so there has to be a way to say so.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from backend import profile_manager, shared_library


class CloseLpo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        manager = profile_manager.ProfileManager(root / "data", APP_ROOT / "static")
        manager.create_registry("abraj")
        p = patch.object(profile_manager, "_MANAGER", manager); p.start(); self.addCleanup(p.stop)
        p = patch.object(profile_manager, "legacy_fallback_enabled", return_value=False)
        p.start(); self.addCleanup(p.stop)
        self.assertTrue(profile_manager.shared_db_path().resolve().is_relative_to(root.resolve()))
        shared_library.init_shared_db()
        from backend import database, client_master
        self.db = database
        client_master.save_record({"name": "HDEC"})
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-RENT-0002",
                          kind="rental", total=11900, currency="USD",
                          period_start="8/6/2026", period_end="8/1/2027")

    def current(self):
        return self.db.load_lpo("HDEC", "CSSP-RENT-0002")

    def test_a_running_rental_is_not_closed_until_it_is_closed(self):
        self.assertIsNone(self.current()["closed_at"])

    def test_closing_records_when_and_why(self):
        res = self.db.close_lpo("HDEC", "CSSP-RENT-0002", True, "terminated early")
        self.assertTrue(res["ok"])
        row = self.current()
        self.assertTrue(row["closed_at"], "a close must record when")
        self.assertEqual("terminated early", row["close_reason"])

    def test_reopening_clears_both(self):
        self.db.close_lpo("HDEC", "CSSP-RENT-0002", True, "terminated early")
        self.db.close_lpo("HDEC", "CSSP-RENT-0002", False)
        row = self.current()
        self.assertIsNone(row["closed_at"])
        self.assertIsNone(row["close_reason"],
                          "a reopened contract must not keep the reason it ended")

    def test_editing_the_header_does_not_reopen_a_closed_contract(self):
        """save_lpo owns the header; closed_at belongs to close_lpo alone -
        the same split file_path and ack_path already have."""
        self.db.close_lpo("HDEC", "CSSP-RENT-0002", True, "terminated early")
        self.db.save_lpo(client_name="HDEC", lpo_no="CSSP-RENT-0002",
                         kind="rental", total=12000, currency="USD")
        row = self.current()
        self.assertTrue(row["closed_at"], "editing the total reopened the contract")
        self.assertEqual(12000, row["total"], "the edit itself must still land")

    def test_closing_an_lpo_that_is_not_there_says_so(self):
        res = self.db.close_lpo("HDEC", "NOPE-0001", True)
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_close.test.py
```

Expected: FAIL - `module 'backend.database' has no attribute 'close_lpo'`.

- [ ] **Step 3: Implement `close_lpo`**

```python
def close_lpo(client_name: str, lpo_no: str, closed: bool = True,
              reason: str = "") -> dict[str, Any]:
    """Retire an LPO by hand, or put it back.

    Owned separately from `save_lpo` on purpose, the same way `file_path` and
    `ack_path` are: editing a rental's monthly rate must not silently reopen a
    contract the client terminated.
    """
    client_name, lpo_no = _lpo_key(client_name, lpo_no)
    now = datetime.now().isoformat(timespec="seconds")
    reason_value = ((reason or "").strip() or None) if closed else None
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE client_lpos SET closed_at = ?, close_reason = ?, updated_at = ? "
            "WHERE client_name = ? AND lpo_no = ?",
            (now if closed else None, reason_value, now, client_name, lpo_no))
        if not cur.rowcount:
            return {"ok": False, "error": f"no LPO {lpo_no} for {client_name}"}
    return {"ok": True, "closed": bool(closed)}
```

- [ ] **Step 4: Add the bridge method**

```python
    def close_lpo(self, client_name: str, lpo_no: str, closed: bool = True,
                  reason: str = "") -> dict:
        """Retire a contract by hand, or put it back."""
        return database.close_lpo(client_name, lpo_no, bool(closed), reason or "")
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
python tests/client_lpo_close.test.py
```

Expected: `Ran 5 tests ... OK`

- [ ] **Step 6: Mutate**

Add `closed_at = excluded.closed_at,` to `save_lpo`'s `DO UPDATE SET`, rerun,
confirm `test_editing_the_header_does_not_reopen_a_closed_contract` goes red.
Remove it and rerun green.

- [ ] **Step 7: Commit**

```bash
git add backend/database.py backend/api.py tests/client_lpo_close.test.py
git commit -m "feat: a rental can be closed early, the way their PO allows

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: What is still owed, line by line

**Files:**
- Modify: `backend/database.py` (add after `lpo_usage_map`)
- Modify: `backend/api.py` (bridge method)
- Test: `tests/client_lpo_fulfillment.test.py` (create)

Lines are matched on item plus description. That text came from our own
quotation on both sides of the comparison, and a document built from the LPO
copies it verbatim, so it matches by construction.

- [ ] **Step 1: Write the failing test**

```python
"""What is still owed on an LPO, line by line.

CSSP-ADM-0010 ordered 60 office desks. Deliver 40 and the app should say 20 are
still owed - today you would open the PDF and compare by eye.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database

ORDERED = [
    {"item": "Office desk", "description": "1650*850*800", "unit": "EA", "qty": 60, "rate": "135"},
    {"item": "Office chair", "description": "670*580*1050", "unit": "EA", "qty": 78, "rate": "75"},
]


class Fulfillment(unittest.TestCase):
    def test_nothing_delivered_means_everything_is_owed(self):
        out = database._fulfillment_from_rows(ORDERED, [], [])
        self.assertEqual([60, 78], [l["remaining"] for l in out["lines"]])
        self.assertFalse(out["complete"])

    def test_a_partial_delivery_is_counted(self):
        delivered = [[{"item": "Office desk", "description": "1650*850*800", "qty": 40}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(40, out["lines"][0]["delivered"])
        self.assertEqual(20, out["lines"][0]["remaining"])
        self.assertEqual(78, out["lines"][1]["remaining"])

    def test_deliveries_across_several_notes_add_up(self):
        delivered = [
            [{"item": "Office desk", "description": "1650*850*800", "qty": 40}],
            [{"item": "Office desk", "description": "1650*850*800", "qty": 20}],
        ]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(60, out["lines"][0]["delivered"])
        self.assertEqual(0, out["lines"][0]["remaining"])

    def test_spacing_and_case_do_not_break_the_match(self):
        delivered = [[{"item": "  office DESK ", "description": "1650*850*800", "qty": 60}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(60, out["lines"][0]["delivered"])

    def test_a_line_the_lpo_never_ordered_is_reported_not_swallowed(self):
        """A DN carrying something the LPO never ordered is worth naming."""
        delivered = [[{"item": "Filing cabinet", "description": "x", "qty": 2}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(["Filing cabinet"], [e["item"] for e in out["extra"]])

    def test_complete_only_when_every_line_is_delivered_and_invoiced(self):
        both = [[{"item": "Office desk", "description": "1650*850*800", "qty": 60},
                 {"item": "Office chair", "description": "670*580*1050", "qty": 78}]]
        self.assertFalse(database._fulfillment_from_rows(ORDERED, both, [])["complete"])
        self.assertTrue(database._fulfillment_from_rows(ORDERED, both, both)["complete"])

    def test_an_lpo_with_no_recorded_lines_reports_nothing_rather_than_zero(self):
        """rows_json NULL means we never recorded the order. Reporting
        "0 of 0 delivered, complete" would be a confident lie."""
        out = database._fulfillment_from_rows(None, [], [])
        self.assertEqual([], out["lines"])
        self.assertIsNone(out["complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_fulfillment.test.py
```

Expected: FAIL - no attribute `_fulfillment_from_rows`.

- [ ] **Step 3: Implement**

```python
def _line_key(row: dict[str, Any]) -> tuple[str, str]:
    """How an ordered line is recognised on a delivery note or invoice.

    Item plus description, squashed. Both sides of this comparison came from the
    same quotation text - their PO's Certification column says "Specifications
    identical to those in the attached quotation" - and a document built from
    the LPO copies the text verbatim, so it matches by construction.
    """
    def squash(v: Any) -> str:
        return " ".join(str(v or "").split()).strip().lower()
    return squash(row.get("item")), squash(row.get("description"))


def _fulfillment_from_rows(ordered: list | None,
                           delivered_docs: list[list],
                           invoiced_docs: list[list]) -> dict[str, Any]:
    """Per-line ordered / delivered / invoiced / remaining.

    `ordered=None` means the order was never recorded. That is not the same as
    an empty order, so `complete` comes back None rather than True.
    """
    if ordered is None:
        return {"lines": [], "extra": [], "complete": None}

    def tally(docs: list[list]) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        for rows in docs:
            for row in rows or []:
                try:
                    qty = float(row.get("qty") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                key = _line_key(row)
                if key == ("", ""):
                    continue                      # a blank spacer row
                out[key] = out.get(key, 0.0) + qty
        return out

    got, billed = tally(delivered_docs), tally(invoiced_docs)
    lines, seen = [], set()
    for row in ordered:
        key = _line_key(row)
        seen.add(key)
        try:
            qty = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        d, i = got.get(key, 0.0), billed.get(key, 0.0)
        lines.append({
            "item": row.get("item") or "", "description": row.get("description") or "",
            "unit": row.get("unit") or "", "rate": row.get("rate") or "",
            "ordered": round(qty, 3), "delivered": round(d, 3),
            "invoiced": round(i, 3), "remaining": round(max(0.0, qty - d), 3),
        })
    # Anything shipped that the LPO never ordered. Worth naming, not hiding.
    extra = [{"item": k[0], "description": k[1], "qty": round(v, 3)}
             for k, v in got.items() if k not in seen]
    complete = bool(lines) and all(
        l["delivered"] >= l["ordered"] - 0.001 and l["invoiced"] >= l["ordered"] - 0.001
        for l in lines)
    return {"lines": lines, "extra": extra, "complete": complete}


def lpo_fulfillment(client_name: str, lpo_no: str) -> dict[str, Any]:
    """What is still owed on one LPO, read from the documents linked to it."""
    rec = load_lpo(client_name, lpo_no)
    if not rec:
        return {"lines": [], "extra": [], "complete": None}
    ordered = json.loads(rec["rows_json"]) if rec.get("rows_json") else None
    key = _usage_key(rec["client_name"], rec["lpo_no"])
    dn_rows, inv_rows = [], []
    with _connect() as conn:
        for r in conn.execute(
                "SELECT client_name, lpo_no, rows_json FROM dns "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a')"):
            if _usage_key(r["client_name"], r["lpo_no"]) == key:
                dn_rows.append(json.loads(r["rows_json"] or "[]"))
        for r in conn.execute(
                "SELECT client_name, lpo_no, rows_json FROM invoices "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
            if _usage_key(r["client_name"], r["lpo_no"]) == key:
                inv_rows.append(json.loads(r["rows_json"] or "[]"))
    return _fulfillment_from_rows(ordered, dn_rows, inv_rows)
```

- [ ] **Step 4: Add the bridge method**

```python
    def lpo_fulfillment(self, client_name: str, lpo_no: str) -> dict:
        """Per-line ordered / delivered / invoiced / remaining for one LPO."""
        return _bridge_json_safe(database.lpo_fulfillment(client_name, lpo_no))
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
python tests/client_lpo_fulfillment.test.py
```

Expected: `Ran 7 tests ... OK`

- [ ] **Step 6: Mutate**

Change `if ordered is None:` to `if not ordered:`, rerun, and confirm
`test_an_lpo_with_no_recorded_lines_reports_nothing_rather_than_zero` goes red
(it now reports `complete: False` instead of None). Revert.

- [ ] **Step 7: Commit**

```bash
git add backend/database.py backend/api.py tests/client_lpo_fulfillment.test.py
git commit -m "feat: an LPO knows what is still owed, line by line

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: The monthly billing reminder

**Files:**
- Modify: `backend/database.py` (`_ATTENTION_CATEGORIES` at 2413, and
  `dashboard_needs_attention`)
- Modify: `static/todo.js`
- Test: `tests/client_lpo_billing_due.test.py` (create)

One entry per LPO, not per missing month: the row names the oldest unbilled
month and how many there are. Per-month rows would let one forgotten contract
fill the feed, and the mute key is `(kind, doc_no)`, so muting would be
all-or-nothing anyway.

- [ ] **Step 1: Write the failing test**

```python
"""Rentals and supply contracts bill every month, and the app should say so.

CSSP-RENT-0002 runs seven months from 2026-06-08. Nothing in the app notices a
month going by unbilled, so the reminder has to be computed from the period.
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


class BillingDue(unittest.TestCase):
    def months(self, today=date(2026, 9, 11), **kw):
        base = {"kind": "rental", "client_name": "HDEC", "lpo_no": "CSSP-RENT-0002",
                "period_start": "8/6/2026", "period_end": "8/1/2027",
                "lpo_date": "4/6/2026", "closed_at": None, "invoice_months": set()}
        base.update(kw)
        return database._lpo_months_unbilled(base, today=today)

    def test_a_materials_po_never_raises_a_billing_reminder(self):
        self.assertEqual([], self.months(kind="materials"))

    def test_every_month_since_the_period_started_is_due(self):
        """Started 8 June, today is 11 September: June, July, August, September."""
        self.assertEqual([(2026, 6), (2026, 7), (2026, 8), (2026, 9)], self.months())

    def test_an_invoiced_month_drops_out(self):
        self.assertEqual([(2026, 6), (2026, 8), (2026, 9)],
                         self.months(invoice_months={(2026, 7)}))

    def test_nothing_is_due_past_the_period_end(self):
        self.assertEqual([], self.months(period_end="8/7/2026",
                                         invoice_months={(2026, 6), (2026, 7)}))

    def test_a_closed_contract_stops_asking(self):
        self.assertEqual([], self.months(closed_at="2026-07-01T00:00:00"))

    def test_an_open_contract_counts_from_its_lpo_date_when_no_period_was_given(self):
        """CSSP-OPEN-0001 carries no period at all."""
        self.assertEqual([(2026, 8), (2026, 9)],
                         self.months(kind="open", period_start="", period_end="",
                                     lpo_date="15/8/2026"))

    def test_the_future_is_not_overdue(self):
        self.assertEqual([], self.months(period_start="1/12/2026"))


class Bucket(unittest.TestCase):
    def test_billing_can_be_muted(self):
        self.assertIn("billing", database._ATTENTION_CATEGORIES,
                      "a bucket with no Ignore button can never be silenced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/client_lpo_billing_due.test.py
```

Expected: FAIL - no attribute `_lpo_months_unbilled`.

- [ ] **Step 3: Implement the month walk**

```python
def _month_seq(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) from start to end inclusive."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _lpo_months_unbilled(d: dict[str, Any], today: date | None = None) -> list[tuple[int, int]]:
    """Which months of a running contract have no invoice against them.

    Only rental and open contracts bill monthly; a materials PO is one order.
    An open contract with no period recorded counts from its own date, which is
    all CSSP-OPEN-0001 gives us - it carries no period at all.
    """
    kind = (d.get("kind") or "materials").strip().lower()
    if kind not in ("rental", "open") or d.get("closed_at"):
        return []
    today = today or date.today()
    start = (_parse_doc_date(d.get("period_start") or "")
             or _parse_doc_date(d.get("lpo_date") or ""))
    if not start or start > today:
        return []
    end = _parse_doc_date(d.get("period_end") or "") or today
    end = min(end, today)
    billed = set(d.get("invoice_months") or ())
    return [ym for ym in _month_seq(start, end) if ym not in billed]


def _lpo_invoice_months(row: dict[str, Any]) -> set[tuple[int, int]]:
    """Which months already carry an invoice against this LPO."""
    key = _usage_key(row.get("client_name", ""), row.get("lpo_no", ""))
    out: set[tuple[int, int]] = set()
    with _connect() as conn:
        for r in conn.execute(
                "SELECT client_name, lpo_no, year, month FROM invoices "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
            if _usage_key(r["client_name"], r["lpo_no"]) == key and r["year"]:
                out.add((int(r["year"]), int(r["month"] or 0)))
    return out
```

Add `"billing"` to `_ATTENTION_CATEGORIES`.

- [ ] **Step 4: Run the test and confirm it passes**

```bash
python tests/client_lpo_billing_due.test.py
```

Expected: `Ran 8 tests ... OK`

- [ ] **Step 5: Mutate**

Remove `or d.get("closed_at")` from the guard, rerun, confirm
`test_a_closed_contract_stops_asking` goes red. Restore.

- [ ] **Step 6: Add the bucket to the feed**

Read the existing buckets first (`docs/03-BACKEND-DATA.md:276` describes the
contract) and follow their exact shape: entries go through `_oldest_first` and
the ignore partition.

```python
    billing = []
    for row in list_lpos(""):
        months = _lpo_months_unbilled({**row, "invoice_months": _lpo_invoice_months(row)})
        if not months:
            continue
        y, m = months[0]
        billing.append({
            "kind": "lpo", "doc_no": row["lpo_no"], "client_name": row["client_name"],
            "date": row.get("lpo_date") or "",
            "months_due": len(months),
            "note": (f"{len(months)} month{'s' if len(months) != 1 else ''} "
                     f"not invoiced, oldest {_month_label(y, m)}"),
        })
```

`_month_label` does not exist yet - `backend/` has no month-name list at all
(checked 2026-09-11). Add it next to `_month_seq`:

```python
def _month_label(year: int, month: int) -> str:
    """"August 2026" - for a reminder a person reads, not a sort key."""
    import calendar
    name = calendar.month_name[month] if 1 <= int(month or 0) <= 12 else "?"
    return f"{name} {year}"
```

- [ ] **Step 7: Render it in the To-Do view**

In `static/todo.js`, add a `billing` section alongside `deliver`. Follow the
`deliver` bucket exactly (`docs/07-FRONTEND-SUPPORT.md:65`): its rows get an
"Open LPO" button rather than a state-change action, and an eye-off Ignore
button like every other row. The row text is the entry's `note`, which already
reads as a sentence. **No colour-only signalling** - the month count is words.

- [ ] **Step 8: Verify against the live data**

```bash
python -c "import sys; sys.path.insert(0,'.'); from backend import database; d=database.dashboard_needs_attention(); print('billing entries:', len(d.get('billing', []))); [print(' ', e['doc_no'], e['note']) for e in d.get('billing', [])]"
```

Expected today: `billing entries: 0` - no LPOs are filed yet. Re-run after Task
13 files the real ones and confirm the rentals and the open contract appear.

- [ ] **Step 9: Commit**

```bash
git add backend/database.py static/todo.js tests/client_lpo_billing_due.test.py
git commit -m "feat: a rental or supply contract says when a month has gone unbilled

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: The ordered-lines editor

**Files:**
- Create: `static/lpo_lines.js`
- Modify: `templates/index.html` (script tag after `lpo.js`)
- Test: `tests/lpo_lines_editor.test.js` (create)

This is the piece that makes filing an LPO take five seconds. Pull the cited
quotation, show its lines pre-ticked at quoted quantity, untick what the client
dropped, adjust a quantity they changed. Live total, and a mismatch against the
PO's printed total stated in words.

- [ ] **Step 1: Write the failing test**

```js
/* The ordered-lines editor: inherit a quotation's lines, tick what they ordered.

   CSSP-ADM-0010 took four of QUT-MT-HDEC-029's five lines verbatim and dropped
   the fifth. Filing it must be "untick one line", not "retype four". */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "lpo_lines.js");

function load() {
    const ctx = { window: {}, document: { getElementById: () => null } };
    ctx.window.document = ctx.document;
    vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx);
    return ctx.window.LpoLines;
}

const QUOTE_ROWS = [
    { item: "Office desk", description: "1650*850*800", unit: "EA", qty: 60, rate: "135" },
    { item: "3-drawer mobile pedestal under desk", description: "400*520*600", unit: "EA", qty: 60, rate: "64" },
    { item: "Office chair", description: "670*580*1050", unit: "EA", qty: 78, rate: "75" },
    { item: "Meeting Table-1", description: "2100*900*720", unit: "EA", qty: 3, rate: "200" },
    { item: "3-drawer enclosed type", description: "800*410*1200", unit: "EA", qty: 7, rate: "128" },
];

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

const L = load();

check("every quoted line arrives ticked at its quoted quantity", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    assert.strictEqual(lines.length, 5);
    assert.ok(lines.every(l => l.take === true), "lines must start ticked");
    assert.deepEqual(lines.map(l => l.qty), [60, 60, 78, 3, 7]);
});

check("the total of the ticked lines is the LPO value", () => {
    assert.strictEqual(L.total(L.fromQuote(QUOTE_ROWS)), 19286);
});

check("unticking the line they dropped gives their printed total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;                       // 3-drawer enclosed type, 7 @ 128
    assert.strictEqual(L.total(lines), 18390,
        "CSSP-ADM-0010 prints 18,390 and this is how you reach it");
});

check("changing a quantity changes the total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[0].qty = 30;                           // half the desks
    assert.strictEqual(L.total(lines), 19286 - 30 * 135);
});

check("a mismatch is described in words, with the difference", () => {
    const note = L.mismatch(L.fromQuote(QUOTE_ROWS), 18390);
    assert.ok(note, "19,286 of lines against a printed 18,390 is a mismatch");
    assert.ok(/19,286/.test(note) && /18,390/.test(note) && /896/.test(note),
        "the note must give both totals and the difference: " + note);
});

check("no note when the lines agree with the printed total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;
    assert.strictEqual(L.mismatch(lines, 18390), "");
});

check("no note when no total was typed - nothing to disagree with", () => {
    assert.strictEqual(L.mismatch(L.fromQuote(QUOTE_ROWS), 0), "");
});

check("rounding to the cent does not raise a false mismatch", () => {
    assert.strictEqual(L.mismatch([{ take: true, qty: 3, rate: "0.1" }], 0.3), "");
});

check("only ticked lines are saved", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;
    const rows = L.toRows(lines);
    assert.strictEqual(rows.length, 4);
    assert.ok(rows.every(r => !("take" in r)), "the tick is UI state, not data");
    assert.deepEqual(Object.keys(rows[0]).sort(),
        ["description", "item", "qty", "rate", "unit"]);
});

check("a blank manual line is dropped rather than saved empty", () => {
    const rows = L.toRows([{ take: true, item: "", description: "", qty: 0, rate: "" }]);
    assert.strictEqual(rows.length, 0);
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
node tests/lpo_lines_editor.test.js
```

Expected: it cannot load - `ENOENT ... static/lpo_lines.js`.

- [ ] **Step 3: Write `static/lpo_lines.js`**

```js
/* The ordered lines on a client's LPO (2026-09-11).

   Waleed's POs do not invent items. CSSP-ADM-0010 cites QUT-MT-HDEC-029 on its
   face and reproduces four of our five lines verbatim - same wording, same
   dimensions, same quantity, same rate - dropping the fifth. Their own
   "Certification" column says "Specifications identical to those in the
   attached quotation". So the lines are INHERITED and the work is a tick box,
   not a keyboard.

   Manual entry stays for the POs that cite the client's own reference:
   CSSP-RENT-0002 cites QUT-146 and CSSP-OPEN-0001 cites QUT-03, neither of
   which is ours. Both are a single line. */
(function () {
    'use strict';

    const num = v => {
        const n = parseFloat(String(v == null ? '' : v).replace(/,/g, ''));
        return isFinite(n) ? n : 0;
    };
    const money = n => n.toLocaleString('en-US',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    /* Every line of the quotation, ticked, at the quantity we quoted. The
       client's changes are subtractions from this - that is what a PO IS. */
    function fromQuote(rows) {
        return (rows || []).map(r => ({
            take: true,
            item: r.item || '',
            description: r.description || '',
            unit: r.unit || '',
            qty: num(r.qty),
            rate: r.rate == null ? '' : String(r.rate),
        }));
    }

    function blank() {
        return { take: true, item: '', description: '', unit: '', qty: 0, rate: '' };
    }

    function total(lines) {
        return Math.round((lines || []).reduce(
            (a, l) => l.take ? a + num(l.qty) * num(l.rate) : a, 0) * 100) / 100;
    }

    /* Stated in words, never blocking (Waleed, 2026-09-11). A PO can legitimately
       carry an odd total - a discount, a rounding, a typo on their side - and
       refusing the save would strand him. Saying nothing would hide a misread. */
    function mismatch(lines, printedTotal) {
        const printed = num(printedTotal);
        if (!printed) return '';                 // nothing typed, nothing to disagree with
        const linesTotal = total(lines);
        const diff = Math.round((linesTotal - printed) * 100) / 100;
        if (Math.abs(diff) < 0.01) return '';
        return `These lines add up to ${money(linesTotal)}, but the PO says `
             + `${money(printed)} - a difference of ${money(Math.abs(diff))}. `
             + `Save anyway if that is what their PO shows.`;
    }

    /* What gets stored. The tick is UI state and never reaches the record, and
       a line with no item and no quantity is somebody's abandoned keystroke. */
    function toRows(lines) {
        return (lines || [])
            .filter(l => l.take)
            .filter(l => String(l.item || '').trim() || num(l.qty))
            .map(l => ({
                item: String(l.item || '').trim(),
                description: String(l.description || '').trim(),
                unit: String(l.unit || '').trim(),
                qty: num(l.qty),
                rate: l.rate == null ? '' : String(l.rate),
            }));
    }

    /* A stored record read back for editing: everything on file was ordered. */
    function fromRows(rows) {
        return (rows || []).map(r => ({ take: true, ...r, qty: num(r.qty) }));
    }

    window.LpoLines = { fromQuote, fromRows, toRows, blank, total, mismatch };
})();
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
node tests/lpo_lines_editor.test.js
```

Expected: ten `ok` lines then `all passed`.

- [ ] **Step 5: Mutate**

Change `if (Math.abs(diff) < 0.01) return '';` to `if (diff === 0) return '';`
and rerun - "rounding to the cent does not raise a false mismatch" must go red.
Revert. Then change `.filter(l => l.take)` to `.filter(() => true)` and confirm
"only ticked lines are saved" goes red. Revert.

- [ ] **Step 6: Add the script tag**

In `templates/index.html`, immediately AFTER the existing `lpo.js` tag:

```html
<script src="../static/lpo_lines.js"></script>
```

- [ ] **Step 7: Commit**

```bash
git add static/lpo_lines.js templates/index.html tests/lpo_lines_editor.test.js
git commit -m "feat: LPO lines are inherited from the quotation, not retyped

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: The redesigned LPO form

**Files:**
- Modify: `static/lpo.js` (`openLpoForm`, roughly lines 152-215)
- Modify: `backend/api.py` (add `lpo_from_quote`)
- Modify: `static/app_styles.css`
- Test: `tests/lpo_form_shape.test.js` (create)

The form is three stacked parts. Only the first is always visible.

1. **Identity** - client (picked, never free - Task 4 refuses an unknown one),
   their LPO number, date, and the kind as three labelled choices:
   **Materials** (a one-off order) / **Rental** (runs for a period) /
   **Supply contract** (open ended).
2. **The money block, which depends on the kind:**
   - Materials: total value + currency.
   - Rental: monthly amount + currency, period start, period end. The full-term
     total is shown computed and stays editable if their PO says otherwise.
   - Supply contract: currency, an optional expiry, and NO total field at all. A
     hint says why: "An open contract has no ceiling. CSSP-OPEN-0001 prints
     0.77, which is the price of a litre of diesel."
3. **What they ordered** - the `LpoLines` editor, with a "Pull from quotation"
   row at its top pre-filled from "Against quotation".

- [ ] **Step 1: Add the backend method that hands over a quotation's lines**

```python
    def lpo_from_quote(self, quote_no: str) -> dict:
        """The lines of one of our quotations, for filing the LPO that cites it.

        Images are stripped: a quotation row can carry a base64 photo, which has
        no business in an LPO record and makes the bridge payload enormous.
        """
        quote = database.load_quote(quote_no or "")
        if not quote:
            return {"ok": False, "error": f"no quotation {quote_no}"}
        rows = [{k: v for k, v in (row or {}).items()
                 if k in ("item", "description", "unit", "qty", "rate")}
                for row in (quote.get("items") or quote.get("rows") or [])]
        return {"ok": True, "quote_no": quote.get("quote_no"),
                "client_name": quote.get("client_name"), "rows": rows}
```

Read `database.load_quote` (`backend/database.py:1793`) first and use whichever
key it actually returns the rows under, rather than relying on the `or`.

- [ ] **Step 2: Write the failing test**

```js
/* The LPO form must not offer a total on a contract that has no ceiling, and
   must not hide the period on one that runs for months. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "lpo.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("the form asks which kind of PO this is", () => {
    assert.ok(/lpoFKind/.test(SRC), "no kind control in the form");
    for (const k of ["materials", "rental", "open"]) {
        assert.ok(new RegExp(`["']${k}["']`).test(SRC), `kind ${k} is not offered`);
    }
});

check("a rental asks for its period", () => {
    assert.ok(/lpoFPeriodStart/.test(SRC) && /lpoFPeriodEnd/.test(SRC),
        "a rental with no period cannot raise a monthly reminder");
});

check("a rental asks for a monthly amount", () => {
    assert.ok(/lpoFMonthly/.test(SRC));
});

check("the form can pull lines from the cited quotation", () => {
    assert.ok(/lpo_from_quote/.test(SRC), "nothing calls the quotation bridge");
    assert.ok(/LpoLines/.test(SRC), "the lines editor is never used");
});

check("the mismatch note is shown, not swallowed", () => {
    assert.ok(/\.mismatch\(/.test(SRC),
        "a lines-versus-total mismatch is computed but never displayed");
});

check("saving sends the new fields", () => {
    const start = SRC.indexOf("const payload = {");
    assert.ok(start > 0, "could not find the save payload");
    const body = SRC.slice(start, start + 1200);
    for (const f of ["kind", "rows", "period_start", "period_end", "monthly_amount"]) {
        assert.ok(new RegExp(`\\b${f}\\b`).test(body), `payload omits ${f}`);
    }
});

check("a refused client offers the add-client popup", () => {
    assert.ok(/ClientRequired/.test(SRC),
        "an LPO naming an unknown client would dead-end with a toast");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
node tests/lpo_form_shape.test.js
```

Expected: all seven FAIL.

- [ ] **Step 4: Rewrite `openLpoForm`**

Replace the existing function. Requirements, all pinned by the test above:

- Build the client datalist from client master (`cm_list`), not the old
  `client_list()`. Read how `static/client_required.js` repopulates a client
  list and follow it.
- `#lpoFKind` is a three-way control. Changing it shows and hides the money
  block; use `el.hidden`, not `style.display`.
- `#lpoFQuote` gets a **Pull lines** button calling `api().lpo_from_quote(no)`
  then `LpoLines.fromQuote(res.rows)`, rendering the tick list. On failure, a
  toast naming the number - never a silent no-op.
- The lines area shows a live total and, underneath, `LpoLines.mismatch(...)`
  against `#lpoFTotal`. It is a plain sentence in the normal text colour with a
  warning glyph, **not colour-coded** (Waleed is colour blind).
- An **Add a line** button appends `LpoLines.blank()`, for POs citing the
  client's own reference.
- The save payload gains `kind`, `rows: LpoLines.toRows(lines)`, `period_start`,
  `period_end`, `monthly_amount`, `erp_no`, `project`, `contact`. Existing
  fields stay exactly as they are.
- After `api().save_lpo(payload)` returns, if
  `window.ClientRequired && await window.ClientRequired.offer(res, "lpo")`
  is true, rebuild the payload and save once more - mirroring `inv.js` /
  `quote.js` / `dn.js`. `ClientRequired`'s `FIELDS` map has no `lpo` entry; read
  `static/client_required.js:136-143`, confirm it tolerates a missing one, and
  add an `lpo` entry pointing at `#lpoFClient` so the new name lands back in the
  form.

- [ ] **Step 5: Run the test and confirm it passes**

```bash
node tests/lpo_form_shape.test.js
```

Expected: seven `ok` lines then `all passed`.

- [ ] **Step 6: Mutate**

Delete the `rows:` entry from the save payload, rerun, confirm "saving sends the
new fields" goes red naming `rows`. Restore.

- [ ] **Step 7: Check the em dash ban**

```bash
node tests/no_em_dash.test.js
```

Expected: PASS. If it names `static/lpo.js` or `static/lpo_lines.js`, replace
the character with a plain hyphen. Do not add an exception.

- [ ] **Step 8: Commit**

```bash
git add static/lpo.js static/client_required.js backend/api.py static/app_styles.css tests/lpo_form_shape.test.js
git commit -m "feat: the LPO form knows the three kinds of PO and pulls its lines from the quotation

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Filing an LPO marks the quotation Won

**Files:**
- Modify: `backend/api.py` (replace the Task 4 stub)
- Test: `tests/lpo_marks_quote_won.test.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""An LPO IS the win.

Today a quotation only counts as won once it is invoiced, which can be weeks
after the client ordered - so the Decide list nags about quotations already
sitting on a purchase order.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MarksWon(unittest.TestCase):
    def call(self, lpo, existing_status="open"):
        from backend import api
        bridge = api.Api()
        with patch.object(api, "_known_client_error", return_value=None), \
             patch.object(api.database, "save_lpo", return_value={"ok": True}), \
             patch.object(api.database, "load_lpo", return_value=None), \
             patch.object(api.database, "load_quote",
                          return_value={"quote_no": lpo.get("source_quote_no"),
                                        "bid_status": existing_status}), \
             patch.object(api.database, "set_bid_status",
                          return_value={"ok": True}) as won:
            bridge.save_lpo(lpo)
        return won

    def test_filing_an_lpo_wins_the_quotation_it_cites(self):
        won = self.call({"client_name": "HDEC", "lpo_no": "CSSP-ADM-0010",
                         "source_quote_no": "QUT-MT-HDEC-029"})
        won.assert_called_once_with("QUT-MT-HDEC-029", "won")

    def test_an_lpo_citing_nothing_of_ours_touches_no_quotation(self):
        """CSSP-RENT-0002 cites QUT-146, which is HDEC's own reference."""
        won = self.call({"client_name": "HDEC", "lpo_no": "CSSP-RENT-0002",
                         "source_quote_no": ""})
        won.assert_not_called()

    def test_a_quotation_already_marked_lost_is_left_alone(self):
        """If it was marked lost deliberately, an edit to the LPO header must
        not quietly overturn that."""
        won = self.call({"client_name": "HDEC", "lpo_no": "CSSP-ADM-0010",
                         "source_quote_no": "QUT-MT-HDEC-029"},
                        existing_status="lost")
        won.assert_not_called()

    def test_a_failed_save_wins_nothing(self):
        from backend import api
        bridge = api.Api()
        with patch.object(api, "_known_client_error", return_value=None), \
             patch.object(api.database, "save_lpo",
                          return_value={"ok": False, "error": "nope"}), \
             patch.object(api.database, "load_lpo", return_value=None), \
             patch.object(api.database, "set_bid_status") as won:
            bridge.save_lpo({"client_name": "HDEC", "lpo_no": "X",
                             "source_quote_no": "QUT-MT-HDEC-029"})
        won.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python tests/lpo_marks_quote_won.test.py
```

Expected: FAIL - `set_bid_status` was never called (the Task 4 stub does nothing).

- [ ] **Step 3: Replace the stub**

```python
    @staticmethod
    def _win_quote_for_lpo(quote_no: str | None) -> None:
        """An LPO is the client accepting the quotation, so mark it won.

        Won used to be derived from having invoiced, which can be weeks later -
        the Decide list nagged about quotations already sitting on a purchase
        order. A quotation deliberately marked LOST is left alone: overturning
        that from a header edit would be the app arguing with the user.

        Never raises. Filing the client's paperwork must not fail because the
        quotation it cites has gone missing.
        """
        number = (quote_no or "").strip()
        if not number:
            return                       # cites the client's own reference, not ours
        try:
            quote = database.load_quote(number)
            if not quote or (quote.get("bid_status") or "").strip().lower() == "lost":
                return
            database.set_bid_status(number, "won")
        except Exception:
            pass
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
python tests/lpo_marks_quote_won.test.py
```

Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: Mutate**

Remove the `lost` check, rerun, confirm
`test_a_quotation_already_marked_lost_is_left_alone` goes red. Restore. Then
move the `_win_quote_for_lpo` call above the `if result.get("ok")` guard and
confirm `test_a_failed_save_wins_nothing` goes red. Restore.

- [ ] **Step 6: Commit**

```bash
git add backend/api.py tests/lpo_marks_quote_won.test.py
git commit -m "feat: filing an LPO marks the quotation it cites as won

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: The LPO picker on the DN and invoice editors

**Files:**
- Create: `static/lpo_picker.js`
- Modify: `static/dn.js`, `static/inv.js`, `templates/index.html`
- Test: `tests/lpo_picker.test.js` (create)

`#dnLpo` and `#invLpo` are plain text inputs defaulting to `N/A`
(`templates/index.html:1439` and `:1561`). They become a picker: click, see this
client's open LPOs, choose one. `N/A` stays a first-class choice - 63 of 64
delivery notes legitimately have no LPO.

- [ ] **Step 1: Write the failing test**

```js
/* Attaching a document to an LPO. The number still prints on the document; what
   changes is that it can no longer be a number nobody ever received. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "lpo_picker.js");

function load(listLpos) {
    const ctx = {
        window: {}, console,
        document: { getElementById: () => null },
        setTimeout: (f) => f(),
    };
    ctx.window.document = ctx.document;
    ctx.window.pywebview = { api: { list_lpos: listLpos } };
    vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx);
    return ctx.window.LpoPicker;
}

const ROWS = [
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0010", status: "delivered", kind: "materials", total: 18390, currency: "USD" },
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0017", status: "closed", kind: "materials", total: 273.4, currency: "USD" },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0002", status: "invoiced", kind: "rental", total: 11900, currency: "USD" },
];

/* list_lpos filters by client server-side, so the fake does too. */
const fake = async (client) => ROWS.filter(r => !client || r.client_name === client);

let failures = 0;
function check(what, fn) {
    return Promise.resolve().then(fn)
        .then(() => console.log("  ok   " + what))
        .catch(e => { failures++; console.log("  FAIL " + what + "\n       " + e.message); });
}

(async () => {
    const P = load(fake);

    await check("only this client's LPOs are offered", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(out.every(r => r.client_name === "HDEC"),
            "another client's PO must never be offered");
    });

    await check("a closed LPO is not offered", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(!out.some(r => r.lpo_no === "CSSP-ADM-0017"),
            "CSSP-ADM-0017 is closed and cannot take new documents");
    });

    await check("a running rental is still offered after it has been invoiced", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(out.some(r => r.lpo_no === "CSSP-RENT-0002"),
            "a rental is invoiced every month and stays open all term");
    });

    await check("the current value is kept even when it is now closed", async () => {
        const out = await P.choicesFor("HDEC", "CSSP-ADM-0017");
        assert.ok(out.some(r => r.lpo_no === "CSSP-ADM-0017"),
            "a saved document must not lose the LPO it was saved against");
    });

    await check("a client with no LPOs on file yields an empty list, not an error", async () => {
        assert.deepEqual(await P.choicesFor("Nobody"), []);
    });

    await check("N/A is a real answer", () => {
        assert.strictEqual(P.NO_LPO, "N/A");
    });

    await check("an unreachable bridge returns empty rather than throwing", async () => {
        const Q = load(async () => { throw new Error("gone"); });
        assert.deepEqual(await Q.choicesFor("HDEC"), []);
    });

    console.log(failures ? `\n${failures} FAILED` : "\nall passed");
    process.exit(failures ? 1 : 0);
})();
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
node tests/lpo_picker.test.js
```

Expected: `ENOENT ... static/lpo_picker.js`.

- [ ] **Step 3: Write `static/lpo_picker.js`**

```js
/* Attaching a delivery note or invoice to the LPO it fulfils (2026-09-11).

   `dns.lpo_no` and `invoices.lpo_no` were free text, and it showed: 63 of 64
   delivery notes said "N/A", one said CSSP-ADM-0010, and two invoices carried
   "20260905 SHIP WATER", which is not an LPO number at all. Everything the LPO
   screen reports - delivered, invoiced, closed, what is still owed - hangs off
   that string matching, so nothing ever lit up.

   The number is still what prints on the document. What changes is that it can
   only be a number this client actually sent. */
(function () {
    'use strict';

    const NO_LPO = 'N/A';
    const api = () => window.pywebview.api;

    /* Which LPOs this client can still take documents against.

       A closed one is excluded - it is finished. `current` is always kept even
       when closed: reopening a saved delivery note must never quietly drop the
       LPO it was filed against. */
    async function choicesFor(clientName, current) {
        let rows = [];
        try {
            rows = (await api().list_lpos(clientName || '')) || [];
        } catch (error) {
            return [];                  // the editor still works without a picker
        }
        const keep = String(current || '').trim().toLowerCase();
        return rows.filter(r =>
            String(r.lpo_no || '').trim().toLowerCase() === keep
            || (r.status || '') !== 'closed');
    }

    window.LpoPicker = { NO_LPO, choicesFor };
})();
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
node tests/lpo_picker.test.js
```

Expected: seven `ok` lines then `all passed`.

- [ ] **Step 5: Mutate**

Drop the `keep` clause so the filter is only `(r.status || '') !== 'closed'` and
rerun: "the current value is kept even when it is now closed" must go red.
Restore.

- [ ] **Step 6: Wire the two editors**

Add the script tag to `templates/index.html` after `lpo_lines.js`. In both
`static/dn.js` and `static/inv.js`, attach a click handler to `#dnLpo` /
`#invLpo` opening a `Popup` that lists `await LpoPicker.choicesFor(client, cur)`
plus a **No LPO (N/A)** row at the top, and writes the chosen `lpo_no` into the
input. Each row shows the number, the kind, the status word and the value.

Keep the input readable and typable: a picker that refuses the keyboard would be
a regression for the odd case. The point is that picking is the easy path, not
that typing is forbidden.

- [ ] **Step 7: Verify both editors still save**

```bash
python smoke_test.py
```

```bash
python smoke_quote.py
```

Expected: both report success.

- [ ] **Step 8: Commit**

```bash
git add static/lpo_picker.js static/dn.js static/inv.js templates/index.html tests/lpo_picker.test.js
git commit -m "feat: a delivery note or invoice picks its LPO instead of retyping the number

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: The redesigned list screen

**Files:**
- Modify: `static/lpo.js` (`renderLpoList`, the filter bar, the totals strip)
- Modify: `templates/index.html:1718-1752` (`lpoView`)
- Modify: `static/app_styles.css`
- Modify: `backend/database.py` (`list_lpos` supplies the rental month counts)
- Test: `tests/lpo_row_summary.test.js` (create)

The row's job is to answer "where is this one up to" without opening anything.
That answer is different per kind, which the current single row cannot express.

- [ ] **Step 1: Write the failing test**

```js
/* Each kind of PO needs a different sentence. A rental's progress is months; a
   supply contract has no total to be a percentage of. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "static", "lpo.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("the row summary is a named function, not inline string soup", () => {
    assert.ok(/function lpoProgress/.test(SOURCE),
        "extract the per-kind sentence so it can be tested");
});

/* Pull just that function out and run it in isolation. */
function progress() {
    const start = SOURCE.indexOf("function lpoProgress");
    assert.ok(start > 0, "lpoProgress not found");
    let depth = 0, end = start;
    for (let i = SOURCE.indexOf("{", start); i < SOURCE.length; i++) {
        if (SOURCE[i] === "{") depth++;
        else if (SOURCE[i] === "}") { depth--; if (!depth) { end = i + 1; break; } }
    }
    const ctx = { console, out: null };
    vm.runInNewContext(SOURCE.slice(start, end) + "; out = lpoProgress;", ctx);
    return ctx.out;
}

const P = progress();

check("a materials PO reports value invoiced against value ordered", () => {
    const s = P({ kind: "materials", currency: "USD",
                  invoiced_usd: 8100, total_usd: 18390 });
    assert.ok(/8,100/.test(s) && /18,390/.test(s), s);
});

check("a supply contract never reports a percentage of its printed total", () => {
    const s = P({ kind: "open", currency: "USD", invoiced_usd: 1240,
                  total_usd: 0.77, invoices: ["INV-MT-HDEC-070"] });
    assert.ok(!/0\.77/.test(s),
        "0.77 is the price of a litre of diesel, not a contract value: " + s);
    assert.ok(/1,240/.test(s), "what HAS been invoiced is the useful number: " + s);
});

check("a rental reports months, not money alone", () => {
    const s = P({ kind: "rental", currency: "USD", invoiced_usd: 1700,
                  total_usd: 11900, months_total: 7, months_invoiced: 2 });
    assert.ok(/month/i.test(s), "a rental's progress is months: " + s);
    assert.ok(/2/.test(s) && /7/.test(s), s);
});

check("every status is carried by a word, never by colour alone", () => {
    for (const w of ["Received", "Acknowledged", "Delivered", "Invoiced", "Closed"]) {
        assert.ok(new RegExp(w).test(SOURCE), `the ${w} label went missing`);
    }
});

check("the over-value warning follows the backend flag, not a local sum", () => {
    assert.ok(/over_value/.test(SOURCE),
        "recomputing the ceiling in JS is how the open-contract bug comes back");
    assert.ok(!/Number\(r\.invoiced_usd\)\s*\|\|\s*0\)\s*>\s*\(Number\(r\.total_usd/.test(SOURCE),
        "the old local comparison is still here");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
node tests/lpo_row_summary.test.js
```

Expected: FAIL from the first check onward.

- [ ] **Step 3: Implement `lpoProgress` and rework the row**

```js
/* Where one LPO has got to, in a sentence. Different per kind because the
   question is different: a materials PO is money against money, a rental is
   months, and a supply contract has no ceiling to be a fraction of. */
function lpoProgress(r) {
    const cur = r.currency || "USD";
    const m = v => (cur === "IQD")
        ? "IQD " + Math.round(Number(v) || 0).toLocaleString("en-US")
        : "$" + (Number(v) || 0).toLocaleString("en-US",
            { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const kind = (r.kind || "materials").toLowerCase();
    if (kind === "open") {
        // No total. CSSP-OPEN-0001 prints 0.77, the price of one litre.
        const n = (r.invoices || []).length;
        return `${m(r.invoiced_usd)} invoiced on ${n} invoice${n === 1 ? "" : "s"}`;
    }
    if (kind === "rental") {
        const done = Number(r.months_invoiced) || 0;
        const all = Number(r.months_total) || 0;
        const months = all ? `month ${done} of ${all} invoiced`
                           : `${done} month${done === 1 ? "" : "s"} invoiced`;
        return `${months} - ${m(r.invoiced_usd)} of ${m(r.total_usd)}`;
    }
    return `${m(r.invoiced_usd)} of ${m(r.total_usd)} invoiced`;
}
```

In `renderLpoList`: replace the `over` line with `const over = !!r.over_value;`,
put `lpoProgress(r)` into the row's meta line, and add a kind chip reading
**Materials** / **Rental** / **Supply contract** beside the status chip. Add a
**Close** / **Reopen** action calling `api().close_lpo(...)`; for a rental,
prompt for a reason via `Popup` and pass it through.

Add a kind filter beside the status filter in `templates/index.html`, and change
the totals strip so open contracts are excluded from "not yet invoiced" - an
open contract has no remaining value to report.

- [ ] **Step 4: Run the test and confirm it passes**

```bash
node tests/lpo_row_summary.test.js
```

Expected: six `ok` lines then `all passed`.

- [ ] **Step 5: Mutate**

Make the `open` branch fall through to the materials return, rerun, and confirm
"a supply contract never reports a percentage of its printed total" goes red.
Restore.

- [ ] **Step 6: Supply the rental month counts from the backend**

In `database.list_lpos`, inside the per-row loop:

```python
        if d.get("kind") == "rental":
            start = _parse_doc_date(d.get("period_start") or "")
            end = _parse_doc_date(d.get("period_end") or "")
            d["months_total"] = len(_month_seq(start, end)) if start and end else 0
            d["months_invoiced"] = len(_lpo_invoice_months(d))
```

- [ ] **Step 7: Commit**

```bash
git add static/lpo.js templates/index.html static/app_styles.css backend/database.py tests/lpo_row_summary.test.js
git commit -m "feat: an LPO row says where it has got to in its own terms

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: File the six real LPOs and clear the orphan numbers

**Files:**
- Test: `tests/no_orphan_lpo_numbers.test.py` (create)

Four documents name an LPO with no record behind it: `DN-HDEC-046` and
`INV-MT-HDEC-043` both say `CSSP-ADM-0010`; `INV-MT-TDC-053` and
`INV-MT-TDC-054` say `20260905 SHIP WATER` and `20260905 SHIP FOOD`, which are
descriptions, not LPO numbers.

- [ ] **Step 1: Write the guard test**

```python
"""Every LPO number on a document should have a record behind it.

Read-only. It names the documents and stops; the fix is to file the LPO or
correct the document, and neither belongs in a test.

`N/A` is a legitimate answer and always will be - 63 of 64 delivery notes carry
it honestly.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


class NoOrphanLpoNumbers(unittest.TestCase):
    def test_every_referenced_lpo_number_is_on_file(self):
        known = {database._usage_key(r["client_name"], r["lpo_no"])
                 for r in database.list_lpos("")}
        orphans = []
        with database._connect() as conn:
            for table, col in (("dns", "dn_no"), ("invoices", "inv_no")):
                for r in conn.execute(
                        f"SELECT {col} AS no, client_name, lpo_no FROM {table} "
                        "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a')"):
                    if database._usage_key(r["client_name"], r["lpo_no"]) not in known:
                        orphans.append((r["no"], r["client_name"], r["lpo_no"]))
        self.assertEqual(
            [], orphans,
            "these documents name an LPO with no record behind it, so nothing "
            "they deliver or bill can ever roll up:\n"
            + "\n".join(f"    {no}  ({client})  ->  {lpo!r}"
                        for no, client, lpo in orphans)
            + "\n  Either file that LPO on the Client LPOs screen, or correct "
              "the document if what it carries is not an LPO number at all.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and confirm it fails, naming all four documents**

```bash
python tests/no_orphan_lpo_numbers.test.py
```

Expected: FAIL listing `DN-HDEC-046`, `INV-MT-HDEC-043` (both `CSSP-ADM-0010`)
and the two `INV-MT-TDC-05x` rows.

- [ ] **Step 3: File the six real LPOs through the UI**

Run `python app.py`, open Client LPOs, and file each PDF in
`D:\ABRAJ\CSSP\LPO`. This is the real acceptance test for Tasks 8 to 12 - if
filing `CSSP-ADM-0010` is not a matter of picking `QUT-MT-HDEC-029` and
unticking one line, the form is not finished.

| File | Kind | Cites | Notes |
|---|---|---|---|
| `LPO CSSP-ADM-0010` | materials | `QUT-MT-HDEC-029` (ours) | pull lines, untick "3-drawer enclosed type", expect 18,390 |
| `LPO CSSP-ADM-0017` | materials | `QUT-MT-HDEC-037` (ours) | pull lines, expect 273.40 |
| `CSSP-RENT-0001` | rental | theirs | read the PDF for period and monthly amount |
| `CSSP-RENT-0002` | rental | `QUT-146` (theirs) | 14 set-month @ 850, from 2026-06-08, 7-month term |
| `LPO CSSP-RENT-0004` | rental | theirs | read the PDF |
| `CSSP-OPEN-0001` | open | `QUT-03` (theirs) | diesel 2,000 L/month at 0.77/L. **No total.** Type the one line |

Attach each PDF to its record and acknowledge the ones already counter-signed.

- [ ] **Step 4: Decide the two TDC invoices with Waleed**

`20260905 SHIP WATER` and `20260905 SHIP FOOD` are descriptions. Ask whether TDC
sent a real PO number for those two jobs. If yes, file it and repoint the
invoices. If no, set both invoices' LPO to `N/A`, which is what they are.
**Do not guess** - changing what a saved invoice says about its client's PO is
not a cleanup decision.

- [ ] **Step 5: Run the guard test and confirm it passes**

```bash
python tests/no_orphan_lpo_numbers.test.py
```

Expected: `Ran 1 test ... OK`

- [ ] **Step 6: Confirm the billing reminder now fires**

```bash
python -c "import sys; sys.path.insert(0,'.'); from backend import database; d=database.dashboard_needs_attention(); [print(e['doc_no'], '-', e['note']) for e in d.get('billing', [])]"
```

Expected: the three rentals and the open contract each name their oldest
unbilled month. If a rental that has been fully invoiced still appears, its
period or its invoice links are wrong - investigate before adjusting the rule.

- [ ] **Step 7: Commit**

```bash
git add tests/no_orphan_lpo_numbers.test.py data/profiles/abraj/po_tool.db
git commit -m "test: every LPO number on a document must have a record behind it

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 14: Documentation

**Files:**
- Modify: `docs/03-BACKEND-DATA.md` (the `client_lpos` section at line 104)
- Modify: `docs/07-FRONTEND-SUPPORT.md` (the `static/lpo.js` section at line 14)
- Modify: `docs/09-DATA-MODEL.md` (the `client_lpos` row at line 78)
- Modify: `docs/01-ARCHITECTURE.md` (the Impact Map)
- Modify: `docs/11-RUN-AND-MAINTAIN.md` (the test list)
- Modify: `AGENT-BOARD.md`

- [ ] **Step 1: Update each doc**

`03-BACKEND-DATA.md` - the new columns, `_lpo_status` being per kind,
`lpo_is_over_value`, `close_lpo`, `lpo_fulfillment`, `_lpo_months_unbilled`, the
`billing` bucket, and `_usage_key` now resolving through `client_aliases`.

`07-FRONTEND-SUPPORT.md` - the new form, `lpo_lines.js`, `lpo_picker.js`,
`lpoProgress`. **Keep every existing note about the viewer and mark placement** -
none of that changed and each line was won against a real failure.

`09-DATA-MODEL.md` - the `client_lpos` column list.

`01-ARCHITECTURE.md` Impact Map - add a row: *touch the LPO kinds, check*
`_lpo_status`, `lpo_is_over_value`, `_lpo_months_unbilled`, `lpoProgress` and
the picker's closed-status filter - **five places decide what a kind means and
they must agree.**

`11-RUN-AND-MAINTAIN.md` - add every new test file with its one-line purpose.

- [ ] **Step 2: Answer the two propagation questions on the board**

`AGENT-BOARD.md` - what this change made wrong, and where else it would help.
One candidate to raise with Waleed, with a count from his own data rather than a
vague maybe: **three older Abraj quotations disagree with their own client**
(`QUT-MT-Hanjun-041`, `QUT-MT-HDEC-041`, `QUT-MT-HDEC-059`, with 041 used
twice). They were reported on 2026-09-11 and deliberately not fixed. Once LPOs
carry lines, a quotation that disagrees with the LPO citing it becomes
detectable the same way.

- [ ] **Step 3: Verify the whole suite**

```bash
node tests/no_em_dash.test.js
```

Then the full Python run, one suite at a time (never two at once; delete any
stale `.test-work/` first).

- [ ] **Step 4: Commit**

```bash
git add docs AGENT-BOARD.md
git commit -m "docs: the LPO screen knows three kinds of PO and what is still owed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Deliberately not built

- **No per-LPO bank account or withholding override.** Client master data holds
  the receiving account and Waleed has one account per client. `ADM-0017` says
  the registered account cannot be changed, which argues for the client being
  the right home for it, not the PO.
- **No generated monthly invoice.** The app raises the reminder; Waleed writes
  the invoice. He chose this over a generated draft.
- **No LPO numbering.** We do not issue LPOs. The number is always the client's.
- **The mark-placement viewer is untouched.** Fractions-of-the-page placement,
  the real signature image inside the box, the `object-fit: contain` agreement
  with `lpo_ack._overlay_html`, and the file banner all stay exactly as they
  are. Each was fixed against a real failure.
