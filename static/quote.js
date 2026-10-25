/* Quotation editor logic (English, LTR) - schema-driven for 4 types.
   Reuses shared helpers from app.js: api(), toast(), showModal(),
   withLoading(), esc(), safe(), hideAllViews(), setBrand(), window.ICON,
   and the global `state` (state.today). */

const quoteState = {
    type: "materials",
    schema: null,        // current type's schema {code,label,columns,terms,grand_total}
    types: {},           // all type schemas from the backend
    quoteNo: "-",
    date: "-",
    rows: [],            // list of dicts keyed by column keys
    terms: [],
    mode: "new",
    editQuoteNo: null,
    dirty: false,
    initialized: false,
    addressOptions: [],
    layoutDefault: {},
    autofitRows: false,
};
window.quoteState = quoteState;

let _quoteDirty = !!quoteState.dirty;
Object.defineProperty(quoteState, "dirty", {
    get() { return _quoteDirty; },
    set(v) {
        _quoteDirty = !!v;
        if (typeof refreshQuoteSaved === "function") refreshQuoteSaved();
    },
    configurable: true,
    enumerable: true,
});

const QMONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const QTYPE_ORDER = ["materials", "equipment", "manpower", "service"];

function quoteLayoutAutofit(layout, fallback = false) {
    if (window.layoutAutofitValue) return window.layoutAutofitValue(layout, fallback);
    if (layout && typeof layout === "object" &&
        Object.prototype.hasOwnProperty.call(layout, "autofit_rows")) {
        return !!layout.autofit_rows;
    }
    return !!fallback;
}

function quoteDefaultAutofit() {
    return quoteLayoutAutofit(quoteState.layoutDefault, false);
}

function quoteLayoutPayload() {
    return window.editorLayoutPayload
        ? window.editorLayoutPayload(quoteState.autofitRows)
        : { autofit_rows: !!quoteState.autofitRows };
}

function quoteSyncAutofitButton() {
    if (window.syncAutofitButton) {
        window.syncAutofitButton("btnQAutofit", quoteState.autofitRows);
    }
}
window.quoteSyncAutofitButton = quoteSyncAutofitButton;

// ============================================================
// Module init (once, at bootstrap)
// ============================================================
async function initQuoteModule() {
    const d = await api().get_quote_defaults();
    quoteState.addressOptions = d.address_options || [];
    quoteState.layoutDefault = d.layout || {};
    quoteState.autofitRows = quoteDefaultAutofit();
    try { quoteState.types = await api().get_quote_types(); } catch (e) { quoteState.types = {}; }

    document.getElementById("qAddrList").innerHTML =
        quoteState.addressOptions.map(a => `<option value="${esc(a)}">`).join("");

    const sel = document.getElementById("qOpenMonth");
    sel.innerHTML = QMONTHS.map((n, i) => `<option value="${i + 1}">${n}</option>`).join("");
    if (state.today) {
        sel.value = state.today.month;
        document.getElementById("qOpenYear").value = state.today.year;
    }

    document.getElementById("btnQNew").onclick        = safe(quoteNew,     "New quote");
    { const b = document.getElementById("btnQOpen"); if (b) b.onclick = safe(quoteOpen, "Open quote"); }
    document.getElementById("btnQPreview").onclick    = safe(quotePreview, "Preview quote");
    { const b = document.getElementById("btnQPrint"); if (b) b.onclick = safe(quotePrint, "Print quote"); }
    const btnQStay = document.getElementById("btnQStay");
    if (btnQStay) btnQStay.onclick                    = safe(() => quoteSave({ stay: true, skipConfirm: true }), "Save quote");
    const btnQMasterFill = document.getElementById("btnQMasterFill");
    if (btnQMasterFill) btnQMasterFill.onclick = safe(() => window.openMasterDataFill({
        docType: "qut",
        subType: quoteState.type,
        rows: quoteState.rows,
        primaryKey: (quoteState.schema && quoteState.schema.primary_key) || "item",
        costCurrency: quoteState.costCurrency || "USD",
        costPeg: quotePegSync(),
        sellCurrency: "USD",
        render: () => fullRenderQuoteRows(),
        markDirty: () => { quoteState.dirty = true; },
    }), "Fill from Master Data");
    const btnQAutofit = document.getElementById("btnQAutofit");
    if (btnQAutofit) btnQAutofit.onclick = safe(() => {
        quoteState.autofitRows = !quoteState.autofitRows;
        quoteState.dirty = true;
        quoteSyncAutofitButton();
        refreshQuoteSaved();
    }, "Auto-fit rows");
    if (window.wireFitColsButton) window.wireFitColsButton("btnQFitCols", "#qThead", {
        pdf: {
            widthMm: 186,
            headerFont: 'bold 10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
            // Body font of `.q-table` in quote_styles.css - the fit measures a
            // money value at PRINT size so `$4,300.00` cannot wrap (2026-09-02).
            bodyFont: '10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
        },
        // Which columns print money. Read off the live schema so a renamed or
        // added money column is covered without touching this line.
        money: (key) => (quoteRenderColumns() || [])
            .some(c => c.key === key && (c.kind === "money" || c.kind === "computed")),
        formatMoney: (v) => qmoney(v),
        onFit: () => { quoteState.dirty = true; refreshQuoteSaved(); },
    });
    quoteSyncAutofitButton();
    document.getElementById("btnQAddRow").onclick     = safe(() => {
        const last = quoteState.rows[quoteState.rows.length - 1];
        if (!last || hasQuoteContent(last)) addQuoteRow();
        focusFirstCol(quoteState.rows.length - 1);
    }, "Add row");
    document.getElementById("btnQTerms").onclick      = safe(openTermsEditor, "Terms");
    document.getElementById("btnQRefreshList").onclick = safe(refreshQuoteList, "Refresh");
    document.getElementById("btnQCloseOpen").onclick   = safe(showQuoteHub, "Back");
    document.getElementById("btnQTypes").onclick       = safe(showQuoteHub, "Back to types");

    // ⚙ on the QUOTATION EDITOR (per-sub-type). The settings modal opens with
    // a sub-type-specific section pre-filled for the currently-active type.
    const qSetBtn = document.getElementById("btnQTypeSettings");
    if (qSetBtn) qSetBtn.onclick = safe(() => {
        if (!window.openDocSettings) { toast("Settings module not loaded", true); return; }
        const activeColumns = quoteSettingsColumnsSnapshot();
        quoteState.columns = _cloneCols(activeColumns);
        window.openDocSettings("quotation", {
            subtype: quoteState.type,
            subtypeLabel: quoteState.schema && quoteState.schema.label,
            columns: activeColumns,
            onSaved: async (meta = {}) => {
                // Pull fresh per-type schema/layout. Only replace this open
                // document's columns when the settings columns editor was
                // explicitly changed; layout-only saves must preserve the
                // document-specific column layout.
                try {
                    quoteState.types = await api().get_quote_types();
                    quoteState.schema = quoteState.types[quoteState.type] || quoteState.schema;
                    if (meta.columnsChanged) {
                        const nextColumns = _cloneCols((quoteState.schema && quoteState.schema.columns) || []);
                        if (nextColumns.length) {
                            const before = JSON.stringify(quoteState.columns || []);
                            const after = JSON.stringify(nextColumns);
                            quoteState.columns = nextColumns;
                            if (before !== after) quoteState.dirty = true;
                        }
                    }
                    buildQuoteThead();
                    fullRenderQuoteRows();
                    refreshQuoteSaved();
                    const d = await api().get_quote_defaults();
                    quoteState.addressOptions = d.address_options || [];
                    quoteState.layoutDefault = d.layout || {};
                    document.getElementById("qAddrList").innerHTML =
                        quoteState.addressOptions.map(a => `<option value="${esc(a)}">`).join("");
                } catch {}
            },
        });
    }, "Quotation type settings");

    // Mini-hub back button → main hub.
    const qHubBack = document.getElementById("btnQuoteHubBack");
    if (qHubBack) qHubBack.onclick = safe(() => window.showHub && window.showHub(), "Back");

    document.getElementById("qClient").addEventListener("change", safe(refreshQuoteNumber, "Quote number"));
    document.getElementById("qReference").addEventListener("input", () => {
        quoteState.dirty = true;
    });

    window.showQuoteHub = showQuoteHub;
    window.enterQuoteType = enterQuoteType;
    window.quoteSave = quoteSave;
    window.quotePreview = quotePreview;
    window.quoteNew = quoteNew;
    window.quoteOpen = quoteOpen;
}
window.initQuoteModule = initQuoteModule;

// ============================================================
// Mini-hub (4 type cards)
// ============================================================
// Phase M.8 - the old full-page type picker is replaced by the master–detail
// type rail. Entering Quotation goes straight into the editor for the current
// (or default) type, with the rail shown for switching.
async function showQuoteHub() {
    if (!quoteState.types || !Object.keys(quoteState.types).length) {
        try { quoteState.types = await api().get_quote_types(); }
        catch { quoteState.types = {}; }
    }
    enterQuoteType(quoteState.type || "materials");
}
window.showQuoteHub = showQuoteHub;

// Build the rail item list (only types that exist), in canonical order.
function quoteRailItems() {
    return QTYPE_ORDER
        .filter(k => quoteState.types && quoteState.types[k])
        .map(k => ({ key: k, label: quoteState.types[k].label }));
}

function enterQuoteType(key) {
    quoteState.drafts = quoteState.drafts || {};
    const oldType = quoteState.type;
    const switchingType = oldType !== key;
    // Phase M.8 - stash the current type's in-progress work before leaving it,
    // so switching types (or coming back later) never loses a draft.
    if (switchingType && quoteState.initialized) {
        stashQuoteDraftIfDirty(oldType);
    }
    quoteState.type = key;
    // Phase M.7 - ALWAYS resolve the schema (never leave it null).
    quoteState.schema = (quoteState.types && (quoteState.types[key] || quoteState.types["materials"]))
                        || { label: key, columns: [] };
    // Phase T - seed this type's columns BEFORE the thead builds. A stashed
    // draft carries its own columns (restored below); otherwise start from stock.
    if (switchingType || !quoteState.columns || !quoteState.columns.length) {
        const stashed = quoteState.drafts[key];
        quoteState.columns = (stashed && stashed.columns && stashed.columns.length)
            ? _cloneCols(stashed.columns) : _cloneCols(_stockColumns());
    }

    hideAllViews();
    document.getElementById("quoteView").hidden = false;
    document.getElementById("quoteActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Quotation - " + quoteState.schema.label);
    if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();

    document.getElementById("qGrandWrap").style.display =
        quoteState.schema.grand_total ? "" : "none";

    buildQuoteThead();   // build for the NEW type's columns before rendering rows

    if (switchingType) {
        const d = quoteState.drafts[key];
        if (d) {
            restoreQuoteDraft(d);        // bring back this type's stashed draft
            delete quoteState.drafts[key];   // it's the live doc again
        } else {
            quoteState.initialized = false;  // no draft → fresh doc below
        }
    }
    quoteDraftUnsaved[key] = false;          // active type - never shows a rail dot
    if (!quoteState.initialized) initNewQuote();

    // Phase M.8 - rail with unsaved-draft dots on the other types.
    if (window.showTypeRail) showTypeRail("Quotation Types", quoteRailItemsBadged(key), key, (k) => enterQuoteType(k));
    refreshQuoteSaved();
    // Phase M.8d - the type bar now drives the Open panel's type filter.
    syncQuotePanelType();
}
window.enterQuoteType = enterQuoteType;

