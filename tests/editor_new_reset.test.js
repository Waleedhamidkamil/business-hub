const fs = require("fs");
const vm = require("vm");

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function makeElement(id = "") {
  return {
    id,
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    checked: false,
    disabled: false,
    dataset: {},
    style: {},
    tagName: "INPUT",
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild() {},
    remove() {},
    addEventListener() {},
    setAttribute() {},
    focus() {},
    querySelector() { return makeElement(); },
    querySelectorAll() { return []; },
    getBoundingClientRect() { return { top: 0, left: 0, bottom: 0, width: 120 }; },
  };
}

async function runFrontendFile(file, after) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const el = makeElement(id);
      if (/Thead$/.test(id)) el.tagName = "THEAD";
      if (/RowsBody$/.test(id)) el.tagName = "TBODY";
      if (/Date$/.test(id)) el.tagName = "INPUT";
      elements.set(id, el);
    }
    return elements.get(id);
  };

  const context = {
    console,
    setTimeout,
    clearTimeout,
    Promise,
    window: {
      ICON: { trash: "x" },
      scrollX: 0,
      scrollY: 0,
      setModeChip() {},
      setUnsavedChip() {},
      setSignChip() {},
      refreshUniversalSettingsBtn() {},
      showTypeRail() {},
      refreshOpenPanel() {},
      autoSizeAllTextareas() {},
    },
    document: {
      readyState: "loading",
      addEventListener() {},
      getElementById: element,
      createElement(tag) {
        const el = makeElement();
        el.tagName = String(tag || "").toUpperCase();
        return el;
      },
      querySelector() { return makeElement(); },
      querySelectorAll() { return []; },
    },
    state: {
      today: { month: 7, year: 2026, display: "4/7/2026", iso: "2026-07-04" },
    },
    api() {
      return {
        suggest_clients: async () => [],
        list_quote_numbers: async () => [],
        get_next_quote_number: async () => ({ quote_no: "QUT-NEW" }),
        get_next_inv_number: async () => ({ inv_no: "INV-NEW" }),
        get_next_dn_number: async () => ({ dn_no: "DN-NEW" }),
      };
    },
    toast() {},
    showModal: async () => "discard",
    withLoading: async (_, fn) => fn(),
    safe(fn) { return fn; },
    hideAllViews() {},
    setBrand() {},
    showTypeRail() {},
    renderChipRow() {},
    countByKey() { return {}; },
    uniqueSortedKeys() { return []; },
    displayToIso() { return "2026-07-04"; },
    isoToDisplay() { return "4/7/2026"; },
    esc,
  };
  context.window.displayToIso = context.displayToIso;
  context.window.isoToDisplay = context.isoToDisplay;

  vm.createContext(context);
  /* The three editors build their cost cell with shared helpers that live in
     app.js, which this harness does not load. Rather than hand-stub the markup
     - which would drift from the real thing and quietly stop testing it - pull
     the actual block out of app.js and run it in the same context. */
  {
    const appSrc = fs.readFileSync("static/app.js", "utf8");
    const from = appSrc.indexOf("function rowCostCurrency(");
    const marker = "window.convertRowsToCostCurrency = convertRowsToCostCurrency;";
    const to = appSrc.indexOf(marker);
    if (from < 0 || to < 0) {
      throw new Error("app.js: shared cost-cell helpers not found - did they move?");
    }
    vm.runInContext(appSrc.slice(from, to + marker.length), context,
                    { filename: "static/app.js (cost-cell helpers)" });

    /* Same reason for bindDocDate (2026-09-02): all three editors paint and
       wire their date field through it, so a hand-stub here would stop testing
       the real reset. */
    const bindFrom = appSrc.indexOf("function bindDocDate(");
    const bindMarker = "window.bindDocDate = bindDocDate;";
    const bindTo = appSrc.indexOf(bindMarker);
    if (bindFrom < 0 || bindTo < 0) {
      throw new Error("app.js: bindDocDate not found - did it move?");
    }
    vm.runInContext(appSrc.slice(bindFrom, bindTo + bindMarker.length), context,
                    { filename: "static/app.js (bindDocDate)" });
  }
  const code = fs.readFileSync(file, "utf8") + "\n" + after;
  await vm.runInContext(code, context, { filename: file });
  return context;
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setTimeout(resolve, 0));
}

