"""Finding a reference picture online (2026-08-13).

`backend/image_search.py` is the ONLY part of this app that talks to the
internet. What matters is not that it finds pictures - it is that it refuses
to hand back the wrong one, and that it cannot hang or flood the editor when
the network misbehaves.

The scoring tests are the heart of this file, and each is a real failure the
first implementation had:

  * "Angle iron 75mm" -> "Flat Iron at Night" shares the word `iron`. A plain
    coverage score rated that 0.5 and used it. A photograph of a building in a
    steel line is worse than a blank cell, because a filled cell is never
    re-checked.
  * "Sledgehammer" -> "Various sledge hammers and an axe" is the RIGHT hit and
    scored ZERO on token overlap: the catalog spells it as one word and the
    world spells it as two.

Network tests are skipped when offline, so the file still passes on a
disconnected machine - but the pure logic (query cleaning, scoring, the
download guards) is exercised every run.
"""
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import image_search as s  # noqa: E402


def _online():
    try:
        socket.setdefaulttimeout(6)
        socket.getaddrinfo("www.bing.com", 443)
        return True
    except Exception:
        return False


ONLINE = _online()


class QueryCleaning(unittest.TestCase):
    """Measurements are noise. No photograph is titled "75mm*75mm*6m"."""

    def test_dimensions_are_stripped(self):
        self.assertEqual(s._clean_query("Angle iron", "75mm*75mm*6m"), "angle iron")

    def test_a_greek_diameter_is_stripped(self):
        self.assertEqual(s._clean_query("Hemp rope", "φ20mm"), "hemp rope")

    def test_a_size_code_is_stripped(self):
        self.assertEqual(s._clean_query("Sledgehammer", "12P"), "sledgehammer")

    def test_a_brand_survives(self):
        """Stripping must not eat the words that identify the thing."""
        q = s._clean_query("Drinking water", "15 L Durratal Alkhaleej")
        self.assertIn("drinking water", q)
        self.assertIn("durratal", q)

    def test_the_name_leads(self):
        self.assertTrue(s._clean_query("Hemp rope", "strong").startswith("hemp rope"))

    def test_it_is_bounded(self):
        long_desc = " ".join(f"word{i}" for i in range(40))
        self.assertLessEqual(len(s._clean_query("Thing", long_desc).split()), 6)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(s._clean_query("", ""), "")
        self.assertEqual(s._clean_query("   ", "12mm"), "")


class Scoring(unittest.TestCase):

    def test_a_shared_word_is_NOT_a_match(self):
        """The one that motivated the whole scorer."""
        self.assertLess(s._score("angle iron", "Flat Iron at Night"), s.MIN_SCORE)

    def test_a_compound_word_IS_a_match(self):
        """One word here, two words in the wild."""
        self.assertGreaterEqual(
            s._score("sledgehammer", "Various sledge hammers and an axe"), s.MIN_SCORE)

    def test_the_obvious_hit_scores_top(self):
        """High, not exactly 1.0 - the score also rewards a tight title, and
        pinning the exact number just makes the test brittle."""
        self.assertGreaterEqual(s._score("hemp rope", "Hemp Rope 1740"), 0.8)

    def test_the_right_object_beats_the_wrong_one(self):
        good = s._score("angle iron", "Cutting Angle Iron")
        bad = s._score("angle iron", "Flat Iron at Night")
        self.assertGreater(good, bad)
        self.assertGreaterEqual(good, s.MIN_SCORE)
        self.assertLess(bad, s.MIN_SCORE)

    def test_an_unrelated_title_scores_nothing(self):
        self.assertLess(s._score("celery", "Vintage Motorcycle"), s.MIN_SCORE)

    def test_an_empty_query_never_matches(self):
        self.assertEqual(s._score("", "anything at all"), 0.0)

    def test_two_thirds_of_the_query_is_a_match(self):
        """Merchant titles rarely repeat every word of an item name. Demanding
        all of them meant the PRECISE query was thrown away and a vaguer one
        used instead - "Oil drum binding strap" fell back to "material ratchet"
        and found a generic strap."""
        self.assertGreaterEqual(s._score("oil drum binding strap",
                                         "Heavy-Duty Vertical Oil Drum Lifting Strap"),
                                s.MIN_SCORE)

    def test_half_the_query_is_still_NOT_a_match(self):
        """The band boundary is the whole point: 3-of-4 passes, 1-of-2 does
        not. If this stops holding, "Flat Iron at Night" comes back."""
        self.assertLess(s._score("angle iron", "Flat Iron at Night"), s.MIN_SCORE)
        self.assertLess(s._score("chest freezer", "Chest Workout Guide"), s.MIN_SCORE)

    def test_three_of_five_words_is_NOT_enough(self):
        """The lower band has to stay a band. Grading partials on a single
        straight line rather than two rates the same 0.6 coverage above the
        threshold, and a five-word item name matching only three of its words
        is not the same object."""
        self.assertLess(s._score("oil drum binding strap set", "Oil Drum Strap"),
                        s.MIN_SCORE)

    def test_a_full_match_still_outranks_a_partial_one(self):
        full = s._score("oil drum strap", "Oil Drum Strap")
        part = s._score("oil drum binding strap", "Oil Drum Lifting Strap")
        self.assertGreater(full, part)


