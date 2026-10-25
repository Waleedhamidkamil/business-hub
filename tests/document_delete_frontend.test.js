const fs = require("fs");
const vm = require("vm");

function makeElement() {
  return {
    value: "",
    checked: false,
    hidden: false,
    disabled: false,
    textContent: "",
    innerHTML: "",
    dataset: {},
    style: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    setAttribute() {},
    getAttribute() { return ""; },
    appendChild() {},
    remove() {},
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
    getBoundingClientRect() { return { width: 100, height: 30, top: 0, left: 0, bottom: 30 }; },
  };
}

let apiCall = null;

const context = {
  console,
  setTimeout() {},
  clearTimeout() {},
  requestAnimationFrame(fn) { fn(); },
  cancelAnimationFrame() {},
  getComputedStyle() { return { getPropertyValue() { return ""; } }; },
  localStorage: { getItem() { return null; }, setItem() {} },
  window: {
    addEventListener() {},
    matchMedia() { return { matches: false }; },
    localStorage: { getItem() { return null; }, setItem() {} },
    pywebview: {
      api: {
        delete_document: async (kind, docNo, path) => {
          apiCall = { kind, docNo, path };
          return { ok: true, recycle_path: "RECYCLE/QUT-1.pdf" };
        },
        soft_delete_file: async () => {
          throw new Error("document delete helper must not call file-only delete");
        },
      },
    },
  },
  document: {
    readyState: "loading",
    addEventListener() {},
    documentElement: { getAttribute() { return "light"; }, setAttribute() {} },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    getElementById() { return makeElement(); },
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
    createElement() { return makeElement(); },
  },
};
context.window.document = context.document;
context.window.localStorage = context.localStorage;

vm.createContext(context);
vm.runInContext(fs.readFileSync("static/app.js", "utf8"), context, { filename: "static/app.js" });
vm.runInContext(`
  showModal = async (opts) => {
    window.__modalBody = opts.body || "";
    return "ok";
  };
  toast = (msg) => { window.__toastMessage = msg; };
`, context);

context.window.__toastMessage = "";
context.window.__modalBody = "";

async function main() {
  await vm.runInContext(`
    window.confirmAndDeleteDocument("quote", "QUT-1", "D:/Docs/QUT-1.pdf", "QUT-1", () => {
      window.__refreshed = true;
    });
  `, context);

  const refreshed = context.window.__refreshed === true;
  const toastMessage = context.window.__toastMessage || "";
  const modalBody = context.window.__modalBody || "";
  if (!apiCall) {
    throw new Error("confirmAndDeleteDocument should call the document delete API");
  }
  if (apiCall.kind !== "quote" || apiCall.docNo !== "QUT-1" || apiCall.path !== "D:/Docs/QUT-1.pdf") {
    throw new Error(`delete_document called with wrong arguments: ${JSON.stringify(apiCall)}`);
  }
  if (!refreshed) {
    throw new Error("confirmAndDeleteDocument should refresh after successful delete");
  }
  if (!/record/i.test(modalBody) || !/Costing/i.test(modalBody)) {
    throw new Error("delete confirmation should explain that the saved document record is removed too");
  }
  if (!/RECYCLE/i.test(toastMessage)) {
    throw new Error("successful delete should tell the user the PDF moved to RECYCLE");
  }
  console.log("document delete frontend tests passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
