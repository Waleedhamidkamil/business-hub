# Document Status Precedence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show one coherent operational status on document cards while preserving all underlying document state.

**Architecture:** Derive DN/invoice link state from the existing `po_fulfillment` table and annotate monthly API list rows. Keep workflow data unchanged, then apply a small frontend precedence layer in the existing card renderers.

**Tech Stack:** Python, SQLite, pywebview bridge, vanilla JavaScript, CSS.

---

### Task 1: Backend linked-source annotation

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/api.py`
- Test: `tests/document_status_precedence_backend.test.py`

- [ ] Write a failing backend test that seeds PO fulfillment and asserts DN/invoice monthly list rows receive `linked=True` while unrelated rows remain false.
- [ ] Run `python tests/document_status_precedence_backend.test.py` and confirm it fails because the list rows have no linked annotation.
- [ ] Add a bulk `linked_source_set(source_kind)` query and use it in the DN/invoice monthly list APIs.
- [ ] Re-run the backend test and the existing fulfillment/status tests.

### Task 2: Frontend status precedence

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/quote.js`
- Modify: `static/dn.js`
- Modify: `static/inv.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`
- Test: `tests/document_status_precedence_frontend.test.js`

- [ ] Write a failing source/behavior regression covering removal of Submitted pills, Signed suppression after submission, Linked source badges, invoice Paid precedence, and PO Paid-over-Delivered precedence.
- [ ] Run `node tests/document_status_precedence_frontend.test.js` and confirm the current markup fails the contract.
- [ ] Add shared Linked badge markup, remove Submitted-pill rendering/topbar markup, and update each right-panel renderer to enforce the approved precedence.
- [ ] Ensure submit and paid button actions rerender their card status immediately while preserving the underlying flags.
- [ ] Re-run the frontend regression and existing invoice/toolbar/panel tests.

### Task 3: Documentation and verification

**Files:**
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/08-STYLES-TEMPLATES.md`

- [ ] Update the map with the derived linked annotation and right-panel precedence rules.
- [ ] Run JavaScript syntax checks and Python compilation.
- [ ] Run the focused backend/frontend regressions plus related existing suites.
- [ ] Run the isolated smoke test, including PDF generation, and inspect `git diff --check`.