// ============================================================
// Schema helpers
// ============================================================
// Phase T - per-document columns. quoteState.columns is THIS document's own
// column layout (renamed headers + custom columns). New docs seed from the
// effective type schema; existing docs load their saved layout (or fall back to
// the effective type schema). Editing columns in the table is per-document
// until the type-settings modal saves those columns as the subtype default.
function _cloneCols(cols) { return (cols || []).map(c => ({ ...c })); }
function _stockColumns() {
    const sch = quoteState.schema || {};
    // New quotes should start from the effective type schema, including any
    // Equipment/Materials settings the user saved. `stock_columns` remains a
    // backend reference to the built-in defaults, not the editor seed.
    return (sch.columns && sch.columns.length) ? sch.columns : (sch.stock_columns || []);
}
function _nextQuoteColKey() {
    const taken = new Set((quoteState.columns || []).map(c => c.key));
    let n = 2;
    while (taken.has(`col_${n}`)) n++;
    return `col_${n}`;
}
function editableColumns() {
    // Image columns ARE shown in the editor (with upload/paste/crop UI),
    // they're just rendered differently from text/money columns.
    if (!quoteState.columns || !quoteState.columns.length) {
        quoteState.columns = _cloneCols(_stockColumns());
    }
    return quoteState.columns;
}

function quoteSettingsColumnsSnapshot() {
    const heads = {};
    document.querySelectorAll("#qThead .qc-head-input").forEach(inp => {
        if (inp.dataset && inp.dataset.colKey) heads[inp.dataset.colKey] = inp.value;
    });
    const thead = document.getElementById("qThead");
    const widths = (window.readEditorColWidths && thead)
        ? (window.readEditorColWidths(thead) || {}) : {};
    return editableColumns().map(c => {
        const out = { ...c };
        if (Object.prototype.hasOwnProperty.call(heads, c.key)) out.head = heads[c.key];
        const w = widths[c.key];
        if (typeof w === "number" && w > 0) out.width = w;
        return out;
    });
}
window.quoteSettingsColumnsSnapshot = quoteSettingsColumnsSnapshot;

// Insert a blank custom column AFTER the column with key `afterKey` (or at the
// end). Per-document only. Re-renders the header + rows and focuses the header.
function insertQuoteColAfter(afterKey) {
    const cols = editableColumns();
    let at = cols.length;
    if (afterKey) {
        const idx = cols.findIndex(c => c.key === afterKey);
        if (idx >= 0) at = idx + 1;
    }
    const key = _nextQuoteColKey();
    cols.splice(at, 0, { key, head: "New column", kind: "text", width: 12 });
    quoteState.dirty = true;
    buildQuoteThead();
    fullRenderQuoteRows();
    const inp = document.querySelector(`#qThead .qc-head-input[data-col-key="${key}"]`);
    if (inp) { inp.focus(); inp.select(); }
}
window.insertQuoteColAfter = insertQuoteColAfter;

// Remove a column from THIS document (never the primary/anchor column).
function deleteQuoteCol(key) {
    const cols = editableColumns();
    const idx = cols.findIndex(c => c.key === key);
    if (idx < 0) return;
    if (key === (quoteState.schema && quoteState.schema.primary_key)) {
        toast("Can't remove the main column", true); return;
    }
    if (cols.length <= 1) { toast("Keep at least one column", true); return; }
    cols.splice(idx, 1);
    quoteState.dirty = true;
    buildQuoteThead();
    fullRenderQuoteRows();
}
window.deleteQuoteCol = deleteQuoteCol;

// The COST column is ALWAYS shown (2026-07-24 - was a toolbar toggle). It is
// bound to each row's hidden `cost` field - the same one the Costing screen
// uses - and is NOT a schema column, so it never reaches the PDF generator and
// never prints. Always the LAST column, consistently across all editors.
function quoteRenderColumns() {
    const cols = editableColumns().slice();
    cols.push({ key: "cost", head: "COST", kind: "money", width: 10, _synthetic: true });
    // PROFIT rides beside COST in the private strip (2026-08-25). Read-only and
    // COMPUTED - `profit` is never a row key, so it cannot reach rows_json or a
    // PDF payload the way `cost` deliberately does.
    cols.push({ key: "profit", head: "PROFIT", kind: "profit", width: 10, _synthetic: true, _readonly: true });
    return cols;
}

function buildQuoteThead() {
    // Phase L.4.1 - propagate the schema's per-column width into the editor
    // so columns get reasonable shapes (not all-equal). Without this, the
    // editor table renders every column at 1/N width regardless of the
    // schema, and important columns like Description/Image get squeezed.
    let html = `<tr><th class="qc-no">NO.</th>`;
    const primaryKey = quoteState.schema && quoteState.schema.primary_key;
    quoteRenderColumns().forEach(c => {
        const al = c.align === "left" ? "al-left" : "al-center";
        const w = c.width ? ` style="width: ${c.width}%"` : "";
        if (c._synthetic) {
            const stripCls = c.key === "profit" ? "qc-profit-col" : "qc-cost-col";
            // The COST header used to carry an IQD/USD switch; removed
            // 2026-08-20 (each cost cell has its own $/IQD toggle, which is the
            // one that matters - a cost is stored in the currency it was typed
            // in, per line).
            html += `<th class="${al} ${stripCls} qc-private" data-col-key="${esc(c.key)}"${w}>
                <div class="qcc-head">${esc(c.head)}</div>
            </th>`;
        } else {
            // Phase T - editable header + inline insert(+)/remove(×) controls.
            const delBtn = (c.key === primaryKey) ? ""
                : `<button type="button" class="qc-head-del" data-col-key="${esc(c.key)}" title="Remove this column" tabindex="-1">×</button>`;
            // An image column also gets "Find" - one click fetches a reference
            // photo for every row that has no picture yet (app.js).
            const findBtn = (c.kind === "image" && window.imgFetchButtonHTML)
                ? window.imgFetchButtonHTML(c.key) : "";
            html += `<th class="${al} qc-head-th" data-col-key="${esc(c.key)}"${w}>
                <input class="qc-head-input" data-col-key="${esc(c.key)}" value="${esc(c.head)}" spellcheck="false" autocomplete="off" aria-label="Column header">
                ${delBtn}
                <button type="button" class="qc-head-add" data-after="${esc(c.key)}" title="Insert a column after this one" tabindex="-1">+</button>
                ${findBtn}
            </th>`;
        }
    });
    html += `<th class="qc-del"></th></tr>`;
    const thead = document.getElementById("qThead");
    thead.innerHTML = html;
    thead.querySelectorAll("[data-find-img]").forEach(b => {
        b.onclick = () => window.fillImagesFromWeb({
            rows: quoteState.rows,
            key: b.dataset.findImg,
            tbody: "qRowsBody",
            nameOf: (row) => row[quoteState.schema && quoteState.schema.primary_key],
            get: (row) => row[b.dataset.findImg],
            set: (row, uri) => { row[b.dataset.findImg] = uri; quoteState.dirty = true; },
        });
    });
    // Wire editable headers + the inline +/× controls.
    thead.querySelectorAll(".qc-head-input").forEach(inp => {
        inp.addEventListener("input", () => {
            const col = (quoteState.columns || []).find(c => c.key === inp.dataset.colKey);
            if (col) { col.head = inp.value; quoteState.dirty = true; }
        });
        // Keep typing/selecting in the header from triggering a column resize.
        inp.addEventListener("mousedown", (e) => e.stopPropagation());
    });
    thead.querySelectorAll(".qc-head-add").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); insertQuoteColAfter(b.dataset.after); }));
    thead.querySelectorAll(".qc-head-del").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); deleteQuoteCol(b.dataset.colKey); }));
    if (window.refreshColResizers) window.refreshColResizers();
    wireQuoteCurrencySwitch();
}

// Phase R - IQD/USD cost-entry switch shown in the quote editor's COST header
// (only when cost-entry is on). Same look as the Costing screen's switch; the
// chosen currency is saved with the quote and read back by the Costing screen.
function quoteCurrencySwitchHTML(cur) {
    const usd = cur === "USD";
    return `<div class="cost-cur-switch ${usd ? "is-usd" : "is-iqd"}" id="qCostCurSwitch"
                 role="switch" aria-checked="${usd}" tabindex="0"
                 aria-label="Cost entry currency"
                 title="Cost entry currency - click to switch. Profit stays in USD.">
        <span class="ccs-opt ccs-iqd-opt">IQD</span>
        <span class="ccs-opt ccs-usd-opt">$</span>
        <span class="ccs-knob" aria-hidden="true"></span>
    </div>`;
}
function wireQuoteCurrencySwitch() {
    const sw = document.getElementById("qCostCurSwitch");
    if (!sw) return;
    const toggle = () => setQuoteCostCurrency((quoteState.costCurrency || "USD") === "USD" ? "IQD" : "USD");
    sw.addEventListener("click", toggle);
    sw.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
}
// Flip the quote's cost-entry currency, converting already-typed cost cells so
// the real cost is preserved. Persisted to doc_cost_meta on the next quote Save
// (together with the converted costs, so currency + numbers always agree).
/* This quote's IQD peg, cached so the live profit cards can convert an IQD
   cost on every keystroke without a bridge round-trip. Refreshed on load and
   whenever the cost currency is switched. */
let _quotePegCache = null;
async function quoteDocPeg() {
    let iqd = window.IQD_PER_USD || 1400;
    // A rate the user pinned on THIS quote wins, and is known without a
    // round-trip once loaded - quotePinnedPeg mirrors doc_cost_meta.rate.
    if (Number(quoteState.costPeg) > 0) {
        _quotePegCache = Number(quoteState.costPeg);
        return _quotePegCache;
    }
    const no = quoteState.editQuoteNo || quoteState.quoteNo;
    if (no) {
        try { const r = await api().doc_iqd_per_usd("quote", no); if (Number(r)) iqd = Number(r); }
        catch (e) { /* fall back to the profile peg */ }
    }
    _quotePegCache = iqd;
    return iqd;
}
function quotePegSync() {
    if (Number(quoteState.costPeg) > 0) return Number(quoteState.costPeg);
    return _quotePegCache || window.IQD_PER_USD || 1400;
}

/* Ask for the cost exchange rate and pin it to this quote. Reuses the invoice
   editor's prompt so both places ask the same way; the value is persisted via
   set_cost_rate, and doc_iqd_per_usd then returns it for every profit figure
   this document produces. */
