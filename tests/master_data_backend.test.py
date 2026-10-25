"""
Item Master - the unified read model over the autocomplete catalogs.

`backend/master_data.py` folds seven tables into one row per item and fans
writes back out. Two classes of mistake matter, and this suite is built around
them:

  1. **Losing a catalog.** The section metadata carries a ready-made
     `("quote_type = ?", "materials")` filter pair, NOT (column, value).
     Reading it as the latter selects nothing - and that failed SILENTLY: the
     PO and DN catalogs kept working while the quotation and invoice ones
     contributed zero items, so the view looked fine and was missing a third
     of the data. Every source is asserted to reach the fold.

  2. **Fanning a write to the wrong rows.** An edit here rewrites every
     physical row behind the item. If the identity is wrong it silently
     rewrites somebody else's row, so identity, spelling collapse and the
     patch-not-replace rule are all pinned.

Plus the money rule: a raw `cost` with no `cost_usd` predates the currency fix
and must be reported as unusable, never quietly used as if it were dollars.

!! Read-mostly. The write tests use a sentinel item name and delete it again -
!! the live shared catalog has no test isolation (docs/11).
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import data_manager, database, master_data, master_items as mi, profile_manager  # noqa: E402

SENTINEL = "ZZ-MASTER-DATA-TEST-ITEM"


def _shared():
    conn = sqlite3.connect(profile_manager.shared_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _wipe_sentinel():
    conn = _shared()
    try:
        for table, col in (("items", "name"), ("quote_items", "name"),
                           ("dn_items", "name"), ("inv_items", "name")):
            try:
                conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (SENTINEL + "%",))
            except sqlite3.Error:
                pass
        # A document save also writes the item RECORD, and the resolver reads
        # THAT now (2026-08-19) - so a sentinel left there would outlive the
        # catalog rows and answer the next test.
        try:
            conn.execute("DELETE FROM master_items WHERE name_en LIKE ? "
                         "   OR name_ar LIKE ?", (SENTINEL + "%", SENTINEL + "%"))
        except sqlite3.Error:
            pass
        conn.commit()
    finally:
        conn.close()


class SourcesNoLongerReachACatalog(unittest.TestCase):
    """What `SOURCES` is, and is not, since the four catalogs were dropped.

    This class used to be `EverySourceReachesTheFold`, and it pinned the
    relationship `SOURCES[i]["section"]` -> a `data_manager` catalog section,
    with a `sub_filter` per quote/invoice sub-type. That relationship existed to
    serve `_variants()`, which read the four per-document catalogs.

    Those catalogs (`items`, `quote_items`, `dn_items`, `inv_items`) were
    physically DROPPED on 2026-08-20 and the item catalogue is `master_items`
    now. So on 2026-09-05 the ten dead Data Manager sections were removed -
    they were still listed on the Data Manager screen and threw `no such table`
    the moment any of them was opened.

    What survives of `SOURCES` is its `key` vocabulary (`po`, `qut:materials`,
    `dn`, `inv:service`, …), which `_preferred_sources` still uses to order
    `_fill_from`'s trust. The `section` field is vestigial and reaches only two
    paths that can no longer return anything: `_variants()` and `save_item()`.

    These tests now pin THAT, so the next person does not restore a section
    pointing at a table that is gone.
    """

    def test_the_source_KEY_vocabulary_is_still_live(self):
        """`_preferred_sources` orders the fill by document type, and that is
        the part of SOURCES the resolver genuinely uses."""
        keys = {s["key"] for s in master_data.SOURCES}
        for expected in ("po", "qut:materials", "dn", "inv:materials"):
            self.assertIn(expected, keys)
        prefer = master_data._preferred_sources("quote", "materials")
        self.assertTrue(prefer, "a quotation must prefer quotation sources")
        self.assertTrue(all(p.startswith("qut") for p in prefer),
                        f"a quotation must not prefer another type's source: {prefer}")

    def test_no_source_still_claims_a_live_catalog_section(self):
        """The inverse of the assertion this class used to make. A section that
        resolves would mean someone re-added a catalog over a dropped table -
        which is exactly the state that put ten broken entries on the Data
        Manager screen for a fortnight."""
        alive = [s["key"] for s in master_data.SOURCES
                 if data_manager._catalog_by_key(s.get("section", ""))]
        self.assertEqual(
            alive, [],
            "these SOURCES resolve to a data_manager catalog again: "
            f"{alive}. If an item catalogue is genuinely coming back it must go "
            "through master_items, not a section over a dropped table.")

    def test_the_variant_reader_is_inert_and_says_so(self):
        """`_variants()` is the only consumer of `section` that mattered. With
        every catalog gone it must return nothing - quietly, not by raising."""
        conn = _shared()
        try:
            self.assertEqual(master_data._variants(conn), [])
        finally:
            conn.close()

    def test_the_data_manager_offers_only_sections_that_open(self):
        """The bug itself, guarded: every section the screen lists must answer
        when it is opened. Ten of thirteen did not."""
        broken = []
        for c in data_manager.CATALOG_SECTIONS:
            try:
                data_manager.list_catalog_records(c["key"], limit=1)
            except Exception as exc:                    # noqa: BLE001
                broken.append(f"{c['key']} ({c.get('table')}): {type(exc).__name__}")
        self.assertEqual(broken, [],
                         "the Data Manager lists sections that throw when "
                         "opened: " + "; ".join(broken))

class Identity(unittest.TestCase):
    def test_spelling_differences_are_one_item(self):
        a = master_data._norm("Copper  Cable")
        self.assertEqual(a, master_data._norm("copper cable"))
        self.assertEqual(a, master_data._norm(" COPPER CABLE "))

    def test_identity_matches_the_duplicate_finder(self):
        """Two views of the catalog that disagree on what counts as the same
        name would be worse than either alone."""
        for s in ("Copper Cable", "  A/B  ", "بايب", "12mm PIPE"):
            self.assertEqual(master_data._norm(s), data_manager._normalize_name(s))

    def test_different_items_stay_apart(self):
        self.assertNotEqual(master_data._norm("Pipe 12mm"), master_data._norm("Pipe 14mm"))


class Money(unittest.TestCase):
    def test_a_cost_without_a_recorded_currency_is_never_presented(self):
        """Checked against the RAW rows, not against the flag: promoting the
        raw cost also clears the flag, so a flag-filtered check would just find
        nothing and pass."""
        conn = _shared()
        try:
            variants = master_data._variants(conn)
        finally:
            conn.close()
        ident = master_data._ident
        legacy = {ident(v["name"], v["description"], v["unit"]) for v in variants
                  if v["cost_raw"] and v["cost_usd"] is None and v["cost_native"] is None}
        usable = {ident(v["name"], v["description"], v["unit"]) for v in variants
                  if v["cost_usd"] is not None or v["cost_native"] is not None}
        only_legacy = legacy - usable
        if not only_legacy:
            self.skipTest("no item whose only cost is in an unrecorded currency")
        by_id = {i["id"]: i for i in master_data.list_items(limit=1000)["rows"]}
        checked = 0
        for key in only_legacy:
            i = by_id.get(key)
            if not i:
                continue
            checked += 1
            self.assertIsNone(i["cost"],
                              f"{i['name']}: a cost of unknown currency was presented "
                              f"as if it were USD")
            self.assertIn("legacy_cost", i["flags"])
        if not checked:
            self.skipTest("no legacy-cost item survived into the listed page")

    def test_margin_is_only_shown_when_both_numbers_exist(self):
        for i in master_data.list_items(limit=400)["rows"]:
            if i["margin"] is not None:
                self.assertIsNotNone(i["price"])
                self.assertIsNotNone(i["cost"])
                self.assertGreater(i["price"], 0)

    def test_no_cost_and_legacy_cost_are_mutually_exclusive(self):
        for i in master_data.list_items(limit=400)["rows"]:
            self.assertFalse("no_cost" in i["flags"] and "legacy_cost" in i["flags"],
                             f"{i['name']} is both 'no cost' and 'unusable cost'")


class Filtering(unittest.TestCase):
    def test_a_flag_filter_returns_only_that_flag(self):
        for flag in ("no_unit", "split", "no_price"):
            rows = master_data.list_items(flags=[flag], limit=200)["rows"]
            for i in rows:
                self.assertIn(flag, i["flags"], f"{i['name']} lacks {flag}")

    def test_a_source_filter_returns_only_that_source(self):
        counted = {s["key"]: s["count"] for s in master_data.list_items(limit=1)["sources"]}
        key = next((k for k, v in counted.items() if v), None)
        if not key:
            self.skipTest("no populated source")
        for i in master_data.list_items(source=key, limit=200)["rows"]:
            self.assertIn(key, i["sources"])

    def test_search_matches_alternative_spellings_too(self):
        rows = master_data.list_items(limit=400)["rows"]
        # The alternative must NOT be a substring of the display name, or the
        # plain name match would find it anyway and prove nothing.
        multi, other = None, None
        for i in rows:
            for s in i["spellings"]:
                if s != i["name"] and s.lower() not in i["name"].lower():
                    multi, other = i, s
                    break
            if multi:
                break
        if not multi:
            self.skipTest("no item whose alternative spelling is distinguishable")
        hits = master_data.list_items(search=other, limit=50)["rows"]
        self.assertIn(multi["name"], [h["name"] for h in hits],
                      "searching an alternative spelling must find the item")

    def test_two_flags_are_ANDed_not_ORed(self):
        """Filters narrow. An OR would widen the list every time you add one,
        which is the opposite of what clicking a second chip means."""
        a, b = "no_cost", "no_price"
        ra = master_data.list_items(flags=[a], limit=1000)
        rb = master_data.list_items(flags=[b], limit=1000)
        rboth = master_data.list_items(flags=[a, b], limit=1000)
        if not ra["total"] or not rb["total"]:
            self.skipTest("need items under both flags")

        # Compare TOTALS and membership, not the returned pages: `list_items`
        # caps a page at 1000, so once the catalog passed that the two pages
        # were truncated differently and the set difference said nothing about
        # AND vs OR. (That is what made this fail after the variant rebuild.)
        self.assertLessEqual(rboth["total"], min(ra["total"], rb["total"]),
                             "an AND cannot return more than either side alone")
        for i in rboth["rows"]:
            self.assertIn(a, i["flags"], i["id"])
            self.assertIn(b, i["flags"], i["id"])
        # And the converse: something carrying only ONE of them is excluded.
        one_only = [i for i in ra["rows"] if b not in i["flags"]]
        if one_only:
            ids = {i["id"] for i in rboth["rows"]}
            self.assertNotIn(one_only[0]["id"], ids,
                             "an item with only one of the two flags leaked in")

    def test_an_unknown_flag_matches_nothing_rather_than_everything(self):
        self.assertEqual(master_data.list_items(flags=["not_a_flag"], limit=10)["total"], 0)

    def test_paging_does_not_lose_or_repeat_rows(self):
        first = master_data.list_items(limit=10, offset=0)["rows"]
        second = master_data.list_items(limit=10, offset=10)["rows"]
        self.assertEqual(len(set(r["id"] for r in first) & set(r["id"] for r in second)), 0)


class WriteFanOut(unittest.TestCase):
    """Edits must reach every stored row, and only this item's rows."""

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        _wipe_sentinel()
        conn = _shared()
        try:
            # ONE item in TWO catalogs - same name, description and unit, which
            # is what identity means since 2026-08-13. (Rows that differ on
            # description or unit are separate ITEMS now: that is how "Hemp
            # rope" phi12mm stopped being merged into phi20mm.)
            conn.execute("INSERT INTO items(name, data_sig, description, unit, "
                         "last_company, use_count, last_used_at) VALUES(?,?,?,?,?,?,?)",
                         (SENTINEL, "sigA", "first", "pcs", "ZZ Co", 3, "2026-08-01T00:00:00"))
            conn.execute("INSERT INTO dn_items(name, data_sig, data_json, use_count, last_used_at) "
                         "VALUES(?,?,?,?,?)",
                         (SENTINEL, "sigB", '{"unit": "pcs", "description": "first", "rate": "12"}',
                          5, "2026-08-02T00:00:00"))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        _wipe_sentinel()

    def test_the_sentinel_folds_into_one_item(self):
        d = master_data.item_detail(SENTINEL)
        self.assertTrue(d["ok"])
        self.assertEqual(d["item"]["variant_count"], 2)
        self.assertEqual(d["item"]["uses"], 8)
        self.assertEqual(sorted(d["item"]["sources"]), ["dn", "po"])

    def test_one_edit_reaches_every_stored_row(self):
        r = master_data.save_item(SENTINEL, {"unit": "BOX"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["updated"], 2)
        for v in master_data.item_detail(SENTINEL)["item"]["variant_rows"]:
            self.assertEqual(v["unit"], "BOX", f"{v['source']} kept its old unit")

    def test_a_patch_leaves_unmentioned_fields_alone(self):
        """The trap: `update_catalog_entry` REPLACES a row, so a patch that
        does not re-state every field blanks the rest."""
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        rows = master_data.item_detail(SENTINEL)["item"]["variant_rows"]
        # EVERY row, not "at least one" - the two storage shapes take different
        # code paths and only one of them had the bug.
        for v in rows:
            self.assertTrue(v["description"],
                            f"{v['source']} lost its description to a unit-only edit")

    def test_renaming_moves_every_row(self):
        new = SENTINEL + "-RENAMED"
        r = master_data.save_item(SENTINEL, {"name": new})
        self.assertTrue(r["ok"])
        self.assertFalse(master_data.item_detail(SENTINEL)["ok"],
                         "the old name still resolves after a rename")
        d = master_data.item_detail(new)
        self.assertTrue(d["ok"])
        self.assertEqual(d["item"]["variant_count"], 2)

    def test_deleting_from_one_source_leaves_the_others(self):
        r = master_data.delete_items([SENTINEL], "po")
        self.assertTrue(r["ok"])
        d = master_data.item_detail(SENTINEL)
        self.assertTrue(d["ok"], "deleting from one source removed the item entirely")
        self.assertEqual(d["item"]["sources"], ["dn"])

    def test_deleting_from_all_sources_removes_the_item(self):
        r = master_data.delete_items([SENTINEL], "")
        self.assertTrue(r["ok"])
        self.assertFalse(master_data.item_detail(SENTINEL)["ok"])

    def test_an_unknown_source_is_refused_not_ignored(self):
        r = master_data.delete_items([SENTINEL], "not_a_source")
        self.assertFalse(r["ok"])
        self.assertIn("unknown source", (r.get("error") or "").lower(),
                      "a bad source must be REFUSED by name, not silently match nothing")
        self.assertTrue(master_data.item_detail(SENTINEL)["ok"],
                        "a typo in the source must not delete everything")

    def test_empty_input_is_refused(self):
        self.assertFalse(master_data.save_item(SENTINEL, {})["ok"])
        self.assertFalse(master_data.delete_items([], "")["ok"])
        self.assertFalse(master_data.item_detail("")["ok"])
        self.assertFalse(master_data.merge_items("", ["x"])["ok"])

    def test_merging_into_itself_is_refused(self):
        self.assertFalse(master_data.merge_items(SENTINEL, [SENTINEL])["ok"])


class Health(unittest.TestCase):
    def test_health_counts_agree_with_the_MASTER_DATA_filters(self):
        """CHANGED 2026-08-19. Health used to fold the four document catalogs
        and compare itself against the old `list_items` filter bar. Both it and
        the Master Data screen read the RECORD now, so the number the panel
        shows and the number the chip shows come from the same SQL - a panel
        that says 12 and then shows 9 is worse than no panel."""
        from backend import master_items as mi
        h = master_data.health()
        listed = mi.list_records(limit=1)
        self.assertEqual(h["items"], listed["all_total"])
        for g in h["gaps"]:
            self.assertEqual(g["count"], listed["missing_counts"][g["key"]],
                             f"health and the Master Data chip disagree on {g['key']}")

    def test_the_picture_report_partitions_the_store(self):
        img = master_data.image_report()
        self.assertEqual(img["files"], img["referenced"] + img["orphan_count"],
                         "every file on disk is either referenced or an orphan")
        self.assertEqual(len(img["orphans"]), img["orphan_count"])

    def test_cell_memory_is_reported_but_not_browsable(self):
        """~1,800 rows of individual cell values - an index, not master data.
        It is a number and a Clear button, never a list to scroll."""
        h = master_data.health()
        self.assertIsInstance(h["cell_memory"], int)
        sections = [s["section"] for s in master_data.SOURCES]
        self.assertFalse([s for s in sections if "column_memory" in s],
                         "the per-cell index must never be folded in as an item catalog")

class HealthAndPictures(unittest.TestCase):
    """The Health panel reads the RECORD (2026-08-19), and the orphan report
    that feeds a DELETE has to see the record's pictures too."""

    def setUp(self):
        _wipe_sentinel()
        self.addCleanup(_wipe_sentinel)

    def test_health_counts_ITEMS_not_catalog_rows(self):
        from backend import master_items as mi
        h = master_data.health()
        self.assertTrue(h.get("ok"), h)
        self.assertEqual(h["items"], mi.list_records(limit=1)["all_total"],
                         "the headline number must be items, not stored rows")

    def test_health_reports_what_is_still_MISSING(self):
        h = master_data.health()
        keys = {g["key"] for g in h["gaps"]}
        for k in ("name_ar", "name_en", "unit", "price", "cost", "image"):
            self.assertIn(k, keys, f"the panel cannot show the {k} gap")
        for g in h["gaps"]:
            self.assertIsInstance(g["count"], int)
            self.assertTrue(g["label"], "every gap is a WORD, never a colour")

    def test_a_picture_held_ONLY_by_the_record_is_not_an_orphan(self):
        """`purge_orphan_images` DELETES what this calls an orphan. A picture
        can live only on the record - an item typed by hand, or one carried
        across a rebuild after its catalog row went - and counting the four
        catalogs alone would delete it."""
        ref = "zz-master-data-test-picture.jpg"
        conn = _shared()
        try:
            conn.execute(
                "INSERT INTO master_items(name_en, image_refs, use_count) "
                "VALUES(?,?,0)", (SENTINEL + " Picture", '{"image": "%s"}' % ref))
            conn.commit()
        finally:
            conn.close()
        self.assertNotIn(ref, master_data.image_report()["orphans"],
                         "a picture the record still points at was called an "
                         "orphan - purging would delete it")


class PictureBackfill(unittest.TestCase):
    """Pictures already inside saved documents, taught to the RECORD
    (2026-08-19). It used to write one row per catalog per item."""

    def setUp(self):
        _wipe_sentinel()
        self.addCleanup(_wipe_sentinel)

    def test_a_dry_run_writes_nothing(self):
        before = master_data.image_report()["files"]
        r = master_data.backfill_images(apply=False)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r["applied"], 0)
        self.assertEqual(r["rows_written"], 0)
        self.assertEqual(master_data.image_report()["files"], before,
                         "a preview must not store a single file")

    def test_it_reports_against_the_RECORD(self):
        """An item with no record cannot be given a picture - it is reported as
        unmatched, never invented."""
        r = master_data.backfill_images(apply=False)
        self.assertEqual(r["found"], r["would_update"] + r["already_have"]
                         + r["unmatched"],
                         "every picture found is accounted for exactly once")

    def test_an_item_that_HAS_a_picture_keeps_it(self):
        """The stored one was chosen deliberately; a document's copy must never
        overwrite it."""
        conn = _shared()
        try:
            conn.execute(
                "INSERT INTO master_items(name_en, image_refs, use_count) "
                "VALUES(?,?,0)", (SENTINEL + " Keeper", '{"image": "keep-me.jpg"}'))
            conn.commit()
            master_data.backfill_images(apply=True)
            got = conn.execute(
                "SELECT image_refs FROM master_items WHERE name_en = ?",
                (SENTINEL + " Keeper",)).fetchone()[0]
        finally:
            conn.close()
        self.assertIn("keep-me.jpg", got, "the item's own picture was replaced")


