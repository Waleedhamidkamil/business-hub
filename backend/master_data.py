"""
Item Master - one place for everything autocomplete remembers.

**The problem this replaces.** What the app used to call "Autofill Suggestions"
was thirteen list views over seven tables, and the SAME physical item appeared
in up to four of them: `items` (POs), `quote_items` (per quote type), `dn_items`
and `inv_items` (per invoice type). On the live Abraj catalog that is ~2,800
rows describing perhaps 700 real things. Renaming an item meant finding and
fixing it four times; a wrong unit stayed wrong everywhere you did not look.
One of the thirteen (`column_memory`, ~1,800 rows of individual cell values) is
an internal index no human should ever have been asked to browse.

**What this module does instead.** It presents ONE row per item, gathered by
name across every catalog, and fans writes back out to whichever underlying
rows exist. Nothing is migrated and no table is dropped: those tables are what
the editors' autocomplete actually reads, and rewriting them is a risk with no
payoff. The unification is a read model plus a write fan-out.

    identity   the normalised name (`_norm`), so "Copper Cable" and
               "copper  cable" are one item - the same rule the duplicate
               finder already used, kept deliberately identical.
    variants   the physical rows behind that identity. Several are normal:
               one per document type, plus one per differing (unit,
               description) combination - that is how the catalogs record
               "the same thing, described two ways".
    sources    which document types know the item, i.e. where a change here
               will be felt.

**Row plumbing is NOT reimplemented here.** Every write goes through
`data_manager`'s catalog section machinery (`_upsert_row`, `update_catalog_entry`,
`delete_catalog_entry`), which already handles the two storage shapes - explicit
columns in `items`, a `data_json` blob everywhere else - and recomputes
`data_sig` correctly. This module only decides WHICH rows to touch.

Money note: `cost_usd` is the only cost figure that can be autofilled. A row
carrying a raw `cost` without it predates the 2026-08 currency fix and its
currency was never recorded, so replaying it could put 84,000 in a cell that
should read 60. Those are surfaced as a fixable flag rather than silently used.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, data_manager, database, item_images, profile_manager, shared_library, sqlite_policy

# --------------------------------------------------------------------------- #
# Which catalogs describe an ITEM, and what to call each in the UI.
# `section` is the data_manager catalog that owns that table's row plumbing.
# --------------------------------------------------------------------------- #
SOURCES: list[dict[str, str]] = [
    {"key": "po",           "section": "cat_po_items",      "label": "Purchase Order", "short": "PO"},
    {"key": "qut:materials", "section": "cat_qut_mat_items", "label": "Quotation · Materials", "short": "QUT"},
    {"key": "qut:equipment", "section": "cat_qut_eq_items",  "label": "Quotation · Equipment", "short": "QUT"},
    {"key": "qut:manpower",  "section": "cat_qut_mp_items",  "label": "Quotation · Manpower",  "short": "QUT"},
    {"key": "qut:service",   "section": "cat_qut_sv_items",  "label": "Quotation · Service",   "short": "QUT"},
    {"key": "dn",            "section": "cat_dn_items",      "label": "Delivery Note", "short": "DN"},
    {"key": "inv:materials", "section": "cat_inv_mat_items", "label": "Invoice · Materials", "short": "INV"},
    {"key": "inv:equipment", "section": "cat_inv_eq_items",  "label": "Invoice · Equipment", "short": "INV"},
    {"key": "inv:manpower",  "section": "cat_inv_mp_items",  "label": "Invoice · Manpower",  "short": "INV"},
    {"key": "inv:service",   "section": "cat_inv_sv_items",  "label": "Invoice · Service",   "short": "INV"},
]
_SOURCE_BY_KEY = {s["key"]: s for s in SOURCES}

# The duplicate-review tool deliberately works on the three material catalogs
# only.  QUT equipment/manpower/service, Invoice equivalents, and PO memory
# often share a word while describing different things, so they stay out.
MATERIAL_SOURCES = frozenset(("qut:materials", "dn", "inv:materials"))

# An item untouched for this long is "dormant" - surfaced, never auto-deleted.
DORMANT_DAYS = 180

FLAGS = [
    {"key": "no_cost",     "label": "No cost",        "hint": "nothing to autofill a cost from"},
    {"key": "legacy_cost", "label": "Unusable cost",  "hint": "a cost is stored but its currency was never recorded, so it is not autofilled"},
    {"key": "no_price",    "label": "No price",       "hint": "no rate remembered"},
    {"key": "no_unit",     "label": "No unit",        "hint": "no unit of measure"},
    {"key": "has_image",   "label": "Has picture",    "hint": "a picture is remembered for this item"},
    {"key": "split",       "label": "Split across rows", "hint": "more than one stored variant - merging tidies autocomplete"},
    {"key": "dormant",     "label": f"Unused {DORMANT_DAYS}+ days", "hint": "not used in any document for a long time"},
]


def _connect() -> sqlite3.Connection:
    return shared_library.connect()


def _norm(name: str) -> str:
    """Item identity. Deliberately the SAME normalisation the catalog duplicate
    finder uses - two views of the catalog that disagree on what counts as the
    same name would be worse than either alone."""
    return data_manager._normalize_name(name or "")


def _num(v: Any) -> float | None:
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _s(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _days_since(iso: str) -> int | None:
    s = _s(iso)[:19]
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return (datetime.now() - datetime.strptime(s, fmt)).days
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Load every catalog row into one shape
# --------------------------------------------------------------------------- #

def _sub_where(c: dict[str, Any]) -> tuple[str, tuple]:
    """The catalog section's own row filter.

    `sub_filter` is already a ready-made `("quote_type = ?", "materials")`
    pair - NOT (column, value). Reading it as the latter silently selected
    nothing, which showed up as the quotation and invoice catalogs contributing
    zero items while the PO and DN ones worked.
    """
    sub = c.get("sub_filter")
    if not sub:
        return "", ()
    clause, value = sub
    return f" WHERE {clause}", (value,)


def _variants(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every row of every ITEM catalog, normalised to a common shape.

    The two storage shapes are handled here and nowhere else: `items` keeps
    description/unit/company in real columns, the rest keep everything in a
    `data_json` blob.
    """
    out: list[dict[str, Any]] = []
    for src in SOURCES:
        c = data_manager._catalog_by_key(src["section"])
        if not c:
            continue
        where, params = _sub_where(c)
        try:
            rows = conn.execute(f'SELECT * FROM "{c["table"]}"{where}', params).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            row = dict(r)
            name = _s(row.get(c["name_col"]))
            if not name:
                continue
            data: dict[str, Any] = {}
            blob = row.get(c["data_json_col"]) if c.get("data_json_col") else row.get("data_json")
            if blob:
                try:
                    data = json.loads(blob) or {}
                except (TypeError, ValueError):
                    data = {}
            if not isinstance(data, dict):
                data = {}
            unit = _s(data.get("unit")) or _s(row.get("unit"))
            desc = _s(data.get("description")) or _s(row.get("description"))
            out.append({
                "source": src["key"],
                "section": src["section"],
                "source_label": src["label"],
                "source_short": src["short"],
                "pk": {k: row.get(k) for k in c["pk_cols"]},
                "name": name,
                "unit": unit,
                "description": desc,
                "company": _s(row.get("last_company")),
                "rate": _num(data.get("rate")),
                # The price AS TYPED, with its own unit - same rule as the
                # cost. `rate` beside it is the dollar figure for rows that
                # came from a document (quotations and DNs sell in USD, and
                # `api.save_inv` normalises an invoice rate back to USD at
                # that invoice's own rate), so those are genuinely dollars.
                "price_native": _num(data.get("price_native")),
                "price_native_ccy": _ccy(data.get("price_native_ccy")),
                "cost_raw": _s(data.get("cost")),
                "cost_usd": _num(data.get("cost_usd")),
                # The cost AS TYPED, with its own unit. `cost_usd` beside it
                # is the dollar figure for rows that came from a document
                # (which knew its own rate at save time and is therefore
                # correct); a master-typed dinar cost has no dollar figure at
                # all, on purpose - the document supplies the rate.
                "item_no": _s(data.get("item_no")),
                "cost_native": _num(data.get("cost_native")),
                "cost_native_ccy": _ccy(data.get("cost_native_ccy")),
                # How this item PREFERS to be shown and typed. The stored
                # numbers stay USD - margins, the leaderboard and the paste
                # resolver all compare items, and they can only do that in
                # one unit. This is the unit the human reads, nothing more.
                "money_ccy": _ccy(data.get("money_ccy")),
                # `price_ccy` falls back to `money_ccy`, which used to govern
                # BOTH figures - so items stored before the split keep
                # showing exactly what they showed.
                "price_ccy": _ccy(data.get("price_ccy")) or _ccy(data.get("money_ccy")),
                "image_refs": data.get("image_refs") if isinstance(data.get("image_refs"), dict) else {},
                "uses": int(row.get("use_count") or 0),
                "last_used": _s(row.get("last_used_at")),
                "data": data,
            })
    return out


def _ccy(value: Any) -> str:
    """'USD' | 'IQD' | '' - anything unrecognised falls back rather than
    guessing, because guessing wrong divides a dollar figure by 1400."""
    v = _s(value).upper()
    return v if v in ("USD", "IQD") else ""


def _ident(name: Any, description: Any = "", unit: Any = "") -> str:
    """What makes two stored rows THE SAME item.

    Name + description + unit - the same three fields the catalog's own
    `data_sig` keys a variant on, so this read model agrees with how the rows
    are actually stored instead of inventing a looser rule. Folding on the name
    alone merged "Hemp rope phi12mm" into "Hemp rope phi20mm".

    The separator cannot appear in a normalised value (`_norm` strips
    punctuation), so the parts can never run together.
    """
    return "|".join((_norm(name), _norm(description), _norm(unit)))


