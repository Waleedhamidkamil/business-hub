"""What is WRONG with the catalogue, as a worklist (2026-09-08).

The Master Data screen already reports what is MISSING - its chips carry live
counts for no Arabic name, no unit, no price, no picture, possible duplicates.
Three things it cannot do, and this module is those three:

  1. **Fix in bulk.** Filtering to 621 records leaves the user typing each one.
  2. **See a problem that is not a blank field.** Measured on the live catalogue
     the day this was written: 21 groups / 43 records share a name AND a
     description, and 24 descriptions have a date typed into them. None of
     those is a blank field, so no chip can show them.
  3. **Remember a ruling.** "These two are not duplicates" had nowhere to live,
     so the chip reported them for ever.

Nothing here writes on its own and nothing here touches the network. `scan` is
pure reads; `apply` performs ONE finding's fix and records that it happened;
`dismiss` records a ruling and changes no data.

**A ruling is keyed on WORDS, never on `item_id`.** A merge deletes ids, so a
dismissal keyed on one would be resurrected by the next scan - which is exactly
the nagging this replaces.

**Waleed's ruling, 2026-09-08: a unit disagreement is a VARIANT, not a
duplicate.** FLOUR at BAG / BOX / KG is three real products he sells three ways.
So that finding asks for the descriptions that tell them apart; it never
proposes a merge. Only a group that agrees on name, description AND unit is
offered as one product stored twice.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import master_data, master_items, profile_manager, sqlite_policy

# A date belongs to a purchase order, not to a product. 24 descriptions carry
# one, mostly on the `Common Laborer` rows.
_DATE = re.compile(r"(?:\bdate\s*[:：]\s*)?\b\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*20\d\d\b"
                   r"|\bdate\s*[:：]\s*", re.I)

# How far apart two prices have to be before they are telling us something.
# Used for the evidence on screen and - per Waleed's ruling, 2026-09-08 - as the
# ONLY thing about money that may ever be sent to a language model.
_CLOSE = 0.05          # within 5% is the same price typed twice


def _norm(text: Any) -> str:
    return master_data._norm(text)


def _words(row: sqlite3.Row | dict, field: str) -> str:
    try:
        return str(row[field] or "")
    except (IndexError, KeyError):
        return ""


def _name(row) -> str:
    return _words(row, "name_en") or _words(row, "name_ar")


def _description(row) -> str:
    return _words(row, "description_en") or _words(row, "description_ar")


def _unit(row) -> str:
    return _words(row, "unit_en") or _words(row, "unit_ar")


def price_relation(values: Any) -> str:
    """`same`, `close`, `far` or `unknown` - never an amount.

    This is the whole of what a language model may be told about money. A model
    judging "is this one product or two" is helped enormously by knowing the
    prices disagree and not at all by knowing they are $300 and $400, and the
    second costs Waleed his price list.
    """
    seen = []
    for v in (values or []):
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            seen.append(f)
    if len(seen) < 2:
        return "unknown"
    low, high = min(seen), max(seen)
    if low == high:
        return "same"
    return "close" if (high - low) / high <= _CLOSE else "far"


def _identity(row) -> str:
    """A record's words, as the rest of the app understands identity:
    name + description + unit, with the unit's MEANING rather than its
    spelling, so كيلو and KG are one unit."""
    return "\x1f".join((
        _norm(_name(row)),
        _norm(_description(row)),
        master_items.unit_class(_unit(row)) or _norm(_unit(row)),
    ))


def _group_key(kind: str, rows) -> str:
    """A finding's stable name. Sorted, so the same group keys the same way
    however the rows come back, and made of words, so it survives a merge."""
    return kind + "|" + "|".join(sorted(_identity(r) for r in rows))


def _row(r) -> dict[str, Any]:
    return {
        "item_id": r["item_id"],
        "item_no": _words(r, "item_no"),
        "name": _name(r),
        "name_en": _words(r, "name_en"),
        "name_ar": _words(r, "name_ar"),
        "description": _description(r),
        "unit": _unit(r),
        "category": _words(r, "category"),
        "price": r["price_native"],
        "price_ccy": _words(r, "price_native_ccy"),
        "uses": r["use_count"] or 0,
    }


# --------------------------------------------------------------------------- #
# Rulings
# --------------------------------------------------------------------------- #

def decisions(conn: sqlite3.Connection | None = None) -> dict[str, str]:
    own = conn is None
    conn = conn or master_items._connect()
    out: dict[str, str] = {}
    try:
        master_items.init(conn)
        for r in conn.execute("SELECT kind, key, decision FROM master_review"):
            out[f"{r['kind']}|{r['key']}"] = str(r["decision"] or "")
    except sqlite3.Error:
        return {}
    finally:
        if own:
            conn.close()
    return out


def _record(kind: str, key: str, decision: str,
            conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    conn = conn or master_items._connect()
    try:
        master_items.init(conn)
        conn.execute(
            "INSERT INTO master_review(kind, key, decision, decided_at) "
            "VALUES(?,?,?,?) ON CONFLICT(kind, key) DO UPDATE SET "
            "decision = excluded.decision, decided_at = excluded.decided_at",
            (kind, key, decision, datetime.now().isoformat(timespec="seconds")))
        if own:
            conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        if own:
            conn.close()


def dismiss(kind: str, key: str) -> dict[str, Any]:
    """"Not an issue." Changes no data and is never asked again."""
    if not str(kind or "").strip() or not str(key or "").strip():
        return {"ok": False, "error": "Nothing to dismiss."}
    return {"ok": _record(str(kind), str(key), "dismissed")}


def undismiss(kind: str, key: str) -> dict[str, Any]:
    conn = master_items._connect()
    try:
        master_items.init(conn)
        conn.execute("DELETE FROM master_review WHERE kind = ? AND key = ?",
                     (str(kind), str(key)))
        conn.commit()
        return {"ok": True}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Detectors - every one of these is local. No network, no model.
# --------------------------------------------------------------------------- #

def _duplicates(rows) -> list[dict[str, Any]]:
    """One product stored twice: same name, same description, same unit MEANING.

    The same rule `master_items.duplicate_ids` uses, because a second opinion
    about what a duplicate is would be a second answer to "how many records do
    I have". What is added here is the evidence - above all whether the copies
    agree about the price, which is the reason this matters: Round Table sat on
    file at $300 and $400 and the catalogue could hand an invoice either.
    """
    # Indexed under BOTH names, exactly as `master_items.duplicate_ids` does.
    # Keying on the English name alone missed the pair that matters most: the
    # 2026-09-07 `سبلت جداري` copies are "Wall Split Unit" and "Wall-Mounted
    # Split AC", one product under one Arabic name and two English ones, which
    # no English-first key can see.
    groups: dict[tuple, list] = {}
    for r in rows:
        detail = (_norm(_description(r)),
                  master_items.unit_class(_unit(r)) or _norm(_unit(r)))
        for nm in (_words(r, "name_en"), _words(r, "name_ar")):
            if _norm(nm):
                groups.setdefault((_norm(nm), *detail), []).append(r)
    out = []
    done: set[int] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        # A record matching under both its names belongs to the FIRST group
        # only, or the screen reports the same problem twice.
        if any(r["item_id"] in done for r in members):
            continue
        done |= {r["item_id"] for r in members}
        members = sorted(members, key=lambda r: (-(r["use_count"] or 0),
                                                 str(r["created_at"] or "")))
        prices = price_relation([r["price_native"] for r in members])
        out.append({
            "kind": "duplicate",
            "key": _group_key("duplicate", members),
            "title": _name(members[0]),
            "evidence": {
                "price": prices,
                "count": len(members),
                "rows": [_row(r) for r in members],
            },
            "proposal": {"action": "merge",
                         "keep": members[0]["item_id"],
                         "drop": [r["item_id"] for r in members[1:]]},
            "item_ids": [r["item_id"] for r in members],
            # A price disagreement is the difference between tidying and
            # preventing a wrong invoice, so it leads the list.
            "weight": 100 + (50 if prices == "far" else 0) + len(members),
        })
    return out


def _variants_needing_descriptions(rows) -> list[dict[str, Any]]:
    """Products that share a name and cannot be told apart.

    Waleed's ruling: these are VARIANTS, not duplicates - FLOUR really is sold
    by the bag, the box and the kilo. The defect is that nothing on the record
    says which is which, so every lookup is a tie and the review popup asks him
    to choose, every single time.
    """
    groups: dict[tuple, list] = {}
    for r in rows:
        if not _norm(_name(r)):
            continue
        groups.setdefault((_norm(_name(r)), _norm(_description(r))), []).append(r)
    out = []
    for (name_key, _desc), members in groups.items():
        if len(members) < 2:
            continue
        units = {master_items.unit_class(_unit(r)) or _norm(_unit(r)) for r in members}
        if len(units) < 2:
            continue                      # same unit too - that is a duplicate
        blank = [r for r in members if not _norm(_description(r))]
        if not blank:
            continue                      # already told apart
        out.append({
            "kind": "variant_needs_description",
            "key": _group_key("variant_needs_description", members),
            "title": _name(members[0]),
            "evidence": {
                "price": price_relation([r["price_native"] for r in members]),
                "count": len(members),
                "blank": len(blank),
                "rows": [_row(r) for r in members],
            },
            "proposal": {"action": "describe",
                         "item_ids": [r["item_id"] for r in blank]},
            "item_ids": [r["item_id"] for r in members],
            "weight": 60 + len(blank),
        })
    return out


def _junk(rows) -> list[dict[str, Any]]:
    """A date typed into a description, and newlines inside a field.

    Neither is a judgement call, so neither needs a model - but neither is a
    blank field either, so no chip can show them.
    """
    out = []
    for r in rows:
        for field in ("description_en", "description_ar"):
            value = _words(r, field)
            if not value or not _DATE.search(value):
                continue
            cleaned = re.sub(r"\s{2,}", " ", _DATE.sub(" ", value)).strip(" ,;-")
            out.append({
                "kind": "junk_date",
                "key": _group_key("junk_date", [r]) + "|" + field,
                "title": _name(r),
                "evidence": {"field": field, "was": value, "rows": [_row(r)]},
                "proposal": {"action": "set", "item_id": r["item_id"],
                             "patch": {field: cleaned}},
                "item_ids": [r["item_id"]],
                "weight": 30,
            })
        # NAMES only. A newline in a name matches nothing and prints oddly
        # ("Ice Box / Cooler Box" split before "48 Liter"). A newline in a
        # DESCRIPTION is usually deliberate and load-bearing - the 25-Seater Bus
        # record holds a four-line route with departure times, and flattening
        # that would destroy the only place those times are written down.
        # 2026-09-10: the first version claimed 134 fields and almost all of
        # them were descriptions doing their job.
        for field in ("name_en", "name_ar"):
            value = _words(r, field)
            if "\n" not in value and "\r" not in value:
                continue
            out.append({
                "kind": "junk_newline",
                "key": _group_key("junk_newline", [r]) + "|" + field,
                "title": _name(r),
                "evidence": {"field": field, "was": value, "rows": [_row(r)]},
                "proposal": {"action": "set", "item_id": r["item_id"],
                             "patch": {field: re.sub(r"\s+", " ", value).strip()}},
                "item_ids": [r["item_id"]],
                "weight": 20,
            })
    return out


# Words that make an item a PERSON rather than a thing being hired.
_PEOPLE = ("laborer", "labourer", "labor", "worker", "operator", "driver",
           "engineer", "supervisor", "foreman", "helper", "technician", "guard",
           "cleaner", "cook", "chef", "welder", "mason", "carpenter", "plumber",
           "electrician", "surveyor", "storekeeper", "عامل", "سائق", "مهندس",
           "مشرف", "فني", "حارس", "طباخ", "حداد", "نجار")


def _category_from_unit(row) -> str:
    """A price per MONTH, DAY or HOUR is not describing a product - but it does
    NOT say what KIND of thing it is.

    The first version called every time-priced record Manpower, and the review
    screen duly proposed **Manpower for a 4WD Pickup Truck**, a Boom Truck and a
    Cargo Crane. A truck hired by the month is Vehicles & Transport. The unit
    says HIRED; only the name says whether the thing hired is a person. So the
    rule needs both, and everything else is left to a human or the model rather
    than guessed - a wrong category is worse than a blank one, because nobody
    re-checks a field that is already filled.
    """
    unit = _norm(_unit(row))
    if not unit or not any(w in unit for w in master_items._TIME_UNITS):
        return ""
    name = f"{_norm(_name(row))} {_norm(_words(row, 'name_ar'))}"
    return "Manpower" if any(w in name for w in _PEOPLE) else ""


def _categories(rows) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if _words(r, "category"):
            continue
        guess = _category_from_unit(r)
        if not guess:
            continue                      # the rest is a question for the model
        out.append({
            "kind": "category",
            "key": _group_key("category", [r]),
            "title": _name(r),
            "evidence": {"why": f"priced per {_unit(r)}", "rows": [_row(r)]},
            "proposal": {"action": "set", "item_id": r["item_id"],
                         "patch": {"category": guess}},
            "item_ids": [r["item_id"]],
            "weight": 10,
        })
    return out


_DETECTORS = {
    "duplicate": _duplicates,
    "variant_needs_description": _variants_needing_descriptions,
    "junk_date": _junk,
    "junk_newline": _junk,
    "category": _categories,
}


def scan(kinds: Any = None, limit: int = 0) -> dict[str, Any]:
    """Every finding, worst first. Reads only."""
    wanted = {str(k) for k in kinds} if kinds else set(_DETECTORS)
    conn = master_items._connect()
    try:
        master_items.init(conn)
        rows = conn.execute("SELECT * FROM master_items").fetchall()
        ruled = decisions(conn)
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc), "findings": [], "counts": {}}
    finally:
        conn.close()

    findings: list[dict[str, Any]] = []
    for name in ("duplicate", "variant_needs_description", "junk_date", "category"):
        if name == "junk_date":
            produced = _junk(rows)          # yields junk_date AND junk_newline
        else:
            produced = _DETECTORS[name](rows)
        findings += [f for f in produced if f["kind"] in wanted]

    findings = [f for f in findings
                if ruled.get(f"{f['kind']}|{f['key']}") != "dismissed"]
    findings.sort(key=lambda f: (-f["weight"], f["title"].lower()))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    if limit and limit > 0:
        findings = findings[:limit]
    return {"ok": True, "findings": findings, "counts": counts,
            "dismissed": len(ruled)}


# --------------------------------------------------------------------------- #
# Applying one finding
# --------------------------------------------------------------------------- #

def _backup_root() -> Path:
    """Beside the other audited catalogue changes, not in the profile folder.

    `master_items` is SHARED between Abraj and Eshraqat, so a restore point for
    it belongs with the shared data, exactly where the 2026-09-07 cleanup put
    its own.
    """
    try:
        return profile_manager.shared_db_path().parent.parent / "master-data-backups"
    except Exception:
        return Path(__file__).resolve().parents[1] / "data" / "master-data-backups"


# One restore point per SESSION, not per finding: thirty merges would otherwise
# leave thirty copies of a 900-record database, and the useful thing to go back
# to is the catalogue as it was before this sitting started.
_SESSION_BACKUP: dict[str, str] = {}


def restore_point(force: bool = False) -> dict[str, Any]:
    """Copy the shared library aside before the first change of a session.

    A merge DELETES rows and there is no row-level undo. The 2026-09-07 cleanup
    set the precedent and it is the only reason that change was safe to run.
    """
    if _SESSION_BACKUP.get("path") and not force:
        return {"ok": True, "path": _SESSION_BACKUP["path"], "reused": True}
    try:
        source = Path(master_items.shared_library.db_path())             if hasattr(master_items.shared_library, "db_path") else None
    except Exception:
        source = None
    if source is None or not source.is_file():
        try:
            source = Path(profile_manager.shared_db_path())
        except Exception:
            return {"ok": False, "error": "Cannot find the shared library."}
    if not source.is_file():
        return {"ok": False, "error": "Cannot find the shared library."}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = _backup_root() / f"{stamp}-doctor"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        # A live SQLite file may have a -wal beside it; the online backup API
        # copies a consistent snapshot, which a file copy would not.
        with sqlite_policy.open_db(source, row_factory=False) as src, sqlite_policy.open_db(target, wal=False, row_factory=False) as dst:
            src.backup(dst)
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "error": str(exc)}
    _SESSION_BACKUP["path"] = str(folder)
    return {"ok": True, "path": str(folder), "reused": False}


def apply(kind: str, key: str, proposal: Any = None) -> dict[str, Any]:
    """Carry out ONE finding, and remember that it was carried out.

    The proposal is re-read from the caller rather than trusted blindly for its
    ids: `merge_records` and `save_record` do their own checking, and a stale
    id from a screen left open simply fails rather than editing a stranger.
    """
    kind = str(kind or "")
    proposal = proposal or {}
    action = str(proposal.get("action") or "")
    if action == "merge":
        # A merge deletes rows. Nothing may be deleted before the catalogue has
        # been copied aside, and a failed copy STOPS the merge rather than
        # proceeding without a way back.
        saved = restore_point()
        if not saved.get("ok"):
            return {"ok": False,
                    "error": f"No restore point could be made, so nothing was "
                             f"merged: {saved.get('error', '')}".strip()}
        keep = proposal.get("keep")
        drop = proposal.get("drop") or []
        result = master_items.merge_records(keep, drop)
        if not result.get("ok"):
            return result
        _record(kind, str(key), "applied")
        return {"ok": True, "merged": result.get("merged", 0)}
    if action == "set":
        patch = proposal.get("patch") or {}
        if not isinstance(patch, dict) or not patch:
            return {"ok": False, "error": "Nothing to change."}
        result = master_items.save_record(proposal.get("item_id"), patch)
        if not result.get("ok"):
            return result
        _record(kind, str(key), "applied")
        return {"ok": True, "item": result.get("item")}
    return {"ok": False, "error": f"Nothing to do for {action or 'this finding'}."}
