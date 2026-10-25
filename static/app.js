/* PO Tool - frontend logic */

const api = () => window.pywebview.api;

const state = {
    poNo: "-",
    poDate: "-",
    rows: [],          // [{name, description, unit, qty, company}]
    mode: "new",       // "new" | "edit"
    editPoNo: null,
    dirtySinceSave: false,
    autofitRows: false,
    sourceQuote: "",
};
// Phase X.25 - `state.today` is ALWAYS the current local date. It used to be
// fetched once at bootstrap and cached, so a session left open across midnight
// (or a "New" clicked right after opening an older document) served a stale
// date to every editor (quote/dn/inv read state.today on new; PO reads the
// #today input). A live getter guarantees each new document grabs today. The
// no-op setter keeps the old `state.today = ...` bootstrap assignment harmless.
Object.defineProperty(state, "today", {
    get() { return todayInfo(); },
    set() { /* date is computed live - ignore stale assignments */ },
    enumerable: true, configurable: true,
});

let _poDirtySinceSave = !!state.dirtySinceSave;
Object.defineProperty(state, "dirtySinceSave", {
    get() { return _poDirtySinceSave; },
    set(v) {
        _poDirtySinceSave = !!v;
        refreshPoSavedState();
    },
    enumerable: true,
    configurable: true,
});

function refreshPoSavedState() {
    const dirty = !!state.dirtySinceSave;
    if (window.setUnsavedChip) window.setUnsavedChip(dirty);
    if (window.setEditorSavedState) {
        window.setEditorSavedState(state.mode === "edit" && !dirty);
    }
    if (window.refreshDocumentWorkflowButtons) window.refreshDocumentWorkflowButtons();
}
window.refreshPoSavedState = refreshPoSavedState;
window.getPoWorkflowContext = function () {
    return state.mode === "edit" && state.editPoNo
        ? { kind: "po", no: state.editPoNo }
        : null;
};

/* Whether this PO is safely on disk, for callers that must READ IT BACK
   (2026-09-05 - `po_handoff.js`, Make DN / Make Invoice). `state` is a module
   const, so it is not reachable as `window.state` from another script; the
   workflow context above says "saved" but not "saved and unchanged since", and
   converting a PO with unsaved edits would build a document from rows the
   `load_po` call cannot see. */
window.getPoSaveState = function () {
    return {
        mode: state.mode,
        poNo: state.editPoNo || state.poNo || "",
        dirty: !!state.dirtySinceSave,
        filled: (typeof filledRows === "function" ? filledRows().length : 0),
    };
};

function bindPoWorkflowButton() {
    const btn = document.getElementById("btnPoWorkflow");
    if (!btn || btn._workflowWired) return;
    btn._workflowWired = true;
    btn.onclick = () => {
        const ctx = window.getPoWorkflowContext();
        if (!ctx) { toast("Save this purchase order before opening Workflow", true); return; }
        if (window.openDocumentWorkflow) {
            window.openDocumentWorkflow(ctx.kind, ctx.no, { onAfter: reloadPoFulfillment });
        }
    };
}

const DOC_AUTOFIT_DEFAULTS = { po: false, qut: false, dn: false, inv: false };

function layoutAutofitValue(layout, fallback = false) {
    if (layout && typeof layout === "object" &&
        Object.prototype.hasOwnProperty.call(layout, "autofit_rows")) {
        return !!layout.autofit_rows;
    }
    return !!fallback;
}

function editorLayoutPayload(autofitRows) {
    return { autofit_rows: !!autofitRows };
}

function syncDocAutofitDefaults(settings = {}) {
    DOC_AUTOFIT_DEFAULTS.po = !!settings.autofit_rows;
    DOC_AUTOFIT_DEFAULTS.qut = layoutAutofitValue(settings.quotation && settings.quotation.layout, false);
    DOC_AUTOFIT_DEFAULTS.dn = layoutAutofitValue(settings.delivery && settings.delivery.layout, false);
    DOC_AUTOFIT_DEFAULTS.inv = layoutAutofitValue(settings.invoice && settings.invoice.layout, false);
    return { ...DOC_AUTOFIT_DEFAULTS };
}

function docAutofitDefault(kind) {
    return !!DOC_AUTOFIT_DEFAULTS[kind];
}

function syncAutofitButton(buttonId, value) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;
    const on = !!value;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "Auto-fit row height is on for this document"
                   : "Auto-fit row height is off for this document";
}

window.layoutAutofitValue = layoutAutofitValue;
window.editorLayoutPayload = editorLayoutPayload;
window.syncDocAutofitDefaults = syncDocAutofitDefaults;
window.docAutofitDefault = docAutofitDefault;
window.syncAutofitButton = syncAutofitButton;

// ============================================================
// Boot
// ============================================================
let _poInitialized = false;

async function bootstrap() {
    console.log("[bootstrap] starting");
    if (window.Gate) window.Gate.pageLoaded();
    let hubPainted = null;
    try {
        // These reads are independent. Finish all three before binding the
        // editors so dates, company identity and file-backed preferences agree.
        await Promise.all([initToday(), initCompanyProfile(), loadUserPreferences()]);
        bindUi();
        if (window.initOfficeManagement) window.initOfficeManagement();
        try {
            const access = await api().activity_can_view();
            const officeButton = document.getElementById("dashOfficeBtn");
            if (officeButton) officeButton.hidden = !(access && access.can);
        } catch (e) { /* non-server desktop mode has no office management */ }
        populateOpenSelectors();
        applyTheme(window.prefGet("theme", "light"), { persist: false });
        if (window.initQuoteModule) window.initQuoteModule();  // from quote.js
        if (window.initDnModule)    window.initDnModule();     // from dn.js
        if (window.initInvModule)   window.initInvModule();    // from inv.js
        applyCompactTables();                                  // restore table-density pref
        setupDocStatusSync();                                  // keep the topbar status cluster in sync
        hubPainted = showHub();                                // land on the hub
        initThemeToggle();                                     // Phase X.18 - dark mode control
        console.log("[bootstrap] complete");
    } catch (err) {
        console.error("[bootstrap] failed:", err);
        try { toast("خطأ في بدء التشغيل: " + (err && err.message), true); } catch {}
    }
    // Open the gate once the dashboard has drawn - or straight away if the
    // boot failed, so an error is never hidden behind closed doors.
    Promise.resolve(hubPainted).catch(() => {}).then(() => { if (window.Gate) window.Gate.ready(); });
}

// ============================================================
// Navigation (hub <-> document editors)
// ============================================================
const ALL_VIEWS = ["hubView", "editorView", "openView", "settingsView",
                   "quoteHubView", "quoteView", "quoteOpenView",
                   "dnView", "dnOpenView",
                   "invHubView", "invView", "invOpenView",
                   "dataManagerView", "analyticsView",
                   "lpoView", "lpoDocView", "itemMasterView", "clientMasterView",
                   "todoView", "activityView", "officeManagementView"];

function hideAllViews() {
    ALL_VIEWS.forEach(id => { const el = document.getElementById(id); if (el) el.hidden = true; });
    document.getElementById("poStrip").hidden = true;
    document.getElementById("poActions").hidden = true;
    document.getElementById("quoteActions").hidden = true;
    const dnA  = document.getElementById("dnActions");  if (dnA)  dnA.hidden  = true;
    const invA = document.getElementById("invActions"); if (invA) invA.hidden = true;
    // Phase M - leaving the dashboard restores the topbar. showHub() re-adds
    // this class after calling hideAllViews().
    document.body.classList.remove("on-dashboard");
    // Phase M.8 - hide the type rail; the entering view re-shows it if needed.
    hideTypeRail();
    // Any normal navigation clears the editor "back" breadcrumb. The two
    // return-aware sources (Clients / Recent) re-arm it AFTER their loader runs.
    if (window.clearEditorReturn) window.clearEditorReturn();
    // Hide the unified status cluster; the entering editor re-shows it.
    const ds = document.getElementById("docStatus");
    if (ds) ds.hidden = true;
    window.__currentEditorDoc = null;
    // An unsaved duplicate belongs to the editor you are leaving. Safe here
    // because duplicateDocument sets it AFTER the loader (and so after this
    // runs); the list refresh that follows does not come through here.
    if (window.clearPendingDoc) window.clearPendingDoc();
    // Phase Y - clear PO fulfillment chip/decoration; loadForEdit re-sets it.
    if (typeof state !== "undefined") state.poFulfillment = null;
    if (window.setPoFulfillChips) window.setPoFulfillChips("none", "none", "none");
}

// ============================================================
// Editor "back" breadcrumb - Clients / Recent → open a doc in its editor →
// a Back button returns one step to where the user came from. Only armed when
// a doc is opened from those two places; every other navigation clears it via
// hideAllViews().
// ============================================================
window.__editorReturn = null;
const _BACK_BTN_IDS = ["btnPoBack", "btnQBack", "btnDnBack", "btnInvBack"];
function refreshBackButtons() {
    const show = !!window.__editorReturn;
    _BACK_BTN_IDS.forEach(id => {
        const b = document.getElementById(id);
        if (b) {
            b.hidden = !show;
            const div = b.nextElementSibling;
            if (div && div.classList.contains("editor-back-div")) div.hidden = !show;
        }
    });
}
window.refreshBackButtons = refreshBackButtons;
window.setEditorReturn = function (ctx) {
    window.__editorReturn = (ctx && typeof ctx.run === "function") ? ctx : null;
    refreshBackButtons();
};
window.clearEditorReturn = function () {
    window.__editorReturn = null;
    refreshBackButtons();
};
window.runEditorReturn = async function () {
    const ctx = window.__editorReturn;
    window.__editorReturn = null;
    refreshBackButtons();
    if (ctx && ctx.run) { try { await ctx.run(); } catch (e) { /* ignore */ } }
};

// ============================================================
// Signed-status helpers - shared by the editors, the Open lists and the
// Clients profile so "Signed / Not signed" looks and reads the same everywhere.
// ============================================================
// True if a signing overlay actually places a signature or stamp (mirrors the
// backend signing.overlay_is_signed).
window.overlaySigned = function (ov) {
    if (!ov || typeof ov !== "object") return false;
    const slots = ov.slots || {};
    return Object.keys(slots).some((k) => {
        const v = slots[k];
        return v && (v.sig_index === 1 || v.sig_index === 2 || v.sig_index === 3 || !!v.stamp);
    });
};

const EDITOR_SAVE_BUTTON_IDS = ["btnPoStay", "btnQStay", "btnDnStay", "btnInvStay"];
const EDITOR_SIGN_BUTTON_IDS = ["btnPoSign", "btnQSign", "btnDnSign", "btnInvSign"];

window.setEditorSavedState = function (saved) {
    EDITOR_SAVE_BUTTON_IDS.forEach(id => {
        const btn = document.getElementById(id);
        if (!btn) return;
        const on = !!saved;
        btn.classList.toggle("is-saved", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
};

function setEditorSignButtonState(signed) {
    EDITOR_SIGN_BUTTON_IDS.forEach(id => {
        const btn = document.getElementById(id);
        if (!btn) return;
        const on = !!signed;
        btn.classList.toggle("is-signed", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
}

// Toggle the (now unified, topbar) signed status chip. `labels` is optional
// {yes, no} so the Arabic PO editor can localize it.
window.setSignChip = function (chipId, signed, labels) {
    const el = document.getElementById(chipId);
    if (!el) return;
    const L = labels || { yes: "Signed", no: "Not signed" };
    el.classList.toggle("is-signed", !!signed);
    el.classList.toggle("not-signed", !signed);
    el.textContent = signed ? L.yes : L.no;
    setEditorSignButtonState(!!signed);
};

// Unified "Edit mode" chip in the topbar status cluster. `text` lets each
// editor localize (English vs the Arabic PO).
window.setModeChip = function (show, text) {
    const c = document.getElementById("docModeChip");
    if (!c) return;
    if (text != null) c.textContent = text;
    c.hidden = !show;
};

// Unified "Unsaved" chip in the topbar status cluster.
window.setUnsavedChip = function (show) {
    const c = document.getElementById("docUnsavedChip");
    if (c) c.hidden = !show;
};

// Phase Q - "Submitted" chip in the topbar status cluster (read-only; the
// toggle lives on the right-panel doc cards). Also remembers the current
// editor doc so a submit from the panel can refresh the chip live.
// {kind, no} of the doc open in an editor. Exposed as an accessor so that every
// assignment (loadXForEdit sets it; navigation clears it to null) automatically
// re-highlights the matching row in the right-side Open panel - see Phase W.
(function () {
    let _doc = null;
    Object.defineProperty(window, "__currentEditorDoc", {
        configurable: true,
        get() { return _doc; },
        set(v) {
            _doc = v;
            // null means "no document is open". That has to reach the panel as
            // a CLEAR, not a re-highlight: the panel also remembers the last row
            // you clicked, and that memory used to survive New and keep the old
            // document lit up beside an empty editor (2026-10-01).
            try {
                const p = window.OpenPanel;
                if (!p) return;
                if (v == null && p.clearActive) p.clearActive();
                else if (p.markActive) p.markActive();
            } catch (e) {}
        },
    });
})();
// Inline badge markup for list rows (Open lists + Clients). `labels` optional
// {yes, no} for localized lists (e.g. the Arabic PO list).
window.signBadgeHTML = function (signed, labels) {
    const L = labels || { yes: "Signed", no: "Unsigned" };
    return signed
        ? '<span class="sign-badge is-signed" title="This document is signed">'
          + '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
          + L.yes + '</span>'
        : '<span class="sign-badge not-signed" title="This document has not been signed">' + L.no + '</span>';
};

// ============================================================
// Submitted state is carried by the compact toggle button. Right-panel badges
// show the latest operational stage instead: Linked / Delivered / Paid, with
// Signed visible only before the document advances.
// ============================================================
const _SUBMIT_ICON = '<svg aria-hidden="true" focusable="false" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
window.linkedPillHTML = function (linked) {
    return linked ? '<span class="linked-pill" title="Linked to a purchase order">Linked</span>' : "";
};
window.paidPillHTML = function (paid) {
    return paid ? '<span class="paid-pill" title="Marked as paid">Paid</span>' : "";
};
// PO stage pills. Each state is "none"|"partial"|"full"; show the latest stage.
window.poFulfillPillsHTML = function (delivered, invoiced, paid) {
    const pill = (state, kind) => {
        if (!state || state === "none") return "";
        const partial = state === "partial";
        const words = {
            delivered: partial ? "Partly delivered" : "Delivered",
            invoiced: partial ? "Partly invoiced" : "Invoiced",
            paid: partial ? "Partly paid" : "Paid",
        };
        const tips = {
            delivered: partial ? "Some PO items delivered" : "All PO items delivered",
            invoiced: partial ? "Some PO items invoiced" : "All PO items invoiced",
            paid: partial ? "Some PO items paid" : "All PO items paid",
        };
        const word = words[kind];
        const tip = tips[kind];
        return `<span class="fulfill-pill is-${kind}${partial ? " is-partial" : ""}" title="${tip}">${word}</span>`;
    };
    return pill(paid, "paid") || pill(invoiced, "invoiced") || pill(delivered, "delivered");
};
// Client-signature pill (DN 2026-07-24, invoices 2026-08-27) - whether the
// CLIENT returned a counter-signed copy, NOT whether we signed it digitally. Deliberately
// red/black (never red/green) and the colour is always paired with words, so
// the state is readable without relying on colour at all.
window.clientSignedPillHTML = function (signed) {
    return signed
        ? '<span class="client-sign-pill is-signed" title="The client returned a signed copy">'
          + '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>'
          + 'Signed by client</span>'
        : '<span class="client-sign-pill not-signed" title="No signed copy from the client yet">'
          + 'Awaiting client signature</span>';
};
// Whether a document should still nag for the client's signature. Chasing it is
// pointless once the money is in, or when the user waived it for this document.
// Shared by DN and invoice (2026-08-27): `paid` is the DN's invoice being paid,
// or - for an invoice - that invoice itself.
window.awaitsClientSignature = function (d) {
    return !d.client_signed && !d.client_sign_waived && !d.paid;
};
window.dnAwaitsClientSignature = window.awaitsClientSignature;   // original name
window.panelStatusHTML = function (kind, doc, labels) {
    const d = doc || {};
    if (d.incomplete_price) {
        return '<span class="incomplete-price-pill" title="Pricing is incomplete; this draft has no PDF"><b aria-hidden="true">!</b> No PDF</span>';
    }
    if (kind === "po") {
        const fulfillment = window.poFulfillPillsHTML(d.delivered, d.invoiced, d.paid);
        if (fulfillment) return fulfillment;
    }
    if (kind === "inv") {
        // 2026-08-27 - the client-signature pill shows ALONGSIDE Linked/Paid,
        // never instead of them (the mistake the DN branch had to fix below).
        const invoiceStatus = window.linkedPillHTML(!!d.linked) + window.paidPillHTML(!!d.paid);
        const clientSign = d.client_signed
            ? window.clientSignedPillHTML(true)
            : (window.awaitsClientSignature(d) ? window.clientSignedPillHTML(false) : "");
        if (invoiceStatus || clientSign) return invoiceStatus + clientSign;
    }
    if (kind === "dn") {
        // The client-signature pill shows ALONGSIDE Linked, not instead of it -
        // it used to be swallowed on exactly the DNs that were linked.
        const linkedPill = d.linked ? window.linkedPillHTML(true) : "";
        if (d.client_signed) return linkedPill + window.clientSignedPillHTML(true);
        // Paid or waived ⇒ no signature pill at all (nothing left to chase).
        if (!window.awaitsClientSignature(d)) return linkedPill;
        return linkedPill + window.clientSignedPillHTML(false);
    }
    if (d.submitted) return "";
    return window.signBadgeHTML ? window.signBadgeHTML(!!d.signed, labels) : "";
};
// One compact identity block shared by PO, quotation, DN and invoice cards.
// `statuses` is trusted markup produced by panelStatusHTML; every user/database
// value is escaped here, including Reference's title attribute.
window.panelCardMainHTML = function ({ no = "", date = "", reference = "", amount = "", statuses = "" } = {}) {
    const ref = String(reference || "").trim();
    const amountHtml = amount ? `<span class="panel-row-amount inv-row-amount">${esc(amount)}</span>` : "";
    const referenceHtml = ref
        ? `<div class="panel-row-reference" title="${esc(ref)}">${esc(ref)}</div>`
        : "";
    const statusesHtml = statuses
        ? `<div class="panel-row-statuses inv-row-statuses">${statuses}</div>`
        : "";
    return `<div class="panel-row-main inv-row-main">
        <div class="panel-row-head inv-row-head">
            <div class="po-num">${esc(no)}</div>
            <div class="panel-row-topmeta inv-row-topmeta">${amountHtml}<span>${esc(date)}</span></div>
        </div>
        ${referenceHtml}
        ${statusesHtml}
    </div>`;
};
// PO-editor header chip mirror of the pills (hidden when nothing is fulfilled).
window.setPoFulfillChips = function (delivered, invoiced, paid) {
    const c = document.getElementById("docFulfillChip");
    if (!c) return;
    const html = window.poFulfillPillsHTML(delivered, invoiced, paid);
    c.innerHTML = html;
    c.hidden = !html;
};
window.submitBtnHTML = function (kind, docNo, submitted) {
    const lbl = submitted ? "Submitted" : "Submit";
    const tip = submitted ? "Submitted - click to unmark" : "Mark as submitted";
    return `<button type="button" class="submit-btn${submitted ? " is-submitted" : ""}" data-act="submit"`
        + ` data-kind="${kind}" data-no="${esc(docNo)}" data-submitted="${submitted ? "1" : "0"}"`
        + ` title="${tip}" aria-label="${tip}" aria-pressed="${submitted ? "true" : "false"}">`
        + _SUBMIT_ICON + `<span>${lbl}</span></button>`;
};
// Wire every .submit-btn inside `container`. Toggles immediately and updates
// the button, the card's pill, the editor status chip AND the cached list
// entry (so re-renders / doc switches keep the state without a refresh).
//   dataArray + keyField: the module's list array and its id field, e.g.
//   (qOpenAllQuotes, "quote_no") - so entry.submitted stays in sync.
window.openSubmittedFulfillment = function (kind, no) {
    kind = String(kind || "").trim().toLowerCase();
    no = String(no || "").trim();
    if (!["dn", "inv"].includes(kind) || !no || !window.openPoFulfillPicker) return false;
    window.openPoFulfillPicker(kind, no);
    return true;
};

window.wireSubmitButtons = function (container, dataArray, keyField) {
    if (!container) return;
    container.querySelectorAll(".submit-btn[data-act='submit']").forEach(b => {
        b.onclick = async (e) => {
            e.stopPropagation();
            const kind = b.dataset.kind, no = b.dataset.no;
            const next = b.dataset.submitted !== "1";
            let r = null;
            try { r = await api().set_submitted(kind, no, next); }
            catch (err) { r = null; }
            if (!r || !r.ok) { toast("Couldn't update submitted status", true); return; }
            window.applySubmittedToButton(b, next);
            // Keep the cached list entry in sync so re-renders don't revert it.
            if (dataArray && keyField) {
                const entry = dataArray.find(x => x && x[keyField] === no);
                if (entry) entry.submitted = next;
            }
            toast(next ? "Marked as submitted" : "Submitted mark removed");
            if (window.refreshOpenPanel) window.refreshOpenPanel();
            // DN/Invoice submission continues into the same fulfillment picker
            // from every surface (right panel, To-Do, and dashboard).
            if (next) window.openSubmittedFulfillment(kind, no);
        };
    });
};

// Apply a submitted state to a single button + its card pill (no backend call).
window.applySubmittedToButton = function (b, submitted) {
    b.dataset.submitted = submitted ? "1" : "0";
    b.classList.toggle("is-submitted", submitted);
    b.setAttribute("aria-pressed", submitted ? "true" : "false");
    const tip = submitted ? "Submitted - click to unmark" : "Not submitted - click to mark";
    b.title = tip; b.setAttribute("aria-label", tip);
    const span = b.querySelector("span");
    if (span) span.textContent = submitted ? "Submitted" : "Submit";
    const card = b.closest(".po-row");
    if (card) {
        if (submitted) {
            const sign = card.querySelector(".sign-badge");
            if (sign) sign.remove();
        }
    }
};

// ============================================================
// User preferences - persisted in data/settings.json under "preferences".
// localStorage is only a compatibility/cache layer (mainly first-paint theme).
// ============================================================
const USER_PREF_DEFAULTS = {
    theme: "light",
    after_export: "copy",
    compact_tables: false,
    dn_prices_default: false,
};
const USER_PREF_BOOL_KEYS = new Set(["compact_tables", "dn_prices_default"]);
window.__userPreferences = { ...USER_PREF_DEFAULTS };

function _prefBoolValue(v, dflt = false) {
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return !!v;
    if (v == null) return !!dflt;
    const s = String(v).trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(s)) return true;
    if (["0", "false", "no", "off"].includes(s)) return false;
    return !!dflt;
}
function _normalizePreference(key, val) {
    if (key === "theme") return val === "dark" ? "dark" : "light";
    if (key === "after_export") {
        return ["copy", "open", "reveal", "none"].includes(val) ? val : "copy";
    }
    if (USER_PREF_BOOL_KEYS.has(key)) return _prefBoolValue(val, USER_PREF_DEFAULTS[key]);
    return val;
}
function _cachePreference(key, val) {
    try {
        const stored = typeof val === "boolean" ? (val ? "1" : "0") : String(val);
        localStorage.setItem("abraj_" + key, stored);
    } catch (e) { /* ignore */ }
}
function currentUserPreferences() {
    return { ...USER_PREF_DEFAULTS, ...(window.__userPreferences || {}) };
}
function syncUserPreferences(prefs = {}) {
    const next = { ...USER_PREF_DEFAULTS };
    Object.keys(USER_PREF_DEFAULTS).forEach(k => {
        if (Object.prototype.hasOwnProperty.call(prefs || {}, k)) {
            next[k] = _normalizePreference(k, prefs[k]);
        } else {
            try {
                const cached = localStorage.getItem("abraj_" + k);
                if (cached != null) next[k] = _normalizePreference(k, cached);
            } catch (e) { /* ignore */ }
        }
        _cachePreference(k, next[k]);
    });
    window.__userPreferences = next;
    return next;
}
async function loadUserPreferences(settings = null) {
    let s = settings;
    if (!s) {
        try { s = await api().get_settings(); }
        catch (e) { s = {}; }
    }
    syncUserPreferences((s && s.preferences) || {});
    syncDocAutofitDefaults(s || {});
    return s || {};
}
function persistUserPreferences() {
    if (!(window.pywebview && window.pywebview.api)) return;
    try {
        api().save_settings({ preferences: currentUserPreferences() })
            .then(r => { if (r && r.ok === false) toast("Couldn't save preference: " + r.error, true); })
            .catch(() => toast("Couldn't save preference", true));
    } catch (e) { /* bridge not ready */ }
}
window.prefGet = function (key, dflt) {
    const prefs = window.__userPreferences || {};
    if (Object.prototype.hasOwnProperty.call(prefs, key)) return prefs[key];
    try {
        const v = localStorage.getItem("abraj_" + key);
        return v == null ? dflt : _normalizePreference(key, v);
    } catch (e) { return dflt; }
};
window.prefSet = function (key, val, opts = {}) {
    const next = _normalizePreference(key, val);
    window.__userPreferences = { ...currentUserPreferences(), [key]: next };
    _cachePreference(key, next);
    if (opts.persist !== false) persistUserPreferences();
};
window.prefBool = function (key, dflt) {
    return _prefBoolValue(window.prefGet(key, dflt), dflt);
};
window.currentUserPreferences = currentUserPreferences;
window.syncUserPreferences = syncUserPreferences;
window.loadUserPreferences = loadUserPreferences;
function applyCompactTables() {
    document.body.classList.toggle("compact-tables", window.prefBool("compact_tables", false));
}
window.applyCompactTables = applyCompactTables;

// Centralized "after export" behaviour - honoured by every editor's Export
// so the preference lives in one place. Always shows a confirming toast.
window.afterExportPdf = async function (path, label) {
    const action = window.prefGet("after_export", "copy");
    try {
        if (action === "open") { await api().open_path(path); toast(`Exported ${label} - opened`); return; }
        if (action === "reveal") { await api().reveal_in_folder(path); toast(`Exported ${label} - shown in folder`); return; }
        if (action === "none") { toast(`Exported ${label}`); return; }
        // default: copy the file to the clipboard
        let ok = false;
        try { const c = await api().copy_file_to_clipboard(path); ok = !!(c && c.ok); } catch (e) { /* ignore */ }
        toast(ok ? `Exported ${label} - PDF copied to clipboard` : `Exported ${label}`);
    } catch (e) { toast(`Exported ${label}`); }
};

// Arabic-toast variant for the Purchase Order editor (same preference).
async function poAfterExport(path, label) {
    const action = window.prefGet ? window.prefGet("after_export", "copy") : "copy";
    try {
        if (action === "open")   { await api().open_path(path); toast(`تم التصدير ${label} - فُتح الملف`); return; }
        if (action === "reveal") { await api().reveal_in_folder(path); toast(`تم التصدير ${label} - عُرض في المجلد`); return; }
        if (action === "none")   { toast(`تم التصدير ${label}`); return; }
        let ok = false;
        try { const c = await api().copy_file_to_clipboard(path); ok = !!(c && c.ok); } catch (e) { /* ignore */ }
        toast(ok ? `تم التصدير ${label} - نُسخ الـPDF للحافظة` : `تم التصدير ${label}`);
    } catch (e) { toast(`تم التصدير ${label}`); }
}

// ============================================================
// Phase M.8 - Type rail (master–detail sidebar)
// Per-sub-type icon, accent color, and one-line description. Keys match the
// backend type keys for Quotation/Invoice (materials/equipment/manpower/
// service).
// ============================================================
const RAIL_ICONS = {
    box:    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>',
    truck:  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>',
    people: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    wrench: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    doc:    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    globe:  '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>',
};
const RAIL_META = {
    materials: { color: "#2563EB", sub: "Supply & materials", icon: "box" },
    equipment: { color: "#0891B2", sub: "Rental & equipment", icon: "truck" },
    manpower:  { color: "#7C3AED", sub: "Labor & manpower",   icon: "people" },
    service:   { color: "#059669", sub: "Services rendered",  icon: "wrench" },
};

// Show the rail. `items` = [{key,label}], activeKey highlighted, onSelect(key).
function showTypeRail(headText, items, activeKey, onSelect) {
    const rail = document.getElementById("typeRail");
    const list = document.getElementById("typeRailList");
    const head = document.getElementById("typeRailHead");
    if (!rail || !list) return;
    if (head) head.textContent = headText;
    list.innerHTML = (items || []).map(it => {
        const m = RAIL_META[it.key] || {};
        const color = m.color || "var(--navy)";
        const icon = RAIL_ICONS[m.icon] || RAIL_ICONS.doc;
        const sub = m.sub ? `<span class="type-rail-sub">${esc(m.sub)}</span>` : "";
        // A pulsing dot marks types with an unsaved draft you've switched away from.
        const badge = (it.badge && it.key !== activeKey)
            ? `<span class="type-rail-badge" title="Unsaved draft"></span>` : "";
        return `<button class="type-rail-item${it.key === activeKey ? " active" : ""}" data-key="${esc(it.key)}" style="--rail-accent:${color}">
                    <span class="type-rail-icon" aria-hidden="true">${icon}</span>
                    <span class="type-rail-text"><span class="type-rail-label">${esc(it.label)}</span>${sub}</span>
                    ${badge}
                </button>`;
    }).join("");
    list.querySelectorAll(".type-rail-item").forEach(b =>
        b.onclick = safe(() => onSelect(b.dataset.key), "Open " + (b.querySelector(".type-rail-label") || {}).textContent));
    rail.hidden = false;
    document.body.classList.add("has-rail");
    // Phase M.8c - tag the rail with the active section so CSS can pick up
    // the right accent tone (the leading-edge stripe + the head dot).
    rail.classList.remove("rail-qut", "rail-inv");
    if (!document.getElementById("quoteView").hidden) rail.classList.add("rail-qut");
    else if (!document.getElementById("invView").hidden) rail.classList.add("rail-inv");
}
window.showTypeRail = showTypeRail;

function hideTypeRail() {
    const rail = document.getElementById("typeRail");
    if (rail) rail.hidden = true;
    document.body.classList.remove("has-rail");
}
window.hideTypeRail = hideTypeRail;

function setBrand(text) { document.getElementById("brandText").textContent = text; }

// Show the universal (app-wide) ⚙ only when we're on the main hub. Inside any
// editor / mini-hub, the per-section ⚙ on that view handles its settings.
const EDITOR_ACTION_IDS = ["poActions", "quoteActions", "dnActions", "invActions"];

// The unified topbar status cluster shows only while a document editor is
// active. Driven off the action groups so it stays correct on EVERY path
// (new doc, opening an old doc, switching types) without each one remembering
// to call it.
function syncDocStatusVisibility() {
    const editorActive = EDITOR_ACTION_IDS.some(id => {
        const el = document.getElementById(id); return el && !el.hidden;
    });
    const ds = document.getElementById("docStatus");
    if (ds) ds.hidden = !editorActive;
}

// Re-sync whenever any editor action group is shown or hidden - covers the
// loader paths that don't call refreshUniversalSettingsBtn().
function setupDocStatusSync() {
    EDITOR_ACTION_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el) new MutationObserver(syncDocStatusVisibility)
            .observe(el, { attributes: true, attributeFilter: ["hidden"] });
    });
    syncDocStatusVisibility();
}

function refreshUniversalSettingsBtn() {
    const onHub = !document.getElementById("hubView").hidden;
    const btn = document.getElementById("btnSettings");
    if (btn) btn.hidden = !onHub;
    syncDocStatusVisibility();
}
window.refreshUniversalSettingsBtn = refreshUniversalSettingsBtn;

// Phase X.11 - hero logo: flip + gold shine on click. The idle float is the
// element's default animation; `.is-flip` overrides it for the flip and is
// cleared afterwards so the float resumes. Bound once (idempotent).
function bindHeroLogoFlip() {
    const mark = document.querySelector("#hubView .dash-hero-mark");
    if (!mark || mark.dataset.flipBound) return;
    mark.dataset.flipBound = "1";
    mark.setAttribute("role", "button");
    mark.setAttribute("tabindex", "0");
    mark.setAttribute("aria-label", "ABRAJ");
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const play = () => {
        if (reduce) return;
        mark.classList.remove("is-flip");
        void mark.offsetWidth;                 // restart the animation
        mark.classList.add("is-flip");
        clearTimeout(mark._flipT);
        mark._flipT = setTimeout(() => mark.classList.remove("is-flip"), 1050);
    };
    mark.addEventListener("click", play);
    mark.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); play(); }
    });
}
window.bindHeroLogoFlip = bindHeroLogoFlip;

// Phase X.14 - animated Lottie icons on the Create New cards. Each mapped card's
// static SVG glyph is replaced by a Lottie animation (bundled JSON, served via
// the api bridge so it works under file://). Plays once on landing + on hover.
// Reduced-motion: shows the finished frame, no autoplay.
const CARD_LOTTIE = { inv: "inv", dn: "dn", po: "po", quote: "doc_lordicon" };
// `CARD_HOVER_START` (a per-slug "frame where the real motion begins") is gone:
// po, dn and inv were rebuilt on the Lordicon document base 2026-08-02 and all
// carry a `default:hover-swipe` marker, so every card icon now takes the
// playSegments path and nothing read it any more. A future marker-less icon
// just replays from frame 0.
const _cardAnims = {};

// Lordicon-style JSONs carry named markers ("in-*", "hover-*"). Derive the
// reveal segment, a hover gesture, and the formed rest frame from them. Returns
// null for our hand-built card icons (no markers) so they keep their own timing.
function lottieSegments(data) {
    const mk = (data && data.markers) || [];
    if (!mk.length) return null;
    const seg = (m) => m ? [Math.round(m.tm), Math.round(m.tm + m.dr)] : null;
    const segIn = seg(mk.find(m => /in/i.test(m.cm || "")));
    const segHover = seg(mk.find(m => /hover/i.test(m.cm || "") && !/morph/i.test(m.cm || "")));
    return { segIn: segIn, segHover: segHover || segIn, rest: segIn ? segIn[1] - 1 : (data.op || 1) - 1 };
}

// Bundled animations are shared across profiles. Reuse even an in-flight
// request, but give each host its own copy: Lottie mutates animationData.
const _lottieDataRequests = new Map();
async function loadLottieData(slug) {
    let request = _lottieDataRequests.get(slug);
    if (!request) {
        request = Promise.resolve().then(() => api().get_lottie(slug))
            .then(data => {
                if (!data || !Array.isArray(data.layers)) {
                    _lottieDataRequests.delete(slug);
                    return null;
                }
                return data;
            }, () => {
                _lottieDataRequests.delete(slug);
                return null;
            });
        _lottieDataRequests.set(slug, request);
    }
    const data = await request;
    return data ? JSON.parse(JSON.stringify(data)) : null;
}

async function initCardLotties() {
    if (!window.lottie) return;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let shown = 0;
    for (const doc of Object.keys(CARD_LOTTIE)) {
        const card = document.querySelector(`#hubView .dash-row.cols-4 .dash-card[data-doc="${doc}"]`);
        if (!card) continue;
        const chip = card.querySelector(".dash-card-icon");
        if (!chip || chip.dataset.lottie) continue;     // once per chip
        let data = null;
        try { data = await loadLottieData(CARD_LOTTIE[doc]); } catch (e) { data = null; }
        if (!data || !data.layers || chip.dataset.lottie) continue; // keep static SVG or existing mount
        chip.dataset.lottie = "1";
        chip.innerHTML = "";
        const box = document.createElement("div");
        box.className = "dash-lottie";
        chip.appendChild(box);
        const anim = window.lottie.loadAnimation({
            container: box, renderer: "svg", loop: false, autoplay: false, animationData: data,
        });
        _cardAnims[doc] = anim;
        const seg = lottieSegments(data);                // marker-based
        const playSeg = seg && (seg.segIn || seg.segHover);   // doc_lordicon has only a hover marker
        const rest = seg ? seg.rest : (data.op || 1) - 1;
        const play = playSeg
            ? () => anim.playSegments(playSeg, true)      // re-draw / replay the gesture
            : () => anim.goToAndPlay(0, true);            // marker-less fallback
        if (reduce) {
            anim.addEventListener("DOMLoaded", () => anim.goToAndStop(rest, true));
        } else {
            const delay = 350 + shown * 180; shown++;
            anim.addEventListener("DOMLoaded", () => {
                anim.goToAndStop(rest, true);            // rest on the finished icon
                setTimeout(play, delay);
            });
            card.addEventListener("mouseenter", play);
        }
    }
}

// Phase X.15 - Lordicon icons across the rest of the app, wired by selector so
// the SAME concept shows the SAME icon everywhere it appears. Each target's
// existing <svg> is swapped for the matching Lottie (kept at the svg's size;
// any text label is untouched). Rests on the formed frame; plays its hover
// gesture on mouseenter, then settles back. One source file per slug.
const LICON_MAP = [
    ["home",     "#btnHome"],
    ["settings", "#btnSettings, #dashSettingsBtn, #btnPoSettings, #btnQTypeSettings, #btnDnSettings, #btnInvTypeSettings"],
    ["preview",  "#btnPreview, #btnQPreview, #btnDnPreview, #btnInvPreview"],
    ["refresh",  "#btnRefreshList, #btnQRefreshList, #btnDnRefreshList, #btnInvRefreshList, #costRefresh, #opRefreshBtn"],
    ["autofill", "#dashOpenAutofill .dash-card-icon"],
    // 2026-08-01 - the shield in the stock "privacy policy" icon was replaced
    // with the word LPO, drawn as stroked paths with trim-path write-on so it
    // matches the document lines. Only a hover marker, like doc_lordicon, so
    // lottieSegments gives segIn=null and it rests on the formed frame (88).
    ["lpo",      "#dashOpenLpos .dash-card-icon"],
    // Phase X.17 - hand-built editor-toolbar icons (traced from the videos).
    ["new",      "#btnNew, #btnQNew, #btnDnNew, #btnInvNew"],
    ["import",   "#btnDnImport, #btnInvImport"],
    ["save",     "#btnPoStay, #btnQStay, #btnDnStay, #btnInvStay"],
    ["sign",     "#btnPoSign, #btnQSign, #btnDnSign, #btnInvSign"],
];
async function mountLottieIcon(host, slug) {
    if (!window.lottie || !host || host.dataset.liconReady) return;
    let data = null;
    try { data = await loadLottieData(slug); } catch (e) { data = null; }
    if (!data || !data.layers || host.dataset.liconReady) return; // keep static SVG or existing mount
    const svg = host.querySelector("svg");
    const box = document.createElement("span");
    box.className = "licon-box";
    if (svg) {
        const w = parseFloat(svg.getAttribute("width")) || 20;
        const h = parseFloat(svg.getAttribute("height")) || w;
        box.style.width = w + "px"; box.style.height = h + "px";
        svg.replaceWith(box);                           // keep the button's label
    } else {
        box.style.width = "100%"; box.style.height = "100%";
        host.appendChild(box);
    }
    host.dataset.liconReady = "1";
    const anim = window.lottie.loadAnimation({
        container: box, renderer: "svg", loop: false, autoplay: false, animationData: data,
    });
    const seg = lottieSegments(data) || { segHover: null, rest: (data.op || 1) - 1 };
    anim.addEventListener("DOMLoaded", () => anim.goToAndStop(seg.rest, true));
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    // Lordicon files play their hover marker; hand-built icons (no markers) replay the whole clip.
    const hover = seg.segHover
        ? () => anim.playSegments(seg.segHover, true)
        : () => anim.goToAndPlay(0, true);
    const trigger = host.closest("button, a, .dash-card") || host;
    trigger.addEventListener("mouseenter", () => {
        hover();
        anim.addEventListener("complete", function once() {
            anim.removeEventListener("complete", once);
            anim.goToAndStop(seg.rest, true);           // always settle formed
        });
    });
}
function initLottieIcons(root) {
    if (!window.lottie) return;
    const scope = root || document;
    LICON_MAP.forEach(([slug, sel]) => {
        scope.querySelectorAll(sel).forEach(host => mountLottieIcon(host, slug));
    });
}
window.initLottieIcons = initLottieIcons;

