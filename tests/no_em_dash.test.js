/*
   NO EM DASH, ANYWHERE.  (Waleed's ruling, 2026-09-11.)

   He does not want the character "—" (U+2014) in this app for any reason. Not
   on a document, not in the interface, not in a comment, not in the docs. Use
   a plain hyphen "-".

   A rule written in CLAUDE.md is a rule the next session can skim past. This
   test is the part that cannot be skimmed past: it walks the repository and
   fails with the file and line of every em dash it finds. 10,581 were removed
   the day it was written; the job now is keeping it at zero.

   It also bars the character from the CODE, not only from strings the user
   sees, and that is deliberate. A comment written with em dashes is a comment
   that gets copied into a UI string later, and then it is on an invoice.
*/
const fs = require("fs");
const path = require("path");

const EM = "—";

/* Not ours to police: other checkouts of this repo, dependencies, build
   leftovers, and the binary formats where a byte sequence may coincide. */
const SKIP_DIRS = new Set([
  ".git", "node_modules", ".test-work", "tmp", "graphify-out", "__pycache__",
  ".worktrees", ".claude", ".codex", ".superpowers", "fonts", "brands",
]);
const SKIP_EXT = new Set([
  ".db", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2",
  ".ttf", ".eot", ".zip", ".xlsx", ".pyc", ".map",
]);

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(path.join(dir, entry.name), out);
    } else {
      const p = path.join(dir, entry.name);
      if (SKIP_EXT.has(path.extname(entry.name).toLowerCase())) continue;
      if (/\.min\.(js|css)$/.test(entry.name)) continue;
      if (entry.name === "no_em_dash.test.js") continue;   // it names the character
      out.push(p);
    }
  }
  return out;
}

const offenders = [];
for (const file of walk(".")) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (e) {
    continue;                       // unreadable or not text; not our business
  }
  if (!text.includes(EM)) continue;
  text.split("\n").forEach((line, i) => {
    if (line.includes(EM)) {
      offenders.push(`${file}:${i + 1}: ${line.trim().slice(0, 100)}`);
    }
  });
}

/* The character is not the only way to put one on screen (2026-10-09). The
   To-Do heading carried "&mdash;" and Insights carried "—" in template
   strings: neither is the raw character, so the walk above passed while the
   app showed four em dashes. In the front end (and the tests, whose messages
   are read too) the ESCAPED forms count as well. The backend is left out on
   purpose: image_search.py matches a typed em dash in a regex so it can treat
   it as a hyphen, which is the rule being enforced, not broken. */
const ESCAPED = /&mdash;|&#8212;|&#x2014;|\\u2014/i;
for (const root of ["static", "templates", "tests"]) {
  if (!fs.existsSync(root)) continue;
  for (const file of walk(root)) {
    if (!/\.(js|cjs|html|css|py)$/.test(file) || /[\\/]lib[\\/]/.test(file)) continue;
    let text;
    try { text = fs.readFileSync(file, "utf8"); } catch (e) { continue; }
    if (!ESCAPED.test(text)) continue;
    text.split("\n").forEach((line, i) => {
      if (ESCAPED.test(line)) offenders.push(`${file}:${i + 1}: (escaped) ${line.trim().slice(0, 100)}`);
    });
  }
}

if (offenders.length) {
  throw new Error(
    `Found ${offenders.length} em dash(es). This app does not use "—" for any `
    + `reason — use a plain hyphen "-". See CLAUDE.md and AGENTS.md.\n\n`
      .replace(EM, "-")            // even this message obeys the rule
    + offenders.slice(0, 40).join("\n")
    + (offenders.length > 40 ? `\n... and ${offenders.length - 40} more` : ""));
}

console.log("no_em_dash.test.js: no em dashes anywhere in the app");
