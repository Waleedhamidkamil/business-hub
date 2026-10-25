/* Filing a client LPO in one pass, and handing everything it knows to the next
   document (2026-09-28).

   Four things Waleed had to do by hand, each of which the app already held the
   answer to:

     1. Add the LPO, find it in the list, THEN attach their PDF. The file is
        chosen in the same dialog now, and uploaded the moment the record
        exists - `attach_lpo_file_data` refuses a PO it cannot find, which is
        why it happens after the save and not before.
     2. Type the quotation number from memory. HDEC has 41 of them and a wrong
        one is silent: the lines pull from the wrong deal and the invoice made
        off that LPO months later is costed against it.
     3. Retype the total that is printed on the quotation he just picked.
     4. Retype the quotation number and every line's COST on the delivery note
        or invoice made from the LPO.

   Part 5 is a REAL behaviour test, not a source string: `smartMapRow` appended
   `cost_ccy` to the description as prose, so a seeded row arrived reading
   "1650*850*800 / USD · USD". Nothing in the live data hit it yet; every LPO
   seeded off a quotation would have.

   Source-level for the wiring, which is the part that rots. The seed's
   arithmetic is covered in `tests/lpo_quote_and_costs.test.py`. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const read = (...p) => fs.readFileSync(path.join(root, ...p), "utf8");
const LPO = read("static", "lpo.js");
const INV = read("static", "inv.js");
const DN = read("static", "dn.js");
const APP = read("static", "app.js");
const HTML = read("templates", "index.html");
const PICKER = read("static", "quote_picker.js");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

/* Read a FUNCTION BODY, never the whole file: an earlier test in this repo
   asserted on an attribute and was satisfied by the comment explaining it. */
function body(src, startsWith, endsWith) {
    const at = src.indexOf(startsWith);
    assert.ok(at > 0, "could not find `" + startsWith + "`");
    const to = src.indexOf(endsWith, at);
    return src.slice(at, to > at ? to : at + 4000);
}

// ---------------------------------------------------------------- 1. attach
check("the add form has a file box for their PO document", () => {
    assert.ok(/id="lpoFFile"/.test(LPO), "no file input on the form");
    assert.ok(/accept="\.pdf,\.png,\.jpg,\.jpeg"/.test(LPO),
        "it must accept the same kinds the Attach button does");
});

check("the chosen file is read BEFORE the dialog closes", () => {
    /* `Popup.close` detaches the panel before it resolves, so anything read
       after it is gone - that is exactly how all twelve fields arrived empty
       on 2026-09-19. The file has to be captured beside the payload. */
    const submit = body(LPO, "async function submitLpo", "\n    const choice");
    const getFile = submit.indexOf("lpoFFile");
    const close = submit.indexOf("h.close(");
    assert.ok(getFile > 0, "submitLpo never reads the file box");
    assert.ok(close > 0, "submitLpo never closes the dialog");
    assert.ok(getFile < close,
        "the file is read AFTER the dialog closes, which reads undefined - "
        + "capture it beside the payload, like every other field");
});

