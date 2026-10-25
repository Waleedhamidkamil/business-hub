# Payment Methods and Per-Account Exchange Rates Implementation Plan

> **For agentic workers:** execute this plan inline with `superpowers:executing-plans`.
> **Do NOT use subagent-driven-development here** - `CLAUDE.md` rules it out for
> this repo: parallel agents against one shared live SQLite database is how the
> test-lock problem was created. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Each payment method carries its own IQD-per-USD rate; the account a
client is actually paid into decides the rate a document's profit is costed at;
the rate is stamped on the document at save so no figure ever moves afterwards.

**Architecture:** `doc_cost_meta` gains `account_key` + `account_rate`. A new
`backend/payment_accounts.py` owns "which account, and at what rate" for one
document. `database.doc_iqd_per_usd` consults the stamp before resolving live and
loses its two hidden rules (Eshraqat 1500, cash deals 1500). A migration stamps
every existing document with the rate the OLD rules give it, so history is frozen
before the rules are deleted. A Payments tab in main Settings edits the accounts.

**Tech Stack:** Python 3.13 + SQLite (no ORM), vanilla JS frontend, pywebview
bridge. Tests are `unittest` (`tests/*.test.py`) and hand-rolled Node assertions
(`tests/*.test.js`). No jsdom.

**Read first:** `docs/superpowers/specs/2026-09-21-payment-methods-and-per-account-rates-design.md`,
then `docs/03-BACKEND-DATA.md` section "Costing / profit engine".

**Repo rules that apply to every task:**
- NEVER use the character U+2014. `tests/no_em_dash.test.js` walks the repo.
- Files are CRLF. A multi-line Python replacement string written with `\n` will
  silently fail to match. Replace single lines, or write `\r\n` explicitly.
- Every new test must be mutation-checked: break the code it covers, confirm the
  test goes red, restore. A test that cannot fail is not a test.
- Waleed is colour blind. No state may be carried by colour alone.
- Verify before claiming done: command output in the same message as the claim.

---

### Task 0: Safety net before anything is touched

**Files:**
- Create: `scratchpad/rate_snapshot.py` (working file, not committed)
- Create: `scratchpad/rates-before.json`

- [ ] **Step 1: Write the snapshot script**

```python
# scratchpad/rate_snapshot.py
"""Every document's cost peg and profit, BEFORE the change. The migration test
compares against this file, so it has to be taken while the old rules are live."""
import json, sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from backend.profile_manager import manager as pm

pm().bootstrap_profiles(legacy_db=APP / "data" / "po_tool.db",
                        legacy_settings=APP / "data" / "settings.json",
                        legacy_assets=APP / "static")
from backend import config, database, profile_manager

TABLES = {"quote": ("quotes", "quote_no"),
          "dn": ("dns", "dn_no"),
          "inv": ("invoices", "inv_no")}

out = {}
start = pm().active_profile_id()
try:
    for pid in ("abraj", "eshraqat"):
        pm().activate(pid)
        config.reload_settings() if hasattr(config, "reload_settings") else None
        rows = {}
        with database._connect() as conn:
            for kind, (table, col) in TABLES.items():
                for r in conn.execute(f"SELECT {col} AS no FROM {table}"):
                    no = r["no"]
                    rate = database.doc_iqd_per_usd(kind, no)
                    try:
                        payload = database.load_doc_costing(kind, no) or {}
                        totals = payload.get("totals") or {}
                    except Exception as e:
                        totals = {"error": str(e)}
                    rows[f"{kind}:{no}"] = {
                        "rate": rate,
                        "profit": totals.get("profit"),
                        "cost": totals.get("cost"),
                        "revenue": totals.get("revenue"),
                    }
        out[pid] = rows
finally:
    pm().activate(start)

Path(APP / "scratchpad" / "rates-before.json").write_text(
    json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
print({pid: len(rows) for pid, rows in out.items()})
```

- [ ] **Step 2: Run it**

Run: `python scratchpad/rate_snapshot.py`
Expected: a dict of counts, e.g. `{'abraj': 199, 'eshraqat': 14}`, and
`scratchpad/rates-before.json` on disk. If `load_doc_costing` is not the right
entry point, find the real one with
`grep -n "def load_doc_costing\|def doc_costing" backend/database.py` and fix the
script before continuing. **Do not proceed without this file.**

- [ ] **Step 3: Back up both live databases**

```bash
mkdir -p data/master-data-backups
cp data/profiles/abraj/po_tool.db    data/master-data-backups/po_tool.abraj.pre-20260921-rates.db
cp data/profiles/eshraqat/po_tool.db data/master-data-backups/po_tool.eshraqat.pre-20260921-rates.db
```

Expected: both files exist. Confirm with `ls -la data/master-data-backups | tail -3`.

- [ ] **Step 4: Note the active profile and restore it at the end of every task**

Run: `grep active data/profile-registry.json`
Write the answer down. The suite can flip it; it must be left as found.

---

### Task 1: The two stamp columns

**Files:**
- Modify: `backend/database.py` (the `doc_cost_meta` block in `_SCHEMA`, near line 279)
- Modify: `backend/migrations.py` (new migration at the end of the file)
- Test: `tests/payment_account_rate.test.py` (created here, grown in later tasks)

- [ ] **Step 1: Write the failing test**

