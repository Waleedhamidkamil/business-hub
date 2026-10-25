"""
A verification code on every document (2026-10-07).

Waleed's stamp and signature can be copied - from the PDF, from a screenshot,
or by turning the pages into images. What a copy cannot carry onto a FAKE
document is a valid code. Every PO, quotation, delivery note and invoice the
app renders prints, on every page:

    Verification: INV-MT-TDC-030 | 7K2F-9QXA

The code is an HMAC of what the document says - its kind, number, date,
client and every printed line (item, description, unit, quantity, rate...) -
under a secret key kept in the git-ignored `data/secrets.json`. So:

  * a fake invoice has no code, or one that the app rejects;
  * a REAL invoice whose amount or lines were edited keeps its old code, which
    no longer matches what it says - the check shows the real figures;
  * an untouched copy of a genuine document checks as genuine, which is true.

`verify(doc_no, code)` is what the "Verify a document" box calls. It looks the
code up in `doc_verify_log` (every code the app ever printed, with what that
version said), then falls back to recomputing it from the current record. The
log is what keeps an older version verifiable after the document is edited and
re-saved, and what keeps every code verifiable if the key is ever lost.

Costs never enter the code: they are not printed, and changing a cost must not
make a sent document fail its check.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, pdf_lock, sqlite_policy

KEY_NAME = "verify_key"
KINDS = {"po": "Purchase order", "qut": "Quotation", "dn": "Delivery note", "inv": "Invoice"}

_SKIP_KEY = re.compile(r"cost|image|^_", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# the key
# --------------------------------------------------------------------------- #

def _key() -> bytes:
    """The machine's verification key, made and saved on first use."""
    raw = pdf_lock._read()
    block = raw.get(pdf_lock.SECTION) if isinstance(raw.get(pdf_lock.SECTION), dict) else {}
    key = str(block.get(KEY_NAME) or "")
    if not key:
        key = secrets.token_urlsafe(32)
        block[KEY_NAME] = key
        raw[pdf_lock.SECTION] = block
        path = pdf_lock._secrets_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass
    return key.encode("utf-8")


# --------------------------------------------------------------------------- #
# what a document says
# --------------------------------------------------------------------------- #

def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _lines(rows: Any) -> list[list[list[str]]]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cells = sorted([k, _text(v)] for k, v in r.items()
                       if not _SKIP_KEY.search(str(k)) and not isinstance(v, (dict, list))
                       and _text(v))
        if cells:
            out.append(cells)
    return out


def facts(kind: str, doc: dict[str, Any]) -> dict[str, Any]:
    """The printed facts a code stands for, from a generator's payload."""
    no_key = {"po": "po_no", "qut": "quote_no", "dn": "dn_no", "inv": "inv_no"}[kind]
    rows = doc.get("items") if doc.get("items") is not None else doc.get("rows")
    total = sum(_num(r.get("qty")) * _num(r.get("rate")) for r in rows or [] if isinstance(r, dict))
    return {
        "kind": kind,
        "no": _text(doc.get(no_key)),
        "date": _text(doc.get("date") or doc.get(f"{no_key[:-3]}_date") or doc.get("po_date")),
        "client": _text(doc.get("client_name")),
        "currency": _text(doc.get("sell_currency") or doc.get("currency") or ""),
        "lines": _lines(rows),
        "total": round(total, 2),
    }


