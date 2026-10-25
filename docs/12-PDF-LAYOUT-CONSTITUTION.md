# 12 - PDF LAYOUT CONSTITUTION (universal print rules)

The single, agreed-upon set of rules for how **every generated PDF** arranges its
table, footer, signature/stamp, and page number across **1, 2, or N pages**. All
item-table generators - `quote_generator`, `dn_generator`, `inv_generator` - MUST
comply; PO is the one documented exception (see the end). This is
the "constitution" the layout has lacked: when you touch any generator's
pagination, re-read this and keep it true.

Related: [[04-BACKEND-DOCGEN]] (the generators this governs), [[08-STYLES-TEMPLATES]]
(the print CSS these numbers mirror), [[13-PROFILES-BRANDS]] (the multi-company
profile subsystem behind the skins in §6.1), [[11-RUN-AND-MAINTAIN]] (how to measure).

> **2026-09-05 - this document is now executable.** For a month it was prose,
> and it could not enforce itself: §2 already said the engine constants and the
> print CSS are "a coupled pair," and the PO breached exactly that (A-84 planned
> 2 pages and printed 3, carrying "Page 1 of 2" footers on a three-sheet
> document). `tests/layout_planned_matches_rendered.test.py` now checks the one
> invariant every other rule reduces to - **booked pages == drawn pages** - for
> all four types on real PDFs. It found the same defect in the **invoice** and
> the **delivery note** the day it was written (see §4.6). When this document and
> that test disagree, the test is right.

> **Phase X.27** established the page-ROLE model below and fixed the long-standing
> "wasted band at the bottom of every middle page" bug (headerless pages were
> reserving the ~70–88mm page-1 header they never render, and were packed at the
> smallest capacity). Result on a 101-item quote: **17 pages → 10**, middle-page
> waste **~92mm → ~25mm**, with 1-page/2-page output unchanged.

---

## 1. The page-content box

A4 is 210 × 297 mm. Each type's `@page` margin is subtracted OUTSIDE the engine.
For the **Abraj** (default) skin: PO 10mm; quote/dn/inv 12mm. The
**Eshraqat** skin ships its own `@page` in `eshraqat_print.css` (**8.8mm 8.75mm
15.9mm**) - that geometry difference is what the 34mm chrome constant in §6.1
absorbs. Everything below is measured inside the content box, whose height is
`*_USABLE_MM` (**273mm** for quote/dn/inv). The page number lives at the very
bottom of this box; the header (page 1) at the very top.

## 2. The four page ROLES (the core model)

Every page is exactly one role and reserves ONLY what it actually renders:

| role | when | reserves at TOP | reserves at BOTTOM |
|---|---|---|---|
| **header page** | page 1 of a multi-page doc | the header block `*_TOP_MM` (~70/75/88mm) | page number |
| **body page** | middle pages (2 … N-1) | small margin `*_CONT_TOP_MM` (~2mm) | page number |
| **footer page** | last page of a multi-page doc | small margin `*_CONT_TOP_MM` | closing + terms/payment + signature/stamp |
| **single page** | the only page | header block `*_TOP_MM` | closing + footer |

**Golden rule:** a **headerless page never reserves the header block.** Only page 1
(and a single page) render the logo/title/meta header, so only they reserve
`*_TOP_MM`. Body and footer pages start their table at the top of the content box.
Violating this is exactly the bug X.27 fixed.

Area math - **one implementation since 2026-09-05**: `backend/doc_layout.py`
(`PageGeometry`, `role_for`). It was three copies of the shape below, and all
three had lost the same branch: the role selection knew `header`/`body`/`footer`
but not **`single`**, so a one-page document that reached the multi-page path got
the HEADERLESS area while still drawing its header (see §4.6). Each type still
supplies its OWN measured millimetres - §6 forbids sharing those - and still owns
its own packing, stretching and last-page trimming. Only the arithmetic is shared.
`chrome` per §6.1:
```
header_area = USABLE - TOP      - PAGENUM      - chrome   # page 1 of multi
body_area   = USABLE - CONT_TOP - PAGENUM      - chrome   # middle pages (the biggest)
footer_area = USABLE - CONT_TOP - last_reserve - chrome   # last page of multi
single_area = USABLE - TOP      - last_reserve - chrome   # the only page
```
`last_reserve = closing + footer_block + safe_gap` (the footer/signature zone).

