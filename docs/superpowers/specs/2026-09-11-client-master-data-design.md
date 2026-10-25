# Client Master Data Design

## Goal

Make one canonical client record drive short document codes, printable names, aliases, and invoice defaults while leaving historical document numbers and PDFs unchanged.

## Data model

The cross-profile shared `clients` table remains the canonical catalog and gains:

- `client_id`: stable generated identifier.
- `document_code`: short code used in Abraj quotation, DN, and invoice numbers and client folders, such as `TDC`, `HDEC`, or `JUNE`.
- `legal_name_en` and `legal_name_ar`.
- `default_print_name`: the normal name printed on new English documents.
- `default_payment_bank_key`, `default_address`, and `default_withholding_included`.

A new `client_aliases` table stores many names for one client. Code, catalog name, legal names, and explicit aliases all resolve to the same client. Matching is exact after safe Unicode, whitespace, and case normalization; fuzzy matching may be shown to the user but must never silently bind a document.

Existing `clients.name` remains for compatibility and becomes the concise catalog/display label. Existing rows are upgraded in place with generated IDs and codes derived from their current name. Existing document rows are not rewritten during schema migration.

## Document behavior

For a new quotation, DN, or invoice, the entered name is resolved through client master data:

- Number and folder use `document_code` under the Abraj profile.
- The PDF uses the selected per-document printable name.
- The saved document keeps that printable name as a historical snapshot.
- A new `client_id` column keeps the document attached to the canonical client for grouping and defaults.

Eshraqat keeps its existing client-independent numbering (`QT-0001`, `DN-0001`, `IV-0001`). Client identity and defaults still apply.

Manual edits always win for the current document. Changing master data affects new documents only, never an already-saved PDF or number.

## Invoice defaults

When a new invoice client resolves successfully, the editor applies the client's default payment method, address, and withholding setting. Editing an existing invoice preserves its saved values. Changing from one client to another reapplies the newly selected client's defaults; the user can then override them for that invoice.

DN-to-invoice and PO-to-invoice hand-offs resolve the source name to the canonical client before applying defaults and generating the number.

## PO behavior

The visible PO item `company` text remains exactly what the user typed, including Arabic. Saved PO rows gain hidden `client_id` and `client_code` values when an alias resolves.

A new **Make invoice** action on a saved PO:

1. Resolves every client-classified PO item company.
2. Ignores items classified as internal.
3. Continues automatically when all selected items resolve to one client.
4. Stops with a short actionable message when names are unresolved or resolve to more than one client.
5. Creates a materials invoice, copies the matching PO items, applies client defaults, generates the number from the client code, and links those PO item indexes to the saved invoice.

## Client management UI

The existing Clients master-detail screen gains an **Edit master data** action. The editor manages the document code, English/Arabic legal names, default print name, aliases, payment method, address, and withholding. The client history and document tabs remain read-only.

Validation prevents blank or duplicate document codes and prevents one alias from belonging to two clients.

## Compatibility and migration

- Shared-schema upgrades are idempotent and run from `shared_library.init_shared_db()`.
- New per-profile `client_id` columns are declared in `database._SCHEMA` and added by an idempotent migration.
- Existing documents are backfilled only when their current `client_name` resolves unambiguously.
- Existing numbers, filenames, PDF contents, and printed names are never changed automatically.
- Legacy APIs that pass only a name continue to work; resolution happens behind the bridge.

## Testing

Backend tests cover schema upgrade, alias resolution, duplicate rejection, default lookup, number-code resolution, document client IDs, and migration safety. Frontend tests cover client-default application, DN and PO hand-offs, Arabic PO aliases, direct PO fulfillment linking, and preservation of manual overrides/edit-mode values. Existing smoke tests remain the final regression gate.
