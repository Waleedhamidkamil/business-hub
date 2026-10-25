/* Insights view (rebuilt 2026-08-09) - the rules the redesign is built on,
   asserted against the real files rather than trusted to survive edits.

   The four that matter most:
     1. NO red/green pair signals direction anywhere. The user is colour blind;
        a delta must carry an arrow AND a word, and a loss must carry a literal
        minus sign, so the meaning survives with the colour removed.
     2. NO dual-axis chart. Money and margin-% are two separate charts - two
        y-scales on one plot invent a correlation that is not in the data.
     3. Every chart has a tooltip AND a table twin on the page, so no value is
        reachable only by hovering.
     4. The series colours come from CSS tokens that exist in BOTH themes. The
        palette order is the colourblind-safety mechanism; a hex hard-coded in
        the JS would silently escape the dark-mode re-stepping.

   Assertions run against COMMENT-STRIPPED source: this file's own prose
   mentions "red"/"green"/"opposite", and so do the comments in the files it
   reads. Matching those would be matching myself.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

const rawJs = fs.readFileSync("static/analytics.js", "utf8").replace(/\r\n/g, "\n");
const rawCss = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");
const rawDark = fs.readFileSync("static/dark.css", "utf8").replace(/\r\n/g, "\n");
const rawHtml = fs.readFileSync("templates/index.html", "utf8").replace(/\r\n/g, "\n");
const appJs = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

const stripBlock = (s) => s.replace(/\/\*[\s\S]*?\*\//g, " ");
const stripLine = (s) => s.split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
const js = stripLine(stripBlock(rawJs));
const css = stripBlock(rawCss);
const dark = stripBlock(rawDark);
const html = rawHtml.replace(/<!--[\s\S]*?-->/g, " ");

/* ---------- 0. the contract with app.js ---------- */
{
  assert(/window\.showAnalytics\s*=/.test(js), "showAnalytics must stay exported");
  assert(/window\.rerenderAnalytics\s*=/.test(js), "rerenderAnalytics must stay exported (app.js calls it on theme change)");
  assert(appJs.includes("window.showAnalytics"), "app.js should still open the view through showAnalytics");
  assert(/window\.rerenderAnalytics/.test(appJs), "app.js re-themes the view through rerenderAnalytics");
}

/* ---------- 1. money comes from the insights bridge ---------- */
{
  assert(js.includes("api().insights_overview("), "must call the insights_overview bridge method");
  assert(js.includes("api().client_insights("), "must call the client_insights bridge method");
  assert(!js.includes("dashboard_analytics"),
    "dashboard_analytics counts DOCUMENTS and belongs to the dashboard card - insights is money");
  assert(!js.includes("costing_trend"),
    "costing_trend is superseded by insights_overview; two sources would drift");
}

