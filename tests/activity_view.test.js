/* The Activity log screen and "Report a problem" (2026-10-23):
   static/activity.js, RUN here against a stand-in page, plus the page and
   style wiring it needs.

   What must hold: everything shown is escaped (log text comes from people,
   documents and files - a client named <img onerror> must stay text); each
   result says in words whose fault it was; the default view is problems AND
   refusals, "every step" asks for the routine rows too, and the tiles count
   by outcome without the table's narrowing; the screen is one of the app's
   views (so leaving it hides it); its tiles undo the global button box (it
   squashed them into 40px pills once); the Activity button starts hidden
   (admin only), Report a problem does not. */
const fs = require("fs");
const vm = require("vm");

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const src = fs.readFileSync("static/activity.js", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const app = fs.readFileSync("static/app.js", "utf8");

const listeners = {};
const ctx = {
  console, Date, JSON, Object, Number, String, Promise, setTimeout, clearTimeout,
  document: { getElementById: () => null, querySelectorAll: () => [] },
  window: { addEventListener: (t, f) => { listeners[t] = f; } },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const V = ctx.window.ActivityView;

check("everything shown is escaped", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const row = V.rowHtml({ id: 7, ts: Date.now() / 1000, person: evil, pc: evil + " (10.0.0.1)", source: "call",
                          action: "save_inv", summary: evil, outcome: "refused", message: evil, ref: evil });
  assert(!row.includes("<img"), "a row let markup through: " + row);
  const det = V.detailHtml({ event: { id: 7, ts: Date.now() / 1000, person: evil, pc: evil, company: evil,
                                      source: "call", action: evil, summary: evil, outcome: "app_error",
                                      message: evil, ref: evil, call_id: evil,
                                      detail: { exception: evil, traceback: [evil], args: [evil] } },
                             trail: [{ id: 7, ts: Date.now() / 1000, source: "screen", action: evil, summary: evil,
                                       outcome: "warning", message: evil }] });
  assert(!det.includes("<img"), "the detail let markup through");
});

check("each result says in words whose fault it was", () => {
  const expect = { app_error: /not ahmed's fault/, server_error: /server's machine/, network: /could not reach the server/,
                   refused: /said no on purpose/, warning: /was shown this error/, report: /reported this/ };
  for (const [outcome, re] of Object.entries(expect)) {
    assert(re.test(V.fault({ outcome, person: "ahmed" })), outcome + ": " + V.fault({ outcome, person: "ahmed" }));
  }
  for (const o of Object.keys(V.RESULT)) assert(V.RESULT[o].word, o + " has no word");
});

check("problems view includes refusals; every step includes routine rows", () => {
  V.state.show = "problems"; V.state.outcome = ""; V.state.period = "all";
  let f = V.filters(0);
  assert(JSON.stringify(f.outcomes.slice().sort()) === JSON.stringify(
    ["app_error", "network", "refused", "report", "server_error", "warning"]), "problems: " + JSON.stringify(f.outcomes));
  assert(!f.include_routine, "problems should not ask for routine rows");
  V.state.show = "steps";
  f = V.filters(0);
  assert(f.include_routine && !f.outcomes, "every step: " + JSON.stringify(f));
  V.state.show = "problems"; V.state.outcome = "network";
  f = V.filters(42);
  assert(JSON.stringify(f.outcomes) === '["network"]' && f.before_id === 42, "a tile narrows to one outcome: " + JSON.stringify(f));
  V.state.outcome = "";
});

check("the screen is one of the app's views", () => {
  assert(/<main id="activityView" class="view /.test(html), "no activityView main");
  assert(/"todoView", "activityView"\]/.test(app), "ALL_VIEWS does not hide it");
  assert(html.includes('<script src="../static/activity.js"></script>'), "activity.js is not loaded");
});

check("its tiles undo the global button box", () => {
  const m = css.match(/\.in-kpi\.act-kpi\s*\{([^}]*)\}/);
  assert(m && /height:\s*auto/.test(m[1]) && /display:\s*block/.test(m[1]) && /white-space:\s*normal/.test(m[1]),
         "the tiles squash into 40px pills without height:auto / display:block / white-space:normal");
});

check("Activity log starts hidden (admin only); Report a problem does not", () => {
  assert(/<button[^>]*id="dashActivityBtn"[^>]*\shidden>/.test(html), "the Activity button is visible before the role is known");
  const rep = html.match(/<button[^>]*id="dashReportBtn"[^>]*>/);
  assert(rep && !/\shidden[\s>]/.test(rep[0]), "Report a problem is hidden");
});

if (failures) { console.log(`\n${failures} activity view check(s) FAILED`); process.exit(1); }
console.log("\nall activity view checks passed");