class Arabic(unittest.TestCase):
    """A large part of this catalog is Arabic, and the first version could not
    search ANY of it: the word pattern was `[a-z0-9]+`, so an Arabic name
    produced zero words and the button answered "nothing to search for"."""

    def test_arabic_words_are_extracted_at_all(self):
        self.assertTrue(s._query_terms("سبلت جداري", ""),
                        "an Arabic name must produce a query")

    def test_the_head_noun_is_the_FIRST_word_in_arabic(self):
        """Arabic leads with the noun and trails the adjectives - the opposite
        of English. "ستاند خوذ سلامة" is a *stand*, for safety helmets."""
        self.assertEqual(s._head_word("ستاند خوذ سلامة"), "ستاند")
        self.assertEqual(s._head_word("سبلت جداري"), "سبلت")

    def test_english_still_takes_the_LAST_word(self):
        self.assertEqual(s._head_word("Cotton Rectangular Cleaning Mop"), "mop")

    def test_the_ladder_sheds_from_the_tail_in_arabic(self):
        """Shortening from the front would drop the object and search the
        adjective: "خوذ سلامة" finds helmets, never the stand.

        FOUR words, deliberately. With three the ladder's widest rung is the
        whole name either way, so flipping the direction changes nothing and
        a three-word name proves nothing - the first version of this test
        passed against the bug it was written to catch."""
        terms = s._query_terms("ستاند خوذ سلامة معدني", "")
        self.assertGreaterEqual(len(terms), 3, f"expected a full ladder: {terms}")
        self.assertTrue(all(t.startswith("ستاند") for t in terms),
                        f"every Arabic query must keep the head noun: {terms}")

    def test_the_shortest_arabic_rung_still_leads_with_the_noun(self):
        terms = s._query_terms("ستاند خوذ سلامة معدني", "")
        self.assertEqual(terms[-1], "ستاند خوذ")

    def test_the_ladder_sheds_from_the_front_in_english(self):
        terms = s._query_terms("Cotton Rectangular Cleaning Mop", "")
        self.assertTrue(all(t.endswith("mop") for t in terms), terms)


class TheDiameterSymbol(unittest.TestCase):
    """Making the word pattern Unicode-aware had a side effect: "φ20mm" became
    one word beginning with a letter, so it survived the unit stripper and
    "Hemp rope φ20mm" searched `rope φ20mm` - which found a STEEL WIRE rope."""

    def test_it_is_stripped(self):
        self.assertEqual(s._clean_query("Hemp rope", "φ20mm"), "hemp rope")

    def test_every_query_in_the_ladder_is_clean(self):
        for t in s._query_terms("Hemp rope", "φ20mm"):
            self.assertNotIn("φ", t)
            self.assertNotIn("20mm", t)

    def test_other_diameter_marks_too(self):
        for mark in ("Ø20mm", "⌀20mm", "Φ20mm"):
            self.assertEqual(s._clean_query("Hemp rope", mark), "hemp rope", mark)


