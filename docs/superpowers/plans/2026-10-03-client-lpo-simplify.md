# Client LPOs Simplified - Implementation Plan

> **For agentic workers:** this repo does NOT use subagent-driven development
> (CLAUDE.md, Waleed's ruling 2026-09-11: parallel agents against one live SQLite
> database created the test-lock problem). Execute inline, task by task. Steps use
> checkbox (`- [ ]`) syntax. No commits unless Waleed asks.

**Goal:** One LPO list with type tabs and per-type columns plus a next-step button
per row, and a calmer LPO screen (one primary action, a More menu, a right panel
that leads with what matters), backed by ONE backend rule for "next step".

**Architecture:** `database.lpo_next_step()` decides each LPO's next step from
the rollups `list_lpos` already computes plus a materials fulfilment rollup built
from one delivery-note/invoice index. `static/lpo.js` renders the list and the
LPO screen from those fields; the billing ledger, signing and the form keep
their behaviour.

**Tech stack:** Python 3.13 (sqlite3), vanilla JS in pywebview/WebView2, shared
`renderChipRow` from `static/app.js`, `Popup` from `static/popup.js`, pdf.js.

Spec: [2026-10-03-client-lpo-simplify-design.md](../specs/2026-10-03-client-lpo-simplify-design.md)

---

## File map

| File | Change |
|---|---|
| `backend/database.py` | add `_fulfillment_docs`, `_items_rollup`, `_ACTION_STEPS`, `lpo_next_step`; `list_lpos` adds `items` + `next_step`; `lpo_fulfillment` uses the shared index |
| `tests/lpo_next_step.test.py` | new: the rule table + the list rollup on isolated data |
| `static/lpo.js` | list (tabs, columns, figures, next step), LPO screen (header actions, More menu, panel sections, Fit width), attach resolves after upload, Previous/Next removed |
| `templates/index.html` | `#lpoView` and `#lpoDocView` markup |
| `static/app_styles.css` | replace the list/row/KPI/group/side-panel rules with the new ones; keep form, picker, marks, To-Do preview |
| `tests/lpo_row_summary.test.js`, `tests/lpo_audit_frontend.test.js`, `tests/lpo_viewer_integrity.test.js`, `tests/lpo_workflow.qa.cjs` | update where they pinned removed markup, keeping what each protected |
| `tests/lpo_screen.test.js` | new: tabs, columns, next step, header actions, panel sections, against the real `lpo.js` |
| docs 03, 05, 07, 08, 17, AGENT-BOARD | same-change doc updates |

---

### Task 1: Backend next step and materials rollup

**Files:** Modify `backend/database.py` (near `lpo_fulfillment`, ~line 2718; `list_lpos`, ~line 2332). Test: `tests/lpo_next_step.test.py`.

- [ ] **Step 1: Write the failing test** `tests/lpo_next_step.test.py`:

