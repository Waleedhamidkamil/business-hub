# 11 - Run & Maintain

How to run the app, run the smoke tests, and - most importantly - **keep this map in sync with the code on every change**.
**Covers:** run/build steps, dependencies, smoke tests, the map-update protocol, graphify status.

See also: [[01-ARCHITECTURE]] · [[README]]

---

## Two agents share this repo  ⭐
Claude Code and Codex both work here, sometimes at once, on the same files and
the same **live** database. They have no channel to each other, so
**[`AGENT-BOARD.md`](../AGENT-BOARD.md)** is it: who is holding what, who holds
the test lock, what was done in the last two days, and the rules that stop one
session treading on the other.

Each rule on that board is there because it broke something on 2026-08-19/20 -
a `git add -A` that swept the other session's uncommitted work into a commit
and split one change across two, twice-crashed git leaving a stale
`.git/index.lock`, and two agents independently building the same feature.

**The test lock matters most.** `tests/*.test.py` write to
`data/shared/library.db` - Waleed's real catalogue. Two suites at once
interleave writes and collide on sentinel names, and seven test files have
already leaked items onto his Master Data screen by cleaning up their catalog
rows but not the `master_items` record a save mirrors into.

## Run the app

It is a **Windows desktop app** (pywebview + Edge WebView2), not a web server.

```bash
# from the project root:
pip install -r requirements.txt      # pywebview, jinja2, openpyxl, pypdf
python app.py                        # opens the desktop window (maximized)
```

- **Entry point:** [`app.py`](../app.py) → `main()` runs `database.init_db()`, `migrations.run_all()`, then `webview.create_window(url=templates/index.html, js_api=Api())`. `webview.start()` uses non-private persistent storage at `%LOCALAPPDATA%/BusinessHub/WebView2`; do not restore the default temporary private profile, which can leave a locked `BrowserMetrics` file and emit `Failed to delete user data folder` during shutdown.
- **Debug/devtools:** set `ABRAJ_DEBUG=1` in the environment before launching to re-enable WebView2 devtools (off by default). On Windows PowerShell: `$env:ABRAJ_DEBUG="1"; python app.py`.
- **Vendored front-end libraries** live in `static/lib/`: `apexcharts.min.js`, `lottie.min.js`, and (2026-07-31) **`pdf.min.js` + `pdf.worker.min.js`** for the client-LPO viewer. pdf.js must stay the **classic UMD build, not the `.mjs` one** - the app loads `index.html` over `file://`, where Chromium refuses ES-module scripts. If the worker can't load over `file://`, pdf.js silently falls back to an in-thread "fake worker": slower on a big scan, still correct.
- **PDF rendering** needs Chrome or Edge installed (found via the Windows registry in [`backend/pdf_engine.py`](../backend/pdf_engine.py)) - nothing in Python rasterises or lays out a page. **`pypdf` (2026-07-30) is the one Python PDF dependency** and is used for exactly one job: merging a client's PDF signed copy into a finished invoice (`inv_generator._merge_signed_pdfs`). Pure Python, no native binary.
- No `.vbs`/`.bat` launcher is checked in; `app.py` hides its own console when double-launched.

## Databases are WAL - checkpoint before a data commit (2026-10-22)

Every SQLite file opens in WAL mode (`backend/sqlite_policy.py`): the newest
writes can sit in `po_tool.db-wal` until a checkpoint. The `-wal`/`-shm` files
are git-ignored, so before a "data:" commit fold them into the `.db`:

```bash
python -c "import sqlite3,glob; [sqlite3.connect(p).execute('PRAGMA wal_checkpoint(TRUNCATE)').close() for p in glob.glob('data/profiles/*/po_tool.db')+['data/shared/library.db']]"
```

Never copy a live `.db` by hand for a backup - use the app's backup (it takes a
`sqlite_policy.snapshot`), or the command above first.

## Smoke tests (no UI)

These run the backend offline and are the fast "did I break generation" check. **Keep them.**

> ### ⚠ Backend tests run against the LIVE profile database
>
> There is **no test-DB isolation** in this project. `backend.config.DB_PATH` resolves to the active profile's real `data/profiles/<id>/po_tool.db`, and setting `PO_TOOL_DATA_DIR` (or any other env var) does **nothing** - `config` reads the profile registry, not the environment. Every `*.test.py` writes into the user's real data.
>
> Rules for any new backend test, without exception:
>
> 1. **NEVER write an unqualified `DELETE FROM <table>`.** This wiped 17 live invoices + 16 `doc_cost_meta` rows on 2026-07-29. (They were recovered from the DB's free pages, because SQLite's no-WHERE delete truncates onto the freelist without clearing the payloads - but that is luck, not a safety net, and it only worked because nothing had reused those pages yet.)
> 2. Give every row a **sentinel document number** (`INV-__MYTEST__-001`) and delete only those: `DELETE FROM invoices WHERE inv_no IN (?, …)`.
> 3. Put rows in a **far-future month** (e.g. 2099-01) so they can never surface in a real report even mid-run.
> 4. Assert aggregates as a **delta against a baseline**, never an absolute total - the live DB has real rows in it.
> 5. Clean up in `tearDown` **and** `tearDownModule`, so a mid-run failure still leaves the DB clean.
>
> `tests/inv_sell_currency_backend.test.py` is the reference example. Existing tests follow the same sentinel convention (`__SRCDN__`, `__DELETE_DOC__`, …).

