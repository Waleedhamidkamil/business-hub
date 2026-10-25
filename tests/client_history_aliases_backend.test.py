"""Real client-history and Insights queries against isolated profile databases."""
import json
import sqlite3
import unittest
from contextlib import closing

from isolated_client_data import activate

# Install temporary profile/shared paths before any config-bound backend import.
TEST_ROOT = activate()
from backend import client_master, clients, config, insights, profile_manager


class ClientHistoryAliases(unittest.TestCase):
    def setUp(self):
        for pid in ("abraj", "eshraqat"):
            path = profile_manager.profile_db_path(pid)
            self.assertTrue(path.resolve().is_relative_to(TEST_ROOT.resolve()))
            with closing(sqlite3.connect(path)) as conn, conn:
                for table in ("quotes", "invoices", "dns"):
                    conn.execute(f"DELETE FROM {table}")
        shared_path = profile_manager.shared_db_path()
        self.assertTrue(shared_path.resolve().is_relative_to(TEST_ROOT.resolve()))
        with closing(sqlite3.connect(shared_path)) as conn, conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            for table in ("clients", "client_aliases", "client_preferences", "client_records"):
                if table in tables:
                    conn.execute(f"DELETE FROM {table}")
        result = client_master.save_record({
            "name": "HDEC", "aliases": ["Hyundai Engineering", "Straße"]})
        self.assertTrue(result["ok"], result)
        self.invoice("I-1", "HDEC", 100, "2026-09-04", "2026-09-04")
        self.invoice("I-2", "Hyundai Engineering", 200, "2026-06-01", "2026-09-05")
        self.invoice("I-3", "Hyundai  Engineering", 300, "2026-07-01", "2026-09-03")
        self.invoice("I-4", "STRASSE", 400, "2026-08-01", "2026-09-02")
        self.invoice("I-OTHER", "Other Client", 9000, "2000-01-01", "2030-01-01")
        self.invoice("I-PROFILE", "HDEC", 5000, "2001-01-01", "2031-01-01", "eshraqat")
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.execute(
                "INSERT INTO quotes(quote_no,quote_type,created_at,updated_at,quote_date,"
                "year,month,client_name,pdf_path,rows_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("Q-1", "materials", "2026-09-01", "2026-09-01", "2026-09-01",
                 2026, 9, " hyundai\tengineering ", "quote.pdf", "[]"))
            conn.execute(
                "INSERT INTO dns(dn_no,created_at,updated_at,dn_date,year,month,"
                "client_name,pdf_path,rows_json) VALUES(?,?,?,?,?,?,?,?,?)",
                ("D-1", "2026-09-01", "2026-09-01", "2026-09-01", 2026, 9,
                 "STRASSE", "dn.pdf", "[]"))

    def invoice(self, number, name, amount, created, updated, profile="abraj"):
        rows = json.dumps([{"item": "Widget", "qty": 1, "rate": amount,
                            "cost": amount / 2, "cost_ccy": "USD"}])
        with closing(sqlite3.connect(profile_manager.profile_db_path(profile))) as conn, conn:
            conn.execute(
                "INSERT INTO invoices(inv_no,inv_type,created_at,updated_at,inv_date,"
                "year,month,client_name,pdf_path,rows_json,grand_total) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (number, "materials", created, updated, "2026-09-01", 2026, 9,
                 name, "invoice.pdf", rows, amount))

    def test_client_history_matches_normalized_list_totals(self):
        summary = next(r for r in clients.client_list() if r["name"] == "HDEC")
        detail = clients.client_profile(" hyundai engineering ")
        self.assertEqual(summary["invoiced_total"], 1000)
        self.assertEqual(detail["summary"]["invoiced_total"], summary["invoiced_total"])
        self.assertEqual(detail["summary"]["invoices"], 4)
        self.assertEqual(detail["summary"]["quotes"], 1)
        self.assertEqual(detail["summary"]["dns"], 1)

    def test_insights_detail_includes_all_normalized_alias_documents(self):
        detail = insights.client_insights("HDEC", 1, 2026, 9)
        self.assertEqual(detail["client"]["charged"], 1000)
        self.assertEqual(detail["invoice_summary"]["charged"], detail["client"]["charged"])
        self.assertEqual({d["no"] for d in detail["docs"]},
                         {"I-1", "I-2", "I-3", "I-4", "Q-1", "D-1"})

    def test_alias_request_selects_the_canonical_insights_summary(self):
        detail = insights.client_insights("Hyundai Engineering", 1, 2026, 9)
        self.assertEqual(detail["client"]["name"], "HDEC")
        self.assertEqual(detail["client"]["charged"], 1000)
        self.assertIsNotNone(detail["client"]["rank"])

    def test_activity_includes_alias_dates_without_other_clients_or_profiles(self):
        overview = insights.insights_overview(1, 2026, 9)
        record = next(c for c in overview["clients"] if c["name"] == "HDEC")
        self.assertEqual(record["first_seen"], "2026-06-01")
        self.assertEqual(record["last_activity"], "2026-09-05")

    def test_out_of_window_alias_request_keeps_all_time_activity(self):
        detail = insights.client_insights("Hyundai Engineering", 1, 2026, 8)
        self.assertEqual(detail["client"]["name"], "HDEC")
        self.assertEqual(detail["client"]["charged"], 0)
        self.assertEqual(detail["client"]["first_seen"], "2026-06-01")
        self.assertEqual(detail["client"]["last_activity"], "2026-09-05")

    def test_history_queries_do_not_rewrite_saved_names(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            before = list(conn.execute("SELECT inv_no,client_name FROM invoices ORDER BY inv_no"))
        clients.client_profile("HDEC")
        insights.client_insights("Hyundai Engineering", 1, 2026, 9)
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            after = list(conn.execute("SELECT inv_no,client_name FROM invoices ORDER BY inv_no"))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
