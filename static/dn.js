/* Delivery Note editor (English, LTR).
   Reuses shared helpers from app.js: api(), toast(), showModal(),
   withLoading(), esc(), safe(), hideAllViews(), setBrand(), window.ICON,
   window.copyFileToClipboard, and the global `state` (state.today). */

const dnState = {
    dnNo: "-",
    date: "-",
    rows: [],                 // [{item, description, unit, qty, rate, cost}]
    includePrices: false,
    mode: "new",              // "new" | "edit"
    editDnNo: null,
    dirty: false,
    initialized: false,
    numberOverridden: false,
    company: null,
    addressOptions: [],
    sourceQuote: "",          // Phase N.3 - the quote this DN was made from
    /* 2026-09-05 - the PO this DN was made from, and WHICH of its lines, so the
       fulfillment link can be written once this DN has a number. TRANSIENT, in
       exactly the way `invState.sourceDn` is: it must be cleared everywhere a
       different document is loaded into this editor, or a later unrelated save
       marks the wrong PO's items delivered. `{ poNo, indexes }` or null. */
    sourcePo: null,
    layoutDefault: {},
    autofitRows: false,
    costCurrency: "USD",      // cost-entry currency (USD/IQD)
};

window.dnState = dnState;       // expose for app.js beforeunload check

// DN sets dnState.dirty in many places and never had an "Unsaved" pill before.
// Route the flag through an accessor so every assignment reflects onto the
// shared topbar status cluster automatically (no need to patch each call site).
let _dnDirty = false;
Object.defineProperty(dnState, "dirty", {
    get() { return _dnDirty; },
    set(v) {
        _dnDirty = !!v;
        refreshDnSaved();
    },
    configurable: true,
    enumerable: true,
});

function refreshDnSaved() {
    if (window.setUnsavedChip) window.setUnsavedChip(dnState.dirty);
    if (window.setEditorSavedState) {
        window.setEditorSavedState(dnState.mode === "edit" && !dnState.dirty);
    }
    if (window.refreshDocumentWorkflowButtons) window.refreshDocumentWorkflowButtons();
}
window.refreshDnSaved = refreshDnSaved;
window.getDnWorkflowContext = function () {
    return dnState.mode === "edit" && dnState.editDnNo
        ? { kind: "dn", no: dnState.editDnNo }
        : null;
};

function bindDnWorkflowButton() {
    const btn = document.getElementById("btnDnWorkflow");
    if (!btn || btn._workflowWired) return;
    btn._workflowWired = true;
    btn.onclick = () => {
        const ctx = window.getDnWorkflowContext();
        if (!ctx) { toast("Save this Delivery Note before opening Workflow", true); return; }
        if (window.openDocumentWorkflow) window.openDocumentWorkflow(ctx.kind, ctx.no);
    };
}

// ============================================================
// Signed copy (2026-07-24) - the counter-signed paper the CLIENT scans back.
// Distinct from the Sign button, which applies OUR digital signature.
// The dialog itself moved to static/signed_copy.js on 2026-08-27 when the
// invoice grew the same feature; only this DN's button and state live here.
// ============================================================
function _dnSignedCopyBtn() { return document.getElementById("btnDnSignedCopy"); }

function refreshDnSignedCopyButton() {
    if (!window.SignedCopy) return;
    window.SignedCopy.paintButton(
        _dnSignedCopyBtn(), "dn",
        dnState.mode === "edit" && !!dnState.editDnNo,
        !!dnState.hasSignedCopy);
}
window.refreshDnSignedCopyButton = refreshDnSignedCopyButton;

async function reloadDnSignedCopy() {
    const rec = window.SignedCopy
        ? await window.SignedCopy.load("dn", dnState.editDnNo)
        : null;
    dnState.hasSignedCopy = !!rec;
    refreshDnSignedCopyButton();
    return rec;
}

async function openDnSignedCopy() {
    const dnNo = dnState.editDnNo;
    if (!dnNo) { toast("Save this Delivery Note first", true); return; }
    return window.SignedCopy.open("dn", dnNo, {
        onChange: (rec) => {
            dnState.hasSignedCopy = !!rec;
            refreshDnSignedCopyButton();
        },
    });
}
window.openDnSignedCopy = openDnSignedCopy;

const DMONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

function dnLayoutAutofit(layout, fallback = false) {
    if (window.layoutAutofitValue) return window.layoutAutofitValue(layout, fallback);
    if (layout && typeof layout === "object" &&
        Object.prototype.hasOwnProperty.call(layout, "autofit_rows")) {
        return !!layout.autofit_rows;
    }
    return !!fallback;
}

function dnDefaultAutofit() {
    return dnLayoutAutofit(dnState.layoutDefault, false);
}

function dnLayoutPayload() {
    return window.editorLayoutPayload
        ? window.editorLayoutPayload(dnState.autofitRows)
        : { autofit_rows: !!dnState.autofitRows };
}

function dnSyncAutofitButton() {
    if (window.syncAutofitButton) {
        window.syncAutofitButton("btnDnAutofit", dnState.autofitRows);
    }
}
window.dnSyncAutofitButton = dnSyncAutofitButton;

function dnSyncPricesToggle() {
    const active = !!dnState.includePrices;
    const toggle = document.getElementById("dnIncludePrices");
    if (toggle) toggle.checked = active;
    const btn = document.getElementById("btnDnPrices");
    if (btn) {
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
        btn.title = active ? "Hide prices on this delivery note" : "Show prices on this delivery note";
    }
    const label = document.getElementById("dnPricesLabel");
    if (label) label.textContent = active ? "Prices on" : "Prices";
    const grand = document.getElementById("dnGrandWrap");
    if (grand) grand.hidden = !active;
}
window.dnSyncPricesToggle = dnSyncPricesToggle;

// Fallback columns - used only until initDnModule() pulls the live shape
// from get_dn_defaults(). Once initialized, dnState.baseColumns +
// dnState.priceColumns are the source of truth (and can be customised via
// the DN settings modal).
const DN_COLS_BASE_FALLBACK = [
    { key: "item",        head: "Item",        kind: "text"     },
    { key: "description", head: "Description", kind: "text"     },
    { key: "unit",        head: "Unit/Size",   kind: "text"     },
    { key: "qty",         head: "Q'ty",        kind: "num"      },
];
const DN_COLS_PRICE_FALLBACK = [
    { key: "rate",  head: "Rate",  kind: "money"    },
    { key: "total", head: "Total", kind: "computed" },
];

// Phase T - per-document base columns. dnState.columns is THIS delivery note's
// own base column layout (renamed/added columns). The Rate/Total price columns
// are appended automatically when "Show prices" is on (they're not part of the
// per-doc layout). New DNs seed from the effective DN defaults.
function _dnCloneCols(cols) { return (cols || []).map(c => ({ ...c })); }
function _dnStockBase() {
    // New DNs should start from the effective DN settings. The stock base
    // stays available as the built-in reference, not the editor seed.
    return (dnState.baseColumns && dnState.baseColumns.length)
        ? dnState.baseColumns
        : ((dnState.stockBaseColumns && dnState.stockBaseColumns.length) ? dnState.stockBaseColumns : DN_COLS_BASE_FALLBACK);
}
function _dnBaseColumns() {
    if (!dnState.columns || !dnState.columns.length) {
        dnState.columns = _dnCloneCols(
            (dnState.baseColumns && dnState.baseColumns.length) ? dnState.baseColumns : DN_COLS_BASE_FALLBACK);
    }
    return dnState.columns;
}
function _dnPriceColumns() {
    return (dnState.priceColumns && dnState.priceColumns.length) ? dnState.priceColumns : DN_COLS_PRICE_FALLBACK;
}
function dnColumns() {
    const base  = _dnBaseColumns();
    const price = _dnPriceColumns();
    return dnState.includePrices ? [...base, ...price] : [...base];
}

// Inline private COST column - ALWAYS shown (2026-07-24; was a toolbar toggle).
// Bound to each row's hidden `cost` field - the SAME one the Costing screen uses
// - so it is not a schema column and never reaches the PDF generator. Always the
// LAST column, consistently across all editors.
function dnRenderColumns() {
    const cols = dnColumns();
    cols.push({ key: "cost", head: "COST", kind: "money", width: 10, _synthetic: true });
    // PROFIT only exists where there is a sell side - a DN issued WITHOUT
    // prices has none, and a profit column there would read as a 100% loss on
    // every line (2026-08-25). Read-only and computed; never a row key.
    if (dnState.includePrices) {
        cols.push({ key: "profit", head: "PROFIT", kind: "profit", width: 10, _synthetic: true, _readonly: true });
    }
    return cols;
}

// USD/IQD cost-entry switch in the COST header - reuses the quote/Costing markup
// + styles. Converts already-typed cost cells so the real cost is preserved.
function dnCurrencySwitchHTML(cur) {
    const usd = cur === "USD";
    return `<div class="cost-cur-switch ${usd ? "is-usd" : "is-iqd"}" id="dnCostCurSwitch"
                 role="switch" aria-checked="${usd}" tabindex="0" aria-label="Cost entry currency"
                 title="Cost entry currency - click to switch. Profit stays in USD.">
        <span class="ccs-opt ccs-iqd-opt">IQD</span>
        <span class="ccs-opt ccs-usd-opt">$</span>
        <span class="ccs-knob" aria-hidden="true"></span>
    </div>`;
}
function wireDnCurrencySwitch() {
    const sw = document.getElementById("dnCostCurSwitch");
    if (!sw) return;
    const toggle = () => setDnCostCurrency((dnState.costCurrency || "USD") === "USD" ? "IQD" : "USD");
    sw.addEventListener("click", toggle);
    sw.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
}
/* This DN's IQD peg, cached for the live profit cards (see quote.js). */
let _dnPegCache = null;
async function dnDocPeg() {
    let iqd = window.IQD_PER_USD || 1400;
    const no = dnState.editDnNo || dnState.dnNo;
    if (no && no !== "-") {
        try { const r = await api().doc_iqd_per_usd("dn", no); if (Number(r)) iqd = Number(r); }
        catch (e) { /* fall back to the profile peg */ }
    }
    _dnPegCache = iqd;
    return iqd;
}
function dnPegSync() { return _dnPegCache || window.IQD_PER_USD || 1400; }

async function setDnCostCurrency(next) {
    const cur = dnState.costCurrency || "USD";
    if (next === cur) return;
    let _iqd = window.IQD_PER_USD || 1400;
    const _no = dnState.editDnNo || dnState.dnNo;
    if (_no && _no !== "-") {
        try { const r = await api().doc_iqd_per_usd("dn", _no); if (Number(r)) _iqd = Number(r); }
        catch (e) { /* fall back to the profile peg */ }
    }
    /* Per row, FROM ITS OWN currency - a line already priced in dinars inside
       a dollar DN must not be multiplied again when the document flips. */
    window.convertRowsToCostCurrency(dnState.rows, "dnRowsBody", next, _iqd, cur);
    dnState.costCurrency = next;
    dnState.dirty = true;
    const sw = document.getElementById("dnCostCurSwitch");
    if (sw) {
        const usd = next === "USD";
        sw.classList.toggle("is-usd", usd);
        sw.classList.toggle("is-iqd", !usd);
        sw.setAttribute("aria-checked", usd ? "true" : "false");
    }
}
/* Flip ONE DN line between $ and IQD. The real cost is unchanged - only the
   unit it is written in. Shared maths: window.toggleRowCostCurrency (app.js). */
