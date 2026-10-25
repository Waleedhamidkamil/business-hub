/* Invoice editor (English, LTR) - schema-driven for 4 types.
   Reuses shared helpers from app.js: api(), toast(), showModal(),
   withLoading(), esc(), safe(), hideAllViews(), setBrand(), window.ICON,
   window.copyFileToClipboard, and the global `state` (state.today). */

const invState = {
    type: "materials",
    schema: null,             // current type's schema {code,label,columns,primary_key}
    types: {},                // all type schemas from the backend
    invNo: "-",
    date: "-",
    rows: [],
    bankInfo: [],             // [{key,label,...}]
    bankKey: "",              // currently selected bank key
    withholdingPct: 3.3,
    withholdingIncluded: true,
    mode: "new",              // "new" | "edit"
    editInvNo: null,
    dirty: false,
    initialized: false,
    numberOverridden: false,
    addressOptions: [],
    company: null,
    sourceQuote: "",          // Phase N.3 - the quote this invoice belongs to
    sourceDn: "",             // transient Make-Invoice source for Workflow linking
    /* 2026-09-05 - the PO this invoice was made from, and WHICH of its lines,
       so the fulfillment link can be written once the invoice has a number.
       Transient in exactly the way `sourceDn` above is, and cleared in all the
       same places: a stale value would mark an unrelated PO's items invoiced on
       the next save. `{ poNo, indexes }` or null. */
    sourcePo: null,
    layoutDefault: {},
    autofitRows: false,
    costCurrency: "USD",      // cost-entry currency (USD/IQD) - private, costing only
    sellCurrency: "USD",      // 2026-07-29 - currency the CLIENT is billed in
    sellPeg: null,            // 2026-08-04 - IQD per $ this invoice was converted at
    hasSignedCopy: false,     // 2026-08-27 - the CLIENT's counter-signed scan
};
window.invState = invState;     // expose for app.js beforeunload check

let _invDirty = !!invState.dirty;
Object.defineProperty(invState, "dirty", {
    get() { return _invDirty; },
    set(v) {
        _invDirty = !!v;
        if (typeof refreshInvSaved === "function") refreshInvSaved();
    },
    configurable: true,
    enumerable: true,
});

window.getInvWorkflowContext = function () {
    return invState.mode === "edit" && invState.editInvNo
        ? { kind: "inv", no: invState.editInvNo }
        : null;
};

// ============================================================
// Signed copy (2026-08-27) - the counter-signed paper the CLIENT scans back,
// same feature the DN has had since 2026-07-24 and the same dialog
// (static/signed_copy.js). Distinct from the Sign button, which applies OUR
// digital signature: `signed` = we signed it, `client_signed` = they did.
// ============================================================
function _invSignedCopyBtn() { return document.getElementById("btnInvSignedCopy"); }

function refreshInvSignedCopyButton() {
    if (!window.SignedCopy) return;
    window.SignedCopy.paintButton(
        _invSignedCopyBtn(), "inv",
        invState.mode === "edit" && !!invState.editInvNo,
        !!invState.hasSignedCopy);
}
window.refreshInvSignedCopyButton = refreshInvSignedCopyButton;

async function reloadInvSignedCopy() {
    const rec = window.SignedCopy
        ? await window.SignedCopy.load("inv", invState.editInvNo)
        : null;
    invState.hasSignedCopy = !!rec;
    refreshInvSignedCopyButton();
    return rec;
}
window.reloadInvSignedCopy = reloadInvSignedCopy;

async function openInvSignedCopy() {
    const invNo = invState.editInvNo;
    if (!invNo) { toast("Save this Invoice first", true); return; }
    return window.SignedCopy.open("inv", invNo, {
        onChange: (rec) => {
            invState.hasSignedCopy = !!rec;
            refreshInvSignedCopyButton();
        },
    });
}
window.openInvSignedCopy = openInvSignedCopy;

function bindInvWorkflowButton() {
    const btn = document.getElementById("btnInvWorkflow");
    if (!btn || btn._workflowWired) return;
    btn._workflowWired = true;
    btn.onclick = () => {
        const ctx = window.getInvWorkflowContext();
        if (!ctx) { toast("Save this Invoice before opening Workflow", true); return; }
        if (window.openDocumentWorkflow) window.openDocumentWorkflow(ctx.kind, ctx.no);
    };
}

const IMONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
const ITYPE_ORDER = ["materials", "equipment", "manpower", "service"];

function invLayoutAutofit(layout, fallback = false) {
    if (window.layoutAutofitValue) return window.layoutAutofitValue(layout, fallback);
    if (layout && typeof layout === "object" &&
        Object.prototype.hasOwnProperty.call(layout, "autofit_rows")) {
        return !!layout.autofit_rows;
    }
    return !!fallback;
}

function invDefaultAutofit() {
    return invLayoutAutofit(invState.layoutDefault, false);
}

function invLayoutPayload() {
    return window.editorLayoutPayload
        ? window.editorLayoutPayload(invState.autofitRows)
        : { autofit_rows: !!invState.autofitRows };
}

function invSyncAutofitButton() {
    if (window.syncAutofitButton) {
        window.syncAutofitButton("btnInvAutofit", invState.autofitRows);
    }
}
window.invSyncAutofitButton = invSyncAutofitButton;

// ============================================================
// Module init (once, at bootstrap)
// ============================================================
async function initInvModule() {
    bindInvWorkflowButton();
    let d = { address_options: [], bank_info: [], company: null, withholding_pct: 3.3 };
    try { d = await api().get_inv_defaults(); } catch (e) { /* keep empties */ }
    invState.addressOptions   = d.address_options || [];
    invState.bankInfo         = d.bank_info || [];
    invState.company          = d.company || null;
    invState.withholdingPct   = d.withholding_pct ?? 3.3;
    invState.layoutDefault    = d.layout || {};
    invState.autofitRows      = invDefaultAutofit();
    try { invState.types = await api().get_inv_types(); } catch (e) { invState.types = {}; }

    document.getElementById("invAddrList").innerHTML =
        invState.addressOptions.map(a => `<option value="${esc(a)}">`).join("");
    // Populate bank dropdown
    const bankSel = document.getElementById("invBank");
    bankSel.innerHTML = invState.bankInfo
        .map(b => `<option value="${esc(b.key)}">${esc(b.label)}</option>`).join("");
    if (invState.bankInfo.length) invState.bankKey = invState.bankInfo[0].key;

    const sel = document.getElementById("invOpenMonth");
    sel.innerHTML = IMONTHS.map((n, i) => `<option value="${i + 1}">${n}</option>`).join("");
    if (state.today) {
        sel.value = state.today.month;
        document.getElementById("invOpenYear").value = state.today.year;
    }

    document.getElementById("btnInvNew").onclick         = safe(invNew,     "New invoice");
    { const b = document.getElementById("btnInvOpen"); if (b) b.onclick = safe(invOpen, "Open invoice"); }
    document.getElementById("btnInvImport").onclick      = safe(openImportDialog, "Import");
    const btnInvMasterFill = document.getElementById("btnInvMasterFill");
    if (btnInvMasterFill) btnInvMasterFill.onclick = safe(() => window.openMasterDataFill({
        docType: "inv",
        subType: invState.type,
        rows: invState.rows,
        primaryKey: (invState.schema && invState.schema.primary_key) || "item",
        costCurrency: invState.costCurrency || "USD",
        costPeg: invPegSync(),
        sellCurrency: invState.sellCurrency || "USD",
        sellPeg: invSellPeg(),
        render: () => fullRenderInvRows(),
        markDirty: () => { invState.dirty = true; },
    }), "Fill from Master Data");
    document.getElementById("btnInvPreview").onclick     = safe(invPreview, "Preview invoice");
    { const b = _invSignedCopyBtn(); if (b) b.onclick = safe(openInvSignedCopy, "Signed copy"); }
    { const b = document.getElementById("btnInvPrint"); if (b) b.onclick = safe(invPrint, "Print invoice"); }
    const btnInvStay = document.getElementById("btnInvStay");
    if (btnInvStay) btnInvStay.onclick                   = safe(() => invSave({ stay: true,  skipConfirm: true }), "Save invoice");
    const btnInvAutofit = document.getElementById("btnInvAutofit");
    if (btnInvAutofit) btnInvAutofit.onclick = safe(() => {
        invState.autofitRows = !invState.autofitRows;
        invState.dirty = true;
        invSyncAutofitButton();
        refreshInvSaved();
    }, "Auto-fit rows");
    if (window.wireFitColsButton) window.wireFitColsButton("btnInvFitCols", "#invThead", {
        pdf: {
            widthMm: 186,
            headerFont: 'bold 10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
            bodyFont: '10pt "Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif',
        },
        money: (key) => (invRenderColumns() || [])
            .some(c => c.key === key && (c.kind === "money" || c.kind === "computed")),
        // An IQD invoice prints "IQD 1,234,568" - wider than the dollar form,
        // and the fit must reserve for whichever this document actually uses.
        formatMoney: (v) => imoney(v),
        onFit: () => { invState.dirty = true; refreshInvSaved(); },
    });
    invSyncAutofitButton();
    document.getElementById("btnInvAddRow").onclick      = safe(() => {
        const last = invState.rows[invState.rows.length - 1];
        if (!last || hasInvContent(last)) addInvRow();
        focusInvFirstCol(invState.rows.length - 1);
    }, "Add row");
    document.getElementById("btnInvTypes").onclick       = safe(showInvHub, "Back to types");
    document.getElementById("btnInvRefreshList").onclick = safe(refreshInvList, "Refresh");
    document.getElementById("btnInvCloseOpen").onclick   = safe(showInvHub, "Back");

    // ⚙ on the INVOICE EDITOR (per-sub-type). The settings modal opens with
    // a sub-type-specific section for the currently-active type.
    const iSetBtn = document.getElementById("btnInvTypeSettings");
    if (iSetBtn) iSetBtn.onclick = safe(() => {
        if (!window.openDocSettings) { toast("Settings module not loaded", true); return; }
        const activeColumns = invSettingsColumnsSnapshot();
        invState.columns = _invCloneCols(activeColumns);
        window.openDocSettings("invoice", {
            subtype: invState.type,
            subtypeLabel: invState.schema && invState.schema.label,
            columns: activeColumns,
            onSaved: async (meta = {}) => {
                try {
                    // Refresh per-type schema, banks, withholding label.
                    invState.types = await api().get_inv_types();
                    invState.schema = invState.types[invState.type] || invState.schema;
                    if (meta.columnsChanged && invState.schema && invState.schema.columns) {
                        const before = JSON.stringify(invState.columns || []);
                        const nextColumns = _invCloneCols(invState.schema.columns || []);
                        invState.columns = nextColumns;
                        if (before !== JSON.stringify(nextColumns)) invState.dirty = true;
                    }
                    const d = await api().get_inv_defaults();
                    invState.bankInfo       = d.bank_info || [];
                    invState.withholdingPct = d.withholding_pct ?? 3.3;
                    invState.layoutDefault  = d.layout || invState.layoutDefault;
                    const bankSel = document.getElementById("invBank");
                    bankSel.innerHTML = invState.bankInfo
                        .map(b => `<option value="${esc(b.key)}">${esc(b.label)}</option>`).join("");
                    if (invState.bankInfo.length && !invState.bankInfo.find(b => b.key === invState.bankKey)) {
                        invState.bankKey = invState.bankInfo[0].key;
                        bankSel.value = invState.bankKey;
                    } else if (invState.bankKey) {
                        bankSel.value = invState.bankKey;
                    }
                    if (invState.schema) {
                        buildInvThead();
                        fullRenderInvRows();
                    }
                } catch {}
            },
        });
    }, "Invoice type settings");

    // Mini-hub back button → main hub.
    const iHubBack = document.getElementById("btnInvHubBack");
    if (iHubBack) iHubBack.onclick = safe(() => window.showHub && window.showHub(), "Back");

    document.getElementById("invClient").addEventListener("change", safe(refreshInvNumber, "Inv number"));
    document.getElementById("invReference").addEventListener("input", () => {
        invState.dirty = true;
    });
    bankSel.addEventListener("change", () => {
        invState.bankKey = bankSel.value;
        invState.dirty = true;
    });

    document.getElementById("invLpo").addEventListener("input", updateInvLpoWarning);
    // Pick the LPO from the ones this client actually sent (2026-09-11).
    // The number still prints on the document; what changes is that it can
    // no longer be a number nobody ever received - which is why 63 of 64
    // delivery notes said "N/A" and the LPO screen never lit up.
    const invLpoPick = document.getElementById("invLpoPick");
    if (invLpoPick) invLpoPick.onclick = async () => {
        if (!window.LpoPicker) return;
        const client = (document.getElementById("invClient") || {}).value || "";
        const picked = await window.LpoPicker.pick("invLpo", client.trim());
        updateInvLpoWarning();
        /* Picking the PO brings what the PO knows (2026-09-28) - see
           `adoptLpoIntoInv`. This used to stop at the number. */
        if (picked == null || window.LpoPicker.isNoLpo(picked)) return;
        await adoptLpoIntoInv(client.trim(), picked, { confirm: true });
    };
    document.getElementById("invLpo").addEventListener("change", updateInvLpoWarning);

    window.showInvHub  = showInvHub;
    window.enterInvType = enterInvType;
    window.invNew     = invNew;
    window.invOpen    = invOpen;
    window.invPreview = invPreview;
    window.invSave    = invSave;
}
window.initInvModule = initInvModule;

// ============================================================
// Mini-hub (4 type cards)
// ============================================================
// Phase M.8 - replaced the full-page picker with the master–detail type rail.
async function showInvHub() {
    if (!invState.types || !Object.keys(invState.types).length) {
        try { invState.types = await api().get_inv_types(); }
        catch { invState.types = {}; }
    }
    enterInvType(invState.type || "materials");
}
window.showInvHub = showInvHub;

function invRailItems() {
    return ITYPE_ORDER
        .filter(k => invState.types && invState.types[k])
        .map(k => ({ key: k, label: invState.types[k].label }));
}

function enterInvType(key) {
    invState.drafts = invState.drafts || {};
    const oldType = invState.type;
    const switchingType = oldType !== key;
    // Phase M.8 - stash the current type's draft before leaving it.
    if (switchingType && invState.initialized) {
        stashInvDraftIfDirty(oldType);
    }
    invState.type = key;
    // Phase M.7 - ALWAYS resolve the schema (never leave it null).
    invState.schema = (invState.types && (invState.types[key] || invState.types["materials"]))
                      || { label: key, columns: [] };
    // Phase T - seed this type's columns before the thead builds (drafts restore
    // their own below; otherwise start from the effective type defaults).
    if (switchingType || !invState.columns || !invState.columns.length) {
        const stashed = invState.drafts[key];
        invState.columns = (stashed && stashed.columns && stashed.columns.length)
            ? _invCloneCols(stashed.columns) : _invCloneCols(_invStockColumns());
    }

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
    invSyncAutofitButton();

    // Phase G.11: hide the Import button on Equipment + Manpower invoices -
    // these aren't built from prior QUT/DN documents (they come from
    // contracts/timesheets).
    const importBtn = document.getElementById("btnInvImport");
    if (importBtn) {
        const importable = (key !== "equipment" && key !== "manpower");
        importBtn.hidden = !importable;
    }

    buildInvThead();   // for the NEW type's columns before rendering rows

    if (switchingType) {
        const d = invState.drafts[key];
        if (d) {
            restoreInvDraft(d);
            delete invState.drafts[key];
        } else {
            invState.initialized = false;
        }
    }
    invDraftUnsaved[key] = false;
    if (!invState.initialized) initNewInv();

    // Phase M.8 - rail with unsaved-draft dots on the other types.
    if (window.showTypeRail) showTypeRail("Invoice Types", invRailItemsBadged(key), key, (k) => enterInvType(k));
    refreshInvSaved();
    // Phase M.8d - the type bar drives the Open panel's type filter.
    syncInvPanelType();
}
window.enterInvType = enterInvType;

// ============================================================
// Schema helpers + thead
// ============================================================
// Phase T - per-document columns (mirrors the Quotation editor). New invoices
// seed from the effective type schema; existing invoices load their saved layout
// (or fall back to the effective type schema). Editing never touches the type.
function _invCloneCols(cols) { return (cols || []).map(c => ({ ...c })); }
function _invStockColumns() {
    const sch = invState.schema || {};
    // New invoices should start from the effective type schema, including any
    // invoice-type settings the user saved. `stock_columns` remains available
    // as the built-in reference, not the editor seed.
    return (sch.columns && sch.columns.length) ? sch.columns : (sch.stock_columns || []);
}
function _nextInvColKey() {
    const taken = new Set((invState.columns || []).map(c => c.key));
    let n = 2;
    while (taken.has(`col_${n}`)) n++;
    return `col_${n}`;
}
function editableInvColumns() {
    if (!invState.columns || !invState.columns.length) {
        invState.columns = _invCloneCols(_invStockColumns());
    }
    return invState.columns;
}

