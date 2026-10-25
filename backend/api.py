"""
Methods exposed to the webview's JavaScript via pywebview's `js_api`.
Every method here is callable from JS as `await window.pywebview.api.<name>(args)`.
"""
from __future__ import annotations

import base64
import hashlib
import os
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any

from . import (config, database, doc_settings, pdf_generator, po_numbering,
               quote_generator, quote_numbering,
               dn_generator, dn_numbering,
               inv_generator, inv_numbering,
               data_manager, signing, clients, analytics, insights, master_data,
               master_items, lpo_ack,
               spreadsheet_parser, profile_manager, profile_numbering, migrations,
               item_images, po_catalogue_review, master_doctor)


from . import boot_cache  # noqa: E402  (after the package imports above)
from . import number_claims  # noqa: E402
from . import activity_log  # noqa: E402

# The activity log sees exceptions a method catches and turns into
# {"ok": False, "error": ...}, so a hidden bug is logged as one (2026-10-23).
activity_log.watch_exceptions()

_PROFILE_SWITCH_LOCK = threading.RLock()
# Calls that touch no company data and must not queue behind the ones that
# do. gate_ready only tells the launch gate to open; held behind the
# dashboard's icon loads it arrived ~0.3 s late (measured 2026-10-02).
_UNLOCKED_CALLS = frozenset({"gate_ready"})


def _bridge_json_safe(value):
    """Recursively replace NaN/Infinity before pywebview serializes a result.

    JavaScript's JSON.parse rejects Python's non-standard Infinity/NaN tokens;
    when that happens pywebview never resolves the originating promise.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {key: _bridge_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bridge_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_bridge_json_safe(item) for item in value]
    return value


# The client used for work that has no client: a quick quotation for someone
# unrelated, a sample, a price check. It is a REAL record rather than a magic
# value, so it needs no special case anywhere - it numbers as NA, files under
# NA, groups in Insights and shows NA on the editor chip like any other client.
# The one thing it does differently is PRINT (Waleed, 2026-09-11): the client
# line comes out blank, because "N/A" on a page that leaves the building looks
# unfinished.
NO_CLIENT = "N/A"


def _is_no_client(name: Any) -> bool:
    from . import client_master
    text = str(name or "").strip()
    if not text:
        return False
    try:
        return client_master.canonical(text) == NO_CLIENT
    except Exception:
        return text.upper() in {"N/A", "NA"}


def _printable_document(payload: dict | None) -> dict:
    """Copy a document payload without app-only metadata.

    Also the one place the no-client record is turned into a blank client line.
    Every document type renders through here, so there is no second copy of this
    rule to forget.
    """
    out = {key: value for key, value in (payload or {}).items()
           if key != "reference"}
    if _is_no_client(out.get("client_name")):
        out["client_name"] = ""
    return out


def _claim_holder() -> str:
    """Who holds a document number while it is being saved (multi-user,
    2026-10-22). "" until there are user accounts - then every save in one app
    is the same holder, exactly as before."""
    return os.environ.get("BUSINESSHUB_USER", "")


def _number_taken(exc: "number_claims.NumberTaken") -> dict:
    """The answer when the number an editor proposed was taken a moment ago.
    The editors key on `number_taken` to fetch the next number."""
    return {"ok": False, "number_taken": True, "doc_no": exc.doc_no,
            "error": f"{exc.doc_no} was just taken by "
                     f"{exc.holder or 'another save'}. Save again to take the next number."}


def _known_client_error(client_name: Any, existing: dict | None = None) -> dict | None:
    """A document may only name a client that is ON FILE (Waleed, 2026-09-11).

    Free typing in the client box is how "JUNE CONSTRUCTION CO." ended up in a
    document number and how three quotations came to disagree with their own
    client. The editors offer to add the client on the spot; this is the check
    that cannot be walked around, so a save from anywhere obeys it.

    Returns None when the name is fine, else an error dict. `unknown_client` is
    what the editor keys its quick-add dialog on - a plain error string would
    make it guess.
    """
    from . import client_master
    name = str(client_name or "").strip()
    # An ALREADY SAVED document keeps the name it was saved with. Locking the
    # list must never make an existing document uneditable - one of the 180
    # documents on file names a client that is not in Master Data.
    if existing and str(existing.get("client_name") or "").strip() == name:
        return None
    if not name:
        return {"ok": False, "unknown_client": "",
                "error": "This document needs a client. Pick one from the list, "
                         f"or choose {NO_CLIENT} if it is not for a client."}
    try:
        found = client_master.get_record(name)
    except Exception:
        return None          # the client store is unreadable; do not block a save
    record = (found or {}).get("record") or {}
    if found.get("ok") and record.get("name") and not record.get("archived"):
        return None
    if record.get("archived"):
        return {"ok": False, "unknown_client": name, "archived_client": True,
                "error": f'"{record.get("name") or name}" is archived. Restore it in '
                         "Master Data > Clients to use it on a document."}
    return {"ok": False, "unknown_client": name,
            "error": f'"{name}" is not one of your clients yet.'}


def _format_date(d: date) -> str:
    """Format like the sample PO: D/M/YYYY (no zero padding)."""
    return f"{d.day}/{d.month}/{d.year}"



# Accepted band for a typed IQD-per-USD rate. Deliberately wide - the point is
# to catch a slip, not to have an opinion about the market. The damage a bad
# value does is quiet and wide: grand_total is derived from it and summed by
# analytics, the dashboard revenue series and the clients page, so a stray
# "15000" or a "1.5" would misprice an invoice everywhere at once with nothing
# on screen to show for it. Out-of-band falls back to the derived rate.
_PEG_MIN = 100.0
_PEG_MAX = 100_000.0


def _cost_to_usd_for_memory(raw: Any, currency: str, peg: float) -> float | None:
    """A remembered cost, always in USD - or None when there is nothing to store.

    The item memory is SHARED across every document of a type, so a cost typed
    into a dinar quote and replayed into a dollar one is wrong by the exchange
    rate. `remember_inv_item` already normalises `rate` for exactly this reason
    (see save_inv); costs never got the same treatment, which is why they are
    stored under a new key rather than fixed in place.
    """
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value <= 0:
        return None
    if (currency or "USD").strip().upper() == "IQD":
        rate = float(peg) if peg else config.IQD_PER_USD
        if rate <= 0:
            return None
        value = value / rate
    return round(value, 4)


def _item_memory_payload(row: dict, primary: str, image_keys: set,
                         cost_currency: str, cost_peg: float,
                         store_image=None) -> dict:
    """What gets remembered for ONE row, for next-time autocomplete.

    Extracted from save_quote so it can be tested directly. It was previously
    inline, and the test had to reimplement it - which meant the test passed
    happily while the real code was mutated out from under it.

    Two exclusions and one conversion:
      * the primary key is the lookup name, not part of the payload;
      * image columns are 200KB+ base64 data URIs and are NOT stored inline -
        that bloated the shared library badly enough to be removed once
        already. The bytes go to the content-addressed store instead and the
        item remembers a short `image_refs` map (see backend/item_images);
      * the raw `cost` is REPLACED by `cost_usd`. Costs are typed in USD or
        IQD and nothing recorded which, so replaying a remembered one blind
        could put 84,000 in a cell that should read 60. `cost_usd` is both the
        value and the marker: an entry without it predates 2026-08-05 and is
        not autofilled at all. Dropping the raw key is deliberate - leaving it
        alongside is an invitation to read it as dollars.
    """
    data = {k: v for k, v in row.items()
            if k != primary and k not in (image_keys or set())}

    # Pictures: keep a reference, never the bytes. `store_image` is injectable
    # so the payload can be tested without touching the real store.
    put = store_image if store_image is not None else item_images.store
    refs = {}
    for key in (image_keys or set()):
        ref = put(row.get(key, ""))
        if ref:
            refs[key] = ref
    if refs:
        data["image_refs"] = refs

    raw_cost = data.pop("cost", "")
    # The ROW's own currency wins over the document's (2026-08-06): a table can
    # mix them, so normalising the whole document at one rate would remember a
    # dinar line as though it were dollars.
    row_ccy = database.row_cost_currency(row, cost_currency)
    data.pop("cost_ccy", None)          # not a column worth remembering
    cost_usd = _cost_to_usd_for_memory(raw_cost, row_ccy, cost_peg)
    if cost_usd is not None:
        data["cost_usd"] = cost_usd
        # The legacy autocomplete needs a currency-free USD figure, but the
        # ONE Master Data record deliberately stores what the user typed and
        # which currency it was in. Before these two keys existed here, a new
        # saved item learned its text and picture but silently lost its cost.
        data["cost_native"] = _cost_to_usd_for_memory(
            raw_cost, "USD", cost_peg)
        data["cost_native_ccy"] = row_ccy

    # `rate` is the document column's name for the item's selling price.
    # Quotation/DN rates are USD; save_inv converts an IQD invoice's rates to
    # USD before calling this builder. Teach the Master Data record the same
    # canonical price on the item's first save while retaining `rate` for the
    # legacy autocomplete table.
    native_price = _cost_to_usd_for_memory(data.get("rate"), "USD", cost_peg)
    if native_price is not None:
        data["price_native"] = native_price
        data["price_native_ccy"] = "USD"
    return data


# The PO's item memory. A PO row has no cost column today, so this is about
# the picture: the bytes go to the content-addressed store and the item keeps a
# short `image_refs` map, exactly as a quotation's does. Deliberately NARROW -
# only the keys the four fixed `items` columns cannot hold - because the rest
# (description / unit / company) already round-trip through those columns and
# `qty` is per-document, not per-item.
def _po_item_payload(row: dict) -> dict:
    full = _item_memory_payload(row, "name", {"image"}, "USD", config.IQD_PER_USD)
    return {k: full[k] for k in ("image_refs", "cost_usd") if full.get(k)}


def _dn_item_payload(dn: dict):
    """Build the per-row item memory for one delivery note.

    Returns the callback `database.save_dn_record` invokes per row. The DN used
    to be the odd one out: quotations and invoices route their memory through
    `_item_memory_payload` and record `cost_usd`, while the DN stored the row
    verbatim - a bare `cost` in whatever currency the editor was in. The
    catalog ended up holding 2002, 504 and 49994 for items whose documents read
    1.43, 0.36 and 35.71, with nothing to say which was which.

    The currency comes from the PAYLOAD first: dn.js calls `set_cost_currency`
    AFTER this save returns, so a stored read is one save behind.
    """
    cost_ccy = (dn.get("cost_currency") or
                database.get_cost_currency("dn", (dn.get("dn_no") or "").strip()) or
                "USD").strip().upper()
    cost_peg = _sane_peg(dn.get("cost_iqd_per_usd")) or config.IQD_PER_USD
    image_keys = {c.get("key") for c in (dn.get("columns") or [])
                  if isinstance(c, dict) and c.get("kind") == "image" and c.get("key")}

    def build(row: dict) -> dict:
        return _item_memory_payload(row, "item", image_keys, cost_ccy, cost_peg)

    return build


def _sane_peg(value: Any) -> float | None:
    """A usable IQD-per-USD rate, or None to fall back to the derived one."""
    try:
        rate = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not (_PEG_MIN <= rate <= _PEG_MAX):
        return None
    return rate


def _same_dir(a: Path, b: Path) -> bool:
    """Windows-safe directory comparison (case-insensitive, trailing-slash safe)."""
    norm = lambda p: os.path.normcase(str(p).rstrip("\\/"))   # noqa: E731
    return norm(a) == norm(b)


def _edit_target_folder(old_pdf_path: Any, dated_folder: Path) -> tuple[Path, Path | None]:
    """Where an EDITED document's PDF belongs - and the file it leaves behind.

    Re-saving used to pin the PDF to wherever it already lived, while the DB row
    was written with the EDITED date's year+month. The right-hand list joins the
    two on the month (`editable = doc_no in db_rows`, e.g. `list_quotes_for_month`),
    so moving a document's date across a month boundary left it:
        * visible but VIEW-ONLY in the old month (file found, no DB row), and
        * missing entirely from the new month (DB row found, no file)
    i.e. editable from neither. Reported 2026-08-02 for QUT-MT-TDC-043.

    The file now follows the date. Any difference in the computed folder counts,
    not just the month, so changing the client or the document type relocates it
    too rather than stranding the PDF under the old name.

    Returns (folder_to_write_into, stale_file_to_remove_after_a_good_save).
    """
    old = Path(old_pdf_path) if old_pdf_path else None
    if old is None:
        return dated_folder, None
    if _same_dir(old.parent, dated_folder):
        return old.parent, None
    return dated_folder, old


def _drop_stale_pdf(stale: Path | None, new_path: Path) -> None:
    """Remove the pre-move copy - only once the new one is definitely on disk.

    Deliberately conservative: a failure here leaves a harmless duplicate, while
    getting it wrong deletes the only copy of a real document.
    """
    if not stale:
        return
    try:
        if _same_dir(stale, new_path) and os.path.normcase(str(stale)) == os.path.normcase(str(new_path)):
            return
        if not stale.exists() or not new_path.exists():
            return
        stale.unlink()
    except Exception:
        pass          # a leftover file is recoverable; a failed save is not


def _stage_incomplete_draft_pdf(pdf_path: Any) -> tuple[Path, Path] | None:
    """Atomically hide an old PDF before replacing its DB row with an app draft.

    Renaming first gives us both safety properties: the old PDF cannot be opened
    or copied once the draft save starts, and it can be restored if the DB write
    itself fails. After a good write `_finish_incomplete_draft_pdf` deletes the
    extension-changed temporary file.
    """
    raw = str(pdf_path or "").strip()
    if not raw:
        return None
    old = Path(raw)
    if not old.exists():
        return None
    if not old.is_file():
        raise ValueError(f"saved PDF path is not a file: {old}")
    retired = old.with_name(f".{old.name}.{os.getpid()}.incomplete-draft")
    counter = 1
    while retired.exists():
        retired = old.with_name(
            f".{old.name}.{os.getpid()}.{counter}.incomplete-draft")
        counter += 1
    old.replace(retired)
    return old, retired


def _finish_incomplete_draft_pdf(staged: tuple[Path, Path] | None) -> None:
    if staged and staged[1].exists():
        staged[1].unlink()


def _restore_incomplete_draft_pdf(staged: tuple[Path, Path] | None) -> None:
    if staged and staged[1].exists() and not staged[0].exists():
        staged[1].replace(staged[0])


def _parse_display_date(s: str) -> date | None:
    """
    Parse a user-facing date into a Python date. Accepts either
    D/M/YYYY (display format used by the editor inputs) or
    YYYY-MM-DD (ISO - what HTML <input type="date"> emits).
    Returns None on any failure so callers can fall back to today.
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # ISO first (YYYY-MM-DD) - only valid if the leading chunk is 4 digits.
    if len(s) >= 8 and s[4:5] in "-/." and s[:4].isdigit():
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
    import re
    parts = re.split(r"[/\-.]", s)
    if len(parts) != 3:
        return None
    try:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:        # 26 -> 2026 (best-effort)
            y += 2000
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def _attach_created_and_sort(entries: list[dict], db_rows: dict,
                             no_key: str) -> list[dict]:
    """Attach a `created_at` to each panel entry and return them newest-created
    first (powers the left panel's "last created first" order + day grouping).
    Editable docs use the DB `created_at`; view-only PDFs fall back to the file's
    modified time so they still slot into the timeline."""
    from datetime import datetime
    for e in entries:
        db = db_rows.get(e.get(no_key))
        ts = ""
        if db:
            ts = db.get("created_at") or db.get("updated_at") or ""
        if not ts:
            try:
                p = e.get("path")
                if p and os.path.exists(p):
                    ts = datetime.fromtimestamp(
                        os.path.getmtime(p)).isoformat(timespec="seconds")
            except Exception:
                ts = ""
        e["created_at"] = ts
    entries.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return entries


def _merge_app_only_drafts(entries: list[dict], db_rows: dict, no_key: str,
                           type_key: str, schemas: dict) -> list[dict]:
    """Merge DB-only incomplete-price drafts into a filesystem-backed panel.

    Normal panel rows originate from PDFs on disk. An app-only draft has no PDF
    by design, so it must be synthesized from its DB row. If an orphaned old PDF
    with the same number is still discoverable, the empty DB path wins and its
    actions remain suppressed.
    """
    seen: set[str] = set()
    for entry in entries:
        no = entry.get(no_key)
        if not no:
            continue
        seen.add(no)
        db = db_rows.get(no)
        incomplete = bool(db is not None and not str(db.get("pdf_path") or "").strip())
        entry["incomplete_price"] = incomplete
        if incomplete:
            entry["path"] = ""
            entry["filename"] = ""

    for no, db in db_rows.items():
        if no in seen or str(db.get("pdf_path") or "").strip():
            continue
        schema = schemas.get(db.get(type_key) or "", {}) or {}
        entries.append({
            no_key: no,
            "filename": "",
            "path": "",
            "client": db.get("client_name") or "",
            "client_name": db.get("client_name") or "",
            "type_code": schema.get("code") or "",
            "incomplete_price": True,
        })
    return entries


def _find_pdf_by_stem(root: Path, doc_no: str) -> str:
    """Exact filesystem fallback for a view-only document being overwritten."""
    wanted = str(doc_no or "").strip().casefold()
    if not wanted or not root.exists():
        return ""
    try:
        for path in root.rglob("*.pdf"):
            if path.stem.casefold() == wanted:
                return str(path)
    except OSError:
        pass
    return ""


def _dn_render_doc(dn_no: str) -> dict | None:
    """A saved DN mapped into the shape `dn_generator.render_dn_html` expects
    (rows→items, dn_date→date, + columns/layout/signing overlay) so its pages can
    be appended to an invoice PDF (2026-07-18). None if the DN no longer exists."""
    rec = database.load_dn(dn_no)
    if not rec:
        return None
    rec["items"] = rec.get("rows") or []
    rec["date"] = rec.get("dn_date") or ""
    rec["columns"] = database.get_doc_columns("dn", dn_no)
    rec["layout"] = database.get_doc_layout("dn", dn_no)
    rec["signing_overlay"] = signing.load_doc_overlay("dn", dn_no)
    return rec


def _po_stored_col_widths(po_no: str) -> dict | None:
    """PO column widths persisted at save time (2026-07-24), as render_pdf wants
    them. PO widths used to be sent per-save and never stored, which made any
    re-render fall back to template defaults - see `sign_saved_document`."""
    out = {}
    for c in (database.get_doc_columns("po", po_no) or []):
        key, width = c.get("key"), c.get("width")
        if key and width not in (None, ""):
            out[key] = width
    return out or None


