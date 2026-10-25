"""Every spacing knob the settings UI offers must actually reach the renderer.

Audited 2026-07-30. Of the five controls each editor showed, two were phantoms:
`max_items` and `budget_mm` were whitelisted by config, written to settings.json
and echoed back on reload - but no generator ever read them. Meanwhile
`max_row_mm` and `image_row_min_mm` WERE read by the generators and were being
silently dropped by that same whitelist, so they could never be set. The
per-sub-type Max-items / Min-row boxes were dead for a third reason: nothing
reads a sub-type `layout` at all.

Two independent things have to line up for a knob to work, and this file pins
both so the two halves cannot drift apart again:

  A. config._apply_user_settings' `_layout_dict` whitelist passes the key
     through from settings.json into config.<TYPE>_LAYOUT.
  B. the generator reads it - proved by reloading the module with the key set
     and watching a module constant move.

Writes no files: `config.load_user_settings` is monkeypatched, which is exactly
the path a settings save takes, minus the disk.
"""
import importlib
import re
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

# This module reads profile-dependent constants, so it is meaningless under the
# wrong profile - and it used to FAIL there, cryptically, which is how the board
# ended up warning readers not to believe it instead of fixing it (2026-09-05).
# Say so plainly instead. See tests/profile_guard.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_guard import require_profile  # noqa: E402

require_profile("abraj")


from backend import config  # noqa: E402

# doc type -> (config LAYOUT attr, settings.json section, generator module)
DOCS = {
    "quotation": ("QUOTE_LAYOUT", "backend.quote_generator"),
    "delivery":  ("DN_LAYOUT",    "backend.dn_generator"),
    "invoice":   ("INV_LAYOUT",   "backend.inv_generator"),
}
# Knobs offered only on one panel (see doc_settings.js).
QUOTATION_ONLY = {"image_row_min_mm"}

_real_loader = config.load_user_settings


def ui_layout_keys() -> set[str]:
    """The numeric layout keys the settings panel collects, read out of the JS
    so this test tracks the real UI instead of a copy of it."""
    js = (APP / "static" / "doc_settings.js").read_text(encoding="utf-8")
    body = js[js.index("const intIfSet"):]
    return set(re.findall(r'intIfSet\(\s*"#\w+"\s*,\s*"(\w+)"', body))


def whitelist_passes(section: str, attr: str, key: str) -> bool:
    config.load_user_settings = lambda: {section: {"layout": {key: 33.0}}}
    try:
        config._apply_user_settings()
        return key in getattr(config, attr)
    finally:
        config.load_user_settings = _real_loader
        config._apply_user_settings()


def generator_reads(mod_name: str, attr: str, key: str) -> bool:
    mod = importlib.import_module(mod_name)
    importlib.reload(mod)
    before = {k: v for k, v in vars(mod).items() if isinstance(v, float) and k.isupper()}
    setattr(config, attr, {key: 33.0})
    try:
        importlib.reload(mod)
        after = {k: v for k, v in vars(mod).items() if isinstance(v, float) and k.isupper()}
    finally:
        setattr(config, attr, {})
        importlib.reload(mod)
    return any(before.get(k) != after.get(k) for k in before)


class EveryOfferedKnobWorks(unittest.TestCase):
    def test_ui_offers_the_expected_set(self):
        """Fails loudly if someone adds a knob to the panel without adding it
        here - which is how the phantom controls survived for so long."""
        self.assertEqual(
            ui_layout_keys(),
            {"min_row_mm", "max_row_mm", "image_row_min_mm", "safe_gap_mm"})

    def test_every_offered_knob_reaches_the_renderer(self):
        broken = []
        for key in sorted(ui_layout_keys()):
            for section, (attr, mod) in DOCS.items():
                if key in QUOTATION_ONLY and section != "quotation":
                    continue
                if not whitelist_passes(section, attr, key):
                    broken.append(f"{section}.{key}: blocked by config's whitelist")
                elif not generator_reads(mod, attr, key):
                    broken.append(f"{section}.{key}: no generator constant changes")
        self.assertEqual(broken, [], "phantom spacing knobs:\n  " + "\n  ".join(broken))

    def test_autofit_still_works_everywhere(self):
        for attr, mod, fn in (("QUOTE_LAYOUT", "backend.quote_generator", "_q_autofit"),
                              ("DN_LAYOUT", "backend.dn_generator", "_dn_autofit"),
                              ("INV_LAYOUT", "backend.inv_generator", "_inv_autofit")):
            m = importlib.import_module(mod)
            f = getattr(m, fn)
            setattr(config, attr, {})
            self.assertFalse(f(None), f"{fn} should default off")
            setattr(config, attr, {"autofit_rows": True})
            self.assertTrue(f(None), f"{fn} should honour the setting")
            setattr(config, attr, {})


