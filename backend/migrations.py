"""
One-shot migrations that bring existing data on disk + in the DB in sync with
schema changes. Each function is idempotent - running it twice does nothing
the second time.

Run order is fixed by `run_all()`. The result of each step is recorded as a
row in the `_migrations_applied` table so it isn't repeated.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config, database, doc_settings


_REGISTRY = []   # populated by @migration decorator below


def migration(key: str):
    """Decorator: register a migration function under a stable key."""
    def wrap(fn):
        _REGISTRY.append((key, fn))
        return fn
    return wrap


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations_applied (
            key        TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            notes      TEXT
        )
    """)


def _was_applied(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM _migrations_applied WHERE key = ?", (key,)
    ).fetchone()
    return row is not None


def _record(conn: sqlite3.Connection, key: str, notes: str = "") -> None:
    conn.execute(
        "INSERT INTO _migrations_applied(key, applied_at, notes) VALUES (?, ?, ?)",
        (key, datetime.now().isoformat(timespec="seconds"), notes),
    )


def run_all() -> list[dict]:
    """Apply every pending migration in order. Returns a per-step report."""
    out = []
    with database._connect() as conn:
        _ensure_table(conn)
        for key, fn in _REGISTRY:
            if _was_applied(conn, key):
                out.append({"key": key, "status": "skipped"})
                continue
            try:
                notes = fn(conn) or ""
            except Exception as e:
                out.append({"key": key, "status": "failed", "error": str(e)})
                continue
            _record(conn, key, str(notes))
            conn.commit()
            out.append({"key": key, "status": "applied", "notes": str(notes)})
    return out


@migration("2026-07-07_paid_invoices")
def _paid_invoices(conn: sqlite3.Connection) -> str:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paid_invoices (
            inv_no  TEXT NOT NULL PRIMARY KEY,
            paid_at TEXT NOT NULL
        )
    """)
    return "ensured paid_invoices table"


@migration("2026-07-08_attention_ignores")
def _attention_ignores(conn: sqlite3.Connection) -> str:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attention_ignores (
            kind       TEXT NOT NULL,
            doc_no     TEXT NOT NULL,
            category   TEXT NOT NULL,
            ignored_at TEXT NOT NULL,
            PRIMARY KEY (kind, doc_no, category)
        )
    """)
    return "ensured attention_ignores table"


@migration("2026-07-13_po_source_quote")
def _po_source_quote(conn: sqlite3.Connection) -> str:
    """Allow purchase orders to store an optional quotation reference."""
    cols = conn.execute("PRAGMA table_info(pos)").fetchall()
    if any(r["name"] == "source_quote_no" for r in cols):
        return "pos.source_quote_no already present"
    conn.execute("ALTER TABLE pos ADD COLUMN source_quote_no TEXT")
    return "added pos.source_quote_no"


@migration("2026-07-15_po_fulfillment")
def _po_fulfillment(conn: sqlite3.Connection) -> str:
    """Ensure the PO fulfillment (delivered/paid item marks) table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS po_fulfillment (
            po_no       TEXT    NOT NULL,
            item_index  INTEGER NOT NULL,
            status_kind TEXT    NOT NULL,
            source_kind TEXT    NOT NULL,
            source_no   TEXT    NOT NULL,
            marked_at   TEXT    NOT NULL,
            PRIMARY KEY (po_no, item_index, status_kind)
        )
    """)
    return "ensured po_fulfillment table"


# --------------------------------------------------------------------------- #
# 2026-05-31 - Phase F.1
# Folder renames:
#   QUOTATION    -> QUOTATIONS         (drop per-type subfolder)
#   DELIVERY     -> DELIVERY NOTES
#   INVOICE      -> INVOICES           (drop per-type subfolder)
#   D:\ABRAJ\CSSP\PO\<month>  -> <DOCUMENTS_ROOT>\PURCHASE ORDERS\<year>\<month>
# --------------------------------------------------------------------------- #


def _move_tree_into(src: Path, dst: Path) -> int:
    """
    Move every file under `src` into `dst`, preserving relative subpaths.
    Returns the number of files actually moved.

    If `src` doesn't exist, nothing happens. If a destination file already
    exists, the source is left alone (we never overwrite).
    """
    if not src.exists() or not src.is_dir():
        return 0
    moved = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        target = dst / rel
        if target.exists():
            continue   # don't clobber
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(f), str(target))
            moved += 1
        except (OSError, shutil.Error):
            continue
    # Remove now-empty source directory tree (best effort).
    for d in sorted([p for p in src.rglob("*") if p.is_dir()], reverse=True):
        try: d.rmdir()
        except OSError: pass
    try: src.rmdir()
    except OSError: pass
    return moved


def _strip_type_subfolder(client_yr_dir: Path) -> int:
    """
    For a path like <Client>/<Year>/, flatten any second-level type folders
    (MT/EQ/MP/SV) by moving their MONTH/* contents up to <Client>/<Year>/<MONTH>/.
    Returns the number of files moved.
    """
    if not client_yr_dir.is_dir():
        return 0
    moved = 0
    for type_dir in list(client_yr_dir.iterdir()):
        if not type_dir.is_dir():
            continue
        # Only flatten directories that look like type codes (2 uppercase letters).
        if not re.fullmatch(r"[A-Z]{2}", type_dir.name):
            continue
        for month_dir in list(type_dir.iterdir()):
            if not month_dir.is_dir():
                continue
            target_month = client_yr_dir / month_dir.name
            target_month.mkdir(parents=True, exist_ok=True)
            for f in list(month_dir.iterdir()):
                if not f.is_file():
                    continue
                target = target_month / f.name
                if target.exists():
                    continue
                try:
                    shutil.move(str(f), str(target))
                    moved += 1
                except (OSError, shutil.Error):
                    pass
            try: month_dir.rmdir()
            except OSError: pass
        try: type_dir.rmdir()
        except OSError: pass
    return moved


def _update_pdf_paths(conn: sqlite3.Connection, table: str, id_col: str,
                       old_substr: str, new_substr: str) -> int:
    """
    Replace `old_substr` with `new_substr` inside the pdf_path column of
    `table`, but only for rows whose file currently exists at the rewritten
    path (defensive - don't update rows whose file we couldn't migrate).
    """
    rows = conn.execute(
        f"SELECT {id_col}, pdf_path FROM {table} WHERE pdf_path LIKE ?",
        (f"%{old_substr}%",),
    ).fetchall()
    fixed = 0
    for r in rows:
        new_path = (r["pdf_path"] or "").replace(old_substr, new_substr)
        if Path(new_path).exists():
            conn.execute(
                f"UPDATE {table} SET pdf_path = ? WHERE {id_col} = ?",
                (new_path, r[id_col]),
            )
            fixed += 1
    return fixed