```bash
python smoke_test.py     # DB init + PO numbering + HTML (→ data/_smoke.html) + PDF render (→ data/_smoke.pdf) + item-memory (remember_item/suggest_items)
python smoke_quote.py    # renders one quotation of each of the 4 quote types (materials, equipment, manpower, service) → data/_q_*.pdf
python tests/master_data_backend.test.py   # Item Master: catalog fold, identity, safe write fan-out, and paste resolver (wrapped Excel names, ambiguous variants, typo/plural suggestions that never auto-fill)
python tests/master_items_build.test.py    # the item RECORD, built: unit meaning (كيلو == KG), the language join, one record per product, sources recorded, a dry run that writes nothing
python tests/master_items_stay_current.test.py  # a document save keeps the record in step: a brand-new item appears at once, a second catalog updates ONE record, an Arabic PO line joins by its paired name, disagreeing units stay two records, a silent save never blanks what is known
python tests/master_items_screen.test.py  # the Master Data screen's API: the fields the screen shows, money keeping its own currency, one search box over every field, patch-not-replace editing, numbering that survives a rebuild, the "still missing" filters
python tests/master_items_two_languages.test.py  # description and unit PER LANGUAGE: which slot text goes in (the SCRIPT decides, never the document), the fallback, a save that learns without overwriting, variants staying apart, the migration on its own scratch DB, and the backfill that recovers what the old single column dropped
python tests/unit_in_both_languages.test.py  # every item's unit in BOTH languages: the counterpart word per meaning, a typed unit never rewritten, an item with NO unit never given one, an unknown unit reported rather than guessed, and every word UNIT_WORDS writes reading back as the meaning it came from
python tests/po_item_memory_backend.test.py  # PO items remember their picture: round-trip through items.data_json + the content store, no autocomplete fork, a blank picture cell never erases one
node tests/inv_autofill.test.js           # focused frontend regression: invoice autofill matches DNs by NUMBER only (item names used to drown the list) and never replays a raw cost in an unknown currency
python tests/image_search_backend.test.py  # the app's ONLY network call: the Bing tile parser, the Arabic head-noun/ladder direction, the TRANSLATED second pass and which of the two wins, the user's own wording, the two-band partial score, the relevance floor that rejects "Flat Iron at Night" for "angle iron", and the download guards (scheme, content-type, byte cap). Live tests skip when offline; the provider mock MUST patch `bing` or the unit tests go to the internet
python tests/item_used_in_documents.test.py  # "Used in": every document holding an item, newest first, with its own lines; works for an UNCOSTED item (which cost_targets refuses); a PO has no client column. LIVE DB - sentinel numbers only
node tests/item_master_used_in.test.js  # the Used-in list EXECUTED: documents render as real buttons, the popup comes first and opening is a second explicit step, the Open button is hidden when no editor route exists, and a stale async reply never paints into a newer panel
node tests/quote_requires_price.test.js  # quotation pricing rules + split behavior: Preview/Print stay strict; Save sends incomplete work to the app-only/no-PDF path
node tests/inv_requires_price.test.js   # invoice pricing rules + split behavior: every filled row needs positive qty/rate; Preview/Print block while Save remains available as an app-only draft
node tests/incomplete_price_panel.test.js  # incomplete-price right-panel warning pill and omission of all PDF actions
python tests/incomplete_price_save_backend.test.py  # QUT/INV app-only saves skip rendering, retire an older PDF, list DB-only drafts, and restore normal PDF output once pricing is complete
python tests/incomplete_price_draft_isolation_backend.test.py  # empty-path app drafts stay editable but cannot affect analytics, profit, client totals, bid/LPO/DN/PO workflow, Unsubmitted/Unpaid, Submit, or Paid
python tests/document_reference_backend.test.py  # isolated four-table Reference migration, save/load/list/search round-trip (temp SQLite only)
python tests/document_reference_api.test.py      # Reference reaches persistence but is stripped before every PO/QUT/DN/INV renderer
node tests/document_reference_frontend.test.js   # four editor lifecycles plus shared compact card escaping/ellipsis/status/amount contract
node tests/unified_document_info_row.test.js     # PO/QUT/DN/INV metadata render as one aligned non-wrapping row: shared doc-info-row/field/control hooks, the 34px control token, one sized track per field, and no auto-fit re-wrap
node tests/todo_open_and_ignored.test.js       # To-Do popup offers Open document in every attention queue (not deliver/search, which already open), one shared EDIT_LOADER, and Ignored renders as a collapsed accessible disclosure
python tests/quote_does_not_pollute_po_memory.test.py  # saving a QUOTATION must not write the `items` table (PO autocomplete) - the leak that filled an Arabic-only PO dropdown with 462 English quotation/DN/invoice names. Also asserts a PO still remembers its own items. LIVE DB - sentinel numbers only
python tests/item_search_phrase.test.py   # a hand-typed search wording is remembered against the ITEM and must NOT fork it (img_query is in _SIG_IGNORE); an empty phrase clears the key rather than storing null; and _set_data_key really reaches PO items, whose catalog entry wrongly declares no data_json column. LIVE DB - sentinel names only
node tests/find_images_button.test.js     # the Find button EXECUTED: a filled cell is never overwritten or even looked up, a refused match leaves the cell empty and is named, the picture reaches the screen
node tests/row_cost_currency.test.js     # RUNS the shared cost-cell helpers: per-row $/IQD conversion, blank cells still stamped, the document switch converting FROM EACH ROW'S OWN currency, and all three editors on the one implementation
python tests/insights_invoices_backend.test.py  # Insights → Invoices: every invoice listed once, paid + unpaid accounting for all of them and their money, totals matching the rest of the page, and every losing invoice carrying a REASON (named lines below cost worst-first and capped, extra costs, uncosted lines). LIVE DB - read-only
node tests/insights_invoices_section.test.js  # the "why" wording EXECUTED: losing lines named with their holes, the cap still counting the rest, extra costs reported without claiming a bad line, uncosted lines downgrading it to a BEST CASE, and no green pill anywhere
python tests/dn_as_invoice_line.test.py  # a delivery note billed as ONE invoice line is revenue, not an item: the three ways it is recognised, why no item name can be mistaken for one, and that excluding them from Top items moves the LIST and never the MONEY. LIVE DB - read-only
node tests/dn_line_cost_fill.test.js  # picking a DN in the invoice item field EXECUTED: a costed DN fills its cost and reconciles the currency, an UNCOSTED DN leaves the cell EMPTY (a literal 0 counts as a known cost and reports 100% profit) and says which DN, the inherited total is stamped with the DN's own cost currency (and the stamp removed when there is no cost), and the dropdown names the DN's own currency
node tests/cost_currency_rides_with_cost.test.js   # a cost never moves without its unit: reconcileImportedCostCurrency EXECUTED per ROW (a dollar line inside a dinar doc is left alone, two docs sharing a flag still convert their odd rows, blank cells still stamped, and TWO pegs so a 1400 DN billed by a 1500 cash invoice survives the round trip), smartMapRow carries cost_ccy, the Costing grid's per-line chip / cost_ccys-on-save contract, costCellFromMemory filling in the SAVED unit, and all three editors repainting the $/IQD chip after a pick
python tests/cost_currency_rides_with_cost.test.py  # the backend half: save_doc_costs stamps cost_ccy from cost_ccys (an explicit `currency` asserts, no argument means the row keeps its own), load_doc_costing hands every row a RESOLVED unit, pull_costs_from_quote reads each SOURCE row in its own unit
node tests/popup_contract.test.js  # THE popup contract EXECUTED against a DOM double: title + aria wiring, Esc / backdrop / X (and the drag that must NOT dismiss), dismissible:false, focus in-trapped-and-back over the whole panel, the counted scroll lock, one set of widths, text-by-default bodies, stacking + Esc hitting only the innermost, idempotent close, and showModal's unchanged contract
node tests/line_profit_and_loss.test.js  # per-item profit/loss EXECUTED: the four line states, which lines sell below cost and by how much in $, an uncosted line reading "-" rather than $0.00, both currency conversions, the cent of tolerance, worst-first order with the remainder counted, the PROFIT cell painted and repainted, the privacy switch (body class, persistence, button state, no write-back at boot), the invoice tfoot padding for both private columns, paste/fit guards, and no green profit state anywhere
node tests/dn_autofill.test.js            # DN picker EXECUTED against stubs: cost_usd converts into the DN's currency, a legacy raw cost is never replayed, the synthetic COST cell is actually repainted, the subtitle never prints [object Object]
python tests/master_data_sort_and_duplicates.test.py  # Master Data ordering + duplicate pairing: every offered column is a real sort key, direction flips and ONLY the direction, blanks stay at the bottom both ways, and the duplicate groups are pairs with no record in two of them, primary = most used then oldest. LIVE shared library - read-only
node tests/master_data_ui.test.js         # Master Data screen: row focus ring, text/chip contrast COMPUTED from the live tokens in both themes, table+sort semantics, focus in/out of the detail panel, named controls
python tests/cost_push_to_documents.test.py  # pushing a Master Data cost into saved documents: preview writes nothing, only ticked docs change, a dinar document gets the dinar figure at its own peg, other items in the same document are untouched
python tests/dn_cost_memory.test.py       # DN item memory: an IQD cost is remembered as cost_usd, the payload currency beats the stored flag, cost/picture are STICKY across a blank re-save, and five legacy "Celery" rows collapse to one suggestion without deleting anything
python tests/todo_all_months_backend.test.py  # Needs Attention/To-Do spans ALL months: default is the union of every month, age_days on every entry, oldest-first, mutes partitioned once
python tests/client_lpo_schema.test.py       # client_lpos can describe rentals and open supply contracts; the migration is idempotent
python tests/client_lpo_status.test.py       # closing is per kind: materials on value, rental at period end AND fully billed, open only by hand
python tests/client_lpo_usage_match.test.py  # an LPO finds its documents through the client alias index, not by raw spelling
python tests/client_lpo_save.test.py         # save_lpo stores kind/lines/period and refuses a client that is not on file; asserts BOTH databases are isolated
python tests/client_lpo_close.test.py        # close_lpo owns closed_at, so editing a header cannot reopen a terminated contract
python tests/client_lpo_fulfillment.test.py  # what is still owed, line by line; lines match on NAME because the spec moves between columns
python tests/client_lpo_billing_due.test.py  # the monthly 'Bill this month' bucket, and a rental term measured as the PO states it
python tests/lpo_from_quote_backend.test.py  # pulling a quotation's lines; images and costs stripped; an unreadable quotation is refused by name
python tests/lpo_marks_quote_won.test.py     # filing an LPO marks the quotation it cites won, unless it was deliberately marked lost
python tests/lpo_page_image_backend.test.py  # a page as a cached JPEG; the cache key invalidates on a replaced file; degrades without a rasteriser
python tests/no_orphan_lpo_numbers.test.py   # READ-ONLY: every LPO number on a document has a record, and no job note sits in the LPO column
node tests/lpo_lines_editor.test.js          # the tick-and-adjust lines editor, using the real CSSP-ADM-0010 arithmetic
node tests/lpo_form_shape.test.js            # the form covers all three kinds; its consts stay above Popup.open
node tests/lpo_picker.test.js                # choosing an LPO on a DN or invoice; N/A stays a real answer
node tests/lpo_row_summary.test.js           # the per-kind progress sentence, the bar, and the three groups
node tests/todo_billing_section.test.js      # the billing bucket actually reaches the To-Do screen
node tests/lpo_viewer_integrity.test.js      # every element the LPO module reads resolves; the signing viewer's invariants hold
python tests/insights_parity_backend.test.py  # Insights money engine: batched peg/currency index vs database.doc_iqd_per_usd for EVERY doc, month totals vs profit_summary, window maths, per-client roll-ups
node tests/insights_view.test.js          # focused frontend regression: Insights obeys its design rules - one filter row, no dual axis, legend/direct labels, tooltips + table twins, tokenised colours in both themes, no red/green direction cue
node tests/quote_settings_columns.test.js  # focused frontend regression: settings can display active table columns, but layout-only saves do not promote document-only columns to defaults
node tests/editor_new_reset.test.js       # focused frontend regression: saved docs keep their own columns/widths, New uses defaults, and clean type switches do not restore finished-doc custom columns
node tests/editor_toolbar_layout.test.js  # focused frontend regression: universal editor toolbar order, labels, removed Open buttons, and Save/Sign state hooks
# tests/profile_guard.py is a HELPER, not a test. `require_profile("abraj")` makes a
# profile-dependent module SKIP with a plain reason instead of failing on an unrelated
# assertion when the registry is on the wrong profile. Six tests use it
# (spacing_settings_reach_renderer, document_delete_backend, doc_autofit_layout_backend,
# po_image_column, po_pagination_backend, inv_dn_signed_copy_page). It replaced a standing
# board WARNING to "check the active profile before believing a failure" - a warning the
# reader has to remember is not a fix, and that habit is what let five real bugs hide
# behind "known failures" for weeks.
python tests/layout_planned_matches_rendered.test.py  # THE pagination guard: booked pages == drawn pages, all 4 types, real Chromium + PyMuPDF (slow)
python tests/doc_layout_engine.test.py   # the shared page-area engine: the four roles, four areas, four capacities, docs/12 SS4 invariants as checks (fast)
python tests/layout_css_engine_coupling.test.py  # every mm the engine reserves == the mm the print CSS draws; pins the Eshraqat literals that stay literals
node tests/po_handoff_review.test.js     # PO -> DN/Invoice: readiness by SCRIPT not tier, company grouping, explicit row build, write-back patches built by inclusion, sourcePo clear-site count
python tests/po_make_document_resolve_backend.test.py  # the Arabic-only record that matches at tier `exact` - the trap the review screen exists for
python tests/po_catalogue_review_backend.test.py  # PO new-item dismissal persists in per-profile doc_meta and round-trips through the WebView bridge
python tests/po_master_writeback_backend.test.py       # save_record's destructive edges: empty money NULLs both legs, blank language-free `unit` clears both, bare `name` is a no-op
python tests/po_handoff_link_backend.test.py           # set_po_fulfillment from a PO: saved-row indexes, idempotence, the unpriced-invoice refusal and retry, the Workflow lane
node tests/editor_meta_compact_layout.test.js  # focused frontend regression: DN prices lives in the toolbar, invoice linked quotation stays beside bank, and doc no/date wrappers keep the compact identity styling
node tests/bid_outcomes_interaction.test.js  # focused frontend regression: dashboard Bid outcomes donut/legend hover and click share one selected state
node tests/cost_grid_paste.test.js        # focused frontend regression: quote COST column and Costing & Profit fixed-row tables support Excel-style paste
node tests/editor_textarea_no_soft_wrap.test.js  # editor textareas preserve intentional newlines but never soft-wrap a one-line Excel value in PO/QUT/DN/INV
python tests/editor_textarea_no_soft_wrap_browser.test.py  # real Chromium layout: all four editor textareas scroll horizontally without soft-wrap, clipped text, or loss of explicit-newline growth
python tests/grid_paste_excel_html_whitespace.test.py  # real Chromium + all 32 cells from captured Excel HTML: source-formatting CRLF collapses to spaces, while nested/repeated structural <br>/<div> breaks remain cell newlines
node tests/grid_paste_incell_text.test.js  # text copied from INSIDE one Excel cell (plain text, NO html flavour) lands in ONE cell instead of a row per line; numeric cells and genuine range copies are unaffected
python tests/fit_columns_money_width.test.py  # Fit columns must leave a money value on ONE line: RATE is sized from the PRINTED "$4,300.00", not the cell's raw "4300"; the table still sums to the same width and one absurd figure cannot swallow it (real Chromium - canvas text metrics)
node tests/costing_scope_frontend.test.js # focused frontend regression: Costing & Profit kind chips expose QUT/INV only
node tests/dashboard_theme_toggle.test.js # focused frontend regression: dashboard premium theme toggle shares the file-backed theme preference and syncs with Settings
node tests/user_preferences.test.js       # focused frontend regression: file-backed preferences feed theme/behavior settings instead of only local browser cache
node tests/document_delete_frontend.test.js  # focused frontend regression: editor Open-list delete calls the document-aware delete API
node tests/invoice_paid_panel.test.js     # focused frontend regression: invoice right-panel rows use compact amount/date/status layout and expose the Paid control
node tests/po_quote_link_panel.test.js    # focused frontend regression: PO right panel exposes Submit/Submitted and the PO strip exposes the quotation picker/link field
node tests/dashboard_todo_popup.test.js   # focused frontend regression: dashboard Needs-Attention cards reuse the To-Do preview popup
node tests/dash_card_row_grid.test.js     # the Create-New row fills its width: every .dash-row.cols-N declares N columns, the markup's card count matches its class, cards are exactly quote/po/dn/inv, no :nth-child stagger past the last card
python tests/user_preferences_backend.test.py  # focused backend regression: settings.json preferences round-trip through config/current_effective_settings
python tests/doc_autofit_layout_backend.test.py  # focused backend regression: PO/QUT/DN/INV save/load + render use per-document Auto-fit layout
python tests/costing_scope_backend.test.py  # focused backend regression: default costing and Needs Attention uncosted scope is QUT/INV, while DN deal-flow links still work
python tests/document_delete_backend.test.py  # focused backend regression: document delete clears active row, satellites, Needs Attention/Costing ghosts, and quote child links
python tests/invoice_paid_backend.test.py  # focused backend regression: invoice Paid state persists through list/load/API and is cleared by invoice delete
python tests/po_submitted_quote_link_backend.test.py  # focused backend regression: PO submitted state and quotation reference persist and clean up on delete
python tests/po_deliver_invoice_backend.test.py  # focused backend regression: To-Do "deliver" bucket - client PO shows, office/blank lines ignored, invoice (not DN) clears it, ignore mutes it
python tests/dn_cost_summary_backend.test.py  # DN cost summary (sell/cost totals) + list_invoiceable_dns (priced DNs only) + EVERY cost read in the currency it was typed in (a dollar line beside a dinar line; the raw sum is what reported DN-TDC-032 at 856,961.40) + has_costs telling "never entered" from "free"
python tests/inv_dn_autolink_backend.test.py  # a dn_ref invoice line marks the DN's POs invoiced (link_invoice_dn_refs)
python tests/inv_source_dn_workflow_backend.test.py  # invoices.source_dn_no: DN shows in the Workflow with NO PO, both write paths + LINKED pill
python tests/auto_sign_on_save_backend.test.py  # auto-sign: defaults, re-roll every save, keeps explicit picks, never signs with missing assets
python tests/sign_saved_document_backend.test.py  # Apply sign & stamp on a saved doc: guards, legacy-PO layout confirm, failed render must not mark signed
python tests/dn_signed_copy_backend.test.py  # client's signed copy: rename to DN no, no phantom DN, failed attach leaves no record, delete cleans up; isolated cleanup also proves no fake numbering tombstone survives
node tests/editor_date_edit_reaches_save.test.js  # editing a saved document's date reaches the save: bindDocDate wires one listener on every lifecycle path, no editor touches the date element directly
python tests/dn_signed_copy_reaches_invoice.test.py  # a DN's signed copy reaches the invoice: workflow link attaches only once the copy exists, attach/remove rewrites the linked invoice PDFs, a free-text mention of the DN number rewrites nothing
python tests/inv_signed_copy_backend.test.py  # the same on INVOICES (2026-08-27): rename to inv no, `_is_signed_copy` keeps the scan out of the month list and the running number, an unpriced draft is refused, month list carries client_signed/waived; isolated cleanup proves SIGNEDCOPY-901 cannot survive in deleted_docs
python tests/invoice_number_repair_backend.test.py  # temporary-DB contract for the audited 2026-09-08 repair: 902-907 -> 048-053, every invoice-number satellite follows, Submitted is cleared, confirmed test tombstones alone are removed, next is 054
node tests/inv_signed_copy_frontend.test.js  # the invoice is wired at every lifecycle point (new/open/save/duplicate) and NEITHER editor keeps its own copy of the dialog
node tests/lpo_mark_placement.test.js  # LPO viewer marks: the box draws the real signature/stamp, live mm readout, a second mark never spawns under the first, missing assets are refused, preview and overlay share object-fit
python tests/client_lpo_backend.test.py  # LPOs received from clients: keyed by (client, lpo_no) so two clients may share a number, USD-canonical value, status ladder + invoiced/remaining rollup, acknowledgement stamps where asked and never edits the client's original
python tests/inv_dn_signed_copy_page.test.py  # an invoice attaches the CLIENT's signed DN copy instead of a re-render: images inline, PDFs merged in place (placeholder found by text search, pages fitted to A4, dn_ref order preserved); unreadable/unsupported/moved files fall back and say why; partial-return warning
python tests/spacing_settings_reach_renderer.test.py  # every spacing knob the settings panel offers survives config's whitelist AND moves a generator constant; the retired phantom controls stay gone; image row height is honoured end-to-end (page count + CSS cap)
python tests/quote_blank_rate_total.test.py  # a quotation line with a BLANK rate prints an empty TOTAL, never "$0.00" (which would read as free); a typed 0 still prints $0.00
python tests/po_pagination_backend.test.py  # PO = ONE doc over N pages: one number on every page, page roles, per-role budgets, two-pass packer (no oscillation into the signature), newline-aware row heights, retired split API; plus rendered-PDF geometry when Chrome+fitz are present
python tests/po_row_height_matches_render.test.py  # a PO row RENDERS at the height the paginator BOOKED: A-84's 29-row shape, a walk across the page boundary, the row setting actually changing the page, the content floor, and a tall setting not growing the <thead> (real Chromium + fitz)
python tests/doc_settings_unified.test.py  # ONE settings contract for all four doc types: every declared setting reaches the config global it names (the check that would have caught the inert PO row height), deep merge keeps sibling keys, a sibling SECTION is never touched, empty pops an override, and the legacy top-level spelling still loads
python tests/inv_sell_currency_backend.test.py  # IQD invoicing: grand_total stays USD while sell_total/sell_currency carry dinars, revenue sums + costing convert, dinar money/words formatting, migration backfill
python tests/inv_appended_dn_pages_backend.test.py  # invoice appends its DN-line pages (HTML-level always; PDF page-count when Chrome+fitz present)
python tests/po_link_suggestions_backend.test.py  # isolated regression: four-direction count/quantity suggestions, linked-DN-only tab data, multi-PO exact indexes, bridge + frontend contracts; never touches live/shared DB
python tests/eshraqat_pagination_solidity.test.py  # Phase Y.2: Eshraqat quote/DN/invoice at 90 wrapping rows paginate with NO near-empty spill page, every page numbered, sane page count (guards docs/12 §6.1 per-skin chrome reserve)
# - profile subsystem suites (see docs/13-PROFILES-BRANDS.md); these pin their own temp ProfileManager:
python tests/profile_manager_backend.test.py         # registry read/write, path helpers, validation
python tests/profile_migration_backend.test.py       # one-shot journaled legacy→profiles migration
python tests/profile_config_assets_backend.test.py   # per-profile config binding + brand assets
python tests/profile_data_isolation_backend.test.py  # document state never leaks across profile DBs
python tests/profile_switch_backend.test.py          # switch_profile validate/activate/reload/rollback
python tests/profile_legacy_fallback_backend.test.py # DOCUMENTS_HUB_LEGACY_FALLBACK=1 path mapping
python tests/profile_backup_restore_backend.test.py  # v2 profile-scoped backup + cross-profile refusal
python tests/eshraqat_numbering_backend.test.py      # permanent PREFIX-NNNN counters, peek/commit, tombstone floor
python tests/eshraqat_assets_backend.test.py         # seed assets + required-logo behavior
python tests/eshraqat_shared_template.test.py        # eshraqat _components.html macro contract
python tests/shared_library_save_resilience_backend.test.py  # shared-DB failure never fails a document save
python tests/client_rename_isolation_backend.test.py # rename stays correct within a profile
python tests/client_rename_profiles_backend.test.py  # rename propagates across all profile DBs
node tests/company_profile_switcher.test.js          # dashboard switcher UI + dirty-editor guard
python tests/eshraqat_quote_rendering.test.py   # Eshraqat brand skin: quote page-roles + identity
python tests/eshraqat_dn_rendering.test.py      # Eshraqat brand skin: DN page-roles + identity
python tests/eshraqat_invoice_rendering.test.py # Eshraqat brand skin: invoice page-roles + identity
python tests/eshraqat_po_rendering.test.py      # Eshraqat brand skin: PO split + identity
```

