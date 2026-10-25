"""The rate a document's costs convert at follows the ACCOUNT it is paid into,
and is frozen on the document when it is saved (2026-09-21).

Waleed: "if I made quotation in june for hdec and at that time the receiving bank
was bank of baghdad and the exchange rate was 1400 then in july I changed the
receiving bank to ADIB and made quotation, that quotation profit is calculated
and saved for 1500. When I go back to check the quotation from june it should be
saved and calculated on 1400 because the account used at that time is bank of
baghdad."

Before this, two rules decided the number and neither was visible anywhere:
"Eshraqat is always 1500" and "a deal paid in cash is 1500". The Costing screen
printed the result with no way to see why.

Isolated - `isolated_client_data.activate()` must run BEFORE backend config is
imported, for the reason `tests/client_lpo_save.test.py` documents at length.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, profile_manager  # noqa: E402


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}. "
            "Stop: this suite would write into the live data.")


_assert_isolated()
database.init_db()


class TheStampColumnsExist(unittest.TestCase):
    def test_doc_cost_meta_carries_the_account_and_its_rate(self):
        with database._connect() as conn:
            cols = {r["name"] for r in
                    conn.execute("PRAGMA table_info(doc_cost_meta)").fetchall()}
        self.assertIn("account_key", cols,
                      "which account this document was costed against")
        self.assertIn("account_rate", cols,
                      "that account's rate at the moment it was saved - this is "
                      "what stops a historical profit figure moving")


from backend import client_master, payment_accounts  # noqa: E402


class AccountsCase(unittest.TestCase):
    """Two accounts at different rates, and a client paid into one of them."""

    BANKS = [
        {"key": "bob", "label": "Bank of Baghdad", "currency": "USD",
         "iqd_per_usd": 1400},
        {"key": "adib", "label": "ADIB", "currency": "USD",
         "iqd_per_usd": 1500},
        {"key": "cash", "label": "Cash", "currency": "USD",
         "iqd_per_usd": 1500},
    ]

    @staticmethod
    def put_client(bank_key="bob"):
        """HDEC, paid into `bank_key`. Rewritten for EVERY test: one of them
        moves the client to another account, and a leaked account is a rate
        leaking into the next test."""
        found = client_master.get_record("HDEC") or {}
        existing = found.get("record") or {}
        payload = {"name": "HDEC",
                   "legal_name": "Hyundai Engineering and Construction Co., Ltd",
                   "aliases": [], "contacts": [], "addresses": [],
                   "defaults": {"bank_key": bank_key}}
        if existing.get("id"):
            payload["id"] = existing["id"]
            payload["updated_at"] = existing.get("updated_at")
        res = client_master.save_record(payload)
        assert res.get("ok"), res

    def setUp(self):
        _assert_isolated()
        # `client_master._init` imports the legacy autocomplete names the first
        # time it opens the store, so HDEC may already exist as a bare record.
        self.put_client("bob")
        payment_accounts._accounts_override = [dict(b) for b in self.BANKS]
        self.addCleanup(setattr, payment_accounts, "_accounts_override", None)


class WhichRateAnAccountCarries(AccountsCase):
    def test_a_known_account_returns_its_own_rate(self):
        self.assertEqual(1400, payment_accounts.rate_for_account("bob"))
        self.assertEqual(1500, payment_accounts.rate_for_account("adib"))

    def test_the_key_is_matched_case_insensitively(self):
        """settings.json holds "ADIB" while a client record may hold "adib".
        They have always meant one account; a lookup that says otherwise
        silently changes a rate."""
        self.assertEqual(1500, payment_accounts.rate_for_account("ADIB"))
        self.assertEqual(1400, payment_accounts.rate_for_account("  BOB "))

    def test_an_unknown_account_falls_back_rather_than_guessing(self):
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("no-such-bank"))

    def test_an_account_with_no_rate_falls_back(self):
        payment_accounts._accounts_override = [{"key": "bob", "label": "B"}]
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("bob"))

    def test_a_zero_or_negative_rate_is_not_a_rate(self):
        payment_accounts._accounts_override = [
            {"key": "bob", "iqd_per_usd": 0},
            {"key": "adib", "iqd_per_usd": -5},
        ]
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("bob"))
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_account("adib"))


class WhichAccountADocumentBelongsTo(AccountsCase):
    """Invoice: its own selected account. Quotation and DN: the client's."""

    def test_a_quotation_uses_the_clients_receiving_account(self):
        self.assertEqual("bob", payment_accounts.account_for_document(
            "quote", client_name="HDEC"))

    def test_any_spelling_of_the_client_finds_the_same_account(self):
        self.assertEqual("bob", payment_accounts.account_for_document(
            "quote", client_name="Hyundai Engineering and Construction Co., Ltd"),
            "the identity index already says these are one client")

    def test_an_invoice_uses_the_account_selected_on_it(self):
        self.assertEqual("adib", payment_accounts.account_for_document(
            "inv", client_name="HDEC", bank_key="adib"),
            "that is the account the money actually arrives in")

    def test_an_invoice_with_no_account_of_its_own_uses_the_clients(self):
        self.assertEqual("bob", payment_accounts.account_for_document(
            "inv", client_name="HDEC", bank_key=""))

    def test_an_account_that_no_longer_exists_falls_back_to_the_clients(self):
        self.assertEqual("bob", payment_accounts.account_for_document(
            "inv", client_name="HDEC", bank_key="deleted-bank"))

    def test_an_unknown_client_has_no_account(self):
        self.assertEqual("", payment_accounts.account_for_document(
            "quote", client_name="Nobody Ltd"))

    def test_the_rate_follows_the_account(self):
        self.assertEqual(1400, payment_accounts.rate_for_document(
            "quote", client_name="HDEC"))
        self.assertEqual(1500, payment_accounts.rate_for_document(
            "inv", client_name="HDEC", bank_key="adib"))
        self.assertEqual(payment_accounts.fallback_rate(),
                         payment_accounts.rate_for_document(
                             "quote", client_name="Nobody Ltd"))


