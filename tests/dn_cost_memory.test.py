"""DN item memory: costs are remembered WITH their currency, and one
suggestion is offered per name.

Two defects are pinned here, both found by asking a plain question - "if I
enter a cost for Celery in a DN, does another DN get it?":

  1. `save_dn_record` stored the row VERBATIM, so a cost typed in dinars was
     remembered as a bare number with nothing recording the unit. The live
     catalog held 2002 / 504 / 49994 for items whose documents read 1.43 /
     0.36 / 35.71. Autofilling one into a dollar cell reads as $2,002.
  2. `suggest_dn_items` never collapsed the pre-`_SIG_IGNORE` variant rows, so
     "Celery" offered five identical-looking entries. Quotations already
     collapsed; DN and invoices did not.

TESTS HIT THE LIVE DATABASE - there is no isolation here. Every row this file
writes uses the sentinel names below, and it only ever deletes those.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import api, database, master_items  # noqa: E402

# Sentinels. Nothing in the real catalog starts with this.
PFX = "ZZTest DN Cost "
CELERY = PFX + "Celery"
STICKY = PFX + "Sticky"


# The DN numbers this file writes into the LIVE profile DB. Left behind they
# would surface as real delivery notes in the To-Do list and the insights, so
# every one of them is removed again in teardown.
SENTINEL_DNS = ("ZZTEST-DN-PAYLOAD", "ZZTEST-DN-VERBATIM", "ZZTEST-DN-BOOM",
                "ZZTEST-DN-CCY")


def _wipe():
    with database._connect_shared() as conn:
        try:
            conn.execute("DELETE FROM dn_items WHERE name LIKE ? || '%'", (PFX,))
        except sqlite3.OperationalError:
            pass
        conn.execute("DELETE FROM clients WHERE name LIKE 'ZZTest %'")
        # A document save also writes the item RECORD (backend/master_items.py).
        # Cleaning only the catalog rows left these on the Master Data screen.
        conn.execute("DELETE FROM master_items "
                     "WHERE name_en LIKE ? || '%' OR name_ar LIKE ? || '%'",
                     (PFX, PFX))
    with database._connect() as conn:
        marks = ",".join("?" * len(SENTINEL_DNS))
        conn.execute(f"DELETE FROM dns WHERE dn_no IN ({marks})", SENTINEL_DNS)
        conn.execute(
            f"DELETE FROM doc_cost_meta WHERE kind = 'dn' AND doc_no IN ({marks})",
            SENTINEL_DNS)


def _mine(rows):
    """Only this file's sentinel rows.

    Autocomplete searches every catalog and matches on WORDS since 2026-08-16,
    so `PFX` ("ZZTest DN Cost ") now legitimately also matches real catalog
    items containing "dn" or "cost" - `DN-TDC-020` among them. These tests are
    about how VARIANTS OF ONE ITEM collapse, so they assert over their own
    rows and let the rest of the catalog answer too.
    """
    return [r for r in rows if str(r.get("name", "")).startswith(PFX)]


def _records(name):
    """The item record a document save writes now (backend/master_items.py).
    The four catalog tables (`dn_items`, …) it used to write are retired, so a
    saved cost/picture lives in the record's own columns, one row per item."""
    conn = master_items._connect()
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM master_items WHERE name_en = ? OR name_ar = ?",
            (name, name)).fetchall()
    finally:
        conn.close()


