/* Duplicate a document (2026-08-04).

   The panel-row Duplicate button opens a document in its editor as a NEW,
   UNSAVED copy: next number, today's date, everything else pre-filled.

   It works by calling the editor's own `load*ForEdit` and then CLEARING the
   identity, which reuses all the loading logic but makes clearing the risky
   half. That asymmetry is the whole reason this file exists:

     - a missed COPY leaves an obviously blank field, and you notice
     - a missed CLEAR ships a duplicate that claims to be signed, already
       submitted, or made from a delivery note it has nothing to do with

   So every field each `*MakeDuplicate` hook must clear is asserted here, by
   source inspection of the hook (the state objects live in five different
   modules that can't be loaded standalone), plus the shared dispatcher is
   actually EXECUTED against stubs.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const read = (p) => fs.readFileSync(p, "utf8").replace(/\r\n/g, "\n");
const app = read("static/app.js");
const editors = {
  quote: read("static/quote.js"),
  dn: read("static/dn.js"),
  inv: read("static/inv.js"),
};

const KINDS = ["po", "quote", "dn", "inv"];

/* ---------- 1. the dispatcher covers all four document types ---------- */
{
  const at = app.indexOf("const DUPLICATE_HOOK = {");
  assert(at >= 0, "DUPLICATE_HOOK not found in static/app.js");
  const block = app.slice(at, app.indexOf("};", at));
  for (const kind of KINDS) {
    assert(new RegExp(`\\b${kind}:\\s*\\{`).test(block),
      `DUPLICATE_HOOK is missing "${kind}" - that document type's Duplicate `
      + "button would toast 'Cannot duplicate this document type'");
  }
  // The loader names must match the real functions, or the button silently
  // reports "the editor is not loaded".
  const loaders = {
    po: ["loadForEdit", app], quote: ["loadQuoteForEdit", editors.quote],
    dn: ["loadDnForEdit", editors.dn], inv: ["loadInvForEdit", editors.inv],
  };
  for (const [kind, [fn, src]] of Object.entries(loaders)) {
    assert(block.includes(`"${fn}"`), `DUPLICATE_HOOK.${kind} must name loader ${fn}`);
    assert(new RegExp(`(async )?function ${fn}\\b`).test(src),
      `${fn} does not exist as a top-level function - window[${fn}] would be undefined`);
  }
}

/* ---------- 2. every hook exists and is reachable off window ---------- */
{
  const hooks = {
    po: ["poMakeDuplicate", app], quote: ["quoteMakeDuplicate", editors.quote],
    dn: ["dnMakeDuplicate", editors.dn], inv: ["invMakeDuplicate", editors.inv],
  };
  for (const [kind, [fn, src]] of Object.entries(hooks)) {
    assert(new RegExp(`async function ${fn}\\b`).test(src),
      `${fn} (${kind}) is missing`);
    assert(src.includes(`window.${fn} = ${fn}`),
      `${fn} must be assigned to window - app.js dispatches via window[hook]`);
  }
}

/* ---------- 3. the clear-list: identity ---------- */
{
  const hookSrc = (name, src) => {
    const at = src.indexOf(`async function ${name}`);
    assert(at >= 0, `${name} not found`);
    // Up to the closing brace at column 0.
    const end = src.indexOf("\n}", at);
    return src.slice(at, end);
  };

  const cases = [
    ["poMakeDuplicate",    app,           "state",      "editPoNo"],
    ["quoteMakeDuplicate", editors.quote, "quoteState", "editQuoteNo"],
    ["dnMakeDuplicate",    editors.dn,    "dnState",    "editDnNo"],
    ["invMakeDuplicate",   editors.inv,   "invState",   "editInvNo"],
  ];

  for (const [fn, src, stateName, editField] of cases) {
    const body = hookSrc(fn, src);

    assert(new RegExp(`${stateName}\\.mode\\s*=\\s*"new"`).test(body),
      `${fn} must set ${stateName}.mode = "new", or Save would UPDATE the original`);
    assert(new RegExp(`${stateName}\\.${editField}\\s*=\\s*null`).test(body),
      `${fn} must null ${stateName}.${editField} - a stale edit number is what `
      + "makes Save overwrite the document you copied");
    assert(new RegExp(`${stateName}\\.signingOverlay\\s*=\\s*null`).test(body),
      `${fn} must clear ${stateName}.signingOverlay, or the copy claims to be signed`);
    assert(/__currentEditorDoc\s*=\s*null/.test(body),
      `${fn} must clear window.__currentEditorDoc - the duplicate is not a saved `
      + "document, and leaving it set keeps the old row highlighted in the panel");
    assert(/setModeChip\(false\)/.test(body),
      `${fn} must drop the "Edit mode" chip`);
    assert(new RegExp(`${stateName}\\.(dirty|dirtySinceSave)\\s*=\\s*true`).test(body),
      `${fn} must mark the editor dirty - it holds real unsaved content, and `
      + "without this the close/navigate guards would let it vanish silently");
    assert(/RefreshSignChip\(\)|poRefreshSignChip\(\)/.test(body),
      `${fn} must refresh the sign chip after clearing the overlay, or the topbar `
      + "still shows the original's signed state");
  }
}

