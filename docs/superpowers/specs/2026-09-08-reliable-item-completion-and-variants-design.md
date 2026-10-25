# Reliable Item Completion and Variant-Safe PO Handoff

**Date:** 2026-09-08

**Status:** Approved

## Goal

Make PO-to-Delivery Note and PO-to-Invoice review reliable in one user action:

- complete all missing language fields with one AI button press;
- translate units locally when Master Data already knows the unit pair;
- keep rows with the same name but different descriptions as separate item variants;
- recommend the correct existing Master Data variant without sending local IDs or private commercial data to Gemini;
- explain and highlight exactly what still blocks document creation;
- never use AI to invent a selling rate.

The two live acceptance cases are PO A-83 and PO A-89.

## Root causes

### A-83 requires more than one AI press

`ItemReviewPopup._suggest` currently sends each row to Gemini when a name or
description input is blank. Unit completion is mixed into that response even
though `mi_unit_map` already provides the company's authoritative Arabic to
English unit vocabulary. Gemini is allowed to return blanks when uncertain, so
one response can fill `unit_en` while leaving `name_en` empty. The next button
press sends the row again and can produce the name.

A-83 has two saved rows:

1. `يلك سلامة`, description `مع طباعة لوكو`, unit `عدد`;
2. `سيارة ابيض`, description `2`, unit `عدد`.

For a Delivery Note, both need English unit completion and the second needs an
English name. For an Invoice, both also need a selling rate. Gemini translation
cannot resolve that commercial requirement. The current validation message
says only that items need highlighted details, but no fields are highlighted
and the message does not name the missing rates.

### A-89 collapses two variants into one row

`poMakeDocument` deduplicates selected PO lines with a key built only from the
normalized item name. A-89 contains two `سبلت جداري` rows, one described as
`طن ونص` and one as `2 طن`. Both are placed in one decision and only the first
PO row supplies its context to the popup.

Master Data correctly contains separate 1.5 Ton and 2 Ton variants. The
frontend grouping key discards the description before those candidates reach
the reviewer.

## Chosen approach

Use deterministic local enrichment first, followed by one bounded AI completion
operation, while preserving a human confirmation boundary for uncertain
variant matches.

This approach is preferred over a minimal grouping patch because it also fixes
the repeated AI interaction and unclear validation. It is preferred over fully
automatic Master Data writes because AI output remains reviewable and cannot
silently merge or relabel materials.

## Item identity

Add one pure `handoffReviewKey(row)` helper. Its identity contains:

- normalized item name;
- normalized description;
- normalized unit.

Whitespace and case differences do not create separate decisions. A different
description or unit does. Company is not part of the material identity because
the same material bought for two clients is still one material.

PO lines with the same full identity continue to share one decision and fan the
result back to all matching saved row indexes. A-89's 1.5 Ton and 2 Ton rows
produce two decisions and two visible review rows.

## Deterministic unit completion

Load `mi_unit_map()` once before readiness is calculated. For every decision
whose English unit is absent or still Arabic, look up the PO unit and store the
known English counterpart as `suggestedUnit`.

`decisionFill` and `reviewRowFromDecision` overlay `suggestedUnit` whenever the
saved decision has no usable English unit. This means readiness is calculated
from the same effective value the popup displays. The popup marks it as a local
Master Data value, not an AI suggestion. The reviewer can edit it, and the
edited value becomes the decision value when the review is accepted.

Unit completion does not consume Gemini quota and does not need another button
press. A-83's `عدد` becomes `EA` for both rows before the popup opens.

## One-click AI completion

Rename the action to **Complete Missing Details with AI**.

One user press performs this bounded sequence:

1. Gather every row that still lacks a required target-language name, has an
   optional description that can usefully be translated, or has an unresolved
   variant choice.
2. Send one batch to the existing provider-neutral `tr_suggest` bridge.
3. Apply every returned name, description, and unit without overwriting a
   nonblank field.
4. Apply a recommended candidate index when it points to a valid candidate for
   that row.
5. Recalculate required language gaps. An optional blank description never
   keeps the row in the repair stage and never blocks submission.
6. If a required name is still blank, make one automatic repair request for
   only the unresolved rows.
7. Apply the repair response and stop. There is never an unbounded retry loop.

The backend prompt requires a best concise English trade name for every input
row. Description remains optional. Arabic echoed into an English field remains
invalid and is discarded. If Gemini omits a requested row, the adapter restores
that row by its request ID with blank proposal fields so the caller always gets
one normalized result per request and can make the bounded repair decision.

