# Smart Document Link Suggestions

**Date:** 2026-07-17  
**Status:** Approved design

## Goal

Make PO fulfillment linking faster and safer by ranking likely document matches
before the complete document list, and by allowing an invoice to reach a PO
through a Delivery Note that is already linked to that PO.

The feature applies within the active company profile only and preserves the
existing item-level `po_fulfillment` model.

## Supported Flows

Suggestions appear in all existing fulfillment-link directions:

- Delivery Note to Purchase Order
- Invoice to Purchase Order
- Purchase Order to Delivery Note
- Purchase Order to Invoice

The invoice-to-PO picker additionally provides a `Linked delivery notes` tab.
This tab is not shown for DN-to-PO linking or for the PO-side source pickers.

## Matching Model

Matching is computed in Python from the documents' stored `rows_json`; the
frontend does not issue one bridge call per candidate. No database schema change
or persisted search index is required.

For each source/candidate pair:

1. Count non-empty item rows using the same primary-name fallback used by the
   fulfillment detail UI (`item`, `equipment`, `role`, `name`, `description`).
2. A candidate is suggestion-eligible when its item-count difference is at most
   two.
3. Item count is the strongest signal: exact, one apart, then two apart.
4. Quantity similarity improves the score. Compare normalized, positive row
   quantities as a sorted multiset so row order does not matter; reward exact
   quantity patterns first, then close total quantities.
5. Date proximity is a small tie-breaker only. It must never outrank a better
   item-count or quantity match.
6. Stable final ordering is score descending, date proximity ascending, then
   document number.

Return at most five suggestions. Each result includes structured reasons for the
UI, such as `20 items`, `Quantities match`, or `2 days apart`, plus its normal
candidate summary. Existing links are returned as linked state, not presented as
new suggestions.

Candidates outside the suggestion threshold remain available in the complete,
searchable list.

## Picker Layout

The existing modal shell remains. A compact `Suggested` section appears above
the normal document list only when matches exist. Suggested rows use the same
document-row component as the full list, with a restrained violet accent and
small reason labels. The full list remains immediately below it under an `All
documents` heading.

Search filters both sections. A suggestion that also appears in the full list is
not duplicated while the unfiltered suggestion section is visible; after a
search, the filtered complete list remains authoritative.

Linked rows retain the existing `linked` label. Empty suggestion results do not
show an empty panel; the picker falls back cleanly to the current list.

## Invoice Through A Linked DN

When linking an invoice to a PO, the picker has two tabs:

- `Purchase orders`: the existing PO list with smart suggestions.
- `Linked delivery notes`: only DNs that currently have at least one
  `po_fulfillment` row with `source_kind='dn'`.

The DN tab ranks likely DNs against the invoice using the same item-count and
quantity score. It still exposes all linked DNs below the suggestions.

Selecting a DN opens a confirmation view containing:

- DN number, date, and item rows.
- Every PO currently linked through that DN.
- The PO rows associated with the DN fulfillment marks.
- The invoice-to-PO rows proposed for linking.

If the DN links to multiple POs, each PO is a separate selectable group. The
user must confirm before any invoice fulfillment marks are written. Existing
invoice marks are shown and excluded from new writes. Closing, going back, or
switching tabs writes nothing.

Confirmation writes the existing invoice fulfillment records through
`set_po_fulfillment(po_no, indexes, 'inv', invoice_no, true)`. It then follows
the existing invoice paid/link behavior; this feature does not redefine paid,
submitted, delivered, or deletion semantics.

## Backend Boundary

Add focused database/API helpers rather than expanding candidate payloads for
every caller:

- A profile-scoped suggestion function for a source document and target kind.
- A linked-DN candidate function for invoice-to-PO linking.
- A linked-DN detail function returning its linked PO numbers and item indexes.

The bridge returns JSON-safe data only. Invalid kinds, missing documents, broken
row JSON, and missing dates degrade to empty suggestions or reduced scoring, not
exceptions in the picker.

## Safety And Compatibility

- All reads and writes use the active profile database.
- No data is shared between Abraj and Eshraqat by this feature.
- Existing manual linking remains available and unchanged.
- Existing item-level fulfillment marks and delete cleanup remain authoritative.
- Suggestions never auto-link documents.
- A DN with no linked PO never appears in the Linked delivery notes tab.
- A DN linked to multiple POs always requires explicit PO-group confirmation.
- Image/base64 cells are ignored by item and quantity matching.

## Verification

Backend regression coverage must verify:

- exact item-count matches outrank one- and two-item differences;
- quantity-pattern matches break equal-count ties;
- candidates more than two items apart are absent from Suggestions but remain in
  the complete list;
- already-linked candidates are not suggested as new links;
- only DNs with live PO fulfillment links appear in the DN tab;
- one DN can return multiple linked POs and the exact item indexes;
- profile isolation;
- malformed rows and missing quantities do not fail the picker.

Frontend verification must cover:

- Suggestions render before All documents in all four directions;
- search, Back, close, and existing linked labels still work;
- the invoice-side picker alone receives the two tabs;
- DN selection shows DN items, linked PO groups, and proposed invoice rows;
- no fulfillment write occurs before confirmation;
- successful confirmation refreshes the open panel and PO fulfillment state;
- keyboard focus, narrow-window layout, dark mode, and reduced motion remain
  usable.

Run the focused tests, JavaScript syntax checks, `python smoke_test.py`, and the
relevant profile-isolation suites before completion.
