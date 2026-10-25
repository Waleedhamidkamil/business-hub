const fs = require("fs");
const vm = require("vm");

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function runFrontendFile(file, after) {
  const code = fs.readFileSync(file, "utf8") + "\n" + after;
  const context = {
    console,
    setTimeout(fn) { fn(); },
    window: { ICON: { trash: "x" } },
    document: {
      readyState: "loading",
      addEventListener() {},
      getElementById() { return null; },
      querySelectorAll() { return []; },
      createElement() {
        return {
          className: "",
          innerHTML: "",
          classList: { add() {} },
          querySelector() { return { value: "", addEventListener() {}, disabled: false }; },
          querySelectorAll() { return []; },
        };
      },
    },
    state: {
      today: { month: 7, year: 2026, display: "4/7/2026", iso: "2026-07-04" },
    },
    api() { return {}; },
    toast() {},
    showModal() {},
    withLoading() {},
    safe(fn) { return fn; },
    hideAllViews() {},
    setBrand() {},
    renderChipRow() {},
    countByKey() { return {}; },
    uniqueSortedKeys() { return []; },
    esc,
  };
  vm.createContext(context);
  const result = vm.runInContext(code, context, { filename: file });
  if (result && typeof result.then === "function") await result;
}

(async () => {
await runFrontendFile("static/quote.js", `
  quoteState.schema = {
    columns: [{ key: "equipment", head: "Saved Equipment", kind: "text" }],
    stock_columns: [{ key: "equipment", head: "Stock Equipment", kind: "text" }]
  };
  const cols = _stockColumns();
  if (cols[0].head !== "Saved Equipment") {
    throw new Error("new quote columns ignored effective type settings");
  }
`);

await runFrontendFile("static/quote.js", `
  quoteState.schema = {
    columns: [
      { key: "equipment", head: "Saved Equipment", kind: "text", width: 20 },
      { key: "rate", head: "Saved Rate", kind: "money", width: 14 }
    ]
  };
  quoteState.columns = [
    { key: "equipment", head: "State Equipment", kind: "text", width: 20 },
    { key: "rate", head: "State Rate", kind: "money", width: 14 }
  ];
  document.querySelectorAll = (selector) => selector === "#qThead .qc-head-input"
    ? [
        { dataset: { colKey: "equipment" }, value: "Visible Equipment" },
        { dataset: { colKey: "rate" }, value: "Visible Rate" }
      ]
    : [];
  document.getElementById = (id) => id === "qThead" ? { id: "qThead" } : null;
  window.readEditorColWidths = () => ({ equipment: 31, rate: 19 });

  const cols = quoteSettingsColumnsSnapshot();
  if (cols[0].head !== "Visible Equipment" || cols[1].head !== "Visible Rate") {
    throw new Error("settings snapshot did not read visible table headers");
  }
  if (cols[0].width !== 31 || cols[1].width !== 19) {
    throw new Error("settings snapshot did not read visible table widths");
  }
`);

await runFrontendFile("static/quote.js", `
  quoteState.quoteNo = "QUT-EQ-CLIENT-001";
  quoteState.date = "4/7/2026";
  quoteState.type = "equipment";
  quoteState.terms = [];
  quoteState.mode = "edit";
  quoteState.schema = {
    primary_key: "equipment",
    columns: [
      { key: "equipment", head: "Equipment", kind: "text", width: 20 },
      { key: "rate", head: "Rate", kind: "money", width: 14 }
    ]
  };
  quoteState.columns = [
    { key: "equipment", head: "Equipment", kind: "text", width: 20 },
    { key: "rate", head: "Rate", kind: "money", width: 14 }
  ];
  quoteState.rows = [{ equipment: "Crane", rate: "100" }];
  document.getElementById = (id) => ({
    qClient: { value: "Client" },
    qRfq: { value: "N/A" },
    qAddress: { value: "CSSP Site" },
    // 2026-09-05 - #qReference arrived with the app-only Reference field on
    // 2026-08-24 and this fixture was never told. buildQuotePayload reads it
    // unguarded (as every editor reads its own fields), so the whole file threw
    // and was filed as a known failure for a fortnight. The element map has to
    // keep up with the payload; it is deliberately explicit rather than a
    // catch-all stub, so a missing field fails loudly instead of silently
    // returning an empty string.
    // (No backticks in here - this block lives inside a template literal.)
    qReference: { value: "" },
    qThead: { id: "qThead" }
  })[id] || null;
  window.readEditorColWidths = () => ({ equipment: 33.5, rate: 21.25 });

  const payload = buildQuotePayload();
  if (payload.columns[0].width !== 33.5 || payload.columns[1].width !== 21.25) {
    throw new Error("save payload did not persist live resized column widths");
  }
  if (payload.col_widths.equipment !== 33.5 || payload.col_widths.rate !== 21.25) {
    throw new Error("preview payload did not include live resized column widths");
  }
`);

await runFrontendFile("static/doc_settings.js", `
  const html = renderDocSettingsModal(
    "quotation",
    { company: {}, equipment: {} },
    {
      subtype: "equipment",
      subtypeLabel: "Equipment",
      columns: [
        { key: "equipment", head: "Visible Equipment", kind: "text", width: 31 },
        { key: "rate", head: "Visible Rate", kind: "money", width: 19 }
      ]
    },
    {
      equipment: {
        primary_key: "equipment",
        columns: [{ key: "equipment", head: "Backend Equipment", kind: "text" }]
      }
    }
  );
  if (!html.includes("Visible Equipment") || !html.includes("Visible Rate")) {
    throw new Error("settings modal ignored active editor columns");
  }
  if (!html.includes("&quot;width&quot;:31") || !html.includes("&quot;width&quot;:19")) {
    throw new Error("settings modal did not preserve active column widths");
  }
`);

await runFrontendFile("static/doc_settings.js", `
  (async () => {
  let savedPayload = null;
  let savedMeta = null;
  api = () => ({
    save_doc_settings: async (docType, payload) => {
      savedPayload = payload;
      return { ok: true };
    }
  });

  function el(value = "") {
    return {
      value,
      onclick: null,
      addEventListener() {},
      classList: { add() {} },
      dataset: {},
      disabled: false,
    };
  }

  const saveBtn = el();
  const resetBtn = el();
  const cancelBtn = el();
  const addColBtn = el();
  const wrap = {
    dataset: {
      colsState: JSON.stringify([
        { key: "equipment", head: "Visible Equipment", kind: "text", width: 31, primary: true },
        { key: "doc_only", head: "Document Only", kind: "text", width: 19, primary: false }
      ])
    }
  };
  const list = { innerHTML: "", appendChild() {} };
  const signatureName = el("Engineer");
  const signatureTitle = el("Manager");
  const root = {
    querySelector(selector) {
      if (selector === ".ds-modal") return root;
      if (selector === ".ds-cols-editor") return wrap;
      if (selector === "#dsColsList") return list;
      if (selector === "#dsAddCol") return addColBtn;
      // 2026-09-05 - the action buttons moved from #dsSave/#dsReset/#dsCancel
      // to Popup's [data-value=...] when every dialog was migrated onto the
      // shared Popup on 2026-08-26. This fixture kept the old ids, so the
      // binder wired its handlers onto throwaway elements and the test's
      // saveBtn.onclick stayed null. Both spellings are answered so the file
      // works either way; the data-value one is what the app uses today.
      if (selector === "#dsSave" || selector === "[data-value='save']") return saveBtn;
      if (selector === "#dsReset" || selector === "[data-value='reset']") return resetBtn;
      if (selector === "#dsCancel" || selector === "[data-value='cancel']") return cancelBtn;
      if (selector === '[data-k="signature_name"]') return signatureName;
      if (selector === '[data-k="signature_title"]') return signatureTitle;
      return el();
    },
    querySelectorAll() { return []; },
  };
  // 2026-09-05 - this modelled the OLD two-level shape (backdrop -> .ds-modal
  // -> root) and returned \`root\` for every selector. On 2026-08-26
  // bindDocSettingsModal became \`const root = backdrop\` - Popup.open puts the
  // class on the PANEL and hands that panel back - so the panel IS the root
  // now, and this double was answering the wrong node for .ds-cols-editor.
  // Delegate, so the fixture has the same one-level shape the app has.
  const backdrop = {
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll() { return []; },
    classList: { add() {} },
    remove() {},
    // openDocSettings sets this on the panel; bindDocSettingsModal's close()
    // reads it as (backdrop._popup || _h), and _h is a const in the OTHER
    // function - so without it the handler dies with
    // "ReferenceError: _h is not defined" rather than anything readable.
    _popup: { close() {} },
  };

  bindDocSettingsModal(
    backdrop,
    "quotation",
    {
      company: {},
      equipment: {
        columns: [{ key: "equipment", head: "Default Equipment", kind: "text", width: 20 }]
      }
    },
    {
      subtype: "equipment",
      columns: [
        { key: "equipment", head: "Visible Equipment", kind: "text", width: 31 },
        { key: "doc_only", head: "Document Only", kind: "text", width: 19 }
      ],
      onSaved: (meta) => { savedMeta = meta; }
    },
    { equipment: { primary_key: "equipment" } }
  );

  await saveBtn.onclick();

  const savedColumns = savedPayload.equipment && savedPayload.equipment.columns;
  if (!Array.isArray(savedColumns) || savedColumns[0].head !== "Default Equipment") {
    throw new Error("settings save should preserve existing default columns when active document columns were not edited");
  }
  if (savedColumns.some(c => c.key === "doc_only")) {
    throw new Error("settings save promoted document-only columns to type defaults without a column edit");
  }
  if (!savedMeta || savedMeta.columnsChanged !== false) {
    throw new Error("settings save did not report unchanged columns");
  }
  })();
`);

await runFrontendFile("static/doc_settings.js", `
  (async () => {
  let savedPayload = null;
  let savedMeta = null;
  api = () => ({
    save_doc_settings: async (docType, payload) => {
      savedPayload = payload;
      return { ok: true };
    }
  });

  function el(value = "") {
    return {
      value,
      onclick: null,
      addEventListener() {},
      classList: { add() {} },
      dataset: {},
      disabled: false,
    };
  }

  const saveBtn = el();
  const resetBtn = el();
  const cancelBtn = el();
  const addColBtn = el();
  const wrap = {
    dataset: {
      colsState: JSON.stringify([
        { key: "equipment", head: "Visible Equipment", kind: "text", width: 31, primary: true },
        { key: "rate", head: "Rate", kind: "money", width: 19, primary: false }
      ])
    }
  };
  const list = { innerHTML: "", appendChild() {} };
  const signatureName = el("Engineer");
  const signatureTitle = el("Manager");
  const root = {
    querySelector(selector) {
      if (selector === ".ds-modal") return root;
      if (selector === ".ds-cols-editor") return wrap;
      if (selector === "#dsColsList") return list;
      if (selector === "#dsAddCol") return addColBtn;
      // 2026-09-05 - the action buttons moved from #dsSave/#dsReset/#dsCancel
      // to Popup's [data-value=...] when every dialog was migrated onto the
      // shared Popup on 2026-08-26. This fixture kept the old ids, so the
      // binder wired its handlers onto throwaway elements and the test's
      // saveBtn.onclick stayed null. Both spellings are answered so the file
      // works either way; the data-value one is what the app uses today.
      if (selector === "#dsSave" || selector === "[data-value='save']") return saveBtn;
      if (selector === "#dsReset" || selector === "[data-value='reset']") return resetBtn;
      if (selector === "#dsCancel" || selector === "[data-value='cancel']") return cancelBtn;
      if (selector === '[data-k="signature_name"]') return signatureName;
      if (selector === '[data-k="signature_title"]') return signatureTitle;
      return el();
    },
    querySelectorAll() { return []; },
  };
  // 2026-09-05 - this modelled the OLD two-level shape (backdrop -> .ds-modal
  // -> root) and returned \`root\` for every selector. On 2026-08-26
  // bindDocSettingsModal became \`const root = backdrop\` - Popup.open puts the
  // class on the PANEL and hands that panel back - so the panel IS the root
  // now, and this double was answering the wrong node for .ds-cols-editor.
  // Delegate, so the fixture has the same one-level shape the app has.
  const backdrop = {
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll() { return []; },
    classList: { add() {} },
    remove() {},
    // openDocSettings sets this on the panel; bindDocSettingsModal's close()
    // reads it as (backdrop._popup || _h), and _h is a const in the OTHER
    // function - so without it the handler dies with
    // "ReferenceError: _h is not defined" rather than anything readable.
    _popup: { close() {} },
  };

  bindDocSettingsModal(
    backdrop,
    "quotation",
    { company: {}, equipment: {} },
    {
      subtype: "equipment",
      columns: [
        { key: "equipment", head: "Visible Equipment", kind: "text", width: 31 },
        { key: "rate", head: "Rate", kind: "money", width: 19 }
      ],
      onSaved: (meta) => { savedMeta = meta; }
    },
    { equipment: { primary_key: "equipment" } }
  );

  addColBtn.onclick();
  await saveBtn.onclick();

  const savedColumns = savedPayload.equipment && savedPayload.equipment.columns;
  if (!Array.isArray(savedColumns) || !savedColumns.some(c => c.key === "col_3")) {
    throw new Error("edited settings columns were not saved as type defaults");
  }
  if (!savedMeta || savedMeta.columnsChanged !== true) {
    throw new Error("settings save did not report changed columns");
  }
  })();
`);

console.log("quote settings column tests passed");
})().catch(err => {
  console.error(err);
  process.exit(1);
});
