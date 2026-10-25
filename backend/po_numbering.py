"""
PO numbering and month folder logic.

Rules:
- POs are saved under <PO_ROOT>\\<YEAR>\\<MONTH>\\A-NN.pdf
- "Current month" is determined by today's date.
- If the current month folder is missing, it's created. The next number is
  (max across BOTH the current month AND the previous month) + 1 so numbering
  continues from where the last month left off.
- If files like A-09.pdf exist, next is A-10.pdf.

Phase F.1: PO_ROOT default moved from D:\\ABRAJ\\CSSP\\PO to
<DOCUMENTS_ROOT>\\PURCHASE ORDERS, and per-year nesting was added.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import config

_PO_RE = re.compile(rf"^{re.escape(config.PO_PREFIX)}-(\d+)\.pdf$", re.IGNORECASE)
_PO_NO_RE = re.compile(rf"^{re.escape(config.PO_PREFIX)}-(\d+)$", re.IGNORECASE)


def _day_folder_name(d: date) -> str:
    """Phase M.1 - per-day folder, DD-MM-YYYY (e.g. 11-06-2026)."""
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def _month_folder(d: date) -> Path:
    return config.PO_ROOT / str(d.year) / config.MONTH_FOLDERS[d.month]


def _previous_month_folder(d: date) -> Path:
    if d.month == 1:
        return config.PO_ROOT / str(d.year - 1) / config.MONTH_FOLDERS[12]
    return config.PO_ROOT / str(d.year) / config.MONTH_FOLDERS[d.month - 1]


def _max_po_number_in_folder(folder: Path) -> int:
    """Highest NN across A-NN.pdf files in the folder INCLUDING day
    subfolders (Phase M.1), or 0 if none/folder missing."""
    if not folder.exists():
        return 0
    best = 0
    for f in folder.rglob("*.pdf"):
        m = _PO_RE.match(f.name)
        if m:
            n = int(m.group(1))
            if n > best:
                best = n
    return best


def _max_deleted_po_number() -> int:
    """Highest tombstoned PO number, so deleting a PO never reuses its number."""
    best = 0
    try:
        from . import database
        for no in database.deleted_document_numbers("po"):
            m = _PO_NO_RE.match(str(no or ""))
            if m:
                best = max(best, int(m.group(1)))
    except Exception:
        pass
    return best


def get_next_po_number(today: date | None = None) -> tuple[str, Path]:
    """
    Return (po_no_str, target_folder).
    Phase M.1: target folder is the DAY folder
        <PO_ROOT>/<Year>/<MONTH>/<DD-MM-YYYY>/
    Ensures it exists.
    """
    today = today or date.today()
    current_folder = folder_for_date(today)
    current_folder.mkdir(parents=True, exist_ok=True)

    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        n = number_claims.peek_next("po", profile_numbering.next_value("po"))
        return profile_numbering.format_number("po", n), current_folder

    # past every claimed number too: a save without a PDF leaves no file here
    next_n = number_claims.peek_next("po", _files_next_po_value(today))
    return f"{config.PO_PREFIX}-{next_n}", current_folder


def _files_next_po_value(today: date) -> int:
    """What the PO folders and tombstones say the next running number is."""
    cur_max = _max_po_number_in_folder(_month_folder(today))
    prev_max = _max_po_number_in_folder(_previous_month_folder(today))
    deleted_max = _max_deleted_po_number()
    return max(cur_max, prev_max, deleted_max) + 1


def claim_next_po_number(*, holder: str = "", today: date | None = None) -> str:
    """The next PO number, HELD for this save (multi-user, 2026-10-22)."""
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        return number_claims.claim_next(
            "po", profile_numbering.next_value("po"),
            lambda n: profile_numbering.format_number("po", n), holder=holder)
    return number_claims.claim_next(
        "po", _files_next_po_value(today or date.today()),
        lambda n: f"{config.PO_PREFIX}-{n}", holder=holder)


def list_pos_for_month(year: int, month: int) -> list[dict]:
    """List PO PDFs for the month, including day subfolders, sorted by number."""
    folder = config.PO_ROOT / str(year) / config.MONTH_FOLDERS[month]
    if not folder.exists():
        return []
    items = []
    for f in folder.rglob("*.pdf"):
        m = _PO_RE.match(f.name)
        if m:
            items.append({
                "po_no": f"{config.PO_PREFIX}-{int(m.group(1))}",
                "number": int(m.group(1)),
                "filename": f.name,
                "path": str(f),
            })
    items.sort(key=lambda x: x["number"])
    return items


def folder_for_month(year: int, month: int) -> Path:
    return config.PO_ROOT / str(year) / config.MONTH_FOLDERS[month]


def folder_for_date(d: date) -> Path:
    """Phase M.1 - day-level save folder for a specific date."""
    return _month_folder(d) / _day_folder_name(d)
