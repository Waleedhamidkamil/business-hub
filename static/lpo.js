/* ============================================================
   Client LPOs (2026-07-31)
   The documents clients send US: quotation (ours) -> LPO (theirs)
   -> delivery note (ours) -> invoice (ours).

   Deliberately NOT a document-type editor. We never generate an LPO,
   so there is no numbering, no preview, no print template - this is a
   store with a viewer, plus one action that matters: counter-sign the
   client's own PDF and send it back.

   The viewer renders the client's PDF with pdf.js onto a canvas and lets
   you DRAG a resizable box to place the signature/stamp, because their
   signature block is in a different spot on every client's form. Boxes
   are reported to the backend as fractions of the page (0-1), never
   pixels - so the zoom level can never leak into the stamped result.
   ============================================================ */

const lpoState = {
    rows: [],
    search: "",
    // The type tab (2026-10-03): all | materials | rental | open - the same chip
    // control the Quotation and Invoice lists use for their types.
    kind: "all",
    client: "all",
    // "Need action": only the LPOs whose next step is something to do.
    due: false,
    // The document zoom, kept for the session: "fit" (the default - the page
    // fills the width) or the pdf.js scale last chosen with + / -.
    zoom: "fit",
    viewer: null,          // { doc, page, scale, marks: [], lpo }
    // The signature and stamp images, fetched once per session by `lpoAssets()`
    // and dropped by `clearLpoAssets()` when Settings uploads a new one. They
    // are 679 KB and were previously re-fetched on every document open.
    assets: null,
};

window.lpoState = lpoState;     // exposed like invState, for tests + debugging

const LPO_STATUS = {
    received:     { label: "Received",     cls: "lpo-st-received" },
    acknowledged: { label: "Acknowledged", cls: "lpo-st-ack" },
    delivered:    { label: "Delivered",    cls: "lpo-st-delivered" },
    invoiced:     { label: "Invoiced",     cls: "lpo-st-invoiced" },
    closed:       { label: "Closed",       cls: "lpo-st-closed" },
};

