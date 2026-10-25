"""A contract ends when the client says it ends.

CSSP-RENT-0002 carries: "The contract term is seven months; however, please note
that it may be terminated earlier if necessary." Nothing the app can compute
tells it that happened, so there has to be a way to say so - and a way to undo
saying it.

Isolation: `isolated_client_data.activate()` runs before backend config is
imported, because `config.DB_PATH` is bound at import time. `_assert_isolated`
checks the profile database as well as the shared one; asserting only the shared
path is what let an earlier suite write into the live Abraj database on
2026-09-11.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, profile_manager  # noqa: E402

CLIENT = "HDEC"
RENTAL = "CSSP-RENT-0002"


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}. "
            "Stop: this suite would write into the live data.")


_assert_isolated()
database.init_db()


class CloseLpo(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
        self.assertTrue(database.save_lpo(
            client_name=CLIENT, lpo_no=RENTAL, kind="rental",
            total=11900, currency="USD", monthly_amount=850,
            period_start="8/6/2026", period_end="8/1/2027")["ok"])

    def current(self):
        return database.load_lpo(CLIENT, RENTAL)

    def test_a_running_rental_is_not_closed_until_it_is_closed(self):
        self.assertIsNone(self.current()["closed_at"])

    def test_closing_records_when_and_why(self):
        res = database.close_lpo(CLIENT, RENTAL, True, "terminated early")
        self.assertTrue(res["ok"])
        row = self.current()
        self.assertTrue(row["closed_at"], "a close must record when")
        self.assertEqual("terminated early", row["close_reason"])

    def test_a_close_with_no_reason_is_allowed(self):
        """Not every ending has an explanation worth typing."""
        self.assertTrue(database.close_lpo(CLIENT, RENTAL, True)["ok"])
        row = self.current()
        self.assertTrue(row["closed_at"])
        self.assertIsNone(row["close_reason"])

    def test_a_closed_contract_reads_as_closed(self):
        database.close_lpo(CLIENT, RENTAL, True, "terminated early")
        row = [r for r in database.list_lpos(CLIENT) if r["lpo_no"] == RENTAL][0]
        self.assertEqual("closed", row["status"])

    def test_reopening_clears_both(self):
        database.close_lpo(CLIENT, RENTAL, True, "terminated early")
        database.close_lpo(CLIENT, RENTAL, False)
        row = self.current()
        self.assertIsNone(row["closed_at"])
        self.assertIsNone(row["close_reason"],
                          "a reopened contract must not keep the reason it ended")

    def test_a_reason_passed_while_reopening_is_ignored(self):
        """Pins the rule rather than the default.

        Reopening with no reason clears `close_reason` whether the code checks
        `closed` or not, so the test above cannot tell the two apart - it passed
        against a mutation that dropped the check entirely. A reason supplied on
        the way OPEN is the case that separates them: a running contract must
        never carry an explanation for an ending.
        """
        database.close_lpo(CLIENT, RENTAL, True, "terminated early")
        database.close_lpo(CLIENT, RENTAL, False, "changed my mind")
        row = self.current()
        self.assertIsNone(row["closed_at"])
        self.assertIsNone(row["close_reason"],
                          "a running contract must carry no closing reason")

    def test_editing_the_header_does_not_reopen_a_closed_contract(self):
        """save_lpo owns the header; closed_at belongs to close_lpo alone - the
        same split file_path and ack_path already have."""
        database.close_lpo(CLIENT, RENTAL, True, "terminated early")
        self.assertTrue(database.save_lpo(
            client_name=CLIENT, lpo_no=RENTAL, kind="rental",
            total=12000, currency="USD")["ok"])
        row = self.current()
        self.assertTrue(row["closed_at"], "editing the total reopened the contract")
        self.assertEqual("terminated early", row["close_reason"])
        self.assertEqual(12000, row["total"], "the edit itself must still land")

    def test_closing_an_lpo_that_is_not_there_says_so(self):
        res = database.close_lpo(CLIENT, "NOPE-0001", True)
        self.assertFalse(res["ok"])
        self.assertIn("NOPE-0001", res["error"])

    def test_the_bridge_exposes_it(self):
        """The screen's Close button calls this, so it has to be on the bridge."""
        bridge = api.Api()
        self.assertTrue(bridge.close_lpo(CLIENT, RENTAL, True, "terminated early")["ok"])
        self.assertTrue(self.current()["closed_at"])
        self.assertTrue(bridge.close_lpo(CLIENT, RENTAL, False)["ok"])
        self.assertIsNone(self.current()["closed_at"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
