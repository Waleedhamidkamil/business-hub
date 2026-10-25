"""ONE declaration of every per-document setting (2026-09-05).

Why this file exists
--------------------
A setting used to be spelled out in four unrelated places - the panel's HTML, the
payload the panel posted, the branch in `config._apply_user_settings` that read
it, and whatever consumed the global afterwards. Nothing tied those together, so
a setting could be offered, saved, reloaded and still reach nothing. That is
exactly what happened to the PO row height: it was in the panel, it saved, it
came back on reload, and the page it was supposed to size never looked at it
(see `pdf_generator.estimate_row_height_mm` and the 2026-09-05 entry in
[[04-BACKEND-DOCGEN]]).

So each setting is declared ONCE here, together with the config global it is
supposed to move. `tests/doc_settings_unified.test.py` walks this table and
proves every field round-trips through the bridge AND changes its target - a
knob cannot go inert again without failing the suite.

Layout on disk
--------------
Each document type owns one SECTION of settings.json:

    purchase_order / quotation / delivery / invoice

The PO's keys lived at the TOP LEVEL until 2026-09-05, which is why it needed its
own save path with its own merge rule while the other three shared another. They
are moved into `purchase_order` by migration `2026-09-05_po_settings_section`;
`LEGACY_TOP_LEVEL` keeps the old spellings readable so an un-migrated profile (or
a settings.json restored from an old backup) still loads.

App-level keys - `documents_root`, `po_root`, `ui_lang`, `preferences`,
`signing` - are NOT document settings and deliberately stay at the top level.
"""
from __future__ import annotations

from typing import Any, Callable, NamedTuple

# doc kind -> its section name in settings.json
SECTIONS: dict[str, str] = {
    "po": "purchase_order",
    "quotation": "quotation",
    "delivery": "delivery",
    "invoice": "invoice",
}

KINDS = tuple(SECTIONS)

# Top-level keys that belong to the APP, not to any document. Never migrated
# into a section, never touched by save_doc_settings.
APP_LEVEL_KEYS = ("documents_root", "po_root", "ui_lang", "preferences", "signing")


class Field(NamedTuple):
    """One setting, and the config global it is supposed to move.

    path    where it lives inside its section, e.g. ("layout", "table_min_row_mm")
    legacy  the old TOP-LEVEL key, when this setting used to live there
    cast    how the stored value becomes the config value
    target  the name of the `config` global this field feeds, or "" when the
            value is consumed directly out of the section (terms, columns…).
            The guard test asserts every named target actually moves.
    """
    path: tuple[str, ...]
    legacy: str = ""
    cast: str = "str"
    target: str = ""


def _f(*path, legacy: str = "", cast: str = "str", target: str = "") -> Field:
    return Field(tuple(path), legacy, cast, target)


# --------------------------------------------------------------------------- #
# Purchase Order - the type that moved. Spacing sits under `layout` so it is
# shaped like the other three rather than being a flat special case.
# --------------------------------------------------------------------------- #
PO_FIELDS: tuple[Field, ...] = (
    _f("prefix",       legacy="po_prefix",   cast="str",   target="PO_PREFIX"),
    _f("brand_color",  legacy="brand_color", cast="color", target="BRAND_COLOR"),
    _f("company",      legacy="company",     cast="dict",  target="COMPANY"),
    _f("company_tags", legacy="po_company_tags", cast="dict", target="PO_COMPANY_TAGS"),
    _f("layout", "autofit_rows",
       legacy="autofit_rows",         cast="bool",  target="PO_AUTOFIT"),
    _f("layout", "table_header_mm",
       legacy="table_header_mm",      cast="float", target="TABLE_HEADER_MM"),
    _f("layout", "table_min_row_mm",
       legacy="table_min_row_mm",     cast="float", target="TABLE_MIN_ROW_MM"),
    _f("layout", "table_line_height_mm",
       legacy="table_line_height_mm", cast="float", target="TABLE_LINE_HEIGHT_MM"),
    _f("layout", "signature_mm",
       legacy="po_signature_mm",      cast="float", target="PO_SIGNATURE_MM"),
)