check("the upload runs after the save, and is shared with the Attach button", () => {
    assert.ok(/async function attachLpoData\(/.test(LPO),
        "the upload must be its own function, or the form and the Attach "
        + "button grow two copies of the replace-the-acknowledged-copy rule");
    const attach = body(LPO, "function attachLpoFile", "input.click();");
    assert.ok(/attachLpoData\(/.test(attach),
        "the Attach button must go through the same upload");
    const tail = body(LPO, "const choice = await handle.result", "\n}");
    assert.ok(/attachLpoData\(/.test(tail),
        "nothing uploads the file the form collected");
    assert.ok(tail.indexOf("attachLpoData") > tail.indexOf("choice !== \"ok\""),
        "the file must only be sent once the record saved - "
        + "attach_lpo_file_data refuses a PO that is not on file");
});

// ------------------------------------------------------------- 2+3. the pick
check("the quotation bar has a Select button", () => {
    assert.ok(/id="lpoPickQuote"/.test(LPO), "no Select button");
    assert.ok(/id="lpoPullLines"/.test(LPO), "Pull lines must stay");
    const wire = body(LPO, "function wireLpoForm", "\n    function buildLpoPayload");
    assert.ok(/lpoPickQuote[\s\S]{0,120}onclick\s*=\s*pickQuote/.test(wire),
        "the Select button is not wired to anything");
});

check("Select lists THIS client's quotations, not everyone's", () => {
    assert.ok(/window\.QuotePicker/.test(LPO), "the form does not use the picker");
    const pick = body(LPO, "async function pickQuote", "\n    function wireLpoForm");
    assert.ok(/lpoFClient/.test(pick),
        "it must pass the client from the form - a picker that ignores the "
        + "client offers every client's quotations, which is how the wrong "
        + "deal gets cited");
    const choices = body(PICKER, "async function choicesFor", "\n    /* The dialog");
    assert.ok(/if\s*\(!client\)\s*return\s*\[\]/.test(choices),
        "a blank client must return nothing, never every quotation");
    assert.ok(/client_quotes\(/.test(choices),
        "it must ask the identity-aware bridge, so a PO typed under HDEC "
        + "finds the quotations saved under the legal name");
});

check("the picker never prints a total it could not read", () => {
    const describe = body(PICKER, "function describe(row)", "\n    /* The quotations");
    assert.ok(/total_readable/.test(describe),
        "an equipment quotation keeps its quantities in per-document custom "
        + "columns, so nothing here can total it - printing $0.00 beside a "
        + "real quotation reads as free");
});

check("picking a quotation fills the total value", () => {
    const pick = body(LPO, "async function pickQuote", "\n    function wireLpoForm");
    assert.ok(/lpoFTotal/.test(pick), "the total box is never looked up");
    /* The ASSIGNMENT, not just the lookup. The first draft of this check
       matched the `getElementById` and the guard below it, and stayed green
       with the line that actually writes the figure deleted. */
    assert.ok(/totalBox\.value\s*=\s*String\(/.test(pick),
        "nothing writes the quotation's total into the box");
    assert.ok(/Number\(pulled\.total\)/.test(pick),
        "it must take the QUOTATION's own total, not the sum of the lines that "
        + "happened to come across - when those differ, the gap is the thing "
        + "worth seeing, and filling the line sum hides it");
    assert.ok(/parseFloat\(totalBox\.value/.test(pick),
        "it must not overwrite a figure already typed off the PO in front of "
        + "him - their total is legitimately not ours");
});

// ------------------------------------------------- 4. the hand-off onwards
check("the delivery note and invoice seed from the LPO's own record", () => {
    assert.ok(/async function lpoSeedForDocument/.test(INV),
        "nothing reads the seed");
    assert.ok(/lpo_document_seed\(/.test(INV),
        "the seed must come from the bridge, which knows the quotation");
    assert.ok(/lpo_fulfillment\(/.test(INV),
        "keep the fulfilment fallback so an older backend still starts a "
        + "document rather than an empty one");
});

check("the quotation number is carried onto both documents", () => {
    const inv = body(INV, "async function createInvoiceFromLpo", "\n    updateInvLpoWarning");
    assert.ok(/invState\.sourceQuote\s*=\s*seed\.source_quote_no/.test(inv),
        "the invoice drops the quotation the LPO already knew");
    assert.ok(/invSrcQuote/.test(inv), "...and never shows it in the field");
    const dn = body(DN, "async function createDnFromLpo", "\n    updateLpoWarning");
    assert.ok(/dnState\.sourceQuote\s*=\s*seed\.source_quote_no/.test(dn),
        "the delivery note drops it");
    assert.ok(/dnSrcQuote/.test(dn), "...and never shows it in the field");
});

check("a carried cost is converted into the destination's currency", () => {
    for (const [name, src, start, end] of [
        ["invoice", INV, "async function createInvoiceFromLpo", "\n    updateInvLpoWarning"],
        ["delivery note", DN, "async function createDnFromLpo", "\n    updateLpoWarning"],
    ]) {
        const fn = body(src, start, end);
        assert.ok(/reconcileImportedCostCurrency\(/.test(fn),
            `the ${name} copies the cost raw - 250,000 IQD lands in a dollar `
            + "document reading as $250,000");
        assert.ok(fn.indexOf("reconcileImportedCostCurrency")
                  > fn.indexOf("smartMapRow"),
            `the ${name} must convert AFTER the rows are mapped`);
    }
});

check("the toast says how many lines actually got a cost", () => {
    assert.ok(/function lpoSeedToast/.test(INV), "no seed toast");
    const fn = body(INV, "function lpoSeedToast", "\nasync function createInvoiceFromLpo");
    assert.ok(/costed/.test(fn) && /uncosted/.test(fn),
        "silence about the lines with no cost is how a document gets invoiced "
        + "at an invented profit");
});

// --------------------------------------- 5. the currency-in-the-prose leak
check("a row's currency stamps never leak into its description", () => {
    /* A REAL run, not a source string. `cost_ccy: "USD"` is text, so the
       leftovers sweep appended it to the description. */
    const sandbox = { window: {} };
    const from = APP.indexOf("const IMPORT_KEY_ALIASES") >= 0
        ? Math.min(APP.indexOf("const IMPORT_KEY_ALIASES"),
                   APP.indexOf("const IMPORT_SKIP_KEYS"))
        : APP.indexOf("const IMPORT_SKIP_KEYS");
    const to = APP.indexOf("window.smartMapRow = smartMapRow;");
    assert.ok(from > 0 && to > from, "could not find smartMapRow in app.js");
    const fn = new Function("window", APP.slice(from, to) + "\nreturn smartMapRow;")(
        sandbox.window);

    const cols = [{ key: "item", kind: "text" }, { key: "description", kind: "text" },
                  { key: "unit", kind: "text" }, { key: "qty", kind: "num" },
                  { key: "rate", kind: "money" }, { key: "total", kind: "computed" }];
    const out = fn({ item: "Office desk", description: "1650*850*800", unit: "EA",
                     qty: 60, rate: "135", cost: 128.57, cost_ccy: "USD",
                     rate_ccy: "USD" }, cols, "item");
    assert.strictEqual(out.description, "1650*850*800",
        "the currency stamps were appended to the description as prose: got "
        + JSON.stringify(out.description));
    assert.strictEqual(String(out.cost), "128.57", "the cost must still carry");
    assert.strictEqual(out.cost_ccy, "USD", "...and so must its currency");

    const iqd = fn({ item: "Sign", description: "Site map", unit: "EA", qty: 1,
                     rate: "370", cost: 370000, cost_ccy: "IQD" }, cols, "item");
    assert.strictEqual(iqd.description, "Site map");
    assert.strictEqual(iqd.cost_ccy, "IQD",
        "a dinar cost must not be relabelled on the way across");
});

// ------------------------------------------------------------------ loading
check("the quotation picker is loaded by the page", () => {
    assert.ok(/quote_picker\.js/.test(HTML),
        "the module is never loaded, so the Select button finds nothing");
    assert.ok(HTML.indexOf("quote_picker.js") > HTML.indexOf("popup.js"),
        "it opens a Popup, so popup.js must load first");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
