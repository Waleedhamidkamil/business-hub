/* Invoice signed copy - the CLIENT's counter-signed scan (2026-08-27).
 *
 * The invoice got the delivery note's feature, and the dialog moved into
 * static/signed_copy.js so there is ONE of it. This test pins the two things
 * that would quietly rot:
 *   1. the invoice is wired at every point of its lifecycle (a missed reset
 *      shows the PREVIOUS invoice's copy against a new number),
 *   2. neither editor grows a second copy of the dialog.
 */
const fs = require("fs");

const html   = fs.readFileSync("templates/index.html", "utf8");
const shared = fs.readFileSync("static/signed_copy.js", "utf8");
const inv    = fs.readFileSync("static/inv.js", "utf8");
const dn     = fs.readFileSync("static/dn.js", "utf8");
const app    = fs.readFileSync("static/app.js", "utf8");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

/* Source of one function, from its `function` line to the next top-level one. */
function fnBody(src, name) {
  const re = new RegExp(`(?:async )?function ${name}\\s*\\(`);
  const m = src.match(re);
  assert(m, `${name} is missing`);
  const start = m.index;
  const next = src.indexOf("\n}", start);
  assert(next > 0, `cannot find the end of ${name}`);
  return src.slice(start, next);
}

// ---------------------------------------------------------------- 1. markup
assert(/id="btnInvSignedCopy"[^>]*class="[^"]*\bsigned-copy-btn\b/.test(html)
    || /class="[^"]*\bsigned-copy-btn\b[^"]*"[^>]*id="btnInvSignedCopy"/.test(html),
  "btnInvSignedCopy must carry the signed-copy-btn class, like the DN's");
assert(/<button id="btnInvSignedCopy"[^>]*\bdisabled\b/.test(html),
  "btnInvSignedCopy must start disabled - there is nothing to attach a copy to "
  + "until the invoice is saved");

const scriptOrder = Array.from(html.matchAll(/<script src="\.\.\/static\/([^"]+)"/g), m => m[1]);
for (const after of ["dn.js", "inv.js"]) {
  assert(scriptOrder.indexOf("signed_copy.js") > -1
      && scriptOrder.indexOf("signed_copy.js") < scriptOrder.indexOf(after),
    `signed_copy.js must load before ${after}`);
}

