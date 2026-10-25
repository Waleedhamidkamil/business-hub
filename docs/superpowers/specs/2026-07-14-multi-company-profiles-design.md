# Multi-Company Profiles and Eshraqat Document Family Design

## Goal

Extend ABRAJ Documents Hub into one maintained desktop application that can operate for Abraj Albadr and Eshraqat Albadr without mixing business records or company identity.

The Eshraqat profile must support all five existing document types and all existing editor workflows. Its PDFs must use the visual design language of `C:\Users\Waleed\Documents\ESHRAQAT-QUOTATION-DRAFT-01.docx`, the exact supplied logo at `D:\ESHRAQATZ\11.PNGZ\logo with manpower.png`, and the app's existing pagination and anti-overlap rules.

## Approved Decisions

- Use one application with a visible company-profile switcher.
- Abraj and Eshraqat have isolated documents, databases, numbering, generated files, settings, banks, signatures, stamps, costing, statuses, analytics, To-Do records, and PDF identity.
- Share only the client catalog and autocomplete knowledge between profiles.
- Existing data becomes the Abraj profile. Existing generated PDFs are not renamed or moved.
- Eshraqat starts with no documents, banks, signatures, or stamp.
- Eshraqat generated documents live under `D:\ESHRAQAT DOCUMENTS` by default.
- Eshraqat default numbers are `QT-0001`, `IV-0001`, `DN-0001`, `PO-0001`, and `LT-0001`. Counters continue permanently and do not reset each year.
- Prefixes and zero-padding remain configurable in Eshraqat settings; permanent non-reuse is not configurable.
- All five Eshraqat document types use the same reference design system while retaining their document-specific fields and behavior.
- The app remembers the last active profile and reloads that workspace at startup.

## Architecture

### Profile Registry

Add a small global registry outside both company profiles. It contains profile metadata and the active profile ID, but no document or financial data.

Suggested location and shape:

```json
{
  "version": 1,
  "active_profile": "abraj",
  "profiles": [
    {"id": "abraj", "label": "Abraj Albadr"},
    {"id": "eshraqat", "label": "Eshraqat Albadr"}
  ]
}
```

Writes to the registry must use a temporary file plus atomic replacement. A corrupt or missing registry falls back to Abraj.

### Profile Context

Introduce one backend profile-context owner that resolves every company-dependent path at call time:

```text
data/
  profiles/
    abraj/
      po_tool.db
      settings.json
      assets/
        logo.png
        iso-certifications.png
        sigs/
    eshraqat/
      po_tool.db
      settings.json
      assets/
        logo.png
        iso-certifications.png
        sigs/
  shared/
    library.db
  profile-registry.json
```

`config.py`, database connections, generators, numbering, signing, settings, analytics, clients, costing, and data-manager operations must resolve the active profile through this context. No feature may build a company-dependent path directly from a legacy global constant.

### Profile Database

Each profile database owns all transactional and document-derived tables:

- `pos`, `quotes`, `dns`, `invoices`, `letters`
- `submitted_docs`, `paid_invoices`, `attention_ignores`
- `doc_cost_meta`, `doc_columns`, `doc_meta`, `deleted_docs`
- all document costing, linking, layout, signing-overlay, migration, and analytics state
- a new `document_counters` table for the Eshraqat numbering strategy

Abraj keeps its existing numbering behavior and data semantics. Eshraqat uses the permanent counters described below.

### Shared Work Library

The shared library owns convenience data only:

- client names, reusable contact/address suggestions, and client aliases
- PO item and company autocomplete
- quotation, DN, and invoice item variants
- universal per-column autocomplete values

In current schema terms this includes the catalog behavior backed by `clients`, `items`, `companies`, `quote_items`, `dn_items`, `inv_items`, and `column_memory`.

Client document lists, totals, addresses inferred from saved documents, payment history, signed state, and profitability always come from the active profile database. A client can therefore appear in both profiles while showing different company-specific document history and totals.

Catalog operations in Data Manager use the shared library and must be visibly labeled as shared. Destructive catalog actions warn that suggestions are affected in both profiles. Document sections in Data Manager remain active-profile-only.

## One-Time Migration

On the first profile-aware launch:

1. Detect the existing legacy database, settings, logo, signatures, stamp, and optional ISO assets.
2. Copy them into `data/profiles/abraj/` without moving or modifying the originals.
3. Populate `data/shared/library.db` from the existing catalog/autocomplete tables.
4. Create a clean Eshraqat profile database and settings file.
5. Copy the exact supplied Eshraqat logo and the reference document's ISO certification strip into Eshraqat profile assets.
6. Verify source/destination table counts, settings readability, and required Abraj assets.
7. Mark the migration applied and activate Abraj only after verification succeeds.