class ReferenceCodesAreNotSearched(unittest.TestCase):
    """Measured on 40 random catalog rows, every wrong picture was of something
    that is not a physical good, and two were document references. "TDC-DN-7"
    found a YouTube thumbnail called *"TDC - DN - YouTube"* - a GENUINE word
    match, so no score can reject it. It has to be refused before it is sent.
    """

    CODES = ["TDC-DN-7", "DN-TDC-029", "A-71", "Q-2026-001", "PO-14/8", "INV-3"]

    # The expensive half of this rule. A guard that skips real items is worse
    # than the wrong picture it prevents, because the user never sees why.
    NOT_CODES = ["T-shirt", "Quick-drying cement", "X-ray film", "PVC-U pipe",
                 "Bus", "Sugar", "Water Melon", "Engine Oil 15W40, API CI-4",
                 "PLUG RJ 45 BLUE STORM CAT6", "Non-stick frying pan",
                 "سبلت جداري", "ستاند خوذ سلامة",
                 # Manufacturer part numbers. Both are in the live catalog and
                 # both were verified to find the RIGHT product on Bing - the
                 # first returns the Hikvision camera, the second the NVR. A
                 # guard that blocks these costs more than it saves.
                 "HIK-IPC-2CD1067G2-L(4MM)(6MP)", "HIK-NVR-7616NXI-I2/S"]

    def test_a_part_number_is_not_a_document_code(self):
        """The difference is the shape of the segments: a part number has a
        long mixed letter-and-digit run, a document reference has short letter
        groups and a serial."""
        self.assertTrue(s._is_part_number("HIK-IPC-2CD1067G2-L"))
        self.assertTrue(s._is_part_number("HIK-NVR-7616NXI-I2/S"))
        self.assertFalse(s._is_part_number("DN-TDC-029"))
        self.assertFalse(s._is_part_number("TDC-DN-7"))
        self.assertFalse(s._is_part_number("Q-2026-001"))

    def test_codes_are_recognised(self):
        for c in self.CODES:
            self.assertTrue(s._looks_like_a_code(c), c)

    def test_real_items_are_NOT_recognised_as_codes(self):
        for n in self.NOT_CODES:
            self.assertFalse(s._looks_like_a_code(n), n)

    def test_a_word_with_no_hyphen_is_never_a_code(self):
        """Without this half, every short item name is a code."""
        self.assertFalse(s._looks_like_a_code("Bus"))
        self.assertFalse(s._looks_like_a_code("TDC"))

    def test_nothing_is_sent_for_a_code(self):
        asked = []

        def spy(q):
            asked.append(q)
            return []

        with mock.patch.dict(s.PROVIDERS, {"bing": spy, "openverse": spy,
                                           "commons": spy}):
            r = s.search("TDC-DN-7", "")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("not_an_item"))
        self.assertEqual(asked, [], "a code must not reach the network at all")

    def test_the_refusal_says_why(self):
        r = s.search("DN-TDC-029", "")
        self.assertIn("reference code", r["error"])
        self.assertIn("DN-TDC-029", r["error"])

    def test_a_coded_row_WITH_a_description_searches_the_description(self):
        """A row can be named by its reference and described properly. Throwing
        it away would lose a findable item."""
        asked = []

        def spy(q):
            asked.append(q)
            return [{"title": "Toilet Brush", "url": "https://x/y.jpg",
                     "source": "bing", "license": ""}]

        with mock.patch.dict(s.PROVIDERS, {"bing": spy, "openverse": lambda q: [],
                                           "commons": lambda q: []}):
            r = s.search("TDC-DN-7", "Toilet Brush")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(asked, "it should have searched")
        for q in asked:
            self.assertNotIn("tdc", q, f"the code must not ride along: {q}")
            self.assertNotIn("dn-", q)

    def test_a_real_item_still_reaches_the_network(self):
        asked = []

        def spy(q):
            asked.append(q)
            return [{"title": "Quick Drying Cement", "url": "https://x/y.jpg",
                     "source": "bing", "license": ""}]

        with mock.patch.dict(s.PROVIDERS, {"bing": spy, "openverse": lambda q: [],
                                           "commons": lambda q: []}):
            r = s.search("Quick-drying cement", "")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(asked)