function toggleDnRowCostCurrency(i) {
    const row = dnState.rows[i];
    if (!row) return;
    const to = window.toggleRowCostCurrency(row, dnState.costCurrency, dnPegSync());
    dnState.dirty = true;
    const tr = document.querySelector(`#dnRowsBody tr[data-i="${i}"]`);
    if (tr) {
        const inp = tr.querySelector('input[data-k="cost"]');
        if (inp) inp.value = row.cost != null ? row.cost : "";
        window.paintRowCostChip(tr, to);
    }
    updateDnTotals();
}

function dnSettingsColumnsSnapshot() {
    const heads = {};
    document.querySelectorAll("#dnThead .qc-head-input").forEach(inp => {
        if (inp.dataset && inp.dataset.colKey) heads[inp.dataset.colKey] = inp.value;
    });
    const thead = document.getElementById("dnThead");
    const widths = (window.readEditorColWidths && thead)
        ? (window.readEditorColWidths(thead) || {}) : {};
    return _dnBaseColumns().map(c => {
        const out = { ...c };
        if (Object.prototype.hasOwnProperty.call(heads, c.key)) out.head = heads[c.key];
        const w = widths[c.key];
        if (typeof w === "number" && w > 0) out.width = w;
        return out;
    });
}
window.dnSettingsColumnsSnapshot = dnSettingsColumnsSnapshot;
function _nextDnColKey() {
    const taken = new Set((dnState.columns || []).map(c => c.key));
    let n = 2;
    while (taken.has(`col_${n}`)) n++;
    return `col_${n}`;
}
function insertDnColAfter(afterKey) {
    const cols = _dnBaseColumns();
    let at = cols.length;
    if (afterKey) {
        const idx = cols.findIndex(c => c.key === afterKey);
        if (idx >= 0) at = idx + 1;
    }
    const key = _nextDnColKey();
    cols.splice(at, 0, { key, head: "New column", kind: "text", width: 12 });
    dnState.dirty = true;
    buildDnThead();
    fullRenderDnRows();
    const inp = document.querySelector(`#dnThead .qc-head-input[data-col-key="${key}"]`);
    if (inp) { inp.focus(); inp.select(); }
}
window.insertDnColAfter = insertDnColAfter;
function deleteDnCol(key) {
    const cols = _dnBaseColumns();
    const idx = cols.findIndex(c => c.key === key);
    if (idx < 0) return;
    if (key === "item") { toast("Can't remove the main column", true); return; }
    if (cols.length <= 1) { toast("Keep at least one column", true); return; }
    cols.splice(idx, 1);
    dnState.dirty = true;
    buildDnThead();
    fullRenderDnRows();
}
window.deleteDnCol = deleteDnCol;

// ============================================================
// Module init (once, at bootstrap)
// ============================================================
async function initDnModule() {
    bindDnWorkflowButton();
    let defaults = { address_options: [], company: null, base_columns: [], price_columns: [] };
    try { defaults = await api().get_dn_defaults(); } catch (e) { /* keep empties */ }
    dnState.addressOptions = defaults.address_options || [];
    dnState.company        = defaults.company || null;
    dnState.baseColumns    = defaults.base_columns  || [];
    dnState.priceColumns   = defaults.price_columns || [];
    dnState.stockBaseColumns = defaults.stock_base_columns || defaults.base_columns || [];   // Phase T
    dnState.layoutDefault  = defaults.layout || {};
    dnState.autofitRows    = dnDefaultAutofit();

    document.getElementById("dnAddrList").innerHTML =
        dnState.addressOptions.map(a => `<option value="${esc(a)}">`).join("");

    const sel = document.getElementById("dnOpenMonth");
    sel.innerHTML = DMONTHS.map((n, i) => `<option value="${i + 1}">${n}</option>`).join("");
    if (state.today) {
        sel.value = state.today.month;
        document.getElementById("dnOpenYear").value = state.today.year;
    }

    document.getElementById("btnDnNew").onclick         = safe(dnNew,        "New DN");
    { const b = document.getElementById("btnDnOpen"); if (b) b.onclick = safe(dnOpen, "Open DN"); }
    document.getElementById("btnDnImport").onclick      = safe(openImportFromQuoteDialog, "Import from QUT");
    document.getElementById("btnDnInvoice").onclick     = safe(createInvoiceFromDN, "Make Invoice");
    const btnDnMasterFill = document.getElementById("btnDnMasterFill");
    if (btnDnMasterFill) btnDnMasterFill.onclick = safe(() => window.openMasterDataFill({
        docType: "dn",
        rows: dnState.rows,
        primaryKey: "item",
        costCurrency: dnState.costCurrency || "USD",
        costPeg: dnPegSync(),
        sellCurrency: "USD",
        render: () => fullRenderDnRows(),
        markDirty: () => { dnState.dirty = true; },
    }), "Fill from Master Data");
    document.getElementById("btnDnPreview").onclick     = safe(dnPreview,    "Preview DN");
    { const b = document.getElementById("btnDnPrint"); if (b) b.onclick = safe(dnPrint, "Print DN"); }
    { const b = _dnSignedCopyBtn(); if (b) b.onclick = safe(openDnSignedCopy, "Signed copy"); }
    const btnDnStay = document.getElementById("btnDnStay");
    if (btnDnStay) btnDnStay.onclick                    = safe(() => dnSave({ stay: true,  skipConfirm: true }), "Save DN");
    const btnDnAutofit = document.getElementById("btnDnAutofit");
    if (btnDnAutofit) btnDnAutofit.onclick = safe(() => {
        dnState.autofitRows = !dnState.autofitRows;
        dnState.dirty = true;
        dnSyncAutofitButton();
    }, "Auto-fit rows");
    if (window.wireFitColsButton) window.wireFitColsButton("btnDnFitCols", "#dnThead", {
        pdf: {
            widthMm: 186,
            headerFont: 'bold 10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
            bodyFont: '10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
        },
        money: (key) => (dnRenderColumns() || [])
            .some(c => c.key === key && (c.kind === "money" || c.kind === "computed")),
        onFit: () => { dnState.dirty = true; },
    });
    dnSyncAutofitButton();
    document.getElementById("btnDnAddRow").onclick      = safe(() => {
        const last = dnState.rows[dnState.rows.length - 1];
        if (!last || hasDnContent(last)) addDnRow();
        focusDnFirstCol(dnState.rows.length - 1);
    }, "Add row");
    document.getElementById("btnDnRefreshList").onclick = safe(refreshDnList, "Refresh");
    document.getElementById("btnDnCloseOpen").onclick   = safe(enterDnEditor, "Back");

    // Unsaved-changes guard for the unified Home button (the old in-group
    // btnDnBack was removed). Returns true if it's safe to leave home.
    window.dnConfirmLeave = async function () {
        return window.unsavedGuard.confirmLeave("dn");
    };

    // ⚙ in the DN action group - opens the per-section settings modal.
    const dSetBtn = document.getElementById("btnDnSettings");
    if (dSetBtn) dSetBtn.onclick = safe(() => {
        if (!window.openDocSettings) { toast("Settings module not loaded", true); return; }
        const activeColumns = dnSettingsColumnsSnapshot();
        dnState.columns = _dnCloneCols(activeColumns);
        window.openDocSettings("delivery", { columns: activeColumns, onSaved: async (meta = {}) => {
            // Refresh address list + columns so the DN editor picks up edits.
            try {
                const d = await api().get_dn_defaults();
                dnState.addressOptions = d.address_options || [];
                dnState.company        = d.company || null;
                dnState.baseColumns    = d.base_columns  || dnState.baseColumns;
                dnState.priceColumns   = d.price_columns || dnState.priceColumns;
                dnState.layoutDefault  = d.layout || dnState.layoutDefault;
                if (meta.columnsChanged && d.base_columns && d.base_columns.length) {
                    const before = JSON.stringify(dnState.columns || []);
                    const nextColumns = _dnCloneCols(d.base_columns || []);
                    dnState.columns = nextColumns;
                    if (before !== JSON.stringify(nextColumns)) dnState.dirty = true;
                }
                document.getElementById("dnAddrList").innerHTML =
                    dnState.addressOptions.map(a => `<option value="${esc(a)}">`).join("");
                // Rebuild thead + re-render rows so the new column shape takes effect immediately.
                buildDnThead();
                fullRenderDnRows();
            } catch {}
        }});
    }, "DN settings");

    document.getElementById("dnClient").addEventListener("change", safe(refreshDnNumber, "DN number"));
    document.getElementById("dnReference").addEventListener("input", () => {
        dnState.dirty = true;
    });

    // Price toggle: re-renders columns + footer total visibility.
    const toggle = document.getElementById("dnIncludePrices");
    const applyPriceToggle = (checked) => {
        dnState.includePrices = !!checked;
        dnSyncPricesToggle();
        buildDnThead();
        fullRenderDnRows();
        updateDnTotals();
        dnState.dirty = true;
    };
    if (toggle) toggle.addEventListener("change", () => applyPriceToggle(toggle.checked));
    const btnDnPrices = document.getElementById("btnDnPrices");
    if (btnDnPrices) btnDnPrices.onclick = safe(() => applyPriceToggle(!dnState.includePrices), "Show prices");
    dnSyncPricesToggle();

    // LPO live warning - show a soft warning if LPO is empty or N/A.
    document.getElementById("dnLpo").addEventListener("input", updateLpoWarning);
    // Pick the LPO from the ones this client actually sent (2026-09-11).
    // The number still prints on the document; what changes is that it can
    // no longer be a number nobody ever received - which is why 63 of 64
    // delivery notes said "N/A" and the LPO screen never lit up.
    const dnLpoPick = document.getElementById("dnLpoPick");
    if (dnLpoPick) dnLpoPick.onclick = async () => {
        if (!window.LpoPicker) return;
        const client = (document.getElementById("dnClient") || {}).value || "";
        const picked = await window.LpoPicker.pick("dnLpo", client.trim());
        updateLpoWarning();
        /* Picking the PO brings what the PO knows (2026-09-28). This used to
           stop at the number, so an LPO carrying its ordered lines, the
           quotation it answers and every line's cost filled one text box. */
        if (picked == null || window.LpoPicker.isNoLpo(picked)) return;
        await adoptLpoIntoDn(client.trim(), picked, { confirm: true });
    };
    document.getElementById("dnLpo").addEventListener("change", updateLpoWarning);

    window.enterDnEditor = enterDnEditor;
    window.dnNew     = dnNew;
    window.dnOpen    = dnOpen;
    window.dnPreview = dnPreview;
    window.dnSave    = dnSave;
}
window.initDnModule = initDnModule;

// ============================================================
// Navigation
// ============================================================
function enterDnEditor() {
    hideAllViews();
    document.getElementById("dnView").hidden = false;
    document.getElementById("dnActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Delivery Note");
    if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
    dnSyncAutofitButton();
    dnSyncPricesToggle();
    buildDnThead();
    if (!dnState.initialized) initNewDn();
}
window.enterDnEditor = enterDnEditor;

