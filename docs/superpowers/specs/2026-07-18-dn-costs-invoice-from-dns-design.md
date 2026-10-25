# DN Costing + Invoice-from-DNs Design

**Date:** 2026-07-18
**Status:** approved (design) - pending spec review

## Context / goal

Sometimes one invoice covers **several delivery notes**, and the invoice total is the sum of those DNs' totals (see reference `INVOICES/Materials/TDC/2026/JUL/18-07-2026/INV-MT-TDC-017.pdf`: four lines `DN-TDC-036…039`, each Qty 1 / Rate = that DN's total, plus a "Transportation fees" line, and the four DN documents appended after the invoice page).

Costing such an invoice is painful today: costs must be entered per invoice item, but the "items" are whole DNs. This feature lets the user **enter market-price costs once, in the DN**, then **drop that DN into an invoice as a single line** that carries its total (as the rate) and its summed cost (for profit). Inserting a DN also **appends the DN's pages to the invoice PDF** and **marks the DN's PO(s) as invoiced** (closing the "Deliver & Invoice" To-Do loop).

## Part 1 - DN costing

- A **Costs** button in the DN editor toolbar opens an inline panel listing the DN's item rows with a **cost (market price)** field per line - mirroring the existing Costing & Profit cost grid. It shows the running **DN total cost** (sum) and, when the DN has prices, profit (DN sell total − cost).
- Persisted through the **existing** `save_doc_costs(kind="dn", …)` / `doc_cost_meta` path (already supports arbitrary kinds); no new table. Cost currency reuses the existing per-doc USD/IQD switch (`get_cost_currency` / `doc_iqd_per_usd`).
- A small backend read, `dn_cost_summary(dn_no) → {sell_total, cost_total, currency, iqd_per_usd}`, exposes the sums for the invoice-line inheritance (Part 2).

## Part 2 - DN as an invoice line

- The invoice item grid's item field gains **DN suggestions** (a new autocomplete source): typing offers **priced** DNs by number; picking one fills a single line - Item = DN number, Unit = `EA`, Qty = `1`, **Rate = DN sell total**, Total = Rate. The row carries an explicit marker (`dn_ref = "<DN no>"`) so the generator and cost logic can recognize it.
- The line's **cost is inherited** from `dn_cost_summary` - not re-entered. Invoice profit = Σ(line rate) − Σ(inherited DN cost) for DN lines (plus normal costing for typed lines).
- **Snapshot vs live (important):** the invoice **stores** the DN line's rate/total/inherited-cost at insertion time - a financial document must not have its total silently change if the DN is edited later. Only the **appended pages** (Part 3) re-render from current DN data.
- Multiple DN lines + normal typed lines coexist (e.g. "Transportation fees"), exactly like the reference.
- Only **priced DNs** are insertable (an unpriced DN has no total to bill); the suggestion list filters to priced DNs.
- **Auto-close the PO loop:** on invoice **save**, each `dn_ref` line runs `link_invoice_from_dn(dn_ref, invoice_no)` (Codex's existing, currently-unwired function - it copies the DN's delivered PO item indexes to this invoice as `paid`/`inv` marks, with conflict reporting). The POs behind the DN then leave the "Deliver & Invoice" To-Do automatically. Deleting the invoice already cleans these marks (`delete_document_record` drops `po_fulfillment WHERE source_kind='inv' AND source_no=?`).

## Part 3 - Appended DN pages (approach A: re-render inline)

- When an invoice has `dn_ref` lines, the invoice generator appends each referenced DN's page(s) after the invoice page in the **same headless-browser render pass** (no new dependency; the app has no Python PDF library by design). It reuses `dn_generator`'s page/HTML building to produce the DN pages, separated by `page-break-before: always`, honoring the active brand skin (abraj/eshraqat).
- The preview and the saved PDF both include the appended DN pages. Appended pages reflect the DN's data **at render time** (consistent with re-rendering; no frozen snapshot).
- Page numbering: the invoice keeps its own "Page X of Y"; each appended DN keeps its own numbering (self-contained document per the existing DN template), matching the reference where each appended DN reads "Page 1 of 1".

## Data flow

`DN editor → save_doc_costs(kind="dn")` → costs live in the DN's cost meta.
`Invoice editor → pick DN → dn_cost_summary(dn_no)` fills rate + inherited cost; row tagged `dn_ref`.
`save_invoice` → renders invoice HTML + appended DN HTML → one PDF; then `link_invoice_from_dn` per `dn_ref`.
`Costing & Profit / dashboard` → invoice profit already reads inherited line costs; DN lines are not a separate profit source (no double count).

## Edge cases

- **Unpriced DN inserted** - prevented (suggestions filter to priced DNs); if a `dn_ref` later loses prices, its rate shows 0 and it is flagged in the editor.
- **DN edited after invoicing** - the invoice line's stored rate/total/cost do NOT change (snapshot, Part 2); only the appended pages re-render to the DN's current content. If the DN's total changed, the appended pages can differ from the invoiced line total until the user deliberately re-inserts the DN - accepted, since invoices are rarely regenerated post-issue.
- **DN deleted after invoicing** - the `dn_ref` line keeps its stored rate/total (self-contained on the invoice); the appended pages for the missing DN are skipped, and the fulfillment marks were removed on DN delete.
- **Cost currency mismatch** (DN cost in IQD, invoice in USD) - inherited cost is converted through the existing peg (`doc_iqd_per_usd`) so profit math stays in one currency, same as today's costing.

## Out of scope

- No change to normal typed invoice lines or to quotes/PO/letters.
- No merging of pre-existing saved PDF files (approach B rejected - would add a dependency).
- No new DN "profit line" in Costing & Profit (DN cost is realized only via the invoice).

## Verification

- **Backend:** `dn_cost_summary` sums correctly (per-item costs → total, currency-aware); inserting a DN line then saving an invoice writes the inherited cost and the `link_invoice_from_dn` marks; the PO drops from `pos_awaiting_invoice`; deleting the invoice restores it.
- **Generator:** an invoice with `dn_ref` lines renders the invoice page **plus** each DN's page(s) in one PDF (page count = invoice pages + Σ DN pages), in both abraj and eshraqat skins; smoke + eshraqat rendering tests stay green.
- **Frontend:** DN suggestions appear only for priced DNs; picking one fills rate + Unit `EA`/Qty `1`; the DN Costs panel sums and persists; existing invoice/costing regressions stay green.
- Run `python smoke_test.py`, the invoice/eshraqat rendering tests, and the costing/deliver tests before claiming done.
