/*
   Completing the ARABIC half from a quotation (2026-09-10, static/po_handoff.js).

   The mirror of the purchase order's save-time review, and the reason it is
   worth having: a PO is written in Arabic and needs its English half so a
   delivery note can be made from it; a QUOTATION is written in English and
   needs its Arabic half, so the same goods are FOUND when they are later
   ordered on an Arabic PO. Measured on the live catalogue 2026-09-10: 346 of
   the 421 distinct items ever quoted (82%) have no Arabic name, and every one
   of them is invisible to a PO lookup - which is how the catalogue grows a
   second record for something it already has.

   ONE implementation, two directions. A mirrored copy of a 200-line review is
   how the signed-copy dialog and the cost-currency rules drifted apart, twice.

   Pure functions and a DOM double - no browser needed.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

const context = {
  console, window: {}, document: {}, setTimeout() {},
  Promise, Map, Set, Object, Array, Number, String, JSON, parseFloat, parseInt,
};
vm.createContext(context);
for (const f of ["static/master_data_fill.js", "static/item_review_popup.js",
                 "static/po_handoff.js"]) {
  vm.runInContext(fs.readFileSync(f, "utf8"), context, { filename: f });
}
const H = context.window.__poHandoffInternals;
const Review = context.window.ItemReviewPopup;
const pohSrc = fs.readFileSync("static/po_handoff.js", "utf8");

/* ---------------------------------------------------------------- direction */
const ar = H.direction("ar");
const en = H.direction("en");
assert(ar.target === "ar" && ar.source === "en", "the quotation fills the Arabic half");
assert(en.target === "en" && en.source === "ar", "the PO fills the English half");
assert(ar.ask === "po", "ask the resolver in ARABIC - a PO is what will look these up");
assert(en.ask === "dn", "ask the resolver in ENGLISH - a delivery note is what follows a PO");
assert(ar.missing("Office Chair") && !ar.missing("كرسي مكتب"),
  "an English name is what is MISSING when the Arabic half is wanted");
assert(en.missing("كرسي مكتب") && !en.missing("Office Chair"),
  "and the reverse for a purchase order");
assert(H.direction("nonsense").target === "en",
  "an unknown direction falls back to the long-standing one, never to nothing");

/* ------------------------------------------------- which half is missing */
const withArabic = { item_id: 7, name_en: "Office Chair", name_ar: "كرسي مكتب" };
const englishOnly = { item_id: 8, name_en: "Office Locker", name_ar: "" };
assert(!H.recordNeedsLanguage(withArabic, "ar"), "a record that has Arabic is not asked about");
assert(H.recordNeedsLanguage(englishOnly, "ar"), "one that has none is");
assert(!H.recordNeedsLanguage(englishOnly, "en"),
  "and it is NOT asked about on the PO side - it already has its English");
assert(H.recordNeedsLanguage({}, "ar"), "nothing on file at all is always asked about");

/* An exact name covering several variants is an ambiguity, not a new item -
   but only when every tied record already carries the wanted half. */
const tiedAllArabic = {
  ambiguous_variants: true,
  suggestions: [{ name_ar: "طاولة دائرية" }, { name_ar: "طاولة مستديرة" }],
};
assert(!H.recordNeedsLanguage(tiedAllArabic, "ar"),
  "a tie where every record has Arabic is not a missing name");
const tiedOneBlank = {
  ambiguous_variants: true,
  suggestions: [{ name_ar: "طاولة دائرية" }, { name_ar: "" }],
};
assert(H.recordNeedsLanguage(tiedOneBlank, "ar"), "one blank in the tie still needs asking");

/* --------------------------------------------- reading a quotation row

   Four quote types, each naming its own columns, plus whatever the user has
   added. The NAME is the type's `primary_key` - the same key `api.save_quote`
   already remembers items under - and the description comes only from a column
   actually CALLED `description` or `specs`. */