```python
"""Every LPO says what to do next - one rule, used by the list and the LPO screen.

Waleed asked for a simpler Client LPOs screen (2026-10-03). The list now carries
a "Next step" per row and the LPO screen leads with the same action, so the
decision lives here once instead of being re-derived in JavaScript.

Isolation: see the note in `client_lpo_save.test.py`.
"""
import sys
import unittest
from datetime import date
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import config, database, profile_manager  # noqa: E402

TODAY = date(2026, 10, 3)


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


def line(ordered, delivered=0, invoiced=0):
    return {"item": "x", "ordered": ordered, "delivered": delivered, "invoiced": invoiced}


def step(row, fulfil=None):
    base = {"kind": "materials", "file_exists": True, "closed_at": None,
            "total_usd": 0, "invoiced_usd": 0, "dns": [], "invoices": []}
    base.update(row)
    return database.lpo_next_step(base, fulfil, today=TODAY)


class Materials(unittest.TestCase):
    def test_delivered_but_not_invoiced_asks_for_the_invoice(self):
        """LPO CSSP-ADM-0033: both items delivered on DN-HDEC-067, none invoiced."""
        s = step({"total_usd": 490}, {"lines": [line(1, 1), line(1, 1)]})
        self.assertEqual(("invoice", "Create invoice", True), (s["key"], s["text"], s["action"]))

    def test_nothing_delivered_asks_for_the_delivery_note(self):
        """CSSP-ADM-0017: twelve items ordered on 24 July, none delivered."""
        s = step({"total_usd": 273.4}, {"lines": [line(4), line(8)]})
        self.assertEqual("deliver", s["key"])
        self.assertTrue(s["action"])

    def test_money_comes_before_the_rest_of_the_delivery(self):
        s = step({"total_usd": 100}, {"lines": [line(10, 5, 0)]})
        self.assertEqual("invoice", s["key"])

    def test_invoiced_directly_still_owes_the_delivery(self):
        """The fully invoiced order whose goods have not all gone out yet."""
        s = step({"total_usd": 100, "invoiced_usd": 100}, {"lines": [line(10, 0, 10)]})
        self.assertEqual("deliver", s["key"])

    def test_fully_invoiced_value_never_asks_for_another_invoice(self):
        """Invoice rows that do not match the LPO's line names leave `invoiced` at
        0 per line; the money says it is billed, so no invoice is suggested."""
        s = step({"total_usd": 100, "invoiced_usd": 100}, {"lines": [line(10, 10, 0)]})
        self.assertEqual("done", s["key"])

    def test_everything_delivered_and_invoiced_is_done(self):
        s = step({"total_usd": 18390, "invoiced_usd": 18390},
                 {"lines": [line(60, 60, 60), line(78, 78, 78)]})
        self.assertEqual(("done", "Fully invoiced", False), (s["key"], s["text"], s["action"]))

    def test_no_lines_and_no_documents_asks_for_the_delivery_note(self):
        self.assertEqual("deliver", step({"total_usd": 50}, {"lines": []})["key"])

    def test_no_lines_but_delivered_asks_for_the_invoice(self):
        self.assertEqual("invoice", step({"total_usd": 50, "dns": ["DN-1"]})["key"])

    def test_no_lines_and_fully_invoiced_is_done(self):
        s = step({"total_usd": 50, "invoiced_usd": 50, "dns": ["DN-1"], "invoices": ["INV-1"]})
        self.assertEqual("done", s["key"])


class Contracts(unittest.TestCase):
    def rental(self, **kw):
        base = {"kind": "rental", "total_usd": 11900, "invoiced_usd": 6800,
                "period_start": "8/6/2026", "period_end": "8/1/2027",
                "months_unbilled": 0, "oldest_due_date": ""}
        base.update(kw)
        return step(base)

    def test_an_unbilled_period_names_its_month(self):
        s = self.rental(months_unbilled=1, oldest_due_date="2026-09-24")
        self.assertEqual(("invoice_period", "Invoice September"), (s["key"], s["text"]))
        self.assertEqual("2026-09-24", s["period_start"])
        self.assertTrue(s["action"])

    def test_a_supply_contract_names_its_oldest_month(self):
        """CSSP-OPEN-0001: six calendar months unbilled, the oldest is May."""
        s = step({"kind": "open", "months_unbilled": 6, "oldest_due_date": "2026-05-15"})
        self.assertEqual(("invoice_period", "Invoice May"), (s["key"], s["text"]))

    def test_fully_invoiced_with_no_end_date_asks_for_the_end_date(self):
        """CSSP-RENT-0004: $15,600 of $15,600 invoiced, no end date, so the ledger
        keeps opening a new month. The fix is the date, not another invoice."""
        s = self.rental(total_usd=15600, invoiced_usd=15600, period_start="24/7/2026",
                        period_end="", months_unbilled=1, oldest_due_date="2026-09-24")
        self.assertEqual(("end_date", "Set the end date", True), (s["key"], s["text"], s["action"]))

    def test_fully_invoiced_and_ending_soon_says_when(self):
        """CSSP-RENT-0005(3): all three months billed, the term ends 11 November."""
        s = self.rental(total_usd=5100, invoiced_usd=5100, period_start="12/8/2026",
                        period_end="11/11/2026")
        self.assertEqual(("ending", "Ends 11 Nov", False), (s["key"], s["text"], s["action"]))

    def test_fully_invoiced_and_ended_is_finished(self):
        s = self.rental(total_usd=27139.4, invoiced_usd=27139.4, period_start="18/4/2026",
                        period_end="12/8/2026")
        self.assertEqual(("done", "Finished"), (s["key"], s["text"]))

    def test_a_running_rental_with_nothing_due_is_up_to_date(self):
        """CSSP-RENT-0002: September billed, the next period starts 8 October."""
        s = self.rental()
        self.assertEqual(("ok", "Up to date", False), (s["key"], s["text"], s["action"]))

    def test_a_rental_ending_within_thirty_days_says_so(self):
        s = self.rental(period_end="20/10/2026")
        self.assertEqual(("ending", "Ends 20 Oct"), (s["key"], s["text"]))

    def test_an_ended_rental_with_nothing_due_is_finished(self):
        self.assertEqual("done", self.rental(period_end="1/9/2026")["key"])


class Everything(unittest.TestCase):
    def test_closed_by_hand_wins_over_everything(self):
        s = step({"closed_at": "2026-09-01T00:00:00", "total_usd": 100}, {"lines": [line(1, 1)]})
        self.assertEqual(("closed", "Closed", False), (s["key"], s["text"], s["action"]))

    def test_no_document_asks_for_it_when_nothing_else_is_due(self):
        s = step({"kind": "rental", "file_exists": False, "total_usd": 100,
                  "period_start": "1/9/2026", "period_end": "1/9/2027"})
        self.assertEqual(("attach", True), (s["key"], s["action"]))

    def test_money_outranks_a_missing_document(self):
        s = step({"file_exists": False, "total_usd": 100}, {"lines": [line(1, 1)]})
        self.assertEqual("invoice", s["key"])


class FromTheDatabase(unittest.TestCase):
    """`list_lpos` hands every row its next step, and materials rows their items."""

    ORDERED = [{"item": "Currency Counting Machine", "unit": "EA", "qty": 1, "rate": "250"},
               {"item": "High Security Steel Cash Locker", "unit": "EA", "qty": 1, "rate": "240"}]

    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
            conn.execute("DELETE FROM dns")
            conn.execute("DELETE FROM invoices")
        self.assertTrue(database.save_lpo(
            client_name="HDEC", lpo_no="LPO CSSP-ADM-0033", kind="materials",
            total=490, currency="USD", rows=self.ORDERED)["ok"])

    def row(self):
        return next(r for r in database.list_lpos("") if r["lpo_no"] == "LPO CSSP-ADM-0033")

    def test_a_new_order_asks_for_its_delivery_note(self):
        r = self.row()
        self.assertEqual("deliver", r["next_step"]["key"])
        self.assertEqual({"lines": 2, "delivered": 0, "invoiced": 0,
                          "part_delivered": 0}, {k: r["items"][k] for k in
                          ("lines", "delivered", "invoiced", "part_delivered")})

    def test_a_delivery_note_moves_it_to_the_invoice(self):
        database.save_dn_record(dn_no="DN-HDEC-067", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0,
                                pdf_path="DN-HDEC-067.pdf", rows=self.ORDERED)
        r = self.row()
        self.assertEqual(2, r["items"]["delivered"])
        self.assertEqual("invoice", r["next_step"]["key"])

    def test_a_draft_delivery_note_does_not_count(self):
        database.save_dn_record(dn_no="DN-HDEC-068", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0, pdf_path="",
                                rows=self.ORDERED)
        self.assertEqual(0, self.row()["items"]["delivered"])

    def test_the_list_and_the_lpo_screen_agree(self):
        database.save_dn_record(dn_no="DN-HDEC-067", dn_date="28/9/2026", year=2026, month=9,
                                client_name="HDEC", lpo_no="LPO CSSP-ADM-0033",
                                client_address="", include_prices=0,
                                pdf_path="DN-HDEC-067.pdf", rows=self.ORDERED[:1])
        screen = database.lpo_fulfillment("HDEC", "LPO CSSP-ADM-0033")
        listed = self.row()["items"]
        self.assertEqual(sum(1 for l in screen["lines"] if l["delivered"] >= l["ordered"]),
                         listed["delivered"])
        self.assertEqual(1, listed["delivered"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run it, expect failure**

Run: `python tests/lpo_next_step.test.py`
Expected: errors, `AttributeError: module 'backend.database' has no attribute 'lpo_next_step'`.

- [ ] **Step 3: Implement** in `backend/database.py`.

Replace the body of `lpo_fulfillment` after the `rec` lookup with the shared index, and add the helpers right after it:

```python
def lpo_fulfillment(client_name: str, lpo_no: str) -> dict[str, Any]:
    """(docstring unchanged)"""
    rec = load_lpo(client_name, lpo_no)
    if not rec:
        return {"lines": [], "extra": [], "complete": None}
    ordered = json.loads(rec["rows_json"]) if rec.get("rows_json") else None
    identities = _identities()
    key = _usage_key(rec["client_name"], rec["lpo_no"], identities)
    dn_rows, inv_rows = _fulfillment_docs(identities).get(key, ([], []))
    return _fulfillment_from_rows(ordered, dn_rows, inv_rows)


