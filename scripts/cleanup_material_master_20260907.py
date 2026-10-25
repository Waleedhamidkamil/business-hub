"""Audited, reversible cleanup of the live material master.

The script always rehearses against a SQLite backup first. With ``--apply`` it
publishes the same reviewed transaction only if the live master has not changed
since the backup. Saved business documents and every non-material table remain
untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data" / "shared" / "library.db"
OLD_PLAN = ROOT / "scratchpad" / "deeper_master_pairing_20260820.py"
OUTPUT_ROOT = ROOT / "data" / "master-data-backups"

TEXT_FIELDS = (
    "name_en", "name_ar", "description_en", "description_ar", "unit_en",
    "unit_ar", "img_query", "last_company",
)
JUNK_IDS = (
    1934634, 1934635,  # Delivery-note numbers accidentally saved as materials.
    1960430, 1960431,  # Explicit delete/test records.
    1952493, 1952525, 1952526,  # One-letter A/B/Z test records.
    1964199, 1964213, 1964508, 1964509, 1964734,  # Demo car/material records.
)

# Gemini proposed translations are used only for blank English fields. These
# local-Iraqi terms remain untouched because the proposed meaning is not safe
# enough to publish without Waleed's confirmation.
TRANSLATION_BLOCKED = {
    1933867, 1933940, 1934009, 1934028, 1934030, 1934097, 1934101,
}

# Human-reviewed corrections for Iraqi terminology and professional naming.
TRANSLATION_OVERRIDES: dict[int, dict[str, str]] = {
    1933865: {"name_en": "Electric Motor", "description_en": "2 HP"},
    1933869: {"name_en": "Fosroc Conbextra GP Grout"},
    1933983: {"name_en": "20 L Insulated Water Cooler"},
    1933984: {"name_en": "100 Ah Battery"},
    1933943: {"name_en": "Dining Chair"},
    1934015: {"name_en": "Angle Iron"},
    1934020: {"name_en": "Squat Toilet Base"},
    1934026: {"name_en": "Squat Toilet Pan"},
    1934096: {"name_en": "Heavy-Duty Water Tank"},
    1934099: {"name_en": "4-Inch Steel Socket"},
    1934100: {"name_en": "4-Inch Steel Plug"},
    1934115: {"name_en": "O3 Safety Shoes", "description_en": "Size 43"},
    1934116: {"name_en": "PEAK Safety Shoes", "description_en": "Size 42"},
    1954797: {"name_en": "Swivel Office Chair"},
    1965409: {"name_en": "Wall-Mounted Split AC", "description_en": "2 Ton"},
}


def _load_old_plan():
    spec = importlib.util.spec_from_file_location("old_material_pairing", OLD_PLAN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load reviewed plan: {OLD_PLAN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connect(path: Path, mode: str = "rw") -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode={mode}", uri=True, timeout=15,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _backup(source: Path, target: Path) -> None:
    with closing(_connect(source, "ro")) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    return value


def _table_hash(conn: sqlite3.Connection, table: str) -> str:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    if not cols:
        return ""
    order = ",".join(f'"{c}"' for c in cols)
    digest = hashlib.sha256()
    for row in conn.execute(f'SELECT {order} FROM "{table}" ORDER BY {order}'):
        digest.update(json.dumps(
            [_json_value(v) for v in row], ensure_ascii=False, default=str,
            separators=(",", ":"),
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _other_table_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name <> 'master_items' ORDER BY name"
    )]
    return {table: _table_hash(conn, table) for table in tables}


def _stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) total,
        SUM(TRIM(COALESCE(name_en,''))='' AND TRIM(COALESCE(name_ar,''))<>'') arabic_only,
        SUM(TRIM(COALESCE(name_en,''))<>'' AND TRIM(COALESCE(name_ar,''))='') english_only,
        SUM(TRIM(COALESCE(name_en,''))<>'' AND TRIM(COALESCE(name_ar,''))<>'') bilingual,
        SUM(INSTR(COALESCE(name_en,''),CHAR(10))>0 OR INSTR(COALESCE(name_ar,''),CHAR(10))>0) names_with_newline,
        SUM(INSTR(COALESCE(unit_en,''),CHAR(10))>0 OR INSTR(COALESCE(unit_ar,''),CHAR(10))>0) units_with_newline,
        SUM(price_native IS NOT NULL AND price_native<=0) nonpositive_prices,
        SUM(cost_native=1 AND UPPER(COALESCE(cost_native_ccy,''))='IQD') one_iqd_costs
        FROM master_items"""
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def _op(label: str, keep: int, keep_name: str, drops=(), patch=None) -> dict[str, Any]:
    return {
        "label": label,
        "keep": int(keep),
        "keep_name": keep_name,
        "drops": tuple(int(v) for v in drops),
        "patch": dict(patch or {}),
    }