The migration is idempotent and resumable. If any verification fails, the app continues with the legacy Abraj setup and reports the failed step. It must not partially activate the profile system.

Existing Abraj PDF paths and document folders remain unchanged. The original legacy database/settings/assets remain as a recovery copy and are no longer written after successful activation.

## Profile Switching

### UI

Place a compact company switcher in the dashboard top bar immediately before the theme toggle and Settings button. It shows the active company's logo, name, and a chevron. The menu contains Abraj and Eshraqat with a clear active checkmark.

Repeat the active identity in the dashboard hero using the active company name, logo, and accent. The desktop window title becomes neutral, such as `Documents Hub`, so it is not permanently branded as Abraj.

### Behavior

When switching profiles:

1. If the active editor is dirty, offer Save, Discard, or Cancel.
2. Validate that the target database, settings, and asset directory are readable.
3. Atomically update the active profile.
4. Reload profile-aware configuration, generators, numbering, signing, analytics, and database routing.
5. Clear frontend editor caches, open-document IDs, right-panel selections, columns, status pills, and document defaults.
6. Reload the shell so dashboard, lists, counters, To-Do, costing, clients, and editors bootstrap from the target profile.

A failed switch restores the previous profile pointer and reloads the previous workspace. No stale editor state may remain visible after a successful switch.

The last active profile is restored on app startup.

## Eshraqat Numbering

Add a profile-local `document_counters` table keyed by document kind. The defaults are:

| Kind | Prefix | First number |
|---|---|---|
| Quotation | `QT` | `QT-0001` |
| Invoice | `IV` | `IV-0001` |
| Delivery Note | `DN` | `DN-0001` |
| Purchase Order | `PO` | `PO-0001` |
| Official Letter | `LT` | `LT-0001` |

Rules:

- Each kind has an independent permanent counter.
- Counters do not reset by year, month, client, or subtype.
- A deleted document leaves its number consumed.
- Editing retains the original number.
- Preview may show the next tentative number, but only a successful save commits it.
- Saving starts an immediate profile-database transaction, selects the next number, renders to a temporary file, stages the record/counter update, atomically moves the PDF, and commits only after the final file exists. If the move or commit fails, roll back the database and delete any temporary/final file created by that attempt.
- A rendering, path, or database failure does not leave a document record, final PDF, duplicate number, or accidentally consumed number.
- If a PO is split into multiple PO documents by the existing row-budget behavior, allocate one sequential PO number per generated document.

Abraj numbering modules and existing consumed-number behavior remain unchanged.

## Eshraqat Settings and Assets

Eshraqat settings start from a dedicated preset, not from Abraj settings.

Initial Eshraqat identity comes from the reference document:

- Name: Eshraqat Albadr
- Business lines: Contracting, General Services, Heavy Equipment Rental, and Employment of Iraqi Labor
- Website: `www.eshraqatalbadr.com`
- Emails: `info@eshraqatalbadr.com` and `eshraqat.albadr@gmail.com`
- Exact supplied logo, unchanged
- ISO 14001:2015, ISO 9001:2015, and ISO 45001:2018 strip from the reference document
- Burgundy reference palette

Eshraqat invoice banks initially contain only the existing Cash/no-bank sentinel. Signature and stamp slots start empty. Missing optional assets render as empty space or disabled signing controls; the app must never substitute an Abraj asset.

All document settings, user preferences, columns, default terms, prefixes, banks, and signing sizes are profile-local. Only the active-profile registry and approved shared library sit outside the profiles.

## Eshraqat PDF Design System

### Visual Contract

The supplied Word quotation is the visual reference for all Eshraqat documents:

- exact supplied logo and company identity at the top
- burgundy document title and table header treatment
- compact two-column document/client metadata block
- crisp bordered item table with optional image support
- amount-in-words and totals block where applicable
- terms and authorized-signatory area
- restrained website/email footer with ISO marks

Build one shared Eshraqat document shell and stylesheet used by PO, quotation, DN, invoice, and letter templates. Document-specific generators provide their own titles, metadata fields, column schema, totals/payment blocks, terms, and signing slots. This shared shell prevents the five templates from drifting visually.

Quotation follows the reference most directly. Invoice, DN, PO, and Letter use the same proportions, spacing, typography, colors, metadata treatment, table treatment, and footer while retaining existing document-specific content and controls. English labels are the default Eshraqat document language; Arabic official-letter content retains RTL direction inside the same visual system.

The exact logo file is used with proportional containment. Do not redraw, crop, recolor, or replace it.

### Existing Rendering Rules

The Eshraqat family must obey `docs/12-PDF-LAYOUT-CONSTITUTION.md`.

