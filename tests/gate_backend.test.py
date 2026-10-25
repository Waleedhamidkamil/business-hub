# -*- coding: utf-8 -*-
"""The launch card's backend (2026-10-20; the doors before it, 2026-10-02).

Waleed: a launch screen that lasts exactly as long as loading - the logo on a
white panel, a greeting, the date, this month's tally, ONE agenda item in his
order (collect, deliver & invoice, follow up, submit), and a loading bar with
the step written under it - only the card on screen, the desktop behind it. Above all: "load
everything fast and prepare it while that card is loading". This file checks
the pieces that make that true:

  * BriefLines            - the switch gate's three lines; money keeps its currency
  * BriefMatchesDashboard - that brief and Needs Attention agree on live data
  * CardData              - tally, agenda priority, last edited, read-only
  * AgendaFeed            - the card's agenda IS the dashboard's, without writing
  * BootCache             - start-up answers computed in the background, handed
                            over once, never stale, never a failure
  * CardDrawing           - the panels, the bar (no see-through hole), the rows
  * WindowHold            - only the card on screen: the app window loads
                            invisibly and fades in as the card leaves
  * GateProcess           - the app talks to the card's own process by pipe,
                            and does no work for it
  * GateStartRules        - switch-off, no registry, greeting
  * OneBrandColour        - the company colour is written in one place
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import boot_cache, gate_brief, launch_gate  # noqa: E402


def _scratch_db(path, invoices, docs_per_table=0, today=date(2026, 10, 2)):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE invoices (inv_no TEXT, year INT, month INT, sell_currency TEXT, sell_total REAL)")
    for t in ("quotes", "dns", "pos"):
        c.execute(f"CREATE TABLE {t} (no TEXT, year INT, month INT)")
        for i in range(docs_per_table):
            c.execute(f"INSERT INTO {t} VALUES (?,?,?)", (f"{t}-{i}", today.year, today.month))
    c.executemany("INSERT INTO invoices VALUES (?,?,?,?,?)", invoices)
    c.commit()
    c.close()


class BriefLines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")

    def brief(self, invoices, feed, docs_per_table=0):
        if os.path.exists(self.db):
            os.remove(self.db)               # a fresh database per call
        _scratch_db(self.db, invoices, docs_per_table)
        with mock.patch.object(gate_brief.config, "DB_PATH", Path(self.db)):
            return gate_brief.brief(date(2026, 10, 2), feed=feed)

    def test_unpaid_is_summed_per_currency_never_mixed(self):
        out = self.brief(
            [("INV-1", 2026, 9, "USD", 1200.0), ("INV-2", 2026, 9, "IQD", 2500000.0),
             ("INV-3", 2026, 9, "USD", 300.4), ("INV-PAID", 2026, 9, "USD", 99999.0)],
            {"unpaid": [{"doc_no": "INV-1", "age_days": 12}, {"doc_no": "INV-2", "age_days": 67},
                        {"doc_no": "INV-3", "age_days": 3}], "unsubmitted": []})
        unpaid = out["lines"][0]
        self.assertEqual(unpaid["key"], "unpaid")
        self.assertEqual(unpaid["value"], "USD 1,500 + IQD 2,500,000",
                         "dinars must never be added to dollars, and every figure carries its currency")
        self.assertEqual(unpaid["detail"], "3 invoices, oldest 67 days")

    def test_unsubmitted_names_the_kinds(self):
        mixed = self.brief([], {"unpaid": [], "unsubmitted": [
            {"kind": "inv", "age_days": 138}, {"kind": "dn", "age_days": 4}, {"kind": "inv", "age_days": 9}]})
        line = mixed["lines"][0]
        self.assertEqual(line["value"], "3 documents")
        self.assertEqual(line["detail"], "2 invoices, 1 delivery note, oldest 138 days")
        one = self.brief([], {"unpaid": [], "unsubmitted": [{"kind": "dn", "age_days": 1}]})
        self.assertEqual(one["lines"][0]["value"], "1 delivery note")
        self.assertEqual(one["lines"][0]["detail"], "oldest 1 day")

    def test_this_month_counts_all_four_types_and_this_month_only(self):
        out = self.brief([("INV-A", 2026, 10, "USD", 6900.0), ("INV-B", 2026, 9, "USD", 5.0)],
                         {"unpaid": [], "unsubmitted": []}, docs_per_table=1)
        month = out["lines"][0]
        self.assertEqual(month["key"], "month")
        self.assertEqual(month["value"], "4 documents")
        self.assertEqual(month["detail"], "USD 6,900 invoiced")

    def test_zero_lines_are_left_out_and_nothing_at_all_is_all_clear(self):
        out = self.brief([], {"unpaid": [], "unsubmitted": []})
        self.assertEqual(out["lines"], [])
        self.assertTrue(out["all_clear"])

    def test_order_is_money_owed_then_not_sent_then_month(self):
        out = self.brief([("INV-1", 2026, 10, "USD", 10.0)],
                         {"unpaid": [{"doc_no": "INV-1", "age_days": 1}],
                          "unsubmitted": [{"kind": "inv", "age_days": 1}]})
        self.assertEqual([l["key"] for l in out["lines"]], ["unpaid", "unsubmitted", "month"])

    def test_a_feed_in_hand_is_not_fetched_again(self):
        # Start-up computes Needs Attention once (boot_cache) and the brief reuses it.
        with mock.patch.object(gate_brief.database, "dashboard_needs_attention",
                               side_effect=AssertionError("fetched twice")):
            self.brief([], {"unpaid": [], "unsubmitted": []})


class BriefMatchesDashboard(unittest.TestCase):
    """On the live data, the brief's counts are Needs Attention's counts."""

    def test_counts_agree_with_needs_attention(self):
        from backend import database
        feed = database.dashboard_needs_attention()
        out = gate_brief.brief()
        by_key = {l["key"]: l for l in out["lines"]}
        n_unpaid = len(feed.get("unpaid") or [])
        n_unsub = len(feed.get("unsubmitted") or [])
        if n_unpaid:
            self.assertIn(f"{n_unpaid:,} invoice", by_key["unpaid"]["detail"])
        else:
            self.assertNotIn("unpaid", by_key)
        if n_unsub:
            self.assertTrue(by_key["unsubmitted"]["value"].startswith(f"{n_unsub:,} "))
        else:
            self.assertNotIn("unsubmitted", by_key)


def _card_db(path, today=date(2026, 10, 20)):
    """A scratch database with just what gate_brief.card() reads."""
    c = sqlite3.connect(path)
    for table, no in (("quotes", "quote_no"), ("pos", "po_no"), ("dns", "dn_no")):
        c.execute(f"CREATE TABLE {table} ({no} TEXT, year INT, month INT, updated_at TEXT)")
    c.execute("CREATE TABLE invoices (inv_no TEXT, year INT, month INT, updated_at TEXT, "
              "sell_currency TEXT, sell_total REAL)")
    y, m = today.year, today.month
    c.executemany("INSERT INTO quotes VALUES (?,?,?,?)", [
        ("QT-1", y, m, "2026-10-20T09:00:00"), ("QT-2", y, m, "2026-10-19 08:00:00"),
        ("QT-OLD", y, m - 1, "2026-09-02 08:00:00")])
    c.executemany("INSERT INTO pos VALUES (?,?,?,?)", [("A-1", y, m, "2026-10-20 10:00:00")])
    c.executemany("INSERT INTO dns VALUES (?,?,?,?)", [("DN-BLANK", y, m - 1, "")])
    c.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?)", [
        ("INV-1", y, m, "2026-10-18 12:00:00", "USD", 1200.0),
        ("INV-2", y, m - 1, "2026-10-01 12:00:00", "IQD", 2500000.0),
        ("INV-3", y, m - 1, "2026-09-30 12:00:00", "USD", 300.4)])
    c.commit()
    c.close()


class CardData(unittest.TestCase):
    """gate_brief.card(): what the launch card says under the greeting."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "card.db")
        _card_db(self.db)
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)

    def test_tally_counts_this_month_only_and_shows_zeros(self):
        out = gate_brief.tally(self.conn, date(2026, 10, 20))
        self.assertEqual(out["text"], "2 quotations · 1 PO · 0 DNs · 1 invoice")

    def test_tally_reads_the_dashboards_four_tables(self):
        from backend import analytics
        self.assertEqual({t for t, _, _ in gate_brief._TALLY}, set(analytics._DOC_TABLES.values()),
                         "the card counts different tables from the dashboard's This Month")

    def agenda(self, **buckets):
        feed = {"unpaid": [], "deliver": [], "decide": [], "unsubmitted": []}
        feed.update(buckets)
        return gate_brief.agenda(self.conn, feed)

    def test_agenda_priority_is_collect_deliver_follow_up_submit(self):
        unpaid = [{"doc_no": "INV-1", "kind": "inv", "age_days": 2}]
        deliver = [{"doc_no": "A-1", "kind": "po", "age_days": 30}]
        decide = [{"doc_no": "QT-1", "kind": "quote", "age_days": 9, "days_left": 5}]
        unsent = [{"doc_no": "DN-1", "kind": "dn", "age_days": 4}]
        self.assertEqual(self.agenda(unpaid=unpaid, deliver=deliver, decide=decide, unsubmitted=unsent)["key"], "unpaid")
        self.assertEqual(self.agenda(deliver=deliver, decide=decide, unsubmitted=unsent)["key"], "deliver")
        self.assertEqual(self.agenda(decide=decide, unsubmitted=unsent)["key"], "decide")
        self.assertEqual(self.agenda(unsubmitted=unsent)["key"], "unsubmitted")
        self.assertEqual(self.agenda()["key"], "clear")

    def test_collect_keeps_every_currency_apart(self):
        out = self.agenda(unpaid=[{"doc_no": n, "kind": "inv", "age_days": a}
                                  for n, a in (("INV-1", 3), ("INV-2", 67), ("INV-3", 12))])
        self.assertEqual(out["verb"], "Collect")
        self.assertEqual(out["text"], "USD 1,500 + IQD 2,500,000 from 3 invoices",
                         "dinars must never be added to dollars")
        self.assertEqual(out["more"], "oldest 67 days")

    def test_the_other_items_read_naturally(self):
        self.assertEqual(self.agenda(deliver=[{"doc_no": "A-1", "kind": "po", "age_days": 1}])["text"], "1 client PO")
        follow = self.agenda(decide=[{"doc_no": "Q", "kind": "quote", "days_left": 6},
                                     {"doc_no": "R", "kind": "quote", "days_left": 3}])
        self.assertEqual((follow["text"], follow["more"]), ("2 quotations", "3 days left to mark Won or Lost"))
        self.assertEqual(self.agenda(decide=[{"doc_no": "Q", "kind": "quote", "days_left": 0}])["more"],
                         "mark Won or Lost today")
        dns = self.agenda(unsubmitted=[{"kind": "dn", "age_days": 1}, {"kind": "dn", "age_days": 9}])
        self.assertEqual((dns["text"], dns["more"]), ("2 delivery notes", "oldest 9 days"))
        mixed = self.agenda(unsubmitted=[{"kind": "dn", "age_days": 1}, {"kind": "inv", "age_days": 2}])
        self.assertEqual(mixed["text"], "2 documents")

    def card(self, **patches):
        with mock.patch.object(gate_brief.config, "DB_PATH", Path(self.db)), \
                mock.patch.object(gate_brief.database, "dashboard_needs_attention",
                                  side_effect=AssertionError("the card must not run the full feed")):
            with mock.patch.object(gate_brief.database, "agenda_feed", **patches):
                return gate_brief.card(date(2026, 10, 20))

    def test_card_reads_the_agenda_feed_and_never_the_full_dashboard(self):
        out = self.card(return_value={"unpaid": [], "deliver": [{"doc_no": "A-1", "kind": "po", "age_days": 1}],
                                      "decide": [], "unsubmitted": []})
        self.assertEqual(out["agenda"]["key"], "deliver")
        self.assertIn("tally", out)
        self.assertNotIn("recent", out, "'last edited' was dropped (Waleed, 2026-10-20) - do not compute it")

    def test_a_part_that_fails_leaves_the_others_standing(self):
        out = self.card(side_effect=RuntimeError("locked"))
        self.assertNotIn("agenda", out)
        self.assertIn("locked", out["errors"]["agenda"])
        self.assertIn("tally", out)

    def test_card_opens_the_database_read_only(self):
        src = (APP_ROOT / "backend" / "gate_brief.py").read_text(encoding="utf-8")
        body = src[src.index("def card("):]
        self.assertIn('"?mode=ro"', body, "the card's own process must never be able to write")


