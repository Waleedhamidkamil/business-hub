"""
Counter-sign a client's LPO - stamp it where the user pointed, and write a
SEPARATE acknowledged copy to send back (2026-07-31).

Why this is not `signing.py`: that module bakes OUR signature into documents we
RENDER, choosing the placement itself (random jitter inside a known slot). Here
the document is the client's, already exists as a PDF or a scan, and we have no
idea where their signature block is - so the user points at it in the viewer and
we overlay exactly there.

How, without a new dependency:
  1. The base becomes a PDF (an image LPO is wrapped onto one A4 page).
  2. For each page carrying placements, an overlay page is RENDERED BY THE SAME
     headless Chrome the whole app already uses, sized to that exact page, with
     the signature/stamp images absolutely positioned.
  3. pypdf merges overlay onto base. A Chrome-rendered page paints no background,
     so the client's content shows through - verified, not assumed.

The client's original file is never modified: an acknowledgement they might later
dispute has to remain checkable against the file they actually sent.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from . import config, pdf_engine, pdf_lock, signing

# Placement kinds the viewer can emit.
_SIG = "sig"
_STAMP = "stamp"

PT_PER_MM = 72.0 / 25.4


def _asset_data_uri(kind: str, sig_index: int = 1) -> str | None:
    """The signature or stamp image, as the generators embed it."""
    sigs = signing._sig_dir()
    if kind == _STAMP:
        return signing._image_to_data_uri(sigs / signing.STAMP_FILE)
    name = signing.SIG_FILES.get(int(sig_index or 1), signing.SIG_FILES[1])
    return signing._image_to_data_uri(sigs / name)


def _image_base_pdf(src: Path, dest_dir: Path) -> Path:
    """Wrap an image LPO onto a single A4 page so it can be overlaid like a PDF."""
    raw = base64.b64encode(src.read_bytes()).decode("ascii")
    mime = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
    html = (
        "<html><head><style>"
        "@page{size:A4;margin:0}html,body{margin:0;padding:0}"
        ".w{width:210mm;height:297mm;display:flex;align-items:center;"
        "justify-content:center;overflow:hidden}"
        ".w img{max-width:100%;max-height:100%;object-fit:contain}"
        "</style></head><body><div class='w'>"
        f"<img src='data:{mime};base64,{raw}'></div></body></html>"
    )
    return Path(pdf_engine.write_pdf(html, dest_dir / f"{src.stem}._base.pdf"))


def _overlay_html(width_pt: float, height_pt: float, marks: list[dict]) -> str:
    """One transparent page, sized to the target page, with the marks placed.

    Positions arrive as fractions of the page with y measured from the TOP (what
    the viewer sees). Chrome lays out from the top-left too, so rendering the
    overlay at the same page size makes the two line up with no coordinate
    conversion - and nothing for a zoom level to corrupt.
    """
    w_mm = width_pt / PT_PER_MM
    h_mm = height_pt / PT_PER_MM
    items = []
    for m in marks:
        uri = m.get("_data_uri")
        if not uri:
            continue
        left = float(m.get("x", 0)) * 100.0
        top = float(m.get("y", 0)) * 100.0
        wpc = float(m.get("w", 0.2)) * 100.0
        hpc = float(m.get("h", 0.1)) * 100.0
        rot = float(m.get("rot", 0) or 0)
        transform = f"transform:rotate({rot}deg);" if rot else ""
        items.append(
            f"<img src='{uri}' style='position:absolute;left:{left:.4f}%;"
            f"top:{top:.4f}%;width:{wpc:.4f}%;height:{hpc:.4f}%;"
            f"object-fit:contain;{transform}'>"
        )
    return (
        "<html><head><style>"
        f"@page{{size:{w_mm:.3f}mm {h_mm:.3f}mm;margin:0}}"
        "html,body{margin:0;padding:0;background:transparent}"
        f".pg{{position:relative;width:{w_mm:.3f}mm;height:{h_mm:.3f}mm}}"
        "</style></head><body><div class='pg'>" + "".join(items) + "</div></body></html>"
    )


def _resolve_marks(placements: list[dict]) -> dict[int, list[dict]]:
    """Group placements by page and attach the image each one needs."""
    by_page: dict[int, list[dict]] = {}
    for p in placements:
        kind = (p.get("kind") or _SIG).strip().lower()
        if kind not in (_SIG, _STAMP):
            continue
        uri = _asset_data_uri(kind, p.get("sig_index", 1))
        if not uri:                      # asset never uploaded - skip, don't fake it
            continue
        page = max(0, int(p.get("page") or 0))
        by_page.setdefault(page, []).append({**p, "_data_uri": uri})
    return by_page


def write_acknowledged(src: Path, dest: Path, placements: list[dict]) -> dict:
    """Write `dest` = the client's LPO with our signature/stamp where asked.

    Returns `{"path", "applied", "requested"}`. The counts matter: a mark can be
    dropped for good reasons (no uploaded asset, a page index past the end of the
    document), and silently writing a file with fewer marks than asked for is
    exactly the failure that is hard to notice - you only find out when the
    client asks why the stamp is missing.
    """
    from pypdf import PdfReader, PdfWriter

    src = Path(src)
    tmp_base: Path | None = None
    if src.suffix.lower() != ".pdf":
        tmp_base = _image_base_pdf(src, dest.parent)
        reader = PdfReader(str(tmp_base))
    else:
        reader = PdfReader(str(src))
        if reader.is_encrypted:
            reader.decrypt("")

    by_page = _resolve_marks(placements)
    if not by_page:
        raise ValueError("no usable placement - is the signature/stamp uploaded?")

    requested = sum(len(v) for v in by_page.values())
    applied = 0
    writer = PdfWriter()
    tmp_overlays: list[Path] = []
    try:
        for i, page in enumerate(reader.pages):
            marks = by_page.get(i)
            if marks:
                # A scanned page often carries /Rotate; bake it into the content
                # first so the mediabox matches what the user actually saw.
                if getattr(page, "rotation", 0):
                    try:
                        page.transfer_rotation_to_content()
                    except Exception:
                        pass
                w = float(page.mediabox.width)
                h = float(page.mediabox.height)
                ov_path = dest.parent / f"{dest.stem}._ov{i}.pdf"
                pdf_engine.write_pdf(_overlay_html(w, h, marks), ov_path)
                tmp_overlays.append(ov_path)
                ov = PdfReader(str(ov_path)).pages[0]
                page.merge_page(ov)
                applied += len(marks)
            writer.add_page(page)
        with open(dest, "wb") as fh:
            writer.write(fh)
        # Our stamp is now on it - locked like every PDF we make (pdf_lock.py).
        pdf_lock.lock(dest)
    finally:
        writer.close()
        for p in tmp_overlays:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        if tmp_base:
            try:
                tmp_base.unlink(missing_ok=True)
            except OSError:
                pass
    return {"path": str(dest), "applied": applied, "requested": len(placements or [])}


def default_mark_size(kind: str) -> dict[str, float]:
    """Starting box for a freshly dropped mark, as a fraction of an A4 page.

    Read from the LIVE settings (`signing.get_settings()["size"]`, which the user
    can change in Settings) so a counter-signed LPO carries the same footprint as
    every other document we sign - stamp 46.5x29mm, signature 55x22mm by default.
    """
    try:
        size = (signing.get_settings() or {}).get("size") or {}
    except Exception:
        size = {}
    fallback = signing.DEFAULTS.get("size") or {}

    def mm(key, default):
        try:
            return float(size.get(key, fallback.get(key, default)))
        except (TypeError, ValueError):
            return float(default)

    if kind == _STAMP:
        w, h = mm("stamp_w_mm", 46.5), mm("stamp_h_mm", 29.0)
    else:
        w, h = mm("sig_w_mm", 55.0), mm("sig_h_mm", 22.0)
    return {"w": w / 210.0, "h": h / 297.0}
