"""Content-addressed store for remembered item pictures (2026-08-05).

Autocomplete remembers what an item cost and how it was described; it now also
remembers what it LOOKS like. The obvious way - keeping the base64 data URI in
`quote_items.data_json` - was tried and deliberately removed: an equipment
quote saved 200KB+ per row and the shared library ballooned. That comment still
stands at the call site in `api.save_quote`.

So the bytes live on disk, named by their own SHA-256, and the item remembers
only the short reference. Two consequences worth stating:

  * the same photo used on fifty items is stored ONCE - the hash IS the
    identity, so dedup is automatic rather than something to maintain;
  * the shared database stays small, which matters because it is copied whole
    by the backup/restore paths in data_manager.

Files sit beside `library.db` under `data/shared/`, because the item memory
they belong to is shared across company profiles.

SECURITY NOTE: a ref is read back out of the database and turned into a file
path. `_safe_name` therefore accepts a bare hex digest plus a known extension
and nothing else, and `path_for` re-checks that the resolved path is really
inside the store. Without both, a crafted or corrupted ref reads arbitrary
files off the disk and hands them to the UI.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from . import profile_manager

# The cropper emits JPEG; the others are here because a pasted or dropped image
# reaches the store unmodified if it never passes through the cropper.
_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}
_EXT_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif", ".bmp": "image/bmp"}

_DATA_URI_RE = re.compile(r"^data:(image/[a-z]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_REF_RE = re.compile(r"^[0-9a-f]{64}(\.[a-z]{3,4})$")

# A cropped picture is ~40-120KB. Anything far above that is not a photo of a
# part; refusing it keeps one bad paste from filling the disk.
MAX_BYTES = 4 * 1024 * 1024


def store_dir() -> Path:
    """`data/shared/item_images` - beside library.db, shared across profiles."""
    return profile_manager.shared_db_path().parent / "item_images"


def _safe_name(ref: str) -> str | None:
    """A ref is a 64-char hex digest plus a known extension. Nothing else.

    Rejecting everything else is what stops `../../settings.json` (or any other
    traversal) from becoming a readable path.
    """
    name = str(ref or "").strip().lower()
    m = _REF_RE.match(name)
    if not m or m.group(1) not in _EXT_MIME:
        return None
    return name


def path_for(ref: str) -> Path | None:
    """Absolute path for a ref, or None if it is not a legitimate one.

    Belt AND braces: the name is validated, and the resolved path is then
    confirmed to sit inside the store. Either check alone would probably do;
    together they mean a mistake in one is not a file-disclosure bug.
    """
    name = _safe_name(ref)
    if not name:
        return None
    root = store_dir().resolve()
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def store(data_uri: str) -> str | None:
    """Write a data URI into the store; return its ref, or None.

    Idempotent by construction: identical bytes hash to the same name, so
    re-saving a quote that uses the same photo writes nothing new.
    """
    m = _DATA_URI_RE.match(str(data_uri or "").strip())
    if not m:
        return None
    mime = m.group(1).lower()
    ext = _MIME_EXT.get(mime)
    if not ext:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except Exception:
        return None
    if not raw or len(raw) > MAX_BYTES:
        return None

    ref = hashlib.sha256(raw).hexdigest() + ext
    target = path_for(ref)
    if target is None:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():          # same bytes, same name - already there
            target.write_bytes(raw)
    except OSError:
        return None
    return ref


def load(ref: str) -> str:
    """A ref back as a data URI, or "" when it is missing or not valid.

    Returns "" rather than raising: a picture that has been deleted from disk
    must degrade to "no picture", never to a broken autocomplete.
    """
    path = path_for(ref)
    if path is None or not path.exists():
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    mime = _EXT_MIME.get(path.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def store_size() -> dict:
    """{files, bytes} - for the maintenance screen to report."""
    root = store_dir()
    if not root.exists():
        return {"files": 0, "bytes": 0}
    files = [p for p in root.iterdir() if p.is_file()]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
