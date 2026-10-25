"""A document may only name a client that is on file (Waleed, 2026-09-11).

"no new clients shall be accepted when typing in client name in all editors."
Free typing is how `JUNE CONSTRUCTION CO.` ended up inside a document number
and how three quotations came to disagree with their own client. The editors
offer to add the client on the spot; this is the check underneath, which a save
from anywhere has to pass.

Two things it must NOT break:
  - an already-saved document keeps the name it was saved with, or the one
    quotation on file that names an off-list client becomes uneditable;
  - work that genuinely has no client, which is what the N/A record is for.

Runs against temporary SQLite files, never the live library.
"""
import importlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from backend import config, profile_manager, shared_library


class ClientMustBeOnFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        manager = profile_manager.ProfileManager(root / "data", APP_ROOT / "static")
        manager.create_registry("abraj")
        p = patch.object(profile_manager, "_MANAGER", manager); p.start(); self.addCleanup(p.stop)
        p = patch.object(profile_manager, "legacy_fallback_enabled", return_value=False)
        p.start(); self.addCleanup(p.stop)
        # `config.DB_PATH` and `config.DATA_DIR` are bound at import, so patching
        # the profile manager alone leaves every profile-database write pointing
        # at the live file. `database._connect()` reads both at CALL time, so
        # redirecting them here is enough.
        p = patch.object(config, "DATA_DIR", root / "data"); p.start(); self.addCleanup(p.stop)
        p = patch.object(config, "DB_PATH", root / "data" / "profile.db")
        p.start(); self.addCleanup(p.stop)
        # BOTH databases, not just the shared one (2026-09-11). Patching
        # `_MANAGER` repoints shared_db_path() but NOT config.DB_PATH, which
        # is bound at import - a sibling suite asserted only the shared path,
        # passed, and wrote eight rows into the live Abraj database.
        self.assertTrue(profile_manager.shared_db_path().resolve().is_relative_to(root.resolve()))
        self.assertFalse(Path(config.DB_PATH).resolve().is_relative_to((APP_ROOT / "data").resolve()),
                         "the profile database is NOT isolated - this suite would write live data")

        from backend import api, client_master
        self.api = api
        self.cm = client_master
        shared_library.init_shared_db()
        self.assertTrue(self.cm.save_record({"name": "TDC", "aliases": ["CCCC ltd"]})["ok"])
        self.assertTrue(self.cm.save_record({"name": "N/A"})["ok"])
        self.assertTrue(self.cm.save_record({"name": "Retired"})["ok"])
        self.cm.set_archived("Retired", True)

    def guard(self, name, existing=None):
        return self.api._known_client_error(name, existing)

    # ---- what must be refused -------------------------------------------
    def test_a_name_that_is_not_on_file_is_refused(self):
        refusal = self.guard("Brand New Client")
        self.assertIsNotNone(refusal, "a client nobody has filed must not reach a document")
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["unknown_client"], "Brand New Client",
                         "the editor keys its quick-add dialog on this field")
        self.assertIn("Brand New Client", refusal["error"])

    def test_a_blank_client_is_refused_and_points_at_the_way_out(self):
        refusal = self.guard("   ")
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal["unknown_client"], "")
        self.assertIn("N/A", refusal["error"],
                      "a blank client should be told about the record that exists for it")

    def test_an_archived_client_is_refused_by_name(self):
        """Archived means retired. Say so, rather than 'not a client'."""
        refusal = self.guard("Retired")
        self.assertIsNotNone(refusal)
        self.assertTrue(refusal.get("archived_client"))
        self.assertIn("archived", refusal["error"].lower())
        self.assertIn("Retired", refusal["error"])

    # ---- what must still pass -------------------------------------------
    def test_every_spelling_of_a_client_on_file_passes(self):
        for typed in ("TDC", "CCCC ltd", "  tdc  "):
            self.assertIsNone(self.guard(typed), f"{typed!r} is TDC and must be accepted")

    def test_work_with_no_client_passes(self):
        self.assertIsNone(self.guard("N/A"))
        self.assertIsNone(self.guard("n/a"), "the record resolves however it is typed")

    def test_a_saved_document_keeps_the_name_it_was_saved_with(self):
        """Locking the list must never make an existing document uneditable."""
        existing = {"client_name": "ABRAJ"}
        self.assertIsNone(self.guard("ABRAJ", existing))
        # ...but that grandfathering is for THAT name only.
        self.assertIsNotNone(self.guard("Somebody Else", existing),
                             "editing a document must not become a way in for any name")

    def test_an_unreadable_client_store_does_not_block_a_save(self):
        """A save is the user's work. Losing it to a broken index is worse."""
        with patch.object(self.cm, "get_record", side_effect=RuntimeError("db is gone")):
            self.assertIsNone(self.guard("Brand New Client"))

    def test_all_three_save_paths_actually_ask(self):
        """The guard is only worth anything if the saves call it.

        Four other suites now patch `_known_client_error` out because they mock
        the whole storage layer. That is fine for them and fatal if it ever
        becomes true of the product - so the calls are checked here, in the
        source, where removing one cannot pass quietly.
        """
        source = (APP_ROOT / "backend" / "api.py").read_text(encoding="utf-8")
        for method in ("save_quote", "save_dn", "save_inv"):
            start = source.index(f"def {method}(self")
            end = source.index("\n    def ", start + 10)
            body = source[start:end]
            self.assertIn("_known_client_error(", body,
                          f"{method} must refuse a client that is not on file")
            self.assertIn("return refusal", body,
                          f"{method} asks but does not act on the answer")

    # ---- the no-client record prints blank -------------------------------
    def test_the_no_client_record_prints_a_blank_client_line(self):
        printed = self.api._printable_document(
            {"quote_no": "QUT-MT-NA-070", "client_name": "N/A", "items": []})
        self.assertEqual(printed["client_name"], "",
                         'a quotation for nobody must not print "N/A" to whoever reads it')
        kept = self.api._printable_document({"client_name": "TDC", "items": []})
        self.assertEqual(kept["client_name"], "TDC", "every other client prints normally")

    def test_the_no_client_record_still_numbers_and_files(self):
        """Blank on the paper, findable in the app: NA in the number and folder."""
        self.assertEqual(self.cm.document_code("N/A"), "NA")
        self.assertEqual(self.cm.defaults_for("N/A")["code"], "NA",
                         "the editor chip must show what the number will use")

    def test_the_chip_and_the_number_cannot_disagree(self):
        """One rule, one implementation - the numbering modules defer to it."""
        from backend import inv_numbering, dn_numbering, quote_numbering
        for name in ("TDC", "CCCC ltd", "N/A", "Not On File"):
            codes = {m._sanitize_client(name)
                     for m in (inv_numbering, dn_numbering, quote_numbering)}
            self.assertEqual(len(codes), 1, f"the three modules disagree about {name!r}: {codes}")
            self.assertEqual(codes.pop(), self.cm.document_code(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