def _build_plan(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[str]]:
    old = _load_old_plan()
    operations = [dict(item, drops=tuple(item["drops"]), patch=dict(item["patch"]))
                  for item in old.OPS]

    extras_by_label = {
        "Office desk 1650": (1952444,),
        "Office chair": (1952446, 1954797),
        "Meeting table": (1952447,),
    }
    for item in operations:
        item["drops"] = tuple(dict.fromkeys(
            (*item["drops"], *extras_by_label.get(item["label"], ()))
        ))
        if item["label"] == "3-drawer pedestal":
            item.update(
                keep=1934593,
                keep_name="3-drawer mobile pedestal under desk",
                drops=(1933964, 1952445),
                patch={"name_ar": "مجرات 3 مجر", "unit_en": "EA"},
            )
        elif item["label"] == "Crowbar 0.8m translation":
            item["drops"] = (1952438,)
        elif item["label"] == "Scaffolding pipe 2mm typo duplicate":
            item["drops"] = tuple(dict.fromkeys((*item["drops"], 1934104)))
            item["patch"].update({
                "name_ar": "بوري سكلة",
                "description_ar": "2 ملم (خفيف)\n6 متر",
            })

    operations += [
        _op("Peach exact duplicate", 1934117, "Peach", [1952523]),
        _op("Pear bilingual duplicate", 1952452, "PEAR", [1934118]),
        _op("Camera box whitespace duplicate", 1934660, "BOX , CAMERA", [1952479]),
        _op("HD cable whitespace duplicate", 1934661, "Cable HD,4K,5M", [1952483]),
        _op("CAT6 cable whitespace duplicate", 1934662,
            "CABLE CAT6 , MORRELL 6063,305M", [1952484]),
        _op("Rapoo mouse whitespace duplicate", 1934663,
            "MOUS RAPOO WIRELESS", [1952482],
            {"name_en": "Rapoo Wireless Mouse"}),
        _op("WD 8TB whitespace duplicate", 1934664,
            "Original Storage memory HDD , WD 8TB", [1952478]),
        _op("PVC pipe whitespace duplicate", 1934665,
            "PIPE PLASTIC PVC 25mm (30PCS)", [1952485]),
        _op("RJ45 plug whitespace duplicate", 1934666,
            "PLUG RJ 45 BLUE STORM CAT6", [1952480]),
        _op("15U rack whitespace duplicate", 1934667,
            "RACK BLACK BEAR15U", [1952481]),
        _op("A4 file folder duplicate", 1957739,
            "Plastic Clear File Folder\n A4 FLAMINGO", [1954700],
            {"name_en": "Plastic Clear File Folder A4 FLAMINGO"}),
        _op("Office desk with drawers duplicate", 1954794,
            "Office desk & Drawers", [1963711]),
        _op("Office locker duplicate", 1954796, "Office Locker", [1963713]),
        _op("Safety vest with logo bilingual pair", 1954807,
            "يلك سلامة", [1934224], {
                "name_en": "Safety Vest",
                "description_en": "Yellow color\nWith logo",
            }),
        _op("Nylon rope 8mm bilingual pair", 1934054,
            "حبل نايلون", [1952436], {
                "name_en": "Nylon Rope",
                "description_en": "8mm nylon braided rope",
            }),
        _op("Mung beans bilingual pair", 1934123,
            "ماش", [1952461], {"name_en": "Mung Beans"}),
        _op("Millet bilingual pair", 1934124,
            "دخن", [1952462], {"name_en": "Millet"}),
        _op("Frozen boneless chicken exact duplicate", 1933892,
            "Frozen Boneless Chicken Whole", [1952489]),
        _op("Full-fat milk exact duplicate", 1933900,
            "Full Fat Milk", [1952459]),
        _op("Cucumber pickle exact duplicate", 1933902,
            "Cucumber Pickle", [1952490]),
        _op("Frozen tilapia exact duplicate", 1933948,
            "Frozen Tilapia (Clean, 200/300g)", [1952487]),
        _op("Mouse trap exact duplicate", 1934334,
            "Mouse trap", [1952430]),
        _op("Induction cooker exact duplicate", 1934339,
            "Induction cooker", [1952431]),
        _op("Portable flashlight exact duplicate", 1934343,
            "Portable high-intensity flashlight", [1952432]),
        _op("Electrical wire exact duplicate", 1934379,
            "Electrical wire", [1952477]),
        _op("Frozen mackerel exact duplicate", 1934581,
            "Frozen Mackerel Fish(Clean, 200/400g)", [1952488]),
    ]

    notes: list[str] = []
    ready: list[dict[str, Any]] = []
    touched: set[int] = set()
    for item in operations:
        ids = (item["keep"], *item["drops"])
        present = {r[0] for r in conn.execute(
            f"SELECT item_id FROM master_items WHERE item_id IN ({','.join('?' * len(ids))})",
            ids,
        )}
        if item["keep"] not in present:
            raise RuntimeError(f"{item['label']}: survivor {item['keep']} is missing")
        missing_drops = set(item["drops"]) - present
        if missing_drops:
            if item["label"] in {"Chicken breast spelling", "Tomato bad-price duplicate"}:
                notes.append(f"Already completed earlier: {item['label']}")
                item["drops"] = tuple(v for v in item["drops"] if v in present)
            else:
                raise RuntimeError(
                    f"{item['label']}: expected duplicate rows are missing: {sorted(missing_drops)}"
                )
        keep = conn.execute(
            "SELECT name_en,name_ar FROM master_items WHERE item_id=?", (item["keep"],)
        ).fetchone()
        visible = (keep["name_en"] or keep["name_ar"] or "").strip()
        if visible.casefold() != item["keep_name"].casefold():
            raise RuntimeError(
                f"{item['label']}: survivor changed from {item['keep_name']!r} to {visible!r}"
            )
        current_ids = {item["keep"], *item["drops"]}
        overlap = touched & current_ids
        if overlap:
            raise RuntimeError(f"Plan reuses material ids: {sorted(overlap)}")
        touched |= current_ids
        if item["drops"] or item["patch"]:
            ready.append(item)
    return ready, notes