// ------------------------------------------------------- 2. one dialog only
for (const [name, src] of [["inv.js", inv], ["dn.js", dn]]) {
  assert(!/sc-drop|sc-waive|attach_doc_signed_copy_data/.test(src),
    `${name} carries its own signed-copy dialog again - it belongs in `
    + "signed_copy.js, or the two editors drift apart");
  assert(/window\.SignedCopy/.test(src),
    `${name} must go through window.SignedCopy`);
}
// The bridge is called kind-first, so one dialog serves both documents.
for (const call of ["get_doc_signed_copy", "attach_doc_signed_copy",
                    "attach_doc_signed_copy_data", "remove_doc_signed_copy"]) {
  assert(new RegExp(`${call}\\(\\s*(?:kind|"|')`).test(shared),
    `signed_copy.js must call ${call} with the document kind`);
}
assert(/KINDS\s*=\s*{[\s\S]*?\bdn:[\s\S]*?\binv:/.test(shared),
  "signed_copy.js must know both kinds");

// -------------------------------------------------- 3. invoice lifecycle
assert(/hasSignedCopy/.test(inv), "invState must track hasSignedCopy");
assert(/invState\.hasSignedCopy = false/.test(fnBody(inv, "initNewInv")),
  "initNewInv must clear hasSignedCopy - a fresh invoice cannot have one, and "
  + "a stale true leaves the toolbar button lit");
assert(/reloadInvSignedCopy\(\)/.test(fnBody(inv, "loadInvForEdit")),
  "loadInvForEdit must re-read the signed copy for the invoice being opened");
assert(/reloadInvSignedCopy\(\)/.test(fnBody(inv, "invMakeDuplicate")),
  "invMakeDuplicate must re-run reloadInvSignedCopy so the original's attached "
  + "client copy does not follow the duplicate");
assert(/reloadInvSignedCopy\(\)/.test(fnBody(inv, "invSave")),
  "a stay-save must re-read the signed copy - the invoice has only just become "
  + "saveable, so the button can finally enable");
assert(/btnInvSignedCopy|_invSignedCopyBtn\(\)/.test(inv)
    && /onclick\s*=\s*safe\(openInvSignedCopy/.test(inv),
  "the toolbar button must be wired to openInvSignedCopy");

// --------------------------------------------------------- 4. panel pill
const statusFn = app.slice(app.indexOf("window.panelStatusHTML"),
                           app.indexOf("window.panelCardMainHTML"));
const invBranch = statusFn.slice(statusFn.indexOf('kind === "inv"'),
                                statusFn.indexOf('kind === "dn"'));
assert(/clientSignedPillHTML/.test(invBranch),
  "the invoice panel row must show the client-signature pill, same as the DN");
assert(/awaitsClientSignature/.test(invBranch),
  "a paid or waived invoice must stop asking for a signed copy");
assert(/linkedPillHTML[\s\S]*paidPillHTML[\s\S]*clientSignedPillHTML/.test(invBranch),
  "the pill must show ALONGSIDE Linked/Paid, never instead of them");

// ------------------------------------------------ 5. paid prompts the attach
// Marking an invoice paid is when the client's stamped copy is actually in
// hand, so that is when we ask for it (2026-08-27). All THREE places that can
// mark an invoice paid must ask - the panel row button, the dashboard's Needs
// attention card, and the To-Do list.
const todo = fs.readFileSync("static/todo.js", "utf8");
for (const [name, src] of [["inv.js", inv], ["app.js", app], ["todo.js", todo]]) {
  const paidCalls = (src.match(/set_invoice_paid\(/g) || []).length;
  assert(paidCalls > 0, `${name} should still be a place invoices are marked paid`);
  assert(/SignedCopy\.promptAfterPaid\("inv"/.test(src),
    `${name} marks invoices paid but never prompts for the signed copy`);
}
assert(/await window\.SignedCopy\.promptAfterPaid\("inv", no\);[\s\S]{0,400}openPoFulfillPicker/
  .test(inv),
  "the panel's Paid button must AWAIT the signed-copy prompt before opening the "
  + "PO picker - two popups at once would fight over the screen");

// ---------------------------------------------- 6. promptAfterPaid behaviour
// Run the real module against stubs: it must stay silent when there is nothing
// to ask for, and it must resolve only once the dialog is closed.
(function runPromptAfterPaid() {
  const calls = { opened: 0 };
  const handles = [];
  global.window = global;
  global.document = { getElementById: () => null, querySelectorAll: () => [] };
  global.esc = (s) => String(s == null ? "" : s);
  global.safe = (fn) => fn;
  global.toast = () => {};
  global.ICON = {};
  global.Popup = {
    open() {
      calls.opened++;
      let resolve;
      const result = new Promise(r => { resolve = r; });
      const h = { panel: { querySelectorAll: () => [] }, body: {}, closed: false,
                  result, close: (v) => { h.closed = true; resolve(v); } };
      handles.push(h);
      return h;
    },
  };
  let state = { copy: null, waived: false };
  global.pywebview = { api: {
    get_doc_signed_copy: async () => state.copy,
    is_attention_ignored: async () => state.waived,
  } };

  delete require.cache[require.resolve("../static/signed_copy.js")];
  require("../static/signed_copy.js");
  const S = window.SignedCopy;

  return (async () => {
    state = { copy: { path: "x.pdf" }, waived: false };
    await S.promptAfterPaid("inv", "INV-1");
    assert(calls.opened === 0,
      "a copy is already attached - paid must not nag for another one");

    state = { copy: null, waived: true };
    await S.promptAfterPaid("inv", "INV-1");
    assert(calls.opened === 0,
      '"No signed copy expected" must silence the paid prompt too');

    state = { copy: null, waived: false };
    let settled = false;
    const p = S.promptAfterPaid("inv", "INV-1").then(() => { settled = true; });
    await new Promise(r => setTimeout(r, 20));
    assert(calls.opened === 1, "an unpaid-until-now invoice with no copy must ask");
    assert(!settled, "promptAfterPaid must not resolve while the dialog is open");
    handles[handles.length - 1].close(null);
    await p;
    assert(settled, "promptAfterPaid must resolve once the dialog closes");

    await S.promptAfterPaid("qut", "QUT-1");
    assert(calls.opened === 1, "only dn/inv keep a client signed copy");

    console.log("inv signed copy frontend tests passed");
  })();
})();
