/* DN autofill (2026-08-13).

   Asked plainly: "if I enter a cost for Celery in a DN and save it, does the
   next DN with Celery get that cost?" It did - and that was the problem.

   1. THE REMEMBERED COST WAS A CURRENCY TRAP. `save_dn_record` stored the row
      VERBATIM, so the cost went into the catalog as a bare number with nothing
      recording whether it was dollars or dinars. The live catalog held 2002,
      504 and 49994 for items whose documents read 1.43, 0.36 and 35.71. The
      picker then copied that number straight into the cost cell. Exactly the
      bug already fixed on the quotation and invoice sides; the DN now stores
      `cost_usd` and the picker converts back into the DN's own currency.

   2. THE RECALLED COST WAS NEVER PAINTED. The repaint walked `dnColumns()`,
      which omits the synthetic COST column, so a recalled cost reached state
      and then vanished from the screen - picking an item looked like it
      ignored the cost entirely. Same bug, same fix as inv.js.

   3. `image_refs` IS AN OBJECT. String()-ing it in the dropdown subtitle
      printed the literal text "[object Object]". Harmless until the backfill
      gave 33 DN entries a picture, and until the collapse started PREFERRING
      the entry that has one.

   The picker is EXECUTED here, not pattern-matched: the comments above name
   every identifier being checked, so text assertions would largely be
   matching this explanation. Where source shape is genuinely the subject
   (which column list the repaint walks) the stubs are built to make the wrong
   choice observable rather than asserting on the text.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const rawDn = fs.readFileSync("static/dn.js", "utf8").replace(/\r\n/g, "\n");
const stripJs = (s) => s.replace(/\/\*[\s\S]*?\*\//g, " ")
                        .split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
const dn = stripJs(rawDn);

/* The picker converts through `window.costFromMemory` (app.js), shared by all
   three editors. Pull in the REAL one rather than stubbing it - a stub would
   let this test pass while the actual conversion was broken. */
const rawApp = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const _cfmStart = rawApp.indexOf("function costFromMemory(");
const _cfmEnd = rawApp.indexOf("window.costFromMemory = costFromMemory;");
assert(_cfmStart >= 0 && _cfmEnd > _cfmStart, "costFromMemory missing from app.js");
const costFromMemory = new Function("window",
  rawApp.slice(_cfmStart, _cfmEnd) + "\nreturn costFromMemory;")({ IQD_PER_USD: 1400 });
/* Since 2026-08-26 the picker fills through ONE shared helper,
   `costCellFromMemory`, which answers with the number AND the unit it is
   written in. Pull in the real one, for the same reason as above. */
const _ccfmStart = rawApp.indexOf("function costCellFromMemory(");
const _ccfmEnd = rawApp.indexOf("window.costCellFromMemory = costCellFromMemory;");
assert(_ccfmStart >= 0 && _ccfmEnd > _ccfmStart, "costCellFromMemory missing from app.js");
const costCellFromMemory = new Function("window",
  rawApp.slice(_cfmStart, _cfmEnd) + rawApp.slice(_ccfmStart, _ccfmEnd)
  + "\nreturn costCellFromMemory;")({ IQD_PER_USD: 1400 });
/* The $/IQD CHIP is not a `data-k` cell, so the picker's repaint loop cannot
   touch it - it is painted explicitly (2026-08-26). The real reader decides
   which unit it shows, so pull that in too rather than stubbing it. */
const _rccStart = rawApp.indexOf("function rowCostCurrency(");
const _rccEnd = rawApp.indexOf("window.rowCostCurrency = rowCostCurrency;");
assert(_rccStart >= 0 && _rccEnd > _rccStart, "rowCostCurrency missing from app.js");
const rowCostCurrency = new Function(
  rawApp.slice(_rccStart, _rccEnd) + "\nreturn rowCostCurrency;")();
const _pfmStart = rawApp.indexOf("function priceFromMemory(");
const _pfmEnd = rawApp.indexOf("window.priceFromMemory = priceFromMemory;");
assert(_pfmStart >= 0 && _pfmEnd > _pfmStart, "priceFromMemory missing from app.js");
const priceFromMemory = new Function("window",
  rawApp.slice(_pfmStart, _pfmEnd) + "\nreturn priceFromMemory;")({ IQD_PER_USD: 1400 });