class RetiredKnobsAreNotOffered(unittest.TestCase):
    def test_dead_controls_are_gone_from_the_panel(self):
        """`max_items` (fights content-aware pagination) and `budget_mm` (the
        page-role engine derives the budget per role) can't be honoured as
        written, so the UI must not pretend otherwise."""
        js = (APP / "static" / "doc_settings.js").read_text(encoding="utf-8")
        for dead in ('id="dsMaxItems"', 'id="dsBudget"', 'data-sub-layout'):
            self.assertNotIn(dead, js, f"{dead} is a phantom control")

    def test_config_still_tolerates_them_in_an_old_settings_file(self):
        """Existing settings.json files carry the retired keys; parsing must not
        blow up, it just must not be surfaced as a control any more."""
        config.load_user_settings = lambda: {
            "quotation": {"layout": {"max_items": 20, "budget_mm": 199,
                                     "min_row_mm": 11}}}
        try:
            config._apply_user_settings()
            self.assertEqual(config.QUOTE_LAYOUT.get("min_row_mm"), 11.0)
        finally:
            config.load_user_settings = _real_loader
            config._apply_user_settings()


class ImageRowHeightIsHonouredEndToEnd(unittest.TestCase):
    """The knob has to move BOTH the reserved page space and the CSS cap.

    It previously would have moved only the first: quote_styles.css hard-capped
    .cell-image at 22mm, so raising the setting bought page space the picture
    never grew into.
    """

    def setUp(self):
        self.qg = importlib.import_module("backend.quote_generator")

    def tearDown(self):
        config.QUOTE_LAYOUT = {}
        importlib.reload(self.qg)

    def _render(self, value):
        config.QUOTE_LAYOUT = {"image_row_min_mm": value} if value else {}
        importlib.reload(self.qg)
        rows = [{"item": f"I{i}", "description": "d", "unit": "EA", "qty": "1",
                 "rate": "2", "image": "data:image/jpeg;base64,AAAA"}
                for i in range(40)]
        pages = self.qg.build_layout(rows, [], self.qg._schema_for("materials"))
        html = self.qg.render_quote_html({
            "quote_no": "QUT-IMGROW-TEST", "date": "30/7/2026",
            "quote_type": "materials", "client_name": "T",
            "items": rows, "terms": [],
        })
        return len(pages), html

    def test_css_cap_follows_the_setting(self):
        for value, expect in ((15, "--q-img-mm: 15"), (35, "--q-img-mm: 35")):
            _, html = self._render(value)
            self.assertIn(expect, html,
                          f"CSS cap did not follow image_row_min_mm={value}")

    def test_page_count_follows_the_setting(self):
        tight, _ = self._render(15)
        default, _ = self._render(None)
        loose, _ = self._render(35)
        self.assertLess(tight, default, "a smaller image row should fit more per page")
        self.assertGreater(loose, default, "a larger image row should fit fewer per page")

    def test_default_is_unchanged(self):
        """22mm is the historical hardcoded value - the default render must be
        byte-identical in this respect so existing quotes don't reflow."""
        _, html = self._render(None)
        self.assertIn("--q-img-mm: 22", html)
        self.assertIn("--q-img-autofit-mm: 24", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