const materials = H.quoteRowFields(
  { item: "WATER DISPENSER", description: "Model: GYWK-LRS02B", unit: "EA", rate: "300" },
  { primary_key: "item" });
assert(materials.name === "WATER DISPENSER" && materials.description === "Model: GYWK-LRS02B"
    && materials.unit === "EA", "a materials row reads item / description / unit");

const equipment = H.quoteRowFields(
  { equipment: "Vacuum Truck", specs: "20 Ton\nScania 2013", col_3: "EA" },
  { primary_key: "equipment" });
assert(equipment.name === "Vacuum Truck" && equipment.description === "20 Ton\nScania 2013",
  "an equipment row's description is its `specs`");
assert(equipment.unit === "",
  "`col_3` is a column the USER added and nothing declares it a unit - guessing "
  + "would put an invented unit into Master Data");

const manpower = H.quoteRowFields({ role: "Common Laborer", unit: "Per Day" },
                                  { primary_key: "role" });
assert(manpower.name === "Common Laborer" && manpower.description === ""
    && manpower.unit === "Per Day", "a manpower row has a unit and no description");

const service = H.quoteRowFields({ description: "Site cleaning", unit: "MONTH",
                                   remarks: "night shift" },
                                 { primary_key: "description" });
assert(service.name === "Site cleaning", "a service row's NAME is its description column");
assert(service.description === "",
  "and it must not be copied into the description as well - `remarks` is not a "
  + "description, and a wrong one changes what the record means");

/* ------------------------------------------------------ the write-back */
const quoteDecision = {
  dir: "ar",
  mode: "manual",
  query: "Office Locker",                       // what the quotation says
  poRow: { name: "Office Locker", unit: "EA" },
  result: { item_id: 8, name_en: "Office Locker", name_ar: "", unit_en: "EA" },
  typed: { name_ar: "خزانة مكتب", unit_ar: "عدد" },
};
let patch = H.buildMasterPatch(quoteDecision);
assert(patch.name_ar === "خزانة مكتب", "the typed Arabic name is what gets filled in");
assert(patch.unit_ar === "عدد", "and its Arabic unit");
assert(!("name_en" in patch),
  "the record already HAS its English name - writing over it would merge two products");

/* The document's own side is filed too, but only into a blank slot. */
const blankEnglish = {
  ...quoteDecision,
  result: { item_id: 9, name_en: "", name_ar: "", unit_en: "" },
};
patch = H.buildMasterPatch(blankEnglish);
assert(patch.name_en === "Office Locker",
  "a record with no English name learns it from the quotation");
assert(patch.unit_en === "EA", "and the unit the quotation used");

/* A PO's write-back must be completely unchanged by all of this. */
const poDecision = {
  mode: "manual",
  query: "ماء بطل",
  poRow: { name: "ماء بطل", unit: "عدد" },
  result: { item_id: 3, name_en: "", name_ar: "ماء بطل", unit_ar: "عدد" },
  typed: { name_en: "Bottled Water", unit_en: "EA" },
};
patch = H.buildMasterPatch(poDecision);
assert(patch.name_en === "Bottled Water" && patch.unit_en === "EA",
  "the purchase order's direction still fills the ENGLISH half");
assert(!("name_ar" in patch), "and does not rewrite the Arabic name it already has");

/* An English unit typed on a quotation is not an Arabic unit, so it is not
   filed as one. Money still moves as a pair or not at all. */
const englishUnitOnQuote = {
  dir: "ar", mode: "manual", query: "Office Chair",
  poRow: { name: "Office Chair", unit: "EA" },
  result: { item_id: 4, name_en: "Office Chair", name_ar: "", unit_ar: "" },
  typed: { name_ar: "كرسي مكتب", rate: "45", rate_ccy: "IQD" },
};
patch = H.buildMasterPatch(englishUnitOnQuote);
assert(!("unit_ar" in patch),
  "`EA` is English - filing it as the Arabic unit would print English on a PO");