/* Pull one top-level function out of the file, comments and all. */
function extract(name) {
  const at = rawDn.indexOf("function " + name + "(");
  assert(at >= 0, name + " not found in static/dn.js");
  const rest = rawDn.slice(at);
  return rest.slice(0, rest.indexOf("\n}") + 2);
}

/* ------------------------------------------------------------------ *
 * A DN row's cells, so we can see what was actually PAINTED.
 * ------------------------------------------------------------------ */
function fakeRow(keys) {
  const cells = {};
  keys.forEach((k) => { cells[k] = { value: "", tagName: "INPUT" }; });
  return {
    cells,
    querySelector(sel) {
      const m = /\[data-k="([^"]+)"\]/.exec(sel);
      return m ? (cells[m[1]] || null) : null;
    },
  };
}

const BASE_COLS = [{ key: "item" }, { key: "description" }, { key: "unit" },
                   { key: "qty" }, { key: "rate" }];
const COST_COL = { key: "cost", kind: "money" };

function runPick(suggestion, opts) {
  opts = opts || {};
  const row = {};
  const dnState = {
    rows: [row, {}],
    costCurrency: opts.costCurrency || "USD",
    dirty: false,
  };
  const tr = fakeRow(BASE_COLS.map((c) => c.key).concat(["cost"]));
  const doc = { querySelector: () => tr };
  const chip = { ccy: null };
  const pick = new Function(
    "dacTarget", "dacItems", "dnState", "document", "window",
    "dnColumns", "dnRenderColumns", "autoResizeDnTextarea",
    "hideDnAc", "updateDnTotals", "addDnRow", "dnPegSync",
    extract("pickDnSuggestion") + "\nreturn pickDnSuggestion;"
  )(
    { field: {}, rowIdx: 0 },
    [suggestion],
    dnState,
    doc,
    // `window.ITEM_META_KEYS` (app.js) lists the payload keys that describe
    // the ITEM rather than a column - cost_usd, img_query and the
    // other-language name. The real page always has it; this sandbox
    // evals the picker on its own and must supply it.
    { costFromMemory, costCellFromMemory, priceFromMemory, rowCostCurrency,
      paintRowCostChip: (_tr, ccy) => { chip.ccy = ccy; },
      IQD_PER_USD: 1400,
      ITEM_META_KEYS: new Set(["cost_usd", "cost_native", "cost_native_ccy",
        "price_native", "price_native_ccy", "item_no",
        "name_ar", "name_en", "img_query"]) },
    /* dnColumns()      */ () => BASE_COLS.slice(),
    /* dnRenderColumns()*/ () => BASE_COLS.concat([COST_COL]),
    () => {}, () => {}, () => {}, () => {},
    () => opts.peg || 1400
  );
  pick(0);
  return { row, cells: tr.cells, chip: chip.ccy };
}

/* ---------- 4c. the Master Data price becomes the DN rate ---------- */
{
  const r = runPick({ name: "Wire rope", data: {
    price_native: 150, price_native_ccy: "USD",
    cost_native: 180000, cost_native_ccy: "IQD",
  }}, { costCurrency: "USD", peg: 1400 });
  ok(String(r.row.rate) === "150",
     "the Master Data price must fill the DN rate, got " + r.row.rate);
  ok(r.cells.rate.value === "150", "the recalled price must be painted in Rate");
}

/* ---------- 1. a remembered cost is USD, and converts ---------- */
{
  const r = runPick({ name: "Celery", data: { unit: "KG", cost_usd: 1.43 } },
                    { costCurrency: "USD" });
  ok(String(r.row.cost) === "1.43",
     "a USD DN should recall the cost as 1.43, got " + r.row.cost);

  const iqd = runPick({ name: "Celery", data: { unit: "KG", cost_usd: 1.43 } },
                      { costCurrency: "IQD", peg: 1400 });
  ok(String(iqd.row.cost) === "2002",
     "an IQD DN should recall 1.43 USD as 2002 IQD, got " + iqd.row.cost);
}

