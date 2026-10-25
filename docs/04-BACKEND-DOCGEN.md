# 04 - Backend: Document Generation (numbering, generators, PDF, signing)

The document-generation engine: four parallel doc types (PO, Quote, DN, INV), each with a numbering module (next-number + on-disk save folder + month listing), a generator (column schema → pagination → Jinja HTML → PDF), plus the shared headless-browser PDF engine, the signing/overlay system, and the spreadsheet bulk-paste parser. Every numbering module and generator forks on the active company profile (Abraj vs Eshraqat - see the profile-branch overview below and [[13-PROFILES-BRANDS]]).

**Covers:** backend/po_numbering.py, backend/quote_numbering.py, backend/dn_numbering.py, backend/inv_numbering.py, backend/profile_numbering.py, backend/pdf_generator.py, backend/quote_generator.py, backend/dn_generator.py, backend/inv_generator.py, backend/eshraqat_render.py, backend/brand_assets.py, backend/pdf_engine.py, backend/signing.py, backend/spreadsheet_parser.py

Related: [[01-ARCHITECTURE]], [[02-BACKEND-CORE]], [[03-BACKEND-DATA]], [[08-STYLES-TEMPLATES]], [[09-DATA-MODEL]], [[12-PDF-LAYOUT-CONSTITUTION]], [[13-PROFILES-BRANDS]]

---

## Big picture: what is shared vs. duplicated across the 5 doc types

**Shared (single source of truth):**
- `pdf_engine.write_pdf` / `find_browser` - every generator's `render_*_pdf` ends here.
- `signing.py` - one overlay/asset system for all five (`build_template_context`, `roll_overlay`, `save_doc_overlay`, …).
- `quote_generator.amount_in_words` - **imported** by both `dn_generator` and `inv_generator`; `inv_generator` additionally imports `_money`/`_num` from quote_generator. (`_int_to_words`/`_three_digits_to_words` are internal helpers of `amount_in_words`, not imported directly by dn/inv.)
- `spreadsheet_parser.parse_file` - one parser for bulk-paste across editors.
- `config.MONTH_FOLDERS`, `config.*_ROOT`, `config.*_PREFIX`, `config.*_TYPE_CODES` - the numbering modules all read these.
- Per-document `layout.autofit_rows` overrides the type/global Auto-fit setting for PO/QUT/DN/INV renderers. If a document has no layout row, the generators fall back to `PO_AUTOFIT` or the relevant `*_LAYOUT.autofit_rows`.

**Profile branch (Abraj vs Eshraqat) - every module below forks on `config.ACTIVE_PROFILE`** (full profile subsystem: [[13-PROFILES-BRANDS]]):
- **Numbering:** all five `get_next_*_number` short-circuit to `profile_numbering.peek(kind)` under `eshraqat` (see the `profile_numbering.py` section) and return `PO-0001 / QT-0001 / DN-0001 / IV-0001 / LT-0001` (4-digit zero-pad, no client/type segment). The legacy filesystem-scan + tombstone path described per-module below is **Abraj-only**.
- **Prefixes are profile-dependent:** `config.py` (~line 500–528) rewrites them at import under eshraqat - `PO_PREFIX="PO"`, `QUOTE_PREFIX="QT"`, `INV_PREFIX="IV"` (DN stays `"DN"`); user settings can further override `po_prefix`. Abraj defaults: `A/QUT/DN/INV`.
- **Generators:** all four `render_*_html` branch to `templates/eshraqat/{po,quote,dn,inv}_page.html` + `static/eshraqat_print.css`, with shared context from `eshraqat_render.common_context(title=…, doc_no=…, pages=…)`.
- **Assets are per-profile:** every generator's `_logo_data_uri` (and signing's `_sig_dir`) reads `config.ASSETS_DIR` (= `profile_manager.assets_dir()`, i.e. `data/profiles/<id>/assets/`), **not** `static/`. `brand_assets` never falls back across profiles.
- **Import-time binding:** `config.ACTIVE_PROFILE`, `DB_PATH`, `ASSETS_DIR` and the prefix rewrites are computed at config import; `po_numbering._PO_RE` compiles at module import. Profile switches rely on app restart/module reload - don't cache these in new code.
- **Guard tests:** `tests/eshraqat_{numbering_backend,pagination_solidity,quote_rendering,dn_rendering,invoice_rendering,po_rendering,assets_backend,shared_template}.test.py` plus `smoke_test.py` / `smoke_quote.py`.

**The document HEADER (meta block) is shared too (2026-10-02).** Waleed, on IV-0005: "why is the address not below the linked quotation? ... the pdf form layout and design should be united between the types." Each generator built its own list, so the three English types put the same fields in three places, and the Eshraqat invoice alone used full-width (`wide`) rows for Client and Address, which pushed Address out from under Linked Quotation and indented those two values differently. **Eshraqat:** every quotation / DN / invoice header comes from `eshraqat_render.meta_rows(kind, doc)` - two columns, three rows: `<Doc> No. | Date`, `LPO No. (RFQ No. on a quote) | Linked Quotation (Attention on a quote)`, `Client | Address`. One label per field ("Linked Quotation", never "Source Quotation"; "Address", never "Client Address"). **Abraj** keeps its centred-list design but uses one order and spelling in all three templates: Date, `<Doc> No.`, `LPO No.`/`RFQ No.`, Client Name, Client Address. The DN number column reads `NO.` like the others (it was `No`). Header height can only have SHRUNK (the invoice lost a row), so every `*_TOP_MM` reserve stays safe - confirmed by `tests/layout_planned_matches_rendered.test.py` and `tests/eshraqat_pagination_solidity.test.py`. Guard: `tests/doc_header_unified.test.py`. **Deliberately NOT unified:** the per-type title colours (Quotation black, DN red, Invoice blue) - Waleed chose those; see the comment in `eshraqat_print.css`.

**Duplicated per doc type (copy-paste drift risk - the biggest gotcha area):**
- `_logo_data_uri()`, `_read_css()` - reimplemented in **every** generator (near-identical bodies, different CSS filename). All `_logo_data_uri` now read `config.ASSETS_DIR` (per-profile); `_read_css` switches to `eshraqat_print.css` under eshraqat in pdf_generator/dn/inv (quote reads the eshraqat CSS via a separate path inside its render branch).
- `_money()` / `_num()` - defined in quote_generator AND dn_generator (inv re-imports them; po_generator has neither).
- `estimate_row_height_mm`, `build_layout`, `_apply_col_widths`, `_cell`, `AF_BASE/AF_LINE` autofit metrics, and a stretched-row helper (named `_q_stretched_row_mm` in quote, `_stretched_row_mm` in dn, `_i_stretched_row_mm` in inv) - each of quote/dn/inv has its own near-identical copy with **different magic constants** (see per-file gotchas). The schema layer differs: **quote and inv** share `_rebalance_widths`, `_schema_for`, `_schema_for_doc` (sub-type overrides + per-doc columns); **dn** has none of those - it builds columns in `_columns()` from `config.DN_BASE_COLUMNS` and rebalances inline. PO (`pdf_generator`) still has its own simpler paginator (`paginate_po` - greedy two-pass over `estimate_row_height_mm`, no `build_layout`) and no schema/rebalance at all, but since 2026-07-29 it produces **one document over N pages** with the same page roles as the others.
- Numbering modules: `_sanitize_client`, `_day_folder`, `_global_max_number`, `list_*_for_month` are structurally cloned across quote/dn/inv. **`_sanitize_client` is cloned three times and the three copies must stay identical** - a client resolved in one module and not another files the same client's documents in two different folders. Their Abraj counters read active PDFs plus `database.deleted_document_numbers(kind)` tombstones so app-deleted numbers are not reused; the eshraqat floor in `profile_numbering._floor` honors the same tombstones - **removing either check reintroduces number reuse**. The type-code-from-filename helper is `_type_code_from_filename` in quote/inv and `_dn_number_from_stem` in dn (returns the number, not a code). PO differs entirely (regex `_PO_RE`, prev-month carryover, no client/type in the name) but also reads deleted PO tombstones.

**Common save-folder convention (Phase M.1):** `<ROOT>/[<TypeLabel>/]<Client-or-Lang>/<Year>/<MONTH>/<DD-MM-YYYY>/`. `MONTH` is the localized folder name from `config.MONTH_FOLDERS`; `DD-MM-YYYY` is the per-day leaf folder. PO omits the client/type segment; DN omits the type segment (no sub-type).

---

## backend/po_numbering.py

**Purpose** - Compute the next PO number and its day-level save folder; list POs for a month. Under Abraj, PO is the only type with month-carryover numbering and a fixed `A-NN` scheme; under eshraqat the number comes from `profile_numbering.peek("po")` (`PO-0001`).

**Key symbols**
- `_day_folder_name(d: date) -> str` - `DD-MM-YYYY` leaf folder name.
- `_month_folder(d) -> Path` - `PO_ROOT/<year>/<MONTH>`.
- `_previous_month_folder(d) -> Path` - prior month (rolls year back if January).
- `_max_po_number_in_folder(folder) -> int` - highest `NN` across `A-NN.pdf` via `rglob` (includes day subfolders); 0 if missing. Uses module regex `_PO_RE = ^{PO_PREFIX}-(\d+)\.pdf$`.
- `_max_deleted_po_number() -> int` - max from `deleted_docs` tombstones for `kind="po"`.
- `get_next_po_number(today=None) -> (po_no_str, target_folder)` - **creates** the day folder, then: under eshraqat returns `(profile_numbering.peek("po"), <day folder>)` (~line 82); under Abraj `next = max(cur_month_max, prev_month_max, deleted_max) + 1`, e.g. `("A-10", <day folder>)`.
- `list_pos_for_month(year, month) -> list[dict]` - `{po_no, number, filename, path}` sorted by number.
- `folder_for_month(year, month) -> Path`, `folder_for_date(d) -> Path`.

**Reads / writes** - Reads/creates directories under `config.PO_ROOT` (`<DOCUMENTS_ROOT>/PURCHASE ORDERS`) and reads `database.deleted_document_numbers("po")`. Filename format `<PO_PREFIX>-<N>.pdf` - `config.PO_PREFIX` is `"A"` (Abraj), `"PO"` (eshraqat), and user settings can override it.

**Calls / called by** - Reads `config.PO_ROOT`, `PO_PREFIX`, `MONTH_FOLDERS`. Called by `api.get_next_po_number` (→ JS `api().get_next_po_number`) and the PO save path.

