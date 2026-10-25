/* Rotating hero greeting (2026-08-04).

   The hub used to say "Good morning" + one fixed subtitle forever. It now
   picks a greeting + tagline ONCE PER APP START from a large pool.

   It used to re-pick on every entry to the hub; Waleed stopped that on
   2026-10-01 because the line changed each time he came back from a
   document. Section 7 is the test of that ruling - delete the cache and it
   goes red.

   Two things can quietly break this:

   1. THE HERO IS A FIXED HEIGHT WITH overflow:hidden
      (`.dash-view .dash-hero { height: clamp(118px, 17vh, 200px) }`).
      A greeting long enough to wrap pushes the date row out of sight. The
      current pools were measured in headless Chrome at 1024x600, 1366x768
      and 1920x1080: the widest greeting ("Good afternoon - the desk is
      yours, {name}", 42 chars) takes 65% of the available width in the
      WORST case (1024x600), and the widest tagline (67 chars) 58%. That
      puts the overflow point around 64 and 115 chars respectively; the
      caps below sit well inside those with room for a longer name.
      The nowrap/ellipsis CSS is the backstop, not the plan.

   2. A QUESTION GREETING + A QUESTION TAGLINE reads badly
      ("What's next, Waleed?" / "What can we get done today?"). Taglines are
      therefore statements only - that rule is enforced here because it is
      invisible in the code itself.

   Runs the REAL renderHeroGreeting against a stub DOM - no browser.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const js = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");

/* ---------- load the real module code ---------- */
const start = js.indexOf("const DASH_GREETINGS = {");
assert(start >= 0, "DASH_GREETINGS not found in static/app.js");
const end = js.indexOf("async function renderDashboard", start);
assert(end > start, "cannot find the end of the greeting block");
const src = js.slice(start, end);

// Stub DOM: four spans/divs whose textContent we can read back.
const nodes = {
  dashGreeting: { textContent: "" },
  dashUser: { textContent: "Waleed" },
  dashGreetingTail: { textContent: "" },
  dashTagline: { textContent: "" },
};
const documentStub = { getElementById: (id) => nodes[id] || null };

const mod = new Function("document", src + `
  return { DASH_GREETINGS, DASH_TAGLINES, dashGreetingBucket, pickFreshLine,
           pickHeroPair, renderHeroGreeting,
           // test-only seam: start a new "app run" without reloading the module
           newAppRun: () => { _dashHeroPick = null; } };
`)(documentStub);

const { DASH_GREETINGS, DASH_TAGLINES, dashGreetingBucket, pickHeroPair,
        renderHeroGreeting, newAppRun } = mod;

const BUCKETS = ["morning", "afternoon", "evening", "night", "any"];
const allGreetings = BUCKETS.flatMap(b => DASH_GREETINGS[b] || []);

/* ---------- 1. the pools are real and varied ---------- */
{
  for (const b of BUCKETS) {
    assert(Array.isArray(DASH_GREETINGS[b]), `DASH_GREETINGS.${b} is missing`);
    assert(DASH_GREETINGS[b].length >= 8,
      `DASH_GREETINGS.${b} has only ${DASH_GREETINGS[b].length} lines - the point `
      + "of this feature is that you rarely see the same one twice");
  }
  assert(allGreetings.length >= 60,
    `only ${allGreetings.length} greetings total; expected 60+`);
  assert(DASH_TAGLINES.length >= 30,
    `only ${DASH_TAGLINES.length} taglines; expected 30+`);

  const dupG = allGreetings.filter((l, i) => allGreetings.indexOf(l) !== i);
  assert(!dupG.length, "duplicate greeting(s): " + [...new Set(dupG)].join(" | "));
  const dupT = DASH_TAGLINES.filter((l, i) => DASH_TAGLINES.indexOf(l) !== i);
  assert(!dupT.length, "duplicate tagline(s): " + [...new Set(dupT)].join(" | "));
}