def _load_translation_patches(path: Path | None) -> tuple[dict[int, dict[str, str]], str]:
    if path is None:
        return {}, ""
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError("Translation review contains unresolved Gemini errors")
    patches: dict[int, dict[str, str]] = {}
    for suggestion in payload.get("suggestions", []):
        item_id = int(suggestion["item_id"])
        if item_id in JUNK_IDS or item_id in TRANSLATION_BLOCKED:
            continue
        gemini = suggestion.get("gemini") or {}
        patch = {
            field: str(gemini.get(field) or "").strip()
            for field in ("name_en", "description_en")
        }
        patch.update(TRANSLATION_OVERRIDES.get(item_id, {}))
        patch = {field: value for field, value in patch.items() if value}
        if not patch.get("name_en"):
            continue
        if any(re.search(r"[\u0600-\u06ff]", value) for value in patch.values()):
            raise RuntimeError(f"Arabic text found in English translation for item {item_id}")
        if item_id in patches:
            raise RuntimeError(f"Duplicate translation suggestion for item {item_id}")
        patches[item_id] = patch
    return patches, hashlib.sha256(raw).hexdigest()


def _newest_with(rows: list[sqlite3.Row], field: str):
    candidates = [r for r in rows if r[field] not in (None, "")]
    return max(
        candidates,
        key=lambda r: (r["last_used_at"] or "", r["use_count"] or 0),
        default=None,
    )


def _merge_images(rows: list[sqlite3.Row]) -> str | None:
    values: list[str] = []
    for row in rows:
        try:
            blob = json.loads(row["image_refs"] or "null")
        except (TypeError, ValueError):
            continue
        if isinstance(blob, str):
            values.append(blob)
        elif isinstance(blob, dict):
            values.extend(str(v) for v in blob.values() if v)
    unique = list(dict.fromkeys(values))
    if not unique:
        return None
    return json.dumps(
        {"image" if i == 0 else f"image_{i + 1}": value
         for i, value in enumerate(unique)},
        ensure_ascii=False,
    )


