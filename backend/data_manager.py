"""
Phase I - Data Manager backend.

Central place to browse, lightly maintain, back up, and restore the SQLite
records the app keeps for every document type.

Design rules (locked with the user):
- Delete only removes the DB row. The on-disk PDF is NEVER touched here.
  (The integrity check surfaces "extra PDFs" as INFORMATIONAL, not an error.)
- Backup writes a zip of `data.db` + every document folder (PO/QUT/DN/INV)
  to a folder the user picks via the native dialog.
- Restore extracts a backup zip on top of the current data folders, after
  the user confirms.

The frontend talks to this module through a thin wrapper in `api.py`. Routes
that need to open a document in its normal editor return an `editor_route`
hint so the JS knows which view to push.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import config, profile_manager, shared_library, sqlite_policy


# --------------------------------------------------------------------------- #
# Section registry
# --------------------------------------------------------------------------- #
# Each section describes one logical "tab" the user can browse. Some doc
# types map to a single section (PO, DN), others split by sub-type (QUT/INV
# × materials/equipment/manpower/service).
#
# Fields:
#   key              short id used in the API
#   label            displayed in the sidebar
#   group            high-level grouping ('po' / 'qut' / 'dn' / 'inv')
#   table            SQLite table name
#   pk               primary-key column (the document number)
#   sub_filter       optional SQL fragment + params used to restrict to a
#                    sub-type (e.g. quote_type='materials'). Tuple
#                    (sql_fragment, value) - None when no sub-type.
#   editor_route     identifier the frontend uses to "Open in editor".
#                    Must match a known JS routing key.
#   display_columns  list of (column_key, header, kind). `kind` is one of:
#                       'text'  | 'date' | 'money' | 'count'
#                    `count` is computed from a JSON column (e.g. rows_json
#                    length). Special handling in the row builder.
#   search_fields    list of columns LIKE-searched when the user types in
#                    the search box.
#   date_field       column that represents the document's date (for filters).
#   client_field     column that holds the client/customer name (for client
#                    filter chips). None when not applicable (PO).
#   sub_type_value   the canonical sub-type string ('materials' / 'en' / ...)
#                    used by the chip filter. None for single-section docs.
#   group_label      pretty label for the high-level group.
#   row_count_source key into the row JSON to count line items. None for
#                    sections that don't store a rows_json.

def _section(**kw) -> dict[str, Any]:
    return kw


SECTIONS: list[dict[str, Any]] = [
    # ---------------- Purchase Orders ----------------
    _section(
        key="po", group="po", group_label="Purchase Orders",
        label="Purchase Orders",
        table="pos", pk="po_no",
        sub_filter=None, sub_type_value=None,
        editor_route="po",
        display_columns=[
            ("po_no",      "PO No.",        "text"),
            ("po_date",    "Date",          "date"),
            ("__lines",    "Lines",         "count"),
            ("updated_at", "Updated",       "datetime"),
        ],
        search_fields=["po_no", "notes"],
        date_field="po_date",
        client_field=None,
        row_count_source="rows_json",
    ),
    # ---------------- Quotations (×4 sub-types) ----------------
    *[
        _section(
            key=f"qut_{code}", group="qut", group_label="Quotations",
            label=f"Quotations - {label}",
            table="quotes", pk="quote_no",
            sub_filter=("quote_type = ?", sub),
            sub_type_value=sub,
            editor_route="quote",
            display_columns=[
                ("quote_no",     "Quote No.",  "text"),
                ("quote_date",   "Date",       "date"),
                ("client_name",  "Client",     "text"),
                ("__lines",      "Lines",      "count"),
                ("updated_at",   "Updated",    "datetime"),
            ],
            search_fields=["quote_no", "client_name", "rfq_no"],
            date_field="quote_date",
            client_field="client_name",
            row_count_source="rows_json",
        )
        for (code, sub, label) in [
            ("mat", "materials", "Materials"),
            ("eq",  "equipment", "Equipment"),
            ("mp",  "manpower",  "Manpower"),
            ("sv",  "service",   "Service"),
        ]
    ],
    # ---------------- Delivery Notes ----------------
    _section(
        key="dn", group="dn", group_label="Delivery Notes",
        label="Delivery Notes",
        table="dns", pk="dn_no",
        sub_filter=None, sub_type_value=None,
        editor_route="dn",
        display_columns=[
            ("dn_no",        "DN No.",     "text"),
            ("dn_date",      "Date",       "date"),
            ("client_name",  "Client",     "text"),
            ("__lines",      "Lines",      "count"),
            ("updated_at",   "Updated",    "datetime"),
        ],
        search_fields=["dn_no", "client_name", "lpo_no"],
        date_field="dn_date",
        client_field="client_name",
        row_count_source="rows_json",
    ),
    # ---------------- Invoices (×4 sub-types) ----------------
    *[
        _section(
            key=f"inv_{code}", group="inv", group_label="Invoices",
            label=f"Invoices - {label}",
            table="invoices", pk="inv_no",
            sub_filter=("inv_type = ?", sub),
            sub_type_value=sub,
            editor_route="inv",
            display_columns=[
                ("inv_no",        "INV No.",     "text"),
                ("inv_date",      "Date",        "date"),
                ("client_name",   "Client",      "text"),
                ("grand_total",   "Total",       "money"),
                ("__lines",       "Lines",       "count"),
                ("updated_at",    "Updated",     "datetime"),
            ],
            search_fields=["inv_no", "client_name", "lpo_no"],
            date_field="inv_date",
            client_field="client_name",
            row_count_source="rows_json",
        )
        for (code, sub, label) in [
            ("mat", "materials", "Materials"),
            ("eq",  "equipment", "Equipment"),
            ("mp",  "manpower",  "Manpower"),
            ("sv",  "service",   "Service"),
        ]
    ],
]


def _section_by_key(key: str) -> dict[str, Any] | None:
    for s in SECTIONS:
        if s["key"] == key:
            return s
    return None


# --------------------------------------------------------------------------- #
# Public - section meta
# --------------------------------------------------------------------------- #

def list_sections() -> list[dict[str, Any]]:
    """Slim payload for the sidebar - no SQL filters, just labels."""
    out = []
    for s in SECTIONS:
        out.append({
            "key": s["key"],
            "label": s["label"],
            "group": s["group"],
            "group_label": s["group_label"],
            "sub_type": s["sub_type_value"],
            "editor_route": s["editor_route"],
            "display_columns": [
                {"key": k, "header": h, "kind": kind}
                for (k, h, kind) in s["display_columns"]
            ],
            "has_client_filter": s["client_field"] is not None,
        })
    return out


# --------------------------------------------------------------------------- #
# Browse
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection:
    return sqlite_policy.open_db(config.DB_PATH)


def _connect_shared() -> sqlite3.Connection:
    return shared_library.connect()


def list_records(section_key: str,
                 year: int | None = None,
                 month: int | None = None,
                 client: str | None = None,
                 search: str | None = None,
                 limit: int = 500,
                 offset: int = 0) -> dict[str, Any]:
    """
    Browse one section with optional filters. Returns:
        {
          "rows":   [ {column_key: value, ...}, ...],   # display-shaped
          "total":  N,                                  # before LIMIT/OFFSET
          "section": <section_meta>,
        }
    """
    s = _section_by_key(section_key)
    if not s:
        return {"rows": [], "total": 0, "section": None}

    where_parts: list[str] = []
    params: list[Any] = []

    # Sub-type filter (always-on for this section)
    if s["sub_filter"]:
        frag, val = s["sub_filter"]
        where_parts.append(frag)
        params.append(val)

    # Year/Month
    if year:
        where_parts.append("year = ?")
        params.append(int(year))
    if month:
        where_parts.append("month = ?")
        params.append(int(month))

    # Client
    if client and s["client_field"]:
        where_parts.append(f"{s['client_field']} = ?")
        params.append(client)

    # Search
    if search:
        s_like = f"%{search.strip()}%"
        like_parts = [f"{c} LIKE ?" for c in s["search_fields"]]
        if like_parts:
            where_parts.append("(" + " OR ".join(like_parts) + ")")
            params.extend([s_like] * len(like_parts))

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # Columns to select - only "real" columns + the pk + row JSON if we need
    # to count lines + pdf_path (returned to the frontend for the Open-PDF
    # action).
    real_cols = {k for (k, _h, _kind) in s["display_columns"] if not k.startswith("__")}
    select_cols = real_cols | {s["pk"], "pdf_path"}
    if s["row_count_source"]:
        select_cols.add(s["row_count_source"])
    select_sql = ", ".join(sorted(select_cols))

    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM {s['table']} {where_sql}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT {select_sql} FROM {s['table']} {where_sql} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        ).fetchall()

    out_rows = []
    for r in rows:
        d = dict(r)
        # __lines - compute from JSON column.
        if s["row_count_source"]:
            try:
                d["__lines"] = len(json.loads(d.get(s["row_count_source"]) or "[]"))
            except Exception:
                d["__lines"] = 0
            d.pop(s["row_count_source"], None)
        # Always expose the PK under a known key + the pdf_path.
        d["__pk"] = d.get(s["pk"])
        d["__pdf"] = d.get("pdf_path")
        d["__pdf_exists"] = bool(d["__pdf"] and Path(d["__pdf"]).exists())
        out_rows.append(d)

    return {
        "rows": out_rows,
        "total": total,
        "section": {
            "key": s["key"],
            "label": s["label"],
            "pk": s["pk"],
            "editor_route": s["editor_route"],
            "display_columns": [
                {"key": k, "header": h, "kind": kind}
                for (k, h, kind) in s["display_columns"]
            ],
            "has_client_filter": s["client_field"] is not None,
        },
    }


def list_clients_for_section(section_key: str) -> list[str]:
    """Distinct client names that ever appeared in this section."""
    s = _section_by_key(section_key)
    if not s or not s["client_field"]:
        return []
    where_parts: list[str] = [
        f"{s['client_field']} IS NOT NULL",
        f"TRIM({s['client_field']}) <> ''",
    ]
    params: list[Any] = []
    if s["sub_filter"]:
        frag, val = s["sub_filter"]
        where_parts.append(frag)
        params.append(val)
    where_sql = "WHERE " + " AND ".join(where_parts)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {s['client_field']} AS c FROM {s['table']} {where_sql}",
            params,
        ).fetchall()
    return sorted({(r["c"] or "").strip() for r in rows if r["c"]}, key=lambda x: x.lower())


def list_years_for_section(section_key: str) -> list[int]:
    """Distinct years that ever appeared in this section, descending."""
    s = _section_by_key(section_key)
    if not s:
        return []
    where_parts: list[str] = []
    params: list[Any] = []
    if s["sub_filter"]:
        frag, val = s["sub_filter"]
        where_parts.append(frag)
        params.append(val)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT year AS y FROM {s['table']} {where_sql} "
            f"ORDER BY year DESC", params,
        ).fetchall()
    return [int(r["y"]) for r in rows if r["y"] is not None]


# --------------------------------------------------------------------------- #
# Row maintenance - delete-row / relink-pdf
# --------------------------------------------------------------------------- #

def delete_record(section_key: str, pk_value: str) -> dict[str, Any]:
    """
    Remove ONE row from the DB. The PDF on disk is intentionally NOT touched
    (locked decision - user said the Data Manager should never delete PDFs).
    """
    s = _section_by_key(section_key)
    if not s:
        return {"ok": False, "error": f"unknown section: {section_key}"}
    pk_value = (pk_value or "").strip()
    if not pk_value:
        return {"ok": False, "error": "missing primary key"}
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM {s['table']} WHERE {s['pk']} = ?", (pk_value,)
        )
        deleted = cur.rowcount
    return {"ok": True, "deleted": deleted, "kept_pdf": True}


def delete_records_bulk(section_key: str, pks: list[str]) -> dict[str, Any]:
    """Bulk version of delete_record. PDFs are NOT touched."""
    s = _section_by_key(section_key)
    if not s:
        return {"ok": False, "error": f"unknown section: {section_key}"}
    pks = [str(p).strip() for p in (pks or []) if str(p).strip()]
    if not pks:
        return {"ok": True, "deleted": 0, "kept_pdf": True}
    placeholders = ",".join("?" * len(pks))
    with _connect() as conn:
        cur = conn.execute(
            f"DELETE FROM {s['table']} WHERE {s['pk']} IN ({placeholders})",
            pks,
        )
        deleted = cur.rowcount
    return {"ok": True, "deleted": deleted, "kept_pdf": True}


def relink_pdf(section_key: str, pk_value: str, new_pdf_path: str) -> dict[str, Any]:
    """Point a row's pdf_path at a new file on disk (after the user moved it)."""
    s = _section_by_key(section_key)
    if not s:
        return {"ok": False, "error": f"unknown section: {section_key}"}
    if not new_pdf_path or not Path(new_pdf_path).exists():
        return {"ok": False, "error": "new pdf path does not exist"}
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE {s['table']} SET pdf_path = ?, "
            f"updated_at = ? WHERE {s['pk']} = ?",
            (str(new_pdf_path),
             datetime.now().isoformat(timespec="seconds"),
             pk_value),
        )
    return {"ok": True, "updated": cur.rowcount}


