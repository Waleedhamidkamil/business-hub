# 13 - Company Profiles & Brands

The **multi-company profile subsystem** (added 2026-07-14): the app now hosts **two isolated company workspaces** - `abraj` ("Abraj Albadr") and `eshraqat` ("Eshraqat Albadr") - switchable at runtime from the dashboard. Each profile has its own DB, settings, brand assets, documents root, numbering scheme, and print skin; only the autocomplete catalogs are shared.

**Covers:** `backend/profile_manager.py`, `backend/profile_numbering.py`, `backend/brand_assets.py`, `backend/eshraqat_render.py`, `backend/shared_library.py`, the profile branches in `config.py` / `api.py` / `app.py`, the dashboard switcher in `app.js` / `index.html`, `templates/eshraqat/*`, `static/eshraqat_print.css`, `static/brands/`, and the `data/` profile layout.

See also: [[01-ARCHITECTURE]] · [[02-BACKEND-CORE]] · [[03-BACKEND-DATA]] · [[04-BACKEND-DOCGEN]] · [[09-DATA-MODEL]] · [[12-PDF-LAYOUT-CONSTITUTION]] · intent plans in `docs/superpowers/plans/2026-07-14-multi-company-profile-foundation.md` and `2026-07-14-eshraqat-document-family.md`.

---

## What a profile is

```
data/
├─ profile-registry.json          {version:1, active_profile, profiles:[{id,label}]}
├─ profile-migration-v1.json      one-shot migration marker + baseline counts  ← NEVER delete
├─ profile-migration.lock         file lock used during bootstrap
├─ profiles/
│  ├─ abraj/     po_tool.db · settings.json · assets/ (logo.*, iso-certifications.png, sigs/sig_1..3.png + stamp.png)
│  └─ eshraqat/  po_tool.db · settings.json · assets/ (same shape)
├─ shared/library.db              ONLY the 7 shared autocomplete catalogs
├─ po_tool.db + settings.json     frozen LEGACY copies - the fallback source, not the live DB
```

- **Per-profile DB** holds ALL document/business state: `pos`, `quotes`+items, `dns`+items, `invoices`+items, costing, submitted/paid, `deleted_docs`, `doc_meta`, `doc_columns`, `document_counters`, and its own `_migrations_applied` ledger.
- **`data/shared/library.db`** holds ONLY the 7 `SHARED_TABLES` suggestion catalogs: `items`, `companies`, `clients`, `quote_items`, `dn_items`, `inv_items`, `column_memory`.
- **Documents root** is per profile: Abraj `D:\PO TOOL`, Eshraqat `D:\ESHRAQAT DOCUMENTS` (defaults; profile `settings.json` may pin).
- Exactly two hard-coded IDs - `ProfileManager.VALID_IDS`. Adding a third company means touching every place this doc lists as "hardcoded per profile".

## Boot & one-time migration

`app.py main()` calls `profile_manager.manager().bootstrap_profiles(legacy_db, legacy_settings, legacy_assets)` **before importing any backend module** - `config.py` binds `ACTIVE_PROFILE`/`DB_PATH`/`SETTINGS_PATH`/`ASSETS_DIR` at import time, so this order is load-bearing (backend imports live *inside* `main()`).

- Bootstrap failure ⇒ sets env `DOCUMENTS_HUB_LEGACY_FALLBACK=1` + `DOCUMENTS_HUB_PROFILE_WARNING` and the app continues on the legacy Abraj paths; `get_active_profile().warning` surfaces it as a toast and `switch_profile` refuses.
- The migration is **one-shot and journaled**: in-process lock + `profile-migration.lock` (msvcrt/fcntl); stages under `data/profiles/.staging-<uuid>`; copies the legacy DB via the sqlite backup API under `BEGIN IMMEDIATE` with per-table count verification; seeds abraj settings/logo/sigs; creates an empty eshraqat DB; builds `shared/library.db` via `shared_library.copy_catalogs`; atomic dir replace; writes the `profile-migration-v1.json` marker (baseline counts), then the registry. A pending journal without a marker triggers `_recover_interrupted_migration()` which **deletes the profile dirs** - never hand-seed `data/profiles/` and never delete the marker.
- Every later launch re-validates the marker (PRAGMA `quick_check` on all 3 DBs + logical-data-loss baseline floors incl. `deleted_docs` tombstones). `ensure_seed_assets()` copies `static/brands/eshraqat/*` into the profile assets dir only when absent (never overwrites user files).
- **Retired-catalog caveat (2026-08-20):** the "did migration lose an Abraj table?" check compares the profile DB against the legacy file's table list, so it MUST exclude tables that a profile DB is not meant to keep - `shared_library.RETIRED_CATALOGS` (`items`/`quote_items`/`dn_items`/`inv_items`, deliberately dropped) and `master_items` (a shared-DB record store that only appears in the legacy file because dev scripts created it there). Without that exclusion, dropping the catalogs makes bootstrap fail with "found an incomplete Abraj database" and the app silently falls back to legacy Abraj data. The shared-catalog baseline-loss check iterates `SHARED_TABLES` only, so the marker's stale `catalog_baseline_counts` for the retired tables are ignored. Regression: `tests/profile_migration_backend.test.py::assert_retired_catalog_drop_is_not_data_loss`.

