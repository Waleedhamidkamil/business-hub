# 14 - UI contracts

**Purpose** - the shared rules. Not "how a screen works" (that is what the other
docs are for) but the handful of things that must be **the same everywhere**, so
that a popup added next month behaves exactly like a popup added last year.

Each contract here has three parts, and it is only a contract when it has all
three:

| | |
|---|---|
| **One implementation** | the code every caller goes through, so the rule cannot be re-invented by accident |
| **The reasons** | written at the implementation, so the next person can tell a rule from a preference |
| **A test that fails** | a rule nothing enforces is a suggestion |

If you are adding a shared behaviour, add it here the same way.

---

## 1. Popups - `static/popup.js`, `window.Popup`

**One implementation.** Everything that opens over the page goes through
`Popup.open(spec)` - or `Popup.mount(el)` for the five dialogs whose CONTENT is
authored in `index.html`. `showModal(...)` is a thin wrapper over `open`, so all
~55 of its call sites get the rules for free and cannot drift. **As of
2026-08-26 there are no exceptions**: `tests/popup_contract.test.js` fails if any
file under `static/` builds a backdrop of its own.

**Why it exists.** The app grew popups one at a time and ended up with three
vocabularies for the same idea - `.modal-*` (the confirm dialog + five static
blocks in `index.html`), `.imgf-*` (the Find-pictures progress dialog) and
`.cost-linkpicker-*` (the three costing overlays). They disagreed on everything
a reader notices: whether Esc closes, whether the dim background closes, whether
there is an X, where the title sits, how wide the panel is, whether the page
behind still scrolls, whether Tab can walk out of the dialog into the page.

**The rules** (each one is tested in `tests/popup_contract.test.js`):

1. **Every popup has a title.** A box with no name gives the reader nothing to
   orient by and a screen reader nothing to announce, so it is required and the
   panel is `aria-labelledby` it. A missing title logs an error rather than
   quietly substituting one.
2. **Three ways out: Esc, the dim background, and an X** - unless
   `dismissible: false`, which is reserved for a genuine choice the app cannot
   make for the user (the DN "one line or itemised?" question). A popup with
   one way out must say why.
   - A drag that *ends* on the backdrop does not close it. Only a click that
     **began** there counts, so selecting text in the body is safe.
3. **Focus goes in, stays in, and comes back.** The field first if the body has
   one (that is what the user came to type in), else the primary action. Tab is
   trapped over the **whole panel** - the old modal trapped over buttons only,
   so any popup with an input leaked focus into the page behind. On close,
   focus returns to whatever opened it.
4. **The page behind does not scroll.** Locked while any popup is open,
   restored exactly when the last one closes (a counted lock, so closing an
   inner popup does not unlock the page under its parent). **The lock is
   OWNED (`scrollHeld`), not inferred from `body.style.overflow`** (fixed
   2026-10-09): a closing popup leaves the stack at once but releases the page
   only after its 180ms fade, and a popup opened in that window used to read
   back the lock's own `hidden` as the page's setting and restore it on close -
   Insights and every page-scrolled screen stopped scrolling until a restart.
   Submit in the document preview (close, then the fulfilment popup a bridge
   call later) hit it. Pinned by rule 4b in `tests/popup_contract.test.js`.
5. **One set of widths** - `sm 380 · md 480 · lg 680 · xl 900 · full`. Pick by
   what the popup IS, not by how much text it happens to have: a question is
   `sm`, a document preview is `lg`, and anything the user WRITES in takes
   `xl` - the Terms editor is `xl` with auto-growing textareas, because a term
   you cannot read back is a term you cannot check before it prints. `full` is in the list rather than being an escape hatch, and it is only for a popup that shows a DOCUMENT (the LPO viewer). An unknown size
   falls back to `md`. "A bit wider than that other one" is how a UI stops
   looking designed.
