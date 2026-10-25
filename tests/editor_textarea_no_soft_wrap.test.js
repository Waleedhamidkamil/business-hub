/* Editor cells must not invent visual line breaks for one-line Excel values.

   PO, quotation, delivery-note, and invoice text cells are textareas because
   users may intentionally enter real newlines. A textarea soft-wraps long text
   by default, though, and the editors then auto-grow it from scrollHeight. The
   result is a one-line Excel cell appearing as two lines after paste.

   The editor contract is therefore:
     - preserve explicit newlines (`white-space: pre`),
     - never soft-wrap a long logical line,
     - scroll horizontally when that line is wider than the column, and
     - hide the scrollbar chrome so it cannot steal vertical text room, and
     - keep vertical overflow hidden because JS owns textarea height.
*/
const fs = require("fs");

const css = fs.readFileSync("static/app_styles.css", "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function declarationsFor(selector) {
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const rule = rules.find((match) => match[1]
    .split(",")
    .map((part) => part.trim())
    .includes(selector));
  assert(rule, `missing CSS rule for ${selector}`);
  return rule[2];
}

function assertNoSoftWrap(selector) {
  const declarations = declarationsFor(selector);
  assert(/white-space\s*:\s*pre\s*;/.test(declarations),
    `${selector} must preserve explicit newlines without soft-wrapping`);
  assert(/overflow-x\s*:\s*auto\s*;/.test(declarations),
    `${selector} must allow horizontal scrolling for a long single line`);
  assert(/overflow-y\s*:\s*hidden\s*;/.test(declarations),
    `${selector} must leave vertical sizing to the editor's textarea autosizer`);
  assert(/scrollbar-width\s*:\s*none\s*;/.test(declarations),
    `${selector} must not lose text height to horizontal scrollbar chrome`);
}

[
  ".items-table textarea",
  ".q-edit-table textarea",
  ".dn-edit-table textarea",
  ".inv-edit-table textarea",
].forEach(assertNoSoftWrap);

console.log("Editor textarea no-soft-wrap contract passed");
