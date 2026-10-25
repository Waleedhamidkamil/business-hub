const fs = require("fs");

const app = fs.readFileSync("static/app.js", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

// 2026-08-24 - the PO strip no longer exposes the quotation-link or fulfillment-link
// fields (removed for a cleaner information row). The LINK ITSELF still lives on the
// record: state.sourceQuote is loaded from and saved back as source_quote_no, so
// existing PO->quotation links survive an open/save round-trip untouched.
assert(!html.includes('id="poSrcQuote"'), "PO strip should no longer show the quotation-link field");
assert(!html.includes('id="btnPoLinkDn"') && !html.includes('id="btnPoLinkInv"'),
  "PO strip should no longer show the DN/Invoice fulfillment-link buttons");

assert(/sourceQuote:\s*""/.test(app), "PO state should track the linked quotation number");
assert(/function\s+setPoSourceQuote/.test(app), "PO editor should centralize quote-link field updates");
assert(/function\s+openPoQuotePicker/.test(app), "PO editor should expose a quotation picker popup");
assert(/list_quote_refs/.test(app), "PO quotation picker should load saved quotations from the backend");
assert(/source_quote_no/.test(app), "PO save/update calls should persist source_quote_no");
assert(/submitBtnHTML\("po"/.test(app), "PO panel rows should render the shared Submit/Submitted button");
assert(/wireSubmitButtons\(box,\s*list,\s*"po_no"\)/.test(app), "PO panel should wire submitted toggles into the shared handler");
assert(/panelStatusHTML\("po",\s*p,/.test(app), "PO panel rows should use workflow status precedence");
assert(!app.includes("setSubmittedChip"), "PO editor should not retain the removed Submitted topbar chip");

assert(!css.includes(".po-quote-control"), "dead PO quote-control styling should be removed with the field");
assert(!css.includes(".po-link-cta"), "dead PO fulfillment-link button styling should be removed with the buttons");

console.log("PO quote-link panel tests passed");
