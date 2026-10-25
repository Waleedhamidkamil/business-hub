# Unified Item Review Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two divergent PO item-review renderers with one compact reusable class that correctly presents A-88's three matched, rate-only items and supports either language direction.

**Architecture:** Add a focused `window.ItemReviewPopup` class that renders and controls item-review rows while delegating modal chrome to `window.Popup`. `po_handoff.js` keeps resolution, readiness, document construction, and Master Data write-back, then adapts its decisions into the class and reads normalized values back. No backend or database schema change is required.

**Tech Stack:** Plain browser JavaScript, existing `window.Popup`, existing Gemini bridge, HTML/CSS, Node source-contract tests, Python backend resolver tests, WebView2 browser verification.

---

## File structure

- Create `static/item_review_popup.js`: reusable class, compact renderer, event delegation, AI action, validation, and normalized result reading.
- Create `static/item_review_popup.css`: the component's layout and states, separate from PO business-flow styling.
- Modify `templates/index.html`: load the new CSS and JavaScript before `po_handoff.js`.
- Modify `static/po_handoff.js`: adapt decisions into `ItemReviewPopup`, remove the two local review renderers, preserve business and write-back helpers.
- Create `tests/item_review_popup.test.js`: component API, rendering, direction, rate-only, candidate, AI visibility, and source-contract coverage.
- Modify `tests/po_handoff_review.test.js`: require the shared class and remove assertions that preserve separate renderers.
- Modify `tests/po_make_document_resolve_backend.test.py`: fixture proving a matched bilingual item without price is rate-only, not a new item.
- Modify `docs/07-FRONTEND-SUPPORT.md`: document the new component and PO callers.
- Modify `docs/14-UI-CONTRACTS.md`: record the item-review contract beneath the existing popup-chrome contract.

### Task 1: Pin the A-88 failure as a regression

**Files:**
- Modify: `tests/po_make_document_resolve_backend.test.py`
- Create: `tests/item_review_popup.test.js`

- [ ] **Step 1: Add a backend fixture for a known item with no rate**

Create a bilingual Master Data record equivalent to A-88's Fig row, resolve its Arabic name for an invoice, and assert that identity, English name, and unit are present while rate is absent:

```python
resolved = master_data.resolve_items(
    ["تين"], doc_type="inv", sell_currency="USD", sell_peg=0
)["results"][0]
self.assertEqual(resolved["item_id"], fig_id)
self.assertEqual(resolved["name_en"], "Fig")
self.assertEqual(resolved["fill"]["unit"], "KG")
self.assertNotIn("rate", resolved["fill"])
```

- [ ] **Step 2: Add a failing component contract test**

Load `static/item_review_popup.js` in a VM with a small DOM double and assert this row model:

```js
const row = {
  id: "fig",
  sourceName: "تين",
  targetName: "Fig",
  matched: true,
  missing: ["rate"],
  fields: { rate: "" },
};
const html = ItemReviewPopup.rowHtml(row, 0, { targetLanguage: "en", requireRate: true });
assert(html.includes("Fig"));
assert(html.includes('data-irp-field="0:rate"'));
assert(!html.includes('data-irp-field="0:name_en"'));
assert(!/new item|nothing on file/i.test(html));
```

- [ ] **Step 3: Run both tests and confirm the component test fails**

Run:

```text
python tests/po_make_document_resolve_backend.test.py
node tests/item_review_popup.test.js
```

Expected: backend fixture passes, Node test fails because `window.ItemReviewPopup` does not exist.

- [ ] **Step 4: Commit the red tests**

Stage only the two test paths and commit with `test: pin rate-only item review`.

### Task 2: Build the reusable class and compact layout

**Files:**
- Create: `static/item_review_popup.js`
- Create: `static/item_review_popup.css`
- Modify: `templates/index.html`
- Test: `tests/item_review_popup.test.js`

- [ ] **Step 1: Implement the public class boundary**

Expose a class with pure static render helpers and one async instance method:

```js
class ItemReviewPopup {
  constructor(options) {
    this.options = normalizeOptions(options);
    this.rows = this.options.rows.map(normalizeRow);
  }

  static rowHtml(row, index, options) { return rowHtml(row, index, options); }
  static tableHtml(rows, options) { return tableHtml(rows, options); }
  async open() { /* Popup.open, bind, validate, return decisions */ }
}
window.ItemReviewPopup = ItemReviewPopup;
```

