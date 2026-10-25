# Multi-Company Profile Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safely switchable Abraj and Eshraqat workspaces with isolated business data, a shared client/autocomplete library, permanent Eshraqat numbering, and no regression to existing Abraj behavior.

**Architecture:** A new `profile_manager` owns active-profile paths and one-time migration. Profile databases contain documents and all derived business state; a separate shared SQLite library contains only client/catalog/autocomplete tables. Existing modules resolve paths through `config`, while profile switching reloads backend configuration and clears the frontend workspace.

**Tech Stack:** Python 3, SQLite, pywebview bridge, vanilla JavaScript, HTML/CSS, standalone Python/Node regression scripts.

---

## File Structure

- Create `backend/profile_manager.py`: registry, profile paths, presets, migration, activation rollback.
- Create `backend/shared_library.py`: shared schema, migration copy, shared connections.
- Create `backend/profile_numbering.py`: Eshraqat permanent counters and formatting.
- Modify `app.py`: initialize profiles before importing profile-dependent backend modules; neutral window title.
- Modify `backend/config.py`: active-profile DB/settings/assets/documents roots and Eshraqat defaults.
- Modify `backend/database.py`: profile connection plus shared autocomplete routing and counters schema.
- Modify `backend/clients.py`: shared client catalog plus active-profile history/totals.
- Modify `backend/data_manager.py`: shared catalog connections; active-profile document connections.
- Modify `backend/analytics.py`, `backend/signing.py`, `backend/migrations.py`: active-profile paths.
- Modify `backend/api.py`: profile read/switch bridge, profile-aware logo upload, permanent numbering integration.
- Modify five numbering modules: choose Abraj legacy or Eshraqat permanent strategy.
- Modify `templates/index.html`, `static/app.js`, `static/app_styles.css`, `static/dark.css`: switcher and guarded reload.
- Create focused backend/frontend regression tests listed below.

The current workspace has no `.git` directory. Run the commit steps only if Git metadata is restored; otherwise preserve each passing task as a logical checkpoint without manufacturing a repository.

### Task 1: Profile Registry and Paths

**Files:**
- Create: `backend/profile_manager.py`
- Test: `tests/profile_manager_backend.test.py`

- [ ] **Step 1: Write the failing registry/path test**

Create a standalone test using a temporary directory and a constructed `ProfileManager`:

```python
from tempfile import TemporaryDirectory
from pathlib import Path
from backend.profile_manager import ProfileManager

with TemporaryDirectory() as td:
    root = Path(td)
    manager = ProfileManager(root / "data", root / "static")
    manager.create_registry("abraj")
    assert manager.active_profile_id() == "abraj"
    assert manager.profile_db_path("abraj") == root / "data/profiles/abraj/po_tool.db"
    assert manager.shared_db_path() == root / "data/shared/library.db"
    manager.activate("eshraqat")
    assert manager.active_profile_id() == "eshraqat"
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `python tests/profile_manager_backend.test.py`

Expected: FAIL with `ModuleNotFoundError: backend.profile_manager`.

- [ ] **Step 3: Implement registry and path ownership**

Implement `ProfileManager` with these public contracts:

```python
import json