// Inline private COST column - ALWAYS shown (2026-07-24; was a toolbar toggle).
// Bound to each row's hidden `cost` field - the SAME one the Costing screen uses
// - so it is not a schema column and never reaches the PDF generator. Always the
// LAST column, consistently across all editors.
function invRenderColumns() {
    const cols = editableInvColumns().slice();
    cols.push({ key: "cost", head: "COST", kind: "money", width: 10, _synthetic: true });
    // PROFIT rides beside COST in the private strip (2026-08-25). Read-only and
    // COMPUTED - never a row key, so it cannot reach rows_json or the renderer.
    cols.push({ key: "profit", head: "PROFIT", kind: "profit", width: 10, _synthetic: true, _readonly: true });
    return cols;
}

// USD/IQD switch - reuses the quote/DN/Costing markup + styles. Two live in the
// invoice editor: the COST header one (private cost-entry currency) and the
// footer one (the currency the CLIENT is billed in). Same widget, same feel.
function invCurrencySwitchHTML(cur, id, label, title) {
    const usd = cur === "USD";
    return `<div class="cost-cur-switch ${usd ? "is-usd" : "is-iqd"}" id="${id}"
                 role="switch" aria-checked="${usd}" tabindex="0" aria-label="${label}"
                 title="${title}">
        <span class="ccs-opt ccs-iqd-opt">IQD</span>
        <span class="ccs-opt ccs-usd-opt">$</span>
        <span class="ccs-knob" aria-hidden="true"></span>
    </div>`;
}
function invCostSwitchHTML(cur) {
    return invCurrencySwitchHTML(cur, "invCostCurSwitch", "Cost entry currency",
        "Cost entry currency - click to switch. Profit stays in USD.");
}
function invSellSwitchHTML(cur) {
    return invCurrencySwitchHTML(cur, "invSellCurSwitch", "Invoice currency",
        "Currency the client is billed in - click to switch. Rates convert; reports stay in USD.");
}

// Reflect a switch's state without rebuilding it.
function invSyncSwitchEl(el, cur) {
    if (!el) return;
    const usd = cur === "USD";
    el.classList.toggle("is-usd", usd);
    el.classList.toggle("is-iqd", !usd);
    el.setAttribute("aria-checked", usd ? "true" : "false");
}

function invWireSwitch(el, onToggle) {
    if (!el) return;
    el.addEventListener("click", onToggle);
    el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); }
    });
}
function wireInvCurrencySwitch() {
    invWireSwitch(document.getElementById("invCostCurSwitch"),
        () => setInvCostCurrency((invState.costCurrency || "USD") === "USD" ? "IQD" : "USD"));
}

// The document's IQD peg - the same one the costing screen converts at, so an
// amount typed here and the profit computed later agree. Cached because the
// import paths (autocomplete, DN-as-line) need it synchronously mid-render.
let _invPegCache = null;
async function invDocPeg() {
    // The rate chosen for THIS invoice wins. It is the number its amounts were
    // converted at, so an item imported later has to use the same one or the
    // line lands at a different rate from every other line on the page.
    if (Number(invState.sellPeg) > 0) {
        _invPegCache = Number(invState.sellPeg);
        return _invPegCache;
    }
    let iqd = window.IQD_PER_USD || 1400;
    const no = invState.editInvNo || invState.invNo;
    if (no && no !== "-") {
        try { const r = await api().doc_iqd_per_usd("inv", no); if (Number(r)) iqd = Number(r); }
        catch (e) { /* fall back to the profile peg */ }
    }
    _invPegCache = iqd;
    return iqd;
}
function invPegSync() {
    if (Number(invState.sellPeg) > 0) return Number(invState.sellPeg);
    return _invPegCache || window.IQD_PER_USD || 1400;
}

// Everything imported into an invoice - remembered item rates, a DN's total -
// is held in USD. Convert it into whatever this invoice bills in, or an IQD
// invoice would quietly bill a client $840 as "IQD 840".
function invSellFromUsd(v) {
    if ((invState.sellCurrency || "USD") !== "IQD") return v;
    const n = parseFloat(String(v == null ? "" : v).replace(/[, ]/g, ""));
    if (!isFinite(n)) return v;
    return String(Math.round(n * invPegSync()));
}

// Convert the values already typed into one column so the real amount survives
// a currency flip (a $100 rate becomes 140,000 IQD, not 100 IQD).
function invConvertColumn(key, factor, next) {
    document.querySelectorAll("#invRowsBody tr").forEach(tr => {
        const i = +tr.dataset.i;
        const inp = tr.querySelector(`[data-k="${key}"]`);
        if (inp && "value" in inp && String(inp.value).trim() !== "") {
            const v = (parseFloat(String(inp.value).replace(/[, ]/g, "")) || 0) * factor;
            const out = next === "IQD" ? String(Math.round(v)) : String(Math.round(v * 100) / 100);
            inp.value = out;
            if (invState.rows[i]) invState.rows[i][key] = out;
        }
    });
}

/* Flip ONE invoice line between $ and IQD. Shared maths: app.js. */
function toggleInvRowCostCurrency(i) {
    const row = invState.rows[i];
    if (!row) return;
    const to = window.toggleRowCostCurrency(row, invState.costCurrency, invPegSync());
    invState.dirty = true;
    const tr = document.querySelector(`#invRowsBody tr[data-i="${i}"]`);
    if (tr) {
        const inp = tr.querySelector('input[data-k="cost"]');
        if (inp) inp.value = row.cost != null ? row.cost : "";
        window.paintRowCostChip(tr, to);
    }
    updateInvTotals();
}

async function setInvCostCurrency(next) {
    const cur = invState.costCurrency || "USD";
    if (next === cur) return;
    const iqd = await invDocPeg();
    /* Per row, FROM ITS OWN currency (2026-08-13) - invConvertColumn multiplies
       every cell blindly, which would inflate a line already priced in dinars.
       It still serves the `rate` column, where the currency IS document-wide. */
    window.convertRowsToCostCurrency(invState.rows, "invRowsBody", next, iqd, invState.costCurrency);
    invState.costCurrency = next;
    invState.dirty = true;
    invSyncSwitchEl(document.getElementById("invCostCurSwitch"), next);
}

/* ---------- The invoice's exchange rate (2026-08-04) ------------------------
   The market rate moves, so the rate that was right for last month's invoice
   is not right for this one. It is asked for at the ONE moment it means
   something - when you flip $ → IQD and the amounts are about to be multiplied
   - instead of sitting in a box whose effect on already-typed prices would be
   ambiguous.

   The chosen rate is stored ON the invoice (`invoices.sell_iqd_per_usd`), and
   `grand_total` - the USD figure analytics, the dashboard and the clients page
   sum - is computed from it. So the rate is not a display preference: it is
   what the invoice is worth. */
const INV_DEFAULT_PEG = 1500;

function invSellPeg() {
    const n = Number(invState.sellPeg);
    return isFinite(n) && n > 0 ? n : INV_DEFAULT_PEG;
}

/* Ask for the rate. Resolves to a positive number, or null if cancelled.
   `current` seeds the field. Reads the input back AFTER the modal resolves -
   showModal builds the body synchronously and does not clear it on close, so
   the node is still there with the typed value. */
async function askInvPeg(current, opts) {
    const o = opts || {};
    const seed = Number(current) > 0 ? Number(current) : INV_DEFAULT_PEG;
    const pending = window.showModal({
        title: o.title || "Exchange rate",
        html: true,
        body: `<div class="peg-ask">
                 <label for="invPegInput">${esc(o.prompt || "Multiply the amounts by")}</label>
                 <div class="peg-ask-row">
                   <span class="peg-ask-unit">1 $ =</span>
                   <input id="invPegInput" type="text" inputmode="decimal"
                          value="${esc(String(seed))}" autocomplete="off">
                   <span class="peg-ask-unit">IQD</span>
                 </div>
                 <div class="peg-ask-hint">${esc(o.hint || "")}</div>
               </div>`,
        buttons: [
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: o.okLabel || "Convert", value: "ok", cls: "primary" },
        ],
    });
    const input = document.getElementById("invPegInput");
    if (input) {
        setTimeout(() => { try { input.focus(); input.select(); } catch (e) {} }, 60);
        // Enter should confirm - the modal traps Tab but knows nothing of our field.
        input.addEventListener("keydown", (e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            const ok = Array.from(document.querySelectorAll("#modalActions button"))
                .find(b => b.textContent === (o.okLabel || "Convert"));
            if (ok) ok.click();
        });
    }
    const typed = input ? input.value : "";
    const choice = await pending;
    if (choice !== "ok") return null;
    const rate = parseFloat(String(input ? input.value : typed).replace(/[, ]/g, ""));
    if (!isFinite(rate) || rate <= 0) {
        toast("That is not a usable exchange rate - nothing was converted", true);
        return null;
    }
    return rate;
}

window.askInvPeg = askInvPeg;   // reused by the quotation's cost-rate chip

// 2026-07-29 - the invoice's own currency. Flipping it converts every rate cell
// so the deal's value is unchanged; only the unit the client reads changes.
// The saved `grand_total` stays USD either way (see backend/api.save_inv).
/* `stillCurrent` (optional) is asked again after the rate prompt closes: the
   prompt is modal but not instantaneous, so the document it was opened for can
   be replaced while it is up. Without the re-check the answer lands on the
   REPLACEMENT invoice and re-prices a document the user never touched. */
async function setInvSellCurrency(next, stillCurrent) {
    const cur = invState.sellCurrency || "USD";
    if (next === cur) return;
    const live = typeof stillCurrent === "function" ? stillCurrent : () => true;

    if (next === "IQD") {
        // Ask BEFORE touching anything, so Cancel leaves the invoice untouched
        // rather than half-converted.
        const rate = await askInvPeg(invState.sellPeg || INV_DEFAULT_PEG, {
            prompt: "Multiply the amounts by",
            hint: "This rate is saved with the invoice and sets its value in reports.",
        });
        if (rate == null) return;                 // cancelled - stay in USD
        if (!live()) return;                      // a different invoice is loaded now
        invState.sellPeg = rate;
        invConvertColumn("rate", rate, "IQD");
    } else {
        // Back to USD divides by the SAME rate it was converted at, so the
        // round trip is lossless. Deriving a fresh rate here would quietly
        // change the invoice's dollar value just by toggling twice.
        invConvertColumn("rate", 1 / invSellPeg(), "USD");
    }

    invState.sellCurrency = next;
    invState.dirty = true;
    invSyncSwitchEl(document.getElementById("invSellCurSwitch"), next);
    renderInvPegChip();
    updateInvTotals();
}

/* Change the rate on an invoice that is already in IQD. Rescales by the RATIO,
   which keeps the invoice's dollar value fixed and moves the dinar figures -
   the only reading of "the rate was wrong" that leaves the deal intact. */
async function changeInvPeg() {
    if ((invState.sellCurrency || "USD") !== "IQD") return;
    const old = invSellPeg();
    const rate = await askInvPeg(old, {
        title: "Change exchange rate",
        prompt: "Re-price the amounts at",
        okLabel: "Re-price",
        hint: "The amounts are rescaled, so the invoice keeps the same value in $.",
    });
    if (rate == null || rate === old) return;
    invConvertColumn("rate", rate / old, "IQD");
    invState.sellPeg = rate;
    invState.dirty = true;
    renderInvPegChip();
    updateInvTotals();
}
window.changeInvPeg = changeInvPeg;

/* The rate, shown beside the switch so it is never a hidden number. IQD only -
   a USD invoice has nothing to convert. */
function renderInvPegChip() {
    const host = document.getElementById("invPegChip");
    if (!host) return;
    if ((invState.sellCurrency || "USD") !== "IQD") {
        host.hidden = true;
        host.innerHTML = "";
        return;
    }
    host.hidden = false;
    host.innerHTML = `<button type="button" class="peg-chip" title="Click to change the rate and re-price the amounts">
        1 $ = ${invSellPeg().toLocaleString("en-US")} IQD</button>`;
    const b = host.querySelector("button");
    if (b) b.onclick = safe(() => changeInvPeg(), "Change exchange rate");
}

// Paint + wire the footer "Invoice in $ / IQD" switch.
function renderInvSellSwitch() {
    const wrap = document.getElementById("invSellCurWrap");
    if (!wrap) return;
    wrap.innerHTML = invSellSwitchHTML(invState.sellCurrency || "USD");
    invWireSwitch(document.getElementById("invSellCurSwitch"),
        () => setInvSellCurrency((invState.sellCurrency || "USD") === "USD" ? "IQD" : "USD"));
    renderInvPegChip();
}

function invSettingsColumnsSnapshot() {
    const heads = {};
    document.querySelectorAll("#invThead .qc-head-input").forEach(inp => {
        if (inp.dataset && inp.dataset.colKey) heads[inp.dataset.colKey] = inp.value;
    });
    const thead = document.getElementById("invThead");
    const widths = (window.readEditorColWidths && thead)
        ? (window.readEditorColWidths(thead) || {}) : {};
    return editableInvColumns().map(c => {
        const out = { ...c };
        if (Object.prototype.hasOwnProperty.call(heads, c.key)) out.head = heads[c.key];
        const w = widths[c.key];
        if (typeof w === "number" && w > 0) out.width = w;
        return out;
    });
}
window.invSettingsColumnsSnapshot = invSettingsColumnsSnapshot;
function insertInvColAfter(afterKey) {
    const cols = editableInvColumns();
    let at = cols.length;
    if (afterKey) {
        const idx = cols.findIndex(c => c.key === afterKey);
        if (idx >= 0) at = idx + 1;
    }
    const key = _nextInvColKey();
    cols.splice(at, 0, { key, head: "New column", kind: "text", width: 12 });
    invState.dirty = true;
    buildInvThead();
    fullRenderInvRows();
    const inp = document.querySelector(`#invThead .qc-head-input[data-col-key="${key}"]`);
    if (inp) { inp.focus(); inp.select(); }
}
window.insertInvColAfter = insertInvColAfter;
function deleteInvCol(key) {
    const cols = editableInvColumns();
    const idx = cols.findIndex(c => c.key === key);
    if (idx < 0) return;
    if (key === (invState.schema && invState.schema.primary_key)) {
        toast("Can't remove the main column", true); return;
    }
    if (cols.length <= 1) { toast("Keep at least one column", true); return; }
    cols.splice(idx, 1);
    invState.dirty = true;
    buildInvThead();
    fullRenderInvRows();
}
window.deleteInvCol = deleteInvCol;

function buildInvThead() {
    const cols = editableInvColumns();
    const primaryKey = invState.schema && invState.schema.primary_key;
    // Phase L.4.1 - propagate per-column widths into the editor.
    let html = `<tr><th class="qc-no">NO.</th>`;
    cols.forEach(c => {
        const w = c.width ? ` style="width: ${c.width}%"` : "";
        // Phase T - editable header + inline insert(+)/remove(×).
        const delBtn = (c.key === primaryKey) ? ""
            : `<button type="button" class="qc-head-del" data-col-key="${esc(c.key)}" title="Remove this column" tabindex="-1">×</button>`;
        html += `<th class="al-center qc-head-th" data-col-key="${esc(c.key)}"${w}>
            <input class="qc-head-input" data-col-key="${esc(c.key)}" value="${esc(c.head)}" spellcheck="false" autocomplete="off" aria-label="Column header">
            ${delBtn}
            <button type="button" class="qc-head-add" data-after="${esc(c.key)}" title="Insert a column after this one" tabindex="-1">+</button>
        </th>`;
    });
    html += `<th class="al-center qc-cost-col qc-private" data-col-key="cost" style="width: 10%">
        <div class="qcc-head">COST</div>
    </th>`;
    html += `<th class="al-center qc-profit-col qc-private" data-col-key="profit" style="width: 10%">
        <div class="qcc-head">PROFIT</div>
    </th>`;
    html += `<th class="qc-del"></th></tr>`;
    const invThead = document.getElementById("invThead");
    invThead.innerHTML = html;
    invThead.querySelectorAll(".qc-head-input").forEach(inp => {
        inp.addEventListener("input", () => {
            const col = (invState.columns || []).find(c => c.key === inp.dataset.colKey);
            if (col) { col.head = inp.value; invState.dirty = true; }
        });
        inp.addEventListener("mousedown", (e) => e.stopPropagation());
    });
    invThead.querySelectorAll(".qc-head-add").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); insertInvColAfter(b.dataset.after); }));
    invThead.querySelectorAll(".qc-head-del").forEach(b =>
        b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); deleteInvCol(b.dataset.colKey); }));
    if (window.refreshColResizers) window.refreshColResizers();

    // Rebuild the tfoot tax + total cells to span the right number of columns.
    // Total columns in <tbody>: NO + editable + del = 1 + N + 1
    const totalEditorCols = cols.length + 2;     // includes NO + del
    // Tax row: label spans everything except the final "Total"-column position +
    // the trailing del cell.
    // The Total column lives at index (1 + cols.length - 1) inside the editable
    // grid (since Total is the rightmost editable column). Tax-row's "Included"
    // chip should sit under that column; the label spans everything before it.
    // To keep things simple and visually consistent with the PDF, we span:
    //   tax-label = totalEditorCols - 2  (leave 1 cell for "Included", 1 for del)
    //   total-label = 2 (NO + first editable column)
    //   total-amount = 1
    //   total-words = totalEditorCols - 2 - 1 - 1   (the middle stretch)
    const taxLabelSpan   = totalEditorCols - 2;
    const totalLabelSpan = 2;
    const totalAmtSpan   = 1;
    const totalWordsSpan = Math.max(1, totalEditorCols - totalLabelSpan - totalAmtSpan - 1);

    // The synthetic COST and PROFIT columns add two body cells between the last
    // editable column and the trailing del cell - pad both tfoot rows with the
    // SAME two, or every total below the table slides one column left.
    const costFootCell = `<td class="qc-cost-col qc-private"></td>`
                       + `<td class="qc-profit-col qc-private"></td>`;

    const taxRow = document.querySelector("#invTfoot .inv-tax-row");
    taxRow.innerHTML =
        `<td colspan="${taxLabelSpan}" class="al-center" id="invTaxLblCell">Withholding Tax (${invState.withholdingPct} %)</td>` +
        `<td class="al-center inv-tax-status" id="invTaxStatusCell">Included</td>` +
        costFootCell +
        `<td></td>`;

    const totalRow = document.querySelector("#invTfoot .inv-total-row");
    totalRow.innerHTML =
        `<td colspan="${totalLabelSpan}" class="al-center inv-total-lbl">Total</td>` +
        `<td colspan="${totalWordsSpan}" class="al-center inv-total-words" id="invTotalWordsCell">No Dollars and No Cents</td>` +
        `<td colspan="${totalAmtSpan}" class="al-center inv-total-amt" id="invTotalAmtCell">$0.00</td>` +
        costFootCell +
        `<td></td>`;

    wireInvCurrencySwitch();   // wire the COST-header USD/IQD switch (when shown)
}

