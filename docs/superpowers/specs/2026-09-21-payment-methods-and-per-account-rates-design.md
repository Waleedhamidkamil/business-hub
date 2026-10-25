# Payment Methods and Per-Account Exchange Rates

## Goal

Manage payment methods in one place, give each one its own IQD-per-USD rate, and
let the account a client is actually paid into decide the rate that document's
profit is calculated at - frozen on the document at save, so a figure never
moves after the fact.

Waleed, 2026-09-21: "if HDEC's receiving bank in the client master data settings
is bank of baghdad that means whenever we are making quotation the exchange rate
of that bank is used"; and, on history: "if I made quotation in june for hdec and
at that time the receiving bank was bank of baghdad and the exchange rate was
1400 then in july I changed the receiving bank to ADIB and made quotation, that
quotation profit is calculated and saved for 1500. When I go back to check the
quotation from june it should be saved and calculated on 1400 because the account
used at that time is bank of baghdad."

## Scope

The account's rate drives the **profit maths only** - the conversion of costs to
USD. What an invoice PRINTS is untouched: `invoices.sell_iqd_per_usd` remains the
rate the client is billed at, and no existing PDF changes. A deal can legitimately
be costed at one rate and billed at another; that distinction already exists and
this design keeps it.

## What exists today

- Payment methods live per profile in `data/profiles/<id>/settings.json` under
  `invoice.bank_info[]`: `{key, label, company_line, bank_name, branch, swift,
  account_no, iban, currency}`. Abraj and Eshraqat have separate lists.
- They are editable only from the **⚙ on the invoice editor**
  (`btnInvTypeSettings` -> `doc_settings.js` `renderBankList`). Quotations and
  delivery notes have no bank concept at all: `static/quote.js` contains the
  string "bank" zero times.
- A client's receiving account is already on the client record:
  `client_master` `defaults.bank_key`, plus `alternative_bank_keys`.
