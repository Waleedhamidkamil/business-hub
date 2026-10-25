# Smart Document Link Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank likely PO/DN/invoice fulfillment links by item count and quantity similarity, and let invoices reach POs through already-linked Delivery Notes with explicit item-level confirmation.

**Architecture:** Add pure matching helpers and profile-scoped query functions to `backend/database.py`, expose narrow bridge methods in `backend/api.py`, and extend the existing self-contained `static/po_link.js` modal. Reuse `po_fulfillment` as the source of truth, make no schema changes, and preserve the complete searchable candidate lists below the suggestions.

**Tech Stack:** Python 3.13, SQLite, pywebview bridge, vanilla JavaScript/CSS, script-style backend regression tests.

---

## File Structure

- Create `tests/po_link_suggestions_backend.test.py`: isolated SQLite regression coverage for scoring, linked-DN filtering, and exact PO item-index derivation.
- Modify `backend/database.py`: pure row/count/quantity scoring plus candidate and linked-DN queries.
- Modify `backend/api.py`: JSON-safe bridge wrappers for the new query functions.
- Modify `static/po_link.js`: suggestion sections, invoice-side tabs, linked-DN confirmation, and existing-flow preservation.
- Modify `static/app_styles.css`: restrained suggestion, tab, and linked-DN group styling.
- Modify `static/dark.css`: dark-theme contrast for the new controls.
- Modify `docs/02-BACKEND-CORE.md`, `docs/03-BACKEND-DATA.md`, `docs/07-FRONTEND-SUPPORT.md`, `docs/08-STYLES-TEMPLATES.md`, and `docs/11-RUN-AND-MAINTAIN.md`: map the new API, data flow, UI behavior, styles, and regression test.

### Task 1: Backend Match Scoring

**Files:**
- Create: `tests/po_link_suggestions_backend.test.py`
- Modify: `backend/database.py`

- [ ] **Step 1: Write failing scoring tests**

Create an isolated test database from `database._SCHEMA`, insert one source PO and candidate DNs/invoices directly with SQL, then assert the wished-for API:

```python
exact = database._score_link_rows(source_rows, source_date, exact_rows, exact_date)
one_off = database._score_link_rows(source_rows, source_date, one_off_rows, one_off_date)
two_off = database._score_link_rows(source_rows, source_date, two_off_rows, two_off_date)
outside = database._score_link_rows(source_rows, source_date, outside_rows, outside_date)

assert exact["eligible"] and exact["score"] > one_off["score"] > two_off["score"]
assert not outside["eligible"]
assert "Quantities match" in exact["reasons"]
```

Add an equal-item-count pair where the exact quantity multiset outranks a different quantity pattern. Use direct SQL instead of `save_po_record()` so the shared autocomplete library is never touched.

- [ ] **Step 2: Run the test and verify RED**

Run: `python tests/po_link_suggestions_backend.test.py`

Expected: FAIL because `_score_link_rows` and `suggest_link_candidates` do not exist.

- [ ] **Step 3: Implement minimal pure matching helpers**

Add to `backend/database.py`:

```python
_LINK_DOC_SPECS = {
    "po": ("pos", "po_no", "po_date"),
    "dn": ("dns", "dn_no", "dn_date"),
    "inv": ("invoices", "inv_no", "inv_date"),
}

def _link_primary(row): ...
def _link_rows(rows): ...
def _link_qtys(rows): ...
def _score_link_rows(source_rows, source_date, candidate_rows, candidate_date): ...
def suggest_link_candidates(source_kind, source_no, target_kind, limit=5): ...
```

The score bands must make item-count distance dominant: exact `300`, one apart `200`, two apart `100`; exact sorted quantity multisets add `40`, close totals add at most `20`, and date proximity adds at most `9`. Return `{eligible, score, item_count, item_diff, quantity_similarity, days_apart, reasons}`. Exclude already-linked source/target pairs from the suggestion result while leaving them in existing complete-list APIs.

- [ ] **Step 4: Run the test and verify GREEN**

Run: `python tests/po_link_suggestions_backend.test.py`

Expected: scoring assertions pass.

### Task 2: Linked Delivery Note Queries

**Files:**
- Modify: `tests/po_link_suggestions_backend.test.py`
- Modify: `backend/database.py`
- Modify: `backend/api.py`

- [ ] **Step 1: Add failing linked-DN tests**

Seed two DNs but add `po_fulfillment` delivered rows for only one. Link that DN to item indexes across two POs. Assert:

```python
rows = database.list_linked_dn_candidates(invoice_no)
assert [row["no"] for row in rows] == [linked_dn_no]
assert rows[0]["linked_po_count"] == 2

detail = database.linked_dn_invoice_detail(linked_dn_no, invoice_no)
assert {group["po_no"] for group in detail["pos"]} == {po_a, po_b}
assert detail["pos"][0]["dn_item_indexes"]
assert all("proposed_indexes" in group for group in detail["pos"])
```

Also verify an item already marked by this invoice is absent from `proposed_indexes`.

- [ ] **Step 2: Run the test and verify RED**

Run: `python tests/po_link_suggestions_backend.test.py`

Expected: FAIL because the linked-DN query functions do not exist.

- [ ] **Step 3: Implement linked-DN candidate and detail functions**

Add:

```python
def list_linked_dn_candidates(invoice_no: str, limit: int = 300) -> list[dict]: ...
def linked_dn_invoice_detail(dn_no: str, invoice_no: str) -> dict: ...
```

