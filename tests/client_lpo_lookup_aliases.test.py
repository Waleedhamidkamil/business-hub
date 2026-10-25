"""Looking something up BY CLIENT must find every spelling of that client.

Reported by Waleed on 2026-09-19: an LPO filed under `HDEC`, then an invoice for
`Hyundai Engineering and Construction Co., Ltd` - and the LPO picker answered
"Hyundai Engineering ... has no open LPOs on file". Measured on his live
Eshraqat data before the fix:

    list_lpos('HDEC')                                     -> 1 row
    list_lpos('Hyundai Engineering and Construction ...') -> 0 rows
    list_quote_numbers('HDEC')                            -> 5
    list_quote_numbers('Hyundai Engineering and ...')     -> 4

One client, two answers, decided by which spelling the document happened to
carry. `client_aliases` resolves all of them to the same identity, and the app
already knows this in two places - `clients.client_profile` matches on
`client_key(client_name) IN (alias keys)`, and `database._usage_key` resolves
the rollup through `client_master.canonical`. `client_lpo_usage_match` even
claims the rollups were "the last place still comparing raw strings". They were
not: the QUERIES still were, so the rollup had nothing to roll up.

Why the fix is at read time and not by rewriting what is stored: renaming a
client deliberately touches no saved document (`client_rename_isolation_backend`
pins that), and documents legitimately carry different spellings because the
legal name is what some of them print. The stored name is history; identity is
resolved when it is read.

Isolated - `isolated_client_data.activate()` before backend config is imported,
for the reason `client_lpo_save.test.py` documents at length.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import client_master, config, database, profile_manager  # noqa: E402


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}. "
            "Stop: this suite would write into the live data.")


_assert_isolated()
database.init_db()

SHORT = "HDEC"
LEGAL = "Hyundai Engineering and Construction Co., Ltd"
AMPER = "Hyundai Engineering & Construction"
ARABIC = "هيونداي"
OTHER = "TDC"


class ClientNameCase(unittest.TestCase):
    """One client with four spellings, one unrelated client, a clean profile."""

    @classmethod
    def setUpClass(cls):
        _assert_isolated()
        # `client_master._init` imports the legacy autocomplete names into
        # `client_records` the first time it opens the store, so these may
        # already exist as bare records - fill them in rather than collide.
        cls.put(name=SHORT, legal_name=LEGAL, name_ar=ARABIC, aliases=[AMPER])
        cls.put(name=OTHER, legal_name="Turkish Development Co.", aliases=[])
        keys = client_master.alias_keys_for(LEGAL)
        assert client_master.normalize(SHORT) in keys, (
            "the fixture never built the identity these tests are about, so "
            f"they would prove nothing; alias keys were {keys}")

    @classmethod
    def put(cls, **fields):
        found = client_master.get_record(fields["name"]) or {}
        existing = found.get("record") or {}
        payload = {"contacts": [], "addresses": [], "defaults": {}, **fields}
        if existing.get("id"):
            payload["id"] = existing["id"]
            payload["updated_at"] = existing.get("updated_at")
        res = client_master.save_record(payload)
        assert res.get("ok"), res

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM quotes")

    def file_lpo(self, client=SHORT, no="CSSP-ADM-0010", **kw):
        args = {"client_name": client, "lpo_no": no, "total": 18390,
                "currency": "USD", "lpo_date": "9/7/2026"}
        args.update(kw)
        res = database.save_lpo(**args)
        self.assertTrue(res.get("ok"), res)

    def file_quote(self, client=SHORT, no="QUT-MT-HDEC-041"):
        """A saved quotation. Only a PDF-bearing row is a link candidate."""
        with database._connect() as conn:
            conn.execute(
                "INSERT INTO quotes(quote_no, quote_date, quote_type, year, month, "
                "client_name, pdf_path, rows_json, bid_status, reference, "
                "updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (no, "9/7/2026", "materials", 2026, 7, client,
                 r"D:\docs\%s.pdf" % no, "[]", "", "",
                 "2026-09-19T10:00:00", "2026-09-19T10:00:00"))


class TheLpoListFindsTheClient(ClientNameCase):
    def test_the_legal_name_finds_an_lpo_filed_under_the_short_name(self):
        """The exact bug he reported."""
        self.file_lpo(client=SHORT)
        rows = database.list_lpos(LEGAL)
        self.assertEqual(1, len(rows),
                         f"{LEGAL} and {SHORT} are one client; the invoice "
                         "carries the legal name and the LPO the short one")
        self.assertEqual("CSSP-ADM-0010", rows[0]["lpo_no"])

    def test_every_spelling_on_the_record_finds_it(self):
        self.file_lpo(client=SHORT)
        for spelling in (SHORT, LEGAL, AMPER, ARABIC, " hdec ", "hdec"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    1, len(database.list_lpos(spelling)),
                    f"{spelling!r} is on the identity, so it must find the LPO")

    def test_the_short_name_finds_an_lpo_filed_under_the_legal_name(self):
        """It has to work in both directions, or filing order decides."""
        self.file_lpo(client=LEGAL)
        self.assertEqual(1, len(database.list_lpos(SHORT)))

    def test_another_client_gets_nothing(self):
        """The fix must not simply widen every query to everything."""
        self.file_lpo(client=SHORT)
        self.assertEqual([], database.list_lpos(OTHER),
                         "TDC has no LPOs; matching on identity must not turn "
                         "into matching on anything")

    def test_a_client_who_is_not_on_file_still_matches_its_own_spelling(self):
        """No client record, no alias index - the literal name must still work,
        or an LPO filed before the client existed becomes unreachable."""
        self.file_lpo(client="Nobody Ltd")
        self.assertEqual(1, len(database.list_lpos("Nobody Ltd")))
        self.assertEqual([], database.list_lpos(SHORT))

    def test_no_client_still_lists_everything(self):
        self.file_lpo(client=SHORT)
        self.file_lpo(client=OTHER, no="TDC-0001")
        self.assertEqual(2, len(database.list_lpos("")))


class OpeningOneLpoFindsIt(ClientNameCase):
    def test_load_lpo_resolves_the_spelling(self):
        self.file_lpo(client=SHORT)
        row = database.load_lpo(LEGAL, "CSSP-ADM-0010")
        self.assertIsNotNone(
            row, "opening, attaching and signing all identify an LPO this way")
        self.assertEqual(SHORT, row["client_name"],
                         "and it comes back under the spelling it was FILED "
                         "with - the stored name is history, not a guess")

    def test_load_lpo_does_not_cross_clients(self):
        self.file_lpo(client=SHORT)
        self.assertIsNone(database.load_lpo(OTHER, "CSSP-ADM-0010"))

    def test_suggest_lpos_resolves_the_spelling(self):
        self.file_lpo(client=SHORT)
        self.assertEqual(1, len(database.suggest_lpos(LEGAL)),
                         "the type-ahead is the same question as the picker")


class FilingTheSameNumberTwice(ClientNameCase):
    def test_a_second_spelling_updates_the_LPO_rather_than_forking_it(self):
        """`client_lpos` is keyed on (client_name, lpo_no), so two spellings
        used to mean two rows for one purchase order - and after this fix the
        picker would show the same number twice."""
        self.file_lpo(client=SHORT, total=18390)
        self.file_lpo(client=LEGAL, total=19000)
        rows = database.list_lpos(SHORT)
        self.assertEqual(1, len(rows),
                         "one client, one LPO number, one record")
        self.assertEqual(SHORT, rows[0]["client_name"],
                         "the spelling it was first filed under is kept")
        self.assertEqual(19000, rows[0]["total"], "and the edit landed")


class LinkingDocumentsFindsTheClient(ClientNameCase):
    def test_quote_numbers_come_back_under_either_spelling(self):
        self.file_quote(client=SHORT)
        self.assertEqual(1, len(database.list_quote_numbers(LEGAL)),
                         "an invoice saved under the legal name could not see "
                         "the quotations saved under the short one")
        self.assertEqual(1, len(database.list_quote_numbers(SHORT)))

    def test_quote_numbers_do_not_cross_clients(self):
        self.file_quote(client=SHORT)
        self.assertEqual([], database.list_quote_numbers(OTHER))

    def test_link_candidates_come_back_under_either_spelling(self):
        self.file_quote(client=SHORT)
        self.assertEqual(1, len(database.list_link_candidates("quote", LEGAL)),
                         "same question, same answer required")

    def test_link_candidates_do_not_cross_clients(self):
        self.file_quote(client=SHORT)
        self.assertEqual([], database.list_link_candidates("quote", OTHER))


if __name__ == "__main__":
    unittest.main(verbosity=2)