```python
"""The rate a document's costs convert at follows the ACCOUNT it is paid into,
and is frozen on the document when it is saved (2026-09-21).

Waleed: "if I made quotation in june for hdec and at that time the receiving bank
was bank of baghdad and the exchange rate was 1400 then in july I changed the
receiving bank to ADIB and made quotation, that quotation profit is calculated
and saved for 1500. When I go back to check the quotation from june it should be
saved and calculated on 1400."

Isolated - `isolated_client_data.activate()` before backend config is imported,
for the reason `tests/client_lpo_save.test.py` documents at length.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, profile_manager  # noqa: E402


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}. "
            "Stop: this suite would write into the live data.")


_assert_isolated()
database.init_db()


class TheStampColumnsExist(unittest.TestCase):
    def test_doc_cost_meta_carries_the_account_and_its_rate(self):
        with database._connect() as conn:
            cols = {r["name"] for r in
                    conn.execute("PRAGMA table_info(doc_cost_meta)").fetchall()}
        self.assertIn("account_key", cols,
                      "which account this document was costed against")
        self.assertIn("account_rate", cols,
                      "that account's rate at the moment it was saved - this is "
                      "what stops a historical profit figure moving")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `'account_key' not found in {...}`.

- [ ] **Step 3: Add the columns to `_SCHEMA`**

In `backend/database.py`, the `doc_cost_meta` CREATE TABLE currently ends:

```
    rate         REAL,
    PRIMARY KEY (kind, doc_no)
);
```

Replace those three lines with:

```
    rate         REAL,
    -- 2026-09-21 - the payment ACCOUNT this document was costed against, and
    -- that account's IQD-per-USD rate at the moment it was saved. The rate is
    -- stamped so a figure cannot move when an account's rate is edited later,
    -- or when a client's receiving account changes. NULL = never stamped.
    -- Declared here as well as in the migration: a NEW profile database never
    -- runs migrations.
    account_key  TEXT,
    account_rate REAL,
    PRIMARY KEY (kind, doc_no)
);
```

- [ ] **Step 4: Add the migration**

Append to `backend/migrations.py` (follow the file's existing `@migration`
decorator style):

```python
@migration("2026-09-21_doc_cost_account")
def _doc_cost_account(conn: sqlite3.Connection) -> str:
    """Record WHICH account a document was costed against, and at what rate.

    Separate from `doc_cost_meta.rate`, which is a rate the user pinned by hand
    on the Costing screen and still outranks everything. This pair is written
    automatically on every save.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(doc_cost_meta)").fetchall()}
    added = []
    if "account_key" not in cols:
        conn.execute("ALTER TABLE doc_cost_meta ADD COLUMN account_key TEXT")
        added.append("account_key")
    if "account_rate" not in cols:
        conn.execute("ALTER TABLE doc_cost_meta ADD COLUMN account_rate REAL")
        added.append("account_rate")
    return f"added doc_cost_meta.{', '.join(added)}" if added else "already present"
```

- [ ] **Step 5: Run the test again**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 6: Mutation-check**

Comment out the `account_rate` ALTER and the `_SCHEMA` line, re-run, confirm
FAIL, restore, confirm OK. Paste both outputs.

- [ ] **Step 7: Migrate the live databases and confirm nothing else moved**

Run: `python -c "from backend import migrations; print(migrations.run_all())"`
Expected: the new migration reported as applied, no failures.

- [ ] **Step 8: Commit**

```bash
git add backend/database.py backend/migrations.py tests/payment_account_rate.test.py
git commit -m "feat: doc_cost_meta records the account a document was costed against"
```

---

### Task 2: `payment_accounts.py` - which account, and at what rate

**Files:**
- Create: `backend/payment_accounts.py`
- Test: `tests/payment_account_rate.test.py` (add a class)

This module is the only place that answers "which account does this document
belong to". Keeping it out of `database.py` matters: `database.py` is already
5000+ lines, and this needs `client_master` and `config`, which makes the import
graph easier to reason about in its own file.

- [ ] **Step 1: Write the failing test**

Add to `tests/payment_account_rate.test.py`, above `if __name__`:

```python
from backend import client_master, payment_accounts  # noqa: E402


class AccountsCase(unittest.TestCase):
    """Two accounts at different rates, and a client paid into one of them."""

    BANKS = [
        {"key": "bob", "label": "Bank of Baghdad", "currency": "USD",
         "iqd_per_usd": 1400},
        {"key": "adib", "label": "ADIB", "currency": "USD",
         "iqd_per_usd": 1500},
        {"key": "cash", "label": "Cash", "currency": "USD",
         "iqd_per_usd": 1500},
    ]

    def setUp(self):
        _assert_isolated()
        payment_accounts._accounts_override = self.BANKS      # test seam
        self.addCleanup(lambda: setattr(payment_accounts, "_accounts_override", None))


class WhichRateAnAccountCarries(AccountsCase):
    def test_a_known_account_returns_its_own_rate(self):
        self.assertEqual(1400, payment_accounts.rate_for_account("bob"))
        self.assertEqual(1500, payment_accounts.rate_for_account("adib"))

    def test_the_key_is_matched_case_insensitively(self):
        """settings.json has "ADIB" while a client record may hold "adib"."""
        self.assertEqual(1500, payment_accounts.rate_for_account("ADIB"))

    def test_an_unknown_account_falls_back_rather_than_guessing(self):
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("no-such-bank"))

    def test_an_account_with_no_rate_falls_back(self):
        payment_accounts._accounts_override = [{"key": "bob", "label": "B"}]
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("bob"))

    def test_a_zero_or_negative_rate_is_not_a_rate(self):
        payment_accounts._accounts_override = [
            {"key": "bob", "iqd_per_usd": 0},
            {"key": "adib", "iqd_per_usd": -5},
        ]
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("bob"))
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("adib"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `ImportError: cannot import name 'payment_accounts'`.

- [ ] **Step 3: Write the module**

```python
"""Which payment account a document is costed against, and at what rate.

One client is paid into one account, and that account has its own IQD-per-USD
rate: Bank of Baghdad at 1400, ADIB at 1500. The rate a document's COSTS convert
at follows the account the money actually arrives in.

This is deliberately the only place that answers that question. Before
2026-09-21 the answer was spread across two invisible rules inside
`database.doc_iqd_per_usd` - "Eshraqat is always 1500" and "a cash deal is 1500"
- which produced a number on the Costing screen with no way to see why.