// Phase X.18 - dark mode. The cached theme is applied before first paint by an
// inline <head> script (no flash); boot reconciles it with settings.json. This
// syncs the Settings segmented control and handles user flips.
function syncThemeControls(theme) {
    const next = theme === "dark" ? "dark" : "light";
    const dark = next === "dark";
    document.querySelectorAll("#themeSeg .theme-seg-opt").forEach(function (b) {
        b.classList.toggle("active", b.dataset.themeVal === next);
    });
    const dashToggle = document.getElementById("dashThemeToggle");
    if (dashToggle) {
        dashToggle.classList.toggle("is-dark", dark);
        dashToggle.setAttribute("aria-pressed", dark ? "true" : "false");
        dashToggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
        dashToggle.title = dark ? "Switch to light mode" : "Switch to dark mode";
    }
    setThemeLottie(dark, true);   // animate the day/night pill to match
}

// Phase X.30 - Lottie day/night pill for the dashboard theme toggle. The clip is
// a one-way day→night→day loop; the sun→moon slide lives in frames [0, 120]. We
// drive that single slide forward for light→dark and reversed for dark→light, so
// there is no dead dwell and no frame jump. Falls back to the CSS sun/moon toggle
// (kept in the markup) if the bridge/asset is unavailable.
const THEME_LOTTIE_LIGHT = 0;
const THEME_LOTTIE_DARK = 120;
const THEME_LOTTIE_DUR_MS = 850;
let _themeAnim = null;
let _themeAnimState = null;      // "light" | "dark" - the pill's settled state
let _themeAnimPending = null;    // desired state requested before DOMLoaded
let _themeAnimFrame = THEME_LOTTIE_LIGHT;   // the pill's current (possibly mid-tween) frame
let _themeAnimRaf = 0;
function setThemeLottie(dark, animate) {
    const target = dark ? "dark" : "light";
    if (!_themeAnim) { _themeAnimPending = target; return; }
    if (_themeAnimState === target) return;
    _themeAnimState = target;
    const targetFrame = dark ? THEME_LOTTIE_DARK : THEME_LOTTIE_LIGHT;
    if (_themeAnimRaf) { cancelAnimationFrame(_themeAnimRaf); _themeAnimRaf = 0; }
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!animate || reduce) {
        _themeAnimFrame = targetFrame;
        _themeAnim.goToAndStop(targetFrame, true);
        return;
    }
    // This build's goToAndPlay/playSegments don't clamp reliably, but goToAndStop
    // renders any (fractional) frame exactly - so we tween the sun→moon slide
    // [0,120] ourselves. Starting from the live frame makes mid-flight re-toggles
    // reverse smoothly instead of snapping.
    const startFrame = _themeAnimFrame;
    const t0 = performance.now();
    const ease = (p) => (p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2);   // easeInOutQuad
    const step = (now) => {
        const p = Math.min(1, (now - t0) / THEME_LOTTIE_DUR_MS);
        _themeAnimFrame = startFrame + (targetFrame - startFrame) * ease(p);
        _themeAnim.goToAndStop(_themeAnimFrame, true);
        if (p < 1) {
            _themeAnimRaf = requestAnimationFrame(step);
        } else {
            _themeAnimRaf = 0;
            _themeAnimFrame = targetFrame;
            _themeAnim.goToAndStop(targetFrame, true);
        }
    };
    _themeAnimRaf = requestAnimationFrame(step);
}
async function initThemeLottie() {
    const btn = document.getElementById("dashThemeToggle");
    if (!btn || !window.lottie || btn._lottieReady) return;
    let data = null;
    try { data = await loadLottieData("theme"); } catch (e) { data = null; }
    if (!data || !data.layers || btn._lottieReady) return; // keep CSS fallback or existing mount
    btn._lottieReady = true;
    btn.classList.add("has-lottie");
    btn.classList.remove("glass");                     // the pill owns its own look
    const box = document.createElement("span");
    box.className = "dash-theme-lottie";
    btn.innerHTML = "";
    btn.appendChild(box);
    const anim = window.lottie.loadAnimation({
        container: box, renderer: "svg", loop: false, autoplay: false, animationData: data,
    });
    anim.setSpeed(1.5);
    _themeAnim = anim;
    anim.addEventListener("DOMLoaded", () => {
        const dark = document.documentElement.getAttribute("data-theme") === "dark";
        _themeAnimFrame = dark ? THEME_LOTTIE_DARK : THEME_LOTTIE_LIGHT;
        anim.goToAndStop(_themeAnimFrame, true);
        _themeAnimState = dark ? "dark" : "light";
        // Honour any theme flip that happened before the asset finished loading.
        if (_themeAnimPending && _themeAnimPending !== _themeAnimState) {
            setThemeLottie(_themeAnimPending === "dark", true);
        }
        _themeAnimPending = null;
    });
}
window.initThemeLottie = initThemeLottie;
function applyTheme(theme, opts = {}) {
    const next = theme === "dark" ? "dark" : "light";
    const dark = next === "dark";
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    try { localStorage.setItem("abraj_theme", next); } catch (e) { /* ignore */ }
    if (window.prefSet) window.prefSet("theme", next, { persist: opts.persist !== false });
    syncThemeControls(next);
    // Phase X.19 - re-tint the Insights donut to the new theme if it's on screen.
    if (typeof _bidDonutData !== "undefined" && _bidDonutData) {
        try { renderBidDonut(_bidDonutData.segs, _bidDonutData.winRate); } catch (e) { /* noop */ }
    }
    // Phase X.20 - re-theme the full Insights view charts if it's visible.
    if (window.rerenderAnalytics) { try { window.rerenderAnalytics(); } catch (e) { /* noop */ } }
}
function initThemeToggle() {
    const seg = document.getElementById("themeSeg");
    const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    syncThemeControls(cur);
    if (seg) {
        seg.querySelectorAll(".theme-seg-opt").forEach(function (b) {
            b.onclick = function () { applyTheme(b.dataset.themeVal); };
        });
    }
    const dashToggle = document.getElementById("dashThemeToggle");
    if (dashToggle && !dashToggle._wired) {
        dashToggle.onclick = function () {
            const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
            applyTheme(current === "dark" ? "light" : "dark");
        };
        dashToggle._wired = true;
    }
}
window.applyTheme = applyTheme;
window.syncThemeControls = syncThemeControls;
window.initThemeToggle = initThemeToggle;
window.initCardLotties = initCardLotties;

let _activeCompanyProfile = null;

function profileDisplayName(profileId) {
    const profiles = (_activeCompanyProfile && _activeCompanyProfile.profiles) || [];
    const match = profiles.find(p => p.id === profileId);
    return (match && match.label) || (profileId === "eshraqat" ? "Eshraqat Albadr" : "Abraj Albadr");
}

function closeProfileMenu() {
    const host = document.getElementById("dashProfileSwitcher");
    const button = document.getElementById("dashProfileButton");
    const menu = document.getElementById("dashProfileMenu");
    if (host) host.classList.remove("is-open");
    if (button) button.setAttribute("aria-expanded", "false");
    if (menu) menu.hidden = true;
}

function applyCompanyProfile(metadata) {
    if (!metadata || !metadata.id) return;
    _activeCompanyProfile = metadata;
    const label = profileDisplayName(metadata.id);
    const shortName = metadata.id === "eshraqat" ? "ESHRAQAT" : "ABRAJ";
    const labelEl = document.getElementById("dashProfileLabel");
    const heroCompany = document.getElementById("dashHeroCompany");
    if (labelEl) labelEl.textContent = label;
    if (heroCompany) heroCompany.textContent = `${shortName} Business Hub`;
    document.documentElement.style.setProperty("--profile-accent", metadata.brand_color || "#1F3864");
    // Profile hook for skin-specific dashboard styling (e.g. Eshraqat red hero).
    document.documentElement.setAttribute("data-profile", metadata.id);
    // Profit-math USD peg for client-side cost currency conversion (Abraj 1400,
    // Eshraqat 1500). Read lazily by costing.js / quote.js / cost-import reconcile.
    window.IQD_PER_USD = Number(metadata.iqd_per_usd) || 1400;
    // Markup assumed for a line with no cost (config.UNCOSTED_MARKUP).
    if (Number.isFinite(Number(metadata.uncosted_markup))) window.UNCOSTED_MARKUP = Number(metadata.uncosted_markup);

    [document.getElementById("dashProfileLogo"), document.getElementById("dashHeroLogo")]
        .filter(Boolean)
        .forEach(img => {
            img.src = metadata.logo_data_uri || "";
            img.hidden = !metadata.logo_data_uri;
            if (img.parentElement) img.parentElement.style.display = metadata.logo_data_uri ? "" : "none";
        });

    document.querySelectorAll("#dashProfileMenu [data-profile-id]").forEach(option => {
        const active = option.dataset.profileId === metadata.id;
        option.setAttribute("aria-checked", active ? "true" : "false");
        option.classList.toggle("active", active);
    });
}

function initProfileSwitcher() {
    const host = document.getElementById("dashProfileSwitcher");
    const button = document.getElementById("dashProfileButton");
    const menu = document.getElementById("dashProfileMenu");
    if (!host || !button || !menu || button._wired) return;
    button.onclick = () => {
        const open = button.getAttribute("aria-expanded") === "true";
        host.classList.toggle("is-open", !open);
        button.setAttribute("aria-expanded", open ? "false" : "true");
        menu.hidden = open;
        if (!open) {
            const selected = menu.querySelector('[aria-checked="true"]');
            if (selected && selected.focus) selected.focus();
        }
    };
    menu.querySelectorAll("[data-profile-id]").forEach(option => {
        option.onclick = () => requestProfileSwitch(option.dataset.profileId);
    });
    document.addEventListener("click", (event) => {
        if (!host.contains(event.target)) closeProfileMenu();
    });
    menu.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.preventDefault();
            closeProfileMenu();
            button.focus();
        }
    });
    button._wired = true;
}

async function initCompanyProfile() {
    const metadata = await api().get_active_profile();
    applyCompanyProfile(metadata);
    initProfileSwitcher();
    if (metadata.warning) toast(metadata.warning, true);
}

// Which editor is holding unsaved work, for the profile switcher and the
// duplicate flow. It asks `unsavedGuard` rather than reading the four dirty
// flags itself, so it answers the SAME question the New and Home buttons ask:
// dirty AND non-empty. It used to read `dirty` alone, which meant switching
// company while a freshly opened document sat in an editor asked to save a
// document nobody had touched - the same complaint Waleed raised about New.
// Each editor registers itself (see static/unsaved_guard.js); PO registers
// just below, quote.js / dn.js / inv.js register as they load.
function activeDirtyEditor() {
    return (window.unsavedGuard && window.unsavedGuard.dirtyEditor()) || null;
}

// PO's entry in the shared registry.
if (window.unsavedGuard) {
    window.unsavedGuard.register("po", {
        noun: "purchase order",
        label: "Purchase Order",
        dirty: () => !!state.dirtySinceSave,
        filled: () => filledRows(),
        save: (opts) => savePO(opts),
    });
}

async function requestProfileSwitch(profileId) {
    if (!profileId || (_activeCompanyProfile && profileId === _activeCompanyProfile.id)) {
        closeProfileMenu();
        return false;
    }
    const dirtyEditor = activeDirtyEditor();
    if (dirtyEditor) {
        const prompt = window.profileSwitchPrompt || ((options) => showModal(options));
        const choice = await prompt({
            title: "Unsaved document",
            body: `Save the current ${dirtyEditor.kind} before switching to ${profileDisplayName(profileId)}?`,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Discard", value: "discard", cls: "warm-red" },
                { label: "Save", value: "save", cls: "primary" },
            ],
        });
        if (choice === "cancel" || !choice) return false;
        if (choice === "save") {
            const saved = await dirtyEditor.save();
            if (!saved) return false;
        }
    }

    const button = document.getElementById("dashProfileButton");
    if (button) button.disabled = true;
    try {
        closeProfileMenu();
        // The gate closes in this company's colours, changes brand while shut,
        // and stays shut through the reload (static/gate.js).
        const doSwitch = () => api().switch_profile(profileId);
        const result = window.Gate
            ? await window.Gate.switchTo(gateBrand(_activeCompanyProfile && _activeCompanyProfile.id), gateBrand(profileId), doSwitch)
            : await doSwitch();
        if (!result || !result.ok) {
            toast((result && result.error) || "Company switch failed", true);
            return false;
        }
        closeProfileMenu();
        if (result.reload) window.location.reload();
        return true;
    } catch (error) {
        toast("Company switch failed: " + (error && error.message || error), true);
        return false;
    } finally {
        if (button) button.disabled = false;
    }
}

// What the gate's left door shows for a company.
function gateBrand(profileId) {
    const meta = _activeCompanyProfile || {};
    const entry = (meta.profiles || []).find(p => p.id === profileId) || {};
    return {
        id: profileId,
        label: profileDisplayName(profileId),
        accent: entry.brand_color || (profileId === meta.id ? meta.brand_color : "") || "#1F3864",
        logo: entry.logo_data_uri || (profileId === meta.id ? meta.logo_data_uri : "") || "",
    };
}

// Ctrl+L and the dashboard's lock button: close the gate over the app.
function lockScreen() {
    if (!window.Gate) return;
    if (window.Popup && window.Popup.count && window.Popup.count()) {
        // A popup may hold half-typed work and its own keyboard focus; locking
        // over it would leave that focus live behind the doors.
        toast("Close the open window first, then lock.", true);
        return;
    }
    window.Gate.lock(gateBrand(_activeCompanyProfile && _activeCompanyProfile.id));
}
window.lockScreen = lockScreen;

window.applyCompanyProfile = applyCompanyProfile;
window.initCompanyProfile = initCompanyProfile;
window.activeDirtyEditor = activeDirtyEditor;
window.requestProfileSwitch = requestProfileSwitch;

function scheduleDashboardAnimations() {
    // Data shares the bridge's profile lock with assets. Let the hub finish
    // and paint before optional SVG animation work enters that same queue.
    requestAnimationFrame(() => requestAnimationFrame(() => {
        initCardLotties();
        initLottieIcons();
        initThemeLottie();
    }));
}

function showHub() {
    hideAllViews();
    document.getElementById("hubView").hidden = false;
    document.getElementById("btnHome").hidden = true;     // already home
    document.body.classList.add("on-dashboard");          // hides the topbar
    setBrand(_activeCompanyProfile ? `${profileDisplayName(_activeCompanyProfile.id)} Documents` : "Business Hub");
    refreshUniversalSettingsBtn();
    // Phase M - refresh the dashboard's greeting / time / stats / recent each
    // time we land here. Fire-and-forget; failures degrade gracefully.
    // Returned so bootstrap can open the gate once the dashboard has drawn.
    const painted = renderDashboard().catch((error) => {
        console.error("[dashboard] render failed", error);
        renderDashboardError("all");
    });
    // The icon animations load fifteen files through the bridge; started while
    // the gate is opening they queued its "ready" call ~0.3 s (measured
    // 2026-10-02). Gate.settled() resolves once the doors are apart.
    Promise.all([painted, window.Gate ? window.Gate.settled() : null]).then(scheduleDashboardAnimations);
    // Phase X.4 - size the dashboard to the viewport now (the ResizeObserver
    // + the call at the end of renderDashboard handle later content changes).
    if (window.scheduleDashFit) window.scheduleDashFit();
    bindHeroLogoFlip();   // Phase X.11
    return painted;
}
window.showHub = showHub;

// Unsaved-changes guard for the PO editor, invoked by the unified Home button.
// Returns true if it's safe to leave (no unsaved data, or the user confirmed).
async function poConfirmLeave() {
    return window.unsavedGuard.confirmLeave("po");
}

// Phase M.3 - dashboard dynamic content. Greeting + datetime live in JS so
// they stay current. The "This Month" card is a sliding carousel of donut
// pages (by doc type / quotations by category / invoices by category).
// Everything is best-effort: silently skipped if any API call fails.
const DASH_TYPES = [
    { key: "quote", label: "Quotations",     color: "#2563EB" },
    { key: "inv",   label: "Invoices",        color: "#7C3AED" },
    { key: "dn",    label: "Delivery Notes",  color: "#0891B2" },
    { key: "po",    label: "Purchase Orders", color: "#059669" },
];
// Sub-type categories (shared by Quotations & Invoices). type_code on each
// record is MT/EQ/MP/SV.
const DASH_SUBTYPES = [
    { code: "MT", label: "Materials",  color: "#2563EB" },
    { code: "EQ", label: "Equipment",  color: "#0891B2" },
    { code: "MP", label: "Manpower",   color: "#7C3AED" },
    { code: "SV", label: "Service",    color: "#059669" },
];
const DASH_BADGE = { quote: "QUOTE", inv: "INVOICE", dn: "DN", po: "PO" };
const DASH_ICON = {
    quote: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
    po:    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
    dn:    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>',
    inv:   '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="13" y2="13"/></svg>',
};
window.DASH_ICON = DASH_ICON;   // reused by todo.js (To-Do view)

const DASH_DONUT_R = 46;
const DASH_DONUT_C = 2 * Math.PI * DASH_DONUT_R;

let _dashSliderTimer = null;
let _dashSlideIdx = 0;
let _dashSlideCount = 0;
let _dashSearchDocs = [];   // cached docs (this + last month) powering search
let _dashRenderSeq = 0;     // prevents an older dashboard load replacing a newer one

/* WHAT THE HUB IS SHOWING, so a return to it can keep it (2026-10-01).

   Waleed: going back to the hub meant ~2 seconds of loading every time. Two
   causes. The backend one (Needs Attention re-ran client setup 359 times) is
   fixed in backend/client_master.py. This is the frontend one: every return
   blanked all three sections to skeletons and rebuilt every chart from
   scratch, even when he had only opened a document and looked at it.

   `_dashShownToken` is the backend's `dashboard_state_token` for the data on
   screen. It moves on ANY database commit, a settings change or a new day -
   never on a timer - so "same token" really does mean "nothing to redraw".
   Null means nothing reliable is drawn (first visit, or a section failed) and
   the next visit must fetch.

   `_dashDrawn` holds a signature per section, so a refresh caused by an
   unrelated change (an item edited in Master Data moves the token too) does
   not replay the donut animation and count-ups on numbers that are
   identical. */
let _dashShownToken = null;
const _dashDrawn = { attention: null, bid: null, stats: null };

const DASH_BRIDGE_TIMEOUT_MS = 3500;

function _dashboardAttempt(factory, timeoutMs) {
    let timer = null;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Dashboard request timed out")), timeoutMs);
    });
    return Promise.race([Promise.resolve().then(factory), timeout])
        .finally(() => { if (timer) clearTimeout(timer); });
}

async function dashboardBridgeCall(label, factory) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            const value = await _dashboardAttempt(factory, DASH_BRIDGE_TIMEOUT_MS);
            if (value && value.ok === false) throw new Error(value.error || `${label} failed`);
            return { ok: true, value };
        } catch (error) {
            lastError = error;
            if (attempt === 0) await new Promise(resolve => setTimeout(resolve, 160));
        }
    }
    console.error(`[dashboard] ${label} failed`, lastError);
    return { ok: false, value: null, error: lastError };
}

function renderDashboardLoading() {
    _dashDrawn.attention = _dashDrawn.bid = _dashDrawn.stats = null;
    const focus = document.getElementById("dashFocus");
    const count = document.getElementById("dashFocusCount");
    if (count) count.hidden = true;
    if (focus) {
        focus.innerHTML = `<div class="dash-loading" role="status" aria-label="Loading To-Do items">`
            + `<span class="dash-skeleton-line is-wide"></span>`
            + `<span class="dash-skeleton-line"></span>`
            + `<span class="dash-skeleton-line is-mid"></span></div>`;
    }
    const bid = document.getElementById("dashBidDonut");
    const legend = document.getElementById("dashBidLegend");
    const track = document.getElementById("dashSliderTrack");
    const dots = document.getElementById("dashDots");
    if (bid) bid.innerHTML = `<span class="dash-skeleton-ring" aria-hidden="true"></span>`;
    if (legend) legend.innerHTML = `<div class="dash-loading is-compact" role="status" aria-label="Loading bid outcomes"><span class="dash-skeleton-line"></span><span class="dash-skeleton-line is-mid"></span><span class="dash-skeleton-line"></span></div>`;
    if (track) track.innerHTML = `<div class="dash-loading is-chart" role="status" aria-label="Loading Insights"><span class="dash-skeleton-line is-wide"></span><span class="dash-skeleton-block"></span></div>`;
    if (dots) dots.innerHTML = "";
}

function renderDashboardError(section) {
    // A section showing an error is not showing data; the next pass must draw
    // it even if the data turns out identical to what was there before.
    if (section === "attention" || section === "all") _dashDrawn.attention = null;
    if (section === "bid" || section === "all") _dashDrawn.bid = null;
    if (section === "stats" || section === "all") _dashDrawn.stats = null;
    _dashShownToken = null;
    const retry = `<button type="button" class="dash-load-retry" data-dash-retry>Retry</button>`;
    const message = `<div class="dash-load-error"><strong>Couldn’t load this section.</strong>${retry}</div>`;
    if (section === "attention" || section === "all") {
        const focus = document.getElementById("dashFocus");
        if (focus) focus.innerHTML = message;
    }
    if (section === "bid" || section === "all") {
        const bid = document.getElementById("dashBidDonut");
        const legend = document.getElementById("dashBidLegend");
        if (bid) bid.innerHTML = "";
        if (legend) legend.innerHTML = message;
    }
    if (section === "stats" || section === "all") {
        const track = document.getElementById("dashSliderTrack");
        const dots = document.getElementById("dashDots");
        if (track) track.innerHTML = message;
        if (dots) dots.innerHTML = "";
    }
    document.querySelectorAll("[data-dash-retry]").forEach(button => {
        button.onclick = () => renderDashboard({ force: true }).catch(error => {
            console.error("[dashboard] retry failed", error);
            renderDashboardError("all");
        });
    });
}

async function buildDashboardSearchIndex(seq, current, year, month) {
    const last = new Date(year, month - 2, 1);
    const yy = last.getFullYear(), mm = last.getMonth() + 1;
    const previous = await Promise.all([
        dashboardBridgeCall("previous quotations", () => api().list_quotes_for_month(yy, mm)),
        dashboardBridgeCall("previous invoices", () => api().list_invoices_for_month(yy, mm)),
        dashboardBridgeCall("previous delivery notes", () => api().list_dns_for_month(yy, mm)),
        dashboardBridgeCall("previous purchase orders", () => api().list_pos_for_month(yy, mm)),
    ]);
    if (seq !== _dashRenderSeq) return;
    const prev = previous.map(result => result.ok && Array.isArray(result.value) ? result.value : []);
    const all = [];
    const add = (sets) => {
        const [qq, ii, dd, pp] = sets;
        const cl = x => x.client_name || x.client || "";
        qq.forEach(x => all.push({ doc: "quote", no: x.quote_no, when: x.updated_at, path: x.path, client: cl(x) }));
        ii.forEach(x => all.push({ doc: "inv", no: x.inv_no, when: x.updated_at, path: x.path, client: cl(x) }));
        dd.forEach(x => all.push({ doc: "dn", no: x.dn_no, when: x.updated_at, path: x.path, client: cl(x) }));
        pp.forEach(x => all.push({ doc: "po", no: x.po_no, when: x.updated_at, path: x.path, client: cl(x) }));
    };
    add(current);
    add(prev);
    all.sort((a, b) => (b.when || "").localeCompare(a.when || ""));
    _dashSearchDocs = all;
    const search = document.getElementById("dashSearch");
    if (search) search.value = "";
    wireDashSearch();
}

/* ---------- Hero greeting ----------------------------------------------
   The hero line is picked ONCE PER APP START, not on every entry to the hub
   (Waleed's ruling, 2026-10-01): re-picking it meant the line changed every
   time he stepped into a document and came back, which read as a glitch.
   The pools stay large so the line differs between sessions.
   `{name}` marks where the gold accent name goes; a line without it just
   renders on its own. Keep every line SHORT - the hero is a fixed height
   with overflow:hidden, so a wrapped h1 would push the date row out of
   sight (the nowrap/ellipsis rule in app_styles.css is the safety net,
   not a licence for long lines).
   Taglines are deliberately statements, never questions, so they read
   correctly after a greeting that is itself a question. They also never
   claim anything about the DATA ("all caught up", "everything's in
   order") - nothing here has looked at the documents yet. */
const DASH_GREETINGS = {
    morning: [
        "Good morning, {name}",
        "Morning, {name}",
        "{name}, good morning",
        "A fresh start, {name}",
        "Bright and early, {name}",
        "The morning is yours, {name}",
        "Off to an early start, {name}",
        "Let's make the morning count, {name}",
        "First task of the day, {name}?",
        "Morning, {name} - the desk is clear",
        "Set for a strong morning, {name}",
        "Where shall we start, {name}?",
        "Ready for the morning, {name}?",
        "New day, {name}",
    ],
    afternoon: [
        "Good afternoon, {name}",
        "Afternoon, {name}",
        "{name}, good afternoon",
        "Halfway there, {name}",
        "Plenty of day left, {name}",
        "Back at it, {name}",
        "Afternoon, {name} - let's keep the pace",
        "Second half of the day, {name}",
        "What's next, {name}?",
        "Making good time, {name}",
        "Afternoon momentum, {name}",
        "Let's pick up the thread, {name}",
        "Good afternoon - the desk is yours, {name}",
        "Ready for the next one, {name}?",
    ],
    evening: [
        "Good evening, {name}",
        "Evening, {name}",
        "{name}, good evening",
        "Winding down, {name}?",
        "One more before the day closes, {name}?",
        "Evening, {name} - let's finish strong",
        "Closing out the day, {name}",
        "Last of the paperwork, {name}?",
        "The evening shift, {name}",
        "Quiet hours, {name}",
        "Good evening - take your time, {name}",
        "Wrapping up, {name}?",
        "What's left on the desk, {name}?",
        "Still going, {name}? Let's be quick",
    ],
    night: [
        "Working late, {name}?",
        "Burning the midnight oil, {name}?",
        "Late night, {name}",
        "The office is quiet, {name}",
        "Still at it, {name}?",
        "Good evening, {name} - or is it morning?",
        "Night shift, {name}",
        "Late one tonight, {name}",
        "Don't stay up too late, {name}",
        "Let's keep it short tonight, {name}",
    ],
    any: [
        "Welcome back, {name}",
        "Good to see you, {name}",
        "Hello again, {name}",
        "Ready when you are, {name}",
        "Let's get to work, {name}",
        "What are we creating today, {name}?",
        "Where would you like to start, {name}?",
        "At your service, {name}",
        "Back in the hub, {name}",
        "The floor is yours, {name}",
        "Let's begin, {name}",
        "Standing by, {name}",
        "All set, {name}?",
        "Good to have you back, {name}",
        "Let's make something official, {name}",
        "Your workspace is ready, {name}",
        "What's on the agenda, {name}?",
        "Pick up where you left off, {name}",
        "Say the word, {name}",
        "Here to help, {name}",
        "Let's put it in writing, {name}",
        "What can we get done, {name}?",
    ],
};

const DASH_TAGLINES = [
    "Here is what's happening with your documents today.",
    "Quotations, orders, delivery notes and invoices - all in one place.",
    "Everything you need to get the paperwork out the door.",
    "Your documents, your clients, your numbers.",
    "Create, track and close from a single dashboard.",
    "A quick look at the month so far.",
    "The latest across every document type.",
    "From first quotation to final invoice.",
    "Your paperwork, organised and ready.",
    "Let's turn today's work into documents.",
    "Every document, one click away.",
    "Start something new, or pick up where you left off.",
    "The month at a glance.",
    "Built to keep the paperwork moving.",
    "Documents, costing and clients - all in reach.",
    "A clear view of the work in progress.",
    "Filed, numbered and exactly where you left it.",
    "Ready to draft, review or send.",
    "Your business paperwork, end to end.",
    "One place for every document you issue.",
    "Take a look at what needs your attention.",
    "The numbers behind this month's work.",
    "Draft it, price it, send it.",
    "Keeping the record straight, one document at a time.",
    "Your workspace is set up and waiting.",
    "Let's keep the pipeline moving.",
    "Everything from quotation to payment.",
    "Track the work, not the paperwork.",
    "Here's the shape of the month so far.",
    "New documents start here.",
    "Costs, profit and clients - a step away.",
    "All your documents, neatly numbered.",
    "The day's work, ready when you are.",
    "Pick a document type and get started.",
    "Order, delivery, invoice - in that order.",
    "A tidy desk for a busy business.",
    "Whatever needs issuing today, it starts here.",
    "The full picture of your documents.",
    "Every client, every document, one hub.",
    "Let's get the day's documents out.",
];

// Last few picks, so a fresh pick never repeats the line you just read.
const _dashRecentLines = { greeting: [], tagline: [] };
let _dashUserName = null;
// The one pair chosen for this run of the app. Null until the hub is first
// rendered; set once and reused, which is what keeps the line steady while
// Waleed moves between the hub and a document.
let _dashHeroPick = null;

function dashGreetingBucket(hour) {
    if (hour < 5) return "night";
    if (hour < 12) return "morning";
    if (hour < 17) return "afternoon";
    if (hour < 22) return "evening";
    return "night";
}

function pickFreshLine(pool, memory, keep) {
    if (!pool.length) return "";
    const fresh = pool.filter(line => !memory.includes(line));
    const source = fresh.length ? fresh : pool;
    const choice = source[Math.floor(Math.random() * source.length)];
    memory.push(choice);
    while (memory.length > keep) memory.shift();
    return choice;
}

// One greeting + tagline for the time of day. Called once per app start.
function pickHeroPair(now) {
    const bucket = DASH_GREETINGS[dashGreetingBucket(now.getHours())] || [];
    return {
        greeting: pickFreshLine(bucket.concat(DASH_GREETINGS.any), _dashRecentLines.greeting, 6),
        tagline: pickFreshLine(DASH_TAGLINES, _dashRecentLines.tagline, 12),
    };
}

function renderHeroGreeting(now) {
    const headEl = document.getElementById("dashGreeting");
    const nameEl = document.getElementById("dashUser");
    const tailEl = document.getElementById("dashGreetingTail");
    // The name lives in index.html; read it once, before we overwrite it.
    if (_dashUserName == null) _dashUserName = (nameEl ? nameEl.textContent : "").trim() || "Waleed";

    // Pick on the FIRST render only. Every later entry to the hub re-renders
    // the same line, so leaving a document and coming back looks like coming
    // back, not like a new session.
    if (!_dashHeroPick) _dashHeroPick = pickHeroPair(now);
    const line = _dashHeroPick.greeting;
    const at = line.indexOf("{name}");
    if (headEl) headEl.textContent = at < 0 ? line : line.slice(0, at);
    if (nameEl) nameEl.textContent = at < 0 ? "" : _dashUserName;
    if (tailEl) tailEl.textContent = at < 0 ? "" : line.slice(at + "{name}".length);

    const tagEl = document.getElementById("dashTagline");
    if (tagEl) tagEl.textContent = _dashHeroPick.tagline;
}