**Gotchas**
- **Carryover is PO-only (and Abraj-only).** Next number is the max across current month, previous month, and deleted PO tombstones so numbering continues after month rollover and deleted POs are not reused. Quote/DN/INV scan the *entire* root (`_global_max_number`) plus deleted-document tombstones. None of this runs under eshraqat.
- `get_next_po_number` *creates* the folder as a side effect; the sibling modules' `*_save_folder` do NOT create (folder is made lazily on save by `pdf_engine`).
- Abraj number is **not zero-padded** (`A-9`, `A-10`); Phase N zero-padding (`:03d`) applies to quote/dn/inv only. Eshraqat numbers are 4-digit (`PO-0001`) via `profile_numbering.format_number`.
- `_PO_RE`/`_PO_NO_RE` compile at **module import** from `config.PO_PREFIX` and are anchored to `.pdf` + the prefix; changing `PO_PREFIX` (settings or profile) silently re-buckets which files count and needs a restart to take effect.

---

## backend/quote_numbering.py

**Purpose** - Next quotation number + nested save folder + month listing. Under Abraj the number carries a type code (MT/EQ/MP/SV) and client name with a global running counter; under eshraqat it is a plain `QT-0001` from `profile_numbering.peek("quote")`.

**Key symbols**
- `_sanitize_client(name) -> str` - strips `\/:*?"<>|`, defaults to `"CLIENT"`. Since 2026-09-11 it FIRST resolves the name through Clients Master Data (`client_master.canonical`), so "CCCC ltd", the full legal name and an Arabic spelling all produce the one short code the document is numbered and filed under. A name that is not on file is used as typed. The lookup is lazy and its errors are swallowed - numbering must never be blocked by the client store.
- `_type_code(quote_type) -> str` - `config.QUOTE_TYPE_CODES.get(type, "MT")`.
- `_type_label(quote_type) -> str` - folder-friendly label from `config.QUOTE_TYPES[type]["label"]`.
- `_day_folder(d) -> str` - `DD-MM-YYYY`.
- `quote_save_folder(client_name, quote_type="materials", d=None) -> Path` - `QUOTE_ROOT/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/`. Does NOT create.
- `_global_max_number() -> int` - max trailing `-N` across active `QUT-*.pdf` files under `QUOTE_ROOT` plus `deleted_docs` quote tombstones (`_TRAILING_NUM = -(\d+)$` on the stem).
- `get_next_quote_number(client_name, quote_type="materials") -> (quote_no, folder, next_n)` - eshraqat branch (~line 93) returns `profile_numbering.peek("quote")` (`QT-0001`); Abraj number = `QUT-<TYPE>-<CLIENT>-<NNN>` (Phase N: type code leads, 3-digit zero-pad).
- `_type_code_from_filename(stem) -> str` - handles both new (`QUT-<TYPE>-<CLIENT>-<N>`, code = parts[1]) and legacy (`QUT-<CLIENT>-<TYPE>-<N>`, code = parts[-2]) layouts. `_KNOWN_CODES = {MT,EQ,MP,SV}`.
- `list_quotes_for_month(year, month) -> list[dict]` - recursive scan filtered by `/<year>/<MONTH>/` path segments; client = segment before the year folder. Returns `{quote_no, filename, path, client, type_code}`. Profile branch (~lines 140/155): under eshraqat the glob is `QT-*.pdf` and `type_code` is always `""`.

**Reads / writes** - `config.QUOTE_ROOT` (`<DOCUMENTS_ROOT>/QUOTATIONS`) plus `database.deleted_document_numbers("quote")`.

**Calls / called by** - Reads `config.QUOTE_ROOT`, `QUOTE_PREFIX`, `QUOTE_TYPE_CODES`, `QUOTE_TYPES`, `MONTH_FOLDERS`. Called by `api.get_next_quote_number` / quote save.

**Gotchas**
- **Global counter** - `_global_max_number` recursively scans the whole `QUOTE_ROOT`; on a large tree this walks every PDF each call. It also reads `deleted_docs`, so app-deleted quote numbers remain consumed even after the PDF leaves the active tree.
- The **TYPE_CODE lives in the filename only, not the path** - the path uses the human `_type_label`. Renaming a `label` in config changes future *folders* but not filenames; old files stay findable by `list_*` because the scan keys on `/<year>/<MONTH>/`, not the type segment.
- `_type_code_from_filename` must keep supporting both segment orders or legacy files mis-classify their type badge.
- The module's own docstring is **stale** (describes the pre-Phase-N `QUT-<CLIENT>-<TYPE>-<N>` layout) - trust the code, not the docstring.

---

## backend/dn_numbering.py

**Purpose** - Next Delivery Note number + save folder + listing. DN has **no sub-type**, so no type folder/segment; Abraj counter is global; eshraqat uses `profile_numbering.peek("dn")` (`DN-0001`).

**Key symbols**
- `_dn_number_from_stem(stem) -> int|None` - recognizes new `DN-<CLIENT>-<NNN>`, legacy `<CLIENT>-DN-<N>`, and oldest bare `DN-<N>`; requires a `DN` token and a trailing `-N`.
- `_sanitize_client`, `_day_folder` - as in quote module.
- `dn_save_folder(client_name, d=None) -> Path` - `DN_ROOT/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/` (no type folder). Does NOT create.
- `_global_max_number() -> int` - max via `_dn_number_from_stem` across every active `*.pdf` under `DN_ROOT` plus `deleted_docs` DN tombstones (scans all PDFs, not a `DN-*` glob).
- `get_next_dn_number(client_name="") -> (dn_no, folder, next_n)` - eshraqat branch (~line 91) returns `profile_numbering.peek("dn")` (`DN-0001`); Abraj: `DN-<CLIENT>-<NNN>` (Phase N: prefix leads, 3-digit).
- `list_dns_for_month(year, month) -> list[dict]` - recursive, filtered by `/<year>/<MONTH>/`; `{dn_no, filename, path, client}`. No profile branch in the lister (unlike quote/inv) - `_dn_number_from_stem` already matches `DN-0001`.

**Reads / writes** - `config.DN_ROOT` (`<DOCUMENTS_ROOT>/DELIVERY NOTES`) plus `database.deleted_document_numbers("dn")`.

**Calls / called by** - Reads `config.DN_ROOT`, `DN_PREFIX`, `MONTH_FOLDERS`. Called by `api.get_next_dn_number` / DN save.

**Gotchas**
- `_global_max_number` iterates **all** `*.pdf` (relying on `_dn_number_from_stem` to filter) - heavier than the glob-based quote/inv scans. Any non-DN pdf containing a `DN` token in its stem could pollute the counter.
- Legacy `<CLIENT>-DN-N` filenames are recognized so the counter doesn't collide post-migration; the migration runner ([[03-BACKEND-DATA]]/migrations) renames them on first launch. Removing the legacy branch would double-issue numbers.

---

## backend/inv_numbering.py

**Purpose** - Next invoice number + save folder + listing. Mirrors `quote_numbering.py` almost exactly (type code + client, global counter under Abraj; `IV-0001` from `profile_numbering.peek("inv")` under eshraqat).

**Key symbols**
- `_sanitize_client`, `_type_code`, `_day_folder` - as quote.
- `_type_label(inv_type) -> str` - like quote but **strips the word "Invoice"** from the schema label (parent folder is already `INVOICES`).
- `inv_save_folder(client_name, inv_type="materials", d=None) -> Path` - `INV_ROOT/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/`.
- `_is_signed_copy(stem) -> bool` (2026-08-27) - `INV-….signed.pdf` is the CLIENT's counter-signed scan stored beside its invoice ([[02-BACKEND-CORE]]), never an invoice of its own. **Every filesystem scan of the invoice tree must skip it** - both scans below do.
- `_global_max_number() -> int` - max trailing `-N` across active `INV-*.pdf` files under `INV_ROOT` (skipping signed copies) plus `deleted_docs` invoice tombstones.
- `get_next_inv_number(client_name, inv_type="materials") -> (inv_no, folder, next_n)` - eshraqat branch (~line 89) returns `profile_numbering.peek("inv")` (`IV-0001`); Abraj: `INV-<TYPE>-<CLIENT>-<NNN>`.
- `_type_code_from_filename(stem)` - dual-layout (new parts[1] / legacy parts[-2]); `_KNOWN_CODES = {MT,EQ,MP,SV}`.
- `list_invoices_for_month(year, month) -> list[dict]` - `{inv_no, filename, path, client, type_code}`. Profile branch: eshraqat globs `IV-*.pdf` and returns `type_code=""`. Skips `_is_signed_copy` stems.

**Reads / writes** - `config.INV_ROOT` (`<DOCUMENTS_ROOT>/INVOICES`) plus `database.deleted_document_numbers("inv")`.

**Calls / called by** - Reads `config.INV_ROOT`, `INV_PREFIX`, `INV_TYPE_CODES`, `INV_TYPES`, `MONTH_FOLDERS`. Called by `api.get_next_inv_number` / INV save.

**Gotchas**
- Only structural difference from quote is the `"Invoice"`-stripping `_type_label`. If an INV schema `label` doesn't contain "Invoice", the label is used as-is - a mismatch here silently changes folder names.
- Same global-counter cost caveat as quote: it scans the full active tree and deleted-number tombstones.
- **`inv_no` is just the file stem here - there is no number parser to fall through.** That is why the client's signed copy needs the explicit `_is_signed_copy` skip: unlike `dn_numbering._dn_number_from_stem`, nothing else would reject `INV-MT-TDC-017.signed` and it would list as a phantom invoice (with an `.signed` number, no DB row, and a Copy button pointing at the scan). Pinned by `tests/inv_signed_copy_backend.test.py`.

---

## backend/profile_numbering.py  (eshraqat DB-backed permanent counters)

**Purpose** - Profile-scoped, database-backed document counters used by all five numbering modules when `config.ACTIVE_PROFILE == "eshraqat"`. Replaces the filesystem-scan counters with a `document_counters` table and a peek/commit lifecycle. See [[13-PROFILES-BRANDS]] for the profile subsystem it sits in.

