/* To-Do / Needs-Attention: "Open document" in the preview popup, and the
   Ignored section as a collapsed disclosure.

   Source-contract style, matching tests/dashboard_todo_popup.test.js - todo.js
   is a self-contained IIFE whose internals aren't exported, so the invariants
   are asserted against the source and the stylesheet. */
const fs = require("fs");

const todo = fs.readFileSync("static/todo.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/* ---------- 1. One place that knows how to open a document ---------- */

assert(/const EDIT_LOADER = \{/.test(todo),
  "todo.js should declare a single EDIT_LOADER kind->loader map");
/* The third parameter arrived on 2026-09-12 with `lpo`, whose key is client +
   number; the other loaders ignore it. */
assert(/function openDocEditor\(kind, no(, client)?\)/.test(todo),
  "todo.js should route every 'open' action through one openDocEditor helper");
assert((todo.match(/loadQuoteForEdit/g) || []).length === 1,
  "the kind->loader map must exist exactly once (it was copy-pasted in both the row and modal handlers)");
assert((todo.match(/openDocEditor\(/g) || []).length >= 3,
  "openDocEditor should be declared and called from both the row and modal handlers");

/* ---------- 2. The preview popup offers "Open document" ---------- */

/* 2026-08-26 - the preview's actions moved from a hand-built header strip into
   the shared popup's footer ([[14-UI-CONTRACTS]]), so they are now DATA. The
   behaviour being pinned is unchanged: one reusable "Open document" action,
   offered by every attention category, secondary to that category's own
   decision, with a visible word rather than an icon alone - and NOT duplicated
   in the two categories whose primary action already opens the document.

   Run the real function rather than matching markup: the whole point of the
   move is that the markup is no longer todo.js's business. */
const actsSrc = todo.slice(todo.indexOf("const GOTO_ACT"),
                           todo.indexOf("let _modal = null"));
assert(/const GOTO_ACT\s*=/.test(actsSrc),
  "todo.js should declare one reusable 'Open document' action");
const modalActions = new Function(actsSrc + "\nreturn modalActions;")();

const CATS = ["decide", "uncosted", "unpaid", "unsubmitted", "ignored"];
for (const cat of CATS) {
  const acts = modalActions(cat);
  const goto = acts.find((a) => a.value === "open");
  assert(goto, `the ${cat} popup should let you open the real document`);
  assert(/Open document/.test(goto.label),
    "the Open-document action needs a visible text label, never an icon alone");
  assert(goto.cls !== "primary",
    "opening the document is never the PRIMARY act from an attention queue - "
    + `it sits beside that category's own decision; got ${goto.cls} for ${cat}`);
  const primaries = acts.filter((a) => a.cls === "primary");
  assert(primaries.length === 1,
    `${cat} must offer exactly one primary decision; got ${primaries.length}`);
  assert(acts[acts.length - 1].cls === "primary",
    "the shared footer puts the primary action last");
}
for (const cat of ["deliver", "search"]) {
  const acts = modalActions(cat);
  const opens = acts.filter((a) => a.value === "open");
  assert(opens.length === 1,
    `${cat} already opens the document as its primary action - it must not `
    + "show a second Open-document button");
  assert(opens[0].cls === "primary",
    `${cat}'s open IS the decision, so it keeps the primary fill`);
}
assert(modalActions("decide").some((a) => a.value === "won")
    && modalActions("decide").some((a) => a.value === "lost"),
  "the decide popup keeps Won and Lost");
assert(modalActions("ignored").some((a) => a.value === "restore"),
  "an ignored entry can be restored");
assert(!modalActions("ignored").some((a) => a.value === "ignore"),
  "...and cannot be ignored twice");

/* Insights (2026-10-06) opens documents in this same preview. A paid or
   non-invoice document: Open document is the one (primary) way on. An unpaid
   invoice: Mark paid is the decision, Open document beside it. Never Ignore -
   muting a To-Do reminder means nothing on the Insights screen. */
{
  const look = modalActions("insights");
  assert(look.length === 1 && look[0].value === "open" && look[0].cls === "primary",
    "an Insights preview offers Open document as its one action");
  const owed = modalActions("insights-unpaid");
  assert(owed.some((a) => a.value === "open" && a.cls !== "primary"),
    "an unpaid invoice's preview still lets you open it");
  assert(owed.filter((a) => a.cls === "primary").length === 1 && owed.at(-1).value === "paid",
    "an unpaid invoice's preview offers Mark paid as its decision, last");
  assert(!look.concat(owed).some((a) => a.value === "ignore"),
    "Ignore mutes a To-Do reminder - it has no place on Insights");
}

/* ---------- 3. Ignored stays out of the way ----------
   The To-Do is one page of Insights tables (2026-10-21). Ignored only ever
   grows and is not work, so it is the LAST section and its table starts
   closed behind a Show/Hide button; every row in it offers Restore. */

const secSrc = todo.slice(todo.indexOf("const SECTIONS = ["), todo.indexOf("];", todo.indexOf("const SECTIONS = [")));
const secCats = (secSrc.match(/cat: "(\w+)"/g) || []).map((m) => m.slice(6, -1));
assert(secCats[secCats.length - 1] === "ignored", "Ignored should be the last section: " + secCats.join(", "));
assert(/let _ignoredOpen = false/.test(todo), "Ignored should start closed - it grows without bound");
assert(/aria-expanded="\$\{open\}" aria-controls="todoCard_ignored"/.test(todo),
  "the Show/Hide toggle needs aria-expanded + aria-controls");
assert(/<button type="button" class="in-back todo-toggle"/.test(todo),
  "the toggle must be a <button> so it is keyboard-operable for free");
assert(/data-act="restore"/.test(todo), "an ignored row must offer Restore");

/* ---------- 4. Styling contract ---------- */

/* The popup's buttons are the shared `.popup-actions` ones now, so the old
   `.todo-act.is-goto` rule went with the markup it styled. The ROW buttons in
   the list are untouched and still need theirs. */
assert(!/\.todo-act\.is-goto\s*\{/.test(css),
  "the popup's bespoke Open-document button style should be gone with the "
  + "bespoke button - dead CSS outlives the markup that explains it");
assert(/\.todo-act\.is-ignore\s*\{/.test(css),
  "the row list still has its own action buttons and still needs their style");
assert(/\.todo-toggle\s*\{[^}]*order:\s*2/.test(css),
  "Show/Hide must sit at the far end of the Ignored heading, after its rule");

console.log("To-Do open-document + Ignored section tests passed");
