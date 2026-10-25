# Client Master Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make aliases, short document codes, printable legal names, and invoice defaults resolve through one canonical client record, including DN/PO-to-invoice hand-offs.

**Architecture:** Extend the shared client catalog with stable IDs and an alias table, add optional client IDs to client-facing document rows, and centralize resolution in `backend/clients.py`. Editors call one bridge method when a client changes; numbering receives the resolved short code while the payload retains the printable name snapshot.

**Tech Stack:** Python 3, SQLite, pywebview bridge, plain JavaScript, Node-based source tests.

---

### Task 1: Shared client master schema and resolver

**Files:**
- Modify: `backend/shared_library.py`
- Modify: `backend/clients.py`
- Create: `tests/client_master_backend.test.py`

- [ ] **Step 1: Write the failing backend test**

Create an isolated `ProfileManager`, initialize the DB, and assert that saving this master record succeeds:

```python
saved = clients.save_client_master({
    "name": "TDC",
    "document_code": "TDC",
    "legal_name_en": "China Communications Construction Company Ltd. Iraq Branch",
    "legal_name_ar": "شركة الاتصالات الصينية",
    "default_print_name": "China Communications Construction Company Ltd. Iraq Branch",
    "default_payment_bank_key": "cash",
    "default_address": "Iraq Branch",
    "default_withholding_included": True,
    "aliases": ["CCCC Ltd", "تي دي سي"],
})
assert saved["ok"]
assert clients.resolve_client("cccc ltd")["document_code"] == "TDC"
assert clients.resolve_client("تي دي سي")["client_id"] == saved["client"]["client_id"]
```

Also assert duplicate aliases and duplicate document codes are rejected without changing either client.

- [ ] **Step 2: Run the test and verify RED**

Run: `python tests/client_master_backend.test.py`

Expected: failure because `save_client_master` and the new schema do not exist.

- [ ] **Step 3: Implement the shared schema**

Add nullable master columns to `clients`, create `client_aliases(client_id, alias, alias_key, created_at)`, and have `init_shared_db()` use `PRAGMA table_info` plus guarded `ALTER TABLE` statements before backfilling missing IDs/codes and aliases.

Use `uuid.uuid4().hex` for IDs and normalize aliases with Unicode NFKC, collapsed whitespace, and `casefold()`.

- [ ] **Step 4: Implement resolver and save API**

Add `resolve_client(value)`, `get_client_master(value)`, and `save_client_master(payload)` to `backend/clients.py`. Return a bridge-safe client object containing aliases and defaults. Enforce unique alias keys and case-insensitive document codes in one transaction.

- [ ] **Step 5: Run the backend test and verify GREEN**

Run: `python tests/client_master_backend.test.py`

Expected: `client master backend tests passed`.

