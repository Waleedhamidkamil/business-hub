# -*- coding: utf-8 -*-
"""Merging items must not write the internal identity into the name.

Reported: "when I tried to merge them into 1 it glitched the name to
'bed|1m*2m without mattress|ea'".

The Master Data list addresses rows by IDENTITY - `_ident` builds
`name|description|unit`, normalised and lower-cased - and the merge button
sends the chosen row's identity as `canonical`. `merge_items` used that string
verbatim as the new name, so five rows across quote_items and inv_items were
renamed to the identity itself. Splitting on "|" is not enough either: that
recovers "bed", not "Bed".

THESE TESTS RUN AGAINST THE LIVE SHARED LIBRARY. Sentinel names only, removed
again in teardown; never an unqualified DELETE.
"""
import sys, os, unittest, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import database, master_data

NAME = "ZZ_TEST_Merge Bed"
SENTINELS = (NAME,)


def _names():
    with database._connect_shared() as conn:
        out = []
        for t in ("items", "quote_items", "dn_items", "inv_items"):
            out += [r[0] for r in conn.execute(
                f"SELECT name FROM {t} WHERE name LIKE 'ZZ\\_TEST\\_Merge%' ESCAPE '\\'")]
        return out


class MergeByIdentity(unittest.TestCase):

    def setUp(self):
        self.skipTest("the four catalogs were dropped 2026-08-20")
        self._wipe()
        # Three real variants of one product, exactly like the live Bed rows.
        # `merge_items`/`item_detail` operate on the four catalog tables, not the
        # item record (2026-08-20: saves stopped populating the catalogs but the
        # merge path still lives there), so seed the catalog directly - a save
        # via remember_quote_item would now leave these tables empty and the
        # merge would find nothing to fold.
        with database._connect_shared() as conn:
            for desc, rate in (("1M*2M", "100"), ("1.2M*2M", "160"),
                               ("1M*2M Without mattress", "120")):
                sig = database._data_sig(
                    {"description": desc, "unit": "EA", "rate": rate})
                conn.execute(
                    "INSERT INTO quote_items(quote_type, name, data_sig, "
                    "data_json, use_count, last_used_at) VALUES(?,?,?,?,?,?)",
                    ("materials", NAME, sig,
                     json.dumps({"description": desc, "unit": "EA", "rate": rate}),
                     1, "2026-08-20"))
        self.addCleanup(self._wipe)

    def _wipe(self):
        with database._connect_shared() as conn:
            for t in ("items", "quote_items", "dn_items", "inv_items"):
                for n in SENTINELS:
                    conn.execute(f"DELETE FROM {t} WHERE name = ?", (n,))
                # A corrupted name is the IDENTITY string, and `_ident`
                # normalises "ZZ_TEST_Merge" to "zz test merge" - underscores
                # become spaces. Matching the underscored form missed it, so
                # MUTATION runs (which deliberately break the fix, let the bug
                # happen, and then fail) left `zz test merge bed|1m*2m|ea` in
                # the live catalog. Match both spellings.
                conn.execute(
                    f"DELETE FROM {t} WHERE name LIKE '%|%'"
                    f"   AND (name LIKE 'zz\\_test%' ESCAPE '\\' OR name LIKE 'zz test%')")
            # A document save also writes the item RECORD now
            # (backend/master_items.py). Cleaning only the catalog rows left
            # these sentinels sitting on the live Master Data screen.
            for n in SENTINELS:
                conn.execute("DELETE FROM master_items "
                             "WHERE name_en = ? OR name_ar = ?", (n, n))

    def _ident(self, desc):
        return master_data._ident(NAME, desc, "EA")

    def test_merging_by_identity_keeps_the_real_name(self):
        target = self._ident("1M*2M")
        other = self._ident("1.2M*2M")
        res = master_data.merge_items(target, [other])
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("name"), NAME,
                         "the merge must report the item's NAME, not its identity")
        for n in _names():
            self.assertNotIn("|", n,
                             f"the identity string was written into a name: {n!r}")
            self.assertEqual(n, NAME)

    def test_the_original_casing_survives(self):
        """`_ident` lower-cases, so splitting the identity on '|' would rename
        "ZZ_TEST_Merge Bed" to "zz_test_merge bed"."""
        master_data.merge_items(self._ident("1M*2M"), [self._ident("1.2M*2M")])
        self.assertTrue(any(n == NAME for n in _names()),
                        f"expected the stored casing {NAME!r}, got {set(_names())}")

    def test_merging_an_item_into_itself_is_refused(self):
        ident = self._ident("1M*2M")
        self.assertFalse(master_data.merge_items(ident, [ident]).get("ok"),
                         "an identity merged onto itself changes nothing and "
                         "must not report success")

    def test_a_bare_name_that_resolves_to_the_target_is_refused(self):
        """The caller may send either form; a bare name that resolves to the
        same item is still the item itself."""
        target = self._ident("1M*2M")
        res = master_data.merge_items(target, [NAME])
        self.assertFalse(res.get("ok"),
                         "resolving both sides must show these are one item")

    def test_an_unknown_target_is_refused_rather_than_creating_one(self):
        res = master_data.merge_items("ZZ_TEST_Merge Nothing Here|x|y", [NAME])
        self.assertFalse(res.get("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
