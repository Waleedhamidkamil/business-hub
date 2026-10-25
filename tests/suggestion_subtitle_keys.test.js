/* The dropdown must not print the item's other-language name (2026-08-16).

   Reported: "I see arabic suggestions in quotation".

   They were not Arabic suggestions. Since the catalogs learned `name_ar` /
   `name_en` - one product, two names, because a PO is written in Arabic and
   the delivery note that fulfils it in English - the suggestion payload
   carries the other name, and all three editors built the grey subtitle by
   printing EVERY string value they received. So `Cucumber` came up reading
   "خيار", which looks exactly like the dropdown having switched language.

   `img_query` (the wording that finds the item's picture) leaked the same way,
   and the same loops write a picked suggestion into the row - so an internal
   key could also land in the document itself.
*/
const fs = require("fs");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const app = fs.readFileSync("static/app.js", "utf8");

const m = app.match(/const ITEM_META_KEYS = new Set\(\[([^\]]*)\]\)/);
assert(m, "app.js must define ITEM_META_KEYS");
const keys = m[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean);

for (const k of ["name_ar", "name_en", "img_query", "item_no", "cost_usd",
                 "cost_native", "cost_native_ccy", "price_native", "price_native_ccy"]) {
  ok(keys.includes(k), `${k} describes the ITEM and must never be printed as a column value`);
}
ok(/window\.ITEM_META_KEYS = ITEM_META_KEYS/.test(app),
   "the set must reach quote.js / dn.js / inv.js, which are separate scripts");

/* Each editor has TWO loops over the payload: one builds the subtitle, one
   applies the pick to the row. Both must skip these keys - filtering only the
   display would still let `name_ar` be written into a document row. */
for (const f of ["static/quote.js", "static/dn.js", "static/inv.js"]) {
  const src = fs.readFileSync(f, "utf8");
  const uses = (src.match(/ITEM_META_KEYS\.has\(k\)/g) || []).length;
  ok(uses >= 2,
     `${f} must skip the meta keys in BOTH the subtitle and the apply loop, found ${uses}`);
  ok(!/if \(k === "cost_usd"\) continue;/.test(src),
     `${f} still has the old single-key check, which would let name_ar through`);
}

/* The backend deliberately still SENDS them - the pairing is stored there and
   the editors will want it once the language split is built. The fix belongs
   in what is displayed, not in what is known. */
const db = fs.readFileSync("backend/database.py", "utf8");
ok(/"image_refs", "img_query", "name_ar", "name_en",/.test(db),
   "the suggestion payload should still carry the item's other-language name; "
   + "it is the DISPLAY that must ignore it");

console.log("suggestion_subtitle_keys: OK (" + checks + " checks)");