/* ---------- 4. the clear-list: per-kind provenance ---------- */
{
  const body = (name, src) => {
    const at = src.indexOf(`async function ${name}`);
    return src.slice(at, src.indexOf("\n}", at));
  };

  // PO: delivered / invoiced / paid belong to the original.
  const po = body("poMakeDuplicate", app);
  assert(/state\.poFulfillment\s*=\s*null/.test(po),
    "poMakeDuplicate must clear state.poFulfillment - otherwise the copy shows "
    + "delivered/invoiced/paid pills and tinted rows it never earned");
  assert(/setPoFulfillChips\("none",\s*"none",\s*"none"\)/.test(po),
    "poMakeDuplicate must reset the fulfillment chips to none");
  assert(/decoratePoFulfillment\(\)/.test(po),
    "poMakeDuplicate must re-run decoratePoFulfillment to drop the row tints");

  // Invoice: sourceDn is the transient Make-Invoice link.
  const inv = body("invMakeDuplicate", editors.inv);
  assert(/invState\.sourceDn\s*=\s*""/.test(inv),
    "invMakeDuplicate must clear invState.sourceDn - it records which DN the "
    + "invoice was built from, and the copy was not built from it");

  // DN: the client's returned signed copy must not follow the duplicate.
  const dn = body("dnMakeDuplicate", editors.dn);
  assert(/reloadDnSignedCopy\(\)/.test(dn),
    "dnMakeDuplicate must re-run reloadDnSignedCopy so the attached client copy "
    + "is re-read for the NEW number instead of showing the original's");

  // The deal link SHOULD survive - guard against over-clearing, which is the
  // failure mode in the other direction.
  for (const [fn, src] of [["dnMakeDuplicate", editors.dn],
                           ["invMakeDuplicate", editors.inv]]) {
    const b = body(fn, src);
    assert(!/\.sourceQuote\s*=/.test(b),
      `${fn} should NOT touch sourceQuote - the copy belongs to the same deal, `
      + "and the quotation reference is descriptive, not a per-document fact");
  }
}

/* ---------- 5. the number is re-derived, not carried ---------- */
{
  const pairs = [
    ["poMakeDuplicate", app, "initNextPoNumber()"],
    ["quoteMakeDuplicate", editors.quote, "refreshQuoteNumber()"],
    ["dnMakeDuplicate", editors.dn, "refreshDnNumber()"],
    ["invMakeDuplicate", editors.inv, "refreshInvNumber()"],
  ];
  for (const [fn, src, call] of pairs) {
    const at = src.indexOf(`async function ${fn}`);
    const b = src.slice(at, src.indexOf("\n}", at));
    assert(b.includes(call),
      `${fn} must call ${call} to get the next number - carrying the source's `
      + "number over would make Save collide with the original");
  }

  // The three English editors gate refresh*Number on numberOverridden, which
  // is TRUE after a load. Leaving it set means the number never updates and the
  // duplicate keeps the original's number.
  for (const [fn, src] of [["quoteMakeDuplicate", editors.quote],
                           ["dnMakeDuplicate", editors.dn],
                           ["invMakeDuplicate", editors.inv]]) {
    const at = src.indexOf(`async function ${fn}`);
    const b = src.slice(at, src.indexOf("\n}", at));
    assert(/numberOverridden\s*=\s*false/.test(b),
      `${fn} must reset numberOverridden - load*ForEdit sets it true, and `
      + "refresh*Number() is a NO-OP while it is true, so the copy would keep "
      + "the original's number");
  }
}

