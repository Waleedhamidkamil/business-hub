# Client LPOs, simplified (2026-10-03)

Waleed's brief: "simple, great looking, easy to use, professional and reliable".
Tabs like the Materials / Equipment / Manpower / Service chips on the Quotation
and Invoice lists. Remove what is not needed (he named Previous / Next LPO),
add what is genuinely useful. Approved direction:
[docs/designs/client-lpo-simple.html](../../designs/client-lpo-simple.html).

## What changes

### 1. The list (`#lpoView`)

- **Header:** title + count, search, Add LPO, Back. Unchanged ids.
- **One line of figures** instead of four KPI cards: Ordered, Not invoiced yet,
  Need action. "Need action" is a toggle button that shows only the LPOs whose
  next step is an action; it replaces the old "Needs billing" toggle.
- **Type tabs:** the shared `renderChipRow` control (`.filter-chip`), exactly as
  the Quotation and Invoice lists use it: All, Materials, Rentals, Supply
  contracts, with counts. A second chip row for clients appears only when the
  LPOs span more than one client; it replaces the client `<select>`.
- **A table whose columns follow the tab:**

  | Tab | Columns |
  |---|---|
  | All | LPO, Type, Progress, Value, Next step |
  | Materials | LPO, Items, Delivered, Invoiced, Value, Next step |
  | Rentals | LPO, Period, Months billed, Billed, Next step |
  | Supply contracts | LPO, Supplying, Unit price, Invoiced, Not invoiced, Next step |

- **Next step** on every row, as a button when there is something to do. The
  rest of the row opens the LPO. Open LPOs first (newest first), closed ones at
  the bottom, dimmed and struck through.
- **Unmatched invoice references** move from a full-width banner to a quiet
  link under the table. Same review dialog.
- Removed: the three kind group headers, the four KPI cards, the status pill
  column, the client `<select>`.

### 2. The LPO screen (`#lpoDocView`)

- **Header:** "LPOs" back link, LPO number, type, status; on the right the next
  step as the primary button, a second create button where it applies
  (materials and supply contracts have both delivery notes and invoices), and a
  "More" menu: Open in PDF reader, Print, Copy file, Show in folder, Edit
  details, Replace or Attach file, Sign again, Close or Reopen, Remove.
- **Removed:** Previous / Next LPO, the ten-button grid, the big "Sign and
  stamp" button when a signed copy already exists.
- **Document toolbar:** page, zoom, **Fit width** (new; the default on open),
  Original / Signed copy switch. The zoom you choose is kept for the session.
- **Right panel, fixed 360 px**, in this order:
  1. one summary line (client, received date, quotation link) and notes;
  2. three figures that depend on the type;
  3. a **signed copy** line: saved (with Copy file, to send it back), or not
     yet (with Sign and stamp), or no document (with Attach file);
  4. a warning only when one applies (over the LPO value; fully invoiced with
     no end date);
  5. **Items** (materials: ordered, delivered, invoiced) or **Billing periods**
     (rental and supply: ONE list, each period showing its invoice number, or
     its own Invoice button; estimated dates carry a Confirm button);
  6. linked documents (quotation, delivery notes, and invoices that are not
     already shown against a period);
  7. details, folded.
- Signing mode keeps its behaviour and ids; the panel is restyled to match.

### 3. One rule for "next step" (backend)

`database.lpo_next_step(row, fulfil)` decides it, once, for the list and the
LPO screen. Returned as `next_step = {key, text, action, period_start?}`.

| Kind | Condition, in order | key | text |
|---|---|---|---|
| any | closed by hand | closed | Closed |
| materials | a line delivered more than invoiced | invoice | Create invoice |
| materials | a line not fully delivered | deliver | Create delivery note |
| materials | no lines recorded: no DN and no invoice | deliver | Create delivery note |
| materials | no lines recorded: invoiced below total | invoice | Create invoice |
| materials | everything delivered and invoiced | done | Fully invoiced |
| rental | value fully invoiced, periods unbilled, no end date | end_date | Set the end date |
| rental | value fully invoiced | done | Fully invoiced |
| rental / supply | a period unbilled (`months_unbilled`) | invoice_period | Invoice <month of oldest> |
| rental | term ended, nothing unbilled | done | Finished |
| rental | ends within 45 days (time to ask for the next LPO) | ending | Ends <d Mon> |
| any | no document attached | attach | Attach their PO |
| any | otherwise | ok | Up to date |

`action` is true for invoice, deliver, invoice_period, end_date and attach.
The billing ledger itself (`lpo_billing.summary`) is unchanged.

To give materials rows their delivered / invoiced counts without a bridge call
per row, `list_lpos` builds ONE delivery-note and invoice index and runs the
existing `_fulfillment_from_rows` per materials LPO. `lpo_fulfillment` uses the
same index builder, so the list and the LPO screen cannot disagree.

## Not changing

- Billing periods and due rules (`lpo_billing`), including the current month
  counting as due (pinned by `client_lpo_billing_due.test.py`).
- Signing: placement, fractions, merge, the never-modified original.
- The add / edit form, except that "Set the end date" opens it.
- Data. The 42 diesel delivery notes to HDEC marked LPO "N/A" (May to
  September, about 47,806 L) are reported to Waleed, not relinked.

## Verification

- Python: `lpo_next_step` for every row of the table above and the list
  rollup, on isolated data (`tests/isolated_client_data.py`).
- JS: tab columns, filters, next-step buttons, viewer header actions, panel
  sections, removed Previous / Next, against the real `static/lpo.js`.
- Existing LPO suites updated where they pinned removed markup, keeping what
  each protected.
- Real page in headless Chrome with both companies' real data, light and dark,
  1366 and 1024 wide.
- Full JS suite, LPO Python suites, both smokes, no em dash.
