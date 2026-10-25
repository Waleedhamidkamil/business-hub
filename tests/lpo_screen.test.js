/* The Client LPOs screen, rebuilt 2026-10-03 - what Waleed sees and clicks.

   He asked for something "simple, great looking, easy to use, professional and
   reliable", with tabs like the Materials / Equipment / Manpower / Service ones
   on the Quotation and Invoice lists, and without the Previous / Next LPO
   buttons. This drives the REAL static/lpo.js in a vm against a small DOM
   double and checks the behaviour, not the wording:

     - each type tab shows its own columns, and the counts are right;
     - "Need action" and the search narrow the list;
     - finished LPOs sink to the bottom; the newest open one leads;
     - a row carries at most ONE button - its next step - and the step runs;
     - the LPO screen's header offers the right create actions per kind and
       state, with everything rare in More;
     - the right panel shows items for an order and ONE billing list for a
       contract, and the signed-copy line says the truth with the right button;
     - Attach waits for the upload before the screen reloads;
     - "Copy file" next to a signed copy always copies the signed copy.

   The backend half of "next step" is pinned in tests/lpo_next_step.test.py.
*/
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static/lpo.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "templates/index.html"), "utf8");

/* ---------------------------------------------------------------- the world */
function el(id) {
    return {
        id, innerHTML: "", textContent: "", hidden: false, value: "", dataset: {},
        classList: { add() {}, remove() {}, toggle() {} },
        setAttribute() {}, addEventListener() {}, querySelector: () => null,
        querySelectorAll: () => [], style: {},
        appendChild(c) { (this.children = this.children || []).push(c); return c; },
        getContext: () => ({}),
    };
}