/* ---------- 6. the button: rendered per kind, only when editable ---------- */
{
  // PO's row is Arabic and builds the button inline; the other three use the
  // shared English helper.
  assert(/data-act="duplicate" data-kind="po"/.test(app),
    "the PO panel row has no Duplicate button");
  assert(/\$\{p\.editable \?/.test(app.slice(app.indexOf('data-act="duplicate" data-kind="po"') - 400,
                                             app.indexOf('data-act="duplicate" data-kind="po"') + 40)),
    "the PO Duplicate button must be gated on p.editable - a view-only row has "
    + "no database record, so load_po returns null and duplicate cannot work");

  const shared = [["quote", editors.quote, "q"], ["dn", editors.dn, "d"],
                  ["inv", editors.inv, "d"]];
  for (const [kind, src, v] of shared) {
    const re = new RegExp(`\\$\\{${v}\\.editable && window\\.duplicateBtnHTML \\? window\\.duplicateBtnHTML\\("${kind}"`);
    assert(re.test(src),
      `${kind}: the panel row must render the Duplicate button via `
      + `duplicateBtnHTML("${kind}", …) and gate it on ${v}.editable`);
  }

  // Every renderer must also WIRE it, or the button is decoration.
  assert(/wireDuplicateButtons\(box\)/.test(app), "PO list does not wire Duplicate");
  for (const [kind, src] of [["quote", editors.quote], ["dn", editors.dn],
                             ["inv", editors.inv]]) {
    assert(/window\.wireDuplicateButtons\(box\)/.test(src),
      `${kind} list does not call window.wireDuplicateButtons(box)`);
  }
}

/* ---------- 7. the icon is distinct from Copy ---------- */
{
  const icon = (name) => {
    const at = app.indexOf(`    ${name}: \``);
    assert(at >= 0, `ICON.${name} not found`);
    return app.slice(at, app.indexOf("`,", at + 10));
  };
  const dup = icon("duplicate"), cp = icon("copy");
  assert(dup !== cp,
    "ICON.duplicate must not be the same glyph as ICON.copy - they sit side by "
    + "side in every panel row and mean different things (clipboard vs new doc)");
  assert(/<line/.test(dup),
    "ICON.duplicate should carry the distinguishing plus strokes");
}

/* ---------- 8. the real loaders must LEAVE __currentEditorDoc set ---------- */
{
  /* The invariant the dispatcher depends on and section 9 cannot see, because
     it stubs the loaders. duplicateDocument confirms the source really loaded
     by reading the value back:

         await loader(docNo);
         if (!window.__currentEditorDoc || ...no !== docNo) return false;

     hideAllViews() - which every loader calls to swap views, and which
     showEditorView() calls internally - sets it to null. So a loader that
     assigns it BEFORE that call returns with it null, the guard fails, and
     Duplicate silently does nothing except open the original for editing.

     That is exactly what shipped: quote, dn and inv all assigned it before
     their own hideAllViews(), and only PO worked, because PO assigns it a
     SECOND time after showEditorView(). It also broke the Open-panel row
     highlight for those, since the setter is what calls markActive().

     Comments are stripped first: the explanatory comment now sitting above
     each assignment mentions hideAllViews(), and would otherwise satisfy the
     ordering check on its own. */
  const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  const CLEARERS = ["hideAllViews()", "showEditorView()"];

  const loaders = [
    ["loadForEdit", app, "po"],
    ["loadQuoteForEdit", editors.quote, "quote"],
    ["loadDnForEdit", editors.dn, "dn"],
    ["loadInvForEdit", editors.inv, "inv"],
  ];

  for (const [fn, src, kind] of loaders) {
    const at = src.indexOf(`async function ${fn}`);
    assert(at >= 0, `${fn} not found`);
    const body = strip(src.slice(at, src.indexOf("\n}", at)));

    const setAt = body.lastIndexOf("__currentEditorDoc =");
    assert(setAt >= 0,
      `${fn} never sets window.__currentEditorDoc - Duplicate cannot confirm the `
      + "source loaded, and the panel row will not highlight");
    assert(new RegExp(`__currentEditorDoc\\s*=\\s*\\{\\s*kind:\\s*"${kind}"`).test(body),
      `${fn} must set __currentEditorDoc with kind "${kind}"`);

    for (const call of CLEARERS) {
      const clearAt = body.lastIndexOf(call);
      if (clearAt < 0) continue;
      assert(setAt > clearAt,
        `${fn}: the last __currentEditorDoc assignment comes BEFORE ${call}, which `
        + `clears it. By the time ${fn} returns the value is null, so the Duplicate `
        + `button silently does nothing (you just get ${kind} open for editing) and `
        + `the Open-panel row never highlights. Move the assignment after ${call}.`);
    }
  }
}

/* ---------- 9. the pending row is wired into all five renderers ---------- */
{
  /* A duplicate is unsaved, so no month list can return it and the panel would
     show nothing until Save. `pendingRowEl` renders a provisional row for it.

     Two placement rules matter more than they look:

       - it must be built BEFORE the empty-state early-return, or the first
         document of a new month (an empty list) returns early and the only
         row there is never renders;
       - it must be built from the UNFILTERED month list, because that list is
         what proves the duplicate has been saved and the row can go. Passing
         the filtered list would keep the provisional row on screen next to the
         real one whenever a type or client chip hid the saved document. */
  const renderers = [
    ["app.js",   app,           "po",    "po_no",    "list"],
    ["quote.js", editors.quote, "quote", "quote_no", "qOpenAllQuotes"],
    ["dn.js",    editors.dn,    "dn",    "dn_no",    "dnOpenAll"],
    ["inv.js",   editors.inv,   "inv",   "inv_no",   "invOpenAll"],
  ];
  for (const [name, src, kind, keyField, listVar] of renderers) {
    const call = new RegExp(`pendingRowEl\\("${kind}"[\\s\\S]{0,220}?${listVar},\\s*"${keyField}"\\)`);
    assert(call.test(src),
      `${name}: its list renderer must call pendingRowEl("${kind}", year, month, `
      + `${listVar}, "${keyField}") - with the UNFILTERED list, which is what `
      + "retires the provisional row once the duplicate is saved");
    assert(/if \(!list\.length && !pendingRow\)/.test(src) || /list\.length === 0 && !pendingRow/.test(src),
      `${name}: the empty-state guard must also test !pendingRow, or a month `
      + "whose only document is the unsaved duplicate returns early and shows "
      + "the empty state instead of the row");
    assert(/if \(pendingRow\) box\.appendChild\(pendingRow\)/.test(src),
      `${name}: the pending row is created but never appended`);
  }

  // Cleared when you leave the editor or start a new document.
  assert(/clearPendingDoc\(\)/.test(app.slice(app.indexOf("function hideAllViews"),
                                              app.indexOf("function hideAllViews") + 1600)),
    "hideAllViews must clear the pending duplicate - it belongs to the editor "
    + "you are navigating away from");
  for (const [name, src] of [["quote.js", editors.quote], ["dn.js", editors.dn],
                             ["inv.js", editors.inv]]) {
    assert(/clearPendingDoc\(\)/.test(src),
      `${name}: the New/reset path must clear the pending duplicate`);
  }
}

/* ---------- 10. run the real pendingRowEl ---------- */
{
  const start = app.indexOf("let _pendingDoc = null;");
  const end = app.indexOf("window.pendingRowEl = pendingRowEl;", start);
  assert(start >= 0 && end > start, "cannot slice the pending-doc block out of app.js");
  const src = app.slice(start, end);

  // Minimal DOM: pendingRowEl only builds one element and sets innerHTML.
  const made = [];
  const documentStub = {
    createElement: (tag) => {
      const el = { tagName: tag, className: "", title: "", innerHTML: "" };
      made.push(el);
      return el;
    },
  };
  // The slice assigns the helpers onto `window`, so it needs one.
  const mod = new Function("document", "esc", "window", src + `
    return { setPendingDoc, clearPendingDoc, currentPendingDoc, pendingRowEl };`
  )(documentStub, (s) => String(s), {});

  const PEND = { kind: "quote", no: "QUT-MAT-ACME-9", sourceNo: "QUT-MAT-ACME-3", year: 2026, month: 8 };

  // (a) shows in its own kind, year and month
  {
    mod.setPendingDoc({ ...PEND });
    const el = mod.pendingRowEl("quote", 2026, 8, [], "quote_no");
    assert(el, "the pending row must render for its own kind/month");
    assert(/is-pending/.test(el.className), "the row needs the is-pending class");
    assert(!/is-clickable/.test(el.className),
      "the pending row must NOT be clickable - there is no saved document to "
      + "load, and it is already open in the editor");
    assert(el.innerHTML.includes("QUT-MAT-ACME-9"), "the row must show the new number");
    assert(el.innerHTML.includes("QUT-MAT-ACME-3"),
      "the row must name the document it was copied from");
    assert(/Not saved yet/.test(el.innerHTML),
      "the row must say IN WORDS that it is unsaved - colour alone is not a "
      + "usable signal here");
  }

  // (b) wrong kind / wrong month → nothing
  {
    mod.setPendingDoc({ ...PEND });
    assert(!mod.pendingRowEl("dn", 2026, 8, [], "dn_no"), "must not leak into another kind's pane");
    assert(!mod.pendingRowEl("quote", 2026, 7, [], "quote_no"),
      "must not appear while browsing a different month - the duplicate is "
      + "dated today and belongs to today's month only");
    assert(!mod.pendingRowEl("quote", 2025, 8, [], "quote_no"), "must check the year too");
  }

  // (c) once the real document is in the list, the row retires itself
  {
    mod.setPendingDoc({ ...PEND });
    const list = [{ quote_no: "QUT-MAT-ACME-9" }];
    assert(!mod.pendingRowEl("quote", 2026, 8, list, "quote_no"),
      "the provisional row must disappear when the saved document arrives, or "
      + "the panel shows the same number twice");
    assert(mod.currentPendingDoc() === null,
      "seeing the saved document must also CLEAR the pending state - otherwise "
      + "it lingers and reappears the moment a filter hides the real row");
  }

  // (d) nothing pending → nothing rendered
  {
    mod.clearPendingDoc();
    assert(!mod.pendingRowEl("quote", 2026, 8, [], "quote_no"),
      "no pending document must render no row");
  }

  // (e) the PO pane is Arabic
  {
    mod.setPendingDoc({ kind: "po", no: "A-55", sourceNo: "A-12", year: 2026, month: 8 });
    const el = mod.pendingRowEl("po", 2026, 8, [], "po_no");
    assert(el && /لم يُحفظ بعد/.test(el.innerHTML),
      "the PO panel is Arabic - its pending row must be too, like every other "
      + "string in that list");
    mod.clearPendingDoc();
  }
}

/* ---------- 11. run the real dispatcher ---------- */
async function runDispatcherTests() {
  // Pull duplicateDocument + its helpers out of app.js and execute them.
  const start = app.indexOf("const DUPLICATE_HOOK = {");
  const end = app.indexOf("/* PO's own hook.", start);
  assert(end > start, "cannot slice the duplicate dispatcher out of app.js");
  const src = app.slice(start, end);

  function build(overrides) {
    const calls = [];
    const env = {
      toast: (m, err) => calls.push(["toast", m, !!err]),
      showModal: async (o) => { calls.push(["modal", o.title]); return env.__modalChoice; },
      esc: (s) => String(s),
      ICON: { duplicate: "<svg/>" },
      activeDirtyEditor: () => env.__dirty,
      safe: (fn) => fn,
      window: {},
      calls,
      __modalChoice: "discard",
      __dirty: null,
      ...overrides,
    };
    env.window = env.window || {};
    // The slice now also covers the pending-duplicate helpers, so it needs a
    // `document` (showPendingInPanel reads the pane's month inputs) and a
    // `todayInfo` (the duplicate is filed under today's month).
    env.document = env.document || { getElementById: () => null };
    env.todayInfo = env.todayInfo || (() => ({ year: 2026, month: 8 }));
    const factory = new Function(
      "toast", "showModal", "esc", "ICON", "activeDirtyEditor", "safe", "window",
      "document", "todayInfo",
      src + "\n return { duplicateDocument, duplicateBtnHTML, DUPLICATE_HOOK,"
          + " currentPendingDoc };");
    return { env, api: factory(env.toast, env.showModal, env.esc, env.ICON,
                               () => env.__dirty, env.safe, env.window,
                               env.document, env.todayInfo) };
  }

  // (a) unknown kind → toasts, does not throw
  {
    const { env, api } = build({});
    const ok = await api.duplicateDocument("lpo", "X-1");
    assert(ok === false, "an unknown kind must return false");
    assert(env.calls.some(c => c[0] === "toast" && /Cannot duplicate this document type/.test(c[1])),
      "an unknown kind must toast an error");
  }

  // (b) blank number → no-op
  {
    const { api } = build({});
    assert((await api.duplicateDocument("po", "   ")) === false,
      "a blank document number must be a no-op");
  }

  // (c) happy path: loader runs, then the hook, and the hook's number is toasted
  {
    const { env, api } = build({});
    const order = [];
    env.window.loadForEdit = async (no) => {
      order.push("load:" + no);
      env.window.__currentEditorDoc = { kind: "po", no };
    };
    env.window.poMakeDuplicate = async () => { order.push("hook"); return "A-99"; };
    const ok = await api.duplicateDocument("po", "A-12");
    assert(ok === true, "the happy path must return true");
    assert(order.join(",") === "load:A-12,hook",
      "the loader must run BEFORE the hook, got: " + order.join(","));
    assert(env.calls.some(c => c[0] === "toast" && c[1].includes("A-99")),
      "the toast must name the new number so the user knows what Save will write");
    // The one place the pending row gets armed. If this stops happening the
    // panel goes back to showing nothing until Save.
    const pend = api.currentPendingDoc();
    assert(pend && pend.kind === "po" && pend.no === "A-99" && pend.sourceNo === "A-12",
      "duplicateDocument must record the pending duplicate (kind/new no/source), "
      + "got: " + JSON.stringify(pend));
    assert(+pend.year === 2026 && +pend.month === 8,
      "the pending duplicate must be filed under TODAY's month - that is the "
      + "date the hook stamped on it, and the pane it will show up in");
  }

  // (c2) a hook that cannot derive a number arms nothing
  {
    const { env, api } = build({});
    env.window.loadForEdit = async (no) => { env.window.__currentEditorDoc = { kind: "po", no }; };
    env.window.poMakeDuplicate = async () => "";      // number lookup failed
    await api.duplicateDocument("po", "A-12");
    assert(api.currentPendingDoc() === null,
      "with no new number there is nothing to show in the panel - arming a "
      + "pending row with a blank number would render an anonymous ghost row");
  }

  // (d) the load failed (missing record) → do NOT convert whatever is open
  {
    const { env, api } = build({});
    let hooked = false;
    env.window.loadForEdit = async () => { /* loader bailed; sets nothing */ };
    env.window.poMakeDuplicate = async () => { hooked = true; return "A-99"; };
    const ok = await api.duplicateDocument("po", "A-404");
    assert(ok === false, "a failed load must return false");
    assert(!hooked,
      "the hook must NOT run when the loader bailed - it would turn the document "
      + "already open in the editor into a duplicate of nothing");
  }

  // (e) a stale __currentEditorDoc from a PREVIOUS document must not pass
  {
    const { env, api } = build({});
    let hooked = false;
    env.window.__currentEditorDoc = { kind: "po", no: "A-7" };   // left over
    env.window.loadForEdit = async () => { /* bails */ };
    env.window.poMakeDuplicate = async () => { hooked = true; return "A-99"; };
    assert((await api.duplicateDocument("po", "A-404")) === false,
      "a stale __currentEditorDoc must not be mistaken for a successful load");
    assert(!hooked, "the hook must not run off a stale __currentEditorDoc");
  }

  /* (f) and (g) MUST define both the loader and the hook. An earlier version set
     only the loader, so the missing-hook guard returned false before the loader
     ever ran - both tests passed for the wrong reason and did not notice when
     the dirty guard was deleted outright. Mutation testing caught it. */
  const wireOk = (env, flag) => {
    env.window.loadForEdit = async (no) => {
      flag.loaded = true;
      env.window.__currentEditorDoc = { kind: "po", no };
    };
    env.window.poMakeDuplicate = async () => { flag.hooked = true; return "A-99"; };
  };

  // (f) dirty editor: Cancel aborts before anything is loaded
  {
    const { env, api } = build({});
    const flag = {};
    env.__dirty = { kind: "Quotation", save: async () => true };
    env.__modalChoice = "cancel";
    wireOk(env, flag);
    assert((await api.duplicateDocument("po", "A-12")) === false, "Cancel must abort");
    assert(!flag.loaded,
      "Cancel must abort BEFORE the loader runs - loading replaces the editor "
      + "contents, which is exactly the unsaved work the prompt is protecting");
    assert(env.calls.some(c => c[0] === "modal"), "the prompt must actually be shown");
  }

  // (g) dirty editor: Save that fails aborts too
  {
    const { env, api } = build({});
    const flag = {};
    env.__dirty = { kind: "Quotation", save: async () => false };   // save failed
    env.__modalChoice = "save";
    wireOk(env, flag);
    assert((await api.duplicateDocument("po", "A-12")) === false,
      "a failed save must abort the duplicate");
    assert(!flag.loaded,
      "a failed save must not go on to load over the unsaved work anyway");
  }

  // (g2) no dirty editor at all → no prompt, straight through
  {
    const { env, api } = build({});
    const flag = {};
    env.__dirty = null;
    wireOk(env, flag);
    assert((await api.duplicateDocument("po", "A-12")) === true, "clean editor must proceed");
    assert(!env.calls.some(c => c[0] === "modal"),
      "a clean editor must not be prompted - the prompt only exists to protect "
      + "unsaved work, and asking every time trains people to click through it");
  }

  // (h) dirty editor: Discard proceeds; Save that succeeds proceeds
  for (const [choice, saveResult] of [["discard", true], ["save", true]]) {
    const { env, api } = build({});
    const flag = {};
    env.__dirty = { kind: "Quotation", save: async () => saveResult };
    env.__modalChoice = choice;
    wireOk(env, flag);
    assert((await api.duplicateDocument("po", "A-12")) === true,
      `"${choice}" must proceed with the duplicate`);
    assert(flag.loaded && flag.hooked,
      `"${choice}" must go on to load AND convert, got loaded=${!!flag.loaded} hooked=${!!flag.hooked}`);
  }

  // (i) missing hook → clear error, no crash
  {
    const { env, api } = build({});
    env.window.loadForEdit = async (no) => { env.window.__currentEditorDoc = { kind: "po", no }; };
    // poMakeDuplicate deliberately absent
    assert((await api.duplicateDocument("po", "A-12")) === false,
      "a missing hook must return false rather than throwing");
    assert(env.calls.some(c => c[0] === "toast" && /not loaded/.test(c[1])),
      "a missing hook must explain itself");
  }

  // (j) the button markup carries what the handler reads back
  {
    const { api } = build({});
    const html = api.duplicateBtnHTML("quote", "QUT-MAT-ACME-003");
    assert(/data-act="duplicate"/.test(html), "missing data-act");
    assert(/data-kind="quote"/.test(html), "missing data-kind");
    assert(/data-no="QUT-MAT-ACME-003"/.test(html), "missing data-no");
  }
}

runDispatcherTests().then(() => {
  console.log("Duplicate document tests passed");
}, (err) => {
  console.error(err);
  process.exit(1);
});