class PasteResolver(unittest.TestCase):
    """Matching a pasted Excel block against the master.

    The tiers exist so that being WRONG is bounded: `exact` and `contained` are
    applied without asking, `similar` never is. Each test below is one way the
    boundary could move in the dangerous direction.
    """

    def setUp(self):
        # SAVED the way the app saves (2026-08-19). The resolver reads the item
        # RECORD, and a catalog row written past the save path has no record -
        # a state the app itself cannot reach.
        _wipe_sentinel()
        database.remember_item(SENTINEL + " (Big Size)", "graded", "KG", "ZZ Co")
        database.remember_quote_item("materials", SENTINEL + " (Big Size)", {
            "description": "graded", "unit": "KG",
            "price_native": 0.75, "price_native_ccy": "USD",
            "cost_native": 0.4, "cost_native_ccy": "USD"})

    def tearDown(self):
        _wipe_sentinel()

    def test_a_name_covering_SEVERAL_variants_never_auto_fills(self):
        """"Hemp rope" is three items - Diameter 12mm, phi15mm, phi20mm. A paste
        gives only the name, so which one is genuinely unknown. The resolver's
        standing rule applies: a tie is never a match. It offers the variants
        and fills nothing, rather than picking one and quietly attaching the
        wrong price to an issued document."""
        database.remember_quote_item("materials", SENTINEL + " (Big Size)", {
            "description": "premium", "unit": "KG",
            "price_native": 9.99, "price_native_ccy": "USD"})
        r = self._one(SENTINEL + " (Big Size)")
        self.assertEqual(r["tier"], "similar")
        self.assertEqual(r["fill"], {}, "an ambiguous name must fill NOTHING")
        descs = {a.get("description") for a in r["alternatives"]}
        self.assertIn("graded", descs)
        self.assertIn("premium", descs)

    def _one(self, name, **kw):
        kw.setdefault("doc_type", "quote")
        kw.setdefault("sub_type", "materials")
        return master_data.resolve_items([name], **kw)["results"][0]

    # ---- the case the whole feature was asked for -------------------------
    def test_an_excel_wrapped_cell_is_an_EXACT_match(self):
        """'Potato (Big \n\n Size)' is not a fuzzy-matching problem - the
        identity function already collapses whitespace. It must land in the
        tier that cannot be wrong."""
        r = self._one(SENTINEL + " (Big \n\n Size)   ")
        self.assertEqual(r["tier"], "exact")
        self.assertEqual(r["score"], 1.0)
        self.assertEqual(r["fill"].get("unit"), "KG")

    def test_case_and_padding_are_exact_too(self):
        for variant in ("  " + SENTINEL.lower() + "  (big size)",
                        SENTINEL + " (Big Size)\r\n",
                        SENTINEL + "   (Big   Size)"):
            self.assertEqual(self._one(variant)["tier"], "exact", variant)

    # ---- the tier boundary ------------------------------------------------
    def test_a_qualifier_is_CONTAINED_and_fills(self):
        r = self._one(SENTINEL + " (Big Size) 5kg")
        self.assertEqual(r["tier"], "contained")
        self.assertEqual(r["fill"].get("unit"), "KG")

    def test_a_coincidental_word_is_NOT_a_match(self):
        """The floor that matters. Without it a one-word master item swallows
        any query containing that word - 'Totally New Widget XYZ' matched
        'Widget' at 0.25 overlap and filled its price."""
        r = master_data.resolve_items(["Totally Unrelated " + SENTINEL + " Nonsense Words Here"],
                                      doc_type="quote", sub_type="materials")["results"][0]
        self.assertNotEqual(r["tier"], "contained")
        self.assertEqual(r["fill"], {})

    def test_a_similar_name_suggests_but_never_fills(self):
        r = self._one(SENTINEL + " (Big Sizes)")
        if r["tier"] != "similar":
            self.skipTest("this catalog resolves that spelling differently")
        self.assertEqual(r["fill"], {},
                         "a `similar` match must never apply itself - the user accepts it")
        self.assertTrue(r["alternatives"], "a suggestion with no candidates is useless")
        self.assertTrue(r.get("suggested_fill"),
                        "accepting should be one click, so the values ride along")

    def test_an_unknown_item_is_new_and_fills_nothing(self):
        r = self._one("ZZ-NOTHING-LIKE-THIS-AT-ALL-9987")
        self.assertEqual(r["tier"], "new")
        self.assertEqual(r["fill"], {})
        self.assertEqual(r["name"], "")

    # ---- what gets filled -------------------------------------------------
    def test_a_partial_match_does_not_overwrite_the_pasted_qualifier(self):
        """'X BLACK' must not be given the master's description 'Red'."""
        r = self._one(SENTINEL + " (Big Size) 5kg")
        self.assertNotIn("description", r["fill"],
                         "the words the query added ARE the description - the "
                         "master's would talk over them")
        self.assertIn("description", self._one(SENTINEL + " (Big Size)")["fill"],
                      "an exact match should still bring its description")

    def test_cost_fills_in_the_currency_it_was_SAVED_in(self):
        """CHANGED 2026-08-20 (Waleed): the Fill button fills a cost in the
        currency it was saved in - no conversion to the document's cost
        currency. This item's cost is 0.4 USD, so it fills as 0.4 USD whether
        the document is costing in USD or IQD; the cell's own $/IQD toggle
        shows it."""
        for doc_ccy, peg in (("USD", 0.0), ("IQD", 1400)):
            fill = self._one(SENTINEL + " (Big Size)",
                             cost_currency=doc_ccy, cost_peg=peg)["fill"]
            self.assertAlmostEqual(fill["cost"], 0.4, places=6,
                                   msg=f"cost must stay 0.4 for a {doc_ccy} doc")
            self.assertEqual(fill["cost_ccy"], "USD",
                             "the cost keeps its saved currency")

    def test_the_document_type_is_preferred_for_the_rate(self):
        """A delivery note's rate is not a quotation's rate."""
        pref = master_data._preferred_sources("quote", "materials")
        self.assertEqual(pref[0], "qut:materials")
        self.assertTrue(all(p.startswith("qut") for p in pref))
        self.assertEqual(master_data._preferred_sources("inv", "equipment")[0], "inv:equipment")
        self.assertEqual(master_data._preferred_sources("", "")[0:1], [])

    def test_a_blank_name_resolves_to_new_without_crashing(self):
        for blank in ("", "   ", "\n\n"):
            r = self._one(blank)
            self.assertEqual(r["tier"], "new")
            self.assertEqual(r["fill"], {})

    def test_the_summary_adds_up(self):
        names = [SENTINEL + " (Big Size)", SENTINEL + " (Big Size) 5kg",
                 "ZZ-NOTHING-LIKE-THIS-9987", ""]
        r = master_data.resolve_items(names, "quote", "materials")
        self.assertEqual(len(r["results"]), len(names))
        counted = sum(r["summary"][t] for t in ("exact", "contained", "similar", "new"))
        self.assertEqual(counted, len(names))
        self.assertEqual(r["summary"]["filled"],
                         r["summary"]["exact"] + r["summary"]["contained"])

    def test_results_come_back_in_input_order(self):
        names = ["ZZ-NOTHING-9987", SENTINEL + " (Big Size)", "ZZ-NOTHING-9988"]
        r = master_data.resolve_items(names, "quote", "materials")
        self.assertEqual([x["query"] for x in r["results"]], names)

    def test_an_empty_paste_is_not_an_error(self):
        r = master_data.resolve_items([], "quote", "materials")
        self.assertTrue(r["ok"])
        self.assertEqual(r["results"], [])