class AgendaFeed(unittest.TestCase):
    """database.agenda_feed(): the dashboard's own buckets, read-only."""

    def test_it_never_runs_the_sweep(self):
        from backend import database
        with mock.patch.object(database, "auto_lose_stale_quotes",
                               side_effect=AssertionError("agenda_feed wrote to the database")):
            out = database.agenda_feed()
        self.assertEqual(set(out) - {"ok"}, {"unpaid", "deliver", "decide", "unsubmitted"})

    def test_it_skips_exactly_what_the_sweep_would_mark_lost(self):
        from backend import database
        today = date(2026, 10, 20)
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE quotes (quote_no TEXT, quote_date TEXT, quote_type TEXT, "
                     "client_name TEXT, bid_status TEXT)")
        conn.execute("CREATE TABLE invoices (source_quote_no TEXT, pdf_path TEXT)")
        conn.execute("CREATE TABLE submitted_docs (kind TEXT, doc_no TEXT, submitted_at TEXT)")
        for no, days in (("Q-STALE", 20), ("Q-DECIDE", 9), ("Q-FRESH", 2)):
            conn.execute("INSERT INTO quotes VALUES (?,?,?,?,?)", (no, "2026-09-01", "", "", "open"))
            sub = date.fromordinal(today.toordinal() - days).isoformat()
            conn.execute("INSERT INTO submitted_docs VALUES ('quote', ?, ?)", (no, sub))
        unswept = [e["doc_no"] for e in database._decide_bucket(conn, today, swept=False)]
        swept = [e["doc_no"] for e in database._decide_bucket(conn, today, swept=True)]
        self.assertEqual(unswept, ["Q-DECIDE"], "a quote past the 14-day cutoff would be lost already")
        self.assertEqual(swept, ["Q-STALE", "Q-DECIDE"],
                         "swept=True must add no filter of its own - the real sweep already ran")

    def test_it_agrees_with_the_dashboard_on_live_data(self):
        from backend import database
        full = database.dashboard_needs_attention()       # sweeps, as every launch does
        mine = database.agenda_feed()
        for bucket in ("unpaid", "deliver", "decide", "unsubmitted"):
            self.assertEqual([e["doc_no"] for e in mine[bucket]], [e["doc_no"] for e in full[bucket]],
                             f"the card's {bucket} differs from the To-Do's")


