"""Client-only storage and safety contracts, always against temporary SQLite files."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config, profile_manager, shared_library

class ClientMaster(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.client_master"),
                             "Dedicated Clients Master Data service must exist")
        from backend import client_master
        self.cm = client_master
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        manager = profile_manager.ProfileManager(root / "data", root / "static")
        manager.create_registry("abraj")
        p = patch.object(profile_manager, "_MANAGER", manager); p.start(); self.addCleanup(p.stop)
        p = patch.object(profile_manager, "legacy_fallback_enabled", return_value=False); p.start(); self.addCleanup(p.stop)
        self.assertTrue(profile_manager.shared_db_path().resolve().is_relative_to(root.resolve()))
        self.assertTrue(profile_manager.profile_db_path().resolve().is_relative_to(root.resolve()))
        p = patch.object(config, "INV_BANK_INFO", [
            {"key":"adib","label":"ADIB","currency":"USD"},
            {"key":"bob","label":"Bank of Baghdad","currency":"USD"},
            {"key":"cash","label":"Cash","currency":"USD"},
        ]); p.start(); self.addCleanup(p.stop)
        for pid in ("abraj", "eshraqat"):
            path=manager.profile_db_path(pid); path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(path)) as c, c:
                for table in ("quotes","dns","invoices","client_lpos"):
                    c.execute(f"CREATE TABLE {table}(client_name TEXT, doc_no TEXT)")
                c.execute("CREATE TABLE pos(rows_json TEXT)")
        shared_library.init_shared_db()
        with closing(shared_library.connect()) as c, c:
            c.execute("CREATE TABLE master_items(item_id TEXT, name_en TEXT, last_company TEXT)")
            c.execute("INSERT INTO master_items VALUES('M-1','Protected material','TDC')")
        self.initial_material = self.materials()
    def materials(self):
        c=shared_library.connect()
        try:return list(c.execute("SELECT * FROM master_items ORDER BY item_id"))
        finally:c.close()
    def save(self, **kw):
        result=self.cm.save_record(kw)
        self.assertTrue(result["ok"],result)
        return result["record"]
    def test_save_reopen_contacts_and_profile_defaults(self):
        r=self.save(name="HDEC",aliases=["Hyundai"],contacts=[{"name":"Ali","role":"Accounts","email":"a@example.com","phone":"0123","primary":True}],
                    defaults={"payment_method":"bank_transfer","bank_key":"bob","alternative_bank_keys":["adib"],"payment_terms_days":30})
        g=self.cm.get_record(r["id"])["record"]
        self.assertEqual(g["contacts"][0]["phone"],"0123")
        self.assertEqual(g["defaults"]["bank_key"],"bob")
        self.assertEqual(self.cm.defaults_for("hyundai")["payment_bank_key"],"bob")
        self.assertEqual(self.materials(),self.initial_material)
    def test_every_spelling_of_a_client_resolves_to_one_code(self):
        """Legal name and Arabic name are SPELLINGS, not decoration (2026-09-11).

        Waleed types whichever one is in front of him - a PO carries the Arabic,
        an email carries the legal name, his own head carries "TDC". All three
        have to land on the same client, or the same job ends up numbered and
        filed under three different codes.
        """
        self.save(name="TDC", legal_name="China Communications construction company Ltd. Iraq Branch",
                  aliases=["CCCC ltd"], defaults={"payment_method":"cash","bank_key":"cash"})
        self.save(name="HDEC", legal_name="Hyundai Engineering and Construction Co., Ltd",
                  name_ar="هيونداي",
                  defaults={"payment_method":"bank_transfer","bank_key":"bob"})
        for typed in ("TDC", "CCCC ltd", "China Communications construction company Ltd. Iraq Branch"):
            self.assertEqual(self.cm.canonical(typed), "TDC", typed)
        for typed in ("HDEC", "Hyundai Engineering and Construction Co., Ltd",
                      "هيونداي"):
            self.assertEqual(self.cm.canonical(typed), "HDEC", typed)
        # The payment method follows the client, not the spelling.
        for typed in ("TDC", "CCCC ltd", "China Communications construction company Ltd. Iraq Branch"):
            self.assertEqual(self.cm.defaults_for(typed)["payment_bank_key"], "cash", typed)
        self.assertEqual(self.cm.defaults_for(
            "هيونداي")["payment_bank_key"], "bob")
        # A name nobody has filed is still a usable name - it comes back as
        # typed, which is what lets the app number a client before anyone has
        # got around to filing them.
        self.assertEqual(self.cm.canonical("Brand New Client"), "Brand New Client")
        self.assertFalse(self.cm.defaults_for("Brand New Client")["ok"])

    def test_defaults_carry_the_printed_name_and_the_code(self):
        """The editor prints one and files under the other."""
        self.save(name="TDC", legal_name="China Communications construction company Ltd. Iraq Branch",
                  aliases=["CCCC ltd"])
        out = self.cm.defaults_for("CCCC ltd")
        self.assertEqual(out["code"], "TDC")
        self.assertEqual(out["print_name"],
                         "China Communications construction company Ltd. Iraq Branch")
        self.assertEqual(out["name"], "TDC", "`name` stays the short code the app groups by")
        # A client with no legal name prints its own name rather than nothing.
        self.save(name="Gusan")
        self.assertEqual(self.cm.defaults_for("Gusan")["print_name"], "Gusan")

    def test_document_numbers_use_the_display_name(self):
        """INV-MT-TDC-054, not INV-MT-China Communications...-054.

        All three numbering modules resolve through the client store, so the
        number and the folder come out the same whichever spelling was typed.
        """
        from backend import inv_numbering, dn_numbering, quote_numbering
        self.save(name="TDC", legal_name="China Communications construction company Ltd. Iraq Branch",
                  aliases=["CCCC ltd"])
        self.save(name="JUNE", legal_name="JUNE CONSTRUCTION CO.")
        for module in (inv_numbering, dn_numbering, quote_numbering):
            for typed in ("CCCC ltd", "China Communications construction company Ltd. Iraq Branch"):
                self.assertEqual(module._sanitize_client(typed), "TDC",
                                 f"{module.__name__} / {typed}")
            self.assertEqual(module._sanitize_client("JUNE CONSTRUCTION CO."), "JUNE",
                             f"{module.__name__} must shorten the long display name")
            # Unknown names still number, and are still made safe for a folder.
            self.assertEqual(module._sanitize_client("New/Client"), "NewClient")
            self.assertEqual(module._sanitize_client(""), "CLIENT")

    def test_alias_collision_is_atomic(self):
        a=self.save(name="HDEC",aliases=["Hyundai"])
        b=self.save(name="TDC")
        out=self.cm.save_record({"id":b["id"],"name":"Changed","aliases":["HYUNDAI"]})
        self.assertFalse(out["ok"])
        self.assertEqual(self.cm.get_record(b["id"])["record"]["name"],"TDC")
        self.assertEqual(self.cm.get_record("Hyundai")["record"]["id"],a["id"])
    def test_archive_survives_remember_and_restores(self):
        r=self.save(name="Unused")
        self.cm.set_archived(r["id"],True)
        self.cm.remember("Unused")
        self.assertEqual(self.cm.list_records()["records"],[])
        self.assertEqual(len(self.cm.list_records(status="archived")["records"]),1)
        self.cm.set_archived(r["id"],False)
        self.assertEqual(len(self.cm.list_records()["records"]),1)
    def test_preferences_do_not_cross_profiles(self):
        r=self.save(name="HDEC",defaults={"bank_key":"bob"})
        profile_manager.manager().activate("eshraqat")
        self.assertEqual(self.cm.get_record(r["id"])["record"]["defaults"],{})
    def test_wrong_profile_save_rejected(self):
        out=self.cm.save_record({"name":"HDEC","profile_id":"eshraqat"})
        self.assertFalse(out["ok"])
    def test_unknown_bank_and_bad_types_rejected(self):
        self.assertFalse(self.cm.save_record({"name":"Bad","defaults":{"bank_key":"other"}})["ok"])
        self.assertFalse(self.cm.save_record({"name":"Bad","aliases":"oops"})["ok"])
        self.assertFalse(self.cm.save_record({"name":"Bad","defaults":{"payment_terms_days":-1}})["ok"])
    def test_history_aliases_do_not_rewrite_documents(self):
        r=self.save(name="HDEC",aliases=["Hyundai"])
        with closing(sqlite3.connect(profile_manager.profile_db_path("eshraqat"))) as c, c:
            c.execute("INSERT INTO quotes VALUES('Hyundai','Q-1')")
        g=self.cm.get_record(r["id"])["record"]
        self.assertEqual(g["usage"]["total"],1)
        self.assertEqual(g["usage"]["by_profile"]["eshraqat"],1)
        with closing(sqlite3.connect(profile_manager.profile_db_path("eshraqat"))) as c, c:
            self.assertEqual(c.execute("SELECT client_name FROM quotes").fetchone()[0],"Hyundai")
    def test_delete_refuses_other_profile_usage(self):
        r=self.save(name="Used")
        with closing(sqlite3.connect(profile_manager.profile_db_path("eshraqat"))) as c, c:
            c.execute("INSERT INTO client_lpos VALUES('Used','PO-1')")
        self.assertFalse(self.cm.remove_unused(r["id"])["ok"])
    def test_delete_refuses_material_reference(self):
        r=self.save(name="TDC")
        self.assertFalse(self.cm.remove_unused(r["id"])["ok"])
        self.assertEqual(self.materials(),self.initial_material)

    def test_delete_refuses_unavailable_registered_profile(self):
        r=self.save(name="Possibly used")
        path=profile_manager.profile_db_path("eshraqat")
        parked=path.with_suffix(".unavailable")
        path.rename(parked)
        try:
            self.assertFalse(self.cm.remove_unused(r["id"])["ok"])
            self.assertTrue(self.cm.get_record(r["id"])["record"]["usage"]["errors"])
        finally:
            parked.rename(path)
    def test_stale_save_rejected(self):
        r=self.save(name="HDEC")
        self.save(id=r["id"],notes="New note")
        out=self.cm.save_record({"id":r["id"],"updated_at":r["updated_at"],"notes":"Stale"})
        self.assertFalse(out["ok"])
    def test_rename_keeps_old_alias(self):
        r=self.save(name="Old")
        r=self.save(id=r["id"],name="New")
        self.assertEqual(self.cm.get_record("Old")["record"]["id"],r["id"])
    def test_seed_once_does_not_restore_deleted_presets(self):
        self.cm.seed_once(["Unused preset"])
        r=self.cm.get_record("Unused preset")["record"]
        self.assertTrue(self.cm.remove_unused(r["id"])["ok"])
        self.cm.seed_once(["Unused preset"])
        self.assertFalse(self.cm.get_record("Unused preset")["ok"])
    def test_legacy_catalog_import_does_not_duplicate_alias(self):
        r=self.save(name="HDEC",aliases=["Hyundai"])
        with closing(shared_library.connect()) as c, c:
            c.execute("INSERT OR IGNORE INTO clients VALUES('Hyundai',1,'today')")
        self.assertEqual(len(self.cm.list_records()["records"]),1)

    def test_removing_unused_legacy_alias_does_not_resurrect_client(self):
        r=self.save(name="HDEC",aliases=["Hyundai"])
        with closing(shared_library.connect()) as c, c:
            c.execute("INSERT OR IGNORE INTO clients VALUES('Hyundai',1,'today')")
        self.save(id=r["id"],aliases=[])
        self.assertEqual(len(self.cm.list_records()["records"]),1)

    def test_empty_identity_cannot_apply_defaults_or_archive(self):
        self.assertFalse(self.cm.defaults_for("")["ok"])
        self.assertFalse(self.cm.set_archived("")["ok"])

    def test_normalized_legacy_name_is_removed_completely(self):
        with closing(shared_library.connect()) as c, c:
            c.execute("INSERT INTO clients VALUES('Acme  Corp',1,'today')")
        r=self.cm.get_record("Acme Corp")["record"]
        self.save(id=r["id"],name="Acme Corp")
        self.assertTrue(self.cm.remove_unused(r["id"])["ok"])
        self.assertFalse(self.cm.get_record("Acme Corp")["ok"])

    def test_cash_missing_is_not_silently_applied(self):
        self.save(name="TDC",defaults={"payment_method":"cash"})
        with patch.object(config,"INV_BANK_INFO",[]):
            self.assertFalse(self.cm.defaults_for("TDC")["ok"])

    def test_bank_transfer_cannot_select_cash(self):
        result=self.cm.save_record({"name":"Bad","defaults":{"payment_method":"bank_transfer","bank_key":"cash"}})
        self.assertFalse(result["ok"])

    def test_post_commit_usage_error_is_reported_as_saved(self):
        with patch.object(self.cm,"usage_for",side_effect=sqlite3.OperationalError("busy")):
            result=self.cm.save_record({"name":"Saved"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["record"]["usage"]["errors"])
        self.assertTrue(self.cm.get_record("Saved")["ok"])

if __name__=="__main__": unittest.main()
