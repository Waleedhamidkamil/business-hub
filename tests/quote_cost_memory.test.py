"""Remembered item costs are currency-free (2026-08-05).

Picking an item from the quotation autocomplete now fills its COST as well as
its price. Two things had to be true first, and both are invisible in the code:

1. THE MEMORY IS SHARED ACROSS EVERY QUOTE OF A TYPE. A cost typed into a dinar
   quote and replayed into a dollar one is wrong by the exchange rate - 84,000
   appearing where 60 belongs. `remember_inv_item` already normalises `rate` to
   USD for exactly this reason (see api.save_inv); costs never got the same
   treatment. They are now stored as `cost_usd`.

2. EXISTING ENTRIES ARE AMBIGUOUS. Nothing recorded which currency the stored
   `cost` was typed in, and it cannot be recovered. So `cost_usd` is both the
   value AND the marker: an item without it predates this change and is simply
   not autofilled. That is why the raw `cost` key is DROPPED rather than left
   in place - leaving it there is an invitation to read it as dollars.

The user asked for old entries to be skipped rather than guessed at.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import api, config  # noqa: E402


class CostToUsd(unittest.TestCase):
    """The normaliser itself."""

    def test_a_dollar_cost_is_unchanged(self):
        self.assertEqual(api._cost_to_usd_for_memory("60", "USD", 1400), 60.0)

    def test_a_dinar_cost_is_divided_by_the_rate(self):
        self.assertEqual(api._cost_to_usd_for_memory("84000", "IQD", 1400), 60.0)

    def test_both_currencies_converge_on_the_same_memory(self):
        """The whole point: what is remembered no longer depends on how it was typed."""
        usd = api._cost_to_usd_for_memory("60", "USD", 1400)
        iqd = api._cost_to_usd_for_memory("84000", "IQD", 1400)
        self.assertEqual(usd, iqd)

    def test_the_rate_is_honoured_not_assumed(self):
        # A cash deal settles at 1500; using the profile's 1400 would remember
        # a cost ~7% too high and quietly inflate every future quote using it.
        self.assertEqual(api._cost_to_usd_for_memory("90000", "IQD", 1500), 60.0)
        self.assertNotEqual(api._cost_to_usd_for_memory("90000", "IQD", 1500),
                            api._cost_to_usd_for_memory("90000", "IQD", 1400))

    def test_thousands_separators_survive(self):
        self.assertEqual(api._cost_to_usd_for_memory("1,400", "IQD", 1400), 1.0)

    def test_nothing_to_remember_returns_none(self):
        for raw in ("", "   ", None, "n/a", "abc"):
            self.assertIsNone(api._cost_to_usd_for_memory(raw, "USD", 1400),
                              f"{raw!r} is not a cost")

    def test_zero_and_negative_are_not_costs(self):
        # A remembered 0 would autofill as "this is free", which is a claim.
        for raw in ("0", "0.0", "-5"):
            self.assertIsNone(api._cost_to_usd_for_memory(raw, "USD", 1400))

    def test_a_broken_rate_falls_back_rather_than_dividing_by_zero(self):
        self.assertEqual(api._cost_to_usd_for_memory("84000", "IQD", 0),
                         84000 / config.IQD_PER_USD)

    def test_case_and_padding_on_the_currency(self):
        self.assertEqual(api._cost_to_usd_for_memory("84000", " iqd ", 1400), 60.0)


class RememberedShape(unittest.TestCase):
    """What actually lands in quote_items.data_json."""

    def _remember(self, row, currency="USD", peg=1400.0):
        """Calls the REAL builder, not a copy of it.

        This used to reimplement the remember block inline, which meant the
        assertions below passed while the actual code was mutated out from
        under them - dropping the `data.pop("cost")` went undetected. The block
        was extracted to `api._item_memory_payload` precisely so this could
        call it.
        """
        schema = config.QUOTE_TYPES.get("materials", {})
        primary = schema.get("primary_key")
        image_keys = {c["key"] for c in schema.get("columns", [])
                      if c.get("kind") == "image"}
        return api._item_memory_payload(row, primary, image_keys, currency, peg)

    def setUp(self):
        self.pk = config.QUOTE_TYPES["materials"]["primary_key"]

    def test_the_ambiguous_raw_cost_is_not_kept(self):
        data = self._remember({self.pk: "X", "rate": "100", "cost": "84000"},
                              "IQD", 1400)
        self.assertNotIn("cost", data,
                         "keeping the raw cost alongside cost_usd invites someone "
                         "to read it as dollars - it is the ambiguity itself")
        self.assertEqual(data["cost_usd"], 60.0)

    def test_an_item_with_no_cost_gets_no_marker(self):
        data = self._remember({self.pk: "X", "rate": "100"})
        self.assertNotIn("cost_usd", data)

    def test_other_columns_are_untouched(self):
        data = self._remember({self.pk: "X", "rate": "100", "qty": "2",
                               "unit": "pcs", "cost": "60"})
        self.assertEqual(data["rate"], "100")
        self.assertEqual(data["qty"], "2")
        self.assertEqual(data["unit"], "pcs")

    def test_images_are_still_excluded(self):
        # A 200KB base64 string per row is what the image filter exists to stop;
        # the cost work must not have reopened that.
        img_cols = [c["key"] for c in config.QUOTE_TYPES["materials"].get("columns", [])
                    if c.get("kind") == "image"]
        if not img_cols:
            self.skipTest("this quote type has no image column")
        row = {self.pk: "X", "rate": "100", img_cols[0]: "data:image/png;base64,AAAA"}
        self.assertNotIn(img_cols[0], self._remember(row))


class FrontendContract(unittest.TestCase):
    """The editor half, checked at source level."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/quote.js").read_text(encoding="utf8")

    def test_the_payload_sends_the_cost_currency(self):
        # Without this the backend normalises against the PREVIOUS currency:
        # quote.js calls set_cost_currency only AFTER save_quote returns.
        self.assertIn("cost_currency: quoteState.costCurrency", self.js)
        self.assertIn("cost_iqd_per_usd: quotePegSync()", self.js)

    def test_the_pick_converts_at_THIS_quote_s_rate(self):
        """2026-08-13 - the conversion moved into `window.costFromMemory`
        (app.js), shared by all three editors. What matters has not changed and
        is now stronger: the rate handed to it is the QUOTE's own
        (`quotePegSync()` -> the pinned rate, else `doc_iqd_per_usd`, which
        applies the cash rule), never a flat profile rate."""
        # 2026-09-05 - this assertion named the helper `costFromMemory`, which
        # was renamed `costCellFromMemory` on 2026-08-26 (it answers with the
        # number AND its currency now). Nothing about the behaviour changed, but
        # the string did, so this file sat red for ten days and got written off
        # as noise - which is how the genuinely broken things around it stayed
        # invisible. Source-string assertions cost exactly this.
        #
        # Pin the ARGUMENTS instead: they are the substance. Whatever the helper
        # is called, the rate it is handed must be the QUOTE's own
        # (`quotePegSync()` -> the pinned rate, else `doc_iqd_per_usd`, which
        # applies the cash rule), never a flat profile rate.
        self.assertRegex(
            self.js,
            r"window\.cost(?:Cell)?FromMemory\(\s*s\.data,\s*"
            r"quoteState\.costCurrency,\s*quotePegSync\(\)\s*\)",
            "the autocomplete pick must convert the remembered cost at THIS "
            "quote's rate")
        app = (ROOT / "static/app.js").read_text(encoding="utf8")
        self.assertRegex(app, r"function cost(?:Cell)?FromMemory\(",
                         "the shared helper must live in app.js, not be copied "
                         "into each editor")
        # The catalog may not supply a rate of its own.
        self.assertNotIn("s.data.cost_iqd_per_usd", self.js)

    def test_cost_usd_is_never_written_as_a_column(self):
        # Skipped via the shared `window.ITEM_META_KEYS` set (app.js) since
        # 2026-08-16. Same guarantee, now covering `img_query` and the
        # other-language name too - those reached the dropdown subtitle and
        # were reported as "arabic suggestions in quotation".
        self.assertIn("if (window.ITEM_META_KEYS.has(k)) continue;", self.js,
                      "cost_usd is a memory key, not a table column - writing it "
                      "onto the row would save it into rows_json as a phantom field")
        app_js = (ROOT / "static/app.js").read_text(encoding="utf8")
        self.assertIn('"cost_usd"', app_js.split("ITEM_META_KEYS = new Set([")[1][:120],
                      "cost_usd must still be one of the skipped keys")

    def test_the_cost_cell_is_repainted(self):
        """editableColumns() excludes the synthetic COST column, which is why the
        remembered cost used to reach state but never the screen.

        Comments are stripped first: the explanatory note now sitting above that
        line reads "quoteRenderColumns(), NOT editableColumns()", which satisfies
        a naive `assertNotIn` search and makes the check pass no matter what the
        code does. It did exactly that on the first run.
        """
        import re
        at = self.js.index("function pickQuoteSuggestion")
        body = self.js[at:self.js.index("\n}", at)]
        body = re.sub(r"/\*[\s\S]*?\*/", "", body)
        body = re.sub(r"//[^\n]*", "", body)
        self.assertIn("quoteRenderColumns()", body)
        self.assertNotIn("editableColumns()", body,
                         "the DOM refresh must cover the COST column")


