const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const app = fs.readFileSync("static/app.js", "utf8");
const dn = fs.readFileSync("static/dn.js", "utf8");
const inv = fs.readFileSync("static/inv.js", "utf8");
const workflow = fs.existsSync("static/document_workflow.js")
  ? fs.readFileSync("static/document_workflow.js", "utf8") : "";
const poLink = fs.readFileSync("static/po_link.js", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const dark = fs.readFileSync("static/dark.css", "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

for (const id of ["btnPoWorkflow", "btnDnWorkflow", "btnInvWorkflow"]) {
  assert(html.includes(`id="${id}"`), `${id} must exist in its editor toolbar`);
}
assert(!html.includes('id="btnQWorkflow"'), "quotation must not get Workflow");

const poLinkScript = html.indexOf('src="../static/po_link.js"');
const workflowScript = html.indexOf('src="../static/document_workflow.js"');
const openPanelScript = html.indexOf('src="../static/open_panel.js"');
assert(poLinkScript >= 0 && workflowScript > poLinkScript,
  "document workflow must load after the existing PO picker module");
assert(openPanelScript > workflowScript,
  "document workflow must load before the last-loaded open panel controller");

assert(workflow.includes("document_workflow"), "popup must load the unified backend workflow");
assert(workflow.includes("openPoFulfillPicker"), "unlinked DN/INV must reuse the PO picker");
assert(workflow.includes("openPoSourcePicker"), "missing DN/invoice stages must reuse source pickers");
assert(workflow.includes("openDocumentWorkflow"), "shared popup entry point must be exported");
assert(workflow.includes("PO") && workflow.includes("Delivery") && workflow.includes("Invoice"),
  "the three workflow stages must be visible");
assert(workflow.includes("data-manage-kind"), "linked workflow nodes need a Manage-link control");
assert(workflow.includes("Remove link") && workflow.includes("Replace"),
  "Manage link must offer remove and replace actions");
assert(workflow.includes("clear_po_fulfillment_link"),
  "workflow removal must call the exact backend relationship delete");
assert(/onAfter[\s\S]*clear_po_fulfillment_link/.test(workflow),
  "replacement must clear the old link only from the successful picker callback");
assert(workflow.includes("delivery_notes") && workflow.includes("direct_item_count"),
  "invoice workflow nodes must render multi-DN and direct-to-PO contributions");
assert(workflow.includes("Direct to PO"),
  "invoice workflow nodes need a direct-to-PO fallback");
assert(workflow.includes("data-add-dn"),
  "every invoice workflow node needs an Add delivery note action");
assert(/openPoFulfillPicker\([\s\S]*initialTab:\s*["']dn["']/.test(workflow),
  "Add delivery note must reuse the invoice picker's linked-DN tab");

assert(poLink.includes('data-tab="available"'), "every picker needs an Available tab");
assert(poLink.includes('data-tab="linked"'), "every picker needs a separate Linked tab");
assert(poLink.includes('data-tab="dn"'), "invoice picker must preserve its linked-DN route tab");
assert(poLink.includes("Available POs") && poLink.includes("Linked POs"),
  "DN/invoice-to-PO picker tabs need explicit labels");
assert(poLink.includes("Available DNs") && poLink.includes("Linked DNs"),
  "PO-to-DN picker tabs need explicit labels");
assert(poLink.includes("Available Invoices") && poLink.includes("Linked Invoices"),
  "PO-to-invoice picker tabs need explicit labels");
assert(poLink.includes("document_workflow(sourceKind, sourceNo)"),
  "linked PO tabs must merge authoritative links beyond the recent candidate limit");
assert(poLink.includes('document_workflow("po", poNo)'),
  "linked DN/invoice tabs must merge authoritative links beyond the recent candidate limit");
assert(poLink.includes("opts.excludeNo") && workflow.includes("excludeNo:"),
  "replacement pickers must exclude the old relationship to prevent self-replacement deletion");

assert(app.includes("btnPoWorkflow") && app.includes("openDocumentWorkflow"),
  "PO editor must wire its saved document to Workflow");
assert(dn.includes("btnDnWorkflow") && dn.includes("openDocumentWorkflow"),
  "DN editor must wire its saved document to Workflow");
assert(inv.includes("btnInvWorkflow") && inv.includes("openDocumentWorkflow"),
  "invoice editor must wire its saved document to Workflow");

assert(css.includes(".doc-workflow-modal") && css.includes(".doc-workflow-lane"),
  "workflow popup needs light-theme layout styles");
assert(css.includes(".doc-workflow-invoice-links") && css.includes(".doc-workflow-add-dn"),
  "invoice contribution details need stable light-theme layout styles");
assert(dark.includes(".doc-workflow-modal"), "workflow popup needs dark-theme styles");
assert(dark.includes(".doc-workflow-invoice-links"),
  "invoice contribution details need dark-theme styling");

console.log("document workflow frontend tests passed");
