"""
Phase K.1 - Digital signatures & stamp.

Three things live here:
  1. The settings model - uploaded sig images (×3) + stamp (×1), labels,
     size and randomness tuning.
  2. The per-doc overlay payload - which slot gets which signature + stamp,
     plus the random offset/rotation values that were rolled when the user
     pressed Apply.
  3. Image read helpers - return data URIs to inject into the PDF
     templates (so the rendering is self-contained / portable).

Storage layout (relative to the active company profile):
   assets/sigs/sig_1.png
   assets/sigs/sig_2.png
   assets/sigs/sig_3.png
   assets/sigs/stamp.png

Per-doc signing payload lives in the generic doc_meta table (see
database._init_doc_meta) keyed by (doc_type, doc_no, "signing").
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import config, sqlite_policy


# --------------------------------------------------------------------------- #
# Defaults (overridable via data/settings.json → "signing" key).
# --------------------------------------------------------------------------- #
DEFAULTS: dict[str, Any] = {
    "signatures": [
        {"label": "", "has_image": False},
        {"label": "", "has_image": False},
        {"label": "", "has_image": False},
    ],
    "stamp": {"has_image": False},

    # 1.83" × 1.14"  ≈  46.5mm × 29mm  (the user's spec for the stamp footprint).
    # Signature is wider and shorter - sits below the stamp, crossing the
    # signature line.
    "size": {
        "stamp_w_mm": 46.5,
        "stamp_h_mm": 29.0,
        "sig_w_mm":   55.0,
        "sig_h_mm":   22.0,
    },

    # Random offsets rolled at Apply-time.
    # dx/dy are ±half-range mm; rotation is ±half-range degrees.
    "randomness": {
        "stamp_dx_mm":  4.0,
        "stamp_dy_mm":  3.0,
        "stamp_rot":    4.0,
        "sig_dx_mm":    3.0,
        "sig_dy_mm":    2.0,
        "sig_rot":      2.0,
        # Optional asymmetry: stamp can sit slightly above or slightly
        # overlapping the signature. Vertical separation in mm.
        "stamp_vs_sig_y_mm": 4.0,
    },

    # Auto-sign (2026-07-24) - every save signs the document unless turned off
    # in Settings. `sig_index`/`stamp` are the defaults applied to a document
    # that has no signature yet; placement randomness is re-rolled every save.
    "auto": {
        "enabled":   True,
        "sig_index": 1,
        "stamp":     True,
    },
}


SIG_FILES = {
    1: "sig_1.png",
    2: "sig_2.png",
    3: "sig_3.png",
}
STAMP_FILE = "stamp.png"


def _sig_dir() -> Path:
    return config.ASSETS_DIR / "sigs"


# --------------------------------------------------------------------------- #
# Settings - read / write
# --------------------------------------------------------------------------- #

def _user_signing() -> dict[str, Any]:
    """Pull the 'signing' sub-section from data/settings.json (if any)."""
    s = config.load_user_settings() or {}
    return s.get("signing") or {}


def get_settings() -> dict[str, Any]:
    """
    Merged signing settings (defaults + user overrides) + a `has_image` flag
    on each slot so the frontend knows whether to show a thumbnail or an
    upload affordance.
    """
    user = _user_signing()
    out  = json.loads(json.dumps(DEFAULTS))  # deep copy

    # Labels are user-defined; nothing to merge from defaults.
    if isinstance(user.get("signatures"), list):
        for i, entry in enumerate(user["signatures"]):
            if i >= len(out["signatures"]): break
            if isinstance(entry, dict):
                out["signatures"][i]["label"] = str(entry.get("label", "") or "")

    # Override sizes / randomness if the user customized them.
    for key in ("size", "randomness"):
        if isinstance(user.get(key), dict):
            for k, v in user[key].items():
                if k in out[key]:
                    try: out[key][k] = float(v)
                    except (TypeError, ValueError): pass

    # Auto-sign block - booleans + an int slot, so it can't go through float().
    if isinstance(user.get("auto"), dict):
        ua = user["auto"]
        if "enabled" in ua:
            out["auto"]["enabled"] = bool(ua["enabled"])
        if "stamp" in ua:
            out["auto"]["stamp"] = bool(ua["stamp"])
        if "sig_index" in ua:
            try:
                idx = int(ua["sig_index"])
            except (TypeError, ValueError):
                idx = None
            out["auto"]["sig_index"] = idx if idx in (1, 2, 3) else None

    # has_image flag follows the filesystem.
    for i, fname in SIG_FILES.items():
        path = _sig_dir() / fname
        out["signatures"][i - 1]["has_image"] = path.exists()
        out["signatures"][i - 1]["data_uri"] = _image_to_data_uri(path)
    stamp_path = _sig_dir() / STAMP_FILE
    out["stamp"]["has_image"] = stamp_path.exists()
    out["stamp"]["data_uri"] = _image_to_data_uri(stamp_path)

    return out


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Persist user-editable signing settings (labels + sizes + randomness).
    The image files themselves are handled by `upload_*` below.
    """
    existing = config.load_user_settings() or {}
    section = dict(existing.get("signing") or {})

    if isinstance(payload.get("signatures"), list):
        normalized = []
        for entry in payload["signatures"][:3]:
            if isinstance(entry, dict):
                normalized.append({"label": str(entry.get("label", "") or "")})
            else:
                normalized.append({"label": ""})
        section["signatures"] = normalized

    for key in ("size", "randomness"):
        if isinstance(payload.get(key), dict):
            section[key] = {k: float(v) for k, v in payload[key].items()
                            if isinstance(v, (int, float, str)) and str(v).strip()}

    # Auto-sign block (2026-07-24) - stored as-is; get_settings() normalizes it.
    if isinstance(payload.get("auto"), dict):
        a = payload["auto"]
        auto = dict(section.get("auto") or {})
        if "enabled" in a:
            auto["enabled"] = bool(a["enabled"])
        if "stamp" in a:
            auto["stamp"] = bool(a["stamp"])
        if "sig_index" in a:
            try:
                idx = int(a["sig_index"])
            except (TypeError, ValueError):
                idx = None
            auto["sig_index"] = idx if idx in (1, 2, 3) else None
        section["auto"] = auto

    existing["signing"] = section
    config.save_user_settings(existing)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Image uploads (data-URI in, PNG file out - same pattern as upload_logo)