class PinnedCostRate(unittest.TestCase):
    """The editable rate in "converted at 1 $ = 1,400 IQD"."""

    DOC = "QUT-TEST-PINRATE-9901"

    def setUp(self):
        from backend import database
        self.db = database
        self.addCleanup(self._forget)
        self._forget()

    def _forget(self):
        import sqlite3
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("DELETE FROM doc_cost_meta WHERE doc_no = ?", (self.DOC,))
            conn.commit()

    def test_unpinned_documents_keep_the_derived_rate(self):
        """Every quotation that exists today is unpinned; none may move."""
        self.assertIsNone(self.db.get_cost_rate("quote", self.DOC))
        self.assertEqual(self.db.doc_iqd_per_usd("quote", self.DOC),
                         config.CASH_IQD_PER_USD
                         if config.ACTIVE_PROFILE == "eshraqat" else config.IQD_PER_USD)

    def test_a_pinned_rate_wins(self):
        self.db.set_cost_rate("quote", self.DOC, 1480)
        self.assertEqual(self.db.get_cost_rate("quote", self.DOC), 1480.0)
        self.assertEqual(self.db.doc_iqd_per_usd("quote", self.DOC), 1480.0)

    def test_switching_to_usd_does_not_discard_the_pin(self):
        """set_cost_currency DELETEs the row for USD - and the rate lives in it.

        Flipping IQD -> USD -> IQD must not silently lose a rate the user set.
        """
        self.db.set_cost_rate("quote", self.DOC, 1480)
        self.db.set_cost_currency("quote", self.DOC, "USD")
        self.assertEqual(self.db.get_cost_rate("quote", self.DOC), 1480.0,
                         "the pinned rate was thrown away by the USD switch")

    def test_clearing_the_pin_restores_the_derived_rate(self):
        self.db.set_cost_rate("quote", self.DOC, 1480)
        self.db.set_cost_rate("quote", self.DOC, 0)
        self.assertIsNone(self.db.get_cost_rate("quote", self.DOC))
        self.assertEqual(self.db.doc_iqd_per_usd("quote", self.DOC), config.IQD_PER_USD)

    def test_a_nonsense_rate_is_refused_at_the_bridge(self):
        a = api.Api()
        for bad in (1.5, 150000, -3):
            res = a.set_cost_rate("quote", self.DOC, bad)
            self.assertFalse(res.get("ok"), f"{bad} should be refused")
        self.assertIsNone(self.db.get_cost_rate("quote", self.DOC))

    def test_the_base_schema_declares_the_column(self):
        # Migrations never run on a NEW profile database - the same trap the
        # invoice sell rate hit.
        self.assertIn("rate", self.db._SCHEMA[self.db._SCHEMA.index("doc_cost_meta"):
                                              self.db._SCHEMA.index("doc_cost_meta") + 400],
                      "doc_cost_meta.rate is missing from the base DDL")

    def test_the_editor_guards_the_new_bridge_method(self):
        """F5 without a restart = new JS, old Python.

        An unguarded call to a brand-new bridge method throws inside
        loadQuoteForEdit and the quotation never opens. The existing
        editor_new_reset suite caught exactly this.
        """
        js = (ROOT / "static/quote.js").read_text(encoding="utf8")
        at = js.index("get_cost_rate")
        window = js[max(0, at - 500):at]
        self.assertIn("typeof getRate === \"function\"", js,
                      "the get_cost_rate call must be feature-detected")