class TranslatedSecondPass(unittest.TestCase):
    """Bing's ARABIC index is mostly recipe blogs and clip-art. Measured on the
    real PO A-61 it "found" 17 of 20 and returned a line DRAWING for جزر, and
    PICKLES for خيار. The English index for the same objects is full of clean
    product photographs, so an Arabic item is searched TWICE - as typed, and
    translated - and the better result wins.
    """

    def setUp(self):
        s._TRANSLATE_CACHE.clear()
        s._TRANSLATE_FAILED[0] = False

    tearDown = setUp

    def _providers(self, by_query):
        def fake(q):
            return by_query.get(q, [])
        return mock.patch.dict(s.PROVIDERS, {"bing": fake,
                                             "openverse": lambda q: [],
                                             "commons": lambda q: []})

    @staticmethod
    def _hit(title):
        return [{"title": title, "url": "https://x/" + title[:4] + ".jpg",
                 "source": "bing", "license": ""}]

    def test_english_wins_when_it_scores_higher(self):
        """جزر alone finds a DRAWING of a carrot; the translation finds one."""
        with mock.patch.object(s, "translate_to_english", return_value="carrot"):
            with self._providers({"جزر": self._hit("رسم جزر"),
                                  "carrot": self._hit("Carrot")}):
                r = s.search("جزر", "")
        self.assertTrue(r["ok"])
        self.assertEqual(r["title"], "Carrot")
        self.assertTrue(r.get("via_translation"))
        self.assertEqual(r.get("english"), "carrot")

    def test_the_original_wins_when_the_translation_is_a_homonym(self):
        """خيار is both "cucumber" and "option". Translating it finds options
        TRADING, so the Arabic result has to be allowed to win."""
        with mock.patch.object(s, "translate_to_english", return_value="option"):
            with self._providers({"خيار": self._hit("مخلل خيار عضوي"),
                                  "option": self._hit("What is options trading? A guide")}):
                r = s.search("خيار", "")
        self.assertTrue(r["ok"])
        self.assertEqual(r["title"], "مخلل خيار عضوي")
        self.assertFalse(r.get("via_translation"))

    def test_a_translation_rescues_an_item_that_found_NOTHING(self):
        """ستاند خوذ سلامة returned nothing at all before this."""
        with mock.patch.object(s, "translate_to_english",
                               return_value="Safety helmet stand"):
            # lower-case: `_query_terms` normalises before it queries.
            with self._providers({"safety helmet stand":
                                  self._hit("Stainless Steel Safety Helmet Stand")}):
                r = s.search("ستاند خوذ سلامة", "")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(r.get("via_translation"))

    def test_a_confident_translation_beats_a_WEAK_original(self):
        """The harder half of "a hit beats a refusal": the original did return
        a candidate, just not a convincing one. Comparing the two refusals'
        `best_guess` instead of their ok-ness keeps the weak original and
        throws the good translation away."""
        with mock.patch.object(s, "translate_to_english",
                               return_value="chinese cabbage"):
            with self._providers({
                    # contains the head noun, so it is scored - but only one
                    # of the two query words, which is below the floor.
                    "لهانة صيني": self._hit("لهانة"),
                    "chinese cabbage": self._hit("Chinese Cabbage")}):
                r = s.search("لهانة صيني", "")
        self.assertTrue(r["ok"], f"expected the translation to win: {r}")
        self.assertEqual(r["title"], "Chinese Cabbage")
        self.assertTrue(r.get("via_translation"))

    def test_english_items_are_NOT_translated(self):
        """A round trip for an English name buys nothing and costs a request
        per item."""
        called = []

        def spy(t):
            called.append(t)
            return "whatever"

        with mock.patch.object(s, "translate_to_english", side_effect=spy):
            with self._providers({"toilet brush": self._hit("Toilet Brushes")}):
                r = s.search("Toilet Brush", "")
        self.assertTrue(r["ok"])
        self.assertEqual(called, [], "no translation for an English name")

    def test_a_dead_translator_does_not_break_the_search(self):
        with mock.patch.object(s, "translate_to_english", return_value=""):
            with self._providers({"باذنجان": self._hit("باذنجان طازج")}):
                r = s.search("باذنجان", "")
        self.assertTrue(r["ok"], "the Arabic result must still come through")

    def test_one_translator_failure_disables_it_for_the_run(self):
        """Twenty more attempts against a dead endpoint only cost twenty more
        timeouts."""
        calls = []

        def boom(req, timeout=None):
            calls.append(1)
            raise urllib.error.URLError("no route")

        with mock.patch.object(s.urllib.request, "urlopen", side_effect=boom):
            self.assertEqual(s.translate_to_english("باذنجان"), "")
            self.assertEqual(s.translate_to_english("جزر"), "")
        self.assertEqual(len(calls), 1, "it must give up after the first failure")

    def test_a_translation_that_stays_arabic_is_discarded(self):
        payload = json.dumps([[["باذنجان", "باذنجان", None, None]]]).encode()

        class Fake:
            def read(self, n=None): return payload
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with mock.patch.object(s.urllib.request, "urlopen", return_value=Fake()):
            self.assertEqual(s.translate_to_english("باذنجان"), "")