- Single page: header, metadata, table, totals, terms/signature, and footer fit without overlap.
- Two pages: page 1 reserves only its rendered header; page 2 receives remaining rows plus totals/closing/signature/footer.
- Long documents: headerless middle pages use their full body capacity; the last page reserves closing and footer space.
- Repeat table headers on continuation pages.
- Do not split a row across pages.
- Image dimensions and wrapped text contribute to measured row height.
- Saved document columns, renamed headers, dragged widths, Fit Columns, Fit Rows, and document-level Auto-fit remain honored.
- Totals, terms, signature/stamp, website/email/ISO footer, and page number receive measured reserved space.
- Keep a visible safe gap between the table/totals and terms/signing zone.
- Pull rows to an earlier page when the final closing area needs room.
- Preview, Save, Export, Print, and Sign use the same profile-aware render path and pagination result.

PO keeps its documented split-into-multiple-documents behavior instead of adopting quote-style pagination. Letters keep browser-flow pagination. Both use the Eshraqat shell and the same no-overlap outcome.

## Error Handling

- Migration failure: do not activate profiles; keep legacy Abraj operational.
- Target-profile validation failure: leave the current profile active.
- Mid-switch failure: restore the previous registry value and reload the previous workspace.
- PDF/path/database failure: remove temporary output, do not leave a document row, and do not consume an Eshraqat counter.
- Missing Eshraqat logo: block final PDF generation with a clear profile-specific error because the exact logo is required.
- Missing optional signature/stamp/bank: leave the optional area empty or disable the action; never fall back to Abraj data.
- Shared-library failure: document records remain readable; shared suggestions show an explicit unavailable state instead of querying a profile database as a fallback.

## Verification and Acceptance

### Migration and Isolation

- Verify Abraj source/destination table counts and settings before activation.
- Verify existing Abraj documents reopen and render unchanged after migration.
- Create records in one profile and prove they do not appear in the other profile's lists, search, dashboard, To-Do, costing, analytics, clients history, or Data Manager document sections.
- Prove logos, settings, banks, signatures, stamps, columns, statuses, deleted-number history, and PDF roots do not cross profiles.
- Prove clients and autocomplete variants are visible in both profiles.
- Prove shared client profiles show only active-profile document history and totals.
- Exercise switch rollback and unsaved-editor Save/Discard/Cancel behavior.

### Numbering

- Prove independent `QT`, `IV`, `DN`, `PO`, and `LT` counters.
- Prove counters continue across month/year changes and app restarts.
- Prove edit keeps the number and delete never releases it.
- Prove failed render/path/database saves do not duplicate or consume a number.
- Prove split POs allocate consecutive permanent PO numbers.

### Editor Workflows

For all five Eshraqat editors, verify New, Save, reopen, edit, delete, preview, export, print, and sign where supported. Also verify document-specific Submit, Paid, Cost, quote linking, import, Make Invoice, Show Prices, and status behavior where applicable.

New documents start from Eshraqat defaults. Saved documents restore their own fields, custom columns, widths, layout, Auto-fit value, links, and statuses.

### PDF Matrix

Render every Eshraqat document type with:

- one short row
- the maximum content that still fits on one page
- a two-page case
- a 40-100-row long case where supported
- long wrapped text
- optional images and tall rows
- custom/renamed columns and dragged widths
- Auto-fit on and off
- signature/stamp overlays after Eshraqat assets are added

Visually and geometrically verify the table never overlaps totals, terms, signature/stamp, footer, or page numbers. Middle pages must use available space efficiently. Compare the quotation's identity, proportions, metadata block, table treatment, closing area, and footer directly against the supplied Word reference.

### Regression Gate

Run all existing focused tests plus `python smoke_test.py` and `python smoke_quote.py`. Add profile, migration, shared-library, switcher, numbering, and Eshraqat rendering tests. The work is not complete after testing only a quotation; all five document types and both profiles must pass.

## Implementation Sequence

1. Add profile registry/context and profile-local path resolution.
2. Add idempotent migration and shared-library database routing.
3. Add profile switching and frontend state reset.
4. Add permanent Eshraqat numbering.
5. Add Eshraqat preset/assets and shared PDF shell.
6. Apply the Eshraqat shell to all five generators while preserving existing behavior.
7. Add isolation, workflow, numbering, migration, and PDF regression coverage.
8. Update the architecture map and maintenance documentation.

## Non-Goals

- No combined cross-company dashboard, costing, analytics, To-Do, search, or document list.
- No copying documents between companies.
- No automatic reuse of Abraj banks, signatures, stamp, numbering, settings, or PDF assets.
- No second executable or duplicated application codebase.
- No change to existing Abraj PDF design or numbering behavior beyond the storage migration required for profile support.
