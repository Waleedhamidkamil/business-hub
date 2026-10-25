/* Per-item profit and loss, and the privacy switch (2026-08-25).

   The document-level Profit card answers "is this deal worth doing". It cannot
   answer "which of these twenty lines is carrying it, and which is bleeding" -
   and the two are routinely both true at once: a quotation $2,000 up overall
   with three lines under cost. Those lines were invisible until someone divided
   each cost by each sell by hand.

   So every editor that has a cost AND a price - quotation, priced delivery
   note, invoice - carries a private PROFIT column beside COST, and lists the
   losing lines with the dollar figure underneath. (The separate Costing screen
   carried them too until it was retired on 2026-09-11.)

   Because those two columns are exactly what the user does not want on screen
   when somebody is standing beside them, ONE control hides the whole strip and
   the profit cards with it.

   What must hold, and why each one is here:

     1. Only a BILLABLE line with a REAL cost can be judged. An uncosted line
        looks free; calling it "no profit" would be the confident wrong number
        the uncosted warning exists to prevent - it reads "-", not "$0.00".
     2. The figures are in DOLLARS, after every currency conversion. A dinar
        cost against a dollar sell is wrong by ~1400x.
     3. Under a cent is rounding, not a loss.
     4. The loss list NAMES the item and shows sell vs cost; worst first.
     5. Never red-vs-green, never colour alone. The user is colour blind
        ([[waleed-colour-blind]]): a loss says the word LOSS and carries a sign,
        and a healthy line stays NEUTRAL.
     6. PROFIT is COMPUTED and read-only. It must never become a row key, or it
        lands in rows_json and from there in a PDF the client sees.
     7. Hiding must hide EVERYTHING private at once. Hiding the columns while
        the same numbers sat in the cards below would be worse than no control.
     8. All three editors are on the one implementation, so they cannot drift.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const src = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");

/* ---------- load the real functions out of app.js ---------- */
const start = src.indexOf("function _econNum(");
const end = src.indexOf("window.focusEditorRow = focusEditorRow;");
assert(start >= 0 && end > start, "cannot slice the econ block out of app.js");

let documentStub = null;                       // swapped per test
const documentDouble = {
  get getElementById() { return documentStub.getElementById; },
  createElement() {
    const el = { className: "", textContent: "", title: "", _parent: null };
    el.remove = () => {
      if (!el._parent) return;
      const kids = el._parent.children;
      kids.splice(kids.indexOf(el), 1);
    };
    return el;
  },
};
const mod = new Function("esc", "window", "document", src.slice(start, end) +
  "\n return { computeDocEcon, renderEconCards, paintRowEconCells, rowProfitCellHTML };")(
  (s) => String(s), {}, documentDouble);

const paint = (econ, opts) => {
  const h = { innerHTML: "" };
  mod.renderEconCards(h, econ, opts);
  return h.innerHTML;
};

/* ============================================================
   1. Every line's own economics, and its STATE
   ============================================================ */
{
  const econ = mod.computeDocEcon({
    rows: [
      { item: "Traffic Cone", qty: "3", rate: "40", cost: "68" },   // -$84
      { item: "Safety Vest",  qty: "100", rate: "12", cost: "8" },  // +$400
      { item: "Helmet",       qty: "2", rate: "15" },               // no cost
      { item: "",             qty: "",  rate: "" },                 // empty row
    ],
    costCurrency: "USD", primaryKey: "item",
  });

  ok(econ.lines.length === 4, "every row gets a line, index-aligned with rows");
  ok(econ.lines.map(l => l.state).join(",") === "loss,profit,unknown,blank",
    "the four states must be told apart; got " + econ.lines.map(l => l.state).join(","));
  ok(econ.lines[0].lineProfit === -84, `expected -84, got ${econ.lines[0].lineProfit}`);
  ok(econ.lines[1].lineProfit === 400, `expected 400, got ${econ.lines[1].lineProfit}`);
  ok(econ.lines[2].state === "unknown",
    "a billable line with NO cost is unknown, never a $0.00 profit - that is "
    + "the same confident wrong number the uncosted warning guards against");
  ok(econ.lines[1].index === 1, "each line carries its row index");
  ok(Math.abs(econ.lines[1].margin - (400 / 1200) * 100) < 1e-9,
    "each line carries its own margin for the tooltip");
  // Changed 2026-10-21 (Waleed): the uncosted Helmet line is no longer free -
  // its $30 is costed at a 40% markup (30 / 1.4), so it is not pure profit.
  ok(Math.abs(econ.profit - (346 - 30 / 1.4)) < 1e-9,
    "the document reads as IN PROFIT while one of its lines sells under cost - "
    + "the whole reason this feature exists (the uncosted line is costed at the "
    + "40% markup estimate); got " + econ.profit);
  ok(Math.abs(econ.estCost - 30 / 1.4) < 1e-9 && econ.lines[2].state === "unknown",
    "the estimate is reported as such and the line still reads as unknown; got " + econ.estCost);
}

