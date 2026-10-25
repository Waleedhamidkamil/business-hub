/* The dashboard's mid row, as Waleed redesigned it on 2026-10-21.

   1. Needs attention fills the WHOLE left column, with ONE-LINE cards: icon,
      number, status tag, action. The client · date line went - the button says
      what the document needs, and one line per card lets the grid show twice as
      many (8 -> 16 on his 1080p screen). Client and date stay in the hover title.
   2. Insights is ONE card: bid outcomes and By Document Type are two panels
      inside it, not two cards.
   3. Quick access moved under Insights in the right column, as two compact
      buttons, so both columns end on the same line.

   Pure text assertions over the HTML, JS and CSS - no browser.
*/
const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const js = fs.readFileSync("static/app.js", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

/* the mid row's two columns, cut out of index.html */
const rowAt = html.indexOf('<div class="dash-mid-row">');
const mainAt = html.indexOf('<div class="dash-mid-main">', rowAt);
const sideAt = html.indexOf('<div class="dash-mid-side">', rowAt);
const rowEnd = html.indexOf("</main>", rowAt);
const mainCol = html.slice(mainAt, sideAt);
const sideCol = html.slice(sideAt, rowEnd);

function ruleBody(selector) {
  const at = css.indexOf(selector + " {");
  assert(at >= 0, "CSS rule not found: " + selector);
  return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
}

console.log("dashboard mid row");

check("Needs attention has the left column to itself", () => {
  assert(rowAt >= 0 && mainAt > rowAt && sideAt > mainAt, "mid row columns not found");
  assert(mainCol.includes('id="dashFocus"'), "Needs attention is not in the left column");
  assert(!mainCol.includes("dash-bottom") && !mainCol.includes("dash-qa-head"),
    "Quick access is back under Needs attention - it moved under Insights so the list can run the full height");
});

check("Insights is ONE card with two panels inside", () => {
  const cardAt = sideCol.indexOf('id="dashInsightsCard"');
  assert(cardAt >= 0, "#dashInsightsCard missing from the right column");
  const cardEnd = sideCol.indexOf("</section>", cardAt);
  const card = sideCol.slice(cardAt, cardEnd);
  assert(card.includes('id="dashDonutCard"'), "bid outcomes is not inside the Insights card");
  assert(card.includes('class="dash-stats-card"'), "By Document Type is not inside the Insights card");
  const glassCards = (sideCol.match(/class="[^"]*\bglass\b[^"]*"/g) || [])
    .filter(c => !/dash-card/.test(c));         // the quick-access buttons are glass too
  assert(glassCards.length === 1,
    `the right column has ${glassCards.length} glass cards for Insights, not 1: ${glassCards.join(" | ")}`);
});

check("Quick access sits under Insights, in the right column", () => {
  const ins = sideCol.indexOf('id="dashInsightsCard"');
  const qa = sideCol.indexOf('<div class="dash-bottom">');
  assert(qa > ins && ins >= 0, "Quick access must come after the Insights card in the right column");
  assert(/\.dash-view \.dash-mid-side \.dash-qa-head\s*\{/.test(css),
    "the Quick access heading rule still targets the old (left) column");
});

/* the attention card builder, cut out of app.js */
const cardFn = (() => {
  const a = js.indexOf("const naCard = (cat, r) =>");
  assert(a >= 0, "naCard not found in app.js");
  return js.slice(a, js.indexOf("};", js.indexOf("return `<div class=\"dash-na-card", a)));
})();

check("attention cards are one line: no client · date row", () => {
  assert(!cardFn.includes("dash-na-meta"), "the client · date line is back on the attention card");
  assert(/is-compact/.test(cardFn), "attention cards must carry .is-compact");
});

check("nothing is lost: client and date ride in the hover title", () => {
  assert(/\[r\.doc_no, r\.client, \(r\.date \|\| ""\)\.slice\(0, 10\)\]/.test(cardFn),
    "the hover title must still carry the client and the date");
  assert(/title="\$\{esc\(tip\)\}"/.test(cardFn), "the card's title attribute must be the tip");
});

check("a bid's countdown stays on the card, in its tag", () => {
  assert(/d left/.test(cardFn), "a Decide card must still say how many days are left");
});

check("the action button is not the global 40px button", () => {
  // The global `button` rule is height: 40px. On a one-line card that 40px,
  // not the text, set the card's height (measured 56px -> 46px once fixed).
  const btn = ruleBody(".dash-na-card.is-compact .dash-na-btn");
  assert(/height:\s*28px/.test(btn), "the compact card's action button must set its own height");
  const acts = ruleBody(".dash-na-card.is-compact .dash-na-acts");
  assert(/align-items:\s*center/.test(acts), "the action row must centre its buttons");
});

check("unpaid comes LAST, on the dashboard and in the To-Do view", () => {
  // 2026-10-21, Waleed: payment takes the client weeks and there is nothing
  // to do until it lands, so unpaid invoices were filling the grid ahead of
  // work that can be done today.
  const at = js.search(/const groups = \[\s*\["decide"/);
  assert(at >= 0, "the dashboard's group order was not found");
  const order = js.slice(at, js.indexOf("].filter(", at)).match(/\["(\w+)", "/g)
    .map(m => m.slice(2, -4));
  assert(order[order.length - 1] === "unpaid",
    `the dashboard lists ${order.join(" > ")} - unpaid must be last`);
  // The To-Do view is one page of sections since 2026-10-21; Ignored sits
  // after the work, so unpaid must be the last section BEFORE it.
  const todo = fs.readFileSync("static/todo.js", "utf8");
  const tabsSrc = todo.slice(todo.indexOf("const SECTIONS = ["), todo.indexOf("];", todo.indexOf("const SECTIONS = [")));
  const tabs = (tabsSrc.match(/cat: "(\w+)"/g) || []).map(m => m.slice(6, -1)).filter(c => c !== "ignored");
  assert(tabs.length >= 6 && tabs[tabs.length - 1] === "unpaid",
    `the To-Do view lists ${tabs.join(" > ")} - unpaid must be last`);
});

check("needs attention is grouped by type, one heading per type", () => {
  // 2026-10-21, Waleed: separate the types. A heading row (icon, name, count)
  // across both columns, then that type's cards; the card stops repeating it.
  const at = js.indexOf("function renderNeedsAttention");
  const fn = js.slice(at, js.indexOf("window.renderNeedsAttention", at));
  assert(/dash-focus-grid is-grouped/.test(fn), "the attention grid is not marked as grouped");
  assert(/class="dash-na-group is-\$\{cat\}"/.test(fn), "no group heading is drawn per type");
  assert(/dash-na-group-count">\$\{list\.length\}/.test(fn), "the group heading must show the type's full count");
  assert(!/na-tag-\$\{cat\}/.test(cardFn), "the card repeats its type in a tag - the heading says it now");
  assert(/grid-column:\s*1 \/ -1/.test(ruleBody(".dash-na-group")), "the heading must span every column");
  assert(/repeat\(auto-fill,/.test(ruleBody(".dash-focus-grid.is-grouped")),
    "the grouped grid must take as many columns as fit, not a fixed 2");
});

check("the fitter shares the room: every type gets a row before any gets two", () => {
  const at = js.indexOf("function fitGroupedGrid");
  assert(at >= 0, "fitGroupedGrid not found");
  const fn = js.slice(at, js.indexOf("function scheduleDashFit", at));
  const pass1 = fn.indexOf("alloc[i] = 1");
  const pass2 = fn.indexOf("alloc[i]++");
  assert(pass1 >= 0 && pass2 > pass1, "pass 1 (one row per type) must run before pass 2 (extra rows)");
  assert(/\+\$\{left\} more/.test(fn), "a cut group must say how many more it has");
  assert(/fitGroupedGrid\(grid, va\)/.test(js.slice(js.indexOf("function fitFocusGrid"))), "fitFocusGrid never calls the grouped fitter");
});

check("search results keep their client · date line", () => {
  // They reuse the grid, but for a search hit the client is how you recognise it.
  const s = js.slice(js.indexOf("function renderSearchResults"), js.indexOf("function renderSearchResults") + 3000);
  assert(s.includes("dash-na-meta"), "search results lost their client · date line");
});

if (failures) { console.log(`\n${failures} failed`); process.exit(1); }
console.log("\nall mid-row checks passed");
