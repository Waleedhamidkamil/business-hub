# 07 - Frontend Support (Cross-cutting modules)

> **Popups live in [[14-UI-CONTRACTS]] now.** Every overlay in this app is
> built by `Popup.open` (`static/popup.js`) so they cannot drift apart; the
> rules, the migration list and the gotchas are there, not here.

Cross-cutting frontend modules that sit outside the five per-doc editors: costing/profit workspace, Data Manager, analytics dashboard, Master Data ▸ Clients + the client settings the editors apply on selection, clients view, signing UI, per-doc settings editors, spreadsheet-paste importers, and item-grid column resizing.

**Covers:** static/doc_verify.js (Verify a document, 2026-10-07: number + code -> `verify_document` -> GENUINE / NOT GENUINE as a word, the reason, and what our document says; `window.openVerifyDocument`), static/unsaved_guard.js, static/master_doctor.js, static/todo.js, static/po_link.js, static/document_workflow.js, static/data_manager.js, static/analytics.js, static/signing.js, static/signed_copy.js, static/doc_settings.js, static/bulk_paste.js, static/grid_paste.js, static/col_resize.js

All modules are IIFEs (`(function(){ "use strict"; … })()`) that reach the Python backend via `const api = () => window.pywebview.api;` then `api().method_name(...)` (maps to `backend.api.Api.method_name`) - EXCEPT doc_settings.js, which is top-level async functions + `window.*` exports. They expose their public entry points on `window`. See [[01-ARCHITECTURE]] for the pywebview bridge, [[03-BACKEND-DATA]] for the api methods, [[09-DATA-MODEL]] for tables/settings shapes, [[13-PROFILES-BRANDS]] for the multi-company profile subsystem (per-profile settings/assets/DB these modules read and write through the bridge). Editor consumers live in [[06-FRONTEND-EDITORS]] / app.js `ALL_VIEWS`.


## static/gate.js - the gate: company switch, lock, and the start-up hand-off (2026-10-02)

Waleed's design: two doors meet in the middle, the brand on the left, a Welcome door on the right. Loaded before app.js; publishes `window.Gate = {ready, pageLoaded, switchTo, lock, isBusy}`.

- **Start-up:** since 2026-10-20 Windows draws a launch CARD, not the doors ([[03-BACKEND-DATA]] ▸ launch_gate.py); the doors below are now the switch and lock screens only. `Gate.ready()` waits two frames after the dashboard renders and calls `api().gate_ready(marks)` **once** per page load; the answer's `settle_ms` (hold + the card's fade) is how long the dashboard's own animations wait.
- **Switch:** `switchTo(from, to, doSwitch)` - close (420 ms) → `doSwitch()` → on `{ok, reload}` mark stage 1, swap the left door to the new company, write `sessionStorage["bh-gate"]` = `{mode, stage, accent, logo, label, html}` and return; the caller reloads. The note's `html` is REBUILT from state (`welcomeHTML(sub, SWITCH_STAGES, stage)`), not read back off the page. After the reload, index.html's inline script paints the doors shut on the first frame; `resumeAfterReload()` reads and deletes the note; `pageLoaded()` ticks stage 2 and fetches `api().gate_brief()`; `ready()` ticks 3 and opens after `HOLD_MS`. A refused or throwing switch opens the doors again (and rethrows). **Safety:** a resumed switch that never becomes ready opens after 20 s.
- **Lock:** `lock(brand)` closes over everything; keys are swallowed while closing, so the Ctrl+L press that locked can never unlock; once shut, any key or a click opens. Lock does nothing while a switch is running.
- Every tick is a stage that really finished; the only timers are the animation lengths and the safety limit. Ticks show a tick mark AND the word (DONE / WORKING / WAITING) - Waleed is colour blind.
- **Tests:** `tests/gate_behaviour.test.js` drives the real file on a fake DOM and clock (9 behaviours + 7 wiring checks; 7/7 mutations caught). Manual QA page: `tests/gate_qa.html` (serve the repo over http, e.g. `python -m http.server`, and open `/tests/gate_qa.html`; `?hold` keeps the doors shut after a switch). Its gate block must be a byte copy of index.html's - the test enforces it.

## static/unsaved_guard.js - ONE rule for "you have unsaved changes" (2026-10-01)

**Purpose** - Waleed opened an existing purchase order from the Open panel,
changed nothing, pressed **New**, and was asked whether to save. The rule had
been written four times, once per editor, and PO's copy was missing a clause:

```
onNewClicked            if (filledRows().length === 0) -> straight to New   <-- asked about CONTENT, never EDITS
quoteNew/dnNew/invNew   if (filled > 0 && state.dirty) -> prompt
poConfirmLeave          if (dirty && filled > 0)       -> prompt
activeDirtyEditor       dirty only                     -> prompt            <-- same false alarm on a company switch
```

There is now exactly one rule, and every caller asks it:

> **unsaved work == the editor is dirty AND it has at least one filled row**

**Key symbols** - `unsavedGuard.register(kind, impl)` where `impl` is
`{ noun, label, dirty(), filled(), save(opts) }`; `unsavedGuard.isUnsaved(kind)`;
`confirmNew(kind)` (the New button, resolves true when it is safe to proceed);
`confirmLeave(kind)` (the Home button); `dirtyEditor()` (what
`activeDirtyEditor()` in app.js returns to the profile switcher and the
duplicate flow). `kind` is `"po" | "quote" | "dn" | "inv"`.

**Reads / writes** - no bridge calls, no storage. It calls `window.showModal`
(app.js) and the `save` each editor registered. Nothing runs on load.

**Gotchas**
- **Each editor registers ITSELF**, because `filledRows` / `quoteState` and the
  rest are module-local: PO in app.js beside `activeDirtyEditor`, the other
  three in quote.js / dn.js / inv.js. `unsaved_guard.js` is therefore loaded
  **before** app.js in index.html - `tests/unsaved_guard.test.js` asserts the
  script order.
- **Both halves of the rule are load bearing.** `dirty` alone fires on a
  document you only opened; `filled` alone fires on an untouched document that
  merely has rows (the reported bug); dropping `filled` nags about a blank
  editor whose date you nudged.
- **`save(opts)` takes options for a reason.** `confirmNew` passes
  `{skipConfirm:true}` only - that is each editor's normal "Save & New", and for
  PO it is the **export** path that runs his after-export action on the PDF.
  `dirtyEditor().save()` passes `{stay:true, skipConfirm:true}` because the
  profile switcher and the duplicate flow leave the user where they are.
- **A dismissed modal (`null`) is Cancel, never Discard.** Losing a document to
  an Esc keypress is the worst available reading of a null.
- **A failed save does not proceed.** Save-and-New on a save that returns false
  stays put, or New would destroy the thing the user asked to keep.
- **An unregistered `kind` answers "nothing unsaved"** and never blocks a
  button, so a module that fails to load cannot wedge the New button.
- Quotation and Invoice deliberately have **no Home guard** - they keep a
  per-type draft ([[06-FRONTEND-EDITORS]]); only PO and DN call `confirmLeave`.
- PO prompts in **Arabic**, the other three in English, matching each editor's
  own language. The copy lives in `COPY` in this file.

## static/lpo_picker.js - attaching a document to an LPO (2026-09-11)

**Purpose** - the "Choose" button beside `#dnLpo` and `#invLpo`. Until now those
were free text, and it showed: **63 of the 64 delivery notes in the live Abraj
database say `N/A`**, one says `CSSP-ADM-0010`, and two invoices carry
`20260905 SHIP WATER`, which is not an LPO number at all. Everything the LPO
screen reports hangs off that string matching, so nothing ever lit up.

**Key symbols** - `choicesFor(client, current)`, `describe(row)`, `pick(fieldId,
client)`, `isNoLpo(value)`, `NO_LPO`.

**Gotchas**
- **The number still PRINTS on the document.** Picking is the easy path, not the
  only one: the input stays typable, because an odd case must never be
  un-fileable. `tests/lpo_picker.test.js` asserts it is not `readonly`.
- **`N/A` is a real answer**, offered first in the dialog. 63 delivery notes
  carry it honestly.
- **A blank client returns nothing**, never everyone's purchase orders.
- **A closed LPO is left out, except when it is the document's current value** -
  reopening a saved delivery note must not silently drop the LPO it was filed
  against. Matched case- and space-insensitively.
- **An open contract is never described by its total.** CSSP-OPEN-0001 prints
  `0.77`, the price of a litre of diesel; showing `$0.00` would read as free.
- Kind, status and value are all **words** in the row; the current choice is
  marked by a thicker border and the word "current", never colour alone.
- A bridge that throws returns `[]` - the editor still works without a picker.

## static/lpo_lines.js - the ordered lines on an LPO (2026-09-11)

## static/quote_picker.js - which quotation this PO answers (2026-09-28)

**Purpose** - The **Select** button on the LPO form. Same shape as `lpo_picker.js`: no DOM beyond the dialog, so the list logic is testable on its own.

**Key symbols** - `QuotePicker.choicesFor(client)`, `describe(row)`, `pick(client, current)`.

- `choicesFor` returns `[]` for a blank client. Offering every client's quotations is exactly how the wrong deal gets cited; it asks `api().client_quotes`, which is identity-aware ([[03-BACKEND-DATA]]).
- `describe` prints type, date, line count and total - **words and numbers, never colour**. A quotation whose total could not be read says `total not readable here` instead of `$0.00`, which beside a real quotation reads as free.
- `pick` returns the whole ROW, not just the number, because the caller wants its total too.


**Purpose** - the arithmetic behind "what did they actually order". Pure data in,
pure data out; no DOM, so it is testable without a browser. `lpo.js` owns the
markup.

**Key symbols** - `fromQuote(rows)` / `fromRows(rows)` / `toRows(lines)`,
`blank()`, `total(lines)`, `mismatch(lines, printedTotal)`.

**Gotchas**
- **Lines are INHERITED, never retyped.** CSSP-ADM-0010 cites QUT-MT-HDEC-029
  and reproduces four of our five lines verbatim; their total (18,390) is ours
  (19,286) minus exactly the line they dropped (7 @ 128 = 896). `fromQuote`
  returns every quoted line **ticked at the quoted quantity**, because a PO is a
  subtraction from what we offered. The test hard-codes those real numbers.
- **`take` is UI state and must never reach the record.** `toRows` strips it, so
  a saved order compares equal to the same order typed fresh.
- **A mismatch is a sentence, never a block** (Waleed, 2026-09-11): a PO can
  legitimately carry an odd total, and refusing the save would strand him.
  The difference is printed unsigned - the sentence already says which way round
  it is.
- **`mismatch` does NOT pre-round the difference.** Rounding to cents first
  snaps every sub-cent gap to zero and makes the `< 0.01` tolerance a branch
  that can never run; mutation testing found exactly that on the first draft.
- A line with **only a quantity is kept** - the diesel line may be typed
  numbers-first, and dropping it would lose real work. Only a line with no name
  AND no quantity is discarded.

**Guarded by** `tests/lpo_lines_editor.test.js`.

## The LPO screens, rebuilt 2026-09-11 (Waleed's brief)

Measured before the rebuild: a row was ~103px (three stacked lines plus 14px
padding and a 10px gap) and carried **six action buttons** - View, Original,
Edit, Attach, Close, Remove - repeated on every record for maybe one use each.
Six LPOs filled a 1080p screen, while the only summary on the page sat in 13px
grey in a corner. His words: "too much spaces and little bit things".

**The list.** Rows are 46px and carry no buttons: number, client, kind, one
state chip, the progress sentence with its bar, the value, a caret. **The row IS
the button.** The freed band above became `lpo-kpis` - ordered, not yet
invoiced, months to bill, awaiting signature - the last two being figures that
could previously only be found in the To-Do. The kind and status dropdowns were
retired (the groups already separate the kinds); `lpoDueFilter` toggles
`lpoState.due`, which keys on `months_unbilled`.

**One LPO, open (`lpoDocView`).** Clicking a row opens the client's document
beside its record: the PO number leads the topbar, work on the left, controls on
the right, and **"All LPOs" sits at the far right** with Previous/Next (Waleed,
2026-09-12: it is a back button, so it belongs with the other navigation).
**70/30**, because the left pane is where the signature gets placed and judged.
`showLpoDoc(lpo)` loads the file and `lpo_fulfillment` before the panel paints.
**Every page is rendered at once and the stage scrolls** (2026-09-12): each
page is its own `.lpo-v-pg` box, Previous/Next SCROLL rather than re-render,
and `V.page` follows whatever page is in view (that is the page `addLpoMark`
places onto). A mark lives inside its own page's box and is measured against
that page's canvas, so a second sheet of a different format cannot shift it.
`stepLpoDoc(±1)` walks the list **as filtered and grouped on screen**.
`window.openLpoByNo(lpoNo, clientName)` is the way in from elsewhere (the To-Do
billing reminder): it loads the list if it is cold - Previous/Next needs it
anyway - matches on client + number, falls back to number alone, and only lands
on the list when nothing matches. Guard: `tests/lpo_viewer_integrity.test.js`.