class DnCostCurrency(unittest.TestCase):
    """A remembered DN cost must carry its unit or not be remembered at all."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_an_iqd_cost_is_remembered_in_dollars(self):
        build = api._dn_item_payload({"dn_no": "", "cost_currency": "IQD",
                                      "cost_iqd_per_usd": 1400})
        data = build({"item": CELERY, "unit": "KG", "qty": 2, "rate": 0.94,
                      "cost": "2002"})
        self.assertNotIn("cost", data,
                         "the raw cost must be dropped - it reads as dollars")
        self.assertAlmostEqual(data["cost_usd"], 1.43, places=2)

    def test_a_usd_cost_is_remembered_unchanged(self):
        build = api._dn_item_payload({"dn_no": "", "cost_currency": "USD",
                                      "cost_iqd_per_usd": 1400})
        data = build({"item": CELERY, "unit": "KG", "cost": "1.43"})
        self.assertAlmostEqual(data["cost_usd"], 1.43, places=2)
        self.assertNotIn("cost", data)

    def test_the_payload_currency_beats_the_stored_one(self):
        """dn.js calls set_cost_currency AFTER the save, so reading the stored
        flag would remember one save behind - a dinar cost as dollars."""
        database.set_cost_currency("dn", "ZZTEST-DN-CCY", "USD")
        build = api._dn_item_payload({"dn_no": "ZZTEST-DN-CCY",
                                      "cost_currency": "IQD",
                                      "cost_iqd_per_usd": 1400})
        data = build({"item": CELERY, "cost": "1400"})
        self.assertAlmostEqual(data["cost_usd"], 1.0, places=3)

    def test_a_row_with_no_cost_records_no_cost(self):
        build = api._dn_item_payload({"dn_no": "", "cost_currency": "USD"})
        data = build({"item": CELERY, "unit": "KG", "qty": 3})
        self.assertNotIn("cost_usd", data)
        self.assertEqual(data.get("unit"), "KG")

    def test_the_name_is_not_part_of_the_payload(self):
        build = api._dn_item_payload({"dn_no": "", "cost_currency": "USD"})
        self.assertNotIn("item", build({"item": CELERY, "unit": "KG"}))

    def test_save_dn_record_uses_the_injected_payload(self):
        """The wiring, not just the builder: a DN saved in IQD must leave the
        converted cost on the item RECORD (the four catalogs are retired, so the
        payload's `cost_native` lands in the record's own column)."""
        seen = []

        def build(row):
            seen.append(row)
            return {"unit": row.get("unit"), "cost_native": 1.43,
                    "cost_native_ccy": "USD"}

        database.save_dn_record(
            dn_no="ZZTEST-DN-PAYLOAD", dn_date="01/01/2026", year=2026, month=1,
            client_name="ZZTest Client", lpo_no="", client_address="",
            include_prices=True, pdf_path="", item_payload=build,
            rows=[{"item": CELERY, "unit": "KG", "cost": "2002"}],
        )
        self.assertEqual(len(seen), 1, "the callback must be reached")
        recs = _records(CELERY)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["cost_native"], 1.43)
        self.assertEqual(recs[0]["cost_native_ccy"], "USD")

    # REMOVED 2026-08-20: test_without_a_payload_the_row_is_stored_verbatim
    # tested the retired `dn_items` catalog's verbatim `data_json` fallback for
    # payload-less callers. The record has no verbatim blob - it stores known
    # columns only - so that storage shape no longer exists.

    def test_a_failing_payload_never_fails_the_save(self):
        def boom(row):
            raise RuntimeError("no")

        database.save_dn_record(
            dn_no="ZZTEST-DN-BOOM", dn_date="01/01/2026", year=2026, month=1,
            client_name="ZZTest Client", lpo_no="", client_address="",
            include_prices=True, pdf_path="", item_payload=boom,
            rows=[{"item": CELERY, "unit": "KG"}],
        )
        self.assertEqual(len(_records(CELERY)), 1, "the item is still remembered")


class StickyDetails(unittest.TestCase):
    """A cost, once learned, behaves like the price and the unit: re-saving
    the item with that cell blank must not erase it."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def test_a_blank_cost_does_not_erase_a_remembered_one(self):
        # CHANGED 2026-08-20: costs live in the record's own columns now, as
        # cost_native + currency (cost_usd is not a record field).
        database.remember_dn_item(STICKY, {"unit": "EA", "cost_native": 12.5,
                                           "cost_native_ccy": "USD"})
        database.remember_dn_item(STICKY, {"unit": "EA"})
        recs = _records(STICKY)
        self.assertEqual(len(recs), 1, "same item - one record, bumped")
        self.assertEqual(recs[0]["cost_native"], 12.5)

    # REMOVED 2026-08-20: test_a_new_cost_replaces_the_old_one. The item record
    # LEARNS a cost once; a later document save no longer overwrites it (a
    # correction is made on the Master Data screen). The retired catalog's
    # "explicit new cost replaces" behaviour no longer applies to a save.

    def test_a_picture_survives_a_save_without_one(self):
        database.remember_dn_item(STICKY, {"unit": "EA",
                                           "image_refs": {"image": "abc123"}})
        database.remember_dn_item(STICKY, {"unit": "EA"})
        self.assertIn("abc123", _records(STICKY)[0]["image_refs"] or "")

    def test_stickiness_does_not_leak_across_variants(self):
        """A different spec is a different record; it must not inherit the
        other's cost."""
        database.remember_dn_item(STICKY, {"unit": "EA", "cost_native": 12.5,
                                           "cost_native_ccy": "USD"})
        database.remember_dn_item(STICKY, {"unit": "BOX"})
        recs = _records(STICKY)
        self.assertEqual(len(recs), 2)
        box = [r for r in recs if r["unit_en"] == "BOX"][0]
        self.assertIsNone(box["cost_native"])

    def test_the_invoice_side_is_sticky_too(self):
        database.remember_inv_item("materials", STICKY,
                                   {"unit": "EA", "cost_native": 7.0,
                                    "cost_native_ccy": "USD"})
        database.remember_inv_item("materials", STICKY, {"unit": "EA"})
        self.assertEqual(_records(STICKY)[0]["cost_native"], 7.0)