class ProfileManager:
    VALID_IDS = ("abraj", "eshraqat")

    def __init__(self, data_root: Path, static_root: Path):
        self.data_root = Path(data_root)
        self.static_root = Path(static_root)
        self.registry_path = self.data_root / "profile-registry.json"

    def _profile_id(self, profile_id: str | None = None) -> str:
        value = profile_id or self.active_profile_id()
        if value not in self.VALID_IDS:
            raise ValueError(f"Unknown profile: {value}")
        return value

    def _write_registry(self, payload: dict) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.registry_path)

    def create_registry(self, active: str = "abraj") -> None:
        active = self._profile_id(active)
        self._write_registry({
            "version": 1,
            "active_profile": active,
            "profiles": [
                {"id": "abraj", "label": "Abraj Albadr"},
                {"id": "eshraqat", "label": "Eshraqat Albadr"},
            ],
        })

    def registry(self) -> dict:
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return raw if raw.get("active_profile") in self.VALID_IDS else {}
        except (OSError, ValueError, TypeError):
            return {}

    def active_profile_id(self) -> str:
        return self.registry().get("active_profile", "abraj")

    def activate(self, profile_id: str) -> dict:
        profile_id = self._profile_id(profile_id)
        payload = self.registry()
        if not payload:
            self.create_registry(profile_id)
        else:
            payload["active_profile"] = profile_id
            self._write_registry(payload)
        return {"ok": True, "active_profile": profile_id}

    def profile_dir(self, profile_id: str | None = None) -> Path:
        return self.data_root / "profiles" / self._profile_id(profile_id)

    def profile_db_path(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "po_tool.db"

    def settings_path(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "settings.json"

    def assets_dir(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "assets"

    def signatures_dir(self, profile_id: str | None = None) -> Path:
        return self.assets_dir(profile_id) / "sigs"

    def shared_db_path(self) -> Path:
        return self.data_root / "shared" / "library.db"
```

Use `Path.replace()` from a sibling temporary JSON file for atomic registry writes. Reject unknown profile IDs before touching the registry. A missing/unreadable registry returns Abraj as a safe fallback.

Expose one production singleton plus thin module functions (`active_profile_id`, `profile_db_path`, `settings_path`, `assets_dir`, `shared_db_path`, `activate_profile`) so existing modules do not instantiate managers.

- [ ] **Step 4: Run the focused test**

Run: `python tests/profile_manager_backend.test.py`

Expected: `profile manager backend tests passed`.

- [ ] **Step 5: Checkpoint**

If Git is available:

```powershell
git add backend/profile_manager.py tests/profile_manager_backend.test.py
git commit -m "feat: add company profile registry"
```

### Task 2: Idempotent Profile Migration and Shared Schema

**Files:**
- Create: `backend/shared_library.py`
- Modify: `backend/profile_manager.py`
- Modify: `app.py`
- Test: `tests/profile_migration_backend.test.py`

- [ ] **Step 1: Write a failing migration test**

Seed a temporary legacy `po_tool.db` with one document and one row in each catalog table. Seed `settings.json`, `logo.png`, and `sigs/stamp.png`. Call `bootstrap_profiles()` twice and assert:

```python
import sqlite3

def table_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

result = manager.bootstrap_profiles(
    legacy_db=legacy_db,
    legacy_settings=legacy_settings,
    legacy_assets=legacy_static,
)
assert result["ok"] is True
assert manager.profile_db_path("abraj").exists()
assert manager.settings_path("abraj").exists()
assert legacy_db.exists()                       # source untouched
assert table_count(manager.shared_db_path(), "clients") == 1
assert table_count(manager.profile_db_path("abraj"), "quotes") == 1
again = manager.bootstrap_profiles(
    legacy_db=legacy_db,
    legacy_settings=legacy_settings,
    legacy_assets=legacy_static,
)
assert again["already_applied"] is True
```

Also simulate a count mismatch and assert the registry is not activated.

- [ ] **Step 2: Run the migration test red**

Run: `python tests/profile_migration_backend.test.py`

Expected: FAIL because `bootstrap_profiles` and shared schema do not exist.

- [ ] **Step 3: Implement the shared schema**

Create `SHARED_SCHEMA` containing the current definitions for:

```text
items, companies, clients, quote_items, dn_items, inv_items, column_memory
```

Provide:

```python
def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else profile_manager.shared_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_shared_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SHARED_SCHEMA)

def copy_catalogs(source_db: Path, target_db: Path) -> dict[str, int]:
    init_shared_db(target_db)
    counts = {}
    with sqlite3.connect(source_db) as source, connect(target_db) as target:
        source.row_factory = sqlite3.Row
        for table in SHARED_TABLES:
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                keys = rows[0].keys()
                marks = ",".join("?" for _ in keys)
                columns = ",".join(keys)
                target.executemany(
                    f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})",
                    [tuple(row[key] for key in keys) for row in rows],
                )
            counts[table] = len(rows)
    return counts
```

Copy with `INSERT OR REPLACE`, preserve composite keys/data signatures, and return per-table counts for verification.

- [ ] **Step 4: Implement idempotent migration**

`ProfileManager.bootstrap_profiles()` must:

1. Create staging directories under `data/profiles/.staging-*`.
2. Copy legacy DB/settings/assets into staging Abraj.
3. Initialize clean Eshraqat profile directories and an empty database.
4. Initialize/copy the shared library.
5. Verify source/destination transactional and catalog counts.
6. Move staged directories into final profile paths.
7. Write a migration marker containing source DB size and completion timestamp.
8. Create/activate the Abraj registry only after all checks pass.
9. Remove staging on failure and return `{"ok": False, "error": str(exc)}`.

Update `app.main()` so profile bootstrap happens before imports of `config`, `database`, `migrations`, or `Api`. Keep `_hide_console()` and `sys.path` ordering unchanged.

- [ ] **Step 5: Run migration tests twice**

Run: `python tests/profile_migration_backend.test.py`

Expected: first and idempotent second runs pass, including the failure rollback case.

- [ ] **Step 6: Checkpoint**

If Git is available:

```powershell
git add app.py backend/profile_manager.py backend/shared_library.py tests/profile_migration_backend.test.py
git commit -m "feat: migrate legacy data into isolated profiles"
```

### Task 3: Profile-Aware Configuration, Settings, and Assets

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/api.py`
- Modify: `backend/signing.py`
- Modify: `backend/migrations.py`
- Test: `tests/profile_config_assets_backend.test.py`