class ResolverSafetyNets(unittest.TestCase):
    """The three ways a match can be confidently wrong.

    Each needs its own purpose-built catalog row: the shared sentinel is a
    long multi-word name with a usable cost, which is exactly the shape that
    does NOT exercise these paths.
    """

    SHORT = "ZZWIDGETX"
    TIE_A = "ZZTIE ALPHA"
    TIE_B = "ZZTIE BETA"
    RAWCOST = "ZZRAWCOST ITEM"

    def setUp(self):
        self._wipe()
        conn = _shared()
        try:
            conn.commit()
        finally:
            conn.close()
        # a ONE-WORD item: the shape that gets swallowed without a floor
        database.remember_item(self.SHORT, "", "EA", "")
        # two items that tie on containment against the same query
        for n in (self.TIE_A, self.TIE_B):
            database.remember_item(n, "", "EA", "")
        # a cost with NO recorded currency - the pre-2026-08 shape
        database.remember_quote_item("materials", self.RAWCOST,
                                     {"unit": "EA", "cost": "84000"})

    def tearDown(self):
        self._wipe()

    def _wipe(self):
        conn = _shared()
        try:
            for t in ("items", "quote_items", "dn_items", "inv_items"):
                try:
                    conn.execute(f"DELETE FROM {t} WHERE name LIKE 'ZZWIDGETX%' "
                                 f"OR name LIKE 'ZZTIE%' OR name LIKE 'ZZRAWCOST%'")
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                "DELETE FROM master_items WHERE name_en LIKE 'ZZWIDGETX%' "
                "OR name_en LIKE 'ZZTIE%' OR name_en LIKE 'ZZRAWCOST%'")
            conn.commit()
        finally:
            conn.close()

    def _one(self, name):
        return master_data.resolve_items([name], "quote", "materials")["results"][0]

    def test_a_one_word_item_does_not_swallow_a_longer_query(self):
        """Containment without an overlap floor makes every short master item
        match anything mentioning it: 'Totally New Widget XYZ' -> 'Widget',
        price and all."""
        r = self._one("Totally New " + self.SHORT + " Nonsense Words Here")
        self.assertNotEqual(r["tier"], "contained",
                            f"a 1-of-6 word overlap was treated as a match: {r}")
        self.assertEqual(r["fill"], {})

    def test_a_tie_is_offered_never_chosen(self):
        r = self._one("ZZTIE")
        self.assertNotEqual(r["tier"], "contained",
                            "two equally good candidates must not silently resolve to one")
        self.assertEqual(r["fill"], {})
        names = [a["name"] for a in r["alternatives"]]
        self.assertIn(self.TIE_A, names)
        self.assertIn(self.TIE_B, names)
        self.assertGreaterEqual(len(r.get("suggestions", [])), 2,
                                "a multiple-match result must expose selectable candidates")
        self.assertTrue(all(c.get("name") and isinstance(c.get("fill"), dict)
                            for c in r["suggestions"]),
                        "each selectable candidate must carry its own fill values")

    def test_a_cost_with_no_recorded_currency_is_never_filled(self):
        """84,000 dinars landing in a dollar cell as $84,000 is the exact
        failure the cost_usd marker exists to prevent."""
        r = self._one(self.RAWCOST)
        self.assertEqual(r["tier"], "exact")
        self.assertNotIn("cost", r["fill"],
                         "a cost of unknown currency was filled in")
        self.assertEqual(r["fill"].get("unit"), "EA",
                         "the rest of the row should still autofill")


