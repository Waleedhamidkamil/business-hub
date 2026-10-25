"""An LPO and its documents must meet even when the client was typed differently.

`client_aliases` is the single index that says HDEC and Hyundai Engineering are
one client - the editors, the numbering and the printed name all resolve through
it. The LPO rollups were the last place still comparing raw strings, so an LPO
filed under one spelling and an invoice saved under another never met, and the
delivered / invoiced / closed ladder stayed dark.

Pure function over strings. Touches no database of its own; `client_master` is
patched so the test does not depend on what the live library happens to hold.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import database


def one_client(name, identities=None):
    """Stand-in alias index: every spelling of HDEC resolves to HDEC.

    Takes `identities` like the real `client_master.canonical` does: since
    2026-10-01 `_usage_key` passes the alias map it was handed, and a stand-in
    with the old one-argument signature raises TypeError, which `_usage_key`
    swallows by design - so the test failed on the double, not on the code."""
    return "HDEC" if "hyundai" in name.lower() or name.strip().upper() == "HDEC" else name


class UsageKey(unittest.TestCase):
    def test_two_spellings_of_one_client_share_a_key(self):
        with patch("backend.client_master.canonical", side_effect=one_client):
            a = database._usage_key("HDEC", "CSSP-ADM-0010")
            b = database._usage_key("Hyundai Engineering", "CSSP-ADM-0010")
        self.assertEqual(a, b, "the alias index says these are the same client")

    def test_the_lpo_number_is_still_matched_case_insensitively(self):
        with patch("backend.client_master.canonical", side_effect=one_client):
            self.assertEqual(database._usage_key("HDEC", "cssp-adm-0010"),
                             database._usage_key("HDEC", "CSSP-ADM-0010"))

    def test_surrounding_space_does_not_split_a_match(self):
        with patch("backend.client_master.canonical", side_effect=one_client):
            self.assertEqual(database._usage_key("HDEC", "CSSP-ADM-0010"),
                             database._usage_key("  HDEC  ", " CSSP-ADM-0010 "))

    def test_two_clients_sending_the_same_number_stay_apart(self):
        """PK is (client, number) because the number is the CLIENT's sequence."""
        with patch("backend.client_master.canonical", side_effect=lambda n, identities=None: n):
            self.assertNotEqual(database._usage_key("TDC", "PO-001"),
                                database._usage_key("HDEC", "PO-001"))

    def test_an_unreadable_client_store_falls_back_to_the_spelling(self):
        """A broken index must degrade to the old behaviour, never raise -
        `list_lpos` runs on every visit to the screen, and losing the screen is
        worse than losing an alias match."""
        with patch("backend.client_master.canonical",
                   side_effect=RuntimeError("db is gone")):
            self.assertEqual(("hdec", "cssp-adm-0010"),
                             database._usage_key("HDEC", "CSSP-ADM-0010"))

    def test_a_client_the_index_does_not_know_keeps_its_own_name(self):
        """canonical() returning nothing must not collapse every stranger
        into one shared empty key."""
        with patch("backend.client_master.canonical", return_value=""):
            self.assertEqual(("someone else", "po-001"),
                             database._usage_key("Someone Else", "PO-001"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
