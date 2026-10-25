/* =========================================================
   To-Do / Needs Attention  (Phase X.26)
   ---------------------------------------------------------
   The "View all" target of the dashboard Needs-Attention card. One dedicated
   screen with organized sections so the user can finish what's unfinished:
   2026-08-10 - this screen shows EVERYTHING outstanding, across all months.
   It used to scope Uncosted / Unsubmitted / Deliver-and-invoice to the picked
   month, so anything left over rolled out of sight at the month boundary -
   in a list whose only job is to stop work being forgotten. The month picker
   is now an opt-in "One month only" filter, and every row shows how long it
   has been waiting (`age_days`, oldest first).

   2026-10-21 (Waleed) - ONE page in the Insights layout: a KPI row, then an
   Insights table per type, sortable on every heading. See the comment
   above SECTIONS.

     • Decide      - open quotes to mark Won/Lost before the 14-day auto-loss.
     • Uncosted    - docs lacking cost data.
     • Unsubmitted - docs not marked submitted.
     • Unpaid      - outstanding invoices not marked paid.
     • Deliver     - client POs with items not yet invoiced.

   Data: api.dashboard_needs_attention() -> {decide, uncosted, unsubmitted,
   unpaid, deliver, ignored}; call it with (year, month) only when the user
   picks "One month only". Actions: set_bid_status / set_submitted /
   set_invoice_paid.

   Phase X.29 - clicking a row opens a read-only PREVIEW MODAL (items table,
   scrollable) with the section's action(s) in its top-right corner: Won/Lost
   (decide), "Add costs" → the document's own cost column (uncosted), "Mark submitted"
   (unsubmitted), "Mark paid" (unpaid). Doc data loads via api.load_quote /
   load_dn / load_invoice (bridge attaches per-doc `columns`; a fallback
   derives columns from row keys for docs saved before Phase T).
   Self-contained IIFE; reuses window.DASH_ICON / window.DASH_CASE_ICON.
   Exposes the same preview modal to the dashboard via window.openTodoDocModal.
   ========================================================= */