class FuzzyNameSuggestions(unittest.TestCase):
    """Misspellings and plurals should find names, never silently fill them."""

    SHAMPOO = "ZZFUZZSHAMPOO"
    PIPE = "ZZFUZZPIPE"
    WRENCH = "ZZFUZZPIPE WRENCH"
    SCAFFOLD = "SCAFFOLDING ZZFUZZPIPE"

    def setUp(self):
        self._wipe()
        for name in (self.SHAMPOO, self.PIPE, self.WRENCH, self.SCAFFOLD):
            description = "good quality bottle" if name == self.SHAMPOO else ""
            database.remember_item(name, description, "EA", "")

    def tearDown(self):
        self._wipe()

    def _wipe(self):
        conn = _shared()
        try:
            for table in ("items", "quote_items", "dn_items", "inv_items"):
                try:
                    conn.execute(
                        f"DELETE FROM {table} WHERE name LIKE 'ZZFUZZ%' "
                        "OR name LIKE 'SCAFFOLDING ZZFUZZ%'")
                except sqlite3.Error:
                    pass
            try:            # …and the item RECORD the save mirrored into
                conn.execute(
                    "DELETE FROM master_items WHERE name_en LIKE 'ZZFUZZ%' "
                    "OR name_en LIKE 'SCAFFOLDING ZZFUZZ%' "
                    "OR name_ar LIKE 'ZZFUZZ%'")
            except sqlite3.Error:
                pass
            conn.commit()
        finally:
            conn.close()

    def _one(self, name):
        return master_data.resolve_items([name], "quote", "materials")["results"][0]

    def test_a_simple_name_typo_is_offered_as_a_suggestion(self):
        r = self._one("ZZFUZZSHAMPO")
        self.assertEqual(r["tier"], "similar")
        self.assertEqual(r["fill"], {}, "a fuzzy match must not fill on its own")
        self.assertIn(self.SHAMPOO, [c["name"] for c in r["suggestions"]])

    def test_a_plural_generic_name_offers_distinct_related_items(self):
        r = self._one("ZZFUZZPIPES")
        self.assertEqual(r["tier"], "similar")
        self.assertEqual(r["fill"], {}, "a broad match must remain a user choice")
        names = {c["name"] for c in r["suggestions"]}
        self.assertTrue({self.PIPE, self.WRENCH, self.SCAFFOLD}.issubset(names),
                        f"related pipe suggestions were lost: {r}")


