/* Activity log (2026-10-23) - what happened, what broke, and whose fault it
   was (Waleed: "know what broke, if it is his fault or the server's or the
   app's, and what was done before it").

   The screen reads backend/activity_log.py through activity_list /
   activity_detail / activity_people, and only the admin may open it
   (activity_can_view; the desktop app's one user is its owner). It is built
   on the To-Do page and Insights pieces: KPI tiles that filter, one table,
   and a popup per row that says in words whose fault it was and lists what
   that person did in the minutes before.

   Also here: "Report a problem", for everyone - the dashboard's flag button
   opens a short popup and the report lands in the same log, with the steps
   that led to it (static/activity_capture.js).

   Every value shown comes from people, documents or files, so ALL of it goes
   through esc(). Results are WORDS; colour only backs them up
   ([[waleed-colour-blind]]). */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const api = () => window.pywebview && window.pywebview.api;
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
    const clip = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 3) + "..." : s; };

    // Whose fault, in words.
    const RESULT = {
        app_error:    { word: "App problem",    sub: "A mistake in the app - not the person's fault" },
        server_error: { word: "Server problem", sub: "The server's files, disk, database or Chrome" },
        network:      { word: "Network",        sub: "A PC could not reach the server" },
        refused:      { word: "Refused",        sub: "The app said no - a rule or what was entered" },
        warning:      { word: "Error shown",    sub: "An error message someone was shown" },
        report:       { word: "Report",         sub: "Sent with Report a problem" },
        ok:           { word: "OK",             sub: "It worked" },
        info:         { word: "Info",           sub: "Worth knowing" },
    };
    const KPI_ORDER = ["app_error", "server_error", "network", "refused", "warning", "report"];
    const PROBLEMS_AND_REFUSALS = ["app_error", "server_error", "network", "warning", "report", "refused"];
    const SOURCE = { call: "App", screen: "Screen", pc: "PC", server: "Server", login: "Login" };
    const COMPANY = { abraj: "Abraj Albadr", eshraqat: "Eshraqat Albadr" };

    const state = { show: "problems", period: "week", person: "", outcome: "", q: "" };
    let rows = [], counts = {}, more = false, initOnce = false, loading = false, searchT = null;

    // ------------------------------------------------------------ words
    function fault(ev) {
        const who = ev.person || "the person at this PC";
        switch (ev.outcome) {
            case "app_error":
                return `A mistake in the app - not ${who}'s fault. The technical details below show where it happened.`;
            case "server_error":
                return `The server's machine let it down: a file, the disk, the database, or a program such as Chrome. Not ${who}'s fault.`;
            case "network":
                return `${who}'s PC could not reach the server, or the line broke. Check the network and that the server is on.`;
            case "refused":
                return `The app said no on purpose - a rule, or what was entered. ${who} can put it right; the message is what they were told.`;
            case "warning":
                return `${who} was shown this error message.`;
            case "report":
                return `${who} reported this. What they did just before is listed below.`;
            case "ok":
                return "This worked.";
            default:
                return "For information.";
        }
    }

    function when(ts) {
        const d = new Date(ts * 1000);
        const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        const today = new Date();
        if (d.toDateString() === today.toDateString()) return time;
        return d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " + time;
    }

    function since() {
        const now = new Date();
        if (state.period === "today") return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
        if (state.period === "week") return now.getTime() / 1000 - 7 * 86400;
        if (state.period === "month") return now.getTime() / 1000 - 30 * 86400;
        return 0;
    }

    /* The filters the server is asked for. The tiles count by outcome inside
       the period and person only, so they do not change as the table narrows. */
    function filters(beforeId) {
        const f = { limit: 200 };
        const s = since();
        if (s) f.since = s;
        if (state.person) f.person = state.person;
        if (state.q.trim()) f.q = state.q.trim();
        if (state.outcome) f.outcomes = [state.outcome];
        else if (state.show === "problems") f.outcomes = PROBLEMS_AND_REFUSALS.slice();
        if (state.show === "steps") f.include_routine = true;
        if (beforeId) f.before_id = beforeId;
        return f;
    }
    function tileFilters() {
        const f = { limit: 1 };
        const s = since();
        if (s) f.since = s;
        if (state.person) f.person = state.person;
        return f;
    }

    // ------------------------------------------------------------ show
    async function showActivity() {
        document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
        ["poStrip", "poActions", "quoteActions", "dnActions", "invActions"]
            .forEach((id) => { const el = $(id); if (el) el.hidden = true; });
        document.body.classList.remove("on-dashboard", "has-rail");
        const rail = $("typeRail"); if (rail) rail.hidden = true;
        if (window.OpenPanel) window.OpenPanel.hide();
        $("activityView").hidden = false;
        const home = $("btnHome"); if (home) home.hidden = false;
        if (window.setBrand) window.setBrand("Activity log");
        if (!initOnce) { initControls(); initOnce = true; }
        await loadPeople();
        await load(false);
    }
    window.showActivity = showActivity;

    function initControls() {
        $("actShow").value = state.show;
        $("actPeriod").value = state.period;
        $("actShow").addEventListener("change", (e) => { state.show = e.target.value; state.outcome = ""; load(false); });
        $("actPeriod").addEventListener("change", (e) => { state.period = e.target.value; load(false); });
        $("actPerson").addEventListener("change", (e) => { state.person = e.target.value; load(false); });
        $("actSearch").addEventListener("input", (e) => {
            state.q = e.target.value;
            clearTimeout(searchT);
            searchT = setTimeout(() => load(false), 300);
        });
        $("actRefresh").onclick = () => load(false);
        $("actBack").onclick = () => { if (window.showHub) window.showHub(); };
    }

    async function loadPeople() {
        const sel = $("actPerson");
        try {
            const r = await api().activity_people();
            const names = (r && r.ok && r.people) || [];
            sel.innerHTML = `<option value="">Everyone</option>`
                + names.map((n) => `<option value="${esc(n)}">${esc(n || "This PC (desktop)")}</option>`).join("");
            sel.value = names.includes(state.person) ? state.person : "";
            state.person = sel.value;
        } catch (e) { /* the list still works without names */ }
    }

    // ------------------------------------------------------------ data
    async function load(append) {
        if (loading) return;
        loading = true;
        const body = $("actBody");
        try {
            if (!append && !rows.length) body.innerHTML = `<div class="todo-loading">Loading...</div>`;
            const a = api();
            const last = append && rows.length ? rows[rows.length - 1].id : 0;
            const [list, tiles] = await Promise.all([
                a.activity_list(filters(last)),
                append ? Promise.resolve(null) : a.activity_list(tileFilters()),
            ]);
            if (!list || !list.ok) {
                body.innerHTML = `<div class="act-empty">${esc((list && list.error) || "The log could not be read.")}</div>`;
                return;
            }
            rows = append ? rows.concat(list.rows || []) : (list.rows || []);
            more = !!list.more;
            if (tiles && tiles.ok) counts = tiles.counts || {};
            render();
        } catch (e) {
            body.innerHTML = `<div class="act-empty">${esc((e && e.message) || e)}</div>`;
        } finally {
            loading = false;
        }
    }

    // ------------------------------------------------------------ draw
    function kpiHtml(o, i) {
        const on = state.outcome === o;
        return `<button type="button" class="in-kpi act-kpi act-kpi-${o}${on ? " is-on" : ""}" data-outcome="${o}"`
            + ` aria-pressed="${on}" style="--i:${i}" title="Show only: ${esc(RESULT[o].word)}">`
            + `<div class="in-kpi-label">${esc(RESULT[o].word)}</div>`
            + `<div class="in-kpi-value">${Number(counts[o]) || 0}</div>`
            + `<div class="in-kpi-sub">${esc(RESULT[o].sub)}</div></button>`;
    }

    function badge(outcome) {
        const res = RESULT[outcome] || RESULT.info;
        return `<span class="act-res act-res-${esc(outcome)}">${esc(res.word)}</span>`;
    }

    function rowHtml(r) {
        const pcName = String(r.pc || "").replace(/\s*\([^)]*\)\s*$/, "") || String(r.pc || "");
        return `<tr class="in-link act-row" tabindex="0" data-id="${Number(r.id) || 0}" title="Open what happened">`
            + `<td class="act-when">${esc(when(r.ts))}</td>`
            + `<td>${esc(r.person || "This PC")}</td>`
            + `<td title="${esc(r.pc)}">${esc(pcName)}</td>`
            + `<td><span class="act-src">${esc(SOURCE[r.source] || r.source)}</span>${esc(r.summary || r.action)}`
            + (r.ref ? ` <span class="act-ref">${esc(r.ref)}</span>` : "") + `</td>`
            + `<td>${badge(r.outcome)}</td>`
            + `<td class="act-msg-cell" title="${esc(r.message)}">${esc(clip(r.message, 140))}</td></tr>`;
    }

    function heading() {
        if (state.outcome) return RESULT[state.outcome].word;
        return state.show === "problems" ? "Problems and refusals"
            : state.show === "steps" ? "Every step" : "Everything that happened";
    }

    function render() {
        const body = $("actBody");
        const table = rows.length
            ? `<div class="in-card act-card"><div class="in-scroll"><table class="in-table act-table"><thead><tr>`
              + ["When", "Who", "PC", "What happened", "Result", "Message"].map((h) => `<th>${h}</th>`).join("")
              + `</tr></thead><tbody>${rows.map(rowHtml).join("")}</tbody></table></div></div>`
              + (more ? `<div class="act-more"><button type="button" class="secondary" id="actMore">Show older</button></div>` : "")
            : `<div class="act-empty"><strong>Nothing here.</strong><span>${state.show === "problems" && !state.outcome
                ? "No problems or refusals in this period - everything worked."
                : "Nothing matches these filters."}</span></div>`;
        body.innerHTML = `<div class="todo-page act-page">`
            + `<div class="in-kpis act-kpis">${KPI_ORDER.map(kpiHtml).join("")}</div>`
            + `<div class="in-sect"><h3>${esc(heading())}</h3><span>${rows.length}${more ? "+" : ""} shown</span>`
            + (state.outcome ? `<button type="button" class="in-back" id="actClearTile">Show all again</button>` : "")
            + `</div>${table}</div>`;
        body.querySelectorAll(".act-kpi").forEach((b) => {
            b.onclick = () => { state.outcome = state.outcome === b.dataset.outcome ? "" : b.dataset.outcome; load(false); };
        });
        const clear = $("actClearTile"); if (clear) clear.onclick = () => { state.outcome = ""; load(false); };
        const moreBtn = $("actMore"); if (moreBtn) moreBtn.onclick = () => load(true);
        body.querySelectorAll(".act-row").forEach((tr) => {
            const open = () => openDetail(Number(tr.dataset.id));
            tr.onclick = open;
            tr.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
        });
    }

    // ------------------------------------------------------------ one entry
    function detailHtml(d) {
        const ev = d.event, det = (ev.detail && typeof ev.detail === "object") ? ev.detail : {};
        const fact = (label, value) => value ? `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>` : "";
        const took = ev.ms != null ? (ev.ms >= 1000 ? (ev.ms / 1000).toFixed(1) + " s" : ev.ms + " ms") : "";
        const trail = (d.trail || []).map((t) => `<li class="${t.id === ev.id ? "is-this" : ""}">`
            + `<time>${esc(when(t.ts))}</time><span class="act-src">${esc(SOURCE[t.source] || t.source)}</span>`
            + `<span class="act-trail-what">${t.id === ev.id ? "<b>This: </b>" : ""}${esc(t.summary || t.action)}`
            + (t.message && t.outcome !== "ok" ? `<small>${esc(clip(t.message, 160))}</small>` : "") + `</span>`
            + `${badge(t.outcome)}</li>`).join("");
        const tech = [];
        tech.push(`<div class="act-tech-row"><span>Function</span><code>${esc(ev.action)}</code></div>`);
        if (ev.call_id) tech.push(`<div class="act-tech-row"><span>Call</span><code>${esc(ev.call_id)}</code></div>`);
        if (det.exception) tech.push(`<div class="act-tech-row"><span>Exception</span><code>${esc(det.exception)}${det.swallowed ? " (caught inside the app)" : ""}</code></div>`);
        if (det.waited_ms) tech.push(`<div class="act-tech-row"><span>Waited</span><code>${esc(det.waited_ms)} ms for another call</code></div>`);
        if (Array.isArray(det.traceback)) tech.push(`<pre class="act-pre">${esc(det.traceback.join("\n"))}</pre>`);
        const rest = Object.assign({}, det);
        ["exception", "traceback", "swallowed", "crashed", "waited_ms"].forEach((k) => delete rest[k]);
        if (Object.keys(rest).length) tech.push(`<pre class="act-pre">${esc(JSON.stringify(rest, null, 2))}</pre>`);
        return `<div class="act-detail">`
            + `<div class="act-fault">${badge(ev.outcome)}<span>${esc(fault(ev))}</span></div>`
            + `<dl class="act-facts">`
            + fact("When", new Date(ev.ts * 1000).toLocaleString())
            + fact("Who", ev.person || "This PC (desktop)") + fact("PC", ev.pc)
            + fact("Company", COMPANY[ev.company] || ev.company) + fact("Took", took) + fact("Document", ev.ref)
            + `</dl>`
            + (ev.message ? `<div class="act-message">${esc(ev.message)}</div>` : "")
            + `<h4>What ${esc(ev.person || "they")} did just before</h4>`
            + (trail ? `<ol class="act-trail">${trail}</ol>` : `<p class="act-none">Nothing else in the five minutes before.</p>`)
            + `<details class="act-tech"><summary>Technical details</summary>${tech.join("")}</details>`
            + `</div>`;
    }

    async function openDetail(id) {
        let d = null;
        try { d = await api().activity_detail(id); } catch (e) { d = { ok: false, error: (e && e.message) || String(e) }; }
        if (!d || !d.ok) { if (window.toast) window.toast((d && d.error) || "Could not open that entry.", true); return; }
        const ev = d.event;
        window.Popup.open({
            title: ev.summary || ev.action,
            subtitle: `${RESULT[ev.outcome] ? RESULT[ev.outcome].word : ev.outcome} - ${new Date(ev.ts * 1000).toLocaleString()}`,
            size: "xl", html: true, body: detailHtml(d), className: "act-popup",
            actions: [{ label: "Close", value: "close", cls: "primary", primary: true }],
        });
    }

    // ------------------------------------------------------------ report a problem
    async function reportProblem() {
        const h = window.Popup.open({
            title: "Report a problem",
            subtitle: "Say what went wrong. What you did just before is sent with it.",
            size: "md", html: true,
            body: `<label class="act-report-label" for="actReportText">What went wrong?</label>`
                + `<textarea id="actReportText" class="act-report-text" rows="5" maxlength="2000"`
                + ` placeholder="For example: the total on IV-0003 did not change after I edited a line."></textarea>`
                + `<p class="act-report-hint" id="actReportHint" hidden>Write a few words first.</p>`,
            actions: [{ label: "Cancel", value: "cancel", cls: "ghost" },
                      { label: "Send", value: "send", cls: "primary", primary: true }],
            onOpen: (hh) => { const t = hh.panel.querySelector("#actReportText"); if (t) t.focus(); },
            beforeClose: (value, hh) => {
                if (value !== "send") return true;
                const t = hh.panel.querySelector("#actReportText");
                const ok = !!(t && t.value.trim());
                const hint = hh.panel.querySelector("#actReportHint");
                if (hint) hint.hidden = ok;
                if (!ok && t) t.focus();
                return ok;
            },
        });
        const value = await h.result;
        if (value !== "send") return;
        const text = (h.panel.querySelector("#actReportText") || {}).value || "";
        const sent = window.ActivityCapture && window.ActivityCapture.report(text);
        // No full stops: the toast is right-to-left for Arabic, and a trailing
        // "." in English text lands at the start (".Sent - ...").
        if (window.toast) window.toast(sent ? "Sent - with what you did just before" : "Could not send the report", !sent);
    }
    window.reportProblem = reportProblem;

    // ------------------------------------------------------------ the dashboard's two buttons
    async function wireButtons() {
        const rep = $("dashReportBtn");
        if (rep) rep.onclick = reportProblem;
        const btn = $("dashActivityBtn");
        if (!btn) return;
        btn.onclick = showActivity;
        try {
            const r = await api().activity_can_view();
            btn.hidden = !(r && r.can);              // the admin only
        } catch (e) { btn.hidden = true; }
    }
    if (api()) wireButtons();
    else window.addEventListener("pywebviewready", wireButtons);

    // for tests
    window.ActivityView = { fault, filters, rowHtml, detailHtml, RESULT, state };
})();
