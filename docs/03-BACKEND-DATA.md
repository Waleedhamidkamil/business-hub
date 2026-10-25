# Backend - Data & Persistence

The SQLite persistence layer plus the read-side aggregates (Data Manager, client profiles, dashboard analytics) that sit on top of it. Since the multi-company profile split, persistence is **TWO databases**: the **per-profile** document DB (`data/profiles/<ACTIVE_PROFILE>/po_tool.db`) and the **cross-profile shared catalog DB** (`data/shared/library.db`, owned by `backend/shared_library.py`). The profile subsystem itself (registry, paths, bootstrap, switching) is [[13-PROFILES-BRANDS]].

**Covers:** backend/database.py, backend/db_version.py, backend/migrations.py, backend/data_manager.py, backend/client_master.py, backend/po_catalogue_review.py, backend/translate.py, backend/clients.py, backend/analytics.py, backend/insights.py, backend/master_data.py (+ backend/shared_library.py in brief)

Related: [[01-ARCHITECTURE]], [[02-BACKEND-CORE]], [[04-BACKEND-DOCGEN]], [[07-FRONTEND-SUPPORT]], [[09-DATA-MODEL]], [[13-PROFILES-BRANDS]]

---

## backend/database.py

**Purpose** - The single SQLite module. Owns the per-profile schema (`_SCHEMA`), every per-doc-type `save_/load_/list_`, all autofill (`suggest_/remember_` - these now read/write the SHARED library DB), the decoupled side-tables (`submitted_docs`, `doc_cost_meta`, `doc_columns`, `doc_meta`, `column_memory`), and the entire Phase N/R costing + profit engine that reads those tables.

### Connection & init (two databases)

- `_connect() -> sqlite3.Connection` - opens `config.DB_PATH` = `profile_manager.profile_db_path()` = `data/profiles/<ACTIVE_PROFILE>/po_tool.db`, sets `row_factory = Row`, `PRAGMA foreign_keys = ON`. Every function opens its own short-lived connection; the `with` block commits on exit. **All doc tables, satellites, costing, `deleted_docs`, and `_migrations_applied` are PER-PROFILE.** **The data-dir `mkdir` is memoized in `_ensured_data_dir`** (2026-07-18) - re-run only when `config.DATA_DIR` changes (profile switch / a test repointing it), so the hottest DB primitive skips a redundant filesystem stat on every open. Don't assume `_connect` always `mkdir`s; if you repoint `config.DATA_DIR` to a fresh dir at runtime it will still be created on the next connect (the memo compares paths by value).
- `_connect_shared() -> sqlite3.Connection` (~line 324) - `shared_library.connect()` → `data/shared/library.db`. **The cross-profile shared DB holds the item record store `master_items` plus `companies`, `clients`, `column_memory` (= `shared_library.SHARED_TABLES`)** - every `suggest_*`/`remember_*`/column-memory function goes through it. The four document catalogs (`items`/`quote_items`/`dn_items`/`inv_items`) were **retired and dropped on 2026-08-20**; they survive only as `shared_library.RETIRED_CATALOGS`, a name-list used by the `_data_sig` normalizer and the profile-migration integrity check ([[13-PROFILES-BRANDS]]).
- `best_effort_shared(operation, *args, **kwargs) -> bool` (~line 328) - swallow-and-log wrapper. **Every `remember_*` call inside the document-save paths (`save_po_record`/`save_quote_record`/`save_dn_record`, plus 5 call sites in `api.py`) is wrapped in it so a shared-DB failure never fails a document save.**
- `init_db() -> None` - runs `_SCHEMA` via `executescript` on the PROFILE DB (all `CREATE TABLE IF NOT EXISTS`), then `shared_library.init_shared_db()`, then two idempotent seeds run on the SHARED connection: backfill `companies` from `items.last_company`, and seed `clients` from `config.QUOTE_CLIENTS_SEED`. Called from `app.main()` before `migrations.run_all()` - and re-run (together with `migrations.run_all()`) on EVERY profile switch by `Api._reload_profile_runtime` ([[02-BACKEND-CORE]], [[13-PROFILES-BRANDS]]).

### Schema (`_SCHEMA`) - tables created

`items`, `companies`, `pos` (including optional `source_quote_no`), `clients`, `quotes`, `quote_items`, `dns`, `dn_items`, `invoices`, `inv_items`, `submitted_docs`, `paid_invoices`, `doc_cost_meta`, `doc_columns`, `doc_meta`, `deleted_docs`, `column_memory`, `document_counters`. Indices: `idx_pos_month`, `idx_quotes_month`, `idx_dns_month`, `idx_invoices_month`, `idx_deleted_docs_kind`, `idx_column_memory_lookup`. Full column list is in [[09-DATA-MODEL]]. Note the multi-variant PKs `(name, data_sig)` on `items`/`dn_items`, `(quote_type, name, data_sig)` / `(inv_type, name, data_sig)` on the item-memory tables.

Two profile-era caveats: `document_counters(kind, last_value)` (~line 289) is the **permanent per-profile numbering floor** written by `profile_numbering.py` ([[13-PROFILES-BRANDS]]); and the autofill tables (`items`/`companies`/`clients`/`quote_items`/`dn_items`/`inv_items`/`column_memory`) that `_SCHEMA` still creates in each profile DB are **vestigial there** - live autofill data is only in `data/shared/library.db` (see the bootstrap gotcha below).

### Autofill: suggest_ / remember_

All of these run against the SHARED library DB via `_connect_shared` - one catalog serving every profile.

- `suggest_items(prefix, limit=8)` / `remember_item(name, description, unit, company, data=None)` - PO item memory. `remember_item` computes `data_sig` from `{description, unit, last_company}` so same-name variants coexist. **`data` (2026-08-11) is the extra payload the four fixed columns cannot hold - today `image_refs`, so a PO item's PICTURE comes back with it.** It is deliberately OUTSIDE the signature: remembering a picture must not fork a second entry for the same item. `ON CONFLICT` uses `COALESCE(excluded.data_json, items.data_json)`, so re-saving the item from a PO with an empty picture cell does not erase the one remembered earlier. `suggest_items` returns the parsed blob as `data` (always a dict; a corrupt blob degrades to `{}`).
- `suggest_companies(prefix, limit=8)` / `remember_company(name)` - PO company memory (returns `[{"name": ...}]`).
- `suggest_clients(prefix, limit=12)` / `remember_client(name)` - client catalog. **Empty prefix returns the full list** (dropdown-on-focus).
- `suggest_dn_items(prefix, limit=8)` / `remember_dn_item(name, data)` - DN item memory. `data` is supplied by the caller: `api._dn_item_payload` normalises the cost to `cost_usd` (2026-08-13); without an `item_payload` the record falls back to "everything except `item`", raw cost included.
- `suggest_quote_items(quote_type, prefix, limit=8)` / `remember_quote_item(quote_type, name, data)` - per-quote-type memory.
- `suggest_inv_items(inv_type, prefix, limit=8)` / `remember_inv_item(inv_type, name, data)` - per-invoice-type memory.
- All `suggest_*` order by `use_count DESC, last_used_at DESC`; all `remember_*` upsert with `ON CONFLICT … DO UPDATE SET use_count = <t>.use_count + 1`.

### Data signature (multi-variant)

- `_data_sig(data) -> str` - deterministic hash: lower-case+trim keys, drop empty/None values, `json.dumps(sort_keys=True)`, `sha1[:16]`. Returns `""` for non-dict, `"empty"` for empty dict. **Duplicated verbatim** in `migrations._sig_from_data`, `data_manager._data_sig`, AND `shared_library._data_sig` (four copies total).

### PO records

- `save_po_record(po_no, po_date, year, month, pdf_path, rows, notes="", source_quote_no=None, item_payload=None)` - upsert on `po_no`; `source_quote_no=None` preserves any existing quotation reference, `""` clears it. Then `remember_item` + `remember_company` per row. **`item_payload(row) -> dict | None`** is injected by `api.save_po` (`_po_item_payload`): building it means WRITING image bytes to the content-addressed store, which this layer has no business knowing about. A raising callback is swallowed - a picture must never fail a PO save.
- `load_po(po_no)` - row as dict, `rows_json` → `rows`, includes `source_quote_no`.
- `list_pos_in_db(year, month)` - light list ordered by `po_no`, includes `source_quote_no`.
- `po_exists(po_no)` - DB check, then **filesystem fallback** `config.PO_ROOT.rglob(f"{po_no}.pdf")`.

### Quote records

- `save_quote_record(quote_no, quote_date, quote_type, year, month, client_name, rfq_no, client_address, pdf_path, rows, terms)` - upsert; **does NOT write `extra_costs_json`/`bid_status`** (costing owns those). Then `remember_client` only. **It must NOT call `remember_item` (2026-08-14).** That writes the `items` table, which is *PO* memory; a quotation's items belong in `quote_items`, written by `api.save_quote`. The old call dated from when `items` was the only memory there was (2026-05-26; `quote_items` arrived 06-01) and was never removed, so every saved quotation poured its items into PO autocomplete. Measured before removal: **492 of 815 PO memory rows were English and not one appeared in any of the 69 saved POs** - 462 belonged to quotations, DNs and invoices, which is why an Arabic-only PO dropdown filled with English. 461 stale rows were purged; pinned by `tests/quote_does_not_pollute_po_memory.test.py`.
- `load_quote(quote_no)` - parses `rows_json`→`rows`, `terms_json`→`terms`.
- `list_quotes_in_db(year, month)`, `quote_exists(quote_no)` (DB + `QUOTE_ROOT` fallback).

### DN records

- `save_dn_record(dn_no, …, include_prices, pdf_path, rows, source_quote_no=None, item_payload=None)` - upsert; **`source_quote_no=None` preserves the existing link** (reads prior value first). Then `remember_client` + `remember_dn_item`. `item_payload(row) -> dict|None` mirrors `save_po_record`'s hook: `api.save_dn` injects one so the cost is remembered as `cost_usd`. A raising callback is swallowed - a memory payload must never fail a DN save.
- `load_dn(dn_no)` - `rows`, `include_prices`→bool.
- `list_dns_in_db(year, month)`, `dn_exists(dn_no)`.

### Invoice records

- `save_inv_record(inv_no, …, payment_bank_key, withholding_included, grand_total, pdf_path, rows, source_quote_no=None, source_dn_no=None, sell_currency="USD", sell_total=None, sell_iqd_per_usd=None)` - same source_quote_no-preservation trick, and `source_dn_no` (2026-07-24) follows the identical rule: `None` preserves the existing link, `""` clears it. `grand_total` is **always USD**; `sell_currency`/`sell_total` (2026-07-29) carry the issued currency - see the sell-currency section below. **Does not call any remember_ helper** (unlike PO/quote/DN).
- **`invoices.source_dn_no` (2026-07-24)** - the DN an invoice was created from, stored outright. Added by migration `2026-07-24_inv_source_dn` (mirrors `pos.source_quote_no`). Previously the DN→INV relationship was ONLY derived through `po_fulfillment`, so an invoice made from a DN with no linked PO recorded nothing and its Workflow showed no DN at all (63% of DNs in the live Abraj DB had no PO link). `api.save_inv` fills it from either write path: the "Make invoice" flow's `source_dn_no`, else the first `dn_ref` row (`_inv_dn_refs`).
- `load_inv(inv_no)` - `rows`, `withholding_included`→bool, `paid` from `paid_invoices`.
- `list_invoices_in_db(year, month)` (includes `inv_type`, `grand_total`, `sell_currency`, `sell_total`, `paid`), `inv_exists(inv_no)`.
- **App-only pricing drafts (2026-08-24):** an empty `quotes.pdf_path` / `invoices.pdf_path` is a deliberate editable draft state, not a broken file reference. Month lists and client document lists keep these rows so the table/items can be reopened, but all issued-document reads exclude them: analytics/Insights/profit and client totals (including quoted-value and activity metrics), quote-won logic, LPO usage, DN/PO fulfillment, deal/link pickers, Unsubmitted/Unpaid, and signed ratios. Submit, Paid, Won/Lost, and invoice→PO fulfillment mutations reject the draft until a complete save writes a PDF path; the same row then automatically rejoins official/workflow reads. The right panel owns the visible `incomplete_price` marker. Guards: `tests/incomplete_price_draft_isolation_backend.test.py`.

### submitted_docs (Phase Q)

- `set_submitted(kind, doc_no, value=True) -> dict` - insert/delete in `submitted_docs`; `kind` ∈ `_SUBMIT_KINDS = {"po","quote","dn","inv"}`. Dashboard unsubmitted reminders still enumerate QUT/DN/INV only.
- `is_submitted(kind, doc_no) -> bool`.
- `submitted_set(kind) -> set[str]` - all submitted numbers for a kind (cheap list annotation).

### attention_ignores (Phase X.30)

- `set_attention_ignore(kind, doc_no, category, value=True) -> dict` - mute (or restore) ONE doc for ONE Needs-Attention category (`decide`/`uncosted`/`unsubmitted`/`unpaid`; validated; kinds include PO/QUT/DN/INV where that category can produce them). Per-category on purpose: silencing one concern keeps the others nagging.
- `attention_ignore_map() -> {category: {(kind, doc_no)}}` - cheap feed filtering.

### paid_invoices

- `set_invoice_paid(inv_no, value=True) -> dict` - insert/delete an invoice-only paid flag without re-saving the invoice/PDF. Any linked PO reads that marker immediately, so it becomes Paid automatically (and returns to Invoiced when unmarked) without selecting the PO again.
- `is_invoice_paid(inv_no) -> bool`.
- `paid_invoice_set() -> set[str]` - all paid invoice numbers for cheap list annotation.

### po_fulfillment (Phase Y)

PO items marked delivered (from a DN submit) or paid (from an Invoice paid), item-level so a PO can be partially fulfilled and one DN/INV can span several POs.
- `set_po_fulfillment(po_no, item_indexes, source_kind, source_no, value=True) -> dict` - mark/unmark 0-based item indexes; `source_kind` `dn`→delivered, `inv`→paid (status derived, bad source rejected). Refuses an `inv` link while that invoice row has an empty `pdf_path` (an incomplete-price draft). Writes are `ON CONFLICT DO UPDATE`, so a retry is free - which is what **Make DN / Make Invoice from a PO** (2026-09-05, [[07-FRONTEND-SUPPORT]] `po_handoff.js`) relies on: it links after the save, keeps its `sourcePo` when the draft link is refused and retries on the next save. **The indexes are into the SAVED `rows_json`**, not the editor's rows - `savePO` persists `filledRows()`, so blank editor rows are dropped and the two indexes differ.
- `link_invoice_from_dn(dn_no, invoice_no) -> dict` (2026-07-18) - Make-Invoice helper used after the invoice record exists. In one DB transaction it reads every valid `delivered` index belonging to the DN across active POs and adds only currently-unpaid `paid` marks for the saved invoice. Same-invoice marks are idempotent; another invoice's mark is never overwritten and is returned in `conflicts`. Missing docs return `ok:false`; a DN with no active PO delivery marks returns `ok:true,no_po_links:true`. The helper stores no direct DN/INV relationship: Workflow still derives it from item-index overlap.
### backend/lpo_ack.py (2026-07-31) - counter-signing a client's LPO

Not `signing.py`: that bakes OUR signature into documents we RENDER, choosing placement itself. Here the document is the client's and already exists, so the user points at the spot in the viewer and we overlay exactly there.

- `write_acknowledged(src, dest, placements) -> {path, applied, requested}` - overlay pages are **rendered by the same headless Chrome** the app already uses, then merged with pypdf. A Chrome-rendered page paints no background, so the client's content shows through (verified, not assumed). **Returns counts** (2026-08-01): a mark can be dropped for a missing asset or a page index past the end, and silently writing fewer marks than asked is the failure you only notice when the client asks where the stamp is - `acknowledge_lpo` passes `applied`/`requested` to the UI, which refuses to say "done" when they differ.
- Placements are **fractions of the page (0-1) with y from the TOP** - the viewer's own coordinate space. Chrome lays out from the top-left too, so rendering the overlay at the same page size needs no conversion and no zoom level can corrupt it.
- `_overlay_html` uses `object-fit: contain`; **the viewer's `.lpo-mark-img` must use the same fit** or the preview lies about the merged result ([[07-FRONTEND-SUPPORT]]).
- A scanned page often carries `/Rotate`; it is baked into the content first (`transfer_rotation_to_content`) so the mediabox matches what the user actually saw.
- An image LPO is wrapped onto one A4 page first, so it acknowledges identically.
- The client's original is **never modified** - an acknowledgement they might dispute has to stay checkable against the file they sent.

### client_lpos (2026-07-31) - LPOs received FROM clients

The one document in the chain we do not produce. A received-document store, not a doc type.

- **PK `(client_name, lpo_no)`** - the number belongs to the CLIENT's sequence, so two clients can both send `PO-001`; keying on `lpo_no` alone collides. Both columns are `COLLATE NOCASE`.
- **`total_usd` is canonical**, `total` + `currency` keep what the client wrote - identical to the `invoices.grand_total` rule, so a dinar LPO can be summed beside a dollar one.
- `save_lpo(...)` never touches `file_path`/`ack_path` **or `closed_at`/`close_reason`**: those belong to the attach and acknowledge flows, so editing the header can't drop the document. `set_lpo_file(kind='file'|'ack')` owns them. Since 2026-09-11 it also stores `kind`, `rows` (as `rows_json`), `period_start`/`period_end`, `monthly_amount`, `erp_no`, `project` and `contact`. **`rows=None` stores NULL, `rows=[]` stores `[]`** - "never recorded" and "ordered nothing" are different statements. An unparseable `monthly_amount` becomes NULL, not 0: a zero monthly rate would read as a free rental.
- **`Api.save_lpo` refuses a client that is not on file** (2026-09-11), the same `_known_client_error` guard `save_quote`/`save_dn`/`save_inv` use - this was the last save that took any name. The grandfathering lookup (`load_lpo`) is wrapped in its own try: it runs BEFORE the method's try block, so an exception there would escape the bridge instead of returning an error the editor can show.
- `list_lpos(client_name='')` joins in `lpo_usage_map()` and returns `dns`, `invoices`, `invoiced_usd`, `remaining_usd`, `status`, and since 2026-10-03 **`next_step`** (every row) and **`items`** (materials rows: `lines`, `delivered`, `invoiced`, `part_delivered`, `complete` - counts of LINES, because EA and PACK cannot be added up).
- **`lpo_next_step(row, fulfil=None, today=None)`** (2026-10-03) - ONE rule for "what to do next on this LPO", read by the list's Next step column and the LPO screen's primary button. Returns `{key, text, action[, period_start]}`; `action` is true for `invoice`, `deliver`, `invoice_period`, `attach`. Order: closed by hand -> materials (money first: something delivered and not invoiced, unless the value is already fully invoiced; then anything undelivered; no lines recorded falls back to the documents; then `not_sent` "N invoices not sent yet" or `done`) -> contracts by the MONTH ledger (2026-10-06): `invoice_period` "Invoice <oldest forgotten month>", then `not_sent` / `done` once every month of a fixed term is invoiced, then `this_month` "October not invoiced yet" (no alarm), then `ending` within `_ENDING_SOON_DAYS = 45` -> `attach` -> `ok`. Amounts no longer decide anything for a contract (the 2026-10-03 `end_date` step is gone). **DONE means everything invoiced AND every invoice sent, for every kind**; `list_lpos` sets `status = closed` on `done`, and `_lpo_status` no longer closes anything except by hand. Pinned by `tests/lpo_next_step.test.py` (27 tests).
- **`_fulfillment_docs(identities)`** (2026-10-03) - every ISSUED delivery note's and invoice's rows grouped by LPO identity, from one read of each table (`N/A` and drafts excluded, as before). `list_lpos` builds it once for all materials rows and runs `_fulfillment_from_rows` per LPO; `lpo_fulfillment` uses the same index, so the list and the LPO screen cannot disagree. `_items_rollup(fulfil)` turns the per-line result into the list's counts.
- **`lpo_usage_map()`** matches the free-text `dns.lpo_no` / `invoices.lpo_no` **per client**, case-insensitively, skipping `''`/`N/A` - every DN in the live Abraj DB currently carries `N/A`, so that filter is load-bearing, not defensive. `invoiced_usd` sums `invoices.grand_total`, which is USD for every invoice.
- **`_client_match(column, client_name)`** (2026-09-19) - the WHERE clause that matches every spelling of one client, built from `client_master.alias_keys_for` and compared with the `client_key(...)` SQL function (registered per query by `_client_key_fn`, not in `_connect`, which is the hottest primitive here). Used by `list_lpos`, `suggest_lpos`, `load_lpo`, `list_link_candidates` and `list_quote_numbers`. **These five were the last raw string comparisons in the app** - `_usage_key` below was taught the identity rule on 2026-09-11 but the queries feeding it were not, so the rollup had nothing to roll up. Reported 2026-09-19: an LPO filed under `HDEC` was invisible to an invoice for `Hyundai Engineering and Construction Co., Ltd`, and the picker answered "has no open LPOs on file". Measured on the live Eshraqat data at the time: `list_lpos('HDEC')` 1 row vs the legal name 0, `list_quote_numbers` 5 vs 4. A client with no record falls back to its own spelling; an unreadable client store falls back to `COLLATE NOCASE` rather than losing the screen.
- **The LPO MUTATIONS stay exact** (`set_lpo_file`, `close_lpo`, `delete_lpo`). Each is handed a row's own stored `client_name`, and "delete whichever row matches any spelling" is not a rule you want behind a delete.
- **`save_lpo` adopts the spelling an existing record already uses** for the same identity + number (2026-09-19). The table is keyed on `(client_name, lpo_no)`, so re-filing one purchase order under a second spelling forked it into two rows - and once the queries resolve the identity, the picker would offer the same number twice. Nothing rewrites a saved row's client name: renaming a client touches no document (`client_rename_isolation_backend`), which is exactly why identity is resolved at READ time and not by normalising what is stored.
- **`_usage_key(client, lpo_no)`** (2026-09-11) - the key an LPO and its documents meet on, now resolved through **`client_master.canonical`** rather than compared as typed. An LPO filed under `HDEC` and an invoice saved as `Hyundai Engineering` are one client, and the whole ladder depended on those two strings happening to match. A broken or empty client store falls back to the raw spelling and never raises: `list_lpos` runs on every visit to the screen.
- **`lpo_fulfillment(client, lpo_no)`** (2026-09-11) - per-line `ordered` / `delivered` / `invoiced` / `remaining`, plus `extra` (shipped but never ordered) and `complete`. **Lines are matched on the line's NAME alone (`_line_key`, read through `_link_primary`)**, corrected the same day against the real documents: the first version keyed on item plus description, and every line of CSSP-ADM-0010 came back "never ordered". The dimensions MOVE between columns - `QUT-MT-HDEC-029` keeps `1650*850*800` in `description`, while `DN-HDEC-046` and `INV-MT-HDEC-043` keep it inside `unit` as `EA The name goes through **`_link_primary`**, the identity rule this file already uses for fulfilment links, because the column depends on the document type: a materials document calls it `item`, an equipment one calls it `equipment` (QUT-EQ-HDEC-013 does) and its description `specs`. A second rule here would have meant equipment LPOs never matched anything.
1650*850*800` and leave `description` empty. **`_line_spec`** reads description AND unit together as a token SET (the columns disagree about order too) and is consulted only to tell apart two ordered lines that share a name. `remaining` is never negative while `delivered` still reports what was really shipped; `extra` names the line as the DOCUMENT wrote it. **`complete` is None when `rows_json` is NULL** - "never recorded" and "ordered nothing" are different answers. Bridge: `Api.lpo_fulfillment`.
- **LPO billing (months since 2026-10-06)** - `lpo_billing.months` is authoritative for `list_lpos` and `_lpo_billing_due`, so the screen and the To-Do count the same months: invoices per month, a month that ended with none is forgotten (see `## backend/lpo_billing.py`). The To-Do note reads "N months not invoiced, oldest June 2026"; one row per LPO; a contract closed by hand stops asking. `_lpo_months_unbilled`, `_lpo_invoice_months` and `_invoice_months_index` remain legacy calendar helpers for compatibility, not the production reminder ledger.
- **`_term_months(start, end)`** (2026-09-11) - how long a rental TERM runs, which is NOT the count of calendar months it touches. CSSP-RENT-0002 runs 8 June to 8 January: eight month names, but their PO says "the contract term is seven months", and the screen has to agree with the paper. A part month at the end still counts as one, because it is still billed.
- **`_lpo_kind(value)`** (2026-09-11) - `materials` | `rental` | `open`, falling back to `materials` for anything unreadable. **Five places decide what a kind means and they must agree**: `_lpo_status`, `lpo_is_over_value`, `_lpo_months_unbilled`, the row summary `lpoProgress`, and the picker's closed filter.
- `_lpo_status()` ladder: `received` -> `acknowledged` -> `delivered` -> `invoiced` -> `closed`. Highest reached wins, but **`closed` is per KIND since 2026-09-11**. A manual `closed_at` always wins. `materials` closes when invoiced value reaches `total_usd`; `rental` closes when `period_end` has passed **and nothing is left unbilled** (`months_unbilled`, supplied by `list_lpos`) - CSSP-RENT-0001's term ran out in August with five months never invoiced, and a bare date check had it reading "Closed" on the list while the To-Do said "5 months not invoiced"; a missing or unparseable end date is NOT a date in the past; **`open` never closes on its own**. The old single rule retired `CSSP-OPEN-0001` on its first invoice, because that PO's printed total (`USD 0.77`) is the unit price of a litre of diesel, not a ceiling.
- **`lpo_is_over_value(d)`** (2026-09-11) - the "Invoiced over LPO value" flag, carried on every `list_lpos` row as `over_value`. True only for a `materials` PO with `total_usd > 0`: an open contract has no ceiling and a total of 0 means *not recorded*, not *zero*. **The frontend must read this flag, never recompute the comparison** - recomputing in JS is how the open-contract bug comes back.
- **`close_lpo(client, lpo_no, closed=True, reason='')`** (2026-09-11) - retire a contract by hand or put it back. **Owned separately from `save_lpo`**, the same way `file_path`/`ack_path` are: editing a rental's monthly rate must not silently reopen a contract the client terminated. Reopening clears `close_reason` too, and ignores any reason passed on the way open - a running contract must carry no explanation for an ending. CSSP-RENT-0002 says the term "may be terminated earlier if necessary", and no computable rule will ever know that happened. Bridge: `Api.close_lpo`.
- `delete_lpo` forgets the RECORD only - the client's file stays on disk, same rule as `clear_signed_copy`.
- **`_RETIRED_DOC_ROOTS` (2026-08-27)** - restore skips a document folder the app no longer writes to (`LETTERS`). Without it, retiring a document type makes every backup taken before the retirement un-restorable: the unknown-root guard would refuse the whole archive over a folder nothing needs any more.
- Guarded by `tests/client_lpo_backend.test.py`.

