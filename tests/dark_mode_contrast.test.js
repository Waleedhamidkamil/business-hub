/*
   DARK MODE STAYS READABLE  (Waleed, 2026-10-09)

   "If a dark background then we need a light font, and if a light background
   then a dark font. Use less white background for buttons."

   The 2026-10-09 audit found three mechanical causes behind every white panel
   and unreadable label in dark mode. Each is pinned here, so a new rule cannot
   quietly bring one back:

   1. A component background written as a literal white (#fff / white) instead
      of var(--surface). Light mode cannot tell the difference; dark mode
      paints a white slab. 54 rules did this (To-Do, Needs attention, the PO
      settings popup...).
   2. A token READ with a light fallback but DEFINED nowhere (--bg-subtle,
      --navy-tint...). The fallback is all either theme ever sees, so dark mode
      showed the Document Records sidebar white.
   3. A control filled with --navy and written in white. --navy is a light
      steel-blue in dark mode, so that was 3.36:1. dark.css must repaint every
      such rule with --accent-fill.

   Plus the measured numbers the fix rests on: the ink/fill pairs in both
   themes must clear WCAG AA (4.5:1).
*/
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..");
const read = p => fs.readFileSync(path.join(ROOT, p), "utf8");
const APP = read("static/app_styles.css");
const DARK = read("static/dark.css");

const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, " "));
function rules(css) {
  const out = [];
  const re = /([^{}@;]+)\{([^{}]*)\}/g;
  // Strip ONCE (2026-10-23). Stripping inside the loop condition made a fresh
  // copy of the whole stylesheet per rule, and every rule's text kept its copy
  // alive: memory grew as rules x file size and hit Node's 2 GB limit once the
  // activity-log styles were added (2072 MB peak with a raised limit).
  const src = stripComments(css);
  let m;
  while ((m = re.exec(src))) {
    out.push({ sel: m[1].trim().replace(/\s+/g, " "), body: m[2] });
  }
  return out;
}
const decl = (body, prop) => {
  const m = body.match(new RegExp("(?:^|;)\\s*" + prop + "\\s*:\\s*([^;]+)", "i"));
  return m ? m[1].trim() : null;
};
// Drop every var(--x, fallback): a fallback is not a colour the theme paints
// when the token exists, and point 2 below polices the ones that do not.
const noVars = v => { let p; do { p = v; v = v.replace(/var\(--[\w-]+(?:,[^()]*(?:\([^()]*\)[^()]*)*)?\)/g, "VAR"); } while (v !== p); return v; };