class StampingFreezesHistory(AccountsCase):
    """Waleed's June/July example, in the storage layer."""

    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")

    def test_a_stamp_round_trips(self):
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual(("bob", 1400.0),
                         database.get_account_stamp("quote", "QT-JUNE"))

    def test_an_unstamped_document_says_so(self):
        self.assertEqual(("", None),
                         database.get_account_stamp("quote", "QT-NEVER"))

    def test_re_saving_re_stamps(self):
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        database.set_account_stamp("quote", "QT-JUNE", "adib", 1500)
        self.assertEqual(("adib", 1500.0),
                         database.get_account_stamp("quote", "QT-JUNE"),
                         "re-saving against a different account is a re-issue")

    def test_a_stamp_does_not_disturb_a_hand_pinned_rate(self):
        database.set_cost_rate("quote", "QT-JUNE", 1234)
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual(1234, database.get_cost_rate("quote", "QT-JUNE"),
                         "the rate the user typed by hand still outranks it")

    def test_a_stamp_does_not_disturb_the_cost_currency(self):
        database.set_cost_currency("quote", "QT-JUNE", "IQD")
        database.set_account_stamp("quote", "QT-JUNE", "bob", 1400)
        self.assertEqual("IQD", database.get_cost_currency("quote", "QT-JUNE"),
                         "one row, three independent facts about the costs")


class TheMigrationFreezesWhatTheOldRulesSaid(AccountsCase):
    """Stamping has to happen while the OLD rules still answer, so that deleting
    them cannot move a figure."""

    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")
            conn.execute("DELETE FROM quotes")

    def _seed_quote(self, no):
        with database._connect() as conn:
            conn.execute(
                "INSERT INTO quotes(quote_no, quote_date, quote_type, year, month, "
                "client_name, pdf_path, rows_json, bid_status, reference, "
                "updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (no, "9/7/2026", "materials", 2026, 7, "HDEC",
                 r"D:\docs\%s.pdf" % no, "[]", "", "",
                 "2026-09-21T10:00:00", "2026-09-21T10:00:00"))

    def _run(self):
        from backend import migrations
        with database._connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            return migrations._stamp_document_cost_rates(conn)

    def test_an_unstamped_document_is_stamped_with_the_old_answer(self):
        self._seed_quote("QT-OLD")
        self._run()
        _key, rate = database.get_account_stamp("quote", "QT-OLD")
        self.assertIsNotNone(rate, "every existing document must end up stamped, "
                             "or deleting the old rules moves its profit")

    def test_a_document_that_is_already_stamped_is_left_alone(self):
        self._seed_quote("QT-DONE")
        database.set_account_stamp("quote", "QT-DONE", "adib", 1500)
        self._run()
        self.assertEqual(("adib", 1500.0),
                         database.get_account_stamp("quote", "QT-DONE"),
                         "the migration is idempotent - running it twice must "
                         "not overwrite a stamp")

    def test_a_hand_pinned_rate_is_not_touched(self):
        self._seed_quote("QT-PINNED")
        database.set_cost_rate("quote", "QT-PINNED", 1234)
        self._run()
        self.assertEqual(1234, database.get_cost_rate("quote", "QT-PINNED"))

    def test_a_pinned_document_is_stamped_with_the_SAME_rate(self):
        """Found on QUT-EQ-HDEC-048 and QUT-MT-TDC-047, both pinned at 1500 in
        a 1400 profile: the migration stamped 1400 underneath the pin. Harmless
        only while the pin is there - clear it and the document silently drops
        to a rate it never used."""
        self._seed_quote("QT-PINNED2")
        database.set_cost_rate("quote", "QT-PINNED2", 1500)
        self._run()
        _key, stamp = database.get_account_stamp("quote", "QT-PINNED2")
        self.assertEqual(1500.0, stamp,
                         "the stamp must agree with the rate the document is "
                         "actually costed at, not with the profile peg")


