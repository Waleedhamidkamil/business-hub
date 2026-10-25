# Internal Document Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an application-only Reference field to PO, quotation, delivery-note, and invoice documents and unify their right-panel cards around the compact invoice hierarchy.

**Architecture:** Store `reference` as a dedicated text column on each document table, expose it through the existing save/load/list/search paths, and deliberately remove it before any quote/DN/invoice renderer receives a payload. Add one shared card-content formatter in `static/app.js`; each editor remains responsible for its own action buttons and loading behavior.

**Tech Stack:** Python 3, SQLite, pywebview bridge methods, vanilla JavaScript, HTML/CSS, Python `unittest`, and Node-based frontend contract tests.

---

## File map

- Create `tests/document_reference_backend.test.py`: isolated SQLite migration, persistence, list, and dashboard-search regression.
- Create `tests/document_reference_api.test.py`: bridge threading and printable-payload exclusion regression using patched renderers/database calls.
- Create `tests/document_reference_frontend.test.js`: editor field, save/load/New/Duplicate, shared card markup, escaping, and PDF-template exclusion contract.
- Modify `backend/database.py`: new-profile schema plus save/load/list/search support.
- Modify `backend/migrations.py`: idempotent four-table migration for existing profile databases.
- Modify `backend/api.py`: pass Reference to persistence while removing it from renderer payloads.
- Modify `templates/index.html`: four matching `Reference` inputs.
- Modify `static/app.js`: PO Reference behavior, shared compact-card formatter, and PO card conversion.
- Modify `static/quote.js`: quotation Reference behavior and card conversion.
- Modify `static/dn.js`: DN Reference behavior and card conversion.
- Modify `static/inv.js`: invoice Reference behavior and shared card conversion.
- Modify `static/app_styles.css`: matching PO field sizing and compact Reference/card CSS.
- Modify `docs/02-BACKEND-CORE.md`, `docs/03-BACKEND-DATA.md`, `docs/05-FRONTEND-SHELL.md`, `docs/06-FRONTEND-EDITORS.md`, `docs/08-STYLES-TEMPLATES.md`, `docs/09-DATA-MODEL.md`, and `docs/11-RUN-AND-MAINTAIN.md`: keep the architecture map and test catalogue current.

### Task 1: Database schema, migration, persistence, lists, and search

**Files:**
- Create: `tests/document_reference_backend.test.py`
- Modify: `backend/database.py`
- Modify: `backend/migrations.py`

- [ ] **Step 1: Write the failing isolated backend regression**

Create a `unittest.TestCase` that points `config.DB_PATH` at a temporary SQLite file, executes `database._SCHEMA`, patches `database.best_effort_shared` to avoid the live shared catalogue, and saves one PO/QUT/DN/INV with distinct References. The assertions must use the real public functions:

```python
self.assertEqual(database.load_po("PO-REF")["reference"], "Warehouse restock")
self.assertEqual(database.load_quote("QUT-REF")["reference"], "Basra tender")
self.assertEqual(database.load_dn("DN-REF")["reference"], "Gate 4 delivery")
self.assertEqual(database.load_inv("INV-REF")["reference"], "August settlement")
self.assertEqual(database.list_pos_in_db(2099, 1)[0]["reference"], "Warehouse restock")
self.assertEqual(database.list_quotes_in_db(2099, 1)[0]["reference"], "Basra tender")
self.assertEqual(database.list_dns_in_db(2099, 1)[0]["reference"], "Gate 4 delivery")
self.assertEqual(database.list_invoices_in_db(2099, 1)[0]["reference"], "August settlement")
```

Add a legacy-schema test that creates the four tables without `reference`, invokes the new migration function directly, and asserts `PRAGMA table_info(...)` contains `reference` with default `''`. Add search assertions proving a unique word in each Reference returns the expected document and the full Reference as `snippet`.

- [ ] **Step 2: Run the backend regression and verify RED**

Run:

```powershell
python tests/document_reference_backend.test.py
```

Expected: failures because save functions do not accept `reference`, schema/migration columns are absent, and search does not query Reference.

- [ ] **Step 3: Add the new-profile schema and existing-profile migration**

In each `_SCHEMA` document table definition add:

```sql
reference TEXT NOT NULL DEFAULT ''
```

In `backend/migrations.py` register one migration that safely adds all missing columns:

```python
@migration("2026-08-24_document_references")
def _document_references(conn: sqlite3.Connection) -> str:
    added = []
    for table in ("pos", "quotes", "dns", "invoices"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "reference" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN reference TEXT NOT NULL DEFAULT ''")
            added.append(f"{table}.reference")
    return "added " + ", ".join(added) if added else "document reference columns already present"
```

- [ ] **Step 4: Thread Reference through database save and monthly-list functions**

Add `reference: str = ""` as the last non-callback argument of all four save-record functions, normalize with `reference = str(reference or "").strip()`, include it in INSERT/UPSERT SQL, and select it in every monthly-list query. `load_*` already selects `*` and will return the column automatically.

For dashboard search, select `reference`, add `OR reference LIKE ?`, and prefer a Reference snippet when it matches:

```python
reference_val = r["reference"] or ""
reference_hit = ql in reference_val.lower()
snippet = " ".join(reference_val.split()) if reference_hit else _match_item_snippet(r["rows_json"], ql)
```

Cap the displayed snippet using the existing 60-character rule, but search against the full stored value.

- [ ] **Step 5: Run the backend regression and verify GREEN**

Run:

```powershell
python tests/document_reference_backend.test.py
```

Expected: all migration, round-trip, list, and search tests pass against only the temporary database.

- [ ] **Step 6: Commit the database slice**

Stage only the explicit paths after confirming the other session has released `backend/database.py`:

```powershell
git add -- backend/database.py backend/migrations.py tests/document_reference_backend.test.py
git commit -m "feat: persist internal document references"
```

### Task 2: Bridge persistence and strict PDF exclusion

**Files:**
- Create: `tests/document_reference_api.test.py`
- Modify: `backend/api.py`

- [ ] **Step 1: Write failing API tests**

Patch each renderer and database save function. Call each real bridge save method with `reference: "Internal only"`. Assert the database mock receives `reference="Internal only"`, while the quote/DN/invoice renderer's document dictionary does not contain the `reference` key. For PO, assert the existing positional renderer call remains unchanged and the database call receives Reference. Cover both `save_po` and `update_po`.

The quote test contains these assertions, and the DN/invoice tests use their
corresponding save mock and renderer mock with the same two assertions:

```python
saved = save_quote_record.call_args.kwargs
self.assertEqual(saved["reference"], "Internal only")
rendered_payload = render_quote_pdf.call_args.args[0]
self.assertNotIn("reference", rendered_payload)
```

- [ ] **Step 2: Run the API regression and verify RED**

Run:

```powershell
python tests/document_reference_api.test.py
```

Expected: Reference is absent from persistence calls and remains present in renderer dictionaries.

- [ ] **Step 3: Implement minimal bridge threading**

For quote, DN, and invoice saves, derive two values before rendering:

```python
reference = str(payload.get("reference") or "").strip()
printable = {k: v for k, v in payload.items() if k != "reference"}
```

Pass `printable` to render/build functions and use the original payload for non-printing editor logic. Pass `reference=reference` to the corresponding database save call. Add optional `reference: str = ""` to `save_po` and `update_po`, passing it only to `database.save_po_record`; do not add it to `pdf_generator.render_pdf`.

- [ ] **Step 4: Run API tests and focused incomplete-draft regressions**

Run:

```powershell
python tests/document_reference_api.test.py
python tests/incomplete_price_save_backend.test.py
python tests/incomplete_price_draft_isolation_backend.test.py
```

Expected: all tests pass; incomplete QUT/INV app-only saves still bypass PDF rendering exactly as before.

- [ ] **Step 5: Commit the bridge slice**

```powershell
git add -- backend/api.py tests/document_reference_api.test.py
git commit -m "feat: keep document references out of PDFs"
```

### Task 3: Editor fields and lifecycle behavior