`list_linked_dn_candidates` must select only DNs present as `source_kind='dn'` in `po_fulfillment`, return normal candidate fields plus `linked_po_count`, and attach the same suggestion metadata against the invoice. `linked_dn_invoice_detail` must group exact delivered item indexes by PO, load each PO's current rows, include the DN and invoice rows, and remove indexes already carrying an invoice-side `paid` fulfillment mark from `proposed_indexes`.

Expose bridge methods with exception-safe empty results:

```python
def suggest_fulfillment_links(self, source_kind, source_no, target_kind, limit=5): ...
def list_linked_dn_candidates(self, invoice_no, limit=300): ...
def linked_dn_invoice_detail(self, dn_no, invoice_no): ...
```

- [ ] **Step 4: Run backend tests and syntax checks**

Run:

```powershell
python tests/po_link_suggestions_backend.test.py
python -m py_compile backend/database.py backend/api.py tests/po_link_suggestions_backend.test.py
```

Expected: all assertions pass and compilation exits zero.

### Task 3: Suggestion Sections In All Four Directions

**Files:**
- Modify: `static/po_link.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`

- [ ] **Step 1: Add a failing static contract check**

Extend the regression script to inspect `static/po_link.js` and assert it calls `suggest_fulfillment_links` in both `openPicker` and `openSourcePicker`, renders `Suggested` before `All documents`, and retains the existing complete-list APIs.

- [ ] **Step 2: Run the test and verify RED**

Run: `python tests/po_link_suggestions_backend.test.py`

Expected: FAIL because the new bridge call and section labels are absent.

- [ ] **Step 3: Implement shared candidate rendering**

In `static/po_link.js`, add small helpers:

```javascript
function reasonHtml(row) { ... }
function candidateSections(rows, suggestions, renderRow, filter) { ... }
```

Call `suggest_fulfillment_links(sourceKind, sourceNo, "po", 5)` in `openPicker` and `suggest_fulfillment_links("po", poNo, sourceKind, 5)` in `openSourcePicker`. Render up to five suggestions first and the complete filtered list second. Suppress duplicate suggested rows from the unfiltered complete section, retain linked labels, and keep search/back/close behavior unchanged.

Add `.po-link-section`, `.po-link-section-head`, `.po-link-reasons`, and `.po-link-reason` styles. Use existing spacing/radius tokens, a restrained violet suggestion accent, visible keyboard focus, narrow-window wrapping, and dark-theme overrides.

- [ ] **Step 4: Verify GREEN and JavaScript syntax**

Run:

```powershell
python tests/po_link_suggestions_backend.test.py
node --check static/po_link.js
```

Expected: contract assertions pass and JavaScript parses.

### Task 4: Invoice Linked-DN Tab And Confirmation

**Files:**
- Modify: `tests/po_link_suggestions_backend.test.py`
- Modify: `static/po_link.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`

- [ ] **Step 1: Add failing invoice-tab contract assertions**

Assert the source contains the labels `Purchase orders` and `Linked delivery notes`, calls both new linked-DN APIs, renders grouped PO confirmation, and calls `set_po_fulfillment` only inside the confirmation handler.

- [ ] **Step 2: Run the test and verify RED**

Run: `python tests/po_link_suggestions_backend.test.py`

Expected: FAIL because the invoice tabs and confirmation view are absent.

- [ ] **Step 3: Implement invoice-only tabs**

For `openPicker("inv", invoiceNo, opts)`, render a stable two-tab segmented control. Keep `Purchase orders` active initially. The second tab calls `list_linked_dn_candidates(invoiceNo)`, renders Suggested and All linked DNs, and never includes an unlinked DN.

- [ ] **Step 4: Implement linked-DN confirmation**

Selecting a DN calls `linked_dn_invoice_detail(dnNo, invoiceNo)` and opens a confirmation modal. Render a DN item reference strip followed by one fieldset per linked PO. Each group shows PO number/date, the exact DN-linked PO rows, existing invoice state, and checked checkboxes for `proposed_indexes`. Confirmation iterates selected groups and calls:

```javascript
await api().set_po_fulfillment(poNo, selectedIndexes, "inv", invoiceNo, true);
```

Do not write before confirmation. On success, retain the existing invoice-paid state, close the modal, call `opts.onAfter`, and refresh the open panel. On partial failure, keep the modal open and show the specific PO that failed.

- [ ] **Step 5: Verify GREEN and interaction contracts**

Run:

```powershell
python tests/po_link_suggestions_backend.test.py
node --check static/po_link.js
```

Expected: all backend and static contract assertions pass.

### Task 5: Documentation And Full Verification

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`
- Modify: `docs/08-STYLES-TEMPLATES.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`

- [ ] **Step 1: Update the app map**

Document the three bridge/query methods, score ordering, the four suggestion directions, the invoice-only DN tab, exact DN-derived indexes, styling classes, and the new isolated test. Explicitly preserve the positional-index caveat already documented for `po_fulfillment`.

- [ ] **Step 2: Run focused and impact-map suites**

Run:

```powershell
python tests/po_link_suggestions_backend.test.py
python tests/po_deliver_invoice_backend.test.py
python tests/po_submitted_quote_link_backend.test.py
python tests/profile_data_isolation_backend.test.py
node --check static/po_link.js
python -m py_compile backend/database.py backend/api.py
python smoke_test.py
```

Expected: every command exits zero. If an existing test writes to the active shared library, isolate it or report the side effect instead of silently altering live data.

- [ ] **Step 3: Inspect the final diff and working tree**

Run:

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: only feature files, map updates, and the user's pre-existing `data/profiles/abraj/settings.json` change are present; the settings file is not staged or modified by this work.
