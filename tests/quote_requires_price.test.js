/* A quotation may not offer a line for nothing (2026-08-14).

   Same rule as the invoice (tests/inv_requires_price.test.js), but the four
   quote types are NOT the same shape and that is the whole difficulty:

     materials   item / qty / rate / total, grand total ON  - a priced table
     equipment   equipment / specs / rate,  grand total OFF - a RATE CARD
     manpower    role / unit / rate,        grand total OFF - a rate card
     service     description / unit / rate, grand total OFF - a rate card

   Demanding a quantity on a rate card would block every equipment quotation
   ever written, so the guard asks the schema instead of assuming.

   And the replaced-column check runs ONLY where a total is computed. Seven
   saved equipment quotations (QUT-EQ-HDEC-048 and friends) were rebuilt with
   custom col_N columns; nothing computes on them, they print what they show,
   and refusing to save them would make the user's own documents unfixable.
*/
const fs = require("fs");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const src = fs.readFileSync("static/quote.js", "utf8").replace(/\r\n/g, "\n");

function extract(name, endMarker) {
  const start = src.indexOf(`function ${name}(`);
  const end = src.indexOf(endMarker, start);
  assert(start >= 0 && end > start, `${name} not found`);
  return src.slice(start, end);
}

const body =
  extract("_quoteStockKeys", "function quotePricingKeys(")
  + extract("quotePricingKeys", "window.quoteUnpricedRows")
  + extract("quotePricingColumnsMissing", "window.quotePricingColumnsMissing");

const MATERIALS = {
  primary_key: "item", grand_total: true,
  stock_columns: [{ key: "item" }, { key: "description" }, { key: "unit" },
                  { key: "qty" }, { key: "rate" }, { key: "total" }],
};
const EQUIPMENT = {
  primary_key: "equipment", grand_total: false,
  stock_columns: [{ key: "equipment" }, { key: "specs" }, { key: "rate" },
                  { key: "image" }],
};

function run(rows, columns, schema) {
  const quoteState = { rows, columns, schema };
  return new Function(
    "quoteState", "hasQuoteContent", "editableColumns",
    body + "\nreturn { quoteUnpricedRows, quotePricingColumnsMissing, quotePricingKeys };"
  )(quoteState,
    (row) => Object.keys(row).some((k) => String(row[k] || "").trim()),
    () => quoteState.columns);
}

/* ---------- materials: qty AND rate are both required ---------- */
let api = run([{ item: "Cement", qty: "5", rate: "12" }],
              MATERIALS.stock_columns, MATERIALS);
ok(api.quotePricingKeys().join() === "qty,rate",
   "a materials quote prices from both: " + api.quotePricingKeys());
ok(api.quoteUnpricedRows().length === 0, "a fully priced row passes");

api = run([{ item: "Cement", qty: "5", rate: "" }], MATERIALS.stock_columns, MATERIALS);
let bad = api.quoteUnpricedRows();
ok(bad.length === 1 && bad[0].missing.join() === "rate",
   "a blank rate is caught: " + JSON.stringify(bad));
ok(bad[0].n === 1 && bad[0].label === "Cement", "and the row is named");

api = run([{ item: "Freebie", qty: "0", rate: "10" }], MATERIALS.stock_columns, MATERIALS);
ok(api.quoteUnpricedRows()[0].missing.join() === "qty", "zero quantity is not a quantity");
api = run([{ item: "Freebie", qty: "3", rate: "0" }], MATERIALS.stock_columns, MATERIALS);
ok(api.quoteUnpricedRows()[0].missing.join() === "rate", "zero rate is not a rate");

/* ---------- a blank spacer row is not an error ---------- */
api = run([{ item: "Cement", qty: "1", rate: "1" }, { item: "", qty: "", rate: "" }],
          MATERIALS.stock_columns, MATERIALS);
ok(api.quoteUnpricedRows().length === 0, "an empty row is not an item");