async function changeQuoteCostPeg() {
    if (!window.askInvPeg) { toast("Rate editor not loaded", true); return; }
    const old = quotePegSync();
    const rate = await window.askInvPeg(old, {
        title: "Cost exchange rate",
        prompt: "Costs on this quotation convert at",
        okLabel: "Use this rate",
        hint: "Saved with the quotation and used for its profit figures.",
    });
    if (rate == null || rate === old) return;
    quoteState.costPeg = rate;
    _quotePegCache = rate;
    const no = quoteState.editQuoteNo || quoteState.quoteNo;
    if (no) {
        try { await api().set_cost_rate("quote", no, rate); }
        catch (e) { toast("Could not save the rate", true); }
    }
    quoteState.dirty = true;
    renderQuoteEconCards();
}
window.changeQuoteCostPeg = changeQuoteCostPeg;

async function setQuoteCostCurrency(next) {
    const cur = quoteState.costCurrency || "USD";
    if (next === cur) return;
    // Convert at THIS quote's deal peg (profile peg, or the market rate if the
    // deal is paid in cash) so the stored number agrees with the profit math.
    // An unsaved quote has no deal yet → profile peg.
    let _iqd = window.IQD_PER_USD || 1400;
    const _no = quoteState.editQuoteNo || quoteState.quoteNo;
    if (_no) {
        try {
            const r = await api().doc_iqd_per_usd("quote", _no);
            if (Number(r)) _iqd = Number(r);
        } catch (e) { /* fall back to the profile peg */ }
    }
    /* Convert each row FROM ITS OWN currency, not blindly by one factor.
       Since 2026-08-06 a row can carry `cost_ccy`, so a line already priced in
       dinars inside a dollar quote must be left alone when the document flips
       to dinars - multiplying it again would inflate it by the exchange rate.
       Every row is then stamped with the new currency, because after this they
       really are all in it. */
    window.convertRowsToCostCurrency(quoteState.rows, "qRowsBody", next, _iqd, cur);
    quoteState.costCurrency = next;
    quoteState.dirty = true;
    const sw = document.getElementById("qCostCurSwitch");
    if (sw) {
        const usd = next === "USD";
        sw.classList.toggle("is-usd", usd);
        sw.classList.toggle("is-iqd", !usd);
        sw.setAttribute("aria-checked", usd ? "true" : "false");
    }
}

// ============================================================
// New / number
// ============================================================
async function initNewQuote() {
    quoteState.mode = "new";
    quoteState.editQuoteNo = null;
    quoteState.dirty = false;
    quoteState.numberOverridden = false;    // fresh quote -> auto number again
    discardQuoteDraft(quoteState.type);     // explicit fresh doc must not revive an old finished layout
    quoteState.rows = [];
    quoteState.costCurrency = "USD";        // Phase R - cost-entry currency
    quoteState.extraCosts = [];             // document-level costs (Costing screen)
    quoteState.costPeg = null;              // pinned cost exchange rate
    _quotePegCache = null;
    quoteState.columns = _cloneCols(_stockColumns());   // Phase T - fresh = stock
    quoteState.terms = (quoteState.schema.terms || []).slice();
    quoteState.signingOverlay = null;
    quoteState.autofitRows = quoteDefaultAutofit();
    quoteRefreshSignChip();
    quoteSyncAutofitButton();
    quoteState.date = state.today ? state.today.display : "";
    if (window.clearPendingDoc) window.clearPendingDoc();   // a New doc replaces any unsaved duplicate
    // Phase X.26 - fresh doc: drop the panel highlight and put the list on
    // today's month (shared with the other three editors, see app.js).
    if (window.panelNewDocument) window.panelNewDocument("quote");
    else window.__currentEditorDoc = null;

    document.getElementById("qClient").value = "";
    document.getElementById("qRfq").value = "N/A";
    document.getElementById("qAddress").value = quoteState.addressOptions[0] || "";
    document.getElementById("qReference").value = "";
    // Date input - initialise to today and wire change handler once.
    bindDocDate("qDate", quoteState);
    window.setModeChip(false);

    await populateClientList();
    await refreshQuoteNumber();

    buildQuoteThead();   // Phase T - rebuild header for the fresh stock columns
    document.getElementById("qRowsBody").innerHTML = "";
    addQuoteRow();
    quoteState.initialized = true;
    updateTermsCount();
    updateQuoteTotals();
    // A different document is in the editor now, so client settings must
    // forget what they filled into the last one. Every other path that replaces
    // the document (load / restore / duplicate / import) resets it too.
    if (window.ClientDefaults) window.ClientDefaults.reset("qut");
}

async function populateClientList() {
    const list = await api().suggest_clients("");
    document.getElementById("qClientList").innerHTML =
        list.map(c => `<option value="${esc(c.name)}">`).join("");
}

async function refreshQuoteNumber() {
    const client = document.getElementById("qClient").value.trim();
    const r = await api().get_next_quote_number(client, quoteState.type);
    // Only overwrite the visible value if the user hasn't manually overridden it.
    const el = document.getElementById("qNo");
    if (!quoteState.numberOverridden) {
        quoteState.quoteNo = r.quote_no;
        el.value = r.quote_no;
    }
    // Wire the input event once.
    if (!el._wired) {
        el.addEventListener("input", () => {
            quoteState.quoteNo = el.value.trim();
            quoteState.numberOverridden = true;     // stop client-change from clobbering it
            quoteState.dirty = true;
        });
        el._wired = true;
    }
}

// The "do I have unsaved work?" rule lives in static/unsaved_guard.js, one
// copy for all four editors. See that file for why it was collected there.
if (window.unsavedGuard) {
    window.unsavedGuard.register("quote", {
        noun: "quotation",
        label: "Quotation",
        dirty: () => !!quoteState.dirty,
        filled: () => filledQuoteRows(),
        save: (opts) => quoteSave(opts),
    });
}

async function quoteNew() {
    if (!(await window.unsavedGuard.confirmNew("quote"))) return;
    quoteState.initialized = false;
    await initNewQuote();
}

// ============================================================
// Rows
// ============================================================
function addQuoteRow(data = {}) {
    const row = {};
    editableColumns().forEach(c => {
        if (c.kind === "computed") return;
        row[c.key] = (data[c.key] !== undefined && data[c.key] !== null)
            ? data[c.key] : (c.default || "");
    });
    quoteState.rows.push(row);
    document.getElementById("qRowsBody").appendChild(buildQuoteRowTr(quoteState.rows.length - 1));
    updateQuoteTotals();
}

// Insert a blank row at index i (shared hover "+" affordance - per document).
function insertQuoteRowAt(i) {
    const row = {};
    editableColumns().forEach(c => { if (c.kind !== "computed") row[c.key] = c.default || ""; });
    quoteState.rows.splice(i, 0, row);
    quoteState.dirty = true;
    fullRenderQuoteRows();
    updateQuoteTotals();
    const tr = document.querySelector(`#qRowsBody tr[data-i="${i}"]`);
    const f = tr && tr.querySelector("textarea, input");
    if (f) f.focus();
}
window.insertQuoteRowAt = insertQuoteRowAt;

function buildQuoteRowTr(i) {
    const r = quoteState.rows[i];
    const tr = document.createElement("tr");
    tr.dataset.i = i;
    let html = `<td class="qc-no">${i + 1}</td>`;
    quoteRenderColumns().forEach(c => {
        if (c.kind === "computed") {
            html += `<td class="qc-total" data-k="${c.key}"></td>`;
        } else if (c.kind === "text") {
            html += `<td><textarea data-k="${c.key}" rows="1" autocomplete="off">${esc(r[c.key] || "")}</textarea></td>`;
        } else if (c.kind === "image") {
            // Markup shared with the PO editor (window.rowImageCellHTML, app.js)
            // so the two tables cannot drift on what a filled cell looks like.
            html += `<td class="qc-img-cell" data-k="${c.key}">${window.rowImageCellHTML(r[c.key])}</td>`;
        } else if (c._synthetic && c.key === "cost") {
            /* 2026-08-06 - the cost cell carries its OWN currency, so one table
               can price one line in dollars and the next in dinars and the
               profit maths still adds up (database.row_cost_currency /
               rowCostCurrency in app.js). A row with no `cost_ccy` inherits the
               document's, which is every row saved before this existed. */
            /* 2026-08-13: markup moved to app.js, shared with DN + invoice. */
            html += window.rowCostCellHTML(r, quoteState.costCurrency);
        } else if (c._synthetic && c.key === "profit") {
            // Painted by window.paintRowEconCells from updateQuoteTotals().
            html += window.rowProfitCellHTML();
        } else {
            const im = 'inputmode="decimal"';
            html += `<td><input data-k="${c.key}" value="${esc(r[c.key] || "")}" ${im} autocomplete="off" spellcheck="false"></td>`;
        }
    });
    html += `<td class="qc-del"><button class="del-btn" title="delete" aria-label="delete">${window.ICON ? window.ICON.trash : "×"}</button></td>`;
    tr.innerHTML = html;
    wireQuoteRow(tr);
    return tr;
}

function autoResizeTextarea(ta) {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
}

// ============================================================
// Per-type autocomplete on the schema's primary column
//   (item / equipment / role / description, depending on quote type).
// On pick: fills every other editable column from the remembered data.
// ============================================================
let qacTarget = null;     // { field, rowIdx }
let qacItems  = [];
let qacIdx    = -1;

async function onPrimaryInput(field, rowIdx) {
    const q = field.value.trim();
    if (!q) { hideQuoteAc(); return; }
    const suggestions = await api().suggest_quote_items(quoteState.type, q);
    if (!suggestions || suggestions.length === 0) { hideQuoteAc(); return; }
    showQuoteAc(field, rowIdx, suggestions);
}

function showQuoteAc(field, rowIdx, suggestions) {
    const box = document.getElementById("acBox");
    box.innerHTML = "";
    qacItems  = suggestions;
    qacIdx    = -1;
    qacTarget = { field, rowIdx };
    suggestions.forEach((s, i) => {
        // Subtitle = the other columns' remembered values, joined
        /* Build the subtitle from the remembered values.
           Two keys are deliberately kept out of it:
             - `cost_usd` is stored in a different unit from the one being typed
               in, so a bare number here would misread as the cost;
             - `image_refs` is an OBJECT - String()-ing it printed the literal
               text "[object Object]" in the dropdown.
           Non-strings are skipped wholesale rather than special-casing that one
           key, so a future object-valued field cannot reintroduce it. */
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
        div.onmousedown = (e) => { e.preventDefault(); pickQuoteSuggestion(i); };
        box.appendChild(div);
    });
    const rect = field.getBoundingClientRect();
    box.style.top      = (rect.bottom + window.scrollY) + "px";
    box.style.left     = (rect.left   + window.scrollX) + "px";
    box.style.minWidth = rect.width + "px";
    box.hidden = false;
}

function hideQuoteAc() {
    const box = document.getElementById("acBox");
    if (box) box.hidden = true;
    qacTarget = null; qacItems = []; qacIdx = -1;
}