Scope: the PROFIT maths only. What an invoice bills the client at is
`invoices.sell_iqd_per_usd` and is not this module's business.
"""
from __future__ import annotations

from typing import Any

from . import config

# Set by tests to stand in for the profile's settings. None in the real app.
_accounts_override: list[dict[str, Any]] | None = None

# The rate used when no account can be determined. Waleed, 2026-09-21: "1500".
# Overridable per profile through `invoice.fallback_iqd_per_usd` so it is a
# number somebody can see and change, not one buried in code.
DEFAULT_FALLBACK = 1500.0


def _num(value: Any) -> float | None:
    """A usable rate, or None. Zero and negatives are not rates."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def accounts() -> list[dict[str, Any]]:
    """The active profile's payment methods."""
    if _accounts_override is not None:
        return list(_accounts_override)
    banks = getattr(config, "INV_BANK_INFO", None) or []
    return [b for b in banks if isinstance(b, dict)]


def fallback_rate() -> float:
    """The rate for a document with no account behind it."""
    section = getattr(config, "INV_FALLBACK_IQD_PER_USD", None)
    return _num(section) or DEFAULT_FALLBACK


def find_account(key: str) -> dict[str, Any] | None:
    """The account with this key, matched case-insensitively.

    settings.json holds "ADIB" while a client record may hold "adib"; the two
    have always meant the same account and a lookup that says otherwise silently
    changes a rate.
    """
    want = str(key or "").strip().casefold()
    if not want:
        return None
    for account in accounts():
        if str(account.get("key") or "").strip().casefold() == want:
            return account
    return None


def rate_for_account(key: str) -> float:
    """This account's rate, or the fallback when it has none."""
    account = find_account(key)
    if not account:
        return fallback_rate()
    return _num(account.get("iqd_per_usd")) or fallback_rate()
```

- [ ] **Step 4: Run the test**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 5: Mutation-check**

Change `find_account` to compare `==` without `casefold()`; the
case-insensitivity test must go red. Restore. Then make `_num` accept zero; the
zero/negative test must go red. Restore. Paste both outputs.

- [ ] **Step 6: Commit**

```bash
git add backend/payment_accounts.py tests/payment_account_rate.test.py
git commit -m "feat: each payment account carries its own exchange rate"
```

---

### Task 3: Which account a DOCUMENT belongs to

**Files:**
- Modify: `backend/payment_accounts.py`
- Test: `tests/payment_account_rate.test.py`

- [ ] **Step 1: Write the failing test**

```python
class WhichAccountADocumentBelongsTo(AccountsCase):
    """Invoice: its own selected account. Quotation and DN: the client's."""

    @classmethod
    def setUpClass(cls):
        _assert_isolated()
        found = client_master.get_record("HDEC") or {}
        existing = found.get("record") or {}
        payload = {"name": "HDEC",
                   "legal_name": "Hyundai Engineering and Construction Co., Ltd",
                   "aliases": [], "contacts": [], "addresses": [],
                   "defaults": {"bank_key": "bob"}}
        if existing.get("id"):
            payload["id"] = existing["id"]
            payload["updated_at"] = existing.get("updated_at")
        res = client_master.save_record(payload)
        assert res.get("ok"), res

    def test_a_quotation_uses_the_clients_receiving_account(self):
        key = payment_accounts.account_for_document("quote", client_name="HDEC")
        self.assertEqual("bob", key)

    def test_any_spelling_of_the_client_finds_the_same_account(self):
        key = payment_accounts.account_for_document(
            "quote", client_name="Hyundai Engineering and Construction Co., Ltd")
        self.assertEqual("bob", key,
                         "the identity index already says these are one client")

    def test_an_invoice_uses_the_account_selected_on_it(self):
        key = payment_accounts.account_for_document(
            "inv", client_name="HDEC", bank_key="adib")
        self.assertEqual("adib", key,
                         "that is the account the money arrives in")

    def test_an_invoice_with_no_account_of_its_own_uses_the_clients(self):
        key = payment_accounts.account_for_document("inv", client_name="HDEC",
                                                    bank_key="")
        self.assertEqual("bob", key)

    def test_an_unknown_client_has_no_account(self):
        self.assertEqual("", payment_accounts.account_for_document(
            "quote", client_name="Nobody Ltd"))

    def test_the_rate_follows_the_account(self):
        self.assertEqual(1400, payment_accounts.rate_for_document(
            "quote", client_name="HDEC"))
        self.assertEqual(1500, payment_accounts.rate_for_document(
            "inv", client_name="HDEC", bank_key="adib"))
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_document(
                             "quote", client_name="Nobody Ltd"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `module 'backend.payment_accounts' has no attribute 'account_for_document'`.

- [ ] **Step 3: Implement**

Append to `backend/payment_accounts.py`:

```python
def client_account(client_name: str) -> str:
    """The account this client is paid into, from Clients Master Data.

    Resolved through the identity index, so `HDEC` and `Hyundai Engineering and
    Construction Co., Ltd` reach the same record. A client with no record, or an
    unreadable client store, returns "" rather than raising: the Costing screen
    runs on every keystroke and losing it is worse than losing a rate.
    """
    name = str(client_name or "").strip()
    if not name:
        return ""
    try:
        from . import client_master
        found = client_master.get_record(name) or {}
    except Exception:
        return ""
    record = found.get("record") or {}
    defaults = record.get("defaults") or {}
    return str(defaults.get("bank_key") or "").strip()


def account_for_document(kind: str, client_name: str = "",
                         bank_key: str = "") -> str:
    """Which account this document is costed against.

    An INVOICE names its own account and that wins: it is the account receiving
    the money, even when it differs from the client's usual one. A quotation or
    delivery note has no account of its own, so the client's stands.
    """
    chosen = str(bank_key or "").strip()
    if chosen and find_account(chosen):
        return chosen
    return client_account(client_name)


def rate_for_document(kind: str, client_name: str = "",
                      bank_key: str = "") -> float:
    """The rate this document's costs convert at, resolved live."""
    return rate_for_account(account_for_document(kind, client_name, bank_key))
```

- [ ] **Step 4: Run the test**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 5: Mutation-check**

Make `account_for_document` ignore `bank_key` (return `client_account(...)`
always); `test_an_invoice_uses_the_account_selected_on_it` must go red. Restore
and paste both outputs.

- [ ] **Step 6: Commit**

```bash
git add backend/payment_accounts.py tests/payment_account_rate.test.py
git commit -m "feat: the client's receiving account decides a document's rate"
```

---

### Task 4: Stamp the rate on save, and read it back

**Files:**
- Modify: `backend/database.py` (new `get_account_stamp` / `set_account_stamp` beside `get_cost_rate`)
- Test: `tests/payment_account_rate.test.py`

- [ ] **Step 1: Write the failing test**

```python
class StampingFreezesHistory(AccountsCase):
    """Waleed's June/July example, verbatim."""

    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")

    def test_a_stamp_round_trips(self):
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual(("bob", 1400.0),
                         database.get_account_stamp("quote", "QT-JUNE"))

    def test_an_unstamped_document_says_so(self):
        self.assertEqual(("", None),
                         database.get_account_stamp("quote", "QT-NEVER"))

    def test_re_saving_re_stamps(self):
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        database.set_account_stamp("quote", "QT-JUNE", "adib", 1500)
        self.assertEqual(("adib", 1500.0),
                         database.get_account_stamp("quote", "QT-JUNE"))

    def test_a_stamp_does_not_disturb_a_hand_pinned_rate(self):
        database.set_cost_rate("quote", "QT-JUNE", 1234)
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual(1234, database.get_cost_rate("quote", "QT-JUNE"),
                         "the rate the user typed by hand still outranks it")

    def test_a_stamp_does_not_disturb_the_cost_currency(self):
        database.set_cost_currency("quote", "QT-JUNE", "IQD")
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual("IQD", database.get_cost_currency("quote", "QT-JUNE"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `no attribute 'set_account_stamp'`.

- [ ] **Step 3: Implement**

Add to `backend/database.py`, immediately after `set_cost_rate`:

```python
def get_account_stamp(kind: str, doc_no: str) -> tuple[str, float | None]:
    """The account this document was costed against and the rate it used.

    `("", None)` means never stamped - every document that existed before
    2026-09-21 until the migration runs, and any document not yet saved.
    """
    doc_no = (doc_no or "").strip()
    if not doc_no:
        return "", None
    try:
        with _connect() as conn:
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
    account carried in June after that account's rate, or the client's account,
    changes in July.
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
```

- [ ] **Step 4: Run the test**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 5: Mutation-check**

Change the `ON CONFLICT` clause to also write `currency = 'USD'`; the
cost-currency test must go red. Restore and paste both outputs.

- [ ] **Step 6: Commit**

```bash
git add backend/database.py tests/payment_account_rate.test.py
git commit -m "feat: freeze the account and rate a document was costed against"
```

---

### Task 5: The migration that protects history

**Do this task BEFORE Task 6.** Task 6 deletes the old rules; this one freezes
their answers first. In the wrong order every Eshraqat profit figure moves.

**Files:**
- Modify: `backend/migrations.py`
- Test: `tests/payment_account_rate.test.py`

- [ ] **Step 1: Write the failing test**

```python
class TheMigrationFreezesWhatTheOldRulesSaid(AccountsCase):
    """Stamping has to happen while the OLD rules still answer, so that deleting
    them cannot move a figure. This proves the migration writes a stamp for a
    document that has none, and leaves an existing one alone."""

    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")
            conn.execute("DELETE FROM quotes")

    def _seed_quote(self, no):
        with database._connect() as conn:
            conn.execute(
                "INSERT INTO quotes(quote_no, quote_date, quote_type, year, month, "
                "client_name, pdf_path, rows_json, bid_status, reference, "
                "updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (no, "9/7/2026", "materials", 2026, 7, "HDEC",
                 r"D:\docs\%s.pdf" % no, "[]", "", "",
                 "2026-09-21T10:00:00", "2026-09-21T10:00:00"))

    def test_an_unstamped_document_is_stamped_with_the_old_answer(self):
        from backend import migrations
        self._seed_quote("QT-OLD")
        with database._connect() as conn:
            migrations._stamp_document_cost_rates(conn)
        key, rate = database.get_account_stamp("quote", "QT-OLD")
        self.assertIsNotNone(rate, "every existing document must end up stamped")

    def test_a_document_that_is_already_stamped_is_left_alone(self):
        from backend import migrations
        self._seed_quote("QT-DONE")
        database.set_account_stamp("quote", "QT-DONE", "adib", 1500)
        with database._connect() as conn:
            migrations._stamp_document_cost_rates(conn)
        self.assertEqual(("adib", 1500.0),
                         database.get_account_stamp("quote", "QT-DONE"),
                         "the migration is idempotent - running it twice must "
                         "not overwrite a stamp")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `no attribute '_stamp_document_cost_rates'`.