class OneSuggestionPerName(unittest.TestCase):
    """Five Celerys in the dropdown, all saying the same thing."""

    def setUp(self):
        _wipe()
        self.addCleanup(_wipe)

    def _seed_legacy_variants(self):
        """The five rows the live catalog held for "Celery" - saved the way the
        app really saves them. They used to be inserted past
        `remember_dn_item`; a suggestion is built from the item RECORD now
        (2026-08-19), and a catalog row nothing ever saved has no record, so
        writing past the save path tested a state the app cannot reach."""
        for payload in (
                {"description": "", "qty": "5", "unit": "KG"},
                {"description": "", "qty": "2", "rate": "0.94", "unit": ""},
                {"qty": 5, "rate": 0.94, "unit": "KG"},
                {"qty": 2, "rate": 0.94, "unit": "KG"},
                {"description": "", "qty": 3, "rate": 0.94, "unit": "KG"}):
            database.remember_dn_item(CELERY, payload)

    def test_five_REDUNDANT_variants_offer_one_suggestion(self):
        """The five Celery rows differ only in quantity and price, which
        `_data_sig` already ignores - they are the same item five times."""
        self._seed_legacy_variants()
        # ONE record, not five: the item record dedups by (name, description,
        # unit), and qty/rate are not identity. A blank unit contradicts nothing,
        # so even the unit-less save folds into the same Celery. (The four
        # catalog tables the old assertion read are retired.)
        self.assertEqual(len(_records(CELERY)), 1, "one Celery record is stored")
        out = _mine(database.suggest_dn_items(CELERY))
        self.assertEqual({s["name"] for s in out}, {CELERY})
        # ONE suggestion (2026-08-19). Four of the five are the same item -
        # only qty and rate differ, which the signature ignores. The fifth was
        # saved with NO unit, and under the record's rule a missing unit
        # contradicts nothing, so it is the same Celery rather than a second
        # one. That is the improvement: leaving the unit blank once should not
        # put a duplicate in the dropdown for ever.
        #
        # Genuine variants still stay apart - see the next test, where three
        # real diameters give three suggestions.
        self.assertEqual(len(out), 1, f"expected one Celery, got {out}")
        self.assertEqual(out[0]["data"].get("unit"), "KG",
                         "and it keeps the unit it was actually taught")

    def test_GENUINE_variants_are_all_offered(self):
        """The other half of the rule, and the reason it is keyed on the spec
        rather than the name: "Hemp rope" comes in Diameter 12mm, phi15mm and
        phi20mm. Collapsing by name left ONE rope in the dropdown and there was
        no way to pick the others."""
        for desc in ("Diameter 12mm", "φ15mm", "φ20mm"):
            database.remember_dn_item(CELERY, {"description": desc,
                                               "unit": "meter"})
        out = _mine(database.suggest_dn_items(CELERY))
        descs = {s["data"].get("description", "") for s in out}
        self.assertEqual(descs, {"Diameter 12mm", "φ15mm", "φ20mm"},
                         f"every real variant must be offered, got {descs}")

    def test_nothing_is_deleted_to_achieve_it(self):
        """The collapse is a display rule. Destroying rows would take the
        pictures and costs attached to the losers with them."""
        self._seed_legacy_variants()
        database.suggest_dn_items(CELERY)
        self.assertEqual(len(_records(CELERY)), 1)

    def test_the_entry_carrying_a_cost_wins(self):
        """Recalling the cost is the point of picking the item, so the useful
        row beats the merely busiest one."""
        database.remember_dn_item(CELERY, {"unit": "KG"})
        database.remember_dn_item(CELERY, {"unit": "KG", "cost_native": 1.43,
                                           "cost_native_ccy": "USD"})
        out = _mine(database.suggest_dn_items(CELERY))
        self.assertEqual(len(out), 1, "one item, one suggestion")
        self.assertEqual(out[0]["data"].get("cost_native"), 1.43,
                         "the cost the item knows must come with it")

    def test_a_picture_beats_a_bare_row(self):
        database.remember_dn_item(CELERY, {"unit": "KG"})
        database.remember_dn_item(CELERY, {"unit": "KG",
                                           "image_refs": {"image": "r1"}})
        out = _mine(database.suggest_dn_items(CELERY))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["data"].get("image_refs"))

    def test_distinct_items_are_not_merged(self):
        """The collapse is per NAME. Two different items sharing a prefix must
        both survive, or the dropdown becomes useless."""
        for n in (CELERY, CELERY + " Leaf"):
            database.remember_dn_item(n, {"unit": "KG"})
        names = {s["name"] for s in _mine(database.suggest_dn_items(PFX))}
        self.assertEqual(names, {CELERY, CELERY + " Leaf"})

    def test_the_limit_is_honoured_after_collapsing(self):
        """Over-fetch then collapse - stopping at `limit` in SQL would return
        one name where the user asked for eight."""
        with database._connect_shared() as conn:
            for i in range(12):
                for sig in range(3):        # 3 legacy variants each
                    database.remember_dn_item(f"{PFX}Item{i:02d}",
                                              {"unit": "KG"})
        out = database.suggest_dn_items(PFX, limit=8)
        self.assertEqual(len(out), 8)
        self.assertEqual(len({s["name"] for s in out}), 8, "8 DISTINCT names")

    def test_a_suggestion_still_carries_its_data(self):
        """`rate` was the quotation column's own name for a price. The item
        holds one price now, so that is what travels (2026-08-19)."""
        database.remember_dn_item(CELERY, {"unit": "KG", "price_native": 0.94,
                                           "price_native_ccy": "USD"})
        out = database.suggest_dn_items(CELERY)
        self.assertEqual(out[0]["data"]["unit"], "KG")
        self.assertEqual(out[0]["data"]["price_native"], 0.94)

    def test_an_empty_prefix_offers_nothing(self):
        self.assertEqual(database.suggest_dn_items(""), [])

    def test_the_invoice_dropdown_collapses_the_same_way(self):
        with database._connect_shared() as conn:
            try:
                conn.execute("DELETE FROM inv_items WHERE name LIKE ? || '%'", (PFX,))
            except sqlite3.OperationalError:
                pass
        for _ in range(3):
            database.remember_inv_item("materials", CELERY, {"unit": "KG"})
        try:
            out = _mine(database.suggest_inv_items("materials", CELERY))
            self.assertEqual(len(out), 1)
        finally:
            with database._connect_shared() as conn:
                try:
                    conn.execute("DELETE FROM inv_items WHERE name LIKE ? || '%'", (PFX,))
                except sqlite3.OperationalError:
                    pass

    def test_the_invoice_type_no_longer_segregates(self):
        """REVERSED on 2026-08-16, deliberately.

        This used to assert `suggest_inv_items("manpower", CELERY) == []` -
        that an item remembered under one invoice type was invisible under
        another. That was the bug, not the rule: "I tried to quote a Traffic
        Cone which I have invoiced before but it didn't show up". The same
        walls stood between the four catalogs and between quote types.

        Every catalog is now searched and the caller's own type merely RANKS
        first (pinned by tests/item_suggestions_universal.test.py). The
        collapse this class is really about is unchanged.
        """
        with database._connect_shared() as conn:
            try:
                conn.execute("DELETE FROM inv_items WHERE name LIKE ? || '%'", (PFX,))
            except sqlite3.OperationalError:
                pass
        database.remember_inv_item("materials", CELERY, {"unit": "KG"})
        try:
            self.assertEqual(len(_mine(database.suggest_inv_items("materials", CELERY))), 1)
            self.assertEqual(len(_mine(database.suggest_inv_items("manpower", CELERY))), 1,
                             "an item invoiced as materials must be reachable "
                             "from a manpower invoice too")
        finally:
            with database._connect_shared() as conn:
                try:
                    conn.execute("DELETE FROM inv_items WHERE name LIKE ? || '%'", (PFX,))
                except sqlite3.OperationalError:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