## Runtime switch flow (end-to-end)

JS `requestProfileSwitch(id)` (`app.js` ~903–1066) → `activeDirtyEditor()` checks all five editors' dirty flags, modal Save/Discard/Cancel (Save reuses each editor's save fn with `{stay:true, skipConfirm:true}`) → `api().switch_profile(id)` → `validate_profile` (DB exists + `quick_check`, settings parse, assets dir, documents-root **parent** drive exists - hardcoded roots) → `activate_profile` (atomic registry rewrite) → `Api._reload_profile_runtime()` → returns `{ok, active_profile, reload:true}` → JS `window.location.reload()`. **Any exception rolls back**: previous profile re-activated + runtime reloaded again.

- **`Api._reload_profile_runtime()`** = `importlib.reload(config)` **first**, then 16 modules (database, profile_numbering, migrations, all 5 generators, all 5 numbering modules, signing, clients, analytics, data_manager), then `database.init_db()` + `migrations.run_all()` on the new profile DB (a failed migration raises ⇒ rollback). Also called by `dm_restore_backup` after a successful restore.
- **`Api.__getattribute__`** wraps every *public* callable in `_PROFILE_SWITCH_LOCK` (a `threading.RLock`) so a switch can never interleave with an in-flight bridge call. New public `Api` methods get the guard automatically; underscore-prefixed helpers bypass it.
- `get_active_profile()` returns `{id, profiles:[{id,label,logo_data_uri,brand_color}], logo_data_uri, brand_color, documents_root, iqd_per_usd, warning}`. JS `applyCompanyProfile` sets `--profile-accent`, `data-profile` on `<html>` (drives Eshraqat dashboard skin CSS), `window.IQD_PER_USD`, hero/label/logos. Switcher DOM: `#dashProfileSwitcher/#dashProfileButton/#dashProfileMenu` (`index.html` ~428–448, hardcoded `data-profile-id` options).

## Per-profile config (`config.py`)

`ACTIVE_PROFILE`, `PROFILE_DIR=DATA_DIR`, `DB_PATH`, `SETTINGS_PATH`, `ASSETS_DIR`, `PROFILE_ACCENT` (`#1F3864` abraj / `#861432` eshraqat), `IQD_PER_USD` (1400 / 1500), `CASH_IQD_PER_USD=1500`, `ESHRAQAT_IDENTITY` - all recomputed on `importlib.reload(config)`.

`_apply_company_profile_defaults()` (~line 497, called **before** the user-settings overlay): under eshraqat it rewrites `DOCUMENTS_ROOT=D:\ESHRAQAT DOCUMENTS`, doc prefixes to `PO/QT/DN/IV` (abraj keeps `A/QUT/DN/INV`), all brand colors to `PROFILE_ACCENT`, the company identity lines, `INV_BANK_INFO=[CASH_BANK]`, and empties address options / default signatures. **User `settings.json` still wins** - keep that call order.

## Numbering (`backend/profile_numbering.py`)

`enabled()` ⇔ `ACTIVE_PROFILE == 'eshraqat'`. Permanent counters in the **profile DB** table `document_counters(kind PK, last_value)`; format `{PO,QT,DN,IV,LT}-NNNN` (zero-padded 4). All five `get_next_*` / save paths in `api.py` branch on it; abraj keeps the legacy per-month filesystem-scan numbering ([[04-BACKEND-DOCGEN]]).

