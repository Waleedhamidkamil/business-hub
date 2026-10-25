/* The shared "unsaved changes" rule (2026-10-01).

   Waleed opened an existing purchase order from the Open panel, touched
   nothing, pressed New, and was asked whether to save. He asked for two
   things: the prompt must appear only after a real edit, and the rule must be
   shared by every editor instead of being re-written four times.

   The bug was that PO's New button tested CONTENT and never EDITS:

       if (filledRows().length === 0) { straight to New }      <-- PO
       if (filled > 0 && state.dirty) { prompt }               <-- qut/dn/inv

   So this file does two jobs:
     1. drives the REAL static/unsaved_guard.js and proves the rule, including
        the exact case he reported (dirty = false, rows present -> no prompt),
     2. greps each editor for the old per-editor copy of the rule, because a
        correct shared module is worthless if someone re-inlines the test.

   Runs in plain node - no browser.
*/
const fs = require("fs");
const vm = require("vm");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/* ---------- load the real module into a stub window ---------- */
const src = fs.readFileSync("static/unsaved_guard.js", "utf8");

function freshGuard() {
  const win = {};
  const ctx = { window: win };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  assert(win.unsavedGuard, "static/unsaved_guard.js did not publish window.unsavedGuard");
  return win;
}

/* A fake editor whose dirty flag and rows we drive from the test. */
function fakeEditor(win, kind, opts = {}) {
  const e = {
    dirty: false,
    rows: [],
    saveResult: true,
    saveCalls: [],
  };
  win.unsavedGuard.register(kind, {
    noun: opts.noun || kind,
    label: opts.label,
    dirty: () => e.dirty,
    filled: () => e.rows,
    save: (o) => { e.saveCalls.push(o); return Promise.resolve(e.saveResult); },
  });
  return e;
}

/* Records every modal shown and answers with a queued choice. */
function stubModal(win, answers) {
  const shown = [];
  win.showModal = (options) => {
    shown.push(options);
    return Promise.resolve(answers.length ? answers.shift() : null);
  };
  return shown;
}

const KINDS = ["po", "quote", "dn", "inv"];

