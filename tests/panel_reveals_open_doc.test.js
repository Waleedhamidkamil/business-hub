/* Opening a document puts the right panel on ITS month (2026-10-01).

   Waleed clicked "Open document" on a July document from Needs Attention while
   the app was in September. The editor loaded the right document; the panel
   stayed on September, so the thing he was editing was not on the list beside
   it and he had to go and find it.

   Two halves, in two places, tested here together:
     * `revealDocInPanel(kind, no, date)` in app.js owns the MONTH - it owns the
       month/year selects (`PANEL_MONTH_INPUTS` / `showPendingInPanel`),
     * `OpenPanel.revealRow(no)` in open_panel.js owns the SCROLL - it owns the
       panel's scroll container.

   The month half is driven for real here (the function is extracted from
   app.js and run against a stub DOM). The wiring half - every loader calling
   it - is asserted by reading the four loaders, because a correct function
   nobody calls is the failure mode that caused this bug in the first place.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const app = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

/* ---------- extract the real month-switching code ---------- */
const start = app.indexOf("const PANEL_MONTH_INPUTS = {");
assert(start >= 0, "PANEL_MONTH_INPUTS not found in static/app.js");
const end = app.indexOf("window.panelNewDocument = panelNewDocument;", start);
assert(end > start, "panelNewDocument is not exported from static/app.js");
const src = app.slice(start, end);
assert(/displayToIso/.test(src),
  "revealDocInPanel should parse the display date through displayToIso");

// displayToIso is defined far earlier in app.js; take the real one.
const isoStart = app.indexOf("function displayToIso(disp) {");
const isoSrc = app.slice(isoStart, app.indexOf("\n}\n", isoStart) + 3);

/* The real __currentEditorDoc accessor, also from app.js. It has to be the real
   one: the whole point of section 12 is that assigning null goes through this
   setter and reaches OpenPanel.clearActive. A plain property on a stub window
   would make that test vacuous. */
const propAt = app.indexOf('Object.defineProperty(window, "__currentEditorDoc"');
assert(propAt > 0, "the __currentEditorDoc accessor is gone from static/app.js");
const docPropSrc = app.slice(app.lastIndexOf("(function () {", propAt),
                             app.indexOf("})();", propAt) + 5);
assert(/clearActive/.test(docPropSrc),
  "the accessor should route a null through OpenPanel.clearActive");

function harness() {
  const selects = {};
  for (const id of ["openYear", "openMonth", "qOpenYear", "qOpenMonth",
                    "dnOpenYear", "dnOpenMonth", "invOpenYear", "invOpenMonth"]) {
    selects[id] = { value: "" };
  }
  const env = {
    refreshes: [],
    revealed: [],
    document: { getElementById: (id) => selects[id] || null },
    selects,
  };
  const ctx = {
    document: env.document,
    state: { today: { year: 2026, month: 9, display: "29/9/2026" } },
    window: {
      cleared: 0,
      refreshPoList:    async () => { env.refreshes.push("po"); },
      refreshQuoteList: async () => { env.refreshes.push("quote"); },
      refreshDnList:    async () => { env.refreshes.push("dn"); },
      refreshInvList:   async () => { env.refreshes.push("inv"); },
      OpenPanel: {
        revealRow: (no) => env.revealed.push(no),
        markActive: () => {},
        clearActive: () => { ctx.window.cleared += 1; },
      },
    },
  };
  ctx.window.state = ctx.state;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  for (const part of [docPropSrc, isoSrc, src]) vm.runInContext(part, ctx);
  env.reveal = (kind, no, date) =>
    vm.runInContext("revealDocInPanel", ctx)(kind, no, date);
  env.newDoc = (kind) => vm.runInContext("panelNewDocument", ctx)(kind);
  env.ctx = ctx;
  return env;
}