/* ============================================================
   2. Which lines are losing, and by how much
   ============================================================ */
{
  const econ = mod.computeDocEcon({
    rows: [
      { item: "Traffic Cone", qty: "3", rate: "40", cost: "68" },   // -$84
      { item: "Safety Vest",  qty: "100", rate: "12", cost: "8" },  // +$400
      { item: "Helmet",       qty: "2", rate: "15", cost: "20" },   // -$10
    ],
    costCurrency: "USD", primaryKey: "item",
  });
  ok(econ.lossCount === 2, `2 lines sell below cost, got ${econ.lossCount}`);
  ok(Math.abs(econ.lossTotal - 94) < 1e-9,
    `the dollar answer to "by how much" is 84 + 10 = 94, got ${econ.lossTotal}`);
  ok(econ.losers[0].name === "Traffic Cone",
    "worst first - the biggest dollar hole is the one worth re-pricing; got "
    + econ.losers.map(l => l.name).join(", "));
  ok(econ.losers[0].loss === 84 && econ.losers[0].loss > 0,
    "`loss` is the POSITIVE size of the hole so callers never render '--$84'");
  ok(econ.losers[0].index === 0 && econ.losers[1].index === 2,
    "each loser must carry its ROW index, or the list cannot point at the line");
  ok(econ.losers[0].sell === 40 && econ.losers[0].unitCost === 68,
    "the two numbers that caused the loss must come back with it");
}

/* ============================================================
   3. An uncosted line is not a "free" line
   ============================================================ */
{
  const econ = mod.computeDocEcon({
    rows: [
      { item: "Costed",   qty: "1", rate: "10", cost: "30" },   // a real loss
      { item: "Uncosted", qty: "1", rate: "10" },               // unknown, not free
      { item: "Blank",    qty: "1", rate: "10", cost: "" },     // unknown, not free
      { item: "Empty",    qty: "", rate: "" },                  // not billable
    ],
    costCurrency: "USD", primaryKey: "item",
  });
  ok(econ.lossCount === 1,
    "only the line with a REAL cost can be judged; got " + econ.lossCount);
  ok(econ.losers[0].name === "Costed", "the wrong line was flagged");
  ok(econ.uncosted === 2, "the uncosted lines still count as uncosted");
}

/* ============================================================
   4. Dollars, after conversion - both sides
   ============================================================ */
{
  // 98,000 IQD at 1400 = $70/unit against a $60 sell -> $10 lost per unit.
  const iqdCost = mod.computeDocEcon({
    rows: [{ item: "Cable", qty: "2", rate: "60", cost: "98000" }],
    costCurrency: "IQD", costPeg: 1400, primaryKey: "item",
  });
  ok(iqdCost.lossCount === 1 && Math.abs(iqdCost.lossTotal - 20) < 1e-9,
    "an IQD cost must be converted before it is compared with a dollar sell; "
    + "got " + JSON.stringify(iqdCost.losers));
  ok(iqdCost.lossTotal < 1000,
    "a raw dinar cost against a dollar sell reports every line as a "
    + "catastrophe - that is the ~1400x bug this assertion exists to catch");

  // An invoice billed in dinars: 150,000 IQD at 1500 = $100 sell vs $130 cost.
  const iqdSell = mod.computeDocEcon({
    rows: [{ item: "Panel", qty: "1", rate: "150000", cost: "130" }],
    costCurrency: "USD", sellCurrency: "IQD", sellPeg: 1500, primaryKey: "item",
  });
  ok(iqdSell.lossCount === 1 && Math.abs(iqdSell.lossTotal - 30) < 1e-9,
    "an IQD-billed invoice must convert its SELL side too, or a healthy line "
    + "reads as a 150,000 profit; got " + JSON.stringify(iqdSell.losers));
}