6. **One footer row**, actions in order, primary last.
   - **THE PLACEMENT RULE: X at the trailing edge of the head, title at the
     leading edge, actions at the trailing edge of the footer.** In an English
     popup that reads **X top-right, name top-left, buttons bottom-right**. An
     Arabic popup mirrors all three - the same rule seen in a mirror, not a
     second arrangement. A Close that moves between popups makes the user hunt
     for it every time. The only thing a caller must get right is the ORDER of
     `actions`: secondary first, primary last.
   - **It is one CSS rule and no popup may override it.** The X is the last
     child of the head; the actions are `flex-end`; `direction` does the rest.
   - **Direction is keyed on the `dir` ATTRIBUTE, and that is load-bearing.**
     The document is `<html dir="rtl">`. An *author* rule setting `direction`
     on the panel beats the UA rule behind `dir`, so `.modal { direction: rtl }`
     silently overrode `dir="ltr"` on **fifteen popups at once** - every English
     dialog that asked for LTR rendered RTL, while the ones that asked for
     nothing got LTR from a `:not([dir])` rule. Perfectly inverted, which is
     worse than either. That is why buttons and X sat on opposite sides and why
     English text in the Terms editor was right-aligned. `direction` no longer
     appears on `.modal` at all; the four `.popup` rules own it and the `[dir]`
     ones come last so the attribute always wins.
   - **Two classes on one element at the same specificity is the trap.** Every
     action row is `.modal-actions .popup-actions`; when both set layout, which
     one wins is decided by their ORDER in the stylesheet - a reorder away from
     flipping every popup's buttons. Layout now lives in `.popup-actions` alone.
   - The bulk-paste wizard was also the one dialog whose footer never got the
     shared class at all; §9e fails if any authored footer is not a
     `.popup-actions`, and §9d fails if `.modal` sets `direction` or
     `.modal-actions` sets layout.
7. **The body is TEXT** unless `html: true` is opted into - document and client
   data flows through these bodies. The panel's own structure is built from
   elements, so the only `innerHTML` in the file is that opt-in.
8. **Colour never carries meaning.** A destructive action is a word
   ("Delete"), never a red button alone ([[waleed-colour-blind]]).
9. **Popups stack.** One opened from another sits above it, takes Esc first,
   and closing it leaves the parent open. `close()` is idempotent - a second
   call cannot re-resolve or re-fire `onClose`.

**The head has three optional parts**, and which one a thing belongs in is the
rule that stops popups re-inventing their arrangement:

| | for | example |
|---|---|---|
| `icon` / `badge` | WHAT this popup is about | the document kind on the To-Do preview |
| `toolbar` | controls that work the CONTENT | the LPO viewer's page nav and zoom |
| `actions` (footer) | ways to LEAVE, decisions | Cancel / Save / Mark submitted |

A sticky strip at the top of the body (`.po-link-sticky`) holds tabs and search
boxes: they filter the content, so they travel with it and stay put while it
scrolls.

**Header text that arrives LATER goes through `handle.setSubtitle(text)`** - a
document's client and date after the bridge answers, an item count after the PO
loads. It creates the subtitle line if the popup opened without one, so a caller
never has to know which case it is in. **Do not keep your own meta element for
this.** Migrating a hand-rolled header to `Popup.open` deletes whatever divs it
used to build, and a reader left pointing at one of them returns `null`; because
these popups paint their loading text, then `await` a bridge call, then paint the
answer, that null throws AFTER the loading text is on screen - an unhandled
rejection nobody sees and a spinner that never stops. That is exactly what
happened to `po_link.js`'s `#poDetMeta` (both PO-link directions, 2026-09-06).
`tests/po_link_detail_renders.test.js` fails if any `static/*.js` looks up an id
that neither it nor `index.html` creates.

**`Popup.mount(el, opts)`** applies the whole contract to a dialog written as
markup: Esc, the dim background, the focus trap, the scroll lock, the stack and
focus restored on close. The module keeps its own show/hide call sites and its
own `id`-wired innards - it just routes them through here. `[data-popup-close]`
on any button inside makes it a close button, re-pointed on every mount (wiring
it once means the X silently dies the second time the dialog opens).

```js
const { result } = Popup.open({
    title: "Delete this document?",
    subtitle: "PO A-42 · 12/8/2026",       // optional
    body: "The PDF and the record both go.",
    size: "sm",
    actions: [{ label: "Cancel", value: "cancel", cls: "ghost" },
              { label: "Delete", value: "ok", cls: "primary" }],
});
if (await result === "ok") { … }
```

`Popup.alert(title, body)` and `Popup.confirm(title, body, opts)` are shorthands
over the same box. `Popup.top()` / `Popup.closeTop()` / `Popup.count()` exist
for code that must react to a popup it did not open - the global Esc handler
uses `count()` to know it must stay out of the way.

**Gotchas**