class TheRateChain(AccountsCase):
    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")

    def test_a_hand_pinned_rate_wins_over_everything(self):
        database.set_account_stamp("quote", "QT-1", "adib", 1500)
        database.set_cost_rate("quote", "QT-1", 1234)
        self.assertEqual(1234, database.doc_iqd_per_usd("quote", "QT-1"))

    def test_the_stamp_wins_over_the_live_account(self):
        database.set_account_stamp("quote", "QT-1", "bob", 1400)
        self.assertEqual(1400, database.doc_iqd_per_usd("quote", "QT-1"),
                         "this is what stops June's quotation moving to 1500 "
                         "when the client's account changes in July")

    def test_an_unsaved_document_gets_the_fallback(self):
        self.assertEqual(payment_accounts.fallback_rate(),
                         database.doc_iqd_per_usd("quote", "QT-UNSAVED"))

    def test_the_eshraqat_rule_is_gone(self):
        """It used to return 1500 for every Eshraqat document regardless. A
        stamped document must answer from its stamp in any profile."""
        database.set_account_stamp("quote", "QT-1", "bob", 1400)
        self.assertEqual(1400, database.doc_iqd_per_usd("quote", "QT-1"))


class TheEditorCanAskForTheLiveRate(AccountsCase):
    def test_the_bridge_names_the_account_beside_the_rate(self):
        from backend.api import Api
        res = Api().rate_for_document("quote", "HDEC", "")
        self.assertEqual(1400, res["rate"])
        self.assertEqual("bob", res["account_key"])
        self.assertEqual("Bank of Baghdad", res["account_label"],
                         "the Costing screen prints the account beside the rate, "
                         "so a number always says where it came from")

    def test_an_unknown_client_gets_the_fallback_and_says_so(self):
        from backend.api import Api
        res = Api().rate_for_document("quote", "Nobody Ltd", "")
        self.assertEqual(payment_accounts.fallback_rate(), res["rate"])
        self.assertEqual("", res["account_key"])
        self.assertEqual("", res["account_label"])

    def test_an_invoice_reports_its_own_account(self):
        from backend.api import Api
        res = Api().rate_for_document("inv", "HDEC", "adib")
        self.assertEqual(1500, res["rate"])
        self.assertEqual("ADIB", res["account_label"])


class SavingStampsTheDocument(AccountsCase):
    """Runs the REAL save path. `save_without_pdf` keeps it to the database,
    which is where the stamp lives - the PDF renderer is not what is under test
    and the smokes do not exercise this path at all (smoke_quote.py calls
    render_quote_pdf directly, never Api.save_quote)."""

    def setUp(self):
        super().setUp()
        with database._connect() as conn:
            conn.execute("DELETE FROM doc_cost_meta")
            conn.execute("DELETE FROM quotes")

    def _save(self, no="QUT-STAMP-1", client="HDEC"):
        """Save through the REAL path and return the number the app ASSIGNED.

        `save_quote` recomputes the number for any document that is not an edit,
        so the one passed in is a request, not a promise. The first draft of
        this test assumed otherwise and ended up comparing one document with
        itself.
        """
        from backend.api import Api
        res = Api().save_quote({
                "quote_no": no, "date": "29/5/2026", "quote_type": "materials",
                "client_name": client, "client_address": "Site", "rfq_no": "N/A",
                "items": [], "terms": [], "save_without_pdf": True,
                "is_edit": False,
            })
        self.assertTrue(res.get("ok"), res)
        return res.get("quote_no") or no

    def test_saving_a_quotation_stamps_the_clients_account(self):
        no = self._save()
        self.assertEqual(("bob", 1400.0), database.get_account_stamp("quote", no),
                         "the rate a document was costed at has to be recorded "
                         "when it is saved, or it cannot be frozen")

    def test_the_stamped_rate_is_what_the_document_reports_afterwards(self):
        no = self._save()
        self.assertEqual(1400, database.doc_iqd_per_usd("quote", no))

    def test_moving_the_client_to_another_account_leaves_june_alone(self):
        """Waleed's example, end to end.

        June's quotation is saved while HDEC is paid into Bank of Baghdad at
        1400. In July the client moves to ADIB at 1500. June must still read
        1400, and anything costed from then on must read 1500.
        """
        june = self._save()
        self.assertEqual(1400, database.doc_iqd_per_usd("quote", june))

        self.put_client("adib")          # July: the client moves to ADIB

        self.assertEqual(1400, database.doc_iqd_per_usd("quote", june),
                         "June still reads the rate its account carried when it "
                         "was saved - this is the whole point")
        from backend.api import Api
        self.assertEqual(1500, Api().rate_for_document("quote", "HDEC", "")["rate"],
                         "and a document costed from now on uses the new account")

    def test_re_saving_june_after_the_move_re_stamps_it(self):
        """Because that is a re-issue: the document really is being costed
        against the account the client is paid into now."""
        june = self._save()
        self.put_client("adib")
        again = self._save(no=june)
        self.assertEqual(1500, database.doc_iqd_per_usd("quote", again))

