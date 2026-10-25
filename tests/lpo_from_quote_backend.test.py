"""Handing a quotation's lines over to the LPO form.

This is what makes filing CSSP-ADM-0010 a tick box instead of four lines of
retyping: the PO names QUT-MT-HDEC-029 on its face, and we already hold it.

Isolation: see the note in `client_lpo_save.test.py`. The profile database is
asserted, not assumed.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, profile_manager  # noqa: E402

# QUT-MT-HDEC-029 as it really is, including the base64 photo a quotation row
# can carry. That image is the reason this method exists rather than the form
# calling load_quote directly.
QUOTE_ROWS = [
    {"item": "Office desk", "description": "1650*850*800", "unit": "EA",
     "qty": 60, "rate": "135", "cost": 128.57,
     "image": "data:image/jpeg;base64," + ("A" * 4000)},
    {"item": "3-drawer enclosed type", "description": "800*410*1200", "unit": "EA",
     "qty": 7, "rate": "128", "cost": 128.0, "cost_qty": 1.0},
]


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


class LpoFromQuote(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM quotes")
        database.save_quote_record(
            quote_no="QUT-MT-HDEC-029", quote_date="6/7/2026", quote_type="MT",
            year=2026, month=7, client_name="HDEC", rfq_no="", client_address="",
            pdf_path="q.pdf", rows=QUOTE_ROWS, terms=[])
        self.bridge = api.Api()

    def test_the_quoted_lines_come_back(self):
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        self.assertTrue(res["ok"], res)
        self.assertEqual(2, len(res["rows"]))
        self.assertEqual("Office desk", res["rows"][0]["item"])
        self.assertEqual(60, res["rows"][0]["qty"])
        self.assertEqual("135", res["rows"][0]["rate"])

    def test_the_description_carries_the_specification(self):
        """Their PO reprints these dimensions verbatim under "Certification"."""
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        self.assertEqual("1650*850*800", res["rows"][0]["description"])

    def test_images_are_stripped(self):
        """A quotation row can hold a base64 photo. It has no business in an LPO
        record and would make the bridge payload enormous - the live
        QUT-MT-HDEC-029 is 216KB of mostly image."""
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        for row in res["rows"]:
            self.assertNotIn("image", row)
        self.assertLess(len(str(res)), 2000, "the payload is still carrying a photo")

    def test_costing_is_not_handed_to_the_client_record(self):
        """`cost` is what WE pay. It must never ride along into a document
        describing what the client ordered."""
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        for row in res["rows"]:
            self.assertNotIn("cost", row)
            self.assertNotIn("cost_qty", row)

    def test_the_client_comes_back_so_the_form_can_check_it(self):
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        self.assertEqual("HDEC", res["client_name"])
        self.assertEqual("QUT-MT-HDEC-029", res["quote_no"])

    def test_a_quotation_we_do_not_hold_says_so_by_name(self):
        """CSSP-RENT-0002 cites QUT-146, which is HDEC's own reference."""
        res = self.bridge.lpo_from_quote("QUT-146")
        self.assertFalse(res["ok"])
        self.assertIn("QUT-146", res["error"],
                      "the message must name what was asked for")

    def test_asking_for_nothing_is_refused_not_crashed(self):
        self.assertFalse(self.bridge.lpo_from_quote("")["ok"])

    def test_the_rows_survive_the_round_trip_into_a_saved_lpo(self):
        """The whole point: pull, then file, and the order is on record."""
        res = self.bridge.lpo_from_quote("QUT-MT-HDEC-029")
        from backend import client_master
        if not client_master.get_record("HDEC").get("record"):
            client_master.save_record({"name": "HDEC"})
        saved = self.bridge.save_lpo({
            "client_name": "HDEC", "lpo_no": "CSSP-ADM-0010",
            "kind": "materials", "total": 18390, "rows": res["rows"]})
        self.assertTrue(saved.get("ok"), saved)
        out = database.lpo_fulfillment("HDEC", "CSSP-ADM-0010")
        self.assertEqual(["Office desk", "3-drawer enclosed type"],
                         [l["item"] for l in out["lines"]])
        self.assertEqual(60, out["lines"][0]["ordered"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
