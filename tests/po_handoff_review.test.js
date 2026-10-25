/*
   Make DN / Make Invoice from a PO - the decisions, not the pixels
   (2026-09-05, static/po_handoff.js).

   Three things can go wrong on this path, and each of them is silent:

   1. **Arabic reaches a client's document.** `master_data.resolve_items` names
      a record with `prefer(name_en, name_ar, lang)`, which FALLS BACK, so an
      item known only in Arabic comes back matched - at a confident tier -
      carrying Arabic text. Any readiness check written against the tier ships
      that to a client. Readiness must be a SCRIPT test, per field. The backend
      half of this is pinned in `po_make_document_resolve_backend.test.py`.

   2. **The private cost prints as the sell price.** `IMPORT_KEY_ALIASES.rate`
      in app.js is `[..., "cost"]` and `smartMapRow` falls through empty values,
      so a row with a cost and no rate yields `rate = cost`. That is why this
      path builds its rows explicitly, and why there is a source assertion below
      that it never calls `smartMapRow`.

   3. **A master record is damaged by an over-eager patch.** `save_record`
      treats a PRESENT-but-empty money key as "delete this amount and its
      currency", and a blank language-free `unit` as "this item has no unit" -
      clearing BOTH languages. So the patch is built by INCLUSION. See
      `po_master_writeback_backend.test.py` for those edges proven against the
      real function.

   Pure functions and a DOM double - no browser needed.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ---------------------------------------------------------------- harness
const mdfSrc = fs.readFileSync("static/master_data_fill.js", "utf8");
const itemReviewSrc = fs.readFileSync("static/item_review_popup.js", "utf8");
const pohSrc = fs.readFileSync("static/po_handoff.js", "utf8");
const context = {
  console, window: {}, document: {}, setTimeout() {},
  Promise, Map, Set, Object, Array, Number, String, JSON, parseFloat, parseInt,
};
vm.createContext(context);
vm.runInContext(mdfSrc, context, { filename: "static/master_data_fill.js" });
vm.runInContext(itemReviewSrc, context, { filename: "static/item_review_popup.js" });
vm.runInContext(pohSrc, context, { filename: "static/po_handoff.js" });

const H = context.window.__poHandoffInternals;
assert(H, "po_handoff internals were not exposed");
assert(context.window.MasterDataFill, "MasterDataFill was not exported");
assert(context.window.__masterDataFillInternals === context.window.MasterDataFill,
  "the old __masterDataFillInternals name must still resolve to the same object");

// ================================================================ 1. script
assert(H.isArabic("مفتاح ربط"), "Arabic text must be detected");
assert(H.isArabic("مسمار 12"), "Arabic mixed with digits is still Arabic");
assert(!H.isArabic("Spanner"), "English must not be flagged");
assert(!H.isArabic("PCS"), "an English unit must not be flagged");
assert(!H.isArabic(""), "blank is not Arabic");
assert(!H.isArabic(null), "null is not Arabic");

// The handoff identity is a material identity, not just a name. A-89 contains
// two wall splits with different capacities and both must reach the review.
assert(typeof H.handoffReviewKey === "function", "handoff review keys must be testable");
const split15 = H.handoffReviewKey({
  name: "سبلت جداري", description: "طن ونص", unit: "عدد",
});
const split20 = H.handoffReviewKey({
  name: "سبلت جداري", description: "2 طن", unit: "عدد",
});
assert(split15 !== split20,
  "same-name products with different descriptions must remain separate");
assert(split20 === H.handoffReviewKey({
  name: " سبلت  جداري ", description: " 2 طن ", unit: " عدد ",
}), "harmless whitespace must not split an identical product");

// A-83 uses the established Arabic unit عدد. This is local catalogue data,
// so the review must be ready with EA before Gemini is involved.
assert(typeof H.applySuggestedUnit === "function", "local unit enrichment must be testable");
const locallyCompleted = H.applySuggestedUnit({
  poRow: { name: "سيارة ابيض", unit: "عدد" },
  fill: { item: "White Car", unit: "عدد" },
  mode: "auto", typed: {}, result: {},
}, { "عدد": "EA" });
assert(locallyCompleted.suggestedUnit === "EA", "A-83 must get EA from Master Data");
assert(H.decisionFill(locallyCompleted).unit === "EA",
  "readiness and the popup must use the same local unit");

// ================================================================ 2. readiness
// THE case: a high tier carrying Arabic. Readiness must refuse it.
const arabicExact = { item: "سبلت جداري", unit: "عدد" };
let r = H.readinessFor(arabicExact, { unit: "عدد" }, "dn");
assert(!r.ready, "an Arabic name must never be 'ready' - whatever the tier said");
assert(r.missing.includes("name"), "and the name is what is missing");
assert(r.missing.includes("unit"), "the Arabic unit is missing too - readiness is per FIELD");

const good = { item: "Spanner", unit: "PCS", rate: 12.5, rate_ccy: "USD" };
assert(H.readinessFor(good, { unit: "قطعة" }, "dn").ready, "a fully English row is ready");
assert(H.readinessFor(good, { unit: "قطعة" }, "inv").ready, "...for an invoice too, it has a rate");

// A DN may be unpriced; an invoice may not - an unpriced invoice saves as a
// draft with no PDF and set_po_fulfillment refuses to link it.
const noRate = { item: "Spanner", unit: "PCS" };
assert(H.readinessFor(noRate, { unit: "قطعة" }, "dn").ready,
  "a delivery note is legitimately unpriced");
assert(!H.readinessFor(noRate, { unit: "قطعة" }, "inv").ready,
  "an invoice needs a rate");
assert(H.readinessFor(noRate, { unit: "قطعة" }, "inv").missing.includes("rate"), "...and says so");

// A PO line with no unit does not demand one.
assert(H.readinessFor({ item: "Spanner" }, {}, "dn").ready,
  "no unit on the PO means no unit is required on the document");

// ================================================================ 3. grouping
const TAGS = { "ابراج البدر": "internal", "TDC": "client" };
const ROWS = [
  { name: "أ", company: "TDC", qty: 2 },
  { name: "ب", company: "ابراج البدر", qty: 1 },   // our own office
  { name: "ج", company: "", qty: 5 },              // addressed to nobody
  { name: "د", company: "HDEC", qty: 3 },          // untagged => billable
  { name: "ه", company: "TDC", qty: 1 },
];
let groups = H.billableGroups(ROWS, TAGS, {});
assert(groups.length === 2, `expected 2 billable companies, got ${groups.length}`);
assert(groups[0].company === "TDC" && groups[0].lines.length === 2, "TDC keeps both its lines");
assert(groups[1].company === "HDEC", "an UNTAGGED company is billable - that is the app's rule");
const flat = groups.flatMap(g => g.lines.map(l => l.index));
assert(!flat.includes(1), "an internal line is never offered");
assert(!flat.includes(2), "a line with no company is never offered");
assert(flat.includes(0) && flat.includes(4) && flat.includes(3), "indexes are the SAVED row indexes");

// One company => the caller takes them all without asking.
assert(H.billableGroups([ROWS[0], ROWS[4]], TAGS, {}).length === 1,
  "a single-company PO produces one group, so no picker is shown");

// Already-fulfilled lines are still listed, but flagged. Keys arrive as STRINGS.
groups = H.billableGroups(ROWS, TAGS, { "0": { source_no: "DN-0007" } });
const line0 = groups[0].lines.find(l => l.index === 0);
assert(line0.done && line0.done.source_no === "DN-0007",
  "an already-delivered line must be recognised through a string key");
assert(!groups[0].lines.find(l => l.index === 4).done, "...and only that line");

// ================================================================ 4. row build
const built = H.buildHandoffRow(
  { name: "مفتاح ربط", company: "TDC", qty: 4, unit: "قطعة", image: "data:image/png;base64,AAA" },
  { item: "Spanner", description: "Adjustable", unit: "PCS",
    rate: 12.5, rate_ccy: "USD", cost: 9000, cost_ccy: "IQD" });
assert(built.item === "Spanner", "the ENGLISH name goes on the document");
assert(built.qty === 4, "qty carries straight across");
assert(built.unit === "PCS", "the English unit, not the PO's Arabic one");
assert(built.rate === 12.5 && built.rate_ccy === "USD", "the rate rides with its currency");
assert(built.cost === 9000 && built.cost_ccy === "IQD",
  "and the cost with ITS OWN currency, unconverted");
assert(!("company" in built), "the PO's company must never reach the document row");
assert(!("image" in built), "nor the PO's picture");
assert(built.description !== "TDC", "and it must not be smuggled into the description");

// The cost must never be readable as a rate.
const costOnly = H.buildHandoffRow({ qty: 1 }, { item: "Spanner", cost: 9000, cost_ccy: "IQD" });
assert(!("rate" in costOnly),
  "a row with a cost and NO rate must not grow one - that is the smartMapRow bug");

// An Arabic description is dropped rather than printed.
const arDesc = H.buildHandoffRow({ qty: 1 }, { item: "Spanner", description: "وصف عربي" });
assert(arDesc.description === "", "an Arabic description is dropped, not printed");

// Money never travels bare.
const bareRate = H.buildHandoffRow({ qty: 1 }, { item: "X", rate: 5 });
assert(bareRate.rate_ccy === "USD", "a rate with no stated currency still gets one");

// ================================================================ 5. patches
const AR = "مفتاح ربط";
const poRow = { name: AR, unit: "قطعة" };

// (a) accepting an English item whose Arabic slot is EMPTY records the pairing.
let d = { mode: "candidate", query: AR, poRow,
          candidate: { item_id: 7, name: "Spanner", name_ar: "", unit_ar: "" }, result: {} };
let patch = H.buildMasterPatch(d);
assert(patch && patch.name_ar === AR, "a blank Arabic slot is filled with the PO's name");
assert(patch.unit_ar === "قطعة", "and the Arabic unit likewise");
assert(!("name_en" in patch), "accepting must not push anything over the English side");
assert(H.writeTargetId(d) === 7, "the write goes to the candidate the user picked");

// ...but a slot that is already taken is LEFT ALONE. Overwriting it would
// silently relabel a different product.
d.candidate = { item_id: 7, name: "Spanner", name_ar: "برغي", unit_ar: "قطعة" };
assert(H.buildMasterPatch(d) === null,
  "a record that already pairs with another name must not be rewritten");

// A candidate may still need a rate. The shared row keeps that rate input
// visible, so its typed money must ride into both the document and that chosen
// Master Data record.
d.typed = { rate: "18", rate_ccy: "USD" };
patch = H.buildMasterPatch(d);
assert(patch.price_native === "18" && patch.price_native_ccy === "USD",
  "a rate entered beside a candidate is saved to the chosen record");
const candidateFill = H.decisionFill(d);
assert(candidateFill.item === "Spanner" && candidateFill.rate === "18" && candidateFill.rate_ccy === "USD",
  "a candidate's typed rate reaches the document too");

// (b) manual entry.
d = { mode: "manual", query: AR, poRow,
      result: { item_id: 42, name_en: "", name_ar: AR, unit_en: "", unit_ar: "قطعة" },
      typed: { name_en: "Spanner", unit_en: "PCS", description_en: "",
               rate: "12.5", rate_ccy: "USD", cost: "", cost_ccy: "IQD" } };
patch = H.buildMasterPatch(d);
assert(patch.name_en === "Spanner", "the English name is sent as name_en...");
assert(!("name" in patch), "...never as a bare `name`, which save_record silently drops");
assert(patch.unit_en === "PCS", "the English unit is sent as unit_en...");
assert(!("unit" in patch), "...never as a bare `unit`, which would clear BOTH languages");
assert(!("name_ar" in patch), "the record already has an Arabic name - leave it");
assert(!("unit_ar" in patch), "and an Arabic unit");

// A locally known unit is part of the accepted result even when it renders as
// confirmed text instead of an input. It must be written back with a new name,
// otherwise the next flow depends on some other record keeping the unit map alive.
const localUnitWrite = H.buildMasterPatch({
  mode: "manual", query: "سيارة ابيض",
  poRow: { name: "سيارة ابيض", unit: "عدد" },
  result: { item_id: 42, name_en: "", unit_en: "", unit_ar: "عدد" },
  fill: {}, suggestedUnit: "EA", typed: { name_en: "White Car" },
});
assert(localUnitWrite.unit_en === "EA",
  "the locally completed A-83 unit is persisted with the accepted English name");
assert(!("description_en" in patch), "an untouched description is ABSENT, not empty");
assert(patch.price_native === "12.5" && patch.price_native_ccy === "USD",
  "money is written with its currency");
assert(!("cost_native" in patch),
  "an EMPTY cost box must be absent - present-and-empty NULLs the stored cost "
  + "and its currency, which is how a catalogue loses months of work");
assert(!("cost_native_ccy" in patch), "and its currency must not be sent alone either");
assert(H.writeTargetId(d) === 42, "manual entry edits the record that matched");

// A genuinely new item has no target, so the caller creates one.
d.result = {};
assert(H.writeTargetId(d) === null, "no match means create, not edit");
patch = H.buildMasterPatch(d);
assert(patch.name_ar === AR, "a new record records the PO's Arabic name too");
assert(patch.unit_ar === "قطعة", "and its Arabic unit");

// An English PO unit is not filed as an Arabic one.
d.poRow = { name: AR, unit: "KG" };
patch = H.buildMasterPatch(d);
assert(!("unit_ar" in patch), "an English unit on the PO is not written into unit_ar");

// (c) 'use here only' and 'leave off' never write.
assert(H.buildMasterPatch({ mode: "asis", query: AR, poRow, typed: { name_en: "X" } }) === null,
  "'use here only' must not touch Master Data");
assert(H.buildMasterPatch({ mode: "exclude", query: AR, poRow }) === null,
  "an excluded row must not touch Master Data");

// (d) The line's description names the VARIANT (2026-10-03, A-117: دهن هيدروليك
// is 18 L and 20 L). Picking one in the review records the PO's description on
// it - into a blank slot only - so the next order finds that variant unasked.
const oilRow = { name: "دهن هيدروليك", description: "20 لتر", unit: "عدد" };
const oil = (descAr) => ({ mode: "candidate", query: oilRow.name, poRow: oilRow, result: {},
  candidate: { item_id: 9, name: "Hydraulic Oil", name_ar: oilRow.name, unit_ar: "عدد",
               description_en: "20 L", description_ar: descAr } });
patch = H.buildMasterPatch(oil(""));
assert(patch && patch.description_ar === "20 لتر",
  "the picked variant learns the PO's Arabic description: " + JSON.stringify(patch));
assert(!("description_en" in patch), "and nothing is pushed over its English description");
assert(H.buildMasterPatch(oil("18 لتر")) === null,
  "a variant already described differently is never relabelled");
const enDescOnPo = H.buildMasterPatch({ ...oil(""), poRow: { ...oilRow, description: "20 L" } });
assert(!enDescOnPo || !("description_ar" in enDescOnPo),
  "an English description on the PO is not filed as the Arabic one");
const manualOil = H.buildMasterPatch({ mode: "manual", query: oilRow.name, poRow: oilRow,
  result: { item_id: 11, name_ar: oilRow.name, unit_ar: "عدد", description_ar: "" },
  typed: { name_en: "Hydraulic Oil" } });
assert(manualOil.description_ar === "20 لتر",
  "a record completed by hand keeps the line's description too");

// The resolver can only tell the variants apart if it is TOLD what the line
// says beside its name - on every path that asks it about document lines.
assert(JSON.stringify(H.lineDetails({ name: "x", description: "20 لتر", unit: "عدد", qty: 3 }))
  === JSON.stringify({ description: "20 لتر", unit: "عدد" }), "lineDetails carries description + unit only");
assert(JSON.stringify(H.lineDetails({ name: "x" })) === JSON.stringify({ description: "", unit: "" }),
  "a missing description is blank, never 'undefined'");
{
  const make = pohSrc.slice(pohSrc.indexOf("// ---- 4. resolve"), pohSrc.indexOf("// ---- 5. review"));
  assert(/queries\.map\(q => lineDetails\(q\.row\)\)/.test(make),
    "Make DN / Make Invoice must send each PO line's description and unit");
  const cat = pohSrc.slice(pohSrc.indexOf("async function catalogueReview"),
                           pohSrc.indexOf("async function catalogueReview") + 2500);
  assert(/rows\.map\(lineDetails\)/.test(cat),
    "the save-time catalogue review must send them too");
}

// ================================================================ 6. shared review adapter
// A known bilingual record with no sell price is NOT a new item. Its compact
// review row keeps the matched English identity and asks for the rate only.
let decisions = [{
  query: "تين", poRow: { name: "تين", unit: "كيلو" }, rowReady: false,
  mode: "auto", result: { item_id: 44, name_en: "Fig", unit_en: "KG" },
  fill: { item: "Fig", unit: "KG" }, rowIndexes: [0],
}];
let reviewRow = H.reviewRowFromDecision(decisions[0], "inv", 0);
assert(reviewRow.matched, "a record with an item_id is presented as matched");
assert(reviewRow.targetName === "Fig" && reviewRow.targetUnit === "KG",
  "known English details stay visible in the shared review row");
assert(reviewRow.missing.length === 1 && reviewRow.missing[0] === "rate",
  "the A-88 case asks for rate only, not the known English identity");

// The normalized component result is copied back without changing the stable
// business helpers that build and write the document row.
H.applyReviewResults(decisions, [{
  id: 0, mode: "manual", candidate: null,
  typed: { rate: "12.5", rate_ccy: "USD" },
}]);
let fill = H.decisionFill(decisions[0]);
assert(fill.item === "Fig", "a rate-only edit preserves the matched English name");
assert(fill.rate === "12.5" && fill.rate_ccy === "USD", "typed money keeps its currency");
assert(H.readinessFor(fill, decisions[0].poRow, "inv").ready,
  "the reviewed row is now fit to print");

// Missing English remains a target-language input and can carry an existing
// candidate. The component owns the pixels, the PO adapter owns the meaning.
const missingEnglish = {
  query: AR, poRow, rowReady: false, mode: "auto", result: {}, fill: {}, rowIndexes: [0],
};
reviewRow = H.reviewRowFromDecision(missingEnglish, "dn", 3);
assert(reviewRow.id === 3 && reviewRow.missing.includes("name"),
  "the adapter keeps stable row identity and missing-name state");
assert(reviewRow.sourceName === AR && reviewRow.sourceUnit === "قطعة",
  "source-language context reaches the shared component");

// Only ENGLISH candidates are offered as a fix - an Arabic one solves nothing.
const sugg = H.englishSuggestions({ suggestions: [
  { name: "سبلت جداري", item_id: 1 }, { name: "Split AC", item_id: 2 }, { name: "", item_id: 3 },
]});
assert(sugg.length === 1 && sugg[0].item_id === 2,
  "only the English candidate is offered - the others cannot resolve the problem");

// A-89's exact-name tie is not safe to save as a fresh manual Master Data
// record. The user or AI must select one variant, or explicitly choose values
// for this document only.
assert(typeof H.needsVariantChoice === "function", "variant choice rules must be testable");
const ambiguous = {
  query: "سبلت جداري",
  poRow: { name: "سبلت جداري", description: "2 طن", unit: "عدد" },
  rowReady: false,
  mode: "manual",
  result: {
    ambiguous_variants: true,
    suggestions: [
      { item_id: 1, name: "Wall Split Unit", description: "1.5 Ton", unit: "EA" },
      { item_id: 2, name: "Wall Split Unit", description: "2 Ton", unit: "EA" },
    ],
  },
  fill: {},
  typed: { name_en: "Wall Split Unit", unit_en: "EA" },
  rowIndexes: [0],
};
assert(H.needsVariantChoice(ambiguous),
  "manual text cannot silently create a duplicate for an ambiguous item");
assert(H.buildMasterPatch(ambiguous) === null,
  "an unresolved ambiguous row cannot write a new Master Data record");
let ambiguousReview = H.reviewRowFromDecision(ambiguous, "dn", 0);
assert(ambiguousReview.ambiguous && ambiguousReview.candidates.length === 2,
  "all English candidates remain visible even when a provisional name exists");

const fourVariants = {
  ...ambiguous,
  result: {
    ambiguous_variants: true,
    suggestions: [1, 2, 3, 4].map(n => ({
      item_id: n, name: "Wall Split Unit", description: `${n} Ton`, unit: "EA",
    })),
  },
};
assert(H.reviewRowFromDecision(fourVariants, "dn", 0).candidates.length === 4,
  "the correct variant must not disappear merely because it ranks fourth");

ambiguous.mode = "asis";
assert(!H.needsVariantChoice(ambiguous),
  "document-only values are an explicit safe choice");
ambiguous.mode = "candidate";
ambiguous.candidate = ambiguous.result.suggestions[1];
assert(!H.needsVariantChoice(ambiguous), "selecting the 2 Ton record resolves the choice");

assert(typeof H.reviewValidation === "function", "structured review validation must be testable");
const rateDecision = {
  query: "تين", poRow: { name: "تين" }, rowReady: false, mode: "manual",
  result: {}, fill: { item: "Fig" }, typed: {}, rowIndexes: [0],
};
const invoiceErrors = H.reviewValidation([rateDecision], "inv", false);
assert(invoiceErrors.errors.length === 1
    && invoiceErrors.errors[0].fields.length === 1
    && invoiceErrors.errors[0].fields[0] === "rate",
  "a rate-only invoice error identifies the exact field");
assert(/AI completes language details, not prices/.test(invoiceErrors.message),
  "the rate blocker explains why the AI button cannot solve it");

// ================================================================ 7. source
const src = fs.readFileSync("static/po_handoff.js", "utf8");
for (const forbidden of ["smartMapRow", "save_item", "im_save(", "reconcileImportedCostCurrency"]) {
  assert(!new RegExp(`(?:^|[^a-zA-Z_.])${forbidden.replace("(", "\\(")}`, "m")
           .test(src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")),
    `po_handoff.js must not call ${forbidden} - see this file's header for why`);
}

// Both editor hooks exist and are reachable by the name po_handoff.js calls.
const dnSrc = fs.readFileSync("static/dn.js", "utf8");
const invSrc = fs.readFileSync("static/inv.js", "utf8");
assert(/window\.createDnFromPo\s*=/.test(dnSrc), "dn.js must expose createDnFromPo");
assert(/window\.createInvoiceFromPo\s*=/.test(invSrc), "inv.js must expose createInvoiceFromPo");

/* `sourcePo` is TRANSIENT. It must be cleared everywhere a different document
   is loaded into the editor, or a later unrelated save marks the wrong PO's
   items. Counted rather than listed so that adding a new load path without
   clearing it fails here. */