class TheUsersOwnWording(unittest.TestCase):
    """The escape hatch for everything automation cannot know: that "خيار" is a
    cucumber and not a stock option, that "Bleach" is a cleaning product and
    not a cartoon. No score resolves a genuine ambiguity in the wider world.
    """

    def _spy(self, asked, title=None):
        def fake(q):
            asked.append(q)
            # Echoing the query back guarantees the head-word gate passes, so
            # these tests measure WHAT WAS SEARCHED rather than the scorer.
            return [{"title": (title or q).title(), "url": "https://x/c.jpg",
                     "source": "bing", "license": ""}]
        return mock.patch.dict(s.PROVIDERS, {"bing": fake,
                                             "openverse": lambda q: [],
                                             "commons": lambda q: []})

    def test_the_phrase_replaces_the_name_entirely(self):
        asked = []
        with self._spy(asked):
            r = s.search("خيار", "some description", phrase="fresh cucumber vegetable")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r.get("phrase"), "fresh cucumber vegetable")
        for q in asked:
            self.assertNotIn("خيار", q, f"the row's own name must not leak in: {q}")

    def test_a_phrase_skips_the_reference_code_guard(self):
        """If the user typed words for a coded row, they mean it."""
        asked = []
        with self._spy(asked):
            r = s.search("TDC-DN-7", "", phrase="stainless steel bucket")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue(asked)

    def test_a_phrase_is_not_translated(self):
        called = []
        with mock.patch.object(s, "translate_to_english",
                               side_effect=lambda t: called.append(t) or "x"):
            with self._spy([]):
                s.search("خيار", "", phrase="fresh cucumber vegetable")
        self.assertEqual(called, [], "the user's own words are already the query")

    def test_an_empty_phrase_falls_back_to_the_row(self):
        asked = []
        with self._spy(asked):
            s.search("Cucumber", "", phrase="   ")
        self.assertTrue(any("cucumber" in q for q in asked))


