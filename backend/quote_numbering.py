"""
Quotation numbering + nested folder layout.

Save path:
    <DOCUMENTS_ROOT>/QUOTATIONS/<Client>/<Year>/<Month>/QUT-...pdf
        e.g. D:\\PO TOOL\\QUOTATIONS\\HDEC\\2026\\MAY\\QUT-HDEC-EQ-7.pdf

The TYPE_CODE (MT/EQ/MP/SV) is preserved in the FILENAME only - not the path.

Number format: QUT-<CLIENT>-<TYPE>-<N>
  - CLIENT: client name (filename-sanitized)
  - TYPE:   MT / MP / EQ / SV
  - N:      a GLOBAL running counter (Option A) across all types/clients/months.
            Computed by recursively scanning every QUT-*.pdf under
            <DOCUMENTS_ROOT>/QUOTATIONS for the maximum trailing -<number>.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import config

_TRAILING_NUM = re.compile(r"-(\d+)$")


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

def _type_code(quote_type: str) -> str:
    return config.QUOTE_TYPE_CODES.get(quote_type, "MT")


def _type_label(quote_type: str) -> str:
    """Folder-friendly type label (Materials / Equipment / Manpower / Service)."""
    schema = config.QUOTE_TYPES.get(quote_type) or {}
    return schema.get("label") or (quote_type or "Materials").title()


def _day_folder(d: date) -> str:
    """Phase M.1 - per-day folder, DD-MM-YYYY (e.g. 11-06-2026)."""
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def quote_save_folder(client_name: str, quote_type: str = "materials",
                      d: date | None = None) -> Path:
    """Phase M.1 layout:
        <QUOTATIONS>/<TypeLabel>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/
    e.g. QUOTATIONS/Service/HDEC/2026/JUN/11-06-2026/QUT-SV-HDEC-007.pdf
    """
    d = d or date.today()
    return (config.QUOTE_ROOT
            / _type_label(quote_type)
            / _sanitize_client(client_name)
            / str(d.year)
            / config.MONTH_FOLDERS[d.month]
            / _day_folder(d))


def _global_max_number() -> int:
    """Largest trailing -N across active QUT PDFs and deleted quote tombstones."""
    root = config.QUOTE_ROOT
    mx = 0
    if root.exists():
        for f in root.rglob("QUT-*.pdf"):
            m = _TRAILING_NUM.search(f.stem)
            if m:
                mx = max(mx, int(m.group(1)))
    try:
        from . import database
        for no in database.deleted_document_numbers("quote"):
            m = _TRAILING_NUM.search(no)
            if m:
                mx = max(mx, int(m.group(1)))
    except Exception:
        pass
    return mx


def get_next_quote_number(client_name: str, quote_type: str = "materials") -> tuple[str, Path, int]:
    """
    Returns (quote_no, target_folder, next_n).
    Target folder is lazily created on save.

    Number format (Phase N): QUT-<TYPE>-<CLIENT>-<NNN>
      e.g. QUT-MT-HDEC-007  (type code leads; running number zero-padded to 3).
    """
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        n = number_claims.peek_next("quote", profile_numbering.next_value("quote"))
        return profile_numbering.format_number("quote", n), quote_save_folder(client_name, quote_type), n

    # past every claimed number too: a save without a PDF leaves no file here
    n = number_claims.peek_next("quote", _global_max_number() + 1)
    client = _sanitize_client(client_name)
    code = _type_code(quote_type)
    quote_no = f"{config.QUOTE_PREFIX}-{code}-{client}-{n:03d}"
    return quote_no, quote_save_folder(client_name, quote_type), n


def claim_next_quote_number(client_name: str, quote_type: str = "materials", *, holder: str = "") -> str:
    """The next quotation number, HELD for this save (multi-user, 2026-10-22)."""
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        return number_claims.claim_next(
            "quote", profile_numbering.next_value("quote"),
            lambda n: profile_numbering.format_number("quote", n), holder=holder)
    client = _sanitize_client(client_name)
    code = _type_code(quote_type)
    return number_claims.claim_next(
        "quote", _global_max_number() + 1,
        lambda n: f"{config.QUOTE_PREFIX}-{code}-{client}-{n:03d}", holder=holder)


_KNOWN_CODES = {"MT", "EQ", "MP", "SV"}


def _type_code_from_filename(stem: str) -> str:
    """Pull the type code (MT/EQ/MP/SV) out of a QUT filename stem.

    Handles BOTH naming arrangements so old files keep working:
      • new: QUT-<TYPE>-<CLIENT>-<N>   (code is the 2nd segment)
      • old: QUT-<CLIENT>-<TYPE>-<N>   (code is the segment before the number)
    """
    parts = stem.split("-")
    if len(parts) >= 2 and parts[1] in _KNOWN_CODES:
        return parts[1]            # new arrangement
    if len(parts) >= 2 and parts[-2] in _KNOWN_CODES:
        return parts[-2]           # legacy arrangement
    return "MT"


def list_quotes_for_month(year: int, month: int) -> list[dict]:
    """
    Filesystem listing for the month.

    Phase M.1 - recursive scan filtered by the /<year>/<MONTH>/ path segments
    so it finds files in EVERY historical layout at once:
      • newest:  <Type>/<Client>/<year>/<MONTH>/<DD-MM-YYYY>/QUT-*.pdf
      • old:     <Client>/<year>/<MONTH>/QUT-*.pdf
    The client is the path segment immediately before the year folder in
    both layouts.
    """
    root = config.QUOTE_ROOT
    if not root.exists():
        return []
    month_name = config.MONTH_FOLDERS[month]
    year_s = str(year)
    out = []
    file_pattern = "QT-*.pdf" if config.ACTIVE_PROFILE == "eshraqat" else "QUT-*.pdf"
    for f in sorted(root.rglob(file_pattern)):
        rel_parts = f.relative_to(root).parts
        if year_s not in rel_parts:
            continue
        yi = rel_parts.index(year_s)
        # The month folder must directly follow the year folder.
        if yi + 1 >= len(rel_parts) - 1 or rel_parts[yi + 1] != month_name:
            continue
        client = rel_parts[yi - 1] if yi >= 1 else ""
        out.append({
            "quote_no": f.stem,
            "filename": f.name,
            "path": str(f),
            "client": client,
            "type_code": "" if config.ACTIVE_PROFILE == "eshraqat" else _type_code_from_filename(f.stem),
        })
    return out
