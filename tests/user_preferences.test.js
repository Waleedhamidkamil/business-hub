const fs = require("fs");
const vm = require("vm");

function makeElement(value = "") {
  return {
    value,
    checked: false,
    hidden: false,
    textContent: "",
    innerHTML: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
  };
}

const storage = new Map();
const context = {
  console,
  setTimeout() {},
  clearTimeout() {},
  requestAnimationFrame(fn) { fn(); },
  window: {
    addEventListener() {},
    matchMedia() { return { matches: false }; },
  },
  document: {
    readyState: "loading",
    addEventListener() {},
    documentElement: {
      setAttribute() {},
      getAttribute() { return "light"; },
    },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    getElementById() { return makeElement(); },
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
    createElement() { return makeElement(); },
  },
  localStorage: {
    getItem(k) { return storage.has(k) ? storage.get(k) : null; },
    setItem(k, v) { storage.set(k, String(v)); },
  },
};
context.window.localStorage = context.localStorage;
context.window.document = context.document;

vm.createContext(context);
vm.runInContext(fs.readFileSync("static/app.js", "utf8"), context, { filename: "static/app.js" });

context.window.__userPreferences = {
  theme: "dark",
  after_export: "open",
  compact_tables: true,
  dn_prices_default: false,
};

if (vm.runInContext('window.prefGet("theme", "light")', context) !== "dark") {
  throw new Error("prefGet should prefer file-backed user preferences over localStorage/defaults");
}
if (vm.runInContext('window.prefBool("compact_tables", false)', context) !== true) {
  throw new Error("prefBool should understand boolean user preferences");
}

const elements = {
  set_documents_root: makeElement("D:\\PO TOOL"),
  set_po_root: makeElement(""),
  set_ui_lang: makeElement("en"),
};
context.document.getElementById = (id) => elements[id] || makeElement();

const payload = vm.runInContext("collectSettingsForm()", context);
if (!payload.preferences || payload.preferences.theme !== "dark") {
  throw new Error("settings save payload should include the current theme preference");
}
if (payload.preferences.after_export !== "open" || payload.preferences.compact_tables !== true) {
  throw new Error("settings save payload should include all user preferences");
}

console.log("user preference frontend tests passed");