/* ---------- 2. lengths stay inside the measured safe zone ---------- */
{
  const G_CAP = 52;   // measured overflow ~64 chars at 1024x600
  const T_CAP = 90;   // measured overflow ~115 chars at 1024x600

  for (const line of allGreetings) {
    assert(line.length <= G_CAP,
      `greeting is ${line.length} chars (cap ${G_CAP}): "${line}". The hero is a `
      + "fixed height with overflow:hidden - a wrapped h1 hides the date row.");
  }
  for (const line of DASH_TAGLINES) {
    assert(line.length <= T_CAP,
      `tagline is ${line.length} chars (cap ${T_CAP}): "${line}"`);
  }
}

/* ---------- 3. {name} is a well-formed single slot ---------- */
{
  for (const line of allGreetings) {
    const first = line.indexOf("{name}");
    assert(first >= 0,
      `greeting has no {name} slot: "${line}". A nameless line is allowed by the `
      + "renderer, but every line in these pools is meant to address the user - "
      + "if that changes on purpose, relax this assertion deliberately.");
    assert(line.indexOf("{name}", first + 1) < 0,
      `greeting uses {name} twice: "${line}" - only the first is substituted, so `
      + "the second would render as a literal placeholder");
    assert(!/\{\s*name\s*\}/.test(line.replace("{name}", "")),
      `stray name-like placeholder in: "${line}"`);
  }
}

/* ---------- 4. taglines are statements, never questions ---------- */
{
  for (const line of DASH_TAGLINES) {
    assert(!line.includes("?"),
      `tagline is a question: "${line}". Greetings may be questions, so a question `
      + "tagline can land directly under one and read as a double-ask.");
  }
}

/* ---------- 5. time buckets map to the right pool ---------- */
{
  const cases = [
    [0, "night"], [4, "night"], [5, "morning"], [11, "morning"],
    [12, "afternoon"], [16, "afternoon"], [17, "evening"], [21, "evening"],
    [22, "night"], [23, "night"],
  ];
  for (const [hour, expected] of cases) {
    assert(dashGreetingBucket(hour) === expected,
      `hour ${hour} mapped to ${dashGreetingBucket(hour)}, expected ${expected}`);
  }
  // Every hour of the day must land in a pool that exists.
  for (let h = 0; h < 24; h++) {
    const b = dashGreetingBucket(h);
    assert(Array.isArray(DASH_GREETINGS[b]) && DASH_GREETINGS[b].length,
      `hour ${h} -> bucket "${b}", which has no lines`);
  }
}

/* ---------- 6. the renderer splits the line around the name ---------- */
{
  const NAME = "Waleed";
  const seen = new Set();

  for (let i = 0; i < 400; i++) {
    const now = new Date(2026, 7, 4, i % 24, 0, 0);
    newAppRun();              // each iteration stands for a fresh app start
    renderHeroGreeting(now);

    const head = nodes.dashGreeting.textContent;
    const name = nodes.dashUser.textContent;
    const tail = nodes.dashGreetingTail.textContent;
    const rebuilt = head + "{name}" + tail;

    assert(name === NAME,
      `the accent span should hold the name, got "${name}" - it is read once from `
      + "index.html and cached, so an empty first read would stick forever");
    assert(allGreetings.includes(rebuilt),
      `rendered pieces do not rebuild a known greeting: ` +
      `head="${head}" name="${name}" tail="${tail}"`);
    assert(DASH_TAGLINES.includes(nodes.dashTagline.textContent),
      `unknown tagline rendered: "${nodes.dashTagline.textContent}"`);

    // The line must belong to this hour's bucket or the always-on pool.
    const bucket = dashGreetingBucket(now.getHours());
    const allowed = DASH_GREETINGS[bucket].concat(DASH_GREETINGS.any);
    assert(allowed.includes(rebuilt),
      `hour ${now.getHours()} (${bucket}) rendered an out-of-bucket line: "${rebuilt}"`);

    seen.add(rebuilt);
  }

  assert(seen.size >= 30,
    `only ${seen.size} distinct greetings in 400 app runs - the picker is stuck`);
}

