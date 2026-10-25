# DN Costing + Invoice-from-DNs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enter market-price costs once in the DN editor, then insert a priced DN as a single invoice line (Rate = DN total, cost inherited) whose DN pages are appended to the invoice PDF, and which marks the DN's POs invoiced.

**Architecture:** DN costs reuse the existing `save_doc_costs(kind="dn")` / `load_doc_costing(kind="dn")` storage (`_DOC_COST_SPEC` already includes `dn`). Invoice lines tagged `dn_ref` snapshot the DN's total/cost at insertion. The invoice generator appends each `dn_ref` DN's page HTML into the SAME headless-render pass (invoice and DN share identical A4 @page geometry - 12mm margin, 186×273mm box - and under the eshraqat skin share one stylesheet). On save, each `dn_ref` runs the existing `link_invoice_from_dn`.

**Tech Stack:** Python (SQLite, Jinja2), vanilla JS (pywebview bridge), headless Chrome/Edge HTML→PDF. No new dependency.

**Reference:** `docs/superpowers/specs/2026-07-18-dn-costs-invoice-from-dns-design.md`. Design proof: `INVOICES/Materials/TDC/2026/JUL/18-07-2026/INV-MT-TDC-017.pdf`.

**Verify caveat:** Frontend can't be driven headlessly; every phase ends with backend tests + `python smoke_test.py`, and the generator phase renders a real combined PDF and inspects it with `fitz`. Manual UI confirmation is called out where it's the only check.

---

## Phase 1 - DN cost summary (backend)

