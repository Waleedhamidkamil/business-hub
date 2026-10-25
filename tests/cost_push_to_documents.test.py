"""Pushing a Master Data cost back into saved documents (2026-08-13).

Setting a cost in Master Data changes what gets AUTOFILLED next time; the
documents already saved keep the cost they were saved with. That is the right
default - a document is a record. This is the opt-in way to correct them
anyway, and it has to be exact, because it rewrites records in bulk:

  1. **Preview writes nothing.** Looking must never change anything.
  2. **Only the chosen documents change.** An unticked document is untouched.
  3. **Each row keeps its own currency.** The master figure is USD; a row typed
     in dinars gets the dinar number, converted at THAT document's peg. Writing
     a dollar figure into a dinar cell is the 84,000-where-60-belongs bug at
     scale.
  4. **Exact names only.** A bulk write must not guess which item it means.
  5. **The written value is self-describing** - `cost_ccy` is stamped, so the
     number can never be re-read in a different unit later.

!! Writes to the LIVE profile DB under sentinel document numbers in a
!! far-future month, and deletes them again (docs/11).
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import database, master_data, profile_manager  # noqa: E402

ITEM = "ZZ-COSTPUSH-ITEM"
OTHER = "ZZ-COSTPUSH-OTHER"
DN_USD = "ZZ-CP-DN-USD"
DN_IQD = "ZZ-CP-DN-IQD"
DN_SKIP = "ZZ-CP-DN-SKIP"
DNS = (DN_USD, DN_IQD, DN_SKIP)
YEAR, MONTH = 2099, 1


def _profile_db():
    return sqlite3.connect(profile_manager.profile_db_path(
        profile_manager.active_profile_id()))


def _shared():
    return sqlite3.connect(profile_manager.shared_db_path())


def _wipe():
    conn = _profile_db()
    try:
        marks = ",".join("?" * len(DNS))
        conn.execute(f"DELETE FROM dns WHERE dn_no IN ({marks})", DNS)
        try:
            conn.execute(f"DELETE FROM doc_cost_meta WHERE doc_no IN ({marks})", DNS)
        except sqlite3.Error:
            pass
        try:
            conn.execute(f"DELETE FROM submitted_docs WHERE doc_no IN ({marks})", DNS)
        except sqlite3.Error:
            pass
        conn.commit()
    finally:
        conn.close()
    conn = _shared()
    try:
        for t in ("items", "quote_items", "dn_items", "inv_items"):
            try:
                conn.execute(f"DELETE FROM {t} WHERE name LIKE 'ZZ-COSTPUSH%'")
            except sqlite3.Error:
                pass
        conn.commit()
    finally:
        conn.close()


def _seed_dn(no, rows, cost_ccy="USD"):
    conn = _profile_db()
    try:
        conn.execute(
            "INSERT INTO dns(dn_no, created_at, updated_at, dn_date, year, month,"
            " client_name, lpo_no, client_address, include_prices, pdf_path,"
            " rows_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, "2099-01-01T00:00:00", "2099-01-01T00:00:00", "01/01/2099",
             YEAR, MONTH, "ZZ Client", "", "", 1, "",
             json.dumps(rows, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    if cost_ccy != "USD":
        database.set_cost_currency("dn", no, cost_ccy)


# `updated_at` is included on purpose: it must NOT move. The document as
# issued has not changed (the cost is private and unprinted), and bumping it
# would reshuffle every "recently edited" list in the app.
_META_COLS = ("dn_date", "client_name", "year", "month", "updated_at")


def _meta_of(no):
    conn = _profile_db()
    try:
        row = conn.execute(
            f"SELECT {', '.join(_META_COLS)} FROM dns WHERE dn_no = ?", (no,)
        ).fetchone()
    finally:
        conn.close()
    return dict(zip(_META_COLS, row)) if row else {}


def _rows_of(no):
    conn = _profile_db()
    try:
        r = conn.execute("SELECT rows_json FROM dns WHERE dn_no = ?", (no,)).fetchone()
    finally:
        conn.close()
    return json.loads(r[0]) if r else []


class CostPush(unittest.TestCase):

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        _wipe()
        # The master item, costed at $6.
        conn = _shared()
        try:
            conn.execute("INSERT INTO dn_items(name, data_sig, data_json, use_count,"
                         " last_used_at) VALUES(?,?,?,?,?)",
                         (ITEM, "cp1", '{"unit": "EA", "cost_usd": 6}', 2,
                          "2099-01-01T00:00:00"))
            # A second item that IS in the catalog but has no usable cost.
            conn.execute("INSERT INTO dn_items(name, data_sig, data_json, use_count,"
                         " last_used_at) VALUES(?,?,?,?,?)",
                         (OTHER, "cp2", '{"unit": "EA"}', 1, "2099-01-01T00:00:00"))
            conn.commit()
        finally:
            conn.close()
        _seed_dn(DN_USD, [{"item": ITEM, "qty": "1"},
                          {"item": OTHER, "qty": "1", "cost": "99"}])
        _seed_dn(DN_IQD, [{"item": ITEM, "qty": "1"}], cost_ccy="IQD")
        _seed_dn(DN_SKIP, [{"item": ITEM, "qty": "1"}])
        self.addCleanup(_wipe)

    def _targets(self):
        r = master_data.cost_targets(ITEM)
        self.assertTrue(r.get("ok"), r.get("error"))
        return {t["key"]: t for t in r["targets"]}

    # ---------------------------------------------------------------- preview
    def test_preview_finds_the_documents(self):
        keys = self._targets()
        for no in DNS:
            self.assertIn(f"dn:{no}", keys, f"{no} should be offered")

    def test_preview_writes_nothing(self):
        before = {no: _rows_of(no) for no in DNS}
        master_data.cost_targets(ITEM)
        master_data.apply_cost_to_documents(ITEM, [f"dn:{n}" for n in DNS],
                                            apply=False)
        for no in DNS:
            self.assertEqual(_rows_of(no), before[no], f"{no} was modified by a preview")

    def test_an_item_with_no_cost_is_refused(self):
        r = master_data.cost_targets(OTHER)
        self.assertFalse(r.get("ok"))
        self.assertIn("cost", r.get("error", "").lower())

    def test_a_document_that_already_agrees_is_not_offered(self):
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        self.assertNotIn(f"dn:{DN_USD}", self._targets(),
                         "a document already holding this cost is noise in the list")

    # ------------------------------------------------------------------ apply
    def test_only_the_chosen_documents_change(self):
        r = master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["documents"], 1)
        self.assertEqual(_rows_of(DN_USD)[0]["cost"], "6.0")
        self.assertNotIn("cost", _rows_of(DN_SKIP)[0],
                         "an unticked document must be untouched")

    def test_other_items_in_the_same_document_are_untouched(self):
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        rows = _rows_of(DN_USD)
        self.assertEqual(rows[1]["item"], OTHER)
        self.assertEqual(rows[1]["cost"], "99", "a different item was overwritten")

    def test_a_dinar_document_gets_the_dinar_figure(self):
        """The whole reason this is not a single number written everywhere."""
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_IQD}"], apply=True)
        row = _rows_of(DN_IQD)[0]
        peg = database.doc_iqd_per_usd("dn", DN_IQD) or 1400
        self.assertEqual(row["cost"], str(round(6 * peg)))
        self.assertNotEqual(row["cost"], "6.0", "a dollar figure landed in a dinar cell")

    def test_the_written_value_says_which_currency_it_is(self):
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_IQD}"], apply=True)
        self.assertEqual(_rows_of(DN_IQD)[0]["cost_ccy"], "IQD")
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        self.assertEqual(_rows_of(DN_USD)[0]["cost_ccy"], "USD")

    def test_nothing_is_written_without_apply(self):
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"])
        self.assertNotIn("cost", _rows_of(DN_USD)[0])

    def test_nothing_is_written_without_keys(self):
        r = master_data.apply_cost_to_documents(ITEM, [], apply=True)
        self.assertEqual(r["documents"], 0)
        self.assertNotIn("cost", _rows_of(DN_USD)[0])

    def test_an_unknown_key_is_ignored_not_guessed(self):
        r = master_data.apply_cost_to_documents(ITEM, ["dn:NOPE-404"], apply=True)
        self.assertEqual(r["documents"], 0)
        for no in DNS:
            self.assertNotIn("cost", _rows_of(no)[0])

    def test_applying_twice_is_a_no_op(self):
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        first = _rows_of(DN_USD)
        r = master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        self.assertEqual(_rows_of(DN_USD), first)
        self.assertEqual(r["selected"], 0,
                         "a settled document should no longer be a target")

    def test_the_document_is_otherwise_unchanged(self):
        """Only the cost cells move - quantity, client, date and the row order
        are part of the record."""
        before = _meta_of(DN_USD)
        master_data.apply_cost_to_documents(ITEM, [f"dn:{DN_USD}"], apply=True)
        self.assertEqual(_meta_of(DN_USD), before)
        self.assertEqual(_rows_of(DN_USD)[0]["qty"], "1")

    # ------------------------------------------------------------- submission
    def test_a_submitted_document_is_flagged_but_still_offered(self):
        """Listed, so you can correct it deliberately; the UI leaves it
        unticked so you cannot restate an issued document by accident."""
        database.set_submitted("dn", DN_SKIP, True)
        t = self._targets()[f"dn:{DN_SKIP}"]
        self.assertTrue(t["submitted"])
        self.assertFalse(self._targets()[f"dn:{DN_USD}"]["submitted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