// ============================================================
// Thead (depends on includePrices)
// ============================================================
function buildDnThead() {
    // Phase L.4.1 - propagate per-column widths into the editor.
    let html = `<tr><th class="qc-no">NO.</th>`;
    // Phase T - base columns are editable (rename / insert / remove); the
    // Rate/Total price columns (when shown) stay fixed.
    _dnBaseColumns().forEach(c => {
        const w = c.width ? ` style="width: ${c.width}%"` : "";
        const delBtn = (c.key === "item") ? ""
            : `<button type="button" class="qc-head-del" data-col-key="${esc(c.key)}" title="Remove this column" tabindex="-1">×</button>`;
        html += `<th class="al-center qc-head-th" data-col-key="${esc(c.key)}"${w}>
            <input class="qc-head-input" data-col-key="${esc(c.key)}" value="${esc(c.head)}" spellcheck="false" autocomplete="off" aria-label="Column header">
            ${delBtn}
            <button type="button" class="qc-head-add" data-after="${esc(c.key)}" title="Insert a column after this one" tabindex="-1">+</button>
        </th>`;
    });
    if (dnState.includePrices) {
        _dnPriceColumns().forEach(c => {
            const w = c.width ? ` style="width: ${c.width}%"` : "";
            html += `<th class="al-center" data-col-key="${esc(c.key)}"${w}>${esc(c.head)}</th>`;
        });
    }
    html += `<th class="al-center qc-cost-col qc-private" data-col-key="cost" style="width: 10%">
        <div class="qcc-head">COST</div>
    </th>`;
    // The PROFIT half of the private strip - only when this DN carries prices.
    if (dnState.includePrices) {
        html += `<th class="al-center qc-profit-col qc-private" data-col-key="profit" style="width: 10%">
            <div class="qcc-head">PROFIT</div>
        </th>`;
    }
    html += `<th class="qc-del"></th></tr>`;
    const dnThead = document.getElementById("dnThead");
    dnThead.innerHTML = html;
    dnThead.querySelectorAll(".qc-head-input").forEach(inp => {
        inp.addEventListener("input", () => {
            const col = (dnState.columns || []).find(c => c.key === inp.dataset.colKey);
            if (col) { col.head = inp.value; dnState.dirty = true; }
        });
        inp.addEventListener("mousedown", (e) => e.stopPropagation());
    });
    dnThead.querySelectorAll(".qc-head-add").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); insertDnColAfter(b.dataset.after); }));
    dnThead.querySelectorAll(".qc-head-del").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); deleteDnCol(b.dataset.colKey); }));
    // Re-attach the Excel-style column-resize handles synchronously.
    if (window.refreshColResizers) window.refreshColResizers();
    wireDnCurrencySwitch();   // wire the COST-header USD/IQD switch (when shown)
}

// ============================================================
// New / number
// ============================================================
async function initNewDn() {
    dnState.mode = "new";
    dnState.editDnNo = null;
    dnState.dirty = false;
    dnState.numberOverridden = false;
    dnState.hasSignedCopy = false;      // fresh DN can't have a client copy yet
    refreshDnSignedCopyButton();
    dnState.rows = [];
    dnState.columns = _dnCloneCols(_dnStockBase());   // Phase T - fresh = effective defaults
    dnState.sourceQuote = "";          // Phase N.3 - fresh DN has no deal link
    dnState.sourcePo = null;           // ...and no PO to mark delivered
    // Honour the "New Delivery Notes start with prices" preference (Settings → Behaviour).
    const startWithPrices = !!(window.prefBool && window.prefBool("dn_prices_default", false));
    dnState.includePrices = startWithPrices;
    dnState.costCurrency = "USD";
    dnState.extraCosts = [];                // document-level costs (Costing screen)
    _dnPegCache = null;
    dnState.signingOverlay = null;
    dnState.autofitRows = dnDefaultAutofit();
    dnRefreshSignChip();
    dnSyncAutofitButton();
    dnSyncPricesToggle();
    buildDnThead();   // rebuild header so columns match the prices state
    dnState.date = state.today ? state.today.display : "";
    if (window.clearPendingDoc) window.clearPendingDoc();   // a New doc replaces any unsaved duplicate
    // Phase X.26 - fresh doc: drop the panel highlight and put the list on
    // today's month (shared with the other three editors, see app.js).
    if (window.panelNewDocument) window.panelNewDocument("dn");
    else window.__currentEditorDoc = null;

    document.getElementById("dnClient").value = "";
    document.getElementById("dnLpo").value = "N/A";
    document.getElementById("dnAddress").value = dnState.addressOptions[0] || "";
    document.getElementById("dnReference").value = "";
    bindDocDate("dnDate", dnState);
    window.setModeChip(false);

    // Phase N.6 - Linked Quotation field (dnState.sourceQuote already "" here).
    wireDnSrcQuote();
    { const sq = document.getElementById("dnSrcQuote"); if (sq) sq.value = ""; }
    await populateDnQuoteList("");

    await populateDnClientList();
    await refreshDnNumber();

    document.getElementById("dnRowsBody").innerHTML = "";
    addDnRow();
    dnState.initialized = true;
    updateDnTotals();
    updateLpoWarning();
    // See initNewQuote - a new document forgets the last one's client settings.
    if (window.ClientDefaults) window.ClientDefaults.reset("dn");
}

async function populateDnClientList() {
    let list = [];
    try { list = await api().suggest_clients(""); } catch (e) { list = []; }
    document.getElementById("dnClientList").innerHTML =
        (list || []).map(c => `<option value="${esc(c.name)}">`).join("");
}

// ============================================================
// Phase N.6 - Linked Quotation field (deal link).
// source_quote_no is the single source of truth; this field reads/writes
// dnState.sourceQuote, which already flows into buildDnPayload + save.
// ============================================================
async function populateDnQuoteList(client) {
    const dl = document.getElementById("dnQuoteList");
    if (!dl) return;
    let nums = [];
    try { nums = await api().list_quote_numbers(client || ""); } catch (e) { nums = []; }
    dl.innerHTML = (nums || []).map(n => `<option value="${esc(n)}">`).join("");
}

function wireDnSrcQuote() {
    const inp = document.getElementById("dnSrcQuote");
    if (inp && !inp._wired) {
        const onEdit = () => { dnState.sourceQuote = inp.value.trim(); dnState.dirty = true; };
        inp.addEventListener("input",  onEdit);
        inp.addEventListener("change", onEdit);
        inp._wired = true;
    }
    const clr = document.getElementById("dnSrcQuoteClear");
    if (clr && !clr._wired) {
        clr.addEventListener("click", () => {
            const el = document.getElementById("dnSrcQuote");
            if (el) el.value = "";
            dnState.sourceQuote = "";
            dnState.dirty = true;
        });
        clr._wired = true;
    }
    // Refresh quote suggestions when the client changes (best-effort).
    const clientEl = document.getElementById("dnClient");
    if (clientEl && !clientEl._quoteListWired) {
        clientEl.addEventListener("change", () => {
            populateDnQuoteList(clientEl.value.trim()).catch(() => {});
        });
        clientEl._quoteListWired = true;
    }
}

async function refreshDnNumber() {
    const client = document.getElementById("dnClient").value.trim();
    const r = await api().get_next_dn_number(client);
    const el = document.getElementById("dnNo");
    if (!dnState.numberOverridden) {
        dnState.dnNo = r.dn_no;
        el.value = r.dn_no;
    }
    if (!el._wired) {
        el.addEventListener("input", () => {
            dnState.dnNo = el.value.trim();
            dnState.numberOverridden = true;
            dnState.dirty = true;
        });
        el._wired = true;
    }
}

// The "do I have unsaved work?" rule lives in static/unsaved_guard.js, one
// copy for all four editors. See that file for why it was collected there.
if (window.unsavedGuard) {
    window.unsavedGuard.register("dn", {
        noun: "delivery note",
        label: "Delivery Note",
        dirty: () => !!dnState.dirty,
        filled: () => filledDnRows(),
        save: (opts) => dnSave(opts),
    });
}

async function dnNew() {
    if (!(await window.unsavedGuard.confirmNew("dn"))) return;
    dnState.initialized = false;
    await initNewDn();
}

// ============================================================
// Rows
// ============================================================
function addDnRow(data = {}) {
    const row = {};
    dnColumns().forEach(c => {
        if (c.kind === "computed") return;
        row[c.key] = data[c.key] != null ? data[c.key] : "";
    });
    dnState.rows.push(row);
    document.getElementById("dnRowsBody").appendChild(buildDnRowTr(dnState.rows.length - 1));
    updateDnTotals();
}

// Insert a blank row at index i (shared hover "+" affordance - per document).
function insertDnRowAt(i) {
    const row = {};
    dnColumns().forEach(c => { if (c.kind !== "computed") row[c.key] = ""; });
    dnState.rows.splice(i, 0, row);
    dnState.dirty = true;
    fullRenderDnRows();
    updateDnTotals();
    const tr = document.querySelector(`#dnRowsBody tr[data-i="${i}"]`);
    const f = tr && tr.querySelector("textarea, input");
    if (f) f.focus();
}
window.insertDnRowAt = insertDnRowAt;

function buildDnRowTr(i) {
    const r = dnState.rows[i];
    const tr = document.createElement("tr");
    tr.dataset.i = i;
    let html = `<td class="qc-no">${i + 1}</td>`;
    dnRenderColumns().forEach(c => {
        if (c.kind === "computed") {
            html += `<td class="qc-total" data-k="${c.key}"></td>`;
        } else if (c.kind === "text") {
            html += `<td><textarea data-k="${c.key}" rows="1" autocomplete="off">${esc(r[c.key] || "")}</textarea></td>`;
        } else if (c._synthetic && c.key === "cost") {
            /* The cost cell carries its OWN currency (2026-08-13), so one table
               can price one line in dollars and the next in dinars and the
               profit maths still adds up. Markup + chip come from app.js so the
               quotation, DN and invoice tables cannot drift. */
            html += window.rowCostCellHTML(r, dnState.costCurrency);
        } else if (c._synthetic && c.key === "profit") {
            // Painted by window.paintRowEconCells from updateDnTotals().
            html += window.rowProfitCellHTML();
        } else {
            const im = 'inputmode="decimal"';
            html += `<td><input data-k="${c.key}" value="${esc(r[c.key] || "")}" ${im} autocomplete="off"></td>`;
        }
    });
    html += `<td class="qc-del"><button class="del-btn" title="delete" aria-label="delete">${window.ICON ? window.ICON.trash : "×"}</button></td>`;
    tr.innerHTML = html;
    wireDnRow(tr);
    return tr;
}

function autoResizeDnTextarea(ta) {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
}

// ============================================================
// Per-DN-item autocomplete on the "item" column
// ============================================================
let dacTarget = null;
let dacItems  = [];
let dacIdx    = -1;

