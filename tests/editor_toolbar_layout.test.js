const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");

function actionGroup(id) {
  const start = html.indexOf(`<div class="actions" id="${id}"`);
  if (start < 0) throw new Error(`Missing action group ${id}`);
  const end = html.indexOf("\n    </div>", start);
  if (end < 0) throw new Error(`Cannot find end of action group ${id}`);
  return html.slice(start, end);
}

function buttonIds(groupHtml) {
  return Array.from(groupHtml.matchAll(/<button\b[^>]*\bid="([^"]+)"/g), m => m[1]);
}

function relevantToolbarIds(groupHtml) {
  const skip = new Set(["btnPoBack", "btnQBack", "btnDnBack", "btnInvBack",
                        "btnQTypes", "btnInvTypes"]);
  return buttonIds(groupHtml).filter(id => !skip.has(id));
}

function assertOrder(groupId, expected) {
  const got = relevantToolbarIds(actionGroup(groupId));
  const same = got.length === expected.length && got.every((id, i) => id === expected[i]);
  if (!same) {
    throw new Error(`${groupId} order mismatch\nexpected: ${expected.join(", ")}\nactual:   ${got.join(", ")}`);
  }
}

function buttonTag(id) {
  const m = html.match(new RegExp(`<button\\b(?=[^>]*\\bid="${id}")[^>]*>`));
  if (!m) throw new Error(`Missing button ${id}`);
  return m[0];
}

function hasClass(id, className) {
  const tag = buttonTag(id);
  const m = tag.match(/\bclass="([^"]*)"/);
  return !!m && m[1].split(/\s+/).includes(className);
}

// 2026-07-24 - Export / Print / Cost buttons removed to free topbar space.
// Export: "Save" already writes the PDF, so nothing is lost.
// Print: still available per row in the right-side Open panel.
// Cost: the COST column is now always shown, so the toggle is gone.
// 2026-09-05 - btnPoMakeDn / btnPoMakeInv sit right after Save, mirroring where
// btnDnImport / btnDnInvoice sit in the DN toolbar: the cross-document buttons
// are a group, and both of these need a SAVED PO, so they belong beside it.
assertOrder("poActions", ["btnNew", "btnPoStay", "btnPoMakeDn", "btnPoMakeInv", "btnPreview", "btnPoSign", "btnPoWorkflow", "btnPoFitCols", "btnPoAutofit", "btnPoSettings"]);
assertOrder("quoteActions", ["btnQNew", "btnQStay", "btnQMasterFill", "btnQPreview", "btnQSign", "btnQFitCols", "btnQAutofit", "btnQTypeSettings"]);
assertOrder("dnActions", ["btnDnNew", "btnDnStay", "btnDnImport", "btnDnInvoice", "btnDnMasterFill", "btnDnPreview", "btnDnSign", "btnDnSignedCopy", "btnDnWorkflow", "btnDnFitCols", "btnDnAutofit", "btnDnPrices", "btnDnSettings"]);
assertOrder("invActions", ["btnInvNew", "btnInvStay", "btnInvImport", "btnInvMasterFill", "btnInvPreview", "btnInvSign", "btnInvSignedCopy", "btnInvWorkflow", "btnInvFitCols", "btnInvAutofit", "btnInvTypeSettings"]);

// The removed buttons must not creep back into any toolbar.
for (const id of ["btnSave", "btnQSave", "btnDnSave", "btnInvSave",
                  "btnPoPrint", "btnQPrint", "btnDnPrint", "btnInvPrint",
                  "btnQCosts", "btnDnCosts", "btnInvCosts"]) {
  if (html.includes(`id="${id}"`)) throw new Error(`${id} was removed from the toolbar (2026-07-24) but is back`);
}

for (const id of ["btnOpen", "btnQOpen", "btnDnOpen", "btnInvOpen"]) {
  if (html.includes(`id="${id}"`)) throw new Error(`${id} should not be in the editor toolbar`);
}

const toolbarHtml = ["poActions", "quoteActions", "dnActions", "invActions"]
  .map(id => actionGroup(id)).join("\n");
if (/Export PDF/.test(toolbarHtml)) throw new Error("Editor export buttons should say Export, not Export PDF");

for (const id of ["btnNew", "btnQNew", "btnDnNew", "btnInvNew",
                  "btnPoStay", "btnQStay", "btnDnStay", "btnInvStay",
                  "btnDnImport", "btnDnInvoice", "btnInvImport",
                  "btnPoMakeDn", "btnPoMakeInv"]) {
  if (!hasClass(id, "doc-flow-btn")) throw new Error(`${id} should use the unified workflow color`);
}

for (const id of ["btnPoStay", "btnQStay", "btnDnStay", "btnInvStay"]) {
  if (!hasClass(id, "editor-save-btn")) throw new Error(`${id} should expose saved-state styling`);
}

for (const id of ["btnPoSign", "btnQSign", "btnDnSign", "btnInvSign"]) {
  if (!hasClass(id, "sig-btn")) throw new Error(`${id} should expose signed-state styling`);
}

console.log("editor toolbar layout tests passed");
