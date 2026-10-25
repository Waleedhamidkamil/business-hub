/* The monthly billing reminder has to reach the screen.

   `dashboard_needs_attention` grew a `billing` bucket on 2026-09-11: rentals
   and supply contracts with a month nobody has invoiced. A bucket the To-Do
   view never renders is a bucket that does not exist, and the six edits that
   wire it in are spread across one file - exactly the shape of change that
   rots quietly.

   Source-level, like the other todo.js guards: this file is an IIFE with no
   exports, and standing up a DOM for it needs jsdom, which this project
   deliberately does not have. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
    path.join(__dirname, "..", "static", "todo.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("the bucket is read out of the feed", () => {
    assert.ok(/d\.billing\s*\|\|\s*\[\]/.test(SRC),
        "render() never reads d.billing, so the section can only ever be empty");
});

check("it counts towards whether anything is outstanding", () => {
    const m = SRC.match(/const total =[\s\S]{0,200}?;/);
    assert.ok(m, "could not find the outstanding-work total");
    assert.ok(/billing\.length/.test(m[0]),
        'an unbilled month would leave the To-Do saying "All caught up": ' + m[0]);
});

/* 2026-10-21 (Waleed): every type is a TAB holding a table now, so "its own
   section" means its own entry in TABS and its own column set. */