**Key symbols**
- `_SPECS` - kind → `(prefix, DB table, doc-no column)`: `po:(PO,pos,po_no)`, `quote:(QT,quotes,quote_no)`, `dn:(DN,dns,dn_no)`, `inv:(IV,invoices,inv_no)`.
- `enabled() -> bool` - `config.ACTIVE_PROFILE == "eshraqat"`.
- `format_number(kind, n) -> str` - `f"{PREFIX}-{n:04d}"`; `parse_number(kind, doc_no) -> int` - the reverse. **Never hand-format an eshraqat number** - use these.
- `_floor(conn, kind) -> int` - `max(document_counters.last_value, parsed doc_nos in the kind's DB table, deleted_docs tombstones)`. Tombstones keep deleted numbers consumed, same guarantee as the Abraj scans.
- `peek(kind) -> str` / `peek_many(kind, count) -> list[str]` - next number(s) = floor + 1…; **do NOT consume** the counter.
- `commit(kind, doc_numbers) -> dict` - advance the counter after a **successful save**; `BEGIN IMMEDIATE` + module `RLock`; idempotent (MAX upsert - recommitting old numbers is a no-op); a multi-number batch (multi-PO save) must be **consecutive** integers.

**Reads / writes** - SQLite `document_counters` table in the per-profile `config.DB_PATH`, plus reads of the five document tables and `deleted_docs`.

**Calls / called by** - Called by the four `get_next_*_number` (peek) and by `api.py`'s save handlers (commit after each successful `save_po/save_quote/save_dn/save_inv`). See [[02-BACKEND-CORE]].

**Gotchas**
- **peek ≠ allocate.** `peek` returns the same number until `commit` runs; `api.py` must commit only after the PDF save succeeds, or the counter and the disk diverge.
- The floor honors `deleted_docs` tombstones - removing that check (or the one in the Abraj scans) reintroduces number reuse.
- `commit` batches must be consecutive. This mattered for the old multi-PO split (retired 2026-07-29); `peek_many`/batch commits are still used elsewhere.
- Guard test: `tests/eshraqat_numbering_backend.test.py`.

---

## backend/doc_layout.py  (the shared page-area engine, 2026-09-05)

**Purpose** - The page-ROLE model of [[12-PDF-LAYOUT-CONSTITUTION]] §2, in ONE
place. It used to be written out three times, as four identical arithmetic lines
plus an identical role-selection branch, in `quote_generator`, `dn_generator` and
`inv_generator`.

**Why it was extracted** - because all three copies had drifted into the *same*
defect. The role selection handled `header`, `body` and `footer` pages but not
the **single** page: when the greedy packer happened to fit every row into one
chunk, that page was handed `footer_area` - the HEADERLESS area - while still
rendering its header. A DN of 19 wrapping rows built a 234mm table under a 75mm
header on a 273mm sheet, printed on two pages, and its own footer said "Page 1 of
1". Quote and invoice carried the identical bug; their single-page guard just
caught the cases first, which is exactly how three copies hide one defect.

**Key symbols**
- `role_for(page_index, total_pages)` → `header` / `body` / `footer` / `single`.
  **`single` is not a special case of `footer`** - it reserves the header AND the
  closing block. That distinction is the whole module.
- `PageGeometry` - a frozen dataclass of one type's measured millimetres, with
  `header_area` / `body_area` / `footer_area` / `single_area`, `area(role)`,
  `area_for(page_index, total)`, `capacity(role)` and `capacity_for(...)`.
- `PageGeometry.problems()` / `.validate()` - the §4 anti-overlap invariants as
  CHECKS rather than prose: every area positive, every page with real room for
  rows, the golden rule (`cont_top_mm` must be smaller than `top_mm`, or a
  headerless page is still reserving the header band), and the single page being
  the smallest of the four. `problems()` returns; `validate()` raises
  `LayoutError`.
- `fits_on_one_page(geometry, heights)` - named because the question is not "do
  these fit the biggest page" but "do they fit the page that also carries the
  header and the closing block", which is the smallest.

**What it deliberately does NOT own** - packing, row stretching and last-page
trimming. Those genuinely differ per type (quote peels + orphan-guards, invoice
runs a light-balancing loop, invoice never stretches its last page), and
[[12-PDF-LAYOUT-CONSTITUTION]] §6 forbids copying a measured constant between
types. The extraction covers the arithmetic, not the policy.

**Reads / writes** - nothing. No config, no globals, no I/O: arithmetic over
numbers the caller has already resolved, which is what makes it testable in
milliseconds without rendering a PDF.

**Calls / called by** - imported by `quote_generator`, `dn_generator` and
`inv_generator` inside `build_layout`. `pdf_generator` (PO) still uses its own
simpler `paginate_po` - see §7 of the constitution.

**Tests** - `tests/doc_layout_engine.test.py` (19, arithmetic, fast) and
`tests/layout_planned_matches_rendered.test.py` (the end-to-end proof that the
model matches what Chrome draws).

---

## backend/pdf_generator.py  (PO generator)

> **2026-10-11 - PO pages are planned on MEASURED rows (`plan_po`), not estimated.** A-129 (8 lines, 7 pictures) printed 7 + 1 over two pages while its table filled half of page 1: every picture row was booked at a flat 22mm, and wide sign photos print at 10-14mm. That was the fourth PO broken by a guess in the estimator (A-84 typed line breaks, A-88 auto-fit, A-110 glyph widths). The PO is now laid out once in the same Chrome that prints it (`backend/pdf_measure.py`, `--dump-dom` with an injected script) and the planner reads back each row's real height, the header, the signature + stamp block (stamp image included) and the page number. Then:
> - `_pack_po(metrics)` - one packer for both paths: one page if everything fits with the signature; greedy against non-last budgets; spill the last page's tail until it fits with the signature; then **refill** the page before the signature page and keep **at least `PO_MIN_LAST_ROWS` (2) lines with the signature** (no lone line beside it). Budgets now also keep the **page number** clear on every page (`pagenum`, measured ~3.7mm on the Abraj skin) - the old budgets let a perfectly full page reach the "Page X of Y" line, hidden only because the estimate over-booked.
> - The **signature reserve setting `po_signature_mm` is a floor**: `closeout = max(measured, setting)`. It keeps room for a hand stamp on a PO printed unsigned; measuring may only grow it.
> - `plan_po` then CHECKS the finished layout with a second measurement (`_po_layout_problems`: no page box grew, the table clears the page number everywhere, and the signature/stamp + the reserve on the last page) and re-plans with +3mm per attempt (`PO_PLAN_ATTEMPTS` = 3) if anything fails. `render_pdf` finally compares the printed page count with the plan and re-plans once more if they differ.
> - **Measure synchronously in the `load` event, never on an animation frame or a promise.** The first version did (`fonts.ready` -> `requestAnimationFrame`), and `--dump-dom` can serialise the page before either runs: 4 of 6 plans of an 80-line PO came back empty and fell back to the estimate *silently*, with PDFs that mostly still looked right. `pdf_measure.measure` also retries once. `test_measuring_never_quietly_falls_back` takes single shots (no retry) on a text-only and a mixed PO and catches the old timing.
> - **No browser** (or a failed measurement) falls back to the estimate - a save never fails because of measuring. The estimate itself is now picture-SHAPE aware (`_image_row_mm`: column width x aspect, capped, +`PO_IMAGE_CELL_EXTRA_MM`) because it still drives the editor's live "N pages" hint (`po_page_count`, which must not start a browser per keystroke). After a render, `po_page_count` returns the count that PDF really has (`_MEASURED_COUNTS`).
> - `render_html` / `render_pdf` share `_po_render_pages(pages, ...)`, the template half that renders exactly the pages it is given. Cost: two extra headless launches per save, ~3.3s total for A-129 (was ~1.4s).
> - Guarded by `tests/po_measured_pagination.test.py`: real PDFs read back with PyMuPDF, independently of the planner - nothing overlaps (page number, signature/stamp, reserve) and nothing is wasted (a page ends only when the next row truly did not fit) - plus packer unit rules and the Eshraqat skin. Six deliberate mutations of the planner, each caught.

**Purpose** - Renders the PO HTML preview and PDF. **2026-07-29: a PO is now ONE document over N pages**, like quote/dn/inv. It used to split an overflowing item list into *multiple POs* (one number + one PDF each), so a 40-item order became two unrelated purchase orders.

**Key symbols**
- `_refresh_config_if_changed()` - hot-reloads `config.py` (compares mtime) so the row model can be tuned live; called at the top of `paginate_po`.
- `_logo_data_uri()`, `_read_css()` - embed `logo.*` (from per-profile `config.ASSETS_DIR`, not `static/`) / CSS as data-URI/text (duplicated pattern). `_read_css` (~line 70) picks `eshraqat_print.css` under eshraqat, else `po_styles.css`.
- `estimate_row_height_mm(row, autofit=False, col_widths=None) -> float` - lines per column via **`_po_chars_per_line(col_widths)`**, which derives the budget from each column's own width: `max(PO_MIN_CPL, effective_pct * PO_CHARS_PER_PCT)` with `PO_CHARS_PER_PCT = 0.9`. Same formula `quote_generator` has always used (`cpl = max(6, width*0.9)`). Fixed mode uses `max(TABLE_MIN_ROW_MM, PO_ROW_CONTENT_FLOOR_MM) + extra_lines * TABLE_LINE_HEIGHT_MM`; Auto-fit uses its own measured CSS geometry `PO_AF_BASE_MM=7.0 / PO_AF_LINE_MM=5.3`. Since 2026-09-05 the fixed first term is the height the page ACTUALLY draws (see the gotcha below), not a guess at it.
- `PO_ROW_CONTENT_FLOOR_MM = 8.2` (2026-09-05) - the shortest a row can render whatever the setting says. `height` on a table cell is a MINIMUM: one line of 12pt plus the 6px padding and border needs 8.2mm (measured in Chrome by collapsing `--po-row-mm` to 0) and the browser will not go below it, so booking less would put the estimate back under the render.
- `PO_PAGE_SAFETY_MM = 2.0` (2026-09-05) - slack subtracted from every page budget. The estimate is now EXACT rather than generous, so a page filled to the last tenth of a millimetre is one rounding from spilling. Mirrors `quote_generator.Q_PAGE_SAFETY_MM`.
- **`paginate_po(rows, po_no, layout=None) -> list[page dict]`** (2026-07-29, replaces `split_into_pos`) - lays one PO's rows over as many pages as they need. Page dict: `{po_no, page_index, total_pages, show_header, is_last, rows}`; `po_no` is the **SAME on every page**, and each row's `index` is **continuous across pages** (page 2 starts at 31, it does not restart at 1). Rows carry `index` + `filled=True` as before. The document `layout.autofit_rows` is resolved before measuring rows, so the page plan and the CSS use the same mode.
  - **Two-pass packing, NOT a fixed-point loop.** Only the last page reserves the signature band, and which page is last isn't known until packing ends. Guessing and re-packing *oscillates*: with 15 rows of 14mm, "assume 1 page" overflows the 173mm single-page budget → 2 pages, but "assume 2 pages" gives page 1 the 227mm non-last budget so all 15 fit again → 1 page, forever. The first implementation did exactly that and fell through to a single **222mm page - the table ran 49mm into the signature**. So: pass 1 packs greedily against the *non-last* budget; pass 2 spills trailing rows off the final page until it honours the last-page budget. Terminates (each spill shortens the last page or adds one) and never leaves a page empty. Guarded by `tests/po_pagination_backend.test.py`.
