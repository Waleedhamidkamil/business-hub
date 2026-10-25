# -*- coding: utf-8 -*-
"""English suggestions for Arabic items (2026-09-06, backend/translate.py).

No network. Every test here drives the pure parts - configuration, the prompt,
the reply parser - and the one test that exercises `suggest()` end to end
replaces the HTTP call with a local function. Reaching Google from a test suite
would be slow, flaky, and would spend the user's quota.

Three things this file exists to protect:

1. **The key must not be committed.** `data/settings.json` is TRACKED, so the
   key lives in `data/secrets.json` (git-ignored) or the environment. A test
   asserts the ignore rule, because the failure mode is a key in a public diff.

2. **A suggestion must never be an assertion.** The model returning Arabic in an
   `_en` field means it echoed the input; that is not an answer and must not
   arrive looking like one.

3. **Nothing here may raise.** `suggest()` is called from a button, and a
   network failure must come back as a message, not an exception.
"""
import io
import json
import re
import socket
import sys
import unittest
import urllib.error
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import translate  # noqa: E402


class Configuration(unittest.TestCase):
    def test_secrets_file_is_git_ignored(self):
        ignore = io.open(APP_ROOT / ".gitignore", encoding="utf-8").read()
        self.assertIn("data/secrets.json", ignore,
                      "the API key file must never be committable")

    def test_key_never_read_from_the_tracked_settings_file(self):
        source = io.open(APP_ROOT / "backend/translate.py", encoding="utf-8").read()
        self.assertNotIn("SETTINGS_PATH", source)
        self.assertNotIn("settings.json", source.replace(
            "`data/settings.json` is tracked in git, so the key does NOT", ""))

    def test_secrets_live_beside_the_shared_library_not_in_a_profile(self):
        """A key belongs to the machine. Under `config.DATA_DIR` it would be
        per-company and appear to switch off when the profile changes."""
        from backend import profile_manager
        self.assertEqual(translate._secrets_path().parent,
                         profile_manager.shared_db_path().parent.parent)

    def test_a_key_file_written_by_powershell_is_readable(self):
        """Windows PowerShell's `>` and `Out-File` write UTF-16 with a BOM.

        This is not hypothetical: the first key on this machine was created that
        way and the app reported "no API key" while the file sat there looking
        correct. Reading BYTES lets json detect the encoding (RFC 4627 §3).
        """
        import tempfile
        for encoding in ("utf-16", "utf-8-sig", "utf-8"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "secrets.json"
                path.write_text('{"translation": {"api_key": "KEY-%s"}}' % encoding,
                                encoding=encoding)
                original = translate._secrets_path
                translate._secrets_path = lambda p=path: p
                try:
                    self.assertEqual(translate._settings()["api_key"], "KEY-" + encoding,
                                     f"a {encoding} key file must be readable")
                finally:
                    translate._secrets_path = original

    def test_a_corrupt_key_file_is_not_configured_not_a_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            path.write_bytes(bytes([0, 1]) + b" not json at all")
            original = translate._secrets_path
            translate._secrets_path = lambda p=path: p
            try:
                self.assertTrue(translate.status()["ok"])      # must not raise
            finally:
                translate._secrets_path = original

    def test_status_without_a_key_is_not_an_error(self):
        s = translate.status()
        self.assertTrue(s["ok"])
        self.assertIn("configured", s)
        if not s["configured"]:
            self.assertTrue(s["reason"], "an unconfigured app must say how to configure it")


class Prompt(unittest.TestCase):
    ITEMS = [{"name": "طاولة دائرية", "description": "قطر 120 سم", "unit": "عدد"}]

    def test_carries_the_companys_own_unit_vocabulary(self):
        prompt = translate._build_prompt(self.ITEMS, {"عدد": "EA", "متر": "M"}, [])
        self.assertIn("عدد = EA", prompt)
        self.assertIn("متر = M", prompt)
        self.assertIn("Do not invent unit spellings", prompt)

    def test_carries_the_companys_own_name_pairs(self):
        prompt = translate._build_prompt(self.ITEMS, {}, [("كرسي طاولة", "Table Chair")])
        self.assertIn("كرسي طاولة = Table Chair", prompt)

    def test_requires_one_best_trade_name_for_every_item(self):
        prompt = translate._build_prompt(self.ITEMS, {}, [])
        self.assertIn("best concise English trade name for every item", prompt)
        self.assertIn("must not be blank", prompt)

    def test_sends_only_words_that_would_be_printed_anyway(self):
        """No client, price, cost, company, document number or profile."""
        item = dict(self.ITEMS[0], company="HDEC", rate="250", cost="180",
                    po_no="A-84", qty="6")
        prompt = translate._build_prompt([item], {}, [])
        for secret in ("HDEC", "250", "180", "A-84"):
            self.assertNotIn(secret, prompt, f"{secret!r} must not leave the machine")

    def test_glossary_puts_relevant_pairs_first(self):
        pairs = translate._glossary([{"name": "طاولة دائرية"}], limit=5)
        if pairs:                      # live catalogue; skip the shape if empty
            self.assertTrue(any("طاولة" in ar for ar, _en in pairs[:3]),
                            "a pair sharing a word with the query must lead")

    def test_candidate_context_contains_only_printable_words(self):
        item = dict(self.ITEMS[0], candidates=[{
            "item_id": 1933874,
            "name": "Wall Split Unit",
            "description": "2 Ton",
            "unit": "EA",
            "rate": 750,
            "cost": 600,
            "client": "HDEC",
        }])
        prompt = translate._build_prompt([item], {}, [])
        self.assertIn("candidate 0: Wall Split Unit | 2 Ton | EA", prompt)
        for secret in ("1933874", "750", "600", "HDEC"):
            self.assertNotIn(secret, prompt)
        self.assertIn('"candidate_index": 0', prompt)


class ParseReply(unittest.TestCase):
    def test_plain_json(self):
        out = translate._parse('{"items":[{"index":0,"name_en":"Round Table"}]}', 1)
        self.assertEqual(out, [{"index": 0, "name_en": "Round Table",
                                "description_en": "", "unit_en": "",
                                "candidate_index": None}])

    def test_fenced_json_still_reads(self):
        out = translate._parse('```json\n{"items":[{"index":0,"name_en":"Cable"}]}\n```', 1)
        self.assertEqual(out[0]["name_en"], "Cable")

    def test_prose_around_the_json_still_reads(self):
        out = translate._parse('Sure!\n{"items":[{"index":0,"name_en":"Cable"}]}\nHope that helps.', 1)
        self.assertEqual(out[0]["name_en"], "Cable")

    def test_arabic_in_an_english_field_is_dropped(self):
        """The model echoing the input back is not a translation. Returning it
        would fill the box with the very thing the user needs replaced."""
        out = translate._parse('{"items":[{"index":0,"name_en":"طاولة دائرية"}]}', 1)
        self.assertEqual(out[0]["name_en"], "")

    def test_out_of_range_and_malformed_indexes_are_dropped(self):
        out = translate._parse(
            '{"items":[{"index":9,"name_en":"X"},{"index":"a","name_en":"Y"},'
            '{"index":0,"name_en":"Z"}]}', 1)
        self.assertEqual([r["name_en"] for r in out], ["Z"])

    def test_indexes_are_not_coerced_into_another_requested_row(self):
        items = [{"name": "first"}, {"name": "second"}]
        for bad in (True, 0.9, "1"):
            raw = json.dumps({"items": [{"index": bad, "name_en": "Wrong Row"}]})
            out = translate._parse(raw, items)
            self.assertTrue(all(row["name_en"] == "" for row in out),
                            f"index {bad!r} must not be coerced into a valid row")

    def test_junk_is_empty_not_an_exception(self):
        self.assertEqual(translate._parse("not json at all", 2), [])
        self.assertEqual(translate._parse("", 2), [])

    def test_candidate_index_is_integer_and_in_bounds(self):
        items = [{"candidates": [{"name": "1.5 Ton"}, {"name": "2 Ton"}]}]
        out = translate._parse(
            '{"items":[{"index":0,"name_en":"Split AC","candidate_index":1}]}',
            items)
        self.assertEqual(out[0]["candidate_index"], 1)

        for bad in ('2', -1, 2, True):
            raw = json.dumps({"items": [{"index": 0, "candidate_index": bad}]})
            self.assertIsNone(translate._parse(raw, items)[0]["candidate_index"])

    def test_missing_rows_are_restored_for_bounded_repair(self):
        out = translate._parse('{"items":[]}', [{"name": "x"}, {"name": "y"}])
        self.assertEqual([row["index"] for row in out], [0, 1])
        self.assertTrue(all(row["name_en"] == "" for row in out))


class Suggest(unittest.TestCase):
    """`suggest()` end to end with the HTTP call replaced."""

    def setUp(self):
        self._post = translate._post
        self._settings = translate._settings
        translate._settings = lambda: {"provider": "gemini", "model": "test-model",
                                       "api_key": "TEST-KEY"}
        self.addCleanup(lambda: setattr(translate, "_post", self._post))
        self.addCleanup(lambda: setattr(translate, "_settings", self._settings))

    def _reply(self, text):
        return lambda url, payload, *_a, **_k: (
            {"candidates": [{"content": {"parts": [{"text": text}]}}]}, "", False)

    def test_happy_path(self):
        translate._post = self._reply('{"items":[{"index":0,"name_en":"Round Table",'
                                      '"unit_en":"EA"}]}')
        r = translate.suggest([{"name": "طاولة دائرية", "unit": "عدد"}])
        self.assertTrue(r["ok"])
        self.assertEqual(r["suggestions"][0]["name_en"], "Round Table")
        self.assertEqual(r["suggestions"][0]["unit_en"], "EA")

    def test_the_key_travels_in_a_header_never_in_the_url(self):
        """A URL is the part of a request that gets logged, put into an
        exception's text and shown in a toast. The key must not be in it."""
        seen = {}

        def capture(url, payload, headers=None):
            seen["url"] = url
            seen["payload"] = payload
            seen["headers"] = headers or {}
            return self._reply('{"items":[{"index":0,"name_en":"X"}]}')(url, payload)

        translate._post = capture
        translate.suggest([{"name": "كيبل نحاس"}])
        self.assertIn("test-model", seen["url"])
        self.assertNotIn("TEST-KEY", seen["url"])
        self.assertNotIn("key=", seen["url"])
        self.assertEqual(seen["headers"].get(translate._KEY_HEADER), "TEST-KEY")
        body = json.dumps(seen["payload"], ensure_ascii=False)
        self.assertIn("كيبل نحاس", body)
        self.assertEqual(seen["payload"]["generationConfig"]["temperature"], 0)

    def test_a_network_failure_is_a_message_not_an_exception(self):
        translate._post = lambda url, payload, *_a, **_k: (None, "Could not reach the service", False)
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])
        self.assertIn("Could not reach", r["error"])
        self.assertEqual(r["suggestions"], [])

    def test_an_exploding_transport_is_still_a_message(self):
        def boom(url, payload, *_a, **_k):
            raise RuntimeError("socket exploded")
        translate._post = boom
        r = translate.suggest([{"name": "x"}])          # must not raise
        self.assertFalse(r["ok"])
        self.assertIn("socket exploded", r["error"])

    def test_an_empty_answer_is_reported_not_silently_ok(self):
        translate._post = self._reply("{}")
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])

    def test_a_long_list_is_SPLIT_not_refused(self):
        """A-88 alone had 46 rows. Refusing a longer list left the user in a
        forty-row popup with the button they just pressed doing nothing."""
        batches = []

        def post(url, payload, headers=None):
            text = payload["contents"][0]["parts"][0]["text"]
            count = len([line for line in text.splitlines()
                         if line.strip().startswith(tuple("0123456789"))
                         and ". name:" in line])
            batches.append(count)
            rows = ",".join('{"index":%d,"name_en":"Item %d"}' % (i, i) for i in range(count))
            return ({"candidates": [{"content": {"parts": [
                {"text": '{"items":[%s]}' % rows}]}}]}, "", False)

        translate._post = post
        wanted = translate._MAX_ITEMS + 5
        r = translate.suggest([{"name": f"مادة {i}"} for i in range(wanted)])
        self.assertTrue(r["ok"], r)
        self.assertEqual(batches, [translate._MAX_ITEMS, 5], "split into whole requests")
        self.assertEqual(len(r["suggestions"]), wanted)
        self.assertEqual([row["index"] for row in r["suggestions"]], list(range(wanted)),
                         "indexes must be the CALLER's, not each batch's")

    def test_work_already_done_survives_a_later_batch_failing(self):
        calls = []

        def post(url, payload, headers=None):
            calls.append(1)
            if len(calls) > 1:
                return None, "Could not reach the translation service", False
            return ({"candidates": [{"content": {"parts": [
                {"text": '{"items":[{"index":0,"name_en":"First"}]}'}]}}]}, "", False)

        translate._post = post
        r = translate.suggest([{"name": "x"}] * (translate._MAX_ITEMS + 1))
        self.assertTrue(r["ok"], "throwing the finished work away makes the user pay twice")
        self.assertTrue(r["suggestions"])
        self.assertIn("stopped", r["warning"])

    def test_the_first_batch_failing_is_still_a_plain_failure(self):
        translate._post = lambda url, payload, headers=None: (None, "no network", False)
        r = translate.suggest([{"name": "x"}] * (translate._MAX_ITEMS + 1))
        self.assertFalse(r["ok"])
        self.assertNotIn("warning", r)

    def test_an_absurd_list_is_still_refused_before_any_call(self):
        called = []
        translate._post = lambda url, payload, *_a, **_k: called.append(1) or (None, "x", False)
        r = translate.suggest([{"name": "x"}] * (translate._MAX_TOTAL + 1))
        self.assertFalse(r["ok"])
        self.assertEqual(called, [], "the ceiling must be checked before the network")

    def test_the_process_wide_socket_timeout_is_never_touched(self):
        """It used to be set and never restored, so after one AI press every
        socket in the app - image search included - inherited it."""
        source = (Path(__file__).resolve().parents[1] / "backend" / "translate.py")
        self.assertNotIn("setdefaulttimeout", source.read_text(encoding="utf-8"))

    def test_no_key_means_no_call(self):
        translate._settings = lambda: {"provider": "gemini", "model": "m", "api_key": ""}
        called = []
        translate._post = lambda url, payload, *_a, **_k: called.append(1) or (None, "x", False)
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])
        self.assertEqual(called, [])