function lpoMoney(v, cur) {
    const c = (cur || "USD").toUpperCase();
    if (c === "IQD") return "IQD " + Math.round(v || 0).toLocaleString("en-US");
    return "$" + (Number(v) || 0).toLocaleString("en-US",
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ============================================================
// Screen
// ============================================================
window.showLpos = async function showLpos() {
    hideAllViews();
    document.getElementById("lpoView").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand("Client LPOs");
    await refreshLpos();
};

/* Opening ONE LPO from anywhere else in the app (2026-09-12).

   The To-Do's billing reminder used to hand "lpo" to the editor map, which had
   no record loader to call and so landed on the whole Client LPOs list. Waleed
   asked for that LPO, not the list of them.

   The caller carries a number and usually a client, never the record - so the
   list is loaded (Previous/Next reads it anyway) and the row is matched. The
   client is optional because the row-action buttons only carry a number; a
   number-only match is the fallback, not the first try, since two clients can
   in principle use the same PO number. */
window.openLpoByNo = async function openLpoByNo(lpoNo, clientName) {
    const want = String(lpoNo || "").trim().toLowerCase();
    if (!want) return false;
    if (!lpoState.rows.length) {
        try { lpoState.rows = (await api().list_lpos("")) || []; }
        catch (e) { lpoState.rows = []; }
    }
    const no = (r) => String(r.lpo_no || "").trim().toLowerCase();
    let row = null;
    if (String(clientName || "").trim()) {
        let resolved;
        try { resolved = await api().get_lpo(clientName, lpoNo); }
        catch (e) { toast("Could not resolve this LPO: " + e, true); return false; }
        if (resolved) row = lpoState.rows.find(r => no(r) === want
            && r.client_name === resolved.client_name) || resolved;
    } else {
        const matches = lpoState.rows.filter(r => no(r) === want);
        if (matches.length > 1) {
            toast("More than one client uses this LPO number. Choose the client first.", true);
            return false;
        }
        row = matches[0];
    }
    if (!row) {
        toast(`${lpoNo} is not on file`, true);
        await showLpos();
        return false;
    }
    await showLpoDoc(row);
    return true;
};

async function refreshLpos() {
    try {
        lpoState.rows = (await api().list_lpos("")) || [];
    } catch (e) {
        lpoState.rows = [];
        toast("Could not load LPOs: " + e, true);
    }
    renderLpoList();
    // A quiet link under the table, not a banner over it: the references are
    // worth reviewing once, not worth a full-width bar every visit.
    const review = document.getElementById("lpoReferenceReview");
    if (review) {
        review.hidden = true;
        if (typeof api().lpo_reference_gaps === "function") {
            try {
                const gaps = await api().lpo_reference_gaps();
                review.hidden = !gaps.length;
                review.textContent = `${gaps.length} document${gaps.length === 1 ? " cites" : "s cite"} an LPO that is not on file - review`;
                review.onclick = () => openLpoReferenceReview(gaps);
            } catch (e) { toast("Could not check document references",true); }
        }
    }
}
window.refreshLpos = refreshLpos;

function openLpoReferenceReview(gaps) {
    window.Popup.open({title:"Document references to review",size:"xl",html:true,
        body:`<p>These issued documents cite an LPO that is not on file for the same client.
        Check the original paperwork before correcting a reference or adding an LPO.</p>
        <table class="lpo-lines-tbl"><thead><tr><th>Document</th><th>Client</th><th>LPO reference</th><th>Action</th></tr></thead>
        <tbody>${gaps.map((r,i)=>`<tr><td>${esc(r.doc_no)}</td><td>${esc(r.client_name)}</td><td>${esc(r.lpo_no)}</td>
        <td><button type="button" class="ghost" data-review-index="${i}">Open document</button></td></tr>`).join("")}</tbody></table>`,
        actions:[{label:"Close",value:null,cls:"ghost"}],
        onOpen:h=>h.body.querySelectorAll('[data-review-index]').forEach(b=>{
            b.onclick=safe(async()=>{
                const row=gaps[Number(b.dataset.reviewIndex)];
                const loader=row.kind==='dn' ? window.loadDnForEdit : window.loadInvForEdit;
                if(typeof loader!=='function'){toast("This document cannot be opened here",true);return;}
                h.close("open");await loader(row.doc_no);
            },"Open document for review");
        }),
    });
}

/* The kind of PO, lower-cased, with the old default. */
function lpoKind(r) { return String((r && r.kind) || "materials").toLowerCase(); }

/* The type tabs, in the order of the chip row (2026-10-03). The same control
   the Quotation and Invoice lists use for Materials / Equipment / Manpower /
   Service, because Waleed asked for exactly that. */
const LPO_TABS = [
    { key: "all", label: "All" },
    { key: "materials", label: "Materials" },
    { key: "rental", label: "Rentals" },
    { key: "open", label: "Supply contracts" },
];

function visibleLpos() {
    const q = (lpoState.search || "").trim().toLowerCase();
    return lpoState.rows.filter(r => {
        if (lpoState.kind !== "all" && lpoKind(r) !== lpoState.kind) return false;
        if (lpoState.client !== "all" && r.client_name !== lpoState.client) return false;
        if (lpoState.due && !(r.next_step && r.next_step.action)) return false;
        if (!q) return true;
        // An item, a delivery note or an invoice number finds its LPO too:
        // "which LPO were the office chairs on?" is a real question.
        return [r.lpo_no, r.client_name, r.source_quote_no, r.erp_no, r.project, r.notes,
                ...(r.dns || []), ...(r.invoices || []),
                ...(Array.isArray(r.rows) ? r.rows.map(l => l && l.item) : [])]
            .some(v => String(v || "").toLowerCase().includes(q));
    });
}

/* The three figures over the list (2026-10-03; four cards before).

   Open supply contracts are left out of both money figures on purpose: their
   stored total is a UNIT price (CSSP-OPEN-0001 prints 0.77, a litre of diesel),
   so summing them produces a number that means nothing. An LPO closed by hand
   is left out of "not invoiced yet" - the rest of its value will never be
   billed, and counting it would leave a debt on screen that nobody is owed.
   "Need action" counts the rows whose next step is something to do; it is also
   the toggle that shows only those rows. */
function lpoKpis(rows) {
    const priced = rows.filter(r => lpoKind(r) !== "open");
    const ordered = priced.reduce((a, r) => a + (Number(r.total_usd) || 0), 0);
    const open = priced.filter(r => !r.closed_at)
        .reduce((a, r) => a + (Number(r.remaining_usd) || 0), 0);
    const action = rows.filter(r => r.next_step && r.next_step.action).length;
    return [
        { k: "Ordered", v: lpoMoney(ordered) },
        { k: "Not invoiced yet", v: lpoMoney(open) },
        { k: "Need action", v: String(action), alert: action > 0, toggle: true },
    ];
}

function renderLpoKpis(rows) {
    const box = document.getElementById("lpoKpis");
    if (!box) return;
    box.innerHTML = lpoKpis(rows).map(c => c.toggle
        ? `<button type="button" id="lpoDueFilter" class="lpo-fig lpo-fig-toggle${
              c.alert ? " is-alert" : ""}${lpoState.due ? " is-on" : ""}"
              aria-pressed="${lpoState.due ? "true" : "false"}"
              title="${lpoState.due ? "Show every LPO" : "Show only the LPOs that need something done"}">
              <span class="lpo-fig-k">${esc(c.k)}</span><span class="lpo-fig-v">${esc(c.v)}</span>${
              lpoState.due ? `<span class="lpo-fig-on">showing only these - show all</span>` : ""}</button>`
        : `<div class="lpo-fig"><span class="lpo-fig-k">${esc(c.k)}</span><span class="lpo-fig-v">${esc(c.v)}</span></div>`
    ).join("");
    const toggle = document.getElementById("lpoDueFilter");
    if (toggle) toggle.onclick = () => { lpoState.due = !lpoState.due; renderLpoList(); };
}

/* Type tabs, and client tabs only when there is more than one client - one
   chip saying "HDEC" above eight HDEC rows is chrome, not a filter. Counts are
   over everything on file, as on the Quotation list. */
function renderLpoChips() {
    const rows = lpoState.rows;
    const chips = window.renderChipRow || renderChipRow;
    const count = (k) => rows.filter(r => k === "all" || lpoKind(r) === k).length;
    chips(document.getElementById("lpoKindChips"),
        LPO_TABS.map(t => ({ key: t.key, label: t.label, count: count(t.key) })),
        lpoState.kind, (k) => { lpoState.kind = k; renderLpoList(); });
    const names = [...new Set(rows.map(r => r.client_name).filter(Boolean))].sort();
    if (lpoState.client !== "all" && !names.includes(lpoState.client)) lpoState.client = "all";
    const box = document.getElementById("lpoClientChips");
    if (!box) return;
    if (names.length < 2) { box.innerHTML = ""; return; }
    chips(box, [{ key: "all", label: "All clients", count: rows.length }].concat(
        names.map(n => ({ key: n, label: n, count: rows.filter(r => r.client_name === n).length }))),
        lpoState.client, (k) => { lpoState.client = k; renderLpoList(); });
}

/* Where one LPO has got to, in a sentence (2026-09-11).

   Different per kind because the QUESTION is different. A materials PO is money
   against money. A rental is MONTHS - the months are what was bought, and a
   fully paid-up rental is still running. A supply contract has no total to be a
   fraction of at all: CSSP-OPEN-0001 prints 0.77, the price of a litre of
   diesel, and reporting "1,240 of 0.77" would be nonsense.

   Kept free of the DOM so the sentences can be tested directly. */
function lpoProgress(r) {
    // Rollup fields are canonical USD, even when the original LPO is in IQD.
    const m = (v) => "$" + (Number(v) || 0).toLocaleString("en-US",
            { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const kind = String(r.kind || "materials").toLowerCase();

    // A contract is counted in MONTHS (Waleed, 2026-10-06): how many months
    // have their invoice, and how many ended without one.
    const missed = Number(r.months_unbilled) || 0;
    const late = missed ? ` - ${missed} month${missed === 1 ? "" : "s"} missed` : "";
    if (kind === "open") {
        const n = (r.invoices || []).length;
        return `${m(r.invoiced_usd)} invoiced on ${n} invoice${n === 1 ? "" : "s"}${late}`;
    }
    if (kind === "rental") {
        const done = Number(r.months_invoiced) || 0;
        const all = Number(r.months_total) || 0;
        // "0 of 0" is worse than saying nothing: a rental with no period
        // recorded simply has no month count to report yet.
        const months = all
            ? `${done} of ${all} months invoiced`
            : `${done} month${done === 1 ? "" : "s"} invoiced`;
        return `${months}${late}`;
    }
    return `${m(r.invoiced_usd)} of ${m(r.total_usd)} invoiced`;
}

/* The thin bar beside the sentence. Returns null when there is no honest
   denominator - drawing one anyway would be inventing a number.

   `label` is mandatory: the bar is a second reading of the same fact, never the
   only one, because Waleed is colour blind. */
function lpoBar(r) {
    const kind = String(r.kind || "materials").toLowerCase();
    if (kind === "open") return null;          // no ceiling to be a fraction of
    let done, all, label;
    if (kind === "rental") {
        done = Number(r.months_invoiced) || 0;
        all = Number(r.months_total) || 0;
        label = all ? `${done} of ${all} months` : "";
    } else {
        done = Number(r.invoiced_usd) || 0;
        all = Number(r.total_usd) || 0;
        label = all ? `${Math.round((done / all) * 100)}% invoiced` : "";
    }
    if (!all) return null;                     // not recorded is not zero
    return { pct: Math.min(100, (done / all) * 100), label, done, all };
}

/* ---- dates, the way the list writes them ---------------------------------- */
const LPO_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* "26/8/2026" (how an LPO stores its dates) or "2026-08-26" (the billing
   ledger) as a Date, or null. Never `new Date(string)`: it reads 4/6/2026 as
   April. */
function lpoDate(v) {
    const s = String(v || "").trim();
    let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (m) return new Date(+m[3], +m[2] - 1, +m[1]);
    return null;
}

/* "26 Aug 2026", or "26 Aug" when the year is said elsewhere. */
function lpoDay(v, withYear = true) {
    const d = lpoDate(v);
    if (!d) return String(v || "");
    return `${d.getDate()} ${LPO_MON[d.getMonth()]}${withYear ? " " + d.getFullYear() : ""}`;
}

/* "8 Jun - 11 Nov 2026", "8 Jun 2026 - 8 Jan 2027", or "From 24 Jul 2026". */
function lpoSpan(a, b) {
    const da = lpoDate(a), db = lpoDate(b);
    if (!da) return "";
    if (!db) return `From ${lpoDay(a)}`;
    return `${lpoDay(a, da.getFullYear() !== db.getFullYear())} - ${lpoDay(b)}`;
}

/* 60, 2.5, 876.8 - quantities without trailing zeros. */
function lpoQty(v) {
    const n = Number(v);
    return isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 3 }) : String(v || "");
}

/* ---- order -------------------------------------------------------------- */
/* Finished, as far as the list is concerned: the backend says closed (by hand,
   or by the kind's own rule) AND nothing is left to do. A materials order that
   was invoiced directly can still owe its delivery - that one stays up top. */
function lpoIsDone(r) {
    return r.status === "closed" && !(r.next_step && r.next_step.action);
}

/* Open LPOs first, newest first by the LPO's own date; finished ones last.
   The date the client wrote, not the date it was filed here: eight of them
   were filed in one sitting on 11 September. */
function lpoOrder(rows) {
    const at = (r) => { const d = lpoDate(r.lpo_date); return d ? d.getTime() : 0; };
    return (rows || []).slice().sort((a, b) =>
        (Number(lpoIsDone(a)) - Number(lpoIsDone(b))) || (at(b) - at(a))
        || String(b.received_at || "").localeCompare(String(a.received_at || "")));
}

/* ---- cells ---------------------------------------------------------------- */
/* Words first, the bar second - never the bar alone (Waleed is colour blind). */
function lpoMeter(text, pct, sub) {
    const bar = pct == null ? "" : `<span class="lpo-meter-bar" aria-hidden="true"><span style="width:${
        Math.max(0, Math.min(100, pct)).toFixed(1)}%"></span></span>`;
    return `<span class="lpo-meter"><span class="lpo-meter-t">${text}</span>${bar}${
        sub ? `<span class="lpo-sub">${sub}</span>` : ""}</span>`;
}

function lpoCellNo(r) {
    const meta = [esc(r.client_name), esc(lpoDay(r.lpo_date))].filter(Boolean).join(" &middot; ");
    return `<span class="lpo-no">${esc(r.lpo_no)}</span><span class="lpo-sub">${meta}</span>`;
}

function lpoCellKind(r) {
    return `<span class="lpo-kindchip">${esc(LPO_KIND_LABEL[lpoKind(r)] || "Materials")}</span>`;
}

function lpoCellValue(r) {
    if (lpoKind(r) === "open") return `<span class="lpo-none">no ceiling</span>`;
    return Number(r.total) ? esc(lpoMoney(r.total, r.currency)) : `<span class="lpo-none">not recorded</span>`;
}

function lpoCellProgress(r) {
    const bar = lpoBar(r);
    return lpoMeter(esc(lpoProgress(r)), bar ? bar.pct : null);
}

function lpoCellItems(r) {
    return r.items && r.items.lines ? String(r.items.lines) : `<span class="lpo-none">-</span>`;
}

function lpoCellDelivered(r) {
    const it = r.items;
    if (!it || !it.lines) return `<span class="lpo-none">no lines recorded</span>`;
    const part = it.part_delivered ? ` (+${it.part_delivered} part)` : "";
    return lpoMeter(`${it.delivered} of ${it.lines}${part}`, (it.delivered / it.lines) * 100);
}

// Rollup money is canonical USD, even on an IQD LPO.
function lpoCellInvoiced(r) {
    const total = Number(r.total_usd) || 0, done = Number(r.invoiced_usd) || 0;
    return lpoMeter(esc(lpoMoney(done)), total ? (done / total) * 100 : null,
                    total ? "of " + esc(lpoMoney(total)) : "");
}

function lpoCellPeriod(r) {
    const span = lpoSpan(r.period_start || r.lpo_date, r.period_end);
    return `${esc(span) || `<span class="lpo-none">no period recorded</span>`}${
        lpoDate(r.period_end) ? "" : `<span class="lpo-sub"><span class="lpo-flag">no end date</span></span>`}`;
}

function lpoCellMonths(r) {
    const done = Number(r.months_invoiced) || 0, all = Number(r.months_total) || 0;
    const fixed = Number(r.term_months) > 0;
    const missed = Number(r.months_unbilled) || 0, unsent = Number(r.invoices_not_sent) || 0;
    const notes = [missed ? `<span class="lpo-flag">${missed} missed</span>` : "",
                   unsent ? `${unsent} not sent` : ""].filter(Boolean).join(" &middot; ");
    return lpoMeter(fixed ? `${done} of ${all}` : `${done} invoiced`,
                    fixed && all ? (done / all) * 100 : null, notes);
}

function lpoCellBilled(r) {
    const total = Number(r.total_usd) || 0;
    return `${esc(lpoMoney(r.invoiced_usd))}${total ? `<span class="lpo-sub">of ${esc(lpoMoney(total))}</span>` : ""}`;
}

function lpoLines(r) {
    return (Array.isArray(r.rows) ? r.rows : []).filter(l => l && String(l.item || "").trim());
}

function lpoCellSupplying(r) {
    const lines = lpoLines(r);
    if (!lines.length) return `<span class="lpo-none">not recorded</span>`;
    return esc(lines[0].item) + (lines.length > 1 ? `<span class="lpo-sub">+${lines.length - 1} more</span>` : "");
}

function lpoCellUnitPrice(r) {
    const l = lpoLines(r)[0];
    const rate = l ? Number(String(l.rate == null ? "" : l.rate).replace(/,/g, "")) : NaN;
    if (!l || !isFinite(rate) || !rate) return `<span class="lpo-none">-</span>`;
    return `${esc(lpoMoney(rate, r.currency))}${l.unit ? " / " + esc(l.unit) : ""}`;
}

function lpoCellOpenInvoiced(r) {
    const n = (r.invoices || []).length;
    return `${esc(lpoMoney(r.invoiced_usd))}<span class="lpo-sub">${n} invoice${n === 1 ? "" : "s"}</span>`;
}

function lpoCellUnbilled(r) {
    const n = Number(r.months_unbilled) || 0;
    if (!n) return `<span class="lpo-none">none</span>`;
    const from = lpoDate(r.oldest_due_date);
    return `<span class="lpo-flag">${n} month${n === 1 ? "" : "s"}${from ? ", from " + LPO_MON[from.getMonth()] : ""}</span>`;
}

/* The next step, as the backend decided it (`database.lpo_next_step`): a
   button when there is something to do, quiet words when there is not. */
function lpoNextHtml(r) {
    const s = r && r.next_step;
    if (!s || !s.text) return "";
    if (s.action) {
        return `<button type="button" class="secondary lpo-next" data-next="${esc(s.key)}">${esc(s.text)}</button>`;
    }
    return `<span class="${s.key === "ending" ? "lpo-flag" : "lpo-none"}">${esc(s.text)}</span>`;
}

/* Which columns a tab shows - each kind answers its own question, the way a
   quotation's type decides its columns. */
function lpoColumns(kind) {
    const no = { label: "LPO", cls: "lpo-c-no", html: lpoCellNo };
    const value = { label: "Value", cls: "lpo-c-num", html: lpoCellValue };
    const next = { label: "Next step", cls: "lpo-c-next", html: lpoNextHtml };
    if (kind === "materials") return [no,
        { label: "Items", cls: "lpo-c-num", html: lpoCellItems },
        { label: "Delivered", cls: "lpo-c-prog", html: lpoCellDelivered },
        { label: "Invoiced", cls: "lpo-c-prog", html: lpoCellInvoiced },
        value, next];
    if (kind === "rental") return [no,
        { label: "Period", cls: "lpo-c-text", html: lpoCellPeriod },
        { label: "Months invoiced", cls: "lpo-c-prog", html: lpoCellMonths },
        { label: "Billed", cls: "lpo-c-num", html: lpoCellBilled },
        next];
    if (kind === "open") return [no,
        { label: "Supplying", cls: "lpo-c-text", html: lpoCellSupplying },
        { label: "Unit price", cls: "lpo-c-text", html: lpoCellUnitPrice },
        { label: "Invoiced", cls: "lpo-c-num", html: lpoCellOpenInvoiced },
        { label: "Not invoiced", cls: "lpo-c-text", html: lpoCellUnbilled },
        next];
    return [no,
        { label: "Type", cls: "lpo-c-type", html: lpoCellKind },
        { label: "Progress", cls: "lpo-c-prog", html: lpoCellProgress },
        value, next];
}

function renderLpoList() {
    const box = document.getElementById("lpoList");
    if (!box) return;
    renderLpoKpis(lpoState.rows);
    renderLpoChips();
    const countEl = document.getElementById("lpoCount");
    if (countEl) countEl.textContent = lpoState.rows.length ? `${lpoState.rows.length} on file` : "";

    const rows = lpoOrder(visibleLpos());
    const totEl = document.getElementById("lpoTotals");
    if (totEl) {
        totEl.textContent = rows.length !== lpoState.rows.length
            ? `${rows.length} of ${lpoState.rows.length} shown` : "";
    }
    if (!rows.length) {
        box.innerHTML = `<div class="empty-state"><div class="icon">&#8709;</div>
            <div class="title">No LPOs${lpoState.rows.length ? " match these filters" : " filed yet"}</div>
            <div class="sub">${lpoState.rows.length ? "Clear the search or pick another tab to see them."
                : "Add the LPO your client sent and attach their document - the rest follows from it."}</div></div>`;
        return;
    }
    const cols = lpoColumns(lpoState.kind);
    box.innerHTML = `<table class="lpo-table">
        <thead><tr>${cols.map(c => `<th class="${c.cls}" scope="col">${esc(c.label)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(r => lpoRowHtml(r, cols)).join("")}</tbody></table>`;
}

const LPO_KIND_LABEL = { materials: "Materials", rental: "Rental", open: "Supply contract" };

/* One row. The ROW opens the LPO; the one button in it is the next step.
   Six buttons per row (View, Original, Edit, Attach, Close, Remove) is what
   made six LPOs fill a 1080p screen in 2026-09 - everything else lives on the
   LPO screen. */
function lpoRowHtml(r, cols) {
    const done = lpoIsDone(r);
    return `<tr class="lpo-row${done ? " is-closed" : ""}" tabindex="0"
            data-client="${esc(r.client_name)}" data-no="${esc(r.lpo_no)}"
            title="Open ${esc(r.lpo_no)}">${
        (cols || lpoColumns("all")).map(c => `<td class="${c.cls}">${c.html(r)}</td>`).join("")
    }</tr>`;
}

/* Do the next step. `deliver` and `invoice` open the editors the LPO screen
   already uses; `invoice_period` opens the invoice for the oldest month that
   ended without one; `attach` asks for the file. */
async function runLpoNextStep(lpo) {
    const s = lpo && lpo.next_step;
    if (!s) return;
    if (s.key === "deliver") return window.createDnFromLpo(lpo.client_name, lpo.lpo_no);
    if (s.key === "invoice") return window.createInvoiceFromLpo(lpo.client_name, lpo.lpo_no);
    if (s.key === "invoice_period") return invoiceLpoPeriod(lpo, null, s.period_start);
    if (s.key === "attach") {
        if (await attachLpoFile(lpo) && lpoState.doc && lpoState.doc.lpo_no === lpo.lpo_no) {
            await reopenLpoDoc(lpo);
        }
    }
}

/* Invoice one month of the ledger. On the LPO screen the row comes from the
   ledger already loaded; from the list only the month is known, so the ledger
   is read first. The month's service dates go to the invoice editor, which
   writes them into the description. A month that already has an invoice asks
   first - a second one is sometimes right, never by accident. */
async function invoiceLpoPeriod(lpo, month, start) {
    if (!month) {
        let ledger = null;
        try { ledger = await api().lpo_billing_detail(lpo.client_name, lpo.lpo_no); }
        catch (e) { ledger = null; }
        const months = (ledger && ledger.ok && ledger.months) || [];
        const key = String(start || "").slice(0, 7);
        month = months.find(m => m.month === key)
            || months.find(m => m.expected && !m.count) || null;
        if (!month) {
            toast("Could not find that month - open the LPO to pick one", true);
            return;
        }
    }
    if (month.count && !await window.Popup.confirm(`${month.label} already has an invoice`,
            `${(month.invoices || []).join(", ")} is dated in ${month.label}. Make another one for the same month?`,
            { confirm: "Make another", cancel: "Back" })) return;
    return window.createInvoiceFromLpo(lpo.client_name, lpo.lpo_no,
        month.start ? { start: month.start, end: month.end, label: month.label } : null);
}

/* Close by hand, or put it back. The only way to end a rental the client
   terminated early - which CSSP-RENT-0002 says in so many words they may do -
   and the only way an open supply contract is ever retired at all. */
async function closeLpoRow(lpo) {
    if (lpo.closed_at) {
        const back = await window.Popup.confirm(
            "Reopen this LPO?",
            `${lpo.lpo_no} is closed. Reopening lets documents be filed against it `
            + "again, and puts it back in the monthly billing reminders.",
            { confirm: "Reopen" });
        if (!back) return;
        const res = await api().close_lpo(lpo.client_name, lpo.lpo_no, false);
        if (!res || !res.ok) { toast((res && res.error) || "Could not reopen", true); return; }
        toast(`${lpo.lpo_no} is open again.`);
        await refreshLpos();
        return;
    }

    let reason = "";
    const handle = window.Popup.open({
        title: "Close this LPO",
        subtitle: lpo.lpo_no,
        html: true,
        size: "sm",
        body: `<div class="lpo-close">
            <p>Closing stops the monthly billing reminders and takes this LPO out
               of the picker on new delivery notes and invoices. Nothing is deleted.</p>
            <label>Why did it end? <span class="helper subtle">optional</span>
                <input id="lpoCloseReason" autocomplete="off"
                       placeholder="terminated early, contract completed...">
            </label>
        </div>`,
        actions: [
            { label: "Cancel", value: null, cls: "ghost" },
            { label: "Close LPO", value: "close", cls: "primary", primary: true },
        ],
        onOpen: (h) => setTimeout(() => h.body.querySelector("#lpoCloseReason")?.focus(), 0),
        onClose: () => {
            const el = document.getElementById("lpoCloseReason");
            if (el) reason = el.value.trim();
        },
    });
    if (await handle.result !== "close") return;
    const res = await api().close_lpo(lpo.client_name, lpo.lpo_no, true, reason);
    if (!res || !res.ok) { toast((res && res.error) || "Could not close", true); return; }
    toast(`${lpo.lpo_no} closed.`);
    await refreshLpos();
}

function lpoRowFromEvent(e) {
    const row = e.target.closest(".lpo-row");
    if (!row) return null;
    return lpoState.rows.find(r => r.client_name === row.dataset.client
                               && r.lpo_no === row.dataset.no) || null;
}

// ============================================================
// Add / edit
// ============================================================
/* One PO shape per kind (2026-09-11).

   Materials is the old form: a total and a currency. A rental adds the period
   and the monthly rate the reminder needs. A supply contract has NO total field
   at all - CSSP-OPEN-0001 prints "Total Amount USD 0.77", which is the price of
   a litre of diesel, and typing that as a value is what made the first invoice
   read "Invoiced over LPO value". */
const LPO_KINDS = [
    { value: "materials", label: "Materials",
      hint: "A one-off order. Delivered, invoiced, done." },
    { value: "rental", label: "Rental",
      hint: "Runs for a period at a monthly rate." },
    { value: "open", label: "Supply contract",
      hint: "Open ended. No ceiling, billed as you supply." },
];

async function openLpoForm(existing) {
    const isEdit = !!existing;
    const r = existing || {};

    // Clients Master Data is the one list a client may come from - the same
    // rule the quotation, DN and invoice editors follow. `save_lpo` refuses
    // anything else, and ClientRequired offers to add it on the spot.
    let clients = [];
    try {
        const got = await api().cm_list("", "active");
        clients = (got && got.records) || [];
    } catch (e) { clients = []; }
    const opts = clients.map(c => `<option value="${esc(c.name)}">`).join("");

    // Lines live outside the markup so the tick boxes survive a re-render.
    let lines = window.LpoLines ? window.LpoLines.fromRows(r.rows || []) : [];

    const kindNow = (r.kind || "materials");
    const kindRadios = LPO_KINDS.map(k => `
        <label class="lpo-kind${k.value === kindNow ? " is-on" : ""}">
            <input type="radio" name="lpoFKind" value="${k.value}"
                   ${k.value === kindNow ? "checked" : ""}>
            <span class="lpo-kind-name">${k.label}</span>
            <span class="lpo-kind-hint">${k.hint}</span>
        </label>`).join("");

    const body = `
        <div class="lpo-form">
            <div class="lpo-form-row">
                <label>Client
                    <input id="lpoFClient" list="lpoClientOpts" value="${esc(r.client_name || "")}"
                           ${isEdit ? "readonly" : ""} autocomplete="off">
                    <datalist id="lpoClientOpts">${opts}</datalist>
                </label>
                <label>Their LPO number
                    <input id="lpoFNo" value="${esc(r.lpo_no || "")}" ${isEdit ? "readonly" : ""}
                           placeholder="e.g. CSSP-ADM-0010" autocomplete="off">
                </label>
                <label>LPO date
                    <input id="lpoFDate" value="${esc(r.lpo_date || (state.today ? state.today.display : ""))}"
                           placeholder="D/M/YYYY" autocomplete="off">
                </label>
            </div>

            <div class="lpo-kinds" id="lpoKinds">${kindRadios}</div>

            <div class="lpo-form-money">
                <label id="lpoFTotalWrap">Total value
                    <input id="lpoFTotal" type="number" step="0.01" min="0"
                           value="${r.total != null && r.total !== 0 ? esc(String(r.total)) : ""}">
                </label>
                <label id="lpoFMonthlyWrap" hidden>Monthly amount
                    <input id="lpoFMonthly" type="number" step="0.01" min="0"
                           value="${r.monthly_amount != null ? esc(String(r.monthly_amount)) : ""}">
                </label>
                <label>Currency
                    <select id="lpoFCur">
                        <option value="USD" ${(r.currency || "USD") === "USD" ? "selected" : ""}>USD</option>
                        <option value="IQD" ${r.currency === "IQD" ? "selected" : ""}>IQD</option>
                    </select>
                </label>
            </div>

            <div class="lpo-form-row" id="lpoPeriodWrap" hidden>
                <label>Period starts
                    <input id="lpoFPeriodStart" value="${esc(r.period_start || "")}"
                           placeholder="D/M/YYYY" autocomplete="off">
                </label>
                <label>Period ends <span class="helper subtle">blank = open ended</span>
                    <input id="lpoFPeriodEnd" value="${esc(r.period_end || "")}"
                           placeholder="D/M/YYYY" autocomplete="off">
                </label>
            </div>
            <p class="lpo-kind-note" id="lpoKindNote" hidden></p>

            <div class="lpo-form-row lpo-quote-row">
                <label>Against quotation <span class="helper subtle">optional</span>
                    <input id="lpoFQuote" value="${esc(r.source_quote_no || "")}"
                           placeholder="QUT-MT-HDEC-029" autocomplete="off">
                </label>
                <button type="button" class="ghost" id="lpoPickQuote">Select</button>
                <button type="button" class="ghost" id="lpoPullLines">Pull lines</button>
            </div>
            <p class="helper subtle lpo-quote-hint">Select lists this client's own
               quotations and fills the lines and the total from the one you pick.
               Type the number by hand if their PO cites a reference of theirs.</p>

            <div class="lpo-lines" id="lpoLinesWrap">
                <div class="lpo-lines-head">
                    <strong>What they ordered</strong>
                    <span class="helper subtle">Untick anything their PO left out.</span>
                    <span class="spacer"></span>
                    <button type="button" class="ghost" id="lpoAddLine">Add a line</button>
                </div>
                <div id="lpoLinesBody"></div>
                <div class="lpo-lines-foot">
                    <span id="lpoLinesTotal"></span>
                </div>
                <p class="lpo-mismatch" id="lpoMismatch" hidden></p>
            </div>

            <details class="lpo-more">
                <summary>More details</summary>
                <div class="lpo-form-row">
                    <label>ERP No <input id="lpoFErp" value="${esc(r.erp_no || "")}" autocomplete="off"></label>
                    <label>Project <input id="lpoFProject" value="${esc(r.project || "")}" placeholder="CSSP" autocomplete="off"></label>
                </div>
                <label>Their contact for this PO
                    <input id="lpoFContact" value="${esc(r.contact || "")}"
                           placeholder="Mr. Sung Hoon Jung - sunghoon@hdec.co.kr" autocomplete="off">
                </label>
            </details>

            <label>Notes
                <textarea id="lpoFNotes" rows="2">${esc(r.notes || "")}</textarea>
            </label>

            <div class="lpo-form-attach">
                <label>Their PO document <span class="helper subtle">PDF, PNG or JPG</span>
                    <input type="file" id="lpoFFile" accept=".pdf,.png,.jpg,.jpeg">
                </label>
                <span class="helper subtle" id="lpoFFileNote">${
                    isEdit && r.file_exists
                        ? "A document is already attached. Choosing one here replaces it."
                        : "Attached as soon as the record saves."}</span>
            </div>
            ${isEdit ? `<div class="helper subtle">Client and LPO number identify the record and can't be changed - remove and re-add to correct them.</div>` : ""}
        </div>`;

    /* Declared BEFORE Popup.open: it calls `onOpen` synchronously (popup.js:324)
       and swallows anything thrown, so a `const` used by the wiring but declared
       below would hit the temporal dead zone and leave the form silently
       unwired - open, blank and with no error anywhere. */
    const g = id => (document.getElementById(id)?.value || "").trim();
    const currentKind = () =>
        document.querySelector('input[name="lpoFKind"]:checked')?.value || "materials";
    let baseline = "", saving = false;
    const formSnapshot = () => JSON.stringify({payload: buildLpoPayload(), lines,
        inputs: Array.from(document.querySelectorAll('#lpoLinesBody [data-f]')).map(el => [el.value,el.checked]),
        file: document.getElementById("lpoFFile")?.files?.[0]?.name || ""});

    const handle = window.Popup.open({
        title: isEdit ? `Edit ${r.lpo_no}` : "Add a client LPO",
        html: true, body, size: "lg", className: "lpo-form-popup",
        actions: [
            { label: "Cancel", value: null, cls: "ghost" },
            /* `keep: true` - the button acts WITHOUT closing, because everything
               it sends is read out of this dialog and Popup detaches the panel
               before it resolves (popup.js `finish`). Reading after the close
               is how every field arrived empty and the save came back "this
               document needs a client" (Waleed, Eshraqat, 2026-09-19). Same
               shape as the quick-add dialog in client_required.js. */
            { label: isEdit ? "Save" : "Add", value: "ok", cls: "primary", primary: true,
              keep: true, onClick: (h) => { submitLpo(h); } },
        ],
        onOpen: (h) => {
            wireLpoForm(h);
            baseline = formSnapshot();
        },
        beforeClose: async value => {
            if (value === "ok") return true;
            if (saving) return false;
            if (!baseline || baseline === formSnapshot()) return true;
            return window.Popup.confirm("Discard LPO changes?", "Your changes have not been saved.",
                {confirm: "Discard changes", cancel: "Keep editing"});
        },
    });

    /* Which money fields this kind uses, and why. Toggled with `hidden`, the
       one visibility switch every screen here agrees on. */
    function applyKindFields() {
        const kind = currentKind();
        const show = (id, on) => {
            const el = document.getElementById(id);
            if (el) el.hidden = !on;
        };
        show("lpoFTotalWrap", kind !== "open");
        show("lpoFMonthlyWrap", kind === "rental");
        show("lpoPeriodWrap", kind !== "materials");
        const note = document.getElementById("lpoKindNote");
        if (note) {
            const text = kind === "open"
                ? "An open contract has no ceiling, so there is no total to record. "
                + "CSSP-OPEN-0001 prints 0.77, which is the price of a litre of diesel."
                : kind === "rental"
                ? "The period is what the monthly reminder counts. Leave the end date "
                + "blank if the term is not fixed; close the contract by hand if it ends early."
                : "";
            note.textContent = text;
            note.hidden = !text;
        }
        document.querySelectorAll(".lpo-kind").forEach((el) => {
            const input = el.querySelector('input[name="lpoFKind"]');
            el.classList.toggle("is-on", !!input && input.checked);
        });
        paintLines();
    }

    function paintLines() {
        const box = document.getElementById("lpoLinesBody");
        if (!box || !window.LpoLines) return;
        box.innerHTML = lines.length
            ? lines.map((l, i) => `
                <div class="lpo-line${l.take ? "" : " is-off"}" data-i="${i}">
                    <label class="lpo-line-take">
                        <input type="checkbox" ${l.take ? "checked" : ""} data-f="take">
                        <span class="sr-only">Ordered</span>
                    </label>
                    <input class="lpo-line-item" data-f="item" value="${esc(l.item)}" placeholder="Item" aria-label="Item ${i+1}">
                    <input class="lpo-line-desc" data-f="description" value="${esc(l.description)}" placeholder="Description" aria-label="Description ${i+1}">
                    <input class="lpo-line-unit" data-f="unit" value="${esc(l.unit)}" placeholder="Unit" aria-label="Unit ${i+1}">
                    <input class="lpo-line-qty" data-f="qty" type="number" step="any" value="${esc(String(l.qty || ""))}" placeholder="Qty" aria-label="Quantity ${i+1}">
                    <input class="lpo-line-rate" data-f="rate" value="${esc(l.rate)}" placeholder="Rate" aria-label="Rate ${i+1}">
                    <button type="button" class="ghost lpo-line-del" data-del="${i}" title="Remove this line" aria-label="Remove line ${i+1}">&times;</button>
                </div>`).join("")
            : `<div class="lpo-lines-empty">No lines recorded. Pull them from the quotation
                 this PO cites, or add them by hand.</div>`;
        box.querySelectorAll(".lpo-line").forEach((row) => {
            const i = Number(row.dataset.i);
            row.querySelectorAll("[data-f]").forEach((input) => {
                const f = input.dataset.f;
                input.onchange = () => {
                    lines[i][f] = f === "take" ? input.checked
                                : f === "qty" ? (parseFloat(input.value) || 0)
                                : input.value;
                    if (f === "take") row.classList.toggle("is-off", !input.checked);
                    paintTotals();
                };
                if (f !== "take") input.oninput = () => {
                    lines[i][f] = f === "qty" ? (parseFloat(input.value) || 0) : input.value;
                    paintTotals();
                };
            });
            const del = row.querySelector("[data-del]");
            if (del) del.onclick = () => { lines.splice(i, 1); paintLines(); };
        });
        paintTotals();
    }

    /* The live total, and the sentence that appears when it disagrees with what
       the PO prints. Never blocks the save - Waleed's call, 2026-09-11. */
    function paintTotals() {
        if (!window.LpoLines) return;
        const sum = window.LpoLines.total(lines);
        const el = document.getElementById("lpoLinesTotal");
        if (el) {
            el.textContent = lines.length
                ? `${lines.filter(l => l.take).length} line(s) ordered - ${lpoMoney(sum, document.getElementById("lpoFCur")?.value)}`
                : "";
        }
        const note = document.getElementById("lpoMismatch");
        if (!note) return;
        // An open contract has no printed total to disagree with.
        const text = (currentKind() === "open" || !lines.length)
            ? "" : window.LpoLines.mismatch(lines, g("lpoFTotal"));
        note.textContent = text ? "⚠ " + text : "";
        note.hidden = !text;
    }

    /* Returns the quotation it read, so `pickQuote` can take its TOTAL as well
       as its lines. Null when nothing came across. */
    async function pullLines() {
        const no = g("lpoFQuote");
        if (!no) { toast("Type the quotation number this PO answers first", true); return null; }
        let res;
        try { res = await api().lpo_from_quote(no, g("lpoFClient")); }
        catch (e) { res = null; }
        if (!res || !res.ok) {
            // Never a dead button: CSSP-RENT-0002 cites QUT-146, which is
            // HDEC's own reference and not a quotation we hold.
            toast((res && res.error) || `Could not read ${no}`, true);
            return null;
        }
        if (!res.rows.length) { toast(`${no} has no lines to pull`, true); return null; }
        lines = window.LpoLines.fromQuote(res.rows);
        paintLines();
        toast(`${res.rows.length} line(s) from ${res.quote_no} - untick anything their PO left out`);
        return res;
    }

    /* Pick the quotation from the client's own list, then take everything it
       can give: the number, the lines, and the total.

       The total is FILLED, not locked. A PO legitimately prints a total of its
       own - CSSP-ADM-0010 drops one of our five lines and prints 18,390 against
       our 19,286 - and `LpoLines.mismatch` already says so in words when they
       disagree. What this removes is retyping a figure that is usually right. */
    async function pickQuote() {
        if (!window.QuotePicker) { toast("The quotation picker is not loaded", true); return; }
        const client = g("lpoFClient");
        if (!client) { toast("Fill the client in first", true); return; }
        const row = await window.QuotePicker.pick(client, g("lpoFQuote"));
        if (!row) return;
        const box = document.getElementById("lpoFQuote");
        if (box) box.value = row.quote_no || "";
        const pulled = await pullLines();
        /* The QUOTATION's own total, not the sum of the lines that came
           across. They are the same number whenever every line reads - and
           when they are not, that gap is the thing worth seeing: `paintTotals`
           says it in words below the lines. Filling the line sum instead would
           silently hide a quotation whose lines could not all be read.

           Only from a quotation we could read, and never over a figure already
           typed off the PO in front of him. */
        const totalBox = document.getElementById("lpoFTotal");
        if (pulled && totalBox && !parseFloat(totalBox.value || "")) {
            const sum = Number(pulled.total) || window.LpoLines.total(lines);
            if (sum > 0) {
                totalBox.value = String(sum);
                const cur = document.getElementById("lpoFCur");
                if (cur) cur.value = pulled.currency || "USD";   // quotations sell in dollars
                paintTotals();
            }
        }
    }

    function wireLpoForm() {
        document.querySelectorAll('input[name="lpoFKind"]').forEach((el) => {
            el.onchange = applyKindFields;
        });
        const total = document.getElementById("lpoFTotal");
        if (total) total.oninput = paintTotals;
        const cur = document.getElementById("lpoFCur");
        if (cur) cur.onchange = paintTotals;
        const pull = document.getElementById("lpoPullLines");
        if (pull) pull.onclick = pullLines;
        const pick = document.getElementById("lpoPickQuote");
        if (pick) pick.onclick = pickQuote;
        const add = document.getElementById("lpoAddLine");
        if (add) add.onclick = () => { lines.push(window.LpoLines.blank()); paintLines(); };
        applyKindFields();
    }

    function buildLpoPayload() {
        const kind = currentKind();
        return {
            client_name: isEdit ? r.client_name : g("lpoFClient"),
            lpo_no: isEdit ? r.lpo_no : g("lpoFNo"),
            lpo_date: g("lpoFDate"),
            kind,
            // An open contract has no ceiling, so nothing is stored as one.
            total: kind === "open" ? 0 : (parseFloat(g("lpoFTotal")) || 0),
            currency: document.getElementById("lpoFCur")?.value || "USD",
            monthly_amount: kind === "rental" ? (g("lpoFMonthly") || null) : null,
            period_start: kind === "materials" ? "" : g("lpoFPeriodStart"),
            period_end: kind === "materials" ? "" : g("lpoFPeriodEnd"),
            rows: window.LpoLines ? window.LpoLines.toRows(lines) : [],
            source_quote_no: g("lpoFQuote"),
            erp_no: g("lpoFErp"),
            project: g("lpoFProject"),
            contact: g("lpoFContact"),
            notes: g("lpoFNotes"),
        };
    }

    /* Runs from the Add / Save button while the dialog is still on screen, so
       the fields it reads still exist. It closes only once the save is in:
       a refusal leaves the form open with the typing intact, which is also
       what lets ClientRequired put a new client name back in the box and try
       again. */
    let saved = null;
    let savedFile = null;
    async function submitLpo(h) {
        if (saving) return;
        saving = true;
        const button = h.panel.querySelector('[data-value="ok"]');
        if (button) button.disabled = true;
        try {
        let payload = buildLpoPayload();
        /* Read the chosen file HERE, beside every other field, for the same
           reason the payload is: `Popup.close` detaches the panel before it
           resolves, so anything read after it is gone (2026-09-19). The file
           is UPLOADED after the dialog closes - that part needs no DOM. */
        const chosen = document.getElementById("lpoFFile");
        const file = (chosen && chosen.files && chosen.files[0]) || null;
        if (file && !lpoAcceptable(file)) {
            toast("Only PDF, PNG or JPG can be attached", true);
            return;
        }
        let res;
        try { res = await api().save_lpo(payload); }
        catch (e) { res = { ok: false, error: String(e && e.message || e) }; }
        // A client who is not on file yet: offer to add them here rather than
        // sending him to Master Data with a half-filled PO on screen.
        if (window.ClientRequired && await window.ClientRequired.offer(res, "lpo")) {
            payload = buildLpoPayload();
            try { res = await api().save_lpo(payload); }
            catch (e) { res = { ok: false, error: String(e && e.message || e) }; }
        }
        if (!res || !res.ok) {
            toast("Could not save: " + (res && res.error || "unknown"), true);
            return;
        }
        saved = payload;
        savedFile = file;
        h.close("ok");
        } finally {
            saving = false;
            if (button) button.disabled = false;
        }
    }

    const choice = await handle.result;
    if (choice !== "ok" || !saved) return null;
    /* The record exists now, which is what `attach_lpo_file_data` requires -
       so filing a PO and its document is ONE trip through the form instead of
       "add it, find it in the list, then attach". */
    if (savedFile) {
        const ok = await attachLpoData(saved.client_name, saved.lpo_no, savedFile,
                                       isEdit && r.ack_exists);
        if (!ok) toast(`${saved.lpo_no} was saved, but the document was not attached`, true);
    }
    await refreshLpos();
    return saved;
}

// ============================================================
// Attach the client's document
// ============================================================
function lpoAcceptable(file) {
    return !!file && /\.(pdf|png|jpe?g)$/i.test(file.name || "");
}
function fileToDataUri(file) {
    return new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = () => rej(fr.error);
        fr.readAsDataURL(file);
    });
}

/* Upload one already-chosen file. Shared by the Attach button and by the add
   form, which picks the file while it is still on screen and sends it once the
   record exists - `attach_lpo_file_data` refuses a PO it cannot find. */
async function attachLpoData(clientName, lpoNo, file, hasAck) {
    if (!file) return false;
    if (!lpoAcceptable(file)) {
        toast("Only PDF, PNG or JPG can be attached", true);
        return false;
    }
    if (hasAck) {
        const go = await showModal({
            title: "Replace the document?",
            body: `${lpoNo} already has an acknowledged copy. Replacing the client's original will delete that counter-signed copy - you'd need to sign the new file again.`,
            buttons: [{ label: "Cancel", value: "no" },
                      { label: "Replace", value: "ok", cls: "danger" }],
        });
        if (go !== "ok") return false;
    }
    let r;
    try {
        r = await api().attach_lpo_file_data(
            clientName, lpoNo, await fileToDataUri(file), file.name);
    } catch (e) { r = { ok: false, error: String(e && e.message || e) }; }
    if (!r || !r.ok) {
        toast("Attach failed: " + (r && r.error || "unknown"), true);
        return false;
    }
    toast("Document attached to " + lpoNo);
    return true;
}

/* Settles once the file is attached (true) or the picker was dismissed (false).

   It used to return as soon as the picker OPENED, so the LPO screen reloaded
   before the file had been chosen and kept showing the old document after a
   successful Replace (found 2026-10-03). */
function attachLpoFile(lpo) {
    return new Promise((resolve) => {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = ".pdf,.png,.jpg,.jpeg";
        input.onchange = async () => {
            const f = input.files && input.files[0];
            if (!f) { resolve(false); return; }
            const ok = await attachLpoData(lpo.client_name, lpo.lpo_no, f, lpo.ack_exists);
            if (ok) await refreshLpos();
            resolve(ok);
        };
        input.addEventListener("cancel", () => resolve(false));
        input.click();
    });
}

// ============================================================
// Viewer + placement
// ============================================================
function lpoWorkerReady() {
    if (!window.pdfjsLib) return false;
    // pdf.js falls back to an in-thread "fake worker" if this file can't be
    // loaded over file:// - slower on a big scan, but it still renders.
    try {
        pdfjsLib.GlobalWorkerOptions.workerSrc = "../static/lib/pdf.worker.min.js";
    } catch (e) { /* fake-worker fallback */ }
    return true;
}

/* ============================================================
   ONE LPO, OPEN (2026-09-11)

   Replaces the full-screen viewer popup. Clicking a row brings the client's
   document up beside its record, the shape the PO and quotation editors already
   use: back button and identity in the topbar, the work on the left, the
   controls on the right. 70/30, because the left pane is where the signature
   actually gets placed and judged.

   The placement machinery is UNCHANGED - `renderLpoPage`, `addLpoMark`,
   `paintLpoMarks`, `startLpoDrag` and `updateLpoMarkCount` all work off
   `lpoState.viewer` and the ids `lpoCanvasWrap` / `lpoPageLbl` / `lpoVHint`,
   which this screen provides. Fractions of the page, the real signature drawn
   in the box, and the merged result agreeing with the preview were each fixed
   against a real failure; moving the frame around them must not disturb any of
   it.
   ============================================================ */


window.showLpoDoc = async function showLpoDoc(lpo) {
    if (!lpo) return;
    hideAllViews();
    document.getElementById("lpoDocView").hidden = false;
    document.getElementById("btnHome").hidden = false;
    setBrand(lpo.lpo_no);
    lpoState.doc = lpo;
    lpoState.signing = false;
    renderLpoDocHeader(lpo);
    // What is still owed, line by line. Read before the panel paints so the
    // ordered list is there on the first frame rather than popping in.
    const [fulfil, billing] = await Promise.all([
        api().lpo_fulfillment(lpo.client_name, lpo.lpo_no).catch(() => null),
        lpo.kind !== "materials" && typeof api().lpo_billing_detail === "function"
            ? api().lpo_billing_detail(lpo.client_name, lpo.lpo_no).catch(() => null)
            : Promise.resolve(null),
    ]);
    if (lpoState.doc !== lpo) return;
    lpoState.fulfil = fulfil;
    lpoState.billing = billing;
    renderLpoDocSide(lpo);

    // Editing the header does not change the PDF, so do not pull it across the
    // bridge again - it is 819 KB and the reload is visible. The two file paths
    // are the honest test: signing writes `ack_path`, attaching replaces
    // `file_path`, and either MUST reload. Anything else (a new total, a closed
    // contract, a corrected date) leaves the rendered document valid.
    const V = lpoState.viewer;
    const same = V && V.lpo
        && V.lpo.client_name === lpo.client_name && V.lpo.lpo_no === lpo.lpo_no
        && (V.lpo.file_path || "") === (lpo.file_path || "")
        && (V.lpo.ack_path || "") === (lpo.ack_path || "");
    if (same) {
        V.lpo = lpo;                       // keep the record fresh behind the page
        renderLpoDocToolbar(lpo, V.which);
        return;
    }
    await openLpoDocFile(lpo, lpo.ack_exists ? "ack" : "file");
};

/* The header (2026-10-03): the number, what kind of PO it is and where it has
   got to, then its buttons - the next step as the ONE primary button, the other
   create action where the kind has both (a materials order or a supply contract
   is delivered AND invoiced; a rental is only invoiced), and everything else in
   More. A closed LPO offers nothing to create, and a fully invoiced one no
   longer offers Create invoice. The ten-button grid this replaces gave Remove
   the same weight as Create invoice. */
function renderLpoDocHeader(lpo) {
    const no = document.getElementById("lpoDocNo");
    if (no) no.textContent = lpo.lpo_no;
    const chips = document.getElementById("lpoDocChips");
    if (chips) {
        const st = LPO_STATUS[lpo.status] || LPO_STATUS.received;
        chips.innerHTML = lpoCellKind(lpo)
            + `<span class="lpo-status ${st.cls}">${st.label}</span>`
            + (lpo.over_value ? `<span class="lpo-status lpo-st-over">Over LPO value</span>` : "");
    }
    const acts = document.getElementById("lpoDocActions");
    if (!acts) return;
    // While signing, the only decisions are Save and Cancel, in the panel.
    acts.innerHTML = lpoState.signing ? "" : lpoDocActionsHtml(lpo);
    acts.querySelectorAll("[data-doc]").forEach(b => {
        b.onclick = safe(() => lpoDocAction(b.dataset.doc, lpo), "LPO action");
    });
    acts.querySelectorAll("[data-next]").forEach(b => {
        b.onclick = safe(() => runLpoNextStep(lpo), "LPO next step");
    });
    wireLpoMenu(acts);
}

function lpoDocActionsHtml(lpo) {
    const kind = lpoKind(lpo);
    const s = lpo.next_step || {};
    const done = s.key === "done";
    const out = [];
    if (!lpo.closed_at) {
        // Attaching is offered where the document would be, in the panel.
        const primary = s.action && s.key !== "attach" ? s.key : "";
        if (primary) {
            out.push(`<button type="button" class="primary" data-next="${esc(s.key)}">${esc(s.text)}</button>`);
        }
        if (kind !== "rental" && primary !== "deliver" && !done) {
            out.push(`<button type="button" class="secondary" data-doc="dn">Create delivery note</button>`);
        }
        if (lpo.status !== "closed" && !done && primary !== "invoice" && primary !== "invoice_period") {
            out.push(`<button type="button" class="secondary" data-doc="inv">Create invoice</button>`);
        }
    }
    out.push(lpoMenuHtml(lpo));
    return out.join("");
}

/* Everything that is not the next step: rare, or about the file itself. */
function lpoMenuHtml(lpo) {
    const f = !!lpo.file_exists;
    const item = (act, label, on = true, cls = "") =>
        `<button type="button" role="menuitem" class="lpo-menu-item${cls}" data-doc="${act}" ${on ? "" : "disabled"}>${label}</button>`;
    return `<div class="lpo-menu">
        <button type="button" class="ghost lpo-menu-btn" aria-haspopup="menu" aria-expanded="false">More
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <div class="lpo-menu-pop" role="menu" hidden>
            ${item("open", "Open in PDF reader", f)}
            ${item("print", "Print", f)}
            ${item("copy", "Copy file", f)}
            ${item("folder", "Show in folder", f)}
            <hr>
            ${item("edit", "Edit details")}
            ${item("attach", f ? "Replace file" : "Attach file")}
            ${lpo.ack_exists ? item("sign", "Sign again", f) : ""}
            <hr>
            ${item("close", lpo.closed_at ? "Reopen LPO" : "Close LPO")}
            ${item("delete", "Remove", true, " is-danger")}
        </div>
    </div>`;
}

let lpoMenuOutsideBound = false;
function wireLpoMenu(host) {
    const btn = host.querySelector(".lpo-menu-btn");
    const pop = host.querySelector(".lpo-menu-pop");
    if (!btn || !pop) return;
    const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.onclick = (e) => {
        e.stopPropagation();
        const open = pop.hidden;
        pop.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) { const first = pop.querySelector("button:not([disabled])"); if (first) first.focus(); }
    };
    pop.addEventListener("click", close);          // choosing an item closes it
    pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); btn.focus(); } });
    if (!lpoMenuOutsideBound && document.addEventListener) {
        lpoMenuOutsideBound = true;
        document.addEventListener("click", (e) => {
            if (e.target && e.target.closest && e.target.closest(".lpo-menu")) return;
            document.querySelectorAll(".lpo-menu-pop").forEach(p => { p.hidden = true; });
            document.querySelectorAll(".lpo-menu-btn").forEach(b => b.setAttribute("aria-expanded", "false"));
        });
    }
}

/* Which file is on screen, and the tools for it.

   The banner naming the file is not decoration: without it a perfectly
   successful acknowledge looked like a no-op, because the client's original is
   deliberately never modified - you signed, reopened, and saw an unstamped
   page. */
async function openLpoDocFile(lpo, which) {
    /* The latest open wins (2026-10-03). Every step below waits on something -
       the file over the bridge, pdf.js, the fit measurement - and opening a
       second LPO meanwhile used to let the first one finish on top of it:
       its pages drawn into the second's screen, or a crash on a viewer that
       had not loaded yet. Each wait re-checks the ticket. */
    const ticket = lpoState.openTicket = (lpoState.openTicket || 0) + 1;
    const stale = () => ticket !== lpoState.openTicket;
    const stage = document.getElementById("lpoCanvasWrap");
    const banner = document.getElementById("lpoDocFile");
    if (!lpo.file_exists) {
        lpoState.viewer = null;
        if (stage) stage.innerHTML = "";
        renderLpoDocToolbar(lpo, which);
        if (banner) {
            banner.className = "lpo-v-file is-missing";
            banner.textContent = "No document attached yet. Use Attach file to add the PDF or scan the client sent.";
        }
        return;
    }
    let got;
    try { got = await api().lpo_file_data_uri(lpo.client_name, lpo.lpo_no, which); }
    catch (e) { got = null; }
    if (stale()) return;
    if (!got || !got.ok) {
        toast((got && got.error) || "Could not open the document", true);
        return;
    }

    const V = lpoState.viewer = {
        lpo, which, page: 1, pages: 1, scale: 1.25, marks: [], doc: null,
        isImage: !got.is_pdf, dataUri: got.data_uri, filename: got.filename,
        path: got.path,
    };
    if (got.is_pdf) {
        if (!lpoWorkerReady()) { toast("PDF viewer failed to load", true); return; }
        try {
            V.doc = await pdfjsLib.getDocument({ data: _b64ToBytes(got.data_uri) }).promise;
            V.pages = V.doc.numPages;
        } catch (e) { if (!stale()) toast("Could not read this PDF: " + e, true); return; }
        if (stale()) return;
    }
    // The zoom chosen earlier this session, or the page fitted to the width.
    V.scale = lpoState.zoom === "fit" ? await lpoFitScale(V) : (Number(lpoState.zoom) || 1.25);
    if (stale()) return;
    renderLpoDocToolbar(lpo, which);
    if (banner) {
        banner.className = "lpo-v-file" + (which === "ack" ? " is-ack" : "");
        banner.innerHTML = which === "ack"
            ? `Showing the <strong>signed copy</strong> - ${esc(got.filename)}. This is the file to send back.`
            : `Showing the <strong>client's original</strong> - ${esc(got.filename)}. It is never modified; signing writes a separate copy.`;
    }
    await renderLpoPage();
}

/* The signature and stamp images, fetched ONCE per session.

   `signing_assets()` is a 679 KB payload - two base64 PNGs - and it was being
   pulled across the bridge every time a document was opened or a file switched
   (reported as "the LPO screens are slow", 2026-09-12). Opening one LPO cost
   about 1.5 MB of JSON over the pywebview bridge, two thirds of it images that
   had not changed.

   They only change when the user uploads a new one in Settings, which calls
   `window.clearLpoAssets()`. */
async function lpoAssets() {
    if (lpoState.assets) return lpoState.assets;
    try { lpoState.assets = await api().signing_assets(); }
    catch (e) { lpoState.assets = {}; }
    return lpoState.assets;
}
window.clearLpoAssets = function clearLpoAssets() { lpoState.assets = null; };

function renderLpoDocToolbar(lpo, which) {
    const bar = document.getElementById("lpoDocToolbar");
    if (!bar) return;
    const signing = !!lpoState.signing;
    // width/height on the tag, not left to CSS: an inline <svg> with neither
    // falls back to its intrinsic size (~300px), which is exactly how the
    // Signature and Stamp buttons came out as a giant squiggle and circle
    // overlapping their own labels (reported 2026-09-12).
    const ico = (p) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round"
        stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
    bar.innerHTML = `
        <button class="lpo-icob" id="lpoPrev" title="Previous page" aria-label="Previous page">
            ${ico('<polyline points="15 18 9 12 15 6"/>')}</button>
        <span class="lpo-v-page" id="lpoPageLbl">1 / 1</span>
        <button class="lpo-icob" id="lpoNext" title="Next page" aria-label="Next page">
            ${ico('<polyline points="9 18 15 12 9 6"/>')}</button>
        <span class="lpo-v-sep"></span>
        <button class="lpo-icob" id="lpoZoomOut" title="Zoom out" aria-label="Zoom out">
            ${ico('<line x1="5" y1="12" x2="19" y2="12"/>')}</button>
        <span class="lpo-v-page" id="lpoZoomLbl">${lpoZoomLabel()}</span>
        <button class="lpo-icob" id="lpoZoomIn" title="Zoom in" aria-label="Zoom in">
            ${ico('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>')}</button>
        <button class="ghost lpo-v-fit${lpoState.zoom === "fit" ? " is-on" : ""}" id="lpoZoomFit"
                aria-pressed="${lpoState.zoom === "fit" ? "true" : "false"}"
                title="Fit the page to the width">Fit width</button>
        ${signing ? `
        <span class="lpo-v-sep"></span>
        <button class="ghost lpo-v-add" id="lpoAddSig">${ico('<path d="M3 17c3-6 6 6 9 0s6-4 9-2"/>')}<span>Signature</span></button>
        <button class="ghost lpo-v-add" id="lpoAddStamp">${ico('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>')}<span>Stamp</span></button>` : ""}
        <span class="spacer"></span>
        ${lpo.ack_exists && !signing ? `
        <div class="lpo-v-switch" role="group" aria-label="Which file">
            <button class="${which === "file" ? "is-on" : ""}" data-which="file">Original</button>
            <button class="${which === "ack" ? "is-on" : ""}" data-which="ack">Signed copy</button>
        </div>` : ""}`;

    const on = (id, fn) => { const b = document.getElementById(id); if (b) b.onclick = fn; };
    const V = () => lpoState.viewer;
    // Every page is already rendered, so these SCROLL - no re-render, and no
    // page that can only be reached one click at a time.
    on("lpoPrev", () => { const v = V(); if (v) scrollLpoToPage(v.page - 1); });
    on("lpoNext", () => { const v = V(); if (v) scrollLpoToPage(v.page + 1); });
    on("lpoZoomIn", () => lpoSetZoom(0.25));
    on("lpoZoomOut", () => lpoSetZoom(-0.25));
    on("lpoZoomFit", () => lpoSetZoom("fit"));
    on("lpoAddSig", () => addLpoMark("sig"));
    on("lpoAddStamp", () => addLpoMark("stamp"));
    bar.querySelectorAll("[data-which]").forEach(b => {
        b.onclick = () => openLpoDocFile(lpo, b.dataset.which);
    });
}

function lpoZoomLabel() {
    const V = lpoState.viewer;
    return Math.round((((V && V.scale) || 1.25) / 1.25) * 100) + "%";
}

function paintLpoZoom() {
    const el = document.getElementById("lpoZoomLbl");
    if (el) el.textContent = lpoZoomLabel();
    const fit = document.getElementById("lpoZoomFit");
    if (fit) {
        fit.classList.toggle("is-on", lpoState.zoom === "fit");
        fit.setAttribute("aria-pressed", lpoState.zoom === "fit" ? "true" : "false");
    }
}

/* + / - step from wherever the page is now; Fit width measures the stage.
   Either way the choice is kept for the session, so the next LPO opens the
   same way instead of snapping back to a fixed 125%. */
async function lpoSetZoom(change) {
    const V = lpoState.viewer;
    if (!V) return;
    if (change === "fit") {
        lpoState.zoom = "fit";
        V.scale = await lpoFitScale(V);
    } else {
        V.scale = Math.max(0.5, Math.min(3, Math.round((V.scale + change) * 100) / 100));
        lpoState.zoom = V.scale;
    }
    await renderLpoPage();
    paintLpoZoom();
    scrollLpoToPage(V.page);
}

/* The scale at which page 1 fills the stage's width (2026-10-03). The stage is
   on screen by the time this runs - `showLpoDoc` shows the view first. */
async function lpoFitScale(V) {
    const stage = document.getElementById("lpoStage");
    const avail = Math.max(240, ((stage && stage.clientWidth) || 900) - 40);
    let natural = 0;
    try {
        if (V.isImage) {
            const img = new Image();
            img.src = V.dataUri;
            await new Promise(res => { img.onload = res; img.onerror = res; });
            natural = (img.naturalWidth || 0) * 0.5;   // renderLpoPage draws images at naturalWidth * scale * 0.5
        } else if (V.doc) {
            natural = (await V.doc.getPage(1)).getViewport({ scale: 1 }).width;
        }
    } catch (e) { natural = 0; }
    if (!natural) return 1.25;
    return Math.max(0.5, Math.min(3, Math.round((avail / natural) * 100) / 100));
}

/* The right-hand panel (2026-10-03). Read-only, in the order the questions
   come: what is this and where is it up to, has it been signed, is anything
   wrong, then the items (a materials order) or the billing periods (a
   contract), its delivery notes and invoices, and the details last, folded.
   Edit opens the form that already exists - no half-saved state to reconcile
   when you click away. */
function renderLpoDocSide(lpo) {
    const side = document.getElementById("lpoDocSide");
    if (!side) return;
    if (lpoState.signing) { renderLpoSigningSide(lpo); return; }
    const contract = lpoKind(lpo) !== "materials";
    side.innerHTML = lpoSummaryHtml(lpo) + lpoSignedHtml(lpo) + lpoWarningsHtml(lpo)
        + (contract ? lpoBillingSection(lpo) + lpoOrderedHtml(lpo) : lpoItemsHtml(lpo))
        + lpoLinkedHtml(lpo) + lpoDetailsHtml(lpo);

    side.querySelectorAll("[data-doc]").forEach(b => {
        b.onclick = safe(() => lpoDocAction(b.dataset.doc, lpo), "LPO action");
    });
    side.querySelectorAll("[data-next]").forEach(b => {
        b.onclick = safe(() => runLpoNextStep(lpo), "LPO next step");
    });
    side.querySelectorAll("[data-linked-kind]").forEach(b => {
        b.onclick = safe(async () => {
            const loader = { quote: "loadQuoteForEdit", dn: "loadDnForEdit", inv: "loadInvForEdit" }[b.dataset.linkedKind];
            if (typeof window[loader] === "function") return window[loader](b.dataset.linkedNo);
            toast("This document cannot be opened here.", true);
        }, "Open linked document");
    });
    side.querySelectorAll("[data-month-index]").forEach(b => {
        b.onclick = safe(async () => {
            const month = lpoState.billing?.months?.[Number(b.dataset.monthIndex)];
            if (month) return invoiceLpoPeriod(lpo, month);
        }, "Invoice this month");
    });
}

function lpoSummaryHtml(lpo) {
    const who = [esc(lpo.client_name)];
    if (lpo.lpo_date) who.push("dated " + esc(lpoDay(lpo.lpo_date)));
    const quote = lpo.source_quote_no
        ? `<span class="lpo-summary-line">Against <button type="button" class="lpo-inline-link" data-linked-kind="quote"
               data-linked-no="${esc(lpo.source_quote_no)}">${esc(lpo.source_quote_no)}</button></span>` : "";
    return `<section class="lpo-card lpo-summary">
        <div class="lpo-summary-top">
            <p class="lpo-summary-text"><span class="lpo-summary-line">${who.join(" &middot; ")}</span>${quote}</p>
            <button type="button" class="ghost lpo-sm" data-doc="edit">Edit</button>
        </div>
        ${lpo.notes ? `<p class="lpo-notes">${esc(lpo.notes)}</p>` : ""}
        <div class="lpo-figs">${lpoDocFigures(lpo).map(f => `<div class="lpo-fig-box">
            <span class="lpo-fig-k">${esc(f.k)}</span><span class="lpo-fig-v">${esc(f.v)}</span>${
            f.s ? `<span class="lpo-fig-s">${esc(f.s)}</span>` : ""}</div>`).join("")}</div>
    </section>`;
}

/* Three figures, each kind its own: a materials order is value, delivery and
   invoicing; a rental is billed money, months and the end; a supply contract
   has no total, so it is what has been invoiced and what has not. */
function lpoDocFigures(lpo) {
    const kind = lpoKind(lpo);
    const usd = (v) => lpoMoney(v);                 // rollups are canonical USD
    const total = Number(lpo.total_usd) || 0;
    if (kind === "rental") {
        const done = Number(lpo.months_invoiced) || 0, all = Number(lpo.months_total) || 0;
        const missed = Number(lpo.months_unbilled) || 0;
        return [
            { k: "Months invoiced", v: Number(lpo.term_months) > 0 ? `${done} of ${all}` : `${done}`,
              s: missed ? `${missed} missed` : "" },
            { k: "Billed", v: usd(lpo.invoiced_usd), s: total ? "of " + usd(total) : "" },
            { k: "Ends", v: lpoDate(lpo.period_end) ? lpoDay(lpo.period_end) : "No end date" },
        ];
    }
    if (kind === "open") {
        const n = (lpo.invoices || []).length, due = Number(lpo.months_unbilled) || 0;
        return [
            { k: "Invoiced", v: usd(lpo.invoiced_usd), s: `${n} invoice${n === 1 ? "" : "s"}` },
            { k: "Missed months", v: due ? String(due) : "None" },
            { k: "Started", v: lpoDay(lpo.period_start || lpo.lpo_date) || "-" },
        ];
    }
    const lines = (lpoState.fulfil && lpoState.fulfil.lines) || [];
    const delivered = lines.filter(l => l.delivered >= l.ordered - 0.001).length;
    return [
        { k: "Value", v: Number(lpo.total) ? lpoMoney(lpo.total, lpo.currency) : "Not recorded" },
        { k: "Delivered", v: lines.length ? `${delivered} of ${lines.length}` : "-", s: lines.length ? "items" : "" },
        { k: "Invoiced", v: usd(lpo.invoiced_usd), s: total ? "of " + usd(total) : "" },
    ];
}

/* The counter-signed copy is what the client is waiting for, so its state is a
   sentence with the one button that moves it on: Copy file to send it back,
   Sign and stamp to make it, Attach file to have something to sign. "No signed
   copy here", not "not signed": seven of the eight Abraj LPOs may well have been
   signed on paper. */
function lpoSignedHtml(lpo) {
    let cls, title, sub, btn;
    if (!lpo.file_exists) {
        cls = "is-missing"; title = "No document attached"; sub = "Attach the PDF or scan the client sent.";
        btn = `<button type="button" class="primary lpo-sm" data-doc="attach">Attach file</button>`;
    } else if (lpo.ack_exists) {
        cls = "is-done"; title = "Signed copy saved"; sub = "This is the file to send back.";
        btn = `<button type="button" class="secondary lpo-sm" data-doc="copy-signed"
                       title="Copy the signed copy, to paste into an email or chat">Copy file</button>`;
    } else {
        cls = "is-todo"; title = "No signed copy here"; sub = "Sign and stamp it, then send the copy back.";
        btn = `<button type="button" class="primary lpo-sm" data-doc="sign">Sign and stamp</button>`;
    }
    return `<section class="lpo-card lpo-signed ${cls}">
        <span class="lpo-signed-mark" aria-hidden="true"></span>
        <span class="lpo-signed-text"><strong>${title}</strong><span>${sub}</span></span>
        ${btn}
    </section>`;
}

/* Only when something is actually wrong - a warning that is always there is
   a warning nobody reads. */
function lpoWarningsHtml(lpo) {
    const out = [];
    if (lpo.over_value) {
        out.push(`<p class="lpo-warn">More has been invoiced than this LPO is worth. Check its invoices.</p>`);
    }
    return out.join("");
}

function lpoItemsHtml(lpo) {
    const fulfil = lpoState.fulfil || {};
    const lines = fulfil.lines || [];
    const extra = fulfil.extra || [];
    const eps = 0.001;
    const body = lines.length ? `<table class="lpo-lines-tbl">
        <thead><tr><th>Item</th><th>Ordered</th><th>Delivered</th><th>Invoiced</th></tr></thead>
        <tbody>${lines.map(l => `<tr>
            <td>${esc(l.item)}${l.description ? `<span class="lpo-sub">${esc(l.description)}</span>` : ""}</td>
            <td class="q">${esc(lpoQty(l.ordered))}${l.unit ? " " + esc(l.unit) : ""}</td>
            <td class="q${l.delivered < l.ordered - eps ? " is-owed" : ""}">${esc(lpoQty(l.delivered))}</td>
            <td class="q${l.invoiced < l.delivered - eps ? " is-owed" : ""}">${esc(lpoQty(l.invoiced))}</td>
        </tr>`).join("")}</tbody></table>`
        : `<p class="lpo-empty">No lines recorded. <button type="button" class="lpo-inline-link" data-doc="edit">Add them</button>
            from the quotation or by hand, and what is delivered and invoiced shows up here.</p>`;
    // Shipped or billed but never ordered - named in the document's own words,
    // because it explains a "0 delivered" that looks wrong.
    const stray = extra.length ? `<p class="lpo-sub">On its documents but not on this LPO: ${
        extra.map(e => esc(e.item || e.description || "item") + " (" + esc(lpoQty(e.qty)) + ")").join(", ")}</p>` : "";
    return `<section class="lpo-card"><h3 class="lpo-card-h">Items</h3>
        <div class="lpo-lines-scroll">${body}</div>${stray}</section>`;
}

/* A contract's lines, folded: the billing periods are what it is managed by. */
function lpoOrderedHtml(lpo) {
    const lines = lpoLines(lpo);
    if (!lines.length) return "";
    return `<details class="lpo-card lpo-fold"><summary>What they ordered
        <span class="lpo-fold-n">${lines.length} line${lines.length === 1 ? "" : "s"}</span></summary>
        <div class="lpo-lines-scroll"><table class="lpo-lines-tbl">
        <thead><tr><th>Item</th><th>Qty</th><th>Rate</th></tr></thead><tbody>${lines.map(l => {
            const rate = Number(String(l.rate == null ? "" : l.rate).replace(/,/g, ""));
            return `<tr><td>${esc(l.item)}${l.description ? `<span class="lpo-sub">${esc(l.description)}</span>` : ""}</td>
                <td class="q">${esc(lpoQty(l.qty))}${l.unit ? " " + esc(l.unit) : ""}</td>
                <td class="q">${rate ? esc(lpoMoney(rate, lpo.currency)) : "-"}</td></tr>`;
        }).join("")}</tbody></table></div></details>`;
}

/* Delivery notes and invoices, each opening its own editor. A contract's
   invoices are already listed against their periods, so they are not repeated. */
function lpoLinkedHtml(lpo) {
    const inPeriods = new Set(lpoKind(lpo) === "materials" ? []
        : ((lpoState.billing && lpoState.billing.months) || []).flatMap(m => m.invoices || []));
    const chip = (k, n) => `<button type="button" class="lpo-chip" data-linked-kind="${k}" data-linked-no="${esc(n)}">${esc(n)}</button>`;
    const chips = (lpo.dns || []).map(n => chip("dn", n))
        .concat((lpo.invoices || []).filter(n => !inPeriods.has(n)).map(n => chip("inv", n)));
    if (!chips.length) return "";
    return `<section class="lpo-card"><h3 class="lpo-card-h">Delivery notes and invoices</h3>
        <div class="lpo-chips">${chips.join("")}</div></section>`;
}

function lpoDetailsHtml(lpo) {
    const kind = lpoKind(lpo);
    const rows = [];
    const add = (k, v) => { if (v) rows.push([k, v]); };
    add("LPO date", lpoDay(lpo.lpo_date));
    if (kind !== "open" && Number(lpo.total)) add("Value as written", lpoMoney(lpo.total, lpo.currency));
    if (kind === "rental" && lpo.monthly_amount) add("Monthly rate (recorded)", lpoMoney(lpo.monthly_amount, lpo.currency));
    if (kind !== "materials") add("Period", lpoSpan(lpo.period_start || lpo.lpo_date, lpo.period_end));
    add("ERP No", lpo.erp_no);
    add("Project", lpo.project);
    add("Contact", lpo.contact);
    if (lpo.closed_at) {
        add("Closed", lpoDay(String(lpo.closed_at).slice(0, 10)) + (lpo.close_reason ? " - " + lpo.close_reason : ""));
    }
    add("File", lpo.filename);
    return `<details class="lpo-card lpo-fold"><summary>Details</summary>
        <dl class="lpo-dl">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    </details>`;
}

/* The months, as ONE list (2026-10-06): each month with how many invoices are
   dated in it. Waleed: "it can help me know if I am forgetting an invoice for
   this month or other month". A month that ended with none says "Missed" and
   offers Invoice; the current month says "Not invoiced yet" (no alarm);
   months to come say "Upcoming". Each word is the state - colour only repeats
   it. An invoice dated outside the contract's months is listed as "Outside
   the term" so nothing is hidden. Counting rules: backend/lpo_billing.py. */
function lpoBillingSection(lpo) {
    const ledger = lpoState.billing;
    const head = (extra) => `<h3 class="lpo-card-h">Invoices by month${extra || ""}</h3>`;
    if (!ledger || !ledger.ok) {
        return `<section class="lpo-card">${head()}<p class="lpo-empty">The months could not be loaded. Open this LPO again to retry.</p></section>`;
    }
    const months = ledger.months || [];
    const locked = !!lpo.closed_at;
    const fixed = Number(ledger.term_months) > 0;
    const done = Number(ledger.months_invoiced) || 0, all = Number(ledger.months_total) || 0;
    const count = all ? ` <span class="lpo-fold-n">${fixed ? `${done} of ${all}` : `${done} of ${all} so far`}</span>` : "";
    const WORD = { invoiced: "", missed: "Missed", current: "Not invoiced yet",
                   upcoming: "Upcoming", extra: "Outside the term" };
    const chip = (no) => `<button type="button" class="lpo-chip" data-linked-kind="inv" data-linked-no="${esc(no)}">${esc(no)}</button>`;
    const rows = months.map((m, i) => {
        const word = m.count
            ? `${m.count} invoice${m.count === 1 ? "" : "s"}${m.state === "extra" ? " &middot; outside the term" : ""}`
            : WORD[m.state] || "";
        const acts = (m.invoices || []).map(chip);
        if (m.expected && !m.count && !locked) {
            acts.push(`<button type="button" class="${m.state === "missed" ? "secondary" : "ghost"} lpo-sm" data-month-index="${i}">Invoice</button>`);
        }
        return `<div class="lpo-period is-${m.state}">
            <span class="lpo-period-info">
                <span class="lpo-period-when">${esc(m.label)}</span>
                <span class="lpo-period-state">${word}${m.covers ? ` &middot; covers ${esc(m.covers)}` : ""}</span>
            </span>
            <span class="lpo-period-act">${acts.join("")}</span>
        </div>`;
    });
    const unsent = Number(ledger.invoices_not_sent) || 0;
    return `<section class="lpo-card lpo-billing">${head(count)}
        ${unsent ? `<p class="lpo-note">${unsent} invoice${unsent === 1 ? " is" : "s are"} not marked sent yet
            - the LPO is done once every month is invoiced and sent.</p>` : ""}
        ${rows.length ? `<div class="lpo-periods">${rows.join("")}</div>`
                      : `<p class="lpo-empty">Record the contract's start date to see its months.</p>`}
    </section>`;
}

/* WHICH file the Open / Print / Copy buttons act on (2026-09-19).

   One rule, one place. They used to spell it three different ways and only
   Open followed the viewer, so signing an LPO and pressing "Copy file" put
   the client's own UNSIGNED original on the clipboard - the one button whose
   entire purpose is sending the document back to them.

     1. what is on screen, when the viewer is showing THIS lpo - the Original
        / Signed copy switch has to mean something;
     2. else the signed copy, when one exists. `showLpoDoc` already opens on
        `ack_exists ? "ack" : "file"`; this is the same rule;
     3. the client's original only when nothing has been signed.

   The lpo_no guard matters: the panel and the viewer are separate pieces of
   state, and handing over a different LPO's file is worse than handing over
   the wrong version of this one. */
function lpoSendPath(lpo) {
    const V = lpoState.viewer;
    if (V && V.path && V.lpo && V.lpo.lpo_no === lpo.lpo_no
        && String(V.lpo.client_name || "") === String(lpo.client_name || "")) return V.path;
    if (lpo.ack_exists && lpo.ack_path) return lpo.ack_path;
    return lpo.file_path;
}

async function lpoDocAction(act, lpo) {
    if (act === "dn") return window.createDnFromLpo(lpo.client_name, lpo.lpo_no);
    if (act === "inv") return window.createInvoiceFromLpo(lpo.client_name, lpo.lpo_no);
    if (act === "sign") return startLpoSigning(lpo);
    if (act === "open") return window.afterExportPdf(lpoSendPath(lpo), lpo.lpo_no);
    // The folder is the same either way - the signed copy is written beside
    // the original as `<no>.acknowledged.pdf` - so this still reveals the
    // record's own file.
    if (act === "folder") return api().reveal_in_folder(lpo.file_path);
    if (act === "print") return window.printPdfFile && window.printPdfFile(lpoSendPath(lpo));
    if (act === "copy") return window.copyFileToClipboard && window.copyFileToClipboard(lpoSendPath(lpo));
    // The signed-copy line's own button: always the counter-signed file, even
    // when the viewer happens to be showing the original.
    if (act === "copy-signed") {
        return window.copyFileToClipboard && window.copyFileToClipboard(lpo.ack_path || lpoSendPath(lpo));
    }
    if (act === "edit") {
        const saved = await openLpoForm(lpo);
        if (saved) await reopenLpoDoc(lpo);
        return;
    }
    if (act === "attach") {
        if (await attachLpoFile(lpo)) return reopenLpoDoc(lpo);
        return;
    }
    if (act === "close") { await closeLpoRow(lpo); return reopenLpoDoc(lpo); }
    if (act === "delete") {
        const go = await window.Popup.confirm(
            `Remove ${lpo.lpo_no}?`,
            "This forgets the record. The client's file stays on disk - delete it yourself if you want it gone.",
            { confirm: "Remove", cls: "danger" });
        if (!go) return;
        await api().delete_lpo(lpo.client_name, lpo.lpo_no);
        await refreshLpos();
        return showLpos();
    }
}

/* Re-read the record after something changed it, so the panel and the list can
   never disagree about what is on file. */
async function reopenLpoDoc(lpo) {
    await refreshLpos();
    const fresh = lpoState.rows.find(
        r => r.client_name === lpo.client_name && r.lpo_no === lpo.lpo_no);
    if (!fresh) return showLpos();
    await showLpoDoc(fresh);
}

/* ---- signing, in the same screen -------------------------------------- */

async function startLpoSigning(lpo) {
    if (!lpo.file_exists) { toast("Attach the client's document first", true); return; }
    // Fetched HERE rather than on every open: viewing a document does not need
    // the images at all, and they are two thirds of the bridge traffic.
    const assets = await lpoAssets();
    if (!assets.has_sig && !assets.has_stamp) {
        toast("Upload a signature or stamp in Settings first", true);
        return;
    }
    lpoState.signing = true;
    // ALWAYS from the client's original: signing the acknowledged copy would
    // stack a second stamp on top of the first.
    openLpoDocFile(lpo, "file").then(() => {
        document.getElementById("lpoVHint").hidden = false;
        updateLpoMarkCount();
        renderLpoDocHeader(lpo);
        renderLpoDocSide(lpo);
    });
}

function stopLpoSigning(lpo) {
    lpoState.signing = false;
    const hint = document.getElementById("lpoVHint");
    if (hint) hint.hidden = true;
    if (lpoState.viewer) lpoState.viewer.marks = [];
    return reopenLpoDoc(lpo);
}

function renderLpoSigningSide(lpo) {
    const side = document.getElementById("lpoDocSide");
    if (!side) return;
    side.innerHTML = `
        <section class="lpo-card lpo-signing">
            <h3 class="lpo-card-h">Sign ${esc(lpo.lpo_no)}</h3>
            <ol class="lpo-sign-steps">
                <li>Add a signature or stamp from the toolbar above the page.</li>
                <li>Drag it onto their signature block; drag the corner to resize.</li>
                <li>Save. The box shows the real image, so what you see is what is merged.</li>
            </ol>
            <button class="primary" data-sign="apply">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
                <span>Save signed copy</span>
            </button>
            <button class="ghost" data-sign="cancel">Cancel</button>
            <p class="lpo-note">The client's original is never modified. This writes a separate copy to send back.</p>
        </section>`;
    side.querySelectorAll("[data-sign]").forEach(b => {
        b.onclick = safe(async () => {
            if (b.dataset.sign === "cancel") return stopLpoSigning(lpo);
            await applyLpoSigning(lpo, b);
        }, "Sign LPO");
    });
}

async function applyLpoSigning(lpo, btn) {
    const V = lpoState.viewer;
    if (!V || !V.marks.length) { toast("Place a signature or stamp first", true); return; }
    let r;
    await withLoading(btn, async () => {
        r = await api().acknowledge_lpo(lpo.client_name, lpo.lpo_no,
            V.marks.map(m => ({ page: m.page - 1, kind: m.kind, x: m.x, y: m.y, w: m.w, h: m.h })));
    });
    if (!r || !r.ok) { toast("Could not sign: " + (r && r.error || "unknown"), true); return; }
    // Report what actually LANDED. Saying "done" when fewer marks merged than
    // were placed is the failure you only notice when the client asks where the
    // stamp is.
    const applied = Number(r.applied || 0), asked = Number(r.requested || V.marks.length);
    if (applied < asked) {
        toast(`${lpo.lpo_no}: only ${applied} of ${asked} marks were applied - `
            + "check the saved copy before sending it.", true);
    } else {
        toast(`${lpo.lpo_no} signed - ${applied} mark${applied === 1 ? "" : "s"} applied. `
            + "Send the saved copy to the client.");
    }
    lpoState.signing = false;
    const hint = document.getElementById("lpoVHint");
    if (hint) hint.hidden = true;
    await reopenLpoDoc(lpo);
}

function _b64ToBytes(dataUri) {
    const b64 = String(dataUri).split(",")[1] || "";
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

/* EVERY page, stacked and scrollable (2026-09-12).

   It used to paint one page into the stage and swap it on Previous/Next, so a
   two-page PO could only be read a page at a time and the second page was easy
   to miss entirely. Waleed asked for pages you can scroll, here as well as in
   the To-Do popup.

   The placement machinery is untouched in the part that matters: a mark is
   still a FRACTION of its own page, and it now lives inside that page's own
   box, so page 2's stamp cannot be measured against page 1's canvas. Previous
   and Next scroll rather than re-render, and `V.page` - which is what
   `addLpoMark` places onto - follows whatever page is in view. */
async function renderLpoPage() {
    const V = lpoState.viewer;
    if (!V) return;
    const wrap = document.getElementById("lpoCanvasWrap");
    if (!wrap) return;
    // A newer render (another LPO, or a second zoom click) clears the stage and
    // starts again; this one must stop rather than append its pages to it.
    const pass = V.renderPass = (V.renderPass || 0) + 1;
    const stale = () => lpoState.viewer !== V || V.renderPass !== pass;
    wrap.innerHTML = "";
    V.pageEls = {};
    V.pageMms = {};

    const box = (n) => {
        const d = document.createElement("div");
        d.className = "lpo-v-pg";
        d.dataset.page = String(n);
        wrap.appendChild(d);
        V.pageEls[n] = d;
        return d;
    };
    const caption = (host, n, total) => {
        if (total < 2) return;
        const s = document.createElement("span");
        s.className = "lpo-v-pgno";
        s.textContent = `Page ${n} of ${total}`;
        host.appendChild(s);
    };

    if (V.isImage) {
        // A photographed LPO: one page, shown as-is. The backend wraps it onto
        // an A4 sheet when acknowledging, and fractions map the same either way.
        const img = new Image();
        img.src = V.dataUri;
        img.className = "lpo-v-img";
        await new Promise(res => { img.onload = res; img.onerror = res; });
        if (stale()) return;
        img.style.width = Math.round(img.naturalWidth * V.scale * 0.5) + "px";
        box(1).appendChild(img);
        V.pages = 1;
        V.page = 1;
    } else {
        for (let n = 1; n <= V.pages; n++) {
            const page = await V.doc.getPage(n);
            if (stale()) return;
            // The page's TRUE size in mm, so the size readout is honest on a
            // Letter or A5 scan instead of assuming A4. pdf.js reports points.
            const base = page.getViewport({ scale: 1 });
            V.pageMms[n] = { w: base.width * 25.4 / 72, h: base.height * 25.4 / 72 };
            const viewport = page.getViewport({ scale: V.scale });
            const canvas = document.createElement("canvas");
            canvas.width = Math.floor(viewport.width);
            canvas.height = Math.floor(viewport.height);
            canvas.className = "lpo-v-canvas";
            const host = box(n);
            host.appendChild(canvas);
            caption(host, n, V.pages);
            await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
            if (stale()) return;
        }
        V.pageMm = V.pageMms[V.page] || V.pageMms[1];
    }
    watchLpoScroll();
    paintLpoPageLabel();
    paintLpoMarks();
}

// The page a mark would land on: whichever one the stage is actually showing.
function watchLpoScroll() {
    const stage = document.getElementById("lpoStage");
    if (!stage || stage.dataset.lpoScrollBound === "1") return;
    stage.dataset.lpoScrollBound = "1";
    stage.addEventListener("scroll", () => {
        const V = lpoState.viewer;
        if (!V || !V.pageEls) return;
        // The page covering the middle of the viewport - not the top edge,
        // which would flip to the next page while most of this one is still
        // on screen.
        const mid = stage.clientHeight / 2;
        let seen = 1;
        // Measured against the stage's own box, not `offsetTop`: the page boxes
        // sit inside a positioned wrapper, so their offsetTop is relative to
        // THAT and would be short by the wrapper's own offset.
        const top = stage.getBoundingClientRect().top;
        Object.keys(V.pageEls).forEach((k) => {
            const el = V.pageEls[k];
            if (el && el.getBoundingClientRect().top - top <= mid) seen = Number(k);
        });
        if (seen !== V.page) { V.page = seen; paintLpoPageLabel(); }
    });
}

function paintLpoPageLabel() {
    const V = lpoState.viewer;
    const el = document.getElementById("lpoPageLbl");
    if (el && V) el.textContent = `${V.page} / ${V.pages}`;
}

function scrollLpoToPage(n) {
    const V = lpoState.viewer;
    if (!V) return;
    V.page = Math.max(1, Math.min(V.pages, n));
    paintLpoPageLabel();
    const el = V.pageEls && V.pageEls[V.page];
    const stage = document.getElementById("lpoStage");
    if (el && stage && el.getBoundingClientRect) {
        const to = stage.scrollTop
            + (el.getBoundingClientRect().top - stage.getBoundingClientRect().top);
        stage.scrollTo({ top: Math.max(0, to - 12), behavior: "smooth" });
    }
}

// Page dimensions in mm for the live size readout. The viewer works in
// fractions of the page, so anything non-A4 still reports true millimetres.
function lpoPageMm(n) {
    const V = lpoState.viewer;
    const surface = document.querySelector("#lpoCanvasWrap canvas, #lpoCanvasWrap img");
    if (!V || !surface) return { w: 210, h: 297 };
    if (n && V.pageMms && V.pageMms[n]) return V.pageMms[n];
    // Each page carries its own size: a PO whose second sheet is a different
    // format must not be measured against the first.
    // Fall back to A4 unless the PDF told us otherwise (pdf.js gives points).
    return (V.pageMms && V.pageMms[V.page]) || V.pageMm || { w: 210, h: 297 };
}

function addLpoMark(kind) {
    const V = lpoState.viewer;
    if (!V) return;
    const assets = lpoState.assets || {};
    // Refuse rather than add an invisible box that would produce nothing:
    // `_resolve_marks` skips a mark whose asset is missing, so the acknowledge
    // would silently do less than asked.
    if (kind === "stamp" && !assets.has_stamp) {
        toast("No stamp image uploaded - add one in Settings → Signature & stamp", true);
        return;
    }
    if (kind === "sig" && !assets.has_sig) {
        toast("No signature image uploaded - add one in Settings → Signature & stamp", true);
        return;
    }
    // Default footprint = what the app prints everywhere else, as a fraction of
    // the page. Read from the live settings so it tracks Settings changes.
    const sz = assets.size || {};
    const pg = lpoPageMm();
    const wmm = kind === "stamp" ? (+sz.stamp_w_mm || 46.5) : (+sz.sig_w_mm || 55);
    const hmm = kind === "stamp" ? (+sz.stamp_h_mm || 29.0) : (+sz.sig_h_mm || 22);

    // Cascade each new mark so a second one can NEVER land exactly beneath the
    // first - that made the stamp invisible and looked like it was never added.
    const n = V.marks.filter(m => m.page === V.page).length;
    const step = 0.035 * n;
    V.marks.push({
        kind, page: V.page,
        x: Math.min(0.12 + step, 0.75),
        y: Math.min(0.72 + step, 0.85),
        w: wmm / pg.w, h: hmm / pg.h,
    });
    paintLpoMarks();
}

function lpoMarkImage(kind) {
    const a = lpoState.assets || {};
    return kind === "stamp" ? (a.stamp_data_uri || "") : (a.sig_data_uri || "");
}

function updateLpoMarkReadout(el, mark) {
    // The mark's OWN page, not whichever one is in view: with every page on
    // screen at once they are no longer the same thing.
    const pg = lpoPageMm(mark.page);
    const w = Math.round(mark.w * pg.w * 10) / 10;
    const h = Math.round(mark.h * pg.h * 10) / 10;
    const tag = el.querySelector(".lpo-mark-tag");
    if (tag) tag.textContent =
        `${mark.kind === "stamp" ? "Stamp" : "Signature"} · ${w}×${h} mm`;
}

function paintLpoMarks() {
    const V = lpoState.viewer;
    const wrap = document.getElementById("lpoCanvasWrap");
    if (!V || !wrap) return;
    wrap.querySelectorAll(".lpo-mark").forEach(el => el.remove());

    V.marks.forEach((m, idx) => {
        // Every page is on screen now, so every mark is painted - on its OWN
        // page, against its OWN canvas. Measuring page 2 against page 1 would
        // put the stamp somewhere else entirely if the sheets differ.
        const host = (V.pageEls && V.pageEls[m.page]) || wrap;
        const surface = host.querySelector("canvas, img") || wrap.querySelector("canvas, img");
        if (!surface) return;
        const W = surface.clientWidth, H = surface.clientHeight;
        const el = document.createElement("div");
        el.className = "lpo-mark lpo-mark-" + m.kind;
        el.style.left = (m.x * W) + "px";
        el.style.top = (m.y * H) + "px";
        el.style.width = (m.w * W) + "px";
        el.style.height = (m.h * H) + "px";
        // THE actual signature/stamp, not an empty outline - this is what makes
        // the size judgeable before committing. The CSS uses `object-fit:
        // contain`, matching `lpo_ack._overlay_html`, so the preview and the
        // merged result are the same shape. Keep those two in step.
        el.innerHTML =
            `<img class="lpo-mark-img" src="${lpoMarkImage(m.kind)}" alt="" draggable="false">
             <span class="lpo-mark-tag"></span>
             <button class="lpo-mark-del" title="Remove">×</button>
             <span class="lpo-mark-grip"></span>`;
        host.appendChild(el);
        updateLpoMarkReadout(el, m);

        el.querySelector(".lpo-mark-del").onclick = (e) => {
            e.stopPropagation();
            V.marks.splice(idx, 1);
            paintLpoMarks();
            updateLpoMarkCount();
        };
        el.querySelector(".lpo-mark-grip").addEventListener("mousedown",
            (e) => startLpoDrag(e, m, el, surface, "resize"));
        el.addEventListener("mousedown", (e) => {
            if (e.target.closest(".lpo-mark-del, .lpo-mark-grip")) return;
            startLpoDrag(e, m, el, surface, "move");
        });
    });
    updateLpoMarkCount();
}

