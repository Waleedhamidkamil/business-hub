/* The LPO list screen: one row must answer "where is this up to" without being
   opened, and that question means something different for each kind of PO.

   A materials PO is money against money. A rental is MONTHS - the months are
   what was bought. A supply contract has no total to be a fraction of at all:
   CSSP-OPEN-0001 prints 0.77, the price of a litre of diesel.

   `lpoProgress`, `lpoBar` and `lpoOrder` are pulled out of lpo.js and run in
   isolation, so the sentences and the order are tested properly rather than by
   grepping for strings. (The three kind groups gave way to type tabs on
   2026-10-03 - tests/lpo_screen.test.js drives those.) */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = fs.readFileSync(
    path.join(__dirname, "..", "static", "lpo.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

/* Lift one top-level function out of the file by brace matching. */
function lift(names) {
    let code = "";
    for (const name of names) {
        const start = SOURCE.indexOf(`function ${name}(`);
        assert.ok(start > 0, `${name} not found - extract it so it can be tested`);
        let depth = 0, end = start;
        for (let i = SOURCE.indexOf("{", start); i < SOURCE.length; i++) {
            if (SOURCE[i] === "{") depth++;
            else if (SOURCE[i] === "}") { depth--; if (!depth) { end = i + 1; break; } }
        }
        code += SOURCE.slice(start, end) + "\n";
    }
    const ctx = { console, out: {} };
    vm.runInNewContext(code + "\nout = {" + names.join(", ") + "};", ctx);
    return ctx.out;
}

const { lpoProgress, lpoBar, lpoOrder } =
    lift(["lpoProgress", "lpoBar", "lpoDate", "lpoIsDone", "lpoOrder"]);

const MATERIALS = { kind: "materials", currency: "USD", lpo_no: "CSSP-ADM-0010",
                    invoiced_usd: 8100, total_usd: 18390, total: 18390,
                    invoices: ["INV-MT-HDEC-043"], dns: ["DN-HDEC-046"] };
const RENTAL = { kind: "rental", currency: "USD", lpo_no: "CSSP-RENT-0002",
                 invoiced_usd: 1700, total_usd: 11900, total: 11900,
                 months_total: 7, months_invoiced: 2, invoices: [] };
const OPEN = { kind: "open", currency: "USD", lpo_no: "CSSP-OPEN-0001",
               invoiced_usd: 1240, total_usd: 0.77, total: 0.77,
               invoices: ["a", "b", "c", "d"] };

/* ---------------------------------------------------------- the sentence */

check("a materials PO reports value invoiced against value ordered", () => {
    const s = lpoProgress(MATERIALS);
    assert.ok(/8,100/.test(s) && /18,390/.test(s), s);
});

check("a supply contract never reports a percentage of its printed total", () => {
    const s = lpoProgress(OPEN);
    assert.ok(!/0\.77/.test(s),
        "0.77 is the price of a litre of diesel, not a contract value: " + s);
    assert.ok(/1,240/.test(s), "what HAS been invoiced is the useful number: " + s);
});

check("a supply contract says how many invoices it has run to", () => {
    assert.ok(/4 invoices/.test(lpoProgress(OPEN)), lpoProgress(OPEN));
    assert.ok(/1 invoice\b/.test(lpoProgress({ ...OPEN, invoices: ["a"] })),
        "one invoice must not read as '1 invoices'");
});

check("a rental reports months, never money", () => {
    /* Waleed, 2026-10-06: a contract is counted in months, not amounts. */
    const s = lpoProgress(RENTAL);
    assert.ok(/month/i.test(s), "a rental's progress is months: " + s);
    assert.ok(/2/.test(s) && /7/.test(s), s);
    assert.ok(!/\$/.test(s), "money crept back into a rental's progress: " + s);
});

check("missed months are said in the sentence", () => {
    const s = lpoProgress({ ...RENTAL, months_unbilled: 2 });
    assert.ok(/2 months missed/.test(s), s);
});

check("a rental with no period recorded still says something useful", () => {
    const s = lpoProgress({ ...RENTAL, months_total: 0, months_invoiced: 0 });
    assert.ok(s.length > 0, "a blank cell tells the reader nothing");
    assert.ok(!/of 0/.test(s), "'month 0 of 0' is worse than saying nothing: " + s);
});

check("canonical invoice totals stay labeled USD for an IQD LPO", () => {
    const s = lpoProgress({ ...MATERIALS, currency: "IQD" });
    // These fields are *_usd; native value still appears in its own column.
    assert.ok(!/IQD/.test(s), s);
    assert.ok(/\$/.test(s), s);
});

check("nothing invoiced yet reads as nothing, not as blank", () => {
    const s = lpoProgress({ ...MATERIALS, invoiced_usd: 0, invoices: [] });
    assert.ok(/0\.00/.test(s), s);
});

/* --------------------------------------------------------------- the bar */

check("a materials PO's bar is value invoiced over value ordered", () => {
    const bar = lpoBar(MATERIALS);
    assert.ok(bar, "a materials PO with a real total has something to show");
    assert.ok(Math.abs(bar.pct - (8100 / 18390) * 100) < 0.5, JSON.stringify(bar));
});

check("a rental's bar is months, matching its sentence", () => {
    const bar = lpoBar(RENTAL);
    assert.ok(Math.abs(bar.pct - (2 / 7) * 100) < 0.5, JSON.stringify(bar));
});

check("a supply contract has no bar at all", () => {
    assert.strictEqual(lpoBar(OPEN), null,
        "there is no denominator, so a bar would be inventing one");
});

check("an LPO with no total has no bar", () => {
    assert.strictEqual(lpoBar({ ...MATERIALS, total_usd: 0 }), null,
        "an unrecorded total is not a ceiling of zero");
});

check("a bar never exceeds full", () => {
    const bar = lpoBar({ ...MATERIALS, invoiced_usd: 99999 });
    assert.ok(bar.pct <= 100, "over-invoiced must not draw past the end: " + bar.pct);
});

check("the bar always carries the words as well", () => {
    /* Waleed is colour blind: a bar on its own is not an answer. */
    assert.ok(lpoBar(MATERIALS).label, "the bar has no readable label");
});

/* ------------------------------------------------------------- the order */

check("newest first by the LPO's own date, not by when it was filed", () => {
    /* Eight LPOs were filed in one sitting on 11 September; the order that
       means something is the date the client wrote on them. */
    const rows = [{ ...MATERIALS, lpo_no: "OLD", lpo_date: "9/7/2026", received_at: "2026-09-11T21:10" },
                  { ...MATERIALS, lpo_no: "NEW", lpo_date: "26/9/2026", received_at: "2026-09-01T10:00" }];
    assert.deepEqual(lpoOrder(rows).map(r => r.lpo_no), ["NEW", "OLD"]);
});

check("a D/M/Y date is never read as M/D/Y", () => {
    /* 4/6/2026 is the 4th of June. `new Date("4/6/2026")` says April. */
    const rows = [{ lpo_no: "JUNE", lpo_date: "4/6/2026" }, { lpo_no: "MAY", lpo_date: "15/5/2026" }];
    assert.deepEqual(lpoOrder(rows).map(r => r.lpo_no), ["JUNE", "MAY"]);
});

check("finished LPOs go to the bottom", () => {
    const done = { ...MATERIALS, lpo_no: "DONE", lpo_date: "1/10/2026", status: "closed",
                   next_step: { key: "done", action: false } };
    const open = { ...RENTAL, lpo_no: "OPEN", lpo_date: "1/1/2026", status: "invoiced",
                   next_step: { key: "ok", action: false } };
    assert.deepEqual(lpoOrder([done, open]).map(r => r.lpo_no), ["OPEN", "DONE"]);
});

check("closed by value but still owed a delivery is not finished", () => {
    const owes = { ...MATERIALS, lpo_no: "OWES", lpo_date: "1/1/2026", status: "closed",
                   next_step: { key: "deliver", action: true } };
    const open = { ...MATERIALS, lpo_no: "OPEN", lpo_date: "1/2/2026", status: "invoiced",
                   next_step: { key: "ok", action: false } };
    assert.deepEqual(lpoOrder([open, owes]).map(r => r.lpo_no), ["OPEN", "OWES"],
        "both are open, so the newer one leads");
});

/* ----------------------------------------------------------- the rendering */

check("every status is carried by a word, never by colour alone", () => {
    for (const w of ["Received", "Acknowledged", "Delivered", "Invoiced", "Closed"]) {
        assert.ok(new RegExp(w).test(SOURCE), `the ${w} label went missing`);
    }
});

check("the over-value warning follows the backend flag", () => {
    assert.ok(/over_value/.test(SOURCE),
        "recomputing the ceiling in JS is how the open-contract bug comes back");
    assert.ok(!/Number\(r\.invoiced_usd\)[^\n]*>[^\n]*Number\(r\.total_usd\)/.test(SOURCE),
        "the old local comparison is still here");
});

check("a contract can be closed and reopened from the row", () => {
    assert.ok(/close_lpo/.test(SOURCE),
        "no way to record an early termination, which their PO explicitly allows");
});

check("the list can be narrowed to what needs doing", () => {
    /* One filter earns a permanent place: "Need action", keyed on the backend's
       next step (database.lpo_next_step), never re-derived from a status label. */
    assert.ok(/lpoDueFilter/.test(SOURCE), "no need-action filter");
    assert.ok(/lpoState\.due/.test(SOURCE), "the toggle is not wired to the list");
    const i = SOURCE.indexOf("function visibleLpos");
    assert.ok(/r\.next_step && r\.next_step\.action/.test(SOURCE.slice(i, i + 900)),
        "the filter must key on the next step");
});

check("a row carries one action at most - its next step", () => {
    /* Six buttons per row, on every row, for maybe one use each - that is what
       made six LPOs fill a 1080p screen. The ROW opens the LPO; the only button
       in it is the next step, and only when there is one. */
    const i = SOURCE.indexOf("function lpoRowHtml");
    assert.ok(i > 0, "lpoRowHtml is gone");
    const body = SOURCE.slice(i, SOURCE.indexOf("\n}", i));
    assert.ok(!/data-act=|<button/.test(body), "a button is built into the row itself: " + body);
    const n = SOURCE.indexOf("function lpoNextHtml");
    const next = SOURCE.slice(n, SOURCE.indexOf("\n}", n));
    assert.strictEqual((next.match(/<button/g) || []).length, 1, "the next step is ONE button");
    assert.ok(/if \(s\.action\)/.test(next), "a button is offered even when there is nothing to do");
});

check("the summary figures report what needs attention", () => {
    const i = SOURCE.indexOf("function lpoKpis");
    assert.ok(i > 0, "the summary figures are gone");
    const body = SOURCE.slice(i, SOURCE.indexOf("\n}", i));
    assert.ok(/next_step && r\.next_step\.action/.test(body), "it does not count what needs action");
    assert.ok(/!== "open"/.test(body),
        "open contracts must be left out of the money figures - their stored "
        + "total is a unit price, so summing them means nothing");
    assert.ok(/!r\.closed_at/.test(body),
        "an LPO closed by hand still counts as money not yet invoiced");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
