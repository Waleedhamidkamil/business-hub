/* The dashboard on a short screen (2026-10-21/22, Waleed's laptop).

   1920x1080 at 125% Windows scaling is a 1536x801 page. The dashboard fills
   exactly one screen, every section took its height from what was left, and
   nothing had a floor - so Insights' "By Document Type" absorbed every
   missing pixel and drew at 94px, one crushed row.

   The fix, measured from 650px to 1377px tall in a real browser on his data:
     - what gives way first is decided by HEIGHT, and it is NOT the hero or
       the four Create New tiles (Waleed: keep them at a good size on the
       small screen) - it is the bid-outcomes donut and Quick access;
     - the mid row has a floor, with ~20px of slack under the laptop's own
       ~419px so title-bar / DPI rounding cannot tip it into scrolling;
     - below the floor the page SCROLLS, it never crushes;
     - tiles on the bottom edge do not animate in from below it (that made
       a 2px scrollbar flash at 1536x801).
   Result: 1536x801 fits with no scroll and a 158px chart; 1920x1017 is
   unchanged (hero 173, chart 199, 16 cards).

   Text assertions over the CSS and app.js - no browser. */
const fs = require("fs");

const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");
const js = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const html = fs.readFileSync("templates/index.html", "utf8");

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const blockAt = css.indexOf("SHORT SCREENS (2026-10-21");
const block = css.slice(blockAt, css.indexOf("/* (the block above sits AFTER", blockAt));

/* every rule body inside a (max-height: N) media query, in the whole sheet */
function heightQueries() {
  const out = [];
  const re = /@media\s*\(max-height:\s*(\d+)px\)\s*\{/g;
  let m;
  while ((m = re.exec(css))) {
    let depth = 1, i = re.lastIndex;
    while (depth && i < css.length) { if (css[i] === "{") depth++; else if (css[i] === "}") depth--; i++; }
    out.push({ px: +m[1], body: css.slice(re.lastIndex, i - 1) });
  }
  return out;
}

console.log("dashboard on short screens");

check("the short-screen block exists and sits AFTER the rules it overrides", () => {
  assert(blockAt > 0, "the SHORT SCREENS block is gone");
  // Same specificity as the hero / tile / mid-row rules: the later rule wins.
  const heroRule = css.indexOf(".dash-view .dash-hero h1 { font-size: clamp(");
  const qaRule = css.indexOf(".dash-view .dash-bottom .dash-card {");
  assert(heroRule > 0 && blockAt > heroRule, "the block must come after the hero rules");
  assert(qaRule > 0, "Quick access card rule not found");
});

check("the hero and the Create New tiles keep their size on a short screen", () => {
  // Waleed, 2026-10-22: "I would like to have the hero card and the apps card
  // below it in good size in small screen."
  heightQueries().forEach((q) => {
    assert(!/\.dash-hero|\.dash-row\.cols-4|dash-create-head/.test(q.body),
      `a (max-height: ${q.px}px) step shrinks the hero or the Create New tiles again`);
  });
});

check("what gives way is the donut and Quick access, at 960px tall and below", () => {
  const q = heightQueries().find((x) => x.px === 960);
  assert(q, "no (max-height: 960px) step");
  assert(/--dash-donut-h:\s*136/.test(q.body), "the donut does not shrink in the short step");
  assert(/\.dash-qa-head\s*\{\s*display:\s*none/.test(q.body), "the Quick access heading still takes its 30px");
  assert(/#dashBidDonut\s*\{\s*--dash-donut-h:\s*188;\s*\}/.test(block), "the full-size donut height (188) is not the default");
});

check("the mid row has a floor, with slack under the laptop's ~419px", () => {
  const m = block.match(/\.dash-inner > \.dash-mid-row \{[^}]*min-height:\s*(\d+)px/);
  assert(m, "the mid row has no floor - a short screen crushes the chart again");
  const floor = +m[1];
  assert(floor >= 380 && floor <= 405,
    `floor ${floor}px: under ~380 the chart is crushed; over ~405 the laptop (419px) scrolls on DPI rounding`);
  assert(/margin-bottom:\s*-1px/.test(block.slice(block.indexOf(".dash-inner > .dash-mid-row {"))),
    "nothing absorbs the sub-pixel rounding, so a fitting page shows a 1px scrollbar");
});

check("below the floor the page scrolls instead of crushing", () => {
  assert(/\.dash-inner\s*\{[^}]*overflow-y:\s*auto/.test(block), ".dash-inner cannot scroll");
});

check("the donut's height comes from CSS and is redrawn once a resize settles", () => {
  assert(!/chart: \{ type: "donut", height: 188/.test(js), "the donut height is hard-coded again");
  assert(/chart: \{ type: "donut", height: _bidDonutDrawnAt\(host\)/.test(js), "the donut does not read --dash-donut-h");
  const fn = js.slice(js.indexOf("function scheduleDonutRefit()"), js.indexOf("function scheduleDonutRefit()") + 700);
  assert(/setTimeout\(/.test(fn), "the redraw is not deferred - it fires before the column has its new width");
  assert(/h === _bidDonutDrawn\.h && w === _bidDonutDrawn\.w\) return;/.test(fn), "it redraws even when nothing changed");
  assert(/addEventListener\("resize", scheduleDonutRefit\)/.test(js), "a window resize does not refit the donut");
  assert(/matchMedia\("\(max-height: 960px\)"\)/.test(js) && /addEventListener\("change", scheduleDonutRefit\)/.test(js),
    "crossing the 960px step does not refit the donut");
});

check("bottom-edge tiles never animate in from below the page's edge", () => {
  assert(/\.dash-view \.dash-mid-side \.dash-bottom \.dash-card \{ animation-name: dashEdgeIn; \}/.test(block),
    "Quick access uses dashCardIn again - it starts 16px below the edge and flashes a scrollbar");
  const kf = block.slice(block.indexOf("@keyframes dashEdgeIn"), block.indexOf("}", block.indexOf("to ", block.indexOf("@keyframes dashEdgeIn"))));
  assert(!/translate/.test(kf), "dashEdgeIn moves the tile - a scale from the centre is the only safe motion");
});

check("the Create New heading carries its own class", () => {
  assert(/class="dash-section-head dash-create-head"/.test(html), "dash-create-head is gone from index.html");
});

if (failures) { console.log(`\n${failures} failed`); process.exit(1); }
console.log("\nall short-screen checks passed");