/* ---------- 2. a legacy raw cost is NEVER replayed ---------- */
{
  /* This is the whole point. 2002 was typed as dinars; nothing recorded that.
     Copying it into a dollar cell reads as $2,002 against a $0.94 sell price
     and turns a healthy margin into a catastrophic-looking loss. */
  const r = runPick({ name: "Celery", data: { unit: "KG", cost: "2002" } });
  ok(!r.row.cost, "a raw currency-less cost must not be replayed, got " + r.row.cost);
  ok(r.cells.cost.value === "",
     "and it must not be painted either, got " + r.cells.cost.value);
  ok(r.row.unit === "KG", "the rest of the row still autofills");
}

/* ---------- 3. the recalled cost is PAINTED, not just stored ---------- */
{
  /* dnColumns() omits the synthetic COST column. The stubs above return
     different lists precisely so walking the wrong one is visible here. */
  const r = runPick({ name: "Celery", data: { unit: "KG", cost_usd: 1.43 } });
  ok(r.cells.cost.value === "1.43",
     "the COST cell must be repainted (walk dnRenderColumns, not dnColumns), got '"
     + r.cells.cost.value + "'");
  ok(r.cells.unit.value === "KG", "ordinary cells are still repainted");
}

/* ---------- 4. references and markers are not values ---------- */
{
  const r = runPick({ name: "Celery",
                      data: { unit: "KG", image_refs: { image: "abc" },
                              cost_ccy: "IQD", cost_usd: 2 } });
  ok(r.row.image_refs === undefined,
     "image_refs is a reference map, not a cell value");
  /* `cost_ccy` is NOT copied from the remembered entry - it is stamped with
     THIS document's currency, so the number just written is self-describing.
     The memory said IQD; the DN is costing in USD; the row must say USD. */
  ok(r.row.cost_ccy === "USD",
     "the row must be stamped with the DOCUMENT's currency, got " + r.row.cost_ccy);
  ok(r.row.cost_usd === undefined, "cost_usd is not a column; it becomes `cost`");
  ok(String(r.row.cost) === "2", "…and it did become the cost");
}

/* ---------- 4b. a native cost keeps the currency it was SAVED in ---------- */
{
  /* CHANGED 2026-08-20 (Waleed): a cost saved in dinars fills as dinars, and
     the cell's own $/IQD toggle shows it. NO conversion to the document's cost
     currency - a cost typed in IQD must never be silently restated in dollars.
     The document currency no longer touches the number at all. */
  for (const docCcy of ["IQD", "USD"]) {
    const r = runPick({ name: "Celery", data: { cost_native: 1500, cost_native_ccy: "IQD" } },
                      { costCurrency: docCcy, peg: 1500 });
    ok(Number(r.row.cost) === 1500,
       `1500 IQD must stay 1500 whatever the ${docCcy} doc costs in, got ` + r.row.cost);
    ok(String(r.row.cost_ccy).toUpperCase() === "IQD",
       "the cost keeps its saved currency (IQD), got " + r.row.cost_ccy);
    /* And the CHIP says so (2026-08-26). It carries no `data-k`, so the
       repaint loop above cannot touch it - the picker has to paint it
       explicitly, or the dinar number sits under a "$" and the toggle
       contradicts the number it labels. */
    ok(r.chip === "IQD",
       "the $/IQD chip must be repainted to the saved unit, got "
       + JSON.stringify(r.chip));
  }
}

/* ---------- 5. a zero / missing cost leaves the cell alone ---------- */
{
  const r = runPick({ name: "Celery", data: { unit: "KG" } });
  ok(!r.row.cost, "nothing remembered means nothing filled");
  const z = runPick({ name: "Celery", data: { unit: "KG", cost_usd: 0 } });
  ok(!z.row.cost, "a zero cost is not a cost");
}

