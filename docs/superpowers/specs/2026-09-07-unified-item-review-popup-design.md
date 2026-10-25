# Unified item-review popup design

**Date:** 2026-09-07

**Status:** approved for implementation
**Scope:** PO save-time catalogue completion, PO to Delivery Note, PO to Invoice, and a reusable language direction for the later quotation English-to-Arabic workflow.

## Problem

PO A-88 proves that the current review UI confuses two different conditions. All 46 rows resolve to records in Materials Master Data, but Fig, Red Plum, and Low Fat Milk have no selling rate. The existing renderer classifies each row only as ready or unready and sends every unready row through the same large form. A missing rate therefore shows blank English-name controls and copy that implies the item is not in Master Data.

The PO save-time workflow already has a second, compact table renderer for missing English names. Maintaining two renderers has produced different layouts, different controls, and different interpretations of the same record.

## Decision

Create one reusable `ItemReviewPopup` class. It owns the item-review presentation and interaction while continuing to use `window.Popup` for modal chrome, focus management, dismissal, stacking, and accessibility.

The class is configured by data rather than by document-specific markup:

- source language and target language;
- source and target field names;
- per-row matched identity, current values, missing fields, suggestions, and optional money requirements;
- title, subtitle, primary action label, and completion callback;
- optional AI suggestion callback and button label.

PO-specific loading, item resolution, document construction, fulfillment linking, and persistence remain outside the class.

## Visible design

The default view is one compact table with one row per item requiring attention.

Each row contains:

1. Source item, with source description and unit as quiet context.
2. Matched or editable target name.
3. Only the fields currently missing, such as target unit or invoice rate.
4. One Details control and one Skip checkbox.

Fields already known by Master Data are shown as confirmed text, not as empty inputs. For A-88, Fig and Red Plum therefore show their matched English names and only a Rate input. Low Fat Milk shows its matched name and description and only a Rate input.

The Details disclosure contains optional or uncommon controls:

- target description;
- private cost;
- alternative Master Data candidates;
- the explicit choice to use entered values for this document without changing Master Data.

The popup introduction is one short sentence. Ready rows are summarized as a count and are not expanded by default. Repeated per-row explanations and radio groups are removed from the normal view.

The footer contains Cancel, an optional AI suggestion action, and the primary completion action. The AI action appears only when at least one visible target-language name or description is blank. It is absent for rate-only review.

## Behavior by caller

### PO save-time catalogue completion

- Direction is Arabic to English.
- Show only items whose English name or description is missing.
- Show the local unit-map result without calling AI.
- Primary action is Save to Master Data.
- AI action is Suggest English when Gemini is configured.
- The already-reviewed state remains per PO and survives restart.

### PO to Delivery Note

- Direction is Arabic to English.
- Show only unresolved target-language fields.
- Rate is optional and hidden in Details unless already relevant.
- Primary action is Create delivery note.

### PO to Invoice

- Direction is Arabic to English.
- Show missing target-language fields and missing rates.
- A record with a known English name but no rate is presented as an existing matched item, never as a new item.
- Primary action is Create invoice.

### Future quotation review

- Direction is English to Arabic.
- The same class accepts Arabic target fields and an eventual Suggest Arabic callback.
- No quotation integration or reverse Gemini backend is included in this implementation. The component boundary is made ready for it without exposing an inactive control.

## Data and safety rules

- A resolver tier is not readiness. Readiness remains field-by-field and script-aware.
- A tied candidate set is never selected automatically.
- AI suggestions are visibly marked, editable, and never committed until the user presses the primary action.
- Existing nonblank Master Data fields are never overwritten by AI.
- Money always travels with its currency.
- Cost remains private and never becomes an invoice rate.
- Skipping a row excludes it from the new document and does not delete or alter its Master Data record.
- Failure to write back to Master Data must not discard a document the user completed.

## Component boundary

The shared module exposes one constructor and one opening method. Callers pass normalized row models and receive normalized decisions. The class owns rendering, event delegation, AI button state, candidate choice, disclosure state, validation messages, and reading values from the form.

The existing `po_handoff.js` keeps its pure business helpers and adapts decisions into the shared row model. It no longer owns separate catalogue and handoff HTML renderers.

## Error handling

- Lookup failure closes no existing document and reports a clear toast.
- AI failure leaves every typed value intact and re-enables its button.
- Validation identifies the exact missing field per row.
- Master Data write failure is reported after the document decision and does not silently replace values.
- Missing popup or bridge dependencies fail with an actionable message rather than an inert dialog.

## Verification

Tests must prove:

- A-88 resolves all 46 names and presents exactly three rate-only rows.
- Rate-only rows do not render editable English-name fields or new-item wording.
- PO save-time and handoff callers instantiate the same class.
- Arabic-to-English and English-to-Arabic configurations render the correct target fields.
- AI appears for missing language fields and not for rate-only rows.
- Candidate ties require an explicit choice.
- Existing write-back safety and persistent reviewed-state tests remain green.
- Shared popup focus, dismissal, direction, and action ordering contracts remain green.
- Both application smoke tests pass.

## Out of scope

- Automatically inventing or estimating prices.
- Reverse English-to-Arabic Gemini prompting.
- Changing how Master Data identifies materials.
- Merging conflicting material records.
- Altering saved A-88 rows or its generated PDF.
