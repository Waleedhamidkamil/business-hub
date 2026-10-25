# 16 - Client LPO audit

Date: 2026-10-02. Scope: current working copy, both company profiles, correctness,
data quality, connections, and UI/UX. Audit only. No production fix or data repair
was applied. Existing temporary tests, showcase assets and backups were retained.

## Main assessment

Client LPOs has a useful base: received-document filing, quotation selection,
editable ordered lines, original/signed-copy handling, line fulfillment, contract
billing reminders, and a shared DN/invoice seed. Fix correctness before expanding
or polishing it. The most urgent issue can erase ordered lines during an ordinary
notes edit.

## Verified coverage

- **23/23 existing LPO suites passed**: 13 Python suites and 10 JavaScript suites.
  These cover schema/migrations, save/close, aliases, fulfillment, billing,
  attachments, signature coordinates, page images, quotation/cost transfer,
  pickers, form lifecycle and signed-copy selection. One backend test is skipped
  by its own environment guard; passing suites are not proof of every workflow.
- Drove the actual LPO template, styles, popup, lines editor and JavaScript in
  headless Chromium with captured snapshot responses. Opened the real two-page
  CSSP-ADM-0010 PDF read-only. Checked light/dark at 1366 x 900 and the list at
  1024 x 768. No JavaScript page errors or page-level horizontal overflow.
- Adding a new materials LPO submitted its header and one manually entered line
  correctly. Editing a saved four-line LPO submitted zero lines. The real Python
  save API accepted that payload and erased the lines **on the isolated copy**.
- A corrected source variant outside the repository loaded four lines instead;
  the defect assertion failed as expected. This confirms the reproduction reacts
  to the root cause. Production source was not changed.
- Original databases were read through SQLite backup connections in read-only
  mode. Application reads and test writes used separate profile/shared databases.
  Original PDFs were only read. Native Windows print, clipboard, external file
  opening, and a new signed PDF were not exercised end-to-end in this audit.
  PDF smoke tests were not repeated because no application code changed.

## Confirmed defects and gaps

| Priority | Finding and consequence | Cause / next change |
|---|---|---|
| P1 | **Editing can erase all ordered items.** CSSP-ADM-0010 has 4 stored lines. Edit details shows none; change notes and Save submits `rows: []`. The backend replaces the stored JSON with `[]`. All 9 current records have saved lines, so all are exposed to this path. | `static/lpo.js:449` reads `r.rows`; `database._lpo_row` and both list/get APIs return `rows_json`. Decode consistently, preserve lines on header edits, add a real list -> edit -> save regression. |
| P1 | **DN and invoice preparation use the same delivery balance.** For 100 ordered, 60 delivered and none invoiced, both receive quantity 40. That is the delivery remainder, not the billable delivered quantity. If all lines are delivered, the fallback offers the whole order even after it has been invoiced. | `Api.lpo_document_seed` uses `remaining = ordered - delivered`, has no target document argument, then falls back to the whole order. Define DN and invoice rules separately, including explicit repeat/reissue behavior. Existing seed tests assert this shared behavior and need stronger invoice scenarios. |
| P2 | **IQD progress and the Invoiced detail value mislabel USD figures.** A 1,400,000 IQD LPO with `total_usd=1000` and `invoiced_usd=500` reads `IQD 500 of IQD 1,000`. | `lpoProgress` and `renderLpoDocSide` put the native currency label on canonical USD fields. Use clearly labeled USD comparisons or convert with an explicit stable rate. All current LPOs are USD, so this is a reproduced supported-currency defect, not an observed live IQD discrepancy. |
| P2 | **Rental progress mixes different definitions of a month.** Live rows display `month 3 of 2` and `month 4 of 3`, while also warning that a month is missing. The 12 Aug to 11 Nov term is counted as 2 months. | `_term_months` subtracts a month before the anniversary despite its comment saying partial months count. Billing progress counts calendar months of invoice dates; the denominator counts term anniversaries. Agree the contract billing periods and store which period an invoice covers. Don't correct figures by clamping the bar. |
| P2 | **A client-qualified link can open another client's LPO.** With identical numbers for A/B and an unmatched explicit client C, `openLpoByNo` opens A. | The exact-name lookup falls back to number alone even when a client was supplied. Resolve canonical client identity and refuse an unmatched/ambiguous number. |
| P2 | **Typed quotation references are not checked against the LPO client.** The acceptance helper marks any existing, non-lost quotation number won. | `_win_quote_for_lpo` receives only the quotation number. Validate identity for an internal quotation before importing items/costs or marking it won. Keep legitimate external client references allowed. |
| P2 | **Connections require extra navigation.** Detail has 0 clickable linked documents and no Create DN/Create invoice buttons. | `renderLpoDocSide` renders references as spans. The creation hooks already exist in DN/invoice code and are reached from To-Do or editor pickers. Reuse those hooks on this screen after fixing their quantities. |
| P2 | **The signature deadline is invented globally.** Every unsigned record says the client requires a signed copy within one day. The opened CSSP-ADM-0010 PDF actually says within a week. | Hardcoded sentences in `lpoKpis` and the detail panel. Show unsigned status without a deadline unless the specific LPO has one recorded. |
| P3 | **Some state labels are hard to read in dark mode.** Delivered text remains near-black. Other styling is generally coherent. | LPO overrides in `static/dark.css` use `.dark`; the app activates dark mode with `html[data-theme="dark"]`. Align selectors and verify contrast. |
| P3 | **Smaller-window and accessibility gaps.** At 1024 px all row progress is hidden. Search/client selector and manual line fields lack explicit labels; the line remove button has no accessible name beyond its symbol/title. Unsaved LPO form edits have no discard warning. | Keep a compact progress reading at smaller widths; add accessible names and a dirty-form guard through shared Popup behavior. Expand search to quotation, project and ERP references rather than adding permanent empty filters. |