- **`showModal` stamps the legacy ids** (`modalBackdrop`, `modalTitle`,
  `modalBody`, `modalActions`) onto the live panel, because a few callers reach
  in by id after opening: `master_data_fill` wires its inputs inside
  `#modalBody`, and `inv.js` / `item_master.js` enable and disable the primary
  button through `#modalActions` while the dialog is open. The static block in
  `index.html` was **removed** when this landed - leaving it would have meant
  two elements sharing those ids, with `getElementById` returning the dead one.
- **The panel keeps `.modal`**, so it is still the same element the app always
  had; `.popup-*` adds the structure that used to be re-invented per popup, and
  the glass look on top of it.
- **The look is a SOLID panel (2026-08-26), and glass was tried twice before
  landing here.** Both attempts are worth knowing about, because the pull to
  try again is real:
  - *First attempt* blurred the backdrop **and** the panel. `backdrop-filter`
    makes an element a *backdrop root*, so the panel's own filter saw only what
    was between the two - nothing. It did literally nothing and the result was
    a tinted card. (MDN lists this as the commonest reason `backdrop-filter`
    "has no visible effect".) Worth remembering if anything in this app ever
    nests two of them again.
  - *Second attempt* was real liquid glass, built from Waleed's reference card:
    an SVG lens (three `feDisplacementMap` passes at different scales, one per
    colour channel, screen-blended for the fringing a real lens edge has, then
    frosted), a five-layer inset edge, and a light fill - with the displacement
    map ramping only in the outer 14% of each axis so the rim bent and the text
    did not. It worked, and it still did not FIT: these popups carry item
    tables, forms and money, they open over a dense editor, and every gram of
    translucency spends legibility on decoration.
  - **What ships:** `background: var(--surface)`, a **layered** shadow (a tight
    contact shadow for edge weight plus two wide ones for lift - one blurry
    shadow always reads as a sticker), the page behind blurred gently by the
    backdrop so the panel is unmistakably in front, hairlines under the head and
    over the footer, a **tinted footer** so the decision row is visibly not part
    of the content and the seam is hidden when the body scrolls under it, and
    one 22px gutter throughout. Dark restates only the shadow - a light-theme
    shadow is invisible on a dark ground and the panel would float with no edge.
  - `tests/popup_contract.test.js` §9d asserts the panel is opaque and that no
    orphan of either glass attempt is left behind. If someone wants glass again,
    that is the assertion asking them to explain why THIS app is the one where
    see-through numbers are a good idea.
  - **Action buttons are small but not tiny**: 32px is the floor, 12.5px the
    label. Below that a button stops being easy to hit, which is not a trade
    worth making for tidiness. The old 100px `min-width` is beaten explicitly -
    three buttons at that width read as a toolbar, not a decision.
  - **`.modal` is RTL** (Arabic-first shell), so `.popup:not([dir])` forces LTR
    and `.popup.modal-legacy:not([dir])` puts RTL back for `showModal`'s Arabic
    confirmations. The `:not([dir])` is what keeps an explicit `dir` on the
    panel winning over both - that is how the PO settings dialog stays RTL.
  - All of it is pinned in `tests/popup_contract.test.js` §9d.
- `showModal`'s dialog stays **RTL** with its reversed action row - its bodies
  are Arabic confirmations. Popups built directly are LTR unless they say `dir`.
- **Everything is migrated (2026-08-26).** 23 popups across what used to be four
  vocabularies: the To-Do / Needs-attention preview, both document-workflow
  popups, the four PO-linker dialogs, the three costing overlays, the DN
  signed-copy panel, the LPO viewer, the image cropper, the import preview, the
  PO quotation picker, the company-classification ask, the shortcut sheet, the
  quotation terms editor, the DN and invoice importers, the three settings
  dialogs, and - through `mount` - signing, bulk paste, the data manager's
  editor and client prompt, and the Find-pictures progress dialog.
  `.todo-md-backdrop`, `.cost-linkpicker-backdrop`, `.cotag-backdrop`,
  `.ks-backdrop` and `.imgf-backdrop` are gone as concepts; the bespoke chrome
  CSS that styled their heads and action rows went with them.
- **Two dialogs are deliberately not dismissible**: the Find-pictures progress
  (a run is in flight and Esc would hide the log it exists to produce) and the
  DN "one line or itemised?" question (the app cannot answer it for you).
