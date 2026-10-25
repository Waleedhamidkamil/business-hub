/* Attaching a delivery note or invoice to the LPO it fulfils.

   `dns.lpo_no` and `invoices.lpo_no` were free text, and it showed: 63 of 64
   delivery notes in the live Abraj database say "N/A", one says CSSP-ADM-0010,
   and two invoices carry "20260905 SHIP WATER", which is not an LPO number at
   all. Everything the LPO screen reports - delivered, invoiced, closed, what is
   still owed - hangs off that string matching, so nothing ever lit up.

   The number is still what prints on the document. What changes is that it can
   only be a number this client actually sent. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "lpo_picker.js");

function load(listLpos) {
    const ctx = { window: {}, console, setTimeout: (f) => f() };
    ctx.window.document = { getElementById: () => null };
    ctx.window.pywebview = { api: { list_lpos: listLpos } };
    vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx);
    return ctx.window.LpoPicker;
}

const ROWS = [
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0010", status: "delivered",
      kind: "materials", total: 18390, currency: "USD", lpo_date: "9/7/2026" },
    { client_name: "HDEC", lpo_no: "CSSP-ADM-0017", status: "closed",
      kind: "materials", total: 273.4, currency: "USD", lpo_date: "24/7/2026" },
    { client_name: "HDEC", lpo_no: "CSSP-RENT-0002", status: "invoiced",
      kind: "rental", total: 11900, currency: "USD", lpo_date: "4/6/2026" },
    { client_name: "HDEC", lpo_no: "CSSP-OPEN-0001", status: "received",
      kind: "open", total: 0, currency: "USD", lpo_date: "15/5/2026" },
];

/* list_lpos filters by client server-side, so the fake does too. */
const fake = async (client) => ROWS.filter(r => !client || r.client_name === client);

let failures = 0;
function check(what, fn) {
    return Promise.resolve().then(fn)
        .then(() => console.log("  ok   " + what))
        .catch(e => { failures++; console.log("  FAIL " + what + "\n       " + e.message); });
}

(async () => {
    const P = load(fake);

    await check("only this client's LPOs are offered", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(out.length, "nothing came back at all");
        assert.ok(out.every(r => r.client_name === "HDEC"),
            "another client's PO must never be offered");
    });

    await check("a closed LPO is not offered", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(!out.some(r => r.lpo_no === "CSSP-ADM-0017"),
            "CSSP-ADM-0017 is closed and cannot take new documents");
    });

    await check("a running rental is still offered after it has been invoiced", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(out.some(r => r.lpo_no === "CSSP-RENT-0002"),
            "a rental is invoiced every month and stays open all term");
    });

    await check("an open supply contract is always offered", async () => {
        const out = await P.choicesFor("HDEC");
        assert.ok(out.some(r => r.lpo_no === "CSSP-OPEN-0001"));
    });

    await check("the current value is kept even when it is now closed", async () => {
        const out = await P.choicesFor("HDEC", "CSSP-ADM-0017");
        assert.ok(out.some(r => r.lpo_no === "CSSP-ADM-0017"),
            "reopening a saved document must not silently drop its LPO");
    });

    await check("the current value is matched however it was typed", async () => {
        const out = await P.choicesFor("HDEC", "  cssp-adm-0017 ");
        assert.ok(out.some(r => r.lpo_no === "CSSP-ADM-0017"));
    });

    await check("a client with no LPOs on file yields an empty list, not an error", async () => {
        assert.deepEqual(await P.choicesFor("Nobody"), []);
    });

    await check("no client named yields nothing rather than everyone's POs", async () => {
        /* A blank client on a new document must not offer another client's
           purchase orders to attach to it. */
        assert.deepEqual(await P.choicesFor(""), []);
    });

    await check("N/A is a real answer", () => {
        assert.strictEqual(P.NO_LPO, "N/A");
    });

    await check("N/A is recognised however it is typed", () => {
        assert.ok(P.isNoLpo("N/A"));
        assert.ok(P.isNoLpo(" n/a "));
        assert.ok(P.isNoLpo(""), "an empty box means no LPO too");
        assert.ok(!P.isNoLpo("CSSP-ADM-0010"));
    });

    await check("an unreachable bridge returns empty rather than throwing", async () => {
        const Q = load(async () => { throw new Error("gone"); });
        assert.deepEqual(await Q.choicesFor("HDEC"), []);
    });

    await check("a bridge returning nothing is handled", async () => {
        const Q = load(async () => null);
        assert.deepEqual(await Q.choicesFor("HDEC"), []);
    });

    await check("each choice describes itself in words", async () => {
        const out = await P.choicesFor("HDEC");
        const rental = out.find(r => r.lpo_no === "CSSP-RENT-0002");
        const line = P.describe(rental);
        assert.ok(/Rental/i.test(line), "the kind must be readable: " + line);
        assert.ok(/11,900/.test(line), "the value must be readable: " + line);
        assert.ok(/Invoiced/i.test(line), "the status is a WORD, never a colour: " + line);
    });

    await check("an open contract is not described by its meaningless total", async () => {
        const out = await P.choicesFor("HDEC");
        const open = out.find(r => r.lpo_no === "CSSP-OPEN-0001");
        const line = P.describe(open);
        assert.ok(!/0\.00/.test(line),
            "an open contract has no value to show, and 0.00 reads as free: " + line);
        assert.ok(/Supply contract/i.test(line), line);
    });

    /* ---- wiring: a picker nothing calls is a picker that does not exist ---- */
    const read = (f) => fs.readFileSync(path.join(__dirname, "..", f), "utf8");
    const HTML = read("templates/index.html");
    const DN = read("static/dn.js");
    const INV = read("static/inv.js");

    await check("the page loads the picker", () => {
        assert.ok(/static\/lpo_picker\.js/.test(HTML),
            "LpoPicker would be undefined at runtime");
    });

    for (const [label, src, field, client] of [
            ["delivery note", DN, "dnLpo", "dnClient"],
            ["invoice", INV, "invLpo", "invClient"]]) {
        await check(`the ${label} editor has a Choose button`, () => {
            assert.ok(new RegExp(`id="${field}Pick"`).test(HTML),
                `no ${field}Pick button in the markup`);
            assert.ok(new RegExp(`${field}Pick`).test(src),
                `${label}: the button exists but nothing binds it`);
        });

        await check(`the ${label} picker is given its client`, () => {
            const i = src.indexOf(`${field}Pick`);
            const near = src.slice(i, i + 600);
            assert.ok(near.includes(client),
                `${label}: without the client it would offer everyone's POs`);
            assert.ok(/LpoPicker\.pick\(/.test(near),
                `${label}: the button does not open the picker`);
        });

        await check(`the ${label} LPO field stays typable`, () => {
            const m = HTML.match(new RegExp(`<input[^>]*id="${field}"[^>]*>`));
            assert.ok(m, `${field} is gone from the markup`);
            assert.ok(!/readonly|disabled/.test(m[0]),
                `${label}: an odd case must never be un-fileable: ` + m[0]);
        });
    }

    console.log(failures ? `\n${failures} FAILED` : "\nall passed");
    process.exit(failures ? 1 : 0);
})();
