/* Activity capture (2026-10-23) - what the person did and saw on this screen,
   for the activity and problems log (backend/activity_log.py, Waleed: "know
   what broke and what happened before it").

   Records, in order:
     view      a screen was opened (which of the app's views became visible)
     click     a button was pressed (its label - never anything typed)
     toast     a message was shown, and whether it was an error
     js_error  an error in the screen itself (uncaught, or a failed request
               nothing caught)
     report    the person used "report a problem"

   They go to the server in small batches: every 15 s, at once after an error
   or a report, and when the page goes away. Logging never slows or breaks the
   page: every step is guarded, and a batch that cannot be sent is kept (the
   newest 200) for the next try. Loaded before the app's own scripts, so an
   error while they start is caught too. */
(function () {
    "use strict";
    const MAX = 200;
    const SOON = 300, LATER = 15000;
    const queue = [];
    let timer = null;
    let sending = false;

    const VIEW_NAMES = {
        hubView: "Dashboard", editorView: "Purchase order editor", openView: "Purchase orders",
        quoteHubView: "Quotations", quoteView: "Quotation editor", quoteOpenView: "Quotation list",
        dnView: "Delivery note editor", dnOpenView: "Delivery notes",
        invHubView: "Invoices", invView: "Invoice editor", invOpenView: "Invoice list",
        settingsView: "Settings", dataManagerView: "Data manager", analyticsView: "Insights",
        lpoView: "Client LPOs", lpoDocView: "Client LPO", itemMasterView: "Master data - items",
        clientMasterView: "Master data - clients", todoView: "To-do", activityView: "Activity log",
    };
    // Browser chatter that is not a problem in the app.
    const NOT_PROBLEMS = /ResizeObserver loop|Script error\.?$/;

    function api() { return window.pywebview && window.pywebview.api; }
    function clip(s, n) {
        s = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
        return s.length > n ? s.slice(0, n - 3) + "..." : s;
    }

    function push(ev) {
        try {
            ev.t = Date.now();
            queue.push(ev);
            if (queue.length > MAX) queue.splice(0, queue.length - MAX);
            const urgent = ev.kind === "js_error" || ev.kind === "report" || ev.outcome === "warning";
            schedule(urgent ? SOON : LATER);
        } catch (e) { /* never break the page */ }
    }

    function schedule(ms) {
        if (timer !== null) {
            if (ms >= LATER) return;        // a send is already coming
            clearTimeout(timer);
        }
        timer = setTimeout(send, ms);
    }

    async function send() {
        timer = null;
        const a = api();
        if (!queue.length) return;
        if (sending || !a || typeof a.log_client_events !== "function") { schedule(LATER); return; }
        sending = true;
        const batch = queue.splice(0, queue.length);
        try {
            const r = await a.log_client_events({ sent: Date.now(), events: batch });
            if (!r || !r.ok) throw new Error("not stored");
        } catch (e) {
            queue.unshift(...batch);           // keep them for the next try
            if (queue.length > MAX) queue.splice(0, queue.length - MAX);
            schedule(LATER);
        } finally {
            sending = false;
        }
    }

    // ---- screens opened
    function watchViews() {
        Object.keys(VIEW_NAMES).forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            new MutationObserver(() => {
                if (!el.hidden) push({ kind: "view", action: "view:" + id, summary: "Opened " + VIEW_NAMES[id] });
            }).observe(el, { attributes: true, attributeFilter: ["hidden"] });
        });
    }

    // ---- messages shown
    function watchToast() {
        const el = document.getElementById("toast");
        if (!el) return;
        let last = "", lastAt = 0;
        new MutationObserver(() => {
            if (el.hidden) return;
            const text = clip(el.textContent, 500);
            if (!text || (text === last && Date.now() - lastAt < 1500)) return;
            last = text; lastAt = Date.now();
            const isError = el.classList.contains("error");
            push({ kind: "toast", action: "message", outcome: isError ? "warning" : "info",
                   summary: isError ? "Was shown an error" : "Was shown a message", message: text });
        }).observe(el, { childList: true, characterData: true, subtree: true,
                         attributes: true, attributeFilter: ["class", "hidden"] });
    }

    // ---- buttons pressed (the label only)
    function onClick(e) {
        try {
            const t = e.target;
            const b = t && t.closest ? t.closest("button, a, [role=button], [data-act]") : null;
            if (!b) return;
            const label = clip(b.getAttribute("aria-label") || b.getAttribute("title") || b.textContent ||
                               b.id || (b.dataset && b.dataset.act), 60);
            if (!label) return;
            push({ kind: "click", action: "click", summary: label,
                   detail: { id: b.id || "", act: (b.dataset && b.dataset.act) || "" } });
        } catch (err) { /* never break the page */ }
    }

    // ---- errors in the screen itself
    function onError(e) {
        const message = clip(e && e.message, 500);
        if (!message || NOT_PROBLEMS.test(message)) return;
        const file = String((e && e.filename) || "").split("/").pop();
        push({ kind: "js_error", action: "screen_error", summary: "Error in the screen", message,
               detail: { where: file ? `${file}:${e.lineno}:${e.colno}` : "",
                         stack: clip(e && e.error && e.error.stack, 1500) } });
    }
    function onRejection(e) {
        const r = e && e.reason;
        const message = clip((r && r.message) || r, 500);
        if (!message || NOT_PROBLEMS.test(message)) return;
        push({ kind: "js_error", action: "screen_error",
               summary: "A request failed and the screen did not handle it", message,
               detail: { stack: clip(r && r.stack, 1500) } });
    }

    // ---- "report a problem"
    function report(text) {
        const message = clip(text, 2000);
        if (!message) return false;
        push({ kind: "report", action: "report", outcome: "report", summary: "Reported a problem", message });
        return true;
    }

    function start() {
        try { watchViews(); watchToast(); } catch (e) { /* the page still works */ }
    }

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    window.addEventListener("pywebviewready", () => schedule(SOON));
    window.addEventListener("pagehide", () => { send(); });
    document.addEventListener("click", onClick, true);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();

    window.ActivityCapture = { push, send, report, pending: () => queue.slice(), VIEW_NAMES };
})();
