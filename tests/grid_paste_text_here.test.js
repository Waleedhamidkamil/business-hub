const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("static/grid_paste.js", "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function makeHarness(clipboardText) {
  const listeners = {};
  const rows = [
    { item: "before", description: "keep this" },
    { item: "next", description: "also keep this" },
  ];
  const row = { dataset: { i: "0" } };
  const cell = {
    dataset: { k: "description" },
    closest(selector) {
      if (selector === "[data-k]") return cell;
      if (selector === "tr[data-i]") return row;
      return null;
    },
  };
  const tbody = {
    _gridPasteWired: false,
    addEventListener(type, fn) { listeners[type] = fn; },
    querySelector() { return null; },
    contains() { return false; },
  };
  let menu = null;
  const buttons = {};
  const context = {
    console,
    setTimeout() {},
    clearTimeout() {},
    navigator: {
      clipboard: {
        async read() {
          return [{
            types: ["text/plain"],
            async getType() {
              return { async text() { return clipboardText; } };
            },
          }];
        },
      },
    },
    document: {
      activeElement: null,
      addEventListener() {},
      removeEventListener() {},
      getElementById(id) { return id === "gridPasteMenu" ? menu : null; },
      createElement() {
        menu = {
          style: {},
          innerHTML: "",
          getBoundingClientRect() {
            return { right: 100, bottom: 100, width: 100, height: 80 };
          },
          appendChild() {},
          remove() { menu = null; },
          querySelector(selector) {
            const match = selector.match(/data-act="([^"]+)"/);
            return match ? (buttons[match[1]] ||= {}) : null;
          },
        };
        return menu;
      },
      body: { appendChild() {} },
    },
    window: { innerWidth: 1200, innerHeight: 800, ICON: {}, toast() {} },
  };
  context.window.document = context.document;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "static/grid_paste.js" });

  context.window.setupGridPaste(tbody, {
    columns: () => [
      { key: "item", kind: "text" },
      { key: "description", kind: "text" },
    ],
    rowCount: () => rows.length,
    addEmptyRow: () => rows.push({ item: "", description: "" }),
    setCell: (i, key, value) => { rows[i][key] = value; },
    fullRender() {},
    snapshot: () => JSON.parse(JSON.stringify(rows)),
    restore(snapshot) {
      rows.length = 0;
      snapshot.forEach(r => rows.push(r));
    },
  });

  return {
    rows,
    fireContextMenu() {
      listeners.contextmenu({
        target: cell,
        clientX: 10,
        clientY: 20,
        preventDefault() {},
      });
    },
    buttons,
  };
}

async function testPasteTextHereKeepsMultilineClipboardInOneCell() {
  const h = makeHarness("Line one\nLine two\nLine three");
  h.fireContextMenu();

  assert(h.buttons["paste-text"], "right-click menu must expose Paste text here");
  await h.buttons["paste-text"].onclick();

  assert(h.rows[0].description === "Line one\nLine two\nLine three",
    "Paste text here must keep all clipboard lines in the anchor cell");
  assert(h.rows.length === 2,
    "Paste text here must not append rows for multiline clipboard text");
  assert(h.rows[0].item === "before" && h.rows[1].description === "also keep this",
    "Paste text here must not alter neighboring cells");
}

async function testPasteTextHereDoesNotUseGridParsingOrNumericCoercion() {
  const h = makeHarness("001\t002\n003\t004");
  h.fireContextMenu();

  await h.buttons["paste-text"].onclick();

  assert(h.rows[0].description === "001\t002\n003\t004",
    "Paste text here must preserve tabs and newlines as text in one cell");
  assert(typeof h.rows[0].description === "string",
    "Paste text here must not coerce clipboard text into a number");
  assert(h.rows[1].item === "next" && h.rows[1].description === "also keep this",
    "Paste text here must not use grid row or column traversal");
}

Promise.resolve()
  .then(testPasteTextHereKeepsMultilineClipboardInOneCell)
  .then(testPasteTextHereDoesNotUseGridParsingOrNumericCoercion)
  .then(() => console.log("Grid paste text-here tests passed"));