- **`peek()`/`peek_many()` do NOT reserve** - `api.py` calls `commit(kind, numbers)` only **after a successful save** (`BEGIN IMMEDIATE`, monotonic MAX upsert, idempotent; multi-number batches must be consecutive - PO split relies on this).
- Floor = max(counter row, numbers parsed from the live table, `deleted_docs` tombstones) ⇒ **numbers are never reused or regressed**, even after restoring an older DB. Don't "fix" apparent gaps.
- The floor parser only understands the eshraqat format - abraj-format numbers parse to 0, so never point `profile_numbering` at an abraj DB.

## Brand rendering

Generators keep **one** schema/measurement/pagination engine; per profile they only swap template + context + CSS ([[12-PDF-LAYOUT-CONSTITUTION]] §6.1 - never fork `build_layout` per brand, add profile-guarded chrome reserves like `*_ESH_PAGE_CHROME_MM=34` with Abraj staying byte-identical at 0):

- `ACTIVE_PROFILE=='eshraqat'` ⇒ `templates/eshraqat/<kind>_page.html` (+ shared macro library `templates/eshraqat/_components.html`) + `static/eshraqat_print.css`, context merged from `eshraqat_render.common_context(...)` - which **raises** under any other profile, so every call site stays inside a profile branch.
- **The Eshraqat PO is Arabic + RTL (2026-08-09); the other three are English + LTR.** It uses the `header_ar` / `page_number_ar` macros, `ESHRAQAT_IDENTITY["name_lines_ar"]` + `["po_title_ar"]`, prints **no footer band**, and carries the picture column in the same slot as the Abraj PO. Its overrides are the `.esh-po-document` block at the end of `eshraqat_print.css`, and its extra flow space is why `pdf_generator.PO_ESH_USABLE_MM` is 260 rather than 250 - details in [[08-STYLES-TEMPLATES]] and [[04-BACKEND-DOCGEN]].
- **Never put the string "Abraj" in `eshraqat_print.css` or the eshraqat templates**, not even in a comment: the stylesheet is inlined into every eshraqat PDF and `tests/eshraqat_quote_rendering.test.py` asserts `"ABRAJ" not in html.upper()`.
- `brand_assets.py` encodes images from `config.ASSETS_DIR` only - **no cross-company fallback, ever**. Eshraqat logo is `required=True`: a missing `data/profiles/eshraqat/assets/logo.png` breaks every eshraqat render, by design (fail > wrong brand).
- Signing images resolve at call time as `config.ASSETS_DIR/'sigs'` - per-profile signatures/stamp (`static/sigs/` is legacy-fallback only). `Api.upload_logo` writes `config.ASSETS_DIR/logo.png`.
- INV under eshraqat also swaps last-page reserve composition (`INV_ESH_CLOSEOUT_MM=58` replaces payment block; totals render outside the table) - see [[12-PDF-LAYOUT-CONSTITUTION]]. Eshraqat quotes strip IMAGE columns (`_strip_image_columns`).

## Shared library (`backend/shared_library.py`)

`data/shared/library.db` = the 7 suggestion catalogs only. `database._connect_shared()` / `clients` / `data_manager` route every `suggest_*` / `remember_*` / client-catalog operation there. All `remember_*` calls inside document-save paths are wrapped in `best_effort_shared` - **a shared-DB failure must never fail a document save** (autocomplete just stops learning). `_data_sig` now exists in **four** byte-identical copies (`database.py`, `data_manager.py`, `shared_library.py`, `migrations._sig_from_data`) - change all or none. `rename_client` ATTACHes every profile DB and renames across **all** profiles.

## Backup / restore (`backend/data_manager.py` ~462–671)

Version-2 archives `documents-hub-<profile>-backup-<ts>.zip`: manifest (with `profile_id`) + `profile/po_tool.db` + `profile/settings.json` + `profile/assets/**` + `shared/library.db` + document roots. `restore_backup` **refuses** version-1 archives and archives whose `profile_id` ≠ active profile ("switch to that profile before restoring"), `quick_check`s staged DBs, writes `.before-restore.bak` safety copies, and hot-reloads the runtime afterwards. Keep the manifest check - it is what prevents restoring Abraj data into Eshraqat.