> **Profile note:** the generator/rendering tests resolve `config.ACTIVE_PROFILE`. The
> `eshraqat_*` tests pin their own profile via a temp `ProfileManager`; the Abraj-oriented
> ones assume the **active on-disk profile is Abraj** - if `data/profile-registry.json`
> currently has Eshraqat active they render the Eshraqat skin / use Eshraqat numbering and
> their Abraj-class assertions fail. Known instances: **`doc_autofit_layout_backend`**
> ("DN render ignored explicit document Auto-fit true") and **`document_delete_backend`**
> ("next quote number should count deleted tombstones, got `QT-0003`" - `QT-NNNN` is the
> Eshraqat permanent counter; Abraj expects `QUT-MT-CLIENT-NNN`). Both are a **test-isolation
> gap, not product bugs**: switch back to Abraj (or ignore) when running them against a live
> Eshraqat-active workspace. Fixing properly = pin the profile with a temp `ProfileManager`
> the way the `eshraqat_*` tests do.

Run at least `smoke_test.py` after touching anything in [[04-BACKEND-DOCGEN]], [[03-BACKEND-DATA]], or the print templates in [[08-STYLES-TEMPLATES]].

> **Note:** `smoke_quote.py` is *not* fully headless - it ends by calling `os.startfile()` on `data/_q_equipment.pdf` and `data/_q_manpower.pdf`, so on Windows it pops open the default PDF viewer for a visual check. `smoke_test.py` writes files only and opens nothing.