# --------------------------------------------------------------------------- #

_IMG_DATA_URI_RE = re.compile(r"^data:image/(png|jpeg|jpg);base64,(.+)$",
                              re.IGNORECASE | re.DOTALL)


def _write_data_uri(data_uri: str, dest: Path) -> dict[str, Any]:
    m = _IMG_DATA_URI_RE.match(data_uri or "")
    if not m:
        return {"ok": False, "error": "Expected a PNG or JPEG data URI."}
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception as e:
        return {"ok": False, "error": f"Base64 decode failed: {e}"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {"ok": True, "bytes": len(raw)}


def upload_signature(slot: int, data_uri: str) -> dict[str, Any]:
    if slot not in SIG_FILES:
        return {"ok": False, "error": f"Bad signature slot: {slot}"}
    return _write_data_uri(data_uri, _sig_dir() / SIG_FILES[slot])


def upload_stamp(data_uri: str) -> dict[str, Any]:
    return _write_data_uri(data_uri, _sig_dir() / STAMP_FILE)


def delete_signature(slot: int) -> dict[str, Any]:
    if slot not in SIG_FILES:
        return {"ok": False, "error": f"Bad signature slot: {slot}"}
    p = _sig_dir() / SIG_FILES[slot]
    if p.exists():
        try: p.unlink()
        except OSError as e: return {"ok": False, "error": str(e)}
    return {"ok": True}


def delete_stamp() -> dict[str, Any]:
    p = _sig_dir() / STAMP_FILE
    if p.exists():
        try: p.unlink()
        except OSError as e: return {"ok": False, "error": str(e)}
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Image read helpers for templates
# --------------------------------------------------------------------------- #

def _image_to_data_uri(p: Path) -> str | None:
    if not p.exists(): return None
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    # PNG is the only thing we write, but tolerate any image MIME for sanity.
    mime = "image/png"
    ext = p.suffix.lower()
    if ext in (".jpg", ".jpeg"): mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def get_assets() -> dict[str, Any]:
    """
    Snapshot of every available image as data URIs, plus active settings.
    Used by the PDF generators to inline the images in the rendered HTML.
    """
    settings = get_settings()
    signatures = []
    for i, fname in SIG_FILES.items():
        signatures.append({
            "slot": i,
            "label": settings["signatures"][i - 1]["label"],
            "data_uri": _image_to_data_uri(_sig_dir() / fname),
        })
    return {
        "signatures": signatures,
        "stamp_data_uri": _image_to_data_uri(_sig_dir() / STAMP_FILE),
        "size": settings["size"],
    }


# --------------------------------------------------------------------------- #
# Per-doc signing payload - roll the random offsets when Apply is pressed.
# --------------------------------------------------------------------------- #
# Slots per doc:
#   po:  ["main"]
#   qut: ["main"]
#   dn:  ["recipient", "supplier"]
#   inv: ["main"]
# --------------------------------------------------------------------------- #

DOC_SLOTS: dict[str, list[str]] = {
    "po":  ["main"],
    "qut": ["main"],
    # Delivery Notes only have one signable slot for US - the Supplier box.
    # The Recipient box is left blank for the client to sign in pen when they
    # take delivery; we never sign it digitally.
    "dn":  ["supplier"],
    "inv": ["main"],
}


def slots_for(doc_type: str) -> list[str]:
    return list(DOC_SLOTS.get(doc_type, ["main"]))


def _rand_pm(half_range: float) -> float:
    """Uniform in [-half_range, +half_range], rounded to 0.01."""
    if half_range <= 0: return 0.0
    return round(random.uniform(-half_range, half_range), 2)


def roll_overlay(doc_type: str,
                 selections: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    """
    Take the user's Sign-modal picks and produce a stable overlay payload
    with random offsets/rotation baked in.

    `selections`: { <slot>: {"sig_index": 1|2|3|null, "stamp": True/False} }
    Returns:     { "slots": { <slot>: {...full payload...} } }

    The returned object is what gets saved to doc_meta. It includes
    `dx_mm`/`dy_mm`/`rot_deg` for stamp + signature so the renderer doesn't
    need to know about randomness - it just applies what's in the payload.
    """
    settings = get_settings()
    r = settings["randomness"]
    out_slots: dict[str, Any] = {}
    for slot in slots_for(doc_type):
        pick = (selections or {}).get(slot) or {}
        sig_index = pick.get("sig_index")
        try:
            sig_index = int(sig_index) if sig_index not in (None, "", "null") else None
        except (TypeError, ValueError):
            sig_index = None
        if sig_index is not None and sig_index not in (1, 2, 3):
            sig_index = None
        use_stamp = bool(pick.get("stamp"))

        if sig_index is None and not use_stamp:
            out_slots[slot] = None
            continue

        out_slots[slot] = {
            "sig_index": sig_index,
            "stamp": use_stamp,
            "stamp_dx_mm":  _rand_pm(r["stamp_dx_mm"]),
            "stamp_dy_mm":  _rand_pm(r["stamp_dy_mm"]),
            "stamp_rot":    _rand_pm(r["stamp_rot"]),
            "sig_dx_mm":    _rand_pm(r["sig_dx_mm"]),
            "sig_dy_mm":    _rand_pm(r["sig_dy_mm"]),
            "sig_rot":      _rand_pm(r["sig_rot"]),
            "stamp_vs_sig_y_mm": _rand_pm(r["stamp_vs_sig_y_mm"]),
        }
    return {"slots": out_slots, "size": dict(settings["size"])}


def _selections_from_overlay(overlay: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The user's per-slot picks (sig_index + stamp) out of a stored overlay,
    discarding the rolled placement values."""
    out: dict[str, dict[str, Any]] = {}
    for slot, v in ((overlay or {}).get("slots") or {}).items():
        if isinstance(v, dict):
            out[slot] = {"sig_index": v.get("sig_index"), "stamp": bool(v.get("stamp"))}
    return out


def overlay_for_save(doc_type: str,
                     existing: dict[str, Any] | None,
                     force: bool = False) -> dict[str, Any] | None:
    """The overlay to bake into the PDF on save (auto-sign, 2026-07-24).

    Two rules, in order:
      1. If the document already carries picks, RE-ROLL their placement. Every
         save re-randomizes position/rotation (the user's explicit choice) while
         keeping which signature and whether the stamp were selected, so a
         deliberate pick is never silently discarded.
      2. Otherwise, if auto-sign is on, apply the configured default (signature
         1 + stamp by default) to every slot this doc type signs. For a DN that
         is the SUPPLIER box only - the recipient box stays blank for the client
         to sign in pen (see DOC_SLOTS).

    Returns `existing` untouched when auto-sign is off, and refuses to produce a
    "signed" overlay for assets that do not exist - otherwise a document with no
    uploaded signature would read as signed while printing nothing.

    `force=True` signs even when auto-sign is switched off, and falls back to
    whichever assets DO exist. Used by "Apply sign & stamp" on an already-saved
    document, where the user asked for it explicitly.
    """
    picks = _selections_from_overlay(existing)
    if any(p.get("sig_index") or p.get("stamp") for p in picks.values()):
        return roll_overlay(doc_type, picks)

    settings = get_settings()
    auto = settings.get("auto") or {}
    if not force and not auto.get("enabled"):
        return existing

    sig_index = auto.get("sig_index")
    if sig_index not in (1, 2, 3):
        sig_index = None
    have_sig = bool(sig_index) and bool(
        settings["signatures"][sig_index - 1].get("has_image"))
    have_stamp = bool(auto.get("stamp")) and bool(settings["stamp"].get("has_image"))
    if force and not have_sig and not have_stamp:
        # Explicit request - use whatever is actually installed.
        for i in (1, 2, 3):
            if settings["signatures"][i - 1].get("has_image"):
                sig_index, have_sig = i, True
                break
        have_stamp = bool(settings["stamp"].get("has_image"))
    if not have_sig and not have_stamp:
        return existing

    picks = {slot: {"sig_index": sig_index if have_sig else None,
                    "stamp": have_stamp}
             for slot in slots_for(doc_type)}
    return roll_overlay(doc_type, picks)


# --------------------------------------------------------------------------- #
# Per-doc storage via the generic doc_meta key/value table.
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection:
    # Ensure the active profile's data dir exists before opening - mirrors
    # database._connect. Without this, signing on a freshly-activated profile
    # whose dir hasn't been touched yet fails with "unable to open database file".
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_policy.open_db(config.DB_PATH)
    return conn


_INIT_DONE = False

def _ensure_table() -> None:
    """Lazy init of the doc_meta table - idempotent, runs once per process."""
    global _INIT_DONE
    if _INIT_DONE: return
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_meta (
                doc_type TEXT NOT NULL,
                doc_no   TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT,
                PRIMARY KEY (doc_type, doc_no, key)
            )
        """)
    _INIT_DONE = True


def save_doc_overlay(doc_type: str, doc_no: str, overlay: dict[str, Any] | None) -> None:
    _ensure_table()
    doc_no = (doc_no or "").strip()
    if not doc_no: return
    payload = json.dumps(overlay or {}, ensure_ascii=False)
    with _connect() as conn:
        if overlay:
            conn.execute(
                "INSERT INTO doc_meta(doc_type, doc_no, key, value) VALUES(?, ?, 'signing', ?) "
                "ON CONFLICT(doc_type, doc_no, key) DO UPDATE SET value = excluded.value",
                (doc_type, doc_no, payload),
            )
        else:
            conn.execute(
                "DELETE FROM doc_meta WHERE doc_type = ? AND doc_no = ? AND key = 'signing'",
                (doc_type, doc_no),
            )


def overlay_is_signed(overlay: dict[str, Any] | None) -> bool:
    """True if an overlay payload actually places a signature or stamp on at
    least one slot (an overlay can exist but be empty if the user cleared it)."""
    if not overlay or not isinstance(overlay, dict):
        return False
    slots = overlay.get("slots") or {}
    for v in slots.values():
        if v and (v.get("sig_index") in (1, 2, 3) or v.get("stamp")):
            return True
    return False


def is_doc_signed(doc_type: str, doc_no: str) -> bool:
    """Whether a single saved document carries a signature/stamp overlay."""
    return overlay_is_signed(load_doc_overlay(doc_type, doc_no))


def signed_doc_nos(doc_type: str) -> set[str]:
    """Every doc_no of this type whose stored overlay actually signs it.
    One query - used by the Open lists and the Clients profile to badge rows."""
    _ensure_table()
    out: set[str] = set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT doc_no, value FROM doc_meta WHERE doc_type = ? AND key = 'signing'",
            (doc_type,),
        ).fetchall()
    for row in rows:
        try:
            ov = json.loads(row["value"]) if row["value"] else None
        except Exception:
            ov = None
        if overlay_is_signed(ov):
            out.add((row["doc_no"] or "").strip())
    return out


def load_doc_overlay(doc_type: str, doc_no: str) -> dict[str, Any] | None:
    _ensure_table()
    doc_no = (doc_no or "").strip()
    if not doc_no: return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM doc_meta WHERE doc_type = ? AND doc_no = ? AND key = 'signing'",
            (doc_type, doc_no),
        ).fetchone()
    if not row or not row["value"]: return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Render-time helper: produce per-slot HTML the templates can inject as-is.
# Keeps the placement math (transform / position) out of every template.
# --------------------------------------------------------------------------- #

def build_template_context(doc_type: str,
                           overlay: dict[str, Any] | None,
                           doc_no: str | None = None) -> dict[str, str]:
    """
    Convenience for generators: returns a dict
        { "sig_overlay_<slot>": "<html>", ... }
    that can be spread into the Jinja render call. If `overlay` is None and
    `doc_no` is provided, this loads any persisted overlay from doc_meta.
    """
    if overlay is None and doc_no:
        overlay = load_doc_overlay(doc_type, doc_no)
    assets = get_assets()
    ctx: dict[str, str] = {}
    for slot in slots_for(doc_type):
        ctx[f"sig_overlay_{slot}"] = render_overlay_html(overlay, slot, assets)
    return ctx


def _image_px(data_uri: str | None) -> tuple[int, int] | None:
    """(width, height) of a PNG data URI, read from its header; None otherwise."""
    if not data_uri or not data_uri.startswith("data:image/png;base64,"):
        return None
    try:
        head = base64.b64decode(data_uri.split(",", 1)[1][:44] + "==")[:24]
    except Exception:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    return (w, h) if w > 0 and h > 0 else None


def _ink_gap_mm(data_uri: str | None, box_w: float, box_h: float) -> float:
    """Empty space below the drawn image inside its box. The images use
    `object-fit: contain`, so a wide signature in a taller box is drawn
    centred with a gap above and below it."""
    px = _image_px(data_uri)
    if not px:
        return 0.0
    drawn_h = min(box_h, box_w * px[1] / px[0])
    return max(0.0, (box_h - drawn_h) / 2)


def _band(rolled: float, half_range: float, lo: float, hi: float) -> float:
    """Map a rolled offset in [-half_range, +half_range] onto [lo, hi]."""
    if half_range <= 0:
        return (lo + hi) / 2
    t = (max(-half_range, min(half_range, rolled)) + half_range) / (2 * half_range)
    return lo + t * (hi - lo)


# Where the ink must END, as a fraction of the printed name block's height
# (2026-10-07). Waleed: "make part of both sign and stamp overlap with the name
# so it cant be screenshoted". A screenshot of a stamp sitting clear above the
# name is a clean stamp; one that runs through the name carries the name with
# it. The block's height is the CSS variable --sig-text-h, set per template in
# its stylesheet (two lines on Abraj documents, one on Eshraqat), measured from
# the box bottom, which is where the name ends on every template.
STAMP_BAND = (0.55, 0.90)
SIGN_BAND = (0.10, 0.50)


def render_overlay_html(overlay: dict[str, Any] | None,
                        slot: str,
                        assets: dict[str, Any] | None = None) -> str:
    """
    Returns an HTML snippet that should be placed inside the signature box
    (which must be `position: relative`). Empty string when this slot has
    no signing assigned. The snippet contains an absolute-positioned wrapper
    with the stamp (rotated) and the signature in front of it, each with
    their pre-rolled random offsets.

    Both always run INTO the printed title/name (2026-10-07, see STAMP_BAND):
    the rolled vertical offset now chooses where inside that block the ink
    ends, instead of how far above it the stamp floats. Both are drawn with
    `mix-blend-mode: multiply`, so the name shows through the ink the way it
    does under a real rubber stamp.
    """
    if not overlay or "slots" not in overlay: return ""
    pick = (overlay["slots"] or {}).get(slot)
    if not pick: return ""

    assets = assets or get_assets()
    size = overlay.get("size") or assets["size"]
    stamp_w = float(size.get("stamp_w_mm", 46.5))
    stamp_h = float(size.get("stamp_h_mm", 29.0))
    sig_w   = float(size.get("sig_w_mm",   55.0))
    sig_h   = float(size.get("sig_h_mm",   22.0))
    # The rolled offsets' ranges, to map them onto the overlap bands.
    rand = DEFAULTS["randomness"]

    parts: list[str] = []
    parts.append('<div class="sig-overlay" aria-hidden="true">')

    if pick.get("stamp") and assets.get("stamp_data_uri"):
        # Stamp footprint is centered horizontally above the line.
        # Random offsets layer on top.
        dx = float(pick.get("stamp_dx_mm", 0))
        dy = float(pick.get("stamp_dy_mm", 0))
        rot = float(pick.get("stamp_rot", 0))
        frac = _band(dy, rand.get("stamp_dy_mm", 3.0), *STAMP_BAND)
        gap = _ink_gap_mm(assets["stamp_data_uri"], stamp_w, stamp_h)
        parts.append(
            f'<img class="sig-stamp" '
            f'src="{assets["stamp_data_uri"]}" '
            f'style="'
            f'  width:{stamp_w:.2f}mm; height:{stamp_h:.2f}mm;'
            f'  bottom:calc(var(--sig-text-h, 9mm) * {frac:.3f} - {gap:.2f}mm);'
            f'  left:calc(50% + {dx:.2f}mm);'
            f'  transform: translateX(-50%) rotate({rot:.2f}deg);'
            f'">'
        )

    sig_idx = pick.get("sig_index")
    if sig_idx in (1, 2, 3):
        # The signature scribble sits just below where the stamp ends,
        # crossing the signature line. Anchor its bottom near the line.
        sig_uri = None
        for s in (assets.get("signatures") or []):
            if s.get("slot") == sig_idx and s.get("data_uri"):
                sig_uri = s["data_uri"]; break
        if sig_uri:
            dx = float(pick.get("sig_dx_mm", 0))
            dy = float(pick.get("sig_dy_mm", 0))
            rot = float(pick.get("sig_rot", 0))
            # In front of the stamp, its lower edge crossing the name.
            frac = _band(dy, rand.get("sig_dy_mm", 2.0), *SIGN_BAND)
            gap = _ink_gap_mm(sig_uri, sig_w, sig_h)
            parts.append(
                f'<img class="sig-hand" '
                f'src="{sig_uri}" '
                f'style="'
                f'  width:{sig_w:.2f}mm; height:{sig_h:.2f}mm;'
                f'  bottom:calc(var(--sig-text-h, 9mm) * {frac:.3f} - {gap:.2f}mm);'
                f'  left:calc(50% + {dx:.2f}mm);'
                f'  transform: translateX(-50%) rotate({rot:.2f}deg);'
                f'">'
            )

    parts.append('</div>')
    return "".join(parts)