/* ---- 1. no literal white component backgrounds ------------------------- */
// These stay white ON PURPOSE in both themes: they are mats behind a logo, a
// signature, a scanned page, or the knob of a switch.
const WHITE_ON_PURPOSE = new Set([
  ".dn-slider::after",
  ".glass",                                   // dark.css repaints it
  ".dash-profile-logo-wrap",
  ':root[data-profile="eshraqat"] .dash-hero-mark',
  ".sig-slot-preview",
  ".cl-li-avatar", ".dm-cl-avatar",           // dark.css repaints both
  ".linked-pill", ".paid-pill", ".po-link-tag.is-route", ".paid-btn:hover",  // dark.css
  ".lpo-v-canvas, .lpo-v-img", ".lpo-mark-grip",
  ".todo-lpo-page img",
]);
const whites = [];
for (const r of rules(APP)) {
  if (r.sel.includes("data-theme")) continue;
  for (const prop of ["background", "background-color"]) {
    const v = decl(r.body, prop);
    if (v && /(^|[\s,(])(#fff|#ffffff|white)(?![\w-])/i.test(noVars(v)) && !WHITE_ON_PURPOSE.has(r.sel)) {
      whites.push(`${r.sel} { ${prop}: ${v} }`);
    }
  }
}
assert.deepStrictEqual(whites, [],
  "a component background is a literal white - write var(--surface) (identical in light mode, "
  + "dark in dark mode):\n  " + whites.join("\n  "));

/* ---- 2. every token read with a fallback is defined somewhere ---------- */
const allCss = fs.readdirSync(path.join(ROOT, "static"))
  .filter(f => f.endsWith(".css") && !f.includes(".min."))
  .map(f => read("static/" + f)).join("\n");
const js = fs.readdirSync(path.join(ROOT, "static")).filter(f => f.endsWith(".js"))
  .map(f => read("static/" + f)).join("\n") + read("templates/index.html");
const defined = new Set([...allCss.matchAll(/(--[\w-]+)\s*:/g), ...js.matchAll(/(--[\w-]+)\s*:/g),
  ...js.matchAll(/setProperty\(\s*["'](--[\w-]+)/g)].map(m => m[1]));
const LIGHT_HEX = /#(?:f[0-9a-f]{2}(?:[0-9a-f]{3})?|e[0-9a-f]{5}|fff)\b|white/i;
const leaks = new Set();
for (const m of allCss.matchAll(/var\((--[\w-]+)\s*,\s*([^;{}]*?)\)\s*[;,}\s]/g)) {
  if (!defined.has(m[1]) && LIGHT_HEX.test(m[2])) leaks.add(`${m[1]} (fallback ${m[2].trim()})`);
}
assert.deepStrictEqual([...leaks], [],
  "these tokens are read with a LIGHT fallback but never defined, so dark mode paints the "
  + "fallback - define them (dark.css at least):\n  " + [...leaks].join("\n  "));

/* ---- 3. navy-filled white-ink controls are repainted in dark mode ------- */
const darkSel = DARK.replace(/\s+/g, " ");
const missing = [];
for (const r of rules(APP)) {
  if (r.sel.includes("data-theme") || r.sel.includes(":hover")) continue;
  const bg = decl(r.body, "background") || decl(r.body, "background-color") || "";
  const ink = decl(r.body, "color") || "";
  if (/^var\(--navy\b/.test(bg) && /^(#fff|#ffffff|white)$/i.test(ink)) {
    for (const s of r.sel.split(",").map(x => x.trim())) {
      if (!darkSel.includes(`html[data-theme="dark"] ${s},`) && !darkSel.includes(`html[data-theme="dark"] ${s} {`)) missing.push(s);
    }
  }
}
assert.deepStrictEqual(missing, [],
  "these fill with --navy and write white on it; --navy is LIGHT in dark mode (3.36:1). Add them "
  + "to the --accent-fill block in dark.css:\n  " + missing.join("\n  "));

/* ---- 4. the numbers ----------------------------------------------------- */
const hex = (css, scope, name) => {
  const block = scope ? css.slice(css.indexOf(scope)) : css;
  const m = block.match(new RegExp(name + "\\s*:\\s*(#[0-9a-fA-F]{6})"));
  assert(m, `${name} must be a #rrggbb colour in ${scope || "the stylesheet"}`);
  return m[1];
};
const lum = h => { const c = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
  .map(v => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const AA = (a, b, what) => { const r = ratio(a, b);
  assert(r >= 4.5, `${what}: ${a} on ${b} is ${r.toFixed(2)}:1 - needs 4.5:1`); };

const ROOT_BLOCK = ":root {";
const DARK_BLOCK = 'html[data-theme="dark"] {';
AA("#FFFFFF", hex(APP, ROOT_BLOCK, "--accent-fill"), "light primary button");
AA("#FFFFFF", hex(DARK, DARK_BLOCK, "--accent-fill"), "dark primary button");
AA(hex(APP, ROOT_BLOCK, "--text-subtle"), "#FFFFFF", "light caption grey");
const darkSurface = hex(DARK, DARK_BLOCK, "--surface");
AA(hex(DARK, DARK_BLOCK, "--text-subtle"), darkSurface, "dark caption grey");
AA(hex(DARK, DARK_BLOCK, "--text-subtle"), hex(DARK, DARK_BLOCK, "--header-tint"), "dark caption grey on a table header");
AA(hex(DARK, DARK_BLOCK, "--navy"), hex(DARK, DARK_BLOCK, "--header-tint"), "dark link / heading ink on a table header");

/* ---- 5. the buttons Waleed named --------------------------------------- */
const primary = rules(APP).find(r => r.sel === "button.primary");
assert(/var\(--accent-fill\)/.test(decl(primary.body, "background")),
  "button.primary must fill with --accent-fill - the green it used was white-on-green, 3.77:1 light / 1.89:1 dark");
const back = rules(APP).find(r => r.sel === "button.warm-red");
assert(/var\(--surface\)/.test(decl(back.body, "background")) && !/#fff/i.test(decl(back.body, "color")),
  "the Back button (button.warm-red) is neutral navigation, not a coral slab with white text");
const saved = rules(APP).find(r => r.sel.startsWith("button.editor-save-btn.is-saved"));
assert(!/^(#fff|white)$/i.test(decl(saved.body, "color")),
  "Saved / Signed is a state tint with dark ink, not a solid green button with white text");

/* ---- 6. the document colours (Waleed's palette, 2026-10-09) ------------ */
const docBlock = APP.slice(APP.indexOf("Per-doc-type accent palette"));
const docHex = k => { const m = docBlock.match(new RegExp("--c-" + k + "\\s*:\\s*(#[0-9a-fA-F]{6})"));
  assert(m, "--c-" + k + " must be a #rrggbb colour in the document palette block"); return m[1].toUpperCase(); };
const DOC = { quote: docHex("quote"), po: docHex("po"), dn: docHex("dn"), inv: docHex("inv") };
for (const [k, v] of Object.entries(DOC)) AA("#FFFFFF", v, `white text on the ${k} card`);
const dm = read("static/data_manager.js");
const acc = name => (dm.match(new RegExp('"' + name + '":\\s*"(#[0-9a-fA-F]{6})"')) || [])[1];
assert.deepStrictEqual(
  [acc("Quotations"), acc("Purchase Orders"), acc("Delivery Notes"), acc("Invoices")].map(x => (x || "").toUpperCase()),
  [DOC.quote, DOC.po, DOC.dn, DOC.inv],
  "the Document Records sidebar colours each document family the same as the dashboard does");

console.log("dark_mode_contrast.test.js: no white slabs, no undefined light tokens, every navy fill repainted, AA ink in both themes");