- `database.doc_iqd_per_usd(kind, doc_no)` resolves the cost peg, in this order:
  1. `doc_cost_meta.rate` - a rate pinned by hand on the Costing screen;
  2. `invoices.sell_iqd_per_usd` - for an invoice that recorded one;
  3. **`CASH_IQD_PER_USD` (1500) when the profile is Eshraqat**;
  4. **`CASH_IQD_PER_USD` (1500) when the deal is paid in cash**, reaching the
     whole deal (a cash invoice re-pegs its root quotation and that quote's DNs);
  5. `config.IQD_PER_USD` - 1500 for Eshraqat, 1400 for Abraj, from
     `backend/config.py:35`. Neither profile sets `iqd_per_usd` in its
     settings.json, so both use that built-in default.

Rules 3 and 4 are invisible: the number they produce appears on the Costing
screen with no way to see why.

## Design

### 1. A Payments tab in main Settings

A sixth section in the Settings screen's nav, beside General / Behaviour /
Signatures & Stamp / Document Layout / About. It owns the payment-method list:
every field above, plus

- **`iqd_per_usd`** - this account's rate. Required, greater than zero.

and one setting that belongs to the profile rather than to a method:

- **`fallback_iqd_per_usd`** - the rate used when no account can be determined.
  Ships at **1500**. Editable, because a rate nobody can see or change is how the
  two hidden rules above came to exist.

The bank editor is REMOVED from the invoice ⚙ modal (`doc_settings.js`), which
keeps its column/sub-type settings. Two editors for one list is how they drift.
The modal gets a line pointing at Settings > Payments.

Storage stays in `invoice.bank_info[]` - a new key on each entry, and
`fallback_iqd_per_usd` beside it. Every existing reader keeps working.

Cash is an ordinary method with its own rate. Nothing about it is special any
more.

### 2. Which account a document belongs to

- **Invoice** - the account selected on the invoice itself (`#invBank`). That is
  the account receiving the money, so it decides, even when it differs from the
  client's default.
- **Quotation and delivery note** - they have no account of their own, so the
  client's `defaults.bank_key` from Master Data.
- **Neither available** - `fallback_iqd_per_usd`.

The client is resolved through `client_master`, so `HDEC` and
`Hyundai Engineering and Construction Co., Ltd` reach the same record.

### 3. The rate a document uses

`doc_iqd_per_usd(kind, doc_no)` becomes:

1. `doc_cost_meta.rate` - a hand-pinned rate. Unchanged, still wins: the user
   typed it while looking at the figure it produced.
2. **`doc_cost_meta.account_rate`** - the rate stamped when the document was
   saved. This is the new step and it is what freezes history.
3. Resolved live from the account (section 2), for a document not yet saved.
4. `fallback_iqd_per_usd`.

Rules 3 and 4 of the old chain - Eshraqat, and cash deals - are **deleted**.
`invoices.sell_iqd_per_usd` stops being read as a cost peg; it keeps its real job
of recording what the client was billed at.

### 4. Stamping

Two new columns on `doc_cost_meta`, which already keys on `(kind, doc_no)`:

- `account_key TEXT` - which payment method this document was costed against.
- `account_rate REAL` - that account's rate at the moment of saving.

Written by the quote / DN / invoice save paths, after the record is stored. Both
NULL means "never stamped", which is every document that exists today until the
migration runs.

Re-saving a document re-stamps it, which is correct: if the receiving account
changed, the document was re-issued against the new one.

### 5. Migration - the part that can go wrong

Deleting the Eshraqat and cash rules changes what `doc_iqd_per_usd` returns for
documents that rely on them. Every Eshraqat document does, and every cash deal in
Abraj does. Unstamped, their profit would move the moment this ships.

So the migration stamps first:

`2026-09-21_stamp_document_cost_rates`

1. For every row in `quotes`, `dns` and `invoices`, in both profiles, compute
   `doc_iqd_per_usd` **under the OLD rules**.
2. Write it to `doc_cost_meta.account_rate`, with `account_key` set to the
   account the new rules would have chosen, or `''` when none can be determined
   (the rate is what protects the figure; the key is for the readout).
3. Leave `doc_cost_meta.rate` alone - a hand-pinned rate keeps winning.

After it runs, every existing document answers from its stamp and cannot move.
The migration is idempotent: a row that already has `account_rate` is skipped.

The old-rule computation has to live in the migration itself, not be imported
from `database.py`, because `database.py` will no longer contain those rules.

### 6. What the user sees

The Costing screen already prints "Costs entered in IQD, converted at
1 $ = 1,400 IQD". It gains the account: "**converted at 1 $ = 1,400 IQD
(Bank of Baghdad)**", and for a stamped document, "(Bank of Baghdad, recorded
when this document was saved)". The reason for a number should be readable off
the screen that shows it.

No state is carried by colour alone (Waleed is colour blind): the account name is
words.

## Testing

The migration test is the one that matters and it gets written first.

1. **No historical figure moves.** Compute `doc_iqd_per_usd` and the profit total
   for every document in both live profiles BEFORE the change, store them, run
   the migration and the new code, recompute, assert every single figure is
   identical. Run against a copy of the live databases, not the live ones.
2. **Rate resolution**, as a table: hand pin beats stamp beats account beats
   fallback; invoice's own account beats the client's default; a client resolved
   through an alias finds the same account.
3. **Stamping freezes.** Save a quotation while the client's account is Bank of
   Baghdad at 1400, change the client to ADIB at 1500, assert the June quotation
   still reports 1400 and a new one reports 1500. This is Waleed's example,
   verbatim, as a test.
4. **Re-saving re-stamps**, and a hand pin survives a re-save.
5. **Settings round-trip**: a rate typed in the Payments tab reaches
   `settings.json` and comes back; a zero, blank or negative rate is refused
   rather than stored.
6. **The invoice modal no longer edits banks**, so the two editors cannot return.
7. Every new test is mutation-checked - break the code it covers, confirm it goes
   red - per the local rule in CLAUDE.md.

## Files

- `templates/index.html` - the Payments nav item and panel.
- `static/app.js` - the Settings screen owns its nav and panels there (`settingsNav`, around line 3949); the Payments editor is wired beside them. If it grows past a screenful, it moves to its own `static/payments_settings.js` rather than thickening `app.js` further.
- `static/doc_settings.js` - bank editor removed, pointer added.
- `backend/doc_settings.py` - `bank_info` is already a declared field there (line 115, `target="INV_BANK_INFO"`); the per-method rate rides inside it and `fallback_iqd_per_usd` is declared beside it.
- `backend/database.py` - `doc_iqd_per_usd` rewritten; `set_account_rate` /
  `get_account_rate` on `doc_cost_meta`; `_SCHEMA` gains the two columns (a new
  profile database never runs migrations).
- `backend/api.py` - the quote / DN / invoice save paths stamp.
- `backend/migrations.py` - `2026-09-21_stamp_document_cost_rates`.
- `static/app.js` - the Costing screen readout.
- Docs: `03-BACKEND-DATA.md` (the rate chain), `06-FRONTEND-EDITORS.md` (profit
  cards), `13-PROFILES-BRANDS.md` if the per-profile settings shape is described
  there.

## Out of scope

- What an invoice prints, and `invoices.sell_iqd_per_usd`.
- Tax and withholding. Profit remains revenue minus cost, before the 3.3%
  withholding, exactly as today.
- Per-row cost currency, which already works and is unaffected.
