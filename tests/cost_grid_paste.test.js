const fs = require("fs");
const vm = require("vm");

function makeElement(id = "") {
  return {
    id,
    tagName: "DIV",
    dataset: {},
    value: "",
    innerHTML: "",
    hidden: false,
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    appendChild() {},
    remove() {},
    focus() {},
    setSelectionRange() {},
    getBoundingClientRect() {
      return { right: 0, bottom: 0, width: 120, height: 32 };
    },
  };
}

function testQuoteGridPasteExposesSyntheticCostColumn() {
  let captured = null;
  const tbody = makeElement("qRowsBody");
  const context = {
    console,
    setTimeout() {},
    clearTimeout() {},
    window: {
      ICON: { trash: "x" },
      setupGridPaste(_tbody, opts) { captured = opts; },
      autoSizeAllTextareas() {},
      layoutAutofitValue(layout, fallback) {
        return layout && Object.prototype.hasOwnProperty.call(layout, "autofit_rows")
          ? !!layout.autofit_rows
          : !!fallback;
      },
      editorLayoutPayload(v) { return { autofit_rows: !!v }; },
    },
    document: {
      readyState: "loading",
      addEventListener() {},
      getElementById(id) { return id === "qRowsBody" ? tbody : makeElement(id); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      createElement() { return makeElement(); },
    },
    state: { today: { display: "6/7/2026", iso: "2026-07-06" } },
    toast() {},
    showModal: async () => "ok",
    withLoading: async (_, fn) => fn(),
    safe(fn) { return fn; },
    hideAllViews() {},
    setBrand() {},
    showTypeRail() {},
    renderChipRow() {},
    countByKey() { return {}; },
    uniqueSortedKeys() { return []; },
    displayToIso() { return "2026-07-06"; },
    isoToDisplay() { return "6/7/2026"; },
    api() { return {}; },
    esc(v) {
      return String(v ?? "").replace(/[&<>"]/g, s => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[s]));
    },
  };
  context.window.document = context.document;

  vm.createContext(context);
  vm.runInContext(fs.readFileSync("static/quote.js", "utf8") + `
    quoteState.schema = {
      label: "Materials",
      primary_key: "item",
      grand_total: true,
      columns: [
        { key: "item", head: "Item", kind: "text" },
        { key: "qty", head: "Qty", kind: "num" },
        { key: "total", head: "Total", kind: "computed" }
      ],
      terms: []
    };
    quoteState.columns = [
      { key: "item", head: "Item", kind: "text" },
      { key: "qty", head: "Qty", kind: "num" },
      { key: "total", head: "Total", kind: "computed" }
    ];
    quoteState.enterCosts = true;
    _wireQuoteGridPaste();
  `, context, { filename: "static/quote.js" });

  if (!captured) throw new Error("quotation grid paste was not wired");
  const keys = captured.columns().map(c => c.key);
  if (!keys.includes("cost")) {
    throw new Error("quotation grid paste should expose the visible synthetic COST column");
  }
}

function testGridPasteCanStopAtExistingRowsForCostingTables() {
  const listeners = {};
  const tbody = makeElement("costGridBody");
  tbody.addEventListener = (type, fn) => { listeners[type] = fn; };
  tbody.querySelector = () => rowEl;

  const inputEl = makeElement();
  inputEl.tagName = "INPUT";
  inputEl.dataset.k = "cost";
  inputEl.closest = (selector) => selector === "[data-k]" ? inputEl
    : selector === "tr[data-i]" ? rowEl
    : null;

  const rowEl = makeElement();
  rowEl.dataset.i = "0";
  rowEl.querySelector = () => inputEl;

  const context = {
    console,
    setTimeout(fn) { if (typeof fn === "function") fn(); },
    clearTimeout() {},
    window: {
      innerWidth: 1200,
      innerHeight: 800,
      ICON: {},
      toast() {},
    },
    document: {
      activeElement: inputEl,
      body: { appendChild() {} },
      addEventListener() {},
      removeEventListener() {},
      getElementById() { return null; },
      createElement() { return makeElement(); },
    },
    navigator: { clipboard: {} },
  };
  context.window.document = context.document;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("static/grid_paste.js", "utf8"), context, { filename: "static/grid_paste.js" });

  const rows = [{}, {}];
  context.window.setupGridPaste(tbody, {
    columns: () => [{ key: "cost", kind: "money" }],
    rowCount: () => rows.length,
    allowRowGrowth: false,
    setCell: (i, key, value) => { rows[i][key] = value; },
    fullRender() {},
    snapshot: () => JSON.parse(JSON.stringify(rows)),
    restore: snap => {
      rows.length = 0;
      snap.forEach(r => rows.push(r));
    },
  });

  listeners.paste({
    target: inputEl,
    clipboardData: {
      getData(type) {
        return type === "text/plain" ? "10\n20\n30" : "";
      },
    },
    preventDefault() { this.defaultPrevented = true; },
  });

  if (rows.length !== 2) {
    throw new Error("costing-style paste should not append document item rows");
  }
  if (rows[0].cost !== 10 || rows[1].cost !== 20) {
    throw new Error("costing-style paste should fill only existing rows from the anchor downward");
  }
}

testQuoteGridPasteExposesSyntheticCostColumn();
testGridPasteCanStopAtExistingRowsForCostingTables();
console.log("cost grid paste tests passed");
