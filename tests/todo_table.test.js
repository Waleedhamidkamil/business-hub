/* The To-Do is ONE page of Insights tables (2026-10-21, Waleed).

   He asked for the Insights table inside Needs attention: every type on one
   page (no tabs - he saw a tabbed version and turned it down), each as an
   Insights section heading over an Insights table card that sorts on any
   heading, a KPI row on top, a row click that previews the document - and no
   more lists scrolling inside a page that also scrolls (the 31-row Unpaid
   block caught the wheel half the time).

   This replaces todo_row_fits_its_cell.test.js, whose subject - a card that
   overflowed its grid cell - no longer exists.

   The pure half (columns, sorting, filtering, money, summaries) is loaded out
   of todo.js and RUN; the layout half is read from the source and the CSS. */
const fs = require("fs");

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const src = fs.readFileSync("static/todo.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");
const html = fs.readFileSync("templates/index.html", "utf8");

const start = src.indexOf("    const SECTIONS = [");
const end = src.indexOf("    function lists() {");
assert(start > 0 && end > start, "cannot cut the table block out of todo.js");
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
const T = new Function("esc", "ICON_RESTORE", "ICON_EYEOFF", "_scope", "KIND_BADGE", "CASE_ICON",
  src.slice(start, end) + `
  return { SECTIONS, COL, COLS_FOR, fmtMoney, fmtDate, sorted, filtered, summaryText,
           rowsHtml, setSort: (c, s) => { _sort[c] = s; }, setQuery: (q) => { _query = q; } };`)(
  esc, "<svg/>", "<svg/>", "all", { quote: "QUT", dn: "DN", inv: "INV", po: "PO", lpo: "LPO" }, () => "<svg/>");