- **`className` goes on the PANEL, and the handle hands you that panel**
  (2026-08-26). `handle.panel` IS `.your-class` - so a binder that then does
  `panel.querySelector(".your-class")` gets `null` and throws on its first
  `root.querySelector(...)`. That does not fail loudly: the dialog still opens,
  fully rendered, with **nothing wired** - Save, Cancel and every field dead,
  and only a console error. It is what killed the per-doc-type settings modal
  in this migration; the invoice's whole **Bank accounts** editor was
  unreachable and Save did nothing on all three doc types, while
  `openPoSettings` beside it had been converted correctly. Bind to the panel
  you were handed: `const root = backdrop;`. Pinned by
  `tests/popup_contract.test.js` §9g, which discovers every settings binder in
  `doc_settings.js` rather than listing them, so a new one cannot skip the rule.
- **Where an action must NOT close the popup**, give it `keep: true` and close
  it yourself. Used by every action that can fail (the To-Do state changes leave
  the preview up with its buttons live when the write fails) and by every action
  whose result you need to SEE (Reset to default, Use best for all, Restore
  defaults).

---

## 2. Item review - `static/item_review_popup.js`, `window.ItemReviewPopup`

**One implementation.** Every workflow that must complete or confirm item
details uses `ItemReviewPopup` for content and `Popup` for modal chrome. The PO
save-time Master Data completion and PO-to-Delivery Note / Invoice handoff both
pass row models into the same class. Callers own business meaning and
write-back; the component owns item layout, interaction, AI proposals, and
normalized results.

**Why it exists.** The earlier PO implementation had a compact catalogue table
and a separate document-review form. They used different markup and behavior,
and the document version rendered every possible field even when Master Data
already knew most of them. On A-88, three matched bilingual items were missing
only a sell rate, but the screen looked like three unknown materials and showed
empty English inputs. One component makes that state impossible to present in
two contradictory ways.

**The rules** (enforced by `tests/item_review_popup.test.js` and
`tests/po_handoff_review.test.js`):

1. Render by missing field, not by a generic ready/unready state. Known names
   and units are confirmed text; only missing values become inputs.
2. A matched item missing only a rate is never described as new. It shows the
   source name, confirmed target name, known unit, and one Rate control.
3. Rare choices stay under Details: description, private cost, candidate
   matches, and document-only values. Skip stays available in the main row.
4. Money is absent from catalogue completion. Invoice review exposes Rate only
   when it is missing; Delivery Note can remain unpriced.
5. **Complete Missing Details with AI** appears when a target-language detail
   or ambiguous variant needs help and the configured provider is available.
   One press makes one batch request and at most one automatic repair request
   for required names still blank. It never retries optional descriptions,
   never sends rows already marked Skip or document-only, never overwrites a
   nonblank field, and marks every proposal in words until the user edits it.
6. Language direction is configuration, not a second renderer. `sourceLanguage`
   and `targetLanguage` control field keys and text direction. **In use both
   ways since 2026-09-10**: a purchase order shows Arabic and collects English,
   a quotation shows English and collects Arabic into an RTL `name_ar` box.
   The AI is asked in the same direction, or the model answers in the language
   already on the page and the popup silently applies nothing.
7. AI availability is checked asynchronously after the popup is interactive.
   A slow provider-status call must never delay the primary action.
8. Structured validation identifies row IDs and exact fields. The component
   applies `aria-invalid`, visible error text, opens Details for candidate
   errors, scrolls to the first invalid row, and focuses its first invalid
   control. Rate errors explain that AI does not provide prices.
9. AI candidate proposals use only a zero-based position within that row. The
   component maps it to the local candidate and ignores non-integer or
   out-of-range values, so one row can never select another row's record.
10. Candidate choices are not cut to an arbitrary visual limit. Every resolver
    candidate remains available under Details so a lower-ranked but
    description-correct material cannot disappear.
11. **Choosing a candidate re-seeds the whole row from that record** - its
    description, its unit, its price and its cost, and BLANK where the record
    holds none. A row opens pre-filled from whatever the resolver matched
    automatically, which is a different record; leaving its values in the boxes
    made `read()` report them as typed, so one product's private cost was put
    on the document and written into another product's master record. A box the
    person typed in is theirs and is never re-seeded (added 2026-09-08).
