const fs = require("fs");

const inv = fs.readFileSync("static/inv.js", "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(/sourceDn:\s*""/.test(inv),
  "invoice draft state must track the transient source DN");
assert(/source_dn_no:\s*invState\.sourceDn\s*\|\|\s*null/.test(inv),
  "invoice save payload must carry the source DN");
assert(/async function importDnIntoInv\(dnNo,\s*opts\s*=\s*\{\}\)/.test(inv),
  "DN import must accept explicit workflow-link intent");
assert(/invState\.sourceDn\s*=\s*opts\.linkWorkflow\s*\?\s*dnNo\s*:\s*""/.test(inv),
  "ordinary imports must clear source-DN workflow intent");
assert(/createInvoiceFromDnNo[\s\S]*importDnIntoInv\(dnNo,\s*\{\s*linkWorkflow:\s*true\s*\}\)/.test(inv),
  "only Make Invoice must opt into automatic DN workflow linking");
assert(inv.includes("workflow_link") && inv.includes("no_po_links") && inv.includes("conflict_count"),
  "invoice save must surface no-link and conflict outcomes");
assert((inv.match(/invState\.sourceDn\s*=\s*""/g) || []).length >= 3,
  "new, loaded, and successfully saved invoices must clear transient DN state");

console.log("DN Make Invoice workflow frontend tests passed");