async function onDnItemInput(field, rowIdx) {
    const q = field.value.trim();
    if (!q) { hideDnAc(); return; }
    let suggestions = [];
    try { suggestions = await api().suggest_dn_items(q); } catch (e) { suggestions = []; }
    if (!suggestions || suggestions.length === 0) { hideDnAc(); return; }
    showDnAc(field, rowIdx, suggestions);
}

function showDnAc(field, rowIdx, suggestions) {
    const box = document.getElementById("acBox");
    box.innerHTML = "";
    dacItems  = suggestions;
    dacIdx    = -1;
    dacTarget = { field, rowIdx };
    suggestions.forEach((s, i) => {
        /* Subtitle = the other remembered values. Two keys stay out of it, as
           on the quotation side: `cost_usd` is stored in a different unit from
           the one being typed in, so a bare number would misread as the cost;
           `image_refs` is an OBJECT and String()-ing it printed the literal
           text "[object Object]". Non-strings are skipped wholesale rather
           than special-casing one key. */
        const parts = [];
        let hasImage = false;
        for (const [k, v] of Object.entries(s.data || {})) {
            if (window.ITEM_META_KEYS.has(k)) continue;
            if (k === "image_refs") {
                hasImage = !!(v && typeof v === "object" && Object.keys(v).length);
                continue;
            }
            if (v == null || v === "" || typeof v === "object") continue;
            parts.push(String(v));
        }
        const sub = parts.join(" · ");
        const tag = hasImage
            ? `<span class="ac-img-tag" title="This item has a picture">`
              + `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`
              + `Image</span>`
            : "";
        const div = document.createElement("div");
        div.className = "ac-item";
        div.innerHTML = `<div class="ac-name">${esc(s.name)}${tag}</div>`
                      + (sub ? `<div class="sub">${esc(sub)}</div>` : "");
        div.onmousedown = (e) => { e.preventDefault(); pickDnSuggestion(i); };
        box.appendChild(div);
    });
    const rect = field.getBoundingClientRect();
    box.style.top      = (rect.bottom + window.scrollY) + "px";
    box.style.left     = (rect.left   + window.scrollX) + "px";
    box.style.minWidth = rect.width + "px";
    box.hidden = false;
}

function hideDnAc() {
    const box = document.getElementById("acBox");
    if (box) box.hidden = true;
    dacTarget = null; dacItems = []; dacIdx = -1;
}

function pickDnSuggestion(i) {
    if (!dacTarget) return;
    const s = dacItems[i];
    const ri = dacTarget.rowIdx;
    const row = dnState.rows[ri];

    row.item = s.name;
    if (s.data) {
        for (const k of Object.keys(s.data)) {
            // `cost` is skipped DELIBERATELY, exactly as the invoice and
            // quotation pickers skip it. Entries remembered before 2026-08-13
            // hold a raw cost in an unrecorded currency - 2002 meaning dinars
            // for an item that costs $1.43 - and copying it into a dollar cell
            // reads as $2,002. The cost comes from `cost_usd` below, or not at
            // all.
            if (k === "cost" || k === "cost_ccy") continue;
            if (window.ITEM_META_KEYS.has(k)) continue;       // not a column; applied below
            if (k === "image_refs") continue;     // a reference, not a value
            if (s.data[k] !== undefined && s.data[k] !== null && s.data[k] !== "") {
                row[k] = s.data[k];
            }
        }
        /* A delivery note also sells in USD. Apply the ONE Master Data price
           to its Rate cell explicitly; the generic loop skips the canonical
           storage keys so they never become phantom document fields. */
        const pr = window.priceFromMemory(s.data, "USD", dnPegSync());
        if (pr !== null) row.rate = pr;
        /* The cost is filled in the CURRENCY IT WAS SAVED IN - dinars stay
           dinars, and the cell's own $/IQD toggle shows it (2026-08-20). No
           conversion. Older entries with no recorded currency fall back to the
           safe converter, which still refuses a blind raw cost. */
        const cc = window.costCellFromMemory(s.data, dnState.costCurrency, dnPegSync());
        if (cc) { row.cost = cc.value; row.cost_ccy = cc.ccy; }
    }
    const tr = document.querySelector(`#dnRowsBody tr[data-i="${ri}"]`);
    if (tr) {
        /* dnRenderColumns(), NOT dnColumns(): the COST column is synthetic and
           absent from the base list, so a remembered cost was written into
           state and then never painted into the cell - which is what made
           picking an item look like it ignored the cost. */
        dnRenderColumns().forEach(c => {
            if (c.kind === "computed") return;
            const el = tr.querySelector(`[data-k="${c.key}"]`);
            if (el && "value" in el) {
                el.value = row[c.key] || "";
                if (el.tagName === "TEXTAREA") autoResizeDnTextarea(el);
            }
        });
        /* The chip is NOT a `data-k` cell, so the repaint above cannot touch it
           (2026-08-26). A remembered dinar cost therefore landed in the cell
           with the chip still reading "$" - the number said one thing and the
           toggle another, which is the one thing this cell exists to prevent. */
        window.paintRowCostChip(tr, window.rowCostCurrency(row, dnState.costCurrency));
    }
    hideDnAc();
    dnState.dirty = true;
    updateDnTotals();
    if (ri === dnState.rows.length - 1) addDnRow();
}

document.addEventListener("keydown", (e) => {
    const box = document.getElementById("acBox");
    if (!dacTarget || !box || box.hidden) return;
    if (e.key === "ArrowDown")        { e.preventDefault(); dacIdx = Math.min(dacIdx + 1, dacItems.length - 1); refreshDacActive(); }
    else if (e.key === "ArrowUp")     { e.preventDefault(); dacIdx = Math.max(dacIdx - 1, 0); refreshDacActive(); }
    else if (e.key === "Enter" && dacIdx >= 0) { e.preventDefault(); pickDnSuggestion(dacIdx); }
    else if (e.key === "Escape")      { hideDnAc(); }
});
function refreshDacActive() {
    document.querySelectorAll("#acBox .ac-item")
        .forEach((el, i) => el.classList.toggle("active", i === dacIdx));
}

function wireDnRow(tr) {
    const i = +tr.dataset.i;

    // Per-line cost currency - shared toggle (app.js).
    const ccyBtn = tr.querySelector(".cost-ccy");
    if (ccyBtn) ccyBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleDnRowCostCurrency(i);
    });
    tr.querySelectorAll("input, textarea").forEach(field => {
        const k = field.dataset.k;
        if (field.tagName === "TEXTAREA") autoResizeDnTextarea(field);
        field.addEventListener("input", () => {
            dnState.rows[i][k] = field.value;
            dnState.dirty = true;
            if (field.tagName === "TEXTAREA") autoResizeDnTextarea(field);
            if (k === "item") onDnItemInput(field, i);
            if (i === dnState.rows.length - 1 && hasDnContent(dnState.rows[i])) addDnRow();
            updateDnTotals();
        });
        if (k === "item") {
            field.addEventListener("focus", () => {
                if (field.value.trim()) onDnItemInput(field, i);
            });
            field.addEventListener("blur", () => setTimeout(hideDnAc, 150));
        } else {
            // Phase G.14: per-column autocomplete on every non-primary text-ish input.
            const cols = dnColumns();
            const colDef = cols.find(c => c.key === k);
            const kind = colDef && colDef.kind;
            if (kind === "text" || kind === "num" || kind === "money") {
                window.wireUniversalAutocomplete && window.wireUniversalAutocomplete(field, {
                    docType: "dn",
                    subType: "",
                    colKey:  k,
                    isPrimary: false,
                    onPick: (val) => {
                        field.value = val;
                        dnState.rows[i][k] = val;
                        dnState.dirty = true;
                        if (field.tagName === "TEXTAREA") autoResizeDnTextarea(field);
                        updateDnTotals();
                    },
                });
            }
        }
    });
    tr.querySelector(".del-btn").addEventListener("click", () => removeDnRow(+tr.dataset.i));
}

function removeDnRow(i) {
    dnState.rows.splice(i, 1);
    dnState.dirty = true;
    fullRenderDnRows();
    if (dnState.rows.length === 0) addDnRow();
    updateDnTotals();
}

function fullRenderDnRows() {
    const body = document.getElementById("dnRowsBody");
    body.innerHTML = "";
    dnState.rows.forEach((_, i) => body.appendChild(buildDnRowTr(i)));
    updateDnTotals();
}

function focusDnFirstCol(i) {
    const tr = document.querySelector(`#dnRowsBody tr[data-i="${i}"]`);
    if (!tr) return;
    const inp = tr.querySelector("textarea, input");
    if (inp) inp.focus();
}

function hasDnContent(row) {
    for (const c of dnColumns()) {
        if (c.kind === "computed") continue;
        const v = (row[c.key] || "").toString().trim();
        if (v) return true;
    }
    return false;
}
function filledDnRows() { return dnState.rows.filter(hasDnContent); }