def _rerender_signed(kind: str, doc_no: str, overlay: dict | None) -> str:
    """Re-render an ALREADY SAVED document's PDF in place, carrying `overlay`.

    Rebuilds each generator's payload from the DB record + the persisted
    per-document columns/layout, so the re-rendered file matches the original
    apart from the signature. Raises ValueError if the document is gone.
    """
    if kind == "po":
        rec = database.load_po(doc_no)
        if not rec:
            raise ValueError(f"purchase order not found: {doc_no}")
        out = Path(rec["pdf_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        pdf_generator.render_pdf(
            doc_no, rec.get("po_date") or "", rec.get("rows") or [], out,
            signing_overlay=overlay,
            col_widths=_po_stored_col_widths(doc_no),
            layout=database.get_doc_layout("po", doc_no))
        return str(out)

    spec = {
        "qut": (database.load_quote, "quote_date", "quote", "qut"),
        "dn":  (database.load_dn,    "dn_date",    "dn",    "dn"),
        "inv": (database.load_inv,   "inv_date",   "inv",   "inv"),
    }.get(kind)
    if not spec:
        raise ValueError(f"bad kind: {kind}")
    loader, date_field, col_kind, layout_kind = spec

    rec = loader(doc_no)
    if not rec:
        raise ValueError(f"document not found: {doc_no}")
    if not str(rec.get("pdf_path") or "").strip():
        raise ValueError(
            "PDF unavailable: complete the document pricing and save it first.")
    out = Path(rec["pdf_path"])
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = dict(rec)
    doc["items"] = rec.get("rows") or []
    doc["date"] = rec.get(date_field) or ""
    if col_kind:
        doc["columns"] = database.get_doc_columns(col_kind, doc_no)
    doc["layout"] = database.get_doc_layout(layout_kind, doc_no)
    doc["signing_overlay"] = overlay

    if kind == "qut":
        quote_generator.render_quote_pdf(doc, out)
    elif kind == "dn":
        dn_generator.render_dn_pdf(doc, out)
    else:                                     # "inv"
        doc["_dn_docs"] = _resolve_dn_docs(doc)   # keep appended DN pages
        inv_generator.render_inv_pdf(doc, out)
    return str(out)


def _inv_dn_refs(items: list) -> list[str]:
    """Order-preserved, de-duplicated `dn_ref` values from invoice item rows."""
    refs: list[str] = []
    for r in (items or []):
        ref = str((r or {}).get("dn_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


# Signed-copy types the invoice can carry. IMAGES ride inline as a data URI in
# the single headless-Chrome pass. PDFs cannot be spliced into HTML, so they are
# merged into the finished PDF afterwards (inv_generator._merge_signed_pdfs) -
# the invoice HTML carries a placeholder page that the merge swaps out, which is
# what keeps a PDF copy in its correct position among the other DN attachments.
_IMAGE_COPY_SUFFIXES = {".png", ".jpg", ".jpeg"}
_COPY_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _dn_signed_copy(dn_no: str) -> dict | None:
    """How the client's signed copy of this DN should be attached, or None.

    None means "fall back to re-rendering the DN": no copy stored, or the file
    has been moved away. A PDF that cannot be opened is reported as
    `reason="unreadable"` rather than silently dropped - validating it HERE,
    before anything renders, is what lets the merge step assume its input is
    sound.
    """
    rec = database.get_signed_copy("dn", dn_no)
    if not rec or not rec.get("exists"):
        return None
    path = Path(rec["path"])
    suffix = path.suffix.lower()

    if suffix in _IMAGE_COPY_SUFFIXES:
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return None
        return {"kind": "image", "filename": rec["filename"],
                "data_uri": f"data:{_COPY_MIME[suffix]};base64,{b64}"}

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            if reader.is_encrypted:            # a password-protected scan
                reader.decrypt("")             # empty owner password is common
            pages = len(reader.pages)
            if pages < 1:
                raise ValueError("no pages")
        except Exception:
            return {"kind": "skip", "reason": "unreadable",
                    "filename": rec["filename"]}
        return {"kind": "pdf", "filename": rec["filename"],
                "path": str(path), "pages": pages}

    return {"kind": "skip", "reason": "unsupported", "filename": rec["filename"]}


def _inv_attachment_dn_refs(inv: dict) -> list[str]:
    """Every DN whose pages this invoice's PDF should carry.

    A DN reaches an invoice two ways, and they attach on different terms:

      * `dn_ref` item lines (the DN picker) - always appended, signed copy or
        not: the user explicitly put that delivery note on the invoice.
      * `source_dn_no` - the direct workflow link "Make invoice" writes. Appended
        ONLY once a signed copy has been attached to that DN (2026-09-02,
        Waleed). The point of stapling a DN to an invoice is proving delivery,
        and a bare re-render proves nothing; gating on the copy also means the
        thousands of existing workflow-linked invoices keep exactly the page
        count they were sent with.
    """
    refs = _inv_dn_refs(inv.get("items") or [])
    source = _inv_source_dn(inv)
    if source and source not in refs and _dn_signed_copy(source):
        refs.append(source)
    return refs


def _inv_source_dn(inv: dict) -> str:
    """This invoice's workflow link, falling back to the STORED one.

    `invState.sourceDn` in inv.js is transient - it is set only by "Make
    invoice" on a DN and cleared by `loadInvForEdit`. So every preview, edit and
    re-save of an existing invoice sends `source_dn_no: null` even though the
    link is right there in the row. Reading the payload alone meant the attached
    delivery note appeared once, at first save, and vanished from every render
    afterwards (2026-09-02).

    `save_inv` already resolves the DB link this same way for the record it
    writes ("None preserves an existing link on re-save") - this keeps what gets
    RENDERED agreeing with what gets STORED, which is the whole point.
    """
    source = str(inv.get("source_dn_no") or "").strip()
    if source:
        return source
    inv_no = str(inv.get("inv_no") or "").strip()
    if not inv_no:
        return ""
    try:
        rec = database.load_inv(inv_no)
    except Exception:
        return ""
    return str((rec or {}).get("source_dn_no") or "").strip()


def _resolve_dn_docs(inv: dict) -> list[dict]:
    """Render-ready DN docs for the invoice's `dn_ref` lines, order-preserved and
    de-duplicated; missing DNs are dropped. Consumed by inv_generator's appender.

    2026-07-30 - when the client has returned a SIGNED copy of the DN, that copy
    is attached instead of a fresh re-render. The re-render only ever carries OUR
    supplier signature (`signing.DOC_SLOTS["dn"] == ["supplier"]` leaves the
    recipient box blank for the client's pen), so an invoice used to go out
    looking as though the delivery had never been acknowledged. The scan is a
    superset: it is the copy we already signed, plus theirs.

    Images embed directly; PDFs are marked for the post-render merge and carry a
    `signed_copy_token` so the merge can find the placeholder page they belong
    at, instead of guessing a page index.
    """
    docs = []
    for ref in _inv_attachment_dn_refs(inv):
        doc = _dn_render_doc(ref)
        if not doc:
            continue
        copy = _dn_signed_copy(ref)
        if copy:
            doc["signed_copy_name"] = copy.get("filename")
            if copy["kind"] == "image":
                doc["signed_copy_image"] = copy["data_uri"]
            elif copy["kind"] == "pdf":
                doc["signed_copy_pdf"] = copy["path"]
                doc["signed_copy_pages"] = copy["pages"]
                doc["signed_copy_token"] = f"__SIGNEDCOPY__{ref}__"
            else:
                doc["signed_copy_skipped"] = copy.get("reason")
        docs.append(doc)
    return docs


def _dn_rendered_page_count(doc: dict) -> int:
    """How many pages this DN's own re-render would occupy. Used to spot a
    PARTIAL return: a single-sheet scan standing in for a 3-page delivery note
    means the client signed and sent back only page 1."""
    try:
        cols = dn_generator._columns(
            bool(doc.get("include_prices")),
            doc.get("columns") if isinstance(doc.get("columns"), list) and doc.get("columns") else None)
        layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else None
        autofit = layout.get("autofit_rows") if layout else None
        return len(dn_generator.build_layout(
            doc.get("items") or [], cols,
            include_prices=bool(doc.get("include_prices")),
            autofit=autofit))
    except Exception:
        return 1


def _dn_copy_notes(dn_docs: list[dict]) -> list[str]:
    """Plain-language notices about which copy each DN line contributed.

    Silence here would be the failure mode: the point of stapling the DN to an
    invoice is proving delivery, so the user needs to know when the proof is
    missing or incomplete.
    """
    notes = []
    for doc in dn_docs or []:
        no = doc.get("dn_no") or "?"
        reason = doc.get("signed_copy_skipped")
        if doc.get("signed_copy_merge_failed"):
            notes.append(
                f"{no}: its signed copy could not be merged into the invoice - "
                f"the delivery note is NOT attached. Re-attach the file and save "
                f"again.")
        elif reason == "unreadable":
            notes.append(
                f"{no}: its signed copy is a PDF that could not be opened "
                f"(damaged, or password-protected). The invoice carries a "
                f"re-rendered delivery note instead, with no client signature "
                f"on it.")
        elif reason == "unsupported":
            notes.append(
                f"{no}: its signed copy is not a PDF, JPG or PNG, so it cannot "
                f"be attached. The invoice carries a re-rendered delivery note "
                f"instead.")
        elif doc.get("signed_copy_image") or doc.get("signed_copy_pdf"):
            # A partial return: the client signed and sent back fewer sheets
            # than the delivery note has. An image copy is always one page.
            attached = 1 if doc.get("signed_copy_image") else int(doc.get("signed_copy_pages") or 1)
            pages = _dn_rendered_page_count(doc)
            if pages > attached:
                sheet = "page" if attached == 1 else "pages"
                notes.append(
                    f"{no}: the signed copy is {attached} {sheet} but this "
                    f"delivery note is {pages} pages - check the client returned "
                    f"every sheet, not just the first.")
    return notes


def _refresh_invoice_attachments(dn_no: str) -> dict:
    """Rewrite the PDF of every invoice that carries this DN's pages.

    An invoice PDF is written once, at save time, and Open / Copy / Print all
    hand over that file untouched - so a signed copy attached to the DN
    afterwards would never reach the invoice the client actually receives.
    2026-09-02 (Waleed): attaching or un-attaching a DN's signed copy now
    re-renders those invoices in place.

    Each invoice is re-rendered with its OWN stored signature overlay
    (`load_doc_overlay`, not a fresh roll), so the only thing that changes is
    the attached delivery note - the invoice keeps the same signature position
    it was sent with.

    Never raises: a broken link on one old invoice must not make attaching the
    signed copy fail.
    """
    dn_no = (dn_no or "").strip()
    out: dict = {"invoices_refreshed": [], "invoices_failed": []}
    if not dn_no:
        return out
    try:
        inv_nos = database.invoices_referencing_dn(dn_no)
    except Exception as e:
        out["invoices_failed"].append({"inv_no": "", "error": str(e)})
        return out
    for inv_no in inv_nos:
        try:
            _rerender_signed("inv", inv_no, signing.load_doc_overlay("inv", inv_no))
            out["invoices_refreshed"].append(inv_no)
        except Exception as e:
            out["invoices_failed"].append({"inv_no": inv_no, "error": str(e)})
    return out


class Api:
    def __getattribute__(self, name: str):
        """Keep profile activation stable for the full duration of every API call."""
        attribute = object.__getattribute__(self, name)
        if name.startswith("_") or not callable(attribute) or name in _UNLOCKED_CALLS:
            return attribute

        @wraps(attribute)
        def guarded(*args, **kwargs):
            # Every call lands in the activity log: who, what, how long, and
            # whose fault if it failed (backend/activity_log.py, 2026-10-23).
            call = activity_log.start_call(name, args)
            result, error = None, None
            try:
                # Start-up: the answer may already be waiting (backend/boot_cache.py).
                if not kwargs:
                    kept = boot_cache.take(name, args)
                    if kept is not None:
                        result = kept
                        return kept
                with _PROFILE_SWITCH_LOCK:
                    activity_log.started(call)
                    result = attribute(*args, **kwargs)
                    return result
            except BaseException as e:
                error = e
                raise
            finally:
                activity_log.end_call(call, result, error)

        return guarded

    # ---------- meta ----------
    def get_active_profile(self) -> dict:
        profiles = []
        for entry in profile_manager.list_profiles():
            profile_id = entry["id"]
            profiles.append({
                **entry,
                "logo_data_uri": profile_manager.logo_ui_data_uri(profile_id),
                "brand_color": profile_manager.ProfileManager.PROFILE_ACCENTS[profile_id],
            })
        return {
            "id": config.ACTIVE_PROFILE,
            "profiles": profiles,
            "logo_data_uri": profile_manager.logo_ui_data_uri(),
            "brand_color": config.PROFILE_ACCENT,
            "documents_root": str(config.DOCUMENTS_ROOT),
            "iqd_per_usd": config.IQD_PER_USD,   # profit-math peg (Abraj 1400, Eshraqat 1500)
            # markup assumed for a line with no cost (2026-10-21), so the editors'
            # live profit cards estimate exactly as database._line_econ does
            "uncosted_markup": config.UNCOSTED_MARKUP,
            "warning": os.environ.get("DOCUMENTS_HUB_PROFILE_WARNING", ""),
        }

    @staticmethod
    def _reload_profile_runtime() -> None:
        import importlib

        importlib.reload(config)
        for module in (
            database,
            profile_numbering,
            migrations,
            pdf_generator,
            quote_generator,
            quote_numbering,
            dn_generator,
            dn_numbering,
            inv_generator,
            inv_numbering,
            po_numbering,
            signing,
            clients,
            analytics,
            insights,
            data_manager,
            master_data,
        ):
            importlib.reload(module)
        database.init_db()
        report = migrations.run_all()
        failed = [step for step in report if step.get("status") == "failed"]
        if failed:
            keys = ", ".join(str(step.get("key") or "unknown") for step in failed)
            raise RuntimeError(f"Database migration failed for {keys}")

    def switch_profile(self, profile_id: str) -> dict:
        with _PROFILE_SWITCH_LOCK:
            previous = profile_manager.active_profile_id()
            if profile_manager.legacy_fallback_enabled():
                return {
                    "ok": False,
                    "active_profile": previous,
                    "error": "Company switching is unavailable until profile setup succeeds.",
                }
            try:
                profile_manager.manager().validate_profile(profile_id)
                if profile_id == previous:
                    return {"ok": True, "active_profile": previous, "reload": False}
                profile_manager.activate_profile(profile_id)
                self._reload_profile_runtime()
                return {"ok": True, "active_profile": profile_id, "reload": True}
            except Exception as exc:
                if profile_manager.active_profile_id() != previous:
                    try:
                        profile_manager.activate_profile(previous)
                        self._reload_profile_runtime()
                    except Exception as rollback_exc:
                        return {
                            "ok": False,
                            "active_profile": previous,
                            "error": f"Switch failed and rollback could not reload: {rollback_exc}",
                        }
                return {
                    "ok": False,
                    "active_profile": previous,
                    "error": str(exc),
                }

    def get_today(self) -> dict:
        d = date.today()
        return {
            "iso": d.isoformat(),
            "display": _format_date(d),
            "year": d.year,
            "month": d.month,
        }

    def get_next_po_number(self) -> dict:
        po_no, folder = po_numbering.get_next_po_number()
        return {"po_no": po_no, "folder": str(folder)}

    def check_po_exists(self, po_no: str) -> dict:
        """Used by the editor to warn before overwriting an existing PO."""
        return {"exists": database.po_exists(po_no)}

    def check_quote_exists(self, quote_no: str) -> dict:
        return {"exists": database.quote_exists(quote_no)}

    # ---------- item autocomplete ----------
    def suggest_items(self, prefix: str) -> list[dict]:
        return database.suggest_items(prefix or "", limit=8)

    def suggest_companies(self, prefix: str) -> list[dict]:
        return database.suggest_companies(prefix or "", limit=8)

    def suggest_quote_items(self, quote_type: str, prefix: str) -> list[dict]:
        """Per-type autocomplete - items remembered for this specific quotation type."""
        return database.suggest_quote_items(quote_type or "", prefix or "", limit=8)

    # ---- Phase G.14 - universal per-column autocomplete -------------------
    def suggest_column_values(self, doc_type: str, sub_type: str,
                              col_key: str, prefix: str) -> list[dict]:
        """
        Return remembered values for any column in any editor.
          doc_type : 'po' | 'quote' | 'dn' | 'inv'
          sub_type : 'materials' / 'equipment' / 'manpower' / 'service'
                     (empty for po/dn)
          col_key  : the column's key (item / description / unit / role / ...)
        Memory is strictly segregated by (doc_type, sub_type) - Equipment
        QUT items never appear in Materials QUT, QUT items never appear in
        INV, etc.
        """
        return database.suggest_column_values(
            doc_type or "", sub_type or "", col_key or "", prefix or "", limit=10
        )

    # ---------- settings ----------
    def get_settings(self) -> dict:
        """Return the effective settings (defaults + overrides merged)."""
        return config.current_effective_settings()

    def save_settings(self, settings: dict) -> dict:
        """
        Persist new top-level settings and hot-reload.

        Phase L.2: SHALLOW MERGE (was full replace). Previously, callers had
        to assemble a complete settings dict by spreading from
        current_effective_settings(). That snapshot doesn't include `signing`
        (or any future top-level sections), so every Save wiped untouched
        sections like `signing.signatures` (labels). Now this method merges
        the partial payload into the existing settings.json - send only what
        you want to change; empty values ('', {}, [], None) pop the
        override; everything else is replaced.
        """
        try:
            existing = config.load_user_settings() or {}
            for k, v in (settings or {}).items():
                if v in (None, "", {}, []):
                    existing.pop(k, None)
                else:
                    existing[k] = v
            config.save_user_settings(existing)
            # Re-apply so the running process sees the new values immediately.
            import importlib
            importlib.reload(config)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_po_company_tags(self) -> dict:
        """PO per-line company classification map {name: "client"|"internal"}.
        Seeds the list from existing POs (all "client") the first time it's read,
        so the Settings editor has something to show. Per active profile."""
        try:
            return database.seed_po_company_tags()
        except Exception:
            return dict(getattr(config, "PO_COMPANY_TAGS", {}) or {})

    def set_po_company_tag(self, name: str, tag: str) -> dict:
        """Upsert ONE PO company classification. tag "client"|"internal"; any
        other value removes the entry. Read-modify-write so the rest of the map
        is never clobbered (unlike save_settings' top-level shallow merge), then
        update the in-memory config so the To-Do reflects it immediately."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "no name"}
        tag = (tag or "").strip().lower()
        try:
            settings = config.load_user_settings() or {}
            # The SAME copy PO Settings edits and config reads (2026-10-21):
            # this used the old top-level copy and replaced the live map with
            # it, which turned "our company" back into "client".
            tags = database.read_po_company_tags(settings)
            key = name.casefold()
            # drop any case-insensitive duplicate of this name first
            tags = {k: v for k, v in tags.items()
                    if str(k).strip().casefold() != key}
            if tag in ("client", "internal"):
                tags[name] = tag
            database.write_po_company_tags(settings, tags)
            return {"ok": True, "name": name, "tag": tag, "tags": tags}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reset_settings(self) -> dict:
        """Delete only the active company's settings so defaults apply."""
        try:
            if config.SETTINGS_PATH.exists():
                config.SETTINGS_PATH.unlink()
            import importlib
            importlib.reload(config)
            # Also reload modules that captured config references at import time.
            from . import (pdf_generator, quote_generator, dn_generator,
                           inv_generator)
            for m in (pdf_generator, quote_generator, dn_generator, inv_generator):
                importlib.reload(m)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- per-doc-type settings (Phase E) -----------------------------------
    def get_doc_settings(self, doc_type: str) -> dict:
        """Editable settings for ONE document type - the same shape
        `save_doc_settings` expects back.

        2026-09-05 - `po` joined the other three. Its keys used to sit at the
        TOP LEVEL of settings.json and go through `save_settings`, which is why
        there were two save paths with two different merge rules and why a PO
        panel could clobber a key it never showed. `doc_settings.SECTIONS` is
        now the one map from a doc type to its section.
        """
        doc_type = str(doc_type or "").strip().lower()
        if doc_type not in doc_settings.SECTIONS:
            return {"doc_type": doc_type, "settings": {},
                    "error": f"unknown doc_type: {doc_type}"}
        eff = config.current_effective_settings()
        section_name = doc_settings.SECTIONS[doc_type]
        section = eff.get(section_name) or {}
        # The editor needs both views. `settings` is the resolved value used by
        # the renderer, while `stored` contains only explicit user overrides.
        # Without the latter the PO panel always showed 10.3/5.5/12/54 after a
        # clear, because current_effective_settings correctly fills defaults.
        # That made a cleared field look saved instead of returning to Auto.
        raw = config.load_user_settings()
        stored = raw.get(section_name) if isinstance(raw, dict) else {}
        if not isinstance(stored, dict):
            stored = {}
        return {"doc_type": doc_type, "settings": section,
                "stored": stored}

    def save_doc_settings(self, doc_type: str, settings: dict) -> dict:
        """
        Shallow-merge `settings` into data/settings.json under the given
        doc-type key, then hot-reload. Only that doc-type section is touched
        - other doc-type sections + the flat PO-level keys are preserved.

        Phase G.2: also handles nested sub-type keys (materials/equipment/
        manpower/service). Sending {"materials": {"terms": [...]}} REPLACES
        the materials section but leaves equipment/manpower/service intact.
        Sending {"materials": {}} REMOVES the materials override entirely.

        2026-09-05 - accepts `po` too, and merges DEEPLY. The old shallow merge
        meant a panel that sent a whole `company` object replaced everything
        stored under it, so one panel could drop a key it never displayed.
        An empty value still pops its key: that is how an override is cleared.
        """
        doc_type = str(doc_type or "").strip().lower()
        if doc_type not in doc_settings.SECTIONS:
            return {"ok": False, "error": f"unknown doc_type: {doc_type}"}
        key = doc_settings.SECTIONS[doc_type]
        try:
            existing = config.load_user_settings() or {}
            section = doc_settings.deep_merge(existing.get(key) or {}, settings or {})
            if section:
                existing[key] = section
            else:
                existing.pop(key, None)
            config.save_user_settings(existing)
            import importlib
            importlib.reload(config)
            from . import (pdf_generator, quote_generator, dn_generator,
                           inv_generator)
            for m in (pdf_generator, quote_generator, dn_generator,
                      inv_generator):
                importlib.reload(m)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def upload_logo(self, data_uri: str) -> dict:
        """
        Save a base64 data-URI image as the active company's logo.
        """
        try:
            import base64, re
            m = re.match(r"^data:image/(png|jpeg|jpg);base64,(.+)$", data_uri or "")
            if not m:
                return {"ok": False, "error": "Expected a PNG or JPEG data URI."}
            raw = base64.b64decode(m.group(2))
            config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            out = config.ASSETS_DIR / "logo.png"
            out.write_bytes(raw)
            for legacy in ("logo.jpg", "logo.jpeg"):
                p = config.ASSETS_DIR / legacy
                if p.exists():
                    try: p.unlink()
                    except OSError: pass
            return {
                "ok": True,
                "logo_data_uri": profile_manager.logo_ui_data_uri(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ========================================================
    # QUOTATIONS
    # ========================================================
    def get_quote_defaults(self) -> dict:
        """Static lists the quote editor needs to populate dropdowns."""
        return {
            "address_options": list(config.QUOTE_ADDRESS_OPTIONS),
            "terms_default": list(config.QUOTE_TERMS_DEFAULT),
            "type_codes": dict(config.QUOTE_TYPE_CODES),
            "layout": dict(getattr(config, "QUOTE_LAYOUT", {}) or {}),
        }

    def get_quote_types(self) -> dict:
        """
        Full per-type schema (columns + terms + grand_total) for the editor,
        with per-sub-type column-head overrides applied so the live editor
        shows the renamed headers immediately.
        """
        from .quote_generator import _schema_for as _qs
        out = {}
        for key in config.QUOTE_TYPES.keys():
            sch = _qs(key)
            # _schema_for returns a copy when overrides apply, otherwise the
            # raw default. Either way we copy lightly to avoid mutating config.
            out[key] = {**sch, "columns": list(sch["columns"])}
            # Phase T - the ORIGINAL stock columns (code defaults, ignoring any
            # type-level override). The editor now seeds new docs from the
            # effective `columns`; this remains available for UI reset/reference
            # paths that need the built-in defaults.
            out[key]["stock_columns"] = [dict(c) for c in config.QUOTE_TYPES[key]["columns"]]
            # Also honour per-sub-type terms override here so the editor's
            # "Reset to default" Terms button picks up the new default.
            overrides = (getattr(config, "QUOTE_SUBTYPES_OVERRIDES", {}) or {}).get(key) or {}
            if isinstance(overrides.get("terms"), list) and overrides["terms"]:
                out[key]["terms"] = list(overrides["terms"])
        return out

    def suggest_clients(self, prefix: str) -> list[dict]:
        return database.suggest_clients(prefix or "", limit=12)

    def get_next_quote_number(self, client_name: str, quote_type: str = "materials") -> dict:
        quote_no, folder, n = quote_numbering.get_next_quote_number(client_name or "", quote_type)
        return {"quote_no": quote_no, "folder": str(folder), "n": n}

    def preview_quote(self, quote: dict) -> dict:
        """Render a quotation to a TEMP pdf for preview. Returns the path."""
        tmp_dir = Path(tempfile.gettempdir()) / "po_tool_preview"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe = (quote.get("quote_no") or "quote").replace("/", "_").replace("\\", "_")
        out = tmp_dir / f"preview_{safe}.pdf"
        try:
            quote_generator.render_quote_pdf(_printable_document(quote), out)
            return {"ok": True, "path": str(out)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_quote(self, quote: dict) -> dict:
        """
        Record the quotation and, unless it is an incomplete-price app draft,
        render + file its PDF. Remember each row's primary value for per-type
        autocomplete next time.

        File path:
            <DOCUMENTS_ROOT>/QUOTATION/<Client>/<Year>/<TypeCode>/<Month>/QUT-...pdf
        """
        try:
            target = _parse_display_date(quote.get("date")) or date.today()
            quote_type = quote.get("quote_type", "materials")
            client_name = quote.get("client_name", "")
            app_only = bool(quote.get("save_without_pdf"))
            refusal = _known_client_error(
                client_name,
                database.load_quote(quote["quote_no"])
                if quote.get("quote_no") and quote.get("is_edit") else None)
            if refusal:
                return refusal

            # Build the folder from the EDITED date (Phase G.6).
            folder = quote_numbering.quote_save_folder(client_name, quote_type, target)
            # Re-saving an existing quote (edit mode) keeps its NUMBER. The file
            # follows the edited date - see _edit_target_folder for why pinning
            # it made date changes across a month unreachable for editing.
            stale_pdf = None
            existing_rec = None
            claimed = None          # a NEW number this save holds (number_claims)
            if quote.get("quote_no") and quote.get("is_edit"):
                quote_no = quote["quote_no"]
                existing_rec = database.load_quote(quote_no)
                if existing_rec and not app_only:
                    folder, stale_pdf = _edit_target_folder(existing_rec.get("pdf_path"), folder)
            else:
                # A new quote takes its number at save time, held through
                # number_claims so two saves cannot leave with the same one.
                quote_no = claimed = quote_numbering.claim_next_quote_number(
                    client_name, quote_type, holder=_claim_holder())

            quote = {**quote, "quote_no": quote_no}
            # Phase N.1 - preserve any per-row costs recorded in the costing
            # workspace so a normal editor re-save never wipes them.
            _old = existing_rec or database.load_quote(quote_no)
            if _old and _old.get("rows"):
                _pk = (config.QUOTE_TYPES.get(quote_type, {}) or {}).get("primary_key", "item")
                quote["items"] = database.inherit_costs(
                    _old.get("rows"), quote.get("items", []), _pk)
            out = None
            if not app_only:
                folder.mkdir(parents=True, exist_ok=True)
                out = folder / f"{quote_no}.pdf"
                # Auto-sign (2026-07-24) - must run BEFORE the render so the
                # signature is baked into the PDF, not just stored afterwards.
                quote["signing_overlay"] = signing.overlay_for_save(
                    "qut", quote.get("signing_overlay"))
                quote_generator.render_quote_pdf(_printable_document(quote), out)

            staged_pdf = _stage_incomplete_draft_pdf(
                existing_rec.get("pdf_path") if app_only and existing_rec else "")
            try:
                database.save_quote_record(
                    quote_no=quote_no,
                    quote_date=quote.get("date") or _format_date(target),
                    quote_type=quote_type,
                    year=target.year, month=target.month,
                    client_name=client_name,
                    rfq_no=quote.get("rfq_no", ""),
                    client_address=quote.get("client_address", ""),
                    pdf_path="" if app_only else out,
                    rows=quote.get("items", []),
                    terms=quote.get("terms", []),
                    reference=quote.get("reference", ""),
                )
            except Exception:
                _restore_incomplete_draft_pdf(staged_pdf)
                raise
            _finish_incomplete_draft_pdf(staged_pdf)
            if app_only:
                database.set_submitted("quote", quote_no, False)
            else:
                _drop_stale_pdf(stale_pdf, out)
            # Phase T - persist this document's own column layout (or clear it).
            if "columns" in quote:
                database.set_doc_columns("quote", quote_no, quote.get("columns"))
            if "layout" in quote:
                database.set_doc_layout("qut", quote_no, quote.get("layout"))
            # Phase K.1 - persist the signing overlay (or clear if cleared).
            if "signing_overlay" in quote:
                signing.save_doc_overlay("qut", quote_no, quote.get("signing_overlay"))

            # Remember items for next-time autocomplete (per quote type).
            # IMPORTANT: skip image-kind columns - they're base64 data URIs that
            # bloat the DB and aren't useful for autocomplete. Without this filter,
            # EQ quotes (which carry an image cell) save 200KB+ JSON per row.
            schema = config.QUOTE_TYPES.get(quote_type, {})
            primary = schema.get("primary_key")
            cost_ccy = (quote.get("cost_currency") or "USD").strip().upper()
            cost_peg = _sane_peg(quote.get("cost_iqd_per_usd")) or config.IQD_PER_USD
            image_keys = {c["key"] for c in schema.get("columns", []) if c.get("kind") == "image"}
            if primary:
                for row in (quote.get("items") or []):
                    name = str(row.get(primary, "")).strip()
                    if not name:
                        continue
                    data = _item_memory_payload(
                        row, primary, image_keys, cost_ccy, cost_peg)
                    database.best_effort_shared(
                        database.remember_quote_item, quote_type, name, data,
                    )
            # Phase G.14: also remember EVERY cell value in column_memory so
            # users get autocomplete on every column, not just the primary one.
            database.best_effort_shared(
                database.remember_doc_columns,
                "quote", quote_type, quote.get("items") or [],
                schema.get("columns") or [],
            )

            if profile_numbering.enabled():
                profile_numbering.commit("quote", quote_no)

            # Freeze the account and rate these costs were converted at, so the
            # figure cannot move when an account's rate, or the client's
            # receiving account, changes later (2026-09-21).
            try:
                from . import payment_accounts
                _acc = payment_accounts.account_for_document(
                    "quote", client_name, "")
                database.set_account_stamp("quote", quote_no, _acc,
                                           payment_accounts.rate_for_account(_acc))
            except Exception:
                pass        # a save must never fail over a rate stamp

            if claimed:
                number_claims.confirm("quote", claimed)
            return {"ok": True, "quote_no": quote_no,
                    "path": "" if app_only else str(out),
                    "incomplete_price": app_only}
        except Exception as e:
            if locals().get("claimed"):
                number_claims.release("quote", claimed, holder=_claim_holder())
            return {"ok": False, "error": str(e)}

    def list_quotes_for_month(self, year: int, month: int) -> list[dict]:
        fs = quote_numbering.list_quotes_for_month(year, month)
        db_rows = {r["quote_no"]: r for r in database.list_quotes_in_db(year, month)}
        _merge_app_only_drafts(
            fs, db_rows, "quote_no", "quote_type", config.QUOTE_TYPES)
        signed = signing.signed_doc_nos("qut")
        submitted = database.submitted_set("quote")
        for entry in fs:
            entry["editable"] = entry["quote_no"] in db_rows
            entry["signed"] = (not entry.get("incomplete_price")
                               and entry["quote_no"] in signed)
            entry["submitted"] = (not entry.get("incomplete_price")
                                  and entry["quote_no"] in submitted)
            if entry["editable"]:
                entry["updated_at"] = db_rows[entry["quote_no"]]["updated_at"]
                entry["client_name"] = db_rows[entry["quote_no"]].get("client_name", "")
                entry["reference"] = db_rows[entry["quote_no"]].get("reference", "")
        return _bridge_json_safe(_attach_created_and_sort(fs, db_rows, "quote_no"))

    def search_documents(self, query: str, limit: int = 24) -> list[dict]:
        """Dashboard search across all DB-backed docs - matches number, client,
        AND item content inside each document. Returns most-recent-first with a
        `snippet` naming the matched item. Empty list on any failure."""
        try:
            return database.search_documents(query, int(limit))
        except Exception:
            return []

    def load_quote(self, quote_no: str) -> dict | None:
        rec = database.load_quote(quote_no)
        if rec:
            rec["signing_overlay"] = signing.load_doc_overlay("qut", quote_no)
            rec["submitted"] = database.is_submitted("quote", quote_no)
            rec["columns"] = database.get_doc_columns("quote", quote_no)  # Phase T
            rec["layout"] = database.get_doc_layout("qut", quote_no)
        return rec

    def set_submitted(self, kind: str, doc_no: str, value: bool = True) -> dict:
        """Phase Q - mark a quote/DN/invoice as submitted (or unmark)."""
        try:
            return database.set_submitted(kind, doc_no, bool(value))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_invoice_paid(self, inv_no: str, value: bool = True) -> dict:
        """Mark an invoice as paid (or unmark it)."""
        try:
            return database.set_invoice_paid(inv_no, bool(value))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_po_fulfillment(self, po_no: str, item_indexes, source_kind: str,
                           source_no: str, value: bool = True) -> dict:
        """Link PO items to a DN (delivered) or invoice (invoiced).

        Invoice payment is derived separately from set_invoice_paid.
        """
        try:
            return database.set_po_fulfillment(
                po_no, item_indexes, source_kind, source_no, bool(value))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def po_fulfillment_detail(self, po_no: str) -> dict:
        """Per-item delivered/invoiced/paid state for the PO editor."""
        try:
            return database.po_fulfillment_detail(po_no)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_po_candidates(self, limit: int = 300) -> list:
        """Recent POs for the DN/INV -> PO fulfillment picker."""
        try:
            return database.list_po_candidates(int(limit or 300))
        except Exception:
            return []

    def po_links_for_doc(self, source_kind: str, source_no: str) -> list:
        """PO numbers a given DN/INV has already marked (for the picker)."""
        try:
            return database.po_links_for_doc(source_kind, source_no)
        except Exception:
            return []

    def document_workflow(self, kind: str, doc_no: str) -> dict:
        """PO-centered delivery/payment lanes for the editor Workflow popup."""
        try:
            return database.document_workflow(kind, doc_no)
        except Exception as e:
            return {"ok": False, "error": str(e), "lanes": []}

    def clear_po_fulfillment_link(self, po_no: str, source_kind: str,
                                  source_no: str) -> dict:
        """Remove one exact PO-to-DN/invoice fulfillment relationship."""
        try:
            return database.clear_po_fulfillment_link(po_no, source_kind, source_no)
        except Exception as e:
            return {"ok": False, "error": str(e), "removed": 0}

    def list_dn_candidates(self, limit: int = 300) -> list:
        """Recent Delivery Notes for the PO-side 'link a DN' picker."""
        try:
            return database.list_dn_candidates(int(limit or 300))
        except Exception:
            return []

    def list_inv_candidates(self, limit: int = 300) -> list:
        """Recent Invoices for the PO-side 'link an invoice' picker."""
        try:
            return database.list_inv_candidates(int(limit or 300))
        except Exception:
            return []

    def suggest_fulfillment_links(self, source_kind: str, source_no: str,
                                  target_kind: str, limit: int = 5) -> list:
        """Rank likely unlinked PO/DN/invoice fulfillment counterparts."""
        try:
            return database.suggest_link_candidates(
                source_kind, source_no, target_kind, int(limit or 5))
        except Exception:
            return []

    def list_linked_dn_candidates(self, invoice_no: str,
                                  limit: int = 300) -> list:
        """DNs with live PO links, ranked against one invoice when possible."""
        try:
            return database.list_linked_dn_candidates(
                invoice_no, int(limit or 300))
        except Exception:
            return []

    def linked_dn_invoice_detail(self, dn_no: str, invoice_no: str) -> dict:
        """Exact DN-derived PO item groups for invoice-link confirmation."""
        try:
            return database.linked_dn_invoice_detail(dn_no, invoice_no)
        except Exception as e:
            return {"ok": False, "error": str(e), "pos": []}

    def set_attention_ignore(self, kind: str, doc_no: str, category: str,
                             value: bool = True) -> dict:
        """Phase X.30 - mute (or restore) one doc for one Needs-Attention
        category ('decide' | 'uncosted' | 'unsubmitted' | 'unpaid' | 'deliver' |
        'client_sign')."""
        try:
            return database.set_attention_ignore(kind, doc_no, category, bool(value))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def is_attention_ignored(self, kind: str, doc_no: str, category: str) -> bool:
        """Single-document check for one ignore category."""
        try:
            return database.is_attention_ignored(kind, doc_no, category)
        except Exception:
            return False

    # ========================================================
    # DELIVERY NOTES
    # ========================================================
    def get_dn_defaults(self) -> dict:
        # Phase G.7: surface the active base columns so the editor's thead
        # picks up any user override on the next render.
        active_base = (getattr(config, "DN_BASE_COLUMNS_OVERRIDE", None)
                       or config.DN_BASE_COLUMNS)
        return {
            "address_options": list(config.DN_ADDRESS_OPTIONS),
            "company":         dict(config.DN_COMPANY),
            "base_columns":    [dict(c) for c in active_base],
            "price_columns":   [dict(c) for c in config.DN_PRICE_COLUMNS],
            # Phase T - original stock base columns for a NEW delivery note.
            "stock_base_columns": [dict(c) for c in config.DN_BASE_COLUMNS],
            "layout": dict(getattr(config, "DN_LAYOUT", {}) or {}),
        }

    def get_next_dn_number(self, client_name: str = "") -> dict:
        dn_no, folder, n = dn_numbering.get_next_dn_number(client_name or "")
        return {"dn_no": dn_no, "folder": str(folder), "n": n}

    def check_dn_exists(self, dn_no: str) -> dict:
        return {"exists": database.dn_exists(dn_no)}

    def suggest_dn_items(self, prefix: str) -> list[dict]:
        return database.suggest_dn_items(prefix or "", limit=8)

    def preview_dn(self, dn: dict) -> dict:
        """Render a DN to a TEMP pdf for preview."""
        tmp_dir = Path(tempfile.gettempdir()) / "po_tool_preview"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe = (dn.get("dn_no") or "dn").replace("/", "_").replace("\\", "_")
        out = tmp_dir / f"preview_{safe}.pdf"
        try:
            dn_generator.render_dn_pdf(_printable_document(dn), out)
            return {"ok": True, "path": str(out)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_dn(self, dn: dict) -> dict:
        try:
            target = _parse_display_date(dn.get("date")) or date.today()
            client_name = dn.get("client_name", "")
            user_no = (dn.get("dn_no") or "").strip()
            is_edit = bool(dn.get("is_edit"))
            refusal = _known_client_error(
                client_name, database.load_dn(user_no) if is_edit and user_no else None)
            if refusal:
                return refusal

            stale_pdf = None
            claimed = None          # a NEW number this save holds (number_claims)
            if is_edit and user_no and database.load_dn(user_no):
                # Same-number update; the file follows the edited date.
                dn_no = user_no
                rec = database.load_dn(user_no)
                folder, stale_pdf = _edit_target_folder(
                    rec.get("pdf_path"), dn_numbering.dn_save_folder(client_name, target))
            elif profile_numbering.enabled():
                dn_no = claimed = dn_numbering.claim_next_dn_number(
                    client_name, holder=_claim_holder())
                folder = dn_numbering.dn_save_folder(client_name, target)
            elif user_no:
                # User-specified number - folder follows the EDITED date. Hold
                # it, or hear it was just taken.
                try:
                    number_claims.claim("dn", user_no, holder=_claim_holder())
                except number_claims.NumberTaken as exc:
                    return _number_taken(exc)
                dn_no = claimed = user_no
                folder = dn_numbering.dn_save_folder(client_name, target)
            else:
                dn_no = claimed = dn_numbering.claim_next_dn_number(
                    client_name, holder=_claim_holder())
                folder = dn_numbering.dn_save_folder(client_name, target)

            dn = {**dn, "dn_no": dn_no}
            # Phase N.1 - preserve per-row costs recorded in the costing screen.
            _old = database.load_dn(dn_no)
            if _old and _old.get("rows"):
                dn["items"] = database.inherit_costs(
                    _old.get("rows"), dn.get("items", []), "item")
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"{dn_no}.pdf"
            # Auto-sign (2026-07-24) - before the render. DN signs the SUPPLIER
            # box only; the recipient box stays blank for the client's pen.
            dn["signing_overlay"] = signing.overlay_for_save(
                "dn", dn.get("signing_overlay"))
            dn_generator.render_dn_pdf(_printable_document(dn), out)

            database.save_dn_record(
                dn_no=dn_no,
                dn_date=dn.get("date") or _format_date(target),
                year=target.year, month=target.month,
                client_name=client_name,
                lpo_no=dn.get("lpo_no", ""),
                client_address=dn.get("client_address", ""),
                include_prices=bool(dn.get("include_prices")),
                pdf_path=out,
                rows=dn.get("items", []),
                # Phase N.3 - thread the deal link (None preserves an existing one).
                source_quote_no=dn.get("source_quote_no"),
                reference=dn.get("reference", ""),
                item_payload=_dn_item_payload(dn),
            )
            _drop_stale_pdf(stale_pdf, out)
            if "columns" in dn:           # Phase T - per-document base columns
                database.set_doc_columns("dn", dn_no, dn.get("columns"))
            if "layout" in dn:
                database.set_doc_layout("dn", dn_no, dn.get("layout"))
            if "signing_overlay" in dn:
                signing.save_doc_overlay("dn", dn_no, dn.get("signing_overlay"))
            # Phase G.14: remember every cell value in column_memory.
            dn_active_base = (getattr(config, "DN_BASE_COLUMNS_OVERRIDE", None)
                              or config.DN_BASE_COLUMNS)
            dn_columns_for_memory = list(dn_active_base) + list(config.DN_PRICE_COLUMNS)
            database.best_effort_shared(
                database.remember_doc_columns,
                "dn", "", dn.get("items") or [], dn_columns_for_memory,
            )
            if profile_numbering.enabled():
                profile_numbering.commit("dn", dn_no)

            # Freeze the account and rate these costs were converted at, so the
            # figure cannot move when an account's rate, or the client's
            # receiving account, changes later (2026-09-21).
            try:
                from . import payment_accounts
                _acc = payment_accounts.account_for_document(
                    "dn", client_name, "")
                database.set_account_stamp("dn", dn_no, _acc,
                                           payment_accounts.rate_for_account(_acc))
            except Exception:
                pass        # a save must never fail over a rate stamp

            if claimed:
                number_claims.confirm("dn", claimed)
            return {"ok": True, "dn_no": dn_no, "path": str(out)}
        except Exception as e:
            if locals().get("claimed"):
                number_claims.release("dn", claimed, holder=_claim_holder())
            return {"ok": False, "error": str(e)}

    def list_dns_for_month(self, year: int, month: int) -> list[dict]:
        fs = dn_numbering.list_dns_for_month(year, month)
        db_rows = {r["dn_no"]: r for r in database.list_dns_in_db(year, month)}
        signed = signing.signed_doc_nos("dn")
        submitted = database.submitted_set("dn")
        linked = database.linked_source_set("dn")
        # `signed` = OUR digital signature (drives the Copy guard).
        # `client_signed` = the client returned a counter-signed copy (the pill).
        # Two different things - don't collapse them.
        client_signed = database.signed_copy_set("dn")
        # Suppress "Awaiting client signature" when chasing it is pointless:
        # the user waived it for this DN, or the work has already been paid for.
        waived = {d for (k, d) in
                  database.attention_ignore_map().get("client_sign", set()) if k == "dn"}
        paid = database.dn_paid_set()
        for entry in fs:
            entry["editable"] = entry["dn_no"] in db_rows
            entry["signed"] = entry["dn_no"] in signed
            entry["client_signed"] = entry["dn_no"] in client_signed
            entry["client_sign_waived"] = entry["dn_no"] in waived
            entry["paid"] = entry["dn_no"] in paid
            entry["submitted"] = entry["dn_no"] in submitted
            entry["linked"] = entry["dn_no"] in linked
            if entry["editable"]:
                entry["updated_at"] = db_rows[entry["dn_no"]]["updated_at"]
                entry["client_name"] = db_rows[entry["dn_no"]].get("client_name", "")
                entry["reference"] = db_rows[entry["dn_no"]].get("reference", "")
        return _bridge_json_safe(_attach_created_and_sort(fs, db_rows, "dn_no"))

    def load_dn(self, dn_no: str) -> dict | None:
        rec = database.load_dn(dn_no)
        if rec:
            rec["signing_overlay"] = signing.load_doc_overlay("dn", dn_no)
            rec["submitted"] = database.is_submitted("dn", dn_no)
            rec["columns"] = database.get_doc_columns("dn", dn_no)  # Phase T
            rec["layout"] = database.get_doc_layout("dn", dn_no)
        return rec

    # ========================================================
    # INVOICES
    # ========================================================
    def get_inv_defaults(self) -> dict:
        return {
            "address_options": list(config.INV_ADDRESS_OPTIONS),
            "company":         dict(config.INV_COMPANY),
            "bank_info":       [dict(b) for b in config.INV_BANK_INFO],
            "withholding_pct": config.INV_WITHHOLDING_PCT,
            "type_codes":      dict(config.INV_TYPE_CODES),
            "layout": dict(getattr(config, "INV_LAYOUT", {}) or {}),
        }

    def get_inv_types(self) -> dict:
        """Per-type schema (columns) for the editor, with per-sub-type
        column-head overrides applied."""
        from .inv_generator import _schema_for as _is
        out = {}
        for key in config.INV_TYPES.keys():
            sch = _is(key)
            out[key] = {**sch, "columns": list(sch["columns"])}
            # Phase T - stock (code-default) columns for new invoices.
            out[key]["stock_columns"] = [dict(c) for c in config.INV_TYPES[key]["columns"]]
        return out

    def get_next_inv_number(self, client_name: str = "", inv_type: str = "materials") -> dict:
        inv_no, folder, n = inv_numbering.get_next_inv_number(client_name or "", inv_type or "materials")
        return {"inv_no": inv_no, "folder": str(folder), "n": n}

    def check_inv_exists(self, inv_no: str) -> dict:
        return {"exists": database.inv_exists(inv_no)}

    def suggest_inv_items(self, inv_type: str, prefix: str) -> list[dict]:
        return database.suggest_inv_items(inv_type or "", prefix or "", limit=8)

    def preview_inv(self, inv: dict) -> dict:
        """Render an invoice to a TEMP pdf for preview."""
        tmp_dir = Path(tempfile.gettempdir()) / "po_tool_preview"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe = (inv.get("inv_no") or "inv").replace("/", "_").replace("\\", "_")
        out = tmp_dir / f"preview_{safe}.pdf"
        try:
            inv = {**_printable_document(inv),
                   "_dn_docs": _resolve_dn_docs(inv)}   # append DN-line pages
            inv_generator.render_inv_pdf(inv, out)
            return {"ok": True, "path": str(out)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_inv(self, inv: dict) -> dict:
        try:
            target = _parse_display_date(inv.get("date")) or date.today()
            client_name = inv.get("client_name", "")
            inv_type = inv.get("inv_type", "materials")
            user_no = (inv.get("inv_no") or "").strip()
            is_edit = bool(inv.get("is_edit"))
            app_only = bool(inv.get("save_without_pdf"))
            refusal = _known_client_error(
                client_name, database.load_inv(user_no) if is_edit and user_no else None)
            if refusal:
                return refusal

            stale_pdf = None
            existing_rec = database.load_inv(user_no) if is_edit and user_no else None
            claimed = None          # a NEW number this save holds (number_claims)
            if existing_rec:
                inv_no = user_no
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
                if not app_only:
                    folder, stale_pdf = _edit_target_folder(
                        existing_rec.get("pdf_path"), folder)
            elif profile_numbering.enabled():
                inv_no = claimed = inv_numbering.claim_next_inv_number(
                    client_name, inv_type, holder=_claim_holder())
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
            elif user_no:
                # The editor's proposed number: hold it, or hear it was just taken.
                try:
                    number_claims.claim("inv", user_no, holder=_claim_holder())
                except number_claims.NumberTaken as exc:
                    return _number_taken(exc)
                inv_no = claimed = user_no
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)
            else:
                inv_no = claimed = inv_numbering.claim_next_inv_number(
                    client_name, inv_type, holder=_claim_holder())
                folder = inv_numbering.inv_save_folder(client_name, inv_type, target)

            # New-mode Overwrite is legal in the editor. For an app-only save we
            # still need the previous record/path so its old PDF cannot survive.
            if app_only and not existing_rec:
                existing_rec = database.load_inv(inv_no)
                if not existing_rec:
                    old_path = _find_pdf_by_stem(config.INV_ROOT, inv_no)
                    if old_path:
                        existing_rec = {"pdf_path": old_path, "rows": []}

            inv = {**inv, "inv_no": inv_no}
            # Phase N.1 - preserve per-row costs recorded in the costing screen.
            _old = existing_rec or database.load_inv(inv_no)
            if _old and _old.get("rows"):
                _ipk = (config.INV_TYPES.get(inv_type, {}) or {}).get("primary_key", "item")
                inv["items"] = database.inherit_costs(
                    _old.get("rows"), inv.get("items", []), _ipk)
            out = None
            if not app_only:
                folder.mkdir(parents=True, exist_ok=True)
                out = folder / f"{inv_no}.pdf"
                inv["_dn_docs"] = _resolve_dn_docs(inv)   # append DN-line pages to the PDF
                # Auto-sign (2026-07-24) - before the render.
                inv["signing_overlay"] = signing.overlay_for_save(
                    "inv", inv.get("signing_overlay"))
                inv_generator.render_inv_pdf(_printable_document(inv), out)

            # Compute grand total for the DB record (qty * rate sum), in the
            # currency the invoice is ISSUED in.
            grand = 0.0
            for r in (inv.get("items") or []):
                try:
                    grand += float(r.get("qty") or 0) * float(r.get("rate") or 0)
                except (TypeError, ValueError):
                    pass

            # 2026-07-29 - IQD invoices. `invoices.grand_total` is summed
            # straight in SQL by analytics, the dashboard and the clients page,
            # so it stays USD; the dinar figure the client sees rides along in
            # sell_total/sell_currency. A cash deal settles at the market rate
            # in every profile - resolve that here rather than through
            # doc_iqd_per_usd, which cannot see a brand-new invoice's bank key.
            sell_currency = (inv.get("sell_currency") or "USD").strip().upper()
            if sell_currency not in ("USD", "IQD"):
                sell_currency = "USD"
            # The rate the editor actually converted the amounts at wins over
            # every derived rule - it is what the client's dinar figures were
            # produced from, so grand_total has to agree with it or the USD
            # value in every report drifts from the PDF.
            typed_peg = _sane_peg(inv.get("sell_iqd_per_usd"))
            if typed_peg:
                peg = typed_peg
            elif (inv.get("payment_bank_key") or "").strip().lower() == "cash":
                peg = config.CASH_IQD_PER_USD
            else:
                peg = database.doc_iqd_per_usd("inv", inv_no)
            grand_usd = (database.money_to_usd(grand, "IQD", peg)
                         if sell_currency == "IQD" else grand)

            # 2026-07-24 - the DN this invoice came from, recorded directly so the
            # Workflow shows the link even when no PO is involved. Two paths feed
            # it: "Make invoice" on a DN (source_dn_no) and DNs inserted as invoice
            # lines (dn_ref); the first dn_ref wins when there is no explicit
            # source. None preserves an existing link on re-save.
            source_dn_no = (inv.get("source_dn_no") or "").strip()
            dn_refs = _inv_dn_refs(inv.get("items") or [])
            linked_dn_no = (source_dn_no or (dn_refs[0] if dn_refs else "")
                            or str((existing_rec or {}).get("source_dn_no") or "").strip())

            staged_pdf = _stage_incomplete_draft_pdf(
                existing_rec.get("pdf_path") if app_only and existing_rec else "")
            try:
                database.save_inv_record(
                    inv_no=inv_no,
                    inv_date=inv.get("date") or _format_date(target),
                    inv_type=inv_type,
                    year=target.year, month=target.month,
                    client_name=client_name,
                    lpo_no=inv.get("lpo_no", ""),
                    client_address=inv.get("client_address", ""),
                    payment_bank_key=inv.get("payment_bank_key", ""),
                    withholding_included=bool(inv.get("withholding_included", True)),
                    grand_total=round(grand_usd, 2),
                    sell_currency=sell_currency,
                    sell_total=round(grand, 2),
                    # Only an IQD invoice has a rate worth recording; a USD one has
                    # nothing to convert, and storing a rate there would make a
                    # later currency flip look like it had already been priced.
                    sell_iqd_per_usd=(peg if sell_currency == "IQD" else None),
                    pdf_path="" if app_only else out,
                    rows=inv.get("items", []),
                    # Phase N.3 - thread the deal link (None preserves an existing one).
                    source_quote_no=inv.get("source_quote_no"),
                    source_dn_no=linked_dn_no or None,
                    reference=inv.get("reference", ""),
                )
            except Exception:
                _restore_incomplete_draft_pdf(staged_pdf)
                raise
            _finish_incomplete_draft_pdf(staged_pdf)
            if app_only:
                database.set_submitted("inv", inv_no, False)
                database.set_invoice_paid(inv_no, False)
            else:
                _drop_stale_pdf(stale_pdf, out)
            if "columns" in inv:          # Phase T - per-document columns
                database.set_doc_columns("inv", inv_no, inv.get("columns"))
            if "layout" in inv:
                database.set_doc_layout("inv", inv_no, inv.get("layout"))
            if "signing_overlay" in inv:
                signing.save_doc_overlay("inv", inv_no, inv.get("signing_overlay"))

            # Remember items for the autocomplete (per-type).
            schema = config.INV_TYPES.get(inv_type, config.INV_TYPES["materials"])
            primary = schema.get("primary_key", "item")
            image_keys = {c["key"] for c in schema.get("columns", []) if c.get("kind") == "image"}
            # Both memories are SHARED across every invoice of this type, so
            # they are kept in USD. Without this a rate remembered from a dinar
            # invoice would resurface as a ~1400x price on the next dollar one.
            # inv.js converts back up when a suggestion is picked.
            memory_items = inv.get("items") or []
            if sell_currency == "IQD":
                memory_items = [
                    {**r, "rate": round(database.money_to_usd(r.get("rate"), "IQD", peg), 2)}
                    if isinstance(r, dict) and str(r.get("rate", "")).strip() != "" else r
                    for r in memory_items
                ]
            # 2026-08-11 - routed through the SAME payload builder the
            # quotation uses. It used to keep the raw `cost`, which is typed in
            # dollars or dinars with nothing recording which, so a dinar cost
            # replayed onto the next invoice as dollars (40,000 IQD arriving as
            # $40,000 - the exact bug already fixed on the quotation side).
            # `cost_usd` is both the value and the marker.
            # From the payload, NOT the database: `set_cost_currency` runs
            # after this call returns, so a stored read is one save behind.
            cost_ccy = (inv.get("cost_currency") or
                        database.get_cost_currency("inv", inv_no) or "USD").strip().upper()
            cost_peg = _sane_peg(inv.get("cost_iqd_per_usd")) or peg
            for row in memory_items:
                name = (row.get(primary) or "").strip()
                if not name:
                    continue
                data = _item_memory_payload(
                    row, primary, image_keys, cost_ccy, cost_peg)
                database.best_effort_shared(
                    database.remember_inv_item, inv_type, name, data,
                )
            # Phase G.14: remember every cell value in column_memory.
            database.best_effort_shared(
                database.remember_doc_columns,
                "inv", inv_type, memory_items,
                schema.get("columns") or [],
            )

            if profile_numbering.enabled():
                profile_numbering.commit("inv", inv_no)

            workflow_link = None
            if linked_dn_no and not app_only:
                try:
                    workflow_link = database.link_invoice_from_dn(linked_dn_no, inv_no)
                except Exception as link_error:
                    # The invoice and PDF are already saved. Report the optional
                    # workflow failure without falsely declaring the save failed.
                    workflow_link = {"ok": False, "error": str(link_error)}

            # DN-line invoices (2026-07-18): each `dn_ref` line also marks its DN's
            # delivered PO items invoiced, so those POs leave the Deliver & Invoice
            # To-Do. Best-effort - billing must not fail if a link can't be written.
            if not app_only:
                try:
                    database.link_invoice_dn_refs(inv_no, inv.get("items") or [])
                except Exception:
                    pass

            # Freeze the account and rate these costs were converted at, so the
            # figure cannot move when an account's rate, or the client's
            # receiving account, changes later (2026-09-21).
            try:
                from . import payment_accounts
                _acc = payment_accounts.account_for_document(
                    "inv", client_name, inv.get("payment_bank_key") or "")
                database.set_account_stamp("inv", inv_no, _acc,
                                           payment_accounts.rate_for_account(_acc))
            except Exception:
                pass        # a save must never fail over a rate stamp

            if claimed:
                number_claims.confirm("inv", claimed)
            return {"ok": True, "inv_no": inv_no,
                    "path": "" if app_only else str(out),
                    "incomplete_price": app_only,
                    "workflow_link": workflow_link,
                    # 2026-07-30 - which copy each DN line contributed, and any
                    # reason the client's signed copy could NOT travel with the
                    # invoice. The frontend surfaces these as toasts.
                    "dn_copy_notes": (_dn_copy_notes(inv.get("_dn_docs") or [])
                                      if not app_only else [])}
        except Exception as e:
            if locals().get("claimed"):
                number_claims.release("inv", claimed, holder=_claim_holder())
            return {"ok": False, "error": str(e)}

    def list_invoices_for_month(self, year: int, month: int) -> list[dict]:
        fs = inv_numbering.list_invoices_for_month(year, month)
        db_rows = {r["inv_no"]: r for r in database.list_invoices_in_db(year, month)}
        _merge_app_only_drafts(
            fs, db_rows, "inv_no", "inv_type", config.INV_TYPES)
        signed = signing.signed_doc_nos("inv")
        submitted = database.submitted_set("inv")
        paid = database.paid_invoice_set()
        linked = database.linked_source_set("inv")
        # `signed` = OUR digital signature (drives the Copy guard).
        # `client_signed` = the client returned a counter-signed copy (the pill).
        # Two different things - don't collapse them. (2026-08-27, mirroring DN.)
        client_signed = database.signed_copy_set("inv")
        waived = {d for (k, d) in
                  database.attention_ignore_map().get("client_sign", set()) if k == "inv"}
        for entry in fs:
            entry["editable"] = entry["inv_no"] in db_rows
            entry["signed"] = (not entry.get("incomplete_price")
                               and entry["inv_no"] in signed)
            entry["client_signed"] = entry["inv_no"] in client_signed
            entry["client_sign_waived"] = entry["inv_no"] in waived
            entry["submitted"] = (not entry.get("incomplete_price")
                                  and entry["inv_no"] in submitted)
            entry["paid"] = entry["inv_no"] in paid
            entry["linked"] = entry["inv_no"] in linked
            if entry["editable"]:
                entry["updated_at"]  = db_rows[entry["inv_no"]]["updated_at"]
                entry["client_name"] = db_rows[entry["inv_no"]].get("client_name", "")
                entry["grand_total"] = db_rows[entry["inv_no"]].get("grand_total", 0)
                entry["inv_type"]    = db_rows[entry["inv_no"]].get("inv_type", "")
                # 2026-07-29 - the panel shows the figure the client was billed,
                # which is the native one; grand_total above stays the USD
                # equivalent every total/report is built from.
                entry["sell_currency"] = db_rows[entry["inv_no"]].get("sell_currency") or "USD"
                entry["sell_total"]    = db_rows[entry["inv_no"]].get("sell_total", 0)
                entry["reference"]     = db_rows[entry["inv_no"]].get("reference", "")
        return _bridge_json_safe(_attach_created_and_sort(fs, db_rows, "inv_no"))

    def load_invoice(self, inv_no: str) -> dict | None:
        rec = database.load_inv(inv_no)
        if rec:
            rec["signing_overlay"] = signing.load_doc_overlay("inv", inv_no)
            rec["submitted"] = database.is_submitted("inv", inv_no)
            rec["paid"] = database.is_invoice_paid(inv_no)
            rec["columns"] = database.get_doc_columns("inv", inv_no)  # Phase T
            rec["layout"] = database.get_doc_layout("inv", inv_no)
        return rec

    def get_lottie(self, name: str) -> dict:
        """Return a bundled Lottie animation (static/lottie/<name>.json) for the
        dashboard card icons. Served through the bridge so it works under file://
        (where lottie's own XHR path-loading is blocked). {} if missing/invalid."""
        import re, json as _json
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
            return {}
        p = Path(__file__).resolve().parent.parent / "static" / "lottie" / f"{name}.json"
        try:
            return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:
            return {}

    # ========================================================
    # COSTING & PROFIT (Phase N.1)
    # ========================================================
    def list_costing_docs(self, year: int, month: int,
                          kinds: list | None = None) -> list[dict]:
        """Docs (QUT/DN/INV) in the month for the costing workspace list."""
        return _bridge_json_safe(
            database.list_docs_for_costing(int(year), int(month), kinds))

    def load_doc_costing(self, kind: str, doc_no: str) -> dict | None:
        """One document's rows + per-row cost + computed profit totals."""
        return _bridge_json_safe(database.load_doc_costing(kind, doc_no))

    def dn_cost_summary(self, dn_no: str) -> dict:
        """Sell + cost totals for a DN - the invoice DN-line inherits these.
        (DN cost load/save reuse `load_doc_costing`/`save_doc_costs` with kind='dn'.)"""
        return _bridge_json_safe(database.dn_cost_summary(dn_no))

    def list_invoiceable_dns(self, limit: int = 300) -> list:
        """Priced DNs (with sell/cost totals) for the invoice item-field DN picker."""
        return _bridge_json_safe(database.list_invoiceable_dns(int(limit)))

    def costing_summary(self, year: int, month: int) -> dict:
        """Phase N.4 - month profit roll-up: realized (invoices) vs pipeline
        (open quotes) + per-client / per-type breakdowns + win/lost tally."""
        try:
            return _bridge_json_safe(
                {"ok": True, **database.profit_summary(int(year), int(month))})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def dashboard_needs_attention(self, year: int | None = None,
                                  month: int | None = None) -> dict:
        """The dashboard 'Needs Attention' feed and the To-Do list.

        Call with NO arguments for everything outstanding across all months -
        that is the default and what both callers use. `year`+`month` scope it
        to one month on purpose (the To-Do view's month filter). See
        database.dashboard_needs_attention for why the scoping is opt-in."""
        try:
            y = int(year) if year else None
            m = int(month) if month else None
            return _bridge_json_safe(database.dashboard_needs_attention(y, m))
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "decide": [], "uncosted": [], "unsubmitted": [],
                    "unpaid": [], "deliver": [], "ignored": []}

    # ---------- the launch card (2026-10-20, backend/launch_gate.py) + the page's gate (static/gate.js) ----------
    def gate_ready(self, marks: dict | None = None) -> dict:
        """The dashboard has painted: let the launch card finish and fade.

        Called once by the page after its first dashboard render. Harmless when
        there is no launch card (started without one, or already gone)."""
        from . import launch_gate
        gate = launch_gate.current()
        page = launch_gate.page_marks(marks)
        if gate is None or not gate.alive():
            # the card has gone (or never came): show the window at once -
            # it may still be held invisible from before the card went
            launch_gate.reveal_window()
            launch_gate.record_ready_without_gate(page)
            return {"ok": True, "gate": False}
        gate.add_marks(page)
        gate.open()
        # the window fades in under the card as the card fades out
        launch_gate.reveal_window(launch_gate.HOLD_MS)
        # the page holds its own start-up animations until the window is in
        return {"ok": True, "gate": True,
                "settle_ms": launch_gate.HOLD_MS + launch_gate.WINDOW_IN_MS}

    def gate_brief(self) -> dict:
        """The Welcome door's three lines for the active company - the page's
        gate shows them after a company switch. (The launch card works out its
        own rows: gate_brief.card(), in its own process.)"""
        try:
            from . import gate_brief
            return _bridge_json_safe(gate_brief.brief())
        except Exception as e:
            return {"ok": False, "error": str(e), "lines": [], "all_clear": False}

    # ---------- activity and problems log (2026-10-23) ----------
    def log_client_events(self, batch: dict) -> dict:
        """What a screen or a launcher saw and did - screens opened, buttons,
        messages shown, screen errors, "report a problem", PC-side trouble -
        sent in batches. See backend/activity_log.py."""
        return {"ok": True, "stored": activity_log.client_events(batch)}

    @staticmethod
    def _can_view_log() -> bool:
        # The desktop app has one user, its owner. On the office server the
        # gateway starts each engine with that person's role.
        return os.environ.get("BUSINESSHUB_ROLE", "admin") == "admin"

    def activity_can_view(self) -> dict:
        return {"ok": True, "can": self._can_view_log()}

    def activity_list(self, filters: dict | None = None) -> dict:
        if not self._can_view_log():
            return {"ok": False, "error": "Only the admin can see the activity log."}
        try:
            return activity_log.list_events(filters or {})
        except Exception as e:
            return {"ok": False, "error": str(e), "rows": [], "counts": {}}

    def activity_detail(self, event_id: int) -> dict:
        if not self._can_view_log():
            return {"ok": False, "error": "Only the admin can see the activity log."}
        try:
            return activity_log.event_detail(int(event_id))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def activity_people(self) -> dict:
        if not self._can_view_log():
            return {"ok": False, "error": "Only the admin can see the activity log.", "people": []}
        try:
            return {"ok": True, "people": activity_log.people()}
        except Exception as e:
            return {"ok": False, "error": str(e), "people": []}

    def dashboard_state_token(self) -> dict:
        """Has anything the hub shows changed since it was last drawn? (2026-10-01)

        Returning to the hub used to throw the whole dashboard away and fetch
        it again - eight bridge calls and every chart rebuilt - even when the
        user had only opened a document and looked at it. This answers "did
        anything change?" in microseconds, so the hub can keep what it already
        shows when nothing did, and refetch the moment something has.

        The token is everything the hub's numbers depend on:
          * the active company,
          * the company database's change counter and the shared library's
            (`db_version` - SQLite's own file change counter, moved by ANY commit from
            any connection or process),
          * settings.json (exchange rate and other inputs live there),
          * today's date (unbilled months and quote ages roll over at midnight).

        A counter that cannot be read makes the token unique, so the hub
        refetches - unknown is never treated as unchanged.
        """
        import time
        from datetime import date
        from . import db_version
        parts = [str(config.ACTIVE_PROFILE)]
        for path in (config.DB_PATH, profile_manager.shared_db_path()):
            v = db_version.version(path)
            parts.append("x%d" % time.time_ns() if v is None else str(v))
        try:
            st = os.stat(config.SETTINGS_PATH)
            parts.append("%d:%d" % (st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append("no-settings")
        parts.append(date.today().isoformat())
        return {"ok": True, "token": "|".join(parts)}

    def deal_flow(self, kind: str, doc_no: str) -> dict:
        """Phase N.5 - the document's deal family (root quote + linked DNs/INVs)
        for the inline flow diagram."""
        try:
            return {"ok": True, **database.deal_flow(kind, doc_no)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def costing_trend(self, year=None, month=None, span: int = 12) -> dict:
        """Phase N.4 - profit across the last `span` months ending at
        (year, month), defaulting to the current month. Per-month
        realized/pipeline + totals + by-client/type + win rate. Powers the
        Insights → Profit & Margin section."""
        try:
            if not year or not month:
                from datetime import date
                today = date.today()
                year, month = today.year, today.month
            return _bridge_json_safe(
                {"ok": True, **database.profit_trend(int(year), int(month), int(span))})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_doc_costs(self, payload: dict) -> dict:
        """
        Persist costing data for one document WITHOUT re-rendering its PDF.
        payload = {kind, doc_no, costs:[...]|{idx:val}, extra_costs:[{label,amount}],
                   bid_status?, source_quote_no?, qtys?, sells?, currency?,
                   cost_ccys:[...]|{idx:'USD'|'IQD'}}
        """
        try:
            return database.save_doc_costs(
                kind=payload.get("kind"),
                doc_no=payload.get("doc_no"),
                costs=payload.get("costs"),
                extra_costs=payload.get("extra_costs"),
                bid_status=payload.get("bid_status"),
                source_quote_no=payload.get("source_quote_no"),
                qtys=payload.get("qtys"),
                sells=payload.get("sells"),
                currency=payload.get("currency"),
                # Per-row entry currency (2026-08-26) - the Costing grid gives
                # every line its own $/IQD chip, so the unit rides with the
                # number instead of the whole document being assumed.
                cost_ccys=payload.get("cost_ccys"),
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- Phase R - cost-entry currency (USD | IQD), per document ----
    def get_cost_currency(self, kind: str, doc_no: str) -> str:
        try:
            return database.get_cost_currency(kind, doc_no)
        except Exception:
            return "USD"

    def get_item_image(self, ref: str) -> str:
        """A remembered item picture, as a data URI ("" when absent).

        Exists because the front end is loaded over `file://` and cannot read a
        local file itself (the same constraint `get_lottie` works around).
        Never raises: a picture deleted from disk must degrade to "no picture",
        not to a broken autocomplete.
        """
        try:
            return item_images.load(ref)
        except Exception:
            return ""

    def item_image_store_size(self) -> dict:
        """{files, bytes} for the maintenance screen."""
        try:
            return item_images.store_size()
        except Exception:
            return {"files": 0, "bytes": 0}

    def get_cost_rate(self, kind: str, doc_no: str) -> float:
        """The rate this document's costs convert at. 0 = not pinned."""
        try:
            return float(database.get_cost_rate(kind, doc_no) or 0.0)
        except Exception:
            return 0.0

    def set_cost_rate(self, kind: str, doc_no: str, rate: float) -> dict:
        """Pin the cost exchange rate for one document (0 clears it).

        Validated with the same band as the invoice sell rate: profit for the
        WHOLE document is derived from this number, so a decimal slip or an
        extra zero would misprice it everywhere at once.
        """
        try:
            if not rate:
                return database.set_cost_rate(kind, doc_no, None)
            clean = _sane_peg(rate)
            if clean is None:
                return {"ok": False, "error": f"rate out of range: {rate!r}"}
            return database.set_cost_rate(kind, doc_no, clean)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def set_cost_currency(self, kind: str, doc_no: str, currency: str) -> dict:
        try:
            return database.set_cost_currency(kind, doc_no, currency)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def doc_iqd_per_usd(self, kind: str, doc_no: str) -> float:
        """Profit-math peg for one document - the profile peg, or the market rate
        when the document's deal is paid in cash. Lets the client convert IQD cost
        entry exactly the way the profit math will."""
        try:
            return database.doc_iqd_per_usd(kind, doc_no)
        except Exception:
            return config.IQD_PER_USD

    def find_known_costs(self, items: list | None, limit_per: int = 3) -> dict:
        """Phase R - look up previously-costed prices for the given items.
        Returns {ok, iqd_per_usd, results:[{query, matches:[...]}]}."""
        try:
            results = database.find_known_costs(items or [], int(limit_per))
            return {"ok": True, "iqd_per_usd": database.IQD_PER_USD, "results": results}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_bid_status(self, quote_no: str, status: str) -> dict:
        """Mark a quotation open / won / lost."""
        try:
            return database.set_bid_status(quote_no, status)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pull_costs_from_quote(self, kind: str, doc_no: str, quote_no: str) -> dict:
        """
        Fill a DN/INV's per-row costs from a quotation by matching item names,
        and stamp source_quote_no. Returns {ok, matched, total}.
        """
        try:
            src = database.load_quote(quote_no)
            if not src:
                return {"ok": False, "error": f"quote not found: {quote_no}"}
            tgt = database.load_doc_costing(kind, doc_no)
            if not tgt:
                return {"ok": False, "error": f"{kind} not found: {doc_no}"}
            # Phase R - the quote's costs are stored in the QUOTE's entry currency;
            # the target keeps its OWN currency. Convert each pulled cost from the
            # source currency into the target currency so the stored number agrees
            # with the target's currency flag (else profit math is off by 1400x).
            src_cur = database.get_cost_currency("quote", quote_no)
            tgt_cur = tgt.get("cost_currency", "USD")
            # Source and target are the SAME deal (this call stamps source_quote_no),
            # so both sides convert at the one deal peg - self-inverse and matching
            # what the profit math will use.
            rate = database.doc_iqd_per_usd("quote", quote_no)

            def _convert(c, from_ccy):
                # From the SOURCE ROW's own unit (2026-08-26), not the quote's:
                # one quotation can price a line in dollars and the next in
                # dinars, and reading both as the document's flag divides a
                # dollar cost by the peg.
                usd = database.cost_to_usd(c, from_ccy, rate)
                return usd * rate if tgt_cur == "IQD" else usd

            # Build name -> (cost, its currency) from the quote (anchored on the
            # quote's primary). The cost never travels without its unit.
            qpk = (config.QUOTE_TYPES.get(src.get("quote_type", ""), {}) or {}).get(
                "primary_key", "item")
            cost_by_name = {}
            for r in (src.get("rows") or []):
                nm = str(r.get(qpk, "")).strip().lower()
                c = r.get("cost")
                if nm and c not in (None, ""):
                    cost_by_name[nm] = (c, database.row_cost_currency(r, src_cur))
            tpk = tgt.get("primary_key", "item")
            costs = []
            ccys = []
            matched = 0
            for r in tgt.get("rows", []):
                nm = str(r.get(tpk, "")).strip().lower()
                if nm in cost_by_name:
                    c, c_ccy = cost_by_name[nm]
                    costs.append(_convert(c, c_ccy))
                    # Written in the TARGET's currency, so it is stamped with it.
                    ccys.append(tgt_cur)
                    matched += 1
                else:
                    # Untouched - the row keeps its own number AND its own unit.
                    costs.append(r.get("cost", ""))
                    ccys.append(database.row_cost_currency(r, tgt_cur))
            res = database.save_doc_costs(
                kind=kind, doc_no=doc_no, costs=costs, cost_ccys=ccys,
                source_quote_no=quote_no)
            res["matched"] = matched
            res["total"] = len(costs)
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- Phase N.6 - deal linking (source_quote_no is the only link state) ----
    def link_doc_to_quote(self, kind, doc_no, quote_no) -> dict:
        """Set/clear a DN/INV's source_quote_no. Empty quote_no = unlink."""
        try:
            return database.set_doc_source_quote(kind, doc_no, quote_no)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_link_candidates(self, kind, client="") -> list:
        """DNs/INVs across all months that can be linked to a quotation."""
        try:
            return database.list_link_candidates(kind, client)
        except Exception:
            return []

    def list_quote_numbers(self, client="") -> list:
        """Quotation numbers (for the link datalist), optionally by client."""
        try:
            return database.list_quote_numbers(client)
        except Exception:
            return []

    def list_quote_refs(self, query="", limit=800) -> list:
        """Saved quotations for picker popups, optionally search-filtered."""
        try:
            return database.list_quote_refs(query, limit)
        except Exception:
            return []

    # ---------- splitting / preview info ----------
    def po_page_count(self, base_po_no: str, rows: list[dict],
                      layout: dict | None = None,
                      col_widths: dict | None = None) -> int:
        """How many PAGES this row set prints on - always ONE PO (2026-07-29).

        `col_widths` because the wrap budget depends on how wide the column
        will be drawn (2026-09-28). The editor's warning counted pages at the
        default widths while the PDF rendered at the dragged ones.
        Replaces `page_po_numbers`, which listed one number per split chunk."""
        return pdf_generator.po_page_count(rows or [], base_po_no, layout=layout,
                                           col_widths=col_widths)

    def render_preview_html(self, po_no: str, po_date: str, rows: list[dict],
                            layout: dict | None = None) -> str:
        # The preview iframe loads via a data: URL with the CSS already embedded
        # via the linked stylesheet path that pywebview can resolve.
        return pdf_generator.render_html(po_no, po_date, rows or [], layout=layout)

    # ---------- save flow ----------
    def preview_pdf(self, po_no: str, po_date: str, rows: list[dict],
                    signing_overlay: dict | None = None,
                    col_widths: dict | None = None,
                    layout: dict | None = None) -> dict:
        """Render to a TEMP pdf for preview only. Returns the path."""
        tmp_dir = Path(tempfile.gettempdir()) / "po_tool_preview"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out = tmp_dir / f"preview_{po_no}.pdf"
        try:
            pdf_generator.render_pdf(po_no, po_date, rows, out,
                                     signing_overlay=signing_overlay,
                                     col_widths=col_widths,
                                     layout=layout)
            return {"ok": True, "path": str(out)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Dashboard analytics / Insights ----------
    def dashboard_analytics(self, months_back: int = 12,
                            ref_year: int | None = None,
                            ref_month: int | None = None) -> dict:
        """Aggregated stats for the 'This Month' card + the Insights view:
        monthly revenue/volume series, top clients, invoice-by-category,
        signed ratio, and this-vs-last-month KPIs."""
        try:
            return _bridge_json_safe(
                analytics.dashboard_analytics(months_back, ref_year, ref_month))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Item Master (2026-08-11) ----------
    # Replaces the thirteen "Autofill Suggestions" lists with one row per item.
    # See backend/master_data.py for why the tables underneath are left alone.
    def im_list(self, search: str = "", source: str = "", flags=None,
                sort: str = "used", limit: int = 200, offset: int = 0) -> dict:
        """One row per item across every catalog, filtered, with facet counts."""
        try:
            return _bridge_json_safe(master_data.list_items(
                search, source, flags, sort, limit, offset))
        except Exception as e:
            return {"ok": False, "error": str(e), "rows": [], "total": 0}

    # ---- the item RECORD (2026-08-16) --------------------------------------
    # The Master Data screen's own bridge. `mi_*` reads and writes ONE row per
    # item in `master_items` - number, English name, Arabic name, description
    # and unit IN EACH LANGUAGE (2026-08-17), price, cost, picture, and nothing
    # else. The `im_*` methods above still fold the four document catalogs and
    # are what the screen used before; both exist until the catalogs are retired.
    def mi_list(self, search: str = "", sort: str = "used", limit: int = 200,
                offset: int = 0, missing: str = "", desc=None) -> dict:
        """One row per item. No sources, no variants, no folding.

        `desc` (2026-08-26) is the sort DIRECTION: None means the column's own
        default, which is what a first click on a header gets. It is optional
        so an older front end keeps working unchanged."""
        try:
            return _bridge_json_safe(master_items.list_records(
                search, sort, limit, offset, missing, desc))
        except Exception as e:
            return {"ok": False, "error": str(e), "rows": [], "total": 0}

    def mi_get(self, item_id) -> dict:
        try:
            return _bridge_json_safe(master_items.get_record(item_id))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def tr_status(self) -> dict:
        """Can English suggestions be offered at all? Never touches the network."""
        try:
            from . import translate
            return translate.status()
        except Exception as e:
            return {"ok": False, "configured": False, "reason": str(e)}

    def tr_suggest(self, items: list, want: str = "en") -> dict:
        """English SUGGESTIONS for a batch of Arabic items. Writes nothing.

        The only bridge in this app besides image search that reaches the
        internet, and like that one it is never on a save path - the caller is a
        button a person pressed. See `backend/translate.py`.
        """
        try:
            from . import translate
            return translate.suggest(items if isinstance(items, list) else [], want)
        except Exception as e:
            return {"ok": False, "suggestions": [], "error": str(e)}

    # ---- catalogue review (the "Doctor") --------------------------------
    def md_scan(self, kinds: list | None = None, limit: int = 0) -> dict:
        """What is wrong with the catalogue, worst first. Reads only.

        Deliberately NOT the same question as the Master Data chips: those
        report blank fields, this reports problems that are not a blank field -
        one product stored twice, variants nothing can tell apart, a date typed
        into a description. See `backend/master_doctor.py`.
        """
        try:
            return master_doctor.scan(kinds, limit)
        except Exception as e:
            return {"ok": False, "error": str(e), "findings": [], "counts": {}}

    def md_apply(self, kind: str, key: str, proposal: dict | None = None) -> dict:
        """Carry out ONE finding. Never a batch: a merge deletes rows."""
        try:
            return master_doctor.apply(kind, key, proposal)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def md_dismiss(self, kind: str, key: str) -> dict:
        """"Not an issue" - remembered by the finding's WORDS, so it survives a
        merge and a rescan."""
        try:
            return master_doctor.dismiss(kind, key)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def md_undismiss(self, kind: str, key: str) -> dict:
        try:
            return master_doctor.undismiss(kind, key)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_categories(self) -> dict:
        """The closed list of categories. Free text would give 400 of them."""
        return {"ok": True, "categories": list(master_items.CATEGORIES)}

    def mi_unit_map(self, want: str = "en") -> dict:
        """One language's unit -> the other's, as this catalogue writes it.

        A whole-map call rather than one per row: it is ~30 entries each way and
        the caller fills a table with it. `want="en"` for a PO becoming a
        delivery note, `want="ar"` for a quotation teaching the catalogue the
        Arabic half. See `master_items.unit_map`.
        """
        try:
            return {"ok": True, "units": master_items.unit_map(want)}
        except Exception as e:
            return {"ok": False, "error": str(e), "units": {}}

    def mi_create(self, patch: dict) -> dict:
        """Add an item by hand - Master Data is a master, not a report of what
        the documents happened to save.

        Refuses a duplicate rather than making a second record for one product,
        and says WHICH item it collided with in `already` (with its `item_id`),
        so a caller that meant "record this item" can fall through to `mi_save`
        on the record that already exists.
        """
        try:
            return _bridge_json_safe(master_items.create_record(patch))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_unit_counterpart(self, unit: str, want: str = "en") -> dict:
        """The same unit of measure in the other language, or ''.

        For prefilling an English unit from the Arabic one a PO was written
        with. It answers ONLY from the known unit vocabulary and returns '' for
        anything else - a guessed unit is worse than a blank the user fills,
        because it prints on a client's document looking authoritative.
        """
        try:
            return {"ok": True, "unit": master_items.unit_counterpart(unit, want)}
        except Exception as e:
            return {"ok": False, "unit": "", "error": str(e)}

    def mi_save(self, item_id, patch: dict) -> dict:
        try:
            return _bridge_json_safe(master_items.save_record(item_id, patch))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_image(self, item_id) -> str:
        """The record's picture as a data URI ('' when none)."""
        try:
            return master_items.record_image(item_id)
        except Exception:
            return ""

    def mi_delete(self, item_ids: list) -> dict:
        try:
            return _bridge_json_safe(master_items.delete_records(item_ids))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_merge(self, keep_id, drop_ids: list) -> dict:
        try:
            return _bridge_json_safe(master_items.merge_records(keep_id, drop_ids))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_fill_units(self, apply: bool = False) -> dict:
        """Give every item its unit in BOTH languages. Fills a blank slot from
        the other language's word; never rewrites a unit somebody typed, and
        never invents one for an item that has no unit at all."""
        try:
            return _bridge_json_safe(master_items.fill_missing_units(bool(apply)))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def mi_assign_numbers(self, apply: bool = False) -> dict:
        try:
            return _bridge_json_safe(master_items.assign_numbers(bool(apply)))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_detail(self, name: str) -> dict:
        """Everything known about one item + every physical row behind it."""
        try:
            return _bridge_json_safe(master_data.item_detail(name))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_image(self, name: str) -> str:
        """The item's remembered picture as a data URI ('' when none)."""
        try:
            return master_data.item_image(name)
        except Exception:
            return ""

    def im_save(self, name: str, patch: dict) -> dict:
        """Apply an edit to EVERY catalog row behind this item."""
        try:
            return _bridge_json_safe(master_data.save_item(name, patch))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_merge(self, canonical: str, names: list) -> dict:
        """Fold several item names into one (use counts are summed)."""
        try:
            return _bridge_json_safe(master_data.merge_items(canonical, names))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_delete(self, names: list, source: str = "") -> dict:
        """Forget items - from one document type, or from all of them.
        Saved documents are never touched."""
        try:
            return _bridge_json_safe(master_data.delete_items(names, source))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_resolve(self, names: list, doc_type: str = "", sub_type: str = "",
                   cost_currency: str = "USD", cost_peg: float = 0.0,
                   want_image: bool = False, sell_currency: str = "USD",
                   sell_peg: float = 0.0, details: list | None = None) -> dict:
        """Match a whole pasted block against the Item Master in ONE call.

        Returns a tier per name - exact / contained / similar / new - plus the
        values to fill. `exact` and `contained` are safe to apply; `similar` is
        a suggestion the user must accept. `sell_currency`/`sell_peg` provide
        the invoice-side context needed when the UI asks for a remembered
        price, while `cost_currency`/`cost_peg` do the same for cost.
        `details` is one `{description, unit}` per name: what the document
        line says beside it, which settles a name shared by several variants
        (A-117's 18 L / 20 L hydraulic oil). See backend/master_data.py."""
        try:
            return _bridge_json_safe(master_data.resolve_items(
                names, doc_type, sub_type, cost_currency, cost_peg, want_image,
                sell_currency, sell_peg, details))
        except Exception as e:
            return {"ok": False, "error": str(e), "results": [], "summary": {}}

    def po_catalogue_review_get(self, po_no: str, doc_type: str = "po") -> dict:
        """Rows already handled by a save-time Master Data review.

        `doc_type` is "po" for a purchase order completing the English half and
        "qut" for a quotation completing the Arabic half; they must not share a
        key, or reviewing one would silence the other.
        """
        try:
            return {"ok": True,
                    "reviewed": po_catalogue_review.reviewed(po_no, doc_type)}
        except Exception as e:
            return {"ok": False, "error": str(e), "reviewed": []}

    def po_catalogue_review_mark(self, po_no: str, keys: list,
                                 doc_type: str = "po") -> dict:
        """Persist presented rows so an app restart does not ask again."""
        try:
            return po_catalogue_review.mark_reviewed(po_no, keys, doc_type)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def find_item_image(self, name: str, description: str = "",
                        phrase: str = "", use_memory: bool = True) -> dict:
        """A reference picture for ONE item - from memory if we have it, from
        the internet if we do not.

        Per item rather than per table on purpose: the editor stays responsive
        and can show progress, and a single slow lookup cannot stall the rest.

        THREE sources, in this order, and the order is the whole feature:

          1. **The item's own remembered picture.** Once an item has been
             pictured it is never searched again - instant, offline, and
             identical in every document. With ~800 items that repeat
             constantly, the catalog fills itself and searching becomes rare.
          2. **The wording the user corrected by hand**, if any. This is what
             makes a mistake permanent-fixed rather than permanent.
          3. A fresh search.

        The only thing that leaves this machine is the item's name and
        description, as a search phrase. A weak match is reported as a miss -
        `image_search` scores candidates and refuses the ones that merely share
        a word, because a wrong picture in a filled cell is never re-checked.
        """
        try:
            from . import image_search
            name = name or ""
            description = description or ""
            phrase = (phrase or "").strip()

            if use_memory and not phrase:
                try:
                    remembered = master_data.item_image(name)
                except Exception:
                    remembered = ""
                if remembered:
                    return _bridge_json_safe({
                        "ok": True, "data_uri": remembered, "from_memory": True,
                        "source": "memory", "title": name.strip(),
                        "bytes": len(remembered)})
                try:
                    phrase = master_data.item_search_phrase(name, description)
                except Exception:
                    phrase = ""

            return _bridge_json_safe(
                image_search.find_image(name, description, phrase))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def remember_item_search_phrase(self, name: str, phrase: str,
                                    description: str = "",
                                    unit: str = "") -> dict:
        """Remember the user's OWN wording for finding this item's picture.

        See backend/master_data.remember_search_phrase - an empty phrase
        clears it, which is how a correction is undone.
        """
        try:
            return _bridge_json_safe(master_data.remember_search_phrase(
                name or "", phrase or "", description or "", unit or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def im_item_documents(self, name: str) -> dict:
        """Every saved document holding this item - read-only.

        Separate from `im_cost_targets` on purpose: that one prices a change
        and refuses when the item has no usable cost, which is most of the
        catalog. "Where have I used this?" has to answer for any item.
        """
        try:
            return _bridge_json_safe(master_data.item_documents(name or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def im_cost_targets(self, name: str) -> dict:
        """Which saved documents would change if this item's cost were pushed
        into them. Read-only - see backend/master_data.cost_targets."""
        try:
            return _bridge_json_safe(master_data.cost_targets(name or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def im_apply_cost(self, name: str, keys: list | None = None,
                      apply: bool = False) -> dict:
        """Write this item's cost into the CHOSEN documents.

        Nothing is written without both an explicit `keys` list and apply=True.
        Each row is written in its own currency at its own document's peg, and
        no PDF is re-rendered - the cost is private and never printed."""
        try:
            return _bridge_json_safe(
                master_data.apply_cost_to_documents(name or "", keys or [],
                                                    bool(apply)))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def im_backfill_images(self, apply: bool = False) -> dict:
        """Recover pictures already sitting inside saved documents.

        Call with apply=False first - it reports what WOULD change and touches
        nothing. Documents are never modified; an item that already has a
        picture keeps it; matching is exact-name only. See
        backend/master_data.backfill_images."""
        try:
            return _bridge_json_safe(master_data.backfill_images(bool(apply)))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_health(self) -> dict:
        """Catalog quality: counts per flag, row sprawl, picture store state."""
        try:
            return _bridge_json_safe(master_data.health())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_purge_images(self) -> dict:
        """Delete picture files nothing references any more."""
        try:
            return _bridge_json_safe(master_data.purge_orphan_images())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def im_clear_cell_memory(self, doc_type: str = "", sub_type: str = "") -> dict:
        """Wipe the per-cell autocomplete index; it rebuilds as documents save."""
        try:
            return _bridge_json_safe(master_data.clear_cell_memory(doc_type, sub_type))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Insights: money, per client (2026-08-09) ----------
    def insights_overview(self, months: int = 12, end_year: int | None = None,
                          end_month: int | None = None) -> dict:
        """Company money for a window, plus a full metric block per client.

        `months` is the window LENGTH (1/3/6/12) and `end_year`/`end_month`
        its last month, so the UI can step back through history at any span.
        Money is USD throughout; see backend/insights.py for the vocabulary."""
        try:
            return _bridge_json_safe(
                insights.insights_overview(months, end_year, end_month))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def client_insights(self, name: str, months: int = 12,
                        end_year: int | None = None,
                        end_month: int | None = None) -> dict:
        """One client's deep dive over the same window: their metric block,
        every document with its own profit, and their top items."""
        try:
            return _bridge_json_safe(
                insights.client_insights(name, months, end_year, end_month))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def client_statement_months(self, name: str) -> dict:
        """The months a client has issued invoices in, newest first - what the
        Monthly statement popup in Insights offers (backend/statements.py)."""
        try:
            from . import statements
            return {"ok": True, "months": statements.statement_months(name)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def make_client_statement(self, name: str, year: int, month: int) -> dict:
        """Build one client's monthly statement PDF (summary page plus every
        invoice, delivery note and purchase order it names, linked) and save it
        under <documents root>\\REPORTS\\<year>\\<MON>\\."""
        try:
            from . import statements
            return _bridge_json_safe(statements.build(name, int(year), int(month)))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_document(self, doc_no: str, code: str) -> dict:
        """Is this document number + verification code one this app printed?
        Every PDF carries both on each page (backend/doc_verify.py)."""
        try:
            from . import doc_verify
            return _bridge_json_safe(doc_verify.verify(doc_no, code))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Clients section (profiles) ----------
    def cm_list(self, search: str = "", status: str = "active") -> dict:
        from . import client_master
        with _PROFILE_SWITCH_LOCK:
            return client_master.list_records(search, status)

    def cm_get(self, identity: str = "") -> dict:
        from . import client_master
        with _PROFILE_SWITCH_LOCK:
            return client_master.get_record(identity)

    def cm_save(self, payload: dict) -> dict:
        from . import client_master
        with _PROFILE_SWITCH_LOCK:
            return client_master.save_record(payload)

    def cm_archive(self, identity: str, archived: bool = True) -> dict:
        from . import client_master
        with _PROFILE_SWITCH_LOCK:
            return client_master.set_archived(identity, archived)

    def cm_defaults(self, name: str) -> dict:
        from . import client_master
        with _PROFILE_SWITCH_LOCK:
            return client_master.defaults_for(name)

    def client_list(self) -> list:
        """Every client with a quick summary (counts, invoiced total, last seen)."""
        return clients.client_list()

    def client_profile(self, name: str) -> dict:
        """Full profile for one client: documents, totals, addresses, LPOs, terms."""
        return clients.client_profile(name)

    def client_add(self, name: str) -> dict:
        """Add a client to the catalog."""
        return clients.add_client(name)

    def client_delete(self, name: str) -> dict:
        """Remove a client from the catalog (documents are untouched)."""
        return clients.delete_client(name)

    def client_rename(self, old: str, new: str) -> dict:
        """Rename/merge a client across the catalog and all their documents."""
        return clients.rename_client(old, new)

    def delete_document(self, kind: str, doc_no: str, path: str = "") -> dict:
        """
        Delete one saved document everywhere the active app reads it: move the
        PDF to RECYCLE when present, then remove the DB record plus document
        satellites (submitted/cost currency/custom columns/layout/signing).
        """
        try:
            norm = database.normalize_document_kind(kind)
            doc_no = (doc_no or "").strip()
            if not doc_no:
                return {"ok": False, "error": "missing document number"}

            file_path = (path or database.document_pdf_path(norm, doc_no) or "").strip()
            recycle_path = ""
            file_deleted = False
            if file_path:
                p = Path(file_path)
                if p.exists():
                    moved = self.soft_delete_file(file_path)
                    if not moved.get("ok"):
                        return moved
                    recycle_path = moved.get("recycle_path", "")
                    file_deleted = True

            res = database.delete_document_record(
                norm, doc_no, recycle_path=recycle_path, pdf_path=file_path)
            if not res.get("ok"):
                return res
            res["pdf_path"] = file_path or res.get("pdf_path", "")
            res["file_deleted"] = file_deleted
            res["recycle_path"] = recycle_path
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def client_delete_doc(self, doc_type: str, no: str) -> dict:
        """Delete one of a client's documents: remove the DB record AND move
        its PDF to the Recycle folder (reusing the soft-delete path)."""
        return self.delete_document(doc_type, no)

    def open_path(self, path: str) -> dict:
        """Open a file with the system default app (used to show the PDF preview)."""
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reveal_in_folder(self, path: str) -> dict:
        """Open the containing folder with the file selected/highlighted."""
        try:
            p = Path(path)
            if not p.exists():
                return {"ok": False, "error": f"File not found: {path}"}
            if sys.platform == "win32":
                CREATE_NO_WINDOW = 0x08000000
                subprocess.run(["explorer", "/select,", str(p)],
                               creationflags=CREATE_NO_WINDOW)
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p.parent)], check=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def copy_file_to_clipboard(self, path: str) -> dict:
        """
        Put the FILE itself on the Windows clipboard (CF_HDROP), so pasting
        in WhatsApp / Outlook / Word / Explorer attaches the file. Uses
        PowerShell's Set-Clipboard -Path which handles the native format.
        """
        try:
            if sys.platform != "win32":
                return {"ok": False, "error": "Copy-to-clipboard is Windows-only"}
            p = Path(path)
            if not p.exists():
                return {"ok": False, "error": f"File not found: {path}"}
            # Escape single quotes for the PowerShell single-quoted string.
            ps_path = str(p).replace("'", "''")
            CREATE_NO_WINDOW = 0x08000000
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"Set-Clipboard -Path '{ps_path}'"],
                check=True, capture_output=True, text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            return {"ok": True}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": (e.stderr or e.stdout or str(e)).strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- Phase M (items 11, 11.1) - Print + Soft Delete ----------
    def print_file(self, path: str) -> dict:
        """Send a PDF (or any file with a registered 'print' verb) to the
        Windows default printer via the OS print dialog."""
        try:
            if sys.platform != "win32":
                return {"ok": False, "error": "Print is Windows-only"}
            p = Path(path)
            if not p.exists():
                return {"ok": False, "error": f"File not found: {path}"}
            os.startfile(str(p), "print")  # type: ignore[attr-defined]
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def soft_delete_file(self, path: str) -> dict:
        """Move a saved document into a sibling RECYCLE folder, timestamped,
        with a small sidecar JSON noting the original path so it can be
        restored manually. Files older than 30 days in RECYCLE are purged on
        next call. The DB row is left alone (Document Records page is the
        authoritative source for permanent purges)."""
        try:
            from datetime import datetime, timedelta
            import json as _json
            p = Path(path)
            if not p.exists():
                return {"ok": False, "error": f"File not found: {path}"}
            base = Path(getattr(config, "DOCUMENTS_ROOT", p.parent.parent))
            recycle = base / "RECYCLE"
            recycle.mkdir(parents=True, exist_ok=True)
            # Purge files older than 30 days (best-effort; never fail the delete
            # because of a stale leftover).
            cutoff = datetime.now() - timedelta(days=30)
            for old in recycle.glob("*"):
                try:
                    if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
                        old.unlink()
                except Exception:
                    pass
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = recycle / f"{stamp}__{p.name}"
            p.rename(dest)
            sidecar = dest.with_suffix(dest.suffix + ".json")
            try:
                sidecar.write_text(_json.dumps({
                    "original_path": str(p),
                    "deleted_at": datetime.now().isoformat(timespec="seconds"),
                }, indent=2), encoding="utf-8")
            except Exception:
                pass
            return {"ok": True, "recycle_path": str(dest)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_po(self, po_no: str, po_date: str, rows: list[dict],
                signing_overlay: dict | None = None,
                col_widths: dict | None = None,
                layout: dict | None = None,
                source_quote_no: str | None = None,
                reference: str = "") -> dict:
        """
        Save the PO as ONE document in the month folder of the EDITED date
        (falls back to today on parse failure). Phase G.6.

        2026-07-29: a long item list no longer becomes several POs. It is one PO
        number, one PDF, one DB row, paginated over as many pages as it needs -
        the same model quotations / DNs / invoices use. `saved` is still a list
        so the frontend contract is unchanged; it now always holds one entry.
        """
        try:
            target = _parse_display_date(po_date) or date.today()
            # Phase M.1 - file under the DAY folder of the edited date.
            folder = po_numbering.folder_for_date(target)
            folder.mkdir(parents=True, exist_ok=True)
            claimed = None          # a NEW number this save holds (number_claims)
            if profile_numbering.enabled():
                po_no = claimed = po_numbering.claim_next_po_number(holder=_claim_holder())
            elif not database.load_po(po_no):
                # A NEW PO number from the editor: hold it, or hear it was just
                # taken. An existing PO is an overwrite of the same document.
                try:
                    number_claims.claim("po", po_no, holder=_claim_holder())
                except number_claims.NumberTaken as exc:
                    return _number_taken(exc)
                claimed = po_no
            po_rows = [
                {k: v for k, v in r.items() if k not in ("filled", "index")}
                for r in (rows or [])
            ]
            # Auto-sign (2026-07-24) - before the render, as in the other saves.
            signing_overlay = signing.overlay_for_save("po", signing_overlay)
            # Persist the overlay BEFORE rendering so build_template_context
            # picks it up from doc_meta (and so list/load returns it later).
            if signing_overlay is not None:
                signing.save_doc_overlay("po", po_no, signing_overlay)
            if layout is not None:
                database.set_doc_layout("po", po_no, layout)
            # Persist the column widths (2026-07-24) so a later re-render
            # (e.g. "Apply sign & stamp") reproduces this exact layout.
            if col_widths:
                database.set_doc_columns("po", po_no, [
                    {"key": k, "width": w} for k, w in col_widths.items()])
            out = folder / f"{po_no}.pdf"
            pdf_generator.render_pdf(po_no, po_date, po_rows, out,
                                     signing_overlay=signing_overlay,
                                     col_widths=col_widths,
                                     layout=layout)
            database.save_po_record(
                po_no=po_no,
                po_date=po_date,
                year=target.year,
                month=target.month,
                pdf_path=out,
                rows=po_rows,
                source_quote_no=source_quote_no,
                reference=reference,
                item_payload=_po_item_payload,
            )
            if profile_numbering.enabled():
                profile_numbering.commit("po", po_no)
            if claimed:
                number_claims.confirm("po", claimed)
            return {"ok": True, "saved": [{"po_no": po_no, "path": str(out)}],
                    "pages": pdf_generator.po_page_count(
                        po_rows, po_no, layout=layout,
                        col_widths=col_widths)}
        except Exception as e:
            if locals().get("claimed"):
                number_claims.release("po", claimed, holder=_claim_holder())
            return {"ok": False, "error": str(e)}

    # ---------- open existing ----------
    def list_pos_for_month(self, year: int, month: int) -> list[dict]:
        # Merge filesystem listing (truth) with DB records (for editability)
        fs = po_numbering.list_pos_for_month(year, month)
        db_rows = {r["po_no"]: r for r in database.list_pos_in_db(year, month)}
        signed = signing.signed_doc_nos("po")
        submitted = database.submitted_set("po")
        fulfill = database.po_fulfillment_map()
        for entry in fs:
            entry["editable"] = entry["po_no"] in db_rows
            entry["signed"] = entry["po_no"] in signed
            entry["submitted"] = entry["po_no"] in submitted
            f = fulfill.get(entry["po_no"])
            entry["delivered"] = (f or {}).get("delivered", "none")
            entry["invoiced"] = (f or {}).get("invoiced", "none")
            entry["paid"] = (f or {}).get("paid", "none")
            if entry["editable"]:
                entry["updated_at"] = db_rows[entry["po_no"]]["updated_at"]
                entry["source_quote_no"] = db_rows[entry["po_no"]].get("source_quote_no") or ""
                entry["reference"] = db_rows[entry["po_no"]].get("reference", "")
        return _bridge_json_safe(_attach_created_and_sort(fs, db_rows, "po_no"))

    def load_po(self, po_no: str) -> dict | None:
        rec = database.load_po(po_no)
        if rec:
            rec["signing_overlay"] = signing.load_doc_overlay("po", po_no)
            rec["layout"] = database.get_doc_layout("po", po_no)
            rec["submitted"] = database.is_submitted("po", po_no)
            rec["fulfillment"] = database.po_fulfillment_detail(po_no)
        return rec

    def update_po(self, po_no: str, po_date: str, rows: list[dict],
                  signing_overlay: dict | None = None,
                  col_widths: dict | None = None,
                  layout: dict | None = None,
                  source_quote_no: str | None = None,
                  reference: str = "") -> dict:
        """Re-save an existing PO under its existing number (overwrites PDF, updates DB)."""
        try:
            rec = database.load_po(po_no)
            if not rec:
                return {"ok": False, "error": f"PO {po_no} not found in DB"}
            out = Path(rec["pdf_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            # Auto-sign (2026-07-24). The PO editor always sends the overlay it
            # loaded, so `None` genuinely means "unsigned" - don't re-read
            # doc_meta here or clearing a signature could never stick.
            signing_overlay = signing.overlay_for_save("po", signing_overlay)
            if signing_overlay is not None:
                signing.save_doc_overlay("po", po_no, signing_overlay)
            if layout is not None:
                database.set_doc_layout("po", po_no, layout)
            # Persist the column widths (2026-07-24) - see save_po.
            if col_widths:
                database.set_doc_columns("po", po_no, [
                    {"key": k, "width": w} for k, w in col_widths.items()])
            pdf_generator.render_pdf(po_no, po_date, rows or [], out,
                                     signing_overlay=signing_overlay,
                                     col_widths=col_widths,
                                     layout=layout)
            database.save_po_record(
                po_no=po_no,
                po_date=po_date,
                year=rec["year"],
                month=rec["month"],
                pdf_path=out,
                rows=rows or [],
                source_quote_no=source_quote_no,
                reference=reference,
                item_payload=_po_item_payload,
            )
            if profile_numbering.enabled():
                profile_numbering.commit("po", po_no)
            return {"ok": True, "path": str(out)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ========================================================
    # PHASE I - DATA MANAGER
    # ========================================================
    # Thin wrappers around backend.data_manager. The frontend treats every
    # method as a Promise; on the backend they're plain dicts/lists.

    def dm_list_sections(self) -> list[dict]:
        """Sidebar payload: every browsable section + its display columns."""
        return data_manager.list_sections()

    def dm_list_records(self, section_key: str,
                        year: int | None = None,
                        month: int | None = None,
                        client: str | None = None,
                        search: str | None = None,
                        limit: int = 500,
                        offset: int = 0) -> dict:
        """Browse one section with optional filters. See data_manager.list_records."""
        try:
            return data_manager.list_records(
                section_key, year=year, month=month,
                client=client, search=search,
                limit=int(limit or 500), offset=int(offset or 0),
            )
        except Exception as e:
            return {"ok": False, "error": str(e), "rows": [], "total": 0,
                    "section": None}

    def dm_list_filters(self, section_key: str) -> dict:
        """Years + clients available for the section (to populate filter chips)."""
        return {
            "years":   data_manager.list_years_for_section(section_key),
            "clients": data_manager.list_clients_for_section(section_key),
        }

    def dm_delete_record(self, section_key: str, pk_value: str) -> dict:
        """Delete one row from the section's table. PDF on disk is preserved."""
        return data_manager.delete_record(section_key, pk_value)

    def dm_delete_records(self, section_key: str, pks: list[str]) -> dict:
        """Bulk delete. PDFs on disk are preserved."""
        return data_manager.delete_records_bulk(section_key, pks or [])

    def dm_pick_folder(self, title: str = "Pick a folder") -> dict:
        """
        Open the native folder picker (pywebview FOLDER_DIALOG).
        Returns {"ok": True, "path": "C:\\..."} or {"ok": False, "cancelled": True}.
        """
        return _dm_pick_folder(title=title)

    def dm_pick_file(self, title: str = "Pick a file",
                     file_types: list[str] | None = None) -> dict:
        """Native single-file picker. file_types like ['Backup archive (*.zip)']."""
        return _dm_pick_file(title=title, file_types=file_types or [])

    # -------- Phase M.3 - spreadsheet ingest for the bulk-paste wizard ------
    def bulk_pick_spreadsheet(self) -> dict:
        """Open a file picker filtered to spreadsheet/text formats.

        Phase M.3 - pywebview's file-types are validated against a strict regex
        that only accepts `Word chars (*.ext1;*.ext2;…)` (no slashes in the
        description, every extension must be word-chars only - `*.*` is
        rejected). The strings below pass that regex.
        """
        return _dm_pick_file(
            title="Pick a spreadsheet (.xlsx, .csv, .tsv)",
            file_types=[
                "Spreadsheets (*.xlsx;*.xlsm;*.csv;*.tsv;*.txt)",
                "Excel (*.xlsx;*.xlsm)",
                "CSV and TSV (*.csv;*.tsv;*.txt)",
                "All supported (*.xlsx;*.xlsm;*.xls;*.xlsb;*.ods;*.csv;*.tsv;*.txt)",
            ],
        )

    def bulk_parse_spreadsheet(self, path: str) -> dict:
        """Parse an .xlsx / .csv / .tsv file into a 2D matrix the wizard reads."""
        return spreadsheet_parser.parse_file(path or "")

    def bulk_parse_uploaded_bytes(self, suggested_name: str, data_uri: str) -> dict:
        """
        Phase M.3 - for drag-dropped files. The frontend reads the dropped file
        as a base64 data URI and posts it here; we write it to a temp file
        (preserving the suffix from the suggested filename so the parser routes
        correctly), parse it, then clean up.
        """
        import base64, os, re, tempfile
        try:
            m = re.match(r"^data:[^;]*;base64,(.+)$", data_uri or "", re.DOTALL)
            if not m:
                return {"ok": False, "error": "Expected a base64 data URI."}
            raw = base64.b64decode(m.group(1), validate=True)
            suffix = ""
            name = (suggested_name or "").strip().lower()
            for ext in (".xlsx", ".xlsm", ".csv", ".tsv", ".txt"):
                if name.endswith(ext):
                    suffix = ext
                    break
            fd, tmp_path = tempfile.mkstemp(prefix="bulk_paste_", suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                return spreadsheet_parser.parse_file(tmp_path)
            finally:
                try: os.unlink(tmp_path)
                except OSError: pass
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def dm_create_backup(self, target_folder: str) -> dict:
        """Zip db + every doc folder into target_folder. Returns archive path."""
        return data_manager.create_backup(target_folder)

    def dm_restore_backup(self, archive_path: str,
                          replace_docs: bool = False) -> dict:
        """Restore from a backup archive (DB always overwritten, docs merged)."""
        result = data_manager.restore_backup(archive_path, replace_docs=bool(replace_docs))
        if result.get("ok"):
            try:
                self._reload_profile_runtime()
            except Exception as exc:
                return {"ok": False, "error": f"Restore completed but reload failed: {exc}"}
        return result

    def dm_relink_pdf(self, section_key: str, pk_value: str,
                      new_pdf_path: str) -> dict:
        """Point a row's pdf_path at a new file on disk."""
        return data_manager.relink_pdf(section_key, pk_value, new_pdf_path)

    # ---- Phase I.5 - Autofill catalog management -------------------------
    # Independent of dm_list_records - works against the suggestion tables
    # (items, companies, clients, quote_items, dn_items, inv_items,
    # column_memory). Catalog-only merges (saved documents unaffected).

    def dm_list_catalogs(self) -> list[dict]:
        """Sidebar payload of every browseable catalog + display columns."""
        return data_manager.list_catalogs()

    def dm_list_catalog_records(self, section_key: str,
                                search: str | None = None,
                                limit: int = 500,
                                offset: int = 0) -> dict:
        try:
            return data_manager.list_catalog_records(
                section_key, search=search,
                limit=int(limit or 500), offset=int(offset or 0),
            )
        except Exception as e:
            return {"ok": False, "error": str(e), "rows": [], "total": 0,
                    "section": None}

    def dm_delete_catalog_entry(self, section_key: str,
                                pk_dict: dict) -> dict:
        return data_manager.delete_catalog_entry(section_key, pk_dict or {})

    def dm_delete_catalog_entries(self, section_key: str,
                                  pk_dicts: list[dict]) -> dict:
        return data_manager.delete_catalog_entries(section_key, pk_dicts or [])

    def dm_update_catalog_entry(self, section_key: str,
                                old_pk_dict: dict,
                                new_values: dict) -> dict:
        return data_manager.update_catalog_entry(
            section_key, old_pk_dict or {}, new_values or {}
        )

    def dm_find_catalog_duplicates(self, section_key: str,
                                   threshold: float = 0.85) -> dict:
        return data_manager.find_catalog_duplicates(
            section_key, threshold=float(threshold or 0.85)
        )

    def dm_merge_catalog_entries(self, section_key: str,
                                 canonical_values: dict,
                                 absorbed_pks: list[dict]) -> dict:
        return data_manager.merge_catalog_entries(
            section_key, canonical_values or {}, absorbed_pks or []
        )

    # ========================================================
    # PHASE K - DIGITAL SIGNATURES & STAMP
    # ========================================================
    def signing_get_settings(self) -> dict:
        return signing.get_settings()

    def signing_save_settings(self, payload: dict) -> dict:
        return signing.save_settings(payload or {})

    # ---- Client's signed copy (DN 2026-07-24, invoices 2026-08-27) --------- #
    # The counter-signed paper the client scans back to us. Stored beside the
    # generated PDF as "<doc_no>.signed.<ext>" - verified safe on BOTH sides:
    # that stem parses to None in dn_numbering, and inv_numbering skips a
    # ".signed" stem outright, so the copy can never appear as a phantom
    # document in a month list or bump the running number.
    #
    # Everything below is kind-generic (`dn` | `inv`); the per-kind wrappers at
    # the end are what the editors call. There is ONE implementation on purpose
    # - a second copy of it would drift, exactly as the per-editor cost-currency
    # rules did.

    _SIGNED_COPY_DOCS = {
        "dn":  ("Delivery Note", "load_dn"),
        "inv": ("Invoice",       "load_inv"),
    }

    @classmethod
    def _signed_copy_kind(cls, kind: str) -> str:
        k = (kind or "").strip().lower()
        if k not in cls._SIGNED_COPY_DOCS:
            raise ValueError(f"signed copies are not kept for: {kind or '(none)'}")
        return k

    def _signed_copy_dest(self, kind: str, doc_no: str, suffix: str) -> Path:
        kind = self._signed_copy_kind(kind)
        label, loader = self._SIGNED_COPY_DOCS[kind]
        rec = getattr(database, loader)(doc_no)
        if not rec:
            raise ValueError(f"{label.lower()} not found: {doc_no}")
        # An incomplete-price draft has no PDF yet, and `Path("").parent` is the
        # working directory - refuse rather than scatter scans next to app.py.
        pdf_path = str(rec.get("pdf_path") or "").strip()
        if not pdf_path:
            raise ValueError(
                f"This {label} has no PDF yet - complete its pricing and save "
                "it before attaching a signed copy.")
        folder = Path(pdf_path).parent
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{doc_no}.signed{suffix}"

    @staticmethod
    def _clean_suffix(name: str, fallback: str = ".pdf") -> str:
        suffix = Path(name or "").suffix.lower()
        return suffix if suffix in (".pdf", ".png", ".jpg", ".jpeg") else fallback

    def get_doc_signed_copy(self, kind: str, doc_no: str) -> dict | None:
        try:
            kind = self._signed_copy_kind(kind)
        except ValueError:
            return None
        return database.get_signed_copy(kind, (doc_no or "").strip())

    def attach_doc_signed_copy(self, kind: str, doc_no: str) -> dict:
        """Pick the client's signed copy from disk and attach it to a document."""
        try:
            kind = self._signed_copy_kind(kind)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        doc_no = (doc_no or "").strip()
        if not doc_no:
            return {"ok": False, "error": "no document number"}
        picked = _dm_pick_file(
            title="Choose the signed copy",
            file_types=("Signed copy (*.pdf;*.png;*.jpg;*.jpeg)",))
        if not picked.get("ok"):
            return picked if picked.get("cancelled") else {"ok": False,
                                                           "error": picked.get("error", "No file chosen")}
        return self._store_signed_copy(kind, doc_no, Path(picked["path"]))

    def attach_doc_signed_copy_data(self, kind: str, doc_no: str, data_uri: str,
                                    filename: str = "") -> dict:
        """Attach a pasted/dropped signed copy handed over as a data URI."""
        try:
            kind = self._signed_copy_kind(kind)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        doc_no = (doc_no or "").strip()
        if not doc_no:
            return {"ok": False, "error": "no document number"}
        m = re.match(r"^data:([^;,]*);base64,(.+)$", data_uri or "", re.S)
        if not m:
            return {"ok": False, "error": "Unsupported clipboard content"}
        mime, payload = m.group(1).lower(), m.group(2)
        by_mime = {"application/pdf": ".pdf", "image/png": ".png",
                   "image/jpeg": ".jpg", "image/jpg": ".jpg"}
        suffix = self._clean_suffix(filename, by_mime.get(mime, ""))
        if not suffix:
            return {"ok": False, "error": f"Unsupported file type: {mime or 'unknown'}"}
        try:
            raw = base64.b64decode(payload)
        except Exception:
            return {"ok": False, "error": "Could not read the pasted file"}
        try:
            dest = self._signed_copy_dest(kind, doc_no, suffix)
            self._replace_existing_signed_copy(kind, doc_no, dest)
            dest.write_bytes(raw)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        database.set_signed_copy(kind, doc_no, dest)
        return {"ok": True, **(database.get_signed_copy(kind, doc_no) or {}),
                **(_refresh_invoice_attachments(doc_no) if kind == "dn" else {})}

    def _replace_existing_signed_copy(self, kind: str, doc_no: str, dest: Path) -> None:
        """Drop a previous copy whose extension differs, so re-attaching a PDF
        over a JPG doesn't leave the old file orphaned next to the new one."""
        prev = database.get_signed_copy(kind, doc_no)
        old = Path(prev["path"]) if prev and prev.get("path") else None
        if old and old != dest and old.exists():
            try:
                old.unlink()
            except OSError:
                pass

    def _store_signed_copy(self, kind: str, doc_no: str, src: Path) -> dict:
        """Copy the chosen file next to the document, renamed to its number."""
        try:
            if not src.exists():
                return {"ok": False, "error": f"File not found: {src}"}
            dest = self._signed_copy_dest(kind, doc_no, self._clean_suffix(src.name))
            already_in_place = dest.exists() and src.resolve() == dest.resolve()
            if not already_in_place:
                self._replace_existing_signed_copy(kind, doc_no, dest)
                shutil.copy2(src, dest)
        except Exception as e:
            # The DB row is written ONLY after the copy lands, so a failed copy
            # never leaves a document claiming to have a signed copy it doesn't.
            return {"ok": False, "error": str(e)}
        database.set_signed_copy(kind, doc_no, dest)
        return {"ok": True, **(database.get_signed_copy(kind, doc_no) or {}),
                **(_refresh_invoice_attachments(doc_no) if kind == "dn" else {})}

    def remove_doc_signed_copy(self, kind: str, doc_no: str,
                               delete_file: bool = True) -> dict:
        """Un-attach the signed copy. Also deletes the copy we made (it lives in
        our folder and is ours to manage); the user's original scan is untouched."""
        try:
            kind = self._signed_copy_kind(kind)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        doc_no = (doc_no or "").strip()
        rec = database.get_signed_copy(kind, doc_no)
        if delete_file and rec and rec.get("path"):
            try:
                p = Path(rec["path"])
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        cleared = database.clear_signed_copy(kind, doc_no)
        # Symmetric with attaching: the invoice must stop carrying a page whose
        # source has just been taken away.
        if kind == "dn":
            cleared = {**(cleared or {}), **_refresh_invoice_attachments(doc_no)}
        return cleared

    # ---- per-kind wrappers (what the editors and older callers use) -------- #

    def get_dn_signed_copy(self, dn_no: str) -> dict | None:
        return self.get_doc_signed_copy("dn", dn_no)

    def attach_dn_signed_copy(self, dn_no: str) -> dict:
        return self.attach_doc_signed_copy("dn", dn_no)

    def attach_dn_signed_copy_data(self, dn_no: str, data_uri: str,
                                   filename: str = "") -> dict:
        return self.attach_doc_signed_copy_data("dn", dn_no, data_uri, filename)

    def remove_dn_signed_copy(self, dn_no: str, delete_file: bool = True) -> dict:
        return self.remove_doc_signed_copy("dn", dn_no, delete_file)

    def get_inv_signed_copy(self, inv_no: str) -> dict | None:
        return self.get_doc_signed_copy("inv", inv_no)

    def attach_inv_signed_copy(self, inv_no: str) -> dict:
        return self.attach_doc_signed_copy("inv", inv_no)

    def attach_inv_signed_copy_data(self, inv_no: str, data_uri: str,
                                    filename: str = "") -> dict:
        return self.attach_doc_signed_copy_data("inv", inv_no, data_uri, filename)

    def remove_inv_signed_copy(self, inv_no: str, delete_file: bool = True) -> dict:
        return self.remove_doc_signed_copy("inv", inv_no, delete_file)

    # ================================================================
    # LPOs RECEIVED FROM CLIENTS (2026-07-31)
    # quotation (ours) -> LPO (theirs) -> DN (ours) -> invoice (ours).
    # A received-document store, not a sixth document type: the client
    # assigns the number and produces the document, so there is no
    # numbering, no generator and no template to own.
    # ================================================================
    _LPO_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}

    @staticmethod
    def _lpo_folder(client_name: str, lpo_date: str) -> Path:
        """`<DOCUMENTS_ROOT>/LPOs/<Client>/<Year>/<MONTH>/` - a sibling of the
        five generated-document trees, following the same convention."""
        target = _parse_display_date(lpo_date) or date.today()
        safe = re.sub(r'[<>:"/\\|?*]', "-", (client_name or "Unknown").strip()) or "Unknown"
        folder = (config.DOCUMENTS_ROOT / "LPOs" / safe /
                  f"{target.year}" / target.strftime("%b").upper())
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def signing_assets(self) -> dict:
        """The signature/stamp images as data URIs, plus their print sizes.

        The LPO viewer draws the REAL image inside each placement box - an empty
        outline gives you no way to judge whether the stamp is the right size
        before committing, which is the whole point of placing it by hand.
        `has_sig`/`has_stamp` let the UI refuse to add a mark for an asset that
        was never uploaded, instead of silently producing nothing.
        """
        a = signing.get_assets() or {}
        sigs = a.get("signatures") or []
        first = next((s.get("data_uri") for s in sigs if s.get("data_uri")), "")
        return {
            "signatures": [s.get("data_uri") or "" for s in sigs],
            "sig_data_uri": first,
            "stamp_data_uri": a.get("stamp_data_uri") or "",
            "size": a.get("size") or {},
            "has_sig": bool(first),
            "has_stamp": bool(a.get("stamp_data_uri")),
        }

    def list_lpos(self, client_name: str = "") -> list[dict]:
        return _bridge_json_safe(database.list_lpos(client_name or ""))

    def get_lpo(self, client_name: str, lpo_no: str) -> dict | None:
        return _bridge_json_safe(database.load_lpo(client_name, lpo_no))

    def rate_for_document(self, kind: str, client_name: str = "",
                          bank_key: str = "") -> dict:
        """The rate this document's costs WOULD convert at right now, and the
        account it comes from, for the Costing screen's readout.

        A SAVED document answers from its stamp instead
        (`database.doc_iqd_per_usd`); this is what an unsaved one shows while
        it is being priced.
        """
        from . import payment_accounts
        key = payment_accounts.account_for_document(
            kind or "", client_name or "", bank_key or "")
        account = payment_accounts.find_account(key) or {}
        return {"ok": True,
                "rate": payment_accounts.rate_for_account(key),
                "account_key": key,
                "account_label": str(account.get("label") or "")}

    def save_lpo(self, lpo: dict) -> dict:
        """Create or update one received LPO (header details and ordered lines).

        Same client rule as save_quote / save_dn / save_inv (2026-09-11): the
        name must be on file. This was the one save that still took any name, so
        the client's own paperwork could be filed under a client no document of
        ours was allowed to name. An LPO already on file is grandfathered by its
        own name, exactly as a saved document is.
        """
        # The grandfathering lookup runs BEFORE the try block below, so it must
        # not be able to raise: an exception here would escape the bridge
        # entirely instead of coming back as an error the editor can show. A
        # lookup that fails simply means "no record to grandfather".
        try:
            existing = database.load_lpo(lpo.get("client_name", ""),
                                         lpo.get("lpo_no", ""))
        except Exception:
            existing = None
        refusal = _known_client_error(lpo.get("client_name", ""), existing)
        if refusal:
            return refusal
        try:
            quote_error = self._lpo_quote_client_error(
                lpo.get("source_quote_no"), lpo.get("client_name", ""), allow_unavailable=True)
            if quote_error:
                return {"ok": False, "error": quote_error}
            target = _parse_display_date(lpo.get("lpo_date")) or date.today()
            result = database.save_lpo(
                client_name=lpo.get("client_name", ""),
                lpo_no=lpo.get("lpo_no", ""),
                lpo_date=lpo.get("lpo_date") or _format_date(target),
                total=lpo.get("total") or 0,
                currency=lpo.get("currency") or "USD",
                source_quote_no=lpo.get("source_quote_no"),
                notes=lpo.get("notes") or "",
                kind=lpo.get("kind") or "materials",
                rows=lpo.get("rows"),
                period_start=lpo.get("period_start") or "",
                period_end=lpo.get("period_end") or "",
                monthly_amount=lpo.get("monthly_amount"),
                erp_no=lpo.get("erp_no") or "",
                project=lpo.get("project") or "",
                contact=lpo.get("contact") or "",
                year=target.year, month=target.month,
            )
            if result.get("ok"):
                self._win_quote_for_lpo(lpo.get("source_quote_no"), lpo.get("client_name", ""))
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _lpo_quote_client_error(quote_no: str | None, client_name: str,
                              allow_unavailable: bool = False) -> str:
        """External references are allowed; our quotations must belong to this client."""
        number = (quote_no or "").strip()
        if not number or not client_name:
            return ""
        try:
            quote = database.load_quote(number)
        except Exception:
            # Filing paperwork survives an unavailable quote store. Importing
            # prices or marking a win must wait until its identity is verified.
            return "" if allow_unavailable else f"Could not verify quotation {number}. Try again before importing it."
        if quote and database._usage_key(quote.get("client_name", ""), "") != database._usage_key(client_name, ""):
            return f"{number} belongs to {quote.get('client_name')}, not {client_name}. Choose this client's quotation."
        return ""

    @staticmethod
    def _win_quote_for_lpo(quote_no: str | None, client_name: str = "") -> None:
        """An LPO is the client accepting the quotation, so mark it won.

        Won used to be derived from having invoiced, which can be weeks later -
        so the Decide list nagged about quotations already sitting on a purchase
        order. A quotation deliberately marked LOST is left alone: overturning
        that from an LPO header edit would be the app arguing with the user.

        Never raises. Filing the client's paperwork is the user's work, and
        losing it because the quotation lookup fell over would be the worse
        failure by far.
        """
        number = (quote_no or "").strip()
        if not number:
            return                   # cites the client's own reference, not ours
        try:
            if Api._lpo_quote_client_error(number, client_name):
                return
            quote = database.load_quote(number)
            if not quote:
                return               # a reference of theirs we never held
            if (quote.get("bid_status") or "").strip().lower() == "lost":
                return
            database.set_bid_status(number, "won")
        except Exception:
            pass

    def suggest_lpos(self, client_name: str = "", prefix: str = "") -> list[dict]:
        return database.suggest_lpos(client_name or "", prefix or "")

    def delete_lpo(self, client_name: str, lpo_no: str) -> dict:
        """Forget the record. The client's file stays on disk on purpose."""
        return database.delete_lpo(client_name, lpo_no)

    # The lines a quotation carries that belong on the LPO answering it. Kept
    # to exactly these: `cost` is what WE pay and has no business in a record of
    # what the CLIENT ordered, and `image` is a base64 photo (the live
    # QUT-MT-HDEC-029 is 216KB, almost all of it pictures).
    _LPO_LINE_FIELDS = ("item", "description", "unit", "qty", "rate")

    def lpo_from_quote(self, quote_no: str, client_name: str = "") -> dict:
        """The lines of one of our quotations, for filing the LPO that cites it.

        This is what makes filing a PO a tick box instead of retyping:
        CSSP-ADM-0010 names QUT-MT-HDEC-029 on its face and reproduces four of
        its five lines verbatim, so the form pulls them and you untick the one
        they dropped.

        A PO citing the client's OWN reference (CSSP-RENT-0002 cites QUT-146)
        simply comes back not-found, and the form falls back to typing.
        """
        number = (quote_no or "").strip()
        if not number:
            return {"ok": False, "error": "no quotation number given"}
        try:
            quote = database.load_quote(number)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not quote:
            return {"ok": False, "error": f"{number} is not one of our quotations"}
        if client_name:
            error = self._lpo_quote_client_error(number, client_name)
            if error:
                return {"ok": False, "error": error}
        rows, skipped = [], 0
        for row in (quote.get("rows") or []):
            line = self._lpo_line_from_quote_row(row or {})
            if line:
                rows.append(line)
            else:
                skipped += 1
        if not rows:
            # Never hand back a list of blanks. QUT-EQ-HDEC-013 is an equipment
            # quotation whose quantities live in per-document custom columns
            # (col_2..col_6) that only that document can interpret - pulling it
            # would have quietly added empty lines and looked like it worked.
            return {"ok": False, "quote_no": quote.get("quote_no"),
                    "error": f"{number} has no lines that can be read here - "
                             "type them from the PO instead"}
        total = 0.0
        for line in rows:
            try:
                total += float(line.get("qty") or 0) * float(
                    str(line.get("rate") or 0).replace(",", "") or 0)
            except (TypeError, ValueError):
                pass
        return {"ok": True, "quote_no": quote.get("quote_no"),
                "client_name": quote.get("client_name") or "",
                # What the quotation adds up to, so the form can fill the
                # "Total value" box instead of asking for it to be retyped off
                # the PO. It stays editable: a PO legitimately prints a total of
                # its own (CSSP-ADM-0010 drops a line and prints 18,390 against
                # our 19,286), and `LpoLines.mismatch` says so in words.
                "total": round(total, 2), "currency": "USD",
                "rows": rows, "skipped": skipped}

    def client_quotes(self, client_name: str = "") -> list[dict]:
        """One client's quotations, for the Select button on the LPO form.

        Typing `QUT-MT-HDEC-029` from memory is how the wrong quotation gets
        cited. This is the list to choose from, and it is identity-aware, so a
        PO filed under `HDEC` sees the quotations saved under the legal name.
        """
        try:
            return _bridge_json_safe(database.client_quotes(client_name or ""))
        except Exception:
            return []

    @staticmethod
    def _lpo_line_from_quote_row(row: dict) -> dict | None:
        """One quotation row as an LPO line, or None if there is nothing in it.

        The column a line's NAME lives in depends on the quotation type: a
        materials one calls it `item`, an equipment one calls it `equipment`
        and its description `specs` (QUT-EQ-HDEC-013 does). `database._link_primary`
        is the rule the rest of the app already uses for this, so it is reused
        here rather than guessing a second time.
        """
        name = database._link_primary(row)
        try:
            qty = float(row.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if not name and not qty:
            return None
        return {
            "item": name,
            "description": str(row.get("description") or row.get("specs") or "").strip(),
            "unit": str(row.get("unit") or "").strip(),
            "qty": qty,
            "rate": "" if row.get("rate") is None else str(row.get("rate")),
        }

    def lpo_fulfillment(self, client_name: str, lpo_no: str) -> dict:
        """Per-line ordered / delivered / invoiced / remaining for one LPO.

        `complete` is None when the order was never recorded - the screen must
        say "not recorded" there, never "complete".
        """
        return _bridge_json_safe(database.lpo_fulfillment(client_name, lpo_no))

    # What a quotation row knows that an LPO line deliberately does not. The
    # LPO records what the CLIENT ordered; `cost` is what WE pay, and it has no
    # business in that record (`_LPO_LINE_FIELDS`). But a delivery note or
    # invoice needs it, and the quotation the PO cites already has it - so it is
    # read at HAND-OFF time rather than copied into the order.
    _SEED_EXTRA_FIELDS = ("cost", "cost_ccy", "rate_ccy", "image")

    def lpo_document_seed(self, client_name: str, lpo_no: str,
                          document_kind: str = "dn") -> dict:
        """Everything a delivery note or invoice should start with from an LPO.

        Three things used to be lost on this hand-off and each had to be typed
        again: the quotation the PO answers (the LPO knows it), the per-line
        COST (the quotation has it), and the currency each of those is in.

        DN quantities are undelivered. Invoices bill delivered, unbilled items,
        or the unbilled order when no DN exists (direct invoicing). A settled
        order never silently becomes a new full-order draft. Recurring orders
        provide a priced template with blank quantities for the chosen period.

        No cost travels without its currency. 121 of the 332 quotation rows
        that carry a cost predate per-cell currencies, so theirs is resolved the
        way this app already resolves it everywhere else - `row_cost_currency`
        over the QUOTATION's own cost currency (`_line_econ` reads exactly that
        fallback). Inventing "USD" instead would put 84,000 where 60 belongs on
        every dinar-costed quotation.
        """
        out: dict[str, Any] = {"ok": False, "client_name": client_name or "",
                               "lpo_no": lpo_no or "", "source_quote_no": "",
                               "rows": [], "costed": 0, "uncosted": 0}
        try:
            rec = database.load_lpo(client_name, lpo_no)
        except Exception as e:
            out["error"] = str(e)
            return out
        if not rec:
            out["error"] = f"{lpo_no} is not on file for {client_name}"
            return out
        out["client_name"] = rec.get("client_name") or client_name or ""
        out["lpo_no"] = rec.get("lpo_no") or lpo_no or ""
        out["source_quote_no"] = (rec.get("source_quote_no") or "").strip()
        out["kind"] = rec.get("kind") or "materials"
        out["currency"] = rec.get("currency") or "USD"
        out["document_kind"] = document_kind
        if document_kind not in ("dn", "inv"):
            out["error"] = "Choose a delivery note or invoice"
            return out
        if rec.get("closed_at"):
            out["error"] = "This LPO is closed. Reopen it before creating a document."
            return out
        error = self._lpo_quote_client_error(out["source_quote_no"], out["client_name"])
        if error:
            out["error"] = error
            return out

        try:
            ful = database.lpo_fulfillment(client_name, lpo_no)
        except Exception:
            ful = {"lines": []}
        lines = (ful or {}).get("lines") or []
        has_deliveries = any(float(ln.get("delivered") or 0) > 0 for ln in lines)
        recurring = out["kind"] in ("rental", "open")
        use = []
        for ln in lines:
            if recurring and document_kind == "inv":
                quantity = ""
            elif document_kind == "inv":
                base = ln.get("delivered") if has_deliveries else ln.get("ordered")
                quantity = max(0, float(base or 0) - float(ln.get("invoiced") or 0))
            else:
                quantity = max(0, float(ln.get("remaining") or 0))
            if quantity == "" or quantity > 0:
                use.append((ln, quantity))
        out["quantity_policy"] = ("enter_period_quantity" if recurring and document_kind == "inv"
                                  else "delivered_unbilled" if document_kind == "inv" and has_deliveries
                                  else "ordered_unbilled" if document_kind == "inv" else "undelivered")

        # The cited quotation, indexed the way the rest of the app matches an
        # ordered line to a document row: by NAME, through `_link_primary`.
        detail: dict[str, dict] = {}
        quote_ccy = "USD"
        if out["source_quote_no"]:
            try:
                quote = database.load_quote(out["source_quote_no"])
            except Exception:
                quote = None
            if quote:
                try:
                    quote_ccy = database.get_cost_currency(
                        "quote", out["source_quote_no"]) or "USD"
                except Exception:
                    quote_ccy = "USD"
            for row in ((quote or {}).get("rows") or []):
                if not isinstance(row, dict):
                    continue
                key = database._line_key(row)
                if key and key not in detail:
                    detail[key] = row

        rows: list[dict[str, Any]] = []
        for ln, quantity in use:
            row: dict[str, Any] = {
                "item": ln.get("item") or "",
                "description": ln.get("description") or "",
                "unit": ln.get("unit") or "",
                "qty": quantity,
                "rate": ln.get("rate") or "",
            }
            src = detail.get(database._line_key(row)) or {}
            for field in self._SEED_EXTRA_FIELDS:
                value = src.get(field)
                if value not in (None, ""):
                    row[field] = value
            row["rate_ccy"] = out["currency"]
            if row.get("cost") not in (None, ""):
                # Resolved, never blank: the row's own currency when it has one,
                # the quotation's when it does not.
                row["cost_ccy"] = database.row_cost_currency(src, quote_ccy)
            else:
                row.pop("cost_ccy", None)
            if row.get("cost") in (None, ""):
                out["uncosted"] += 1
            else:
                out["costed"] += 1
            rows.append(row)

        out["ok"] = True
        out["rows"] = rows
        return _bridge_json_safe(out)

    def lpo_billing_detail(self, client_name: str, lpo_no: str) -> dict:
        """The month ledger for one contract LPO: each month with how many
        invoices are dated in it (2026-10-06, `lpo_billing.months`)."""
        from . import lpo_billing
        rec = database.load_lpo(client_name, lpo_no)
        if not rec:
            return {"ok": False, "error": "This LPO is not on file"}
        return _bridge_json_safe(lpo_billing.months(rec))

    def lpo_reference_gaps(self) -> list[dict]:
        from . import lpo_billing
        return _bridge_json_safe(lpo_billing.reference_gaps())

    def close_lpo(self, client_name: str, lpo_no: str, closed: bool = True,
                  reason: str = "") -> dict:
        """Retire a contract by hand, or put it back.

        The only way to end a rental the client terminated early, and the only
        way an open supply contract is ever retired at all.
        """
        return database.close_lpo(client_name, lpo_no, bool(closed), reason or "")

    def attach_lpo_file_data(self, client_name: str, lpo_no: str,
                             data_uri: str, filename: str = "") -> dict:
        """Store the client's LPO document (PDF/PNG/JPG) from a data URI."""
        try:
            rec = database.load_lpo(client_name, lpo_no)
            if not rec:
                return {"ok": False, "error": "save the LPO details first"}
            m = re.match(r"^data:([\w./+-]+);base64,(.+)$", data_uri or "", re.S)
            if not m:
                return {"ok": False, "error": "unreadable file data"}
            # NOT `_clean_suffix` on its own: its fallback is ".pdf", so an
            # unknown type (a .zip, say) would be silently stored AS a pdf.
            # Trust the filename only when it names a type we accept, else fall
            # back to the data URI's MIME, and reject when neither qualifies.
            by_mime = {"application/pdf": ".pdf", "image/png": ".png",
                       "image/jpeg": ".jpg", "image/jpg": ".jpg"}
            suffix = Path(filename or "").suffix.lower()
            if suffix not in self._LPO_SUFFIXES:
                suffix = by_mime.get(m.group(1).lower(), "")
            if suffix not in self._LPO_SUFFIXES:
                return {"ok": False, "error": "only PDF, PNG or JPG can be attached"}
            folder = self._lpo_folder(rec["client_name"], rec.get("lpo_date") or "")
            safe_no = re.sub(r'[<>:"/\\|?*]', "-", rec["lpo_no"])
            dest = folder / f"{safe_no}{suffix}"
            dest.write_bytes(base64.b64decode(m.group(2)))
            # Replacing the original invalidates any acknowledged copy made from
            # the previous file - drop it rather than leave a stale counter-sign.
            self._drop_lpo_ack(rec)
            database.set_lpo_file(rec["client_name"], rec["lpo_no"], dest, "file")
            return {"ok": True, **(database.load_lpo(rec["client_name"], rec["lpo_no"]) or {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _drop_lpo_ack(rec: dict) -> None:
        old = rec.get("ack_path")
        if old:
            try:
                Path(old).unlink(missing_ok=True)
            except OSError:
                pass
            database.set_lpo_file(rec["client_name"], rec["lpo_no"], None, "ack")

    # ---- rendering a page as an image (2026-09-12) ------------------------
    # Waleed asked whether showing the pages as images would be faster than
    # handing the whole PDF to pdf.js. Measured on CSSP-ADM-0010, a 614 KB
    # two-page scan:
    #
    #     the PDF, base64 for the bridge ........ 819 KB
    #     both pages as JPEG at 130 dpi ......... 326 KB
    #     ONE page, which is all a first paint needs ... 163 KB
    #
    # So yes - about a fifth of the traffic for the first page, and no pdf.js
    # parse. It is safe because the viewer only DISPLAYS: `acknowledge_lpo`
    # reads the PDF from disk, so where the stamp lands is unaffected by what
    # the screen was showing.
    #
    # The catch is that this needs a rasteriser, and the app deliberately ships
    # almost no native code (see requirements.txt). So it is OPTIONAL: with
    # PyMuPDF present the screen gets images, without it the call reports
    # `reason: "no_rasteriser"` and the frontend falls back to pdf.js exactly as
    # before. Nothing breaks on a machine that does not have it.
    _LPO_PAGE_DPI = 130

    @staticmethod
    def _lpo_page_cache_dir() -> Path:
        d = Path(config.DATA_DIR) / "cache" / "lpo-pages"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def lpo_page_image(self, client_name: str, lpo_no: str, page: int = 0,
                       which: str = "file", dpi: int = 0) -> dict:
        """One page of an LPO as a JPEG data URI, cached on disk.

        Cached under a key that includes the file's size and modification time,
        so replacing the client's document or writing a signed copy produces a
        different key and the stale image can never be served.
        """
        try:
            import fitz                       # PyMuPDF, optional
        except Exception:
            return {"ok": False, "reason": "no_rasteriser",
                    "error": "no PDF rasteriser installed - using pdf.js"}
        try:
            rec = database.load_lpo(client_name, lpo_no)
            if not rec:
                return {"ok": False, "error": "LPO not found"}
            key = "ack_path" if which == "ack" else "file_path"
            raw = rec.get(key)
            if not raw:
                return {"ok": False, "error": "no document attached"}
            src = Path(raw)
            if not src.exists():
                return {"ok": False, "error": f"file is missing: {src.name}"}
            if src.suffix.lower() != ".pdf":
                # A photographed LPO is already an image - hand it over as it
                # is rather than rasterising a raster.
                return self.lpo_file_data_uri(client_name, lpo_no, which)

            page = max(0, int(page or 0))
            dpi = int(dpi or self._LPO_PAGE_DPI)
            stat = src.stat()
            token = f"{src}|{stat.st_size}|{int(stat.st_mtime)}|{page}|{dpi}"
            name = hashlib.sha1(token.encode("utf-8")).hexdigest()[:20] + ".jpg"
            cached = self._lpo_page_cache_dir() / name

            with fitz.open(str(src)) as doc:
                pages = doc.page_count
                if page >= pages:
                    return {"ok": False, "error": f"page {page + 1} of {pages}"}
                if cached.exists():
                    data = cached.read_bytes()
                    pm_w = pm_h = 0
                else:
                    pm = doc[page].get_pixmap(dpi=dpi)
                    pm_w, pm_h = pm.width, pm.height
                    data = pm.tobytes("jpeg", jpg_quality=78)
                    try:
                        cached.write_bytes(data)
                    except OSError:
                        pass                  # a cache that cannot be written is not an error
                # The page's TRUE size in mm, so the placement readout stays
                # honest on a Letter or A5 scan instead of assuming A4.
                rect = doc[page].rect
                mm = {"w": rect.width * 25.4 / 72, "h": rect.height * 25.4 / 72}

            return {"ok": True, "page": page, "pages": pages, "mm": mm,
                    "width": pm_w, "height": pm_h,
                    "filename": src.name, "path": str(src),
                    "data_uri": "data:image/jpeg;base64,"
                                + base64.b64encode(data).decode("ascii")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def lpo_file_data_uri(self, client_name: str, lpo_no: str,
                          which: str = "file") -> dict:
        """The stored document as a data URI, for the in-app viewer.

        The frontend is loaded from a `file://` URL and cannot fetch arbitrary
        paths, so the bytes have to come across the bridge (same reason
        `get_lottie` exists).
        """
        rec = database.load_lpo(client_name, lpo_no)
        if not rec:
            return {"ok": False, "error": "LPO not found"}
        path = rec.get("ack_path") if which == "ack" else rec.get("file_path")
        if not path or not Path(path).exists():
            return {"ok": False, "error": "no file stored"}
        p = Path(path)
        mime = {".pdf": "application/pdf", ".png": "image/png",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(p.suffix.lower(), "")
        if not mime:
            return {"ok": False, "error": f"unsupported file type: {p.suffix}"}
        try:
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "data_uri": f"data:{mime};base64,{b64}",
                "filename": p.name, "is_pdf": p.suffix.lower() == ".pdf"}

    def acknowledge_lpo(self, client_name: str, lpo_no: str,
                        placements: list[dict]) -> dict:
        """Counter-sign the client's LPO at the spots the user picked, and write
        a SEPARATE acknowledged copy to send back.

        `placements` are page-relative fractions (0-1) measured in the viewer:
            {page:int (0-based), kind:"sig"|"stamp", x, y, w, h}
        Fractions rather than points so the viewer never has to know the PDF's
        coordinate system, and so a zoom level can't leak into the result.

        The client's original is NEVER modified - an acknowledgement they can
        dispute has to be checkable against the file they sent.
        """
        try:
            rec = database.load_lpo(client_name, lpo_no)
            if not rec:
                return {"ok": False, "error": "LPO not found"}
            if not rec.get("file_exists"):
                return {"ok": False, "error": "attach the client's LPO document first"}
            places = [p for p in (placements or []) if isinstance(p, dict)]
            if not places:
                return {"ok": False, "error": "place the signature or stamp first"}
            src = Path(rec["file_path"])
            dest = src.with_name(f"{src.stem}.acknowledged.pdf")
            res = lpo_ack.write_acknowledged(src, dest, places)
            database.set_lpo_file(rec["client_name"], rec["lpo_no"], dest, "ack")
            # `applied` is reported so the UI can never say "done" when fewer
            # marks landed than were placed - the quiet failure mode you only
            # notice when the client asks where the stamp is.
            return {"ok": True, "path": str(dest),
                    "applied": res["applied"], "requested": res["requested"],
                    **(database.load_lpo(rec["client_name"], rec["lpo_no"]) or {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def remove_lpo_ack(self, client_name: str, lpo_no: str,
                       delete_file: bool = True) -> dict:
        rec = database.load_lpo(client_name, lpo_no)
        if not rec:
            return {"ok": False, "error": "LPO not found"}
        if delete_file:
            self._drop_lpo_ack(rec)
        else:
            database.set_lpo_file(rec["client_name"], rec["lpo_no"], None, "ack")
        return {"ok": True}

    def doc_is_signed(self, kind: str, doc_no: str) -> bool:
        """Whether a saved document's stored overlay actually signs it."""
        kind = (kind or "").strip().lower()
        if kind not in ("po", "qut", "dn", "inv"):
            return False
        return signing.overlay_is_signed(
            signing.load_doc_overlay(kind, (doc_no or "").strip()) or {})

    def sign_saved_document(self, kind: str, doc_no: str,
                            allow_layout_reset: bool = False) -> dict:
        """Sign an ALREADY SAVED document and re-render its PDF in place.

        Backs the Copy warning's "Apply sign & stamp". Signs even when auto-sign
        is switched off (the user asked for it explicitly).

        A PO saved before 2026-07-24 has no persisted column widths, so
        re-rendering it would silently fall back to template widths and change
        how the document looks. Rather than do that quietly, this returns
        `{"ok": False, "needs_confirm": "layout"}` so the caller can warn first
        and re-call with `allow_layout_reset=True`.
        """
        kind = (kind or "").strip().lower()
        doc_no = (doc_no or "").strip()
        if kind not in ("po", "qut", "dn", "inv"):
            return {"ok": False, "error": f"bad kind: {kind}"}
        if not doc_no:
            return {"ok": False, "error": "no document number"}
        try:
            if (kind == "po" and not allow_layout_reset
                    and _po_stored_col_widths(doc_no) is None):
                return {"ok": False, "needs_confirm": "layout", "kind": kind,
                        "doc_no": doc_no}
            existing = signing.load_doc_overlay(kind, doc_no)
            overlay = signing.overlay_for_save(kind, existing, force=True)
            if not signing.overlay_is_signed(overlay or {}):
                return {"ok": False, "error":
                        "No signature or stamp image is set up in Settings."}
            path = _rerender_signed(kind, doc_no, overlay)
            signing.save_doc_overlay(kind, doc_no, overlay)
            return {"ok": True, "kind": kind, "doc_no": doc_no, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def signing_upload_signature(self, slot: int, data_uri: str) -> dict:
        return signing.upload_signature(int(slot or 0), data_uri or "")

    def signing_upload_stamp(self, data_uri: str) -> dict:
        return signing.upload_stamp(data_uri or "")

    def signing_delete_signature(self, slot: int) -> dict:
        return signing.delete_signature(int(slot or 0))

    def signing_delete_stamp(self) -> dict:
        return signing.delete_stamp()

    def signing_slots_for(self, doc_type: str) -> list[str]:
        return signing.slots_for(doc_type or "")

    def signing_roll_overlay(self, doc_type: str, selections: dict) -> dict:
        """Pick a fresh random offset/rotation and return the full overlay payload."""
        return signing.roll_overlay(doc_type or "", selections or {})

    def signing_save_doc_overlay(self, doc_type: str, doc_no: str,
                                 overlay: dict | None) -> dict:
        try:
            signing.save_doc_overlay(doc_type or "", doc_no or "", overlay)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def signing_load_doc_overlay(self, doc_type: str, doc_no: str) -> dict | None:
        return signing.load_doc_overlay(doc_type or "", doc_no or "")


# --------------------------------------------------------------------------- #
# Native dialog helpers (pywebview).
# Kept at module scope so the Api class stays light, and so we can guard the
# import of `webview` (it's only present at runtime, not during smoke tests).
# --------------------------------------------------------------------------- #

def _dm_pick_folder(title: str = "") -> dict:
    try:
        import webview
        if not webview.windows:
            return {"ok": False, "error": "No active window"}
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG, allow_multiple=False,
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _dm_pick_file(title: str = "", file_types: list[str] | None = None) -> dict:
    try:
        import webview
        if not webview.windows:
            return {"ok": False, "error": "No active window"}
        kwargs: dict[str, Any] = {"allow_multiple": False}
        if file_types:
            kwargs["file_types"] = tuple(file_types)
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, **kwargs,
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
