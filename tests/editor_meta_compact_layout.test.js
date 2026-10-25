const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");

function sliceBetween(startNeedle, endNeedle, label) {
  const start = html.indexOf(startNeedle);
  if (start < 0) throw new Error(`Missing ${label} start`);
  const end = html.indexOf(endNeedle, start);
  if (end < 0) throw new Error(`Missing ${label} end`);
  return html.slice(start, end);
}

function assertInOrder(block, needles, label) {
  let pos = -1;
  for (const needle of needles) {
    const next = block.indexOf(needle, pos + 1);
    if (next < 0) throw new Error(`${label}: missing ${needle}`);
    if (next < pos) throw new Error(`${label}: ${needle} is out of order`);
    pos = next;
  }
}

function nearestDivTagForInput(inputId) {
  const input = html.indexOf(`id="${inputId}"`);
  if (input < 0) throw new Error(`Missing input ${inputId}`);
  const div = html.lastIndexOf("<div", input);
  const end = html.indexOf(">", div);
  if (div < 0 || end < 0) throw new Error(`Missing wrapper div for ${inputId}`);
  return html.slice(div, end + 1);
}

function assertWrapperClass(inputId, className) {
  const tag = nearestDivTagForInput(inputId);
  const hasClass = new RegExp(`\\bclass="[^"]*\\b${className}\\b`).test(tag);
  if (!hasClass) throw new Error(`${inputId} wrapper should include ${className}`);
}

const dnActions = sliceBetween('<div class="actions" id="dnActions"', "\n    </div>", "DN actions");
assertInOrder(dnActions, ['id="btnDnFitCols"', 'id="btnDnAutofit"', 'id="btnDnPrices"', 'id="btnDnSettings"'], "DN toolbar");
if (!dnActions.includes('id="dnIncludePrices"')) {
  throw new Error("DN prices state input should live with the toolbar prices control");
}

const dnMeta = sliceBetween('<main id="dnView"', '<div class="items-wrap">', "DN meta");
if (dnMeta.includes('id="dnIncludePrices"') || dnMeta.includes("dn-toggle-field")) {
  throw new Error("DN meta strip should not carry the Show prices toggle");
}
if (!/class="[^"]*\bdn-meta-form\b/.test(dnMeta)) {
  throw new Error("DN meta form should expose compact DN layout class");
}

const invMeta = sliceBetween('<main id="invView"', '<div class="items-wrap">', "invoice meta");
assertInOrder(invMeta, ['id="invBank"', 'id="invSrcQuote"'], "invoice bank and linked quotation");
if (!/class="[^"]*\binv-meta-form\b/.test(invMeta)) {
  throw new Error("Invoice meta form should expose compact invoice layout class");
}
if (!/class="[^"]*\binv-linked-quote\b/.test(invMeta)) {
  throw new Error("Invoice linked quotation field should expose compact layout class");
}

for (const id of ["poNo", "qNo", "dnNo", "invNo"]) {
  assertWrapperClass(id, "doc-no-field");
}
for (const id of ["today", "qDate", "dnDate", "invDate"]) {
  assertWrapperClass(id, "doc-date-field");
}

console.log("editor meta compact layout tests passed");
