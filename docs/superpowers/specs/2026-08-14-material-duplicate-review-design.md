# Material Duplicate Review - Design

## Goal

Add a simple **Review Material Duplicates** action to the Master Data screen.
It scans the shared Material catalog used by Quotation Materials, Delivery
Notes, and Invoice Materials, lets the user review duplicates, and merges only
the records they explicitly approve. Saved documents are never changed.

## Scope

- Sources: `qut:materials`, `dn`, and `inv:materials` only.
- The action lives beside Master Data's existing Health button.
- The screen is preview-first: scanning writes nothing.
- Applying creates a timestamped SQLite backup of the shared library before
  changing any catalog row.

## Candidate categories

| Category | Detection | Default action |
| --- | --- | --- |
| Ready to merge | Same item after normalising whitespace/case or removing an accidental missing word-space; description/unit/price/cost/custom data agree, or one record is only missing a value the selected record has. | Preselected, with a recommended canonical record. |
| Needs review | Same likely item text but two nonblank values disagree (price, cost, unit, description, currency, image, or custom data). | Unticked; user must select a canonical record and opt in. |
| Keep separate | Different meaningful specifications, e.g. BOX vs EA at different prices. | Not included in the result. |

`item_no` is deliberately not treated as a conflict: the chosen canonical
record keeps its material number and absorbed numbers retire. Usage counts are
summed. An image or custom-data mismatch is a review case, never an automatic
merge.

## Backend contract

`master_data.material_duplicate_review()` returns a read-only plan containing:

- summary counts;
- candidate groups and category;
- source, stored rows, selected/recommended canonical candidate;
- field-by-field differences and a human-readable reason;
- the exact row keys required to apply a group.

`master_data.apply_material_duplicate_review(groups)` validates the selected
group IDs and canonical row IDs against a fresh plan, backs up
`data/shared/library.db`, then merges only those groups. It returns merged
group/row counts, skipped stale groups, and the backup path. The backend owns
all detection and all merging; the browser never sends raw SQL or invents a
canonical payload.

Merges are source-local. A selected canonical preserves every one of its
stored fields, including picture refs and custom data; only records whose
metadata is compatible may be auto-selected. A chosen review merge receives
the user's explicit canonical selection, so no field value is guessed.

## UI flow

1. User clicks **Review Material Duplicates**.
2. A modal shows a short summary and three sections:
   - **Ready to merge** cards with a checked checkbox and recommended target;
   - **Needs review** cards with radio candidates, initially unticked;
   - a calm empty state when no action is needed.
3. Each card shows name, description, unit, price, cost, material number,
   source, use count, and a concise reason.
4. **Apply selected merges** states how many groups will change, requires the
   existing confirmation pattern, then refreshes Master Data on success.

The popup remains usable for long lists: its list scrolls while the title and
actions remain reachable. It uses words and values, not colour alone, to
describe safety and conflict states.

## Error handling

- A failed scan changes nothing and names the failure.
- A stale row between preview and apply is skipped and reported; other selected
  groups can still proceed.
- Backup failure aborts the entire apply.
- Any invalid group or canonical selection is rejected by the backend.

## Verification

Backend tests will pin:

- line-break/case/missing-space duplicates;
- blank-detail enrichment;
- price, cost, unit, currency, image, and custom-data conflicts;
- material-number retention and usage-count addition;
- source-local merging, backup-before-write, stale-plan refusal, and no saved
  document writes.

Frontend tests will pin the header button, empty/result states, default
selection rules, required radio choice for conflicts, confirmation payload,
and refresh after success.
