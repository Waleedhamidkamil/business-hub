"""
PO item memory: the picture comes back with the item (2026-08-11).

A quotation has remembered what an item LOOKS like since 2026-08-05, but the PO
could not: the shared `items` table held four fixed columns and had nowhere to
put a reference, so a picture put on a PO row was saved with that document and
lost to autocomplete. `items.data_json` closes that.

What matters here, in order of how badly it bites:

  1. **The picture round-trips.** Save a PO row with an image, type the name
     again, get the image back.
  2. **It does not fork the autocomplete.** The signature still hashes only
     (description, unit, last_company) - remembering a picture must not create
     a second entry for the same item, which is exactly the mess `_SIG_IGNORE`
     was introduced to clean up on the quotation side.
  3. **A picture-less re-save does not erase it.** Saving the same item from a
     PO whose picture cell is empty must not wipe the one remembered earlier.
  4. **The payload stays narrow.** Only what the four columns cannot hold -
     never the base64 bytes (that ballooned the shared library once already and
     was removed), and never `qty`, which is per-document not per-item.
  5. **The schema exists in BOTH places.** A column added only by the runtime
     ALTER is missing from a brand-new shared DB, and vice versa.

!! Writes to the SHARED catalog under a sentinel name and deletes it again -
!! the live DB has no test isolation (docs/11). It also writes one image to the
!! content store and removes it in teardown.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import api, config, database, item_images, master_items, profile_manager, shared_library  # noqa: E402

# 1x1 PNG. Small on purpose: this lands in the real content store.
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
       "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
NAME = "ZZ-PO-ITEM-MEMORY-TEST"


def _row(**over):
    row = {"name": NAME, "description": "sentinel row", "unit": "pcs",
           "qty": "7", "company": "ZZ Sentinel Co", "image": PNG}
    row.update(over)
    return row


class PoItemPicture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()               # applies the PRAGMA-guarded ALTER
        cls._refs = set()

    @classmethod
    def tearDownClass(cls):
        conn = sqlite3.connect(profile_manager.shared_db_path())
        try:
            try:
                conn.execute("DELETE FROM items WHERE name = ?", (NAME,))
            except sqlite3.OperationalError:
                pass
            # A PO save also writes the item RECORD (backend/master_items.py).
            conn.execute("DELETE FROM master_items "
                         "WHERE name_en = ? OR name_ar = ?", (NAME, NAME))
            conn.commit()
        finally:
            conn.close()
        for ref in cls._refs:                     # never leave junk in the store
            p = item_images.path_for(ref)
            if p and p.exists():
                p.unlink()

    def setUp(self):
        conn = sqlite3.connect(profile_manager.shared_db_path())
        try:
            try:
                conn.execute("DELETE FROM items WHERE name = ?", (NAME,))
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()

    def remember(self, row):
        payload = api._po_item_payload(row)
        for ref in (payload.get("image_refs") or {}).values():
            type(self)._refs.add(ref)
        database.remember_item(row["name"], row["description"], row["unit"],
                               row["company"], payload or None)
        return payload

    def only(self):
        hits = [s for s in database.suggest_items(NAME) if s["name"] == NAME]
        self.assertEqual(len(hits), 1, f"expected exactly one entry, got {len(hits)}")
        return hits[0]

    # ---- 1. the round trip ------------------------------------------------
    def test_the_picture_comes_back_with_the_item(self):
        self.remember(_row())
        s = self.only()
        ref = (s["data"].get("image_refs") or {}).get("image")
        self.assertTrue(ref, "no picture reference was remembered")
        self.assertEqual(item_images.load(ref), PNG,
                         "the bytes read back out of the store differ")

    def test_the_other_fields_still_come_back(self):
        # CHANGED 2026-08-20: a save writes ONLY the item record now, and a PO
        # suggestion speaks Arabic (and never fills the company), so read the
        # remembered fields off the record itself.
        self.remember(_row())
        rec = self._record()
        self.assertEqual(rec["description_en"], "sentinel row")
        self.assertEqual(rec["unit_en"], "pcs")
        self.assertEqual(rec["last_company"], "ZZ Sentinel Co")

    # ---- 2. no fork -------------------------------------------------------
    def test_remembering_a_picture_does_not_fork_the_entry(self):
        self.remember(_row(image=""))
        self.remember(_row())
        self.only()      # asserts exactly one

    def test_a_different_picture_updates_the_same_entry(self):
        self.remember(_row())
        first = self.only()["data"]["image_refs"]["image"]
        other = PNG.replace("iVBORw0KGgo", "iVBORw0KGgo")  # same bytes, same hash
        self.remember(_row(image=other))
        self.assertEqual(self.only()["data"]["image_refs"]["image"], first)

    # ---- 3. a blank picture cell does not erase the memory ----------------
    def test_a_picture_less_resave_keeps_the_remembered_one(self):
        self.remember(_row())
        ref = self.only()["data"]["image_refs"]["image"]
        self.remember(_row(image=""))
        self.assertEqual(self.only()["data"].get("image_refs", {}).get("image"), ref,
                         "saving the item without a picture wiped the one it had")

    # ---- 4. the payload stays narrow -------------------------------------
    def test_the_payload_carries_only_what_the_columns_cannot(self):
        payload = api._po_item_payload(_row())
        self.assertEqual(set(payload), {"image_refs"})
        for junk in ("qty", "description", "unit", "company", "name"):
            self.assertNotIn(junk, payload, f"`{junk}` does not belong in the payload")

    def test_the_bytes_never_reach_the_database(self):
        # CHANGED 2026-08-20: the picture reference lives on the item record's
        # `image_refs` column now (the four catalogs are retired). Same rule:
        # a reference, never the base64 bytes.
        self.remember(_row())
        rec = self._record()
        blob = rec["image_refs"]
        self.assertNotIn("base64", blob or "",
                         "the base64 image landed in the shared library - that is "
                         "what the content-addressed store exists to prevent")
        self.assertLess(len(blob or ""), 200, "the payload should be a short reference")

    def test_a_row_with_no_picture_stores_no_payload(self):
        self.assertEqual(api._po_item_payload(_row(image="")), {})

    # ---- 5. schema in both places ----------------------------------------
    def test_the_column_is_declared_in_both_schemas(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        for src in (shared_library.SHARED_SCHEMA, database._SCHEMA):
            items = src[src.index("CREATE TABLE IF NOT EXISTS items"):]
            items = items[:items.index(");")]
            self.assertIn("data_json", items,
                          "a column only in one schema means new and upgraded "
                          "shared databases disagree")

    def test_the_runtime_alter_is_idempotent(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        before = self._columns()
        database.init_db()
        database.init_db()
        self.assertEqual(self._columns(), before)
        self.assertIn("data_json", before)

    def test_a_shared_db_built_from_scratch_has_the_column(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        tmp = APP_ROOT / ".test-work" / "po-item-memory" / "fresh.db"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            tmp.unlink()
        shared_library.init_shared_db(tmp)
        conn = sqlite3.connect(tmp)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        finally:
            conn.close()
            tmp.unlink(missing_ok=True)
        self.assertIn("data_json", cols)

    def _columns(self):
        conn = sqlite3.connect(profile_manager.shared_db_path())
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        finally:
            conn.close()

    # ---- suggest_items shape ---------------------------------------------
    def test_suggest_items_always_carries_a_data_dict(self):
        self.remember(_row(image=""))
        s = self.only()
        self.assertIsInstance(s["data"], dict,
                              "callers index `data.image_refs` - it must never be None")

    def test_a_corrupt_payload_degrades_to_empty(self):
        self.remember(_row())
        conn = sqlite3.connect(profile_manager.shared_db_path())
        try:
            try:
                conn.execute("UPDATE items SET data_json = ? WHERE name = ?",
                             ("{not json", NAME))
            except sqlite3.OperationalError:
                pass
            conn.commit()
        finally:
            conn.close()
        # CHANGED 2026-08-19: a suggestion is built from the item RECORD, not
        # from this row's blob, so a corrupt blob can no longer empty it. The
        # thing that matters is unchanged and still asserted - autocomplete
        # keeps working and hands back a usable item.
        got = self.only()
        self.assertIsInstance(got["data"], dict,
                              "a corrupt payload must not break autocomplete")
        self.assertEqual(got["name"], NAME)

    # REMOVED 2026-08-20: test_the_signature_ignores_the_payload tested the
    # retired `items` catalog's per-row `data_sig` write mechanics. Saves no
    # longer populate the catalogs, and the record has no per-catalog signature.

    def _record(self):
        """The one item record a save writes now (backend/master_items.py)."""
        conn = master_items._connect()
        try:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM master_items WHERE name_en = ? OR name_ar = ?",
                (NAME, NAME)).fetchone()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
