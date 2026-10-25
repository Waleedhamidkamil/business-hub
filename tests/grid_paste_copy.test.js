/* Right-click Copy (2026-08-16).

   The app starts pywebview with debug=False (app.py), and pywebview ties
   AreDefaultContextMenusEnabled to that flag - so the WebView2 default menu is
   OFF and a right-click shows exactly what grid_paste.js puts in it. Before
   this, that was "Paste here / Paste text here" inside item tables and NOTHING
   anywhere else: the mouse could not copy text in this app at all.

   Two paths, deliberately different:

     inside an item cell   selection, else the WHOLE CELL - a bounded, known
                           string, so copying a cell you did not select is safe
     everywhere else       selection, else the form field's value, else NO MENU
                           - falling back to textContent out here would put half
                           the page on the clipboard for a right-click on any
                           container div
*/
const fs = require("fs");
const vm = require("vm");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const source = fs.readFileSync("static/grid_paste.js", "utf8");

function el(tag, props) {
  const node = Object.assign({
    tagName: tag,
    dataset: {},
    parent: null,
    children: [],
    textContent: "",
    querySelector() { return null; },
    closest(sel) {
      let n = node;
      while (n) {
        if (sel === "[data-k]" && n.dataset && n.dataset.k) return n;
        if (sel === "tr[data-i]" && n.tagName === "TR" && n.dataset.i != null) return n;
        if (sel === "#gridPasteMenu" && n.id === "gridPasteMenu") return n;
        n = n.parent;
      }
      return null;
    },
  }, props || {});
  return node;
}

function makeHarness(opts) {
  opts = opts || {};
  const listeners = {};        // tbody listeners
  const docListeners = {};     // document listeners
  const written = [];          // everything handed to navigator.clipboard.writeText
  const toasts = [];
  const rows = [{ item: "Cement", qty: 5 }];

  let menu = null;
  const buttons = {};

  const tr = el("TR", { dataset: { i: "0" } });
  const td = el("TD", { dataset: { k: "item" } });
  td.parent = tr;
  const field = el("INPUT", Object.assign({
    type: "text",
    value: "Portland Cement 42.5N",
  }, opts.field || {}));
  field.parent = td;
  if (opts.throwOnSelection) {
    // Defined here rather than in the literal above because Object.assign would
    // INVOKE the getter while copying it and blow up the harness itself.
    Object.defineProperty(field, "selectionStart", {
      get() { throw new TypeError("does not support selection"); },
    });
  }
  td.querySelector = (sel) => (sel === "input, textarea, select" ? field : null);

  const tbody = {
    _gridPasteWired: false,
    addEventListener(type, fn) { listeners[type] = fn; },
    querySelector() { return null; },
    contains() { return false; },
  };

  const context = {
    console,
    setTimeout() {},
    clearTimeout() {},
    navigator: {
      clipboard: {
        async read() { return []; },
        async writeText(t) { written.push(t); },
      },
    },
    document: {
      activeElement: null,
      addEventListener(type, fn) { docListeners[type] = fn; },
      removeEventListener() {},
      getElementById(id) { return id === "gridPasteMenu" ? menu : null; },
      createElement() {
        menu = el("DIV", {
          id: "gridPasteMenu",
          style: {},
          innerHTML: "",
          getBoundingClientRect() { return { right: 100, bottom: 100, width: 100, height: 80 }; },
          appendChild() {},
          remove() { menu = null; },
          querySelector(sel) {
            const m = sel.match(/data-act="([^"]+)"/);
            if (!m) return null;
            // Only hand back a button the menu actually rendered - the real DOM
            // returns null for an item that was left out, and the "no Copy on
            // an empty cell" case depends on that being modelled.
            if (menu.innerHTML.indexOf(`data-act="${m[1]}"`) < 0) return null;
            return (buttons[m[1]] ||= {});
          },
        });
        return menu;
      },
      body: { appendChild() {} },
    },
    window: {
      innerWidth: 1200,
      innerHeight: 800,
      ICON: { copy: "<svg/>", clipboard: "<svg/>" },
      toast(msg, isErr) { toasts.push({ msg, isErr }); },
      getSelection: () => opts.selection || { isCollapsed: true, toString: () => "" },
    },
  };
  context.window.document = context.document;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "static/grid_paste.js" });

  context.window.setupGridPaste(tbody, {
    columns: () => [{ key: "item", kind: "text" }, { key: "qty", kind: "num" }],
    rowCount: () => rows.length,
    addEmptyRow: () => rows.push({ item: "", qty: "" }),
    setCell: (i, k, v) => { rows[i][k] = v; },
    fullRender() {},
    snapshot: () => JSON.parse(JSON.stringify(rows)),
    restore(s) { rows.length = 0; s.forEach(r => rows.push(r)); },
  });

  function fire(which, target, alreadyHandled) {
    const ev = {
      target,
      clientX: 10,
      clientY: 20,
      defaultPrevented: !!alreadyHandled,
      preventDefault() { ev.defaultPrevented = true; },
    };
    (which === "doc" ? docListeners : listeners).contextmenu(ev);
    return ev;
  }

  return {
    field, td, tbody, written, toasts, buttons,
    acts: () => (menu ? (menu.innerHTML.match(/data-act="([^"]+)"/g) || [])
      .map(s => s.slice(10, -1)) : []),
    fireCell: () => fire("cell", field),
    fireDoc: (target, alreadyHandled) =>
      fire("doc", target === undefined ? field : target, alreadyHandled),
    fireBoth: (target) => {
      // What really happens in the browser: the cell is inside the tbody, so
      // the tbody listener runs first and the document listener runs after it
      // on the SAME event.
      const ev = fire("cell", target);
      docListeners.contextmenu(ev);
      return ev;
    },
    el,
  };
}