/* ---------- 2. no red/green direction signalling ---------- */
{
  /* The delta helper must produce words, not just glyphs. */
  const trend = js.slice(js.indexOf("function trendText("));
  assert(/up/.test(trend) && /down/.test(trend) && /level/.test(trend),
    "trendText must spell the direction out in words");
  assert(/▲/.test(trend) && /▼/.test(trend), "trendText must carry an arrow glyph too");

  /* A negative amount always renders with a literal minus sign. Run the real
     formatters rather than pattern-matching them - a regex over the block
     passes as long as ANY of the three still carries the sign. */
    const fmtSrc = rawJs.slice(rawJs.indexOf("function esc("), rawJs.indexOf("function tok("));
    const fmt = new Function(fmtSrc + "\nreturn { money, money2, moneyShort, pctText, trendText };")();
  [["money", fmt.money], ["money2", fmt.money2], ["moneyShort", fmt.moneyShort]].forEach(([name, fn]) => {
    assert(String(fn(-1234)).startsWith("−"),
      `${name}(-1234) must start with a minus sign - colour cannot be the only cue`);
    assert(!String(fn(1234)).startsWith("−"), `${name}(1234) must not look negative`);
  });
  assert(String(fmt.moneyShort(-2_500_000)).startsWith("−$2.5M"), "compacted negatives keep the sign");
  ["up", "down"].forEach((word, i) => {
    const out = fmt.trendText(i === 0 ? 200 : 50, 100, "prev month");
    assert(out.includes(word), `trendText should say "${word}": got "${out}"`);
  });
  assert(fmt.trendText(100, 0, "prev month").includes("compare"),
    "no baseline must say so, not print a fake 0%");

  /* The stylesheet must not pair a green 'up' with a red 'down'. */
  /* Bound the slice to the INSIGHTS block. An open-ended slice ran to the end
     of the stylesheet and swept in whatever was appended after it - the Item
     Master's destructive-action button, for one, which legitimately uses the
     danger token and has nothing to do with these rules. */
  const rawStart = rawCss.indexOf(".in-view {");
  const rawEnd = rawCss.indexOf("/* ===", rawStart + 1);   // the next banner
  const inBlock = stripBlock(rawCss.slice(rawStart, rawEnd > rawStart ? rawEnd : undefined));
  assert(inBlock.includes(".in-kpi"), "the Insights CSS slice lost its own rules");
  assert(!inBlock.includes(".im-btn"), "the slice ran past the Insights block");
  assert(!/--in-pos\b/.test(inBlock),
    "there is deliberately no positive/negative COLOUR PAIR - only --in-neg, used beside a minus sign");
  const success = /\.in-[a-z-]*\s*\{[^}]*var\(--success/.test(inBlock);
  assert(!success, "no Insights element may take its colour from --success (green) for state");
  assert(!/var\(--danger/.test(inBlock),
    "state colour comes from --in-neg (used with a sign), never the app's red danger token");
}

/* ---------- 3. no dual-axis chart ---------- */
{
  assert(!/opposite:\s*true/.test(js),
    "a second y-axis on the opposite side is the dual-axis anti-pattern - use two charts");
  /* Each mount must pass a single yaxis object, never an array of scales.
     `annotations: { yaxis: [...] }` is a list of reference LINES on the one
     scale, not a second scale - allow that specific nesting only. */
  let at = js.indexOf("yaxis: [");
  while (at !== -1) {
    const before = js.slice(Math.max(0, at - 200), at);
    assert(/annotations:\s*\{\s*$/.test(before),
      "an array of y-axes means more than one scale on one plot");
    at = js.indexOf("yaxis: [", at + 1);
  }
}

/* ---------- 4. every chart is hoverable and has a table twin ---------- */
{
  const mounts = js.split("mount(").slice(1);
  assert(mounts.length >= 4, "expected several charts");
  let charted = 0;
  mounts.forEach((m) => {
    const body = m.slice(0, m.indexOf("\n    }"));
    if (!body.includes("chart:")) return;      // the spark helper's own shape
    charted += 1;
    assert(body.includes("tooltip:"), "every chart needs a tooltip: " + body.slice(0, 60));
  });
  assert(charted >= 3, "expected at least three real charts");
  assert(js.includes('class="in-table"'), "the leaderboard/table twin must exist");
  assert((js.match(/class="in-table"/g) || []).length >= 3,
    "leaderboard, items and documents should all render as tables");
}

/* ---------- 5. legends for multi-series, direct labels for single-series ---------- */
{
  const money = js.slice(js.indexOf("function moneySeriesChart("), js.indexOf("function marginChart("));
  assert(/series:\s*\[/.test(money) && (money.match(/name:/g) || []).length >= 3, "money chart has 3 series");
  assert(/legend:\s*\{[^}]*position/.test(money), "a 3-series chart must show a legend");
  assert(/dataLabels:\s*\{\s*enabled:\s*false/.test(money),
    "a number on every column is chaos - the axis and tooltip carry them");

  const hbar = js.slice(js.indexOf("function hbarChart("), js.indexOf("function categoryChart("));
  assert(/legend:\s*\{\s*show:\s*false/.test(hbar), "a single-series chart needs no legend box");
  assert(/dataLabels:\s*\{\s*\n?\s*enabled:\s*true/.test(hbar),
    "the single-series bars must be direct-labelled (also the contrast relief the palette owes)");
}

/* ---------- 6. mark specs: surface gap, hairline solid grid ---------- */
{
  assert(/colors:\s*\[T\.surface\]/.test(js),
    "touching bars are separated by a 2px gap in the SURFACE colour, not a border");
  assert(!/strokeDashArray:\s*[1-9]/.test(js),
    "gridlines and annotation rules are solid hairlines - dashing reads as a threshold");
}

/* ---------- 7. colours are tokens, defined in both themes ---------- */
{
  const used = new Set();
  const re = /tok\("(--in-[a-z0-9-]+)"/g;
  let m;
  while ((m = re.exec(js))) used.add(m[1]);
  assert(used.size >= 5, "expected the series tokens to be read from CSS");
  used.forEach((t) => {
    assert(new RegExp("\\" + t + ":").test(css), `light theme is missing ${t}`);
    assert(new RegExp("\\" + t + ":").test(dark), `dark theme is missing ${t}`);
  });
  /* No raw hex series colours in the chart code - they would escape the
     dark-mode re-stepping and the validator. Fallbacks inside tok() are fine. */
  const noFallbacks = js.replace(/tok\([^)]*\)/g, "TOK");
  const hexes = (noFallbacks.match(/#[0-9a-fA-F]{6}\b/g) || []);
  assert(hexes.length === 0, "hard-coded series hex found: " + hexes.join(", "));
}

/* ---------- 8. one filter row, and it scopes everything ---------- */
{
  ["inPeriod", "inMonthPrev", "inMonthLabel", "inMonthNext", "inSpan",
   "inClientSearch", "inRail", "inBody"].forEach((id) => {
    const hits = (html.match(new RegExp(`id="${id}"`, "g")) || []).length;
    assert(hits === 1, `#${id} must appear exactly once in index.html (found ${hits})`);
  });
  [1, 3, 6, 12].forEach((n) => {
    assert(html.includes(`data-span="${n}"`), `the ${n}-month span button is missing`);
  });
  /* Reloading must re-fetch with the CURRENT span AND month - a control that
     silently drops one of them is the classic "filter does nothing" bug. */
  assert(/insights_overview\(state\.span,\s*state\.year,\s*state\.month\)/.test(js),
    "the overview call must pass span, year and month together");
  assert(/client_insights\(state\.client,\s*state\.span,\s*state\.year,\s*state\.month\)/.test(js),
    "the client call must pass the same window as the overview");
}

/* ---------- 9. refetch holds the previous render ---------- */
{
  const load = js.slice(js.indexOf("async function load()"), js.indexOf("window.rerenderAnalytics"));
  assert(load.includes('classList.add("is-loading")') && load.includes('classList.remove("is-loading")'),
    "refetch dims the existing render instead of flashing a skeleton");
  assert(!/innerHTML\s*=\s*("" |""|``|''|"\s*")/.test(load),
    "load() must not blank anything before the data arrives - that is the layout jump");
  assert(/\.is-loading\s*\{[^}]*opacity/.test(css), "the loading state is an opacity hold");
}

/* ---------- 10. user-supplied text is escaped ---------- */
{
  /* EVERY interpolation of a name / label / document number into HTML must go
     through esc(). Checking that one of them does is not enough - the one that
     doesn't is the hole. */
  const DATA_FIELD = /\.(name|no|label|status|sub_type|top1_name|best_month|date)\b/;
  /* A ternary whose branches are both string LITERALS emits no data - it only
     reads one (`x.name === picked ? " active" : ""`). */
  const literalTernary = /\?\s*(["'`])[^"'`]*\1\s*:\s*(["'`])[^"'`]*\2\s*\}$/;
  const interpolations = js.match(/\$\{[^{}]*(\{[^{}]*\}[^{}]*)*\}/g) || [];
  const risky = interpolations.filter((s) =>
    DATA_FIELD.test(s) && !s.includes("esc(") && !literalTernary.test(s));
  assert(risky.length === 0, "unescaped interpolation(s) into HTML: " + risky.join(" | "));
  assert((js.match(/data-client="\$\{esc\(/g) || []).length >= 2,
    "both the rail card and the leaderboard row must escape the client name");
}

/* ---------- 11. the old skin is really gone ---------- */
{
  assert(!/\.an-/.test(css), "leftover .an-* rules from the previous Insights skin");
  assert(!/\.an-/.test(dark), "leftover .an-* dark overrides");
  assert(!/class="an-/.test(html), "leftover an-* markup");
  assert(!/id="an[A-Z]/.test(html), "leftover an* element ids");
}

console.log("Insights view tests passed");
