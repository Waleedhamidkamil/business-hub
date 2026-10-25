# Client LPO fixes and organization proposal

Authorized by Waleed on 2026-10-02 after the audit in ../16-CLIENT-LPO-AUDIT.md.
Fix application defects now; deliver a concrete UI organization suggestion.

1. Add isolated regressions and reproduce item loss, wrong seed quantities,
   currency/identity mistakes, rental periods and navigation gaps.
2. Decode stored lines for the bridge; preserve them when a header-only save
   omits rows; retain explicit empty orders. Validate internal quotation identity.
3. DN uses undelivered quantities. Materials invoices use delivered minus billed
   when a DN exists, otherwise order minus billed for direct invoicing. No silent
   full-order fallback after completion. Recurring contracts need an explicit
   period and entered quantity rather than billing their entire contract again.
4. Build one inspectable rental/supply billing ledger for the LPO screen and
   dashboard. Prefer recorded service ranges over invoice issue dates; mark date
   estimates clearly and allow a confirmed period without altering the PDF.
5. Use canonical client matching for links; label canonical figures in USD;
   remove invented signature deadlines; connect detail to DN/invoice and linked
   documents. Fix small-window progress, dark labels, accessible names and dirty
   form dismissal through shared Popup behavior.
6. Surface unresolved references for reconciliation. Do not invent an LPO or
   modify historical documents without evidence from their original paperwork.
7. Produce an interactive local UI proposal: list with attention filters, one
   workspace with Overview/Items/Billing/Documents, and a dedicated signing view.
8. Run focused/full JS checks, affected Python checks, controls that demonstrate
   regressions can fail, both real-PDF smokes on snapshots, and screen checks.
   Update map/board and commit only our explicit changes.

Completed: see ../17-CLIENT-LPO-FIXES.md for resulting behavior, the workspace
proposal and verification. Historical unmatched references remain in the
read-only review until their original paperwork identifies the correct LPO.
