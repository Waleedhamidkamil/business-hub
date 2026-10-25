"""
Lock every PDF the app makes (2026-10-07).

The stamp and the signatures ride inside each document as images, and an
unlocked PDF lets anyone lift them out cleanly with any free PDF tool. Waleed
asked for every generated PDF to be protected.

What the lock does - AES-256, standard PDF permissions:
    allowed   opening with NO password (clients must be able to read it),
              printing at full quality
    blocked   editing, copying text or images out, extracting pages,
              re-assembling, filling, commenting/drawing on it (an annotation
              can carry an image, so commenting is an editing door too)

What it cannot do, said plainly so nobody over-trusts it: a PDF that opens
without a password can be unlocked by a determined person with a free
command-line tool. It stops ordinary people and ordinary PDF apps. The
document's verification code (docs/04-BACKEND-DOCGEN.md) is what protects
against a forgery that gets past it.

The owner password is random, made once per machine and kept in the
git-ignored `data/secrets.json` under "pdf_protection". Losing it costs
nothing: the app never needs to unlock its own files (pypdf, pdf.js and
PyMuPDF all read them with the empty user password), it only ever locks.

Switches, also under "pdf_protection" in secrets.json:
    "lock": false            turn locking off on this machine
    "allow_comments": true   let clients write or sign on the PDF digitally

Called LAST, after every other step that writes the file (the signed-copy
merge, the LPO acknowledgement overlay, the statement join), so nothing in the
app has to read a locked file in order to change it.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

SECTION = "pdf_protection"

# PDF permission bits (ISO 32000-1, table 22).
P_PRINT = 1 << 2
P_MODIFY = 1 << 3
P_COPY = 1 << 4
P_ANNOTATE = 1 << 5
P_FILL = 1 << 8
P_ACCESSIBILITY = 1 << 9
P_ASSEMBLE = 1 << 10
P_PRINT_HIGH = 1 << 11


def _secrets_path() -> Path:
    """The machine's secrets file - the same one translate.py reads."""
    from . import translate
    return translate._secrets_path()


def _read() -> dict[str, Any]:
    try:
        path = _secrets_path()
        if path.is_file():
            raw = json.loads(path.read_bytes())
            return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def settings() -> dict[str, Any]:
    block = _read().get(SECTION)
    block = block if isinstance(block, dict) else {}
    return {
        "lock": block.get("lock", True) is not False,
        "allow_comments": block.get("allow_comments") is True,
        "owner_password": str(block.get("owner_password") or ""),
    }


def _owner_password() -> str:
    """The machine's owner password, made and saved on first use."""
    current = settings()["owner_password"]
    if current:
        return current
    made = secrets.token_urlsafe(32)
    raw = _read()
    block = raw.get(SECTION) if isinstance(raw.get(SECTION), dict) else {}
    block["owner_password"] = made
    raw[SECTION] = block
    path = _secrets_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Unsaved is still a valid lock - only this run's files share it.
        pass
    return made


def permissions(allow_comments: bool = False) -> int:
    """Print (full quality) and screen readers; nothing that changes the file."""
    flags = P_PRINT | P_PRINT_HIGH | P_ACCESSIBILITY
    if allow_comments:
        flags |= P_ANNOTATE
    return flags


def is_locked(path: str | Path) -> bool:
    from pypdf import PdfReader
    return bool(PdfReader(str(path)).is_encrypted)


def lock(path: str | Path) -> Path:
    """Protect the finished PDF in place and return it.

    Abraj: AES-256 (below). Eshraqat (2026-10-17): a different, ordinary-looking
    protection - metadata scrub + an invisible certification signature, never
    AES - so its documents do not read as made by the same tool. See
    `backend/pdf_certify.py`. This is the single final-protection step every
    generator calls, so the profile split lives here and nowhere else.

    Written to a temporary file beside it and swapped in, so a failure never
    leaves half a PDF where a document was. An already-locked file is read with
    the empty user password and locked again with the current settings.
    """
    from pypdf import PdfReader, PdfWriter

    from . import config

    path = Path(path)
    if not path.is_file():
        return path
    if config.ACTIVE_PROFILE == "eshraqat":
        from . import pdf_certify
        return pdf_certify.finish_eshraqat(path)
    cfg = settings()
    if not cfg["lock"]:
        return path
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter(clone_from=reader)
    writer.encrypt(user_password="", owner_password=_owner_password(),
                   permissions_flag=permissions(cfg["allow_comments"]),
                   algorithm="AES-256")
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
