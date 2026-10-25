"""Regression contract for the 2026-09-08 Abraj invoice-number repair.

This suite uses temporary databases only. It proves that re-keying the six
affected invoices reaches every table that stores an invoice number, clears
Submitted status, removes only confirmed test tombstones, and leaves the
correct next number at 054.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "scripts" / "repair_invoice_number_jump_20260908.py"


def load_repair_module():
    if not SCRIPT.is_file():
        raise AssertionError("repair utility is missing")
    spec = importlib.util.spec_from_file_location("invoice_number_repair", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("repair utility could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA = """
CREATE TABLE invoices (
    inv_no TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, inv_date TEXT,
    inv_type TEXT, year INTEGER, month INTEGER, client_name TEXT, lpo_no TEXT,
    client_address TEXT, payment_bank_key TEXT, withholding_included INTEGER,
    grand_total REAL, sell_currency TEXT, sell_total REAL,
    sell_iqd_per_usd REAL, pdf_path TEXT, rows_json TEXT,
    source_quote_no TEXT, extra_costs_json TEXT, source_dn_no TEXT,
    reference TEXT
);
CREATE TABLE submitted_docs (
    kind TEXT, doc_no TEXT, submitted_at TEXT, PRIMARY KEY(kind, doc_no)
);
CREATE TABLE paid_invoices (inv_no TEXT PRIMARY KEY, paid_at TEXT);
CREATE TABLE signed_copies (
    kind TEXT, doc_no TEXT, path TEXT, attached_at TEXT,
    PRIMARY KEY(kind, doc_no)
);
CREATE TABLE attention_ignores (
    kind TEXT, doc_no TEXT, category TEXT, ignored_at TEXT,
    PRIMARY KEY(kind, doc_no, category)
);
CREATE TABLE po_fulfillment (
    po_no TEXT, item_index INTEGER, status_kind TEXT, source_kind TEXT,
    source_no TEXT, marked_at TEXT,
    PRIMARY KEY(po_no, item_index, status_kind)
);
CREATE TABLE doc_cost_meta (
    kind TEXT, doc_no TEXT, currency TEXT, rate REAL,
    PRIMARY KEY(kind, doc_no)
);
CREATE TABLE doc_columns (
    kind TEXT, doc_no TEXT, columns_json TEXT,
    PRIMARY KEY(kind, doc_no)
);
CREATE TABLE doc_meta (
    doc_type TEXT, doc_no TEXT, key TEXT, value TEXT,
    PRIMARY KEY(doc_type, doc_no, key)
);
CREATE TABLE deleted_docs (
    kind TEXT, doc_no TEXT, deleted_at TEXT, pdf_path TEXT, recycle_path TEXT,
    PRIMARY KEY(kind, doc_no)
);
"""


def seed_abraj(conn: sqlite3.Connection, root: Path, mapping: dict[str, str]):
    conn.executescript(SCHEMA)
    previous = root / "INV-MT-TDC-047.pdf"
    conn.execute(
        "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,year,month,"
        "client_name,pdf_path,rows_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("INV-MT-TDC-047", "before", "before", "24/8/2026", "materials", 2026, 8,
         "TDC", str(previous), "[]"),
    )
    for index, (old, _new) in enumerate(mapping.items()):
        old_pdf = root / f"{old}.pdf"
        old_pdf.write_bytes(f"original {old}".encode())
        rows_json = json.dumps([{
            "item": f"item-{index}", "description": f"description-{index}",
            "unit": "pcs", "qty": index + 1, "rate": 10.5 + index,
        }])
        extra_costs_json = json.dumps([{"label": f"freight-{index}", "amount": index + 3.5}])
        conn.execute(
            "INSERT INTO invoices(inv_no,created_at,updated_at,inv_date,inv_type,year,month,"
            "client_name,lpo_no,client_address,payment_bank_key,withholding_included,"
            "grand_total,sell_currency,sell_total,sell_iqd_per_usd,pdf_path,rows_json,"
            "source_quote_no,extra_costs_json,source_dn_no,reference) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (old, f"created-{index}", f"updated-{index}", "8/9/2026", "materials",
             2026, 9, "TDC", f"LPO-{index}", f"address-{index}", "adib",
             index % 2, 100.25 + index, "IQD", 140350 + index, 1400,
             str(old_pdf), rows_json, f"QUT-{index}", extra_costs_json,
             f"DN-{index}", f"reference-{index}"),
        )
        conn.execute("INSERT INTO submitted_docs VALUES(?,?,?)", ("inv", old, "submitted"))
        conn.execute("INSERT INTO doc_columns VALUES(?,?,?)", ("inv", old, "[]"))
        conn.execute("INSERT INTO doc_meta VALUES(?,?,?,?)", ("inv", old, "layout", "{}"))
    first = next(iter(mapping))
    second = list(mapping)[1]
    third = list(mapping)[2]
    conn.execute("INSERT INTO paid_invoices VALUES(?,?)", (first, "paid"))
    conn.execute("INSERT INTO doc_cost_meta VALUES(?,?,?,?)", ("inv", second, "IQD", 1400))
    conn.execute("INSERT INTO attention_ignores VALUES(?,?,?,?)", ("inv", third, "unpaid", "ignored"))
    conn.execute("INSERT INTO signed_copies VALUES(?,?,?,?)", (
        "inv", first, str(root / f"{first}.signed.pdf"), "attached"))
    for index, old in enumerate(list(mapping)[1:], start=1):
        conn.execute("INSERT INTO po_fulfillment VALUES(?,?,?,?,?,?)", (
            f"A-{80 + index}", 0, "paid", "inv", old, "linked"))
    conn.executemany(
        "INSERT INTO deleted_docs VALUES(?,?,?,?,?)",
        [
            ("inv", "INV-MT-SIGNEDCOPY-901", "test", r"D:\app\.test-work\fake.pdf", ""),
            ("dn", "DN-__SIGNEDCOPY__-001", "test", r"D:\app\.test-work\fake.pdf", ""),
            ("inv", "INV-MT-TDC-025", "real", r"D:\PO TOOL\INVOICES\real.pdf", "recycle"),
        ],
    )
    conn.commit()


def refs(conn: sqlite3.Connection, table: str, column: str) -> set[str]:
    return {row[0] for row in conn.execute(f'SELECT "{column}" FROM "{table}"')}


def assert_partial_file_publish_rolls_back(repair, root: Path):
    mapping = {"INV-OLD-1": "INV-NEW-1", "INV-OLD-2": "INV-NEW-2"}
    source_dir = root / "rollback-source"
    staged_dir = root / "rollback-staged"
    backup_dir = root / "rollback-backup"
    source_dir.mkdir()
    staged_dir.mkdir()
    backup_dir.mkdir()
    records = {}
    for old, new in mapping.items():
        source = source_dir / f"{old}.pdf"
        target = source_dir / f"{new}.pdf"
        source.write_bytes(f"original {old}".encode())
        (backup_dir / source.name).write_bytes(source.read_bytes())
        records[old] = {"old_pdf": str(source), "new_pdf": str(target)}
    # Only the first staged file exists. The second copy fails after the first
    # source has already been replaced, which exercises partial rollback.
    (staged_dir / "INV-NEW-1.pdf").write_bytes(b"corrected one")
    try:
        repair.install_files_with_rollback(
            records, mapping, staged_dir, [], backup_dir
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("partial file publish should have failed")
    for old, new in mapping.items():
        assert (source_dir / f"{old}.pdf").read_bytes() == f"original {old}".encode()
        assert not (source_dir / f"{new}.pdf").exists()


def assert_exact_fake_number_with_real_path_is_refused(repair, root: Path):
    db = root / "unsafe-tombstone.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO deleted_docs VALUES(?,?,?,?,?)",
            ("inv", "INV-MT-SIGNEDCOPY-901", "real", str(root / "real.pdf"), "recycle"),
        )
        try:
            repair.remove_confirmed_test_tombstones(conn, "abraj")
        except RuntimeError as exc:
            assert "unverified tombstone" in str(exc)
        else:
            raise AssertionError("an exact fake number without a test path must be refused")
        assert refs(conn, "deleted_docs", "doc_no") == {"INV-MT-SIGNEDCOPY-901"}


def assert_precondition_drift_is_refused(repair, root: Path, mapping: dict[str, str]):
    db = root / "drift.db"
    with closing(sqlite3.connect(db)) as conn:
        seed_abraj(conn, root / "drift-pdfs", mapping)
        expected = repair.audit_repair_preconditions(conn, mapping)
        conn.execute(
            "DELETE FROM doc_meta WHERE doc_type='inv' AND doc_no=?",
            (next(iter(mapping)),),
        )
        try:
            repair.audit_repair_preconditions(conn, mapping, expected)
        except RuntimeError as exc:
            assert "precondition" in str(exc).casefold()
        else:
            raise AssertionError("reference-count drift must stop the repair")


def assert_database_restore_is_verified(repair, root: Path):
    live = root / "restore-live.db"
    backup = root / "restore-backup.db"
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES('before')")
        conn.commit()
    repair._backup_database(live, backup)
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("UPDATE sample SET value='after'")
        conn.commit()
    repair._restore_database(backup, live)
    with closing(sqlite3.connect(live)) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "before"
    assert repair._read_fingerprint(live) == repair._read_fingerprint(backup)


def assert_post_publish_recovery_restores_everything(repair, root: Path):
    original_dir = root / "recover-originals"
    original_dir.mkdir()
    source = root / "INV-OLD.pdf"
    target = root / "INV-NEW.pdf"
    (original_dir / source.name).write_bytes(b"original invoice")
    target.write_bytes(b"replacement invoice")

    database_pairs = []
    for name in ("abraj", "eshraqat"):
        live = root / f"{name}-recover-live.db"
        backup = root / f"{name}-recover-backup.db"
        with closing(sqlite3.connect(live)) as conn:
            conn.execute("CREATE TABLE sample(value TEXT)")
            conn.execute("INSERT INTO sample VALUES('before')")
            conn.commit()
        repair._backup_database(live, backup)
        with closing(sqlite3.connect(live)) as conn:
            conn.execute("UPDATE sample SET value='published'")
            conn.commit()
        database_pairs.append((backup, live))

    errors = repair._recover_published_state(
        [(source, target)], original_dir, [], database_pairs
    )
    assert errors == []
    assert source.read_bytes() == b"original invoice"
    assert not target.exists()
    for backup, live in database_pairs:
        assert repair._read_fingerprint(live) == repair._read_fingerprint(backup)


def assert_missing_restore_copy_is_reported(repair, root: Path):
    source = root / "missing-old.pdf"
    target = root / "missing-new.pdf"
    target.write_bytes(b"replacement")
    try:
        repair._restore_files([(source, target)], root / "no-backups", [])
    except RuntimeError as exc:
        assert "restore copy is missing" in str(exc)
    else:
        raise AssertionError("rollback must report a missing restore copy")


def main():
    repair = load_repair_module()
    mapping = dict(repair.INVOICE_MAPPING)
    assert list(mapping.values()) == [
        "INV-MT-TDC-048",
        "INV-MT-JUNE CONSTRUCTION CO.-049",
        "INV-MT-TDC-050",
        "INV-MT-TDC-051",
        "INV-MT-TDC-052",
        "INV-MT-TDC-053",
    ]

    with tempfile.TemporaryDirectory(prefix="invoice-number-repair-test-") as tmp:
        root = Path(tmp)
        (root / "drift-pdfs").mkdir()
        abraj_db = root / "abraj.db"
        eshraqat_db = root / "eshraqat.db"
        with closing(sqlite3.connect(abraj_db)) as conn:
            seed_abraj(conn, root, mapping)
            invoice_columns = [
                column[1] for column in conn.execute("PRAGMA table_info(invoices)")
            ]
            before_rows = {
                old: dict(zip(invoice_columns, conn.execute(
                    "SELECT * FROM invoices WHERE inv_no=?", (old,)
                ).fetchone()))
                for old in mapping
            }
            new_paths = {old: str(root / f"{new}.pdf") for old, new in mapping.items()}
            result = repair.rekey_abraj_database(conn, mapping, new_paths)
            assert result["invoices"] == 6
            assert result["submitted_removed"] == 6
            assert result["po_links"] == 5
            assert refs(conn, "invoices", "inv_no") >= set(mapping.values())
            assert not (refs(conn, "invoices", "inv_no") & set(mapping))
            assert not refs(conn, "submitted_docs", "doc_no")
            assert refs(conn, "paid_invoices", "inv_no") == {mapping[next(iter(mapping))]}
            assert refs(conn, "doc_cost_meta", "doc_no") == {mapping[list(mapping)[1]]}
            assert refs(conn, "attention_ignores", "doc_no") == {mapping[list(mapping)[2]]}
            assert refs(conn, "signed_copies", "doc_no") == {mapping[next(iter(mapping))]}
            assert refs(conn, "po_fulfillment", "source_no") == {
                mapping[old] for old in list(mapping)[1:]
            }
            assert refs(conn, "doc_columns", "doc_no") == set(mapping.values())
            assert refs(conn, "doc_meta", "doc_no") == set(mapping.values())
            assert refs(conn, "deleted_docs", "doc_no") == {"INV-MT-TDC-025"}
            assert repair.next_abraj_invoice_number(conn) == 54

            for old, new in mapping.items():
                row = conn.execute(
                    "SELECT * FROM invoices WHERE inv_no=?",
                    (new,),
                ).fetchone()
                index = list(mapping).index(old)
                after = dict(zip(invoice_columns, row))
                for column in invoice_columns:
                    if column == "inv_no":
                        assert after[column] == new
                    elif column == "pdf_path":
                        assert after[column] == new_paths[old]
                    else:
                        assert after[column] == before_rows[old][column], (
                            old, column, before_rows[old][column], after[column]
                        )
                assert after["created_at"] == f"created-{index}"
                assert after["updated_at"] == f"updated-{index}"
                assert after["reference"] == f"reference-{index}"

            first_new = next(iter(mapping.values()))
            record = dict(zip(
                [column[1] for column in conn.execute("PRAGMA table_info(invoices)")],
                conn.execute("SELECT * FROM invoices WHERE inv_no=?", (first_new,)).fetchone(),
            ))
            record["rows"] = json.loads(record["rows_json"])
            record["columns"] = [{"key": "item"}]
            record["layout"] = {"margin_top": 10}
            record["signing_overlay"] = {"enabled": True}
            payload = repair._invoice_payload(record, first_new)
            assert payload["items"] == record["rows"]
            assert payload["extra_costs"] == json.loads(record["extra_costs_json"])
            assert payload["lpo_no"] == "LPO-0"
            assert payload["columns"] == record["columns"]
            assert payload["layout"] == record["layout"]
            assert payload["signing_overlay"] == record["signing_overlay"]

        with closing(sqlite3.connect(eshraqat_db)) as conn:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT INTO deleted_docs VALUES(?,?,?,?,?)",
                [
                    ("inv", "IV-MT-SIGNEDCOPY-901", "test", ".test-work/fake.pdf", ""),
                    ("dn", "DN-__SIGNEDCOPY__-001", "test", ".test-work/fake.pdf", ""),
                    ("inv", "IV-0002", "real", "real.pdf", "recycle"),
                ],
            )
            removed = repair.remove_confirmed_test_tombstones(conn, "eshraqat")
            assert removed == 2
            assert refs(conn, "deleted_docs", "doc_no") == {"IV-0002"}

        assert_partial_file_publish_rolls_back(repair, root)
        assert_exact_fake_number_with_real_path_is_refused(repair, root)
        assert_precondition_drift_is_refused(repair, root, mapping)
        assert_database_restore_is_verified(repair, root)
        assert_post_publish_recovery_restores_everything(repair, root)
        assert_missing_restore_copy_is_reported(repair, root)

    print("invoice_number_repair_backend: passed")


if __name__ == "__main__":
    main()
