/* What you typed into the LPO form must reach the save (2026-09-19).

   Reported by Waleed on the Eshraqat profile: "Add a client LPO", pick the
   client, fill it in, press Add - and the app answers "This document needs a
   client. Pick one from the list". He had picked one.

   He had. The name never left the form. `openLpoForm` built its payload AFTER
   `await handle.result`, and `Popup.close` detaches the dialog BEFORE it
   resolves that promise (popup.js: `finish()` runs `bd.remove()` and only then
   `resolveResult(value)`). So every `document.getElementById("lpoF...")` came
   back null, every field read as "", and the blank client was simply the first
   thing the backend refused. The date, total, currency, lines and notes were
   being dropped the same way - the client was just the loudest.

   It looked profile-specific because it is not: no LPO had been added through
   the form in either profile since the dialog moved to `Popup.open` on
   2026-09-11. The six LPOs in the Abraj file were written by the backend tests,
   four of them inside the same second. Eshraqat is simply where he next tried
   to add a real one.

   Two halves, and both matter:
     1. the platform fact, run against the real popup.js - a field inside a
        popup is gone by the time `result` resolves;
     2. `openLpoForm` therefore reads and SAVES while the dialog is still open,
        the way `client_required.js` already does (`keep: true` + `onClick`).

   No jsdom in this repo on purpose; part 1 runs the real dialog against a DOM
   double, part 2 is source-level over the one function that has to obey. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(root, "static", "lpo.js"), "utf8");
const POPUP = fs.readFileSync(path.join(root, "static", "popup.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

/* ------------------------------------------------------------------ */
/* A DOM double deep enough to run a real dialog. Same shape as the one */
/* in popup_contract.test.js, plus the getElementById that is the whole */
/* point here: like a browser's, it finds ATTACHED nodes only.          */
/* ------------------------------------------------------------------ */
function makeDom() {
    const listeners = {};
    const doc = {
        activeElement: null,
        createElement: mkEl,
        addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
        removeEventListener: (t, fn) => {
            listeners[t] = (listeners[t] || []).filter((f) => f !== fn);
        },
    };
    function mkEl(tag) {
        const el = {
            tagName: String(tag).toUpperCase(),
            children: [], parent: null, style: {}, attrs: {},
            id: "", className: "", value: "", offsetParent: {}, _text: "", _html: "", _l: {},
        };
        const has = (c) => el.className.split(/\s+/).indexOf(c) >= 0;
        el.classList = {
            contains: has,
            add: (...c) => c.forEach((x) => { if (!has(x)) el.className = (el.className + " " + x).trim(); }),
            remove: (...c) => c.forEach((x) => {
                el.className = el.className.split(/\s+/).filter((y) => y && y !== x).join(" ");
            }),
            toggle: (c, on) => (on ? el.classList.add(c) : el.classList.remove(c)),
        };
        el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
        el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
        el.appendChild = (c) => { c.parent = el; el.children.push(c); return c; };
        el.remove = () => {
            if (el.parent) el.parent.children = el.parent.children.filter((c) => c !== el);
            el.parent = null;
        };
        el.addEventListener = (t, fn) => { (el._l[t] = el._l[t] || []).push(fn); };
        el.focus = () => { doc.activeElement = el; };
        el.contains = (n) => { for (let p = n; p; p = p.parent) if (p === el) return true; return false; };
        Object.defineProperty(el, "textContent", {
            get: () => el._text, set: (v) => { el._text = String(v); el.children = []; },
        });
        Object.defineProperty(el, "innerHTML", {
            get: () => el._html, set: (v) => { el._html = String(v); },
        });
        el.querySelectorAll = () => [];
        el.querySelector = () => null;
        return el;
    }
    doc.body = mkEl("body");
    // A browser walks the attached tree. A detached node is not in it.
    doc.getElementById = (id) => {
        let found = null;
        (function walk(n) {
            n.children.forEach((c) => { if (c.id === id && !found) found = c; walk(c); });
        })(doc.body);
        return found;
    };
    return doc;
}