---

## The map-update protocol  ⭐

**Rule: this vault is only useful if it is fresh. After ANY code change, update the doc that owns the changed file _in the same change_.** A stale map is worse than no map - it gives false confidence. Never trust a doc for an edit without opening the real file; and never leave the doc behind after editing the file.

### Which doc owns which code

| If you changed… | Update this doc |
|---|---|
| `app.py`, `backend/api.py`, `backend/config.py` | [[02-BACKEND-CORE]] (+ [[13-PROFILES-BRANDS]] if the profile branches changed) |
| `backend/database.py`, `migrations.py`, `data_manager.py`, `client_master.py`, `po_catalogue_review.py`, `translate.py`, `clients.py`, `analytics.py`, `insights.py`, `master_data.py` | [[03-BACKEND-DATA]] (+ [[09-DATA-MODEL]] if schema changed) |
| `backend/*_numbering.py`, `backend/*_generator.py`, `pdf_engine.py`, `pdf_measure.py`, `signing.py`, `spreadsheet_parser.py` | [[04-BACKEND-DOCGEN]] |
| `backend/profile_manager.py`, `profile_numbering.py`, `brand_assets.py`, `eshraqat_render.py`, `shared_library.py` | [[13-PROFILES-BRANDS]] |
| `static/app.js`, `templates/index.html` | [[05-FRONTEND-SHELL]] (profile switcher UI → also [[13-PROFILES-BRANDS]]) |
| `static/quote.js`, `dn.js`, `inv.js`, `open_panel.js` | [[06-FRONTEND-EDITORS]] |
| `backend/doc_settings.py` | [[03-BACKEND-DATA]] |
| `static/costing.js`, `data_manager.js`, `analytics.js`, `clients.js`, `signing.js`, `signed_copy.js`, `doc_settings.js`, `bulk_paste.js`, `grid_paste.js`, `master_data_fill.js`, `col_resize.js`, `todo.js`, `po_link.js`, `client_master.js`, `client_defaults.js` | [[07-FRONTEND-SUPPORT]] |
| `static/popup.js`, or ANY new shared UI rule | [[14-UI-CONTRACTS]] |
| any `static/*.css` (incl. `eshraqat_print.css`), `templates/*_page.html`, `templates/eshraqat/*` | [[08-STYLES-TEMPLATES]] |
| DB schema, per-profile `settings.json`, on-disk folder layout, `data/` layout | [[09-DATA-MODEL]] |
| added/removed a throwaway/scratch file | [[10-SCRATCH-LEFTOVERS]] |
| `backend/activity_log.py` (the activity and problems log) | [[03-BACKEND-DATA]] (+ [[09-DATA-MODEL]] for `events`, + [[18-OFFICE-SERVER]]) |
| `static/activity.js`, `static/activity_capture.js` | [[07-FRONTEND-SUPPORT]] (+ [[05-FRONTEND-SHELL]] for the page wiring) |
| `server/*`, `launcher/*`, or an `Api` method that opens a window / file dialog / Explorer / the clipboard (it must join `server/gateway.py` `LOCAL_ONLY` and `launcher/client.py` `LOCAL_CALLS`) | [[18-OFFICE-SERVER]] |