## Current data, verified on fresh copies

| Check | Abraj | Eshraqat |
|---|---:|---:|
| Filed LPOs | 8 | 1 |
| Materials / rental / supply contract | 3 / 4 / 1 | 1 / 0 / 0 |
| Original attachment available | 8 | 1 |
| Ordered lines recorded | 8 | 1 |
| Saved signed copy available | 1 | 1 |
| Cited internal quotation resolves | 5 of 5 cited | 1 of 1 cited |
| Months flagged unbilled by current rules | 10 across 5 contracts | 0 |
| Invoices naming an unfiled LPO reference | 14 | 1 |

The 10-month count is what the software reports, not a confirmed debt or missing
payment. Rental periods need reconciliation first. The 7 Abraj originals without
a saved signed copy may have been signed outside the app.

The 14 Abraj unmatched references include 12 TDC job descriptions plus
INV-MT-TDC-034 (`20260801 ship`) and INV-MT-JUNE-065 (`LPO-003`). The job-description
classification is an inference from their text and matching internal references;
verify the original documents before moving anything. Eshraqat IV-0005 names
`LPO CSSP-OPEN-0006`, which is not filed. Do not fuzzy-match these onto another
order or repair them automatically.

The existing read-only `no_orphan_lpo_numbers` check was run on each snapshot:
Abraj `FAILED (failures=2)`; Eshraqat `FAILED (failures=1)`. These are confirmed
data-quality failures, separate from the 23 passing fixture suites.

## Improvement order for discussion

1. **Protect correctness:** preserve items on edits, separate delivery/billing
   quantities, enforce client identity, fix currency labels and rental periods.
2. **Make this the order workspace:** clear Create DN/Create invoice actions;
   clickable quotation, DN and invoice references; client contact and defaults
   from Clients Master Data. Abraj currently has 5 linked quotations and 5
   contracts with billing reminders that would benefit from this navigation.
3. **Make decisions clearer:** ordered/delivered/invoiced quantities and amounts
   shown separately, billing-period history, unknown totals explicitly marked,
   and discrepancy warnings with a direct path to the affected document.
4. **Polish use and appearance:** compact progress at smaller widths, readable
   dark statuses, better search, accessible inputs, and protection for unsaved
   work. Keep the current three-kind grouping and document/record split.

External sharing currently uses the existing file, print, clipboard and folder
helpers. No new email, accounting or other external service integration was added.
Consider import/export only after the internal order flow is trustworthy.

## Evidence and reproduction

Diagnostics are retained outside the repository:
`%LOCALAPPDATA%/BusinessHub/Diagnostics/lpo-audit-20261002/`.
They include `test-results.json`, all suite logs, `data-results.json`,
`ui-results.json`, `partial-seed.json`, `edit-payload.json`, `edit-control.log`,
two orphan-check logs and screenshots of list/detail/form states.

Helpers are in the parent Diagnostics folder: `lpo_audit_tests.py`,
`lpo_audit_data.py`, `lpo_audit_ui.cjs`, `lpo_audit_backend_edges.py`, and
`lpo_audit_orphans.py`. Run the data helper first to refresh snapshots, then UI
and backend edge checks. The UI helper deliberately detects the current defects;
it will need updating when they are fixed. The data helper re-creates audit copies,
so do not use those copies as the only place to keep unrelated work.

UI review also used the current
[Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md),
alongside this app's [[14-UI-CONTRACTS]].


## Implementation after review

2026-10-02: the application defects above were fixed and verified. See
[[17-CLIENT-LPO-FIXES]] for resulting behavior, retained business-data questions
and the interactive workspace suggestion. This audit's original defect counts
describe the pre-fix state. Do not run its deliberately defect-detecting helpers
as passing regression checks; use the new checks listed in the implementation note.