- `list_invoiceable_dns(limit=300)` also returns **`client_signed`** (2026-07-30) so the invoice DN picker can say which delivery notes come with the client's returned copy - that copy is what the invoice actually attaches, so it belongs in the choice.
- **`dn_paid_set()` (2026-07-24)** - DNs whose work has actually been **paid for**, so the "Awaiting client signature" pill is pointless. Two routes, both counted: (a) an invoice created from the DN (`invoices.source_dn_no`) that appears in `paid_invoices`; (b) the DN's delivered PO items billed by an invoice that is in `paid_invoices`. **Gotcha:** a `po_fulfillment` row with `status_kind='paid'` and `source_kind='inv'` only means *invoiced* - real payment always comes from `paid_invoices`, so route (b) must join through it (an unpaid invoice must NOT silence the pill).
- **`is_attention_ignored(kind, doc_no, category)` (2026-07-24)** - single-row ignore check, so a dialog needn't pull the whole `attention_ignore_map()`. `_ATTENTION_CATEGORIES` gained **`client_sign`** = "this document will never get a signed copy back" (DN 2026-07-24, invoices 2026-08-27 - same category, different `kind`); it reuses the existing per-doc/per-category mute rather than adding a second ignore mechanism.
- **`signed_copies` (2026-07-24)** - `(kind, doc_no, path, attached_at)`, PK `(kind, doc_no)`; created by migration `2026-07-24_signed_copies`. Holds the **CLIENT's counter-signed scan** returned to us. **Not** our own digital signature - that is `doc_meta` key `'signing'`. Keep the two apart: the panel/editor `signed` flag means *we* signed it (drives the Copy guard), `client_signed` means *they* did (drives the DN pill). `_SIGNED_COPY_KINDS = {"dn","inv"}` - **both have UI since 2026-08-27** (the invoice editor got the DN's feature; one shared dialog, `static/signed_copy.js`). Accessors `set_signed_copy` / `get_signed_copy` (adds `filename` + `exists`, since the row can outlive a file the user moved) / `clear_signed_copy` (record only - never deletes the file) / `signed_copy_set(kind)`. Registered in `delete_document_record`'s satellite cleanup so deleting a DN can't orphan the row.
- **`invoices_referencing_dn(dn_no) -> list[str]` (2026-09-02)** - every invoice whose PDF should carry that delivery note's pages, by **either** link: `source_dn_no`, or a `dn_ref` marker inside `rows_json`. The SQL `rows_json LIKE '%<dn_no>%'` is only a cheap pre-filter - the number can appear in any free-text field, so each hit is re-checked in Python for a real `dn_ref` key before anything rewrites a document's PDF. Invoices with an empty `pdf_path` (an incomplete-price draft) are excluded: there is no file to refresh and re-rendering one raises. Consumed by `api._refresh_invoice_attachments` ([[02-BACKEND-CORE]]).
- `linked_source_set(source_kind) -> set[str]` - set of DN or invoice source numbers that currently mark at least one PO item, **plus those joined by a direct DN↔INV link** (`invoices.source_dn_no`, 2026-07-24) so an invoice made from a PO-less DN still shows the panel's `linked` badge and agrees with what the Workflow renders. Invalid source kinds return an empty set. Monthly DN/invoice API lists use it to derive their right-panel badge without duplicating link state.
- `po_fulfillment_detail(po_no) -> dict` - per-item `delivered`/`invoiced`/`paid` maps `{idx:{source_kind,source_no}}`. Every invoice link appears in `invoiced`; only links whose invoice exists in `paid_invoices` appear in `paid`. **Note the mismatch worth knowing:** an invoice link is STORED with `status_kind='paid'` (legacy) but reported here under `invoiced` - a picker reading `.paid` would show every unpaid-but-invoiced line as still available. Links whose invoice has no PDF are filtered out entirely, so a draft never makes a line look invoiced. `database.load_po` does NOT carry this; `api.load_po` bolts it on.
- `po_fulfillment_map() -> {po_no:{delivered,invoiced,paid}}` - none/partial/full per axis (PO-list pills; only POs with links). PO Paid is derived, never created merely by linking.
- `pos_awaiting_invoice(year=None, month=None) -> [{kind:"po", doc_no, client, date, sub_type:"", age_days}]` (2026-07-17; **all months by default since 2026-08-10**) - POs with ≥1 **client line not yet invoiced**. Pass `year`+`month` to scope one month deliberately. A line is a client line when its `company` is non-blank and NOT `config.po_company_is_internal(name)` (untagged ⇒ client); "invoiced" = that item index has an invoice link (stored under the legacy internal `status_kind='paid'`). Office/blank lines ignored; **removal trigger is the invoice link, not invoice payment - a DN (`delivered`) does NOT clear it.** Feeds the To-Do `deliver` bucket ([[07-FRONTEND-SUPPORT]]).
- **DN costing + invoice-from-DNs (2026-07-18):** `dn_cost_summary(dn_no) -> {ok, sell_total, cost_total, cost_total_usd, has_costs, currency, iqd_per_usd, has_prices}` sums Σ qty·rate and Σ qty·cost (using the `cost_qty`/`cost_sell`/`cost` costing overrides when set).
  - **Every cost is read in the currency it was TYPED in (2026-08-25).** Each ROW has carried its own `cost_ccy` since 2026-08-06, and this function summed the raw numbers - dinars added to dollars. `DN-TDC-032` reported **856,961.40** against $703.60 of revenue; it really cost **$622.11**. **23 of 63 delivery notes were wrong**, and the figure feeds the invoice DN picker, so picking one dropped a six-figure "cost" onto an invoice line. It now converts per row through `cost_to_usd(cost, row_cost_currency(row, doc_ccy), peg)`, exactly as `_line_econ` does, and the test asserts the two agree on the same DN.
  - `cost_total` stays in the DN's **document** cost currency - that is what every caller reconciles from (`reconcileImportedCostCurrency`) - with the dollar figure alongside as **`cost_total_usd`**. **`has_costs`** distinguishes "nothing was ever entered" from "it is free"; a total of 0 cannot tell them apart, and the invoice line must be left EMPTY for the first ([[06-FRONTEND-EDITORS]]).
  - `sell_total` is still plain Σ qty·rate and does NOT go through `_detect_unit_econ`, so on 2 of 63 DNs (`DN-TDC-031`, `DN-TDC-020`) it differs from the costing engine's revenue. Left alone deliberately: changing it would move what the picker offers as the invoice rate. `list_invoiceable_dns(limit=300)` = `list_dn_candidates` filtered to priced DNs, each carrying `sell_total`/`cost_total` - feeds the invoice item-field DN picker. `link_invoice_dn_refs(invoice_no, items)` runs `link_invoice_from_dn` for every `dn_ref` line so a DN-line invoice closes its POs' Deliver-&-Invoice loop (called by `Api.save_inv`). **DN cost storage is the EXISTING `save_doc_costs`/`load_doc_costing` with `kind="dn"`** (`_DOC_COST_SPEC` already includes dn) - no new table. See [[04-BACKEND-DOCGEN]] (appended DN pages) + [[06-FRONTEND-EDITORS]] (DN Costs panel, invoice DN-line).
- `seed_po_company_tags() -> {name: "client"}` (2026-07-17) - one-time: if `settings.json.po_company_tags` is absent/empty, populate it with every distinct company name across saved POs, all `client` (user re-tags office names in PO Settings). Idempotent; also sets `config.PO_COMPANY_TAGS` in-memory. Called lazily by `Api.get_po_company_tags`. **2026-10-21 - ONE copy:** the map lives in `purchase_order.company_tags` (what PO Settings edits and config reads); `database.read_po_company_tags(settings)` / `write_po_company_tags(settings, tags)` are the only reader/writer, and the writer drops the old top-level `po_company_tags`. The PO editor's prompt used to read/write that old copy and REPLACE the live map with it - on Abraj it still had ابراج البدر as client, so one prompt answer put every our-company PO in the To-Do. Cancelling the prompt now saves nothing (it used to save "client", filing typos like v/j/ت as clients). Pinned by `tests/po_company_tags_one_copy.test.py`.
- `po_links_for_doc(source_kind, source_no) -> [po_no]`, `list_po_candidates(limit=300)` - reverse lookup + picker list.
- `document_workflow(kind, doc_no) -> {ok,current,linked,lanes}` (2026-07-17, extended 2026-07-18) - read-only workflow graph for saved PO/DN/Invoice editors. Each lane is one `{po,dns,invoices}` relationship derived from `po_fulfillment`; repeated item marks deduplicate into one node, a source spanning multiple POs appears in each correct lane, stale source/PO rows whose saved document was deleted are skipped, and an unlinked DN/Invoice returns an actionable lane with `po:null` plus the current document. **Direct DN↔INV link (2026-07-24):** `po_fulfillment` is no longer the *only* relationship source - `invoices.source_dn_no` is also read, so an invoice made from a DN with no PO shows that DN (and the DN's own workflow shows its invoices). These appear in the `po:null` lane, are filtered against documents a PO lane already shows so nothing duplicates, and set `linked:true`. PO lanes are unchanged. Base nodes carry `kind/no/date/client/item_count/current`. Within each PO lane, invoice nodes additionally carry `delivery_notes:[{no,item_count}]` for every DN whose delivered item-index set intersects that invoice's paid set, plus `direct_item_count` for paid indexes with no active delivered source. This derived overlap lets one invoice consume multiple DNs without a second relationship table and stays lane-local when documents span several POs.
- `clear_po_fulfillment_link(po_no, source_kind, source_no) -> {ok,po_no,source_kind,source_no,removed}` (2026-07-18) - exact relationship delete across all matching item indexes. It preserves every other source and PO lane, plus `submitted_docs`, `paid_invoices`, and signing metadata. Replacement links the new source/PO first and calls this only from the successful picker callback.
- `_doc_candidates(table, no_col, date_col, name_keys, limit)` + `list_dn_candidates(limit=300)` / `list_inv_candidates(limit=300)` - **Phase Y.7** recent DNs / Invoices for the PO-side link pickers; each `{no, doc_date, year, month, item_count, summary, search}` (summary = first 3 item names for DISPLAY). Mirrors `list_po_candidates`.
- `_candidate_search_blob(no, items)` (2026-07-18) - the picker **search haystack**: lowercased doc number + EVERY non-image, non-cost text value across ALL item rows. Every link-picker candidate builder (`list_po_candidates`, `_doc_candidates`, `suggest_link_candidates`, `list_linked_dn_candidates`) now returns a `search` field from it. **Fixes the bug where searching an item/company beyond the 3-item `summary` found nothing** - `summary` is display-only; the frontend `po_link.js` searches `row.search`. Gotcha: `summary` and `search` are DIFFERENT (short-display vs full-search) - keep both when adding a candidate source, or its picker search silently narrows to number+first-3-items. (Candidate lists are still capped at `limit=300` most-recent; a doc older than that isn't fetched and so can't be searched here - separate from this fix.)
- **Smart matching (2026-07-17):** `_score_link_rows(source_rows, source_date, candidate_rows, candidate_date)` counts every row with printable user content (excluding costing metadata and embedded image data), including documents whose content lives in an added `col_*` column. `_link_primary` separately chooses the best display/matching identity (`item|equipment|role|name|description`, then a custom user cell, then company/remarks) and deliberately does not present qty/rate/total as an item summary. Count distance dominates (`0→300`, `1→200`, `2→100`, larger = ineligible); an exact sorted positive-quantity multiset adds 40, close totals add at most 20, and date proximity adds at most 9. `suggest_link_candidates(source_kind, source_no, target_kind, limit=5)` applies it to the four PO↔DN/INV picker directions, excludes already-linked targets, and returns stable score/date/no ordering with human-readable reasons. Malformed row JSON degrades to no suggestion.
- **Invoice via linked DN (2026-07-17):** `list_linked_dn_candidates(invoice_no, limit=300)` lists only DNs present as `source_kind='dn'` in `po_fulfillment`, annotating linked-PO count + invoice match score. `linked_dn_invoice_detail(dn_no, invoice_no)` groups that DN's exact delivered item indexes by PO, loads the current PO rows, identifies any existing `paid`/invoice source per index, and returns only un-invoiced indexes as `proposed_indexes`. It never guesses PO rows from item text and does not write; the confirmation UI writes through `set_po_fulfillment`.
- `search_documents(query, limit=24)` - **dashboard search across DB-backed docs**: matches document number, client, AND item content (each doc's `rows_json`). Returns a unified most-recent-first list `{doc, no, client, when, path, snippet}` where `snippet` (via `_match_item_snippet`) names the first matching item so the UI can show WHY a doc surfaced. Scoped to the active profile DB; file-only docs with no DB row aren't item-searchable. Exposed as `Api.search_documents` ([[02-BACKEND-CORE]]).
  - **Embedded-image data is NOT searchable** (2026-07-17): an image cell stores a base64 data URI (`data:image/…;base64,…`) inside `rows_json`, and the SQL `rows_json LIKE` would otherwise match arbitrary substrings of that blob. `_match_item_snippet` skips any value whose lstripped prefix is `data:image`, and the main loop **drops a candidate whose only hit was the image blob** (query absent from number AND client AND `snippet is None`). So a query that only appears inside base64 returns nothing; real number/client/text-item matches are unaffected. Guard: `_match_item_snippet` returning None is now also the "image-only false positive" signal - keep the loop's drop condition in sync if you change the snippet contract.
- Cleanup lives in `delete_document_record`: deleting the PO drops all its rows; deleting the source DN/INV drops only rows it created (items revert). Rename migrations do NOT re-key it (same caveat as the other `(kind, doc_no)` satellites).

### doc_cost_meta + currency (Phase R)

- `IQD_PER_USD = config.IQD_PER_USD` - the **profile** peg (Abraj 1400, Eshraqat 1500); `CASH_IQD_PER_USD = config.CASH_IQD_PER_USD` (1500) - the market rate. `_COST_CURRENCIES = {"USD","IQD"}`. These are module-level snapshots of `config` taken at import - they stay correct across profile switches ONLY because `Api._reload_profile_runtime` reloads `database` right after `config` (see gotchas).
- `get_cost_currency(kind, doc_no) -> str` - defaults `"USD"` when no row.
- `set_cost_currency(kind, doc_no, currency) -> dict` - **`"USD"` DELETEs the row** (absent = USD default); validates `kind` and currency.
- `money_to_usd(value, currency, rate=None) -> float` (2026-07-29) - `value / rate` when IQD, else `value`; `rate` defaults to the profile peg. **Always pass the document's rate** from `doc_iqd_per_usd`. `cost_to_usd(...)` is a thin alias kept for the cost-entry callers.
- `get_inv_sell_currency(inv_no) -> str` (2026-07-29) - the currency an invoice was **issued** in. **Not the same thing as `get_cost_currency`:** cost currency is private book-keeping, sell currency is what the client sees on the PDF. Returns `"USD"` for a missing invoice and for a pre-migration DB (catches `OperationalError`).

### Invoice sell currency - IQD invoicing (2026-07-29)

Invoices can be issued in Iraqi Dinar. Two columns carry it, added by migration `2026-07-29_inv_sell_currency`:

| column | meaning |
| --- | --- |
| `invoices.sell_currency` | `'USD'` (default) or `'IQD'` - what the client is billed in |
| `invoices.sell_total` | the grand total **in that currency** - the figure printed on the PDF |
| `invoices.grand_total` | **ALWAYS USD**, converted at the document's peg |
| `invoices.sell_iqd_per_usd` | 2026-08-04 - the rate it was **actually** converted at. `NULL` = not recorded |

- **`grand_total` must never hold dinars.** `analytics._grouped_counts`, the `inv_by_cat` query and `clients.client_list` all reduce it with a plain SQL `SUM`, which cannot convert per row - a single IQD row there inflates every revenue figure by ~1400x. The migration backfills `sell_total` from `grand_total` (existing rows are all USD).
- `save_inv_record(..., sell_currency="USD", sell_total=None)` - `sell_total=None` defaults to `grand_total`, which is the correct USD case. An unknown currency string falls back to `USD`.
- **The USD figure is frozen at save time**, unlike costs (converted on every read). `api.save_inv` resolves the peg itself - `CASH_IQD_PER_USD` when the bank key is `cash`, else `doc_iqd_per_usd` - because a brand-new invoice is not yet in the table for `_cash_deal_index` to see.
- **A line with no cost is ESTIMATED, not free (2026-10-21, Waleed).** A billable line with no cost entered is costed at `config.UNCOSTED_MARKUP` (0.40 on cost): `est_cost = revenue / 1.4`, so $9,000 uncosted reads ~$6,430 cost and ~$2,570 profit instead of $9,000 profit (INV-SV-JUNE-059). It lives in `_line_econ`, so the Costing payload (`totals.estimated_cost`), `profit_summary`/`profit_trend`, Insights (`_doc_econ` `estimated`, overview `totals.estimated_spent` + `estimate_markup_pct`, item `estimated`) and the monthly report all get it. `has_cost` stays False - the Uncosted flag, the To-Do and `_rows_fully_costed` are unchanged, so the real cost is still asked for. A typed cost (even 0) is never replaced. Mirrored in JS `docLineEcon` (markup from profile metadata `uncosted_markup`). On 2026-10-21 this moved Abraj's 12-month profit 128,488 -> 110,525. Pinned by `tests/uncosted_estimate.test.py`.
- `_line_econ(row, pk, currency, rate, sell_currency)` converts the **seeded** per-unit sell when the doc is IQD. A `cost_sell` override is always USD (it is typed on the costing screen, which is USD end-to-end). `_doc_costing_payload` resolves `sell_currency` once and returns it in the payload; every number in that payload stays USD.
- **Each ROW carries its own cost currency (2026-08-06)** - `row_cost_currency(row, doc_currency)`, read by `_line_econ`. One table can price one line in dollars and the next in dinars and the profit maths still adds up. A row with no `cost_ccy` inherits the document's currency, which is every row saved before this; an unrecognised value falls back rather than guessing, because guessing wrong divides a dollar cost by 1400. Mirrored in JS as `rowCostCurrency` ([[05-FRONTEND-SHELL]]) and pinned by six fixtures in `tests/doc_econ_parity.test.py`.
- **`_data_sig` ignores per-use values (2026-08-06, `_SIG_IGNORE`)** - `qty`, `rate`, `cost`, `cost_usd`, `cost_ccy`, `cost_qty`, `cost_sell`, `total`, `image_refs`, `money_ccy`, `price_ccy` - the last two are Master Data DISPLAY units (cost and price, independent of each other), never part of what an item IS. Including `rate` is what turned ONE item into eighteen autocomplete entries: every re-quote at a different price minted another "variant", and 239 of 409 remembered names were affected. An item's identity is descriptive (description, unit, specs); its price and cost are what you want RECALLED, so a newer save must update the row rather than sit beside it.
  - **`shared_library._data_sig` is a second copy** used when copying catalogs between profiles, with its own `_SIG_IGNORE`. **The two MUST agree** or a copy re-splits every item it touches; `tests/quote_cost_memory.test.py` compares them directly.
    - **They had silently drifted (fixed 2026-08-16).** `img_query` was added to `database._SIG_IGNORE` on 2026-08-14 and never mirrored here, so copying a profile would have re-split every item that had ever been given a picture-search phrase. Both copies now also carry `name_ar` / `name_en`, and `tests/catalog_keeps_costs.test.py` asserts set equality rather than checking for particular keys - the drift happened because nothing compared the whole set.
  - **`data_manager._data_sig` did NOT agree (fixed 2026-08-13).** Its docstring claimed the same algorithm, but it applied no ignore set at all - it hashed `cost_usd`, `image_refs`, `qty`, `rate` and everything else. So an item edited in Master Data got an identity computed by one rule and was re-saved from an editor under another, re-splitting the rows that screen exists to unify. It now imports `database._SIG_IGNORE` (lazily, via `_sig_ignore()`) rather than keeping a fifth divergent copy; `tests/master_data_backend.test.py` compares the two directly.
- **Item autocomplete searches ONE pool and matches on WORDS (2026-08-16).** `_suggest_across(query, limit, home_table, home_sub_col, home_sub)` backs all four of `suggest_items` / `suggest_quote_items` / `suggest_dn_items` / `suggest_inv_items`; their signatures and return shape are unchanged, so **no frontend edit was needed**.
  - **The bug, both halves.** Reported as "I tried to quote a Traffic Cone which I have invoiced before but it didn't show up". Each dropdown read only its own catalog, so an item delivered and invoiced but never quoted could not be offered by a quotation - `Traffic Cone` lived in `dn_items` and `inv_items` only. And each matched `name LIKE ? || '%'`, so typing "cone" never reached it. The sub-type filters were narrower still: `quote_type = ?` hid an item quoted as equipment from a materials quotation, and `inv_type = ?` did the same to invoices. The catalog Master Data presents as one thing was thirteen lists to the editors.
  - **Rank, don't filter.** Every catalog is searched; the caller's own table *and sub-type* score first, so the familiar item is still the first suggestion. `_match_score` grades exact → prefix → substring → whole-word hits → partial-word hits, and returns 0 for no hit at all, so an empty or unrelated query still lists nothing.
  - **One item, ONE suggestion, in the document's own language (2026-08-16, step 3b).** A PO is written in Arabic and every other document in English, so a paired item used to offer BOTH names in the same dropdown. Two results carrying the same `master_items` id are the same product (`master_items.record_ids_for`), and the one whose name is already in this document's language wins - the WHOLE ROW, so a PO gets كيلو and an invoice KG instead of the two being mixed. An item with a name in only one language is still offered in that language, never hidden.
  - **The paired name is a way IN.** Writing an Arabic PO you may only know the English word, so the SQL prefilter also searches `data_json` (where `name_ar`/`name_en` live) and the score considers the paired name at 0.9x. That factor is a preference, **not** a guarantee: when both names belong to the same record the language grouping decides before score does, so no test can pin it - 5 of 6 mutations are caught, and this is the sixth.
  - Cost: the `data_json LIKE` scan roughly tripled a lookup, ~3.5 ms → ~10 ms per keystroke on 1,300 rows. Fine for one desktop user; revisit if the catalog grows an order of magnitude.
- **Payload is filtered when it crosses catalogs** (`_CROSS_CATALOG_KEYS`): description/unit/item_no/rate/costs/prices/currencies/images/names travel; a quotation's `col_4`, an equipment row's `specs` and per-use `qty` do not, because autofilling them into another document type puts junk in. Within its OWN catalog a row's payload passes untouched - that is what autofill is for.
  - **Collapse is on item identity** (name + description + unit), not on the payload hash, so `Traffic Cone` found in two catalogs is offered once while Hemp rope φ12/φ15/φ20 stay three.
  - Uses `try/finally` + `conn.close()`, not `with conn` - on a sqlite3 connection `with` opens a transaction and closes nothing, and this runs four reads per keystroke. (The 11 `remember_*`/`suggest_*` sites still using `with _connect_shared()` leak the same way; pre-existing.)
  - Pinned by `tests/item_suggestions_universal.test.py` (16 tests, mutation-checked 7/7: home-only search, prefix-only match, rejecting partial hits, unfiltered cross payload, name-only collapse, empty-query listing, and losing the sub-type tiebreak each fail it).
- **ONE row per name in every dropdown.** `_SIG_IGNORE` stops new duplicates being created, but the ones already stored still have to be collapsed at read time, and the winner is the most USEFUL entry rather than merely the first: one carrying a cost outranks one without (recalling the cost is the point of picking it), then `image_refs`, then use count, then recency. That rule now lives inside `_suggest_across`. **`_one_per_name` was deleted on 2026-08-16** - rewiring the four dropdowns left it with no callers, and a helper kept "just in case" is a second, drifting copy of the collapse rule.
  The collapse was applied to quotations only for a week, which is why "Celery" still offered **five** identical-looking DN entries (282 of 370 dn_items names, 25 of 224 inv_items names were in the same state). **It is a display rule - nothing is deleted**, because the losing rows carry pictures and costs of their own.
- **Sticky details: `_STICKY_MEMORY` (2026-08-13, widened 2026-08-16).** The `remember_*_item` upserts replace `data_json` wholesale, so re-saving a document with the cost cell blank used to wipe a cost the item had already taught us. `_merge_sticky(conn, table, where, params, data)` carries the sticky keys over from the stored row when the incoming payload is silent about them - and is keyed on the **full PK including `data_sig`**, so a different spec never inherits another variant's cost.
  - **The list had stopped covering costs.** It named only `cost_usd`, from when a cost was always a dollar figure; costs are now stored AS TYPED in `cost_native` + `cost_native_ccy` and that pair was never added, so the very protection this exists for no longer applied to the field costs live in. Found when 119 costs imported into the catalog on 2026-08-16 turned out to be one blank-cost delivery note away from erasure each, silently. Now: `cost_usd`, `cost_native`, `cost_native_ccy`, `price_native`, `price_native_ccy`, `money_ccy`, `price_ccy`, `image_refs`, `name_ar`, `name_en`. Pinned by `tests/catalog_keeps_costs.test.py` (mutation-checked 4/4 - dropping the pair, dropping only the currency, dropping the price pair, or letting sticky override an explicit new value each fail it).
  - **Sticky means "keep what the save OMITS", never "refuse what the save STATES"** - the guard is `if not str(out.get(k) or "").strip() and prev.get(k)`. Invert it and a corrected cost could never be entered from a document again.
- **Bilingual item names (2026-08-16).** `name_ar` / `name_en` hold the item's OTHER-language label in `data_json`: a PO is written in Arabic and the delivery note fulfilling it in English, so بصل and `Onion` are one item with two names. The label is in **both** `_SIG_IGNORE` (adding or correcting a name must not fork the item) and `_STICKY_MEMORY` (no document payload ever carries it, so without stickiness the first save after pairing would wipe it). 38 pairs were harvested from `po_fulfillment` and written on 2026-08-16; the design and the remaining orphans are in [[09-DATA-MODEL]].
### backend/item_images.py - remembered item pictures (2026-08-05)

Autocomplete restores an item's photo as well as its price and cost. The bytes
do **not** go in the database: keeping base64 in `quote_items.data_json` was
tried and removed once already, because an equipment quote saved 200KB+ per row
and the shared library ballooned. So pictures are content-addressed on disk and
the item remembers a short ref.

- `store_dir()` → `data/shared/item_images`, beside `library.db`, because the
  item memory it belongs to is shared across company profiles.
- `store(data_uri) -> ref | None` - writes `<sha256>.<ext>`. **Dedup is
  automatic**: the hash IS the name, so one photo on fifty items is one file,
  and re-saving a quote writes nothing new. Caps at `MAX_BYTES` (4MB) so a
  single bad paste cannot fill the disk; refuses anything that is not a
  recognised image data URI.
- `load(ref) -> data_uri | ""` - returns `""` for missing/invalid rather than
  raising: a photo deleted off disk must degrade to "no picture", never to a
  broken autocomplete.
- `path_for(ref)` / `_safe_name(ref)` - **the security boundary.** A ref comes
  back OUT of the database and becomes a file path that is read and handed to
  the UI. `_safe_name` accepts a bare 64-char hex digest plus a known extension
  and nothing else; `path_for` then re-confirms the resolved path sits inside
  the store. The second check is unreachable while the first holds, which is
  exactly why `tests/item_images_backend.test.py` bypasses `_safe_name` to test
  it directly - otherwise deleting it passes every other test.
- `store_size() -> {files, bytes}` - surfaced by `Api.item_image_store_size`.
- **No cleanup yet.** Nothing removes a picture when the last item referencing
  it is deleted, so the store only grows. Deduped and capped it grows slowly,
  but a maintenance action to sweep unreferenced files is still owed.
- `Api.get_item_image(ref)` is the bridge method (the front end runs over
  `file://` and cannot read a local file itself - the same constraint
  `get_lottie` works around). **New method, so an app RESTART is required**, and
  quote.js feature-detects it before calling.

- **The rate a document's COSTS convert at comes from the ACCOUNT the client is paid into (2026-09-21).** `doc_iqd_per_usd(kind, doc_no)`, in order: a rate **pinned by hand** on the Costing screen; `invoices.sell_iqd_per_usd` for an invoice that recorded one (invoices only, and it must stay above the stamp - one `rate` converts the SELL side too in `_doc_costing_payload`, and the printed `grand_total` was computed from it); the **stamp** written when the document was saved; else `payment_accounts.fallback_rate()`. **Two rules were DELETED**: "Eshraqat is always 1500" and "a deal paid in cash is 1500". Both produced a number on screen with no way to see why; cash is now a payment method with a rate you can read and edit in Settings > Payments. `backend/payment_accounts.py` owns which account a document belongs to - an invoice's own `payment_bank_key` wins, else the client's `defaults.bank_key` from Master Data, resolved through the alias index. Pinned by `tests/payment_account_rate.test.py`.
- **`config._apply_user_settings` REBUILDS each `bank_info` entry from a whitelist**, so a key missing from that list is silently discarded on load however correctly it was saved. `iqd_per_usd` is in it since 2026-09-21, and `invoice.fallback_iqd_per_usd` -> `INV_FALLBACK_IQD_PER_USD` is read beside it and surfaced by `current_effective_settings` so the Payments tab can prefill its box. **This is how the feature shipped broken:** the rate reached settings.json and was thrown away on the way back, so the tab reloaded blank and `payment_accounts` never saw it. Every unit test passed because they set `payment_accounts._accounts_override`, which bypasses config entirely. `TheRateSurvivesTheSettingsFile` in `tests/payment_account_rate.test.py` goes through the FILE for that reason - add a settings key and add a case there.
- **`doc_cost_meta.account_key` / `account_rate`** - the stamp, written by `set_account_stamp` from all three save paths. Re-saving re-stamps, which is correct: that is a re-issue against whatever account the client is paid into now. It touches only its own two columns; `rate` and `currency` are independent facts about the same costs.
- **Why the identity is resolved at READ time and never by normalising what is stored:** renaming a client rewrites no saved document (`client_rename_isolation_backend`), and documents legitimately carry different spellings because the legal name is what some of them print.
- **The migration is the dangerous part, and it ran first.** `2026-09-21_stamp_document_cost_rates` stamped 171 Abraj and 16 Eshraqat documents at the rate the OLD rules gave them, reimplementing those rules inside the migration because `database.py` no longer holds them. `tests/rates_did_not_move.test.py` compares every document against `scratchpad/rates-before.json`, taken while they were still live: **no rate and no profit figure moved.** Remove the stamp lookup and 117 rates and 51 profit figures move, which is the damage it prevents. It also has to honour a hand-pinned rate: the first run stamped the profile peg underneath the pins on QUT-EQ-HDEC-048 and QUT-MT-TDC-047, which looks right only until the pin is cleared.
- **A document can PIN its cost exchange rate- **A document can PIN its cost exchange rate (2026-08-05, migration `2026-08-05_doc_cost_rate`)** - `doc_cost_meta.rate`, via `get_cost_rate(kind, doc_no)` / `set_cost_rate(kind, doc_no, rate)`. The editor showed the rate it was converting costs at ("Costs entered in IQD, converted at 1 $ = 1,400 IQD") with no way to correct it, while the document's whole profit figure hung off that number. `doc_iqd_per_usd` now returns a pinned rate **before** every other rule.
  - `NULL` = not pinned, which is every document that exists today, so **no historical profit figure moves**.
  - **`set_cost_currency` DELETEs the row for USD**, and the rate lives in that row - so it now keeps the row when a rate is pinned. Without that, flipping IQD → USD → IQD silently discarded a rate the user had set.
  - Distinct from `invoices.sell_iqd_per_usd`: that is what the CLIENT is billed at, this is what the costs convert at. A deal can legitimately use different rates for each.
  - The column is in the base `_SCHEMA` as well as the migration - a new profile database never runs migrations.
- **The RECORDED rate (2026-08-04, migration `2026-08-04_inv_sell_peg`) beats every derived rule.** The market rate moves, so the peg that was right when an invoice was issued is not the one a later lookup returns - and `grand_total` is computed from it, so an invoice's dollar value was quietly floating with a rule. The invoice editor now asks for the rate at the moment it flips `$ → IQD` ([[06-FRONTEND-EDITORS]]) and `api.save_inv` stores it. `_stored_inv_peg(inv_no)` reads it back and `doc_iqd_per_usd` returns it **before** the Eshraqat and cash checks: it is what the printed dinar figures were produced from, so anything else disagrees with the PDF.
  - `NULL` means *not recorded* - every invoice issued before the column existed. Those keep falling back to the rules below, so **no historical figure moves**. Only USD invoices and pre-2026-08-04 rows are NULL.
  - `_stored_inv_peg` rejects `0`, negatives and non-numeric junk, returning `None`. SQLite is dynamically typed and the value is used as a **divisor** in `money_to_usd`; a stored `0` would otherwise make an invoice worth infinity.
  - The UPSERT in `save_inv_record` uses `COALESCE(excluded.sell_iqd_per_usd, invoices.sell_iqd_per_usd)` - passing `None` means "not supplied" and must not erase a rate an earlier save recorded.
  - **The column is in the base `_SCHEMA` as well as the migration.** Migrations only touch databases that already exist; a new profile is built from `_SCHEMA` and never sees them. Adding it to the migration alone broke the first invoice save on every fresh database (`tests/inv_exchange_rate.test.py::FreshDatabase` pins both halves).
- **`doc_iqd_per_usd(kind, doc_no) -> float`** - the peg for ONE document. A recorded invoice rate wins (above); otherwise Eshraqat always uses the market rate (1500), and in **any** profile a deal paid in **cash** does too, and the rule reaches the **WHOLE deal** - a cash invoice re-pegs its root quotation and that quote's DNs, so one deal converts at one rate end-to-end. No cash invoice in the deal → profile peg. Deliberately a light SQL lookup (`_cash_deal_index` + `_deal_root_quote`), **never** via `deal_flow` (which calls `load_doc_costing` → this; that would recurse).
- `_cash_deal_index(conn) -> (cash_quote_roots, cash_inv_nos)` - resolves every cash deal in one query; used per-doc and in bulk by `_costed_catalog`.

### doc_columns (Phase T)

- `get_doc_columns(kind, doc_no) -> list[dict] | None` - None ⇒ document uses its type's columns.
- `set_doc_columns(kind, doc_no, columns) -> dict` - strips to lightweight shape fields (`key, head, kind, width, align, default`) - never stores image data or values; empty ⇒ DELETE.

### doc_meta layout

- `get_doc_layout(doc_type, doc_no) -> dict | None` / `set_doc_layout(doc_type, doc_no, layout) -> dict` - shared `doc_meta` storage for per-document layout flags. Valid types: `po/qut/dn/inv`. Only `autofit_rows` is currently persisted, and explicit `false` is stored so one saved doc can override an enabled default.

### active document deletion

- `normalize_document_kind(kind) -> str` - accepts bridge/UI aliases (`qut`, `quote(s)`, `inv(oice/s)`, `dn(s)`, `po(s)`) and returns the canonical app kind.
- `document_pdf_path(kind, doc_no) -> str` - returns the active row's `pdf_path` without mutating anything; used before recycling a PDF.
- `deleted_document_numbers(kind) -> list[str]` - reads the `deleted_docs` tombstone ledger. Numbering modules use it so deleted document numbers are not reused.
- `delete_document_record(kind, doc_no, recycle_path="", pdf_path="") -> dict` - removes one active row from `pos/quotes/dns/invoices`; records a `deleted_docs` tombstone; cleans `submitted_docs`, `doc_cost_meta`, `doc_columns`, `attention_ignores`, invoice `paid_invoices`, and all `doc_meta` keys for document types that use them. Also reverts `po_fulfillment` (Phase Y): deleting a PO drops all its marks; deleting a DN/INV drops the marks it was the source of. Deleting a quote also NULLs `source_quote_no` on linked POs/DNs/INVs so saved references and deal flow do not point at a missing quote.

### column_memory (Phase G.14)

- `_VALID_DOC_TYPES = {"po","quote","dn","inv"}`.
- `remember_column_value(doc_type, sub_type, col_key, value)` - bump/insert one tuple.
- `suggest_column_values(doc_type, sub_type, col_key, prefix, limit=10)` - prefix search, `use_count DESC`.
- `remember_doc_columns(doc_type, sub_type, rows, columns)` - walks every row × column and remembers each text cell; **skips `kind ∈ {image, computed, index}`**.

### Costing / profit engine (Phase N.1 / N.4–N.7 / R / V / X.5)

Cost rides **per-row inside `rows_json`** under keys `cost`, `cost_qty`, `cost_sell` (`_COST_FIELDS`); profit is **always computed on read, never stored**.

- `_DOC_COST_SPEC` - `{kind: (table, pk_col, rows_col, sub_type_col)}` for quote/dn/inv.
- `_primary_key_for(kind, sub_type)` - resolves the row's anchor column via `config.QUOTE_TYPES`/`INV_TYPES` `primary_key` (DN always `"item"`).
- `_coerce_cost(v)` / `_num(v)` - parse messy cells to float (`_coerce_cost` returns `""` on blank, `_num` returns `None`); `_num` rejects non-finite results.
- `inherit_costs(old_rows, new_rows, primary_key)` - carries `_COST_FIELDS` from a prior save into a re-save keyed by primary value, so a normal editor re-save never wipes costing-screen data. **`_COST_FIELDS = ("cost", "cost_ccy", "cost_qty", "cost_sell")`** - `cost_ccy` joined it on 2026-08-13, because inheriting the number without its unit is worse than inheriting neither: a dinar cost carried into a re-save with no currency is then read as the document's, i.e. as dollars.
- `_index_map(seq)` - list-by-position or dict-by-str-index → int-keyed map.
- `save_doc_costs(kind, doc_no, costs, extra_costs, bid_status, source_quote_no, qtys, sells, currency, cost_ccys)` - patches ONLY costing fields (never the PDF); `bid_status` only for quote, `source_quote_no` only for dn/inv; qty/sell overrides stored only when they genuinely differ from the auto-detected value.
  - **`cost_ccys` (2026-08-26) - a cost is never written without its unit.** Aligns to the rows exactly as `costs` does (list by position / dict by stringified index) and carries the `USD`/`IQD` each number was typed in. Every row whose cost this writes gets a `cost_ccy`: from `cost_ccys` if it names a real currency, else from an explicit `currency` (the caller ASSERTING what unit its numbers are in), else the unit the row already reads as - which is what pass-through callers like `pull_costs_from_quote` rely on. Before this the number was written and the old stamp left in place, so a line stamped IQD by the DN editor and then re-costed in dollars on the Costing screen kept the IQD stamp and had its dollar figure divided by the peg.
- `set_bid_status(quote_no, status)` - open|won|lost.
- `_detect_unit_econ(row) -> (unit_sell, base_qty, auto)` - unit price + implied qty; precedence: explicit `total` → largest numeric `col_*` custom column (flagged `auto`) → `qty*rate` → flat-rate (rate, qty 1). Embedded `data:image/...` custom values are ignored.
- `_line_econ(row, pk, currency="USD")` - per-line USD economics honoring `cost_qty`/`cost_sell` overrides.
- `_rows_fully_costed(rows, pk)` / `_costed_from_json(rows_json, kind, sub_type)` - "fully costed" (req 3) = every billable line has a cost AND qty>0. Drives the "Uncosted" pill.
- `_doc_costing_payload(kind, d)` - shapes a loaded record into rows + `totals` (revenue/line_cost/extra_cost/cost/profit/margin, all USD). Each returned row carries a **RESOLVED `cost_ccy`** (2026-08-26) - `row_cost_currency(row, doc currency)`, never blank - because the Costing screen prints the raw `cost` number and has to know which unit it is in ([[07-FRONTEND-SUPPORT]]).
- `load_doc_costing(kind, doc_no)` - dispatches to load_*; for quote adds `has_invoice`/`effective_status`/`stale`.
- Bid staleness: `QUOTE_STALE_DAYS = 14` (auto-loss cutoff), `QUOTE_DECIDE_DAYS = 7` (surface for a decision), `_parse_doc_date`, `quote_is_stale`, `effective_bid_status(stored, has_invoice, quote_date, today)` - **Phase X.26: an invoice (or manual `won`) now wins over everything, incl. a stored `lost`**; then manual/auto `lost`; then open+stale ⇒ lost; else open. `auto_lose_stale_quotes(today)` PERSISTS the rule - stores `lost` for open, ≥14-day, un-invoiced quotes (idempotent; only touches stored `open`).
- `quote_has_invoice(quote_no)`, `_won_quote_nos(conn)` - invoice→quote linkage via `source_quote_no`.
- **`dashboard_needs_attention(year=None, month=None)`** - runs `auto_lose_stale_quotes()` first, then `{scoped, decide, uncosted, unsubmitted, unpaid, deliver, ignored}`. **Since 2026-08-10 every bucket spans ALL months by default**; `year`+`month` is an opt-in filter (the `scoped` flag echoes which mode ran). It used to month-scope `uncosted` / `unsubmitted` / `deliver`, so outstanding work rolled out of sight at the month boundary - measured on the live Abraj data the day it changed, standing on August showed 15 items and hid 55. **A bucket that waits on the OTHER PARTY only lists SUBMITTED documents** (2026-08-10, `_submitted_only(entries, submitted_by_kind)`): a draft nobody has been sent cannot be overdue or awaiting an invoice. `decide` always worked this way; `unpaid` and `deliver` now match it. **`uncosted` is exempt on purpose** - costing is internal and happens *before* a quotation goes out (the cost is how you price it), so gating it would drop the reminder exactly when it is most useful. Before this, an unissued PO appeared in BOTH `deliver` and `unsubmitted` - and led the list, because drafts are old and everything sorts oldest-first. **`unsubmitted` is the one bucket that is NOT gated** (gating it would hide the work entirely), so nothing disappears: a draft still shows exactly once, under the only action that applies to it. The gate matches on `(kind, doc_no)`, never the number alone. `decide` = SUBMITTED open quotes with `QUOTE_DECIDE_DAYS ≤ days_open < QUOTE_STALE_DAYS` + `days_left`, ranked by urgency; `uncosted` = QUT/INV lacking complete costs (not gated); `unsubmitted` = PO/QUT/DN/INV absent from `submitted_docs`; `unpaid` (Phase X.28) = submitted invoices absent from `paid_invoices`; `deliver` (2026-07-17) = submitted `pos_awaiting_invoice()`. `list_unsubmitted(year=None, month=None)` and `list_unpaid()` are the helpers. **`_oldest_first(entries)`** stamps every entry with `age_days` (`None` when the doc has no parseable date) and sorts oldest-first, undated last - age is what ranks a list that now spans years. **Phase X.30:** entries muted via `attention_ignores` are partitioned OUT of their bucket into an `ignored` list (each stamped with its `category` for Restore) - so counts exclude muted docs, and a muted doc that gets resolved drops out entirely. **`_ATTENTION_CATEGORIES`** (the ignore whitelist) now includes `"deliver"` - a new bucket that wants an Ignore button MUST be added there or `set_attention_ignore` rejects it as `bad category`. **The mute IS the escape hatch for old items the user will never action - do not "fix" a long list by re-introducing a date cap, that is the bug this replaced.**
- **`_stamp_attention_amounts(entries)` (2026-10-21)** - the To-Do table's money. Stamps `amount` + `currency` (the document's OWN currency: an IQD invoice reads `sell_total` in IQD) and `amount_usd` (sorting and tab totals) on every inv / quote / dn entry in `decide`, `uncosted`, `unsubmitted`, `unpaid` and `ignored`; an invoice reads its stored totals, a quotation or DN sums `_line_econ` revenue. POs and LPOs get nothing (no sell figure), and a document it cannot find is left without an amount rather than given a zero. One query per kind, only for the documents in the feed (~100 ms for the whole feed, live Abraj). `pos_awaiting_invoice` entries now also carry `open_lines` / `client_lines`. Pinned by `tests/todo_amounts_backend.test.py`.
- **`agenda_feed()`** (2026-10-20) - the launch card's agenda: `{ok, unpaid, deliver, decide, unsubmitted}` exactly as `dashboard_needs_attention` returns them (muted entries removed), but **READ-ONLY** and without the costly buckets (`uncosted` parses every document's rows, `billing` every LPO): ~40 ms against ~470 ms, live Abraj. It runs in the launch card's own process, so instead of running the 14-day sweep it skips the quotes the sweep WOULD mark lost (stored `open`, `>= QUOTE_STALE_DAYS`). Both functions build their buckets with the same helpers - `_attention_buckets(today, year, month, *, swept)` (decide + the submitted gate), `_decide_bucket(conn, today, *, swept)` and `_unmuted(entries, category, ig, ignored)` - so the card and the To-Do cannot disagree. The 2026-10-20 refactor that split these out was checked byte-identical against the old function on both companies, full and month-scoped; `tests/gate_backend.test.py` AgendaFeed compares the two on live data and pins that `agenda_feed` never sweeps.
- `list_docs_for_costing(year, month, kinds=None)` - cheap month listing with `costed`/`effective_status`/`stale` flags (does not return `rows_json`); default scope is QUT/INV, while explicit `["dn"]` still reads legacy DN cost data and supports deal-flow internals.
- Deal linking: `set_doc_source_quote(kind, doc_no, quote_no)` (dn/inv only, empty ⇒ NULL), `list_link_candidates(kind, client, limit=400)`, `list_quote_numbers(client, limit=300)`, `deal_flow(kind, doc_no)` (root quote + its DNs/INVs fan-out).
- **`client_quotes(client_name, limit=60)`** (2026-09-28) - one client's quotations with enough on each to CHOOSE between them: `quote_date`, `quote_type`, `lines`, `total`, `total_readable`. `list_quote_numbers` answers a datalist with bare strings, which is no help when the question is "which of HDEC's 41 quotations is this PO answering?". The total is the **sum of the lines** (there is no stored grand total on `quotes` - only `invoices` has one), and **`total_readable` is False when nothing could be summed**: an equipment quotation keeps its quantities in per-document custom columns (`col_2`..`col_6`), so printing `$0.00` beside a real quotation would read as free. Identity-aware through `_client_match`, so a PO typed under `HDEC` reaches the quotations saved under the legal name - the sixth query to join that list. A blank client returns `[]`, never every client's. Bridge: `Api.client_quotes`.
- `list_quote_refs(query, limit=800)` (~line 2981) - quotation reference-picker list: all quotes newest-first with a `type_code` (MT/EQ/MP/SV), optionally filtered by `query`.
- Cost memory: `_norm_name`, `_tokens`, `_all_cost_currencies(conn)`, `_costed_catalog()` (every previously-costed line, USD-normalized; ~line 3045 - also branches on the eshraqat profile for its IQD rate), `find_known_costs(items, limit_per=3)` (token-Jaccard fuzzy match, exact name ⇒ 1.0, containment ⇒ ≥0.7, threshold 0.3).
- Profit rollups: `profit_summary(year, month)` (realized=invoices, pipeline=OPEN quotes only), `_months_back`, `profit_trend(end_year, end_month, span=12)` (folds `profit_summary` per month), `_MONTH_ABBR`.