const dnClears = (dnSrc.match(/dnState\.sourcePo = null/g) || []).length;
assert(dnClears >= 4,
  `dn.js clears sourcePo ${dnClears} times; expected at least 4 (new / after a `
  + `successful link / load-for-edit / import-from-quote)`);
const invClears = (invSrc.match(/invState\.sourcePo = null/g) || []).length;
assert(invClears >= 6,
  `inv.js clears sourcePo ${invClears} times; expected at least 6 (new / after a `
  + `successful link / load-for-edit / duplicate / both DN imports / quote import)`);

// The link happens after the save, keyed on the number the SAVE returned.
assert(/linkPoAfterSave\(dnState\.sourcePo, "dn", r\.dn_no\)/.test(dnSrc),
  "the DN link must use r.dn_no - #dnNo can have been retyped");
assert(/linkPoAfterSave\(invState\.sourcePo, "inv", r\.inv_no\)/.test(invSrc),
  "the invoice link must use r.inv_no");

// ================================================================ saving a PO
/* Saving a PO offers to complete the items it just invented. The record ALREADY
   exists by then (`database.remember_item` runs inside the save), so the only
   question is its English half - and the answer must never be able to fail the
   save that already succeeded. */
assert(typeof H.recordNeedsEnglish === "function", "recordNeedsEnglish must be exposed");
assert(H.recordNeedsEnglish({ item_id: null }),
  "an item with no record at all needs English");
