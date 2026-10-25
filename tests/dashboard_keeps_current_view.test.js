/* Returning to the hub keeps what is already current (2026-10-01).

   Waleed: every trip back to the hub meant about two seconds of loading. The
   hub used to blank all three sections to skeletons and rebuild every chart
   on each return, even when he had only opened a document and looked at it.

   Now `renderDashboard` asks the backend for `dashboard_state_token` first -
   a value that moves on ANY database commit, a settings change or a new day -
   and:
     * same token  -> nothing changed: no data calls, no redraw at all,
     * new token   -> fetch, and swap the new data in WITHOUT blanking to
                      skeletons first; a section whose data is identical is
                      not redrawn (no replayed animation),
     * any failure -> the token is not remembered, so the next visit fetches.

   This runs the REAL renderDashboard, renderDashboardLoading and
   renderDashboardError from static/app.js against a fake bridge that counts
   every call, so "no data calls" is measured, not asserted from a comment.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const app = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

function slice(startMarker, endMarker) {
  const a = app.indexOf(startMarker);
  assert(a >= 0, "not found in static/app.js: " + startMarker);
  const b = app.indexOf(endMarker, a);
  assert(b > a, "end not found after: " + startMarker);
  return app.slice(a, b);
}

// The state block (tokens + drawn signatures), the two helpers that reset it,
// and renderDashboard itself - all real code.
const stateSrc = slice("let _dashShownToken = null;", "\nconst DASH_BRIDGE_TIMEOUT_MS");
const loadingSrc = slice("function renderDashboardLoading() {", "\nfunction renderDashboardError(");
const errorSrc = slice("function renderDashboardError(section) {", "\nasync function buildDashboardSearchIndex");
const renderSrc = slice("async function renderDashboard(", "\nwindow.renderDashboard = renderDashboard");

function harness() {
  const env = {
    token: "T1",
    calls: [],            // every data call the dashboard made
    tokenCalls: 0,
    skeletons: 0,
    drawn: { attention: 0, bid: 0, stats: 0 },
    fail: new Set(),      // which data calls should fail
    attention: { decide: [{ doc_no: "Q-1" }] },
    quotes: [{ quote_no: "Q-1", type_code: "MT" }],
  };
  const el = () => ({ innerHTML: "", textContent: "", hidden: false, set onclick(v) {} });
  const ok = (name, value) => {
    env.calls.push(name);
    return Promise.resolve(env.fail.has(name) ? { ok: false, error: "boom" } : value);
  };
  const bridge = {
    dashboard_state_token: () => { env.tokenCalls++; return Promise.resolve({ ok: true, token: env.token }); },
    dashboard_needs_attention: () => ok("attention", env.attention),
    list_quotes_for_month: () => ok("quotes", env.quotes),
    list_invoices_for_month: () => ok("invoices", []),
    list_dns_for_month: () => ok("dns", []),
    list_pos_for_month: () => ok("pos", []),
    dashboard_analytics: () => ok("analytics", { daily: {} }),
    costing_summary: () => ok("costing", { ok: true, bids: { won: 1, open: 2, lost: 0, win_rate: 50 }, by_type: [] }),
  };
  const ctx = {
    console,
    JSON, Math, Number, Object, Array, String, Promise, Date,
    setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
    document: {
      getElementById: () => el(),
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    window: {},
    api: () => bridge,
    // Real bridge wrapper semantics, minus the timeout: {ok, value}.
    dashboardBridgeCall: async (label, factory) => {
      try {
        const value = await factory();
        if (value && value.ok === false) throw new Error(value.error);
        return { ok: true, value };
      } catch (e) { return { ok: false, value: null, error: e }; }
    },
    renderHeroGreeting: () => {},
    renderNeedsAttention: () => { env.drawn.attention++; },
    renderBidDonut: () => { env.drawn.bid++; },
    renderStatsSlider: () => { env.drawn.stats++; },
    restartAutoAdvance: () => {},
    forceInsightsPaint: () => {},
    scheduleInsightsFit: () => {},
    buildDashboardSearchIndex: () => Promise.resolve(),
    DASH_SUBTYPES: [{ code: "MT", label: "Materials", color: "#000" }],
    _dashSliderTimer: null,
    _dashRenderSeq: 0,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(stateSrc + "\n" + loadingSrc + "\n" + errorSrc + "\n" + renderSrc
    + "\nthis.__render = renderDashboard;"
    + "\nthis.__state = () => ({ token: _dashShownToken, drawn: Object.assign({}, _dashDrawn) });",
    ctx);
  // Count skeleton paints by wrapping the REAL function.
  vm.runInContext("const __realLoading = renderDashboardLoading;"
    + "renderDashboardLoading = function () { this.__skeletons = (this.__skeletons || 0) + 1; return __realLoading(); };",
    ctx);
  env.render = (opts) => ctx.__render(opts);
  env.state = () => ctx.__state();
  env.skeletonCount = () => ctx.__skeletons || 0;
  return env;
}

const DATA_CALLS = ["attention", "quotes", "invoices", "dns", "pos", "analytics", "costing"];

(async () => {
  /* ---------- 1. first visit: fetch everything, skeletons first ---------- */
  const env = harness();
  await env.render();
  assert(DATA_CALLS.every(c => env.calls.includes(c)),
    "the first visit must fetch every section, got [" + env.calls + "]");
  assert(env.skeletonCount() === 1, "the first visit paints skeletons once, got " + env.skeletonCount());
  assert(env.drawn.attention === 1 && env.drawn.bid === 1 && env.drawn.stats === 1,
    "the first visit draws all three sections, got " + JSON.stringify(env.drawn));
  assert(env.state().token === "T1", "a complete render remembers its token");

  /* ---------- 2. come back, nothing changed: NO data calls at all ---------- */
  env.calls.length = 0;
  for (let i = 0; i < 5; i++) await env.render();
  assert(env.calls.length === 0,
    "returning to the hub with nothing changed made data calls: [" + env.calls + "]. "
    + "This is the two-second wait Waleed reported.");
  assert(env.tokenCalls === 6, "each visit asks for the token exactly once, got " + env.tokenCalls);
  assert(env.skeletonCount() === 1, "nothing changed must never blank the hub to skeletons");
  assert(env.drawn.attention === 1 && env.drawn.bid === 1 && env.drawn.stats === 1,
    "nothing changed must redraw nothing, got " + JSON.stringify(env.drawn));

  /* ---------- 3. something changed: fetch, swap in place ---------- */
  env.token = "T2";
  env.attention = { decide: [{ doc_no: "Q-1" }, { doc_no: "Q-2" }] };   // attention changed
  await env.render();
  assert(DATA_CALLS.every(c => env.calls.includes(c)),
    "a changed token must refetch every section, got [" + env.calls + "]");
  assert(env.skeletonCount() === 1,
    "a refresh over existing content must NOT blank to skeletons first - that is the flash");
  assert(env.drawn.attention === 2, "the changed section must be redrawn");
  assert(env.drawn.bid === 1 && env.drawn.stats === 1,
    "sections whose data is identical must not be redrawn (no replayed animation), got "
    + JSON.stringify(env.drawn));
  assert(env.state().token === "T2", "the new token is remembered");

  /* ---------- 4. a failed section is never remembered as current ---------- */
  env.token = "T3";
  env.fail.add("costing");
  env.calls.length = 0;
  await env.render();
  assert(env.state().token === null,
    "a render with a failed section must not remember the token - the next visit "
    + "would otherwise keep showing an error as if it were current");
  env.fail.clear();
  env.calls.length = 0;
  await env.render();                    // same token T3, but last render was incomplete
  assert(env.calls.includes("costing"),
    "after a failure the next visit must fetch again even with the same token");
  assert(env.state().token === "T3", "and remember it once everything succeeded");
  assert(env.drawn.bid >= 2, "the section that had failed must be redrawn from data");

  /* ---------- 5. Retry forces a fetch even when the token matches ---------- */
  env.calls.length = 0;
  await env.render({ force: true });
  assert(DATA_CALLS.every(c => env.calls.includes(c)), "force must refetch");

  /* ---------- 6. an unreadable token is never 'the same' ---------- */
  {
    const e2 = harness();
    await e2.render();
    e2.calls.length = 0;
    e2.token = "";                       // backend could not read a counter
    await e2.render();
    assert(DATA_CALLS.every(c => e2.calls.includes(c)),
      "an empty token must mean refetch, never 'unchanged'");
  }

  /* ---------- 7. the hidden slider does no work ---------- */
  {
    const tick = app.slice(app.indexOf("_dashSliderTimer = setInterval("),
                           app.indexOf("}, 7000);") + 9);
    assert(/hubView/.test(tick) && /\.hidden\) return/.test(tick),
      "the Insights slider must skip its tick while the hub is hidden - it now "
      + "survives trips to an editor and would animate charts nobody can see");
  }

  console.log("Dashboard keeps the current view tests passed");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