class ImageBackfill(unittest.TestCase):
    """Recovering pictures that were already inside saved documents.

    This rewrites hundreds of catalog rows in one go, so the failure modes are
    proportionally worse than a single edit: a guessed name attaches the wrong
    photo to an item, a non-idempotent write doubles the store on every run,
    and touching `rows_json` would corrupt issued documents.
    """

    def _with_fake_pictures(self, pictures):
        """Drive the backfill from a controlled set instead of whatever the
        live documents happen to hold. Once the real backfill has been run
        there is nothing left to recover, so effect-based assertions would
        quietly stop proving anything."""
        real = master_data._document_pictures
        master_data._document_pictures = lambda: pictures
        self.addCleanup(setattr, master_data, "_document_pictures", real)

    def _sentinel_picture(self):
        """A 1x1 PNG attached to the sentinel item's name."""
        item = master_data.list_items(limit=1)["rows"]
        if not item:
            self.skipTest("empty catalog")
        name = item[0]["name"]
        return name, {master_data._norm(name): {
            "name": name, "column": "image", "doc": "ZZ-FAKE",
            "when": "2000-01-01T00:00:00",
            "data_uri": ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                         "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),
        }}

    def test_preview_changes_nothing(self):
        """Driven from a synthetic picture set so it still bites after the real
        backfill has already been applied."""
        name, fake = self._sentinel_picture()
        self._with_fake_pictures(fake)
        before_pic = master_data.item_detail(name)["item"]["image_ref"]
        before_store = master_data.image_report()["files"]
        r = master_data.backfill_images(apply=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["applied"], 0)
        self.assertEqual(r["rows_written"], 0)
        self.assertEqual(r["stored_bytes"], 0)
        self.assertEqual(master_data.item_detail(name)["item"]["image_ref"], before_pic,
                         "a preview must not change a stored picture")
        self.assertEqual(master_data.image_report()["files"], before_store,
                         "a preview must not write to the picture store")

    def test_the_report_partitions_a_known_set(self):
        """Arithmetic pinned on a controlled input: with live data the
        `unmatched` bucket is empty, so the identity holds either way."""
        self._with_fake_pictures({
            "zz nothing like this at all 9987": {
                "name": "ZZ Nothing Like This At All 9987", "column": "image",
                "doc": "ZZ-FAKE", "when": "2000-01-01T00:00:00", "data_uri": "data:image/png;base64,AA",
            },
        })
        r = master_data.backfill_images(apply=False)
        self.assertEqual(r["found"], 1)
        self.assertEqual(r["unmatched"], 1,
                         "a picture whose item is not in the catalog must be reported, "
                         "not silently dropped")
        self.assertEqual(r["would_update"], 0)
        self.assertEqual(r["found"], r["would_update"] + r["already_have"] + r["unmatched"])

    def test_the_preview_flag_is_what_gates_the_write(self):
        """Source-level, because the effect is invisible once the catalog is
        fully backfilled: `apply` must guard the write, not merely be logged."""
        import inspect
        src = inspect.getsource(master_data.backfill_images)
        self.assertIn("if not apply", src,
                      "the write must be gated on `apply`")

    def test_it_is_idempotent(self):
        """Content-addressed storage makes this true by construction; asserted
        because a re-run that keeps writing would grow the store forever."""
        first = master_data.backfill_images(apply=False)["would_update"]
        again = master_data.backfill_images(apply=True)
        if first:
            self.skipTest("there is still un-backfilled work; run it first")
        self.assertEqual(again["applied"], 0)
        self.assertEqual(again["rows_written"], 0)

    def test_documents_are_only_read(self):
        """The source of the pictures is `rows_json` in the PROFILE database.
        Nothing here may open it for writing."""
        import inspect
        src = inspect.getsource(master_data._document_pictures)
        for verb in ("UPDATE", "INSERT", "DELETE", "commit("):
            self.assertNotIn(verb, src,
                             f"the document scan must be read-only, found {verb}")

    def test_it_matches_on_the_exact_name_only(self):
        """A backfill has no human watching it. `contained` and `similar` are
        fine for a paste; here they would mis-attach photos in bulk."""
        import inspect
        src = inspect.getsource(master_data.backfill_images)
        self.assertIn("_norm", inspect.getsource(master_data._document_pictures))
        for fuzzy in ("_similarity", "SIMILAR_MIN", "CONTAINED_MIN_OVERLAP", "resolve_items"):
            self.assertNotIn(fuzzy, src,
                             f"the backfill must not use {fuzzy} - exact names only")

    def test_an_item_that_already_has_a_picture_keeps_it(self):
        """Offered a DIFFERENT picture for an item that already has one - the
        stored one must win. Re-offering the same bytes proves nothing: the
        store is content-addressed, so the reference would be identical either
        way and an overwrite would be invisible."""
        with_pics = master_data.list_items(flags=["has_image"], limit=5)["rows"]
        if not with_pics:
            self.skipTest("no item has a picture yet")
        target = with_pics[0]
        original = target["image_ref"]
        # A 1x1 GIF, not the 1x1 PNG the other tests use: the store is
        # content-addressed, so re-offering identical bytes yields an identical
        # reference and an overwrite would be undetectable.
        other = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        self._with_fake_pictures({master_data._norm(target["name"]): {
            "name": target["name"], "column": "image", "doc": "ZZ-OLD-DOC",
            "when": "2000-01-01T00:00:00", "data_uri": other}})
        r = master_data.backfill_images(apply=True)
        self.assertEqual(r["applied"], 0, "it rewrote an item that already had a picture")
        self.assertEqual(
            master_data.item_detail(target["name"])["item"]["image_ref"], original,
            f"{target['name']}'s picture was replaced by an older document's")

    def test_a_picture_reads_back_as_an_image(self):
        """A reference whose FILE is present must decode. A reference whose file
        is absent is a different thing - an incomplete store, which
        `image_report().missing` already reports - and is what a catalog copied
        without its picture directory looks like. Conflating the two made this
        fail in a test environment rather than on a real defect."""
        rows = master_data.list_items(flags=["has_image"], limit=8)["rows"]
        if not rows:
            self.skipTest("no pictures in the catalog")
        missing = set(master_data.image_report()["missing"])
        checked = 0
        for i in rows:
            if i["image_ref"] in missing:
                continue                       # store is incomplete here, not corrupt
            checked += 1
            uri = master_data.item_image(i["name"])
            self.assertTrue(uri.startswith("data:image/"),
                            f"{i['name']}: the file is on disk but does not decode")
        if not checked:
            self.skipTest("no picture files present in this environment")

    def test_an_unresolvable_reference_is_reported_as_missing(self):
        """The invariant that holds everywhere: anything `item_image` cannot
        return must appear in the missing list, never vanish silently."""
        for i in master_data.list_items(flags=["has_image"], limit=8)["rows"]:
            if not master_data.item_image(i["name"]):
                self.assertIn(i["image_ref"], master_data.image_report()["missing"],
                              f"{i['name']}'s picture is unreadable and unreported")

    def test_the_reference_never_changes_the_row_identity(self):
        """`image_refs` is in `_SIG_IGNORE`, which is what makes an in-place
        `data_json` UPDATE safe - the row's data_sig, half its primary key,
        does not depend on it. If that ever changes, this backfill silently
        orphans every row it touches."""
        self.assertIn("image_refs", database_sig_ignore())


