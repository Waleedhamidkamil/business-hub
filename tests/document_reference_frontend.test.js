/* Internal document references (2026-08-24).

   References are app-only metadata.  This source contract protects the four
   editor lifecycles because the modules are browser globals and each owns its
   own New / load / Duplicate implementation.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const read = (p) => fs.readFileSync(p, "utf8").replace(/\r\n/g, "\n");
const html = read("templates/index.html");
const sources = {
  po: read("static/app.js"),
  quote: read("static/quote.js"),
  dn: read("static/dn.js"),
  inv: read("static/inv.js"),
};

const fields = {
  po: "poReference",
  quote: "qReference",
  dn: "dnReference",
  inv: "invReference",
};

function functionBody(src, name, nextMarker = "\n}") {
  const at = src.indexOf(`function ${name}`);
  assert(at >= 0, `${name} is missing`);
  const end = src.indexOf(nextMarker, at);
  assert(end >= 0, `${name} has no closing marker`);
  return src.slice(at, end + nextMarker.length);
}

/* ---------- 1. same-sized Reference field exists in every editor ---------- */
for (const [kind, id] of Object.entries(fields)) {
  const at = html.indexOf(`id="${id}"`);
  assert(at >= 0, `${kind} editor is missing #${id}`);
  const around = html.slice(Math.max(0, at - 220), at + 180);
  assert(/(?:<label>|class="label">)Reference<\//.test(around),
    `${kind} field must be labelled exactly Reference`);
  assert(/type="text"/.test(around), `${kind} Reference must be a text input`);
}

/* ---------- 2. save payloads carry trimmed app-only metadata ---------- */
assert(functionBody(sources.quote, "buildQuotePayload").includes(
  'reference: document.getElementById("qReference").value.trim()'),
  "quotation payload must include trimmed Reference");
assert(functionBody(sources.dn, "buildDnPayload").includes(
  'reference: document.getElementById("dnReference").value.trim()'),
  "DN payload must include trimmed Reference");
assert(functionBody(sources.inv, "buildInvPayload").includes(
  'reference: document.getElementById("invReference").value.trim()'),
  "invoice payload must include trimmed Reference");

const poSave = functionBody(sources.po, "savePO", "\n}\n");
assert(poSave.includes('document.getElementById("poReference").value.trim()'),
  "PO save and update must include trimmed Reference");

/* ---------- 3. load restores, New and Duplicate clear ---------- */
const lifecycles = {
  po: ["loadForEdit", "resetEditor", "poMakeDuplicate"],
  quote: ["loadQuoteForEdit", "initNewQuote", "quoteMakeDuplicate"],
  dn: ["loadDnForEdit", "initNewDn", "dnMakeDuplicate"],
  inv: ["loadInvForEdit", "initNewInv", "invMakeDuplicate"],
};

for (const [kind, [load, fresh, duplicate]] of Object.entries(lifecycles)) {
  const src = sources[kind];
  const id = fields[kind];
  const loadBody = functionBody(src, load, "\n}\n");
  const freshBody = functionBody(src, fresh, "\n}\n");
  const duplicateBody = functionBody(src, duplicate, "\n}\n");
  assert(loadBody.includes(`document.getElementById("${id}").value = rec.reference || ""`),
    `${kind} load must restore Reference`);
  assert(freshBody.includes(`document.getElementById("${id}").value = ""`),
    `${kind} New must clear Reference`);
  assert(duplicateBody.includes(`document.getElementById("${id}").value = ""`),
    `${kind} Duplicate must clear Reference`);
}

/* ---------- 4. typing Reference marks the active editor dirty ---------- */
const dirtySignals = {
  po: "state.dirtySinceSave = true",
  quote: "quoteState.dirty = true",
  dn: "dnState.dirty = true",
  inv: "invState.dirty = true",
};
for (const [kind, id] of Object.entries(fields)) {
  const src = sources[kind];
  const at = src.indexOf(`document.getElementById("${id}").addEventListener("input"`);
  assert(at >= 0, `${kind} Reference input must have a dirty-state listener`);
  assert(src.slice(at, at + 180).includes(dirtySignals[kind]),
    `${kind} Reference listener must mark the editor dirty`);
}

console.log("document reference frontend lifecycle tests passed");

/* ---------- 5. compact cards share one safe reference renderer ---------- */
const helperStart = sources.po.indexOf("window.panelCardMainHTML = function");
assert(helperStart >= 0, "shared panelCardMainHTML renderer is missing");
const helperEnd = sources.po.indexOf("\n};", helperStart);
assert(helperEnd > helperStart, "panelCardMainHTML renderer has no end");
const helperSource = sources.po.slice(helperStart, helperEnd + 3);
const makeRenderer = new Function("window", "esc", `${helperSource}; return window.panelCardMainHTML;`);
const panelCardMainHTML = makeRenderer({}, (value) => String(value ?? "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;"));

const hostileRef = '<load id="reference"> & remember this very long internal note';
const rendered = panelCardMainHTML({
  no: "QUT-123", date: "2026-08-24", reference: hostileRef,
  amount: "$1,250.00", statuses: '<span class="status-test">Saved</span>',
});
assert(rendered.includes("panel-row-main") && rendered.includes("panel-row-head"),
  "shared renderer must produce the compact card hierarchy");