class BusyFallback(unittest.TestCase):
    """A busy service is worth another model. A wrong request is not.

    Measured 2026-09-06: the newest flash models answer 503 "high demand" on a
    free key more often than not, so one overloaded model must not be the end of
    it. But a 400/404 means WE asked wrongly, and re-asking a second model the
    same wrong question only spends the user's quota.
    """

    def setUp(self):
        self._post, self._settings = translate._post, translate._settings
        translate._settings = lambda: {"provider": "gemini", "model": "primary",
                                       "api_key": "K"}
        self.addCleanup(lambda: setattr(translate, "_post", self._post))
        self.addCleanup(lambda: setattr(translate, "_settings", self._settings))

    def test_busy_primary_falls_through_to_a_working_model(self):
        seen = []

        def post(url, payload, *_a, **_k):
            seen.append(url)
            if len(seen) == 1:
                return None, "busy", True
            return ({"candidates": [{"content": {"parts": [
                {"text": '{"items":[{"index":0,"name_en":"Chair"}]}'}]}}]}, "", False)

        translate._post = post
        r = translate.suggest([{"name": "كرسي"}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["suggestions"][0]["name_en"], "Chair")
        self.assertEqual(len(seen), 2, "one busy reply must not be the end of it")
        self.assertEqual(r["tried"], ["primary"],
                         "the second attempt is the SAME model - when it is busy "
                         "or slow the model is not the problem, the moment is")

    def test_a_wrong_request_stops_at_the_first_model(self):
        seen = []

        def post(url, payload, *_a, **_k):
            seen.append(url)
            return None, "Translation service refused the request (404). No such model.", False

        translate._post = post
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])
        self.assertIn("404", r["error"])
        self.assertEqual(len(seen), 1, "a 404 must not be re-asked of every fallback")

    def test_all_busy_is_reported_and_bounded(self):
        seen = []
        translate._post = lambda url, payload, *_a, **_k: seen.append(url) or (None, "busy", True)
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])
        self.assertIn("press the button again", r["error"].lower(),
                      "the message must say what to do, not just what failed")
        self.assertEqual(len(seen), 2 + len(translate._FALLBACK_MODELS),
                         "the chosen model twice, then each fallback once")
        self.assertLessEqual(len(seen), 6, "the button must not hang on a long chain")

    def test_a_TIMEOUT_is_retried_rather_than_reported_as_unreachable(self):
        """Measured 2026-09-10: the identical request took 0.9s, 3.6s, 14.3s and
        over 30s within one minute. A read timeout used to return busy=False,
        which skipped the whole fallback chain and told the user the service
        could not be reached - for a service that was merely slow."""
        seen = []

        def post(url, payload, *_a, **_k):
            seen.append(url)
            if len(seen) == 1:
                return None, "The translation service did not answer in time.", True
            return ({"candidates": [{"content": {"parts": [
                {"text": '{"items":[{"index":0,"name_en":"Chair"}]}'}]}}]}, "", False)

        translate._post = post
        r = translate.suggest([{"name": "كرسي"}])
        self.assertTrue(r["ok"], "a slow answer is not a failed one")
        self.assertEqual(r["suggestions"][0]["name_en"], "Chair")

    def test_a_real_connection_failure_is_NOT_retried(self):
        """No network fails the same way four times and spends two minutes
        doing it. Only a timeout is worth another go."""
        seen = []
        translate._post = lambda url, payload, *_a, **_k: seen.append(url) or (
            None, "Could not reach the translation service: no route to host", False)
        r = translate.suggest([{"name": "x"}])
        self.assertFalse(r["ok"])
        self.assertEqual(len(seen), 1, "offline must fail fast and say so")
        self.assertIn("could not reach", r["error"].lower())

    def test_the_retry_budget_is_wall_clock_not_attempt_count(self):
        """A person is holding a button down. Four 30s timeouts in a row is not
        a retry policy, it is a hang."""
        import time as _t
        self.assertLessEqual(translate._TOTAL_BUDGET, 90,
                             "nobody waits longer than this for a name")
        self.assertGreaterEqual(translate._TOTAL_BUDGET, translate._READ_TIMEOUT * 2,
                                "the budget must allow at least one real retry")

        seen = []
        real = _t.monotonic
        clock = {"now": 0.0}

        def post(url, payload, *_a, **_k):
            seen.append(url)
            clock["now"] += translate._READ_TIMEOUT       # every attempt times out
            return None, "The translation service did not answer in time.", True

        translate._post = post
        _t.monotonic = lambda: clock["now"]
        try:
            r = translate.suggest([{"name": "x"}])
        finally:
            _t.monotonic = real
        self.assertFalse(r["ok"])
        self.assertLessEqual(len(seen) * translate._READ_TIMEOUT,
                             translate._TOTAL_BUDGET + translate._READ_TIMEOUT,
                             "the chain must stop once the budget is spent")

    def test_the_REAL_post_calls_a_timeout_retryable_and_offline_not(self):
        """Drives `_post` itself, not a stub of it.

        A test that hand-feeds `busy=True` proves nothing about the code that
        DECIDES it - the classification is the whole fix, and mutating it out
        left every other test in this file green.
        """
        import urllib.request as _u
        real = _u.urlopen
        self.addCleanup(lambda: setattr(_u, "urlopen", real))

        def raising(exc):
            def boom(*_a, **_k):
                raise exc
            return boom

        for label, exc in (
            ("bare socket timeout", socket.timeout("timed out")),
            ("timeout inside URLError", urllib.error.URLError(TimeoutError("timed out"))),
        ):
            _u.urlopen = raising(exc)
            data, error, busy = translate._post("https://x/y", {"a": 1})
            self.assertIsNone(data, label)
            self.assertTrue(busy, f"{label}: a slow service is worth another go")
            self.assertIn("did not answer in time", error.lower(), label)

        _u.urlopen = raising(urllib.error.URLError("no route to host"))
        data, error, busy = translate._post("https://x/y", {"a": 1})
        self.assertFalse(busy, "offline must NOT be retried four times")
        self.assertIn("could not reach", error.lower())

    def test_the_default_model_is_one_that_was_MEASURED(self):
        """A default nobody has called is a guess. `gemini-2.0-flash` was the
        first one and is not on this account at all.

        Chosen 2026-09-10 from eight models over three round-robin rounds
        against this catalogue: `gemini-3.5-flash` was both the steadiest
        (19.5-21.2s across every run) and the only tier that read the Iraqi
        trade terms correctly - a lite model answered `كلبس كابسة` (staples)
        with "Wire Rope Clip", which is rigging hardware, on its way to a
        client's invoice."""
        self.assertEqual(translate._DEFAULT_MODEL, "gemini-3.5-flash")
        self.assertNotIn(translate._DEFAULT_MODEL, translate._FALLBACK_MODELS,
                         "the chain already retries the chosen model itself")
        self.assertNotIn("gemini-2.5-flash-lite", translate._FALLBACK_MODELS,
                         "404 on this account - a fallback that cannot answer "
                         "only spends the budget")