def code_for(f: dict[str, Any]) -> str:
    """XXXX-XXXX, from the facts that are printed. Totals are derived from the
    lines, so they are covered without being hashed twice."""
    payload = json.dumps([f["kind"], f["no"], f["date"], f["client"], f["currency"], f["lines"]],
                         ensure_ascii=False, separators=(",", ":"))
    digest = hmac.new(_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    c = base64.b32encode(digest).decode("ascii")[:8]
    return f"{c[:4]}-{c[4:]}"


def _clean(code: str) -> str:
    c = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    return f"{c[:4]}-{c[4:8]}" if len(c) == 8 else c


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #

def _connect() -> sqlite3.Connection:
    conn = sqlite_policy.open_db(config.DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS doc_verify_log (
        doc_no TEXT NOT NULL, code TEXT NOT NULL, kind TEXT NOT NULL,
        facts TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        PRIMARY KEY (doc_no, code))""")
    return conn


def _record(f: dict[str, Any], code: str) -> None:
    if not f["no"]:
        return
    try:
        now = datetime.now().isoformat(timespec="microseconds")
        with _connect() as conn:
            conn.execute(
                "INSERT INTO doc_verify_log(doc_no, code, kind, facts, first_seen, last_seen) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(doc_no, code) DO UPDATE SET "
                "last_seen = excluded.last_seen",
                (f["no"], code, f["kind"], json.dumps(f, ensure_ascii=False), now, now))
    except sqlite3.Error:
        pass                    # the code still prints and recomputes


# --------------------------------------------------------------------------- #
# printing it
# --------------------------------------------------------------------------- #

# Written onto every page of the FINISHED PDF rather than into the HTML: the
# templates position their pages absolutely, so a fixed HTML footer landed
# under the letterhead, and a returned scan merged into an invoice has no HTML
# at all. Bottom centre, 6mm up - clear of every template's own footer, inside
# what a printer can print. Helvetica is built into every PDF reader, so no
# font is embedded.
_BOTTOM_PT = 17.0
_SIZE_PT = 6.5


def _pdf_text(s: str) -> str:
    s = s.encode("ascii", "replace").decode("ascii").replace("?", "-")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def line_for(kind: str, doc: dict[str, Any]) -> str:
    """The printed verification line for a document, logging its code."""
    f = facts(kind, doc)
    if not f["no"]:
        return ""
    code = code_for(f)
    _record(f, code)
    return f"Verification: {f['no']} | {code}"


def stamp_pdf(path, kind: str, doc: dict[str, Any]):
    """Write the verification line onto every page of `path`, in place.

    Eshraqat documents carry NO verification mark (Waleed, 2026-10-17): its PDFs
    must not read as generated by the same tool. The whole mechanism is skipped
    for that profile - no printed line, and no code logged to `doc_verify_log`,
    so nothing ties one Eshraqat file to another or to this machine. Verifying an
    Eshraqat document is therefore not offered; that is the point.
    """
    if config.ACTIVE_PROFILE == "eshraqat":
        return path

    from pypdf import PageObject, PdfReader, PdfWriter
    from pypdf.generic import (ContentStream, DictionaryObject, NameObject)

    text = line_for(kind, doc)
    if not text:
        return path
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter(clone_from=reader)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    est_w = len(text) * _SIZE_PT * 0.43     # measured: Helvetica averages ~0.43 em here
    for page in writer.pages:
        box = page.mediabox
        w = float(box.width)
        x = float(box.left) + max(4.0, (w - est_w) / 2)
        y = float(box.bottom) + _BOTTOM_PT
        over = PageObject.create_blank_page(width=w, height=float(box.height))
        over[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/FVer"): font})})
        stream = ContentStream(None, None)
        stream.set_data(f"q 0.47 g BT /FVer {_SIZE_PT} Tf {x:.2f} {y:.2f} Td "
                        f"({_pdf_text(text)}) Tj ET Q".encode("ascii"))
        over[NameObject("/Contents")] = writer._add_object(stream)
        page.merge_page(over)
    tmp = Path(str(path) + ".verify.tmp")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- #
# checking one
# --------------------------------------------------------------------------- #

def _current(doc_no: str) -> tuple[str, dict[str, Any]] | None:
    """The document as it stands now, as (kind, generator-shaped payload)."""
    from . import database
    for kind, loader, date_key in (("inv", database.load_inv, "inv_date"),
                                   ("qut", database.load_quote, "quote_date"),
                                   ("dn", database.load_dn, "dn_date"),
                                   ("po", database.load_po, "po_date")):
        try:
            rec = loader(doc_no)
        except Exception:
            rec = None
        if rec:
            doc = dict(rec)
            doc["items"] = rec.get("rows") or []
            doc["date"] = rec.get(date_key) or ""
            return kind, doc
    return None


def _show(f: dict[str, Any]) -> dict[str, Any]:
    return {"kind": KINDS.get(f.get("kind"), f.get("kind")), "no": f.get("no"),
            "date": f.get("date"), "client": f.get("client"),
            "lines": len(f.get("lines") or []), "total": f.get("total"),
            "currency": f.get("currency")}


def verify(doc_no: str, code: str) -> dict[str, Any]:
    """Is this document number + code one the app printed?

    Answered from the LOG of printed codes, so a document edited after a copy
    went out is still recognised ("genuine, but an older version") and the
    figures shown are what the app last printed. Recomputing from the current
    record is only the fallback for a document whose log is missing.

    Returns {ok, genuine, current, reason, document, latest}.
    """
    doc_no, code = _text(doc_no), _clean(code)
    if not doc_no or len(code) != 9:
        return {"ok": False, "error": "Enter the document number and its 8-character code."}
    with _connect() as conn:
        rows = conn.execute("SELECT code, facts FROM doc_verify_log WHERE doc_no = ? "
                            "ORDER BY last_seen DESC", (doc_no,)).fetchall()
    printed = {r["code"]: json.loads(r["facts"]) for r in rows}
    latest = json.loads(rows[0]["facts"]) if rows else None
    if code in printed:
        current = code == rows[0]["code"]
        return {"ok": True, "genuine": True, "current": current,
                "document": _show(printed[code]),
                "latest": None if current else _show(latest),
                "reason": ("Genuine. This is the latest version we printed." if current else
                           "Genuine, but an OLDER version: the document was changed after "
                           "this copy was made. The latest version is shown below it.")}
    if rows:
        return {"ok": True, "genuine": False, "current": False, "document": _show(latest),
                "latest": None,
                "reason": f"NOT GENUINE. We never printed this code on {doc_no}. The copy "
                          "was altered or made by someone else. Our document says:"}
    now = _current(doc_no)
    if now:
        f = facts(now[0], now[1])
        if code == code_for(f):
            return {"ok": True, "genuine": True, "current": True, "document": _show(f),
                    "latest": None, "reason": "Genuine. It matches the document on file."}
        return {"ok": True, "genuine": False, "current": False, "document": _show(f),
                "latest": None,
                "reason": f"NOT GENUINE. This code does not belong to {doc_no}. (Documents "
                          "made before 7 October 2026 carry no code at all.) Our document says:"}
    return {"ok": True, "genuine": False, "current": False, "document": None, "latest": None,
            "reason": f"No document {doc_no} exists in this company. Check the number, or "
                      "switch company. If neither finds it, it is not ours."}
