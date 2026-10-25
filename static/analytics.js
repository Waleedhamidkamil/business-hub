/* =========================================================
   Insights - rebuilt 2026-08-09 around MONEY PER CLIENT.

   What changed from the old view: it counted documents; this one answers
   "for this client, what did we charge, what did it cost us, what did we
   keep" - plus the same question for the whole company. Backed by the new
   `insights_overview` / `client_insights` bridge calls (backend/insights.py),
   NOT the document-counting `dashboard_analytics` (which still powers the
   dashboard's This-Month card).

   Design rules this file follows - change them only deliberately:
   · ONE filter row (month + span) scopes the entire page. No per-card filters.
   · Never a dual-axis chart. Money and margin-% are two separate charts.
   · A legend for >= 2 series; single-series charts direct-label instead.
   · Every chart has a table twin on the page (the leaderboard, the docs table,
     the items table) - no value is reachable only by hovering.
   · NO red/green pairs - the user is colour blind. Direction is an arrow
     glyph plus a word; a loss is red ONLY next to a literal "−".
   · Series colours come from CSS tokens (--in-s1..s5), so light/dark swap in
     one place and stay validated. See the palette note in app_styles.css.
   ========================================================= */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const api = () => window.pywebview.api;

    const SPANS = [1, 3, 6, 12];
    const state = {
        span: 12,
        year: null,          // window END month; null = "now"
        month: null,
        client: null,        // null = all clients
        overview: null,
        detail: null,
        loading: false,
    };
    const charts = {};       // host id -> ApexCharts instance

    // ------------------------------------------------------------ utils
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[m]));
    }
    function money(n) {
        const v = Number(n) || 0;
        const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        return (v < 0 ? "−$" : "$") + s;
    }
    function money2(n) {
        const v = Number(n) || 0;
        const s = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return (v < 0 ? "−$" : "$") + s;
    }
    function moneyShort(n) {
        const v = Number(n) || 0, a = Math.abs(v), sign = v < 0 ? "−$" : "$";
        if (a >= 1e6) return sign + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
        if (a >= 1e3) return sign + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k";
        return sign + Math.round(a);
    }
    function pctText(n) { return (n == null) ? "-" : (Math.round(n * 10) / 10) + "%"; }
    function num(n) { return (Number(n) || 0).toLocaleString(); }
    function dateShort(iso) {
        const s = String(iso || "").slice(0, 10);
        if (!s) return "-";
        const d = new Date(s + "T00:00:00");
        return isNaN(d) ? s : d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
    }
    /** A signed change, carried by an arrow AND a word - never colour alone. */
    function trendText(cur, prev, label) {
        if (prev == null || !prev) return `no ${label} to compare`;
        const p = Math.round(((cur - prev) / Math.abs(prev)) * 1000) / 10;
        if (!isFinite(p)) return `no ${label} to compare`;
        if (Math.abs(p) < 0.05) return `level vs ${label}`;
        return `${p > 0 ? "▲ up" : "▼ down"} ${Math.abs(p)}% vs ${label}`;
    }

    function tok(name, fallback) {
        const host = $("analyticsView") || document.documentElement;
        return getComputedStyle(host).getPropertyValue(name).trim() || fallback;
    }
    function theme() {
        const dark = document.documentElement.getAttribute("data-theme") === "dark";
        return {
            dark,
            s1: tok("--in-s1", "#2a78d6"),
            s2: tok("--in-s2", "#eb6834"),
            s3: tok("--in-s3", "#1baf7a"),
            s4: tok("--in-s4", "#eda100"),
            s5: tok("--in-s5", "#4a3aa7"),
            neg: tok("--in-neg", "#d03b3b"),
            ink: tok("--text", "#0F172A"),
            muted: tok("--text-muted", "#475569"),
            grid: tok("--border", "#E4E7EB"),
            surface: tok("--surface", "#ffffff"),
        };
    }
    const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function baseChart(T, type, height) {
        return {
            type, height, fontFamily: "inherit", foreColor: T.muted,
            background: "transparent", toolbar: { show: false }, parentHeightOffset: 0,
            animations: { enabled: !reduced(), speed: 500 },
        };
    }
    /* Hairline, SOLID grid - never dashed (dashing reads as "threshold"). */
    function gridOf(T) { return { borderColor: T.grid, strokeDashArray: 0, padding: { left: 4, right: 8 } }; }
    function axisOf(T) {
        return { axisBorder: { show: false }, axisTicks: { show: false }, labels: { style: { colors: T.muted, fontSize: "11px" } } };
    }
    function mount(id, opts) {
        const el = $(id);
        if (!el) return null;
        if (charts[id]) { try { charts[id].destroy(); } catch (e) { /* noop */ } delete charts[id]; }
        if (!window.ApexCharts) { el.innerHTML = `<div class="in-empty">Charts unavailable.</div>`; return null; }
        el.innerHTML = "";
        const c = new window.ApexCharts(el, opts);
        c.render();
        charts[id] = c;
        return c;
    }
    function destroyAll() {
        Object.keys(charts).forEach((id) => {
            try { charts[id].destroy(); } catch (e) { /* noop */ }
            delete charts[id];
        });
    }
    function empty(id, msg) {
        const el = $(id);
        if (el) el.innerHTML = `<div class="in-empty">${esc(msg)}</div>`;
    }

    // ------------------------------------------------------------ chrome
    function monthLabel(y, m) {
        if (!y || !m) return "-";
        return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
    }
    function atLatestMonth() {
        const now = new Date();
        return !state.year || (state.year === now.getFullYear() && state.month === now.getMonth() + 1);
    }
    function stepMonth(delta) {
        const now = new Date();
        let y = state.year || now.getFullYear();
        let m = state.month || (now.getMonth() + 1);
        m += delta;
        if (m > 12) { m = 1; y += 1; }
        if (m < 1) { m = 12; y -= 1; }
        if (y > now.getFullYear() || (y === now.getFullYear() && m > now.getMonth() + 1)) return;
        state.year = y; state.month = m;
        load();
    }
    function syncChrome() {
        const now = new Date();
        const y = state.year || now.getFullYear();
        const m = state.month || (now.getMonth() + 1);
        const lbl = $("inMonthLabel");
        if (lbl) lbl.textContent = monthLabel(y, m);
        const next = $("inMonthNext");
        if (next) next.disabled = atLatestMonth();
        const span = $("inSpan");
        if (span) span.querySelectorAll("button[data-span]").forEach((b) => {
            b.classList.toggle("active", parseInt(b.dataset.span, 10) === state.span);
        });
    }

    // ------------------------------------------------------------ entry
    async function showAnalytics() {
        document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
        ["poStrip", "poActions", "quoteActions", "dnActions", "invActions"]
            .forEach((id) => { const el = $(id); if (el) el.hidden = true; });
        document.body.classList.remove("on-dashboard", "has-rail");
        const rail = $("typeRail"); if (rail) rail.hidden = true;

        $("analyticsView").hidden = false;
        const home = $("btnHome"); if (home) home.hidden = false;
        if (window.setBrand) window.setBrand("Insights");
        if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
        await load();
    }
    window.showAnalytics = showAnalytics;

    async function load() {
        const body = $("inBody");
        if (state.loading) return;
        state.loading = true;
        // Hold the previous render at reduced opacity - no skeleton flash,
        // no layout jump.
        if (body) body.classList.add("is-loading");
        syncChrome();
        try {
            const ov = await api().insights_overview(state.span, state.year, state.month);
            state.overview = (ov && ov.ok) ? ov : null;
            if (state.overview) {
                state.year = state.overview.end_year;
                state.month = state.overview.end_month;
            }
            if (state.client && state.overview) {
                const d = await api().client_insights(state.client, state.span, state.year, state.month);
                state.detail = (d && d.ok) ? d : null;
            } else {
                state.detail = null;
            }
        } catch (e) {
            state.overview = null; state.detail = null;
        }
        state.loading = false;
        if (body) body.classList.remove("is-loading");
        syncChrome();
        render();
    }

    window.rerenderAnalytics = function () {
        const v = $("analyticsView");
        if (!v || v.hidden || !state.overview) return;
        render();
    };

    // ------------------------------------------------------------ render
    function render() {
        const body = $("inBody");
        const period = $("inPeriod");
        destroyAll();
        if (!state.overview) {
            if (period) period.textContent = "-";
            if (body) body.innerHTML = `<div class="in-empty">Couldn't load insights.</div>`;
            renderRail();
            return;
        }
        if (period) period.textContent = state.overview.period_label || "-";
        renderRail();
        if (state.client && state.detail) renderClientView();
        else renderAllView();
    }

    // ---- the client picker rail (cards) ----
    function renderRail() {
        const rail = $("inRail");
        if (!rail) return;
        const ov = state.overview;
        const hint = $("inScopeHint");
        if (!ov) { rail.innerHTML = ""; return; }
        const q = (($("inClientSearch") || {}).value || "").trim().toLowerCase();
        const all = ov.clients || [];
        const list = q ? all.filter((c) => c.name.toLowerCase().includes(q)) : all;
        const max = Math.max(1, ...all.map((c) => Math.abs(c.profit)));
        const T = ov.totals || {};

        const cards = [`
            <button type="button" class="in-ccard all${state.client ? "" : " active"}" data-client="">
                <div class="in-ccard-name">All clients</div>
                <div class="in-ccard-val">${moneyShort(T.profit)}</div>
                <div class="in-ccard-sub"><span>profit</span><span>${pctText(T.margin)} margin</span></div>
                <span class="in-ccard-track"><span class="in-ccard-fill" style="width:100%"></span></span>
            </button>`];
        list.forEach((c) => {
            const w = Math.max(3, Math.round((Math.abs(c.profit) / max) * 100));
            const neg = c.profit < 0;
            cards.push(`
            <button type="button" class="in-ccard${state.client && state.client.toLowerCase() === c.name.toLowerCase() ? " active" : ""}" data-client="${esc(c.name)}" title="${esc(c.name)}">
                <div class="in-ccard-name">${esc(c.name)}</div>
                <div class="in-ccard-val${neg ? " is-neg" : ""}">${moneyShort(c.profit)}</div>
                <div class="in-ccard-sub"><span>${c.invoices} inv · ${c.quotes} qut</span><span>${pctText(c.margin)}</span></div>
                <span class="in-ccard-track"><span class="in-ccard-fill${neg ? " is-neg" : ""}" style="width:${w}%"></span></span>
            </button>`);
        });
        rail.innerHTML = cards.join("");
        if (hint) {
            hint.textContent = list.length
                ? `${list.length} client${list.length === 1 ? "" : "s"} in ${state.overview.period_label}`
                : (q ? "no client matches that search" : "no client activity in this period");
        }
        rail.querySelectorAll(".in-ccard").forEach((b) => {
            b.onclick = () => pickClient(b.dataset.client || null);
        });
    }

    function pickClient(name) {
        state.client = name || null;
        const rail = $("inRail");
        if (rail) rail.scrollTop = 0;
        load();
        // Back to the top rather than scrollIntoView on the body: the latter
        // pushes the month/span filter row off-screen, and those still scope
        // whatever you just opened.
        const view = $("analyticsView");
        if (view) view.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
        window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
    }

    // ---- shared pieces -------------------------------------------------
    function kpi(label, value, sub, opts) {
        const o = opts || {};
        return `<div class="in-kpi${o.neg ? " is-neg" : ""}">
            <div class="in-kpi-label">${esc(label)}</div>
            <div class="in-kpi-value">${value}</div>
            <div class="in-kpi-sub">${sub || "&nbsp;"}</div>
            ${o.spark ? `<div class="in-kpi-spark" id="${o.spark}"></div>` : ""}
        </div>`;
    }
    function sect(title, note) {
        return `<div class="in-sect"><h3>${esc(title)}</h3>${note ? `<span>${esc(note)}</span>` : ""}</div>`;
    }
    function meter(value, caption, note, color, pct) {
        return `<div class="in-meter">
            <div class="in-meter-top"><span class="in-meter-val">${value}</span><span class="in-meter-cap">${esc(caption)}</span></div>
            <span class="in-meter-track"><span class="in-meter-fill" style="width:${Math.max(0, Math.min(100, pct))}%;background:${color}"></span></span>
            <div class="in-meter-note">${note}</div>
        </div>`;
    }
    function cellbar(value, max, neg) {
        const w = max > 0 ? Math.max(2, Math.round((Math.abs(value) / max) * 100)) : 0;
        return `<span class="in-cellbar">
            <span class="in-cellbar-track"><span class="in-cellbar-fill${neg ? " is-neg" : ""}" style="width:${w}%"></span></span>
            <b>${money(value)}</b></span>`;
    }

    /* Charged / Spent / Profit over the window - one money axis, three series,
       legend + tooltip + the leaderboard table underneath. */
    function moneySeriesChart(hostId, months) {
        const T = theme();
        if (!months || !months.length) { empty(hostId, "No data in this period."); return; }
        mount(hostId, {
            chart: baseChart(T, "bar", 290),
            series: [
                { name: "Charged", data: months.map((m) => Math.round(m.charged)) },
                { name: "Spent", data: months.map((m) => Math.round(m.spent)) },
                { name: "Profit", data: months.map((m) => Math.round(m.profit)) },
            ],
            colors: [T.s1, T.s2, T.s3],
            plotOptions: { bar: { columnWidth: months.length > 6 ? "78%" : "52%", borderRadius: 4, borderRadiusApplication: "end" } },
            // A 2px gap in the SURFACE colour separates touching bars - this is
            // the spacer, not a border drawn around the mark.
            stroke: { show: true, width: 2, colors: [T.surface] },
            dataLabels: { enabled: false },
            grid: gridOf(T),
            xaxis: Object.assign({ categories: months.map((m) => m.label) }, axisOf(T)),
            yaxis: { labels: { style: { colors: T.muted, fontSize: "11px" }, formatter: (v) => moneyShort(v) } },
            legend: { position: "top", horizontalAlign: "left", offsetY: -4, labels: { colors: T.muted }, markers: { radius: 3 }, fontWeight: 600 },
            tooltip: { theme: T.dark ? "dark" : "light", shared: true, intersect: false, y: { formatter: (v) => money2(v) } },
        });
    }

    /* Margin % - its own chart on its own axis. NEVER a second axis on the
       money chart: two scales on one plot invent a correlation. */
    function marginChart(hostId, months) {
        const T = theme();
        const pts = (months || []).filter((m) => m.charged > 0);
        if (pts.length < 2) { empty(hostId, "Needs at least two months with invoices."); return; }
        // Derive rather than trust: a series that reaches here without `margin`
        // would otherwise plot NaN and print "average NaN%".
        const marginOf = (m) => {
            const v = Number(m.margin);
            return isFinite(v) ? v : (m.charged ? (Number(m.profit) / m.charged) * 100 : 0);
        };
        const vals = months.map((m) => (m.charged > 0 ? Math.round(marginOf(m) * 10) / 10 : null));
        const avg = Math.round((pts.reduce((a, m) => a + marginOf(m), 0) / pts.length) * 10) / 10;
        mount(hostId, {
            chart: baseChart(T, "line", 230),
            series: [{ name: "Margin", data: vals }],
            colors: [T.s3],
            stroke: { width: 2, curve: "smooth", lineCap: "round" },
            markers: { size: 4, strokeWidth: 2, strokeColors: T.surface, hover: { size: 6 } },
            dataLabels: { enabled: false },
            grid: gridOf(T),
            xaxis: Object.assign({ categories: months.map((m) => m.label) }, axisOf(T)),
            yaxis: { labels: { style: { colors: T.muted, fontSize: "11px" }, formatter: (v) => Math.round(v) + "%" } },
            legend: { show: false },
            annotations: {
                yaxis: [{
                    y: avg, borderColor: T.muted, strokeDashArray: 0, opacity: 0.5,
                    label: { text: "average " + avg + "%", position: "right", offsetX: -4, offsetY: -2, borderWidth: 0, style: { color: T.muted, background: T.surface, fontSize: "11px", fontWeight: 600 } },
                }],
            },
            tooltip: { theme: T.dark ? "dark" : "light", y: { formatter: (v) => (v == null ? "no invoices" : v + "%") } },
        });
    }

    /* Single-series horizontal bars, direct-labelled. The labels are also the
       "relief" the light-mode palette owes for the sub-3:1 aqua. */
    function hbarChart(hostId, rows, valueKey, height) {
        const T = theme();
        if (!rows || !rows.length) { empty(hostId, "Nothing to show yet."); return; }
        const vals = rows.map((r) => Math.round(r[valueKey]));
        mount(hostId, {
            chart: baseChart(T, "bar", height || Math.max(150, 34 * rows.length + 40)),
            series: [{ name: "Profit", data: vals }],
            colors: [T.s3],
            plotOptions: {
                bar: {
                    horizontal: true, borderRadius: 4, borderRadiusApplication: "end", barHeight: "58%",
                    distributed: true,
                    colors: { ranges: [{ from: -1e12, to: -0.0001, color: T.neg }] },
                },
            },
            dataLabels: {
                enabled: true, textAnchor: "start", offsetX: 6,
                style: { colors: [T.ink], fontWeight: 700, fontSize: "11.5px" },
                formatter: (v) => moneyShort(v),
            },
            stroke: { show: true, width: 2, colors: [T.surface] },
            grid: Object.assign(gridOf(T), { xaxis: { lines: { show: true } }, yaxis: { lines: { show: false } } }),
            xaxis: Object.assign({ categories: rows.map((r) => r.name) }, axisOf(T), { labels: { show: false } }),
            yaxis: { labels: { style: { colors: T.ink, fontSize: "12px", fontWeight: 600 }, maxWidth: 170 } },
            legend: { show: false },
            tooltip: { theme: T.dark ? "dark" : "light", y: { formatter: (v) => money2(v) } },
        });
    }

    /* Charged vs spent per category - 2 series, so a legend is mandatory. */
    function categoryChart(hostId, cats) {
        const T = theme();
        const list = (cats || []).filter((c) => c.charged > 0 || c.spent > 0);
        if (!list.length) { empty(hostId, "No invoices in this period."); return; }
        mount(hostId, {
            chart: baseChart(T, "bar", Math.max(160, 46 * list.length + 60)),
            series: [
                { name: "Charged", data: list.map((c) => Math.round(c.charged)) },
                { name: "Spent", data: list.map((c) => Math.round(c.spent)) },
            ],
            colors: [T.s1, T.s2],
            plotOptions: { bar: { horizontal: true, borderRadius: 4, borderRadiusApplication: "end", barHeight: "68%" } },
            stroke: { show: true, width: 2, colors: [T.surface] },
            dataLabels: { enabled: false },
            grid: Object.assign(gridOf(T), { yaxis: { lines: { show: false } } }),
            xaxis: Object.assign({ categories: list.map((c) => c.label) }, axisOf(T), {
                labels: { style: { colors: T.muted, fontSize: "11px" }, formatter: (v) => moneyShort(v) },
            }),
            yaxis: { labels: { style: { colors: T.ink, fontSize: "12px", fontWeight: 600 } } },
            legend: { position: "top", horizontalAlign: "left", offsetY: -4, labels: { colors: T.muted }, markers: { radius: 3 }, fontWeight: 600 },
            tooltip: { theme: T.dark ? "dark" : "light", shared: true, intersect: false, y: { formatter: (v) => money2(v) } },
        });
    }

    function spark(id, data, color) {
        if (!data || data.length < 2) return;
        const T = theme();
        mount(id, {
            chart: { type: "area", height: 40, sparkline: { enabled: true }, animations: { enabled: !reduced() } },
            series: [{ data: data.map((v) => Math.round(v)) }],
            colors: [color], stroke: { curve: "smooth", width: 2 },
            fill: { type: "gradient", gradient: { opacityFrom: 0.28, opacityTo: 0 } },
            tooltip: { theme: T.dark ? "dark" : "light", y: { formatter: (v) => money2(v) }, x: { show: false } },
        });
    }

    // ---- ALL-CLIENTS view ---------------------------------------------
    function renderAllView() {
        const ov = state.overview, T = ov.totals, S = ov.series || [];
        const host = $("inBody");
        const prev = S.length >= 2 ? S[S.length - 2] : null;
        const last = S.length ? S[S.length - 1] : null;
        const spanWord = state.span === 1 ? "this month" : `${state.span} months`;
        const th = theme();

        const kpis = [
            kpi("Charged", moneyShort(T.charged), `${num(T.invoices)} invoice${T.invoices === 1 ? "" : "s"} · avg ${moneyShort(T.avg_invoice)}`, { spark: "inSpkCharged" }),
            kpi("Spent", moneyShort(T.spent), `what those invoices cost us`, { spark: "inSpkSpent" }),
            kpi("Profit", moneyShort(T.profit), (last && prev) ? trendText(last.profit, prev.profit, "prev month") : `over ${spanWord}`, { neg: T.profit < 0, spark: "inSpkProfit" }),
            kpi("Margin", pctText(T.margin), T.profit < 0 ? "running at a loss" : `keeps ${pctText(T.margin)} of every dollar`, { neg: T.profit < 0 }),
            kpi("Awaiting payment", moneyShort(T.outstanding), `${num(T.outstanding_count)} invoice${T.outstanding_count === 1 ? "" : "s"} not marked paid`),
            kpi("Open pipeline", moneyShort(T.pipeline_value), `${num(T.open)} open bid${T.open === 1 ? "" : "s"} · ${moneyShort(T.pipeline_profit)} potential profit`),
            kpi("Win rate", pctText(T.win_rate), `${num(T.won)} won · ${num(T.lost)} lost`),
            kpi("Active clients", num(T.clients), `${num(T.docs)} documents in this period`),
        ].join("");

        const clients = ov.clients || [];
        const earners = clients.filter((c) => Math.abs(c.profit) > 0.005).slice(0, 8)
            .map((c) => ({ name: c.name, profit: c.profit }));
        const conc = ov.concentration || {};

        host.innerHTML = `
            ${sect("Headline", `all clients · ${ov.period_label}`)}
            <div class="in-kpis">${kpis}</div>

            ${sect("Money over time", "charged, spent and what is left")}
            <div class="in-card">
                <div class="in-card-head">
                    <h4>Charged vs spent vs profit</h4>
                    <span class="in-card-sub">click a name in the key to hide that series</span>
                </div>
                <div class="in-chart" id="inMoney"></div>
            </div>
            <div class="in-grid">
                <div class="in-card">
                    <div class="in-card-head"><h4>Margin trend</h4><span class="in-card-sub">profit ÷ charged, per month</span></div>
                    <div class="in-chart" id="inMargin"></div>
                </div>
                <div class="in-card">
                    <div class="in-card-head"><h4>Where the money comes from</h4><span class="in-card-sub">by invoice category</span></div>
                    <div class="in-chart" id="inCats"></div>
                </div>
            </div>

            ${sect("Clients", "who pays, what it costs, what we keep")}
            <div class="in-grid">
                <div class="in-card">
                    <div class="in-card-head"><h4>Profit by client</h4><span class="in-card-sub">top ${Math.min(8, earners.length)}</span></div>
                    <div class="in-chart" id="inProfitClients"></div>
                </div>
                <div class="in-card">
                    <div class="in-card-head"><h4>How concentrated are we?</h4><span class="in-card-sub">risk if one client leaves</span></div>
                    ${meter(pctText(conc.top3), "from the top 3 clients",
                        conc.top1_name ? `<b>${esc(conc.top1_name)}</b> alone is ${pctText(conc.top1)} of profit. ${num(conc.earning_clients)} client${conc.earning_clients === 1 ? "" : "s"} turned a profit this period.` : "No profitable client yet in this period.",
                        th.s1, conc.top3 || 0)}
                    <div style="height:14px"></div>
                    ${meter(pctText(T.collected_pct), "of charged money collected",
                        `${moneyShort(T.outstanding)} still outstanding across ${num(T.outstanding_count)} invoice${T.outstanding_count === 1 ? "" : "s"}.`,
                        th.s5, T.collected_pct || 0)}
                </div>
            </div>
            <div class="in-card">
                <div class="in-card-head">
                    <h4>Every client</h4>
                    <span class="in-card-sub">click a row to open that client · click a heading to sort</span>
                </div>
                <div class="in-scroll" id="inBoard"></div>
            </div>

            ${(T.uncosted || T.unsubmitted) ? sect("Worth a look", "things that quietly distort the numbers above") : ""}
            ${(T.uncosted || T.unsubmitted) ? `<div class="in-grid">
                ${T.uncosted ? `<div class="in-note warn">
                    <span class="in-pill warn">Costs missing</span>
                    <span><b>${num(T.uncosted)} invoice${T.uncosted === 1 ? "" : "s"}</b> covering <b>${money(T.uncosted_charged)}</b> of charged money have at least one line with no cost entered. Those lines are counted at an <b>estimated</b> cost (a ${num(T.estimate_markup_pct || 40)}% markup, ${money(T.estimated_spent || 0)} in all), so the profit above is close but not exact. Enter the real costs to make it exact.</span>
                </div>` : ""}
                ${T.unsubmitted ? `<div class="in-note">
                    <span class="in-pill">Not submitted</span>
                    <span><b>${num(T.unsubmitted)} quotation${T.unsubmitted === 1 ? "" : "s"}</b> in this period were never marked submitted. They are drafts, so they never age into a lost bid and never count toward the win rate.</span>
                </div>` : ""}
            </div>` : ""}
        `;

        moneySeriesChart("inMoney", S);
        marginChart("inMargin", S);
        categoryChart("inCats", ov.categories);
        hbarChart("inProfitClients", earners, "profit");
        spark("inSpkCharged", S.map((m) => m.charged), th.s1);
        spark("inSpkSpent", S.map((m) => m.spent), th.s2);
        spark("inSpkProfit", S.map((m) => m.profit), th.s3);
        renderBoard(clients);
    }

    // ---- the leaderboard table (also the chart's WCAG twin) -------------
    const BOARD_COLS = [
        { key: "name", label: "Client", sort: (a, b) => a.name.localeCompare(b.name), align: "left" },
        { key: "charged", label: "Charged", bar: true },
        { key: "spent", label: "Spent" },
        { key: "profit", label: "Profit", bar: true },
        { key: "margin", label: "Margin", fmt: (c) => pctText(c.margin) },
        { key: "outstanding", label: "Unpaid", fmt: (c) => (c.outstanding ? money(c.outstanding) : "-") },
        { key: "invoices", label: "Inv", fmt: (c) => num(c.invoices) },
        { key: "quotes", label: "Qut", fmt: (c) => num(c.quotes) },
        { key: "win_rate", label: "Win", fmt: (c) => pctText(c.win_rate) },
        { key: "share_profit", label: "Share", fmt: (c) => pctText(c.share_profit) },
        { key: "last_activity", label: "Last seen", fmt: (c) => dateShort(c.last_activity), align: "right" },
    ];
    let boardSort = { key: "profit", dir: -1 };

    function renderBoard(clients) {
        const host = $("inBoard");
        if (!host) return;
        if (!clients || !clients.length) { host.innerHTML = `<div class="in-empty">No client activity in this period.</div>`; return; }
        const rows = clients.slice();
        const col = BOARD_COLS.find((c) => c.key === boardSort.key) || BOARD_COLS[3];
        rows.sort((a, b) => {
            if (col.sort) return col.sort(a, b) * boardSort.dir;
            const av = a[col.key], bv = b[col.key];
            const an = (av == null) ? -Infinity : (typeof av === "number" ? av : String(av));
            const bn = (bv == null) ? -Infinity : (typeof bv === "number" ? bv : String(bv));
            if (an < bn) return -1 * boardSort.dir;
            if (an > bn) return 1 * boardSort.dir;
            return 0;
        });
        const maxCharged = Math.max(1, ...rows.map((c) => Math.abs(c.charged)));
        const maxProfit = Math.max(1, ...rows.map((c) => Math.abs(c.profit)));

        const head = BOARD_COLS.map((c) =>
            `<th data-key="${c.key}"${c.align === "left" ? ' style="text-align:left"' : ""}>${esc(c.label)}` +
            (boardSort.key === c.key ? `<span class="in-sortmark">${boardSort.dir < 0 ? "▼" : "▲"}</span>` : "") + `</th>`).join("");

        const body = rows.map((c) => {
            const neg = c.profit < 0;
            return `<tr data-client="${esc(c.name)}">
                <td><span class="in-name"><span class="in-rank">${c.rank}</span><b title="${esc(c.name)}">${esc(c.name)}</b></span></td>
                <td>${cellbar(c.charged, maxCharged, false)}</td>
                <td>${money(c.spent)}</td>
                <td class="${neg ? "is-neg" : ""}">${cellbar(c.profit, maxProfit, neg)}</td>
                <td class="${neg ? "is-neg" : ""}">${pctText(c.margin)}</td>
                <td>${c.outstanding ? money(c.outstanding) : "-"}</td>
                <td>${num(c.invoices)}</td>
                <td>${num(c.quotes)}</td>
                <td>${pctText(c.win_rate)}</td>
                <td>${pctText(c.share_profit)}</td>
                <td>${dateShort(c.last_activity)}</td>
            </tr>`;
        }).join("");

        host.innerHTML = `<table class="in-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
        host.querySelectorAll("th[data-key]").forEach((th) => {
            th.onclick = () => {
                const k = th.dataset.key;
                boardSort = (boardSort.key === k) ? { key: k, dir: -boardSort.dir } : { key: k, dir: k === "name" ? 1 : -1 };
                renderBoard(clients);
            };
        });
        host.querySelectorAll("tbody tr[data-client]").forEach((tr) => {
            tr.onclick = () => pickClient(tr.dataset.client);
        });
    }

    // ---- sortable tables and rows that open their document (2026-10-06) ----
    /* Waleed: "make the titles of the tables clickable so I can sort for unpaid
       invoices", and "make these document lines clickable to send me to that
       document". The client view's Top items / Invoices / Documents tables now
       sort on any header - click again to reverse - and an invoice, quotation or
       delivery-note row opens it in its own editor.

       Until a header is clicked a table keeps the backend's order. Blank values
       sort last both ways, and ties keep that order, so a sort never shuffles
       rows that compare equal. The choice is kept per table while the app runs. */
    const tableSort = {};          // table id -> { key, dir }

    /* "31/8/2026" (how documents store dates) or ISO, as a number - never
       `new Date(string)`, which reads 4/6/2026 as April. */
    function dateVal(v) {
        const s = String(v || "").trim();
        let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
        if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3]);
        m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
        return m ? Date.UTC(+m[3], +m[2] - 1, +m[1]) : null;
    }

    function sortRows(id, rows, cols) {
        const s = tableSort[id];
        if (!s) return rows.slice();
        const col = cols.find((c) => c.key === s.key);
        if (!col) return rows.slice();
        const val = col.value || ((r) => r[col.key]);
        return rows.map((r, i) => [r, i]).sort((A, B) => {
            const a = val(A[0]), b = val(B[0]);
            const blankA = a == null || a === "", blankB = b == null || b === "";
            if (blankA || blankB) return (blankA - blankB) || (A[1] - B[1]);
            const cmp = (typeof a === "number" && typeof b === "number")
                ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
            return (cmp * s.dir) || (A[1] - B[1]);
        }).map((p) => p[0]);
    }

    function sortHead(id, cols) {
        const s = tableSort[id];
        return cols.map((c) => {
            const on = s && s.key === c.key;
            return `<th data-sort="${c.key}"${c.left ? ' style="text-align:left"' : ""}
                aria-sort="${on ? (s.dir < 0 ? "descending" : "ascending") : "none"}"
                title="Sort by ${esc(c.label.toLowerCase())}">${esc(c.label)}${
                on ? `<span class="in-sortmark">${s.dir < 0 ? "▼" : "▲"}</span>` : ""}</th>`;
        }).join("");
    }

    /* A header click: the same column reverses; a new one starts at its own
       natural direction (`first`: 1 = A to Z / oldest / "Unpaid" first, -1 =
       biggest / newest first). */
    function wireSortHead(host, id, cols, redraw) {
        host.querySelectorAll("th[data-sort]").forEach((th) => {
            th.onclick = () => {
                const k = th.dataset.sort;
                const col = cols.find((c) => c.key === k) || {};
                const cur = tableSort[id];
                tableSort[id] = (cur && cur.key === k) ? { key: k, dir: -cur.dir } : { key: k, dir: col.first || -1 };
                redraw();
            };
        });
    }

    const DOC_LOADER = { inv: "loadInvForEdit", quote: "loadQuoteForEdit", dn: "loadDnForEdit" };

    /* A row opens the app's shared document PREVIEW (2026-10-06) - the popup
       the To-Do and the dashboard already use (`window.openTodoDocModal`).
       Waleed: "so I can go back easier". Reading a document is a look, not a
       trip: close the popup and the sorted table is still there. "Open document"
       in the popup goes on to the editor; an unpaid invoice also gets "Mark
       paid", after which the page refreshes in place. Without the To-Do module
       (it is always loaded in the app) the row falls back to the editor. */
    function openDoc(kind, no, row) {
        if (!no) return;
        if (window.openTodoDocModal) {
            const cat = kind === "inv" && row && row.paid === false ? "insights-unpaid" : "insights";
            window.openTodoDocModal(cat, kind, no, (row && row.date) || "", cat,
                                    { onAfterAction: refreshInPlace });
            return;
        }
        const fn = window[DOC_LOADER[kind]];
        if (typeof fn !== "function") {
            if (window.toast) window.toast("Can't open this document from here.", true);
            return;
        }
        fn(no);
    }

    /* After "Mark paid": re-read the numbers and keep the reader where they
       were - the sort lives in `tableSort`, the scroll position is put back on
       whichever ancestor actually scrolls (it depends on the window layout). */
    function scrollerOf(el) {
        for (let n = el && el.parentElement; n; n = n.parentElement) {
            const oy = getComputedStyle(n).overflowY;
            if ((oy === "auto" || oy === "scroll") && n.scrollHeight > n.clientHeight) return n;
        }
        return document.scrollingElement || document.documentElement;
    }
    async function refreshInPlace() {
        const sc = scrollerOf($("inBody"));
        const top = sc ? sc.scrollTop : 0;
        await load();
        if (sc) sc.scrollTop = top;
    }

    /* Rows carrying data-doc-kind open their document: click, or Enter / Space
       when focused (each is tabindex=0). */
    function wireDocRows(host) {
        host.querySelectorAll("tr[data-doc-kind]").forEach((tr) => {
            const go = () => openDoc(tr.dataset.docKind, tr.dataset.docNo, {
                paid: tr.dataset.docPaid === "1" ? true : tr.dataset.docPaid === "0" ? false : null,
                date: tr.dataset.docDate || "",
            });
            tr.onclick = go;
            tr.onkeydown = (e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
            };
        });
    }

    /* Takes the record, not its number: the attributes are escaped here, once. */
    function docRowAttrs(kind, rec) {
        const no = (rec && rec.no) || "";
        const paid = rec && rec.paid === true ? "1" : rec && rec.paid === false ? "0" : "";
        return `data-doc-kind="${esc(kind)}" data-doc-no="${esc(no)}" data-doc-paid="${paid}"
            data-doc-date="${esc((rec && rec.date) || "")}" tabindex="0" title="Preview ${esc(no)}"`;
    }

    // ---- ONE-CLIENT view ------------------------------------------------
    function renderClientView() {
        const d = state.detail, c = d.client, co = d.company, host = $("inBody");
        const th = theme();
        const S = c.months || [];
        const neg = c.profit < 0;
        const since = c.first_seen ? dateShort(c.first_seen) : "";

        const meta = [];
        if (c.rank) meta.push(`#${c.rank} of ${d.client_count} by profit`);
        if (since) meta.push(`client since ${since}`);
        if (c.last_activity) meta.push(`last document ${dateShort(c.last_activity)}`);
        meta.push(`${c.active_months} of ${state.span} month${state.span === 1 ? "" : "s"} active`);

        const kpis = [
            kpi("Charged", moneyShort(c.charged), `${num(c.invoices)} invoice${c.invoices === 1 ? "" : "s"} · avg ${moneyShort(c.avg_invoice)}`),
            kpi("Spent", moneyShort(c.spent), c.markup ? `we charge ${c.markup}× what we pay` : "no costs recorded"),
            kpi("Margin", pctText(c.margin), neg ? "running at a loss" : `keeps ${pctText(c.margin)} of every dollar`, { neg }),
            kpi("Awaiting payment", moneyShort(c.outstanding), `${num(c.outstanding_count)} unpaid · ${moneyShort(c.collected)} collected`),
            kpi("Win rate", pctText(c.win_rate), `${num(c.won)} won · ${num(c.lost)} lost · ${num(c.open)} open`),
            kpi("Open pipeline", moneyShort(c.pipeline_value), `${moneyShort(c.pipeline_profit)} potential profit`),
            kpi("Quote → invoice", c.avg_days_to_invoice == null ? "-" : `${c.avg_days_to_invoice}d`, "average time to bill a won quote"),
            kpi("Biggest invoice", moneyShort(c.biggest_invoice), c.best_month ? `best month ${esc(c.best_month)}` : "&nbsp;"),
        ].join("");

        const items = d.items || [];
        const maxItem = Math.max(1, ...items.map((i) => Math.abs(i.charged)));
        const ITEM_COLS = [
            { key: "name", label: "Item", left: true, first: 1 },
            { key: "charged", label: "Charged" },
            { key: "spent", label: "Spent" },
            { key: "profit", label: "Profit" },
            { key: "margin", label: "Margin" },
            { key: "count", label: "Times" },
        ];
        const drawItems = () => {
            const host = $("inItemsTbl");
            if (!host) return;
            if (!items.length) { host.innerHTML = `<div class="in-empty">No invoiced lines in this period.</div>`; return; }
            host.innerHTML = `<table class="in-table">
                <thead><tr>${sortHead("items", ITEM_COLS)}</tr></thead>
                <tbody>${sortRows("items", items, ITEM_COLS).map((i) => `<tr>
                    <td><span class="in-name"><b title="${esc(i.name)}">${esc(i.name)}</b></span></td>
                    <td>${cellbar(i.charged, maxItem, false)}</td>
                    <td>${money(i.spent)}</td>
                    <td class="${i.profit < 0 ? "is-neg" : ""}">${money(i.profit)}</td>
                    <td class="${i.profit < 0 ? "is-neg" : ""}">${pctText(i.margin)}</td>
                    <td>${num(i.count)}</td></tr>`).join("")}</tbody></table>`;
            wireSortHead(host, "items", ITEM_COLS, drawItems);
        };

        /* Delivery notes billed as ONE invoice line are revenue but not items,
           so the backend keeps them out of the table above (2026-08-25). A
           filter the reader cannot see is a lie, so it is stated here - with
           how much of it is uncosted, because those lines report as pure
           profit until their delivery notes are costed. */
        const dnl = d.dn_lines || {};
        const dnLineNote = dnl.count ? `<div class="in-note" style="margin-top:12px">
            <span class="in-pill">Not listed above</span>
            <span><b>${num(dnl.count)} invoice line${dnl.count === 1 ? "" : "s"}</b> in this period
            ${dnl.count === 1 ? "is a whole delivery note" : "are whole delivery notes"}
            billed as one line - <b>${money(dnl.charged)}</b> charged. They are counted in
            every total on this page, but they are documents, not items, so they are left out
            of the table above.${dnl.uncosted ? ` <b>${num(dnl.uncosted)}</b> of them
            ${dnl.uncosted === 1 ? "has" : "have"} no cost recorded, so
            ${dnl.uncosted === 1 ? "its cost is" : "their cost is"} an estimate here.` : ""}</span>
        </div>` : "";

        /* ---- Invoices: paid or not, losing or not, and WHY (2026-08-26) ----
           The Documents table below mixes quotations, delivery notes and
           invoices and answers "what happened". This answers the two questions
           actually asked of an invoice: has the money arrived, and if the
           invoice lost money, what did it.

           "Why" is never just a red total. Three causes, in the order a reader
           can act on them: lines sold below cost (named, with the dollar hole),
           document-level extra costs that no line carries, and lines with no
           cost entered at all - which means the figure is a best case and the
           real loss may be worse. Colour never carries any of it: every state
           is a word. */
        const invs = d.invoices || [];
        const IS = d.invoice_summary || {};
        const invWhy = (x) => {
            const bits = [];
            if (x.losing_line_count) {
                const named = x.losing_lines.map((l) =>
                    `<b>${esc(l.name)}</b> ${money(-l.loss)}`).join(", ");
                const rest = x.losing_line_count - x.losing_lines.length;
                bits.push(`${num(x.losing_line_count)} line${x.losing_line_count === 1 ? "" : "s"}
                    sold below cost (${money(-x.losing_line_total)} in total): ${named}${rest > 0
                        ? ` and ${num(rest)} more` : ""}`);
            }
            if (x.extra > 0) {
                bits.push(`${money(x.extra)} of extra costs carried by the document
                    rather than by any line`);
            }
            if (x.uncosted_lines) {
                bits.push(`${num(x.uncosted_lines)} line${x.uncosted_lines === 1 ? "" : "s"}
                    with no cost entered, counted at an <b>estimated</b> cost - the
                    real loss may differ`);
            }
            if (!bits.length) {
                bits.push(`its costs come to more than it charged; no single line is
                    below cost, so the margin was thin everywhere`);
            }
            return bits.join(" \u00b7 ");
        };

        /* Status sorts Unpaid first on the first click - the question asked of
           this column is "what is still owed" - and a losing invoice ahead of a
           healthy one within each. */
        const INV_COLS = [
            { key: "no", label: "Invoice", left: true, first: 1 },
            { key: "label", label: "Type", left: true, first: 1 },
            { key: "date", label: "Date", value: (x) => dateVal(x.date) },
            { key: "charged", label: "Charged" },
            { key: "profit", label: "Profit" },
            { key: "margin", label: "Margin" },
            { key: "status", label: "Status", left: true, first: 1,
              value: (x) => (x.paid ? 2 : 0) + (x.losing ? 0 : 1) },
        ];
        const drawInvoices = () => {
            const host = $("inInvTbl");
            if (!host) return;
            if (!invs.length) { host.innerHTML = `<div class="in-empty">No invoices for this client in this period.</div>`; return; }
            const rowsHtml = sortRows("invoices", invs, INV_COLS).map((x) => {
                const flags = [
                    x.paid ? `<span class="in-pill">Paid</span>`
                           : `<span class="in-pill warn">Unpaid</span>`,
                    x.losing ? `<span class="in-pill neg">Loss</span>` : "",
                    x.costed ? "" : `<span class="in-pill warn">No costs</span>`,
                ].filter(Boolean).join(" ");
                // The "why" line travels with its invoice whichever way the table sorts.
                const why = x.losing ? `<tr class="in-why in-link" ${docRowAttrs("inv", x)}><td colspan="7">
                    <span class="in-why-tag">Why</span><span>${invWhy(x)}</span></td></tr>` : "";
                return `<tr class="in-link${x.losing ? " is-losing" : ""}" ${docRowAttrs("inv", x)}>
                    <td><b>${esc(x.no)}</b></td>
                    <td style="text-align:left">${esc(x.label || "-")}</td>
                    <td>${esc(x.date || "-")}</td>
                    <td>${money(x.charged)}</td>
                    <td class="${x.profit < 0 ? "is-neg" : ""}">${money(x.profit)}</td>
                    <td class="${x.profit < 0 ? "is-neg" : ""}">${pctText(x.margin)}</td>
                    <td style="text-align:left">${flags}</td></tr>${why}`;
            }).join("");
            host.innerHTML = `<table class="in-table">
                <thead><tr>${sortHead("invoices", INV_COLS)}</tr></thead>
                <tbody>${rowsHtml}</tbody></table>`;
            wireSortHead(host, "invoices", INV_COLS, drawInvoices);
            wireDocRows(host);
        };

        const invKpis = invs.length ? `<div class="in-kpis">
            ${kpi("Paid", money(IS.paid_charged),
                  `${num(IS.paid)} of ${num(IS.count)} invoice${IS.count === 1 ? "" : "s"}`)}
            ${kpi("Not paid yet", money(IS.unpaid_charged),
                  `${num(IS.unpaid)} invoice${IS.unpaid === 1 ? "" : "s"} still owed`,
                  { neg: false })}
            ${kpi(IS.losing === 1 ? "Losing money" : "Losing money",
                  IS.losing ? money(-IS.losing_loss) : "None",
                  IS.losing ? `${num(IS.losing)} invoice${IS.losing === 1 ? "" : "s"} cost more than they charged`
                            : "every invoice covered its costs",
                  { neg: !!IS.losing })}
        </div>` : "";

        const KIND = { inv: "Invoice", quote: "Quotation", dn: "Delivery note" };
        const docs = d.docs || [];
        // A delivery note carries no money, so its money cells are blank and sort last.
        const money_ = (k) => (x) => (x.kind === "dn" ? null : x[k]);
        const DOC_COLS = [
            { key: "no", label: "Document", left: true, first: 1 },
            { key: "type", label: "Type", left: true, first: 1,
              value: (x) => (KIND[x.kind] || x.kind) + (x.label ? " " + x.label : "") },
            { key: "date", label: "Date", value: (x) => dateVal(x.date) },
            { key: "charged", label: "Charged", value: money_("charged") },
            { key: "spent", label: "Spent", value: money_("spent") },
            { key: "profit", label: "Profit", value: money_("profit") },
            { key: "margin", label: "Margin", value: money_("margin") },
            // Unpaid invoices first, then paid, then quotations by state, then delivery notes.
            { key: "status", label: "Status", left: true, first: 1,
              value: (x) => x.kind === "inv" ? (x.paid ? "1" : "0")
                          : x.kind === "quote" ? "2 " + (x.status || "") : "3" },
        ];
        const drawDocs = () => {
            const host = $("inDocsTbl");
            if (!host) return;
            if (!docs.length) { host.innerHTML = `<div class="in-empty">No documents for this client in this period.</div>`; return; }
            host.innerHTML = `<table class="in-table">
                <thead><tr>${sortHead("docs", DOC_COLS)}</tr></thead>
                <tbody>${sortRows("docs", docs, DOC_COLS).map((x) => {
                    const flags = [];
                    if (x.kind === "inv") flags.push(x.paid ? `<span class="in-pill">Paid</span>` : `<span class="in-pill warn">Unpaid</span>`);
                    if (x.kind === "quote" && x.status) flags.push(`<span class="in-pill">${esc(x.status[0].toUpperCase() + x.status.slice(1))}</span>`);
                    if (!x.costed && x.kind !== "dn") flags.push(`<span class="in-pill warn">No costs</span>`);
                    return `<tr class="in-link" ${docRowAttrs(x.kind, x)}>
                        <td><b>${esc(x.no)}</b></td>
                        <td style="text-align:left">${esc(KIND[x.kind] || x.kind)}${x.label ? ` · ${esc(x.label)}` : ""}</td>
                        <td>${esc(x.date || "-")}</td>
                        <td>${x.kind === "dn" ? "-" : money(x.charged)}</td>
                        <td>${x.kind === "dn" ? "-" : money(x.spent)}</td>
                        <td class="${x.profit < 0 ? "is-neg" : ""}">${x.kind === "dn" ? "-" : money(x.profit)}</td>
                        <td class="${x.profit < 0 ? "is-neg" : ""}">${x.kind === "dn" ? "-" : pctText(x.margin)}</td>
                        <td style="text-align:left">${flags.join(" ")}</td></tr>`;
                }).join("")}</tbody></table>`;
            wireSortHead(host, "docs", DOC_COLS, drawDocs);
            wireDocRows(host);
        };

        const shareNote = co.profit > 0
            ? `${pctText(c.share_profit)} of all profit and ${pctText(c.share_charged)} of everything charged in this period.`
            : `${pctText(c.share_charged)} of everything charged in this period.`;

        host.innerHTML = `
            <div class="in-hero">
                <div>
                    <div class="in-hero-tools">
                        <button type="button" class="in-back" id="inBack">← All clients</button>
                        <button type="button" class="in-back" id="inStatement"
                            title="One PDF for a month: every invoice, its delivery note and purchase orders, linked">Monthly statement</button>
                    </div>
                    <h2 class="in-hero-name" style="margin-top:10px">${esc(c.name)}</h2>
                    <div class="in-hero-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</div>
                    <div class="in-hero-meta"><span>${shareNote}</span></div>
                </div>
                <div class="in-hero-fig">
                    <div class="in-hero-num${neg ? " is-neg" : ""}">${moneyShort(c.profit)}</div>
                    <div class="in-hero-cap">${neg ? "loss" : "profit"} · ${money(c.charged)} charged − ${money(c.spent)} spent</div>
                </div>
            </div>

            ${sect("The numbers", ov_period())}
            <div class="in-kpis">${kpis}</div>

            ${sect("Over time", "how this client trends")}
            <div class="in-card">
                <div class="in-card-head">
                    <h4>Charged vs spent vs profit</h4>
                    <span class="in-card-sub">click a name in the key to hide that series</span>
                </div>
                <div class="in-chart" id="inMoney"></div>
            </div>
            <div class="in-grid">
                <div class="in-card">
                    <div class="in-card-head"><h4>Margin trend</h4><span class="in-card-sub">profit ÷ charged, per month</span></div>
                    <div class="in-chart" id="inMargin"></div>
                </div>
                <div class="in-card">
                    <div class="in-card-head"><h4>What we sell them</h4><span class="in-card-sub">by invoice category</span></div>
                    <div class="in-chart" id="inCats"></div>
                </div>
            </div>

            ${sect("Top items", "the lines that carry this client")}
            <div class="in-card"><div class="in-scroll" id="inItemsTbl"></div>${dnLineNote}</div>

            ${sect("Invoices", invs.length
                ? `${invs.length} in this period \u00b7 what is paid, and what is losing money`
                : "none in this period")}
            ${invKpis}
            <div class="in-card"><div class="in-scroll tall" id="inInvTbl"></div></div>

            ${sect("Documents", `${docs.length} in this period · click a heading to sort, a row to preview it`)}
            <div class="in-card"><div class="in-scroll tall" id="inDocsTbl"></div></div>

            ${c.uncosted ? `<div class="in-note warn" style="margin-top:14px">
                <span class="in-pill warn">Costs missing</span>
                <span><b>${num(c.uncosted)} invoice${c.uncosted === 1 ? "" : "s"}</b> worth <b>${money(c.uncosted_charged)}</b> have lines with no cost entered. They are counted at an <b>estimated</b> cost, so this client's profit is close but not exact until you enter the real costs.</span>
            </div>` : ""}
        `;

        const back = $("inBack");
        if (back) back.onclick = () => pickClient(null);
        const stmt = $("inStatement");
        if (stmt) stmt.onclick = () => openStatement(c.name);
        drawItems();
        drawInvoices();
        drawDocs();
        moneySeriesChart("inMoney", S);
        marginChart("inMargin", S);
        categoryChart("inCats", c.categories);
    }

    /* ---- Monthly statement (2026-10-06) ----------------------------------
       One PDF per client per month: a summary page of the month's invoices,
       then every invoice, delivery note and purchase order it names, each
       number a link to its pages (backend/statements.py). The month offered
       first is the one Insights is showing, when the client invoiced in it;
       otherwise their latest. Built in a popup so the page stays where it was. */
    async function openStatement(name) {
        const P = window.Popup;
        if (!P || !api().make_client_statement) return;
        const res = await api().client_statement_months(name);
        const months = (res && res.ok && res.months) || [];
        if (!months.length) {
            P.alert("Monthly statement", `${name} has no issued invoices yet.`);
            return;
        }
        const key = (m) => `${m.year}-${m.month}`;
        const shown = months.find((m) => m.year === state.year && m.month === state.month);

        const body = document.createElement("div");
        body.className = "in-stmt";
        const label = document.createElement("label");
        label.textContent = "Month";
        const pick = document.createElement("select");
        pick.id = "inStmtMonth";
        months.forEach((m) => {
            const o = document.createElement("option");
            o.value = key(m);
            // textContent, not HTML - and no template string, so insights_view.test.js
            // keeps its "every interpolated label is escaped" rule strict.
            o.textContent = m.label + " - " + m.invoices + " invoice" + (m.invoices === 1 ? "" : "s");
            pick.appendChild(o);
        });
        pick.value = key(shown || months[0]);
        label.appendChild(pick);
        const note = document.createElement("p");
        note.className = "in-stmt-note";
        const sayNote = () => {
            const m = months.find((x) => key(x) === pick.value);
            note.textContent = "A summary page, then each invoice (the paid copy when one is on file), "
                + "its delivery note and purchase orders. Saved in the REPORTS folder"
                + (m && m.exists ? " - this replaces the statement already made for that month." : ".");
        };
        pick.onchange = sayNote;
        sayNote();
        body.append(label, note);

        let made = null;
        const h = P.open({
            title: "Monthly statement", subtitle: name, size: "md", body,
            actions: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Make PDF", value: "make", cls: "primary", keep: true,
                  onClick: () => (made ? reveal() : make()) },
            ],
        });
        const btn = () => h.panel.querySelector('[data-value="make"]');
        const reveal = () => { if (made) api().reveal_in_folder(made.path); };
        async function make() {
            const m = months.find((x) => key(x) === pick.value);
            const b = btn();
            if (!m || !b || b.disabled) return;
            b.disabled = true;
            b.textContent = "Making...";
            pick.disabled = true;
            const r = await api().make_client_statement(name, m.year, m.month);
            if (h.closed) return;
            if (!r || !r.ok) {
                b.disabled = false;
                b.textContent = "Make PDF";
                pick.disabled = false;
                note.textContent = (r && r.error) || "The statement could not be made.";
                return;
            }
            made = r;
            const lines = [
                r.label + ": " + r.invoices + " invoice" + (r.invoices === 1 ? "" : "s") + ", " + r.paid + " paid, " + r.pending + " pending.",
                `${r.pages} pages, ${r.size_mb} MB. Opened, and saved as:`,
                r.path,
            ];
            if (r.missing && r.missing.length) lines.push(`Not found, so not included: ${r.missing.join(", ")}.`);
            if (r.uncosted && r.uncosted.length) lines.push(`Costs incomplete on: ${r.uncosted.join(", ")}.`);
            P.setBody(h, (el) => {
                lines.forEach((t, i) => {
                    const p = document.createElement("p");
                    p.className = i === 2 ? "in-stmt-path" : "in-stmt-note";
                    p.textContent = t;
                    el.appendChild(p);
                });
            });
            b.disabled = false;
            b.textContent = "Show in folder";
            const cancel = h.panel.querySelector('[data-value="cancel"]');
            if (cancel) cancel.textContent = "Close";
            api().open_path(r.path);
        }
    }

    function ov_period() {
        return (state.overview && state.overview.period_label) || "";
    }

    // ------------------------------------------------------------ wiring
    function init() {
        const span = $("inSpan");
        if (span) span.querySelectorAll("button[data-span]").forEach((b) => {
            b.onclick = () => {
                const v = parseInt(b.dataset.span, 10);
                if (!SPANS.includes(v) || v === state.span) return;
                state.span = v;
                load();
            };
        });
        const prev = $("inMonthPrev"); if (prev) prev.onclick = () => stepMonth(-1);
        const next = $("inMonthNext"); if (next) next.onclick = () => stepMonth(1);
        const search = $("inClientSearch");
        if (search) search.oninput = () => renderRail();
        syncChrome();
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
