/* Editing a saved document's DATE must reach the save (2026-09-02).
 *
 * Reported by Waleed on invoices: open a saved invoice, change its date, save -
 * and the PDF still showed the original date and the file stayed in its old
 * month folder.
 *
 * The backend was never at fault. `api.save_inv` derives the folder, the DB
 * year/month AND the rendered date from `inv["date"]`, and `_edit_target_folder`
 * moves the PDF (tests/edit_date_moves_file.test.py pins that). The date simply
 * never got that far: all three editors wired the date field's `input` listener
 * inside `initNew*` ONLY, while the load-for-edit path did a bare `.value =`.
 * A programmatic `.value =` fires no `input` event, so `state.date` kept the
 * loaded date and the save wrote THAT.
 *
 * It looked intermittent because the element is permanent: press New once in a
 * session and the listener is wired for every document opened afterwards.
 *
 * Two halves here, and both matter:
 *   1. `bindDocDate` (app.js) actually behaves - it repaints on every call,
 *      wires exactly one listener, and pushes typing into state.
 *   2. no editor goes back to touching the date element directly.
 */
const fs = require("fs");

// CRLF in the working tree would break the \n-anchored extraction below.
const app   = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const inv   = fs.readFileSync("static/inv.js", "utf8");
const dn    = fs.readFileSync("static/dn.js", "utf8");
const quote = fs.readFileSync("static/quote.js", "utf8");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ------------------------------------------------- 1. bindDocDate behaviour
// Run the real source against a tiny fake DOM; no jsdom in this repo.
function makeEl() {
  return {
    tagName: "INPUT",
    value: "",
    textContent: "",
    _listeners: [],
    addEventListener(ev, fn) { this._listeners.push([ev, fn]); },
    fire(ev) { this._listeners.filter(([e]) => e === ev).forEach(([, fn]) => fn()); },
  };
}

const el = makeEl();
const sandbox = {
  document: { getElementById: (id) => (id === "invDate" ? el : null) },
  window: {},
  state: { today: { display: "2/9/2026", iso: "2026-09-02" } },
};
sandbox.window.state = sandbox.state;

// displayToIso / isoToDisplay / bindDocDate, verbatim from app.js.
const pick = (name) => {
  const start = app.indexOf("\nfunction " + name + "(");
  assert(start > -1, name + " is missing from app.js");
  const end = app.indexOf("\n}\n", start);
  assert(end > start, "cannot find the end of " + name);
  return app.slice(start, end + 3);
};
const src = pick("displayToIso") + pick("isoToDisplay") + pick("bindDocDate");
new Function("document", "window", "state", src + "\nwindow.__bind = bindDocDate;")(
  sandbox.document, sandbox.window, sandbox.state);
const bindDocDate = sandbox.window.__bind;

const invState = { date: "24/8/2026", dirty: false };
bindDocDate("invDate", invState);
assert(el.value === "2026-08-24",
  `the field must show the document's own date, got ${el.value}`);
assert(el._listeners.length === 1, "exactly one input listener");
assert(invState.dirty === false,
  "painting the loaded date must not mark a freshly opened document dirty");

// The user types a September date.
el.value = "2026-09-15";
el.fire("input");
assert(invState.date === "15/9/2026",
  `typing must reach state.date - this is the whole bug. Got ${invState.date}`);
assert(invState.dirty === true, "an edited date must mark the document dirty");

// Opening another document re-paints, and must NOT stack a second listener -
// one stacked handler per open would rewrite state.date from a stale closure.
const other = { date: "1/1/2026", dirty: false };
bindDocDate("invDate", other);
assert(el.value === "2026-01-01", "re-binding must repaint the new date");
assert(el._listeners.length === 1,
  "a second listener was stacked - the element outlives the editor");

// A half-typed date must not blank the date the save is about to use.
const keep = { date: "15/9/2026", dirty: false };
bindDocDate("invDate", keep);
el.value = "20";
el.fire("input");
assert(keep.date === "15/9/2026",
  "an unparseable half-typed value must leave the last good date alone");

// A non-INPUT (read-only display) still gets its text, and no listener.
const span = makeEl();
span.tagName = "SPAN";
sandbox.document.getElementById = (id) => (id === "invDate" ? span : null);
bindDocDate("invDate", { date: "3/3/2026" });
assert(span.textContent === "3/3/2026" && span._listeners.length === 0,
  "a read-only date display must be filled but never wired");

// ------------------------------------------- 2. no editor bypasses it again
for (const [name, s] of [["inv.js", inv], ["dn.js", dn], ["quote.js", quote]]) {
  assert(!/getElementById\("(invDate|dnDate|qDate)"\)/.test(s),
    `${name} reaches for the date element directly again. Every path - New, `
    + "load-for-edit AND duplicate - must go through bindDocDate, or an edited "
    + "date silently fails to reach the save.");
}

// Each editor must bind on all three lifecycle paths.
const binds = (s, id) => s.split('bindDocDate("' + id + '",').length - 1;
assert(binds(inv, "invDate") >= 3,
  "inv.js must bind the date on New, load-for-edit and duplicate");
assert(binds(dn, "dnDate") >= 3,
  "dn.js must bind the date on New, load-for-edit and duplicate");
assert(binds(quote, "qDate") >= 3,
  "quote.js must bind the date on New, load-for-edit and duplicate");

console.log("editor date edit reaches save: passed");