function ruleBody(selector) {
  const at = css.search(new RegExp("(^|\\n)" + selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{"));
  assert(at >= 0, "CSS rule not found: " + selector);
  return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
}

const unpaid = [
  { kind: "inv", doc_no: "INV-A-001", client: "TDC", date: "5/8/2026", sub_type: "materials", age_days: 77, amount: 200, currency: "USD", amount_usd: 200 },
  { kind: "inv", doc_no: "INV-A-002", client: "JUNE CONSTRUCTION CO.", date: "10/8/2026", sub_type: "materials", age_days: 5, amount: 12600000, currency: "IQD", amount_usd: 9000 },
  { kind: "inv", doc_no: "INV-A-003", client: "Hyundai", date: "1/9/2026", sub_type: "equipment", age_days: 50 },
];

console.log("To-Do: one page of Insights tables");

check("no tabs: every type is a section on one page", () => {
  assert(!/role="tablist"|role="tab"/.test(src), "the tabs are back - Waleed turned them down");
  const r = src.slice(src.indexOf("    function render() {"), src.indexOf("    function allClearHtml()"));
  assert(/work\.map\(\(s\) => sectionHtml\(s, L\[s\.cat\]\)\)/.test(r), "render() no longer draws every section");
});

check("it IS the Insights UI: KPI row, section heading, table card, sort marks", () => {
  assert(/class="in-kpis/.test(src) && /class="in-kpi todo-kpi"/.test(src), "no Insights KPI row");
  assert(/<div class="in-sect">(<span class="todo-ico">[^`]*?<\/span>)?<h3>/.test(src), "no Insights section heading");
  assert(/class="in-card todo-card"/.test(src) && /<table class="in-table">/.test(src), "not an Insights table card");
  assert(/class="in-sortmark"/.test(src), "the sort mark is not Insights'");
  assert(/id="todoView" class="view todo-view in-view"/.test(html), "the view lacks Insights' variables (--in-neg)");
});

check("every type keeps its order, unpaid is the last piece of work, Ignored after it", () => {
  const cats = T.SECTIONS.map((t) => t.cat);
  assert(cats.join(",") === "decide,billing,deliver,unsubmitted,uncosted,unpaid,ignored",
    "section order changed: " + cats.join(", "));
  cats.forEach((c) => assert(Array.isArray(T.COLS_FOR[c]) && T.COLS_FOR[c][0] === "doc",
    `the ${c} table has no columns, or does not lead with the document`));
});

check("each table shows what its work is judged by", () => {
  assert(T.COLS_FOR.unpaid.includes("amount") && T.COLS_FOR.unpaid.includes("waiting"),
    "an unpaid invoice needs its amount and how long it has waited");
  assert(T.COLS_FOR.decide.includes("left"), "a bid needs its countdown to auto-loss");
  assert(T.COLS_FOR.deliver.includes("lines"), "a PO needs how many lines are left to invoice");
  assert(T.COLS_FOR.billing.includes("due"), "a contract needs how many months are due");
  assert(T.COLS_FOR.ignored.includes("muted"), "a muted row needs what it was muted from");
});

check("money keeps its own currency; sorting uses dollars", () => {
  assert(T.fmtMoney(9000, "USD") === "$9,000.00", T.fmtMoney(9000, "USD"));
  assert(T.fmtMoney(12600000, "IQD") === "IQD 12,600,000", T.fmtMoney(12600000, "IQD"));
  assert(T.rowsHtml("unpaid", unpaid).includes("IQD 12,600,000"), "a dinar invoice must show dinars on its row");
  T.setSort("unpaid", { key: "amount", dir: -1 });
  const order = T.sorted("unpaid", unpaid).map((r) => r.doc_no);
  assert(order.join() === "INV-A-002,INV-A-001,INV-A-003",
    "biggest first by DOLLAR value, blanks last: " + order.join());
  T.setSort("unpaid", { key: "amount", dir: 1 });
  assert(T.sorted("unpaid", unpaid).map((r) => r.doc_no).pop() === "INV-A-003",
    "a row with no amount sorts last in BOTH directions");
  T.setSort("unpaid", null);
});

check("dates sort as dates, not as text (5/8 before 10/8)", () => {
  T.setSort("unpaid", { key: "date", dir: 1 });
  assert(T.sorted("unpaid", unpaid).map((r) => r.doc_no).join() === "INV-A-001,INV-A-002,INV-A-003");
  assert(T.fmtDate("4/6/2026") === "4 Jun 2026", "dd/mm must not be read as mm/dd: " + T.fmtDate("4/6/2026"));
  T.setSort("unpaid", null);
});

check("a heading click re-sorts the ROWS, not just the arrow", () => {
  // Caught in the browser 2026-10-21: the arrow flipped and the rows did not move.
  const i = src.indexOf("th.onclick = () => {");
  assert(i > 0 && /drawRows\(cat\)/.test(src.slice(i, i + 600)), "the sort handler never redraws the rows");
});

check("the filter matches the number or the client, across every table", () => {
  T.setQuery("june");
  assert(T.filtered(unpaid).map((r) => r.doc_no).join() === "INV-A-002", "client match failed");
  T.setQuery("a-003");
  assert(T.filtered(unpaid).map((r) => r.doc_no).join() === "INV-A-003", "number match failed");
  T.setQuery("zzz");
  assert(/todo-nomatch/.test(T.rowsHtml("unpaid", unpaid)), "an emptied table must say nothing matches");
  T.setQuery("");
  assert(T.filtered(unpaid).length === 3, "an empty filter must show everything");
  const i = src.indexOf("search.oninput");
  assert(/drawRows\(\)/.test(src.slice(i, i + 120)) && !/render\(\)/.test(src.slice(i, i + 120)),
    "typing must redraw the rows only, so the box keeps its focus");
});

check("age is said in words; the tag only strengthens it", () => {
  const html = T.rowsHtml("unpaid", unpaid);
  assert(/<td class="is-neg todo-late"[^>]*><span class="todo-age is-late">77 days<\/span><\/td>/.test(html),
    "77 days waiting must read late, in words: " + html.slice(0, 600));
  assert(/<span class="todo-age">5 days<\/span>/.test(html), "5 days is neither aging nor late");
  assert(/<span class="todo-age is-late">50 days<\/span>/.test(html), "50 days is late");
});

check("every type wears its own colour, in both themes", () => {
  // 2026-10-21, Waleed: "it feels dead". The colour identifies; the words carry it.
  const dark = fs.readFileSync("static/dark.css", "utf8");
  ["decide", "billing", "deliver", "unsubmitted", "uncosted", "unpaid"].forEach((c) => {
    assert(new RegExp(`\\[data-sec="${c}"\\],\\s*\\.todo-kpi\\[data-jump="${c}"\\]\\s*\\{[^}]*--tc:`).test(css),
      `${c} has no colour in light mode`);
    assert(new RegExp(`\\[data-sec="${c}"\\],\\s*html\\[data-theme="dark"\\] \\.todo-kpi\\[data-jump="${c}"\\]\\s*\\{[^}]*--tc-ink:`).test(dark),
      `${c} has no light step for dark mode`);
  });
  assert(/class="todo-act is-main" data-act="paid"/.test(src), "a row's own action does not take its section's colour");
});

check("a tile flags what has waited too long, in words", () => {
  assert(/waiting \$\{LATE_DAYS\}\+ days/.test(src) && /lost within 2 days/.test(src),
    "the KPI tiles lost their late count");
  assert(/sec\.cat === "billing"\s*\n?\s*\? list\.filter\(\(r\) => \(Number\(r\.months_due\) \|\| 0\) >= 3\)/.test(src),
    "a contract is late by MONTHS DUE - its age is just the LPO date");
});

check("the page fades in once per visit, never on a reload after an action", () => {
  assert(/_enter = true;\s*\n\s*await load\(\);/.test(src), "showTodo does not arm the entrance");
  assert(/const enter = _enter;\s*\n\s*_enter = false;/.test(src), "render() replays the entrance on every reload");
  assert(/prefers-reduced-motion[\s\S]{0,200}todo-page\.is-entering[\s\S]{0,120}animation: none/.test(css),
    "the entrance ignores reduced motion");
});

check("the section line totals what matters, in dollars", () => {
  const s = T.summaryText("unpaid", unpaid);
  assert(s === "3 invoices · $9,200.00 owed · oldest waiting 77 days", s);
});

check("a row click opens the shared preview popup", () => {
  assert(/openDocModal\(el\.dataset\.cat, el\.dataset\.kind, el\.dataset\.no,/.test(src),
    "rows no longer open the document preview");
});

check("ONE scroller: the page scrolls, no table scrolls on its own", () => {
  assert(/overflow-y:\s*auto/.test(ruleBody(".todo-body")), "the page must scroll");
  assert(!/in-scroll tall/.test(src), "a To-Do table has its own height cap again - that is the wheel fight");
  assert(!/\.todo-card[^{]*\{[^}]*max-height/.test(css), "a To-Do table card has its own height cap");
});

if (failures) { console.log(`\n${failures} failed`); process.exit(1); }
console.log("\nall To-Do table checks passed");