12. **A slow request says so.** After a few seconds the AI button counts the
    wait ("Completing... 12s"). Six real calls on 2026-09-10 measured 11.7s to
    47.7s; a button frozen on "Completing..." for three quarters of a minute is
    indistinguishable from a broken one, and was reported as exactly that
    (added 2026-09-10).
13. **The AI request holds the primary action.** While a suggestion request is
    out, both the AI button and the primary are disabled. A primary that stayed
    live read a half-filled form, and the answer then landed on a popup that
    had already closed (added 2026-09-08).
14. **Skip and document-only are answers, not gaps.** The count in the toast
    after an AI press excludes them; counting them told the user that rows they
    had just excluded on purpose still needed review. A partly-completed batch
    (a long list is several requests) reports what did NOT land, as a problem
    (added 2026-09-08).
15. **No exit discards typed work silently.** The popup is not dismissible:
    Esc, the dim background and Cancel all route through one "Discard what you
    have entered?" question, and only once there is something to discard. Forty
    rows of typing must not vanish to a stray keypress (added 2026-09-08).

**What the component may be given** (the caller's half of the contract):

- A row is only listed when something is genuinely missing, so the caller
  decides readiness - and **a guess is not a value**. `master_data.resolve_items`
  leaves `fill` empty and sets no top-level `item_id` for the `similar` tier
  exactly so nothing is applied without a person, but
  `MasterDataFill.fillFor` falls back to `suggested_fill`, which is right in the
  paste grid (the user ticks each field) and wrong here (a filled row is a row
  the caller marks ready and never shows anyone). `po_handoff.appliedFill`
  therefore refuses a `similar` fill outright. Measured on the live catalogue
  2026-09-08: 267 of 278 fuzzy matches were being applied with no review, 174
  of them carrying a rate or a cost - "Whiteboard Set" silently became
  Whiteboard Eraser at $4.60 on a delivery note.
- Recording that a row was offered is **two** memories, not one. Confirming
  persists to `doc_meta`; cancelling only quietens the row for the session.
  Persisting a cancel retired the row for good, because nothing in the app
  passes `force` to `poReviewNewItems` (added 2026-09-08).

---

## 3. Contracts that already existed, and where they are written down

These predate this file. They are listed so the set is findable from one place;
the detail stays where it is.

| Contract | One implementation | Written up in |
|---|---|---|
| **Never red/green** - a state is a WORD; red is reinforcement, and there is no green counterpart | - (a review rule, enforced per feature test) | [[08-STYLES-TEMPLATES]], [[waleed-colour-blind]] |
| **Private figures** - cost and profit are teal, live in the `.qc-private` strip, and hide together | `paintRowEconCells`, `body.privacy-on` | [[05-FRONTEND-SHELL]] |
| **Money keeps the currency it was typed in** - per-row `cost_ccy`, converted only by the deal peg | `row_cost_currency` / `_line_econ` | [[03-BACKEND-DATA]] |
| **Profit is computed, never stored** | `_line_econ` + its JS port | [[03-BACKEND-DATA]], [[05-FRONTEND-SHELL]] |
| **Unknown ≠ zero** - an uncosted line reads "-" and warns; it never reports 0 | `computeDocEcon` line states | [[05-FRONTEND-SHELL]] |
| **Editor row cells** - the cost cell, the image cell, the profit cell are built by shared helpers | `rowCostCellHTML` / `rowImageCellHTML` / `rowProfitCellHTML` | [[05-FRONTEND-SHELL]] |

---

**Owns:** `static/popup.js`.
**Tested by:** `tests/popup_contract.test.js`.
**See also:** [[08-STYLES-TEMPLATES]] (the look), [[07-FRONTEND-SUPPORT]] (the
screens that open these), [[11-RUN-AND-MAINTAIN]] (how to run the tests).


## Optional dirty-form close guard (2026-10-02)

`Popup.open` accepts `beforeClose(value, handle)`, returning true (possibly
asynchronously) to allow closing. The same guard handles X, Escape, backdrop,
Cancel and programmatic close. One pending guard suppresses repeated dismissal;
false or rejection leaves the form intact. Callers without a guard retain the
original synchronous close behavior. Client LPO declares its baseline before
`onOpen`, snapshots live fields before dismissal and allows a successful Save.
`tests/popup_contract.test.js` executes refusal/deduplication/approval; real LPO
browser QA exercises all four dirty dismissal routes. [[17-CLIENT-LPO-FIXES]].