### Steps for an editing agent (or human)

1. **Before editing:** open [[01-ARCHITECTURE]] → "Impact Map - touch X, check Y", then read the owning doc above, then read the **real source file** you're about to change.
2. **Make the code change.**
3. **Update the owning doc:** revise the affected `## <path>` section - its **Key symbols** (added/renamed/removed functions), **Reads/writes**, and especially **Gotchas**. If you added a new source file, add a new `## <path>` section. If you deleted one, remove its section.
4. **If the change is cross-cutting** (new bridge method, new DB table, new doc type, changed numbering/column-width math, new shared assumption): also update the relevant table and the **Impact Map** in [[01-ARCHITECTURE]].
5. **Ask the two propagation questions** (below). They take a minute and they are where this repo's worst bugs came from.
6. **Run the smoke test** that covers the area and confirm it passes before claiming done.

### The two propagation questions

Nothing here is built once and used once. A change that stops at the file it was
made in is the single most expensive habit this codebase has, so after every
change - a new feature, a shared module, a replaced component - answer both:

**1. What did this make WRONG?** Who still points at the thing you just replaced,
removed or superseded? This is a question of FACT with one right answer, it takes
a grep, and it is where every one of these came from:

| What changed | What was left pointing at it | How it showed up |
|---|---|---|
| Popup migration replaced a hand-rolled header | `po_link.js` still read the deleted `#poDetMeta` | PO⇄Invoice linking stuck on "Loading items…" in BOTH directions, silently |
| Four document catalogs dropped (2026-08-20) | Ten Data Manager sections still listed them | Every one raised `no such table` the moment it was opened |
| `costCellFromMemory` renamed | A test still asserted the old name | Ten days of red for a rename |
| Unified information row (2026-08-25) | Two CSS selector strings in a test | Red for a spelling while the behaviour was correct |

