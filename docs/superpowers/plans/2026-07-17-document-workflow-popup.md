# Document Workflow Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PO/DN/Invoice editor Workflow popup that displays and extends the existing delivery/payment links.

**Architecture:** Resolve fulfillment relationships in one backend function that returns PO lanes, then render them in a shared frontend module. All link writes continue through the existing PO fulfillment pickers.

**Tech Stack:** Python, SQLite, pywebview API bridge, vanilla JavaScript, HTML, CSS, Node/Python regression tests.

---

### Task 1: Lock The Workflow Contract With Failing Tests

**Files:**
- Create: `tests/document_workflow_backend.test.py`
- Create: `tests/document_workflow_frontend.test.js`

- [ ] Add isolated SQLite fixtures for a PO, two DNs, and two invoices linked through `po_fulfillment`.
- [ ] Assert `database.document_workflow(kind, no)` returns one lane per PO, includes the active document, deduplicates source documents, and keeps unlinked DN/INV actionable.
- [ ] Assert only PO/DN/INV toolbars contain Workflow, the shared module is loaded after `po_link.js`, and it delegates missing stages to `openPoFulfillPicker` or `openPoSourcePicker`.
- [ ] Run both tests and confirm they fail because the workflow API, toolbar controls, and module do not exist.

### Task 2: Add The Unified Backend Read Model

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/api.py`
- Test: `tests/document_workflow_backend.test.py`

- [ ] Implement `document_workflow(kind, doc_no)` for only `po`, `dn`, and `inv`.
- [ ] Build stable document nodes with kind, number, date, client, item count, and current-document state.
- [ ] Return PO-centered lanes using `po_fulfillment`, with an unlinked lane for an orphan DN or invoice.
- [ ] Expose the function as `Api.document_workflow` with a guarded error response.
- [ ] Run the backend test until green.

### Task 3: Add Toolbar Entry Points And Shared Popup

**Files:**
- Create: `static/document_workflow.js`
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/dn.js`
- Modify: `static/inv.js`
- Modify: `static/styles.css`
- Modify: `static/dark.css`
- Test: `tests/document_workflow_frontend.test.js`
- Test: `tests/editor_toolbar_layout.test.js`

- [ ] Add a Workflow icon-and-text button after Sign in the three eligible editor toolbars.
- [ ] Wire each editor to pass only its saved identity and fulfillment refresh callback.
- [ ] Render accessible PO lanes, active-document styling, compact linked-document nodes, empty-stage actions, loading, and error states.
- [ ] Reuse `openPoFulfillPicker` for DN/INV-to-PO and `openPoSourcePicker` for PO-to-DN/INV; reopen Workflow after a successful link.
- [ ] Open linked documents through the app's existing editor navigation functions.
- [ ] Add responsive and dark-theme styling without changing existing link popup selectors.
- [ ] Run frontend tests until green.

### Task 4: Documentation And Regression Verification

**Files:**
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`

- [ ] Document the workflow read model, API bridge method, shared module, toolbar scope, and picker handoff rules.
- [ ] Run workflow tests, toolbar tests, fulfillment suggestion tests, status precedence tests, and JavaScript syntax checks.
- [ ] Run `python smoke_test.py` and report any environment limitation explicitly.

No Git commands are part of this plan, per the user's instruction.

### Task 5: Linked Picker Tabs And Workflow Link Management

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/api.py`
- Modify: `static/po_link.js`
- Modify: `static/document_workflow.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`
- Modify: `tests/document_workflow_backend.test.py`
- Modify: `tests/document_workflow_frontend.test.js`
- Modify: `tests/po_link_suggestions_backend.test.py`

- [ ] Add failing tests for exact PO/source unlinking, linked-tab separation, the invoice's third linked-DN route, Manage-link targeting, and replace-after-success ordering.
- [ ] Implement `clear_po_fulfillment_link(po_no, source_kind, source_no)` and expose it through `Api` without changing submitted/paid state.
- [ ] Refactor both fulfillment picker directions to render Available and Linked datasets in separate tabs while keeping Suggestions only in Available.
- [ ] Add a compact X to eligible workflow nodes; open a Replace/Remove/Cancel decision dialog and refresh Workflow after removal.
- [ ] For replacement, hand off to the existing smart picker and clear the old exact link only from the successful `onAfter` callback.
- [ ] Run all workflow/link/status/scroll tests plus isolated smoke tests.

### Task 6: Allow One Invoice To Consume Multiple Delivery Notes

**Files:**
- Modify: `backend/database.py`
- Modify: `static/document_workflow.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`
- Modify: `tests/document_workflow_backend.test.py`
- Modify: `tests/document_workflow_frontend.test.js`
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`

- [x] Add an isolated fixture where two DNs deliver different indexes of one PO and one invoice pays both indexes; assert the invoice node reports both DN contributions once with their overlap counts.
- [x] Add direct and mixed fixtures; assert a direct-to-PO invoice reports no contributing DNs and the correct direct item count.
- [x] Run the backend test and confirm it fails because invoice nodes do not yet include contribution metadata.
- [x] Derive each invoice node's `delivery_notes` and `direct_item_count` from delivered/paid item-index intersections inside each PO lane, without adding schema or writes.
- [x] Run the backend test until green.
- [x] Add frontend contract assertions for contribution labels and an invoice-node `Add delivery note` action that calls `openPoFulfillPicker(invoice, {initialTab: "dn"})` and refreshes Workflow after success.
- [x] Run the frontend test and confirm it fails before changing production JavaScript.
- [x] Render compact DN contribution chips, a `Direct to PO` fallback, mixed direct-item metadata, and the add-DN action without nesting buttons.
- [x] Add light, dark, and responsive styles for the contribution row.
- [x] Run workflow tests, all frontend contract suites, fulfillment/status regressions, Python/JavaScript syntax checks, profile isolation, and isolated PDF smoke tests.

No Git commands are part of this extension, per the user's instruction.