def _merged_values(rows: list[sqlite3.Row], keep_id: int,
                   patch: dict[str, Any], changed_at: str) -> dict[str, Any]:
    base = next(r for r in rows if r["item_id"] == keep_id)
    out = dict(base)
    for field in TEXT_FIELDS:
        if field in patch:
            out[field] = patch[field]
        elif not str(out[field] or "").strip():
            donor = _newest_with(rows, field)
            if donor is not None:
                out[field] = donor[field]
    for amount, currency in (
        ("cost_native", "cost_native_ccy"),
        ("price_native", "price_native_ccy"),
    ):
        donor = _newest_with(rows, amount)
        if donor is not None:
            out[amount], out[currency] = donor[amount], donor[currency]
    out["image_refs"] = _merge_images(rows)
    out["use_count"] = sum(int(r["use_count"] or 0) for r in rows)
    out["last_used_at"] = max((r["last_used_at"] or "" for r in rows), default="")
    out["created_at"] = min(
        (r["created_at"] or "" for r in rows if r["created_at"]),
        default=base["created_at"],
    )
    out["updated_at"] = changed_at
    if not out["data_json"]:
        donor = _newest_with(rows, "data_json")
        if donor is not None:
            out["data_json"] = donor["data_json"]
    return out


def _apply_cleanup(conn: sqlite3.Connection, plan: list[dict[str, Any]],
                   changed_at: str,
                   translation_patches: dict[int, dict[str, str]]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "deleted_junk": [], "cleared_invalid_money": [], "merged": [],
        "normalized_names": [], "repaired_units": [], "corrected_text": [],
        "filled_english": [],
    }

    for item_id in JUNK_IDS:
        row = conn.execute(
            "SELECT item_id,item_no,name_en,name_ar FROM master_items WHERE item_id=?",
            (item_id,),
        ).fetchone()
        if row:
            report["deleted_junk"].append(dict(row))
            conn.execute("DELETE FROM master_items WHERE item_id=?", (item_id,))

    bad_costs = list(conn.execute(
        "SELECT item_id,item_no,name_en,name_ar,cost_native,cost_native_ccy "
        "FROM master_items WHERE cost_native=1 "
        "AND UPPER(COALESCE(cost_native_ccy,''))='IQD'"
    ))
    for row in bad_costs:
        report["cleared_invalid_money"].append(dict(row))
    conn.execute(
        "UPDATE master_items SET cost_native=NULL,cost_native_ccy='',updated_at=? "
        "WHERE cost_native=1 AND UPPER(COALESCE(cost_native_ccy,''))='IQD'",
        (changed_at,),
    )
    bad_prices = list(conn.execute(
        "SELECT item_id,item_no,name_en,name_ar,price_native,price_native_ccy "
        "FROM master_items WHERE price_native IS NOT NULL AND price_native<=0"
    ))
    for row in bad_prices:
        report["cleared_invalid_money"].append(dict(row))
    conn.execute(
        "UPDATE master_items SET price_native=NULL,price_native_ccy='',updated_at=? "
        "WHERE price_native IS NOT NULL AND price_native<=0",
        (changed_at,),
    )

    editable = [r[1] for r in conn.execute("PRAGMA table_info(master_items)")
                if r[1] != "item_id"]
    for item in plan:
        ids = (item["keep"], *item["drops"])
        rows = list(conn.execute(
            f"SELECT * FROM master_items WHERE item_id IN ({','.join('?' * len(ids))})",
            ids,
        ))
        if len(rows) != len(ids):
            raise RuntimeError(f"{item['label']}: rows changed while applying")
        merged = _merged_values(rows, item["keep"], item["patch"], changed_at)
        conn.execute(
            "UPDATE master_items SET " + ",".join(f'"{field}"=?' for field in editable)
            + " WHERE item_id=?",
            [merged[field] for field in editable] + [item["keep"]],
        )
        if item["drops"]:
            deleted = conn.execute(
                f"DELETE FROM master_items WHERE item_id IN ({','.join('?' * len(item['drops']))})",
                item["drops"],
            ).rowcount
            if deleted != len(item["drops"]):
                raise RuntimeError(f"{item['label']}: expected {len(item['drops'])} deletes")
        report["merged"].append({
            "label": item["label"], "kept_item_id": item["keep"],
            "kept_item_no": merged["item_no"], "name_en": merged["name_en"],
            "name_ar": merged["name_ar"], "removed_item_ids": list(item["drops"]),
        })

    for item_id, proposed in translation_patches.items():
        row = conn.execute(
            "SELECT item_id,item_no,name_en,name_ar,description_en FROM master_items "
            "WHERE item_id=?", (item_id,),
        ).fetchone()
        if not row:
            continue
        patch: dict[str, str] = {}
        if not str(row["name_en"] or "").strip() and proposed.get("name_en"):
            patch["name_en"] = proposed["name_en"]
        if (not str(row["description_en"] or "").strip()
                and proposed.get("description_en")):
            patch["description_en"] = proposed["description_en"]
        if not patch:
            continue
        conn.execute(
            "UPDATE master_items SET " + ",".join(f'"{key}"=?' for key in patch)
            + ",updated_at=? WHERE item_id=?",
            [*patch.values(), changed_at, item_id],
        )
        report["filled_english"].append({
            "item_id": item_id, "item_no": row["item_no"],
            "name_ar": row["name_ar"], "values": patch,
        })

    rows = list(conn.execute(
        "SELECT item_id,name_en,name_ar FROM master_items "
        "WHERE INSTR(COALESCE(name_en,''),CHAR(10))>0 "
        "OR INSTR(COALESCE(name_ar,''),CHAR(10))>0 "
        "OR INSTR(COALESCE(name_en,''),CHAR(13))>0 "
        "OR INSTR(COALESCE(name_ar,''),CHAR(13))>0"
    ))
    for row in rows:
        before = {"name_en": row["name_en"], "name_ar": row["name_ar"]}
        after = {key: re.sub(r"\s+", " ", str(before[key] or "")).strip()
                 for key in before}
        conn.execute(
            "UPDATE master_items SET name_en=?,name_ar=?,updated_at=? WHERE item_id=?",
            (after["name_en"], after["name_ar"], changed_at, row["item_id"]),
        )
        report["normalized_names"].append(
            {"item_id": row["item_id"], "before": before, "after": after}
        )

    rows = list(conn.execute(
        "SELECT item_id,unit_en,description_en FROM master_items "
        "WHERE INSTR(COALESCE(unit_en,''),CHAR(10))>0"
    ))
    for row in rows:
        parts = [part.strip() for part in re.split(r"[\r\n]+", row["unit_en"] or "")
                 if part.strip()]
        if len(parts) < 2 or parts[0].upper() != "EA":
            raise RuntimeError(f"Unexpected multiline unit on item {row['item_id']}")
        description = (row["description_en"] or "").strip() or "\n".join(parts[1:])
        conn.execute(
            "UPDATE master_items SET unit_en='EA',description_en=?,updated_at=? "
            "WHERE item_id=?", (description, changed_at, row["item_id"]),
        )
        report["repaired_units"].append({
            "item_id": row["item_id"], "before": row["unit_en"],
            "unit_en": "EA", "description_en": description,
        })

    text_patches = {
        1934672: {"name_en": "6-Port PoE Switch 300W"},
        1934545: {"description_en": "12 Ton\n1 1/4 inch\nType BC"},
        1934546: {"description_en": "3.5 Ton\n5/8 inch\nType BC"},
        1933869: {"description_en": "Conbextra GP"},
    }
    for item_id, patch in text_patches.items():
        row = conn.execute("SELECT * FROM master_items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            continue
        before = {key: row[key] for key in patch}
        conn.execute(
            "UPDATE master_items SET " + ",".join(f'"{key}"=?' for key in patch)
            + ",updated_at=? WHERE item_id=?",
            [*patch.values(), changed_at, item_id],
        )
        report["corrected_text"].append(
            {"item_id": item_id, "before": before, "after": patch}
        )

    return report


def _validate(conn: sqlite3.Connection) -> dict[str, Any]:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    if conn.execute(
        "SELECT COUNT(*) FROM master_items WHERE "
        "TRIM(COALESCE(name_en,''))='' AND TRIM(COALESCE(name_ar,''))=''"
    ).fetchone()[0]:
        raise RuntimeError("Cleanup produced a material with no name")
    if conn.execute(
        "SELECT COUNT(*) FROM (SELECT item_no FROM master_items "
        "WHERE TRIM(COALESCE(item_no,''))<>'' GROUP BY item_no HAVING COUNT(*)>1)"
    ).fetchone()[0]:
        raise RuntimeError("Cleanup produced duplicate material numbers")
    if conn.execute(
        "SELECT COUNT(*) FROM master_items WHERE "
        "INSTR(COALESCE(unit_en,''),CHAR(10))>0 OR "
        "INSTR(COALESCE(unit_ar,''),CHAR(10))>0"
    ).fetchone()[0]:
        raise RuntimeError("A multiline unit remains after cleanup")
    return {"integrity": integrity, "stats": _stats(conn),
            "master_hash": _table_hash(conn, "master_items")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--translation-review", type=Path)
    args = parser.parse_args()

    translation_patches, translation_hash = _load_translation_patches(
        args.translation_review
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    changed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = OUTPUT_ROOT / stamp
    output.mkdir(parents=True, exist_ok=False)
    before_db = output / "library.before.db"
    rehearsal_db = output / "library.rehearsal.db"
    _backup(LIVE, before_db)
    _backup(LIVE, rehearsal_db)

    with closing(_connect(before_db, "ro")) as before:
        before_stats = _stats(before)
        before_master_hash = _table_hash(before, "master_items")
        other_hashes = _other_table_hashes(before)
        plan, notes = _build_plan(before)

    with closing(_connect(rehearsal_db)) as rehearsal:
        rehearsal.execute("BEGIN IMMEDIATE")
        changes = _apply_cleanup(rehearsal, plan, changed_at, translation_patches)
        rehearsal.commit()
        rehearsal_validation = _validate(rehearsal)
        if _other_table_hashes(rehearsal) != other_hashes:
            raise RuntimeError("Rehearsal changed a non-material table")

    applied = False
    if args.apply:
        with closing(_connect(LIVE)) as live:
            if _table_hash(live, "master_items") != before_master_hash:
                raise RuntimeError("Live Master Data changed during rehearsal; nothing applied")
            if _other_table_hashes(live) != other_hashes:
                raise RuntimeError("A non-material table changed during rehearsal; nothing applied")
            live.execute("BEGIN IMMEDIATE")
            live_changes = _apply_cleanup(live, plan, changed_at, translation_patches)
            live.commit()
            live_validation = _validate(live)
            if live_validation["master_hash"] != rehearsal_validation["master_hash"]:
                raise RuntimeError("Published Master Data does not match the rehearsal")
            if _other_table_hashes(live) != other_hashes:
                raise RuntimeError("Published cleanup changed a non-material table")
            if live_changes != changes:
                raise RuntimeError("Published audit differs from the rehearsal")
        applied = True

    report = {
        "applied": applied,
        "created_at": changed_at,
        "live_database": str(LIVE),
        "restore_point": str(before_db),
        "rehearsal_database": str(rehearsal_db),
        "before": before_stats,
        "after": rehearsal_validation["stats"],
        "plan": {"operations": len(plan),
                 "duplicate_rows_removed": sum(len(item["drops"]) for item in plan)},
        "notes": notes,
        "translation_review": {
            "source": str(args.translation_review) if args.translation_review else "",
            "sha256": translation_hash,
            "blocked_item_ids": sorted(TRANSLATION_BLOCKED),
        },
        "changes": changes,
        "verification": {
            "integrity": rehearsal_validation["integrity"],
            "non_material_tables_unchanged": True,
            "live_matches_rehearsal": applied,
        },
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "applied": applied,
        "output": str(output),
        "before": before_stats,
        "after": rehearsal_validation["stats"],
        "operations": len(plan),
        "duplicate_rows_removed": report["plan"]["duplicate_rows_removed"],
        "junk_rows_removed": len(changes["deleted_junk"]),
        "invalid_money_cleared": len(changes["cleared_invalid_money"]),
        "names_normalized": len(changes["normalized_names"]),
        "units_repaired": len(changes["repaired_units"]),
        "english_fields_filled": len(changes["filled_english"]),
        "integrity": rehearsal_validation["integrity"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