def database_sig_ignore():
    from backend import database
    return database._SIG_IGNORE


class DisplayCurrency(unittest.TestCase):
    """Master Data shows and takes money in USD or IQD (2026-08-13).

    The stored numbers stay USD on purpose: `margin`, the sort orders and the
    paste resolver all COMPARE items, and they can only do that in one unit.
    `money_ccy` records which unit the human wants to read, and the frontend
    converts on the way in and out - so what crosses this boundary is always
    dollars, and a dinar figure landing in a field read as dollars (the
    84,000-where-60-belongs bug) cannot happen here.
    """

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        _wipe_sentinel()
        conn = _shared()
        try:
            conn.execute("INSERT INTO dn_items(name, data_sig, data_json, use_count, "
                         "last_used_at) VALUES(?,?,?,?,?)",
                         (SENTINEL, "sigC",
                          '{"unit": "EA", "rate": "10", "cost_usd": 6}',
                          4, "2026-08-02T00:00:00"))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        _wipe_sentinel()

    def _item(self):
        return master_data.item_detail(SENTINEL)["item"]

    def test_an_item_defaults_to_dollars(self):
        self.assertEqual(self._item()["money_ccy"], "USD")

    def test_the_choice_is_stored_and_read_back(self):
        self.assertTrue(master_data.save_item(SENTINEL, {"money_ccy": "IQD"})["ok"])
        self.assertEqual(self._item()["money_ccy"], "IQD")

    def test_it_reaches_every_stored_row(self):
        """The whole point of this screen: one edit, every catalog."""
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        rows = self._item()["variant_rows"]
        self.assertTrue(rows)
        for v in rows:
            self.assertEqual(v["money_ccy"], "IQD", v["source"])

    def test_junk_is_refused_rather_than_guessed(self):
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        master_data.save_item(SENTINEL, {"money_ccy": "pesos"})
        self.assertEqual(self._item()["money_ccy"], "IQD",
                         "an unrecognised currency must not overwrite a good one")

    def test_the_numbers_are_untouched_by_the_display_choice(self):
        """Flipping the unit must not restate the money - the frontend does
        that conversion, and doing it here too would apply it twice."""
        before = self._item()
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        after = self._item()
        self.assertEqual(after["cost"], before["cost"])
        self.assertEqual(after["price"], before["price"])
        self.assertEqual(after["cost"], 6.0)

    def test_margin_is_computed_from_the_usd_pair(self):
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        it = self._item()
        self.assertEqual(it["margin"], 40.0,
                         "margin must not move when the display unit does")

    def test_cost_and_price_have_INDEPENDENT_units(self):
        """Buying in dinars while selling in dollars is ordinary. Both figures
        are stored in USD, so there is nothing unsafe about the pair
        disagreeing - tying them together was a simplification, not a rule."""
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        it = self._item()
        self.assertEqual(it["money_ccy"], "IQD", "the cost follows the chip")
        self.assertEqual(it["price_ccy"], "USD",
                         "changing the COST unit must not drag the price with it")

        master_data.save_item(SENTINEL, {"price_ccy": "IQD"})
        self.assertEqual(self._item()["price_ccy"], "IQD")
        self.assertEqual(self._item()["money_ccy"], "IQD")

        master_data.save_item(SENTINEL, {"money_ccy": "USD"})
        it = self._item()
        self.assertEqual(it["money_ccy"], "USD")
        self.assertEqual(it["price_ccy"], "IQD",
                         "the price keeps its own unit when the cost changes")

    def test_a_row_from_before_the_split_shows_both_the_same(self):
        """`price_ccy` falls back to `money_ccy`, which used to govern both, so
        items stored before the split keep showing exactly what they showed."""
        conn = _shared()
        try:
            conn.execute("UPDATE dn_items SET data_json = ? WHERE name = ?",
                         ('{"unit": "EA", "rate": "10", "cost_usd": 6,'
                          ' "money_ccy": "IQD"}', SENTINEL))
            conn.commit()
        finally:
            conn.close()
        it = self._item()
        self.assertEqual(it["money_ccy"], "IQD")
        self.assertEqual(it["price_ccy"], "IQD")

    def test_both_units_survive_an_unrelated_edit(self):
        master_data.save_item(SENTINEL, {"money_ccy": "IQD", "price_ccy": "USD"})
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        it = self._item()
        self.assertEqual(it["money_ccy"], "IQD")
        self.assertEqual(it["price_ccy"], "USD")
        self.assertEqual(it["unit"], "BOX")

    def test_neither_unit_is_part_of_the_item_identity(self):
        """A display preference must not fork a second autocomplete entry."""
        from backend import database
        for k in ("money_ccy", "price_ccy"):
            self.assertIn(k, database._SIG_IGNORE)

    def test_the_display_choice_is_not_a_free_form_extra(self):
        """`money_ccy` has its own field; leaking into `extra` would also make
        it show up as an unknown column in the variant list."""
        master_data.save_item(SENTINEL, {"money_ccy": "IQD", "price_ccy": "USD"})
        for v in self._item()["variant_rows"]:
            self.assertNotIn("money_ccy", (v.get("extra") or {}))
            self.assertNotIn("price_ccy", (v.get("extra") or {}))