@migration("2026-05-31_rename_doc_roots")
def _rename_doc_roots(conn: sqlite3.Connection) -> str:
    base = config.DOCUMENTS_ROOT
    notes = []

    # ---- QUOTATION -> QUOTATIONS  +  drop type subfolders ----
    old_q = base / "QUOTATION"
    new_q = base / "QUOTATIONS"
    if old_q.exists():
        n = _move_tree_into(old_q, new_q)
        notes.append(f"QUOTATION->QUOTATIONS: moved {n} files")
    # In the destination (whether just moved or pre-existing), flatten type subfolders.
    if new_q.exists():
        flat = 0
        for client_dir in list(new_q.iterdir()):
            if not client_dir.is_dir(): continue
            for yr_dir in list(client_dir.iterdir()):
                if not yr_dir.is_dir(): continue
                flat += _strip_type_subfolder(yr_dir)
        if flat:
            notes.append(f"QUOTATIONS: flattened {flat} files out of type subfolders")
    n = _update_pdf_paths(conn, "quotes", "quote_no",
                          str(base / "QUOTATION"), str(new_q))
    if n: notes.append(f"quotes.pdf_path: rewrote {n} rows for QUOTATION->QUOTATIONS")
    # Also strip type segment ("\MT\", "\EQ\", "\MP\", "\SV\") that may still be in DB rows.
    rows = conn.execute("SELECT quote_no, pdf_path FROM quotes").fetchall()
    fixed = 0
    for r in rows:
        p = r["pdf_path"] or ""
        new_p = re.sub(r"\\(MT|EQ|MP|SV)\\", r"\\", p)
        if new_p != p and Path(new_p).exists():
            conn.execute("UPDATE quotes SET pdf_path = ? WHERE quote_no = ?",
                         (new_p, r["quote_no"]))
            fixed += 1
    if fixed: notes.append(f"quotes.pdf_path: dropped type segment from {fixed} rows")

    # ---- DELIVERY -> DELIVERY NOTES ----
    old_d = base / "DELIVERY"
    new_d = base / "DELIVERY NOTES"
    if old_d.exists():
        n = _move_tree_into(old_d, new_d)
        notes.append(f"DELIVERY->DELIVERY NOTES: moved {n} files")
    n = _update_pdf_paths(conn, "dns", "dn_no",
                          str(old_d), str(new_d))
    if n: notes.append(f"dns.pdf_path: rewrote {n} rows")

    # ---- INVOICE -> INVOICES  +  drop type subfolders ----
    old_i = base / "INVOICE"
    new_i = base / "INVOICES"
    if old_i.exists():
        n = _move_tree_into(old_i, new_i)
        notes.append(f"INVOICE->INVOICES: moved {n} files")
    if new_i.exists():
        flat = 0
        for client_dir in list(new_i.iterdir()):
            if not client_dir.is_dir(): continue
            for yr_dir in list(client_dir.iterdir()):
                if not yr_dir.is_dir(): continue
                flat += _strip_type_subfolder(yr_dir)
        if flat:
            notes.append(f"INVOICES: flattened {flat} files out of type subfolders")
    n = _update_pdf_paths(conn, "invoices", "inv_no",
                          str(base / "INVOICE"), str(new_i))
    if n: notes.append(f"invoices.pdf_path: rewrote {n} rows for INVOICE->INVOICES")
    rows = conn.execute("SELECT inv_no, pdf_path FROM invoices").fetchall()
    fixed = 0
    for r in rows:
        p = r["pdf_path"] or ""
        new_p = re.sub(r"\\(MT|EQ|MP|SV)\\", r"\\", p)
        if new_p != p and Path(new_p).exists():
            conn.execute("UPDATE invoices SET pdf_path = ? WHERE inv_no = ?",
                         (new_p, r["inv_no"]))
            fixed += 1
    if fixed: notes.append(f"invoices.pdf_path: dropped type segment from {fixed} rows")

    # ---- PO_ROOT move: D:\ABRAJ\CSSP\PO\<month>  ->  <BASE>\PURCHASE ORDERS\<year>\<month> ----
    legacy_po = Path(r"D:\ABRAJ\CSSP\PO")
    new_po_root = base / "PURCHASE ORDERS"
    if legacy_po.exists() and legacy_po.is_dir():
        moved = 0
        # Legacy layout: <PO>\<MONTH>\<files>. We don't have the year, so we
        # assign by file mtime (year of newest .pdf in that month folder).
        for month_dir in list(legacy_po.iterdir()):
            if not month_dir.is_dir(): continue
            if month_dir.name not in config.MONTH_FOLDERS.values(): continue
            for f in list(month_dir.iterdir()):
                if not f.is_file() or not f.name.lower().endswith(".pdf"): continue
                yr = datetime.fromtimestamp(f.stat().st_mtime).year
                target_dir = new_po_root / str(yr) / month_dir.name
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / f.name
                if target.exists(): continue
                try:
                    shutil.move(str(f), str(target)); moved += 1
                except (OSError, shutil.Error): pass
            try: month_dir.rmdir()
            except OSError: pass
        try: legacy_po.rmdir()
        except OSError: pass
        if moved: notes.append(f"PO legacy -> PURCHASE ORDERS: moved {moved} files")
    # (legacy PO path rewrite continues below)
    return "; ".join(notes) if notes else "no-op (nothing to migrate)"


# --------------------------------------------------------------------------- #
# 2026-05-31 - Phase G.5
# Delivery Notes: rename DN-N.pdf -> <client>-DN-N.pdf so DN filenames
# include the client prefix (matches the QUT convention).
# --------------------------------------------------------------------------- #