// ============================================================
// New / number
// ============================================================
async function initNewInv() {
    invState.mode = "new";
    invState.editInvNo = null;
    invState.dirty = false;
    invState.numberOverridden = false;
    discardInvDraft(invState.type);     // explicit fresh doc must not revive an old finished layout
    invState.rows = [];
    invState.columns = _invCloneCols(_invStockColumns());   // Phase T - fresh = effective defaults
    invState.sourceQuote = "";          // Phase N.3 - fresh invoice has no deal link
    invState.sourceDn = "";
    invState.sourcePo = null;
    invState.withholdingIncluded = true;
    invState.costCurrency = "USD";
    invState.extraCosts = [];               // document-level costs (Costing screen)
    invState.sellCurrency = "USD";
    invState.sellPeg = null;
    _invPegCache = null;
    renderInvSellSwitch();
    invState.signingOverlay = null;
    invState.hasSignedCopy = false;     // fresh invoice can't have a client copy yet
    refreshInvSignedCopyButton();
    invState.autofitRows = invDefaultAutofit();
    invRefreshSignChip();
    invSyncAutofitButton();
    invState.date = state.today ? state.today.display : "";
    if (window.clearPendingDoc) window.clearPendingDoc();   // a New doc replaces any unsaved duplicate
    // Phase X.26 - fresh doc: drop the panel highlight and put the list on
    // today's month (shared with the other three editors, see app.js).
    if (window.panelNewDocument) window.panelNewDocument("inv");
    else window.__currentEditorDoc = null;

    document.getElementById("invClient").value = "";
    document.getElementById("invLpo").value = "N/A";
    document.getElementById("invAddress").value = invState.addressOptions[0] || "";
    document.getElementById("invReference").value = "";
    bindDocDate("invDate", invState);
    window.setModeChip(false);
    if (invState.bankInfo.length) {
        document.getElementById("invBank").value = invState.bankKey || invState.bankInfo[0].key;
        invState.bankKey = document.getElementById("invBank").value;
    }

    // Phase N.6 - Linked Quotation field (invState.sourceQuote already "" here).
    wireInvSrcQuote();
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = ""; }
    await populateInvQuoteList("");

    await populateInvClientList();
    await refreshInvNumber();

    buildInvThead();   // Phase T - rebuild header for the fresh effective defaults
    document.getElementById("invRowsBody").innerHTML = "";
    addInvRow();
    invState.initialized = true;
    updateInvTotals();
    updateInvLpoWarning();
    // See initNewQuote - a new document forgets the last one's client settings.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
}

async function populateInvClientList() {
    let list = [];
    try { list = await api().suggest_clients(""); } catch (e) { list = []; }
    document.getElementById("invClientList").innerHTML =
        (list || []).map(c => `<option value="${esc(c.name)}">`).join("");
}

// ============================================================
// Phase N.6 - Linked Quotation field (deal link).
// source_quote_no is the single source of truth; this field reads/writes
// invState.sourceQuote, which already flows into buildInvPayload + save.
// ============================================================
async function populateInvQuoteList(client) {
    const dl = document.getElementById("invQuoteList");
    if (!dl) return;
    let nums = [];
    try { nums = await api().list_quote_numbers(client || ""); } catch (e) { nums = []; }
    dl.innerHTML = (nums || []).map(n => `<option value="${esc(n)}">`).join("");
}

function wireInvSrcQuote() {
    const inp = document.getElementById("invSrcQuote");
    if (inp && !inp._wired) {
        const onEdit = () => { invState.sourceQuote = inp.value.trim(); invState.dirty = true; };
        inp.addEventListener("input",  onEdit);
        inp.addEventListener("change", onEdit);
        inp._wired = true;
    }
    const clr = document.getElementById("invSrcQuoteClear");
    if (clr && !clr._wired) {
        clr.addEventListener("click", () => {
            const el = document.getElementById("invSrcQuote");
            if (el) el.value = "";
            invState.sourceQuote = "";
            invState.dirty = true;
        });
        clr._wired = true;
    }
    // Refresh quote suggestions when the client changes (best-effort).
    const clientEl = document.getElementById("invClient");
    if (clientEl && !clientEl._quoteListWired) {
        clientEl.addEventListener("change", () => {
            populateInvQuoteList(clientEl.value.trim()).catch(() => {});
        });
        clientEl._quoteListWired = true;
    }
}

async function refreshInvNumber() {
    const client = document.getElementById("invClient").value.trim();
    const r = await api().get_next_inv_number(client, invState.type);
    const el = document.getElementById("invNo");
    if (!invState.numberOverridden) {
        invState.invNo = r.inv_no;
        el.value = r.inv_no;
    }
    if (!el._wired) {
        el.addEventListener("input", () => {
            invState.invNo = el.value.trim();
            invState.numberOverridden = true;
            invState.dirty = true;
        });
        el._wired = true;
    }
}

// The "do I have unsaved work?" rule lives in static/unsaved_guard.js, one
// copy for all four editors. See that file for why it was collected there.
if (window.unsavedGuard) {
    window.unsavedGuard.register("inv", {
        noun: "invoice",
        label: "Invoice",
        dirty: () => !!invState.dirty,
        filled: () => filledInvRows(),
        save: (opts) => invSave(opts),
    });
}

async function invNew() {
    _invDnCache = null;   // pick up any DNs costed since the editor last opened
    if (!(await window.unsavedGuard.confirmNew("inv"))) return;
    invState.initialized = false;
    await initNewInv();
}

// ============================================================
// Rows
// ============================================================
function addInvRow(data = {}) {
    const row = {};
    editableInvColumns().forEach(c => {
        if (c.kind === "computed") return;
        row[c.key] = (data[c.key] !== undefined && data[c.key] !== null)
            ? data[c.key] : (c.default || "");
    });
    invState.rows.push(row);
    document.getElementById("invRowsBody").appendChild(buildInvRowTr(invState.rows.length - 1));
    updateInvTotals();
}

// Insert a blank row at index i (shared hover "+" affordance - per document).
function insertInvRowAt(i) {
    const row = {};
    editableInvColumns().forEach(c => { if (c.kind !== "computed") row[c.key] = c.default || ""; });
    invState.rows.splice(i, 0, row);
    invState.dirty = true;
    fullRenderInvRows();
    updateInvTotals();
    const tr = document.querySelector(`#invRowsBody tr[data-i="${i}"]`);
    const f = tr && tr.querySelector("textarea, input");
    if (f) f.focus();
}
window.insertInvRowAt = insertInvRowAt;

function buildInvRowTr(i) {
    const r = invState.rows[i];
    const tr = document.createElement("tr");
    tr.dataset.i = i;
    let html = `<td class="qc-no">${i + 1}</td>`;
    invRenderColumns().forEach(c => {
        if (c.kind === "computed") {
            html += `<td class="qc-total" data-k="${c.key}"></td>`;
        } else if (c.kind === "text") {
            html += `<td><textarea data-k="${c.key}" rows="1" autocomplete="off">${esc(r[c.key] || "")}</textarea></td>`;
        } else if (c._synthetic && c.key === "cost") {
            /* The cost cell carries its OWN currency (2026-08-13), so one table
               can price one line in dollars and the next in dinars and the
               profit maths still adds up. Markup + chip come from app.js so the
               quotation, DN and invoice tables cannot drift. */
            html += window.rowCostCellHTML(r, invState.costCurrency);
        } else if (c._synthetic && c.key === "profit") {
            // Painted by window.paintRowEconCells from updateInvTotals().
            html += window.rowProfitCellHTML();
        } else {
            const im = 'inputmode="decimal"';
            html += `<td><input data-k="${c.key}" value="${esc(r[c.key] || "")}" ${im} autocomplete="off"></td>`;
        }
    });
    html += `<td class="qc-del"><button class="del-btn" title="delete" aria-label="delete">${window.ICON ? window.ICON.trash : "×"}</button></td>`;
    tr.innerHTML = html;
    wireInvRow(tr);
    return tr;
}

function autoResizeInvTextarea(ta) {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
}

// ============================================================
// Per-type autocomplete on the schema's primary column
// ============================================================
let iacTarget = null;
let iacItems  = [];
let iacIdx    = -1;

// Priced DNs available to drop into an invoice as a single line. Cached per
// editor session (invalidate on New/Open); refresh with invalidateInvDnCache().
let _invDnCache = null;
async function _invDnCandidates() {
    if (_invDnCache) return _invDnCache;
    try { _invDnCache = (await api().list_invoiceable_dns(300)) || []; }
    catch (e) { _invDnCache = []; }
    return _invDnCache;
}
window.invalidateInvDnCache = () => { _invDnCache = null; };

/* Does this query read like someone reaching for a delivery note?

   2026-08-11 - it used to match a DN on `d.search`, which is a blob of every
   ITEM NAME inside that DN. Typing an ordinary item therefore surfaced every
   DN that happened to contain it, and because DNs were unconditionally placed
   FIRST and capped the list at six, the ordinary item suggestions were pushed
   off the bottom: the dropdown looked like it only knew about DNs.

   A DN is now matched on its NUMBER only - type "D", "DN-", "DN-TDC-04" and
   they appear; type "cable" and you get cables. */
function invQueryMatchesDn(dn, q) {
    return String(dn.no || "").toLowerCase().includes(q);
}

async function onInvPrimaryInput(field, rowIdx) {
    const q = field.value.trim();
    if (!q) { hideInvAc(); return; }
    let suggestions = [];
    try { suggestions = await api().suggest_inv_items(invState.type, q); } catch (e) { suggestions = []; }
    // A priced DN can be dropped in as ONE line, so it sits in the same list as
    // the items - it just carries a DN badge and its own pick behaviour.
    let dnSug = [];
    try {
        const ql = q.toLowerCase();
        dnSug = (await _invDnCandidates())
            .filter(d => invQueryMatchesDn(d, ql))
            .slice(0, 6)
            .map(d => ({ name: d.no, _dn: d }));
    } catch (e) {}
    // DNs lead only when the query actually looks like a document number;
    // otherwise the items the user is far more likely to want come first.
    const numberish = /^d/i.test(q) || /-/.test(q);
    const all = numberish ? dnSug.concat(suggestions || [])
                          : (suggestions || []).concat(dnSug);
    if (!all.length) { hideInvAc(); return; }
    showInvAc(field, rowIdx, all);
}
window.invQueryMatchesDn = invQueryMatchesDn;

function showInvAc(field, rowIdx, suggestions) {
    const box = document.getElementById("acBox");
    box.innerHTML = "";
    iacItems  = suggestions;
    iacIdx    = -1;
    iacTarget = { field, rowIdx };
    suggestions.forEach((s, i) => {
        const isDn = !!s._dn;
        // 2026-07-30 - the invoice attaches the CLIENT's signed copy when one is
        // there, so say which DNs have one at pick time rather than at save.
        /* Subtitle = the other remembered values, minus `cost_usd` (a different
           unit from the cell being typed in) and `image_refs` (an OBJECT -
           String()-ing it printed "[object Object]"). Same rule as quote.js. */
        const parts = [];
        let hasImage = false;
        if (!isDn) {
            for (const [k, v] of Object.entries(s.data || {})) {
                if (window.ITEM_META_KEYS.has(k)) continue;
                if (k === "image_refs") {
                    hasImage = !!(v && typeof v === "object" && Object.keys(v).length);
                    continue;
                }
                if (v == null || v === "" || typeof v === "object") continue;
                parts.push(String(v));
            }
        }
        /* 2026-08-25 - the subtitle carries the COST side too. A DN whose own
           costs were never entered bills at 100% profit once it lands on the
           invoice, and the moment to notice that is BEFORE picking it, not in
           a month-end report. `cost_total` is in the DN's own currency, so it
           is shown with that currency's name rather than silently as dollars. */
        const sub = isDn
            ? `Delivery note · total $${(Number(s._dn.sell_total) || 0).toFixed(2)}`
              + (invDnHasCost(s._dn)
                    ? ` · cost ${invDnCostText(s._dn)}`
                    : " · NO COST RECORDED")
              + (s._dn.client_signed ? " · signed copy will be attached"
                                     : " · no signed copy yet")
            : parts.join(" · ");
        const tag = hasImage
            ? `<span class="ac-img-tag" title="This item has a picture">`
              + `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`
              + `Image</span>`
            : "";
        const div = document.createElement("div");
        div.className = "ac-item" + (isDn ? " ac-item-dn" : "");
        div.innerHTML = `<div class="ac-name">${esc(s.name)}${isDn ? ' <span class="ac-dn-badge">DN</span>' : ""}${tag}</div>`
            + (sub ? `<div class="sub">${esc(sub)}</div>` : "");
        div.onmousedown = (e) => { e.preventDefault(); pickInvSuggestion(i); };
        box.appendChild(div);
    });
    const rect = field.getBoundingClientRect();
    box.style.top      = (rect.bottom + window.scrollY) + "px";
    box.style.left     = (rect.left   + window.scrollX) + "px";
    box.style.minWidth = rect.width + "px";
    box.hidden = false;
}

function hideInvAc() {
    const box = document.getElementById("acBox");
    if (box) box.hidden = true;
    iacTarget = null; iacItems = []; iacIdx = -1;
}

/* A DN's `cost_total` is in the DN's OWN cost-entry currency (`currency`),
   never necessarily dollars - the same trap as the import path. Naming the
   currency is what stops "cost 750,000" being read as dollars, and a dinar
   total is followed by the dollar figure so the two are never confused. */
function invDnCostText(dn) {
    const v = Number(dn && dn.cost_total) || 0;
    const ccy = String((dn && dn.currency) || "USD").toUpperCase();
    if (ccy !== "IQD") return `$${v.toFixed(2)}`;
    const usd = Number(dn && dn.cost_total_usd);
    return `${Math.round(v).toLocaleString("en-US")} IQD`
         + (isFinite(usd) && usd ? ` ($${usd.toFixed(2)})` : "");
}
window.invDnCostText = invDnCostText;

/* Does this DN carry ANY cost? `has_costs` is the honest answer and comes from
   the backend; the `cost_total` fallback keeps an F5-without-restart (new JS,
   old Python) working rather than treating every DN as uncosted. */
function invDnHasCost(dn) {
    if (!dn) return false;
    if (dn.has_costs !== undefined) return !!dn.has_costs;
    return Number(dn.cost_total) > 0;
}
window.invDnHasCost = invDnHasCost;

