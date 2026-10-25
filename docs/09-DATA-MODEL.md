# 09 - Data Model, Settings & On-Disk Layout

The single reference for DATA in Business Hub: the full SQLite schema, the generated-document folder convention, the config/settings surface, and the JSON row/meta shape carried across the pywebview bridge.

**Covers:** backend/database.py, backend/migrations.py, backend/config.py, backend/po_numbering.py, backend/quote_numbering.py, backend/shared_library.py, per-profile `settings.json`, static/lottie/

Related: [[01-ARCHITECTURE]], [[03-BACKEND-DATA]], [[04-BACKEND-DOCGEN]] - the PDF generators and document metadata helpers consume `config.QUOTE_TYPES`/`INV_TYPES`/`DN_*` column schemas documented here. The multi-company profile subsystem (`profile_manager.py`, `profile_numbering.py`, `brand_assets.py`, `eshraqat_render.py`, bootstrap migration, profile switching) is owned by [[13-PROFILES-BRANDS]] - this doc only covers its data-layer touch points.

---

## Which database? (profile-scoped paths)

Since the multi-company profile split, **the live DB is per-profile**:

- `config.ACTIVE_PROFILE = profile_manager.active_profile_id()` - read from `data/profile-registry.json` (`{version:1, active_profile, profiles:[{id,label}]}`; valid ids `abraj` | `eshraqat`).
- `config.DATA_DIR` = `config.PROFILE_DIR` = `data/profiles/<id>/`; `DB_PATH = <profile>/po_tool.db`; `SETTINGS_PATH = <profile>/settings.json`; `ASSETS_DIR = <profile>/assets`.
- Root `data/po_tool.db` and `data/settings.json` are **frozen legacy copies**, used ONLY when env `DOCUMENTS_HUB_LEGACY_FALLBACK=1` (set by `app.main()` when profile bootstrap fails). Every `profile_manager` path function branches on that env var.
- **`master_items` is not in `SHARED_SCHEMA`** - `master_items.init(conn)` creates it lazily on first use. Any code that builds a shared library from scratch (notably `tests/isolated_client_data.py`) must call it, or every read dies with `no such table: master_items`.
- **The four document catalogs `items` / `quote_items` / `dn_items` / `inv_items` were DROPPED on 2026-08-20** - from the shared DB *and* every profile DB, and from `SHARED_SCHEMA` / the profile `_SCHEMA` so they are never recreated. The item **record** (`master_items`) is the only item store now; a document save writes only it. What remains cross-profile in `data/shared/library.db` (`shared_library.SHARED_TABLES`) is `companies`, `clients`, `column_memory`.
- `app.main()` runs `profile_manager.manager().bootstrap_profiles(...)` **before** importing `backend.database`/`config` - a one-time staged migration of legacy data into `data/profiles/{abraj,eshraqat}/` + `data/shared/library.db`, guarded by marker `data/profile-migration-v1.json`, a pending-journal, and file lock `data/profile-migration.lock`. Details in [[13-PROFILES-BRANDS]].

**Import-order invariant:** `backend/config.py` binds `ACTIVE_PROFILE`/`DB_PATH`/`SETTINGS_PATH`/`ASSETS_DIR` at import from `profile_manager` - profile bootstrap must complete before importing any config-bound module, and any profile switch or settings save requires the `importlib.reload` dance (`api._reload_profile_runtime()` reloads config + 16 backend modules, then `init_db()` + `migrations.run_all()`).

---

## Quick map: table -> doc type / feature

Tables marked **(shared)** are read/written in `data/shared/library.db`; everything else lives in the active profile DB.

| Table | Serves | PK |
|---|---|---|
| `items` **(shared)** | PO item autocomplete (also reused by quote save) | `(name, data_sig)` |
| `companies` **(shared)** | PO "للشركة" company-name autocomplete | `name` |
| `pos` | archived Purchase Orders | `po_no` |
| `clients` **(shared)** | client autocomplete (quotes/DN/INV) | `name` |
| `client_records` **(shared)** | the client IDENTITY, one row per client (2026-09-06) | `id` (uuid hex) |
| `client_aliases` **(shared)** | every spelling a client has been called -> its identity | `alias_key` (normalized) |
| `client_preferences` **(shared)** | that client's commercial defaults, PER company profile | `(client_id, profile_id)` |
| `client_master_meta` **(shared)** | one-time markers (`presets_seeded`) | `key` |
| `quotes` | archived Quotations | `quote_no` |
| `quote_items` **(shared)** | per-type quote item autocomplete | `(quote_type, name, data_sig)` |
| `dns` | archived Delivery Notes | `dn_no` |
| `dn_items` **(shared)** | DN item autocomplete | `(name, data_sig)` |
| `invoices` | archived Invoices | `inv_no` |
| `inv_items` **(shared)** | per-type invoice item autocomplete | `(inv_type, name, data_sig)` |
| `submitted_docs` | "submitted" flag for po/quote/dn/inv (Phase Q) | `(kind, doc_no)` |
| `paid_invoices` | invoice-only paid flag | `inv_no` |
| `attention_ignores` | Needs-Attention mute, per doc per category (Phase X.30) | `(kind, doc_no, category)` |
| `po_fulfillment` | PO items marked delivered (via DN) / paid (via INV) (Phase Y) | `(po_no, item_index, status_kind)` |
| `doc_cost_meta` | per-doc cost-entry currency USD/IQD (Phase R) | `(kind, doc_no)` |
| `doc_columns` | per-document custom column layout (Phase T) | `(kind, doc_no)` |
| `doc_meta` | per-document metadata: signing overlays, layout flags, and handled PO catalogue-review rows | `(doc_type, doc_no, key)` |
| `deleted_docs` | tombstone ledger for deleted active documents; preserves consumed numbers without showing records in active screens | `(kind, doc_no)` |
| `column_memory` **(shared)** | universal per-cell value autocomplete (Phase G.14) | `(doc_type, sub_type, col_key, value)` |
| `document_counters` | permanent per-profile numbering sequences (`kind` → `last_value`); used only when `ACTIVE_PROFILE=='eshraqat'` via `backend/profile_numbering.py` | `kind` |
| `client_lpos` | LPOs RECEIVED from clients (2026-07-31) - the one document in the chain we don't produce | `(client_name, lpo_no)` |
| `_migrations_applied` | idempotency ledger (migrations.py) - one per profile DB | `key` |

---

## backend/database.py

**Purpose** - The entire SQLite layer: schema definition, all CRUD/autocomplete helpers per doc type, and the Phase N/R costing-and-profit engine (per-row costs inside `rows_json`, computed-on-read profit). Catalog suggest/remember helpers are routed to the shared library DB.

### Schema (module constant `_SCHEMA`, applied by `init_db`)

Every table is `CREATE TABLE IF NOT EXISTS`. Columns below reflect the CREATE statements; migrations add some columns to older DBs (see migrations.py). The catalog tables still appear in `_SCHEMA` (so every profile DB has them), but the live copies are in the shared DB.