async function renderDashboard(opts = {}) {
    const seq = ++_dashRenderSeq;

    // Greeting + tagline: one pair per app start, re-rendered unchanged here.
    const now = new Date();
    renderHeroGreeting(now);
    const dtEl = document.getElementById("dashDatetime");
    if (dtEl) {
        const fmtD = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
        const fmtT = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        dtEl.textContent = `${fmtD}  ·  ${fmtT}`;
    }
    const periodEl = document.getElementById("dashStatsPeriod");
    if (periodEl) periodEl.textContent = now.toLocaleDateString(undefined, { month: "short", year: "numeric" });

    // Did anything change since the hub was last drawn? One call, ~0.2 ms in
    // the backend. Same token -> what is on screen is already current; keep it
    // and do nothing else. A failed or unknown token is never "the same".
    let token = null;
    try {
        const t = await api().dashboard_state_token();
        token = t && t.ok && t.token ? String(t.token) : null;
    } catch (e) { token = null; }
    if (seq !== _dashRenderSeq) return;
    if (!opts.force && token && token === _dashShownToken) {
        forceInsightsPaint();
        scheduleInsightsFit();
        if (window.scheduleDashFit) window.scheduleDashFit();
        return;
    }

    // Something changed (or nothing is drawn yet). Fetch. Skeletons ONLY when
    // the hub is empty - over existing content the new data swaps in place, so
    // coming back never flashes blank.
    if (_dashSliderTimer) { clearInterval(_dashSliderTimer); _dashSliderTimer = null; }
    if (_dashShownToken == null) renderDashboardLoading();
    let complete = true;      // every section drawn from fresh data

    const y = now.getFullYear();
    const m = now.getMonth() + 1;

    // All visible dashboard data starts together. Needs Attention renders as
    // soon as its own call completes and never waits for Insights.
    // No month argument on purpose: Needs Attention is EVERYTHING outstanding,
    // not this month's slice. Scoping it hid a June PO from an August dashboard.
    const attentionTask = dashboardBridgeCall(
        "Needs Attention", () => api().dashboard_needs_attention())
        .then(result => {
            if (seq !== _dashRenderSeq) return;
            if (!result.ok) { complete = false; renderDashboardError("attention"); return; }
            const sig = JSON.stringify(result.value || {});
            if (sig === _dashDrawn.attention) return;        // identical - leave it
            renderNeedsAttention(result.value || {});
            _dashDrawn.attention = sig;
        });
    const listTask = Promise.all([
        dashboardBridgeCall("quotations", () => api().list_quotes_for_month(y, m)),
        dashboardBridgeCall("invoices", () => api().list_invoices_for_month(y, m)),
        dashboardBridgeCall("delivery notes", () => api().list_dns_for_month(y, m)),
        dashboardBridgeCall("purchase orders", () => api().list_pos_for_month(y, m)),
    ]);
    const analyticsTask = dashboardBridgeCall("analytics", () => api().dashboard_analytics(3));
    const costingTask = dashboardBridgeCall(
        "costing summary", () => api().costing_summary(y, m));
    const [listResults, analyticsResult, costingResult] = await Promise.all([
        listTask, analyticsTask, costingTask,
    ]);
    if (seq !== _dashRenderSeq) return;
    if (!listResults.every(r => r.ok) || !analyticsResult.ok || !costingResult.ok) complete = false;
    const lists = listResults.map(result =>
        result.ok && Array.isArray(result.value) ? result.value : []);
    let [q, i, d, p] = lists;

    // ---- Build the 3 slider pages ----
    const byType = [
        { label: "Quotations",     color: "#2563EB", value: q.length },
        { label: "Invoices",       color: "#7C3AED", value: i.length },
        { label: "Delivery Notes", color: "#0891B2", value: d.length },
        { label: "Purchase Orders",color: "#059669", value: p.length },
    ];
    const countByCode = (arr) => {
        const out = {};
        arr.forEach(x => { const c = (x.type_code || "").toUpperCase(); out[c] = (out[c] || 0) + 1; });
        return out;
    };
    const qByCode = countByCode(q);
    const iByCode = countByCode(i);
    const quoteCats = DASH_SUBTYPES.map(s => ({ label: s.label, color: s.color, value: qByCode[s.code] || 0 }));
    const invCats   = DASH_SUBTYPES.map(s => ({ label: s.label, color: s.color, value: iByCode[s.code] || 0 }));

    // Revenue this month, split by client and by category (value-weighted).
    const CLIENT_PALETTE = ["#2563EB", "#7C3AED", "#0891B2", "#059669", "#DB2777", "#F59E0B"];
    const revByClient = {};
    const revByCat = {};
    i.forEach(x => {
        const amt = Number(x && x.grand_total) || 0;
        if (!amt) return;
        const cn = (x.client_name || "-").trim() || "-";
        revByClient[cn] = (revByClient[cn] || 0) + amt;
        const ct = (x.inv_type || "").toLowerCase();
        revByCat[ct] = (revByCat[ct] || 0) + amt;
    });
    const clientRevSegs = Object.entries(revByClient)
        .sort((a, b) => b[1] - a[1]).slice(0, 6)
        .map(([name, val], idx) => ({ label: name, color: CLIENT_PALETTE[idx % CLIENT_PALETTE.length], value: Math.round(val * 100) / 100 }));
    const CAT_MAP = { materials: ["Materials", "#2563EB"], equipment: ["Equipment", "#0891B2"], manpower: ["Manpower", "#7C3AED"], service: ["Service", "#059669"] };
    const catRevSegs = Object.keys(CAT_MAP)
        .map(k => ({ label: CAT_MAP[k][0], color: CAT_MAP[k][1], value: Math.round((revByCat[k] || 0) * 100) / 100 }));

    // Daily activity sparkline for the current month (from the analytics endpoint).
    let dailyPoints = [], dailyCaption = "";
    try {
        const an = analyticsResult.ok ? analyticsResult.value : null;
        if (an && an.daily) {
            const yy = now.getFullYear(), mm = now.getMonth() + 1, today = now.getDate();
            let tot = 0;
            for (let dd = 1; dd <= today; dd++) {
                const k = `${yy}-${String(mm).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
                const v = an.daily[k] || 0; tot += v;
                dailyPoints.push({ label: String(dd), value: v });
            }
            dailyCaption = `${tot} document${tot === 1 ? "" : "s"} so far this month`;
        }
    } catch (e) { /* sparkline optional */ }

    // Phase U - profit + bid snapshot for this month (from the costing summary),
    // so the glance card reflects the new Costing & Profit data, not just volume.
    let bidSegs = [], bidWinRate = 0, profitTypeRows = [];
    try {
        const cs = costingResult.ok ? costingResult.value : null;
        if (cs && cs.ok) {
            const b = cs.bids || {};
            bidWinRate = b.win_rate || 0;
            bidSegs = [
                { label: "Won",  color: "#16A34A", value: b.won  || 0 },
                { label: "Open", color: "#2563EB", value: b.open || 0 },
                { label: "Lost", color: "#DC2626", value: b.lost || 0 },
            ];
            const TLAB = { materials: ["Materials", "#2563EB"], equipment: ["Equipment", "#0891B2"],
                           manpower: ["Manpower", "#7C3AED"], service: ["Service", "#059669"] };
            profitTypeRows = (cs.by_type || [])
                .map(x => ({
                    label: (TLAB[x.type] && TLAB[x.type][0]) || x.type,
                    color: (TLAB[x.type] && TLAB[x.type][1]) || "#2563EB",
                    value: Math.round((((x.realized && x.realized.profit) || 0)) * 100) / 100,
                }))
                .filter(r => r.value > 0)
                .sort((a, b) => b.value - a.value);
        }
    } catch (e) { /* profit snapshot optional */ }

    if (costingResult.ok) {
        const sig = JSON.stringify([bidSegs, bidWinRate]);
        if (sig !== _dashDrawn.bid) {
            try { renderBidDonut(bidSegs, bidWinRate); _dashDrawn.bid = sig; }
            catch (error) {
                console.error("[dashboard] bid chart failed", error);
                complete = false;
                renderDashboardError("bid");
            }
        }
    } else {
        renderDashboardError("bid");
    }

    // A diverse mix of shapes - not just donuts.
    const pages = [
        { title: "By Document Type",    centerLbl: "Docs", segments: byType },                 // donut
        { title: "Revenue by Category", type: "vbars", money: true, bars: catRevSegs },        // vertical bars
        { title: "Revenue by Client",   type: "hbars", money: true, rows: clientRevSegs },     // horizontal bars
    ];
    if (profitTypeRows.length) {
        pages.push({ title: "Profit by Type", type: "hbars", money: true, rows: profitTypeRows });
    }
    // Bid Status is now the dedicated, animated Insights donut (Phase X.19) - not a slider page.
    if (dailyPoints.length) {
        pages.push({ title: "Daily Activity", type: "area", points: dailyPoints, caption: dailyCaption });
    }
    if (listResults.some(result => result.ok)) {
        const sig = JSON.stringify(pages);
        if (sig !== _dashDrawn.stats) {
            try { renderStatsSlider(pages); _dashDrawn.stats = sig; }
            catch (error) {
                console.error("[dashboard] stats chart failed", error);
                complete = false;
                renderDashboardError("stats");
            }
        } else {
            // Same pages: keep the slide he was on, just resume rotating.
            restartAutoAdvance(pages);
        }
    } else {
        renderDashboardError("stats");
    }

    // WebView2 can leave the freshly-rendered Insights charts (ApexCharts SVG +
    // the skeleton→content swap) uncomposited until something invalidates the
    // region - the "loading circle that only clears when you hover the donut"
    // bug. Force a repaint of the Insights column so it shows on load.
    forceInsightsPaint();
    // Hide the "By Document Type" slider card when the column is too short to
    // show it at full size (small screens); it returns on a taller monitor.
    scheduleInsightsFit();

    // Search indexing is non-visible startup work. Reuse this month's lists and
    // fetch only the previous month after the visible dashboard has rendered.
    void buildDashboardSearchIndex(seq, [q, i, d, p], y, m).catch(error => {
        console.error("[dashboard] search index failed", error);
    });
    await attentionTask;
    // Remember what is drawn ONLY if every section came from this fetch. The
    // token was read BEFORE fetching, so a write that landed mid-fetch makes
    // the next visit's token differ and refetch - it can cost one extra fetch,
    // it can never hide a change.
    if (seq === _dashRenderSeq) _dashShownToken = complete && token ? token : null;
    // Phase X.4 - re-fit once the dynamic content (recent cards, slides) is in.
    if (window.scheduleDashFit) window.scheduleDashFit();
}
window.renderDashboard = renderDashboard;

// ----------------------------------------------------------------
// Phase X.4 - Fit-to-screen dashboard.
// The dashboard is laid out at its natural design size; this scales
// the Needs-attention card GRID to whatever height the flex layout leaves it:
// it shows as many cards as fit and reveals a "View all" for the overflow. The
// dashboard itself fills the viewport purely via CSS flex (no transform), so
// this only governs how many attention cards are visible.
// ----------------------------------------------------------------
let _dashFitRaf = 0;
function fitFocusGrid() {
    const view = document.getElementById("hubView");
    if (!view || view.hidden) return;
    const focus = document.getElementById("dashFocus");
    const grid = focus && focus.querySelector(".dash-focus-grid");
    if (!grid) return;
    const va = document.getElementById("dashFocusViewAll");
    if (grid.classList.contains("is-grouped")) { fitGroupedGrid(grid, va); return; }
    const cards = Array.from(grid.children);
    if (!cards.length) return;
    // Show everything, then measure each card's bottom relative to the grid.
    cards.forEach(c => { c.style.display = ""; });
    if (va) va.style.display = "none";
    const avail = grid.clientHeight;
    if (!avail) return;
    const top = grid.getBoundingClientRect().top;
    const bottoms = cards.map(c => c.getBoundingClientRect().bottom - top);
    const fits = (limit) => {
        let n = cards.length;
        for (let i = 0; i < cards.length; i++) {
            if (bottoms[i] > limit + 1) { n = i; break; }
        }
        return n;
    };
    // Always reserve room for the "View all" row and keep it visible whenever
    // there are items, so the dedicated To-Do view is reachable even when every
    // card already fits (Phase X.26).
    const show = Math.max(1, fits(avail - 46));
    cards.forEach((c, i) => { c.style.display = i < show ? "" : "none"; });
    if (va) {
        const any = cards.length > 0;
        va.hidden = !any;
        va.style.display = any ? "" : "none";
    }
}
// Needs attention grouped by type (2026-10-21): cutting the grid top-down would
// hide whole types behind a long first one, so the room is shared out in rows.
// Pass 1 gives every type its heading and ONE row, most urgent first; pass 2
// hands the rows left over to the most urgent types. A heading whose cards
// were cut says "+N more" (N counts the full list, not just what was drawn).
function fitGroupedGrid(grid, va) {
    const kids = Array.from(grid.children);
    kids.forEach(c => { c.style.display = ""; });
    if (va) va.style.display = "none";
    const avail = grid.clientHeight - 46;       // room kept for "View all"
    if (avail <= 0) return;
    const cs = getComputedStyle(grid);
    const cols = cs.gridTemplateColumns.split(" ").filter(Boolean).length || 1;
    const gap = parseFloat(cs.rowGap) || 0;
    const groups = [];
    kids.forEach(el => {
        if (el.classList.contains("dash-na-group")) groups.push({ head: el, cards: [] });
        else if (groups.length) groups[groups.length - 1].cards.push(el);
    });
    const cards = groups.flatMap(g => g.cards);
    const rowCost = Math.max(0, ...cards.map(c => c.offsetHeight)) + gap;
    const budget = avail + gap;                 // n rows have n-1 gaps
    const alloc = groups.map(() => 0);
    let used = 0;
    for (let i = 0; i < groups.length; i++) {
        const cost = groups[i].head.offsetHeight + gap + rowCost;
        if (used + cost > budget) break;
        alloc[i] = 1; used += cost;
    }
    if (groups.length && !alloc[0]) alloc[0] = 1;   // always show something
    groups.forEach((g, i) => {
        const rows = Math.ceil(g.cards.length / cols);
        while (alloc[i] && alloc[i] < rows && used + rowCost <= budget) { alloc[i]++; used += rowCost; }
    });
    groups.forEach((g, i) => {
        const n = Math.min(g.cards.length, alloc[i] * cols);
        g.head.style.display = alloc[i] ? "" : "none";
        g.cards.forEach((c, j) => { c.style.display = j < n ? "" : "none"; });
    });
    // The sums above are an estimate; trim from the end until nothing spills.
    const limit = grid.getBoundingClientRect().top + avail + 1;
    for (let k = kids.length - 1; k > 0; k--) {
        const last = kids[k];
        if (last.style.display === "none") continue;
        if (last.getBoundingClientRect().bottom <= limit) break;
        last.style.display = "none";
    }
    groups.forEach(g => {
        const shown = g.cards.filter(c => c.style.display !== "none").length;
        if (!shown) g.head.style.display = "none";  // no heading left alone
        const more = g.head.querySelector(".dash-na-group-more");
        const left = (Number(g.head.dataset.total) || g.cards.length) - shown;
        if (more) { more.textContent = `+${left} more`; more.hidden = left <= 0; }
    });
    if (va) { va.hidden = false; va.style.display = ""; }
}
function scheduleDashFit() {
    if (_dashFitRaf) cancelAnimationFrame(_dashFitRaf);
    _dashFitRaf = requestAnimationFrame(() => { _dashFitRaf = 0; fitFocusGrid(); });
}
window.fitFocusGrid = fitFocusGrid;
window.scheduleDashFit = scheduleDashFit;

// Recompute when the window is resized or moved to another screen.
window.addEventListener("resize", scheduleDashFit);

// Search box (Phase M.6) - filters the cached document set by number or
// client, live, into the Recent Documents grid. Empty query restores the
// 6 most-recent. Enter opens the first match.
function wireDashSearch() {
    const inp = document.getElementById("dashSearch");
    if (!inp) return;
    let t = null;
    inp.oninput = () => {
        if (t) clearTimeout(t);
        t = setTimeout(() => applyDashSearch(inp.value), 160);
    };
    inp.onkeydown = (e) => {
        if (e.key === "Enter") {
            if (_dashLastSearchMatches.length) openSearchDoc(_dashLastSearchMatches[0]);
        } else if (e.key === "Escape") {
            inp.value = ""; applyDashSearch("");
        }
    };
}

// Local metadata-only fallback (number/client) used if the bridge search fails.
function _localSearchDocs(q) {
    const ql = q.toLowerCase();
    return _dashSearchDocs.filter(x =>
        (x.no || "").toLowerCase().includes(ql) ||
        (x.client || "").toLowerCase().includes(ql)
    ).slice(0, 12);
}

let _dashSearchSeq = 0;   // guards against out-of-order async responses
// Searches document NUMBER, CLIENT, and ITEM CONTENT (via the backend, which
// reads each doc's stored rows). Empty query restores the Needs-Attention strip.
async function applyDashSearch(query) {
    const q = (query || "").trim();
    if (!q) {
        renderNeedsAttention(_dashLastNeedsAttention || { decide: [], uncosted: [], unsubmitted: [] });
        return;
    }
    const seq = ++_dashSearchSeq;
    let matches;
    try {
        matches = await api().search_documents(q, 24);
    } catch {
        matches = null;
    }
    if (seq !== _dashSearchSeq) return;            // a newer keystroke superseded this
    renderSearchResults(matches || _localSearchDocs(q), query);
}

// Open a search-result doc in its editor (fallback: open the PDF).
async function openSearchDoc(r) {
    if (!r) return;
    const fn = window[EDIT_LOADER[r.doc]];
    if (fn && r.no) {
        try {
            await fn(r.no);
            if (window.setEditorReturn) window.setEditorReturn({ run: () => showHub() });
            return;
        } catch { /* fall through to opening the file */ }
    }
    if (r.path) api().open_path(r.path);
}

// Build one donut's inner SVG + legend HTML for a page's segments.
// Segments start collapsed (dasharray "0 C") with the real target stashed in
// data-dash, so activateSlide() can "draw" them in with a CSS transition.
// Compact money for the donut center/legend ($12.4k, $980, $1.2M).
function dashMoneyShort(n) {
    const v = Number(n) || 0;
    if (Math.abs(v) >= 1e6) return "$" + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (Math.abs(v) >= 1e3) return "$" + (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return "$" + Math.round(v);
}

function buildDonutHTML(page) {
    const total = page.segments.reduce((s, x) => s + (x.value || 0), 0);
    const fmtVal = page.money ? dashMoneyShort : (v) => (v || 0);
    const C = DASH_DONUT_C, R = DASH_DONUT_R;
    let acc = 0, di = 0;
    const segs = page.segments.map(seg => {
        const v = seg.value || 0;
        if (!v || !total) return "";
        const segLen = (v / total) * C;
        const offset = -acc;
        acc += segLen;
        const target = `${segLen.toFixed(2)} ${(C - segLen).toFixed(2)}`;
        const delay = 80 * (di++);
        // Build at the TARGET dasharray so the ring is visible by default -
        // the draw-in (activateSlide) is a progressive enhancement that
        // resets to 0 then animates back. If JS never runs, the ring still shows.
        return `<circle class="seg" cx="58" cy="58" r="${R}" fill="none"
                        stroke="${seg.color}" stroke-width="12" stroke-linecap="butt"
                        stroke-dashoffset="${offset.toFixed(2)}"
                        stroke-dasharray="${target}"
                        data-dash="${target}"
                        style="transition-delay:${delay}ms"/>`;
    }).join("");

    const legend = page.segments.map(seg => `
        <div class="dash-legend-item">
            <span class="dash-legend-dot" style="--c:${seg.color}"></span>
            <span class="dash-legend-name">${esc(seg.label)}</span>
            <span class="dash-legend-val">${fmtVal(seg.value || 0)}</span>
        </div>`).join("");

    return `
        <div class="dash-donut-wrap">
            <div class="dash-donut">
                <svg viewBox="0 0 116 116">
                    <circle cx="58" cy="58" r="${R}" fill="none" stroke="#EEF1F6" stroke-width="12"/>
                    ${segs}
                </svg>
                <div class="dash-donut-center">
                    ${page.money
                        ? `<div class="num num-money">${dashMoneyShort(total)}</div>`
                        : `<div class="num" data-target="${total}">${total}</div>`}
                    <div class="lbl">${esc(page.centerLbl)}</div>
                </div>
            </div>
            <div class="dash-legend">${legend}</div>
        </div>`;
}

// Render the slider: build all slides, wire nav, activate page 0.
// Dispatch a slide to the right chart shape so the card isn't all donuts.
function buildSlideHTML(pg) {
    if (pg.type === "vbars") return buildMiniVBarsHTML(pg);
    if (pg.type === "hbars") return buildMiniHBarsHTML(pg);
    if (pg.type === "area")  return buildMiniAreaHTML(pg);
    return buildDonutHTML(pg);
}

// Mini vertical bars (e.g. revenue by category).
function buildMiniVBarsHTML(pg) {
    const bars = pg.bars || [];
    const fmt = pg.money ? dashMoneyShort : (v) => String(v || 0);
    const max = Math.max(1, ...bars.map(b => b.value || 0));
    const hasAny = bars.some(b => (b.value || 0) > 0);
    if (!hasAny) return `<div class="dash-mini dash-mini-empty">No data this month</div>`;
    const cols = bars.map(b => {
        const h = Math.round(((b.value || 0) / max) * 100);
        return `<div class="dash-vb-col" title="${esc(b.label)}: ${fmt(b.value || 0)}">
            <div class="dash-vb-wrap">
                <span class="dash-vb-val">${(b.value || 0) > 0 ? fmt(b.value) : ""}</span>
                <span class="dash-vb-bar" style="height:${h}%;--c:${b.color || "#2563EB"}"></span>
            </div>
            <span class="dash-vb-label">${esc(b.label)}</span>
        </div>`;
    }).join("");
    return `<div class="dash-mini dash-mini-vbars">${cols}</div>`;
}

// Mini horizontal bars (e.g. revenue by client).
function buildMiniHBarsHTML(pg) {
    const rows = pg.rows || [];
    const fmt = pg.money ? dashMoneyShort : (v) => String(v || 0);
    if (!rows.length) return `<div class="dash-mini dash-mini-empty">No data this month</div>`;
    const max = Math.max(1, ...rows.map(r => r.value || 0));
    return `<div class="dash-mini dash-mini-hbars">${rows.map(r => {
        const w = Math.max(3, Math.round(((r.value || 0) / max) * 100));
        return `<div class="dash-hb-row" title="${esc(r.label)}: ${fmt(r.value || 0)}">
            <span class="dash-hb-label">${esc(r.label)}</span>
            <span class="dash-hb-track"><span class="dash-hb-fill" style="width:${w}%"></span></span>
            <span class="dash-hb-val">${fmt(r.value || 0)}</span>
        </div>`;
    }).join("")}</div>`;
}

// Mini gradient sparkline area (e.g. daily activity this month).
function buildMiniAreaHTML(pg) {
    const pts = pg.points || [];
    const vals = pts.map(p => p.value || 0);
    if (!vals.some(v => v > 0)) return `<div class="dash-mini dash-mini-empty">No activity yet this month</div>`;
    const max = Math.max(1, ...vals);
    const W = 300, H = 92, padB = 4, padT = 8;
    const n = pts.length || 1;
    const stepX = n > 1 ? W / (n - 1) : 0;
    const yAt = (v) => (H - padB) - (v / max) * (H - padT - padB);
    let line = "";
    pts.forEach((p, i) => { const x = i * stepX, y = yAt(p.value || 0); line += (i === 0 ? `M ${x.toFixed(1)} ${y.toFixed(1)}` : ` L ${x.toFixed(1)} ${y.toFixed(1)}`); });
    const area = line + ` L ${((n - 1) * stepX).toFixed(1)} ${H - padB} L 0 ${H - padB} Z`;
    return `<div class="dash-mini dash-mini-area">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="dash-spark">
            <defs><linearGradient id="dashSparkGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#2563EB" stop-opacity="0.26"/>
                <stop offset="100%" stop-color="#2563EB" stop-opacity="0.02"/>
            </linearGradient></defs>
            <path d="${area}" fill="url(#dashSparkGrad)"/>
            <path d="${line}" class="dash-spark-line"/>
        </svg>
        <div class="dash-mini-cap">${esc(pg.caption || "")}</div>
    </div>`;
}

// Phase X.19 - dashboard "Insights" donut: bid outcomes (Won / Open / Lost).
// The ring and legend share one interaction state: hover previews an outcome,
// click pins it, and clicking the same outcome clears the pin.
let _bidDonut = null, _bidDonutData = null;
// WebView2/Chromium-in-webview sometimes leaves a freshly-injected chart SVG
// (or an innerHTML swap) uncomposited until an event invalidates the region -
// the user sees the loading skeleton until they hover the donut. Nudge a repaint
// of the Insights column so it appears on load without interaction. Cheap and
// visually a no-op; runs once now and once after the entrance animation settles.
function forceInsightsPaint() {
    const side = document.querySelector(".dash-mid-side");
    if (!side) return;
    const nudge = () => {
        side.style.opacity = "0.9999";       // invalidate compositing (no visible change)
        void side.offsetHeight;              // force reflow
        requestAnimationFrame(() => {
            side.style.opacity = "";
            side.style.transform = "translateZ(0)";   // bump a GPU layer to recomposite
            void side.offsetHeight;
            requestAnimationFrame(() => { side.style.transform = ""; });
        });
    };
    requestAnimationFrame(nudge);
    setTimeout(nudge, 400);
}
window.forceInsightsPaint = forceInsightsPaint;

// Keep the "By Document Type" slider card usable when the Insights column is
// short. The card is always shown; its slide bodies scale via CSS caps, and
// here we trim the card's chrome (padding / header / dots / button) so the
// chart keeps a usable share of the height instead of being squeezed to
// nothing. Measured off the card's own height, so it adapts to the real
// available space at any window size / DPI - no screen-size guess.
// Below this card height the "By Document Type" chrome (padding / header / dots
// / button) eats too much of the card, leaving no usable chart area - so trim it.
const INSIGHTS_TIGHT_PX = 240;
function fitInsightsCard() {
    const card = document.querySelector(".dash-mid-side .dash-stats-card");
    if (!card) return;
    // The card is `flex: 1 1 auto`, so its height comes from the column, not from
    // its own content - toggling the class can't change it, so this can't loop.
    const h = card.getBoundingClientRect().height;
    if (!h) return;                                     // not laid out yet
    card.classList.toggle("stats-tight", h < INSIGHTS_TIGHT_PX);
}
window.fitInsightsCard = fitInsightsCard;
// Re-measure after layout settles - the Bid-outcomes chart renders async, so the
// first pass can read a pre-layout height.
function scheduleInsightsFit() {
    requestAnimationFrame(fitInsightsCard);
    setTimeout(fitInsightsCard, 300);
    setTimeout(fitInsightsCard, 900);
}
window.scheduleInsightsFit = scheduleInsightsFit;
window.addEventListener("resize", () => requestAnimationFrame(fitInsightsCard));

// The donut's height comes from CSS (--dash-donut-h on #dashBidDonut), so the
// short-screen step in app_styles.css can give its room to "By Document Type"
// (2026-10-22). ApexCharts draws at a fixed size, so it is re-drawn - from the
// data it already has - once a resize has SETTLED and only if the height or
// its column's width really changed. Not on the media-query change itself:
// that fires before the column has its new width (measured: a 1920x1017 ->
// 1536x801 move drew it for the old width, 149px instead of 115px).
let _bidDonutDrawn = null;           // { h, w } the current donut was drawn at
function _bidDonutDrawnAt(host) {
    const h = donutHeight(host);
    _bidDonutDrawn = { h, w: Math.round(host.getBoundingClientRect().width) };
    return h;
}
let _bidDonutResizeT = 0;
function donutHeight(host) {
    const v = parseFloat(getComputedStyle(host).getPropertyValue("--dash-donut-h"));
    return Number.isFinite(v) && v > 0 ? v : 188;
}
function scheduleDonutRefit() {
    clearTimeout(_bidDonutResizeT);
    _bidDonutResizeT = setTimeout(() => {
        const host = document.getElementById("dashBidDonut");
        if (!host || !_bidDonutData || !_bidDonutDrawn) return;
        const h = donutHeight(host), w = Math.round(host.getBoundingClientRect().width);
        if (h === _bidDonutDrawn.h && w === _bidDonutDrawn.w) return;
        try { renderBidDonut(_bidDonutData.segs, _bidDonutData.winRate); } catch (e) { /* noop */ }
        scheduleInsightsFit();
    }, 180);
}
window.addEventListener("resize", scheduleDonutRefit);
// The step's own media query too: a viewport can change without a `resize`
// event reaching the page (seen in an emulated viewport), and the settle
// delay above still lets the column take its new width first.
if (window.matchMedia) {
    const mq = window.matchMedia("(max-height: 960px)");
    if (mq.addEventListener) mq.addEventListener("change", scheduleDonutRefit);
    else if (mq.addListener) mq.addListener(scheduleDonutRefit);
}

function renderBidDonut(segs, winRate) {
    const host = document.getElementById("dashBidDonut");
    const legend = document.getElementById("dashBidLegend");
    if (!host || !window.ApexCharts) return;
    _bidDonutData = { segs: segs, winRate: winRate };
    if (_bidDonut) { try { _bidDonut.destroy(); } catch (e) { /* noop */ } _bidDonut = null; }
    let nextChartIndex = 0;
    const items = (segs || []).map(s => {
        const value = Math.max(0, Number(s.value) || 0);
        const item = {
            label: s.label || "",
            color: s.color || "#2563EB",
            value,
            chartIndex: value > 0 ? nextChartIndex++ : null,
        };
        return item;
    });
    const data = items.filter(s => s.value > 0);
    const total = data.reduce((a, s) => a + s.value, 0);
    if (legend) {
        legend.innerHTML = items.map(s => {
            const pc = total ? Math.round(s.value / total * 100) : 0;
            const disabled = s.chartIndex == null;
            const title = disabled
                ? `No ${s.label} bids this month`
                : `${s.label}: ${s.value} bid${s.value === 1 ? "" : "s"} (${pc}%)`;
            return `<button type="button" class="dash-bid-option${disabled ? " is-disabled" : ""}" style="--bid-color:${s.color}"` +
                   `${disabled ? " disabled aria-disabled=\"true\"" : ` data-bid-index="${s.chartIndex}"`}` +
                   ` aria-pressed="false" title="${esc(title)}">` +
                   `<span class="dot"></span>` +
                   `<span class="name">${esc(s.label)}</span>` +
                   `<span class="v">${s.value}</span><span class="pc">${pc}%</span></button>`;
        }).join("");
    }
    const buttons = legend ? Array.from(legend.querySelectorAll(".dash-bid-option")) : [];
    host.classList.remove("donut-hovering", "donut-pinned");
    if (!total) { host.innerHTML = ""; return; }
    const css = getComputedStyle(document.documentElement);
    const tok = (n, f) => (css.getPropertyValue(n).trim() || f);
    const INK = tok("--text", "#0F172A"), MUT = tok("--text-muted", "#475569"), SURF = tok("--surface", "#fff");
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const wr = Math.round(winRate || 0);
    let pinnedIndex = null;
    let previewIndex = null;

    function validIndex(index) {
        const i = Number(index);
        return Number.isInteger(i) && !!data[i] ? i : null;
    }
    function activeIndex() {
        return previewIndex != null ? previewIndex : pinnedIndex;
    }
    function labelState(index) {
        const L = opt.plotOptions.pie.donut.labels;
        const item = index == null ? null : data[index];
        if (item) {
            L.total.label = item.label;
            L.total.formatter = () => String(item.value);
            L.value.formatter = () => String(item.value);
        } else {
            L.total.label = "Win rate";
            L.total.formatter = () => wr + "%";
            L.value.formatter = () => wr + "%";
        }
    }
    function syncVisualState() {
        const current = activeIndex();
        host.classList.toggle("donut-hovering", current != null);
        host.classList.toggle("donut-pinned", pinnedIndex != null);
        host.querySelectorAll(".apexcharts-pie-area").forEach(p => {
            p.classList.toggle("slice-hot", current != null && p.getAttribute("j") === String(current));
        });
        buttons.forEach(btn => {
            const idx = validIndex(btn.dataset.bidIndex);
            const selected = idx != null && idx === pinnedIndex;
            const preview = idx != null && idx === previewIndex && pinnedIndex == null;
            btn.classList.toggle("is-selected", selected);
            btn.classList.toggle("is-preview", preview);
            btn.setAttribute("aria-pressed", selected ? "true" : "false");
        });
    }
    function updateDisplay() {
        labelState(activeIndex());
        if (_bidDonut && typeof _bidDonut.updateOptions === "function") {
            _bidDonut.updateOptions(opt, false, false);
        }
        syncVisualState();
        requestAnimationFrame(syncVisualState);
    }
    function preview(index) {
        if (pinnedIndex != null) return;
        const i = validIndex(index);
        if (i == null) return;
        previewIndex = i;
        updateDisplay();
    }
    function clearPreview() {
        if (pinnedIndex != null || previewIndex == null) return;
        previewIndex = null;
        updateDisplay();
    }
    function togglePin(index) {
        const i = validIndex(index);
        if (i == null) return;
        pinnedIndex = pinnedIndex === i ? null : i;
        previewIndex = null;
        updateDisplay();
    }
    function previewFromConfig(cfg) {
        if (cfg) preview(cfg.dataPointIndex);
    }
    function toggleFromConfig(cfg) {
        if (cfg) togglePin(cfg.dataPointIndex);
    }

    const opt = {
        chart: { type: "donut", height: _bidDonutDrawnAt(host), parentHeightOffset: 0, fontFamily: "inherit", background: "transparent",
            animations: { enabled: !reduce, speed: 650, animateGradually: { enabled: true, delay: 90 } },
            events: {
                dataPointMouseEnter: (e, c, cfg) => { if (!reduce) previewFromConfig(cfg); },
                dataPointMouseLeave: () => { if (!reduce) clearPreview(); },
                dataPointSelection: (e, c, cfg) => toggleFromConfig(cfg),
            } },
        series: data.map(s => s.value), labels: data.map(s => s.label), colors: data.map(s => s.color),
        stroke: { width: 2, colors: [SURF], lineCap: "round" },
        plotOptions: { pie: { expandOnClick: false, donut: { size: "73%", labels: { show: true,
            name: { show: true, offsetY: 16, fontSize: "11px", color: MUT },
            value: { show: true, offsetY: -12, fontSize: "23px", fontWeight: 800, color: INK, formatter: () => wr + "%" },
            total: { show: true, showAlways: true, label: "Win rate", color: MUT, fontSize: "11px", formatter: () => wr + "%" } } } } },
        fill: { type: "gradient", gradient: { shade: dark ? "dark" : "light", type: "horizontal", shadeIntensity: 0.25, opacityFrom: 1, opacityTo: 0.9, stops: [0, 100] } },
        dataLabels: { enabled: false }, legend: { show: false },
        states: { hover: { filter: { type: "none" } } },
        tooltip: { theme: dark ? "dark" : "light", y: { formatter: v => v + " bids" } },
    };
    // Clear the loading placeholder (`.dash-skeleton-ring`) FIRST - ApexCharts
    // appends its chart into the host without removing existing children, so the
    // grey skeleton ring used to sit on top of the donut until the first hover
    // (whose updateOptions() rebuilt the host and incidentally wiped it). That
    // was the "stuck loading spinner that only clears when you hover" bug.
    host.innerHTML = "";
    _bidDonut = new ApexCharts(host, opt);
    _bidDonut.render();
    syncVisualState();
    buttons.forEach(btn => {
        const idx = validIndex(btn.dataset.bidIndex);
        if (idx == null || btn.disabled) return;
        btn.addEventListener("mouseenter", () => preview(idx));
        btn.addEventListener("mouseleave", clearPreview);
        btn.addEventListener("focus", () => preview(idx));
        btn.addEventListener("blur", clearPreview);
        btn.addEventListener("click", e => {
            e.preventDefault();
            togglePin(idx);
        });
    });
}
window.renderBidDonut = renderBidDonut;

function renderStatsSlider(pages) {
    const track = document.getElementById("dashSliderTrack");
    const dots  = document.getElementById("dashDots");
    if (!track) return;

    track.innerHTML = pages.map(pg => `<div class="dash-slide">${buildSlideHTML(pg)}</div>`).join("");
    if (dots) {
        dots.innerHTML = pages.map((_, idx) =>
            `<button class="dash-dot${idx === 0 ? " active" : ""}" data-idx="${idx}" aria-label="Page ${idx + 1}"></button>`).join("");
        dots.querySelectorAll(".dash-dot").forEach(b =>
            b.onclick = () => { goToSlide(parseInt(b.dataset.idx, 10), pages); restartAutoAdvance(pages); });
    }
    _dashSlideCount = pages.length;
    _dashSlideIdx = 0;

    const prev = document.getElementById("dashSlidePrev");
    const next = document.getElementById("dashSlideNext");
    if (prev) prev.onclick = () => { goToSlide(_dashSlideIdx - 1, pages); restartAutoAdvance(pages); };
    if (next) next.onclick = () => { goToSlide(_dashSlideIdx + 1, pages); restartAutoAdvance(pages); };

    activateSlide(0, pages);
    restartAutoAdvance(pages);
}

function goToSlide(idx, pages) {
    const n = pages.length;
    const next = ((idx % n) + n) % n;   // wrap both directions
    activateSlide(next, pages);
}

function activateSlide(idx, pages) {
    _dashSlideIdx = idx;
    const track = document.getElementById("dashSliderTrack");
    const titleEl = document.getElementById("dashStatsTitle");
    const dots = document.getElementById("dashDots");
    if (track) track.style.transform = `translateX(-${idx * 100}%)`;
    if (titleEl && pages[idx]) titleEl.textContent = pages[idx].title;
    if (dots) dots.querySelectorAll(".dash-dot").forEach((d, di) => d.classList.toggle("active", di === idx));

    // Draw-in the donut segments + count up the center for the active slide.
    const slides = track ? track.querySelectorAll(".dash-slide") : [];
    const slide = slides[idx];
    if (!slide) return;
    // Draw-in flourish: collapse segments, force a reflow so the 0 state
    // paints, then set the target - the CSS transition animates the "fill".
    // The end state is always the target, so even if anything here is skipped
    // the ring stays visible (segments are built at target in buildDonutHTML).
    const svg = slide.querySelector(".dash-donut svg");
    const segs = slide.querySelectorAll(".dash-donut svg .seg");
    if (segs.length) {
        segs.forEach(s => { s.style.strokeDasharray = `0 ${DASH_DONUT_C.toFixed(2)}`; });
        if (svg) void svg.getBoundingClientRect();   // force reflow
        segs.forEach(s => { if (s.dataset.dash) s.style.strokeDasharray = s.dataset.dash; });
    }
    const numEl = slide.querySelector(".dash-donut-center .num");
    // Money pages render a preformatted string (no data-target) - leave them be;
    // only the count pages get the count-up flourish.
    if (numEl && numEl.dataset.target != null) {
        const tgt = parseInt(numEl.dataset.target, 10) || 0; numEl.textContent = "0"; animateCount(numEl, tgt);
    }
}

function restartAutoAdvance(pages) {
    if (_dashSliderTimer) { clearInterval(_dashSliderTimer); _dashSliderTimer = null; }
    if (pages.length <= 1) return;
    // Skip the tick while the hub is hidden: the timer now survives a trip to
    // an editor (the hub is no longer rebuilt on return), and animating charts
    // nobody can see is CPU for nothing.
    _dashSliderTimer = setInterval(() => {
        const hub = document.getElementById("hubView");
        if (hub && hub.hidden) return;
        goToSlide(_dashSlideIdx + 1, pages);
    }, 7000);
}

// Recent-document cards - icon + body (name, badge, date).
// Quick-action icons shown on the right of each Recent card.
const DASH_ACT_ICON = {
    copy:   '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    folder: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    print:  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>',
    open:   '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
};
const DASH_ACT_TITLE = {
    copy: "Copy file", folder: "Show in folder", print: "Print", open: "Open PDF",
};
// Phase X.26 - per-CASE icons for Needs Attention / To-Do (distinct from the
// per-doc-TYPE icons in DASH_ICON): a target for "decide won/lost", coins for
// "uncosted", a paper-plane for "unsubmitted".
const DASH_CASE_ICON = {
    decide:      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    uncosted:    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/></svg>',
    unsubmitted: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    unpaid:      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/></svg>',
    ignored:     '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>',
    deliver:     '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1" y="3" width="15" height="13" rx="1"/><path d="M16 8h4l3 3v5h-7z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>',
};
window.DASH_CASE_ICON = DASH_CASE_ICON;   // reused by todo.js (To-Do view)

// Phase X.5 - Needs Attention (replaces Recent Documents).
// Minimal, actionable list with at most ONE inline action per row where it
// genuinely saves a click (stale-quote rows get a "Won" button). Everything
// else is a clickable row that opens the relevant document/view.
const EDIT_LOADER = {
    quote: "loadQuoteForEdit", inv: "loadInvForEdit", dn: "loadDnForEdit",
    po: "loadForEdit",
};
let _dashLastNeedsAttention = null;     // cached so search can restore it

function renderNeedsAttention(payload) {
    _dashLastNeedsAttention = payload || { decide: [], uncosted: [], unsubmitted: [], unpaid: [], deliver: [] };
    const wrap = document.getElementById("dashFocus");
    const title = document.getElementById("dashFocusTitle");
    const countEl = document.getElementById("dashFocusCount");
    if (!wrap) return;
    if (title) title.textContent = "Needs attention";

    const decide = (payload && payload.decide)      || [];
    const unc    = (payload && payload.uncosted)     || [];
    const unsub  = (payload && payload.unsubmitted)  || [];
    const unpaid = (payload && payload.unpaid)       || [];
    const deliver = (payload && payload.deliver)     || [];
    const total = decide.length + unc.length + unsub.length + unpaid.length + deliver.length;
    if (countEl) {
        if (total) { countEl.textContent = total; countEl.hidden = false; }
        else       { countEl.hidden = true; }
    }

    if (!total) {
        wrap.innerHTML =
            `<div class="dash-focus-empty">`
            + `<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`
            + `<div class="dash-focus-empty-text">`
            + `<strong>All caught up.</strong>`
            + `<span>No bids to decide, and every document this month is costed, submitted, and paid - nice work.</span>`
            + `</div></div>`;
        return;
    }

    // One attention card - ONE line (2026-10-21, Waleed): icon, number and
    // the action. The client and date line went: the button already says what
    // the document needs, and a one-line card lets the grid show far more of
    // them. Both are still in the hover title. The type is the group heading
    // above the cards, so the card no longer repeats it; a bid's countdown is
    // the one fact worth keeping, so it rides in a tag ("3d left").
    // div[role=button] so inner action buttons are valid; a whole-card click
    // opens the To-Do's read-only preview modal.
    const naCard = (cat, r) => {
        let tag = "";
        if (cat === "decide") {
            const dleft = Number(r.days_left);
            const urgent = !(dleft > 0);
            tag = `<span class="dash-na-tag na-tag-decide${urgent ? " is-urgent" : ""}">${esc(urgent ? "Due today" : `${dleft}d left`)}</span>`;
        }
        const tip = [r.doc_no, r.client, (r.date || "").slice(0, 10)].filter(Boolean).join(" · ")
            + (cat === "decide" ? ` - open ${r.days_open} days, auto-marked Lost at 14` : "");
        let btns = "";
        if (cat === "decide") {
            btns = `<button type="button" class="dash-na-btn is-won" data-act="won" data-no="${esc(r.doc_no)}" title="Mark ${esc(r.doc_no)} as Won">Won</button>`
                 + `<button type="button" class="dash-na-btn is-lost" data-act="lost" data-no="${esc(r.doc_no)}" title="Mark ${esc(r.doc_no)} as Lost">Lost</button>`;
        } else if (cat === "unsubmitted") {
            btns = `<button type="button" class="dash-na-btn is-submit" data-act="submit" data-kind="${esc(r.kind)}" data-no="${esc(r.doc_no)}" title="Mark ${esc(r.doc_no)} as submitted">Submit</button>`;
        } else if (cat === "unpaid") {
            btns = `<button type="button" class="dash-na-btn is-paid" data-act="paid" data-no="${esc(r.doc_no)}" title="Mark ${esc(r.doc_no)} as paid">Paid</button>`;
        }
        // Phase X.30 - every card gets a subtle Ignore (mute this concern).
        const actions = `<div class="dash-na-acts">${btns}`
            + `<button type="button" class="dash-na-ignore" data-act="ignore" data-cat="${esc(cat)}" data-kind="${esc(r.kind)}" data-no="${esc(r.doc_no)}" title="Ignore - ${esc(r.doc_no)} doesn't need attention here" aria-label="Ignore ${esc(r.doc_no)}">${DASH_CASE_ICON.ignored}</button>`
            + `</div>`;
        return `<div class="dash-na-card is-${cat} is-compact" role="button" tabindex="0" data-cat="${esc(cat)}" data-kind="${esc(r.kind)}" data-no="${esc(r.doc_no)}" data-date="${esc(r.date || "")}" title="${esc(tip)}">`
            + `<div class="dash-na-icon">${DASH_ICON[r.kind] || ""}</div>`
            + `<div class="dash-na-main">`
            +   `<div class="dash-na-no-line"><span class="dash-na-no">${esc(r.doc_no || "")}</span>${tag}</div>`
            + `</div>`
            + actions
            + `</div>`;
    };

    // The cards are GROUPED by type (2026-10-21, Waleed): a heading per type
    // with its icon, name and full count, then that type's cards. Same names
    // as the To-Do view's sections. Decide first (time-sensitive); Unpaid LAST
    // - payment takes the client weeks and there is nothing to do until it
    // lands. The grid-fitter gives every type a row before any type gets a
    // second one, and shows "+N more" on a heading whose cards were cut.
    const GROUP_CAP = 20;
    const groups = [
        ["decide", "Decide - Won or Lost", decide],
        ["uncosted", "Uncosted", unc],
        ["unsubmitted", "Unsubmitted", unsub],
        ["deliver", "Deliver & Invoice", deliver],
        ["unpaid", "Unpaid", unpaid],
    ].filter(([, , list]) => list.length);
    const groupHtml = ([cat, label, list]) =>
        `<div class="dash-na-group is-${cat}" role="heading" aria-level="3" data-total="${list.length}">`
        + `<span class="dash-na-group-icon">${DASH_CASE_ICON[cat] || ""}</span>`
        + `<span class="dash-na-group-name">${esc(label)}</span>`
        + `<span class="dash-na-group-count">${list.length}</span>`
        + `<span class="dash-na-group-rule" aria-hidden="true"></span>`
        + `<span class="dash-na-group-more" hidden></span>`
        + `</div>`
        + list.slice(0, GROUP_CAP).map(r => naCard(cat, r)).join("");
    wrap.innerHTML =
        `<div class="dash-focus-grid is-grouped">${groups.map(groupHtml).join("")}</div>`
        + `<button type="button" class="dash-focus-viewall" id="dashFocusViewAll" hidden>`
        +   `View all (${total}) - finish what's pending`
        +   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`
        + `</button>`;

    const va = document.getElementById("dashFocusViewAll");
    if (va) va.onclick = () => {
        if (window.showTodo) window.showTodo();
        else toast("To-do not loaded", true);
    };

    const refreshDashboardAttention = async () => {
        const na = await api().dashboard_needs_attention();
        renderNeedsAttention(na || {});
    };

    // Card click → preview popup first. Costs are typed in the document's own
    // private cost column, so an uncosted row opens the document (2026-09-11,
    // when the separate Costing & Profit screen was retired).
    wrap.querySelectorAll(".dash-na-card").forEach(el => {
        const open = () => {
            if (window.openTodoDocModal) {
                window.openTodoDocModal(
                    el.dataset.cat, el.dataset.kind, el.dataset.no, el.dataset.date, el.dataset.cat,
                    { onAfterAction: refreshDashboardAttention },
                );
            }
            else if (window.showTodo) window.showTodo();
        };
        el.onclick = open;
        el.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
        };
    });
    // Inline actions - resolve a bid (Won/Lost), mark submitted/paid, or mute
    // (Ignore) without leaving the dashboard, then refresh the feed.
    wrap.querySelectorAll(".dash-na-btn, .dash-na-ignore").forEach(b => {
        b.onclick = async (e) => {
            e.stopPropagation();
            const act = b.dataset.act;
            b.disabled = true;
            try {
                let ok = false;
                if (act === "won" || act === "lost") {
                    const r = await api().set_bid_status(b.dataset.no, act);
                    ok = r && r.ok;
                    if (ok) toast(`Marked ${b.dataset.no} as ${act === "won" ? "Won" : "Lost"}`);
                } else if (act === "submit") {
                    const r = await api().set_submitted(b.dataset.kind, b.dataset.no, true);
                    ok = r && r.ok;
                    if (ok) toast(`Marked ${b.dataset.no} as submitted`);
                } else if (act === "paid") {
                    const r = await api().set_invoice_paid(b.dataset.no, true);
                    ok = r && r.ok;
                    if (ok) toast(`Marked ${b.dataset.no} as paid`);
                } else if (act === "ignore") {
                    // Phase X.30 - mute this doc for this category.
                    const r = await api().set_attention_ignore(
                        b.dataset.kind, b.dataset.no, b.dataset.cat, true);
                    ok = r && r.ok;
                    if (ok) toast(`Ignored ${b.dataset.no} - restore it from the To-Do view`);
                }
                if (ok) {
                    await refreshDashboardAttention();
                    if (act === "submit" && window.openSubmittedFulfillment) {
                        window.openSubmittedFulfillment(b.dataset.kind, b.dataset.no);
                    }
                    // 2026-08-27 - paid is when the client's stamped copy lands
                    // on the desk, so ask for it (silent if already attached or
                    // waived). Same prompt as the panel's Paid button.
                    if (act === "paid" && window.SignedCopy) {
                        await window.SignedCopy.promptAfterPaid("inv", b.dataset.no);
                        await refreshDashboardAttention();
                    }
                } else {
                    toast("Couldn't update", true);
                    b.disabled = false;
                }
            } catch (err) {
                toast("Action failed: " + (err && err.message || err), true);
                b.disabled = false;
            }
        };
    });

    if (window.scheduleDashFit) window.scheduleDashFit();
}
window.renderNeedsAttention = renderNeedsAttention;

// Search results re-use the focus slot (same card grid). Clearing the search
// restores the cached Needs Attention payload.
let _dashLastSearchMatches = [];   // cached so Enter opens the first result
function renderSearchResults(matches, query) {
    matches = matches || [];
    _dashLastSearchMatches = matches;
    const wrap = document.getElementById("dashFocus");
    const title = document.getElementById("dashFocusTitle");
    const countEl = document.getElementById("dashFocusCount");
    if (!wrap) return;
    if (title) title.textContent = matches.length
        ? `Search results for "${query}"` : `No matches for "${query}"`;
    if (countEl) {
        if (matches.length) { countEl.textContent = matches.length; countEl.hidden = false; }
        else                { countEl.hidden = true; }
    }
    if (!matches.length) {
        wrap.innerHTML = `<div class="dash-focus-empty">`
            + `<div class="dash-focus-empty-text"><strong>No documents match.</strong>`
            + `<span>Searches document number, client, and items inside documents.</span></div></div>`;
        return;
    }
    // `snippet` is the item text that matched - shown so the user sees WHY a doc
    // surfaced when the hit was on its contents rather than its number/client.
    const card = (r, i) =>
        `<div class="dash-na-card" role="button" tabindex="0" data-idx="${i}" title="Open ${esc(r.no || "")}">`
        + `<div class="dash-na-icon">${DASH_ICON[r.doc] || ""}</div>`
        + `<div class="dash-na-main">`
        +   `<div class="dash-na-no-line"><span class="dash-na-no">${esc(r.no || "")}</span><span class="dash-na-tag">${esc(DASH_BADGE[r.doc] || r.doc)}</span></div>`
        +   `<div class="dash-na-meta"><span class="dash-na-client">${esc(r.client || "-")}</span><span class="dash-na-dot">·</span><span class="dash-na-date">${esc((r.when || "").slice(0, 10))}</span></div>`
        +   (r.snippet ? `<div class="dash-na-snippet">matches item: <span>${esc(r.snippet)}</span></div>` : "")
        + `</div></div>`;
    wrap.innerHTML =
        `<div class="dash-focus-grid">${matches.map(card).join("")}</div>`
        + `<button type="button" class="dash-focus-viewall" id="dashFocusViewAll" hidden>`
        +   `Open Document Records`
        +   `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`
        + `</button>`;
    const va = document.getElementById("dashFocusViewAll");
    if (va) va.onclick = () => { if (window.showDataManager) window.showDataManager("documents"); };
    wrap.querySelectorAll(".dash-na-card").forEach(el => {
        // Click → read-only preview popup (the shared To-Do modal) with a
        // "Go to document" button; the button opens the doc in its editor.
        // Falls back to opening directly if the modal module isn't loaded.
        const open = () => {
            const r = matches[Number(el.dataset.idx)];
            if (!r) return;
            if (window.openTodoDocModal) {
                window.openTodoDocModal(
                    "search", r.doc, r.no, (r.when || "").slice(0, 10), "search",
                    { onOpen: () => openSearchDoc(r) },
                );
            } else {
                openSearchDoc(r);
            }
        };
        el.onclick = open;
        el.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
        };
    });
    if (window.scheduleDashFit) window.scheduleDashFit();
}

// Smooth count-up - small flourish for the donut center numbers.
function animateCount(el, target) {
    if (!el) return;
    const from = parseInt(el.textContent, 10) || 0;
    if (target === from) { el.textContent = String(target); return; }
    const dur = 800;
    const t0 = performance.now();
    function tick(t) {
        const pr = Math.min(1, (t - t0) / dur);
        const eased = 1 - Math.pow(1 - pr, 3);
        el.textContent = String(Math.round(from + (target - from) * eased));
        if (pr < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

async function enterPO() {
    hideAllViews();
    document.getElementById("editorView").hidden = false;
    document.getElementById("poStrip").hidden = false;
    document.getElementById("poActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("طلب تجهيز مواد");
    refreshUniversalSettingsBtn();
    if (!_poInitialized) {
        state.autofitRows = docAutofitDefault("po");
        await initNextPoNumber();
        addRow();
        applyPoDateToday();   // Phase X.25 - first PO of the session shows today
        _poInitialized = true;
    }
    poSyncAutofitButton();
}

function enterQuote() {
    // Quotation opens a mini-hub of the 4 types; quote.js handles it.
    if (window.showQuoteHub) window.showQuoteHub();
}

function enterDN() {
    // Delivery Note opens directly in the editor (no mini-hub yet).
    if (window.enterDnEditor) window.enterDnEditor();
}

function enterINV() {
    // Invoice opens a mini-hub of the 4 types; inv.js handles it.
    if (window.showInvHub) window.showInvHub();
}

// Run when pywebview's JS bridge is ready. If we missed the event (e.g.
// after an F5 refresh), the bridge object is already present - just go.
function _startBoot() {
    if (window.pywebview && window.pywebview.api) bootstrap();
    else window.addEventListener("pywebviewready", bootstrap, { once: true });
}
// Defer until the whole document (including quote.js) has parsed, so
// window.initQuoteModule is defined before bootstrap calls it.
if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", _startBoot);
} else {
    setTimeout(_startBoot, 0);
}

// ============================================================
// Adaptive editor toolbar - always ONE row, never wrap, never clip.
//
// The topbar action groups (DN/Invoice are the widest) must stay a single
// row at every screen size. This measures the visible group against the
// space it has and steps DOWN through sizes until it fits:
//   full labels  →  .tb-compact (tighter, still labelled)  →  .tb-icons.
// Wide monitors keep the full look untouched; a laptop drops only as far
// as it must to stay on one line. CSS for the two classes lives in
// app_styles.css. Driven by a MutationObserver on each group's `hidden`
// attribute (fires when an editor reveals its toolbar) plus a debounced
// window-resize listener - deliberately NOT a ResizeObserver, which would
// re-fire on our own class changes and loop.
// ============================================================
function fitOneToolbar(el) {
    if (!el || el.hidden || el.clientWidth === 0) return;
    el.classList.remove("tb-compact", "tb-icons");
    // Reading scrollWidth forces reflow, so each check sees the new state.
    if (el.scrollWidth <= el.clientWidth + 1) return;   // full labels fit
    el.classList.add("tb-compact");
    if (el.scrollWidth <= el.clientWidth + 1) return;   // compact (short labels) fits
    el.classList.remove("tb-compact");
    el.classList.add("tb-icons");                        // last resort: icons only
}
function fitActiveToolbar() {
    document.querySelectorAll(".topbar .actions").forEach(fitOneToolbar);
}
window.fitActiveToolbar = fitActiveToolbar;
(function setupToolbarFit() {
    const run = () => {
        const groups = document.querySelectorAll(".topbar .actions");
        if (!groups.length) { setTimeout(run, 50); return; }
        groups.forEach(fitOneToolbar);
        const mo = new MutationObserver(muts => {
            for (const m of muts) {
                const el = m.target;
                if (!el.hidden) requestAnimationFrame(() => fitOneToolbar(el));
            }
        });
        groups.forEach(g => mo.observe(g, { attributes: true, attributeFilter: ["hidden"] }));
        let t = null;
        window.addEventListener("resize", () => {
            if (t) cancelAnimationFrame(t);
            t = requestAnimationFrame(() => groups.forEach(fitOneToolbar));
        });
    };
    if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", run);
    else run();
})();

// ============================================================
// Universal column-memory autocomplete (Phase G.14)
//
// One generic dropdown that fires for any TEXT input in any editor. It uses
// the per-(doc_type, sub_type, col_key) memory built up by saves so users get
// suggestions on every column, not just the primary one.
//
// Usage from an editor:
//   wireUniversalAutocomplete(inputEl, {
//       docType:   "quote",                  // "po" | "quote" | "dn" | "inv"
//       subType:   quoteState.type,          // "" for po/dn
//       colKey:    "description",
//       onPick:    (value) => { ... },       // fill the cell
//       isPrimary: false,                    // skip if primary (uses its own)
//   });
//
// The dropdown shows up to 10 matches. Arrow keys + Enter navigate, Esc dismisses.
// ============================================================
let UAC_TARGET = null;        // {input, opts, items, idx}

function _uacBox() { return document.getElementById("acBox"); }

function hideUniversalAc() {
    const box = _uacBox();
    if (box) box.hidden = true;
    UAC_TARGET = null;
}

async function _uacQuery(input, opts) {
    const prefix = (input.value || "").trim();
    if (!prefix) { hideUniversalAc(); return; }
    let items = [];
    try {
        items = await api().suggest_column_values(
            opts.docType, opts.subType || "", opts.colKey, prefix
        );
    } catch (e) { items = []; }
    if (!items || items.length === 0) { hideUniversalAc(); return; }
    _uacShow(input, opts, items);
}

function _uacShow(input, opts, items) {
    const box = _uacBox();
    if (!box) return;
    UAC_TARGET = { input, opts, items, idx: -1 };
    box.innerHTML = items.map((it, i) => {
        const v = it.value || "";
        const used = it.use_count > 1 ? ` <span class="ac-meta">·  ${it.use_count}×</span>` : "";
        return `<div class="ac-item" data-i="${i}"><div>${esc(v)}${used}</div></div>`;
    }).join("");
    box.querySelectorAll(".ac-item").forEach(el => {
        el.onmousedown = (e) => {
            e.preventDefault();
            _uacPick(parseInt(el.dataset.i, 10));
        };
    });
    const rect = input.getBoundingClientRect();
    box.style.top      = (rect.bottom + window.scrollY) + "px";
    box.style.left     = (rect.left   + window.scrollX) + "px";
    box.style.minWidth = rect.width + "px";
    box.hidden = false;
}

function _uacPick(i) {
    if (!UAC_TARGET) return;
    const t = UAC_TARGET;
    const value = (t.items[i] && t.items[i].value) || "";
    if (t.opts.onPick) t.opts.onPick(value);
    hideUniversalAc();
}

// Phase L.1.1 - User request: suggestions should ONLY appear in the first
// (primary) column. Non-primary columns (qty/rate/unit/description/...) no
// longer surface a dropdown. column_memory writes continue (cheap, future-
// proof) - we just don't show them. Flip SUGGEST_ON_NON_PRIMARY to true to
// re-enable the old behavior across the whole app.
const SUGGEST_ON_NON_PRIMARY = false;

// ============================================================
// Phase L.1.3 - Auto-size textareas to fit their content.
// Works for:
//   - typing into a textarea (delegated input listener below grows it live)
//   - loading an existing record (editors call autoSizeAllTextareas after
//     building rows / setting values)
// ============================================================
function autoSizeTextarea(ta) {
    if (!ta || ta.tagName !== "TEXTAREA") return;
    // Reset first so shrinking text also shrinks the box.
    ta.style.height = "auto";
    ta.style.height = (ta.scrollHeight + 1) + "px";
}
window.autoSizeTextarea = autoSizeTextarea;

function autoSizeAllTextareas(root) {
    root = root || document;
    root.querySelectorAll("textarea").forEach(ta => {
        // Only size visible ones - sizing hidden textareas reports 0.
        if (ta.offsetParent !== null || root !== document) autoSizeTextarea(ta);
    });
}
window.autoSizeAllTextareas = autoSizeAllTextareas;

// Delegated input handler: grow the textarea as the user types.
document.addEventListener("input", (e) => {
    if (e.target && e.target.tagName === "TEXTAREA") autoSizeTextarea(e.target);
});

// ============================================================
// Phase L.1.5 - Arrow-key navigation between table cells.
// Behavior:
//   ←/→  move between cells when cursor is at the start / end of the value
//   ↑/↓  move to the same column in the previous / next row, but only when
//        the cursor is on the first / last visual line of the cell
// Skips:
//   - Keys already handled by autocomplete (e.defaultPrevented)
//   - Inputs outside tables
// ============================================================
document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented) return;
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const el = e.target;
    if (!el || (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA")) return;
    const td = el.closest("td");
    if (!td) return;
    const tr = td.closest("tr");
    const tbody = tr && tr.parentElement;
    if (!tr || !tbody) return;

    // Cursor boundary check - let arrow keys move within the cell first.
    const len = (el.value || "").length;
    const ss  = el.selectionStart, se = el.selectionEnd;
    const collapsed = (ss === se);
    const atStart = collapsed && ss === 0;
    const atEnd   = collapsed && ss === len;
    const before  = (el.value || "").substring(0, ss);
    const after   = (el.value || "").substring(se);
    const onFirstLine = !before.includes("\n");
    const onLastLine  = !after.includes("\n");

    if (e.key === "ArrowLeft"  && !atStart) return;
    if (e.key === "ArrowRight" && !atEnd) return;
    if (e.key === "ArrowUp"    && !(collapsed && onFirstLine)) return;
    if (e.key === "ArrowDown"  && !(collapsed && onLastLine))  return;

    const cells = Array.from(tr.children);
    const tdIdx = cells.indexOf(td);
    if (tdIdx < 0) return;

    // Phase L.4.4 - detect RTL so the arrow direction matches visual reading.
    // In an RTL table the DOM-first cell appears on the RIGHT, so ArrowLeft
    // (visually leftward) should move to a higher DOM index.
    const tbl = tr.closest("table");
    const dirCtx = (tbl && (tbl.dir
        || tbl.closest("[dir]")?.getAttribute("dir")
        || window.getComputedStyle(tbl).direction)) || "ltr";
    const isRTL = String(dirCtx).toLowerCase() === "rtl";

    let target = null;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        // visualLeft = true means user wants to go visually leftward.
        const visualLeft = e.key === "ArrowLeft";
        // In LTR, visual-left = DOM-prev; in RTL, visual-left = DOM-next.
        const dir = isRTL ? (visualLeft ? 1 : -1) : (visualLeft ? -1 : 1);
        let idx = tdIdx + dir;
        while (idx >= 0 && idx < cells.length) {
            // Phase L.4.3 - DN/INV/QUT cells may wrap the input inside extra
            // <div>s (e.g. .qc-img-wrap). Use any descendant input/textarea,
            // not just direct children.
            const inp = cells[idx].querySelector(
                "input:not([disabled]):not([type=hidden]), textarea:not([disabled]), select:not([disabled])"
            );
            if (inp) { target = inp; break; }
            idx += dir;
        }
    } else {
        const rows = Array.from(tbody.children);
        const rowIdx = rows.indexOf(tr);
        const dir = e.key === "ArrowUp" ? -1 : 1;
        let ri = rowIdx + dir;
        while (ri >= 0 && ri < rows.length) {
            const sameTd = rows[ri].children[tdIdx];
            if (sameTd) {
                const inp = sameTd.querySelector(
                    "input:not([disabled]), textarea:not([disabled]), select:not([disabled])"
                );
                if (inp) { target = inp; break; }
            }
            ri += dir;
        }
    }
    if (target) {
        e.preventDefault();
        target.focus();
        // Place cursor at end so the user can keep typing where they expect.
        if (target.setSelectionRange && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
            const tlen = (target.value || "").length;
            target.setSelectionRange(tlen, tlen);
        }
    }
});

// ============================================================
// Phase M.2 - smart row mapper for Import-from-QUT/DN.
//
// The old importers copied values by EXACT key match only, so source
// documents whose schemas use different keys (service quotes keyed on
// `description`, manpower on `role`, user-added custom columns, etc.)
// imported wrong or empty. This maps by alias groups and collects any
// leftover text into the description so no data is silently dropped.
// ============================================================
const IMPORT_KEY_ALIASES = {
    item:        ["item", "equipment", "role", "name", "material", "product"],
    description: ["description", "specs", "desc", "details", "spec"],
    unit:        ["unit", "unit_type", "uom", "period", "measure", "unit_of_measure"],
    qty:         ["qty", "quantity", "count", "hours", "days", "number"],
    rate:        ["rate", "price", "unit_price", "unit_rate", "monthly_rate", "cost"],
    remarks:     ["remarks", "notes", "comment", "comments"],
};
// Keys that should never be treated as importable text (images are huge
// data URIs; computed cells are derived).
// 2026-09-05 - `company` joined the list. It is a PO column (which of our
// clients a line is for), and a PO row reaching this mapper from ANY path would
// otherwise fall through to the leftovers pass below and be appended into the
// target's DESCRIPTION, so every converted line printed "· TDC" to the client.
/* Keys the leftovers sweep must never treat as text worth keeping.
   `cost` is a bare number and was already dropped there, but the currency and
   costing machinery beside it is TEXT, so a row carrying `cost_ccy: "USD"`
   had "USD" appended to its description - and with `rate_ccy` too, "USD · USD".
   Found 2026-09-28 seeding a delivery note from an LPO's quotation; the same
   leak was reachable from the plain Quote → DN / INV import, where 211 live
   quotation rows carry `cost_ccy`. `smartMapRow` re-attaches cost and
   `cost_ccy` deliberately at the end - this only stops them being prose. */
const IMPORT_SKIP_KEYS = new Set(["image", "photo", "total", "company", "_index", "_no",
                                  "cost", "cost_ccy", "cost_usd", "cost_qty", "cost_sell",
                                  "rate_ccy", "money_ccy", "price_ccy"]);

function smartMapRow(srcRow, targetCols, targetPk) {
    const src = { ...srcRow };
    const used = new Set();

    const takeByAliases = (aliases) => {
        for (const a of aliases) {
            const v = src[a];
            if (!used.has(a) && v !== undefined && v !== null
                && String(v).trim() !== "") {
                used.add(a);
                return v;
            }
        }
        return undefined;
    };

    const out = {};
    // 1. Primary first - gets first crack at the alias pool so e.g. a
    //    service quote's `description` becomes the INV item, and is NOT
    //    duplicated into the description column afterwards. The chain falls
    //    through item-ish then description-ish keys so ANY source schema
    //    yields a primary value.
    const pkChain = [...new Set([
        targetPk,
        ...(IMPORT_KEY_ALIASES[targetPk] || []),
        ...IMPORT_KEY_ALIASES.item,
        ...IMPORT_KEY_ALIASES.description,
    ])];
    out[targetPk] = takeByAliases(pkChain) ?? "";

    // 2. Remaining target columns: exact key, then alias group, then default.
    for (const c of targetCols) {
        if (c.kind === "computed" || c.kind === "image") continue;
        if (c.key === targetPk) continue;
        const aliases = [c.key, ...(IMPORT_KEY_ALIASES[c.key] || [])];
        const v = takeByAliases(aliases);
        if (v !== undefined) out[c.key] = v;
        else if (c.default)  out[c.key] = c.default;
        else                 out[c.key] = "";
    }

    // 3. Leftovers - any unused source TEXT value gets appended to the
    //    description (or the widest text column) so data isn't lost.
    const leftovers = [];
    for (const [k, v] of Object.entries(src)) {
        if (used.has(k) || IMPORT_SKIP_KEYS.has(k)) continue;
        const s = (v === undefined || v === null) ? "" : String(v).trim();
        if (!s) continue;
        if (s.startsWith("data:")) continue;            // base64 images
        if (!isNaN(parseFloat(s)) && isFinite(s)) continue; // bare numbers - ambiguous, skip
        leftovers.push(s);
    }
    if (leftovers.length) {
        const descCol = targetCols.find(c => c.key === "description" && c.kind !== "computed")
                     || targetCols.find(c => c.kind === "text" && c.key !== targetPk);
        if (descCol) {
            const existing = (out[descCol.key] || "").toString().trim();
            out[descCol.key] = existing
                ? existing + "\n" + leftovers.join(" · ")
                : leftovers.join(" · ");
        }
    }
    // Phase N.3 - carry the hidden per-row COST through every import so profit
    // data flows Quote → DN → INV without re-entry. cost is never a schema
    // column, so the mapping above would otherwise drop it.
    if (srcRow.cost !== undefined && srcRow.cost !== null
        && String(srcRow.cost).trim() !== "") {
        out.cost = srcRow.cost;
        /* ...and NEVER without the unit it is written in (2026-08-26). Carrying
           the number alone let a dinar-costed line land in a dollar document
           looking like dollars; `reconcileImportedCostCurrency` below then
           converted the whole batch by the SOURCE DOCUMENT's flag, which is not
           what this row was typed in. */
        const srcCcy = String(srcRow.cost_ccy || "").trim().toUpperCase();
        if (srcCcy === "USD" || srcCcy === "IQD") out.cost_ccy = srcCcy;
    }
    return out;
}
window.smartMapRow = smartMapRow;

// Phase X.10 - currency-safe cost transfer on import.
// smartMapRow copies the raw per-row `cost`, but a cost is stored in the SOURCE
// document's entry currency while the destination keeps its OWN currency (USD by
// default). Without conversion an IQD cost (e.g. 7000 IQD = $5.00) is copied raw
// into a USD-default DN/INV and later misread as $7000. This converts each
// carried cost from the source currency into the destination's currency - same
// math as the backend `pull_costs_from_quote`. Best-effort: on any failure the
// costs are left exactly as copied.
async function reconcileImportedCostCurrency(rows, srcKind, srcNo, dstKind, dstNo) {
    try {
        const up = s => (s || "USD").toString().toUpperCase();
        const srcCur = up(await api().get_cost_currency(srcKind, srcNo));
        let dstCur = "USD";
        if (dstNo) { try { dstCur = up(await api().get_cost_currency(dstKind, dstNo)); } catch (e) {} }
        /* TWO pegs, not one (2026-08-26). "Source and target are the same deal
           so the conversion is self-inverse" held only while both documents
           resolved to the same rate - and they do not: a DN sits at the profile
           peg (1400) while the invoice that bills it settles in CASH at 1500.
           Converting $2,611.34 out of DN-TDC-039 at 1400 stored 3,655,876 IQD
           on an invoice that reads dinars at 1500 - $2,437.25, $174 of profit
           invented on one line. The number is read back by the DESTINATION, so
           the destination's peg is the one that has to put it there: out of the
           source's unit at the SOURCE's peg, into the destination's at the
           DESTINATION's. USD in the middle is the only common ground. */
        const peg = async (kind, no, fallback) => {
            try { const r = await api().doc_iqd_per_usd(kind, no); if (Number(r)) return Number(r); }
            catch (e) { /* fall back below */ }
            return fallback;
        };
        const profilePeg = window.IQD_PER_USD || 1400;
        const srcIQD = await peg(srcKind, srcNo, profilePeg);
        const dstIQD = dstNo ? await peg(dstKind, dstNo, srcIQD) : srcIQD;
        rows.forEach(r => {
            if (!r) return;
            /* Per ROW, from ITS OWN unit (2026-08-26). Two documents can share a
               currency flag and still hold lines typed in the other one, so the
               old `srcCur === dstCur → return` shortcut left those lines
               unconverted, and converting by the document flag divided a dollar
               line inside a dinar document by 1400. */
            const from = rowCostCurrency(r, srcCur);
            const raw = r.cost;
            const hasCost = !(raw === undefined || raw === null || String(raw).trim() === "");
            const v = hasCost ? parseFloat(String(raw).replace(/,/g, "")) : NaN;
            if (hasCost && isFinite(v) && from !== dstCur) {
                const usd = from === "IQD" ? v / srcIQD : v;            // → USD, source's peg
                const out = dstCur === "IQD" ? usd * dstIQD : usd;      // → destination's peg
                r.cost = formatRowCost(out, dstCur);
            }
            /* Stamped even when the cell is blank or unparseable: the number now
               IS in the destination's currency, and a row that says otherwise is
               read back at the wrong rate for the rest of its life. */
            r.cost_ccy = dstCur;
        });
    } catch (e) { /* leave costs as-is */ }
    return rows;
}
window.reconcileImportedCostCurrency = reconcileImportedCostCurrency;

// Phase M.2 - gather list_*_for_month results over the past N months so the
// import pickers aren't limited to "this month + last".
async function gatherRecentMonths(listFn, monthsBack = 6) {
    const t = state.today || { year: new Date().getFullYear(), month: new Date().getMonth() + 1 };
    let y = t.year, m = t.month;
    const out = [];
    for (let i = 0; i < monthsBack; i++) {
        try { out.push(...await listFn(y, m)); } catch { /* skip month */ }
        m -= 1;
        if (m === 0) { m = 12; y -= 1; }
    }
    return out;
}
window.gatherRecentMonths = gatherRecentMonths;

function wireUniversalAutocomplete(input, opts) {
    if (!input || input._uacWired) return;
    input._uacWired = true;
    // Primary column has its own richer per-doctype autocomplete elsewhere.
    if (opts.isPrimary) return;
    // Phase L.1.1 - non-primary suggestions globally suppressed.
    if (!SUGGEST_ON_NON_PRIMARY) return;
    input.addEventListener("input", () => _uacQuery(input, opts));
    input.addEventListener("focus", () => {
        if ((input.value || "").trim()) _uacQuery(input, opts);
    });
    input.addEventListener("blur",  () => setTimeout(() => {
        // Only clear if the dropdown isn't being clicked.
        if (UAC_TARGET && UAC_TARGET.input === input) hideUniversalAc();
    }, 150));
}
window.wireUniversalAutocomplete = wireUniversalAutocomplete;
window.hideUniversalAc          = hideUniversalAc;

// Keyboard nav for the universal autocomplete (separate handler - checks UAC_TARGET).
document.addEventListener("keydown", (e) => {
    const box = _uacBox();
    if (!UAC_TARGET || !box || box.hidden) return;
    if (e.key === "ArrowDown")  {
        e.preventDefault();
        UAC_TARGET.idx = Math.min(UAC_TARGET.idx + 1, UAC_TARGET.items.length - 1);
        _uacRefreshActive();
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        UAC_TARGET.idx = Math.max(UAC_TARGET.idx - 1, 0);
        _uacRefreshActive();
    } else if (e.key === "Enter" && UAC_TARGET.idx >= 0) {
        e.preventDefault();
        _uacPick(UAC_TARGET.idx);
    } else if (e.key === "Escape") {
        hideUniversalAc();
    }
});
function _uacRefreshActive() {
    document.querySelectorAll("#acBox .ac-item")
        .forEach((el, i) => el.classList.toggle("active", i === UAC_TARGET.idx));
}

// ============================================================
// Reusable filter chip rows (Phase G.9 / G.10)
//
// renderChipRow(container, options, current, onChange)
//   container : HTMLElement to fill with chip buttons
//   options   : [{ key, label, count? }] - chip definitions. "all" is canonical for the no-filter chip.
//   current   : currently-active key (defaults to "all")
//   onChange  : (newKey) => void
//
// Chips with count===0 (and key !== "all") render disabled.
// ============================================================
function renderChipRow(container, options, current, onChange) {
    if (!container) return;
    container.innerHTML = options.map(o => {
        const active = (current === o.key);
        const dead   = (o.count === 0 && o.key !== "all");
        // Phase M.8c - `title` (optional) gives the chip a hover tooltip,
        // useful when label is a short code like "MT" and we want to show
        // "Materials" on hover.
        const tip = o.title ? ` title="${esc(o.title)}"` : "";
        return `<button type="button" class="filter-chip${active ? ' is-active' : ''}"
                        data-chip-key="${esc(o.key)}" ${dead ? 'disabled' : ''}${tip}>
                    <span>${esc(o.label)}</span>${typeof o.count === "number"
                        ? `<span class="chip-count">${o.count}</span>` : ""}
                </button>`;
    }).join("");
    container.querySelectorAll(".filter-chip").forEach(btn => {
        btn.onclick = () => onChange(btn.dataset.chipKey);
    });
}
window.renderChipRow = renderChipRow;

// Count helpers used by chip rows everywhere.
function countByKey(items, pickKey) {
    const out = {};
    items.forEach(it => {
        const k = pickKey(it);
        if (k == null) return;
        out[k] = (out[k] || 0) + 1;
    });
    return out;
}
function uniqueSortedKeys(items, pickKey) {
    const set = new Set();
    items.forEach(it => { const k = pickKey(it); if (k) set.add(k); });
    return Array.from(set).sort((a, b) => a.localeCompare(b));
}
window.countByKey       = countByKey;
window.uniqueSortedKeys = uniqueSortedKeys;

// ============================================================
// Date helpers (Phase G.6)
// Backend uses D/M/YYYY (no zero padding). HTML <input type="date"> uses
// ISO YYYY-MM-DD. These helpers convert between the two so an editable date
// input stays in sync with what the backend expects.
// ============================================================
function displayToIso(disp) {
    if (!disp || disp === "-") return "";
    const m = String(disp).match(/^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})$/);
    if (!m) return "";
    const [, d, mo, y] = m;
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
function isoToDisplay(iso) {
    if (!iso) return "";
    const m = String(iso).match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (!m) return "";
    const [, y, mo, d] = m;
    return `${parseInt(d, 10)}/${parseInt(mo, 10)}/${y}`;
}
window.displayToIso = displayToIso;
window.isoToDisplay = isoToDisplay;

/* The document date field - ONE implementation for the quotation, DN and
   invoice editors (2026-09-02).

   Each editor used to inline this block, and all three inlined it in
   `initNew*` ONLY: the load-for-edit path set `.value` and stopped. Because a
   programmatic `.value =` fires no `input` event, opening a saved document and
   typing a new date changed what was on screen and nothing else - `state.date`
   still held the loaded date, so saving wrote the OLD one. The file never moved
   month and the PDF still showed the original date, which is exactly what
   Waleed reported on invoices. It only ever appeared to work if you had pressed
   New earlier in the session: that wired the listener on the same element, and
   the element outlives the editor.

   Call it from BOTH paths. `_wired` guards the listener (the element is
   permanent, the handler must not stack); the value is repainted every call.
   `docState` is each editor's single module-level state object, so the closure
   cannot go stale. */
function bindDocDate(elId, docState) {
    const el = document.getElementById(elId);
    if (!el || !docState) return;
    if (el.tagName !== "INPUT") { el.textContent = docState.date; return; }
    el.value = displayToIso(docState.date) || "";
    if (el._wired) return;
    el.addEventListener("input", () => {
        const disp = isoToDisplay(el.value);
        // A half-typed date parses to nothing - keep the last good one rather
        // than blanking the date the save is about to use.
        if (disp) { docState.date = disp; docState.dirty = true; }
    });
    el._wired = true;
}
window.bindDocDate = bindDocDate;

// Current local date in the app's canonical shape - matches backend
// api.get_today() exactly (_format_date is D/M/YYYY, no zero-padding). Computed
// fresh on every call so a long-running session never serves a stale "today".
function todayInfo() {
    const d = new Date();
    const y = d.getFullYear(), mo = d.getMonth() + 1, da = d.getDate();
    return {
        iso: `${y}-${String(mo).padStart(2, "0")}-${String(da).padStart(2, "0")}`,
        display: `${da}/${mo}/${y}`,
        year: y, month: mo,
    };
}
window.todayInfo = todayInfo;

// Set the PO editor's date field + state.poDate to TODAY. Used by every PO
// "new" path (first entry of a session, the New button, and after export) so a
// new order never inherits a previously-opened PO's date or a stale bootstrap
// date. Editing an existing PO does NOT call this (loadForEdit keeps its date).
function applyPoDateToday() {
    const t = todayInfo();
    state.poDate = t.display;
    const el = document.getElementById("today");
    if (el && el.tagName === "INPUT") el.value = t.iso;
    else if (el) el.textContent = t.display;
}
window.applyPoDateToday = applyPoDateToday;

async function initToday() {
    const t = await api().get_today();
    state.today = t;
    state.poDate = t.display;
    // PO date is now editable. Initialize from today and mirror changes into
    // state.poDate (in D/M/YYYY format which is what the backend expects).
    const el = document.getElementById("today");
    if (el && el.tagName === "INPUT") {
        el.value = displayToIso(t.display) || t.iso || "";
        el.oninput = () => {
            state.poDate = isoToDisplay(el.value) || t.display;
            state.dirtySinceSave = true;
        };
    } else if (el) {
        el.textContent = t.display;
    }
}

async function initNextPoNumber() {
    const r = await api().get_next_po_number();
    state.poNo = r.po_no;
    const el = document.getElementById("poNo");
    el.value = r.po_no;                 // editable input now, not a span
    document.getElementById("poExtra").textContent = "";
    window.setModeChip(false);
    // Mirror typed value into state so savePO uses what the user sees.
    el.oninput = () => { state.poNo = el.value.trim(); state.dirtySinceSave = true; };
}

function setPoSourceQuote(no, markDirty = false) {
    const val = String(no || "").trim();
    state.sourceQuote = val;
    const el = document.getElementById("poSrcQuote");
    if (el && el.value !== val) el.value = val;
    if (markDirty) state.dirtySinceSave = true;
}
window.setPoSourceQuote = setPoSourceQuote;

function getPoSourceQuote() {
    const el = document.getElementById("poSrcQuote");
    const val = (el ? el.value : state.sourceQuote || "").trim();
    state.sourceQuote = val;
    return val;
}

function bindPoQuoteField() {
    const input = document.getElementById("poSrcQuote");
    if (input && !input._poQuoteWired) {
        input._poQuoteWired = true;
        input.addEventListener("input", () => setPoSourceQuote(input.value, true));
        input.addEventListener("keydown", (e) => {
            if (e.key === "ArrowDown" && e.altKey) {
                e.preventDefault();
                openPoQuotePicker();
            }
        });
    }
    const btn = document.getElementById("btnPoPickQuote");
    if (btn && !btn._poQuoteWired) {
        btn._poQuoteWired = true;
        btn.onclick = safe(openPoQuotePicker, "Select quotation");
    }
}

// ── Phase Y.7 - PO-side fulfillment link buttons (DN / Invoice) ──────────────
// A saved PO can link a Delivery Note (mark its items delivered) or an Invoice
// (mark them paid) through the same item-checkbox popup used from the DN/INV
// side. The link needs a saved PO (keys on its number + reads its items), so the
// buttons stay disabled until then. Count badges show how many distinct DNs /
// invoices are already linked; the existing Delivered/Paid pills show status.
function poIsSaved() {
    return state.mode === "edit" && !!state.editPoNo;
}
function poLinkedCount(kind) {
    const f = state.poFulfillment;
    const bucket = kind === "dn" ? (f && f.delivered) : (f && f.paid);
    if (!bucket) return 0;
    const src = new Set();
    Object.values(bucket).forEach((v) => { if (v && v.source_no) src.add(v.source_no); });
    return src.size;
}
function refreshPoLinkButtons() {
    const saved = poIsSaved();
    [["btnPoLinkDn", "dn", "poLinkDnCount", "ربط إشعار تسليم بهذا الطلب"],
     ["btnPoLinkInv", "inv", "poLinkInvCount", "ربط فاتورة بهذا الطلب"]]
        .forEach(([btnId, kind, cntId, tip]) => {
            const btn = document.getElementById(btnId);
            if (btn) {
                btn.disabled = !saved;
                btn.title = saved ? tip : "احفظ الطلب أولاً";
            }
            const cnt = document.getElementById(cntId);
            if (cnt) {
                const n = saved ? poLinkedCount(kind) : 0;
                if (n > 0) { cnt.textContent = String(n); cnt.hidden = false; }
                else { cnt.hidden = true; cnt.textContent = ""; }
            }
        });
}
window.refreshPoLinkButtons = refreshPoLinkButtons;

// Reload this PO's fulfillment after a link is saved: refresh state, row tints,
// header chip, the link-button counts and the right panel.
async function reloadPoFulfillment() {
    if (!poIsSaved()) return;
    let rec = null;
    try { rec = await api().load_po(state.editPoNo); } catch (e) { rec = null; }
    if (rec) {
        state.poFulfillment = rec.fulfillment || null;
        decoratePoFulfillment();
        if (window.setPoFulfillChips) window.setPoFulfillChips(...poFulfillSummary());
    }
    refreshPoLinkButtons();
    if (window.refreshOpenPanel) window.refreshOpenPanel();
}

function bindPoLinkButtons() {
    [["btnPoLinkDn", "dn"], ["btnPoLinkInv", "inv"]].forEach(([id, kind]) => {
        const btn = document.getElementById(id);
        if (!btn || btn._poLinkWired) return;
        btn._poLinkWired = true;
        btn.onclick = () => {
            if (!poIsSaved()) { toast("احفظ الطلب أولاً قبل الربط", true); return; }
            if (window.openPoSourcePicker) {
                window.openPoSourcePicker(state.editPoNo, kind, { onAfter: reloadPoFulfillment });
            }
        };
    });
    refreshPoLinkButtons();
}

function _poQuoteSearchText(q) {
    return [
        q.quote_no, q.quote_date, q.quote_type, q.type_code,
        q.client_name, q.rfq_no,
    ].join(" ").toLowerCase();
}

async function openPoQuotePicker() {
    // Re-opening replaces the one already up, rather than stacking a second.
    if (window.Popup.count()) window.Popup.closeTop(null);

    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). The search filters
       the list, so it rides at the top of the body and stays put while the list
       scrolls; "Clear link" is a decision and sits in the footer. */
    const _h = window.Popup.open({
        title: "Link quotation",
        subtitle: getPoSourceQuote() || "No quotation selected",
        size: "lg",
        className: "po-quote-modal",
        html: true,
        body: `<div class="po-link-sticky"><div class="po-quote-search">`
            + `<input type="search" id="poQuoteSearch" placeholder="Search quotation, client, RFQ..." autocomplete="off" spellcheck="false" />`
            + `</div></div>`
            + `<div class="po-quote-list" id="poQuoteList">`
            + `<div class="po-quote-empty">Loading quotations...</div></div>`,
        actions: [{ label: "Clear link", value: "clear", cls: "ghost" }],
    });
    const backdrop = _h.panel;
    const close = () => _h.close(null);
    _h.result.then((v) => {
        if (v !== "clear") return;
        setPoSourceQuote("", true);
        toast("Quotation link cleared");
    });

    const search = backdrop.querySelector("#poQuoteSearch");
    const listEl = backdrop.querySelector("#poQuoteList");
    let quotes = [];
    const render = () => {
        const term = (search.value || "").trim().toLowerCase();
        const filtered = term ? quotes.filter(q => _poQuoteSearchText(q).includes(term)) : quotes;
        if (!filtered.length) {
            listEl.innerHTML = `<div class="po-quote-empty">No quotations found</div>`;
            return;
        }
        listEl.innerHTML = filtered.map(q => {
            const meta = [q.client_name, q.rfq_no, q.quote_date].filter(Boolean).join(" · ");
            return `
                <button type="button" class="po-quote-row" data-no="${esc(q.quote_no)}">
                    <span class="po-quote-row-main">
                        <strong>${esc(q.quote_no)}</strong>
                        ${q.type_code ? `<span class="qt-chip">${esc(q.type_code)}</span>` : ""}
                    </span>
                    <span class="po-quote-row-sub">${esc(meta || q.quote_type || "")}</span>
                </button>`;
        }).join("");
        listEl.querySelectorAll(".po-quote-row").forEach(row => {
            row.onclick = () => {
                setPoSourceQuote(row.dataset.no, true);
                close();
                toast(`Linked ${row.dataset.no}`);
            };
        });
    };

    search.addEventListener("input", render);
    try {
        quotes = await api().list_quote_refs("", 1000);
        if (!Array.isArray(quotes)) quotes = [];
    } catch (e) {
        quotes = [];
    }
    render();
    setTimeout(() => { try { search.focus(); } catch (e) {} }, 30);
}
window.openPoQuotePicker = openPoQuotePicker;

function updateRowCount() {
    const n = filledRows().length;
    document.getElementById("rowCount").textContent = n;
}

/**
 * Wrap a handler so any thrown error becomes a visible toast instead of
 * silently dying. Without this, an exception inside an async handler
 * just disappears into the void and the button appears to "do nothing".
 */
function safe(fn, label) {
    return async (...args) => {
        try { return await fn(...args); }
        catch (err) {
            console.error(`[${label}]`, err);
            toast(`خطأ في ${label}: ${err && err.message ? err.message : err}`, true);
        }
    };
}

/**
 * Run an async operation with the given button in a "loading" visual
 * state (spinner inside, disabled). Always restores the button, even
 * if the operation throws.
 */
/* ---------- Busy indicator (2026-08-05) ------------------------------------
   The in-button spinner was the ONLY sign that anything was happening, and it
   is addressed by element id. When the 2026-07-24 toolbar trim removed Export
   and Print, nine of those ids stopped existing - including `#btnSave`, which
   is what `savePO` still looked up. `withLoading(null, fn)` returns silently,
   so saving a PO showed no feedback at all while Chrome rendered the PDF.

   The fix is a signal that does not depend on any button existing: a thin
   indeterminate bar across the top of the window, driven by a COUNT of
   in-flight operations rather than by whoever was clicked. A missing button
   can no longer take the feedback down with it, and overlapping operations
   can't switch it off early.

   The button spinner stays where a button really exists - it is where the eye
   already is - but it is now the garnish, not the mechanism. */
let _busyDepth = 0;
let _busyShowTimer = null;

function _busyBar() { return document.getElementById("busyBar"); }

function beginBusy() {
    _busyDepth += 1;
    if (_busyDepth > 1) return;                  // already showing
    // Hold off briefly: most calls finish in well under this, and a bar that
    // flashes on every keystroke-fast operation reads as a glitch.
    clearTimeout(_busyShowTimer);
    _busyShowTimer = setTimeout(() => {
        const bar = _busyBar();
        if (bar && _busyDepth > 0) { bar.hidden = false; bar.classList.add("is-on"); }
    }, 140);
}

function endBusy() {
    _busyDepth = Math.max(0, _busyDepth - 1);
    if (_busyDepth > 0) return;                  // something else is still running
    clearTimeout(_busyShowTimer);
    const bar = _busyBar();
    if (!bar) return;
    bar.classList.remove("is-on");
    // Let the fade finish before hiding, but only if nothing new started.
    setTimeout(() => { if (_busyDepth === 0) bar.hidden = true; }, 220);
}
window.beginBusy = beginBusy;
window.endBusy = endBusy;

/** First of these ids that actually exists - so a renamed or removed button
 *  degrades to "no spinner" instead of pointing at nothing. */
function firstEl(...ids) {
    for (const id of ids) {
        const el = id && document.getElementById(id);
        if (el) return el;
    }
    return null;
}
window.firstEl = firstEl;

async function withLoading(buttonEl, asyncFn) {
    beginBusy();
    if (buttonEl) {
        buttonEl.classList.add("is-loading");
        buttonEl.disabled = true;
    }
    try {
        return await asyncFn();
    } finally {
        if (buttonEl) {
            buttonEl.classList.remove("is-loading");
            buttonEl.disabled = false;
        }
        endBusy();
    }
}

function bindUi() {
    bindPoQuoteField();
    bindPoLinkButtons();
    bindPoWorkflowButton();
    document.getElementById("poReference").addEventListener("input", () => {
        state.dirtySinceSave = true;
    });

    // "+ إضافة سطر" - if the trailing blank row is already empty, just focus it.
    // Otherwise add a new row and focus that. Prevents stacking empty rows.
    document.getElementById("btnAddRow").onclick = safe(() => {
        const last = state.rows[state.rows.length - 1];
        if (!last || hasAnyContent(last)) {
            addRow();
        }
        focusRow(state.rows.length - 1, "name");
    }, "إضافة سطر");

    document.getElementById("btnNew").onclick        = safe(onNewClicked,   "جديد");
    document.getElementById("btnPreview").onclick    = safe(previewPdf,     "معاينة");
    { const b = document.getElementById("btnPoPrint"); if (b) b.onclick = safe(printPO, "طباعة"); }
    // Phase L.1.2 - Save (stay in editor) vs Export (reset to new).
    const btnPoStay = document.getElementById("btnPoStay");
    if (btnPoStay) btnPoStay.onclick                 = safe(() => savePO({ stay: true,  skipConfirm: true }), "حفظ");
    // Make DN / Make Invoice from this PO (2026-09-05). Both go through the ONE
    // implementation in po_handoff.js - see that file's header for why.
    { const b = document.getElementById("btnPoMakeDn");
      if (b) b.onclick = safe(() => window.poMakeDocument("dn"), "Make DN"); }
    { const b = document.getElementById("btnPoMakeInv");
      if (b) b.onclick = safe(() => window.poMakeDocument("inv"), "Make Invoice"); }
    const btnPoAutofit = document.getElementById("btnPoAutofit");
    if (btnPoAutofit) btnPoAutofit.onclick = safe(async () => {
        state.autofitRows = !state.autofitRows;
        state.dirtySinceSave = true;
        poSyncAutofitButton();
        await updateSplitWarning();
    }, "Auto-fit rows");
    if (window.wireFitColsButton) window.wireFitColsButton("btnPoFitCols", "#editorTable", {
        // No money column in the Arabic PO table (name/description/unit/qty/
        // company/image), so it needs no value floor - see col_resize.js.
        pdf: { widthMm: 190, headerFont: 'bold 14pt "Arial","Tahoma",sans-serif' },
        emptyMsg: "أضف مواد بمحتوى أولاً ثم لائم الأعمدة.",
        onFit: () => { state.dirtySinceSave = true; },
    });
    { const b = document.getElementById("btnOpen"); if (b) b.onclick = safe(showOpenView, "فتح طلب سابق"); }
    document.getElementById("btnCloseOpen").onclick  = safe(showEditorView, "رجوع");
    document.getElementById("btnRefreshList").onclick = safe(refreshPoList, "تحديث القائمة");

    // Universal settings panel (only opens from the main hub)
    document.getElementById("btnSettings").onclick       = safe(showSettingsView,      "Settings");
    document.getElementById("btnSettingsCancel").onclick = safe(showHub,               "Cancel settings");
    document.getElementById("btnSettingsSave").onclick   = safe(saveSettings,          "Save settings");
    document.getElementById("btnSettingsReset").onclick  = safe(resetSettings,         "Restore defaults");
    document.getElementById("btnUploadLogo").onclick     = safe(uploadLogoFromSettings, "Upload logo");

    // Data Manager / Document Records are reached straight from the dashboard
    // (and the settings → About → quick-access shortcuts). Its Close returns home.
    const dmCloseBtn = document.getElementById("btnDmClose");
    if (dmCloseBtn) dmCloseBtn.onclick = safe(showHub, "Close Data Manager");

    // Per-section ⚙ for the PO action group (PO has no mini-hub).
    const poSetBtn = document.getElementById("btnPoSettings");
    if (poSetBtn) poSetBtn.onclick = safe(() => {
        if (!window.openPoSettings) { toast("Settings module not loaded", true); return; }
        window.openPoSettings();
    }, "إعدادات PO");

    // Hub navigation - single unified Home button (top-right). It carries the
    // unsaved-changes guard that used to live on the per-section "back" buttons
    // (btnPoBack / btnDnBack, now removed). PO's guard is inline here; DN
    // exposes window.dnConfirmLeave. QUT/INV keep their per-type drafts so they
    // don't need a prompt.
    document.getElementById("btnHome").onclick = safe(async () => {
        const cmView = document.getElementById("clientMasterView");
        if (cmView && !cmView.hidden && window.ClientMaster && !(await window.ClientMaster.canLeave())) return;
        const poA = document.getElementById("poActions");
        const dnA = document.getElementById("dnActions");
        if (poA && !poA.hidden) {
            if (!(await poConfirmLeave())) return;
        } else if (dnA && !dnA.hidden && window.dnConfirmLeave) {
            if (!(await window.dnConfirmLeave())) return;
        }
        showHub();
    }, "الرئيسية");
    // Editor "Back" buttons (Clients / Recent → editor → back one step).
    _BACK_BTN_IDS.forEach(id => {
        const b = document.getElementById(id);
        if (b) b.onclick = safe(() => window.runEditorReturn(), "Back");
    });
    // Phase M - both legacy .hub-card (sub-hub views) and new .dash-card
    // (main dashboard) trigger the same enter* flows.
    document.querySelectorAll(".hub-card[data-doc], .dash-card[data-doc]").forEach(card => {
        card.onclick = safe(() => {
            const doc = card.dataset.doc;
            if (doc === "po") return enterPO();
            if (doc === "quote") return enterQuote();
            if (doc === "dn") return enterDN();
            if (doc === "inv") return enterINV();
        }, "فتح المستند");
    });
    // Phase M - dashboard hero buttons.
    const dashLock = document.getElementById("dashLockBtn");
    if (dashLock) dashLock.onclick = safe(() => lockScreen(), "Lock");
    const dashSet = document.getElementById("dashSettingsBtn");
    if (dashSet) dashSet.onclick = safe(() => document.getElementById("btnSettings").click(), "Open settings");
    // 2026-08-11 - this card used to open the Data Manager's "catalogs" mode:
    // thirteen lists over seven tables, with the same item in up to four of
    // them. It now opens Master Data, which is one row per item.
    const dashAF = document.getElementById("dashOpenAutofill");
    if (dashAF) dashAF.onclick = safe(() => {
        if (window.showItemMaster) window.showItemMaster();
        else toast("Master Data not loaded", true);
    }, "Open Master Data");
    // Phase X.16 - Document Records moved off the dashboard into Settings.
    const openRecords = safe(() => {
        if (window.showDataManager) window.showDataManager("documents");
        else toast("Data Manager not loaded", true);
    }, "Open Records");
    const dashRec = document.getElementById("dashOpenRecords");   // legacy (card removed)
    if (dashRec) dashRec.onclick = openRecords;
    const setRec = document.getElementById("btnOpenRecords");     // now in Settings → General
    if (setRec) setRec.onclick = openRecords;
    const dashIns = document.getElementById("dashOpenInsights");
    if (dashIns) dashIns.onclick = safe(() => {
        if (window.showAnalytics) window.showAnalytics();
        else toast("Insights not loaded", true);
    }, "Open Insights");
    const dashLpo = document.getElementById("dashOpenLpos");
    if (dashLpo) dashLpo.onclick = safe(() => {
        if (window.showLpos) window.showLpos();
        else toast("LPOs not loaded", true);
    }, "Open Client LPOs");
    // Global keyboard shortcuts.
    bindKeyboardShortcuts();

    // Privacy mode - restore whether the private COST/PROFIT strip is hidden
    // and wire the three editors' toggles (2026-08-25).
    initPrivacyMode();

    // Window-close protection - warn if there's unsaved data.
    bindBeforeUnload();

    // Hover-between-rows insert affordance.
    bindRowInsertAffordance();
}

// ============================================================
// Keyboard shortcuts
// Convention: hotkeys do NOT fire while the user is typing inside an
// <input> or <textarea>, except for Ctrl/Cmd-modified combos (which the
// user clearly intends as shortcuts, not as text).
// ============================================================
function bindKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
        const inField = isTextField(document.activeElement);
        const ctrl    = e.ctrlKey || e.metaKey;

        // Esc - close any open modal/view as a "back" action.
        if (e.key === "Escape") {
            // A popup handles its own Esc (and stops the event), so if one is
            // open this handler must not also navigate the view behind it.
            if (window.Popup && window.Popup.count()) return;
            if (!document.getElementById("settingsView").hidden) {
                e.preventDefault(); showHub(); return;
            }
            if (!document.getElementById("openView").hidden) {
                e.preventDefault(); showEditorView(); return;
            }
            if (!document.getElementById("quoteOpenView").hidden) {
                e.preventDefault(); enterQuote(); return;
            }
            const dnOpen = document.getElementById("dnOpenView");
            if (dnOpen && !dnOpen.hidden) {
                e.preventDefault(); enterDN(); return;
            }
            const invOpen = document.getElementById("invOpenView");
            if (invOpen && !invOpen.hidden) {
                e.preventDefault(); enterINV(); return;
            }
            return;
        }

        // Ctrl + L - lock: the gate closes over the app (2026-10-02). Works
        // while typing too; somebody walking up is exactly when it is needed.
        if (ctrl && !e.shiftKey && !e.altKey && (e.key === "l" || e.key === "L")) {
            e.preventDefault();
            lockScreen();
            return;
        }

        // "?" - keyboard shortcut cheat-sheet (Phase O). Only when not typing.
        if (e.key === "?" && !ctrl && !inField) {
            e.preventDefault();
            showShortcutSheet();
            return;
        }

        // Ctrl + Shift + H - hide/show the private figures (COST, PROFIT and
        // the profit cards). Works while typing in a cell too: the whole point
        // is that somebody just walked up while you were entering costs.
        if (ctrl && e.shiftKey && (e.key === "H" || e.key === "h")) {
            e.preventDefault();
            togglePrivacyMode();
            return;
        }

        if (!ctrl) return;
        const k = e.key.toLowerCase();
        const inQuote = !document.getElementById("quoteView").hidden;
        const inPO    = !document.getElementById("editorView").hidden;
        const dnEl    = document.getElementById("dnView");
        const inDN    = !!(dnEl && !dnEl.hidden);
        const invEl   = document.getElementById("invView");
        const inINV   = !!(invEl && !invEl.hidden);
        if (!inQuote && !inPO && !inDN && !inINV) return;

        if (inINV) {
            if (k === "s") { e.preventDefault(); window.invSave    && window.invSave({ stay: true, skipConfirm: true }); }
            else if (k === "p") { e.preventDefault(); window.invPreview && window.invPreview(); }
            else if (k === "n") { e.preventDefault(); window.invNew     && window.invNew(); }
            else if (k === "o") { e.preventDefault(); window.invOpen    && window.invOpen(); }
            return;
        }
        if (inDN) {
            if (k === "s") { e.preventDefault(); window.dnSave    && window.dnSave({ stay: true, skipConfirm: true }); }
            else if (k === "p") { e.preventDefault(); window.dnPreview && window.dnPreview(); }
            else if (k === "n") { e.preventDefault(); window.dnNew     && window.dnNew(); }
            else if (k === "o") { e.preventDefault(); window.dnOpen    && window.dnOpen(); }
            return;
        }
        if (inQuote) {
            // Route to quote.js handlers (exposed on window).
            if (k === "s") { e.preventDefault(); window.quoteSave && window.quoteSave({ stay: true, skipConfirm: true }); }
            else if (k === "p") { e.preventDefault(); window.quotePreview && window.quotePreview(); }
            else if (k === "n") { e.preventDefault(); window.quoteNew && window.quoteNew(); }
            else if (k === "o") { e.preventDefault(); window.quoteOpen && window.quoteOpen(); }
            return;
        }
        // PO context
        if (k === "s") { e.preventDefault(); savePO({ stay: true, skipConfirm: true }); }
        else if (k === "p") { e.preventDefault(); previewPdf(); }
        else if (k === "n") { e.preventDefault(); onNewClicked(); }
        else if (k === "o") { e.preventDefault(); showOpenView(); }
    });
}

function isTextField(el) {
    if (!el) return false;
    const tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
}

// ============================================================
// Window-close protection
// ============================================================
function bindBeforeUnload() {
    window.addEventListener("beforeunload", (e) => {
        // PO editor unsaved data
        const inPO = !document.getElementById("editorView").hidden;
        if (inPO && state.dirtySinceSave && filledRows().length > 0) {
            e.preventDefault();
            e.returnValue = "لديك طلب لم يُحفَظ بعد. هل تريد المغادرة؟";
            return e.returnValue;
        }
        // DN editor unsaved data
        const dnEl = document.getElementById("dnView");
        if (dnEl && !dnEl.hidden && window.dnState && window.dnState.dirty) {
            e.preventDefault();
            e.returnValue = "You have an unsaved delivery note. Leave anyway?";
            return e.returnValue;
        }
        // Quotation editor unsaved data
        const quoteEl = document.getElementById("quoteView");
        if (quoteEl && !quoteEl.hidden && window.quoteState && window.quoteState.dirty) {
            e.preventDefault();
            e.returnValue = "You have an unsaved quotation. Leave anyway?";
            return e.returnValue;
        }
        // INV editor unsaved data
        const invEl = document.getElementById("invView");
        if (invEl && !invEl.hidden && window.invState && window.invState.dirty) {
            e.preventDefault();
            e.returnValue = "You have an unsaved invoice. Leave anyway?";
            return e.returnValue;
        }
    });
}

// ============================================================
// Hover-between-rows insert affordance
//
// Strategy:
//   - One document-level mousemove handler updates `targetIndex` whenever
//     the cursor is near a row border.
//   - A separate `lastShownIndex` is captured WHEN the button shows.
//     The click handler uses this - it can NEVER be -1 while the button
//     is visible, even if mousemove resets targetIndex just before the
//     click event fires.
//   - Generous dead zone around the button (16px) so cursor on/near the
//     button always preserves visibility.
// ============================================================
const INSERT_THRESHOLD_PX        = 14;
const BUTTON_DEAD_ZONE_PX        = 16;
const TABLE_HORIZ_SLOP_PX        = 8;

// Editors that support the hover "+ insert row" affordance. Each names its
// view + tbody and resolves its per-document insert function (defined in the
// respective editor file). PO's insertRowAt is local; the rest are on window.
const ROW_INSERT_EDITORS = [
    { view: "editorView", tbody: "rowsBody",    get: () => (typeof insertRowAt === "function" ? insertRowAt : null) },
    { view: "quoteView",  tbody: "qRowsBody",   get: () => window.insertQuoteRowAt },
    { view: "dnView",     tbody: "dnRowsBody",  get: () => window.insertDnRowAt },
    { view: "invView",    tbody: "invRowsBody", get: () => window.insertInvRowAt },
];
function activeRowInsertEditor() {
    for (const ed of ROW_INSERT_EDITORS) {
        const v  = document.getElementById(ed.view);
        const tb = document.getElementById(ed.tbody);
        const fn = ed.get();
        if (v && !v.hidden && tb && typeof fn === "function") return { tbody: tb, insertAt: fn };
    }
    return null;
}

function bindRowInsertAffordance() {
    const btn = document.getElementById("rowInsertBtn");
    if (!btn) return;

    let targetIndex    = -1;     // updated continuously by mousemove
    let lastShownIndex = -1;     // captured WHEN the button shows; used on click
    let activeInsert   = null;   // the insert fn of the editor under the cursor

    document.addEventListener("mousemove", (e) => {
        const ed = activeRowInsertEditor();
        if (!ed) { hide(); return; }
        const tbody = ed.tbody;

        // If the cursor is on or right beside the button, freeze (don't recalc).
        if (!btn.hidden) {
            const b = btn.getBoundingClientRect();
            const pad = BUTTON_DEAD_ZONE_PX;
            if (e.clientX >= b.left - pad && e.clientX <= b.right + pad &&
                e.clientY >= b.top  - pad && e.clientY <= b.bottom + pad) {
                return;
            }
        }

        const rows = tbody.querySelectorAll("tr");
        if (rows.length < 2) { hide(); return; }   // need 2+ rows to insert between

        const tableRect = tbody.getBoundingClientRect();
        if (e.clientX < tableRect.left  - TABLE_HORIZ_SLOP_PX ||
            e.clientX > tableRect.right + TABLE_HORIZ_SLOP_PX ||
            e.clientY < tableRect.top    - INSERT_THRESHOLD_PX ||
            e.clientY > tableRect.bottom + INSERT_THRESHOLD_PX) {
            hide(); return;
        }

        // Find the closest between-rows border (rows index 1..N-1).
        let best = -1;
        let bestDist = INSERT_THRESHOLD_PX + 1;
        for (let i = 1; i < rows.length; i++) {
            const dist = Math.abs(e.clientY - rows[i].getBoundingClientRect().top);
            if (dist < bestDist) {
                bestDist = dist;
                best = i;
            }
        }
        if (best < 0) { hide(); return; }

        activeInsert = ed.insertAt;     // keep current even when pinned
        // Already pinned at this border - leave it alone (no flicker).
        if (!btn.hidden && best === targetIndex) return;

        const t = rows[best].getBoundingClientRect();
        const isRtl = document.documentElement.dir === "rtl";
        btn.style.top  = (window.scrollY + t.top) + "px";
        btn.style.left = (window.scrollX + (isRtl ? t.right - 14 : t.left + 14)) + "px";
        btn.hidden = false;
        targetIndex = best;
        lastShownIndex = best;
    });

    // Use mousedown not click - fires before any tail-end mousemove can
    // reset targetIndex. We commit on mousedown using lastShownIndex
    // (which was captured WHEN the button became visible).
    btn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = lastShownIndex;
        const ins = activeInsert;
        const rtl = document.documentElement.dir === "rtl";
        if (idx < 0 || typeof ins !== "function") {
            toast(rtl ? "لم يحدد موضع الإدراج. مرر المؤشر بين سطرين وحاول مرة أخرى." : "Hover between two rows, then click.", true);
            return;
        }
        hide();
        try {
            ins(idx);
            toast(rtl ? "تم إدراج سطر فارغ" : "Row inserted");
        } catch (err) {
            toast((rtl ? "خطأ في إدراج السطر: " : "Insert failed: ") + (err && err.message ? err.message : err), true);
        }
    });

    function hide() {
        if (!btn.hidden) btn.hidden = true;
        targetIndex = -1;
        // NOTE: lastShownIndex is NOT reset here - it's the value to use
        // if the button was just clicked. It's only updated when a new
        // position is shown.
    }
}