class NotOnASavePath(unittest.TestCase):
    """The app is offline-first. A translation call must never sit where a slow
    connection could delay saving a document."""

    def test_no_document_save_reaches_the_translator(self):
        # The IMPORT, not the word: "translate" also appears as a CSS transform
        # in the generators, which has nothing to do with this module.
        for name in ("database.py", "pdf_generator.py", "quote_generator.py",
                     "dn_generator.py", "inv_generator.py", "master_items.py"):
            source = io.open(APP_ROOT / "backend" / name, encoding="utf-8").read()
            for marker in ("import translate", "translate.suggest", "translate.status"):
                self.assertNotIn(marker, source,
                                 f"{name} must not reach the translation service")

    def test_the_frontend_asks_only_from_a_button(self):
        source = io.open(APP_ROOT / "static/po_handoff.js", encoding="utf-8").read()
        self.assertIn("tr_suggest", source)
        component = io.open(APP_ROOT / "static/item_review_popup.js", encoding="utf-8").read()
        self.assertIn("suggestButton.onclick", component,
                      "suggestions must come from a press, never automatically")
        self.assertNotIn("tr_suggest", io.open(APP_ROOT / "static/app.js",
                                               encoding="utf-8").read())



class HouseWordingWins(unittest.TestCase):
    """The catalogue's own wording must reach the prompt (2026-09-10).

    Waleed's ruling: `قلم تصحيح` stays "correction tape" and `فخذ جاموس مجمد`
    stays "Frozen Buffalo Topside", even though every model asked cold says
    "Correction Pen" and "Frozen Buffalo Leg". The glossary is what makes that
    hold - with those pairs in the prompt, all four house terms came back
    verbatim on two live runs. If the glossary ever stops reaching the prompt,
    the house wording degrades silently and nobody finds out until it is on an
    invoice.
    """

    def test_the_pairs_we_already_use_are_IN_the_prompt(self):
        glossary = [("قلم تصحيح", "correction tape"),
                    ("فخذ جاموس مجمد", "Frozen Buffalo Topside")]
        prompt = translate._build_prompt([{"name": "قلم تصحيح"}], {}, glossary, "en")
        for arabic, english in glossary:
            self.assertIn(arabic, prompt)
            self.assertIn(english, prompt)
        self.assertIn("already uses", prompt,
                      "and they must be labelled as OUR words, not as examples "
                      "to improve on")

    def test_it_holds_in_the_arabic_direction_too(self):
        prompt = translate._build_prompt([{"name": "Correction Tape"}], {},
                                         [("Correction Tape", "قلم تصحيح")], "ar")
        self.assertIn("قلم تصحيح", prompt)


