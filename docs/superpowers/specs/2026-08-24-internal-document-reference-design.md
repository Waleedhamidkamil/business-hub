# Internal Document Reference and Compact Right-Panel Cards

**Date:** 2026-08-24
**Status:** Approved for implementation; user explicitly waived written-spec review
**Applies to:** Purchase Orders, Quotations, Delivery Notes, and Invoices

## Goal

Give every supported document an internal `Reference` text field that helps the
user remember what the document is for. The value is saved with the editable
document, restored when reopened, visible and searchable in the application,
and never shown in previews, printed output, or generated PDFs.

At the same time, make the PO, quotation, and delivery-note cards use the
invoice card's compact hierarchy so the new Reference does not make the already
crowded right panel harder to scan.

## User experience

### Editor field

- Add one single-line input labelled exactly `Reference` to the existing
  document-information area of each PO, quotation, DN, and invoice editor.
- Match the design and size of the surrounding document-information controls.
- Accept ordinary free text without making the field required.
- A new document starts with an empty Reference.
- Reopening a saved document restores the complete Reference.
- Duplicating a saved document clears the Reference; it must not follow the
  source document into the new copy.
- Reference edits participate in each editor's existing dirty/unsaved state.

### Right-panel cards

Use this hierarchy for PO, quotation, DN, and invoice cards:

1. Document number on the left and the date already used by the current card
   behavior on the right. The redesign does not change date semantics.
2. When non-empty, one compact Reference line below the header. Long text is
   truncated with an ellipsis; the complete value is available through the
   native hover tooltip.
3. Existing status pills below the header/Reference.
4. Existing action buttons below the content.

Additional card rules:

- An empty Reference adds no row or vertical space.
- Remove the PDF filename/legacy metadata line from the PO card.
- Do not show document Type pills on quotation or invoice cards. Existing type
  filtering outside the cards remains unchanged.
- Keep the invoice amount beside its existing date because it is useful and is
  already part of the invoice card design.
- Do not remove or change open, folder, copy, print, duplicate, delete,
  submit, paid, signed, linked, or other existing actions/statuses.
- View-only filesystem documents that have no database record simply show no
  Reference.

## Storage and migration

Use a dedicated `reference` column on each existing document table:

- `pos.reference`
- `quotes.reference`
- `dns.reference`
- `invoices.reference`

The normal idempotent database initialization/migration adds missing columns
with an empty default. Existing profiles and existing documents therefore open
unchanged and receive an empty Reference until the user enters one.

Save/update functions accept the Reference explicitly, normalize missing values
to an empty string, and persist it in the same transaction as the document.
Load functions return the full value. Monthly list functions include it so the
right panel does not need one load call per card.

## Data flow and PDF exclusion

Each editor reads its `Reference` input while building a save/update payload.
The bridge passes that value only to the database persistence call. Loading a
document writes the returned value back into the input; New and Duplicate clear
the input.

Preview, print, and PDF generator/template contracts remain unchanged. No PDF
template receives or renders `reference`. This explicit separation makes the
field application-only even if a frontend payload happens to carry it alongside
other document data.

## Search

- Database-backed dashboard search includes the complete Reference for all four
  document tables, case-insensitively.
- A Reference hit returns the document through the existing unified search
  result shape. The result snippet identifies the matched Reference text so the
  user understands why the document matched.
- The right-panel's existing local card filter finds Reference automatically
  because the escaped text is present in the card DOM, including text hidden by
  visual ellipsis.
- Search evaluates the stored full value, never the shortened visual label.

## Safety and edge cases

- Escape Reference text before inserting it into card HTML or tooltip
  attributes.
- Empty, missing, and legacy values are equivalent to `""`.
- Long values remain intact in storage; only the card presentation truncates.
- Saving an existing document without changing its Reference preserves it.
- PDF-only/view-only documents remain functional without database migration
  data.
- Document deletion follows existing row deletion, so no separate Reference
  cleanup is required.

## Implementation boundaries

- Database schema, save/load/list/search: `backend/database.py`; idempotent
  existing-profile column migration: `backend/migrations.py`.
- Bridge payload threading only: `backend/api.py`.
- Editor inputs and card containers: `templates/index.html` and shared editor
  styles in `static/app_styles.css`.
- PO behavior/card: `static/app.js`.
- Quotation behavior/card: `static/quote.js`.
- DN behavior/card: `static/dn.js`.
- Invoice behavior/card: `static/inv.js`.
- The right-panel card markup should use a small shared formatter/helper where
  that reduces drift, while each editor keeps ownership of its action wiring.
- PDF generators and PDF templates are intentionally out of scope except for
  regression assertions proving the field is absent.

## Test strategy

Follow test-driven development:

1. Add an isolated database regression that first fails because the columns and
   round-trip behavior are missing. Cover all four document types, monthly list
   exposure, legacy empty defaults, full-value search, and Reference snippets.
2. Add focused frontend regressions that first fail because the editor inputs,
   payload/load/new/duplicate behavior, and compact card structure are missing.
3. Cover a long Reference, HTML-special characters, empty References, removal
   of the PO filename metadata, removal of Type pills, preserved status/action
   controls, and unchanged invoice amount/date presentation.
4. Assert the printable templates and generator contexts never render or label
   the internal Reference.
5. Run the focused PO/QUT/DN/INV frontend and backend suites, then the required
   smoke test while holding the shared test lock.

## Acceptance criteria

- All four editors show the same `Reference` field in their information area.
- Save/reopen round-trips the complete value in every document type.
- New and Duplicate always start with an empty Reference.
- Dashboard and right-panel searches find documents by the full Reference.
- Right-panel cards follow the approved compact hierarchy without Type pills or
  the PO PDF filename line.
- Long Reference text never expands the card beyond one Reference line and the
  complete value remains accessible on hover.
- No generated PDF contains the Reference label or value.
- Existing documents, view-only files, statuses, actions, filters, and invoice
  amount/date behavior continue working.