/* ---------- the grid cell menu ---------- */
async function testCellCopiesWholeCellWhenNothingSelected() {
  const h = makeHarness();
  h.fireCell();

  ok(h.acts()[0] === "copy",
     "Copy leads the cell menu, as it does in every OS menu: " + h.acts().join(","));
  ok(h.acts().join(",") === "copy,paste,paste-rows,paste-text,cancel",
     "…and the paste items it grew out of are still there: " + h.acts().join(","));

  await h.buttons.copy.onclick();
  ok(h.written.length === 1 && h.written[0] === "Portland Cement 42.5N",
     "right-clicking a cell with no selection copies the WHOLE cell: "
     + JSON.stringify(h.written));
  ok(h.toasts.length === 1 && !h.toasts[0].isErr, "and says so");
}

async function testSelectionInsideTheCellWins() {
  const h = makeHarness({ field: { selectionStart: 9, selectionEnd: 15 } });
  h.fireCell();
  await h.buttons.copy.onclick();
  ok(h.written[0] === "Cement",
     "a selection inside the cell beats the whole cell - the user picked those "
     + "characters on purpose: " + JSON.stringify(h.written));
}

async function testEmptyCellOffersNoCopyAtAll() {
  const h = makeHarness({ field: { value: "" } });
  h.fireCell();
  ok(h.acts().indexOf("copy") < 0,
     "an empty cell has nothing to copy, so the row is left out rather than "
     + "shown greyed: " + h.acts().join(","));
  ok(h.acts().join(",") === "paste,paste-rows,paste-text,cancel",
     "…and pasting into it still works, which is the whole point of an empty "
     + "cell: " + h.acts().join(","));
}

async function testNumberInputThatThrowsOnSelectionStartStillCopies() {
  // Chromium THROWS on .selectionStart for input[type=number] rather than
  // returning null. Unguarded, that would take the whole menu down.
  const h = makeHarness({
    throwOnSelection: true,
    field: { type: "number", value: "1700" },
  });
  h.fireCell();
  ok(h.acts()[0] === "copy", "a numeric cell still offers Copy");
  await h.buttons.copy.onclick();
  ok(h.written[0] === "1700", "…and copies its value: " + JSON.stringify(h.written));
}

