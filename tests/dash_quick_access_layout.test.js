/* Quick access must stay an EVEN grid (2026-08-01).

   Adding "Client LPOs" made it four cards against a `repeat(3, 1fr)` grid, so
   the dashboard rendered 3 across and a single orphan underneath. The fix is
   2 x 2 - but two rows cost vertical space in a dashboard that is fitted to
   the viewport, and that space comes straight out of the Needs-attention grid
   directly above. So this also pins the trims that pay for the second row.

   The real trap is the next card: a FIFTH quick-access card against a 2-column
   grid reproduces the exact same orphan. The count assertion below fails loudly
   if that happens, rather than letting it ship looking lopsided.

   Pure text assertions over the CSS and HTML - no browser.
*/
const fs = require("fs");

const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/* ---------- pull the Quick access block out of index.html ---------- */
function quickAccessBlock() {
  const start = html.indexOf('<div class="dash-bottom">');
  assert(start >= 0, "Quick access block (.dash-bottom) not found in index.html");
  const end = html.indexOf("\n                </div>", start);
  assert(end > start, "Cannot find the end of the .dash-bottom block");
  return html.slice(start, end);
}

/* ---------- pull a CSS rule body by exact selector ---------- */
function ruleBody(selector) {
  const at = css.indexOf(selector + " {");
  assert(at >= 0, `CSS rule not found: ${selector}`);
  const open = css.indexOf("{", at);
  const close = css.indexOf("}", open);
  assert(close > open, `Unterminated CSS rule: ${selector}`);
  return css.slice(open + 1, close);
}

const block = quickAccessBlock();
const cards = Array.from(block.matchAll(/<button\b[^>]*\bid="([^"]+)"/g), m => m[1]);