- [ ] **Step 3: Write the migration**

Append to `backend/migrations.py`:

```python
@migration("2026-09-21_stamp_document_cost_rates")
def _stamp_document_cost_rates(conn: sqlite3.Connection) -> str:
    """Freeze every existing document at the rate the OLD rules gave it.

    `doc_iqd_per_usd` is about to lose two rules - "Eshraqat is always 1500" and
    "a deal paid in cash is 1500" - in favour of the rate carried by the account
    the client is actually paid into. Every Eshraqat document relies on the
    first and every cash deal on the second, so deleting them without stamping
    first would silently move real profit figures.

    The old rules are reimplemented HERE rather than imported, because
    `database.py` will not contain them any more. They are, in order:
      1. doc_cost_meta.rate       - pinned by hand; left alone, it still wins
      2. invoices.sell_iqd_per_usd - a rate the invoice recorded
      3. 1500 when the profile is eshraqat
      4. 1500 when the deal is paid in cash
      5. the profile peg: 1500 eshraqat / 1400 abraj

    Rules 3 to 5 are what this writes. A document already stamped is skipped,
    so the migration is safe to re-run.
    """
    from . import config

    cash = 1500.0
    profile = str(getattr(config, "ACTIVE_PROFILE", "") or "").strip().lower()
    peg = cash if profile == "eshraqat" else float(getattr(config, "IQD_PER_USD", 1400.0))

    # Rule 4 - which deals were settled in cash. Reproduced with one flat query
    # rather than the deal-walk in database.py: a cash INVOICE re-pegs its whole
    # deal, so the root quotation and that quote's DNs are cash too.
    cash_quotes: set[str] = set()
    cash_invs: set[str] = set()
    try:
        for row in conn.execute(
                "SELECT inv_no, source_quote_no, payment_bank_key FROM invoices"):
            if str(row["payment_bank_key"] or "").strip().lower() != "cash":
                continue
            cash_invs.add(str(row["inv_no"] or ""))
            root = str(row["source_quote_no"] or "").strip()
            if root:
                cash_quotes.add(root)
    except sqlite3.OperationalError:
        pass

    cash_dns: set[str] = set()
    try:
        for row in conn.execute("SELECT dn_no, source_quote_no FROM dns"):
            if str(row["source_quote_no"] or "").strip() in cash_quotes:
                cash_dns.add(str(row["dn_no"] or ""))
    except sqlite3.OperationalError:
        pass

    def old_rate(kind: str, doc_no: str, sell_peg: float | None) -> float:
        if sell_peg and sell_peg > 0:
            return sell_peg
        if profile == "eshraqat":
            return cash
        if kind == "inv" and doc_no in cash_invs:
            return cash
        if kind == "quote" and doc_no in cash_quotes:
            return cash
        if kind == "dn" and doc_no in cash_dns:
            return cash
        return peg

    done = {(r["kind"], r["doc_no"]) for r in conn.execute(
        "SELECT kind, doc_no FROM doc_cost_meta WHERE account_rate IS NOT NULL")}

    stamped = 0
    plan = (("quote", "quotes", "quote_no", False),
            ("dn", "dns", "dn_no", False),
            ("inv", "invoices", "inv_no", True))
    for kind, table, col, has_peg in plan:
        cols = f"{col} AS no" + (", sell_iqd_per_usd AS peg" if has_peg else "")
        try:
            rows = conn.execute(f"SELECT {cols} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            doc_no = str(row["no"] or "").strip()
            if not doc_no or (kind, doc_no) in done:
                continue
            sell_peg = None
            if has_peg:
                try:
                    sell_peg = float(row["peg"] or 0) or None
                except (TypeError, ValueError):
                    sell_peg = None
            rate = old_rate(kind, doc_no, sell_peg)
            # account_key is left EMPTY on purpose: these documents predate the
            # idea, and inventing an account for them would be a guess printed
            # on the Costing screen. The rate is what protects the figure.
            conn.execute(
                "INSERT INTO doc_cost_meta(kind, doc_no, account_key, account_rate) "
                "VALUES(?, ?, '', ?) "
                "ON CONFLICT(kind, doc_no) DO UPDATE SET account_rate = excluded.account_rate "
                "WHERE doc_cost_meta.account_rate IS NULL",
                (kind, doc_no, rate),
            )
            stamped += 1
    return f"stamped {stamped} document(s) at their pre-2026-09-21 rate"
```

