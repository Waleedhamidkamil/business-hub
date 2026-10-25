/* App-only QUT/INV drafts must be obvious and must expose no PDF action. */
const fs = require("fs");

function assert(c, m) { if (!c) throw new Error(m); }

const app = fs.readFileSync("static/app.js", "utf8");
const quote = fs.readFileSync("static/quote.js", "utf8");
const inv = fs.readFileSync("static/inv.js", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

assert(/incomplete-price-pill/.test(app), "shared panel status must render the warning pill");
assert(/>!<|>!\s/.test(app), "the warning pill must visibly contain !");
assert(/\.incomplete-price-pill\s*\{/.test(css), "the warning pill needs an owned style");

for (const [name, src] of [["quotation", quote], ["invoice", inv]]) {
  assert(/incomplete_price/.test(src), `${name} panel must branch on incomplete price state`);
  assert(/hasPdf/.test(src), `${name} panel must explicitly decide whether a PDF exists`);
  assert(/pdfActions/.test(src), `${name} panel must omit open/folder/copy actions without a PDF`);
}

console.log("incomplete_price_panel: OK");
