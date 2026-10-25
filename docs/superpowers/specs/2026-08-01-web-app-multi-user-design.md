# Business Hub on the web - multi-user design

**Date:** 2026-08-01
**Status:** design, awaiting approval
**Scope:** Phase 1 - turn the desktop app into a multi-user website for Abraj,
run it on test data, then cut over to real data.

---

## 1. Why

Today Business Hub is a single-user desktop app. Only Waleed can use it, and
everything lives on one PC. The goals are:

- Let employees use it - an **accountant** and a **DC** (document controller) to
  start, more later.
- Control what each person can see and do. Cost and profit figures must not
  reach every employee.
- Reach it from anywhere over the internet.
- Later (separate phases): notifications between documents, shared storage
  rules, and new features built on top.

## 2. Decisions already taken

| Question | Decision |
|---|---|
| Where used from | Internet - staff log in from anywhere |
| Roles at launch | Admin (Waleed), Accountant, DC |
| Custom roles | Granular permissions from day one; admin UI to create roles comes later |
| Who sees cost/profit | Admin and Accountant only. DC never. |
| Company profile | Abraj |
| Desktop app | Retired for Abraj **at cutover**. No parallel running. |
| Scope of v1 | Full parity - every screen the desktop app has |
| How built | Approach A - reuse the existing frontend, replace the bridge |
| Hosting | Free while testing (Oracle Cloud Always Free, to be confirmed). Reviewed before cutover. |

### Rejected, and why

- **Running desktop and web in parallel.** Both systems pick the next document
  number by scanning for the highest and adding one, and neither can see the
  other. Two users would produce two different invoices both numbered `022`.
- **Desktop app syncing to a server.** Costs more, not less: you build the
  server, the desktop app, *and* the sync layer, plus permissions twice
  (a desktop user can open the local database regardless of what the UI hides).
- **Rewriting the frontend in React/Vue.** Throws away ~32,000 lines of working
  UI and every calibrated millimetre of PDF layout.

## 3. Architecture

```
Browser (Admin / Accountant / DC)
        │  HTTPS  (hub.<domain>)
        ▼
┌────────────────────────────────────────┐
│ Server                                 │
│                                        │
│  NEW    web layer                      │
│         ├── login + sessions           │
│         ├── permission gate            │
│         └── bridge adapter ────┐       │
│                                │       │
│  UNCHANGED                     ▼       │
│         backend/*.py    (Api class)    │
│         ├── database.py                │
│         ├── *_generator.py             │
│         └── *_numbering.py  (see §6)   │
│                                        │
│         SQLite database                │
│         documents folder               │
│         headless Chrome (PDF)          │
└────────────────────────────────────────┘
```

Everything under "UNCHANGED" is the code that already exists and already works.
The new web layer sits in front of it.

## 4. The bridge swap - how Approach A works

### Browser side

Line 3 of `static/app.js` is the whole connection today:

```js
const api = () => window.pywebview.api;
```

It is replaced by a shim that presents the same interface but sends HTTP:

```js
// static/api_bridge.js
const api = () => new Proxy({}, {
    get: (_, name) => async (...args) =>
        postJson(`/api/${name}`, args),
});
```

Every existing call - `api().save_inv(...)`, `api().get_next_po_number()` - keeps
working untouched. No other frontend file changes for the bridge.

### Server side

One adapter walks the `Api` class and publishes each public method:

```python
for name, fn in inspect.getmembers(Api, inspect.isfunction):
    if name.startswith("_"):
        continue
    register_endpoint(f"/api/{name}", fn)
```

161 methods, one registration loop - not 161 hand-written endpoints.

**Framework:** FastAPI. Chosen because it is plain Python functions, has built-in
request validation, and does not impose a project layout on existing code.

### What genuinely changes

Six methods assume a local machine and need web equivalents:

| Today | On the web |
|---|---|
| `os.startfile(path)` - open a PDF | Served to a browser tab |
| `explorer /select` - reveal in folder | Removed (no web equivalent) |
| `os.startfile(path, "print")` | Browser print dialog |
| `FOLDER_DIALOG` - pick documents root | Admin-only text setting |
| `OPEN_DIALOG` - pick a file to attach | Standard file upload |
| Window/close handling in `app.py` | Not needed |

## 5. Login and permissions

### Tables

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,          -- argon2 or bcrypt, never plaintext
    full_name     TEXT NOT NULL DEFAULT '',
    role_id       INTEGER NOT NULL REFERENCES roles(id),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE roles (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    preset  INTEGER NOT NULL DEFAULT 0     -- 1 = shipped, cannot be deleted
);

