"""Renumber the four JUNE documents onto the client's DISPLAY name.

Waleed's ruling, 2026-09-11: "the full company name in the doc number ... is
messy and too much. instead if we are using the display name which is just
JUNE it would be much better". Everything numbered after this date already
comes out as JUNE - `_sanitize_client` now resolves through Clients Master Data
- so these four are the only documents left holding the long form:

    INV-MT-JUNE CONSTRUCTION CO.-049  ->  INV-MT-JUNE-049
    QUT-MT-JUNE CONSTRUCTION CO.-055  ->  QUT-MT-JUNE-055
    QUT-EQ-JUNE CONSTRUCTION CO.-065  ->  QUT-EQ-JUNE-065
    QUT-MT-JUNE CONSTRUCTION CO.-066  ->  QUT-MT-JUNE-066

The PRINTED client name does not change. "JUNE CONSTRUCTION CO." is this
client's legal name and stays on the paper; only the number and the folder
shorten. The sequence numbers do not move either - they are per profile and
per type, not per client.

Default mode rehearses: it takes a restore point, regenerates the corrected
PDFs into a staging folder, checks them against the originals, replays the
SQLite edits on COPIES of the live databases and verifies those. Nothing live
is touched until --apply.

    python scripts/renumber_june_to_display_name_20260911.py
    python scripts/renumber_june_to_display_name_20260911.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = APP_ROOT / "data"
ABRAJ_DB = DATA_ROOT / "profiles" / "abraj" / "po_tool.db"
REGISTRY = DATA_ROOT / "profile-registry.json"
BACKUP_ROOT = DATA_ROOT / "master-data-backups"

OLD_CLIENT = "JUNE CONSTRUCTION CO."
NEW_CLIENT = "JUNE"

# kind, old number, new number. Deliberately explicit: this utility renumbers
# these four documents and nothing else.
DOCUMENTS = (
    ("inv", "INV-MT-JUNE CONSTRUCTION CO.-049", "INV-MT-JUNE-049"),
    ("qut", "QUT-MT-JUNE CONSTRUCTION CO.-055", "QUT-MT-JUNE-055"),
    ("qut", "QUT-EQ-JUNE CONSTRUCTION CO.-065", "QUT-EQ-JUNE-065"),
    ("qut", "QUT-MT-JUNE CONSTRUCTION CO.-066", "QUT-MT-JUNE-066"),
)

# Every place a document NUMBER is stored, found by scanning all columns of all
# tables in both profile databases and the shared library for the old client
# string. The client-name columns are deliberately absent: the legal name stays
# on the document. (table, key column, qualifier column, qualifier value)
NUMBER_COLUMNS = (
    ("invoices", "inv_no", None, None),
    ("invoices", "source_quote_no", None, None),
    ("quotes", "quote_no", None, None),
    ("doc_columns", "doc_no", "kind", {"inv": "inv", "qut": "quote"}),
    ("doc_meta", "doc_no", "doc_type", {"inv": "inv", "qut": "qut"}),
    ("submitted_docs", "doc_no", "kind", {"inv": "inv", "qut": "quote"}),
    ("po_fulfillment", "source_no", None, None),
)

# The client-name columns this script must leave EXACTLY as they are. Checked
# before and after, in both directions: a renumber that also rewrote the
# printed name would be a different, unasked-for change.
CLIENT_NAME_COLUMNS = (("invoices", "client_name"), ("quotes", "client_name"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if not reader.pages:
        raise RuntimeError(f"PDF has no pages: {path}")
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _new_path(old_path: str, old_no: str, new_no: str) -> Path:
    """Where the document is filed once its client segment shortens.

    Derived from where it is filed NOW rather than recomputed, so a document
    that was filed under an unusual date or month keeps its own folder. The
    caller checks the result against what the numbering modules would build.
    """
    parts = list(Path(old_path).parts)
    for index, part in enumerate(parts):
        if part in (OLD_CLIENT, OLD_CLIENT.rstrip(".")):
            parts[index] = NEW_CLIENT
    path = Path(*parts)
    if path.name != f"{old_no}.pdf":
        raise RuntimeError(f"Unexpected file name for {old_no}: {path.name}")
    return path.with_name(f"{new_no}.pdf")


def _expected_folder(record: dict, kind: str) -> Path:
    """What the app would build for this document today, for comparison."""
    sys.path.insert(0, str(APP_ROOT))
    from backend import inv_numbering, quote_numbering
    from backend.api import _parse_display_date

    if kind == "inv":
        when = _parse_display_date(record["inv_date"])
        return inv_numbering.inv_save_folder(record["client_name"], record["inv_type"], when)
    when = _parse_display_date(record["quote_date"])
    return quote_numbering.quote_save_folder(record["client_name"], record["quote_type"], when)


def load_documents() -> dict[str, dict]:
    """Read the four records, with everything a re-render needs."""
    uri = ABRAJ_DB.resolve().as_uri() + "?mode=ro"
    records: dict[str, dict] = {}
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for kind, old, new in DOCUMENTS:
            table, key = ("invoices", "inv_no") if kind == "inv" else ("quotes", "quote_no")
            row = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (old,)).fetchone()
            if not row:
                raise RuntimeError(f"Document is missing from the database: {old}")
            record = dict(row)
            record["kind"] = kind
            record["old_no"] = old
            record["new_no"] = new
            record["rows"] = json.loads(record.pop("rows_json") or "[]")
            if kind == "qut":
                record["terms"] = json.loads(record.pop("terms_json") or "[]")
            record["extra_costs"] = json.loads(record.get("extra_costs_json") or "[]")

            columns_kind = "inv" if kind == "inv" else "quote"
            columns = conn.execute(
                "SELECT columns_json FROM doc_columns WHERE kind=? AND doc_no=?",
                (columns_kind, old),
            ).fetchone()
            record["columns"] = json.loads(columns[0]) if columns and columns[0] else None
            meta = {k: v for k, v in conn.execute(
                "SELECT key,value FROM doc_meta WHERE doc_type=? AND doc_no=?", (kind, old))}
            record["layout"] = json.loads(meta["layout"]) if meta.get("layout") else None
            record["signing_overlay"] = json.loads(meta["signing"]) if meta.get("signing") else None

            old_pdf = Path(record["pdf_path"] or "")
            if not old_pdf.is_file():
                raise RuntimeError(f"Document PDF is missing: {old_pdf}")
            new_pdf = _new_path(record["pdf_path"], old, new)
            if new_pdf.exists():
                raise RuntimeError(f"Target PDF already exists: {new_pdf}")
            expected = _expected_folder(record, kind)
            if Path(new_pdf).parent != expected:
                raise RuntimeError(
                    f"{new} would be filed at {new_pdf.parent}, but the app would look in "
                    f"{expected}. Renumbering it there would hide the document.")
            record["old_pdf"] = old_pdf
            record["new_pdf"] = new_pdf
            records[old] = record
    return records


def payload_for(record: dict) -> dict:
    """The render payload, identical to the one the editor saves."""
    if record["kind"] == "inv":
        payload = {
            "inv_no": record["new_no"],
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
            "extra_costs": record.get("extra_costs") or [],
            # The source quotation is renumbered in the same pass, so the link
            # printed on the invoice has to move with it.
            "source_quote_no": _renumbered(record.get("source_quote_no")),
            "source_dn_no": record.get("source_dn_no"),
            "reference": record.get("reference") or "",
        }
    else:
        payload = {
            "quote_no": record["new_no"],
            "date": record.get("quote_date"),
            "quote_type": record.get("quote_type") or "materials",
            "client_name": record.get("client_name") or "",
            "rfq_no": record.get("rfq_no") or "",
            "client_address": record.get("client_address") or "",
            "items": record.get("rows") or [],
            "terms": record.get("terms") or [],
            "reference": record.get("reference") or "",
        }
    for key in ("columns", "layout", "signing_overlay"):
        if record.get(key) is not None:
            payload[key] = record[key]
    return payload


def _renumbered(number: str | None) -> str | None:
    if not number:
        return number
    for _kind, old, new in DOCUMENTS:
        if number == old:
            return new
    return number


def render_corrected(records: dict[str, dict], output: Path) -> list[dict]:
    """Re-render each document under its new number and prove it is the same one.

    The text of the corrected PDF must match the original everywhere the number
    is not. That is the check that matters: a field the database never stored
    would come back missing, and a plain "the new number is in there" assertion
    would happily pass while the document quietly lost a line.
    """
    sys.path.insert(0, str(APP_ROOT))
    from backend import api, inv_generator, quote_generator

    output.mkdir(parents=True, exist_ok=True)
    results = []
    for _kind, old, new in DOCUMENTS:
        record = records[old]
        payload = api._printable_document(payload_for(record))
        destination = output / f"{new}.pdf"
        if record["kind"] == "inv":
            payload["_dn_docs"] = api._resolve_dn_docs(payload)
            inv_generator.render_inv_pdf(payload, destination)
        else:
            quote_generator.render_quote_pdf(payload, destination)

        before, after = _pdf_text(record["old_pdf"]), _pdf_text(destination)
        if new not in after:
            raise RuntimeError(f"The new number is not printed on {destination}")
        if old in after:
            raise RuntimeError(f"The old number is still printed on {destination}")
        # Normalise both texts through the same substitution and compare. Every
        # document in this batch is substituted, so a printed cross-reference
        # between two of them normalises on both sides.
        #
        # Whitespace is squashed BEFORE the substitution, not after: the long
        # number does not fit the field on the printed page and wraps onto a
        # second line, so a substitution run on the raw text finds nothing to
        # replace and every document looks changed. Which is its own small
        # argument for the short code.
        normalised_before, normalised_after = _squash(before), _squash(after)
        for _k, o, n in DOCUMENTS:
            normalised_before = normalised_before.replace(o, n)
        if normalised_before != normalised_after:
            raise RuntimeError(
                f"{new} does not print the same document as {old}. The regenerated PDF "
                "differs by more than the number - compare it before going further:\n"
                f"  original : {record['old_pdf']}\n  regenerated: {destination}")
        results.append({"old": old, "new": new, "pages": after.count("\f") + 1,
                        "sha256": _sha256(destination), "staged": str(destination)})
    return results


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def rekey(conn: sqlite3.Connection) -> dict[str, int]:
    """Move every stored number, and only the numbers."""
    counts: dict[str, int] = {}
    for kind, old, new in DOCUMENTS:
        for table, column, qualifier, mapping in NUMBER_COLUMNS:
            where, params = f'"{column}"=?', [new, old]
            if qualifier:
                where += f' AND "{qualifier}"=?'
                params.append(mapping[kind])
            cursor = conn.execute(
                f'UPDATE "{table}" SET "{column}"=? WHERE {where}', params)
            if cursor.rowcount:
                counts[f"{table}.{column}"] = counts.get(f"{table}.{column}", 0) + cursor.rowcount
    for kind, old, new in DOCUMENTS:
        table, key = ("invoices", "inv_no") if kind == "inv" else ("quotes", "quote_no")
        record = conn.execute(
            f"SELECT pdf_path FROM {table} WHERE {key}=?", (new,)).fetchone()
        conn.execute(f"UPDATE {table} SET pdf_path=? WHERE {key}=?",
                     (str(_new_path(record[0], old, new)), new))
        counts[f"{table}.pdf_path"] = counts.get(f"{table}.pdf_path", 0) + 1
    return counts


def verify(conn: sqlite3.Connection, records: dict[str, dict]) -> None:
    """Nothing is left holding the long number, and the names are untouched."""
    for table, column, _q, _m in NUMBER_COLUMNS:
        stale = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" LIKE ?',
            (f"%{OLD_CLIENT}%",)).fetchone()[0]
        if stale:
            raise RuntimeError(f"{stale} row(s) still hold the long number in {table}.{column}")
    for kind, _old, new in DOCUMENTS:
        table, key = ("invoices", "inv_no") if kind == "inv" else ("quotes", "quote_no")
        row = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (new,)).fetchone()
        if not row:
            raise RuntimeError(f"{new} is missing after the renumber")
    for table, column in CLIENT_NAME_COLUMNS:
        kept = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{column}"=?', (OLD_CLIENT,)).fetchone()[0]
        expected = sum(1 for k, _o, _n in DOCUMENTS
                       if (k == "inv") == (table == "invoices"))
        if kept != expected:
            raise RuntimeError(
                f"{table}.{column} should still name {OLD_CLIENT} on {expected} row(s), "
                f"found {kept}. The printed client name must not change.")
    # The invoice's link to its quotation has to survive the move.
    link = conn.execute(
        "SELECT source_quote_no FROM invoices WHERE inv_no=?", ("INV-MT-JUNE-049",)).fetchone()[0]
    if link != "QUT-MT-JUNE-066":
        raise RuntimeError(f"The invoice lost its quotation link: {link!r}")


def restore_point(stamp: str) -> Path:
    folder = BACKUP_ROOT / f"{stamp}-june-renumber"
    (folder / "original-pdfs").mkdir(parents=True, exist_ok=True)
    for name in ("abraj/po_tool.db", "eshraqat/po_tool.db"):
        source = DATA_ROOT / "profiles" / name
        if source.is_file():
            target = folder / "databases" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_database(source, target)
    shutil.copy2(DATA_ROOT / "shared" / "library.db", folder / "databases" / "library.db")
    shutil.copy2(REGISTRY, folder / "profile-registry.json")
    return folder


def _copy_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(target)) as copy:
        live.backup(copy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="publish the rehearsed renumber to the live data")
    args = parser.parse_args()

    active = json.loads(REGISTRY.read_text(encoding="utf-8")).get("active_profile")
    if active != "abraj":
        print(f"The active profile is {active!r}; these documents are Abraj's. Switch first.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = restore_point(stamp)
    print(f"Restore point: {folder}")

    records = load_documents()
    for old, record in records.items():
        print(f"  {old}\n    -> {record['new_no']}\n       {record['new_pdf']}")
        shutil.copy2(record["old_pdf"], folder / "original-pdfs" / record["old_pdf"].name)

    rendered = render_corrected(records, folder / "corrected-pdfs")
    print(f"\nRegenerated {len(rendered)} PDF(s); each prints the same document as before.")

    rehearsal = folder / "rehearsal.db"
    _copy_database(ABRAJ_DB, rehearsal)
    with closing(sqlite3.connect(rehearsal)) as conn:
        with conn:
            counts = rekey(conn)
        verify(conn, records)
    print("Rehearsed on a copy:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    report = {"stamp": stamp, "documents": [dict(kind=k, old=o, new=n) for k, o, n in DOCUMENTS],
              "rendered": rendered, "rehearsed_counts": counts}
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not args.apply:
        print("\nRehearsal only. Nothing live was changed. Re-run with --apply to publish.")
        return 0

    published = []
    try:
        with closing(sqlite3.connect(ABRAJ_DB)) as conn:
            with conn:
                rekey(conn)
            verify(conn, records)
        for entry in rendered:
            record = records[entry["old"]]
            record["new_pdf"].parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["staged"], record["new_pdf"])
            published.append(record["new_pdf"])
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        _copy_database(folder / "databases" / "abraj" / "po_tool.db", ABRAJ_DB)
        print("Publish failed and was rolled back from the restore point.")
        raise

    for record in records.values():
        record["old_pdf"].unlink(missing_ok=True)
    _prune_empty({r["old_pdf"].parent for r in records.values()})

    with closing(sqlite3.connect(ABRAJ_DB)) as conn:
        verify(conn, records)
        for _kind, _old, new in DOCUMENTS:
            table, key = ("invoices", "inv_no") if new.startswith("INV") else ("quotes", "quote_no")
            path = Path(conn.execute(
                f"SELECT pdf_path FROM {table} WHERE {key}=?", (new,)).fetchone()[0])
            if not path.is_file():
                raise RuntimeError(f"{new} points at a file that is not there: {path}")
            print(f"  published {new} -> {path}")
    print("\nApplied. The four JUNE documents are numbered on the display name.")
    return 0


def _prune_empty(folders: set[Path]) -> None:
    """Remove the old client folders once their last document has moved.

    Windows strips a trailing dot from a directory NAME, so the database says
    `JUNE CONSTRUCTION CO.\\...` while the folder on disk is `JUNE CONSTRUCTION
    CO`. Reads follow either spelling; `rmdir` refuses the one with the dot
    (WinError 5). So each folder is re-addressed through its parent's real
    listing before it is removed, and a folder that still holds anything is
    left alone rather than forced.
    """
    for folder in folders:
        current = _as_on_disk(folder)
        while current is not None and current.is_dir() and not any(current.iterdir()):
            parent = current.parent
            current.rmdir()
            current = _as_on_disk(parent)


def _as_on_disk(folder: Path) -> Path | None:
    """The same folder, spelled the way the filesystem spells it."""
    if folder.is_dir() and folder.name in {c.name for c in folder.parent.iterdir()}:
        return folder
    trimmed = folder.name.rstrip(". ")
    for child in folder.parent.iterdir() if folder.parent.is_dir() else []:
        if child.is_dir() and child.name == trimmed:
            return child
    return None


if __name__ == "__main__":
    raise SystemExit(main())