class _FakeApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            if name == "costing_summary":
                return {"ok": False, "error": "boom"}
            return {"ok": True, "name": name, "args": list(args)}
        return call


class BootCache(unittest.TestCase):
    def setUp(self):
        boot_cache.reset()
        self.stamp = "v1"
        p = mock.patch.object(boot_cache, "fingerprint", side_effect=lambda: self.stamp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(boot_cache.reset)

    def warm(self):
        api = _FakeApi()
        t = boot_cache.start(api, today=date(2026, 10, 2))
        t.join(5)
        return api

    def test_answers_are_ready_and_used_once(self):
        api = self.warm()
        self.assertIn(("dashboard_needs_attention", ()), api.calls)
        got = boot_cache.take("dashboard_needs_attention", ())
        self.assertEqual(got["name"], "dashboard_needs_attention")
        self.assertIsNone(boot_cache.take("dashboard_needs_attention", ()), "an answer must be used once")

    def test_arguments_are_part_of_the_question(self):
        self.warm()
        self.assertIsNotNone(boot_cache.take("list_quotes_for_month", (2026, 10)))
        self.assertIsNotNone(boot_cache.take("list_quotes_for_month", (2026, 9)), "last month is warmed too")
        self.assertIsNone(boot_cache.take("list_quotes_for_month", (2026, 8)))

    def test_any_change_to_the_data_throws_every_answer_away(self):
        self.warm()
        self.stamp = "v2"                 # something was saved
        self.assertIsNone(boot_cache.take("dashboard_needs_attention", ()))
        self.assertIsNone(boot_cache.take("dashboard_analytics", (3,)))

    def test_a_failure_is_never_kept(self):
        self.warm()
        self.assertIsNone(boot_cache.take("costing_summary", (2026, 10)))

    def test_answers_expire(self):
        self.warm()
        with mock.patch.object(boot_cache.time, "monotonic", return_value=time.monotonic() + boot_cache.TTL_S + 1):
            self.assertIsNone(boot_cache.take("dashboard_needs_attention", ()))

    def test_a_call_the_page_already_made_is_not_warmed_again(self):
        # the page asked before warming began: it computes that one itself
        self.assertIsNone(boot_cache.take("dashboard_needs_attention", ()))
        api = self.warm()
        self.assertNotIn(("dashboard_needs_attention", ()), api.calls, "computed twice")

    def test_calls_match_what_the_page_asks_at_start_up(self):
        # Every warmed call must be one the page really makes at boot, with the
        # same arguments; a warmed answer nobody asks for is wasted start-up time.
        app_js = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for name, args in boot_cache.calls_for(date(2026, 10, 2)):
            self.assertIn(f"api().{name}(", app_js, f"the page never calls {name}")
        names = [n for n, _ in boot_cache.calls_for(date(2026, 10, 2))]
        self.assertIn("dashboard_analytics", names)
        self.assertIn("api().dashboard_analytics(3)", app_js, "warmed with 3 months; the page must ask for 3")

    def test_the_bridge_serves_a_kept_answer(self):
        from backend import api as api_mod
        a = api_mod.Api()
        with mock.patch.object(boot_cache, "take", return_value={"ok": True, "kept": 1}) as take:
            self.assertEqual(a.dashboard_needs_attention(), {"ok": True, "kept": 1})
            take.assert_called_with("dashboard_needs_attention", ())


class CardDrawing(unittest.TestCase):
    """The card as the child draws it, through the real painter."""

    @classmethod
    def setUpClass(cls):
        info = launch_gate.gate_info(APP_ROOT)
        if not info:
            raise unittest.SkipTest("no active company - NOTHING WAS CHECKED")
        cls.info = info
        cls.p = launch_gate._Painter(info, 1600, 900, 23)
        cls.chrome = cls.p.chrome()

    def test_the_layer_is_the_card_plus_room_for_its_shadow(self):
        p = self.p
        self.assertEqual(self.chrome.size, (p.lw, p.lh))
        self.assertEqual((p.lw, p.lh), (p.cw + 2 * p.m, p.ch + 2 * p.m))
        self.assertLessEqual(p.x0 + p.iw, p.m + p.cw - p.px(launch_gate.C["pad_x"]) + 1,
                             "the words run past the card's right padding")
        self.assertGreater(p.ys["hello"], p.m)
        self.assertLess(p.ys["step"], p.m + p.ch, "the step under the bar fell off the card")

    def test_the_logo_panel_is_white_and_the_other_is_the_company_colour(self):
        p, img = self.p, self.chrome
        left = img.getpixel((p.m + 8, p.m + p.ch // 2))
        self.assertEqual(left[:3], (255, 255, 255), f"the logo panel is not white: {left}")
        right = img.getpixel((p.m + p.cw - p.px(30), p.m + p.px(30)))
        acc = launch_gate._hex(self.info["accent"])
        self.assertTrue(all(abs(right[i] - acc[i]) <= 24 for i in range(3)),
                        f"the words' panel is not the company colour: {right} vs {acc}")
        self.assertEqual(right[3], 255)

    def test_the_shadow_falls_below_and_the_corners_are_clear(self):
        p, a = self.p, self.chrome.getchannel("A")
        self.assertEqual(a.getpixel((0, 0)), 0, "the layer's corner should be see-through")
        below = a.getpixel((p.lw // 2, p.m + p.ch + p.px(10)))
        above = a.getpixel((p.lw // 2, p.m - p.px(10)))
        self.assertGreater(below, above, "the shadow should fall below the card")

    def _bar(self, bar, label=launch_gate.STAGES[1]):
        img = self.chrome.copy()
        g = launch_gate._ChildGate(dict(self.info, launched_at=time.time()))
        g._draw_bar(self.p, img, bar, label, None, 1.0, 0, -10.0, time.perf_counter(), 0)
        return img

    def test_the_bar_never_punches_a_hole_in_the_card(self):
        # 2026-10-20: drawn straight onto the card with ImageDraw, the faint
        # track REPLACED the card's pixels with 22% white - the backdrop showed
        # through. Every pixel across the bar must stay fully opaque.
        p = self.p
        img = self._bar(0.3)
        y = p.ys["bar"] + max(2, p.px(launch_gate.C["bar_h"])) // 2
        alphas = {img.getpixel((x, y))[3] for x in range(p.x0, p.x0 + p.iw)}
        self.assertEqual(alphas, {255}, "the bar's track is see-through")

    def test_the_bar_fills_with_progress(self):
        p = self.p
        y = p.ys["bar"] + max(2, p.px(launch_gate.C["bar_h"])) // 2

        def white(img):
            return sum(1 for x in range(p.x0, p.x0 + p.iw) if img.getpixel((x, y))[:3] == (255, 255, 255))
        quarter, three = white(self._bar(0.25)), white(self._bar(0.75))
        self.assertGreater(quarter, 0)
        self.assertGreater(three, quarter * 2, "the bar should fill as the stages finish")

    def test_the_step_is_written_under_the_bar(self):
        a = self._bar(0.25, launch_gate.STAGES[1])
        b = self._bar(0.25, launch_gate.STAGES[3])
        self.assertNotEqual(a.tobytes(), b.tobytes(), "the words under the bar did not change with the step")

    def test_rows_draw_from_the_cards_data_and_stay_empty_without_it(self):
        p = self.p
        data = {"tally": {"text": "4 quotations · 26 POs · 1 DN · 30 invoices"},
                "agenda": {"verb": "Collect", "text": "USD 160,861 from 33 invoices", "more": "oldest 85 days"}}
        self.assertEqual([k for k, _ in launch_gate.ROWS], ["tally", "agenda"], "two rows: no 'last edited'")
        for key, label in launch_gate.ROWS:
            made = p.row_layer(key, label, data)
            self.assertIsNotNone(made, f"{key} drew nothing")
            self.assertLessEqual(made[1].width, p.iw + 2, f"{key} runs past the card")
            self.assertIsNone(p.row_layer(key, label, {"ok": False}), f"{key} drew without data")

    def test_a_long_line_is_cut_to_fit_never_overflows(self):
        measure = len                                          # one unit per character
        self.assertEqual(launch_gate.fit_text(measure, ["abc", "def"], 20), "abc · def")
        self.assertEqual(launch_gate.fit_text(measure, ["abc", "def"], 8), "abc", "drop whole parts first")
        cut = launch_gate.fit_text(measure, ["abcdefghij"], 6)
        self.assertTrue(cut.endswith(launch_gate.ELLIPSIS))
        self.assertLessEqual(len(cut), 6)

    def test_the_bar_never_reaches_the_end_before_ready(self):
        g = launch_gate._ChildGate(dict(self.info, launched_at=time.time()))
        g.stage = 3
        g.stage_at = time.perf_counter() - 60          # waited a whole minute
        self.assertLess(g._goal(), 1.0)
        g.stage = 4
        self.assertEqual(g._goal(), 1.0)


class _Events:
    def __init__(self, window):
        # pywebview's REAL Event, so handlers are dispatched exactly as the
        # app dispatches them. (A fake that called the handler directly let a
        # handler with the wrong parameter name pass - pywebview passes the
        # window only to a parameter named `window`.)
        from webview.event import Event
        self.before_show = Event(window, True)


class _Window:
    def __init__(self, form=None):
        self.native = form
        self.events = _Events(self)


class _Form:
    """A stand-in for the WinForms form: Invoke runs the action at once."""

    def __init__(self):
        self.Opacity = 1.0
        self.seen = []

    def Invoke(self, action):
        action()
        self.seen.append(self.Opacity)


class WindowHold(unittest.TestCase):
    """2026-10-20, Waleed: only the card on screen, the desktop visible
    behind it. The app window is SHOWN (WebView2 only loads while shown) but
    at Opacity 0 from its first frame, and fades in as the card leaves."""

    def setUp(self):
        self._gate, self._held = launch_gate._gate, launch_gate._held
        self.addCleanup(self._restore)
        # pythonnet's System.Action, as the app has it once pywebview is loaded
        fake_system = type(sys)("System")
        fake_system.Action = lambda fn: fn
        p = mock.patch.dict(sys.modules, {"System": fake_system})
        p.start()
        self.addCleanup(p.stop)
        self.timers = []
        t = mock.patch.object(launch_gate.threading, "Timer",
                              side_effect=lambda s, fn: self.timers.append((s, fn)) or mock.Mock())
        t.start()
        self.addCleanup(t.stop)

    def _restore(self):
        launch_gate._gate, launch_gate._held = self._gate, self._held

    @staticmethod
    def _alive(alive=True):
        g = mock.Mock()
        g.alive.return_value = alive
        return g

    @staticmethod
    def _join_reveal():
        import threading
        for t in threading.enumerate():
            if t.name == "reveal-window":
                t.join(3)

    def test_with_no_card_the_window_shows_as_it_always_did(self):
        for gate in (None, self._alive(False)):
            launch_gate._gate = gate
            form = _Form()
            w = _Window(form)
            self.assertFalse(launch_gate.hold_window(w))
            w.events.before_show.set()
            self.assertEqual(form.Opacity, 1.0, "the window was held with no card up")

    def test_held_invisible_from_the_first_frame_then_faded_in(self):
        launch_gate._gate = self._alive()
        form = _Form()
        w = _Window(form)
        self.assertTrue(launch_gate.hold_window(w))
        w.events.before_show.set()               # what pywebview does just before Show()
        self.assertEqual(form.Opacity, 0.0, "the window must be invisible from its first frame")
        launch_gate.reveal_window(0)
        self._join_reveal()
        self.assertEqual(form.Opacity, 1.0, "exactly 1.0, so WinForms drops the layered style")
        self.assertEqual(form.seen, sorted(form.seen), "the fade went backwards")
        self.assertGreater(len(set(form.seen)), 3, "it should fade, not snap")
        form.Opacity = 0.5
        launch_gate.reveal_window(0)             # nothing held any more: a no-op
        self._join_reveal()
        self.assertEqual(form.Opacity, 0.5)

    def test_never_a_window_region(self):
        # 2026-10-20: an empty window region hid the window and painted faster,
        # but WebView2's picture then landed 31 px LOW for the whole session
        # (white strip on top, bottom cut off - Waleed's screenshot), with every
        # window rect reading correct. Only a screenshot shows it.
        src = (APP_ROOT / "backend" / "launch_gate.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("SetWindowRgn", code, "a window region shifts the page - see the comment above hold_window")

    def test_never_left_invisible(self):
        launch_gate._gate = self._alive()
        launch_gate.hold_window(_Window())
        self.assertEqual(self.timers, [(launch_gate.WINDOW_SAFETY_S, launch_gate.reveal_window)],
                         "a safety timer must reveal the window whatever the page does")
        self.assertLess(launch_gate.WINDOW_SAFETY_S, launch_gate.SAFETY_S)
        src = (APP_ROOT / "app.py").read_text(encoding="utf-8")
        main_fn = src[src.index("def main()"):src.index("def _main()")]
        self.assertIn("launch_gate.reveal_window()", main_fn, "a start-up error must reveal the window")
        self.assertLess(src.index("launch_gate.hold_window(window)"), src.index("webview.start("),
                        "the hold must be registered before the window is shown")

    def test_ready_reveals_the_window_even_when_the_card_has_gone(self):
        from backend import api as api_mod
        for alive, delay in ((True, launch_gate.HOLD_MS), (False, None)):
            with mock.patch.object(launch_gate, "current", return_value=self._alive(alive)), \
                    mock.patch.object(launch_gate, "reveal_window") as reveal, \
                    mock.patch.object(launch_gate, "record_ready_without_gate"):
                api_mod.Api().gate_ready({"origin": 0})
            self.assertTrue(reveal.called, f"card alive={alive}: the window was never revealed")
            if delay is not None:
                reveal.assert_called_with(delay)


class GateProcess(unittest.TestCase):
    def test_messages_are_one_json_line_each_and_a_dead_gate_is_ignored(self):
        class Proc:
            def __init__(self):
                self.stdin = io.BytesIO()

            def poll(self):
                return None
        proc = Proc()
        g = launch_gate.GateProcess(proc)
        g.stage_done(2)
        g.open()
        g.close()
        lines = [json.loads(l) for l in proc.stdin.getvalue().decode().splitlines()]
        self.assertEqual([l["cmd"] for l in lines], ["stage", "open", "close"])
        self.assertEqual(lines[0]["n"], 2)
        proc.stdin.close()
        g.open()                          # pipe gone: must not raise

    def test_the_child_reads_its_own_info(self):
        # the card is spawned before anything else; it works out the company
        # and logo itself, so the app never waits for it
        src = (APP_ROOT / "backend" / "launch_gate.py").read_text(encoding="utf-8")
        start = src[src.index("def start(app_dir"):src.index("# ---------------------------------------------------------------- timings")]
        self.assertNotIn("gate_info(", start, "start() must not compute the card's info on the app's time")
        self.assertIn("0x4000", start, "the card must run below normal priority")

    def test_the_app_does_no_work_for_the_card(self):
        # The rows are worked out in the card's own process; the app must not
        # also build them (it used to send a brief from its Needs Attention).
        src = (APP_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("gate_brief", src)
        self.assertNotIn("set_brief", src)
        child = (APP_ROOT / "backend" / "launch_gate.py").read_text(encoding="utf-8")
        self.assertIn("gate_brief.card()", child.split("def _load_card")[1].split("def ")[0])

    def test_the_hold_is_the_same_on_both_sides(self):
        js = (APP_ROOT / "static" / "gate.js").read_text(encoding="utf-8")
        self.assertIn(f"HOLD_MS = {launch_gate.HOLD_MS}", js,
                      "the page and Windows must pause equally before the card goes")

    def test_the_page_settles_when_the_card_has_gone(self):
        from backend import api as api_mod

        class Gate:
            def alive(self):
                return True

            def add_marks(self, marks):
                pass

            def open(self):
                self.opened = True
        gate = Gate()
        with mock.patch.object(launch_gate, "current", return_value=gate), \
                mock.patch.object(launch_gate, "reveal_window"):
            out = api_mod.Api().gate_ready({"origin": 0})
        self.assertTrue(getattr(gate, "opened", False))
        self.assertEqual(out["settle_ms"], launch_gate.HOLD_MS + launch_gate.WINDOW_IN_MS,
                         "the page's own animations must wait until the window has faded in")


class GateStartRules(unittest.TestCase):
    def test_switch_off(self):
        with mock.patch.dict(os.environ, {"DOCUMENTS_HUB_NO_GATE": "1"}):
            self.assertIsNone(launch_gate.start(APP_ROOT))

    def test_no_registry_means_no_gate_not_a_crash(self):
        from backend import profile_manager
        from backend.profile_manager import ProfileManager
        work = Path(tempfile.mkdtemp())
        original = profile_manager._MANAGER
        try:
            profile_manager._MANAGER = ProfileManager(work / "data", APP_ROOT / "static")
            self.assertIsNone(launch_gate.gate_info(APP_ROOT))
        finally:
            profile_manager._MANAGER = original

    def test_greeting_by_hour(self):
        self.assertEqual(launch_gate.greeting(9), "Good morning")
        self.assertEqual(launch_gate.greeting(13), "Good afternoon")
        self.assertEqual(launch_gate.greeting(20), "Good evening")

    def test_importing_app_never_puts_the_gate_up(self):
        with mock.patch.object(launch_gate, "start", side_effect=AssertionError("gate started on import")):
            import importlib
            import app  # noqa: F401
            importlib.reload(app)


class OneBrandColour(unittest.TestCase):
    def test_config_reads_the_shared_map(self):
        from backend import config
        from backend.profile_manager import ProfileManager
        self.assertEqual(config.PROFILE_ACCENT, ProfileManager.PROFILE_ACCENTS[config.ACTIVE_PROFILE])

    def test_no_second_copy_of_the_rule(self):
        for name in ("config.py", "api.py", "launch_gate.py"):
            src = (APP_ROOT / "backend" / name).read_text(encoding="utf-8")
            self.assertNotRegex(src, r'"#1F3864" if .*abraj.* else "#861432"',
                                f"{name} decides the company colour again")


class SmallLogos(unittest.TestCase):
    def test_the_bridge_sends_a_display_size_logo(self):
        # 650 KB of base64 for Abraj, three times per start, before 2026-10-02
        import base64
        from PIL import Image
        from backend import profile_manager
        full = profile_manager.logo_data_uri()
        if not full:
            self.skipTest("no logo - NOTHING WAS CHECKED")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
            small = profile_manager.logo_ui_data_uri()       # made fresh, not read from a cache
        self.assertTrue(small.startswith("data:image/"))
        img = Image.open(io.BytesIO(base64.b64decode(small.split(",", 1)[1])))
        self.assertLessEqual(max(img.size), profile_manager.UI_LOGO_MAX_PX,
                             f"the display logo is {img.size}, not display size")
        self.assertLess(len(small), len(full) // 3, "the display logo should be a fraction of the original")
        from backend import api as api_mod
        meta = api_mod.Api().get_active_profile()
        self.assertLess(len(json.dumps(meta)), 400_000, "get_active_profile is heavy again")


if __name__ == "__main__":
    unittest.main(verbosity=2)