check("it renders as its own tab", () => {
    assert.ok(/\{ cat: "billing", label:/.test(SRC),
        "the bucket is counted but never drawn");
    assert.ok(/billing:\s*\["doc", "client"/.test(SRC), "the billing tab has no columns");
});

check("the tab explains itself in words", () => {
    const m = SRC.match(/\{ cat: "billing", label: "([^"]+)",\s*\n?\s*hint: "([^"]+)"/);
    assert.ok(m, "the billing tab has no title and hint");
    assert.ok(m[1].length > 3, "a tab needs a readable title");
    assert.ok(/invoice/i.test(m[2]), "the hint should say what the work is: " + m[2]);
});

check("a row offers a way to reach the LPO", () => {
    /* Anchored on the row-builder branch specifically. `cat === "billing"`
       appears three times in this file (tail, row actions, modal actions), so
       indexOf alone lands on the wrong one. */
    const i = SRC.indexOf('else if (cat === "billing") {');
    assert.ok(i > 0, "no billing branch in the row builder");
    const m = [SRC.slice(i, i + 900)];
    assert.ok(/data-act="open"/.test(m[0]),
        "the row has no action, so the reminder is a dead end");
    assert.ok(/Open LPO/.test(m[0]), "the button needs a readable label");
});

check("Open LPO reaches THAT LPO, not the list of them", () => {
    /* 2026-09-12 - it used to map to `showLpos`, which dropped Waleed on the
       whole Client LPOs screen and left him to find the row again. */
    assert.ok(/lpo:\s*"openLpoByNo"/.test(SRC),
        'kind "lpo" has no entry, so Open LPO would toast "can\'t open this type"');
    assert.ok(!/lpo:\s*"showLpos"/.test(SRC), "still routed at the list screen");
});

check("the client travels with the number", () => {
    /* A PO number alone does not identify an LPO - the key is client + number,
       and two clients can each have a CSSP-ADM-0010. */
    assert.ok(/function openDocEditor\(kind, no, client\)/.test(SRC),
        "openDocEditor cannot carry a client");
    assert.ok(/fn\(no,\s*client\)/.test(SRC), "the client is accepted but never passed on");
    assert.ok(/openDocEditor\("lpo", lpoNo, clientName\)/.test(SRC),
        "the preview's Open LPO drops the client");
});

check("the preview is big enough to read a scanned page", () => {
    /* Waleed, 2026-09-12: "the LPO is not visable and cannot be read
       correctly". A4 inside a 680px popup is about a third of print size. */
    const i = SRC.indexOf("function openLpoTodoModal");
    const body = SRC.slice(i, SRC.indexOf("async function openDocModal", i));
    assert.ok(/size:\s*"xl"/.test(body), "the LPO preview is back to a small dialog");
    const css = fs.readFileSync(
        path.join(__dirname, "..", "static", "app_styles.css"), "utf8");
    assert.ok(/\.popup\.todo-lpo-popup\s*\{[^}]*width:/.test(css),
        "nothing widens the dialog past .popup-xl's 900px");
    assert.ok(/\.todo-lpo-page\s*\{[^}]*max-height:\s*calc\(100vh/.test(css),
        "the page box is still capped at a fixed pixel height");
});

check("the preview can reach page 2", () => {
    /* Waleed, 2026-09-12: "no pages navigation buttons in the todos popup".
       A PO's second sheet was unreachable - page 1 was all it ever showed. */
    const i = SRC.indexOf("function openLpoTodoModal");
    const body = SRC.slice(i, SRC.indexOf("async function openDocModal", i));
    assert.ok(/todoLpoPager/.test(body), "the dialog has no pager");
    assert.ok(/function loadLpoModalPages/.test(body), "nothing loads the other pages");
    const loader = SRC.slice(SRC.indexOf("async function loadLpoModalPages"),
                             SRC.indexOf("async function openDocModal"));
    assert.ok(/for \(let n = 2; n <= total/.test(loader),
        "only the first page is ever fetched");
    assert.ok(/data-step/.test(loader) && /scrollTo/.test(loader),
        "the buttons do not move between pages");
    const css = fs.readFileSync(
        path.join(__dirname, "..", "static", "app_styles.css"), "utf8");
    assert.ok(/\.todo-lpo-page\s*\{[^}]*overflow:\s*auto/.test(css),
        "the page box does not scroll, so the pages below the first are cut off");
});

check("the rest of the pages arrive after the first is on screen", () => {
    /* A three-page contract must not hold the dialog blank while it renders. */
    const loader = SRC.slice(SRC.indexOf("async function loadLpoModalPages"),
                             SRC.indexOf("async function openDocModal"));
    assert.ok(loader.indexOf("b.innerHTML = shot(") < loader.indexOf("for (let n = 2"),
        "page 1 is painted only after every other page has been fetched");
    assert.ok(/if \(!live\) return;/.test(loader),
        "a popup closed mid-load would write into a dialog that is gone");
});

check("an LPO row is badged as an LPO", () => {
    assert.ok(/lpo:\s*"LPO"/.test(SRC),
        "without a badge the row shows a raw kind string");
});

check("the month count is carried by words, not by colour", () => {
    /* the "Months due" column (2026-10-21 table) */
    const i = SRC.indexOf('due: { label: "Months due"');
    assert.ok(i > 0, "the billing table has no Months due column");
    const branch = SRC.slice(i, i + 900);
    assert.ok(/month\$\{/.test(branch) || /month\s*\$\{/.test(branch) || /months due/.test(branch)
              || /month\$\{n === 1/.test(branch),
        "the tail must spell out the count - Waleed is colour blind: " + branch.slice(0, 300));
});

check("a billing row opens its OWN preview, not the document one", () => {
    /* The shared preview loads via load_quote / load_dn / load_invoice, none of
       which can fetch a client's LPO - it would come up empty. Waleed asked for
       a popup here like every other To-Do row (2026-09-12), so this one has its
       own: the PO's first page, plus the actions that clear the reminder. */
    const i = SRC.indexOf('el.dataset.cat === "billing"');
    assert.ok(i > 0, "the billing row no longer routes anywhere of its own");
    assert.ok(/openLpoTodoModal/.test(SRC.slice(i, i + 320)),
        "a billing row should open the LPO preview");
    assert.ok(/function openLpoTodoModal/.test(SRC), "that preview does not exist");
});

check("the preview offers the two documents that clear the reminder", () => {
    const i = SRC.indexOf("function openLpoTodoModal");
    const body = SRC.slice(i, SRC.indexOf("async function openDocModal", i));
    assert.ok(/createInvoiceFromLpo/.test(body), "no way to start the invoice");
    assert.ok(/createDnFromLpo/.test(body), "no way to start the delivery note");
    assert.ok(/openDocEditor\("lpo"/.test(body), "no way to reach the record itself");
});

check("the preview shows the client's actual page", () => {
    const i = SRC.indexOf("function openLpoTodoModal");
    const body = SRC.slice(i, SRC.indexOf("async function openDocModal", i));
    assert.ok(/lpo_page_image/.test(body),
        "the popup shows no document - Waleed asked to SEE the LPO");
    /* Fetched after the popup is up: a click that shows nothing for a second
       reads as a click that did nothing. Measured inside the modal function
       alone - the loader that does the fetching sits below it. */
    const modal = SRC.slice(i, SRC.indexOf("async function loadLpoModalPages", i));
    assert.ok(modal.indexOf("Popup.open") < modal.indexOf("loadLpoModalPages"),
        "the image must be fetched AFTER the dialog is on screen");
});

check("a missing rasteriser is not a broken popup", () => {
    const i = SRC.indexOf("function openLpoTodoModal");
    const body = SRC.slice(i, SRC.indexOf("async function openDocModal", i));
    assert.ok(/img && img\.ok/.test(body),
        "nothing checks whether the page actually rendered");
    assert.ok(/Open the LPO to see the document/.test(body),
        "there is no fallback message when it did not");
});

check("the modal offers Open LPO rather than a state change", () => {
    const m = SRC.match(/return \[IGNORE_ACT, \{ label: "Open LPO"[\s\S]{0,200}?\]/);
    assert.ok(m,
        "the modal has no Open LPO action, or it is not muteable - and this "
        + "bucket fires every month forever, so Ignore has to be there");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