**Reads / writes** - Owns the per-profile DB `data/profiles/<ACTIVE_PROFILE>/po_tool.db` (every doc/satellite/costing table) AND reads/writes the shared catalog DB `data/shared/library.db` via `_connect_shared` (all autofill + column memory). Reads `config.DB_PATH`, the `*_ROOT` folders (existence fallbacks in `*_exists` - the roots are per-profile trees: `D:\PO TOOL` for abraj, `D:\ESHRAQAT DOCUMENTS` for eshraqat), `QUOTE_CLIENTS_SEED`, `QUOTE_TYPES`/`INV_TYPES`. No PDF writing.

**Calls / called by** - Called by the generators (save side), the API bridge, `migrations.run_all` (via `_connect`), `clients.py`/`analytics.py` (reuse `_quote_value`), and `data_manager.py`. Save/load/suggest functions are exposed to JS through `backend.api.Api` as `api().save_quote`, `api().suggest_items`, `api().set_submitted`, `api().save_doc_costs`, `api().dashboard_needs_attention`, `api().deal_flow`, etc. (thin wrappers - see [[02-BACKEND-CORE]]).

**Gotchas**
- **Never resolve a client name once per row (2026-10-01).** `_usage_key(client, lpo, identities)` takes the alias map; build it ONCE with `_identities()` and pass it down. Every LPO helper used to let `_usage_key` look it up itself, and `dashboard_needs_attention` opened **359** shared-database connections on one visit to the hub - 1.4 s of the two-second wait Waleed reported on going home. The same visit also rescanned every invoice once per LPO, twice over (`list_lpos` and `_lpo_billing_due` each asked `_lpo_invoice_months` per row); `_invoice_months_index(identities)` now scans once and `_lpo_invoice_months(row, index, identities)` looks up. Result: **1,400 ms -> 80 ms**, output byte-identical to the old code on both companies. `tests/db_version_cache.test.py` fails if the shared-connection count for one Needs Attention call climbs past 25.
- **`_use(conn)` - pass ONE connection down a read loop (2026-10-01).** `get_cost_currency`, `get_cost_rate`, `get_account_stamp`, `_stored_inv_peg`, `doc_iqd_per_usd` and `dn_cost_summary` take an optional `conn`; with none they open their own exactly as before. `list_invoiceable_dns` (the invoice editor's DN picker) asked four of them about every DN with a connection each - 314 connections for 78 DNs, 220 ms on the first keystroke - and now passes its one connection: **220 ms -> 17 ms**, identical output. The rate PRECEDENCE in `doc_iqd_per_usd` is untouched; only the plumbing changed.
- **Costing values must stay finite.** Custom equipment columns can contain image data URIs whose base64 digits look numeric after loose cleaning. Those values are skipped before fallback price detection, all parsed numbers are checked with `math.isfinite()`, and currency conversion rejects non-finite inputs so bridge responses never contain `NaN`/`Infinity`.
- **Every SQLite file opens through `sqlite_policy.open_db` (multi-user Phase 1, 2026-10-22).** WAL journal + `busy_timeout` 30 s on every connection, so several engines can share a company database: readers never block the writer, and a second writer WAITS instead of failing with "database is locked". `open_db(path, uri=, row_factory=True, foreign_keys=False, wal=True, busy_ms=30000)`; `wal=False` / a short `busy_ms` are for MAINTENANCE opens only (restore staging, migration lock - 250 ms on purpose - VACUUM, quick_check). `sqlite_policy.snapshot(src, dest)` is the consistent copy of a live WAL database (SQLite's backup API) - a plain file copy or zip of the `.db` can miss rows still in its `-wal`. `tests/sqlite_policy.test.py` fails on any raw `sqlite3.connect(` in `backend/`. WAL side files (`*.db-wal`, `*.db-shm`) are git-ignored; checkpoint before a data commit (see [[11-RUN-AND-MAINTAIN]]).
- **Every NEW document number comes from `number_claims` (multi-user Phase 1, 2026-10-22).** Before, a new number was "newest PDF + 1" (Abraj) or a counter read before the save and bumped after (Eshraqat): two saves starting together left with the same number and the second overwrote the first - and a save WITHOUT a PDF left nothing for "newest PDF + 1" to see, so even one user's two PDF-less saves could collide (measured on a scratch copy: 80 saves, 1 number). Table `number_claims(kind, doc_no, seq, holder, claimed_at, state)` in each profile DB, created on first use. `claim_next(kind, existing_next, fmt, holder=)` holds the first number at or after what the files/counter say AND past every claim, inside one `BEGIN IMMEDIATE` (serialised across processes); `claim(kind, doc_no, holder=)` holds an editor-proposed number and raises `NumberTaken` when ANOTHER holder holds or already saved it (the same holder re-saving is allowed, as before); `confirm` = used for good; `release` = the save failed (own, held only); a held claim older than 10 minutes is forgotten (engine died mid-save) so its number is reused without a gap; `peek_next(kind, existing_next)` is the read-only number the editors SHOW. The numbering modules wrap it: `claim_next_inv_number` / `claim_next_quote_number` / `claim_next_dn_number` / `claim_next_po_number`, and their `get_next_*_number` now count claims. `holder` is env `BUSINESSHUB_USER` (`api._claim_holder()`), "" until user accounts exist - so a single user never refuses themselves. Pinned by `tests/number_claims.test.py` and the proof `tests/two_engines_one_number.test.py` (two processes x 40 invoices, both companies: 80 distinct numbers, 80 on file; fails with `BEGIN` instead of `BEGIN IMMEDIATE` and with the claims bypassed).
- **Profit is never persisted.** `cost`/`cost_qty`/`cost_sell` live inside `rows_json`; totals recompute each read. Changing `_detect_unit_econ` or `_line_econ` silently changes historical profit numbers.
- **The peg is per-DEAL, not global.** Costs are stored in the doc's entry currency; only profit math converts, at `doc_iqd_per_usd(kind, doc_no)` - profile peg (Abraj 1400 / Eshraqat 1500), overridden to the 1500 market rate for a whole deal whose invoice is paid in **cash**. `_doc_costing_payload` resolves it ONCE per doc and returns it as **`iqd_per_usd`** so the UI converts identically. Absent `doc_cost_meta` row = USD.
- **`_data_sig` is QUADRUPLICATED** (here ~line 1992, `migrations._sig_from_data`, `data_manager._data_sig`, `shared_library._data_sig`). The four must stay byte-identical or the same item splits into multiple rows.
- **Keep every `remember_*` call inside a document-save path wrapped in `best_effort_shared`** - a shared-DB failure must never fail a document save.
- **Module-level `IQD_PER_USD`/`CASH_IQD_PER_USD` snapshot `config` at import.** They are only correct after a profile switch because `api._reload_profile_runtime` importlib-reloads `database` right after `config`; any new module-level config read must tolerate that reload.
- **Do NOT "clean up" the vestigial autofill tables in the per-profile `_SCHEMA`** (nor the `multivariant_items` migration that rebuilds them per-profile) without checking `profile_manager.bootstrap_profiles` / `shared_library.copy_catalogs` - bootstrap copies legacy per-profile catalogs from those tables into `shared/library.db`.
- **`save_quote_record` does not touch `bid_status`/`extra_costs_json`; `save_dn_record`/`save_inv_record` preserve `source_quote_no` when passed `None`.** A generator that passes `""` instead of `None` will WIPE the deal link.
- `set_cost_currency`/`set_doc_columns`/`set_submitted` store the DEFAULT by DELETING the row - never assume a row exists. `set_doc_layout` is the exception for `autofit_rows:false`: explicit false is kept because it is a document-specific override.
- Active document deletes must go through `delete_document_record` (or `Api.delete_document`) so satellite rows do not keep ghosts alive in Costing, Needs Attention, signing/layout state, or linked quote flow. The same helper writes `deleted_docs`, which preserves the numbering sequence without keeping the document active.
- `*_exists` fall back to `rglob` over the doc roots - slow if the tree is huge; a missing DB row can still report "exists".
- `_won_quote_nos` treats any invoice with a non-empty `source_quote_no` as a won quote. **Phase X.26 reversed the precedence**: `has_invoice` is now checked BEFORE the `lost` branch in `effective_bid_status`, so linking an invoice DOES resurrect a bid to `won` even if it was stored `lost` (an invoice ⇒ realized ⇒ won). This is what lets `auto_lose_stale_quotes` safely persist `lost` - a later invoice still reads `won`. Its frontend mirror lived in `costing.js` `setBid` until that screen was retired (2026-09-11); the backend order is now the only one.
- Item memory is **shared** between PO and quote saves (both call `remember_item`); DN/INV use their own tables. The PO passes an `item_payload`; the quote save path does not, so a quote never overwrites a PO's remembered picture with `None` (that is what the `COALESCE` in the upsert protects).

---

## backend/shared_library.py (in brief)

**Adding a column to a shared table (2026-08-11).** `SHARED_SCHEMA` is executed with `CREATE TABLE IF NOT EXISTS`, which cannot add a column to a table that already exists, and the profile migration ledger does **not** cover `data/shared/library.db`. So every post-ship column is listed in `_ADDED_COLUMNS` and applied by `_ensure_columns()` inside `init_shared_db()` - PRAGMA-guarded, therefore safe on every launch. **Declare it in BOTH places**: `SHARED_SCHEMA` (and `database._SCHEMA`, which carries a copy) for a brand-new database, and `_ADDED_COLUMNS` for an upgraded one. Only one of the two and the two kinds of database disagree. `copy_catalogs` needs no change - it does `SELECT *` and builds its INSERT from the source row's own keys.

**`connect()` skips the mkdir it has already done (2026-10-01)** - `_ensured_dirs` remembers each parent folder, the same trick `database._connect` uses; `connect` runs for every shared read.

**Purpose** - The cross-profile catalog DB `data/shared/library.db`. `SHARED_TABLES` = `items, companies, clients, quote_items, dn_items, inv_items, column_memory`; `connect()` / `init_shared_db()` create it on demand at `profile_manager.shared_db_path()`; `copy_catalogs(source_db, target_db)` is what profile bootstrap uses to lift legacy per-profile catalogs into the shared DB; `_data_sig` here is the FOURTH copy of the signature hash. The profile subsystem that owns paths, registry, and bootstrap is [[13-PROFILES-BRANDS]].

---

## backend/db_version.py - "has this database changed since I last looked?" (2026-10-01)

**Purpose** - Speed without staleness. Returning to the hub took ~2 s and Waleed asked for it to be fast "with no issues to up to date data". A timer cache cannot promise that; this asks the database FILE. `version(path)` returns a fingerprint built from SQLite's own **file change counter** (4 bytes at header offset 24, incremented on every committed write in rollback-journal mode - this app's `journal_mode=delete`), plus the file's size and mtime, plus the size and mtime of any `-wal` file (in WAL mode commits land there and the header counter is not kept per commit - covered so a later journal-mode change cannot silently make every cache stale). Any commit by any connection or process moves it; a read never does. Open, read 28 bytes, close: tens of microseconds.

**Key symbols** - `version(path)` -> fingerprint string, or **None when the file cannot be read or is not a database**.

**Used by** - `client_master._prepare` (re-run the legacy-name sync only after a commit), `client_master.canonical_map` (rebuild the alias map only after a commit), and `api.dashboard_state_token` (the hub's "did anything change?" - [[02-BACKEND-CORE]], [[05-FRONTEND-SHELL]]).

**Gotchas**
- **None means "changed", always.** Every caller must recompute on None; a cache that cannot prove it is current must never serve. `tests/db_version_cache.test.py` blinds it and checks for a rebuild.
- **Read the fingerprint BEFORE the data it guards.** Then a commit landing in between costs one extra recompute next time and can never leave old data filed under the new fingerprint. `_prepare` got this wrong in its first draft (it read the fingerprint after syncing, which would have marked another process's commit as already imported).
- **It must hold NO handle open.** The first design kept an idle SQLite connection per database for `PRAGMA data_version`. Windows will not delete or replace a file another handle holds: the test suite's scratch databases could no longer be removed, and nobody could have replaced a database file while the app ran. `tests/db_version_cache.test.py` deletes a file straight after reading its version to keep it that way.
- `PRAGMA data_version` also missed a connection's OWN commits; the header counter does not.

---

## backend/migrations.py

**Purpose** - One-shot, idempotent migrations that reconcile on-disk PDFs + DB rows with schema/layout changes. Fixed run order; each step recorded in `_migrations_applied` - a **per-profile** ledger, so every profile replays the whole registry against its own DB and file tree. There is NO profile-specific migration step; profile bootstrap (staging dir, registry lock, `shared_library.copy_catalogs`) lives in `profile_manager.bootstrap_profiles` ([[13-PROFILES-BRANDS]]).

**Key symbols**
- `migration(key)` - decorator registering a fn under a stable key in `_REGISTRY` (order = definition order).
- `_ensure_table(conn)` / `_was_applied(conn, key)` / `_record(conn, key, notes)` - the `_migrations_applied(key, applied_at, notes)` bookkeeping.
- `run_all() -> list[dict]` - iterates `_REGISTRY`; skips applied, runs pending, records + commits on success, captures exceptions as `{"status": "failed"}` per step (one failure does not abort the rest). Called from `app.main()` after `init_db()`, and re-run against the new profile DB on every profile switch (`Api._reload_profile_runtime`).
- Folder movers: `_move_tree_into(src, dst)` (never clobbers, removes empty dirs), `_strip_type_subfolder(client_yr_dir)` (flattens 2-letter type-code folders), `_update_pdf_paths(conn, table, id_col, old, new)` (rewrites only if the new file exists), `_safe_move(src, dst)` (appends ` (1)` on collision), `_pluck_db_dates`, `_update_pdf_path`, `_file_mtime_date`, `_day_folder`, `_parse_date_display` (D/M/YYYY or ISO).
- Filename re-arrangers: `_new_stem_typed(prefix, stem)` (`QUT-CLIENT-CODE-N` → `QUT-CODE-CLIENT-NNN`), `_new_stem_dn(stem)` (`CLIENT-DN-N`/`DN-N` → `DN-CLIENT-NNN`/`DN-NNN`), `_type_code_from_stem`, `_rename_doc_tree` (renames + syncs pk/pdf_path/`doc_meta`).
- Registered migrations (run in definition order - this list IS the run order):
  1. `2026-07-07_paid_invoices` - ensures the invoice-only paid-status table exists.
  2. `2026-07-08_attention_ignores` - ensures the Needs-Attention mute table exists (Phase X.30).
  3. `2026-07-13_po_source_quote` - **PRAGMA-guarded `ADD COLUMN`** for `pos.source_quote_no`.
  4. `2026-07-15_po_fulfillment` - ensures the PO delivered/paid item-marks table exists (Phase Y).
  5. `2026-05-31_rename_doc_roots` - QUOTATION→QUOTATIONS, DELIVERY→DELIVERY NOTES, INVOICE→INVOICES, drop MT/EQ/MP/SV type segments, move legacy `D:\ABRAJ\CSSP\PO` into `PURCHASE ORDERS\<year>\<month>` (year from file mtime).
  6. `2026-05-31_multivariant_items` - **table rebuilds** (`items_new`/`quote_items_new`/`dn_items_new`/`inv_items_new`): adds `data_sig`, recomputes it, DROP + RENAME. Guarded by `_has_sig` PRAGMA check. Still runs against the (vestigial) per-profile autofill tables.
  7. `2026-05-31_add_cash_bank` - appends the `cash` bank sentinel to `settings.json` `invoice.bank_info`, then `importlib.reload(config)`.
  8. `2026-05-31_dn_client_prefix` - `DN-N` → `<client>-DN-N` on disk + DB (`dn_no` is the PK, so it rewrites the PK).
  9. `2026-06-14_costing_fields` - **PRAGMA-guarded `ADD COLUMN`** for `quotes.extra_costs_json`/`bid_status`, `dns.source_quote_no`/`extra_costs_json`, `invoices.source_quote_no`/`extra_costs_json`.
  10. `2026-06-03_doc_name_rearrange` - applies `_rename_doc_tree` to QUT/INV/DN (leading type code, zero-padded number).
  11. `2026-06-12_day_type_folders` (`migrate_day_type_folders`) - moves every PDF into `…\<Client>\<Year>\<MONTH>\<DD-MM-YYYY>\`; DB date is source of truth, mtime fallback; skips files already in a `DD-MM-YYYY` folder; cleans empty dirs.

**Reads / writes** - Reads/moves files under `config.DOCUMENTS_ROOT` and the `*_ROOT` folders (+ legacy `D:\ABRAJ\CSSP\PO`) - these roots are **per-profile** (abraj `D:\PO TOOL`, eshraqat `D:\ESHRAQAT DOCUMENTS`), so the file movers act only on the active profile's tree; reads/writes `config.SETTINGS_PATH`; UPDATEs `quotes/dns/invoices/pos` (`pdf_path`, sometimes the PK) and `doc_meta`; owns `_migrations_applied` (per profile DB).

**Calls / called by** - Uses `database._connect`, `config.*` roots + `MONTH_FOLDERS` + `save_user_settings`. Called by `app.main()` at startup and by `Api._reload_profile_runtime` on every profile switch. Not exposed to JS.

**Gotchas**
- **Never renumber a registered `key`** - the old key is what `_migrations_applied` checks; renaming re-runs the migration. And because the ledger is per-profile, a NEW key runs once **per profile DB**.
- Registration order = definition order; the dates in the keys lie - e.g. `2026-07-13_po_source_quote` runs 3rd, and `2026-06-14_costing_fields` runs BEFORE `2026-06-03_doc_name_rearrange`.
- Table rebuilds `DROP TABLE items` then `RENAME items_new` - a crash mid-step could leave `_new` orphans; re-run is guarded by `_has_sig`.
- `migrate_day_type_folders` derives Client as the path segment before the year folder in the OLD layout; if it can't find one, the file is **left in place** (silently un-migrated).
- `_add_cash_bank` mutates `settings.json` and reloads `config` in-process - any module holding a stale reference to a config value won't see it.
- `_move_tree_into`/`_safe_move` never overwrite; a pre-existing target leaves the source behind (files can silently fail to migrate).
- `2026-05-31_dn_client_prefix` rewrites `dn_no` (the PK); depends on the old bare `DN-N` filename convention.

---

## backend/doc_settings.py  (the ONE declaration of every document setting)

**Purpose** - Each per-document setting is declared ONCE, together with the
`config` global it is supposed to move. Added 2026-09-05 after a setting was
found that could be offered, saved, reloaded and still reach nothing: the PO row
height was in the panel, it persisted, it came back - and the page it sized never
read it ([[04-BACKEND-DOCGEN]]). A setting used to be spelled out in four
unrelated places (panel HTML, payload, the `config` branch that read it, the
consumer) with nothing tying them together.

**Key symbols**
- `SECTIONS` - doc kind → its section in settings.json: `po → purchase_order`,
  and `quotation`/`delivery`/`invoice` to their own names. THE map; both bridge
  methods and the migration read it.
- `FIELDS` / `PO_FIELDS` / … - the declaration. Each `Field` carries `path` (where
  it lives inside the section), `legacy` (the old TOP-LEVEL spelling), `cast`, and
  `target` (**the `config` global it feeds**). `tests/doc_settings_unified.test.py`
  walks this and proves each value travels the whole way, so a knob cannot go
  inert again without failing the suite.
- `read(settings, kind, field)` - the section first, then the legacy spelling.
  That fallback is what keeps an un-migrated profile, or a settings.json restored
  from a pre-2026-09-05 backup, working.
- `deep_merge(base, patch)` - recurses into nested dicts. The old SHALLOW merge is
  why a panel sending a whole `company` object replaced everything stored under
  it. An empty value (`None`/`""`/`{}`/`[]`) pops its key - that is how an
  override is cleared - while a literal `False` is KEPT, so "autofit off" is a
  stored choice rather than a cleared one.
- `APP_LEVEL_KEYS` - `documents_root`, `po_root`, `ui_lang`, `preferences`,
  `signing`. App settings, not document settings; never migrated into a section.

**Reads / writes** - Pure declaration + helpers. No I/O: `config` reads through
it, `api.get_doc_settings`/`save_doc_settings` write through it, migration
`2026-09-05_po_settings_section` moves the legacy keys.

**Gotchas**
- **The PO's legacy top-level keys are deliberately LEFT in settings.json** after
  the move. `current_effective_settings` still publishes them, the App-settings
  screen still reads some, and an older backup restored over the file must not
  lose them. The section WINS when both are present - otherwise a stale
  top-level key would quietly override an edit made in the panel.
- Adding a setting means adding a `Field` here. Skip it and the guard test
  cannot see it, which is exactly how the last one went inert.
- **A field is only as safe as its CAST is probed.** `PO_COMPANY_TAGS` was
  read from the legacy top-level key for one release after the panel had
  moved to `purchase_order.company_tags` - saved to disk, never reaching the
  global - because the guard test walked the declaration but `continue`d past
  every cast it had no probe for (`dict`, `list`). It now probes those too and
  `test_no_declared_field_is_left_unprobed` FAILS on an unprobed cast rather
  than skipping it. Declare a new cast, add its probe in the same change.

---

## backend/data_manager.py

**Purpose** - The Data Manager (Phase I): a section-driven browser + light maintenance over both the *document* tables (SECTIONS - the **per-profile** DB) and the *autofill catalogs* (CATALOG_SECTIONS - the **shared** library DB), plus profile-aware zip backup/restore (v2). Delete removes DB rows only - PDFs on disk are never touched.

**Key symbols**
- `SECTIONS` (via `_section(**kw)`) - 10 document sections: PO(1), QUT×4, DN(1), INV×4. (LTR×2 went with the Official Letter on 2026-08-27.) Each carries `key, label, group, group_label, table, pk, sub_filter, editor_route, display_columns, search_fields, date_field, client_field, sub_type_value, row_count_source`. (`display_columns` kinds: `text | date | datetime | money | count`; `__lines` is the `count` col.)
- `CATALOG_SECTIONS` (via `_cat(**kw)`) - **3** catalog sections: `companies`, `clients`, `column_memory`. It was 13 until 2026-09-05, and **ten of them pointed at the four catalogs dropped on 2026-08-20** (`items`, `quote_items`, `dn_items`, `inv_items`). They were still listed on the Data Manager screen and raised `no such table` the moment any was opened - a dead menu the user could not tell from a live one until they clicked. The item catalogue is `master_items` now; never add a section over a dropped table. Guarded by `tests/master_data_backend.test.py::SourcesNoLongerReachACatalog`. Carry `key, label, group, group_label, table, name_col, pk_cols, variant_cols, data_json_col, sub_filter, fixed_values` (+ optional `primary_header`, used by column_memory to label the name column "Value"). **Every catalog browse/edit/delete/merge/dedup function runs through `_connect_shared`** against `data/shared/library.db` - catalog changes here affect ALL profiles.
- `_section_by_key` / `_catalog_by_key`, `all_section_keys`, `all_catalog_keys`, `section_label`.
- Document browse: `list_sections()`, `list_records(section_key, year, month, client, search, limit=500, offset=0)` (computes `__lines` from `row_count_source`, `__pk`, `__pdf`, `__pdf_exists`), `list_clients_for_section`, `list_years_for_section`.
- Document maintenance: `delete_record(section_key, pk_value)`, `delete_records_bulk(section_key, pks)`, `relink_pdf(section_key, pk_value, new_pdf_path)`. **`delete_record` is a RAW row delete** - no satellite cleanup, no `deleted_docs` tombstone; the safe active-doc delete is `database.delete_document_record`.
- Catalog browse: `list_catalogs()`, `list_catalog_records(section_key, search, limit=500, offset=0)` (unpacks `data_json` variant fields, builds composite `__pk` dict + `__name`; ORDER BY `use_count DESC, last_used_at DESC`).
- Catalog edit/delete: `_pk_where`, `delete_catalog_entry`, `delete_catalog_entries`, `_row_data_for_sig`, `_upsert_row` (INSERT … ON CONFLICT summing `use_count`), `update_catalog_entry` (delete-then-upsert preserving use_count).
  - **`_row_data_for_sig` builds the row's `data_json`, not just its hash.** It carries every supplied non-structural key - declared fields, ignored currency/picture fields, and canonical custom data - while excluding the SQL name, fixed type discriminator, signature and counters. `_data_sig` alone decides what affects identity. This prevents a Master Data edit from silently losing a custom material field; pinned by `AnEditKeepsWhatItDidNotTouch`. (The reviewed-merge half was retired 2026-09-05 with the material duplicate review - see below.)
- Dedup + merge: `_normalize_name` (lowercase, collapse ws, strip punct + bidi marks), `_similarity` (difflib ratio), `find_catalog_duplicates(section_key, threshold=0.85)` (greedy single-link clustering, drops singletons), `merge_catalog_entries(section_key, canonical_values, absorbed_pks, connection=None)` (sums use_counts, deletes absorbed, upserts canonical; an optional shared connection makes a batch atomic).
- Backup/restore (v2, profile-aware): `_doc_roots`, `_safe_archive_name`, `create_backup(target_folder)` - archive named `documents-hub-<profile>-backup-<ts>.zip` containing `manifest.json` `{version: 2, profile_id, documents_root, document_roots}` plus arcnames `profile/po_tool.db`, `profile/settings.json`, `profile/assets/**`, `shared/library.db`, `docs/<root>/**`. `restore_backup(archive_path, replace_docs=False)` accepts ONLY version-2 archives whose manifest `profile_id` equals `config.ACTIVE_PROFILE` (hard-refuses cross-profile restores); stages to `.restore-<uuid>` under `data/profiles/`; restores the profile DB + settings + assets AND `shared/library.db`; merges docs by default (`replace_docs=True` rmtrees the doc roots first).
- `_data_sig` - local copy of `database._data_sig` (see quadruplication gotcha).

**Reads / writes** - Reads doc tables via its own `_connect` (profile DB) and catalog tables via `_connect_shared` (shared DB); DELETEs/UPDATEs doc rows (`delete_record`, `relink_pdf`) and catalog rows (edit/delete/merge - cross-profile). `create_backup` reads the profile DB + settings + assets, the shared library DB, and `_doc_roots`; `restore_backup` OVERWRITES the profile DB, settings, assets, the shared library DB, and (optionally) doc folders.

**Calls / called by** - Imports `config`, `profile_manager`, `shared_library`. Exposed to JS as `api().dm_list_records`, `api().dm_delete_record`, `api().dm_relink_pdf`, `api().dm_list_catalog_records`, `api().dm_find_catalog_duplicates`, `api().dm_merge_catalog_entries`, `api().dm_create_backup`, `api().dm_restore_backup`, etc. Feeds `dataManagerView` (see [[02-BACKEND-CORE]], [[07-FRONTEND-SUPPORT]]).

**Gotchas**
- **Delete NEVER removes the PDF** (locked decision) - `kept_pdf: True` in every response; orphaned PDFs are only surfaced as informational by the integrity check. And it is a RAW delete: satellites (`submitted_docs`, costing, `doc_meta`, fulfillment…) are NOT cleaned and no `deleted_docs` tombstone is written - by design; use `database.delete_document_record` for a real active-doc delete.
- `restore_backup` **REPLACES the live profile DB, settings.json, profile assets AND the shared library DB wholesale**; it refuses archives from another profile; `replace_docs=True` `rmtree`s the active profile's doc roots first - destructive.
- `SECTIONS`/`CATALOG_SECTIONS` `table`/`pk`/`sub_filter` are interpolated straight into SQL - they are trusted constants, NOT user input; never make them dynamic.
- The `count` column `__lines`/`__pk`/`__pdf`/`__pdf_exists` synthetic keys must match the frontend renderer.
- `_upsert_row` recomputes `data_sig` from `variant_cols`; for a merge to actually collapse duplicates the variant fields must reduce to the same sig - a difference in any variant column keeps them separate.
- `find_catalog_duplicates` loads up to 5000 rows and uses greedy single-link clustering - anchor order (most-used first) affects grouping; changing the sort changes results.
- `_normalize_name` strips bidi marks and most punctuation - Arabic/mixed names may normalize into the same anchor unexpectedly.

---

## backend/client_master.py

**Purpose** - The client IDENTITY, in the shared library. One record per client, carrying every spelling that client has ever been called. Added 2026-09-05/06 as the Clients half of Master Data, beside Materials. It deliberately shares **nothing** with material storage.

**Why it exists** - A client's name is written onto documents that leave the building. Once a quotation says "Hyundai Engineering", that is what the customer holds, so the saved row must keep saying it too. Renaming used to rewrite `client_name` across every profile's `quotes`/`invoices`/`dns`, which made the database disagree with the paper. Now the identity is renamed once and the old spelling becomes an **alias**; the alias is what keeps the old documents in this client's history.

**Storage** (shared library, `data/shared/library.db`)
- `client_records` - `id` (uuid4 hex), `name`, `details_json`, `archived`, `created_at`, `updated_at`.
- `client_aliases` - `alias_key` (PK, the normalized form) -> `alias` (as typed) -> `client_id`. The display name is itself an alias row, so lookup is one join for any spelling.
- `client_preferences` - `(client_id, profile_id)` -> `data_json`. Commercial defaults are **per company profile**: Abraj's preferred receiving account is not Eshraqat's.
- `client_master_meta` - one-time markers, currently `presets_seeded`. This is what stopped `database.init_db` re-inserting the nine preset clients on every launch.
- The legacy `clients` table stays as a **compatibility index** (it still carries `use_count`), not as the source of truth. `_init` imports any name in it that no alias owns yet, so a name an older app instance remembered cannot be lost - and a name an alias already owns is never re-imported as a second identity.

**The one identity rule** - `normalize(value)` = collapse inner whitespace + Unicode `casefold()`. Everything keys off it. It is NOT `COLLATE NOCASE` - see the Gotchas.

**Every spelling is a key (2026-09-11)** - `identity_names(name, details)` returns the display name plus the two identity FIELDS, `legal_name` and `name_ar`, and `save_record` indexes all of them as aliases. So "TDC", "CCCC ltd", "China Communications construction company Ltd. Iraq Branch" and "هيونداي" all resolve to their one client, and its payment method, address and document number follow from any of them. `_index_identity_names(c)` backfills records saved before the rule; it is idempotent and never takes a spelling another client already owns. The editable alias list in `_out` excludes these two, so a field is not also shown as an alias to delete.

**Key symbols**
- `normalize(value)`, `_now()`, `_text(value,label,limit)`, `_alias(c,value)`, `_find(c,identity)` (by id, else by alias), `_insert(c,name)`, `_init(c)` (schema + `_sync`), `_sync(c)` (legacy-name import + identity re-index + commit), `_prepare(c)` / `_db_file(c)` (see the Gotcha on setup cost), `_connection()` (context manager; commits, rolls back, always closes).
- `list_records(search,status)` / `get_record(identity)` / `save_record(payload)` / `set_archived(identity,archived)` - the CRUD behind the `cm_*` bridges.
- `save_record` validates types and lengths, refuses a name another client owns, refuses a stale `updated_at`, refuses a `profile_id` that is not the active one, and **always retains an old alias that documents still reference**.
- `defaults_for(name)` - the settings the editors apply when a client is PICKED on a document. Carries **`print_name`** (the legal name, else the display name - what the document prints) and **`code`** (the display name - what the number and the folder are built from). `name` stays the display name; nothing groups by `print_name`. Resolves the preferred receiving account and refuses when it is missing from this profile or the client is archived. Every return carries **`known`**: the editors apply this automatically, so an unknown name (`known: False`) must fail silently - naming a new client is ordinary - while a real client whose settings cannot be applied (`known: True`) is reported to the user.
- `document_code(name)` - **the one implementation** of "what short code do this client's documents carry": resolve every spelling to the display name, then strip what a Windows path cannot hold. `defaults_for` returns it as `code` (the editor's chip) and `_sanitize_client` in all three numbering modules calls it (the number and the folder). A second copy of this rule is how the chip could say one thing while the number said another, and the chip exists to be trusted. A name that is not on file comes back as typed.
- `aliases_for(name)` (spellings), `alias_keys_for(name)` (**normalized** - what a query must match on), `canonical(name, identities=None)`, `canonical_map()` (normalized alias -> display name), `suggestions(prefix,limit)` (one row per identity, active only), `remember(name)`, `seed_once(names)`.
- `usage_for(names, index=None)` - counts real dependencies across BOTH profiles plus the material company memory, read-only (`mode=ro` URIs) and **never creates a profile file**. A profile it cannot open is reported in `errors`, never silently counted as zero. **`usage_index()` (2026-10-01)** walks those tables ONCE and counts by normalised name; `list_records` builds it once and passes it to every client. It used to rescan every document of both companies - parsing every PO's rows - once per client: Master Data > Clients took 160-470 ms and grew with every document saved; now ~25 ms. The walk is the old one statement for statement (same counts, reference keys, key order, error strings, partial counts on a half-failed profile); `tests/db_version_cache.test.py` compares it against the original algorithm kept verbatim in the test.
- `remove_unused(identity)` (refuses if referenced, re-checks at mutation time), `merge_empty_records(target,sources)` (refuses a source that has saved details).

**Calls / called by** - Imports `config`, `profile_manager`, `shared_library`. Called by `clients.py`, `insights.py`, `database.suggest_clients`/`init_db`, and by `inv_numbering` / `dn_numbering` / `quote_numbering` (each `_sanitize_client` resolves through `canonical` so a document is numbered and filed under the short code whichever spelling was typed; an unknown name comes back as typed, which is what lets a client be invoiced before anyone files them). Exposed to JS as `api().cm_list`, `api().cm_get`, `api().cm_save`, `api().cm_archive`, `api().cm_defaults` -> `clientMasterView` (see [[07-FRONTEND-SUPPORT]]).

**The client named `N/A`** - work that genuinely has no client: a quick quotation for someone unrelated, a sample, a price check. It is a REAL record rather than a magic value, so nothing anywhere needs a special case: it numbers and files as `NA`, appears in the datalist, groups in Insights and shows `NA` on the editor chip like any other client. The single thing it does differently is PRINT - `api._printable_document` blanks the client line, because "N/A" on a page that leaves the building looks unfinished.

**Gotchas**
- **Setup runs when the data changed, not on every connection (2026-10-01).** `_init` used to run IN FULL on every `_connection()` - schema script, a scan of the legacy `clients` table, a re-index of every record's legal and Arabic names (one SELECT per spelling) and a commit, ~4 ms each time. That was 1.3 s of the hub's 1.4 s Needs Attention call. Now `_prepare` runs the schema once per database file per process and re-runs `_sync` only when `db_version.version` has moved since the last sync. **The sync is still load bearing**: `database.remember_client` writes a typed client name to the legacy `clients` table on every document save, and the sync is what turns it into a record - it now rides on the change counter instead of on every connection. `canonical_map()` is cached on the same counter. Both are keyed on the file the connection REALLY has open (`_db_file`, from `PRAGMA database_list`), not on `profile_manager.shared_db_path()`, because tests swap `shared_library.connect` for a scratch database.
- **`canonical(name)` without `identities` opens a connection.** Pass the map in any loop - the docstring always said so, and the LPO helpers ignoring it is what made the hub slow. See the database.py Gotcha.
- **`normalize()` and `COLLATE NOCASE` are not the same rule.** `COLLATE NOCASE` folds ASCII only and does not collapse whitespace, so `Hyundai  Engineering` and `STRASSE` never matched the aliases `Hyundai Engineering` and `Straße`. Aliases resolved in Python while their documents stayed invisible to the query. `clients.py` and `insights.py` now register `normalize` on the connection as a deterministic SQL function `client_key(...)` and match on that - the fix is to teach SQL the real rule, never to keep a weaker copy of it.
- `id` is a **uuid hex string**, not a number. The Clients list read it back with `Number(...)`, which is `NaN` for every real id, and that made the whole list unclickable (fixed 2026-09-06, pinned by `client_master_ui`).
- Preferences are profile-scoped; the record and its aliases are shared. Saving with a `profile_id` other than the active one is refused rather than written to the wrong company.
- **The numbering modules import this lazily and swallow its errors.** A client store that cannot be read must not stop a document being numbered - `_sanitize_client` falls back to the name as typed. Do not "improve" that into a hard failure.
- A `usage_for` failure AFTER a committed save is reported inside `record["usage"]["errors"]` - the save is not re-reported as failed, because prompting a retry of a completed mutation is worse than a missing count.

---

## backend/clients.py

**Purpose** - Client profiles: aggregate every client-facing document (quotes, invoices, DNs - not POs, which are suppliers') from the **active profile's** DB into per-client rollups for the Clients section. The aggregation is read-only; the Data-Manager add/delete/rename helpers are now thin wrappers over `client_master` and write only the shared library.

**Key symbols**
- `_connect` (active profile DB - **registers the `client_key(...)` SQL function**, see Gotchas), `_connect_shared` (shared catalog DB), `_num`, `_exists`, `_now`, `_quote_value(rows_json)` (best-effort sum of `qty*rate` or bare `rate` - also reused by `analytics.py`), `_bank_labels()` (from `config.INV_BANK_INFO`).
- `add_client(name)` - `client_master.save_record({"name": ...})`.
- `delete_client(name)` - **archives** the identity (`set_archived`). Nothing is deleted; saved documents keep their `client_name` and stay reachable. Returns `archived=True, deleted=0`.
- `rename_client(old, new)` - renames the identity and keeps the previous spelling as an alias. **Writes to no profile database.** A name another client already owns is REFUSED (`ok: False`), never silently merged. Returns `{ok, new}`, or `{ok, noop: True}` when the name did not change.
- `delete_doc(doc_type, no)` - delete one client document row (`_DOC_TABLE` maps `invoices`/`quotes`/`dns`), returns its `pdf_path` for the caller to recycle.
- `client_list()` - every identity from the shared records plus any `client_name` on an ACTIVE-profile doc; aliases fold into ONE row (`+=`, keyed by `normalize`), so a client's counts are not split across spellings. Sorted: no-docs last, then recent activity, then name.
- `client_profile(name)` - full profile from the ACTIVE profile's docs, matched on `client_key(client_name) IN (alias_keys_for(name))`: invoice/quote/DN lists, most-common address (`default_address` + `addresses`), recent distinct LPOs (excludes `N/A`), most-common payment bank + majority withholding, top quotation terms, `invoiced_total`, first-seen/last-activity, and per-doc `signed` badges via `signing.signed_doc_nos`. The returned `name` is the CANONICAL display name whichever spelling was asked for.

**Reads / writes** - Reads the active profile's `quotes`/`invoices`/`dns` plus the shared client records. Writes only via `add_client`/`delete_client`/`rename_client` (shared library only) and `delete_doc` (active profile). Uses `config.INV_BANK_INFO` and `signing.signed_doc_nos`.

**Calls / called by** - Imports `config`, `profile_manager`, `shared_library`, `signing`, `client_master`. `_quote_value` and `client_list` are called by `analytics.dashboard_analytics`. Exposed to JS as `api().client_list`, `api().client_profile`, `api().add_client`, `api().rename_client`, `api().delete_client`, `api().delete_doc` -> `clientsView`.

**Gotchas**
- **Only quotes/invoices/dns carry `client_name`** - POs are suppliers' and are intentionally excluded from every rollup.
- `invoiced_total` = `SUM(grand_total)` from invoices only; `_quote_value` is a heuristic (`qty*rate` or bare `rate`) and can diverge from the printed quote total. That SUM is safe across IQD invoices **only because `grand_total` is always USD** (2026-07-29) - never switch it to `sell_total`.
- **Never match a client with `COLLATE NOCASE`.** It is not the identity rule; `_connect` registers `client_key(...)` so a query can use the real one. Reverting a query to `COLLATE NOCASE` silently drops documents whose saved spelling differs by a space or a non-ASCII letter case - caught by `client_history_aliases_backend`.
- Renaming no longer touches any profile database, so there is nothing cross-profile to make atomic. The old ATTACH-every-profile machinery and its use_count merge are **gone**; do not reintroduce them.
- `delete_doc` returns the path but does **not** delete the file (caller decides).

---

## backend/analytics.py

**Purpose** - Dashboard/Insights analytics: month-windowed counts, invoice value by category, quoted value, activity heatmap, signed ratio, top clients, and month-over-month KPIs - all from the saved-document tables.

**Key symbols**
- `_connect`, `_MONTH_ABBR` (1-indexed, `[0]=""`), `_DOC_TABLES` (quotes/invoices/dns/pos), `_CATEGORY_LABEL`.
- `_month_window(months_back, ry, rm)` - last N (year, month) pairs ending at ref, oldest first.
- `_ym(year, month)` - sortable int key `year*12 + (month-1)` (used for `BETWEEN` window filters).
- `_grouped_counts(conn, table, with_money=False)` - `{(year,month): {c, s}}`; `s` = `SUM(grand_total)` only when `with_money`.
- `_signed_ratio(conn)` - signed vs unsigned across all types all-time (intersects `signing.signed_doc_nos` with existing rows).
- `dashboard_analytics(months_back=12, ref_year=None, ref_month=None)` - the single entry point returning `months` series, `totals` (incl. `invoiced`, `quoted`), `inv_by_category`, `top_clients` (all-time by invoiced), `signed`, `kpis` (this vs last month, deltas via `_delta`, quote→invoice %), `daily` heatmap (18 weeks from `created_at`), `daily_max`.

**Reads / writes** - Read-only. Reads all five `_DOC_TABLES` in the **active profile's** DB - zero profile branching in this module (switching profiles simply repoints `_connect`); uses `clients._quote_value`, `clients.client_list`, `signing.signed_doc_nos`. Clamps `months_back` to 3–24.

**Calls / called by** - Imports `config`, `clients`, `signing`. Exposed to JS as `api().dashboard_analytics` → `analyticsView` + the hub "This Month" card (see [[07-FRONTEND-SUPPORT]]).

**Gotchas**
- **Revenue is invoice-based** (`grand_total`); `quoted` value uses the same `_quote_value` heuristic as clients.py. `grand_total` is USD for every invoice including dinar ones (2026-07-29) - a per-row currency conversion is impossible inside these `GROUP BY` queries, which is exactly why the column is kept single-currency.
- `_delta` returns `None` when the previous period is 0 (no baseline) - the UI must handle a null delta, not treat it as 0%.
- The heatmap keys off `created_at` (creation date), NOT the document's stated date - differs from the month-window series which keys off stored `year`/`month`.
- `_month_window` and its near-identical twins in `database._months_back` are duplicated logic - keep the "oldest first" ordering consistent.
- `top_clients` reuses `clients.client_list()` (all-time), so it ignores the `months_back` window - deliberately all-time, not period-scoped. Note `client_list` now also unions in shared-catalog clients with no docs in this profile (they carry zero totals, so rankings are unaffected).
- Missing tables/columns are swallowed (`except sqlite3.Error: pass`) - a broken table silently yields zeros rather than an error.

---

## backend/insights.py

**Purpose** - The money engine behind the Insights view (2026-08-09): **charged / spent / profit per client**, per month, over a 1/3/6/12-month window ending at any month. Added 2026-08-09 with the Insights redesign ([[07-FRONTEND-SUPPORT]]).

**Why it is not more `analytics.py`.** `analytics.py` counts DOCUMENTS. Everything here is MONEY - and money in this app is never a stored column: profit is recomputed on read from the per-row costs inside `rows_json` ([[09-DATA-MODEL]]). `database.profit_summary` already does that correctly but re-opens each document one at a time (`load_doc_costing` → `load_quote` → `get_cost_currency` → `doc_iqd_per_usd` → …), which is fine for one month and far too chatty for "every client over 12 months". This module keeps the **same arithmetic** - it calls `database._line_econ`, the one function that turns a row into revenue/cost - and only replaces the *plumbing*: every per-document lookup resolved once into an index, then one pass over the window. Measured 0.05 s for 12 months / ~106 documents.

**Vocabulary** (also the UI's): `charged` = invoiced revenue USD · `spent` = line costs + extra costs · `profit` = charged − spent · `margin` = profit ÷ charged · `pipeline` = **OPEN quotations only** (a won quote's money is counted through its invoice; a lost one is not money).

**Key symbols**
- `normalize_window(months, end_year, end_month) -> (window, ey, em, n)` - oldest-first `(year, month)` pairs. A falsy `months` means "no preference" → 12; anything else clamps to 1…`max(ALLOWED_WINDOWS)`. `ALLOWED_WINDOWS = (1, 3, 6, 12, 24)`.
- **`_Ctx(conn)`** - the index. Each attribute mirrors exactly one `database` function: `cost_currency` ↔ `get_cost_currency`, `peg` ↔ `doc_iqd_per_usd` (same branch order: pinned `doc_cost_meta.rate` > the invoice's recorded `sell_iqd_per_usd` > eshraqat market rate > cash deal > profile peg), plus `cash_invs`/`cash_roots`/`dn_root` ↔ `_cash_deal_index`, `won_quotes`, `paid_invs`, `submitted`, `quote_invoiced_on`.
- `_doc_econ(ctx, kind, doc_no, sub_type, rows_json, extra_json, want_items)` - one document's `{charged, spent, profit, lines, costed, items}` by delegating each row to `database._line_econ`.
- `_collect(window, client=None, want_items=False)` - every quote / invoice / DN in the window with its economics attached (3 queries + the index).
- `insights_overview(months, end_year, end_month)` - `{totals, series (per month), clients (full metric block each), categories, concentration, period_label}`.
- `client_insights(name, months, …)` - the overview's block for that client + `docs` (every document with its own profit) + `items` (top 12 invoiced lines) + **`dn_lines`** (2026-08-25) + **`invoices` / `invoice_summary`** (2026-08-26).
- **The invoice block (2026-08-26)** - an invoice is asked two questions the mixed `docs` table answers badly: has the money arrived, and - when it lost money - what did it. `invoices` is one row per invoice (charged / spent / profit / margin / `paid` / `costed` / `submitted`) plus the diagnosis: `losing`, `loss`, `losing_lines` (worst-first, capped at 4, each named with its dollar hole), `losing_line_count`, `losing_line_total`, `uncosted_lines`, `extra`. `invoice_summary` splits the period into paid vs unpaid (counts + money), losing (count + total loss) and uncosted (count + money covered).
  - **Three causes look identical in a red total**: lines sold below cost, document-level `extra` costs that no line carries (so an invoice can be in the red with every line healthy), and lines with no cost entered (which makes the loss a *lower* bound). `_doc_econ` now returns `uncosted_lines` and `extra` so each can be named per document.
  - Nothing here re-does the arithmetic - the figures are the same `_doc_econ` numbers as the totals above them, which is what stops the section disagreeing with the page it sits on. Pinned by `tests/insights_invoices_backend.test.py`.
- `_is_document_line(row, name, known_dns)` (2026-08-25) - is this invoice line a whole DELIVERY NOTE billed as one row? The invoice editor can drop a DN in as a single summary line, and Insights was ranking those as items: **eight of TDC's top twelve "items" were DN numbers**, at 100% profit (those lines carry no cost), pushing the real items out of a 12-row table. Three tests, none of them a guess: the `dn_ref` marker (only on lines saved since 2026-07-18), the name being a DN number in this profile (`_Ctx.dn_numbers`), or a single-token name with a bare `DN` segment (`DN-TDC-024`, `TDC-DN-6` - older numbering no longer in `dns`). A name with a space can never match, so no item name can be mistaken for one.
  - **The money does not move.** These lines are real invoiced revenue and stay in `charged`/`spent`/`profit`; only the item roll-up excludes them, and what was excluded comes back as `dn_lines` (`count`/`charged`/`spent`/`profit`/`uncosted`) so the UI can state it. A filter the reader cannot see is a lie. Pinned by `tests/dn_as_invoice_line.test.py`.
- `_finish_client` / `_blank_client` - the per-client metric block: charged, spent, profit, margin, **markup**, avg/biggest invoice, outstanding vs collected, pipeline value/profit, won/lost/open + win rate, uncosted count and the money it covers, active months, best month, per-month series, category mix, avg days quote→invoice, share of company charged/profit, rank.
- `_all_time_activity(names)` - `first_seen` / `last_activity` **all-time**, deliberately not window-scoped.

**Reads / writes** - Read-only, active profile DB only. `_connect()` is a `@contextmanager` that actually **closes** (the package's usual `with sqlite3.connect(...)` commits but does not close, and this module opens a connection per call while the user clicks through months and clients).

**Calls / called by** - Uses `database._line_econ`, `_primary_key_for`, `cost_to_usd`, `effective_bid_status`, `_parse_doc_date`, `paid_invoice_set`, `submitted_set`. Exposed as `api().insights_overview` / `api().client_insights` ([[02-BACKEND-CORE]]) and consumed by `static/analytics.js`. In `Api._reload_profile_runtime`'s reload list, per the import-order invariant in [[13-PROFILES-BRANDS]].

**Gotchas**
- **Never re-implement the arithmetic here.** `tests/insights_parity_backend.test.py` pins this module against `profit_summary` month by month and `_Ctx.peg` against `doc_iqd_per_usd` for **every document in the DB**. Change `_line_econ` instead.
- The parity tolerance is ~1 cent per document **on purpose**: `profit_summary` sums each document's already-rounded totals, this module sums raw and rounds once. A real disagreement (a wrong peg, a dropped extra cost) is dollars, not cents.
- **Delivery notes carry no money** - they are counted, never summed. The invoice is where the money is.
- The `eshraqat` branch in `peg` is invisible on real data: `config.IQD_PER_USD` is 1500 under eshraqat and `CASH_IQD_PER_USD` is also 1500, so it and its fallback return the same number. The test pins it with a synthetic `_Ctx` instead.
- `charged` is the **costing engine's** revenue (so profit and margin are internally consistent), not `invoices.grand_total`. Both are returned - `totals.invoiced_pdf` / `client.invoiced_pdf` is the PDF figure - and they can differ where an invoice's extras or withholding sit outside the line items.
- `uncosted` is the honest caveat, not a nag: an invoice with a costless billable line reports that line's cost as **zero**, so its profit is an upper bound. The UI says so; do not quietly exclude those documents instead (that would understate revenue).
- **Aliases are one client here too.** `_connect` registers `client_key(...)` (= `client_master.normalize`); `_collect` filters with it, `_all_time_activity` groups with it, `insights_overview` keys `by_client` on it, and `client_insights` canonicalises the requested name BEFORE looking for its block. Skip that canonicalisation and an alias request silently falls through to the EMPTY block and reports zero for a client with real money. Pinned by `client_history_aliases_backend`.

---

## backend/master_items.py

**Purpose (2026-08-16, step 1 of the item-record rebuild)** - ONE row per item, in a real table, which is what the name "Master Data" has always implied and never been. `master_data.py` has **no storage of its own** (zero `CREATE TABLE`): it reads the four document catalogs and folds them for display, so the flow ran backwards - documents saved whatever they saved, and Master Data read it back. That is why one product could be four rows, why a cost had to be written onto each separately, and why بصل and `Onion` counted as two items.

**Key symbols**
- `SCHEMA` - `master_items` (item_id, item_no, name_en, name_ar, **description_en, description_ar, unit_en, unit_ar**, cost/price native + currency, image_refs, img_query, data_json, use_count, timestamps) and `master_item_sources` (which catalog rows each record was folded from).
### Step 4 - the item holds its own details (2026-08-19)

The first attempt at this consolidated the four catalogs into a second table (`master_item_lines`) holding what each document type said about an item. **Waleed rejected it, and he was right.** His model has always been one place with one set of fields, and the thing that supposedly did not fit turned out to fit perfectly:

| what the line held | where it actually belongs |
|---|---|
| `specs` | the item's **description** - the equipment quotation's column is literally headed *"Specifications"* |
| `rate` | the item's **price** - that column is headed *"Monthly Rental Rate"* |
| `col_3` = `EA` | the item's **unit** |
| `col_4` = qty | nowhere: a quantity is what ONE document ordered |
| `col_2`, `col_5`, `col_6` | user-added columns of no consistent meaning - one of them held a base64 JPEG someone pasted in |

So the side table is gone and its contents were folded into the record: **13 descriptions and 21 prices** filled in, the rest dropped. `master_item_sources` went with it.

**What is NOT done automatically: splitting a second wording into a second item.** A dry run showed `Hydraulic Crane` becoming nine items all priced 152000, differing only by brand and year, one of them reading `120 TON | Brand:  | ` - one offering typed with varying detail, not nine products. Bus's second wording was the half-typed `45-Seater | Driver I`. Each item KEEPS its most-used wording as its description; the other 13 are written to `data/shared/other-wordings-to-review.txt` for Waleed, and the **New item** button is how any of them becomes a real product. Guessing here would have made the catalogue worse.

`backfill_languages` now finds a record's catalog rows **by name** rather than through the retired link table. Looser on purpose, and safe: it only ever fills a BLANK slot, so the worst a wrong row can do is supply a unit the item does not have yet, and the per-language rule still decides the slot.

### Step 4b - every dropdown reads the RECORD (2026-08-19)

`database._suggest_across` used to search all four catalogs on every keystroke, score the results, de-duplicate them and then pick a language. There is one row per item now, so it reads `master_items` and none of that is needed.

- **The row it hands back speaks the asking document's language.** A PO gets بصل and كيلو; an invoice gets `Onion` and `KG`. Name, description and unit together, never a mixture. `_ARABIC_DOCUMENTS` is the list of Arabic document types (the PO, today).
- **The description is STRICT to that language (2026-08-20)** - no cross-language fallback. Typing an English name (`Carrot (Big size)`) fills its English description only; if it has only an Arabic وصف, the description comes back empty rather than in the wrong language. The unit still falls back (كيلو and KG are one thing).
- **Both names are still a way IN.** Typing `onion` in an Arabic PO finds بصل, scored at 0.9x so the name actually typed always leads. An item known in only one language is offered in that language rather than hidden.
- **Multi-word queries: every typed word must appear, when a full match exists (2026-08-20).** `_token_coverage` counts how many typed words a candidate contains; if any candidate covers them all, `_suggest_across` drops the ones that cover only some. So `رف حديد` (iron shelf) keeps `رفوف حديد` and drops `حديد زاوية` (shares only the material word) - but `safety cone` still reaches `Traffic Cone` because nothing there covers both words. A single-word query stays broad.
- **`_desc_key(home_table, home_sub)` decides WHICH COLUMN gets the description.** An equipment quotation calls it *Specifications*, everything else calls it *description*, and a `service` quotation's first column IS `description` - the item's name goes there, so emitting one would overwrite the name.
- **`_FILL_FROM_RECORD`** is everything that travels: cost, price, picture, search phrase, item number. A quantity, a total, and the **company are NEVER filled** (2026-08-20, Waleed) - a quantity is what one document ordered, and the company is a fact about the last purchase order, not the item. `last_company` still lives on the record but the suggestion no longer carries it.

**Two behaviours changed on purpose**, both agreed with Waleed:
- a **custom column** (`col_2`…`col_6`) is no longer remembered anywhere. It is not a property of a product, and the live values were a rate, a quantity and one entire base64 photograph;
- an item has **one price**, served to every document type. Only 5 of 143 multi-priced items had ever disagreed across types, all of them a quoted figure against a billed one. A price that really differs is a different offering and belongs to its own record.

**A REBUILD MUST NOT UNDO THE MASTER.** This has now bitten three times - the item number, the unit rulings, the folded spec text - and `create_record` made it sharp: an item typed by hand has no catalog row, so `build()` simply deleted it. `build()` now (a) keeps hand-made records whole, (b) carries every `_KEEPABLE` field onto a record the rebuild produced blank, and (c) refuses to hand ONE number to several items - the names-only fallback was giving three `Bus` write-ups the same M-00127. Pinned by `ARebuildDoesNotUndoTheMaster`.

`specs` is read as a description by BOTH halves - `_rows()` for the rebuild and `_mirror_to_master` for a document save - or the two would disagree, which is the drift this step exists to close. It also stops every equipment save opening a second record beside the one that already has a description.

**The Fill button and paste matching read the record too.** `resolve_items` used to call `_variants()` + `_fold()` - every row of all four catalogs, grouped back together on every paste. `_items_from_records(lang)` builds the same shape straight from `master_items`, so the grouping is already done. The 250 lines of matching below it (tiers, typo tolerance, the refusal to guess between two plausible items) are **unchanged**; only where the list comes from has moved.
- The list is built in the **asking document's language** - a PO fills بصل and كيلو, everything else `Onion` and `KG`.
- Both names are indexed as ways to FIND an item (`aliases`), while what gets FILLED is the document's language. That replaced the old "swap onto the paired name" step, which existed only because the two languages were separate items.
- The identity is the **item number** now, not a `name|description|unit` string - it survives a rename, which the string never could.
- One behaviour got **stricter**: an English name shared by three Arabic variants is an ambiguous name, because three real records answer to it. The resolver offers them and fills nothing. It used to keep the English row, since the English side was an item of its own; it is not one any more.

**`remember()` stores BOTH names on insert.** An Arabic PO line carrying its English name used to create a record with the Arabic side only - the pairing the document had just told us was thrown away, and the item could not be found in English until some later save happened to teach it.

**Health reads the record (2026-08-19).** The panel's lead used to be *"800 items stored as 3,000 rows across 4 catalogs"* - an answer to "how much is stored", which is a fact about the old storage. It now reports **what is still to fill in**, item by item, and its numbers come from the SAME `_MISSING` SQL the Master Data chips use, so the panel and the chip can never disagree.

**`image_report` counts the record's pictures as referenced.** It read the four catalogs only, and `purge_orphan_images` **deletes** what it calls an orphan - so a picture living only on the record (an item typed by hand, or one carried across a rebuild after its catalog row went) would have been deleted. The union is the safe direction: at worst a genuinely dead file survives.

**The picture backfill writes the RECORD.** It used to attach a document's picture to every catalog ROW of an item - one write per row per catalog. One row per item now, so it is one write. The three rules that make it safe to run are unchanged: documents are never touched, EXACT names only (a backfill that guessed would attach the wrong photo to hundreds of items silently), and an item that already has a picture keeps it.

**Finding duplicates moved onto the Master Data screen.** `master_items.duplicate_ids()` + the `Possible duplicates` chip: records whose name matches another's once spacing is ignored - `CHICKEN BREAST` against `CHICKENBREAST`, `BOX ,
CAMERA` against `BOX , CAMERA`. Keyed on name **and** description **and** unit class, because name alone flagged 254 records (`Hemp rope` φ12/φ15/φ20 legitimately share one). 50 records on the live catalogue. Merging already lives on that screen - tick two rows and press Merge - so this is only the half that finds them, and it looks at every item rather than one document type at a time.

**`last_company` lives on the record (2026-08-20).** The company a PO item was last bought FOR is not a property of the product, but the PO editor fills its company column from it, so the record keeps it as internal memory beside `img_query` - it is not one of the fields the screen shows. Unlike everything else `remember()` learns, the LAST write wins: "last bought for" that never changed would be a lie the first time the item is bought for somebody else. 266 records were backfilled from the PO catalog by the migration.

**Where the four catalogs now stand.** Nothing user-facing reads them any more - not a dropdown, a fill, a paste, the Master Data screen, Health, the duplicate finder, the picture backfill, or the PO's company. They are still WRITTEN on every save, so they are a redundant second copy, and they are still read by `build()` (the rebuild), by the legacy Master Data backend (`_load`, `item_detail`, `save_item`, `merge_items`, `delete_items`, `list_items` - none of which any screen calls) and by the superseded material duplicate review.

**Still to do:** the remaining `Api` methods routed through `master_data.py` (the old Master Data screen's backend, plus health/duplicates/cost-push) still read the four catalogs, and `remember_*_item` still writes them. `save_item` writes the catalogs and NOT the record, so an edit made through that path is invisible to every read above - the screen uses `mi_save`, so nothing user-facing hits it, but it has to go before the catalogs do.

### Two languages, all the way down (2026-08-17)

Waleed: *"for each arabic item there is description in arabic and unit in arabic so that also needs to be included in each items details in master data"*. He was describing a real loss. The PO is written in Arabic and the quotation / delivery note / invoice in English, so one product carries كيلو on one document and `KG` on the other. A single `unit` column kept whichever it met first and **silently discarded the other**: `Celery / كرفس` stored كيلو while the `KG` its own delivery notes used was nowhere in the record. 495 records held an English unit, 285 an Arabic one, and **none held both**.

- `description_en` / `description_ar` and `unit_en` / `unit_ar` - one slot per language, exactly as the two names already were. There is **no** third `description` / `unit` column; the old ones were dropped, because a second copy always drifts (the two `_SIG_IGNORE` lists proved that).
- `is_arabic(text)` / `_slot(field, text)` - which slot a value goes in, decided by the **script of the text**, never by the document that carried it. An Arabic PO line whose unit cell says `KG` really does hold an English unit. Text with no letters at all (`12mm`, `220V`) counts as English and is served to both sides by `prefer`.
- `prefer(en, ar, lang)` - the wanted language, falling back to the other: one known unit beats none. `_row_out` uses it to derive the plain `description` / `unit` keys the API still returns, so they are computed, never stored.
- `_migrate(conn)` - runs from `init()`, so on every connection. Adds the four columns, moves each old value into the slot for its own script, then `DROP COLUMN`s `description` and `unit`. Idempotent.
- `UNIT_WORDS` / `_UNIT_PHRASE` / `unit_counterpart(unit, want)` / `fill_missing_units(apply)` (2026-08-19) - *"fill every unit in both lang with whats right"*. An item that has a unit in one language has the SAME unit in the other, so a blank slot beside a filled one is a gap, not a fact. `UNIT_WORDS` is the **one place in the app a unit word is invented**: the word each meaning is written with per language, chosen from what this catalogue already uses most (`EA` 249 / عدد 145). `fill_missing_units` fills a BLANK slot only, from a unit whose meaning `unit_class` recognises. It will not rewrite a unit somebody typed (`PCS` stays `PCS` and gets عدد beside it), and it will not invent one for an item with no unit at all. Bridged as `mi_fill_units`, and - like the numbering top-up - **`list_records` runs it**, so an item saved this morning does not sit showing `KG` and a blank until the next rebuild. Result: **all 822 items carry both**, 0 half-filled, 0 disagreeing.
  - `_UNIT_CLASS` gained the meanings the catalogue was already using and this table did not know - trip, month, day, hour, drum, strip, sheet, tube, jerrycan, block, length - because water and waste are sold BY THE TRIP, plant is hired by the month/day/hour, and bolts arrive on a strip.
  - **`"متر مكعب"` is listed WHOLE**, and the phrase unit uses WESTERN digits. `unit_class` scans tokens in order and would stop at متر, filing a cubic metre as a metre; and it reads `م3` but not `م٣`. Both were caught by `tests/unit_in_both_languages.test.py` before anything was written - the test that every word `UNIT_WORDS` writes must read back as the class it came from.
  - **`build()` re-derives the fill.** A rebuild reads the four catalogs and a catalog row carries ONE language, so a fill that lived only in the record was silently undone by the next rebuild - the test suite's own rebuilds did exactly that, twice, before it was noticed. Everything the fill does is derived (`UNIT_WORDS`, `_UNIT_PHRASE`, `_UNIT_FROM_NAME`), so re-deriving it at the end of `build()` is repeatable rather than a saved edit.
  - **One overwrite IS allowed**: a unit in the wrong SCRIPT for its slot. `箱` and `千克` came off a Chinese supplier sheet and are not English units by any reading, so they become `BOX` / `KG` - which is not the same as rewriting somebody's `PCS`. A wrong-script unit whose meaning is unknown is left alone and reported, because replacing it would destroy the only record of what the supplier said.
  - `_UNIT_FROM_NAME` reads a unit off the item's own name where it states one - "Monthly vehicle rental service "
    "Pickup truck (4WD)" is hired by the month. A rule rather than a one-off edit, so the rebuild keeps it.
  - **A unit the CATALOGS do not hold survives the rebuild too.** Waleed's ruling that plant hire is `EA` / عدد exists only in the record - a crane has never carried a unit on any document - so without carrying it forward `build()` would wipe all 37, exactly as it used to wipe the item number. Carried by the item NUMBER, which is exact (a name key put `BOOMTRUCK` and `Boomtruck` in one bucket and the ambiguity guard then dropped both), and applied ONLY to a record the rebuild left with no unit, so a document always outranks it.
  - Closed 2026-08-19 on Waleed's ruling: the 37 plant-hire items are `EA` / عدد, and the 9 junk records (`A`, `B`, `Z`, `Delete Test Item`, `Zzql Cabbage`, and unit-less duplicate `Onion` / `Pump` / `Pipe` x2 - none on any saved document) were deleted along with the exact catalog rows they were folded from, found through `master_item_sources`: deleting by NAME would have taken the real `Pump` (255 uses, $135.82) with them. **All 822 records now carry a unit in both languages.**
  - ~~Still open: 46 items have no unit in either language~~ - 38 are plant hire (crane, forklift, generator: never billed per piece, per day and per month interchangeably, and no document said which) and 8 are junk rows (`A`, `B`, `Z`, `Delete Test Item`). Waleed's call.
- `backfill_languages(apply)` - the one-shot that recovers what the old shape dropped, by re-reading each record's own source rows via `master_item_sources`. Fills **blank slots only**; never overwrites, never re-groups, and - unlike a rebuild - never discards what was typed on the Master Data screen. On the live library it recovered 56 English units and 1 Arabic description; 57 records now hold both units.

- `unit_class(unit)` / `units_agree(a, b)` - unit words compared by **meaning**, not spelling: the PO catalog says كيلو where the invoice catalog says `KG`. Without this no Arabic item ever joins its English twin and the fold is a no-op. Two *unrecognised* words must match literally - guessing they are the same silently merges different products.
- `build(apply=False)` - folds every catalog row. Identity is `master_data._ident` (name + description + unit, the same rule the catalogs key a variant on); two folded keys are then JOINED by union-find when one carries the other's name in `name_ar`/`name_en` **and their units agree**. A same-language label equal to the row's own name is ignored - it is metadata, not permission to absorb every other size with that name. A different nonblank `item_no` always refuses a candidate, even when it is the only one; if several candidates remain, the shared number must identify the exact one. This keeps 2T/5T hoists, medium/large first-aid kits, and similarly named variants separate. For conflicting current descriptions/costs/prices, the newest catalog use wins rather than whichever table happened to be scanned first. Pictures are unioned (not first-one-wins), and a record-only picture or `last_company` is carried across. `apply=False` reports and writes nothing.

**Reads / writes** - Reads all four legacy catalog tables and replaces `master_items`; the retired `master_item_sources` table no longer exists. Saved documents and catalog rows are not edited, but the table replacement is still a destructive operation: any record-only fact must be covered by `_KEEPABLE`, and a number not represented in a catalog row can be vulnerable when several rebuilt identities share its names. Back up `library.db` before a production rebuild and verify number→item mappings afterwards; the bilingual cleanup writes its survivor number onto the exact catalog rows so those 46 pairings are rebuild-safe.

- `name_pairs()` - `{normalised name: the other language's normalised name}`, both directions, for records holding both. The FIRST thing the record answers that the four catalogs could not; read by `master_data._join_language_pairs`.
- `remember(name, description, unit, data, source, sub_type)` - the INCREMENTAL half, called from all four `remember_*_item` paths in `database.py` via `_mirror_to_master`. `build()` is a full rebuild and far too slow for a document save. Matching is `_find`: item identity first (with `units_agree`), then the other-language name the row carries - which is how an Arabic PO line reaches the record its English delivery note created. **Never raises**: imported lazily (master_items → master_data → database is a cycle) and wrapped in a bare `except`, because a document save must not fail over the record.
  - **Save-shape boundary (2026-08-20).** Editors name the money cells `cost` and `rate`; the record names them `cost_native`/currency and `price_native`/currency. `api._item_memory_payload` must emit the record pairs as well as the legacy autocomplete keys. Without that boundary conversion a new item was created correctly but both money fields were silently blank. The record remains learn-only: an empty later save cannot erase known money.

**Ordering, and duplicates shown as PAIRS (2026-08-26).** `_SORT_SPEC` replaced the six fixed-direction `_SORTS` strings: one entry per HEADER - `number`, `name`, `name_ar`, `desc`, `unit`, `price`, `cost`, `picture`, plus `used`/`recent` - each an ascending expression, a default direction (text A→Z, money and counts biggest-first) and an "is this blank" test. `_order_by(sort, desc)` builds the ORDER BY; `list_records(..., desc=None)` and `mi_list(..., desc=None)` carry the direction, `None` meaning the column's own default.
  - **The blanks test is ordered FIRST and never flips**, which pins empty rows to the bottom in BOTH directions. Reversing a column must reorder the records that have a value, not parade the ones that do not past the reader twice.
  - The old dropdown offered **"Longest idle"** with no `idle` key behind it, so it silently fell back to Most used. Removed; every option there is now also a clickable header, and `tests/master_data_ui.test.js` fails if the two lists ever drift apart or an option names a key `_SORT_SPEC` does not have.
  - **`duplicate_groups()`** is `duplicate_ids()` with the groups kept instead of flattened: `{key, name, ids, primary}` per set, primary = **most used, then oldest** (the record documents already point at, and the one a merge should keep). A record matching under two names is placed in the FIRST group only - showing it twice would make the screen miscount itself. `list_records(missing="duplicate")` re-orders through `_group_duplicates` so each copy follows its original, stamped `dupe_group` / `dupe_role` / `dupe_of` / `dupe_name` / `dupe_size`. The sort column is deliberately ignored in that view: any other ordering separates the pair, which is the one thing it exists to show. Pinned by `tests/master_data_sort_and_duplicates.test.py`.

**The screen's own API (2026-08-16).** `list_records(search, sort, limit, offset, missing, desc=None)`, `get_record(item_id)`, `create_record(patch)`, `save_record(item_id, patch)`, `assign_numbers(apply)` - bridged as `mi_list` / `mi_get` / `mi_create` / `mi_save` / `mi_assign_numbers`. Waleed's spec verbatim: "one place that holds every item that gets into the app and stores its details - name in English, name in Arabic, description, unit, price, cost and image - nothing more, all under one item number". So the screen shows eight fields and **no source pills**: an item belonged to four catalogs only because it was stored four times.
- `list_records` searches number / EITHER name / EITHER description / EITHER unit in ONE box, and `missing=` filters to records lacking a field - that is how the catalog gets finished off. `missing_counts` is returned for the chips and comes from the SAME `_MISSING` SQL the filter uses, so a chip cannot say 12 and then show 9. `missing="unit"` means neither language; `missing="unit_ar"` is asked only of items that **have** an Arabic name (an item that has never been on a PO does not need an Arabic unit yet, and counting all ~500 of those would bury the 11 that do).
- `list_records` also **numbers anything still unnumbered**. `remember()` deliberately does not: a document carrying its own `item_no` must still be able to teach it, and a placeholder assigned on insert would block that. By the time the screen is open the saves are done - 9 live records were sitting unnumbered before this.
- `save_record` is a PATCH: an omitted field is left alone, an explicit `""` clears it. The four language slots are **literal** - typing Arabic into the `unit_en` box leaves it there, because second-guessing a named field moves the user's text out from under them. A language-free `description` / `unit` is still accepted for callers with one box and is filed by script; a **blank** one clears both slots, since "this item has no unit" cannot leave one behind. Amount and currency are always written together (an amount without its currency is the 1500x mistake). `item_no` and `use_count` are NOT editable - the number is the one thing about an item that never moves.
- An item must keep a name in **at least one** language; clearing both would leave a row nobody could ever find.
- `create_record(patch)` (2026-08-19) - an item entered **by hand**, before any document has used it. Waleed: *"we should also be able to fill it by hand by the way, its a master data at the end."* Until then a record could only appear as a side effect of a document save, which made the screen a report rather than a master. Numbered immediately, and REFUSED when it would duplicate an item already on file - matched exactly the way a document save matches (`_find`: either name, then description, then unit by meaning). The refusal returns `already`, the record it clashes with, so the screen can open that item instead of just saying no. The same name with a DIFFERENT description is a new item on purpose: `Bus` shuttle-only and `Bus` with staff transport are two products at two prices.
- `assign_numbers` gave the 250 records that had none an `M-xxxxx`; all 821 now have one.
- Pinned by `tests/unit_in_both_languages.test.py` (29 tests, mutation-checked **18/18** - overwriting a typed unit, inventing one for an item that has none, guessing at an unknown unit, swapping the two languages, writing into the slot it read from, a dry run that writes, a phrase answered with the plain class word, a cubic metre filed as a metre, and a meaning left with no word to fill from each fail it).
- Pinned by `tests/master_items_screen.test.py` (19 tests) and `tests/master_items_two_languages.test.py` (40 tests, mutation-checked **20/20** - filing everything as English, letting the document rather than the text decide, overwriting a filled slot instead of learning, dropping the fallback, a migration that leaves the old columns behind, a backfill that overwrites or writes on a dry run, a search that ignores the Arabic slots, and a `_describes` loose enough for plain `Rope` to absorb `Rope 12mm` each fail it).

**Status.** Steps: ~~1 build~~, ~~2 keep current on save~~, ~~3a the Master Data list reads the pairing~~, **3b move the remaining reads** (suggestions, Fill button), 4 retire the four catalogs.

**WRITES DELIBERATELY CAME BEFORE READS.** Switching reads first would mean an item saved on a fresh document did not appear in the next document's suggestions until someone rebuilt - a regression on exactly the workflow this rebuild exists to fix. The record is kept current first, while the four catalogs still answer every question, so a failure in this half loses nothing a rebuild cannot recover.

Populated from 1,306 catalog rows → ~1,045 records; `Ballpoint pen` was 7 rows across all four catalogs. Reads still go through `database._suggest_across` over the four catalogs.

**Gotchas**
- **`build()` is the authority; `remember()` is the fast path.** They can drift: `remember` matches one row at a time and may open a new record where the rebuild's union-find would have joined an existing one. That is tolerable *while the four catalogs are still the system of record* - a rebuild reconciles it from scratch and destroys nothing. It stops being tolerable at step 4, when the catalogs are retired and there is nothing left to rebuild FROM; the drift has to be closed before then.
- **A description is compared only against its OWN language's slot.** `Big size` and `حجم كبير` are the same fact written twice and no string comparison can see that, so `_describes` scores per language: the slot is filled → it must match (this is what keeps `Rope 12mm` and `Rope 20mm` two records); the record has no description at all → only an undescribed row fits; the record is described in the *other* language only → a described row fits and an undescribed one does not. `_find` takes the **best** score, not the first fit, so an exact `20mm` beats a 12mm record that merely had nothing in Arabic to disagree with. **A tie is not a fit (2026-10-03):** `_matches` reports `tied` when no record fits exactly and two or more merely do not contradict (`حبل / 12 ملم` against Rope 12mm and Rope 20mm, each described in English only). `remember` then learns NOTHING - it used to write the line's description onto whichever variant was read first, after which the resolver's `details` rule would pick that wrong variant automatically, price and all (76 variant groups were exposed). The user names the variant when the line becomes a DN/invoice, and that review records the description on it. `create_record` still uses `_find` and still refuses a tied duplicate.
- **`remember()` LEARNS, it never forgets.** A save that says nothing about a field must not blank it - the same rule as `_STICKY_MEMORY` above, for the same reason: re-saving a delivery note with the cost cell empty used to erase a cost the catalog already knew. Only blank fields are filled in; `use_count` and `last_used_at` always advance.
- The join is on the name pairing written by the bilingual work ([[09-DATA-MODEL]]), so its coverage is the fold's coverage: 45 records carry both names today, out of 38 confirmed pairs.
- A pair whose **units disagree stays two records** on purpose. The pairing says "same product", never "same line" - fusing a per-kilo cost onto a per-box item is the 1000x error class the cost import was built to avoid.
- Pinned by `tests/master_items_build.test.py` (12 tests; includes the bilingual-marker/same-name variant regression and newest-source selection) plus `ARebuildDoesNotUndoTheMaster` in `tests/master_items_screen.test.py` (record-only fields, pictures and `last_company` survive).

### Audited live catalogue cleanup (2026-09-07)

`scripts/cleanup_material_master_20260907.py` is the reproducible, transaction-safe cleanup used on the shared live `master_items` table. It always makes a timestamped SQLite restore copy and an independent rehearsal database under `data/master-data-backups/`, validates `PRAGMA integrity_check`, hashes every non-material table, and refuses to publish if the live catalogue changes between backup and apply. The publish run is recorded in `data/master-data-backups/20260907-160542-193590/report.json`.

The reviewed pass consolidated 126 duplicate rows into 107 surviving records, removed 12 confirmed test/demo rows, cleared 12 invalid money placeholders, normalized 20 multiline names, and filled 80 blank English fields from a Gemini review with human overrides for Iraqi terminology. It reduced the catalogue from 1,031 to 893 records and Arabic-only names from 157 to 7. Ambiguous specifications and local terms were deliberately retained as separate or blank records. Saved documents, client records, settings, and all other shared tables were hash-verified unchanged during the transaction.

The cleanup is intentionally not a general fuzzy merge. Exact wording alone is insufficient when a description, size, model, unit, or price conflicts, so the remaining Beef Ribs, Chinese Cabbage, Round Table, and 2 Ton wall-split groups remain visible for human review.

---

### `unit_english_map()` - units are looked up, never translated (2026-09-06)

Arabic unit -> the English unit **this catalogue actually uses** for it, exposed as `api().mi_unit_map()`. Not a dictionary: a VOTE. The live catalogue already answers the question 58 different ways and the only real problem is that it answers some of them more than once - `عدد` is recorded as `EA` 410 times, `piece` 24, `PCS` 8, `pcs` 3, and `handle` 11 (somebody's slip). The function folds spellings case-insensitively, sums the counts, and returns the most-used original spelling, which gives `عدد -> EA`, `كارتون -> BOX`, `متر -> M`, `رولة -> ROLL` across 30 Arabic units.

**Why this is not a translation problem.** A unit is not a sentence. A general-purpose translator asked for `عدد` returns "number" - correct Arabic and wrong on an invoice. The business's own vocabulary is the only right answer, it is already recorded, and reading it is local, exact and offline. Used by the PO save-time review ([[07-FRONTEND-SUPPORT]]) to pre-fill the English unit so the only thing usually left to type is the name.

---

**Other spellings that find a record** (2026-09-08) - `remember_aliases(item_id, spellings, source)`, `aliases_by_item()`, `drop_aliases(ids)` over the new `master_aliases` table. A document stores an item's WORDS, not its id, and the only route to a record was its own two names - so `merge_records`, which DELETES the losing rows, deleted the only route to their words and the next document that used them made a fresh record. **Cleaning the catalogue was feeding it.** `merge_records` now records the losers' names (and their own aliases, or a chain of merges loses the first one's) inside the same transaction; `delete_records` drops them so an alias can never point at nothing. An alias is a way IN and never a value - see [[09-DATA-MODEL]].

**`unit_map(want)`** (2026-09-10) - the catalogue's own unit vote, read in either direction: `want="en"` answers Arabic→English (a PO becoming a delivery note), `want="ar"` answers English→Arabic (a quotation teaching the catalogue the half a future PO will need). `unit_english_map()` remains as the `en` case. 30 entries one way, 54 the other.

**`CATEGORIES`** (2026-09-08) - 13 values derived from this catalogue's own vocabulary. `save_record` refuses an unknown one, matches the list case-insensitively and stores the list's own spelling, so a filter can never miss a row on capitalisation. `_TIME_UNITS` is the free local signal: a price per month/day/hour is manpower or hire, never a product.

## backend/master_doctor.py - the catalogue review (2026-09-08)

**Purpose** - What is WRONG with the catalogue, as a worklist. The Master Data screen already reports what is MISSING (its chips carry live counts: no Arabic name 621, no unit 105, no price 195, possible duplicates 8). This module is the three things those chips cannot do: **fix in bulk**, **see a problem that is not a blank field**, and **remember a ruling**.

**Nothing here writes on its own and nothing here touches the network.** `scan` is pure reads, `apply` carries out ONE finding, `dismiss` records a ruling and changes no data.

**Key symbols**
- `scan(kinds=None, limit=0)` - every finding, worst first, each `{kind, key, title, evidence, proposal, item_ids, weight}`. A price disagreement outranks a tidy duplicate: `Round Table` on file at $300 and $400 is the difference between untidiness and a wrong invoice.
- `apply(kind, key, proposal)` - one finding's fix, through `merge_records` / `save_record` so their own checks still run, then records that it happened. A stale id from a screen left open fails rather than editing a stranger.
- `dismiss` / `undismiss` / `decisions` - "not an issue", keyed on the finding's WORDS.
- `price_relation(values) -> "same" | "close" | "far" | "unknown"` - **the only thing about money that may ever be sent to a language model** (Waleed's ruling, 2026-09-08). A model judging "one product or two" is helped enormously by knowing the prices disagree and not at all by knowing they are $300 and $400. Within 5% is "the same price typed twice".

**The findings** (measured on the live catalogue 2026-09-08: 4 / 7 / 24 / 130 / 88)
- `duplicate` - same name, description and unit MEANING. Indexed under **both** names, as `master_items.duplicate_ids` does: the pair that matters most is one product under one Arabic name and two English ones (`سبلت جداري` = "Wall Split Unit" + "Wall-Mounted Split AC"), which no English-first key can see. Proposes a merge into the record the documents already point at.
- `variant_needs_description` - same name, different unit. **Waleed's ruling: these are VARIANTS, not duplicates.** FLOUR really is sold by the bag, the box and the kilo. The defect is that nothing says which is which, so every lookup is a tie and the review popup asks him to choose every single time. It asks for descriptions and **never proposes a merge**.
- `junk_date` - a date typed into a description (`Date:18/07/2026`, mostly on the `Common Laborer` rows). Regex; the date goes, what it described stays.
- `junk_newline` - a newline inside a **name**, and deliberately not a description. Names only: the 25-Seater Bus record holds a four-line route with departure times, and the first version offered to flatten it. 134 findings became 1.
- `category` - proposed locally only where the unit measures TIME (`_TIME_UNITS`) **and the name is a person** (`_PEOPLE`). The first version needed only the unit, and the review screen duly proposed **Manpower for a 4WD Pickup Truck, a Boom Truck and a Cargo Crane** - the unit says HIRED, and only the name says whether the thing hired is a person. 88 proposals became 40, all of them actual people. Everything else is left for a person or the model: a wrong category is worse than a blank one, because nobody re-checks a field that is already filled.

**Reads / writes** - Reads `master_items`; writes only through `master_items.merge_records` / `save_record` and its own `master_review` rulings. **`restore_point()` copies the shared library aside before the first MERGE of a sitting** (`data/master-data-backups/<stamp>-doctor/`), through SQLite's online backup API rather than a file copy, because a live database has a `-wal` beside it. One per sitting, not per finding - thirty merges would leave thirty copies of a 900-record database, and the thing worth going back to is the catalogue as it was before you started. **A failed copy stops the merge**; a non-deleting fix takes no copy at all. Five of the detectors need no model at all, which is the point: an API call for a regex would be silly.

**Gotchas**
- **A ruling keyed on `item_id` is a ruling that comes back.** A merge deletes ids. The key is the group's identities, sorted, so the same problem under new ids is the same problem.
- **A record matching under both its names is reported once**, or the screen lies about how many problems there are.
- `apply` is deliberately one finding at a time. A merge deletes rows; a batch that half-succeeds has no honest report.

## backend/po_catalogue_review.py - persistent PO new-item review state (2026-09-07)

**Purpose** - Remembers which incomplete item rows the user has already handled in the save-time PO popup. It uses `doc_meta(doc_type='po', key='catalogue_reviewed')`, so the state is per company profile, survives an app restart, and is deleted by the existing document-delete cleanup.

**Key symbols** - `reviewed(po_no)` returns the stored normalized row identities; `mark_reviewed(po_no, keys)` validates a list, removes blanks and duplicates, and merges it with earlier identities instead of replacing them. The caller's identity is name + description + unit, which lets a new or changed row on an old PO appear without nagging about rows already dismissed.

**Reads / writes** - Reads and upserts only the existing `doc_meta` table in the active profile database. It does not read or write `master_items`, so this persistence fix cannot modify Materials Master Data. **`doc_type` is a parameter** (2026-09-10, default `po`): `po` for a purchase order completing its English half, `qut` for a quotation completing its Arabic half. The two must not share a key, or reviewing one would silence the other - pinned by `TwoDocumentTypes` in `tests/po_catalogue_review_backend.test.py`.

## backend/translate.py - English suggestions for Arabic items (2026-09-06)

**Purpose** - Asks a language model for the English name/description/unit an item record is missing and, when relevant, which printable Master Data candidate best matches the source description. The **second** place in this app that talks to the internet (`image_search` is the other) and it follows that module's rules exactly: **stdlib only** (`urllib.request`, no new dependency), bounded (one read timeout that covers connect too, a byte cap, 60 items per request and a hard ceiling of 240), **never raises** (every failure is a returned `{"ok": False, "error": ...}`), and **never on a save path** - the caller is always a button a person pressed.

**A suggestion is OFFERED, never applied.** Master Data is what documents print from, so a confident-looking wrong English name would reach a client. Same reasoning as `image_search`'s `MIN_SCORE`: a plausible wrong value is worse than a blank, because nobody re-checks a cell that is already filled. The UI marks every suggested box and the user still presses Save.

**What leaves the machine** - item names, descriptions and units (the words that would be printed on a document anyway), a glossary of the catalogue's own pairs, and candidate English name/description/unit text. Candidate dictionaries are rebuilt field by field. **No** local item ID, item number, document number, client, price, cost, company, quantity, bank/payment data or profile. Pinned by `test_sends_only_words_that_would_be_printed_anyway` and `test_candidate_context_contains_only_printable_words`.

**Configuration - the key is NOT in `data/settings.json`, which is tracked in git.** It comes from `data/secrets.json` (git-ignored) or `GEMINI_API_KEY`/`GOOGLE_API_KEY`:

```json
{"translation": {"api_key": "…", "model": "gemini-3.5-flash-lite"}}
```

`_secrets_path()` resolves beside the SHARED library, not under `config.DATA_DIR` - that constant is the ACTIVE PROFILE's folder, so using it would give Abraj and Eshraqat separate keys and make the feature appear to switch off when the company changes.

**The file is read as BYTES, not text.** On Windows, PowerShell's `>` and `Out-File` write UTF-16 with a BOM. The first key on this machine was created exactly that way and the app reported "no API key" while a perfectly correct-looking file sat on disk. `json.loads` on bytes detects UTF-8/16/32 and the BOM (RFC 4627 §3). Pinned by `test_a_key_file_written_by_powershell_is_readable`.

**Model choice was MEASURED, not guessed** - re-measured 2026-09-10 after "the AI translation is broken". Eight models, three rounds each, run ROUND-ROBIN (this service's latency drifts minute to minute, so consecutive runs of one model measure the weather). A batch of 16 real Arabic item names whose English the catalogue already knows, with those pairs **removed from the glossary** so a model could not copy the answer out of the prompt - without that the benchmark measures nothing.

| Model | median | worst | verdict |
|---|---|---|---|
| **`gemini-3.5-flash`** | **20.1s** | **21.2s** | **the default.** The steadiest of everything measured (19.5-21.2s across every run) and the only tier that reads the Iraqi trade terms |
| `gemini-3.1-flash-lite` | 6.7s | 18.4s | three times faster and the first fallback, but see the trade terms below |
| `gemini-3.5-flash-lite` | 17.8s | 40.6s | the previous default. Slower than 3.1-flash-lite, far more erratic, and made the worst error |
| `gemini-flash-lite-latest` | 14.6s | 15.1s | the same answers as the old default |
| `gemma-4-31b-it` | 50.3s | 51.7s | `3.5-flash`'s answers, 2.5x slower |
| `gemini-flash-latest` | 80.1s | 81.3s | `3.5-flash`'s answers, 4x slower |
| `gemini-3.8-flash` | 83.3s | 83.7s | `3.5-flash`'s answers, 4x slower |
| `gemini-2.5-flash-lite` | - | - | 404 on this account, all three rounds |

**The exact-match score barely separated them** (8/16 against 9/16) and is the wrong measure: most misses are the catalogue's own house wording, where every model agrees with itself and not with us - "Frozen Fish Fillet" for our `Frozen Fillet Fish`, "Hammer" for our `Sledgehammer`, "Safety Helmet Stand" for our `Helmet Stand`. What actually separates the models is the Iraqi trade vocabulary, which is the whole reason this feature exists:

| Arabic | what it is | `gemini-3.5-flash` | a lite model |
|---|---|---|---|
| `كلبس كابسة` | staples, for a stapler | **Staples** | **"Wire Rope Clip"** - rigging hardware, on its way to a client's invoice |
| `بوري شخاط` | rectangular box-section pipe | **Rectangular Pipe** | "Match Pipe" / "Matchbox Pipe" |
| `برغي سيركت` | circuit-breaker screw | **Circuit Breaker Screw** | "Circuit Screw" |
| `خشب سير` | timber batten | **Timber Batten** | "Conveyor Wood" |
| `لوبياء` | cowpea / long green bean | **Black-Eyed Peas** | "Kidney Beans" |

Thirteen seconds of latency is the cheaper side of that trade. `_FALLBACK_MODELS` leads with `gemini-3.1-flash-lite` because a fallback only fires when the primary is busy, and a fast answer a human still reviews beats no answer at all.

**The benchmark measured the COLD case, and that is not the common one.** Its 16 pairs were removed from the glossary so a model could not copy the answer; that is the right way to compare models, and it is the situation for a genuinely new item. But when a pair IS on file the glossary carries our own wording into the prompt, and the model uses it. Asked with the glossary intact, `gemini-3.5-flash` returned `correction tape`, `Frozen Buffalo Topside`, `Sledgehammer` and `Frozen Fillet Fish` - all four verbatim, on two consecutive live runs - where the same model asked cold said "Correction Pen", "Frozen Buffalo Leg", "Hammer" and "Frozen Fish Fillet". **Real accuracy on items we have seen before is therefore higher than the table above.**

Two of those were put to Waleed as possible catalogue errors (`قلم` is a pen, `فخذ` is the leg). **His ruling, 2026-09-10: keep them.** They are the house terms for what he actually sells, and the glossary already makes them win. `HouseWordingWins` in `tests/translate_suggestions.test.py` pins the mechanism: if the glossary ever stops reaching the prompt, the house wording degrades silently and nobody finds out until it is on an invoice.

`_FALLBACK_MODELS` is tried in order, but **only past a "busy" reply** (429/5xx). A 400 or 404 means the request was wrong, and re-asking a second model the same wrong question only spends the user's quota.

**A TIMEOUT is worth another go** (2026-09-10). This model's latency is wildly erratic - the identical request measured 0.9s, 3.6s, 14.3s and over 30s within one minute, on both the current code and the code from two days earlier. A read timeout used to return `busy=False`, which skipped the entire fallback chain and reported "could not reach the translation service" for a service that was merely slow; pressing the button again almost always worked, which is exactly what a retry is. `_post` now separates the two cases: a `socket.timeout` (bare, or wrapped in a `URLError`) is retryable, while a genuine connection failure - no network, DNS, refused - is **not**, because it fails the same way four times and spends two minutes doing it.

The chain is now `[chosen, chosen, *fallbacks]`: the second attempt is the SAME model, because when it times out the model is not the problem, the moment is. `_TOTAL_BUDGET` (75s) bounds the whole batch in WALL CLOCK, not attempts - a person is holding a button down, and four 30s timeouts in a row is not a retry policy, it is a hang. When the budget is spent the message says what to do ("press the button again") rather than only what failed. Pinned by `test_the_REAL_post_calls_a_timeout_retryable_and_offline_not`, which drives `_post` itself: a test that hand-feeds `busy=True` proves nothing about the code that decides it, and mutating the classification out left every other test in the file green.

**It answers in EITHER direction** (2026-09-10). `suggest(items, want)` - `want="en"` is a purchase order's Arabic items needing their English half, `want="ar"` is a quotation's English items needing their Arabic half. One prompt, one parser, one JSON contract; only the language named in it changes, because two prompts drift the way two implementations always do. The echo guard flips with it: Arabic in an English slot was always dropped, and Latin script in an Arabic slot is now dropped too - a model repeating `Office Locker` back has told us nothing. `_glossary(items, limit, want)` scores its word overlap against the side being ASKED about, or an English query against Arabic names finds nothing and the 60-pair budget fills with noise. Units come from `master_items.unit_map(want)`, the same catalogue vote read the other way round.

**The key travels in the `x-goog-api-key` HEADER, never in the query string** (2026-09-08). Google accepts both, so this costs nothing - and a URL is the part of a request that gets logged, lands inside an exception's text and can end up in a toast. `_post` takes the headers as its third argument; `_KEY_HEADER` names it. Pinned by `test_the_key_travels_in_a_header_never_in_the_url`.

**A long list is SPLIT, not refused** (2026-09-08). `_MAX_ITEMS` (60) is now the size of ONE request and `_one_request` does a single batch, so `suggest` walks the list in chunks and re-bases each answer's `index` onto the caller's numbering. Refusing sent the user back to a forty-row popup with the button they had just pressed doing nothing - A-88 alone had 46 rows, so the old cap was one ordinary purchase order away. `_MAX_TOTAL` (240, four requests) is still refused outright, before any call. **If a later chunk fails after earlier ones succeeded, the work already done is returned** with a `warning` the popup shows as a problem; throwing it away would make the user pay for the same tokens twice. Pinned by `test_a_long_list_is_SPLIT_not_refused` and `test_work_already_done_survives_a_later_batch_failing`.

**It no longer touches `socket.setdefaulttimeout`** (2026-09-08). That is process-global and was never restored, so after the first press of the AI button every socket in the app - `image_search` included - inherited a 30s default for the rest of the session. `urlopen(timeout=…)` already governs connect and read for the request that sets it, which is all this ever needed. `_CONNECT_TIMEOUT` is retired. Pinned by `test_the_process_wide_socket_timeout_is_never_touched`.

**Key symbols** - `status()` (configured? never touches the network), `suggest(items)` (the one entry point), `_settings()`, `_secrets_path()`, `_glossary(items)` (existing ar/en pairs, **word-overlap matches first** - `طاولة دائرية` is solved by having seen `طاولة`), `_build_prompt(items, units, glossary)`, `_post(url, payload)`, `_extract_text`, `_parse(text, items_or_count)`. The parser restores one normalized result per requested item after valid JSON, which lets the frontend identify partial replies and perform one bounded repair request.

**Why a model and not a translation API** - a unit is not a sentence. Google Translate answers `عدد` with "number", which is right in Arabic and wrong on an invoice. `master_items.unit_english_map()` already answers units exactly from the catalogue's own vocabulary, and that vocabulary goes INTO the prompt so the model uses the business's words instead of inventing synonyms.

**Calls / called by** - Imports `config`, and lazily `profile_manager` and `master_items`. Exposed as `api().tr_status` / `api().tr_suggest`, called only from **Complete Missing Details with AI** in the shared PO item review ([[07-FRONTEND-SUPPORT]]).

**Gotchas**
- **Arabic in an `_en` field is dropped, not returned.** The model echoing the input back is not a translation, and it must not arrive looking like one - it would fill the box with the very thing the user needs replaced.
- The prompt requires one best concise trade name per item. A partial valid response is normalized with blank fields so the UI can make one focused repair request and then stop.
- Both the response row `index` and `candidate_index` require real JSON integers. A row index is discarded unless it is in the request bounds. A candidate index additionally must be within that row's candidate list. Booleans, numeric strings, fractions, negatives and out-of-range indexes are never coerced into another row.
- `temperature: 0` and `responseMimeType: "application/json"`; `_parse` still survives a fenced block or surrounding prose, because a stray sentence must not lose the whole batch.
- The model name is configurable and Google's own error text is surfaced verbatim - model names change faster than this file, so a wrong one must say so rather than fail vaguely. That is how `gemini-2.5-flash-lite` announced its own replacement ("no longer available to new users… use models/gemini-3.5-flash-lite").
- **Brand names are the one thing it cannot get right**, and no prompt fixes it: `برغي جينكو` came back "Ginko Screw" on one run and "Gisco Screw" on the next. What DOES fix it is the feedback loop - an accepted suggestion is saved with both names and becomes a `_glossary` pair, so a brand corrected once is right from then on. Mining brands out of `name_en` automatically was tried and is too noisy to use (the top hits are "Frozen", "Cleaning", "Common").
- Local knowledge WINS. The unit box is pre-filled from `unit_english_map` before this is ever called, and the frontend never overwrites a non-empty box, so a catalogue answer always beats a suggested one.

---

## backend/master_data.py

**Fill from Master Data speaks the document's language (2026-08-16, step 3c).** A PO is written in Arabic and every other document in English, so pasting an English list into a PO used to fill the English name. `resolve_items` now consults `master_items.name_pairs()` (read ONCE per paste, not per row) and swaps an exact match to the document's own language - name AND fill, so the unit comes across as كيلو rather than KG.
  - **This is not the guess the "a tie is never a match" rule forbids.** The pairing is 1:1 and BOTH sides must be single items; if the other language covers several variants the paste stays on the unambiguous name it actually matched. A name covering several variants (φ12/φ15/φ20) is still an ambiguity and still fills nothing.
  - There is deliberately **no "name not found, try its partner" fallback**: the pairing is built FROM catalog rows, so a paired name is always itself a catalog name and that branch was unreachable. It was written, found untestable by mutation, and removed - the same lesson as `_one_per_name`.
  - Pinned by `tests/suggestions_speak_the_documents_language.test.py` (14 tests; the resolver half is mutation-checked 3/3).

**Editor fill resolver (2026-08-14)** - `resolve_items(..., sell_currency="USD", sell_peg=0.0)` is shared by Excel paste and the top-toolbar **Fill from Master Data** action in Quotation, Invoice and DN. The popup receives `fill`/`suggested_fill`, lets the user choose Item, Description, Unit, Price and Cost independently (all available fields checked by default), and applies the selection in memory only; saving remains the editor's responsibility. Prices use the document's sell currency/peg while costs use its cost currency/peg. Fuzzy suggestions compare the typed text to the item's **name**, never its internal `name|description|unit` identity, so `Shampo` can offer `Shampoo` even when the stored description is long. A one-word close singular/plural or prefix such as `Pipes` offers related item names (Pipe, Pipe Wrench, Scaffolding pipe) as user choices only; it cannot auto-fill.

## backend/image_search.py

**Purpose** - Find a reference picture for an item online (2026-08-13, rewritten 2026-08-14). **The ONLY part of this app that makes a network call.** stdlib `urllib` only, so nothing is added to `requirements.txt` and the app still runs disconnected. Three keyless providers tried in order: **bing** (the ordinary web image index) then **openverse** (Flickr/Wikimedia, CC) then **commons**.

**Only the item's name and description leave the machine**, as a search phrase - never a document number, client, price or cost.

**Key symbols** - `search(name, description, phrase)` (URL only), `fetch(url)` (bytes → data URI), `find_image(...)` (both), `_run_ladder` (one language's walk), `_better` (which of two wins), `translate_to_english`, `_query_terms` (the ladder), `_head_word`, `_score`, `MIN_SCORE = 0.34`, `PROVIDER_ORDER`.

**An Arabic item is searched TWICE (2026-08-14).** Measured on the real PO A-61 (20 Arabic vegetable names), Bing's **Arabic** index is mostly recipe blogs and clip-art: it "found" 17 of 20 and returned a line DRAWING for جزر, PICKLES for خيار, the COLOUR orange for برتقال and a design portfolio for رقي. Finding something is not finding the item. The English index for the same objects is full of clean product photography, so `search` runs `_run_ladder` on the name as typed **and** on `translate_to_english(...)`, then keeps the better via `_better`. They fail in *different* places, which is the whole point - English rescues جزر and ستاند خوذ سلامة (which previously found nothing at all), Arabic rescues خيار, whose translation is the homonym "option" and finds options trading.
- **Google Images itself is unreachable.** Nine plain-HTTP routes (consent cookie, `gbv=1`, old-IE/Lynx/w3m user agents, `udm=2`, the `ichunk` async endpoint, images.google.com) all return an empty JavaScript shell with **zero** image addresses. Real headless Chrome renders the page - 2.4MB, 400 URLs - but only for the first couple of queries before Google throttles it to a 6KB stub, so it cannot carry a 20-row table. Google **Translate** is a different story: keyless, and 23 of 23 items in 12 seconds.
- `_better`: a hit beats a refusal; between two hits the higher score wins; **a tie keeps the original**, because the user's own words are the better guess about their own catalog. Between two refusals the one that at least named a candidate wins.
- `translate_to_english` never raises and never blocks - a translation is an improvement, not a dependency. **One failure disables it for the rest of the run** (`_TRANSLATE_FAILED`); against a dead endpoint, twenty more attempts only cost twenty more timeouts. A result that comes back still in Arabic, or unchanged, is discarded.
- **English names are never translated** - the round trip buys nothing and costs a request per item.

**`phrase` - the user's own wording (2026-08-14).** When supplied it replaces the name and description entirely, skips the reference-code guard and skips translation: the user already said what they meant. It is the escape hatch for what automation cannot know - that "خيار" is a cucumber and not a stock option, that "Bleach" is a cleaning product and not a cartoon. Stored per item by [[03-BACKEND-DATA]] `master_data.remember_search_phrase` and replayed automatically by `api.find_item_image`, which is what makes a correction permanent instead of a mistake permanent.

**The CC libraries alone were close to useless (2026-08-14).** Measured on the live catalog they found **1 item in 15**, and the one they found was wrong: "Toilet Brush" → *"Hillary Clinton Toilet Brush"*. They are art and journalism archives; nobody photographs a floor mop for the commons. Bing indexes the shops, so it went in front - 23 of 24 real items, Arabic included. The two libraries stay behind it because the Bing route is an unofficial scrape.

**Gotchas**
- **Use `/images/async`, NEVER `/images/search`.** The human-facing page answers a plain `urllib` request with **filler** - measured: Italian crime photographs for "Hemp rope", call-centre training slides for "Sledgehammer" - at HTTP 200, with nothing to signal it. Adding a cookie jar made it worse. `/images/async` (what the results grid itself calls while scrolling) returns the real ranked tiles to the same plain request. Headless Chrome against the page was also tried: identical results, ~3 s slower per item, so it buys nothing.
- **Each tile carries JSON in its `m=` attribute** - `t` title, `murl` original, `turl` Bing's CDN thumbnail. **Both links are kept.** `find_image` downloads the original first and falls back to the thumbnail: merchants block hot-linking and their photographs run past `MAX_BYTES`. A table cell prints at ~22 mm, so the fallback is the right size anyway. One malformed tile `continue`s - it must not lose the other 34.
- **Bing's order IS the relevance signal.** `_bing` preserves it and the sort is stable, so equal scores keep rank. Re-ordering throws away the only ranking this module gets.
- **A weak match is REFUSED, not used.** A wrong picture in a filled cell is never re-checked, so the floor exists to leave it empty instead. Bing indexes *everything* and will always return something, which makes the head-word gate and the floor more load-bearing here, not less.
- **`_head_word` is the anchor, and it is script-dependent.** English trails the noun ("Cleaning *Mop*", "Angle *iron*") so the LAST non-qualifier word wins; **Arabic leads with it** ("*ستاند* خوذ سلامة" is a *stand*, for safety helmets) so the FIRST word wins. `_query_terms` sheds words from the matching end for the same reason - shortening Arabic from the front searches the adjective and finds helmets, never the stand.
- **`_WORD` is `[^\W_]+` (Unicode), not `[a-z0-9]+`.** With the ASCII pattern an Arabic name produced **zero** words, so the ladder was empty and the button answered "nothing to search for" - a large part of this catalog could never be searched at all.
- **The diameter symbols (`φΦØø⌀°`) are stripped as punctuation.** Making `_WORD` Unicode-aware had a side effect: "φ20mm" became ONE word starting with a letter, survived the unit stripper, and "Hemp rope φ20mm" searched `rope φ20mm` - which found a *steel wire* rope. Stripping the symbol leaves "20mm" alone, where the unit rule removes it.
- **`_score` grades partial matches in two BANDS, not on one line** - `coverage × 0.6` at ≥ 0.65 coverage, `× 0.4` below. Merchant titles rarely repeat every word of an item name; demanding all of them threw away the precise query and fell back to a vaguer one ("Oil drum binding strap" → "material ratchet"). Three of four passes, one of two does not. It also compares letters with the spaces removed, because "Sledgehammer" is one word in the catalog and two in the wild.
- **`fetch` is bounded on every axis the remote controls**: scheme (http/https only), `Content-Type` must be `image/*`, a byte cap checked against BYTES READ (never a `Content-Length` header, which can lie), and a timeout.
- **A reference code is refused BEFORE any request goes out** (`_looks_like_a_code`, 2026-08-14). Measured on 40 random catalog rows, every wrong picture was of something that is not a physical good, and two were document references: "TDC-DN-7" matched a YouTube thumbnail titled *"TDC - DN - YouTube"*. The word match is **genuine**, so no score can reject it - the only fix is not to search. Three tests, and all three are load-bearing: a code joiner (`-–-/`) must be present, there must be no word of 4+ letters (`_has_real_word` - 3 would let "TDC" through, 5 would lose "pipe" and "film"), and it must not be a **manufacturer part number** (`_is_part_number`: a long mixed letter-and-digit run like `2CD1067G2`). Without the last one it blocked `HIK-IPC-2CD1067G2-L(4MM)(6MP)`, a real Hikvision camera that Bing finds correctly. Flags 22 of 809 live names, all genuine DN references. A coded row that HAS a real description searches the description alone - the code must not ride along.
- **Nothing returned across the bridge may reference itself.** `search` used to hang `candidates = scored[:5]` onto `scored[0]` - a dict inside its own list - and `_bridge_json_safe` ([[02-BACKEND-CORE]]) scrubs NaN by walking the result **recursively**, so every refusal that had candidates surfaced as *"maximum recursion depth exceeded"* rather than "nothing convincing". It only became visible once Bing went in front, because the CC libraries rarely produced candidates to refuse. `best_overall` is now a `dict(...)` copy. Any bridge method building a result that points back into its own parts has this bug.
- **Known limitation:** a one-word name colliding with famous media loses - "Bleach" returns the anime. Bing's photo-only filter was measured and does not fix it. The dialog names every title it inserts, so it is visible.
- Bridge: `api.find_item_image(name, description)` - one item per call so the editor stays responsive; the button ([[05-FRONTEND-SHELL]] `fillImagesFromWeb`) loops. Pinned by `tests/image_search_backend.test.py` (live tests skip when offline). **Its provider mock must patch `bing` too** - it is first, so omitting it does not mock the search, it sends the unit tests to the internet.

**An item is (name + description + unit), not a name (2026-08-13).** `_ident(name, description, unit)` - the same three fields the catalog's own `data_sig` keys a variant on, so this read model agrees with how rows are actually stored. Folding on the NAME alone showed one row for "Hemp rope" Diameter 12mm / φ15mm / φ20mm - and worse, `item_detail` built `variant_rows` from every row sharing the name, so a save fanned across all three, stamped one description onto the others and MERGED them. Both are fixed: `variant_rows` is scoped to the chosen identity, and `item_detail` accepts an identity (a bare name still works, resolving to the most-used and reporting the rest in `sibling_variants`). The dropdowns' own collapse (`_suggest_across`) is keyed on name + description + unit for the same reason - by name alone it hid real variants. `resolve_items` indexes by NAME (a paste only gives one) and treats a name covering several variants as AMBIGUOUS: it offers them and fills nothing, per the standing "a tie is never a match" rule.

**`item_documents(name)` - where an item was actually used (2026-08-14).** Every saved document in the ACTIVE profile holding this item, newest first, with `kind`, `kind_label`, `no`, `date`, `client` (blank for a PO - `pos` has no `client_name` column), `submitted`, and the item's own `lines` (description/unit/qty/rate/cost). **Deliberately separate from `cost_targets`**, which exists to price a change and refuses outright when the item has no usable cost - that is most of the catalog, and "where have I used this?" has to answer for any item. Bridge: `api.im_item_documents`; drives the **Used in** list in [[07-FRONTEND-SUPPORT]]. Pinned by `tests/item_used_in_documents.test.py`.

**Remembered search wording (2026-08-14).** `item_search_phrase(name, description, unit)` / `remember_search_phrase(name, phrase, description, unit)` store the words that actually found an item's picture, under `img_query` in `data_json`. Written to **every** row behind the item (the `assign_item_numbers` fan-out) because the item is one thing to the user even though it lives in up to four catalog tables. `img_query` is in `_SIG_IGNORE`, so a correction cannot fork the item into a duplicate - `tests/item_search_phrase.test.py` pins exactly that. An **empty phrase removes the key** rather than storing a null, so a cleared correction leaves no trace. `_set_data_key` now removes a key when handed `None` for the same reason.

**`_json_col_on(conn, table)` - ask the table, not the registry (2026-08-14).** `CATALOG_SECTIONS` declares `data_json_col=None` for **cat_po_items**, and that is stale rather than deliberate: the `items` table gained a `data_json` column when PO items learned to remember a picture, and the registry entry (which describes description/unit/last_company as explicit columns) was never updated. Every write through `_set_data_key` therefore returned `False` for PO items **silently** - `assign_item_numbers` skipped them and a remembered phrase evaporated. `_set_data_key` now falls back to the real column. Deliberately *not* fixed in the registry, because `data_json_col` also drives what the Master Data browse/edit screens show, and that behaviour is intended. (`_attach_image_ref` already carried the same `or "data_json"` fallback - this makes the two agree.)

**Material numbers (2026-08-13).** `assign_item_numbers(apply)` gives every identity a stable `M-00000` code, written to every row behind it and excluded from `_SIG_IGNORE`. Identity moves when the name, description or unit is edited; the number does not, so it is what the UI re-finds an item by after a rename. `rebuild_variants_from_documents(apply)` puts back identities the catalog lost but the documents still hold - identity only, never a price or cost, because a rate lifted from a document row needs that document's currency to mean anything. Recovered 61 on the live data.

**The PRICE keeps its unit too (2026-08-13).** `price_native` + `price_native_ccy`, mirroring the cost - but converted at the **SELL** rate, not the cost one: `_fill_from` takes `sell_currency`/`sell_peg`, which is the invoice's pinned "1 $ = N IQD" (quotations and DNs always sell in USD). `rate` is still written for USD-native prices so everything that reads it keeps working, and deliberately NOT written for a dinar price - inventing a dollar figure there needs a rate this layer must not choose. `migrate_prices_to_native(apply)` stamped all 735 existing prices as native USD: unlike the cost there was nothing to recover, because every stored `rate` genuinely was dollars (quotes/DNs sell in USD; `api.save_inv` normalises an invoice rate at that invoice's own rate before remembering it).

**A master cost is stored in the currency it was TYPED in (2026-08-13).** `cost_native` + `cost_native_ccy`. Converting at the profile rate on entry baked an error into the item and made it *profile-dependent*: 1000 IQD typed under Abraj stored `cost_usd 0.7143`, and the same row read back under Eshraqat (1500) said 1071 IQD. **The rate belongs to the document, never to the catalog.** `_cost_in(native, native_ccy, want, peg)` converts at the caller's peg - and hands the number over UNTOUCHED when the units already match, which is the accurate common case. `_item_native(item, pick)` falls back to a stored `cost_usd` for rows written by a document save (those knew their own rate, so they are genuinely dollars). The folded `cost` field is now a COMPARISON figure only - derived at the profile rate so Margin and "highest cost" can line a dinar cost up beside a dollar price - and is flagged `cost_is_approx`; it never reaches a document. `migrate_costs_to_native(rate=1400, apply)` did the one-time conversion: 22 costs, 9 of them recovered back to round dinar figures (1000, 1500, 1650, 1900, 2000, 18000, 46000, 180000) by multiplying back by the rate they had been divided by. `money_ccy == "IQD"` is the marker for that group - only `save_item` ever writes it.

**`cost_targets(name)` / `apply_cost_to_documents(name, keys, apply)` (2026-08-13)** - push a master cost back into documents already saved. Setting a cost normally changes only what is AUTOFILLED next time; a document is a record, and silently restating last month's costs would move profit figures already read and reported. So this is opt-in and previewed. Scoped to the **active profile** deliberately - `get_cost_currency`/`doc_iqd_per_usd` read the active profile's DB, so ranging wider would price other companies' documents at this one's peg. Exact-name matching only; each row is written in ITS OWN currency at ITS OWN document's peg and stamped with `cost_ccy`; `updated_at` is deliberately not bumped (the issued document has not changed - the cost is private and never printed, so no PDF is re-made). Pinned by `tests/cost_push_to_documents.test.py`.

**Purpose** - The **Master Data** screen (2026-08-11): one row per item across every autocomplete catalog, with writes fanned back out. Replaces the thirteen-list "Autofill Suggestions" browser (`data_manager`'s `CATALOG_SECTIONS` UI mode), which showed the SAME physical item in up to four places. Measured on the live Abraj catalog: **1,007 items stored as 2,998 rows** across 10 catalogs, plus 1,842 rows of per-cell index. Front end: `static/item_master.js` ([[07-FRONTEND-SUPPORT]]).

**Nothing is migrated.** Those tables are what the editors' autocomplete actually reads; rewriting them is risk with no payoff. This is a read model plus a write fan-out.

**Key symbols**
- `SOURCES` - the 10 catalogs that describe an ITEM, each mapped to the `data_manager` catalog section that owns its row plumbing. `column_memory`, `companies` and `clients` are deliberately NOT here: the first is an index, the other two are not items.
- `_norm(name)` - item identity, delegated to `data_manager._normalize_name` **on purpose**: two views of the catalog that disagree about what counts as the same name would be worse than either alone.
- **`_sub_where(c)`** - a section's `sub_filter` is a ready-made `("quote_type = ?", "materials")` **clause + value**, NOT (column, value). Reading it as the latter selects nothing and fails **silently** - the PO and DN catalogs keep working while quotation and invoice contribute zero items. Pinned by `tests/master_data_backend.test.py`.
- `_variants(conn)` - every catalog row in one shape; the only place the two storage layouts (explicit columns in `items`, a `data_json` blob elsewhere) are reconciled.
- `_fold(variants)` - group into one item: best-known unit/description/price/cost, the most-used spelling as the display name, `sources`, `uses`, `days_idle`, `variant_count`, and `flags`.
- `list_items(search, source, flags, sort, limit, offset)` - the list + facet counts. **Flags AND together** (each chip narrows). `search` covers name, every alternative spelling, description, unit and company - one box.
- `item_detail(name)`, `item_image(name)`.
- `save_item(name, patch)` - applies the patch to EVERY row behind the item, through `data_manager.update_catalog_entry`. **A patch, not a replace**: `update_catalog_entry` rewrites the whole row, so every variant column has to be re-stated or a unit-only edit blanks the description.
- `merge_items(canonical, names)`, `delete_items(names, source="")` - an unknown `source` is **refused by name**, never silently matched to nothing.
- **Material duplicate review - REMOVED 2026-09-05.** `material_duplicate_review()`,
  `apply_material_duplicate_review()` and the ten `_material_*` helpers read the
  four catalogs dropped 2026-08-20, so the review returned `groups: 0`
  unconditionally and the Master Data button reported no duplicate materials
  while `master_items.duplicate_groups()` was finding 64. Its 11-test file passed
  by skipping all 11. Removed with its button, handler, bridges and both test
  files; nothing was shared with the live duplicate path (checked first).
- `health()`, `image_report()`, `purge_orphan_images()`, `clear_cell_memory(doc_type, sub_type)`.
- **`backfill_images(apply=False)`** (2026-08-11) - recovers pictures already sitting inside saved documents' `rows_json`. Items only started remembering a picture on 2026-08-05 (quotations) / 2026-08-11 (POs); everything older was still in the documents but the catalog never learned it. Run on the live data: **283 image cells across 22 documents → 229 items gained a picture, 1,162 catalog rows written, 3.9 MB stored.** Helpers `_document_pictures()` (read-only scan of EVERY profile's document tables) and `_attach_image_ref()`.
- **`resolve_items(names, doc_type, sub_type, cost_currency, cost_peg, want_image, sell_currency, sell_peg, details=None)`** (2026-08-11; candidate picker 2026-08-14; `details` 2026-10-03) - matches a whole pasted Excel block in ONE call and returns what to fill. Four tiers, and the split between them IS the design: `exact` (identical once normalised - this is where Excel's damage lives: a wrapped cell, CRLF, double spaces, case; it cannot be wrong), `contained` (one name's words are a subset of the other's, e.g. `… 5kg`), `similar` (close but not provably the same - returned as a SUGGESTION with `suggested_fill`, `fill` deliberately EMPTY), `new`. Fuzzy similarity compares against the normalized display **name**, not the full variant identity. For a one-word near singular/plural/prefix, `_single_token_prefix_score` adds related names to the suggestion list only; `ordered_suggestions` puts distinct names ahead of duplicate variants, then returns up to `SUGGESTION_LIMIT = 20` choices. When several candidates tie, `suggestions` carries each candidate's own fill values; the UI requires the user to choose one before the five field checkboxes can apply it. `alternatives` remains the compact name/score list for callers. Helpers: `_toks`, `_single_token_prefix_score`, `_preferred_sources`, `_fill_from`, `_identity`; knobs: `SIMILAR_MIN = 0.82`, `PREFIX_TOKEN_MIN = 0.8`, `CONTAINED_MIN_OVERLAP = 0.5`.
  - **`details` - what the line says beside its name (2026-10-03, A-117).** One `{description, unit}` per name, same order. It only ever settles a tier-1 name shared by several variants: `_line_variant(cands, detail)` keeps the ONE candidate whose description in the line's own language matches EXACTLY (`master_items._describes == 2`) and whose unit agrees (`_unit_fits`) - the identity rule a document save files by. "Does not contradict" never counts, a blank description narrows nothing (in an editor blank means "not filled yet"), and two exact winners stay a choice. A-117's `دهن هيدروليك / 20 لتر` now resolves to M-02039 (20 L) instead of asking between it and M-01572 (18 L). Sizes are NOT compared across languages: 29 records write them differently (`طن ونص` vs `1.5 Ton`, `80 سم` vs `0.8m`). Callers: Make DN / Make Invoice and the save-time catalogue review (`po_handoff.js`), Fill from Master Data (`master_data_fill.js`). Item dicts and `_identity` now carry both `description_en` / `description_ar` slots. Pinned by `tests/po_line_variant_backend.test.py`.

  **Identity for write-back (2026-09-05).** A matched result (`exact`/`contained`) and every `suggestions[]` candidate now also carry `item_id`, `item_no`, `name_en`, `name_ar`, `unit_en`, `unit_ar` via `_identity(item)`. `fill` says what to put in a DOCUMENT; these say what may be written BACK, and they are separate on purpose so a write-back key can never reach a document row. `item_id` is what `master_items.save_record` takes - `id` is the item NUMBER (`M-00123`) and `save_record` rejects it, which is why write-back was impossible before. A `similar` result deliberately gets **no top-level identity**: the ids live on the candidates, so a write can only land on the one the user picked. Extends the "a tie is never a match" rule to writes.

  **A TIER IS NOT A READINESS SIGNAL (2026-09-05).** `_items_from_records(lang)` names each record with `master_items.prefer(name_en, name_ar, lang)`, and `prefer` FALLS BACK to the other language when the wanted slot is empty. So a record known only in Arabic is listed under its Arabic name even when `lang="en"`, matches its own name, and comes back **`tier: "exact"` carrying Arabic** - `name_pairs()` cannot rescue it either, because it indexes only rows where BOTH names are filled. `unit` falls back the same way. Verified on the live catalogue: `ثلج`, `خشب سير`, `سبري زنك`, `مواد فوسروك`, `صبغ جوتن` all return `exact` with Arabic names. Any caller deciding "is this fit for an English document" must test the SCRIPT of the value, per field, not the tier. Pinned in `tests/po_make_document_resolve_backend.test.py`; the consumer is `static/po_handoff.js` ([[07-FRONTEND-SUPPORT]]).

**Reads / writes** - Everything through `shared_library.connect()` (`data/shared/library.db`) - **cross-profile**: an edit here changes what both companies' autocomplete offers. Writes go through `data_manager`'s catalog helpers, never raw SQL. Applying a material duplicate review first writes a timestamped SQLite backup beside the shared library; `purge_orphan_images` deletes files from the `item_images` store.

**Calls / called by** - Imports `config`, `data_manager`, `item_images`, `profile_manager`, `shared_library`. Exposed as `api().im_list / im_detail / im_image / im_save / im_merge / im_delete / im_health / im_purge_images / im_clear_cell_memory`.

**Backfill gotchas**
- **Documents are never touched.** `_document_pictures` only reads `rows_json`; the writes go to the shared catalog and the picture store.
- **Exact names only** - deliberately NOT the paste resolver. `contained`/`similar` are fine when a human is watching one paste; a bulk run with nobody watching would mis-attach photos to hundreds of items at once.
- **An item that already has a picture keeps it.** The stored one was set deliberately and recently; an old document must not overwrite it.
- **`_attach_image_ref` UPDATEs `data_json` in place** rather than going through `update_catalog_entry`. That is safe *because* `image_refs` is in `database._SIG_IGNORE`, so the row's `data_sig` - half its primary key - does not depend on it. If that ever changes, this silently orphans every row it touches (pinned by a test).
- Idempotent by construction (content-addressed store); `apply=False` is a preview that writes nothing, and the UI is preview-then-commit.

**Resolver gotchas** (all three are mutation-pinned in `tests/master_data_backend.test.py`)
- **`CONTAINED_MIN_OVERLAP` is load-bearing.** Without it the subset rule lets any one-word master item swallow any query containing that word - "Totally New Widget XYZ" matched "Widget" at 0.25 overlap and filled its price.
- **A tie is never a match.** Two equally-scoring candidates demote to `similar` with both offered; silently picking one is how a wrong price reaches an issued document.
- **Fuzzy matching is name-only and suggestion-only.** The internal identity includes description/unit and must not dilute a legitimate typo score; broad one-word singular/plural/prefix matches are useful to show, but never safe enough to apply without an explicit candidate choice.
- **A partial match does not fill `description`.** The words the query added ARE the description ("Ballpoint pen BLACK"), so the master's would talk over what was pasted.
- **Cost only from `cost_usd`**, converted to the document's cost currency at its peg. A raw `cost` has no recorded currency - filling one puts 84,000 in a cell that should read 60.

**Gotchas**
- **`cost_usd` is the only usable cost.** A row with a raw `cost` and no `cost_usd` predates the 2026-08 currency fix; its currency was never recorded, so replaying it could put 84,000 in a cell that should read 60. Surfaced as the `legacy_cost` flag ("unusable cost") and never presented as a number - 120 items on the live catalog.
- **More than one stored row per item is NORMAL**, not corruption: one per document type, plus one per differing (unit, description). The `split` flag counts them so merging is a choice, not an alarm.
- **Duplicate review is exact compact-name cleanup, not fuzzy matching.** Any nonblank detail conflict is shown as an unticked review choice; it is never auto-merged. The merge remains source-local, so a DN choice cannot change its QUT or Invoice counterpart.
- `_upsert_row` recomputes `data_sig` from the variant fields, so an edit that changes a variant column MOVES the row's primary key. That is why `save_item` goes through `update_catalog_entry` (delete-then-upsert, use_count carried) rather than an UPDATE.
- `image_report()` partitions the store into referenced vs orphaned; `purge_orphan_images` is **explicit only** - a half-written catalog must never look like permission to throw pictures away.
- The old `data_manager` catalog browser still exists and still works; only the dashboard entry point moved. Removing it is a separate change.


## backend/lpo_billing.py - invoices counted per month (2026-10-06)

Waleed: "the counting should be based on how many times that LPO has been
invoiced per month - it can help me know if I am forgetting an invoice for this
month or other month". `months(lpo, index=None, today=None)` is that ledger; it
REPLACED the 2026-10-02 service-period ledger (`periods`, `_service_ranges`,
`summary`, `save_period`), which matched invoice descriptions to anniversary
periods and asked for dates to be confirmed.

- **Contract with an end date:** needs `_term_months(start, end)` invoices, one
  per month. **No end date:** one every month until closed by hand.
- **Counting starts** at the month of the first invoice or the month after the
  start, whichever is earlier: HDEC is billed after each month of service
  (RENT-0004 starts 24 Jul, first invoiced 17 Aug), while RENT-0002 is billed
  the same month (starts 8 Jun, invoiced 30 Jun). A late first invoice does not
  hide the months before it.
- **States:** `invoiced` (count >= 1, a second invoice shows 2), `missed` (the
  month ENDED with none), `current` (this month, none yet - said, never an
  alarm), `upcoming`, `extra` (an invoice dated outside the contract's months -
  listed, so nothing hides). Each expected month carries `start` / `end` /
  `covers`, the service dates handed to the invoice editor.
- **What counts:** issued invoices (a PDF; drafts never), against this LPO's
  identity (`_usage_key`), never `N/A`, in the month they are DATED - as soon as
  they exist. `invoices_not_sent` / `not_sent` come from `submitted_docs`.
- Output read by `list_lpos`: `months_total`, `months_invoiced`,
  `months_unbilled` (missed only; 0 once closed by hand), `oldest_due_month`,
  `oldest_due_date` (the INVOICE month, first day - never the service dates),
  `this_month_due` / `this_month_label`, `term_months`, `billing_complete`,
  `invoices_not_sent`. A materials order gets no months but does get
  `invoices_not_sent`.
- `invoice_index()` reads every issued LPO invoice with its Submitted mark ONCE.
- `lpo_invoice_periods` (table, trigger, migration) is left in place but nothing
  writes it any more.

Pinned by `tests/lpo_month_ledger.test.py` (13 tests on Waleed's real shapes,
9/9 mutation-checked).

`_lpo_row` now exposes decoded `rows` alongside `rows_json`. `save_lpo` preserves
lines when rows is omitted, and accepts an explicit empty list. Draft delivery
notes are excluded from usage and fulfillment. [[17-CLIENT-LPO-FIXES]].

## backend/statements.py - monthly client statement (2026-10-06)

One PDF per client per month, from the **Monthly statement** button in Insights
(`static/analytics.js` `openStatement`). Replaces Codex's one-off TDC script
(`docs/reports/2026-10-06-tdc-statements.md`), which had the client and the month
written into it.

- `gather(client, year, month)` - decisions only, no files: the month's ISSUED
  invoices of that client (`insights._collect`, so the money is the Insights
  money), date order; per invoice the copy to attach (a PAID invoice uses its
  returned copy from `signed_copies`, an unpaid one always the PDF we issued),
  its delivery note (`invoices.source_dn_no`, signed copy preferred) and
  purchase orders (`po_fulfillment` rows with `source_kind='inv'`); purchase
  orders dated this month but invoiced in another month; the attachment order
  (invoices, DNs, POs, each once).
- `build(client, year, month, out_path=None)` - summary page as HTML through
  `pdf_engine` (landscape A4, each number an `<a href>` to a placeholder URI),
  returned photos as A4 pages in ONE more Chrome pass, pypdf joins everything and
  `_rewrite_links` turns each placeholder into a jump to that document's first
  page (Chrome placed the links, so nothing is searched for). Bookmarks per
  document, then `pdf_lock.lock`. Missing files are reported on the page and not
  linked. A file open in a viewer returns "close it and try again".
- `output_path` - `<documents root>\REPORTS\<year>\<MON>\<Client> - <Month> <year>.pdf`
  (overwritten on a re-run; the popup says so).
- `statement_months(client)`.

Real-data check: TDC August/September 2026 reproduce Codex's 64 / 42 pages and
counts; totals differ by cents because the statement uses Insights' line maths
where the script used `grand_total`. 81 links checked against their target
pages. Pinned by `tests/statement_backend.test.py` (19).

## backend/pdf_lock.py - every PDF locked (2026-10-07)

Waleed: the stamp and signature "might get stolen". `lock(path)` re-writes a PDF
in place with **AES-256**, an empty user password (clients open it with none) and
a random owner password; permitted: print (full quality) and accessibility; NOT
permitted: modify, copy/extract, comment/annotate (an annotation can carry an
image), fill, assemble. Called LAST by every writer: the four generators, the LPO
acknowledgement (`lpo_ack`) and `statements`.

**Profile split (2026-10-17).** `lock()` is the one final-protection step, so the
Abraj/Eshraqat difference lives here: for `config.ACTIVE_PROFILE == "eshraqat"` it
delegates to `pdf_certify.finish_eshraqat` and returns, never applying AES. See
`backend/pdf_certify.py` below. Abraj is unchanged.

- Settings live in the git-ignored `data/secrets.json` under `"pdf_protection"`
  (same file as translate's key): `owner_password` (made on first use; losing it
  costs nothing - the app never unlocks its own files), `"lock": false` to switch
  it off on a machine, `"allow_comments": true` if a client must sign inside the
  PDF.
- **Honest limit:** a PDF that opens without a password can be unlocked with a
  free tool by someone determined. It stops ordinary people and ordinary PDF apps;
  `doc_verify` is what catches a forgery that gets past it.
- Needs `cryptography` (AES) - now in requirements.txt.

## backend/pdf_certify.py - Eshraqat's unbranded protection (2026-10-17)

Waleed's rule: an Eshraqat PDF must not read as made by the same tool as Abraj's
(no shared verification mark, no AES fingerprint, no renderer name). `lock()`
routes every Eshraqat document here instead of AES. `finish_eshraqat(path)`:
- **scrub_metadata** - blanks the `/Info` keys Chrome fills to name the renderer
  (`/Creator ...HeadlessChrome...`, `/Producer Skia/PDF`) plus `/Author`; keeps
  `/Title` (the number) and the dates. Chrome's `--print-to-pdf` writes no XMP
  packet, so `/Info` is the whole story.
- **certify** - an INVISIBLE certification signature (Acrobat's "Certify
  (Invisible Signature)") with Waleed's own `.pfx`, DocMDP `NO_CHANGES`, via
  `pyhanko`. The file then reads as a normally-signed PDF and is sealed against
  edits. `_silence_vendor()` zeroes pyHanko's `VENDOR` so it does not write its
  own name into `/Producer` (the only place its name would otherwise leak).
- Config under `"eshraqat_sign"` in `data/secrets.json` (git-ignored): `enabled`,
  `pfx_path`, `passphrase`. Disabled or no usable `.pfx` -> scrubbed but unsigned
  (still never AES-locked). A certification signature seals the bytes, so this is
  the LAST write; nothing may touch the PDF afterwards.
- Verifying an Eshraqat document is intentionally not offered (`doc_verify`
  skips the profile too). Pinned by `tests/eshraqat_pdf_certify.test.py` and
  `tests/eshraqat_no_verification.test.py`. Needs `pyhanko` (requirements.txt).

## backend/doc_verify.py - a verification code on every page (2026-10-07)

`Verification: INV-MT-TDC-030 | 7K2F-9QXA` at the foot (bottom centre, 6mm up)
of EVERY page of every PO, quotation, delivery note and invoice - including
appended delivery notes and merged scans - written onto the finished PDF by
`stamp_pdf` (Helvetica, no font embedded). It is NOT in the HTML: the templates
position their pages absolutely and a fixed HTML footer landed under the
letterhead.

- **Abraj only (2026-10-17).** `stamp_pdf` returns early when
  `config.ACTIVE_PROFILE == "eshraqat"`: no line is drawn and NO code is logged,
  so nothing ties one Eshraqat file to another or to this machine. Waleed's rule
  is that Eshraqat PDFs must not read as generated by the same tool. Verifying an
  Eshraqat document is therefore not offered. The other two fingerprints (the
  Chrome `/Creator` + `/Producer` metadata and the AES-256 lock) are handled by
  `pdf_certify.py` (below), which scrubs the metadata and replaces AES with an
  invisible certification signature. Gate pinned by
  `tests/eshraqat_no_verification.test.py`.

- `facts(kind, doc)` - kind, number, date, client, currency and every printed
  line (all row cells except keys matching `cost|image|^_`); `code_for` = HMAC-
  SHA256 of those under `pdf_protection.verify_key` (secrets.json), base32, 8
  characters. A changed quantity, date, client or number changes the code; a
  changed COST does not (costs are not printed).
- Every printed code is logged in the profile table `doc_verify_log` with what
  that version said ([[09-DATA-MODEL]]); `last_seen` marks the latest printed.
- `verify(doc_no, code)` answers from the log: genuine (latest), genuine but an
  OLDER version (shows the latest), NOT GENUINE (shows what our document says),
  or no such document in this company. Recomputing from the current record is
  only the fallback when the log is missing. Documents made before 2026-10-07
  carry no code.
- UI: Settings > About > Quick access > **Verify a document**
  (`static/doc_verify.js`).

Pinned, with the lock and the stamp overlap, by `tests/pdf_protection.test.py`
(10 checks, 12/12 deliberate breaks caught).

## backend/gate_brief.py - what the launch card and the switch gate say about the day

**`card(today=None)` (2026-10-20) - the launch card's two rows.** Runs in the launch card's OWN process (backend/launch_gate.py `_load_card`), so it costs the app nothing; ~0.2 s of imports + ~30-70 ms of queries, measured on both companies. Opens the profile database **read-only** (`file:...?mode=ro`, pinned). Each part is worked out on its own; one that raises is left out and named under `errors`, the other still shows.
- `tally(conn, today)` - "4 quotations · 26 POs · 1 DN · 30 invoices": made this month, all four always shown (zeros too), short nouns. Its tables are `analytics._DOC_TABLES` (pinned by a test, NOT imported - importing analytics pulled `clients` and `signing` into the card's process; `brief()` now imports it lazily for the same reason).
- `agenda(conn, feed)` - the ONE most pressing thing, in Waleed's order: **Collect** (unpaid: money per currency, count, oldest) -> **Deliver & invoice** (client POs) -> **Follow up** (bids, with the days left before the 14-day auto-loss) -> **Submit** (unsent documents, named by kind when they are all one kind) -> "All caught up". `feed` is `database.agenda_feed()`: the dashboard's own buckets, so the card and the To-Do never disagree.
- A third row, "last edited", was built and dropped the same day (Waleed: useless) - it is not computed (pinned).

**`brief(today=None)` (2026-10-02) - the page gate's Welcome door, shown after a company switch.** Waleed picked these from nine candidates, in this order: **money waiting for payment**, **documents not submitted**, **this month so far**.

- `brief(today=None) -> {"ok", "lines": [{key, value, text, detail}], "all_clear"}`. Lines 1-2 are `database.dashboard_needs_attention()`'s own `unpaid` and `unsubmitted` buckets (muted items already excluded), so the gate can never disagree with Needs Attention; `tests/gate_backend.test.py` checks that on live data.
- **Money is summed per `sell_currency`** and printed `"USD 1,500 + IQD 2,500,000"` by `money()`; never added across currencies, never a bare number (house rule). Unpaid sums `invoices.sell_total`.
- `unsubmitted` names its kinds when mixed: "29 documents ... 15 invoices, 14 delivery notes, oldest 138 days" - it is NOT invoices only.
- "This month" counts the four tables in `analytics._DOC_TABLES` (the dashboard card's own list - an earlier draft guessed table names and swallowed the error into a silent zero; now a missing table raises).
- Zero lines are dropped; nothing at all is `all_clear` ("All clear - nothing is waiting for you").
- **Gotcha:** `with sqlite3.connect(...)` only commits, it does not close - `brief()` and `card()` wrap it in `contextlib.closing`. The scratch-database test found the leaked handle on Windows.

## backend/launch_gate.py - the launch card drawn by Windows at start-up (2026-10-20)

Waleed picked concept C of the launch-screen proposal on 2026-10-20 to replace the doors (2026-10-02, concept D): **only a card on screen, the desktop visible behind it** - the company logo on a **white** panel, and on the company-coloured panel "Good evening, Waleed", the date and company, two rows (THIS MONTH / AGENDA, from `gate_brief.card()`), and a loading bar with the step written under it. ~645 x 255 px on a 1080p screen (the first build was ~860 x 380 - "too large"). His rule above all: it must never slow the start-up. **Switching company and lock still use the page's doors** (static/gate.js); only the start-up changed. Design record: `docs/designs/splash-concepts.html`.

- **Why native:** the page cannot draw anything until WebView2 has loaded it (~1.3-2 s). The card is a Win32 **layered** window (`WS_EX_LAYERED | TOOLWINDOW | TOPMOST | NOACTIVATE`, cursor `IDC_APPSTARTING`) painted from a bitmap with `UpdateLayeredWindow`, up ~0.28 s after the click. `WS_EX_TOOLWINDOW` also keeps it out of Chromium's occlusion check, so the page keeps rendering under it.
- **The app window loads invisibly - `hold_window(window)` / `reveal_window(delay_ms)` (app side).** WebView2 only finishes starting while its window is SHOWN, so it cannot be hidden until ready (that delays everything); shown normally, the half-built window appears round the card at ~1.4 s. So `before_show` sets **Opacity 0** - shown, invisible from its first frame, clicks pass through - and `Api.gate_ready` -> `reveal_window(HOLD_MS)` fades it to exactly 1.0 over `WINDOW_IN_MS = 200` under the fading card (WinForms drops the layered style itself at 1.0, so the session is an ordinary window). A `WINDOW_SAFETY_S = 20` timer and `main()`'s error path reveal it whatever happens; with no card up, nothing is held. Timeline mark `window_held`. **Never a window region (Waleed's screenshot, 2026-10-20):** an empty region paints faster (Opacity makes the window LAYERED: ~0.08 s slower paint), but set before `Show()` the window is never maximized, and set after it WebView2's picture is composited **31 px low** (title bar + border) for the whole session - white strip on top, bottom cut off - while every window rect, the client area and the page viewport read correct. Only a screenshot shows it: 3/3 runs each, Opacity puts the page's first row at y=43 like no hold, any region at y=74. Pinned (no `SetWindowRgn`). **Gotcha:** pywebview's `Event` passes the window ONLY to a parameter named `window` and calls any other handler with no arguments - the TypeError is logged and swallowed. The first build named it `win`; the window quietly showed half-built. The tests dispatch through pywebview's real `Event` for that reason.
- **The child process** (`launch_gate.py --child <app dir>`, spawned by `start()` at below-normal priority); the app holds a `GateProcess` and talks to it with JSON lines on stdin: `stage_done(n)`, `add_marks`, `open()`, `close()`. **The app sends no data** - the child reads its own rows on a daemon thread (`_load_card` -> `gate_brief.card()`, read-only) while it draws. Started at once: deferring it until the app window exists was measured (5 launches) - ready did not move, the rows just came at ~1.95 s instead of ~0.6 s. Inside the child, `_ChildGate.run` has its own message pump and a daemon `_reader` thread on `sys.stdin.buffer`.
- **The child ends with `os._exit`, never `sys.exit` (2026-10-07).** The app keeps the pipe open, so `_reader` is still blocked in `sys.stdin.buffer` at the end; normal interpreter shutdown then cannot take stdin's lock and ABORTS ("Fatal Python error: _enter_buffered_busy ...") - the "Just-In-Time Debugger" box Waleed saw after every start-up from 2026-10-02. Pinned by `tests/gate_child_exit.test.py`, which since 2026-10-20 also fails on a `gate-error.txt` (the child swallows its own errors so it can never take the app down - which also hides them) and requires `card_data` in the run's timeline. Anything the child must keep has to be WRITTEN before it returns. `start(app_dir)` returns `None` (no card, app unaffected) on any problem, without a registry, or with `DOCUMENTS_HUB_NO_GATE=1`. A **45 s safety timer** closes it if "ready" never comes.
- **Drawing:** `_Painter` (Pillow, imported lazily - it costs ~0.3 s) in units of `u = client height/1040`, sizes in `C`; `card_geometry()` is the layout as pure arithmetic (no Pillow). Two pictures are cached under `%LOCALAPPDATA%/BusinessHub/gate/` so a launch shows the card with no drawing: `card-chrome-<key>.bgra` (the card with no words, the same every day) and `card-first-<day key>.bgra` (today's opening picture: greeting, date, empty bar - **never the rows**, which are fresh each launch). Bump `DESIGN_VERSION` (now `card-2`) when the drawing changes. `_sweep_cache` deletes the doors' old pictures and any card picture older than two days.
- **Frames:** only what moved is redrawn (`present_rows`, a dirty band); a piece that has landed is baked into the still picture once, drawn at full strength, and never redrawn; the bar alone redraws a thin strip at most 30/s. Fades (`_Layer.fade`) pass NO source DC, so Windows reuses the picture it has. **Gotcha:** `ImageDraw.Draw(img, "RGBA")` on an RGBA image SETS pixels instead of blending, so the bar's faint track drawn straight onto the card punched a see-through hole; it is drawn on its own small picture and laid over (pinned). Text layers are created on their own colour at alpha 0 so white text never gets dark fringes.
- **Timing:** appears (180 ms fade) -> rows rise in when `card()` answers -> on `open` the bar finishes for `HOLD_MS = 200` (same as gate.js, pinned) -> the card fades in `CARD_OUT_MS = 180`, drifting up, while the window fades in under it (`WINDOW_IN_MS = 200`). `Api.gate_ready` returns `settle_ms = HOLD_MS + WINDOW_IN_MS`. It leaves on time even if a row is still rising.
- **Which screen:** `_work_area()` = the monitor under the mouse, because pywebview opens the window with WinForms' CenterScreen, which picks that monitor. The primary monitor was wrong on Waleed's two-screen desk.
- **Colour blind (Waleed):** the step is written under the bar ("Loading the interface", "Step 3 of 4", then "Ready / Done") - the bar is never the only signal.
- `gate_info()` reads the company, label, colour (`ProfileManager.PROFILE_ACCENTS`), logo and the user's name (from `id="dashUser"` in index.html - the one place it is written). No theme: the card looks the same in light and dark.
- `note(name)`, `page_marks(marks)`, `record_ready_without_gate()` and `_seconds_since_process_start()` produce the start-up timeline (`%LOCALAPPDATA%/BusinessHub/Diagnostics/gate-timeline.json`, see [[02-BACKEND-CORE]] `gate_ready`).
- **Cost, measured 2026-10-20 (5 alternating launches each, seconds from the click):** dashboard ready median **2.44 with the card vs 2.20 with no start-up screen at all** (best runs 2.35 vs 2.17) - ~0.17-0.24 s, the same as the doors (0.16): ~0.08 s before the window appears (the card's process sharing a busy 4-thread CPU; deferring its work was measured and did not help) and ~0.08 s of slower painting in the invisible (layered) window - the price of not showing a half-built window. Card on screen at **~0.28**, its rows at **~0.60**, the dashboard shown **~0.4 s** after ready (the doors took 0.76-0.86 s).

## backend/activity_log.py - the activity and problems log (2026-10-23)

**Purpose** - Waleed: "know what broke, whose fault it was, and what was done before it". One log for
everything, in `<data>/logs/activity.db` (data root found like `secrets.json`; the gateway and engines
`configure()` it explicitly). Sources: `call` (every Api method), `screen` (views, buttons, messages
shown, screen errors, reports - `static/activity_capture.js`), `pc` (the launcher's open/print/copy
results and network failures), `server` (engines starting, stuck, lost), `login`. Every row says whose
fault in one word: `ok`, `refused` (the app said no on purpose), `app_error` (a bug - crashed or
swallowed), `server_error` (OSError, sqlite3.OperationalError, MemoryError, timeouts: the machine),
`network`, `warning` (an error message shown), `report`, `info`.

**Key symbols**
- `start_call / started / end_call` - wrapped around every call in `Api.__getattribute__` (backend/api.py).
  Nested calls (one method calling another) belong to the outer one (thread-local depth); the log's own
  methods (`NOT_LOGGED`) are skipped. Records ms, `waited_ms` (time spent behind `_PROFILE_SWITCH_LOCK`),
  args trimmed by `summarize()` (data URIs -> "<file N KB>", keys like pass/secret/token/api_key/pfx ->
  "<hidden>", long text cut), `ref` (a document number from the args/result), and a plain `describe()`.
- `watch_exceptions()` - `sys.monitoring` EXCEPTION_HANDLED (tool id 4). When a call returns
  `{"ok": False, "error": msg}` and an exception it caught matches `msg`, the row is app_error /
  server_error WITH that traceback (`detail.swallowed`); otherwise `refused`.
- `record(...)` - one row, never raises; a background `_Writer` thread batches inserts (problems wake it
  at once); `flush()` for tests and shutdown; `prune()` every 6 h: routine ok rows after 30 days,
  everything after 400.
- `client_events({"sent", "events"})` - what screens and PCs send (`Api.log_client_events`), placed on the
  server's clock in the sender's order.
- `list_events(filters)`, `event_detail(id)` (the row + the same person's trail 5 min before),
  `people()` - for the Activity screen (admin only, see backend/api.py).

**Reads/writes** - `<data>/logs/activity.db` (table `events`, see [[09-DATA-MODEL]]); context from
`set_context()` (the engine sets person / PC / call id per request; desktop falls back to
`BUSINESSHUB_USER` / the computer name); company from `config.ACTIVE_PROFILE`.

**Gotchas**
- NEVER hold a caught exception object. Its traceback keeps every frame alive, and Python frees some
  resources only when a frame goes: subprocess closes a pipe end that way (its `_on_error_fd_closer`
  generator), so the first version made every Chrome PDF render in an engine wait 60 s for an end of
  output that never came. `_Seen` copies type, text and a frame-free `StackSummary` on the spot, and
  StopIteration / GeneratorExit are skipped. Guarded by `tests/activity_log.test.py`
  (`WatchingNeverHoldsExceptions`, two tests).
- Logging must never break or slow the app: every write path is guarded, rows queue in memory, a full
  queue drops (counted in `_Writer.dropped`).