# --------------------------------------------------------------------------- #
# Backup / Restore
# --------------------------------------------------------------------------- #

# Document folders this app no longer writes to. A backup taken before the type
# was retired still carries the folder; restore skips it instead of refusing.
_RETIRED_DOC_ROOTS = {"LETTERS"}      # Official Letters, retired 2026-08-27


def _doc_roots() -> list[Path]:
    """All document folders the app writes to. Skips folders that don't exist."""
    candidates: list[Path] = [
        getattr(config, "PO_ROOT", None),
        getattr(config, "QUOTE_ROOT", None),
        getattr(config, "DN_ROOT", None),
        getattr(config, "INV_ROOT", None),
    ]
    return [Path(p) for p in candidates if p and Path(p).exists()]


def _safe_archive_name() -> str:
    return (
        f"documents-hub-{config.ACTIVE_PROFILE}-backup-"
        f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.zip"
    )


def _zip_db_snapshot(zf: zipfile.ZipFile, db_path: Path, arcname: str, archive: Path) -> None:
    """Zip a CONSISTENT copy of a live database (multi-user, 2026-10-22).

    The databases run in WAL mode: a database is the .db file PLUS its -wal
    file, and zipping the .db alone loses the newest saves. SQLite's backup
    API reads both."""
    snap = archive.with_name(archive.stem + "." + Path(arcname).stem + ".snapshot.db")
    try:
        sqlite_policy.snapshot(db_path, snap)
        zf.write(snap, arcname=arcname)
    finally:
        snap.unlink(missing_ok=True)


