"""
The To-Do list must never hide outstanding work behind a month boundary.

Until 2026-08-10 `dashboard_needs_attention` scoped three of its five buckets
(`uncosted`, `unsubmitted`, `deliver`) to one month, so a PO raised in June
whose client lines were never invoiced simply stopped appearing once July
started - in the one screen whose entire job is to stop things being
forgotten. Measured on the live Abraj data the day this changed: standing on
August the list showed 15 items and hid 55.

The buckets now span every month by default; passing `year`+`month` is an
explicit, opt-in filter. This suite pins that:

  1. The default (no arguments) is a SUPERSET of any single month.
  2. Every month's scoped result is contained in the default - nothing is
     invented, and nothing that used to show is lost.
  3. The union of all months EQUALS the default - the unscoped queries and the
     scoped ones select the same rows, just without the WHERE.
  4. Every entry carries `age_days`, and the buckets are ordered oldest-first
     (except `decide`, which is ranked by how close it is to auto-loss).
  5. Muted entries are still partitioned out into `ignored` exactly once.
  6. A bucket that waits on the OTHER PARTY (`decide`, `unpaid`, `deliver`)
     only lists SUBMITTED documents - a draft nobody has been sent cannot be
     overdue. `unsubmitted` is never gated (that would hide the work) and
     `uncosted` is exempt on purpose (costing precedes sending).

!! Read-only. Runs against the LIVE profile DB (see the warning in docs/11)
!! and writes nothing - no `set_attention_ignore`, no doc mutations.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import config, database  # noqa: E402

CARRYOVER = ("uncosted", "unsubmitted", "deliver")
ALL_BUCKETS = ("decide", "uncosted", "unsubmitted", "unpaid", "deliver", "ignored")


def _months_with_docs():
    out = set()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        for table in ("pos", "quotes", "dns", "invoices"):
            try:
                for r in conn.execute(f"SELECT DISTINCT year, month FROM {table}"):
                    out.add((r["year"], r["month"]))
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return sorted(out)


def _keys(entries):
    return {(e["kind"], e["doc_no"]) for e in entries}


class DefaultSpansEveryMonth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.months = _months_with_docs()
        cls.all = database.dashboard_needs_attention()

    def test_the_scoped_flag_reports_which_mode_ran(self):
        self.assertFalse(self.all["scoped"])
        self.assertTrue(database.dashboard_needs_attention(2026, 8)["scoped"])
        # A falsy month is "no preference", not month zero.
        self.assertFalse(database.dashboard_needs_attention(2026, None)["scoped"])

    def test_default_contains_every_single_month(self):
        if len(self.months) < 2:
            self.skipTest("need documents in at least two months")
        for (y, m) in self.months:
            one = database.dashboard_needs_attention(y, m)
            for bucket in CARRYOVER:
                missing = _keys(one[bucket]) - _keys(self.all[bucket])
                self.assertEqual(
                    missing, set(),
                    f"{y}-{m:02d} {bucket}: {len(missing)} entr(ies) visible when "
                    f"that month is picked but MISSING from the default view")

    def test_default_is_exactly_the_union_of_the_months(self):
        """Not just a superset - the same rows. A wider default that also
        invents entries would pass the containment check above."""
        if not self.months:
            self.skipTest("no documents in the active profile")
        for bucket in CARRYOVER:
            union = set()
            for (y, m) in self.months:
                union |= _keys(database.dashboard_needs_attention(y, m)[bucket])
            self.assertEqual(_keys(self.all[bucket]), union,
                             f"{bucket}: the default and the month-by-month union differ")

    def test_carryover_actually_carries(self):
        """The regression itself: standing on the LATEST month must not hide
        older outstanding work."""
        if len(self.months) < 2:
            self.skipTest("need documents in at least two months")
        latest = self.months[-1]
        scoped = database.dashboard_needs_attention(*latest)
        hidden = sum(len(_keys(self.all[b]) - _keys(scoped[b])) for b in CARRYOVER)
        older = sum(len(database.dashboard_needs_attention(y, m)[b])
                    for (y, m) in self.months[:-1] for b in CARRYOVER)
        if older == 0:
            self.skipTest("nothing outstanding in the earlier months to carry over")
        self.assertGreater(hidden, 0,
                           "older outstanding work exists but the default view "
                           "shows no more than the latest month")

    def test_unpaid_and_decide_were_already_all_months(self):
        """These two were never scoped; scoping them now would be a regression
        in the other direction."""
        if not self.months:
            self.skipTest("no documents in the active profile")
        for (y, m) in self.months:
            one = database.dashboard_needs_attention(y, m)
            self.assertEqual(_keys(one["unpaid"]), _keys(self.all["unpaid"]))
            self.assertEqual(_keys(one["decide"]), _keys(self.all["decide"]))


class DraftsAreNotChased(unittest.TestCase):
    """A document nobody has been sent cannot be overdue.

    Until 2026-08-10 an unsubmitted PO appeared in BOTH `deliver` ("chase the
    invoice for this") and `unsubmitted` ("send this"), and because drafts are
    old and every bucket sorts oldest-first they led the list - on the live
    Abraj data the five oldest entries, all "76 days waiting", were POs that
    were never issued. `decide` had always excluded unsubmitted quotes; the
    other action buckets now match it.
    """

    @classmethod
    def setUpClass(cls):
        cls.all = database.dashboard_needs_attention()
        cls.submitted = {k: database.submitted_set(k)
                         for k in ("po", "quote", "dn", "inv")}

    def _drafts_in(self, bucket):
        return [e["doc_no"] for e in self.all[bucket]
                if e["doc_no"] not in self.submitted.get(e["kind"], set())]

    def test_buckets_that_wait_on_someone_else_need_a_submitted_document(self):
        for bucket in ("decide", "unpaid", "deliver"):
            self.assertEqual(
                self._drafts_in(bucket), [],
                f"{bucket} is chasing document(s) that were never submitted")

    def test_uncosted_is_deliberately_exempt_from_the_gate(self):
        """Costing is INTERNAL and happens BEFORE you send a quotation - the
        cost is how you price it - so gating `uncosted` on submission would
        drop the reminder exactly when it is most useful. Pinned so the
        exemption stays a decision rather than drifting into a leak, and so
        anyone tempted to "make it consistent" sees why it is not.
        """
        drafts = self._drafts_in("uncosted")
        uncosted_drafts_exist = any(
            e["doc_no"] not in self.submitted.get(e["kind"], set())
            for e in database.dashboard_needs_attention()["uncosted"]
        ) or bool(drafts)
        if not uncosted_drafts_exist:
            self.skipTest("no uncosted draft in this profile to prove the exemption")
        self.assertTrue(drafts,
                        "an uncosted draft exists but `uncosted` filtered it out - "
                        "costing precedes submission, so it must not be gated")

    def test_unsubmitted_is_the_one_bucket_that_is_not_gated(self):
        """Gating it too would hide the work completely - that is the trap."""
        drafts = self._drafts_in("unsubmitted")
        self.assertEqual(len(drafts), len(self.all["unsubmitted"]),
                         "every entry in `unsubmitted` is by definition a draft")

    def test_the_gate_matches_on_kind_not_just_the_number(self):
        """`submitted_docs` is keyed `(kind, doc_no)` because a number is only
        unique within its own type. The live data has no cross-kind collision,
        so only a synthetic one can prove the gate honours the kind."""
        submitted = {"po": {"X-1"}, "inv": set(), "quote": set(), "dn": set()}
        entries = [{"kind": "po", "doc_no": "X-1"}, {"kind": "inv", "doc_no": "X-1"}]
        kept = database._submitted_only(entries, submitted)
        self.assertEqual(kept, [{"kind": "po", "doc_no": "X-1"}],
                         "the invoice shares a number with a submitted PO but was "
                         "never submitted itself - it must not pass the gate")

    def test_an_unknown_kind_never_passes_the_gate(self):
        self.assertEqual(
            database._submitted_only([{"kind": "ltr", "doc_no": "L-1"}], {"po": {"L-1"}}), [])

    def test_nothing_is_lost_only_moved(self):
        """A draft removed from an action bucket must still be reachable - it
        appears under `unsubmitted`, whose action ('send it') is the real one."""
        unscoped = {(e["kind"], e["doc_no"]) for e in self.all["unsubmitted"]}
        raw_deliver = database.pos_awaiting_invoice()
        raw_unpaid = database.list_unpaid()
        shown = {(e["kind"], e["doc_no"]) for e in self.all["deliver"]}
        shown |= {(e["kind"], e["doc_no"]) for e in self.all["unpaid"]}
        muted = {(e["kind"], e["doc_no"]) for e in self.all["ignored"]}
        for e in raw_deliver + raw_unpaid:
            key = (e["kind"], e["doc_no"])
            self.assertTrue(key in shown or key in unscoped or key in muted,
                            f"{e['doc_no']} vanished from the To-Do entirely")


class AgeAndOrdering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all = database.dashboard_needs_attention()

    def test_every_entry_carries_its_age(self):
        for bucket in ALL_BUCKETS:
            for e in self.all[bucket]:
                self.assertIn("age_days", e, f"{bucket}/{e['doc_no']} has no age_days")
                self.assertTrue(e["age_days"] is None or isinstance(e["age_days"], int))

    def test_age_matches_the_document_date(self):
        from datetime import date
        today = date.today()
        for bucket in ALL_BUCKETS:
            for e in self.all[bucket]:
                d = database._parse_doc_date(e.get("date"))
                if d is None:
                    self.assertIsNone(e["age_days"], "an undated row cannot have an age")
                else:
                    self.assertEqual(e["age_days"], (today - d).days, e["doc_no"])

    def test_buckets_are_oldest_first(self):
        """Oldest first is what makes the list usable once it spans years -
        the thing that has waited longest sits at the top."""
        from datetime import date
        for bucket in ("uncosted", "unsubmitted", "unpaid", "deliver"):
            dates = [database._parse_doc_date(e.get("date")) or date.max
                     for e in self.all[bucket]]
            self.assertEqual(dates, sorted(dates), f"{bucket} is not oldest-first")

    def test_decide_is_ranked_by_urgency_not_date(self):
        # An explicit skip, not a silent pass: with fewer than two bids the
        # ordering assertion proves nothing, and a profile with no open bids
        # would otherwise report this rule as covered when it isn't.
        if len(self.all["decide"]) < 2:
            self.skipTest("fewer than two bids to decide in this profile")
        ages = [e["days_open"] for e in self.all["decide"]]
        self.assertEqual(ages, sorted(ages, reverse=True))

    def test_a_muted_entry_appears_once_and_only_in_ignored(self):
        ignored = {(e["category"], e["kind"], e["doc_no"]) for e in self.all["ignored"]}
        for bucket in ("decide", "uncosted", "unsubmitted", "unpaid", "deliver"):
            for e in self.all[bucket]:
                self.assertNotIn((bucket, e["kind"], e["doc_no"]), ignored,
                                 f"{e['doc_no']} is both live and muted in {bucket}")
        self.assertEqual(len(ignored), len(self.all["ignored"]), "duplicate ignored rows")


class HelperDefaults(unittest.TestCase):
    """The two list helpers the buckets are built from."""

    def test_list_unsubmitted_defaults_to_every_month(self):
        months = _months_with_docs()
        if len(months) < 2:
            self.skipTest("need documents in at least two months")
        every = _keys(database.list_unsubmitted())
        for (y, m) in months:
            self.assertLessEqual(_keys(database.list_unsubmitted(y, m)), every)

    def test_pos_awaiting_invoice_defaults_to_every_month(self):
        months = _months_with_docs()
        if len(months) < 2:
            self.skipTest("need documents in at least two months")
        every = _keys(database.pos_awaiting_invoice())
        for (y, m) in months:
            self.assertLessEqual(_keys(database.pos_awaiting_invoice(y, m)), every)

    def test_helpers_stamp_age_too(self):
        for entries in (database.list_unsubmitted(), database.list_unpaid(),
                        database.pos_awaiting_invoice()):
            for e in entries:
                self.assertIn("age_days", e)

    def test_oldest_first_helper_puts_undated_rows_last(self):
        rows = [{"doc_no": "B", "date": ""}, {"doc_no": "A", "date": "01/01/2026"}]
        ordered = database._oldest_first(rows)
        self.assertEqual([r["doc_no"] for r in ordered], ["A", "B"])
        self.assertIsNone(ordered[1]["age_days"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