def _fold(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the physical rows into one record per item identity."""
    by_id: dict[str, dict[str, Any]] = {}
    for v in variants:
        if not _norm(v["name"]):
            continue
        key = _ident(v["name"], v["description"], v["unit"])
        it = by_id.setdefault(key, {
            "id": key, "spellings": {}, "sources": {}, "variants": [],
            "uses": 0, "last_used": "",
        })
        it["variants"].append(v)
        it["uses"] += v["uses"]
        it["spellings"][v["name"]] = it["spellings"].get(v["name"], 0) + max(1, v["uses"])
        it["sources"][v["source"]] = it["sources"].get(v["source"], 0) + max(1, v["uses"])
        if v["last_used"] > it["last_used"]:
            it["last_used"] = v["last_used"]

    items: list[dict[str, Any]] = []
    for it in by_id.values():
        vs = it["variants"]
        # The most-used spelling is the one the user actually types.
        display = max(it["spellings"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        # "Best known" = from the most-used variant that has the field at all.
        ranked = sorted(vs, key=lambda v: (-v["uses"], v["last_used"]), reverse=False)

        def best(field):
            for v in ranked:
                if v.get(field):
                    return v[field]
            return ""

        def best_num(field):
            for v in ranked:
                if v.get(field) is not None:
                    return v[field]
            return None

        image_ref = ""
        for v in ranked:
            if v["image_refs"]:
                image_ref = next(iter(v["image_refs"].values()), "")
                if image_ref:
                    break

        native = best_num("cost_native")
        native_ccy = next((v["cost_native_ccy"] for v in ranked
                           if v["cost_native"] is not None and v["cost_native_ccy"]),
                          "USD")
        stored_usd = best_num("cost_usd")
        if native is None and stored_usd is not None:
            # Nothing native yet: a dollar figure from a document save IS the
            # native value, in dollars.
            native, native_ccy = stored_usd, "USD"
        # `cost` is a COMPARISON figure only - it lines a dinar cost up beside
        # a dollar price so Margin and "highest cost" can sort. It is derived
        # at the profile rate and never reaches a document; every document
        # converts the NATIVE value at its own rate instead.
        if native is None:
            cost_usd = None
        elif native_ccy == "IQD":
            cost_usd = round(native / float(config.IQD_PER_USD or 1), 4)
        else:
            cost_usd = native
        has_legacy_cost = any(v["cost_raw"] and v["cost_usd"] is None
                              and v["cost_native"] is None for v in vs)
        item_no = next((v["item_no"] for v in ranked if v["item_no"]), "")
        money_ccy = next((v["money_ccy"] for v in ranked if v["money_ccy"]), "USD")
        price_ccy = next((v["price_ccy"] for v in ranked if v["price_ccy"]), money_ccy)
        unit = best("unit")
        p_native = best_num("price_native")
        p_ccy = next((v["price_native_ccy"] for v in ranked
                      if v["price_native"] is not None and v["price_native_ccy"]),
                     "USD")
        stored_rate = best_num("rate")
        if p_native is None and stored_rate is not None:
            p_native, p_ccy = stored_rate, "USD"
        # `price` is the COMPARISON figure (margin, "highest price" sort),
        # derived at the profile rate and never sent to a document.
        if p_native is None:
            price = None
        elif p_ccy == "IQD":
            price = round(p_native / float(config.IQD_PER_USD or 1), 4)
        else:
            price = p_native
        age = _days_since(it["last_used"])

        flags = []
        if cost_usd is None and not has_legacy_cost:
            flags.append("no_cost")
        if cost_usd is None and has_legacy_cost:
            flags.append("legacy_cost")
        if price is None:
            flags.append("no_price")
        if not unit:
            flags.append("no_unit")
        if image_ref:
            flags.append("has_image")
        if len(vs) > 1:
            flags.append("split")
        if age is not None and age >= DORMANT_DAYS:
            flags.append("dormant")

        items.append({
            "id": it["id"],
            # The one stable handle on this item: identity is name +
            # description + unit and therefore moves when any of those is
            # edited, but the number does not.
            "item_no": item_no,
            "name": display,
            "spellings": sorted(it["spellings"], key=lambda n: -it["spellings"][n]),
            "unit": unit,
            "description": best("description"),
            "company": best("company"),
            "price": price,
            "cost": cost_usd,
            # `price`/`cost` are ALWAYS USD; these two say only how to SHOW
            # them, and they are independent - buying in dinars while selling
            # in dollars is ordinary. The margin below is computed from the
            # USD pair, so it is unaffected by either.
            "money_ccy": money_ccy,       # the COST display unit
            "price_ccy": price_ccy,       # the PRICE display unit
            # What was actually typed, and in what. This is what documents use.
            "cost_native": native,
            "cost_native_ccy": native_ccy if native is not None else "",
            "price_native": p_native,
            "price_native_ccy": p_ccy if p_native is not None else "",
            "price_is_approx": bool(p_native is not None and p_ccy == "IQD"),
            "cost_is_approx": bool(native is not None and native_ccy == "IQD"),
            "margin": (round((price - cost_usd) / price * 100, 1)
                       if (price and cost_usd is not None and price > 0) else None),
            "image_ref": image_ref,
            "sources": sorted(it["sources"], key=lambda k: -it["sources"][k]),
            "uses": it["uses"],
            "last_used": it["last_used"],
            "days_idle": age,
            "variant_count": len(vs),
            "flags": flags,
        })
    return items


def _join_language_pairs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Show بصل and `Onion` as ONE row.

    Identity is name + description + unit, so the two languages can never meet
    on it: the names differ and so do the unit WORDS (كيلو against KG). What
    says they are one product is the pairing the catalogs learned - and that
    now lives in `master_items`, which is the point of the record.

    Applied to the LIST only, never inside `_fold`. `item_detail` folds too,
    and `save_item` fans a patch across every `variant_row` it returns - so
    merging there would let one rename overwrite the item's OTHER-language
    name. The list may show one row; editing stays scoped to one identity.
    """
    from . import master_items            # cycle: master_items imports this
    pairs = master_items.name_pairs()
    if not pairs:
        return items

    by_norm: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_norm.setdefault(_norm(it["name"]), []).append(it)

    merged_into: dict[int, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for it in items:
        if id(it) in merged_into:
            continue
        partner_norm = pairs.get(_norm(it["name"]))
        mate = None
        for cand in by_norm.get(partner_norm or "", ()):
            if cand is it or id(cand) in merged_into:
                continue
            if master_items.units_agree(cand["unit"], it["unit"]):
                mate = cand
                break
        if mate is None:
            out.append(it)
            continue
        # BOTH sides are spent. Marking only the mate let a name with several
        # variants on the other side pair with the same partner twice and emit
        # the identity twice - "مخلل خيار" and "فستق مقشر" each appeared as a
        # duplicate row.
        merged_into[id(mate)] = it
        merged_into[id(it)] = it
        # The row keeps the identity, flags and money of the side the user is
        # most likely to be looking at - the busier one - and simply gains the
        # other name, so nothing about editing or costing changes.
        primary, secondary = (it, mate) if it["uses"] >= mate["uses"] else (mate, it)
        row = dict(primary)
        row["also_named"] = secondary["name"]
        row["also_id"] = secondary["id"]
        row["uses"] = it["uses"] + mate["uses"]
        row["variant_count"] = it["variant_count"] + mate["variant_count"]
        row["sources"] = sorted(set(it["sources"]) | set(mate["sources"]))
        row["last_used"] = max(it["last_used"], mate["last_used"])
        for field in ("cost_native", "price_native", "item_no", "image_ref",
                      "description", "company"):
            if not row.get(field) and secondary.get(field):
                row[field] = secondary[field]
                if field == "cost_native":
                    row["cost_native_ccy"] = secondary["cost_native_ccy"]
                    row["cost"] = secondary["cost"]
                elif field == "price_native":
                    row["price_native_ccy"] = secondary["price_native_ccy"]
                    row["price"] = secondary["price"]
        out.append(row)
    return out


def _load() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        return _join_language_pairs(_fold(_variants(conn)))
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Public - browse
# --------------------------------------------------------------------------- #

_SORTS = {
    "used":  lambda i: (-i["uses"], i["name"].lower()),
    "recent": lambda i: (i["last_used"] == "", _neg_str(i["last_used"]), i["name"].lower()),
    "name":  lambda i: i["name"].lower(),
    "price": lambda i: (i["price"] is None, -(i["price"] or 0)),
    "cost":  lambda i: (i["cost"] is None, -(i["cost"] or 0)),
    "idle":  lambda i: (i["days_idle"] is None, -(i["days_idle"] or 0)),
}


def _neg_str(s: str) -> tuple:
    """Sort a timestamp DESCENDING inside an ascending sort key."""
    return tuple(-ord(ch) for ch in s)


def list_items(search: str = "", source: str = "", flags: Any = None,
               sort: str = "used", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """The Item Master list: one row per item, filtered and counted.

    `search` matches the material number, the name, any alternative spelling,
    the description or the
    unit - one box, because asking a user which field to search in is exactly
    the kind of friction this view exists to remove.
    """
    items = _load()
    q = _s(search).lower()
    want_flags = [f for f in (flags or []) if f]
    if isinstance(want_flags, str):
        want_flags = [want_flags]

    def matches(i):
        if source and source not in i["sources"]:
            return False
        for f in want_flags:
            if f not in i["flags"]:
                return False
        if not q:
            return True
        # The material number is the one handle that never moves, so it has
        # to be findable - typing "M-00386" or just "386" should land on it.
        if q in (i["item_no"] or "").lower():
            return True
        if q in i["name"].lower() or q in (i["description"] or "").lower():
            return True
        # The row may carry the item's OTHER-language name after the language
        # join. Without this, merging بصل احمر into `Red Onion` would make it
        # unfindable by the Arabic name the PO editor actually uses.
        if q in (i.get("also_named") or "").lower():
            return True
        if q in (i["unit"] or "").lower() or q in (i["company"] or "").lower():
            return True
        return any(q in s.lower() for s in i["spellings"])

    # Facets are counted against everything EXCEPT the facet being counted, so
    # a filter never shows itself as "0" once applied.
    def count_flag(f):
        return sum(1 for i in items
                   if f in i["flags"]
                   and (not source or source in i["sources"])
                   and (not q or matches_text(i, q)))

    def matches_text(i, qq):
        return (qq in i["name"].lower() or qq in (i["description"] or "").lower()
                or qq in (i["unit"] or "").lower() or qq in (i["company"] or "").lower()
                or qq in (i.get("also_named") or "").lower()
                or any(qq in s.lower() for s in i["spellings"]))

    filtered = [i for i in items if matches(i)]
    key = _SORTS.get(sort) or _SORTS["used"]
    filtered.sort(key=key)

    try:
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        limit, offset = 200, 0

    src_counts: dict[str, int] = {}
    for i in items:
        for s in i["sources"]:
            src_counts[s] = src_counts.get(s, 0) + 1

    return {
        "ok": True,
        "rows": filtered[offset:offset + limit],
        "total": len(filtered),
        "all_total": len(items),
        "offset": offset,
        "limit": limit,
        "sources": [{**s, "count": src_counts.get(s["key"], 0)} for s in SOURCES],
        "flags": [{**f, "count": count_flag(f["key"])} for f in FLAGS],
        "variant_rows": sum(i["variant_count"] for i in items),
    }


def item_detail(name: str) -> dict[str, Any]:
    """Everything known about one item, plus every physical row behind it.

    `name` is an IDENTITY (`name|description|unit`, from `list_items`) - or a
    bare item name, which every older caller passes. A bare name that matches
    several variants resolves to the most-used one and reports the rest in
    `sibling_variants`, rather than quietly editing whichever came first.
    """
    wanted = _s(name)
    ident = wanted if "|" in wanted else ""
    key = _norm(wanted.split("|")[0])
    if not key:
        return {"ok": False, "error": "No item."}
    conn = _connect()
    try:
        rows = _variants(conn)
    finally:
        conn.close()
    if ident:
        variants = [v for v in rows
                    if _ident(v["name"], v["description"], v["unit"]) == ident]
    else:
        variants = [v for v in rows if _norm(v["name"]) == key]
    if not variants:
        return {"ok": False, "error": "Item not found."}
    folded = _fold(variants)
    if len(folded) > 1:
        # The name matched several variants. An exact identity picks one; a
        # bare name gets the most-used, and `variants_of` lists the rest so the
        # caller can offer a choice rather than silently editing the wrong one.
        exact = [i for i in folded if i["id"] == _s(name)]
        folded.sort(key=lambda i: -i["uses"])
        item = exact[0] if exact else folded[0]
        item["sibling_variants"] = [
            {"id": i["id"], "description": i["description"], "unit": i["unit"],
             "uses": i["uses"]}
            for i in folded if i["id"] != item["id"]]
    else:
        item = folded[0]
        item["sibling_variants"] = []
    # ONLY this identity's rows. Building them from every row that shares the
    # NAME is what let a save reach into sibling variants and overwrite them -
    # the exact mechanism that merged "Hemp rope" phi12mm into phi20mm.
    variants = [v for v in variants
                if _ident(v["name"], v["description"], v["unit"]) == item["id"]]
    item["variant_rows"] = [{
        "source": v["source"], "source_label": v["source_label"],
        "section": v["section"], "pk": v["pk"], "name": v["name"],
        "unit": v["unit"], "description": v["description"],
        "rate": v["rate"], "cost_usd": v["cost_usd"],
        "cost_raw": v["cost_raw"], "money_ccy": v["money_ccy"],
        "price_ccy": v["price_ccy"],
        "cost_native": v["cost_native"], "cost_native_ccy": v["cost_native_ccy"],
        "item_no": v["item_no"],
        "price_native": v["price_native"], "price_native_ccy": v["price_native_ccy"],
        "has_image": bool(v["image_refs"]),
        # The refs themselves, so `save_item` can put them back. Without
        # this an edit here silently threw the item's picture away: the
        # refs are excluded from `extra` (they are not a readable field),
        # so nothing carried them into the rewritten row.
        "image_refs": v["image_refs"],
        "uses": v["uses"], "last_used": v["last_used"],
        # Only the keys a person would recognise; qty and the internal
        # bookkeeping fields are noise in a master-data view.
        "extra": {k: v["data"][k] for k in sorted(v["data"])
                  if k not in ("unit", "description", "rate", "cost", "cost_usd",
                               "cost_ccy", "money_ccy", "price_ccy",
                               "cost_native", "cost_native_ccy",
                               "price_native", "price_native_ccy", "item_no",
                               "image_refs",
                               "qty", "cost_qty",
                               "cost_sell", "total")
                  and _s(v["data"][k])},
    } for v in sorted(variants, key=lambda v: (-v["uses"], v["source"]))]
    return {"ok": True, "item": item}


def item_image(name: str) -> str:
    """The remembered picture for an item, as a data URI ("" when none)."""
    d = item_detail(name)
    ref = (d.get("item") or {}).get("image_ref") if d.get("ok") else ""
    if not ref:
        return ""
    try:
        return item_images.load(ref) or ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Search wording the user corrected by hand
# --------------------------------------------------------------------------- #
#
# The image search is right about eight times in ten and cannot get the last
# two by itself: "خيار" is both cucumber and stock option, "رقي" is both
# watermelon and refinement, "Bleach" is a cartoon to the entire internet. No
# amount of scoring resolves a genuine ambiguity in the wider world.
#
# So the user resolves it ONCE. The wording is stored against the item and
# reused forever, which is what turns a recurring 20% error rate into a
# shrinking one - every correction is permanent and applies in every future
# document, because the item memory is shared across all of them.

IMG_QUERY_KEY = "img_query"


def _rows_for(conn: sqlite3.Connection, name: str,
              description: str = "", unit: str = "") -> list[dict[str, Any]]:
    """Every stored row behind one item.

    Matched on the full identity when a description or unit is supplied, and
    on the name alone when only a name is known - which is the case for a
    document row, where the picture button lives.
    """
    rows = _variants(conn)
    if _s(description) or _s(unit):
        want = _ident(name, description, unit)
        hit = [v for v in rows
               if _ident(v["name"], v["description"], v["unit"]) == want]
        if hit:
            return hit
    n = _norm(name)
    return [v for v in rows if _norm(v["name"]) == n]


def _phrase_record_ids(conn, name: str) -> list[int]:
    """The record(s) an item name reaches - matched on either language."""
    key = _norm(name)
    if not key:
        return []
    out = []
    for r in conn.execute("SELECT item_id, name_en, name_ar FROM master_items"):
        if _norm(r["name_en"]) == key or _norm(r["name_ar"]) == key:
            out.append(r["item_id"])
    return out


def item_search_phrase(name: str, description: str = "", unit: str = "") -> str:
    """The wording this item was last found by, or "". Reads the RECORD's
    `img_query` column (2026-08-20); it used to fan out over the four catalogs."""
    try:
        from . import master_items
        conn = master_items._connect()
    except Exception:
        return ""
    try:
        master_items.init(conn)
        for iid in _phrase_record_ids(conn, name):
            got = conn.execute("SELECT img_query FROM master_items WHERE item_id = ?",
                               (iid,)).fetchone()
            if got and _s(got[0]):
                return _s(got[0])
        return ""
    finally:
        conn.close()


def remember_search_phrase(name: str, phrase: str, description: str = "",
                           unit: str = "") -> dict[str, Any]:
    """Store the wording that actually found this item's picture, on the item's
    own record (2026-08-20). One row per item now, so there is nothing to fan
    out to. An empty phrase CLEARS the memory - that is how a correction is
    undone."""
    phrase = (phrase or "").strip()
    if not (name or "").strip():
        return {"ok": False, "error": "No item."}
    try:
        from . import master_items
        conn = master_items._connect()
    except Exception:
        return {"ok": False, "error": "The item record is unavailable."}
    try:
        master_items.init(conn)
        ids = _phrase_record_ids(conn, name)
        if not ids:
            return {"ok": False, "error": "This item is not in Master Data yet."}
        now = datetime.now().isoformat(timespec="seconds")
        for iid in ids:
            conn.execute("UPDATE master_items SET img_query = ?, updated_at = ? "
                         "WHERE item_id = ?", (phrase or None, now, iid))
        conn.commit()
        return {"ok": True, "written": len(ids), "phrase": phrase}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Paste matching - turn pasted item names into known items
# --------------------------------------------------------------------------- #
#
# Pasting 20 rows out of Excel should not mean re-typing 20 units and 20 rates.
# This resolves each pasted name against the master and hands back what to fill.
#
# FOUR TIERS, and the split between them is the whole design:
#
#   exact      identical once normalised. Excel's damage lives here - a wrapped
#              cell ("Potato (Big \n\nSize)"), a trailing tab, CRLF, double
#              spaces, different case - because `_norm` collapses all of it.
#              This is not fuzzy matching and cannot be wrong.
#   contained  one name's words are a subset of the other's ("Potato (Big
#              Size) 5kg" vs "Potato (Big Size)"). Safe enough to fill.
#   similar    close but not provably the same ("Ball point pen" vs "Ballpoint
#              pen"). Returned as a SUGGESTION - the caller must not apply it
#              without the user, because a wrong rate reaching an issued
#              document is worse than typing one.
#   new        nothing close. The item is new; say so rather than guess.
#
# **A tie is never a match.** If two different master items score equally the
# result is demoted to `similar` with both offered, because silently picking
# one of two plausible items is exactly how a wrong price gets issued.

_TIER_EXACT, _TIER_CONTAINED, _TIER_SIMILAR, _TIER_NEW = "exact", "contained", "similar", "new"

# difflib ratio at or above this is worth SUGGESTING. Deliberately high: this
# tier never fills by itself, so its job is to be worth a glance, not to be right.
SIMILAR_MIN = 0.82

# A one-word item such as "Pipes" can describe a family, not one SKU.  A close
# singular/plural or prefix token is useful for OFFERING that family (Pipe,
# Pipe Wrench, Scaffolding pipe), but remains too broad to fill automatically.
PREFIX_TOKEN_MIN = 0.8
SUGGESTION_LIMIT = 20

# A `contained` match must also SHARE most of its words. Without this floor the
# rule "one name's tokens are a subset of the other's" makes every one-word
# master item swallow any query containing it - "Totally New Widget XYZ" was
# matched to "Widget" at 0.25 overlap and filled with its price. Containment
# is meant for a qualifier ("... 5kg"), not for a coincidence.
CONTAINED_MIN_OVERLAP = 0.5

# Editor doc_type -> the SOURCES prefix, so a quotation prefers a quotation's
# remembered rate. A delivery note's rate is not a quotation's rate.
_DOC_PREFIX = {"quote": "qut", "qut": "qut", "inv": "inv", "dn": "dn", "po": "po"}

# Which language a NAME is in. A PO is written in Arabic, every other document
# in English, so this decides which half of a paired item a paste fills with.
_ARABIC_NAME = re.compile(r"[؀-ۿ]")


def _toks(s: str) -> set:
    return {t for t in _norm(s).split(" ") if t}


def _single_token_prefix_score(query_tokens: set, item_tokens: set) -> float:
    """Score a close word prefix for the suggestion tier only.

    This catches a common pasted typo/plural ("Shampo", "Pipes") without
    turning a multi-word phrase into a loose match.  It deliberately returns
    a score only when at least 80% of both words is shared, so short/generic
    tokens such as "car" cannot spray unrelated suggestions across the UI.
    """
    if len(query_tokens) != 1:
        return 0.0
    query = next(iter(query_tokens))
    if len(query) < 4:
        return 0.0
    best = 0.0
    for item in item_tokens:
        if len(item) < 4 or not (query.startswith(item) or item.startswith(query)):
            continue
        best = max(best, min(len(query), len(item)) / max(len(query), len(item)))
    return best if best >= PREFIX_TOKEN_MIN else 0.0


def _items_from_records(lang: str = "en") -> list[dict[str, Any]]:
    """The resolver's item list, built from `master_items` (2026-08-19).

    It used to come from `_variants()` + `_fold()` - every row of all four
    catalogs, grouped back together on every paste. There is one row per item
    now, so the grouping is already done and the fold is pure overhead.

    Shaped exactly as `_fold` shaped it, because the 250 lines of matching
    below (tiers, typo tolerance, the refusal to guess between two plausible
    items) are unchanged and good. Only where the list comes FROM has moved.

    `lang` picks which half of a paired item a paste fills with: a PO is
    written in Arabic and everything else in English.
    """
    try:
        from . import master_items
    except Exception:
        return []
    conn = None
    try:
        conn = master_items._connect()
        master_items.init(conn)
        rows = conn.execute("SELECT * FROM master_items").fetchall()
        # Read once for the whole paste, not per row.
        stored_aliases = master_items.aliases_by_item(conn)
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    out: list[dict[str, Any]] = []
    for r in rows:
        name = master_items.prefer(r["name_en"], r["name_ar"], lang)
        if not _norm(name):
            continue
        # The description follows the NAME's own language, STRICT - no fallback.
        # A row showing an English name must carry its English description or
        # none; it must never fill the Arabic وصف beside an English item. (The
        # name itself may fall back - an item known in one language only is
        # still worth filling - so key off the name we actually chose, not the
        # requested lang.)
        name_ar = master_items.is_arabic(name)
        desc = str((r["description_ar"] if name_ar else r["description_en"]) or "")
        unit = master_items.prefer(r["unit_en"], r["unit_ar"], lang)
        try:
            img = json.loads(r["image_refs"] or "null")
        except (TypeError, ValueError):
            img = None
        ref = ""
        if isinstance(img, dict):
            ref = next((v for v in img.values() if v), "")
        elif isinstance(img, str):
            ref = img
        out.append({
            # The ITEM NUMBER is the identity now, not a `name|desc|unit`
            # string. It survives a rename, which the string never could.
            "id": r["item_no"] or _ident(name, desc, unit),
            "item_no": r["item_no"] or "",
            "item_id": r["item_id"],
            "name": name, "description": desc, "unit": unit,
            # The RAW slots, un-collapsed (2026-09-05). `name` above is the
            # language-collapsed display name and `prefer` FALLS BACK, so an
            # item known only in Arabic is listed under its Arabic name even
            # when the caller asked for English - and then matches at tier
            # `exact`. A caller that has to tell "already has English" from
            # "fell back to Arabic" cannot do it from `name`; these can.
            # They are also what says whether writing a name into the empty
            # slot would pair two products or fill a blank.
            "name_en": str(r["name_en"] or ""),
            "name_ar": str(r["name_ar"] or ""),
            "unit_en": str(r["unit_en"] or ""),
            "unit_ar": str(r["unit_ar"] or ""),
            # Both description slots too: `description` above is the NAME's
            # language only, and telling variants apart needs the line's own
            # language (`_line_variant`), whichever that is.
            "description_en": str(r["description_en"] or ""),
            "description_ar": str(r["description_ar"] or ""),
            # The item's OTHER-language name is a way to FIND it, never what
            # gets filled. Pasting an English list into an Arabic PO lands on
            # the item and writes بصل; the reverse writes `Onion`.
            # Other ways IN, never what gets filled: the item's own
            # other-language name, plus the spellings recorded against it
            # (2026-09-08) - the names a merge folded away, and anything the
            # catalogue review has been taught. Without these, merging deletes
            # the only route to the losing record's words and the next document
            # that uses them creates the duplicate again.
            "aliases": [n for n in (r["name_en"], r["name_ar"],
                                    *stored_aliases.get(r["item_id"], []))
                        if n and _norm(n) != _norm(name)],
            "company": "",
            "price_native": r["price_native"],
            "price_native_ccy": r["price_native_ccy"] or "",
            "cost_native": r["cost_native"],
            "cost_native_ccy": r["cost_native_ccy"] or "",
            "image_ref": ref,
            "uses": r["use_count"] or 0,
            "last_used": r["last_used_at"] or "",
            "variants": [],
            "sources": [],
        })
    return out


def _preferred_sources(doc_type: str, sub_type: str) -> list[str]:
    """Which catalogs to trust first for this document, best match first."""
    prefix = _DOC_PREFIX.get(_s(doc_type).lower(), "")
    if not prefix:
        return []
    sub = _s(sub_type).lower()
    exact = f"{prefix}:{sub}" if sub else prefix
    same_prefix = [s["key"] for s in SOURCES
                   if s["key"] == prefix or s["key"].startswith(prefix + ":")]
    ordered = ([exact] if exact in same_prefix else []) + [k for k in same_prefix if k != exact]
    return ordered


def _identity(item: dict[str, Any]) -> dict[str, Any]:
    """WHICH record matched, and what its name/description/unit slots hold.

    `fill` says what to write into a document; this says what to write BACK.
    A caller that wants to record the English name it just learned needs the
    `item_id` primary key (`save_record` takes nothing else - `id` here is the
    ITEM NUMBER, "M-00123", and `save_record` would reject it), and it needs to
    see the raw slots before it writes: filling a blank `name_ar` records a
    translation, but overwriting a full one merges two different products.

    Kept separate from `fill` on purpose - `fill` is "what this document should
    show" and these are "what the catalogue currently knows". Merging them
    would let a write-back key reach a document row.
    """
    return {
        "item_id": item.get("item_id"),
        "item_no": item.get("item_no", ""),
        "name_en": item.get("name_en", ""),
        "name_ar": item.get("name_ar", ""),
        "unit_en": item.get("unit_en", ""),
        "unit_ar": item.get("unit_ar", ""),
        # So a review can record the line's description on the variant the
        # user picked - into a BLANK slot only (po_handoff.js, 2026-10-03).
        "description_en": item.get("description_en", ""),
        "description_ar": item.get("description_ar", ""),
    }


def _line_variant(cands: list[dict[str, Any]], detail: Any) -> dict[str, Any] | None:
    """The ONE variant a document line names, or None (2026-10-03, A-117).

    A name can cover several real variants - دهن هيدروليك is 18 L and 20 L. A
    line also carries a description and a unit, and together those are the
    app's item identity: the same rule a document save files the line by
    (`master_items._matches`). Only an EXACT description in the line's own
    language counts, with a unit that agrees by meaning. "Does not contradict"
    is not enough here - that is a guess between variants, and the resolver's
    rule is that a tie is never a match.

    A blank description identifies nothing. In an editor a blank cell means
    "not filled yet", and the point of asking Master Data is to fill it.
    """
    if not isinstance(detail, dict):
        return None
    if not _norm(detail.get("description")):
        return None
    from . import master_items
    hits = [c for c in cands
            if master_items._describes(c, detail.get("description")) == 2
            and master_items._unit_fits(c, detail.get("unit"))]
    return hits[0] if len(hits) == 1 else None


def _fill_from(item: dict[str, Any], variants: list[dict[str, Any]],
               prefer: list[str], cost_currency: str, cost_peg: float,
               want_image: bool, skip_description: bool = False,
               sell_currency: str = "USD", sell_peg: float = 0.0) -> dict[str, Any]:
    """The values to drop into the pasted row.

    Ordered by trust: a variant from THIS document type first, then any other,
    then the item's folded best-known value. Only ever returns what it actually
    knows - a missing field is absent, never an empty string, so the caller can
    tell "no unit remembered" from "unit is blank".
    """
    def pick(field):
        for src in prefer:
            for v in variants:
                if v["source"] == src and v.get(field) not in (None, ""):
                    return v[field]
        for v in variants:
            if v.get(field) not in (None, ""):
                return v[field]
        return None

    out: dict[str, Any] = {}
    unit = pick("unit") or item.get("unit")
    if unit:
        out["unit"] = unit
    # On a partial match the words the query added are usually the description
    # ("Ballpoint pen BLACK"), so the master's own description would contradict
    # what was pasted - it filled "Red" over a row that said Black. Leave it.
    if not skip_description:
        desc = pick("description") or item.get("description")
        if desc:
            out["description"] = desc
    # The price is carried in the unit it was typed in and converted at the
    # SELL rate - the invoice's own (its pinned rate), not the cost rate, and
    # not a flat profile rate. Quotations and DNs always sell in USD.
    p_native = item.get("price_native")
    p_ccy = _s(item.get("price_native_ccy")).upper() or "USD"
    if p_native is None:
        p_native = pick("rate")
        p_ccy = "USD"
    if p_native is None:
        p_native = item.get("price")
        p_ccy = p_ccy or "USD"
    if p_native is not None:
        want = _s(sell_currency).upper() or "USD"
        out["rate"] = _cost_in(p_native, p_ccy, want, sell_peg)
        out["rate_ccy"] = want

    # The cost fills in the CURRENCY IT WAS SAVED IN (2026-08-20, Waleed) - a
    # cost saved in dinars fills as dinars, and the cell's own $/IQD toggle
    # shows it. No conversion to the document's cost currency. A raw `cost` with
    # no recorded currency is still never replayed: it could put 84,000 where 60
    # belongs, so only a native value WITH its recorded currency is trusted.
    native, nccy = _item_native(item, pick)
    if native is not None and _s(nccy):
        out["cost"] = native
        out["cost_ccy"] = _s(nccy).upper()
        if _s(nccy).upper() == "USD":
            out["cost_usd"] = native

    if want_image and item.get("image_ref"):
        out["image_ref"] = item["image_ref"]
    return out


def _cost_in(native: float, native_ccy: str, want: str, peg: float) -> Any:
    """`native` expressed in `want`, at `peg`.

    When the units already match the number is handed over UNTOUCHED - no rate,
    no rounding drift, nothing to be wrong about. That is the common case once
    costs are stored as typed: a dinar item landing in a dinar-costed invoice.
    """
    want = _s(want).upper() or "USD"
    src = _s(native_ccy).upper() or "USD"
    if src == want:
        return native
    rate = float(peg) if (peg and float(peg) > 0) else float(config.IQD_PER_USD)
    return round(native * rate) if want == "IQD" else round(native / rate, 2)


def _item_native(item: dict[str, Any], pick=None):
    """(value, currency) for an item's cost, or (None, '').

    Falls back to a stored `cost_usd` for rows written before costs were kept
    as typed - those came from a document that knew its own rate, so they are
    genuinely dollars.
    """
    native = item.get("cost_native")
    ccy = _s(item.get("cost_native_ccy")).upper()
    if native is None and pick is not None:
        native = pick("cost_usd")
        ccy = "USD"
    if native is None:
        native = item.get("cost")
        ccy = ccy or "USD"
    return (native, ccy or "USD") if native is not None else (None, "")


def resolve_items(names: Any, doc_type: str = "", sub_type: str = "",
                  cost_currency: str = "USD", cost_peg: float = 0.0,
                  want_image: bool = False,
                  sell_currency: str = "USD", sell_peg: float = 0.0,
                  details: Any = None) -> dict[str, Any]:
    """Match pasted item names against the master. ONE call for a whole paste.

    Returns one result per input, in input order, each carrying its tier, the
    matched item, what to fill, and (for `similar`) the alternatives to choose
    between. The caller decides what to apply - this function never mutates
    anything and never decides on the user's behalf.

    `details`, when given, is one `{description, unit}` per name, in the same
    order: what the document line says beside the name. It only ever settles a
    name shared by several variants - see `_line_variant`.
    """
    queries = [_s(n) for n in (names or [])]
    details = list(details) if isinstance(details, (list, tuple)) else []
    if not queries:
        return {"ok": True, "results": [], "summary": {}}

    # THE RECORD, not the four catalogs (2026-08-19, step 4b). A PO is written
    # in Arabic and every other document in English, so the list is built in
    # the asking document's language - pasting an English list into a PO still
    # lands, through the pairing below, but what it FILLS is Arabic.
    lang = "ar" if _s(doc_type).lower() == "po" else "en"
    items = _items_from_records(lang)
    by_id = {i["id"]: i for i in items}
    # A paste gives a NAME, while an item's identity is name + description +
    # unit. So index by name - and when one name covers several variants
    # ("Hemp rope" phi12 / phi15 / phi20) that is an AMBIGUITY, not a match:
    # the resolver's standing rule is that a tie is never a match, because
    # silently picking one of several plausible items is how a wrong price
    # reaches an issued document.
    by_name: dict[str, list] = {}
    for i in items:
        for spelling in (i["name"], *i.get("aliases", [])):
            k = _norm(spelling)
            if k and i not in by_name.setdefault(k, []):
                by_name[k].append(i)
    for lst in by_name.values():
        lst.sort(key=lambda i: -i["uses"])
    # One record per item, so there are no variants to prefer between. The
    # parameter stays because `_fill_from` falls back through it.
    vars_by_id: dict[str, list] = {}
    prefer = _preferred_sources(doc_type, sub_type)
    # The item's other-language name, so a paste in either language lands.
    # Read once for the whole paste rather than per row.
    try:
        from . import master_items
        _pair_index = master_items.name_pairs()
    except Exception:
        _pair_index = {}
    # Keep the readable name separate from the internal identity.  The latter
    # includes description + unit (for variant safety), which made a typo such
    # as "Shampo" fail to find a master name "Shampoo" when its description
    # was "good quality bottle".
    tok_index = [(i["id"], _toks(i["name"]), _norm(i["name"])) for i in items]

    def candidate(iid: str, score: float, skip_description: bool = False) -> dict[str, Any]:
        """Return one selectable suggestion, including its own fill values."""
        it = by_id[iid]
        return {
            "id": it["id"],
            "name": it["name"],
            "description": it.get("description", ""),
            "unit": it.get("unit", ""),
            "score": round(score, 3),
            **_identity(it),
            "fill": _fill_from(
                it, vars_by_id.get(iid, []), prefer,
                cost_currency, cost_peg, want_image,
                skip_description=skip_description,
                sell_currency=sell_currency, sell_peg=sell_peg,
            ),
        }

    def ordered_suggestions(scored: list[tuple[float, str]]) -> list[tuple[float, str]]:
        """Keep variants available while showing different item names first.

        A generic query like "Pipes" often has several variants of plain
        "Pipe".  Listing those first used to hide "Pipe Wrench" and
        "Scaffolding pipe" behind a five-row cutoff.  The first pass gives one
        choice per distinct name; later passes include the remaining variants.
        """
        groups: dict[str, list[tuple[float, str]]] = {}
        for score, iid in scored:
            groups.setdefault(_norm(by_id[iid]["name"]), []).append((score, iid))
        for choices in groups.values():
            choices.sort(key=lambda choice: (
                -choice[0], -by_id[choice[1]].get("uses", 0), choice[1]))
        grouped = sorted(groups.values(), key=lambda choices: (
            -choices[0][0], -by_id[choices[0][1]].get("uses", 0),
            _norm(by_id[choices[0][1]]["name"])))
        ordered: list[tuple[float, str]] = []
        depth = 0
        while len(ordered) < SUGGESTION_LIMIT:
            added = False
            for choices in grouped:
                if depth < len(choices):
                    ordered.append(choices[depth])
                    added = True
                    if len(ordered) >= SUGGESTION_LIMIT:
                        break
            if not added:
                break
            depth += 1
        return ordered

    results = []
    for qi, q in enumerate(queries):
        key = _norm(q)
        res = {"query": q, "tier": _TIER_NEW, "name": "", "score": 0.0,
               "fill": {}, "alternatives": []}
        if not key:
            results.append(res)
            continue

        # --- tier 1: exact after normalisation -------------------------------
        # A paste may use the item's OTHER-language name: an English list going
        # into an Arabic PO, or the reverse. Tried only when the name as given
        # finds NOTHING, so this can never turn an existing match into an
        # ambiguity - it strictly recovers pastes that used to come back empty.
        # No "name not found, try its partner" fallback: a paired name is
        # itself a catalog name by construction (the pairing is BUILT from
        # catalog rows), so that branch was unreachable - dead code that no
        # test could ever reach.
        lookup = key
        if lookup in by_name and len(by_name[lookup]) == 1:
            # The name landed - but a PO is written in Arabic and every other
            # document in English. When the match is the OTHER language of a
            # known pair, fill the document's own language instead. That is not
            # a guess between candidates (which the "a tie is never a match"
            # rule forbids); it is the same product under its other name, and
            # both are single, so nothing is being chosen on the user's behalf.
            want_arabic = _s(doc_type).lower() == "po"
            it = by_name[lookup][0]
            if bool(_ARABIC_NAME.search(it["name"])) != want_arabic:
                partner = _pair_index.get(_norm(it["name"]))
                if partner and len(by_name.get(partner) or []) == 1:
                    lookup = partner

        if lookup in by_name:
            cands = by_name[lookup]
            named = _line_variant(cands, details[qi] if qi < len(details) else None)                 if len(cands) > 1 else None
            if named is not None:
                cands = [named]
            if len(cands) == 1:
                it = cands[0]
                res.update(tier=_TIER_EXACT, name=it["name"], score=1.0,
                           **_identity(it),
                           fill=_fill_from(it, vars_by_id.get(it["id"], []), prefer,
                                           cost_currency, cost_peg, want_image,
                                           sell_currency=sell_currency,
                                           sell_peg=sell_peg))
            else:
                # The name is right but it names several real variants. Offer
                # them; fill nothing.
                res.update(tier=_TIER_SIMILAR, name=cands[0]["name"], score=1.0)
                res["alternatives"] = [
                    {"name": c["name"], "id": c["id"], "item_no": c["item_no"],
                     "description": c["description"], "unit": c["unit"], "score": 1.0}
                    for c in cands[:6]]
                res["suggestions"] = [candidate(c["id"], 1.0) for c in cands[:6]]
                res["ambiguous_variants"] = True
            results.append(res)
            continue

        qt = _toks(q)
        if not qt:
            results.append(res)
            continue

        # --- tier 2: one name's words contain the other's --------------------
        contained = []
        for iid, it_toks, _ in tok_index:
            if not it_toks:
                continue
            if qt <= it_toks or it_toks <= qt:
                overlap = len(qt & it_toks) / max(len(qt | it_toks), 1)
                if overlap >= CONTAINED_MIN_OVERLAP:
                    contained.append((overlap, iid))
        if contained:
            contained.sort(reverse=True)
            top = contained[0][0]
            tied = [c for c in contained if abs(c[0] - top) < 1e-9]
            if len(tied) == 1:
                iid = tied[0][1]
                it = by_id[iid]
                # The query carrying EXTRA words means those words describe this
                # instance; the master's description would talk over them.
                extra = bool(qt - _toks(it["name"]))
                res.update(tier=_TIER_CONTAINED, name=it["name"], score=round(top, 3),
                           **_identity(it),
                           fill=_fill_from(it, vars_by_id.get(iid, []), prefer,
                                           cost_currency, cost_peg, want_image,
                                           skip_description=extra))
                results.append(res)
                continue
            # A tie is NOT a match - offer the choice instead of picking one.
            # When a short generic item and a more specific item have the same
            # Jaccard score ("Mop" vs "Cotton Rectangular Cleaning Mop" for
            # "Cleaning Mop"), put the candidate covering more query words
            # first. It is still a choice, never an automatic fill.
            tied.sort(key=lambda c: (
                len(qt & _toks(by_id[c[1]]["name"])),
                by_id[c[1]].get("uses", 0),
            ), reverse=True)
            res["alternatives"] = [{"name": by_id[i]["name"], "score": round(s, 3)}
                                   for s, i in tied[:5]]
            res["suggestions"] = [
                candidate(i, s, skip_description=bool(qt - _toks(by_id[i]["name"])))
                for s, i in tied[:5]
            ]
            res.update(tier=_TIER_SIMILAR, score=round(top, 3))
            results.append(res)
            continue

        # --- tier 3: close enough to suggest, never to apply ------------------
        scored = []
        for iid, it_toks, item_name in tok_index:
            if not it_toks:
                continue
            jac = len(qt & it_toks) / max(len(qt | it_toks), 1)
            ratio = data_manager._similarity(key, item_name)
            prefix = _single_token_prefix_score(qt, it_toks)
            score = max(jac, ratio, prefix)
            if score >= SIMILAR_MIN or prefix or (jac >= 0.6 and ratio >= 0.6):
                scored.append((score, iid))
        if scored:
            scored = ordered_suggestions(scored)
            res["alternatives"] = [{"name": by_id[i]["name"], "score": round(s, 3)}
                                   for s, i in scored]
            res["suggestions"] = [candidate(i, s) for s, i in scored]
            res.update(tier=_TIER_SIMILAR, score=round(scored[0][0], 3))
            # The best candidate's values ride along so accepting is one click,
            # but `fill` stays EMPTY: nothing is applied without the user.
            # For the same reason NO `item_id` is set on the result itself
            # (2026-09-05) - every candidate carries its own identity inside
            # `suggestions[]`, so a caller that writes back to the catalogue
            # has to go through the one the user picked. A top-level id here
            # would let a write land on a guess.
            best = by_id[scored[0][1]]
            res["suggested_fill"] = _fill_from(best, vars_by_id.get(scored[0][1], []),
                                               prefer, cost_currency, cost_peg, want_image,
                                               sell_currency=sell_currency,
                                               sell_peg=sell_peg)
            res["name"] = best["name"]
        results.append(res)

    summary = {t: sum(1 for r in results if r["tier"] == t)
               for t in (_TIER_EXACT, _TIER_CONTAINED, _TIER_SIMILAR, _TIER_NEW)}
    summary["filled"] = summary[_TIER_EXACT] + summary[_TIER_CONTAINED]
    return {"ok": True, "results": results, "summary": summary,
            "preferred_sources": prefer}


# --------------------------------------------------------------------------- #
# Public - maintain
# --------------------------------------------------------------------------- #

# `money_ccy` (cost) and `price_ccy` (price) are display-only, but they are
# edited and fanned out exactly like the rest, so an item shown in dinars
# stays that way in every catalog it lives in. They are INDEPENDENT: an item
# bought in dinars and sold in dollars is ordinary, and since both figures
# are stored in USD there is nothing unsafe about the pair disagreeing.
# The frontend converts what was typed into USD before sending - `rate` and
# `cost_usd` crossing this boundary are ALWAYS dollars.
_EDITABLE = ("name", "unit", "description", "rate",
             "cost_native", "cost_native_ccy", "cost_usd",
             "price_native", "price_native_ccy",
             "money_ccy", "price_ccy")


def save_item(name: str, patch: dict[str, Any] | None) -> dict[str, Any]:
    """Apply an edit to EVERY physical row behind one item.

    That fan-out is the whole point: a unit fixed here is fixed for the
    quotation, the delivery note and the invoice at once, which is what made
    the old thirteen-list view unusable. Empty / omitted fields are left alone
    - this is a patch, not a replace, so blanking a field is an explicit "".
    """
    patch = {k: v for k, v in (patch or {}).items() if k in _EDITABLE}
    if not patch:
        return {"ok": False, "error": "Nothing to change."}
    detail = item_detail(name)
    if not detail.get("ok"):
        return detail

    new_name = _s(patch.get("name"))
    touched = 0
    errors: list[str] = []
    for v in detail["item"]["variant_rows"]:
        c = data_manager._catalog_by_key(v["section"])
        if not c:
            continue
        values: dict[str, Any] = {c["name_col"]: new_name or v["name"]}
        # Re-state every variant column, or update_catalog_entry writes blanks
        # over the fields this patch does not mention.
        for col in c["variant_cols"]:
            k = col["key"]
            if k in patch:
                values[k] = _s(patch[k])
            elif k == "description":
                values[k] = v["description"]
            elif k == "unit":
                values[k] = v["unit"]
            elif k == "last_company":
                values[k] = detail["item"]["company"]
            else:
                values[k] = _s((v.get("extra") or {}).get(k))
        if c.get("data_json_col"):
            # A blob table: carry the rest of the payload through untouched.
            for k, val in (v.get("extra") or {}).items():
                values.setdefault(k, val)
            values["unit"] = _s(patch.get("unit", v["unit"]))
            values["description"] = _s(patch.get("description", v["description"]))
            # The price is stored AS TYPED, exactly like the cost. `rate`
            # (the dollar figure the rest of the app still reads) is written
            # only when the native unit already IS dollars.
            if "price_native" in patch:
                pn = _num(patch["price_native"])
                pc = _ccy(patch.get("price_native_ccy")) or "USD"
                if pn is not None:
                    values["price_native"] = pn
                    values["price_native_ccy"] = pc
                    if pc == "USD":
                        values["rate"] = pn
                    else:
                        values.pop("rate", None)
            else:
                if v["price_native"] is not None:
                    values["price_native"] = v["price_native"]
                    values["price_native_ccy"] = v["price_native_ccy"] or "USD"
                if "rate" in patch:
                    values["rate"] = _s(patch["rate"])
                elif v["rate"] is not None:
                    values["rate"] = v["rate"]
            # The cost is stored AS TYPED. `cost_usd` is written only when
            # the native unit already IS dollars - inventing one for a dinar
            # cost would need a rate this layer has no business choosing.
            if "cost_native" in patch:
                nat = _num(patch["cost_native"])
                nccy = _ccy(patch.get("cost_native_ccy")) or "USD"
                if nat is not None:
                    values["cost_native"] = nat
                    values["cost_native_ccy"] = nccy
                    if nccy == "USD":
                        values["cost_usd"] = nat
                    else:
                        values.pop("cost_usd", None)
            else:
                if v["cost_native"] is not None:
                    values["cost_native"] = v["cost_native"]
                    values["cost_native_ccy"] = v["cost_native_ccy"] or "USD"
                if "cost_usd" in patch:
                    usd = _num(patch["cost_usd"])
                    if usd is not None:
                        values["cost_usd"] = usd
                elif v["cost_usd"] is not None:
                    values["cost_usd"] = v["cost_usd"]
            # An unrecognised currency is IGNORED, not written and not
            # allowed to drop what is already stored - the same "fall back
            # rather than guess" rule `_ccy` follows, since guessing wrong
            # here misreads every figure on the item by a factor of 1400.
            # Both are written EXPLICITLY, even the one this patch does
            # not mention. `price_ccy` falls back to `money_ccy` when it
            # has never been stored (so rows predating the split still
            # read right) - but leaving it unstored would mean switching
            # the cost to dinars silently dragged the price with it.
            # Pinning its CURRENT effective value stops that.
            for field in ("money_ccy", "price_ccy"):
                asked = _ccy(patch.get(field)) if field in patch else ""
                chosen = asked or v[field] or _ccy(detail["item"].get(field))
                if chosen:
                    values[field] = chosen
            # Neither the picture nor the material number is edited from this
            # form, and both are excluded from `extra`, so they are carried
            # through explicitly or a save would drop them.
            if v.get("image_refs"):
                values["image_refs"] = v["image_refs"]
            if v.get("item_no"):
                values["item_no"] = v["item_no"]
        r = data_manager.update_catalog_entry(v["section"], v["pk"], values)
        if r.get("ok"):
            touched += 1
        else:
            errors.append(f'{v["source"]}: {r.get("error")}')
    return {"ok": touched > 0, "updated": touched,
            "name": new_name or detail["item"]["name"],
            "errors": errors}


def migrate_costs_to_native(rate: float = 1400.0,
                            apply: bool = False) -> dict[str, Any]:
    """One-time: record every stored cost in the currency it was typed in.

    Two populations, and they are NOT the same:

      * Costs that arrived from a document save are genuinely dollars - that
        path knew the document's own rate at save time. They become native USD,
        unchanged, losslessly.

      * Costs typed through Master Data with the dinar chip were divided by the
        profile rate on the way in, so the stored dollar figure is an artefact
        (and a profile-dependent one: read under a 1500 profile it comes back
        as a different dinar number than was typed). Multiplying back by the
        rate they were divided BY recovers the original figure exactly, and the
        fabricated `cost_usd` is then dropped - the document supplies the rate
        from now on.

    `money_ccy == "IQD"` is the marker for the second group: only `save_item`
    ever writes it. `rate` defaults to 1400 because that is the Abraj profile
    rate those entries were made under - the preview prints every recovered
    number so it can be checked before anything is written.
    """
    rate = float(rate) if rate and float(rate) > 0 else float(config.IQD_PER_USD)
    conn = _connect()
    try:
        variants = _variants(conn)
    finally:
        conn.close()
    items = _fold(variants)

    plan: list[dict[str, Any]] = []
    for it in items:
        if it["cost_native"] is None:
            continue
        already = any(v["cost_native"] is not None
                      for v in variants if _norm(v["name"]) == it["id"])
        if already:
            continue
        if it["money_ccy"] == "IQD":
            plan.append({"id": it["id"], "name": it["name"], "from_usd": it["cost"],
                         "native": round(it["cost"] * rate),
                         "ccy": "IQD", "recovered": True})
        else:
            plan.append({"id": it["id"], "name": it["name"], "from_usd": it["cost"],
                         "native": it["cost"], "ccy": "USD", "recovered": False})

    result = {"ok": True, "rate": rate, "total": len(plan),
              "recovered": sum(1 for p in plan if p["recovered"]),
              "plan": plan, "applied": 0}
    if not apply or not plan:
        return result

    for p in plan:
        patch = {"cost_native": p["native"], "cost_native_ccy": p["ccy"]}
        # By IDENTITY - addressing by name would write this cost into every
        # variant that shares the name.
        if save_item(p["id"], patch).get("ok"):
            result["applied"] += 1
    return result


# --------------------------------------------------------------------------- #
# Material numbers, and rebuilding variants the catalog lost
# --------------------------------------------------------------------------- #

ITEM_NO_PREFIX = "M-"
ITEM_NO_WIDTH = 5


_JSON_COL_CACHE: dict[str, str] = {}


def _json_col_on(conn: sqlite3.Connection, table: str) -> str:
    """`data_json` if that table really has one.

    `CATALOG_SECTIONS` declares `data_json_col=None` for **cat_po_items**, and
    that is stale rather than deliberate: the `items` table gained a
    `data_json` column when PO items learned to remember a picture, and the
    registry entry - which describes description/unit/last_company as explicit
    columns - was never updated. The consequence was silent: every write
    through `_set_data_key` returned False for PO items, so `assign_item_numbers`
    skipped them and a remembered search phrase evaporated.

    Asking the table rather than the registry fixes both without touching what
    the registry means for the Master Data browse/edit screens, which is where
    `data_json_col` has its other, deliberate effects.
    """
    if not table:
        return ""
    if table in _JSON_COL_CACHE:
        return _JSON_COL_CACHE[table]
    try:
        cols = {r[1] for r in conn.execute(f"pragma table_info({table})")}
    except Exception:
        return ""
    got = "data_json" if "data_json" in cols else ""
    _JSON_COL_CACHE[table] = got
    return got


def _set_data_key(conn, variant: dict[str, Any], key: str, value: Any) -> bool:
    """Write one key into a stored row's `data_json`, IN PLACE.

    Safe precisely because `key` is in `_SIG_IGNORE`: `data_sig` - half the
    row's primary key - does not depend on it, so the row keeps its identity
    and cannot collide with a sibling.
    """
    c = data_manager._catalog_by_key(variant["section"])
    if not c:
        return False
    col = c.get("data_json_col") or _json_col_on(conn, c.get("table"))
    if not col:
        return False
    data = dict(variant.get("data") or {})
    if data.get(key) == value:
        return False
    if value is None:
        # None REMOVES the key rather than storing a null - a cleared value
        # should leave no trace, so nothing downstream has to tell the two
        # apart.
        if key not in data:
            return False
        data.pop(key, None)
    else:
        data[key] = value
    where, params = data_manager._pk_where(c["pk_cols"], variant["pk"])
    try:
        conn.execute(
            f'UPDATE "{c["table"]}" SET {col} = ? WHERE {where}',
            (json.dumps(data, ensure_ascii=False, sort_keys=True), *params))
    except sqlite3.Error:
        return False
    return True


def assign_item_numbers(apply: bool = False) -> dict[str, Any]:
    """Give every item a stable material number, and keep it.

    The number is the ONE thing about an item that never changes - rename it,
    re-unit it, re-describe it, and the number still points at the same thing.
    Identity is (name + description + unit), which by definition moves when any
    of those is edited; the number is what survives that.

    Assigned per IDENTITY and written to every physical row behind it, so all
    of an item's catalog copies carry the same number.
    """
    conn = _connect()
    try:
        variants = _variants(conn)
        used: set[int] = set()
        by_ident: dict[str, list] = {}
        existing: dict[str, str] = {}
        for v in variants:
            ident = _ident(v["name"], v["description"], v["unit"])
            by_ident.setdefault(ident, []).append(v)
            no = _s((v.get("data") or {}).get("item_no"))
            if no.startswith(ITEM_NO_PREFIX):
                existing.setdefault(ident, no)
                try:
                    used.add(int(no[len(ITEM_NO_PREFIX):]))
                except ValueError:
                    pass

        nxt = 1
        assigned = 0
        rows = 0
        for ident in sorted(by_ident):
            no = existing.get(ident)
            if not no:
                while nxt in used:
                    nxt += 1
                used.add(nxt)
                no = f"{ITEM_NO_PREFIX}{nxt:0{ITEM_NO_WIDTH}d}"
                assigned += 1
            if apply:
                for v in by_ident[ident]:
                    if _set_data_key(conn, v, "item_no", no):
                        rows += 1
        if apply:
            conn.commit()
        return {"ok": True, "items": len(by_ident), "assigned": assigned,
                "rows_written": rows, "already_numbered": len(existing)}
    finally:
        conn.close()


def rebuild_variants_from_documents(apply: bool = False) -> dict[str, Any]:
    """Put back item variants the catalog lost but the documents still have.

    Saving in Master Data used to stamp ONE description across every row behind
    a name, so genuine variants ("Hemp rope" phi12mm / phi15mm / phi20mm)
    collided and merged. Your documents are the record and still hold every
    combination actually used, so the identities are recoverable.

    **Identity only** - name, description and unit. Deliberately no price and
    no cost: a rate or cost lifted out of a document row would need that
    document's currency and rate to mean anything, and inventing one here is
    the class of bug this whole area has been fixing. Money is relearned the
    next time a document holding the item is saved, which is the path that
    records the currency properly.
    """
    from . import profile_manager

    conn = _connect()
    try:
        have = {_ident(v["name"], v["description"], v["unit"]) for v in _variants(conn)}
    finally:
        conn.close()

    seen: dict[str, dict[str, Any]] = {}
    profiles = ([{"id": profile_manager.active_profile_id()}]
                if profile_manager.legacy_fallback_enabled()
                else profile_manager.list_profiles())
    for prof in profiles:
        db = profile_manager.profile_db_path(prof["id"])
        if not db.is_file():
            continue
        c = sqlite_policy.open_db(db)
        try:
            for table, pk in _DOC_TABLES:
                kind = _DOC_KIND[table]
                try:
                    rows = c.execute(
                        f'SELECT {pk} AS no, rows_json FROM "{table}"').fetchall()
                except sqlite3.Error:
                    continue
                for r in rows:
                    try:
                        items = json.loads(r["rows_json"] or "[]")
                    except (TypeError, ValueError):
                        continue
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        nm = _row_name(it)
                        desc, unit = _s(it.get("description")), _s(it.get("unit"))
                        if not nm:
                            continue
                        ident = _ident(nm, desc, unit)
                        if ident in have or ident in seen:
                            continue
                        seen[ident] = {"ident": ident, "name": nm, "description": desc,
                                       "unit": unit, "kind": kind, "doc": r["no"]}
        finally:
            c.close()

    plan = sorted(seen.values(), key=lambda d: (d["name"].lower(), d["description"]))
    result = {"ok": True, "missing": len(plan), "restored": 0,
              "sample": [f'{p["name"]} - {p["description"] or "(no description)"}'
                         f' [{p["unit"] or "no unit"}]' for p in plan[:15]]}
    if not apply or not plan:
        return result

    for p in plan:
        payload = {"description": p["description"], "unit": p["unit"]}
        try:
            if p["kind"] == "dn":
                database.remember_dn_item(p["name"], payload)
            elif p["kind"] == "inv":
                database.remember_inv_item("materials", p["name"], payload)
            elif p["kind"] == "quote":
                database.remember_quote_item("materials", p["name"], payload)
            else:
                database.remember_item(p["name"], p["description"], p["unit"], "")
            result["restored"] += 1
        except Exception:
            pass
    return result


def migrate_prices_to_native(apply: bool = False) -> dict[str, Any]:
    """One-time: record every stored price as native USD.

    Unlike the cost, there is nothing to RECOVER here. Every stored `rate`
    genuinely is dollars: quotations and delivery notes sell in USD, and
    `api.save_inv` normalises an invoice's rate back to USD at that invoice's
    own pinned rate before remembering it. The handful of items whose
    `price_ccy` says IQD only inherited that as a DISPLAY preference from the
    cost chip - their figures (0.94, 1.50, 48.00) are plainly dollars, and
    multiplying them by a rate would invent an error rather than undo one.

    So this only stamps the unit that was always true, which makes the price
    self-describing from here on.
    """
    conn = _connect()
    try:
        variants = _variants(conn)
    finally:
        conn.close()
    plan = []
    for it in _fold(variants):
        if it["price_native"] is None:
            continue
        if any(v["price_native"] is not None for v in variants
               if _ident(v["name"], v["description"], v["unit"]) == it["id"]):
            continue
        plan.append({"id": it["id"], "name": it["name"], "native": it["price_native"]})

    result = {"ok": True, "total": len(plan), "applied": 0}
    if not apply or not plan:
        return result
    for pl in plan:
        if save_item(pl["id"], {"price_native": pl["native"],
                                "price_native_ccy": "USD"}).get("ok"):
            result["applied"] += 1
    return result


def merge_items(canonical: str, names: Any) -> dict[str, Any]:
    """Fold several item names into one. Use counts survive (they are summed by
    the upsert), so merging never makes an item look less used than it was."""
    target_key = _s(canonical)
    if not target_key:
        return {"ok": False, "error": "No item to merge into."}
    # The Master Data list addresses rows by IDENTITY (`name|description|unit`
    # from `_ident`), and that is what the merge button sends. Using it as the
    # new name wrote the identity string INTO the name: five rows across
    # quote_items and inv_items became "bed|1m*2m without mattress|ea".
    #
    # Resolve it to the item's real stored name instead. That also restores the
    # original casing, which `_ident` normalises away - splitting on "|" would
    # have renamed "Bed" to "bed".
    target_detail = item_detail(target_key)
    if not target_detail.get("ok"):
        return {"ok": False, "error": "No item to merge into."}
    target = _s(target_detail["item"]["name"])
    target_ident = _s(target_detail["item"].get("id")) or _norm(target)
    if not target:
        return {"ok": False, "error": "No item to merge into."}
    # Normalised, NOT exact - and that is deliberate. Two spellings of one
    # name ("ROPE" / "Rope") are the same key to `item_detail`, which resolves
    # by `_norm`, so "merging" them can only rename an item onto itself. This
    # was changed to an exact comparison during a 2026-08-14 catalog cleanup
    # and the result was worse: 22 merges reported success and nothing moved.
    #
    # Names differing only in case are usually NOT duplicates here anyway.
    # "ROPE" is a ROLL with no description; "Rope" is 8mm nylon braided rope
    # by the METRE. Identity is (name + description + unit) precisely so those
    # stay apart, and folding them would put the wrong unit on a document.
    def _same_item(n: Any) -> bool:
        """Is `n` the target itself? Compare RESOLVED identities - the caller
        may send either an identity or a bare name, and a bare name that
        resolves to the target must not be 'merged' onto itself."""
        s = _s(n)
        if not s or s == target_key or s == target_ident:
            return True
        d = item_detail(s)
        return bool(d.get("ok")) and _s(d["item"].get("id")) == target_ident

    others = [n for n in (names or []) if _s(n) and not _same_item(n)]
    if not others:
        return {"ok": False, "error": "Nothing to merge."}
    merged = 0
    for other in others:
        r = save_item(other, {"name": target})
        if r.get("ok"):
            merged += 1
    return {"ok": merged > 0, "merged": merged, "name": target}


# --------------------------------------------------------------------------- #
# REMOVED 2026-09-05 - the "reviewed material duplicate cleanup" feature.
#
# `material_duplicate_review()` / `apply_material_duplicate_review()` and their
# ten `_material_*` helpers read the four catalogs (`items`, `quote_items`,
# `dn_items`, `inv_items`) that were physically DROPPED on 2026-08-20. From that
# day the review returned `{"groups": 0}` unconditionally, so the Master Data
# screen's "Material duplicates" button told the user they had NO duplicate
# materials - while the live "Possible duplicates" chip was finding 64. A dead
# feature is bad; one that answers confidently and wrongly is worse.
#
# Its 11-test file passed for a fortnight by skipping all 11.
#
# The live duplicate detection is `master_items.duplicate_groups()` /
# `_group_duplicates()` - a different module, which shared nothing with this
# cluster (checked before removal).
# --------------------------------------------------------------------------- #




def delete_items(names: Any, source: str = "") -> dict[str, Any]:
    """Forget one or more items - from one document type, or from all of them.

    Saved documents are untouched: this only removes what autocomplete offers.
    """
    wanted = [n for n in (names or []) if _s(n)]
    if not wanted:
        return {"ok": False, "error": "Nothing selected."}
    if source and source not in _SOURCE_BY_KEY:
        return {"ok": False, "error": f"unknown source: {source}"}
    removed = 0
    for n in wanted:
        d = item_detail(n)
        if not d.get("ok"):
            continue
        for v in d["item"]["variant_rows"]:
            if source and v["source"] != source:
                continue
            r = data_manager.delete_catalog_entry(v["section"], v["pk"])
            if r.get("ok"):
                removed += int(r.get("deleted") or 1)
    return {"ok": removed > 0, "deleted": removed}


# --------------------------------------------------------------------------- #
# Public - health
# --------------------------------------------------------------------------- #

def health() -> dict[str, Any]:
    """What is still missing from the catalog, and how much of it.

    Reads the RECORD (2026-08-19). It used to fold the four document catalogs
    and count their rows - 3,000 rows describing 800 items - so the headline
    number answered "how much is stored" rather than "how much is finished".
    Every entry is a COUNT plus the filter that shows exactly those items, so
    the panel is a set of links into the work rather than a report to read.
    """
    try:
        from . import master_items
    except Exception:
        return {"ok": False, "error": "The item record is unavailable."}
    listing = master_items.list_records(limit=1)
    missing = listing.get("missing_counts", {}) or {}
    total = listing.get("all_total", 0)

    # The same names the Master Data chips use, in the order the work is
    # usually done: name it, unit it, price it, cost it, picture it.
    gaps = [
        {"key": "name_ar", "label": "No Arabic name",
         "hint": "A PO is written in Arabic, so it cannot be quoted from here yet."},
        {"key": "name_en", "label": "No English name",
         "hint": "Needed for the quotation, delivery note and invoice."},
        {"key": "unit", "label": "No unit at all"},
        {"key": "unit_ar", "label": "No Arabic unit",
         "hint": "Asked only of items that already have an Arabic name."},
        {"key": "price", "label": "No price"},
        {"key": "cost", "label": "No cost"},
        {"key": "image", "label": "No picture"},
    ]
    for g in gaps:
        g["count"] = missing.get(g["key"], 0)

    conn = _connect()
    try:
        try:
            cell_memory = conn.execute(
                "SELECT COUNT(*) FROM column_memory").fetchone()[0]
        except sqlite3.Error:
            cell_memory = 0
        # The four document catalogs are still WRITTEN on every save, but
        # nothing reads them for a suggestion any more. Counted so the panel
        # can say how much is left to retire.
        legacy = {}
        for src in SOURCES:
            c = data_manager._catalog_by_key(src["section"])
            if not c:
                continue
            where, params = _sub_where(c)
            try:
                legacy[src["key"]] = conn.execute(
                    f'SELECT COUNT(*) FROM "{c["table"]}"{where}', params).fetchone()[0]
            except sqlite3.Error:
                legacy[src["key"]] = 0
    finally:
        conn.close()

    return {
        "ok": True,
        "items": total,
        "gaps": gaps,
        "complete": sum(1 for g in gaps if g["count"] == 0) == len(gaps),
        "cell_memory": cell_memory,
        "legacy_rows": sum(legacy.values()),
        "rows_by_source": legacy,
        "images": image_report(),
        "dormant_days": DORMANT_DAYS,
        # Kept so an older panel build does not blow up on a missing key.
        "rows": sum(legacy.values()),
        "counts": {g["key"]: g["count"] for g in gaps},
        "split_names": 0,
        "flags": FLAGS,
    }


# --------------------------------------------------------------------------- #
# Backfill - recover pictures already sitting inside saved documents
# --------------------------------------------------------------------------- #
#
# Items only started remembering their picture on 2026-08-05 (quotations) and
# 2026-08-11 (POs). Everything drawn or pasted before that is still there - as
# a base64 data URI inside the document's own `rows_json` - but the catalog
# never learned it, so picking the item again offered no picture. Measured on
# the live data the day this was written: 283 image cells across 22 documents,
# 239 distinct items, 234 of them with no picture in the master.
#
# Three rules make this safe to run:
#
#   * **Documents are never touched.** This reads `rows_json` and writes only to
#     the shared catalog and the picture store.
#   * **Exact names only.** A backfill that guesses would attach the wrong photo
#     to hundreds of items at once, silently. `contained`/`similar` are fine
#     when a human is watching a paste; they are not fine here.
#   * **An item that already has a picture keeps it.** The stored one was set
#     deliberately and recently; an old document must not overwrite it.
#
# Idempotent by construction: the store is content-addressed, so re-running
# produces the same refs and re-writes the same values.

# The document tables that can carry an image cell, per profile DB.
_DOC_TABLES = (("quotes", "quote_no"), ("pos", "po_no"),
               ("dns", "dn_no"), ("invoices", "inv_no"))

# Which row key holds the item's NAME. First non-empty wins - the primary
# column differs per document type (item / equipment / role / description).
_NAME_KEYS = ("item", "equipment", "role", "name", "description")


def _row_name(row: dict[str, Any]) -> str:
    """The item name out of a saved document row - the primary column differs
    per document type (item / equipment / role / name / description)."""
    for k in _NAME_KEYS:
        if _s(row.get(k)):
            return _s(row[k])
    return ""


def _document_pictures() -> dict[str, dict[str, Any]]:
    """{normalised name: {name, column, data_uri, doc, when}} - the NEWEST
    picture found for each item across every profile's saved documents."""
    from . import profile_manager

    found: dict[str, dict[str, Any]] = {}
    profiles = ([{"id": profile_manager.active_profile_id()}]
                if profile_manager.legacy_fallback_enabled()
                else profile_manager.list_profiles())
    for prof in profiles:
        db = profile_manager.profile_db_path(prof["id"])
        if not db.is_file():
            continue
        conn = sqlite_policy.open_db(db)
        try:
            for table, pk in _DOC_TABLES:
                try:
                    rows = conn.execute(
                        f'SELECT {pk} AS no, rows_json, updated_at FROM "{table}"'
                    ).fetchall()
                except sqlite3.Error:
                    continue
                for r in rows:
                    try:
                        items = json.loads(r["rows_json"] or "[]")
                    except (TypeError, ValueError):
                        continue
                    when = _s(r["updated_at"])
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        name = ""
                        for k in _NAME_KEYS:
                            if _s(it.get(k)):
                                name = _s(it[k])
                                break
                        if not name:
                            continue
                        key = _norm(name)
                        if not key:
                            continue
                        for col, val in it.items():
                            if not (isinstance(val, str) and val.startswith("data:image/")):
                                continue
                            prev = found.get(key)
                            if prev and prev["when"] >= when:
                                continue        # keep the newest document's picture
                            found[key] = {"name": name, "column": col, "data_uri": val,
                                          "doc": r["no"], "when": when}
        finally:
            conn.close()
    return found


def _attach_image_ref(conn: sqlite3.Connection, variant: dict[str, Any],
                      column: str, ref: str) -> bool:
    """Merge one picture reference into a stored catalog row, IN PLACE.

    An in-place `data_json` UPDATE is safe here and a delete-then-reinsert is
    not: `image_refs` is in `_SIG_IGNORE`, so the row's `data_sig` - half its
    primary key - does not depend on it. Re-keying the row would be a much
    bigger operation for no gain, and would risk colliding with a sibling.
    """
    c = data_manager._catalog_by_key(variant["section"])
    if not c:
        return False
    data = dict(variant.get("data") or {})
    refs = dict(data.get("image_refs") or {})
    if refs.get(column) == ref:
        return False                       # already exactly this - nothing to do
    refs[column] = ref
    data["image_refs"] = refs
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True)
    col = c.get("data_json_col") or "data_json"
    where, params = data_manager._pk_where(c["pk_cols"], variant["pk"])
    try:
        conn.execute(f'UPDATE "{c["table"]}" SET {col} = ? WHERE {where}',
                     (blob, *params))
    except sqlite3.Error:
        return False
    return True


def backfill_images(apply: bool = False) -> dict[str, Any]:
    """Teach the ITEM RECORD the pictures already living inside saved documents.

    `apply=False` (the default) reports what WOULD happen and changes nothing -
    it writes hundreds of records, so it is a preview-then-commit action, never
    a surprise.

    Reads the record (2026-08-19). It used to attach the picture to every
    catalog ROW of an item, one write per row per catalog; there is one row per
    item now, so it is one write.

    Three rules, unchanged, are what make it safe to run:
      * documents are never touched - this reads `rows_json` and writes only
        the record and the picture store;
      * EXACT names only. A backfill that guessed would attach the wrong photo
        to hundreds of items at once, silently;
      * an item that already has a picture keeps it.
    """
    pictures = _document_pictures()
    try:
        from . import master_items
    except Exception:
        return {"ok": False, "error": "The item record is unavailable."}

    conn = master_items._connect()
    try:
        master_items.init(conn)
        by_name: dict[str, list] = {}
        has_picture: set[str] = set()
        for r in conn.execute("SELECT item_id, name_en, name_ar, image_refs "
                              "FROM master_items"):
            for nm in (r["name_en"], r["name_ar"]):
                key = _norm(nm)
                if not key:
                    continue
                by_name.setdefault(key, []).append(r["item_id"])
                if str(r["image_refs"] or "").strip():
                    has_picture.add(key)

        plan: list[dict[str, Any]] = []
        unmatched: list[str] = []
        for key, pic in pictures.items():
            if key not in by_name:
                # The picture's item is not on file at all - nothing to attach
                # it to. Reported, never invented.
                unmatched.append(pic["name"])
                continue
            if key in has_picture:
                continue
            plan.append({"key": key, "name": pic["name"], "column": pic["column"],
                         "doc": pic["doc"], "rows": len(by_name[key])})

        result = {
            "ok": True,
            "found": len(pictures),
            "would_update": len(plan),
            "already_have": len([k for k in pictures if k in has_picture]),
            "unmatched": len(unmatched),
            "unmatched_names": sorted(unmatched)[:20],
            "sample": [p["name"] for p in plan[:12]],
            "applied": 0, "rows_written": 0, "stored_bytes": 0,
        }
        if not apply or not plan:
            return result

        written = 0
        stored = 0
        now = datetime.now().isoformat(timespec="seconds")
        for p in plan:
            pic = pictures[p["key"]]
            try:
                ref = item_images.store(pic["data_uri"])
            except Exception:
                ref = ""
            if not ref:
                continue
            path = item_images.path_for(ref)
            if path and path.exists():
                stored += path.stat().st_size
            blob = json.dumps({pic["column"] or "image": ref}, ensure_ascii=False)
            for item_id in by_name[p["key"]]:
                # An item that already has one keeps it - checked again here,
                # because two names can reach the same record.
                cur = conn.execute(
                    "UPDATE master_items SET image_refs = ?, updated_at = ? "
                    "WHERE item_id = ? AND COALESCE(image_refs,'') = ''",
                    (blob, now, item_id))
                written += cur.rowcount
        conn.commit()
        result["applied"] = len(plan)
        result["rows_written"] = written
        result["stored_bytes"] = stored
        return result
    finally:
        conn.close()


def image_report() -> dict[str, Any]:
    """Pictures on disk vs pictures anything still points at.

    The store is content-addressed and nothing ever deleted from it, so an item
    that loses its picture leaves the file behind. Reported, never purged
    automatically - see `purge_orphan_images`.
    """
    referenced: set[str] = set()
    conn = _connect()
    try:
        for v in _variants(conn):
            referenced.update(str(r) for r in (v["image_refs"] or {}).values() if r)
        # THE RECORD COUNTS TOO (2026-08-19). A picture can live only there -
        # on an item typed by hand, or carried across a rebuild after its
        # catalog row went. Counting the catalogs alone would report it as an
        # orphan, and `purge_orphan_images` DELETES orphans. The union is the
        # safe direction: at worst a genuinely dead file survives.
        try:
            for (blob,) in conn.execute(
                    "SELECT image_refs FROM master_items "
                    "WHERE image_refs IS NOT NULL AND image_refs <> ''"):
                try:
                    got = json.loads(blob)
                except (TypeError, ValueError):
                    continue
                if isinstance(got, dict):
                    referenced.update(str(r) for r in got.values() if r)
                elif isinstance(got, str) and got:
                    referenced.add(got)
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    on_disk: set[str] = set()
    total_bytes = 0
    try:
        store = item_images.store_dir()
        if store.exists():
            for p in store.iterdir():
                if p.is_file():
                    on_disk.add(p.name)
                    total_bytes += p.stat().st_size
    except Exception:
        pass
    orphans = sorted(on_disk - referenced)
    missing = sorted(referenced - on_disk)
    return {
        "files": len(on_disk), "bytes": total_bytes,
        "referenced": len(referenced & on_disk),
        "orphans": orphans, "orphan_count": len(orphans),
        "missing": missing, "missing_count": len(missing),
    }


def purge_orphan_images() -> dict[str, Any]:
    """Delete store files nothing references. Explicit, never automatic: the
    store is shared across profiles and a half-written catalog would otherwise
    look like permission to throw pictures away."""
    rep = image_report()
    freed = 0
    removed = 0
    for name in rep["orphans"]:
        p = item_images.path_for(name)
        if p and p.exists():
            try:
                freed += p.stat().st_size
                p.unlink()
                removed += 1
            except OSError:
                pass
    return {"ok": True, "deleted": removed, "freed_bytes": freed}


def clear_cell_memory(doc_type: str = "", sub_type: str = "") -> dict[str, Any]:
    """Wipe the per-cell autocomplete index (`column_memory`).

    This is an INDEX, not master data - every value ever typed into every
    column, ~1,800 rows on the live catalog, which is why it never belonged in
    a browsable list. It rebuilds itself as documents are saved, so clearing it
    is safe and is the honest answer to "my column suggestions are full of
    junk".
    """
    where, params = "", []
    if _s(doc_type):
        where, params = " WHERE doc_type = ?", [_s(doc_type)]
        if _s(sub_type):
            where += " AND sub_type = ?"
            params.append(_s(sub_type))
    conn = _connect()
    try:
        cur = conn.execute(f"DELETE FROM column_memory{where}", params)
        conn.commit()
        return {"ok": True, "deleted": cur.rowcount}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Push a master cost back into saved documents
# --------------------------------------------------------------------------- #
#
# Setting a cost here changes what gets AUTOFILLED next time; the documents you
# already saved keep the cost they were saved with. That is the right default -
# a document is a record, and silently restating last month's costs would move
# profit figures you have already read and reported.
#
# But when you finally learn what something really cost, you often DO want the
# old documents corrected. So this is offered as an explicit, previewed action:
# it lists exactly which documents would change and by how much, and writes
# only the ones handed back to it.
#
# Three rules keep it safe:
#
#   * **Preview first, always.** `apply=False` (the default) changes nothing.
#   * **Exact names only.** A bulk write must never guess which item it is
#     looking at; `contained`/`similar` are for a paste a human is watching.
#   * **Each document keeps its own currency.** The master figure is USD; every
#     row is written in the unit THAT row is typed in, converted at THAT
#     document's peg. Writing a dollar number into a dinar cell is the
#     84,000-where-60-belongs bug, at scale.
#
# Nothing here re-renders a PDF, because the cost is private and never printed
# (see docs/04) - so a corrected cost cannot change a document you have already
# sent to a client.

_DOC_KIND = {"quotes": "quote", "pos": "po", "dns": "dn", "invoices": "inv"}
_DOC_DATE = {"quotes": "quote_date", "pos": "po_date",
             "dns": "dn_date", "invoices": "inv_date"}


def _round_cost(value: float, ccy: str) -> Any:
    """Dinars are whole; dollars keep cents. Same rule as the editors."""
    return round(value) if _s(ccy).upper() == "IQD" else round(value, 2)


def _active_profile_db():
    from . import profile_manager
    return profile_manager.profile_db_path(profile_manager.active_profile_id())


def _submitted_set(conn) -> set:
    try:
        return {(r[0], r[1]) for r in
                conn.execute("SELECT kind, doc_no FROM submitted_docs")}
    except sqlite3.Error:
        return set()


_KIND_LABEL = {"quote": "Quotation", "po": "Purchase Order",
               "dn": "Delivery Note", "inv": "Invoice"}


def item_documents(name: str) -> dict[str, Any]:
    """Every saved document that contains this item, newest first.

    Read-only, and deliberately independent of `cost_targets`: that one exists
    to price a change and refuses outright when the item has no usable cost,
    which is most of the catalog. Answering "where have I used this?" must work
    for any item, costed or not.

    Scoped to the ACTIVE profile, for the same reason `cost_targets` is - the
    other company's documents are not this company's history.
    """
    # Resolve the item from the RECORD, not `item_detail` (which still folds the
    # retired catalogs and so cannot see anything saved after 2026-08-20). Match
    # on either language; keep the name the caller used for the document search.
    key = _norm(name)
    if not key:
        return {"ok": False, "error": "Item not found."}
    try:
        from . import master_items
        rc = master_items._connect()
        try:
            master_items.init(rc)
            found = any(
                _norm(r["name_en"]) == key or _norm(r["name_ar"]) == key
                for r in rc.execute("SELECT name_en, name_ar FROM master_items"))
        finally:
            rc.close()
    except Exception:
        found = False
    if not found:
        return {"ok": False, "error": "Item not found."}
    item = {"name": name}

    db = _active_profile_db()
    if not db.is_file():
        return {"ok": True, "name": item["name"], "documents": []}

    conn = sqlite_policy.open_db(db)
    try:
        submitted = _submitted_set(conn)
        out: list[dict[str, Any]] = []
        for table, pk in _DOC_TABLES:
            kind = _DOC_KIND[table]
            date_col = _DOC_DATE[table]
            client_col = "client_name" if table != "pos" else None
            cols = f'{pk} AS no, {date_col} AS d, year, month, rows_json'
            if client_col:
                cols += f', {client_col} AS client'
            try:
                rows = conn.execute(f'SELECT {cols} FROM "{table}"').fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                try:
                    items = json.loads(r["rows_json"] or "[]")
                except (TypeError, ValueError):
                    continue
                hits = [it for it in items
                        if isinstance(it, dict) and _norm(_row_name(it)) == key]
                if not hits:
                    continue
                lines = []
                for it in hits:
                    lines.append({
                        "description": _s(it.get("description")),
                        "unit": _s(it.get("unit")),
                        "qty": _s(it.get("qty")),
                        "rate": _s(it.get("rate")),
                        "cost": _s(it.get("cost")),
                    })
                keys = r.keys()
                out.append({
                    "kind": kind,
                    "kind_label": _KIND_LABEL.get(kind, kind),
                    "no": r["no"],
                    "date": _s(r["d"]),
                    "year": r["year"],
                    "month": r["month"],
                    "client": _s(r["client"]) if "client" in keys else "",
                    "submitted": (kind, r["no"]) in submitted,
                    "lines": lines,
                    "line_count": len(lines),
                })
        # Newest first - "where did I last use this?" is the usual question.
        out.sort(key=lambda d: (int(d["year"] or 0), int(d["month"] or 0),
                                str(d["no"])), reverse=True)
        return {"ok": True, "name": item["name"], "documents": out,
                "total": len(out)}
    finally:
        conn.close()


def cost_targets(name: str) -> dict[str, Any]:
    """Which saved documents hold this item, and what changing its cost would do.

    Scoped to the ACTIVE profile on purpose: the currency and peg lookups
    (`get_cost_currency`, `doc_iqd_per_usd`) read the active profile's database,
    so ranging across profiles would price other companies' documents at this
    one's exchange rate.
    """
    detail = item_detail(name)
    if not detail.get("ok"):
        return detail
    item = detail["item"]
    native, nccy = _item_native(item)
    if native is None:
        return {"ok": False,
                "error": "This item has no usable cost yet - set one first."}

    key = _norm(item["name"])
    db = _active_profile_db()
    if not db.is_file():
        return {"ok": True, "cost": native, "cost_ccy": nccy, "targets": []}

    conn = sqlite_policy.open_db(db)
    try:
        submitted = _submitted_set(conn)
        targets: list[dict[str, Any]] = []
        for table, pk in _DOC_TABLES:
            kind = _DOC_KIND[table]
            date_col = _DOC_DATE[table]
            try:
                rows = conn.execute(
                    f'SELECT {pk} AS no, {date_col} AS d, year, month, rows_json '
                    f'FROM "{table}"'
                ).fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                try:
                    items = json.loads(r["rows_json"] or "[]")
                except (TypeError, ValueError):
                    continue
                hits = [it for it in items
                        if isinstance(it, dict) and _norm(_row_name(it)) == key]
                if not hits:
                    continue
                doc_ccy = database.get_cost_currency(kind, r["no"])
                peg = database.doc_iqd_per_usd(kind, r["no"]) or config.IQD_PER_USD
                empty = same = differs = 0
                current: list[str] = []
                proposed: list[Any] = []
                for it in hits:
                    row_ccy = database.row_cost_currency(it, doc_ccy)
                    want = _cost_in(native, nccy, row_ccy, peg)
                    have = _s(it.get("cost"))
                    proposed.append(f"{want} {row_ccy}")
                    if not have:
                        empty += 1
                        current.append(f"- {row_ccy}")
                    else:
                        current.append(f"{have} {row_ccy}")
                        if _num(have) is not None and abs(_num(have) - want) < 1e-9:
                            same += 1
                        else:
                            differs += 1
                if empty == 0 and differs == 0:
                    continue            # nothing this document would gain
                targets.append({
                    "key": f"{kind}:{r['no']}",
                    "kind": kind, "doc_no": r["no"], "date": _s(r["d"]),
                    # Sort on the numeric year/month, never the display date:
                    # "9/6/2026" sorts above "2/7/2026" as a string.
                    "_ym": (int(r["year"] or 0), int(r["month"] or 0)),
                    "lines": len(hits), "empty": empty, "same": same,
                    "differs": differs,
                    "submitted": (kind, r["no"]) in submitted,
                    "current": ", ".join(current[:3]),
                    "proposed": ", ".join(sorted(set(proposed))[:3]),
                })
    finally:
        conn.close()

    targets.sort(key=lambda t: (t["_ym"], t["doc_no"]), reverse=True)
    for t in targets:
        t.pop("_ym", None)
    return {"ok": True, "name": item["name"],
            "cost": native, "cost_ccy": nccy,
            "targets": targets,
            "submitted_count": sum(1 for t in targets if t["submitted"])}


def apply_cost_to_documents(name: str, keys: Any = None,
                            apply: bool = False) -> dict[str, Any]:
    """Write the master cost into the chosen documents' rows.

    `keys` are the `kind:doc_no` strings from `cost_targets`. Nothing happens
    without both an explicit list AND `apply=True` - this rewrites records, so
    it is never a side effect of looking.
    """
    found = cost_targets(name)
    if not found.get("ok"):
        return found
    wanted = {_s(k) for k in (keys or []) if _s(k)}
    chosen = [t for t in found["targets"] if t["key"] in wanted]
    result = {"ok": True, "name": found["name"],
              "cost": found["cost"], "cost_ccy": found["cost_ccy"],
              "selected": len(chosen), "documents": 0, "rows": 0}
    if not apply or not chosen:
        result["targets"] = found["targets"]
        return result

    native, nccy = found["cost"], found["cost_ccy"]
    key = _norm(found["name"])
    by_table = {v: k for k, v in _DOC_KIND.items()}
    conn = sqlite_policy.open_db(_active_profile_db())
    try:
        for t in chosen:
            table = by_table[t["kind"]]
            pk = dict(_DOC_TABLES)[table]
            row = conn.execute(
                f'SELECT rows_json FROM "{table}" WHERE {pk} = ?', (t["doc_no"],)
            ).fetchone()
            if not row:
                continue
            try:
                items = json.loads(row["rows_json"] or "[]")
            except (TypeError, ValueError):
                continue
            doc_ccy = database.get_cost_currency(t["kind"], t["doc_no"])
            peg = database.doc_iqd_per_usd(t["kind"], t["doc_no"]) or config.IQD_PER_USD
            touched = 0
            for it in items:
                if not isinstance(it, dict) or _norm(_row_name(it)) != key:
                    continue
                row_ccy = database.row_cost_currency(it, doc_ccy)
                it["cost"] = str(_cost_in(native, nccy, row_ccy, peg))
                # Stamped explicitly so the number we just wrote can never be
                # re-read in a different unit later.
                it["cost_ccy"] = row_ccy
                touched += 1
            if not touched:
                continue
            # `updated_at` is deliberately NOT bumped: the document's content as
            # issued has not changed (the cost is private and unprinted), and
            # moving it would reshuffle every "recently edited" list.
            conn.execute(f'UPDATE "{table}" SET rows_json = ? WHERE {pk} = ?',
                         (json.dumps(items, ensure_ascii=False), t["doc_no"]))
            result["documents"] += 1
            result["rows"] += touched
        conn.commit()
    finally:
        conn.close()
    return result
