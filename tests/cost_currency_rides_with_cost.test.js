/* The front-end half of "a cost never travels without its currency" (2026-08-26).

   Since 2026-08-06 it is the ROW that carries the unit (`cost_ccy`), and both
   profit engines read it - so a number and its stamp disagreeing is a silent
   factor-of-peg error that shows up as a wrong profit, never as an error.
   Several places still assumed one currency per DOCUMENT:

   1. `smartMapRow` (app.js) carried `cost` through every import and dropped
      `cost_ccy` - a dinar-costed line landed in a dollar document looking like
      dollars.
   2. `reconcileImportedCostCurrency` (app.js) converted by the SOURCE
      DOCUMENT's flag, so a dollar line inside a dinar document was divided by
      the peg; it returned early when the two documents shared a flag, leaving
      genuinely-mixed rows unconverted; it never stamped what it wrote; and it
      used ONE peg for both legs, which is wrong the moment the two documents
      settle at different rates (a 1400 DN billed by a 1500 cash invoice).
   3. (retired 2026-09-11) The Costing screen printed the raw number, read the whole
      grid at the document's flag, and saved without a unit.
   4. Autofill filled a remembered cost CONVERTED into the document's currency
      in the invoice, native in the quotation and the DN - and none of the three
      repainted the $/IQD chip after a pick, so a dinar cost landed under a "$".

   The app.js functions are EXECUTED here (the real source, not a copy).
   the editors are checked by shape, because their grids need a
   DOM; the assertions name the identifiers that carry the contract.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const rawApp = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

function extract(src, name, endMarker) {
  const at = src.indexOf(name.startsWith("async") ? name : "function " + name + "(");
  assert(at >= 0, name + " not found");
  const end = src.indexOf(endMarker, at);
  assert(end > at, "end marker for " + name + " not found");
  return src.slice(at, end);
}

/* A sandbox holding the REAL rowCostCurrency / formatRowCost helpers, so the
   conversion under test is the shipped one. */
const helpers = extract(rawApp, "rowCostCurrency", "window.rowCostCurrency =")
              + extract(rawApp, "formatRowCost", "window.formatRowCost =");
const reconcileSrc = extract(rawApp, "async function reconcileImportedCostCurrency(",
                             "window.reconcileImportedCostCurrency =");
const PEG = 1400;

/* `pegs` maps a doc number to its OWN rate, so the two legs can differ. */
function makeReconcile(srcCcy, dstCcy, pegs) {
  const api = () => ({
    get_cost_currency: async (kind) => (kind === "dn" ? srcCcy : dstCcy),
    doc_iqd_per_usd: async (kind, no) => (pegs ? pegs[no] : PEG),
  });
  const body = helpers + reconcileSrc + "\nreturn reconcileImportedCostCurrency;";
  return new Function("api", "window", body)(api, { IQD_PER_USD: PEG });
}

