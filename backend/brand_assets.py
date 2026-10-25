"""Profile-owned brand images encoded for self-contained HTML/PDF rendering."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from . import config


def image_data_uri(path: Path, required: bool = False) -> str | None:
    asset = Path(path)
    if not asset.is_file():
        if required:
            raise FileNotFoundError(f"Required {config.ACTIVE_PROFILE} profile asset is missing: {asset.name}")
        return None
    mime = mimetypes.guess_type(asset.name)[0] or "image/png"
    encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def logo_data_uri(required: bool = False) -> str | None:
    """Return only the active profile's logo; never fall back across companies."""
    for name in ("logo.png", "logo.jpg", "logo.jpeg"):
        path = config.ASSETS_DIR / name
        if path.is_file():
            return image_data_uri(path, required=True)
    if required:
        raise FileNotFoundError(
            f"Required {config.ACTIVE_PROFILE} profile asset is missing: logo.png"
        )
    return None


def iso_data_uri() -> str | None:
    return image_data_uri(config.ASSETS_DIR / "iso-certifications.png")