class TheBingParser(unittest.TestCase):
    """Bing hides each result in a JSON blob on the tile. Parsing is done here
    rather than in a browser, so it must survive a malformed tile."""

    TILE = ('<a class="iusc" style="" '
            'm="{&quot;t&quot;:&quot;Cotton Mop&quot;,'
            '&quot;murl&quot;:&quot;https://shop.example/mop.jpg&quot;,'
            '&quot;turl&quot;:&quot;https://th.bing.com/th/id/abc&quot;}" '
            'href="/images/search?x=1">')

    def _parse(self, html):
        with mock.patch.object(s, "_get_html", return_value=html):
            return s._bing("cotton mop")

    def test_a_tile_yields_title_and_both_links(self):
        got = self._parse(self.TILE)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["title"], "Cotton Mop")
        self.assertEqual(got[0]["url"], "https://shop.example/mop.jpg")
        self.assertEqual(got[0]["thumb"], "https://th.bing.com/th/id/abc")
        self.assertEqual(got[0]["source"], "bing")

    def test_one_broken_tile_does_not_lose_the_others(self):
        broken = '<a class="iusc" m="{not json at all" href="/x">'
        got = self._parse(broken + self.TILE)
        self.assertEqual(len(got), 1, "the good tile must still come through")

    def test_rank_order_is_preserved(self):
        """Bing's order IS the relevance order - re-ordering it throws away
        the only ranking signal this module gets."""
        second = self.TILE.replace("Cotton Mop", "Second Mop").replace(
            "shop.example/mop.jpg", "shop.example/2.jpg")
        got = self._parse(self.TILE + second)
        self.assertEqual([g["title"] for g in got], ["Cotton Mop", "Second Mop"])

    def test_a_page_with_no_tiles_is_empty_not_an_error(self):
        self.assertEqual(self._parse("<html>nothing here</html>"), [])