function insertRowAt(i) {
    // Splice a new blank row into state at position i, then re-render.
    const blank = { name: "", description: "", unit: "", qty: "", company: "" };
    state.rows.splice(i, 0, blank);
    state.dirtySinceSave = true;
    fullRenderRows();
    updateRowCount();
    updateSplitWarning();
    focusRow(i, "name");
}

// ============================================================
// Universal (app-wide) settings panel
// Per-doctype settings live in the ⚙ on each mini-hub or action group.
// This panel only covers logo, paths, and UI language.
// ============================================================
async function showSettingsView() {
    hideAllViews();
    document.getElementById("settingsView").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("App settings");
    // Hide the universal ⚙ gear - it's redundant while already on the settings page.
    refreshUniversalSettingsBtn();
    initSettingsNav();

    const s = await api().get_settings();
    populateSettingsForm(s);

    // About panel - reflect the live documents path + interface language.
    const docsEl = document.getElementById("aboutDocsPath");
    if (docsEl) {
        const root = document.getElementById("set_documents_root");
        docsEl.textContent = (root && root.value.trim()) || "-";
    }
    const langEl = document.getElementById("aboutLang");
    if (langEl) {
        const sel = document.getElementById("set_ui_lang");
        langEl.textContent = sel ? (sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : sel.value) : "-";
    }
    wireAboutPanel();
    wireBehaviourPanel();

    // Phase M.6 - header/closing/address editor (moved here from per-type modals).
    const dtSel = document.getElementById("hdrDocType");
    if (dtSel) {
        dtSel.onchange = () => loadHdrEditor();
        await loadHdrEditor();
    }
    const hdrSave = document.getElementById("btnHdrSave");
    if (hdrSave) hdrSave.onclick = safe(saveHdrEditor, "Save header");
    const cEl = document.getElementById("hdrBrand");
    const hEl = document.getElementById("hdrBrandHex");
    if (cEl && hEl) {
        cEl.oninput = () => { hEl.value = cEl.value.toUpperCase(); };
        hEl.oninput = () => { const v = hEl.value.trim(); if (/^#[0-9A-Fa-f]{6}$/.test(v)) cEl.value = v; };
    }
}

// Settings category nav - switch which panel is shown. Idempotent (safe to
// call on every settings open). Defaults to the "general" panel.
function initSettingsNav() {
    const nav = document.getElementById("settingsNav");
    if (!nav) return;
    const items = Array.from(nav.querySelectorAll(".settings-nav-item"));
    const panels = Array.from(document.querySelectorAll(".settings-panel"));
    function show(sect) {
        items.forEach(b => b.classList.toggle("active", b.dataset.sect === sect));
        panels.forEach(p => { p.hidden = (p.dataset.panel !== sect); });
        const body = document.querySelector(".settings-body");
        if (body) body.scrollTop = 0;
    }
    items.forEach(b => { b.onclick = () => {
        show(b.dataset.sect);
        // Read the accounts when the tab is opened, not on every settings
        // open: they are a file read the other panels do not need.
        if (b.dataset.sect === "payments") renderPaymentAccounts();
    }; });
    // Reset to the first category each time settings opens.
    show("general");
}

/* ============================================================
   Payment accounts (Settings > Payments, 2026-09-21)

   The accounts money arrives in, and the exchange rate each one carries. That
   rate is what a document's COSTS are converted at when the client is paid into
   that account - see backend/payment_accounts.py. It does not touch what an
   invoice bills.

   They live in the invoice section of settings.json, which is where they have
   always lived, so this reads and writes them through the same door the invoice
   gear used to. That editor is gone: two editors for one list is how they drift.
   ============================================================ */
/* Which ACCOUNT the cost exchange rate came from (2026-09-21).

   The Costing screen prints "converted at 1 $ = 1,400 IQD" and that figure
   decides the whole document's profit. The rate now belongs to the account the
   client is paid into, so the readout names it: a number on screen should say
   where it came from.

   Cached per client + account because the econ cards repaint on every
   keystroke and this is a bridge call. A miss returns "" and repaints when the
   answer arrives, so nothing ever blocks on it. */
const _rateAccountCache = new Map();

function rateAccountLabel(kind, clientName, bankKey, repaint) {
    const key = `${kind}|${String(clientName || "").trim().toLowerCase()}`
        + `|${String(bankKey || "").trim().toLowerCase()}`;
    if (_rateAccountCache.has(key)) return _rateAccountCache.get(key);
    _rateAccountCache.set(key, "");          // do not ask twice while in flight
    (async () => {
        let res = null;
        try { res = await api().rate_for_document(kind, clientName || "", bankKey || ""); }
        catch (e) { res = null; }
        const label = (res && res.account_label) || "";
        _rateAccountCache.set(key, label);
        if (label && typeof repaint === "function") { try { repaint(); } catch (e) {} }
    })();
    return "";
}
window.rateAccountLabel = rateAccountLabel;

const PAY_FIELDS = [
    ["label", "Name", "text"],
    ["company_line", "Account holder", "text"],
    ["bank_name", "Bank", "text"],
    ["branch", "Branch", "text"],
    ["swift", "SWIFT", "text"],
    ["account_no", "Account number", "text"],
    ["iban", "IBAN", "text"],
];

let payState = { accounts: [], fallback: 1500 };

function payAccountCard(account, i) {
    const fields = PAY_FIELDS.map(([key, label]) => `
        <label class="field">
            <span>${esc(label)}</span>
            <input data-pay="${key}" data-i="${i}" type="text" value="${esc(account[key] || "")}" autocomplete="off">
        </label>`).join("");
    return `<div class="settings-card pay-account">
        <header><h4>${esc(account.label || account.key || "Account")}</h4></header>
        ${fields}
        <label class="field">
            <span>Currency</span>
            <select data-pay="currency" data-i="${i}">
                <option value="USD"${(account.currency || "USD") === "USD" ? " selected" : ""}>USD</option>
                <option value="IQD"${account.currency === "IQD" ? " selected" : ""}>IQD</option>
            </select>
        </label>
        <label class="field">
            <span>1 USD =</span>
            <input data-pay="iqd_per_usd" data-i="${i}" type="number" min="1" step="1"
                   inputmode="decimal" value="${esc(account.iqd_per_usd == null ? "" : account.iqd_per_usd)}">
            <small>IQD. Costs on a document paid into this account convert at this rate.</small>
        </label>
        <div class="field">
            <button type="button" class="ghost danger" data-pay-remove="${i}">Remove this account</button>
        </div>
    </div>`;
}

function paintPaymentAccounts() {
    const box = document.getElementById("payAccounts");
    if (!box) return;
    box.innerHTML = payState.accounts.length
        ? payState.accounts.map(payAccountCard).join("")
        : '<p class="helper subtle">No payment accounts yet. Add one.</p>';
    box.querySelectorAll("[data-pay]").forEach((el) => {
        el.oninput = el.onchange = () => {
            const account = payState.accounts[Number(el.dataset.i)];
            if (account) account[el.dataset.pay] = el.value;
        };
    });
    box.querySelectorAll("[data-pay-remove]").forEach((el) => {
        el.onclick = () => {
            payState.accounts.splice(Number(el.dataset.payRemove), 1);
            paintPaymentAccounts();
        };
    });
}

async function renderPaymentAccounts() {
    let got = null;
    try { got = await api().get_doc_settings("invoice"); }
    catch (e) { got = null; }
    const settings = (got && got.settings) || {};
    payState.accounts = (settings.bank_info || []).map(b => ({ ...b }));
    payState.fallback = Number(settings.fallback_iqd_per_usd) || 1500;
    const fb = document.getElementById("payFallbackRate");
    if (fb) fb.value = payState.fallback;
    paintPaymentAccounts();

    const add = document.getElementById("payAddAccount");
    if (add) add.onclick = () => {
        const n = payState.accounts.length + 1;
        payState.accounts.push({ key: `account-${n}`, label: `Account ${n}`,
                                 currency: "USD", iqd_per_usd: payState.fallback });
        paintPaymentAccounts();
    };

    const save = document.getElementById("payAccountsSave");
    if (save) save.onclick = safe(savePaymentAccounts, "Save payment accounts");
}

async function savePaymentAccounts() {
    const fb = document.getElementById("payFallbackRate");
    const fallback = Number(fb && fb.value);
    // Zero and negative are not exchange rates. Storing one puts a silently
    // wrong divisor behind every profit figure for that client, so the save is
    // refused and the account is named.
    if (!(fallback > 0)) {
        toast("Give the fallback a rate above zero", true);
        return;
    }
    for (const account of payState.accounts) {
        if (!(Number(account.iqd_per_usd) > 0)) {
            toast(`${account.label || account.key} needs an exchange rate above zero`, true);
            return;
        }
    }
    const payload = {
        bank_info: payState.accounts.map(a => ({ ...a, iqd_per_usd: Number(a.iqd_per_usd) })),
        fallback_iqd_per_usd: fallback,
    };
    let res = null;
    try { res = await api().save_doc_settings("invoice", payload); }
    catch (e) { res = null; }
    if (!res || res.ok === false) {
        toast("Could not save: " + ((res && res.error) || "unknown"), true);
        return;
    }
    toast("Payment accounts saved");
}

// Wire the About & Maintenance quick-access buttons.
function wireAboutPanel() {
    const openFolder = document.getElementById("btnAboutOpenFolder");
    if (openFolder) openFolder.onclick = safe(async () => {
        const root = document.getElementById("set_documents_root");
        const path = root && root.value.trim();
        if (!path) { toast("No documents folder set", true); return; }
        try { await api().open_path(path); }
        catch (e) { toast("Couldn't open the folder", true); }
    }, "Open folder");

    const dm = document.getElementById("btnAboutDataManager");
    if (dm) dm.onclick = safe(() => {
        if (window.showDataManager) window.showDataManager("catalogs");
        else toast("Data Manager not loaded", true);
    }, "Data Manager");

    const backup = document.getElementById("btnAboutBackup");
    if (backup) backup.onclick = safe(async () => {
        if (!window.showDataManager) { toast("Data Manager not loaded", true); return; }
        await window.showDataManager("catalogs");
        const tab = document.querySelector(".dm-tab[data-dm-tab='backup']");
        if (tab) tab.click();
    }, "Backup");

}

// Wire the Behaviour panel - user preferences persisted to settings.json.
// Each control persists on change and applies immediately.
function wireBehaviourPanel() {
    const afterSel = document.getElementById("set_after_export");
    if (afterSel) {
        afterSel.value = window.prefGet("after_export", "copy");
        afterSel.onchange = () => window.prefSet("after_export", afterSel.value);
    }
    const compact = document.getElementById("set_compact_tables");
    if (compact) {
        compact.checked = window.prefBool("compact_tables", false);
        compact.onchange = () => {
            window.prefSet("compact_tables", compact.checked ? "1" : "0");
            applyCompactTables();
        };
    }
    const dnPrices = document.getElementById("set_dn_prices_default");
    if (dnPrices) {
        dnPrices.checked = window.prefBool("dn_prices_default", false);
        dnPrices.onchange = () => window.prefSet("dn_prices_default", dnPrices.checked ? "1" : "0");
    }
}

// Load the saved header/closing/address for the selected doc type.
async function loadHdrEditor() {
    const sel = document.getElementById("hdrDocType");
    if (!sel) return;
    let s = {};
    try { const res = await api().get_doc_settings(sel.value); s = (res && res.settings) || {}; }
    catch { s = {}; }
    const c = s.company || {};
    const setv = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
    setv("hdrNameLines", (c.name_lines || []).join("\n"));
    setv("hdrTitle", c.title || "");
    setv("hdrClosing", c.closing || "");
    setv("hdrAddrs", (s.address_options || []).join("\n"));
    const brand = (s.brand_color || "#1F3864").toUpperCase();
    setv("hdrBrandHex", brand);
    const cEl = document.getElementById("hdrBrand");
    if (cEl && /^#[0-9A-Fa-f]{6}$/.test(brand)) cEl.value = brand;
}

// Save header/closing/address for the selected doc type. Re-reads the
// existing section first and MERGES into company so the signature fields
// (still owned by the per-type modal) are preserved.
async function saveHdrEditor() {
    const sel = document.getElementById("hdrDocType");
    if (!sel) return;
    const docType = sel.value;
    let existing = {};
    try { const res = await api().get_doc_settings(docType); existing = (res && res.settings) || {}; }
    catch { existing = {}; }
    const c = { ...(existing.company || {}) };
    const split = (id) => (document.getElementById(id).value || "")
        .split(/\r?\n/).map(x => x.trim()).filter(Boolean);

    const nameLines = split("hdrNameLines");
    if (nameLines.length) c.name_lines = nameLines; else delete c.name_lines;
    const title = document.getElementById("hdrTitle").value.trim();
    if (title) c.title = title; else delete c.title;
    const closing = document.getElementById("hdrClosing").value.trim();
    if (closing) c.closing = closing; else delete c.closing;

    const addrs = split("hdrAddrs");
    const hex = (document.getElementById("hdrBrandHex").value || "").trim();

    const payload = { company: c, address_options: addrs };
    if (/^#[0-9A-Fa-f]{6}$/.test(hex)) payload.brand_color = hex.toUpperCase();

    const r = await api().save_doc_settings(docType, payload);
    if (!r || !r.ok) { toast("Save failed: " + ((r && r.error) || "unknown"), true); return; }
    toast("Header settings saved");
}

function populateSettingsForm(s) {
    syncUserPreferences((s && s.preferences) || {});
    syncDocAutofitDefaults(s || {});
    applyTheme(window.prefGet("theme", "light"), { persist: false });
    document.getElementById("set_documents_root").value = s.documents_root || "";
    document.getElementById("set_po_root").value        = s.po_root || "";
    document.getElementById("set_ui_lang").value        = s.ui_lang || "en";
    // Bust the cache so an updated logo shows up immediately.
    const preview = document.getElementById("set_logo_preview");
    if (preview) preview.src = s.logo_data_uri || "";
}

function collectSettingsForm() {
    return {
        documents_root: document.getElementById("set_documents_root").value.trim(),
        po_root:        document.getElementById("set_po_root").value.trim(),
        ui_lang:        document.getElementById("set_ui_lang").value,
        preferences:    currentUserPreferences(),
    };
}

async function saveSettings() {
    // Phase L.2 - api.save_settings now does a shallow merge backend-side,
    // so we just send the keys this form actually owns. Untouched sections
    // (quotation, delivery, invoice, signing) are preserved on the
    // backend without us spreading them here. Sending "" for any key pops
    // its override.
    const partial = collectSettingsForm();
    const r = await api().save_settings(partial);
    if (!r.ok) { toast("Save error: " + r.error, true); return; }
    toast("Settings saved");
    showHub();
}

async function resetSettings() {
    const choice = await showModal({
        title: "Restore defaults",
        body: "This wipes data/settings.json and restores every setting (universal + per-doctype) to program defaults. Continue?",
        buttons: [
            { label: "Cancel",  value: "cancel", cls: "ghost" },
            { label: "Restore", value: "ok",     cls: "primary" },
        ],
    });
    if (choice !== "ok") return;
    const r = await api().reset_settings();
    if (!r.ok) { toast("Reset error: " + r.error, true); return; }
    toast("Defaults restored");
    const s = await api().get_settings();
    populateSettingsForm(s);
}

// "Upload new logo" button inside universal settings.
async function uploadLogoFromSettings() {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "image/png,image/jpeg";
    inp.onchange = async () => {
        const f = inp.files && inp.files[0];
        if (!f) return;
        const url = await new Promise((res, rej) => {
            const r = new FileReader();
            r.onload = () => res(r.result); r.onerror = rej;
            r.readAsDataURL(f);
        });
        const r = await api().upload_logo(url);
        if (!r.ok) { toast("Upload failed: " + (r.error || "unknown"), true); return; }
        toast("Logo updated");
        const preview = document.getElementById("set_logo_preview");
        if (preview) preview.src = r.logo_data_uri || url;
    };
    inp.click();
}

// Stub kept for any leftover handler reference; the new universal panel has
// no color picker.

// ============================================================
// Editor rows - incremental rendering (no focus loss)
// ============================================================
function addRow(data = {}) {
    const r = {
        name: data.name || "",
        description: data.description || "",
        unit: data.unit || "",
        qty: data.qty || "",
        company: data.company || "",
        image: data.image || "",          // 2026-08-05 - optional row picture
    };
    state.rows.push(r);
    appendRowDom(state.rows.length - 1);
    updateSplitWarning();
    updateRowCount();
}

function appendRowDom(i) {
    const tbody = document.getElementById("rowsBody");
    const tr = buildRowTr(i);
    tbody.appendChild(tr);
}

// Inline SVG icons (Lucide family). Returning markup strings makes them
// easy to drop into innerHTML.
const ICON = {
    // Privacy toggle (2026-08-25) - hides the COST/PROFIT strip + profit cards.
    eye: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
    eyeOff: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`,

    trash: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
    externalLink: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
    pencil: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>`,
    copy: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
    // Phase M (items 11, 11.1) - Print + Delete actions on Open lists.
    printer: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>`,
    // Phase M.5b - Folder icon (Open folder location action) + clipboard icon
    // (grid-paste context menu).
    folder: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
    clipboard: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>`,
    /* Duplicate (2026-08-04) - copy-PLUS, deliberately NOT `copy`. The two sit
       side by side in every panel row and mean different things: `copy` puts
       the PDF on the clipboard, `duplicate` starts a new document. Two near
       identical glyphs next to each other would be a coin toss. */
    duplicate: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/><line x1="15.5" y1="12.5" x2="15.5" y2="19.5"/><line x1="12" y1="16" x2="19" y2="16"/></svg>`,
};
window.ICON = ICON;   // expose for quote.js / inv.js / dn.js

/* Keys a suggestion carries that describe the ITEM rather than supply a value
   for a column. Two things must skip them: the grey subtitle in the dropdown,
   and the loop that writes a picked suggestion into the row.

   `name_ar`/`name_en` (2026-08-16) are the item's other-language name - one
   product, two names, since a PO is written in Arabic and the delivery note
   that fulfils it in English. Printed blindly they put "خيار" next to
   `Cucumber` in a quotation, which reads as the dropdown having gone Arabic.
   `img_query` is the wording that finds the item's picture. The money pairs
   and `cost_usd` are Master Data storage values: each editor converts them
   into its own Rate/Cost cells explicitly, so the generic copy loop must not
   save them as phantom document columns. `item_no` identifies the record; it
   is not a document column either. */
const ITEM_META_KEYS = new Set([
    "cost_usd", "cost_native", "cost_native_ccy",
    "price_native", "price_native_ccy", "item_no",
    "name_ar", "name_en", "img_query",
]);
window.ITEM_META_KEYS = ITEM_META_KEYS;

/**
 * Phase M (items 11) - confirm-and-delete helper shared by every Open list.
 * Asks the user to confirm, calls the soft-delete API, toasts the result,
 * and runs `onSuccess` so the caller can refresh its list.
 */
async function confirmAndDeleteFile(path, label, onSuccess) {
    if (!path) return;
    const choice = await showModal({
        title: `Delete ${label || "file"}?`,
        body:  `The file will be moved to the RECYCLE folder under your documents root. It auto-purges after 30 days. The database record is untouched - re-open the editor for that document to re-export.`,
        buttons: [
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: "Delete", value: "ok",     cls: "primary danger-text" },
        ],
    });
    if (choice !== "ok") return;
    const r = await api().soft_delete_file(path);
    if (!r.ok) { toast("Delete failed: " + (r.error || ""), true); return; }
    toast(`Moved to RECYCLE`);
    if (typeof onSuccess === "function") onSuccess();
}
window.confirmAndDeleteFile = confirmAndDeleteFile;

/**
 * Delete a saved document from the active app records and move its PDF to
 * RECYCLE. Use this for editor Open-list deletes so Costing, To-Do, submitted
 * flags, custom columns, and per-document layout/signing state do not keep a
 * ghost record alive after the visible PDF disappears.
 */
async function confirmAndDeleteDocument(kind, docNo, path, label, onSuccess) {
    docNo = (docNo || label || "").trim();
    if (!docNo) return;
    const choice = await showModal({
        title: `Delete ${label || docNo}?`,
        body:  `The PDF will be moved to the RECYCLE folder and the saved document record will be removed from the app, including Costing, To-Do, submitted status, and document-specific settings.`,
        buttons: [
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: "Delete", value: "ok",     cls: "primary danger-text" },
        ],
    });
    if (choice !== "ok") return;
    const r = await api().delete_document(kind, docNo, path || "");
    if (!r.ok) { toast("Delete failed: " + (r.error || ""), true); return; }
    toast((r.file_deleted || r.recycle_path) ? "Moved to RECYCLE and removed from app records"
                                             : "Removed from app records");
    if (typeof onSuccess === "function") onSuccess();
}
window.confirmAndDeleteDocument = confirmAndDeleteDocument;

/* ---------- Duplicate a document (2026-08-04) -------------------------------
   Right-panel action on all five document types. It opens the source in its
   editor as a NEW, UNSAVED document: next number, today's date, everything
   else pre-filled. Nothing is written and no number is consumed until Save.

   Why it loads through the editor's own `load*ForEdit` and then clears the
   identity, rather than building a fresh document from the record: the loaders
   already know how to restore rows, column widths, autofit layout, terms,
   payment bank and the DN price flag. A second code path
   for all of that is where fields go missing silently.

   The trade is that CLEARING is now the risky half, so each editor owns its own
   `*MakeDuplicate` hook (it knows its state object) and the clear-list is
   pinned by tests/duplicate_document.test.js. A missed COPY leaves a visibly
   blank field; a missed CLEAR ships a duplicate that claims to be signed,
   submitted or paid. */
const DUPLICATE_HOOK = {
    po:    { loader: "loadForEdit",         hook: "poMakeDuplicate",    label: "Purchase Order" },
    quote: { loader: "loadQuoteForEdit",    hook: "quoteMakeDuplicate", label: "Quotation" },
    dn:    { loader: "loadDnForEdit",       hook: "dnMakeDuplicate",    label: "Delivery Note" },
    inv:   { loader: "loadInvForEdit",      hook: "invMakeDuplicate",   label: "Invoice" },
};

async function duplicateDocument(kind, docNo) {
    const spec = DUPLICATE_HOOK[kind];
    if (!spec) { toast("Cannot duplicate this document type", true); return false; }
    docNo = (docNo || "").trim();
    if (!docNo) return false;

    // Loading the source replaces whatever is in the editor, so ask first -
    // the same three-way prompt the profile switcher uses.
    const dirtyEditor = activeDirtyEditor();
    if (dirtyEditor) {
        const choice = await showModal({
            title: "Unsaved document",
            body: `Save the current ${dirtyEditor.kind} before duplicating ${docNo}?`,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Discard", value: "discard", cls: "warm-red" },
                { label: "Save", value: "save", cls: "primary" },
            ],
        });
        if (choice === "cancel" || !choice) return false;
        if (choice === "save" && !(await dirtyEditor.save())) return false;
    }

    const loader = window[spec.loader];
    const hook = window[spec.hook];
    if (typeof loader !== "function" || typeof hook !== "function") {
        toast("Cannot duplicate - the editor is not loaded", true);
        return false;
    }

    await loader(docNo);
    // The loader bails (and toasts) when the record is missing; in that case the
    // editor still holds whatever it had, so don't convert it into a duplicate.
    if (!window.__currentEditorDoc || window.__currentEditorDoc.no !== docNo) return false;

    const newNo = await hook();
    if (newNo) {
        // The duplicate is dated today, so it belongs in today's month list.
        const t = todayInfo();
        setPendingDoc({ kind, no: newNo, sourceNo: docNo, year: t.year, month: t.month });
        await showPendingInPanel(kind, t.year, t.month);
    }
    toast(newNo ? `Copy of ${docNo} - saved as ${newNo} when you press Save`
                : `Copy of ${docNo} - not saved yet`);
    return true;
}

/* Point the pane at the pending document's month and re-render, so the
   provisional row is actually on screen. Without the month switch a duplicate
   made while browsing an older month would be filtered out by pendingRowEl and
   look like nothing happened. */
const PANEL_MONTH_INPUTS = {
    po:    { year: "openYear",    month: "openMonth",    refresh: "refreshPoList" },
    quote: { year: "qOpenYear",   month: "qOpenMonth",   refresh: "refreshQuoteList" },
    dn:    { year: "dnOpenYear",  month: "dnOpenMonth",  refresh: "refreshDnList" },
    inv:   { year: "invOpenYear", month: "invOpenMonth", refresh: "refreshInvList" },
};

async function showPendingInPanel(kind, year, month) {
    const spec = PANEL_MONTH_INPUTS[kind];
    if (!spec) return;
    const yEl = document.getElementById(spec.year);
    const mEl = document.getElementById(spec.month);
    if (yEl) yEl.value = String(year);
    if (mEl) mEl.value = String(month);
    const refresh = window[spec.refresh];
    if (typeof refresh === "function") { try { await refresh(); } catch (e) {} }
}
window.showPendingInPanel = showPendingInPanel;

/* Point the panel at an OPENED document's own month, then scroll to its row
   (2026-10-01).

   Waleed clicked "Open document" on a July document from Needs Attention in
   September. The editor loaded it correctly and the panel stayed on September,
   so the document he was editing was nowhere on the list beside it and he had
   to go hunting. Every loader calls this at the end, so it works from Needs
   Attention, dashboard search, Master Data ▸ item usage and anywhere else that
   opens a document by number - not just from the one button he reported.

   Deliberately NOT expanding a collapsed panel: collapsing it is a choice the
   user made and this is not important enough to overrule it. The row is
   highlighted and scrolled anyway, so it is already in place when they open it.

   `dateLike` is either the display date (d/m/Y, what every editor's state
   holds) or an ISO date. A date we cannot read means we leave the month alone
   and still try to highlight - a wrong month switch would be worse than none. */
async function revealDocInPanel(kind, no, dateLike) {
    try {
        const spec = PANEL_MONTH_INPUTS[kind];
        if (!spec || !no) return;
        const iso = displayToIso(dateLike) || (/^\d{4}-\d{2}-\d{2}/.test(String(dateLike || ""))
            ? String(dateLike).slice(0, 10) : "");
        if (iso) {
            const y = +iso.slice(0, 4);
            const m = +iso.slice(5, 7);
            const yEl = document.getElementById(spec.year);
            const mEl = document.getElementById(spec.month);
            // Only reload when the panel is actually on another month. Pressing
            // a row in the panel itself must not re-fetch the list underneath
            // the click.
            const onIt = yEl && mEl && +yEl.value === y && +mEl.value === m;
            if (!onIt) await showPendingInPanel(kind, y, m);
        }
        if (window.OpenPanel && window.OpenPanel.revealRow) window.OpenPanel.revealRow(no);
    } catch (e) { /* the editor has already loaded; never fail the open over a scroll */ }
}
window.revealDocInPanel = revealDocInPanel;

/* The other half of the same idea: a FRESH document is in the editor.

   Two things follow, and all four editors need both, so they live here rather
   than three lines deep in each one (2026-10-01):
     1. nothing in the panel is "the open document" any more - clearing
        `__currentEditorDoc` now also clears the panel's last-clicked latch
        ([[static/open_panel.js]] clearActivePanelRow), so the document you were
        just looking at does not stay highlighted beside an empty editor,
     2. a new document is dated TODAY, so the panel should be looking at
        today's month. Without this it stayed wherever the last OPENED document
        left it - press New after opening a July order and the editor holds a
        September document while the list beside it shows July, which is the
        same mismatch the open-document jump was added to fix. */
async function panelNewDocument(kind) {
    window.__currentEditorDoc = null;
    const t = window.state && state.today;
    if (t && PANEL_MONTH_INPUTS[kind]) {
        try { await showPendingInPanel(kind, t.year, t.month); return; } catch (e) {}
    }
    if (window.refreshOpenPanel) window.refreshOpenPanel();
}
window.panelNewDocument = panelNewDocument;
window.duplicateDocument = duplicateDocument;

/* ---------- Row images, shared by the Quotation and PO editors -------------
   Lifted out of quote.js (2026-08-05) so the PO could have the same image
   column without a second copy of the pick / paste / drop / crop pipeline.
   Everything here is target-agnostic: the caller supplies `get`/`set`, so the
   same code serves quoteState.rows and the PO's state.rows.

   The `qc-img-*` class names are deliberately kept - the CSS already exists
   and both tables should look identical, which is the whole point.

   Stored value is a JPEG data URI. It rides inside the row object, so it
   saves, loads, duplicates and imports with every other field for free. */
const ROW_IMG_MAX_W = 900;      // cap the cropped output so a data URI cannot
const ROW_IMG_MAX_H = 700;      // bloat rows_json without limit
const ROW_IMG_QUALITY = 0.85;

function rowImageCellHTML(value) {
    const has = !!value;
    return `<div class="qc-img-wrap">
        <img class="qc-img-thumb" alt="" src="${esc(value || "")}" ${has ? "" : "hidden"}>
        <div class="qc-img-empty" ${has ? "hidden" : ""} title="Click to select cell · Ctrl+V to paste · double-click to browse · drop an image">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
            <span>Paste · Drop · 2×Click</span>
        </div>
        <button type="button" class="qc-img-clear" title="Remove" ${has ? "" : "hidden"}>×</button>
    </div>`;
}
window.rowImageCellHTML = rowImageCellHTML;

/* Turn remembered picture REFERENCES back into data URIs. The bytes are kept
   out of the shared library on purpose (backend/item_images), so this is a
   bridge round-trip per picture and therefore async - callers paint the row
   first and let the image land a moment later.

   Guarded on the bridge method because F5 without restarting the app pairs new
   JS with old Python; an unguarded call would throw mid-pick and abandon the
   rest of the autofill. Returns {columnKey: dataURI} for whatever resolved. */
async function fetchRememberedImages(refs) {
    const out = {};
    if (!refs || typeof refs !== "object") return out;
    let getImage;
    try { getImage = api().get_item_image; } catch (e) { return out; }
    if (typeof getImage !== "function") return out;
    for (const [key, ref] of Object.entries(refs)) {
        if (!ref) continue;
        try {
            const uri = await api().get_item_image(ref);
            if (uri) out[key] = uri;
        } catch (e) { /* deleted from disk -> no picture, not an error */ }
    }
    return out;
}
window.fetchRememberedImages = fetchRememberedImages;

/* Paint a fetched picture into an already-rendered `.qc-img-cell`. The PO and
   the quotation share the cell markup, so they share this. */
function paintRowImageCell(tr, key, uri) {
    const cell = tr && tr.querySelector(`.qc-img-cell[data-k="${key}"]`);
    if (!cell) return;
    const thumb = cell.querySelector(".qc-img-thumb");
    const empty = cell.querySelector(".qc-img-empty");
    const clear = cell.querySelector(".qc-img-clear");
    if (!thumb || !empty || !clear) return;
    thumb.src = uri; thumb.hidden = false;
    empty.hidden = true; clear.hidden = false;
}
window.paintRowImageCell = paintRowImageCell;

/* ---------------------------------------------------------------------------
   "Find pictures" - the button in an image column's header.

   Looks each row's item up online (backend/image_search) and drops a reference
   photo into the EMPTY cells. Three rules, all of them about not making things
   worse:

     * A cell that already has a picture is never touched. You put it there.
     * An item the search cannot match confidently is LEFT EMPTY and counted,
       not filled with the closest thing. A wrong photo in a filled cell is
       never looked at again; an empty cell is.
     * Rows are fetched one at a time so the editor stays usable and the count
       climbs as it goes. Cancelling stops it where it is - what has already
       been filled stays.

   Shared by the quotation and PO editors, which already share the cell markup.
   --------------------------------------------------------------------------- */
function imgFetchButtonHTML(colKey, label, title) {
    return `<button type="button" class="qc-head-find" data-find-img="${esc(colKey)}"
        title="${esc(title || "Find a reference picture online for each row that has no image yet")}"
        tabindex="-1">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>
        </svg>${esc(label || "Find")}</button>`;
}
window.imgFetchButtonHTML = imgFetchButtonHTML;

let _imgFetchRunning = false;

/* `ctx` = { rows, key, nameOf, tbody, get, set }. The editors differ on where
   the name lives and how a cell is written, and on nothing else. */
async function fillImagesFromWeb(ctx) {
    if (_imgFetchRunning) { toast("Already looking…"); return; }
    const targets = [];
    (ctx.rows || []).forEach((row, i) => {
        if (!row) return;
        const name = String(ctx.nameOf(row) || "").trim();
        if (!name) return;
        if (String(ctx.get(row) || "").trim()) return;   // never overwrite
        targets.push({ i, row, name, desc: String(row.description || "").trim() });
    });
    if (!targets.length) {
        toast("Every row with an item already has a picture");
        return;
    }

    /* A progress dialog rather than a busy button: this takes ~1s per row and
       it matters WHICH rows failed. The log names every item as it resolves,
       so a run of 15 leaves you knowing exactly what to go and photograph. */
    const bd = document.getElementById("imgFetchBackdrop");
    const bar = document.getElementById("imgFetchBar");
    const nowEl = document.getElementById("imgFetchNow");
    const cntEl = document.getElementById("imgFetchCount");
    const tallyEl = document.getElementById("imgFetchTally");
    const logEl = document.getElementById("imgFetchLog");
    const cancelBtn = document.getElementById("imgFetchCancel");
    const closeBtn = document.getElementById("imgFetchClose");
    if (!bd) { toast("Progress dialog missing", true); return; }

    let cancelled = false;
    const total = targets.length;
    let filled = 0, missed = 0;
    const misses = [];

    logEl.innerHTML = "";
    bar.style.width = "0%";
    bar.parentElement.setAttribute("aria-valuenow", "0");
    bar.parentElement.setAttribute("aria-valuemax", String(total));
    nowEl.textContent = "Starting…";
    cntEl.textContent = `0 of ${total}`;
    tallyEl.textContent = "";
    cancelBtn.hidden = false;
    closeBtn.hidden = true;
    cancelBtn.disabled = false;
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = () => {
        cancelled = true;
        cancelBtn.disabled = true;
        cancelBtn.textContent = "Stopping…";
        nowEl.textContent = "Finishing the current item…";
    };
    /* Shared behaviour, markup in index.html ([[14-UI-CONTRACTS]]). It is
       NOT dismissible: a run is in flight, and Esc or a stray click on the dim
       background would hide the log the run exists to produce. Cancel stops it;
       Close appears when it is over. */
    const _imgPopup = window.Popup.mount(bd, { dismissible: false });
    _imgFetchRunning = true;

    const setLine = (line, state, name, detail, withFix) => {
        line.className = "imgf-line imgf-" + state;
        // The state is a WORD, never a colour alone.
        line.innerHTML = `<span class="imgf-state">${esc(state)}</span>`
            + `<span class="imgf-item">${esc(name)}</span>`
            + `<span class="imgf-detail">${esc(detail || "")}</span>`
            + (withFix
                ? `<button type="button" class="imgf-fix">Search with my words</button>`
                : "");
    };

    /* Re-run ONE row with wording the user typed, and remember that wording
       against the item. This is the whole answer to the last stubborn 20%:
       the search cannot know that "خيار" is a cucumber and not a stock option,
       or that "Bleach" is a cleaning product and not a cartoon. The user says
       so once and it holds for every future document, because the phrase is
       stored in the shared item memory rather than on this row. */
    const runFix = async (line, t, phrase) => {
        setLine(line, "working", t.name, `searching for "${phrase}"…`, false);
        let r = null;
        try { r = await api().find_item_image(t.name, t.desc, phrase); }
        catch (e) { r = null; }
        if (r && r.ok && r.data_uri) {
            ctx.set(t.row, r.data_uri);
            const tr = document.querySelector(`#${ctx.tbody} tr[data-i="${t.i}"]`);
            if (tr && window.paintRowImageCell) {
                window.paintRowImageCell(tr, ctx.key, r.data_uri);
            }
            if (!t.done) { filled++; missed = Math.max(0, missed - 1); t.done = true; }
            try { await api().remember_item_search_phrase(t.name, phrase, t.desc); }
            catch (e) { /* the picture is in; remembering is a bonus */ }
            setLine(line, "found", t.name,
                    `${r.title || phrase} · remembered for next time`, false);
        } else {
            const why = (r && r.error) || "still nothing";
            setLine(line, "skipped", t.name, why, true);
            wireFix(line, t);
        }
        tallyEl.textContent = `${filled} found · ${missed} not found`;
    };

    const wireFix = (line, t) => {
        if (!line || typeof line.querySelector !== "function") return;
        const btn = line.querySelector(".imgf-fix");
        if (!btn) return;
        btn.onclick = () => {
            if (line.querySelector(".imgf-fixbox")) return;
            const box = document.createElement("div");
            box.className = "imgf-fixbox";
            box.innerHTML =
                `<label class="imgf-fixlabel" for="imgfFix${t.i}">Search words</label>`
                + `<input class="imgf-fixinput" id="imgfFix${t.i}" type="text"`
                + ` value="${esc(t.name)}" autocomplete="off">`
                + `<button type="button" class="imgf-fixgo">Search</button>`;
            line.appendChild(box);
            const input = box.querySelector(".imgf-fixinput");
            const go = box.querySelector(".imgf-fixgo");
            const fire = () => {
                const phrase = String(input.value || "").trim();
                if (phrase) runFix(line, t, phrase);
            };
            if (go) go.onclick = fire;
            if (input) {
                input.onkeydown = (e) => {
                    if (e.key === "Enter") { e.preventDefault(); fire(); }
                };
                if (typeof input.focus === "function") input.focus();
                if (typeof input.select === "function") input.select();
            }
        };
    };

    const logLine = (state, name, detail, t) => {
        const row = document.createElement("div");
        setLine(row, state, name, detail, !!t);
        logEl.appendChild(row);
        if (t) wireFix(row, t);
        logEl.scrollTop = logEl.scrollHeight;
        return row;
    };

    try {
        for (let n = 0; n < total; n++) {
            if (cancelled) break;
            const t = targets[n];
            nowEl.textContent = t.name + (t.desc ? ` - ${t.desc}` : "");
            cntEl.textContent = `${n + 1} of ${total}`;
            bar.style.width = ((n / total) * 100).toFixed(1) + "%";
            bar.parentElement.setAttribute("aria-valuenow", String(n));

            let r = null;
            try { r = await api().find_item_image(t.name, t.desc); }
            catch (e) { r = null; }

            if (r && r.ok && r.data_uri) {
                ctx.set(t.row, r.data_uri);
                const tr = document.querySelector(`#${ctx.tbody} tr[data-i="${t.i}"]`);
                if (tr && window.paintRowImageCell) {
                    window.paintRowImageCell(tr, ctx.key, r.data_uri);
                }
                filled++;
                t.done = true;
                let detail;
                if (r.from_memory) {
                    detail = "your saved picture for this item";
                } else {
                    detail = `${r.title || ""}${r.source ? " · " + r.source : ""}`;
                    if (r.via_translation && r.english) {
                        detail += ` · searched "${r.english}"`;
                    }
                }
                // Even a hit gets the Fix control: the search is confident and
                // wrong often enough that "found" is not the same as "right",
                // and this log is the only place the user sees WHAT was
                // inserted before it is saved into a document.
                logLine("found", t.name, detail, t);
            } else {
                missed++;
                if (misses.length < 6) misses.push(t.name);
                const why = (r && r.weak)
                    ? `nothing convincing${r.best_guess ? ` (closest: ${r.best_guess})` : ""}`
                    : ((r && r.error) || "no result");
                logLine("skipped", t.name, why, t);
            }
            tallyEl.textContent = `${filled} found · ${missed} not found`;
        }
    } finally {
        _imgFetchRunning = false;
        bar.style.width = "100%";
        bar.parentElement.setAttribute("aria-valuenow", String(total));
        nowEl.textContent = cancelled ? "Stopped" : "Finished";
        cancelBtn.hidden = true;
        closeBtn.hidden = false;
        closeBtn.onclick = () => _imgPopup.close(null);
        closeBtn.focus();
    }

    let msg = `${filled} picture${filled === 1 ? "" : "s"} added`;
    if (cancelled) msg = "Stopped - " + msg;
    if (missed) {
        msg += ` · ${missed} not found (${misses.join(", ")}${missed > misses.length ? "…" : ""})`;
    }
    toast(msg, filled === 0);
}
window.fillImagesFromWeb = fillImagesFromWeb;

/* The PO editor's header is static markup in index.html, so its Find button is
   wired once at start-up rather than on every re-render. */
function _wirePoFindImages() {
    // Defensive: this runs at load, and the focused frontend test harnesses
    // provide a minimal `document` without querySelectorAll.
    if (!document || typeof document.querySelectorAll !== "function") return;
    document.querySelectorAll('#editorTable [data-find-img]').forEach(b => {
        b.onclick = () => window.fillImagesFromWeb({
            rows: state.rows,
            key: b.dataset.findImg,
            tbody: "rowsBody",
            nameOf: (row) => row.name,
            get: (row) => row[b.dataset.findImg],
            set: (row, uri) => {
                row[b.dataset.findImg] = uri;
                state.dirtySinceSave = true;
            },
        });
    });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wirePoFindImages);
} else {
    _wirePoFindImages();
}



function readFileToDataURL(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}
window.readFileToDataURL = readFileToDataURL;

/* ONE persistent hidden <input type=file> for the whole session. The original
   implementation removed the input on a 1s timer, but the OS file dialog takes
   longer than that, and once detached WebView2 never delivered the change
   event - picking a file silently did nothing. Do not make this per-call. */
let _rowImgInput = null;
let _rowImgApply = null;
function _rowImagePicker() {
    if (_rowImgInput) return _rowImgInput;
    const inp = document.createElement("input");
    inp.type = "file";
    inp.accept = "image/png,image/jpeg,image/webp,image/gif,image/bmp";
    inp.style.display = "none";
    inp.id = "rowImagePickerInput";
    inp.addEventListener("change", () => {
        const f = inp.files && inp.files[0];
        const apply = _rowImgApply;
        inp.value = "";                      // so the same file can be re-picked
        if (!f || !apply) return;
        readFileToDataURL(f).then(url => openRowImageCropper(url, apply));
    });
    document.body.appendChild(inp);
    _rowImgInput = inp;
    return inp;
}

function pickRowImage(apply) { _rowImgApply = apply; _rowImagePicker().click(); }

function pasteRowImage(e, apply) {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
        if (it.type && it.type.indexOf("image/") === 0) {
            const f = it.getAsFile();
            if (f) { e.preventDefault(); readFileToDataURL(f).then(url => openRowImageCropper(url, apply)); return; }
        }
    }
}