Required options are `title`, `primaryLabel`, `sourceLanguage`, `targetLanguage`, and `rows`. Optional options are `subtitle`, `readyCount`, `suggestLabel`, `suggest`, `onSubmit`, and `allowDocumentOnly`.

- [ ] **Step 2: Implement field-by-field row rendering**

Rules:

- Always show source name and quiet source context.
- Render a confirmed target name as text when matched and nonblank.
- Render a target-name input only when that field is missing.
- Render unit and rate inputs only when missing.
- Keep description, cost, candidates, and document-only choice in the Details row.
- Use `data-irp-*` hooks only inside this module.
- Spell every state in text. Color is reinforcement only.

- [ ] **Step 3: Implement event delegation and normalized reading**

One click listener controls Details and candidate buttons. One input listener clears AI suggestion state and switches a row from candidate to manual when the target name is edited. One change listener controls Skip. `read()` returns one object per row with `mode`, `candidate`, `typed`, and original `id`.

- [ ] **Step 4: Implement conditional AI behavior**

The footer suggestion action is created only when `suggest` exists and at least one visible row is missing a target-language name or description. During the request, disable the button and show `Asking...`; never overwrite a nonblank field; mark inserted values with both `.is-ai-suggested` and visible `AI suggestion` text; restore the button after success or failure.

- [ ] **Step 5: Add the component stylesheet**

Use an opaque surface, 22px popup gutter, compact 42px primary rows, aligned columns, 32px controls, strong focus rings, and a single quiet details row. At widths below 760px, stack source and target content without horizontal scrolling. Use only transform and opacity for the short 160ms disclosure/state transitions, and disable movement under `prefers-reduced-motion`.

- [ ] **Step 6: Load the component before the PO caller**

Add `item_review_popup.css` with the other shared styles and `item_review_popup.js` after `popup.js` but before `po_handoff.js` in `templates/index.html`.

- [ ] **Step 7: Run the component test**

Run `node tests/item_review_popup.test.js`.

Expected: all rate-only, direction, AI visibility, candidate, skip, and markup checks pass.

- [ ] **Step 8: Commit the component**

Stage only the new component, its stylesheet, template include, and component test. Commit with `feat: add shared item review popup`.

### Task 3: Migrate both PO review paths to the class

**Files:**
- Modify: `static/po_handoff.js`
- Modify: `tests/po_handoff_review.test.js`
- Test: `tests/item_review_popup.test.js`

- [ ] **Step 1: Replace renderer-specific source assertions**

Change the existing test so it requires both catalogue save and handoff review to call `new window.ItemReviewPopup(...)`. Assert that `catalogueRowHtml`, `catalogueTableHtml`, `unreadyRowHtml`, and `bindCatalogueRows` no longer exist in `po_handoff.js`.

- [ ] **Step 2: Run the updated source contract and confirm it fails**

Run `node tests/po_handoff_review.test.js`.

Expected: failure because `po_handoff.js` still owns both renderers.

- [ ] **Step 3: Add a decision-to-row adapter**

Create one pure helper in `po_handoff.js`:

```js
function reviewRowFromDecision(d, kind, index) {
  const fill = decisionFill(d);
  const missing = kind === "catalogue"
    ? ["name"].filter(() => recordNeedsEnglish(d.result))
    : readinessFor(fill, d.poRow, kind).missing;
  return {
    id: index,
    sourceName: d.query,
    sourceDescription: d.poRow && d.poRow.description,
    sourceUnit: d.poRow && d.poRow.unit,
    targetName: fill.item || d.result.name_en || "",
    targetDescription: fill.description || d.result.description_en || "",
    targetUnit: fill.unit || d.suggestedUnit || "",
    rate: fill.rate,
    rateCurrency: fill.rate_ccy,
    cost: fill.cost,
    costCurrency: fill.cost_ccy,
    matched: d.result.item_id != null,
    missing,
    candidates: englishSuggestions(d.result),
  };
}
```

- [ ] **Step 4: Replace `openReview` markup with class configuration**

Instantiate the same class for both paths. Catalogue mode supplies `primaryLabel: "Save to Master Data"`, `suggestLabel: "Suggest English"`, and the existing Gemini callback. Invoice mode supplies `primaryLabel: "Create invoice"`, `requireRate: true`, and no AI action when all missing fields are rate-only. Delivery Note uses `Create delivery note` and does not require rate.

- [ ] **Step 5: Map class results back into existing decisions**