(function () {
    "use strict";

    const $   = (id) => document.getElementById(id);
    const api = () => window.pywebview.api;
    const MONTHS = ["January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"];
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));

    const KIND_BADGE = { quote: "QUT", dn: "DN", inv: "INV", po: "PO", lpo: "LPO" };
    const DOC_ICON  = (k) => (window.DASH_ICON && window.DASH_ICON[k]) || "";
    const CASE_ICON = (c) => (window.DASH_CASE_ICON && window.DASH_CASE_ICON[c]) || "";
    // Phase X.30 - mute ("Ignore") / restore icons.
    const ICON_EYEOFF = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
    const ICON_RESTORE = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
    // Arrow shared by every "this takes you out of here" action.
    const ICON_ARROW = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';

    // ONE place that knows how to open a document in its own editor. The row
    // buttons and the preview modal's top-right both go through this; the map
    // used to be copy-pasted into each handler, so a new doc type had to be
    // remembered twice.
    // `lpo` is the client's own document, not one of ours, so it has no editor
    // to load a record into - but it does have a screen of its own, and
    // `openLpoByNo` opens THAT LPO there rather than the list (2026-09-12).
    // The client is passed through because a PO number alone is not a key.
    const EDIT_LOADER = { quote: "loadQuoteForEdit", inv: "loadInvForEdit",
                          dn: "loadDnForEdit", po: "loadForEdit", lpo: "openLpoByNo" };
    function openDocEditor(kind, no, client) {
        const fn = window[EDIT_LOADER[kind]];
        if (typeof fn !== "function") {
            // Fail loudly-but-politely instead of a dead button: the editor
            // module for this doc type isn't loaded.
            if (window.toast) window.toast("Can't open this document type here.", true);
            return false;
        }
        try { fn(no, client); return true; } catch (e) { return false; }
    }

    let _year = 2026, _month = 6, _scope = "all", _initOnce = false, _data = null;
    // The page fades in once per visit; a re-render after an action must not
    // replay it (the table would blink under the button you just pressed).
    let _enter = false;

    // ------------------------------------------------------------------ show
    async function showTodo() {
        document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
        ["poStrip", "poActions", "quoteActions", "dnActions", "invActions"]
            .forEach((id) => { const el = $(id); if (el) el.hidden = true; });
        document.body.classList.remove("on-dashboard", "has-rail");
        const rail = $("typeRail"); if (rail) rail.hidden = true;
        if (window.OpenPanel) window.OpenPanel.hide();

        $("todoView").hidden = false;
        const home = $("btnHome"); if (home) home.hidden = false;
        if (window.setBrand) window.setBrand("To-Do");
        if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();

        if (!_initOnce) { initControls(); _initOnce = true; }
        _enter = true;
        await load();
    }
    window.showTodo = showTodo;

    function syncScopeControls() {
        const on = _scope === "month";
        const mc = $("todoMonthCtl"), yc = $("todoYearCtl");
        if (mc) mc.hidden = !on;
        if (yc) yc.hidden = !on;
    }

    function initControls() {
        const msel = $("todoMonth");
        msel.innerHTML = MONTHS.map((n, i) => `<option value="${i + 1}">${n}</option>`).join("");
        const now = new Date();
        _year = now.getFullYear();
        _month = now.getMonth() + 1;
        msel.value = _month;
        $("todoYear").value = _year;
        msel.addEventListener("change", () => { _month = +msel.value; load(); });
        $("todoYear").addEventListener("change", () => { _year = +$("todoYear").value; load(); });
        const scope = $("todoScope");
        if (scope) {
            scope.value = _scope;
            scope.addEventListener("change", () => {
                _scope = scope.value === "month" ? "month" : "all";
                syncScopeControls();
                load();
            });
        }
        syncScopeControls();
        $("todoRefresh").onclick = () => load();
        $("todoBack").onclick = () => { if (window.showHub) window.showHub(); };
    }

    // ------------------------------------------------------------------ data
    async function load() {
        const body = $("todoBody");
        // Only the first load shows "Loading"; a refresh after an action keeps
        // the table on screen until the new rows arrive (no flash of nothing).
        if (body && !_data) body.innerHTML = `<div class="todo-loading">Loading…</div>`;
        let res = null;
        // No arguments = everything outstanding. Only the explicit
        // "One month only" filter narrows it.
        try {
            res = (_scope === "month")
                ? await api().dashboard_needs_attention(_year, _month)
                : await api().dashboard_needs_attention();
        }
        catch (e) { res = null; }
        _data = res || { decide: [], uncosted: [], unsubmitted: [], unpaid: [], ignored: [] };
        render();
    }

    /* "Add costs" opens the DOCUMENT (2026-09-11).

       It used to open a separate Costing & Profit screen. That screen was
       retired because the cost column lives in the document's own editor -
       behind the privacy strip - and that is where all 146 costed documents
       were actually costed. Sending the user to a second place to type a
       number that belongs on the row in front of them was the detour, not the
       feature. `date` is no longer needed: the editor loads by number. */
    function openInCosting(kind, no) {
        return openDocEditor(kind, no);
    }

    // ------------------------------------------------------------------ render
    /* ONE page of Insights tables (2026-10-21, Waleed).

       The screen used to stack every type as a block of small cards, each block
       scrolling on its own inside a page that also scrolled - a 31-row Unpaid
       block caught the wheel half the time and the page the other half. Waleed
       asked for the Insights table instead: the same `.in-kpis` summary row on
       top, then one `.in-sect` heading and one `.in-card` holding an
       `.in-table` per type, all on one page. Headings sort exactly as they do
       on Insights; a click on a row opens the shared preview popup. The tables
       never scroll on their own - the page is the only scroller.

       Each table shows what that work is judged by: an unpaid invoice its
       amount and how long it has waited, a bid its countdown to auto-loss, a
       PO how many of its lines are still to invoice. Money is always shown in
       the document's own currency; sorting and totals use the dollar figure
       (`amount_usd`) the backend sends beside it. Colour never carries
       anything alone - a late row says how late in words. */
    const SECTIONS = [
        { cat: "decide", label: "Decide - Won or Lost",
          hint: "Open quotes nearing the 14-day auto-loss. Mark the outcome so profit stays accurate." },
        { cat: "billing", label: "Bill this month",
          hint: "Rentals and supply contracts with a month nobody has invoiced yet." },
        { cat: "deliver", label: "Deliver & Invoice",
          hint: "Client POs with items not yet invoiced. Deliver and invoice them to clear. Office lines are ignored." },
        { cat: "unsubmitted", label: "Unsubmitted",
          hint: "Documents not yet marked as submitted." },
        { cat: "uncosted", label: "Uncosted",
          hint: "Documents with no cost entered. Their profit is an estimate until you add the real costs." },
        // Unpaid LAST of the work (2026-10-21, Waleed): payment takes the
        // client weeks; work that can be done today comes first.
        { cat: "unpaid", label: "Unpaid",
          hint: "Invoices not yet marked as paid. Mark them once the client pays." },
        { cat: "ignored", label: "Ignored",
          hint: "Muted - these won't ask for attention. Restore any that should." },
    ];
    const NOUN = {
        decide: ["bid", "bids"], billing: ["contract", "contracts"], deliver: ["PO", "POs"],
        unpaid: ["invoice", "invoices"], ignored: ["muted item", "muted items"],
    };
    const KIND_WORD = { quote: "Quotation", inv: "Invoice", dn: "Delivery note",
                        po: "Purchase order", lpo: "Client LPO" };
    const SUB_WORD = { materials: "Materials", equipment: "Equipment", manpower: "Manpower",
                       service: "Service", services: "Services", rental: "Rental", supply: "Supply" };
    const CAT_WORD = { decide: "Decide", billing: "Bill this month", deliver: "Deliver & Invoice",
                       unsubmitted: "Unsubmitted", uncosted: "Uncosted", unpaid: "Unpaid" };
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const LATE_DAYS = 45;
    const AGING_DAYS = 21;    // three weeks: worth a look, not yet late

    let _query = "";          // the filter box; filters every table at once
    const _sort = {};         // cat -> { key, dir }; until a heading is clicked, the backend's order
    // Ignored only ever grows and is not work, so its table starts closed.
    // Module-scoped so an open survives the reload that follows an action.
    let _ignoredOpen = false;

    /* "31/8/2026" (how documents store dates) or ISO, as a number - never
       `new Date(string)`, which reads 4/6/2026 as April. Same as Insights. */
    function dateVal(v) {
        const s = String(v || "").trim();
        let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
        if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3]);
        m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
        return m ? Date.UTC(+m[3], +m[2] - 1, +m[1]) : null;
    }
    function fmtDate(v) {
        const t = dateVal(v);
        if (t == null) return v ? esc(v) : "-";
        const d = new Date(t);
        return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    }
    function fmtMoney(n, ccy) {
        if (!Number.isFinite(n)) return "-";
        if (ccy === "IQD") return "IQD " + Math.round(n).toLocaleString("en-US");
        return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    const days = (n) => (n === 1 ? "1 day" : `${n} days`);
    function typeText(r) {
        const kind = KIND_WORD[r.kind] || String(r.kind || "").toUpperCase();
        const sub = String(r.sub_type || r.lpo_kind || "").toLowerCase();
        return sub ? `${kind} · ${SUB_WORD[sub] || sub[0].toUpperCase() + sub.slice(1)}` : kind;
    }

    // Every column the tables draw from, in Insights' shape: `left` aligns it
    // left (Insights right-aligns by default), `first` is its first sort
    // direction, `value` what it sorts on, `cell` what it shows, and `late`
    // marks the cell the way Insights marks a loss (`td.is-neg`) - always
    // beside the words that say it.
    const COL = {
        doc: { label: "Document", left: true, first: 1, value: (r) => r.doc_no || "",
               cell: (r) => `<span class="todo-kind k-${esc(r.kind)}">${esc(KIND_BADGE[r.kind] || r.kind || "")}</span>`
                          + `<b class="todo-no">${esc(r.doc_no || "")}</b>` },
        client: { label: "Client", left: true, first: 1, value: (r) => String(r.client || "").toLowerCase(),
                  cell: (r) => `<span class="todo-client" title="${esc(r.client || "")}">${esc(r.client || "-")}</span>` },
        type: { label: "Type", left: true, first: 1, value: (r) => typeText(r), cell: (r) => esc(typeText(r)) },
        date: { label: "Date", value: (r) => dateVal(r.date), cell: (r) => fmtDate(r.date) },
        waiting: { label: "Waiting", value: (r) => (Number.isFinite(r.age_days) ? r.age_days : null),
                   late: (r) => r.age_days >= LATE_DAYS,
                   cell: (r) => {
                       const a = r.age_days;
                       if (!Number.isFinite(a)) return "-";
                       const tier = a >= LATE_DAYS ? " is-late" : a >= AGING_DAYS ? " is-aging" : "";
                       return `<span class="todo-age${tier}">${a <= 0 ? "Today" : days(a)}</span>`;
                   } },
        amount: { label: "Amount", value: (r) => (Number.isFinite(r.amount_usd) ? r.amount_usd : null),
                  cell: (r) => (Number.isFinite(r.amount) ? fmtMoney(r.amount, r.currency) : "-") },
        open: { label: "Open for", value: (r) => Number(r.days_open) || 0,
                cell: (r) => days(Number(r.days_open) || 0) },
        left: { label: "Auto-lost in", first: 1, value: (r) => Number(r.days_left) || 0,
                late: (r) => (Number(r.days_left) || 0) <= 2,
                cell: (r) => { const n = Number(r.days_left) || 0; return n <= 0 ? "Today" : days(n); } },
        lines: { label: "Lines to invoice", value: (r) => r.open_lines,
                 cell: (r) => (Number.isFinite(r.open_lines) ? `${r.open_lines} of ${r.client_lines}` : "-") },
        oldest: { label: "Oldest month", left: true, value: (r) => { const t = Date.parse("1 " + (r.oldest_month || "")); return Number.isNaN(t) ? null : t; },
                  cell: (r) => esc(r.oldest_month || "-") },
        due: { label: "Months due", value: (r) => Number(r.months_due) || 0,
               late: (r) => (Number(r.months_due) || 0) >= 3,
               cell: (r) => { const n = Number(r.months_due) || 0; return `${n} month${n === 1 ? "" : "s"}`; } },
        muted: { label: "Muted from", left: true, first: 1, value: (r) => CAT_WORD[r.category] || r.category || "",
                 cell: (r) => esc(CAT_WORD[r.category] || r.category || "-") },
    };
    const COLS_FOR = {
        decide:      ["doc", "client", "type", "date", "open", "left", "amount"],
        billing:     ["doc", "client", "type", "oldest", "due"],
        deliver:     ["doc", "client", "date", "waiting", "lines"],
        unsubmitted: ["doc", "client", "type", "date", "waiting", "amount"],
        uncosted:    ["doc", "client", "type", "date", "waiting", "amount"],
        unpaid:      ["doc", "client", "type", "date", "waiting", "amount"],
        ignored:     ["doc", "client", "type", "date", "muted", "amount"],
    };

    function actionsHtml(cat, r) {
        const d = `data-kind="${esc(r.kind)}" data-no="${esc(r.doc_no)}"`;
        let acts = "";
        if (cat === "decide") {
            acts = `<button type="button" class="todo-act is-won" data-act="won" ${d} title="Mark ${esc(r.doc_no)} as Won">Won</button>`
                 + `<button type="button" class="todo-act is-lost" data-act="lost" ${d} title="Mark ${esc(r.doc_no)} as Lost">Lost</button>`;
        } else if (cat === "uncosted") {
            acts = `<button type="button" class="todo-act is-main" data-act="cost" ${d} title="Open the document and fill in its cost column">Add costs</button>`;
        } else if (cat === "unpaid") {
            acts = `<button type="button" class="todo-act is-main" data-act="paid" ${d} title="Mark this invoice as paid">Mark paid</button>`;
        } else if (cat === "unsubmitted") {
            acts = `<button type="button" class="todo-act is-main" data-act="submit" ${d} title="Mark as submitted">Mark submitted</button>`;
        } else if (cat === "deliver") {
            acts = `<button type="button" class="todo-act is-main" data-act="open" ${d} title="Open this PO in its editor">Open PO</button>`;
        } else if (cat === "billing") {
            // A rental or supply contract with a month nobody has invoiced. The
            // app does not write the invoice - Waleed chose the reminder over a
            // generated draft - so the only action is "go and look at it". The
            // client rides along: a PO number on its own does not identify one.
            acts = `<button type="button" class="todo-act is-main" data-act="open" ${d} data-client="${esc(r.client || "")}" title="Open ${esc(r.doc_no)}">Open LPO</button>`;
        } else if (cat === "ignored") {
            acts = `<button type="button" class="todo-act" data-act="restore" data-cat="${esc(r.category || "")}" ${d} title="Restore to Needs attention">${ICON_RESTORE}Restore</button>`;
        }
        // Phase X.30 - every live row gets a quiet Ignore (mute this concern).
        if (cat !== "ignored") {
            acts += `<button type="button" class="todo-act is-ignore" data-act="ignore" data-cat="${esc(cat)}" ${d} title="Ignore - ${esc(r.doc_no)} doesn't need attention here" aria-label="Ignore ${esc(r.doc_no)}">${ICON_EYEOFF}</button>`;
        }
        return acts;
    }

    function sorted(cat, list) {
        const s = _sort[cat];
        const col = s && COL[s.key];
        if (!col) return list.slice();
        return list.map((r, i) => [r, i]).sort((A, B) => {
            const a = col.value(A[0]), b = col.value(B[0]);
            const blankA = a == null || a === "", blankB = b == null || b === "";
            if (blankA || blankB) return (blankA - blankB) || (A[1] - B[1]);   // blanks last, both ways
            const cmp = (typeof a === "number" && typeof b === "number")
                ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
            return (cmp * s.dir) || (A[1] - B[1]);
        }).map((p) => p[0]);
    }

    function filtered(list) {
        const q = _query.trim().toLowerCase();
        if (!q) return list;
        return list.filter((r) => String(r.doc_no || "").toLowerCase().includes(q)
                               || String(r.client || "").toLowerCase().includes(q));
    }

    /* The line beside a section's heading - plain words, like Insights' notes. */
    function summaryText(cat, list) {
        const n = list.length;
        const noun = NOUN[cat] || ["document", "documents"];
        const sum = (k) => list.reduce((s, r) => s + (Number(r[k]) || 0), 0);
        const parts = [`${n} ${n === 1 ? noun[0] : noun[1]}`];
        const usd = sum("amount_usd");
        if (cat === "unpaid" && usd) parts.push(`${fmtMoney(usd, "USD")} owed`);
        if (cat === "decide" && usd) parts.push(`${fmtMoney(usd, "USD")} quoted`);
        if (cat === "uncosted" && usd) parts.push(`${fmtMoney(usd, "USD")} with no costs`);
        if (cat === "deliver") parts.push(`${sum("open_lines")} lines to invoice`);
        if (cat === "billing") parts.push(`${sum("months_due")} months not invoiced`);
        if (cat === "decide" && n) {
            const next = Math.min(...list.map((r) => Number(r.days_left) || 0));
            parts.push(next <= 0 ? "one is auto-lost today" : `next auto-loss in ${days(next)}`);
        } else if (["deliver", "unsubmitted", "uncosted", "unpaid"].includes(cat) && n) {
            const oldest = Math.max(0, ...list.map((r) => Number(r.age_days) || 0));
            if (oldest) parts.push(`oldest waiting ${days(oldest)}`);
        }
        return parts.join(" · ");
    }

    // The dashboard's per-type icons; billing has none there, so it gets a calendar.
    const ICON_CAL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
    const secIcon = (cat) => (cat === "billing" ? ICON_CAL : CASE_ICON(cat));
    let _kpiIndex = 0;        // staggers the tiles' fade-in

    /* The Insights KPI tile for one section: count, and the figure that
       matters most for it. A click jumps to its table. */
    function kpiHtml(sec, list) {
        const n = list.length;
        const usd = list.reduce((s, r) => s + (Number(r.amount_usd) || 0), 0);
        const sum = (k) => list.reduce((s, r) => s + (Number(r[k]) || 0), 0);
        const sub = sec.cat === "unpaid" ? `${fmtMoney(usd, "USD")} owed`
            : sec.cat === "decide" ? `${fmtMoney(usd, "USD")} quoted`
            : sec.cat === "deliver" ? `${sum("open_lines")} lines to invoice`
            : sec.cat === "billing" ? `${sum("months_due")} months not invoiced`
            : sec.cat === "uncosted" ? `${fmtMoney(usd, "USD")} with no costs`
            : `${n === 1 ? "document" : "documents"} not sent`;
        // The one number worth a second look: how many have waited too long
        // (bids: how many are lost within two days). Words, never colour alone.
        // A contract's age is its LPO date, not a wait - it is late by months due.
        const late = sec.cat === "decide"
            ? list.filter((r) => (Number(r.days_left) || 0) <= 2).length
            : sec.cat === "billing"
                ? list.filter((r) => (Number(r.months_due) || 0) >= 3).length
                : list.filter((r) => Number(r.age_days) >= LATE_DAYS).length;
        const flag = !late ? ""
            : sec.cat === "decide" ? `${late} lost within 2 days`
            : sec.cat === "billing" ? `${late} with 3+ months due`
            : `${late} waiting ${LATE_DAYS}+ days`;
        return `<button type="button" class="in-kpi todo-kpi" data-jump="${sec.cat}" style="--i:${_kpiIndex++}" title="Go to ${esc(sec.label)}">`
            + `<div class="todo-kpi-top"><span class="todo-ico">${secIcon(sec.cat)}</span>`
            + `<span class="in-kpi-label">${esc(CAT_WORD[sec.cat] || sec.label)}</span></div>`
            + `<div class="in-kpi-value">${n}</div>`
            + `<div class="in-kpi-sub">${esc(sub)}</div>`
            + (flag ? `<div class="todo-kpi-flag">${esc(flag)}</div>` : "")
            + `</button>`;
    }

    function headHtml(cat) {
        const s = _sort[cat];
        return COLS_FOR[cat].map((k) => {
            const c = COL[k];
            const on = s && s.key === k;
            return `<th data-sort="${k}"${c.left ? ' style="text-align:left"' : ""}`
                + ` aria-sort="${on ? (s.dir < 0 ? "descending" : "ascending") : "none"}"`
                + ` title="Sort by ${esc(c.label.toLowerCase())}">${esc(c.label)}${
                    on ? `<span class="in-sortmark">${s.dir < 0 ? "▼" : "▲"}</span>` : ""}</th>`;
        }).join("") + `<th class="static todo-acts-h"><span class="sr-only">Actions</span></th>`;
    }

    function rowsHtml(cat, list) {
        const cols = COLS_FOR[cat];
        const rows = sorted(cat, filtered(list));
        if (!rows.length) {
            return `<tr class="todo-nomatch"><td colspan="${cols.length + 1}">Nothing matches "${esc(_query.trim())}".</td></tr>`;
        }
        return rows.map((r) =>
            `<tr class="in-link todo-row" tabindex="0" data-cat="${esc(cat)}" data-origcat="${esc(cat === "ignored" ? (r.category || "") : cat)}"`
            + ` data-kind="${esc(r.kind)}" data-no="${esc(r.doc_no)}" data-client="${esc(r.client || "")}" data-date="${esc(r.date || "")}" title="Preview ${esc(r.doc_no)}">`
            + cols.map((k) => {
                const c = COL[k];
                const cls = c.late && c.late(r) ? ' class="is-neg todo-late"' : "";
                return `<td${cls}${c.left ? ' style="text-align:left"' : ""}>${c.cell(r)}</td>`;
            }).join("")
            + `<td class="todo-acts">${actionsHtml(cat, r)}</td></tr>`
        ).join("");
    }

    function lists() {
        const d = _data || {};
        return {
            decide: d.decide || [], billing: d.billing || [], deliver: d.deliver || [],
            unsubmitted: d.unsubmitted || [], uncosted: d.uncosted || [], unpaid: d.unpaid || [],
            ignored: d.ignored || [],
        };
    }

    function sectionHtml(sec, list) {
        const isIgnored = sec.cat === "ignored";
        const open = !isIgnored || _ignoredOpen;
        const toggle = isIgnored
            ? `<button type="button" class="in-back todo-toggle" aria-expanded="${open}" aria-controls="todoCard_ignored">${open ? "Hide" : `Show ${list.length}`}</button>`
            : "";
        return `<section class="todo-sec" id="todoSec_${sec.cat}" data-sec="${sec.cat}">`
            + `<div class="in-sect"><span class="todo-ico">${secIcon(sec.cat)}</span><h3>${esc(sec.label)}</h3><span>${esc(summaryText(sec.cat, list))}</span>${toggle}</div>`
            + `<p class="todo-hint">${esc(sec.hint)}</p>`
            + `<div class="in-card todo-card" id="todoCard_${sec.cat}"${open ? "" : " hidden"}><div class="in-scroll">`
            +   `<table class="in-table"><thead><tr>${headHtml(sec.cat)}</tr></thead>`
            +   `<tbody id="todoRows_${sec.cat}">${open ? rowsHtml(sec.cat, list) : ""}</tbody></table>`
            + `</div></div></section>`;
    }

    function render() {
        const body = $("todoBody");
        if (!body) return;
        const L = lists();
        const decide = L.decide, unc = L.uncosted, unsub = L.unsubmitted, unpaid = L.unpaid,
              deliver = L.deliver, billing = L.billing, ignored = L.ignored;
        const total = decide.length + unc.length + unsub.length + unpaid.length
                    + deliver.length + billing.length;
        // Only types with something in them get a section; an empty one is
        // a heading and a blank card that says nothing.
        const work = SECTIONS.filter((s) => s.cat !== "ignored" && L[s.cat].length);
        const top = total
            ? `<div class="in-kpis todo-kpis">${work.map((s) => kpiHtml(s, L[s.cat])).join("")}</div>`
              + `<div class="todo-filter"><input type="search" class="todo-search" id="todoSearch"`
              + ` placeholder="Filter by number or client" aria-label="Filter by number or client" value="${esc(_query)}" autocomplete="off"></div>`
            : allClearHtml();
        _kpiIndex = 0;
        const enter = _enter;
        _enter = false;
        body.innerHTML = `<div class="todo-page${enter ? " is-entering" : ""}">`
            + top
            + work.map((s) => sectionHtml(s, L[s.cat])).join("")
            + (ignored.length ? sectionHtml(SECTIONS.find((s) => s.cat === "ignored"), ignored) : "")
            + `</div>`;
        wire(body);
    }

    function allClearHtml() {
        return `<div class="todo-allclear">`
            + `<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`
            + `<strong>All caught up.</strong>`
            + `<span>${_scope === "month" ? "Nothing outstanding in the month you picked." : "Nothing outstanding anywhere - every document is costed, submitted, invoiced and paid."}</span>`
            + `</div>`;
    }

    /* Redraw one table's rows (a sort) or every table's (the filter), so the
       filter box keeps its focus and nothing else on the page moves. */
    function drawRows(only) {
        const L = lists();
        SECTIONS.forEach((s) => {
            if (only && s.cat !== only) return;
            const tbody = $(`todoRows_${s.cat}`);
            if (!tbody) return;
            if (s.cat === "ignored" && !_ignoredOpen) { tbody.innerHTML = ""; return; }
            tbody.innerHTML = rowsHtml(s.cat, L[s.cat]);
            wireRows(tbody);
        });
    }

    // ------------------------------------------------------------ actions
    // One shared performer for the persisting actions (used by the row buttons
    // AND the preview modal's top-right buttons). Returns true on success.
    async function performAction(act, d) {
        try {
            let ok = false;
            if (act === "won" || act === "lost") {
                const r = await api().set_bid_status(d.no, act);
                ok = r && r.ok;
                if (ok) window.toast && window.toast(`Marked ${d.no} as ${act === "won" ? "Won" : "Lost"}`);
            } else if (act === "submit") {
                const r = await api().set_submitted(d.kind, d.no, true);
                ok = r && r.ok;
                if (ok) window.toast && window.toast(`Marked ${d.no} as submitted`);
            } else if (act === "paid") {
                const r = await api().set_invoice_paid(d.no, true);
                ok = r && r.ok;
                if (ok) {
                    window.toast && window.toast(`Marked ${d.no} as paid`);
                    // 2026-08-27 - same prompt as the panel's Paid button: the
                    // client's stamped copy arrives with the payment.
                    if (window.SignedCopy) await window.SignedCopy.promptAfterPaid("inv", d.no);
                }
            } else if (act === "ignore" || act === "restore") {
                // Phase X.30 - mute/unmute this doc for ONE category.
                const r = await api().set_attention_ignore(d.kind, d.no, d.cat, act === "ignore");
                ok = r && r.ok;
                if (ok) window.toast && window.toast(act === "ignore"
                    ? `Ignored ${d.no} - it won't ask for attention (${d.cat})`
                    : `Restored ${d.no} to Needs attention`);
            }
            if (!ok) window.toast && window.toast("Couldn't update", true);
            return ok;
        } catch (err) {
            window.toast && window.toast("Action failed: " + (err && err.message || err), true);
            return false;
        }
    }

    function openSubmittedFulfillment(act, d) {
        if (act !== "submit" || !window.openSubmittedFulfillment) return;
        window.openSubmittedFulfillment(d.kind, d.no);
    }

    // ------------------------------------------------------ preview modal
    // Phase X.29 - read-only document preview: a well-structured items table
    // (scrollable when long) with the section's action(s) top-right.
    const HEAD_FALLBACK = {
        item: "ITEM", equipment: "EQUIPMENT", role: "ROLE", description: "DESCRIPTION",
        unit: "UNIT", qty: "QTY", rate: "RATE", total: "TOTAL", image: "IMAGE",
    };
    // Internal / costing-only keys that must never show as table columns.
    const SKIP_KEYS = new Set(["cost", "cost_qty", "cost_sell", "c_qty", "c_sell", "c_auto", "_index"]);

    const num = (v) => {
        const n = parseFloat(String(v == null ? "" : v).replace(/,/g, ""));
        return isFinite(n) ? n : 0;
    };
    const money = (n) => "$" + Number(n).toLocaleString(undefined,
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    async function fetchDocRecord(kind, no) {
        try {
            if (kind === "quote") {
                const r = await api().load_quote(no);
                return r && { rows: r.rows || [], cols: r.columns,
                              client: r.client_name || "", date: r.quote_date || "" };
            }
            if (kind === "dn") {
                const r = await api().load_dn(no);
                return r && { rows: r.rows || [], cols: r.columns,
                              client: r.client_name || "", date: r.dn_date || "",
                              dnPrices: !!r.include_prices };
            }
            if (kind === "po") {
                const r = await api().load_po(no);
                return r && { rows: r.rows || [],
                              client: r.source_quote_no ? `Quote ${r.source_quote_no}` : "",
                              date: r.po_date || "" };
            }
            const r = await api().load_invoice(no);
            return r && { rows: r.rows || [], cols: r.columns,
                          client: r.client_name || "", date: r.inv_date || "" };
        } catch (e) { return null; }
    }

    // Column list for the preview table: the doc's own saved columns when
    // present (Phase T), else derived from the row keys (pre-Phase-T docs).
    function previewCols(rec) {
        let cols = Array.isArray(rec.cols) && rec.cols.length
            ? rec.cols.map((c) => ({ ...c })) : null;
        if (!cols) {
            const seen = [];
            const seenSet = new Set();
            rec.rows.forEach((r) => Object.keys(r || {}).forEach((k) => {
                if (seenSet.has(k) || SKIP_KEYS.has(k)) return;
                // only show keys that actually hold content somewhere
                if (!rec.rows.some((x) => x && x[k] != null && String(x[k]).trim() !== "")) return;
                seenSet.add(k); seen.push(k);
            }));
            const PREF = ["item", "equipment", "role", "description", "unit", "qty", "rate", "total"];
            const ordered = PREF.filter((k) => seenSet.has(k))
                .concat(seen.filter((k) => !PREF.includes(k) && k !== "image"));
            if (seenSet.has("image")) ordered.push("image");
            cols = ordered.map((k) => ({ key: k, head: HEAD_FALLBACK[k] || k.replace(/_/g, " ").toUpperCase() }));
        } else {
            cols = cols.filter((c) => c && c.key && !SKIP_KEYS.has(c.key));
        }
        // DN stores base columns only; Rate/Total are reconstructed from the flag.
        if (rec.dnPrices && !cols.some((c) => c.key === "rate")) {
            cols.push({ key: "rate", head: "RATE", kind: "money" },
                      { key: "total", head: "TOTAL", kind: "computed" });
        }
        // Ensure every column has a display kind.
        cols.forEach((c) => {
            if (!c.kind) c.kind = c.key === "image" ? "image"
                : c.key === "total" ? "computed"
                : c.key === "rate" ? "money" : "text";
            if (!c.head) c.head = HEAD_FALLBACK[c.key] || String(c.key).toUpperCase();
        });
        return cols;
    }

    function previewCellHtml(col, r) {
        const v = r ? r[col.key] : "";
        if (col.kind === "image" || (typeof v === "string" && v.startsWith("data:image"))) {
            return v ? `<img class="todo-md-img" src="${esc(v)}" alt="">` : "";
        }
        if (col.kind === "computed") {
            const q = num(r && r.qty), rate = num(r && r.rate);
            return (q && rate) ? money(q * rate) : "";
        }
        if (col.kind === "money") {
            return (v == null || String(v).trim() === "") ? "" : money(num(v));
        }
        return esc(String(v == null ? "" : v)).replace(/\n/g, "<br>");
    }

    /* The preview's actions, as DATA (2026-08-26). They used to be a string of
       buttons wedged into the popup's own header; they are now the shared
       footer row every popup in the app has ([[14-UI-CONTRACTS]]), which is why
       this returns a list rather than markup. Same buttons, same order, same
       words - only the arrangement is now the one everything else uses.

       The popup is a read-only PREVIEW, so "go look at the real document" is a
       reasonable next step from every queue, but it is never the primary act:
       it stays a ghost button beside the filled category action. */
    const GOTO_ACT = { label: "Open document", value: "open", cls: "ghost",
                       title: "Open this document in its editor" };
    const IGNORE_ACT = { label: "Ignore", value: "ignore", cls: "ghost",
                         title: "Ignore - this doesn't need attention here" };

    function modalActions(cat) {
        if (cat === "search") {
            // Dashboard search preview - opening the document IS the action,
            // so it is the primary one and no attention actions apply.
            return [{ label: "Go to document", value: "open", cls: "primary",
                      title: "Open this document in its editor" }];
        }
        if (cat === "insights" || cat === "insights-unpaid") {
            // Insights (2026-10-06) - a preview to read and close, landing back
            // on the sorted table. Opening the document is the way on; an unpaid
            // invoice can also be marked paid here, which is what the Invoices
            // table's "Unpaid first" sort is for. No Ignore: muting a To-Do
            // reminder means nothing on this screen.
            if (cat === "insights") {
                return [{ label: "Open document", value: "open", cls: "primary",
                          title: "Open this document in its editor" }];
            }
            return [GOTO_ACT, { label: "Mark paid", value: "paid", cls: "primary",
                                title: "Mark this invoice as paid" }];
        }
        if (cat === "ignored") {
            // Restore is the only state change that makes sense on a muted
            // entry, but you should still be able to go look at the document.
            return [GOTO_ACT, { label: "Restore", value: "restore", cls: "primary",
                                title: "Restore to Needs attention" }];
        }
        if (cat === "billing") {
            // A contract month that has not been invoiced. Nothing to change
            // here - the work is writing the invoice - so the action is to go
            // and look at the LPO.
            return [IGNORE_ACT, { label: "Open LPO", value: "open", cls: "primary",
                                  title: "Open Client LPOs" }];
        }
        if (cat === "deliver") {
            // Client PO awaiting invoice - its primary action is already "open
            // the PO", so a second Open-document button would just be noise.
            return [IGNORE_ACT, { label: "Open PO", value: "open", cls: "primary",
                                  title: "Open this PO in its editor" }];
        }
        let primary;
        if (cat === "decide") {
            primary = [{ label: "Lost", value: "lost", cls: "secondary", title: "Mark Lost" },
                       { label: "Won", value: "won", cls: "primary", title: "Mark Won" }];
        } else if (cat === "uncosted") {
            primary = [{ label: "Add costs", value: "cost", cls: "primary",
                         title: "Open the document and fill in its cost column" }];
        } else if (cat === "unpaid") {
            primary = [{ label: "Mark paid", value: "paid", cls: "primary",
                         title: "Mark this invoice as paid" }];
        } else {
            primary = [{ label: "Mark submitted", value: "submit", cls: "primary",
                         title: "Mark as submitted" }];
        }
        // Ghosts first, the decision last - the shared footer puts the primary
        // at the end (rule 6).
        return [IGNORE_ACT, GOTO_ACT].concat(primary);
    }

    /* The preview is a shared Popup since 2026-08-26 - Esc, the dim
       background, the X, the focus trap and the scroll lock all come with the
       box ([[14-UI-CONTRACTS]]), so none of them live here any more. */
    let _modal = null;
    function closeDocModal() {
        if (_modal) { _modal.close(null); _modal = null; }
    }

    /* The LPO preview, opened from a "Bill this month" row (2026-09-12).

       Every other To-Do row opens a preview before you act, and this one now
       does too. It shows the client's actual PO - the first page, rendered as
       an image so it appears immediately - and offers the three things worth
       doing with it: go to the record, or start the invoice or delivery note
       that clears the reminder.

       The page comes from `lpo_page_image`, which is about a fifth of the
       traffic of handing the whole PDF to pdf.js and is cached on disk after
       the first render. If no rasteriser is installed it reports
       `no_rasteriser` and this falls back to the record's details alone - the
       actions are the point, the picture is confirmation. */
    async function openLpoTodoModal(lpoNo, clientName) {
        closeDocModal();
        const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
            (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

        let rec = null;
        try { rec = await api().get_lpo(clientName, lpoNo); } catch (e) { rec = null; }
        if (!rec) { if (window.toast) window.toast(`Could not load ${lpoNo}`, true); return; }

        const money = (v) => {
            const cur = String(rec.currency || "USD").toUpperCase();
            if (cur === "IQD") return "IQD " + Math.round(Number(v) || 0).toLocaleString("en-US");
            return "$" + (Number(v) || 0).toLocaleString("en-US",
                { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        };
        const kind = String(rec.kind || "materials").toLowerCase();
        const KINDS = { materials: "Materials", rental: "Rental", open: "Supply contract" };
        const due = Number(rec.months_unbilled) || 0;

        const facts = [
            ["Received", rec.lpo_date || ""],
            kind === "open" ? null : ["Value", money(rec.total)],
            kind === "rental" && rec.monthly_amount ? ["Per month", money(rec.monthly_amount)] : null,
            kind !== "materials" && rec.period_start
                ? ["Period", rec.period_start + (rec.period_end ? " to " + rec.period_end : " onwards")]
                : null,
            rec.source_quote_no ? ["Against", rec.source_quote_no] : null,
        ].filter(Boolean);

        const body = `<div class="todo-lpo">
            <div class="todo-lpo-doc">
                <div class="todo-lpo-pager" id="todoLpoPager" hidden>
                    <button type="button" class="todo-lpo-pbtn" data-step="-1"
                            title="Previous page" aria-label="Previous page">&#8249;</button>
                    <span class="todo-lpo-plbl" id="todoLpoPageLbl">1 / 1</span>
                    <button type="button" class="todo-lpo-pbtn" data-step="1"
                            title="Next page" aria-label="Next page">&#8250;</button>
                </div>
                <div class="todo-lpo-page" id="todoLpoPage">
                    ${rec.file_exists ? `<div class="todo-lpo-loading">Loading the page&#8230;</div>`
                        : `<div class="todo-lpo-nofile">No document attached to this LPO yet.</div>`}
                </div>
            </div>
            <div class="todo-lpo-side">
                <div class="todo-lpo-no">${esc(lpoNo)}</div>
                <div class="todo-lpo-cli">${esc(clientName)} &middot; ${esc(KINDS[kind] || "Materials")}</div>
                ${due ? `<p class="todo-lpo-due">${due} month${due === 1 ? "" : "s"} not invoiced,
                    oldest ${esc(rec.oldest_due_month || "")}.</p>` : ""}
                <dl class="todo-lpo-dl">${facts.map(([k, v]) =>
                    `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("")}</dl>
            </div>
        </div>`;

        const handle = window.Popup.open({
            title: "Bill this month",
            subtitle: `${lpoNo} - ${clientName}`,
            // xl plus `.todo-lpo-popup`, which widens it further: this dialog
            // exists to READ a scanned A4 page, and at 680px it could not be
            // (Waleed, 2026-09-12).
            html: true, body, size: "xl", className: "todo-lpo-popup",
            actions: [
                { label: "Close", value: null, cls: "ghost" },
                { label: "Open LPO", value: "open", cls: "ghost" },
                { label: "Make delivery note", value: "dn", cls: "secondary" },
                { label: "Make invoice", value: "inv", cls: "primary", primary: true },
            ],
        });

        // Fetched after the popup is up, so the dialog appears immediately and
        // the page fills in - rather than the click feeling like it did nothing.
        if (rec.file_exists) loadLpoModalPages(clientName, lpoNo, rec.ack_exists ? "ack" : "file");

        const chosen = await handle.result;
        if (chosen === "open") return openDocEditor("lpo", lpoNo, clientName);
        if (chosen === "inv" && window.createInvoiceFromLpo) {
            return window.createInvoiceFromLpo(clientName, lpoNo);
        }
        if (chosen === "dn" && window.createDnFromLpo) {
            return window.createDnFromLpo(clientName, lpoNo);
        }
    }

    /* Every page of the PO, scrollable, with buttons to step between them
       (2026-09-12 - "no pages navigation buttons in the todos popup").

       Page 1 is fetched on its own and shown as soon as it lands; the rest
       follow in the background and append in order, so a three-page contract
       does not hold the dialog blank while it renders. Each page is a separate
       cached JPEG on the backend, which is why this can be lazy at all.

       165 dpi rather than the 130 default: the dialog is nearly the height of
       the window, and A4 at 130 dpi is 1587px tall - less than the box on a
       tall screen, so it would be upscaled. */
    async function loadLpoModalPages(clientName, lpoNo, which) {
        const box = () => document.getElementById("todoLpoPage");
        const fail = () => {
            const b = box();
            if (b) b.innerHTML = `<div class="todo-lpo-nofile">Open the LPO to see the document.</div>`;
        };
        let first = null;
        try { first = await api().lpo_page_image(clientName, lpoNo, 0, which, 165); }
        catch (e) { first = null; }
        const b = box();
        if (!b) return;                                 // popup already closed
        if (!first || !first.ok) { fail(); return; }

        const total = Number(first.pages) || 1;
        const shot = (uri, n) =>
            `<figure class="todo-lpo-sheet" data-page="${n}">
                <img src="${uri}" alt="Page ${n} of ${esc(lpoNo)}">
                ${total > 1 ? `<figcaption>Page ${n} of ${total}</figcaption>` : ""}
             </figure>`;
        b.innerHTML = shot(first.data_uri, 1);
        b.scrollTop = 0;

        const pager = document.getElementById("todoLpoPager");
        const lbl = document.getElementById("todoLpoPageLbl");
        if (total < 2 || !pager) return;
        pager.hidden = false;
        const say = (n) => { if (lbl) lbl.textContent = `${n} / ${total}`; };
        say(1);

        // The page covering the middle of the box, not the top edge - which
        // would read as page 2 while most of page 1 is still on screen.
        b.onscroll = () => {
            const mid = b.scrollTop + b.clientHeight / 2;
            let seen = 1;
            b.querySelectorAll(".todo-lpo-sheet").forEach((f) => {
                if (f.offsetTop <= mid) seen = Number(f.dataset.page) || seen;
            });
            say(seen);
        };
        pager.querySelectorAll("[data-step]").forEach((btn) => {
            btn.onclick = () => {
                const now = Number((lbl && lbl.textContent || "1").split("/")[0].trim()) || 1;
                const want = Math.max(1, Math.min(total, now + Number(btn.dataset.step)));
                const f = b.querySelector(`.todo-lpo-sheet[data-page="${want}"]`);
                if (f) { b.scrollTo({ top: Math.max(0, f.offsetTop - 6), behavior: "smooth" }); say(want); }
            };
        });

        for (let n = 2; n <= total; n++) {
            let img = null;
            try { img = await api().lpo_page_image(clientName, lpoNo, n - 1, which, 165); }
            catch (e) { img = null; }
            const live = box();
            if (!live) return;                          // closed mid-load
            if (img && img.ok) live.insertAdjacentHTML("beforeend", shot(img.data_uri, n));
        }
    }

    async function openDocModal(cat, kind, no, docDate, origCat, opts = {}) {
        origCat = origCat || cat;          // ignored rows persist against their ORIGINAL category
        closeDocModal();

        /* Every action keeps the popup open and closes it itself: a state
           change that FAILS must leave the preview up with its buttons live,
           which is what `keep` is for. */
        const runAct = async (act, h) => {
            if (act === "cost") {               // navigate - nothing to persist
                h.close(null);
                openInCosting(kind, no);
                return;
            }
            if (act === "open") {               // navigate to the doc's editor
                h.close(null);
                // The dashboard search passes its own navigation (onOpen);
                // everything else opens the doc in its own editor.
                if (opts && typeof opts.onOpen === "function") { opts.onOpen(); return; }
                openDocEditor(kind, no);
                return;
            }
            const btns = Array.from(h.panel.querySelectorAll(".popup-actions button"));
            btns.forEach((b) => { b.disabled = true; });
            const ok = await performAction(act, { kind, no, cat: origCat });
            if (!ok) { btns.forEach((b) => { b.disabled = false; }); return; }
            h.close(act);
            if (opts && typeof opts.onAfterAction === "function") await opts.onAfterAction();
            else await load();
            openSubmittedFulfillment(act, { kind, no });
        };

        const h = window.Popup.open({
            title: no,
            badge: KIND_BADGE[kind] || kind,
            icon: DOC_ICON(kind),
            subtitle: "Loading…",
            size: "xl",
            // The shell is RTL (Arabic-first); an English items table mirrors
            // unless the panel says otherwise.
            dir: "ltr",
            className: "todo-md-popup",
            body: "Loading document…",
            actions: modalActions(cat).map((a) => ({
                ...a, keep: true, onClick: (handle) => runAct(a.value, handle),
            })),
        });
        _modal = h;
        h.result.then(() => { if (_modal === h) _modal = null; });
        h.panel.querySelectorAll(".popup-actions button").forEach((b, i) => {
            const a = modalActions(cat)[i];
            if (a && a.title) b.title = a.title;
        });

        // Load + render the read-only items table.
        const rec = await fetchDocRecord(kind, no);
        if (h.closed) return;                      // dismissed while loading
        const bodyEl = h.body;
        if (!rec) {
            h.setSubtitle("-");
            bodyEl.innerHTML = `<div class="todo-md-loading">Couldn't load this document.</div>`;
            return;
        }
        h.setSubtitle(
            `${rec.client || "-"}${rec.date ? " · " + String(rec.date).slice(0, 10) : ""}`);

        const cols = previewCols(rec);
        const rows = rec.rows.filter((r) => r && cols.some((c) =>
            c.kind !== "computed" && r[c.key] != null && String(r[c.key]).trim() !== ""));
        const NUMISH = new Set(["qty", "rate", "total"]);
        const th = cols.map((c) =>
            `<th class="${NUMISH.has(c.key) || c.kind === "money" || c.kind === "computed" ? "num" : ""}">${esc(c.head)}</th>`).join("");
        const trs = rows.map((r, i) =>
            `<tr><td class="tn">${i + 1}</td>` + cols.map((c) =>
                `<td class="${NUMISH.has(c.key) || c.kind === "money" || c.kind === "computed" ? "num" : ""}">${previewCellHtml(c, r)}</td>`).join("")
            + `</tr>`).join("");

        // Grand total (Σ qty·rate) - shown only when the doc actually has rates.
        let grand = 0;
        if (cols.some((c) => c.key === "rate")) {
            rows.forEach((r) => { grand += num(r.qty) * num(r.rate); });
        }
        bodyEl.innerHTML =
            `<div class="todo-md-tablewrap">`
            + `<table class="todo-md-table"><thead><tr><th class="tn">#</th>${th}</tr></thead>`
            + `<tbody>${trs || `<tr><td colspan="${cols.length + 1}" class="todo-md-loading">No items.</td></tr>`}</tbody></table>`
            + `</div>`
            + `<div class="todo-md-foot"><span>${rows.length} item${rows.length === 1 ? "" : "s"}</span>`
            + (grand > 0 ? `<span class="todo-md-grand">Total ${money(grand)}</span>` : `<span></span>`)
            + `</div>`;
    }

    // ------------------------------------------------------------------ wire
    function wire(body) {
        // A KPI tile jumps to its table.
        body.querySelectorAll(".todo-kpi[data-jump]").forEach((k) => {
            k.onclick = () => {
                const sec = $(`todoSec_${k.dataset.jump}`);
                if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
            };
        });
        // Sortable headings, as on Insights: the same column reverses; a new
        // one starts at its natural direction (`first`: 1 = A to Z / soonest,
        // otherwise biggest or newest first). Only that table redraws.
        body.querySelectorAll(".todo-sec").forEach((sec) => {
            const cat = sec.dataset.sec;
            sec.querySelectorAll("th[data-sort]").forEach((th) => {
                th.onclick = () => {
                    const k = th.dataset.sort;
                    const col = COL[k] || {};
                    const cur = _sort[cat];
                    _sort[cat] = (cur && cur.key === k) ? { key: k, dir: -cur.dir } : { key: k, dir: col.first || -1 };
                    const head = th.closest("tr");
                    if (head) head.innerHTML = headHtml(cat);
                    drawRows(cat);
                    wire(body);
                };
            });
        });
        // Ignored opens and closes in place.
        const toggle = body.querySelector(".todo-toggle");
        if (toggle) {
            toggle.onclick = () => {
                _ignoredOpen = !_ignoredOpen;
                const card = $("todoCard_ignored");
                if (card) card.hidden = !_ignoredOpen;
                toggle.setAttribute("aria-expanded", String(_ignoredOpen));
                toggle.textContent = _ignoredOpen ? "Hide" : `Show ${lists().ignored.length}`;
                drawRows("ignored");
            };
        }
        const search = $("todoSearch");
        if (search) {
            search.oninput = () => { _query = search.value; drawRows(); };
            search.onkeydown = (e) => {
                if (e.key === "Escape" && search.value) { e.stopPropagation(); search.value = ""; _query = ""; drawRows(); }
            };
        }
        body.querySelectorAll("tbody[id^='todoRows_']").forEach(wireRows);
    }

    function wireRows(tbody) {
        // Whole-row click → read-only preview modal with the section's actions.
        tbody.querySelectorAll(".todo-row").forEach((el) => {
            const open = () => {
                // A `billing` row names a client's LPO, not one of our
                // documents. The preview modal loads through load_quote /
                // load_dn / load_invoice, none of which can fetch an LPO - so it
                // gets its own preview instead of a modal that would come up empty.
                if (el.dataset.cat === "billing") {
                    openLpoTodoModal(el.dataset.no, el.dataset.client || "");
                    return;
                }
                openDocModal(el.dataset.cat, el.dataset.kind, el.dataset.no,
                             el.dataset.date, el.dataset.origcat);
            };
            el.onclick = open;
            el.onkeydown = (e) => {
                if (e.target !== el) return;          // a focused button handles its own keys
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
            };
        });
        // Per-row action buttons (no modal detour).
        tbody.querySelectorAll(".todo-act").forEach((b) => {
            b.onclick = async (e) => {
                e.stopPropagation();
                const act = b.dataset.act;
                if (act === "cost") {          // just navigate; nothing to persist
                    openInCosting(b.dataset.kind, b.dataset.no, b.dataset.date);
                    return;
                }
                if (act === "open") {          // open the doc's editor; nothing to persist
                    openDocEditor(b.dataset.kind, b.dataset.no, b.dataset.client);
                    return;
                }
                b.disabled = true;
                const ok = await performAction(act, {
                    kind: b.dataset.kind, no: b.dataset.no, cat: b.dataset.cat,
                });
                if (ok) {
                    await load();
                    openSubmittedFulfillment(act, {
                        kind: b.dataset.kind, no: b.dataset.no,
                    });
                }
                else b.disabled = false;
            };
        });
    }
    window.openTodoDocModal = openDocModal;
})();
