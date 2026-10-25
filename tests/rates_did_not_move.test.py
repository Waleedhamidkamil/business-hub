"""Deleting the two hidden rate rules must not move one historical figure.

`doc_iqd_per_usd` used to answer with "Eshraqat is always 1500" and "a deal paid
in cash is 1500". Both are gone; the rate now comes from the account the client
is paid into, stamped on the document when it was saved. 171 Abraj documents and
16 Eshraqat ones were stamped by `2026-09-21_stamp_document_cost_rates` at the
rate the OLD rules gave them, and 52 of the Abraj ones were only at 1500 BECAUSE
of the cash rule - they are exactly the documents that would move.

`scratchpad/rates-before.json` was taken while the old rules were live. This
compares every document's rate and profit against it.

If this file fails, the migration missed documents. Do NOT "fix" it by
regenerating the snapshot: the snapshot is the evidence.
"""
import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
BEFORE = APP_ROOT / "scratchpad" / "rates-before.json"

from backend.profile_manager import manager as pm  # noqa: E402


@unittest.skipUnless(
    BEFORE.exists(),
    f"NOTHING WAS CHECKED - {BEFORE.name} is missing. It is taken once, before "
    "the rate rules change, by scratchpad/rate_snapshot.py. This is neither a "
    "pass nor a failure of the code under test.")
class NothingMoved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pm().bootstrap_profiles(legacy_db=APP_ROOT / "data" / "po_tool.db",
                                legacy_settings=APP_ROOT / "data" / "settings.json",
                                legacy_assets=APP_ROOT / "static")
        cls.before = json.loads(BEFORE.read_text(encoding="utf-8"))
        cls.start = pm().active_profile_id()

    @classmethod
    def tearDownClass(cls):
        # Restore in a classmethod teardown, not a trailing statement: a crash
        # mid-test must not leave the registry pointing at another profile.
        pm().activate(cls.start)

    def test_every_document_reports_the_same_rate(self):
        import importlib
        moved = []
        checked = 0
        for pid, rows in self.before.items():
            pm().activate(pid)
            from backend import config, database
            importlib.reload(config)
            importlib.reload(database)
            for ref, was in rows.items():
                kind, no = ref.split(":", 1)
                now = database.doc_iqd_per_usd(kind, no)
                checked += 1
                if abs(float(was["rate"]) - float(now)) > 1e-9:
                    moved.append(f"{pid} {ref}: rate {was['rate']} -> {now}")
        self.assertGreater(checked, 100,
                           "the snapshot should cover every document; it covered "
                           f"only {checked}")
        self.assertEqual([], moved,
                         f"{len(moved)} document(s) changed rate when the rules "
                         "did:\n  " + "\n  ".join(moved[:40]))

    def test_every_document_reports_the_same_profit(self):
        import importlib
        moved = []
        for pid, rows in self.before.items():
            pm().activate(pid)
            from backend import config, database
            importlib.reload(config)
            importlib.reload(database)
            for ref, was in rows.items():
                if was.get("profit") is None:
                    continue
                kind, no = ref.split(":", 1)
                try:
                    totals = (database.load_doc_costing(kind, no) or {}).get("totals") or {}
                except Exception:
                    continue
                now = totals.get("profit")
                if now is None:
                    continue
                # 2026-10-21 (Waleed): a line with no cost is now costed at a
                # 40% markup estimate (config.UNCOSTED_MARKUP). The snapshot was
                # taken when it cost zero, and this test is about RATES, so the
                # estimate is added back - it must not hide or fake a rate move.
                est = float(totals.get("estimated_cost") or 0.0)
                now = float(now) + est
                # profit and the estimate are each rounded to the cent, so the
                # sum may be a cent out when an estimate is in it
                tol = 0.015 if est else 0.005
                if abs(float(was["profit"]) - float(now)) > tol:
                    moved.append(f"{pid} {ref}: profit {was['profit']} -> {now}")
        self.assertEqual([], moved,
                         f"{len(moved)} document(s) changed profit when the rate "
                         "rules did:\n  " + "\n  ".join(moved[:40]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