/* ---------- equipment: a RATE CARD has no quantity ---------- */
api = run([{ equipment: "Pick-Up Truck", specs: "Toyota", rate: "1700" }],
          EQUIPMENT.stock_columns, EQUIPMENT);
ok(api.quotePricingKeys().join() === "rate",
   "an equipment quote has no qty column - demanding one would block every "
   + "equipment quotation ever written: " + api.quotePricingKeys());
ok(api.quoteUnpricedRows().length === 0, "a rate alone is enough on a rate card");

api = run([{ equipment: "Pick-Up Truck", specs: "Toyota", rate: "" }],
          EQUIPMENT.stock_columns, EQUIPMENT);
ok(api.quoteUnpricedRows().length === 1, "…but the rate itself is still required");

/* ---------- the replaced-column check ---------- */
const REBUILT_EQ = [{ key: "equipment" }, { key: "specs" }, { key: "col_3" },
                    { key: "col_4" }, { key: "col_5" }, { key: "col_6" }];
api = run([{ equipment: "Pick-Up Truck", col_4: "4", col_5: "1700", col_6: "6800" }],
          REBUILT_EQ, EQUIPMENT);
ok(api.quotePricingColumnsMissing().length === 0,
   "QUT-EQ-HDEC-048 and its six siblings must stay SAVEABLE - nothing is "
   + "computed on a rate card, so there is no wrong total to prevent");
ok(api.quoteUnpricedRows().length === 0,
   "…and their rows must not be reported as unpriced either");

const REBUILT_MT = [{ key: "item" }, { key: "description" }, { key: "col_3" },
                    { key: "col_4" }, { key: "col_5" }];
api = run([{ item: "Cement", col_4: "4", col_5: "12" }], REBUILT_MT, MATERIALS);
const missing = api.quotePricingColumnsMissing();
ok(missing.length === 2 && missing.includes("qty") && missing.includes("rate"),
   "a MATERIALS quote does total qty x rate, so replacing those columns "
   + "silently produces a wrong grand total and must be refused: "
   + JSON.stringify(missing));

/* Renaming only the heading is always fine - the key is what counts. */
const RETITLED = MATERIALS.stock_columns.map((c) => (c.key === "rate"
  ? { ...c, head: "Monthly Rental Rate" } : c));
api = run([{ item: "Truck", qty: "1", rate: "1700" }], RETITLED, MATERIALS);
ok(api.quotePricingColumnsMissing().length === 0,
   "a renamed HEADING must never be flagged");
ok(api.quoteUnpricedRows().length === 0, "…and its rows still price normally");

/* ---------- wired into app-only draft saving ---------- */
const validate = src.slice(src.indexOf("async function validateQuote("),
                           src.indexOf("async function quotePreview("));
ok(/quoteUnpricedRows\(\)/.test(validate) && /quotePricingColumnsMissing\(\)/.test(validate),
   "both pricing checks must remain in validation");
ok(/allowIncomplete/.test(validate),
   "save must be able to pass ordinary validation while preview and print remain strict");

/* Save preserves an unpriced quotation as an app-only draft. Preview and Print
   remain strict, so an incomplete table can never become a sendable PDF. */
const save = src.slice(src.indexOf("async function quoteSave("),
                       src.indexOf("// ============================================================\n// Open existing quotations"));
ok(/validateQuote\(\{\s*allowIncomplete:\s*true\s*\}\)/.test(save),
   "Save must allow the content into the app even when pricing is incomplete");
ok(/save_without_pdf/.test(save),
   "an incomplete quotation save must explicitly tell the backend not to render a PDF");
ok(/Save draft \(no PDF\)/.test(save),
   "the confirmation must say clearly that this is an app-only draft");
ok(/PDF was not created/.test(save),
   "the successful save toast must repeat that no PDF exists");

const preview = src.slice(src.indexOf("async function quotePreview("),
                          src.indexOf("async function quoteSave("));
ok(!/allowIncomplete:\s*true/.test(preview),
   "Preview and Print must remain strict so they can never render an incomplete draft");

console.log("quote_requires_price: OK (" + checks + " checks)");