The engine's `row_min_mm` / `table_area_mm` outputs are passed to the templates
as **CSS variables** (e.g. `--dn-row-mm`) so the rendered PDF matches the reserved
space - the engine constants and the print CSS are a **coupled pair**. Changing
CSS row heights, thead height, or `.esh-page` padding silently invalidates the
engine's mm math; re-measure.

## 3. Capacity & use of space per scenario

Each page packs rows to **its own capacity**, never the smallest-common capacity:
```
capacity = area - thead(HEADER_ROW_MM) - (grand/tax/total if last) - safety
```
No even-balancing - that is what spread rows thin and wasted every page's bottom.

- **1 page** - header + table + footer all fit. Rows sit at natural (or lightly
  stretched - see §4.3) height with `fill_table=False`. The footer is pinned to
  the bottom, so a short document shows a natural gap above the footer.
  **Expected, not a bug.**
- **2 pages** - page 1 fills to header capacity; page 2 (last) holds the remainder
  + footer.
- **N pages** - page 1 = header capacity; pages 2…N-1 = body capacity (the
  fullest, ~90%+); page N = remainder + footer.
- **Last-page trimming differs per type:** quote/dn **peel** trailing rows that
  exceed the footer-page cap, then a light **orphan guard** pulls one row down to
  avoid a lonely 1-row last page. INV instead runs a while-loop **"light
  balancing"** that keeps the last page at ≥ 1/3 of the previous page's item
  count (~inv_generator line 311).
- Filled (non-last) pages **stretch** rows up to `*_MAX_ROW_MM` to close small
  gaps; rows already at their floor (images - quote's floor is
  `Q_IMAGE_ROW_MIN_MM = 22`, Phase L.5 - or tall text) just mean *more rows per
  page*, which is the real space win.
- **Quote's anti-phantom-page guard:** on filled (non-last, non-autofit) pages,
  quote reserves `fill_mm = page_area - count*Q_ROW_OVERHEAD_MM - Q_PAGE_SAFETY_MM`
  (~line 452) - see §4.4. DN/INV have no overhead term (measured ~0).

### 3.1 Runtime overrides - check before "my constant change did nothing"

Many §6 constants are **defaults**, read through per-deployment override dicts
`config.QUOTE_LAYOUT` / `config.DN_LAYOUT` / `config.INV_LAYOUT` via helper
functions `_qov` / `_ov` / `_iov` (e.g. `INV_TOP_MM = _iov('top_mm', 88.0)`,
`Q_BASE_ROW_MM = _qov('min_row_mm', 8.0)`). The dicts are populated from settings
in `config.py` (~lines 781/793/870). Overridable keys include `min_row_mm`,
`max_row_mm`, `safe_gap_mm`, quote `image_row_min_mm`, and `autofit_rows`
(§3.2). **Editing a constant in a generator may be silently overridden at
runtime - check the override dicts first.**

> **The dicts are filtered.** `config._apply_user_settings._layout_dict` holds a
> WHITELIST, and a generator reading `_qov("x", …)` proves nothing on its own -
> if `x` is not whitelisted the setting can never arrive. That drifted in both
> directions until 2026-07-30: `max_row_mm` / `image_row_min_mm` were read but
> blocked, while `max_items` / `budget_mm` were whitelisted but read by nobody
> and shown as two live-looking controls in the settings UI. INV `top_mm` is
> read by the generator but deliberately still NOT whitelisted - it is the
> header reserve, and exposing it invites last-page overlap.
> `tests/spacing_settings_reach_renderer.test.py` now pins both halves
> together: every knob the panel offers must survive the whitelist AND move a
> generator constant.