- [ ] **Step 1: Write failing configuration isolation tests**

Use a temporary production singleton override and assert:

```python
activate_profile("abraj")
reload(config)
assert config.DB_PATH.name == "po_tool.db"
assert config.SETTINGS_PATH.parent.name == "abraj"

activate_profile("eshraqat")
reload(config)
assert config.ACTIVE_PROFILE == "eshraqat"
assert config.DOCUMENTS_ROOT == Path(r"D:\ESHRAQAT DOCUMENTS")
assert config.SETTINGS_PATH.parent.name == "eshraqat"
assert config.ASSETS_DIR.parent.name == "eshraqat"
assert config.INV_BANK_INFO == [config.CASH_BANK]
```

Verify that saving Eshraqat settings and uploading its logo do not alter Abraj files.

- [ ] **Step 2: Run the test red**

Run: `python tests/profile_config_assets_backend.test.py`

Expected: FAIL because active-profile constants and `ASSETS_DIR` do not exist.

- [ ] **Step 3: Split static application files from profile assets**

In `config.py`, retain `STATIC_DIR` for CSS/JS/fonts and add:

```python
ACTIVE_PROFILE = profile_manager.active_profile_id()
PROFILE_DIR = profile_manager.profile_dir()
DATA_DIR = PROFILE_DIR
DB_PATH = profile_manager.profile_db_path()
SETTINGS_PATH = profile_manager.settings_path()
ASSETS_DIR = profile_manager.assets_dir()
```

Select base defaults before applying the profile-local `settings.json`. Abraj uses existing defaults. Eshraqat defaults include `D:\ESHRAQAT DOCUMENTS`, burgundy brand values, `QT/IV/DN/PO/LT`, Cash-only banks, and empty signing labels.

Update logo upload to write `config.ASSETS_DIR/logo.png`. Update signing to resolve `config.ASSETS_DIR/sigs` at call time instead of a stale module-level `SIG_DIR`. Make settings reset delete only the active profile's file.

- [ ] **Step 4: Add active-profile API metadata**

Add:

```python
def get_active_profile(self) -> dict:
    return {
        "id": config.ACTIVE_PROFILE,
        "profiles": profile_manager.list_profiles(),
        "logo_data_uri": profile_manager.logo_data_uri(),
        "brand_color": config.PROFILE_ACCENT,
        "documents_root": str(config.DOCUMENTS_ROOT),
    }
```

Do not expose filesystem paths for signatures/stamps to the frontend.

- [ ] **Step 5: Run focused and preference tests**

Run:

```powershell
python tests/profile_config_assets_backend.test.py
python tests/user_preferences_backend.test.py
node tests/user_preferences.test.js
```

Expected: all pass.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: isolate profile settings and assets`.

### Task 4: Route Shared Catalogs and Active-Profile Business Queries

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/clients.py`
- Modify: `backend/data_manager.py`
- Modify: `backend/analytics.py`
- Test: `tests/profile_data_isolation_backend.test.py`

- [ ] **Step 1: Write a failing two-profile isolation test**

Create temporary Abraj/Eshraqat DBs plus one shared library. Seed client `HDEC` and item `Pipe` through public remember functions, then save one different quotation into each profile. Assert:

```python
assert database.suggest_clients("")[0]["name"] == "HDEC"
assert database.suggest_items("Pi")[0]["name"] == "Pipe"

activate("abraj")
assert {q["quote_no"] for q in database.list_quotes_in_db(2026, 7)} == {"QUT-A"}
assert clients.client_profile("HDEC")["quote_count"] == 1

activate("eshraqat")
assert {q["quote_no"] for q in database.list_quotes_in_db(2026, 7)} == {"QT-0001"}
assert clients.client_profile("HDEC")["quote_count"] == 1
```

Verify To-Do, costing, submitted, paid, deleted, doc columns, and analytics do not cross.

- [ ] **Step 2: Run the isolation test red**

Run: `python tests/profile_data_isolation_backend.test.py`