/* ---------- 7. ONE line per app start, and a fresh one next run ---------- */
{
  // (a) Within one run the hub can be entered any number of times and the
  //     hero must not move. This is the 2026-10-01 ruling.
  newAppRun();
  const nine = new Date(2026, 7, 4, 9, 0, 0);
  renderHeroGreeting(nine);
  const firstG = nodes.dashGreeting.textContent + "{name}" + nodes.dashGreetingTail.textContent;
  const firstT = nodes.dashTagline.textContent;
  for (let i = 0; i < 50; i++) {
    // A later entry can even fall in a different time bucket (he left the hub
    // at 11:58 and came back at 12:05); the line still must not change.
    renderHeroGreeting(new Date(2026, 7, 4, (9 + i) % 24, 0, 0));
    const g = nodes.dashGreeting.textContent + "{name}" + nodes.dashGreetingTail.textContent;
    assert(g === firstG,
      `the greeting changed on re-entering the hub: "${firstG}" -> "${g}"`);
    assert(nodes.dashTagline.textContent === firstT,
      `the tagline changed on re-entering the hub: "${firstT}" -> "${nodes.dashTagline.textContent}"`);
  }

  // (b) A new run must not hand back the line you have just been looking at,
  //     or the feature is a fixed greeting with extra steps.
  let lastG = null, lastT = null;
  for (let i = 0; i < 200; i++) {
    const pair = pickHeroPair(nine);
    assert(pair.greeting !== lastG, `the same greeting was picked twice running: "${pair.greeting}"`);
    assert(pair.tagline !== lastT, `the same tagline was picked twice running: "${pair.tagline}"`);
    lastG = pair.greeting; lastT = pair.tagline;
  }
}

/* ---------- 8. the markup the renderer binds to ---------- */
{
  for (const id of ["dashGreeting", "dashUser", "dashGreetingTail", "dashTagline"]) {
    assert(html.includes(`id="${id}"`),
      `#${id} is missing from index.html - renderHeroGreeting writes into it`);
  }
  // The three spans must be adjacent with NO whitespace between them, or the
  // browser renders a stray space before the name / after it.
  assert(/<span id="dashGreeting">[^<]*<\/span><span class="accent" id="dashUser">[^<]*<\/span><span id="dashGreetingTail"><\/span>/.test(html),
    "the greeting spans must sit flush against each other on one line - HTML "
    + "whitespace between inline spans renders as a real space");

  assert(!/h < 12 \? "Good morning"/.test(js),
    "the old hard-coded time-of-day greeting is still in app.js");

  // renderHeroGreeting must read the cached pair, not call the picker inline.
  // Catches the easy regression: someone puts pickFreshLine back in the
  // renderer and the line starts hopping on every hub entry again.
  const renderer = js.slice(js.indexOf("function renderHeroGreeting"),
                            js.indexOf("async function renderDashboard"));
  assert(/if \(!_dashHeroPick\) _dashHeroPick = pickHeroPair\(now\);/.test(renderer),
    "renderHeroGreeting no longer caches its pick - the hero line will change "
    + "every time the hub is entered, which Waleed asked us to stop");
  assert(!/pickFreshLine\(/.test(renderer),
    "renderHeroGreeting calls pickFreshLine directly again - picking belongs in "
    + "pickHeroPair, which runs once per app start");
}

/* ---------- 9. the CSS backstop against a long line ---------- */
{
  // Comments are stripped before matching. Without that, the explanatory
  // comment ABOVE each of these declarations ("min-width:0 - a flex child
  // defaults to...") satisfies the assertion on its own, and deleting the
  // real declaration still passes. That happened; hence this note.
  function ruleBody(selector) {
    const at = css.indexOf(selector + " {");
    assert(at >= 0, `CSS rule not found: ${selector}`);
    const open = css.indexOf("{", at);
    return css.slice(open + 1, css.indexOf("}", open)).replace(/\/\*[\s\S]*?\*\//g, "");
  }

  for (const sel of [".dash-hero h1", ".dash-hero .dash-user"]) {
    const body = ruleBody(sel);
    assert(/white-space:\s*nowrap/.test(body),
      `${sel} must be nowrap - the hero has a fixed height and overflow:hidden, `
      + "so a wrapped line silently eats the date row below it");
    assert(/text-overflow:\s*ellipsis/.test(body),
      `${sel} needs text-overflow:ellipsis; nowrap alone clips mid-letter`);
  }

  assert(/min-width:\s*0/.test(ruleBody(".dash-hero-content")),
    ".dash-hero-content is a flex child: without min-width:0 it refuses to "
    + "shrink below its content and the ellipsis above never engages");
}

console.log("Dashboard hero greeting tests passed");
