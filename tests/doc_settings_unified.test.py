"""One settings contract for all four document types (2026-09-05).

The bug this exists to prevent
------------------------------
A setting could be offered in a panel, saved, reloaded - and still reach
nothing. The PO row height did exactly that: `table_row_mm` was handed to
po_page.html and never used, so typing a new number moved the page breaks and
nothing on the page. From the outside that is indistinguishable from "the
settings don't save", which is how it was reported.

Nothing could have caught it, because a setting was spelled out in four
unrelated places with no link between them. `backend/doc_settings.py` is now the
single declaration, and each field names the `config` global it is supposed to
move. This test walks that declaration and proves the value travels the whole
way: panel payload -> save_doc_settings -> settings.json -> config global.

Also pinned here: the merge is DEEP. The old shallow merge let a panel that sent
a whole `company` object replace everything under it, dropping keys it never
displayed.

LIVE settings.json - this writes to the active profile's real file (the app has
no test isolation), so the file is backed up and restored around every test.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import api, config, doc_settings  # noqa: E402


# A distinctive value per cast, chosen so a stale default can't look like a pass.
PROBE = {
    "str":   "ZZ_TEST_PREFIX",
    "color": "#4B2E83",
    "float": 17.25,
    "bool":  True,
}


class UnifiedDocSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(config.SETTINGS_PATH)
        cls.backup = cls.path.read_text(encoding="utf-8") if cls.path.is_file() else None

    def setUp(self):
        self.api = api.Api()

    def tearDown(self):
        # Put the user's real settings back, whatever the test did.
        if self.backup is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_text(self.backup, encoding="utf-8")
        importlib.reload(config)

    # ------------------------------------------------------------------ shape
    def test_every_doc_type_has_a_section(self):
        self.assertEqual(sorted(doc_settings.SECTIONS),
                         ["delivery", "invoice", "po", "quotation"])
        self.assertEqual(doc_settings.SECTIONS["po"], "purchase_order")

    def test_app_level_keys_are_not_document_settings(self):
        """documents_root / ui_lang / preferences / signing belong to the app.
        If one of them ever appears in a doc section, two panels own it."""
        for kind, fields in doc_settings.FIELDS.items():
            for f in fields:
                self.assertNotIn(
                    f.path[0], doc_settings.APP_LEVEL_KEYS,
                    f"{kind}.{'.'.join(f.path)} collides with an app-level key")

    def test_unknown_doc_type_is_refused_on_both_sides(self):
        self.assertIn("error", self.api.get_doc_settings("letter"))
        r = self.api.save_doc_settings("letter", {"x": 1})
        self.assertFalse(r.get("ok"))

    # ----------------------------------------------- the value must travel
    def test_every_declared_po_setting_reaches_its_config_global(self):
        """The heart of it. For each PO field, save a distinctive value through
        the bridge and assert the config global it names actually moved."""
        unreachable = []
        for f in doc_settings.PO_FIELDS:
            if f.cast not in PROBE or not f.target:
                continue                      # dict/list fields checked below
            want = PROBE[f.cast]
            if f.cast == "bool":
                want = not bool(getattr(config, f.target))

            # Build the nested payload this field's path implies.
            payload: dict = {}
            cursor = payload
            for part in f.path[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[f.path[-1]] = want

            r = self.api.save_doc_settings("po", payload)
            self.assertTrue(r.get("ok"), f"saving {f.path} failed: {r}")
            importlib.reload(config)
            got = getattr(config, f.target, None)
            if f.cast == "float":
                ok = abs(float(got) - float(want)) < 0.001
            elif f.cast == "bool":
                ok = bool(got) == bool(want)
            else:
                ok = str(got).strip().lower() == str(want).strip().lower()
            if not ok:
                unreachable.append(
                    f"{'.'.join(f.path)} -> config.{f.target}: saved {want!r}, config has {got!r}")
        self.assertEqual(
            unreachable, [],
            "these settings do not reach the renderer - the exact defect that "
            "made the PO row height inert:\n  " + "\n  ".join(unreachable))

    def test_non_scalar_settings_reach_their_config_global_too(self):
        """The hole this test HAD, and the bug it let through.

        The scalar walk above skips `cast="dict"` fields, so `company_tags`
        went unchecked - and it was still being read from the OLD top-level key
        after the panel had moved to `purchase_order.company_tags`. Saved to
        disk, never reached `config.PO_COMPANY_TAGS`, invisible on reopen:
        indistinguishable from "the settings don't save". A cast this test
        cannot probe is a cast where the next one will hide."""
        probes = [
            ("po",        "company_tags",    {"ZZ Test Co": "internal"}, "PO_COMPANY_TAGS"),
            ("po",        "company",         {"title_ar": "ZZ_TEST_TITLE"}, "COMPANY"),
            # `list` lives on the other three; probe one so no cast is unchecked.
            ("quotation", "address_options", ["ZZ_TEST_SITE"], "QUOTE_ADDRESS_OPTIONS"),
        ]
        unreachable = []
        for kind, key, value, target in probes:
            r = self.api.save_doc_settings(kind, {key: value})
            self.assertTrue(r.get("ok"), f"saving {kind}.{key} failed: {r}")
            importlib.reload(config)
            got = getattr(config, target, None)
            if isinstance(value, list):
                if list(got or []) != value:
                    unreachable.append(
                        f"{kind}.{key} -> config.{target}: saved {value!r}, config has {got!r}")
                continue
            got = got or {}
            for k, v in value.items():
                if str(got.get(k, "")).strip().lower() != str(v).strip().lower():
                    unreachable.append(
                        f"{kind}.{key}.{k} -> config.{target}: saved {v!r}, config has {got.get(k)!r}")
        self.assertEqual(
            unreachable, [],
            "these dict settings do not reach the renderer:\n  "
            + "\n  ".join(unreachable))

    def test_no_declared_field_is_left_unprobed(self):
        """A field whose cast this file cannot probe is a blind spot - say so
        loudly rather than letting it pass silently, which is what happened."""
        probed = set(PROBE) | {"dict", "list"}
        blind = sorted({f.cast for fields in doc_settings.FIELDS.values()
                        for f in fields} - probed)
        self.assertEqual(
            blind, [],
            f"no test covers cast(s) {blind}; add a probe before declaring a "
            "field that uses one")

    def test_a_dict_setting_merges_instead_of_replacing(self):
        """Sending ONE company field must not drop the others."""
        before = set((self.api.get_doc_settings("po")["settings"].get("company") or {}))
        self.assertGreater(len(before), 1, "fixture needs a multi-key company")
        r = self.api.save_doc_settings("po", {"company": {"title_ar": "ZZ_TEST_TITLE"}})
        self.assertTrue(r.get("ok"), r)
        after = self.api.get_doc_settings("po")["settings"].get("company") or {}
        self.assertEqual(after.get("title_ar"), "ZZ_TEST_TITLE")
        self.assertEqual(before - set(after), set(),
                         "a one-field save dropped sibling company keys")

    def test_a_sibling_section_is_never_touched(self):
        """Saving the PO must not disturb quotation/delivery/invoice."""
        others = {k: self.api.get_doc_settings(k)["settings"]
                  for k in ("quotation", "delivery", "invoice")}
        self.api.save_doc_settings("po", {"layout": {"table_min_row_mm": 11.1}})
        for k, was in others.items():
            self.assertEqual(self.api.get_doc_settings(k)["settings"], was,
                             f"saving the PO changed the {k} section")

    def test_an_empty_value_clears_the_override(self):
        self.api.save_doc_settings("po", {"prefix": "ZZ"})
        self.assertEqual(self.api.get_doc_settings("po")["settings"].get("prefix"), "ZZ")
        self.api.save_doc_settings("po", {"prefix": ""})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("prefix", stored.get("purchase_order") or {},
                         "an empty value must pop the override, not store a blank")

    def test_po_editor_can_distinguish_explicit_layout_from_auto_defaults(self):
        """The renderer needs resolved values, but blank UI fields need raw overrides."""
        self.api.save_doc_settings("po", {"layout": {
            "table_min_row_mm": 11.1,
            "table_line_height_mm": 6.5,
        }})
        first = self.api.get_doc_settings("po")
        self.assertEqual(first["stored"]["layout"]["table_min_row_mm"], 11.1)
        self.assertEqual(first["settings"]["layout"]["table_min_row_mm"], 11.1)

        # Clearing one box must not require clearing every sibling box.
        self.api.save_doc_settings("po", {"layout": {"table_min_row_mm": ""}})
        second = self.api.get_doc_settings("po")
        self.assertNotIn("table_min_row_mm", second["stored"].get("layout") or {})
        self.assertEqual(
            second["stored"]["layout"]["table_line_height_mm"], 6.5)
        # The effective renderer value remains populated by the program default.
        self.assertIsInstance(
            second["settings"]["layout"]["table_min_row_mm"], (int, float))

    # ------------------------------------------------------- back-compat
    def test_the_old_top_level_spelling_is_still_readable(self):
        """A profile that has not run the move - or a settings.json restored
        from a backup taken before it - must still load."""
        legacy = {"po_prefix": "ZZL", "table_min_row_mm": 9.75}
        self.path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        importlib.reload(config)
        self.assertEqual(config.PO_PREFIX, "ZZL")
        self.assertAlmostEqual(config.TABLE_MIN_ROW_MM, 9.75, places=3)

    def test_the_section_wins_over_the_legacy_spelling(self):
        """Once moved, the section is the truth - otherwise a stale top-level
        key would quietly override an edit made in the panel."""
        self.path.write_text(json.dumps({
            "po_prefix": "OLD",
            "purchase_order": {"prefix": "NEW"},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        importlib.reload(config)
        self.assertEqual(config.PO_PREFIX, "NEW")

    def test_every_po_field_declares_its_legacy_spelling(self):
        """The move relies on it, and so does the fallback."""
        missing = [".".join(f.path) for f in doc_settings.PO_FIELDS if not f.legacy]
        self.assertEqual(missing, [],
                         f"PO fields with no legacy key cannot be migrated: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
