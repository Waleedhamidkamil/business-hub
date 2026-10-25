"""What is still owed on an LPO, line by line.

CSSP-ADM-0010 ordered 60 office desks. Deliver 40 and the app should say 20 are
still owed; today you would open the PDF and compare by eye.

Lines are matched on the item NAME. An earlier version keyed on item plus
description, on the reasoning that both sides came from the same quotation -
and against the real documents every line of CSSP-ADM-0010 came back "never
ordered", because the dimensions move between columns from one document type to
the next. The specification is still read, from `description` AND `unit`
together, but only to tell apart two ordered lines that share a name.

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

from backend import config, database, profile_manager  # noqa: E402

# The four lines CSSP-ADM-0010 actually ordered, copied from our own
# QUT-MT-HDEC-029. Two of them are enough for the arithmetic here.
ORDERED = [
    {"item": "Office desk", "description": "1650*850*800", "unit": "EA",
     "qty": 60, "rate": "135"},
    {"item": "Office chair", "description": "670*580*1050", "unit": "EA",
     "qty": 78, "rate": "75"},
]


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


class LineMatching(unittest.TestCase):
    r"""Corrected 2026-09-11 against the real documents.

    The first version keyed a line on item PLUS description, reasoning that both
    sides came from the same quotation. Every line of CSSP-ADM-0010 then matched
    as "never ordered", because the dimensions MOVE between columns:
    QUT-MT-HDEC-029 keeps "1650*850*800" in `description`, while DN-HDEC-046 and
    INV-MT-HDEC-043 keep it inside `unit` as "EA\n1650*850*800" and leave
    `description` empty.

    So the NAME identifies a line, and the specification is consulted only to
    tell apart two lines that share one.
    """

    def test_spacing_and_case_do_not_split_a_line(self):
        a = database._line_key({"item": "  office DESK ", "description": "1650*850*800"})
        b = database._line_key({"item": "Office desk", "description": ""})
        self.assertEqual(a, b)

    def test_a_stray_newline_does_not_split_a_line(self):
        self.assertEqual("office desk", database._line_key({"item": "Office\ndesk"}))

    def test_the_name_alone_identifies_a_line(self):
        """Because the specification is not reliably in the same column."""
        quoted = {"item": "Office desk", "description": "1650*850*800", "unit": "EA"}
        shipped = {"item": "Office desk", "description": "", "unit": "EA\n1650*850*800"}
        self.assertEqual(database._line_key(quoted), database._line_key(shipped))

    def test_the_specification_is_read_from_either_column(self):
        """Which is what lets it tell two same-named lines apart at all."""
        quoted = {"item": "Office desk", "description": "1650*850*800", "unit": "EA"}
        shipped = {"item": "Office desk", "description": "", "unit": "EA\n1650*850*800"}
        self.assertEqual(database._line_spec(quoted), database._line_spec(shipped),
                         "the same words in different columns are the same spec")

    def test_two_sizes_of_the_same_item_are_still_told_apart(self):
        """The name leads, but a real order CAN carry one name twice."""
        ordered = [
            {"item": "Office desk", "description": "1650*850*800", "unit": "EA",
             "qty": 60, "rate": "135"},
            {"item": "Office desk", "description": "1400*700*750", "unit": "EA",
             "qty": 10, "rate": "110"},
        ]
        delivered = [[{"item": "Office desk", "unit": "EA\n1400*700*750", "qty": 10}]]
        out = database._fulfillment_from_rows(ordered, delivered, [])
        self.assertEqual(0, out["lines"][0]["delivered"], "the big desks are untouched")
        self.assertEqual(10, out["lines"][1]["delivered"], "the small ones arrived")


class TheRealDocuments(unittest.TestCase):
    r"""The exact rows stored for CSSP-ADM-0010's chain, as they really are.

    This is the case the first implementation got wrong: all four lines reported
    zero delivered and all four turned up as "never ordered". It is a fixture
    rather than a live read so it keeps working after the live data moves on.
    """

    QUOTED = [
        {"item": "Office desk", "description": "1650*850*800", "unit": "EA", "qty": 60, "rate": "135"},
        {"item": "3-drawer mobile pedestal under desk", "description": "400*520*600", "unit": "EA", "qty": 60, "rate": "64"},
        {"item": "Office chair", "description": "670*580*1050", "unit": "EA", "qty": 78, "rate": "75"},
        {"item": "Meeting Table-1", "description": "2100*900*720", "unit": "EA", "qty": 3, "rate": "200"},
    ]
    # DN-HDEC-046: no description at all, dimensions inside `unit`.
    DELIVERED = [[
        {"item": "Office desk", "unit": "EA\n1650*850*800", "qty": 60, "rate": "135"},
        {"item": "3-drawer mobile pedestal under desk", "unit": "EA\n400*520*600", "qty": 60, "rate": "64"},
        {"item": "Office chair", "unit": "EA\n670*580*1050", "qty": 78, "rate": "75"},
        {"item": "Meeting Table-1", "unit": "EA\n2100*900*720", "qty": 3, "rate": "200"},
    ]]
    # INV-MT-HDEC-043: an EMPTY description, dimensions inside `unit`.
    INVOICED = [[
        {"item": "Office desk", "description": "", "unit": "EA\n1650*850*800", "qty": 60, "rate": "135"},
        {"item": "3-drawer mobile pedestal under desk", "description": "", "unit": "EA\n400*520*600", "qty": 60, "rate": "64"},
        {"item": "Office chair", "description": "", "unit": "EA\n670*580*1050", "qty": 78, "rate": "75"},
        {"item": "Meeting Table-1", "description": "", "unit": "EA\n2100*900*720", "qty": 3, "rate": "200"},
    ]]

    def test_the_whole_order_is_matched(self):
        out = database._fulfillment_from_rows(self.QUOTED, self.DELIVERED, self.INVOICED)
        self.assertEqual([], out["extra"],
                         "these are the very lines that were ordered")
        self.assertEqual([0, 0, 0, 0], [l["remaining"] for l in out["lines"]])
        self.assertTrue(out["complete"])

    def test_a_partial_real_delivery_reads_correctly(self):
        half = [[dict(self.DELIVERED[0][0], qty=40)]]
        out = database._fulfillment_from_rows(self.QUOTED, half, [])
        self.assertEqual(20, out["lines"][0]["remaining"],
                         "40 of 60 desks delivered leaves 20 owed")
        self.assertEqual([], out["extra"])


class Fulfillment(unittest.TestCase):
    def test_nothing_delivered_means_everything_is_owed(self):
        out = database._fulfillment_from_rows(ORDERED, [], [])
        self.assertEqual([60, 78], [l["remaining"] for l in out["lines"]])
        self.assertFalse(out["complete"])

    def test_a_partial_delivery_is_counted(self):
        delivered = [[{"item": "Office desk", "description": "1650*850*800", "qty": 40}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(40, out["lines"][0]["delivered"])
        self.assertEqual(20, out["lines"][0]["remaining"])
        self.assertEqual(78, out["lines"][1]["remaining"],
                         "an untouched line is still owed in full")

    def test_deliveries_across_several_notes_add_up(self):
        delivered = [
            [{"item": "Office desk", "description": "1650*850*800", "qty": 40}],
            [{"item": "Office desk", "description": "1650*850*800", "qty": 20}],
        ]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(60, out["lines"][0]["delivered"])
        self.assertEqual(0, out["lines"][0]["remaining"])

    def test_over_delivery_does_not_go_negative(self):
        """Owing minus five desks is not a thing anyone needs to read."""
        delivered = [[{"item": "Office desk", "description": "1650*850*800", "qty": 65}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(0, out["lines"][0]["remaining"])
        self.assertEqual(65, out["lines"][0]["delivered"],
                         "what was actually shipped is still reported honestly")

    def test_a_blank_spacer_row_is_ignored(self):
        delivered = [[{"item": "", "description": "", "qty": 0},
                      {"item": "Office desk", "description": "1650*850*800", "qty": 10}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(10, out["lines"][0]["delivered"])
        self.assertEqual([], out["extra"], "a spacer row is not an extra item")

    def test_a_line_the_lpo_never_ordered_is_reported_not_swallowed(self):
        """A delivery note carrying something the LPO never ordered is worth
        naming - quietly dropping it is how a mistake stays invisible."""
        delivered = [[{"item": "Filing cabinet", "description": "x", "qty": 2}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(["Filing cabinet"], [e["item"] for e in out["extra"]])

    def test_invoiced_is_tracked_separately_from_delivered(self):
        both = [[{"item": "Office desk", "description": "1650*850*800", "qty": 60}]]
        out = database._fulfillment_from_rows(ORDERED, both, [])
        self.assertEqual(60, out["lines"][0]["delivered"])
        self.assertEqual(0, out["lines"][0]["invoiced"],
                         "delivered is not invoiced - the whole point of the chain")

    def test_complete_only_when_every_line_is_delivered_and_invoiced(self):
        both = [[{"item": "Office desk", "description": "1650*850*800", "qty": 60},
                 {"item": "Office chair", "description": "670*580*1050", "qty": 78}]]
        self.assertFalse(database._fulfillment_from_rows(ORDERED, both, [])["complete"],
                         "delivered but not invoiced is not complete")
        self.assertTrue(database._fulfillment_from_rows(ORDERED, both, both)["complete"])

    def test_one_line_short_is_not_complete(self):
        part = [[{"item": "Office desk", "description": "1650*850*800", "qty": 60}]]
        self.assertFalse(database._fulfillment_from_rows(ORDERED, part, part)["complete"])

    def test_an_lpo_with_no_recorded_lines_reports_nothing_rather_than_zero(self):
        """rows_json NULL means we never recorded the order. Reporting
        "0 of 0 delivered, complete" would be a confident lie."""
        out = database._fulfillment_from_rows(None, [], [])
        self.assertEqual([], out["lines"])
        self.assertIsNone(out["complete"])

    def test_never_recorded_and_ordered_nothing_are_different_answers(self):
        """Pins the distinction, not just the None case.

        `None` and `[]` both look falsy, so the test above passed against a
        mutation that collapsed them (`if not ordered`). Only comparing the two
        separates "we never typed the order in" from "this PO ordered nothing":
        the first is unknown, the second is known and simply not complete.
        """
        unknown = database._fulfillment_from_rows(None, [], [])
        empty = database._fulfillment_from_rows([], [], [])
        self.assertIsNone(unknown["complete"], "an unrecorded order is unknown")
        self.assertIs(False, empty["complete"], "an empty order is known, and not complete")

    def test_an_unreadable_quantity_counts_as_nothing_not_a_crash(self):
        delivered = [[{"item": "Office desk", "description": "1650*850*800",
                       "qty": "about forty"}]]
        out = database._fulfillment_from_rows(ORDERED, delivered, [])
        self.assertEqual(0, out["lines"][0]["delivered"])


class FulfillmentFromTheDatabase(unittest.TestCase):
    """The same arithmetic, but reading the real linked documents."""

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM dns")
            conn.execute("DELETE FROM invoices")
        self.assertTrue(database.save_lpo(
            client_name="HDEC", lpo_no="CSSP-ADM-0010", kind="materials",
            total=18390, currency="USD", rows=ORDERED)["ok"])

    def file_dn(self, dn_no, rows, lpo_no="CSSP-ADM-0010", client="HDEC"):
        database.save_dn_record(dn_no=dn_no, dn_date="9/7/2026", year=2026, month=7,
                                client_name=client, lpo_no=lpo_no, client_address="",
                                include_prices=0, pdf_path=f"{dn_no}.pdf", rows=rows)

    def test_a_delivery_note_against_the_lpo_counts(self):
        self.file_dn("DN-HDEC-046",
                     [{"item": "Office desk", "description": "1650*850*800", "qty": 40}])
        out = database.lpo_fulfillment("HDEC", "CSSP-ADM-0010")
        self.assertEqual(40, out["lines"][0]["delivered"])
        self.assertEqual(20, out["lines"][0]["remaining"])

    def test_a_delivery_note_for_another_lpo_does_not_count(self):
        self.file_dn("DN-HDEC-047",
                     [{"item": "Office desk", "description": "1650*850*800", "qty": 40}],
                     lpo_no="CSSP-ADM-0017")
        out = database.lpo_fulfillment("HDEC", "CSSP-ADM-0010")
        self.assertEqual(0, out["lines"][0]["delivered"])

    def test_a_delivery_note_marked_na_does_not_count(self):
        """63 of 64 real delivery notes say N/A, so this is the common shape.

        What excludes it is the matching KEY, not the SQL filter: ("hdec","n/a")
        is simply not ("hdec","cssp-adm-0010"). Removing the `NOT IN ('','N/A')`
        clause leaves this test green, which is correct and worth saying out
        loud - that clause is a cheap pre-filter here, and load-bearing only in
        `lpo_usage_map`, where it would otherwise build an "N/A" bucket.
        """
        self.file_dn("DN-HDEC-048",
                     [{"item": "Office desk", "description": "1650*850*800", "qty": 40}],
                     lpo_no="N/A")
        out = database.lpo_fulfillment("HDEC", "CSSP-ADM-0010")
        self.assertEqual(0, out["lines"][0]["delivered"])

    def test_an_lpo_that_is_not_on_file_reports_nothing(self):
        out = database.lpo_fulfillment("HDEC", "NOPE-0001")
        self.assertEqual([], out["lines"])
        self.assertIsNone(out["complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