DN cost STORAGE already works (`save_doc_costs(kind="dn")` writes per-item `cost` into the DN's `rows_json`; `load_doc_costing(kind="dn")` reads it). We only need a summary read for invoice-line inheritance.

### Task 1: `dn_cost_summary(dn_no)` in `backend/database.py`

**Files:**
- Modify: `backend/database.py` (add near `pos_awaiting_invoice`, after the po_fulfillment block)
- Test: `tests/dn_cost_summary_backend.test.py` (create)

- [ ] **Step 1: Write the failing test**

```python
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config, database, migrations

DN = "DN-__COSTSUM__-001"

def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM dns WHERE dn_no=?", (DN,))
        c.execute("DELETE FROM doc_cost_meta WHERE kind='dn' AND doc_no=?", (DN,))

def main():
    database.init_db(); migrations.run_all(); cleanup()
    try:
        database.save_dn_record(dn_no=DN, dn_date="18/7/2026", year=2026, month=7,
            client_name="C", pdf_path=Path("x.pdf"), include_prices=True,
            rows=[{"item":"A","qty":"20","rate":"0.75"},   # sell 15.00
                  {"item":"B","qty":"10","rate":"2.00"}],  # sell 20.00
            source_quote_no=None)
        database.save_doc_costs("dn", DN, costs={"0":"0.55","1":"1.20"}, currency="USD")
        s = database.dn_cost_summary(DN)
        assert s["ok"] is True, s
        assert abs(s["sell_total"] - 35.00) < 1e-6, s      # 15 + 20
        assert abs(s["cost_total"] - 23.00) < 1e-6, s      # 20*.55 + 10*1.20 = 11 + 12
        assert s["currency"] == "USD", s
        assert s["has_prices"] is True, s
        assert database.dn_cost_summary("DN-__NOPE__")["ok"] is False
        print("dn_cost_summary_backend: passed")
    finally:
        cleanup()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run to verify it fails** - `python tests/dn_cost_summary_backend.test.py` → AttributeError: `dn_cost_summary`. (Confirm the exact `save_dn_record` signature first - read `backend/database.py` `def save_dn_record`; adjust kwargs if they differ.)

- [ ] **Step 3: Implement**

```python
def dn_cost_summary(dn_no: str) -> dict[str, Any]:
    """Sell total (Σ qty·rate) and cost total (Σ qty·cost) for a DN, currency-aware.
    Feeds the invoice DN-line inheritance. sell_total is 0 when the DN has no prices."""
    dn_no = (dn_no or "").strip()
    if not dn_no:
        return {"ok": False, "error": "no dn_no"}
    with _connect() as conn:
        row = conn.execute(
            "SELECT rows_json, include_prices FROM dns WHERE dn_no=?", (dn_no,)
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"dn not found: {dn_no}"}
    try:
        items = json.loads(row["rows_json"] or "[]")
    except (TypeError, ValueError):
        items = []
    sell_total = 0.0
    cost_total = 0.0
    for it in items:
        if not isinstance(it, dict):
            continue
        qty = _num(it.get("cost_qty")) if it.get("cost_qty") not in (None, "") else _num(it.get("qty"))
        rate = _num(it.get("cost_sell")) if it.get("cost_sell") not in (None, "") else _num(it.get("rate"))
        cost = _num(it.get("cost"))
        sell_total += (qty or 0) * (rate or 0)
        cost_total += (qty or 0) * (cost or 0)
    return {
        "ok": True, "dn_no": dn_no,
        "sell_total": round(sell_total, 2),
        "cost_total": round(cost_total, 2),
        "currency": get_cost_currency("dn", dn_no),
        "iqd_per_usd": doc_iqd_per_usd("dn", dn_no),
        "has_prices": bool(row["include_prices"]),
    }
```

- [ ] **Step 4: Run** - `python tests/dn_cost_summary_backend.test.py` → `passed`.
- [ ] **Step 5: Commit** - `git add backend/database.py tests/dn_cost_summary_backend.test.py && git commit -m "feat(dn): dn_cost_summary - sell/cost totals for invoice-line inheritance"`

### Task 2: Bridge methods in `backend/api.py`

**Files:** Modify `backend/api.py` (near `save_doc_costs`/`load_doc_costing` wrappers).

- [ ] **Step 1:** Add wrappers (DN costing UI needs load/save; invoice insertion needs the summary + a priced-DN suggestion list):

```python
def dn_cost_summary(self, dn_no: str) -> dict:
    return _bridge_json_safe(database.dn_cost_summary(dn_no))

def load_dn_costing(self, dn_no: str) -> dict | None:
    return _bridge_json_safe(database.load_doc_costing("dn", dn_no))

def save_dn_costs(self, dn_no: str, costs=None, qtys=None, sells=None, currency=None) -> dict:
    return database.save_doc_costs("dn", dn_no, costs=costs, qtys=qtys, sells=sells, currency=currency)

def list_invoiceable_dns(self, limit: int = 300) -> list[dict]:
    """Priced DNs for the invoice item-field picker, each with sell/cost totals."""
    return _bridge_json_safe(database.list_invoiceable_dns(int(limit)))
```

- [ ] **Step 2:** Add `list_invoiceable_dns(limit)` to `backend/database.py` - reuse `list_dn_candidates` shape but keep only priced DNs and attach `dn_cost_summary` fields:

```python
def list_invoiceable_dns(limit: int = 300) -> list[dict[str, Any]]:
    out = []
    for c in list_dn_candidates(limit):
        s = dn_cost_summary(c["no"])
        if not s.get("ok") or not s.get("has_prices") or s["sell_total"] <= 0:
            continue
        out.append({**c, "sell_total": s["sell_total"], "cost_total": s["cost_total"],
                    "currency": s["currency"]})
    return out
```

- [ ] **Step 3: Test** - extend `tests/dn_cost_summary_backend.test.py`: assert `list_invoiceable_dns()` includes `DN` with `sell_total==35.0`, and excludes an unpriced DN (seed one with `include_prices=False`). Run it.
- [ ] **Step 4: Commit** - `git commit -am "feat(dn): bridge - dn cost load/save + invoiceable-DN picker list"`

---

## Phase 2 - DN Costs panel in the DN editor (frontend)

**Files:** Modify `static/dn.js` (toolbar + panel), `static/app_styles.css` (panel styles if needed), `templates/index.html` (only if a container is required - prefer building the panel in JS like the classification popup).

The panel mirrors the costing cost-grid but inline. Reuse the existing modal/backdrop pattern from `static/costing.js` / the `.cotag-*` popup.

- [ ] **Step 1:** In `static/dn.js`, add a **Costs** button to the DN editor toolbar action group (find where DN toolbar buttons are wired - same group as Save/Sign). Follow the existing button pattern.
- [ ] **Step 2:** Add `openDnCosts()`: `await api().load_dn_costing(dnNo)` (falls back to the loaded DN rows), render a modal listing each item row (name + qty + a **cost** input), a USD/IQD switch (reuse costing.js currency switch markup), and a live **Total cost** / **Profit** (sell − cost) footer. Only meaningful for a SAVED DN (needs `dn_no`); if the DN is unsaved, toast "Save the delivery note first."
- [ ] **Step 3:** On the panel's Save: gather per-row costs into `{index: value}`, call `api().save_dn_costs(dnNo, costs, qtys, sells, currency)`, toast, close.
- [ ] **Step 4: Manual check (only check available):** run the app, open a saved DN, click **Costs**, enter costs, save; reopen the panel → values persist. Confirm `load_dn_costing` returns them.
- [ ] **Step 5: Node regression:** add `tests/dn_costs_panel.test.js` in the existing vm-harness style (see `tests/dashboard_todo_popup.test.js`) asserting: the DN toolbar exposes a Costs button, and `openDnCosts` builds rows with cost inputs + a total. Run `node tests/dn_costs_panel.test.js`.
- [ ] **Step 6: Commit** - `git commit -am "feat(dn): Costs panel in the DN editor (per-item market price, summed)"`

---

## Phase 3 - DN as an invoice line (frontend + inheritance)

**Files:** Modify `static/inv.js` (item-field DN suggestions + row insertion + payload), and confirm the invoice row schema carries `dn_ref`.

- [ ] **Step 1:** In `static/inv.js`, find the invoice item-grid autocomplete (mirrors `app.js` `onNameInput`/`pickSuggestion`). Add a DN-suggestion mode: when the item field query looks like a DN (or always, as a secondary section), call `api().list_invoiceable_dns()` and offer matching DNs (number + summary).
- [ ] **Step 2:** On picking a DN, fill that row: `item = dn.no`, `unit = "EA"`, `qty = 1`, `rate = dn.sell_total`, and set a hidden `dn_ref = dn.no` + `cost = dn.cost_total / (qty=1)` (per-unit cost = cost_total, since qty 1) so the existing costing reads it. Mark the row visually (e.g. a small "DN" chip) and make item/unit/qty/rate read-only for `dn_ref` rows (they're a snapshot).
- [ ] **Step 3:** Ensure `buildInvPayload()` includes `dn_ref` and `cost` on the row so `save_inv` persists them in `rows_json`. (No schema migration - `rows_json` is freeform per row.)
- [ ] **Step 4: Node regression:** `tests/inv_dn_line.test.js` (vm harness): simulate picking a DN suggestion → the row gets `dn_ref`, `unit==="EA"`, `qty===1`, `rate===sell_total`, `cost===cost_total`; and `buildInvPayload` carries `dn_ref`. Run it.
- [ ] **Step 5: Manual check:** in the app, new invoice → item field → pick a priced DN → line fills with its total; add "Transportation fees" manually; totals sum. Matches the reference.
- [ ] **Step 6: Commit** - `git commit -am "feat(inv): insert a priced DN as an invoice line (rate=DN total, cost inherited)"`

---

## Phase 4 - Auto-close the PO loop on invoice save (backend)

**Files:** Modify `backend/api.py` `save_inv` (line ~874).

- [ ] **Step 1: Test** - `tests/inv_dn_autolink_backend.test.py`: seed a PO with a client item, mark it delivered by a DN (`set_po_fulfillment(po,[0],"dn",DN,True)`), save an invoice whose `rows_json` has a `dn_ref=DN` line, then assert `pos_awaiting_invoice` no longer contains the PO and `po_fulfillment_detail(po)["paid"]` includes index 0. (Drive `save_inv` via `Api()` with a minimal inv dict; confirm the `save_inv` payload shape by reading it first.)

- [ ] **Step 2: Implement** - after the invoice row is saved (post `render_inv_pdf` / DB write) in `save_inv`, add:

```python
# Auto-close the PO loop: each DN line marks that DN's delivered PO items invoiced.
inv_no = inv.get("inv_no") or inv.get("no")
for row in (inv.get("items") or []):
    dn_ref = str((row or {}).get("dn_ref") or "").strip()
    if dn_ref and inv_no:
        try:
            database.link_invoice_from_dn(dn_ref, inv_no)
        except Exception:
            pass   # billing must not fail if the fulfillment link can't be written
```

Place this where `inv_no` and the saved items are in scope (read the current `save_inv` body to position it after the successful DB save). `link_invoice_from_dn` is idempotent (conflict-guarded), so re-saving an invoice is safe.

- [ ] **Step 3: Run** - `python tests/inv_dn_autolink_backend.test.py` → passes. Also run `python tests/po_deliver_invoice_backend.test.py` (unaffected) and `python tests/po_invoice_payment_semantics_backend.test.py`.
- [ ] **Step 4: Commit** - `git commit -am "feat(inv): saving an invoice with DN lines marks their POs invoiced (wire link_invoice_from_dn)"`

---

## Phase 5 - Append DN pages to the invoice PDF (backend generator - HARDEST)

**Files:** Modify `backend/inv_generator.py` (`render_inv_html` + a new helper), read `templates/inv_page.html` / `templates/dn_page.html` / `templates/eshraqat/*` to confirm the page-div classes.

Invoice and DN share the same `@page` (12mm) and page box, so their pages can live in one HTML document. Under **eshraqat** both use `eshraqat_print.css` (one stylesheet) - trivial. Under **abraj**, include BOTH `inv_styles.css` and `dn_styles.css` in the combined `<head>`; the page content is scoped by distinct classes (`.inv-page` vs `.dn-page`), and `@page`/base rules are compatible.

- [ ] **Step 1: Confirm structure** - read `render_inv_html` end (how it returns the full HTML) and `render_dn_html`; note the wrapper: `<html><head><style>…</style></head><body> …page divs… </body></html>`. Identify the exact page-div wrapper class for inv and dn in both skins.

- [ ] **Step 2: Helper** - add to `backend/inv_generator.py`:

```python
import re
from . import dn_generator

def _body_and_style(html: str) -> tuple[str, str]:
    """Split a rendered doc into (inner-<style>…</style> blocks, <body> inner HTML)."""
    styles = "\n".join(re.findall(r"<style[^>]*>.*?</style>", html, re.S))
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    return styles, (m.group(1) if m else "")

def _append_dn_pages(invoice_html: str, dn_docs: list[dict]) -> str:
    """Splice each DN's page HTML after the invoice pages in ONE document."""
    if not dn_docs:
        return invoice_html
    extra_styles, extra_body = [], []
    seen_style = set()
    for dn in dn_docs:
        dn_html = dn_generator.render_dn_html(dn, inline_css=True)
        st, body = _body_and_style(dn_html)
        if st and st not in seen_style:      # include each distinct stylesheet once
            seen_style.add(st); extra_styles.append(st)
        extra_body.append(f'<div style="break-before: page;"></div>{body}')
    out = invoice_html
    if extra_styles:
        out = out.replace("</head>", "\n".join(extra_styles) + "</head>", 1)
    out = out.replace("</body>", "".join(extra_body) + "</body>", 1)
    return out
```

- [ ] **Step 3: Wire** - in `render_inv_html`, at the end, gather DN docs for `dn_ref` rows and append. The invoice `inv` dict must carry the resolved DN documents; add a resolver so the generator can load each `dn_ref`'s full DN dict (via `database.load_dn`). Because generators avoid importing `database` heavy paths, pass the resolved DN docs INTO the generator from `Api.save_inv`/`preview_inv` instead: add `inv["_dn_docs"] = [load_dn(ref) …]` before calling `render_inv_pdf`. In `render_inv_html`, `return _append_dn_pages(html, inv.get("_dn_docs") or [])`.

- [ ] **Step 4: Api wiring** - in `Api.preview_inv` and `Api.save_inv`, before `render_inv_pdf`, build `inv["_dn_docs"]`:

```python
dn_refs = [str((r or {}).get("dn_ref") or "").strip() for r in (inv.get("items") or [])]
inv["_dn_docs"] = [d for ref in dn_refs if ref for d in [database.load_dn(ref)] if d]
```

- [ ] **Step 5: Test (render + inspect)** - `tests/inv_appended_dn_pages_backend.test.py`: seed a priced DN and an invoice with a `dn_ref` line; render to a temp PDF via `inv_generator.render_inv_pdf(inv_with__dn_docs, out)`; open with `fitz`; assert `page_count == invoice_pages + dn_pages` and that a later page's text contains the DN number. Run it. (Requires Chrome/Edge; same as smoke_test.)

- [ ] **Step 6: Both skins** - run `python tests/eshraqat_invoice_rendering.test.py` after clearing `.test-work`; add an eshraqat variant assertion if practical. Confirm no `@page`/style collision visually by rendering one abraj + one eshraqat combined PDF and eyeballing page 1 (invoice) + page 2 (DN).

- [ ] **Step 7: Commit** - `git commit -am "feat(inv): append linked DN pages into the invoice PDF (one render pass, both skins)"`

---

## Phase 6 - Docs + full verification

- [ ] **Step 1: Docs** - update `docs/04-BACKEND-DOCGEN.md` (the append helper + `_dn_docs` contract), `docs/03-BACKEND-DATA.md` (`dn_cost_summary`, `list_invoiceable_dns`), `docs/02-BACKEND-CORE.md` (new Api methods + `save_inv` auto-link + `_dn_docs` build), `docs/06-FRONTEND-EDITORS.md` (inv DN-line insertion), `docs/07-FRONTEND-SUPPORT.md` (DN Costs panel), `docs/11-RUN-AND-MAINTAIN.md` (new tests), and the `docs/01-ARCHITECTURE.md` Impact Map (DN-line ↔ inv generator ↔ fulfillment).
- [ ] **Step 2: Full backend suite** (clear scratch first): `rm -rf .test-work` then run the new tests + `po_deliver_invoice_backend`, `po_invoice_payment_semantics_backend`, `dn_make_invoice_workflow_backend`, `python smoke_test.py`, `python smoke_quote.py` (visual).
- [ ] **Step 3: Node suite:** `node tests/dn_costs_panel.test.js tests/inv_dn_line.test.js` + existing `invoice_paid_panel`, `po_quote_link_panel`.
- [ ] **Step 4: Manual E2E:** reproduce the reference - cost two DNs, make one invoice inserting both DNs + a Transportation line, verify totals, verify the DN pages are appended in the PDF, and verify both POs left the Deliver & Invoice To-Do.
- [ ] **Step 5: Commit docs** - `git commit -am "docs: DN costing + invoice-from-DNs (map update)"`

---

## Self-review notes (coverage vs spec)

- Spec Part 1 (DN costs) → Phase 1–2. Part 2 (DN-as-line + inherit + snapshot) → Phase 3. Part 2 (auto-close PO) → Phase 4. Part 3 (appended pages, approach A) → Phase 5. Edge cases: unpriced DN excluded (Phase 1 Task 2 filter + Phase 3 picker); snapshot numbers (Phase 3 Step 2 read-only row); DN deleted → appended page skipped (Phase 5 `load_dn` returns None → filtered); cost currency via `doc_iqd_per_usd` (Phase 1 summary + existing costing).
- **Confirm-at-execution** (read the real code first; do not assume): exact `save_dn_record` and `save_inv`/`preview_inv` payload shapes; the invoice item-grid autocomplete hook names in `inv.js`; the page-div wrapper classes in the four templates. These are integration points, not open questions.
- Risk hotspot: Phase 5 style/`@page` interaction across skins - the fitz render test + manual eyeball are the gate; if abraj combined rendering collides, fall back to namespacing the DN body under a wrapper class or (last resort) revisit approach B.
