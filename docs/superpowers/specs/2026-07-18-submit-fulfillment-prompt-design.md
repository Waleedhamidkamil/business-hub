# Submit Fulfillment Prompt Design

## Scope

Successful Submit actions for Delivery Notes and Invoices must open the same existing fulfillment picker from every surface: editor right panel, dedicated To-Do rows, To-Do preview popup, dashboard preview popup, and dashboard inline actions. PO and quotation Submit actions remain unchanged.

## Interaction And Architecture

`window.openSubmittedFulfillment(kind, no)` is the one frontend hand-off. It accepts only `dn` and `inv`, then delegates to `window.openPoFulfillPicker(kind, no)`. Each submit surface persists submitted state and refreshes its own UI first, then calls the helper. Preview modals close before the picker opens, preventing stacked backdrops. Cancelling the picker does not undo submitted state, matching existing right-panel behavior.

No backend, schema, status-precedence, or picker behavior changes are required.

## Verification

Frontend tests cover helper scope, right-panel reuse, To-Do row/modal ordering, dashboard inline reuse, and unchanged PO/quotation behavior. All existing frontend and workflow tests remain green.