async function testQuoteDoesNotRestoreCleanFinishedColumns() {
  await runFrontendFile("static/quote.js", `
    (async () => {
      quoteState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "equipment",
          columns: [{ key: "equipment", head: "Equipment", kind: "text" }],
          terms: []
        },
        materials: {
          label: "Materials",
          primary_key: "item",
          columns: [{ key: "item", head: "Item", kind: "text" }],
          terms: []
        }
      };
      quoteState.type = "equipment";
      quoteState.schema = quoteState.types.equipment;
      quoteState.initialized = true;
      quoteState.mode = "edit";
      quoteState.dirty = false;
      quoteState.columns = [
        { key: "equipment", head: "Equipment", kind: "text" },
        { key: "old_custom", head: "Old custom", kind: "text" }
      ];
      quoteState.rows = [{ equipment: "Crane", old_custom: "legacy" }];

      enterQuoteType("materials");
      await Promise.resolve();
      enterQuoteType("equipment");
      await Promise.resolve();

      if (quoteState.columns.some(c => c.key === "old_custom")) {
        throw new Error("clean finished quote columns were restored as a draft");
      }
    })()
  `);
}

async function testQuoteStillRestoresDirtyDraftColumns() {
  await runFrontendFile("static/quote.js", `
    (async () => {
      quoteState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "equipment",
          columns: [{ key: "equipment", head: "Equipment", kind: "text" }],
          terms: []
        },
        materials: {
          label: "Materials",
          primary_key: "item",
          columns: [{ key: "item", head: "Item", kind: "text" }],
          terms: []
        }
      };
      quoteState.type = "equipment";
      quoteState.schema = quoteState.types.equipment;
      quoteState.initialized = true;
      quoteState.mode = "new";
      quoteState.dirty = true;
      quoteState.columns = [
        { key: "equipment", head: "Equipment", kind: "text" },
        { key: "draft_custom", head: "Draft custom", kind: "text" }
      ];
      quoteState.rows = [{ equipment: "Crane", draft_custom: "keep" }];

      enterQuoteType("materials");
      await Promise.resolve();
      enterQuoteType("equipment");
      await Promise.resolve();

      if (!quoteState.columns.some(c => c.key === "draft_custom")) {
        throw new Error("dirty quote draft columns were not restored");
      }
    })()
  `);
}

async function testInvoiceDoesNotRestoreCleanFinishedColumns() {
  await runFrontendFile("static/inv.js", `
    (async () => {
      invState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "equipment",
          stock_columns: [{ key: "equipment", head: "Equipment", kind: "text" }],
          columns: [{ key: "equipment", head: "Equipment", kind: "text" }]
        },
        materials: {
          label: "Materials",
          primary_key: "item",
          stock_columns: [{ key: "item", head: "Item", kind: "text" }],
          columns: [{ key: "item", head: "Item", kind: "text" }]
        }
      };
      invState.type = "equipment";
      invState.schema = invState.types.equipment;
      invState.initialized = true;
      invState.mode = "edit";
      invState.dirty = false;
      invState.bankInfo = [{ key: "main" }];
      invState.columns = [
        { key: "equipment", head: "Equipment", kind: "text" },
        { key: "old_custom", head: "Old custom", kind: "text" }
      ];
      invState.rows = [{ equipment: "Generator", old_custom: "legacy" }];

      enterInvType("materials");
      await Promise.resolve();
      enterInvType("equipment");
      await Promise.resolve();

      if (invState.columns.some(c => c.key === "old_custom")) {
        throw new Error("clean finished invoice columns were restored as a draft");
      }
    })()
  `);
}

async function testInvoiceStillRestoresDirtyDraftColumns() {
  await runFrontendFile("static/inv.js", `
    (async () => {
      invState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "equipment",
          stock_columns: [{ key: "equipment", head: "Equipment", kind: "text" }],
          columns: [{ key: "equipment", head: "Equipment", kind: "text" }]
        },
        materials: {
          label: "Materials",
          primary_key: "item",
          stock_columns: [{ key: "item", head: "Item", kind: "text" }],
          columns: [{ key: "item", head: "Item", kind: "text" }]
        }
      };
      invState.type = "equipment";
      invState.schema = invState.types.equipment;
      invState.initialized = true;
      invState.mode = "new";
      invState.dirty = true;
      invState.bankInfo = [{ key: "main" }];
      invState.columns = [
        { key: "equipment", head: "Equipment", kind: "text" },
        { key: "draft_custom", head: "Draft custom", kind: "text" }
      ];
      invState.rows = [{ equipment: "Generator", draft_custom: "keep" }];

      enterInvType("materials");
      await Promise.resolve();
      enterInvType("equipment");
      await Promise.resolve();

      if (!invState.columns.some(c => c.key === "draft_custom")) {
        throw new Error("dirty invoice draft columns were not restored");
      }
    })()
  `);
}