function pickInvSuggestion(i) {
    if (!iacTarget) return;
    const s = iacItems[i];
    const ri = iacTarget.rowIdx;
    const row = invState.rows[ri];
    const pk = invState.schema.primary_key;

    // Insert a whole DN as one line: Item = DN no, Unit EA, Qty 1, Rate = DN
    // total (snapshot), plus an inherited cost + a `dn_ref` marker so the
    // generator appends its pages and save_inv closes the PO loop.
    if (s._dn) {
        const d = s._dn;
        row[pk] = d.no;
        row.dn_ref = d.no;
        row.unit = "EA";
        row.qty = 1;
        row.rate = invSellFromUsd(d.sell_total);   // DN totals are USD
        /* The cost comes from the DN itself - it is the one place that knows
           what this delivery actually cost. qty is 1, so the DN's whole cost
           IS the per-unit cost.

           A DN with no costs recorded returns `cost_total: 0`, and writing
           that 0 was the bug (2026-08-25): a literal zero counts as a KNOWN
           cost everywhere downstream (`database._coerce_cost(0)` is 0.0, not
           ""), so the line reported as fully costed at 100% profit - in the
           PROFIT column, on the Costing screen and in Insights alike. Four
           such lines are why INV-MT-TDC-017 reads a 97.6% margin. An unknown
           cost must stay EMPTY and say so. */
        const dnCost = invDnHasCost(d) ? (Number(d.cost_total) || 0) : 0;
        row.cost = dnCost > 0 ? d.cost_total : "";
        /* `cost_total` comes back in the DN's OWN cost-entry currency, so the
           row is stamped with THAT before the reconcile below converts it into
           the invoice's - the number never sits in the row without saying what
           unit it is in. A stale `cost_ccy` from an earlier pick in this cell
           would otherwise reinterpret it (2026-08-26). */
        if (dnCost > 0) row.cost_ccy = (d.currency || "USD").toString().toUpperCase();
        else delete row.cost_ccy;   // no cost -> no unit; the row inherits the invoice's
        if (!(dnCost > 0)) {
            toast(`${d.no} has no costs recorded - cost left empty. `
                + "Add them in Costing so this line's profit is real.", true);
        }
        // `cost_total` is in the DN's OWN cost currency - convert into this
        // invoice's, exactly as the itemised import does. Without this a DN
        // costed in IQD drops a ~1,170,000 "cost" onto a $824 line.
        if (dnCost > 0 && window.reconcileImportedCostCurrency) {
            const _ie = document.getElementById("invNo");
            const _dst = ((_ie && _ie.value) || invState.editInvNo || invState.invNo || "").trim();
            window.reconcileImportedCostCurrency([row], "dn", d.no, "inv", _dst)
                .then(() => {
                    /* This lands AFTER the synchronous repaint below and after
                       updateInvTotals() has already run, so everything the
                       conversion touched has to be repainted here (2026-08-26).
                       Repainting only the number left the chip on the unit the
                       cost arrived in and the PROFIT column on the figure from
                       before the conversion - one line reading 3,655,876 with a
                       "$" chip beside a profit computed from $2,611.34. */
                    const trR = document.querySelector(`#invRowsBody tr[data-i="${ri}"]`);
                    if (trR) {
                        const el = trR.querySelector('[data-k="cost"]');
                        if (el && "value" in el) el.value = row.cost != null ? row.cost : "";
                        window.paintRowCostChip(trR, window.rowCostCurrency(row, invState.costCurrency));
                    }
                    updateInvTotals();
                })
                .catch(() => {});
        }
        const trDn = document.querySelector(`#invRowsBody tr[data-i="${ri}"]`);
        if (trDn) {
            invRenderColumns().forEach(c => {
                if (c.kind === "computed") return;
                const el = trDn.querySelector(`[data-k="${c.key}"]`);
                if (el && "value" in el) {
                    el.value = (row[c.key] != null ? row[c.key] : "");
                    if (el.tagName === "TEXTAREA") autoResizeInvTextarea(el);
                }
            });
            // The chip carries no `data-k` - paint it with the unit the
            // inherited total actually arrived in (the reconcile above repaints
            // it again once it has converted).
            window.paintRowCostChip(trDn, window.rowCostCurrency(row, invState.costCurrency));
        }
        hideInvAc();
        invState.dirty = true;
        updateInvTotals();
        if (ri === invState.rows.length - 1) addInvRow();
        return;
    }

    row[pk] = s.name;
    if (s.data) {
        for (const k of Object.keys(s.data)) {
            // `cost` is skipped DELIBERATELY, exactly as the quotation skips
            // it. Entries remembered before 2026-08-11 hold a raw cost in an
            // unrecorded currency - 40,000 meaning dinars - and copying it
            // into a dollar cell reads as $40,000. The cost comes from
            // `cost_usd` below, or not at all.
            if (k === "cost" || k === "cost_ccy") continue;
            if (window.ITEM_META_KEYS.has(k)) continue;       // not a column; applied below
            if (k === "image_refs") continue;     // a reference, not a value
            if (s.data[k] !== undefined && s.data[k] !== null && s.data[k] !== "") {
                row[k] = s.data[k];
            }
        }
        /* The remembered price is carried in the unit it was typed in and
           converted at THIS invoice's SELL rate (its pinned "1 $ = N IQD"), or
           handed over untouched when the units already match. */
        const pr = window.priceFromMemory(s.data, invState.sellCurrency, invSellPeg());
        if (pr !== null) row.rate = pr;
        else if (row.rate !== undefined && row.rate !== "") row.rate = invSellFromUsd(row.rate);
        /* The remembered cost is filled IN THE CURRENCY IT WAS SAVED IN, and
           the cell's own $/IQD chip shows it (2026-08-26) - dinars stay dinars,
           no peg in the middle. Items remembered before 2026-08-11 recorded no
           currency at all and are still skipped rather than replayed blind:
           84,000 dinars in a dollar cell reads as $84,000. They start
           autofilling once an invoice holding them is saved again. */
        const cc = window.costCellFromMemory(s.data, invState.costCurrency, invPegSync());
        if (cc) { row.cost = cc.value; row.cost_ccy = cc.ccy; }
    }
    const tr = document.querySelector(`#invRowsBody tr[data-i="${ri}"]`);
    if (tr) {
        /* invRenderColumns(), NOT editableInvColumns(): the COST column is
           synthetic and absent from the editable list, so a remembered cost was
           written into state and then never painted into the cell - which is
           what made picking an item look like it ignored the cost. */
        invRenderColumns().forEach(c => {
            if (c.kind === "computed") return;
            const el = tr.querySelector(`[data-k="${c.key}"]`);
            if (el && "value" in el) {
                el.value = row[c.key] || "";
                if (el.tagName === "TEXTAREA") autoResizeInvTextarea(el);
            }
        });
        /* The chip is NOT a `data-k` cell, so the repaint above cannot touch it
           (2026-08-26). A remembered dinar cost therefore landed in the cell
           with the chip still reading "$" - the number said one thing and the
           toggle another, which is the one thing this cell exists to prevent. */
        window.paintRowCostChip(tr, window.rowCostCurrency(row, invState.costCurrency));
    }
    hideInvAc();
    invState.dirty = true;
    updateInvTotals();
    if (ri === invState.rows.length - 1) addInvRow();
}

document.addEventListener("keydown", (e) => {
    const box = document.getElementById("acBox");
    if (!iacTarget || !box || box.hidden) return;
    if (e.key === "ArrowDown")      { e.preventDefault(); iacIdx = Math.min(iacIdx + 1, iacItems.length - 1); refreshIacActive(); }
    else if (e.key === "ArrowUp")   { e.preventDefault(); iacIdx = Math.max(iacIdx - 1, 0); refreshIacActive(); }
    else if (e.key === "Enter" && iacIdx >= 0) { e.preventDefault(); pickInvSuggestion(iacIdx); }
    else if (e.key === "Escape")    { hideInvAc(); }
});
function refreshIacActive() {
    document.querySelectorAll("#acBox .ac-item")
        .forEach((el, i) => el.classList.toggle("active", i === iacIdx));
}

function wireInvRow(tr) {
    const i = +tr.dataset.i;
    const pk = invState.schema && invState.schema.primary_key;

    // Per-line cost currency - shared toggle (app.js).
    const ccyBtn = tr.querySelector(".cost-ccy");
    if (ccyBtn) ccyBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        toggleInvRowCostCurrency(i);
    });
    tr.querySelectorAll("input, textarea").forEach(field => {
        const k = field.dataset.k;
        if (field.tagName === "TEXTAREA") autoResizeInvTextarea(field);
        field.addEventListener("input", () => {
            invState.rows[i][k] = field.value;
            invState.dirty = true;
            if (field.tagName === "TEXTAREA") autoResizeInvTextarea(field);
            if (k === pk) onInvPrimaryInput(field, i);
            if (i === invState.rows.length - 1 && hasInvContent(invState.rows[i])) addInvRow();
            updateInvTotals();
        });
        if (k === pk) {
            field.addEventListener("focus", () => {
                if (field.value.trim()) onInvPrimaryInput(field, i);
            });
            field.addEventListener("blur", () => setTimeout(hideInvAc, 150));
        } else {
            // Phase G.14: per-column autocomplete on every non-primary text-ish input.
            const cols = editableInvColumns();
            const colDef = cols.find(c => c.key === k);
            const kind = colDef && colDef.kind;
            if (kind === "text" || kind === "num" || kind === "money") {
                window.wireUniversalAutocomplete && window.wireUniversalAutocomplete(field, {
                    docType: "inv",
                    subType: invState.type,
                    colKey:  k,
                    isPrimary: false,
                    onPick: (val) => {
                        field.value = val;
                        invState.rows[i][k] = val;
                        invState.dirty = true;
                        if (field.tagName === "TEXTAREA") autoResizeInvTextarea(field);
                        updateInvTotals();
                    },
                });
            }
        }
    });
    tr.querySelector(".del-btn").addEventListener("click", () => removeInvRow(+tr.dataset.i));
}

function removeInvRow(i) {
    invState.rows.splice(i, 1);
    invState.dirty = true;
    fullRenderInvRows();
    if (invState.rows.length === 0) addInvRow();
    updateInvTotals();
}

function fullRenderInvRows() {
    const body = document.getElementById("invRowsBody");
    body.innerHTML = "";
    invState.rows.forEach((_, i) => body.appendChild(buildInvRowTr(i)));
    updateInvTotals();
}

function focusInvFirstCol(i) {
    const tr = document.querySelector(`#invRowsBody tr[data-i="${i}"]`);
    if (!tr) return;
    const inp = tr.querySelector("textarea, input");
    if (inp) inp.focus();
}

function hasInvContent(row) {
    for (const c of editableInvColumns()) {
        if (c.kind === "computed") continue;
        const v = (row[c.key] || "").toString().trim();
        const def = (c.default || "").toString().trim();
        if (v && v !== def) return true;
    }
    return false;
}
function filledInvRows() { return invState.rows.filter(hasInvContent); }

// Mirrors backend/quote_generator._money. `cur` defaults to the invoice being
// edited; pass it explicitly when formatting some OTHER invoice's total (the
// month panel lists invoices in mixed currencies).
function imoney(v, cur) {
    const c = (cur || invState.sellCurrency || "USD").toUpperCase();
    if (c === "IQD") {
        return "IQD " + Math.round(v || 0).toLocaleString("en-US");
    }
    return "$" + (v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ============================================================
// Number -> English words (frontend mirror of quote_generator.amount_in_words).
// Lives here so the editor can show the words live without an API round trip.
// ============================================================
const I_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
                "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
                "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
const I_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
                "Eighty", "Ninety"];
const I_SCALE = ["", "Thousand", "Million", "Billion"];

function _three_to_words(n) {
    const parts = [];
    if (n >= 100) { parts.push(I_ONES[Math.floor(n / 100)] + " Hundred"); n %= 100; }
    if (n >= 20)  { parts.push(I_TENS[Math.floor(n / 10)]);                n %= 10;  }
    if (n >= 1 && n <= 19) parts.push(I_ONES[n]);
    return parts.join(" ").trim();
}
function _int_to_words(n) {
    if (!n || n === 0) return "No";
    const chunks = [];
    let scale = 0;
    while (n > 0) {
        const chunk = n % 1000;
        if (chunk) {
            let w = _three_to_words(chunk);
            if (I_SCALE[scale]) w += " " + I_SCALE[scale];
            chunks.push(w);
        }
        n = Math.floor(n / 1000);
        scale++;
    }
    return chunks.reverse().join(" ").trim();
}
function amountInWords(amount, currency) {
    const cur = (currency || invState.sellCurrency || "USD").toUpperCase();
    if (cur === "IQD") {
        // The dinar has no subunit in practice - whole numbers, no "and Cents".
        const dinars = Math.round(+amount || 0);
        return `${_int_to_words(dinars)} Iraqi ${dinars === 1 ? "Dinar" : "Dinars"}`;
    }
    amount = Math.round((+amount || 0) * 100) / 100;
    const dollars = Math.floor(amount);
    const cents = Math.round((amount - dollars) * 100);
    const d = _int_to_words(dollars);
    const c = _int_to_words(cents);
    return `${d} ${dollars === 1 ? "Dollar" : "Dollars"} and ${c} ${cents === 1 ? "Cent" : "Cents"}`;
}

/* Live cost / sell / profit / margin - see quote.js renderQuoteEconCards.
   The invoice is the only editor where SELL and COST can be in different
   currencies, and each has its own rate: the sell side uses the invoice's own
   recorded rate (invSellPeg), the cost side the deal peg. */
function renderInvEconCards() {
    if (!window.computeDocEcon || !window.renderEconCards) return;
    const costCur = invState.costCurrency || "USD";
    const sellCur = invState.sellCurrency || "USD";
    const costPeg = invPegSync();
    const sellPeg = invSellPeg();
    const econ = window.computeDocEcon({
        rows: invState.rows || [],
        extraCosts: invState.extraCosts || [],
        costCurrency: costCur,
        costPeg: costPeg,
        sellCurrency: sellCur,
        sellPeg: sellPeg,
        primaryKey: (invState.schema && invState.schema.primary_key) || "item",
    });
    // Per-line profit / loss in the PROFIT column (2026-08-25).
    if (window.paintRowEconCells) window.paintRowEconCells("invRowsBody", econ);
    const notes = [];
    if (sellCur === "IQD") notes.push(`Billed in IQD at 1 $ = ${sellPeg.toLocaleString("en-US")}`);
    if (costCur === "IQD") notes.push(`costs at 1 $ = ${costPeg.toLocaleString("en-US")}`);
    window.renderEconCards(document.getElementById("invEconCards"), econ, {
        onPickRow: (i) => window.focusEditorRow && window.focusEditorRow("invRowsBody", i),
        note: notes.length ? notes.join(" · ") + " - all figures shown in $" : "",
    });
}
window.renderInvEconCards = renderInvEconCards;

function updateInvTotals() {
    let grand = 0;
    invState.rows.forEach((r, i) => {
        const t = (parseFloat(r.qty) || 0) * (parseFloat(r.rate) || 0);
        grand += t;
        const tr = document.querySelector(`#invRowsBody tr[data-i="${i}"]`);
        if (tr) {
            const cell = tr.querySelector(".qc-total");
            if (cell) cell.textContent = (parseFloat(r.qty) || parseFloat(r.rate)) ? imoney(t) : imoney(0);
        }
    });
    document.getElementById("invGrandTotal").textContent  = imoney(grand);
    const wordsCell = document.getElementById("invTotalWordsCell");
    const amtCell   = document.getElementById("invTotalAmtCell");
    if (wordsCell) wordsCell.textContent = amountInWords(grand);
    if (amtCell)   amtCell.textContent   = imoney(grand);
    document.getElementById("invRowCount").textContent = filledInvRows().length;
    renderInvEconCards();
    refreshInvSaved();
}

// ============================================================
// Phase M.8 - per-type drafts + unsaved indicator (mirrors quote.js)
// ============================================================
const invDraftUnsaved = {};

function refreshInvSaved() {
    window.setUnsavedChip(invState.dirty);
    if (window.setEditorSavedState) {
        window.setEditorSavedState(invState.mode === "edit" && !invState.dirty);
    }
    if (window.refreshDocumentWorkflowButtons) window.refreshDocumentWorkflowButtons();
}
window.refreshInvSaved = refreshInvSaved;

function discardInvDraft(type) {
    if (!type) return;
    invState.drafts = invState.drafts || {};
    delete invState.drafts[type];
    invDraftUnsaved[type] = false;
}

function stashInvDraftIfDirty(type) {
    if (!type) return;
    invState.drafts = invState.drafts || {};
    if (invState.dirty) {
        invState.drafts[type] = snapshotInvDraft();
        invDraftUnsaved[type] = true;
    } else {
        discardInvDraft(type);
    }
}

function invRailItemsBadged(activeKey) {
    return invRailItems().map(it => ({
        ...it,
        badge: !!invDraftUnsaved[it.key] && it.key !== activeKey,
    }));
}

function snapshotInvDraft() {
    const g = id => { const e = document.getElementById(id); return e ? e.value : ""; };
    return {
        rows: (invState.rows || []).map(r => ({ ...r })),
        columns: _invCloneCols(invState.columns),   // Phase T - per-doc columns
        signingOverlay: invState.signingOverlay || null,
        mode: invState.mode, editInvNo: invState.editInvNo,
        numberOverridden: invState.numberOverridden, dirty: invState.dirty,
        date: invState.date, invNo: invState.invNo,
        bankKey: invState.bankKey, withholdingIncluded: invState.withholdingIncluded,
        autofitRows: !!invState.autofitRows,
        costCurrency: invState.costCurrency || "USD",
        sellCurrency: invState.sellCurrency || "USD",
        sellPeg: invState.sellPeg || null,
        sourceQuote: invState.sourceQuote || "",   // Phase N.6 - keep deal link per-type
        no: g("invNo"), client: g("invClient"), lpo: g("invLpo"),
        address: g("invAddress"), dateInput: g("invDate"), bank: g("invBank"),
    };
}

function restoreInvDraft(d) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    const s = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
    invState.rows = (d.rows || []).map(r => ({ ...r }));
    invState.columns = (d.columns && d.columns.length) ? _invCloneCols(d.columns) : _invCloneCols(_invStockColumns());
    invState.signingOverlay = d.signingOverlay || null;
    invRefreshSignChip();
    invState.mode = d.mode || "new";
    invState.editInvNo = d.editInvNo || null;
    invState.numberOverridden = !!d.numberOverridden;
    invState.dirty = !!d.dirty;
    invState.date = d.date || "";
    invState.invNo = d.invNo || d.no || "";
    invState.bankKey = d.bankKey || "";
    invState.withholdingIncluded = !!d.withholdingIncluded;
    invState.autofitRows = invLayoutAutofit(
        Object.prototype.hasOwnProperty.call(d, "autofitRows")
            ? { autofit_rows: d.autofitRows } : null,
        invDefaultAutofit()
    );
    invState.sourceQuote = d.sourceQuote || "";   // Phase N.6 - restore deal link
    invState.costCurrency = d.costCurrency || "USD";
    invState.sellCurrency = d.sellCurrency || "USD";
    invState.sellPeg = d.sellPeg || null;
    buildInvThead();   // header/tfoot must reflect the restored COST toggle
    renderInvSellSwitch();
    s("invNo", d.no || ""); s("invClient", d.client || ""); s("invLpo", d.lpo || "N/A");
    s("invAddress", d.address || ""); s("invDate", d.dateInput || "");
    if (d.bank) s("invBank", d.bank);
    invSyncAutofitButton();
    // Phase N.6 - sync the Linked Quotation input + suggestions to this draft.
    wireInvSrcQuote();
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = invState.sourceQuote; }
    populateInvQuoteList(d.client || "").catch(() => {});
    window.setModeChip(invState.mode === "edit", "Edit mode");
    invState.initialized = true;
    fullRenderInvRows();
    if (!invState.rows.length || hasInvContent(invState.rows[invState.rows.length - 1])) addInvRow();
    updateInvTotals();
}