/* ============================================================
   5. Break-even is not a loss
   ============================================================ */
{
  const even = mod.computeDocEcon({
    rows: [{ item: "At cost", qty: "5", rate: "100", cost: "100" }],
    costCurrency: "USD", primaryKey: "item",
  });
  ok(even.lossCount === 0, "a line priced exactly at cost is not a loss");
  ok(even.lines[0].state === "profit",
    "break-even belongs on the neutral side, not the marked one");

  const rounded = mod.computeDocEcon({
    rows: [{ item: "Rounded", qty: "1", rate: "70", cost: "98000.001" }],
    costCurrency: "IQD", costPeg: 1400, primaryKey: "item",
  });
  ok(rounded.lossCount === 0,
    "a hundredth of a cent is rounding, not a loss - flagging it teaches the "
    + "user to ignore the marker");

  const cent = mod.computeDocEcon({
    rows: [{ item: "Real", qty: "1", rate: "70", cost: "70.02" }],
    costCurrency: "USD", primaryKey: "item",
  });
  ok(cent.lossCount === 1, "a genuine 2-cent loss must still be reported");
}

/* ============================================================
   6. What the loss panel says
   ============================================================ */
{
  const econ = mod.computeDocEcon({
    rows: [
      { item: "Traffic Cone", qty: "3", rate: "40", cost: "68" },
      { item: "Safety Vest",  qty: "10", rate: "12", cost: "8" },
    ],
    costCurrency: "USD", primaryKey: "item",
  });
  const out = paint(econ);
  ok(/Traffic Cone/.test(out),
    "the losing line must be NAMED - a count alone leaves the user hunting");
  ok(!/Safety Vest/.test(out), "a profitable line must not be listed");
  ok(/-\$84\.00/.test(out), "the loss must be shown in dollars, signed; got: " + out);
  ok(/sells? below cost/.test(out),
    "the heading must say what the block means in words, not in colour");
  ok(/3 &times; \$40\.00 sell/.test(out) && /\$68\.00 cost each/.test(out),
    "sell and cost per unit must both be shown - the amount alone does not "
    + "say which number to move; got: " + out);

  const clean = mod.computeDocEcon({
    rows: [{ item: "Safety Vest", qty: "10", rate: "12", cost: "8" }],
    costCurrency: "USD", primaryKey: "item",
  });
  ok(!/econ-loss/.test(paint(clean)),
    "a document with nothing under cost must show no loss block");

  const unnamed = mod.computeDocEcon({
    rows: [{ qty: "1", rate: "10", cost: "30" }], costCurrency: "USD" });
  ok(/Line 1/.test(paint(unnamed)),
    "a row with no item name must still identify itself by line number");
}

/* ============================================================
   7. A long list is capped, and says so
   ============================================================ */
{
  const rows = [];
  for (let i = 0; i < 8; i++) rows.push({ item: `Item ${i}`, qty: "1", rate: "10", cost: String(20 + i) });
  const econ = mod.computeDocEcon({ rows, costCurrency: "USD", primaryKey: "item" });
  const out = paint(econ);
  ok(econ.lossCount === 8, `all 8 lines lose money, got ${econ.lossCount}`);
  ok((out.match(/econ-loss-row/g) || []).length === 5,
    "at most 5 lines are listed, or the block swallows the editor");
  ok(/\+3 more lines? below cost/.test(out),
    "the ones not listed must be counted, or the block quietly under-reports");
  ok(/-\$108\.00/.test(out),
    "the TOTAL is of every losing line, not just the listed ones; got: " + out);
}