// A running tally, so "I placed two" is verifiable before you commit.
function updateLpoMarkCount() {
    const V = lpoState.viewer;
    const el = document.getElementById("lpoVHint");
    if (!V || !el) return;
    const total = V.marks.length;
    if (!total) {
        el.textContent = "Add a signature or stamp, then drag it onto the client's signature block. Drag the corner to resize.";
        el.classList.remove("is-ready");
        return;
    }
    const per = {};
    V.marks.forEach(m => { per[m.page] = (per[m.page] || 0) + 1; });
    const where = Object.keys(per).sort((a, b) => a - b)
        .map(p => `${per[p]} on page ${p}`).join(", ");
    el.textContent = `${total} mark${total === 1 ? "" : "s"} placed (${where}). `
        + `They are applied exactly as shown when you save.`;
    el.classList.add("is-ready");
}

function startLpoDrag(e, mark, el, surface, mode) {
    e.preventDefault(); e.stopPropagation();
    const W = surface.clientWidth, H = surface.clientHeight;
    const startX = e.clientX, startY = e.clientY;
    const from = { x: mark.x, y: mark.y, w: mark.w, h: mark.h };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    el.classList.add("is-dragging");

    const onMove = (ev) => {
        const dx = (ev.clientX - startX) / W;
        const dy = (ev.clientY - startY) / H;
        if (mode === "move") {
            mark.x = clamp(from.x + dx, 0, 1 - mark.w);
            mark.y = clamp(from.y + dy, 0, 1 - mark.h);
        } else {
            // Keep a floor so a box can never be resized to nothing.
            mark.w = clamp(from.w + dx, 0.03, 1 - mark.x);
            mark.h = clamp(from.h + dy, 0.015, 1 - mark.y);
        }
        el.style.left = (mark.x * W) + "px";
        el.style.top = (mark.y * H) + "px";
        el.style.width = (mark.w * W) + "px";
        el.style.height = (mark.h * H) + "px";
        updateLpoMarkReadout(el, mark);      // live mm while dragging/resizing
    };
    const onUp = () => {
        el.classList.remove("is-dragging");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
}

// ============================================================
// Wiring
// ============================================================
function initLpoModule() {
    const on = (id, ev, fn) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener(ev, fn);
    };
    on("btnLpoClose", "click", () => { if (window.showHub) showHub(); });
    on("btnLpoNew", "click", safe(() => openLpoForm(null), "Add LPO"));
    on("lpoSearch", "input", (e) => { lpoState.search = e.target.value; renderLpoList(); });
    on("btnLpoDocBack", "click", () => showLpos());

    const list = document.getElementById("lpoList");
    if (list) {
        // The ROW opens the LPO; the one button in it does the next step.
        list.addEventListener("click", safe(async (e) => {
            const lpo = lpoRowFromEvent(e);
            if (!lpo) return;
            if (e.target.closest("[data-next]")) return runLpoNextStep(lpo);
            return showLpoDoc(lpo);
        }, "LPO action"));
        list.addEventListener("keydown", (e) => {
            if (e.key !== "Enter" || e.target.closest("button")) return;
            const lpo = lpoRowFromEvent(e);
            if (lpo) { e.preventDefault(); showLpoDoc(lpo); }
        });
    }

    // Fit width stays true when the window is resized.
    let fitTimer = 0;
    if (window.addEventListener) window.addEventListener("resize", () => {
        clearTimeout(fitTimer);
        fitTimer = setTimeout(async () => {
            const view = document.getElementById("lpoDocView");
            const V = lpoState.viewer;
            if (!V || !view || view.hidden || lpoState.zoom !== "fit") return;
            const scale = await lpoFitScale(V);
            if (Math.abs(scale - V.scale) < 0.02) return;
            V.scale = scale;
            await renderLpoPage();
            paintLpoZoom();
        }, 250);
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLpoModule);
} else {
    initLpoModule();
}