**Prefer a check to a memory.** Where the answer is mechanical, leave a test
rather than a note - `tests/po_link_detail_renders.test.js` now fails if ANY
`static/*.js` looks up an id nothing creates, which is the whole bug class above
turned into three lines of grep. A rule nothing enforces is a suggestion
([[14-UI-CONTRACTS]]).

**2. Where ELSE in the app would this capability help?** Not "should this code be
shared" - that is a refactoring question. This one is about the PRODUCT: the app
can now do something it could not do an hour ago, and the place it was built for
is rarely the only place it is worth having. **Ask the user, with evidence.** One
line, a named screen, and a number from their own data - never a menu of
maybes, and never build it unasked.

Worked example, and the reason this question is in the protocol at all. A
translator was built to fill in English names for new items on a PO. Asking where
else it helps produced three answers in a few minutes, one of them larger than
the feature that prompted it:

| Where else | Evidence (measured, not guessed) |
|---|---|
| Master Data screen - a batch "fill the missing English" action | **161** items have an Arabic name and no English one; **118** have an Arabic description and no English one |
| The SAME save-time popup on quotes / DNs / invoices, mirrored | All four document types feed `master_items` (`remember_item`, `remember_dn_item`, `remember_quote_item`, `remember_inv_item`). Those three are English, so what they omit is the ARABIC side - and **738** items are invisible to an Arabic PO for exactly that reason, ~5x the problem the PO-side feature solves |
| Master Data - a unit-consistency chip | **9 of 30** Arabic units are spelled more than one way in English (`عدد` as EA / piece / PCS / pcs / **handle**) |

