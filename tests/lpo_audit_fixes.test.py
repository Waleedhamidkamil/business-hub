"""Regressions from the 2026-10-02 LPO audit. All data is isolated."""
import json
import inspect
from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from isolated_client_data import activate
activate()
from backend import api, client_master, database

CLIENT = "LPO Audit Client"
OTHER = "LPO Audit Other"
ORDER = [{"item": "Desk", "description": "Large", "unit": "EA", "qty": 100, "rate": "10"}]

class AuditFixes(unittest.TestCase):
    def setUp(self):
        self.api = api.Api()
        client_master.save_record({"name": CLIENT, "aliases": ["Audit Alias"]})
        client_master.save_record({"name": OTHER})
        with database._connect() as conn:
            for table in ("client_lpos", "dns", "invoices", "quotes", "lpo_invoice_periods"):
                conn.execute(f"DELETE FROM {table}")
        database.save_lpo(CLIENT, "ORDER", total=1000, rows=ORDER)

    def quote(self, name=OTHER):
        database.save_quote_record(quote_no="FOREIGN", quote_date="1/9/2026", quote_type="materials",
            year=2026, month=9, client_name=name, rfq_no="", client_address="",
            pdf_path="quote.pdf", rows=ORDER, terms=[])

    def delivered(self, qty):
        with database._connect() as conn:
            conn.execute("INSERT INTO dns(dn_no,client_name,lpo_no,rows_json,pdf_path,created_at,updated_at,dn_date,year,month) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("DN-AUDIT", CLIENT, "ORDER", json.dumps([dict(ORDER[0], qty=qty)]), "dn.pdf", "2026-09-01", "2026-09-01", "1/9/2026", 2026, 9))

    def seed(self, kind):
        self.assertIn("document_kind",inspect.signature(self.api.lpo_document_seed).parameters,
            "Invoice and DN need different quantity policies")
        return self.api.lpo_document_seed(CLIENT,"ORDER",kind)

    def invoiced(self, qty, description="Large", inv_date="1/9/2026", no="INV-AUDIT"):
        database.save_inv_record(inv_no=no, inv_date=inv_date, inv_type="MT", year=2026, month=9,
            client_name=CLIENT, lpo_no="ORDER", client_address="", payment_bank_key="",
            withholding_included=1, grand_total=qty*10, pdf_path="invoice.pdf",
            rows=[dict(ORDER[0], qty=qty, description=description)])

    def test_list_and_get_expose_saved_lines(self):
        self.assertEqual(ORDER, self.api.list_lpos("")[0].get("rows"))
        self.assertEqual(ORDER, self.api.get_lpo(CLIENT, "ORDER").get("rows"))

    def test_a_header_only_save_preserves_lines(self):
        result=self.api.save_lpo({"client_name":CLIENT,"lpo_no":"ORDER","total":1000,"notes":"Changed"})
        self.assertTrue(result["ok"])
        raw=database.load_lpo(CLIENT,"ORDER")["rows_json"]
        self.assertIsNotNone(raw,"A header edit discarded recorded lines")
        self.assertEqual(ORDER,json.loads(raw))

    def test_an_explicit_empty_order_can_still_be_saved(self):
        self.assertTrue(self.api.save_lpo({"client_name":CLIENT,"lpo_no":"ORDER","rows":[]})["ok"])
        self.assertEqual([],json.loads(database.load_lpo(CLIENT,"ORDER")["rows_json"]))

    def test_invoice_uses_delivered_unbilled_items(self):
        self.delivered(60)
        self.invoiced(10)
        seed=self.seed("inv")
        self.assertTrue(seed["ok"])
        self.assertEqual(50,seed["rows"][0]["qty"])

    def test_delivery_uses_undelivered_items(self):
        self.delivered(60)
        seed=self.seed("dn")
        self.assertEqual(40,seed["rows"][0]["qty"])

    def test_full_delivery_still_has_unbilled_items(self):
        self.delivered(100)
        self.invoiced(60)
        self.assertEqual(40,self.seed("inv")["rows"][0]["qty"])

    def test_settled_items_are_not_offered_again(self):
        self.delivered(100)
        self.invoiced(100)
        self.assertEqual([],self.seed("inv")["rows"])
        self.assertEqual([],self.seed("dn")["rows"])

    def test_direct_invoice_without_a_dn_uses_unbilled_order(self):
        self.invoiced(20)
        self.assertEqual(80,self.seed("inv")["rows"][0]["qty"])

    def test_foreign_internal_quote_is_refused_on_save(self):
        self.quote()
        result=self.api.save_lpo({"client_name":CLIENT,"lpo_no":"ORDER","source_quote_no":"FOREIGN","rows":ORDER})
        self.assertFalse(result["ok"])
        self.assertNotEqual("won",database.load_quote("FOREIGN").get("bid_status"))

    def test_alias_of_quote_client_is_allowed(self):
        self.quote(CLIENT)
        result=self.api.save_lpo({"client_name":"Audit Alias","lpo_no":"ORDER","source_quote_no":"FOREIGN","rows":ORDER})
        self.assertTrue(result["ok"])
        self.assertEqual("won",database.load_quote("FOREIGN").get("bid_status"))

    def test_foreign_quote_cannot_be_pulled(self):
        self.quote()
        self.assertIn("client_name",inspect.signature(self.api.lpo_from_quote).parameters)
        self.assertFalse(self.api.lpo_from_quote("FOREIGN",CLIENT)["ok"])

    def test_existing_bad_quote_link_is_not_used_for_costs(self):
        self.quote()
        database.save_lpo(CLIENT,"ORDER",rows=ORDER,source_quote_no="FOREIGN")
        self.assertFalse(self.seed("dn")["ok"])

    def test_partial_last_rental_month_counts(self):
        self.assertEqual(3,database._term_months(date(2026,8,12),date(2026,11,11)))
        self.assertEqual(4,database._term_months(date(2026,4,18),date(2026,8,12)))
        self.assertEqual(7,database._term_months(date(2026,6,8),date(2027,1,8)))

    def test_the_month_ledger_counts_an_invoice_in_the_month_it_is_dated(self):
        """2026-10-06: months are counted by invoice date, not by service dates
        read out of the invoice text (Waleed's rule; lpo_billing.months)."""
        database.save_lpo(CLIENT,"ORDER",kind="rental",rows=ORDER,
            period_start="12/8/2026",period_end="11/11/2026")
        self.invoiced(1,"12 Aug - 11 Sep 2026",inv_date="15/9/2026")
        got=self.api.lpo_billing_detail(CLIENT,"ORDER")
        self.assertTrue(got["ok"])
        self.assertEqual(3,got["months_total"])
        self.assertEqual(1,got["months_invoiced"])
        september=next(m for m in got["months"] if m["month"]=="2026-09")
        self.assertEqual((1,["INV-AUDIT"]),(september["count"],september["invoices"]))

    def test_two_invoices_in_one_month_show_two(self):
        database.save_lpo(CLIENT,"ORDER",kind="rental",rows=ORDER,
            period_start="12/8/2026",period_end="11/9/2026")
        self.invoiced(1,no="INV-FIRST",inv_date="1/9/2026")
        self.invoiced(1,no="INV-SECOND",inv_date="20/9/2026")
        got=self.api.lpo_billing_detail(CLIENT,"ORDER")
        september=next(m for m in got["months"] if m["month"]=="2026-09")
        self.assertEqual(2,september["count"])

    def test_draft_delivery_does_not_count(self):
        self.delivered(100)
        with database._connect() as conn:
            conn.execute("UPDATE dns SET pdf_path='' WHERE dn_no='DN-AUDIT'")
        self.assertEqual(100,self.seed("dn")["rows"][0]["qty"])
        self.assertEqual([],self.api.list_lpos("")[0]["dns"])

    def test_recurring_invoice_does_not_guess_period_quantity(self):
        database.save_lpo(CLIENT,"ORDER",kind="rental",rows=ORDER,period_start="12/8/2026")
        got=self.seed("inv")
        self.assertEqual("",got["rows"][0]["qty"])
        self.assertEqual("enter_period_quantity",got["quantity_policy"])

    def test_reference_review_is_scoped_and_read_only(self):
        self.invoiced(1)
        with database._connect() as conn:
            conn.execute("UPDATE invoices SET lpo_no='MISSING' WHERE inv_no='INV-AUDIT'")
        reader=getattr(self.api,"lpo_reference_gaps",None)
        self.assertTrue(callable(reader),"Unmatched references need an inspectable review list")
        gaps=reader()
        self.assertEqual(["INV-AUDIT"],[r["doc_no"] for r in gaps])
        self.assertEqual("MISSING",database.load_inv("INV-AUDIT")["lpo_no"])
        with database._connect() as conn:
            conn.execute("UPDATE invoices SET pdf_path='' WHERE inv_no='INV-AUDIT'")
        self.assertEqual([],reader())

    def test_period_table_upgrade_is_idempotent(self):
        import sqlite3
        from backend import migrations
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE invoices(inv_no TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE lpo_invoice_periods(inv_no TEXT PRIMARY KEY,period_start TEXT NOT NULL,period_end TEXT NOT NULL,updated_at TEXT NOT NULL)")
            conn.execute("INSERT INTO lpo_invoice_periods VALUES('INV','2026-08-12','2026-09-11','now')")
            migrations._lpo_invoice_periods(conn)
            columns=[r[1] for r in conn.execute("PRAGMA table_info(lpo_invoice_periods)")]
            self.assertTrue({"inv_no","period_start","period_end","updated_at","client_name","lpo_no"}<=set(columns))
            migrations._lpo_invoice_periods(conn)
            self.assertEqual(1,conn.execute("SELECT COUNT(*) FROM lpo_invoice_periods").fetchone()[0])

if __name__=="__main__":
    unittest.main(verbosity=2)
