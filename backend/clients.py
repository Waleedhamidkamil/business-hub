"""
Client profiles - aggregate every saved document by client so the Clients
section can show, per client: their documents, total invoiced business, the
address / LPO they usually use, and their typical payment terms.

Only the three client-facing document types carry a client_name:
quotations, invoices, delivery notes. Purchase Orders target suppliers (no
client), so it is excluded.

Read-only: nothing here writes to the database.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, profile_manager, shared_library, signing, client_master, sqlite_policy


def _connect() -> sqlite3.Connection:
    conn = sqlite_policy.open_db(config.DB_PATH)
    # Client identity has ONE rule - client_master.normalize: Unicode casefold
    # plus collapsed inner whitespace. SQL's COLLATE NOCASE does neither, so
    # matching aliases with it silently dropped every document whose saved
    # spelling differed by a space or a non-ASCII letter case. Teach the
    # connection the real rule instead of keeping a weaker copy in SQL.
    conn.create_function("client_key", 1, client_master.normalize, deterministic=True)
    return conn


def _connect_shared() -> sqlite3.Connection:
    return shared_library.connect()


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _exists(path: str | None) -> bool:
    try:
        return bool(path) and Path(path).exists()
    except OSError:
        return False


def _quote_value(rows_json: str | None) -> float:
    """Best-effort monetary value of a quotation from its rows."""
    try:
        rows = json.loads(rows_json or "[]")
    except (TypeError, ValueError):
        return 0.0
    total = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        qty, rate = _num(r.get("qty")), _num(r.get("rate"))
        total += (qty * rate) if qty else rate
    return round(total, 2)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Management (used by the Data Manager → Clients tab)
# --------------------------------------------------------------------------- #

def add_client(name: str) -> dict[str, Any]:
    result = client_master.save_record({"name": (name or "").strip()})
    if result.get("ok"):
        result["name"] = result["record"]["name"]
    return result


def delete_client(name: str) -> dict[str, Any]:
    """Archive the client identity. Historical documents remain available."""
    result = client_master.set_archived(name, True)
    if result.get("ok"):
        result.update(archived=True, deleted=0)
    return result


def rename_client(old: str, new: str) -> dict[str, Any]:
    """Change the display name and retain the previous spelling as an alias.

    Saved documents are NOT rewritten. A quotation that went out saying "Old
    Name" still says it, because the PDF in the client's inbox does; the alias
    is what keeps that document in this client's history. Renaming onto a name
    another client already owns is refused rather than silently merging them -
    merging identities is an explicit operation in Clients Master Data.
    """
    current = client_master.get_record(old)
    if not current.get("ok") or not current.get("record"):
        return {"ok": False, "error": "Client not found."}
    if client_master.normalize(old) == client_master.normalize(new):
        return {"ok": True, "noop": True, "new": current["record"]["name"]}
    result = client_master.save_record({"id": current["record"]["id"], "name": new})
    if result.get("ok"):
        result["new"] = result["record"]["name"]
    return result

_DOC_TABLE = {
    "invoices": ("invoices", "inv_no"),
    "quotes":   ("quotes",   "quote_no"),
    "dns":      ("dns",       "dn_no"),
}


def delete_doc(doc_type: str, no: str) -> dict[str, Any]:
    """Delete one client document RECORD from the database and return its
    pdf_path so the caller can recycle the file. doc_type is one of
    'invoices' / 'quotes' / 'dns' (the Clients-profile tabs)."""
    m = _DOC_TABLE.get(doc_type)
    no = (no or "").strip()
    if not m:
        return {"ok": False, "error": f"Unknown document type: {doc_type}"}
    if not no:
        return {"ok": False, "error": "No document number."}
    table, pk = m
    with _connect() as conn:
        row = conn.execute(f"SELECT pdf_path FROM {table} WHERE {pk} = ?", (no,)).fetchone()
        path = row["pdf_path"] if row else None
        cur = conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (no,))
        conn.commit()
    return {"ok": True, "path": path, "deleted": cur.rowcount}


def _bank_labels() -> dict[str, str]:
    out = {}
    for b in getattr(config, "INV_BANK_INFO", []) or []:
        if isinstance(b, dict) and b.get("key"):
            out[b["key"]] = b.get("label") or b["key"]
    return out


# --------------------------------------------------------------------------- #
# Client list (sidebar)
# --------------------------------------------------------------------------- #

def client_list() -> list[dict[str, Any]]:
    """Every client (from the catalog + any client_name seen on a document),
    with a quick summary: doc counts, total invoiced, last activity."""
    agg: dict[str, dict[str, Any]] = {}
    identities = client_master.canonical_map()

    def slot(name: str) -> dict[str, Any]:
        name = client_master.canonical(name, identities)
        key = client_master.normalize(name)
        if key not in agg:
            agg[key] = {
                "name": name.strip(), "quotes": 0, "invoices": 0, "dns": 0,
                "invoiced_total": 0.0, "last_activity": "",
            }
        return agg[key]

    def touch(entry: dict[str, Any], when: str | None) -> None:
        if when and when > (entry["last_activity"] or ""):
            entry["last_activity"] = when

    with _connect() as conn:
        for r in conn.execute(
            "SELECT client_name AS n, COUNT(*) AS c, MAX(updated_at) AS m FROM quotes "
            "WHERE TRIM(COALESCE(client_name,'')) <> '' "
            "AND TRIM(COALESCE(pdf_path,'')) <> '' GROUP BY client_name COLLATE NOCASE"
        ):
            e = slot(r["n"]); e["quotes"] += r["c"]; touch(e, r["m"])
        for r in conn.execute(
            "SELECT client_name AS n, COUNT(*) AS c, SUM(grand_total) AS t, MAX(updated_at) AS m "
            "FROM invoices WHERE TRIM(COALESCE(client_name,'')) <> '' "
            "AND TRIM(COALESCE(pdf_path,'')) <> '' GROUP BY client_name COLLATE NOCASE"
        ):
            e = slot(r["n"]); e["invoices"] += r["c"]
            e["invoiced_total"] += round(float(r["t"] or 0), 2); touch(e, r["m"])
        for r in conn.execute(
            "SELECT client_name AS n, COUNT(*) AS c, MAX(updated_at) AS m FROM dns "
            "WHERE TRIM(COALESCE(client_name,'')) <> '' GROUP BY client_name COLLATE NOCASE"
        ):
            e = slot(r["n"]); e["dns"] += r["c"]; touch(e, r["m"])
    # Include shared catalog clients that have no documents in this profile.
    for record in client_master.suggestions("", 500):
        slot(record["name"])

    out = list(agg.values())
    for e in out:
        e["doc_count"] = e["quotes"] + e["invoices"] + e["dns"]
    # Stable sorts, least-significant first: name -> recent activity -> has-docs.
    out.sort(key=lambda e: e["name"].lower())
    out.sort(key=lambda e: e["last_activity"] or "", reverse=True)
    out.sort(key=lambda e: e["doc_count"] == 0)   # clients with no docs last
    return out


# --------------------------------------------------------------------------- #
# Single client profile
# --------------------------------------------------------------------------- #

def client_profile(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "No client name."}

    alias_keys = client_master.alias_keys_for(name)
    names_sql = ",".join("?" for _ in alias_keys)
    name = client_master.canonical(name)
    with _connect() as conn:
        inv = [dict(r) for r in conn.execute(
            "SELECT inv_no, inv_date, inv_type, client_address, lpo_no, payment_bank_key, "
            "withholding_included, grand_total, pdf_path, updated_at, created_at "
            f"FROM invoices WHERE client_key(client_name) IN ({names_sql}) "
            "ORDER BY updated_at DESC", tuple(alias_keys))]
        quo = [dict(r) for r in conn.execute(
            "SELECT quote_no, quote_date, quote_type, client_address, rfq_no, terms_json, "
            "rows_json, pdf_path, updated_at, created_at "
            f"FROM quotes WHERE client_key(client_name) IN ({names_sql}) "
            "ORDER BY updated_at DESC", tuple(alias_keys))]
        dns = [dict(r) for r in conn.execute(
            "SELECT dn_no, dn_date, client_address, lpo_no, pdf_path, updated_at, created_at "
            f"FROM dns WHERE client_key(client_name) IN ({names_sql}) "
            "ORDER BY updated_at DESC", tuple(alias_keys))]

    # App-only pricing drafts remain in the document lists so they can be opened
    # and completed, but they are not issued quotes/invoices and must not affect
    # client totals or learned commercial defaults.
    official_inv = [r for r in inv if str(r.get("pdf_path") or "").strip()]
    official_quo = [r for r in quo if str(r.get("pdf_path") or "").strip()]

    # ---- aggregates ----
    addr_counter: Counter = Counter()
    for r in inv + quo + dns:
        a = (r.get("client_address") or "").strip()
        if a:
            addr_counter[a] += 1
    addresses = [{"address": a, "count": c} for a, c in addr_counter.most_common()]
    default_address = addresses[0]["address"] if addresses else ""

    # Recent distinct LPOs (invoices + DNs), newest first.
    lpos: list[str] = []
    seen_lpo = set()
    for r in sorted(official_inv + dns, key=lambda x: x.get("updated_at") or "", reverse=True):
        lp = (r.get("lpo_no") or "").strip()
        if lp and lp.upper() != "N/A" and lp.lower() not in seen_lpo:
            seen_lpo.add(lp.lower()); lpos.append(lp)
    lpos = lpos[:8]

    # Payment: most common bank + withholding among invoices.
    bank_labels = _bank_labels()
    bank_counter = Counter((r.get("payment_bank_key") or "").strip() for r in official_inv if (r.get("payment_bank_key") or "").strip())
    bank_key = bank_counter.most_common(1)[0][0] if bank_counter else ""
    wh_vals = [int(r.get("withholding_included") or 0) for r in official_inv]
    withholding = None
    if wh_vals:
        withholding = (sum(wh_vals) * 2 >= len(wh_vals))   # majority included?

    # Typical quotation terms - most frequent individual term lines.
    term_counter: Counter = Counter()
    for r in official_quo:
        try:
            for t in json.loads(r.get("terms_json") or "[]"):
                t = str(t).strip()
                if t:
                    term_counter[t] += 1
        except (TypeError, ValueError):
            pass
    typical_terms = [{"text": t, "count": c} for t, c in term_counter.most_common(6)]

    invoiced_total = round(sum(_num(r.get("grand_total")) for r in official_inv), 2)
    all_when = [r.get("updated_at") for r in (inv + quo + dns) if r.get("updated_at")]
    all_created = [r.get("created_at") for r in (inv + quo + dns) if r.get("created_at")]

    # Signed-document sets (one query per type) to badge each row.
    inv_signed = signing.signed_doc_nos("inv")
    qut_signed = signing.signed_doc_nos("qut")
    dn_signed  = signing.signed_doc_nos("dn")

    # ---- document lists shaped for the UI ----
    inv_docs = [{
        "no": r["inv_no"], "date": r.get("inv_date") or "", "type": r.get("inv_type") or "",
        "total": round(_num(r.get("grand_total")), 2),
        "path": r.get("pdf_path") or "", "exists": _exists(r.get("pdf_path")),
        "signed": bool(r.get("pdf_path")) and (r["inv_no"] in inv_signed),
        "incomplete_price": not bool(str(r.get("pdf_path") or "").strip()),
        "updated_at": r.get("updated_at") or "",
    } for r in inv]
    quo_docs = [{
        "no": r["quote_no"], "date": r.get("quote_date") or "", "type": r.get("quote_type") or "",
        "value": _quote_value(r.get("rows_json")),
        "path": r.get("pdf_path") or "", "exists": _exists(r.get("pdf_path")),
        "signed": bool(r.get("pdf_path")) and (r["quote_no"] in qut_signed),
        "incomplete_price": not bool(str(r.get("pdf_path") or "").strip()),
        "updated_at": r.get("updated_at") or "",
    } for r in quo]
    dn_docs = [{
        "no": r["dn_no"], "date": r.get("dn_date") or "",
        "lpo": r.get("lpo_no") or "",
        "path": r.get("pdf_path") or "", "exists": _exists(r.get("pdf_path")),
        "signed": (r["dn_no"] in dn_signed),
        "updated_at": r.get("updated_at") or "",
    } for r in dns]

    return {
        "ok": True,
        "name": name,
        "summary": {
            "invoices": len(official_inv), "quotes": len(official_quo), "dns": len(dns),
            "invoiced_total": invoiced_total,
            "first_seen": (min(all_created) if all_created else ""),
            "last_activity": (max(all_when) if all_when else ""),
        },
        "default_address": default_address,
        "addresses": addresses,
        "lpos": lpos,
        "payment": {
            "bank_key": bank_key,
            "bank_label": bank_labels.get(bank_key, bank_key),
            "withholding": withholding,            # True / False / None
            "typical_terms": typical_terms,
        },
        "docs": {"invoices": inv_docs, "quotes": quo_docs, "dns": dn_docs},
    }
