/* Picking a delivery note in the invoice item field must fill its COST - and
   must NOT invent one (2026-08-25).

   Typing a DN number in an invoice's item field and picking it from the
   dropdown drops the whole delivery note in as one line: item = the DN number,
   qty 1, rate = the DN's total. The cost is supposed to come from the DN too.

   It looked like the cost simply was not filled. It was: with a literal `0`,
   for every DN whose own costs were never entered (11 of 32 on this data).
   A zero is not "no cost" anywhere downstream - `database._coerce_cost(0)` is
   `0.0`, not `""` - so the line counted as FULLY COSTED at 100% profit, in the
   new PROFIT column, on the Costing screen and in Insights alike. Four such
   lines are why INV-MT-TDC-017 reports a 97.6% margin on $4,233 of revenue.

   What must hold:

     1. A DN with costs fills the cost cell from the DN.
     2. A DN with NO costs leaves the cell EMPTY (unknown) and says so out
        loud. Empty is the only honest answer, and the only one the rest of the
        app reads as "not costed".
     3. The currency reconcile runs when there IS a cost, and does not run when
        there is nothing to convert.
     4. A stale `cost_ccy` on the row cannot survive the pick. Since
        2026-08-26 it is not merely cleared: the inherited total arrives in the
        DN's OWN cost currency, so the row is stamped with THAT before the
        reconcile converts it into the invoice's and re-stamps. A cost never
        sits in a row without saying what unit it is in. With no cost to
        inherit there is no unit either, and the stamp is removed so the line
        falls back to the invoice's.
     5. The dropdown shows the cost side BEFORE the pick, naming the DN's own
        currency - 750,000 is not dollars.
     6. The other DN-to-invoice path (Make invoice → "One line") follows the
        same rule; two paths that disagree about a zero would be worse than one.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const raw = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");

/* ---------- slice the DN branch out of pickInvSuggestion and RUN it -------- */
const fnAt = raw.indexOf("function pickInvSuggestion(");
assert(fnAt >= 0, "pickInvSuggestion not found");
const branchAt = raw.indexOf("    if (s._dn) {", fnAt);
assert(branchAt > fnAt, "the whole-DN branch of pickInvSuggestion not found");
const endAt = raw.indexOf("\n        return;\n    }\n", branchAt);
assert(endAt > branchAt, "cannot find the end of the DN branch");
const branch = raw.slice(branchAt, endAt + "\n        return;\n    }\n".length);

/* The real `invDnHasCost`, not a stub - whether a DN counts as costed is the
   thing under test. */
const hasCostAt = raw.indexOf("function invDnHasCost(");
assert(hasCostAt >= 0, "invDnHasCost is missing");
const invDnHasCost = new Function(
  raw.slice(hasCostAt, raw.indexOf("\n}", hasCostAt) + 2) + "\nreturn invDnHasCost;")();

const DEPS = ["s", "row", "ri", "pk", "invState", "invSellFromUsd", "invRenderColumns",
              "document", "window", "toast", "hideInvAc", "updateInvTotals",
              "addInvRow", "autoResizeInvTextarea", "invDnHasCost"];

function runPick(dn, row) {
  const toasts = [];
  const reconciled = [];
  const deps = {
    s: { _dn: dn },
    row,
    ri: 0,
    pk: "item",
    invState: { rows: [row], dirty: false, editInvNo: "INV-MT-TDC-050", invNo: "INV-MT-TDC-050" },
    invSellFromUsd: (v) => v,
    invRenderColumns: () => [{ key: "item", kind: "text" }, { key: "cost", kind: "money" }],
    document: { querySelector: () => null, getElementById: () => null },
    window: {
      reconcileImportedCostCurrency: (rows, sk, sn, dk, dnNo) => {
        reconciled.push([sk, sn, dk, dnNo]);
        return { then: (f) => { f(); return { catch: () => {} }; } };
      },
    },
    toast: (m) => toasts.push(String(m)),
    hideInvAc: () => {},
    updateInvTotals: () => {},
    addInvRow: () => {},
    autoResizeInvTextarea: () => {},
    invDnHasCost,
  };
  // The branch ends in `return;` - wrap it so that is legal.
  new Function(...DEPS, "(function(){" + branch + "})();")(...DEPS.map(k => deps[k]));
  return { toasts, reconciled };
}

/* ---------- 1. a costed DN fills the cost ---------- */
{
  const row = {};
  const r = runPick({ no: "DN-TDC-044", sell_total: 824, cost_total: 792.86,
                      cost_total_usd: 792.86, has_costs: true, currency: "USD" }, row);
  ok(row.item === "DN-TDC-044" && row.dn_ref === "DN-TDC-044",
    "the line must carry the DN number and the dn_ref marker");
  ok(Number(row.qty) === 1 && row.unit === "EA", "one line, qty 1");
  ok(Number(row.rate) === 824, `rate comes from the DN total, got ${row.rate}`);
  ok(Number(row.cost) === 792.86,
    `the cost must come from the DN, got ${JSON.stringify(row.cost)}`);
  ok(r.reconciled.length === 1 && r.reconciled[0][1] === "DN-TDC-044",
    "a real cost must be reconciled from the DN's currency into the invoice's");
  ok(r.toasts.length === 0, "nothing to warn about");
}

