/* Picking an LPO inside the editor must bring what the LPO knows (2026-09-28).

   Reported: "I tried picking LPO CSSP-ADM-0033 for a delivery note but the
   items didn't show up."

   He was right that the data was there. `LPO CSSP-ADM-0033` is on file with its
   client (HDEC), the quotation it answers (QUT-MT-HDEC-062), two ordered lines
   with their costs, and the signed PDF. `Api.lpo_document_seed` returns all of
   it.

   The defect was the ENTRY POINT. Two paths reach a delivery note from an LPO:

     1. The LPO screen's "make a delivery note" button -> `createDnFromLpo`,
        which seeds client, number, quotation, rows and costs.
     2. The pick button beside the LPO field in the editor he already had open
        -> `LpoPicker.pick`, which wrote `field.value` and NOTHING else.

   Path 2 is the obvious one when you are already writing the delivery note,
   and it filled a text box. Both editors had the same four-line handler.

   The fix is not a third copy of the seeding code: `adoptLpoIntoDn` /
   `adoptLpoIntoInv` are the half of the create-from-LPO path that runs once
   the editor exists, and BOTH entry points call them.

   Source-level for the wiring, which is the part that rots; the seed's
   arithmetic is covered in `tests/lpo_quote_and_costs.test.py`, and the
   behaviour was driven in a real browser. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const read = (...p) => fs.readFileSync(path.join(root, ...p), "utf8");
const DN = read("static", "dn.js");
const INV = read("static", "inv.js");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

/* Read a FUNCTION BODY, never the whole file: an earlier test in this repo
   asserted on an attribute and was satisfied by the comment explaining it.

   These files are CRLF, so an end anchor written with "\n" never matches and
   the slice used to run 6000 characters PAST the function into the next one -
   which made two of these checks vacuous (they passed with the code they were
   guarding deleted). Normalise first, and refuse to guess: a missing end
   anchor fails loudly rather than over-reading. */
function body(src, startsWith, endsWith) {
    const text = src.replace(/\r\n/g, "\n");
    const at = text.indexOf(startsWith);
    assert.ok(at > 0, "could not find `" + startsWith + "`");
    const to = text.indexOf(endsWith, at + startsWith.length);
    assert.ok(to > at,
        "could not find the end of `" + startsWith + "` (looked for "
        + JSON.stringify(endsWith) + ") - the slice would have read into the "
        + "next function and this check would prove nothing");
    return text.slice(at, to);
}

/* The end of a top-level `async function foo(` - its closing brace in column
   zero. */
const FN_END = "\n}\n";

const EDITORS = [
    { name: "delivery note", src: DN, adopt: "adoptLpoIntoDn",
      pickBtn: "dnLpoPick", field: "dnLpo", state: "dnState",
      filled: "filledDnRows", srcQuote: "dnSrcQuote" },
    { name: "invoice", src: INV, adopt: "adoptLpoIntoInv",
      pickBtn: "invLpoPick", field: "invLpo", state: "invState",
      filled: "filledInvRows", srcQuote: "invSrcQuote" },
];

// ------------------------------------------------------- the reported bug
for (const e of EDITORS) {
    check(`the ${e.name} pick button pulls the LPO's data, not just its number`, () => {
        const handler = body(e.src, `const ${e.pickBtn} = document.getElementById`,
                             "\n    document.getElementById");
        assert.ok(/LpoPicker\.pick\(/.test(handler),
            "the picker is not opened at all");
        assert.ok(new RegExp(e.adopt + "\\(").test(handler),
            `picking an LPO writes #${e.field} and stops - the ordered lines, `
            + "the quotation it answers and the costs are all on file and none "
            + "of them reach the document");
    });

    check(`the ${e.name} seeds through the one shared path`, () => {
        assert.ok(new RegExp("async function " + e.adopt + "\\(").test(e.src),
            `${e.adopt} does not exist; the pick button and the "make a `
            + "document" + "\" button must not grow two copies of the seeding");
        const adopt = body(e.src, `async function ${e.adopt}(`, "\n}\n");
        assert.ok(/lpoSeedForDocument\(/.test(adopt),
            "it must read the seed, which is what knows the quotation and costs");
        assert.ok(/smartMapRow\(/.test(adopt), "nothing maps the rows in");
        assert.ok(/reconcileImportedCostCurrency\(/.test(adopt),
            "a cost carried raw out of an IQD quotation reads as dollars");
        assert.ok(new RegExp(e.state + "\\.sourceQuote\\s*=").test(adopt),
            "the quotation the LPO answers must be stamped on the document");
        assert.ok(new RegExp(e.srcQuote).test(adopt),
            "...and shown in the linked-quotation field");
    });

    check(`the ${e.name} create-from-LPO path reuses it`, () => {
        const create = body(e.src,
            e.name === "invoice" ? "async function createInvoiceFromLpo"
                                 : "async function createDnFromLpo",
            "\n}\n");
        assert.ok(new RegExp(e.adopt + "\\(").test(create),
            "the LPO screen's button still has its own copy of the seeding; "
            + "two copies is how they drift apart");
    });

    check(`the ${e.name} asks before replacing items already typed`, () => {
        const adopt = body(e.src, `async function ${e.adopt}(`, "\n}\n");
        assert.ok(new RegExp(e.filled + "\\(\\)").test(adopt),
            "it must look at what is already in the editor");
        assert.ok(new RegExp(e.state + "\\.dirty").test(adopt),
            "...and whether that was typed rather than loaded");
        assert.ok(/showModal\(/.test(adopt),
            "replacing a half-typed document without asking destroys work - "
            + "`importQuoteIntoDN` has asked since it shipped");
        assert.ok(/return\b/.test(adopt), "and it has to be able to say no");
    });

    check(`the ${e.name} does not seed when "No LPO" is picked`, () => {
        const handler = body(e.src, `const ${e.pickBtn} = document.getElementById`,
                             "\n    document.getElementById");
        assert.ok(/isNoLpo\(|NO_LPO/.test(handler),
            "picking 'No LPO' must not go looking for a purchase order's "
            + "lines - `LpoPicker.isNoLpo` is the existing answer");
    });
}

// ------------------------------------------------ an LPO that is not on file
check("a refused seed is reported, not turned into an empty document", () => {
    /* Found driving the real page: `lpoSeedForDocument` falls back to
       `lpo_fulfillment` when the seed does not come back ok, which is right for
       a backend that lacks the method and wrong for one that answered "that PO
       is not on file" - the document then said "no ordered lines are recorded
       on it", which is a different and wrong thing to tell someone whose LPO
       number is a typo. */
    const fn = body(INV, "async function lpoSeedForDocument(", FN_END);
    assert.ok(/seed\.error/.test(fn),
        "an explicit refusal from the bridge must be handed back, not "
        + "replaced by an empty fulfilment read");
    const guard = fn.indexOf("seed.error");
    const fallback = fn.indexOf("lpo_fulfillment");
    assert.ok(guard > 0 && fallback > guard,
        "the refusal check has to come BEFORE the fallback, or the fallback "
        + "still swallows it");
    for (const e of EDITORS) {
        const adopt = body(e.src, `async function ${e.adopt}(`, FN_END);
        assert.ok(/!seed\.ok/.test(adopt) && /return false/.test(adopt),
            `${e.adopt} must stop and say so when the seed was refused`);
    }
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
