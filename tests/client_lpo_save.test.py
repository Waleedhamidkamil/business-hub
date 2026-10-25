"""Filing an LPO obeys the same client rule as every other save, and stores the
fields the three kinds of PO need.

"no new clients shall be accepted when typing in client name in all editors"
(Waleed, 2026-09-11). The LPO form was the one save that still took any name -
so an LPO could be filed against a client no document was allowed to name.

!! ISOLATION (learned the hard way on 2026-09-11) !!
`isolated_client_data.activate()` must run BEFORE backend config is imported,
because `config.DB_PATH` is bound at import time. Patching
`profile_manager._MANAGER` alone repoints `shared_db_path()` but NOT the profile
database - an earlier draft of this suite asserted only the shared path, passed,
and wrote eight rows straight into Waleed's live Abraj database.
`_assert_isolated` below checks BOTH paths, which is the assertion that would
have caught it.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, client_master, config, database, profile_manager  # noqa: E402


def _assert_isolated():
    """Both databases must be under a temporary root, not Waleed's data dir.

    The profile check is the one that matters: it is the database documents and
    LPOs are written to, and it is the one that stayed live when only
    `_MANAGER` was patched.
    """
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}. "
            "Stop: this suite would write into the live data.")


_assert_isolated()
database.init_db()


class LpoTestCase(unittest.TestCase):
    """A clean, isolated LPO table for every test.

    `DELETE FROM` is safe here and nowhere else in this repo: the database is a
    temporary file created by `isolated_client_data`, re-asserted every setUp.
    """

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
        self.db = database


class SaveLpoAsksAboutTheClient(unittest.TestCase):
    def test_save_lpo_calls_the_guard_and_acts_on_it(self):
        """Checked in the SOURCE, the way the three document saves are.

        Four other suites patch `_known_client_error` out because they mock the
        whole storage layer, so removing the call could otherwise pass quietly.
        """
        source = (APP_ROOT / "backend" / "api.py").read_text(encoding="utf-8")
        start = source.index("def save_lpo(self")
        end = source.index("\n    def ", start + 10)
        body = source[start:end]
        self.assertIn("_known_client_error(", body,
                      "an LPO must not name a client that is not on file")
        self.assertIn("return refusal", body,
                      "save_lpo asks but does not act on the answer")


class KindIsNormalised(unittest.TestCase):
    def test_an_unknown_kind_falls_back_to_materials(self):
        self.assertEqual("materials", database._lpo_kind("nonsense"))
        self.assertEqual("materials", database._lpo_kind(None))
        self.assertEqual("materials", database._lpo_kind(""))

    def test_the_three_real_kinds_survive_spacing_and_case(self):
        self.assertEqual("rental", database._lpo_kind("RENTAL"))
        self.assertEqual("open", database._lpo_kind(" open "))
        self.assertEqual("materials", database._lpo_kind("Materials"))


class SaveLpoStoresTheNewFields(LpoTestCase):
    def save(self, **kw):
        args = {"client_name": "HDEC", "lpo_no": "CSSP-RENT-0002"}
        args.update(kw)
        res = self.db.save_lpo(**args)
        self.assertTrue(res.get("ok"), res)
        return self.db.load_lpo(args["client_name"], args["lpo_no"])

    def test_a_rental_keeps_its_period_and_monthly_rate(self):
        row = self.save(kind="rental", monthly_amount=850, currency="USD",
                        total=11900, period_start="8/6/2026", period_end="8/1/2027")
        self.assertEqual("rental", row["kind"])
        self.assertEqual(850, row["monthly_amount"])
        self.assertEqual("8/6/2026", row["period_start"])
        self.assertEqual("8/1/2027", row["period_end"])

    def test_the_ordered_lines_round_trip(self):
        import json
        rows = [{"item": "Office desk", "description": "1650*850*800",
                 "unit": "EA", "qty": 60, "rate": "135"}]
        row = self.save(lpo_no="CSSP-ADM-0010", kind="materials", rows=rows)
        self.assertEqual(rows, json.loads(row["rows_json"]))

    def test_no_lines_supplied_stores_null_not_an_empty_list(self):
        """NULL says "we never recorded the order"; [] says "they ordered
        nothing". Reporting the second when the first is true is a lie."""
        row = self.save(lpo_no="CSSP-OPEN-0001", kind="open")
        self.assertIsNone(row["rows_json"])

    def test_an_empty_order_is_stored_as_an_empty_order(self):
        row = self.save(lpo_no="CSSP-OPEN-0002", kind="open", rows=[])
        self.assertEqual("[]", row["rows_json"])

    def test_the_reference_fields_are_kept(self):
        row = self.save(erp_no="ERP-9", project="CSSP",
                        contact="Mr. Sung Hoon Jung")
        self.assertEqual("ERP-9", row["erp_no"])
        self.assertEqual("CSSP", row["project"])
        self.assertEqual("Mr. Sung Hoon Jung", row["contact"])

    def test_an_unparseable_monthly_amount_becomes_nothing_not_zero(self):
        """Zero would read as a free rental. Nothing reads as not recorded."""
        row = self.save(kind="rental", monthly_amount="not a number")
        self.assertIsNone(row["monthly_amount"])

    def test_editing_an_lpo_updates_the_new_fields_too(self):
        self.save(kind="rental", monthly_amount=850, period_end="8/1/2027")
        row = self.save(kind="rental", monthly_amount=900, period_end="8/3/2027")
        self.assertEqual(900, row["monthly_amount"])
        self.assertEqual("8/3/2027", row["period_end"])

    def test_an_lpo_saved_without_a_kind_is_materials(self):
        row = self.save(lpo_no="CSSP-ADM-0017")
        self.assertEqual("materials", row["kind"])

    def test_the_existing_fields_still_work(self):
        row = self.save(lpo_no="CSSP-ADM-0010", total=18390, currency="USD",
                        source_quote_no="QUT-MT-HDEC-029", notes="office furniture")
        self.assertEqual(18390, row["total"])
        self.assertEqual(18390, row["total_usd"])
        self.assertEqual("QUT-MT-HDEC-029", row["source_quote_no"])
        self.assertEqual("office furniture", row["notes"])


