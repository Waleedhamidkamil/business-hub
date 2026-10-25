# -*- coding: utf-8 -*-
"""ONE record per item - the item master, and the only place an item lives.

    master_items (the record)  ->  documents take what they need
                               <-  a document save adds to it, or teaches it
                                   something it did not know

It was not always this. Until 2026-08-16 `master_data.py` had no storage at
all: it read four document catalogs (`items` for POs, `quote_items`,
`dn_items`, `inv_items`) and folded them into a display, so the flow ran
BACKWARDS from what the name promises - documents saved whatever they saved and
Master Data read it back. That is why one product could be four rows, why a
cost had to be written onto each of them separately, and why بصل and `Onion`
counted as two items.

Those four tables were retired on 2026-08-20, with the record and the catalogs
in exact agreement (1,106 rows -> 765 records, and not one catalog name
unreachable). Nothing folds and nothing rebuilds any more: there is one row per
item and it IS the item. What it holds is Waleed's list and nothing else - "one
place that holds every item that gets into the app and stores its details (name
in English, name in Arabic, description, unit, price, cost and image), nothing
more, all under one item number".

Two languages, all the way down
-------------------------------
An item does not have "a description" and "a unit" - it has one of each PER
LANGUAGE, exactly as it has two names. The PO is written in Arabic (كيلو) and
the quotation, delivery note and invoice in English (KG), and they are the same
kilogram of the same product.

So: `description_en` / `description_ar` and `unit_en` / `unit_ar`, filled by the
script of the TEXT ITSELF - an Arabic unit word goes in the Arabic slot no
matter which document carried it. Reads that want one language ask for it and
fall back to the other, because one known unit beats none.

Units are compared by MEANING, not spelling: كيلو is KG. Without that, every
Arabic item and its English twin look like different units and never join.
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from . import database, master_data, shared_library

SCHEMA = """
CREATE TABLE IF NOT EXISTS master_items (
    item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_no          TEXT,
    name_en          TEXT,
    name_ar          TEXT,
    description_en   TEXT NOT NULL DEFAULT '',
    description_ar   TEXT NOT NULL DEFAULT '',
    unit_en          TEXT NOT NULL DEFAULT '',
    unit_ar          TEXT NOT NULL DEFAULT '',
    cost_native      REAL,
    cost_native_ccy  TEXT,
    price_native     REAL,
    price_native_ccy TEXT,
    image_refs       TEXT,
    img_query        TEXT,
    -- The company this item was last bought FOR. Not one of the fields the
    -- screen shows - internal memory, exactly like `img_query` - but the PO
    -- editor fills its company column from it, and it is the one thing a PO
    -- suggestion needs that is a fact about the last purchase order rather
    -- than about the product.
    last_company     TEXT,
    data_json        TEXT,
    use_count        INTEGER NOT NULL DEFAULT 0,
    last_used_at     TEXT,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_master_items_en ON master_items(name_en);
CREATE INDEX IF NOT EXISTS idx_master_items_ar ON master_items(name_ar);
CREATE INDEX IF NOT EXISTS idx_master_items_no ON master_items(item_no);

-- Other spellings that FIND a record (2026-09-08).
--
-- A document stores an item's WORDS, not its id, and the only way to find a
-- record was its own `name_en` / `name_ar`. So `merge_records`, which deletes
-- the losing rows, also deleted the only route to them: merging "Wall Split
-- Unit" into "Wall-Mounted Split AC" made those words stop matching anything,
-- and the next document that used them made a fresh duplicate. Cleaning the
-- catalogue was feeding it.
--
-- An alias never fills anything. It is a way IN, exactly like the item's
-- other-language name, and when one collides with a real name the resolver's
-- standing rule applies: a tie is never a match.
CREATE TABLE IF NOT EXISTS master_aliases (
    alias_norm  TEXT PRIMARY KEY,
    alias       TEXT NOT NULL,
    item_id     INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_master_aliases_item ON master_aliases(item_id);

-- What the catalogue review has already been told (2026-09-08).
--
-- Keyed on the finding's WORDS, never on `item_id`: a merge deletes ids, and a
-- ruling keyed on one would be resurrected by the next scan.
CREATE TABLE IF NOT EXISTS master_review (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    decision    TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

"""

AR = re.compile(r"[؀-ۿ]")

# Unit words that mean the same thing in the two languages the catalogs use.
# Without this every Arabic item and its English twin look like different units
# and refuse to join - كيلو is KG.
_UNIT_CLASS: dict[str, str] = {}
for _cls, _words in {
    "kg": ("كيلو", "كليو", "كغم", "كجم", "kg", "kgs", "kilogram", "千克"),
    "each": ("عدد", "قطعة", "ea", "pcs", "pc", "piece", "unit", "no", "handle"),
    "box": ("كارتون", "كرتون", "بوكس", "box", "carton", "ctn", "箱"),
    "pack": ("باكيت", "بكيت", "pack", "pkt", "packet"),
    "set": ("سيت", "set"),
    "bag": ("كيس", "كونية", "bag", "sack"),
    "tray": ("طبقة", "tray"),
    "roll": ("رولة", "لفة", "roll"),
    "metre": ("متر", "m", "mtr", "meter", "metre"),
    "ton": ("طن", "ton", "tonne", "mt"),
    "litre": ("لتر", "l", "ltr", "liter", "litre"),
    # "متر مكعب" must be listed WHOLE: `unit_class` scans tokens in order and
    # would stop at متر, filing a cubic metre as a metre.
    "m3": ("m3", "cbm", "متر مكعب", "م3"),
    "tin": ("كوتية", "علبة", "tin", "can"),
    "bale": ("بالة", "bale"),
    "dozen": ("درزن", "dozen", "dzn"),
    "pail": ("سطل", "pail", "bucket"),
    "joint": ("joint", "وصلة"),
    "pair": ("زوج", "pair"),
    "cylinder": ("اسطوانة", "cylinder"),
    "bottle": ("بطل", "قنينة", "bottle"),
    # 2026-08-17 - the meanings the catalogue was already using and this table
    # did not know. Every one of them is a unit some real line is billed by:
    # water and waste are sold BY THE TRIP, plant is hired by the month, day or
    # hour, and bolts arrive on a strip.
    "trip": ("رحلة", "trip", "trips"),
    "month": ("شهر", "شهري", "month"),
    "day": ("يوم", "يومي", "day"),
    "hour": ("ساعة", "hour", "hr"),
    "drum": ("برميل", "drum"),
    "strip": ("شريط", "strip"),
    "sheet": ("لوح", "sheet"),
    "tube": ("انبوب", "أنبوب", "tube"),
    "jerrycan": ("جلكان", "jerrycan"),
    "block": ("قالب", "block"),
    "length": ("طول", "length"),
}.items():
    for _w in _words:
        _UNIT_CLASS[_w] = _cls

# The word each MEANING is written with, per language - what to put in a slot
# the catalogue never filled. Chosen from what this catalogue already uses most
# (`EA` 249 / عدد 145, `KG` 67 / كيلو 79), so a filled unit reads like one
# somebody here typed rather than a dictionary entry.
#
# This is the ONLY place a unit word is invented, and it is invented only into
# an EMPTY slot - a unit somebody typed is never rewritten by it.
UNIT_WORDS: dict[str, tuple[str, str]] = {
    "kg": ("KG", "كيلو"),
    "each": ("EA", "عدد"),
    "box": ("BOX", "كارتون"),
    "pack": ("PACK", "باكيت"),
    "set": ("SET", "سيت"),
    "bag": ("BAG", "كيس"),
    "tray": ("TRAY", "طبقة"),
    "roll": ("ROLL", "رولة"),
    "metre": ("M", "متر"),
    "ton": ("TON", "طن"),
    "litre": ("L", "لتر"),
    "m3": ("M3", "متر مكعب"),
    "tin": ("TIN", "علبة"),
    "bale": ("BALE", "بالة"),
    "dozen": ("DOZEN", "درزن"),
    "pail": ("PAIL", "سطل"),
    "joint": ("JOINT", "وصلة"),
    "pair": ("PAIR", "زوج"),
    "cylinder": ("CYLINDER", "اسطوانة"),
    "bottle": ("BOTTLE", "بطل"),
    "trip": ("TRIP", "رحلة"),
    "month": ("MONTH", "شهر"),
    "day": ("DAY", "يوم"),
    "hour": ("HOUR", "ساعة"),
    "drum": ("DRUM", "برميل"),
    "strip": ("STRIP", "شريط"),
    "sheet": ("SHEET", "لوح"),
    "tube": ("TUBE", "انبوب"),
    "jerrycan": ("JERRYCAN", "جلكان"),
    "block": ("BLOCK", "قالب"),
    "length": ("LENGTH", "طول"),
}

# A few units are a PHRASE, not a word - "Per Truck Load 20 m3" is billed by the
# truck load, and answering it with the class word (متر مكعب) would change what
# the quantity beside it counts. Matched on the whole value, before the class.
# A few items state their unit in their own NAME. "Monthly vehicle rental
# service Pickup truck (4WD)" is hired by the month - that is READ off the item,
# not guessed at, and being a rule rather than a one-off edit it survives the
# next rebuild.
_UNIT_FROM_NAME: tuple[tuple[Any, str], ...] = (
    (re.compile(r"\bmonthly\b.*\brental\b", re.I), "month"),
)

_UNIT_PHRASE: dict[str, tuple[str, str]] = {
    # WESTERN digits on purpose: `unit_class` reads "م3" and would not read
    # "م٣", so the Arabic side would stop meaning cubic metres and the two
    # halves of the same item would no longer agree.
    "per truck load 20 m3": ("Per Truck Load 20 m3", "حمولة شاحنة 20 م3"),
}


def unit_class(unit: Any) -> str:
    """The MEANING of a unit word, or '' when we do not recognise it.

    An unrecognised unit is never treated as equal to another unrecognised one
    - guessing wrong here silently merges two different products.
    """
    u = re.sub(r"[^\w؀-ۿ]+", " ", str(unit or "")).strip().lower()
    if not u:
        return ""
    if u in _UNIT_CLASS:
        return _UNIT_CLASS[u]
    for tok in u.split():
        if tok in _UNIT_CLASS:
            return _UNIT_CLASS[tok]
    return ""


def units_agree(a: Any, b: Any) -> bool:
    """True when two unit words mean the same thing, or one side is blank."""
    ca, cb = unit_class(a), unit_class(b)
    sa, sb = str(a or "").strip(), str(b or "").strip()
    if not sa or not sb:
        return True                      # a missing unit contradicts nothing
    if ca and cb:
        return ca == cb
    return sa.lower() == sb.lower()      # unknown words must match literally


# --------------------------------------------------------------------------- #
# Which language a piece of text is in
# --------------------------------------------------------------------------- #
#
# Decided by the SCRIPT OF THE TEXT, never by the document that carried it. An
# Arabic PO line whose unit cell says "KG" really does hold an English unit, and
# filing it under Arabic because the PO is Arabic would put كيلو and KG in the
# same slot and lose one of them.

TEXT_FIELDS = ("description", "unit")


def is_arabic(text: Any) -> bool:
    return bool(AR.search(str(text or "")))


def _slot(field: str, text: Any) -> str:
    """`unit_ar` for كيلو, `unit_en` for KG. Text with no letters at all - "12mm",
    "220V" - counts as English and is served to both sides by `prefer`."""
    return f"{field}_ar" if is_arabic(text) else f"{field}_en"


def prefer(en: Any, ar: Any, lang: str = "") -> str:
    """The wanted language's text, or the other one when it is all there is.

    One known unit beats none: a document that asks for Arabic and finds only
    `KG` is better off printing `KG` than printing nothing.
    """
    e, a = str(en or "").strip(), str(ar or "").strip()
    return (a or e) if str(lang or "").lower().startswith("ar") else (e or a)


def _connect() -> sqlite3.Connection:
    conn = shared_library.connect()
    conn.row_factory = sqlite3.Row
    return conn


def unit_english_map() -> dict[str, str]:
    """Arabic unit -> the English unit this catalogue uses. See `unit_map`."""
    return unit_map("en")


def unit_map(want: str = "en") -> dict[str, str]:
    """One language's unit -> the OTHER language's, as THIS catalogue writes it.

    `want="en"` answers Arabic->English (a PO becoming a delivery note);
    `want="ar"` answers English->Arabic (2026-09-10, a quotation teaching the
    catalogue the Arabic half it will need when the same goods are ordered).

    Units do not need translating: the catalogue has already answered the
    question 58 different ways and the only real problem is that it answered
    some of them more than once. `عدد` is recorded as EA 410 times, `piece` 24,
    `PCS` 8, `pcs` 3 (and `handle` 11, which is somebody's slip). So the useful
    answer is not a dictionary lookup but a VOTE: the spelling this business
    uses most for that unit.

    Local, exact and offline on purpose - an item's unit is not a sentence, and
    a machine translator asked for `عدد` returns "number", which is right in
    Arabic and wrong on an invoice. Compared case-insensitively so `BOX` and
    `box` are one candidate, and the most-used original spelling is returned.
    """
    out: dict[str, str] = {}
    conn = None
    try:
        conn = _connect()
        init(conn)
        rows = conn.execute(
            "SELECT unit_ar AS ar, unit_en AS en, COUNT(*) AS n FROM master_items "
            "WHERE TRIM(COALESCE(unit_ar,'')) <> '' AND TRIM(COALESCE(unit_en,'')) <> '' "
            "GROUP BY TRIM(unit_ar), TRIM(unit_en)").fetchall()
    except sqlite3.Error:
        return out
    finally:
        if conn is not None:
            conn.close()
    to_arabic = str(want or "en").lower().startswith("ar")
    tally: dict[str, dict[str, int]] = {}
    for r in rows:
        ar, en, n = str(r["ar"]).strip(), str(r["en"]).strip(), int(r["n"] or 0)
        if not ar or not en:
            continue
        # The vote runs the same way in both directions: group by the unit we
        # are given, count the spellings of the one we want.
        asked, answer = (en, ar) if to_arabic else (ar, en)
        by_spelling = tally.setdefault(asked, {})
        by_spelling[answer] = by_spelling.get(answer, 0) + n
    for ar, spellings in tally.items():
        folded: dict[str, tuple[int, str]] = {}
        for en, n in spellings.items():
            key = en.casefold()
            count, best = folded.get(key, (0, en))
            # Most-used spelling wins its own fold; ties keep the first seen.
            folded[key] = (count + n, best if spellings.get(best, 0) >= n else en)
        winner = max(folded.values(), key=lambda v: v[0])
        out[ar] = winner[1]
    return out


def init(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or _connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        if own:
            conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older shared library up to the current shape. Idempotent, and
    guarded by PRAGMA rather than a ledger, because this database has none.

    1. `description` / `unit` split per language (2026-08-17). The old single
       columns held whichever language the record happened to meet first.
       Nothing is thrown away: each value moves into the slot matching its own
       script, then the old columns go so there is exactly ONE place each field
       lives. (A second copy always drifts - `_SIG_IGNORE` proved that.)
    2. The per-line side tables go (2026-08-19). An item's details belong to
       the ITEM: the equipment quotation's "Specifications" column is its
       description and "Monthly Rental Rate" is its price, so both were folded
       into the record and the tables that held them separately were dropped.
       Waleed's model, and the right one: one place, one set of fields.
    """
    for dead in ("master_item_lines", "master_item_sources"):
        conn.execute(f"DROP TABLE IF EXISTS {dead}")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(master_items)")}
    if not cols:
        return
    for c in ("description_en", "description_ar", "unit_en", "unit_ar"):
        if c not in cols:
            conn.execute(f"ALTER TABLE master_items ADD COLUMN {c} "
                         f"TEXT NOT NULL DEFAULT ''")
    if "category" not in cols:
        # 2026-09-08. Nothing in this app had a category, so there was no way to
        # ask what a quarter's safety gear or catering actually cost.
        conn.execute("ALTER TABLE master_items ADD COLUMN category "
                     "TEXT NOT NULL DEFAULT ''")
    if "last_company" not in cols:
        conn.execute("ALTER TABLE master_items ADD COLUMN last_company TEXT")
        # Carried over from the PO catalog, which is where it lived until the
        # record became the only thing a dropdown reads.
        if "items" in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}:
            conn.execute(
                "UPDATE master_items SET last_company = ("
                "  SELECT i.last_company FROM items i"
                "   WHERE (i.name = master_items.name_en"
                "       OR i.name = master_items.name_ar)"
                "     AND COALESCE(i.last_company,'') <> ''"
                "   ORDER BY COALESCE(i.last_used_at,'') DESC LIMIT 1)")
    for old, field in (("description", "description"), ("unit", "unit")):
        if old not in cols:
            continue
        for iid, val in conn.execute(
                f"SELECT item_id, {old} FROM master_items "
                f"WHERE {old} IS NOT NULL AND {old} <> ''").fetchall():
            conn.execute(
                f"UPDATE master_items SET {_slot(field, val)} = ? "
                f"WHERE item_id = ? AND {_slot(field, val)} = ''", (val, iid))
        conn.execute(f"ALTER TABLE master_items DROP COLUMN {old}")


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def _load(blob) -> dict[str, Any]:
    try:
        d = json.loads(blob or "{}")
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _merge_image_refs(*blobs) -> dict[str, str] | None:
    """One image map containing every distinct reference, in input order."""
    refs: list[str] = []
    for blob in blobs:
        if isinstance(blob, str):
            try:
                blob = json.loads(blob)
            except (TypeError, ValueError):
                blob = {"image": blob} if blob.strip() else None
        values = blob.values() if isinstance(blob, dict) else ()
        for value in values:
            ref = str(value or "").strip()
            if ref and ref not in refs:
                refs.append(ref)
    if not refs:
        return None
    return {"image" if i == 0 else f"image_{i + 1}": ref
            for i, ref in enumerate(refs)}


# What a rebuild must CARRY rather than re-derive: everything a record can
# know that the four catalogs cannot say.
_KEEPABLE = ("name_en", "name_ar", "description_en", "description_ar",
             "unit_en", "unit_ar", "price_native", "price_native_ccy",
             "cost_native", "cost_native_ccy", "image_refs", "img_query",
             "last_company")


def _num(v):
    try:
        f = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


# --------------------------------------------------------------------------- #
# Keep it current
# --------------------------------------------------------------------------- #
#
# `build()` is a full rebuild - too slow to run on every document save, and it
# would fight anything the record has learned that the catalogs never held.
# `remember()` is the incremental half: one item, upserted as a document saves.
#
# WRITES COME BEFORE READS, deliberately. Switching the app to read from
# `master_items` first would mean an item saved on a fresh document did not
# appear in the next document's suggestions until someone rebuilt - a
# regression on exactly the workflow this is meant to fix. So the record is
# kept current FIRST, while the four catalogs still answer every question, and
# reads move over only once it is provably in step.

_CARRY = ("cost_native", "cost_native_ccy", "price_native", "price_native_ccy",
          "img_query")


# --------------------------------------------------------------------------- #
# The Master Data screen reads and writes THIS, and only this
# --------------------------------------------------------------------------- #
#
# Waleed's spec, in his words: "one place that holds every item that gets into
# the app and stores its details (name in English, name in Arabic, description,
# unit, price, cost and image) - nothing more, all under one item number".
#
# So the screen shows eight fields and no others. There are deliberately no
# "source" pills: an item belonged to four catalogs only because it was stored
# four times, and it is not any more.

_TEXT_FIELDS = ("name_en", "name_ar", "description_en", "description_ar",
                "unit_en", "unit_ar")

_EDITABLE_FIELDS = _TEXT_FIELDS + ("price_native", "price_native_ccy",
                                   "cost_native", "cost_native_ccy", "category")

# What an item IS, for spend reporting (2026-09-08). Derived from this
# catalogue's own vocabulary, not invented: water/frozen/fresh/oil name food,
# `common laborer` and `operator` name manpower, `land cruiser`/`hardtop`/`trip`
# name transport, and so on. A closed list on purpose - free text would give 400
# categories and answer nothing.
CATEGORIES = (
    "Food & Catering",
    "Manpower",
    "Vehicles & Transport",
    "Fuel & Lubricants",
    "Tools & Hardware",
    "Electrical & AC",
    "Plumbing",
    "Safety & PPE",
    "Office & Stationery",
    "Furniture",
    "Cleaning & Consumables",
    "Building Materials",
    "Other",
)

# A unit that measures TIME is not describing a product. 87 records are priced
# per month, per day or per hour, and every one of them is manpower or hire -
# so they get a category without spending an API call on them.
_TIME_UNITS = ("month", "day", "hour", "week", "year", "شهر", "يوم", "ساعة")

# `description` and `unit` with no language on them are still accepted from
# callers that have only one box to type in. They are FILED BY SCRIPT, not
# stored as a third copy.
_LANGUAGE_FREE = ("description", "unit")

# Every column the screen can sort by (2026-08-26 \u2014 was six keys with a fixed
# direction each). One entry per HEADER, so a column that shows a value the user
# can read is a column they can order by.
#
#   expression      \u2014 what to order on, ASCENDING
#   default         \u2014 the direction a first click uses. Text reads A\u2192Z; a
#                     number, a date or a count is asked about biggest-first.
#   empty           \u2014 SQL that is true when the row has NOTHING in this column.
#                     It is ordered FIRST and always ascending, which pins blank
#                     rows to the bottom in BOTH directions: flipping the arrow
#                     must reorder the records that have a value, not parade the
#                     ones that do not past the reader twice.
_SORT_SPEC = {
    "used":    ("use_count", "DESC", None),
    "recent":  ("COALESCE(last_used_at,'')", "DESC", "COALESCE(last_used_at,'') = ''"),
    "name":    ("COALESCE(NULLIF(name_en,''), name_ar) COLLATE NOCASE", "ASC", None),
    "name_ar": ("name_ar COLLATE NOCASE", "ASC", "COALESCE(name_ar,'') = ''"),
    "number":  ("item_no COLLATE NOCASE", "ASC", "COALESCE(item_no,'') = ''"),
    "unit":    ("COALESCE(NULLIF(unit_en,''), unit_ar) COLLATE NOCASE", "ASC",
                "COALESCE(unit_en,'') = '' AND COALESCE(unit_ar,'') = ''"),
    "desc":    ("COALESCE(NULLIF(description_en,''), description_ar) COLLATE NOCASE", "ASC",
                "COALESCE(description_en,'') = '' AND COALESCE(description_ar,'') = ''"),
    "price":   ("price_native", "DESC", "price_native IS NULL"),
    "cost":    ("cost_native", "DESC", "cost_native IS NULL"),
    "picture": ("(COALESCE(image_refs,'') <> '')", "DESC", None),
}
# The tie-break is always the name, so two records that match on the sorted
# column keep a stable order instead of shuffling between loads.
_SORT_TIE = "COALESCE(NULLIF(name_en,''), name_ar) COLLATE NOCASE"


def sort_default_desc(sort: str) -> bool:
    """Which way a FIRST click on this column points."""
    spec = _SORT_SPEC.get(str(sort or ""), _SORT_SPEC["used"])
    return spec[1] == "DESC"


def _order_by(sort: str, desc: Any = None) -> str:
    """ORDER BY for one column and direction. `desc=None` uses the column's
    own default, which is what a first click gets."""
    key = str(sort or "used")
    expr, default, empty = _SORT_SPEC.get(key, _SORT_SPEC["used"])
    down = (default == "DESC") if desc is None else bool(desc)
    parts = []
    if empty:
        parts.append(f"({empty})")          # blanks last, both directions
    parts.append(f"{expr} {'DESC' if down else 'ASC'}")
    if expr != _SORT_TIE:
        parts.append(_SORT_TIE)
    return ", ".join(parts)


# Kept as a name-only view of the same table for anything that still asks.
_SORTS = {k: _order_by(k) for k in _SORT_SPEC}

# What a record is still missing - the list of gaps the screen offers as chips,
# and the same SQL both counts and filters by, so a chip can never say 12 and
# then show 9.
#
# "no Arabic unit" is asked only of items that HAVE an Arabic name: an item that
# has never appeared on a PO does not need an Arabic unit yet, and counting all
# 500 of them would bury the 70-odd that genuinely want one.
_MISSING = {
    "name_en": "(name_en IS NULL OR name_en = '')",
    "name_ar": "(name_ar IS NULL OR name_ar = '')",
    "unit": "(COALESCE(unit_en,'') = '' AND COALESCE(unit_ar,'') = '')",
    "unit_ar": "(COALESCE(unit_ar,'') = '' AND COALESCE(name_ar,'') <> '')",
    "unit_en": "(COALESCE(unit_en,'') = '' AND COALESCE(name_en,'') <> '')",
    "price": "price_native IS NULL",
    "cost": "cost_native IS NULL",
    "image": "(image_refs IS NULL OR image_refs = '')",
    "category": "(COALESCE(category,'') = '')",
}


def _key(row, name, default=None):
    """One column from a row that may predate it. A shared library upgraded
    mid-session can be read by code newer than its schema."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def _row_out(r: sqlite3.Row) -> dict[str, Any]:
    """One record, shaped for the screen. Money stays AS TYPED with its own
    currency - the app never converts silently."""
    try:
        img = json.loads(r["image_refs"] or "null")
    except (TypeError, ValueError):
        img = None
    ref = ""
    if isinstance(img, dict):
        ref = next((v for v in img.values() if v), "")
    elif isinstance(img, str):
        ref = img
    return {
        "item_id": r["item_id"],
        "item_no": r["item_no"] or "",
        "name_en": r["name_en"] or "",
        "name_ar": r["name_ar"] or "",
        "description_en": r["description_en"] or "",
        "description_ar": r["description_ar"] or "",
        "unit_en": r["unit_en"] or "",
        "unit_ar": r["unit_ar"] or "",
        "category": (_key(r, "category") or ""),
        # DERIVED, never stored - the English side when there is one, else the
        # Arabic, so a caller that wants one string still gets the best one.
        "description": prefer(r["description_en"], r["description_ar"]),
        "unit": prefer(r["unit_en"], r["unit_ar"]),
        "price": r["price_native"],
        "price_ccy": (r["price_native_ccy"] or "USD") if r["price_native"] is not None else "",
        "cost": r["cost_native"],
        "cost_ccy": (r["cost_native_ccy"] or "USD") if r["cost_native"] is not None else "",
        "image_ref": ref,
        "uses": r["use_count"] or 0,
        "last_used": r["last_used_at"] or "",
    }


# A BAND THE ALLOCATOR NEVER ISSUES AND NEVER COUNTS.
#
# The sequence runs "one past the highest", so a single stray high number drags
# every later item up with it. Test sentinels are numbered M-99001, M-99777,
# M-99999 - and on the live catalogue that pushed 49 REAL items into the
# M-99xxx range, which reads as junk beside 807 items numbered in the hundreds.
#
# Anything at or above this is somebody else's: a test, or a number typed by
# hand. It stays exactly where it is and is still refused as a duplicate; it
# simply does not move the sequence.
_RESERVED_FROM = 90000


def _number_state(conn) -> tuple[set[str], int]:
    """Every number in use, and the highest M-xxxxx counter the app issued."""
    used, top = set(), 0
    for (no,) in conn.execute(
            "SELECT item_no FROM master_items WHERE item_no <> '' AND item_no IS NOT NULL"):
        used.add(str(no))
        m = re.match(r"^M-0*(\d+)$", str(no))
        if m and int(m.group(1)) < _RESERVED_FROM:
            top = max(top, int(m.group(1)))
    return used, top


def _plan_numbers(conn) -> list[tuple[int, str]]:
    """`[(item_id, number)]` for every record that has none."""
    used, top = _number_state(conn)
    plan = []
    for (iid,) in conn.execute(
            "SELECT item_id FROM master_items "
            "WHERE item_no IS NULL OR item_no = '' ORDER BY use_count DESC, item_id"):
        top += 1
        no = f"M-{top:05d}"
        while no in used:
            top += 1
            no = f"M-{top:05d}"
        used.add(no)
        plan.append((iid, no))
    return plan


def _number_the_rest(conn) -> int:
    """Number anything unnumbered, on a connection the caller already has."""
    plan = _plan_numbers(conn)
    if plan:
        conn.executemany("UPDATE master_items SET item_no = ? WHERE item_id = ?",
                         [(no, iid) for iid, no in plan])
        conn.commit()
    return len(plan)


def assign_numbers(apply: bool = False) -> dict[str, Any]:
    """Give every record a material number. The number is the ONE thing about
    an item that never changes - rename it, re-unit it, and it still points at
    the same thing."""
    conn = _connect()
    try:
        init(conn)
        plan = _plan_numbers(conn)
        if apply and plan:
            conn.executemany("UPDATE master_items SET item_no = ? WHERE item_id = ?",
                             [(no, iid) for iid, no in plan])
            conn.commit()
        return {"ok": True, "assigned": len(plan), "applied": len(plan) if apply else 0}
    finally:
        conn.close()


def compact_name(name: Any) -> str:
    """Presentation-insensitive name, with no fuzzy guessing.

    Catches `BOX ,
CAMERA` against `BOX , CAMERA` - the same item saved twice
    because a line break was typed into the cell - but never claims `Pipe` and
    `Pipes` are the same product. That bounded rule is what makes it safe to
    preselect a merge.
    """
    return master_data._norm(str(name or "")).replace(" ", "")


def duplicate_ids(conn=None) -> set[int]:
    """Records whose name matches another's once spacing is ignored.

    The Master Data screen's own duplicate finder (2026-08-19). It replaced a
    separate review panel built around CATALOG ROWS - "source-local material
    duplicates" - which could only look inside one document type at a time and
    described a storage shape that no longer exists. Merging already lives on
    this screen; this is the half that finds them for you.
    """
    own = conn is None
    conn = conn or _connect()
    try:
        init(conn)
        # Keyed on the name AND the rest of the identity. Name alone flagged
        # 254 records, because `Hemp rope` φ12 / φ15 / φ20 legitimately share
        # one - three products, not a duplicate. The description and the unit's
        # MEANING complete the key, exactly as they do everywhere else here.
        seen: dict[tuple, list[int]] = {}
        for r in conn.execute("SELECT * FROM master_items"):
            detail = (compact_name(prefer(r["description_en"], r["description_ar"])),
                      unit_class(prefer(r["unit_en"], r["unit_ar"]))
                      or compact_name(prefer(r["unit_en"], r["unit_ar"])))
            for nm in (r["name_en"], r["name_ar"]):
                k = compact_name(nm)
                if k:
                    seen.setdefault((k, *detail), []).append(r["item_id"])
        out: set[int] = set()
        for ids in seen.values():
            uniq = set(ids)
            if len(uniq) > 1:
                out |= uniq
        return out
    except sqlite3.Error:
        return set()
    finally:
        if own:
            conn.close()


def duplicate_groups(conn=None) -> list[dict[str, Any]]:
    """The duplicate sets, not just their ids (2026-08-26).

    `duplicate_ids` flattens every group into one bag, which is what the
    "Possible duplicates" chip used to show: 66 records ordered by use count,
    so the two `Chest Freezer` rows sat five apart and the reader had to hunt
    for the pair. The groups are the answer to "duplicate of WHAT".

    Each group is `{key, name, ids, primary}`. The PRIMARY is the record the
    others are duplicates OF: most used, then oldest, because that is the one
    documents already point at and the one a merge should keep. A record that
    matches under two names (English and Arabic) is placed in the FIRST group
    only \u2014 showing it twice would make the screen lie about how many records
    there are.
    """
    own = conn is None
    conn = conn or _connect()
    try:
        init(conn)
        seen: dict[tuple, list[int]] = {}
        names: dict[tuple, str] = {}
        meta: dict[int, tuple] = {}
        for r in conn.execute("SELECT * FROM master_items"):
            detail = (compact_name(prefer(r["description_en"], r["description_ar"])),
                      unit_class(prefer(r["unit_en"], r["unit_ar"]))
                      or compact_name(prefer(r["unit_en"], r["unit_ar"])))
            meta[r["item_id"]] = (-(r["use_count"] or 0), r["item_id"])
            for nm in (r["name_en"], r["name_ar"]):
                k = compact_name(nm)
                if not k:
                    continue
                key = (k, *detail)
                seen.setdefault(key, []).append(r["item_id"])
                names.setdefault(key, str(nm or "").strip())
        placed: set[int] = set()
        groups: list[dict[str, Any]] = []
        for key in sorted(seen, key=lambda k: (names.get(k, "").lower(), k)):
            ids = [i for i in dict.fromkeys(seen[key]) if i not in placed]
            if len(ids) < 2:
                continue                      # not a duplicate on its own
            ids.sort(key=lambda i: meta[i])   # most used first, then oldest
            placed.update(ids)
            groups.append({"key": "|".join(str(p) for p in key),
                           "name": names.get(key, ""), "ids": ids,
                           "primary": ids[0]})
        return groups
    except sqlite3.Error:
        return []
    finally:
        if own:
            conn.close()


def list_records(search: str = "", sort: str = "used", limit: int = 200,
                 offset: int = 0, missing: str = "",
                 desc: Any = None) -> dict[str, Any]:
    """The Master Data list. ONE row per item - no folding, no variants, no
    per-document duplicates, because there is only one row to begin with.

    `search` matches the number, EITHER name, EITHER description and EITHER
    unit: one box, because asking which field to search in is the friction this
    screen exists to remove. `missing` filters to records lacking a field, which
    is how the catalog gets finished off.
    """
    q = str(search or "").strip().lower()
    order = _order_by(sort, desc)
    where, params = [], []
    _SEARCH_COLS = ("item_no", "name_en", "name_ar", "description_en",
                    "description_ar", "unit_en", "unit_ar")
    if q:
        where.append("(" + " OR ".join(
            f"LOWER(COALESCE({c},'')) LIKE ?" for c in _SEARCH_COLS) + ")")
        params += [f"%{q}%"] * len(_SEARCH_COLS)
    miss = _MISSING.get(str(missing or ""))
    if miss:
        where.append(miss)
    dupe_ids: set[int] | None = None
    groups: list[dict[str, Any]] = []
    if str(missing or "") == "duplicate":
        groups = duplicate_groups()
        dupe_ids = {i for g in groups for i in g["ids"]}
        if not dupe_ids:
            return {"ok": True, "rows": [], "total": 0, "all_total": 0,
                    "missing_counts": {}, "limit": limit, "offset": offset,
                    "groups": 0}
        where.append("item_id IN (" + ",".join("?" * len(dupe_ids)) + ")")
        params += sorted(dupe_ids)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _connect()
    try:
        init(conn)
        # Anything saved since the last rebuild has no number yet, and the
        # number is the first column on this screen - "-" there reads as a
        # broken record. `remember()` deliberately does NOT number: a document
        # that carries its own item number must still be able to teach it, and a
        # placeholder assigned on insert would block that. By the time this
        # screen is open the saves are done, so this is where the gap closes.
        _number_the_rest(conn)
        # Same reasoning for the second half of every unit. A document save
        # writes the one language it was typed in, so an item saved this
        # morning would show `KG` and a blank beside it until the next rebuild.
        _fill_units(conn)
        total = conn.execute(
            f"SELECT COUNT(*) FROM master_items{sql_where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM master_items{sql_where} ORDER BY {order} "
            f"LIMIT ? OFFSET ?", (*params, max(1, int(limit)), max(0, int(offset)))
        ).fetchall()
        counts = {k: conn.execute(
            f"SELECT COUNT(*) FROM master_items WHERE {v}").fetchone()[0]
            for k, v in _MISSING.items()}
        counts["duplicate"] = len(duplicate_ids(conn))
        all_total = conn.execute("SELECT COUNT(*) FROM master_items").fetchone()[0]
    finally:
        conn.close()
    out = [_row_out(r) for r in rows]
    if groups:
        out = _group_duplicates(out, groups)
    return {"ok": True, "rows": out, "total": total,
            "all_total": all_total, "missing_counts": counts,
            "limit": limit, "offset": offset,
            "sort": str(sort or "used"),
            "desc": (sort_default_desc(sort) if desc is None else bool(desc)),
            "groups": len(groups)}


def _group_duplicates(rows: list[dict[str, Any]],
                      groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-order the duplicate view so each copy sits directly under the record
    it duplicates, and stamp every row with which group it is in.

    The sort column is deliberately IGNORED here: any ordering that is not
    "group by group" separates the pair, which is the one thing this view
    exists to show. The screen says so rather than leaving the header arrows
    looking broken.
    """
    by_id = {r["item_id"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for n, g in enumerate(groups, 1):
        present = [by_id[i] for i in g["ids"] if i in by_id]
        if len(present) < 2:
            continue                          # a page boundary split the group
        primary = present[0]
        for pos, r in enumerate(present):
            r["dupe_group"] = n
            r["dupe_name"] = g["name"]
            r["dupe_size"] = len(present)
            r["dupe_role"] = "primary" if pos == 0 else "copy"
            r["dupe_of"] = "" if pos == 0 else (primary.get("item_no")
                                                or primary.get("name_en")
                                                or primary.get("name_ar") or "")
            out.append(r)
    # Anything the grouping could not place (a page boundary) still has to
    # appear - dropping a record from a list that says "66 items" is worse
    # than showing it ungrouped.
    seen = {id(r) for r in out}
    out.extend(r for r in rows if id(r) not in seen)
    return out


def get_record(item_id: Any) -> dict[str, Any]:
    """One record, plus where it has been used."""
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No item."}
    conn = _connect()
    try:
        init(conn)
        r = conn.execute("SELECT * FROM master_items WHERE item_id = ?", (iid,)).fetchone()
        if not r:
            return {"ok": False, "error": "No item."}
        out = _row_out(r)

    finally:
        conn.close()
    return {"ok": True, "item": out}


def create_record(patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add an item BY HAND, before any document has ever used it.

    Waleed: "we should also be able to fill it by hand by the way, its a master
    data at the end." Until now a record could only appear as a side effect of
    saving a document, which made the screen a report rather than a master.

    Numbered immediately - an item with no number is not "all under one item
    number" - and refused when it would duplicate an item already on file,
    because two records for one product is the whole problem this is fixing.
    """
    patch = dict(patch or {})
    fields = {k: re.sub(r"\s+", " ", str(patch.get(k) or "")).strip()
              for k in _TEXT_FIELDS}
    for k in _LANGUAGE_FREE:
        v = str(patch.get(k) or "").strip()
        if v and not fields[_slot(k, v)]:
            fields[_slot(k, v)] = v
    if not fields["name_en"] and not fields["name_ar"]:
        return {"ok": False, "error": "An item needs a name in at least one language."}

    money: dict[str, Any] = {}
    for amount, ccy in (("price_native", "price_native_ccy"),
                        ("cost_native", "cost_native_ccy")):
        v = _num(patch.get(amount))
        money[amount] = v
        money[ccy] = (_ccy(patch.get(ccy)) or "USD") if v is not None else None

    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        init(conn)
        # An item already on file must not be entered a second time. Matched
        # the way a document save matches: either name, then description and
        # unit, with units compared by MEANING.
        names = [n for n in (fields["name_en"], fields["name_ar"]) if n]
        for nm in names:
            hit = _find(conn, nm, prefer(fields["description_en"],
                                         fields["description_ar"]),
                        prefer(fields["unit_en"], fields["unit_ar"]))
            if hit is not None:
                return {"ok": False, "already": _row_out(hit),
                        "error": f"“{hit['name_en'] or hit['name_ar']}” is already "
                                 f"on file as {hit['item_no'] or 'an item'}."}
        cols = [*_TEXT_FIELDS, *money, "item_no", "use_count", "created_at",
                "updated_at"]
        # Inserted unnumbered and numbered a line below, so the new row is
        # counted by the same allocator every other record goes through.
        vals = [*(fields[k] for k in _TEXT_FIELDS), *money.values(),
                "", 0, now, now]
        cur = conn.execute(
            f"INSERT INTO master_items({', '.join(cols)}) "
            f"VALUES({', '.join('?' * len(cols))})", vals)
        item_id = cur.lastrowid
        _number_the_rest(conn)
        conn.commit()
        row = conn.execute("SELECT * FROM master_items WHERE item_id = ?",
                           (item_id,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "item": _row_out(row)}


def save_record(item_id: Any, patch: dict[str, Any] | None) -> dict[str, Any]:
    """Edit one record. A patch, not a replace: a field the caller omits is
    left alone, and an explicit "" clears it."""
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No item."}
    raw = patch or {}
    patch = {k: v for k, v in raw.items() if k in _EDITABLE_FIELDS}
    # A caller with one box per field, not two, still lands in the right slot.
    # An explicit `unit_ar` always wins over a language-free `unit`. A BLANK
    # language-free value means "this item has no unit", so it clears both -
    # anything else leaves a value the caller believes it just removed.
    for k in _LANGUAGE_FREE:
        if k not in raw:
            continue
        if str(raw[k] or "").strip():
            patch.setdefault(_slot(k, raw[k]), raw[k])
        else:
            patch.setdefault(f"{k}_en", "")
            patch.setdefault(f"{k}_ar", "")
    if not patch:
        return {"ok": False, "error": "Nothing to change."}

    sets: dict[str, Any] = {}
    for k in _TEXT_FIELDS:
        if k in patch:
            sets[k] = re.sub(r"\s+", " ", str(patch[k] or "")).strip()
    if "category" in patch:
        # A closed list, matched case-insensitively. An unknown value is
        # refused rather than stored: a category nobody can filter by is worse
        # than none, and free text is how you end up with 400 of them. An
        # explicit blank still clears it.
        wanted = re.sub(r"\s+", " ", str(patch["category"] or "")).strip()
        if not wanted:
            sets["category"] = ""
        else:
            hit = next((c for c in CATEGORIES if c.lower() == wanted.lower()), "")
            if not hit:
                return {"ok": False, "error": f"No such category: {wanted}"}
            sets["category"] = hit
    for money, ccy in (("price_native", "price_native_ccy"),
                       ("cost_native", "cost_native_ccy")):
        if money in patch:
            v = _num(patch[money])
            sets[money] = v
            # The currency is meaningless without the amount, and an amount
            # without its currency is the 1500x mistake this app keeps having
            # to avoid - so they are always written together.
            sets[ccy] = (_ccy(patch.get(ccy)) or "USD") if v is not None else None
    if not sets:
        return {"ok": False, "error": "Nothing to change."}

    conn = _connect()
    try:
        init(conn)
        row = conn.execute("SELECT * FROM master_items WHERE item_id = ?", (iid,)).fetchone()
        if not row:
            return {"ok": False, "error": "No item."}
        if not str(sets.get("name_en", row["name_en"]) or "").strip() and \
                not str(sets.get("name_ar", row["name_ar"]) or "").strip():
            return {"ok": False, "error": "An item needs a name in at least one language."}
        sets["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE master_items SET {cols} WHERE item_id = ?",
                     (*sets.values(), iid))
        conn.commit()
        r = conn.execute("SELECT * FROM master_items WHERE item_id = ?", (iid,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "item": _row_out(r)}


def record_image(item_id: Any) -> str:
    """The item's picture as a data URI, or ''."""
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return ""
    conn = _connect()
    try:
        r = conn.execute("SELECT image_refs FROM master_items WHERE item_id = ?",
                         (iid,)).fetchone()
    finally:
        conn.close()
    if not r or not r["image_refs"]:
        return ""
    try:
        blob = json.loads(r["image_refs"])
    except (TypeError, ValueError):
        return ""
    ref = ""
    if isinstance(blob, dict):
        ref = next((v for v in blob.values() if v), "")
    elif isinstance(blob, str):
        ref = blob
    if not ref:
        return ""
    try:
        from . import item_images
        return item_images.load(ref) or ""
    except Exception:
        return ""


def delete_records(item_ids: Any) -> dict[str, Any]:
    """Remove records. The DOCUMENTS that used them are never touched - this
    is the catalog forgetting an item, not a document losing a line."""
    ids = []
    for v in (item_ids or []):
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {"ok": False, "error": "Nothing selected."}
    conn = _connect()
    try:
        marks = ",".join("?" * len(ids))
        n = conn.execute(f"DELETE FROM master_items WHERE item_id IN ({marks})", ids).rowcount
        # An alias pointing at a record that no longer exists would make the
        # resolver find a hole. Deleting is forgetting the item; forget its
        # other spellings with it.
        conn.execute(f"DELETE FROM master_aliases WHERE item_id IN ({marks})", ids)
        conn.commit()
    finally:
        conn.close()
    return {"ok": n > 0, "deleted": n}


def _alias_norm(text: Any) -> str:
    """The SAME normalisation the resolver indexes names under.

    An alias filed under a different rule is an alias that never matches, which
    is worse than none because it looks like the problem is solved.
    """
    return master_data._norm(text)


def remember_aliases(item_id: Any, spellings: Any, source: str = "",
                     conn: sqlite3.Connection | None = None) -> int:
    """Record other spellings that should FIND this item.

    Never overwrites an alias already pointing somewhere: the first claim wins,
    and a word that means two things is left to the resolver's tie rule rather
    than silently reassigned here.
    """
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return 0
    own = conn is None
    conn = conn or _connect()
    try:
        init(conn)
        row = conn.execute("SELECT name_en, name_ar FROM master_items "
                           "WHERE item_id = ?", (iid,)).fetchone()
        if not row:
            return 0
        # Its own names are already a way in; storing them twice would make the
        # record look ambiguous against itself.
        mine = {_alias_norm(row["name_en"]), _alias_norm(row["name_ar"])}
        now = datetime.now().isoformat(timespec="seconds")
        added = 0
        for spelling in (spellings if isinstance(spellings, (list, tuple, set)) else [spellings]):
            text = re.sub(r"\s+", " ", str(spelling or "")).strip()
            key = _alias_norm(text)
            if not key or key in mine:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO master_aliases"
                "(alias_norm, alias, item_id, source, created_at) VALUES(?,?,?,?,?)",
                (key, text, iid, str(source or ""), now))
            added += cur.rowcount or 0
        if own:
            conn.commit()
        return added
    except sqlite3.Error:
        return 0
    finally:
        if own:
            conn.close()


def aliases_by_item(conn: sqlite3.Connection | None = None) -> dict[int, list[str]]:
    """Every stored spelling, grouped by the record it finds. Read once for a
    whole resolve rather than per row."""
    own = conn is None
    conn = conn or _connect()
    out: dict[int, list[str]] = {}
    try:
        init(conn)
        for r in conn.execute("SELECT item_id, alias FROM master_aliases"):
            out.setdefault(int(r["item_id"]), []).append(str(r["alias"] or ""))
    except sqlite3.Error:
        return {}
    finally:
        if own:
            conn.close()
    return out


def drop_aliases(item_ids: Any, conn: sqlite3.Connection | None = None) -> int:
    """Forget a deleted record's spellings, so they cannot point at nothing."""
    ids = []
    for v in (item_ids if isinstance(item_ids, (list, tuple, set)) else [item_ids]):
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0
    own = conn is None
    conn = conn or _connect()
    try:
        init(conn)
        marks = ",".join("?" * len(ids))
        cur = conn.execute(f"DELETE FROM master_aliases WHERE item_id IN ({marks})", ids)
        if own:
            conn.commit()
        return cur.rowcount or 0
    except sqlite3.Error:
        return 0
    finally:
        if own:
            conn.close()


def merge_records(keep_id: Any, drop_ids: Any) -> dict[str, Any]:
    """Fold several records into one.

    The survivor LEARNS from the others - it takes any field it is missing -
    and never loses one it already has. Use counts add up, so merging never
    makes an item look less used than it was.
    """
    try:
        keep = int(keep_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "No item to merge into."}
    drops = []
    for v in (drop_ids or []):
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv != keep:
            drops.append(iv)
    if not drops:
        return {"ok": False, "error": "Nothing to merge."}
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM master_items WHERE item_id = ?", (keep,)).fetchone()
        if not row:
            return {"ok": False, "error": "No item to merge into."}
        marks = ",".join("?" * len(drops))
        others = conn.execute(
            f"SELECT * FROM master_items WHERE item_id IN ({marks})", drops).fetchall()
        if not others:
            return {"ok": False, "error": "Nothing to merge."}
        alias_map = aliases_by_item(conn)
        sets: dict[str, Any] = {}
        uses = row["use_count"] or 0
        last = row["last_used_at"] or ""
        for o in others:
            uses += o["use_count"] or 0
            if (o["last_used_at"] or "") > last:
                last = o["last_used_at"] or ""
            for f in ("item_no", *_TEXT_FIELDS, "image_refs", "img_query"):
                if not str(row[f] or "").strip() and not str(sets.get(f) or "").strip() \
                        and str(o[f] or "").strip():
                    sets[f] = o[f]
            for amount, ccy in (("price_native", "price_native_ccy"),
                                ("cost_native", "cost_native_ccy")):
                if row[amount] is None and sets.get(amount) is None and o[amount] is not None:
                    sets[amount] = o[amount]
                    sets[ccy] = o[ccy]
        sets["use_count"] = uses
        sets["last_used_at"] = last
        sets["updated_at"] = datetime.now().isoformat(timespec="seconds")
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE master_items SET {cols} WHERE item_id = ?",
                     (*sets.values(), keep))
        # The losing names must survive as a way IN (2026-09-08).
        #
        # A document stores an item's WORDS. Deleting the row deleted the only
        # route to those words, so the next document that used them made a new
        # record - cleaning the catalogue was feeding it. Recorded in the SAME
        # transaction as the merge, and their own aliases come along, or the
        # second merge of a chain loses what the first one saved.
        spellings = []
        for o in others:
            spellings += [o["name_en"], o["name_ar"]]
        spellings += [a for iid in drops for a in alias_map.get(iid, [])]
        remember_aliases(keep, spellings, "merge", conn)
        conn.execute(f"DELETE FROM master_aliases WHERE item_id IN ({marks})", drops)
        conn.execute(f"DELETE FROM master_items WHERE item_id IN ({marks})", drops)
        conn.commit()
        out = conn.execute("SELECT * FROM master_items WHERE item_id = ?", (keep,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "merged": len(others), "item": _row_out(out)}


def _ccy(v: Any) -> str:
    s = str(v or "").strip().upper()
    return s if s in ("USD", "IQD") else ""


def name_pairs() -> dict[str, str]:
    """`{normalised name: the other language's normalised name}`, both ways.

    The one thing that says بصل and `Onion` are one product. Read by the
    Master Data list so it can show them as a single row - the first place the
    record actually answers a question the four catalogs could not.
    """
    out: dict[str, str] = {}
    conn = None
    try:
        conn = _connect()
        init(conn)
        for en, ar in conn.execute(
                "SELECT name_en, name_ar FROM master_items "
                "WHERE name_en <> '' AND name_ar <> '' "
                "  AND name_en IS NOT NULL AND name_ar IS NOT NULL"):
            a, b = master_data._norm(en), master_data._norm(ar)
            if a and b:
                out[a], out[b] = b, a
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def record_ids_for(names: Any) -> dict[str, int]:
    """`{name as given: item_id}` for the names that belong to a record.

    Lets the autocomplete collapse an item's two languages into ONE suggestion
    without teaching it anything about pairing: two results carrying the same
    item_id are the same product.
    """
    wanted = [str(n or "").strip() for n in (names or []) if str(n or "").strip()]
    if not wanted:
        return {}
    out: dict[str, int] = {}
    conn = None
    try:
        conn = _connect()
        init(conn)
        by_norm: dict[str, int] = {}
        marks = ",".join("?" * len(wanted))
        for r in conn.execute(
                f"SELECT item_id, name_en, name_ar FROM master_items "
                f"WHERE name_en COLLATE NOCASE IN ({marks})"
                f"   OR name_ar COLLATE NOCASE IN ({marks})",
                (*wanted, *wanted)):
            for nm in (r["name_en"], r["name_ar"]):
                k = master_data._norm(nm)
                if k:
                    by_norm.setdefault(k, r["item_id"])
        for n in wanted:
            rid = by_norm.get(master_data._norm(n))
            if rid is not None:
                out[n] = rid
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _describes(row, description: Any) -> int:
    """How well a catalog row's description fits this record: 2 exact, 1 does
    not contradict, 0 no.

    A description is compared ONLY against its own language's slot, because
    `Big size` and `حجم كبير` are the same fact written twice and no string
    comparison can see that. So:

      * the slot is filled  -> it must match, and that is what keeps
        `Rope 12mm` and `Rope 20mm` two records;
      * the record has no description at all -> only an undescribed row fits,
        so plain `Rope` never absorbs `Rope 12mm`;
      * the record is described in the OTHER language only -> a described row
        fits (it is teaching this side), an UNdescribed one does not.
    """
    want = master_data._norm(description)
    mine = master_data._norm(row[_slot("description", description)])
    if mine:
        return 2 if mine == want else 0
    other_slot = ("description_en" if _slot("description", description)
                  == "description_ar" else "description_ar")
    if not master_data._norm(row[other_slot]):
        return 2 if want == "" else 0
    return 1 if want else 0


def _unit_fits(row, unit: Any) -> bool:
    """True when a unit does not contradict what the record already stores.
    Compared by MEANING against either slot; a record with no unit at all
    contradicts nothing."""
    stored = [u for u in (row["unit_en"], row["unit_ar"]) if str(u or "").strip()]
    return any(units_agree(u, unit) for u in stored) if stored else True


def _matches(conn, name: str, description: Any, unit: Any,
             other_name: str = "") -> tuple[sqlite3.Row | None, bool]:
    """`(best record, tied)` for one catalog row - see `_find`.

    `tied` is True when no record fits EXACTLY and two or more merely do not
    contradict the row. `best` is then the first of them, which is good enough
    to say "this item is already on file" and NOT good enough to say which
    variant the row is (2026-10-03, A-117).
    """
    names = [n for n in (str(name or "").strip(), str(other_name or "").strip()) if n]
    if not names:
        return None, False
    marks = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT * FROM master_items WHERE name_en COLLATE NOCASE IN ({marks})"
        f"   OR name_ar COLLATE NOCASE IN ({marks})",
        (*names, *names)).fetchall()
    # An EXACT description always beats one that merely does not contradict, so
    # a row saying "20mm" lands on the 20mm record even when a 12mm record was
    # read first and had nothing in this row's language to disagree with.
    best, best_fit, loose = None, 0, 0
    for r in rows:
        if not _unit_fits(r, unit):
            continue
        fit = _describes(r, description)
        if fit == 1:
            loose += 1
        if fit > best_fit:
            best, best_fit = r, fit
            if fit == 2:
                break
    return best, best_fit == 1 and loose > 1


def _find(conn, name: str, description: Any, unit: Any,
          other_name: str = "") -> sqlite3.Row | None:
    """The record this catalog row belongs to, or None.

    Matched on the app's own item identity (name + description + unit) with
    units compared by MEANING, then - failing that - on the other-language
    name the row carries, which is how an Arabic PO line finds the record its
    English delivery note created.
    """
    return _matches(conn, name, description, unit, other_name)[0]


def remember(name: str, description: Any, unit: Any, data: dict[str, Any] | None,
             source: str, sub_type: str = "") -> int | None:
    """Add or update ONE item's record as a document saves it.

    Never raises: a document save must not fail because the record could not be
    updated. Returns the item_id, or None when nothing was done.
    """
    name = str(name or "").strip()
    if not name:
        return None
    data = data if isinstance(data, dict) else {}
    other = str(data.get("name_ar") or data.get("name_en") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")
    conn = None
    try:
        conn = _connect()
        init(conn)
        row, tied = _matches(conn, name, description, unit, other)
        if tied:
            # Two variants both "do not contradict" this line - "حبل / 12 ملم"
            # against Rope 12mm and Rope 20mm, each described in English only.
            # Learning the line onto the first one read wrote its description
            # onto a variant chosen at random, and from then on the resolver
            # picked that variant automatically, price and all. Learn nothing:
            # the user names the variant once when this line becomes a delivery
            # note or invoice, and that review records the description on the
            # variant they chose (`buildMasterPatch` in po_handoff.js).
            return None
        slot = "name_ar" if AR.search(name) else "name_en"
        d_slot, u_slot = _slot("description", description), _slot("unit", unit)
        if row is None:
            # BOTH names, when the row carries one. An Arabic PO line that
            # knows its English name used to create a record with the Arabic
            # side only - the pairing the document just told us was thrown
            # away, and the item could not be found in English until some
            # later save happened to teach it.
            other_slot = "name_en" if slot == "name_ar" else "name_ar"
            cur = conn.execute(
                f"INSERT INTO master_items({slot}, {other_slot}, {d_slot},"
                f" {u_slot}, last_company, item_no,"
                " cost_native, cost_native_ccy, price_native, price_native_ccy,"
                " image_refs, img_query, use_count, last_used_at, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (name, other, str(description or ""), str(unit or ""),
                 str(data.get("last_company") or "").strip() or None,
                 str(data.get("item_no") or ""),
                 _num(data.get("cost_native")),
                 str(data.get("cost_native_ccy") or "").upper() or None,
                 _num(data.get("price_native")),
                 str(data.get("price_native_ccy") or "").upper() or None,
                 json.dumps(data["image_refs"], ensure_ascii=False)
                 if data.get("image_refs") else None,
                 str(data.get("img_query") or "") or None,
                 now, now, now))
            item_id = cur.lastrowid
        else:
            item_id = row["item_id"]
            sets: dict[str, Any] = {"use_count": (row["use_count"] or 0) + 1,
                                    "last_used_at": now, "updated_at": now}
            # The record only LEARNS here; a document that says nothing about a
            # field must not blank it. Same rule as `_STICKY_MEMORY` in
            # database.py, and for the same reason: re-saving a delivery note
            # with the cost cell empty used to erase a cost the catalog knew.
            if not row[slot]:
                sets[slot] = name
            if other and not row["name_ar" if slot == "name_en" else "name_en"]:
                sets["name_ar" if slot == "name_en" else "name_en"] = other
            # THE OTHER LANGUAGE'S description and unit. An English delivery
            # note saying "KG" teaches the record its English unit even though
            # it already knows كيلو from the PO - that second slot is exactly
            # what folding both into one column used to throw away.
            for f, v in (("description", description), ("unit", unit)):
                s = _slot(f, v)
                if str(v or "").strip() and not str(row[s] or "").strip():
                    sets[s] = str(v).strip()
            if not row["item_no"] and data.get("item_no"):
                sets["item_no"] = str(data["item_no"])
            for k in _CARRY:
                v = data.get(k)
                if k.endswith("_ccy"):
                    v = str(v or "").upper() or None
                elif k != "img_query":
                    v = _num(v)
                else:
                    v = str(v or "") or None
                if v not in (None, "") and row[k] in (None, ""):
                    sets[k] = v
            # LAST write wins, unlike everything above. The field means "the
            # company this was last bought FOR", so the newest purchase order
            # is the answer - learning it once and keeping it for ever would
            # make it a lie the first time the item is bought for someone else.
            co = str(data.get("last_company") or "").strip()
            if co:
                sets["last_company"] = co
            if data.get("image_refs") and not row["image_refs"]:
                sets["image_refs"] = json.dumps(data["image_refs"], ensure_ascii=False)
            cols = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE master_items SET {cols} WHERE item_id = ?",
                         (*sets.values(), item_id))
        conn.commit()
        return item_id
    except Exception:
        # Best effort, always. The catalogs remain the system of record until
        # reads move over, so a failure here loses nothing a rebuild cannot
        # recover.
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def unit_counterpart(unit: Any, want: str) -> str:
    """The same unit written in the other language, or '' when we cannot say.

    `unit_counterpart("KG", "ar")` is كيلو. An unrecognised word returns '' -
    a unit is printed on documents, so a guess here is worse than a blank.
    """
    u = str(unit or "").strip()
    if not u:
        return ""
    pair = _UNIT_PHRASE.get(u.lower()) or UNIT_WORDS.get(unit_class(u))
    if not pair:
        return ""
    return pair[1] if str(want).lower().startswith("ar") else pair[0]


def fill_missing_units(apply: bool = False) -> dict[str, Any]:
    """Give every item its unit in BOTH languages.

    Waleed: "fill every unit in both lang with whats right". An item that has a
    unit in one language always has the same unit in the other - the PO says
    كيلو where the invoice says KG - so a blank slot beside a filled one is a
    gap, not a fact.

    Fills a BLANK slot only, and only from a unit whose meaning is known. Two
    things it deliberately will not do: rewrite a unit somebody typed, and
    invent one for an item that has no unit in either language (50 of those are
    plant hire - a crane is not billed per piece, per day and per month
    interchangeably, and the documents never said which).
    """
    conn = _connect()
    try:
        init(conn)
        plan, unknown, rescripted, blank = _plan_units(conn)
        if apply:
            _write_units(conn, plan)
    finally:
        conn.close()
    return {"ok": True,
            "filled": len(plan),
            "filled_en": sum(1 for s, _, _ in plan if s == "unit_en"),
            "filled_ar": sum(1 for s, _, _ in plan if s == "unit_ar"),
            "unknown_unit": unknown,
            "wrong_script": rescripted,
            "no_unit_at_all": blank,
            "applied": len(plan) if apply else 0}


def _write_units(conn, plan) -> int:
    for slot, word, iid in plan:
        conn.execute(f"UPDATE master_items SET {slot} = ?, updated_at = ? "
                     f"WHERE item_id = ?",
                     (word, datetime.now().isoformat(timespec="seconds"), iid))
    if plan:
        conn.commit()
    return len(plan)


def _fill_units(conn) -> int:
    """The fill, on a connection the caller already has."""
    return _write_units(conn, _plan_units(conn)[0])


def _plan_units(conn):
    """`([(slot, word, item_id)], unknown, rescripted, blank)` - what the fill
    WOULD write. Separated so a dry run and the screen's top-up share it."""
    rows = conn.execute(
        "SELECT item_id, name_en, name_ar, unit_en, unit_ar FROM master_items"
    ).fetchall()
    plan: list[tuple[str, str, int]] = []
    unknown: dict[str, int] = {}
    rescripted: list[tuple[str, str]] = []
    blank = 0
    for r in rows:
        en, ar = str(r["unit_en"] or "").strip(), str(r["unit_ar"] or "").strip()

        # A unit in the WRONG SCRIPT for its slot was never typed here - the
        # four '箱' / '千克' came off a Chinese supplier sheet and are not
        # English units by any reading. Rewriting those is not the same as
        # rewriting somebody's `PCS`, so it is the one overwrite allowed.
        for slot, val in (("unit_en", en), ("unit_ar", ar)):
            if not val:
                continue
            right = (is_arabic(val) if slot == "unit_ar"
                     else not is_arabic(val) and val.isascii())
            if right:
                continue
            word = unit_counterpart(val, "ar" if slot == "unit_ar" else "en")
            if word and word != val:
                plan.append((slot, word, r["item_id"]))
                rescripted.append((val, word))
                if slot == "unit_en":
                    en = word
                else:
                    ar = word

        if en and ar:
            continue
        if not en and not ar:
            cls = next((c for rx, c in _UNIT_FROM_NAME
                        if rx.search(f"{r['name_en'] or ''} {r['name_ar'] or ''}")), "")
            if not cls:
                blank += 1
                continue
            plan.append(("unit_en", UNIT_WORDS[cls][0], r["item_id"]))
            plan.append(("unit_ar", UNIT_WORDS[cls][1], r["item_id"]))
            continue
        have, slot, want = (en, "unit_ar", "ar") if en else (ar, "unit_en", "en")
        word = unit_counterpart(have, want)
        if word:
            plan.append((slot, word, r["item_id"]))
        else:
            unknown[have] = unknown.get(have, 0) + 1
    return plan, unknown, rescripted, blank