// ============================================================
// LPO warning
// ============================================================
function updateInvLpoWarning() {
    const lpo = (document.getElementById("invLpo").value || "").trim();
    const warn = document.getElementById("invLpoWarn");
    if (!lpo || lpo.toUpperCase() === "N/A") {
        warn.innerHTML = `LPO No. is empty or "N/A" - the invoice will print without an LPO reference. Set a value if the client provided one.`;
        warn.classList.add("show");
    } else {
        warn.classList.remove("show");
    }
}

// ============================================================
// Validation + payload + save/preview
// ============================================================
function buildInvPayload() {
    const liveColumns = invSettingsColumnsSnapshot();
    invState.columns = _invCloneCols(liveColumns);
    const liveColWidths = {};
    liveColumns.forEach(c => {
        if (typeof c.width === "number" && c.width > 0) liveColWidths[c.key] = c.width;
    });
    return {
        inv_no: invState.invNo,
        date: invState.date,
        inv_type: invState.type,
        client_name: document.getElementById("invClient").value.trim(),
        lpo_no: document.getElementById("invLpo").value.trim() || "N/A",
        client_address: document.getElementById("invAddress").value.trim(),
        reference: document.getElementById("invReference").value.trim(),
        payment_bank_key: invState.bankKey || (invState.bankInfo[0] && invState.bankInfo[0].key) || "",
        withholding_included: !!invState.withholdingIncluded,
        // 2026-07-29 - the currency the rate/total cells below are expressed in.
        // The backend stores this plus the native total, and converts to USD for
        // `grand_total` so every report stays single-currency.
        sell_currency: invState.sellCurrency || "USD",
        // The rate those cells were converted at. The backend stores it and
        // derives grand_total (USD) from it, so reports match the printed page.
        sell_iqd_per_usd: (invState.sellCurrency || "USD") === "IQD" ? invSellPeg() : null,
        items: filledInvRows(),
        // The currency the COST cells are typed in, and the rate they convert
        // at. Sent explicitly for the same reason the quotation sends them:
        // set_cost_currency only runs AFTER save_inv returns, so the backend
        // reading it from the database mid-save would see the PREVIOUS value
        // and remember a dinar cost as though it were dollars.
        cost_currency: invState.costCurrency || "USD",
        cost_iqd_per_usd: invPegSync(),
        columns: _invCloneCols(liveColumns),   // Phase T - per-doc columns
        is_edit: invState.mode === "edit",
        // Phase N.3 - deal link (null preserves any existing link on re-save).
        source_quote_no: invState.sourceQuote || null,
        // Make Invoice from DN: transient hand-off, not an invoice-table field.
        source_dn_no: invState.sourceDn || null,
        // Phase K.1 - digital signature/stamp overlay (null = unsigned).
        signing_overlay: invState.signingOverlay || null,
        layout: invLayoutPayload(),
        // Live editor column widths (honours Excel-style resizing) so the
        // generated/previewed PDF matches the on-screen table. The same
        // widths are persisted inside `columns`, so reopening the invoice
        // restores the dragged layout.
        col_widths: liveColWidths,
    };
}

// Phase K.1 - register with the signing module so the Sign button works.
function invRefreshSignChip() {
    if (window.setSignChip) {
        window.setSignChip("docSignChip", window.overlaySigned && window.overlaySigned(invState.signingOverlay));
    }
}
window.invRefreshSignChip = invRefreshSignChip;

if (window.signingRegister) {
    window.signingRegister({
        docType: "inv",
        getOverlay: () => invState.signingOverlay || null,
        setOverlay: (ov) => { invState.signingOverlay = ov; invState.dirty = true; invRefreshSignChip(); },
    });
}

// Bulk paste/import - insert parsed rows through the normal addInvRow path.
function invBulkInsert(objs, replace) {
    const tb = document.getElementById("invRowsBody");
    if (replace) {
        invState.rows = [];
        if (tb) tb.innerHTML = "";
    } else {
        while (invState.rows.length &&
               !hasInvContent(invState.rows[invState.rows.length - 1])) {
            invState.rows.pop();
            if (tb && tb.lastElementChild) tb.removeChild(tb.lastElementChild);
        }
    }
    (objs || []).forEach(o => addInvRow(o));
    if (!invState.rows.length ||
        hasInvContent(invState.rows[invState.rows.length - 1])) addInvRow();
    invState.dirty = true;
    if (typeof refreshInvSaved === "function") refreshInvSaved();
    updateInvTotals();
}

if (window.bulkPasteRegister) {
    window.bulkPasteRegister({
        docType: "inv",
        buttonId: "btnInvPaste",
        getColumns: () => editableInvColumns()
            .filter(c => !["computed", "image", "index"].includes(c.kind))
            .map(c => ({ key: c.key, head: c.head, kind: c.kind })),
        primaryKey: () => invState.schema && invState.schema.primary_key,
        addRows: invBulkInsert,
    });
}

async function infoModalI(title, body, html = false) {
    await showModal({ title, body, html,
                      buttons: [{ label: "OK", value: "ok", cls: "primary" }] });
}

/* Rows that cannot be billed: no quantity, or no rate, or either one zero.
   Returns [{n, label, qty, rate}] - `n` is the row number as printed.

   An invoice is a demand for money. A row with a blank or zero rate bills the
   client nothing for something that was delivered, and it does it silently:
   the line still prints, the total is simply short. Two such rows were found
   on a real invoice. Unlike a quotation - which is an offer and may legitimately
   carry a "price on application" line - there is no reading of an invoice where
   0 is meant. */
function invUnpricedRows() {
    const bad = [];
    invState.rows.forEach((row, i) => {
        if (!hasInvContent(row)) return;         // a blank spacer row is fine
        const qty = parseFloat(row.qty);
        const rate = parseFloat(row.rate);
        const qtyOk = Number.isFinite(qty) && qty > 0;
        const rateOk = Number.isFinite(rate) && rate > 0;
        if (qtyOk && rateOk) return;
        const pk = (invState.schema && invState.schema.primary_key) || "item";
        const label = String(row[pk] || row.description || "").trim().split("\n")[0];
        bad.push({ n: i + 1, label, qty: qtyOk, rate: rateOk });
    });
    return bad;
}
window.invUnpricedRows = invUnpricedRows;

/* The Total column is COMPUTED from the built-in `qty` and `rate` keys - see
   updateInvTotals. A custom column headed "QTY" is not the quantity: `kind`
   ("num"/"money") is a formatting hint and carries no meaning. If those two
   columns have been renamed away, every line totals zero and the invoice bills
   nothing at all, so that is caught before saving rather than after printing. */
function invPricingColumnsMissing() {
    const cols = editableInvColumns() || [];
    const has = (k) => cols.some((c) => c && c.key === k);
    return ["qty", "rate"].filter((k) => !has(k));
}
window.invPricingColumnsMissing = invPricingColumnsMissing;

async function validateInv(options = {}) {
    if (!document.getElementById("invNo").value.trim()) {
        await infoModalI("Invoice No. required", "Invoice number is empty."); return false;
    }
    if (!document.getElementById("invClient").value.trim()) {
        await infoModalI("Client required", "Please select or enter a client name."); return false;
    }
    if (filledInvRows().length === 0) {
        await infoModalI("No items", "Add at least one item before saving."); return false;
    }

    const missing = invPricingColumnsMissing();
    if (missing.length) {
        if (options.allowIncomplete) return true;
        await infoModalI(
            "This invoice cannot be priced",
            `<p>The <b>Total</b> column is calculated from the built-in `
            + `<b>Qty</b> and <b>Rate</b> columns, and ${missing.length === 2
                ? "both are missing"
                : `<b>${esc(missing[0])}</b> is missing`} from this table.</p>`
            + `<p>Renaming a column's heading is fine - the app follows the column `
            + `itself, not its title. But a <i>new</i> column headed "QTY" or "RATE" `
            + `is only text to the calculation, so every line would total zero.</p>`
            + `<p>Add the built-in column back from the columns menu, or rename the `
            + `existing one instead of replacing it.</p>`, true);
        return false;
    }

    const unpriced = invUnpricedRows();
    if (unpriced.length) {
        if (options.allowIncomplete) return true;
        const list = unpriced.slice(0, 12).map((b) => {
            const missingBits = [b.qty ? "" : "quantity", b.rate ? "" : "rate"]
                .filter(Boolean).join(" and ");
            return `<li>Row ${b.n}${b.label ? ` - ${esc(b.label)}` : ""}: `
                + `no ${missingBits}</li>`;
        }).join("");
        await infoModalI(
            unpriced.length === 1 ? "1 item has no price"
                                  : `${unpriced.length} items have no price`,
            `<p>An invoice cannot bill a line for nothing. Every item needs a `
            + `quantity and a rate, and neither may be zero.</p>`
            + `<ul class="inv-unpriced">${list}</ul>`
            + (unpriced.length > 12
                ? `<p>…and ${unpriced.length - 12} more.</p>` : "")
            + `<p>Fill those in, or delete the rows you do not mean to bill.</p>`, true);
        return false;
    }
    return true;
}

async function invPreview() {
    if (!(await validateInv())) return;
    const btn = document.getElementById("btnInvPreview");
    await withLoading(btn, async () => {
        const r = await api().preview_inv(buildInvPayload());
        if (!r.ok) { toast("Preview error: " + r.error, true); return; }
        await api().open_path(r.path);
    });
}

async function invPrint() {
    if (!(await validateInv())) return;
    const btn = null;                      // Print left the toolbar; the busy bar covers it
    await withLoading(btn, async () => {
        const r = await api().preview_inv(buildInvPayload());
        if (!r.ok) { toast("Print error: " + r.error, true); return; }
        const pr = await api().print_file(r.path);
        toast(pr && pr.ok ? "Sent to printer." : (pr && pr.error) || "Print failed", !(pr && pr.ok));
    });
}