class TheGuardActuallyRefuses(LpoTestCase):
    """The source check above proves the call is wired. This proves it BITES."""

    def setUp(self):
        super().setUp()
        self.bridge = api.Api()
        if not client_master.get_record("HDEC").get("record"):
            self.assertTrue(client_master.save_record({"name": "HDEC"})["ok"])

    def test_an_lpo_for_a_client_on_file_is_saved(self):
        res = self.bridge.save_lpo({"client_name": "HDEC", "lpo_no": "CSSP-ADM-0010",
                                    "total": 18390})
        self.assertTrue(res.get("ok"), res)
        self.assertIsNotNone(self.db.load_lpo("HDEC", "CSSP-ADM-0010"))

    def test_an_lpo_for_a_stranger_is_refused_and_nothing_is_filed(self):
        res = self.bridge.save_lpo({"client_name": "Nobody At All",
                                    "lpo_no": "PO-999", "total": 10})
        self.assertFalse(res.get("ok"), "an off-list client must not reach the store")
        self.assertEqual("Nobody At All", res.get("unknown_client"),
                         "the editor keys its quick-add dialog on this field")
        self.assertIsNone(self.db.load_lpo("Nobody At All", "PO-999"),
                          "the refusal must leave no row behind")

    def test_any_spelling_of_a_client_on_file_is_accepted(self):
        """An alias IS the client. The update passes the stored record back:
        `save_record` without an id is a NEW client and collides on the name,
        which is what makes a correctly stored alias look unregistered."""
        stored = client_master.get_record("HDEC")["record"]
        updated = client_master.save_record({**stored, "aliases": ["Hyundai Engineering"]})
        self.assertTrue(updated["ok"], updated)
        self.assertEqual("HDEC", client_master.canonical("Hyundai Engineering"))
        res = self.bridge.save_lpo({"client_name": "Hyundai Engineering",
                                    "lpo_no": "CSSP-ADM-0011"})
        self.assertTrue(res.get("ok"), res)

    def test_an_lpo_already_on_file_keeps_its_own_name(self):
        """Locking the list must never make an existing record uneditable - the
        same grandfathering a saved document gets."""
        self.assertTrue(self.db.save_lpo(client_name="Legacy Client",
                                         lpo_no="OLD-001")["ok"])
        res = self.bridge.save_lpo({"client_name": "Legacy Client",
                                    "lpo_no": "OLD-001", "total": 5})
        self.assertTrue(res.get("ok"),
                        "an LPO filed before the rule existed must stay editable")

    def test_a_save_survives_an_unreadable_lpo_store(self):
        """The grandfathering lookup must not be able to kill the save.

        It sits OUTSIDE save_lpo's try block, so an exception from it escapes
        the bridge entirely rather than coming back as an error the UI can show.
        """
        from unittest.mock import patch
        with patch.object(database, "load_lpo", side_effect=RuntimeError("db is gone")):
            res = self.bridge.save_lpo({"client_name": "HDEC",
                                        "lpo_no": "CSSP-ADM-0012"})
        self.assertIsInstance(res, dict,
                              "a failed lookup must not raise out of the bridge")


if __name__ == "__main__":
    unittest.main(verbosity=2)