/* ---------- 6. the save payload carries the currency ---------- */
{
  /* dn.js calls set_cost_currency AFTER save_dn returns, so the backend
     reading the stored flag would be one save behind - it would remember a
     dinar cost as dollars. The currency has to ride WITH the save. */
  const body = extract("buildDnPayload");
  ok(/cost_currency:\s*dnState\.costCurrency/.test(body),
     "buildDnPayload must send cost_currency");
  ok(/cost_iqd_per_usd:\s*dnPegSync\(\)/.test(body),
     "buildDnPayload must send the peg the cells were typed against");
}

/* ---------- 7. the dropdown subtitle never prints [object Object] ---------- */
{
  const at = dn.indexOf("function showDnAc(");
  const body = dn.slice(at, dn.indexOf("\n}", at) + 2);
  /* Scoped to the SKIP GUARD. A bare /typeof v === "object"/ also matches the
     hasImage line two lines up, so removing the guard left this passing. */
  ok(/v === "" \|\| typeof v === "object"\) continue/.test(body),
     "showDnAc must skip object-valued fields (image_refs printed as [object Object])");
  ok(/ITEM_META_KEYS\.has\(k\)/.test(body),
     "cost_usd is stored in a different unit than the cell - keep it out of the "
     + "subtitle. Since 2026-08-16 the shared set also keeps out `img_query` "
     + "and the item's other-language name, which showed as Arabic text beside "
     + "an English item.");

  /* Prove it by running the subtitle builder over a real stored payload. */
  const loop = body.slice(body.indexOf("const parts = []"));
  /* The loop names `window.ITEM_META_KEYS` (defined in app.js), so the sandbox
     has to hand one over the same way the page would. */
  const META = { ITEM_META_KEYS: new Set(["cost_usd", "cost_native", "cost_native_ccy",
    "price_native", "price_native_ccy", "item_no", "name_ar", "name_en", "img_query"]) };
  const built = new Function("s", "window",
    loop.slice(0, loop.indexOf("const tag =")) + "\nreturn { sub, hasImage };");
  /* `col_2` is an object here on purpose: `image_refs` has its own `continue`
     BEFORE the guard, so a payload whose only object is image_refs cannot tell
     whether the guard still exists. Real dn_items rows do carry col_2. */
  const out = built({ data: { unit: "KG", rate: 0.94, cost_usd: 1.43,
                              name_ar: "كرفس", img_query: "celery stalks",
                              col_2: { nested: 1 },
                              image_refs: { image: "abc" } } }, META);
  ok(!/object Object/.test(out.sub),
     "subtitle leaked an object: " + out.sub);
  ok(!/1\.43/.test(out.sub), "subtitle leaked the USD cost: " + out.sub);
  ok(!/كرفس/.test(out.sub),
     "subtitle leaked the item's Arabic name - reported as 'arabic suggestions "
     + "in quotation': " + out.sub);
  ok(!/celery stalks/.test(out.sub),
     "subtitle leaked the picture-search wording: " + out.sub);
  ok(out.sub === "KG · 0.94", "subtitle should read 'KG · 0.94', got " + out.sub);
  ok(out.hasImage === true, "an item with a picture is flagged");
  ok(built({ data: { unit: "KG" } }, META).hasImage === false,
     "…and one without is not");
}

/* ---------- 8. the picture tag is a WORD, not a colour ---------- */
{
  /* The user is colour blind: a state carried by hue alone is invisible. */
  const at = dn.indexOf("function showDnAc(");
  const body = dn.slice(at, dn.indexOf("\n}", at) + 2);
  ok(/>Image<|Image<\/span>|\+ `Image/.test(body),
     "the has-a-picture tag must carry the word 'Image', not just an icon");
  /* Anchored to the start of a line: `.ac-item.active .ac-img-tag {` also
     matches a loose /\.ac-img-tag\s*\{/, so renaming the BASE rule left this
     passing while the tag rendered unstyled. */
  const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");
  ok(/(^|\n)\.ac-img-tag\s*\{/.test(css),
     ".ac-img-tag is emitted by three editors and must have its own base rule");
  ok(/(^|\n)\.ac-item \.ac-name\s*\{/.test(css),
     ".ac-name wraps the tag and needs its own rule too");
}

console.log("dn_autofill: OK (" + checks + " checks)");
