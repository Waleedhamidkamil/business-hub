/* The live cost / sell / profit / margin cards (2026-08-04).

   `tests/doc_econ_parity.test.py` proves the ARITHMETIC matches the Costing
   screen. This covers the part a parity test cannot: what the cards actually
   say, and the two ways they could mislead.

     1. A profit computed from half-costed rows is an upper bound, not a
        profit - but it looks just as authoritative sitting in a card. The
        uncosted warning is the only thing standing between the user and a
        confident wrong number, so it is asserted hard.

     2. Profit vs loss must NEVER be signalled by red-vs-green. The user is
        colour blind and cannot separate that pair, so the card's LABEL has to
        change ("Profit" / "Loss") and a profit stays neutral.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const src = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");

/* ---------- load the real functions ---------- */
const start = src.indexOf("function _econNum(");
const end = src.indexOf("window.renderEconCards = renderEconCards;");
assert(start >= 0 && end > start, "cannot slice the econ block out of app.js");
const mod = new Function("esc", "window", src.slice(start, end) +
  "\n return { computeDocEcon, renderEconCards };")((s) => String(s), {});

const host = () => ({ innerHTML: "" });
const paint = (econ, opts) => { const h = host(); mod.renderEconCards(h, econ, opts); return h.innerHTML; };

/* ---------- 1. totals roll up the way the cards claim ---------- */
{
  const econ = mod.computeDocEcon({
    rows: [
      { qty: "3", rate: "100", cost: "60" },     // sell 300, cost 180
      { qty: "2", rate: "50", cost: "20" },      // sell 100, cost  40
    ],
    costCurrency: "USD",
  });
  assert(econ.revenue === 400, `sell should be 400, got ${econ.revenue}`);
  assert(econ.cost === 220, `cost should be 220, got ${econ.cost}`);
  assert(econ.profit === 180, `profit should be 180, got ${econ.profit}`);
  assert(Math.abs(econ.margin - 45) < 1e-9, `margin should be 45%, got ${econ.margin}`);
  assert(econ.uncosted === 0 && econ.costed === true, "both lines are costed");
}

/* ---------- 2. extra costs are included ---------- */
{
  const rows = [{ qty: "1", rate: "1000", cost: "400" }];
  const without = mod.computeDocEcon({ rows, costCurrency: "USD" });
  const withExtra = mod.computeDocEcon({
    rows, costCurrency: "USD",
    extraCosts: [{ label: "Freight", amount: "150" }, { label: "Customs", amount: "50" }],
  });
  assert(without.profit === 600, `expected 600, got ${without.profit}`);
  assert(withExtra.extraCost === 200, `expected 200 extra, got ${withExtra.extraCost}`);
  assert(withExtra.profit === 400,
    "document-level costs from the Costing screen must reduce the profit - "
    + "leaving them out overstates it on every quote that has any; got "
    + withExtra.profit);
  assert(/incl\. \$200\.00 extra/.test(paint(withExtra)),
    "the Cost card must disclose that part of it is extra costs, or the number "
    + "looks wrong against the visible COST column");
}

/* ---------- 3. IQD costs convert before being added to dollars ---------- */
{
  const usd = mod.computeDocEcon({
    rows: [{ qty: "2", rate: "100", cost: "70" }], costCurrency: "USD" });
  const iqd = mod.computeDocEcon({
    rows: [{ qty: "2", rate: "100", cost: "98000" }], costCurrency: "IQD", costPeg: 1400 });
  assert(usd.cost === 140 && iqd.cost === 140,
    `98,000 IQD at 1400 is $70/unit, so both must cost $140; got ${usd.cost} and ${iqd.cost}`);
}

/* ---------- 4. an IQD-billed invoice converts its SELL side too ---------- */
{
  const econ = mod.computeDocEcon({
    rows: [{ qty: "2", rate: "150000", cost: "40" }],
    costCurrency: "USD", sellCurrency: "IQD", sellPeg: 1500,
  });
  assert(econ.revenue === 200,
    `2 x 150,000 IQD at 1500 is $200 of revenue, got ${econ.revenue}`);
  assert(econ.cost === 80, `cost is dollars already, expected 80, got ${econ.cost}`);
  assert(econ.profit === 120, `expected 120, got ${econ.profit}`);
}