/* ---------- 1. THE RULE: dirty AND non-empty, for every editor ---------- */
(async () => {
  for (const kind of KINDS) {
    const win = freshGuard();
    const ed = fakeEditor(win, kind);
    const shown = stubModal(win, []);

    // (a) the reported bug: an existing document, opened and not edited.
    ed.dirty = false;
    ed.rows = [{ name: "Tomato" }, { name: "Cucumber" }];
    assert(win.unsavedGuard.isUnsaved(kind) === false,
      `${kind}: an opened, unedited document must NOT count as unsaved - this is `
      + "exactly what Waleed reported on 2026-10-01");
    assert(await win.unsavedGuard.confirmNew(kind) === true,
      `${kind}: New must go straight through when nothing was edited`);
    assert(await win.unsavedGuard.confirmLeave(kind) === true,
      `${kind}: Home must go straight through when nothing was edited`);
    assert(shown.length === 0,
      `${kind}: no modal may be shown for an unedited document, got ${shown.length}`);

    // (b) dirty but empty: a blank editor whose date you nudged. No nagging.
    ed.dirty = true;
    ed.rows = [];
    assert(win.unsavedGuard.isUnsaved(kind) === false,
      `${kind}: a dirty but EMPTY editor has nothing worth saving`);
    assert(shown.length === 0, `${kind}: still no modal for a dirty empty editor`);

    // (c) dirty with content: the one case that must ask.
    ed.rows = [{ name: "Tomato" }];
    assert(win.unsavedGuard.isUnsaved(kind) === true,
      `${kind}: an edited document with rows IS unsaved work`);
  }

  /* ---------- 2. every answer on the New prompt ---------- */
  for (const kind of KINDS) {
    // Cancel -> stay put
    {
      const win = freshGuard();
      const ed = fakeEditor(win, kind);
      ed.dirty = true; ed.rows = [{ name: "x" }];
      const shown = stubModal(win, ["cancel"]);
      assert(await win.unsavedGuard.confirmNew(kind) === false,
        `${kind}: Cancel must abort New`);
      assert(shown.length === 1, `${kind}: expected exactly one modal`);
      assert(ed.saveCalls.length === 0, `${kind}: Cancel must not save`);
      // Closing the modal (null) is Cancel, not Discard - losing work on an
      // Esc keypress would be the worst possible reading of a null.
      const win2 = freshGuard();
      const ed2 = fakeEditor(win2, kind);
      ed2.dirty = true; ed2.rows = [{ name: "x" }];
      stubModal(win2, [null]);
      assert(await win2.unsavedGuard.confirmNew(kind) === false,
        `${kind}: dismissing the modal must abort New, never discard`);
    }
    // Discard -> proceed without saving
    {
      const win = freshGuard();
      const ed = fakeEditor(win, kind);
      ed.dirty = true; ed.rows = [{ name: "x" }];
      stubModal(win, ["discard"]);
      assert(await win.unsavedGuard.confirmNew(kind) === true,
        `${kind}: Discard must proceed`);
      assert(ed.saveCalls.length === 0, `${kind}: Discard must not save`);
    }
    // Save that works -> proceed. Save that fails -> stay put.
    {
      const win = freshGuard();
      const ed = fakeEditor(win, kind);
      ed.dirty = true; ed.rows = [{ name: "x" }];
      stubModal(win, ["save"]);
      assert(await win.unsavedGuard.confirmNew(kind) === true,
        `${kind}: a successful Save must proceed to New`);
      assert(ed.saveCalls.length === 1, `${kind}: Save must call the editor's save`);
      assert(ed.saveCalls[0] && ed.saveCalls[0].skipConfirm === true,
        `${kind}: the New save must pass skipConfirm - the user already answered`);
      assert(!(ed.saveCalls[0] || {}).stay,
        `${kind}: the New save must NOT pass stay - each editor's "Save & New" is `
        + "its normal save, and for PO that means the export path with his "
        + "after-export action");

      const win2 = freshGuard();
      const ed2 = fakeEditor(win2, kind);
      ed2.dirty = true; ed2.rows = [{ name: "x" }]; ed2.saveResult = false;
      stubModal(win2, ["save"]);
      assert(await win2.unsavedGuard.confirmNew(kind) === false,
        `${kind}: a FAILED save must not throw away the document by starting a new one`);
    }
  }

  /* ---------- 3. the Home prompt is leave-or-cancel, never a silent leave ---------- */
  {
    const win = freshGuard();
    const ed = fakeEditor(win, "po");
    ed.dirty = true; ed.rows = [{ name: "x" }];
    const shown = stubModal(win, ["ok", "cancel", null]);
    assert(await win.unsavedGuard.confirmLeave("po") === true, "'ok' must leave");
    assert(await win.unsavedGuard.confirmLeave("po") === false, "Cancel must stay");
    assert(await win.unsavedGuard.confirmLeave("po") === false,
      "a dismissed modal must stay, not leave");
    assert(shown.length === 3, "each attempt must ask");
    for (const options of shown) {
      const values = options.buttons.map(b => b.value);
      assert(values.includes("cancel") && values.includes("ok"),
        "the leave prompt needs both a cancel and a leave button, got " + values.join("/"));
      assert(!values.includes("discard"),
        "the leave prompt must not offer Discard - 'leave without saving' IS the discard");
    }
  }

  /* ---------- 4. dirtyEditor() answers the same question ---------- */
  {
    const win = freshGuard();
    const po = fakeEditor(win, "po", { label: "Purchase Order" });
    const qut = fakeEditor(win, "quote", { label: "Quotation" });

    // An opened-but-unedited PO must not make the profile switcher ask.
    po.rows = [{ name: "x" }];
    assert(win.unsavedGuard.dirtyEditor() === null,
      "dirtyEditor must ignore an opened, unedited document - otherwise switching "
      + "company asks to save a document nobody touched");

    qut.dirty = true; qut.rows = [{ name: "x" }];
    const found = win.unsavedGuard.dirtyEditor();
    assert(found && found.kind === "Quotation",
      "dirtyEditor must name the editor holding the work, got " + JSON.stringify(found));
    await found.save();
    assert(qut.saveCalls.length === 1, "dirtyEditor().save must reach the editor");
    assert(qut.saveCalls[0].stay === true && qut.saveCalls[0].skipConfirm === true,
      "the profile-switch / duplicate save must pass stay+skipConfirm - the user "
      + "stays where they are, so the editor must not reset itself");
  }

  /* ---------- 5. an unregistered editor never blocks ---------- */
  {
    const win = freshGuard();
    stubModal(win, []);
    assert(win.unsavedGuard.isUnsaved("nope") === false,
      "an unknown kind must answer false rather than throw");
    assert(await win.unsavedGuard.confirmNew("nope") === true,
      "an unknown kind must not block the New button");
  }

  /* ---------- 6. the editors route through the module, not their own copy ---------- */
  {
    const files = {
      "static/app.js": { kind: "po", fn: "onNewClicked" },
      "static/quote.js": { kind: "quote", fn: "quoteNew" },
      "static/dn.js": { kind: "dn", fn: "dnNew" },
      "static/inv.js": { kind: "inv", fn: "invNew" },
    };
    for (const [file, { kind, fn }] of Object.entries(files)) {
      const js = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
      assert(js.includes(`window.unsavedGuard.register("${kind}"`),
        `${file} must register "${kind}" with the shared guard, or the profile `
        + "switcher and the New button disagree about what is unsaved");
      assert(js.includes(`unsavedGuard.confirmNew("${kind}")`),
        `${fn} in ${file} must ask the shared guard`);

      // The old inline rule must be gone. This is the assertion that matters:
      // the module is only a fix for as long as nobody re-inlines the test.
      const body = js.slice(js.indexOf(`function ${fn}(`));
      const end = body.indexOf("\n}\n");
      const inner = body.slice(0, end < 0 ? 400 : end);
      assert(!/showModal\(/.test(inner),
        `${fn} builds its own unsaved modal again - the prompt belongs to `
        + "static/unsaved_guard.js so all four editors ask the same question");
      assert(!/\.dirty\b|dirtySinceSave/.test(inner),
        `${fn} reads a dirty flag directly again - the rule belongs to `
        + "static/unsaved_guard.js");
      assert(!/filled\w*Rows\(\)\.length/.test(inner),
        `${fn} tests row count directly again - that was the original bug: PO's `
        + "New looked at content and never at whether anything was edited");
    }

    // index.html must load the module BEFORE any editor registers with it.
    const html = fs.readFileSync("templates/index.html", "utf8");
    // Match the <script> TAG, not the bare filename: app.js is named in three
    // explanatory comments far earlier in the file, and indexOf found those.
    const tag = (f) => html.indexOf(`<script src="../static/${f}">`);
    const guardAt = tag("unsaved_guard.js");
    assert(guardAt >= 0, "templates/index.html does not load static/unsaved_guard.js");
    for (const f of ["app.js", "quote.js", "dn.js", "inv.js"]) {
      assert(tag(f) > 0, `templates/index.html no longer loads static/${f}`);
      assert(guardAt < tag(f),
        `unsaved_guard.js must be loaded before ${f} - it registers on load`);
    }
  }

  console.log("Unsaved-changes guard tests passed");
})().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