### 3.2 Autofit mode (`autofit_rows`)

A per-document flag (`layout.autofit_rows` on the document, or `autofit_rows` in
the `*_LAYOUT` config dicts) that makes rows **hug their text** instead of the
classic fill model:

- `fill_table` stays **False on every page**; all stretching is disabled.
- Row-height estimation switches from the classic base-row clamp to **measured
  AF metrics**: quote `Q_AF_BASE/LINE = 6.5/4.4` mm, DN `5.8/4.3`, INV `5.8/4.3`.
- **Do NOT use the classic base-row clamp as the CSS row min in autofit**
  (Phase M.4) - autofit uses the AF constants.
- Guarded by `tests/doc_autofit_layout_backend.test.py`.

## 4. Anti-overlap invariants (NON-NEGOTIABLE)

1. **Table never overlaps the page number.** Every non-last page reserves
   `*_PAGENUM_MM = 8mm` at the bottom.
2. **Table never overlaps the footer or signature/stamp.** The last page reserves
   `last_reserve`; the grand-total (quote/dn) or - Abraj only - tax + total rows
   (inv) are reserved *inside* the last page's table (`extra_fixed`). Under
   Eshraqat, INV totals render **outside** the table (§6.1) and the in-table
   reservation is zero. The footer/signature block is pinned below all of it.
3. **The last/single page never FILLS.** `fill_table` is always False on
   last/single pages, for every type. Quote/DN **do** still stretch the
   last-page `row_min` (capped at `MAX_ROW` 22mm); **INV never stretches** -
   its last/single `row_min = max(INV_BASE_ROW_MM, tallest natural row)`.
   Stretching a 5-item invoice to 17mm+/row bloated the table into the
   payment/signature footer (INV Phase M item 12) - keep INV natural-height.
4. **A filled table renders slightly TALLER than the height set** (cell padding +
   collapsed borders + line leading). Quote reserves `Q_ROW_OVERHEAD_MM (2.6)` per
   row on filled pages; DN and INV were measured to render tight (~0) and do not.
   **Never delete a type's overhead reservation without re-measuring** - it is the
   guard against a stranded "Page 1 of N" phantom page.
5. **Column widths must keep summing to the per-type target** - the "NO. column
   takes the rest" rebalance targets are quote **96.4mm**, inv **95.4mm**; INV
   also scales UP stale under-target overrides (Phase M item 12). Under-target
   widths cause wrapping → taller real rows than estimated → last-page overlap.
6. **A height rule must match EXACTLY the rows the engine models (2026-09-05).**
   The engine reserves a *different* height for data rows, the thead, and the
   totals rows. The print CSS must reserve the same three, separately. Every
   instance of this being violated has produced the same failure: a document
   that plans N pages and prints N+1, whose own footers then say "of N".
   - **PO** - `.items-table th, td { height: var(--po-row-mm) }` matched the
     `<thead>` too, so a taller row setting silently grew the header while the
     paginator still booked `TABLE_HEADER_MM`. The thead now has its own
     `--po-head-mm`. (This is A-84.)
   - **INV** - `.i-table tbody tr { height: var(--i-row-mm) }` matched the tax
     and total rows, which live INSIDE `<tbody>`. A wrapping description pushed
     `row_min` to 69mm, so each of those rendered 69mm against the **8mm and
     10mm** the engine reserved, and the payment/signature block was pushed onto
     a page of its own that still printed "Page 1 of 1". They now take
     `--i-tax-mm` / `--i-total-mm`, published from `INV_TAX_ROW_MM` /
     `INV_TOTAL_ROW_MM`.
   - **DN** - same shape, `d-grand-row` inside `<tbody>`; now `--dn-grand-mm`
     from `DN_GRAND_ROW_MM`.
   - **QUOTE** was always correct, and shows the other valid answer: its
     grand-total row is rendered **outside** `<tbody>`, so the data-row rule
     cannot reach it. Either approach is fine. Sharing one height is not.

   **Note the direction of the damage.** Over-booking a row is not merely
   wasteful here: `row_min_mm` is published to the CSS as a *minimum height*, so
   an over-estimate BECOMES the rendered height and creates the overflow it was
   supposed to prevent. The estimate and the render are the same number in two
   places - which is why §4.7 exists.

