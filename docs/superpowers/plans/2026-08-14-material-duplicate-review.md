# Material Duplicate Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a preview-first Master Data action that safely finds and merges duplicate material-catalog rows without changing saved documents.

**Architecture:** The backend owns discovery, safety classification, validation, backup, and source-local merging for Quotation Materials, Delivery Note, and Invoice Materials. The Master Data screen receives a read-only review plan, lets the user opt into each merge and choose a canonical row when details conflict, then submits only stable group/canonical IDs. Existing catalog plumbing performs the write so autocomplete tables and usage counters remain consistent.

**Tech Stack:** Python 3, SQLite, pywebview API bridge, vanilla JavaScript, CSS, unittest, Node.js static/UI tests.

---

## File structure

- `backend/master_data.py` - material-only duplicate discovery, stable row/group IDs, backup-before-write orchestration, and selection validation.
- `backend/data_manager.py` - retain every JSON key supplied by a catalog merge, including canonical custom data.
- `backend/api.py` - bridge methods `im_material_duplicate_review()` and `im_apply_material_duplicate_review(groups)`.
- `templates/index.html` - compact Master Data toolbar action `#imMaterialDuplicatesBtn`.
- `static/item_master.js` - review modal, selected-group state, canonical radios, confirmation, apply, and list refresh.
- `static/app_styles.css` - scrollable, keyboard-visible duplicate-review cards for both themes.
- `tests/master_data_duplicate_review.test.py` - sentinel-only backend behavior tests against the shared library.
- `tests/master_data_duplicate_review_ui.test.js` - deterministic frontend helper/markup contract tests.
- `docs/02-BACKEND-CORE.md`, `docs/03-BACKEND-DATA.md`, `docs/05-FRONTEND-SHELL.md`, `docs/07-FRONTEND-SUPPORT.md`, `docs/08-STYLES-TEMPLATES.md`, `docs/11-RUN-AND-MAINTAIN.md` - map updates for every changed owner.

### Task 1: Pin safe duplicate classification

**Files:**
- Create: `tests/master_data_duplicate_review.test.py`
- Modify: `backend/master_data.py`

- [ ] **Step 1: Write a failing material-plan test with sentinel catalog rows.**

```python
plan = master_data.material_duplicate_review()
ready = next(g for g in plan["groups"] if g["compact_name"] == "zzreviewfrozenchickenleg")
self.assertEqual(ready["category"], "ready")
self.assertTrue(ready["recommended_canonical_id"])
self.assertEqual(ready["source"], "dn")
self.assertFalse(ready["conflicts"])
```

The fixture inserts only `ZZ-REVIEW-*` rows into `dn_items`, `quote_items` and `inv_items`; its cleanup deletes `WHERE name LIKE 'ZZ-REVIEW-%'` from those three tables in both `tearDown` and `tearDownModule`.

- [ ] **Step 2: Run the focused test and verify the expected missing-function failure.**

Run: `python tests/master_data_duplicate_review.test.py`

Expected: a failing `AttributeError` for `material_duplicate_review`, while no non-sentinel row is written or deleted.

- [ ] **Step 3: Implement material-only grouping and classification.**

```python
MATERIAL_SOURCES = frozenset(("qut:materials", "dn", "inv:materials"))

def _material_name_key(name: Any) -> str:
    return _norm(_s(name)).replace(" ", "")

def material_duplicate_review() -> dict[str, Any]:
    # group `_variants(conn)` by (source, _material_name_key(name)); emit only
    # groups with at least two rows; return {"ok": True, "groups": groups,
    # "summary": summary}; never write through this function.
    return {"ok": True, "groups": groups, "summary": summary}
```

Classify a group as `ready` only when every nonblank value in description, unit, price, cost, currency, image refs, and custom data agrees. Classify any two distinct nonblank values as `review`; do not emit lookalikes whose compact name differs. Recommend the row with the most complete details, then the highest use count, then the most recent use.

- [ ] **Step 4: Re-run the focused test and verify it passes.**

Run: `python tests/master_data_duplicate_review.test.py`

Expected: `OK`; the plan detects newline/case/missing-space duplicates and has not mutated the catalog.

### Task 2: Preserve canonical metadata and merge selected rows safely

**Files:**
- Modify: `backend/data_manager.py`
- Modify: `backend/master_data.py`
- Modify: `tests/master_data_duplicate_review.test.py`

- [ ] **Step 1: Write failing apply tests for metadata, material number, usage, and conflicts.**

```python
done = master_data.apply_material_duplicate_review([{
    "group_id": ready["id"],
    "canonical_id": ready["recommended_canonical_id"],
}])
self.assertTrue(done["ok"], done)
self.assertTrue(Path(done["backup_path"]).is_file())
self.assertEqual(_sentinel_rows("dn"), ["ZZ-REVIEW Frozen Chicken Leg"])
self.assertEqual(_sentinel_data("dn")["item_no"], "M-ZZ-COMPLETE")
self.assertEqual(_sentinel_use_count("dn"), 7)
```

Add assertions that price/unit/currency/image/custom-data disagreement produces `category == "review"`, is not selected by the UI contract, and cannot be merged unless the caller selects both the group and one current row ID.

- [ ] **Step 2: Run the test and verify it fails because apply is missing.**

Run: `python tests/master_data_duplicate_review.test.py`

Expected: `AttributeError` for `apply_material_duplicate_review`; no backup and no mutation exist yet.