CREATE TABLE role_permissions (
    role_id    INTEGER NOT NULL REFERENCES roles(id),
    permission TEXT NOT NULL,
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip         TEXT
);
```

Session token is delivered as an `HttpOnly`, `Secure`, `SameSite=Lax` cookie so
page scripts cannot read it.

### Permission keys

Granular from the start, so custom roles later need no re-plumbing. Examples:

```
view_costs          create_po        edit_po        delete_po
create_quote        create_dn        create_inv     record_payment
manage_lpo          sign_document    view_analytics
manage_users        manage_settings  switch_profile
```

### Preset roles at launch

| Permission group | Admin | Accountant | DC |
|---|---|---|---|
| View cost & profit | yes | yes | **no** |
| Create/edit PO | yes | no | yes |
| Create/edit invoice | yes | yes | no |
| Record payment | yes | yes | no |
| Quotations, DNs, LPOs | yes | yes | no |
| Analytics | yes | yes | no |
| Users & settings | yes | no | no |

### How permissions are enforced

A single map file lists the permission each API method requires:

```python
# backend/web/permissions.py
METHOD_PERMISSIONS = {
    "save_po":            "create_po",
    "save_inv":           "create_inv",
    "costing_summary":    "view_costs",
    ...
}
```

**Default deny.** A method absent from the map is Admin-only. Adding a new API
method without thinking about permissions therefore fails closed, not open.

### Cost data is stripped, not hidden

For a user without `view_costs`, the server removes cost and profit fields from
the response before sending it. The figures never reach the browser, so they
cannot be recovered by inspecting the page or the network traffic. Hiding a
column in CSS is not a permission.

## 6. Numbering - the change that makes multi-user safe

### The problem

All four numbering modules derive the next number from the filesystem:

- `po_numbering.py:44` - `folder.rglob("*.pdf")`, take max, add 1
- `inv_numbering.py:68`, `dn_numbering.py:71`, `quote_numbering.py:70` - same shape

Two users saving at the same moment both read the same maximum and both write
the same number.

### The fix

A counter table, incremented atomically inside a transaction:

```sql
CREATE TABLE doc_counters (
    profile   TEXT NOT NULL,
    kind      TEXT NOT NULL,        -- po | quote | dn | inv | ltr
    sub_type  TEXT NOT NULL DEFAULT '',
    last_no   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile, kind, sub_type)
);
```

Rules:

- **Seeded once** from the current filesystem maximum, so numbering continues
  exactly where the desktop app left off.
- **Reserved when the editor opens**, not when the document is saved - so a slow
  connection at save time cannot produce a wrong number.
- **Deleted numbers are never reused.** The existing tombstone behaviour
  (`deleted_document_numbers`) is preserved.
- **A reserved number that is never used is released** when the editor is
  abandoned, so numbers are not silently consumed.

This must land **before** anyone else logs in. It is the one item that is unsafe
to defer.

## 7. Documents and files

- Stored on the server, in the **same folder layout as now**
  (`<ROOT>/PURCHASE ORDERS/<year>/<month>/…`), so nothing about generation
  changes and existing files can be copied straight across.
- Served **through the app**, never as public links, so permissions apply to
  files as well as data.
- File paths are never sent to the browser. Documents are addressed by kind and
  number, and the server resolves the path.

## 8. Audit trail

Multiple people touching financial documents means "who changed this?" must be
answerable.

```sql
CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY,
    at        TEXT NOT NULL,
    user_id   INTEGER NOT NULL,
    action    TEXT NOT NULL,       -- create | edit | delete | sign | pay | login
    kind      TEXT,                -- po | quote | dn | inv | ltr | lpo
    doc_no    TEXT,
    detail    TEXT                 -- JSON, what changed
);
```

Written for every create, edit, delete, signature and payment. Visible to Admin
only.

## 9. Test data, then cutover

- The server runs against a **copy** of the Abraj database and documents folder,
  selected by an environment variable.
- Staff practise on it. Nothing they produce goes to a client, and the real
  records are untouched.
- **Cutover** = point the server at the real data, re-seed `doc_counters` from
  the true maximums, and stop using the desktop app for Abraj.

## 10. Error handling

The three failure modes Waleed named are explicit requirements:

| Failure | Behaviour |
|---|---|
| Slow connection | UI stays responsive; save shows progress; nothing blocks on the network |
| Save fails | Automatic retry; typed data kept in the browser until confirmed saved; never silently lost |
| Number cannot be fetched | Editor refuses to open rather than opening with a guessed number |
| Session expires mid-edit | Redirect to login, unsaved work preserved and re-submitted after |
| PDF generation fails | Clear error naming the stage; document data is still saved |

## 11. Testing

- **The existing 67 test files keep running** (47 Python, 20 JavaScript). They
  exercise `backend/*.py`, which does not change. This is the main safety net
  for the port.
- **New tests required:**
  - a DC session never receives cost or profit fields in any response
  - two simultaneous number requests return different numbers
  - a method missing from `METHOD_PERMISSIONS` is denied, not allowed
  - login, logout, session expiry, and locked/inactive users
  - file access is refused for a user without permission to that document

## 12. Not in this phase

Deliberately excluded, each its own later project:

1. **Notifications** - signed DN alerts the accountant
2. **Cloud/object storage** for documents
3. **Admin UI for creating custom roles** (the data model supports it already)
4. **Phone capture** of signed delivery notes
5. **Eshraqat** - moves over after Abraj proves out
6. **Offline working**

## 13. Open questions

- **Hosting before cutover.** Oracle Always Free is fine for the test phase, but
  Oracle stops instances idling below 10% CPU and 10% network over 7 days - which
  is what a three-person business app looks like. Before cutover, either move the
  Oracle account to pay-as-you-go (Always Free resources remain free, idle reclaim
  reportedly no longer applies) or move to a ~$5/month VPS. Confirmed 2026-08-01.
- Backup destination and frequency for the live phase.

## 14. Confirmed decisions

- **2026-08-01** - the Accountant role includes analytics access (revenue,
  outstanding invoices, client totals), as shown in the role table in §5.