7. **Every coupled number flows ONE way: engine → CSS variable.** A millimetre
   value that the engine reserves must never also be written as a literal in the
   print CSS; the template publishes the engine's constant as a `--var` and the
   CSS consumes it. Two copies of one number is the whole bug class above. The
   CSS default in `var(--x, 9mm)` is a fallback for a render that forgot to
   publish, not a second source of truth - keep it equal to the engine default.


## 5. Stamp & signature placement (signing.py)

- The signature (and optional stamp) is an absolutely-positioned overlay pinned to
  the **bottom of the last page's signature box**: `.sig-box{position:relative}` is
  the positioning context, `.sig-overlay` sits at its bottom. Baselines (~14mm
  stamp / ~6mm signature) are tuned to the shallow box - changing the box height in
  CSS misplaces them (see [[04-BACKEND-DOCGEN]] signing gotchas).
- Because `last_reserve` fences off the footer zone (which contains the signature
  box), **the table can never reach the signature/stamp.** That is the structural
  guarantee - the overlay is not "avoided" by luck, it's in reserved space.
  Shrinking closing/footer/safe-gap constants can put the table into the stamp.
- Slots per type (`signing.DOC_SLOTS`, ~line 255): PO / QUT / INV = `main`;
  DN = `supplier` only. (An old code comment shows a former
  `['recipient','supplier']` DN design - the current dict is `['supplier']`.)

## 6. Per-doc-type constants (the numbers that encode all of the above)

| | quote | dn | inv |
|---|---|---|---|
| `USABLE_MM` | 273 | 273 | 273 |
| header block `TOP_MM` | 70 | 75 | 88 |
| `CONT_TOP_MM` (headerless) | 2 | 2 | 2 |
| `HEADER_ROW_MM` (thead) | 9 | 9 | 9 |
| `PAGENUM_MM` | 8 | 8 | 8 |
| base / max row | 8 / 22 | 8 / 22 | 14 / 17 |
| last-page fixed-in-table | grand 11 | grand 11 (if priced) | tax 8 + total 10 (**Abraj only** - 0 under Eshraqat, §6.1) |
| footer block | terms→sign (≥24) | 2 sign boxes (28) | payment + sig (42; Eshraqat: closeout 58, §6.1) |
| `PAGE_SAFETY_MM` | 4 | 4 | 4 |
| render overhead / row | 2.6 | ~0 | ~0 |
| image row floor | `Q_IMAGE_ROW_MIN_MM` 22 | - | - |
| last page stretches `row_min`? | yes (cap 22, `fill_table` False) | yes (cap 22, `fill_table` False) | **no** (natural) |

These are per-type-tuned; do **not** copy a constant from one generator to another
(or between skins) without measuring - the print CSS (fonts, padding) differs per
type and skin. Also remember most of these are **overridable at runtime** (§3.1).

### 6.1 Multi-profile / brand skins (Phase Y.2) - the rules are SHARED, the geometry is measured per skin