def create_backup(target_folder: str) -> dict[str, Any]:
    """
    Zip the active profile, shared catalog, and generated documents.
    Returns the archive path on success.
    """
    target = Path(target_folder) if target_folder else None
    if not target or not target.exists() or not target.is_dir():
        return {"ok": False, "error": "Target folder is missing or not a directory."}

    archive = target / _safe_archive_name()
    files_added = 0

    # Compute one logical root for every path inside the zip so restore can
    # find pieces unambiguously. Each top-level item gets a stable label:
    #   db/data.db
    #   docs/<root-folder-name>/...
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            manifest = {
                "version": 2,
                "profile_id": config.ACTIVE_PROFILE,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "documents_root": str(config.DOCUMENTS_ROOT),
                "document_roots": [root.name for root in _doc_roots()],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            files_added += 1

            db_path = Path(config.DB_PATH)
            if db_path.exists():
                _zip_db_snapshot(zf, db_path, "profile/po_tool.db", archive)
                files_added += 1
            settings_path = getattr(config, "SETTINGS_PATH", None)
            if settings_path and Path(settings_path).exists():
                zf.write(Path(settings_path), arcname="profile/settings.json")
                files_added += 1

            assets_dir = Path(config.ASSETS_DIR)
            if assets_dir.exists():
                for asset in assets_dir.rglob("*"):
                    if asset.is_file():
                        rel = asset.relative_to(assets_dir).as_posix()
                        zf.write(asset, arcname=f"profile/assets/{rel}")
                        files_added += 1

            shared_db = profile_manager.shared_db_path()
            if shared_db.is_file():
                _zip_db_snapshot(zf, shared_db, "shared/library.db", archive)
                files_added += 1

            for root in _doc_roots():
                root_label = root.name or "DOCS"
                for f in root.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(root).as_posix()
                        zf.write(f, arcname=f"docs/{root_label}/{rel}")
                        files_added += 1
    except Exception as e:
        # Clean up partial archive on failure.
        try:
            if archive.exists(): archive.unlink()
        except OSError:
            pass
        return {"ok": False, "error": f"Backup failed: {e}"}

    return {"ok": True, "path": str(archive), "files": files_added,
            "size_bytes": archive.stat().st_size}


def restore_backup(archive_path: str, replace_docs: bool = False) -> dict[str, Any]:
    """
    Restore only a version-2 backup belonging to the active company profile.
    """
    if not archive_path or not Path(archive_path).exists():
        return {"ok": False, "error": "Archive path does not exist."}
    archive = Path(archive_path)

    tmp = config.PROFILE_DIR.parent / f".restore-{uuid.uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                member = PurePosixPath(info.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"Unsafe archive path: {info.filename}")
            try:
                manifest = json.loads(zf.read("manifest.json"))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("Backup manifest is missing or unreadable") from exc
            if not isinstance(manifest, dict) or manifest.get("version") != 2:
                raise ValueError("Unsupported backup format")
            backup_profile = str(manifest.get("profile_id") or "")
            if backup_profile != config.ACTIVE_PROFILE:
                raise ValueError(
                    f"This is a {backup_profile or 'different'} company backup; "
                    f"switch to {backup_profile or 'that profile'} before restoring it"
                )
            zf.extractall(tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": f"Could not read archive: {e}"}

    staged_db = tmp / "profile" / "po_tool.db"
    staged_shared = tmp / "shared" / "library.db"
    staged_settings = tmp / "profile" / "settings.json"
    try:
        for staged, label in ((staged_db, "profile"), (staged_shared, "shared")):
            if not staged.is_file():
                continue
            with sqlite_policy.open_db(staged, wal=False, row_factory=False) as conn:
                check = conn.execute("PRAGMA quick_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise ValueError(f"The {label} database failed integrity validation")
        if staged_settings.is_file():
            payload = json.loads(staged_settings.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Profile settings must contain a JSON object")
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": f"Backup validation failed: {exc}"}

    restored = {
        "db": False, "shared_db": False, "settings": False,
        "assets": False, "docs_dirs": 0, "doc_files": 0,
    }

    def replace_file(staged: Path, live: Path) -> bool:
        if not staged.is_file():
            return False
        live.parent.mkdir(parents=True, exist_ok=True)
        if live.exists():
            shutil.copy2(live, live.with_suffix(live.suffix + ".before-restore.bak"))
        pending = live.with_suffix(live.suffix + f".{uuid.uuid4().hex}.restore")
        shutil.copy2(staged, pending)
        pending.replace(live)
        return True

    def replace_database(staged: Path, live: Path) -> bool:
        if not staged.is_file():
            return False
        live.parent.mkdir(parents=True, exist_ok=True)
        if live.exists():
            shutil.copy2(live, live.with_suffix(live.suffix + ".before-restore.bak"))
        source = sqlite_policy.open_db(staged, wal=False, row_factory=False)
        target = sqlite_policy.open_db(live, row_factory=False)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        return True

    try:
        restored["db"] = replace_database(staged_db, Path(config.DB_PATH))
        restored["shared_db"] = replace_database(staged_shared, profile_manager.shared_db_path())
        restored["settings"] = replace_file(staged_settings, Path(config.SETTINGS_PATH))

        staged_assets = tmp / "profile" / "assets"
        if staged_assets.is_dir():
            live_assets = Path(config.ASSETS_DIR)
            backup_assets = live_assets.with_name(f"{live_assets.name}.before-restore")
            if backup_assets.exists():
                shutil.rmtree(backup_assets)
            if live_assets.exists():
                live_assets.replace(backup_assets)
            try:
                shutil.copytree(staged_assets, live_assets)
            except Exception:
                if live_assets.exists():
                    shutil.rmtree(live_assets, ignore_errors=True)
                if backup_assets.exists():
                    backup_assets.replace(live_assets)
                raise
            restored["assets"] = True

        staged_docs = tmp / "docs"
        if staged_docs.exists():
            name_to_root: dict[str, Path] = {}
            for root in [getattr(config, k, None) for k in
                         ("PO_ROOT", "QUOTE_ROOT", "DN_ROOT", "INV_ROOT")]:
                if root: name_to_root[Path(root).name] = Path(root)

            for staged_root in staged_docs.iterdir():
                if not staged_root.is_dir(): continue
                # Official Letters were retired on 2026-08-27. A backup taken
                # before that still carries the folder; skip it rather than
                # refusing the whole restore.
                if staged_root.name in _RETIRED_DOC_ROOTS:
                    continue
                live_root = name_to_root.get(staged_root.name)
                if not live_root:
                    raise ValueError(f"Unknown document root in backup: {staged_root.name}")
                if replace_docs and live_root.exists():
                    shutil.rmtree(live_root, ignore_errors=True)
                live_root.mkdir(parents=True, exist_ok=True)
                restored["docs_dirs"] += 1
                for f in staged_root.rglob("*"):
                    if not f.is_file(): continue
                    rel = f.relative_to(staged_root)
                    dest = live_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    restored["doc_files"] += 1
    except Exception as exc:
        return {"ok": False, "error": f"Restore failed: {exc}", "restored": restored}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"ok": True, "restored": restored}


# --------------------------------------------------------------------------- #
# Helpers used by integrity (Phase I.2) and stats (Phase I.4). Kept here so
# everything section-aware lives in one module - the API just calls in.
# --------------------------------------------------------------------------- #

def all_section_keys() -> list[str]:
    return [s["key"] for s in SECTIONS]


def section_label(key: str) -> str:
    s = _section_by_key(key)
    return s["label"] if s else key


# =========================================================================== #
# PHASE I.5 - Autofill catalog management
# --------------------------------------------------------------------------- #
# The user's real ask: manage the *suggestion catalogs* - the tables that
# feed autocomplete in every editor.
#
# Each catalog section describes:
#   key             short id used in the API
#   label           sidebar label
#   group/group_label  high-level grouping in the sidebar
#   table           SQLite table name
#   name_col        the "primary" text column shown in the first column
#                   (e.g. 'name' for items, 'value' for column_memory)
#   primary_header  optional override for the name column's header
#   pk_cols         composite primary-key columns. Used by edit/delete/merge.
#   variant_cols    additional columns to display + edit. For tables that
#                   store the variant in `data_json`, these keys are
#                   unpacked from the JSON. For explicit-column tables
#                   they're real SQL columns.
#                   Each entry: {"key", "header", "kind": "text|money"}.
#   data_json_col   name of the JSON column, or None for explicit columns.
#   sub_filter      (sql_fragment, value) - pinned filter for sub-typed
#                   catalogs (quote_items by quote_type, etc).
#   fixed_values    dict of column→value applied when (re)inserting rows
#                   after a merge/edit - keeps the sub-type pinned.
# =========================================================================== #

def _cat(**kw) -> dict[str, Any]:
    return kw


CATALOG_SECTIONS: list[dict[str, Any]] = [
    # 2026-09-05 - TEN sections were removed from this list, and every one of
    # them was visible in the Data Manager and threw the moment it was opened:
    #
    #   cat_po_items                      -> table `items`
    #   cat_qut_{mat,eq,mp,sv}_items      -> table `quote_items`
    #   cat_dn_items                      -> table `dn_items`
    #   cat_inv_{mat,eq,mp,sv}_items      -> table `inv_items`
    #
    # Those four catalogs were physically DROPPED on 2026-08-20 (see
    # AGENT-BOARD and [[03-BACKEND-DATA]]); the item catalogue is
    # `master_items` now, with the Master Data screen in front of it. The
    # sections were left behind, so the screen listed 13 entries of which 10
    # answered `no such table` - a dead menu the user could not tell from a
    # live one until they clicked it.
    #
    # The removal was not noticed for two weeks because the ONE test that
    # covered it (`tests/profile_data_isolation_backend.test.py`) was itself
    # failing on the same missing table and had been written off as
    # pre-existing noise. A failing test is a report, not a nuisance.
    #
    # If an item catalogue ever comes back, it goes through `master_items` -
    # do NOT re-add a section pointing at a dropped table.
    # ---------------- PO ----------------
    _cat(
        key="cat_companies", label="Companies (PO)",
        group="po", group_label="Purchase Orders",
        table="companies", name_col="name",
        pk_cols=["name"], variant_cols=[],
        data_json_col=None, sub_filter=None, fixed_values={},
    ),
    # ---------------- Shared ----------------
    _cat(
        key="cat_clients", label="Clients (QUT / DN / INV)",
        group="shared", group_label="Shared catalogs",
        table="clients", name_col="name",
        pk_cols=["name"], variant_cols=[],
        data_json_col=None, sub_filter=None, fixed_values={},
    ),
    # ---------------- Universal column memory ----------------
    _cat(
        key="cat_column_memory", label="Universal Column Memory",
        group="shared", group_label="Shared catalogs",
        table="column_memory", name_col="value",
        primary_header="Value",
        pk_cols=["doc_type", "sub_type", "col_key", "value"],
        variant_cols=[
            {"key": "doc_type", "header": "Doc",     "kind": "text"},
            {"key": "sub_type", "header": "Sub-type","kind": "text"},
            {"key": "col_key",  "header": "Column",  "kind": "text"},
        ],
        data_json_col=None, sub_filter=None, fixed_values={},
    ),
]


def _catalog_by_key(key: str) -> dict[str, Any] | None:
    for c in CATALOG_SECTIONS:
        if c["key"] == key:
            return c
    return None


def _sig_ignore() -> set:
    """The keys that are NOT part of an item's identity - imported rather than
    copied, because a fourth divergent copy of this set is exactly how the two
    signature implementations came to disagree in the first place. Imported
    lazily: `database` imports config, which must not be pulled in at module
    import time here."""
    from . import database
    return database._SIG_IGNORE


def _data_sig(data: dict[str, Any]) -> str:
    """
    Re-implementation of database._data_sig so we can recompute it locally
    on edits/merges without coupling to that module's private helper.
    Same algorithm: lower-cased keys, stripped string values, sha1[:16].

    2026-08-13 - it was NOT the same algorithm. This copy hashed every key
    while `database._data_sig` skips the volatile ones (`_SIG_IGNORE`: qty,
    rate, cost, cost_usd, image_refs …). So an item edited in Master Data got a
    signature computed by one rule and re-saved from an editor under another,
    which re-splits the row it was supposed to unify.
    """
    import hashlib
    skip = _sig_ignore()
    norm: dict[str, str] = {}
    for k, v in (data or {}).items():
        if v is None: continue
        if str(k).strip().lower() in skip: continue
        s = str(v).strip()
        if not s: continue
        norm[str(k).strip().lower()] = s
    if not norm:
        return "empty"
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Catalog metadata
# --------------------------------------------------------------------------- #

def list_catalogs() -> list[dict[str, Any]]:
    """Sidebar payload - every browsable catalog + its display columns."""
    out = []
    for c in CATALOG_SECTIONS:
        display_columns = [
            {"key": c["name_col"],
             "header": c.get("primary_header", "Name"),
             "kind": "text"},
            *c["variant_cols"],
            {"key": "use_count",    "header": "Used",      "kind": "count"},
            {"key": "last_used_at", "header": "Last used", "kind": "datetime"},
        ]
        out.append({
            "key": c["key"],
            "label": c["label"],
            "group": c["group"],
            "group_label": c["group_label"],
            "display_columns": display_columns,
            "name_col": c["name_col"],
            "variant_cols": list(c["variant_cols"]),
            "pk_cols": list(c["pk_cols"]),
            "has_json_data": c["data_json_col"] is not None,
        })
    return out


# --------------------------------------------------------------------------- #
# Browse one catalog
# --------------------------------------------------------------------------- #

def list_catalog_records(section_key: str,
                         search: str | None = None,
                         limit: int = 500,
                         offset: int = 0) -> dict[str, Any]:
    c = _catalog_by_key(section_key)
    if not c:
        return {"rows": [], "total": 0, "section": None}

    # Columns to SELECT: PKs + name + variant_cols (if explicit) + data_json
    # (if any) + counters.
    select_cols: set[str] = set(c["pk_cols"]) | {c["name_col"], "use_count", "last_used_at"}
    if c["data_json_col"]:
        select_cols.add(c["data_json_col"])
    else:
        for v in c["variant_cols"]:
            select_cols.add(v["key"])
    select_sql = ", ".join(sorted(select_cols))

    where_parts: list[str] = []
    params: list[Any] = []
    if c["sub_filter"]:
        frag, val = c["sub_filter"]
        where_parts.append(frag); params.append(val)
    if search:
        like = f"%{search.strip()}%"
        # Search the name column + any text variant columns (explicit only -
        # data_json text search is unreliable, so we rely on the name).
        sc = [c["name_col"]]
        if not c["data_json_col"]:
            for v in c["variant_cols"]:
                if v["kind"] == "text":
                    sc.append(v["key"])
        like_parts = [f"{col} LIKE ?" for col in sc]
        where_parts.append("(" + " OR ".join(like_parts) + ")")
        params.extend([like] * len(like_parts))

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with _connect_shared() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM {c['table']} {where_sql}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT {select_sql} FROM {c['table']} {where_sql} "
            f"ORDER BY use_count DESC, last_used_at DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        ).fetchall()

    out_rows = []
    for r in rows:
        d = dict(r)
        # Unpack data_json variant fields, if any.
        if c["data_json_col"]:
            try:
                data = json.loads(d.get(c["data_json_col"]) or "{}")
            except Exception:
                data = {}
            for v in c["variant_cols"]:
                if v["key"] not in d:        # only fill if not already a column
                    d[v["key"]] = data.get(v["key"], "")
            d.pop(c["data_json_col"], None)
        # Composite PK for the frontend - dict so the JS can pass it back.
        d["__pk"] = {pk: d.get(pk) for pk in c["pk_cols"]}
        d["__name"] = d.get(c["name_col"])
        out_rows.append(d)

    display_columns = [
        {"key": c["name_col"],
         "header": c.get("primary_header", "Name"),
         "kind": "text"},
        *c["variant_cols"],
        {"key": "use_count",    "header": "Used",      "kind": "count"},
        {"key": "last_used_at", "header": "Last used", "kind": "datetime"},
    ]
    return {
        "rows": out_rows,
        "total": total,
        "section": {
            "key": c["key"], "label": c["label"],
            "display_columns": display_columns,
            "name_col": c["name_col"],
            "variant_cols": list(c["variant_cols"]),
            "pk_cols": list(c["pk_cols"]),
            "has_json_data": c["data_json_col"] is not None,
        },
    }


# --------------------------------------------------------------------------- #
# Edit / Delete one row
# --------------------------------------------------------------------------- #

def _pk_where(pk_cols: list[str], pk_dict: dict[str, Any]) -> tuple[str, list[Any]]:
    parts, params = [], []
    for c in pk_cols:
        v = pk_dict.get(c)
        if v is None:
            parts.append(f"{c} IS NULL")
        else:
            parts.append(f"{c} = ?")
            params.append(v)
    return " AND ".join(parts), params


def delete_catalog_entry(section_key: str, pk_dict: dict[str, Any]) -> dict[str, Any]:
    if section_key == "cat_clients":
        return {"ok": False, "error": "Use Clients Master Data to archive or restore a client."}
    c = _catalog_by_key(section_key)
    if not c:
        return {"ok": False, "error": f"unknown catalog: {section_key}"}
    where, params = _pk_where(c["pk_cols"], pk_dict or {})
    with _connect_shared() as conn:
        cur = conn.execute(f"DELETE FROM {c['table']} WHERE {where}", params)
    return {"ok": True, "deleted": cur.rowcount}


def delete_catalog_entries(section_key: str,
                           pk_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    if section_key == "cat_clients":
        return {"ok": False, "error": "Use Clients Master Data to archive or restore clients."}
    c = _catalog_by_key(section_key)
    if not c:
        return {"ok": False, "error": f"unknown catalog: {section_key}"}
    deleted = 0
    with _connect_shared() as conn:
        for pd in (pk_dicts or []):
            where, params = _pk_where(c["pk_cols"], pd or {})
            cur = conn.execute(f"DELETE FROM {c['table']} WHERE {where}", params)
            deleted += cur.rowcount
    return {"ok": True, "deleted": deleted}


def _row_data_for_sig(c: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """
    For multi-variant tables (data_sig in PK), build the dict that goes into
    the sig hash. The original save code uses the variant fields except the
    primary name (and except image fields, which catalogs never store).
    """
    out = {}
    for v in c["variant_cols"]:
        if v["key"] in values:
            out[v["key"]] = values[v["key"]]
    # 2026-08-13 - this dict is written to `data_json` as well as hashed, so
    # keeping ONLY the declared columns silently destroyed everything the
    # columns cannot hold: the item's remembered `cost_usd`, its picture
    # (`image_refs`) and its display currency were wiped by any edit here,
    # including one that only changed the unit. Those keys are all in
    # `_SIG_IGNORE`, so carrying them through leaves the signature - half the
    # row's primary key - exactly where it was.
    structural = {c["name_col"], "data_sig", "use_count", "last_used_at"}
    structural.update((c.get("fixed_values") or {}).keys())
    for k, val in (values or {}).items():
        if k in out or k in structural or val is None:
            continue
        # `data_json` is the complete material record, not merely the fields
        # this catalog happens to display.  Keep every supplied key here; the
        # signature function below still decides which volatile keys are not
        # identity.  Limiting this to `_SIG_IGNORE` used to silently discard
        # canonical custom fields during a catalog merge.
        out[k] = val
    return out


def _upsert_row(conn: sqlite3.Connection, c: dict[str, Any],
                values: dict[str, Any],
                extra_use_count: int = 0,
                last_used_at: str | None = None) -> None:
    """
    Insert one catalog row, or sum onto an existing one with the same PK.
    `values` must contain the name column + every variant column the user
    wants set. Missing columns get '' or NULL. For data_sig tables, the
    sig is recomputed from the variant fields.
    """
    table = c["table"]
    now = last_used_at or datetime.now().isoformat(timespec="seconds")
    fixed = dict(c.get("fixed_values") or {})
    name_val = (values.get(c["name_col"]) or "").strip()
    if not name_val:
        # Refuse to insert empty-name rows - they'd never be useful for
        # autocomplete anyway.
        return

    # Build the row dict that will go in the INSERT.
    row: dict[str, Any] = {c["name_col"]: name_val}
    row.update(fixed)
    if c["data_json_col"]:
        data = _row_data_for_sig(c, values)
        row["data_sig"] = _data_sig(data)
        row[c["data_json_col"]] = json.dumps(data, ensure_ascii=False)
    else:
        # Explicit-column tables - pull each declared variant col straight in.
        for v in c["variant_cols"]:
            row[v["key"]] = values.get(v["key"], "") or ""
        # `items` has a data_sig PK part too. Compute it from the explicit
        # variant cols so multi-variant memory stays consistent.
        if "data_sig" in c["pk_cols"]:
            sig_input = {v["key"]: row[v["key"]] for v in c["variant_cols"]}
            row["data_sig"] = _data_sig(sig_input)
    row["use_count"]    = max(1, int(extra_use_count or 1))
    row["last_used_at"] = now

    # column_memory is the only "non-counter-friendly" table where use_count
    # isn't NULLable but every row already has the same structure - handled
    # by the generic path.

    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    pk_list = ", ".join(c["pk_cols"])
    # On conflict (same PK), sum use_counts + refresh last_used_at + update
    # the variant fields. This is what makes merge truly merge.
    updates = []
    for col in cols:
        if col in c["pk_cols"]: continue
        if col == "use_count":
            updates.append(f"use_count = use_count + excluded.use_count")
        elif col == "last_used_at":
            updates.append(f"last_used_at = excluded.last_used_at")
        else:
            updates.append(f"{col} = excluded.{col}")
    update_sql = ", ".join(updates) if updates else "use_count = use_count"
    conn.execute(
        f"INSERT INTO {table}({col_list}) VALUES({placeholders}) "
        f"ON CONFLICT({pk_list}) DO UPDATE SET {update_sql}",
        [row[c2] for c2 in cols],
    )


def update_catalog_entry(section_key: str,
                         old_pk_dict: dict[str, Any],
                         new_values: dict[str, Any]) -> dict[str, Any]:
    """
    Replace one catalog row with a new (name + variants) combo, preserving
    its use_count and last_used_at. If the new row's PK collides with an
    existing one, the two are merged (use_counts summed).
    """
    if section_key == "cat_clients":
        from . import clients
        return clients.rename_client((old_pk_dict or {}).get("name", ""), (new_values or {}).get("name", ""))
    c = _catalog_by_key(section_key)
    if not c:
        return {"ok": False, "error": f"unknown catalog: {section_key}"}
    if not new_values:
        return {"ok": False, "error": "no values to update"}
    where, params = _pk_where(c["pk_cols"], old_pk_dict or {})
    with _connect_shared() as conn:
        row = conn.execute(
            f"SELECT * FROM {c['table']} WHERE {where} LIMIT 1", params,
        ).fetchone()
        if not row:
            return {"ok": False, "error": "old row not found"}
        old = dict(row)
        carried_use_count = int(old.get("use_count") or 1)
        # Delete the old row so the upsert's PK doesn't collide with itself.
        conn.execute(f"DELETE FROM {c['table']} WHERE {where}", params)
        # Insert the new row (or sum into a colliding one).
        _upsert_row(conn, c, new_values, extra_use_count=carried_use_count)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Duplicate detection + merge
# --------------------------------------------------------------------------- #

def _normalize_name(s: str) -> str:
    s = (s or "").lower().strip()
    # collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # drop common punctuation; keep digits + letters
    s = re.sub(r"[‏‎]", "", s)         # bidi marks
    s = re.sub(r"[,.;:_/\\()\[\]{}\"']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _similarity(a: str, b: str) -> float:
    if not a or not b: return 0.0
    if a == b: return 1.0
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_catalog_duplicates(section_key: str,
                            threshold: float = 0.85) -> dict[str, Any]:
    """
    Group entries by similar normalized name. Returns clusters with ≥2
    variants, sorted by total use_count desc.

    Each cluster:
      {
        "anchor": "<normalized canonical>",
        "variants": [
           {"pk": {...}, "name": "<raw>", "fields": {...variant_cols...},
            "use_count": N, "last_used_at": "...", "similarity": 1.0},
           ...
        ],
        "total_use_count": N,
      }

    Match algorithm: greedy single-link clustering, anchored by most-used
    variant first.
    """
    c = _catalog_by_key(section_key)
    if not c:
        return {"ok": False, "error": f"unknown catalog: {section_key}", "clusters": []}
    threshold = max(0.5, min(1.0, float(threshold or 0.85)))

    payload = list_catalog_records(section_key, search=None, limit=5000, offset=0)
    rows = payload["rows"]
    if not rows:
        return {"ok": True, "clusters": [], "threshold": threshold}

    # Sort by use_count desc so anchors are popular variants.
    rows.sort(key=lambda r: int(r.get("use_count") or 0), reverse=True)

    clusters: list[dict[str, Any]] = []
    for r in rows:
        name = str(r.get(c["name_col"]) or "")
        nname = _normalize_name(name)
        if not nname:
            continue
        placed = False
        for cl in clusters:
            sim = _similarity(nname, cl["anchor"])
            if sim >= threshold:
                cl["variants"].append({
                    "pk": r.get("__pk"),
                    "name": name,
                    "fields": {v["key"]: r.get(v["key"], "") for v in c["variant_cols"]},
                    "use_count": int(r.get("use_count") or 0),
                    "last_used_at": r.get("last_used_at") or "",
                    "similarity": round(sim, 3),
                })
                cl["total_use_count"] += int(r.get("use_count") or 0)
                placed = True
                break
        if not placed:
            clusters.append({
                "anchor": nname,
                "variants": [{
                    "pk": r.get("__pk"),
                    "name": name,
                    "fields": {v["key"]: r.get(v["key"], "") for v in c["variant_cols"]},
                    "use_count": int(r.get("use_count") or 0),
                    "last_used_at": r.get("last_used_at") or "",
                    "similarity": 1.0,
                }],
                "total_use_count": int(r.get("use_count") or 0),
            })

    # Drop singleton clusters - they're not duplicates.
    clusters = [cl for cl in clusters if len(cl["variants"]) >= 2]
    # Sort by aggregate usage so the most impactful groups show first.
    clusters.sort(key=lambda cl: cl["total_use_count"], reverse=True)

    return {"ok": True, "clusters": clusters, "threshold": threshold,
            "count": len(clusters)}


def _merge_catalog_entries_in_connection(conn: sqlite3.Connection,
                                         c: dict[str, Any],
                                         canonical_values: dict[str, Any],
                                         absorbed_pks: list[dict[str, Any]]) -> dict[str, Any]:
    """The transaction-aware half of :func:`merge_catalog_entries`.

    Keeping this separate lets a caller combine several independent catalog
    merges in one SQLite transaction without opening nested connections.
    """
    total_absorbed_count = 0
    latest_used = None
    # Read every absorbed row's use_count and last_used_at (max).
    for pk in (absorbed_pks or []):
        where, params = _pk_where(c["pk_cols"], pk or {})
        row = conn.execute(
            f"SELECT use_count, last_used_at FROM {c['table']} WHERE {where} LIMIT 1",
            params,
        ).fetchone()
        if not row:
            continue
        total_absorbed_count += int(row["use_count"] or 0)
        if row["last_used_at"]:
            if latest_used is None or row["last_used_at"] > latest_used:
                latest_used = row["last_used_at"]
        conn.execute(f"DELETE FROM {c['table']} WHERE {where}", params)
    # Upsert canonical with the summed use_count + most-recent last_used_at.
    _upsert_row(
        conn, c, canonical_values,
        extra_use_count=max(1, total_absorbed_count),
        last_used_at=latest_used,
    )
    return {"ok": True, "absorbed_use_count": total_absorbed_count,
            "absorbed_rows": len(absorbed_pks or [])}


def merge_catalog_entries(section_key: str,
                          canonical_values: dict[str, Any],
                          absorbed_pks: list[dict[str, Any]],
                          connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    """
    Catalog-only merge (user's chosen policy - saved documents are not touched).

    `canonical_values`: {name_col, variant fields...} that the merged result
        should have. This row is upserted; its use_count becomes the sum of
        every absorbed row + 1 (if it didn't already exist).
    `absorbed_pks`: list of pk_dicts for the rows being merged in. Their
        use_counts are summed onto the canonical row, then they're deleted.
    """
    if section_key == "cat_clients":
        return {"ok": False, "error": "Manage alternative names in Clients Master Data. Saved documents are preserved."}
    c = _catalog_by_key(section_key)
    if not c:
        return {"ok": False, "error": f"unknown catalog: {section_key}"}
    if not canonical_values:
        return {"ok": False, "error": "missing canonical row values"}

    if connection is not None:
        return _merge_catalog_entries_in_connection(
            connection, c, canonical_values, absorbed_pks)
    with _connect_shared() as conn:
        return _merge_catalog_entries_in_connection(conn, c, canonical_values, absorbed_pks)


def all_catalog_keys() -> list[str]:
    return [c["key"] for c in CATALOG_SECTIONS]