function loadPopup() {
    const doc = makeDom();
    const win = {
        document: doc, console, setTimeout, clearTimeout,
        matchMedia: () => ({ matches: true }),   // reduced motion: close synchronously
        requestAnimationFrame: (fn) => fn(),
        getComputedStyle: () => ({}),
    };
    win.window = win;
    vm.createContext(win);
    vm.runInNewContext(POPUP, win);
    return { doc, win };
}

/* ---------- 1. the platform fact this bug is made of ---------- */
async function fieldIsGoneAfterResult() {
    const { doc, win } = loadPopup();
    const handle = win.Popup.open({
        title: "Add a client LPO",
        body: (host) => {
            const input = doc.createElement("input");
            input.id = "lpoFClient";
            input.value = "HDEC";
            host.appendChild(input);
        },
        actions: [{ label: "Cancel", value: null }, { label: "Add", value: "ok", primary: true }],
    });
    const whileOpen = doc.getElementById("lpoFClient");
    handle.close("ok");
    await handle.result;
    return { whileOpen, afterResult: doc.getElementById("lpoFClient") };
}

/* ---------- 2. the one function that has to obey ---------- */
const at = SRC.indexOf("async function openLpoForm");
const END = SRC.indexOf("\n// =====", at);
const RAW = SRC.slice(at, END > at ? END : SRC.length);
/* Read the CODE, not the commentary. The first draft of this file asserted on
   `keep: true` and was satisfied by the COMMENT that explains why it is there -
   it stayed green with the attribute itself deleted. */
const FORM = RAW.replace(/\/\*[\s\S]*?\*\//g, "")
                .replace(/(^|[^:"'`])\/\/[^\r\n]*/gm, "$1");

(async () => {
    const gone = await fieldIsGoneAfterResult();

    check("a popup's own field is unreachable once the dialog has resolved", () => {
        assert.ok(gone.whileOpen && gone.whileOpen.value === "HDEC",
            "the double never had the field, so this proves nothing");
        assert.strictEqual(gone.afterResult, null,
            "Popup detaches the panel before it resolves, so anything read by id "
            + "after `await handle.result` is null. If this ever passes, popup.js "
            + "changed its close order and this whole class of bug changed shape.");
    });

    check("the Add button saves while the form is still on screen", () => {
        assert.ok(at >= 0, "openLpoForm is gone");
        assert.ok(/keep:\s*true/.test(FORM),
            "the primary action must act WITHOUT closing (`keep: true`), or the "
            + "fields are detached before anything reads them");
        assert.ok(/onClick:/.test(FORM),
            "the primary action needs an onClick to do the saving");
    });

    check("nothing is read out of the form after it has closed", () => {
        const tail = FORM.slice(FORM.indexOf("await handle.result"));
        assert.ok(tail, "openLpoForm no longer awaits its dialog");
        const reads = tail.match(/\bg\(\s*["']lpoF[^"']*["']\s*\)/g) || [];
        assert.deepStrictEqual(reads, [],
            "these read the form after it is gone and come back empty: "
            + reads.join(", "));
        assert.ok(!/getElementById\(\s*["']lpoF/.test(tail),
            "same again, by id: a form field read after the close is null");
    });

    check("and the save itself does not happen after the close", () => {
        const tail = FORM.slice(FORM.indexOf("await handle.result"));
        assert.ok(!/save_lpo\(/.test(tail),
            "save_lpo is called after the dialog closed, so it is handed a "
            + "payload built from a form that is no longer there");
    });

    check("the client refusal can still be answered in place", () => {
        // ClientRequired writes the new name back into #lpoFClient and the form
        // saves again. That only works while the form is still mounted, which
        // is exactly what `keep: true` buys.
        assert.ok(/ClientRequired[\s\S]{0,200}offer\(/.test(FORM),
            "the quick-add offer is gone from the LPO form");
        const offerAt = FORM.indexOf("ClientRequired");
        const closeAt = FORM.indexOf("await handle.result");
        assert.ok(offerAt < closeAt,
            "the quick-add offer must run while the dialog is open, or it writes "
            + "the client's name into a field nobody will read");
    });

    console.log(failures ? `\n${failures} FAILED` : "\nall passed");
    process.exit(failures ? 1 : 0);
})();