- `_po_geometry() -> (usable, header, cont_top, closeout)` - **measured** page geometry for the active skin (read back from a rendered PDF with fitz, not estimated). Abraj: `PO_USABLE_MM 277 / PO_HEADER_MM 50 / PO_CONT_TOP_MM 0` + `config.PO_SIGNATURE_MM 54`. Eshraqat is a different document and has its own set (`PO_ESH_USABLE_MM 260 / PO_ESH_HEADER_MM 50 / PO_ESH_CONT_TOP_MM 0 / PO_ESH_CLOSEOUT_MM 46`), mirroring the `*_ESH_PAGE_CHROME_MM` pattern. Per docs/12 §6.1 these are per-doc-type **on purpose** - never share them with the quote/dn/inv constants. **`PO_ESH_USABLE_MM` is 260, not the 250 the other eshraqat skins imply**, because the redesigned Arabic PO prints no footer band and `.esh-po-document .esh-page` therefore drops its bottom padding 20mm → 10mm ([[08-STYLES-TEMPLATES]]).
- `_po_page_budget(is_first, is_last) -> float` - table mm (thead included) for one page role, **minus `PO_PAGE_SAFETY_MM`**. Abraj before the margin: single 173 | first 227 | middle 277 | last 223.
- `po_page_count(rows, po_no="", layout=None) -> int` - how many pages the rows print on (always one PO). Replaces `page_po_numbers`; receives the same layout as render/save so the editor note cannot disagree with the PDF.
- `_po_effective_widths(col_widths) -> dict` (2026-09-28) - the width PERCENT each column is actually drawn at. `_PO_PDF_DEFAULTS` (col-name 23.9, col-desc 20.0, col-unit 10.2, col-qty 8.0, col-company 16.7) overridden by the editor's widths, scaled to fill `100 - _PO_FIXED_PCT` (`4.7 + 16.6`, col-idx + col-price stay fixed). **`_po_col_style` and `_po_chars_per_line` both read it**, so the CSS and the wrap budget are the same numbers by construction.
- `_po_col_style(col_widths) -> str` - those percentages as a CSS override. Returns `""` when no widths given.
- `_po_autofit(layout=None) -> bool` - document `layout.autofit_rows` wins; falls back to `config.PO_AUTOFIT`.
- `_po_source_quote(rows)`, `_po_suppliers(rows)` - derive `source_quote_no` / `supplier_name` from the rows; fed to the **eshraqat PO template only**.
- `render_html(po_no, po_date, rows, inline_css=False, signing_overlay=None, col_widths=None, layout=None) -> str` - pulls signing via `signing.build_template_context("po", …)`; passes `autofit` from `_po_autofit(layout)`. **Eshraqat branch:** renders `eshraqat/po_page.html` with `eshraqat_render.common_context(title=config.ESHRAQAT_IDENTITY["po_title_ar"], …)` + `source_quote_no`/`supplier_name` and `eshraqat_print.css`; Abraj renders `po_page.html`. The eshraqat PO is **Arabic + RTL** (2026-08-09) - the only one of the four eshraqat skins that is - so its `document_title` is Arabic while quote/dn/inv still pass English titles.
- `render_pdf(...) -> Path` - `render_html(inline_css=True)` → `pdf_engine.write_pdf`.

**Reads / writes** - Reads `config.ASSETS_DIR/logo.*` (per-profile), `po_styles.css` or `eshraqat_print.css`, `config.TEMPLATES_DIR/po_page.html` or `eshraqat/po_page.html`. Writes PDF via pdf_engine. Reads signing assets + doc_meta via signing.

