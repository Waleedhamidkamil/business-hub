"""
Eshraqat's finished-PDF protection (Waleed, 2026-10-17).

Abraj PDFs are locked with AES-256 (`pdf_lock.lock`). Eshraqat must NOT read as
produced by the same tool, so it uses a different, ordinary-looking protection:

    1. scrub the `/Info` metadata that names the renderer - Chrome writes
       `/Creator: ...HeadlessChrome...` and `/Producer: Skia/PDF`, identical on
       every document; those two plus `/Author` are blanked. (Chrome's
       `--print-to-pdf` writes no XMP packet, so `/Info` is the whole story.)
    2. apply an INVISIBLE certification signature with Waleed's own certificate
       (a .pfx), DocMDP "no changes allowed" - the same thing Acrobat's
       "Certify (Invisible Signature)" does. The document then reads as a
       normally-signed PDF, not an app-locked one, and is still sealed against
       editing.

No AES is applied to Eshraqat, so nothing ties its files together through a
shared owner password.

Config lives under "eshraqat_sign" in the git-ignored secrets.json (the same
file `pdf_lock` and `translate` use):

    "eshraqat_sign": {
        "enabled": true,
        "pfx_path": "D:\\\\ESHRAQATZ\\\\CSSP\\\\pdf sign\\\\AymenAli.pfx",
        "passphrase": "..."
    }

With `enabled` off or no usable .pfx, Eshraqat PDFs are still metadata-scrubbed
but not signed (and never AES-locked). Called LAST, from `pdf_lock.lock`, after
every other write to the file - a certification signature seals the bytes, so
nothing may touch the PDF afterwards.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SECTION = "eshraqat_sign"

# Fields Chrome fills that fingerprint the renderer. /Title (the document
# number) and the dates are ordinary and kept.
_SCRUB_KEYS = ("/Creator", "/Producer", "/Author")


def _secrets_path() -> Path:
    from . import translate
    return translate._secrets_path()


def _read_secrets() -> dict[str, Any]:
    try:
        path = _secrets_path()
        if path.is_file():
            raw = json.loads(path.read_bytes())
            return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def sign_settings() -> dict[str, Any]:
    block = _read_secrets().get(SECTION)
    block = block if isinstance(block, dict) else {}
    return {
        "enabled": block.get("enabled") is True,
        "pfx_path": str(block.get("pfx_path") or ""),
        "passphrase": str(block.get("passphrase") or ""),
    }


def scrub_metadata(path: str | Path) -> Path:
    """Blank the `/Info` entries that name the rendering software, in place.

    Writes to a temporary file beside the target and swaps it in, so a failure
    never leaves half a PDF. Keeps `/Title` and the dates.
    """
    from pypdf import PdfReader, PdfWriter

    path = Path(path)
    if not path.is_file():
        return path
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({k: "" for k in _SCRUB_KEYS})
    tmp = path.with_suffix(".scrub.tmp")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(path)
    return path


def _silence_vendor() -> None:
    """Stop pyHanko writing its own name into `/Producer` (and XMP).

    `update_info_dict` always forces `/Producer` to include `model.VENDOR`
    ('pyHanko <ver>'); an empty VENDOR is a substring of any string, so the
    append is skipped and the scrubbed empty producer survives. Setting it is
    idempotent and only affects PDFs this app signs.
    """
    from pyhanko.pdf_utils.metadata import info as _info
    from pyhanko.pdf_utils.metadata import model as _model
    _model.VENDOR = ""
    _info.model.VENDOR = ""


def certify(path: str | Path, pfx_path: str, passphrase: str) -> Path:
    """Seal `path` with an invisible, no-changes certification signature.

    Raises on any failure (bad passphrase, unreadable .pfx) - the caller decides
    whether an unsigned-but-scrubbed PDF is acceptable; it does NOT silently ship
    an unsigned one.
    """
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import MDPPerm

    path = Path(path)
    _silence_vendor()
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=str(pfx_path), passphrase=passphrase.encode("utf-8"))
    if signer is None:
        raise ValueError("could not load the signing certificate (passphrase?)")
    meta = signers.PdfSignatureMetadata(
        field_name="Sig1", certify=True, docmdp_permissions=MDPPerm.NO_CHANGES)
    with path.open("rb+") as fh:
        writer = IncrementalPdfFileWriter(fh)
        signers.sign_pdf(writer, meta, signer=signer, in_place=True)
    return path


def finish_eshraqat(path: str | Path) -> Path:
    """The Eshraqat protection step: scrub, then certify when configured.

    Always scrubs the renderer fingerprint. Signs only when `eshraqat_sign` is
    enabled with a usable .pfx; otherwise the PDF ships scrubbed and unsigned
    (and never AES-locked). Never applies AES-256 to an Eshraqat document.
    """
    path = Path(path)
    scrub_metadata(path)
    cfg = sign_settings()
    if cfg["enabled"] and cfg["pfx_path"] and Path(cfg["pfx_path"]).is_file():
        certify(path, cfg["pfx_path"], cfg["passphrase"])
    return path
