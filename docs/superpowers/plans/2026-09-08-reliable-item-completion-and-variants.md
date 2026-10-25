# Reliable Item Completion and Variant-Safe Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one AI action reliably complete missing item language details, preserve same-name material variants, and show exact blockers for PO-to-DN and PO-to-Invoice creation.

**Architecture:** `static/po_handoff.js` remains the business adapter and owns composite material identity, local unit enrichment, candidate safety, and validation semantics. `static/item_review_popup.js` remains the reusable UI component and owns bounded AI orchestration plus field-level error presentation. `backend/translate.py` accepts only safe printable candidate text and returns normalized, index-safe proposals without learning document or commercial data.

**Tech Stack:** Plain browser JavaScript, pywebview bridge, Python 3, SQLite-backed Master Data, Gemini REST adapter, Node contract tests, Python unittest.

---

## File map

- Modify `static/po_handoff.js`: composite review keys, local unit enrichment, candidate request adapter, ambiguous-choice rules, structured validation.
- Modify `static/item_review_popup.js`: one-click two-stage completion, AI candidate application, exact error marking and focus.
- Modify `static/item_review_popup.css`: accessible invalid states and compact inline error labels.
- Modify `backend/translate.py`: safe candidate serialization, complete normalized response shape, bounded candidate-index parser.
- Modify `tests/po_handoff_review.test.js`: A-83 units/rates and A-89 variant identity regression coverage.
- Modify `tests/item_review_popup.test.js`: one-click repair, candidate selection, and validation UI contracts.
- Modify `tests/translate_suggestions.test.py`: prompt privacy and parser bounds.
- Modify `docs/06-FRONTEND-EDITORS.md`, `docs/07-FRONTEND-SUPPORT.md`, and `docs/02-BACKEND-CORE.md`: update the code map and behavioral contract.

### Task 1: Preserve material variants and enrich local units

**Status:** Complete

**Files:**
- Modify: `tests/po_handoff_review.test.js`
- Modify: `static/po_handoff.js`

- [ ] **Step 1: Write failing composite-identity and local-unit tests**

Add direct helper assertions:

```javascript
assert(H.handoffReviewKey({ name: "سبلت جداري", description: "طن ونص", unit: "عدد" })
    !== H.handoffReviewKey({ name: "سبلت جداري", description: "2 طن", unit: "عدد" }),
  "same-name products with different descriptions must remain separate");
assert(H.handoffReviewKey({ name: " سبلت  جداري ", description: "2 طن", unit: " عدد " })
    === H.handoffReviewKey({ name: "سبلت جداري", description: "2 طن", unit: "عدد" }),
  "harmless whitespace must not split an identical product");

const locallyCompleted = H.applySuggestedUnit({
  poRow: { name: "سيارة ابيض", unit: "عدد" }, fill: { item: "سيارة ابيض", unit: "عدد" }
}, { "عدد": "EA" });
assert(locallyCompleted.suggestedUnit === "EA", "A-83 must get EA from Master Data");
assert(H.decisionFill(locallyCompleted).unit === "EA",
  "readiness and the popup must use the same local unit");
```

- [ ] **Step 2: Run the focused test and confirm the new helpers are missing**

Run: `node tests/po_handoff_review.test.js`

Expected: FAIL because `handoffReviewKey` or `applySuggestedUnit` is not defined.

- [ ] **Step 3: Implement composite identity and local unit overlay**

Add pure helpers and export them through `window.__poHandoffInternals`:

```javascript
function normalReviewText(value) {
    return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}
function handoffReviewKey(row) {
    row = row || {};
    return [row.name, row.description, row.unit].map(normalReviewText).join("\x1f");
}
function applySuggestedUnit(decision, units) {
    const source = String((decision.poRow && decision.poRow.unit) || "").trim();
    const known = units && units[source];
    if (known && (!hasValue(decision.fill.unit) || isArabic(decision.fill.unit))) {
        decision.suggestedUnit = known;
    }
    return decision;
}
```

Make `decisionFill` apply `suggestedUnit` whenever the effective unit is blank or Arabic. Replace the name-only map in `poMakeDocument` with a map keyed by `handoffReviewKey`, retaining each distinct description and unit. Load `unitMap()` before initial readiness and enrich every decision.

- [ ] **Step 4: Run the focused test**