@migration("2026-05-31_multivariant_items")
def _multivariant_items(conn: sqlite3.Connection) -> str:
    """
    Phase G.15: add `data_sig` column to items / quote_items / dn_items /
    inv_items so the PRIMARY KEY can include it. After this, saving two
    variants of the same name (different specs/description) creates TWO
    rows instead of one overwriting the other.

    SQLite can't ALTER an existing PRIMARY KEY in place, so we recreate
    each table with the new schema and copy rows over with a computed
    data_sig.
    """
    import hashlib, json as _json
    from . import database

    def _sig_from_data(data):
        if not isinstance(data, dict): return ""
        norm = {}
        for k, v in data.items():
            if v is None: continue
            s = str(v).strip()
            if not s: continue
            norm[str(k).strip().lower()] = s
        if not norm: return "empty"
        payload = _json.dumps(norm, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _sig_from_json(data_json):
        try: return _sig_from_data(_json.loads(data_json or "{}"))
        except Exception: return ""

    notes = []

    # Check if migration has already partially applied (data_sig column exists).
    def _has_sig(table):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == "data_sig" for r in rows)

    # The four document catalogs were RETIRED on 2026-08-20 (the item record is
    # the only store now). A dropped table has nothing to migrate - treat it as
    # already done, so this old Phase-G.15 reshape neither crashes on the
    # missing table nor recreates it.
    def _gone(table):
        return not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()

    # --- items (PO) ----------------------------------------------------------
    if _gone("items"):
        notes.append("items: retired (record is the store)")
    elif not _has_sig("items"):
        old = conn.execute("SELECT name, description, unit, last_company, use_count, last_used_at FROM items").fetchall()
        conn.execute("DROP TABLE IF EXISTS items_new")
        conn.execute("""
            CREATE TABLE items_new (
                name           TEXT NOT NULL COLLATE NOCASE,
                data_sig       TEXT NOT NULL DEFAULT '',
                description    TEXT,
                unit           TEXT,
                last_company   TEXT,
                use_count      INTEGER NOT NULL DEFAULT 0,
                last_used_at   TEXT,
                PRIMARY KEY (name, data_sig)
            )
        """)
        for r in old:
            sig = _sig_from_data({
                "description": r["description"],
                "unit": r["unit"],
                "last_company": r["last_company"],
            })
            conn.execute(
                "INSERT OR REPLACE INTO items_new(name, data_sig, description, unit, last_company, use_count, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["name"], sig, r["description"], r["unit"], r["last_company"], r["use_count"], r["last_used_at"]),
            )
        conn.execute("DROP TABLE items")
        conn.execute("ALTER TABLE items_new RENAME TO items")
        notes.append(f"items: migrated {len(old)} rows to multi-variant schema")
    else:
        notes.append("items: already has data_sig")

    # --- quote_items ---------------------------------------------------------
    if _gone("quote_items"):
        notes.append("quote_items: retired (record is the store)")
    elif not _has_sig("quote_items"):
        old = conn.execute("SELECT quote_type, name, data_json, use_count, last_used_at FROM quote_items").fetchall()
        conn.execute("DROP TABLE IF EXISTS quote_items_new")
        conn.execute("""
            CREATE TABLE quote_items_new (
                quote_type     TEXT NOT NULL,
                name           TEXT NOT NULL COLLATE NOCASE,
                data_sig       TEXT NOT NULL DEFAULT '',
                data_json      TEXT,
                use_count      INTEGER NOT NULL DEFAULT 0,
                last_used_at   TEXT,
                PRIMARY KEY (quote_type, name, data_sig)
            )
        """)
        for r in old:
            sig = _sig_from_json(r["data_json"])
            conn.execute(
                "INSERT OR REPLACE INTO quote_items_new(quote_type, name, data_sig, data_json, use_count, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["quote_type"], r["name"], sig, r["data_json"], r["use_count"], r["last_used_at"]),
            )
        conn.execute("DROP TABLE quote_items")
        conn.execute("ALTER TABLE quote_items_new RENAME TO quote_items")
        notes.append(f"quote_items: migrated {len(old)} rows")
    else:
        notes.append("quote_items: already has data_sig")

    # --- dn_items ------------------------------------------------------------
    if _gone("dn_items"):
        notes.append("dn_items: retired (record is the store)")
    elif not _has_sig("dn_items"):
        old = conn.execute("SELECT name, data_json, use_count, last_used_at FROM dn_items").fetchall()
        conn.execute("DROP TABLE IF EXISTS dn_items_new")
        conn.execute("""
            CREATE TABLE dn_items_new (
                name           TEXT NOT NULL COLLATE NOCASE,
                data_sig       TEXT NOT NULL DEFAULT '',
                data_json      TEXT,
                use_count      INTEGER NOT NULL DEFAULT 0,
                last_used_at   TEXT,
                PRIMARY KEY (name, data_sig)
            )
        """)
        for r in old:
            sig = _sig_from_json(r["data_json"])
            conn.execute(
                "INSERT OR REPLACE INTO dn_items_new(name, data_sig, data_json, use_count, last_used_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["name"], sig, r["data_json"], r["use_count"], r["last_used_at"]),
            )
        conn.execute("DROP TABLE dn_items")
        conn.execute("ALTER TABLE dn_items_new RENAME TO dn_items")
        notes.append(f"dn_items: migrated {len(old)} rows")
    else:
        notes.append("dn_items: already has data_sig")

    # --- inv_items -----------------------------------------------------------
    if _gone("inv_items"):
        notes.append("inv_items: retired (record is the store)")
    elif not _has_sig("inv_items"):
        old = conn.execute("SELECT inv_type, name, data_json, use_count, last_used_at FROM inv_items").fetchall()
        conn.execute("DROP TABLE IF EXISTS inv_items_new")
        conn.execute("""
            CREATE TABLE inv_items_new (
                inv_type       TEXT NOT NULL,
                name           TEXT NOT NULL COLLATE NOCASE,
                data_sig       TEXT NOT NULL DEFAULT '',
                data_json      TEXT,
                use_count      INTEGER NOT NULL DEFAULT 0,
                last_used_at   TEXT,
                PRIMARY KEY (inv_type, name, data_sig)
            )
        """)
        for r in old:
            sig = _sig_from_json(r["data_json"])
            conn.execute(
                "INSERT OR REPLACE INTO inv_items_new(inv_type, name, data_sig, data_json, use_count, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["inv_type"], r["name"], sig, r["data_json"], r["use_count"], r["last_used_at"]),
            )
        conn.execute("DROP TABLE inv_items")
        conn.execute("ALTER TABLE inv_items_new RENAME TO inv_items")
        notes.append(f"inv_items: migrated {len(old)} rows")
    else:
        notes.append("inv_items: already has data_sig")

    return "; ".join(notes)


@migration("2026-05-31_add_cash_bank")
def _add_cash_bank(conn: sqlite3.Connection) -> str:
    """
    Append the "cash" sentinel entry to any saved invoice.bank_info list
    that doesn't already have it. Lets users who saved their bank list
    before Phase G.12 still see "Cash (no bank details)" in the dropdown.
    """
    import json
    if not config.SETTINGS_PATH.exists():
        return "no settings.json - nothing to do"
    try:
        raw = config.SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        return f"settings.json unreadable: {e}"
    inv = data.get("invoice") or {}
    banks = inv.get("bank_info")
    if not isinstance(banks, list):
        return "no invoice.bank_info override to update"
    if any(isinstance(b, dict) and (b.get("key") == "cash") for b in banks):
        return "cash entry already present"
    banks.append({
        "key": "cash",
        "label": "Cash (no bank details)",
        "company_line": "", "bank_name": "", "branch": "", "swift": "",
        "account_no": "", "iban": "", "currency": "USD",
    })
    inv["bank_info"] = banks
    data["invoice"] = inv
    config.save_user_settings(data)
    # Re-apply so the running process picks up the change.
    import importlib
    importlib.reload(config)
    return f"appended cash entry to invoice.bank_info ({len(banks)} entries total)"


@migration("2026-05-31_dn_client_prefix")
def _dn_client_prefix(conn: sqlite3.Connection) -> str:
    root = config.DN_ROOT
    notes = []
    if not root.exists():
        return "DN root missing - nothing to do"

    moved = 0
    for client_dir in sorted(root.iterdir()):
        if not client_dir.is_dir(): continue
        client = client_dir.name
        for year_dir in client_dir.iterdir():
            if not year_dir.is_dir(): continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir(): continue
                for f in list(month_dir.glob("DN-*.pdf")):
                    # Only the OLD pattern (DN-N, no client prefix).
                    m = re.match(r"^DN-(\d+)$", f.stem, re.IGNORECASE)
                    if not m: continue
                    n = m.group(1)
                    new_stem = f"{client}-DN-{n}"
                    target = f.with_name(f"{new_stem}.pdf")
                    if target.exists():
                        continue   # don't overwrite
                    try:
                        f.rename(target)
                    except OSError:
                        continue
                    # Update the DB: pdf_path + dn_no (which is the PRIMARY KEY).
                    conn.execute(
                        "UPDATE dns SET dn_no = ?, pdf_path = ? WHERE dn_no = ?",
                        (new_stem, str(target), f.stem),
                    )
                    moved += 1
    if moved:
        notes.append(f"DN client-prefix rename: {moved} files renamed")
    return "; ".join(notes) if notes else "no DN files needed renaming"


