/* Per-row cost currency in all three editors (2026-08-13).

   The quotation grew a per-line $/IQD chip on 2026-08-06. The DN and the
   invoice kept only a document-level switch, so a table could not hold a
   dollar-costed line beside a dinar-costed one, and the header switch
   multiplied EVERY cell blindly - flipping a document to IQD inflated a line
   already priced in dinars by the exchange rate.

   The helpers now live once in app.js and all three tables use them. This file
   RUNS them: the maths and the markup are exercised, not pattern-matched.

   What must hold:
     1. Flipping one row converts that row and stamps `cost_ccy`.
     2. The stamp happens even on a blank cell, so the NEXT number typed is
        read in the currency the chip is showing.
     3. The document switch converts each row FROM ITS OWN currency - a dinar
        line inside a dollar document is left alone when the document goes IQD.
     4. The chip is a LABEL ($ / IQD), never colour alone.
     5. All three editors use the shared builder, so they cannot drift.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const appSrc = fs.readFileSync("static/app.js", "utf8");

/* ---------- load the real helpers out of app.js ---------- */
const MARKER = "window.convertRowsToCostCurrency = convertRowsToCostCurrency;";
const from = appSrc.indexOf("function rowCostCurrency(");
const to = appSrc.indexOf(MARKER);
assert(from >= 0 && to > from, "the shared cost-cell helpers are missing from app.js");