async function invSave(opts = {}) {
    if (!(await validateInv({ allowIncomplete: true }))) return false;

    const missingPricingColumns = invPricingColumnsMissing();
    const unpricedRows = invUnpricedRows();
    const incompletePricing = missingPricingColumns.length > 0 || unpricedRows.length > 0;

    const typed = (document.getElementById("invNo").value || "").trim();
    if (!typed) { toast("Invoice No. is empty", true); return false; }
    invState.invNo = typed;

    const sameAsExisting = (invState.mode === "edit" && invState.editInvNo === typed);
    if (!sameAsExisting) {
        const exists = await api().check_inv_exists(typed);
        if (exists.exists) {
            const choice = await showModal({
                title: "Invoice number exists",
                body: `An invoice with number ${typed} already exists. Saving will overwrite it. Continue?`,
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
            body: `${details} The invoice will be saved inside the app, but no PDF will be created until pricing is complete.`,
            buttons: [
                { label: "Go back", value: "cancel", cls: "ghost" },
                { label: "Save draft (no PDF)", value: "ok", cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    } else if (!opts.skipConfirm) {
        const choice = await showModal({
            title: "Save Invoice",
            body: `Save ${invState.invNo}${invState.mode === "edit" ? " (edit)" : ""}?`,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Save",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    }

    // btnInvSave was the Export button, removed in the 2026-07-24 toolbar
    // trim, so the old ternary resolved to a missing element on that path.
    const btn = document.getElementById("btnInvStay");
    let r;
    let payload = { ...buildInvPayload(), save_without_pdf: incompletePricing };
    await withLoading(btn, async () => { r = await api().save_inv(payload); });
    /* The client is not on file: the backend refuses rather than inventing one
       (2026-09-11). `offer` adds them on the spot if the user wants, and only
       then do we save again - with a freshly built payload, because adding the
       client also rewrites the client box to the name documents print. */
    if (window.ClientRequired && await window.ClientRequired.offer(r, "inv")) {
        payload = { ...buildInvPayload(), save_without_pdf: incompletePricing };
        await withLoading(btn, async () => { r = await api().save_inv(payload); });
    }
    /* Someone else saved this number a moment ago (multi-user, 2026-10-22):
       say so and fetch the next one, instead of a bare 'Save error' that
       leaves the stale number in the box. */
    if (r && r.number_taken) { toast(r.error, true); await refreshInvNumber(); return false; }
    if (!r || !r.ok) { toast("Save error: " + (r && r.error || "unknown"), true); return false; }
    const workflowLink = r.workflow_link || null;
    invState.sourceDn = "";
    /* 2026-09-05 - mark the source PO's lines invoiced, now that the invoice
       has a number. Keyed on `r.inv_no`, not #invNo, which the user can have
       retyped. `sourcePo` is KEPT when the link is refused: an invoice with no
       prices saves as a draft with no PDF, and set_po_fulfillment declines to
       link one. The next save (once priced) retries - the write is idempotent.
       Clearing it here would silently orphan the link. */
    if (invState.sourcePo && window.linkPoAfterSave) {
        if (await window.linkPoAfterSave(invState.sourcePo, "inv", r.inv_no)) {
            invState.sourcePo = null;
        }
    }
    if (workflowLink && !workflowLink.ok) {
        toast("Invoice saved, but its Delivery Note workflow link could not be created", true);
    } else if (workflowLink && workflowLink.no_po_links) {
        toast("Invoice saved, but the Delivery Note has no linked PO items", true);
    } else if (workflowLink && Number(workflowLink.conflict_count || 0) > 0) {
        const count = Number(workflowLink.conflict_count || 0);
        toast(`Invoice linked to the Delivery Note; ${count} PO item${count === 1 ? " was" : "s were"} already invoiced and skipped`, true);
    }
    // 2026-07-30 - the appended delivery note is the client's SIGNED copy when
    // one is attached as an image. These notes fire only when that could not
    // happen (PDF copy) or looks incomplete (one scanned sheet, multi-page DN):
    // silence would ship an invoice whose proof of delivery is missing.
    (r.dn_copy_notes || []).forEach((note, i) => setTimeout(() => toast(note, true), 400 * (i + 1)));
    invState.dirty = false;
    // Persist the cost-entry currency (the cost numbers ride in the saved rows).
    try { await api().set_cost_currency("inv", r.inv_no, invState.costCurrency || "USD"); } catch (e) {}
    if (window.refreshInvSaved) refreshInvSaved();
    if (r.incomplete_price) {
        toast(`Saved in app: ${r.inv_no}. PDF was not created until pricing is complete.`);
    } else if (!opts.stay && r.path) {
        await window.afterExportPdf(r.path, r.inv_no);
    } else {
        toast("Saved: " + r.inv_no);
    }
    if (opts.stay) {
        invState.mode = "edit";
        invState.editInvNo = r.inv_no;
        invState.invNo = r.inv_no;
        const el = document.getElementById("invNo");
        if (el) el.value = r.inv_no;
        refreshInvSaved();
        reloadInvSignedCopy();  // now saved ⇒ the Signed copy button can enable
    } else {
        invState.initialized = false;
        await initNewInv();
    }
    if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect the save in the panel
    return true;
}

// ============================================================
// Open existing invoices
// ============================================================
function invOpen() {
    // Phase M.6 - the right panel replaces the standalone Open view.
    if (window.OpenPanel) window.OpenPanel.focusSearch();
    else refreshInvList();
}
window.invOpen = invOpen;

// INV Open filter state (Phase G.9 / G.10).
let invOpenAll = [];
let invTypeFilter   = "all";
let invClientFilter = "all";
const ITYPE_LABELS = { MT: "Materials", EQ: "Equipment", MP: "Manpower", SV: "Service" };
const ITYPE_KEY_TO_CODE = { materials: "MT", equipment: "EQ", manpower: "MP", service: "SV" };

function _invTypeCode(typeKey) {
    const sch = invState.types && invState.types[typeKey];
    if (sch && sch.code) return String(sch.code).toUpperCase();
    return ITYPE_KEY_TO_CODE[typeKey] || "all";
}
// Phase M.8d - the type bar drives the Open panel's type filter.
function syncInvPanelType() {
    // Keep the filter on "all" for a typeless family (Eshraqat) so saved
    // invoices stay visible; only follow the editor sub-type when types exist.
    const hasTypes = Array.isArray(invOpenAll) && invOpenAll.some(d => d.type_code);
    invTypeFilter = hasTypes ? _invTypeCode(invState.type) : "all";
    if (Array.isArray(invOpenAll) && invOpenAll.length) {
        renderInvFilterChips();
        renderInvListFiltered();
    }
}
window.syncInvPanelType = syncInvPanelType;

function _invClientOf(d) { return d.client_name || d.client || ""; }

const _INV_PAID_ICON = '<svg aria-hidden="true" focusable="false" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/><path d="M14 6h2.5a3.5 3.5 0 0 1 0 7H8"/><path d="M8 13h8.5a3.5 3.5 0 0 1 0 7H8"/></svg>';

function invPaidPillHTML(paid) {
    return window.paidPillHTML ? window.paidPillHTML(paid)
        : (paid ? '<span class="paid-pill" title="Marked as paid">Paid</span>' : "");
}

function invPaidBtnHTML(invNo, paid) {
    const tip = paid ? "Paid - click to unmark" : "Mark invoice as paid";
    return `<button type="button" class="paid-btn${paid ? " is-paid" : ""}" data-act="paid"`
        + ` data-no="${esc(invNo)}" data-paid="${paid ? "1" : "0"}"`
        + ` title="${tip}" aria-label="${tip}" aria-pressed="${paid ? "true" : "false"}">`
        + _INV_PAID_ICON + `<span>Paid</span></button>`;
}

function applyInvPaidToButton(b, paid) {
    b.dataset.paid = paid ? "1" : "0";
    b.classList.toggle("is-paid", paid);
    b.setAttribute("aria-pressed", paid ? "true" : "false");
    const tip = paid ? "Paid - click to unmark" : "Not paid - click to mark";
    b.title = tip; b.setAttribute("aria-label", tip);
    const card = b.closest(".po-row");
    if (!card) return;
    const statuses = card.querySelector(".inv-row-statuses");
    const pill = card.querySelector(".paid-pill");
    const linked = card.querySelector(".linked-pill");
    if (paid && !linked && !pill && statuses) statuses.insertAdjacentHTML("beforeend", invPaidPillHTML(true));
    else if (!paid && pill) pill.remove();
    if (paid) {
        const sign = card.querySelector(".sign-badge");
        if (sign) sign.remove();
    }
}

function wireInvPaidButtons(container) {
    if (!container) return;
    container.querySelectorAll(".paid-btn[data-act='paid']").forEach(b => {
        b.onclick = async (e) => {
            e.stopPropagation();
            const no = b.dataset.no;
            const next = b.dataset.paid !== "1";
            let r = null;
            try { r = await api().set_invoice_paid(no, next); }
            catch (err) { r = null; }
            if (!r || !r.ok) { toast("Couldn't update paid status", true); return; }
            applyInvPaidToButton(b, next);
            const entry = invOpenAll.find(x => x && x.inv_no === no);
            if (entry) entry.paid = next;
            toast(next ? "Marked as paid" : "Paid mark removed");
            if (window.refreshOpenPanel) window.refreshOpenPanel();
            // 2026-08-27 - paid is the moment the client's stamped copy is
            // actually in hand, so ask for it here. Silent when one is already
            // attached or the invoice is waived, and AWAITED so it never opens
            // on top of the PO picker below.
            if (next && window.SignedCopy) {
                await window.SignedCopy.promptAfterPaid("inv", no);
                if (window.refreshOpenPanel) window.refreshOpenPanel();
            }
            // Existing invoice links automatically update their POs through the
            // paid_invoices marker. Ask for a PO only when this invoice has none.
            if (next && window.openPoFulfillPicker) {
                let links = [];
                try { links = (await api().po_links_for_doc("inv", no)) || []; }
                catch (err) { links = []; }
                if (!links.length) window.openPoFulfillPicker("inv", no);
            }
        };
    });
}

function renderInvFilterChips() {
    const afterClient = invOpenAll.filter(d =>
        invClientFilter === "all" || _invClientOf(d) === invClientFilter);
    // Typeless invoice families (e.g. Eshraqat's single "Invoice") carry no
    // MT/EQ/MP/SV - hide the type chip row and never filter by type in that case.
    const hasTypes = invOpenAll.some(d => d.type_code);
    const typeChipsEl = document.getElementById("invFilterChips");
    if (!hasTypes) {
        invTypeFilter = "all";
        if (typeChipsEl) typeChipsEl.innerHTML = "";
    } else {
        const typeCounts = countByKey(afterClient, d => (d.type_code || "").toUpperCase());
        const typeOptions = [
            { key: "all", label: "All", count: afterClient.length },
            // Phase M.8c - short codes (MT/EQ/MP/SV) keep all four pills on one row.
            ...["MT","EQ","MP","SV"].map(k => ({ key: k, label: k, title: ITYPE_LABELS[k], count: typeCounts[k] || 0 })),
        ];
        renderChipRow(typeChipsEl, typeOptions, invTypeFilter, key => {
            invTypeFilter = key;
            renderInvFilterChips();
            renderInvListFiltered();
        });
    }

    const afterType = invOpenAll.filter(d =>
        invTypeFilter === "all" || (d.type_code || "").toUpperCase() === invTypeFilter);
    const clientCounts = countByKey(afterType, _invClientOf);
    const clientOptions = [
        { key: "all", label: "All", count: afterType.length },
        ...uniqueSortedKeys(invOpenAll, _invClientOf)
            .map(c => ({ key: c, label: c, count: clientCounts[c] || 0 })),
    ];
    renderChipRow(document.getElementById("invClientChips"), clientOptions, invClientFilter, key => {
        invClientFilter = key;
        renderInvFilterChips();
        renderInvListFiltered();
    });
}

function renderInvListFiltered() {
    const box = document.getElementById("invList");
    const list = invOpenAll.filter(d =>
        // Typeless docs (no type_code - Eshraqat) always pass the type filter.
        (invTypeFilter   === "all" || !d.type_code || (d.type_code || "").toUpperCase() === invTypeFilter) &&
        (invClientFilter === "all" || _invClientOf(d) === invClientFilter));
    box.innerHTML = "";
    // The unsaved duplicate, if this pane is its home. Rendered BEFORE the
    // empty-state check (a month whose only document is the duplicate would
    // otherwise return early and show nothing) and OUTSIDE the filters - it
    // is the document you are working on, so a type or client chip must not
    // hide it. The UNFILTERED list is passed on purpose: it is what decides
    // the duplicate has been saved and the provisional row can go.
    const pendingRow = window.pendingRowEl
        ? window.pendingRowEl("inv", +document.getElementById("invOpenYear").value,
                              +document.getElementById("invOpenMonth").value, invOpenAll, "inv_no")
        : null;
    if (!list.length && !pendingRow) {
        const tlabel = invTypeFilter   === "all" ? "" : (ITYPE_LABELS[invTypeFilter] + " ");
        const clabel = invClientFilter === "all" ? "" : ` for ${esc(invClientFilter)}`;
        box.innerHTML = `<div class="empty-state"><div class="icon">∅</div>
            <div class="title">No ${esc(tlabel)}invoices${clabel} this month</div></div>`;
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
        row.className = "po-row inv-panel-row" + (d.editable ? " is-clickable" : "");
        if (d.editable) { row.dataset.edit = d.inv_no; row.title = "Click row to open for editing"; }
        // Show what the client was billed, in the currency they were billed in.
        // `sell_currency` MUST be passed explicitly - imoney would otherwise
        // fall back to whichever invoice happens to be open in the editor.
        const _cur = (d.sell_currency || "USD").toUpperCase();
        const _amt = (typeof d.sell_total === "number" && _cur === "IQD")
            ? d.sell_total : d.grand_total;
        const grand = (typeof _amt === "number") ? imoney(_amt, _cur) : "";
        const dateLabel = d.editable ? (d.updated_at || "").slice(0, 10) : "view only";
        const hasPdf = !!d.path && !d.incomplete_price;
        const submitBtn = hasPdf && window.submitBtnHTML ? window.submitBtnHTML("inv", d.inv_no, d.submitted) : "";
        const paidBtn = hasPdf ? invPaidBtnHTML(d.inv_no, !!d.paid) : "";
        const pdfActions = hasPdf ? `
                <button class="ghost" data-act="open" data-path="${esc(d.path)}">${window.ICON ? window.ICON.externalLink : ""}<span>Open PDF</span></button>
                <button class="ghost" data-act="reveal" data-path="${esc(d.path)}" title="Open containing folder">${window.ICON ? window.ICON.folder : ""}<span>Folder</span></button>
                <button class="ghost" data-act="copy" data-path="${esc(d.path)}" data-no="${esc(d.inv_no)}" data-signed="${d.signed ? "1" : "0"}" title="Copy file to clipboard">${window.ICON ? window.ICON.copy : ""}<span>Copy</span></button>` : "";
        const statusHtml = window.panelStatusHTML ? window.panelStatusHTML("inv", d) : "";
        const cardMain = window.panelCardMainHTML
            ? window.panelCardMainHTML({ no: d.inv_no, date: dateLabel, reference: d.reference, amount: grand, statuses: statusHtml })
            : `<div class="panel-row-main inv-row-main"><div class="panel-row-head inv-row-head"><div class="po-num">${esc(d.inv_no)}</div><div class="panel-row-topmeta inv-row-topmeta">${grand ? `<span class="panel-row-amount inv-row-amount">${esc(grand)}</span>` : ""}<span>${esc(dateLabel)}</span></div></div><div class="panel-row-statuses inv-row-statuses">${statusHtml}</div></div>`;
        row.innerHTML = `
            ${cardMain}
            <div class="row-actions">
                ${pdfActions}
                <button class="ghost danger-text" data-act="delete" data-path="${esc(d.path)}" data-label="${esc(d.inv_no)}" title="Move to RECYCLE">${window.ICON ? window.ICON.trash : ""}<span>Delete</span></button>
                ${d.editable && window.duplicateBtnHTML ? window.duplicateBtnHTML("inv", d.inv_no) : ""}
                ${paidBtn}
                ${submitBtn}
            </div>`;
        box.appendChild(row);
    });
    if (window.wireSubmitButtons) window.wireSubmitButtons(box, invOpenAll, "inv_no");
    wireInvPaidButtons(box);
    box.querySelectorAll("button[data-act='open']").forEach(b => b.onclick   = (e) => { e.stopPropagation(); api().open_path(b.dataset.path); });
    box.querySelectorAll("button[data-act='reveal']").forEach(b => b.onclick = (e) => { e.stopPropagation(); api().reveal_in_folder(b.dataset.path); });
    box.querySelectorAll("button[data-act='copy']").forEach(b => b.onclick   = safe((e) => { e.stopPropagation(); return window.copyDocumentFile("inv", b.dataset.no, b.dataset.path, b.dataset.signed === "1"); }, "Copy file"));
    box.querySelectorAll("button[data-act='delete']").forEach(b => b.onclick = (e) => { e.stopPropagation(); window.confirmAndDeleteDocument("inv", b.dataset.label, b.dataset.path, b.dataset.label, refreshInvList); });
    if (window.wireDuplicateButtons) window.wireDuplicateButtons(box);
    box.querySelectorAll(".po-row.is-clickable").forEach(row => {
        row.onclick = safe(() => loadInvForEdit(row.dataset.edit), "Load INV");
    });
}

async function refreshInvList() {
    const box = document.getElementById("invList");
    box.innerHTML = `<div class="empty-state"><div class="title">Loading…</div></div>`;
    const y = +document.getElementById("invOpenYear").value;
    const m = +document.getElementById("invOpenMonth").value;
    invOpenAll = await api().list_invoices_for_month(y, m);
    // Phase M.8d - type filter owned by the type bar; pin to active type.
    // …but only when the loaded invoices actually have types (a typeless
    // Eshraqat family keeps "all" so its saved docs stay visible).
    invTypeFilter = invOpenAll.some(d => d.type_code) ? _invTypeCode(invState.type) : "all";
    if (invClientFilter !== "all"
        && !invOpenAll.some(d => _invClientOf(d) === invClientFilter)) {
        invClientFilter = "all";
    }
    renderInvFilterChips();
    renderInvListFiltered();
}

async function loadInvForEdit(invNo) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    const rec = await api().load_invoice(invNo);
    if (!rec) { toast("Cannot load invoice - not in database", true); return; }

    // Phase L.5 - load the schema catalogue lazily so Open-from-hub works
    // even when the user hasn't visited the INV mini-hub yet (which is
    // what populated invState.types historically). Previously, hitting
    // Open straight from the home hub crashed with
    // "Cannot read properties of null (reading 'label')".
    if (!invState.types || !Object.keys(invState.types).length) {
        try { invState.types = await api().get_inv_types(); }
        catch { invState.types = {}; }
    }
    if (!invState.bankInfo || !invState.bankInfo.length) {
        try {
            const d = await api().get_inv_defaults();
            invState.bankInfo       = d.bank_info || [];
            invState.withholdingPct = d.withholding_pct ?? 3.3;
            invState.layoutDefault  = d.layout || invState.layoutDefault;
        } catch {}
    }

    // Phase M.8 - stash any in-progress draft before replacing it.
    invState.drafts = invState.drafts || {};
    if (invState.initialized && invState.type) {
        stashInvDraftIfDirty(invState.type);
    }
    invState.type = rec.inv_type || "materials";
    invState.schema = invState.types[invState.type]
                      || invState.types["materials"]
                      || { label: invState.type, columns: [] };
    invState.mode = "edit";
    invState.editInvNo = invNo;
    invState.invNo = invNo;
    reloadInvSignedCopy();              // async - updates the toolbar button
    // Phase T - this invoice's own columns, or the effective schema for old docs.
    invState.columns = (rec.columns && rec.columns.length)
        ? _invCloneCols(rec.columns) : _invCloneCols(invState.schema.columns || []);
    invState.date = rec.inv_date || (state.today ? state.today.display : "");
    invState.rows = (rec.rows || []).slice();
    invState.sourceQuote = rec.source_quote_no || "";   // Phase N.3 - keep deal link
    invState.sourceDn = "";
    invState.sourcePo = null;          // any real PO link is already in po_fulfillment
    invState.bankKey =rec.payment_bank_key || (invState.bankInfo[0] && invState.bankInfo[0].key) || "";
    invState.withholdingIncluded = !!rec.withholding_included;
    invState.signingOverlay = rec.signing_overlay || null;
    invState.autofitRows = invLayoutAutofit(rec.layout, invDefaultAutofit());
    // Restore cost-entry: auto-show the private COST column when the invoice
    // already carries costs, and load its cost-entry currency.
    try { invState.costCurrency = (await api().get_cost_currency("inv", invNo)) || "USD"; }
    catch (e) { invState.costCurrency = "USD"; }
    // The currency this invoice was issued in - its rate cells are stored in it.
    invState.sellCurrency = (rec.sell_currency || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
    // Null for invoices issued before the rate was recorded - invSellPeg()
    // then falls back to the default rather than inventing a number.
    invState.sellPeg = Number(rec.sell_iqd_per_usd) > 0 ? Number(rec.sell_iqd_per_usd) : null;
    try { invState.extraCosts = JSON.parse(rec.extra_costs_json || "[]") || []; }
    catch (e) { invState.extraCosts = []; }
    // Warm the peg cache so an autocomplete pick converts at this DEAL's rate
    // (a cash deal settles at the market rate, not the profile peg).
    invDocPeg().catch(() => {});
    invRefreshSignChip();
    invSyncAutofitButton();
    invState.dirty = false;
    invState.initialized = true;
    invState.numberOverridden = true;

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    // AFTER hideAllViews() on purpose - it clears __currentEditorDoc, so setting
    // this any earlier leaves it null once the loader returns. That silently
    // broke the panel row highlight and made Duplicate a no-op (its guard reads
    // this to confirm the source actually loaded). See tests/duplicate_document.
    window.__currentEditorDoc = { kind: "inv", no: invNo };
    // Phase M.8 - keep the rail visible with the opened invoice's type active.
    invDraftUnsaved[invState.type] = false;
    if (window.showTypeRail) showTypeRail("Invoice Types", invRailItemsBadged(invState.type), invState.type, (k) => enterInvType(k));
    refreshInvSaved();
    buildInvThead();
    renderInvSellSwitch();

    document.getElementById("invNo").value = invNo;
    // bindDocDate, not a bare `.value =` - typing a new date here has to reach
    // invState.date or the save writes the ORIGINAL one and the invoice never
    // moves month. See app.js.
    bindDocDate("invDate", invState);
    document.getElementById("invClient").value = rec.client_name || "";
    document.getElementById("invLpo").value = rec.lpo_no || "N/A";
    document.getElementById("invAddress").value = rec.client_address || "";
    document.getElementById("invReference").value = rec.reference || "";
    document.getElementById("invBank").value = invState.bankKey;
    // Phase N.6 - Linked Quotation field (invState.sourceQuote already set above).
    wireInvSrcQuote();
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = rec.source_quote_no || ""; }
    await populateInvQuoteList(rec.client_name || "");
    window.setModeChip(true, "Edit mode");

    await populateInvClientList();
    fullRenderInvRows();
    addInvRow();
    updateInvTotals();
    updateInvLpoWarning();
    setTimeout(() => window.autoSizeAllTextareas && window.autoSizeAllTextareas(document.getElementById("invView")), 0);
    // Phase M.8d - opened doc's type becomes the active type → sync the panel.
    syncInvPanelType();
    // Put the panel on THIS document's month and scroll to its row, so
    // opening one from Needs Attention / search / anywhere does not leave
    // the panel on a different month (2026-10-01).
    if (window.revealDocInPanel) window.revealDocInPanel("inv", invNo, invState.date);
}

/* Duplicate (2026-08-04) - turn the just-loaded invoice into a NEW unsaved one.
   Called by window.duplicateDocument after loadInvForEdit; see DUPLICATE_HOOK
   in app.js. Rows, client, address, LPO ref, type, bank, withholding, sell
   currency and layout stay as loaded, and `sourceQuote` stays because the copy
   belongs to the same deal.
   `sourceDn` is cleared - it is the transient Make-Invoice link recording which
   delivery note this invoice was built from, and the copy was not.
   Paid needs no clearing: it is keyed by invoice number in a separate table, so
   a new number is unpaid by construction. */
async function invMakeDuplicate() {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    invState.mode = "new";
    invState.editInvNo = null;
    invState.numberOverridden = false;
    invState.signingOverlay = null;           // a copy has not been signed
    document.getElementById("invReference").value = "";
    invState.sourceDn = "";                   // not made from that DN
    invState.sourcePo = null;                 // ...nor from that PO
    invRefreshSignChip();
    invState.date = (window.state && state.today) ? state.today.display : invState.date;
    bindDocDate("invDate", invState);
    window.__currentEditorDoc = null;         // not a saved document yet
    window.setModeChip(false);
    await reloadInvSignedCopy();              // no attached client copy on a copy
    await refreshInvNumber();
    refreshInvSaved();
    invState.dirty = true;                    // real unsaved content
    return invState.invNo || "";
}
window.invMakeDuplicate = invMakeDuplicate;

// ============================================================
// Import from saved Quotation OR Delivery Note
// ============================================================
async function openImportDialog() {
    // Phase M.2 - scan the last 6 months (was: this month + last only).
    let quotes = [], dns = [];
    try {
        quotes = (await gatherRecentMonths((y, m) => api().list_quotes_for_month(y, m), 6))
            .filter(q => q && q.editable)
            // Phase F.4: hide Equipment quotes from the import picker.
            .filter(q => (q.type_code || "").toUpperCase() !== "EQ");
    } catch (e) { quotes = []; }
    try {
        dns = (await gatherRecentMonths((y, m) => api().list_dns_for_month(y, m), 6))
            .filter(d => d && d.editable);
    } catch (e) { dns = []; }

    if (!quotes.length && !dns.length) {
        await infoModalI("Nothing to import",
            "No editable quotations or delivery notes found in the last 6 months. Save one first, then import from it.");
        return;
    }

    // Build unified list with a `source` tag.
    const combined = [
        ...quotes.map(q => ({ ...q, _source: "QUT", _id: q.quote_no })),
        ...dns.map(d   => ({ ...d, _source: "DN",  _id: d.dn_no   })),
    ];

    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]) - same shape as the
       DN importer, which is the point. */
    const _h = window.Popup.open({
        title: "Import items for this invoice",
        size: "lg",
        dir: "ltr",
        className: "import-modal",
        html: true,
        body: `<div class="po-link-sticky"><div class="filter-chips-stack">`
            + `<div class="filter-chips" id="iimSourceChips"></div>`
            + `<div class="filter-chips filter-chips-client" id="iimClientChips"></div></div>`
            + `<input type="text" class="import-search" id="iimSearch" placeholder="Search by number, client, or filename…"></div>`
            + `<div class="import-list" id="iimList"></div>`,
        actions: [{ label: "Cancel", value: "cancel", cls: "ghost" }],
    });
    const backdrop = _h.panel;
    const close = () => _h.close(null);

    // Modal-local filter state. Source: All / QUT-MT / QUT-MP / QUT-SV / DN.
    let sourceFilter = "all";
    let clientFilter = "all";
    let textFilter = "";
    const sourceKey = it => (it._source === "DN") ? "DN"
        : `QUT-${(it.type_code || "MT").toUpperCase()}`;
    const _clientOf = it => it.client_name || it.client || "";

    function applyFilters() {
        const text = textFilter.toLowerCase();
        return combined.filter(it => {
            if (sourceFilter !== "all" && sourceKey(it) !== sourceFilter) return false;
            if (clientFilter !== "all" && _clientOf(it) !== clientFilter) return false;
            if (text) {
                const hay = [it._id, _clientOf(it), it.filename]
                    .map(x => (x || "").toString().toLowerCase()).join(" ");
                if (!hay.includes(text)) return false;
            }
            return true;
        });
    }

    function renderChips() {
        const afterClient = combined.filter(it =>
            clientFilter === "all" || _clientOf(it) === clientFilter);
        const sCounts = countByKey(afterClient, sourceKey);
        const sourceOpts = [
            { key: "all",    label: "All",            count: afterClient.length },
            { key: "QUT-MT", label: "Materials QUT",  count: sCounts["QUT-MT"] || 0 },
            { key: "QUT-MP", label: "Manpower QUT",   count: sCounts["QUT-MP"] || 0 },
            { key: "QUT-SV", label: "Service QUT",    count: sCounts["QUT-SV"] || 0 },
            { key: "DN",     label: "Delivery Note",  count: sCounts["DN"]     || 0 },
        ];
        renderChipRow(backdrop.querySelector("#iimSourceChips"), sourceOpts, sourceFilter, k => {
            sourceFilter = k; renderChips(); renderList();
        });

        const afterSource = combined.filter(it =>
            sourceFilter === "all" || sourceKey(it) === sourceFilter);
        const cCounts = countByKey(afterSource, _clientOf);
        const clientOpts = [
            { key: "all", label: "All", count: afterSource.length },
            ...uniqueSortedKeys(combined, _clientOf)
                .map(c => ({ key: c, label: c, count: cCounts[c] || 0 })),
        ];
        renderChipRow(backdrop.querySelector("#iimClientChips"), clientOpts, clientFilter, k => {
            clientFilter = k; renderChips(); renderList();
        });
    }

    const listEl = backdrop.querySelector("#iimList");
    function renderList() {
        const rows = applyFilters();
        listEl.innerHTML = "";
        if (!rows.length) {
            listEl.innerHTML = `<div class="empty-state" style="padding:20px;">
                <div class="title">No matches</div></div>`;
            return;
        }
        rows.forEach(it => {
            const row = document.createElement("div");
            row.className = "import-row";
            const tag = it._source === "QUT"
                ? (it.type_code || "QUT").toString().toUpperCase()
                : "DN";
            const clientLbl = _clientOf(it);
            row.innerHTML = `
                <div>
                    <div class="import-main">${esc(it._id)} - ${esc(clientLbl)}</div>
                    <div class="import-sub">${esc(it.filename)}${it.updated_at ? ` · ${esc(it.updated_at)}` : ""}</div>
                </div>
                <span class="import-type-chip">${esc(tag)}</span>
            `;
            row.onclick = safe(() => {
                // Phase X.9 - preview the selected document first, import on confirm.
                const isQ = (it._source === "QUT");
                const doImport = async () => {
                    const ok = isQ ? await importQuoteIntoInv(it._id) : await importDnIntoInv(it._id);
                    if (ok) close();
                };
                if (window.showImportPreview) {
                    window.showImportPreview({ kind: isQ ? "quote" : "dn", no: it._id, onImport: doImport });
                } else {
                    doImport();
                }
            }, "Preview");
            listEl.appendChild(row);
        });
    }
    renderChips();
    renderList();

    const search = backdrop.querySelector("#iimSearch");
    search.addEventListener("input", () => {
        textFilter = search.value.trim();
        renderList();
    });
    setTimeout(() => search.focus(), 60);
}

function confirmReplaceIfDirty(label) {
    if (filledInvRows().length === 0 || !invState.dirty) return Promise.resolve(true);
    return showModal({
        title: "Overwrite current items?",
        body: `Importing from ${label} will replace the current items. Continue?`,
        buttons: [
            { label: "Cancel",  value: "cancel", cls: "ghost" },
            { label: "Replace", value: "ok",     cls: "primary" },
        ],
    }).then(c => c === "ok");
}

async function importQuoteIntoInv(quoteNo) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    const rec = await api().load_quote(quoteNo);
    if (!rec) { toast("Could not load quotation " + quoteNo, true); return false; }
    if (!(await confirmReplaceIfDirty(quoteNo))) return false;

    // Phase M.2 - smart alias-based mapping shared with DN (handles every
    // source sub-type, custom columns, and avoids primary duplication).
    const pk = invState.schema.primary_key;
    const newRows = (rec.rows || [])
        .map(r => window.smartMapRow(r, editableInvColumns(), pk))
        .filter(r => Object.values(r).some(v => (v || "").toString().trim()));

    if (!newRows.length) {
        await infoModalI("Nothing to import", "That quotation has no usable items.");
        return false;
    }

    document.getElementById("invClient").value  = rec.client_name || "";
    document.getElementById("invAddress").value = rec.client_address || "";
    /* The client's own settings for what a quotation does NOT carry (2026-09-11).
       It supplies the client and the address; it has no receiving account and
       no tax status, so those are still this new document's own defaults and
       the client's settings fill them. Waleed hit this making an invoice from
       DN-HDEC-066 and getting the profile's default account. */
    if (window.ClientDefaults) await window.ClientDefaults.adopt("inv", ["address"]);
    await populateInvClientList();
    await refreshInvNumber();

    // Phase X.10 - convert carried costs from the quote's currency into this
    // invoice's currency (don't let an IQD quote cost land as USD).
    if (window.reconcileImportedCostCurrency) {
        const _ie = document.getElementById("invNo");
        const _dst = ((_ie && _ie.value) || invState.editInvNo || invState.invNo || "").trim();
        await window.reconcileImportedCostCurrency(newRows, "quote", quoteNo, "inv", _dst);
    }

    invState.rows = newRows;
    invState.sourceDn = "";
    invState.sourcePo = null;           // these rows came from a quote, not a PO
    invState.sourceQuote = quoteNo;     // Phase N.3 - this invoice's deal
    // Phase N.6 - reflect the deal link into the Linked Quotation field.
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = invState.sourceQuote; }
    populateInvQuoteList(rec.client_name || "").catch(() => {});
    invState.dirty = true;
    fullRenderInvRows();
    addInvRow();
    updateInvTotals();
    updateInvLpoWarning();
    toast(`Imported ${newRows.length} item${newRows.length === 1 ? "" : "s"} from ${quoteNo}`);
    return true;
}

async function importDnIntoInv(dnNo, opts = {}) {
    // This document came from somewhere else (saved, restored, duplicated or
    // imported). Its field values are the DOCUMENT'S, not ours to replace.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    const rec = await api().load_dn(dnNo);
    if (!rec) { toast("Could not load delivery note " + dnNo, true); return false; }
    if (!(await confirmReplaceIfDirty(dnNo))) return false;

    // Phase M.2 - same smart mapper as the QUT path.
    const pk = invState.schema.primary_key;
    const newRows = (rec.rows || [])
        .map(r => window.smartMapRow(r, editableInvColumns(), pk))
        .filter(r => Object.values(r).some(v => (v || "").toString().trim()));

    if (!newRows.length) {
        await infoModalI("Nothing to import", "That delivery note has no usable items.");
        return false;
    }

    document.getElementById("invClient").value  = rec.client_name || "";
    document.getElementById("invAddress").value = rec.client_address || "";
    if (rec.lpo_no) document.getElementById("invLpo").value = rec.lpo_no;
    /* The client's own settings for what a delivery note does NOT carry (2026-09-11).
       It supplies the client and the address; it has no receiving account and
       no tax status, so those are still this new document's own defaults and
       the client's settings fill them. Waleed hit this making an invoice from
       DN-HDEC-066 and getting the profile's default account. */
    if (window.ClientDefaults) await window.ClientDefaults.adopt("inv", ["address"]);
    await populateInvClientList();
    await refreshInvNumber();

    // Phase X.10 - convert carried costs from the DN's currency into this
    // invoice's currency (keeps QUT→DN→INV cost math correct end to end).
    if (window.reconcileImportedCostCurrency) {
        const _ie = document.getElementById("invNo");
        const _dst = ((_ie && _ie.value) || invState.editInvNo || invState.invNo || "").trim();
        await window.reconcileImportedCostCurrency(newRows, "dn", dnNo, "inv", _dst);
    }

    invState.rows = newRows;
    invState.sourceDn = opts.linkWorkflow ? dnNo : "";
    invState.sourcePo = null;           // these rows came from a DN, not a PO
    // Phase N.3 - thread the ORIGINAL quote through the DN so QUT→DN→INV all
    // share one deal link (the DN carries source_quote_no from its own import).
    invState.sourceQuote = rec.source_quote_no || "";
    // Phase N.6 - reflect the deal link into the Linked Quotation field.
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = invState.sourceQuote; }
    populateInvQuoteList(rec.client_name || "").catch(() => {});
    invState.dirty = true;
    fullRenderInvRows();
    addInvRow();
    updateInvTotals();
    updateInvLpoWarning();
    toast(`Imported ${newRows.length} item${newRows.length === 1 ? "" : "s"} from ${dnNo}`);
    return true;
}

// ============================================================
// Make Invoice from a Purchase Order (2026-09-05).
//
// The thin half - see `createDnFromPo` in dn.js and the header of
// po_handoff.js, which holds everything the two share.
// ============================================================
async function createInvoiceFromPo(payload) {
    const p = payload || {};
    if (!Array.isArray(p.rows) || !p.rows.length) return false;

    if (!invState.types || !Object.keys(invState.types).length) {
        invState.types = await api().get_inv_types();
    }
    // Materials, like every other import path; the type rail can switch it.
    invState.type = "materials";
    invState.schema = invState.types["materials"] || { label: "Materials Invoice", columns: [] };
    invState.initialized = false;

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    invDraftUnsaved[invState.type] = false;
    if (window.showTypeRail) showTypeRail("Invoice Types", invRailItemsBadged(invState.type), invState.type, (k) => enterInvType(k));
    buildInvThead();
    await initNewInv();

    /* A PO line's company is whatever was typed when the PO was made - often
       Arabic, often an alias. Resolve it through Clients Master Data before the
       number is drawn (2026-09-11): the box ends up with the client's legal
       name, the chip and the number with the short code, and the client's saved
       receiving account applied. `adopt`, not `apply`: the PO supplies a client
       and nothing else, so this invoice's other fields are still the blank
       ones initNewInv just made. */
    if (p.client) {
        document.getElementById("invClient").value = p.client;
        if (window.ClientDefaults) await window.ClientDefaults.adopt("inv");
    }
    await populateInvClientList();
    await refreshInvNumber();

    /* The primary key can be renamed per invoice type, so the rows arrive keyed
       on `item` and are re-keyed here. No `smartMapRow` - its `rate` alias group
       includes "cost", which would print the private cost as the sell price. */
    const pk = (invState.schema && invState.schema.primary_key) || "item";
    const rows = p.rows.map((r) => {
        const out = { ...r };
        if (pk !== "item") { out[pk] = r.item; delete out.item; }
        return out;
    });

    /* The PO's reference, copied as it was written (2026-09-09). AFTER
       `initNewInv`, which blanks the field. Never invented: a PO with no
       reference leaves the box empty rather than guessing one. */
    if (p.reference) {
        const ref = document.getElementById("invReference");
        if (ref) ref.value = p.reference;
    }

    // AFTER initNewInv - it resets both of these.
    invState.rows = rows;
    invState.sourcePo = p.link || null;
    invState.dirty = true;

    fullRenderInvRows();
    addInvRow();
    updateInvTotals();
    updateInvLpoWarning();
    toast(`Filled ${rows.length} item${rows.length === 1 ? "" : "s"} from ${p.poNo}`);
    return true;
}
window.createInvoiceFromPo = createInvoiceFromPo;

// Public hook so dn.js can drive the "Make invoice from DN" button.
async function createInvoiceFromDnNo(dnNo, opts = {}) {
    if (!invState.types || !Object.keys(invState.types).length) {
        invState.types = await api().get_inv_types();
    }
    // Default to materials; the user can switch type via the rail.
    invState.type = "materials";
    invState.schema = invState.types["materials"] || { label: "Materials Invoice", columns: [] };
    invState.initialized = false;

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    invDraftUnsaved[invState.type] = false;
    if (window.showTypeRail) showTypeRail("Invoice Types", invRailItemsBadged(invState.type), invState.type, (k) => enterInvType(k));
    buildInvThead();
    await initNewInv();
    await importDnIntoInv(dnNo, { linkWorkflow: true });
}
window.createInvoiceFromDnNo = createInvoiceFromDnNo;

/* ---- starting a document from a client's LPO (2026-09-12) --------------

   The To-Do says "CSSP-RENT-0002: 4 months not invoiced". The action that
   clears it is writing the invoice, so the reminder now offers to start it.

   What an LPO supplies: the client, its own number, and what was ORDERED. It
   carries no address, no receiving account and no tax status - those are the
   client's settings, which `ClientDefaults.adopt` fills, exactly as the
   quotation path does.

   Quantities come from what is still OWED, not from what was ordered: invoicing
   60 desks again when 60 are already delivered is the mistake this avoids. */
/* The whole hand-off, not just the words on the lines (2026-09-28).

   This used to read `lpo_fulfillment` and hand back item/description/unit/
   qty/rate. Two things the LPO already knew were then typed again on every
   document made from it: the QUOTATION it answers, and each line's COST. The
   backend's `lpo_document_seed` answers all three at once - the quantities
   still owed, the quotation number, and the cost read back out of that
   quotation with the currency it was written in.

   Falls back to the old fulfilment read if the bridge method is missing, so an
   older backend still starts a document rather than an empty one. */
async function lpoSeedForDocument(clientName, lpoNo, documentKind = "inv") {
    let seed = null;
    try { seed = await api().lpo_document_seed(clientName, lpoNo, documentKind); }
    catch (e) { seed = null; }
    if (seed && seed.ok) return seed;
    /* The fallback below is for a backend that does not HAVE this method, not
       for one that answered "that PO is not on file" (2026-09-28). Falling
       through on a real refusal turned the error into an empty fulfilment read
       and the document said "no ordered lines are recorded on it" - which is a
       different, and wrong, thing to tell someone whose LPO number is a typo. */
    if (seed && seed.error) return seed;

    let ful = null;
    try { ful = await api().lpo_fulfillment(clientName, lpoNo); }
    catch (e) { ful = null; }
    const lines = (ful && ful.lines) || [];
    const delivered = lines.some(l => Number(l.delivered) > 0);
    const use = lines.map(l => ({ ...l, seedQty: documentKind === "inv"
        ? Math.max(0, Number(delivered ? l.delivered : l.ordered) - Number(l.invoiced || 0))
        : Math.max(0, Number(l.remaining || 0)) })).filter(l => l.seedQty > 0);
    return {
        ok: true, source_quote_no: "", costed: 0, uncosted: use.length,
        rows: use.map(l => ({
            item: l.item || "",
            description: l.description || "",
            unit: l.unit || "",
            qty: l.seedQty,
            rate: l.rate || "",
        })),
    };
}

/* The rows alone, for anything that only wants those. */
async function lpoLinesForDocument(clientName, lpoNo) {
    return (await lpoSeedForDocument(clientName, lpoNo)).rows;
}

/* What to say after seeding, in words - how many lines, and how many of them
   arrived with a cost. Silence about the missing ones is how a document gets
   invoiced at an invented profit. */
function lpoSeedToast(seed, lpoNo) {
    const n = seed.rows.length;
    const parts = [`${n} line(s) from ${lpoNo}`];
    if (seed.source_quote_no) parts.push(`priced against ${seed.source_quote_no}`);
    if (seed.quantity_policy === "enter_period_quantity") parts.push("enter this period's quantities");
    if (seed.costed && !seed.uncosted) parts.push("costs filled");
    else if (seed.costed) parts.push(`${seed.costed} of ${n} lines have a cost`);
    else if (seed.source_quote_no) parts.push("no costs on that quotation");
    return parts.join(" - ") + ". Check the quantities before saving.";
}

async function createInvoiceFromLpo(clientName, lpoNo, period = null) {
    if (!invState.types || !Object.keys(invState.types).length) {
        invState.types = await api().get_inv_types();
    }
    let rec = null;
    try { rec = await api().get_lpo(clientName, lpoNo); } catch (e) { /* seed reports a read error */ }
    invState.type = rec && rec.kind === "rental" ? "equipment" : "materials";
    invState.schema = invState.types[invState.type] || { label: "Invoice", columns: [] };
    invState.initialized = false;

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    invDraftUnsaved[invState.type] = false;
    if (window.showTypeRail) {
        showTypeRail("Invoice Types", invRailItemsBadged(invState.type),
                     invState.type, (k) => enterInvType(k));
    }
    buildInvThead();
    await initNewInv();

    // A NEW document that happens to know its client: the client's own
    // settings supply everything the LPO does not carry.
    if (window.ClientDefaults) window.ClientDefaults.reset("inv");
    document.getElementById("invClient").value = clientName || "";
    document.getElementById("invLpo").value = lpoNo || "";
    if (window.ClientDefaults) await window.ClientDefaults.adopt("inv");
    if (rec && rec.currency === "IQD") {
        invState.sellCurrency = "IQD";
        invState.sellPeg = Number(rec.total_usd) > 0 ? Number(rec.total) / Number(rec.total_usd) : invSellPeg();
        renderInvSellSwitch();
    }
    await populateInvClientList();
    await refreshInvNumber();

    await adoptLpoIntoInv(clientName, lpoNo, { confirm: false, period });
}
window.createInvoiceFromLpo = createInvoiceFromLpo;

/* Bring everything an LPO knows into the invoice that is already open
   (2026-09-28). The twin of `adoptLpoIntoDn`, and the same story: this was the
   tail of `createInvoiceFromLpo` and nothing else could reach it, so picking an
   LPO beside the field wrote the number and stopped.

   `confirm: false` from the LPO screen (brand-new document, nothing to lose),
   `confirm: true` from the pick button. */
async function adoptLpoIntoInv(clientName, lpoNo, opts = {}) {
    const seed = await lpoSeedForDocument(clientName, lpoNo, "inv");
    const rows = (seed && seed.rows) || [];
    if (!seed || !seed.ok) {
        toast(`Could not read ${lpoNo}` + (seed && seed.error ? ": " + seed.error : ""), true);
        updateInvLpoWarning();
        return false;
    }
    /* Ask BEFORE anything is written, exactly as `confirmReplaceIfDirty` does
       for a quotation or DN import. The picked number stays either way - the
       invoice IS against that PO; only the items are in question. */
    if (opts.confirm && rows.length && filledInvRows().length > 0 && invState.dirty) {
        const choice = await showModal({
            title: "Overwrite current items?",
            body: `${lpoNo} has ${rows.length} ordered line(s). Bringing them in `
                + "will replace the items already in this invoice. Continue?",
            buttons: [
                { label: "Keep mine", value: "cancel", cls: "ghost" },
                { label: "Replace",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") { updateInvLpoWarning(); return false; }
    }

    if (rows.length) {
        invState.sourceQuote = seed.source_quote_no || "";
        invState.sourceDn = null;
        invState.sourcePo = null;
        const source = document.getElementById("invSrcQuote");
        if (source) source.value = invState.sourceQuote;
    }
    /* The deal link the LPO already knew. Set before the rows so the cost
       reconciliation below can read the SOURCE document's currency and peg
       from it - the same link `importQuoteIntoDN` stamps. */
    if (seed.source_quote_no) {
        invState.sourceQuote = seed.source_quote_no;
        const sq = document.getElementById("invSrcQuote");
        if (sq) sq.value = seed.source_quote_no;
        populateInvQuoteList(clientName || "").catch(() => {});
    }
    if (rows.length) {
        const pk = invState.schema.primary_key;
        const newRows = rows.map(r => {
            const row = { ...r };
            if (opts.period && opts.period.start && opts.period.end) {
                row.description = [row.description, `Service period: ${opts.period.start} to ${opts.period.end}`].filter(Boolean).join("\n");
            }
            if (window.priceFromMemory) {
                const converted = window.priceFromMemory({ ...row, price_native: String(row.rate || "").replace(/,/g,""),
                    price_native_ccy: row.rate_ccy || seed.currency || "USD" },
                    invState.sellCurrency || "USD", invSellPeg());
                if (converted !== null && converted !== undefined) row.rate = converted;
            }
            return window.smartMapRow(row, editableInvColumns(), pk);
        });
        /* A cost is stored in its own document's currency. Carrying 250,000 IQD
           into a dollar invoice unconverted reads as $250,000, so the same
           reconciliation the quotation import uses runs here too. */
        if (seed.source_quote_no && window.reconcileImportedCostCurrency) {
            const el = document.getElementById("invNo");
            const dst = ((el && el.value) || invState.editInvNo || invState.invNo || "").trim();
            await window.reconcileImportedCostCurrency(
                newRows, "quote", seed.source_quote_no, "inv", dst);
        }
        invState.rows = newRows;
        invState.dirty = true;
        fullRenderInvRows();
        addInvRow();
        updateInvTotals();
        toast(lpoSeedToast(seed, lpoNo));
    } else {
        toast(seed.quantity_policy
            ? `${lpoNo}: no unbilled quantities are available for this invoice.`
            : `Invoice filed against ${lpoNo}. No ordered lines are recorded on it.`);
    }
    updateInvLpoWarning();
    return true;
}
window.adoptLpoIntoInv = adoptLpoIntoInv;
// Shared with dn.js, which starts a delivery note the same way.
window.lpoLinesForDocument = lpoLinesForDocument;
window.lpoSeedForDocument = lpoSeedForDocument;
window.lpoSeedToast = lpoSeedToast;


// "One line for the whole delivery note" (2026-07-24). Same hand-off as
// createInvoiceFromDnNo, but instead of copying the DN's rows it writes ONE
// summary line carrying the DN number, its total and its inherited cost - the
// same shape the invoice item-field's DN picker produces, so `dn_ref` still
// drives the appended DN pages and the PO-loop close on save.
async function createInvoiceFromDnAsLine(dnNo) {
    const rec = await api().load_dn(dnNo);
    if (!rec) { toast("Could not load delivery note " + dnNo, true); return; }
    let sum = null;
    try { sum = await api().dn_cost_summary(dnNo); } catch (e) { sum = null; }
    if (!sum || !sum.ok) { toast("Could not total that delivery note", true); return; }

    if (!invState.types || !Object.keys(invState.types).length) {
        invState.types = await api().get_inv_types();
    }
    invState.type = "materials";
    invState.schema = invState.types["materials"] || { label: "Materials Invoice", columns: [] };
    invState.initialized = false;

    hideAllViews();
    document.getElementById("invView").hidden = false;
    document.getElementById("invActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Invoice - " + invState.schema.label);
    invDraftUnsaved[invState.type] = false;
    if (window.showTypeRail) showTypeRail("Invoice Types", invRailItemsBadged(invState.type), invState.type, (k) => enterInvType(k));
    buildInvThead();
    await initNewInv();

    document.getElementById("invClient").value  = rec.client_name || "";
    document.getElementById("invAddress").value = rec.client_address || "";
    if (rec.lpo_no) document.getElementById("invLpo").value = rec.lpo_no;
    await populateInvClientList();
    await refreshInvNumber();

    const pk = invState.schema.primary_key;
    const row = {};
    editableInvColumns().forEach(c => { if (c.kind !== "computed") row[c.key] = c.default || ""; });
    row[pk] = dnNo;
    row.dn_ref = dnNo;                  // drives appended DN pages + PO-loop close
    row.unit = "EA";
    row.qty = 1;
    row.rate = invSellFromUsd(sum.sell_total);   // DN totals are USD
    /* qty is 1, so the DN's whole cost IS the per-unit cost. A DN with no
       costs recorded returns 0, and a literal 0 counts as a KNOWN cost
       everywhere downstream - the line would report 100% profit rather than
       "not costed". Leave it EMPTY and say so (2026-08-25, same rule as the
       item-field DN picker). */
    const _dnCost = invDnHasCost(sum) ? (Number(sum.cost_total) || 0) : 0;
    row.cost = _dnCost > 0 ? sum.cost_total : "";
    // Stamped with the DN's own cost-entry currency - the reconcile below
    // converts it into the invoice's and re-stamps. Same rule as the picker.
    if (_dnCost > 0) row.cost_ccy = (sum.currency || "USD").toString().toUpperCase();
    else delete row.cost_ccy;
    if (!(_dnCost > 0)) {
        toast(`${dnNo} has no costs recorded - cost left empty. `
            + "Add them in Costing so this invoice's profit is real.", true);
    }
    // `cost_total` comes back in the DN's OWN cost-entry currency (a DN costed in
    // IQD returns e.g. 1170000 against an $824 sell). Convert it into this
    // invoice's currency or the profit maths is wildly wrong - same reconcile the
    // itemised import does.
    if (_dnCost > 0 && window.reconcileImportedCostCurrency) {
        const _ie = document.getElementById("invNo");
        const _dst = ((_ie && _ie.value) || invState.editInvNo || invState.invNo || "").trim();
        await window.reconcileImportedCostCurrency([row], "dn", dnNo, "inv", _dst);
    }
    invState.rows = [row];

    invState.sourceDn = dnNo;           // Workflow link on save
    invState.sourcePo = null;           // this line came from a DN, not a PO
    invState.sourceQuote = rec.source_quote_no || "";
    { const sq = document.getElementById("invSrcQuote"); if (sq) sq.value = invState.sourceQuote; }
    populateInvQuoteList(rec.client_name || "").catch(() => {});

    invState.dirty = true;
    fullRenderInvRows();
    addInvRow();
    updateInvTotals();
    updateInvLpoWarning();
    toast(`${dnNo} added as one line`);
}
window.createInvoiceFromDnAsLine = createInvoiceFromDnAsLine;

// ============================================================
// Phase M.5a - Excel-style grid paste on invRowsBody
// ============================================================
function _pushEmptyInvRow() {
    const row = {};
    editableInvColumns().forEach(c => { if (c.kind !== 'computed') row[c.key] = (c.default || ''); });
    invState.rows.push(row);
}
function _wireInvGridPaste() {
    const tbody = document.getElementById('invRowsBody');
    if (!tbody || !window.setupGridPaste) return;
    const _renderAfterPaste = () => {
        fullRenderInvRows();
        if (filledInvRows().length === invState.rows.length) addInvRow();
        if (typeof updateInvTotals === 'function') updateInvTotals();
    };
    window.setupGridPaste(tbody, {
        columns:     () => editableInvColumns(),
        rowCount:    () => invState.rows.length,
        addEmptyRow: () => _pushEmptyInvRow(),
        setCell:     (i, k, v) => { if (invState.rows[i]) { invState.rows[i][k] = v; invState.dirty = true; } },
        fullRender:  _renderAfterPaste,
        // Phase M.5c - Ctrl+Z undo via snapshot/restore.
        snapshot: () => JSON.parse(JSON.stringify(invState.rows)),
        restore:  (snap) => {
            invState.rows = snap;
            invState.dirty = true;
            _renderAfterPaste();
        },
    });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _wireInvGridPaste);
else _wireInvGridPaste();
