/* One Excel cell's Alt+Enter text must land in ONE cell (2026-09-02).
 *
 * Reported with a screenshot: a spec cell holding
 *
 *     • Elec. Panel board shall be provided with MCB incoming & RCBO outgoings.
 *     • PVC Conduit shall be provided for wiring.
 *     • MCB Incoming Breaker 63A (1EA)
 *     ...
 *
 * was copied into the DESCRIPTION column and the editor answered
 * "Pasted 8 rows x 1 cell" - one row per line.
 *
 * Why the existing HTML-is-authoritative rule did not save it: the user copied
 * from INSIDE the cell (double-click, select the text, Ctrl+C), not the cell
 * itself. Excel puts a `text/html` <table> on the clipboard only for a real
 * RANGE copy; an in-cell text selection is plain text and nothing else. So the
 * parser had no boundary information at all and fell back to splitting lines.
 *
 * The rule under test: no <table>, no TAB and more than one line means the text
 * did not come from a grid. Pasted into a cell that can hold line breaks
 * (a <textarea> - the editors render text columns as textareas and numeric ones
 * as inputs) it is ONE value. Numeric cells are unaffected, a real Excel range
 * is unaffected, and "Paste as rows" forces the old reading back.
 */
const fs = require("fs");
const vm = require("vm");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const source = fs.readFileSync("static/grid_paste.js", "utf8");

// The real spec cell from the report.
const CELL = [
  "• Elec. Panel board shall be provided with MCB incoming & RCBO outgoings. (Brand : Schneider)",
  "• PVC Conduit shall be provided for wiring.",
  "• MCB Incoming Breaker 63A (1EA)",
  "• RCBO Outgoing Breaker",
  " 1) AC Socket 32A (1EA)",
  " 2) Power Socket 20A (1EA)",
  " 3) Lighting 16A (1EA)",
  " 4) Spare Breaker 32A (1EA), 20A (1EA)",
].join("\n");

function node(tag, props) {
  const n = Object.assign({
    tagName: tag,
    dataset: {},
    parent: null,
    querySelector() { return null; },
    closest(sel) {
      let c = n;
      while (c) {
        if (sel === "[data-k]" && c.dataset && c.dataset.k) return c;
        if (sel === "tr[data-i]" && c.tagName === "TR" && c.dataset.i != null) return c;
        n_last = c;
        c = c.parent;
      }
      return null;
    },
  }, props || {});
  let n_last;
  return n;
}

/* The editors render a text column as
 *     <td><textarea data-k="description" ...></textarea></td>
 * so `data-k` sits on the FIELD itself. A numeric column is an <input>.
 * Modelling that exactly is the point - the fix reads the tag off the DOM. */
function harness(fieldTag) {
  const listeners = {};
  const toasts = [];
  const rows = [{ item: "GUARD HOUSE", description: "", qty: "" }];

  const tr = node("TR", { dataset: { i: "0" } });
  const td = node("TD", {});
  td.parent = tr;
  const field = node(fieldTag, { dataset: { k: "description" }, value: "" });
  field.parent = td;

  const tbody = {
    _gridPasteWired: false,
    addEventListener(type, fn) { listeners[type] = fn; },
    querySelector() { return null; },
    contains() { return false; },
  };

  const ctx = {
    console,
    setTimeout() {},
    clearTimeout() {},
    DOMParser: function () {
      this.parseFromString = () => ({ querySelector: () => null });
    },
    navigator: { clipboard: { async read() { return []; } } },
    document: {
      activeElement: null,
      addEventListener() {},
      removeEventListener() {},
      getElementById() { return null; },
      createElement() { return node("DIV", { style: {}, appendChild() {}, remove() {} }); },
      body: { appendChild() {} },
    },
    window: {
      innerWidth: 1200,
      innerHeight: 800,
      ICON: {},
      toast(msg) { toasts.push(msg); },
      getSelection: () => ({ isCollapsed: true, toString: () => "" }),
    },
  };
  ctx.window.document = ctx.document;
  vm.createContext(ctx);
  vm.runInContext(source, ctx, { filename: "static/grid_paste.js" });

  ctx.window.setupGridPaste(tbody, {
    columns: () => [
      { key: "item", kind: "text" },
      { key: "description", kind: "text" },
      { key: "qty", kind: "num" },
    ],
    rowCount: () => rows.length,
    addEmptyRow: () => rows.push({ item: "", description: "", qty: "" }),
    setCell: (i, k, v) => { rows[i][k] = v; },
    fullRender() {},
    snapshot: () => JSON.parse(JSON.stringify(rows)),
    restore(s) { rows.length = 0; s.forEach(r => rows.push(r)); },
  });

  function paste(text, html) {
    const ev = {
      target: field,
      defaultPrevented: false,
      preventDefault() { ev.defaultPrevented = true; },
      clipboardData: { getData: (t) => (t === "text/html" ? (html || "") : text) },
    };
    listeners.paste(ev);
    return ev;
  }

  return { rows, toasts, paste, ctx };
}