function pickQuoteSuggestion(i) {
    if (!qacTarget) return;
    const s = qacItems[i];
    const ri = qacTarget.rowIdx;
    const row = quoteState.rows[ri];
    const pk = quoteState.schema.primary_key;

    row[pk] = s.name;
    if (s.data) {
        for (const k of Object.keys(s.data)) {
            // `cost` is skipped DELIBERATELY. Entries remembered before
            // 2026-08-05 hold a raw cost in an unrecorded currency - 40,000
            // meaning dinars - and copying it into a dollar cell reads as
            // $40,000. The cost comes from `cost_usd` below or not at all.
            // (Before the repaint fix this was invisible rather than absent,
            // which is why it surfaced only once the cell started showing.)
            if (k === "cost" || k === "cost_ccy") continue;
            if (window.ITEM_META_KEYS.has(k)) continue;          // not a column; applied below
            if (k === "image_refs") continue;        // fetched asynchronously below
            if (s.data[k] !== undefined && s.data[k] !== null && s.data[k] !== "") {
                row[k] = s.data[k];
            }
        }
        /* Master Data stores one selling price for the item. A quotation
           always sells in USD, so translate that canonical value into the
           document's Rate cell instead of copying `price_native` as a hidden
           phantom field. The helper also supports the older `rate` shape. */
        const pr = window.priceFromMemory(s.data, "USD", quotePegSync());
        if (pr !== null) row.rate = pr;
        /* The cost is filled in the CURRENCY IT WAS SAVED IN - an item costed
           in dinars lands as dinars, and the cell's own $/IQD toggle shows it.
           No conversion (2026-08-20): a cost typed in IQD must not be silently
           restated in dollars. Older entries with no recorded currency fall
           back to the safe converter, which still refuses a blind raw cost. */
        const cc = window.costCellFromMemory(s.data, quoteState.costCurrency, quotePegSync());
        if (cc) { row.cost = cc.value; row.cost_ccy = cc.ccy; }
    }
    // Update DOM in place (keep focus context) - re-render existing cells only.
    const tr = document.querySelector(`#qRowsBody tr[data-i="${ri}"]`);
    if (tr) {
        /* quoteRenderColumns(), NOT editableColumns(): the COST column is
           synthetic and absent from the editable list, so a remembered cost
           was written into state and then never painted into the cell. That is
           the whole reason picking an item appeared to ignore the cost. */
        quoteRenderColumns().forEach(c => {
            if (c.kind === "computed" || c.kind === "image") return;
            const el = tr.querySelector(`[data-k="${c.key}"]`);
            if (el && "value" in el) {
                el.value = row[c.key] || "";
                if (el.tagName === "TEXTAREA") autoResizeTextarea(el);
            }
        });
        /* The chip is NOT a `data-k` cell, so the repaint above cannot touch it
           (2026-08-26). A remembered dinar cost therefore landed in the cell
           with the chip still reading "$" - the number said one thing and the
           toggle another, which is the one thing this cell exists to prevent. */
        window.paintRowCostChip(tr, window.rowCostCurrency(row, quoteState.costCurrency));
    }
    // The picture is a REFERENCE, not bytes - the base64 is kept out of the
    // shared library on purpose (see backend/item_images). Fetching it is
    // async, so the row is already usable and the image lands a moment later.
    restoreRememberedImages(ri, s.data && s.data.image_refs);

    hideQuoteAc();
    quoteState.dirty = true;
    updateQuoteTotals();
    if (ri === quoteState.rows.length - 1) addQuoteRow();
}

// Keyboard support for the ac-box while it's open.
document.addEventListener("keydown", (e) => {
    const box = document.getElementById("acBox");
    if (!qacTarget || !box || box.hidden) return;
    if (e.key === "ArrowDown")      { e.preventDefault(); qacIdx = Math.min(qacIdx + 1, qacItems.length - 1); refreshQacActive(); }
    else if (e.key === "ArrowUp")   { e.preventDefault(); qacIdx = Math.max(qacIdx - 1, 0); refreshQacActive(); }
    else if (e.key === "Enter" && qacIdx >= 0) { e.preventDefault(); pickQuoteSuggestion(qacIdx); }
    else if (e.key === "Escape")    { hideQuoteAc(); }
});
function refreshQacActive() {
    document.querySelectorAll("#acBox .ac-item")
        .forEach((el, i) => el.classList.toggle("active", i === qacIdx));
}

function wireQuoteRow(tr) {
    const i = +tr.dataset.i;
    const pk = quoteState.schema && quoteState.schema.primary_key;
    tr.querySelectorAll("input, textarea").forEach(field => {
        const k = field.dataset.k;
        if (field.tagName === "TEXTAREA") autoResizeTextarea(field);
        field.addEventListener("input", () => {
            quoteState.rows[i][k] = field.value;
            quoteState.dirty = true;
            if (field.tagName === "TEXTAREA") autoResizeTextarea(field);
            if (k === pk) onPrimaryInput(field, i);       // per-type rich suggestions (fills whole row)
            if (i === quoteState.rows.length - 1 && hasQuoteContent(quoteState.rows[i])) addQuoteRow();
            updateQuoteTotals();
        });
        if (k === pk) {
            field.addEventListener("focus", () => {
                if (field.value.trim()) onPrimaryInput(field, i);
            });
            field.addEventListener("blur", () => setTimeout(hideQuoteAc, 150));
        } else {
            // Phase G.14: per-column autocomplete on every non-primary text-ish input.
            // On pick, fill ONLY this cell. Skipped for image/computed cells (they
            // aren't inputs anyway).
            const colDef = (quoteState.schema.columns || []).find(c => c.key === k);
            const kind = colDef && colDef.kind;
            if (kind === "text" || kind === "num" || kind === "money") {
                window.wireUniversalAutocomplete && window.wireUniversalAutocomplete(field, {
                    docType: "quote",
                    subType: quoteState.type,
                    colKey:  k,
                    isPrimary: false,
                    onPick: (val) => {
                        field.value = val;
                        quoteState.rows[i][k] = val;
                        quoteState.dirty = true;
                        if (field.tagName === "TEXTAREA") autoResizeTextarea(field);
                        updateQuoteTotals();
                    },
                });
            }
        }
    });

    // Per-line cost currency.
    const ccyBtn = tr.querySelector(".cost-ccy");
    if (ccyBtn) ccyBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleQuoteRowCostCurrency(i);
    });

    // Pick / paste / drop / crop all live in app.js so the PO editor runs the
    // same code; this only says WHERE the value is kept.
    tr.querySelectorAll(".qc-img-cell").forEach(cell => {
        const k = cell.dataset.k;
        window.wireRowImageCell(cell, {
            get: () => quoteState.rows[i][k],
            set: (v) => { quoteState.rows[i][k] = v; quoteState.dirty = true; },
        });
    });

    tr.querySelector(".del-btn").addEventListener("click", () => removeQuoteRow(+tr.dataset.i));
}

// Image upload / paste / crop moved to static/app.js (2026-08-05) -
// rowImageCellHTML / wireRowImageCell / openRowImageCropper - so the PO editor
// could gain the same image column without a second copy of the pipeline.

/* Pull remembered pictures back out of the content-addressed store and paint
   them into the row. Guarded on the bridge method because get_item_image is
   NEW: F5 without restarting the app pairs new JS with old Python, and an
   unguarded call would throw mid-pick and abandon the rest of the autofill. */
async function restoreRememberedImages(rowIdx, refs) {
    if (!refs || typeof refs !== "object") return;
    let getImage;
    try { getImage = api().get_item_image; } catch (e) { return; }
    if (typeof getImage !== "function") return;

    for (const [key, ref] of Object.entries(refs)) {
        if (!ref) continue;
        let uri = "";
        try { uri = await api().get_item_image(ref); }
        catch (e) { continue; }               // deleted from disk -> no picture
        if (!uri) continue;
        const row = quoteState.rows[rowIdx];
        if (!row) return;                     // row deleted while we waited
        row[key] = uri;
        const tr = document.querySelector(`#qRowsBody tr[data-i="${rowIdx}"]`);
        const cell = tr && tr.querySelector(`.qc-img-cell[data-k="${key}"]`);
        if (cell) {
            const thumb = cell.querySelector(".qc-img-thumb");
            const empty = cell.querySelector(".qc-img-empty");
            const clear = cell.querySelector(".qc-img-clear");
            if (thumb && empty && clear) {
                thumb.src = uri; thumb.hidden = false;
                empty.hidden = true; clear.hidden = false;
            }
        }
    }
}

/* Flip ONE line's cost currency, converting the number so the real cost is
   unchanged - only the unit it is written in changes. Mirrors what the
   document-level switch does, but for a single row. */
function toggleQuoteRowCostCurrency(i) {
    const row = quoteState.rows[i];
    if (!row) return;
    const to = window.toggleRowCostCurrency(row, quoteState.costCurrency, quotePegSync());
    quoteState.dirty = true;

    const tr = document.querySelector(`#qRowsBody tr[data-i="${i}"]`);
    if (tr) {
        const inp = tr.querySelector('input[data-k="cost"]');
        if (inp) inp.value = row.cost != null ? row.cost : "";
        window.paintRowCostChip(tr, to);
    }
    updateQuoteTotals();
}

function removeQuoteRow(i) {
    quoteState.rows.splice(i, 1);
    quoteState.dirty = true;
    fullRenderQuoteRows();
    if (quoteState.rows.length === 0) addQuoteRow();
    updateQuoteTotals();
}

function fullRenderQuoteRows() {
    const body = document.getElementById("qRowsBody");
    body.innerHTML = "";
    quoteState.rows.forEach((_, i) => body.appendChild(buildQuoteRowTr(i)));
    updateQuoteTotals();
}

function focusFirstCol(i) {
    const tr = document.querySelector(`#qRowsBody tr[data-i="${i}"]`);
    if (!tr) return;
    const inp = tr.querySelector("input");
    if (inp) inp.focus();
}

function hasQuoteContent(row) {
    for (const c of editableColumns()) {
        if (c.kind === "computed") continue;
        const v = (row[c.key] || "").toString().trim();
        const def = (c.default || "").toString().trim();
        if (v && v !== def) return true;     // ignore default-only values
    }
    return false;
}
function filledQuoteRows() { return quoteState.rows.filter(hasQuoteContent); }

