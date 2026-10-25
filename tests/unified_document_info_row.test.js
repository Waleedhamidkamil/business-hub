const fs = require("fs");
const html = fs.readFileSync("templates/index.html", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function openingTag(id) {
  const at = html.indexOf(`id="${id}"`);
  assert(at >= 0, `missing #${id}`);
  const start = html.lastIndexOf("<", at);
  const end = html.indexOf(">", at);
  return html.slice(start, end + 1);
}

function hasClass(tag, name) {
  return new RegExp(`class="[^"]*\\b${name}\\b`).test(tag);
}

for (const id of ["poStrip", "quoteMeta", "dnMeta", "invMeta"]) {
  assert(hasClass(openingTag(id), "doc-info-row"), `${id} must use doc-info-row`);
}

for (const id of [
  "poNo", "poReference", "today",
  "qNo", "qDate", "qReference", "qClient", "qRfq", "qAddress",
  "dnNo", "dnDate", "dnReference", "dnClient", "dnLpo", "dnAddress", "dnSrcQuote",
  "invNo", "invDate", "invReference", "invClient", "invLpo", "invAddress", "invBank", "invSrcQuote",
]) {
  assert(hasClass(openingTag(id), "doc-info-control"), `${id} must use doc-info-control`);
}

assert(css.includes("--doc-info-control-h: 34px"), "shared control height token missing");
assert(/\.doc-info-row\s*\{[\s\S]*display:\s*grid[\s\S]*column-gap:\s*10px/.test(css),
  "shared information row must be a 10px-gap grid");
assert(/\.doc-info-field\s*\{[\s\S]*min-width:\s*0[\s\S]*gap:\s*4px/.test(css),
  "shared fields must align label/control stacks");
assert(/\.doc-info-control[\s\S]*height:\s*var\(--doc-info-control-h\)/.test(css),
  "controls must consume the shared height token");
assert(!/@media[\s\S]*\.q-meta-form\.dn-meta-form[\s\S]*repeat\(auto-fit/.test(css),
  "responsive CSS must not wrap DN/INV metadata into auto-fit rows");

// The PO strip sizes its three fields explicitly and ends with a filler track so
// the band keeps its full-width bottom border (see app_styles.css).
const poTracks = css.match(/\.po-strip\.doc-info-row\s*\{[\s\S]*?grid-template-columns:([^;]+);/);
assert(poTracks, ".po-strip.doc-info-row track rule missing");
assert((poTracks[1].match(/minmax\(/g) || []).length === 3,
  "PO strip must size its three information fields");
assert(/1fr\s*$/.test(poTracks[1].replace(/\/\*[\s\S]*?\*\//g, "").trim()),
  "PO strip must end with a filler track so the band stays full width");

const tracks = {
  ".quote-meta-form.doc-info-row": 6,
  ".dn-meta-form.doc-info-row": 7,
  ".inv-meta-form.doc-info-row": 8,
};
for (const [selector, count] of Object.entries(tracks)) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{[\\s\\S]*?grid-template-columns:([^;]+);`));
  assert(match, `${selector} track rule missing`);
  assert((match[1].match(/fr\b/g) || []).length === count,
    `${selector} must have ${count} proportional tracks`);
}

console.log("unified document information row tests passed");