/* ============================================================
   8. Clicking a listed line points at the row
   ============================================================ */
{
  const econ = mod.computeDocEcon({
    rows: [{ item: "A", qty: "1", rate: "1", cost: "5" }],
    costCurrency: "USD", primaryKey: "item",
  });
  const picked = [];
  const buttons = [];
  const host = {
    innerHTML: "",
    querySelectorAll(sel) {
      if (sel !== ".econ-loss-row") return [];
      const idx = [...host.innerHTML.matchAll(/data-loss-i="(\d+)"/g)].map(m => m[1]);
      buttons.length = 0;
      idx.forEach(i => buttons.push({ dataset: { lossI: i }, onclick: null }));
      return buttons;
    },
  };
  mod.renderEconCards(host, econ, { onPickRow: (i) => picked.push(i) });
  ok(buttons.length === 1, "the entry must be a real button, not inert text");
  buttons[0].onclick();
  ok(picked.length === 1 && picked[0] === 0,
    "clicking a listed line must hand back its ROW index; got " + JSON.stringify(picked));
  ok(/econ-loss/.test(paint(econ)), "the list must render without a picker too");
}

/* ============================================================
   9. The PROFIT cell on each row
   ============================================================ */
{
  ok(/data-profit/.test(mod.rowProfitCellHTML()) && /qc-private/.test(mod.rowProfitCellHTML()),
    "the profit cell must be addressable AND marked private; got: " + mod.rowProfitCellHTML());
  ok(!/data-k=/.test(mod.rowProfitCellHTML()),
    "the profit cell must have NO data-k - a row key would carry it into "
    + "rows_json and from there into a PDF the client sees");

  function makeTr(i) {
    const cell = { className: "", textContent: "", innerHTML: "", title: "",
                   classes: new Set() };
    cell.classList = {
      toggle: (n, on) => { if (on) cell.classes.add(n); else cell.classes.delete(n); },
      has: (n) => cell.classes.has(n),
    };
    const rowClasses = new Set();
    return {
      dataset: { i: String(i) },
      classList: {
        toggle(n, on) { if (on) rowClasses.add(n); else rowClasses.delete(n); },
        has: (n) => rowClasses.has(n),
      },
      querySelector(sel) { return sel === "[data-profit]" ? cell : null; },
      _cell: cell,
    };
  }
  const trs = [makeTr(0), makeTr(1), makeTr(2), makeTr(3)];
  documentStub = { getElementById: () => ({ querySelectorAll: () => trs }) };

  const econ = mod.computeDocEcon({
    rows: [
      { item: "Traffic Cone", qty: "3", rate: "40", cost: "68" },   // loss
      { item: "Safety Vest",  qty: "10", rate: "12", cost: "8" },   // profit
      { item: "Helmet",       qty: "2", rate: "15" },               // unknown
      { item: "", qty: "", rate: "" },                              // blank
    ],
    costCurrency: "USD", primaryKey: "item",
  });
  mod.paintRowEconCells("qRowsBody", econ);

  ok(/LOSS/.test(trs[0]._cell.innerHTML) && /-\$84\.00/.test(trs[0]._cell.innerHTML),
    "a losing line must carry the WORD and the amount - colour cannot be the "
    + "message for this user; got: " + trs[0]._cell.innerHTML);
  ok(trs[0]._cell.classList.has("is-loss") && trs[0].classList.has("is-loss-row"),
    "the losing cell and its row must both be marked");
  ok(trs[1]._cell.textContent === "+$40.00",
    "a profitable line must show its profit, signed; got: " + trs[1]._cell.textContent);
  ok(!trs[1]._cell.classList.has("is-loss"),
    "a profit must NOT be styled as a loss");
  ok(trs[2]._cell.textContent === "-" && trs[2]._cell.classList.has("is-unknown"),
    "an uncosted line reads '-', never $0.00 - unknown and zero are different "
    + "things; got: " + trs[2]._cell.textContent);
  ok(/unknown, not zero/.test(trs[2]._cell.title),
    "and says so on hover; got: " + trs[2]._cell.title);
  ok(trs[3]._cell.textContent === "", "an empty row shows nothing at all");
  ok(/\$68\.00 cost each/.test(trs[0]._cell.title) && /margin/.test(trs[1]._cell.title),
    "the tooltip must explain the numbers behind the figure");

  // Fixing the price must clear the loss, not leave a ghost.
  const fixed = mod.computeDocEcon({
    rows: [
      { item: "Traffic Cone", qty: "3", rate: "80", cost: "68" },
      { item: "Safety Vest",  qty: "10", rate: "12", cost: "8" },
      { item: "Helmet",       qty: "2", rate: "15" },
      { item: "", qty: "", rate: "" },
    ],
    costCurrency: "USD", primaryKey: "item",
  });
  mod.paintRowEconCells("qRowsBody", fixed);
  ok(trs[0]._cell.textContent === "+$36.00" && !trs[0]._cell.classList.has("is-loss"),
    "raising the price must repaint the cell as a profit; got: " + trs[0]._cell.textContent);
  ok(!trs[0].classList.has("is-loss-row"), "and clear the row marking with it");

  // A table with no profit column (a DN without prices) must not throw.
  const noCells = [{ dataset: { i: "0" }, classList: { toggle() {} },
                     querySelector: () => null }];
  documentStub = { getElementById: () => ({ querySelectorAll: () => noCells }) };
  mod.paintRowEconCells("dnRowsBody", econ);
  mod.paintRowEconCells("dnRowsBody", null);
  ok(true, "painting a table with no PROFIT column is a no-op, not a crash");
  documentStub = null;
}