class TheThumbnailFallback(unittest.TestCase):
    """Merchants block hot-linking and their photographs run past MAX_BYTES.
    Bing's own thumbnail always answers and is the right size for a 22mm cell,
    so a failed original must fall back to it rather than lose the picture."""

    def _found(self):
        return {"ok": True, "query": "mop", "title": "Cotton Mop",
                "url": "https://shop.example/huge.jpg",
                "thumb": "https://th.bing.com/th/id/abc",
                "score": 0.9, "source": "bing", "license": ""}

    def test_the_thumbnail_is_used_when_the_original_fails(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            if "shop.example" in url:
                return {"ok": False, "error": "That image is too large."}
            return {"ok": True, "bytes": 40, "data_uri": "data:image/jpeg;base64,AA"}

        with mock.patch.object(s, "search", return_value=self._found()), \
             mock.patch.object(s, "fetch", side_effect=fake_fetch):
            r = s.find_image("Cotton mop", "")
        self.assertTrue(r["ok"], "the thumbnail should have rescued it")
        self.assertEqual(len(calls), 2, "the original must be tried FIRST")
        self.assertIn("th.bing.com", calls[1])

    def test_the_original_is_preferred_when_it_works(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return {"ok": True, "bytes": 90, "data_uri": "data:image/jpeg;base64,BB"}

        with mock.patch.object(s, "search", return_value=self._found()), \
             mock.patch.object(s, "fetch", side_effect=fake_fetch):
            r = s.find_image("Cotton mop", "")
        self.assertTrue(r["ok"])
        self.assertEqual(calls, ["https://shop.example/huge.jpg"],
                         "no second request when the first one worked")

    def test_both_failing_is_reported_not_faked(self):
        with mock.patch.object(s, "search", return_value=self._found()), \
             mock.patch.object(s, "fetch",
                               return_value={"ok": False, "error": "no"}):
            r = s.find_image("Cotton mop", "")
        self.assertFalse(r["ok"])
        self.assertNotIn("data_uri", r)


class ProviderOrder(unittest.TestCase):

    def test_bing_is_tried_first(self):
        """It is the only provider that indexes shops, and the only one that
        answers in Arabic. The CC libraries are the fallback, not the front."""
        self.assertEqual(s.PROVIDER_ORDER[0], "bing")
        self.assertIn("openverse", s.PROVIDER_ORDER)
        self.assertIn("commons", s.PROVIDER_ORDER)

    def test_a_dead_bing_falls_through_rather_than_failing(self):
        """The scrape is unofficial. The day it breaks, the button must get
        quieter, not stop."""
        def dead(q):
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)

        with mock.patch.dict(s.PROVIDERS, {
                "bing": dead,
                "openverse": lambda q: [{"title": "Hemp Rope", "url": "https://x/y.jpg",
                                         "source": "openverse", "license": ""}],
                "commons": lambda q: []}):
            r = s.search("Hemp rope", "")
        self.assertTrue(r["ok"], "openverse should have answered instead")
        self.assertEqual(r["source"], "openverse")


class DownloadGuards(unittest.TestCase):
    """A remote server controls the bytes, the type and the clock. None of
    those may be able to hurt the editor."""

    def test_a_non_web_address_is_refused(self):
        for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)",
                    "data:image/png;base64,AAAA", "", "ftp://x/y.png"):
            r = s.fetch(bad)
            self.assertFalse(r["ok"], bad)

    def test_a_non_image_is_refused(self):
        class Fake:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def read(self, n=None): return b"<html>not an image</html>"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(s.urllib.request, "urlopen", return_value=Fake()):
            r = s.fetch("https://example.com/x")
        self.assertFalse(r["ok"])
        self.assertIn("not an image", r["error"].lower())

    def test_an_oversized_image_is_refused(self):
        """Checked against the BYTES READ, not Content-Length - a lying header
        must not get a 50MB file past the cap."""
        class Fake:
            headers = {"Content-Type": "image/jpeg", "Content-Length": "10"}
            def read(self, n=None): return b"x" * (s.MAX_BYTES + 1)
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(s.urllib.request, "urlopen", return_value=Fake()):
            r = s.fetch("https://example.com/x.jpg")
        self.assertFalse(r["ok"])
        self.assertIn("too large", r["error"].lower())

    def test_an_empty_body_is_refused(self):
        class Fake:
            headers = {"Content-Type": "image/png"}
            def read(self, n=None): return b""
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(s.urllib.request, "urlopen", return_value=Fake()):
            self.assertFalse(s.fetch("https://example.com/x.png")["ok"])

    def test_a_good_image_becomes_a_data_uri(self):
        class Fake:
            headers = {"Content-Type": "image/png"}
            def read(self, n=None): return b"\x89PNG\r\n\x1a\n" + b"0" * 40
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with mock.patch.object(s.urllib.request, "urlopen", return_value=Fake()):
            r = s.fetch("https://example.com/x.png")
        self.assertTrue(r["ok"])
        self.assertTrue(r["data_uri"].startswith("data:image/png;base64,"))

    def test_being_offline_is_an_ANSWER_not_a_crash(self):
        with mock.patch.object(s.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no route")):
            r = s.fetch("https://example.com/x.png")
            self.assertFalse(r["ok"])
            self.assertIn("could not download", r["error"].lower())
            found = s.search("Hemp rope", "")
            self.assertFalse(found["ok"])
            self.assertTrue(found.get("offline"))


class WeakMatchesAreRefused(unittest.TestCase):
    """The rule the whole module exists for."""

    def _providers(self, hits):
        """EVERY provider is replaced, `bing` included.

        It has to be. `bing` is first in PROVIDER_ORDER and it is a live
        scrape, so leaving it out of the patch does not mock the search - it
        sends these unit tests to the internet, where they pass or fail on
        whatever Bing happens to be ranking today.
        """
        return mock.patch.dict(
            s.PROVIDERS, {"bing": lambda q: hits,
                          "openverse": lambda q: [], "commons": lambda q: []})

    def test_a_weak_top_hit_is_reported_not_used(self):
        with self._providers([{"title": "Flat Iron at Night",
                               "url": "https://x/y.jpg", "source": "t", "license": ""}]):
            r = s.search("Angle iron", "75mm*75mm*6m")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("weak"))
        self.assertEqual(r.get("best_guess"), "Flat Iron at Night")

    def test_a_strong_hit_is_used(self):
        with self._providers([{"title": "Cutting Angle Iron",
                               "url": "https://x/y.jpg", "source": "t", "license": ""}]):
            r = s.search("Angle iron", "75mm*75mm*6m")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["url"], "https://x/y.jpg")

    def test_the_best_candidate_wins_not_the_first(self):
        with self._providers([
            {"title": "Flat Iron at Night", "url": "https://x/bad.jpg", "source": "t", "license": ""},
            {"title": "Cutting Angle Iron", "url": "https://x/good.jpg", "source": "t", "license": ""},
        ]):
            r = s.search("Angle iron", "")
        self.assertTrue(r["ok"])
        self.assertEqual(r["url"], "https://x/good.jpg")

    def test_a_refusal_can_actually_cross_the_bridge(self):
        """It could not. `best_overall` was `scored[0]` itself and then got
        `candidates = scored[:5]` hung on it - a dict inside its own list. The
        bridge scrubs NaN by walking the result recursively, so every refusal
        that had candidates came back as "maximum recursion depth exceeded"
        instead of "nothing convincing". `json.dumps` fails the same way, which
        is what this asserts."""
        import json as _json
        with self._providers([{"title": "Flat Iron at Night",
                               "url": "https://x/y.jpg", "source": "t", "license": ""}]):
            r = s.search("Angle iron", "75mm*75mm*6m")
        self.assertFalse(r["ok"])
        _json.dumps(r)          # ValueError: Circular reference detected

    def test_a_successful_result_is_serialisable_too(self):
        import json as _json
        with self._providers([{"title": "Cutting Angle Iron",
                               "url": "https://x/y.jpg", "source": "t", "license": ""}]):
            r = s.search("Angle iron", "")
        self.assertTrue(r["ok"])
        _json.dumps(r)

    def test_find_image_does_not_download_a_refused_match(self):
        calls = []
        with self._providers([{"title": "Flat Iron at Night",
                               "url": "https://x/y.jpg", "source": "t", "license": ""}]):
            with mock.patch.object(s, "fetch", side_effect=lambda u: calls.append(u)):
                r = s.find_image("Angle iron", "")
        self.assertFalse(r["ok"])
        self.assertEqual(calls, [], "a refused match must not be downloaded")