This constitution is **universal across company profiles**. Both Abraj and Eshraqat
render through the **same** `build_layout` page-role engine and the same anti-overlap
invariants - a second company does **not** get a second pagination algorithm. What
differs is the **brand skin**: Eshraqat swaps `eshraqat/*_page.html` +
`eshraqat_print.css` in each generator's `if config.ACTIVE_PROFILE == "eshraqat"`
branch and merges `eshraqat_render.common_context()` (a tiny shared module supplying
identity/brand/logo data-URIs/page_count; it raises `RuntimeError` if the active
profile isn't eshraqat). `config.ACTIVE_PROFILE` is read **at call time** inside the
chrome/footer helpers, so switching profiles needs no generator reload at runtime.
The profile subsystem itself is documented in [[13-PROFILES-BRANDS]].

A brand skin has its **own physical geometry**, so the constants above (which encode
Abraj's rendered CSS) do not describe it. The Eshraqat `.esh-page` / `.esh-table` skin
adds, on **every** page, chrome the Abraj-tuned areas never modelled: ~20 mm `.esh-page`
bottom padding (footer/page-number band) **plus** a taller table header than
`HEADER_ROW_MM` (and its own `@page` margins, §1). Unaccounted for, that chrome pushes
each force-filled table past the printable box → the table spills onto a near-empty
continuation page (the classic "every other page is 3 rows" symptom).

The fix keeps the shared engine and subtracts a **single measured per-skin reserve**
from *every* page area - implemented as `_q_page_chrome_mm` / `_dn_page_chrome_mm` /
`_page_chrome_mm` (inv), each returning:

```
chrome = *_ESH_PAGE_CHROME_MM if config.ACTIVE_PROFILE == "eshraqat" else 0.0
```
(see the area math in §2 - `chrome` is subtracted from all four areas).

| skin | `*_ESH_PAGE_CHROME_MM` (quote/dn/inv) |
|---|---|
| Abraj (default) | 0 - areas byte-identical to before |
| Eshraqat | **34** (measured: ~20 mm bottom padding + ~14 mm taller thead) |

**Known per-type Eshraqat divergences beyond the skin swap:**
- **INV last-page composition changes:** the payment block (`INV_PAYMENT_MM = 42`)
  is replaced by the closing block `INV_ESH_CLOSEOUT_MM = 58` (~inv_generator
  line 225), and `last_fixed_in_table = 0.0` because tax + total render **outside**
  the table in the eshraqat closeout. §6's "tax 8 + total 10 in-table" is Abraj-only.
- **Quote strips the IMAGE column before layout** (`_strip_image_columns`,
  ~quote_generator line 557), so the `Q_IMAGE_ROW_MIN_MM = 22` floor never
  triggers under that skin - row-height estimation inputs differ per skin.

**Rules for adding a future skin (or retuning Eshraqat):**
- Never fork `build_layout`. Add a profile-guarded reserve subtracted from ALL
  four page areas; keep Abraj at 0 so its output is byte-identical.
- **Measure, don't guess** the reserve: render a long (~90-item) doc with wrapping rows
  and confirm (a) no near-empty continuation page and (b) the last page's totals /
  signature / footer never overlap. `tests/eshraqat_pagination_solidity.test.py` is the
  guard: it renders 90 wrapping-row quote/dn/inv docs under a temp eshraqat profile and
  asserts no non-last page falls below 40% of median line density, every page shows
  "Page X of Y", and page count ≤ 16. **Keep it green** after any pagination change
  (plus `smoke_test.py` and `smoke_quote.py`).
- The reserve is per **doc type** (constant lives in each generator) because each type's
  closing block differs. The three values happen to match today (34) because the
  *page* chrome (padding + thead) is shared CSS - but they are per-type **on
  purpose**: retune individually, never globally.

## 7. The exceptions

- **PO is MEASURED, not estimated (2026-10-11).** `pdf_generator.plan_po` lays
  the PO out in the printing browser (`backend/pdf_measure.py`), plans pages on
  the real row heights, header, signature/stamp block and page number, then
  checks the finished layout and the printed page count before it is filed. The
  constants below are now the FALLBACK (no browser) and the editor's live hint.
  Two rules the measured planner added: every page keeps the **page number**
  clear, and the signature page carries **at least two lines** (the page before
  it is refilled instead). See [[04-BACKEND-DOCGEN]] for the detail.
  Quote, DN and invoice still estimate; the same measuring can be applied to them.
- **PO** (`pdf_generator.paginate_po`) **does** paginate one document as of
  2026-07-29 - it used to *split* the item list into multiple PO documents, and
  this section used to tell you not to apply the page-role model to it. That is
  now exactly what it does: page 1 carries the header block, continuation pages
  start flush at the top, and the **last** page reserves the signature band.
  Geometry is **measured** and lives in `pdf_generator` (`PO_USABLE_MM`,
  `PO_HEADER_MM`, `PO_CONT_TOP_MM`, + `config.PO_SIGNATURE_MM`), with a separate
  eshraqat set (`PO_ESH_*`) exactly as §6.1 prescribes. `config.TABLE_BUDGET_MM`
  and `config.MAX_ITEMS_PER_PO` are **retired**.
  - **The eshraqat PO skin is the one place a `*_ESH_USABLE_MM` differs from its
    siblings.** Re-measured 2026-08-09 after the Arabic/RTL redesign: that skin
    prints **no footer band**, so `.esh-po-document .esh-page` carries 10mm of
    bottom padding instead of 20mm and `PO_ESH_USABLE_MM` is **260** (quote/dn/inv
    still model 20mm via their `*_ESH_PAGE_CHROME_MM = 34`). Its closing zone is
    pinned to the bottom of the last page by CSS (`margin-top: auto`), so
    `PO_ESH_CLOSEOUT_MM = 46` is what keeps the table clear of it - measured
    worst case leaves >100mm of headroom.
  - The shared `_CHARS_PER_LINE` row model is calibrated for the **Abraj** skin's
    12pt table and is NOT skin-aware. Under eshraqat's 9pt table it over-books
    height by roughly a third, so eshraqat POs break to a new page earlier than
    they need to. Safe direction, deliberately left alone - see the gotcha in
    [[04-BACKEND-DOCGEN]] before "fixing" it.
  - It still does **not** use `build_layout`: the row engine is the simpler
    `estimate_row_height_mm` + greedy packing. That part remains PO-specific.
  - **Do not replace the two-pass packer with a fixed-point loop.** Only the last
    page reserves the signature, so guessing the page count and re-packing
    oscillates and silently overflows the signature - see the gotcha in
    [[04-BACKEND-DOCGEN]] and `tests/po_pagination_backend.test.py`.
  - Eshraqat PO numbers zero-pad to width 4; there is now only ever **one**
    number per PO, so nothing consumes the successor numbers any more.
- ~~**Letters** (`ltr_generator`)~~ - the Official Letter was retired on
  2026-08-27 (never used). It was the other single-flow document; PO is now the
  only exception to this constitution. PO still does the eshraqat
  template/CSS/`common_context` swap - see [[13-PROFILES-BRANDS]].

## 8. Protocol - when you change any pagination

1. Re-read this constitution (and [[04-BACKEND-DOCGEN]]).
2. **Check the runtime overrides** (§3.1) - a settings-driven `*_LAYOUT` dict may
   be masking the constant you're editing.
3. **Measure, don't eyeball.** Render at least: 1 item, the boundary count that
   just fits one page, a 2-page count, and a 40–100-item doc. Assert (a) no page's
   last data row exceeds ~285mm (no overflow / phantom page), and (b) middle pages
   are ≥ ~90% full.
4. Verify the **last page visually**: footer + signature/stamp + page number all
   clear of the table.
5. Run the guards. **`tests/layout_planned_matches_rendered.test.py` is the one
   that matters** - it renders real PDFs for all four types and asserts the page
   count the engine BOOKED equals the count the browser DREW. Every invariant in
   §4 reduces to that, so a violation shows up there without the test needing to
   know which rule was broken. It is slower than the rest of the suite because
   it drives real Chromium; run it anyway.
   Then: `smoke_test.py`, `smoke_quote.py`,
   `tests/eshraqat_pagination_solidity.test.py`, and
   `tests/doc_autofit_layout_backend.test.py` if row estimation changed.
6. Update this doc **and** [[04-BACKEND-DOCGEN]] in the same change (per
   [[11-RUN-AND-MAINTAIN]]).
