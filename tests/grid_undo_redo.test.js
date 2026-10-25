/* Undo must step, not reset (2026-08-01).

   Reported: "paste something, adjust some prices, Ctrl+Z - it goes back to
   before the paste." Correct diagnosis, and it was by design: the M.5c stack
   snapshotted ONLY before a paste. Typing was never recorded, so the single
   stored snapshot was the pre-paste state and restoring it discarded every
   correction made since. Ctrl+Shift+Z was explicitly ignored, so no redo.

   Now every committed change is one step - one per cell edit, one per paste,
   one per in-table click that changes the rows - with a matching redo stack.

   Driven through the real listeners `setupGridPaste` installs, against a
   stubbed tbody. No browser.
*/
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("static/grid_paste.js", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/* A tbody stub that records listeners so the test can fire events. */
function makeTbody() {
  const handlers = {};
  return {
    tagName: "TBODY",
    _handlers: handlers,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    contains() { return false; },   // focus is treated as having left the table
    closest() { return null; },
    fire(type, ev) { (handlers[type] || []).forEach(fn => fn(ev || {})); },
  };
}

const FIELD = { tagName: "INPUT", closest: () => null };

function harness() {
  const timers = [];
  const ctx = {
    console,
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout() {},
    document: {
      addEventListener() {}, removeEventListener() {},
      createElement: () => ({ style: {}, classList: { add() {}, remove() {} },
                              addEventListener() {}, appendChild() {} }),
      body: { appendChild() {}, removeChild() {} },
      activeElement: null,
    },
    navigator: {},
    toast: () => {},
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInNewContext(source, ctx);

  // Minimal editor state: the thing undo/redo actually moves.
  let rows = [{ name: "a", qty: "1" }, { name: "b", qty: "2" }];
  const tbody = makeTbody();
  ctx.setupGridPaste(tbody, {
    columns: () => ([{ key: "name", kind: "text" }, { key: "qty", kind: "num" }]),
    rowCount: () => rows.length,
    addEmptyRow: () => rows.push({ name: "", qty: "" }),
    setCell: (i, k, v) => { if (rows[i]) rows[i][k] = v; },
    fullRender: () => {},
    snapshot: () => JSON.parse(JSON.stringify(rows)),
    restore: (s) => { rows = s; },
  });

  const flush = () => { const t = timers.splice(0); t.forEach(fn => fn()); };
  const edit = (fn) => {                       // one complete cell edit
    tbody.fire("focusin", { target: FIELD });
    fn();
    tbody.fire("change", { target: FIELD });
  };
  const key = (k, opt = {}) => {
    let prevented = false;
    tbody.fire("keydown", {
      ctrlKey: true, shiftKey: !!opt.shift, key: k,
      preventDefault: () => { prevented = true; }, stopPropagation() {},
    });
    return prevented;
  };
  return { ctx, tbody, flush, edit, key,
           get rows() { return rows; } };
}

/* ---------- 1. THE REPORTED BUG: edits after a paste undo on their own ------ */
{
  const h = harness();
  // Stand in for the paste: a bulk change committed as one step.
  h.edit(() => { h.rows.push({ name: "pasted-1", qty: "9" },
                              { name: "pasted-2", qty: "9" }); });
  // Then the user "adjusts some prices" - two separate cell edits.
  h.edit(() => { h.rows[2].qty = "11"; });
  h.edit(() => { h.rows[3].qty = "22"; });

  assert(h.key("z"), "Ctrl+Z must be handled once there is history");
  assert(h.rows[3].qty === "9" && h.rows[2].qty === "11",
    "one Ctrl+Z must undo ONLY the last price edit, got qty " + h.rows[3].qty);

  h.key("z");
  assert(h.rows[2].qty === "9" && h.rows.length === 4,
    "the second Ctrl+Z undoes the other price edit - the pasted rows must SURVIVE. "
    + "This is the exact regression: rows=" + JSON.stringify(h.rows));

  h.key("z");
  assert(h.rows.length === 2,
    "only the third Ctrl+Z removes the pasted rows, got " + h.rows.length);
}

/* ---------- 2. redo, both key combinations ---------- */
{
  const h = harness();
  h.edit(() => { h.rows[0].name = "changed"; });
  h.key("z");
  assert(h.rows[0].name === "a", "undo failed before testing redo");

  assert(h.key("z", { shift: true }), "Ctrl+Shift+Z must be handled");
  assert(h.rows[0].name === "changed", "Ctrl+Shift+Z must redo the edit");

  h.key("z");
  assert(h.key("y"), "Ctrl+Y must be handled as a redo alias");
  assert(h.rows[0].name === "changed", "Ctrl+Y must redo the edit");
}

/* ---------- 3. a new edit clears the redo branch ---------- */
{
  const h = harness();
  h.edit(() => { h.rows[0].name = "one"; });
  h.key("z");
  h.edit(() => { h.rows[0].name = "two"; });
  assert(!h.key("z", { shift: true }),
    "after a new edit there is no forward branch, so redo must NOT be handled "
    + "(it would resurrect an abandoned state)");
  assert(h.rows[0].name === "two", "redo must not have changed anything");
}

/* ---------- 4. with no history, native text undo is left alone ---------- */
{
  const h = harness();
  assert(!h.key("z"),
    "with an empty history Ctrl+Z must NOT be preventDefault'd - the browser's "
    + "per-character undo inside a cell has to keep working");
  assert(!h.key("z", { shift: true }), "same for redo with nothing to redo");
}

/* ---------- 5. visiting a cell without changing it is not a step ---------- */
{
  const h = harness();
  h.edit(() => { h.rows[0].name = "real"; });
  h.edit(() => {});                       // focus + leave, no change
  h.key("z");
  assert(h.rows[0].name === "a",
    "the no-op visit must not consume the undo - one Ctrl+Z should reach the "
    + "real edit, got " + h.rows[0].name);
}

/* ---------- 6. an auto-appended blank row is not a step ---------- */
{
  const h = harness();
  h.edit(() => { h.rows.push({ name: "", qty: "" }); });   // trailing blank only
  assert(!h.key("z"),
    "editors append a blank row as soon as the last one gets content; that "
    + "alone must not register as an undoable step");
}

/* ---------- 7. an in-progress edit becomes its own step ---------- */
{
  const h = harness();
  h.edit(() => { h.rows[0].name = "first"; });
  // Typing, but focus never left - `change` has not fired.
  h.tbody.fire("focusin", { target: FIELD });
  h.rows[1].name = "typing";
  h.key("z");
  assert(h.rows[1].name === "b",
    "Ctrl+Z mid-edit must undo what was just typed, not skip back past it; "
    + "got " + h.rows[1].name);
  assert(h.rows[0].name === "first", "the earlier edit must still stand");
}

/* ---------- 8. row deletes via in-table clicks are undoable ---------- */
{
  const h = harness();
  h.tbody.fire("mousedown", { target: { tagName: "BUTTON", closest: () => null } });
  h.rows.splice(0, 1);                    // the delete handler's effect
  h.flush();                              // the deferred compare
  h.key("z");
  assert(h.rows.length === 2 && h.rows[0].name === "a",
    "deleting a row from the table must be undoable, got " + JSON.stringify(h.rows));
}

/* ---------- 9. the old paste-only stack is really gone ---------- */
{
  assert(!/_pushUndo|_popUndo|_undoCount/.test(source),
    "the paste-only stack helpers must be removed, not left shadowing the new "
    + "history");
  assert(/if \(ev\.shiftKey\) return;\s*\/\/ leave Ctrl\+Shift\+Z alone/.test(source) === false,
    "the early-return that disabled redo must be gone");
}

console.log("Grid undo/redo tests passed");