class PerLineCostCurrency(unittest.TestCase):
    """Each COST cell carries its own currency (2026-08-06).

    One table can price one line in dollars and the next in dinars. Before
    this, the currency was a document-level toggle, so a remembered dinar cost
    dropped into a dollar quote read as dollars - the user hit exactly that:
    40,000 IQD autofilled into a $ cell and treated as $40,000.
    """

    def setUp(self):
        from backend import database
        self.db = database

    def test_the_rows_own_currency_wins(self):
        self.assertEqual(self.db.row_cost_currency({"cost_ccy": "IQD"}, "USD"), "IQD")
        self.assertEqual(self.db.row_cost_currency({"cost_ccy": "usd"}, "IQD"), "USD")

    def test_a_row_without_one_inherits_the_document(self):
        # Every row saved before this existed.
        self.assertEqual(self.db.row_cost_currency({}, "IQD"), "IQD")
        self.assertEqual(self.db.row_cost_currency({"cost_ccy": ""}, "IQD"), "IQD")

    def test_junk_falls_back_rather_than_guessing(self):
        # Guessing wrong divides a dollar cost by 1400.
        for bad in ("GBP", "x", "123", None):
            self.assertEqual(self.db.row_cost_currency({"cost_ccy": bad}, "USD"), "USD")

    def test_two_currencies_in_one_table_both_come_out_right(self):
        """The headline requirement."""
        usd_row = {"qty": "1", "rate": "100", "cost": "60", "cost_ccy": "USD"}
        iqd_row = {"qty": "1", "rate": "100", "cost": "84000", "cost_ccy": "IQD"}
        for doc_ccy in ("USD", "IQD"):
            with self.subTest(document_currency=doc_ccy):
                a = self.db._line_econ(dict(usd_row), "name", doc_ccy, 1400.0, "USD")
                b = self.db._line_econ(dict(iqd_row), "name", doc_ccy, 1400.0, "USD")
                self.assertAlmostEqual(a["line_cost"], 60.0)
                self.assertAlmostEqual(b["line_cost"], 60.0,
                                       msg="a dinar line must not be read as dollars")

    def test_the_memory_normalises_per_row_not_per_document(self):
        pk = config.QUOTE_TYPES["materials"]["primary_key"]
        # A dinar line inside a DOLLAR document.
        d = api._item_memory_payload(
            {pk: "X", "cost": "84000", "cost_ccy": "IQD"}, pk, set(), "USD", 1400)
        self.assertAlmostEqual(d["cost_usd"], 60.0,
                               msg="normalising at the document currency would "
                                   "remember 84,000 as $84,000")

    def test_cost_ccy_is_not_remembered_as_a_field(self):
        pk = config.QUOTE_TYPES["materials"]["primary_key"]
        d = api._item_memory_payload(
            {pk: "X", "cost": "60", "cost_ccy": "USD"}, pk, set(), "USD", 1400)
        self.assertNotIn("cost_ccy", d,
                         "the remembered cost is already normalised to USD, so a "
                         "currency alongside it is meaningless and would be read "
                         "as authoritative")