class TheRateSurvivesTheSettingsFile(unittest.TestCase):
    """The rate typed in Settings > Payments must REACH payment_accounts.

    Reported by Waleed 2026-09-21: "I tried to put exchange rate in settings and
    didnt work". It was written to settings.json correctly and thrown away on
    the way back: `config._apply_user_settings` rebuilt every bank entry from a
    whitelist of eight keys and `iqd_per_usd` was not one of them, and
    `fallback_iqd_per_usd` was never read at all.

    Every other test in this file sets `payment_accounts._accounts_override`,
    which bypasses config entirely - so they all passed while the real path was
    dead. This one goes through the FILE, which is the only reason it would have
    caught it.
    """

    def setUp(self):
        _assert_isolated()
        payment_accounts._accounts_override = None   # the real path, on purpose
        self.path = Path(profile_manager.settings_path())
        self.original = self.path.read_text(encoding="utf-8")
        self.addCleanup(self._restore)

    def _restore(self):
        self.path.write_text(self.original, encoding="utf-8")
        config._apply_user_settings()

    def _write(self, banks, fallback=None):
        import json
        data = json.loads(self.path.read_text(encoding="utf-8"))
        inv = data.setdefault("invoice", {})
        inv["bank_info"] = banks
        if fallback is not None:
            inv["fallback_iqd_per_usd"] = fallback
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        config._apply_user_settings()

    def test_an_account_rate_written_to_settings_reaches_the_code(self):
        self._write([{"key": "bob", "label": "Bank of Baghdad", "currency": "USD",
                      "iqd_per_usd": 1400},
                     {"key": "adib", "label": "ADIB", "currency": "USD",
                      "iqd_per_usd": 1500}])
        self.assertEqual(1400, payment_accounts.rate_for_account("bob"),
                         "the rate typed in Settings > Payments never arrived")
        self.assertEqual(1500, payment_accounts.rate_for_account("adib"))

    def test_the_fallback_written_to_settings_reaches_the_code(self):
        self._write([{"key": "bob", "label": "B", "iqd_per_usd": 1400}],
                    fallback=1450)
        self.assertEqual(1450, payment_accounts.fallback_rate(),
                         "the fallback box in Settings > Payments did nothing")

    def test_the_editor_reads_back_what_it_saved(self):
        """A rate that saves but comes back blank is indistinguishable from one
        that never saved, which is exactly how this was reported."""
        from backend.api import Api
        self._write([{"key": "bob", "label": "Bank of Baghdad",
                      "currency": "USD", "iqd_per_usd": 1400}], fallback=1450)
        settings = (Api().get_doc_settings("invoice") or {}).get("settings") or {}
        banks = settings.get("bank_info") or []
        self.assertEqual(1400, banks[0].get("iqd_per_usd"),
                         "the Payments tab would show an empty rate box")
        self.assertEqual(1450, settings.get("fallback_iqd_per_usd"))

    def test_saving_through_the_bridge_round_trips(self):
        """The whole path the button uses: save_doc_settings, then read back."""
        from backend.api import Api
        res = Api().save_doc_settings("invoice", {
            "bank_info": [{"key": "bob", "label": "Bank of Baghdad",
                           "currency": "USD", "iqd_per_usd": 1400}],
            "fallback_iqd_per_usd": 1450,
        })
        self.assertTrue(res.get("ok", True), res)
        self.assertEqual(1400, payment_accounts.rate_for_account("bob"))
        self.assertEqual(1450, payment_accounts.fallback_rate())


if __name__ == "__main__":
    unittest.main(verbosity=2)
