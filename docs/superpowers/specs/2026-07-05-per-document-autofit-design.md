# Per-Document Auto-Fit Rows Design

## Goal
Make Auto-fit row height visible in the active editor toolbar and save it per document for PO, Quotation, Delivery Note, and Invoice. Official Letters are excluded.

## Behavior
- Each editor shows an "Auto-fit rows" toggle in its top action group near the type/settings gear.
- The toggle affects preview, print, save, and export for the currently open document.
- New documents start from the existing document-type default.
- Saved documents store an explicit `autofit_rows` value with that document, including `false`.
- Reopening a saved document restores its own value, so one quotation can have Auto-fit on while another quotation has it off.

## Storage
Use the existing generic `doc_meta` concept with a new `layout` key for document-level layout metadata. Store only lightweight layout values for now:

```json
{ "autofit_rows": true }
```

The value is keyed by document type and document number. Use `po`, `qut`, `dn`, and `inv`; do not add LTR support.

## Rendering
Generators should prefer the document payload layout when it explicitly contains `autofit_rows`. If the payload has no explicit value, fall back to the existing type/default settings.

## Testing
Add backend regression coverage that proves saved document layout metadata round-trips through API load for PO, QUT, DN, and INV, including explicit `false`.

