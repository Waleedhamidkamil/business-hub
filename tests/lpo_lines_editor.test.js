/* The ordered-lines editor: inherit a quotation's lines, tick what they ordered.

   CSSP-ADM-0010 cites QUT-MT-HDEC-029 on its face and took four of our five
   lines verbatim - same wording, same dimensions, same quantity, same rate -
   dropping the fifth. Their total 18,390 is our 19,286 minus exactly 896, the
   line they dropped. So filing it must be "untick one line", not "retype four".

   The numbers below are the real ones from those two documents. If the
   arithmetic here ever stops matching them, something has drifted. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "lpo_lines.js");

function load() {
    const ctx = { window: {}, console };
    ctx.window.document = { getElementById: () => null };
    vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx);
    return ctx.window.LpoLines;
}

/* QUT-MT-HDEC-029, exactly as it is stored in the live Abraj database. */
const QUOTE_ROWS = [
    { item: "Office desk", description: "1650*850*800", unit: "EA", qty: 60, rate: "135" },
    { item: "3-drawer mobile pedestal under desk", description: "400*520*600", unit: "EA", qty: 60, rate: "64" },
    { item: "Office chair", description: "670*580*1050", unit: "EA", qty: 78, rate: "75" },
    { item: "Meeting Table-1", description: "2100*900*720", unit: "EA", qty: 3, rate: "200" },
    { item: "3-drawer enclosed type", description: "800*410*1200", unit: "EA", qty: 7, rate: "128" },
];
const QUOTE_TOTAL = 19286;      // what we quoted
const PO_TOTAL = 18390;         // what CSSP-ADM-0010 actually ordered
const DROPPED = 896;            // 7 @ 128, the line they did not take

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

const L = load();

check("every quoted line arrives ticked at its quoted quantity", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    assert.strictEqual(lines.length, 5);
    assert.ok(lines.every(l => l.take === true), "lines must start ticked");
    assert.deepEqual(lines.map(l => l.qty), [60, 60, 78, 3, 7]);
});

check("the quoted text is carried across untouched", () => {
    const line = L.fromQuote(QUOTE_ROWS)[0];
    assert.strictEqual(line.item, "Office desk");
    assert.strictEqual(line.description, "1650*850*800",
        "the dimensions ARE the specification - their PO reprints them");
    assert.strictEqual(line.unit, "EA");
    assert.strictEqual(line.rate, "135");
});

check("the total of the ticked lines is what we quoted", () => {
    assert.strictEqual(L.total(L.fromQuote(QUOTE_ROWS)), QUOTE_TOTAL);
});

check("unticking the line they dropped gives their printed total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;                       // 3-drawer enclosed type, 7 @ 128
    assert.strictEqual(L.total(lines), PO_TOTAL,
        "CSSP-ADM-0010 prints 18,390 and this is how you reach it");
    assert.strictEqual(QUOTE_TOTAL - PO_TOTAL, DROPPED);
});

check("changing a quantity changes the total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[0].qty = 30;                           // half the desks
    assert.strictEqual(L.total(lines), QUOTE_TOTAL - 30 * 135);
});

check("an unticked line contributes nothing even with a quantity on it", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[0].take = false;
    assert.strictEqual(L.total(lines), QUOTE_TOTAL - 60 * 135);
});

check("a mismatch is described in words, with both totals and the difference", () => {
    const note = L.mismatch(L.fromQuote(QUOTE_ROWS), PO_TOTAL);
    assert.ok(note, "19,286 of lines against a printed 18,390 is a mismatch");
    assert.ok(/19,286/.test(note), "the note must give the lines total: " + note);
    assert.ok(/18,390/.test(note), "and the printed total: " + note);
    assert.ok(/896/.test(note), "and the difference: " + note);
});

check("the mismatch note never forbids the save", () => {
    const note = L.mismatch(L.fromQuote(QUOTE_ROWS), PO_TOTAL);
    assert.ok(/save/i.test(note),
        "Waleed chose 'say so, let me save anyway' - the wording must offer that");
});