function world() {
    const els = {};
    const byId = (id) => (els[id] = els[id] || el(id));
    const calls = { chips: {}, created: [], copied: [], confirm: [], attached: [], ledger: 0, reopened: 0 };
    const ctx = {
        console, setTimeout, clearTimeout, Promise, JSON, Math, Number, String, Date, Set, Map, Array, Object,
        document: {
            readyState: "loading", addEventListener() {},
            getElementById: byId, querySelector: () => null, querySelectorAll: () => [],
            createElement: () => {
                const input = el("input");
                input.click = () => { calls.picker = input; };
                return input;
            },
        },
        esc: (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
        toast: (m) => { calls.toast = m; },
        safe: (fn) => fn,
        hideAllViews() {}, setBrand() {},
        renderChipRow: (box, options, current) => { calls.chips[box.id] = { options, current }; box.innerHTML = options.length ? "chips" : ""; },
    };
    ctx.window = ctx;
    ctx.createDnFromLpo = async (c, n) => calls.created.push({ kind: "dn", n });
    ctx.createInvoiceFromLpo = async (c, n, p) => calls.created.push({ kind: "inv", n, p });
    ctx.copyFileToClipboard = async (p) => calls.copied.push(p);
    ctx.Popup = { confirm: async (t) => { calls.confirm.push(t); return false; } };
    ctx.api = () => ctx.bridge;
    ctx.bridge = {
        lpo_billing_detail: async () => { calls.ledger++; return ctx.ledger; },
        attach_lpo_file_data: async () => ({ ok: true }),
        list_lpos: async () => ctx.window.lpoState.rows,
        lpo_reference_gaps: async () => [],
    };
    vm.createContext(ctx);
    vm.runInContext(SRC, ctx, { filename: "static/lpo.js" });
    return { ctx, byId, calls, S: ctx.window.lpoState };
}

const step = (key, text, extra = {}) =>
    ({ key, text, action: ["invoice", "deliver", "invoice_period", "attach"].includes(key), ...extra });

// The eight Abraj LPOs as they stood on 2026-10-03, reduced to what the screen reads.
const ROWS = [
    { client_name: "HDEC", lpo_no: "LPO CSSP-ADM-0033", kind: "materials", lpo_date: "26/9/2026", status: "delivered",
      total: 490, currency: "USD", total_usd: 490, invoiced_usd: 0, remaining_usd: 490, file_exists: true, ack_exists: true,
      ack_path: "D:/LPOs/LPO CSSP-ADM-0033.acknowledged.pdf", file_path: "D:/LPOs/LPO CSSP-ADM-0033.pdf",
      dns: ["DN-HDEC-067"], invoices: [], rows: [{ item: "Currency Counting Machine" }, { item: "High Security Steel Cash Locker" }],
      items: { lines: 2, delivered: 2, invoiced: 0, part_delivered: 0 }, next_step: step("invoice", "Create invoice") },
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0017", kind: "materials", lpo_date: "24/7/2026", status: "received",
      total: 273.4, total_usd: 273.4, invoiced_usd: 0, remaining_usd: 273.4, file_exists: true, dns: [], invoices: [],
      rows: [{ item: "Office chair" }], items: { lines: 12, delivered: 0, invoiced: 0, part_delivered: 0 },
      next_step: step("deliver", "Create delivery note") },
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0010", kind: "materials", lpo_date: "9/7/2026", status: "closed",
      total: 18390, total_usd: 18390, invoiced_usd: 18390, remaining_usd: 0, file_exists: true,
      dns: ["DN-HDEC-046"], invoices: ["INV-MT-HDEC-043"], items: { lines: 4, delivered: 4, invoiced: 4, part_delivered: 0 },
      next_step: step("done", "Fully invoiced") },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0002", kind: "rental", lpo_date: "4/6/2026", status: "invoiced",
      total: 11900, total_usd: 11900, invoiced_usd: 6800, remaining_usd: 5100, period_start: "8/6/2026", period_end: "8/1/2027",
      months_invoiced: 4, months_total: 7, months_unbilled: 0, file_exists: true, ack_exists: false,
      dns: [], invoices: ["INV-EQ-HDEC-069", "INV-EQ-HDEC-070"], next_step: step("ok", "Up to date") },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0004", kind: "rental", lpo_date: "24/7/2026", status: "invoiced",
      total: 15600, total_usd: 15600, invoiced_usd: 15600, remaining_usd: 0, period_start: "24/7/2026", period_end: "",
      months_invoiced: 2, months_total: 0, months_unbilled: 1, file_exists: true, dns: [], invoices: ["INV-EQ-HDEC-093"],
      next_step: step("ok", "Up to date") },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0001", kind: "rental", lpo_date: "18/5/2026", status: "closed",
      total: 27139.4, total_usd: 27139.4, invoiced_usd: 27139.4, remaining_usd: 0, period_start: "18/4/2026",
      period_end: "12/8/2026", months_invoiced: 4, months_total: 4, file_exists: true, dns: [], invoices: [],
      next_step: step("done", "Finished") },
    { client_name: "HDEC", lpo_no: "CSSP-OPEN-0001", kind: "open", lpo_date: "15/5/2026", status: "received",
      total: 0, total_usd: 0.77, invoiced_usd: 0, remaining_usd: 0, months_unbilled: 6, oldest_due_date: "2026-05-15",
      file_exists: true, dns: [], invoices: [], rows: [{ item: "Supply of Diesel", unit: "L", rate: "0.77", qty: 2000 }],
      next_step: step("invoice_period", "Invoice May", { period_start: "2026-05-15" }) },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0005(3)", kind: "rental", lpo_date: "26/8/2026", status: "invoiced",
      total: 5100, total_usd: 5100, invoiced_usd: 5100, remaining_usd: 0, period_start: "12/8/2026", period_end: "11/11/2026",
      months_invoiced: 3, months_total: 3, file_exists: true, dns: [], invoices: [], next_step: step("ending", "Ends 11 Nov") },
];

