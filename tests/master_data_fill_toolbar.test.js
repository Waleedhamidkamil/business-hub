const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const source = {
  quote: fs.readFileSync("static/quote.js", "utf8"),
  dn: fs.readFileSync("static/dn.js", "utf8"),
  inv: fs.readFileSync("static/inv.js", "utf8"),
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

for (const id of ["btnQMasterFill", "btnDnMasterFill", "btnInvMasterFill"]) {
  assert(html.includes(`id="${id}"`), `toolbar button ${id} is missing`);
  assert(html.includes(`id="${id}"`) && html.includes("Master Data"),
    `${id} must be labelled Master Data`);
}
assert(html.indexOf('<script src="../static/master_data_fill.js"></script>') <
       html.indexOf('<script src="../static/quote.js"></script>'),
  "shared Master Data fill module must load before the editors");
assert(source.quote.includes("btnQMasterFill") && source.quote.includes("docType: \"qut\""),
  "quotation must wire the Master Data button");
assert(source.dn.includes("btnDnMasterFill") && source.dn.includes("docType: \"dn\""),
  "DN must wire the Master Data button");
assert(source.inv.includes("btnInvMasterFill") && source.inv.includes("docType: \"inv\""),
  "invoice must wire the Master Data button");

console.log("Master Data toolbar tests passed");