## Legacy fallback

`DOCUMENTS_HUB_LEGACY_FALLBACK=1` flips every `profile_manager` path helper to pre-profile locations (`data/po_tool.db`, `data/settings.json`, `static/`, `static/sigs`) and disables `switch_profile`. Consequence: **path-touching code must always go through `profile_manager` / `config` helpers** - never hardcode `data/po_tool.db` or `static/sigs`.

## Cross-cutting gotchas

- **Import-order invariant:** profile bootstrap before any backend import; config values are bound at import. Any NEW module that caches config values at import must be added to `Api._reload_profile_runtime`'s reload list (and, if it's a generator, to the `reset_settings`/`save_doc_settings` reload loops) or it silently serves the previous company after a switch.
- **Data isolation:** only the 7 `SHARED_TABLES` may live in the shared DB; never silently fall back from a failed shared connection to the profile DB.
- **Test isolation quirk:** `smoke_test.py`, `smoke_quote.py` and the Abraj-oriented tests assume the on-disk registry has **abraj active**; the `eshraqat_*` and `profile_*` tests pin their own temp `ProfileManager`. Failures under an eshraqat-active registry are the quirk, not bugs ([[11-RUN-AND-MAINTAIN]]).
- **Duplicated brand colors:** `get_active_profile()` hardcodes `#1F3864`/`#861432` in `api.py` separately from `config.PROFILE_ACCENT`; `doc_settings.js` fallbacks are Abraj-flavored. Change a brand color ⇒ sync all.
- **USD peg:** `IQD_PER_USD` is per-profile; cash deals settle at `CASH_IQD_PER_USD=1500`; Eshraqat always settles at market rate. Costing must go through `database.doc_iqd_per_usd()` / the frontend per-doc accessor - never a literal.
- Switching reloads the whole SPA (`window.location.reload`) - all in-memory JS state is lost by design; the dirty-editor guard must run **before** `api().switch_profile`.

## Files

| File | Role | ~lines |
|---|---|---|
| `backend/profile_manager.py` | Registry + path owner, legacy fallback, seed assets, validation, journaled one-time migration | 712 |
| `backend/profile_numbering.py` | Eshraqat permanent counters: format/parse, peek/peek_many/commit, tombstone-aware floor | 175 |
| `backend/brand_assets.py` | Profile-owned image → data-URI (logo required, ISO optional); no cross-brand fallback | 36 |
| `backend/eshraqat_render.py` | Shared Jinja context for the five Eshraqat templates; raises off-profile | 19 |
| `backend/shared_library.py` | Shared catalog schema + `copy_catalogs` migration with `_data_sig` normalization | 183 |
| `templates/eshraqat/` | `_components.html` macros + 5 `*_page.html` brand-skin templates | - |
| `static/eshraqat_print.css` | The complete second print skin (all five doc types) - see [[08-STYLES-TEMPLATES]] | ~215 |
| `static/brands/eshraqat/` | Packaged seed assets copied once by `ensure_seed_assets()` | - |

Test coverage: `tests/profile_{manager,migration,config_assets,data_isolation,switch,legacy_fallback,backup_restore}_backend.test.py`, `tests/eshraqat_{assets,numbering,shared_template}_backend/…`, the five `eshraqat_*_rendering` + `eshraqat_pagination_solidity` suites, `tests/company_profile_switcher.test.js`, `tests/shared_library_save_resilience_backend.test.py`, `tests/client_rename_{profiles,isolation}_backend.test.py`.

## A damaged company database is refused, journal or not (2026-10-23)

`ProfileManager._quick_check` (used by `validate_profile`, so by every company switch) first reads the
file's 16-byte header and refuses anything that does not start `SQLite format 3\0` (an empty file is
still SQLite's new, empty database). `PRAGMA quick_check` alone could pass a damaged file: while any
connection still has the database open - in this process (`with conn:` commits but never closes), or in
another person's engine on the office server - SQLite reads page 1 from the `-wal` journal. Found when
`tests/profile_switch_backend.test.py` began failing after unrelated changes: it had been passing or
failing on when Python's garbage collector happened to run; it now holds the collector off for its whole
run, so it fails every time without the header check. The underlying open connections are a separate
follow-up (task "Close SQLite connections the app leaves open").