class AnEditKeepsWhatItDidNotTouch(unittest.TestCase):
    """Editing an item must not destroy what the form does not show.

    Found 2026-08-13 while adding the currency toggle. `_row_data_for_sig`
    rebuilt `data_json` from the DECLARED columns only, and that dict is what
    gets written - so any save here (even one that only changed the unit) wiped
    the item's remembered `cost_usd`, its `image_refs` and anything else the
    columns cannot hold. Silent, and it applied to the 229 pictures the
    backfill had just recovered.

    Those keys are all in `_SIG_IGNORE`, so carrying them through leaves
    `data_sig` - half the row's primary key - exactly where it was.
    """

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        _wipe_sentinel()
        conn = _shared()
        try:
            conn.execute("INSERT INTO dn_items(name, data_sig, data_json, use_count, "
                         "last_used_at) VALUES(?,?,?,?,?)",
                         (SENTINEL, "sigD",
                          '{"unit": "EA", "description": "d", "rate": "10",'
                          ' "cost_usd": 6, "image_refs": {"image": "zz-fake.png"}}',
                          4, "2026-08-02T00:00:00"))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        _wipe_sentinel()

    def _row(self):
        conn = _shared()
        try:
            r = conn.execute("SELECT data_sig, data_json FROM dn_items WHERE name = ?",
                             (SENTINEL,)).fetchone()
        finally:
            conn.close()
        return (r[0], json.loads(r[1])) if r else (None, {})

    def test_a_unit_edit_keeps_the_cost(self):
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        self.assertEqual(self._row()[1].get("cost_usd"), 6.0)

    def test_a_unit_edit_keeps_the_picture(self):
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        self.assertEqual(self._row()[1].get("image_refs"), {"image": "zz-fake.png"})

    def test_the_edit_still_lands(self):
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        self.assertEqual(self._row()[1].get("unit"), "BOX")

    def _normalised_sig(self):
        """The fixture seeds a placeholder `data_sig`, so the FIRST save always
        recomputes it. Save once to settle it, then compare - otherwise the
        test measures the fixture, not the rule."""
        master_data.save_item(SENTINEL, {"description": "d"})
        return self._row()[0]

    def test_the_signature_does_not_move_for_an_ignored_key(self):
        """`money_ccy` is a display preference, not part of what the item IS.
        If it moved the sig it would fork a second autocomplete entry."""
        before = self._normalised_sig()
        master_data.save_item(SENTINEL, {"money_ccy": "IQD"})
        self.assertEqual(self._row()[0], before)

    def test_the_signature_does_not_move_for_a_new_cost(self):
        before = self._normalised_sig()
        master_data.save_item(SENTINEL, {"cost_usd": "99"})
        self.assertEqual(self._row()[0], before,
                         "re-costing an item must not fork a second entry")

    def test_the_signature_still_moves_for_a_real_change(self):
        before = self._normalised_sig()
        master_data.save_item(SENTINEL, {"unit": "BOX"})
        self.assertNotEqual(self._row()[0], before,
                            "a different unit IS a different variant")

    def test_the_two_signature_implementations_agree(self):
        """`data_manager._data_sig` claimed to be the same algorithm as
        `database._data_sig` and was not - it hashed every key, including the
        volatile ones. An item edited here then re-saved from an editor got two
        different identities and re-split into the rows this screen exists to
        unify."""
        from backend import data_manager, database
        for payload in ({"unit": "EA", "cost_usd": 6},
                        {"unit": "EA", "cost_usd": 999, "qty": 3, "rate": "10"},
                        {"unit": "EA", "image_refs": {"image": "x.png"}},
                        {"unit": "EA", "money_ccy": "IQD"}):
            self.assertEqual(data_manager._data_sig(payload),
                             database._data_sig(payload), payload)


