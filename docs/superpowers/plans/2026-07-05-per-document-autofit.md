# Per-Document Auto-Fit Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a visible per-editor Auto-fit rows toggle whose value is saved per PO, QUT, DN, and INV document.

**Architecture:** Store per-document layout metadata in `doc_meta` under the `layout` key. Frontend editor state carries `autofitRows`, payloads include `{layout:{autofit_rows:<bool>}}`, API save/load persists it, and generators prefer the payload value over type defaults.

**Tech Stack:** pywebview bridge, Python backend, SQLite, vanilla JS frontend, Jinja PDF templates.

---

### Task 1: Backend Round-Trip Test

**Files:**
- Create: `tests/doc_autofit_layout_backend.test.py`

- [ ] Write a failing Python test that uses an isolated SQLite DB, saves PO/QUT/DN/INV records, stores explicit document layout metadata, and asserts `Api.load_*` returns the saved `layout.autofit_rows` values.

- [ ] Run `python tests\doc_autofit_layout_backend.test.py` and confirm it fails because document layout metadata helpers/load fields do not exist yet.

### Task 2: Backend Storage And API

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/api.py`

- [ ] Add `set_doc_layout(doc_type, doc_no, layout)` and `get_doc_layout(doc_type, doc_no)` helpers backed by the existing `doc_meta` table shape.

- [ ] Save `layout` in `save_quote`, `save_dn`, `save_inv`, `save_po`, and `update_po`.

- [ ] Load `layout` in `load_quote`, `load_dn`, `load_invoice`, and `load_po`.

- [ ] Rerun the backend layout test and confirm it passes.

### Task 3: Generator Payload Override

**Files:**
- Modify: `backend/pdf_generator.py`
- Modify: `backend/quote_generator.py`
- Modify: `backend/dn_generator.py`
- Modify: `backend/inv_generator.py`

- [ ] Add a small resolver in each generator that uses `payload.layout.autofit_rows` when present, otherwise the existing type/default setting.

- [ ] Thread the resolved boolean into pagination and template rendering.

### Task 4: Frontend Toolbar Toggle

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/quote.js`
- Modify: `static/dn.js`
- Modify: `static/inv.js`
- Modify: `static/app_styles.css`

- [ ] Add an Auto-fit rows toggle button to PO, QUT, DN, and INV action groups, before the settings gear.

- [ ] Add shared toggle styling.

- [ ] Add editor state fields, sync helpers, and payload `layout` for all four editors.

- [ ] Load saved document layout into the state; new documents use type defaults.

### Task 5: Docs And Verification

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/04-BACKEND-DOCGEN.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/09-DATA-MODEL.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`

- [ ] Update the map to document per-document `layout` metadata and the visible toolbar toggle.

- [ ] Run focused tests, then `python smoke_test.py` and `python smoke_quote.py`.