**Calls / called by** - Calls `pdf_engine.write_pdf`, `signing.build_template_context`. Called by `api.save_po` / preview (→ JS `api().save_po`, `api().render_po_html` etc.). `po_page_count` pre-counts the PAGES a save will emit (shown in the editor's page-count note).

**Gotchas**
- **PO paginates one document (since 2026-07-29)** - it no longer mints a PO number per overflow. `paginate_po` replaced `split_into_pos`; `po_page_count` replaced `page_po_numbers`; `api.save_po` writes ONE PDF + ONE DB row (its `saved` return stayed a one-element list so the frontend contract didn't change). `config.MAX_ITEMS_PER_PO` and `config.TABLE_BUDGET_MM` are **retired** (the cap only existed to force the split; one flat budget can't describe a first page and a middle page at once). Old `settings.json` files still carry those keys - they are deliberately ignored.
  - It also fixed a latent bug: `update_po` always rendered *all* rows into one PDF, so re-saving an oversized PO produced a document whose page 2 was labelled with a **different PO number**.
  - The PO number no longer needs to be parseable - `split_into_pos` did `int(base_po_no.split("-")[1])` and crashed on any other format.
- **THE WRAP BUDGET COMES FROM THE COLUMN WIDTH, and a fixed table cannot be right (2026-09-28).** It used to be `_CHARS_PER_LINE = {name 14, description 12, company 11, unit 6}`, "derived from each column's width in mm vs ~2.7mm per Arabic glyph at 12pt" and rounded down on purpose so height was over-estimated. **The glyph figure was wrong.** Measured through the real renderer (render, then read the first length that wraps back with fitz), an Arabic glyph at this size averages ~1.73mm, so every column's capacity was understated by 64-100%:

  | column | booked capacity | real capacity | width % | chars per % |
  |---|---|---|---|---|
  | description | 12 | **22** | 20.0 | 1.100 |
  | name | 14 | **25** | 23.9 | 1.046 |
  | company | 11 | **18** | 16.7 | 1.078 |
  | unit | 6 | **12** | 10.2 | 1.176 |

  Reported from **A-110**: 22 rows printed 16 + 6 with an obvious gap at the bottom of page 1. Four of its sixteen page-1 rows were single-line rows booked as two (`بوري مربع` / `4 انج 2 ملم 6 متر` is 17 characters and drew on one line at 10.32mm, booked 15.8mm). Page 1 was packed to 215.3mm of a 225mm budget while drawing ~193mm; the 22mm those four wasted is three more rows. It now packs **19 + 3**.

  **The second half of the defect is why no single number could fix it: PO column widths are PER DOCUMENT** (`doc_columns` - 43 live POs carry a 21% description column, 13 carry 24%, three carry 28.39%) and `paginate_po` never received them, so the planner and the renderer disagreed about how wide a column was. `col_widths` now flows `render_html` -> `paginate_po` -> `estimate_row_height_mm`, and `Api.po_page_count` takes it too (the editor's "this will print on N pages" warning counted at the defaults while the PDF rendered at the dragged widths). `_po_effective_widths` is the ONE place the scaling happens, shared by `_po_col_style` (which emits the CSS) and `_po_chars_per_line` (which turns it into a budget), so they cannot drift apart again.

  **The margin is still deliberate, only its size was wrong.** 0.9 leaves ~14% of headroom under the tightest measured column. Under-booking is the unrecoverable direction - the table runs into the absolutely-positioned signature - so the budget must never EXCEED what a column really fits. Both directions are now pinned: `tests/po_row_height_matches_render.test.py` guards overflow, `tests/po_row_height_is_not_over_booked.test.py` guards the waste and **measures the real wrap point rather than asserting a constant**, because a constant is exactly what drifted. Measured impact across the 105 live Abraj POs: 4 drop a page, 101 unchanged, none get worse.
  - **They are calibrated for the Abraj skin's 12pt Arial and are shared by both skins.** The eshraqat PO renders at 9pt in a slightly wider table, so the same text needs roughly a third fewer lines than the estimator books - measured 2026-08-09, an eshraqat page packed to a 212mm estimate rendered 135mm tall, i.e. eshraqat POs paginate ~40% earlier than they need to. That is the SAFE direction (over-reserving), so it is left alone; making it skin-aware needs the full measure-and-verify protocol in [[12-PDF-LAYOUT-CONSTITUTION]] §8.
- **`estimate_row_height_mm` must count explicit newlines** (fixed 2026-07-29). `po_styles.css` sets `white-space: pre-wrap` so a newline typed into a PO row textarea survives into the PDF, but the estimator only ever divided by chars-per-line - a two-line typed cell was estimated at 9mm against a real 11.9mm. **Under-estimating is the dangerous direction** (the table reaches the signature). Each segment between newlines now wraps independently and the segments are summed, which is what the browser does.
- **Auto-fit must change pagination, not only CSS (2026-09-07, A-88).** A-88 had 46 mostly one-line rows and stored `layout.autofit_rows=true`. The template drew compact rows, but `paginate_po` still called the fixed 10.3mm estimator, producing a sparse 20 + 23 + 3 split. Auto-fit now uses the measured 7.0mm base / 5.3mm extra-line model in the planner and renders A-88 as 30 + 16 over two pages. `render_html`, `po_page_count`, the editor note, and the save response all pass the same document layout. Guarded by unit and real-Chromium cases in `tests/po_pagination_backend.test.py`.
- **THE ROW HEIGHT IS THE SETTING, and that is now literally true (2026-09-05).** It was not, and the gap shipped a wrong document: `po_styles.css` set a hard `height: 26px` on the item cells with the default **content-box**, so the 6px padding and the border were ADDED and a row drew **10.32mm** - while the paginator booked `TABLE_MIN_ROW_MM`, then **7**. Every row was under-booked by a third, which stays invisible until a PO is long enough to break: **A-84** (29 rows) planned 2 pages, printed on 3, and its own footers read "Page 1 of 2" / "Page 2 of 2" on a document already sent. The same defect made the PO-settings row-height box appear dead - `table_row_mm` was passed into `po_page.html` and **never used**, so the number moved the page breaks and nothing on the page. Now: the template publishes `--po-row-mm` / `--po-head-mm`, `po_styles.css` consumes them with **`box-sizing: border-box`** (padding and border INSIDE the height), and the estimator reads the same constants. **Change the CSS rule and you must change `estimate_row_height_mm` with it.** `TABLE_MIN_ROW_MM` default is now **10.3** (renders 10.32) and a stored legacy `7` is lifted by migration `2026-09-05_po_row_mm_is_now_rendered` so existing POs keep their look. `TABLE_LINE_HEIGHT_MM = 5.5` (measured 5.30) and `TABLE_HEADER_MM = 12` (renders 10.32) stay rounded UP - that slack is deliberate.
- **`.items-table th, td` matches the `<thead>` too.** The header therefore needs its OWN height (`--po-head-mm`), or a taller row setting silently grows the header while the paginator still books `TABLE_HEADER_MM` - 20mm rows overflowed for exactly that reason before the split. Guarded by `tests/po_row_height_matches_render.test.py`.
- `_po_col_style` percentages must stay in sync with `po_styles.css` class widths; the fixed `_PO_FIXED_PCT` must match the idx+price CSS widths or columns won't sum to 100%.
- Config hot-reload only reloads `config`, not this module; changing constants defined *here* still needs a restart.

---

## backend/quote_generator.py  (Quote generator - also the shared money/words source)

**Purpose** - Schema-driven quotation HTML + PDF. Home of the shared `amount_in_words` / `_int_to_words` reused by DN and INV. Content-aware pagination with a footer (terms + signature) pinned to the last page.

**Key symbols - numbers→words (shared):**
- `_three_digits_to_words(n) -> str`, `_int_to_words(n) -> str` (0 → `"No"`; scales Thousand/Million/Billion), `amount_in_words(amount, currency="USD") -> str` → `"<D> Dollars and <C> Cents"` (singular/plural aware), or `"<N> Iraqi Dinars"` for `currency="IQD"` - **whole units, no "and … Cents" tail** (the dinar has no subunit in practice).
- `_money(v, currency="USD") -> str` → `"$1,234.56"`, or `"IQD 1,234,568"` for IQD (thousands-separated, zero decimals); `_num(v) -> float` (safe). **Imported by inv_generator; DN has its own copies.**
- The `currency` argument is **optional and defaults to USD**, so quote/DN callers are untouched - only `inv_generator` passes it (2026-07-29).

**Key symbols - schema / widths:**
- `_qov(key, default)`, `_q_autofit(layout=None)` - read document `layout.autofit_rows` first, else `config.QUOTE_LAYOUT`.
- `_rebalance_widths(cols, target_total=96.4) -> cols` - missing-width cols split the remainder; if explicit widths exceed target, scale all down. (**Note target 96.4 here vs 95.4 in inv/dn.**)
- `_schema_for(quote_type) -> dict` - applies `config.QUOTE_SUBTYPES_OVERRIDES[type]`: `columns` override (Phase L.4 inherits width/kind from default by key; migrates stale head text via `STALE_HEADS`, e.g. `image: ["IMAGE (IF ANY)"]`), then `column_heads` renames, then rebalance. Returns base unchanged if no override.
- `_schema_for_doc(quote) -> dict` - Phase T: if the document carries its own `columns`, build the schema from those (inherit width/kind from type default), else fall back to `_schema_for`.
- `_terms_for(quote_type) -> list|None` - per-sub-type terms override; `None` only when **not configured**. An explicitly EMPTY list is returned as-is ("no terms"), never collapsed to `None`.
- `_apply_col_widths(schema, doc) -> schema` - overlay live editor `doc["col_widths"]={key:pct}`, then rebalance. No-op when absent.

**Key symbols - layout:**
- `_footer_block_mm(terms) -> float` - height of terms block (wrapped at `Q_TERM_CPL=48`), floored at `Q_SIGN_MM`.
- `estimate_row_height_mm(row, schema, autofit=False) -> float` - tallest driver of: wrapped text (`cpl = max(6, width*0.9)`, counts lines **per explicit `\n`**), populated image cell (`Q_IMAGE_ROW_MIN_MM`), base row. Autofit uses `Q_AF_BASE_MM=6.5 / Q_AF_LINE_MM=4.4`.
- `_q_stretched_row_mm(area, header, extra_fixed, count) -> float` - per-page row height clamped to `[Q_BASE_ROW_MM, Q_MAX_ROW_MM]`.
- `build_layout(items, terms, schema, autofit=None) -> list[page]` - **Phase X.27 page-ROLE packing** (governed by [[12-PDF-LAYOUT-CONSTITUTION]]): single-page fast path (`single_cap`, unchanged); else pack page 1 at `header_cap`, every later page at `body_cap` (headerless body/last pages reserve `Q_CONT_TOP_MM≈2` instead of the 70mm header, so middle pages FILL), peel the last page to `footer_cap`, plus a light orphan guard. No even-balancing. Page dict: `{show_header, show_footer, is_last, fill_table, table_area_mm, row_min_mm, rows(+_index), page_index, total_pages}`. **Phase Y.2 - per-skin chrome:** all four page areas subtract `_q_page_chrome_mm()` (`Q_ESH_PAGE_CHROME_MM=34` under the Eshraqat profile, `0` for Abraj) so the shared engine accounts for the Eshraqat brand skin's extra per-page chrome (~20mm `.esh-page` bottom padding + taller thead) and never spills onto a near-empty page. `dn_generator` / `inv_generator` have the identical `_dn_page_chrome_mm()` / `_page_chrome_mm()` (`*_ESH_PAGE_CHROME_MM=34`). Abraj output is byte-identical (chrome 0). Rules for retuning/adding a skin + the guard test (`eshraqat_pagination_solidity`) live in [[12-PDF-LAYOUT-CONSTITUTION]] §6.1.

**Key symbols - render:**
- `_logo_data_uri()` (reads per-profile `config.ASSETS_DIR`), `_read_css()` (quote_styles.css; the eshraqat render branch reads `eshraqat_print.css` via its own path), `_build_columns(schema)` (prepends `_no` index col, width 3.6), `_cell(row, col, qty, rate)` (kinds: index/image/computed/money/text). **A `computed` TOTAL renders empty when the row's RATE cell is blank** (2026-07-30): the guard reads the RAW `row["rate"]`, not the coerced `rate` float, because a blank cell and a typed `0` are indistinguishable once coerced. The old `if (qty or rate)` test let a quantity alone force `$0.00`, which tells the client an unpriced line is FREE. A deliberately typed `0` still prints `$0.00`. Guarded by `tests/quote_blank_rate_total.test.py`. **`dn_generator._cell` still has the old `(qty or rate)` form** - same latent issue on priced DNs, left alone deliberately; `inv_generator` prints `_money(0)` on purpose.
- `render_quote_html(quote, inline_css=False) -> str` - schema (`_schema_for_doc` → `_apply_col_widths`) → columns → terms resolution (**doc terms → sub-type override → schema default**) → grand total (materials only, `qty*rate`) → document-aware autofit → `build_layout` → attach `display_rows` → render `quote_page.html` with `signing.build_template_context("qut", …)`. **Eshraqat branch (Phase Y.3):** the header title is the fixed document type **"Quotation"** for every sub-type (was `schema["label"]` = Materials/Equipment/…); the sub-type still drives columns + folder, just not the header. `inv_generator` matches: Eshraqat invoice title is **"Invoice"** (was the sub-type label). DN/PO already used the doc-type name. **Phase Y.4 - match the approved Eshraqat quotation design:** `_strip_image_columns(schema)` drops any image-kind column (text-only table, widths rebalanced); meta label is "Client Address"; the closing totals render a 3-row block **Subtotal / Withholding Tax (3.3%) [Included|Not Included] / Grand Total** via `total_rows` (withholding is a status, not a deduction → Subtotal == Grand Total). `eshraqat/quote_page.html` passes `total_rows` to the `amount_totals` macro. CSS: `.esh-amount-block` uses `fr` tracks (was `64% 36%` + 6mm gap → overflowed and clipped the right-aligned totals); `.esh-document-title` is ink black (was brand red). The terms+signature block is moved OUT of `.esh-closing-zone` to be a direct child of `.esh-page`, and quote-scoped CSS (`.esh-quote-document .esh-page{display:flex;flex-direction:column}` + `.esh-terms-signing{margin-top:auto}`) pushes it to the **bottom** of the page (above the absolute footer) instead of floating high under the totals - overlap-safe because the auto margin collapses to 0 on a full last page. Scoped to `.esh-quote-document` so DN/INV/PO closings are unaffected. **Phase Y.8 (per user):** the Eshraqat quotation's **ATTENTION meta row is fixed to "Admin Manager"** - it no longer reads `attention_to`/`attention`/`attn` off the document (Eshraqat quotes are always addressed to the Admin Manager). Eshraqat-only; the Abraj quote has no Attention row.
- `render_quote_pdf(quote, out_path) -> Path`.

**Reads / writes** - Reads `config.QUOTE_TYPES/QUOTE_SUBTYPES_OVERRIDES/QUOTE_LAYOUT/QUOTE_COMPANY/QUOTE_BRAND_COLOR`, `quote_styles.css` (or `eshraqat_print.css`), `config.ASSETS_DIR/logo.*`, `quote_page.html` (or `eshraqat/quote_page.html` + `eshraqat_render.common_context`, ~line 604). Writes PDF. Reads signing assets/doc_meta.

**Calls / called by** - Calls `pdf_engine.write_pdf`, `signing.build_template_context`. Called by `api.save_quote` / preview (→ JS `api().save_quote`, `api().render_quote_html`). **Exports `amount_in_words` (consumed by dn + inv) and `_money`/`_num` (consumed by inv only; dn defines its own).**

**Gotchas**
- **Magic constant drift:** `_rebalance_widths` target is **96.4** here but **95.4** in inv/dn; `Q_AF_BASE_MM=6.5` vs DN/INV 5.8; index-col width 3.6 vs 4.6 elsewhere. These are per-type-tuned - copy-editing between generators will subtly break pagination.
- Row-height estimate is a *heuristic* mirroring the CSS; if `quote_styles.css` metrics (font, padding, borders) change, the many `Q_*_MM` constants (esp. `Q_ROW_OVERHEAD_MM=2.6`, `Q_PAGE_SAFETY_MM=4.0`) must be re-tuned or the table overflows onto a phantom page (see the Phase X.24 header-page comment).
- **Phase X.24/X.27 - render overhead reservation.** Quote rows render ~2.3mm TALLER than the `--q-row-mm` height the engine sets (cell padding + collapsed border + line leading), so a *filled* table overshoots its area by ~overhead×rows. X.24 first fixed the header-page orphan by reserving `count·Q_ROW_OVERHEAD_MM + Q_PAGE_SAFETY_MM` in the fill. **X.27 update:** now that headerless middle pages pack FULL (no empty header band to absorb the overshoot), that reservation is applied to **every** filled non-last page, not just page 1. Re-verified: 101-item quote = 10 pages, every middle page ≥~90% full, last data row ≤262mm (no phantom page). **This overshoot is quote-only** - DN/INV render tight (~0) and must NOT get the reservation (see their gotchas).
- Grand total exists **only for the materials sub-type** (`schema["grand_total"]`).
- Terms precedence is doc → sub-type override → schema default; changing that order changes which terms print. **An EMPTY list at any level STOPS the chain** (deliberate "no terms"); only an absent/non-list value falls through. Do **not** rewrite this as an `or` chain - `[] or default` restored the defaults, so deleting every term appeared to do nothing to the PDF (fixed Y.8; guarded by `assert_terms_deletion_is_honoured` in `eshraqat_quote_rendering`). Both quote templates wrap the terms block in `{% if terms %}` so an empty list can't leave an orphan "Terms & Conditions" heading.
- `estimate_row_height_mm` counts wrapped lines per `\n` segment - required so multi-line autofit rows don't under-estimate.

---

## backend/dn_generator.py  (Delivery Note generator)

**Purpose** - Schema-driven DN HTML + PDF (English LTR). Recipient|Supplier sign boxes pinned to the bottom of the last page; optional price columns + grand total. Reuses `amount_in_words` from quote_generator.

**Key symbols**
- `_ov(key, default)`, `_dn_autofit(layout=None)` - read document `layout.autofit_rows` first, else `config.DN_LAYOUT` (defaults in `_DN_LAYOUT_DEFAULTS`).
- `_money`, `_num` - **own copies** (not the quote ones).
- `_logo_data_uri` (per-profile `config.ASSETS_DIR`), `_read_css` (~line 118: `eshraqat_print.css` under eshraqat, else `dn_styles.css`).
- `_columns(include_prices, base_override=None) -> list[dict]` - prepends `_no` (width 4.6); base cols from per-doc `base_override` → `config.DN_BASE_COLUMNS_OVERRIDE` → `config.DN_BASE_COLUMNS`; ensures each has a `kind` (inherit by key); distributes missing widths; appends `config.DN_PRICE_COLUMNS` when `include_prices`; rebalances to **target 95.4**.
- `estimate_row_height_mm(row, cols, autofit=False)` - text-only line count (`cpl = max(6, width*0.9)`, per-`\n`); base `DN_BASE_ROW_MM` (8) / autofit `DN_AF_BASE_MM=5.8, DN_AF_LINE_MM=4.3`.
- `_footer_block_mm() -> float` = `DN_SIGN_MM` (28; two sign boxes, no terms).
- `_stretched_row_mm(area, header, extra_fixed, count)` - clamp `[DN_BASE_ROW_MM, DN_MAX_ROW_MM]`.
- `build_layout(items, cols, include_prices=False, autofit=None) -> list[page]` - **Phase X.27 page-ROLE packing** (see [[12-PDF-LAYOUT-CONSTITUTION]]): single-page fast path; else page 1 at `header_cap`, later pages at `body_cap` (headerless pages reserve `DN_CONT_TOP_MM≈2`, not the 75mm header, so middle pages fill), peel last to `footer_cap`, light orphan guard. `grand_h = DN_GRAND_ROW_MM` when prices on. No overhead reservation (DN renders tight).
- `_cell(row, col, qty, rate)` - kinds index/computed/money/text (no image kind, unlike quote).
- `_apply_col_widths(cols, dn) -> cols` - overlay `dn["col_widths"]`, then **renormalize so the data cols keep their original combined width** (NO. col share untouched) - different normalization strategy than quote/inv.
- `render_dn_html(dn, inline_css=False) -> str` - cols (`_columns` per-doc override → `_apply_col_widths`) → document-aware autofit → `build_layout` → display_rows → grand total spans (`label_span=2` NO+Item, `amount_span=2` Rate+Total, words = middle) → render `dn_page.html` with `signing.build_template_context("dn", …)`. **Eshraqat DN (Phase Y.5, per user):** the meta grid now comes from `eshraqat_render.meta_rows("dn", …)` (2026-10-02 - the same layout as the quotation and invoice, see the Big picture); the grand-total row drops the "Total" label (words cell colspans `label_span+words_span` → reads *amount-in-words + amount*); the "Thank you…" closing line is removed; and only the **Recipient** signature remains (Supplier signature dropped), with `.esh-dn-closing-zone` raised to `bottom:26mm`. All in `templates/eshraqat/dn_page.html` + the DN `meta_rows`. Verified single + full-last-page: no overlap.
- `render_dn_pdf(dn, out_path) -> Path`.

**Reads / writes** - Reads `config.DN_BASE_COLUMNS(_OVERRIDE)/DN_PRICE_COLUMNS/DN_LAYOUT/DN_COMPANY/DN_BRAND_COLOR`, `dn_styles.css` (or `eshraqat_print.css`), `config.ASSETS_DIR/logo.*`, `dn_page.html` (or `eshraqat/dn_page.html` + `eshraqat_render.common_context`, ~line 426). Writes PDF. Reads signing.

**Calls / called by** - Imports `amount_in_words` from `quote_generator`. Calls `pdf_engine.write_pdf`, `signing.build_template_context`. Called by `api.save_dn` / preview (→ JS `api().save_dn`, `api().render_dn_html`).

**Gotchas**
- `_apply_col_widths` **preserves the data-columns' combined width** (renormalizes to `orig_sum`), whereas quote/inv rebalance to their fixed target. Consistency assumption differs - don't cross-port.
- DN has **no image column kind** in `_cell`; a column with `kind:"image"` renders as raw text.
- Grand total spans assume ≥4 columns; `words_span = max(1, n-4)`. With price columns off, no grand total.
- Only the **Supplier** slot is signed digitally (see signing `DOC_SLOTS["dn"] = ["supplier"]`), even though the template shows Recipient+Supplier boxes.
- Uses `amount_in_words` from quote_generator - a change to that function silently changes DN and INV totals-in-words too.
- **Phase X.24/X.27 - DN does NOT share the quote's render overhead, but DID get the page-role fix.** `dn_styles.css` renders rows at (or just under) their set `--dn-row-mm` height (~0 overhead), so a filled page cannot overshoot - **do NOT copy quote's `Q_ROW_OVERHEAD_MM` reservation in.** Separately, X.27 gave DN the same page-ROLE packing as quote (headerless middle/last pages stop reserving the 75mm header) - verified: 40-item DN went to 22+18 with no overflow, 3-item stays single-page. The two changes are independent.

---

## backend/inv_generator.py  (Invoice generator)

> **2026-10-11 - per-invoice "Withholding line" switch.** `layout.show_withholding` (default true) decides whether the "Withholding Tax (x %)" row prints, on both skins. `_inv_show_withholding(inv)` reads it; `build_layout(..., show_withholding=)` stops reserving `INV_TAX_ROW_MM` on the last page when it is off (else 8mm is booked and nothing drawn - docs/12 SS4.6), and publishes `tax_row_mm` 0. The Abraj template now prints `withholding_status` (it printed the config label and ignored the per-invoice `withholding_included` flag). `database.set_doc_layout` allow-lists `show_withholding` beside `autofit_rows` - it silently dropped any other key. Editor: a small checkbox in the invoice footer (`#invShowWithholding`, `invSyncWithholdingSwitch` in `static/inv.js`) that also hides the editor's own tax row. First used on INV-EQ-JUNE-105. Guarded by `tests/inv_withholding_switch.test.py` (3/3 mutations).

**Purpose** - Schema-driven invoice HTML + PDF. Adds two in-table trailing rows (Withholding Tax + Total-in-words) and a Payment Info card + signature pinned to the last page. Reuses `amount_in_words`, `_money`, `_num` from quote_generator.

**Key symbols**
- `_iov(key, default)`, `_inv_autofit(layout=None)` - read document `layout.autofit_rows` first, else `config.INV_LAYOUT`.
- `_rebalance_widths(cols, target_total=95.4)` - like quote but **also scales UP** when total is under target (guards stale narrow overrides).
- `_schema_for(inv_type)` / `_schema_for_doc(inv)` - same structure as quote (Phase L.4 inherit + Phase T per-doc columns).
- `_logo_data_uri` (per-profile `config.ASSETS_DIR`), `_read_css` (~line 186: `eshraqat_print.css` under eshraqat, else `inv_styles.css`), `_columns(schema)` (prepend `_no` width 4.6).
- `estimate_row_height_mm(row, cols, autofit=False)` - base `INV_BASE_ROW_MM=14`; autofit `5.8/4.3`.
- `_footer_block_mm()` = `INV_PAYMENT_MM` (42).
- `_i_stretched_row_mm(...)` - clamp `[INV_BASE_ROW_MM, INV_MAX_ROW_MM=17]`.
- `build_layout(items, cols, autofit=None) -> list[page]` - **Phase X.27 page-ROLE packing** (page 1 `header_cap`; middle `body_cap` no longer reserving the 88mm header; last `footer_cap`); reserves `INV_TAX_ROW_MM + INV_TOTAL_ROW_MM` inside the last page's table; **last/single page never stretches** (row_min = max of natural heights) to avoid the "5-item rows bloat and overlap the footer" bug; filler pages stretch. See [[12-PDF-LAYOUT-CONSTITUTION]].
- **`_append_dn_pages` attaches the client's signed scan when present (2026-07-30).** A DN doc carrying `signed_copy_image` contributes ONE `.dn-signed-copy-page` sheet (A4 186x273mm, `object-fit: contain` so the client's document is never cropped or stretched) INSTEAD of its re-rendered pages - see `_SIGNED_COPY_STYLE` / `_signed_copy_page_html`. The scan rides as a data URI so the existing SINGLE headless-Chrome pass still produces invoice + attachment together; no PDF library is involved. `api._resolve_dn_docs` decides which copy each line gets.
- **`_merge_signed_pdfs(pdf_path, dn_docs)` (2026-07-30)** - splices a **PDF** signed copy into the finished invoice, which HTML cannot carry. The appender emits a one-page placeholder holding `signed_copy_token` (`_signed_copy_placeholder_html`, 6pt near-white text) and this step finds that page **by searching the rendered text** and replaces it with the scan's pages. **Do not switch it to a computed page index** - the invoice's page count and each DN's are both estimates and drift (see the PO pagination gotcha); a text search cannot. Each incoming page goes through `_place_on_a4`, which centres and scales it onto A4 preserving aspect ratio (a Letter or phone-aspect scan would otherwise change sheet size mid-document); an already-A4 page is passed through untouched. **It stands a rotated scan up first (2026-09-02).** A scanner very often writes a **landscape mediabox plus a `/Rotate` flag** telling the viewer to turn the page - which is why the file looks portrait when you open it. `merge_transformed_page` copies the CONTENT and **drops that flag**, so the scan landed sideways in the invoice (DN-HDEC-047: 842x596 with `/Rotate 270`). `_place_on_a4` now calls `page.transfer_rotation_to_content()` when `/Rotate` is non-zero, so the geometry it then measures is the geometry the reader actually shows - and a rotated A4 scan becomes a genuine A4 page that takes the pass-through branch at full quality instead of being shrunk onto a fresh sheet. **A portrait-mediabox check is not enough to test this**: an unrotated landscape page also *measures* portrait once it has been letterboxed onto an A4 sheet, so the regression test asserts the page is passed through untouched. Called from `render_inv_pdf`, so **preview, save and re-sign all get it** - what you preview is what you send. A merge that fails despite resolve-time validation drops the placeholder (never ships marker text) and sets `signed_copy_merge_failed`, which `_dn_copy_notes` reports loudly. Uses **pypdf** - the one Python PDF dependency, added for this; it copies page objects rather than re-rendering, so the scan keeps its quality.
- `_cell(row, col, qty, rate, currency="USD")` - like quote; `computed` returns `_money(0, currency)` for empty (DN/quote return `""`).
- **Sell currency (2026-07-29)** - `render_inv_html` reads `inv["sell_currency"]` (`"USD"` / `"IQD"`, anything else coerced to USD) and threads it through every money cell plus `grand_amount` / `grand_words`; it is also exported to the template as `sell_currency`. **Rendering is pure formatting** - the rate/total values already arrive in that currency (the editor converts the cells when the switch is flipped), and the USD equivalent stored for reporting is computed in `api.save_inv`, never here.
- `_bank_lookup(key) -> dict|None` - `config.INV_BANK_INFO` entry by key; `"cash"` returns None (template omits Payment card); else first entry.
- `_apply_col_widths(schema, doc) -> schema` - overlay + rebalance to 95.4 (quote-style, not DN-style).
- `render_inv_html(inv, inline_css=False) -> str` - schema → cols → document-aware autofit → `build_layout` → grand `qty*rate` sum → tax/total row col-spans (`tax_label_span=n-1`; total: label 2 / amount 1 / words middle) → bank lookup → render `inv_page.html` with `signing.build_template_context("inv", …)`. **Eshraqat invoice (Phase Y.6, per user) - adopts the quotation closing design:** the item table is item-only (the tax + total rows are NOT in the table); the closing block is the shared `.esh-amount-block` - Amount-in-words + **Payment Info/Method** on the left, **Subtotal / Withholding Tax (3.3%) / Grand Total** on the right (Subtotal==Grand, withholding is a status); the "Thank you…" line is removed; and the signature is pushed to the bottom-right via a flex page (`margin-top:auto`, shifted up with `margin-bottom:8mm`). Because the tax/total leave the table, `build_layout` sets `last_fixed_in_table=0` and `_footer_block_mm()=INV_ESH_CLOSEOUT_MM(58)` **only under the Eshraqat profile** (Abraj keeps `INV_TAX_ROW_MM+INV_TOTAL_ROW_MM` in-table and `INV_PAYMENT_MM`). Verified single + full-last-page: no overlap. **Per-doc-type title colours** (`eshraqat_print.css`): Quotation `#1c1d20` (black, default), DN `#8f1730` (red, `.esh-dn-document`), Invoice `#1f4e8f` (blue, `.esh-invoice-document`).
- `render_inv_pdf(inv, out_path) -> Path`.
- **Appended DN pages (2026-07-18):** `render_inv_html` ends with `_append_dn_pages(html, inv.get("_dn_docs") or [])`. `_append_dn_pages` renders each DN via `dn_generator.render_dn_html(dn, inline_css=True)`, splices its `<body>` pages (each wrapped in `break-before:page`) before the invoice's `</body>` and injects each distinct DN `<style>` once (skipping any the invoice already carries) - so ONE headless pass yields invoice + DN pages. Relies on invoice & DN sharing the same A4 `@page` and distinct page classes (eshraqat uses one shared stylesheet). The caller (`Api.preview_inv`/`save_inv`) resolves `inv["_dn_docs"]` from `dn_ref` lines via `api._resolve_dn_docs`/`_dn_render_doc` (maps a saved DN's `rows→items`, `dn_date→date`, + columns/layout/signing). Deleted DNs are skipped. Guard: `_append_dn_pages` regex-splices rendered HTML - if a template's `<body>`/`<style>` wrapper changes, re-check it.