async function testInvoiceNewUsesEffectiveDefaultColumns() {
  await runFrontendFile("static/inv.js", `
    (async () => {
      invState.schema = {
        label: "Equipment",
        primary_key: "item",
        stock_columns: [{ key: "item", head: "Built-in Item", kind: "text" }],
        columns: [
          { key: "item", head: "Default Item", kind: "text" },
          { key: "monthly_rate", head: "Monthly Rate", kind: "money" }
        ]
      };
      invState.type = "equipment";
      invState.bankInfo = [{ key: "main" }];

      await initNewInv();

      if (invState.columns.map(c => c.key).join(",") !== "item,monthly_rate") {
        throw new Error("new invoice ignored effective default columns");
      }
    })()
  `);
}

async function testDeliveryNewResetsColumns() {
  await runFrontendFile("static/dn.js", `
    (async () => {
      dnState.baseColumns = [{ key: "item", head: "Item", kind: "text" }];
      dnState.stockBaseColumns = [{ key: "item", head: "Item", kind: "text" }];
      dnState.priceColumns = [];
      dnState.columns = [
        { key: "item", head: "Item", kind: "text" },
        { key: "old_custom", head: "Old custom", kind: "text" }
      ];
      dnState.rows = [{ item: "Pipe", old_custom: "legacy" }];

      await initNewDn();

      if (dnState.columns.some(c => c.key === "old_custom")) {
        throw new Error("delivery note new document kept old custom columns");
      }
    })()
  `);
}

async function testDeliveryNewUsesEffectiveDefaultColumns() {
  await runFrontendFile("static/dn.js", `
    (async () => {
      dnState.baseColumns = [
        { key: "item", head: "Default Item", kind: "text" },
        { key: "remarks", head: "Remarks", kind: "text" }
      ];
      dnState.stockBaseColumns = [{ key: "item", head: "Built-in Item", kind: "text" }];
      dnState.priceColumns = [];

      await initNewDn();

      if (dnState.columns.map(c => c.key).join(",") !== "item,remarks") {
        throw new Error("new delivery note ignored effective default columns");
      }
    })()
  `);
}

async function testQuoteLoadsColumnsPerSavedDocument() {
  await runFrontendFile("static/quote.js", `
    (async () => {
      const defaultCols = [
        { key: "item", head: "Item", kind: "text" },
        { key: "description", head: "Description", kind: "text" }
      ];
      quoteState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "item",
          columns: defaultCols,
          terms: []
        }
      };
      quoteState.layoutDefault = { autofit_rows: false };
      const records = {
        "QUT-1": {
          quote_type: "equipment",
          quote_date: "4/7/2026",
          client_name: "Client A",
          rfq_no: "RFQ-1",
          client_address: "Address A",
          layout: { autofit_rows: true },
          rows: [{ item: "Pipe", description: "Steel", unit: "pcs", qty: "2", price: "10" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "unit", head: "Unit", kind: "text" },
            { key: "qty", head: "Qty", kind: "num" },
            { key: "price", head: "Price", kind: "money" }
          ],
          terms: []
        },
        "QUT-2": {
          quote_type: "equipment",
          quote_date: "4/7/2026",
          client_name: "Client B",
          rfq_no: "RFQ-2",
          client_address: "Address B",
          layout: { autofit_rows: false },
          rows: [{ item: "Generator", description: "Silent", monthly_rate: "500", remarks: "Standby" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "monthly_rate", head: "Monthly Rate", kind: "money" },
            { key: "remarks", head: "Remarks", kind: "text" }
          ],
          terms: []
        }
      };
      api = () => ({
        load_quote: async (no) => records[no],
        get_quote_types: async () => quoteState.types,
        get_cost_currency: async () => "USD",
        suggest_clients: async () => [],
        list_quote_numbers: async () => [],
        get_next_quote_number: async () => ({ quote_no: "QUT-NEW" })
      });

      await loadQuoteForEdit("QUT-1");
      if (quoteState.columns.map(c => c.key).join(",") !== "item,description,unit,qty,price") {
        throw new Error("QUT-1 did not load its own saved columns");
      }
      if (quoteState.autofitRows !== true) {
        throw new Error("QUT-1 did not load its own Auto-fit row height setting");
      }

      await loadQuoteForEdit("QUT-2");
      if (quoteState.columns.map(c => c.key).join(",") !== "item,description,monthly_rate,remarks") {
        throw new Error("QUT-2 did not replace QUT-1 columns with its own saved columns");
      }
      if (quoteState.autofitRows !== false) {
        throw new Error("QUT-2 inherited QUT-1 Auto-fit row height setting");
      }

      await initNewQuote();
      if (quoteState.columns.map(c => c.key).join(",") !== "item,description") {
        throw new Error("new quotation inherited columns from a saved quotation");
      }
      if (quoteState.autofitRows !== false) {
        throw new Error("new quotation inherited Auto-fit row height from a saved quotation");
      }
    })()
  `);
}

