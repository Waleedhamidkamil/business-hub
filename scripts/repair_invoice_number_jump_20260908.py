"""Audited repair for the Abraj invoice jump from 047 to 902.

Default mode creates a restore point, regenerates corrected PDFs, and rehearses
the SQLite changes on copies. Use --apply to publish the same verified repair.
The utility is intentionally specific to the six confirmed invoices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = APP_ROOT / "data"
PROFILE_ROOT = DATA_ROOT / "profiles"
ABRAJ_DB = PROFILE_ROOT / "abraj" / "po_tool.db"
ESHRAQAT_DB = PROFILE_ROOT / "eshraqat" / "po_tool.db"
REGISTRY = DATA_ROOT / "profile-registry.json"

INVOICE_MAPPING = (
    ("INV-MT-TDC-902", "INV-MT-TDC-048"),
    ("INV-MT-JUNE CONSTRUCTION CO.-903", "INV-MT-JUNE CONSTRUCTION CO.-049"),
    ("INV-MT-TDC-904", "INV-MT-TDC-050"),
    ("INV-MT-TDC-905", "INV-MT-TDC-051"),
    ("INV-MT-TDC-906", "INV-MT-TDC-052"),
    ("INV-MT-TDC-907", "INV-MT-TDC-053"),
)

CONFIRMED_TEST_TOMBSTONES = {
    "abraj": (
        ("inv", "INV-MT-SIGNEDCOPY-901"),
        ("dn", "DN-__SIGNEDCOPY__-001"),
    ),
    "eshraqat": (
        ("inv", "IV-MT-SIGNEDCOPY-901"),
        ("dn", "DN-__SIGNEDCOPY__-001"),
    ),
}

EXPECTED_ABRAJ_PRECONDITIONS = {
    "invoices": 6,
    "submitted": 6,
    "paid": 0,
    "signed_copies": 0,
    "attention_ignores": 0,
    "cost_meta": 0,
    "columns": 6,
    "doc_meta": 12,
    "po_links": 5,
    "po_link_rows": 40,
    "test_tombstones": 2,
}
EXPECTED_ESHRAQAT_TEST_TOMBSTONES = 2

_ABRAJ_INV_NUMBER = re.compile(r"^INV-(?:MT|EQ|MP|SV)-.+-(\d+)$", re.I)


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _count(conn: sqlite3.Connection, sql: str, params: Iterable[object] = ()) -> int:
    return int(conn.execute(sql, tuple(params)).fetchone()[0])


def _assert_no_target_key(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    new_number: str,
    qualifier: tuple[str, str] | None = None,
) -> None:
    if not _table_exists(conn, table):
        return
    where = f"{_qi(column)}=?"
    params: list[object] = [new_number]
    if qualifier:
        where += f" AND {_qi(qualifier[0])}=?"
        params.append(qualifier[1])
    if _count(conn, f"SELECT COUNT(*) FROM {_qi(table)} WHERE {where}", params):
        raise RuntimeError(f"Target number already exists in {table}: {new_number}")


def remove_confirmed_test_tombstones(
    conn: sqlite3.Connection, profile_id: str
) -> int:
    """Remove only the signed-copy test rows proven to have leaked.

    A matching row with a non-test path is refused rather than deleted. This
    makes the exact document number insufficient authorization on its own.
    """
    if not _table_exists(conn, "deleted_docs"):
        return 0
    removed = 0
    for kind, doc_no in CONFIRMED_TEST_TOMBSTONES.get(profile_id, ()):
        row = conn.execute(
            "SELECT pdf_path,recycle_path FROM deleted_docs WHERE kind=? AND doc_no=?",
            (kind, doc_no),
        ).fetchone()
        if not row:
            continue
        path_text = " ".join(str(value or "") for value in row).replace("/", "\\").casefold()
        if ".test-work" not in path_text:
            raise RuntimeError(
                f"Refusing to remove unverified tombstone {profile_id}/{kind}/{doc_no}"
            )
        removed += conn.execute(
            "DELETE FROM deleted_docs WHERE kind=? AND doc_no=?", (kind, doc_no)
        ).rowcount
    return removed


def audit_repair_preconditions(
    conn: sqlite3.Connection,
    mapping: dict[str, str],
    expected: dict[str, int] | None = None,
) -> dict[str, int]:
    """Count every live reference and optionally refuse any baseline drift."""
    old_numbers = tuple(mapping)
    placeholders = ",".join("?" for _ in old_numbers)

    def scoped(table: str, column: str, qualifier: tuple[str, str] | None = None) -> int:
        if not _table_exists(conn, table):
            return 0
        where = f"{_qi(column)} IN ({placeholders})"
        params: list[object] = list(old_numbers)
        if qualifier:
            where += f" AND {_qi(qualifier[0])}=?"
            params.append(qualifier[1])
        return _count(conn, f"SELECT COUNT(*) FROM {_qi(table)} WHERE {where}", params)

    test_tombstones = 0
    if _table_exists(conn, "deleted_docs"):
        for kind, doc_no in CONFIRMED_TEST_TOMBSTONES["abraj"]:
            row = conn.execute(
                "SELECT pdf_path,recycle_path FROM deleted_docs WHERE kind=? AND doc_no=?",
                (kind, doc_no),
            ).fetchone()
            if not row:
                continue
            path_text = " ".join(str(value or "") for value in row).replace("/", "\\").casefold()
            if ".test-work" not in path_text:
                raise RuntimeError(
                    f"Repair precondition failed: unverified tombstone abraj/{kind}/{doc_no}"
                )
            test_tombstones += 1

    counts = {
        "invoices": scoped("invoices", "inv_no"),
        "submitted": scoped("submitted_docs", "doc_no", ("kind", "inv")),
        "paid": scoped("paid_invoices", "inv_no"),
        "signed_copies": scoped("signed_copies", "doc_no", ("kind", "inv")),
        "attention_ignores": scoped("attention_ignores", "doc_no", ("kind", "inv")),
        "cost_meta": scoped("doc_cost_meta", "doc_no", ("kind", "inv")),
        "columns": scoped("doc_columns", "doc_no", ("kind", "inv")),
        "doc_meta": scoped("doc_meta", "doc_no", ("doc_type", "inv")),
        "po_links": 0,
        "po_link_rows": scoped("po_fulfillment", "source_no", ("source_kind", "inv")),
        "test_tombstones": test_tombstones,
    }
    if _table_exists(conn, "po_fulfillment"):
        counts["po_links"] = _count(
            conn,
            f"SELECT COUNT(DISTINCT po_no) FROM po_fulfillment "
            f"WHERE source_no IN ({placeholders}) AND source_kind='inv'",
            old_numbers,
        )
    if expected is not None and counts != expected:
        raise RuntimeError(
            "Repair precondition counts changed: "
            f"expected {expected}, found {counts}"
        )
    return counts


def audit_test_tombstones(
    conn: sqlite3.Connection, profile_id: str, expected: int
) -> int:
    """Verify the exact fake rows and their test paths without changing data."""
    found = 0
    if _table_exists(conn, "deleted_docs"):
        for kind, doc_no in CONFIRMED_TEST_TOMBSTONES.get(profile_id, ()):
            row = conn.execute(
                "SELECT pdf_path,recycle_path FROM deleted_docs WHERE kind=? AND doc_no=?",
                (kind, doc_no),
            ).fetchone()
            if not row:
                continue
            path_text = " ".join(str(value or "") for value in row).replace("/", "\\").casefold()
            if ".test-work" not in path_text:
                raise RuntimeError(
                    f"Repair precondition failed: unverified tombstone {profile_id}/{kind}/{doc_no}"
                )
            found += 1
    if found != expected:
        raise RuntimeError(
            f"Repair precondition counts changed for {profile_id}: "
            f"expected {expected} test tombstones, found {found}"
        )
    return found


def _update_scoped_key(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    old_number: str,
    new_number: str,
    qualifier: tuple[str, str] | None = None,
) -> int:
    if not _table_exists(conn, table):
        return 0
    where = f"{_qi(key_column)}=?"
    params: list[object] = [new_number, old_number]
    if qualifier:
        where += f" AND {_qi(qualifier[0])}=?"
        params.append(qualifier[1])
    return conn.execute(
        f"UPDATE {_qi(table)} SET {_qi(key_column)}=? WHERE {where}", params
    ).rowcount


def rekey_abraj_database(
    conn: sqlite3.Connection,
    mapping: dict[str, str],
    new_pdf_paths: dict[str, str],
) -> dict[str, int]:
    """Re-key the six invoices and all known invoice-number satellites.

    The caller owns the transaction. Created and updated timestamps are not
    touched. Submitted rows are deliberately deleted, per the user's request.
    """
    if set(mapping) != set(new_pdf_paths):
        raise RuntimeError("Every source invoice needs one corrected PDF path")
    if len(set(mapping.values())) != len(mapping):
        raise RuntimeError("Corrected invoice numbers are not unique")

    for old, new in mapping.items():
        if _count(conn, "SELECT COUNT(*) FROM invoices WHERE inv_no=?", (old,)) != 1:
            raise RuntimeError(f"Expected exactly one source invoice: {old}")
        if _count(conn, "SELECT COUNT(*) FROM invoices WHERE inv_no=?", (new,)):
            raise RuntimeError(f"Corrected invoice already exists: {new}")
        for table, column, qualifier in (
            ("paid_invoices", "inv_no", None),
            ("signed_copies", "doc_no", ("kind", "inv")),
            ("attention_ignores", "doc_no", ("kind", "inv")),
            ("doc_cost_meta", "doc_no", ("kind", "inv")),
            ("doc_columns", "doc_no", ("kind", "inv")),
            ("doc_meta", "doc_no", ("doc_type", "inv")),
        ):
            _assert_no_target_key(conn, table, column, new, qualifier)

    counts = {
        "invoices": 0,
        "submitted_removed": 0,
        "paid": 0,
        "signed_copies": 0,
        "attention_ignores": 0,
        "cost_meta": 0,
        "columns": 0,
        "doc_meta": 0,
        "po_links": 0,
        "po_link_rows": 0,
        "test_tombstones_removed": 0,
    }
    linked_pos: set[str] = set()

    for old, new in mapping.items():
        if _table_exists(conn, "submitted_docs"):
            counts["submitted_removed"] += conn.execute(
                "DELETE FROM submitted_docs WHERE kind='inv' AND doc_no=?", (old,)
            ).rowcount
        counts["paid"] += _update_scoped_key(
            conn, "paid_invoices", "inv_no", old, new
        )

        if _table_exists(conn, "signed_copies"):
            copies = conn.execute(
                "SELECT path FROM signed_copies WHERE kind='inv' AND doc_no=?", (old,)
            ).fetchall()
            for (path,) in copies:
                corrected_path = str(path or "").replace(old, new)
                conn.execute(
                    "UPDATE signed_copies SET doc_no=?,path=? "
                    "WHERE kind='inv' AND doc_no=? AND path=?",
                    (new, corrected_path, old, path),
                )
                counts["signed_copies"] += 1

        for table, label in (
            ("attention_ignores", "attention_ignores"),
            ("doc_cost_meta", "cost_meta"),
            ("doc_columns", "columns"),
        ):
            counts[label] += _update_scoped_key(
                conn, table, "doc_no", old, new, ("kind", "inv")
            )
        counts["doc_meta"] += _update_scoped_key(
            conn, "doc_meta", "doc_no", old, new, ("doc_type", "inv")
        )

        if _table_exists(conn, "po_fulfillment"):
            linked_pos.update(
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT po_no FROM po_fulfillment "
                    "WHERE source_kind='inv' AND source_no=?",
                    (old,),
                )
            )
            counts["po_link_rows"] += conn.execute(
                "UPDATE po_fulfillment SET source_no=? "
                "WHERE source_kind='inv' AND source_no=?",
                (new, old),
            ).rowcount

        counts["invoices"] += conn.execute(
            "UPDATE invoices SET inv_no=?,pdf_path=? WHERE inv_no=?",
            (new, new_pdf_paths[old], old),
        ).rowcount

    counts["po_links"] = len(linked_pos)
    counts["test_tombstones_removed"] = remove_confirmed_test_tombstones(conn, "abraj")
    return counts


def next_abraj_invoice_number(conn: sqlite3.Connection) -> int:
    maximum = 0
    if _table_exists(conn, "invoices"):
        for (number,) in conn.execute("SELECT inv_no FROM invoices"):
            match = _ABRAJ_INV_NUMBER.fullmatch(str(number or ""))
            if match:
                maximum = max(maximum, int(match.group(1)))
    if _table_exists(conn, "deleted_docs"):
        for (number,) in conn.execute(
            "SELECT doc_no FROM deleted_docs WHERE kind='inv'"
        ):
            match = _ABRAJ_INV_NUMBER.fullmatch(str(number or ""))
            if match:
                maximum = max(maximum, int(match.group(1)))
    return maximum + 1


def _fingerprint(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name, sql in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        rows = sorted(repr(tuple(row)) for row in conn.execute(f"SELECT * FROM {_qi(name)}"))
        digest = hashlib.sha256(
            (str(sql) + "\n" + "\n".join(rows)).encode("utf-8")
        ).hexdigest()
        result[name] = {"rows": len(rows), "sha256": digest}
    return result


def _read_fingerprint(path: Path) -> dict[str, dict[str, object]]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return _fingerprint(conn)


def _backup_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"Database is unavailable: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def _restore_database(backup: Path, live: Path) -> None:
    """Restore one SQLite database and prove it matches the restore point."""
    if not backup.is_file() or not live.is_file():
        raise RuntimeError(f"Cannot restore database: {backup} -> {live}")
    with closing(sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(live.resolve().as_uri() + "?mode=rw", uri=True)) as dst:
            src.backup(dst)
    if _read_fingerprint(live) != _read_fingerprint(backup):
        raise RuntimeError(f"Database restore verification failed: {live}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_invoice_rows(db_path: Path, mapping: dict[str, str]) -> dict[str, dict]:
    records: dict[str, dict] = {}
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for old, new in mapping.items():
            row = conn.execute("SELECT * FROM invoices WHERE inv_no=?", (old,)).fetchone()
            if not row:
                raise RuntimeError(f"Source invoice is missing: {old}")
            record = dict(row)
            pdf_path = Path(record.get("pdf_path") or "")
            if not pdf_path.is_file():
                raise RuntimeError(f"Source invoice PDF is missing: {pdf_path}")
            new_path = pdf_path.with_name(f"{new}.pdf")
            if new_path.exists():
                raise RuntimeError(f"Corrected invoice PDF already exists: {new_path}")
            columns = conn.execute(
                "SELECT columns_json FROM doc_columns WHERE kind='inv' AND doc_no=?",
                (old,),
            ).fetchone()
            metadata = {
                key: value
                for key, value in conn.execute(
                    "SELECT key,value FROM doc_meta WHERE doc_type='inv' AND doc_no=?",
                    (old,),
                )
            }
            record["rows"] = json.loads(record.pop("rows_json") or "[]")
            record["columns"] = json.loads(columns[0]) if columns and columns[0] else None
            record["layout"] = json.loads(metadata["layout"]) if metadata.get("layout") else None
            record["signing_overlay"] = (
                json.loads(metadata["signing"]) if metadata.get("signing") else None
            )
            record["old_pdf"] = str(pdf_path)
            record["new_pdf"] = str(new_path)
            records[old] = record
    return records


def _invoice_payload(record: dict, new_number: str) -> dict:
    extra_costs = record.get("extra_costs")
    if extra_costs is None:
        extra_costs = json.loads(record.get("extra_costs_json") or "[]")
    payload = {
        "inv_no": new_number,
        "date": record.get("inv_date"),
        "inv_type": record.get("inv_type") or "materials",
        "client_name": record.get("client_name") or "",
        "lpo_no": record.get("lpo_no") or "",
        "client_address": record.get("client_address") or "",
        "payment_bank_key": record.get("payment_bank_key") or "",
        "withholding_included": bool(record.get("withholding_included")),
        "sell_currency": record.get("sell_currency") or "USD",
        "sell_iqd_per_usd": record.get("sell_iqd_per_usd"),
        "items": record.get("rows") or [],
        "extra_costs": extra_costs,
        "source_quote_no": record.get("source_quote_no"),
        "source_dn_no": record.get("source_dn_no"),
        "reference": record.get("reference") or "",
    }
    for key in ("columns", "layout", "signing_overlay"):
        if record.get(key) is not None:
            payload[key] = record[key]
    return payload


def _validate_pdf(path: Path, new_number: str, old_number: str) -> dict[str, object]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not reader.pages:
        raise RuntimeError(f"Corrected PDF has no pages: {path}")
    if new_number not in text:
        raise RuntimeError(f"Corrected number is not visible in PDF: {new_number}")
    if old_number in text:
        raise RuntimeError(f"Old number remains visible in corrected PDF: {old_number}")
    return {"pages": len(reader.pages), "sha256": _sha256(path)}


def _render_corrected_pdfs(
    records: dict[str, dict], mapping: dict[str, str], output: Path
) -> list[dict[str, object]]:
    sys.path.insert(0, str(APP_ROOT))
    from backend import api, inv_generator

    output.mkdir(parents=True, exist_ok=False)
    rendered: list[dict[str, object]] = []
    for old, new in mapping.items():
        payload = _invoice_payload(records[old], new)
        payload["_dn_docs"] = api._resolve_dn_docs(payload)
        destination = output / f"{new}.pdf"
        inv_generator.render_inv_pdf(api._printable_document(payload), destination)
        result = _validate_pdf(destination, new, old)
        rendered.append({
            "old": old,
            "new": new,
            "source": records[old]["old_pdf"],
            "target": records[old]["new_pdf"],
            **result,
        })
    return rendered


def _verify_rekeyed_database(
    conn: sqlite3.Connection, mapping: dict[str, str]
) -> None:
    old_numbers = tuple(mapping)
    new_numbers = tuple(mapping.values())
    placeholders = ",".join("?" for _ in mapping)
    if _count(conn, f"SELECT COUNT(*) FROM invoices WHERE inv_no IN ({placeholders})", old_numbers):
        raise RuntimeError("Old invoice numbers remain in the invoices table")
    if _count(conn, f"SELECT COUNT(*) FROM invoices WHERE inv_no IN ({placeholders})", new_numbers) != len(mapping):
        raise RuntimeError("Not every corrected invoice exists")
    if _count(
        conn,
        f"SELECT COUNT(*) FROM submitted_docs WHERE kind='inv' "
        f"AND doc_no IN ({placeholders})",
        new_numbers,
    ):
        raise RuntimeError("A corrected invoice is still marked Submitted")
    for table, column, qualifier in (
        ("paid_invoices", "inv_no", None),
        ("signed_copies", "doc_no", ("kind", "inv")),
        ("attention_ignores", "doc_no", ("kind", "inv")),
        ("doc_cost_meta", "doc_no", ("kind", "inv")),
        ("doc_columns", "doc_no", ("kind", "inv")),
        ("doc_meta", "doc_no", ("doc_type", "inv")),
        ("po_fulfillment", "source_no", ("source_kind", "inv")),
    ):
        if not _table_exists(conn, table):
            continue
        where = f"{_qi(column)} IN ({placeholders})"
        params: list[object] = list(old_numbers)
        if qualifier:
            where += f" AND {_qi(qualifier[0])}=?"
            params.append(qualifier[1])
        if _count(conn, f"SELECT COUNT(*) FROM {_qi(table)} WHERE {where}", params):
            raise RuntimeError(f"Old invoice references remain in {table}")
    if next_abraj_invoice_number(conn) != 54:
        raise RuntimeError(
            f"Repaired database next invoice is {next_abraj_invoice_number(conn):03d}, expected 054"
        )
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity check failed after invoice repair")


def _signed_copy_moves(
    db_path: Path, mapping: dict[str, str], backup_root: Path
) -> list[dict[str, str]]:
    moves: list[dict[str, str]] = []
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not _table_exists(conn, "signed_copies"):
            return moves
        for old, new in mapping.items():
            for (raw_path,) in conn.execute(
                "SELECT path FROM signed_copies WHERE kind='inv' AND doc_no=?", (old,)
            ):
                source = Path(raw_path)
                if not source.is_file():
                    raise RuntimeError(f"Signed copy is missing: {source}")
                target = Path(str(source).replace(old, new))
                if target.exists():
                    raise RuntimeError(f"Corrected signed-copy path already exists: {target}")
                backup = backup_root / source.name
                shutil.copy2(source, backup)
                moves.append({"source": str(source), "target": str(target), "backup": str(backup)})
    return moves


def _install_files(
    records: dict[str, dict],
    mapping: dict[str, str],
    staged_dir: Path,
    signed_moves: list[dict[str, str]],
    installed: list[tuple[Path, Path]],
) -> list[tuple[Path, Path]]:
    for old, new in mapping.items():
        source = Path(records[old]["old_pdf"])
        target = Path(records[old]["new_pdf"])
        staged = staged_dir / f"{new}.pdf"
        partial = target.with_suffix(target.suffix + ".repairing")
        shutil.copy2(staged, partial)
        os.replace(partial, target)
        installed.append((source, target))
        source.unlink()
    for item in signed_moves:
        source, target = Path(item["source"]), Path(item["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        installed.append((source, target))
    return installed


def _restore_files(
    installed: list[tuple[Path, Path]], original_dir: Path, signed_moves: list[dict[str, str]]
) -> None:
    signed_sources = {Path(item["source"]): Path(item["backup"]) for item in signed_moves}
    errors: list[str] = []
    for source, target in reversed(installed):
        try:
            if target.exists():
                target.unlink()
            backup = signed_sources.get(source, original_dir / source.name)
            if not backup.is_file():
                raise FileNotFoundError(f"restore copy is missing: {backup}")
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, source)
            if not source.is_file() or target.exists():
                raise RuntimeError(f"file restore verification failed: {source}")
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    if errors:
        raise RuntimeError("File rollback failed: " + "; ".join(errors))


def install_files_with_rollback(
    records: dict[str, dict],
    mapping: dict[str, str],
    staged_dir: Path,
    signed_moves: list[dict[str, str]],
    original_dir: Path,
) -> list[tuple[Path, Path]]:
    """Install staged files and restore every earlier move on partial failure."""
    installed: list[tuple[Path, Path]] = []
    try:
        return _install_files(records, mapping, staged_dir, signed_moves, installed)
    except Exception as publish_error:
        rollback_error = None
        try:
            _restore_files(installed, original_dir, signed_moves)
        except Exception as exc:
            rollback_error = exc
        for old in mapping:
            target = Path(records[old]["new_pdf"])
            partial = target.with_suffix(target.suffix + ".repairing")
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
        if rollback_error:
            raise RuntimeError(
                f"File publish failed: {publish_error}; {rollback_error}"
            ) from publish_error
        raise


def _recover_published_state(
    installed: list[tuple[Path, Path]],
    original_dir: Path,
    signed_moves: list[dict[str, str]],
    database_pairs: Iterable[tuple[Path, Path]],
) -> list[str]:
    """Restore files and databases, returning every unresolved recovery error."""
    errors: list[str] = []
    try:
        _restore_files(installed, original_dir, signed_moves)
    except Exception as exc:
        errors.append(str(exc))
    for backup, live in database_pairs:
        try:
            _restore_database(backup, live)
        except Exception as exc:
            errors.append(str(exc))
    return errors


def _publish(
    output: Path,
    records: dict[str, dict],
    mapping: dict[str, str],
    before_abraj: dict,
    before_eshraqat: dict,
    signed_moves: list[dict[str, str]],
    abraj_backup: Path,
    eshraqat_backup: Path,
    abraj_db_path: Path = ABRAJ_DB,
    eshraqat_db_path: Path = ESHRAQAT_DB,
) -> tuple[dict[str, object], list[tuple[Path, Path]]]:
    new_paths = {old: records[old]["new_pdf"] for old in mapping}
    installed: list[tuple[Path, Path]] = []
    abraj = sqlite3.connect(abraj_db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
    eshraqat = sqlite3.connect(eshraqat_db_path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
    result: dict[str, object] | None = None
    publish_error: Exception | None = None
    try:
        abraj.execute("BEGIN IMMEDIATE")
        eshraqat.execute("BEGIN IMMEDIATE")
        if _fingerprint(abraj) != before_abraj:
            raise RuntimeError("Abraj data changed after the restore point was created")
        if _fingerprint(eshraqat) != before_eshraqat:
            raise RuntimeError("Eshraqat data changed after the restore point was created")

        audit_repair_preconditions(abraj, mapping, EXPECTED_ABRAJ_PRECONDITIONS)
        audit_test_tombstones(
            eshraqat, "eshraqat", EXPECTED_ESHRAQAT_TEST_TOMBSTONES
        )

        counts = rekey_abraj_database(abraj, mapping, new_paths)
        eshraqat_removed = remove_confirmed_test_tombstones(eshraqat, "eshraqat")
        _verify_rekeyed_database(abraj, mapping)
        if eshraqat.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Eshraqat SQLite integrity check failed")

        installed = install_files_with_rollback(
            records,
            mapping,
            output / "corrected-pdfs",
            signed_moves,
            output / "original-pdfs",
        )
        # Cross-database atomic commit is not available here. Both restore-point
        # databases are restored below if either commit or later work fails.
        eshraqat.commit()
        abraj.commit()
        result = {
            **counts,
            "eshraqat_test_tombstones_removed": eshraqat_removed,
            "files_installed": len(installed),
        }
    except Exception as exc:
        publish_error = exc
        try:
            abraj.rollback()
        except sqlite3.Error:
            pass
        try:
            eshraqat.rollback()
        except sqlite3.Error:
            pass
    finally:
        abraj.close()
        eshraqat.close()

    if publish_error is not None:
        recovery_errors = _recover_published_state(
            installed,
            output / "original-pdfs",
            signed_moves,
            ((abraj_backup, abraj_db_path), (eshraqat_backup, eshraqat_db_path)),
        )
        if recovery_errors:
            raise RuntimeError(
                f"Repair publish failed: {publish_error}; recovery failed: "
                + "; ".join(recovery_errors)
            ) from publish_error
        raise publish_error
    if result is None:
        raise RuntimeError("Repair publish ended without a result")
    return result, installed


def _verify_live(
    records: dict[str, dict], mapping: dict[str, str]
) -> dict[str, object]:
    pdfs = []
    with closing(sqlite3.connect(ABRAJ_DB.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        _verify_rekeyed_database(conn, mapping)
        for old, new in mapping.items():
            if Path(records[old]["old_pdf"]).exists():
                raise RuntimeError(f"Old invoice PDF still exists: {old}")
            target = Path(records[old]["new_pdf"])
            if not target.is_file():
                raise RuntimeError(f"Corrected invoice PDF is missing: {new}")
            pdfs.append({"number": new, **_validate_pdf(target, new, old)})

    sys.path.insert(0, str(APP_ROOT))
    from backend import config, inv_numbering

    if config.ACTIVE_PROFILE != "abraj":
        raise RuntimeError(f"Active profile changed during repair: {config.ACTIVE_PROFILE}")
    next_number = inv_numbering.get_next_inv_number("HDEC", "materials")[0]
    if next_number != "INV-MT-HDEC-054":
        raise RuntimeError(f"Live next invoice is {next_number}, expected INV-MT-HDEC-054")
    return {"next_invoice": next_number, "pdfs": pdfs}


def run(apply: bool = False) -> tuple[Path, dict[str, object]]:
    registry_before = REGISTRY.read_bytes()
    registry = json.loads(registry_before)
    if registry.get("active_profile") != "abraj":
        raise RuntimeError("Activate the Abraj profile before running this repair")

    mapping = dict(INVOICE_MAPPING)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = DATA_ROOT / "document-backups" / f"{stamp}-invoice-number-repair"
    output.mkdir(parents=True, exist_ok=False)

    abraj_backup = output / "abraj.before.db"
    eshraqat_backup = output / "eshraqat.before.db"
    _backup_database(ABRAJ_DB, abraj_backup)
    _backup_database(ESHRAQAT_DB, eshraqat_backup)
    before_abraj = _read_fingerprint(abraj_backup)
    before_eshraqat = _read_fingerprint(eshraqat_backup)

    with closing(sqlite3.connect(abraj_backup.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        audited_abraj = audit_repair_preconditions(
            conn, mapping, EXPECTED_ABRAJ_PRECONDITIONS
        )
    with closing(sqlite3.connect(eshraqat_backup.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        audited_eshraqat = audit_test_tombstones(
            conn, "eshraqat", EXPECTED_ESHRAQAT_TEST_TOMBSTONES
        )

    records = _load_invoice_rows(abraj_backup, mapping)
    originals = output / "original-pdfs"
    originals.mkdir()
    for old in mapping:
        source = Path(records[old]["old_pdf"])
        shutil.copy2(source, originals / source.name)
    signed_dir = output / "original-signed-copies"
    signed_dir.mkdir()
    signed_moves = _signed_copy_moves(abraj_backup, mapping, signed_dir)

    rendered = _render_corrected_pdfs(records, mapping, output / "corrected-pdfs")

    rehearsal_abraj = output / "abraj.rehearsal.db"
    rehearsal_eshraqat = output / "eshraqat.rehearsal.db"
    shutil.copy2(abraj_backup, rehearsal_abraj)
    shutil.copy2(eshraqat_backup, rehearsal_eshraqat)
    new_paths = {old: records[old]["new_pdf"] for old in mapping}
    with closing(sqlite3.connect(rehearsal_abraj)) as conn:
        audit_repair_preconditions(conn, mapping, EXPECTED_ABRAJ_PRECONDITIONS)
        rehearsal_counts = rekey_abraj_database(conn, mapping, new_paths)
        _verify_rekeyed_database(conn, mapping)
        conn.commit()
    with closing(sqlite3.connect(rehearsal_eshraqat)) as conn:
        audit_test_tombstones(
            conn, "eshraqat", EXPECTED_ESHRAQAT_TEST_TOMBSTONES
        )
        rehearsal_eshraqat_removed = remove_confirmed_test_tombstones(conn, "eshraqat")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Eshraqat rehearsal integrity check failed")
        conn.commit()

    report: dict[str, object] = {
        "repair": "Abraj invoice 902-907 to 048-053",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "backup": str(output),
        "mapping": mapping,
        "audited_preconditions": {
            "abraj": audited_abraj,
            "eshraqat_test_tombstones": audited_eshraqat,
        },
        "rehearsal": {
            **rehearsal_counts,
            "eshraqat_test_tombstones_removed": rehearsal_eshraqat_removed,
            "next_invoice": "INV-MT-HDEC-054",
        },
        "rendered": rendered,
        "applied": False,
    }

    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if apply:
        installed: list[tuple[Path, Path]] = []
        try:
            if REGISTRY.read_bytes() != registry_before:
                raise RuntimeError("Active profile registry changed during rehearsal")
            published, installed = _publish(
                output,
                records,
                mapping,
                before_abraj,
                before_eshraqat,
                signed_moves,
                abraj_backup,
                eshraqat_backup,
            )
            report["published"] = published
            report["verification"] = _verify_live(records, mapping)
            report["applied"] = True
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as repair_error:
            recovery_errors = _recover_published_state(
                installed,
                originals,
                signed_moves,
                ((abraj_backup, ABRAJ_DB), (eshraqat_backup, ESHRAQAT_DB)),
            )
            report["failure"] = str(repair_error)
            report["restored"] = not recovery_errors
            if recovery_errors:
                report["recovery_errors"] = recovery_errors
            try:
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                pass
            if recovery_errors:
                raise RuntimeError(
                    f"Repair failed: {repair_error}; recovery failed: "
                    + "; ".join(recovery_errors)
                ) from repair_error
            raise

    return output, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Publish the rehearsed repair")
    args = parser.parse_args()
    output, report = run(apply=args.apply)
    summary = {
        "backup": str(output),
        "applied": report["applied"],
        "mapping": report["mapping"],
        "rehearsal": report["rehearsal"],
        "published": report.get("published"),
        "next_invoice": (report.get("verification") or {}).get("next_invoice"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