- **The panel is read-only** (Waleed's call): Edit opens the form that already
  exists, so there is no half-saved state in a pane you can click away from. One
  primary action, then a 2-column grid of equal-size secondaries.
- **Signing happens in this screen**, not a popup over it. `startLpoSigning`
  always reloads `"file"` - the client's ORIGINAL - or marks stack on an
  already-stamped copy. The placement tools appear in the toolbar only while
  signing.
- **The placement machinery is UNCHANGED.** `renderLpoPage`, `addLpoMark`,
  `paintLpoMarks`, `startLpoDrag` and `updateLpoMarkCount` work off
  `lpoState.viewer` and the ids `lpoCanvasWrap` / `lpoPageLbl` / `lpoVHint`,
  which this screen supplies. Fractions of the page, the real signature drawn in
  the box, and the preview agreeing with the merged result were each fixed
  against a real failure - moving the frame around them disturbed none of it.

**Two fixes on 2026-09-12, both reported from the running app.**

- **The toolbar icons rendered as a giant squiggle and circle** over their own
  labels. An inline `<svg>` with no `width`/`height` falls back to its intrinsic
  size, about 300px, and the only sizing rule covered `.lpo-icob` (the icon-only
  buttons), not `.lpo-v-add`. The `ico()` helper now writes the size **on the
  tag**, so the icons no longer depend on a CSS rule existing.
- **"The screens are slow."** The backend was not: `list_lpos` is 0.06s and
  8 KB. Opening one LPO cost **~1.5 MB across the pywebview bridge** - 819 KB of
  PDF plus **679 KB of base64 signature images re-fetched every single time**,
  for data that only changes when Settings uploads a new one. `lpoAssets()` now
  caches them for the session, `signing.js` calls `window.clearLpoAssets()` on
  upload, and **viewing never fetches them at all** - only signing does. An open
  is 820 KB, and editing the header reuses the rendered document rather than
  pulling the PDF again (the reuse test keys on `file_path` AND `ack_path`,
  because signing writes one and attaching replaces the other).

Guards: `tests/lpo_row_summary.test.js`, `tests/lpo_viewer_integrity.test.js`,
`tests/lpo_mark_placement.test.js`.

## static/lpo.js - Client LPOs (2026-07-31)

**Audit, 2026-10-02:** [[16-CLIENT-LPO-AUDIT]] records confirmed edit/item-loss,
delivery-versus-billing seed, currency, identity, rental-period and UI gaps.
Implemented behavior and verification are in [[17-CLIENT-LPO-FIXES]]. The audit records the original failures.

**Purpose** - The LPOs screen (`lpoView`) plus the in-app PDF viewer used to counter-sign them. NOT a document-type editor: we never generate an LPO, so there is no numbering, preview or print template - this is a store with a viewer and one action that matters (acknowledge, then send the copy back).

**Key symbols** - list: `showLpos()` / `refreshLpos()`, `renderLpoList()`, `visibleLpos()`, `lpoKpis()` / `renderLpoKpis()`, `renderLpoChips()`, `lpoColumns(kind)`, `lpoOrder()`, `lpoNextHtml()`, `runLpoNextStep()`, `invoiceLpoPeriod()`; LPO screen: `showLpoDoc()`, `renderLpoDocHeader()` / `lpoDocActionsHtml()` / `lpoMenuHtml()`, `renderLpoDocSide()` and its section builders, `lpoBillingSection()`, `openLpoDocFile()`, `lpoSetZoom()` / `lpoFitScale()`; form and file: `openLpoForm(existing, how)`, `attachLpoFile()`; signing: `renderLpoPage()`, `addLpoMark()`, `paintLpoMarks()`, `startLpoDrag()`.

**The list (rebuilt 2026-10-03)** - Waleed asked for "simple, great looking,
easy to use, professional and reliable", with tabs like the Materials / Equipment
/ Manpower / Service ones on the Quotation and Invoice lists. Design and plan:
`docs/superpowers/specs/2026-10-03-client-lpo-simplify-design.md`.

- **Three figures** over the list (`lpoKpis`): Ordered, Not invoiced yet, Need
  action. Supply contracts are left out of both money figures (their stored
  total is a UNIT price - CSSP-OPEN-0001 prints 0.77, a litre of diesel), and an
  LPO closed by hand is left out of "not invoiced yet" (its rest will never be
  billed). **Need action is also the filter** (`#lpoDueFilter`, `lpoState.due`):
  it shows only rows whose `next_step.action` is true, and says "showing only
  these - show all" in words while on.
- **Type tabs** (`renderLpoChips`): the shared `renderChipRow` (app.js) with
  All / Materials / Rentals / Supply contracts and counts, into `#lpoKindChips`.
  A second chip row for clients (`#lpoClientChips`) appears only when the LPOs
  span more than one client; it replaced the client `<select>`.
- **A table whose columns follow the tab** (`lpoColumns(kind)`):

  | tab | columns |
  |---|---|
  | All | LPO, Type, Progress (`lpoProgress` + `lpoBar`), Value, Next step |
  | Materials | LPO, Items, Delivered (lines fully delivered, `+N part`), Invoiced (USD of USD), Value, Next step |
  | Rentals | LPO, Period (`no end date` flagged), Months billed, Billed, Next step |
  | Supply contracts | LPO, Supplying, Unit price, Invoiced (+ invoice count), Not invoiced (months, from when), Next step |

- **Next step** comes from the BACKEND (`database.lpo_next_step`, see
  [[03-BACKEND-DATA]]): a button when `action` is true (Create invoice, Create
  delivery note, Invoice <month>, Set the end date, Attach their PO), quiet words
  otherwise (Up to date, Fully invoiced, Finished, Closed, Ends 11 Nov). The ROW
  opens the LPO; the one button in it runs `runLpoNextStep`. Enter on a focused
  row opens it too.
- **Order** (`lpoOrder`): open LPOs first, newest first by the LPO's OWN date
  (`lpoDate` parses D/M/YYYY and ISO - never `new Date(string)`, which reads
  4/6/2026 as April); finished ones (`lpoIsDone`: status closed AND no pending
  action) last, dimmed and struck through.
- **Search** covers the LPO number, client, quotation, ERP, project, notes, the
  ordered items, and its delivery note and invoice numbers.
- The unmatched-references review (`lpo_reference_gaps`) is a quiet link under
  the table (`#lpoReferenceReview`), not a banner.
- `lpoBar(r)` still returns null when there is no honest denominator; `over_value`
  still comes from the backend flag.

**The LPO screen (rebuilt 2026-10-03)** - `#lpoDocView`.

- **Header** (`renderLpoDocHeader` -> `#lpoDocActions`): the next step is the
  ONE primary button; the other create action is added where the kind has both
  (materials orders and supply contracts are delivered AND invoiced; a rental is
  only invoiced); a closed LPO offers nothing to create and a finished one no
  Create invoice; everything rare is in **More** (`lpoMenuHtml`: Open in PDF
  reader, Print, Copy file, Show in folder, Edit details, Replace / Attach file,
  Sign again - only once a signed copy exists -, Close / Reopen, Remove). While
  signing, the header is empty. **Previous / Next LPO were removed** (Waleed:
  "not very useful"), and with them `stepLpoDoc`. The way out (`#btnLpoDocBack`,
  All LPOs) stays at the far right - his 2026-09-12 call.
- **Right panel** (`renderLpoDocSide`, fixed 380 px), in this order:
  `lpoSummaryHtml` (client, date, quotation link, notes, Edit, three figures per
  kind from `lpoDocFigures`), `lpoSignedHtml` (Signed copy saved + Copy file /
  No signed copy here + Sign and stamp / No document attached + Attach file),
  `lpoWarningsHtml` (only over-value, or fully invoiced with no end date),
  `lpoItemsHtml` (materials: ordered / delivered / invoiced, plus lines found on
  documents but not on the LPO) OR `lpoBillingSection` + `lpoOrderedHtml`
  (contracts), `lpoLinkedHtml` (delivery notes, and invoices not already shown
  against a period), `lpoDetailsHtml` (folded).
- **Since 2026-10-06 `lpoBillingSection` is the MONTH list** ("Invoices by month", `n of N`): each month with its invoice chips and count, "Missed" plus an Invoice button for a month that ended with none, "Not invoiced yet" for the current month (no alarm), "Upcoming", and "outside the term" for an invoice dated beyond the contract's months; a note says how many invoices are not marked sent. `invoiceLpoPeriod(lpo, month, start)` hands the month's service dates to the invoice editor and asks before a second invoice for a month that already has one. The Confirm-dates popup (`openLpoInvoicePeriod`) and the end-date step/warning were removed. Rentals tab column: "Months invoiced" with "N missed" / "N not sent" under it; a contract's progress sentence is months only, never money. The 2026-10-03 description below is history:
- **`lpoBillingSection` is ONE list.** Each period shows Billed / Partly covered
  / Not billed / Upcoming, its invoice chips, Confirm dates when an invoice's
  period was only estimated from its date (title = the ledger's `source`), and
  its own Invoice button when unbilled (upcoming periods too: RENT-0005(3)'s last
  month was billed before it began). Invoices that fall in no period are listed
  under "Invoices not matched to a period" with Set period. It used to be the
  periods, then the same invoices again in a second list.
- **Document toolbar**: page, zoom, **Fit width** (`lpoSetZoom("fit")`,
  `lpoFitScale`), Original / Signed copy. `lpoState.zoom` keeps the choice for
  the session ("fit" by default); a window resize re-fits while in fit mode.

**The form (rebuilt 2026-09-11)** - `openLpoForm()` now covers all three kinds of
PO. `LPO_KINDS` drives a three-way picker; `applyKindFields()` shows and hides the
money block with `el.hidden`. A **supply contract has no total field at all** -
CSSP-OPEN-0001 prints `USD 0.77`, the price of a litre of diesel, and storing
that as a value is what made its first invoice read "Invoiced over LPO value".
A rental adds `period_start` / `period_end` / `monthly_amount`, which is what the
billing reminder counts.

**Filed in one pass (2026-09-28).** Three things the form used to send Waleed away for:

- **Their PO document is chosen in the dialog** (`#lpoFFile`). It is UPLOADED after the save, because `attach_lpo_file_data` refuses a PO that is not on file yet - but the `File` is READ inside `submitLpo`, beside every other field, for the reason the whole form is: `Popup.close` detaches the panel before it resolves, so anything read after it is `undefined`. `attachLpoData(client, no, file, hasAck)` is the shared upload; the Attach button on the detail panel goes through it too, so the "replacing the original deletes the counter-signed copy" confirmation exists once.
- **A Select button beside "Against quotation"** (`#lpoPickQuote` → `pickQuote()` → [[static/quote_picker.js]]). The field was free text with a placeholder, and HDEC has 41 quotations; a wrong number is silent, because the lines then pull from the wrong deal. Typing stays - CSSP-RENT-0002 cites QUT-146 and CSSP-OPEN-0001 cites QUT-03, both HDEC's own references.
- **The total comes from the quotation**, not from the sum of the lines that came across. They are the same number whenever every line reads; when they are not, `LpoLines.mismatch` says so under the lines, and filling the line sum instead would hide it. Never written over a figure already typed off the PO.

`pullLines()` returns the quotation response (or null) rather than a boolean, which is how `pickQuote` gets the total.

**Guarded by** `tests/lpo_form_quote_and_attach.test.js` (13 checks, 13/13 mutation-checked) and `tests/lpo_quote_and_costs.test.py`.

- **"Pull lines" is the point of the form.** `api().lpo_from_quote(no, client)` ->
  `LpoLines.fromQuote(rows)`: CSSP-ADM-0010 cites QUT-MT-HDEC-029 and reproduces
  four of its five lines verbatim, so filing it is unticking one box. A pull that
  fails toasts by name - CSSP-RENT-0002 cites `QUT-146`, which is HDEC's own
  reference, and those POs are typed by hand via `LpoLines.blank()`.
- **The client list comes from `cm_list`**, not the old `clients.py`. `save_lpo`
  refuses a client that is not on file, and `ClientRequired.offer(res, "lpo")`
  offers to add them here; the retry **rebuilds** the payload, because the new
  name is in the field by then. `client_required.js` FIELDS has an `lpo` entry
  with no `populate` (the datalist is built once when the form opens).
- **The Add / Save button saves WITHOUT closing (`keep: true` + `onClick`), and
  the dialog closes only once the save is in (fixed 2026-09-19).** Everything the
  payload carries is read out of this dialog, and `Popup.close` detaches the
  panel **before** it resolves `handle.result` (`popup.js` `finish()`), so the
  old shape - await the result, then build the payload - read twelve fields that
  were already gone. Every one came back `""`; the blank client was simply the
  first thing the backend refused ("This document needs a client. Pick one from
  the list"), which is how it was reported. Nothing had been added through this
  form in either profile since it moved to `Popup.open`; the six LPOs in the
  Abraj file were written by the backend tests. Reordering `popup.js` would not
  have helped - the `await` resumes in a microtask, after `finish()` has run to
  completion either way - so the values must be read while the dialog is up, the
  way `client_required.js` already does. `tests/lpo_form_reaches_save.test.js`
  runs the real dialog to pin the detach, then pins the form's shape.
- **`const g` / `const currentKind` MUST stay above `Popup.open`.** It calls
  `onOpen` synchronously and swallows what it throws (`popup.js:324`), so a const
  declared below it hits the temporal dead zone and the form opens blank, unwired
  and silent. `tests/lpo_form_shape.test.js` pins the order.
- An unticked line is dimmed **and struck through** - the strike carries the
  meaning without colour.

- **Open / Print / Copy all go through `lpoSendPath(lpo)` (2026-09-19).** One rule: what the viewer is showing when it is showing THIS lpo, else the signed copy when one exists, else the client's original. They used to spell it three ways - only Open followed the viewer - so signing an LPO and pressing **Copy file** put the client's own UNSIGNED original on the clipboard, which is the one button whose entire purpose is sending the document back to them. The `lpo_no` + `client_name` guard is deliberate: the side panel and the viewer are separate state, and handing over a DIFFERENT LPO's file is worse than the wrong version of this one. **Show folder** still reveals `file_path` - the signed copy is written beside it as `<no>.acknowledged.pdf`, so it is the same folder. `tests/lpo_copy_takes_the_signed_copy.test.js` runs the real `lpoDocAction`.

**Gotchas**
- **Marks are fractions of the page (0-1), never pixels.** `startLpoDrag` divides by the rendered surface's client size, so zooming between placing and applying cannot move the stamp. The backend multiplies straight back up.
- **pdf.js must be the classic UMD build** (`static/lib/pdf.min.js`), not `.mjs` - the app loads over `file://`, where Chromium blocks ES-module scripts. `lpoWorkerReady()` points `workerSrc` at the vendored worker and tolerates the in-thread fake-worker fallback.
- An **image** LPO skips pdf.js entirely and is shown as an `<img>`; the backend wraps it onto an A4 sheet when acknowledging, and the same fractions apply.
- **The box draws the REAL signature/stamp** (fixed 2026-08-01). It used to be an empty outline, so there was no way to judge size before committing - the whole point of placing by hand. Images come from the `signing_assets()` bridge method; `.lpo-mark-img` uses `object-fit: contain` and **must stay in step with `lpo_ack._overlay_html`**, which uses the same fit - otherwise the preview lies about the merged result. `tests/lpo_mark_placement.test.js` asserts both sides.
- **New marks cascade; they never spawn on the same spot** (fixed 2026-08-01). Both used to start at x=0.12/y=0.76, so a stamp added after a signature sat exactly beneath it - invisible, and the user dragged the top one believing it was the only one, ending up with one mark in the output and concluding nothing had applied.
- `updateLpoMarkReadout` shows live **mm** while moving/resizing, computed from `V.pageMm` (the page's true size from pdf.js, not an A4 assumption). `updateLpoMarkCount` tallies placed marks per page in the hint line, so "I placed two" is verifiable before saving.
- **A mark whose asset was never uploaded is refused with a toast**, not added: `lpo_ack._resolve_marks` silently skips a mark with no image, so an invisible box would make the acknowledge quietly do less than asked.
- **`openLpoViewer(lpo, mode)` - `sign` shows the ORIGINAL with the placement tools, `review` shows the acknowledged copy read-only.** It defaults to `review` whenever `ack_exists`. Until 2026-08-01 it always opened the original, and since the original is deliberately never modified, a perfectly successful acknowledge looked like a no-op: you signed, reopened, and saw an unstamped file. **A banner (`.lpo-v-file`) now names the file on screen** - that is the actual fix, the rest is convenience. Acknowledging reopens in `review` so the result is immediate, and "Sign again" reopens `sign` - re-signing must restart from the original or marks stack on an already-stamped copy. The row button reads "View signed copy" once one exists, with a separate "View original".
- `window.lpoState` is exposed like `window.invState`, for tests and debugging.
- **The latest open wins (2026-10-03).** `openLpoDocFile` takes a ticket
  (`lpoState.openTicket`) and re-checks it after every wait (the file over the
  bridge, pdf.js, the fit measurement); `renderLpoPage` does the same per pass
  (`V.renderPass`). Opening a second LPO while the first was loading used to let
  the first finish on top of it - pages in the wrong screen, or a crash on a
  viewer that had not loaded - and rapid zoom clicks could draw the pages twice.
  Pinned in `tests/lpo_screen.test.js`.
- **`attachLpoFile` returns a promise** that settles after the upload (true) or a
  dismissed picker (false). It used to return when the picker OPENED, so the LPO
  screen reloaded before the file was chosen and kept showing the old document
  after a successful Replace. Callers reload only on true.
- **`copy-signed` always copies `ack_path`.** The More menu's Copy file follows
  the viewer (`lpoSendPath`); the signed-copy line's Copy file must not, or it
  would copy the original whenever the Original tab happens to be showing.
- **Guarded by** `tests/lpo_screen.test.js` (27 behaviour checks against the real
  module, 26/26 mutation-checked), `tests/lpo_row_summary.test.js`,
  `tests/lpo_audit_frontend.test.js`, `tests/lpo_viewer_integrity.test.js`, and
  the browser QA `tests/lpo_workflow.qa.cjs` (updated for the new screens).
- **Verified end to end on the real documents, 2026-09-11** (the viewer itself was NOT changed by that day's redesign). Measured on `CSSP-ADM-0010`: a placed mark lands within **0.3-0.6mm** of the box asked for on the edges where the ink meets it; the inset on the other edges is the image's own aspect ratio inside the box, which is what `object-fit: contain` should do. Multi-page works; a page index past the end is dropped and reported (`applied 1/2`); an unknown kind or an empty list raises rather than writing a file; PNG and JPG LPOs wrap onto A4 (595x842pt); a page carrying `/Rotate` keeps the geometry a viewer shows, with the change confined to the requested box; signing three times from the original produces a byte-identical file, so marks cannot stack; the client's original is unchanged (sha256 verified); an unwritable destination raises `PermissionError`, which the bridge turns into a toast, and `set_lpo_file` runs only AFTER a successful write, so a failure leaves no dangling `ack_path`. Guard: `tests/lpo_viewer_integrity.test.js`.

---

## static/costing.js - RETIRED 2026-09-11

The "Costing & Profit" workspace (dashboard card -> `costingView`) was removed at
Waleed's request: he did not use the screen. **The costing DATA and the cost
columns are untouched** - per-item costs are typed in each document's own
private cost column (`qc-cost-col qc-private` in quote.js / dn.js / inv.js),
which is where all 146 costed documents were actually costed, and which still
feeds every profit figure in Insights.

Removed with it: `costing_styles.css`, its dark-theme block, the dashboard card,
and `tests/costing_scope_*`. The To-do list's **Add costs** action now opens the
document itself through `openDocEditor`, which is where the cost column is.

Left in place deliberately: the `save_doc_costs` / `deal_flow` /
`find_known_costs` / `list_costing_docs` bridges. Three of them are still
exercised by live tests guarding cost-data rules, and `costing_summary` feeds
the dashboard. They are now unreachable from the UI - dead API surface, not dead
behaviour - and can be tidied separately.

## static/todo.js  (Phase X.26)

**Purpose** - The dedicated **To-Do / Needs Attention** view (`todoView`), opened by the dashboard Needs-Attention "View all". Organized sections so the user can finish everything unfinished: **Decide** (submitted open quotes to mark Won/Lost before the 14-day auto-loss, all months), **Uncosted** (month QUT/INV lacking cost data), **Unsubmitted** (month docs not marked submitted), **Unpaid** (Phase X.28 - all outstanding invoices, any month, not marked paid), plus **Ignored** (Phase X.30 - muted entries, shown only when non-empty, each with a gray Restore button; stays visible even when everything else is all-clear so restores are always reachable). **2026-08-25 - Ignored is now a COLLAPSED disclosure:** it is the one section that only ever grows and the least urgent thing on screen, so it renders closed and the real work stays above the fold (measured: 42px closed vs 394px open at 14 entries). Every live row (and the preview modal) carries a subtle eye-off **Ignore** button that mutes that doc for THAT category via `set_attention_ignore` (per-category on purpose - muting an invoice's costing nag keeps its unpaid reminder). Self-contained IIFE; reuses `window.DASH_ICON` / `window.DASH_CASE_ICON`.

**2026-10-21 (Waleed) - ONE page of Insights tables.** The stacked card sections are gone (a tabbed version was built first and turned down: "use the same insights table UI, keep everything in 1 page"). The page is Insights' own layout and classes: an `.in-kpis` row (one `.in-kpi.todo-kpi` button per type with work - count plus the figure that matters; a click scrolls to its table), one filter box (`_query`, number or client, filters every table at once and redraws rows only so it keeps focus), then per type an `.in-sect` heading (name + `summaryText`) and an `.in-card` holding an `.in-table`. Types with nothing in them are not drawn; Ignored is last and starts closed (`_ignoredOpen`, Show/Hide `.todo-toggle`). Columns come from `COL` (`label`, `left`, `first`, `value` to sort on, `cell`, `late` -> Insights' `td.is-neg`) and `COLS_FOR[cat]` (an unpaid invoice its amount and wait, a bid `open`/`left`, a PO `lines` = `open_lines of client_lines`, a contract `oldest`/`due`, a muted row `muted`). Headings sort exactly like Insights (`_sort[cat]`, `.in-sortmark`, same column reverses, blanks last, dates via `dateVal`); a sort redraws only that table (`drawRows(cat)`). Money shows in the document's own currency (`fmtMoney`: `$9,000.00` / `IQD 12,600,000`); sorting and totals use `amount_usd`. A row click opens the same preview popup (`openDocModal`, billing rows `openLpoTodoModal`); row buttons are `actionsHtml(cat, r)` with the same `data-act` values as before. `todoView` carries `in-view` so `--in-neg` resolves. **One scroller:** the page (`.todo-body`); no table has its own height cap. Pinned by `tests/todo_table.test.js` (runs the column/sort/filter/money code) and `tests/todo_amounts_backend.test.py`; `todo_row_fits_its_cell.test.js` was removed with the card grid it guarded. **Colour and life (same day, "it feels dead"):** each type wears one colour (the dashboard's Needs attention family) via `--tc` / `--tc-ink` / `--tc-soft` set on `[data-sec=...]` and `.todo-kpi[data-jump=...]`: the KPI tile's left bar and icon chip (`secIcon` - the dashboard's `DASH_CASE_ICON`, a calendar for billing), the section heading's chip and name, the table card's top rule, and the row's own action (`.todo-act.is-main`; Won stays the blue fill). The document cell leads with a kind badge in that document's colour (`.todo-kind.k-*`). Waiting is a `.todo-age` tag: plain, `.is-aging` from 21 days (`AGING_DAYS`), `.is-late` from 45 - the days always written. Each KPI tile adds a `.todo-kpi-flag` when something has waited too long (bids: lost within 2 days; contracts: 3+ months due, NOT the LPO's age). The page rises in once per visit (`_enter`, armed by `showTodo`, consumed by `render`) so an action's reload never replays it; reduced motion turns it off.

**Submit fulfillment hand-off (2026-07-18)** - `performAction("submit",…)` still owns only submitted-state persistence. After success, each caller refreshes its queue; the preview path also calls `closeDocModal()` first. It then invokes app-shell `openSubmittedFulfillment(kind,no)`, which opens the existing PO/DN picker for DN/Invoice only. No To-Do backdrop remains under the picker, and cancelling linking leaves the submitted mark intact.

**Key symbols** (`window.showTodo` and `window.openTodoDocModal` are public):
- `showTodo()` - nav entry; hides all views + editor chrome, hides the Open panel, shows `todoView`, `setBrand("To-Do")`, one-time `initControls()`, then `load()`. Exposed as `window.showTodo`.
- `initControls()` - seeds `#todoMonth/#todoYear` to today; wires month/year change + `#todoRefresh` + `#todoBack` (→ `showHub`).
- `load()` - `api().dashboard_needs_attention()` (no arguments = **everything outstanding, all months**) → `_data`; `render()`. Only the `#todoScope` selector's "One month only" option passes `(_year, _month)`.
- **Scope filter (2026-08-10)** - `#todoScope` (`all` | `month`, default `all`) with `syncScopeControls()` hiding/showing `#todoMonthCtl` + `#todoYearCtl`. Before this the month picker was mandatory and three buckets were month-scoped, so anything left over vanished at the month boundary - in the one screen whose job is to stop work being forgotten. Section hints and the all-clear text switch wording on `_scope`.
- **Age tail** - every non-`decide` row shows `age_days` as "N days waiting", `is-urgent` past 45 days. The date moves into the tail's tooltip when an age is shown, so the client name isn't squeezed to an ellipsis.
- `rowHtml(cat,r)` / `sectionHtml(cat,title,hint,list,collapsible)` / `render()` - builds the `.todo-section`s. **Phase X.29: clicking a row opens the read-only PREVIEW MODAL** (below), not Costing directly. The per-row buttons stay: Decide Won/Lost, Uncosted "Add costs" (→ costing), Unsubmitted "Mark submitted" (`set_submitted`), Unpaid "Mark paid" (`set_invoice_paid`). Decide meta omits the doc date (tight cell; the age tail is what matters).
- **`deliver` bucket (2026-07-17) - "Deliver & Invoice":** `render()` adds a `deliver` section (client POs not yet invoiced, from `dashboard_needs_attention`; [[03-BACKEND-DATA]] `pos_awaiting_invoice`). Its `rowHtml`/`modalActsHtml` entries give an **"Open PO"** button (`data-act="open"`, kind `po`) instead of a state-change action; the `open` handler (both the inline `wire()` handler and the modal top-right) opens the doc via the `{kind → window.*loader}` map (falls back to that when `opts.onOpen` is absent, so the dashboard search's `onOpen` and the To-Do rows share one path). Removal trigger is the invoice - a DN does not clear it.
- **`billing` bucket (2026-09-11) - "Bill this month":** rentals and supply contracts with a month nobody has invoiced ([[03-BACKEND-DATA]] `_lpo_billing_due`). Rows carry `kind: "lpo"`, are badged `LPO`, and their tail spells the count in WORDS ("3 months due") because Waleed is colour blind. Their only action is **Open LPO** - the app raises the reminder and he writes the invoice, which is what he chose over a generated draft. **A billing row does NOT open the document preview**: that modal loads through `load_quote`/`load_dn`/`load_invoice`, none of which can fetch a client's LPO, so the row gets its own (`openLpoTodoModal`, below). **Open LPO opens THAT LPO** via `EDIT_LOADER.lpo -> window.openLpoByNo(no, client)` (2026-09-12; it used to be `showLpos`, which dropped him on the whole list). The client rides along on `data-client` because a PO number alone is not a key. Guard: `tests/todo_billing_section.test.js`.
- **The billing row opens its OWN preview (2026-09-12).** `openLpoTodoModal` -
  the client's PO on the left as a rendered page image, its details on the
  right, and the three things worth doing: **Open LPO**, **Make delivery note**,
  **Make invoice**. The shared `openDocModal` cannot be used: it loads through
  `load_quote` / `load_dn` / `load_invoice`, none of which can fetch an LPO.
  The image is requested AFTER the dialog is on screen, so the click never looks
  like it did nothing, and a missing rasteriser degrades to a line of text.
  It is a **document viewer, not a confirmation box**: `size: "xl"` plus
  `.popup.todo-lpo-popup`, which widens it to `min(1340px, 100vw - 36px)`, and
  the page image is capped by the VIEWPORT (`calc(100vh - 230px)`), not by a
  fixed pixel height. It is fetched at **165 dpi**, not the 130 default, because
  A4 at 130 dpi is 1587px tall - less than the box on a tall screen, so it would
  be upscaled (Waleed, 2026-09-12: "the LPO is not visable").
  **All of its pages, with a pager** (`loadLpoModalPages`): page 1 is fetched
  alone and painted as soon as it lands, the rest follow in the background and
  append in order, and the box scrolls. The `‹ 1 / 3 ›` buttons scroll to a page
  and the label follows the scroll. Cheap because each page is a separate cached
  JPEG on the backend.
- **`createInvoiceFromLpo` / `createDnFromLpo`** (`inv.js` / `dn.js`) start the
  document the reminder is asking for. They use what is still **OWED**
  (`lpo_fulfillment().lines[].remaining`), not what was ordered - invoicing 60
  desks again when 60 already went out is the mistake that avoids. An LPO
  carries the client, its number and the lines; the address, receiving account
  and tax status come from the client's own settings via `ClientDefaults.adopt`,
  exactly as the quotation path does.
- **`openDocEditor(kind,no,client)` + `EDIT_LOADER` (2026-08-25; `client` 2026-09-12)** - the ONE place that knows how to open a document in its own editor (`{quote,inv,dn,po} -> window.load*ForEdit`, plus `lpo -> window.openLpoByNo`). The third argument exists for `lpo`, whose key is client + number; the other loaders ignore it. The map used to be copy-pasted into both the row handler and the modal handler, so a new doc type had to be remembered twice; both now call this helper. An unknown/unloaded kind toasts "Can't open this document type here." instead of leaving a dead button.
- **"Open document" in the preview popup (2026-08-25)** - `GOTO_BTN` (`data-act="open"`, `.todo-act.is-goto`) is appended by `modalActsHtml` for **decide / uncosted / unsubmitted / unpaid** (beside their own action, before the eye-off Ignore) and for **ignored** (beside Restore). The popup is a read-only preview, so "go look at the real document" is a reasonable next step from every queue. Deliberately the **secondary** outline treatment - the filled button beside it is the one that clears the queue. **`deliver` and `search` are excluded on purpose**: their PRIMARY action already opens the doc ("Open PO" / "Go to document"), so a second button would be noise. Reuses the existing `data-act="open"` handler, so `opts.onOpen` (dashboard search) still wins over the `openDocEditor` fallback.
- **Collapsible section (2026-08-25)** - `sectionHtml(..., collapsible)` renders the header as a real `<button class="todo-sec-head is-toggle">` with `aria-expanded` + `aria-controls` (keyboard/AT support for free); a `<button>` may not contain an `<h3>`, so `.todo-sec-h3` stands in. `_ignoredOpen` (module-scoped, default `false`) survives the re-render that follows an action. `wire()` toggles the `is-collapsed` class **in place** rather than re-rendering, so opening it doesn't flicker or throw away scroll position. The count badge stays visible while closed, so you can see how much is muted without opening it.
- **Preview modal (Phase X.29)** - `openDocModal(cat, kind, no, date, origCat, opts?)` / `closeDocModal()`, exported as `window.openTodoDocModal`: read-only items table with the section's action(s) in the modal's TOP-RIGHT built by `modalActsHtml(cat)` (Won+Lost / Add costs / Mark submitted / Mark paid, plus the eye-off Ignore; an IGNORED row's modal shows Restore only; **`cat === "search"` shows a single primary "Go to document" button** (`data-act="open"`, `.todo-act.is-open`, no attention actions) used by the dashboard search - its handler closes the modal and calls `opts.onOpen()` so the caller controls navigation, exactly like `"cost"` calls `openInCosting`). `origCat` carries the original category so restore/ignore persist against the right bucket. Same `performAction` the row buttons use; success closes + either calls `opts.onAfterAction()` (dashboard reuse) or reloads To-Do). Data via `api().load_quote/load_dn/load_invoice` (the bridge attaches per-doc `columns`); `previewCols(rec)` uses those columns, appends RATE/TOTAL for a priced DN (`include_prices` - DN persists base columns only), and falls back to deriving columns from row keys for pre-Phase-T docs (preferred order + `HEAD_FALLBACK`, internal costing keys in `SKIP_KEYS` never shown). `previewCellHtml` renders image cells as thumbnails, money/computed (`qty·rate`) right-aligned tabular; footer shows item count + grand total (only when the doc has rates). Table area is the only scroller (sticky `thead`); Escape / backdrop / ✕ close.
- `wire(body)` - row click → opens the read-only preview modal (`openDocModal`, Phase X.29 - NOT Costing directly); action buttons → `set_bid_status` / `set_submitted` / `set_invoice_paid` / navigate ("Add costs" → `window.openDocInCosting`), then `load()`. Ignore/Restore per row via `set_attention_ignore(kind, no, category, bool)`.

**Insights uses this preview too (2026-10-06).** `modalActions` has two Insights categories: `insights` (Open document, primary - the only button) and `insights-unpaid` (Open document + Mark paid). Neither offers Ignore: it mutes a To-Do reminder, which means nothing on Insights. Pinned in `tests/todo_open_and_ignored.test.js`.

**Reads / writes** - No direct DB/file; all via api. Actions call `set_bid_status` / `set_submitted` / `set_invoice_paid` / `set_attention_ignore`; navigation via `openDocEditor` (the document's own editor; the separate Costing screen was retired 2026-09-11). **Mark paid also opens the signed-copy dialog** (`window.SignedCopy.promptAfterPaid("inv", …)`, 2026-08-27) - the same prompt the right panel gives, so the client's stamped copy is asked for wherever the payment is recorded.

**Calls / called by** - Bridge: `dashboard_needs_attention`, `set_bid_status`, `set_submitted`, `set_invoice_paid`, `set_attention_ignore`, `load_quote`/`load_dn`/`load_invoice` (preview modal). Globals: `window.openDocInCosting`, `showCosting`, `showHub`, `setBrand`, `toast`, `OpenPanel`, `DASH_ICON`, `DASH_CASE_ICON`. Entered via `window.showTodo` (dashboard "View all"); the dashboard Needs-Attention cards reuse the modal through `window.openTodoDocModal`.

**Gotchas**
- Sections render **Decide → Bill this month → Deliver & Invoice → Unsubmitted → Uncosted → Unpaid**, each oldest-first from the backend. **Unpaid moved LAST on 2026-10-21 (Waleed):** payment takes the client weeks and there is nothing to do until it lands, so it was crowding out work that can be done today. The dashboard's Needs attention grid uses the same rule (decide, uncosted, unsubmitted, deliver, unpaid). Pinned by `tests/dash_mid_row_layout.test.js`.
- **Buckets that wait on the other party list submitted documents only** (2026-08-10) - **Decide, Unpaid, Deliver**. A draft has no bid state, cannot be overdue and is not awaiting an invoice; it shows once under **Unsubmitted**, whose action ('send it') is the real one. **Uncosted is exempt** - you cost a quotation before you send it. Backend gate: [[03-BACKEND-DATA]] `_submitted_only`. The bid clock runs from `submitted_at`, not the quote date (backend gates this - see [[03-BACKEND-DATA]] `auto_lose_stale_quotes` / `dashboard_needs_attention`).
- **The preview modal MUST carry `dir="ltr"`** (set in JS + `direction: ltr` on `.todo-md-backdrop`): it hangs off `<body>`, which is RTL (Arabic-first shell), so without it the whole table mirrors - columns reversed, actions on the wrong side. Any future body-level overlay needs the same.
- The **Uncosted** bucket is QUT/INV-only by design: quotes carry estimated costing, invoices carry final actual costing, and DNs remain operational/linking documents.
- Layout (Phase X.26): `.todo-view` is `height: calc(100vh - 64px)` (below the 64px sticky topbar) so `.todo-body` (`min-height:0; overflow-y:auto`) becomes a real scroll area; each `.todo-sec-list` is a `repeat(auto-fill, minmax(420px,1fr))` grid capped at `44vh` with its own scroll - full width, more items per section, no clipped/unreachable rows. Styles live in `app_styles.css`, not a separate file.
- **Scrolling (2026-10-21):** `.todo-body` is the page's only scroller; the tables grow to their full height inside it (no `.in-scroll.tall` cap). The old `.todo-section` / `.todo-sec-list` nested scrollers are gone. Pinned by `tests/ui_scroll_contract.test.py` and `tests/todo_table.test.js`.

---

## static/po_link.js  (Phase Y)

**Purpose** - The **PO fulfillment popups**. A DN link marks PO items Delivered; an invoice link marks them Invoiced. Invoice payment is a separate explicit state (`paid_invoices`) and linking from any direction never marks an invoice Paid. Self-contained IIFE; reuses the shared `.todo-md-*` / `.todo-act` modal styling. Entry point `window.openPoFulfillPicker(sourceKind, sourceNo, opts)` (opts.onAfter runs after a successful mark). Called from DN submit and from invoice Paid only when that invoice has no PO link. All picker directions show ranked Suggestions and separate Available/Linked tabs; the invoice-side picker can route through linked DNs.

**Key symbols** (module-local):
- `candidateSections(rows, suggestions, filter, noOf, textOf, rowHtml, emptyText)` - shared renderer for a compact `Suggested` section (reason chips such as item count / quantity match) followed by `All documents`; suppresses duplicate suggested rows while search filters both sets. **The `filter` matches against `textOf(row)`** - all three call sites now pass `(row) => row.search || \`${no} ${summary}\`` so the search covers EVERY item + company (the backend `search` blob, [[03-BACKEND-DATA]] `_candidate_search_blob`), not just the 3-item display `summary`. (2026-07-18 fix: searching e.g. `سكر` when it was the PO's 4th+ item, or a company name, previously found nothing.) `.summary` is still used for the row's display line only.
- `openPicker(sourceKind, sourceNo, opts)` - **Available POs | Linked POs** tabs; Suggestions exist only under Available and linked POs are removed from that dataset. It merges `document_workflow(sourceKind,sourceNo)` into the recent `list_po_candidates(300)` results so an old but still-linked PO cannot disappear. Invoices add a third **Linked delivery notes** route tab, lazily loaded from `list_linked_dn_candidates`; unlinked DNs never appear. Picking a PO → `openDetail`; picking a linked DN → `openLinkedDnInvoiceDetail`.
- `openLinkedDnInvoiceDetail(dnNo, invoiceNo, opts, onBack)` - loads `linked_dn_invoice_detail`, shows DN and invoice item chips, then one selectable section per linked PO with the exact DN-delivered PO rows. Already-invoiced indexes are disabled/labeled; confirmation writes checked indexes through `set_po_fulfillment(poNo, indexes, "inv", invoiceNo, true)`. Back/close/tab changes write nothing; a failed PO write names that PO and keeps the modal open.
- `openDetail(sourceKind, sourceNo, poNo, opts)` - `api().load_po(poNo)` → item table (ITEM/DESCRIPTION/UNIT/QTY/COMPANY, only non-empty columns). Rows occupied by another source start unchecked/dimmed with a `delivered/invoiced · <source>` tag. **Mark delivered/invoiced** calls `set_po_fulfillment`; PO-side DN linking may also mark that DN submitted, but invoice linking never calls `set_invoice_paid`. Shared by both link directions.
- `openSourcePicker(poNo, sourceKind, opts)` (**Phase Y.7**, exported as `window.openPoSourcePicker`) - the **PO-side** flow: **Available DNs | Linked DNs** or **Available Invoices | Linked Invoices**. Suggestions exist only under Available; linked sources are removed from it. `document_workflow("po",poNo)` supplements the recent candidate list so every still-existing linked source appears regardless of age. Picking one loads its items (`load_dn` / `load_invoice`) and → `openDetail(..., {flipSourceStatus:true, referenceDoc, onBack:<same active tab>})`. Wired from `bindPoLinkButtons()` in app.js (`#btnPoLinkDn`/`#btnPoLinkInv`).
  - **`opts.referenceDoc`** (PO-side only) makes `openDetail` render a **`.po-link-ref` strip** of the picked DN/invoice's items (chips, so you see what you clicked) and **pre-tick only the PO rows whose primary name matches** it (`primaryOf` over item/equipment/role/name/description; `refNames` set), instead of ticking all. Non-matching PO items stay unticked to tick manually; already-marked rows stay disabled. Without `referenceDoc` (DN/INV-side flow) the old all-ticked, no-strip behaviour is unchanged.
- `shell(spec)` - opens the popup through `Popup.open` ([[14-UI-CONTRACTS]]) with `dir="ltr"` (same reason as the To-Do modal - the RTL body would mirror the table), and paints `spec.loading` into the `spec.bodyId` element. It creates ONLY that body element and, if given, the `spec.sticky` strip - the title, badge and subtitle are the Popup's. Header text that arrives after a bridge call goes through `h.setSubtitle(...)`; there is no meta element to query.

**Gotchas**
- **Never query an element `shell()` does not create.** `openDetail` used to read a `#poDetMeta` div its own hand-rolled header built; the Popup migration replaced that header and deleted the div but left both reads behind. One was guarded, the other was not, so `openDetail` threw straight after painting "Loading items…" - an unhandled rejection nobody sees and a spinner that never stops, in BOTH link directions (fixed 2026-09-06; the count line is `h.setSubtitle(...)` now, as in `todo.js`). `tests/po_link_detail_renders.test.js` now asserts, for every `static/*.js`, that an id looked up with `querySelector("#…")` is one the app can actually produce.
- Item indexes are 0-based positions into the PO's `rows_json`; the backend keys `po_fulfillment` on `(po_no, item_index, status_kind)`. Reordering a PO's items would drift existing marks (same positional caveat as costing).
- `load_po().fulfillment` dict keys arrive as **strings** over the bridge - `openDetail` checks both `map[i]` and `map[String(i)]`.
- Suggestions are advisory only: candidates outside ±2 items stay in `All documents`, and no suggestion or linked-DN selection writes until the user confirms item checkboxes.
- **Constrained-height invariant (2026-07-17):** `.po-link-list` is the picker scroller; `.po-link-section`, `.po-link-section-rows`, and `.po-link-row` must remain non-shrinking flex children. Removing those `flex: 0 0 auto` guards compresses multi-line rows/reason pills into each other instead of producing scroll. Because each row is a `<button>`, `.po-link-row` also overrides the global fixed button height with `height:auto` and `white-space:normal`; without both declarations its reason pills overflow the row border. `primaryOf` prefers real/custom identity text before quantity/rate fields so compact rows do not display `1` as the item summary.

---

## static/document_workflow.js

**Purpose** - Shared delivery/payment overview for saved PO, DN, and Invoice editors. `window.openDocumentWorkflow(kind, docNo, opts)` loads `api().document_workflow` and renders one horizontal `PO -> Delivery Notes -> Invoices` lane per related PO. Multiple sources remain grouped under their exact PO; the current node is highlighted and other linked nodes open through the existing editor loaders.

**Link actions** - Empty PO stages on orphan DNs/Invoices delegate to `window.openPoFulfillPicker(kind,no)`. Empty DN/Invoice stages under a PO delegate to `window.openPoSourcePicker(poNo,targetKind)`. The workflow module never writes fulfillment itself. It closes before handing off so modal backdrops cannot stack, and each picker `onAfter` re-runs the owning editor refresh callback then reopens Workflow with fresh data.

**Manage link (2026-07-18)** - Each linked relationship node has a corner X (`.doc-workflow-manage`) opening a focused Replace/Remove/Cancel dialog. DN/Invoice nodes target that source's marks on the lane PO; a PO node shown from a DN/Invoice targets only the current source's marks on that PO; the current PO node has no X. Remove calls `clear_po_fulfillment_link`. Replace opens the appropriate existing smart picker with `opts.excludeNo` set to the old source/PO (self-replacement therefore cannot save-then-delete itself), and clears the old exact relationship only in the successful `onAfter`, so cancel/failure cannot destroy the old link. Lifecycle flags are untouched.

**Multiple DNs per invoice (2026-07-18)** - Each invoice card renders lane-local DN contribution chips from `node.delivery_notes`; each chip includes the DN number and overlapping item count. Paid PO indexes without a delivered source render as `Direct to PO`, so mixed direct/DN-backed invoices remain explicit. `Add DN` is a sibling action outside the document-open button (no nested interactive controls); it calls `openPoFulfillPicker("inv", invoiceNo, {initialTab:"dn"})`, then refreshes the owning editor and reopens Workflow after the existing linked-DN confirmation succeeds. No invoice-to-DN data is stored in JavaScript.

**Toolbar state** - `refreshDocumentWorkflowButtons()` reads `getPoWorkflowContext` / `getDnWorkflowContext` / `getInvWorkflowContext`; only edit-mode documents have an enabled Workflow button. The module loads after `po_link.js` and before `open_panel.js` in `templates/index.html`.

**Gotchas**
- Keep the popup PO-lane based. Flattening all DNs and invoices into global columns falsely implies relationships between documents belonging to different POs.
- A DN or invoice can span several POs and therefore legitimately appears in several lanes.
- An invoice can consume several DNs. Contribution chips must use the backend's item-index intersection for that exact PO lane; do not infer them from document names or item text.
- Do not add quotation/source-quote logic here; costing's deal flow is a separate relationship model.

---

## static/data_manager.js

**Purpose** - Data Manager UI (`dataManagerView`), reachable from App Settings → "Open Data Manager". Two modes selected by the entry point: `catalogs` (autofill catalogs) and `documents` (document records). Tabs: Browse, Duplicates (catalog-only), Clients, Backup & Restore.

**Key symbols**:
- `dmState` - the whole module's state object (mode, catalogs/documents metas, sectionKey/Type, section, rows/total, filters year/month/client/search, `sectionFilter` sidebar quick-filter, `selected` Set, `edit`, `dup`, `section_counts`, `expandedGroups`).
- `showDataManager(mode)` - entry (`window.showDataManager`); resets section on mode switch, fetches `dm_list_catalogs()` + `dm_list_sections()` (once), renders sidebar + first section. Hides the Duplicates tab in documents mode.
- `switchTab(tab)` / `bindTabs()` - Browse/Duplicates/Clients/Backup panes; loads clients lazily.
- Sidebar: `groupByLabel`, `shortLabel`, `ensureExpanded`, `renderSidebar`, `countBadge`, `allGroupLabels`, `syncGroupsToggleLabel`, `toggleAllGroups`, `sectionKind(key)` - accordion grouped by `group_label`, with per-family accent colors (`GROUP_ACCENT`), lazy-expanded, single-section families flattened.
- `dmLoadSection(key)` - sets active section; shows year/month/client filters only for document sections; for documents fetches `dm_list_filters(key)`; then `dmRefreshRecords()`.
- `dmRefreshRecords()` - `dm_list_catalog_records(...)` OR `dm_list_records(...)` (500-row cap); updates `section_counts`, prunes `selected` to visible rows, `renderTable()`.
- `renderTable()` - builds thead from `section.display_columns` (kinds: date/datetime/money/count/text), select-all + per-row checkboxes, catalog vs document action buttons; wires all actions.
- `updateBulkUI()` - enables `#dmBulkDelete` + updates its count label.
- Document actions: `openInEditor(pk)` (routes to `window.loadForEdit / loadQuoteForEdit / loadDnForEdit / loadInvForEdit` via `section.editor_route`), `relinkDocPdf(pk)` (`dm_pick_file` + `dm_relink_pdf`), `deleteDocOne(pk)` (`dm_delete_record`).
- Catalog actions: `openCatalogEditModal`, `closeCatalogEditModal`, `saveCatalogEdit` (`dm_update_catalog_entry`), `deleteCatalogRow` (`dm_delete_catalog_entry`).
- `deleteSelected()` - bulk delete: catalog → `dm_delete_catalog_entries(pkDicts)`, document → `dm_delete_records(keys)`.
- `confirm2(title, body)` - maps `window.showModal` onto true/false (value `ok`/`cancel`).
- Duplicates: `rebuildDupCatalogSelect`, `runDuplicateScan` (`dm_find_catalog_duplicates(key, threshold)`), `renderDuplicateClusters`, `mergeCluster(idx)` (`dm_merge_catalog_entries(canonValues, absorbedPks)`).
- Backup/restore: `doBackup()` (`dm_pick_folder` + `dm_create_backup`), `doRestore()` (`dm_pick_file` + `dm_restore_backup`) - reloads catalogs/documents after restore.
- Clients tab: `loadDmClients` (`client_list`), `renderDmClients`, `clRename` (`client_rename`), `clDelete` (`client_delete`), `clAdd` (`client_add`), `clPrompt(...)` (text-prompt modal). Local helpers `clMoney`, `clInitials`; module var `_clClients` caches the list.
- Helpers: `pkKey(pk)` (stable string form of a pk dict for the Set), `fmtDate`, `fmtMoney`, `toast`, `escapeHtml`/`escapeAttr`, `debounce`, `bindOnce` (fires on `DOMContentLoaded`).
- Sidebar quick-filter (`bindOnce` wires `#dmSectionFilter`): Esc clears it, Enter jumps to the first matching section.

**Reads / writes** - Through api only. Touches every catalog (`items`, `companies`, `clients`, per-type item catalogs, `column_memory`) and every document table (`pos`, `quotes`, `dns`, `invoices`) - READ + DELETE + relink. **Backup/restore is PROFILE-SCOPED v2** (backend/data_manager.py, see [[13-PROFILES-BRANDS]]): backup zips a SNAPSHOT of the ACTIVE profile's DB (as `profile/po_tool.db`; `data_manager._zip_db_snapshot` via `sqlite_policy.snapshot` since 2026-10-22 - the databases are WAL and the raw `.db` can miss the newest saves) + its settings + assets + the shared catalog DB, filename `documents-hub-<profile>-backup-*`; restore REFUSES a backup whose manifest `profile_id` differs from `config.ACTIVE_PROFILE`. The frontend calls (`dm_create_backup`/`dm_restore_backup`) are unchanged. Never mutates document PDFs on disk (delete = DB row only).

**Calls / called by** - Bridge (many): `dm_list_catalogs`, `dm_list_sections`, `dm_list_filters`, `dm_list_records`, `dm_list_catalog_records`, `dm_update_catalog_entry`, `dm_delete_catalog_entry(_ies)`, `dm_delete_record(s)`, `dm_relink_pdf`, `dm_pick_file`, `dm_pick_folder`, `dm_find_catalog_duplicates`, `dm_merge_catalog_entries`, `dm_create_backup`, `dm_restore_backup`, `open_path`, `client_list/rename/delete/add`. Uses `window.showModal`, `window.toast`, `window.setBrand`, `window.refreshUniversalSettingsBtn`, and the editor loaders. Entered from settings.

**Gotchas**
- `pkKey` MUST produce identical strings for the same pk on every render or the `selected` Set silently loses rows; it sorts dict keys and JSON-encodes. Catalog bulk delete re-derives the actual pk dicts from `dmState.rows`, so a row not in the current (filtered/paginated) view can't be bulk-deleted.
- The 500-row `limit` in refresh is a hard cap; `section_counts` (sidebar badge) only updates when NO in-section filter is active (search/year/month/client all blank).
- Duplicate merge sends `canonValues` = name_col + every variant_col; DOM order (not stored index) is authoritative for which bank/variant is which. Merge sums usage counts into the canonical entry.
- Restore clears `dmState.catalogs`/`documents` then re-fetches - the currently open section is re-loaded, but any custom sidebar expansion state is preserved (not reset).
- `editor_route` on a document section drives `openInEditor`; if the backend renames a route (`po/quote/dn/inv`) the `map` here must match or Edit toasts an error.
- Deleting a catalog entry does NOT touch already-saved documents; deleting a document row leaves its PDF on disk. Both facts are surfaced in the confirm bodies - keep them accurate.

---

## static/analytics.js

**Purpose** - The **Insights** view (`analyticsView`), rebuilt 2026-08-09 around **money per client**. The old version counted documents; this one answers *"for this client, what did we charge, what did it cost us, what did we keep"* - and the same for the whole company. Backed by [[03-BACKEND-DATA]]'s `backend/insights.py` via two bridge calls; the document-counting `dashboard_analytics` stayed behind for the dashboard's This-Month card and is deliberately **not** called from here.

**Two scopes, one page.** `state.client === null` renders the all-clients view; picking a card or a leaderboard row renders that client's deep dive. Both share the same filter row and the same chart builders.

**Key symbols**
- `state` - `{span, year, month, client, overview, detail, loading}`. `span` ∈ 1/3/6/12 months; `year`/`month` are the window's **END**, so the month stepper walks back through history at any span.
- `showAnalytics()` / `window.rerenderAnalytics` - unchanged entry points (app.js opens the view and re-themes it).
- `load()` - one `insights_overview(span, year, month)`, plus `client_insights(...)` when a client is picked; adds/removes `.is-loading` around the fetch.
- `render()` → `renderRail()` + (`renderClientView()` | `renderAllView()`).
- **The Invoices section (2026-08-26)** - between "Top items" and "Documents" in the client view: three KPIs (Paid · Not paid yet · Losing money) over a table of every invoice in the period, and under each losing invoice a `tr.in-why` row spelling out the cause. `invWhy(x)` composes it from the backend's diagnosis ([[03-BACKEND-DATA]]): named lines below cost with their dollar holes, document-level extra costs, and uncosted lines - which since 2026-10-21 are counted at an **estimated** cost (40% markup, `config.UNCOSTED_MARKUP`), so the reason says the figure rests on an estimate. The overview's "Costs missing" note and the client note say the same (close, not exact) instead of "overstated". A row with none of the three still gets a sentence ("the margin was thin everywhere"), or the Why tag would read as a bug.
  - Every state is a WORD - Paid / Unpaid / Loss / No costs. `.in-pill.neg` is the only red pill in Insights and there is deliberately **no green counterpart** ([[waleed-colour-blind]]). Pinned by `tests/insights_invoices_section.test.js`.
- **Top items excludes delivery notes billed as one line (2026-08-25)** - the backend flags them (`insights._is_document_line`, [[03-BACKEND-DATA]]) and returns what it set aside as `dn_lines`; `renderClient` paints an `in-note` under the table naming the count, the money, and how many of them are uncosted. The note is not optional: the table would otherwise be a silently filtered list, and those lines are costed at the 40% estimate until their DNs are costed (`dn_lines.uncosted` counts them by the item's `estimated` flag, not by a zero cost).
- `renderRail()` - the client picker: an "All clients" card plus one card per client (profit, margin, counts, a profit-relative bar), filtered by `#inClientSearch`.
- `renderBoard(clients)` - the sortable **Every client** table (`BOARD_COLS`, `boardSort`); rows open the client. It is also the table twin the charts owe (see below).
- **The client view's tables sort, and their document rows open the document (2026-10-06).** Waleed: "make the titles of the tables clickable so I can sort for unpaid invoices", and "make these documents lines clickable". Top items, Invoices and Documents are each drawn by a closure inside `renderClientView` (`drawItems` / `drawInvoices` / `drawDocs`) into `#inItemsTbl` / `#inInvTbl` / `#inDocsTbl`, so a header click redraws only that table, not the charts. Shared helpers: `tableSort` (per table, kept while the app runs), `sortRows` (backend order until a header is clicked; blanks last both ways; ties keep their order; text compares naturally, so INV-9 before INV-10), `sortHead` (arrow AND `aria-sort`), `wireSortHead` (same header reverses; a new one starts at the column's `first`: A-Z / Unpaid-first, otherwise biggest or newest first), `dateVal` (D/M/YYYY or ISO - never `new Date(string)`, which reads 4/6/2026 as April). The invoice **Status** column sorts Unpaid first, a losing invoice ahead within each; Documents sorts unpaid invoices, then paid, then quotations by state, then delivery notes, whose money cells are blank. Rows carry `data-doc-kind` / `data-doc-no` / `data-doc-paid` / `data-doc-date` (`docRowAttrs(kind, record)` - it takes the record, so `insights_view.test.js`'s escaping rule sees no raw field) and open on click or Enter / Space (rows are `tabindex=0`). **A row opens the shared document PREVIEW, not the editor (same day, Waleed: "so I can go back easier")** - `openDoc` calls `window.openTodoDocModal` (todo.js, the popup the To-Do and the dashboard use) with category `insights`, or `insights-unpaid` for an unpaid invoice. Esc / X / the backdrop close it and the sorted table is exactly where it was; "Open document" goes on to the editor; "Mark paid" (unpaid invoices only) runs the To-Do's own `performAction("paid")` and then `refreshInPlace` re-reads Insights and puts the scroll back on whichever ancestor actually scrolls (`scrollerOf`). Only without the To-Do module does a row fall back to `DOC_LOADER` -> `loadInvForEdit` / `loadQuoteForEdit` / `loadDnForEdit`. An invoice's "Why" line opens the same invoice and travels with it in any sort. Pinned by `tests/insights_sort_and_open.test.js` (10 checks, 8/8 mutation-checked); verified in headless Chrome on HDEC's real data.
- *(gate.js, 2026-10-07)* `setBar` also drives the ring round the seal (`setSweep` -> `--gate-sweep` on #gate; empty again after `openDoors`; the reload restores it from the note's `stage`, painted on the first frame by index.html's inline script). `swapBrand(to)` runs the colour wash + logo blur during a switch; `revealApp(delay)` settles the dashboard as the doors part (the launch doors: `HOLD_MS` after `gate_ready`). `HOLD_MS` = 200 = `launch_gate.HOLD_MS` (pinned).
- **Monthly statement (2026-10-06)** - `#inStatement` beside "All clients" in the client hero calls `openStatement(name)`: a Popup with the months that client has issued invoices in (`client_statement_months`), the Insights month preselected when it has any, else the latest; "Make PDF" calls `make_client_statement`, opens the file (`open_path`), shows pages / counts / path / missing files, and turns into "Show in folder" (`reveal_in_folder`). Labels are built with `textContent` and string concatenation, not template strings, so `insights_view.test.js`'s escaping rule stays strict. Backend: `backend/statements.py` [[03-BACKEND-DATA]].
- Chart builders: `moneySeriesChart` (charged/spent/profit columns), `marginChart` (margin %, its own chart), `hbarChart` (single-series horizontal bars, direct-labelled), `categoryChart` (charged vs spent per invoice category), `spark`.
- Helpers: `kpi`, `sect`, `meter`, `cellbar`, `money`/`money2`/`moneyShort`/`pctText`/`dateShort`, `trendText`, `theme()`/`tok()`, `mount()`/`destroyAll()`/`empty()`, `syncChrome()`, `stepMonth()`, `pickClient()`.

**Reads / writes** - Read-only through the bridge: `insights_overview(months, end_year, end_month)` and `client_insights(name, months, end_year, end_month)`. No DB or file access.

**The design rules this file encodes** (guarded by `tests/insights_view.test.js`; the reasoning is the `dataviz` method)
- **ONE filter row** (`#inMonth` + `#inSpan`) scopes the entire page - never a per-card filter.
- **Never a dual-axis chart.** Money and margin-% are two separate charts; two y-scales on one plot invent a correlation that is not in the data. (`annotations: { yaxis: [...] }` is a reference line on the one scale, not a second scale.)
- **A legend for ≥ 2 series** (ApexCharts' own, which also toggles series); single-series charts carry **direct labels** instead - those labels are also the contrast "relief" the light-mode palette owes for aqua and yellow.
- **Every chart has a tooltip AND a table twin on the page** (leaderboard / items / documents), so no value is reachable only by hovering.
- **NO red/green pair signals direction** - the user is colour blind. `trendText` emits an arrow *and* a word; a negative amount always carries a literal `−`; `--in-neg` is only ever used beside that sign.
- **Series colours are CSS tokens** (`--in-s1…s5`, `--in-neg`) read via `tok()`, never hex literals - the dark theme re-steps them for its own surface ([[08-STYLES-TEMPLATES]]).
- **Refetch holds the previous render** at reduced opacity (`.is-loading`); it never blanks `#inBody` first.

**Gotchas**
- `charts[id]` holds live ApexCharts instances; `mount()` destroys the previous one first and `render()` calls `destroyAll()` - skip either and you leak charts and stack tooltips. Re-theming is a full re-render, not a live update.
- The app's global `button { display: inline-flex; height: 40px; white-space: nowrap }` rule bites every button this view adds. `.in-ccard`, `.in-back`, `.in-span button`, `.in-month button` each undo it explicitly - a new button here needs the same.
- ApexCharts bar animations are `requestAnimationFrame`-driven: in a **hidden/non-compositing** window they never advance and bars render at zero height. That is a screenshot/headless artefact, not a bug - force reduced motion when capturing.
- The client month rows must carry `margin`; the margin chart reads it directly. It derives a fallback if missing, but the backend test pins the key (it shipped missing once and printed "average NaN%").
- `window.rerenderAnalytics` re-renders from `state`, so a theme flip re-reads the CSS tokens. Do not cache `theme()` across renders.

---

## static/item_master.js

**Rebuilt on the RECORD (2026-08-16).** The screen reads `mi_list` / `mi_get` / `mi_save` / `mi_merge` / `mi_delete` - `master_items`, one row per item - instead of folding the four document catalogs. Waleed's spec: "one place that holds every item… name in English, name in Arabic, description, unit, price, cost and image - nothing more, all under one item number".
- **Columns are the item's fields**: check · number · English · Arabic · description · unit · price · cost · picture. Margin / Used-in / Uses / Last-used were dropped from the LIST - they describe documents, not the item.
- **The source pills are gone.** "Purchase Order", "quotation materials", "quotation manpower" existed only because one item was stored four times. The filter row now offers what an item is still MISSING (no Arabic name, no cost, no picture…) with counts - that is what you can act on.
- **The right panel IS the item**: every field editable in place, price and cost each with their own USD/IQD chip, saving through `mi_save`. It never converts; both figures are stored as typed.
- **`New item` adds one BY HAND (2026-08-19)** - `#imNewBtn` → `openNewItem()` → the SAME `renderRecord(it, isNew)` panel with blank fields, saving through `mi_create`. One form, not two, or a field added to the record would be forgotten on the create side. A duplicate is not a shouted failure: the backend returns `already`, and the panel opens that item instead. "Used in" is omitted for an item no document has used yet.
- **Description and unit exist ONCE PER LANGUAGE (2026-08-17).** Waleed: *"for each arabic item there is description in arabic and unit in arabic so that also needs to be included in each items details"*. The PO prints كيلو where the invoice prints KG, and one box per field kept whichever language it met first ([[03-BACKEND-DATA]] has the storage side).
  - The panel has four boxes - *Description (English)* / *الوصف بالعربي* / *Unit (English)* / *الوحدة بالعربي* - and `mi_save` is sent all four by name. There is no single `unit` box left; two of them would drift.
  - The LIST keeps its two columns and **stacks** the languages inside each cell (`bilingual(en, ar)`, `.im-b-en` / `.im-b-ar`): two more columns would not fit at this width. English on top, Arabic under it, each line clipping on its own so one long value cannot hide the other; a blank side prints nothing rather than a dash on every row. `title` carries both in full.
  - Colour-blind rule: the two languages are told apart by **position and script**, never by hue - each is its own line, and `.im-b-ar` uses `--text-muted` (not `--text-subtle`, which is 2.56:1 on white and would make the Arabic unit the faintest thing on the screen). Pinned in `tests/master_data_ui.test.js`, which calculates the ratio from the live tokens.
  - A new **"No Arabic unit"** chip lists the items that have an Arabic name but no Arabic unit - 11 today. Asked only of items that *have* an Arabic name; counting all ~500 English-only ones would bury them.
- **`Possible duplicates` chip (2026-08-19)** - not a gap, the opposite: items that look like the same product stored twice. Tick two and Merge. It replaces the separate *Review materials* panel, which was built around catalog ROWS and could only look inside one document type at a time.
- **"Used in" was RESTORED after being removed with the rest of the old panel.** That was a mistake - Waleed asked for it two days earlier ("make the docs that show there clickable… a popup first and then a button to go for document"). His "nothing more" was about what the record STORES; this is a question asked of the documents, keyed by whichever name they hold (`loadUsedIn(it.name_en || it.name_ar)`).
- Opening an item moves focus into the panel heading (`tabindex="-1"` + `.focus()`), or a keyboard user is left standing in the list. Caught by `tests/master_data_ui.test.js`, not by review.
- ~250 lines of the old panel were deleted rather than left unreferenced.



**Purpose** - The **Master Data** view (`itemMasterView`), 2026-08-11; renamed from "Item Master" on 2026-08-13. **Only the user-facing strings changed** - the view id, the `im_*` bridge methods, `static/item_master.js` and the `.im-*` classes deliberately keep their names, because renaming identifiers is a large, risky refactor that shows the reader nothing.

**Used in - the documents holding an item (2026-08-14).** The detail panel's "Stored rows" list shows CATALOGS ("Quotation Materials", "Delivery Note"), not documents, so it could say an item was remembered without ever saying where it came from. `loadUsedIn(name)` fills `#imUsedIn` from `im_item_documents`; each entry is a real `<button>` (`.im-doc`) so it is keyboard-reachable and announced as an action, not a clickable div. Clicking opens `showDocumentPeek` - the popup answers the usual question in place (number, date, client, and this item's qty/unit/rate on that document) and only THEN offers **Open document**, because leaving abandons the panel being worked in. The Open button is omitted entirely when `DOC_ROUTE` has no loader for that kind. The async reply is discarded if the panel has moved to another item (`host.dataset.item !== name`). Pinned by `tests/item_master_used_in.test.js`.

**Material Duplicate Review - REMOVED 2026-09-05.** `#imMaterialDuplicatesBtn` and `openMaterialDuplicateReview()` are gone. The backend behind them read the four catalogs dropped 2026-08-20, so the panel answered "0 groups" every time - it did not merely stop working, it told the user they had no duplicate materials while the live "Possible duplicates" chip was finding 64. The live duplicate view is that chip, backed by `master_items.duplicate_groups()` ([[03-BACKEND-DATA]]).

**Pushing a cost into saved documents (2026-08-13).** `showCostTargets(name)` → `im_cost_targets` lists every document in this profile holding the item, with its current cost, the proposed one and its condition IN WORDS ("no cost yet" / "different cost" / "submitted"). Already-submitted documents are listed but start **unticked** - correcting a cost you never entered is routine; quietly restating one on a document you have already sent is not. Applying goes through `confirmBox` and then `im_apply_cost`. Three steps on purpose: ask, choose, confirm.

**UI review pass (2026-08-13).** Three measured failures were fixed: rows are tabbable but had NO focus style (the app's only rule is `button:focus-visible` and a row is a `<div>`); `--text-subtle` is **2.56:1** on white and was the item description and empty state (now `--text-muted`, 7.58:1 / 7.23:1); and in DARK mode the active condition chip inherited `color:#fff` over `--warn` for **2.23:1** - light mode was 5.02:1, so a light-only check missed it (`dark.css` now overrides it to `#16213A`, 7.16:1). Also: rows are `role="row"`/`"cell"` not `role="button"` (a button may not contain the row's checkbox); column headers sort with `aria-sort` and keep the dropdown in step; the `▣` glyph became the shared `PIC_SVG` icon (a SECOND copy of it in the stored-rows list was found by the test, not the review); focus moves into the panel on open and back to the row on close; search and both chip rows are named; loading sets `aria-busy` and a `role="status"` region reports the count. Pinned by `tests/master_data_ui.test.js`, which CALCULATES the contrast from the live tokens rather than asserting a colour.

**Every column sorts, both ways (2026-08-26).** `SORTABLE` maps all eight headers to a sort key (was four, and `No.` pointed at a key the backend did not have), `state.desc` carries the direction, and clicking the column already sorting REVERSES it - a different column starts at its own default instead of inheriting the last one's. The arrow points ▼ or ▲, `aria-sort` says which, and `.im-sortbtn.is-on` is what actually paints the active header: `aria-sort` sits on the columnheader SPAN, so the older `.im-sortbtn[aria-sort=...]` rule never matched anything. The `desc` argument is NEW on `mi_list`, so the call is wrapped in a try/catch that falls back to the five-argument form - F5 without a restart pairs new JS with old Python, and an unguarded extra argument throws and leaves the screen empty.

**Possible duplicates are drawn as PAIRS (2026-08-26).** The chip used to filter the 66 flagged records into the normal list in use-count order, so the two `Chest Freezer` rows sat five apart and the reader had to hunt for them. The backend now groups them ([[03-BACKEND-DATA]]); `renderList` draws `.im-dupe-sep` above each group (the matched name + how many records), tags the first row **Original** and every other **Duplicate of M-000xx**, and tints the copies. The words carry it - an indent alone is invisible to a reader who cannot see the tint.

**There is NO exchange rate on this screen (2026-08-13).** `IQD_PER_USD` does not appear in `item_master.js` at all, and `toCcy`/`toUsd`/`money` were deleted - `moneyRaw` prints an amount in the unit it already is. Both the price and the cost are stored as typed and converted only by a document, at its own rate. The inputs bind to `price_native`/`cost_native`, never to the derived comparison figures, or reopening the panel would silently restate a dinar figure as dollars. Pinned by `PriceKeepsItsUnit` in `tests/master_data_backend.test.py`.

**The cost is typed and shown AS IS (2026-08-13).** The Cost field holds `cost_native` and its chip DECLARES the unit rather than converting - there is no deal behind a catalog entry, so any rate used here would be a guess. `moneyRaw()` prints an amount in the unit it already is. The list's Cost column shows the native figure; Margin still uses the derived comparison figure and says so in a tooltip when the cost is in dinars. **The PRICE is still USD-canonical** - the sell side (`invSellFromUsd`, grand totals, the PDF generators) has not moved, so `rate` is still converted on the way in.

**Cost and price have INDEPENDENT display units (2026-08-13).** `money_ccy` is the COST unit, `price_ccy` the PRICE unit, and the two chips flip separately - buying in dinars while selling in dollars is ordinary. There is no safety reason to couple them: both figures are stored in USD, so `margin` is computed from the USD pair and is unaffected by either. `price_ccy` falls back to `money_ccy` when unstored, so items from before the split still read the same; `save_item` then PINS both, or switching the cost would silently drag the price with it. Panel state: `detailCostCcy` / `detailPriceCcy`, `renderDetail(it, {cost, price})`.

**Money is shown and typed in USD or IQD (2026-08-13).** The stored figures stay USD - `margin`, the sort orders and the paste resolver all COMPARE items, and they can only do that in one unit - so `money_ccy` records only which unit the human reads, and `toCcy`/`toUsd` convert at `window.IQD_PER_USD` on the way out and back. What crosses the bridge is ALWAYS dollars. The Price and Cost fields carry the same `.cost-ccy` chip as the three editors, flipped together by `renderDetail(it, forceCcy)`.

Originally, 2026-08-11. Replaces the dashboard's "Autofill Suggestions" destination (`showDataManager("catalogs")`): thirteen lists over seven tables in which the same item appeared up to four times. One row per item; the detail panel edits every stored row behind it at once. Backed by `backend/master_data.py` ([[03-BACKEND-DATA]]).

**Key symbols**
- `showItemMaster()` (`window.showItemMaster`) - nav entry; the dashboard card and `ALL_VIEWS` both point here.
- `state` - `{search, source, flags[], sort, selected, checked:Set}`. `load()` is sequence-guarded (`state.seq`) so a slower keystroke's response can never overwrite a newer one.
- `renderChrome()` - the ONE filter row: source chips + condition chips, both with live counts.
- `renderList()` / `renderBulk()` - the table and the multi-select bar (merge / forget).
- `openDetail(name)` / `renderDetail(item)` - stats, sources, alternative spellings, the remembered picture, the edit form, and every stored row with its own "Remove here".
- `openHealth()` - counts per condition (each a link that applies the filter), the picture store, and the per-cell index with a Clear action.
- `defaultMaterialDuplicateSelections(plan)` / `openMaterialDuplicateReview()` - pure safe-default model plus the preview, canonical choice, confirmation, apply and refresh flow; `window.__itemMasterDuplicateReviewInternals` exposes only the default helper for Node regression coverage.
- `confirmBox` / `pickCanonical` - wrappers over app.js's `showModal`, which takes `{title, body, buttons, html}` and **resolves to the chosen button's `value`** (it is not a callback API).

**Gotchas**
- **`showModal` resolves on close**, so any button INSIDE a modal body must be wired on the next tick while it is open - `openHealth` deliberately does not `await` the modal before wiring.
- The Material Duplicate Review dismisses its first modal **before** it calls `confirmBox`; `showModal` owns one global backdrop and cannot safely nest a confirmation inside an open review dialog.
- Search is debounced 180ms and the response is sequence-checked; `.is-loading` dims the previous render rather than blanking it.
- Colour never carries meaning alone (the user is colour blind): every condition is a word plus a count, and the warning tone appears only beside the word "unusable".
- Every destructive action states that **saved documents are not touched** - this view edits what autocomplete offers, nothing else.
- Edits are **cross-profile**: the catalog lives in `data/shared/library.db`, so a rename here changes both companies' suggestions.

---

## static/client_master.js - Master Data ▸ Clients (2026-09-05/06)

**Purpose** - The `clientMasterView` screen: the Clients half of Master Data, beside Materials. A searchable list of client identities (Active / Archived / All) with a detail form for identity + other names, contacts, addresses, and this company profile's payment preferences. It calls the `cm_*` bridges only, and it does not touch `static/item_master.js` or material storage.

**Key symbols** - `$`, `api()`, `esc()` (all rendered values go through it), `normalise(record)` (fills a blank shape so the form never reads `undefined`), `message(text, error)`, `setDirty()`, `canLeave()` (Popup confirm on unsaved edits), `show()`, `load()` (list, `state.listSeq` guarded), `select(identity)` (detail, `state.detailSeq` guarded), `renderList()`, `renderForm()`, `renderEmpty()`, `edit(event)`, `save()`, `archive()`, `leave(then)`, `init()`. Exposes `window.ClientMaster` and `window.showClientMaster`.

**Navigation** - The Materials screen's `#imClientsTab` opens it; `#cmMaterials` goes back to Materials; `#cmBack` goes to the hub. `clientMasterView` is in `ALL_VIEWS` and `app.js` consults `ClientMaster.canLeave` before navigating away.

**Gotchas**
- **A record id is a uuid hex string.** `Number(row.dataset.cmId)` is `NaN` for every real id - that shipped briefly and made the entire list unclickable while every direct `ui.select(...)` test passed, because those bypass the click handler. `client_master_ui` now clicks a real row with a real uuid.
- Both list and detail carry a **sequence guard** (`listSeq` / `detailSeq`). A slow response for a client the user already navigated away from must not paint over the newer one.
- `save()` sends `updated_at` (optimistic concurrency) and `profile_id` (the profile whose bank list the form was filled from). The backend refuses a stale token or a switched profile rather than writing to the wrong company.
- Preferences on this screen are **per company profile**; identity and other names are shared. The form says so - do not "simplify" that away.

---

## static/client_defaults.js - client settings applied on selection (2026-09-06)

**Purpose** - When a client is PICKED on a quotation, DN or invoice, that client's saved settings fill the document's blank fields: the printed client name, address, receiving account, and tax status. There is no button - an earlier version had an "Apply defaults" button beside the client field; it was removed 2026-09-06 because the useful moment is the moment you choose the client.

**The trigger is the client field's `change` event, and that is the whole safety story.** The browser fires `change` for a real user choice - picking from the `<datalist>`, or committing a typed name - and NEVER for a value assigned in code. Loading, restoring, duplicating and importing all assign in code, so none of them can trigger this. Do not "improve" it to `input`: that fires per keystroke and would apply on a half-typed name.

**What may be filled** - `replaceable(kind, field, current)` allows a write only when the field is untouched by the user AND one of:
- it is empty; or
- it still holds exactly what THIS module last wrote (so switching client replaces the previous client's address, and only that); or
- the document is **fresh** and it still holds the editor's starting value.

`fresh(kind)` = `mode === 'new'` and no `sourceQuote`/`sourceDn`/`sourcePo`. On a loaded, duplicated-from or imported document the starting values are the DOCUMENT'S, so only genuinely empty fields are filled.

**The printed name and the code chip (2026-09-11)** - Waleed's ruling: a document prints the client's LEGAL name, every time, and there is no per-document picker. So picking "CCCC ltd", "هيونداي" or the legal name itself all leave the same text in the box - `result.print_name`. This is the one write that ignores `replaceable`: the substitution IS the feature. The escape hatch for a one-off spelling is a name that is not on file, which is left exactly as typed.

Because the box then says "China Communications construction company Ltd. Iraq Branch" and the number says TDC, the label carries a **code chip** (`#qClientCode` / `#dnClientCode` / `#invClientCode`, styled `.doc-client-code` in `app_styles.css`) showing the short code the number and the folder are built from. If the chip is wrong, the number is about to be wrong too, and you can see it before saving. `paintCode` sets it from an applied lookup; `refreshCode` reads it for a document that was LOADED (those assign the client in code, so no `change` fires) - `reset` clears the chip and schedules that read for the next task, which is why no load path needed a new call.

**`adopt(kind, supplied)` - a document built from another one** - every import assigns the client in code, so no `change` fires and nothing would be applied. `adopt` runs the same application and declares the document fresh **for the fields its source did not supply**. `supplied` is the list of fields that really came across:
- `adopt('inv')` - a **PO**: lines, a company and a reference, and nothing else.
- `adopt('inv', ['address'])` - a **quotation or delivery note**: a client and an address, but no receiving account and no tax status. A delivery note has no bank on it at all.

**Why it is per FIELD** - it was not, and Waleed found the hole: an invoice made from `DN-HDEC-066` came out on the profile's default account instead of HDEC's, because the whole document was treated as "not fresh" for the sake of the one field the DN did supply. `fresh(kind, field)` now answers per field. Note the address is usually protected by the baseline rule anyway (the DN's address is not the value `reset` recorded); `supplied` is what keeps it protected if the `reset` call is ever moved after the assignment, and the lifecycle test covers exactly that case.

**Key symbols** - `adapters` (per editor: client input id, address input id, code chip id, `getState`), `sessions` (per editor: `baseline`, `written`, `touched`, `applying`, `adopted`, `seq`), `reset(kind)`, `fresh(kind)`, `replaceable(kind, field, current)`, `paintCode(kind, result)`, `refreshCode(kind)`, `mark(event)` + the document-level `input`/`change`/`click`/`keydown` capture listeners that populate `touched`, `applyFor(kind, adopted)`, `init()`. Exposes `window.ClientDefaults = {reset, apply, adopt}`.

**`reset(kind)` means "a different document is in the editor now."** Every path that replaces the document calls it: `initNewQuote`/`initNewDn`/`initNewInv` (as their LAST statement, so the baseline they record is the settled one), and `load*ForEdit`, `restore*Draft`, `*MakeDuplicate`, `importQuoteInto*`, `importDnIntoInv`. Paths that call `await initNew*()` first - `createDnFromPo`, `createInvoiceFromPo`, `createInvoiceFromDnNo`, `createInvoiceFromDnAsLine` - inherit its reset and must NOT repeat it.

**Gotchas**
- **The sell currency is reported, never applied.** Changing it needs an exchange rate only the user can confirm, so `setInvSellCurrency` opens the peg prompt - and a rate dialog appearing because you picked a name is an interruption, not a convenience, and it re-prices the invoice. Instead the toast says "HDEC is usually billed in IQD." and the user toggles it themselves. `client_defaults.js` does not call `setInvSellCurrency` at all; a test asserts that.
- **An unknown name must fail silently.** Typing a client who is not in Master Data yet is ordinary. `client_master.defaults_for` returns a `known` flag for exactly this: `known: false` (not a client, or archived) is silent; `known: true` with an error (a receiving account missing from this profile) is shown, because that one is actionable.
- `touched` exists for the case the other rules miss: a value the user set BY HAND that happens to equal the baseline. Toggling the currency switch to IQD and back to USD leaves `sellCurrency` at its baseline, but it is now a decision - so no note is shown either.
- Every write is wrapped in `s.applying = true`, because the `input`/`change` events this module dispatches would otherwise mark its own fields as user-touched.
- `seq` + a re-read of the client field guard the await. A lookup that resolves after the user picked someone else, or after the document was replaced, is dropped.
- Applying marks the document dirty; a note on its own does not.
- The printed-name write dispatches `input`, never `change`: `change` is what called `applyFor` in the first place.

---

## static/client_required.js - add a client without leaving the document (2026-09-11)

**Purpose** - Waleed's ruling: "no new clients shall be accepted when typing in client name in all editors." The REFUSAL is the backend's (`api._known_client_error`, called first thing by `save_quote` / `save_dn` / `save_inv`), so it cannot be walked around. This module is the way OUT of it: a dialog that adds the client on the spot, after which the editor saves again. Sending him to Master Data with a half-finished invoice on screen is friction he would route around within a week.

**The contract** - `window.ClientRequired.offer(result, kind)` returns a promise for a boolean: **true means the editor should save again**. Anything that is not a client refusal returns false untouched, so an unrelated failure still reaches the editor's own error handling - this must never swallow a real error.

**How a refusal is recognised** - the PRESENCE of `unknown_client` on the result, never the error text. An error string is for a person to read and will be reworded one day.

**Three shapes of refusal**
- a name nobody has filed -> the add dialog, pre-filled with what was typed;
- `archived_client` -> a confirm that RESTORES the existing client (`cm_archive(name, false)`). Never a second record: that would split one client's history in two;
- a blank client -> false, with no dialog. The editor's own message already says to pick a client or N/A, which is the whole answer.

**The dialog** - the same identity fields the Clients screen has (short name, legal name, Arabic name, other names) plus the receiving account, written through the same `cm_save`, so there is ONE way a client comes to exist and one set of identity rules. Contacts, addresses and payment terms are filled in later on the Clients screen. Styles live beside that screen's in `client_master.css` (`.client-required-popup`), reusing its field rules rather than growing a second set.

**Gotchas**
- **The typed spelling is kept as an alias.** If he types "Zenith Trading" and shortens the record to "Zenith", the original is added to `aliases` - otherwise the very next document typed the same way is refused again for the same name.
- **The primary action uses `keep: true`,** so a refusal from the client store (a name another client owns) leaves the dialog open with what he typed still in it, showing why.
- **`inv.js` and `quote.js` REBUILD their payload before saving again.** Adding a client also rewrites the client box to the printed legal name, so the first payload is stale. `payload` is a `let` for exactly this reason.
- **The no-client record.** Work that genuinely has no client uses the client named `N/A`: a real record, not a magic value, so it needs no special case anywhere. It numbers and files as `NA` (`document_code` strips the slash), appears in the datalist like any client, and `api._printable_document` blanks the client line so the page does not print "N/A" to whoever reads it.

---

## static/clients.js - RETIRED 2026-09-11

The per-client "Client Profiles" screen (dashboard card + Settings -> Clients ->
`clientsView`) was removed: everything it showed already exists where the work
happens.

| It showed | Already in |
|---|---|
| per-client totals (invoices, quotes, DNs, invoiced total) | **Insights** - `insights.py` builds the same `by_client` breakdown |
| that client's documents, click to open | **the Open panel** - it has client filter chips for QUT / DN / INV |
| default address, payment terms | **Master Data -> Clients** (`client_master.js`), which also EDITS them |

Not to be confused with `static/client_master.js`, which is very much alive -
that is the Clients tab beside Materials in Master Data, holding 22 client
records, 26 aliases and 4 payment preferences.

Left in place: `client_profile` and `client_list`. `client_list` is still used
by Data Manager and the LPO screen, and `client_profile` is exercised by five
tests guarding client rename and profile isolation. The `.cl-*` styles stay too
- Data Manager uses `cl-avatar` and several of those classes are still in live
markup.

## static/signing.js

**Purpose** - Digital Signatures & Stamp frontend (Phase K.1). (1) App-Settings signing card (upload/preview/label/remove for 3 signatures + 1 stamp + size inputs); (2) the universal Sign modal (per-slot signature picker + stamp checkbox); (3) `window.openSignModal` + an editor registry so every editor's Sign button opens the modal and writes back an overlay.

**Key symbols**:
- `refreshSettingsUI()` - `api().signing_get_settings()` → fills stamp/sig previews from **backend-supplied data URIs** (`s.stamp.data_uri` / `signatures[i].data_uri`; no static URLs, no cache-busting), labels, and size inputs. `_showPreview(img, emptyEl, src)` handles broken-image fallback.
- Upload/delete: `uploadStamp(file)`/`uploadSignature(slot, file)` (file→data-URI→`signing_upload_stamp`/`signing_upload_signature`), `deleteStamp`/`deleteSignature(slot)`.
- `saveLabelsAndSizes()` - `api().signing_save_settings({signatures:[{label}×3], size:{stamp_w_mm, stamp_h_mm, sig_w_mm, sig_h_mm}, auto:{enabled, stamp, sig_index}})`; exposed as `window.signingSaveLabelsAndSizes`. Also piggy-backed on the App-settings Save button - via a **delegated click listener on `#btnSettingsSave` inside `bindSettingsCard`**, NOT a call from `app.js saveSettings()` (grepping for the `window.` export alone makes it look unwired). Returns `true`/`false`.
- **Auto-sign card (2026-07-24)** - `#sigAutoEnabled` / `#sigAutoStamp` / `#sigAutoSig` in the Settings → Sign panel, read in `refreshSettingsUI` from `settings.auto` and written back in `saveLabelsAndSizes`. `sig_index` is `null` for the "No signature (stamp only)" option, and the `auto` block is only added to the payload when at least one control exists, so a settings screen without the card can't wipe the stored block. Backend contract: [[04-BACKEND-DOCGEN]] `overlay_for_save`.
- `bindSettingsCard()` - event-delegated (on `document`) upload/remove/save/settings-open handlers + file-input change handlers + a MutationObserver on `#settingsView` `hidden` to refresh previews when settings opens.
- Sign modal: `openSignModal(opts)` (`window.openSignModal`) - fetches settings + `api().signing_slots_for(docType)` (unless `activeSlots` passed), builds one row per slot with a signature `<select>` + "Include stamp" checkbox; empty state if nothing uploaded.
- `readSelections()` - reads per-slot `{sig_index, stamp}`.
- `applySignModal()` - `api().signing_roll_overlay(docType, sels)` (backend rolls randomization), calls `signState.onApply(overlay)`, refreshes the open panel.
- `clearSignModal()` - `onApply(null)`.
- `bindSignModal()` - wires Apply/Cancel/Clear + Esc/Ctrl+Enter.
- Editor registry: `signingRegister(opts)` (`window.signingRegister`) - editors register `{docType, getOverlay, setOverlay, getDocNo, getActiveSlots}`; `bindEditorButton(buttonId, docType)` wires `btnPoSign/btnQSign/btnDnSign/btnInvSign` to open the modal and write back via `setOverlay`.
- Helpers: `slotLabel`, `isOverlayEmpty`, `fileToDataUri`, `escapeHtml`/`escapeAttr`.

**Reads / writes** - Signature/stamp PNGs live **per-profile** at `config.ASSETS_DIR/'sigs'` = `data/profiles/<id>/assets/sigs/` (`sig_1.png`..`sig_3.png`, `stamp.png`); `static/sigs/` is only a legacy-migration fallback ([[13-PROFILES-BRANDS]]). Labels + sizes in the signing settings section (the active profile's settings.json). The frontend never touches these paths directly - previews arrive as data URIs from `signing_get_settings()`. Overlay JSON is stored per-document by the editor's own save path in doc meta (NOT here). See [[03-BACKEND-DATA]] signing api.

**Calls / called by** - Bridge: `signing_get_settings`, `signing_upload_stamp`, `signing_upload_signature`, `signing_delete_stamp`, `signing_delete_signature`, `signing_save_settings`, `signing_slots_for`, `signing_roll_overlay`. Consumed by ALL FIVE editors (they call `signingRegister` on load and rely on the wired Sign buttons). Globals: `window.toast`, `window.refreshOpenPanel`.

**Gotchas**
- Slot vocab differs per doc type and is duplicated in `slotLabel` (`main`/`recipient`/`supplier`); the authoritative slot list comes from `signing_slots_for(docType)` unless the editor passes `activeSlots`. Editor `docType` codes are `po/qut/dn/inv` (note `qut`, not `quote`).
- Randomization is rolled by the BACKEND on Apply (`signing_roll_overlay`) - the frontend only picks sig_index + stamp per slot; don't reimplement rolling here.
- Previews render backend data URIs, so there is no stale-image/cache problem and no `?t=` cache-busting anywhere in the file - do not reintroduce static `/sigs/` URLs (the PNGs are per-profile and outside the webview's static root).
- Handlers are delegated to `document` (survive innerHTML rewrites + load order). The MutationObserver on `#settingsView[hidden]` is the most reliable refresh trigger - belt-and-braces with the click delegation.
- `saveLabelsAndSizes` is fire-and-forget on `#btnSettingsSave`; it runs INDEPENDENTLY of app.js's main settings save. Default sizes (mm) are magic: stamp 46.5×29.0, sig 55.0×22.0.
- Storing the overlay per document is the EDITOR's responsibility; this module only produces it and calls `onApply`.

---

## static/signed_copy.js

**Purpose** - The **CLIENT's** counter-signed copy: the paper they sign and scan back to us. Delivery notes have had it since 2026-07-24; the invoice got the identical feature on 2026-08-27 and the dialog moved here so there is exactly ONE of it. **Not** our own digital signature - that is `signing.js`. Keep the pair straight: `signed` = we signed it (drives the Copy guard), `client_signed` = they did (drives the panel pill).

**Key symbols** - all on `window.SignedCopy`:
- `open(kind, docNo, {onChange})` - the whole dialog. `Popup.open` shell ([[14-UI-CONTRACTS]]) with `dir="ltr"` (the shell is RTL), the attached file with right-panel-style actions (Open / Folder / Print / Copy / Un-attach), a drop zone, and the waive checkbox. `onChange(rec|null)` fires after every attach/replace/un-attach so the editor's toolbar button follows along.
- `load(kind, docNo)` - `get_doc_signed_copy`; returns `null` rather than throwing, so a bridge hiccup can't leave an editor believing it has a copy.
- `waived(kind, docNo)` - `is_attention_ignored(kind, docNo, "client_sign")`.
- **`promptAfterPaid(kind, docNo)` (2026-08-27)** - **marking an invoice paid opens this dialog.** That is the moment the client's stamped copy is physically in hand, so it is when we ask. Deliberately **silent** when there is nothing to ask for: a copy is already attached, or the invoice is waived. Resolves only once the dialog closes, so a caller can queue something behind it. Wired at ALL THREE places an invoice can be marked paid - the right panel's Paid button (inv.js `wireInvPaidButtons`), the dashboard's Needs-Attention card (app.js), and the To-Do list (todo.js `performAction`). Miss one and the prompt looks unreliable rather than absent.
- `paintButton(btn, kind, saved, has)` - disabled until the document is saved, `.active` + `aria-pressed` while a copy is attached.
- `syncEditors(kind, docNo, rec)` - internal; after every change it updates whichever editor currently holds that document (`invState`/`dnState` + its toolbar button). Needed because the dialog now opens from the panel and the To-Do list with **no editor involved at all**, so it cannot rely on the caller passing `onChange`.
- `acceptable(file)` / `TYPES` - PDF, PNG, JPG. `KINDS` holds the only per-kind difference: wording.

**Reads / writes** - Bridge only: `get_doc_signed_copy`, `attach_doc_signed_copy` (native picker), `attach_doc_signed_copy_data` (paste/drop, data URI), `remove_doc_signed_copy`, `is_attention_ignored`, `set_attention_ignore`, `open_path`, `reveal_in_folder`. The file itself is copied next to the document and renamed by the backend ([[02-BACKEND-CORE]]). Globals: `Popup`, `showModal`, `toast`, `esc`, `safe`, `printPdfFile`, `copyFileToClipboard`, `refreshOpenPanel`.

**Calls / called by** - dn.js `openDnSignedCopy` and inv.js `openInvSignedCopy` ([[06-FRONTEND-EDITORS]]); each editor keeps only its own button + `hasSignedCopy` flag.

**Gotchas**
- The drop zone **mirrors the quotation image cell**: single click focuses so **Ctrl+V pastes**, **double click** opens the picker, drag-and-drop also works. Unlike that cell it accepts **PDFs as well as images**, so it deliberately does NOT reuse `onImagePaste` - that one is hard-filtered to `image/*` and would silently drop a pasted PDF.
- The waive checkbox reverts itself when the save fails, so it never shows a state that wasn't stored.
- The dialog is opened once and its BODY repainted (`render`); the head and the way out never move. `_h.closed` is checked before every repaint - a save can land after the user dismissed it.
- Adding a third document kind is `KINDS` + `api._SIGNED_COPY_DOCS` + that editor's button. Do NOT copy this file.
- **The panel's Paid button AWAITS `promptAfterPaid` before opening the PO fulfillment picker.** Both are popups; firing them together puts two dialogs on screen fighting for focus. Same reason `promptAfterPaid` resolves on close rather than on open.
- **`reportInvoiceRefresh(r)` (2026-09-02)** - attaching or un-attaching a **DN's** copy also rewrites the PDF of every invoice carrying that delivery note (`api._refresh_invoice_attachments`, [[02-BACKEND-CORE]]). The dialog toasts `r.invoices_refreshed` / `r.invoices_failed`: the user is standing in the DN and would otherwise have no way to know an invoice they already sent has changed on disk.
- Pinned by `tests/inv_signed_copy_frontend.test.js`, which fails if either editor grows its own copy of the dialog.

---

## static/doc_settings.js

**Purpose** - Per-doc-type settings editors (Phase E). **Since 2026-09-05 all four panels read and write through ONE bridge pair** - `get_doc_settings(kind)` / `save_doc_settings(kind, patch)` with a DEEP merge ([[02-BACKEND-CORE]], [[03-BACKEND-DATA]]). `openPoSettings` was the odd one out: it read `get_settings()` and posted flat top-level keys through `save_settings`, a different call with a SHALLOW merge. Its payload is now a `purchase_order` patch shaped like the others (`prefix`, `brand_color`, `company`, `company_tags`, `layout.{table_min_row_mm, table_line_height_mm, table_header_mm, signature_mm, autofit_rows}`). NOT an IIFE - top-level `async function` + `window.*` exports. `window.openDocSettings(docType)` renders one reusable modal for Quotation/Delivery/Invoice; PO has a dedicated modal. Edits company signature fields, per-sub-type table columns, table-layout knobs, invoice banks + withholding, and PO Arabic identity.

**Key symbols**:
- `openDocSettings(docType, opts?)` (`window.openDocSettings`) - loads `get_doc_settings(docType)`, pulls live schema (`get_quote_types`/`get_inv_types`, or synthesizes a `delivery` entry), then renders + binds the modal. For quotation/invoice/DN table settings, `opts.columns` can override the schema columns so an editor can seed the modal from its currently visible table headers/widths.
- `renderDocSettingsModal(docType, s, opts, schemas)` - markup: signature block (varies: DN=recipient/supplier labels, INV=label+name, QUT=name+title), per-sub-type columns+layout block, shared table-layout block, invoice extras (withholding + banks).
- `renderColsEditor(root)` - the columns editor (reorder ▲▼, key/head/kind/width, delete; primary col locked). Returns the mutable `state` array read at save and tracks `state._dirty` when the user edits columns. `COL_KINDS` / `COL_KINDS_ADV` define selectable kinds (computed/image/index only for default cols).
- `bindDocSettingsModal(...)` - wires Cancel/Reset/Save + Add-bank; **binds to the panel it is handed** (`const root = backdrop;`) - `Popup.open({className: "ds-modal"})` puts that class on the panel ITSELF, so hunting for it as a descendant returns `null` and the whole dialog opens fully rendered with nothing wired, the invoice's Bank accounts editor included ([[14-UI-CONTRACTS]], 2026-08-26). Save validates columns and assembles the sub-type payload; Save validates columns and assembles the sub-type payload. `opts.onSaved` receives `{columnsChanged}` so editors can distinguish layout-only saves from real default-column changes.
- `collectDocSettings(root, docType, banks, originalSettings)` - builds the save payload; starts `company` from EXISTING saved company (only overlays signature fields), always sends a `layout` block (possibly `{}`), and invoice `withholding_pct` + `bank_info`.
- `poLayoutPatch(root)` - collects PO layout fields. Every blank numeric box is sent as `""` so the deep merge removes that one override while preserving numeric siblings; `autofit_rows` remains an explicit boolean. Exported as `window.poLayoutPatch` for its focused UI contract test.
- Bank editor: `renderBankList(root, banksIn)`, `renderBankCard(listEl, banks, i)` - invoice-only; DOM order is the source of truth for indices.
- `openPoSettings()` (`window.openPoSettings`) - dedicated RTL Arabic modal; uses the unified `get_doc_settings("po")` / `save_doc_settings("po", patch)` path and edits the `purchase_order` section. Numeric layout boxes use `response.stored.layout`, not resolved defaults, so clearing a number visibly returns the box to `auto`; helper copy distinguishes the signature protection band from page margins and points dense, short-row POs to Auto-fit. **2026-07-17:** includes a **"تصنيف الشركات" (Company classification)** section - fetches `api().get_po_company_tags()` (seeds on first read) into a local `tagState`, renders `.ds-tag-row`s (name + Client/Our-company `<select>` + remove) via `drawTags()`, plus a manual add input; the full map is saved under `company_tags` in the PO section patch. Feeds the To-Do `deliver` bucket + the PO-editor popup ([[02-BACKEND-CORE]], [[05-FRONTEND-SHELL]]).

**Reads / writes** - All through api; ultimately the **ACTIVE PROFILE's** `settings.json` (`config.SETTINGS_PATH = profile_manager.settings_path()` - see [[13-PROFILES-BRANDS]]). `get_doc_settings`/`save_doc_settings` serves all four document sections, including PO. Column overrides affect the doc's schema/PDF layout. Does NOT upload logos/signatures (moved elsewhere).

**Calls / called by** - Bridge: `get_doc_settings`, `save_doc_settings`, `get_quote_types`, `get_inv_types`. Reuses app.js globals `api()`, `toast()`, `showModal()`, `esc()`, `safe()`, `window.ICON`. Called from the QUT/INV/DN mini-hubs & editors (Settings buttons) and the PO settings entry point. See [[06-FRONTEND-EDITORS]].

**Gotchas**
- **Width preservation is load-bearing (Phase L.4):** `colsState` carries `c.width` through render AND save. If dropped, every save re-divides ~96.4% equally across columns, destroying the schema's intentional weighting. Save only emits `width` when `>0`.
- When an editor passes `opts.columns`, the columns editor must prefer that active table snapshot over `schemas[subtype].columns`; otherwise a user can add/rename/resize columns in the table and open type settings only to see stale backend headers or reset widths. But save must only persist those active columns when `state._dirty` is true; layout/signature/auto-fit-only saves preserve the existing type-default columns instead of promoting a saved document's custom layout to defaults.
- **Spacing knobs must be verified end-to-end, not just rendered (2026-07-30).** The panel used to offer *Max items per page* and *Table vertical budget*, plus per-sub-type *Max items* / *Min row height*. All four were phantoms: they saved to settings.json and echoed back on reload, but no generator read them (a sub-type `layout` is read by nothing at all - the row constants are module-level and cannot vary per sub-type). They are gone. What the panel offers now is `min_row_mm`, `max_row_mm`, `safe_gap_mm` and - quotation only, since it is the only type with an image column - `image_row_min_mm`, the biggest single control over how many picture rows fit a page. Adding a knob here means adding it to `config._layout_dict`'s whitelist too, or it silently does nothing; `tests/spacing_settings_reach_renderer.test.py` fails if the two drift.
- **Always send `layout` even when empty (Phase L.2):** an omitted `layout` key leaves the previously-saved override in place, so "Auto" (blank inputs) never clears. `collectDocSettings` and the sub-type block both force `payload.layout = {}` when all knobs are blank; backend then removes the override.
- **PO blank fields are individual clears (2026-09-07):** `poLayoutPatch` sends `""` for each blank numeric key. That matters when one box is cleared while another still contains an override: sending only the populated siblings would leave the old value stuck. The PO modal displays `get_doc_settings().stored.layout`, while its helper text can show resolved values from `.settings.layout`; using only the latter makes every cleared box immediately repopulate with the default and look as if Auto did not work.
- Delivery has no sub-types: a synthetic `schemas.delivery` entry + `opts.subtype="delivery"` reuse the same code path. Its spacing now comes solely from the doc-level layout block (the sub-type layout it used to merge in was one of the dead controls removed 2026-07-30).
- Settings Auto-fit controls the doc-type default. The visible editor toolbar toggles (`btnQAutofit`/`btnDnAutofit`/`btnInvAutofit`, plus PO's `btnPoAutofit`) control the current document and save into `doc_meta` as `key='layout'`.
- Setting `payload[opts.subtype] = {}` REMOVES the sub-type override entirely; a non-empty object replaces it. Previously-saved `terms` are preserved verbatim (the Terms textarea was removed from the UI; terms are now managed by an in-editor popup).
- `company` is started from the existing saved company and only signature fields overlaid - header/closing/address/brand fields live in App Settings now and would be wiped if this modal sent an empty company. Relies on backend SHALLOW merge.
- Column key validation: `^[a-z][a-z0-9_]*$/i`, no duplicates, key+head required - enforced client-side before save. `col_N` auto-key generation skips taken keys.
- PO modal is RTL/Arabic and edits `name_ar_lines`/`name_en_lines`/`title_ar`/manager+submitter Arabic fields + `prefix` + PO layout keys (`table_min_row_mm`, `table_line_height_mm`, `table_header_mm`, `signature_mm`, `autofit_rows`).
- **Brand-color fallbacks are Abraj-flavored and do NOT branch on profile:** the JS hardcodes QUT/DN/INV fallback `#1F3864` and PO fallback `#002060`; they only apply when no value is saved. Actual brand colors come from the backend-saved settings - e.g. the Eshraqat accent `#861432` (`config.PROFILE_ACCENT`) arrives only via saved values, never from these JS defaults ([[13-PROFILES-BRANDS]]).

---

## static/bulk_paste.js

**Purpose** - Bulk-entry wizard: paste an Excel block (Ctrl+V) or drop/pick a spreadsheet file, auto-detect header + auto-map columns onto the editor's schema, preview, then insert rows into the editor's grid via its own addRow path. Loads BEFORE editors so they can call `window.bulkPasteRegister` at eval time (same pattern as signing.js). Nothing saved to disk - rows land as if typed.

**Key symbols**:
- Pure parsing/cleaning: `coerceDigits` (Arabic-Indic/Eastern-Arabic → ASCII), `normHead`, `cleanNumber`, `trimTrailingEmpty`, `parseMatrix(html, text)` (HTML `<table>` first, then TSV/CSV), `padMatrix`, `bestTarget(src, targets, used)` (fuzzy header→column via `SYN` synonym table), `looksLikeHeader(row, targets)`, `positionMap`, `autoMap(headerRow, targets, width)`. Exposed for tests on `window.__bulkPasteInternals`.
- Registry: `bulkPasteRegister(opts)` (`window.bulkPasteRegister`) - editors register `{docType, buttonId, getColumns(), primaryKey(), addRows(objs, replace)}`; wires the button to `openWizard`.
- Wizard state/flow: `openWizard(docType)`, `closeWizard`, `onPasted(html, text)`, `applyMatrix(m)` (separate path for backend-parsed files - does NOT re-run parseMatrix), `renderGrid()`, `recompute()`, `commit()`.
- Cell logic: `isNumKind`, `numIssue`, `cleanCell`, `dataRows`, `buildObjs()` (maps matrix→row objects, skips empty by primary-key or any-value, counts issues).
- File paths: `pickSpreadsheetFile()` (`api.bulk_pick_spreadsheet` + `api.bulk_parse_spreadsheet`), `handleDroppedFile(f)` (routes binary xlsx/ods to backend, CSV/TSV read as text in JS), `sendFileToParser(f)` (`api.bulk_parse_uploaded_bytes` with base64 data-URL).
- `init()` - wires `#bulkPasteCatcher` paste, `#bulkDrop` (click=file picker, drag/drop), Cancel/Commit/Repaste, header checkbox, mode radios, Esc/Ctrl+Enter.

**Reads / writes** - No DB/file writes; reads dropped/picked spreadsheet files (binary ones via backend parser). Inserted rows enter the editor's in-memory state only.

**Calls / called by** - Bridge (optional, feature-detected): `bulk_pick_spreadsheet`, `bulk_parse_spreadsheet`, `bulk_parse_uploaded_bytes`. Called BY the QUT/INV/DN/PO editors via `bulkPasteRegister`. Uses `window.toast`.

**Gotchas**
- Binary spreadsheets (`.xlsx/.xlsm/.xlsb/.xls/.ods`) are ZIP archives - reading them as text gives mojibake ("PK", "docProps/…"). They MUST go through the backend parser; `applyMatrix` intentionally skips `parseMatrix` for backend-parsed rows so Chinese/Arabic aren't mangled (Phase M.3).
- Column mapping is one-to-one: selecting a target for one source column clears that target from any other column. `buildObjs` skips a row when the primary key (if mapped) is blank, else when every mapped cell is blank.
- Numeric cells that don't parse become an "issue" (highlighted, left as raw text for the user to fix) - they are NOT dropped. `cleanNumber` handles Arabic digits + currency/thousands stripping and collapses multiple decimal points.
- Header detection (`looksLikeHeader`) requires ≥1 name-match AND zero numeric cells; wrong detection shifts the whole grid by one row (toggle via `#bulkHasHeader`).
- `PREVIEW_ROWS = 8` limits the preview only; `commit()` inserts ALL built rows. Replace vs Append is a radio (`input[name='bulkMode']`).
- The pure helpers on `window.__bulkPasteInternals` are used by the headless screenshot/test harness - keep their signatures stable.

---

## static/master_data_fill.js

**Purpose** - Shared top-toolbar **Fill from Master Data** popup for the Quotation, Delivery Note and Invoice editors (2026-08-14). It resolves the nonblank primary-key values in the current table against Master Data, displays exact/contained/similar/new results and alternatives, then applies only the fields the user selected.

**Key symbols**:
- `window.openMasterDataFill(opts)` - accepts the editor's `{docType, subType, rows, primaryKey, costCurrency, costPeg, sellCurrency, sellPeg, render, markDirty}` context, calls `api().im_resolve(...)` with each row's `{description, unit}` as `details` (2026-10-03 - a typed description picks one of several variants; a blank one picks nothing), and opens the shared modal.
- `FIELDS` - the five independent choices: `item`, `description`, `unit`, `price`, `cost`. Available fields are checked by default; unavailable fields remain visible but disabled.
- `applyRowFill(row, primaryKey, result, selected)` - maps the selected UI fields to the editor row (`price` → `rate`) without touching quantity or unchecked fields. Money carries its unit: `cost_ccy` / `rate_ccy` are written beside `cost` / `rate`, because the backend fills a cost in the currency Master Data SAVED it in (`master_data._item_native`, 2026-08-20) rather than converting it. Changes remain in memory until the normal editor save, and the caller's `render()` rebuilds the rows - which is what repaints the `$`/`IQD` chips.
- `previewForField(fill, fieldKey)` (2026-08-26) - what each checkbox SHOWS. A money field is rendered with its unit (`$1.43` / `2002 IQD`); everything else is shown as-is. A bare `2002` in that list reads as dollars when it means dinars, and the whole point of the fill popup is that you can see what you are accepting.

**Reads / writes** - Reads the current editor rows and Master Data through `im_resolve`; writes only to the editor's in-memory row objects, calls the editor render/dirty hooks, and never saves a document itself.

**Calls / called by** - Called by `quote.js`, `dn.js` and `inv.js` through `#btnQMasterFill`, `#btnDnMasterFill` and `#btnInvMasterFill`. Calls `api().im_resolve` and the shared `showModal`/`toast` helpers. Loaded before the editor modules in `templates/index.html`.

**Gotchas**
- The item checkbox writes the editor schema's primary-key field, not necessarily a hard-coded `item` property; QUT/INV pass their schema primary key and DN passes `item`.
- Similar matches expose `suggested_fill` for review but never auto-fill a field unless the user explicitly selects it.
- When `suggestions` contains multiple candidates, the popup shows radio choices with each candidate's description/unit and its own fill values. No candidate is selected automatically; choosing one enables that candidate's Item/Description/Unit/Price/Cost checkboxes, all available fields checked by default.
- Price and cost are independent: the API receives both sell-side and cost-side currency/peg context, and a missing value disables only that field.
- 2026-09-05 - the presentation primitives are exported as **`window.MasterDataFill`** (`FIELDS`, `escText`, `hasValue`, `fillFor`, `valueForField`, `previewForField`, `availableFields`, `applyRowFill`, `resultRowsHtml`) so `po_handoff.js` can render matches the same way. `window.__masterDataFillInternals` is the SAME object under its old name. `openMasterDataFill` itself is deliberately not exported - this module's contract is that it never writes, and the PO screen does.

---

## static/master_doctor.js - the catalogue review panel (2026-09-10)

**Purpose** - The screen half of `backend/master_doctor.py` ([[03-BACKEND-DATA]]). The Master Data chips answer "what is MISSING"; this answers "what is WRONG and needs a decision" - one product stored twice at two different prices, variants nothing can tell apart, a date typed into a description. None of those is a blank field, so no chip can show them.

**Where it lives** - a panel inside `#itemMasterView`, hiding the filter row and the list while open, so it reuses the screen rather than becoming a new one. **The way in is the Health panel** (`imReviewBtn`), where you already look for the state of the catalogue - deliberately NOT a second top-level button beside Health competing to mean the same thing.

**Key symbols**
- `KINDS` - one entry per finding kind: its heading, the sentence explaining it, and what the button says. The backend decides what is wrong; this decides how it reads.
- `_cardHtml` / `_rowsHtml` / `_priceNote` / `describeHtml` - the card. A duplicate shows the real records with their numbers, units, prices and use counts, and says **which one survives** rather than leaving it to row order.
- `act(index, what)` - one finding at a time (`state.busy`). A merge deletes rows; a batch that half-succeeds has no honest report to give.
- `saveDescriptions` - variants get one description box each, in the card. A box left blank is left ALONE: `save_record` treats a present-but-empty value as "clear this". Saving them answers the finding, so it is dismissed afterwards.
- `count()` - the number for the Health panel's one-line summary.

**Gotchas**
- **Colour never carries meaning alone** (the user is colour blind). A price disagreement is the words *"The copies disagree about the price"*; the amber rule beside it is reinforcement. There is no green counterpart anywhere, asserted against the stylesheet's declarations rather than its prose - the comment says "green" and a rule that reads its own documentation passes for the wrong reason.
- **Two detector bugs were caught by DRAWING this, not by unit tests**: `Manpower` proposed for a 4WD Pickup Truck, and an offer to flatten the 25-Seater Bus's four-line route. Both fixed in the detector; see [[03-BACKEND-DATA]]. A screen over real data is a test the unit tests were not.
- `item_master.js` calls `MasterDoctor.count()`, so `master_doctor.js` must load **before** it.

## static/po_handoff.js - Make DN / Make Invoice from a PO (2026-09-05)

**Purpose** - Turns a saved Purchase Order into a filled Delivery Note or Invoice. A PO is written in Arabic and both of those are written in English, so this is a LOOKUP feature, not a data-entry one: `master_data.resolve_items` builds its candidate list in the ASKING document's language, so passing the PO's Arabic names with `doc_type` `"dn"`/`"inv"` returns the English name, unit, sell rate and cost the catalogue already holds. The review screen exists only for rows the catalogue cannot answer, and what is typed there is written BACK, so the same item is never asked about twice.

ONE implementation serves both buttons - `kind` is a parameter. Per-editor copies of a shared flow have drifted apart repeatedly here (the signed-copy dialog, the cost-currency rules).

**Key symbols**
- `window.ItemReviewPopup` in `static/item_review_popup.js` (2026-09-08) - the one reusable content class for save-time catalogue completion and PO-to-document review. `Popup` still owns modal chrome and focus; **dismissal is the component's** - it opens `dismissible: false` so Esc and the dim background cannot bin forty rows of typing, and Esc, Cancel and the backdrop all route through one "Discard what you have entered?" question, asked only when something has actually been typed. Picking a candidate re-seeds that row's description, unit, price and cost from the record chosen (blank where it holds none), except boxes the person typed in; leaving the auto-match's values in place put one product's private cost on the document and into another product's master record. This class owns compact item rows, Details disclosure, candidate selection, Skip, document-only values, AI proposals, and normalized results. It is language-directed through `sourceLanguage` / `targetLanguage`, so the same class is ready for the Quotation editor's future English-to-Arabic review.
- `reviewRowFromDecision(d, kind, index)` / `applyReviewResults(decisions, rows)` - the boundary between PO business decisions and the shared component. The adapter computes missing fields individually. A matched item with a known English name and unit but no sell rate renders those known values as confirmed text and shows only a Rate input. A-88 pins the real case: Fig, Red Plum and Low Fat Milk are matched materials, not new items.
- AI completion is configured once on `ItemReviewPopup`. **Complete Missing Details with AI** sends one batch, applies safe text and candidate proposals, then automatically makes at most one repair request for rows whose required name is still blank. Optional descriptions never trigger the repair. Rows already marked Skip or document-only are not sent. It appears for missing target-language details or an unresolved variant when `tr_status().configured` is true, and remains absent for rate-only review. Suggestions never overwrite nonblank fields, remain editable, and receive visible "AI suggestion" text as well as styling until edited.
- **`DIRECTIONS` / `direction(want)` / `catalogueReview(docNo, spec)`** (2026-09-10) - **ONE save-time review, two directions.** A purchase order is written in Arabic and needs its ENGLISH half so a delivery note or invoice can be made from it. A quotation is written in English and needs its ARABIC half, so the same goods are FOUND when they are later ordered on a PO. Measured 2026-09-10: **346 of the 421 distinct items ever quoted (82%) have no Arabic name**, and every one is invisible to a PO lookup - which is how the catalogue grows a second record for something it already has (four separate `Vacuum Truck` records were created in one day). The descriptor carries `target` / `source` / `ask` (which language `resolve_items` is asked in) / labels / a `missing(name)` predicate; `recordNeedsLanguage`, `buildMasterPatch`, `reviewRowFromDecision`, `targetSuggestions`, `applySuggestedUnit`, `unitMap` and the AI call all read it. A mirrored copy of a 200-line review is how the signed-copy dialog and the cost-currency rules drifted apart - twice.
- **`window.quoteReviewNewItems(quoteNo)` / `quoteRowFields(row, schema)`** (2026-09-10) - the quotation's caller. Reading a quotation row is the one genuinely new part: four quote types each name their own columns (materials `item` + `description`, equipment `equipment` + `specs`, manpower `role`, service `description` - where the name IS the description), plus whatever the user added. The NAME comes from the type's `primary_key`, the same key `api.save_quote` already remembers items under, and the description ONLY from a column literally called `description` or `specs`. Guessing at "the second text column" would file a service quotation's `remarks` as a description, and a user-added `col_3` is not declared to be a unit, so no unit is invented from it.
- `window.poReviewNewItems(poNo)` (2026-09-06, persistence fixed 2026-09-07) - **the third caller, and it is not a document.** Runs from `savePO` AFTER the order is saved and reported: resolves the stored rows, keeps the ones whose record has no English name (`recordNeedsEnglish`), pre-fills the English unit from `mi_unit_map`, and opens the SAME review popup in `catalogue` mode. Confirm writes through the same `writeBack`. **Confirming and cancelling are remembered differently** (2026-09-08): Save to Master Data persists the presented identities through `po_catalogue_review_mark`, while Cancel only adds them to `_reviewedThisSession`. Persisting a cancel retired the row for good - nothing in the app passes `force`, so the popup was simply gone and the item stayed half-recorded; a restart now offers it again. `po_catalogue_review_get` filters the persisted set on later saves and after an app restart. **Keyed by document TYPE as well as number** (2026-09-10): `po` for a purchase order, `qut` for a quotation, both in `doc_meta` and in the session fallback - the two can carry the same number shape, and reviewing one must never silence the other. The identity includes normalized name, description and unit, so a genuinely new or changed row on the same PO is still offered.
- `window.poMakeDocument(kind)` - the whole flow: guard → save gate → `load_po` → company/line selection → `im_resolve` → review (only if needed) → write-back → hand off. Wired to `#btnPoMakeDn` / `#btnPoMakeInv` in `app.js`. **One run at a time** (2026-09-08): both buttons await the bridge before anything modal is on screen, so a second click in that window started a second run over the same PO - two reviews, two write-backs, two documents. `_running` guards it and clears in a `finally`.
- **The PO's `reference` travels with the items** (2026-09-09) - `payload.reference` is `rec.reference` copied verbatim, and `createDnFromPo` / `createInvoiceFromPo` put it in their own reference box AFTER `initNewDn` / `initNewInv` (both blank it). `reference` is the app-only note saying WHAT the order was for - "اوكسجين وغاز", "20260902-CAMP" - and it is the same job when the order becomes a delivery note or an invoice, so it was being re-typed: 11 of 87 POs carry one, 16 of 47 invoices do, and 0 of 63 delivery notes. A COPY, never a derivation: nothing is appended to what he typed, and a PO with no reference leaves the box empty rather than inventing one. Pinned in `tests/po_handoff_review.test.js`.
- `window.linkPoAfterSave(sourcePo, kind, docNo)` - writes `set_po_fulfillment` after the document is saved. Returns `true` when the caller should clear its `sourcePo`, `false` when the link was refused and must be retried.
- `billableGroups(rows, tags, doneMap)` - the PO's lines grouped by company, dropping `internal` lines and lines with no company. A line's `company` IS the client to bill (what `database.pos_awaiting_invoice` keys on).
- `handoffReviewKey(row)` / `normalReviewText(value)` - normalized name + description + unit identity. Plain `toLowerCase`, never the locale-aware one (2026-09-08): this identity is STORED in `doc_meta`, and a key that depends on the machine's locale stops matching when the machine changes. Exact duplicate lines share a decision, while A-89's same-name 1.5 Ton and 2 Ton Split AC rows remain independent.
- `applySuggestedUnit(decision, units)` - overlays the local `mi_unit_map` value before readiness and rendering. A-83's `عدد` is therefore `EA` without consuming an AI request. When the reviewer accepts a new English name, `buildMasterPatch` also persists that locally completed unit even though it was shown as confirmed text rather than an input.
- `appliedFill(result)` (2026-09-08) - the gate in front of `MasterDataFill.fillFor`. **A `similar` tier is a guess, and a guess is never a fill.** `resolve_items` leaves `fill` empty and sets no top-level `item_id` for `similar` precisely so nothing is applied without a person, but `fillFor` falls back to `suggested_fill` - right in the paste grid, where the user ticks each field before it lands, and wrong here, where a filled row is a row `readinessFor` marks READY and the review popup never shows anyone. Measured on the live catalogue 2026-09-08: 267 of 278 fuzzy matches were applied with no review, 174 of them carrying a rate or a cost; "Whiteboard Set" (score 0.839) silently became Whiteboard Eraser at $4.60 on a delivery note. `exact` and `contained` still fill, or every clean lookup would become a question.
- `readinessFor(fill, poRow, kind)` - **per-field, by SCRIPT.** See Gotchas; this is the heart of the module.
- `needsVariantChoice(d)` / `reviewValidation(decisions, kind, catalogue)` - prevent unresolved exact-name ties from creating duplicate materials and return exact row/field errors to the shared component. Invoice rate errors state explicitly that AI completes language details, not prices.
- `buildMasterPatch(d)` / `writeTargetId(d)` - the write-back, built by INCLUSION. Since 2026-10-03 it also records the line's own-language **description** on the chosen candidate (or a hand-completed record), into a BLANK slot only - the description is what tells variants apart, so the next order finds the variant unasked.
- `lineDetails(row)` (2026-10-03) - `{description, unit}` of a PO/quote line, sent as `im_resolve`'s 9th argument by Make DN / Make Invoice and by `catalogueReview`, so a name shared by several variants (A-117: hydraulic oil 18 L / 20 L) resolves to the one the line names. See `master_data._line_variant`.
- `buildHandoffRow(poRow, fill)` - the destination row, built explicitly.
- `window.__poHandoffInternals` - the above, for `tests/po_handoff_review.test.js`.

**Reads / writes** - Reads `load_po`, `get_po_company_tags`, `im_resolve`, and `po_catalogue_review_get`. Writes Master Data through `mi_save` / `mi_create`, records handled save-time rows through `po_catalogue_review_mark`, and writes `po_fulfillment` through `set_po_fulfillment` after the save. Never saves a document itself. It hands rows to the editor, unsaved and dirty.

**Calls / called by** - Called from the PO toolbar via `app.js`. Calls `window.createDnFromPo` (dn.js) / `window.createInvoiceFromPo` (inv.js), the thin halves that touch `dnState`/`invState`. `item_review_popup.js` is loaded after `popup.js` and before `po_handoff.js`; the handoff remains before `dn.js`/`inv.js`.

**Three kinds, ONE renderer and ONE read-back** - `dn`, `inv`, and `catalogue` all instantiate `ItemReviewPopup`. Configuration exposes only the fields each flow needs: catalogue completion has no money or document-only controls, Delivery Note does not require a rate, and Invoice exposes Rate only when missing. Catalogue mode skips the fit-to-print gate because the PO is already saved and a partial Master Data record is still useful. The shared field-by-field renderer replaces both former `unreadyRowHtml` and `catalogueRowHtml` implementations, so their layout and behavior cannot drift again.

**Gotchas**
- **An exact name tie is not automatically a new item.** `resolve_items` deliberately returns several variants as `similar` with no top-level `item_id`. The save-time review checks every tied record: if they all already have English names, the row is not offered. PO A-86's `ماء بطل` is the pinned live example. DN/Invoice creation still presents the ambiguity because choosing the correct product variant matters there.
- **Name alone is not a handoff identity.** A-89 contains two `سبلت جداري` lines whose descriptions identify 1.5 Ton and 2 Ton products. Handoff grouping uses normalized name + description + unit. An unresolved ambiguous row must select an existing candidate or use document-only values, and `buildMasterPatch` will not create a new Master Data row from that unresolved state.
- **Do not truncate a variant tie.** Every English candidate returned by the bounded resolver is available to the reviewer and the AI request. Hiding the fourth candidate can hide the only description/unit combination that matches the PO row.
- **Validation is structured.** The adapter returns `{message, errors: [{id, fields}]}`. `ItemReviewPopup` marks the exact name, unit, rate, or candidate-choice control, opens Details for hidden choices, scrolls to the first invalid row, and focuses the first invalid control.
- **Dismissal is per row identity, not only per PO number and not only in memory.** A session-only `Set` made "Not now" return after every app restart and hid genuinely new rows added to the same PO. The persistent key uses normalized name + description + unit and lives in the PO's `doc_meta`, so deletion cleans it with the document.
- **A suggestion must not be able to become a fact by accident.** `ItemReviewPopup._suggest` writes into the form and stops; the user still presses the primary action, and every suggested value is editable and visibly marked until they touch it. See [[03-BACKEND-DATA]] `backend/translate.py` for the same rule at the other end.
- **Saving a PO already creates the record.** `database.remember_item` -> `master_items.remember` runs inside every PO save and puts the Arabic name in `name_ar`. So the save-time popup is never "save this item or not" - the record exists; what is missing is its English half. Wording that offers to "add" the item would be a lie.
- **The save-time review must never be able to fail the save.** It is called after `state.dirtySinceSave = false` and after the toast, wrapped in `try/catch`, and its result is ignored. A catalogue write that fails is not the user's problem - their order is saved.
- **A TIER IS NOT A READINESS SIGNAL.** `master_data._items_from_records` names each record with `master_items.prefer(name_en, name_ar, lang)`, and `prefer` FALLS BACK to the other language, so a record known only in Arabic is listed under its Arabic name even when English was asked for - and matches at `exact`, with an `item_id`. Verified on the live catalogue: `ثلج`, `خشب سير`, `سبري زنك`, `مواد فوسروك` and `صبغ جوتن` all return `tier: "exact"` carrying Arabic. Readiness is a SCRIPT test on the filled value, per field, sharing the backend's `[؀-ۿ]` range. Pinned in `tests/po_make_document_resolve_backend.test.py`.
- **Never `smartMapRow` on this path.** `IMPORT_KEY_ALIASES.rate` includes `"cost"` and the mapper falls through empty values, so a row with a private cost and no rate would print the cost as the client's sell price. It would also append the PO's `company` into the description. `buildHandoffRow` maps explicitly instead; a source assertion in the test suite enforces it.
- **The patch is built by INCLUSION.** `save_record` treats a present-but-empty money key as "delete the amount and its currency", and a blank language-free `unit` as "clear BOTH units". An untouched field must be ABSENT. Also: `name` is not language-free, so an English name must be sent as `name_en` - a bare `name` is dropped and the call still reports success.
- **`name_ar` is written only into a BLANK slot.** Recording the PO's Arabic name against a record that already has a different one silently relabels somebody else's product.
- **Indexes come from `load_po`, never the editor.** `savePO` persists `filledRows()`, so blank editor rows are dropped and the editor index is not the stored index that `set_po_fulfillment` marks.
- **`po_fulfillment_detail` reports invoice links under `invoiced`, not `paid`** - even though they are STORED with `status_kind='paid'`. It also hides links whose invoice has no PDF, so a draft never makes a line look invoiced.
- **`reconcileImportedCostCurrency` is deliberately NOT called.** It converts by the source document's peg; a PO has no costs and no cost currency, and the master's cost is native-with-its-own-stamp.
- Row selection asks only when the PO's billable lines span more than one company. A cross-company selection leaves the client blank rather than inventing one.

---

## static/grid_paste.js

**Purpose** - Excel-style in-place grid paste into QUT/INV/DN/PO item tables. Click a cell, Ctrl+V (or right-click → "Paste here"); the clipboard grid fills down+across from the anchor, auto-appending rows. Also owns the **per-tbody edit history** behind Ctrl+Z / Ctrl+Shift+Z for every editor that passes `snapshot`/`restore` (PO, QUT, DN, INV, Costing) - undo is not paste-only. Distinct from bulk_paste.js: this pastes into an EXISTING focused cell rather than through a wizard.

Since 2026-08-15 it also owns **the app's only right-click menu, everywhere** - see "Right-click Copy" below. `app.py` starts pywebview with `debug=False`, and pywebview sets `AreDefaultContextMenusEnabled = debug`, so the WebView2 default menu is OFF app-wide: a right-click shows exactly what this file puts in it and nothing else.

**Key symbols**:
- Clipboard parsing: `parseDelimited(text, delim)` (quote-aware), `parseClipboardGrid(text)` (TAB-only split), `_htmlCellText(td)` (one `<td>` → one cell; raw Excel HTML whitespace collapses, while `<br>`/block elements become newlines), `parseClipboardHtml(html)`, `parseClipboard(html, text)` (HTML table authoritative, plain-text fallback), `looksLikeGrid(text)`, **`looksLikeLooseText(html, text)` (2026-09-02 - no `<table>`, no TAB, more than one line ⇒ the text did not come from a grid)**. Exposed on `window.parseClipboard*` / `window.looksLikeGrid` / `window.looksLikeLooseText`.
- `anchorFromTarget(target)` returns `{rowIdx, colKey, **multiline**}`. `multiline` is read off the DOM - text columns render as `<textarea>` and numeric ones as `<input>` - because this one module serves QUT/INV/DN/PO/Costing and only the DOM is common to all five.
- `parseNumberLoose(v)` - lax numeric parse ("1,234.5"/"$ 9.99" → number).
- `writableColumns(columns)` - drops `computed`/`image` kinds.
- `pasteGrid(grid, anchorRowIdx, anchorColKey, opts)` - core: grows state to fit unless `opts.allowRowGrowth === false`, writes cells left-to-right from the anchor col, `fullRender()`.
- `anchorFromTarget(target)` - resolves `{rowIdx, colKey}` from a `[data-k]` cell inside a `tr[data-i]`.
- Floating menu: `showContextMenu(x, y, items)`, `closeMenu()`. `items` is `[{ act, label, icon, kbd, run }]`, built fresh per right-click; `act:"cancel"` renders the dismiss row and needs no `run`. The element keeps its `#gridPasteMenu` id / `.grid-paste-menu` class. Inside an item cell the menu is **Copy · Paste here · Paste text here · Cancel** - **Paste here** uses grid parsing, **Paste text here** puts the complete `text/plain` value in the anchor cell.
- **Right-click Copy (2026-08-15)**: `selectionText(el)`, `fieldValue(el)`, `cellText(target)`, `copyToClipboard(text)` (also `window.copyToClipboard`), `copyItem(text)`. Two paths - inside an item cell, Copy takes the selection, else the **whole cell**; the document-level `contextmenu` listener covers the rest of the app and takes the selection, else a form field's value, else **shows no menu at all**.
- **Edit history (WeakMap per tbody, cap 60)** - `_h`, `_record`, `_begin`, `_commit`, `_step`, `_key`, `_isBlankRow`, `_isField`. Each tbody holds `{undo, redo, pending}`. `_begin` opens a stash of the pre-interaction snapshot (never overwriting an open one, so a single edit can't split into two steps); `_commit` closes it and records a step **only if the state actually changed**; `_step` moves one entry between the stacks, pushing the state being left onto the opposite one so undo/redo round-trip exactly. `_key` compares snapshots **ignoring trailing blank rows**.
- `readClipboardRich()` - async Clipboard API `read()` (for text/html) with `readText()` fallback, used by the right-click path.
- `setupGridPaste(tbody, opts)` (`window.setupGridPaste`) - the public wiring. `opts = { columns(), rowCount(), addEmptyRow(), allowRowGrowth?, setCell(i,key,v), fullRender(), snapshot(), restore(snapshot) }`; pass `allowRowGrowth:false` for fixed-row tables like Costing & Profit. Internally: `_refocus(anchor)`, `_doSingle(val, anchor)`, `_doText(val, anchor)`, `_doPaste(grid, anchor)`, `_applyGrid(grid, anchor, allowNative)`; wires `paste`, `contextmenu`, and Ctrl/Cmd+Z `keydown`.

**Reads / writes** - Reads the system clipboard (sync `clipboardData` on paste; async Clipboard API on right-click) and **writes** to it via `navigator.clipboard.writeText`, with a hidden-textarea `execCommand("copy")` fallback. Otherwise writes only into the editor's in-memory row state via the `opts` callbacks; no DB/file access.

**Calls / called by** - No bridge calls. Called BY the QUT/INV/DN/PO editors: each passes its own `columns/rowCount/addEmptyRow/setCell/fullRender/snapshot/restore` closures. Uses `window.toast`, `window.ICON`.

**Gotchas**
- The HTML `<table>` flavour is AUTHORITATIVE for cell boundaries - an Excel cell with Alt+Enter newlines arrives as UNQUOTED multi-line text/plain (looks like N stacked rows) but as one `<td>` in HTML. Plain-text fallback is quote-aware but can't disambiguate wrapped single cells; only use it when there's no HTML table.
- **There is a case with NO html flavour at all, and it was the reported bug (2026-09-02).** Copying a RANGE gives both flavours; copying from INSIDE a cell - double-click, select the Alt+Enter lines, Ctrl+C - gives plain text and nothing else, so the authoritative source simply isn't there and the fallback split a spec paragraph into 8 rows. The rule that fixes it asks the only answerable question: *did a grid produce this?* No `<table>`, no TAB, >1 line ⇒ no. Pasted into a `<textarea>` cell that case is ONE value (`looksLikeLooseText` + `anchor.multiline`); into an `<input>` it still becomes rows, because a number cell cannot hold line breaks. Both Ctrl+V and right-click → Paste here follow it, the toast names the line count, and **right-click → "Paste as rows" forces the other reading** (deliberately the plain-text parse, so the HTML cannot override the user). Guarded by `tests/grid_paste_incell_text.test.js`.
- **Excel pretty-prints inside `<td>` text.** Its real clipboard HTML can contain `Potato (Big\r\n  size)` even when the workbook value is one line and `wrapText=false`. `_htmlCellText` walks the DOM: text-node whitespace collapses to spaces, while only structural `<br>`/`<div>`/`<p>` boundaries become `\n` (including nested blocks and repeated `<br>` elements). Treating raw HTML CRLF as content reproduces the two-line editor-cell paste bug.
- A plain single value (no line breaks) on Ctrl+V is NOT intercepted (`allowNative=true`) - native input paste runs so you can insert at the cursor. A single value WITH line breaks IS intercepted so newlines stay in one cell. Right-click **Paste here** is always explicit (`allowNative=false`); right-click **Paste text here** bypasses grid parsing, preserves tabs/newlines as text, and never grows or traverses rows.
- **Refocus after every paste/undo is mandatory:** `fullRender()` destroys the focused node; without `_refocus`, focus falls to `<body>` and the next Ctrl+Z keydown never reaches the tbody listener, breaking chained undo.
- **Undo steps, it does not reset (2026-08-01).** The old M.5c stack snapshotted **only before a paste**, so typing was never recorded and one Ctrl+Z restored the pre-paste state - discarding every correction made since. (Reported as "paste, adjust prices, Ctrl+Z, and it goes back to before the paste".) A step is now recorded for each **cell edit** (`focusin` opens the stash, `change`/`focusout` closes it - one step per cell, not per keystroke), each **paste**, and each **in-table click that changes the rows** (`mousedown` + a deferred compare, which is how row deletes get captured). Redo is `Ctrl+Shift+Z` or `Ctrl+Y`, cleared by any new edit.
- **Cell edits rely on editors writing state on `input`, not `change`.** All five do. An editor that only wrote on `change` would leave the snapshot taken at `focusin` equal to the post-edit state, and the edit would silently not be undoable.
- **Never `preventDefault` when there is nothing to restore** - the browser's own per-character text undo inside a cell depends on the event getting through.
- **Trailing blank rows must stay excluded from the comparison** (`_key`). Editors auto-append an empty row the moment the last one gets content, so a raw compare logs a phantom step just for putting the caret in the final row.
- **Ctrl+Z mid-edit commits first**, so it undoes what was just typed instead of skipping past it to the previous action.
- Pinned by `tests/grid_undo_redo.test.js` (mutation-checked: removing the redo-clear, the pre-undo commit, or the blank-row normalisation each fail it).

**Gotchas - right-click Copy (2026-08-15)**
- **The two Copy paths are deliberately different.** Inside an item cell, a right-click with no selection copies the **whole cell** - a bounded, known string. Everywhere else the fallback is the **form field's value only**; with no selection and no field the handler returns *without* `preventDefault` and no menu appears. Falling back to `textContent` out there would put half the page on the clipboard for a right-click on any container div.
- **Read the text while building the menu, never inside `run`.** Pressing a menu button blurs the field and collapses the DOM selection, so by click time there is nothing left to read.
- **`el.selectionStart` THROWS on `input[type=number|date|email]` in Chromium** (it does not return `null`). `selectionText` wraps the read in `try/catch`; without it a right-click on any numeric cell takes the whole menu down.
- **The document listener must bail on `ev.defaultPrevented`.** The tbody listener is an ancestor of the cell and runs first; without the guard the document handler would replace the grid's four-item menu with a bare Copy one on every cell right-click. It also ignores right-clicks landing inside `#gridPasteMenu` itself.
- Password, checkbox, radio and file inputs are never offered.
- Pinned by `tests/grid_paste_copy.test.js` (19 checks, 8/8 mutations caught) - including the empty-cell case, the numeric-input throw, the bare-container refusal, and the two listeners' precedence. The cell menu is `copy,paste,paste-rows,paste-text,cancel`; that exact list is asserted in three places, so adding an item means updating them.
- `pasteGrid` silently drops cells that run past the right edge (no target column), and when `allowRowGrowth:false` it also stops at the last existing row. `writableColumns` skips image/computed columns, so the anchor-column offset counts only writable columns.
- `tbody._gridPasteWired` guards against double-wiring; `setupGridPaste` no-ops on a second call for the same tbody.

---

## static/col_resize.js

**Purpose** - Excel-style column resizing for the editor item tables (QUT/INV/DN schema tables + the RTL PO `#editorTable`). Drag a grab-handle on a header border; the grabbed column and its right neighbour trade width so the table stays exactly 100% wide. Also home of the one-click **Fit columns** feature (`fitEditorColumns`), which computes content-aware widths for you so you don't drag-and-preview repeatedly. Self-contained (no editor deps).

**Key symbols**:
- `startDrag(ev, leftTh, rightTh, table, rtl)` - one drag gesture; computes bounded delta (neither col below `MIN_COL_PX`), writes both widths as % of table width live. `rtl` flips cursor→delta so RTL tables drag correctly.
- `attachHandles(table)` - seeds missing inline widths from rendered widths (only when visible), inserts a `.col-resizer` handle between adjacent `data-col-key` columns (skips index/delete columns), positions the handle on the shared border (right in LTR, left in RTL).
- `init()` - attaches handles to every `TABLE_SELECTOR` table and a `MutationObserver` on each `tHead` (`childList`) to re-attach when the editor rebuilds the header.
- `window.refreshColResizers()` - synchronous re-attach (editors call it right after rebuilding a thead instead of waiting on the observer).
- `window.readEditorColWidths(thead)` - returns `{ colKey: widthPercent }` for every `th[data-col-key]`, reflecting manual resizes (inline % first, rendered % fallback). The backend uses this as explicit per-column widths so the PDF mirrors the on-screen table.
- `window.fitEditorColumns(tableArg, opts)` - **content-aware "Fit columns"** (Phase X.25). For each *flexible* data column it (1) measures the content demand (canvas `measureText` of header + every body cell, `clamp(widestLine+pad, MIN_COL_PX, CAP_PX)`) and (2) computes a **header floor** so a header WORD never breaks mid-word ("RAT E"/"TOTA L" bug) and, for money columns, a **value floor** (2026-09-02) so a printed amount never breaks either. It then **water-fills**: every column gets its floor first, and the remaining width is split by content demand *above* the floor (long-text columns still win the surplus). *Fixed* columns are left untouched so the table stays exactly 100%: NO./delete (no `data-col-key`), **image** columns (thumbnail-driven, detected via the body `.qc-img-cell` td), and the synthetic **COST** column (`.qc-cost-col`). Writes the SAME inline `%` a manual drag would, so the existing `readEditorColWidths` → `col_widths` → backend rebalance pipeline makes the PDF mirror the screen - **no backend change**. `opts.pdf = {widthMm, headerFont}` supplies the PRINT geometry so the header floor is measured at the real print font and expressed as a % of the print table width (percentages transfer 1:1 to the PDF, so headers are guaranteed to fit *in print* even though the editor table is wider); without it the floor falls back to the editor-px minimum. `tableArg` is anything `_resolveTable` accepts: a thead id (`#qThead`/`#dnThead`/`#invThead`), a table selector (`#editorTable`), or an element. Returns `false` (no-op) when the table is hidden (can't measure) or has <2 flexible columns.
- `window.wireFitColsButton(btnId, tableArg, {pdf, emptyMsg, onFit})` - convenience wiring for a toolbar **Fit columns** button: runs `fitEditorColumns(tableArg, {pdf})`, flashes the button (`.just-fitted`) on success, toasts `emptyMsg` when it can't run (empty table), and calls `onFit()` (editors pass a closure that flags the doc dirty). No-op if `btnId` isn't in the DOM. Each editor passes its print geometry: QUT/DN/INV `{widthMm:186, headerFont:'bold 10pt "Bahnschrift",…', bodyFont:'10pt "Bahnschrift",…'}` (condensed), PO `{widthMm:190, headerFont:'bold 14pt "Arial",…'}`. **`money` + `formatMoney` (2026-09-02)** - `money(key)` names the columns that print an amount (each editor answers from its own live schema, `kind === "money" || "computed"`, so a renamed column is still covered); `formatMoney(v)` renders one the way that document prints it (quote → `qmoney`, invoice → `imoney`, which is why an IQD invoice reserves for `IQD 1,234,568` rather than the narrower dollar form). The **PO passes neither** - its Arabic table is name/description/unit/qty/company/image and carries no money column at all.

- **A money column must be measured on what PRINTS, not what the cell holds (2026-09-02).** Fit sized RATE from its `<input>`'s raw value `4300` while the page printed `$4,300.00`, so a real quotation went out with the rate broken over two lines as `$4,300` / `.00`. TOTAL never showed the bug and that asymmetry is the tell: a computed cell already contains the formatted string, so measuring it measured the truth. The header floor protects the WORD "RATE"; nothing protected the number under it. The value floor formats the widest value with the document's own money formatter and measures it at `pdf.bodyFont` - capped at `MONEY_CAP_FRAC` (22%) so one absurd figure cannot swallow the table, the room coming out of the text columns, which wrap gracefully. Money does not. Guarded by `tests/fit_columns_money_width.test.py`, which runs REAL Chromium because stubbed `measureText` would only test arithmetic against invented font metrics.
**Reads / writes** - DOM only (inline `th.style.width` percentages; `fitEditorColumns` also re-flows `textarea` heights). No DB/file/api. `readEditorColWidths` output is later included by editors in their generate/save payloads.

**Calls / called by** - No bridge calls. `window.readEditorColWidths` is called by all four item-table editors (QUT/INV/DN in their own files, PO in app.js) at generate/preview time. `window.refreshColResizers` is called by the QUT/INV/DN editors after a thead rebuild; the PO editor (app.js) relies on the MutationObserver instead. `window.wireFitColsButton` is called once per editor init (`btnQFitCols`/`btnDnFitCols`/`btnInvFitCols` in their files, `btnPoFitCols` in app.js) to wire the toolbar button. Adds/reads CSS classes `col-resizer` / `col-resizing` / `fitcols-btn` / `just-fitted`.

**Gotchas**
- Widths are written as PERCENT of the current table width so the total stays constant and only the dragged pair changes - the table (`table-layout: fixed; width: 100%`, wrapper `overflow: hidden`) never overflows and no other column shifts.
- Handles attach only between two `data-col-key` columns; the NO./index and delete columns are intentionally skipped. Keying off `data-col-key` (not inline width) means resizing still works when a DN column override dropped its width.
- Seeding rendered widths only runs when the table is VISIBLE (rendered widths are 0 when hidden) - resizing a not-yet-shown editor table would otherwise write bogus widths.
- `readEditorColWidths` is the on-screen→PDF bridge: the backend rebalances these exactly like the fitting function, so what you see is what prints. `MIN_COL_PX = 44`, `HANDLE_W = 8` are magic constants.
- The MutationObserver watches `tHead` `childList`; editors that rebuild the header out-of-band should call `refreshColResizers()` to avoid a race where a drag lands before the observer fires.
- **Fit columns is on-screen only, VISIBLE-table only** (needs `getBoundingClientRect().width > 0` to measure) - the button lives in the active editor's toolbar so this always holds when clicked. Like a manual drag it does NOT auto-preview; the user still previews/saves to print.
- **Header floor is print-scale, not editor-scale.** The bug it fixes ("RAT E"/"TOTA L") only shows in the PDF, because the PDF table (186mm quote / 190mm PO) is **narrower** than the on-screen editor table, so a width that fits a header on screen can still break it in print. The floor is therefore computed against `opts.pdf.widthMm` at `opts.pdf.headerFont`, NOT the editor px. `HEADER_CAP_FRAC = 0.17` caps each header floor at 17% of the print table so only short labels (RATE/TOTAL/UNIT/QTY/…) are protected; a genuinely long header word may still wrap to a 2nd line (the accepted `.q-table thead th { white-space: normal }` behavior). `PRINT_PAD_PX = 16` = cell padding + borders + safety margin - verified every quote header word clears its column by ~1.6mm at 186mm.
- Because the header font is **condensed** (`Bahnschrift`/`Roboto Condensed`/`Arial Narrow` at 10pt for QUT/DN/INV; `Arial` 14pt for PO), the floor must measure at that exact stack - passing a plain-`Arial` font would over-reserve. The measurement font stack in each editor's `wireFitColsButton` call MUST match the doc's `*_styles.css` header font.
- The content **CAP is on the per-column *demand* (px)**, `max(140, tableW*0.42)`, NOT the final share - a lone long column can still end up >40% when the other flexible columns have little content (verified: a long Description reached ~40% while Item/Unit/Qty/Rate/Total stayed compact but above their header floors). This is intended: the long column is exactly what needs the room.
- Header text is read from `input.qc-head-input` (QUT/DN/INV editable headers) with a fallback to `th.textContent` (the PO plain-text RTL headers) - both paths verified. The header floor splits the header on whitespace and reserves the widest WORD (so multi-word headers may still wrap between words).
- Fixed-column detection reads a **body** cell (`[data-k]`) for the image markup, not the header, so an image column is recognized by its `.qc-img-cell`/`.qc-img-wrap` td even though the header th carries no such class.


## Client LPO implementation update (2026-10-02)

`openLpoByNo` resolves an explicit client through `get_lpo`; no number-only
fallback after a client miss, and ambiguous number-only links are refused.
`openLpoForm` receives decoded rows and keeps a pre-dismissal snapshot, including
uncommitted line inputs and the selected file. Shared `Popup.beforeClose` protects
every dismissal; repeated submit is blocked while saving. Accessible names cover
line fields and remove buttons. Search includes quotation, ERP and project.

`showLpoDoc` reads fulfillment and billing in parallel and rejects a stale reply
after navigation. `renderLpoDocSide` shows separate delivery/invoice quantities,
USD canonical billing figures, direct document creation and editor links.
`lpoBillingSection` exposes coverage, partial periods and inferred service dates;
`openLpoInvoicePeriod` confirms tracking through the backend without editing PDFs.
Period creation calls `createInvoiceFromLpo(client,no,period)` with blank recurring
quantities. Signing status states the saved-copy status without inventing a deadline.

`refreshLpos` also exposes unmatched issued references through
`openLpoReferenceReview`: a read-only list with Open document actions, no fuzzy
match or automatic rewrite. The four-tab workspace is an interactive design
suggestion in `docs/designs/client-lpo-workspace.html`, not the production layout.
Checks and limits: [[17-CLIENT-LPO-FIXES]].

## static/activity_capture.js - what the screen did and saw (2026-10-23)

Feeds the activity and problems log ([[03-BACKEND-DATA]] `backend/activity_log.py`). Records `view`
(one of `VIEW_NAMES` became visible - MutationObserver on each view's `hidden`), `click` (a button's
aria-label / title / text - never anything typed), `toast` (MutationObserver on `#toast`; `error` class =
outcome `warning`; a repeat within 1.5 s counted once), `js_error` (window `error` and
`unhandledrejection`; ResizeObserver / "Script error." chatter ignored) and `report`. Sends batches to
`log_client_events({sent, events})`: every 15 s, within 300 ms after an error or report, and on
`pagehide`; a batch the server did not take is kept (newest 200). `window.ActivityCapture` = `{push, send,
report, pending, VIEW_NAMES}`. Runs the same on the desktop app (rows go to the local log).
Tests: `tests/activity_capture.test.js` (runs the real file against a stand-in page).

## static/activity.js - the Activity log screen + Report a problem (2026-10-23)

`showActivity()` (admin only - `#dashActivityBtn`): KPI tiles per outcome (App problem, Server problem,
Network, Refused, Error shown, Report - counts for the period and person; a click filters the table), one
Insights table (When, Who, PC, What happened with its source, Result as a WORD badge, Message), "Show
older" paging (`before_id`), and a popup per row: whose fault in a sentence (`fault()`), the facts, the
message, "What <person> did just before" (the trail from `activity_detail`), and Technical details
(function, call id, exception, traceback, arguments). Default view: problems AND refusals, last 7 days.
`reportProblem()` (everyone - `#dashReportBtn`): one textarea, Send refuses an empty report, the report goes
through `ActivityCapture.report()` with the steps before it. EVERY value goes through `esc()` - log text
comes from people, documents and files. Toast texts have no trailing full stop (the toast is RTL; a "."
lands at the start). Tests: `tests/activity_view.test.js`.