**Reads / writes** - Reads `config.INV_TYPES/INV_SUBTYPES_OVERRIDES/INV_LAYOUT/INV_COMPANY/INV_BRAND_COLOR/INV_BANK_INFO`, `inv_styles.css` (or `eshraqat_print.css`), `config.ASSETS_DIR/logo.*`, `inv_page.html` (or `eshraqat/inv_page.html` + `eshraqat_render.common_context`, ~line 471). Writes PDF. Reads signing.

**Calls / called by** - Imports `amount_in_words, _money, _num` from `quote_generator`. Calls `pdf_engine.write_pdf`, `signing.build_template_context`. Called by `api.save_inv` / preview (→ JS `api().save_inv`, `api().render_inv_html`).

**Gotchas**
- `INV_TOP_MM=88` and `INV_MAX_ROW_MM=17` are hand-tuned to the rendered header height; the file's Phase M/Phase X comments document real overlap bugs these numbers fixed. Do not round them off.
- **Last page never stretches rows** (unlike DN/quote which stretch the last page). Cross-porting the stretch logic reintroduces the footer-overlap bug.
- Withholding tax percentage/text lives in the **template**, not here; this module only reserves `INV_TAX_ROW_MM` space and computes the grand total (tax "Included", not added).
- `_bank_lookup` special-cases `"cash"` → None; the template must handle a null bank.
- `_rebalance_widths` differs from quote's by scaling up under-target totals - a deliberate INV-only fix.
- **Phase X.24/X.27 - INV does NOT share the quote's render overhead, but DID get the page-role fix.** INV rows hug their content and the last page never stretches, so a filled page cannot overshoot - the quote's `Q_ROW_OVERHEAD_MM` fix must **not** be ported here. Separately, X.27 gave INV the same page-ROLE packing (headerless middle pages stop reserving the 88mm header): a 40-item INV went 4 pages → 3 (middle page now 17 rows), last page's tax/total + payment/signature footer verified clear of the table. The tax/total reserve and the never-stretch-last-page rule are preserved.