function openRowImageCropper(dataUrl, apply) {
    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). Reset acts WITHOUT
       closing (`keep`), which is the whole point of it; the cropper instance is
       destroyed from `onClose`, so it is released however the popup is left -
       Esc and the dim background included, which used to leak it. */
    let cropper = null;
    const h = window.Popup.open({
        title: "Crop image",
        subtitle: "Drag the corners to crop. The cropped image will fit the table cell.",
        size: "lg",
        dir: "ltr",
        className: "crop-modal",
        body: `<div class="crop-stage"><img id="cropImg" alt=""></div>`,
        html: true,
        actions: [
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: "Reset", value: "reset", cls: "secondary", keep: true,
              onClick: () => { if (cropper) cropper.reset(); } },
            { label: "Use this crop", value: "apply", cls: "primary", keep: true },
        ],
        onClose: () => { if (cropper) { cropper.destroy(); cropper = null; } },
    });
    const bd = h.panel;
    const close = () => h.close(null);

    const img = bd.querySelector("#cropImg");
    img.src = dataUrl;
    img.onload = () => {
        cropper = new Cropper(img, { viewMode: 1, autoCropArea: 1, background: false });
    };
    bd.querySelector("[data-value='apply']").onclick = () => {
        if (!cropper) { close(); return; }
        const canvas = cropper.getCroppedCanvas({
            maxWidth: ROW_IMG_MAX_W, maxHeight: ROW_IMG_MAX_H, imageSmoothingQuality: "high",
        });
        apply(canvas.toDataURL("image/jpeg", ROW_IMG_QUALITY));
        close();
    };
}
window.openRowImageCropper = openRowImageCropper;

