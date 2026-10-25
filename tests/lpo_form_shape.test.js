/* The LPO form must fit all three kinds of PO Waleed receives.

   It must not offer a total on a contract that has no ceiling (CSSP-OPEN-0001
   prints 0.77, the price of a litre of diesel), must not hide the period on one
   that runs for months, and must be able to pull its lines from the quotation
   the PO cites rather than asking him to retype them.

   Source-level: `lpo.js` is an IIFE that talks to the DOM and the pywebview
   bridge, and this project has no jsdom on purpose. The arithmetic it relies on
   is covered properly in `lpo_lines_editor.test.js`; this file guards the
   wiring, which is the part that rots. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
    path.join(__dirname, "..", "static", "lpo.js"), "utf8");
const HTML = fs.readFileSync(
    path.join(__dirname, "..", "templates", "index.html"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("the form asks which kind of PO this is", () => {
    assert.ok(/lpoFKind/.test(SRC), "no kind control in the form");
    for (const k of ["materials", "rental", "open"]) {
        assert.ok(new RegExp(`["']${k}["']`).test(SRC), `kind ${k} is not offered`);
    }
});

check("each kind is named in words a person recognises", () => {
    for (const label of ["Materials", "Rental", "Supply contract"]) {
        assert.ok(SRC.includes(label), `the form never says "${label}"`);
    }
});

check("a rental asks for its period", () => {
    assert.ok(/lpoFPeriodStart/.test(SRC) && /lpoFPeriodEnd/.test(SRC),
        "a rental with no period cannot raise a monthly billing reminder");
});

check("a rental asks for a monthly amount", () => {
    assert.ok(/lpoFMonthly/.test(SRC),
        "CSSP-RENT-0002 is 850 a month - without it the reminder has no figure");
});

check("the money block changes with the kind", () => {
    assert.ok(/lpoKindFields|applyKindFields|paintKind/.test(SRC),
        "nothing switches the form between the three shapes");
});

check("visibility is toggled with hidden, not inline display", () => {
    /* House contract: `[hidden]` is the one switch every screen here uses. */
    assert.ok(/\.hidden\s*=/.test(SRC), "no use of el.hidden");
});

check("the form can pull lines from the cited quotation", () => {
    assert.ok(/lpo_from_quote/.test(SRC), "nothing calls the quotation bridge");
    assert.ok(/LpoLines/.test(SRC), "the lines editor is never used");
});

check("a failed pull says so rather than doing nothing", () => {
    const i = SRC.indexOf("lpo_from_quote");
    const near = SRC.slice(Math.max(0, i - 400), i + 900);
    assert.ok(/toast\(/.test(near),
        "a pull that fails must not look like a dead button: " + near.slice(0, 200));
});

check("the mismatch note is shown, not just computed", () => {
    assert.ok(/\.mismatch\(/.test(SRC),
        "a lines-versus-total mismatch is never worked out");
    const i = SRC.indexOf(".mismatch(");
    const near = SRC.slice(i, i + 400);
    assert.ok(/textContent|innerHTML|hidden/.test(near),
        "the note is computed but never put on screen: " + near.slice(0, 200));
});

check("lines can also be typed by hand", () => {
    /* CSSP-RENT-0002 cites QUT-146 and CSSP-OPEN-0001 cites QUT-03 - both are
       HDEC's own references, not ours, so neither can be pulled. */
    assert.ok(/LpoLines\.blank\(\)/.test(SRC),
        "a PO citing the client's own reference could not be filed at all");
});

check("the client list comes from Clients Master Data", () => {
    assert.ok(/cm_list/.test(SRC),
        "the form still feeds its client list from the old clients.py");
});

check("saving sends every new field", () => {
    /* The payload is built by a named function, not inline, because a client
       refusal has to rebuild it and save again - reading the fields twice from
       two places is how the two copies drift apart. */
    const i = SRC.indexOf("function buildLpoPayload()");
    assert.ok(i > 0, "could not find the save payload builder");
    const body = SRC.slice(i, i + 1600);
    for (const f of ["kind", "rows", "period_start", "period_end",
                     "monthly_amount", "erp_no", "project", "contact"]) {
        assert.ok(new RegExp(`\\b${f}\\b`).test(body), `payload omits ${f}`);
    }
});

check("the existing fields are still sent", () => {
    const i = SRC.indexOf("function buildLpoPayload()");
    const body = SRC.slice(i, i + 1600);
    for (const f of ["client_name", "lpo_no", "lpo_date", "total",
                     "currency", "source_quote_no", "notes"]) {
        assert.ok(new RegExp(`\\b${f}\\b`).test(body), `payload dropped ${f}`);
    }
});

check("a refused client offers the add-client popup", () => {
    assert.ok(/ClientRequired/.test(SRC),
        "an LPO naming an unknown client would dead-end with a toast");
    const i = SRC.indexOf("ClientRequired.offer(");
    assert.ok(i > 0, "the refusal is never offered to the user");
    assert.ok(/save_lpo/.test(SRC.slice(i, i + 400)),
        "the offer is made but the save is never retried");
    assert.ok(/buildLpoPayload\(\)/.test(SRC.slice(i, i + 400)),
        "the retry must REBUILD the payload - the new client name is in the "
        + "field now, and resending the old object would save the refused name");
});

check("client_required knows where to put the name back", () => {
    const CR = fs.readFileSync(
        path.join(__dirname, "..", "static", "client_required.js"), "utf8");
    assert.ok(/lpo:\s*\{/.test(CR),
        "no lpo entry in FIELDS, so a newly added client never lands in the form");
    assert.ok(/lpoFClient/.test(CR), "the entry does not name the form's field");
});

check("the form's helpers are declared before the popup opens", () => {
    /* Popup.open calls onOpen SYNCHRONOUSLY and swallows what it throws
       (popup.js:324). A const used by the wiring but declared below the call
       hits the temporal dead zone, and the form opens blank and unwired with
       no error anywhere - which is exactly what happened on the first draft. */
    /* Scoped to openLpoForm: other popups in this file (the close dialog) open
       earlier, so searching the whole file finds the wrong one. */
    const formAt = SRC.indexOf("async function openLpoForm(");
    assert.ok(formAt > 0, "openLpoForm is gone");
    const openAt = SRC.indexOf("window.Popup.open({", formAt);
    assert.ok(openAt > 0, "could not find the form popup");
    for (const decl of ["const g = id =>", "const currentKind ="]) {
        const at = SRC.indexOf(decl, formAt);
        assert.ok(at > 0, `${decl} is gone from openLpoForm`);
        assert.ok(at < openAt,
            `${decl} is declared AFTER Popup.open - onOpen would throw into a `
            + "catch that hides it");
    }
});

check("lpo_lines.js is actually loaded by the page", () => {
    assert.ok(/static\/lpo_lines\.js/.test(HTML),
        "LpoLines would be undefined at runtime");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
