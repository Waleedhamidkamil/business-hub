/* Loading feedback (2026-08-05).

   Saving a PO showed no feedback at all. `savePO` wrapped the work in
   `withLoading(document.getElementById("btnSave"), ...)`, but `#btnSave` was
   the EXPORT button and the 2026-07-24 toolbar trim deleted it. getElementById
   returned null, withLoading's first line was `if (!buttonEl) return asyncFn()`,
   and the spinner quietly stopped existing. Nine ids were dead the same way.

   Nothing failed, nothing logged. The only symptom was a user eventually
   noticing that a thing they used to see was gone - which is the worst kind of
   regression to rely on catching.

   Two defences, both tested here:

     1. A busy bar driven by a COUNT of in-flight work, not by a button. If a
        button is renamed out from under it, feedback survives.
     2. This test: every element id handed to withLoading must exist in
        index.html. That is the check that would have caught the original.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const html = fs.readFileSync("templates/index.html", "utf8");
const FILES = ["static/app.js", "static/quote.js", "static/dn.js",
               "static/inv.js"];
const src = Object.fromEntries(
  FILES.map(f => [f, fs.readFileSync(f, "utf8").replace(/\r\n/g, "\n")]));

const idExists = (id) => html.includes(`id="${id}"`);

/* ---------- 1. no withLoading target names an element that isn't there ---------- */
{
  /* Scan a window above each `withLoading(btn` for the id(s) `btn` was resolved
     from. Deliberately textual: the point is to catch an id that no longer
     exists in the markup, which no amount of running the code would reveal
     because the failure mode is a silent no-op. */
  let checked = 0;
  for (const [file, s] of Object.entries(src)) {
    const re = /withLoading\(\s*(\w+)\s*,/g;
    let m;
    while ((m = re.exec(s)) !== null) {
      const varName = m[1];
      const before = s.slice(Math.max(0, m.index - 700), m.index);
      const declRe = new RegExp(`const ${varName} = ([^;]+);`, "g");
      const decls = [...before.matchAll(declRe)];
      if (!decls.length) continue;
      const decl = decls[decls.length - 1][1];

      for (const idm of decl.matchAll(/"([A-Za-z][\w-]*)"/g)) {
        const id = idm[1];
        checked += 1;
        assert(idExists(id),
          `${file}: withLoading is handed #${id}, which does not exist in `
          + "index.html. getElementById returns null and the spinner silently "
          + "does nothing - exactly how the PO save indicator was lost when the "
          + "Export and Print buttons were removed. Point it at a button that "
          + "exists, or pass null explicitly if there is no longer a button.");
      }
    }
  }
  assert(checked >= 8,
    `only ${checked} withLoading targets were checked - the scanner has stopped `
    + "matching the call sites, so this test is no longer guarding anything");
}

/* ---------- 2. the specific ids the July trim removed ---------- */
{
  const REMOVED = ["btnSave", "btnPoPrint", "btnQSave", "btnQPrint", "btnDnSave",
                   "btnDnPrint", "btnInvSave", "btnInvPrint"];
  for (const id of REMOVED) {
    assert(!idExists(id),
      `#${id} is back in index.html. It was removed in the 2026-07-24 toolbar `
      + "trim; if it has genuinely returned, drop it from this list.");
  }
  /* Deliberately NOT asserting that no file mentions these ids. The editors
     bind their optional buttons as
         { const b = document.getElementById("btnDnPrint"); if (b) b.onclick = ... }
     which is the codebase's pattern for a button that may not be present, and
     is harmless - the binding simply doesn't happen. What is NOT harmless is
     handing one of these to withLoading, because there the null is swallowed
     and the user loses all feedback. Section 1 is what guards that. */
}

/* ---------- 3. the save paths point at the Save button that exists ---------- */
{
  const SAVE = [["static/app.js", "btnPoStay"], ["static/quote.js", "btnQStay"],
                ["static/dn.js", "btnDnStay"], ["static/inv.js", "btnInvStay"]];
  for (const [file, id] of SAVE) {
    assert(idExists(id), `#${id} is missing from index.html`);
    assert(src[file].includes(id),
      `${file} must resolve its save spinner to #${id} - that is the Save `
      + "button the toolbar actually has");
  }
}

let _mod, _bar, _runTimers;

/* ---------- 4. run the real busy counter ---------- */
{
  const s = src["static/app.js"];
  const start = s.indexOf("let _busyDepth = 0;");
  const end = s.indexOf("function bindUi()");   // include withLoading itself
  assert(start >= 0 && end > start, "cannot slice the busy block out of app.js");

  // Minimal fakes: a bar element and a controllable clock.
  const bar = { hidden: true, _cls: new Set(),
    classList: { add: (c) => bar._cls.add(c), remove: (c) => bar._cls.delete(c),
                 contains: (c) => bar._cls.has(c) } };
  const timers = [];
  const documentStub = { getElementById: (id) => (id === "busyBar" ? bar : null) };
  const setTimeout_ = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  const clearTimeout_ = (h) => { if (h) timers[h - 1] = null; };
  const runTimers = () => { const due = timers.slice(); timers.length = 0;
                            due.forEach(t => t && t.fn()); };

  const mod = new Function("document", "setTimeout", "clearTimeout", "window",
    s.slice(start, end) + "\n return { beginBusy, endBusy, firstEl, withLoading };"
  )(documentStub, setTimeout_, clearTimeout_, {});

  // (a) it waits before showing - a bar that flashes on every fast call reads
  //     as a glitch rather than as progress.
  mod.beginBusy();
  assert(bar.hidden === true, "the bar must not appear instantly");
  runTimers();
  assert(bar.hidden === false && bar._cls.has("is-on"), "the bar must appear once the delay elapses");

  // (b) a fast operation never shows it at all
  mod.endBusy(); runTimers();
  bar.hidden = true; bar._cls.clear();
  mod.beginBusy(); mod.endBusy(); runTimers();
  assert(bar.hidden === true,
    "an operation that finishes inside the delay must never show the bar");

  // (c) OVERLAPPING work: the first one finishing must not switch off the
  //     second. This is why it is a counter and not a boolean.
  bar.hidden = true; bar._cls.clear();
  mod.beginBusy(); mod.beginBusy();
  runTimers();
  assert(bar._cls.has("is-on"), "bar should be on with two operations running");
  mod.endBusy(); runTimers();
  assert(bar._cls.has("is-on"),
    "one of two concurrent operations finishing must NOT clear the bar - the "
    + "other is still running. A boolean flag would have turned it off here.");
  mod.endBusy(); runTimers();
  assert(!bar._cls.has("is-on"), "the last one finishing clears it");

  // (d) unbalanced calls cannot strand it on or drive it negative
  mod.endBusy(); mod.endBusy(); runTimers();
  assert(!bar._cls.has("is-on"), "extra endBusy calls must be harmless");
  mod.beginBusy(); runTimers();
  assert(bar._cls.has("is-on"),
    "after stray endBusy calls the counter must still start from zero, or the "
    + "bar never shows again");
  mod.endBusy(); runTimers();

  _mod = mod; _bar = bar; _runTimers = runTimers;   // reused by section 5

  // (e) firstEl skips ids that are not present
  assert(mod.firstEl("nope", "busyBar") === bar, "firstEl must return the first id that exists");
  assert(mod.firstEl("nope", "alsoNope") === null, "firstEl returns null when none exist");
  assert(mod.firstEl(null, "busyBar") === bar, "firstEl must tolerate a null id");
}

/* ---------- 5. withLoading survives a missing button, AND a failure ---------- */
async function withLoadingTests(mod, bar, runTimers) {
  const s = src["static/app.js"];
  const at = s.indexOf("async function withLoading");
  const body = s.slice(at, s.indexOf("\n}", at));
  assert(!/if \(!buttonEl\) return asyncFn\(\)/.test(body),
    "the early return on a missing button is what made the whole indicator "
    + "vanish when its button was deleted - the work must still be wrapped");

  // (a) a null button is fine: the bar still runs.
  bar.hidden = true; bar._cls.clear();
  let release;
  const gate = new Promise(r => { release = r; });
  const running = mod.withLoading(null, () => gate);
  runTimers();
  assert(bar._cls.has("is-on"),
    "withLoading(null, …) must still show the bar - a missing button is exactly "
    + "the case that used to produce no feedback at all");
  release();
  await running;
  runTimers();
  assert(!bar._cls.has("is-on"), "the bar clears when the work finishes");

  /* (b) THE ONE THAT MATTERS: the work throws.
     Saving crosses the bridge and shells out to Chrome to render a PDF; either
     can fail. If endBusy is not in a `finally`, the counter never comes back
     down, the bar spins forever, and because the counter is shared every later
     operation inherits it.

     Checking the SOURCE for the word "finally" does not catch this - moving
     endBusy to after the try/finally block leaves it textually later in the
     file and reads as correct. That mutation survived the first version of
     this test. Only running it proves anything. */
  bar.hidden = true; bar._cls.clear();
  let threw = false;
  try {
    await mod.withLoading(null, async () => { throw new Error("save failed"); });
  } catch (e) {
    threw = true;
  }
  runTimers();
  assert(threw, "withLoading must re-throw - callers rely on it to report the error");
  assert(!bar._cls.has("is-on"),
    "the busy bar is STILL ON after the work threw. endBusy must be in the "
    + "finally block, or one failed save leaves the bar spinning for the rest "
    + "of the session.");

  // (c) and the counter is genuinely back to zero, not merely hidden
  mod.beginBusy(); runTimers();
  assert(bar._cls.has("is-on"),
    "after a failure the counter must be back at zero - stuck above zero and "
    + "the bar never turns off again");
  mod.endBusy(); runTimers();
  assert(!bar._cls.has("is-on"));
}

/* ---------- 6. the bar is in the markup, at body level ---------- */
{
  assert(/id="busyBar"/.test(html), "#busyBar is missing from index.html");
  const at = html.indexOf('id="busyBar"');
  const view = html.lastIndexOf("<main", at);
  const viewEnd = html.lastIndexOf("</main>", at);
  assert(viewEnd > view,
    "#busyBar must live at body level like #toast - inside a view, a hidden "
    + "view's display:none would hide it exactly when it is needed");
  assert(/\.busy-bar\s*\{[^}]*position:\s*fixed/.test(
           fs.readFileSync("static/app_styles.css", "utf8")),
    ".busy-bar must be position:fixed so it pins to the window");
  assert(/pointer-events:\s*none/.test(
           fs.readFileSync("static/app_styles.css", "utf8")
             .match(/\.busy-bar\s*\{[^}]*\}/)[0]),
    "the bar must not swallow clicks - it spans the full width of the window");
}

withLoadingTests(_mod, _bar, _runTimers).then(
  () => console.log("Busy indicator tests passed"),
  (err) => { console.error(err); process.exit(1); });