- [ ] **Step 4: Run the test**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 5: Mutation-check**

Delete the `WHERE doc_cost_meta.account_rate IS NULL` guard; the idempotence test
must go red. Restore and paste both outputs.

- [ ] **Step 6: Run it against the live databases**

Run: `python -c "from backend import migrations; print(migrations.run_all())"`
Expected: `stamped N document(s)`, N matching the counts from Task 0 Step 2.

Then, for each profile, confirm no document is left unstamped:

```bash
python -c "
import sqlite3
for p in ['data/profiles/abraj/po_tool.db','data/profiles/eshraqat/po_tool.db']:
    c=sqlite3.connect(p)
    n=c.execute('select count(*) from doc_cost_meta where account_rate is not null').fetchone()[0]
    docs=sum(c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('quotes','dns','invoices'))
    print(p, 'stamped', n, 'of', docs)
"
```

Expected: stamped == docs for both.

- [ ] **Step 7: Commit**

```bash
git add backend/migrations.py tests/payment_account_rate.test.py
git commit -m "feat: stamp every existing document before the old rate rules go"
```

---

### Task 6: Rewrite `doc_iqd_per_usd`

**Files:**
- Modify: `backend/database.py` (`doc_iqd_per_usd`)
- Test: `tests/payment_account_rate.test.py`
- Test: `tests/rates_did_not_move.test.py` (new)

- [ ] **Step 1: Write the resolution-order test**

```python
class TheRateChain(AccountsCase):
    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")

    def test_a_hand_pinned_rate_wins_over_everything(self):
        database.set_account_stamp("quote", "QT-1", "adib", 1500)
        database.set_cost_rate("quote", "QT-1", 1234)
        self.assertEqual(1234, database.doc_iqd_per_usd("quote", "QT-1"))

    def test_the_stamp_wins_over_the_live_account(self):
        database.set_account_stamp("quote", "QT-1", "bob", 1400)
        self.assertEqual(1400, database.doc_iqd_per_usd("quote", "QT-1"),
                         "this is what stops June's quotation moving to 1500 "
                         "when the client's account changes in July")

    def test_an_unstamped_document_falls_back(self):
        self.assertEqual(payment_accounts.fallback_rate(),
                         database.doc_iqd_per_usd("quote", "QT-UNSAVED"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL on `test_the_stamp_wins_over_the_live_account` - the stamp is not
consulted yet.

- [ ] **Step 3: Replace the function**

In `backend/database.py`, replace the whole body of `doc_iqd_per_usd` with:

```python
def doc_iqd_per_usd(kind: str, doc_no: str) -> float:
    """The IQD-per-USD rate ONE document's costs convert at.

    The account the client is paid into decides it: Bank of Baghdad at 1400,
    ADIB at 1500 (2026-09-21). In order:

      1. a rate PINNED by hand on the Costing screen - the user typed it while
         looking at the figure it produced, which is as explicit as it gets;
      2. the rate STAMPED on the document when it was saved - this is what keeps
         a June quotation at the rate its account carried in June;
      3. the account's rate, resolved live, for a document not yet saved;
      4. the fallback rate.

    Two rules were deleted here on 2026-09-21: "Eshraqat is always 1500" and "a
    deal paid in cash is 1500". Both produced a number on screen with no way to
    see why, and cash is now simply a payment method with a rate you can edit.
    Every document that existed then carries a stamp written by
    `2026-09-21_stamp_document_cost_rates` at the rate those rules gave it, so
    deleting them moved no figure.

    `invoices.sell_iqd_per_usd` is NOT consulted: it is the rate the client was
    BILLED at, which a deal may legitimately differ from on the cost side.
    """
    from . import payment_accounts
    doc_no = (doc_no or "").strip()
    if doc_no and kind in ("quote", "dn", "inv"):
        pinned = get_cost_rate(kind, doc_no)
        if pinned:
            return pinned
        _key, stamped = get_account_stamp(kind, doc_no)
        if stamped:
            return stamped
    return payment_accounts.fallback_rate()