async function main() {
  /* ---- 1a. Per ROW, not per document ------------------------------- */
  {
    const fn = makeReconcile("IQD", "USD");        // dinar DN -> dollar invoice
    const rows = [
      { item: "dinar line", cost: "14000", cost_ccy: "IQD" },
      { item: "dollar line inside the same DN", cost: "10", cost_ccy: "USD" },
      { item: "unstamped - inherits the DN's", cost: "14000" },
    ];
    await fn(rows, "dn", "DN-1", "inv", "INV-1");
    ok(Math.abs(parseFloat(rows[0].cost) - 10) < 0.01,
       "a dinar line must convert at the peg, got " + rows[0].cost);
    ok(Math.abs(parseFloat(rows[1].cost) - 10) < 0.01,
       "a DOLLAR line inside a dinar document must be left alone, not divided "
       + "by 1400 - got " + rows[1].cost);
    ok(Math.abs(parseFloat(rows[2].cost) - 10) < 0.01,
       "an unstamped line inherits the source DOCUMENT's unit, got " + rows[2].cost);
    ok(rows.every(r => r.cost_ccy === "USD"),
       "every converted row must be stamped with the destination's unit: "
       + JSON.stringify(rows.map(r => r.cost_ccy)));
  }

  /* ---- 1b. Same document flag is NOT the same as same unit --------- */
  {
    const fn = makeReconcile("USD", "USD");
    const rows = [{ item: "dinar line in a dollar DN", cost: "14000", cost_ccy: "IQD" }];
    await fn(rows, "dn", "DN-2", "inv", "INV-2");
    ok(Math.abs(parseFloat(rows[0].cost) - 10) < 0.01,
       "two documents sharing a flag can still hold rows in the other unit - "
       + "the old srcCur===dstCur shortcut skipped them; got " + rows[0].cost);
    ok(rows[0].cost_ccy === "USD", "...and it must be re-stamped");
  }

  /* ---- 1c. A blank cost is still stamped --------------------------- */
  {
    const fn = makeReconcile("IQD", "USD");
    const rows = [{ item: "no cost", cost: "" }];
    await fn(rows, "dn", "DN-3", "inv", "INV-3");
    ok(rows[0].cost_ccy === "USD",
       "an empty cell must carry the destination's unit so the NEXT number "
       + "typed into it is read correctly");
  }

  /* ---- 1d. TWO pegs: out at the source's, in at the destination's --- */
  {
    // DN-TDC-039 sits at the profile peg (1400); INV-MT-TDC-017 is a CASH deal
    // and settles at 1500. The invoice is what reads the number back, so 1500
    // is the rate that has to put it there.
    const fn = makeReconcile("USD", "IQD", { "DN-4": 1400, "INV-4": 1500 });
    const rows = [{ item: "DN-TDC-039", cost: "2611.34", cost_ccy: "USD" }];
    await fn(rows, "dn", "DN-4", "inv", "INV-4");
    const back = parseFloat(rows[0].cost) / 1500;      // how the invoice reads it
    ok(Math.abs(back - 2611.34) < 0.01,
       "the real cost must survive the round trip: converting out at the "
       + "SOURCE's peg and reading back at the DESTINATION's invented $174 of "
       + "profit on one line. Stored " + rows[0].cost + " reads back as $"
       + back.toFixed(2));
    ok(rows[0].cost_ccy === "IQD", "...stamped with the destination's unit");
  }

  /* ---- 2. smartMapRow carries the unit with the number ------------- */
  {
    const src = extract(rawApp, "smartMapRow", "window.smartMapRow =");
    ok(/out\.cost_ccy\s*=/.test(src),
       "smartMapRow must carry `cost_ccy` beside `cost` - carrying the number "
       + "alone is what made an imported dinar cost read as dollars");
    ok(/srcCcy === "USD" \|\| srcCcy === "IQD"/.test(src),
       "...and only a recognised unit may be copied (no guessing)");
  }

  /* ---- 3. (was the Costing grid) ---------------------------------
     The separate Costing & Profit screen was retired on 2026-09-11: costs are
     typed in each document's own private cost column, which is where all 146
     costed documents were costed. Its chip/per-line-unit rules are not gone,
     they are the three editors' rules, checked in section 1 above. ---------- */

  /* ---- 4. The DN-as-one-line invoice paths stamp what they inherit -- */
  {
    const inv = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");
    const stamps = inv.match(/row\.cost_ccy = \((?:d|sum)\.currency \|\| "USD"\)/g) || [];
    ok(stamps.length === 2,
       "both DN-as-one-line paths (the item-field picker and the Make-invoice "
       + "one-line button) must stamp the DN's own cost currency onto the "
       + "inherited total before it is reconciled - found " + stamps.length);
  }

  /* ---- 5a. Autofill fills in the SAVED unit ------------------------ */
  {
    const fn = new Function("window",
      extract(rawApp, "costFromMemory", "window.costFromMemory =")
      + extract(rawApp, "costCellFromMemory", "window.costCellFromMemory =")
      + "\nreturn costCellFromMemory;")({ IQD_PER_USD: PEG });

    const native = fn({ cost_native: 2002, cost_native_ccy: "IQD" }, "USD", PEG);
    ok(native && native.value === "2002" && native.ccy === "IQD",
       "an item costed in dinars must fill a DOLLAR document as dinars, with "
       + "the chip saying so - not converted at a peg: " + JSON.stringify(native));

    const legacy = fn({ cost_usd: 1.43 }, "IQD", PEG);
    ok(legacy && legacy.ccy === "IQD" && Math.abs(parseFloat(legacy.value) - 2002) < 1,
       "an older entry with no native pair is dollars, converted into the "
       + "document's unit and stamped with it: " + JSON.stringify(legacy));

    ok(fn({ cost: 2002 }, "USD", PEG) === null,
       "a raw cost with no recorded currency is still never replayed");
  }

  /* ---- 5b. ...in all three editors, chip repainted with it --------- */
  {
    const editors = [["static/quote.js", "quoteState"],
                     ["static/dn.js", "dnState"],
                     ["static/inv.js", "invState"]];
    for (const pair of editors) {
      const file = pair[0], state = pair[1];
      const src = fs.readFileSync(file, "utf8");
      ok(/window\.costCellFromMemory\(/.test(src),
         file + " must fill its cost cell through the ONE shared helper, so the "
         + "three editors cannot drift on which currency an item recalls");
      const chip = new RegExp(
        "paintRowCostChip\\(tr, window\\.rowCostCurrency\\(row, " + state);
      ok(chip.test(src),
         file + " must repaint the $/IQD chip after a pick - the chip carries "
         + "no data-k, so the repaint loop cannot touch it and a dinar cost "
         + "landed under a dollar chip");
    }
  }

  /* ---- 5c. The invoice's DN pick repaints after its ASYNC reconcile - */
  {
    const inv = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");
    const at = inv.indexOf('reconcileImportedCostCurrency([row], "dn", d.no, "inv", _dst)');
    assert(at >= 0, "the DN-pick reconcile call is missing from inv.js");
    const cb = inv.slice(at, inv.indexOf(".catch(() => {});", at));
    ok(/paintRowCostChip\(/.test(cb),
       "the reconcile lands AFTER the row is painted, so its callback must "
       + "repaint the chip - otherwise the cell shows the converted number "
       + "under the unit it arrived in (3,655,876 beside a dollar chip)");
    ok(/updateInvTotals\(\)/.test(cb),
       "...and recompute the PROFIT column, which was painted from the "
       + "pre-conversion figure");
  }

  /* ---- 5d. The Master Data fill popup shows the unit --------------- */
  {
    const mdf = fs.readFileSync("static/master_data_fill.js", "utf8");
    ok(/function previewForField\(/.test(mdf)
       && /previewForField\(fill, field\.key\)/.test(mdf),
       "the fill popup must show a money value WITH its unit - the cost comes "
       + "in the currency Master Data saved it in, so a bare 2002 reads as "
       + "dollars when it means dinars");
  }

  console.log("cost_currency_rides_with_cost (js): passed - " + checks + " checks");
}

main().catch((e) => { console.error(e.message); process.exit(1); });