function qmoney(v) {
    return "$" + (v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* Live cost / sell / profit / margin under the footer. PRIVATE numbers - they
   never reach the PDF. The maths lives in app.js so all three editors and the
   Costing screen agree; see window.computeDocEcon. */
function renderQuoteEconCards() {
    if (!window.computeDocEcon || !window.renderEconCards) return;
    const cur = quoteState.costCurrency || "USD";
    const peg = quotePegSync();
    const econ = window.computeDocEcon({
        rows: quoteState.rows || [],
        extraCosts: quoteState.extraCosts || [],
        costCurrency: cur,
        costPeg: peg,
        // A quotation always prices in USD - only invoices can be issued in IQD.
        sellCurrency: "USD",
        // Names the losing lines the way the backend does.
        primaryKey: (quoteState.schema && quoteState.schema.primary_key) || "item",
    });
    // Per-line profit / loss in the PROFIT column (2026-08-25).
    if (window.paintRowEconCells) window.paintRowEconCells("qRowsBody", econ);
    window.renderEconCards(document.getElementById("qEconCards"), econ, {
        onPickRow: (i) => window.focusEditorRow && window.focusEditorRow("qRowsBody", i),
        // The rate is a BUTTON, not text: profit for the whole quotation is
        // derived from it, and the market moves. See changeQuoteCostPeg.
        noteHTML: cur === "IQD"
            ? `Costs entered in IQD, converted at 1 $ = <button type="button" class="peg-chip peg-inline" data-act="quote-peg">${peg.toLocaleString("en-US")}</button> IQD${
                  _quoteRateAccount()}`
            : "",
    });
    const pegBtn = document.querySelector('#qEconCards [data-act="quote-peg"]');
    if (pegBtn) pegBtn.onclick = safe(() => changeQuoteCostPeg(), "Change rate");
}
/* The account that rate belongs to, named beside it (2026-09-21). Empty until
   the answer arrives, then the cards repaint - see rateAccountLabel. */
function _quoteRateAccount() {
    const client = document.getElementById("qClient");
    const label = window.rateAccountLabel
        ? window.rateAccountLabel("quote", client ? client.value : "", "",
                                  () => renderQuoteEconCards())
        : "";
    return label ? ` (${esc(label)})` : "";
}

window.renderQuoteEconCards = renderQuoteEconCards;

function updateQuoteTotals() {
    const hasGrand = !!(quoteState.schema && quoteState.schema.grand_total);
    let grand = 0;
    quoteState.rows.forEach((r, i) => {
        if (!hasGrand) return;
        const t = (parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0);
        grand += t;
        const tr = document.querySelector(`#qRowsBody tr[data-i="${i}"]`);
        if (tr) {
            const cell = tr.querySelector(".qc-total");
            if (cell) cell.textContent = (parseFloat(r.qty) || parseFloat(r.rate)) ? qmoney(t) : "";
        }
    });
    if (hasGrand) document.getElementById("qGrandTotal").textContent = qmoney(grand);
    document.getElementById("qRowCount").textContent = filledQuoteRows().length;
    renderQuoteEconCards();
    refreshQuoteSaved();
}

// ============================================================
// Phase M.8 - per-type drafts + unsaved indicator
// ============================================================
const quoteDraftUnsaved = {};   // type -> bool (has an unsaved stashed draft)

function refreshQuoteSaved() {
    window.setUnsavedChip(quoteState.dirty);
    if (window.setEditorSavedState) {
        window.setEditorSavedState(quoteState.mode === "edit" && !quoteState.dirty);
    }
}
window.refreshQuoteSaved = refreshQuoteSaved;

function discardQuoteDraft(type) {
    if (!type) return;
    quoteState.drafts = quoteState.drafts || {};
    delete quoteState.drafts[type];
    quoteDraftUnsaved[type] = false;
}

function stashQuoteDraftIfDirty(type) {
    if (!type) return;
    quoteState.drafts = quoteState.drafts || {};
    if (quoteState.dirty) {
        quoteState.drafts[type] = snapshotQuoteDraft();
        quoteDraftUnsaved[type] = true;
    } else {
        discardQuoteDraft(type);
    }
}

function quoteRailItemsBadged(activeKey) {
    return quoteRailItems().map(it => ({
        ...it,
        badge: !!quoteDraftUnsaved[it.key] && it.key !== activeKey,
    }));
}

function snapshotQuoteDraft() {
    const g = id => { const e = document.getElementById(id); return e ? e.value : ""; };
    return {
        rows: (quoteState.rows || []).map(r => ({ ...r })),
        columns: _cloneCols(quoteState.columns),   // Phase T - per-doc columns
        terms: (quoteState.terms || []).slice(),
        signingOverlay: quoteState.signingOverlay || null,
        autofitRows: !!quoteState.autofitRows,
        mode: quoteState.mode, editQuoteNo: quoteState.editQuoteNo,
        numberOverridden: quoteState.numberOverridden, dirty: quoteState.dirty,
        date: quoteState.date, quoteNo: quoteState.quoteNo,
        no: g("qNo"), client: g("qClient"), rfq: g("qRfq"),
        address: g("qAddress"), dateInput: g("qDate"),
    };
}

function restoreQuoteDraft(d) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("qut");
    const s = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
    quoteState.rows = (d.rows || []).map(r => ({ ...r }));
    quoteState.columns = (d.columns && d.columns.length) ? _cloneCols(d.columns) : _cloneCols(_stockColumns());
    quoteState.terms = (d.terms || []).slice();
    quoteState.signingOverlay = d.signingOverlay || null;
    quoteState.autofitRows = quoteLayoutAutofit(
        Object.prototype.hasOwnProperty.call(d, "autofitRows")
            ? { autofit_rows: d.autofitRows } : null,
        quoteDefaultAutofit()
    );
    quoteRefreshSignChip();
    quoteSyncAutofitButton();
    quoteState.mode = d.mode || "new";
    quoteState.editQuoteNo = d.editQuoteNo || null;
    quoteState.numberOverridden = !!d.numberOverridden;
    quoteState.dirty = !!d.dirty;
    quoteState.date = d.date || "";
    quoteState.quoteNo = d.quoteNo || d.no || "";
    s("qNo", d.no || ""); s("qClient", d.client || ""); s("qRfq", d.rfq || "N/A");
    s("qAddress", d.address || ""); s("qDate", d.dateInput || "");
    window.setModeChip(quoteState.mode === "edit", "Edit mode");
    quoteState.initialized = true;
    fullRenderQuoteRows();
    if (!quoteState.rows.length || hasQuoteContent(quoteState.rows[quoteState.rows.length - 1])) addQuoteRow();
    updateTermsCount();
    updateQuoteTotals();
    populateClientList();
}

// ============================================================
// Terms editor
// ============================================================
function openTermsEditor() {
    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). "Reset to default"
       acts without closing (`keep`) - it refills the list so you can see what
       you are about to save. */
    const _h = window.Popup.open({
        /* The biggest size on the list: this is a WRITING surface, not a
           message. A dozen terms at 680px wrap mid-sentence, and a term you
           cannot read back is a term you cannot check before it prints on a
           quotation. */
        title: "Terms & Conditions",
        size: "xl",
        dir: "ltr",
        className: "terms-modal",
        html: true,
        body: `<div class="terms-list" id="termsList"></div>`
            + `<button class="ghost terms-add" id="termsAdd">`
            + `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`
            + ` Add term</button>`,
        actions: [
            { label: "Reset to default", value: "reset", cls: "ghost", keep: true },
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: "Save terms", value: "save", cls: "primary", keep: true },
        ],
    });
    const backdrop = _h.panel;

    const listEl = backdrop.querySelector("#termsList");
    function addTermRow(text = "") {
        const row = document.createElement("div");
        row.className = "terms-row";
        /* A TEXTAREA, not an input: terms run to two or three lines and a
           single-line box hides everything past the caret. It grows with the
           text so the whole term is readable while it is being edited. */
        row.innerHTML = `<span class="terms-num"></span>
            <textarea rows="2" placeholder="Enter a term">${esc(text)}</textarea>
            <button class="del-btn" title="remove">${window.ICON ? window.ICON.trash : "×"}</button>`;
        const ta = row.querySelector("textarea");
        const grow = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; };
        ta.addEventListener("input", grow);
        setTimeout(grow, 0);
        row.querySelector(".del-btn").onclick = () => { row.remove(); renumber(); };
        listEl.appendChild(row);
        renumber();
    }
    function renumber() {
        [...listEl.querySelectorAll(".terms-row .terms-num")].forEach((s, i) => s.textContent = (i + 1) + ".");
    }
    function load(terms) { listEl.innerHTML = ""; terms.forEach(t => addTermRow(t)); }

    load(quoteState.terms.length ? quoteState.terms : (quoteState.schema.terms || []));
    backdrop.querySelector("#termsAdd").onclick = () => addTermRow("");
    backdrop.querySelector("[data-value='reset']").onclick = () => load(quoteState.schema.terms || []);

    const close = () => _h.close(null);
    backdrop.querySelector("[data-value='save']").onclick = () => {
        // `.terms-row textarea` - the rows became textareas when this popup grew
        // (2026-08-26). Reading `input` here would have saved an EMPTY terms
        // list without a word of complaint.
        quoteState.terms = [...listEl.querySelectorAll(".terms-row textarea")]
            .map(t => t.value.trim()).filter(Boolean);
        quoteState.dirty = true;
        updateTermsCount();
        close();
    };
}

function updateTermsCount() {
    document.getElementById("qTermsCount").textContent = (quoteState.terms || []).length;
}

// ============================================================
// Validation + payload + save/preview
// ============================================================
function buildQuotePayload() {
    const liveColumns = quoteSettingsColumnsSnapshot();
    quoteState.columns = _cloneCols(liveColumns);
    const liveColWidths = {};
    liveColumns.forEach(c => {
        if (typeof c.width === "number" && c.width > 0) liveColWidths[c.key] = c.width;
    });
    return {
        quote_no: quoteState.quoteNo,
        date: quoteState.date,
        quote_type: quoteState.type,
        client_name: document.getElementById("qClient").value.trim(),
        rfq_no: document.getElementById("qRfq").value.trim(),
        client_address: document.getElementById("qAddress").value.trim(),
        reference: document.getElementById("qReference").value.trim(),
        items: filledQuoteRows(),
        // The currency the COST cells are typed in. Sent explicitly because
        // set_cost_currency is only called AFTER save_quote returns - reading
        // it from the database during the save would see the PREVIOUS value.
        cost_currency: quoteState.costCurrency || "USD",
        cost_iqd_per_usd: quotePegSync(),
        terms: quoteState.terms,
        // Phase T - this document's own column layout (renamed/added columns).
        columns: _cloneCols(liveColumns),
        is_edit: quoteState.mode === "edit",
        signing_overlay: quoteState.signingOverlay || null,
        layout: quoteLayoutPayload(),
        // Live editor column widths (honours Excel-style resizing) so the
        // generated/previewed PDF matches the on-screen table. The same
        // widths are persisted inside `columns`, so reopening the doc restores
        // the dragged layout.
        col_widths: liveColWidths,
    };
}

function quoteRefreshSignChip() {
    if (window.setSignChip) {
        window.setSignChip("docSignChip", window.overlaySigned && window.overlaySigned(quoteState.signingOverlay));
    }
}
window.quoteRefreshSignChip = quoteRefreshSignChip;

if (window.signingRegister) {
    window.signingRegister({
        docType: "qut",
        getOverlay: () => quoteState.signingOverlay || null,
        setOverlay: (ov) => { quoteState.signingOverlay = ov; quoteState.dirty = true; quoteRefreshSignChip(); },
    });
}