async function testInvoiceLoadsColumnsPerSavedDocument() {
  await runFrontendFile("static/inv.js", `
    (async () => {
      const defaultCols = [
        { key: "item", head: "Item", kind: "text" },
        { key: "description", head: "Description", kind: "text" }
      ];
      invState.types = {
        equipment: {
          label: "Equipment",
          primary_key: "item",
          columns: defaultCols,
          stock_columns: defaultCols
        }
      };
      invState.layoutDefault = { autofit_rows: false };
      invState.bankInfo = [{ key: "main" }];
      const records = {
        "INV-1": {
          inv_type: "equipment",
          inv_date: "4/7/2026",
          client_name: "Client A",
          lpo_no: "N/A",
          client_address: "Address A",
          payment_bank_key: "main",
          withholding_included: true,
          layout: { autofit_rows: true },
          rows: [{ item: "Pipe", description: "Steel", unit: "pcs", qty: "2", price: "10" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "unit", head: "Unit", kind: "text" },
            { key: "qty", head: "Qty", kind: "num" },
            { key: "price", head: "Price", kind: "money" }
          ]
        },
        "INV-2": {
          inv_type: "equipment",
          inv_date: "4/7/2026",
          client_name: "Client B",
          lpo_no: "N/A",
          client_address: "Address B",
          payment_bank_key: "main",
          withholding_included: true,
          layout: { autofit_rows: false },
          rows: [{ item: "Generator", description: "Silent", monthly_rate: "500", remarks: "Standby" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "monthly_rate", head: "Monthly Rate", kind: "money" },
            { key: "remarks", head: "Remarks", kind: "text" }
          ]
        }
      };
      api = () => ({
        load_invoice: async (no) => records[no],
        get_inv_types: async () => invState.types,
        get_inv_defaults: async () => ({ bank_info: [{ key: "main" }], withholding_pct: 3.3 }),
        suggest_clients: async () => [],
        list_quote_numbers: async () => [],
        get_next_inv_number: async () => ({ inv_no: "INV-NEW" })
      });

      await loadInvForEdit("INV-1");
      if (invState.columns.map(c => c.key).join(",") !== "item,description,unit,qty,price") {
        throw new Error("INV-1 did not load its own saved columns");
      }
      if (invState.autofitRows !== true) {
        throw new Error("INV-1 did not load its own Auto-fit row height setting");
      }

      await loadInvForEdit("INV-2");
      if (invState.columns.map(c => c.key).join(",") !== "item,description,monthly_rate,remarks") {
        throw new Error("INV-2 did not replace INV-1 columns with its own saved columns");
      }
      if (invState.autofitRows !== false) {
        throw new Error("INV-2 inherited INV-1 Auto-fit row height setting");
      }

      await initNewInv();
      if (invState.columns.map(c => c.key).join(",") !== "item,description") {
        throw new Error("new invoice inherited columns from a saved invoice");
      }
      if (invState.autofitRows !== false) {
        throw new Error("new invoice inherited Auto-fit row height from a saved invoice");
      }
    })()
  `);
}