assert(H.recordNeedsEnglish({ item_id: 7, name_en: "" }),
  "a record known only in Arabic needs English");
assert(H.recordNeedsEnglish({ item_id: 7, name_en: "سبلت جداري" }),
  "an Arabic string sitting in name_en is not an English name");
assert(!H.recordNeedsEnglish({ item_id: 7, name_en: "Wall split unit" }),
  "a record that already has an English name must not be asked about again");

// A-86: the Arabic name ماء بطل has several genuine catalogue variants. The
// resolver returns that exact-name tie as `similar`, with identity carried by
// each suggestion instead of the top-level result. Every tied record already
// has an English name, so this is an ambiguity for document creation, not a
// new material for the save-time catalogue review.
assert(!H.recordNeedsEnglish({
  item_id: null,
  ambiguous_variants: true,
  suggestions: [
    { item_id: 1934006, name_en: "Bottled Water 500ml", name_ar: "ماء بطل" },
    { item_id: 1933895, name_en: "Bottled Water", name_ar: "ماء بطل" },
  ],
}), "an exact-name tie whose records all have English is not a new item");
assert(H.recordNeedsEnglish({
  item_id: null,
  ambiguous_variants: true,
  suggestions: [
    { item_id: 1, name_en: "Bottled Water", name_ar: "ماء بطل" },
    { item_id: 2, name_en: "", name_ar: "ماء بطل" },
  ],
}), "an exact-name tie still needs review when one matching record lacks English");

