const fs = require("fs");
const vm = require("vm");

const app = fs.readFileSync("static/app.js", "utf8");
const quote = fs.readFileSync("static/quote.js", "utf8");
const dn = fs.readFileSync("static/dn.js", "utf8");
const inv = fs.readFileSync("static/inv.js", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const dark = fs.readFileSync("static/dark.css", "utf8");

function assert(cond, message) {
  if (!cond) throw new Error(message);
}

assert(!html.includes('id="docSubmittedChip"'), "editor topbar must not contain a Submitted pill");
assert(!app.includes("setSubmittedChip"), "Submitted topbar helper must be removed with the pill");
assert(!app.includes("submittedPillHTML"), "Submitted card-pill helper must be removed");
assert(!app.includes('querySelector(".submitted-pill")'), "card updates must not retain legacy Submitted-pill plumbing");
assert(!quote.includes("submittedPillHTML(q.submitted)"), "quotation cards must not render Submitted pills");
assert(!dn.includes("submittedPillHTML(d.submitted)"), "DN cards must not render Submitted pills");
assert(!inv.includes("submittedPillHTML(d.submitted)"), "invoice cards must not render Submitted pills");

assert(/panelStatusHTML\("quote",\s*q\)/.test(quote), "quotation cards must use workflow status precedence");
assert(/panelStatusHTML\("dn",\s*d\)/.test(dn), "DN cards must use workflow status precedence");
assert(/panelStatusHTML\("inv",\s*d\)/.test(inv), "invoice cards must use workflow status precedence");
assert(/panelStatusHTML\("po",\s*p,/.test(app), "PO cards must use workflow status precedence");

const helperStart = app.indexOf("window.linkedPillHTML = function");
const helperEnd = app.indexOf("window.submitBtnHTML = function", helperStart);
assert(helperStart >= 0 && helperEnd > helperStart, "shared panel status helpers must exist");

const context = {
  window: {
    signBadgeHTML(signed) {
      return signed ? '<span class="sign-badge is-signed">Signed</span>' : '<span class="sign-badge not-signed">Unsigned</span>';
    },
  },
};
vm.createContext(context);
vm.runInContext(app.slice(helperStart, helperEnd), context);

const status = context.window.panelStatusHTML;
assert(typeof status === "function", "panelStatusHTML must be exported");
assert(status("quote", { signed: true, submitted: false }).includes("Signed"), "draft signed quotation should show Signed");
assert(status("quote", { signed: true, submitted: true }) === "", "submitted quotation should suppress Signed");
assert(status("dn", { signed: true, submitted: true, linked: true }).includes("Linked"), "linked DN should show Linked");
assert(!status("dn", { signed: true, submitted: true, linked: true }).includes("Signed"), "linked DN should suppress Signed");
assert(status("inv", { signed: true, submitted: true, paid: true, linked: false }).includes("Paid"), "paid invoice should show Paid");
const linkedPaidInvoice = status("inv", { signed: true, submitted: true, paid: true, linked: true });
assert(linkedPaidInvoice.includes("Linked"), "linked paid invoice should show Linked");
assert(linkedPaidInvoice.includes("Paid"), "linked paid invoice should keep its Paid pill");
assert(!linkedPaidInvoice.includes("Signed"), "linked paid invoice should still suppress Signed");
assert(status("po", { signed: true, submitted: true, delivered: "full", invoiced: "none", paid: "none" }).includes("Delivered"), "delivered PO should show Delivered");
assert(status("po", { signed: true, submitted: true, delivered: "full", invoiced: "full", paid: "none" }).includes("Invoiced"), "linked unpaid invoice should make the PO Invoiced");
assert(status("po", { signed: true, submitted: true, delivered: "full", invoiced: "full", paid: "partial" }).includes("Partly paid"), "PO Paid must supersede Invoiced and Delivered");
assert(!status("po", { signed: true, submitted: true, delivered: "full", invoiced: "full", paid: "partial" }).includes("Invoiced"), "paid PO must show only its latest stage");

assert(css.includes(".linked-pill"), "Linked pill needs light-theme styling");
assert(dark.includes(".linked-pill"), "Linked pill needs dark-theme styling");
assert(/\.linked-pill\s*\{[^}]*#2563EB/is.test(css), "Linked pill should use the blue status family in light mode");
assert(/\.linked-pill\s*\{[^}]*#3B82F6/is.test(dark), "Linked pill should use the blue status family in dark mode");
assert(css.includes(".fulfill-pill.is-invoiced"), "Invoiced PO pill needs dedicated styling");
assert(css.includes("po-row-invoiced"), "invoiced PO items need an editor-row state");

console.log("document status precedence frontend tests passed");