# The other three already lived in sections and `config` already applies them;
# they are declared so the SAVE path, the merge rule and the bridge are one
# thing for all four. Their per-sub-type overrides are handled by
# `save_doc_settings`'s nested rule, not enumerated here.
QUOTE_FIELDS: tuple[Field, ...] = (
    _f("brand_color",     cast="color", target="QUOTE_BRAND_COLOR"),
    _f("company",         cast="dict",  target="QUOTE_COMPANY"),
    _f("address_options", cast="list",  target="QUOTE_ADDRESS_OPTIONS"),
    _f("layout",          cast="dict",  target="QUOTE_LAYOUT"),
)
DN_FIELDS: tuple[Field, ...] = (
    _f("brand_color",     cast="color", target="DN_BRAND_COLOR"),
    _f("company",         cast="dict",  target="DN_COMPANY"),
    _f("address_options", cast="list",  target="DN_ADDRESS_OPTIONS"),
    _f("layout",          cast="dict",  target="DN_LAYOUT"),
)
INV_FIELDS: tuple[Field, ...] = (
    _f("brand_color",     cast="color", target="INV_BRAND_COLOR"),
    _f("company",         cast="dict",  target="INV_COMPANY"),
    _f("address_options", cast="list",  target="INV_ADDRESS_OPTIONS"),
    _f("layout",          cast="dict",  target="INV_LAYOUT"),
    _f("bank_info",       cast="list",  target="INV_BANK_INFO"),
    # 2026-09-21 - the rate a document is costed at when no receiving account
    # can be determined. Declared so it is not dropped on save, and read by
    # payment_accounts.fallback_rate().
    _f("fallback_iqd_per_usd", cast="float", target="INV_FALLBACK_IQD_PER_USD"),
    _f("withholding_pct", cast="float", target="INV_WITHHOLDING_PCT"),
)

FIELDS: dict[str, tuple[Field, ...]] = {
    "po": PO_FIELDS,
    "quotation": QUOTE_FIELDS,
    "delivery": DN_FIELDS,
    "invoice": INV_FIELDS,
}


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

_MISSING = object()


def dig(blob: Any, path: tuple[str, ...]) -> Any:
    """Follow `path` into nested dicts, or `_MISSING`."""
    cur = blob
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def read(settings: dict, kind: str, field: Field) -> Any:
    """This field's stored value: the section first, then the legacy top-level
    spelling, then `_MISSING`.

    The legacy fallback is what lets an un-migrated profile - or a settings.json
    restored from a backup taken before 2026-09-05 - keep working.
    """
    section = (settings or {}).get(SECTIONS.get(kind, kind)) or {}
    found = dig(section, field.path)
    if found is not _MISSING:
        return found
    if field.legacy:
        legacy = (settings or {}).get(field.legacy, _MISSING)
        if legacy is not _MISSING:
            return legacy
    return _MISSING


def is_missing(value: Any) -> bool:
    return value is _MISSING


def legacy_keys(kind: str) -> tuple[str, ...]:
    """The old top-level spellings this kind used to use."""
    return tuple(f.legacy for f in FIELDS.get(kind, ()) if f.legacy)


# --------------------------------------------------------------------------- #
# Merging
# --------------------------------------------------------------------------- #

def deep_merge(base: dict, patch: dict) -> dict:
    """Merge `patch` into `base`, recursing into nested dicts.

    A SHALLOW merge is what let one panel wipe another's keys: the PO panel sent
    a whole `company` object and replaced everything already stored under it. A
    caller that means "remove this" sends an empty value, which pops the key -
    that is how a doc-type override is cleared and the default comes back.
    """
    out = dict(base or {})
    for key, value in (patch or {}).items():
        if value in (None, "", {}, []):
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = deep_merge(out[key], value)
            if merged:
                out[key] = merged
            else:
                out.pop(key, None)
        else:
            out[key] = value
    return out