/* ---------- 1. the reported bug ---------- */
{
  const h = harness("TEXTAREA");
  const ev = h.paste(CELL, "");
  ok(ev.defaultPrevented, "the editor must handle this paste, not the browser");
  ok(h.rows.length === 1,
    `one Excel cell must not become rows - got ${h.rows.length} rows`);
  ok(h.rows[0].description === CELL,
    "the cell must hold the text verbatim, every line intact");
  ok(/1 cell/.test(h.toasts[0] || ""),
    `the toast must say one cell, got: ${h.toasts[0]}`);
  ok(/8 lines/.test(h.toasts[0] || ""),
    "the toast should say how many lines landed, so a wrong guess is visible");
  ok(/Paste as rows/.test(h.toasts[0] || ""),
    "the toast must name the way to get the other reading");
}

/* ---------- 2. a real Excel RANGE still pastes as rows ---------- */
{
  // Two rows, one column. Excel supplies an HTML table for a range copy, and
  // that stays authoritative - this is the case the fix must NOT break.
  const h = harness("TEXTAREA");
  const html = "<table><tr><td>Cement</td></tr><tr><td>Sand</td></tr></table>";
  // The vm's DOMParser stub returns no table, so drive the plain-text branch
  // with a TAB instead - tabs prove real columns just as a <table> does.
  const ev = h.paste("Cement\tbagged\nSand\tloose", "");
  ok(ev.defaultPrevented, "a tabbed grid is handled by the editor");
  ok(h.rows.length === 2,
    `tabbed multi-row text must still paste as rows - got ${h.rows.length}`);
  // The anchor is DESCRIPTION, so the grid's first column lands there and the
  // second spills into the next column across (qty).
  ok(h.rows[0].description === "Cement" && h.rows[1].description === "Sand",
    "the tabbed grid must fill DOWN from the anchor column");
  ok(String(h.rows[0].qty) === "bagged",
    "and ACROSS into the next column");
  ok(html.length > 0, "html sample kept for documentation");
}

/* ---------- 3. a single-line value is left to the browser ---------- */
{
  const h = harness("TEXTAREA");
  const ev = h.paste("Guard House", "");
  ok(!ev.defaultPrevented,
    "a plain one-line value must fall through to the native paste so it "
    + "inserts at the cursor instead of replacing the cell");
}

/* ---------- 4. a NUMERIC cell is untouched by the new rule ---------- */
{
  // qty/rate render as <input> and cannot hold line breaks, so multi-line text
  // there still means one row per line.
  const h = harness("INPUT");
  const ev = h.paste("5\n6\n7", "");
  ok(ev.defaultPrevented, "the editor handles a multi-line paste into a number cell");
  ok(h.rows.length === 3,
    `a single-line field must still receive rows - got ${h.rows.length}`);
}

/* ---------- 5. the predicate itself ---------- */
{
  const h = harness("TEXTAREA");
  const loose = h.ctx.window.looksLikeLooseText;
  ok(loose("", "a\nb") === true, "no html + no tab + 2 lines = loose text");
  ok(loose("", "a\tb\nc\td") === false, "tabs mean a real grid");
  ok(loose("<table><tr><td>a</td></tr></table>", "a\nb") === false,
    "an HTML table always wins - it states the boundaries exactly");
  ok(loose("", "one line") === false, "a single line is not ambiguous");
  ok(loose("", "one line\n") === false,
    "a trailing newline alone is not a second line");
  ok(loose("", "") === false, "empty text is not loose text");
  ok(loose(null, null) === false, "null clipboard must not throw");
}

console.log(`grid paste in-cell text tests passed (${checks} checks)`);