- [ ] **Step 3: Preserve all canonical JSON keys in catalog writes.**

```python
def _row_data_for_sig(c: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    out = {v["key"]: values[v["key"]] for v in c["variant_cols"] if v["key"] in values}
    for key, value in (values or {}).items():
        if key not in out and value is not None:
            out[key] = value
    return out
```

This keeps `image_refs`, currency fields, `item_no`, and arbitrary custom values in the selected canonical JSON. `data_manager._data_sig` continues to ignore only `database._SIG_IGNORE` fields.

- [ ] **Step 4: Implement guarded backup and application.**

```python
def apply_material_duplicate_review(groups: Any) -> dict[str, Any]:
    fresh = material_duplicate_review()
    approved, skipped = _validate_material_review_choices(fresh, groups)
    if not approved:
        return {"ok": False, "error": "Select at least one current duplicate group.", "skipped": skipped}
    backup_path = _backup_shared_library("pre-material-merge")
    for group, canonical in approved:
        data_manager.merge_catalog_entries(group["section"], canonical["values"], group["row_pks"])
    row_count = sum(len(group["rows"]) - 1 for group, canonical in approved)
    return {"ok": True, "groups": len(approved), "rows": row_count,
            "backup_path": str(backup_path), "skipped": skipped}
```

`_backup_shared_library` uses SQLite's `Connection.backup` before the first write. Validate all submitted IDs against the fresh plan; skip stale groups, reject an empty/invalid selection, and never accept raw SQL, raw table names, or arbitrary canonical field values from the browser.

- [ ] **Step 5: Re-run the backend suite and inspect the backup/write assertions.**

Run: `python tests/master_data_duplicate_review.test.py`

Expected: `OK`; each selected source has one canonical row with summed use count and unchanged canonical custom metadata; saved document tables are untouched.

### Task 3: Expose the review through the bridge and Master Data screen

**Files:**
- Modify: `backend/api.py`
- Modify: `templates/index.html`
- Modify: `static/item_master.js`
- Modify: `static/app_styles.css`
- Create: `tests/master_data_duplicate_review_ui.test.js`

- [ ] **Step 1: Write a failing UI contract test.**

```javascript
assert(/id="imMaterialDuplicatesBtn"/.test(html), "Master Data needs the duplicate-review button");
assert(/im_material_duplicate_review\(\)/.test(js), "button must request a read-only review plan");
assert(/im_apply_material_duplicate_review\(selected\)/.test(js), "apply must send only selected group IDs and canonical IDs");
assert(/type="radio"/.test(js), "each group must offer an explicit canonical choice");
assert(/confirmBox\(/.test(js), "apply must use the existing confirmation pattern");
```

- [ ] **Step 2: Run the UI contract test and verify it fails for the absent action.**

Run: `node tests/master_data_duplicate_review_ui.test.js`

Expected: failure naming `imMaterialDuplicatesBtn` or the missing bridge call.

- [ ] **Step 3: Add the bridge methods and toolbar button.**

```python
def im_material_duplicate_review(self) -> dict:
    return _bridge_json_safe(master_data.material_duplicate_review())

def im_apply_material_duplicate_review(self, groups: list) -> dict:
    return _bridge_json_safe(master_data.apply_material_duplicate_review(groups or []))
```

Add `<button type="button" id="imMaterialDuplicatesBtn" class="secondary">Review materials</button>` beside `#imHealthBtn`, then bind it in `init()`.

- [ ] **Step 4: Implement the modal interaction.**

```javascript
async function openMaterialDuplicateReview() {
  const plan = await api().im_material_duplicate_review();
  // Render checked ready groups, unchecked review groups, and radios for every candidate.
  // The Apply handler collects [{group_id, canonical_id}] from checked cards only.
  // Confirm, apply, toast the result, close, and await load().
}
```

Use `showModal`, `confirmBox`, `toast`, `esc`, and the existing `.im-*` conventions. Disable Apply when no group is selected; list conflict fields in text, not colour alone; make the card list scroll inside the existing modal shell.

- [ ] **Step 5: Add scoped CSS and re-run the UI test.**

Run: `node tests/master_data_duplicate_review_ui.test.js`

Expected: `material_duplicate_review_ui: OK`; the source asserts the empty state, ready defaults, conflict opt-in, canonical radios, confirmation, and refresh call.

### Task 4: Regression verification and map updates

**Files:**
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/07-FRONTEND-SUPPORT.md`
- Modify: `docs/08-STYLES-TEMPLATES.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`

- [ ] **Step 1: Update the map at each changed owner.**

Document the bridge names, material-only/snapshot safety model, SQLite backup, source-local canonical merge, Master Data button/modal, duplicate-review styles, and the two focused test commands.

- [ ] **Step 2: Run focused feature and existing Master Data tests.**

Run:

```powershell
python tests/master_data_duplicate_review.test.py
python tests/master_data_backend.test.py
node tests/master_data_duplicate_review_ui.test.js
node tests/master_data_ui.test.js
```

Expected: all exit `0`; live-database resource warnings are recorded separately from failures.

- [ ] **Step 3: Run the required data-layer smoke check.**

Run: `python smoke_test.py`

Expected: exit `0` and generated smoke artifacts only; do not run `smoke_quote.py` because it opens the PDF viewer.

- [ ] **Step 4: Inspect the exact diff and leave collaborative changes unstaged.**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors. Do not stage or commit broad shared files in this concurrent workspace; hand off the exact changed-file list and test evidence to the coordinating developer.