assert(patch.price_native === "45" && patch.price_native_ccy === "IQD",
  "money always travels with the currency it was typed in");

/* ------------------------------------------------------- what is shown */
const decision = H.decisionFrom(
  { query: "Office Locker", item_id: 8, name_en: "Office Locker", name_ar: "",
    tier: "exact", fill: {} },
  { name: "Office Locker", description: "3-drawer", unit: "EA" });
decision.dir = "ar";
const view = H.reviewRowFromDecision(decision, "catalogue", 0, "ar");
assert(view.sourceName === "Office Locker", "the quotation's own words lead the row");
assert(view.missing.includes("name"), "the Arabic name is what is being asked for");
assert(view.targetName === "",
  "the English name must NOT be offered as the Arabic answer");

const html = Review.rowHtml(view, 0, { sourceLanguage: "en", targetLanguage: "ar",
                                       allowMoney: false });
assert(html.includes('data-irp-field="0:name_ar"'), "the box collects an ARABIC name");
assert(/class="irp-input irp-name" type="text" dir="rtl"/.test(html),
  "and it is right-to-left, or the typing looks broken");
assert(!html.includes('data-irp-field="0:rate"'),
  "catalogue completion never asks for a selling rate, in either direction");

/* Only candidates in the wanted language are offered. */
const mixed = { suggestions: [{ name: "طاولة دائرية" }, { name: "Round Table" }] };
assert(H.targetSuggestions(mixed, "ar").length === 1
    && H.targetSuggestions(mixed, "ar")[0].name === "طاولة دائرية",
  "an English candidate cannot answer an Arabic question");
assert(H.targetSuggestions(mixed, "en").length === 1,
  "and the reverse on the purchase order side");

/* -------------------------------------------------- one item, one question */
const twiceOnOneQuote = { name: "Vacuum Truck", description: "20 Ton", unit: "EA" };
assert(H.catalogueReviewKey(twiceOnOneQuote)
    === H.catalogueReviewKey({ name: "vacuum   truck", description: "20 TON", unit: "ea" }),
  "the same item written slightly differently is one question");
assert(H.catalogueReviewKey(twiceOnOneQuote)
    !== H.catalogueReviewKey({ name: "Vacuum Truck", description: "22 Ton", unit: "EA" }),
  "but QUT-EQ-HDEC-068's 20 Ton and 22 Ton trucks are two");
assert(/if \(seen\.has\(key\)\) return;/.test(pohSrc),
  "an item repeated on ONE document is asked about once, and answered once");

/* ------------------------------------------------------- wiring and safety */
assert(/window\.quoteReviewNewItems = quoteReviewNewItems;/.test(pohSrc),
  "the quotation editor needs a way in");
assert(/docType: "qut"/.test(pohSrc) && /docType: "po"/.test(pohSrc),
  "the two reviews must not share a `doc_meta` key, or one would silence the other");

const quoteSrc = fs.readFileSync("static/quote.js", "utf8");
const saveBody = quoteSrc.slice(quoteSrc.indexOf("async function quoteSave"),
                                quoteSrc.indexOf("function quoteOpen"));
assert(/try \{ await window\.quoteReviewNewItems\(r\.quote_no\); \} catch \(e\) \{\}/.test(saveBody),
  "a failure completing Master Data must not make a successful save look failed");
assert(saveBody.indexOf("save_quote(payload)") < saveBody.indexOf("quoteReviewNewItems"),
  "the review comes after the save is recorded, never before");
assert(saveBody.indexOf("quoteReviewNewItems") < saveBody.lastIndexOf("return true"),
  "and it must still return true");

const page = fs.readFileSync("templates/index.html", "utf8");
assert(page.indexOf('src="../static/po_handoff.js"') < page.indexOf('src="../static/quote.js"'),
  "po_handoff must be loaded before the quotation editor that calls it");

console.log("quote_arabic_review.test.js: the Arabic half of a quotation passed");
