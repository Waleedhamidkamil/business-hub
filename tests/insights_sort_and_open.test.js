/* Insights tables sort on their headers and their document rows open the
   document (2026-10-06).

   Waleed: "make the titles of the tables in insights clickable so I can sort
   for unpaid invoices", and "make these documents lines clickable to send me to
   that document". The sort helpers are lifted out of static/analytics.js and
   run for real; the wiring is pinned at the source. */
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const SRC = fs.readFileSync("static/analytics.js", "utf8").replace(/\r\n/g, "\n");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

/* Lift top-level functions of the IIFE by brace matching. */
function lift(names) {
    let code = "";
    for (const name of names) {
        const at = SRC.indexOf(`function ${name}(`);
        assert.ok(at > 0, name + " not found");
        let depth = 0, end = at;
        for (let i = SRC.indexOf("{", at); i < SRC.length; i++) {
            if (SRC[i] === "{") depth++;
            else if (SRC[i] === "}") { depth--; if (!depth) { end = i + 1; break; } }
        }
        code += SRC.slice(at, end) + "\n";
    }
    const ctx = { out: {}, tableSort: {} };
    vm.runInNewContext(code + "\nout = {" + names.join(", ") + "};", ctx);
    return { ...ctx.out, tableSort: ctx.tableSort };
}

const { esc, dateVal, sortRows, sortHead, wireSortHead, tableSort } =
    lift(["esc", "dateVal", "sortRows", "sortHead", "wireSortHead"]);
const plain = (x) => JSON.parse(JSON.stringify(x));

// The invoice table's Status column, exactly as analytics.js defines it.
const statusSrc = SRC.match(/key: "status", label: "Status", left: true, first: 1,\s*value: (\(x\) => \(x\.paid \? 2 : 0\) \+ \(x\.losing \? 0 : 1\))/);
const STATUS = statusSrc && vm.runInNewContext(statusSrc[1]);

const INVOICES = [
    { no: "INV-1", date: "4/6/2026", charged: 900, paid: true, losing: false },
    { no: "INV-2", date: "15/5/2026", charged: 1700, paid: false, losing: false },
    { no: "INV-3", date: "31/8/2026", charged: null, paid: false, losing: true },
    { no: "INV-4", date: "9/7/2026", charged: 1700, paid: true, losing: false },
];
const COLS = [
    { key: "no", label: "Invoice", first: 1 },
    { key: "date", label: "Date", value: (x) => dateVal(x.date) },
    { key: "charged", label: "Charged" },
    { key: "status", label: "Status", first: 1, value: STATUS },
];
const order = (id) => plain(sortRows(id, INVOICES, COLS).map((r) => r.no));

check("the invoice Status column is found in analytics.js", () => {
    assert.ok(typeof STATUS === "function", "the Status sort rule moved or changed shape");
});

check("until a header is clicked, the backend's order is kept", () => {
    delete tableSort.t;
    assert.deepEqual(order("t"), ["INV-1", "INV-2", "INV-3", "INV-4"]);
});

check("sorting by Status puts the unpaid invoices first, the losing one ahead", () => {
    tableSort.t = { key: "status", dir: 1 };
    assert.deepEqual(order("t"), ["INV-3", "INV-2", "INV-1", "INV-4"]);
});

check("dates are D/M/Y - 4/6/2026 is June, after 15 May", () => {
    tableSort.t = { key: "date", dir: -1 };
    assert.deepEqual(order("t"), ["INV-3", "INV-4", "INV-1", "INV-2"]);
});

check("a blank value sorts last in both directions", () => {
    tableSort.t = { key: "charged", dir: -1 };
    assert.equal(order("t").at(-1), "INV-3");
    tableSort.t = { key: "charged", dir: 1 };
    assert.equal(order("t").at(-1), "INV-3");
});

check("equal values keep their original order", () => {
    tableSort.t = { key: "charged", dir: -1 };
    assert.deepEqual(order("t").slice(0, 2), ["INV-2", "INV-4"], "1700 and 1700 swapped");
});

check("document numbers sort naturally (INV-9 before INV-10)", () => {
    tableSort.n = { key: "no", dir: 1 };
    const rows = [{ no: "INV-10" }, { no: "INV-9" }];
    assert.deepEqual(plain(sortRows("n", rows, COLS).map((r) => r.no)), ["INV-9", "INV-10"]);
});

check("clicking a header sorts; clicking it again reverses", () => {
    delete tableSort.w;
    const th = { dataset: { sort: "status" }, onclick: null };
    const host = { querySelectorAll: () => [th] };
    let redraws = 0;
    wireSortHead(host, "w", COLS, () => { redraws++; });
    th.onclick();
    assert.deepEqual(plain(tableSort.w), { key: "status", dir: 1 }, "Status starts unpaid-first");
    th.onclick();
    assert.deepEqual(plain(tableSort.w), { key: "status", dir: -1 });
    assert.equal(redraws, 2);
    th.dataset.sort = "charged";
    th.onclick();
    assert.deepEqual(plain(tableSort.w), { key: "charged", dir: -1 }, "money starts biggest first");
});