class CostInheritanceKeepsTheCurrency(unittest.TestCase):
    """`inherit_costs` carries `cost_ccy` beside `cost` (2026-08-13).

    Inheriting the number without its unit is worse than inheriting neither: a
    dinar cost carried into a re-save with no currency is then read as the
    document's, i.e. as dollars.
    """

    def test_the_currency_rides_with_the_cost(self):
        from backend import database
        old = [{"item": "Celery", "cost": "2002", "cost_ccy": "IQD"}]
        new = database.inherit_costs(old, [{"item": "Celery"}], "item")
        self.assertEqual(new[0]["cost"], "2002")
        self.assertEqual(new[0]["cost_ccy"], "IQD")

    def test_an_incoming_currency_wins(self):
        from backend import database
        old = [{"item": "Celery", "cost": "2002", "cost_ccy": "IQD"}]
        new = database.inherit_costs(
            old, [{"item": "Celery", "cost": "1.43", "cost_ccy": "USD"}], "item")
        self.assertEqual(new[0]["cost"], "1.43")
        self.assertEqual(new[0]["cost_ccy"], "USD")

    def test_nothing_is_invented_for_an_unknown_item(self):
        from backend import database
        old = [{"item": "Celery", "cost": "2002", "cost_ccy": "IQD"}]
        new = database.inherit_costs(old, [{"item": "Onion"}], "item")
        self.assertNotIn("cost_ccy", new[0])


class PriceKeepsItsUnit(unittest.TestCase):
    """The price is stored as typed, and converted only by a document.

    Master Data used to divide a dinar price by the profile rate on entry -
    the same defect already removed from the cost. There is no deal behind a
    catalog entry, so any rate applied there is a guess; the document has a
    real one (its pinned rate, or the cash rule).
    """

    def setUp(self):
        _wipe_sentinel()
        database.remember_quote_item("materials", SENTINEL, {
            "unit": "KG", "price_native": 1500, "price_native_ccy": "IQD"})

    def tearDown(self):
        _wipe_sentinel()

    def _fill(self, **kw):
        r = master_data.resolve_items([SENTINEL], doc_type="quote",
                                      sub_type="materials", **kw)
        return r["results"][0]["fill"]

    def test_it_is_stored_in_the_unit_it_was_typed_in(self):
        # CHANGED 2026-08-20: read the item RECORD. `item_detail` reads the
        # retired catalogs; the record is where a save lands and it keeps money
        # AS TYPED with its own currency (`price` / `price_ccy`).
        row = mi.list_records(search=SENTINEL, limit=5)["rows"][0]
        self.assertEqual(row["price"], 1500)
        self.assertEqual(row["price_ccy"], "IQD")

    def test_the_same_unit_needs_no_rate_at_all(self):
        self.assertEqual(self._fill(sell_currency="IQD", sell_peg=1500)["rate"], 1500)
        self.assertEqual(self._fill(sell_currency="IQD", sell_peg=1400)["rate"], 1500,
                         "no conversion means the rate cannot change the number")

    def test_the_DOCUMENT_s_rate_decides(self):
        self.assertEqual(self._fill(sell_currency="USD", sell_peg=1500)["rate"], 1.0)
        self.assertEqual(self._fill(sell_currency="USD", sell_peg=1400)["rate"], 1.07)

    def test_a_dollar_price_converts_up_for_a_dinar_invoice(self):
        # Edited through the RECORD, which is what the resolver reads
        # (2026-08-19). `save_item` writes the four catalogs and is the old
        # Master Data screen's path.
        from backend import master_items as mi
        iid = mi.list_records(search=SENTINEL, limit=5)["rows"][0]["item_id"]
        mi.save_record(iid, {"price_native": 2, "price_native_ccy": "USD"})
        self.assertEqual(self._fill(sell_currency="IQD", sell_peg=1500)["rate"], 3000)

    def test_the_filled_price_says_which_unit_it_is_in(self):
        self.assertEqual(self._fill(sell_currency="IQD", sell_peg=1500)["rate_ccy"], "IQD")

    def test_a_dinar_price_is_stored_as_typed(self):
        """A price typed in dinars is kept in dinars, unconverted.

        CHANGED 2026-08-20: the dollar `rate` catalog column is retired - the
        record stores the price AS TYPED and never divides by a rate. Edit
        through the record (save_item / item_detail read the retired catalogs)."""
        iid = mi.list_records(search=SENTINEL, limit=5)["rows"][0]["item_id"]
        mi.save_record(iid, {"price_native": 2500, "price_native_ccy": "IQD"})
        row = mi.get_record(iid)["item"]
        self.assertEqual(row["price"], 2500)
        self.assertEqual(row["price_ccy"], "IQD")

    def test_a_dollar_price_fills_a_dollar_document_unchanged(self):
        """The converse: a price typed in dollars fills a dollar document at
        that same number. CHANGED 2026-08-20: the dollar figure is derived by
        the DOCUMENT (the resolver) now, not written into a catalog `rate`."""
        iid = mi.list_records(search=SENTINEL, limit=5)["rows"][0]["item_id"]
        mi.save_record(iid, {"price_native": 7, "price_native_ccy": "USD"})
        self.assertEqual(self._fill(sell_currency="USD", sell_peg=1500)["rate"], 7)

    # REMOVED 2026-08-20: test_the_comparison_figure_is_flagged_approximate. The
    # `price_is_approx` flag and the derived dollar comparison figure were
    # `item_detail` (retired-catalog resolver) constructs. The record sorts on
    # the native price directly, so there is no cross-currency dollar
    # comparison figure to flag.

    def test_the_screen_never_converts(self):
        js = (APP_ROOT / "static/item_master.js").read_text(encoding="utf8")
        self.assertNotIn("IQD_PER_USD", js,
                         "Master Data must not know an exchange rate at all")
        # The inputs must be bound to what was TYPED, not to the derived
        # dollar comparison figure - binding to the latter silently rewrites
        # a dinar price into dollars the next time the panel is opened.
        # The record calls them `price` / `cost` and both are stored AS TYPED,
        # so binding to them is binding to what was typed. The rule is
        # unchanged: never bind an input to a derived dollar figure.
        import re as _re
        for field, src in (("price_native", "it.price"), ("cost_native", "it.cost")):
            self.assertRegex(
                js, _re.escape(f'name="{field}"') + r'[^>]*?'
                    + _re.escape(f'value="${{esc(val({src}))}}"'),
                f"{field} must be bound to the value as TYPED")
        self.assertNotIn("cost_usd", js,
                         "the derived comparison figure must never reach an input")


if __name__ == "__main__":
    unittest.main(verbosity=2)