/* ---------- 5. the uncosted warning ---------- */
{
  const econ = mod.computeDocEcon({
    rows: [
      { qty: "1", rate: "100", cost: "40" },
      { qty: "1", rate: "100" },                 // no cost
      { qty: "1", rate: "100", cost: "" },       // blank cost
      { qty: "", rate: "" },                     // not billable - not counted
    ],
    costCurrency: "USD",
  });
  assert(econ.billable === 3, `3 billable lines, got ${econ.billable}`);
  assert(econ.uncosted === 2, `2 uncosted lines, got ${econ.uncosted}`);
  assert(econ.costed === false, "a partly-costed document is not costed");

  const out = paint(econ);
  assert(/econ-warn/.test(out),
    "a partly-costed document MUST warn. Without it the Profit card reads as "
    + "fact when it is really a best case, and the whole feature misleads.");
  assert(/2 of 3 lines? not costed/.test(out),
    "the warning must say HOW MANY lines, not just that something is missing; got: " + out);

  // ...and must not cry wolf when everything is costed.
  const clean = mod.computeDocEcon({
    rows: [{ qty: "1", rate: "100", cost: "40" }], costCurrency: "USD" });
  assert(!/econ-warn/.test(paint(clean)),
    "a fully costed document must not show the warning - a banner that is "
    + "always there stops being read");
}

/* ---------- 6. profit vs loss is never red-vs-green ---------- */
{
  const profit = mod.computeDocEcon({
    rows: [{ qty: "1", rate: "100", cost: "40" }], costCurrency: "USD" });
  const loss = mod.computeDocEcon({
    rows: [{ qty: "1", rate: "100", cost: "160" }], costCurrency: "USD" });

  const pOut = paint(profit), lOut = paint(loss);
  assert(/>Profit</.test(pOut), "a profit must be LABELLED Profit");
  assert(/>Loss</.test(lOut),
    "a loss must be LABELLED Loss - the word is what carries the meaning, "
    + "because the colour cannot");
  assert(/is-loss/.test(lOut), "a loss needs the is-loss class");
  assert(!/is-loss/.test(pOut), "a profit must not be styled as a loss");
  assert(/-\$60\.00/.test(lOut), "a loss must show a negative amount, got: " + lOut);

  // The CSS must not reach for green on the healthy state.
  const at = css.indexOf(".econ-card {");
  assert(at >= 0, ".econ-card rule not found");
  const block = css.slice(at, css.indexOf("TERMS EDITOR MODAL", at));
  assert(!/is-profit\s*\{[^}]*(#16a34a|#22c55e|#15803d|var\(--success\))/i.test(block),
    "the profit state must NOT be green. Red/green is exactly the pair this "
    + "user cannot distinguish; the label already says which it is.");
  assert(/\.econ-card\.is-loss/.test(block), "the loss state needs its own rule");
}

/* ---------- 7. nothing to show renders nothing ---------- */
{
  assert(paint(mod.computeDocEcon({ rows: [], costCurrency: "USD" })) === "",
    "an empty document must render no cards at all rather than a row of $0.00");
  assert(paint(null) === "", "a null econ must be safe");
}

/* ---------- 8. margin is guarded against divide-by-zero ---------- */
{
  const econ = mod.computeDocEcon({
    rows: [{ qty: "1", rate: "0", cost: "50" }], costCurrency: "USD" });
  assert(econ.margin === 0, "no revenue must give margin 0, not NaN/Infinity");
  const out = paint(econ);
  assert(/-/.test(out),
    "with no revenue the margin card must show a dash, not '0.0%' - a margin "
    + "of zero and no sale at all are different things; got: " + out);
}

/* ---------- 9. the hosts exist and the editors fill them ---------- */
{
  for (const id of ["qEconCards", "dnEconCards", "invEconCards"]) {
    assert(html.includes(`id="${id}"`), `#${id} is missing from index.html`);
  }
  const editors = [
    ["static/quote.js", "renderQuoteEconCards", "updateQuoteTotals"],
    ["static/dn.js", "renderDnEconCards", "updateDnTotals"],
    ["static/inv.js", "renderInvEconCards", "updateInvTotals"],
  ];
  for (const [file, fn, totals] of editors) {
    const s = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
    assert(new RegExp(`function ${fn}\\(`).test(s), `${file}: ${fn} is missing`);
    const at = s.indexOf(`function ${totals}(`);
    assert(at >= 0, `${file}: ${totals} not found`);
    const body = s.slice(at, s.indexOf("\n}", at));
    assert(body.includes(`${fn}()`),
      `${file}: ${totals} must call ${fn}() or the cards never update as you type`);
    // The unfiltered rows and the document's own extra costs, not a subset.
    assert(new RegExp(`extraCosts:\\s*\\w+State\\.extraCosts`).test(s),
      `${file}: must pass the document's extraCosts into computeDocEcon`);
  }
}

/* ---------- 10. a DN without prices has no sell side ---------- */
{
  const s = fs.readFileSync("static/dn.js", "utf8").replace(/\r\n/g, "\n");
  const at = s.indexOf("function renderDnEconCards(");
  const body = s.slice(at, s.indexOf("\n}", at));
  assert(/includePrices/.test(body),
    "a delivery note issued WITHOUT prices has no revenue, so profit and margin "
    + "are meaningless - renderDnEconCards must bail rather than show a 100% loss");
}

console.log("Econ cards render tests passed");
