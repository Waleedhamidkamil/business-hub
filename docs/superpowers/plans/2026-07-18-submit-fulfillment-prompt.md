# Submit Fulfillment Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the existing fulfillment picker after DN/Invoice submission from every right-panel and To-Do surface.

**Architecture:** Add one shared frontend hand-off in `app.js`; call it only after successful persistence and UI refresh. Keep `todo.js` responsible for closing its preview modal before the hand-off.

**Tech Stack:** Vanilla JavaScript, pywebview frontend bridge, Node contract tests.

---

### Task 1: Regression Contract

**Files:**
- Create: `tests/todo_submit_fulfillment.test.js`

- [x] Assert one shared helper accepts DN/Invoice and delegates to `openPoFulfillPicker`.
- [x] Assert right-panel, To-Do inline/modal, and dashboard inline submits reuse it after refresh.
- [x] Run the test and confirm it fails before production edits.

### Task 2: Shared Submit Hand-Off

**Files:**
- Modify: `static/app.js`
- Modify: `static/todo.js`

- [x] Implement `openSubmittedFulfillment` with DN/Invoice-only scope.
- [x] Replace the right-panel DN-only branch with the shared helper.
- [x] Call the helper after To-Do/modal/dashboard refresh; close preview modals first.
- [x] Run the focused test until green.

### Task 3: Documentation And Verification

**Files:**
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`

- [x] Document the shared helper and ordering invariant.
- [x] Run all frontend tests and JavaScript syntax checks.

No Git commands are part of this plan, per the user's instruction.