### Task 2: Persist canonical client IDs without changing historical display text

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/migrations.py`
- Modify: `backend/api.py`
- Extend: `tests/client_master_backend.test.py`

- [ ] **Step 1: Add failing document persistence cases**

Save quote, DN, and invoice records with `client_id=saved_id`; reload them and assert both the ID and the exact printable `client_name` remain present.

- [ ] **Step 2: Run and verify RED**

Run: `python tests/client_master_backend.test.py`

Expected: `TypeError` for the missing `client_id` parameters or missing columns.

- [ ] **Step 3: Add schema and migration**

Declare nullable `client_id TEXT` columns plus indexes for `quotes`, `dns`, and `invoices` in `database._SCHEMA`. Add an idempotent migration that checks `PRAGMA table_info`, adds missing columns, and backfills IDs only for names that resolve unambiguously.

- [ ] **Step 4: Thread IDs through persistence**

Add optional `client_id` parameters to `save_quote_record`, `save_dn_record`, and `save_inv_record`, include them in upserts, and have `Api.save_*` resolve missing IDs from the printable name. Do not rewrite `client_name`.

- [ ] **Step 5: Run and verify GREEN**

Run: `python tests/client_master_backend.test.py`

Expected: all persistence assertions pass.

### Task 3: Resolve short document codes for numbering and folders

**Files:**
- Modify: `backend/quote_numbering.py`
- Modify: `backend/dn_numbering.py`
- Modify: `backend/inv_numbering.py`
- Modify: `backend/api.py`
- Extend: `tests/client_master_backend.test.py`

- [ ] **Step 1: Add failing numbering assertions**

Assert `CCCC Ltd`, the TDC legal name, and the Arabic TDC alias all produce a number containing `-TDC-`; assert `هيونداي` produces `INV-MT-HDEC-` after saving Hyundai master data.

- [ ] **Step 2: Run and verify RED**

Run: `python tests/client_master_backend.test.py`

Expected: raw aliases appear in generated numbers.

- [ ] **Step 3: Resolve at the bridge boundary**

Add `clients.document_code_for(value)` and use it in `Api.get_next_quote_number`, `get_next_dn_number`, and `get_next_inv_number`. In every `save_*`, pass the code to numbering/folder functions while retaining `client_name` in the generator and database payload.

- [ ] **Step 4: Run and verify GREEN**

Run: `python tests/client_master_backend.test.py`

Expected: aliases share the canonical code and historical names remain untouched.

### Task 4: Client master editor and bridge methods

**Files:**
- Modify: `backend/api.py`
- Modify: `static/clients.js`
- Modify: `static/app_styles.css`
- Create: `tests/client_master_frontend.test.js`

- [ ] **Step 1: Write the failing source behavior test**

Assert the bridge exposes `client_master_get`, `client_master_save`, and `resolve_client`; assert Clients UI renders an Edit master data button and fields for code, legal names, aliases, payment, address, and withholding.

- [ ] **Step 2: Run and verify RED**

Run: `node tests/client_master_frontend.test.js`

Expected: missing bridge/UI symbols.

- [ ] **Step 3: Add bridge methods**

Expose thin methods delegating to `clients.get_client_master`, `clients.save_client_master`, and `clients.resolve_client`.

- [ ] **Step 4: Add the modal editor**

Add an `Edit master data` action in `renderProfile`. Load the record, render concise fields and a newline-separated alias list, save through the bridge, toast validation errors, then reload the sidebar and selected profile.

- [ ] **Step 5: Run and verify GREEN**

Run: `node tests/client_master_frontend.test.js`

Expected: `client master frontend tests passed`.

### Task 5: Apply invoice defaults and preserve manual/edit values

**Files:**
- Modify: `static/inv.js`
- Modify: `static/quote.js`
- Modify: `static/dn.js`
- Extend: `tests/client_master_frontend.test.js`

- [ ] **Step 1: Add failing default-flow assertions**

Assert invoice client changes call `resolve_client`, set bank/address/withholding for a new invoice, use the returned code for number refresh, and do not reapply defaults in edit mode. Assert DN-to-invoice runs the same resolver after copying the source client.

- [ ] **Step 2: Run and verify RED**

Run: `node tests/client_master_frontend.test.js`

Expected: client changes only refresh the number.

- [ ] **Step 3: Add editor resolution helpers**

Implement `resolveInvClient({applyDefaults})`, store `clientId` and `clientCode` in state/payload, and set the printable name only when it exactly matches a known alias and a default print name is available. Call it for new-client changes and DN hand-offs, but never while loading an existing invoice.

For quote and DN, resolve IDs/codes before number refresh and include `client_id` in payloads; keep the user's printable text.

- [ ] **Step 4: Run and verify GREEN**

Run: `node tests/client_master_frontend.test.js`

Expected: defaults apply only to new documents and hand-offs.

### Task 6: Make an invoice directly from a saved PO

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/inv.js`
- Modify: `backend/api.py`
- Create: `tests/po_make_invoice_client_master.test.js`
- Extend: `tests/client_master_backend.test.py`

- [ ] **Step 1: Add failing PO resolver/backend assertions**

Save a PO containing `هيونداي`, resolve it, and assert the result contains the HDEC client, exact item index, and copied row. Add unresolved and multi-client cases.

- [ ] **Step 2: Run and verify RED**

Run: `python tests/client_master_backend.test.py`

Expected: missing `client_for_po` bridge behavior.

- [ ] **Step 3: Implement PO preparation**

Add `clients.prepare_po_invoice(po_no)` to resolve non-internal PO rows through aliases and return one canonical client plus source item indexes. Expose it through `Api.prepare_po_invoice`.

- [ ] **Step 4: Add failing frontend hand-off assertions**

Assert the PO toolbar includes `btnPoInvoice`, app.js calls `prepare_po_invoice`, inv.js exposes `createInvoiceFromPo`, applies client defaults, copies rows, and sends `source_po_no` plus `source_po_item_indexes` in its save payload.

- [ ] **Step 5: Run and verify RED**

Run: `node tests/po_make_invoice_client_master.test.js`

Expected: missing toolbar and hand-off symbols.

- [ ] **Step 6: Implement the hand-off and fulfillment link**

Add the button and saved-PO guard. Create a materials invoice from the prepared payload. After `database.save_inv_record`, call `database.set_po_fulfillment(source_po_no, indexes, "inv", inv_no, True)` and return the result as `po_workflow_link`.

- [ ] **Step 7: Run and verify GREEN**

Run both new tests. Expected: both print their passed messages.

### Task 7: Map updates and regression verification

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/04-BACKEND-DOCGEN.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`
- Modify: `docs/09-DATA-MODEL.md`

- [ ] **Step 1: Update the map**

Document the shared client schema, bridge methods, number-code resolution, editor defaults, and PO-to-invoice flow in the owning map files.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python tests/client_master_backend.test.py
node tests/client_master_frontend.test.js
node tests/po_make_invoice_client_master.test.js
python tests/client_rename_isolation_backend.test.py
node tests/dn_make_invoice_workflow_frontend.test.js
```

Expected: every command exits 0 and prints its passed message.

- [ ] **Step 3: Run repository smoke tests**

Run:

```powershell
python smoke_test.py
python smoke_quote.py
```

Expected: both exit 0.

- [ ] **Step 4: Inspect the final diff**

Run `git diff --check` and `git status --short`. Confirm only client-master implementation/docs/tests plus the user's pre-existing files are changed.
