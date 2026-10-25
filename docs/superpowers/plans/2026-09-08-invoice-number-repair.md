# Invoice Number Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely repair Abraj invoices 902 through 907 to 048 through 053, clear their Submitted status, regenerate their PDFs, remove test tombstones, and prevent recurrence.

**Architecture:** A one-time repair utility rehearses against SQLite backups and staged PDFs, then publishes only after exact precondition checks. The signed-copy tests receive defensive tombstone cleanup and a regression assertion so test data cannot influence live document numbering again.

**Tech Stack:** Python 3, SQLite, pathlib, pypdf, the existing invoice generator, and the existing direct Python test runner.

---

### Task 1: Lock the repair contract with a failing test

**Files:**
- Create: `tests/invoice_number_repair_backend.test.py`
- Test: `tests/invoice_number_repair_backend.test.py`

- [ ] **Step 1: Write the failing test**

Create a temporary Abraj database and invoice tree containing the six bad
numbers plus representative satellite rows. Assert that the future repair
module preserves invoice data and references, deletes Submitted rows, removes
only the named fake tombstones, renames the PDFs, and reports next number 054.
The loader must fail with the explicit message `repair utility is missing`
until the implementation exists.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python tests/invoice_number_repair_backend.test.py`

Expected: FAIL with `repair utility is missing`.

### Task 2: Implement the rehearsed repair utility

**Files:**
- Create: `scripts/repair_invoice_number_jump_20260908.py`
- Test: `tests/invoice_number_repair_backend.test.py`

- [ ] **Step 1: Implement pure re-key and validation functions**

Implement exact mapping validation, SQLite re-key propagation, Submitted-row
deletion, fake-tombstone cleanup, filesystem publication with rollback, and a
structured audit report. All paths and connections must be supplied explicitly
so the core is testable without live data.

- [ ] **Step 2: Run the focused test to verify it passes**

Run: `python tests/invoice_number_repair_backend.test.py`

Expected: PASS with six repaired invoices, zero Submitted rows, and next 054.

### Task 3: Prevent signed-copy tests from leaving tombstones

**Files:**
- Modify: `tests/inv_signed_copy_backend.test.py`
- Modify: `tests/dn_signed_copy_backend.test.py`

- [ ] **Step 1: Add failing cleanup assertions**

After each test's cleanup, assert that none of its fake document numbers remain
in `deleted_docs`. Run both tests and confirm the invoice test fails on the
`SIGNEDCOPY-901` tombstone before changing cleanup.

- [ ] **Step 2: Delete the test tombstones during cleanup**

Extend both cleanup functions to delete only their named fake tombstones.

- [ ] **Step 3: Re-run both tests**

Run: `python tests/inv_signed_copy_backend.test.py`

Run: `python tests/dn_signed_copy_backend.test.py`

Expected: both PASS and use only isolated profile databases.

### Task 4: Rehearse and publish the live repair

**Files:**
- Create: `data/document-backups/<timestamp>/`
- Modify: `data/profiles/abraj/po_tool.db`
- Modify: `data/profiles/eshraqat/po_tool.db`
- Replace: six PDFs under `D:\PO TOOL\INVOICES\...`

- [ ] **Step 1: Run rehearsal mode**

Run: `python scripts/repair_invoice_number_jump_20260908.py`

Expected: a restore point and report showing mapping 902-907 to 048-053 with no
live mutation.

- [ ] **Step 2: Review the report and staged PDF text**

Confirm six source records, six source PDFs, all expected satellite counts,
new numbers visible in staged PDFs, old numbers absent, and next number 054 in
the rehearsed state.

- [ ] **Step 3: Publish the repair**

Run: `python scripts/repair_invoice_number_jump_20260908.py --apply`

Expected: the report says `applied: true`, six invoices repaired, six Submitted
rows removed, five PO links preserved, and fake signed-copy tombstones removed.

### Task 5: Verify the live app state and documentation

**Files:**
- Modify: `docs/09-DATA-MODEL.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`

- [ ] **Step 1: Run focused and surrounding tests**

Run the repair test, both signed-copy backend tests, invoice numbering tests,
document deletion tests, invoice rendering tests, and Python compilation.

- [ ] **Step 2: Run both smoke tests while holding the test lock**

Run: `python smoke_test.py`

Run: `python smoke_quote.py`

Expected: both PASS without changing the active profile or reintroducing test
tombstones.

- [ ] **Step 3: Perform final live invariants**

Verify SQLite integrity for both profiles, the active profile remains `abraj`,
the invoice set ends at 053, the next invoice is 054, corrected PDFs contain
their new numbers, no 902-907 active PDF or database reference remains, and the
six corrected invoices are not Submitted.

- [ ] **Step 4: Update the owning map and maintenance instructions**

Document the stricter signed-copy test cleanup, the repair utility, restore
point, and exact verification result.

- [ ] **Step 5: Commit only explicit repair paths**

Stage only the new repair files and the specifically modified tests/docs. Check
`git status` before committing and leave all unrelated user or agent changes
untouched.