**The bar is a NAMED place plus a COUNT.** "This might be useful elsewhere" is
noise and trains the reader to skip it. "The Master Data screen has 161 items
this would fix" is a decision the user can make in one second. If you cannot
name the screen or produce the number, there is nothing to raise.

**And the limit.** Sharing the CODE is a separate call from sharing the
capability. Two real callers is evidence; one caller and a hunch is not - and
some things here must never be shared at all:
[[12-PDF-LAYOUT-CONSTITUTION]] §6 forbids sharing packing, stretching and
last-page trimming between document types because they differ by measurement,
which is why `doc_layout.py` owns only the parts that are genuinely common.

### Optional: automate the reminder
A Claude Code **Stop hook** (or PostToolUse-on-Edit hook) can print "⚠️ update docs/ for the files you changed" so it never slips. Ask to wire it up in `.claude/settings.json` - it enforces this protocol without relying on memory.

### Optional: rebuild the whole map
For a large refactor, re-run the mapping workflow rather than hand-patching every doc. The workflow script is saved under the session's `workflows/scripts/` folder and can be re-invoked (edit the `SLICES` if the structure changed).

---

## graphify status  ⚠️

`graphify-out/` holds an **older** knowledge graph (built ~Jun 5) **and the `graphify` CLI is currently broken on this machine** - the `graphify.exe` in `~/.local/bin` is an orphaned uv shim (`uv tool list` reports no tools installed; its environment is gone). So:

- **This `docs/` vault - not graphify - is the source of truth.** It needs no CLI to read and travels with the repo (Cursor, other Claude sessions, etc.).
- To restore the queryable graph engine, **reinstall graphify** (whatever source it was installed from, e.g. `uv tool install <graphify-package-or-path>`), then `graphify update .` to refresh, and the commands in [root `CLAUDE.md`](../CLAUDE.md) become usable again. Until then, ignore `graphify-out/` (it is catalogued in [[10-SCRATCH-LEFTOVERS]]).