const rows = [];                       // DOM doubles, indexed like the table
function makeCell() { return { value: "", tagName: "INPUT" }; }
function makeTr() {
  const cells = { cost: makeCell() };
  const chip = { dataset: {}, textContent: "", title: "" };
  return {
    cells, chip,
    querySelector(sel) {
      if (sel === ".cost-ccy") return chip;
      const m = /\[data-k="([^"]+)"\]/.exec(sel);
      return m ? (cells[m[1]] || null) : null;
    },
  };
}
const ctx = {
  window: { IQD_PER_USD: 1400 },
  document: {
    querySelector(sel) {
      const m = /tr\[data-i="(\d+)"\]/.exec(sel);
      return m ? (rows[+m[1]] || null) : null;
    },
  },
  esc: (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
};
vm.createContext(ctx);
vm.runInContext(appSrc.slice(from, to + MARKER.length), ctx, { filename: "app.js" });
const W = ctx.window;

/* ---------- 1. one row flips, and converts ---------- */
{
  const row = { cost: "1.43" };
  const to1 = W.toggleRowCostCurrency(row, "USD", 1400);
  ok(to1 === "IQD", "flipping a USD row gives IQD, got " + to1);
  ok(row.cost === "2002", "1.43 USD should read 2002 IQD, got " + row.cost);
  ok(row.cost_ccy === "IQD", "the row must be stamped");

  const back = W.toggleRowCostCurrency(row, "USD", 1400);
  ok(back === "USD" && row.cost === "1.43",
     "flipping back must be lossless, got " + row.cost);
}

/* ---------- 2. a blank cell is still stamped ---------- */
{
  /* Without this the next number typed into a chip reading IQD would be
     recorded as dollars - the whole failure this feature exists to stop. */
  const row = {};
  W.toggleRowCostCurrency(row, "USD", 1400);
  ok(row.cost_ccy === "IQD", "a blank cell must still stamp the currency");
  ok(row.cost === undefined, "…without inventing a value, got " + row.cost);
}

/* ---------- 3. dinars are whole, dollars keep cents ---------- */
{
  ok(W.formatRowCost(2001.6, "IQD") === "2002", "dinars round to whole");
  ok(W.formatRowCost(1.4287, "USD") === "1.43", "dollars keep cents");
  ok(W.formatRowCost(NaN, "USD") === "", "a non-number formats to empty");
}

/* ---------- 4. a row with no cost_ccy inherits the document ---------- */
{
  ok(W.rowCostCurrency({}, "IQD") === "IQD", "no stamp -> document currency");
  ok(W.rowCostCurrency({ cost_ccy: "USD" }, "IQD") === "USD", "the row's own wins");
  ok(W.rowCostCurrency({ cost_ccy: "junk" }, "IQD") === "IQD",
     "an unrecognised stamp falls back rather than guessing");
}

/* ---------- 5. the document switch converts PER ROW ---------- */
{
  /* Row 0 is in dollars, row 1 already in dinars. Flipping the document to
     IQD must convert row 0 and leave row 1 EXACTLY as it is. Multiplying
     both - what the DN and invoice used to do - inflates row 1 by 1400. */
  rows.length = 0;
  rows.push(makeTr(), makeTr());
  const state = [
    { cost: "1.43" },                        // no stamp -> inherits USD
    { cost: "2002", cost_ccy: "IQD" },       // already dinars
  ];
  W.convertRowsToCostCurrency(state, "qRowsBody", "IQD", 1400, "USD");

  ok(state[0].cost === "2002", "the dollar line converts, got " + state[0].cost);
  ok(state[1].cost === "2002",
     "the line ALREADY in dinars must be left alone, got " + state[1].cost);
  ok(state[0].cost_ccy === "IQD" && state[1].cost_ccy === "IQD",
     "every row is stamped with the new document currency");
  ok(rows[0].cells.cost.value === "2002" && rows[1].cells.cost.value === "2002",
     "the cells are repainted");
  ok(rows[0].chip.dataset.ccy === "IQD" && rows[1].chip.textContent === "IQD",
     "the chips are repainted");
}

/* ---------- 6. a blank cell survives the document switch ---------- */
{
  rows.length = 0;
  rows.push(makeTr());
  const state = [{}];
  W.convertRowsToCostCurrency(state, "qRowsBody", "IQD", 1400, "USD");
  ok(state[0].cost === undefined, "no value is invented, got " + state[0].cost);
  ok(state[0].cost_ccy === "IQD", "…but the row is stamped");
}

/* ---------- 7. the chip is a LABEL, not a colour ---------- */
{
  /* The user is colour blind. A state carried by hue alone is invisible. */
  const usd = W.rowCostCellHTML({ cost: "5" }, "USD");
  const iqd = W.rowCostCellHTML({ cost: "7000", cost_ccy: "IQD" }, "IQD");
  ok(/>\$<\/button>/.test(usd), "the USD chip must read '$': " + usd);
  ok(/>IQD<\/button>/.test(iqd), "the IQD chip must read 'IQD': " + iqd);
  ok(/data-ccy="USD"/.test(usd) && /data-ccy="IQD"/.test(iqd),
     "the chip records which currency it is showing");
  ok(/data-k="cost"/.test(usd), "the cell still carries the cost input");
  ok(/value="5"/.test(usd), "…with the row's value");

  const tr = makeTr();
  W.paintRowCostChip(tr, "IQD");
  ok(tr.chip.textContent === "IQD" && /IQD/.test(tr.chip.title),
     "repainting sets both the label and the title");
}

/* ---------- 8. the value is escaped ---------- */
{
  const html = W.rowCostCellHTML({ cost: '"><script>x' }, "USD");
  ok(!/<script>/.test(html), "the cost value must be escaped: " + html);
}

/* ---------- 9. all three editors use the shared builder ---------- */
{
  const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, " ")
                        .split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
  ["quote", "dn", "inv"].forEach((f) => {
    const src = strip(fs.readFileSync(`static/${f}.js`, "utf8"));
    ok(/window\.rowCostCellHTML\(/.test(src),
       `${f}.js must build its cost cell with the shared helper`);
    ok(/window\.convertRowsToCostCurrency\(/.test(src),
       `${f}.js document switch must convert per row`);
    ok(/window\.toggleRowCostCurrency\(/.test(src),
       `${f}.js must have a per-row toggle`);
    ok(/\.cost-ccy/.test(src) && /addEventListener\("click"/.test(src),
       `${f}.js must wire the chip`);
    /* The old blanket converter must be gone from the cost path: it multiplied
       every cell by one factor regardless of what each was priced in. */
    ok(!/const costCls = c\._synthetic/.test(src),
       `${f}.js still builds the old plain cost cell`);
  });
  const inv = strip(fs.readFileSync("static/inv.js", "utf8"));
  ok(!/invConvertColumn\("cost"/.test(inv),
     "inv.js must not convert the cost column blindly any more");
  ok(/invConvertColumn\("rate"/.test(inv),
     "…but rate is document-wide and still uses it");
}

/* ---------- 10. the backend agrees with the frontend ---------- */
{
  const py = fs.readFileSync("backend/database.py", "utf8");
  ok(/_COST_FIELDS = \([^)]*"cost_ccy"/.test(py),
     "inherit_costs must carry cost_ccy - inheriting the number without the "
     + "unit makes a dinar cost read as dollars on the next re-save");
  ok(/"cost_ccy"/.test(py.slice(py.indexOf("_SIG_IGNORE"))),
     "cost_ccy must stay out of the catalog signature");
}

console.log("row_cost_currency: OK (" + checks + " checks)");
