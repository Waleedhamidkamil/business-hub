/*
   The catalogue review panel (2026-09-10, static/master_doctor.js).

   The backend decides WHAT is wrong. This decides how it reads, and what a
   press does. Two of these tests exist because drawing REAL records on screen
   caught what a unit test had happily agreed with:

     · the panel proposed **Manpower for a 4WD Pickup Truck** - the unit says
       hired, only the name says whether the thing hired is a person;
     · it offered to flatten the 25-Seater Bus route, a four-line description
       holding the only written record of the departure times.

   Both were fixed in the detector; the screen is what found them.

   Pure functions and a DOM double - no browser needed.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

const context = {
  console, window: {}, document: { getElementById: () => null,
                                   querySelector: () => null },
  setTimeout, Promise, Map, Set, Object, Array, Number, String, JSON,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("static/master_doctor.js", "utf8"), context,
                { filename: "static/master_doctor.js" });
const MD = context.window.MasterDoctor;
const src = fs.readFileSync("static/master_doctor.js", "utf8");

assert(MD && typeof MD.open === "function", "the panel needs a way in");

/* ------------------------------------------------------------ the evidence */
const duplicate = {
  kind: "duplicate", key: "k1", title: "Round Table",
  evidence: {
    price: "far",
    rows: [
      { item_id: 1, item_no: "M-01804", name: "Round Table", description: "H=80cm",
        unit: "EA", price: 300, price_ccy: "USD", uses: 3 },
      { item_id: 2, item_no: "M-01833", name: "Round Table", description: "H=80cm",
        unit: "EA", price: 400, price_ccy: "USD", uses: 1 },
    ],
  },
  proposal: { action: "merge", keep: 1, drop: [2] },
};

const card = MD._cardHtml(duplicate, 0);
assert(card.includes("Round Table"), "the card names the item");
assert(card.includes("M-01804") && card.includes("M-01833"),
  "a decision about real rows shows the real rows");
assert(card.includes("keeping this one"),
  "which record survives must be said, not inferred from row order");
assert(/\$300/.test(card) && /\$400/.test(card), "and the prices that disagree");

/* The price disagreement is the REASON a duplicate matters, and the user is
   colour blind: it has to be words. */
const warn = MD._priceNote(duplicate);
assert(/disagree about the price/i.test(warn),
  "a price disagreement is stated in WORDS, never a colour alone");
assert(MD._priceNote({ ...duplicate, evidence: { price: "same", rows: [] } }) === "",
  "identical prices are not worth a warning");
assert(/differ slightly/i.test(
  MD._priceNote({ ...duplicate, evidence: { price: "close", rows: [] } })),
  "a small difference is said quietly, not shouted");

/* Checked against the DECLARATIONS, not the prose. The stylesheet's own
   comment contains the word "green", and a rule that reads its own
   documentation is a rule that passes for the wrong reason. */
const css = fs.readFileSync("static/master_doctor.css", "utf8");
const cssRules = css.replace(/\/\*[\s\S]*?\*\//g, "");
assert(!/\b(green|lime|seagreen)\b/i.test(cssRules),
  "there is no green counterpart anywhere on this screen");
assert(/border-inline-start: 3px solid/.test(css),
  "the warning's colour is reinforcement beside the words, not the message");

/* ------------------------------------------------------- one use, not uses */
const single = MD._rowsHtml({
  evidence: { rows: [{ item_id: 9, item_no: "M-1", name: "Pen", unit: "EA",
                       price: null, uses: 1 }] },
  proposal: {},
});
assert(single.includes("1 use<") || single.includes("1 use "),
  "one use is not '1 uses'");
assert(single.includes("no price"),
  "a record with no price says so rather than showing a bare dash");

/* ------------------------------------------------ variants get their boxes */
const variants = {
  kind: "variant_needs_description", key: "k2", title: "FLOUR",
  evidence: {
    price: "far",
    rows: [
      { item_id: 11, item_no: "M-00297", name: "FLOUR", unit: "BAG", price: 100, uses: 14 },
      { item_id: 12, item_no: "M-00298", name: "FLOUR", unit: "BOX", price: 13, uses: 4 },
    ],
  },
  proposal: { action: "describe", item_ids: [11, 12] },
};
const vcard = MD._cardHtml(variants, 3);
assert(vcard.includes('data-mdoc-desc="3:11"') && vcard.includes('data-mdoc-desc="3:12"'),
  "one description box per variant, right here - sending the user off to the "
  + "record and back for six rows is how a list like this goes unfinished");
assert(!vcard.includes("<table"),
  "the boxes already carry number, unit and price; the table above them was "
  + "the same three rows twice");
assert(vcard.includes("Save descriptions"), "and the action says what it does");
assert(!/Merge/.test(vcard),
  "Waleed's ruling: variants are never offered for merging");

/* -------------------------------------------------- what a press must send */
assert(/md_dismiss\(finding\.kind, finding\.key\)/.test(src),
  "'Not an issue' is a ruling on the FINDING, keyed by its words");
assert(/md_apply\(finding\.kind, finding\.key, finding\.proposal\)/.test(src),
  "and Apply sends back the proposal the scan produced");
assert(/if \(!value\) continue;/.test(src),
  "a description left blank is left ALONE - an empty patch would clear it");
assert(/state\.busy/.test(src),
  "one finding at a time: a merge deletes rows, and a batch that half-succeeds "
  + "has no honest report to give");

/* ------------------------------------------------------------- the way in */
const master = fs.readFileSync("static/item_master.js", "utf8");
assert(/MasterDoctor\.count\(\)/.test(master) && /imReviewBtn/.test(master),
  "the way in sits in the Health panel, where you already look for the state "
  + "of the catalogue");
assert(!/id="imReviewTop"/.test(fs.readFileSync("templates/index.html", "utf8")),
  "and NOT as a second top-level button competing with Health to mean the "
  + "same thing");

const page = fs.readFileSync("templates/index.html", "utf8");
assert(page.indexOf('src="../static/master_doctor.js"')
     < page.indexOf('src="../static/item_master.js"'),
  "item_master.js calls MasterDoctor.count(), so it must load after it");
assert(page.includes('href="../static/master_doctor.css"'), "and the panel is styled");

console.log("master_doctor_screen.test.js: the catalogue review panel passed");