/* Wire one `.qc-img-cell`. `io.get()` returns the stored data URI, `io.set(v)`
   stores it (and marks the editor dirty); this owns every DOM update, so the
   two editors cannot drift on what a filled vs empty cell looks like. */
function wireRowImageCell(cell, io) {
    const empty = cell.querySelector(".qc-img-empty");
    const thumb = cell.querySelector(".qc-img-thumb");
    const clear = cell.querySelector(".qc-img-clear");
    if (!empty || !thumb || !clear) return;

    const paint = (v) => {
        if (v) { thumb.src = v; thumb.hidden = false; empty.hidden = true;  clear.hidden = false; }
        else   { thumb.removeAttribute("src"); thumb.hidden = true; empty.hidden = false; clear.hidden = true; }
    };
    const apply = (v) => { io.set(v); paint(v); };

    // Single click FOCUSES (so Ctrl+V lands here); double click opens the file
    // picker. They were once both on single click, and the file dialog stealing
    // focus made paste impossible.
    const focusCell = (e) => { e.stopPropagation(); cell.focus(); };
    empty.addEventListener("click", focusCell);
    thumb.addEventListener("click", focusCell);
    empty.addEventListener("dblclick", (e) => { e.stopPropagation(); pickRowImage(apply); });
    thumb.addEventListener("dblclick", (e) => { e.stopPropagation(); pickRowImage(apply); });
    clear.addEventListener("click", (e) => { e.stopPropagation(); apply(""); });

    cell.tabIndex = 0;                       // focusable, so paste works
    cell.addEventListener("paste", (e) => pasteRowImage(e, apply));
    cell.addEventListener("dragover", (e) => {
        const items = (e.dataTransfer && e.dataTransfer.items) || [];
        for (const it of items) {
            if (it.kind === "file" && it.type && it.type.indexOf("image/") === 0) {
                e.preventDefault(); cell.classList.add("qc-img-dragover"); return;
            }
        }
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("qc-img-dragover"));
    cell.addEventListener("drop", (e) => {
        cell.classList.remove("qc-img-dragover");
        const files = (e.dataTransfer && e.dataTransfer.files) || [];
        for (const f of files) {
            if (f.type && f.type.indexOf("image/") === 0) {
                e.preventDefault();
                readFileToDataURL(f).then(url => openRowImageCropper(url, apply));
                return;
            }
        }
    });
}
window.wireRowImageCell = wireRowImageCell;

/* ---------- Live cost / sell / profit for the open document (2026-08-04) ----
   Cards under the editor footer showing what the document costs us, what it
   sells for, the profit and the margin - live, as you type.

   THE POINT OF THIS BLOCK IS THAT IT AGREES WITH THE COSTING SCREEN. The
   authority is `_detect_unit_econ` + `_line_econ` in backend/database.py; the
   functions below are a deliberate line-by-line port, and
   `tests/doc_econ_parity.test.py` runs shared fixtures through BOTH and fails
   on any divergence. If you change one, change the other and add a fixture -
   two profit figures that disagree are worse than none.

   Three things a naive version gets wrong, all of them silent:
     - `cost` is PER UNIT. Summing the COST column understates every line with
       qty > 1.
     - costs are entered in USD or IQD; IQD has to be converted at THIS
       document's peg before it can be added to a dollar figure.
     - an invoice may SELL in IQD too - a different currency from its costs -
       so revenue needs converting as well, at the invoice's own rate.        */

function _econNum(v) {
    if (v === null || v === undefined) return null;
    const s = String(v).trim().replace(/,/g, "");
    if (s === "") return null;
    const n = parseFloat(s);
    return isFinite(n) ? n : null;
}

/* Mirrors database._coerce_cost: a number, or "" for blank/unparseable. */
function _econCost(v) {
    if (v === null || v === undefined) return "";
    const s = String(v).trim().replace(/,/g, "");
    if (s === "") return "";
    const cleaned = s.replace(/[^0-9.\-]/g, "");
    const n = parseFloat(cleaned);
    return isFinite(n) ? n : "";
}

/* Mirrors database._detect_unit_econ -> {unitSell, baseQty, auto}.
   The order of the four branches is load-bearing; see the Python docstring. */
function docUnitEcon(row) {
    const qty = _econNum(row.qty);
    const rate = _econNum(row.rate);
    const total = _econNum(row.total);

    if (total !== null && total > 0) {
        const bq = (qty && qty > 0) ? qty : 1.0;
        return { unitSell: total / bq, baseQty: bq, auto: false };
    }
    // A table the user extended: the real total often lives in a custom column.
    const customVals = [];
    for (const k of Object.keys(row || {})) {
        if (!k.startsWith("col_")) continue;
        const v = row[k];
        if (typeof v === "string" && v.trimStart().toLowerCase().startsWith("data:image/")) continue;
        const n = _econNum(v);
        if (n !== null && n > 0) customVals.push(n);
    }
    if (customVals.length) {
        const cand = Math.max.apply(null, customVals);
        if (qty && rate && Math.abs(qty * rate - cand) < 0.005) {
            return { unitSell: rate, baseQty: qty, auto: false };
        }
        const bq = (qty && qty > 0) ? qty : 1.0;
        return { unitSell: cand / bq, baseQty: bq, auto: true };
    }
    if (qty !== null && qty > 0 && rate !== null) {
        return { unitSell: rate, baseQty: qty, auto: false };
    }
    if (rate !== null && rate > 0) {
        // Flat-rate line: the rate IS the unit price and qty defaults to 1, so
        // entering qty 10 later gives 10*rate rather than rate/10.
        return { unitSell: rate, baseQty: 1.0, auto: false };
    }
    return { unitSell: 0.0, baseQty: 1.0, auto: false };
}

/* The currency THIS row's cost is typed in - mirrors database.row_cost_currency.
   One table can hold a dollar-costed line beside a dinar-costed one; a row with
   no `cost_ccy` inherits the document's, which is every row saved before this
   existed. Anything unrecognised falls back rather than guessing, because
   guessing wrong divides a dollar cost by 1400. */
function rowCostCurrency(row, docCurrency) {
    const v = String((row || {}).cost_ccy || "").trim().toUpperCase();
    if (v === "USD" || v === "IQD") return v;
    const d = String(docCurrency || "USD").trim().toUpperCase();
    return (d === "USD" || d === "IQD") ? d : "USD";
}
window.rowCostCurrency = rowCostCurrency;

/* ---------------------------------------------------------------------------
   The per-row cost cell - ONE implementation for all three editors.

   The quotation grew a per-line currency chip on 2026-08-06; the DN and the
   invoice kept only a document-level USD/IQD switch, so a table could not hold
   a dollar-costed line beside a dinar-costed one and the header switch
   converted every cell blindly. These four helpers are the quotation's
   behaviour lifted out verbatim so the three tables cannot drift - the same
   reason `rowImageCellHTML`/`wireRowImageCell` live here.

   The chip shows "$" or "IQD" - a LABEL, never a colour on its own.
   --------------------------------------------------------------------------- */

/* Format a cost in `ccy`: dinars are whole, dollars keep cents. */
function formatRowCost(n, ccy) {
    if (!isFinite(n)) return "";
    return String(ccy) === "IQD" ? String(Math.round(n))
                                 : String(Math.round(n * 100) / 100);
}
window.formatRowCost = formatRowCost;

function rowCostCellHTML(row, docCurrency) {
    const ccy = rowCostCurrency(row, docCurrency);
    const val = (row && row.cost != null) ? row.cost : "";
    return `<td class="qc-cost-col qc-private"><div class="cost-cell">`
         + `<input data-k="cost" value="${esc(val)}" inputmode="decimal" autocomplete="off" spellcheck="false">`
         + `<button type="button" class="cost-ccy" data-ccy="${ccy}" tabindex="-1"`
         + ` title="This line's cost is in ${ccy} - click to switch">${ccy === "IQD" ? "IQD" : "$"}</button>`
         + `</div></td>`;
}
window.rowCostCellHTML = rowCostCellHTML;

/* The PROFIT half of the private strip (2026-08-25) - read-only and COMPUTED.
   It carries NO `data-k`: a row key would put it in `rows_json` and from there
   into a payload the client can see. Filled by paintRowEconCells below. */
function rowProfitCellHTML() {
    return `<td class="qc-profit-col qc-private"><span class="row-profit" data-profit></span></td>`;
}
window.rowProfitCellHTML = rowProfitCellHTML;

/* Repaint one row's chip. Kept separate because the document-level switch
   stamps every row and has to update every chip with it. */
function paintRowCostChip(tr, ccy) {
    const btn = tr && tr.querySelector(".cost-ccy");
    if (!btn) return;
    btn.dataset.ccy = ccy;
    btn.textContent = ccy === "IQD" ? "IQD" : "$";
    btn.title = `This line's cost is in ${ccy} - click to switch`;
}
window.paintRowCostChip = paintRowCostChip;

/* Flip ONE row between USD and IQD. The real cost is unchanged - only the unit
   it is written in changes - so the number is converted as the chip flips.
   `cost_ccy` is stamped even when the cell is blank, so the NEXT number typed
   is read in the currency the chip is showing. Returns the new currency. */
function toggleRowCostCurrency(row, docCurrency, peg) {
    const from = rowCostCurrency(row, docCurrency);
    const to = from === "IQD" ? "USD" : "IQD";
    const rate = Number(peg) > 0 ? Number(peg) : (window.IQD_PER_USD || 1400);
    const raw = String(row.cost == null ? "" : row.cost).replace(/[, ]/g, "").trim();
    if (raw !== "") {
        const n = parseFloat(raw);
        if (isFinite(n)) row.cost = formatRowCost(to === "IQD" ? n * rate : n / rate, to);
    }
    row.cost_ccy = to;
    return to;
}
window.toggleRowCostCurrency = toggleRowCostCurrency;

/* The document-level header switch. Converts each row FROM ITS OWN currency,
   not blindly by one factor: a line already priced in dinars inside a dollar
   document must be left alone when the document flips to dinars, or it is
   inflated by the exchange rate. Every row is then stamped, because after this
   they really are all in the new currency. */
function convertRowsToCostCurrency(rows, tbodyId, next, peg, docCurrency) {
    const rate = Number(peg) > 0 ? Number(peg) : (window.IQD_PER_USD || 1400);
    (rows || []).forEach((row, i) => {
        if (!row) return;
        const from = rowCostCurrency(row, docCurrency);
        if (from !== next) {
            const raw = String(row.cost == null ? "" : row.cost).replace(/[, ]/g, "").trim();
            if (raw !== "") {
                const n = parseFloat(raw);
                if (isFinite(n)) row.cost = formatRowCost(next === "IQD" ? n * rate : n / rate, next);
            }
        }
        row.cost_ccy = next;
        const tr = document.querySelector(`#${tbodyId} tr[data-i="${i}"]`);
        if (tr) {
            const inp = tr.querySelector('input[data-k="cost"]');
            if (inp) inp.value = row.cost != null ? row.cost : "";
            paintRowCostChip(tr, next);
        }
    });
}
window.convertRowsToCostCurrency = convertRowsToCostCurrency;

/* The cost an autofilled item should put in a row, in THIS document's unit.

   The catalog keeps a cost in the currency it was typed in. When that unit
   already matches the document's, the number is used verbatim - no rate, so
   nothing to be wrong about. Otherwise it converts at `peg`, which callers
   pass as the DOCUMENT's rate: the one pinned under its table, or the cash
   rate. The catalog never supplies a rate of its own.

   `cost_usd` without a native pair is an older entry from a document save -
   genuinely dollars. A raw `cost` is still never replayed: nothing recorded
   what currency it was in. */
function costFromMemory(data, docCcy, peg) {
    if (!data) return null;
    const want = String(docCcy || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
    let value = parseFloat(data.cost_native);
    let from = String(data.cost_native_ccy || "").toUpperCase();
    if (!isFinite(value)) {
        value = parseFloat(data.cost_usd);
        from = "USD";
    }
    if (!isFinite(value) || value <= 0) return null;
    if (from !== "IQD" && from !== "USD") from = "USD";
    if (from === want) return String(value);
    const rate = Number(peg) > 0 ? Number(peg) : (window.IQD_PER_USD || 1400);
    return want === "IQD" ? String(Math.round(value * rate))
                          : String(Math.round((value / rate) * 100) / 100);
}
window.costFromMemory = costFromMemory;

/* What an autofilled item should put in a row's COST CELL - the number AND the
   unit it is written in, as one answer (2026-08-26).

   The cell has carried its own currency since 2026-08-06, so there is no reason
   to convert a remembered cost at all: an item costed in dinars is filled in
   dinars and the chip says so. That is strictly better than converting into the
   document's unit, because a peg is only ever an approximation of what was
   actually paid - and it is what the user asked for: the toggle must show the
   currency the number was SAVED with.

   Falls back to `costFromMemory` (which converts into the document's unit) for
   older entries that recorded no native pair - those are dollars or nothing.
   Returns `{value, ccy}` or null. The DN did this alone from 2026-08-20; the
   quotation and the invoice were still converting. One implementation now, for
   the same reason the cost cell itself is shared. */
function costCellFromMemory(data, docCcy, peg) {
    if (!data) return null;
    const native = parseFloat(data.cost_native);
    const nccy = String(data.cost_native_ccy || "").trim().toUpperCase();
    if (isFinite(native) && native > 0 && (nccy === "USD" || nccy === "IQD")) {
        return { value: String(native), ccy: nccy };
    }
    const converted = costFromMemory(data, docCcy, peg);
    if (converted === null) return null;
    const want = String(docCcy || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
    return { value: converted, ccy: want };
}
window.costCellFromMemory = costCellFromMemory;

/* The price an autofilled item should put in a row, in THIS document's SELL
   unit. Same rule as the cost, but keyed off the sell currency and the sell
   rate (an invoice's pinned "1 $ = N IQD"), not the cost ones - a document can
   be costed in one currency and billed in another. Quotations and delivery
   notes always sell in USD. */
function priceFromMemory(data, sellCcy, sellPeg) {
    if (!data) return null;
    const want = String(sellCcy || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
    let value = parseFloat(data.price_native);
    let from = String(data.price_native_ccy || "").toUpperCase();
    if (!isFinite(value)) {
        value = parseFloat(data.rate);
        from = "USD";
    }
    if (!isFinite(value) || value <= 0) return null;
    if (from !== "IQD" && from !== "USD") from = "USD";
    if (from === want) return String(value);
    const rate = Number(sellPeg) > 0 ? Number(sellPeg) : (window.IQD_PER_USD || 1400);
    return want === "IQD" ? String(Math.round(value * rate))
                          : String(Math.round((value / rate) * 100) / 100);
}
window.priceFromMemory = priceFromMemory;



/* Under a cent is rounding, not a loss (see computeDocEcon). */
const LOSS_EPSILON = 0.005;

function _econToUsd(value, currency, rate) {
    const n = _econNum(value);
    if (n === null) return 0;
    if (String(currency || "USD").toUpperCase() !== "IQD") return n;
    const r = Number(rate) > 0 ? Number(rate) : 1;
    return n / r;
}

/* Mirrors database._line_econ. Everything returned is USD.
   `opts.primaryKey` is the schema's primary column, so the line can name
   itself the way the backend does - the loss list is unreadable without it. */
function docLineEcon(row, opts) {
    const o = opts || {};
    const det = docUnitEcon(row);
    let unitSell = det.unitSell;
    if (String(o.sellCurrency || "USD").toUpperCase() === "IQD") {
        unitSell = _econToUsd(unitSell, "IQD", o.sellPeg);
    }

    const qOverride = _econNum(row.cost_qty);
    const qty = qOverride !== null ? qOverride : det.baseQty;

    const sOverride = _econNum(row.cost_sell);
    // A cost_sell override is ALWAYS USD - it is typed on the costing screen,
    // which is USD end to end, so it must not be converted again.
    const sell = sOverride !== null ? sOverride : unitSell;

    const revenue = qty * sell;
    const costRaw = _econCost(row.cost);
    const hasCost = costRaw !== "";
    const costUnitUsd = hasCost
        ? _econToUsd(costRaw, rowCostCurrency(row, o.costCurrency), o.costPeg)
        : 0;
    // 2026-10-21 (Waleed) - a billable line with no cost is NOT 100% profit:
    // its cost is estimated at UNCOSTED_MARKUP on cost, exactly as the backend
    // does, so the editor's cards and Insights agree. `hasCost` stays false.
    const billable = revenue > 0 || sell > 0;
    let estCost = 0;
    let lineCost = qty * costUnitUsd;
    if (billable && !hasCost && revenue > 0) {
        estCost = revenue / (1 + uncostedMarkup());
        lineCost = estCost;
    }

    const pk = o.primaryKey || "item";
    const name = String((row && (row[pk] || row.item || row.description)) || "").trim();

    return {
        name, qty, sell, revenue, hasCost,
        unitCost: costUnitUsd,
        lineCost, lineProfit: revenue - lineCost,
        estCost,
        billable,
    };
}

/* config.UNCOSTED_MARKUP, sent with the profile metadata; 0.40 until it lands. */
function uncostedMarkup() {
    const m = Number(window.UNCOSTED_MARKUP);
    return Number.isFinite(m) && m > -1 ? m : 0.40;
}

/* Totals for the whole document. `extraCosts` are the document-level costs
   entered on the Costing screen ({label, amount} in the COST entry currency);
   omitting them would overstate profit on any quote that has them. */
function computeDocEcon(spec) {
    const s = spec || {};
    const rows = s.rows || [];
    let revenue = 0, lineCostTotal = 0, estCostTotal = 0, billable = 0, uncosted = 0;
    let worst = null;
    /* Per-line losses (2026-08-25). A document can show a healthy profit while
       individual lines sell below cost - the winners hide the losers. Only a
       BILLABLE line with a REAL cost can be judged: an uncosted line looks
       free, and calling that "not a loss" would be the same confident wrong
       number the uncosted warning exists to prevent. */
    const losers = [];
    let lossTotal = 0;
    /* Every row's own economics, index-aligned with `rows` (2026-08-25). The
       PROFIT column paints straight out of this, so the figure on a line and
       the figure in the totals can only ever be the same arithmetic. A row
       that is not billable, or has no cost, is present with `state` saying so
       - the column has to distinguish "no profit" from "unknown". */
    const lines = [];

    rows.forEach((row, i) => {
        const e = docLineEcon(row, s);
        revenue += e.revenue;
        lineCostTotal += e.lineCost;
        estCostTotal += e.estCost;
        lines.push({
            index: i, name: e.name, qty: e.qty, sell: e.sell,
            unitCost: e.unitCost, revenue: e.revenue, lineCost: e.lineCost,
            hasCost: e.hasCost, billable: e.billable,
            lineProfit: e.lineProfit,
            margin: e.revenue ? (e.lineProfit / e.revenue) * 100 : 0,
            // "blank"  - nothing to say (an empty row)
            // "unknown" - billable but uncosted; profit is NOT zero, it is unknown
            // "loss" / "profit" - a real figure
            state: !e.billable ? "blank"
                 : !e.hasCost ? "unknown"
                 : (e.lineProfit < -LOSS_EPSILON ? "loss" : "profit"),
        });
        if (!e.billable) return;
        billable += 1;
        if (!e.hasCost) { uncosted += 1; return; }
        const m = e.revenue ? (e.lineProfit / e.revenue) * 100 : 0;
        if (worst === null || m < worst.margin) {
            worst = { index: i, margin: m, profit: e.lineProfit };
        }
        // A cent of tolerance: rounding a converted dinar cost must not invent
        // a "loss" on a line the user priced exactly at cost.
        if (e.lineProfit < -LOSS_EPSILON) {
            lossTotal += -e.lineProfit;
            losers.push({
                index: i,
                name: e.name,
                qty: e.qty,
                sell: e.sell,
                unitCost: e.unitCost,
                revenue: e.revenue,
                lineCost: e.lineCost,
                loss: -e.lineProfit,          // always POSITIVE - "by how much"
                lineProfit: e.lineProfit,
                margin: m,
            });
        }
    });
    // Worst first: the biggest dollar hole is the one worth re-pricing.
    losers.sort((a, b) => b.loss - a.loss);

    let extraCost = 0;
    (s.extraCosts || []).forEach(e => {
        if (e && typeof e === "object") extraCost += _econToUsd(e.amount, s.costCurrency, s.costPeg);
    });

    const cost = lineCostTotal + extraCost;
    const profit = revenue - cost;
    return {
        revenue, lineCost: lineCostTotal, extraCost, cost, profit,
        estCost: estCostTotal,
        margin: revenue ? (profit / revenue) * 100 : 0,
        billable, uncosted, worst,
        lines, losers, lossTotal, lossCount: losers.length,
        // "Fully costed" matches the backend's `costed` flag, which is what the
        // dashboard's Uncosted bucket and the panel pill both use.
        costed: billable > 0 && uncosted === 0,
    };
}
window.docUnitEcon = docUnitEcon;
window.docLineEcon = docLineEcon;
window.computeDocEcon = computeDocEcon;

/* Quantities read as typed: 3 stays 3, 2.5 stays 2.5. */
function _econQty(v) {
    const n = Number(v) || 0;
    return (Math.abs(n - Math.round(n)) < 1e-9)
        ? String(Math.round(n))
        : String(Math.round(n * 1000) / 1000);
}

/* The per-line loss block (2026-08-25) - "which items are a losing deal, and
   by how much in $". The document-level Profit card cannot answer that: a
   quotation can be $2,000 up overall while three lines sell under cost.

   Every entry NAMES the item, states the dollar loss, and shows the two
   numbers that caused it (sell vs cost per unit), because "you are losing
   $84" is not actionable without knowing which of the two to move. Worst
   first - the biggest hole is the one worth re-pricing.

   Never colour alone: the heading says "sell below cost" and every amount
   carries a minus sign, so the red is reinforcement ([[waleed-colour-blind]]). */
const ECON_LOSS_SHOWN = 5;

function _econLossHTML(econ) {
    if (!econ || !econ.lossCount) return "";
    const shown = econ.losers.slice(0, ECON_LOSS_SHOWN);
    const rest = econ.lossCount - shown.length;
    const items = shown.map(l => {
        const label = l.name || `Line ${l.index + 1}`;
        return `<li class="econ-loss-item">
            <button type="button" class="econ-loss-row" data-loss-i="${l.index}"
                    title="Show this line in the table">
                <span class="ell-no">${l.index + 1}</span>
                <span class="ell-name">${esc(label)}</span>
                <span class="ell-amt">-${_econMoney(l.loss)}</span>
            </button>
            <div class="ell-sub">${esc(_econQty(l.qty))} &times; ${_econMoney(l.sell)} sell
                 vs ${_econMoney(l.unitCost)} cost each</div>
        </li>`;
    }).join("");
    return `<div class="econ-loss">
        <div class="econ-loss-head">
            <span class="econ-loss-title">${econ.lossCount} line${econ.lossCount === 1 ? "" : "s"} sell${econ.lossCount === 1 ? "s" : ""} below cost</span>
            <span class="econ-loss-sum">-${_econMoney(econ.lossTotal)}</span>
        </div>
        <ul class="econ-loss-list">${items}</ul>
        ${rest > 0 ? `<div class="econ-loss-more">+${rest} more line${rest === 1 ? "" : "s"} below cost</div>` : ""}
    </div>`;
}

function _econMoney(v) {
    const n = Number(v) || 0;
    const s = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? "-$" : "$") + s;
}

/* Paint the cards. `host` is the per-editor container; `note` is an optional
   line under them (e.g. the IQD rate costs were converted at). */
function renderEconCards(host, econ, opts) {
    if (!host) return;
    const o = opts || {};
    if (!econ || (!econ.billable && !econ.cost)) { host.innerHTML = ""; return; }

    // Never red/green: a loss is red, a profit is NEUTRAL, and the label says
    // which it is either way. Red/green is unreadable for this user.
    const lossy = econ.profit < 0;
    const profitCls = lossy ? "econ-card is-loss" : "econ-card is-profit";
    const marginTxt = econ.revenue
        ? econ.margin.toFixed(1) + "%"
        : "-";

    const cards = [
        `<div class="econ-card"><div class="econ-k">Cost</div>
            <div class="econ-v">${_econMoney(econ.cost)}</div>
            ${econ.extraCost ? `<div class="econ-sub">incl. ${_econMoney(econ.extraCost)} extra</div>` : ""}
         </div>`,
        `<div class="econ-card"><div class="econ-k">Sell</div>
            <div class="econ-v">${_econMoney(econ.revenue)}</div></div>`,
        `<div class="${profitCls}"><div class="econ-k">${lossy ? "Loss" : "Profit"}</div>
            <div class="econ-v">${_econMoney(econ.profit)}</div></div>`,
        `<div class="${profitCls}"><div class="econ-k">Margin</div>
            <div class="econ-v">${esc(marginTxt)}</div></div>`,
    ];

    // The honest guard: lines with no cost are counted at an ESTIMATED cost
    // (UNCOSTED_MARKUP, 2026-10-21), so the cards are close, not exact.
    const warn = econ.uncosted
        ? `<div class="econ-warn" title="Lines with no cost are counted at a ${Math.round(uncostedMarkup() * 100)}% markup until you enter the real cost">
             ${econ.uncosted} of ${econ.billable} line${econ.billable === 1 ? "" : "s"} not costed -
             ${_econMoney(econ.estCost || 0)} of the cost is estimated
           </div>`
        : "";

    // `note` is escaped; `noteHTML` is opt-in markup for callers that need a
    // control in there (the quotation's clickable exchange rate). Same split as
    // showModal's `html` flag, and for the same reason: document data flows
    // through `note`, so it must stay text by default.
    const noteBlock = o.noteHTML
        ? `<div class="econ-note">${o.noteHTML}</div>`
        : (o.note ? `<div class="econ-note">${esc(o.note)}</div>` : "");
    host.innerHTML = `<div class="econ-cards-row">${cards.join("")}</div>`
        + warn + _econLossHTML(econ) + noteBlock;

    /* Clicking a losing line jumps to it in the table. Guarded because the
       render tests paint into a plain object, and because an editor that does
       not supply a picker still gets the list. */
    if (o.onPickRow && host.querySelectorAll) {
        host.querySelectorAll(".econ-loss-row").forEach(btn => {
            btn.onclick = () => o.onPickRow(parseInt(btn.dataset.lossI, 10));
        });
    }
}
window.renderEconCards = renderEconCards;

/* ---- The private strip: COST + PROFIT, shared by the three editors ---------
   (loss tags 2026-08-25, profit column 2026-08-25.)

   The list under the footer says which lines lose money; this puts each line's
   own figure ON the line, so a long table does not have to be cross-read
   against a list - and it shows the winners too, not only the losers.

   The cell is READ-ONLY and computed: `profit` is never a row key and never
   reaches `rows_json` or a PDF payload. It sits in the private strip beside
   COST because it is the same category of number - derived from the cost,
   equally private, and never something the client sees.

   Four states, because "no profit" and "profit unknown" are different things:
     profit  -> +$120.00, neutral
     loss    -> LOSS -$84.00, red
     unknown -> "-" with a tooltip (billable, but no cost entered yet)
     blank   -> empty (an empty row)
   -------------------------------------------------------------------------- */

function paintRowEconCells(tbodyId, econ) {
    const body = document.getElementById(tbodyId);
    if (!body) return;
    const byIndex = new Map();
    ((econ && econ.lines) || []).forEach(l => byIndex.set(l.index, l));
    body.querySelectorAll("tr[data-i]").forEach(tr => {
        const i = parseInt(tr.dataset.i, 10);
        const line = byIndex.get(i);
        const state = line ? line.state : "blank";
        tr.classList.toggle("is-loss-row", state === "loss");
        const cell = tr.querySelector("[data-profit]");
        if (!cell) return;                       // a DN with no prices has none
        cell.classList.toggle("is-loss", state === "loss");
        cell.classList.toggle("is-unknown", state === "unknown");
        if (state === "blank") { cell.textContent = ""; cell.title = ""; return; }
        if (state === "unknown") {
            cell.textContent = "-";
            cell.title = "No cost entered for this line - its profit is unknown, not zero.";
            return;
        }
        const detail = `${_econQty(line.qty)} × ${_econMoney(line.sell)} sell`
                     + ` against ${_econMoney(line.unitCost)} cost each`
                     + ` (${line.margin.toFixed(1)}% margin).`;
        if (state === "loss") {
            // The word carries the meaning; the amount answers "by how much".
            cell.innerHTML = `<span class="rp-loss-tag">LOSS</span>-${_econMoney(-line.lineProfit)}`;
            cell.title = `This line sells at a loss of ${_econMoney(-line.lineProfit)} - ${detail}`;
        } else {
            cell.textContent = `+${_econMoney(line.lineProfit)}`;
            cell.title = `This line makes ${_econMoney(line.lineProfit)} - ${detail}`;
        }
    });
}
window.paintRowEconCells = paintRowEconCells;

/* Scroll a row into view and flash it - the loss list's click target. */
function focusEditorRow(tbodyId, index) {
    const tr = document.querySelector(`#${tbodyId} tr[data-i="${index}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center", behavior: "smooth" });
    tr.classList.remove("row-flash");
    void tr.offsetWidth;               // restart the animation on a re-click
    tr.classList.add("row-flash");
    setTimeout(() => tr.classList.remove("row-flash"), 1600);
    const inp = tr.querySelector('input[data-k="cost"]');
    if (inp) inp.focus({ preventScroll: true });
}
window.focusEditorRow = focusEditorRow;

/* ---- Privacy mode - hide every private figure at once (2026-08-25) ---------
   COST, PROFIT and the profit/loss cards are numbers the user does not want on
   screen when somebody is standing beside them. One control hides ALL of them:
   hiding the cost column while the profit cards still sit under the table
   would give the same numbers away and be worse than no control at all.

   It is a CSS class on <body>, so it costs nothing per keystroke and applies to
   whichever editor is open. Persisted in localStorage, NOT in settings.json:
   this is a screen preference, it must survive a reload, and it must not queue
   a bridge write on a keystroke-fast toggle.

   The state is always VISIBLE in the button ("Show costs" + an active look), so
   a user who forgets they hid the column can never mistake it for a bug. ---- */
const PRIVACY_KEY = "abraj_privacy";

function isPrivacyOn() {
    try { return localStorage.getItem(PRIVACY_KEY) === "1"; } catch (e) { return false; }
}

function setPrivacyMode(on, opts) {
    const o = opts || {};
    document.body.classList.toggle("privacy-on", !!on);
    if (o.persist !== false) {
        try { localStorage.setItem(PRIVACY_KEY, on ? "1" : "0"); } catch (e) { /* private mode */ }
    }
    syncPrivacyButtons(!!on);
}
window.setPrivacyMode = setPrivacyMode;

function syncPrivacyButtons(on) {
    document.querySelectorAll('[data-act="toggle-private"]').forEach(btn => {
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        // Icon-only button (2026-08-25), so the OPEN vs STRUCK-THROUGH eye is
        // what says which state you are in - two different shapes, not two
        // colours. The words moved to the tooltip and the accessible name,
        // which is also the only thing a screen reader has to go on.
        const icon = btn.querySelector(".pv-icon");
        if (icon) icon.innerHTML = on ? ICON.eyeOff : ICON.eye;
        const words = on
            ? "Cost, profit and the profit cards are hidden - click to show them"
            : "Hide the COST and PROFIT columns and the profit cards";
        btn.setAttribute("aria-label", words);
        btn.title = words + " (Ctrl+Shift+H)";
        // Kept for any caller that still renders a label beside the icon.
        const label = btn.querySelector(".pv-label");
        if (label) label.textContent = on ? "Show costs" : "Hide costs";
    });
}

function togglePrivacyMode() {
    const next = !document.body.classList.contains("privacy-on");
    setPrivacyMode(next);
    toast(next ? "Cost & profit hidden" : "Cost & profit shown");
}
window.togglePrivacyMode = togglePrivacyMode;

/* Called once at boot and again whenever an editor paints its footer. */
function initPrivacyMode() {
    setPrivacyMode(isPrivacyOn(), { persist: false });
    document.querySelectorAll('[data-act="toggle-private"]').forEach(btn => {
        if (btn._pvWired) return;
        btn._pvWired = true;
        btn.addEventListener("click", safe(() => togglePrivacyMode(), "Hide costs"));
    });
}
window.initPrivacyMode = initPrivacyMode;

/* ---------- The pending (unsaved) duplicate, shown in the Open panel --------
   A duplicate is not written until you press Save, so it exists in neither the
   database nor the filesystem and no month list can return it. This holds the
   one unsaved duplicate currently open in an editor so its pane can render a
   provisional row for it at the top of the list.

   Set in exactly ONE place - duplicateDocument, below - so the five editors
   don't each need to know about it.

   It clears itself two ways. `hideAllViews()` drops it when you navigate away,
   and each editor's New/reset path drops it. But the load-bearing one is
   passive: `pendingRowEl` suppresses the row as soon as the real document
   appears in the list it is rendering into. That means SAVING needs no hook at
   all - the saved row arrives, the provisional one vanishes, and there is no
   window in which both are on screen. */
let _pendingDoc = null;   // {kind, no, sourceNo, year, month}

function setPendingDoc(doc) { _pendingDoc = doc || null; }
function clearPendingDoc() { _pendingDoc = null; }
function currentPendingDoc() { return _pendingDoc; }
window.setPendingDoc = setPendingDoc;
window.clearPendingDoc = clearPendingDoc;
window.currentPendingDoc = currentPendingDoc;

const PENDING_LABELS = {
    po: { copyOf: (n) => `نسخة من ${n}`, unsaved: "لم يُحفظ بعد", hint: "اضغط حفظ لإنشائه" },
    _:  { copyOf: (n) => `copy of ${n}`, unsaved: "Not saved yet", hint: "press Save to create it" },
};

/* Returns the provisional row for `kind`, or null when there is nothing to
   show. `list` + `keyField` are the month list just fetched by the caller:
   if it already contains the pending number the document has been saved, so
   the row is dropped and the pending state cleared. */
function pendingRowEl(kind, year, month, list, keyField) {
    const p = _pendingDoc;
    if (!p || p.kind !== kind) return null;
    // Only in its own month - otherwise browsing May would show an August doc.
    if (+p.year !== +year || +p.month !== +month) return null;
    if ((list || []).some(x => String(x && x[keyField]) === String(p.no))) {
        clearPendingDoc();
        return null;
    }
    const L = PENDING_LABELS[kind] || PENDING_LABELS._;
    const row = document.createElement("div");
    row.className = "po-row is-pending";
    // Not `is-clickable`: there is nothing to load - it is already in the editor.
    row.title = L.hint;
    row.innerHTML = `
        <div>
            <div class="po-num">${esc(p.no)}
                <span class="pending-pill">${L.unsaved}</span>
            </div>
            <div class="po-meta">${esc(L.copyOf(p.sourceNo || ""))} · ${esc(L.hint)}</div>
        </div>
        <div class="row-actions"></div>`;
    return row;
}
window.pendingRowEl = pendingRowEl;

/** The panel-row Duplicate button, shared by all five list renderers. */
function duplicateBtnHTML(kind, docNo, title) {
    return `<button class="ghost" data-act="duplicate" data-kind="${esc(kind)}" data-no="${esc(docNo)}"`
         + ` title="${esc(title || `Duplicate ${docNo} as a new document`)}">`
         + `${ICON.duplicate}<span>Duplicate</span></button>`;
}
window.duplicateBtnHTML = duplicateBtnHTML;

/** Wire every Duplicate button inside `container`. `onAfter` refreshes the list. */
function wireDuplicateButtons(container) {
    if (!container) return;
    container.querySelectorAll("button[data-act='duplicate']").forEach(b => {
        b.onclick = safe((e) => {
            e.stopPropagation();          // the row itself opens the doc for edit
            return duplicateDocument(b.dataset.kind, b.dataset.no);
        }, "Duplicate document");
    });
}
window.wireDuplicateButtons = wireDuplicateButtons;

/* PO's own hook. Returns the new number, or "" if it could not be derived.
   Everything here is identity or provenance - the rows, layout and quotation
   reference loaded by loadForEdit are exactly what we want to keep. */
async function poMakeDuplicate() {
    state.mode = "new";
    state.editPoNo = null;
    state.signingOverlay = null;          // a copy has not been signed
    state.poFulfillment = null;           // nor delivered, invoiced or paid
    document.getElementById("poReference").value = "";
    window.__currentEditorDoc = null;     // it is not a saved document yet
    // No submitted chip to clear - the Submit button in the panel is the only
    // submitted indicator now, and it is per-row, not per-editor.
    if (window.setPoFulfillChips) window.setPoFulfillChips("none", "none", "none");
    poRefreshSignChip();
    decoratePoFulfillment();              // drop the delivered/paid row tints
    applyPoDateToday();
    // initNextPoNumber() also clears this, but state it here so the hook is
    // self-describing and doesn't silently depend on that side effect.
    window.setModeChip(false);
    await initNextPoNumber();
    refreshPoLinkButtons();               // link buttons off until it is saved
    state.dirtySinceSave = true;          // it is real unsaved content
    return state.poNo || "";
}
window.poMakeDuplicate = poMakeDuplicate;

/** Phase M (item 11.1) - fire-and-forget print via the OS default printer. */
async function printPdfFile(path) {
    if (!path) return;
    const r = await api().print_file(path);
    if (!r.ok) toast("Print failed: " + (r.error || ""), true);
    else        toast("Sent to printer");
}
window.printPdfFile = printPdfFile;

/**
 * Put a file on the Windows clipboard (CF_HDROP), so pasting in WhatsApp /
 * Outlook / etc. attaches the file. Used by both the PO and quote open lists.
 */
async function copyFileToClipboard(path) {
    const r = await api().copy_file_to_clipboard(path);
    if (r && r.ok) {
        toast("Copied - paste anywhere to attach the file");
    } else {
        toast("Copy failed: " + (r && r.error || "unknown"), true);
    }
}
window.copyFileToClipboard = copyFileToClipboard;

// ============================================================
// Copy guard (2026-07-24) - stop an UNSIGNED document going out to a client.
// Every right-panel Copy button routes through here. Signed documents copy
// straight away; an unsigned one offers to sign + re-render first.
// `doc.signed` comes from the list payload (signing.signed_doc_nos).
// ============================================================
async function copyDocumentFile(kind, docNo, path, signed) {
    if (signed) return copyFileToClipboard(path);

    const choice = await showModal({
        title: "This document isn’t signed",
        body: `${docNo || "This document"} has no signature or stamp on it yet. `
            + "Do you want to sign and stamp it before copying?",
        buttons: [
            { label: "Cancel",             value: "cancel", cls: "ghost" },
            { label: "Copy without signing", value: "copy",  cls: "secondary" },
            { label: "Apply sign & stamp", value: "sign",   cls: "primary" },
        ],
    });
    if (choice === "cancel" || choice == null) return;
    if (choice === "copy") return copyFileToClipboard(path);

    let r = await api().sign_saved_document(kind, docNo);
    // A PO saved before column widths were stored would re-render with the
    // template's widths - confirm before changing how the document looks.
    if (r && r.needs_confirm === "layout") {
        const ok = await showModal({
            title: "Column widths may change",
            body: "This purchase order was saved before column widths were stored, "
                + "so re-creating it with a signature may shift its columns back to "
                + "the default layout. The contents stay the same.",
            buttons: [
                { label: "Cancel",            value: "cancel", cls: "ghost" },
                { label: "Sign it anyway",    value: "ok",     cls: "primary" },
            ],
        });
        if (ok !== "ok") return;
        r = await api().sign_saved_document(kind, docNo, true);
    }
    if (!r || !r.ok) {
        toast("Couldn’t sign: " + ((r && r.error) || "unknown"), true);
        return;
    }
    toast("Signed and stamped");
    if (window.refreshOpenPanel) window.refreshOpenPanel();
    await copyFileToClipboard(r.path || path);
}
window.copyDocumentFile = copyDocumentFile;

function buildRowTr(i) {
    const r = state.rows[i];
    const tr = document.createElement("tr");
    tr.dataset.i = i;
    tr.innerHTML = `
        <td class="c-idx">${i + 1}</td>
        <td class="c-name"><textarea data-k="name" rows="1" autocomplete="off">${esc(r.name)}</textarea></td>
        <td class="c-desc"><textarea data-k="description" rows="1">${esc(r.description)}</textarea></td>
        <td class="c-unit"><textarea data-k="unit" rows="1">${esc(r.unit)}</textarea></td>
        <td class="c-qty"><input data-k="qty" value="${esc(r.qty)}" inputmode="decimal"></td>
        <td class="c-company"><textarea data-k="company" rows="1">${esc(r.company)}</textarea></td>
        <td class="c-image qc-img-cell" data-k="image">${rowImageCellHTML(r.image)}</td>
        <td class="c-del"><button class="del-btn" title="حذف" aria-label="حذف السطر">${ICON.trash}</button></td>
    `;
    wireRow(tr);
    return tr;
}

// Auto-grow a textarea to fit its content height.
function autoResizePoTextarea(ta) {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + "px";
}

function wireRow(tr) {
    const i = +tr.dataset.i;
    // Same pick / paste / drop / crop as the quotation's IMAGE column - the
    // pipeline is shared (app.js), this only says where the value is kept.
    tr.querySelectorAll(".qc-img-cell").forEach(cell => {
        wireRowImageCell(cell, {
            get: () => state.rows[i].image,
            set: (v) => {
                state.rows[i].image = v;
                state.dirtySinceSave = true;
                updateSplitWarning();     // an image changes the row height
            },
        });
    });
    // Text columns are now textareas (Enter = newline); qty stays a single-line input.
    tr.querySelectorAll("textarea, input").forEach(field => {
        const k = field.dataset.k;
        if (field.tagName === "TEXTAREA") autoResizePoTextarea(field);
        field.addEventListener("input", () => {
            state.rows[i][k] = field.value;
            state.dirtySinceSave = true;
            if (field.tagName === "TEXTAREA") autoResizePoTextarea(field);
            if (k === "name")    onNameInput(field, i);
            if (k === "company") onCompanyInput(field, i);

            // If this is the LAST row and it now has any content, silently
            // append a fresh blank row to the DOM - without rebuilding existing
            // rows, so the user's caret stays exactly where it is.
            if (i === state.rows.length - 1 && hasAnyContent(state.rows[i])) {
                addRow();
            }
            updateSplitWarning();
            updateRowCount();
        });
        field.addEventListener("focus", () => {
            if (field.value.trim()) {
                if (k === "name")    onNameInput(field, i);
                if (k === "company") onCompanyInput(field, i);
            }
        });
        field.addEventListener("blur", () => {
            setTimeout(hideAc, 150);
            // A company name left in a PO line that we've never classified →
            // ask once whether it's a client (billable) or our own office.
            // Delayed so an autocomplete pick (mousedown) commits its value first.
            if (k === "company") setTimeout(() => queueCompanyTagPrompt(field.value), 200);
        });
    });
    tr.querySelector(".del-btn").addEventListener("click", () => {
        removeRow(+tr.dataset.i);
    });
}

function removeRow(i) {
    const tr = document.querySelector(`#rowsBody tr[data-i="${i}"]`);
    const finish = () => {
        state.rows.splice(i, 1);
        state.dirtySinceSave = true;
        fullRenderRows();
        if (state.rows.length === 0) addRow();
        updateSplitWarning();
        updateRowCount();
    };
    // Animate the row out before removing it from state, so deletion
    // doesn't feel like a flash. The CSS transition is 140ms.
    if (tr) {
        tr.classList.add("is-leaving");
        setTimeout(finish, 140);
    } else {
        finish();
    }
}

function fullRenderRows() {
    // Only used after structural change (delete / reset / load existing PO).
    const tbody = document.getElementById("rowsBody");
    tbody.innerHTML = "";
    state.rows.forEach((_, i) => tbody.appendChild(buildRowTr(i)));
}

function focusRow(i, key = "name") {
    const tr = document.querySelector(`#rowsBody tr[data-i="${i}"]`);
    if (!tr) return;
    const inp = tr.querySelector(`input[data-k="${key}"]`);
    if (inp) inp.focus();
}

function hasAnyContent(r) {
    // A picture alone is content: a row can legitimately be "this part, see
    // photo" with the name filled in later, and dropping it on save would lose
    // an image the user had already cropped.
    if (String(r.image || "").trim() !== "") return true;
    return (r.name + r.description + r.unit + r.qty + r.company).toString().trim() !== "";
}
function filledRows() { return state.rows.filter(hasAnyContent); }

function esc(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// 2026-07-29 - a long PO is no longer split into several POs; it is ONE PO
// printed over several pages (like every other document type). So this is a
// neutral page-count note, not a warning about extra documents. The count comes
// from the backend because the break rule is content-height based.
async function updateSplitWarning() {
    const filled = filledRows();
    const warn = document.getElementById("splitWarn");
    const extra = document.getElementById("poExtra");
    if (filled.length === 0) {
        warn.classList.remove("show");
        extra.textContent = "";
        return;
    }
    /* The dragged widths go with the count (2026-09-28). A narrower column
       wraps sooner and needs more pages, so counting at the defaults told him
       "1 page" for a PDF that printed on two. */
    const pages = Number(await api().po_page_count(
        state.poNo, filled, editorLayoutPayload(state.autofitRows),
        poColWidths())) || 1;
    if (pages <= 1) {
        warn.classList.remove("show");
        extra.textContent = "";
        return;
    }
    warn.innerHTML =
        `سيُطبع هذا الطلب على <strong>${pages}</strong> صفحات في ملف PDF واحد ` +
        `بنفس رقم الطلب <strong>${esc(state.poNo)}</strong>.`;
    warn.classList.add("show");
    extra.textContent = `${pages} صفحات`;
}

// ============================================================
// Autocomplete - generic. Two flavors:
//   - item: suggesting items (fills name, description, unit, company)
//   - company: suggesting just company names (fills company only)
// ============================================================
let acTarget = null;      // { inp, rowIdx, kind: "item" | "company" }
let acItems = [];
let acIdx = -1;

// ---- PO per-line company classification (client vs our own office) ----------
// Lazily-loaded {name: "client"|"internal"} map (seeded on first read). When a
// company name we've never classified is entered on a PO line, ask once and
// remember the answer; known names never prompt again. See backend
// get_po_company_tags / set_po_company_tag and database.pos_awaiting_invoice.
let _poCompanyTags = null;
let _companyPromptOpen = false;

function _poTagKnown(name) {
    if (!_poCompanyTags) return false;
    const key = String(name).trim().toLowerCase();
    return Object.keys(_poCompanyTags).some(k => String(k).trim().toLowerCase() === key);
}

async function queueCompanyTagPrompt(name) {
    const nm = (name || "").trim();
    if (!nm || _companyPromptOpen) return;
    if (_poCompanyTags == null) {
        try { _poCompanyTags = (await api().get_po_company_tags()) || {}; }
        catch { _poCompanyTags = {}; }
    }
    if (_poTagKnown(nm)) return;                 // already classified → silent
    _companyPromptOpen = true;
    const tag = await promptCompanyTag(nm);      // "client" | "internal" | null (cancelled)
    _companyPromptOpen = false;
    // Cancelled = no answer, so nothing is saved (2026-10-21). Cancel used to
    // mean "client", which filed every typo ("v", "j", "ت") as a client
    // company in PO Settings - and the save that did it also reset the rest
    // of the map. An unclassified name is treated as a client anyway.
    if (!tag) return;
    _poCompanyTags[nm] = tag;                     // optimistic - never re-ask this name
    try { await api().set_po_company_tag(nm, tag); }
    catch { /* stays cached for this session; will re-seed next launch */ }
}

// Small modal: classify a new PO company as a client or our own office.
// Cancel / Esc / backdrop = null: nothing is saved (an unclassified name is
// billed like a client, the safe default, and the next real name is asked).
function promptCompanyTag(name) {
    /* Shared Popup ([[14-UI-CONTRACTS]]). Both answers are real choices, so
       neither is the "primary" fill. */
    return window.Popup.open({
        title: name,
        subtitle: "New company on this PO",
        size: "sm",
        dir: "ltr",
        className: "cotag-modal",
        html: true,
        body: `<div class="cotag-q">Are these items for a client (to be delivered &amp; invoiced), or for our own office?</div>`
            + `<div class="cotag-hint">You can change this any time in PO Settings → Company classification.</div>`,
        actions: [
            { label: "Our company - internal", value: "internal", cls: "secondary" },
            { label: "Client - invoice it", value: "client", cls: "primary" },
        ],
    }).result.then((v) => (v === "internal" || v === "client" ? v : null));
}

async function onNameInput(inp, rowIdx) {
    const q = inp.value.trim();
    if (!q) { hideAc(); return; }
    const suggestions = await api().suggest_items(q);
    if (!suggestions || suggestions.length === 0) { hideAc(); return; }
    showAc(inp, rowIdx, suggestions, "item");
}

async function onCompanyInput(inp, rowIdx) {
    const q = inp.value.trim();
    if (!q) { hideAc(); return; }
    const suggestions = await api().suggest_companies(q);
    if (!suggestions || suggestions.length === 0) { hideAc(); return; }
    showAc(inp, rowIdx, suggestions, "company");
}

function showAc(inp, rowIdx, suggestions, kind) {
    const box = document.getElementById("acBox");
    box.innerHTML = "";
    acItems = suggestions;
    acIdx = 0;   // Phase X.21 - pre-select the top match so Enter/Tab accepts it
    acTarget = { inp, rowIdx, kind };
    suggestions.forEach((s, i) => {
        const div = document.createElement("div");
        div.className = "ac-item";
        if (kind === "item") {
            const meta = [s.description, s.unit, s.last_company]
                .map(x => (x || "").toString().trim())
                .filter(Boolean).join(" · ");
            div.innerHTML = `<div>${esc(s.name)}</div>
                ${meta ? `<div class="sub">${esc(meta)}</div>` : ""}`;
        } else {
            div.innerHTML = `<div>${esc(s.name)}</div>`;
        }
        div.onmousedown = (e) => { e.preventDefault(); pickSuggestion(i); };
        box.appendChild(div);
    });
    const rect = inp.getBoundingClientRect();
    box.style.top = (rect.bottom + window.scrollY) + "px";
    box.style.left = (rect.left + window.scrollX) + "px";
    box.style.minWidth = rect.width + "px";
    box.hidden = false;
    refreshAcActive();   // Phase X.21 - highlight the pre-selected first item
}

function hideAc() {
    document.getElementById("acBox").hidden = true;
    acTarget = null; acItems = []; acIdx = -1;
}

function pickSuggestion(i) {
    if (!acTarget) return;
    const s = acItems[i];
    const ri = acTarget.rowIdx;
    const r = state.rows[ri];
    const tr = document.querySelector(`#rowsBody tr[data-i="${ri}"]`);

    if (acTarget.kind === "item") {
        // Fill name + best-known defaults. NAME, DESCRIPTION, UNIT and the
        // picture only - never the company (a fact about the last order, not
        // the item) and never a quantity (2026-08-20). The PO is Arabic, so
        // the name and description arrive in Arabic from Master Data.
        r.name = s.name;
        r.description = s.description || r.description;
        r.unit        = s.unit        || r.unit;
        // The remembered PICTURE (2026-08-11). A reference, not bytes, so it
        // arrives after the row is already usable. Re-read state.rows[ri]
        // inside the callback: the row can be deleted while we wait.
        const refs = (s.data && s.data.image_refs) || null;
        if (refs) {
            fetchRememberedImages(refs).then((imgs) => {
                const live = state.rows[ri];
                if (!live) return;
                let touched = false;
                Object.entries(imgs).forEach(([k, uri]) => {
                    if (live[k]) return;      // never overwrite a picture the user set
                    live[k] = uri;
                    paintRowImageCell(
                        document.querySelector(`#rowsBody tr[data-i="${ri}"]`), k, uri);
                    touched = true;
                });
                if (touched) {
                    state.dirtySinceSave = true;
                    if (typeof updateSplitWarning === "function") updateSplitWarning();
                }
            }).catch(() => {});
        }
        if (tr) {
            // Text columns are textareas now; the qty cell is still an input.
            // Use a key-only selector so the picker finds either tag.
            ["name", "description", "unit", "company"].forEach(k => {
                const el = tr.querySelector(`[data-k="${k}"]`);
                if (el) {
                    el.value = r[k] || "";
                    if (el.tagName === "TEXTAREA") autoResizePoTextarea(el);
                }
            });
        }
    } else {
        // Company-only suggestion: fill ONLY the company cell.
        r.company = s.name;
        if (tr) {
            const el = tr.querySelector('[data-k="company"]');
            if (el) {
                el.value = r.company;
                if (el.tagName === "TEXTAREA") autoResizePoTextarea(el);
            }
        }
    }

    hideAc();
    state.dirtySinceSave = true;
    if (ri === state.rows.length - 1) addRow();
}

document.addEventListener("keydown", (e) => {
    if (!acTarget || document.getElementById("acBox").hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); acIdx = Math.min(acIdx + 1, acItems.length - 1); refreshAcActive(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); acIdx = Math.max(acIdx - 1, 0); refreshAcActive(); }
    else if (e.key === "Enter" && acIdx >= 0) { e.preventDefault(); pickSuggestion(acIdx); }
    else if (e.key === "Tab"   && acIdx >= 0) { e.preventDefault(); pickSuggestion(acIdx); }
    else if (e.key === "Escape") { hideAc(); }
});
function refreshAcActive() {
    document.querySelectorAll(".ac-item").forEach((el, i) =>
        el.classList.toggle("active", i === acIdx));
}

// ============================================================
// Buttons
// ============================================================
function resetEditor() {
    state.rows = [];
    state.mode = "new";
    state.editPoNo = null;
    clearPendingDoc();                    // a New PO replaces any unsaved duplicate
    state.dirtySinceSave = false;
    state.signingOverlay = null;
    state.autofitRows = docAutofitDefault("po");
    setPoSourceQuote("", false);
    document.getElementById("poReference").value = "";
    // Phase X.25 - a new order ALWAYS starts at today's date. resetEditor runs
    // on "New" and after an export-save; without this the #today input kept the
    // date of a previously-opened PO (loadForEdit writes it), so a new PO
    // inherited that old date instead of today.
    applyPoDateToday();
    poRefreshSignChip();
    poSyncAutofitButton();
    document.getElementById("rowsBody").innerHTML = "";
    addRow();
    if (window.refreshPoLinkButtons) window.refreshPoLinkButtons();   // disable on a fresh PO
    // Phase X.26 - a fresh PO is no longer "the open document". Drops the
    // highlight and puts the panel on today's month; see panelNewDocument.
    panelNewDocument("po");
}

// PO signed-status chip (Arabic labels). Reflects the live PO overlay.
function poSyncAutofitButton() {
    syncAutofitButton("btnPoAutofit", state.autofitRows);
}
window.poSyncAutofitButton = poSyncAutofitButton;

function poRefreshSignChip() {
    if (window.setSignChip) {
        window.setSignChip("docSignChip",
            window.overlaySigned && window.overlaySigned(state.signingOverlay),
            { yes: "موقّع", no: "غير موقّع" });
    }
}
window.poRefreshSignChip = poRefreshSignChip;

// Phase K.1 - register the PO editor with the signing module.
if (window.signingRegister) {
    window.signingRegister({
        docType: "po",
        getOverlay: () => state.signingOverlay || null,
        setOverlay: (ov) => { state.signingOverlay = ov; state.dirtySinceSave = true; poRefreshSignChip(); },
    });
}

// Bulk paste/import - insert parsed rows through the normal addRow path (PO).
function poBulkInsert(objs, replace) {
    const tb = document.getElementById("rowsBody");
    if (replace) {
        state.rows = [];
        if (tb) tb.innerHTML = "";
    } else {
        while (state.rows.length && !hasAnyContent(state.rows[state.rows.length - 1])) {
            state.rows.pop();
            if (tb && tb.lastElementChild) tb.removeChild(tb.lastElementChild);
        }
    }
    (objs || []).forEach(o => addRow(o));
    if (!state.rows.length || hasAnyContent(state.rows[state.rows.length - 1])) addRow();
    state.dirtySinceSave = true;
    updateRowCount();
    updateSplitWarning();
}

if (window.bulkPasteRegister) {
    window.bulkPasteRegister({
        docType: "po",
        buttonId: "btnPoPaste",
        getColumns: () => ([
            { key: "name",        head: "المادة",  kind: "text" },
            { key: "description", head: "الوصف",   kind: "text" },
            { key: "unit",        head: "الوحدة",  kind: "text" },
            { key: "qty",         head: "العدد",   kind: "num"  },
            { key: "company",     head: "لشركة",   kind: "text" },
        ]),
        primaryKey: () => "name",
        addRows: poBulkInsert,
    });
}

// ============================================================
// Phase X.21 - Excel-style grid paste on the PO items table (#rowsBody).
// Mirrors the QUT / INV / DN wiring (grid_paste.js): copy a column/block in
// Excel, click the first PO cell, Ctrl+V → fills DOWN + ACROSS, auto-appends
// rows, and Ctrl+Z undoes the paste. The PO table previously had NO in-place
// grid paste (only the import wizard), so pasting a column into one cell did
// nothing to the rest - this closes that gap.
// NOTE: grid_paste.js is loaded AFTER app.js, so window.setupGridPaste is not
// defined at app.js eval time - defer wiring until it exists / DOM is ready.
// ============================================================
function _pushEmptyPoRow() {
    state.rows.push({ name: "", description: "", unit: "", qty: "", company: "" });
}
function _wirePoGridPaste() {
    const tbody = document.getElementById("rowsBody");
    if (!tbody || !window.setupGridPaste) return;
    const _renderAfterPaste = () => {
        fullRenderRows();
        // Keep a trailing blank row so the user can keep typing after a paste.
        if (filledRows().length === state.rows.length) addRow();
        updateSplitWarning();
        updateRowCount();
    };
    window.setupGridPaste(tbody, {
        columns: () => ([
            { key: "name",        kind: "text" },
            { key: "description", kind: "text" },
            { key: "unit",        kind: "text" },
            { key: "qty",         kind: "num"  },
            { key: "company",     kind: "text" },
        ]),
        rowCount:    () => state.rows.length,
        addEmptyRow: () => _pushEmptyPoRow(),
        setCell:     (i, k, v) => { if (state.rows[i]) { state.rows[i][k] = v; state.dirtySinceSave = true; } },
        fullRender:  _renderAfterPaste,
        snapshot:    () => JSON.parse(JSON.stringify(state.rows)),
        restore:     (snap) => { state.rows = snap; state.dirtySinceSave = true; _renderAfterPaste(); },
    });
}
if (window.setupGridPaste) _wirePoGridPaste();
else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _wirePoGridPaste);
else setTimeout(_wirePoGridPaste, 0);

// New. The guard decides whether to ask; this used to test
// `filledRows().length === 0` and never looked at the dirty flag, so opening
// an order from the Open panel and pressing New always prompted even though
// nothing had been edited (Waleed, 2026-10-01).
async function onNewClicked() {
    if (!(await window.unsavedGuard.confirmNew("po"))) return;
    await initNextPoNumber();
    resetEditor();
}

/**
 * Check every filled row for an empty qty (العدد). Returns the 1-based
 * row indexes (1, 2, 3...) of rows missing a qty.
 */
function rowsMissingQty(rows) {
    const missing = [];
    rows.forEach((r, i) => {
        const q = String(r.qty ?? "").trim();
        if (!q) missing.push(i + 1);
    });
    return missing;
}

/**
 * Modal-based validation prompt for empty qty cells. A toast at the
 * bottom is too easy to miss - a modal forces acknowledgement.
 */
async function showMissingQtyModal(missing) {
    const label = missing.length === 1 ? "السطر" : "الأسطر";
    await showModal({
        title: "العدد فارغ",
        body: `لا يمكن المتابعة. الرجاء تعبئة خانة العدد في ${label}: ${missing.join("، ")}`,
        buttons: [
            { label: "حسناً", value: "ok", cls: "primary" },
        ],
    });
}

// Live PO editor column widths (Excel-style resizing) -> {key: pct}. The PO
// table is RTL and class-sized, but it carries inline widths + data-col-key so
// the shared reader works the same as for QUT/INV/DN.
function poColWidths() {
    return window.readEditorColWidths
        ? window.readEditorColWidths(document.querySelector("#editorTable thead"))
        : null;
}

async function previewPdf() {
    const rows = filledRows();
    if (rows.length === 0) { toast("لا يوجد مواد لمعاينتها", true); return; }
    const missing = rowsMissingQty(rows);
    if (missing.length > 0) { await showMissingQtyModal(missing); return; }

    const btn = document.getElementById("btnPreview");
    await withLoading(btn, async () => {
        const r = await api().preview_pdf(state.poNo, state.poDate, rows,
                                          state.signingOverlay || null,
                                          poColWidths(),
                                          editorLayoutPayload(state.autofitRows));
        if (!r.ok) { toast("خطأ: " + r.error, true); return; }
        await api().open_path(r.path);
    });
}

async function printPO() {
    const rows = filledRows();
    if (rows.length === 0) { toast("لا يوجد مواد للطباعة", true); return; }
    const missing = rowsMissingQty(rows);
    if (missing.length > 0) { await showMissingQtyModal(missing); return; }

    const btn = null;                      // Print left the toolbar; the busy bar covers it
    await withLoading(btn, async () => {
        const r = await api().preview_pdf(state.poNo, state.poDate, rows,
                                          state.signingOverlay || null,
                                          poColWidths(),
                                          editorLayoutPayload(state.autofitRows));
        if (!r.ok) { toast("خطأ: " + r.error, true); return; }
        const pr = await api().print_file(r.path);
        toast(pr && pr.ok ? "أُرسل للطباعة" : (pr && pr.error) || "فشلت الطباعة", !(pr && pr.ok));
    });
}

async function savePO(opts = {}) {
    const rows = filledRows();
    if (rows.length === 0) { toast("لا يوجد مواد للحفظ", true); return false; }

    // Pick up the latest typed PO number (the input field is the source of truth).
    const typed = (document.getElementById("poNo").value || "").trim();
    if (!typed) { toast("رقم الطلب فارغ", true); return false; }
    state.poNo = typed;
    const linkedQuote = getPoSourceQuote();
    const reference = document.getElementById("poReference").value.trim();

    const missing = rowsMissingQty(rows);
    if (missing.length > 0) { await showMissingQtyModal(missing); return false; }

    // Uniqueness check: warn if the typed number already exists, unless
    // we're updating the same existing PO (edit mode, number unchanged).
    const sameAsExisting = (state.mode === "edit" && state.editPoNo === typed);
    if (!sameAsExisting) {
        const exists = await api().check_po_exists(typed);
        if (exists.exists) {
            const choice = await showModal({
                title: "رقم الطلب موجود مسبقاً",
                body: `طلب برقم ${typed} موجود بالفعل. الحفظ سيستبدل الملف القديم. هل تريد المتابعة؟`,
                buttons: [
                    { label: "إلغاء", value: "cancel", cls: "ghost" },
                    { label: "استبدال", value: "ok", cls: "primary" },
                ],
            });
            if (choice !== "ok") return false;
        }
    }

    if (!opts.skipConfirm) {
        const choice = await showModal({
            title: "تأكيد الحفظ",
            body: `سيتم حفظ الطلب ${state.poNo}${state.mode === "edit" ? " (تعديل)" : ""}. هل تريد المتابعة؟`,
            buttons: [
                { label: "إلغاء", value: "cancel", cls: "ghost" },
                { label: "حفظ",   value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return false;
    }

    const btn = firstEl("btnPoStay");      // #btnSave was Export, removed 2026-07-24
    let r;
    await withLoading(btn, async () => {
        // True update only if we're editing AND the number wasn't changed.
        // If the user typed a new number in edit mode, fall through to save_po
        // which creates a fresh record under the new number.
        if (state.mode === "edit" && state.editPoNo === typed) {
            r = await api().update_po(state.editPoNo, state.poDate, rows,
                                      state.signingOverlay || null,
                                       poColWidths(),
                                       editorLayoutPayload(state.autofitRows),
                                       linkedQuote,
                                       reference);
            if (r.ok) {
                if (!opts.stay && r.path) {
                    await poAfterExport(r.path, state.editPoNo);
                } else {
                    toast(`تم تحديث ${state.editPoNo}`);
                }
                // Phase L.4.5 - Export should reset to a fresh doc even from
                // edit mode. Save (stay) leaves the editor as-is.
                if (!opts.stay) {
                    await initNextPoNumber();
                    resetEditor();
                }
            }
        } else {
            r = await api().save_po(state.poNo, state.poDate, rows,
                                    state.signingOverlay || null,
                                    poColWidths(),
                                    editorLayoutPayload(state.autofitRows),
                                    linkedQuote,
                                    reference);
            if (r.ok) {
                // 2026-07-29 - always exactly one PO now (it paginates instead
                // of splitting), but `saved` stayed a list so this contract
                // didn't have to change.
                const list = r.saved.map(s => s.po_no).join(", ");
                if (opts.stay) {
                    toast(`تم حفظ: ${list}`);
                    // Save (stay): switch to edit mode on the saved PO so
                    // subsequent Saves update the same record.
                    const firstNo = r.saved[0].po_no;
                    state.mode = "edit";
                    state.editPoNo = firstNo;
                    state.poNo = firstNo;
                    const el = document.getElementById("poNo");
                    if (el) el.value = firstNo;
                    window.__currentEditorDoc = { kind: "po", no: firstNo };
                    window.setModeChip(true, "وضع التعديل");
                    if (window.refreshPoLinkButtons) window.refreshPoLinkButtons();   // Phase Y.7 - now linkable
                } else {
                    // Export: run the user's "after export" action on the (first)
                    // generated PDF, then reset to a fresh PO number.
                    const p = r.saved[0] && r.saved[0].path;
                    if (p) { await poAfterExport(p, list); }
                    else { toast(`تم حفظ: ${list}`); }
                    await initNextPoNumber();
                    resetEditor();
                }
            }
        }
    });
    /* Someone else saved this number a moment ago (multi-user, 2026-10-22):
       say so and fetch the next one, instead of a bare 'Save error' that
       leaves the stale number in the box. */
    if (r && r.number_taken) {
        toast(`الرقم ${r.doc_no} استُخدم للتو من شخص آخر. احفظ مرة أخرى لأخذ الرقم التالي.`, true);
        await initNextPoNumber();
        return false;
    }
    if (!r || !r.ok) { toast("خطأ: " + (r && r.error || "غير معروف"), true); return false; }
    state.dirtySinceSave = false;
    if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect the save in the panel
    // The order is saved and reported. NOW offer to complete any item the
    // catalogue only knows in Arabic, so the delivery note or invoice made from
    // this order later needs no typing. Deliberately after the save and after
    // `return`-worthy failures: it must never make a successful save look
    // failed, so it is awaited but its outcome is ignored ([[07-FRONTEND-SUPPORT]]).
    if (window.poReviewNewItems) {
        const savedNo = (state.mode === "edit" && state.editPoNo) || state.poNo;
        if (savedNo) { try { await window.poReviewNewItems(savedNo); } catch (e) {} }
    }
    return true;
}

// ============================================================
// Modal (promise-based) - animated entry/exit.
// Pattern: add .entering class while hidden, unhide, then remove
// .entering on the next frame so the transition runs from the
// initial state to the resting state.
// ============================================================
// Phase O - keyboard shortcut cheat-sheet (press ?). A self-contained overlay
// (showModal renders text only) listing the app's shortcuts so they're
// discoverable beyond button tooltips.
function showShortcutSheet() {
    if (document.getElementById("shortcutSheet")) return;
    const groups = [
        { title: "Global", items: [
            ["Ctrl + N", "New document"],
            ["Ctrl + O", "Open / files panel"],
            ["Ctrl + P", "Preview PDF"],
            ["Ctrl + S", "Save (stays in the editor)"],
            ["Esc", "Close / go back"],
            ["?", "This shortcut sheet"],
            ["Ctrl + Shift + H", "Hide / show cost, profit and the profit cards"],
            ["Ctrl + L", "Lock: close the gate over the app"],
        ]},
        { title: "Item tables", items: [
            ["Arrow keys", "Move between cells"],
            ["Ctrl + V", "Paste a block from Excel"],
            ["Ctrl + Z", "Undo one step (edit, paste or row delete)"],
            ["Ctrl + Shift + Z", "Redo (Ctrl + Y also works)"],
            ["Right-click", "Paste-here menu"],
        ]},
        { title: "Image cells (Quotation)", items: [
            ["Single click", "Select the cell (then Ctrl+V)"],
            ["Double click", "Browse for an image file"],
            ["Drag & drop", "Drop an image straight in"],
        ]},
    ];
    const html = groups.map(g => `
        <div class="ks-group">
            <div class="ks-group-title">${esc(g.title)}</div>
            ${g.items.map(([k, d]) => `<div class="ks-row"><kbd>${esc(k)}</kbd><span>${esc(d)}</span></div>`).join("")}
        </div>`).join("");
    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). */
    const h = window.Popup.open({
        title: "Keyboard shortcuts",
        size: "lg",
        dir: "ltr",
        className: "ks-modal",
        body: html, html: true,
    });
    h.el.id = "shortcutSheet";               // the re-open guard looks for this
}
window.showShortcutSheet = showShortcutSheet;

// `html: true` opts THIS call into rich body markup (2026-07-24). Default stays
// textContent - callers pass user/document data into `body`, so HTML must never
// be the default. Only pass html:true with markup you built and escaped here.
/* The app-wide confirm dialog. Since 2026-08-26 this is a thin wrapper over
   `Popup.open` (static/popup.js) rather than its own implementation, so all 55
   call sites keep this exact contract AND inherit every popup rule: a close X,
   the dim background as a way out, focus trapped over the whole panel rather
   than just the buttons, the page behind held still, and one set of widths.

   The contract callers rely on, unchanged:
     showModal({title, body, buttons:[{label, value, cls}], html}) -> Promise
     Esc resolves "cancel" when a cancel button exists, else null.
   `html: true` stays opt-in - document and client data flows through `body`. */
function showModal({ title, body, buttons, html, size }) {
    return window.Popup.open({
        title: title || "تأكيد",
        body, html,
        size: size || "md",
        actions: (buttons || []).map(b => ({
            label: b.label, value: b.value, cls: b.cls || "ghost",
        })),
        /* A few callers reach into this dialog by id after opening it -
           `#modalBody` (master_data_fill wires its inputs there),
           `#modalActions` (inv.js and item_master.js enable/disable the primary
           button while it is open). The ids used to belong to a static block in
           index.html; that block is gone, so they are stamped onto the live
           panel instead and every one of those callers keeps working. */
        onOpen: (h) => {
            h.el.id = "modalBackdrop";
            h.panel.classList.add("modal-legacy");
            const t = h.panel.querySelector(".popup-title");
            if (t) t.id = "modalTitle";
            h.body.id = "modalBody";
            const a = h.panel.querySelector(".popup-actions");
            if (a) a.id = "modalActions";
        },
    }).result;
}
window.showModal = showModal;

// ============================================================
// Open existing PO
// ============================================================
const MONTH_NAMES = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

function populateOpenSelectors() {
    const sel = document.getElementById("openMonth");
    MONTH_NAMES.forEach((n, i) => {
        const o = document.createElement("option");
        o.value = i + 1; o.textContent = n;
        sel.appendChild(o);
    });
    const t = state.today;
    sel.value = t.month;
    document.getElementById("openYear").value = t.year;
}

function showOpenView() {
    // Phase M.6 - the right panel replaces the standalone Open view. The PO
    // editor stays put; we just uncollapse the panel + focus search.
    if (window.OpenPanel) window.OpenPanel.focusSearch();
    else refreshPoList();
}
function showEditorView() {
    hideAllViews();
    document.getElementById("editorView").hidden = false;
    document.getElementById("poStrip").hidden = false;
    document.getElementById("poActions").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("طلب تجهيز مواد");
    refreshUniversalSettingsBtn();
    poSyncAutofitButton();
    refreshPoSavedState();
}

function renderSkeletonRows(count = 4) {
    const box = document.getElementById("poList");
    let html = "";
    for (let i = 0; i < count; i++) {
        html += `
            <div class="skel-row">
                <div>
                    <div class="skel lg"></div>
                    <div class="skel md"></div>
                </div>
                <div class="row-actions">
                    <div class="skel btn"></div>
                    <div class="skel btn"></div>
                </div>
            </div>`;
    }
    box.innerHTML = html;
}

// ----------------------------------------------------------------
// Phase X.8 - left-panel day grouping. Lists come from the backend already
// sorted newest-created first; we drop a date divider whenever the created day
// changes so each day's docs are visually separated.
// ----------------------------------------------------------------
function panelDayKey(ts) {
    if (!ts) return "earlier";
    const d = new Date(ts);
    if (isNaN(d)) return "earlier";
    return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate();
}
function panelDayLabel(ts) {
    if (!ts) return "Earlier";
    const d = new Date(ts);
    if (isNaN(d)) return "Earlier";
    const now = new Date();
    const k = x => x.getFullYear() + "-" + x.getMonth() + "-" + x.getDate();
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (k(d) === k(now)) return "Today";
    if (k(d) === k(y)) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
}
function panelDaySepHTML(ts) {
    const label = panelDayLabel(ts);
    const cls = "panel-day-sep" + (label === "Today" ? " is-today" : "");
    return `<div class="${cls}"><span>${esc(label)}</span></div>`;
}
function panelDaySepEl(ts) {
    const label = panelDayLabel(ts);
    const el = document.createElement("div");
    el.className = "panel-day-sep" + (label === "Today" ? " is-today" : "");
    el.innerHTML = `<span>${esc(label)}</span>`;
    return el;
}
window.panelDayKey = panelDayKey;
window.panelDayLabel = panelDayLabel;
window.panelDaySepHTML = panelDaySepHTML;
window.panelDaySepEl = panelDaySepEl;

// ----------------------------------------------------------------
// Phase X.9 - import preview. Before importing a quotation/DN into a DN or
// invoice, show a small popup of the selected document (meta + its items) so
// the user can confirm it's the right one. Shared by the DN and INV importers.
// ----------------------------------------------------------------
async function showImportPreview(opts) {
    const { kind, no, onImport } = opts || {};
    if (!no) return;
    let rec = null;
    try {
        rec = kind === "dn" ? await api().load_dn(no) : await api().load_quote(no);
    } catch (e) { rec = null; }
    if (!rec) { toast("Couldn't load " + no + " for preview", true); return; }

    const KINDLBL = { quote: "Quotation", dn: "Delivery Note" };
    const client = rec.client_name || rec.client || "-";
    const date = rec.quote_date || rec.dn_date || "";
    const rows = Array.isArray(rec.rows) ? rec.rows : [];

    // Prefer the document's own display columns; otherwise infer from the rows
    // (dropping costing-only / image fields that never print).
    let cols = (Array.isArray(rec.columns) && rec.columns.length)
        ? rec.columns.map(c => ({ key: c.key, head: c.head || c.key }))
        : null;
    if (!cols) {
        const SKIP = new Set(["cost", "cost_qty", "cost_sell", "image", "img", "_id"]);
        const keys = rows.length ? Object.keys(rows[0]).filter(k => !SKIP.has(k)) : [];
        cols = keys.map(k => ({ key: k, head: k }));
    }
    const MAXR = 12;
    const shown = rows.slice(0, MAXR);
    const cell = v => esc(v == null ? "" : String(v));
    const thead = `<tr>${cols.map(c => `<th>${esc(c.head)}</th>`).join("")}</tr>`;
    const tbody = shown.map(r =>
        `<tr>${cols.map(c => `<td>${cell(r[c.key])}</td>`).join("")}</tr>`).join("");
    const more = rows.length > MAXR
        ? `<div class="imprev-more">+${rows.length - MAXR} more item${rows.length - MAXR === 1 ? "" : "s"}…</div>` : "";

    /* Shared Popup ([[14-UI-CONTRACTS]]). */
    const _h = window.Popup.open({
        title: `Preview - ${no}`,
        size: "lg",
        dir: "ltr",
        className: "imprev-modal",
        html: true,
        actions: [
            { label: "Back", value: "cancel", cls: "ghost" },
            { label: `Import this ${(KINDLBL[kind] || "document").toLowerCase()}`,
              value: "import", cls: "primary" },
        ],
        body: `
                <div class="imprev-meta">
                    <span class="imprev-chip imprev-chip-${esc(kind)}">${esc(KINDLBL[kind] || "Document")}</span>
                    <span class="imprev-client">${esc(client)}</span>
                    ${date ? `<span class="imprev-dot">·</span><span>${esc(date)}</span>` : ""}
                    <span class="imprev-dot">·</span><span>${rows.length} item${rows.length === 1 ? "" : "s"}</span>
                </div>
                ${rows.length
                    ? `<div class="imprev-tablewrap"><table class="imprev-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>${more}</div>`
                    : `<div class="imprev-empty">This document has no items.</div>`}`,
    });
    if (await _h.result === "import" && typeof onImport === "function") await onImport();
}
window.showImportPreview = showImportPreview;

async function refreshPoList() {
    const box = document.getElementById("poList");
    renderSkeletonRows(4);

    const y = +document.getElementById("openYear").value;
    const m = +document.getElementById("openMonth").value;
    const list = await api().list_pos_for_month(y, m);

    box.innerHTML = "";
    // The unsaved duplicate, if this pane is its home. Rendered BEFORE the
    // empty-state check - in a month whose first document is a duplicate the
    // list is empty, and an early return would hide the only row there is.
    const pendingRow = pendingRowEl("po", y, m, list, "po_no");
    if (list.length === 0 && !pendingRow) {
        box.innerHTML = `
            <div class="empty-state">
                <div class="icon">∅</div>
                <div class="title">لا توجد طلبات في هذا الشهر</div>
                <div class="hint">اختر شهراً آخر، أو ارجع لإنشاء طلب جديد</div>
            </div>`;
        return;
    }
    if (pendingRow) box.appendChild(pendingRow);
    let _lastDay = null;
    list.forEach(p => {
        const dk = panelDayKey(p.created_at);
        if (dk !== _lastDay) { box.appendChild(panelDaySepEl(p.created_at)); _lastDay = dk; }
        const row = document.createElement("div");
        row.className = "po-row" + (p.editable ? " is-clickable" : "");
        if (p.editable) { row.dataset.edit = p.po_no; row.title = "اضغط على الصف لفتحه في وضع التعديل"; }
        const statusHtml = window.panelStatusHTML ? window.panelStatusHTML("po", p, { yes: "موقّع", no: "غير موقّع" }) : "";
        const dateLabel = p.editable ? (p.updated_at || "").slice(0, 10) : "للعرض فقط";
        row.innerHTML = `
            ${window.panelCardMainHTML({ no: p.po_no, date: dateLabel, reference: p.reference, statuses: statusHtml })}
            <div class="row-actions">
                <button class="ghost" data-act="open" data-path="${esc(p.path)}">
                    ${ICON.externalLink}<span>فتح PDF</span>
                </button>
                <button class="ghost" data-act="reveal" data-path="${esc(p.path)}" title="فتح موقع المجلد">
                    ${ICON.folder}<span>المجلد</span>
                </button>
                <button class="ghost" data-act="copy" data-path="${esc(p.path)}" data-no="${esc(p.po_no)}" data-signed="${p.signed ? "1" : "0"}" title="نسخ الملف للحافظة">
                    ${ICON.copy}<span>نسخ</span>
                </button>
                <button class="ghost" data-act="print" data-path="${esc(p.path)}" title="طباعة">
                    ${ICON.printer}<span>طباعة</span>
                </button>
                ${p.editable ? `<button class="ghost" data-act="duplicate" data-kind="po" data-no="${esc(p.po_no)}" title="نسخ الطلب كطلب جديد">
                    ${ICON.duplicate}<span>نسخة جديدة</span>
                </button>` : ""}
                <button class="ghost danger-text" data-act="delete" data-path="${esc(p.path)}" data-label="${esc(p.po_no)}" title="حذف الملف">
                    ${ICON.trash}<span>حذف</span>
                </button>
                ${window.submitBtnHTML ? window.submitBtnHTML("po", p.po_no, !!p.submitted) : ""}
            </div>
        `;
        box.appendChild(row);
    });
    box.querySelectorAll("button[data-act='open']").forEach(b =>
        b.onclick = (e) => { e.stopPropagation(); api().open_path(b.dataset.path); });
    box.querySelectorAll("button[data-act='reveal']").forEach(b =>
        b.onclick = (e) => { e.stopPropagation(); api().reveal_in_folder(b.dataset.path); });
    box.querySelectorAll("button[data-act='copy']").forEach(b =>
        b.onclick = safe((e) => { e.stopPropagation(); return window.copyDocumentFile("po", b.dataset.no, b.dataset.path, b.dataset.signed === "1"); }, "نسخ الملف"));
    box.querySelectorAll("button[data-act='print']").forEach(b =>
        b.onclick = (e) => { e.stopPropagation(); printPdfFile(b.dataset.path); });
    box.querySelectorAll("button[data-act='delete']").forEach(b =>
        b.onclick = (e) => { e.stopPropagation(); confirmAndDeleteDocument("po", b.dataset.label, b.dataset.path, b.dataset.label, refreshPoList); });
    wireDuplicateButtons(box);
    if (window.wireSubmitButtons) window.wireSubmitButtons(box, list, "po_no");
    // Phase M.5b - clicking anywhere on an editable card loads it for edit.
    box.querySelectorAll(".po-row.is-clickable").forEach(row =>
        row.onclick = safe(() => loadForEdit(row.dataset.edit), "تحميل الطلب"));
}

// Summarise state.poFulfillment into [delivered, invoiced, paid] states.
// ("none"|"partial"|"full") for the editor header chip.
function poFulfillSummary() {
    const f = state.poFulfillment;
    const total = Array.isArray(state.rows) ? state.rows.length : 0;
    const st = (bucket) => {
        const n = f && f[bucket] ? Object.keys(f[bucket]).length : 0;
        if (!n) return "none";
        return (total && n >= total) ? "full" : "partial";
    };
    return [st("delivered"), st("invoiced"), st("paid")];
}

// Tint each editor row by its latest delivery/payment workflow stage.
// Index-based (valid right after load); a fresh render/edit re-runs or clears it.
function decoratePoFulfillment() {
    const body = document.getElementById("rowsBody");
    if (!body) return;
    const f = state.poFulfillment;
    body.querySelectorAll("tr").forEach((tr) => {
        tr.classList.remove(
            "po-row-delivered", "po-row-invoiced", "po-row-paid", "po-row-outstanding");
        const i = tr.dataset.i;
        if (i == null || !f) return;
        const delivered = f.delivered && (f.delivered[i] || f.delivered[String(i)]);
        const invoiced = f.invoiced && (f.invoiced[i] || f.invoiced[String(i)]);
        const paid = f.paid && (f.paid[i] || f.paid[String(i)]);
        if (paid) tr.classList.add("po-row-paid");
        else if (invoiced) tr.classList.add("po-row-invoiced");
        else if (delivered) tr.classList.add("po-row-delivered");
        else tr.classList.add("po-row-outstanding");
    });
}

async function loadForEdit(poNo) {
    const rec = await api().load_po(poNo);
    if (!rec) { toast("لا يمكن تحميل الطلب - غير موجود في قاعدة البيانات", true); return; }
    state.rows = rec.rows.slice();
    state.poNo = poNo;
    state.poDate = rec.po_date;
    state.mode = "edit";
    state.editPoNo = poNo;
    window.__currentEditorDoc = { kind: "po", no: poNo };   // Phase W - panel highlight
    state.signingOverlay = rec.signing_overlay || null;
    state.autofitRows = layoutAutofitValue(rec.layout, docAutofitDefault("po"));
    setPoSourceQuote(rec.source_quote_no || "", false);
    document.getElementById("poReference").value = rec.reference || "";
    poRefreshSignChip();
    poSyncAutofitButton();
    state.dirtySinceSave = false;
    document.getElementById("poNo").value = poNo;
    const dateEl = document.getElementById("today");
    if (dateEl && dateEl.tagName === "INPUT") {
        dateEl.value = displayToIso(rec.po_date) || "";
    } else if (dateEl) {
        dateEl.textContent = rec.po_date;
    }
    window.setModeChip(true, "وضع التعديل");
    showEditorView();
    window.__currentEditorDoc = { kind: "po", no: poNo };
    state.poFulfillment = rec.fulfillment || null;
    fullRenderRows();
    decoratePoFulfillment();          // Phase Y - tint delivered/paid/outstanding rows
    addRow(); // trailing blank
    updateRowCount();
    if (window.setPoFulfillChips) window.setPoFulfillChips(...poFulfillSummary());
    refreshPoLinkButtons();           // Phase Y.7 - enable link buttons + counts
    // Phase L.1.3 - fit multi-line text cells to their content on load.
    setTimeout(() => autoSizeAllTextareas(document.getElementById("editorTable")), 0);
    // Put the panel on THIS order's month and scroll to its row (2026-10-01).
    revealDocInPanel("po", poNo, rec.po_date);
}

// ============================================================
// Toast - slide up from bottom. Same direction in/out for spatial
// consistency. Exit is faster than enter (Emil's pattern: slow when
// the user is reading, fast when the system is responding).
// ============================================================
let toastT = null;
function toast(msg, isError = false) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "toast entering" + (isError ? " error" : "");
    el.hidden = false;
    // Next frame: drop .entering so the slide-up transition plays.
    requestAnimationFrame(() => requestAnimationFrame(() =>
        el.classList.remove("entering")));

    if (toastT) clearTimeout(toastT);
    toastT = setTimeout(() => {
        // Animate out by re-adding .entering, then hide after the transition.
        el.classList.add("entering");
        setTimeout(() => { el.hidden = true; }, 220);
    }, 3300);
}