Expected: FAIL because every query still uses one `config.DB_PATH`.

- [ ] **Step 3: Add explicit connection ownership**

In `database.py` keep `_connect()` for the active profile and add `_connect_shared()` using `shared_library.connect()`. Route only these families to shared:

```text
suggest/remember items
suggest/remember companies
suggest/remember clients
suggest/remember quote items
suggest/remember DN items
suggest/remember invoice items
suggest/remember column values
```

Remove shared tables from the new profile schema after migration compatibility is proven, or leave inert compatibility copies that are never queried. Do not silently fall back from a failed shared connection to the active profile DB.

In `data_manager.py`, document sections use `_connect_profile`; catalog sections use `_connect_shared`. In `clients.py`, catalog add/delete/list use shared storage while document history/totals use the active profile connection. In `analytics.py`, all values remain active-profile-only.

- [ ] **Step 4: Run isolation and existing costing/delete tests**

Run:

```powershell
python tests/profile_data_isolation_backend.test.py
python tests/document_delete_backend.test.py
python tests/costing_scope_backend.test.py
python tests/invoice_paid_backend.test.py
python tests/po_submitted_quote_link_backend.test.py
```

Expected: all pass.

- [ ] **Step 5: Checkpoint**

If Git is available, commit with `feat: separate shared catalogs from company records`.

### Task 5: Safe Runtime Profile Switching

**Files:**
- Modify: `backend/profile_manager.py`
- Modify: `backend/api.py`
- Test: `tests/profile_switch_backend.test.py`

- [ ] **Step 1: Write failing switch/rollback tests**

Test `Api.switch_profile("eshraqat")` and a forced target validation failure. Assert success returns active metadata and failure leaves Abraj active:

```python
ok = api.switch_profile("eshraqat")
assert ok == {"ok": True, "active_profile": "eshraqat", "reload": True}

break_target_database()
bad = api.switch_profile("abraj")
assert bad["ok"] is False
assert profile_manager.active_profile_id() == "eshraqat"
```

- [ ] **Step 2: Run the test red**

Run: `python tests/profile_switch_backend.test.py`

Expected: FAIL because `switch_profile` is absent.

- [ ] **Step 3: Implement validated activation and module refresh**

Add a process lock around profile switching. Validate registry entry, DB integrity (`PRAGMA quick_check`), settings JSON, assets directory, and documents root parent. Save the previous ID. Activate target, reload `config`, generator, numbering, signing, clients, analytics, and data-manager modules that cache profile values. On any exception, reactivate/reload previous profile and return a concise error.

Return `reload: True` so the frontend performs a full shell reload after backend activation.

- [ ] **Step 4: Run switch tests**

Run: `python tests/profile_switch_backend.test.py`

Expected: success and rollback cases pass.

- [ ] **Step 5: Checkpoint**

If Git is available, commit with `feat: switch company profiles safely`.

### Task 6: Dashboard Switcher and Unsaved-Work Guard

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/app_styles.css`
- Modify: `static/dark.css`
- Test: `tests/company_profile_switcher.test.js`

- [ ] **Step 1: Write failing frontend structure/behavior tests**

Assert search, profile switcher, theme, settings order and accessible menu state. Execute `app.js` in a VM with mocked editor states/API and prove:

```javascript
await window.requestProfileSwitch("eshraqat");
assert(calls.includes("switch_profile:eshraqat"));
assert(reloadCalled);

quoteState.dirty = true;
modalResult = "cancel";
await window.requestProfileSwitch("abraj");
assert(!calls.includes("switch_profile:abraj"));
```

Cover Save, Discard, Cancel for PO/QUT/DN/INV/LTR using the existing state objects.

- [ ] **Step 2: Run the frontend test red**

Run: `node tests/company_profile_switcher.test.js`

Expected: FAIL because switcher markup/functions are missing.

- [ ] **Step 3: Add switcher markup and styling**

Insert `#dashProfileSwitcher` between `#dashSearch` and `#dashThemeToggle`, with a menu button, logo, active label, chevron, and two profile options. Use stable dimensions, keyboard focus, `aria-expanded`, `role="menu"`, and a check icon. Add compact responsive and dark-mode styles matching the approved mockup.

Replace hard-coded dashboard eyebrow/logo sources at bootstrap from `get_active_profile()`. Use a neutral desktop/window heading in visible About text where appropriate.

- [ ] **Step 4: Implement guarded switching**

Add one `activeDirtyEditor()` helper and one `requestProfileSwitch(id)` controller. Reuse existing save functions when the user chooses Save. Discard skips save; Cancel leaves everything unchanged. On backend success, call `window.location.reload()` so all module states and right panels bootstrap cleanly.

