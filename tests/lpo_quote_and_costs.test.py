"""The LPO carries its quotation, and the quotation carries the costs
(2026-09-28).

Waleed filed a PO against a quotation, made an invoice from it, and had to type
the quotation number again and every line's cost again - both of which the app
already held. Three things are covered here:

  1. `client_quotes` - the list the Select button offers. Identity-aware (a PO
     typed under `HDEC` must see quotations saved under the legal name), and
     honest about a total it cannot read: an equipment quotation keeps its
     quantities in per-document custom columns, so it must say so rather than
     print a confident 0.00 beside a real quotation.
  2. `lpo_from_quote` returns the quotation's TOTAL, so the form fills the
     "Total value" box instead of asking for it off the PO.
  3. `lpo_document_seed` - one call giving a delivery note or invoice the
     quantities still owed, the quotation number, and each line's cost WITH the
     currency it was written in.

The currency rule is the point of the last one. 121 of the 332 live quotation
rows that carry a cost predate per-cell currencies. Dropping them would lose
real costs; stamping them "USD" would put 84,000 where 60 belongs. They are
resolved the way `_line_econ` already resolves them - the quotation's own cost
currency.

!! Runs against the LIVE profile DB (no test isolation in this project). Every
!! row uses the __LPOQTEST__ sentinel and is removed by targeted DELETE.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database  # noqa: E402

TAG = "__LPOQTEST__"
CLIENT = f"Seed Client {TAG}"
ALIAS = f"SC{TAG}"
QUOTE_USD = f"QUT-MT-{TAG}-001"
QUOTE_IQD = f"QUT-MT-{TAG}-002"
QUOTE_EQ = f"QUT-EQ-{TAG}-003"
LPO_NO = f"PO-SEED-{TAG}"


def _register_client():
    from backend import client_master, shared_library
    shared_library.init_shared_db()
    client_master.save_record({"name": CLIENT})


_register_client()


def _save_quote(no, rows, qtype="materials"):
    database.save_quote_record(
        quote_no=no, quote_date="1/9/2026", quote_type=qtype,
        year=2026, month=9, client_name=CLIENT, rfq_no="",
        client_address="", pdf_path=f"{no}.pdf", rows=rows, terms=[])


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM client_lpos WHERE client_name LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM quotes WHERE quote_no LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM dns WHERE dn_no LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM invoices WHERE inv_no LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM doc_cost_meta WHERE doc_no LIKE ?", (f"%{TAG}%",))


def setUpModule():
    cleanup()


def tearDownModule():
    cleanup()


class TheClientsOwnQuotations(unittest.TestCase):
    """What the Select button on the LPO form offers."""

    def setUp(self):
        cleanup()
        _save_quote(QUOTE_USD, [
            {"item": "Office desk", "description": "1650*850*800", "unit": "EA",
             "qty": "60", "rate": "135", "cost": "128.57"},
            {"item": "Office chair", "description": "670*580*1050", "unit": "EA",
             "qty": "78", "rate": "75", "cost": "71.43", "cost_ccy": "USD"},
        ])

    def tearDown(self):
        cleanup()

    def test_it_lists_this_clients_quotations_with_their_totals(self):
        got = [q for q in database.client_quotes(CLIENT) if TAG in q["quote_no"]]
        self.assertEqual([QUOTE_USD], [q["quote_no"] for q in got])
        # 60 x 135 + 78 x 75 = 8100 + 5850
        self.assertAlmostEqual(13950.0, got[0]["total"], places=2)
        self.assertEqual(2, got[0]["lines"])
        self.assertTrue(got[0]["total_readable"])

    def test_a_blank_client_offers_nothing(self):
        """Not 'everything'. Offering every client's quotations is exactly how
        the wrong deal gets cited."""
        self.assertEqual([], database.client_quotes(""))

    def test_an_unreadable_total_says_so_instead_of_printing_zero(self):
        # An equipment quotation's quantities live in per-document custom
        # columns (col_2..col_6), which only that document can interpret.
        _save_quote(QUOTE_EQ, [
            {"equipment": "Toyota Hilux", "specs": "4WD", "rate": "1800",
             "col_2": "6", "col_3": "month"},
        ], qtype="equipment")
        got = {q["quote_no"]: q for q in database.client_quotes(CLIENT)}
        self.assertIn(QUOTE_EQ, got)
        self.assertFalse(got[QUOTE_EQ]["total_readable"],
                         "a quotation nothing here can total must say so")
        self.assertEqual(1, got[QUOTE_EQ]["lines"],
                         "its line is still readable - only the total is not")

    def test_it_finds_quotations_under_another_spelling_of_the_client(self):
        """The 2026-09-19 bug class, on a new query. An LPO filed under `HDEC`
        was invisible to an invoice for the legal name because the lookup
        compared the typed string."""
        from backend import client_master
        rec = (client_master.get_record(CLIENT) or {}).get("record") or {}
        client_master.save_record({"id": rec.get("id"),
                                   "updated_at": rec.get("updated_at"),
                                   "name": CLIENT, "aliases": [ALIAS]})
        self.assertIn(ALIAS.casefold(),
                      [str(a).casefold() for a in client_master.aliases_for(CLIENT)],
                      "the alias did not register - the rest of this proves nothing")
        got = [q["quote_no"] for q in database.client_quotes(ALIAS)]
        self.assertIn(QUOTE_USD, got,
                      "an alias of the client must reach the same quotations")


class TheQuotationsTotalReachesTheForm(unittest.TestCase):
    def setUp(self):
        cleanup()
        _save_quote(QUOTE_USD, [
            {"item": "Office desk", "unit": "EA", "qty": "60", "rate": "135"},
            {"item": "Office chair", "unit": "EA", "qty": "78", "rate": "75"},
        ])

    def tearDown(self):
        cleanup()

    def test_lpo_from_quote_carries_the_total(self):
        res = api.Api().lpo_from_quote(QUOTE_USD)
        self.assertTrue(res["ok"])
        self.assertAlmostEqual(13950.0, res["total"], places=2)
        self.assertEqual("USD", res["currency"])


class TheSeedForADeliveryNoteOrInvoice(unittest.TestCase):
    """`lpo_document_seed` - the whole hand-off in one call."""

    def setUp(self):
        cleanup()
        _save_quote(QUOTE_USD, [
            {"item": "Office desk", "description": "1650*850*800", "unit": "EA",
             "qty": "60", "rate": "135", "cost": "128.57"},
            {"item": "Office chair", "description": "670*580*1050", "unit": "EA",
             "qty": "78", "rate": "75", "cost": "71.43", "cost_ccy": "USD"},
        ])
        database.save_lpo(
            CLIENT, LPO_NO, total=13950, currency="USD",
            source_quote_no=QUOTE_USD,
            rows=[{"item": "Office desk", "description": "1650*850*800",
                   "unit": "EA", "qty": 60, "rate": "135"},
                  {"item": "Office chair", "description": "670*580*1050",
                   "unit": "EA", "qty": 78, "rate": "75"}])

    def tearDown(self):
        cleanup()

    def seed(self):
        return api.Api().lpo_document_seed(CLIENT, LPO_NO)

    def test_it_carries_the_quotation_number(self):
        """The thing that was typed again on every document made from this PO."""
        self.assertEqual(QUOTE_USD, self.seed()["source_quote_no"])

    def test_every_line_arrives_with_its_cost(self):
        seed = self.seed()
        self.assertTrue(seed["ok"])
        self.assertEqual(2, len(seed["rows"]))
        self.assertEqual(2, seed["costed"])
        self.assertEqual(0, seed["uncosted"])
        by_item = {r["item"]: r for r in seed["rows"]}
        self.assertAlmostEqual(128.57, float(by_item["Office desk"]["cost"]), places=2)
        self.assertAlmostEqual(71.43, float(by_item["Office chair"]["cost"]), places=2)

    def test_a_cost_never_arrives_without_its_currency(self):
        """The Office desk row has no `cost_ccy` of its own - 121 live rows are
        like that. It must still come back with one, resolved from the
        quotation, because a number without its unit is how 84,000 becomes
        84,000 dollars."""
        by_item = {r["item"]: r for r in self.seed()["rows"]}
        for name in ("Office desk", "Office chair"):
            self.assertIn("cost_ccy", by_item[name],
                          f"{name} carries a cost with no currency")
            self.assertIn(by_item[name]["cost_ccy"], ("USD", "IQD"))

    def test_a_dinar_costed_quotation_comes_back_in_dinars(self):
        """Not USD. This is the assertion that makes the one above mean
        something: if the fallback hard-coded "USD", this fails."""
        database.set_cost_currency("quote", QUOTE_IQD, "IQD")
        _save_quote(QUOTE_IQD, [
            {"item": "Safety Sign Board", "unit": "EA", "qty": "1",
             "rate": "370", "cost": "370000"},
        ])
        database.save_lpo(
            CLIENT, LPO_NO, total=370, currency="USD",
            source_quote_no=QUOTE_IQD,
            rows=[{"item": "Safety Sign Board", "unit": "EA", "qty": 1, "rate": "370"}])
        row = self.seed()["rows"][0]
        self.assertAlmostEqual(370000.0, float(row["cost"]), places=2)
        self.assertEqual("IQD", row["cost_ccy"],
                         "the quotation's own cost currency, not a guess")

    def test_quantities_are_what_is_still_owed(self):
        """Invoicing 60 desks again when 60 are delivered is the mistake this
        rule exists to avoid."""
        database.save_dn_record(
            dn_no=f"DN-{TAG}-1", dn_date="2/9/2026", year=2026, month=9,
            client_name=CLIENT, lpo_no=LPO_NO, client_address="",
            include_prices=1, pdf_path=f"DN-{TAG}-1.pdf",
            rows=[{"item": "Office desk", "unit": "EA", "qty": 20, "rate": "135"}])
        by_item = {r["item"]: r for r in self.seed()["rows"]}
        self.assertAlmostEqual(40.0, float(by_item["Office desk"]["qty"]), places=2,
                               msg="60 ordered minus 20 delivered")
        self.assertAlmostEqual(78.0, float(by_item["Office chair"]["qty"]), places=2)

    def test_an_lpo_citing_nothing_of_ours_still_seeds_its_lines(self):
        """CSSP-RENT-0002 cites QUT-146, HDEC's own reference. A PO that cannot
        be turned into a document is worse than one with no costs."""
        database.save_lpo(
            CLIENT, LPO_NO, total=850, currency="USD", source_quote_no="QUT-146",
            rows=[{"item": "Rental of Generators", "unit": "Set-Month",
                   "qty": 14, "rate": "850"}])
        seed = self.seed()
        self.assertTrue(seed["ok"])
        self.assertEqual(1, len(seed["rows"]))
        self.assertEqual(1, seed["uncosted"])
        self.assertNotIn("cost", seed["rows"][0])

    def test_an_lpo_that_is_not_on_file_says_so(self):
        seed = api.Api().lpo_document_seed(CLIENT, "NOPE")
        self.assertFalse(seed["ok"])
        self.assertTrue(seed.get("error"))
        self.assertEqual([], seed["rows"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