/* ---------- everywhere else in the app ---------- */
async function testPlainFieldOutsideAGridCopies() {
  const h = makeHarness();
  const input = h.el("INPUT", { type: "text", value: "ABRAJ ENERGY SERVICES" });
  const ev = h.fireDoc(input);
  ok(ev.defaultPrevented, "a right-click on an ordinary input opens our menu");
  ok(h.acts().join(",") === "copy,cancel",
     "…offering Copy and nothing else - there is no cell to paste into: "
     + h.acts().join(","));
  await h.buttons.copy.onclick();
  ok(h.written[0] === "ABRAJ ENERGY SERVICES", "and it copies the field");
}

async function testSelectedTextAnywhereCopies() {
  const h = makeHarness({
    selection: { isCollapsed: false, toString: () => "QUT-MT-TDC-010" },
  });
  const div = h.el("DIV", { textContent: "Quotation QUT-MT-TDC-010 - 12 items" });
  h.fireDoc(div);
  await h.buttons.copy.onclick();
  ok(h.written[0] === "QUT-MT-TDC-010",
     "selected text is copied from any element, not just fields: "
     + JSON.stringify(h.written));
}

async function testBareContainerWithNoSelectionShowsNoMenu() {
  const h = makeHarness();
  const div = h.el("DIV", { textContent: "the entire analytics view, all of it" });
  const ev = h.fireDoc(div);
  ok(!ev.defaultPrevented && h.acts().length === 0,
     "no selection and no field means NO menu - falling back to textContent "
     + "would put half the page on the clipboard for a right-click on any "
     + "container div");
}

async function testPasswordFieldIsNotOffered() {
  const h = makeHarness();
  const pw = h.el("INPUT", { type: "password", value: "hunter2" });
  const ev = h.fireDoc(pw);
  ok(!ev.defaultPrevented, "a password field is never offered for copying");
  ok(h.written.length === 0, "…and nothing reaches the clipboard");
}

async function testCheckboxIsNotOffered() {
  const h = makeHarness();
  const cb = h.el("INPUT", { type: "checkbox", value: "on" });
  const ev = h.fireDoc(cb);
  ok(!ev.defaultPrevented,
     "a checkbox has no text worth copying - 'on' is not what the user meant");
}

async function testDocumentHandlerStandsDownWhenAGridAnswered() {
  const h = makeHarness();
  h.fireBoth(h.field);
  ok(h.acts().join(",") === "copy,paste,paste-rows,paste-text,cancel",
     "the tbody listener runs first, so the document listener must leave its "
     + "menu alone - otherwise every grid right-click would lose its Paste "
     + "items to a bare Copy menu: " + h.acts().join(","));
}

async function testMenuDoesNotReopenOnItself() {
  const h = makeHarness({
    selection: { isCollapsed: false, toString: () => "Paste here" },
  });
  h.fireDoc(h.field);                                  // open a menu
  const opened = h.acts().join(",");
  const inMenu = h.el("SPAN", { textContent: "Paste here" });
  inMenu.parent = h.el("DIV", { id: "gridPasteMenu" });
  const ev = h.fireDoc(inMenu);
  ok(!ev.defaultPrevented && h.acts().join(",") === opened,
     "right-clicking the menu itself must not tear it down and build a second "
     + "one on top of it");
}

Promise.resolve()
  .then(testCellCopiesWholeCellWhenNothingSelected)
  .then(testSelectionInsideTheCellWins)
  .then(testEmptyCellOffersNoCopyAtAll)
  .then(testNumberInputThatThrowsOnSelectionStartStillCopies)
  .then(testPlainFieldOutsideAGridCopies)
  .then(testSelectedTextAnywhereCopies)
  .then(testBareContainerWithNoSelectionShowsNoMenu)
  .then(testPasswordFieldIsNotOffered)
  .then(testCheckboxIsNotOffered)
  .then(testDocumentHandlerStandsDownWhenAGridAnswered)
  .then(testMenuDoesNotReopenOnItself)
  .then(() => console.log("grid_paste_copy: OK (" + checks + " checks)"))
  .catch((e) => { console.error(e.message); process.exit(1); });