```

Note what this deliberately does NOT do: resolve the live account. A saved
document is always stamped, and an UNSAVED one has no `doc_no` to look up - the
editor resolves its own live rate through `Api.rate_for_document` (Task 7) and
shows it, then the save stamps it. Keeping the live lookup out of here keeps this
a light SQL call, which its callers depend on.

- [ ] **Step 4: Run the test**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

- [ ] **Step 5: Write the "nothing moved" test**

```python
"""Deleting the two hidden rate rules must not move one historical figure.

`scratchpad/rates-before.json` was taken while the old rules were live (see the
plan's Task 0). This compares every document's rate and profit against it. If
this file fails, the migration missed documents - do not "fix" it by regenerating
the snapshot.
"""
import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
BEFORE = APP_ROOT / "scratchpad" / "rates-before.json"

from backend.profile_manager import manager as pm  # noqa: E402


@unittest.skipUnless(BEFORE.exists(),
                     f"NOTHING WAS CHECKED - {BEFORE} is missing. It is taken "
                     "once, before the rate rules change. See the plan Task 0.")
class NothingMoved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pm().bootstrap_profiles(legacy_db=APP_ROOT / "data" / "po_tool.db",
                                legacy_settings=APP_ROOT / "data" / "settings.json",
                                legacy_assets=APP_ROOT / "static")
        cls.before = json.loads(BEFORE.read_text(encoding="utf-8"))
        cls.start = pm().active_profile_id()

    @classmethod
    def tearDownClass(cls):
        pm().activate(cls.start)

    def test_every_document_reports_the_same_rate_and_profit(self):
        from backend import database
        moved = []
        for pid, rows in self.before.items():
            pm().activate(pid)
            for ref, was in rows.items():
                kind, no = ref.split(":", 1)
                now_rate = database.doc_iqd_per_usd(kind, no)
                if abs(float(was["rate"]) - float(now_rate)) > 1e-9:
                    moved.append(f"{pid} {ref}: rate {was['rate']} -> {now_rate}")
                    continue
                if was.get("profit") is None:
                    continue
                try:
                    totals = (database.load_doc_costing(kind, no) or {}).get("totals") or {}
                except Exception:
                    continue
                if totals.get("profit") is None:
                    continue
                if abs(float(was["profit"]) - float(totals["profit"])) > 0.005:
                    moved.append(f"{pid} {ref}: profit {was['profit']} -> {totals['profit']}")
        self.assertEqual([], moved,
                         "these documents changed when the rate rules did:\n  "
                         + "\n  ".join(moved[:40]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 6: Run it**

Run: `python tests/rates_did_not_move.test.py`
Expected: `OK`. If it lists documents, the migration missed them - fix Task 5,
re-run the migration, re-run this. Do NOT regenerate the snapshot.

- [ ] **Step 7: Mutation-check**

In `doc_iqd_per_usd`, skip the stamp (comment out the `if stamped: return`
branch). `rates_did_not_move` must go red with a list of documents. Restore and
paste both outputs.

- [ ] **Step 8: Commit**

```bash
git add backend/database.py tests/payment_account_rate.test.py tests/rates_did_not_move.test.py
git commit -m "feat: the account's rate decides the cost peg, and the stamp freezes it"
```

---

### Task 7: Stamp on save, and expose the live rate to the editor

**Files:**
- Modify: `backend/api.py` (`save_quote`, `save_dn`, `save_inv`; add `rate_for_document`)
- Test: `tests/payment_account_rate.test.py`

- [ ] **Step 1: Find the three save paths**

Run: `grep -n "    def save_quote\|    def save_dn\|    def save_inv" backend/api.py`
Expected: three line numbers. In each, find where the record has been written and
the method is about to return its success dict.

- [ ] **Step 2: Write the failing test**

```python
class SavingStampsTheDocument(AccountsCase):
    def test_the_api_exposes_the_live_rate_for_the_editor(self):
        from backend.api import Api
        res = Api().rate_for_document("quote", "HDEC", "")
        self.assertEqual(1400, res["rate"])
        self.assertEqual("bob", res["account_key"])
        self.assertEqual("Bank of Baghdad", res["account_label"],
                         "the Costing screen names the account beside the rate, "
                         "so a number always says where it came from")

    def test_an_unknown_client_gets_the_fallback_and_says_so(self):
        from backend.api import Api
        res = Api().rate_for_document("quote", "Nobody Ltd", "")
        self.assertEqual(payment_accounts.fallback_rate(), res["rate"])
        self.assertEqual("", res["account_key"])
        self.assertEqual("", res["account_label"])
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python tests/payment_account_rate.test.py`
Expected: FAIL, `'Api' object has no attribute 'rate_for_document'`.

- [ ] **Step 4: Add the bridge method**

In `backend/api.py`, beside the other costing methods:

```python
    def rate_for_document(self, kind: str, client_name: str = "",
                          bank_key: str = "") -> dict:
        """The rate this document's costs WOULD convert at right now, and the
        account it comes from, for the Costing screen's readout."""
        from . import payment_accounts
        key = payment_accounts.account_for_document(
            kind or "", client_name or "", bank_key or "")
        account = payment_accounts.find_account(key) or {}
        return {"ok": True,
                "rate": payment_accounts.rate_for_account(key),
                "account_key": key,
                "account_label": str(account.get("label") or "")}
```

- [ ] **Step 5: Stamp in each save path**

In `save_quote`, `save_dn` and `save_inv`, immediately before the success return,
add (substituting the right number variable and client variable for each):

```python
            # Freeze the account and rate this document was costed against, so
            # the figure cannot move when an account's rate, or the client's
            # account, changes later.
            try:
                from . import payment_accounts
                acc = payment_accounts.account_for_document(
                    "quote", client_name, "")           # "dn" / "inv" + bank key
                database.set_account_stamp(
                    "quote", quote_no, acc,
                    payment_accounts.rate_for_account(acc))
            except Exception:
                pass            # a save must never fail over a rate stamp
```

For `save_inv` pass the invoice's own account: `inv.get("bank") or inv.get("payment_bank_key") or ""`.
Confirm the actual key name first with
`grep -n "payment_bank_key\|\"bank\"" backend/api.py | sed -n '1,12p'`.

- [ ] **Step 6: Run the tests**

Run: `python tests/payment_account_rate.test.py`
Expected: `OK`.

Run: `python tests/client_lpo_save.test.py && python smoke_test.py && python smoke_quote.py`
Expected: OK, "Smoke test passed.", "Smoke quote passed." The smokes save real
documents, so they exercise the stamp.

- [ ] **Step 7: Mutation-check**

Remove the stamp from `save_quote`; add a test that saves a quotation and asserts
`get_account_stamp` is non-empty, confirm it goes red, restore.

- [ ] **Step 8: Commit**

```bash
git add backend/api.py tests/payment_account_rate.test.py
git commit -m "feat: every save stamps the account and rate it was costed at"
```

---

### Task 8: The Payments tab

**Files:**
- Modify: `templates/index.html` (settings nav around line 648; a new panel after the `layout` panel)
- Modify: `static/app.js` (settings nav wiring is around line 3949)
- Modify: `static/doc_settings.js` (remove `renderBankList`, add the pointer)
- Modify: `backend/doc_settings.py` (declare `fallback_iqd_per_usd`)
- Test: `tests/payments_settings_tab.test.js` (new)

- [ ] **Step 1: Write the failing test**

```js
/* The payment methods are managed in ONE place (2026-09-21).

   They used to be editable only from the gear on the invoice editor, which is
   not where anybody looks for a bank account, and quotations - which now depend
   on a client's account for their exchange rate - had no way to reach them at
   all. Two editors for one list is how they drift. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const HTML = fs.readFileSync(path.join(root, "templates", "index.html"), "utf8");
const DOCSET = fs.readFileSync(path.join(root, "static", "doc_settings.js"), "utf8");
const APP = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("Settings has a Payments section", () => {
    assert.ok(/data-sect="payments"/.test(HTML), "no nav item");
    assert.ok(/data-panel="payments"/.test(HTML), "no panel");
});

check("the rate is edited per account, beside the account", () => {
    const at = HTML.indexOf('data-panel="payments"');
    assert.ok(at > 0, "no payments panel");
    const panel = HTML.slice(at, at + 4000);
    assert.ok(/payAccounts|pmAccounts/.test(panel),
        "the panel needs a container the editor renders accounts into");
    assert.ok(/fallback/i.test(panel),
        "the rate used when no account is known must be visible and editable - "
        + "a rate nobody can see is how the two hidden rules got there");
});

check("the invoice gear no longer edits banks", () => {
    assert.ok(!/function renderBankList/.test(DOCSET),
        "two editors for one list is how they drift apart");
    assert.ok(/Settings/.test(DOCSET) && /Payments/.test(DOCSET),
        "it should point at where the accounts now live");
});

check("the accounts editor is wired", () => {
    assert.ok(/renderPaymentAccounts|paymentsPanel/.test(APP),
        "nothing renders the payments panel");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
```

- [ ] **Step 2: Run it and watch it fail**

Run: `node tests/payments_settings_tab.test.js`
Expected: 4 FAIL.

- [ ] **Step 3: Add the nav item**

In `templates/index.html`, after the `data-sect="layout"` button and before
`data-sect="about"`:

```html
            <button type="button" class="settings-nav-item" data-sect="payments">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>
                <span>Payments</span>
            </button>
```

- [ ] **Step 4: Add the panel**

After the closing `</div>` of `data-panel="layout"`:

```html
        <div class="settings-panel" data-panel="payments" hidden>
        <section class="settings-card">
            <header>
                <h3>Payment accounts</h3>
                <p>The accounts money arrives in. Each one carries its own
                   exchange rate, and that rate is what a document's costs are
                   converted at when the client is paid into it.</p>
            </header>
            <div class="field" id="payAccounts"></div>
            <div class="field">
                <button type="button" id="payAddAccount" class="ghost">Add an account</button>
            </div>
        </section>
        <section class="settings-card">
            <header>
                <h3>When no account is known</h3>
                <p>Used for a client with no receiving account set in Master Data.</p>
            </header>
            <label class="field">
                <span>1 USD =</span>
                <input id="payFallbackRate" type="number" min="1" step="1" inputmode="decimal">
                <small>IQD. Applies to the profit figure only, never to what an
                       invoice bills.</small>
            </label>
        </section>
        <div class="field">
            <button type="button" id="payAccountsSave" class="primary">Save payment accounts</button>
        </div>
        </div>
```

- [ ] **Step 5: Wire it in `static/app.js`**

Write `renderPaymentAccounts()` next to the existing settings wiring (around line
3949). It reads `api().get_doc_settings("invoice")`, renders one card per entry
of `settings.bank_info` with inputs for label, bank name, branch, SWIFT, account
number, IBAN, currency and **1 USD = ___ IQD**, plus a Remove button; the Save
button sends the whole list back through
`api().save_doc_settings("invoice", {bank_info: [...], fallback_iqd_per_usd: n})`
and toasts the result. Refuse a rate that is blank, zero or negative with a toast
naming the account, and do not save until it is fixed.

- [ ] **Step 6: Declare the new setting**

In `backend/doc_settings.py`, add to `INV_FIELDS` beside `bank_info`:

```python
    _f("fallback_iqd_per_usd", cast="float", target="INV_FALLBACK_IQD_PER_USD"),
```

- [ ] **Step 7: Remove the bank editor from the invoice modal**

In `static/doc_settings.js`, delete `renderBankList` and `renderBankCard`, drop
`banks` from line 401 and the `payload.bank_info` block around line 580, and put a
line in the invoice section of that modal: `Payment accounts and their exchange
rates are in Settings > Payments.`

- [ ] **Step 8: Seed both profiles' settings**

```bash
python -c "
import json
for p in ['data/profiles/abraj/settings.json','data/profiles/eshraqat/settings.json']:
    d=json.load(open(p,encoding='utf-8')); inv=d.setdefault('invoice',{})
    for b in inv.get('bank_info',[]):
        b.setdefault('iqd_per_usd', 1500 if 'abraj' not in p else 1400)
    inv.setdefault('fallback_iqd_per_usd', 1500)
    json.dump(d, open(p,'w',encoding='utf-8'), indent=2, ensure_ascii=False)
    print(p, [(b['key'], b['iqd_per_usd']) for b in inv.get('bank_info',[])])
"
```

Then set the real rates in the app: Bank of Baghdad 1400, ADIB 1500.

- [ ] **Step 9: Run the tests**

Run: `node tests/payments_settings_tab.test.js`
Expected: all ok.

- [ ] **Step 10: Mutation-check**

Delete the `data-sect="payments"` attribute; the first check must go red. Restore.

- [ ] **Step 11: Commit**

```bash
git add templates/index.html static/app.js static/doc_settings.js backend/doc_settings.py data/profiles tests/payments_settings_tab.test.js
git commit -m "feat: a Payments tab owns the accounts and their exchange rates"
```

---

### Task 9: The Costing screen says where the rate came from

**Files:**
- Modify: `static/app.js` (the "Costs entered in IQD, converted at 1 $ = ..." readout)
- Test: `tests/payments_settings_tab.test.js` (add a check)

- [ ] **Step 1: Find the readout**

Run: `grep -rn "converted at" static/*.js`

- [ ] **Step 2: Add the failing check**

```js
check("the rate readout names the account it came from", () => {
    assert.ok(/account_label|accountLabel/.test(APP),
        "the Costing screen shows a converted figure; it must say which account "
        + "that rate belongs to, or the number has no explanation");
});
```

- [ ] **Step 3: Run it, watch it fail, implement**

Extend the readout to `Costs entered in IQD, converted at 1 $ = 1,400 IQD
(Bank of Baghdad)`, and for a saved document append `, recorded when this
document was saved`. Source the label from `api().rate_for_document(...)`.

- [ ] **Step 4: Run, mutation-check, commit**

```bash
git add static/app.js tests/payments_settings_tab.test.js
git commit -m "feat: the cost rate says which account it came from"
```

---

### Task 10: Full verification and documentation

- [ ] **Step 1: Delete any stale work dir**

Run: `rm -rf .test-work`

- [ ] **Step 2: Run everything**

```bash
for f in tests/*.test.js; do node "$f" >/dev/null || echo "RED: $f"; done; echo "JS done"
for f in tests/*.test.py; do python "$f" >/dev/null 2>&1 || echo "RED: $f"; done; echo "PY done"
python smoke_test.py | tail -1
python smoke_quote.py | tail -1
node tests/no_em_dash.test.js | tail -1
```

Expected: the only reds are the four already known and reproduced on `HEAD`
before this work - `june_numbered_on_display_name` (profile guard),
`po_row_height_matches_render`, `quote_cost_memory`, and whatever
`no_stub_pdfs_in_the_live_tree` reports from this run's leftovers. Any other red
is yours. Reproduce each on `HEAD` before calling it pre-existing.

- [ ] **Step 3: Clean up after the suite**

Remove any `__LPOTEST__` stub under the documents roots, delete `.test-work`, and
restore `data/profile-registry.json` to the profile noted in Task 0 Step 4.

- [ ] **Step 4: Update the docs in the same change**

- `docs/03-BACKEND-DATA.md`: rewrite the `doc_iqd_per_usd` chain, document
  `doc_cost_meta.account_key/account_rate`, and say that the Eshraqat and cash
  rules were deleted and why nothing moved.
- `docs/06-FRONTEND-EDITORS.md`: the profit cards now explain their rate.
- `docs/01-ARCHITECTURE.md`: add `payment_accounts.py` to the module map.
- `AGENT-BOARD.md`: release the test lock with the numbers from Step 2.

- [ ] **Step 5: Commit**

```bash
git add docs AGENT-BOARD.md
git commit -m "docs: the account's rate is the cost peg"
```

---

## Self-review

**Spec coverage.** Payments tab -> Task 8. Per-account rate -> Tasks 2, 8.
Editable fallback -> Tasks 2, 8. Account per document -> Task 3. Rate chain and
the deletion of the two rules -> Task 6. Stamping -> Tasks 4, 7. Migration ->
Task 5, with the "nothing moved" test in Task 6. Costing readout -> Task 9. Bank
editor removed from the invoice modal -> Task 8 Step 7. Docs -> Task 10.

**Ordering.** Task 5 must precede Task 6: stamp before the rules are deleted.
Task 0 must precede everything: the snapshot can only be taken while the old
rules are live.

**Naming.** `get_account_stamp` / `set_account_stamp` (Task 4) are the names used
in Tasks 5 and 6. `account_for_document` / `rate_for_account` /
`rate_for_document` / `find_account` / `fallback_rate` (Tasks 2, 3) are the names
used in Tasks 6 and 7. `_accounts_override` is the test seam in every backend
test class.

**Known soft spot.** Task 7 Step 5 cannot give exact line numbers, because the
three save paths differ in where their record lands. The step says to find the
success return first, and Step 6 verifies by running the smokes, which save real
documents.