Run: `node tests/po_handoff_review.test.js`

Expected: PASS, including separate A-89 variant keys and A-83 local unit completion.

### Task 2: Make ambiguous candidate handling safe and validation specific

**Status:** Complete

**Files:**
- Modify: `tests/po_handoff_review.test.js`
- Modify: `static/po_handoff.js`

- [ ] **Step 1: Write failing validation tests**

Pin the adapter contract:

```javascript
const ambiguous = {
  query: "سبلت جداري", poRow: { name: "سبلت جداري", description: "2 طن", unit: "عدد" },
  rowReady: false, mode: "manual", result: { ambiguous_variants: true },
  fill: {}, typed: { name_en: "Wall Split Unit", unit_en: "EA" }
};
assert(H.needsVariantChoice(ambiguous), "manual text cannot silently create an ambiguous duplicate");
ambiguous.mode = "asis";
assert(!H.needsVariantChoice(ambiguous), "document-only mode is an explicit safe choice");

const invoiceErrors = H.reviewValidation([
  { query: "تين", poRow: { name: "تين" }, rowReady: false, mode: "manual",
    result: {}, fill: { item: "Fig" }, typed: {} }
], "inv", false);
assert(invoiceErrors.errors[0].fields.includes("rate"));
assert(/AI completes language details, not prices/.test(invoiceErrors.message));
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `node tests/po_handoff_review.test.js`

Expected: FAIL because `needsVariantChoice` and `reviewValidation` are not defined.

- [ ] **Step 3: Implement choice-aware rows and structured validation**

Add `ambiguous: true` to review rows that have multiple candidates, expose candidates even when the English name is known, and pass candidate display text to the suggestion bridge. Implement:

```javascript
function needsVariantChoice(d) {
    return !!(d.result && d.result.ambiguous_variants)
        && d.mode !== "candidate" && d.mode !== "asis" && d.mode !== "exclude";
}
function reviewValidation(decisions, kind, catalogue) {
    if (catalogue) return { message: "", errors: [] };
    const errors = [];
    decisions.forEach((d, id) => {
        if (d.rowReady || d.mode === "exclude") return;
        const fields = readinessFor(decisionFill(d), d.poRow, kind).missing.slice();
        if (needsVariantChoice(d)) fields.push("choice");
        if (fields.length) errors.push({ id, fields: Array.from(new Set(fields)) });
    });
    return { message: validationMessage(decisions, errors), errors };
}
```

Block `buildMasterPatch` for unresolved ambiguous manual decisions. Have `openReview.validate` return this object, with separate language, candidate-choice, and invoice-rate sentences. Keep catalogue mode permissive and document-only mode explicit.

- [ ] **Step 4: Run the focused test**

Run: `node tests/po_handoff_review.test.js`

Expected: PASS with rate-specific and choice-specific validation.

### Task 3: Complete missing details with one AI action

**Status:** Complete

**Files:**
- Modify: `tests/item_review_popup.test.js`
- Modify: `static/item_review_popup.js`
- Modify: `static/item_review_popup.css`

- [ ] **Step 1: Add failing component orchestration tests**

Create a minimal fake panel and assert that `_suggest` makes one batch request, applies returned fields and a valid candidate index, then makes exactly one repair call only for rows whose required name is still blank:

```javascript
const calls = [];
const review = new Review({
  targetLanguage: "en", rows: twoMissingRows,
  suggest: async rows => {
    calls.push(rows);
    return calls.length === 1
      ? { ok: true, suggestions: [{ index: 0, name_en: "Safety Vest" },
                                   { index: 1, candidate_index: 1 }] }
      : { ok: true, suggestions: [{ index: 0, name_en: "White Car" }] };
  }
});
await review._suggest(fakeRoot, fakeButton);
assert(calls.length === 2, "one press may make one bounded repair call");
assert(calls[1].length === 1, "repair contains only the unresolved row");
assert(review.selected.get(1) === 1, "AI can recommend only a candidate from that row");
```

Also assert `applyValidation` clears prior state, adds `aria-invalid="true"`, opens the hidden Details row for `choice`, scrolls, and focuses the first invalid control.

- [ ] **Step 2: Run the component test and confirm failure**

Run: `node tests/item_review_popup.test.js`

Expected: FAIL because bounded repair, candidate application, and structured error rendering do not exist.

- [ ] **Step 3: Implement bounded completion and field errors**

Refactor `_suggest` into small methods with these concrete contracts:

```javascript
_pendingRequests(root, requiredOnly) {
    const target = this.options.targetLanguage;
    const requests = [];
    this.rows.forEach((row, rowIndex) => {
        const name = root.querySelector(`[data-irp-field="${rowIndex}:name_${target}"]`);
        const description = root.querySelector(`[data-irp-field="${rowIndex}:description_${target}"]`);
        const needsName = !!name && !present(name.value);
        const needsDescription = !requiredOnly && !!description && !present(description.value)
            && present(row.sourceDescription);
        const needsChoice = !requiredOnly && row.ambiguous && !this.selected.has(rowIndex);
        if (!needsName && !needsDescription && !needsChoice) return;
        requests.push({
            rowIndex,
            name: row.sourceName,
            description: row.sourceDescription,
            unit: row.sourceUnit,
            candidates: row.candidates.map(candidate => ({
                name: candidate.name || "",
                description: candidate.description || "",
                unit: candidate.unit || "",
            })),
        });
    });
    return requests;
}