# --------------------------------------------------------------------------- #
# 2026-06-03 - Phase N
# Re-arrange document filenames so the TYPE code leads and the running number
# is zero-padded to 3 digits:
#     QUT-<CLIENT>-<TYPE>-<N>   ->  QUT-<TYPE>-<CLIENT>-<NNN>
#     INV-<CLIENT>-<TYPE>-<N>   ->  INV-<TYPE>-<CLIENT>-<NNN>
#     <CLIENT>-DN-<N> | DN-<N>  ->  DN-<CLIENT>-<NNN> | DN-<NNN>
# Renames the file on disk AND updates the matching DB row (pk + pdf_path)
# and any per-doc signing overlay in doc_meta. Never overwrites an existing
# target; idempotent (already-new names are skipped).
# --------------------------------------------------------------------------- #

_PHASE_N_CODES = {"MT", "EQ", "MP", "SV"}


def _new_stem_typed(prefix: str, stem: str) -> str | None:
    """For QUT/INV: PREFIX-CLIENT-CODE-N -> PREFIX-CODE-CLIENT-NNN.
    Returns None if the stem is already new-format or unrecognised."""
    parts = stem.split("-")
    if len(parts) < 4 or parts[0] != prefix:
        return None
    if parts[1] in _PHASE_N_CODES:
        return None                       # already new arrangement
    if parts[-2] not in _PHASE_N_CODES or not parts[-1].isdigit():
        return None                       # not a recognisable doc number
    code   = parts[-2]
    n      = int(parts[-1])
    client = "-".join(parts[1:-2])
    if not client:
        return None
    return f"{prefix}-{code}-{client}-{n:03d}"


def _new_stem_dn(stem: str) -> str | None:
    """For DN: <CLIENT>-DN-N | DN-N -> DN-<CLIENT>-NNN | DN-NNN.
    Returns None if already new-format or unrecognised."""
    parts = stem.split("-")
    if parts and parts[0].upper() == "DN":
        if len(parts) == 2 and parts[1].isdigit():
            return f"DN-{int(parts[1]):03d}"      # oldest bare DN-N
        return None                                # already DN-CLIENT-NNN
    if len(parts) >= 3 and parts[-2].upper() == "DN" and parts[-1].isdigit():
        client = "-".join(parts[:-2])
        if client:
            return f"DN-{client}-{int(parts[-1]):03d}"
    return None


def _rename_doc_tree(conn: sqlite3.Connection, root: Path, table: str,
                     pk_col: str, doc_meta_type: str,
                     new_stem_fn) -> int:
    """Walk every *.pdf under `root`, rename to the new arrangement, and keep
    the DB in sync (pk column, pdf_path, doc_meta overlays). Returns the count
    of files renamed."""
    if not root.exists():
        return 0
    renamed = 0
    for f in list(root.rglob("*.pdf")):
        old_stem = f.stem
        new_stem = new_stem_fn(old_stem)
        if not new_stem or new_stem == old_stem:
            continue
        target = f.with_name(f"{new_stem}.pdf")
        if target.exists():
            continue                       # never clobber
        try:
            f.rename(target)
        except OSError:
            continue
        # DB: main row (pk is the doc number) + pdf_path.
        try:
            conn.execute(
                f"UPDATE {table} SET {pk_col} = ?, pdf_path = ? WHERE {pk_col} = ?",
                (new_stem, str(target), old_stem),
            )
            # Some rows may store pdf_path but with a stale name even if the pk
            # already matched - patch path on the new pk too (defensive).
            conn.execute(
                f"UPDATE {table} SET pdf_path = ? WHERE {pk_col} = ?",
                (str(target), new_stem),
            )
            # Signing overlay key follows the doc number.
            conn.execute(
                "UPDATE OR IGNORE doc_meta SET doc_no = ? WHERE doc_type = ? AND doc_no = ?",
                (new_stem, doc_meta_type, old_stem),
            )
        except sqlite3.Error:
            pass
        renamed += 1
    return renamed