async function testDeliveryLoadsColumnsPerSavedDocument() {
  await runFrontendFile("static/dn.js", `
    (async () => {
      dnState.baseColumns = [
        { key: "item", head: "Item", kind: "text" },
        { key: "description", head: "Description", kind: "text" }
      ];
      dnState.stockBaseColumns = dnState.baseColumns;
      dnState.priceColumns = [];
      dnState.layoutDefault = { autofit_rows: false };
      const records = {
        "DN-1": {
          dn_date: "4/7/2026",
          client_name: "Client A",
          lpo_no: "N/A",
          client_address: "Address A",
          include_prices: false,
          layout: { autofit_rows: true },
          rows: [{ item: "Pipe", description: "Steel", unit: "pcs", qty: "2" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "unit", head: "Unit", kind: "text" },
            { key: "qty", head: "Qty", kind: "num" }
          ]
        },
        "DN-2": {
          dn_date: "4/7/2026",
          client_name: "Client B",
          lpo_no: "N/A",
          client_address: "Address B",
          include_prices: false,
          layout: { autofit_rows: false },
          rows: [{ item: "Generator", description: "Silent", remarks: "Standby" }],
          columns: [
            { key: "item", head: "Item", kind: "text" },
            { key: "description", head: "Description", kind: "text" },
            { key: "remarks", head: "Remarks", kind: "text" }
          ]
        }
      };
      api = () => ({
        load_dn: async (no) => records[no],
        suggest_clients: async () => [],
        list_quote_numbers: async () => [],
        get_next_dn_number: async () => ({ dn_no: "DN-NEW" })
      });

      await loadDnForEdit("DN-1");
      if (dnState.columns.map(c => c.key).join(",") !== "item,description,unit,qty") {
        throw new Error("DN-1 did not load its own saved columns");
      }
      if (dnState.autofitRows !== true) {
        throw new Error("DN-1 did not load its own Auto-fit row height setting");
      }

      await loadDnForEdit("DN-2");
      if (dnState.columns.map(c => c.key).join(",") !== "item,description,remarks") {
        throw new Error("DN-2 did not replace DN-1 columns with its own saved columns");
      }
      if (dnState.autofitRows !== false) {
        throw new Error("DN-2 inherited DN-1 Auto-fit row height setting");
      }

      await initNewDn();
      if (dnState.columns.map(c => c.key).join(",") !== "item,description") {
        throw new Error("new delivery note inherited columns from a saved delivery note");
      }
      if (dnState.autofitRows !== false) {
        throw new Error("new delivery note inherited Auto-fit row height from a saved delivery note");
      }
    })()
  `);
}

async function testEditorPayloadsPersistAutofitLayout() {
  await runFrontendFile("static/quote.js", `
    (async () => {
      quoteState.quoteNo = "QUT-1";
      quoteState.date = "4/7/2026";
      quoteState.type = "materials";
      quoteState.mode = "edit";
      quoteState.autofitRows = true;
      quoteState.terms = [];
      quoteState.schema = { primary_key: "item", columns: [{ key: "item", head: "Item", kind: "text" }] };
      quoteState.columns = [{ key: "item", head: "Item", kind: "text" }];
      quoteState.rows = [{ item: "Pipe" }];
      document.getElementById("qClient").value = "Client";
      document.getElementById("qRfq").value = "N/A";
      document.getElementById("qAddress").value = "Site";

      const payload = buildQuotePayload();
      if (!payload.layout || payload.layout.autofit_rows !== true) {
        throw new Error("quotation payload did not persist Auto-fit row height");
      }
    })()
  `);

  await runFrontendFile("static/dn.js", `
    (async () => {
      dnState.dnNo = "DN-1";
      dnState.date = "4/7/2026";
      dnState.mode = "edit";
      dnState.autofitRows = false;
      dnState.baseColumns = [{ key: "item", head: "Item", kind: "text" }];
      dnState.columns = [{ key: "item", head: "Item", kind: "text" }];
      dnState.priceColumns = [];
      dnState.rows = [{ item: "Pipe" }];
      document.getElementById("dnClient").value = "Client";
      document.getElementById("dnLpo").value = "N/A";
      document.getElementById("dnAddress").value = "Site";

      const payload = buildDnPayload();
      if (!payload.layout || payload.layout.autofit_rows !== false) {
        throw new Error("delivery note payload did not persist explicit Auto-fit row height false");
      }
    })()
  `);

  await runFrontendFile("static/inv.js", `
    (async () => {
      invState.invNo = "INV-1";
      invState.date = "4/7/2026";
      invState.type = "materials";
      invState.mode = "edit";
      invState.autofitRows = true;
      invState.bankInfo = [{ key: "main" }];
      invState.schema = { primary_key: "item", columns: [{ key: "item", head: "Item", kind: "text" }] };
      invState.columns = [{ key: "item", head: "Item", kind: "text" }];
      invState.rows = [{ item: "Pipe" }];
      document.getElementById("invClient").value = "Client";
      document.getElementById("invLpo").value = "N/A";
      document.getElementById("invAddress").value = "Site";

      const payload = buildInvPayload();
      if (!payload.layout || payload.layout.autofit_rows !== true) {
        throw new Error("invoice payload did not persist Auto-fit row height");
      }
    })()
  `);
}