class BothDirections(unittest.TestCase):
    """A purchase order needs the ENGLISH half of its Arabic items; a quotation
    needs the ARABIC half of its English ones (2026-09-10). One prompt, one
    parser, one JSON contract - only the language named in it changes."""

    def setUp(self):
        self._post, self._settings = translate._post, translate._settings
        translate._settings = lambda: {"provider": "gemini", "model": "m",
                                       "api_key": "K"}
        self.addCleanup(lambda: setattr(translate, "_post", self._post))
        self.addCleanup(lambda: setattr(translate, "_settings", self._settings))

    def _capture(self, reply):
        seen = {}

        def post(url, payload, headers=None):
            seen["prompt"] = payload["contents"][0]["parts"][0]["text"]
            return ({"candidates": [{"content": {"parts": [{"text": reply}]}}]},
                    "", False)

        translate._post = post
        return seen

    def test_a_quotation_asks_for_ARABIC(self):
        seen = self._capture('{"items":[{"index":0,"name_ar":"خزانة مكتب"}]}')
        r = translate.suggest([{"name": "Office Locker"}], "ar")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["suggestions"][0]["name_ar"], "خزانة مكتب")
        self.assertIn("Arabic trade name", seen["prompt"])
        self.assertIn('"name_ar"', seen["prompt"])
        self.assertNotIn('"name_en"', seen["prompt"],
                         "asking for both halves at once is how a model picks one")

    def test_a_purchase_order_still_asks_for_ENGLISH(self):
        seen = self._capture('{"items":[{"index":0,"name_en":"Office Locker"}]}')
        r = translate.suggest([{"name": "خزانة مكتب"}], "en")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["suggestions"][0]["name_en"], "Office Locker")
        self.assertIn("English trade name", seen["prompt"])

    def test_the_default_direction_is_unchanged(self):
        self._capture('{"items":[{"index":0,"name_en":"Chair"}]}')
        r = translate.suggest([{"name": "كرسي"}])
        self.assertEqual(r["suggestions"][0]["name_en"], "Chair")

    def test_an_answer_that_echoes_the_question_back_is_not_an_answer(self):
        """The existing guard, now in both directions: Arabic in an English
        slot was always dropped, and Latin script in an Arabic slot must be
        too - the model repeating `Office Locker` has told us nothing."""
        self._capture('{"items":[{"index":0,"name_ar":"Office Locker"}]}')
        r = translate.suggest([{"name": "Office Locker"}], "ar")
        self.assertEqual(r["suggestions"][0]["name_ar"], "",
                         "an English word is not an Arabic name")

        self._capture('{"items":[{"index":0,"name_en":"كرسي"}]}')
        r = translate.suggest([{"name": "كرسي"}], "en")
        self.assertEqual(r["suggestions"][0]["name_en"], "")

    def test_the_glossary_is_scored_against_the_side_being_ASKED_about(self):
        """Scoring an English query against Arabic names finds nothing, so the
        60-pair budget would fill with noise."""
        pairs = translate._glossary([{"name": "Round Table"}], limit=5, want="ar")
        for asked, answer in pairs:
            self.assertTrue(asked, "the asked side leads the pair")
        if pairs:
            self.assertNotEqual(pairs, translate._glossary(
                [{"name": "Round Table"}], limit=5, want="en"),
                "the two directions cannot present the same pair the same way")

if __name__ == "__main__":
    unittest.main()