class AutocompleteNoise(unittest.TestCase):
    """One suggestion per item, and it is the useful one (2026-08-06)."""

    def setUp(self):
        from backend import database
        self.db = database

    def test_volatile_values_do_not_create_variants(self):
        """`rate` in the signature is what turned one BUNKER into eighteen."""
        base = {"description": "Concrete", "unit": "EA"}
        a = self.db._data_sig({**base, "qty": "4", "rate": "1750", "cost": "1300"})
        b = self.db._data_sig({**base, "qty": "9", "rate": "2400", "cost": "1800"})
        self.assertEqual(a, b, "re-quoting an item at a new price must UPDATE it, "
                               "not mint another autocomplete entry")

    def test_descriptive_differences_still_separate(self):
        a = self.db._data_sig({"description": "Portland Type I", "unit": "EA"})
        b = self.db._data_sig({"description": "OPC", "unit": "EA"})
        self.assertNotEqual(a, b, "genuinely different items must stay distinct")

    def test_the_two_implementations_agree(self):
        """`shared_library._data_sig` is a second copy used when copying
        catalogs between profiles. If they disagree, a copy re-splits every
        item it touches."""
        from backend import shared_library
        for data in ({"description": "Concrete", "unit": "EA", "rate": "1750"},
                     {"description": "OPC", "qty": "9"},
                     {}):
            self.assertEqual(self.db._data_sig(data), shared_library._data_sig(data),
                             f"the two _data_sig implementations disagree on {data}")

    def test_suggestions_collapse_only_when_they_are_the_SAME_item(self):
        """Collapsing is keyed on name AND SPEC (2026-08-13). Keyed on the name
        alone it hid genuine variants - "Hemp rope" phi12/phi15/phi20 showed as
        one rope - while still leaving the redundant rows it was meant to
        remove. A name may legitimately appear more than once here; what may
        not appear twice is the same (name, description, unit)."""
        for q in ("a", "b", "c", "p", "s"):
            out = self.db.suggest_quote_items("materials", q, limit=20)
            keys = [(s["name"].strip().lower(),
                     str(s["data"].get("description", "")).strip().lower(),
                     str(s["data"].get("unit", "")).strip().lower()) for s in out]
            self.assertEqual(len(keys), len(set(keys)),
                             f"the same item appeared more than once: {keys}")

    def test_the_entry_with_a_cost_wins(self):
        """The whole point of picking it is to get the cost."""
        res = self.db.suggest_quote_items("materials", "Breaker")
        self.assertTrue(res, "expected a suggestion for 'Breaker'")
        top = res[0]
        # `cost_usd` was the key when a cost was only ever a dollar figure.
        # Costs are stored AS TYPED now (`cost_native` + its currency) and a
        # suggestion is built from the item record, so either spelling counts.
        cost = top["data"].get("cost_native", top["data"].get("cost_usd", ""))
        self.assertTrue(str(cost).strip(),
                        "an entry carrying a cost must outrank one without - "
                        f"got {top['data']}")


class PickSkipsAmbiguousCosts(unittest.TestCase):
    """The bug the user hit: 40,000 IQD autofilled into a dollar cell."""

    def test_the_raw_cost_key_is_never_copied(self):
        js = (ROOT / "static/quote.js").read_text(encoding="utf8")
        at = js.index("function pickQuoteSuggestion")
        body = js[at:js.index("\n}", at)]
        self.assertIn('if (k === "cost" || k === "cost_ccy") continue;', body,
                      "the generic copy loop must skip the raw `cost`. Entries "
                      "remembered before 2026-08-05 hold a cost in an unrecorded "
                      "currency; copying one into a dollar cell shows 40,000 as "
                      "$40,000. The cost comes from cost_usd or not at all.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
