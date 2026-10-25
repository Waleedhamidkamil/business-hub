"""An LPO IS the win.

Today a quotation only counts as won once it has been invoiced, which can be
weeks after the client actually ordered - so the Decide list nags about
quotations already sitting on a purchase order. Filing the LPO is the earliest
and most accurate moment we know the client said yes.

Isolation: see the note in `client_lpo_save.test.py`.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, client_master, config, database, profile_manager  # noqa: E402

QUOTE = "QUT-MT-HDEC-029"


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


class MarksWon(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM quotes")
        if not client_master.get_record("HDEC").get("record"):
            client_master.save_record({"name": "HDEC"})
        self.bridge = api.Api()

    def file_quote(self, quote_no=QUOTE, bid_status=None):
        database.save_quote_record(
            quote_no=quote_no, quote_date="6/7/2026", quote_type="MT",
            year=2026, month=7, client_name="HDEC", rfq_no="", client_address="",
            pdf_path="q.pdf", rows=[], terms=[])
        if bid_status:
            database.set_bid_status(quote_no, bid_status)

    def status(self, quote_no=QUOTE):
        row = database.load_quote(quote_no)
        return (row or {}).get("bid_status")

    def file_lpo(self, **kw):
        payload = {"client_name": "HDEC", "lpo_no": "CSSP-ADM-0010",
                   "source_quote_no": QUOTE, "total": 18390}
        payload.update(kw)
        return self.bridge.save_lpo(payload)

    def test_filing_an_lpo_wins_the_quotation_it_cites(self):
        self.file_quote()
        self.assertNotEqual("won", self.status(), "precondition: not won yet")
        self.assertTrue(self.file_lpo().get("ok"))
        self.assertEqual("won", self.status(),
                         "the client sent a purchase order - that IS the win")

    def test_an_lpo_citing_nothing_of_ours_touches_no_quotation(self):
        """CSSP-RENT-0002 cites QUT-146, which is HDEC's own reference."""
        self.file_quote()
        self.assertTrue(self.file_lpo(lpo_no="CSSP-RENT-0002",
                                      source_quote_no="").get("ok"))
        self.assertNotEqual("won", self.status())

    def test_a_quotation_we_do_not_hold_is_not_an_error(self):
        """Filing the client's paperwork must not fail over a reference of
        theirs that we were never going to find."""
        self.assertTrue(self.file_lpo(source_quote_no="QUT-146").get("ok"))

    def test_a_quotation_already_marked_lost_is_left_alone(self):
        """If it was marked lost deliberately, an edit to the LPO header must
        not quietly overturn that - the app should not argue with the user."""
        self.file_quote(bid_status="lost")
        self.assertTrue(self.file_lpo().get("ok"))
        self.assertEqual("lost", self.status())

    def test_a_quotation_already_won_stays_won(self):
        self.file_quote(bid_status="won")
        self.assertTrue(self.file_lpo().get("ok"))
        self.assertEqual("won", self.status())

    def test_a_refused_client_wins_nothing(self):
        """The client is not on file, so nothing was filed - and nothing should
        have changed on the quotation either."""
        self.file_quote()
        res = self.bridge.save_lpo({"client_name": "Nobody At All",
                                    "lpo_no": "PO-999", "source_quote_no": QUOTE})
        self.assertFalse(res.get("ok"))
        self.assertNotEqual("won", self.status())

    def test_a_save_that_fails_in_the_store_wins_nothing(self):
        """The case above is refused by the client guard BEFORE the save runs,
        so it cannot tell whether the win is gated on the save succeeding. This
        one gets all the way to `database.save_lpo` and is rejected there.
        """
        self.file_quote()
        res = self.bridge.save_lpo({"client_name": "HDEC", "lpo_no": "   ",
                                    "source_quote_no": QUOTE})
        self.assertFalse(res.get("ok"), "a blank LPO number must be refused")
        self.assertNotEqual("won", self.status(),
                            "nothing was filed, so nothing was won")

    def test_a_broken_quote_store_does_not_lose_the_lpo(self):
        """Filing the client's paperwork is the user's work. Losing it because
        the quotation lookup fell over would be the worse failure."""
        from unittest.mock import patch
        self.file_quote()
        with patch.object(database, "load_quote", side_effect=RuntimeError("gone")):
            res = self.file_lpo()
        self.assertTrue(res.get("ok"), res)
        self.assertIsNotNone(database.load_lpo("HDEC", "CSSP-ADM-0010"))

    def test_editing_an_lpo_still_wins_a_quotation_that_slipped_back(self):
        """The rule is about the LPO existing, not about it being new."""
        self.file_quote()
        self.assertTrue(self.file_lpo().get("ok"))
        database.set_bid_status(QUOTE, "open")
        self.assertTrue(self.file_lpo(total=18400).get("ok"))
        self.assertEqual("won", self.status())


if __name__ == "__main__":
    unittest.main(verbosity=2)
