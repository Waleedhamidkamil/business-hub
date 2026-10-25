# Invoice Number Repair Design

## Problem

Abraj invoice numbering was advanced from 047 to 902 by the test-only tombstone
`INV-MT-SIGNEDCOPY-901`. Six real invoices were then saved as 902 through 907.
The six records are marked Submitted, five participate in PO fulfillment links,
and every PDF prints the incorrect number.

## Approved outcome

Repair the six invoices in creation order and remove their Submitted status:

| Old number | New number |
|---|---|
| `INV-MT-TDC-902` | `INV-MT-TDC-048` |
| `INV-MT-JUNE CONSTRUCTION CO.-903` | `INV-MT-JUNE CONSTRUCTION CO.-049` |
| `INV-MT-TDC-904` | `INV-MT-TDC-050` |
| `INV-MT-TDC-905` | `INV-MT-TDC-051` |
| `INV-MT-TDC-906` | `INV-MT-TDC-052` |
| `INV-MT-TDC-907` | `INV-MT-TDC-053` |

After repair, the next Abraj invoice must be 054. The repair must preserve each
invoice's business data, created timestamp, cost data, layout, signing overlay,
quotation link, and PO fulfillment links. It must remove Submitted status so the
corrected documents can be sent again.

## Considered approaches

1. **Transactional re-key plus PDF regeneration, selected.** Back up both
   profile databases and every affected PDF, stage regenerated PDFs, update all
   keys and paths in one controlled publish step, and verify the visible number
   inside every PDF. This is the only approach that repairs the complete record.
2. **Reset only the next number.** This would make the next invoice 048 but leave
   six issued records and their links under 902 through 907. Rejected because it
   creates duplicate chronology and leaves the user-visible defect in place.
3. **Keep 902 through 907 and continue.** This preserves already sent numbers but
   contradicts the user's instruction to fix and resend them. Rejected.

## Components

### Repair utility

`scripts/repair_invoice_number_jump_20260908.py` owns the one-time audited
migration. Its default mode creates a restore point and rehearses the database
changes without publishing them. `--apply` repeats all precondition checks,
locks both profile databases, publishes the re-key, installs the staged PDFs,
removes the six Submitted rows, and writes a JSON report.

The restore point is kept under `data/document-backups/` and contains SQLite
backups, the six original PDFs, regenerated PDFs, and the report. A failed
publish rolls the databases back and restores the original PDF set.

### Database propagation

The repair updates the invoice primary key and every decoupled reference that
can carry an invoice number:

- `invoices.inv_no` and `invoices.pdf_path`
- `paid_invoices.inv_no`
- `doc_cost_meta`, `doc_columns`, `attention_ignores`, and `submitted_docs`
- `doc_meta`
- `signed_copies`, including its stored file path when present
- `po_fulfillment.source_no` for `source_kind='inv'`

Submitted rows are deleted instead of re-keyed. The fake signed-copy tombstones
are removed from Abraj and Eshraqat, while legitimate deleted-document records
remain untouched.

### PDF regeneration

Each invoice is reconstructed from its saved database record, per-document
columns, layout, and signing overlay, then rendered with the corrected number.
Before publishing, every staged PDF must be readable, contain the new number,
exclude the old number, and have at least one page.

### Prevention

The invoice and delivery-note signed-copy tests must run against isolated
profile databases and must remove their own `deleted_docs` rows during cleanup.
A regression check proves the fake invoice deletion cannot leave numbering
residue behind.

## Safety and verification

The utility refuses to apply if any expected old record or PDF is missing, any
new number already exists, the live databases change after rehearsal, the
affected-reference counts differ from the audited baseline, or a staged PDF
does not contain the corrected number. After publication it checks SQLite
integrity, exact reference propagation, zero Submitted rows for 048 through
053, absence of 902 through 907 from active files and records, and next invoice
054.