assert(typeof H.catalogueReviewKey === "function", "catalogue review keys must be testable");
assert(typeof H.unreviewedCatalogueDecisions === "function",
  "catalogue review filtering must be testable");
const waterKey = H.catalogueReviewKey({ name: "  ماء   بطل ", description: " 500 مل ", unit: " سيت " });
assert(waterKey === H.catalogueReviewKey({ name: "ماء بطل", description: "500 مل", unit: "سيت" }),
  "review identity must ignore harmless whitespace differences");
const reviewDecisions = [
  { reviewKey: waterKey, query: "ماء بطل" },
  { reviewKey: H.catalogueReviewKey({ name: "مادة جديدة", unit: "عدد" }), query: "مادة جديدة" },
];
let remaining = H.unreviewedCatalogueDecisions(reviewDecisions, [waterKey], false);
assert(remaining.length === 1 && remaining[0].query === "مادة جديدة",
  "a dismissed item must stay dismissed while a newly added PO item is still offered");
assert(H.unreviewedCatalogueDecisions(reviewDecisions, [waterKey], true).length === 2,
  "the internal force option must still show every incomplete item");

const pohSrcNow = fs.readFileSync("static/po_handoff.js", "utf8");
assert(/window\.poReviewNewItems = poReviewNewItems/.test(pohSrcNow),
  "the save-time review must be reachable from app.js");