**Files:**
- Create: `tests/document_reference_frontend.test.js`
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/quote.js`
- Modify: `static/dn.js`
- Modify: `static/inv.js`
- Modify: `static/app_styles.css`

- [ ] **Step 1: Write the failing frontend editor contract**

Read the real HTML/JS files in Node and assert:

```javascript
for (const id of ["poReference", "qReference", "dnReference", "invReference"]) {
  assert(html.includes(`id="${id}"`), `${id} field is missing`);
}
assert(/label>Reference<\/label>[\s\S]*id="qReference"/.test(html));
assert(/reference:\s*document\.getElementById\("qReference"\)\.value\.trim\(\)/.test(quote));
assert(/qReference"\)\.value\s*=\s*rec\.reference\s*\|\|\s*""/.test(quote));
assert(/qReference"\)\.value\s*=\s*""/.test(quote));
```

Add the following source-contract assertions for every editor so each path is
named explicitly:

```javascript
const contracts = [
  [po, "poReference", "rec.reference", "save_po"],
  [quote, "qReference", "rec.reference", "reference:"],
  [dn, "dnReference", "rec.reference", "reference:"],
  [inv, "invReference", "rec.reference", "reference:"],
];
for (const [source, id, loadedValue, saveToken] of contracts) {
  assert(source.includes(id), `${id} is not wired into its editor source`);
  assert(source.includes(loadedValue), `${id} is not restored on load`);
  assert(source.includes(saveToken), `${id} is not included during save`);
  const clears = source.match(new RegExp(`${id}[^\\n]*value\\s*=\\s*["']{2}`, "g")) || [];
  assert(clears.length >= 2, `${id} must clear on both New and Duplicate`);
}
```

Also assert each Reference input is included in the same input/change dirty-state
binding used by the editor's neighboring metadata controls.

- [ ] **Step 2: Run the frontend test and verify RED**

Run:

```powershell
node tests/document_reference_frontend.test.js
```

Expected: failure reporting the first missing Reference input.

- [ ] **Step 3: Add matching editor inputs**

Add exactly one input per document:

```html
<div class="qf doc-reference-field">
  <label>Reference</label>
  <input type="text" id="qReference" autocomplete="off" spellcheck="false">
</div>
```

Use `dnReference` and `invReference` in the corresponding metadata grids. In the PO strip use the existing `.field` pattern, label exactly `Reference`, and `id="poReference"`. Extend the relevant grid/strip CSS so these controls match neighboring fields rather than creating a special oversized control.

- [ ] **Step 4: Implement save/load/New/Duplicate behavior**

Add trimmed `reference` to each save payload/call. On load assign `rec.reference || ""`. On every New/reset path and every `*MakeDuplicate` path assign `""`. Bind `input`/`change` through the same dirty-state mechanism used by adjacent metadata inputs.

For PO's positional bridge calls, append the Reference after `source_quote_no` in both save and update calls so existing parameter order remains stable.

- [ ] **Step 5: Run frontend lifecycle regressions**

Run:

```powershell
node tests/document_reference_frontend.test.js
node tests/editor_new_reset.test.js
node tests/duplicate_document.test.js
```

Expected: all tests pass and Duplicate clears only the new internal Reference while retaining the document content currently preserved by duplication.

- [ ] **Step 6: Commit the editor slice**

```powershell
git add -- templates/index.html static/app.js static/quote.js static/dn.js static/inv.js static/app_styles.css tests/document_reference_frontend.test.js
git commit -m "feat: add Reference to document editors"
```

### Task 4: Shared compact right-panel cards

**Files:**
- Modify: `static/app.js`
- Modify: `static/quote.js`
- Modify: `static/dn.js`
- Modify: `static/inv.js`
- Modify: `static/app_styles.css`
- Modify: `tests/document_reference_frontend.test.js`

- [ ] **Step 1: Extend the frontend test for card behavior and verify RED**

Require a shared `window.panelCardMainHTML` helper and assert all four list renderers call it. Execute or source-check the helper with a long Reference containing `<`, `&`, quotes, and apostrophes. Assert the output contains escaped text, a `title` carrying the escaped full value, no Reference row for an empty value, status markup below Reference, and invoice amount/date top metadata.

Also assert quotation card source no longer emits `qt-chip`, PO card source no longer emits `p.filename`, and each existing action wiring token remains present.

Run:

```powershell
node tests/document_reference_frontend.test.js
```

Expected: failure because the helper and shared card structure do not exist.

- [ ] **Step 2: Add the shared escaped card formatter**

Near `window.panelStatusHTML` in `static/app.js`, expose a formatter with this contract:

```javascript
window.panelCardMainHTML = function ({ no, date, reference, statuses, amount }) {
    const ref = String(reference || "").trim();
    return `<div class="panel-row-main">
        <div class="panel-row-head">
            <div class="po-num">${esc(no || "")}</div>
            <div class="panel-row-topmeta">${amount ? `<span class="panel-row-amount">${esc(amount)}</span>` : ""}<span>${esc(date || "")}</span></div>
        </div>
        ${ref ? `<div class="panel-row-reference" title="${esc(ref)}">${esc(ref)}</div>` : ""}
        <div class="panel-row-statuses">${statuses || ""}</div>
    </div>`;
};
```

`statuses` is trusted markup produced only by `panelStatusHTML`; all user data is escaped in the helper.

- [ ] **Step 3: Convert all four cards without altering actions**

Pass each document number, its existing date label, `d.reference`, existing status HTML, and invoice amount where applicable to the shared helper. Keep each renderer's `.row-actions` block and event wiring byte-for-byte unless class-name adaptation is required.

Remove the quotation `typeChip` from card markup only; keep type filters. Remove PO `filename`/source-quote metadata from the card. Do not change the underlying list dates or invoice amount calculations.

- [ ] **Step 4: Add compact shared styles**

Rename/generalize the invoice-only layout selectors to `.panel-row-*`. Add:

```css
.op-pane .panel-row-reference {
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    font-size: 11px;
    line-height: 1.35;
    color: var(--text-subtle);
}
```

Keep the existing one-line number/date header, wrapped status pill row, invoice amount emphasis, and icon action layout.

- [ ] **Step 5: Run card and surrounding panel regressions**

Run:

```powershell
node tests/document_reference_frontend.test.js
node tests/incomplete_price_panel.test.js
node tests/invoice_paid_panel.test.js
node tests/po_quote_link_panel.test.js
node tests/editor_toolbar_layout.test.js
```

Expected: all pass; status pills/actions remain present and long References do not change card width or exceed one line.

- [ ] **Step 6: Commit the card slice**

```powershell
git add -- static/app.js static/quote.js static/dn.js static/inv.js static/app_styles.css tests/document_reference_frontend.test.js
git commit -m "feat: unify compact document panel cards"
```

### Task 5: Map updates and final verification

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/08-STYLES-TEMPLATES.md`
- Modify: `docs/09-DATA-MODEL.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`
- Modify: `AGENT-BOARD.md`

- [ ] **Step 1: Update owning documentation**

Document the four `reference` columns, migration key, bridge exclusion from render payloads, four editor input IDs, New/Duplicate clearing rule, shared `panelCardMainHTML` contract, compact card hierarchy, search behavior, and the three new test commands. Explicitly state that no PDF template renders Reference.

- [ ] **Step 2: Run syntax/diff checks and every focused regression**

Run:

```powershell
git diff --check
python tests/document_reference_backend.test.py
python tests/document_reference_api.test.py
node tests/document_reference_frontend.test.js
node tests/editor_new_reset.test.js
node tests/duplicate_document.test.js
node tests/incomplete_price_panel.test.js
node tests/invoice_paid_panel.test.js
node tests/po_quote_link_panel.test.js
python tests/incomplete_price_save_backend.test.py
python tests/incomplete_price_draft_isolation_backend.test.py
```

Expected: every command exits `0` with no warnings attributable to the change.

- [ ] **Step 3: Hold the project test lock and run smoke verification**

Update `AGENT-BOARD.md` to claim the test lock only after the other session releases it, then run:

```powershell
python smoke_test.py
```

Expected: exit `0`, DB initialization succeeds, and HTML/PDF output is generated without any Reference label/value.

- [ ] **Step 4: Inspect generated output for PDF exclusion**

Use a unique Reference sentinel in a saved/previewed fixture and assert it is absent from generated HTML/PDF-extracted text while still present in `database.load_*`, list results, and search results.

- [ ] **Step 5: Request independent code review and address findings**

Review the final scoped diff for schema compatibility, SQL parameter order, renderer payload leakage, escaping, lifecycle clearing, and preservation of existing panel actions/statuses. Fix every Critical or Important finding and rerun the affected tests.

- [ ] **Step 6: Commit docs/board with explicit staging and hand off**

Stage only the owning docs and this task's board hunk; never stage the other session's files or whole directories:

```powershell
git add -- docs/02-BACKEND-CORE.md docs/03-BACKEND-DATA.md docs/05-FRONTEND-SHELL.md docs/06-FRONTEND-EDITORS.md docs/08-STYLES-TEMPLATES.md docs/09-DATA-MODEL.md docs/11-RUN-AND-MAINTAIN.md
git commit -m "docs: map internal document references"
```

Record the final commits and verification result on `AGENT-BOARD.md`, remove this task's Now row, release the test lock, and commit only that board hunk.
