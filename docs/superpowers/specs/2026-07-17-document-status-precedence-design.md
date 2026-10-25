# Document Status Precedence Design

## Goal

Make right-panel document badges describe the latest operational stage without destroying signing, submission, payment, or PO-fulfillment data.

## Rules

- The Submitted pill is removed from all right-panel cards and from the editor topbar. The existing Submit button remains the only submitted-state indicator.
- A submitted PO, quotation, DN, or invoice suppresses its Signed badge in the right panel. The saved signing overlay remains intact and the editor top-right signing indicator continues to reflect it when the document is opened.
- A manually paid invoice shows Paid and suppresses Signed.
- A DN or invoice linked to at least one PO shows Linked. Linked suppresses Signed; a linked invoice that is also paid keeps both its Linked and Paid pills.
- A PO with paid fulfillment shows Paid; otherwise a PO with delivered fulfillment shows Delivered. Either fulfillment stage suppresses Signed. Paid supersedes Delivered when both records exist.
- Submit, Paid, Linked, Delivered, and signing data remain reversible through their existing actions; this change only controls which right-panel badge is displayed.

## Data Flow

`po_fulfillment` remains the source of truth for PO delivery/payment and source-document links. A new bulk query exposes the set of linked DN or invoice numbers to the existing monthly list APIs. The frontend combines `submitted`, `signed`, `paid`, `linked`, `delivered`, and PO `paid` annotations when rendering each right-panel card.

## Verification

- Backend regression: linked source documents are annotated from `po_fulfillment` without mutating signing/submission records.
- Frontend regression: no Submitted pill markup remains; quotation submission hides Signed; Linked and Paid precedence are correct; PO Paid supersedes Delivered.
- Existing submission, invoice-paid, PO-link, deletion, profile-isolation, and smoke tests remain green.