---

## backend/eshraqat_render.py  (shared eshraqat template context)

**Purpose** - One tiny module giving all four generators the same context dict for the `templates/eshraqat/*` skin, and (since 2026-10-02) the ONE header layout for the English documents. Profile subsystem: [[13-PROFILES-BRANDS]].

**Key symbols**
- `meta_rows(kind, doc) -> list[dict]` (2026-10-02) - the header rows for `kind` in `quote`/`dn`/`inv`, in grid order (left, right, left, right...): No. | Date / LPO-or-RFQ | Linked Quotation-or-Attention / Client | Address. Never `wide`. Pure function - no profile check, so tests can call it directly. `QUOTE_ATTENTION = "Admin Manager"` lives here now (was a literal in quote_generator). The Arabic PO builds its own Arabic grid and does not use it.
- `common_context(*, title, doc_no, pages) -> dict` - raises `RuntimeError` unless `config.ACTIVE_PROFILE == "eshraqat"`. Supplies `eshraqat=True`, `identity=config.ESHRAQAT_IDENTITY` (copy), `brand=config.PROFILE_ACCENT`, `logo_data_uri=brand_assets.logo_data_uri(required=True)`, `iso_data_uri=brand_assets.iso_data_uri()`, `document_title`, `document_no`, `page_count`.

**Gotchas**
- `required=True` means a missing `data/profiles/eshraqat/assets/logo.png` raises `FileNotFoundError` and **breaks every eshraqat render** - deliberate (no silent wrong-brand fallback).
- Guard test: `tests/eshraqat_shared_template.test.py`.

---

## backend/brand_assets.py  (profile-owned image data-URIs)

**Purpose** - Load profile-owned images from `config.ASSETS_DIR` (= `profile_manager.assets_dir()`, i.e. `data/profiles/<id>/assets/`) as data-URIs. **Never falls back across profiles.**