Preserve `buildMasterPatch`, `writeTargetId`, `decisionFill`, and `writeBack`. Copy the class result's `mode`, `candidate`, and `typed` into the original decision object, including currency pairs. Keep the current rule that catalogue completion has no print gate and handoff completion validates every included row.

- [ ] **Step 6: Remove the two local renderers and duplicate AI binder**

Delete `moneyChip`, `unreadyRowHtml`, `catalogueRowHtml`, `catalogueTableHtml`, `bindCatalogueRows`, `readReview`, and `suggestEnglish` from `po_handoff.js` after their responsibilities move to the class. Keep `readyRowHtml` only if the shared class does not own ready-count display; otherwise remove it too.

- [ ] **Step 7: Run focused frontend tests**

Run:

```text
node tests/item_review_popup.test.js
node tests/po_handoff_review.test.js
node tests/popup_contract.test.js
node tests/master_data_fill.test.js
```

Expected: all pass.

- [ ] **Step 8: Commit the migration**

Stage only `static/po_handoff.js` and its focused tests. Commit with `refactor: unite item review workflows`.

### Task 4: Verify A-88 and browser behavior

**Files:**
- Modify only if a failing check identifies a defect in the files already in scope.

- [ ] **Step 1: Run the live A-88 diagnostic without writing data**

Load A-88, resolve all 46 names for an invoice, and feed the results through the exported pure adapter/readiness helpers.

Expected:

- 46 matched Master Data identities;
- 43 ready rows;
- 3 rate-only rows: Fig, Red Plum, Low Fat Milk;
- 0 rows classified as new or missing an English name.

- [ ] **Step 2: Launch the app and open A-88's Make Invoice review**

Verify visually:

- the popup shows three compact rows;
- English names are confirmed text;
- each row shows one Rate input in the main row;
- no new-item or nothing-on-file wording appears;
- the Gemini button is absent because the only missing field is rate;
- Details exposes cost and document-only behavior without expanding other rows;
- keyboard focus, Escape, backdrop close, and focus restoration follow `window.Popup`.

- [ ] **Step 3: Exercise PO save-time completion with a temporary fixture**

Use an isolated test record, not the live catalogue. Confirm the same class shows Arabic source, editable English target, mapped unit, and Suggest English when configured.

- [ ] **Step 4: Exercise future English-to-Arabic configuration**

Instantiate the class in the component test with `sourceLanguage: "en"`, `targetLanguage: "ar"`. Confirm RTL target input, `name_ar` reading, and `Suggest Arabic` label from configuration. No backend request is made.

- [ ] **Step 5: Run syntax and focused backend tests**

Run:

```text
node --check static/item_review_popup.js
node --check static/po_handoff.js
python tests/po_make_document_resolve_backend.test.py
python tests/po_master_writeback_backend.test.py
python tests/po_catalogue_review_backend.test.py
```

Expected: all pass.

### Task 5: Update the map and complete verification

**Files:**
- Modify: `docs/07-FRONTEND-SUPPORT.md`
- Modify: `docs/14-UI-CONTRACTS.md`
- Modify: `AGENT-BOARD.md`

- [ ] **Step 1: Document the shared component**

In `docs/07-FRONTEND-SUPPORT.md`, add its public API, callers, data flow, target-language configuration, AI conditions, and the A-88 rate-only rule. Remove text claiming catalogue and handoff intentionally require separate renderers.

- [ ] **Step 2: Extend the popup contract**

In `docs/14-UI-CONTRACTS.md`, state that all item-completion workflows use `ItemReviewPopup` for content and `Popup` for chrome. Record the field-by-field rendering rule and its component test.

- [ ] **Step 3: Run the full affected verification set**

Run every focused test from Tasks 1 through 4, then:

```text
python smoke_test.py
python smoke_quote.py
git diff --check
```

Expected: every command exits 0, both smoke scripts report passed, and no whitespace errors appear.

- [ ] **Step 4: Ask the two propagation questions**

Search all `static/*.js` for the removed renderer names and direct item-review popup markup. Confirm no caller remains. Report the next approved reuse point by name and count: the Quotation editor and the current number of English-only Master Data records missing Arabic.

- [ ] **Step 5: Update and release the board**

Add the test evidence and A-88 result to Done, remove the active row, and release the test lock.

- [ ] **Step 6: Commit documentation using explicit paths**

Because the workspace contains unrelated work, inspect each file before staging. Stage only safe task-owned paths or record why an overlapping file remains uncommitted.
