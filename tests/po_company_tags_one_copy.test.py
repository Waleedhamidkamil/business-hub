# -*- coding: utf-8 -*-
"""The PO company classification has ONE copy (2026-10-21).

Waleed: typing a wrong company ("v") on a PO line asked "client or our
company?"; he cancelled, typed TDC, saved - and the To-Do then listed every PO
whose lines were for ابراج البدر (our own company) as "to be invoiced".

Root cause, read off his real settings.json: two copies of the map. PO Settings
saves `purchase_order.company_tags` (ابراج البدر = internal), and config reads
that. The prompt's save, `set_po_company_tag`, read the OLD top-level
`po_company_tags` (ابراج البدر = client), added "v", saved it, and REPLACED the
in-memory map with it. And cancel meant "client", so every typo (j, k, 1, ت,
ف, jk, v) had been filed as a client company.

Runs on a scratch settings.json - the real one is never touched.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import api as api_mod, config, database  # noqa: E402

OURS = "ابراج البدر"


class OneCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "settings.json"
        # his file as it was: section says internal, the stale top-level says client
        self.tmp.write_text(json.dumps({
            "purchase_order": {"company_tags": {OURS: "internal", "TDC": "client"}},
            "po_company_tags": {OURS: "client", "TDC": "client"},
        }, ensure_ascii=False), encoding="utf-8")
        p = mock.patch.object(config, "SETTINGS_PATH", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        saved = dict(config.PO_COMPANY_TAGS)
        self.addCleanup(lambda: setattr(config, "PO_COMPANY_TAGS", saved))
        config.PO_COMPANY_TAGS = {OURS: "internal", "TDC": "client"}

    def stored(self):
        return json.loads(self.tmp.read_text(encoding="utf-8"))

    def test_answering_the_prompt_keeps_our_company_internal(self):
        out = api_mod.Api().set_po_company_tag("v", "client")
        self.assertTrue(out["ok"])
        self.assertTrue(config.po_company_is_internal(OURS),
                        "classifying a new name turned our own company into a client")
        self.assertEqual(self.stored()["purchase_order"]["company_tags"],
                         {OURS: "internal", "TDC": "client", "v": "client"})

    def test_there_is_one_copy_after_a_save(self):
        api_mod.Api().set_po_company_tag("JUNE", "client")
        self.assertNotIn("po_company_tags", self.stored(),
                         "the old top-level copy must go, or the two can disagree again")

    def test_the_editor_and_settings_read_the_same_map(self):
        self.assertEqual(api_mod.Api().get_po_company_tags(), {OURS: "internal", "TDC": "client"},
                         "the PO editor reads a different map from PO Settings")

    def test_a_profile_with_only_the_old_copy_still_reads_it(self):
        self.tmp.write_text(json.dumps({"po_company_tags": {OURS: "internal"}}, ensure_ascii=False),
                            encoding="utf-8")
        self.assertEqual(database.read_po_company_tags(config.load_user_settings()), {OURS: "internal"})


class CancelSavesNothing(unittest.TestCase):
    def test_cancel_is_no_answer(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        prompt = js[js.index("function promptCompanyTag"):]
        prompt = prompt[:prompt.index("\n}\n")]
        self.assertIn('v === "internal" || v === "client" ? v : null', prompt,
                      "dismissing the prompt must not come back as \"client\"")
        queue = js[js.index("async function queueCompanyTagPrompt"):js.index("function promptCompanyTag")]
        self.assertLess(queue.index("if (!tag) return;"), queue.index("set_po_company_tag"),
                        "a cancelled prompt must return before anything is saved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
