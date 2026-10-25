const fs = require("fs");
const vm = require("vm");

const code = fs.readFileSync("static/doc_settings.js", "utf8") + `
(() => {
  function input(value, checked = false) {
    return { value, checked };
  }
  const fields = {
    "#po_min_row": input(""),
    "#po_line_h": input("6.5"),
    "#po_header_mm": input(""),
    "#po_sig_mm": input(""),
    "#po_autofit": input("", true),
  };
  const root = { querySelector(selector) { return fields[selector]; } };
  const layout = poLayoutPatch(root);

  for (const key of ["table_min_row_mm", "table_header_mm", "signature_mm"]) {
    if (layout[key] !== "") {
      throw new Error(key + " must be cleared so the backend restores Auto");
    }
  }
  if (layout.table_line_height_mm !== 6.5) {
    throw new Error("a typed PO layout value was not kept as a number");
  }
  if (layout.autofit_rows !== true) {
    throw new Error("PO Auto-fit checkbox was not preserved");
  }
})();
`;

const context = {
  console,
  window: {},
  document: { addEventListener() {}, readyState: "loading" },
  api() { return {}; },
  toast() {},
  safe(fn) { return fn; },
  esc(value) { return String(value ?? ""); },
};
vm.createContext(context);
vm.runInContext(code, context, { filename: "static/doc_settings.js" });

const app = fs.readFileSync("static/app.js", "utf8");
/* The layout argument must be there; what follows it is not this test's
   business. The closing bracket used to be part of the pattern, so adding
   `col_widths` as a fourth argument on 2026-09-28 - which the page count needs,
   because a narrower column wraps sooner and changes the answer - failed a test
   about Auto-fit. Pinning an argument LIST makes every later argument a
   breakage. */
if (!/po_page_count\(\s*state\.poNo,\s*filled,\s*editorLayoutPayload\(state\.autofitRows\)/s.test(app)) {
  throw new Error("the PO page-count note does not send the current Auto-fit layout");
}
/* The dragged column widths go with it too (2026-09-28). A narrower column
   wraps sooner and needs more pages, so counting at the default widths told
   the user "1 page" for a PDF that printed on two. */
if (!/po_page_count\([\s\S]{0,160}?poColWidths\(\)/.test(app)) {
  throw new Error("the PO page-count note does not send the live column widths");
}

const toggle = app.slice(app.indexOf("if (btnPoAutofit)"), app.indexOf("if (window.wireFitColsButton)"));
if (!toggle.includes("await updateSplitWarning()")) {
  throw new Error("toggling PO Auto-fit does not refresh the visible page count");
}

console.log("PO settings blank-as-Auto tests passed");
