/* The Create-New card row must fill its width (2026-08-27).
 *
 * Retiring the Official Letter took the row from five cards to four. The class
 * was renamed `cols-5` → `cols-4` but the rule still said `repeat(5, 1fr)`, so
 * the four cards kept a 5-column track list and the row stopped short of the
 * right edge - a card-shaped hole exactly where Letters used to be. Nothing
 * caught it: the page renders, the cards look right, the row is just short.
 *
 * So: the class name and the column count must agree, and the number of cards
 * in the markup must match both.
 */
const fs = require("fs");

const css  = fs.readFileSync("static/app_styles.css", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

// ---- 1. every .dash-row.cols-N declares exactly N columns -------------------
const declared = [...css.matchAll(/\.dash-row\.cols-(\d+)\s*\{\s*grid-template-columns:\s*repeat\((\d+),/g)];
assert(declared.length >= 3,
  `expected the cols-2/3/4 grid family in app_styles.css, found ${declared.length}`);
for (const [, name, count] of declared) {
  assert(name === count,
    `.dash-row.cols-${name} declares repeat(${count}, …) - the class name is not the `
    + `layout. A row of ${name} cards in a ${count}-column grid leaves `
    + `${count - name} empty slot(s) and stops short of the right edge.`);
}

// ---- 2. the Create-New row's card count matches its class ------------------
const rowMatch = html.match(/<div class="dash-row cols-(\d+)">([\s\S]*?)<\/div>\s*\n\s*\n/);
assert(rowMatch, "cannot find the Create-New row (<div class=\"dash-row cols-N\">)");
const [, rowCols, rowInner] = rowMatch;
const cards = [...rowInner.matchAll(/<button class="dash-card glass" data-doc="([^"]+)"/g)].map(m => m[1]);
assert(cards.length === Number(rowCols),
  `the Create-New row has ${cards.length} cards (${cards.join(", ")}) but class cols-${rowCols}. `
  + "Add/remove a card and the class + its repeat() must move with it.");

// ---- 3. the four document types, and no retired one ------------------------
assert(JSON.stringify(cards) === JSON.stringify(["quote", "po", "dn", "inv"]),
  `unexpected Create-New cards: ${cards.join(", ")} (expected quote, po, dn, inv)`);
assert(!/data-doc="ltr"/.test(html),
  "the Official Letter card is back - that document type was retired on 2026-08-27");

// ---- 4. no stagger rule aims past the last card ----------------------------
const nth = [...css.matchAll(/\.dash-row\.cols-(\d+) \.dash-card:nth-child\((\d+)\)/g)];
for (const [, name, idx] of nth) {
  assert(Number(idx) <= Number(name),
    `.dash-row.cols-${name} has a :nth-child(${idx}) rule but only ${name} cards exist - `
    + "left over from a larger row; dead style that misleads the next reader.");
}

console.log("dashboard card row grid tests passed");