/* ---------- 2. an UNCOSTED DN leaves the cell empty and says so ---------- */
{
  const row = {};
  const r = runPick({ no: "DN-TDC-039", sell_total: 2886.55, cost_total: 0,
                      cost_total_usd: 0, has_costs: false, currency: "USD" }, row);
  ok(row.cost === "",
    "a DN with no costs must leave the cost EMPTY. A literal 0 reads as a "
    + "KNOWN cost of nothing everywhere downstream, so the line reports 100% "
    + "profit instead of 'not costed'; got " + JSON.stringify(row.cost));
  ok(Number(row.rate) === 2886.55, "the price is still filled");
  ok(r.toasts.length === 1 && /no costs recorded/i.test(r.toasts[0]),
    "and the user must be told, at the moment of picking; got "
    + JSON.stringify(r.toasts));
  ok(/DN-TDC-039/.test(r.toasts[0]), "the message must name the delivery note");
  ok(r.reconciled.length === 0,
    "there is nothing to convert, so no reconcile round-trip");
}

/* ---------- 3. a stale row currency cannot survive the pick ---------- */
{
  const row = { cost_ccy: "IQD" };          // left over from an earlier pick
  runPick({ no: "DN-TDC-044", sell_total: 824, cost_total: 792.86,
            has_costs: true, currency: "USD" }, row);
  ok(row.cost_ccy === "USD",
    "the inherited total is in the DN's own cost currency, so the row must be "
    + "stamped with it - the stale IQD would have the reconcile read a dollar "
    + "figure as dinars; got " + JSON.stringify(row.cost_ccy));

  const bare = { cost_ccy: "IQD" };
  runPick({ no: "DN-TDC-039", sell_total: 2886.55, cost_total: 0,
            has_costs: false, currency: "IQD" }, bare);
  ok(!("cost_ccy" in bare),
    "…and with no cost to inherit there is no unit to record either: the "
    + "stamp must go, so the line inherits the INVOICE's currency for whatever "
    + "the user types next");
}

/* ---------- 4. the dropdown shows the cost side before the pick ---------- */
{
  const helperAt = raw.indexOf("function invDnCostText(");
  assert(helperAt >= 0, "invDnCostText is missing");
  const helper = raw.slice(helperAt, raw.indexOf("\n}", helperAt) + 2);
  const fmt = new Function(helper + "\nreturn invDnCostText;")();
  ok(fmt({ cost_total: 792.86, currency: "USD" }) === "$792.86",
    "a dollar cost reads as dollars");
  ok(/^750,000 IQD/.test(fmt({ cost_total: 750000, currency: "IQD" })),
    "a dinar cost must NAME the currency - 750,000 shown as '$750,000.00' is "
    + "the same trap that put a 750,000 cost on an $824 line; got "
    + fmt({ cost_total: 750000, currency: "IQD" }));
  ok(fmt({ cost_total: 750000, cost_total_usd: 500, currency: "IQD" }) === "750,000 IQD ($500.00)",
    "and the dollar figure rides with it, so the two readings cannot be "
    + "confused; got " + fmt({ cost_total: 750000, cost_total_usd: 500, currency: "IQD" }));

  /* `has_costs` is the honest signal; the cost_total fallback keeps an
     F5-without-restart alive rather than calling every DN uncosted. */
  ok(invDnHasCost({ has_costs: true, cost_total: 0 }) === true,
    "the backend's answer wins - a costed DN whose costs happen to total 0");
  ok(invDnHasCost({ has_costs: false, cost_total: 999 }) === false,
    "and when it says no, no");
  ok(invDnHasCost({ cost_total: 792.86 }) === true
     && invDnHasCost({ cost_total: 0 }) === false,
    "an old bridge with no has_costs must still work (new JS, old Python)");

  const showAt = raw.indexOf("function showInvAc(");
  const showBody = raw.slice(showAt, raw.indexOf("\nfunction ", showAt + 10));
  ok(/invDnCostText\(/.test(showBody),
    "the suggestion subtitle must show the DN's cost, so an uncosted DN is "
    + "noticed BEFORE it is picked");
  ok(/NO COST RECORDED/.test(showBody),
    "and must say so in words when there is none");
}

/* ---------- 5. the Make-invoice 'one line' path agrees ---------- */
{
  const at = raw.indexOf("async function createInvoiceFromDnAsLine(");
  assert(at >= 0, "createInvoiceFromDnAsLine not found");
  const body = raw.slice(at, raw.indexOf("\nwindow.createInvoiceFromDnAsLine", at));
  ok(/_dnCost > 0 \? sum\.cost_total : ""/.test(body),
    "the other DN-to-invoice path must apply the same rule - two paths that "
    + "disagree about a zero cost are worse than one");
  ok(/no costs recorded/.test(body), "and warn the same way");
  ok(/_dnCost > 0 && window\.reconcileImportedCostCurrency/.test(body),
    "and skip the reconcile when there is nothing to convert");
}

/* ---------- 6. the backend still supplies what the picker needs ---------- */
{
  const db = fs.readFileSync("backend/database.py", "utf8").replace(/\r\n/g, "\n");
  const at = db.indexOf("def list_invoiceable_dns(");
  const body = db.slice(at, db.indexOf("\ndef ", at + 10));
  for (const key of ["sell_total", "cost_total", "currency"]) {
    ok(new RegExp(`"${key}"`).test(body),
      `list_invoiceable_dns must return ${key} - the picker fills the line from it`);
  }
}

console.log(`dn_line_cost_fill.test.js: ${checks} checks passed`);
