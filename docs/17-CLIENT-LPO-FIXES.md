# Client LPO fixes and workspace suggestion

Implemented on 2026-10-02 after [[16-CLIENT-LPO-AUDIT]]. The audit remains a
record of the original failures; this note describes the resulting behavior.

## Application behavior

- Header edits preserve every recorded order line. List/get responses expose
  decoded `rows` beside `rows_json`. Omitting rows preserves them; an explicit
  empty list still clears them intentionally.
- Delivery notes start with undelivered quantities. Materials invoices start
  with delivered minus invoiced quantities once delivery has begun; direct
  invoicing without a delivery uses ordered minus invoiced quantities. Fully
  fulfilled items never fall back to the whole order. Draft DNs are excluded.
- Rental/supply invoice templates use blank quantities for the actual period.
  Rental invoices open the equipment editor. Selected service dates travel
  into the description/specification; the user enters actual quantities.
- Internal quotations must belong to the same canonical client before saving
  their link, importing rows/costs or marking the quotation won. Legitimate
  external references remain supported. An unavailable quotation store does
  not lose the LPO save, but importing and marking a win wait for verification.
- Client-scoped deep links resolve aliases through the backend. A missing
  client match never falls back to another client's identical number.
- USD canonical totals carry dollar labels, including IQD orders. Native rates
  convert into the destination document's currency, including comma-formatted
  prices. The original recorded LPO value keeps its native currency.
- Detail has Create delivery note/Create invoice and clickable linked documents.
  Financial completion does not block an outstanding delivery; manual closure
  blocks new documents.
- Signature status no longer invents a one-day deadline. Search includes
  quotation, ERP and project references. Progress stays visible at 1024 px;
  dark selectors match the real theme attribute; form controls have names.
- Every LPO form dismissal uses the shared async Popup close guard. Dirty work
  requires discard or continued editing. Repeated saves and dismissals do not
  launch duplicate operations.

## One inspectable billing ledger

`backend/lpo_billing.py` feeds the list, dashboard and detail. Rentals use
anniversary periods and retain a partial last month. Supply contracts use
calendar months. Confirmed tracking dates take precedence over service ranges
on invoice rows; an invoice-date estimate is clearly marked for review.
Overlapping ranges combine without hiding gaps. Partial coverage is visible.
Coverage describes service dates, not payment or all ordered quantities.

`lpo_invoice_periods` stores confirmed dates scoped to invoice, client and LPO.
Editing tracking dates changes no historical invoice content or PDF. Moving an
invoice to another LPO invalidates the old confirmation. A database trigger
cleans confirmations on every invoice deletion path. Fresh schema and an
idempotent migration cover both new and existing profile databases.

Both company snapshots retained 9 LPOs with all their originals and lines.
Abraj reminders now identify 6 unbilled calendar periods on CSSP-OPEN-0001
and partial coverage on the current CSSP-RENT-0004 period. The four
CSSP-RENT-0002 invoices without service ranges remain visibly estimated.
The 3-of-2 and 4-of-3 rental displays now read 3-of-3 and 4-of-4.

## References requiring paperwork

The list's unmatched-reference review is read-only and opens each document.
There are 14 Abraj invoice references and 1 Eshraqat invoice reference without
a matching same-client LPO. Some appear to be job references, but that is not
evidence of the correct replacement. No historical invoice or original/signed PDF was rewritten. No live business
record or retained QA/showcase file was removed.

## Counted by month (2026-10-06)

The service-period ledger described in "One inspectable billing ledger" below
was replaced: Waleed asked for LPOs to be counted by how many times they were
invoiced each month, so a forgotten month shows. `backend/lpo_billing.py`
`months()`; an LPO is done when every month is invoiced and every invoice is
sent. Confirm-dates and the `save_lpo_invoice_period` bridge are gone.
[[03-BACKEND-DATA]].

## Simplified screens (2026-10-03)

Waleed asked for something simpler: tabs like the Quotation and Invoice lists, no
Previous / Next LPO, and only what is useful. The list now has type tabs with
per-type columns and a next-step button per row, decided by one backend rule
(`database.lpo_next_step`); the LPO screen leads with that step, keeps the rare
actions in More, and shows one billing list. Spec and plan:
`docs/superpowers/specs/2026-10-03-client-lpo-simplify-design.md`,
`docs/superpowers/plans/2026-10-03-client-lpo-simplify.md`. Details in
[[07-FRONTEND-SUPPORT]] and [[03-BACKEND-DATA]]. The workspace proposal below
was not built as such; its useful parts (create actions beside the identity,
clickable documents) are in the new screen.

## Suggested organization

[Interactive workspace proposal](designs/client-lpo-workspace.html): one
searchable list with attention filters, then a workspace for each LPO with
Overview, Items, Billing and Documents. Create actions stay beside the order
identity. Documents opens the existing PDF/signing workflow. The proposed
navigation is a preview; this change does not replace the production screen
with the full redesign. Counts and list values use the checked snapshot;
example tab contents illustrate layout rather than claim additional live data.

## Verification

- Focused LPO suites: 26/26 passed, including the exact staged code without
  other sessions' uncommitted work. Full working-tree JavaScript: 83/83 passed.
- New regressions cover rows, quantities, currencies, identity, service dates,
  tracking scope/deletion and migration. Ten backend mutations are caught;
  original frontend and Popup controls fail the new assertions as expected.
- Actual LPO components exercised Add, notes/items save, all four discard
  routes, linked/create buttons, billing confirmation and reference review,
  light/dark, 1024 px and an actual two-page original PDF.
- Native WebView2 exercised the rental equipment-invoice handoff with blank
  quantity/service dates, and a materials delivery with actual seeded items.
- Both real-PDF smoke tests passed on copies and on the isolated staged tree:
  `Smoke test passed.` / `Smoke quote passed.` Shared Popup: 119 checks passed.
  Document deletion and cached-data checks passed on copies.
  Native clipboard/printing and making a newly signed PDF were
  not exercised; their existing regression suites passed.

Reusable checks: `tests/lpo_audit_fixes.test.py`, `lpo_audit_frontend.test.js`,
`lpo_audit_handoff.test.js`, `lpo_audit_controls.py`, `lpo_workflow.qa.cjs`.
Browser QA accepts a snapshot JSON and an output directory; it mocks writes.
Diagnostics/screenshots remain under
`%LOCALAPPDATA%/BusinessHub/Diagnostics/lpo-fixes-20261002/`.