def _fulfillment_docs(identities: dict[str, str] | None = None
                      ) -> dict[tuple[str, str], tuple[list, list]]:
    """Every issued delivery note's and invoice's rows, grouped by LPO, in ONE pass.

    The list needs delivered/invoiced counts for every materials LPO, and the LPO
    screen for one; both read this, so they cannot disagree (2026-10-03). Same
    filters as before: `N/A` is not an LPO, and a draft (no PDF) was never sent.
    """
    out: dict[tuple[str, str], tuple[list, list]] = {}
    with _connect() as conn:
        for table, slot in (("dns", 0), ("invoices", 1)):
            for r in conn.execute(
                    f"SELECT client_name, lpo_no, rows_json FROM {table} "
                    "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                    "AND TRIM(COALESCE(pdf_path,'')) <> ''"):
                key = _usage_key(r["client_name"], r["lpo_no"], identities)
                out.setdefault(key, ([], []))[slot].append(json.loads(r["rows_json"] or "[]"))
    return out


def _items_rollup(fulfil: dict[str, Any]) -> dict[str, Any]:
    """How many of an order's lines are delivered and invoiced - counts of LINES,
    because quantities in different units (EA, PACK) cannot be added up."""
    lines = fulfil.get("lines") or []
    eps = 0.001
    return {
        "lines": len(lines),
        "delivered": sum(1 for l in lines if l["delivered"] >= l["ordered"] - eps),
        "invoiced": sum(1 for l in lines if l["invoiced"] >= l["ordered"] - eps),
        "part_delivered": sum(1 for l in lines if eps < l["delivered"] < l["ordered"] - eps),
        "complete": fulfil.get("complete"),
    }


_ACTION_STEPS = frozenset({"invoice", "deliver", "invoice_period", "end_date", "attach"})


def lpo_next_step(d: dict[str, Any], fulfil: dict[str, Any] | None = None,
                  today: date | None = None) -> dict[str, Any]:
    """What to do next on one LPO: `{key, text, action[, period_start]}`.

    ONE rule for the list's "Next step" column and the LPO screen's main button
    (2026-10-03). It reads only what `list_lpos` already computed - the billing
    ledger's months, the invoice rollup and, for a materials order, `fulfil`
    from `_fulfillment_from_rows` - and never decides billing itself.

    Money first: something delivered and not invoiced outranks the rest of a
    delivery, which outranks paperwork. A materials order whose value is fully
    invoiced is never asked for another invoice even when its invoice rows did
    not match the LPO's line names - the money is the authority there.
    """
    import calendar
    today = today or date.today()
    kind = _lpo_kind(d.get("kind"))
    total = float(d.get("total_usd") or 0)
    invoiced = float(d.get("invoiced_usd") or 0)
    value_used = total > 0 and invoiced >= total - 0.01

    def step(key: str, text: str, **extra: Any) -> dict[str, Any]:
        return {"key": key, "text": text, "action": key in _ACTION_STEPS, **extra}

    if d.get("closed_at"):
        return step("closed", "Closed")

    if kind == "materials":
        lines = (fulfil or {}).get("lines") or []
        eps = 0.001
        if lines:
            if not value_used and any(l["delivered"] > l["invoiced"] + eps for l in lines):
                return step("invoice", "Create invoice")
            if any(l["delivered"] < l["ordered"] - eps for l in lines):
                return step("deliver", "Create delivery note")
            return step("done", "Fully invoiced")
        if not d.get("dns") and not d.get("invoices"):
            return step("deliver", "Create delivery note")
        if not value_used:
            return step("invoice", "Create invoice")
        return step("done", "Fully invoiced")

    due = int(d.get("months_unbilled") or 0)
    end = _parse_doc_date(d.get("period_end") or "")
    if kind == "rental" and value_used:
        if due and not end:
            return step("end_date", "Set the end date")
        if end and end < today:
            return step("done", "Finished")
        if end and (end - today).days <= 30:
            return step("ending", f"Ends {end.day} {calendar.month_abbr[end.month]}")
        return step("done", "Fully invoiced")
    if due:
        oldest = _parse_doc_date(d.get("oldest_due_date") or "")
        text = f"Invoice {calendar.month_name[oldest.month]}" if oldest else "Invoice next period"
        return step("invoice_period", text, period_start=d.get("oldest_due_date") or "")
    if kind == "rental" and end:
        if end < today:
            return step("done", "Finished")
        if (end - today).days <= 30:
            return step("ending", f"Ends {end.day} {calendar.month_abbr[end.month]}")
    if not d.get("file_exists"):
        return step("attach", "Attach their PO")
    return step("ok", "Up to date")
```

In `list_lpos`, build the index once and add the two fields (replace the loop):

```python
    docs = _fulfillment_docs(identities) if any(
        _lpo_kind(r.get("kind")) == "materials" for r in rows) else {}
    out = []
    for d in rows:
        d = _lpo_row(d)
        key = _usage_key(d["client_name"], d["lpo_no"], identities)
        d.update(usage.get(key, {"dns": [], "invoices": [], "invoiced_usd": 0.0}))
        d["remaining_usd"] = round(max(0.0, (d.get("total_usd") or 0) - d["invoiced_usd"]), 2)
        # One service-period ledger feeds this screen and the To-Do reminder.
        if _lpo_kind(d.get("kind")) in ("rental", "open"):
            ledger = lpo_billing.summary(d, billing_index)
            d.update({k:v for k,v in ledger.items() if k not in ("ok","periods","invoices")})
        fulfil = None
        if _lpo_kind(d.get("kind")) == "materials":
            dn_rows, inv_rows = docs.get(key, ([], []))
            fulfil = _fulfillment_from_rows(d.get("rows"), dn_rows, inv_rows)
            d["items"] = _items_rollup(fulfil)
        d["status"] = _lpo_status(d)
        d["over_value"] = lpo_is_over_value(d)
        d["next_step"] = lpo_next_step(d, fulfil)
        out.append(d)
    return out
```

`_parse_doc_date` accepts "D/M/YYYY" and ISO "YYYY-MM-DD" (check; if it does not take ISO, parse `oldest_due_date` with `date.fromisoformat` instead).

- [ ] **Step 4: Run** `python tests/lpo_next_step.test.py` - expect all pass. Then `python tests/client_lpo_fulfillment.test.py` and `python tests/client_lpo_status.test.py` - still green.

- [ ] **Step 5: Mutation checks** - break each branch (money-first order, `value_used` guard, end-date rule, draft filter) and confirm the test goes red.

### Task 2: List markup, styles and rendering

**Files:** `templates/index.html` (`#lpoView`), `static/app_styles.css` (LPO list block), `static/lpo.js` (state, list functions, wiring).

- [ ] **Step 1: Markup.** Replace `#lpoView`'s inside with: the `cl-header` (title + count; search, Add LPO, Back), `.lpo-top` holding `#lpoKpis` (figures) and a `filter-chips-stack` with `#lpoKindChips` and `#lpoClientChips`, then `.lpo-body` with `#lpoList` and a `.lpo-foot` holding `#lpoTotals` and `#lpoReferenceReview`.

- [ ] **Step 2: Rendering** in `static/lpo.js`: state gains `kind: "all"` and `zoom: "fit"`; add `LPO_TABS`, `lpoKind`, `lpoDate`, `lpoDay`, `lpoSpan`, `lpoQty`, `lpoIsDone`, `lpoOrder`, `lpoMeter`, the `lpoCell*` renderers, `lpoColumns(kind)`, `lpoNextHtml`, `runLpoNextStep`, `invoiceLpoPeriod`, `renderLpoChips`; rewrite `visibleLpos` (kind, client, need-action, search incl. items and document numbers), `lpoKpis` (Ordered, Not invoiced yet - excluding closed and supply - and Need action), `renderLpoKpis` (Need action is the toggle `#lpoDueFilter`), `renderLpoList` (table), `lpoRowHtml(r, cols)`; remove `renderLpoClientFilter`, `lpoGroups`, `stepLpoDoc`. Wiring: row click opens, `[data-next]` runs the step, Enter on a focused row opens.

- [ ] **Step 3: Styles.** Delete the superseded `.lpo-row` grid, `.lpo-kpis`, `.lpo-group*`, `.lpo-filters`, `.lpo-due-toggle`, `.lpo-side-*`, `.lpo-sec*`, `.lpo-billing-*` rules; add the `lpo-top`, `lpo-fig*`, `lpo-table`, `lpo-meter`, `lpo-flag`, `lpo-none`, `lpo-sub`, `lpo-foot`, `lpo-inline-link` rules.

### Task 3: The LPO screen

**Files:** `templates/index.html` (`#lpoDocView` head), `static/lpo.js`, `static/app_styles.css`.

- [ ] **Step 1: Markup.** Remove `#btnLpoDocPrev` / `#btnLpoDocNext`; add `<span id="lpoDocActions">` before the divider; `#btnLpoDocBack` stays last (Waleed, 2026-09-12: the way out sits on the right).
- [ ] **Step 2: Header** `renderLpoDocHeader` renders kind/status chips and `lpoDocActionsHtml(lpo)` (primary next step; the other create actions where they apply; nothing to create on a closed LPO; Create invoice hidden once the value is fully invoiced) plus `lpoMenuHtml(lpo)` wired by `wireLpoMenu`.
- [ ] **Step 3: Panel** `renderLpoDocSide` = `lpoSummaryHtml` (line, notes, Edit, `lpoDocFigures`) + `lpoSignedHtml` + `lpoWarningsHtml` + `lpoItemsHtml` (materials) or `lpoBillingSection` (merged periods) + `lpoOrderedHtml` (contracts, folded) + `lpoLinkedHtml` + `lpoDetailsHtml` (folded). Signing mode restyled.
- [ ] **Step 4: Toolbar** Fit width (`#lpoZoomFit`), `lpoFitScale`, session zoom in `lpoState.zoom`, re-fit on window resize while in fit mode.
- [ ] **Step 5: Actions** `copy-signed` copies `ack_path`; `attachLpoFile` returns a promise that settles after the upload (or cancel) so the screen reloads with the new file; `openLpoForm(existing, {focus})` for "Set the end date".

### Task 4: Tests

- [ ] New `tests/lpo_screen.test.js` against the real `lpo.js` in a vm with a small DOM double: tab columns per kind; chip counts; Need action filter; search over items and invoice numbers; ordering (closed last); next-step button per row and nothing else clickable; header primary + secondary rules (closed, financially complete, rental, supply); More menu items; panel sections per kind; signed-copy states; billing merges periods and invoices; Previous/Next gone.
- [ ] Update `lpo_row_summary.test.js` (groups -> ordering/columns; the row carries ONE action at most; figures exclude supply), `lpo_audit_frontend.test.js` (create actions read from the header; estimates wording), `lpo_viewer_integrity.test.js` (panel region), `lpo_workflow.qa.cjs` (new selectors).
- [ ] Mutation checks on the new JS rules.

### Task 5: Verify, document, report

- [ ] Headless Chrome on real data, both companies, light/dark, 1366/1024; screenshots reviewed.
- [ ] Full JS suite, LPO Python suites, `smoke_test.py`, `smoke_quote.py`, `no_em_dash`.
- [ ] Docs 03 (database LPO helpers), 05 (markup), 07 (lpo.js), 08 (styles), 17 (what superseded), AGENT-BOARD (Done entry, release row).
