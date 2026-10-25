"""One place hands out document numbers (multi-user, 2026-10-22).

The check ("is this number free?") and the take ("it is mine") happen in ONE
BEGIN IMMEDIATE transaction, which SQLite serialises across processes - so two
engines can never leave with the same number. A claim is 'held' while its save
runs and 'used' after; a held claim older than 10 minutes (an engine that died
mid-save) is forgotten so its number can be reused without a gap.

Runs on a scratch database - never the live one.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config, number_claims, sqlite_policy  # noqa: E402


def fmt(n):
    return f"INV-MT-TDC-{n:03d}"


class NumberClaims(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "t.db"
        self.patch = patch.object(config, "DB_PATH", self.db)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.td.cleanup()

    def test_next_goes_past_every_live_claim(self):
        a = number_claims.claim_next("inv", 124, fmt, holder="w")
        b = number_claims.claim_next("inv", 124, fmt, holder="a")
        self.assertEqual((a, b), ("INV-MT-TDC-124", "INV-MT-TDC-125"))

    def test_next_never_goes_below_what_the_files_say(self):
        number_claims.claim_next("inv", 5, fmt, holder="w")
        self.assertEqual(number_claims.claim_next("inv", 300, fmt, holder="w"), fmt(300))

    def test_kinds_do_not_share_numbers(self):
        number_claims.claim_next("inv", 124, fmt, holder="w")
        self.assertEqual(number_claims.claim_next("quote", 124, lambda n: f"Q-{n}", holder="w"), "Q-124")

    def test_a_typed_number_held_by_someone_else_is_refused(self):
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")
        with self.assertRaises(number_claims.NumberTaken) as cm:
            number_claims.claim("inv", "INV-MT-TDC-124", holder="a")
        self.assertEqual(cm.exception.holder, "w")
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")   # same holder: fine

    def test_a_number_someone_else_already_saved_is_refused(self):
        # Ahmed's editor showed -124; Waleed saved -124 a second ago. Ahmed's
        # save must NOT overwrite Waleed's invoice - he gets "taken" instead.
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")
        number_claims.confirm("inv", "INV-MT-TDC-124")
        with self.assertRaises(number_claims.NumberTaken):
            number_claims.claim("inv", "INV-MT-TDC-124", holder="a")
        # ...while Waleed re-saving his own number stays allowed, as today.
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")

    def test_a_typed_number_counts_for_the_next_one(self):
        # The editor proposed -124 and is saving it; a save with no number must
        # not be handed -124 as well (it would collide on the same row).
        number_claims.claim("inv", "INV-MT-TDC-124", holder="w")
        self.assertEqual(number_claims.claim_next("inv", 124, fmt, holder="a"), fmt(125))

    def test_the_displayed_next_number_counts_claims(self):
        # A save without a PDF leaves no file for "newest PDF + 1" to see; the
        # number the editor SHOWS must still move past it.
        self.assertEqual(number_claims.peek_next("inv", 124), 124)      # no table yet: no error
        number_claims.confirm("inv", number_claims.claim_next("inv", 124, fmt, holder="w"))
        self.assertEqual(number_claims.peek_next("inv", 124), 125)
        self.assertEqual(number_claims.peek_next("inv", 500), 500)

    def test_release_frees_the_number_without_a_gap(self):
        n = number_claims.claim_next("inv", 124, fmt, holder="w")
        number_claims.release("inv", n, holder="w")
        self.assertEqual(number_claims.claim_next("inv", 124, fmt, holder="a"), n)

    def test_release_cannot_free_someone_elses_number(self):
        n = number_claims.claim_next("inv", 124, fmt, holder="w")
        number_claims.release("inv", n, holder="a")
        self.assertEqual(number_claims.claim_next("inv", 124, fmt, holder="a"), fmt(125))

    def test_a_dead_holders_claim_expires(self):
        n = number_claims.claim_next("inv", 124, fmt, holder="w")
        old = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
        conn = sqlite_policy.open_db(config.DB_PATH)
        try:
            conn.execute("UPDATE number_claims SET claimed_at=?", (old,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(number_claims.claim_next("inv", 124, fmt, holder="a"), n)

    def test_used_numbers_are_never_handed_out_again(self):
        n = number_claims.claim_next("inv", 124, fmt, holder="w")
        number_claims.confirm("inv", n)
        old = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
        conn = sqlite_policy.open_db(config.DB_PATH)
        try:
            conn.execute("UPDATE number_claims SET claimed_at=?", (old,))   # used never expires
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(number_claims.claim_next("inv", 124, fmt, holder="a"), fmt(125))


if __name__ == "__main__":
    unittest.main(verbosity=2)