- [ ] **Step 5: Run frontend regressions**

Run:

```powershell
node tests/company_profile_switcher.test.js
node tests/dashboard_theme_toggle.test.js
node tests/editor_new_reset.test.js
node tests/editor_toolbar_layout.test.js
```

Expected: all pass.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: add guarded company switcher`.

### Task 7: Permanent Eshraqat Numbering

**Files:**
- Create: `backend/profile_numbering.py`
- Modify: `backend/database.py`
- Modify: `backend/api.py`
- Modify: `backend/po_numbering.py`
- Modify: `backend/quote_numbering.py`
- Modify: `backend/dn_numbering.py`
- Modify: `backend/inv_numbering.py`
- Modify: `backend/ltr_numbering.py`
- Test: `tests/eshraqat_numbering_backend.test.py`

- [ ] **Step 1: Write failing permanent-counter tests**

Test all five prefixes, restart persistence, year independence, edit retention, deletion non-reuse, failed-save rollback, and split-PO batch allocation:

```python
assert numbering.peek("quote") == "QT-0001"
assert numbering.commit("quote", "QT-0001")["ok"]
assert numbering.peek("quote") == "QT-0002"
database.record_deleted_doc("quote", "QT-0002")
assert numbering.next_after_existing("quote") == "QT-0003"
assert numbering.peek_many("po", 3) == ["PO-0001", "PO-0002", "PO-0003"]
```

- [ ] **Step 2: Run numbering tests red**

Run: `python tests/eshraqat_numbering_backend.test.py`

Expected: FAIL because `profile_numbering` and `document_counters` are missing.

- [ ] **Step 3: Add counters and formatting**

Add:

```sql
CREATE TABLE IF NOT EXISTS document_counters (
    kind TEXT PRIMARY KEY,
    last_value INTEGER NOT NULL DEFAULT 0
);
```

Implement `peek`, `peek_many`, `commit`, `format_number`, and `parse_number`. Determine the floor from counter, active document tables, and `deleted_docs`; this protects permanent non-reuse even after a restored/older counter file.

For Abraj, numbering modules execute their current code unchanged. For Eshraqat, they call `profile_numbering` and still use current per-type folder builders beneath `D:\ESHRAQAT DOCUMENTS`.

- [ ] **Step 4: Integrate save success/compensation**

For Eshraqat new saves, the backend owns the displayed number. Render to temporary output, save the document and stage the counter in one immediate connection transaction, move the PDF atomically, then commit. On any failure, roll back and remove temporary/final output. Edit mode keeps its number. PO split saves claim and commit a consecutive batch.

- [ ] **Step 5: Run numbering plus editor tests**

Run:

```powershell
python tests/eshraqat_numbering_backend.test.py
python tests/document_delete_backend.test.py
node tests/editor_new_reset.test.js
```

Expected: all pass.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: add permanent Eshraqat numbering`.

### Task 8: Foundation Regression and Documentation

**Files:**
- Modify: `docs/01-ARCHITECTURE.md`
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/09-DATA-MODEL.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`

- [ ] **Step 1: Run syntax checks and all focused foundation tests**

Run:

```powershell
python -m py_compile app.py backend\profile_manager.py backend\shared_library.py backend\profile_numbering.py backend\config.py backend\database.py backend\api.py backend\signing.py backend\clients.py backend\analytics.py backend\data_manager.py
node --check static\app.js
python tests\profile_manager_backend.test.py
python tests\profile_migration_backend.test.py
python tests\profile_config_assets_backend.test.py
python tests\profile_data_isolation_backend.test.py
python tests\profile_switch_backend.test.py
python tests\eshraqat_numbering_backend.test.py
node tests\company_profile_switcher.test.js
```

Expected: every command exits 0.

- [ ] **Step 2: Run existing regression suite**

Run every test listed in `docs/11-RUN-AND-MAINTAIN.md`, then:

```powershell
python smoke_test.py
python smoke_quote.py
```

Expected: all focused tests and both smoke suites pass; no existing Abraj PDF or database behavior regresses.

- [ ] **Step 3: Update the codebase map**

Document profile bootstrap, storage layout, connection ownership, shared library, API methods, switching flow, numbering strategy, migration key/rollback, new tests, and impact-map entries. Explicitly state that generated Abraj PDFs remain in place.

- [ ] **Step 4: Checkpoint**

If Git is available, commit with `docs: map multi-company profile foundation`.