@migration("2026-06-14_costing_fields")
def _add_costing_fields(conn: sqlite3.Connection) -> str:
    """
    Phase N.1 - add the document-level costing columns to existing DBs.
    Per-row `cost` rides inside rows_json (no column needed). New installs get
    these from database._SCHEMA; this migration covers DBs created earlier.
    Idempotent: each ADD COLUMN is guarded by a PRAGMA column-existence check.
    """
    def _has_col(table: str, col: str) -> bool:
        return any(r["name"] == col
                   for r in conn.execute(f"PRAGMA table_info({table})").fetchall())

    notes = []

    def _add(table: str, col: str, decl: str) -> None:
        if not _has_col(table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            notes.append(f"{table}.{col}")

    _add("quotes",   "extra_costs_json", "TEXT")
    _add("quotes",   "bid_status",       "TEXT NOT NULL DEFAULT 'open'")
    _add("dns",      "source_quote_no",  "TEXT")
    _add("dns",      "extra_costs_json", "TEXT")
    _add("invoices", "source_quote_no",  "TEXT")
    _add("invoices", "extra_costs_json", "TEXT")

    return ("added columns: " + ", ".join(notes)) if notes else "all costing columns already present"


@migration("2026-06-03_doc_name_rearrange")
def _doc_name_rearrange(conn: sqlite3.Connection) -> str:
    notes = []
    q = _rename_doc_tree(conn, config.QUOTE_ROOT, "quotes", "quote_no",
                         "qut", lambda s: _new_stem_typed("QUT", s))
    if q: notes.append(f"QUT: re-arranged {q} filenames")
    i = _rename_doc_tree(conn, config.INV_ROOT, "invoices", "inv_no",
                         "inv", lambda s: _new_stem_typed("INV", s))
    if i: notes.append(f"INV: re-arranged {i} filenames")
    d = _rename_doc_tree(conn, config.DN_ROOT, "dns", "dn_no",
                         "dn", _new_stem_dn)
    if d: notes.append(f"DN: re-arranged {d} filenames")
    return "; ".join(notes) if notes else "no files needed re-arranging"


# --------------------------------------------------------------------------- #
# 2026-06-12 - Phase M.1
# Move existing PDFs into the new day+type folder layout:
#     QUOTATIONS\<TypeLabel>\<Client>\<Year>\<MONTH>\<DD-MM-YYYY>\
#     INVOICES\<TypeLabel>\<Client>\<Year>\<MONTH>\<DD-MM-YYYY>\
#     DELIVERY NOTES\<Client>\<Year>\<MONTH>\<DD-MM-YYYY>\
#     PURCHASE ORDERS\<Year>\<MONTH>\<DD-MM-YYYY>\
#     LETTERS\<EN|AR>\<Year>\<MONTH>\<DD-MM-YYYY>\
#
# The DB is the source of truth for the document's date - using mtime would
# put files in random day folders. Files that exist on disk but have no DB
# row are migrated using their last-modified date as a fallback (we tag the
# folder with "_no_db" so it's obvious those are imports).
# --------------------------------------------------------------------------- #

_QUT_TYPE_LABEL = {"MT": "Materials", "EQ": "Equipment", "MP": "Manpower", "SV": "Service"}
_INV_TYPE_LABEL = {"MT": "Materials", "EQ": "Equipment", "MP": "Manpower", "SV": "Service"}
_KNOWN_CODES = {"MT", "EQ", "MP", "SV"}


def _type_code_from_stem(stem: str) -> str:
    """Pull MT/EQ/MP/SV out of QUT-... or INV-... regardless of layout."""
    parts = stem.split("-")
    if len(parts) >= 2 and parts[1].upper() in _KNOWN_CODES:
        return parts[1].upper()
    if len(parts) >= 2 and parts[-2].upper() in _KNOWN_CODES:
        return parts[-2].upper()
    return "MT"


def _parse_date_display(s: str) -> tuple[int, int, int] | None:
    """Accept 'D/M/YYYY' (the in-app display form) or 'YYYY-MM-DD' (ISO).
    Returns (year, month, day) or None."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # ISO
    if len(s) >= 8 and s[4:5] in "-/." and s[:4].isdigit():
        try:
            from datetime import date as _d
            d = _d.fromisoformat(s.replace("/", "-").replace(".", "-"))
            return d.year, d.month, d.day
        except Exception:
            pass
    # D/M/YYYY display
    import re as _re
    parts = _re.split(r"[/\-.]", s)
    if len(parts) != 3:
        return None
    try:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y += 2000
        return y, m, d
    except (TypeError, ValueError):
        return None


def _day_folder(day: int, month: int, year: int) -> str:
    return f"{day:02d}-{month:02d}-{year}"


def _safe_move(src: Path, dst: Path) -> Path:
    """Move src → dst, creating parents. If dst exists, append (1), (2), ..."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return dst
    base, suffix = dst.stem, dst.suffix
    parent = dst.parent
    for i in range(1, 100):
        cand = parent / f"{base} ({i}){suffix}"
        if not cand.exists():
            shutil.move(str(src), str(cand))
            return cand
    return src   # gave up - leave file in place


def _pluck_db_dates(conn: sqlite3.Connection, table: str, key_col: str,
                    date_col: str) -> dict:
    """Return {key: 'date_display_string'} from a doc table."""
    out = {}
    try:
        rows = conn.execute(
            f"SELECT {key_col} AS k, {date_col} AS d FROM {table}"
        ).fetchall()
        for r in rows:
            out[r["k"]] = r["d"]
    except sqlite3.Error:
        pass
    return out


def _update_pdf_path(conn: sqlite3.Connection, table: str, key_col: str,
                     key_val: str, new_path: Path) -> None:
    try:
        conn.execute(
            f"UPDATE {table} SET pdf_path = ? WHERE {key_col} = ?",
            (str(new_path), key_val),
        )
    except sqlite3.Error:
        pass


def _file_mtime_date(p: Path) -> tuple[int, int, int]:
    from datetime import datetime as _dt
    ts = p.stat().st_mtime
    d = _dt.fromtimestamp(ts)
    return d.year, d.month, d.day


@migration("2026-06-12_day_type_folders")
def migrate_day_type_folders(conn: sqlite3.Connection) -> str:
    """Reorganize every existing PDF into the new day+type folder layout.

    Idempotent - if a file is already in a DD-MM-YYYY folder, it's skipped.
    """
    month_name_to_num = {v: k for k, v in config.MONTH_FOLDERS.items()}
    day_re = re.compile(r"^\d{2}-\d{2}-\d{4}$")

    def _already_in_day_folder(rel_parts: tuple[str, ...]) -> bool:
        return any(day_re.match(p) for p in rel_parts)

    notes = []

    # ---- QUOTATIONS ---------------------------------------------------------
    root = getattr(config, "QUOTE_ROOT", None)
    if root and Path(root).exists():
        date_by_no = _pluck_db_dates(conn, "quotes", "quote_no", "quote_date")
        moved = 0
        for f in list(Path(root).rglob("QUT-*.pdf")):
            rel_parts = f.relative_to(root).parts
            if _already_in_day_folder(rel_parts):
                continue
            stem = f.stem
            code = _type_code_from_stem(stem)
            type_label = _QUT_TYPE_LABEL.get(code, "Materials")
            # Find the (year, month, day)
            ymd = _parse_date_display(date_by_no.get(stem, ""))
            if not ymd:
                # Fall back to mtime year/month/day
                ymd = _file_mtime_date(f)
            year, month, day = ymd
            month_lbl = config.MONTH_FOLDERS.get(month)
            if not month_lbl:
                continue
            # Client = the segment immediately before the year folder in the
            # OLD layout. If we can't find one, leave the file alone.
            client = None
            year_s = str(year)
            if year_s in rel_parts:
                yi = rel_parts.index(year_s)
                if yi >= 1:
                    client = rel_parts[yi - 1]
            if not client:
                continue
            new_path = (Path(root) / type_label / client / year_s
                        / month_lbl / _day_folder(day, month, year) / f.name)
            if new_path.resolve() == f.resolve():
                continue
            moved_to = _safe_move(f, new_path)
            _update_pdf_path(conn, "quotes", "quote_no", stem, moved_to)
            moved += 1
        if moved:
            notes.append(f"QUT: moved {moved} files")

    # ---- INVOICES -----------------------------------------------------------
    root = getattr(config, "INV_ROOT", None)
    if root and Path(root).exists():
        date_by_no = _pluck_db_dates(conn, "invoices", "inv_no", "inv_date")
        moved = 0
        for f in list(Path(root).rglob("INV-*.pdf")):
            rel_parts = f.relative_to(root).parts
            if _already_in_day_folder(rel_parts):
                continue
            stem = f.stem
            code = _type_code_from_stem(stem)
            type_label = _INV_TYPE_LABEL.get(code, "Materials")
            ymd = _parse_date_display(date_by_no.get(stem, ""))
            if not ymd:
                ymd = _file_mtime_date(f)
            year, month, day = ymd
            month_lbl = config.MONTH_FOLDERS.get(month)
            if not month_lbl:
                continue
            client = None
            year_s = str(year)
            if year_s in rel_parts:
                yi = rel_parts.index(year_s)
                if yi >= 1:
                    client = rel_parts[yi - 1]
            if not client:
                continue
            new_path = (Path(root) / type_label / client / year_s
                        / month_lbl / _day_folder(day, month, year) / f.name)
            if new_path.resolve() == f.resolve():
                continue
            moved_to = _safe_move(f, new_path)
            _update_pdf_path(conn, "invoices", "inv_no", stem, moved_to)
            moved += 1
        if moved:
            notes.append(f"INV: moved {moved} files")

    # ---- DELIVERY NOTES -----------------------------------------------------
    root = getattr(config, "DN_ROOT", None)
    if root and Path(root).exists():
        date_by_no = _pluck_db_dates(conn, "dns", "dn_no", "dn_date")
        moved = 0
        for f in list(Path(root).rglob("*.pdf")):
            # Match any DN-like stem.
            if not any(p.upper() == "DN" for p in f.stem.split("-")):
                continue
            rel_parts = f.relative_to(root).parts
            if _already_in_day_folder(rel_parts):
                continue
            ymd = _parse_date_display(date_by_no.get(f.stem, ""))
            if not ymd:
                ymd = _file_mtime_date(f)
            year, month, day = ymd
            month_lbl = config.MONTH_FOLDERS.get(month)
            if not month_lbl:
                continue
            client = None
            year_s = str(year)
            if year_s in rel_parts:
                yi = rel_parts.index(year_s)
                if yi >= 1:
                    client = rel_parts[yi - 1]
            if not client:
                continue
            new_path = (Path(root) / client / year_s / month_lbl
                        / _day_folder(day, month, year) / f.name)
            if new_path.resolve() == f.resolve():
                continue
            moved_to = _safe_move(f, new_path)
            _update_pdf_path(conn, "dns", "dn_no", f.stem, moved_to)
            moved += 1
        if moved:
            notes.append(f"DN: moved {moved} files")

    # ---- PURCHASE ORDERS ----------------------------------------------------
    root = getattr(config, "PO_ROOT", None)
    if root and Path(root).exists():
        date_by_no = _pluck_db_dates(conn, "pos", "po_no", "po_date")
        moved = 0
        for f in list(Path(root).rglob("*.pdf")):
            stem = f.stem
            # PO filenames: A-NN (A = prefix). Skip anything else.
            if "-" not in stem:
                continue
            rel_parts = f.relative_to(root).parts
            if _already_in_day_folder(rel_parts):
                continue
            ymd = _parse_date_display(date_by_no.get(stem, ""))
            if not ymd:
                ymd = _file_mtime_date(f)
            year, month, day = ymd
            month_lbl = config.MONTH_FOLDERS.get(month)
            if not month_lbl:
                continue
            new_path = (Path(root) / str(year) / month_lbl
                        / _day_folder(day, month, year) / f.name)
            if new_path.resolve() == f.resolve():
                continue
            moved_to = _safe_move(f, new_path)
            _update_pdf_path(conn, "pos", "po_no", stem, moved_to)
            moved += 1
        if moved:
            notes.append(f"PO: moved {moved} files")

    # ---- LETTERS ------------------------------------------------------------
    # Official Letters were retired on 2026-08-27 and `config.LTR_ROOT` no longer
    # exists, so this block is now a permanent no-op. It stays because applied
    # migrations are history: rewriting one changes what a past run claims to
    # have done.
    root = getattr(config, "LTR_ROOT", None)
    if root and Path(root).exists():
        date_by_no = _pluck_db_dates(conn, "letters", "ltr_no", "ltr_date")
        moved = 0
        for f in list(Path(root).rglob("LTR-*.pdf")):
            rel_parts = f.relative_to(root).parts
            if _already_in_day_folder(rel_parts):
                continue
            # Identify lang from the first path segment OR the filename.
            lang_code = "EN"
            if rel_parts and rel_parts[0].upper() in ("EN", "AR"):
                lang_code = rel_parts[0].upper()
            else:
                stem_parts = f.stem.split("-")
                if len(stem_parts) >= 2 and stem_parts[1].upper() in ("EN", "AR"):
                    lang_code = stem_parts[1].upper()
            ymd = _parse_date_display(date_by_no.get(f.stem, ""))
            if not ymd:
                ymd = _file_mtime_date(f)
            year, month, day = ymd
            month_lbl = config.MONTH_FOLDERS.get(month)
            if not month_lbl:
                continue
            new_path = (Path(root) / lang_code / str(year) / month_lbl
                        / _day_folder(day, month, year) / f.name)
            if new_path.resolve() == f.resolve():
                continue
            moved_to = _safe_move(f, new_path)
            _update_pdf_path(conn, "letters", "ltr_no", f.stem, moved_to)
            moved += 1
        if moved:
            notes.append(f"LTR: moved {moved} files")

    # Clean up empty parent directories left behind from the moves.
    for root_attr in ("QUOTE_ROOT", "INV_ROOT", "DN_ROOT", "PO_ROOT", "LTR_ROOT"):
        root = getattr(config, root_attr, None)
        if not root or not Path(root).exists():
            continue
        # Walk bottom-up; remove empty dirs.
        for d in sorted([p for p in Path(root).rglob("*") if p.is_dir()],
                        key=lambda p: -len(p.parts)):
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

    return "; ".join(notes) if notes else "no files needed re-arranging"


@migration("2026-07-24_signed_copies")
def _signed_copies(conn: sqlite3.Connection) -> str:
    """Table for the CLIENT's signed/stamped copy scanned back to us.

    Distinct from our own digital signature (doc_meta key 'signing'). Only DNs
    use it today; `kind` keeps it open to invoices later.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signed_copies (
            kind        TEXT NOT NULL,
            doc_no      TEXT NOT NULL,
            path        TEXT NOT NULL,
            attached_at TEXT NOT NULL,
            PRIMARY KEY (kind, doc_no)
        )
    """)
    return "signed_copies ready"