assert(rendered.includes("panel-row-reference"), "non-empty Reference must be shown");
assert(rendered.includes("title=\"&lt;load id=&quot;reference&quot;&gt; &amp; remember"),
  "full Reference must be HTML-escaped in the hover tooltip");
assert(!rendered.includes(hostileRef), "Reference HTML must never be injected raw");
assert(rendered.includes("status-test"), "status pills must remain below the heading");
assert(rendered.includes("$1,250.00"), "invoice amount slot must remain available");
assert(!panelCardMainHTML({ no: "PO-1", date: "2026-08-24", reference: "" })
  .includes("panel-row-reference"), "empty Reference must not leave a blank card row");

const cardFunctions = {
  po: functionBody(sources.po, "refreshPoList", "\n}\n"),
  quote: functionBody(sources.quote, "renderQuoteListFiltered", "\n}\n"),
  dn: functionBody(sources.dn, "renderDnList", "\n}\n"),
  inv: functionBody(sources.inv, "renderInvListFiltered", "\n}\n"),
};
for (const [kind, body] of Object.entries(cardFunctions)) {
  assert(body.includes("window.panelCardMainHTML({"),
    `${kind} right-panel cards must use the shared compact renderer`);
  assert(/reference:\s*\w+\.reference/.test(body),
    `${kind} right-panel cards must pass the saved Reference`);
  assert(body.includes("statuses: statusHtml"),
    `${kind} right-panel cards must keep status pills below the name`);
}
assert(!cardFunctions.po.includes("p.filename"), "PO cards must not show the PDF filename");
assert(!cardFunctions.quote.includes("typeChip") && !cardFunctions.quote.includes('class="qt-chip"'),
  "quotation cards must not show a redundant type chip");

const css = read("static/app_styles.css");
/* 2026-09-05 - this asserted the literal string ".po-strip .field.doc-reference-field".
   The unified document-info row (2026-08-25) restyled the strip onto
   `.doc-info-field`, so the spelling changed while the PROPERTY it guards -
   Reference is framed exactly like the fields either side of it - stayed true.
   Ten days red for a rename. Assert the property: the reference field must be
   styled inside .po-strip, and in the SAME rule as its neighbours, which is what
   "the same treatment" actually means. */
const refRule = css.split(/[{}]/).find(
  (sel) => /\.po-strip[^,{]*\.doc-reference-field/.test(sel));
assert(refRule, "PO Reference is not styled inside .po-strip at all");
assert(/doc-no-field/.test(refRule) && /doc-date-field/.test(refRule),
  "PO Reference must share a rule with the doc-number and date fields - that is "
  + "what gives it the same framed information-field treatment. Found: "
  + String(refRule).trim().slice(0, 120));
assert(css.includes(".po-strip .doc-reference-field .value"),
  "PO Reference input needs the same compact input sizing as its neighbours");

/* 2026-09-05 - this looked the rule up by the literal selector
   ".q-meta-form.dn-meta-form". The unified document-information row
   (2026-08-25) re-composed those selectors as ".dn-meta-form.doc-info-row", so
   the lookup missed and this file failed on a rule that was present and
   correct - DN still declares 7 tracks and INV 8. Find the rule by the type
   CLASS, whatever it is combined with: the track COUNT is the property worth
   pinning, not how the selector happens to be spelled this month. */
function gridTrackCount(typeClass) {
  const re = new RegExp(
    "([^{}]*\\." + typeClass + "[^{}]*)\\{[^}]*?grid-template-columns:([\\s\\S]*?)\\}");
  const match = css.match(re);
  assert(match, `.${typeClass} declares no grid-template-columns rule`);
  return (match[2].match(/minmax\(/g) || []).length;
}
assert(gridTrackCount("dn-meta-form") === 7,
  "DN metadata grid needs seven tracks after adding Reference");
assert(gridTrackCount("inv-meta-form") === 8,
  "invoice metadata grid needs eight tracks after adding Reference");

/* Finding the grid rule by type class alone (above) deliberately ignores what
   it is combined with - which would also ignore the row dropping out of the
   shared document-information system altogether. That is a real property, so
   it gets its own check, against the MARKUP where it is actually decided:
   QUT / DN / INV all render one non-wrapping information row, and all three
   must stay on it or their bars stop aligning ([[08-STYLES-TEMPLATES]]). */
const indexHtml = read("templates/index.html");
for (const [kind, id] of [["quotation", "quoteMeta"], ["DN", "dnMeta"], ["invoice", "invMeta"]]) {
  const row = indexHtml.match(new RegExp(`<div class="([^"]*)"[^>]*id="${id}"`));
  assert(row, `${kind}: no metadata row with id="${id}"`);
  assert(/\bdoc-info-row\b/.test(row[1]),
    `${kind} metadata row must carry doc-info-row - it is what makes the five `
    + `editor bars align. Found: "${row[1]}"`);
}

const refCssAt = css.indexOf(".op-pane .panel-row-reference");
assert(refCssAt >= 0, "Reference card line needs dedicated compact styling");
const refCss = css.slice(refCssAt, refCssAt + 420);
for (const rule of ["overflow: hidden", "white-space: nowrap", "text-overflow: ellipsis"]) {
  assert(refCss.includes(rule), `Reference card line must use ${rule}`);
}

console.log("document reference compact panel-card tests passed");