_applySuggestions(root, requests, suggestions) {
    let completed = 0;
    (suggestions || []).forEach(suggestion => {
        const request = requests[Number(suggestion.index)];
        if (!request) return;
        const ci = suggestion.candidate_index;
        if (Number.isInteger(ci) && this.rows[request.rowIndex].candidates[ci]) {
            this._selectCandidate(root, request.rowIndex, ci, true);
            completed++;
        }
        for (const kind of ["name", "description", "unit"]) {
            const key = `${kind}_${this.options.targetLanguage}`;
            const field = root.querySelector(`[data-irp-field="${request.rowIndex}:${key}"]`);
            if (!field || present(field.value) || !present(suggestion[key])) continue;
            field.value = suggestion[key];
            field.classList.add("is-ai-suggested");
            completed++;
        }
    });
    return completed;
}

_clearValidation(root) {
    root.querySelectorAll('[aria-invalid="true"]').forEach(el => el.removeAttribute("aria-invalid"));
    root.querySelectorAll(".has-errors").forEach(el => el.classList.remove("has-errors"));
    root.querySelectorAll("[data-irp-error]").forEach(el => el.remove());
}

_applyValidation(root, validation) {
    this._clearValidation(root);
    const errors = Array.isArray(validation && validation.errors) ? validation.errors : [];
    let first = null;
    errors.forEach(error => {
        const rowIndex = this.rows.findIndex(row => String(row.id) === String(error.id));
        if (rowIndex < 0) return;
        const row = root.querySelector(`[data-irp-row="${rowIndex}"]`);
        if (row) row.classList.add("has-errors");
        (error.fields || []).forEach(kind => {
            let field = root.querySelector(`[data-irp-field="${rowIndex}:${kind}"]`)
                || root.querySelector(`[data-irp-field="${rowIndex}:${kind}_${this.options.targetLanguage}"]`);
            if (kind === "choice") {
                const extra = root.querySelector(`[data-irp-extra="${rowIndex}"]`);
                const more = root.querySelector(`[data-irp-more="${rowIndex}"]`);
                if (extra) extra.hidden = false;
                if (more) more.setAttribute("aria-expanded", "true");
                field = root.querySelector(`[data-irp-extra="${rowIndex}"] .irp-candidates`);
            }
            if (!field) return;
            field.setAttribute("aria-invalid", "true");
            field.classList.add("has-errors");
            if (!first) first = field.matches("input,button,select") ? field : field.querySelector("button,input");
        });
    });
    if (first) {
        first.scrollIntoView({ block: "center", behavior: "smooth" });
        first.focus({ preventScroll: true });
    }
}
```

The first request includes missing names, useful descriptions, and ambiguous choices. After applying it, make one second request only for still-blank required target names. Never repair optional descriptions. Use the label `Complete Missing Details with AI`, progress labels `Completing...` and `Finishing...`, and restore the original label in `finally`.

When a valid `candidate_index` arrives, call one `_selectCandidate` method shared by click and AI paths. Ignore non-integer and out-of-range values. Accept both legacy string validation and the new `{message, errors}` result while callers migrate.

Add compact accessible CSS:

```css
.irp-row.has-errors > td { background: #fff8f6; }
.irp-input[aria-invalid="true"],
.irp-money input[aria-invalid="true"],
.irp-candidates[aria-invalid="true"] {
    border-color: #b54708;
    box-shadow: 0 0 0 2px rgba(181, 71, 8, .12);
}
.irp-field-error {
    display: block;
    margin-top: 4px;
    color: #9a3412;
    font-size: 12px;
    font-weight: 600;
}
```

- [ ] **Step 4: Run the component test**

Run: `node tests/item_review_popup.test.js`

Expected: PASS with a maximum of two provider calls per press.

### Task 4: Add safe candidate context to the Gemini adapter

**Status:** Complete

**Files:**
- Modify: `tests/translate_suggestions.test.py`
- Modify: `backend/translate.py`

- [ ] **Step 1: Write failing prompt and parser tests**

Add tests that pass candidate display data plus forbidden commercial values, then assert only name, description, and unit appear. Add parser tests:

```python
def test_candidate_index_is_integer_and_in_bounds(self):
    out = translate._parse(
        '{"items":[{"index":0,"name_en":"Split AC","candidate_index":1}]}',
        [{"candidates": [{"name": "1.5 Ton"}, {"name": "2 Ton"}]}])
    self.assertEqual(out[0]["candidate_index"], 1)

def test_missing_rows_are_restored_for_bounded_repair(self):
    out = translate._parse('{"items":[]}', [{"name": "x"}, {"name": "y"}])
    self.assertEqual([row["index"] for row in out], [0, 1])
    self.assertTrue(all(row["name_en"] == "" for row in out))
```

- [ ] **Step 2: Run the translator test and confirm failure**

Run: `python tests/translate_suggestions.test.py`

Expected: FAIL because `_parse` does not accept per-row candidate bounds and the prompt omits candidates.

- [ ] **Step 3: Implement safe serialization and normalized parsing**

Change `_parse(text, items_or_count)` to accept the item list while retaining integer-count compatibility. Build a unique output map by requested index, pre-seeded with blank name, description, unit, and `candidate_index: None`. Parse `candidate_index` only when `type(value) is int` and it is within `len(items[index]["candidates"])`.

In `_build_prompt`, serialize each candidate as zero-based position plus printable English name, description, and unit. Do not serialize dictionaries wholesale. Update the prompt to require a best concise name for every row and allow `candidate_index: null`. Keep the top-level `suggest` return contract non-throwing.

- [ ] **Step 4: Run the translator test**

Run: `python tests/translate_suggestions.test.py`

Expected: PASS, including privacy, model fallback, and batch-cap tests.

### Task 5: Update the map and verify the complete flow

**Status:** Complete

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`
- Modify: `AGENT-BOARD.md`

- [ ] **Step 1: Update owning documentation**

Document the composite handoff identity, local unit-first rule, bounded two-call AI completion, candidate privacy boundary, structured validation contract, and rate exclusion from AI.

- [ ] **Step 2: Run focused static tests**

Run:

```text
node tests/item_review_popup.test.js
node tests/po_handoff_review.test.js
python tests/translate_suggestions.test.py
```

Expected: all pass.

- [ ] **Step 3: Take the repository test lock and run smoke tests**

Mark the `Test lock` row in `AGENT-BOARD.md` as held by Codex, then run:

```text
python smoke_test.py
python smoke_quote.py
```

Expected: both exit successfully. Restore the test-lock row to `nobody` immediately after the runs, including on failure.

- [ ] **Step 4: Perform read-only live acceptance checks**

Use the resolver without saving:

- A-83 yields local `EA` for both `عدد` rows and names invoice rate blockers explicitly.
- A-89 yields two independent `سبلت جداري` decisions, one for `طن ونص` and one for `2 طن`.
- No diagnostic writes change `data/shared/library.db`.

- [ ] **Step 5: Check propagation and finalize the board**

Search all `ItemReviewPopup`, `tr_suggest`, name-only handoff grouping, and generic highlighted-details messages. Confirm no second caller still depends on the old validation or AI contract. Move the Codex board row from Now to Done with exact tests and commit IDs.

- [ ] **Step 6: Commit only explicit task paths**

Run `git status --short`, inspect the exact diff, and stage only the plan-owned paths. Never stage the whole directory or unrelated Claude changes.
