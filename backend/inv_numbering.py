"""
Invoice numbering + nested folder layout (mirrors quote_numbering.py).

Save path:
    <DOCUMENTS_ROOT>/INVOICES/<Client>/<Year>/<Month>/INV-...pdf
        e.g. D:\\PO TOOL\\INVOICES\\HDEC\\2026\\MAY\\INV-HDEC-MT-9.pdf

The TYPE_CODE (MT/EQ/MP/SV) is preserved in the FILENAME only - not the path.

Number format: INV-<CLIENT>-<TYPE>-<N>
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import config

_TRAILING_NUM = re.compile(r"-(\d+)$")


def _is_signed_copy(stem: str) -> bool:
    """`INV-….signed.pdf` is the CLIENT's counter-signed scan stored beside the
    invoice it belongs to (2026-08-27) - never an invoice of its own.

    Unlike DN numbers there is no stem parser to fall through here: the month
    scan globs `INV-*.pdf` and would happily list the scan as a phantom
    invoice. Every filesystem scan of the invoice tree must skip it."""
    return str(stem or "").lower().endswith(".signed")


def _sanitize_client(name: str) -> str:
    """The short CODE this client's documents are numbered and filed under.

    The rule itself lives in `client_master.document_code` - resolve every
    spelling of a client to one code, then strip what a path cannot hold - so
    the number, the folder and the code chip in the editor cannot disagree.
    This wrapper stays because all three numbering modules call it by name.
    """
    try:
        from . import client_master
        return client_master.document_code(name)
    except Exception:
        pass                      # never block numbering on the client store
    name = (name or "").strip()
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "")
    return name or "CLIENT"

def _type_code(inv_type: str) -> str:
    return config.INV_TYPE_CODES.get(inv_type, "MT")


def _type_label(inv_type: str) -> str:
    """Folder-friendly type label (Materials / Equipment / Manpower / Service).
    INV schema labels read like 'Materials Invoice' - strip the redundant
    'Invoice' word since the parent folder is already INVOICES."""
    schema = config.INV_TYPES.get(inv_type) or {}
    label = schema.get("label") or (inv_type or "Materials").title()
    label = label.replace("Invoice", "").strip()
    return label or "Materials"


def _day_folder(d: date) -> str:
    """Phase M.1 - per-day folder, DD-MM-YYYY (e.g. 11-06-2026)."""
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def inv_save_folder(client_name: str, inv_type: str = "materials",
                    d: date | None = None) -> Path:
    """Phase M.1 layout:
        <INVOICES>/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/
    """
    d = d or date.today()
    return (config.INV_ROOT
            / _type_label(inv_type)
            / _sanitize_client(client_name)
            / str(d.year)
            / config.MONTH_FOLDERS[d.month]
            / _day_folder(d))


def _global_max_number() -> int:
    """Largest trailing -N across active INV PDFs and deleted invoice tombstones."""
    root = config.INV_ROOT
    mx = 0
    if root.exists():
        for f in root.rglob("INV-*.pdf"):
            if _is_signed_copy(f.stem):
                continue
            m = _TRAILING_NUM.search(f.stem)
            if m:
                mx = max(mx, int(m.group(1)))
    try:
        from . import database
        for no in database.deleted_document_numbers("inv"):
            m = _TRAILING_NUM.search(no)
            if m:
                mx = max(mx, int(m.group(1)))
    except Exception:
        pass
    return mx


def get_next_inv_number(client_name: str, inv_type: str = "materials") -> tuple[str, Path, int]:
    """Returns (inv_no, target_folder, next_n). Folder is lazily created on save.

    Number format (Phase N): INV-<TYPE>-<CLIENT>-<NNN>
      e.g. INV-MT-TDC-006  (type code leads; running number zero-padded to 3).
    """
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        n = number_claims.peek_next("inv", profile_numbering.next_value("inv"))
        return profile_numbering.format_number("inv", n), inv_save_folder(client_name, inv_type), n

    # past every claimed number too: a save without a PDF leaves no file here
    n = number_claims.peek_next("inv", _global_max_number() + 1)
    client = _sanitize_client(client_name)
    code = _type_code(inv_type)
    inv_no = f"{config.INV_PREFIX}-{code}-{client}-{n:03d}"
    return inv_no, inv_save_folder(client_name, inv_type), n


def claim_next_inv_number(client_name: str, inv_type: str = "materials", *, holder: str = "") -> str:
    """The next invoice number, HELD for this save (multi-user, 2026-10-22).

    Same format as get_next_inv_number, but taken through number_claims, so
    two saves at the same moment can never both leave with it."""
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        return number_claims.claim_next(
            "inv", profile_numbering.next_value("inv"),
            lambda n: profile_numbering.format_number("inv", n), holder=holder)
    client = _sanitize_client(client_name)
    code = _type_code(inv_type)
    return number_claims.claim_next(
        "inv", _global_max_number() + 1,
        lambda n: f"{config.INV_PREFIX}-{code}-{client}-{n:03d}", holder=holder)


_KNOWN_CODES = {"MT", "EQ", "MP", "SV"}


def _type_code_from_filename(stem: str) -> str:
    """Pull the type code (MT/EQ/MP/SV) out of an INV filename stem.

    Handles BOTH naming arrangements so old files keep working:
      • new: INV-<TYPE>-<CLIENT>-<N>   (code is the 2nd segment)
      • old: INV-<CLIENT>-<TYPE>-<N>   (code is the segment before the number)
    """
    parts = stem.split("-")
    if len(parts) >= 2 and parts[1] in _KNOWN_CODES:
        return parts[1]            # new arrangement
    if len(parts) >= 2 and parts[-2] in _KNOWN_CODES:
        return parts[-2]           # legacy arrangement
    return "MT"


def list_invoices_for_month(year: int, month: int) -> list[dict]:
    """Phase M.1 - recursive scan filtered by /<year>/<MONTH>/ path segments
    (finds files in both the new Type/Client/Year/MONTH/Day layout and the
    old Client/Year/MONTH one)."""
    root = config.INV_ROOT
    if not root.exists():
        return []
    month_name = config.MONTH_FOLDERS[month]
    year_s = str(year)
    out = []
    file_pattern = "IV-*.pdf" if config.ACTIVE_PROFILE == "eshraqat" else "INV-*.pdf"
    for f in sorted(root.rglob(file_pattern)):
        if _is_signed_copy(f.stem):
            continue
        rel_parts = f.relative_to(root).parts
        if year_s not in rel_parts:
            continue
        yi = rel_parts.index(year_s)
        if yi + 1 >= len(rel_parts) - 1 or rel_parts[yi + 1] != month_name:
            continue
        client = rel_parts[yi - 1] if yi >= 1 else ""
        out.append({
            "inv_no": f.stem,
            "filename": f.name,
            "path": str(f),
            "client": client,
            "type_code": "" if config.ACTIVE_PROFILE == "eshraqat" else _type_code_from_filename(f.stem),
        })
    return out