The button shows progress for both stages, remains disabled while work is in
flight, and reports the final number of rows completed and the number that still
need review. The user never has to press it twice for the same batch.

## AI-assisted variant selection

Rows with multiple plausible Master Data candidates send a safe candidate list
alongside the source text. Each candidate contains only:

- its zero-based position in that row's candidate list;
- English name;
- English description;
- English unit.

The request never contains local item IDs, item numbers, rates, costs, client
names, PO numbers, quantities, company profile, bank details, or payment data.

Gemini may return `candidate_index` or `null`. The frontend maps a valid index
back to the candidate object already held locally, which contains the item ID.
An invalid or missing index is ignored.

When Gemini selects a candidate, the row visibly shows that selection and uses
the candidate's name, description, unit, rate, and cost through the existing
`decisionFill` and `buildMasterPatch` rules. The user can change the selection.

If no candidate is selected for an ambiguous row, the reviewer may either:

- choose a Master Data candidate;
- use explicitly entered values on this document only.

The flow must not create a new Master Data record from an unresolved ambiguous
variant. That prevents A-89 from growing another duplicate Split AC record.

## Readiness and validation

Readiness remains field-based and script-aware. A separate `choice` requirement
is added for ambiguous candidate sets. The validation result becomes structured:

```text
{
  message: string,
  errors: [{ id, fields: ["name", "unit", "rate", "choice"] }]
}
```

`ItemReviewPopup` clears old errors before each submission, marks the exact
controls with `aria-invalid="true"`, adds visible text labels, opens Details for
hidden candidate errors, scrolls to the first invalid row, and focuses its first
invalid control.

Messages name the actual requirement. Examples:

- `Enter a rate for Safety Vest and White Car. AI completes language details, not prices.`
- `Choose the correct Master Data variant for 2 items, or use document-only values.`
- `Add an English name for White Car.`

An Invoice still cannot proceed without a rate. A Delivery Note remains valid
without one. AI never generates prices or rates.

## Component boundaries

### `static/po_handoff.js`

Owns:

- `handoffReviewKey`;
- deterministic unit enrichment;
- ambiguous-choice state;
- decision-to-row adaptation;
- structured validation;
- mapping component results back to business decisions.

It continues to own Master Data write-back and explicit document-row building.

### `static/item_review_popup.js`

Owns:

- one-click AI orchestration;
- a maximum of two provider calls per user press;
- applying text proposals and candidate selection to visible controls;
- error rendering, disclosure opening, scrolling, and focus;
- normalized result reading.

It remains language-direction configurable for future Quotation English to
Arabic review.

### `backend/translate.py`

Owns:

- safe candidate prompt serialization;
- `candidate_index` parsing and bounds validation;
- a prompt that requires one best trade name per row;
- no persistence and no document-save behavior.

## Failure handling

- Local unit lookup failure leaves units editable and does not block opening the
  review.
- A network or Gemini error leaves all current values intact and reports one
  actionable message.
- A partial first response triggers only one focused repair request.
- A partial repair response remains visible as incomplete and is never silently
  treated as success.
- An invalid candidate index is ignored.
- Cancelling the popup creates no document and performs no new write-back.
- AI suggestions remain proposals until the user presses the primary action.

## Test design

### Frontend component tests

- one press can apply a partial first response and a completing repair response;
- the repair call contains only unresolved rows;
- local units do not need AI;
- a valid AI candidate index selects the local candidate;
- an invalid index cannot select another row's candidate;
- structured validation marks and focuses exact fields;
- a rate-only error explains that AI does not provide prices.

### PO handoff tests

- identical name, description, and unit deduplicate;
- identical name with different descriptions remain separate;
- A-89 produces separate 1.5 Ton and 2 Ton decisions;
- `عدد` becomes `EA` before readiness;
- an ambiguous row cannot write a new Master Data record without candidate or
  document-only mode;
- Invoice validation reports rates separately from language fields.

### Translator tests

- candidate details are included without IDs or commercial data;
- `candidate_index` is parsed only when it is an integer in bounds;
- every requested row is represented in the response model;
- Arabic echoes remain rejected;
- network, busy-model, and batch-size boundaries remain unchanged.

### Live read-only verification

- A-83: both units are locally `EA`, `سيارة ابيض` needs one English name, and
  Invoice still names both missing rates explicitly;
- A-89: both Split AC descriptions appear as separate rows with independent
  candidate selection;
- no Master Data rows are changed by diagnostics.

## Out of scope

- generating or estimating selling rates with AI;
- automatically committing AI output without review;
- changing backend Master Data matching tiers;
- implementing the future Quotation English-to-Arabic caller in this change;
- merging existing Master Data records.
