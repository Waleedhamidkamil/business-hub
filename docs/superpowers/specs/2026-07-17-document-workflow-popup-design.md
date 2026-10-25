# Document Workflow Popup Design

## Scope

Add a Workflow toolbar action only to the Purchase Order, Delivery Note, and Invoice editors. The feature tracks delivery and payment through the existing PO fulfillment links. Quotations and Official Letters are unchanged.

## Interaction

- The toolbar button is enabled only while editing a saved document.
- Clicking it opens one shared LTR modal showing one workflow lane per related PO: `Purchase Order -> Delivery Notes -> Invoices`.
- A PO lane contains every DN that delivered at least one of that PO's lines and every invoice that paid at least one line.
- The active document is visually identified. Clicking another linked document opens it in its editor.
- A missing DN or invoice stage is a real action, not an empty decoration. It opens the existing smart PO-side source picker for that exact PO.
- An unlinked DN or invoice shows its current document beside a missing PO action. Clicking the PO action opens the existing DN/Invoice-to-PO picker.
- After a successful link, the Workflow modal reopens with fresh backend data.
- One invoice may consume items delivered by multiple Delivery Notes. Inside each PO lane, an invoice node shows every DN whose delivered PO-item indexes overlap that invoice's paid indexes. Repeated item marks deduplicate into one DN contribution with an item count.
- Invoice items paid directly against the PO remain valid. An invoice with no contributing DN is labelled `Direct to PO`; if it mixes DN-backed and direct PO items, both the DN contributions and the direct-item count are shown.
- Every invoice node offers a compact `Add delivery note` action. It reuses the invoice picker's existing `Linked delivery notes` route, so another DN can contribute any still-uninvoiced PO lines. A successful link refreshes and reopens Workflow.
- Every fulfillment picker separates available candidates from documents already linked to its current source: DN/Invoice-to-PO uses `Available POs | Linked POs`; PO-to-DN and PO-to-Invoice use corresponding Available/Linked tabs. Invoice-to-PO keeps its Linked Delivery Notes route as a third tab. Linked records never remain mixed into the Available list.
- Each relationship node in Workflow has a compact corner Manage-link X where the relationship can be removed or replaced. The current PO node has no X. On a DN/Invoice node, the action affects only that source document's marks for the lane PO. On a PO node viewed from a DN/Invoice, it affects only the current source document's marks for that PO.
- Replace opens the existing smart picker with the old source/PO excluded, saves the new selected relationship first, and removes the old relationship only in the picker's successful `onAfter`; self-replacement is impossible and cancellation or failure preserves the old link. Remove asks through the Manage-link choice and deletes only the exact relationship.

## Architecture

`backend.database.document_workflow(kind, doc_no)` resolves the connected fulfillment data into independent PO lanes. `backend.api.Api.document_workflow` exposes it to pywebview. A focused `static/document_workflow.js` owns the modal, rendering, picker handoff, refresh, and cross-editor navigation. Editor files only provide the saved document identity and refresh callback.

The existing `po_fulfillment` table remains the sole source of truth. This feature does not add tables, duplicate links, or alter status transitions.

Invoice-to-DN relationships are derived, not stored separately: for one PO lane, a DN contributes to an invoice when the DN's `delivered` item-index set intersects the invoice's `paid` item-index set. This keeps the existing one-paid-source-per-PO-line constraint and prevents the Workflow display from drifting away from fulfillment state.

`clear_po_fulfillment_link(po_no, source_kind, source_no)` deletes every item-level mark for exactly one PO/source relationship and reports the removed row count. It does not change submitted, signed, or paid lifecycle state.

## Failure And Empty States

- New or unsaved documents cannot open Workflow and explain that the document must be saved first.
- A deleted or unavailable document returns a stable `ok: false` response and the popup shows a concise error.
- A saved but unlinked DN or invoice remains actionable through the missing PO stage.
- Picker cancellation returns to the editor without changing data.

## Verification

- Backend regression tests cover PO, DN, and invoice lane resolution, multiple links, deduplication, and unlinked documents.
- Backend regression tests cover one invoice consuming multiple DNs, mixed direct/DN-backed invoice lines, and direct-to-PO invoices.
- Frontend contract tests cover toolbar scope, saved-document wiring, shared modal loading, lane actions, existing picker reuse, and script order.
- Picker tests cover Available/Linked separation in all four directions, preservation of the invoice Linked Delivery Notes route, and empty linked states. Workflow tests cover Manage-link targeting, remove refresh, and replace-after-success ordering.
- Frontend workflow tests cover contribution labels and the invoice-node Add-delivery-note handoff to the existing linked-DN picker tab.
- Existing toolbar, fulfillment suggestion, status precedence, and smoke tests remain green.
