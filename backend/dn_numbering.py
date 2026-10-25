"""
Delivery Note numbering + nested folder layout.

Path:   <DOCUMENTS_ROOT>/DELIVERY NOTES/<Client>/<Year>/<Month>/<Client>-DN-N.pdf
Number: <CLIENT>-DN-<N>   (global counter across every client/year/month)

Phase G.5: filenames now include the client prefix (mirrors the QUT pattern).
Legacy "DN-N.pdf" files are recognised by the scanner so the counter doesn't
collide after migration, and the migration runner renames them on first launch.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import config

_TRAILING_NUM = re.compile(r"-(\d+)$")


def _dn_number_from_stem(stem: str) -> int | None:
    """Return the running number for any DN filename arrangement, else None.

    Recognised forms:
      • new:    DN-<CLIENT>-<NNN>   (prefix leads)
      • legacy: <CLIENT>-DN-<N>     (client prefix - Phase G.5)
      • oldest: DN-<N>              (bare)
    A stem is treated as a DN file only if it contains a 'DN' token.
    """
    parts = stem.split("-")
    if not any(p.upper() == "DN" for p in parts):
        return None
    m = _TRAILING_NUM.search(stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


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

def _day_folder(d: date) -> str:
    """Phase M.1 - per-day folder, DD-MM-YYYY (e.g. 11-06-2026)."""
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def dn_save_folder(client_name: str, d: date | None = None) -> Path:
    """Phase M.1 layout: <DELIVERY NOTES>/<Client>/<Year>/<MONTH>/<DD-MM-YYYY>/
    (DNs have no sub-type, so no type folder)."""
    d = d or date.today()
    return (config.DN_ROOT
            / _sanitize_client(client_name)
            / str(d.year)
            / config.MONTH_FOLDERS[d.month]
            / _day_folder(d))


def _global_max_number() -> int:
    """Largest trailing -N across active DN PDFs and deleted DN tombstones."""
    root = config.DN_ROOT
    mx = 0
    if root.exists():
        for f in root.rglob("*.pdf"):
            num = _dn_number_from_stem(f.stem)
            if num is not None:
                mx = max(mx, num)
    try:
        from . import database
        for no in database.deleted_document_numbers("dn"):
            num = _dn_number_from_stem(no)
            if num is not None:
                mx = max(mx, num)
    except Exception:
        pass
    return mx


def get_next_dn_number(client_name: str = "") -> tuple[str, Path, int]:
    """Number format (Phase N): DN-<CLIENT>-<NNN>  (prefix leads; 3-digit number).

    DNs have no sub-type, so the arrangement is just prefix-client-number.
    """
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        n = number_claims.peek_next("dn", profile_numbering.next_value("dn"))
        return profile_numbering.format_number("dn", n), dn_save_folder(client_name), n

    # past every claimed number too: a save without a PDF leaves no file here
    n = number_claims.peek_next("dn", _global_max_number() + 1)
    client = _sanitize_client(client_name)
    dn_no = f"{config.DN_PREFIX}-{client}-{n:03d}"
    return dn_no, dn_save_folder(client_name), n


def claim_next_dn_number(client_name: str = "", *, holder: str = "") -> str:
    """The next delivery-note number, HELD for this save (multi-user, 2026-10-22)."""
    from . import number_claims
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import profile_numbering
        return number_claims.claim_next(
            "dn", profile_numbering.next_value("dn"),
            lambda n: profile_numbering.format_number("dn", n), holder=holder)
    client = _sanitize_client(client_name)
    return number_claims.claim_next(
        "dn", _global_max_number() + 1,
        lambda n: f"{config.DN_PREFIX}-{client}-{n:03d}", holder=holder)


def list_dns_for_month(year: int, month: int) -> list[dict]:
    """Phase M.1 - recursive scan filtered by /<year>/<MONTH>/ path segments
    (finds files both at month level and in DD-MM-YYYY day subfolders)."""
    root = config.DN_ROOT
    if not root.exists():
        return []
    month_name = config.MONTH_FOLDERS[month]
    year_s = str(year)
    out = []
    for f in sorted(root.rglob("*.pdf")):
        # Match every DN arrangement (DN-N, <client>-DN-N, DN-<client>-NNN).
        if _dn_number_from_stem(f.stem) is None:
            continue
        rel_parts = f.relative_to(root).parts
        if year_s not in rel_parts:
            continue
        yi = rel_parts.index(year_s)
        if yi + 1 >= len(rel_parts) - 1 or rel_parts[yi + 1] != month_name:
            continue
        client = rel_parts[yi - 1] if yi >= 1 else ""
        out.append({
            "dn_no": f.stem,
            "filename": f.name,
            "path": str(f),
            "client": client,
        })
    return out
