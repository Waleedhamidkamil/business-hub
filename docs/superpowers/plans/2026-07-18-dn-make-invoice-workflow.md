# DN Make Invoice Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically connect invoices created with a DN's Make Invoice button to that DN in the existing PO-centered Workflow.

**Architecture:** Carry a transient source DN through the invoice draft payload, then atomically copy the DN's delivered PO indexes to the saved invoice's paid indexes. Keep `po_fulfillment` as the only relationship source and skip conflicting paid indexes.

**Tech Stack:** Python, SQLite, pywebview API bridge, vanilla JavaScript, Node/Python regression tests.

---

### Task 1: Lock The Backend Contract

**Files:**
- Create: `tests/dn_make_invoice_workflow_backend.test.py`
- Modify: `backend/database.py`
- Modify: `backend/api.py`

- [x] Seed isolated PO, DN, and invoice rows with one DN delivering lines across multiple POs and one line paid by another invoice.
- [x] Assert automatic linking writes only available paid marks, preserves the conflict, and makes Workflow show the source DN under the saved invoice.
- [x] Assert repeated linking is idempotent and a DN with no PO links returns a successful no-link result.
- [x] Patch PDF rendering/numbering in the isolated test and assert `Api.save_inv({source_dn_no})` invokes the same relationship helper after saving.
- [x] Run the test first and confirm failure because the helper/result do not exist.
- [x] Implement `link_invoice_from_dn` and integrate it into `Api.save_inv` without changing the invoice schema.
- [x] Run the backend test until green.

### Task 2: Carry The Source DN Through The Invoice Draft

**Files:**
- Create: `tests/dn_make_invoice_workflow_frontend.test.js`
- Modify: `static/inv.js`

- [x] Assert invoice state has transient `sourceDn`, fresh/load paths clear it, and `buildInvPayload` sends `source_dn_no`.
- [x] Assert only `createInvoiceFromDnNo` calls `importDnIntoInv` with workflow-link intent; the general import path remains ordinary.
- [x] Assert successful save clears the transient source and handles conflict/no-PO link results.
- [x] Run the frontend test first and confirm failure.
- [x] Implement the minimal state, payload, hand-off, and feedback changes.
- [x] Run the frontend test until green.

### Task 3: Documentation And Regression Verification

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`

- [x] Document the transient payload field, atomic helper, conflict behavior, and Make Invoice scope.
- [x] Run all frontend tests, Workflow/fulfillment/status/delete backend tests, syntax checks, profile isolation, and isolated PDF smoke.

No Git commands are part of this plan, per the user's instruction.