/* ============================================================
   10. All three editors are on the one implementation
   ============================================================ */
{
  const editors = [
    ["static/quote.js", "renderQuoteEconCards", "qRowsBody", "quoteRenderColumns"],
    ["static/dn.js",    "renderDnEconCards",    "dnRowsBody", "dnRenderColumns"],
    ["static/inv.js",   "renderInvEconCards",   "invRowsBody", "invRenderColumns"],
  ];
  for (const [file, fn, tbody, colsFn] of editors) {
    const s = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
    const at = s.indexOf(`function ${fn}(`);
    assert(at >= 0, `${file}: ${fn} not found`);
    const body = s.slice(at, s.indexOf("\n}\n", at));
    ok(/primaryKey:/.test(body),
      `${file}: computeDocEcon needs the schema's primaryKey or every losing `
      + "line is listed as 'Line N' instead of by name");
    ok(body.includes(`paintRowEconCells("${tbody}"`),
      `${file}: must paint the PROFIT column of ${tbody}`);
    ok(body.includes(`focusEditorRow("${tbody}"`),
      `${file}: clicking a listed line must scroll to it in ${tbody}`);

    const cols = s.slice(s.indexOf(`function ${colsFn}(`), s.indexOf("\n}\n", s.indexOf(`function ${colsFn}(`)));
    ok(/key: "profit"/.test(cols), `${file}: ${colsFn} must add the PROFIT column`);
    ok(/_readonly: true/.test(cols),
      `${file}: the PROFIT column must be flagged _readonly - grid_paste's `
      + "writableColumns drops it by that flag, so a wide paste cannot spill "
      + "a value into a computed column");
    ok(!/kind: "computed"/.test(cols),
      `${file}: kind "computed" is claimed by the document's own Total column; `
      + "the row builder would paint PROFIT as a .qc-total cell and never "
      + "reach the profit branch");
    ok(s.includes("window.rowProfitCellHTML()"),
      `${file}: the row builder must use the shared profit cell markup`);
    ok(/qc-profit-col/.test(s) && /qc-private/.test(s),
      `${file}: the PROFIT header must sit in the private strip (qc-profit-col `
      + "+ qc-private), or the hide switch leaves it on screen");
  }

  // A DN without prices has no sell side, so no PROFIT column at all.
  const dn = fs.readFileSync("static/dn.js", "utf8").replace(/\r\n/g, "\n");
  const dnCols = dn.slice(dn.indexOf("function dnRenderColumns("));
  ok(/includePrices[\s\S]{0,200}key: "profit"/.test(dnCols),
    "a DN issued WITHOUT prices must not get a PROFIT column - every line "
    + "would read as a 100% loss");

  // The invoice's tfoot spans the body's cells; two private columns need two pads.
  const inv = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");
  const foot = inv.slice(inv.indexOf("const costFootCell"), inv.indexOf("const taxRow"));
  ok((foot.match(/<td class="qc-/g) || []).length === 2,
    "the invoice tfoot must pad for BOTH private columns, or every total below "
    + "the table slides one column left; got: " + foot);
}

/* ============================================================
   11. One switch hides every private figure
   ============================================================ */
{
  // The real privacy block, executed against stubs.
  const pStart = src.indexOf("const PRIVACY_KEY");
  const pEnd = src.indexOf("window.initPrivacyMode = initPrivacyMode;");
  assert(pStart >= 0 && pEnd > pStart, "the privacy block is missing from app.js");

  const bodyClasses = new Set();
  const store = {};
  const btn = {
    classes: new Set(), attrs: {}, title: "",
    label: { textContent: "" }, icon: { innerHTML: "" },
  };
  btn.classList = {
    toggle: (n, on) => { if (on) btn.classes.add(n); else btn.classes.delete(n); },
    contains: (n) => btn.classes.has(n),
  };
  btn.setAttribute = (k, v) => { btn.attrs[k] = v; };
  btn.querySelector = (sel) => (sel === ".pv-label" ? btn.label
                              : sel === ".pv-icon" ? btn.icon : null);
  btn.addEventListener = () => {};
  const doc = {
    body: { classList: {
      toggle: (n, on) => { if (on) bodyClasses.add(n); else bodyClasses.delete(n); },
      contains: (n) => bodyClasses.has(n),
    } },
    querySelectorAll: (sel) => (sel === '[data-act="toggle-private"]' ? [btn] : []),
  };
  const toasts = [];
  const priv = new Function("document", "localStorage", "ICON", "toast", "safe", "window",
    src.slice(pStart, pEnd) +
    "\n return { setPrivacyMode, togglePrivacyMode, isPrivacyOn, initPrivacyMode };")(
    doc,
    { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    { eye: "<eye/>", eyeOff: "<eyeoff/>" },
    (m) => toasts.push(m),
    (fn) => fn,
    {});

  priv.setPrivacyMode(true);
  ok(doc.body.classList.contains("privacy-on"),
    "hiding must be a single class on <body>, so it applies to whichever "
    + "editor is open and costs nothing per keystroke");
  ok(store["abraj_privacy"] === "1",
    "the choice must survive a reload - a screen preference the user set "
    + "because someone was beside them should not come back on refresh");
  ok(btn.icon.innerHTML === "<eyeoff/>" && btn.classes.has("active"),
    "the button is ICON-ONLY, so the struck-through eye is the ONLY visible "
    + "sign that something is hidden - a user who forgot they hid the column "
    + "would otherwise read it as a bug");
  ok(btn.attrs["aria-pressed"] === "true", "and expose the state to a screen reader");
  ok(/hidden/.test(btn.attrs["aria-label"]) && /hidden/.test(btn.title),
    "with no words on the button, the accessible name and the tooltip are all "
    + "a screen reader or an unsure user has; got aria-label="
    + JSON.stringify(btn.attrs["aria-label"]));

  priv.togglePrivacyMode();
  ok(!doc.body.classList.contains("privacy-on") && store["abraj_privacy"] === "0",
    "toggling back must show the figures again");
  ok(btn.icon.innerHTML === "<eye/>" && !btn.classes.has("active"),
    "and put the open eye back");
  ok(/^Hide the COST/.test(btn.attrs["aria-label"]) && /Ctrl\+Shift\+H/.test(btn.title),
    "the tooltip must keep naming the shortcut - it is the only place it is "
    + "discoverable from the editor; got " + JSON.stringify(btn.title));
  ok(toasts.length === 1 && /shown/i.test(toasts[0]),
    "the toggle confirms in words what just happened; got " + JSON.stringify(toasts));

  // Persisted state is read back, and a locked-down localStorage cannot throw.
  ok(priv.isPrivacyOn() === false, "a '0' reads as off");
  store["abraj_privacy"] = "1";
  ok(priv.isPrivacyOn() === true, "a '1' reads as on");

  // Boot must not re-write the file/store just for restoring the state.
  const writes = [];
  const priv2 = new Function("document", "localStorage", "ICON", "toast", "safe", "window",
    src.slice(pStart, pEnd) + "\n return { setPrivacyMode };")(
    doc,
    { getItem: () => "1", setItem: (k, v) => writes.push([k, v]) },
    { eye: "", eyeOff: "" }, () => {}, (fn) => fn, {});
  priv2.setPrivacyMode(true, { persist: false });
  ok(writes.length === 0, "restoring at boot must not persist anything back");
}

/* ============================================================
   12. ...and the CSS actually hides them
   ============================================================ */
{
  const rule = css.slice(css.indexOf("body.privacy-on"), css.indexOf("body.privacy-on") + 200);
  assert(css.includes("body.privacy-on"), "the privacy rule is missing from app_styles.css");
  ok(/\.qc-private/.test(rule) && /\.econ-cards/.test(rule) && /display:\s*none/.test(rule),
    "privacy must hide the COST/PROFIT strip AND the profit cards together - "
    + "hiding the columns while the same numbers sat in the cards below would "
    + "be worse than no control at all; got: " + rule);

  ok(/return `<td class="qc-cost-col qc-private">/.test(src),
    "the COST cell must carry qc-private, or hiding leaves the column behind");
  ok((html.match(/data-act="toggle-private"/g) || []).length === 3,
    "all three editors need the toggle in their footer");
  ok((html.match(/pv-toggle icon-only/g) || []).length === 3,
    "the toggle is icon-only in all three footers");
  ok(!/pv-label/.test(html),
    "an icon-only button must carry no visible label markup");
  ok((html.match(/aria-label="Hide the COST/g) || []).length === 3,
    "a button with no words MUST have an accessible name, or it is an "
    + "unlabelled control to a screen reader");
  ok(/\.pv-toggle\.icon-only/.test(css),
    "the icon-only button needs its own square sizing rule");
  ok(/Ctrl \+ Shift \+ H/.test(src),
    "the shortcut belongs on the cheat sheet - a control nobody can find is "
    + "no use when somebody is already standing beside you");
  ok(/e\.shiftKey && \(e\.key === "H"/.test(src),
    "Ctrl+Shift+H must be bound");
}

/* ============================================================
   13. (was: the Costing screen tells the same story)
   ------------------------------------------------------------
   The separate Costing & Profit screen was retired on 2026-09-11. Its loss
   panel, LOSS tag, signed money and one-cent tolerance were a SECOND telling of
   what sections 1-12 already pin in the three editors, which is where costs are
   actually typed. One telling is the point.
   ============================================================ */

/* ============================================================
   14. Never red/green, never colour alone
   ============================================================ */
{
  const GREEN = /(#16a34a|#22c55e|#15803d|#059669|var\(--success\))/i;

  const at = css.indexOf(".econ-loss {");
  assert(at >= 0, "the .econ-loss rule is missing from app_styles.css");
  ok(!GREEN.test(css.slice(at, at + 4000)),
    "nothing in the loss block may be green - red/green is the one pair this "
    + "user cannot separate");

  const rp = css.indexOf(".row-profit {");
  assert(rp >= 0, ".row-profit rule not found");
  ok(!GREEN.test(css.slice(rp, rp + 700)),
    "a healthy line's profit must stay in the private TEAL, never green: it is "
    + "the same category of number as the cost beside it, and green would make "
    + "the loss legible only by hue");
  ok(/\.row-profit\.is-loss/.test(css) && /\.rp-loss-tag/.test(css),
    "the loss state needs its own rule and its own tag");

}

/* ============================================================
   15. The new column must not disturb paste or column fitting
   ============================================================ */
{
  const gp = fs.readFileSync("static/grid_paste.js", "utf8");
  const wc = gp.slice(gp.indexOf("function writableColumns("), gp.indexOf("function writableColumns(") + 500);
  ok(/_readonly/.test(wc),
    "a wide Excel paste must not spill a value into the computed PROFIT "
    + "column; writableColumns drops it by the _readonly flag");

  const cr = fs.readFileSync("static/col_resize.js", "utf8");
  ok(/qc-private/.test(cr),
    "the fit-to-content pass must treat the whole private strip as FIXED - "
    + "these are narrow money columns, and the fit is for text-driven ones");
}

console.log(`line_profit_and_loss.test.js: ${checks} checks passed`);