check("the active header says which way it sorts, in the arrow and aria-sort", () => {
    tableSort.h = { key: "charged", dir: -1 };
    const head = sortHead("h", COLS);
    assert.ok(/data-sort="charged"[^>]*aria-sort="descending"[\s\S]*▼/.test(head), head);
    assert.ok(/data-sort="no"[^>]*aria-sort="none"/.test(head));
});

check("every client table is sortable and its document rows open the document", () => {
    for (const id of ['"items"', '"invoices"', '"docs"']) {
        assert.ok(SRC.includes(`sortHead(${id}`) && SRC.includes(`wireSortHead(host, ${id}`), id + " table does not sort");
    }
    assert.ok(/data-doc-kind="\$\{esc\(kind\)\}"/.test(SRC), "rows do not carry their document kind");
    const open = SRC.slice(SRC.indexOf("const DOC_LOADER"), SRC.indexOf("function wireDocRows"));
    for (const [k, fn] of [["inv", "loadInvForEdit"], ["quote", "loadQuoteForEdit"], ["dn", "loadDnForEdit"]]) {
        assert.ok(new RegExp(`${k}: "${fn}"`).test(open), `${k} rows do not open ${fn}`);
    }
    assert.ok((SRC.match(/ wireDocRows\(host\);/g) || []).length === 2, "invoice and document tables must both open rows");
    assert.ok(/e\.key === "Enter"/.test(SRC), "a focused row cannot be opened from the keyboard");
});

check("clicking a row really opens the preview - Mark paid for an unpaid invoice only", () => {
    const at = SRC.indexOf("function openDoc(");
    let depth = 0, end = at;
    for (let i = SRC.indexOf("{", at); i < SRC.length; i++) {
        if (SRC[i] === "{") depth++;
        else if (SRC[i] === "}") { depth--; if (!depth) { end = i + 1; break; } }
    }
    const loader = SRC.match(/const DOC_LOADER = \{[^}]*\};/)[0];
    const popups = [], editors = [];
    const ctx = {
        refreshInPlace() {},
        window: { openTodoDocModal: (...a) => popups.push(a), loadInvForEdit: (n) => editors.push(n) },
    };
    vm.runInNewContext(loader + "\n" + SRC.slice(at, end) + "\nthis.openDoc = openDoc;", ctx);
    ctx.openDoc("inv", "INV-1", { paid: false, date: "31/8/2026" });
    ctx.openDoc("inv", "INV-2", { paid: true, date: "30/6/2026" });
    ctx.openDoc("dn", "DN-1", { paid: null, date: "" });
    assert.deepEqual(popups.map((p) => [p[0], p[1], p[2], p[3]]), [
        ["insights-unpaid", "inv", "INV-1", "31/8/2026"],
        ["insights", "inv", "INV-2", "30/6/2026"],
        ["insights", "dn", "DN-1", ""]]);
    assert.equal(editors.length, 0, "a row went straight to the editor instead of the preview");
    assert.equal(typeof popups[0][5].onAfterAction, "function", "Mark paid does not refresh the page");
    delete ctx.window.openTodoDocModal;
    ctx.openDoc("inv", "INV-3", { paid: false });
    assert.deepEqual(editors, ["INV-3"], "without the preview module, the row must still open the invoice");
});

check("a row opens the shared preview popup, so closing it lands back on the table", () => {
    const at = SRC.indexOf("function openDoc(");
    const body = SRC.slice(at, SRC.indexOf("\n    }\n", at));
    assert.ok(/window\.openTodoDocModal\(cat, kind, no/.test(body), "rows no longer open the preview popup");
    assert.ok(/row\.paid === false \? "insights-unpaid" : "insights"/.test(body),
        "an unpaid invoice must get the Mark paid preview, everything else the plain one");
    assert.ok(/onAfterAction: refreshInPlace/.test(body), "after Mark paid the page is not refreshed");
    assert.ok(body.indexOf("openTodoDocModal") < body.indexOf("DOC_LOADER"),
        "the popup comes first; the editor is only the fallback");
    assert.ok(/data-doc-paid="\$\{paid\}"/.test(SRC), "rows do not carry whether the invoice is paid");
    const refresh = SRC.slice(SRC.indexOf("async function refreshInPlace"), SRC.indexOf("async function refreshInPlace") + 300);
    assert.ok(/scrollTop = top/.test(refresh), "a refresh must keep the reader's place");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