/* ---------- 1. the grid and the card count must AGREE ---------- */
{
  const grid = ruleBody(".dash-view .dash-bottom");
  const m = grid.match(/grid-template-columns:\s*repeat\((\d+),/);
  assert(m, "Quick access must declare an explicit repeat(N, ...) column count; "
          + "got: " + grid.trim().slice(0, 120));
  cols = Number(m[1]);

  /* The RULE, not one instance of it. This has been wrong twice: 3 cards in a
     repeat(3) grid was right; a fourth (Client LPOs, 2026-08-01) made it 3 + 1
     with a lonely orphan, so it moved to repeat(2) as two even rows - which
     cost the Needs-attention grid above a row of height. Retiring the Costing
     & Profit card (2026-09-11) put it back to 3. Hard-coding "4 cards, 2
     columns" is what made this test fail for the RIGHT reason but with the
     WRONG advice, so it now states the invariant instead. */
  assert(cards.length % cols === 0,
    `Quick access has ${cards.length} cards (${cards.join(", ")}) against a `
    + `${cols}-column grid. That leaves ${cards.length % cols} orphaned on the `
    + "last row, which is exactly the bug this test exists for. Change the card "
    + "count or the column count so they divide evenly.");
  assert(cards.length / cols <= 2,
    "more than two rows of quick access eats the Needs-attention grid above it");
}

/* ---------- 2. every card is present and accounted for ---------- */
{
  const expected = ["dashOpenAutofill", "dashOpenLpos"];
  assert(expected.every(id => cards.includes(id)),
    `missing quick-access card(s): ${expected.filter(id => !cards.includes(id)).join(", ")}`);
  assert(!cards.includes("dashOpenCosting"),
    "the Costing & Profit card was retired on 2026-09-11 - costs are typed in "
    + "each document's own private cost column, which is where every costed "
    + "document was actually costed");
  assert(!cards.includes("dashOpenClients"),
    "the Client Profiles card went the same day: its totals are in Insights "
    + "(by_client), its document list is the Open panel's client chips, and its "
    + "addresses and terms are what Master Data > Clients edits");
  assert(cards.length === expected.length,
    "unexpected extra quick-access card(s): "
    + cards.filter(id => !expected.includes(id)).join(", "));
}

/* ---------- 3. the block must not eat the grid above it ---------- */
{
  /* The CONSTRAINT is the block's height, not the icon's size. This used to
     assert "icon < 48px", which was the right limit while quick access was TWO
     rows of cards and the icon was the biggest single contributor. At one row
     that reasoning does not hold, and the assertion blocked a change Waleed
     asked for (2026-09-11: make these cards bigger and more visible). So it now
     measures what actually matters - rows x icon height - against the budget
     the old two-row layout consumed. */
  const icon = ruleBody(".dash-view .dash-bottom .dash-card-icon");
  const m = icon.match(/height:\s*(\d+)px/);
  assert(m, "quick-access icons must set an explicit height so the block's "
           + "height is predictable; none found");
  const rows = Math.ceil(cards.length / (cols || 1));
  const budget = 2 * 47;          // what two rows of the old 40px icon cost
  assert(rows * Number(m[1]) <= budget,
    `quick access is ${rows} row(s) of ${m[1]}px icons = ${rows * Number(m[1])}px `
    + `of icon, over the ${budget}px this block may take from the Needs-attention `
    + "grid above it. Shrink the icon or drop a card.");
}

/* ---------- 4. half-width cards must not go ragged ---------- */
{
  const at = css.indexOf(".dash-view .dash-bottom .dash-card-title");
  assert(at >= 0, "no rule targeting the quick-access card title");
  const body = css.slice(at, css.indexOf("}", at));
  assert(/white-space:\s*nowrap/.test(body),
    "titles like 'Autofill Suggestions' wrap to two lines in a half-width card "
    + "and make the row heights uneven - they must be nowrap + ellipsis");
  assert(/text-overflow:\s*ellipsis/.test(body),
    "nowrap without ellipsis clips mid-letter; both are needed");
}

/* ---------- 5. each card carries its own accent ---------- */
{
  const classes = ["dash-card-autofill", "dash-card-lpos"];
  for (const cls of classes) {
    assert(block.includes(cls),
      `card class ${cls} missing from the Quick access markup`);
    assert(new RegExp(`\\.${cls}\\s*\\{[^}]*--card-accent`).test(css),
      `${cls} must set --card-accent; the shared .dash-card-icon rule derives `
      + "tint, hover and the dark-theme override from it");
  }
  // The old hard-coded icon colour bypassed hover and dark mode. The card it
  // belonged to is gone, so the rule must not linger either.
  assert(!/\.dash-card-clients\s+\.dash-card-icon\s*\{/.test(css),
    "Client Profiles is retired - its icon rule must not linger in the CSS");
}

/* ---------- 6. the animated LPO icon is wired and behaves ---------- */
{
  const js = fs.readFileSync("static/app.js", "utf8");

  assert(/\["lpo",\s*"#dashOpenLpos \.dash-card-icon"\]/.test(js),
    "the Client LPOs card must be in LICON_MAP, or it keeps the static glyph");

  const icon = JSON.parse(fs.readFileSync("static/lottie/lpo.json", "utf8"));
  assert(Array.isArray(icon.layers) && icon.layers.length,
    "static/lottie/lpo.json must be a real animation - mountLottieIcon bails "
    + "on a file with no layers and silently leaves the static SVG");

  // Same marker shape as doc_lordicon: hover only, no intro. lottieSegments
  // then returns segIn=null, so `rest` falls back to op-1 and the icon settles
  // on the FORMED frame. An "in-*" marker would change the resting frame.
  const cms = (icon.markers || []).map(m => m.cm);
  assert(cms.some(c => /hover/i.test(c)), "the icon needs a hover marker to play on hover");
  assert(!cms.some(c => /in/i.test(c)),
    "an 'in-*' marker would make lottieSegments rest on the intro's end frame "
    + "instead of op-1; this file is a single hover state, so there is none");

  // The shield is gone and the letters are really there.
  const c1 = (icon.assets || []).find(a => a.id === "comp_1");
  assert(c1, "comp_1 (Content-28) missing - that is where the page content lives");
  const names = c1.layers.map(l => l.nm);
  assert(!names.includes("outline 2"),
    "'outline 2' is the shield layer - it must not come back");
  const lpo = c1.layers.find(l => l.nm === "lpo-text");
  assert(lpo, "the lpo-text layer is missing");
  assert(lpo.shapes.map(g => g.nm).join(",") === "Letter L,Letter P,Letter O",
    "expected three letter groups in order, got: " + lpo.shapes.map(g => g.nm));

  // Each letter draws itself on, like the document lines below it.
  for (const g of lpo.shapes) {
    const trim = g.it.find(i => i.ty === "tm");
    assert(trim && trim.e && trim.e.a === 1,
      `${g.nm} has no animated trim path - it would pop in instead of writing on`);
  }

  // Colour/width read from the `control` layer. Hard-coding them silently
  // breaks Lordicon-style recolouring, including the dark-theme pass.
  for (const g of lpo.shapes) {
    const st = g.it.find(i => i.ty === "st");
    assert(st && /control/.test((st.c && st.c.x) || ""),
      `${g.nm} stroke colour must keep the control-layer expression`);
    assert(/control/.test((st.w && st.w.x) || ""),
      `${g.nm} stroke width must keep the control-layer expression`);
  }
}

/* ---------- 7. the icon-size rule must not reach into .licon-box ---------- */
{
  assert(/\.dash-view \.dash-bottom \.dash-card-icon > svg/.test(css),
    "the quick-access icon-size rule must use a DIRECT-child selector: once "
    + "mountLottieIcon swaps the glyph for a .licon-box > svg, an unscoped "
    + "rule shrinks the animated icon inside its own box");
}

console.log("Dashboard quick-access layout tests passed");