**Key symbols**
- `image_data_uri(path, required=False) -> str|None` - `None` when missing, or `FileNotFoundError` when `required`.
- `logo_data_uri(required=False) -> str|None` - tries `logo.png/jpg/…` under `config.ASSETS_DIR`.
- `iso_data_uri() -> str|None` - reads `iso-certifications.png` (the eshraqat header's ISO strip).

**Gotchas**
- Asset lookups follow `config.ASSETS_DIR`, which is bound at config import - a profile switch needs an app restart. Guard test: `tests/eshraqat_assets_backend.test.py`.

---

## backend/pdf_engine.py  (shared headless-browser PDF engine)

**Purpose** - Convert a self-contained HTML string to PDF using headless Chrome/Edge (`--print-to-pdf`). It does NOT lock or stamp: the generators do that after it (`doc_verify.stamp_pdf`, then `pdf_lock.lock` LAST, 2026-10-07 - see [[03-BACKEND-DATA]]), because the invoice still has a signed-copy merge to do after rendering. Chosen over WeasyPrint because Chromium ships on every Windows box and renders Arabic/RTL correctly. Used by all five generators.

**Key symbols**
- `PdfEngineNotFound(RuntimeError)` - raised when no browser is found.
- `_registry_app_path(name) -> str|None` - reads `App Paths\chrome.exe`/`msedge.exe` from HKLM/HKCU (and WOW6432Node).
- `find_browser() -> Path` - search order: `chrome.exe`→`msedge.exe` on PATH → registry App Paths → hardcoded common install dirs. Raises `PdfEngineNotFound` if none.
- `write_pdf(html: str, out_path: Path) -> Path` - writes HTML to a space-free temp workdir, runs the browser headless (`--headless=new --disable-gpu --no-pdf-header-footer --no-sandbox --user-data-dir=… --print-to-pdf=…`), then `shutil.move`s the result to `out_path`. 60s timeout; per-call unique `user-data-dir`; `CREATE_NO_WINDOW` on Windows; retries without `--no-pdf-header-footer` (adds legacy `--print-to-pdf-no-header`) if the first attempt produces no file; cleans up the temp dir.

**Reads / writes** - Reads Windows registry (App Paths) + browser exe. Writes to a temp workdir under `tempfile.gettempdir()`, then moves the PDF to `out_path` (creating parent dirs). No DB, no config.

**Calls / called by** - Called by `render_pdf`/`render_quote_pdf`/`render_dn_pdf`/`render_inv_pdf`. Surfaced to the user when `PdfEngineNotFound` bubbles up through `api.save_*`.

**Gotchas**
- **Requires a real space-free temp path** - headless Chrome historically can't read `--print-to-pdf=` paths with spaces; the temp-dir-then-move dance is load-bearing. Don't "simplify" it to write directly to `out_path` (which lives under `D:\PO TOOL\…` with a space).
- Per-call **isolated `user-data-dir`** avoids the "browser already running with your profile" refusal; removing it breaks concurrent/second renders.
- The no-header retry exists for older Chrome builds; both flag forms must stay.
- Silent-ish failure mode: if `tmp_pdf` never appears, it raises `RuntimeError` with truncated stdout/stderr - the only diagnostics you get.
- 60s hard timeout → `RuntimeError("PDF render timed out (browser hung)")`; a huge doc that renders slowly will fail here.

---

## backend/signing.py  (shared signatures + stamp overlay system)

**Purpose** - One system for all doc types: manage uploaded signature (×3) + stamp (×1) assets, roll randomized placement at "Apply" time, persist per-doc overlays in `doc_meta`, and emit ready-to-inject overlay HTML for templates.

**Key symbols - settings/assets:**
- `DEFAULTS` - sizes (`stamp_w/h_mm`, `sig_w/h_mm`) + `randomness` (dx/dy/rot half-ranges + `stamp_vs_sig_y_mm`).
- `_sig_dir() -> Path` = `config.ASSETS_DIR/"sigs"` (~line 81) - **per-profile** (there is no `SIG_DIR` constant anymore; each profile has its own signatures/stamp). `SIG_FILES = {1:sig_1.png,2:sig_2.png,3:sig_3.png}`, `STAMP_FILE = "stamp.png"`.
- `_user_signing()`, `get_settings() -> dict` (defaults + user overrides + filesystem `has_image` flags), `save_settings(payload) -> dict` (persists labels/size/randomness to `data/settings.json`).
- `_write_data_uri(data_uri, dest)`, `upload_signature(slot, data_uri)`, `upload_stamp(data_uri)`, `delete_signature(slot)`, `delete_stamp()` - PNG/JPEG data-URI in, file out under `_sig_dir()`.
- `_image_to_data_uri(p)`, `get_assets() -> dict` - snapshot of all images as data-URIs + active sizes (used by generators to inline images).

**Key symbols - overlay model:**
- `DOC_SLOTS = {po:[main], qut:[main], dn:[supplier], inv:[main]}`; `slots_for(doc_type) -> list[str]`.
- `_rand_pm(half_range) -> float` - uniform `[-h,+h]` rounded to 0.01.
- `roll_overlay(doc_type, selections) -> {"slots":{slot:{...}}, "size":{...}}` - takes the Sign-modal picks `{slot:{sig_index, stamp}}`, validates `sig_index∈{1,2,3}`, and **bakes random dx/dy/rot** for stamp+signature into a stable payload. Empty slots → `None`.
- **`overlay_for_save(doc_type, existing) -> overlay|None` (auto-sign, 2026-07-24)** - the overlay every `api.save_*` bakes into the PDF, called BEFORE the render (the signature must be in the rendered file, not merely stored after it). Rules in order: (1) if `existing` carries picks, **re-roll their placement** but keep `sig_index`/`stamp` - so a hand-picked signature is never silently replaced; (2) else if `settings.auto.enabled`, apply the default (`sig_index` 1 + `stamp`) to every slot of `slots_for(doc_type)`; (3) else return `existing` untouched. **Refuses to sign with a missing asset** (checks `has_image`), otherwise a doc with no uploaded signature would report as signed while printing nothing. `_selections_from_overlay` extracts the picks.
- `DEFAULTS["auto"] = {enabled: True, sig_index: 1, stamp: True}` - merged/persisted by `get_settings`/`save_settings` as booleans + an int slot (deliberately NOT run through the `float()` loop used for `size`/`randomness`).

**Key symbols - persistence (doc_meta):**
- `_connect()`, `_ensure_table()` - lazy idempotent create of `doc_meta(doc_type, doc_no, key, value, PK(doc_type,doc_no,key))`. `_connect()` mkdirs the active profile's data dir before opening `config.DB_PATH` (mirrors `database._connect`), so signing on a freshly-activated profile whose dir hasn't been touched yet doesn't fail with "unable to open database file".
- `save_doc_overlay(doc_type, doc_no, overlay)` - upsert under key `'signing'`; a falsy overlay **deletes** the row.
- `overlay_is_signed(overlay) -> bool`, `is_doc_signed(doc_type, doc_no) -> bool`, `signed_doc_nos(doc_type) -> set[str]` (one query - used to badge Open lists/Clients), `load_doc_overlay(doc_type, doc_no) -> dict|None`.

**Key symbols - render:**
- `build_template_context(doc_type, overlay, doc_no=None) -> {"sig_overlay_<slot>": html, …}` - if `overlay is None` and `doc_no` given, loads persisted overlay; spreads into the Jinja render.
- `render_overlay_html(overlay, slot, assets=None) -> str` - absolute-positioned `<div class="sig-overlay">` with stamp and signature images, applying the pre-rolled offsets. **Since 2026-10-07 both run INTO the printed title/name** (Waleed: overlap the name "so it cant be screenshoted"): the rolled vertical offset picks where inside the name block the ink ENDS - `STAMP_BAND = (0.55, 0.90)` and `SIGN_BAND = (0.10, 0.50)` of `var(--sig-text-h)` (`_band`), minus the empty space `object-fit: contain` leaves under a wide image (`_ink_gap_mm`, from the PNG header). `stamp_vs_sig_y_mm` is no longer used. Both images carry `mix-blend-mode: multiply`, so the name shows through the ink as under a real stamp.

**Reads / writes** - Files under `config.ASSETS_DIR/sigs/` (per-profile, `data/profiles/<id>/assets/sigs/`). `data/settings.json` (`"signing"` section via config). SQLite `doc_meta` table in the **per-profile** `config.DB_PATH` (= `profile_manager.profile_db_path()`). Reads `config.ASSETS_DIR`, `config.load_user_settings/save_user_settings`.

**Calls / called by** - `build_template_context` called by **every** generator's `render_*_html`. `roll_overlay`, `save_doc_overlay`, upload/delete/get_settings exposed to JS via `api.signing_*` (e.g. `api().signing_roll_overlay`, `api().signing_upload_signature`, `api().signing_save_doc_overlay`). See [[02-BACKEND-CORE]].

**Gotchas**
- **Randomness is re-rolled on EVERY save** (changed 2026-07-24 - was "rolled once at Apply, then persisted"). `api.save_*` calls `overlay_for_save` before rendering, so re-saving the same document produces a slightly different signature/stamp position. A pure re-render that does NOT go through a save path still reuses the persisted offsets. `roll_overlay` remains the only place `random` is called.
- **Auto-sign fights explicit un-signing.** Clearing a signature in the Sign modal and saving re-signs the document, because `overlay_for_save` sees no picks and applies the default. The escape hatch is the global `auto.enabled` setting - this is intended ("every document signed by default") but is the first thing to check if a user reports "my signature keeps coming back".
- The overlay assumes the signature box is `position: relative` and that its bottom is where the printed name ends (measured on all seven signed templates, within 1mm). The name block's height is `--sig-text-h` on `.sig-overlay` in each stylesheet: 10.2mm quote, 4.2mm DN, 9.5mm invoice, 10mm PO (Abraj, two lines), 4.2mm on the Eshraqat skin (one line). Change a box's text and re-measure; `tests/pdf_protection.test.py` renders every template at both ends of the roll. Eshraqat delivery notes have no signature slot at all.
- `doc_meta` is created **lazily here** (`_ensure_table`, `_INIT_DONE` per-process) and also declared in the schema/migrations; both must agree on the `(doc_type,doc_no,key)` PK. Empty/cleared overlays are stored as a *deleted* row, so absence == unsigned.
- `DOC_SLOTS` is the single source of which slots each type exposes; DN deliberately signs only `supplier`. Adding a slot here without updating the template yields an unused `sig_overlay_*` key.
- Overlay `size` is snapshotted into the payload at roll-time, so later changing `DEFAULTS`/settings sizes does not resize already-applied overlays.
- **Signature/stamp images and `doc_meta` are per-profile** - switching profiles switches which signatures exist and which docs are "signed". Do not hardcode `static/sigs` anywhere; see [[13-PROFILES-BRANDS]].

---

## backend/spreadsheet_parser.py  (bulk-paste file parser)

**Purpose** - Parse a user-chosen `.xlsx/.xlsm/.csv/.tsv/.txt` file into a rectangular 2D string matrix for the bulk-paste wizard. Never raises - wraps errors for the JS bridge.

**Key symbols**
- `_trim_trailing_empty(rows)`, `_looks_like_xlsx(path)` (PK zip signature sniff), `_cell_to_str(v)` (None→"", whole floats→int, bool→TRUE/FALSE).
- `_parse_xlsx(path) -> list[list[str]]` - openpyxl `read_only, data_only=True` (cached formula values); caps `_MAX_ROWS=5000`, `_MAX_COLS=64`; pads to rectangular; trims trailing empties.
- `_parse_delimited(path) -> list[list[str]]` - decodes with `utf-8-sig`→`utf-8`→`cp1252`→`latin-1`; `csv.Sniffer` for dialect (fallback tab/comma); same caps + padding + trim.
- `parse_file(path) -> dict` - returns `{"ok":True,"rows":[...],"kind":"xlsx"|"delimited"}` or `{"ok":False,"error":...}`. Chooses xlsx path by suffix **or** `_looks_like_xlsx` sniff.

**Reads / writes** - Reads the given file (needs `openpyxl` for xlsx). No DB, no config, no writes.

**Calls / called by** - Called by `api.parse_spreadsheet` / bulk-paste (→ JS `api().parse_spreadsheet`), including a temp-file path variant. Feeds the editors' bulk-paste wizard.

**Gotchas**
- `data_only=True` means **formula cells return the last cached value** - a file saved by a non-Excel tool (no cached values) yields blank cells.
- xlsx detection is by suffix OR PK-signature sniff, so a mislabeled `.csv` that's actually a zip is parsed as xlsx.
- Row/col caps (`5000`/`64`) silently truncate oversized sheets.
- Missing `openpyxl` raises inside `_parse_xlsx` but is caught by `parse_file` and returned as `{"ok":False,"error":...}` - it never crashes the bridge.