async function testInvoicePayloadPersistsLiveColumnWidths() {
  await runFrontendFile("static/inv.js", `
    (async () => {
      invState.invNo = "INV-1";
      invState.date = "4/7/2026";
      invState.type = "equipment";
      invState.mode = "edit";
      invState.bankInfo = [{ key: "main" }];
      invState.schema = {
        primary_key: "item",
        columns: [
          { key: "item", head: "Item", kind: "text", width: 20 },
          { key: "monthly_rate", head: "Monthly Rate", kind: "money", width: 14 }
        ]
      };
      invState.columns = [
        { key: "item", head: "Item", kind: "text", width: 20 },
        { key: "monthly_rate", head: "Monthly Rate", kind: "money", width: 14 }
      ];
      invState.rows = [{ item: "Generator", monthly_rate: "500" }];
      document.getElementById("invClient").value = "Client";
      document.getElementById("invLpo").value = "N/A";
      document.getElementById("invAddress").value = "Site";
      window.readEditorColWidths = () => ({ item: 34.5, monthly_rate: 23.25 });

      const payload = buildInvPayload();
      if (payload.columns[0].width !== 34.5 || payload.columns[1].width !== 23.25) {
        throw new Error("invoice save payload did not persist live resized column widths");
      }
      if (payload.col_widths.item !== 34.5 || payload.col_widths.monthly_rate !== 23.25) {
        throw new Error("invoice preview payload did not include live resized column widths");
      }
    })()
  `);
}

async function testDeliveryPayloadPersistsLiveBaseColumnWidths() {
  await runFrontendFile("static/dn.js", `
    (async () => {
      dnState.dnNo = "DN-1";
      dnState.date = "4/7/2026";
      dnState.mode = "edit";
      dnState.baseColumns = [
        { key: "item", head: "Item", kind: "text", width: 20 },
        { key: "remarks", head: "Remarks", kind: "text", width: 14 }
      ];
      dnState.columns = [
        { key: "item", head: "Item", kind: "text", width: 20 },
        { key: "remarks", head: "Remarks", kind: "text", width: 14 }
      ];
      dnState.priceColumns = [];
      dnState.rows = [{ item: "Pipe", remarks: "Deliver morning" }];
      document.getElementById("dnClient").value = "Client";
      document.getElementById("dnLpo").value = "N/A";
      document.getElementById("dnAddress").value = "Site";
      window.readEditorColWidths = () => ({ item: 36, remarks: 24 });

      const payload = buildDnPayload();
      if (payload.columns[0].width !== 36 || payload.columns[1].width !== 24) {
        throw new Error("delivery note save payload did not persist live resized base column widths");
      }
      if (payload.col_widths.item !== 36 || payload.col_widths.remarks !== 24) {
        throw new Error("delivery note preview payload did not include live resized column widths");
      }
    })()
  `);
}

(async () => {
  await testQuoteDoesNotRestoreCleanFinishedColumns();
  await testQuoteStillRestoresDirtyDraftColumns();
  await testInvoiceDoesNotRestoreCleanFinishedColumns();
  await testInvoiceStillRestoresDirtyDraftColumns();
  await testInvoiceNewUsesEffectiveDefaultColumns();
  await testDeliveryNewResetsColumns();
  await testDeliveryNewUsesEffectiveDefaultColumns();
  await testQuoteLoadsColumnsPerSavedDocument();
  await testInvoiceLoadsColumnsPerSavedDocument();
  await testDeliveryLoadsColumnsPerSavedDocument();
  await testEditorPayloadsPersistAutofitLayout();
  await testInvoicePayloadPersistsLiveColumnWidths();
  await testDeliveryPayloadPersistsLiveBaseColumnWidths();
  await flush();
  console.log("editor new reset tests passed");
})().catch(err => {
  console.error(err);
  process.exit(1);
});