@migration("2026-07-24_inv_source_dn")
def _inv_source_dn(conn: sqlite3.Connection) -> str:
    """Let an invoice record the delivery note it was created from.

    Until now the DN→INV relationship was only ever *derived* through
    `po_fulfillment`, so an invoice made from a DN with no linked PO recorded
    nothing at all and never showed that DN in its Workflow.
    Mirrors the existing `invoices.source_quote_no` column.
    """
    cols = conn.execute("PRAGMA table_info(invoices)").fetchall()
    if any(r["name"] == "source_dn_no" for r in cols):
        return "invoices.source_dn_no already present"
    conn.execute("ALTER TABLE invoices ADD COLUMN source_dn_no TEXT")
    return "added invoices.source_dn_no"


@migration("2026-07-29_inv_sell_currency")
def _inv_sell_currency(conn: sqlite3.Connection) -> str:
    """Let an invoice be ISSUED in Iraqi Dinar.

    `grand_total` deliberately stays USD-only - analytics, the dashboard revenue
    series and the clients page all reduce it with a plain SQL SUM, which would
    silently mix currencies the moment a dinar figure landed there. The native
    figure the client actually sees lives in sell_total/sell_currency instead.
    Existing rows are USD, so sell_total backfills straight from grand_total.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    added = []
    if "sell_currency" not in cols:
        conn.execute(
            "ALTER TABLE invoices ADD COLUMN sell_currency TEXT NOT NULL DEFAULT 'USD'")
        added.append("sell_currency")
    if "sell_total" not in cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN sell_total REAL NOT NULL DEFAULT 0")
        conn.execute("UPDATE invoices SET sell_total = COALESCE(grand_total, 0)")
        added.append("sell_total")
    if not added:
        return "invoices sell-currency columns already present"
    return "added invoices." + ", invoices.".join(added)


@migration("2026-07-31_client_lpos")
def _client_lpos(conn: sqlite3.Connection) -> str:
    """Store for LPOs RECEIVED from clients (quotation -> LPO -> DN -> invoice).

    The only document in that chain we don't produce, which is why it is a
    received-document store rather than a sixth document type: no numbering, no
    generator, no template. PK is (client_name, lpo_no) because the number comes
    from the CLIENT's sequence - two clients can both send "PO-001".
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_lpos (
            client_name     TEXT NOT NULL COLLATE NOCASE,
            lpo_no          TEXT NOT NULL COLLATE NOCASE,
            lpo_date        TEXT,
            year            INTEGER,
            month           INTEGER,
            total           REAL NOT NULL DEFAULT 0,
            currency        TEXT NOT NULL DEFAULT 'USD',
            total_usd       REAL NOT NULL DEFAULT 0,
            source_quote_no TEXT,
            file_path       TEXT,
            ack_path        TEXT,
            acknowledged_at TEXT,
            notes           TEXT,
            received_at     TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            PRIMARY KEY (client_name, lpo_no)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_lpos_month ON client_lpos(year, month)")
    return "client_lpos ready"


@migration("2026-08-04_inv_sell_peg")
def _inv_sell_peg(conn: sqlite3.Connection) -> str:
    """Record the exchange rate an IQD invoice was actually converted at.

    The rate was previously derived, never stored: `doc_iqd_per_usd` returned
    the profile peg (Abraj 1400) unless the deal was paid in cash or the
    profile was Eshraqat, in which case 1500. But the market rate moves, so the
    figure that was correct when an invoice was issued is not the one a later
    lookup returns - and `invoices.grand_total` (USD, summed by analytics, the
    dashboard and the clients page) is computed from it. Storing the rate makes
    an issued invoice's USD value permanent instead of retroactively floating.

    Deliberately NULLable with no default. NULL means "not recorded", which is
    every invoice issued before this column existed; those keep falling back to
    `doc_iqd_per_usd` so no historical figure moves. Only invoices saved from
    here on carry an explicit rate.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    if "sell_iqd_per_usd" in cols:
        return "invoices.sell_iqd_per_usd already present"
    conn.execute("ALTER TABLE invoices ADD COLUMN sell_iqd_per_usd REAL")
    return "added invoices.sell_iqd_per_usd"


@migration("2026-08-05_doc_cost_rate")
def _doc_cost_rate(conn: sqlite3.Connection) -> str:
    """Let a document pin the exchange rate its COSTS are converted at.

    `doc_iqd_per_usd` derives a rate from rules: the profile peg, or the market
    rate for a cash deal or the Eshraqat profile. But the market moves, and the
    editor shows the number it used ("Costs entered in IQD, converted at
    1 $ = 1,400 IQD") with no way to correct it. Profit for the whole document
    hangs off that figure.

    NULL means "not pinned" - every document that exists today - and keeps the
    derived behaviour, so no historical profit number moves. Only documents
    where the rate is explicitly set carry one.

    Distinct from `invoices.sell_iqd_per_usd`, which is the rate the CLIENT is
    billed at. A deal can legitimately be costed at one rate and billed at
    another.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(doc_cost_meta)").fetchall()}
    if "rate" in cols:
        return "doc_cost_meta.rate already present"
    conn.execute("ALTER TABLE doc_cost_meta ADD COLUMN rate REAL")
    return "added doc_cost_meta.rate"


@migration("2026-08-24_document_references")
def _document_references(conn: sqlite3.Connection) -> str:
    """Add the app-only Reference field to every editable document table."""
    added = []
    for table in ("pos", "quotes", "dns", "invoices"):
        cols = {r["name"] for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        if "reference" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN reference TEXT NOT NULL DEFAULT ''")
            added.append(f"{table}.reference")
    if not added:
        return "document reference columns already present"
    return "added " + ", ".join(added)


@migration("2026-09-05_po_row_mm_is_now_rendered")
def _po_row_mm_is_now_rendered(conn: sqlite3.Connection) -> str:
    """`table_min_row_mm` used to be a paginator-only guess; now it is drawn.

    po_styles.css rendered a hard 26px + 6px padding = 10.32mm per PO row while
    the paginator booked `config.TABLE_MIN_ROW_MM`, which defaulted to 7. The
    setting therefore moved the page breaks and nothing else, and every row was
    under-booked by a third (A-84 planned 27 rows onto a sheet that holds 21).

    The CSS now takes its height FROM the setting, so a stored 7 would suddenly
    be real and shrink every PO row by a third. Anyone still holding the old
    default never chose it - it did nothing - so lift it to the height their POs
    have actually been printing at. A value they picked themselves is left alone.

    Per profile: this runs against each profile's own migration ledger, and
    `config.SETTINGS_PATH` is the active profile's settings.json.
    """
    import json as _json

    OLD_DEFAULT = 7.0
    NEW_DEFAULT = 10.3
    path = getattr(config, "SETTINGS_PATH", None)
    if not path or not Path(path).is_file():
        return "no settings.json for this profile - nothing to lift"
    try:
        data = _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"settings.json unreadable, left alone ({exc})"
    if not isinstance(data, dict) or "table_min_row_mm" not in data:
        return "no stored table_min_row_mm - the new default applies"
    try:
        stored = float(data["table_min_row_mm"])
    except (TypeError, ValueError):
        return "stored table_min_row_mm is not a number - left alone"
    if abs(stored - OLD_DEFAULT) > 0.001:
        return f"table_min_row_mm={stored} was chosen deliberately - left alone"
    data["table_min_row_mm"] = NEW_DEFAULT
    Path(path).write_text(
        _json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"lifted table_min_row_mm {OLD_DEFAULT} -> {NEW_DEFAULT} (it now draws the row)"


@migration("2026-09-05_po_settings_section")
def _po_settings_section(conn: sqlite3.Connection) -> str:
    """Move the PO's settings into their own section, like the other three.

    The PO's keys sat at the TOP LEVEL of settings.json (`po_prefix`,
    `table_min_row_mm`, `company`, `brand_color`, …) while Quotation, Delivery
    and Invoice each owned a named section. That split is why there were two
    save paths with two different merge rules - and why the PO panel, sending a
    whole `company` object through a SHALLOW merge, could drop a key it never
    displayed.

    After this they all look the same: one section per document type, one
    `save_doc_settings`, one deep merge. `doc_settings.read` still falls back to
    the old top-level spelling, so a profile that has not run this yet - or a
    settings.json restored from an older backup - keeps working; this migration
    is what stops the two spellings drifting apart from here on.

    The old keys are LEFT IN PLACE deliberately. `current_effective_settings`
    still publishes them and the App-settings screen still reads some of them;
    removing them would be a second, separate change.
    """
    import json as _json

    path = getattr(config, "SETTINGS_PATH", None)
    if not path or not Path(path).is_file():
        return "no settings.json for this profile - the defaults apply"
    try:
        data = _json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"settings.json unreadable, left alone ({exc})"
    if not isinstance(data, dict):
        return "settings.json is not an object - left alone"

    section = dict(data.get("purchase_order") or {})
    moved = []
    for field in doc_settings.PO_FIELDS:
        if not field.legacy or field.legacy not in data:
            continue
        if not doc_settings.is_missing(doc_settings.dig(section, field.path)):
            continue                      # already in the section - do not clobber
        cursor = section
        for part in field.path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[field.path[-1]] = data[field.legacy]
        moved.append(".".join(field.path))

    if not moved:
        return "purchase_order section already holds everything"
    data["purchase_order"] = section
    Path(path).write_text(
        _json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "moved into purchase_order: " + ", ".join(moved)


@migration("2026-09-11_lpo_kinds_and_lines")
def _lpo_kinds_and_lines(conn: sqlite3.Connection) -> str:
    """Give `client_lpos` a kind, ordered lines, a period and a manual close.

    Every LPO filed before today is a materials PO by definition - kinds did not
    exist, so nobody could have filed a rental or an open contract as one. Lines
    stay NULL rather than empty: "we never recorded what they ordered" is not
    the same statement as "they ordered nothing", and only NULL can say the
    first one.
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(client_lpos)")}
    added = []
    for name, decl in (
            ("kind", "TEXT NOT NULL DEFAULT 'materials'"),
            ("rows_json", "TEXT"),
            ("period_start", "TEXT"),
            ("period_end", "TEXT"),
            ("monthly_amount", "REAL"),
            ("closed_at", "TEXT"),
            ("close_reason", "TEXT"),
            ("erp_no", "TEXT"),
            ("project", "TEXT"),
            ("contact", "TEXT")):
        if name not in existing:
            conn.execute(f"ALTER TABLE client_lpos ADD COLUMN {name} {decl}")
            added.append(name)
    return (f"added {len(added)} columns: {', '.join(added)}"
            if added else "already present")


@migration("2026-09-21_doc_cost_account")
def _doc_cost_account(conn: sqlite3.Connection) -> str:
    """Record WHICH account a document was costed against, and at what rate.

    Separate from `doc_cost_meta.rate`, which is a rate the user pinned by hand
    on the Costing screen and still outranks everything. This pair is written
    automatically on every save, and is what keeps a June quotation on the rate
    its account carried in June after that account changes in July.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(doc_cost_meta)").fetchall()}
    added = []
    if "account_key" not in cols:
        conn.execute("ALTER TABLE doc_cost_meta ADD COLUMN account_key TEXT")
        added.append("account_key")
    if "account_rate" not in cols:
        conn.execute("ALTER TABLE doc_cost_meta ADD COLUMN account_rate REAL")
        added.append("account_rate")
    return ("added doc_cost_meta." + ", ".join(added)) if added else "already present"


@migration("2026-09-21_stamp_document_cost_rates")
def _stamp_document_cost_rates(conn: sqlite3.Connection) -> str:
    """Freeze every existing document at the rate the OLD rules gave it.

    `doc_iqd_per_usd` is about to lose two rules - "Eshraqat is always 1500" and
    "a deal paid in cash is 1500" - in favour of the rate carried by the account
    the client is actually paid into. Every Eshraqat document relies on the
    first and 54 Abraj documents on the second, so deleting them without
    stamping first would silently move real profit figures.

    The old rules are reimplemented HERE rather than imported, because
    `database.py` will not contain them any more. In order:
      1. doc_cost_meta.rate        - pinned by hand; left alone, it still wins
      2. invoices.sell_iqd_per_usd - a rate the invoice recorded
      3. 1500 when the profile is eshraqat
      4. 1500 when the deal was settled in cash
      5. the profile peg: 1500 eshraqat / 1400 abraj

    Rules 2 to 5 are what this writes. A document already stamped is skipped,
    so it is safe to re-run.
    """
    from . import config

    cash = 1500.0
    profile = str(getattr(config, "ACTIVE_PROFILE", "") or "").strip().lower()
    peg = cash if profile == "eshraqat" else float(getattr(config, "IQD_PER_USD", 1400.0))

    # Rule 4 - a cash INVOICE re-pegs its whole deal, so the root quotation and
    # that quote's DNs are cash too. Reproduced with flat queries rather than
    # the deal walk in database.py, which is about to change underneath us.
    cash_quotes: set[str] = set()
    cash_invs: set[str] = set()
    try:
        for row in conn.execute(
                "SELECT inv_no, source_quote_no, payment_bank_key FROM invoices"):
            if str(row["payment_bank_key"] or "").strip().lower() != "cash":
                continue
            cash_invs.add(str(row["inv_no"] or ""))
            root = str(row["source_quote_no"] or "").strip()
            if root:
                cash_quotes.add(root)
    except sqlite3.OperationalError:
        pass

    cash_dns: set[str] = set()
    try:
        for row in conn.execute("SELECT dn_no, source_quote_no FROM dns"):
            if str(row["source_quote_no"] or "").strip() in cash_quotes:
                cash_dns.add(str(row["dn_no"] or ""))
    except sqlite3.OperationalError:
        pass

    # Rule 1 - a rate pinned by hand. It outranks everything and still will,
    # so the stamp must AGREE with it: stamping the profile peg underneath a pin
    # is a figure that only looks right while the pin is there, and drops to
    # something else the day it is cleared. Caught on QUT-EQ-HDEC-048 and
    # QUT-MT-TDC-047, both pinned at 1500 over a 1400 profile.
    pinned: dict = {}
    try:
        for row in conn.execute(
                "SELECT kind, doc_no, rate FROM doc_cost_meta WHERE rate IS NOT NULL"):
            try:
                rate = float(row["rate"] or 0)
            except (TypeError, ValueError):
                continue
            if rate > 0:
                pinned[(str(row["kind"]), str(row["doc_no"]))] = rate
    except sqlite3.OperationalError:
        pass

    def old_rate(kind: str, doc_no: str, sell_peg):
        by_hand = pinned.get((kind, doc_no))
        if by_hand:
            return by_hand
        if sell_peg:
            return sell_peg
        if profile == "eshraqat":
            return cash
        if kind == "inv" and doc_no in cash_invs:
            return cash
        if kind == "quote" and doc_no in cash_quotes:
            return cash
        if kind == "dn" and doc_no in cash_dns:
            return cash
        return peg

    done = {(r["kind"], r["doc_no"]) for r in conn.execute(
        "SELECT kind, doc_no FROM doc_cost_meta WHERE account_rate IS NOT NULL")}

    stamped = 0
    plan = (("quote", "quotes", "quote_no", False),
            ("dn", "dns", "dn_no", False),
            ("inv", "invoices", "inv_no", True))
    for kind, table, col, has_peg in plan:
        cols = f"{col} AS no" + (", sell_iqd_per_usd AS peg" if has_peg else "")
        try:
            rows = conn.execute(f"SELECT {cols} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            doc_no = str(row["no"] or "").strip()
            if not doc_no or (kind, doc_no) in done:
                continue
            sell_peg = None
            if has_peg:
                try:
                    sell_peg = float(row["peg"] or 0) or None
                except (TypeError, ValueError):
                    sell_peg = None
            # account_key is left EMPTY on purpose: these documents predate the
            # idea, and inventing an account for them would be a guess printed
            # on the Costing screen. The RATE is what protects the figure.
            conn.execute(
                "INSERT INTO doc_cost_meta(kind, doc_no, account_key, account_rate) "
                "VALUES(?, ?, '', ?) "
                "ON CONFLICT(kind, doc_no) DO UPDATE SET account_rate = excluded.account_rate "
                "WHERE doc_cost_meta.account_rate IS NULL",
                (kind, doc_no, old_rate(kind, doc_no, sell_peg)),
            )
            stamped += 1
    return f"stamped {stamped} document(s) at their pre-2026-09-21 rate"


@migration("2026-10-02_lpo_invoice_periods")
def _lpo_invoice_periods(conn: sqlite3.Connection) -> str:
    conn.execute("CREATE TABLE IF NOT EXISTS lpo_invoice_periods ("
                 "inv_no TEXT PRIMARY KEY, period_start TEXT NOT NULL, "
                 "period_end TEXT NOT NULL, updated_at TEXT NOT NULL, "
                 "client_name TEXT NOT NULL DEFAULT '', lpo_no TEXT NOT NULL DEFAULT '')")
    columns={r[1] for r in conn.execute("PRAGMA table_info(lpo_invoice_periods)")}
    for field in ("client_name","lpo_no"):
        if field not in columns:
            conn.execute(f"ALTER TABLE lpo_invoice_periods ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE TRIGGER IF NOT EXISTS clear_lpo_period_after_invoice_delete "
                 "AFTER DELETE ON invoices BEGIN DELETE FROM lpo_invoice_periods WHERE inv_no=OLD.inv_no; END")
    return "ensured scoped LPO service-period confirmations and deletion cleanup"