// Bulk paste/import - insert parsed rows through the normal addQuoteRow path.
function quoteBulkInsert(objs, replace) {
    const tb = document.getElementById("qRowsBody");
    if (replace) {
        quoteState.rows = [];
        if (tb) tb.innerHTML = "";
    } else {
        // Drop trailing empty rows so imports don't land after a blank.
        while (quoteState.rows.length &&
               !hasQuoteContent(quoteState.rows[quoteState.rows.length - 1])) {
            quoteState.rows.pop();
            if (tb && tb.lastElementChild) tb.removeChild(tb.lastElementChild);
        }
    }
    (objs || []).forEach(o => addQuoteRow(o));
    // Keep one trailing empty row for continued typing.
    if (!quoteState.rows.length ||
        hasQuoteContent(quoteState.rows[quoteState.rows.length - 1])) addQuoteRow();
    quoteState.dirty = true;
    if (typeof refreshQuoteSaved === "function") refreshQuoteSaved();
    updateQuoteTotals();
}

if (window.bulkPasteRegister) {
    window.bulkPasteRegister({
        docType: "quote",
        buttonId: "btnQPaste",
        getColumns: () => editableColumns()
            .filter(c => !["computed", "image", "index"].includes(c.kind))
            .map(c => ({ key: c.key, head: c.head, kind: c.kind })),
        primaryKey: () => quoteState.schema && quoteState.schema.primary_key,
        addRows: quoteBulkInsert,
    });
}

async function infoModal(title, body, html = false) {
    await showModal({ title, body, html,
                      buttons: [{ label: "OK", value: "ok", cls: "primary" }] });
}

/* Which pricing columns THIS quote type actually has. The four types differ:
   Materials is a priced table (item/qty/rate/total, grand total on), while
   Equipment, Manpower and Service are RATE CARDS - no qty, no line total, no
   grand total. Demanding a quantity on a rate card would block every equipment
   quotation ever written, so the guard asks the schema rather than assuming. */
function _quoteStockKeys() {
    const sch = quoteState.schema || {};
    const stock = (sch.stock_columns && sch.stock_columns.length)
        ? sch.stock_columns : (sch.columns || []);
    return new Set(stock.map((c) => c && c.key));
}

function quotePricingKeys() {
    // Only keys this type prices from AND that this document still has. A rate
    // card the user rebuilt with custom columns has nothing here to check, and
    // that is correct - see quotePricingColumnsMissing.
    const stockKeys = _quoteStockKeys();
    const live = new Set((editableColumns() || []).map((c) => c && c.key));
    return ["qty", "rate"].filter((k) => stockKeys.has(k) && live.has(k));
}

/* Rows that carry content but no price. Returns [{n, label, missing:[]}].

   Same rule as the invoice (see inv.js invUnpricedRows) and the same reason: a
   line with a blank or zero rate quotes nothing for something real, silently -
   it still prints, the total is simply short. */
function quoteUnpricedRows() {
    const keys = quotePricingKeys();
    if (!keys.length) return [];
    const bad = [];
    quoteState.rows.forEach((row, i) => {
        if (!hasQuoteContent(row)) return;          // a blank spacer row is fine
        const missing = keys.filter((k) => {
            const v = parseFloat(row[k]);
            return !(Number.isFinite(v) && v > 0);
        });
        if (!missing.length) return;
        const pk = (quoteState.schema && quoteState.schema.primary_key) || "item";
        const label = String(row[pk] || row.description || "").trim().split("\n")[0];
        bad.push({ n: i + 1, label, missing });
    });
    return bad;
}
window.quoteUnpricedRows = quoteUnpricedRows;

/* A pricing column this TYPE normally has, which this document no longer does
   - i.e. it was deleted and replaced by a custom col_N look-alike. `kind`
   ("num"/"money") is a formatting hint and carries no meaning: updateQuoteTotals
   multiplies row.qty * row.rate BY KEY, so the replacement never totals.
   Renaming a heading is safe and deliberately not flagged - only the key counts. */
function quotePricingColumnsMissing() {
    /* ONLY where the app computes a number. Materials quotations total
       qty × rate and carry a grand total, so a replaced column silently
       produces a wrong figure and must never reach PDF output.

       Equipment, Manpower and Service do not: `grand_total` is false and there
       is no line total, so a rebuilt table prints exactly the columns it shows
       and nothing is computed to be wrong. Seven saved equipment quotations
       already look like this (QUT-EQ-HDEC-048 and friends) - blocking them
       would make the user's own documents unsaveable to fix a total that does
       not exist. Their real risk is a hand-typed total going stale, which is a
       different problem and not one a save guard can honestly police. */
    if (!(quoteState.schema && quoteState.schema.grand_total)) return [];
    const stockKeys = _quoteStockKeys();
    const live = new Set((editableColumns() || []).map((c) => c && c.key));
    return ["qty", "rate"].filter((k) => stockKeys.has(k) && !live.has(k));
}
window.quotePricingColumnsMissing = quotePricingColumnsMissing;

async function validateQuote(options = {}) {
    if (!document.getElementById("qClient").value.trim()) {
        await infoModal("Client required", "Please select or enter a client name."); return false;
    }
    if (!document.getElementById("qRfq").value.trim()) {
        await infoModal("RFQ No. is empty", "There is no RFQ No. Enter a value, or type 'N/A' if not applicable."); return false;
    }
    if (filledQuoteRows().length === 0) {
        await infoModal("No items", "Add at least one item before saving."); return false;
    }

    const replaced = quotePricingColumnsMissing();
    if (replaced.length) {
        if (options.allowIncomplete) return true;
        await infoModal(
            "This quotation cannot be priced",
            `<p>This type prices from the built-in `
            + `${replaced.map((k) => `<b>${esc(k === "qty" ? "Qty" : "Rate")}</b>`).join(" and ")} `
            + `column${replaced.length === 1 ? "" : "s"}, which ${replaced.length === 1
                ? "is no longer" : "are no longer"} in this table.</p>`
            + `<p>Renaming a heading is fine - the app follows the column itself, `
            + `not its title. But a <i>new</i> column headed "QTY" or "RATE" is only `
            + `text to the calculation, so every line would total zero.</p>`
            + `<p>Add the built-in column back from the columns menu, or rename the `
            + `existing one instead of replacing it.</p>`, true);
        return false;
    }

    /* Preview/Print stay strict, while Save calls with allowIncomplete and sends
       the table to the DB-only/no-PDF path. A quotation is a working document:
       the client's request list is often typed days before pricing. Measured on
       the real data, a hard save block would have made 4 of 46 quotations
       unsaveable, including a 169-line request list. */
    const unpriced = quoteUnpricedRows();
    if (unpriced.length) {
        if (options.allowIncomplete) return true;
        const list = unpriced.slice(0, 12).map((b) => {
            const what = b.missing
                .map((k) => (k === "qty" ? "quantity" : "rate")).join(" and ");
            return `<li>Row ${b.n}${b.label ? ` - ${esc(b.label)}` : ""}: no ${what}</li>`;
        }).join("");
        await infoModal(
            unpriced.length === 1 ? "1 item has no price"
                                  : `${unpriced.length} items have no price`,
            `<p>These lines cannot be included in a PDF yet:</p>`
            + `<ul class="inv-unpriced">${list}</ul>`
            + (unpriced.length > 12
                ? `<p>…and ${unpriced.length - 12} more.</p>` : "")
            + `<p>Use <b>Save</b> to keep this as an app-only draft, or complete `
            + `the pricing before Preview or Print.</p>`, true);
        return false;
    }
    return true;
}

async function quotePreview() {
    if (!(await validateQuote())) return;
    const btn = document.getElementById("btnQPreview");
    await withLoading(btn, async () => {
        const r = await api().preview_quote(buildQuotePayload());
        if (!r.ok) { toast("Preview error: " + r.error, true); return; }
        await api().open_path(r.path);
    });
}

async function quotePrint() {
    if (!(await validateQuote())) return;
    const btn = null;                      // Print left the toolbar; the busy bar covers it
    await withLoading(btn, async () => {
        const r = await api().preview_quote(buildQuotePayload());   // renders the PDF
        if (!r.ok) { toast("Print error: " + r.error, true); return; }
        const pr = await api().print_file(r.path);
        toast(pr && pr.ok ? "Sent to printer." : (pr && pr.error) || "Print failed", !(pr && pr.ok));
    });
}

