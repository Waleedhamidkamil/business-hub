/* An invoice may not bill a line for nothing (2026-08-14).

   A real invoice was found carrying two items with no price. The line still
   prints; the total is simply short - silently. Unlike a quotation, which is
   an offer and may carry a "price on application" line, there is no reading of
   an invoice where a blank or zero rate is meant.

   The second rule here is subtler and comes from QUT-EQ-HDEC-048. Its table
   was rebuilt with custom columns headed UNIT / QTY / "Monthly Rental Rate",
   typed num and money - but keyed col_3..col_6. `updateInvTotals` multiplies
   `row.qty * row.rate` BY KEY, so a column headed "QTY" is not the quantity
   and `kind` is only a formatting hint. Every line on such a table totals
   zero. Blocking the save is the only place that can be caught before it is
   printed and sent.

   The real functions are executed here against fake state, not re-implemented.
*/
const fs = require("fs");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const src = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");

function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  const end = src.indexOf(`window.${name} = ${name};`);
  assert(start >= 0 && end > start, `${name} not found in inv.js`);
  return src.slice(start, end);
}

const body = extract("invUnpricedRows") + "\n" + extract("invPricingColumnsMissing");

function run(rows, columns, schema) {
  const invState = { rows, columns, schema };
  const fn = new Function(
    "invState", "hasInvContent", "editableInvColumns",
    body + "\nreturn { invUnpricedRows, invPricingColumnsMissing };"
  )(invState,
    (row) => Object.keys(row).some((k) => String(row[k] || "").trim()),
    () => invState.columns);
  return fn;
}

const STOCK = [
  { key: "item", kind: "text" }, { key: "description", kind: "text" },
  { key: "unit", kind: "text" }, { key: "qty", kind: "num" },
  { key: "rate", kind: "money" }, { key: "total", kind: "computed" },
];
const SCHEMA = { primary_key: "item" };

/* ---------- 1. a good invoice passes ---------- */
let api = run([{ item: "Cement", qty: "5", rate: "12" },
               { item: "Sand", qty: "2", rate: "3.5" }], STOCK, SCHEMA);
ok(api.invUnpricedRows().length === 0, "a fully priced invoice must save");

/* ---------- 2. a blank rate is caught ---------- */
api = run([{ item: "Cement", qty: "5", rate: "12" },
           { item: "Free thing", qty: "2", rate: "" }], STOCK, SCHEMA);
let bad = api.invUnpricedRows();
ok(bad.length === 1, "the unpriced row must be caught, got " + bad.length);
ok(bad[0].n === 2, "it must report the ROW NUMBER as printed, got " + bad[0].n);
ok(bad[0].label === "Free thing", "and name the item: " + bad[0].label);
ok(bad[0].rate === false && bad[0].qty === true,
   "it must say WHICH value is missing");

/* ---------- 3. ZERO is not a price ---------- */
api = run([{ item: "Freebie", qty: "3", rate: "0" }], STOCK, SCHEMA);
ok(api.invUnpricedRows().length === 1,
   "a zero rate bills nothing - that is the whole point of the rule");
api = run([{ item: "Nothing", qty: "0", rate: "10" }], STOCK, SCHEMA);
ok(api.invUnpricedRows().length === 1, "a zero quantity bills nothing either");

/* ---------- 4. a blank spacer row is NOT an error ---------- */
api = run([{ item: "Cement", qty: "5", rate: "12" },
           { item: "", description: "", qty: "", rate: "" }], STOCK, SCHEMA);
ok(api.invUnpricedRows().length === 0,
   "an empty row is not an item; only FILLED rows are billed");

/* ---------- 5. a row with only a description still needs a price ---------- */
api = run([{ item: "", description: "Extra work done on site", qty: "", rate: "" }],
          STOCK, SCHEMA);
bad = api.invUnpricedRows();
ok(bad.length === 1, "a described row is an item and must be priced");
ok(bad[0].label === "Extra work done on site",
   "it falls back to the description for a label: " + bad[0].label);

/* ---------- 6. non-numeric text is not a price ---------- */
api = run([{ item: "Thing", qty: "two", rate: "call us" }], STOCK, SCHEMA);
ok(api.invUnpricedRows().length === 1, "text in a money cell is not a price");

/* ---------- 7. the renamed-column trap (QUT-EQ-HDEC-048) ---------- */
const RENAMED = [
  { key: "equipment", kind: "text" }, { key: "specs", kind: "text" },
  { key: "col_3", head: "UNIT", kind: "text" },
  { key: "col_4", head: "QTY", kind: "num" },
  { key: "col_5", head: "Monthly Rental Rate", kind: "money" },
  { key: "col_6", head: "Total", kind: "money" },
];
api = run([{ equipment: "Pick-Up Truck", col_4: "4", col_5: "1700", col_6: "6800" }],
          RENAMED, { primary_key: "equipment" });
const missing = api.invPricingColumnsMissing();
ok(missing.length === 2 && missing.includes("qty") && missing.includes("rate"),
   "a table whose Qty/Rate columns were REPLACED by custom ones cannot be "
   + "priced - every line totals zero: " + JSON.stringify(missing));

/* A stock table reports nothing missing. */
api = run([{ item: "Cement", qty: "1", rate: "1" }], STOCK, SCHEMA);
ok(api.invPricingColumnsMissing().length === 0,
   "the built-in columns must not be reported as missing");

/* Renaming only the HEADING is fine - the key is what counts. */
const RETITLED = STOCK.map((c) => (c.key === "rate"
  ? { ...c, head: "Monthly Rental Rate" } : c));
api = run([{ item: "Truck", qty: "1", rate: "1700" }], RETITLED, SCHEMA);
ok(api.invPricingColumnsMissing().length === 0,
   "a renamed HEADING is fine - the app follows the column, not its title");
ok(api.invUnpricedRows().length === 0, "…and its rows still price normally");

/* ---------- 8. strict export, app-only draft save ---------- */
ok(/invPricingColumnsMissing\(\)/.test(src) && /invUnpricedRows\(\)/.test(src),
   "both checks must be called");
const validate = src.slice(src.indexOf("async function validateInv("),
                           src.indexOf("async function invPreview("));
ok(/invUnpricedRows\(\)/.test(validate),
   "the price check must remain in validateInv");
ok(/allowIncomplete/.test(validate),
   "save must be able to pass ordinary validation while preview and print remain strict");

const save = src.slice(src.indexOf("async function invSave("),
                       src.indexOf("// ============================================================\n// Open existing invoices"));
ok(/validateInv\(\{\s*allowIncomplete:\s*true\s*\}\)/.test(save),
   "Save must preserve an incomplete invoice inside the app");
ok(/save_without_pdf/.test(save),
   "an incomplete invoice save must explicitly suppress PDF rendering");
ok(/Save draft \(no PDF\)/.test(save),
   "the user must be told exactly what the incomplete save does");
ok(/PDF was not created/.test(save),
   "the successful save toast must repeat that there is no PDF");

const preview = src.slice(src.indexOf("async function invPreview("),
                          src.indexOf("async function invSave("));
ok(!/allowIncomplete:\s*true/.test(preview),
   "Preview and Print must never render an incomplete invoice");

console.log("inv_requires_price: OK (" + checks + " checks)");
