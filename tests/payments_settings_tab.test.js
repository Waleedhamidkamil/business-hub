/* The payment accounts are managed in ONE place, and each carries its rate
   (2026-09-21).

   They used to be editable only from the gear on the invoice editor, which is
   not where anybody looks for a bank account - and quotations, which now take
   their exchange rate from the client's receiving account, had no way to reach
   them at all. Two editors for one list is how they drift apart.

   Source-level: this is markup and wiring, which is the part that rots. The
   rate arithmetic itself is covered properly in
   `tests/payment_account_rate.test.py`. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const HTML = fs.readFileSync(path.join(root, "templates", "index.html"), "utf8");
const DOCSET = fs.readFileSync(path.join(root, "static", "doc_settings.js"), "utf8");
const APP = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const PYSET = fs.readFileSync(path.join(root, "backend", "doc_settings.py"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("Settings has a Payments section", () => {
    assert.ok(/data-sect="payments"/.test(HTML), "no nav item");
    assert.ok(/data-panel="payments"/.test(HTML), "no panel");
});

/* Read the CODE, not the commentary: an earlier test in this repo asserted on
   an attribute and was satisfied by the comment explaining it. */
const PANEL = (() => {
    const at = HTML.indexOf('data-panel="payments"');
    return at < 0 ? "" : HTML.slice(at, HTML.indexOf("</div>\n        <div class=\"settings-panel\"", at) + 1 || at + 5000);
})();

check("the rate is edited per account, beside the account", () => {
    assert.ok(PANEL, "no payments panel");
    assert.ok(/id="payAccounts"/.test(PANEL),
        "the panel needs a container the editor renders the accounts into");
    assert.ok(/id="payFallbackRate"/.test(PANEL),
        "the rate used when no account is known must be visible and editable - "
        + "a rate nobody can see is exactly how the two rules this replaces "
        + "came to be invisible");
});

check("the invoice gear no longer edits banks", () => {
    assert.ok(!/function renderBankList/.test(DOCSET),
        "two editors for one list is how they drift apart");
    assert.ok(!/function renderBankCard/.test(DOCSET),
        "the account card editor belongs to the Payments tab now");
    assert.ok(/Settings/.test(DOCSET) && /Payments/.test(DOCSET),
        "it should say where the accounts moved to");
});

check("the accounts editor is wired", () => {
    assert.ok(/function renderPaymentAccounts/.test(APP),
        "nothing renders the payments panel");
    assert.ok(/get_doc_settings\("invoice"\)/.test(APP),
        "the accounts live in the invoice section of settings.json, so the "
        + "editor reads them through the same door the invoice modal used");
    assert.ok(/save_doc_settings\("invoice"/.test(APP),
        "and writes them back the same way");
});

check("a rate that is not a rate is refused, not stored", () => {
    /* Read the SAVE function, not the render one. The first draft of this check
       searched renderPaymentAccounts, matched an unrelated comparison there, and
       stayed green with the validation deleted. */
    const at = APP.indexOf("async function savePaymentAccounts");
    assert.ok(at > 0, "no save function");
    const body = APP.slice(at, APP.indexOf("\n}", at));
    assert.ok(/Number\(account\.iqd_per_usd\)\s*>\s*0/.test(body),
        "zero and negative are not exchange rates; saving one would put a "
        + "silently wrong divisor behind every profit figure for that client");
    assert.ok(/return;/.test(body),
        "and it has to REFUSE, not warn and save anyway");
});

check("the backend knows about the fallback rate", () => {
    assert.ok(/fallback_iqd_per_usd/.test(PYSET),
        "an undeclared settings key is dropped on save");
    assert.ok(/INV_FALLBACK_IQD_PER_USD/.test(PYSET),
        "payment_accounts.fallback_rate reads that config target");
});

check("the rate readout names the account it came from", () => {
    assert.ok(/function rateAccountLabel\(/.test(APP),
        "the Costing screen shows a converted figure; it must say which account "
        + "that rate belongs to, or the number has no explanation");
    const quote = fs.readFileSync(path.join(root, "static", "quote.js"), "utf8");
    const dn = fs.readFileSync(path.join(root, "static", "dn.js"), "utf8");
    assert.ok(/rateAccountLabel\("quote"/.test(quote), "the quotation readout does not");
    assert.ok(/rateAccountLabel\("dn"/.test(dn), "the delivery note readout does not");
});

check("the panel sits inside the settings panel container", () => {
    /* A panel outside `.settings-panels` is never shown by initSettingsNav,
       which toggles `hidden` on `.settings-panel` elements only. */
    const panels = HTML.indexOf('<div class="settings-panels">');
    const payments = HTML.indexOf('data-panel="payments"');
    assert.ok(panels > 0 && payments > panels,
        "the Payments panel must live inside .settings-panels or the tab opens "
        + "on an empty screen");
});

check("opening the tab is what loads the accounts", () => {
    const at = APP.indexOf("function initSettingsNav");
    const body = APP.slice(at, APP.indexOf("\n}", at));
    assert.ok(/renderPaymentAccounts\(\)/.test(body),
        "nothing reads the accounts when the tab is opened, so it paints empty");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