@unittest.skipUnless(ONLINE, "no network")
class Live(unittest.TestCase):
    """Against the real services - skipped when offline so the suite still
    passes on a disconnected machine."""

    def test_the_item_that_started_this_is_found(self):
        """"Cotton Rectangular Cleaning Mop" is the item the user reported as
        unfindable. Against the CC libraries it was: they AND-match, so the
        full phrase returned ZERO. Bing answers the phrase in full."""
        r = s.search("Cotton Rectangular Cleaning Mop", "")
        self.assertTrue(r.get("ok"), r.get("error"))
        self.assertIn("mop", r["title"].lower())

    def test_a_real_item_finds_a_real_picture(self):
        r = s.find_image("Sledgehammer", "12P")
        self.assertTrue(r.get("ok"), r.get("error"))
        self.assertTrue(r["data_uri"].startswith("data:image/"))
        self.assertGreater(r["bytes"], 500)
        self.assertLessEqual(r["bytes"], s.MAX_BYTES)

    def test_an_arabic_item_finds_a_real_picture(self):
        """The whole Arabic half of the catalog used to return "nothing to
        search for" before the query pattern went Unicode."""
        r = s.find_image("سبلت جداري", "")
        self.assertTrue(r.get("ok"), r.get("error"))
        self.assertTrue(r["data_uri"].startswith("data:image/"))

    def test_the_head_noun_is_enforced_against_a_live_index(self):
        """Bing indexes everything, so it always returns *something*. The head
        word is the only thing standing between the user and a confident,
        wrong picture."""
        r = s.search("Hemp rope", "φ20mm")
        if r.get("ok"):
            self.assertIn("rope", r["title"].lower())
            self.assertNotIn("steel wire", r["title"].lower())

    def test_a_nonsense_item_returns_nothing_rather_than_anything(self):
        """Deliberately unpronounceable. An earlier version of this test used
        "Zzqx Nonexistent Widget Qqq" and Bing legitimately matched it to
        "QQQ Widget Testing" - the words really were there. A test of refusal
        needs a query with no real words in it at all."""
        r = s.find_image("Zzqxvbn Qwrtplk Mnbvcxz", "")
        self.assertFalse(r.get("ok"))
        self.assertNotIn("data_uri", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