async function quoteSave(opts = {}) {
    if (!(await validateQuote({ allowIncomplete: true }))) return false;

    const missingPricingColumns = quotePricingColumnsMissing();
    const unpricedRows = quoteUnpricedRows();
    const incompletePricing = missingPricingColumns.length > 0 || unpricedRows.length > 0;

    // Pick up the latest typed number from the input (source of truth).
    const typed = (document.getElementById("qNo").value || "").trim();
    if (!typed) { toast("Quotation No. is empty", true); return false; }
    quoteState.quoteNo = typed;

    // Uniqueness check (skip when the user kept the same number in edit mode).
    const sameAsExisting = (quoteState.mode === "edit" && quoteState.editQuoteNo === typed);
    if (!sameAsExisting) {
        const exists = await api().check_quote_exists(typed);
        if (exists.exists) {
            const choice = await showModal({
                title: "Quotation number exists",
                body: `A quotation with number ${typed} already exists. Saving will overwrite it. Continue?`,
                buttons: [
                    { label: "Cancel",    value: "cancel", cls: "ghost" },
                    { label: "Overwrite", value: "ok",     cls: "primary" },
                ],
            });
            if (choice !== "ok") return false;
        }
    }

    if (!opts.skipConfirm && incompletePricing) {
        const details = missingPricingColumns.length
            ? `The built-in ${missingPricingColumns.map((k) => k === "qty" ? "Qty" : "Rate").join(" and ")} column${missingPricingColumns.length === 1 ? " is" : "s are"} missing.`
            : `${unpricedRows.length} item${unpricedRows.length === 1 ? " has" : "s have"} no complete quantity and rate.`;
        const choice = await showModal({
            title: "Save incomplete draft?",
            body: `${details} The document will be saved inside the app, but no PDF will be created until pricing is complete.`,
            buttons: [
                { label: "Go back", value: "cancel", cls: "ghost" },
                { label: "Save draft (no PDF)", value: "ok", cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    } else if (!opts.skipConfirm) {
        const choice = await showModal({
            title: "Save Quotation",
            body: `Save ${quoteState.quoteNo}${quoteState.mode === "edit" ? " (edit)" : ""}?`,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Save",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    }
    // btnQSave was the Export button, removed in the 2026-07-24 toolbar
    // trim, so the old ternary resolved to a missing element on that path.
    const btn = document.getElementById("btnQStay");
    let r;
    let payload = { ...buildQuotePayload(), save_without_pdf: incompletePricing };
    await withLoading(btn, async () => { r = await api().save_quote(payload); });
    /* The client is not on file: the backend refuses rather than inventing one
       (2026-09-11). `offer` adds them on the spot if the user wants, and only
       then do we save again - with a freshly built payload, because adding the
       client also rewrites the client box to the name documents print. */
    if (window.ClientRequired && await window.ClientRequired.offer(r, "qut")) {
        payload = { ...buildQuotePayload(), save_without_pdf: incompletePricing };
        await withLoading(btn, async () => { r = await api().save_quote(payload); });
    }
    /* Someone else saved this number a moment ago (multi-user, 2026-10-22):
       say so and fetch the next one, instead of a bare 'Save error' that
       leaves the stale number in the box. */
    if (r && r.number_taken) { toast(r.error, true); await refreshQuoteNumber(); return false; }
    if (!r || !r.ok) { toast("Save error: " + (r && r.error || "unknown"), true); return false; }
    quoteState.dirty = false;
    // Phase R - persist the cost-entry currency alongside the saved costs so the
    // Costing screen reads them in the same currency. ALWAYS persist (USD clears
    // the row): a conditional could leave a stale IQD flag on USD numbers after
    // a flip-then-toggle-off, which would divide real USD costs by 1400.
    try { await api().set_cost_currency("quote", r.quote_no, quoteState.costCurrency || "USD"); }
    catch (e) {}
    if (window.refreshQuoteSaved) refreshQuoteSaved();
    // Export (not Stay) runs the user's "after export" action (copy / open /
    // reveal / nothing - see Settings → Behaviour).
    if (r.incomplete_price) {
        toast(`Saved in app: ${r.quote_no}. PDF was not created until pricing is complete.`);
    } else if (!opts.stay && r.path) {
        await window.afterExportPdf(r.path, r.quote_no);
    } else {
        toast("Saved: " + r.quote_no);
    }
    if (opts.stay) {
        // Stay in editor - transition to edit mode so future Saves update
        // the same record.
        quoteState.mode = "edit";
        quoteState.editQuoteNo = r.quote_no;
        quoteState.quoteNo = r.quote_no;
        const el = document.getElementById("qNo");
        if (el) el.value = r.quote_no;
        refreshQuoteSaved();
    } else {
        quoteState.initialized = false;
        await initNewQuote();
    }
    if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect the save in the panel
    /* The quotation is saved and reported. NOW offer to complete the ARABIC
       half of any item the catalogue only knows in English, so the same goods
       are FOUND when they are later ordered on an Arabic purchase order -
       82% of quoted items have no Arabic name today, and every one of them is
       invisible to a PO lookup, which is how a second record gets created for
       something already on file. The mirror of what `savePO` does, sharing one
       implementation ([[07-FRONTEND-SUPPORT]]).

       Deliberately after the save and after every `return`-worthy failure: it
       must never make a successful save look failed, so it is awaited and its
       outcome ignored. */
    if (window.quoteReviewNewItems && r.quote_no) {
        try { await window.quoteReviewNewItems(r.quote_no); } catch (e) {}
    }
    return true;
}

// ============================================================
// Open existing quotations
// ============================================================
function quoteOpen() {
    // Phase M.6 - the Open view is replaced by the always-visible right panel.
    // The topbar Open button just uncollapses the panel + focuses search.
    if (window.OpenPanel) window.OpenPanel.focusSearch();
    else refreshQuoteList();   // graceful fallback if panel hasn't booted yet
}
window.quoteOpen = quoteOpen;

// Open-view filter state. Two independent chip rows.
let qOpenAllQuotes = [];
let qOpenTypeFilter   = "all";   // "all" | MT/EQ/MP/SV
let qOpenClientFilter = "all";   // "all" | client name

const QTYPE_LABELS = { MT: "Materials", EQ: "Equipment", MP: "Manpower", SV: "Service" };
const QTYPE_KEY_TO_CODE = { materials: "MT", equipment: "EQ", manpower: "MP", service: "SV" };

function _quoteTypeCode(typeKey) {
    const sch = quoteState.types && quoteState.types[typeKey];
    if (sch && sch.code) return String(sch.code).toUpperCase();
    return QTYPE_KEY_TO_CODE[typeKey] || "all";
}

// Phase M.8d - the horizontal type bar above the editor drives the Open
// panel's type filter. When the user switches type (or opens a doc), point
// the panel at that type's code and re-render the list (only if the panel's
// data is already loaded - otherwise refreshQuoteList will pick it up).
function syncQuotePanelType() {
    // Only follow the editor's sub-type when the loaded quotes actually have
    // types. For a typeless family (Eshraqat) keep the filter on "all" so the
    // saved docs stay visible in the panel.
    const hasTypes = Array.isArray(qOpenAllQuotes) && qOpenAllQuotes.some(q => q.type_code);
    qOpenTypeFilter = hasTypes ? _quoteTypeCode(quoteState.type) : "all";
    if (Array.isArray(qOpenAllQuotes) && qOpenAllQuotes.length) {
        renderQuoteFilterChips();
        renderQuoteListFiltered();
    }
}
window.syncQuotePanelType = syncQuotePanelType;

function _qClientOf(q) { return q.client_name || q.client || ""; }

function renderQuoteFilterChips() {
    // Apply ONLY the other-axis filter so each chip's count reflects what
    // the user would see if they clicked it.
    const afterClient = qOpenAllQuotes.filter(q =>
        qOpenClientFilter === "all" || _qClientOf(q) === qOpenClientFilter);
    // Some profiles (e.g. Eshraqat) save a single typeless "Quotation" family -
    // no MT/EQ/MP/SV. Only show the type chip row when at least one quote
    // actually carries a type code; otherwise hide it and never filter by type.
    const hasTypes = qOpenAllQuotes.some(q => q.type_code);
    const typeChipsEl = document.getElementById("qFilterChips");
    if (!hasTypes) {
        qOpenTypeFilter = "all";
        if (typeChipsEl) typeChipsEl.innerHTML = "";
    } else {
        const typeCounts = countByKey(afterClient, q => (q.type_code || "").toUpperCase());
        const typeOptions = [
            { key: "all", label: "All", count: afterClient.length },
            // Phase M.8c - short codes in chips (MT, EQ, MP, SV). The full names
            // (Materials, Equipment…) still appear in the type-rail strip and the
            // empty-state copy; here we just need to fit four pills on one row.
            ...["MT","EQ","MP","SV"].map(k => ({ key: k, label: k, title: QTYPE_LABELS[k], count: typeCounts[k] || 0 })),
        ];
        renderChipRow(typeChipsEl, typeOptions, qOpenTypeFilter, key => {
            qOpenTypeFilter = key;
            renderQuoteFilterChips();
            renderQuoteListFiltered();
        });
    }

    const afterType = qOpenAllQuotes.filter(q =>
        qOpenTypeFilter === "all" || (q.type_code || "").toUpperCase() === qOpenTypeFilter);
    const clientCounts = countByKey(afterType, _qClientOf);
    const clientOptions = [
        { key: "all", label: "All", count: afterType.length },
        ...uniqueSortedKeys(qOpenAllQuotes, _qClientOf)
            .map(c => ({ key: c, label: c, count: clientCounts[c] || 0 })),
    ];
    renderChipRow(document.getElementById("qClientChips"), clientOptions, qOpenClientFilter, key => {
        qOpenClientFilter = key;
        renderQuoteFilterChips();
        renderQuoteListFiltered();
    });
}

function renderQuoteListFiltered() {
    const box = document.getElementById("qList");
    const list = qOpenAllQuotes.filter(q =>
        // A typeless doc (no type_code - e.g. Eshraqat "Quotation") always passes
        // the type filter so it can never be hidden by an active MT/EQ/MP/SV chip.
        (qOpenTypeFilter   === "all" || !q.type_code || (q.type_code || "").toUpperCase() === qOpenTypeFilter) &&
        (qOpenClientFilter === "all" || _qClientOf(q) === qOpenClientFilter));

    box.innerHTML = "";
    // The unsaved duplicate, if this pane is its home. Rendered BEFORE the
    // empty-state check (a month whose only document is the duplicate would
    // otherwise return early and show nothing) and OUTSIDE the filters - it
    // is the document you are working on, so a type or client chip must not
    // hide it. The UNFILTERED list is passed on purpose: it is what decides
    // the duplicate has been saved and the provisional row can go.
    const pendingRow = window.pendingRowEl
        ? window.pendingRowEl("quote", +document.getElementById("qOpenYear").value,
                              +document.getElementById("qOpenMonth").value, qOpenAllQuotes, "quote_no")
        : null;
    if (!list.length && !pendingRow) {
        const tlabel = qOpenTypeFilter === "all" ? "" : (QTYPE_LABELS[qOpenTypeFilter] + " ");
        const clabel = qOpenClientFilter === "all" ? "" : ` for ${esc(qOpenClientFilter)}`;
        box.innerHTML = `<div class="empty-state"><div class="icon">∅</div>
            <div class="title">No ${esc(tlabel)}quotations${clabel} this month</div></div>`;
        return;
    }
    if (pendingRow) box.appendChild(pendingRow);
    let _lastDay = null;
    list.forEach(q => {
        const dk = window.panelDayKey ? window.panelDayKey(q.created_at) : null;
        if (dk !== _lastDay) {
            if (window.panelDaySepEl) box.appendChild(window.panelDaySepEl(q.created_at));
            _lastDay = dk;
        }
        const row = document.createElement("div");
        row.className = "po-row" + (q.editable ? " is-clickable" : "");
        if (q.editable) {
            row.dataset.edit = q.quote_no;
            row.title = "Click row to open for editing";
        }
        const hasPdf = !!q.path && !q.incomplete_price;
        const submitBtn = hasPdf && window.submitBtnHTML ? window.submitBtnHTML("quote", q.quote_no, q.submitted) : "";
        const pdfActions = hasPdf ? `
                <button class="ghost" data-act="open" data-path="${esc(q.path)}">${window.ICON ? window.ICON.externalLink : ""}<span>Open PDF</span></button>
                <button class="ghost" data-act="reveal" data-path="${esc(q.path)}" title="Open containing folder">${window.ICON ? window.ICON.folder : ""}<span>Folder</span></button>
                <button class="ghost" data-act="copy" data-path="${esc(q.path)}" data-no="${esc(q.quote_no)}" data-signed="${q.signed ? "1" : "0"}" title="Copy file to clipboard">${window.ICON ? window.ICON.copy : ""}<span>Copy</span></button>
                <button class="ghost" data-act="print" data-path="${esc(q.path)}" title="Print to default printer">${window.ICON ? window.ICON.printer : ""}<span>Print</span></button>` : "";
        const statusHtml = window.panelStatusHTML ? window.panelStatusHTML("quote", q) : "";
        const dateLabel = q.editable ? (q.updated_at || "").slice(0, 10) : "view only";
        const cardMain = window.panelCardMainHTML
            ? window.panelCardMainHTML({ no: q.quote_no, date: dateLabel, reference: q.reference, statuses: statusHtml })
            : `<div class="panel-row-main"><div class="panel-row-head"><div class="po-num">${esc(q.quote_no)}</div><div class="panel-row-topmeta"><span>${esc(dateLabel)}</span></div></div><div class="panel-row-statuses">${statusHtml}</div></div>`;
        row.innerHTML = `
            ${cardMain}
            <div class="row-actions">
                ${pdfActions}
                <button class="ghost danger-text" data-act="delete" data-path="${esc(q.path)}" data-label="${esc(q.quote_no)}" title="Move to RECYCLE">${window.ICON ? window.ICON.trash : ""}<span>Delete</span></button>
                ${q.editable && window.duplicateBtnHTML ? window.duplicateBtnHTML("quote", q.quote_no) : ""}
                ${submitBtn}
            </div>`;
        box.appendChild(row);
    });
    if (window.wireSubmitButtons) window.wireSubmitButtons(box, qOpenAllQuotes, "quote_no");
    box.querySelectorAll("button[data-act='open']").forEach(b => b.onclick   = (e) => { e.stopPropagation(); api().open_path(b.dataset.path); });
    box.querySelectorAll("button[data-act='reveal']").forEach(b => b.onclick = (e) => { e.stopPropagation(); api().reveal_in_folder(b.dataset.path); });
    box.querySelectorAll("button[data-act='copy']").forEach(b => b.onclick   = safe((e) => { e.stopPropagation(); return window.copyDocumentFile("qut", b.dataset.no, b.dataset.path, b.dataset.signed === "1"); }, "Copy file"));
    box.querySelectorAll("button[data-act='print']").forEach(b => b.onclick  = (e) => { e.stopPropagation(); window.printPdfFile(b.dataset.path); });
    box.querySelectorAll("button[data-act='delete']").forEach(b => b.onclick = (e) => { e.stopPropagation(); window.confirmAndDeleteDocument("quote", b.dataset.label, b.dataset.path, b.dataset.label, refreshQuoteList); });
    if (window.wireDuplicateButtons) window.wireDuplicateButtons(box);
    // Phase M.5b - clicking anywhere on an editable card loads it for edit.
    box.querySelectorAll(".po-row.is-clickable").forEach(row => {
        row.onclick = safe(() => loadQuoteForEdit(row.dataset.edit), "Load quote");
    });
}

async function refreshQuoteList() {
    const box = document.getElementById("qList");
    box.innerHTML = `<div class="empty-state"><div class="title">Loading…</div></div>`;
    const y = +document.getElementById("qOpenYear").value;
    const m = +document.getElementById("qOpenMonth").value;
    qOpenAllQuotes = await api().list_quotes_for_month(y, m);
    // Phase M.8d - the type filter is owned by the type bar, so keep it
    // pinned to the active editor type even when this month has no docs of
    // that type (the list just shows an empty state for that type).
    // …but only when the loaded quotes have types; a typeless family
    // (Eshraqat) stays on "all" so its saved docs remain visible.
    qOpenTypeFilter = qOpenAllQuotes.some(q => q.type_code) ? _quoteTypeCode(quoteState.type) : "all";
    // Client filter still resets when it no longer matches anything.
    if (qOpenClientFilter !== "all"
        && !qOpenAllQuotes.some(q => _qClientOf(q) === qOpenClientFilter)) {
        qOpenClientFilter = "all";
    }
    renderQuoteFilterChips();
    renderQuoteListFiltered();
}

async function loadQuoteForEdit(quoteNo) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("qut");
    const rec = await api().load_quote(quoteNo);
    if (!rec) { toast("Cannot load quotation - not in database", true); return; }

    // Phase L.5 - lazy-load schema catalogue so Open from elsewhere works
    // even when the user hasn't visited the QUT mini-hub yet.
    if (!quoteState.types || !Object.keys(quoteState.types).length) {
        try { quoteState.types = await api().get_quote_types(); }
        catch { quoteState.types = {}; }
    }
    // Phase M.8 - stash any in-progress draft before replacing it.
    quoteState.drafts = quoteState.drafts || {};
    if (quoteState.initialized && quoteState.type) {
        stashQuoteDraftIfDirty(quoteState.type);
    }
    quoteState.type = rec.quote_type || "materials";
    quoteState.schema = quoteState.types[quoteState.type]
                        || quoteState.types["materials"]
                        || { label: quoteState.type, columns: [], terms: [] };
    quoteState.mode = "edit";
    quoteState.editQuoteNo = quoteNo;
    quoteState.quoteNo = quoteNo;
    // Phase R - seed the cost-entry currency from the saved doc.
    try { quoteState.costCurrency = await api().get_cost_currency("quote", quoteNo); }
    catch (e) { quoteState.costCurrency = "USD"; }
    // Phase T - this doc's own column layout if it has one; otherwise the
    // effective type schema (so un-migrated docs look exactly as they do now).
    quoteState.columns = (rec.columns && rec.columns.length)
        ? _cloneCols(rec.columns) : _cloneCols(quoteState.schema.columns || []);
    quoteState.date = rec.quote_date;
    quoteState.rows = (rec.rows || []).slice();
    quoteState.terms = (rec.terms && rec.terms.length) ? rec.terms.slice() : (quoteState.schema.terms || []).slice();
    quoteState.signingOverlay = rec.signing_overlay || null;
    quoteState.autofitRows = quoteLayoutAutofit(rec.layout, quoteDefaultAutofit());
    // Document-level costs are entered on the Costing screen and stored on the
    // row; load_quote returns the raw JSON. Omitting them would overstate the
    // profit card on every quote that has any.
    try { quoteState.extraCosts = JSON.parse(rec.extra_costs_json || "[]") || []; }
    catch (e) { quoteState.extraCosts = []; }
    // The pinned cost rate lives in doc_cost_meta, not on the quote row.
    quoteState.costPeg = null;
    // Guarded because get_cost_rate is a NEW bridge method: a user who presses
    // F5 without restarting the app has the new JS and the old Python, and an
    // unguarded call here would throw inside the loader and stop the quotation
    // opening at all. Degrading to the derived rate is the right failure.
    try {
        const getRate = api().get_cost_rate;
        if (typeof getRate === "function") {
            api().get_cost_rate("quote", quoteNo)
                .then(r => {
                    if (Number(r) > 0) { quoteState.costPeg = Number(r); renderQuoteEconCards(); }
                })
                .catch(() => {});
        }
    } catch (e) { /* old backend - fall back to the derived rate */ }
    quoteDocPeg();          // async; the cards re-render on the next keystroke
    quoteRefreshSignChip();
    quoteSyncAutofitButton();
    quoteState.dirty = false;
    quoteState.initialized = true;

    hideAllViews();
    document.getElementById("quoteView").hidden = false;
    document.getElementById("quoteActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Quotation - " + quoteState.schema.label);
    // AFTER hideAllViews() on purpose - it clears __currentEditorDoc, so setting
    // this any earlier leaves it null once the loader returns. That silently
    // broke the panel row highlight and made Duplicate a no-op (its guard reads
    // this to confirm the source actually loaded). See tests/duplicate_document.
    window.__currentEditorDoc = { kind: "quote", no: quoteNo };
    // Phase M.8 - keep the rail visible with the opened doc's type active.
    quoteDraftUnsaved[quoteState.type] = false;
    if (window.showTypeRail) showTypeRail("Quotation Types", quoteRailItemsBadged(quoteState.type), quoteState.type, (k) => enterQuoteType(k));
    refreshQuoteSaved();
    document.getElementById("qGrandWrap").style.display = quoteState.schema.grand_total ? "" : "none";

    buildQuoteThead();

    document.getElementById("qNo").value = quoteNo;
    quoteState.numberOverridden = true;     // editing existing -> don't auto-overwrite

    // See app.js - a bare `.value =` leaves quoteState.date on the loaded date,
    // so an edited date would never reach the save. quoteState.date was set
    // from rec.quote_date above, so this paints the same value AND wires it.
    bindDocDate("qDate", quoteState);
    document.getElementById("qClient").value = rec.client_name || "";
    document.getElementById("qRfq").value = rec.rfq_no || "N/A";
    document.getElementById("qAddress").value = rec.client_address || "";
    document.getElementById("qReference").value = rec.reference || "";
    window.setModeChip(true, "Edit mode");
    await populateClientList();

    fullRenderQuoteRows();
    addQuoteRow();
    updateTermsCount();
    updateQuoteTotals();
    setTimeout(() => window.autoSizeAllTextareas && window.autoSizeAllTextareas(document.getElementById("quoteView")), 0);
    // Phase M.8d - opened doc's type becomes the active type → sync the panel.
    syncQuotePanelType();
    // Put the panel on THIS document's month and scroll to its row, so
    // opening one from Needs Attention / search / anywhere does not leave
    // the panel on a different month (2026-10-01).
    if (window.revealDocInPanel) window.revealDocInPanel("quote", quoteNo, rec.quote_date);
}

/* Duplicate (2026-08-04) - turn the just-loaded quotation into a NEW unsaved
   one. Called by window.duplicateDocument after loadQuoteForEdit; see the
   DUPLICATE_HOOK block in app.js for why the clearing lives here.
   Rows, client, address, RFQ, type, columns, terms and layout stay as loaded.
   `numberOverridden = false` is deliberate: QUT numbers embed the client code,
   so changing the client afterwards must still re-derive the number. */
async function quoteMakeDuplicate() {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("qut");
    quoteState.mode = "new";
    quoteState.editQuoteNo = null;
    quoteState.numberOverridden = false;
    quoteState.signingOverlay = null;         // a copy has not been signed
    document.getElementById("qReference").value = "";
    quoteRefreshSignChip();
    quoteState.date = (window.state && state.today) ? state.today.display : quoteState.date;
    bindDocDate("qDate", quoteState);
    window.__currentEditorDoc = null;         // not a saved document yet
    window.setModeChip(false);
    await refreshQuoteNumber();
    refreshQuoteSaved();
    quoteState.dirty = true;                  // real unsaved content
    return quoteState.quoteNo || "";
}
window.quoteMakeDuplicate = quoteMakeDuplicate;

// ============================================================
// Phase M.5a - Excel-style grid paste on qRowsBody
// ============================================================
function _pushEmptyQuoteRow() {
    const row = {};
    editableColumns().forEach(c => { if (c.kind !== "computed") row[c.key] = (c.default || ""); });
    quoteState.rows.push(row);
}
function _wireQuoteGridPaste() {
    const tbody = document.getElementById("qRowsBody");
    if (!tbody || !window.setupGridPaste) return;
    const _renderAfterPaste = () => {
        fullRenderQuoteRows();
        if (filledQuoteRows().length === quoteState.rows.length) addQuoteRow();
        updateQuoteTotals();
        setTimeout(() => window.autoSizeAllTextareas
            && window.autoSizeAllTextareas(document.getElementById("quoteView")), 0);
    };
    window.setupGridPaste(tbody, {
        columns:     () => quoteRenderColumns(),
        rowCount:    () => quoteState.rows.length,
        addEmptyRow: () => _pushEmptyQuoteRow(),
        setCell:     (i, k, v) => {
            if (!quoteState.rows[i]) return;
            quoteState.rows[i][k] = v;
            quoteState.dirty = true;
        },
        fullRender:  _renderAfterPaste,
        // Phase M.5c - undo (Ctrl+Z) snapshot/restore. Deep clone so the
        // snapshot survives in-place mutation, then replace the rows array
        // wholesale on restore.
        snapshot: () => JSON.parse(JSON.stringify(quoteState.rows)),
        restore:  (snap) => {
            quoteState.rows = snap;
            quoteState.dirty = true;
            _renderAfterPaste();
        },
    });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wireQuoteGridPaste);
} else {
    _wireQuoteGridPaste();
}
