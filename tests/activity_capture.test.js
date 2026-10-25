/* The screen half of the activity and problems log (2026-10-23):
   static/activity_capture.js, RUN here against a stand-in page.

   What must hold: a screen error is recorded and sent at once (with where it
   happened); browser chatter is not a problem; screens opened are named;
   error messages shown are warnings, and a repeat is not double-counted; a
   click records the button's label, never what was typed; a batch the server
   did not take is kept and sent later, the newest 200 at most; "report a
   problem" goes at once; and the script loads before every other one. */
const fs = require("fs");
const vm = require("vm");

let failures = 0;
const results = [];
async function check(name, fn) {
  try { await fn(); results.push("  ok   " + name); }
  catch (e) { failures++; results.push("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const src = fs.readFileSync("static/activity_capture.js", "utf8");

function page() {
  const listeners = { window: {}, document: {} };
  const observers = [];
  const elements = {};
  const el = id => (elements[id] = {
    id, hidden: true, textContent: "",
    classList: { set: new Set(), contains(c) { return this.set.has(c); }, add(c) { this.set.add(c); } },
  });
  ["hubView", "invView", "toast"].forEach(el);
  const sent = [];
  let down = 0;
  const timers = [];
  const window = {
    addEventListener(t, f) { (listeners.window[t] = listeners.window[t] || []).push(f); },
    pywebview: { api: { log_client_events: async batch => {
      if (down > 0) { down--; throw new Error("server unreachable"); }
      sent.push(JSON.parse(JSON.stringify(batch)));
      return { ok: true, stored: batch.events.length };
    } } },
  };
  const document = {
    readyState: "complete",
    addEventListener(t, f) { (listeners.document[t] = listeners.document[t] || []).push(f); },
    getElementById: id => elements[id] || null,
  };
  class MutationObserver { constructor(cb) { this.cb = cb; observers.push(this); } observe(t) { this.target = t; } }
  const ctx = {
    window, document, MutationObserver, console,
    setTimeout: (f, ms) => { timers.push({ f, ms }); return timers.length; },
    clearTimeout: id => { if (timers[id - 1]) timers[id - 1].cancelled = true; },
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return {
    cap: window.ActivityCapture, elements, sent, timers, listeners,
    goDown(n) { down = n; },
    fire(where, type, ev) { (listeners[where][type] || []).forEach(f => f(ev)); },
    mutate(id) { observers.filter(o => o.target === elements[id]).forEach(o => o.cb([])); },
    pendingTimers() { return timers.filter(t => !t.cancelled && !t.ran); },
    async runTimers() {
      const due = timers.filter(t => !t.cancelled && !t.ran);
      due.forEach(t => { t.ran = true; });
      for (const t of due) await t.f();
    },
  };
}

(async () => {
  await check("a screen error is recorded and sent at once, with where it happened", async () => {
    const p = page();
    p.fire("window", "error", { message: "TypeError: rows is undefined", filename: "http://srv:8765/static/inv.js",
                                lineno: 120, colno: 9, error: { stack: "TypeError: rows is undefined\n at save" } });
    assert(p.pendingTimers().some(t => t.ms <= 500), "an error should be sent within half a second");
    await p.runTimers();
    const ev = p.sent[0] && p.sent[0].events[0];
    assert(ev && ev.kind === "js_error", "no screen error sent: " + JSON.stringify(p.sent));
    assert(ev.detail.where === "inv.js:120:9", "where: " + ev.detail.where);
    assert(typeof p.sent[0].sent === "number" && typeof ev.t === "number", "times are needed for the order");
  });

  await check("a failed request nothing caught is a screen error too", async () => {
    const p = page();
    p.fire("window", "unhandledrejection", { reason: { message: "Can't reach the office server", stack: "x" } });
    assert(p.cap.pending()[0].kind === "js_error", JSON.stringify(p.cap.pending()));
  });

  await check("browser chatter is not a problem", async () => {
    const p = page();
    p.fire("window", "error", { message: "ResizeObserver loop completed with undelivered notifications." });
    p.fire("window", "error", { message: "Script error." });
    assert(p.cap.pending().length === 0, JSON.stringify(p.cap.pending()));
  });

  await check("screens opened are named, closing one is not an event", async () => {
    const p = page();
    p.elements.invView.hidden = false; p.mutate("invView");
    p.elements.invView.hidden = true; p.mutate("invView");
    const views = p.cap.pending().filter(e => e.kind === "view");
    assert(views.length === 1 && views[0].summary === "Opened Invoice editor", JSON.stringify(views));
  });

  await check("an error message shown is a warning, and a repeat is counted once", async () => {
    const p = page();
    const t = p.elements.toast;
    t.textContent = "Pick a client first."; t.classList.add("error"); t.hidden = false;
    p.mutate("toast"); p.mutate("toast");
    const toasts = p.cap.pending().filter(e => e.kind === "toast");
    assert(toasts.length === 1, "counted " + toasts.length + " times");
    assert(toasts[0].outcome === "warning" && toasts[0].message === "Pick a client first.", JSON.stringify(toasts[0]));
  });

  await check("a click records the button's label, never what was typed", async () => {
    const p = page();
    const button = { id: "btnSaveInv", textContent: " Save ", dataset: {},
                     getAttribute: a => (a === "aria-label" ? "Save invoice" : null) };
    p.fire("document", "click", { target: { closest: () => button } });
    p.fire("document", "click", { target: { closest: () => null, value: "secret typed text" } });
    const clicks = p.cap.pending().filter(e => e.kind === "click");
    assert(clicks.length === 1 && clicks[0].summary === "Save invoice", JSON.stringify(clicks));
    assert(!JSON.stringify(p.cap.pending()).includes("secret typed text"), "typed text was recorded");
  });

  await check("a batch the server did not take is kept and sent later", async () => {
    const p = page();
    p.goDown(1);
    p.cap.report("The total on IV-0003 looks wrong");
    await p.runTimers();
    assert(p.sent.length === 0 && p.cap.pending().length === 1, "the report was lost while the server was down");
    await p.runTimers();
    assert(p.sent.length === 1 && p.sent[0].events[0].kind === "report", JSON.stringify(p.sent));
    assert(p.cap.pending().length === 0, "sent but still waiting");
  });

  await check("never more than 200 waiting, and the newest are kept", async () => {
    const p = page();
    for (let i = 0; i < 250; i++) p.cap.push({ kind: "click", action: "click", summary: "b" + i });
    const pending = p.cap.pending();
    assert(pending.length === 200, "waiting: " + pending.length);
    assert(pending[199].summary === "b249" && pending[0].summary === "b50", pending[0].summary);
  });

  await check("index.html loads it before every other script", async () => {
    const html = fs.readFileSync("templates/index.html", "utf8");
    const first = html.match(/<script src="([^"]+)"/);
    assert(first && first[1].endsWith("activity_capture.js"), "first script: " + (first && first[1]));
  });

  console.log(results.join("\n"));
  if (failures) { console.log(`\n${failures} activity capture check(s) FAILED`); process.exit(1); }
  console.log("\nall activity capture checks passed");
})();