function dmoney(v) {
    return "$" + (v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Same logic as inv.js amountInWords - kept local so dn.js doesn't depend on
// inv.js load order.
const D_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
                "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
                "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
const D_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
                "Eighty", "Ninety"];
const D_SCALE = ["", "Thousand", "Million", "Billion"];
function _d3(n) {
    const p = [];
    if (n >= 100) { p.push(D_ONES[Math.floor(n / 100)] + " Hundred"); n %= 100; }
    if (n >= 20)  { p.push(D_TENS[Math.floor(n / 10)]);                n %= 10;  }
    if (n >= 1 && n <= 19) p.push(D_ONES[n]);
    return p.join(" ").trim();
}
function _dInt(n) {
    if (!n) return "No";
    const chunks = []; let scale = 0;
    while (n > 0) {
        const c = n % 1000;
        if (c) { let w = _d3(c); if (D_SCALE[scale]) w += " " + D_SCALE[scale]; chunks.push(w); }
        n = Math.floor(n / 1000); scale++;
    }
    return chunks.reverse().join(" ").trim();
}
function dAmountInWords(amount) {
    amount = Math.round((+amount || 0) * 100) / 100;
    const d = Math.floor(amount);
    const c = Math.round((amount - d) * 100);
    return `${_dInt(d)} ${d === 1 ? "Dollar" : "Dollars"} and ${_dInt(c)} ${c === 1 ? "Cent" : "Cents"}`;
}

/* Live cost / sell / profit / margin - see quote.js renderQuoteEconCards. */
function renderDnEconCards() {
    if (!window.computeDocEcon || !window.renderEconCards) return;
    const host = document.getElementById("dnEconCards");
    // A DN issued WITHOUT prices has no sell side, so profit is meaningless.
    if (!dnState.includePrices) {
        if (host) host.innerHTML = "";
        // ...and clear any per-line marking, or turning prices off leaves a
        // stale loss row on a document that no longer has a sell side. (The
        // PROFIT column itself is gone - dnRenderColumns drops it.)
        if (window.paintRowEconCells) window.paintRowEconCells("dnRowsBody", null);
        return;
    }
    const cur = dnState.costCurrency || "USD";
    const peg = dnPegSync();
    const econ = window.computeDocEcon({
        rows: dnState.rows || [],
        extraCosts: dnState.extraCosts || [],
        costCurrency: cur,
        costPeg: peg,
        sellCurrency: "USD",
        primaryKey: (dnColumns()[0] && dnColumns()[0].key) || "item",
    });
    // Per-line profit / loss in the PROFIT column (2026-08-25).
    if (window.paintRowEconCells) window.paintRowEconCells("dnRowsBody", econ);
    window.renderEconCards(host, econ, {
        onPickRow: (i) => window.focusEditorRow && window.focusEditorRow("dnRowsBody", i),
        note: cur === "IQD"
            ? `Costs entered in IQD, converted at 1 $ = ${peg.toLocaleString("en-US")} IQD${
                  _dnRateAccount()}`
            : "",
    });
}
/* The account that rate belongs to, named beside it (2026-09-21). */
function _dnRateAccount() {
    const client = document.getElementById("dnClient");
    const label = window.rateAccountLabel
        ? window.rateAccountLabel("dn", client ? client.value : "", "",
                                  () => renderDnEconCards())
        : "";
    return label ? ` (${label})` : "";
}

window.renderDnEconCards = renderDnEconCards;

function updateDnTotals() {
    let grand = 0;
    dnState.rows.forEach((r, i) => {
        if (!dnState.includePrices) return;
        const t = (parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0);
        grand += t;
        const tr = document.querySelector(`#dnRowsBody tr[data-i="${i}"]`);
        if (tr) {
            const cell = tr.querySelector(".qc-total");
            if (cell) cell.textContent = (parseFloat(r.qty) || parseFloat(r.rate)) ? dmoney(t) : "";
        }
    });
    if (dnState.includePrices) {
        document.getElementById("dnGrandTotal").textContent = dmoney(grand);
        const wEl = document.getElementById("dnGrandWords");
        if (wEl) wEl.textContent = grand ? ` · ${dAmountInWords(grand)}` : "";
    }
    document.getElementById("dnRowCount").textContent = filledDnRows().length;
    renderDnEconCards();
}

// ============================================================
// LPO warning (soft) - empty / N/A is allowed but flagged.
// ============================================================
function updateLpoWarning() {
    const lpo = (document.getElementById("dnLpo").value || "").trim();
    const warn = document.getElementById("dnLpoWarn");
    if (!lpo || lpo.toUpperCase() === "N/A") {
        warn.innerHTML = `LPO No. is empty or "N/A" - the delivery note will print without an LPO reference. Set a value if the client provided one.`;
        warn.classList.add("show");
    } else {
        warn.classList.remove("show");
    }
}

// ============================================================
// Validation + payload + save/preview
// ============================================================
function buildDnPayload() {
    const liveBaseColumns = dnSettingsColumnsSnapshot();
    dnState.columns = _dnCloneCols(liveBaseColumns);
    const liveColWidths = window.readEditorColWidths
        ? (window.readEditorColWidths(document.getElementById("dnThead")) || {}) : {};
    return {
        dn_no: dnState.dnNo,
        date: dnState.date,
        client_name: document.getElementById("dnClient").value.trim(),
        lpo_no: document.getElementById("dnLpo").value.trim() || "N/A",
        client_address: document.getElementById("dnAddress").value.trim(),
        reference: document.getElementById("dnReference").value.trim(),
        include_prices: dnState.includePrices,
        items: filledDnRows(),
        // Phase T - this DN's own base columns (price columns stay automatic).
        columns: _dnCloneCols(liveBaseColumns),
        is_edit: dnState.mode === "edit",
        // Phase N.3 - deal link (null preserves any existing link on re-save).
        source_quote_no: dnState.sourceQuote || null,
        // The cost-entry currency has to ride WITH the save: `set_cost_currency`
        // runs after it returns, so the backend reading the stored flag would be
        // one save behind and would remember a dinar cost as dollars.
        cost_currency: dnState.costCurrency || "USD",
        cost_iqd_per_usd: dnPegSync(),
        signing_overlay: dnState.signingOverlay || null,
        layout: dnLayoutPayload(),
        // Live editor column widths (honours Excel-style resizing) so the
        // generated/previewed PDF matches the on-screen table. Base-column
        // widths are also persisted inside `columns`, so reopening the DN
        // restores its dragged base layout.
        col_widths: liveColWidths,
    };
}

function dnRefreshSignChip() {
    if (window.setSignChip) {
        window.setSignChip("docSignChip", window.overlaySigned && window.overlaySigned(dnState.signingOverlay));
    }
}
window.dnRefreshSignChip = dnRefreshSignChip;

if (window.signingRegister) {
    window.signingRegister({
        docType: "dn",
        getOverlay: () => dnState.signingOverlay || null,
        setOverlay: (ov) => { dnState.signingOverlay = ov; dnState.dirty = true; dnRefreshSignChip(); },
    });
}

// Bulk paste/import - insert parsed rows through the normal addDnRow path.
function dnBulkInsert(objs, replace) {
    const tb = document.getElementById("dnRowsBody");
    if (replace) {
        dnState.rows = [];
        if (tb) tb.innerHTML = "";
    } else {
        while (dnState.rows.length &&
               !hasDnContent(dnState.rows[dnState.rows.length - 1])) {
            dnState.rows.pop();
            if (tb && tb.lastElementChild) tb.removeChild(tb.lastElementChild);
        }
    }
    (objs || []).forEach(o => addDnRow(o));
    if (!dnState.rows.length ||
        hasDnContent(dnState.rows[dnState.rows.length - 1])) addDnRow();
    dnState.dirty = true;
    updateDnTotals();
}

if (window.bulkPasteRegister) {
    window.bulkPasteRegister({
        docType: "dn",
        buttonId: "btnDnPaste",
        getColumns: () => dnColumns()
            .filter(c => !["computed", "image", "index"].includes(c.kind))
            .map(c => ({ key: c.key, head: c.head, kind: c.kind })),
        primaryKey: () => { const cs = dnColumns(); return cs[0] && cs[0].key; },
        addRows: dnBulkInsert,
    });
}

async function infoModal(title, body) {
    await showModal({ title, body, buttons: [{ label: "OK", value: "ok", cls: "primary" }] });
}

async function validateDn() {
    if (!document.getElementById("dnNo").value.trim()) {
        await infoModal("D.N No. required", "Delivery Note number is empty.");
        return false;
    }
    if (!document.getElementById("dnClient").value.trim()) {
        await infoModal("Client required", "Please select or enter a client name.");
        return false;
    }
    if (filledDnRows().length === 0) {
        await infoModal("No items", "Add at least one item before saving.");
        return false;
    }
    return true;
}

async function dnPreview() {
    if (!(await validateDn())) return;
    const btn = document.getElementById("btnDnPreview");
    await withLoading(btn, async () => {
        const r = await api().preview_dn(buildDnPayload());
        if (!r.ok) { toast("Preview error: " + r.error, true); return; }
        await api().open_path(r.path);
    });
}

async function dnPrint() {
    if (!(await validateDn())) return;
    const btn = null;                      // Print left the toolbar; the busy bar covers it
    await withLoading(btn, async () => {
        const r = await api().preview_dn(buildDnPayload());
        if (!r.ok) { toast("Print error: " + r.error, true); return; }
        const pr = await api().print_file(r.path);
        toast(pr && pr.ok ? "Sent to printer." : (pr && pr.error) || "Print failed", !(pr && pr.ok));
    });
}

async function dnSave(opts = {}) {
    if (!(await validateDn())) return false;

    const typed = (document.getElementById("dnNo").value || "").trim();
    if (!typed) { toast("D.N No. is empty", true); return false; }
    dnState.dnNo = typed;

    const sameAsExisting = (dnState.mode === "edit" && dnState.editDnNo === typed);
    if (!sameAsExisting) {
        const exists = await api().check_dn_exists(typed);
        if (exists.exists) {
            const choice = await showModal({
                title: "DN number exists",
                body: `A delivery note with number ${typed} already exists. Saving will overwrite it. Continue?`,
                buttons: [
                    { label: "Cancel",    value: "cancel", cls: "ghost" },
                    { label: "Overwrite", value: "ok",     cls: "primary" },
                ],
            });
            if (choice !== "ok") return false;
        }
    }

    if (!opts.skipConfirm) {
        const choice = await showModal({
            title: "Save Delivery Note",
            body: `Save ${dnState.dnNo}${dnState.mode === "edit" ? " (edit)" : ""}?`,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Save",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    }

    // btnDnSave was the Export button, removed in the 2026-07-24 toolbar
    // trim, so the old ternary resolved to a missing element on that path.
    const btn = document.getElementById("btnDnStay");
    let r;
    await withLoading(btn, async () => { r = await api().save_dn(buildDnPayload()); });
    /* The client is not on file: the backend refuses rather than inventing one
       (2026-09-11). `offer` adds them on the spot if the user wants, and only
       then do we save again - with a freshly built payload, because adding the
       client also rewrites the client box to the name documents print. */
    if (window.ClientRequired && await window.ClientRequired.offer(r, "dn")) {
        await withLoading(btn, async () => { r = await api().save_dn(buildDnPayload()); });
    }
    /* Someone else saved this number a moment ago (multi-user, 2026-10-22):
       say so and fetch the next one, instead of a bare 'Save error' that
       leaves the stale number in the box. */
    if (r && r.number_taken) { toast(r.error, true); await refreshDnNumber(); return false; }
    if (!r || !r.ok) { toast("Save error: " + (r && r.error || "unknown"), true); return false; }
    dnState.dirty = false;
    /* 2026-09-05 - mark the source PO's lines delivered, now that this DN has a
       number. Keyed on `r.dn_no`, never on #dnNo, which the user can have
       retyped. It must happen HERE: the `!opts.stay` branch below calls
       initNewDn(), which clears sourcePo. */
    if (dnState.sourcePo && window.linkPoAfterSave) {
        if (await window.linkPoAfterSave(dnState.sourcePo, "dn", r.dn_no)) {
            dnState.sourcePo = null;
        }
    }
    // Persist the cost-entry currency (the cost numbers ride in the saved rows).
    try { await api().set_cost_currency("dn", r.dn_no, dnState.costCurrency || "USD"); } catch (e) {}
    if (!opts.stay && r.path) {
        await window.afterExportPdf(r.path, r.dn_no);
    } else {
        toast("Saved: " + r.dn_no);
    }
    if (opts.stay) {
        dnState.mode = "edit";
        dnState.editDnNo = r.dn_no;
        dnState.dnNo = r.dn_no;
        const el = document.getElementById("dnNo");
        if (el) el.value = r.dn_no;
        refreshDnSaved();
        reloadDnSignedCopy();   // now saved ⇒ the Signed copy button can enable
    } else {
        dnState.initialized = false;
        await initNewDn();
    }
    if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect the save in the panel
    return true;
}

// ============================================================
// Open existing delivery notes
// ============================================================
function dnOpen() {
    // Phase M.6 - the right panel replaces the standalone Open view.
    if (window.OpenPanel) window.OpenPanel.focusSearch();
    else refreshDnList();
}
window.dnOpen = dnOpen;

// DN Open filter state (Phase G.9 / G.10). DN has no type chips - clients only.
let dnOpenAll = [];
let dnClientFilter = "all";
function _dnClientOf(d) { return d.client_name || d.client || ""; }

function renderDnFilterChips() {
    const counts = countByKey(dnOpenAll, _dnClientOf);
    const options = [
        { key: "all", label: "All", count: dnOpenAll.length },
        ...uniqueSortedKeys(dnOpenAll, _dnClientOf)
            .map(c => ({ key: c, label: c, count: counts[c] || 0 })),
    ];
    renderChipRow(document.getElementById("dnClientChips"), options, dnClientFilter, key => {
        dnClientFilter = key;
        renderDnList();
    });
}

function renderDnList() {
    const box = document.getElementById("dnList");
    const list = dnOpenAll.filter(d => dnClientFilter === "all" || _dnClientOf(d) === dnClientFilter);
    box.innerHTML = "";
    // The unsaved duplicate, if this pane is its home. Rendered BEFORE the
    // empty-state check (a month whose only document is the duplicate would
    // otherwise return early and show nothing) and OUTSIDE the filters - it
    // is the document you are working on, so a type or client chip must not
    // hide it. The UNFILTERED list is passed on purpose: it is what decides
    // the duplicate has been saved and the provisional row can go.
    const pendingRow = window.pendingRowEl
        ? window.pendingRowEl("dn", +document.getElementById("dnOpenYear").value,
                              +document.getElementById("dnOpenMonth").value, dnOpenAll, "dn_no")
        : null;
    if (!list.length && !pendingRow) {
        const lbl = dnClientFilter === "all" ? "" : ` for ${esc(dnClientFilter)}`;
        box.innerHTML = `<div class="empty-state"><div class="icon">∅</div>
            <div class="title">No delivery notes${lbl} this month</div></div>`;
        return;
    }
    if (pendingRow) box.appendChild(pendingRow);
    let _lastDay = null;
    list.forEach(d => {
        const dk = window.panelDayKey ? window.panelDayKey(d.created_at) : null;
        if (dk !== _lastDay) {
            if (window.panelDaySepEl) box.appendChild(window.panelDaySepEl(d.created_at));
            _lastDay = dk;
        }
        const row = document.createElement("div");
        row.className = "po-row" + (d.editable ? " is-clickable" : "");
        if (d.editable) { row.dataset.edit = d.dn_no; row.title = "Click row to open for editing"; }
        const submitBtn = window.submitBtnHTML ? window.submitBtnHTML("dn", d.dn_no, d.submitted) : "";
        const statusHtml = window.panelStatusHTML ? window.panelStatusHTML("dn", d) : "";
        const dateLabel = d.editable ? (d.updated_at || "").slice(0, 10) : "view only";
        const cardMain = window.panelCardMainHTML
            ? window.panelCardMainHTML({ no: d.dn_no, date: dateLabel, reference: d.reference, statuses: statusHtml })
            : `<div class="panel-row-main"><div class="panel-row-head"><div class="po-num">${esc(d.dn_no)}</div><div class="panel-row-topmeta"><span>${esc(dateLabel)}</span></div></div><div class="panel-row-statuses">${statusHtml}</div></div>`;
        row.innerHTML = `
            ${cardMain}
            <div class="row-actions">
                <button class="ghost" data-act="open" data-path="${esc(d.path)}">${window.ICON ? window.ICON.externalLink : ""}<span>Open PDF</span></button>
                <button class="ghost" data-act="reveal" data-path="${esc(d.path)}" title="Open containing folder">${window.ICON ? window.ICON.folder : ""}<span>Folder</span></button>
                <button class="ghost" data-act="copy" data-path="${esc(d.path)}" data-no="${esc(d.dn_no)}" data-signed="${d.signed ? "1" : "0"}" title="Copy file to clipboard">${window.ICON ? window.ICON.copy : ""}<span>Copy</span></button>
                <button class="ghost" data-act="print" data-path="${esc(d.path)}" title="Print to default printer">${window.ICON ? window.ICON.printer : ""}<span>Print</span></button>
                <button class="ghost danger-text" data-act="delete" data-path="${esc(d.path)}" data-label="${esc(d.dn_no)}" title="Move to RECYCLE">${window.ICON ? window.ICON.trash : ""}<span>Delete</span></button>
                ${d.editable && window.duplicateBtnHTML ? window.duplicateBtnHTML("dn", d.dn_no) : ""}
                ${submitBtn}
            </div>`;
        box.appendChild(row);
    });
    if (window.wireSubmitButtons) window.wireSubmitButtons(box, dnOpenAll, "dn_no");
    box.querySelectorAll("button[data-act='open']").forEach(b => b.onclick   = (e) => { e.stopPropagation(); api().open_path(b.dataset.path); });
    box.querySelectorAll("button[data-act='reveal']").forEach(b => b.onclick = (e) => { e.stopPropagation(); api().reveal_in_folder(b.dataset.path); });
    box.querySelectorAll("button[data-act='copy']").forEach(b => b.onclick   = safe((e) => { e.stopPropagation(); return window.copyDocumentFile("dn", b.dataset.no, b.dataset.path, b.dataset.signed === "1"); }, "Copy file"));
    box.querySelectorAll("button[data-act='print']").forEach(b => b.onclick  = (e) => { e.stopPropagation(); window.printPdfFile(b.dataset.path); });
    box.querySelectorAll("button[data-act='delete']").forEach(b => b.onclick = (e) => { e.stopPropagation(); window.confirmAndDeleteDocument("dn", b.dataset.label, b.dataset.path, b.dataset.label, refreshDnList); });
    if (window.wireDuplicateButtons) window.wireDuplicateButtons(box);
    box.querySelectorAll(".po-row.is-clickable").forEach(row => {
        row.onclick = safe(() => loadDnForEdit(row.dataset.edit), "Load DN");
    });
}

async function refreshDnList() {
    const box = document.getElementById("dnList");
    box.innerHTML = `<div class="empty-state"><div class="title">Loading…</div></div>`;
    const y = +document.getElementById("dnOpenYear").value;
    const m = +document.getElementById("dnOpenMonth").value;
    dnOpenAll = await api().list_dns_for_month(y, m);
    if (dnClientFilter !== "all"
        && !dnOpenAll.some(d => _dnClientOf(d) === dnClientFilter)) {
        dnClientFilter = "all";
    }
    renderDnFilterChips();
    renderDnList();
}

async function loadDnForEdit(dnNo) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("dn");
    const rec = await api().load_dn(dnNo);
    if (!rec) { toast("Cannot load delivery note - not in database", true); return; }

    dnState.mode = "edit";
    dnState.editDnNo = dnNo;
    dnState.dnNo = dnNo;
    reloadDnSignedCopy();               // async - updates the toolbar button
    dnState.date = rec.dn_date || (state.today ? state.today.display : "");
    dnState.rows = (rec.rows || []).slice();
    // Phase T - this DN's own base columns, or the current effective base for
    // un-migrated docs (so they look exactly as they do now).
    dnState.columns = (rec.columns && rec.columns.length)
        ? _dnCloneCols(rec.columns)
        : _dnCloneCols((dnState.baseColumns && dnState.baseColumns.length) ? dnState.baseColumns : DN_COLS_BASE_FALLBACK);
    dnState.sourceQuote = rec.source_quote_no || "";   // Phase N.3 - keep deal link
    /* ...but the PO link is TRANSIENT and must NOT survive: opening a different
       DN into this editor and saving it would otherwise mark the previous DN's
       PO lines delivered. Any real PO link is already stored in po_fulfillment
       at this point; this field only means "still to be written". */
    dnState.sourcePo = null;
    dnState.includePrices = !!rec.include_prices;
    // Restore cost-entry: auto-show the private COST column when the DN already
    // carries costs, and load its cost-entry currency.
    try { dnState.costCurrency = (await api().get_cost_currency("dn", dnNo)) || "USD"; }
    catch (e) { dnState.costCurrency = "USD"; }
    dnState.signingOverlay = rec.signing_overlay || null;
    dnState.autofitRows = dnLayoutAutofit(rec.layout, dnDefaultAutofit());
    try { dnState.extraCosts = JSON.parse(rec.extra_costs_json || "[]") || []; }
    catch (e) { dnState.extraCosts = []; }
    dnDocPeg();             // async; the cards re-render on the next keystroke
    dnRefreshSignChip();
    dnSyncAutofitButton();
    dnState.dirty = false;
    dnState.initialized = true;
    dnState.numberOverridden = true;        // editing -> don't auto-overwrite

    hideAllViews();
    document.getElementById("dnView").hidden = false;
    document.getElementById("dnActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Delivery Note");
    // AFTER hideAllViews() on purpose - it clears __currentEditorDoc, so setting
    // this any earlier leaves it null once the loader returns. That silently
    // broke the panel row highlight and made Duplicate a no-op (its guard reads
    // this to confirm the source actually loaded). See tests/duplicate_document.
    window.__currentEditorDoc = { kind: "dn", no: dnNo };
    buildDnThead();

    document.getElementById("dnNo").value = dnNo;
    // See app.js - a bare `.value =` leaves dnState.date on the loaded date.
    bindDocDate("dnDate", dnState);
    document.getElementById("dnClient").value = rec.client_name || "";
    document.getElementById("dnLpo").value = rec.lpo_no || "N/A";
    document.getElementById("dnAddress").value = rec.client_address || "";
    document.getElementById("dnReference").value = rec.reference || "";
    // Phase N.6 - Linked Quotation field (dnState.sourceQuote already set above).
    wireDnSrcQuote();
    { const sq = document.getElementById("dnSrcQuote"); if (sq) sq.value = rec.source_quote_no || ""; }
    await populateDnQuoteList(rec.client_name || "");
    dnSyncPricesToggle();
    window.setModeChip(true, "Edit mode");

    await populateDnClientList();
    fullRenderDnRows();
    addDnRow();           // trailing blank
    updateDnTotals();
    updateLpoWarning();
    setTimeout(() => window.autoSizeAllTextareas && window.autoSizeAllTextareas(document.getElementById("dnView")), 0);
    // Put the panel on THIS document's month and scroll to its row, so
    // opening one from Needs Attention / search / anywhere does not leave
    // the panel on a different month (2026-10-01).
    if (window.revealDocInPanel) window.revealDocInPanel("dn", dnNo, dnState.date);
}

/* Duplicate (2026-08-04) - turn the just-loaded DN into a NEW unsaved one.
   Called by window.duplicateDocument after loadDnForEdit; see DUPLICATE_HOOK
   in app.js. Rows, client, address, LPO ref, prices toggle and layout stay.
   `sourceQuote` stays too - the copy belongs to the same deal.
   reloadDnSignedCopy() re-reads for the NEW number, so the client's returned
   signed copy does not follow the duplicate. */
async function dnMakeDuplicate() {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("dn");
    dnState.mode = "new";
    dnState.editDnNo = null;
    dnState.numberOverridden = false;
    dnState.signingOverlay = null;            // a copy has not been signed
    document.getElementById("dnReference").value = "";
    dnRefreshSignChip();
    dnState.date = (window.state && state.today) ? state.today.display : dnState.date;
    bindDocDate("dnDate", dnState);
    window.__currentEditorDoc = null;         // not a saved document yet
    window.setModeChip(false);
    await refreshDnNumber();
    await reloadDnSignedCopy();               // no attached client copy on a copy
    refreshDnSaved();
    dnState.dirty = true;                     // real unsaved content
    return dnState.dnNo || "";
}
window.dnMakeDuplicate = dnMakeDuplicate;

// ============================================================
// Import from saved Quotation - picks a QUT, copies rows + meta in.
// ============================================================
async function openImportFromQuoteDialog() {
    // Phase M.2 - scan the last 6 months (was: this month + last only).
    let combined = [];
    try {
        combined = (await gatherRecentMonths((y, m) => api().list_quotes_for_month(y, m), 6))
            .filter(q => q && q.editable)
            // Phase F.4: hide Equipment quotes - DN doesn't map well from EQ.
            .filter(q => (q.type_code || "").toUpperCase() !== "EQ");
    } catch (e) { combined = []; }

    if (!combined.length) {
        await infoModal("No editable quotations",
            "No editable quotations found in the last 6 months. Save a quotation first, then import from it.");
        return;
    }

    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). The chips and the
       search filter the list, so they stay put at the top of the body while it
       scrolls. */
    const _h = window.Popup.open({
        title: "Import from Quotation",
        size: "lg",
        dir: "ltr",
        className: "import-modal",
        html: true,
        body: `<div class="po-link-sticky"><div class="filter-chips-stack">`
            + `<div class="filter-chips" id="imTypeChips"></div>`
            + `<div class="filter-chips filter-chips-client" id="imClientChips"></div></div>`
            + `<input type="text" class="import-search" id="imSearch" placeholder="Search by quotation number, RFQ, or client…"></div>`
            + `<div class="import-list" id="imList"></div>`,
        actions: [{ label: "Cancel", value: "cancel", cls: "ghost" }],
    });
    const backdrop = _h.panel;

    const close = () => _h.close(null);

    // Chip + text-search filter state (modal-local).
    let typeFilter = "all";
    let clientFilter = "all";
    let textFilter = "";
    const TLABELS = { MT: "Materials", MP: "Manpower", SV: "Service" };
    const _clientOf = q => q.client_name || q.client || "";

    function applyFilters() {
        const text = textFilter.toLowerCase();
        return combined.filter(q => {
            if (typeFilter   !== "all" && (q.type_code || "").toUpperCase() !== typeFilter) return false;
            if (clientFilter !== "all" && _clientOf(q) !== clientFilter) return false;
            if (text) {
                const hay = [q.quote_no, _clientOf(q), q.filename]
                    .map(x => (x || "").toString().toLowerCase()).join(" ");
                if (!hay.includes(text)) return false;
            }
            return true;
        });
    }

    function renderChips() {
        const afterClient = combined.filter(q =>
            clientFilter === "all" || _clientOf(q) === clientFilter);
        const tCounts = countByKey(afterClient, q => (q.type_code || "").toUpperCase());
        const typeOpts = [
            { key: "all", label: "All", count: afterClient.length },
            // EQ is filtered out upstream - only MT/MP/SV.
            ...["MT","MP","SV"].map(k => ({ key: k, label: TLABELS[k], count: tCounts[k] || 0 })),
        ];
        renderChipRow(backdrop.querySelector("#imTypeChips"), typeOpts, typeFilter, k => {
            typeFilter = k; renderChips(); renderList();
        });

        const afterType = combined.filter(q =>
            typeFilter === "all" || (q.type_code || "").toUpperCase() === typeFilter);
        const cCounts = countByKey(afterType, _clientOf);
        const clientOpts = [
            { key: "all", label: "All", count: afterType.length },
            ...uniqueSortedKeys(combined, _clientOf)
                .map(c => ({ key: c, label: c, count: cCounts[c] || 0 })),
        ];
        renderChipRow(backdrop.querySelector("#imClientChips"), clientOpts, clientFilter, k => {
            clientFilter = k; renderChips(); renderList();
        });
    }

    const listEl = backdrop.querySelector("#imList");
    function renderList() {
        const rows = applyFilters();
        listEl.innerHTML = "";
        if (!rows.length) {
            listEl.innerHTML = `<div class="empty-state" style="padding:20px;">
                <div class="title">No matches</div></div>`;
            return;
        }
        rows.forEach(q => {
            const row = document.createElement("div");
            row.className = "import-row";
            const typeCode = (q.type_code || "QUT").toString().toUpperCase();
            const clientLbl = _clientOf(q);
            row.innerHTML = `
                <div>
                    <div class="import-main">${esc(q.quote_no)} - ${esc(clientLbl)}</div>
                    <div class="import-sub">${esc(q.filename)}${q.updated_at ? ` · ${esc(q.updated_at)}` : ""}</div>
                </div>
                <span class="import-type-chip">${esc(typeCode)}</span>
            `;
            row.onclick = safe(() => {
                // Phase X.9 - preview the quotation first, import on confirm.
                if (window.showImportPreview) {
                    window.showImportPreview({
                        kind: "quote", no: q.quote_no,
                        onImport: async () => { const ok = await importQuoteIntoDN(q.quote_no); if (ok) close(); },
                    });
                } else {
                    importQuoteIntoDN(q.quote_no).then(ok => { if (ok) close(); });
                }
            }, "Preview quote");
            listEl.appendChild(row);
        });
    }
    renderChips();
    renderList();

    const search = backdrop.querySelector("#imSearch");
    search.addEventListener("input", () => { textFilter = search.value.trim(); renderList(); });
    setTimeout(() => search.focus(), 60);
}

/* Start a delivery note against a client's LPO (2026-09-12).

   The LPO supplies the client, its own number, and what is still OWED - not
   what was originally ordered, so a second delivery does not re-deliver what
   already went out. Everything the LPO does NOT carry (the address, and for an
   invoice the account and tax status) comes from the client's own settings via
   `ClientDefaults.adopt`, exactly as the quotation path does. */
async function createDnFromLpo(clientName, lpoNo) {
    hideAllViews();
    document.getElementById("dnView").hidden = false;
    document.getElementById("dnActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Delivery Note");
    if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
    dnSyncAutofitButton();
    dnState.initialized = false;
    await initNewDn();

    // A NEW document that happens to know its client.
    if (window.ClientDefaults) window.ClientDefaults.reset("dn");
    document.getElementById("dnClient").value = clientName || "";
    document.getElementById("dnLpo").value = lpoNo || "";
    if (window.ClientDefaults) await window.ClientDefaults.adopt("dn");
    await populateDnClientList();
    await refreshDnNumber();

    await adoptLpoIntoDn(clientName, lpoNo, { confirm: false });
}
window.createDnFromLpo = createDnFromLpo;

/* Bring everything an LPO knows into the delivery note that is already open
   (2026-09-28).

   This used to be the tail of `createDnFromLpo` and nothing else could reach
   it, so picking an LPO from the button beside the field - the obvious move
   when you are already writing the delivery note - wrote the number into a
   text box and stopped. The ordered lines, the quotation the PO answers and
   every line's cost were all on file and none of them arrived.

   `confirm: false` from the LPO screen, where the document is brand new and
   there is nothing to overwrite. `confirm: true` from the pick button, where
   there may be half a document typed already. */
async function adoptLpoIntoDn(clientName, lpoNo, opts = {}) {
    const seed = await window.lpoSeedForDocument(clientName, lpoNo, "dn");
    const rows = (seed && seed.rows) || [];
    if (!seed || !seed.ok) {
        toast(`Could not read ${lpoNo}` + (seed && seed.error ? ": " + seed.error : ""), true);
        updateLpoWarning();
        return false;
    }
    /* Ask BEFORE anything is written. `importQuoteIntoDN` has asked since it
       shipped, for the same reason: replacing a half-typed document without
       asking destroys work. The number the user just picked stays either way -
       they chose the PO this note is against; only the items are in question. */
    if (opts.confirm && rows.length && filledDnRows().length > 0 && dnState.dirty) {
        const choice = await showModal({
            title: "Overwrite current items?",
            body: `${lpoNo} has ${rows.length} ordered line(s). Bringing them in `
                + "will replace the items already in this delivery note. Continue?",
            buttons: [
                { label: "Keep mine", value: "cancel", cls: "ghost" },
                { label: "Replace",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") { updateLpoWarning(); return false; }
    }

    if (rows.length) {
        dnState.sourceQuote = seed.source_quote_no || "";
        dnState.sourcePo = null;
        const source = document.getElementById("dnSrcQuote");
        if (source) source.value = dnState.sourceQuote;
    }
    /* The quotation the LPO answers, stamped here rather than typed again.
       Set before the rows: the cost reconciliation below reads the SOURCE
       document's currency and peg from it. */
    if (seed.source_quote_no) {
        dnState.sourceQuote = seed.source_quote_no;
        dnState.sourcePo = null;         // these lines came from a quotation
        const sq = document.getElementById("dnSrcQuote");
        if (sq) sq.value = seed.source_quote_no;
        populateDnQuoteList(clientName || "").catch(() => {});
    }
    if (rows.length) {
        // Same column mapper the quotation import uses, including the rate
        // column when prices are off, so a priced LPO line is not dropped.
        const dnCols = dnColumns().concat(
            dnState.includePrices ? [] : [{ key: "rate", head: "Rate", kind: "money" }]);
        const newRows = rows.map(r => {
            const row = { ...r };
            if (window.priceFromMemory) {
                const converted = window.priceFromMemory({ ...row, price_native: String(row.rate || "").replace(/,/g,""),
                    price_native_ccy: row.rate_ccy || seed.currency || "USD" }, "USD", dnPegSync());
                if (converted !== null && converted !== undefined) row.rate = converted;
            }
            return window.smartMapRow(row, dnCols, "item");
        });
        /* A cost is stored in its own document's currency. Carrying 250,000 IQD
           into a dollar delivery note unconverted reads as $250,000. */
        if (seed.source_quote_no && window.reconcileImportedCostCurrency) {
            const el = document.getElementById("dnNo");
            const dst = ((el && el.value) || dnState.editDnNo || dnState.dnNo || "").trim();
            await window.reconcileImportedCostCurrency(
                newRows, "quote", seed.source_quote_no, "dn", dst);
        }
        if (rows.some(r => parseFloat(r.rate) > 0)) {
            dnState.includePrices = true;
            dnSyncPricesToggle();
            buildDnThead();
        }
        dnState.rows = newRows;
        dnState.dirty = true;
        fullRenderDnRows();
        addDnRow();
        updateDnTotals();
        toast(window.lpoSeedToast(seed, lpoNo));
    } else {
        toast(seed.quantity_policy
            ? `${lpoNo}: no quantities remain to deliver.`
            : `Delivery note filed against ${lpoNo}. No ordered lines are recorded on it.`);
    }
    updateLpoWarning();
    return true;
}
window.adoptLpoIntoDn = adoptLpoIntoDn;

async function importQuoteIntoDN(quoteNo) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("dn");
    const rec = await api().load_quote(quoteNo);
    if (!rec) { toast("Could not load quotation " + quoteNo, true); return false; }

    // If the editor already has user-typed content, ask before clobbering.
    if (filledDnRows().length > 0 && dnState.dirty) {
        const choice = await showModal({
            title: "Overwrite current items?",
            body: `Importing from ${quoteNo} will replace the current items. Continue?`,
            buttons: [
                { label: "Cancel",  value: "cancel", cls: "ghost" },
                { label: "Replace", value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    }

    // Phase M.2 - smart alias-based mapping (shared with INV import).
    // Handles every quote sub-type and user-added custom columns: the
    // primary value never duplicates into description, units map from
    // unit/uom/period, rates from rate/price, and leftover text columns
    // are appended to the description instead of being dropped.
    const dnCols = dnColumns().concat(
        dnState.includePrices ? [] : [{ key: "rate", head: "Rate", kind: "money" }]
    );
    const newRows = (rec.rows || [])
        .map(r => window.smartMapRow(r, dnCols, "item"))
        .filter(r => (r.item || r.description || r.qty || "").toString().trim());

    if (!newRows.length) {
        await infoModal("Nothing to import", "That quotation has no usable items.");
        return false;
    }

    // Pull meta from the quote.
    document.getElementById("dnClient").value  = rec.client_name || "";
    document.getElementById("dnAddress").value = rec.client_address || "";
    // RFQ is QUT-specific; DN uses LPO. If the user already typed an LPO,
    // keep it; otherwise leave the default N/A.
    /* The client's own settings for what a quotation does NOT carry (2026-09-11).
       It supplies the client and the address; it has no receiving account and
       no tax status, so those are still this new document's own defaults and
       the client's settings fill them. Waleed hit this making an invoice from
       DN-HDEC-066 and getting the profile's default account. */
    if (window.ClientDefaults) await window.ClientDefaults.adopt("dn", ["address"]);
    await populateDnClientList();
    await refreshDnNumber();   // refresh based on (possibly new) client

    // If any row has a non-zero rate, auto-enable prices toggle.
    const hasRate = newRows.some(r => parseFloat(r.rate) > 0);
    if (hasRate) {
        dnState.includePrices = true;
        dnSyncPricesToggle();
        buildDnThead();
    }

    // Phase X.10 - convert any carried costs from the quote's currency into the
    // DN's currency so an IQD-costed quote doesn't land as USD in the DN.
    if (window.reconcileImportedCostCurrency) {
        const _dnEl = document.getElementById("dnNo");
        const _dstDn = ((_dnEl && _dnEl.value) || dnState.editDnNo || dnState.dnNo || "").trim();
        await window.reconcileImportedCostCurrency(newRows, "quote", quoteNo, "dn", _dstDn);
    }

    dnState.rows = newRows;
    // Phase N.3 - remember the deal link so it's stamped on save and costs
    // (carried in each row by smartMapRow) belong to the same job.
    dnState.sourceQuote = quoteNo;
    dnState.sourcePo = null;           // these rows came from a quote, not a PO
    // Phase N.6 - reflect the deal link into the Linked Quotation field.
    { const sq = document.getElementById("dnSrcQuote"); if (sq) sq.value = dnState.sourceQuote; }
    populateDnQuoteList(rec.client_name || "").catch(() => {});
    dnState.dirty = true;
    fullRenderDnRows();
    addDnRow();          // trailing blank
    updateDnTotals();
    updateLpoWarning();
    toast(`Imported ${newRows.length} item${newRows.length === 1 ? "" : "s"} from ${quoteNo}`);
    return true;
}

// ============================================================
// Make DN from a Purchase Order (2026-09-05).
//
// The thin half of the feature. Everything that could drift between the DN and
// the Invoice - resolving the PO's Arabic names into English, the review
// screen, the write-back into Master Data, building the row values - lives once
// in `po_handoff.js`. This function exists only because it has to touch
// dnState, initNewDn and buildDnThead, which are private to this module. Same
// split `createInvoiceFromDnNo` already uses in inv.js.
// ============================================================
async function createDnFromPo(payload) {
    const p = payload || {};
    if (!Array.isArray(p.rows) || !p.rows.length) return false;

    /* The view is entered by hand rather than through `enterDnEditor()`, which
       ends with an UN-AWAITED `initNewDn()` when the DN editor has never been
       opened this session. Calling it and then awaiting our own `initNewDn()`
       would run two inits concurrently, and the first one's tail
       (`dnState.rows = []`, `addDnRow()`) could land AFTER the rows below are
       assigned - wiping them. It would work every time the DN editor had been
       visited first and fail on a fresh launch, which is the worst shape a bug
       can have. Mirrors what `createInvoiceFromPo` does in inv.js. */
    hideAllViews();
    document.getElementById("dnView").hidden = false;
    document.getElementById("dnActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Delivery Note");
    if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
    dnSyncAutofitButton();
    dnState.initialized = false;
    await initNewDn();

    // Prices come from Master Data, so a DN made from a PO usually HAS rates.
    // Turn the price columns on when they do - the same rule the quotation
    // import uses - and rebuild the header before the rows are drawn.
    if (p.rows.some(r => parseFloat(r.rate) > 0)) {
        dnState.includePrices = true;
        dnSyncPricesToggle();
        buildDnThead();
    }

    // A PO line's company IS the client to bill. Blank when the picked lines
    // spanned more than one - a DN has one client field, and inventing one is
    // worse than a blank the user fills.
    // Resolved through Clients Master Data before the number is drawn
    // (2026-09-11) - see the same block in createInvoiceFromPo.
    if (p.client) {
        document.getElementById("dnClient").value = p.client;
        if (window.ClientDefaults) await window.ClientDefaults.adopt("dn");
    }
    await populateDnClientList();
    await refreshDnNumber();

    /* The PO's reference, copied as it was written (2026-09-09). AFTER
       `initNewDn`, which blanks the field. Never invented: a PO with no
       reference leaves the box empty rather than guessing one. */
    if (p.reference) {
        const ref = document.getElementById("dnReference");
        if (ref) ref.value = p.reference;
    }

    // AFTER initNewDn - it resets both of these.
    dnState.rows = p.rows.slice();
    dnState.sourcePo = p.link || null;
    dnState.dirty = true;

    fullRenderDnRows();
    addDnRow();                 // trailing blank
    updateDnTotals();
    updateLpoWarning();
    toast(`Filled ${p.rows.length} item${p.rows.length === 1 ? "" : "s"} from ${p.poNo}`);
    return true;
}
window.createDnFromPo = createDnFromPo;

// ============================================================
// Create Invoice from this DN - pre-fills an INV with the DN's
// items and client info (Phase D).
// ============================================================
async function createInvoiceFromDN() {
    if (filledDnRows().length === 0) {
        await infoModal("Nothing to invoice", "Add items first, then click again.");
        return;
    }
    // Source-of-truth check: we need a SAVED DN number so the INV editor
    // can call load_dn() and pull the rows from disk/DB. If the user hasn't
    // saved yet (or has unsaved edits), save first.
    if (dnState.mode !== "edit" || dnState.dirty) {
        const choice = await showModal({
            title: dnState.dirty ? "Unsaved delivery note" : "Save delivery note first",
            body: dnState.dirty
                ? "You have unsaved changes. Save the delivery note before creating an invoice from it?"
                : "Save this delivery note first, then I'll pre-fill an invoice from it.",
            buttons: [
                { label: "Cancel",     value: "cancel", cls: "ghost" },
                { label: "Save first", value: "save",   cls: "primary" },
            ],
        });
        if (choice !== "save") return;
        const ok = await dnSave({ skipConfirm: true });
        if (!ok) return;
    }

    // After save, dnSave() reset us into a fresh new DN - recover the just-saved
    // number from editDnNo before reset OR ask the user to re-open the DN list.
    // Simpler: use the dnNo that's currently visible (set before save).
    const savedNo = (document.getElementById("dnNo").value || "").trim()
                  || dnState.editDnNo || dnState.dnNo;
    if (!savedNo) {
        await infoModal("Cannot find DN number",
            "Save the DN, open it from the list, then click 'Make invoice' again.");
        return;
    }

    if (!window.createInvoiceFromDnNo) {
        await infoModal("Invoice module not loaded",
            "The Invoice module didn't initialize. Reload the app and try again.");
        return;
    }

    // How should this DN appear on the invoice? (2026-07-24)
    //   summary  - ONE line carrying the DN number + its total (dn_ref); the
    //              DN's own pages are appended to the invoice PDF.
    //   itemised - every DN row copied across as its own invoice line.
    let total = null;
    try {
        const s = await api().dn_cost_summary(savedNo);
        if (s && s.ok && s.has_prices) total = s.sell_total;
    } catch (e) { /* priced or not, both choices still work */ }
    const totalTxt = (total || total === 0)
        ? ` (${dmoney(total)})`
        : "";

    const choice = await showModal({
        title: "How should this appear on the invoice?",
        body: `<div class="mk-inv-choice">
            <div><b>One line for the whole delivery note</b><br>
            <span>The invoice shows <b>${esc(savedNo)}</b>${esc(totalTxt)} as a single line, and the
            delivery note's pages are attached to the invoice PDF.</span></div>
            <div><b>Copy every item across</b><br>
            <span>Each item on the delivery note becomes its own invoice line, so you can
            edit prices individually.</span></div>
        </div>`,
        html: true,
        buttons: [
            { label: "Cancel",           value: "cancel",   cls: "ghost" },
            { label: "Copy every item",  value: "itemised", cls: "secondary" },
            { label: "One line",         value: "summary",  cls: "primary" },
        ],
    });
    if (choice === "cancel" || choice == null) return;

    if (choice === "summary") {
        if (total == null) {
            await infoModal("This delivery note has no prices",
                "Add rates to the delivery note (or use “Copy every item”) so the "
                + "single line has a value to bill.");
            return;
        }
        await window.createInvoiceFromDnAsLine(savedNo);
        return;
    }
    await window.createInvoiceFromDnNo(savedNo);
}

// ============================================================
// Phase M.5a - Excel-style grid paste on dnRowsBody
// ============================================================
function _pushEmptyDnRow() {
    const row = {};
    dnColumns().forEach(c => { if (c.kind !== 'computed') row[c.key] = ''; });
    dnState.rows.push(row);
}
function _wireDnGridPaste() {
    const tbody = document.getElementById('dnRowsBody');
    if (!tbody || !window.setupGridPaste) return;
    const _renderAfterPaste = () => {
        fullRenderDnRows();
        if (filledDnRows().length === dnState.rows.length) addDnRow();
        if (typeof updateDnTotals === 'function') updateDnTotals();
    };
    window.setupGridPaste(tbody, {
        columns:     () => dnColumns(),
        rowCount:    () => dnState.rows.length,
        addEmptyRow: () => _pushEmptyDnRow(),
        setCell:     (i, k, v) => { if (dnState.rows[i]) { dnState.rows[i][k] = v; dnState.dirty = true; } },
        fullRender:  _renderAfterPaste,
        // Phase M.5c - Ctrl+Z undo via snapshot/restore.
        snapshot: () => JSON.parse(JSON.stringify(dnState.rows)),
        restore:  (snap) => {
            dnState.rows = snap;
            dnState.dirty = true;
            _renderAfterPaste();
        },
    });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _wireDnGridPaste);
else _wireDnGridPaste();