- ~~**items** / **quote_items** / **dn_items** / **inv_items**~~ - the four document catalogs, **DROPPED 2026-08-20** (see the note above). They held one autocomplete row per (name, data_sig) variant per document type; everything they carried - names, description, unit, cost, price, picture - now lives once on the `master_items` record.
- **companies** - `name TEXT PK NOCASE`, `use_count`, `last_used_at`.
- **pos** - `po_no TEXT PK`, `created_at`, `updated_at`, `po_date` (DD/M/YYYY as shown), `year INT`, `month INT`, `pdf_path`, `notes`, `rows_json` (JSON list of `{name,description,unit,qty,company}`), `source_quote_no` (optional PO reference to a quotation). Index `idx_pos_month(year, month)`.
- **clients** - `name TEXT PK NOCASE`, `use_count`, `last_used_at`. Seeded from `config.QUOTE_CLIENTS_SEED` **once** (`client_master.seed_once`, guarded by `client_master_meta.presets_seeded`) - **into the shared DB**. It used to be re-seeded on EVERY launch, which resurrected preset clients the user had removed. Since 2026-09-06 this table is a **compatibility index** (it still carries `use_count`), not the source of truth: autocomplete reads `client_records` via `client_master.suggestions`.
- **client_records / client_aliases / client_preferences / client_master_meta** (2026-09-06, owned by `backend/client_master.py` - see [[03-BACKEND-DATA]]) - the client identity, its alternative names, and its per-profile commercial defaults. Created by `client_master._init` on first connection, NOT by `SHARED_SCHEMA`. Alias keys use `client_master.normalize` (whitespace-collapsed Unicode casefold), which is **not** `COLLATE NOCASE` - a query that needs to match them must call the registered `client_key(...)` SQL function, never the collation.
- **quotes** - `quote_no TEXT PK`, `created_at`, `updated_at`, `quote_date`, `quote_type` (materials/manpower/equipment/service), `year`, `month`, `client_name`, `rfq_no`, `client_address`, `pdf_path`, `terms_json` (JSON list of term strings), `rows_json` (JSON `{item,description,unit,qty,rate,cost}`), `extra_costs_json` (JSON list of `{label,amount}`), `bid_status TEXT DEFAULT 'open'` (open/won/lost). Index `idx_quotes_month`.
- **quote_items** - `quote_type`, `name NOCASE`, `data_sig DEFAULT ''`, `data_json`, `use_count`, `last_used_at`. PK `(quote_type, name, data_sig)`. `name` = the type's primary-column value; `data_json` = other cols.
- **dns** - `dn_no TEXT PK`, `created_at`, `updated_at`, `dn_date`, `year`, `month`, `client_name`, `lpo_no`, `client_address`, `include_prices INT DEFAULT 0`, `pdf_path`, `rows_json`, `source_quote_no` (deal link), `extra_costs_json`. Index `idx_dns_month`.
- **dn_items** - `name NOCASE`, `data_sig`, `data_json` (`{description,unit,rate}`), `use_count`, `last_used_at`. PK `(name, data_sig)`.
- **client_lpos** (2026-07-31) - `client_name TEXT NOCASE`, `lpo_no TEXT NOCASE`, `lpo_date`, `year`, `month`, `total REAL` (in `currency`), `currency TEXT DEFAULT 'USD'`, `total_usd REAL` (**always USD** - what rollups sum), `source_quote_no`, `file_path` (the client's original, never edited), `ack_path` (our counter-signed copy), `acknowledged_at`, `notes`, `received_at`, `updated_at`, and (2026-09-11) `kind TEXT NOT NULL DEFAULT 'materials'` (`materials` | `rental` | `open`), `rows_json` (what they ordered, inherited from the cited quotation; **NULL means never recorded, which is not the same as nothing ordered**), `period_start`, `period_end`, `monthly_amount REAL`, `closed_at`, `close_reason` (both owned by `close_lpo`, NOT by `save_lpo` - the same split `file_path`/`ack_path` have, so editing a header cannot reopen a terminated contract), `erp_no`, `project`, `contact`. PK **`(client_name, lpo_no)`** - the number is the CLIENT's, so two clients can both send `PO-001`. Index `idx_client_lpos_month`. Files live under `<DOCUMENTS_ROOT>/LPOs/<Client>/<Year>/<MONTH>/`.
- **invoices** - `inv_no TEXT PK`, `created_at`, `updated_at`, `inv_date`, `inv_type`, `year`, `month`, `client_name`, `lpo_no`, `client_address`, `payment_bank_key` (`INV_BANK_INFO[*].key`), `withholding_included INT DEFAULT 1`, `grand_total REAL DEFAULT 0` (**always USD**), `sell_currency TEXT DEFAULT 'USD'`, `sell_total REAL DEFAULT 0` (the total in `sell_currency` - what the PDF prints), `pdf_path`, `rows_json`, `sell_iqd_per_usd REAL` (nullable - the rate an IQD invoice was actually converted at; NULL = not recorded), `source_quote_no`, `extra_costs_json`, `source_dn_no`. Index `idx_invoices_month`.
  - **`grand_total` is single-currency on purpose** (2026-07-29, migration `2026-07-29_inv_sell_currency`): analytics, the dashboard revenue series and the clients page all reduce it with a plain SQL `SUM`, which cannot convert per row. A dinar invoice therefore stores its native figure in `sell_total` and the USD equivalent in `grand_total`. See [[03-BACKEND-DATA]].
  - **`sell_iqd_per_usd` freezes the rate** (2026-08-04, migration `2026-08-04_inv_sell_peg`): `grand_total` is derived from it, so without a recorded rate an invoice's dollar value floated with whatever `doc_iqd_per_usd` would derive later. NULL keeps the old derived behaviour, which is what every pre-2026-08-04 invoice relies on. The column is declared in `database._SCHEMA` **as well as** the migration - a new profile DB never runs migrations.
- **inv_items** - `inv_type`, `name NOCASE`, `data_sig`, `data_json`, `use_count`, `last_used_at`. PK `(inv_type, name, data_sig)`.
- **master_items** (2026-08-16, shared DB) - THE item record, one row per product: `item_id INTEGER PK`, `item_no` (`M-xxxxx`, the handle that never moves), `name_en`, `name_ar`, **`description_en`, `description_ar`, `unit_en`, `unit_ar`** (2026-08-17 - a PO says كيلو where an invoice says `KG`; one column per field kept whichever it met first and discarded the other), `cost_native` + `cost_native_ccy`, `price_native` + `price_native_ccy`, `image_refs`, `img_query`, `data_json`, `use_count`, `last_used_at`, `created_at`, `updated_at`. Indexes on both names and on `item_no`. There is **no** plain `description`/`unit` column - the API's keys of those names are derived in `_row_out`.
  - A bilingual label does not weaken variant identity. During `build()`, different nonblank item numbers refuse a join; a paired name with several compatible description variants joins only the candidate carrying the same `item_no`, and an ambiguous unnumbered match is refused. A `name_en` equal to an English source row's own name (or Arabic equivalent) is ignored as a self-label, so it cannot bridge 2T and 5T rows merely because both are called “Manual chain hoist”. Conflicting current details are selected from the newest source row, all distinct image references are retained, and `last_company` is carried/inserted with the rebuilt record.
  - **`category`** (2026-09-08) - one value from the closed list `master_items.CATEGORIES` (13, derived from this catalogue's own vocabulary), or blank. `save_record` REFUSES an unknown value rather than storing it: a category nobody can filter by is worse than none, and free text is how you get 400 of them. It is a chip on the Master Data screen like the other gaps.
  - This database has **no migration ledger**, so the migrations run in `master_items._migrate()` from `init()` - the per-language split of description and unit, and the DROP of the retired `master_item_lines` / `master_item_sources` side tables. PRAGMA-guarded, idempotent, and therefore safe on every connection. There is now exactly ONE item table: an item's details belong to the item. See [[03-BACKEND-DATA]].
- **master_aliases** (2026-09-08, shared DB) - other spellings that FIND a record: `alias_norm TEXT PK` (`master_data._norm`, the SAME normalisation the resolver indexes names under - an alias filed under a different rule never matches, which is worse than none because it looks solved), `alias` as written, `item_id`, `source` (`merge` / `review` / `manual`), `created_at`. Index on `item_id`.
  - **Why it exists.** A document stores an item's WORDS, not its id, and the only way to find a record was its own `name_en` / `name_ar`. `merge_records` DELETES the losing rows, so merging deleted the only route to their words - the next document that used them made a fresh record, and cleaning the catalogue was feeding it. `master_data._items_from_records` now adds the stored spellings to each item's `aliases`, `merge_records` writes the losers' names (and their own aliases, or a chain of merges loses the first one's) in the same transaction, and `delete_records` drops them so an alias can never point at nothing.
  - An alias is a way IN and **never a value**: it cannot reach a document and it cannot overwrite a name. When one collides with a real name, `resolve_items` sees two entries under one key and the standing rule applies - a tie is never a match. The first claim on a spelling wins; a word that means two things is left to that rule rather than silently reassigned.
- **master_review** (2026-09-08, shared DB) - what the catalogue review has been told: `kind`, `key`, `decision` (`dismissed` / `applied`), `decided_at`, PK `(kind, key)`. **Keyed on the finding's WORDS, never on `item_id`** - a merge deletes ids, so an id-keyed ruling would be resurrected by the next scan, which is exactly the nagging it replaces. See `backend/master_doctor.py` in [[03-BACKEND-DATA]].
- ~~**letters**~~ - the Official Letter was retired on 2026-08-27 (never used; zero rows in either profile). The table is no longer created; an existing empty one in an older DB is left alone, never read and never written.
- **submitted_docs** - `kind` (po/quote/dn/inv), `doc_no`, `submitted_at`. PK `(kind, doc_no)`. Dashboard/To-Do unsubmitted reminders query PO/QUT/DN/INV.
- **paid_invoices** - `inv_no`, `paid_at`. PK `inv_no`.
- **attention_ignores** - `kind`, `doc_no`, `category` (decide/uncosted/unsubmitted/unpaid), `ignored_at`. PK `(kind, doc_no, category)`. Phase X.30 Needs-Attention mute; unsubmitted entries can include PO/QUT/DN/INV.
- **po_fulfillment** - `po_no`, `item_index INTEGER` (0-based into `pos.rows_json`), `status_kind` (delivered/paid), `source_kind` (dn/inv), `source_no`, `marked_at`. PK `(po_no, item_index, status_kind)`. Phase Y. A DN submit marks items **delivered**, an INV paid marks items **paid**; item-level so a PO can be partial on each axis and one DN/INV can mark items across several POs. `status_kind` is derived from `source_kind` (dn→delivered, inv→paid). Cleaned up in `delete_document_record`: deleting the PO drops all its rows; deleting the source DN/INV drops only the rows it created (items revert to unmarked).
- **doc_cost_meta** - `kind`, `doc_no`, `currency TEXT DEFAULT 'USD'`. PK `(kind, doc_no)`. Absent row = USD.
  - **This is the DEFAULT, not the truth.** The unit a cost is actually written in lives on the ROW, in `rows_json` as `cost_ccy` (`USD`/`IQD`, 2026-08-06) - one table can price one line in dollars and the next in dinars. `database.row_cost_currency(row, doc_currency)` is the ONLY correct way to read it: the row's own stamp when it names a real currency, this document's flag otherwise (which is every row saved before per-line currencies), and never a guess. Anything that writes, moves or re-saves a `cost` must write `cost_ccy` with it - `save_doc_costs` (`cost_ccys`), `inherit_costs` (`_COST_FIELDS`), `pull_costs_from_quote`, `smartMapRow` and `reconcileImportedCostCurrency` all do. A number whose stamp is wrong is a silent factor-of-peg (~1400×) error in every profit figure, and nothing about it looks like an error.
  - **`rate REAL` (2026-08-05, migration `2026-08-05_doc_cost_rate`)** - the IQD-per-USD rate PINNED to this document's costs. `NULL` = not pinned, so `doc_iqd_per_usd` falls back to its derived rules and no existing document's profit moves. Beware: `set_cost_currency` deletes the row when the currency is USD, so it now preserves the row whenever a rate is pinned - otherwise an IQD → USD → IQD flip silently discards it. Declared in `database._SCHEMA` as well as the migration, because a new profile database never runs migrations.
- **doc_columns** - `kind`, `doc_no`, `columns_json`. PK `(kind, doc_no)`. Absent row = document uses its type's columns.
- **doc_meta** - `doc_type`, `doc_no`, `key`, `value`. PK `(doc_type, doc_no, key)`. Shared satellite table for signing overlays and per-document metadata. `key='layout'` stores `{"autofit_rows": true|false}` for `po/qut/dn/inv` (explicit `false` is preserved). `key='catalogue_reviewed'` stores normalized name + description + unit identities already presented by a save-time Master Data popup, so dismissal survives an app restart without altering `master_items`. Written under **`doc_type='po'`** for a purchase order completing its English half and **`doc_type='qut'`** for a quotation completing its Arabic half (2026-09-10) - the two must not share a key, or reviewing one would silence the other.
- **deleted_docs** - `kind`, `doc_no`, `deleted_at`, `pdf_path`, `recycle_path`. PK `(kind, doc_no)`. Keeps deleted numbers consumed for PO/QUT/DN/INV numbering while active lists, Costing, and To-Do ignore the deleted document.
- **column_memory** - `doc_type` (po/quote/dn/inv), `sub_type` (materials/… or `''`), `col_key`, `value NOCASE`, `use_count DEFAULT 1`, `last_used_at`. PK `(doc_type, sub_type, col_key, value)`. Index `idx_column_memory_lookup`.
- **document_counters** - `kind TEXT PK`, `last_value INTEGER`. Permanent per-profile numbering sequences; written only by `backend/profile_numbering.py` (which also `CREATE TABLE IF NOT EXISTS`-es it defensively). Only consulted when `ACTIVE_PROFILE=='eshraqat'`.

### Key symbols

Connection / bootstrap:
- `_connect() -> sqlite3.Connection` - opens `config.DB_PATH` (the **active profile** DB), `row_factory = Row`, `PRAGMA foreign_keys = ON`, mkdirs `DATA_DIR`.
- `_connect_shared()` - `shared_library.connect()`, the cross-profile catalog DB at `data/shared/library.db`. All `suggest_*`/`remember_*`/`column_memory` helpers use this.
- `best_effort_shared(op, ...)` - wraps catalog writes during document saves and **swallows `sqlite3.Error`/`OSError`** - a broken shared DB never fails a save (the write silently no-ops).
- `init_db() -> None` - runs `_SCHEMA` on the profile DB, then `shared_library.init_shared_db()`, then backfills `companies` from `master_items.last_company` and seeds `clients` from `config.QUOTE_CLIENTS_SEED` **once** via `client_master.seed_once` **in the SHARED db** (not the profile db). Called from `app.main()` before migrations, and by `api._reload_profile_runtime()` on profile switch.

items / companies:
- `suggest_items(prefix, limit=8)` / `remember_item(name, description, unit, company)` - PO autocomplete; remember bumps `use_count` on same `(name, data_sig)`. Shared DB.
- `suggest_companies(prefix, limit=8)` / `remember_company(name)`. Shared DB.

pos:
- `save_po_record(po_no, po_date, year, month, pdf_path, rows, notes="", source_quote_no=None)` - upsert; `source_quote_no=None` preserves the existing reference, `""` clears it; also calls `remember_item`/`remember_company` per row (via `best_effort_shared`).
- `load_po(po_no)` - returns row dict with `rows` (parsed `rows_json`) and `source_quote_no`.
- `list_pos_in_db(year, month)`, `po_exists(po_no)` (DB then filesystem `PO_ROOT.rglob`; list rows include `source_quote_no`).

quotes / clients / quote_items:
- `suggest_clients(prefix, limit=12)` (empty prefix = full list) / `remember_client(name)`. Shared DB.
- `save_quote_record(quote_no, quote_date, quote_type, year, month, client_name, rfq_no, client_address, pdf_path, rows, terms)` - upsert; remembers client + each item into shared `items`.
- `load_quote`, `list_quotes_in_db(year, month)`, `quote_exists(quote_no)`.
- `suggest_quote_items(quote_type, prefix, limit=8)` / `remember_quote_item(quote_type, name, data)`. Shared DB.

dns / dn_items:
- `save_dn_record(dn_no, dn_date, year, month, client_name, lpo_no, client_address, include_prices, pdf_path, rows, source_quote_no=None)` - upsert; **`source_quote_no=None` preserves the existing link** (Phase N.3).
- `load_dn`, `list_dns_in_db`, `dn_exists`.
- `suggest_dn_items(prefix, limit=8)` / `remember_dn_item(name, data)`. Shared DB.

invoices / inv_items:
- `save_inv_record(inv_no, inv_date, inv_type, year, month, client_name, lpo_no, client_address, payment_bank_key, withholding_included, grand_total, pdf_path, rows, source_quote_no=None, source_dn_no=None, sell_currency="USD", sell_total=None, sell_iqd_per_usd=None)` - upsert; same source_quote_no-preserve rule. `sell_iqd_per_usd=None` means "not supplied" and COALESCEs to the stored value rather than erasing it. `grand_total` must be USD; `sell_total=None` defaults to it.
- `load_inv`, `list_invoices_in_db`, `inv_exists` (`load/list` include the invoice `paid` flag).
- `suggest_inv_items(inv_type, prefix, limit=8)` / `remember_inv_item(inv_type, name, data)`. Shared DB.


submitted_docs (Phase Q):
- `set_submitted(kind, doc_no, value=True)`, `is_submitted(kind, doc_no)`, `submitted_set(kind) -> set[str]`. `_SUBMIT_KINDS = {"po","quote","dn","inv"}`.

paid_invoices:
- `set_invoice_paid(inv_no, value=True)`, `is_invoice_paid(inv_no)`, `paid_invoice_set() -> set[str]`.

po_fulfillment (Phase Y):
- `_PO_FULFILL_SOURCES = {"dn":"delivered","inv":"paid"}` retains `paid` as the **legacy stored invoice-link axis** for backward compatibility. It does not itself mean money received; actual payment comes from `paid_invoices`. `_fulfill_state(marked, total) -> none|partial|full`.
- `set_po_fulfillment(po_no, item_indexes, source_kind, source_no, value=True)` - upsert/delete marks for the given 0-based item indexes; status derived from source_kind.
- `po_fulfillment_detail(po_no) -> {po_no, delivered, invoiced, paid}`: `invoiced` contains all invoice links; `paid` is the subset whose `source_no` exists in `paid_invoices`.
- `po_fulfillment_map() -> {po_no: {delivered, invoiced, paid}}` (none/partial/full per axis). This derived split preserves old rows with no migration while enforcing link ≠ payment.
- `po_links_for_doc(source_kind, source_no) -> [po_no]` - POs a given DN/INV already marked.
- `list_po_candidates(limit=300) -> [{po_no, po_date, year, month, item_count, summary, source_quote_no}]` - recent DB-backed POs for the DN/INV→PO picker.

doc_cost_meta (Phase R) / currency peg:
- **The USD peg is per-profile and lives in config.py**: `config.IQD_PER_USD` = 1400 (abraj) / 1500 (eshraqat); `config.CASH_IQD_PER_USD = 1500` always. `database.IQD_PER_USD` / `database.CASH_IQD_PER_USD` are aliases of the config values.
- `database.doc_iqd_per_usd(kind, doc_no) -> float` - the peg for a specific document: 1500 for all eshraqat docs, and 1500 for **any deal containing a cash invoice** (`payment_bank_key='cash'` propagates to the root quote and its DNs). Costing math must go through this, not the constant.
- `_COST_CURRENCIES = {"USD","IQD"}`.
- `get_cost_currency(kind, doc_no) -> str`, `set_cost_currency(kind, doc_no, currency)` ('USD' deletes the row), `cost_to_usd(value, currency) -> float`.

doc_columns (Phase T):
- `get_doc_columns(kind, doc_no) -> list[dict]|None`, `set_doc_columns(kind, doc_no, columns)` - stores only lightweight shape fields (`key/head/kind/width/align/default`); never image data. Empty clears it.

doc_meta layout (per-document layout flags):
- `get_doc_layout(doc_type, doc_no) -> dict|None`, `set_doc_layout(doc_type, doc_no, layout)` - validates `doc_type ∈ {"po","qut","dn","inv"}` and stores only `autofit_rows` under `key='layout'`. Empty/missing layout clears the row; explicit `False` is kept so a saved document can override a type/default Auto-fit setting that is on.

active document deletion:
- `normalize_document_kind`, `document_pdf_path`, `deleted_document_numbers`, `delete_document_record` - shared cleanup used by the bridge document-delete API. Removes the main row, writes `deleted_docs`, removes `submitted_docs` / `doc_cost_meta` / `doc_columns` / `attention_ignores` satellites where applicable, removes `paid_invoices` for invoice deletes, all matching `doc_meta` keys, and quote child links (`pos.source_quote_no`, `dns.source_quote_no`, `invoices.source_quote_no`) when deleting a quotation.

column_memory (Phase G.14):
- `_VALID_DOC_TYPES = {"po","quote","dn","inv"}`.
- `remember_column_value(doc_type, sub_type, col_key, value)`, `suggest_column_values(doc_type, sub_type, col_key, prefix, limit=10)`, `remember_doc_columns(doc_type, sub_type, rows, columns)` (skips kinds `image/computed/index`). Shared DB.

Multi-variant signature (Phase G.15):
- `_data_sig(data) -> str` - SHA1(first 16 hex) of normalised (lower-cased keys, trimmed non-empty values) dict; `""` if not a dict, `"empty"` if no meaningful values. **Duplicated verbatim in migrations.py `_sig_from_data` AND shared_library.py `_data_sig`** - three copies, keep all in sync.

Costing / profit engine (Phase N.1 → V):
- `_DOC_COST_SPEC` - kind → `(table, pk_col, rows_col, sub_type_col)`.
- `_primary_key_for(kind, sub_type)` - resolves the anchor column via `config.QUOTE_TYPES`/`INV_TYPES`; DN always `"item"`.
- `_coerce_cost(v)`, `_num(v)` - parse messy money cells to float or `''`/None.
- `_COST_FIELDS = ("cost","cost_qty","cost_sell")` - per-row costing fields that ride inside `rows_json` but never print.
- `inherit_costs(old_rows, new_rows, primary_key)` - carries costing fields across an editor re-save (matched by primary-key value) so a normal re-save never wipes costing-screen data.
- `save_doc_costs(kind, doc_no, costs, extra_costs, bid_status, source_quote_no, qtys, sells, currency)` - patches ONLY costing data in-place; never touches the PDF. `costs/qtys/sells` align to stored rows by position (list) or stringified index (dict) via `_index_map`.
- `set_bid_status(quote_no, status)` - open/won/lost.
- `_detect_unit_econ(row) -> (unit_sell, base_qty, auto)` - infers per-unit sell price. Priority: explicit `total` col → largest `col_*` custom column (guess, `auto=True`) → `qty*rate` → flat-rate (`rate`, qty 1).
- `_line_econ(row, pk, currency="USD")`, `_rows_fully_costed(rows, pk)`, `_doc_costing_payload(kind, d)`.
- Stale-bid logic: `QUOTE_STALE_DAYS = 14` (auto-loss cutoff), `QUOTE_DECIDE_DAYS = 7`, `_parse_doc_date`, `quote_is_stale`, `effective_bid_status(stored, has_invoice, quote_date, today)`. **Phase X.26**: `auto_lose_stale_quotes()` now PERSISTS `bid_status='lost'` for open, ≥14-day, un-invoiced quotes (run at the top of `dashboard_needs_attention`), so `bid_status` in the `quotes` table is no longer left `open` past the cutoff. `effective_bid_status` checks `has_invoice` FIRST, so a later invoice still reads `won`.
- `quote_has_invoice(quote_no)`, `_won_quote_nos(conn)`.
- `load_doc_costing(kind, doc_no)`, `dashboard_needs_attention(year, month)`, `list_docs_for_costing(year, month, kinds=None)`, `_costed_from_json(...)`. Default costing/to-do scope is QUT/INV; DN cost data remains readable only when explicitly requested for legacy records and deal-flow links.
- Deal linking (Phase N.6/N.7): `set_doc_source_quote(kind, doc_no, quote_no)`, `list_link_candidates(kind, client="", limit=400)`, `list_quote_numbers`, `deal_flow(kind, doc_no)`.
- Cost memory (Phase R): `_norm_name`, `_tokens`, `_all_cost_currencies(conn)` (`{(kind,doc_no): currency}` map), `_costed_catalog`, `find_known_costs(items, limit_per=3)` - fuzzy token-overlap match of item names to previously-costed lines (score ≥ 0.3), costs normalised to USD.
- Reporting: `profit_summary(year, month)` (realized=invoices, pipeline=open quotes only), `profit_trend(end_year, end_month, span=12)`, `_months_back`, `_MONTH_ABBR`.

**Reads / writes** - SQLite at `config.DB_PATH` (`data/profiles/<active>/po_tool.db`) for documents/satellites, and `data/shared/library.db` (via `shared_library.connect()`) for the 7 catalogs. `*_exists` helpers fall back to scanning `config.*_ROOT` folders. Reads `config.QUOTE_TYPES`/`INV_TYPES` for primary keys, `config.QUOTE_CLIENTS_SEED` for seeding, `config.ACTIVE_PROFILE`/`IQD_PER_USD`/`CASH_IQD_PER_USD` for the peg.

**Calls / called by** - All CRUD helpers are wrapped by `backend/api.py` and exposed to JS (e.g. `api().save_quote` → `save_quote_record`, `api().suggest_clients`, `api().load_doc_costing`, `api().profit_trend`). `init_db()` called by `app.main()` and by `api._reload_profile_runtime()`. Generators call the `save_*_record`/`load_*` helpers. `backend/clients.py` aggregates across BOTH profile DBs via `profile_manager.profile_db_path(pid)`; `backend/data_manager.py` backup/restore is profile-aware and includes the shared library DB.

**Gotchas**
- **Profit is never stored** - always recomputed on read (revenue − cost) so a later rate change can't leave a stale profit behind.
- **Per-row cost rides inside `rows_json`** (key `cost`, plus `cost_qty`/`cost_sell` overrides). Anything that rewrites `rows_json` must go through `inherit_costs` or it silently drops costing data. Editor re-saves rely on this.
- `source_quote_no=None` in `save_dn_record`/`save_inv_record` means "leave existing link"; pass `""` to actually unlink via `set_doc_source_quote` (which stores NULL).
- `_data_sig` is now duplicated in **THREE** places - `database._data_sig`, `migrations._sig_from_data`, `shared_library._data_sig`. If the hashing rule changes in one, existing `data_sig` PKs won't match new inserts and autocomplete de-dup breaks in the others.
- **The peg is per-profile, not a constant** - always use `doc_iqd_per_usd(kind, doc_no)`; cash deals re-peg the WHOLE deal chain (root quote + its DNs) at 1500 regardless of profile.
- `best_effort_shared` swallows catalog-write errors - if `data/shared/library.db` is broken, autocomplete silently stops learning while document saves still succeed. Nothing surfaces this to the user.
- Renaming a doc (its number IS the PK) requires updating `pos/quotes/dns/invoices` PK, `pdf_path`, AND the decoupled satellite tables keyed on `(kind, doc_no)`: `submitted_docs`, `doc_cost_meta`, `doc_columns`, `attention_ignores` - plus invoice `paid_invoices` and `doc_meta` (signing/layout, keyed `(doc_type, doc_no, key)`). Migrations only patch the main row + `doc_meta`; the Phase Q/R/T satellites and invoice paid flags are NOT re-keyed by the rename migrations. Deleting an active doc must use `delete_document_record` so these satellites do not linger under an old number and the number is recorded in `deleted_docs` instead of reused.
- `NOCASE` collation on name/value columns means autocomplete is case-insensitive but "Cement" and "cement" collapse to one PK row.
- `list_quotes_in_db` etc. filter by the stored `year/month` columns, not the on-disk folder - DB and folder can diverge if files are moved manually.

---

## backend/migrations.py

**Purpose** - Idempotent one-shot migrations that reconcile on-disk PDFs and DB rows with schema/layout changes. Ordered, each recorded in `_migrations_applied` so it runs once. **Runs against the ACTIVE profile DB** (`database._connect()`), so each profile DB carries its own `_migrations_applied` ledger - a schema migration must be safe to run on both abraj (full history) and eshraqat (near-empty) DBs.

**Key symbols**
- `migration(key)` decorator → appends `(key, fn)` to `_REGISTRY`.
- `run_all() -> list[dict]` - for each pending migration: run, `_record`, `conn.commit()`; returns per-step `{key,status,notes|error}`. Wraps each fn in try/except and continues to the next step on failure - but **`app.main()` raises `RuntimeError` at startup if any step reports `failed`**, unless `DOCUMENTS_HUB_LEGACY_FALLBACK=1` (then it just appends to the profile warning). Called by `app.main()` after `init_db()`, and again on every profile switch (`api._reload_profile_runtime`).
- `_ensure_table`, `_was_applied`, `_record` - manage `_migrations_applied(key PK, applied_at, notes)`.
- Helpers: `_move_tree_into(src, dst)` (never clobbers), `_strip_type_subfolder`, `_update_pdf_paths` (bulk substring rewrite, only when the new path exists on disk), `_update_pdf_path` (single-row path set for the day-folder migration), `_new_stem_typed`, `_new_stem_dn`, `_rename_doc_tree`, `_type_code_from_stem`, `_parse_date_display`, `_day_folder`, `_safe_move` (appends `(1)`,`(2)` on collision), `_pluck_db_dates`, `_file_mtime_date`. Module-level constants: `_PHASE_N_CODES`, `_QUT_TYPE_LABEL`, `_INV_TYPE_LABEL`, `_KNOWN_CODES` (all `{MT,EQ,MP,SV}` maps/sets).

**Registered migrations (run order = decorator source order; keys are labels):**
1. `2026-07-07_paid_invoices` (`_paid_invoices`) - ensures the invoice-only paid-status table exists.
2. `2026-07-08_attention_ignores` (`_attention_ignores`) - ensures the Needs-Attention mute table exists (Phase X.30).
3. `2026-07-13_po_source_quote` (`_po_source_quote`) - ADD COLUMN (PRAGMA-guarded) `pos.source_quote_no`.
4. `2026-07-15_po_fulfillment` (`_po_fulfillment`) - ensures the PO delivered/paid item-marks table exists (Phase Y).
5. `2026-05-31_rename_doc_roots` (`_rename_doc_roots`) - QUOTATION→QUOTATIONS, DELIVERY→DELIVERY NOTES, INVOICE→INVOICES; drops per-type subfolders (MT/EQ/MP/SV); moves legacy `D:\ABRAJ\CSSP\PO\<month>` → `<BASE>\PURCHASE ORDERS\<year>\<month>` (year from file mtime); rewrites `pdf_path`s.
6. `2026-05-31_multivariant_items` (`_multivariant_items`) - adds `data_sig` + composite PK to `items`/`quote_items`/`dn_items`/`inv_items` by recreating each table (SQLite can't ALTER a PK). Guarded by `_has_sig` PRAGMA check. Contains its own `_sig_from_data`/`_sig_from_json` (mirror of `database._data_sig`).
7. `2026-05-31_add_cash_bank` (`_add_cash_bank`) - appends the `cash` sentinel bank to a saved `invoice.bank_info` in settings.json; `importlib.reload(config)` after.
8. `2026-05-31_dn_client_prefix` (`_dn_client_prefix`) - renames `DN-N.pdf` → `<client>-DN-N.pdf`; updates `dns.dn_no` (the PK) + `pdf_path`.
9. `2026-06-14_costing_fields` (`_add_costing_fields`) - ADD COLUMN (PRAGMA-guarded) `quotes.extra_costs_json`, `quotes.bid_status`, `dns.source_quote_no`, `dns.extra_costs_json`, `invoices.source_quote_no`, `invoices.extra_costs_json`.
10. `2026-06-03_doc_name_rearrange` (`_doc_name_rearrange`) - filename rearrange `QUT-<CLIENT>-<TYPE>-<N>` → `QUT-<TYPE>-<CLIENT>-<NNN>` (zero-pad 3), same for INV; `<CLIENT>-DN-<N>`/`DN-<N>` → `DN-<CLIENT>-<NNN>`/`DN-<NNN>`. Updates PK, `pdf_path`, and `doc_meta`.
11. `2026-06-12_day_type_folders` (`migrate_day_type_folders`) - moves every PDF into the day+type layout (see On-Disk Layout below). DB date is source of truth; files with no DB row fall back to mtime. Cleans up empty dirs.

**Reads / writes** - `database._connect()` (the active profile DB), the on-disk `config.*_ROOT` trees, `config.SETTINGS_PATH` (cash migration - the per-profile settings.json), `config.MONTH_FOLDERS`.

**Gotchas**
- `doc_meta` is now part of `database._SCHEMA` and is also defensively ensured by signing/layout helpers. Older databases may still have been created before this table existed, so migration code keeps its `try/except sqlite3.Error` guard when re-keying it.
- Registration order in `_REGISTRY` is source-order of the `@migration` decorators, NOT date order in the key - note `2026-07-13_po_source_quote` runs 3rd (before `rename_doc_roots`) and `2026-06-14_costing_fields` runs *before* `2026-06-03_doc_name_rearrange`. Keys are labels only.
- Each profile DB has its own ledger - a new migration will run once per profile (on first launch/switch into that profile). Never assume it has already run everywhere.
- Migrations never overwrite an existing destination file (`_move_tree_into`, `_safe_move`, `.exists()` guards) - a collision leaves the source in place, which can strand a file outside the new layout.
- The multivariant migration DROPs and recreates tables; if it half-applies and the process dies mid-way, `_has_sig` guards make re-runs safe per-table.
- Rearrange migrations re-key the PK but do NOT update the Phase Q/R/T satellite tables (`submitted_docs`, `doc_cost_meta`, `doc_columns`, `attention_ignores`) or invoice `paid_invoices` - a renamed doc loses its submitted flag / currency / custom columns / paid flag / attention mutes.

---

## backend/config.py

**Purpose** - Central configuration: hard-coded defaults for every doc type (company identity, brand colours, per-type column schemas, folder roots, page-fit constants), a per-profile identity pass, then a per-profile `settings.json` overlay applied at import.

**Key symbols**
- Profile binding (at import, from `profile_manager`): `ACTIVE_PROFILE`, `DATA_DIR`/`PROFILE_DIR` (`data/profiles/<id>/`), `DB_PATH`, `SETTINGS_PATH`, `ASSETS_DIR`. See [[13-PROFILES-BRANDS]].
- Paths: `APP_DIR`, `TEMPLATES_DIR`, `STATIC_DIR`, `DOCUMENTS_ROOT` (default `D:\PO TOOL` abraj / `D:\ESHRAQAT DOCUMENTS` eshraqat), and derived roots `PO_ROOT` (`…\PURCHASE ORDERS`), `QUOTE_ROOT` (`…\QUOTATIONS`), `DN_ROOT` (`…\DELIVERY NOTES`), `INV_ROOT` (`…\INVOICES`).
- `MONTH_FOLDERS` - `{1:"JAN",…,12:"DEC"}`. Used for every doc type's month folder AND by migrations to detect month segments.
- Prefixes/codes (abraj defaults): `PO_PREFIX="A"`, `QUOTE_PREFIX="QUT"`, `DN_PREFIX="DN"`, `INV_PREFIX="INV"`; `QUOTE_TYPE_CODES`/`INV_TYPE_CODES` (`materials→MT, manpower→MP, equipment→EQ, service→SV`). Under eshraqat the prefixes become `PO/QT/DN/IV`.
- Currency peg: `IQD_PER_USD` - **per-profile**, 1400 abraj / 1500 eshraqat; `CASH_IQD_PER_USD = 1500` always (cash-invoice deals re-peg via `database.doc_iqd_per_usd`).
- `_apply_company_profile_defaults()` - runs at import **before** the settings overlay. When `ACTIVE_PROFILE=='eshraqat'`: `DOCUMENTS_ROOT=D:\ESHRAQAT DOCUMENTS`, prefixes `PO/QT/DN/IV`, ALL brand colors overwritten with `PROFILE_ACCENT` `#861432` (abraj accent `#1F3864`), English-only `COMPANY`/`QUOTE_COMPANY`/`DN_COMPANY`/`INV_COMPANY` identities, `INV_BANK_INFO` collapsed to the cash sentinel only, empty address options.
- PO page-fit constants: `PO_AUTOFIT=False`, `TABLE_HEADER_MM=12`, `TABLE_MIN_ROW_MM=7`, `TABLE_LINE_HEIGHT_MM=5.5`, `PO_SIGNATURE_MM=54.0` (all measured against the real renderer). `MAX_ITEMS_PER_PO` / `TABLE_BUDGET_MM` retired 2026-07-29 - POs paginate instead of splitting, and the budget is per page ROLE (see [[04-BACKEND-DOCGEN]]). Quote fit: `QUOTE_TABLE_AREA_MM=120`, `QUOTE_MIN_ROW_MM=12`, `QUOTE_MAX_ROW_MM=55`.
- Identity blocks: `COMPANY` (PO, Arabic under abraj), `QUOTE_COMPANY`, `DN_COMPANY`, `INV_COMPANY`. Brand colours: `BRAND_COLOR`, `QUOTE_BRAND_COLOR`, `DN_BRAND_COLOR`, `INV_BRAND_COLOR` - per-doc-type constants under abraj, all = `PROFILE_ACCENT` under eshraqat.
- `USER_PREFERENCES` - UI-only app preferences persisted in the per-profile `settings.json.preferences`: `theme`, `after_export`, `compact_tables`, `dn_prices_default`.
- `PO_COMPANY_TAGS` - per-profile `settings.json.po_company_tags`: `{company_name: "client"|"internal"}` classifying each PO "لشركة" name. `config.po_company_is_internal(name)` → True only for explicit `internal` (untagged ⇒ client). Seeded once from existing POs (all `client`) by `database.seed_po_company_tags`; edited by the PO-editor popup / PO Settings; drives the To-Do `deliver` bucket (`database.pos_awaiting_invoice`). Not document content - no PDF/schema impact. **2026-10-21 - ONE copy:** the map lives in `purchase_order.company_tags` (what PO Settings edits and config reads); `database.read_po_company_tags(settings)` / `write_po_company_tags(settings, tags)` are the only reader/writer, and the writer drops the old top-level `po_company_tags`. The PO editor's prompt used to read/write that old copy and REPLACE the live map with it - on Abraj it still had ابراج البدر as client, so one prompt answer put every our-company PO in the To-Do. Cancelling the prompt now saves nothing (it used to save "client", filing typos like v/j/ت as clients). Pinned by `tests/po_company_tags_one_copy.test.py`.
- Term lists: `QUOTE_TERMS_DEFAULT`, `QUOTE_TERMS_EQUIPMENT`, `QUOTE_TERMS_MANPOWER`.
- **`QUOTE_TYPES`** / **`INV_TYPES`** - per-sub-type dicts: `{code, label, primary_key, grand_total?, terms?, columns:[{key,head,kind,width,default?}]}`. Column `kind` ∈ `text/num/money/computed/image/index`. Materials widths sum to ~96.4 (NO. index col = remainder).
- **`DN_BASE_COLUMNS`** + **`DN_PRICE_COLUMNS`** - DN has no sub-types; price columns (Rate, Total) are appended at render time only when `include_prices`.
- `INV_BANK_INFO` - list of bank dicts (`key/label/company_line/bank_name/branch/swift/account_no/iban/currency`); includes the `cash` sentinel (omits the Payment Info card). Under eshraqat: cash only. `INV_WITHHOLDING_PCT=3.3`.
- `QUOTE_CLIENTS_SEED`, `QUOTE_ADDRESS_OPTIONS` (["CSSP Site","IBRU"] abraj; empty eshraqat), `DN_ADDRESS_OPTIONS`/`INV_ADDRESS_OPTIONS` (copies).
- Overlay slots (populated by settings): `QUOTE_LAYOUT`, `DN_LAYOUT`, `INV_LAYOUT`, `QUOTE_SUBTYPES_OVERRIDES`, `INV_SUBTYPES_OVERRIDES`, `DN_BASE_COLUMNS_OVERRIDE`.
- `load_user_settings() -> dict` - parse the per-profile settings.json (empty dict on error).
- `save_user_settings(settings) -> None` - write pretty JSON (utf-8).
- `_apply_user_settings() -> None` - mutates module globals from settings.json. Called once at import bottom (after `_apply_company_profile_defaults`). Handles flat PO keys, app `preferences`, `documents_root` re-derivation of all roots, per-section `quotation/delivery/invoice` merges, `_layout_dict`, `_subtype_overrides`, bank_info replacement.
- **`current_effective_settings() -> dict`** - snapshot of every tunable, the shape the Settings UI reads. **Now leads with `profile_id` and `logo_data_uri`** (`profile_manager.logo_data_uri()`).

**`current_effective_settings()` shape** (top level):
`profile_id, logo_data_uri, documents_root, po_root, ui_lang, po_prefix, autofit_rows, table_header_mm, table_min_row_mm, table_line_height_mm, po_signature_mm, brand_color, preferences{theme, after_export, compact_tables, dn_prices_default}, po_company_tags{name: "client"|"internal"}, company{…}`, plus per-doc-type sections:
- `quotation{brand_color, company{…}, address_options[], layout{}, <subtype>{columns/terms/column_heads/layout}…}`
- `delivery{brand_color, company{…}, address_options[], layout{}, columns[]}`
- `invoice{brand_color, company{…}, address_options[], withholding_pct, bank_info[], layout{}, <subtype>{…}}`

**Reads / writes** - reads `data/profile-registry.json` (via profile_manager) and reads/writes the per-profile `data/profiles/<id>/settings.json`. Read by every generator and numbering module. `backend/brand_assets.py` reads `config.ASSETS_DIR` only - no cross-profile asset fallback (`logo_data_uri(required=True)` raises `FileNotFoundError`).

**Calls / called by** - `_apply_company_profile_defaults()` then `_apply_user_settings()` run at import. `save_user_settings` + `importlib.reload(config)` triggered by the Settings save path (`api().save_settings`), by profile switching (`api._reload_profile_runtime` reloads config + 16 backend modules), and by the cash-bank migration.

**Gotchas**
- **Globals are mutated in place at import** - anything importing `config` before overlay would see defaults; the reload dance is required after every settings save AND every profile switch. `api.switch_profile()` = validate → activate → reload dance → `init_db()` + `migrations.run_all()`, rolling back to the previous profile on failure; blocked entirely under legacy fallback.
- `documents_root` in settings re-derives QUOTE/DN/INV roots but PO_ROOT only follows if `po_root` is blank - a pinned `po_root` won't move. Under eshraqat both roots default from `D:\ESHRAQAT DOCUMENTS` and the profile settings.json pins `po_root` explicitly.
- Column `width` values are proportional (percent-ish, summing per type); the NO./index column takes the remainder, so widths that sum to >100 will overflow the table.
- `_subtype_overrides` silently drops columns without both `key` and `head`, and coerces unknown `kind` to `text`.
- Custom `col_2…col_N` columns (see the live equipment override in the abraj settings.json) are how users extend a table; `_detect_unit_econ` special-cases `col_*` keys for costing - renaming that prefix breaks profit inference.
- Changing `MONTH_FOLDERS` names retroactively orphans existing folders (migrations and numbering both key off these exact strings).

---

## backend/po_numbering.py

**Purpose** - PO numbering + the PO on-disk save-folder convention (abraj filesystem-max logic; eshraqat short-circuits to the DB counter).

**Key symbols**
- **Eshraqat branch:** `get_next_po_number` begins `if config.ACTIVE_PROFILE == "eshraqat": return profile_numbering.peek(...)` - the permanent `document_counters` sequence, format `PO-%04d`. All four numbering modules (po/quote/dn/inv) have the identical branch. `peek` does **not** reserve the number; `api.py` must call `profile_numbering.commit(kind, nos)` after a successful save. Abraj behaviour below is unchanged.
- `_PO_RE` - `^A-(\d+)\.pdf$` (prefix from `config.PO_PREFIX`, case-insensitive).
- `_day_folder_name(d)` → `DD-MM-YYYY`. `_month_folder(d)` → `<PO_ROOT>/<year>/<MONTH>`. `_previous_month_folder(d)` (wraps Dec→prev year).
- `_max_po_number_in_folder(folder)` - max NN across `*.pdf` **recursively** (`rglob`, so it descends into day subfolders).
- `get_next_po_number(today=None) -> (po_no, target_folder)` - next N = `max(current_month_max, prev_month_max)+1`; creates & returns the DAY folder `<PO_ROOT>/<Year>/<MONTH>/<DD-MM-YYYY>/`.
- `list_pos_for_month(year, month) -> list[dict]` - `{po_no, number, filename, path}` sorted by number.
- `folder_for_month(year, month)`, `folder_for_date(d)`.

**Reads / writes** - reads `config.PO_ROOT`, `config.PO_PREFIX`, `config.MONTH_FOLDERS`, `config.ACTIVE_PROFILE`; creates the day folder on `get_next_po_number`.

**Calls / called by** - used by the PO generator and `api()` (e.g. `api().next_po_number`, PO browse listing).

**Gotchas**
- (abraj) Next-number spans current month, previous month, and deleted PO tombstones so app-deleted PO numbers are not reused. Active PDF numbering still only looks two months back, so a >1 idle month with no tombstone can restart low.
- (abraj) Numbering is not a DB sequence; it is active-file max plus `deleted_docs` tombstones for app-deleted docs.
- PO number is stored as `A-<N>` **without** zero-padding under abraj (unlike QUT/INV/DN which pad to 3); eshraqat numbers are `PO-0001` (zero-pad 4).
- **Eshraqat number reissue risk:** `profile_numbering.peek()`/`peek_many()` compute a floor = max(counter row, parseable active doc numbers in the table, `deleted_docs` tombstones) but do NOT reserve - a save path that forgets `profile_numbering.commit(kind, nos)` reissues numbers. Commit batches must be consecutive.
- The `document_counters` floor logic parses existing doc numbers with the eshraqat regex only - pre-existing abraj-format numbers in an eshraqat DB parse to 0 and are ignored, so never point `profile_numbering` at an abraj DB.

---

## backend/quote_numbering.py

**Purpose** - Quotation numbering (global running counter) + the nested day/type save-folder layout. Same eshraqat short-circuit as po_numbering (`QT-%04d` via `profile_numbering.peek`; commit after save).

**Key symbols**
- `_TRAILING_NUM` - `-(\d+)$`. `_KNOWN_CODES = {"MT","EQ","MP","SV"}`.
- `_sanitize_client(name)` - strips `\/:*?"<>|`, falls back to `"CLIENT"`. Since 2026-09-11 it FIRST resolves the name through Clients Master Data (`client_master.canonical`), so "CCCC ltd", the full legal name and an Arabic spelling all produce the one short code the document is numbered and filed under. A name that is not on file is used as typed. The lookup is lazy and its errors are swallowed - numbering must never be blocked by the client store.
- `_type_code(quote_type)` (via `config.QUOTE_TYPE_CODES`, default MT), `_type_label(quote_type)` (via `config.QUOTE_TYPES[..].label`, folder-friendly).
- `_day_folder(d)` → `DD-MM-YYYY`.
- `quote_save_folder(client_name, quote_type="materials", d=None) -> Path` - `<QUOTATIONS>/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/`.
- `_global_max_number()` - max trailing `-N` across **every** active `QUT-*.pdf` under `QUOTE_ROOT` (recursive) plus `deleted_docs` quote tombstones - a single global counter across all clients/types/months.
- `get_next_quote_number(client_name, quote_type="materials") -> (quote_no, target_folder, next_n)` - `quote_no = QUT-<CODE>-<CLIENT>-<NNN>` (zero-padded 3).
- `_type_code_from_filename(stem)` - handles BOTH new (`QUT-<TYPE>-<CLIENT>-<N>`) and legacy (`QUT-<CLIENT>-<TYPE>-<N>`) arrangements.
- `list_quotes_for_month(year, month) -> list[dict]` - recursive scan filtered by `/<year>/<MONTH>/` path segments; matches every historical layout; client = segment before the year folder. Returns `{quote_no, filename, path, client, type_code}`.

**Reads / writes** - reads `config.QUOTE_ROOT`, `QUOTE_PREFIX`, `QUOTE_TYPE_CODES`, `QUOTE_TYPES`, `MONTH_FOLDERS`, `config.ACTIVE_PROFILE`. Folder created lazily on save (not here).

**Calls / called by** - quote generator + `api()` (e.g. `api().next_quote_number`, quote browse listing). INV/DN numbering modules follow the same pattern including the eshraqat branch (out of scope here - see [[04-BACKEND-DOCGEN]]).

**Gotchas**
- The running number is a **global** active-filesystem max plus `deleted_docs` tombstones. Deleting through the app preserves the number as consumed; moving/removing files manually outside the app can still affect the active-file side.
- **2026-09-11 JUNE renumber:** four documents predated the rule that a number is built from the client's DISPLAY name and carried the full company name instead (`INV-MT-JUNE CONSTRUCTION CO.-049`, `QUT-MT-...-055`, `QUT-EQ-...-065`, `QUT-MT-...-066`; the long number did not fit its field and wrapped onto a second line on the printed page). `scripts/renumber_june_to_display_name_20260911.py` moved 29 stored values across 9 columns - `invoices.inv_no`, `invoices.pdf_path`, `invoices.source_quote_no`, `quotes.quote_no`, `quotes.pdf_path`, `doc_columns.doc_no`, `doc_meta.doc_no`, `submitted_docs.doc_no`, `po_fulfillment.source_no` - regenerated all four PDFs and verified each prints the same document as before (the texts are compared whole, not just checked for the new number), then moved the files into the `JUNE` folders and removed the three emptied `JUNE CONSTRUCTION CO` trees. The **printed client name did not change**: the legal name still appears on the paper, and resolution maps it back to JUNE. Sequence numbers did not change either - they are per profile and per type, not per client. Restore point: `data/master-data-backups/20260911-151801-june-renumber/`. Pinned by `tests/june_numbered_on_display_name.test.py`.
- **2026-09-08 invoice repair:** a pre-isolation signed-copy test left `INV-MT-SIGNEDCOPY-901` in Abraj `deleted_docs`, so the next six real invoices became 902-907. `scripts/repair_invoice_number_jump_20260908.py` re-keyed them to 048-053, regenerated their PDFs, propagated six column rows, twelve document-meta rows and forty PO-fulfillment rows across five POs, removed all six Submitted flags, and removed the four confirmed signed-copy test tombstones across both profiles. Restore point: `data/document-backups/20260908-145630-367709-invoice-number-repair/`. The next verified Abraj invoice is 054.
- Test fixtures that call `delete_document_record` must use `tests/isolated_client_data.py` and must delete their own fake `deleted_docs` rows during cleanup. A fake document can be a syntactically valid number, so the production counter cannot safely distinguish it from a real deleted document.
- `type_code` lives in the FILENAME only, never the path - the folder uses the human `TypeLabel` (Materials/Equipment/…). Migrations enforce this (stripped type subfolders).
- `list_quotes_for_month` requires the month folder to directly follow the year folder; a manually-nested file won't be listed.
- Client sanitisation strips path-illegal chars but keeps spaces - folder and filename client segments can contain spaces (e.g. `77 Insaat`), so any `-`-split parsing of the stem must account for multi-segment client names (handled by `_new_stem_*` in migrations).

---

## On-disk generated-document layout (authoritative)

Root: `DOCUMENTS_ROOT` (abraj default `D:\PO TOOL`, eshraqat default `D:\ESHRAQAT DOCUMENTS`; settings key `documents_root` in each profile's settings.json). Month folder = `MONTH_FOLDERS[month]` (JAN…DEC). Day folder = `DD-MM-YYYY` (Phase M.1). Current live abraj layout:

| Doc | Path (under DOCUMENTS_ROOT) | Filename (abraj) | Filename (eshraqat) |
|---|---|---|---|
| PO  | `PURCHASE ORDERS/<Year>/<MONTH>/<DD-MM-YYYY>/` | `A-<N>.pdf` | `PO-<NNNN>.pdf` |
| Quote | `QUOTATIONS/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/` | `QUT-<CODE>-<Client>-<NNN>.pdf` | `QT-<NNNN>.pdf` |
| DN  | `DELIVERY NOTES/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/` | `DN-<Client>-<NNN>.pdf` | `DN-<NNNN>.pdf` |
| INV | `INVOICES/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/` | `INV-<CODE>-<Client>-<NNN>.pdf` | `IV-<NNNN>.pdf` |

`<TypeLabel>` = Materials/Equipment/Manpower/Service; `<CODE>` = MT/EQ/MP/SV; `<NNNN>` = zero-pad 4 eshraqat sequence from `document_counters` (prefixes set by `config._apply_company_profile_defaults()`). Legacy layouts (no day folder, type-as-subfolder, `D:\ABRAJ\CSSP\PO`) are migrated forward by migrations.py; the listing scanners in the numbering modules are deliberately layout-agnostic (recursive `rglob` filtered by path segments).

### App-data disk layout (`data/`)

```
data/
  profile-registry.json          # {version:1, active_profile, profiles:[{id,label}]}
  profile-migration-v1.json      # one-time bootstrap marker + baseline row counts - DO NOT DELETE
  shared/library.db              # cross-profile autocomplete catalogs (7 SHARED_TABLES)
  profiles/
    abraj/     {po_tool.db, settings.json, assets/{logo.png, iso-certifications.png, sigs/}}
    eshraqat/  {po_tool.db, settings.json, assets/…}   # assets seeded once from static/brands/eshraqat/
  po_tool.db, settings.json      # FROZEN legacy copies - only read under DOCUMENTS_HUB_LEGACY_FALLBACK=1
```

- **Never hand-edit `data/profile-registry.json` into an invalid shape** - `registry()` returns `{}` on ANY validation failure and `active_profile_id()` silently falls back to `abraj`.
- **Do not delete `data/profile-migration-v1.json`** - bootstrap re-validates it every launch (quick_check on all 3 DBs, baseline count floors incl. `deleted_docs` tombstones); a missing/incomplete state can trigger recovery deletion of the `profiles/` trees per the pending journal. Full bootstrap semantics: [[13-PROFILES-BRANDS]].
- Eshraqat PDF rendering hard-requires `data/profiles/eshraqat/assets/logo.png` (`brand_assets` `required=True` raises; `eshraqat_render.common_context()` raises `RuntimeError` for other profiles) - no fallback to abraj assets by design. `profile_manager.ensure_seed_assets('eshraqat')` copies `static/brands/eshraqat/{logo.png,iso-certifications.png}` in once and never overwrites user files.

---

## Per-profile settings.json (`data/profiles/<id>/settings.json`)

**Purpose** - The user-override overlay for config.py, **one per profile** - abraj and eshraqat each carry their own (including the `signing` block). Read at config import, written by the Settings UI. `data/settings.json` at the root is a frozen legacy copy (legacy-fallback only), and `data/profile-registry.json` is the *other* runtime config file (active-profile selection).

**Shape** - mirrors `current_effective_settings()`: flat PO keys (`documents_root, po_root, ui_lang, po_prefix, table_*_mm, po_signature_mm, brand_color, autofit_rows` - `max_items_per_po` / `table_budget_mm` retired 2026-07-29 and dropped from live files 2026-07-30), `preferences{}` for UI-only app choices, `company{}` (Arabic PO identity under abraj), and sections `quotation`, `delivery`, `invoice`, plus a **`signing`** block NOT emitted by `current_effective_settings()`:
```
"preferences": { "theme": "light|dark", "after_export": "copy|open|reveal|none",
                 "compact_tables": false, "dn_prices_default": false },
"signing": { "signatures": [{"label": "Waleed"}, {"label":"Ali Majeed"}, {"label":""}],
             "size": {"stamp_w_mm":46.5,"stamp_h_mm":29.0,"sig_w_mm":55.0,"sig_h_mm":22.0} }
```
The live abraj file also shows per-sub-type `columns` overrides in action - e.g. `quotation.equipment.columns` extended with custom `col_2…col_6` money columns (Driver/Fuel/Insurance/Rental/Total), and `invoice.materials.columns` with re-weighted widths. Separately, `invoice.layout` (section-level, not inside a sub-type) carries `{safe_gap_mm: 6, autofit_rows: true}`. `delivery.columns` here overrides the DN base table to just `item/unit/qty` (drops Description) - widths omitted, so they auto-balance at render.

**Gotchas**
- `signing.*` is consumed by the signing/PDF-overlay code (`backend/signing.py`, which reads the active profile's block), not by `config._apply_user_settings` - round-tripping settings through `current_effective_settings()` would DROP the signing block. The Settings save path must merge, not replace.
- `preferences.*` is the durable user-settings home for dark mode and behavior toggles. `localStorage.abraj_*` in the frontend is only a first-paint/backward-compatibility cache.
- Any sub-type key present under `quotation`/`invoice` (materials/equipment/manpower/service) is treated as a `_subtype_overrides` entry - a typo'd key is silently ignored.
- Widths in overridden columns are absolute floats the user edited; they may not sum to the same total as the defaults (auto-balanced at render time).
- A settings change edits only the ACTIVE profile's file - the other profile keeps its own values.

---

## static/lottie/  (animation assets - NOT config)

The old `jsons/` folder is **gone** (deleted, tutorial MP4s in `jsons/vids/` removed entirely). The Lottie/Bodymovin animation icons now live in `static/lottie/*.json` with short names: `autofill, client, costing, export, home, refresh, preview, settings, po, quote, dn, inv, import, new, save, print, sign, theme, doc_lordicon`.

**Gotchas**
- `static/lottie/settings.json` is a **cog animation**, entirely unrelated to any settings file. Editing it changes an icon, not any setting.
- These are static frontend assets served to the WebView. `static/brands/eshraqat/` however **IS** read by backend - `profile_manager.ensure_seed_assets` copies its seed logo/ISO images into the eshraqat profile assets dir.

---

## Cross-links
[[01-ARCHITECTURE]] · [[03-BACKEND-DATA]] · [[04-BACKEND-DOCGEN]] · [[11-RUN-AND-MAINTAIN]] · [[13-PROFILES-BRANDS]]


## doc_verify_log + secrets.json pdf_protection (2026-10-07)

`doc_verify_log(doc_no, code, kind, facts, first_seen, last_seen)`, PK `(doc_no, code)`, in the **profile** DB, created lazily by `backend/doc_verify.py` on first use (no migration needed; `CREATE TABLE IF NOT EXISTS`). One row per verification code the app ever printed on a document, with the printed facts of that version; `last_seen` marks the latest. It is what lets an older genuine copy verify after the document is edited, and what keeps codes verifiable if the key is lost - back it up with the profile.

`data/secrets.json` (git-ignored, machine-level) gains `"pdf_protection": {owner_password, verify_key, lock?, allow_comments?}` - see `pdf_lock` / `doc_verify` in [[03-BACKEND-DATA]]. **Back this file up too**: without `verify_key` new codes still work but old ones verify only through the log.

Signature and stamp images (`data/profiles/*/assets/sigs/`, legacy `static/sigs/`) are **no longer tracked by git** (2026-10-07, .gitignore); they live on this PC and in its profile backups. Earlier commits on GitHub still contain them until the history is rewritten.

## LPO invoice service periods (2026-10-02)

**Unused since 2026-10-06** - the month ledger counts invoices by their date and
nothing writes this table any more; it is kept (with its trigger and migration)
rather than dropped, so no stored data is lost.

`lpo_invoice_periods` is profile-local tracking metadata, separate from invoice
content: `inv_no TEXT PRIMARY KEY`, `client_name TEXT NOT NULL DEFAULT ''`,
`lpo_no TEXT NOT NULL DEFAULT ''`, `period_start TEXT NOT NULL`,
`period_end TEXT NOT NULL`, `updated_at TEXT NOT NULL`. Dates are ISO.
Stored client/LPO scope must match the invoice before the confirmation is used.
Blank tracking dates remove the override; inference then uses printed service
dates or marks an invoice-date estimate. The `clear_lpo_period_after_invoice_delete`
trigger deletes this metadata after any invoice deletion. Declared in `_SCHEMA`
and migration `2026-10-02_lpo_invoice_periods`; whole-database backups include it.
[[03-BACKEND-DATA]] [[17-CLIENT-LPO-FIXES]].

## data/logs/activity.db - the activity and problems log (2026-10-23)

One table, `events` (owner: `backend/activity_log.py`, [[03-BACKEND-DATA]]):
`id, ts (unix, server clock), day, person, pc ("NAME (ip)"), company, source (call/screen/pc/server/login),
action (Api method or event), summary (plain words), outcome (ok/refused/app_error/server_error/network/
warning/report/info), message, detail (JSON: args, result, exception, traceback, waited_ms...), ms, ref
(document number), call_id, routine (1 = a plain successful read or click)`.
Indexes on `ts`, `(person, ts)`, `(outcome, ts)`. Pruned every 6 h: routine ok rows after 30 days, all
rows after 400. One file for both companies and every person; WAL, written by every engine and the
gateway at once. Never in git (`.gitignore`: `data/logs/`). The office server's other folders
(`data/server/`, `data/users/`) are in [[18-OFFICE-SERVER]].