/* THE gate must not apply here. The shared popup calls one validation adapter,
   and catalogue mode returns no problem because the PO is already on disk. */
assert(/if \(K\.catalogue\) return "";/.test(pohSrcNow),
  "catalogue mode must skip the fit-to-print gate in shared validation");
assert(/catalogue: true/.test(pohSrcNow), "the catalogue kind must declare itself");

/* Both callers now use the same reusable class. PO business code must not own a
   second set of item-review markup or data hooks. */
assert(/new window\.ItemReviewPopup\s*\(/.test(pohSrcNow),
  "PO catalogue and document review must instantiate ItemReviewPopup");
for (const removed of ["catalogueRowHtml", "catalogueTableHtml", "unreadyRowHtml",
                       "bindCatalogueRows", "readReview", "suggestEnglish"]) {
  assert(!new RegExp(`function\\s+${removed}\\b`).test(pohSrcNow),
    `${removed} must move out of po_handoff.js so the two popup designs cannot drift`);
}
assert(!/data-poh-(?:f|skip|mode|cand|row)/.test(pohSrcNow),
  "item review controls must use only the shared component's data-irp hooks");
assert(/allowMoney:\s*!K\.catalogue/.test(pohSrcNow),
  "catalogue completion must not show rate or cost fields");

/* It runs AFTER the save has been reported, and its failure is swallowed. */
const appSrc = fs.readFileSync("static/app.js", "utf8");
const saveBody = appSrc.slice(appSrc.indexOf("async function savePO"),
                              appSrc.indexOf("function showShortcutSheet"));
assert(/state\.dirtySinceSave = false;[\s\S]*poReviewNewItems/.test(saveBody),
  "the review must come after the save is recorded, never before");
assert(/try \{ await window\.poReviewNewItems\(savedNo\); \} catch \(e\) \{\}/.test(saveBody),
  "a failure completing Master Data must not make a successful save look failed");
assert(saveBody.indexOf("poReviewNewItems") < saveBody.lastIndexOf("return true"),
  "and it must still return true");

/* Units are looked up, not translated: `عدد` is "EA" here and "number" in any
   general-purpose translator. */
assert(/mi_unit_map/.test(pohSrcNow), "the unit vocabulary comes from the catalogue");
assert(/d\.suggestedUnit/.test(pohSrcNow), "and it pre-fills the English unit box");
assert(/po_catalogue_review_get/.test(pohSrcNow),
  "dismissed catalogue rows must be read from persistent document metadata");
assert(/po_catalogue_review_mark/.test(pohSrcNow),
  "confirming the popup must persist the rows that were reviewed");
/* But CANCELLING must not. Marking a cancelled row permanently reviewed
   retired it for good: nothing in the app passes `force`, so the popup was
   simply gone and the item stayed half-recorded for ever. Cancel now quietens
   the row for this session only. Audit, 2026-09-08. */
assert(/markCatalogueReviewed\(docNo, pending, ok, docType\)/.test(pohSrcNow),
  "cancelling must not burn the row: what is persisted depends on the answer");
assert(/if \(!persist\) return;[\s\S]{0,120}po_catalogue_review_mark/.test(pohSrcNow),
  "the session set still quietens a cancelled row, the database does not");
assert(!/const _putOff = new Set/.test(pohSrcNow),
  "a session-only PO set must not make the popup return after every app restart");

/* ---------------------------------------------------------------------
   A `similar` tier is a GUESS and must reach the person, never the document.

   `resolve_items` leaves `fill` empty and sets no top-level `item_id` for
   `similar` precisely so nothing can be applied without the user. But
   `MasterDataFill.fillFor` falls back to `suggested_fill`, which put the
   guess back - and a row that looks filled is a row this file marks READY and
   never shows anyone. Measured against the live catalogue on 2026-09-08:
   267 of 278 fuzzy matches were applied with no review, 174 of them carrying
   a rate or a cost. "Whiteboard Set" silently became Whiteboard Eraser at
   $4.60 on a delivery note.
   --------------------------------------------------------------------- */
const guess = {
  query: "Whiteboard Set", tier: "similar", name: "Whiteboard Eraser", score: 0.839,
  fill: {},
  suggested_fill: { item: "Whiteboard Eraser", unit: "EA", rate: "4.6",
                    rate_ccy: "USD", cost: "350", cost_ccy: "IQD" },
  suggestions: [
    { id: "A", item_id: "A", name: "Whiteboard Eraser", unit: "EA",
      fill: { item: "Whiteboard Eraser", rate: "4.6", rate_ccy: "USD" } },
    { id: "B", item_id: "B", name: "Whiteboard Marker", unit: "EA",
      fill: { item: "Whiteboard Marker", rate: "5", rate_ccy: "USD" } },
  ],
};
const guessRow = { name: "Whiteboard Set", unit: "EA", qty: "3", company: "TDC" };
const guessed = H.decisionFrom(guess, guessRow);
const guessedFill = H.decisionFill(guessed);
assert(!guessedFill.item && !guessedFill.rate && !guessedFill.cost,
  "a fuzzy guess must not become the document's item, price or cost");
["dn", "inv"].forEach(kind => {
  assert(!H.readinessFor(guessedFill, guessRow, kind).ready,
    `a fuzzy guess must never count as ready for a ${kind}`);
});
const guessReviewRow = H.reviewRowFromDecision(guessed, "dn", 0);
assert(guessReviewRow.missing.includes("name"),
  "so it goes to the review with its English name still to settle");
assert(guessReviewRow.candidates.length === 2,
  "and the guesses are offered there as candidates to choose between");
assert(!guessReviewRow.rate && !guessReviewRow.cost,
  "with no stranger's money pre-filled in the boxes");
/* An exact or contained match is a MATCH and still fills, or every clean
   lookup would turn into a question. */
const exact = { query: "Whiteboard Eraser", tier: "exact", item_id: "A",
                name: "Whiteboard Eraser",
                fill: { item: "Whiteboard Eraser", unit: "EA", rate: "4.6",
                        rate_ccy: "USD" } };
const exactFill = H.decisionFill(H.decisionFrom(exact, guessRow));
assert(exactFill.item === "Whiteboard Eraser" && exactFill.rate === "4.6",
  "an exact match still fills the row without asking");
assert(H.readinessFor(exactFill, guessRow, "inv").ready,
  "and is still ready for an invoice");

/* --------------------------------------------------------------------- 
   Three smaller edges from the same audit (2026-09-08).
   --------------------------------------------------------------------- */
/* Both buttons await the bridge before anything modal is on screen, so a
   second click in that window started a second run over the same PO - two
   reviews, two write-backs, two documents. */
assert(/let _running = ""/.test(pohSrcNow) && /_running = kind;/.test(pohSrcNow),
  "Make DN / Make Invoice must not be able to run twice at once");
assert(/finally \{\s*_running = "";/.test(pohSrcNow),
  "and the guard must clear however the run ends");

/* `Popup` sets the subtitle with textContent. Escaping it there printed the
   entities themselves. */
const pickBody = pohSrcNow.slice(pohSrcNow.indexOf("async function pickLines"),
                                pohSrcNow.indexOf("Shared item review adapter"));
assert(/subtitle: `\$\{poNo\}:/.test(pickBody),
  "text going into a textContent field must not be HTML-escaped first");

/* The row identity is STORED in doc_meta. A key that depends on the machine's
   locale is a key that stops matching when the machine changes. */
assert(!/toLocaleLowerCase/.test(pohSrcNow),
  "a persisted identity must not depend on the machine's locale");

/* ---------------------------------------------------------------------
   The PO's reference travels with the items (2026-09-09).

   `reference` is the app-only note saying WHAT the order was for
   ("اوكسجين وغاز", "20260902-CAMP"). It is the same job when the order becomes
   a delivery note or an invoice, and it was being re-typed: 11 of 87 POs carry
   one, 16 of 47 invoices do, and 0 of 63 delivery notes.
   --------------------------------------------------------------------- */
const handoffBody = pohSrcNow.slice(pohSrcNow.indexOf("const payload = {"),
                                    pohSrcNow.indexOf("const hook ="));
assert(/reference: String\(rec\.reference \|\| ""\)\.trim\(\)/.test(handoffBody),
  "the payload must carry the PO's reference, copied as the PO stored it");
assert(!/reference:\s*`/.test(handoffBody),
  "a copy, never a derivation - nothing may be appended to what he typed");

[["dn.js", dnSrc, "createDnFromPo", "initNewDn", "dnReference"],
 ["inv.js", invSrc, "createInvoiceFromPo", "initNewInv", "invReference"],
].forEach(([label, src, fn, init, field]) => {
  const body = src.slice(src.indexOf(`async function ${fn}(`),
                         src.indexOf(`window.${fn} = ${fn}`));
  assert(body.includes(`getElementById("${field}")`),
    `${label}: a document made from a PO must fill its reference box`);
  assert(body.indexOf(`await ${init}()`) < body.indexOf(`getElementById("${field}")`),
    `${label}: ${init} BLANKS that field, so the copy has to come after it`);
  assert(/if \(p\.reference\)/.test(body),
    `${label}: a PO with no reference leaves the box empty, never a guess`);
});

console.log("PO -> DN/Invoice handoff tests passed");
