# DN Make Invoice Workflow Design

## Scope

When a user creates an invoice through a Delivery Note editor's `Make Invoice` button, saving that invoice must connect it to the source DN in the PO-centered Workflow. Ordinary invoice imports and manually created invoices remain unchanged.

## Data Flow

`createInvoiceFromDnNo(dnNo)` marks the new invoice draft with a transient `sourceDn`. The invoice payload sends it as `source_dn_no`; it is not a new invoice-table field. After `Api.save_inv` has rendered and saved the invoice, `database.link_invoice_from_dn(dn_no, inv_no)` copies the source DN's exact delivered PO item indexes into the invoice's `paid` fulfillment marks.

Workflow continues deriving DN-to-invoice relationships from overlapping delivered/paid PO indexes. No direct DN-to-invoice table or migration is added.

## Safety Rules

- Only the `Make Invoice` hand-off sets `sourceDn`; the general Import dialog does not auto-link.
- The helper links every valid delivered index across every active PO related to the DN.
- A PO item already paid by the same invoice is idempotently retained.
- A PO item paid by a different invoice is skipped and reported; it is never overwritten.
- A DN with no active delivered PO lines does not fail invoice saving. The result explains that no Workflow link could be created.
- The transient source DN is cleared after a successful invoice save and whenever a fresh or existing invoice is loaded.

## Verification

- Backend tests cover multiple POs, idempotency, conflicting invoices, missing documents, no-PO DNs, and `Api.save_inv` integration.
- Frontend tests cover Make Invoice scoping, payload propagation, state reset, and post-save conflict/no-link feedback.
- Existing Workflow, fulfillment, status, delete, frontend, profile-isolation, and isolated PDF smoke tests remain green.