const clone = (x) => JSON.parse(JSON.stringify(x));
// Arrays made inside the vm have another realm's prototype; compare plain copies.
const plain = (x) => JSON.parse(JSON.stringify(x));
const listed = (w) => [...w.byId("lpoList").innerHTML.matchAll(/<tr class="lpo-row[^"]*"[^>]*data-no="([^"]+)"/g)].map(m => m[1]);
const rowHtml = (w, no) => {
    const h = w.byId("lpoList").innerHTML;
    const at = h.indexOf(`data-no="${no}"`);
    return h.slice(at, h.indexOf("</tr>", at));
};

let failures = 0;
async function check(name, fn) {
    try { await fn(); console.log("  ok   " + name); }
    catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}

(async () => {
    /* ----------------------------------------------------------- the list */
    await check("each type tab shows the columns that matter for that kind", () => {
        const { ctx } = world();
        const labels = (k) => plain(ctx.lpoColumns(k).map(c => c.label));
        assert.deepEqual(labels("all"), ["LPO", "Type", "Progress", "Value", "Next step"]);
        assert.deepEqual(labels("materials"), ["LPO", "Items", "Delivered", "Invoiced", "Value", "Next step"]);
        assert.deepEqual(labels("rental"), ["LPO", "Period", "Months invoiced", "Billed", "Next step"]);
        assert.deepEqual(labels("open"), ["LPO", "Supplying", "Unit price", "Invoiced", "Not invoiced", "Next step"]);
    });

    await check("the type tabs are the shared chip control, with true counts", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.ctx.renderLpoList();
        const opts = w.calls.chips.lpoKindChips.options;
        assert.deepEqual(plain(opts.map(o => [o.key, o.count])),
            [["all", 8], ["materials", 3], ["rental", 4], ["open", 1]]);
        assert.equal(w.byId("lpoClientChips").innerHTML, "", "one client needs no client chips");
        w.S.rows.push({ ...clone(ROWS[0]), client_name: "TDC", lpo_no: "TDC-1" });
        w.ctx.renderLpoList();
        assert.deepEqual(plain(w.calls.chips.lpoClientChips.options.map(o => o.key)), ["all", "HDEC", "TDC"]);
    });

    await check("a tab shows only its own kind", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.S.kind = "rental";
        w.ctx.renderLpoList();
        assert.deepEqual(listed(w).sort(), ["CSSP-RENT-0001", "CSSP-RENT-0002", "CSSP-RENT-0004", "CSSP-RENT-0005(3)"]);
        assert.match(w.byId("lpoTotals").textContent, /4 of 8 shown/);
    });

    await check("Need action shows only the LPOs with something to do", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.ctx.renderLpoList();
        assert.match(w.byId("lpoKpis").innerHTML, /Need action[\s\S]*>3</, "three LPOs need action - RENT-0004 is up to date by months");
        w.S.due = true;
        w.ctx.renderLpoList();
        assert.deepEqual(listed(w).sort(), ["CSSP-ADM-0017", "CSSP-OPEN-0001", "LPO CSSP-ADM-0033"]);
        assert.match(w.byId("lpoKpis").innerHTML, /aria-pressed="true"/);
        assert.match(w.byId("lpoKpis").innerHTML, /show all/, "the filter's state is said in words");
    });

    await check("search finds an LPO by an item on it or by one of its documents", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.S.search = "office chair";
        assert.deepEqual(w.ctx.visibleLpos().map(r => r.lpo_no), ["CSSP-ADM-0017"]);
        w.S.search = "inv-eq-hdec-070";
        assert.deepEqual(w.ctx.visibleLpos().map(r => r.lpo_no), ["CSSP-RENT-0002"]);
        w.S.search = "dn-hdec-067";
        assert.deepEqual(w.ctx.visibleLpos().map(r => r.lpo_no), ["LPO CSSP-ADM-0033"]);
    });

    await check("newest open LPO first, finished ones last", () => {
        const w = world();
        const order = w.ctx.lpoOrder(clone(ROWS)).map(r => r.lpo_no);
        assert.equal(order[0], "LPO CSSP-ADM-0033", "26 Sep is the newest: " + order);
        assert.deepEqual(order.slice(-2), ["CSSP-ADM-0010", "CSSP-RENT-0001"], order.join(", "));
        assert.ok(order.indexOf("CSSP-RENT-0005(3)") < order.indexOf("CSSP-RENT-0004"), "26 Aug before 24 Jul");
    });

    await check("a closed-by-value order that still owes a delivery stays up top", () => {
        const w = world();
        const owing = { ...clone(ROWS[2]), lpo_no: "OWES", lpo_date: "1/1/2026", next_step: step("deliver", "Create delivery note") };
        const order = w.ctx.lpoOrder([clone(ROWS[2]), owing]).map(r => r.lpo_no);
        assert.deepEqual(order, ["OWES", "CSSP-ADM-0010"]);
    });

    await check("a row carries at most one button, and it is the next step", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.ctx.renderLpoList();
        for (const r of ROWS) {
            const html = rowHtml(w, r.lpo_no);
            const buttons = (html.match(/<button/g) || []).length;
            assert.equal(buttons, r.next_step.action ? 1 : 0, r.lpo_no + ": " + buttons + " buttons");
            if (r.next_step.action) assert.match(html, new RegExp(`data-next="${r.next_step.key}"[^>]*>${r.next_step.text}<`));
            else assert.match(html, new RegExp(r.next_step.text), r.lpo_no + " says nothing about its state");
        }
        assert.ok(!/data-act=/.test(w.byId("lpoList").innerHTML), "an old row action is back");
    });

    await check("finished rows are struck through, open ones are not", () => {
        const w = world();
        w.S.rows = clone(ROWS);
        w.ctx.renderLpoList();
        assert.match(w.byId("lpoList").innerHTML, /class="lpo-row is-closed"[^>]*data-no="CSSP-ADM-0010"/);
        assert.match(w.byId("lpoList").innerHTML, /class="lpo-row"[^>]*data-no="CSSP-RENT-0005\(3\)"/);
    });

    await check("the money figures leave out supply contracts and LPOs closed by hand", () => {
        const w = world();
        const rows = clone(ROWS);
        rows.push({ ...clone(ROWS[3]), lpo_no: "EARLY", closed_at: "2026-09-01", remaining_usd: 9999,
                    next_step: step("closed", "Closed") });
        const k = Object.fromEntries(w.ctx.lpoKpis(rows).map(c => [c.k, c.v]));
        assert.equal(k["Not invoiced yet"], "$5,863.40", "9999 left on a terminated contract is not owed");
        assert.equal(k["Need action"], "3");
        assert.ok(!/0\.77/.test(k.Ordered), "the diesel unit price must not be summed");
    });

    await check("each next step does what it says", async () => {
        const w = world();
        w.S.rows = clone(ROWS);
        await w.ctx.runLpoNextStep(w.S.rows[0]);
        await w.ctx.runLpoNextStep(w.S.rows[1]);
        assert.deepEqual(w.calls.created.map(c => [c.kind, c.n]),
            [["inv", "LPO CSSP-ADM-0033"], ["dn", "CSSP-ADM-0017"]]);
        // CSSP-OPEN-0001's next step names May; the ledger says which dates May covers.
        w.ctx.ledger = { ok: true, months: [
            { month: "2026-05", label: "May 2026", count: 0, invoices: [], expected: true, state: "missed",
              start: "2026-05-15", end: "2026-06-14" },
            { month: "2026-06", label: "June 2026", count: 0, invoices: [], expected: true, state: "missed",
              start: "2026-06-15", end: "2026-07-14" }] };
        await w.ctx.runLpoNextStep(w.S.rows[6]);
        const last = w.calls.created.at(-1);
        assert.equal(last.kind, "inv");
        assert.equal(last.p.start, "2026-05-15", "the oldest missed month is the one invoiced, with its dates");
    });

    await check("a month that already has an invoice asks before a second one", async () => {
        const w = world();
        const before = w.calls.created.length;
        await w.ctx.invoiceLpoPeriod(clone(ROWS[4]), { month: "2026-09", label: "September 2026", count: 1,
                                                       invoices: ["INV-EQ-HDEC-094"], start: "2026-08-24" });
        assert.equal(w.calls.confirm.length, 1, "no question was asked");
        assert.equal(w.calls.created.length, before, "declining must not open an invoice");
    });

    /* ------------------------------------------------------- the LPO screen */
    const header = (lpo, signing = false) => {
        const w = world();
        w.S.signing = signing;
        w.ctx.renderLpoDocHeader(lpo);
        return w.byId("lpoDocActions").innerHTML;
    };

    await check("the header leads with the next step and adds the other create action", () => {
        const h = header(clone(ROWS[0]));
        assert.match(h, /class="primary" data-next="invoice">Create invoice/);
        assert.match(h, /data-doc="dn">Create delivery note/);
        assert.equal((h.match(/Create invoice/g) || []).length, 1, "Create invoice twice");
        assert.match(h, /lpo-menu-btn/);
    });

    await check("a rental is never offered a delivery note, nor a second invoice button", () => {
        const h = header({ ...clone(ROWS[3]), next_step: step("invoice_period", "Invoice October") });
        assert.ok(!/data-doc="dn"/.test(h), "rentals are not delivered");
        assert.match(h, /data-next="invoice_period">Invoice October/);
        assert.ok(!/data-doc="inv"/.test(h), "a second invoice button beside 'Invoice October'");
    });

    await check("a supply contract is delivered and invoiced", () => {
        const h = header(clone(ROWS[6]));
        assert.match(h, /data-next="invoice_period">Invoice May/);
        assert.match(h, /data-doc="dn"/, "diesel is delivered on delivery notes");
    });

    await check("a closed LPO offers nothing to create; a finished one no invoice", () => {
        const closed = header({ ...clone(ROWS[1]), closed_at: "2026-09-01", next_step: step("closed", "Closed") });
        assert.ok(!/data-doc="(dn|inv)"|data-next=/.test(closed), closed);
        const done = header(clone(ROWS[2]));
        assert.ok(!/data-doc="inv"/.test(done), "a fully invoiced order offered another invoice");
    });

    await check("an order invoiced directly can still be delivered", () => {
        const h = header({ ...clone(ROWS[2]), next_step: step("deliver", "Create delivery note") });
        assert.match(h, /data-next="deliver"/);
    });

    await check("while signing, the header steps aside", () => {
        assert.equal(header(clone(ROWS[0]), true), "");
    });

    await check("More holds the rare actions, and Sign again only once a copy exists", () => {
        const w = world();
        const signed = w.ctx.lpoMenuHtml(clone(ROWS[0]));
        for (const a of ["open", "print", "copy", "folder", "edit", "attach", "close", "delete", "sign"]) {
            assert.match(signed, new RegExp(`data-doc="${a}"`), a + " missing from More");
        }
        assert.match(signed, /Replace file/);
        assert.match(signed, /Sign again/);
        const unsigned = w.ctx.lpoMenuHtml({ ...clone(ROWS[1]), file_exists: false });
        assert.ok(!/Sign again/.test(unsigned));
        assert.match(unsigned, /Attach file/);
        assert.match(unsigned, /data-doc="print" disabled/, "no file, nothing to print");
    });

    const side = (lpo, extra = {}) => {
        const w = world();
        Object.assign(w.S, { fulfil: { lines: [] }, billing: null }, extra);
        w.ctx.renderLpoDocSide(lpo);
        return w.byId("lpoDocSide").innerHTML;
    };

    await check("an order shows its items; a contract shows its billing periods", () => {
        const order = side(clone(ROWS[0]), { fulfil: { lines: [
            { item: "Currency Counting Machine", unit: "EA", ordered: 1, delivered: 1, invoiced: 0 }] } });
        assert.match(order, />Items</);
        assert.match(order, /Currency Counting Machine/);
        assert.ok(!/Invoices by month/.test(order));
        const rental = side(clone(ROWS[3]), { billing: { ok: true, months: [] } });
        assert.match(rental, /Invoices by month/);
        assert.ok(!/>Items</.test(rental));
    });

    await check("the signed-copy line tells the truth, with the button that moves it on", () => {
        assert.match(side(clone(ROWS[0])), /Signed copy saved[\s\S]*data-doc="copy-signed"/);
        assert.match(side(clone(ROWS[3])), /No signed copy here[\s\S]*data-doc="sign"[^>]*>Sign and stamp/);
        assert.match(side({ ...clone(ROWS[3]), file_exists: false }), /No document attached[\s\S]*data-doc="attach"/);
    });

    await check("a warning appears only when something is wrong", () => {
        assert.ok(!/lpo-warn/.test(side(clone(ROWS[3]))), "a healthy rental shows a warning");
        assert.ok(!/lpo-warn/.test(side(clone(ROWS[4]))),
            "CSSP-RENT-0004 is fully invoiced with no end date - not a warning any more");
        assert.match(side({ ...clone(ROWS[0]), over_value: true }), /lpo-warn[^>]*>More has been invoiced/);
    });

    await check("billing is ONE list of months: each with its invoices, or Missed and an Invoice button", () => {
        const billing = { ok: true, term_months: 7, months_total: 7, months_invoiced: 1, invoices_not_sent: 1, months: [
            { month: "2026-06", label: "June 2026", count: 1, invoices: ["INV-EQ-HDEC-069"], expected: true, state: "invoiced",
              start: "2026-06-08", end: "2026-07-07", covers: "08 Jun - 07 Jul 2026" },
            { month: "2026-07", label: "July 2026", count: 0, invoices: [], expected: true, state: "missed",
              start: "2026-07-08", end: "2026-08-07", covers: "08 Jul - 07 Aug 2026" },
            { month: "2026-10", label: "October 2026", count: 0, invoices: [], expected: true, state: "current",
              start: "2026-10-08", end: "2026-11-07", covers: "08 Oct - 07 Nov 2026" },
            { month: "2027-02", label: "February 2027", count: 1, invoices: ["INV-STRAY"], expected: false, state: "extra" }] };
        const h = side(clone(ROWS[3]), { billing });
        assert.match(h, /June 2026[\s\S]*1 invoice[\s\S]*data-linked-no="INV-EQ-HDEC-069"/);
        assert.ok(!/data-month-index="0"/.test(h), "an invoiced month offered another invoice");
        assert.match(h, /July 2026[\s\S]*Missed[\s\S]*data-month-index="1"/, "a missed month must say so and offer Invoice");
        assert.match(h, /October 2026[\s\S]*Not invoiced yet/, "the current month is said, not alarmed");
        assert.match(h, /February 2027[\s\S]*outside the term[\s\S]*INV-STRAY/, "an invoice outside the term disappeared");
        assert.ok(!/data-month-index="3"/.test(h), "a month outside the term asked for an invoice");
        assert.match(h, /1 of 7/, "the count of months invoiced is missing");
        assert.match(h, /1 invoice is not marked sent yet/, "unsent invoices must be said");
        assert.equal((h.match(/data-linked-no="INV-EQ-HDEC-069"/g) || []).length, 1,
            "an invoice shown against its month was listed again below");
        assert.match(h, /Delivery notes and invoices[\s\S]*INV-EQ-HDEC-070/,
            "an invoice the ledger does not know about must still be listed, not lost");
        const closed = side({ ...clone(ROWS[3]), closed_at: "2026-09-01" }, { billing });
        assert.ok(!/data-month-index/.test(closed), "a closed contract offered an invoice");
    });

    await check("Copy file beside a signed copy copies the signed copy", async () => {
        const w = world();
        const lpo = clone(ROWS[0]);
        w.S.viewer = { lpo, which: "file", path: lpo.file_path };    // the original is on screen
        await w.ctx.lpoDocAction("copy-signed", lpo);
        assert.deepEqual(w.calls.copied, [lpo.ack_path]);
    });

    await check("Attach settles only after the upload, so the screen reloads with the new file", async () => {
        const w = world();
        let reopened = 0;
        w.ctx.reopenLpoDoc = async () => { reopened++; };
        const done = w.ctx.lpoDocAction("attach", clone(ROWS[1]));
        await new Promise(r => setTimeout(r, 0));
        assert.equal(reopened, 0, "the screen reloaded before a file was chosen");
        const picker = w.calls.picker;
        assert.ok(picker && typeof picker.onchange === "function", "no file picker");
        picker.files = [{ name: "CSSP-ADM-0017.pdf" }];
        w.ctx.FileReader = function () { this.readAsDataURL = () => { this.result = "data:x"; this.onload(); }; };
        await picker.onchange();
        await done;
        assert.equal(reopened, 1, "the screen did not reload after the upload");
    });

    await check("opening another LPO while one is still loading shows the newer one", async () => {
        /* The file, pdf.js and the fit measurement all wait; the first LPO used
           to finish on top of the second - its pages in the wrong screen, or a
           crash on a viewer that had not loaded. */
        const w = world();
        const page = { getViewport: ({ scale }) => ({ width: 100 * scale, height: 141 * scale }),
                       render: () => ({ promise: Promise.resolve() }) };
        w.ctx.pdfjsLib = { GlobalWorkerOptions: {},
                           getDocument: () => ({ promise: Promise.resolve({ numPages: 1, getPage: async () => page }) }) };
        w.ctx.atob = atob;
        const file = (n) => ({ ok: true, is_pdf: true, data_uri: "data:application/pdf;base64,AA==",
                               filename: n + ".pdf", path: n + ".pdf" });
        let releaseA;
        w.ctx.bridge.lpo_file_data_uri = (c, n) => n === "A"
            ? new Promise(r => { releaseA = () => r(file("A")); })
            : Promise.resolve(file("B"));
        const A = { client_name: "HDEC", lpo_no: "A", file_exists: true };
        const B = { ...A, lpo_no: "B" };
        const first = w.ctx.openLpoDocFile(A, "file");
        await w.ctx.openLpoDocFile(B, "file");
        releaseA();
        await first;
        assert.equal(w.S.viewer.lpo.lpo_no, "B", "the slower, older LPO took the screen back");
        assert.match(w.byId("lpoDocFile").innerHTML, /B\.pdf/);
    });

    await check("Previous / Next LPO are gone", () => {
        assert.ok(!/stepLpoDoc|btnLpoDocPrev|btnLpoDocNext/.test(SRC + HTML));
    });

    console.log(failures ? `\n${failures} FAILED` : "\nall passed");
    process.exitCode = failures ? 1 : 0;
})();
