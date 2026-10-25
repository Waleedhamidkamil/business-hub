/* A quotation autocomplete pick must use the ONE Master Data record.

   This executes the real picker with the same payload shape as the Wire rope
   record reported on 2026-08-20.  It is intentionally a behaviour test: the
   values must reach both row state and the visible Rate/Cost cells. */
const fs = require("fs");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const rawQuote = fs.readFileSync("static/quote.js", "utf8").replace(/\r\n/g, "\n");
const rawApp = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

function extract(source, name) {
  const at = source.indexOf("function " + name + "(");
  assert(at >= 0, name + " is missing");
  const rest = source.slice(at);
  return rest.slice(0, rest.indexOf("\n}") + 2);
}

function appHelper(name, exportLine) {
  const at = rawApp.indexOf("function " + name + "(");
  const end = rawApp.indexOf(exportLine, at);
  assert(at >= 0 && end > at, name + " is missing from app.js");
  return new Function("window", rawApp.slice(at, end) + "\nreturn " + name + ";")(
    { IQD_PER_USD: 1400 }
  );
}

const costFromMemory = appHelper("costFromMemory", "window.costFromMemory = costFromMemory;");
// The picker fills through the ONE shared helper since 2026-08-26 - it answers
// with the value AND the unit, so the chip and the number cannot disagree.
const costCellFromMemory = appHelper("costCellFromMemory", "window.costCellFromMemory = costCellFromMemory;");
// The $/IQD chip carries no `data-k`, so the picker paints it explicitly
// (2026-08-26) through the real reader.
const rowCostCurrency = appHelper("rowCostCurrency", "window.rowCostCurrency = rowCostCurrency;");
const priceFromMemory = appHelper("priceFromMemory", "window.priceFromMemory = priceFromMemory;");
const metaMatch = rawApp.match(/const ITEM_META_KEYS = new Set\(\[([^\]]*)\]\)/);
assert(metaMatch, "ITEM_META_KEYS is missing from app.js");
const metaKeys = new Set(metaMatch[1].split(",")
  .map((part) => part.trim().replace(/^["']|["']$/g, ""))
  .filter(Boolean));

const cells = {};
for (const key of ["item", "description", "unit", "qty", "rate", "total", "cost"]) {
  cells[key] = { value: "", tagName: "INPUT" };
}
const tr = {
  querySelector(selector) {
    const match = /\[data-k="([^"]+)"\]/.exec(selector);
    return match ? cells[match[1]] : null;
  },
};
const row = {};
const quoteState = {
  rows: [row, {}],
  schema: { primary_key: "item" },
  costCurrency: "USD",
  dirty: false,
};
const suggestion = {
  name: "Wire rope\nWith TPI",
  data: {
    description: "3.5Ton\n16mm × 10m",
    unit: "EA",
    price_native: 150,
    price_native_ccy: "USD",
    cost_native: 180000,
    cost_native_ccy: "IQD",
    image_refs: { image: "wire-rope.jpg" },
    item_no: "M-01536",
  },
};
let restoredImages = null;
const painted = { ccy: null };   // what the $/IQD chip was repainted to

const pick = new Function(
  "qacTarget", "qacItems", "quoteState", "document", "window",
  "quoteRenderColumns", "autoResizeTextarea", "restoreRememberedImages",
  "hideQuoteAc", "updateQuoteTotals", "addQuoteRow", "quotePegSync",
  extract(rawQuote, "pickQuoteSuggestion") + "\nreturn pickQuoteSuggestion;"
)(
  { field: {}, rowIdx: 0 }, [suggestion], quoteState,
  { querySelector: () => tr },
  { costFromMemory, costCellFromMemory, priceFromMemory, rowCostCurrency,
    paintRowCostChip: (_tr, ccy) => { painted.ccy = ccy; },
    ITEM_META_KEYS: metaKeys, IQD_PER_USD: 1400 },
  () => [
    { key: "item" }, { key: "description" }, { key: "unit" },
    { key: "qty" }, { key: "rate", kind: "money" },
    { key: "total", kind: "computed" }, { key: "cost", kind: "money" },
  ],
  () => {},
  (index, refs) => { restoredImages = { index, refs }; },
  () => {}, () => {}, () => {}, () => 1400
);

pick(0);

assert(row.rate === "150", "Master Data price did not reach row.rate: " + row.rate);
assert(cells.rate.value === "150", "Master Data price was not painted in Rate");
// The cost keeps the CURRENCY IT WAS SAVED IN (2026-08-20): 180000 IQD fills
// as 180000 IQD, not converted to dollars. The cell's own $/IQD toggle shows it.
assert(Number(row.cost) === 180000, "cost must keep its saved IQD value, got " + row.cost);
assert(String(row.cost_ccy).toUpperCase() === "IQD",
  "cost currency must be the saved one (IQD), got " + row.cost_ccy);
assert(Number(cells.cost.value) === 180000, "the saved IQD cost was not painted, got " + cells.cost.value);
// The chip is not a `data-k` cell, so the repaint loop cannot reach it - the
// picker paints it explicitly. Without this the dinar number sat under a "$".
assert(painted.ccy === "IQD",
  "the $/IQD chip must be repainted to the saved unit, got " + JSON.stringify(painted.ccy));
assert(row.price_native === undefined && row.cost_native === undefined,
  "Master Data storage keys leaked into the document row");
assert(restoredImages && restoredImages.refs.image === "wire-rope.jpg",
  "the existing picture fill must remain intact");

console.log("quote master-money autofill: 6 checks passed");