(async () => {
  /* ---------- 1. the reported case: a July document opened in September ---------- */
  {
    const env = harness();
    env.selects.openYear.value = "2026";
    env.selects.openMonth.value = "9";
    await env.reveal("po", "A-77", "14/7/2026");
    assert(env.selects.openMonth.value === "7",
      `the panel month should move to July, got ${env.selects.openMonth.value} - `
      + "this is exactly what Waleed reported");
    assert(env.selects.openYear.value === "2026",
      `the year should be the document's, got ${env.selects.openYear.value}`);
    assert(env.refreshes.join() === "po",
      `the PO list must be re-fetched for the new month, got [${env.refreshes}]`);
    assert(env.revealed.join() === "A-77",
      `the row must be scrolled into view, got [${env.revealed}]`);
  }

  /* ---------- 2. a document from another YEAR ---------- */
  {
    const env = harness();
    env.selects.openYear.value = "2026";
    env.selects.openMonth.value = "2";
    await env.reveal("po", "A-9", "30/12/2025");
    assert(env.selects.openYear.value === "2025" && env.selects.openMonth.value === "12",
      `December 2025 expected, got ${env.selects.openMonth.value}/${env.selects.openYear.value}`);
  }

  /* ---------- 3. already on the right month: do NOT re-fetch ---------- */
  {
    const env = harness();
    env.selects.openYear.value = "2026";
    env.selects.openMonth.value = "9";
    await env.reveal("po", "A-100", "29/9/2026");
    assert(env.refreshes.length === 0,
      "the list must not be re-fetched when the panel is already on that month - "
      + "clicking a row in the panel would reload the list under the click");
    assert(env.revealed.join() === "A-100",
      "the row must still be highlighted and scrolled to");
  }

  /* ---------- 4. each editor drives its OWN selects ---------- */
  {
    const cases = [
      ["quote", "qOpenYear", "qOpenMonth", "quote"],
      ["dn", "dnOpenYear", "dnOpenMonth", "dn"],
      ["inv", "invOpenYear", "invOpenMonth", "inv"],
    ];
    for (const [kind, yId, mId, refresh] of cases) {
      const env = harness();
      env.selects[yId].value = "2026";
      env.selects[mId].value = "9";
      await env.reveal(kind, "X-1", "3/5/2026");
      assert(env.selects[mId].value === "5",
        `${kind}: ${mId} should be May, got ${env.selects[mId].value}`);
      assert(env.refreshes.join() === refresh,
        `${kind}: expected the ${refresh} list to refresh, got [${env.refreshes}]`);
      assert(env.selects.openMonth.value === "",
        `${kind}: it must not touch the PO editor's month select`);
    }
  }

  /* ---------- 5. a date we cannot read leaves the month alone ---------- */
  {
    for (const bad of ["", null, undefined, "-", "N/A", "last July"]) {
      const env = harness();
      env.selects.openYear.value = "2026";
      env.selects.openMonth.value = "9";
      await env.reveal("po", "A-5", bad);
      assert(env.selects.openMonth.value === "9",
        `an unreadable date (${JSON.stringify(bad)}) must not move the month - a `
        + "wrong month switch is worse than none");
      assert(env.revealed.join() === "A-5",
        "it should still try to highlight the row");
    }
  }

  /* ---------- 6. an ISO date works too ---------- */
  {
    const env = harness();
    env.selects.openYear.value = "2026";
    env.selects.openMonth.value = "9";
    await env.reveal("po", "A-6", "2026-07-14");
    assert(env.selects.openMonth.value === "7",
      `an ISO date should also be understood, got ${env.selects.openMonth.value}`);
  }

  /* ---------- 7. an unknown kind, and no document number, do nothing ---------- */
  {
    const env = harness();
    env.selects.openMonth.value = "9";
    await env.reveal("lpo", "CSSP-1", "14/7/2026");
    await env.reveal("po", "", "14/7/2026");
    assert(env.refreshes.length === 0 && env.revealed.length === 0,
      "an unknown doc kind or a missing number must be a quiet no-op");
    assert(env.selects.openMonth.value === "9", "and must not move the month");
  }

  /* ---------- 8. every loader calls it ---------- */
  {
    const loaders = [
      ["static/app.js", "loadForEdit", 'revealDocInPanel("po"'],
      ["static/quote.js", "loadQuoteForEdit", 'revealDocInPanel("quote"'],
      ["static/dn.js", "loadDnForEdit", 'revealDocInPanel("dn"'],
      ["static/inv.js", "loadInvForEdit", 'revealDocInPanel("inv"'],
    ];
    for (const [file, fn, call] of loaders) {
      const js = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
      const at = js.indexOf(`async function ${fn}(`);
      assert(at >= 0, `${fn} not found in ${file}`);
      const body = js.slice(at, js.indexOf("\n}\n", at));
      assert(body.includes(call),
        `${fn} in ${file} must call ${call}...) - the month jump has to work from `
        + "every way in (Needs Attention, dashboard search, item usage), not just "
        + "the one button that was reported");
    }
  }

  /* ---------- 9. the scroll half exists and is published ---------- */
  {
    const panel = fs.readFileSync("static/open_panel.js", "utf8").replace(/\r\n/g, "\n");
    assert(/function revealRow\(/.test(panel),
      "static/open_panel.js should own the scroll (revealRow)");
    assert(/revealRow,/.test(panel.slice(panel.indexOf("window.OpenPanel = {"))),
      "revealRow must be published on window.OpenPanel, or app.js cannot call it");
    const body = panel.slice(panel.indexOf("function revealRow("),
                             panel.indexOf("window.OpenPanel = {"));
    assert(/requestAnimationFrame/.test(body),
      "revealRow must wait for the list to render rather than assume it is there - "
      + "the month refresh that precedes it is async");
    assert(/op-pane-list/.test(body),
      "it must scroll the panel's own scroller (.op-pane-list); scrollIntoView on "
      + "a nested scroller also moves the page behind the panel");
    assert(/highlightActivePanelRow\(\)/.test(body),
      "revealRow should re-apply the is-open highlight once the row exists");
  }

  /* ---------- 10. the highlight is actually visible ---------- */
  {
    // 3.1 in the same report: the old highlight was a 9% tint, which is not a
    // visible step. Guard the floor so a future tidy-up cannot quietly soften
    // it back, and guard the WORD, because Waleed is colour blind and colour
    // alone is never the cue in this app.
    const css = fs.readFileSync("static/app_styles.css", "utf8");
    const at = css.indexOf(".op-pane .po-row.is-open {");
    assert(at >= 0, ".op-pane .po-row.is-open rule not found");
    const rule = css.slice(at, css.indexOf("}", at));
    const mix = /var\(--navy[^)]*\)\s+(\d+)%/.exec(rule);
    assert(mix, "the open row should tint with a percentage of --navy, got: " + rule);
    assert(+mix[1] >= 16,
      `the open-row tint is ${mix[1]}% of --navy; it was 9% and Waleed could not `
      + "see it. Keep it at 16% or more.");
    const rail = /inset\s+(-?\d+)px\s+0\s+0/.exec(rule);
    assert(rail && Math.abs(+rail[1]) >= 4,
      "the left rail should be at least 4px - it was 3px and read as a hairline");
    assert(/inset 0 0 0 1px/.test(rule),
      "the open row should also carry a full ring, so it reads as one selected "
      + "block rather than a slightly different stripe");
    assert(/content:\s*"OPEN"/.test(css),
      "the OPEN pill is the non-colour cue and must stay - colour is never the "
      + "only signal in this app");
  }

  /* ---------- 11. New puts the panel back on TODAY's month ---------- */
  {
    // Waleed, minutes after the July fix: he opened a July order, pressed New,
    // and the panel still showed July with the July row highlighted while the
    // editor held a brand-new document. A new document is dated today, so the
    // panel belongs on today's month.
    const env = harness();
    env.selects.openYear.value = "2026";
    env.selects.openMonth.value = "7";          // left there by opening a July PO
    await env.newDoc("po");
    assert(env.selects.openMonth.value === "9" && env.selects.openYear.value === "2026",
      `New should put the panel on today's month (9/2026), got `
      + `${env.selects.openMonth.value}/${env.selects.openYear.value}`);
    assert(env.refreshes.join() === "po", `the list must reload, got [${env.refreshes}]`);
    assert(env.ctx.window.__currentEditorDoc == null,
      "a fresh document means no document is open");
  }

  /* ---------- 12. New clears the panel highlight, latch and all ---------- */
  {
    const env = harness();
    await env.newDoc("po");
    assert(env.ctx.window.cleared === 1,
      "setting __currentEditorDoc to null must reach OpenPanel.clearActive, not "
      + "markActive - the panel remembers the last row you clicked and that "
      + "memory used to keep the old document highlighted beside an empty editor");
    assert(env.revealed.length === 0, "New has no row to scroll to");
  }

  /* ---------- 13. each editor's New drives its own selects ---------- */
  {
    for (const [kind, mId, refresh] of [["quote", "qOpenMonth", "quote"],
                                        ["dn", "dnOpenMonth", "dn"],
                                        ["inv", "invOpenMonth", "inv"]]) {
      const env = harness();
      env.selects[mId].value = "7";
      await env.newDoc(kind);
      assert(env.selects[mId].value === "9",
        `${kind}: New should move ${mId} to today's month, got ${env.selects[mId].value}`);
      assert(env.refreshes.join() === refresh,
        `${kind}: expected the ${refresh} list to reload, got [${env.refreshes}]`);
    }
  }

  /* ---------- 14. the null -> clearActive wiring is real, in app.js ---------- */
  {
    const setter = app.slice(app.indexOf('Object.defineProperty(window, "__currentEditorDoc"'),
                             app.indexOf("window.signBadgeHTML"));
    assert(/v == null/.test(setter) && /clearActive\(\)/.test(setter),
      "the __currentEditorDoc accessor must route null to OpenPanel.clearActive(); "
      + "without it the panel's last-clicked latch survives New");
    const panel = fs.readFileSync("static/open_panel.js", "utf8");
    const fn = panel.slice(panel.indexOf("function clearActivePanelRow()"));
    assert(/_openDocNo = null/.test(fn.slice(0, 200)),
      "clearActivePanelRow must null _openDocNo - clearing the class alone leaves "
      + "the latch to re-apply it on the next list re-render");
    assert(/clearActive:/.test(panel.slice(panel.indexOf("window.OpenPanel = {"))),
      "clearActive must be published on window.OpenPanel");
    // Every editor's New goes through the one helper.
    for (const [file, kind] of [["static/app.js", "po"], ["static/quote.js", "quote"],
                                ["static/dn.js", "dn"], ["static/inv.js", "inv"]]) {
      const js = fs.readFileSync(file, "utf8");
      assert(js.includes(`panelNewDocument("${kind}")`),
        `${file} must reset the panel through panelNewDocument("${kind}") so all `
        + "four editors behave the same on New");
    }
  }

  console.log("Panel reveals the open document tests passed");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