check("no note when the lines agree with the printed total", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;
    assert.strictEqual(L.mismatch(lines, PO_TOTAL), "");
});

check("no note when no total was typed - nothing to disagree with", () => {
    assert.strictEqual(L.mismatch(L.fromQuote(QUOTE_ROWS), 0), "");
    assert.strictEqual(L.mismatch(L.fromQuote(QUOTE_ROWS), ""), "");
});

check("a mismatch the other way round is still reported", () => {
    /* They ordered MORE than we quoted, which happens. */
    const note = L.mismatch(L.fromQuote(QUOTE_ROWS), 20000);
    assert.ok(note, "a PO above our quotation is just as worth noticing");
    assert.ok(/714/.test(note), "the difference must be named: " + note);
    assert.ok(!/-714/.test(note),
        'the sentence already says which way round it is - "a difference of '
        + '-714.00" reads like a mistake: ' + note);
});

check("rounding to the cent does not raise a false mismatch", () => {
    /* A sub-cent gap, which is what the 0.01 tolerance is FOR. The obvious
       case (3 x 0.1 against 0.3) cannot test it: `total` already rounds to the
       cent, so the difference comes out exactly zero and an equality check
       would pass just as well. */
    assert.strictEqual(L.mismatch([{ take: true, qty: 1, rate: "100" }], 100.004), "",
        "four tenths of a cent is not a discrepancy worth a sentence");
    assert.ok(L.mismatch([{ take: true, qty: 1, rate: "100" }], 100.02),
        "two cents is still a real difference and must be said");
});

check("a comma-formatted rate is still a number", () => {
    assert.strictEqual(L.total([{ take: true, qty: 2, rate: "1,250.50" }]), 2501);
});

check("the tick never reaches the record", () => {
    /* Storing `take` would put a UI flag in the document and make a saved
       order compare unequal to the same order typed fresh. */
    const rows = L.toRows(L.fromQuote(QUOTE_ROWS));
    assert.ok(rows.every(r => !("take" in r)));
    assert.deepEqual(Object.keys(rows[0]).sort(),
        ["description", "item", "qty", "rate", "unit"]);
});

check("only ticked lines are saved", () => {
    const lines = L.fromQuote(QUOTE_ROWS);
    lines[4].take = false;
    const rows = L.toRows(lines);
    assert.strictEqual(rows.length, 4);
    assert.ok(rows.every(r => !("take" in r)), "the tick is UI state, not data");
    assert.deepEqual(Object.keys(rows[0]).sort(),
        ["description", "item", "qty", "rate", "unit"]);
});

check("a blank manual line is dropped rather than saved empty", () => {
    const rows = L.toRows([{ take: true, item: "", description: "", qty: 0, rate: "" }]);
    assert.strictEqual(rows.length, 0);
});

check("a manual line with only a quantity is kept", () => {
    /* CSSP-OPEN-0001's diesel line may be typed qty-first. Dropping it because
       the name came later would lose the user's work. */
    const rows = L.toRows([{ take: true, item: "", description: "", qty: 2000, rate: "0.77" }]);
    assert.strictEqual(rows.length, 1);
});

check("a blank line offers itself ready to type into", () => {
    const b = L.blank();
    assert.strictEqual(b.take, true, "a line you just added is obviously ordered");
    assert.strictEqual(b.qty, 0);
});

check("a stored order reads back as all-ordered", () => {
    const rows = [{ item: "Rental of Generators", description: "100 KVA Silent",
                    unit: "Set-Month", qty: 14, rate: "850" }];
    const lines = L.fromRows(rows);
    assert.ok(lines.every(l => l.take), "everything on file was ordered");
    assert.strictEqual(L.total(lines), 11900, "CSSP-RENT-0002 prints 11,900");
});

check("reading back and saving again changes nothing", () => {
    const rows = L.toRows(L.fromQuote(QUOTE_ROWS));
    assert.deepEqual(L.toRows(L.fromRows(rows)), rows,
        "a round trip through the editor must not quietly rewrite the order");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
