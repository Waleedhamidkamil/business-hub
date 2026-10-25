/* Copy and Print must hand over the SIGNED copy (2026-09-19).

   Reported by Waleed: sign and stamp a client LPO, press "Copy file", paste it
   into WhatsApp - and the client gets their own unsigned original back.

   `lpoDocAction` resolved three buttons three different ways:

       open   -> V ? V.path : lpo.file_path      // follows the viewer
       print  -> lpo.file_path                   // always the original
       copy   -> lpo.file_path                   // always the original

   So the one button whose whole purpose is sending the document to somebody
   else was the one that could not reach the acknowledged copy. The viewer has
   an Original / Signed copy switch and `openLpoDocFile` records what is on
   screen in `V.path`; copy and print simply never read it.

   The rule these tests pin, which is the rule `showLpoDoc` already follows
   (`lpo.ack_exists ? "ack" : "file"`):

     1. what is on screen, when the viewer is showing THIS lpo;
     2. else the signed copy, when one exists;
     3. the client's original only when nothing has been signed.

   This RUNS `lpoDocAction` against a DOM double - no jsdom in this repo. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "lpo.js"), "utf8");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

const FILE = "D:\\PO TOOL\\LPOs\\HDEC\\2026\\JUL\\CSSP-ADM-0010.pdf";
const ACK  = "D:\\PO TOOL\\LPOs\\HDEC\\2026\\JUL\\CSSP-ADM-0010.acknowledged.pdf";

function makeElement(tag = "div") {
    const el = {
        tagName: String(tag).toUpperCase(), children: [], dataset: {}, style: {},
        className: "", textContent: "", innerHTML: "", value: "",
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        appendChild(c) { this.children.push(c); return c; },
        remove() {}, addEventListener() {}, setAttribute() {},
        querySelector() { return null; }, querySelectorAll() { return []; },
    };
    return el;
}

/* Load lpo.js and hand back the copied / printed path for one press. */
function press(act, lpo, viewer) {
    const copied = [];
    const printed = [];
    const opened = [];
    const context = {
        console, setTimeout() {}, clearTimeout() {}, atob: (s) => s,
        Image: function () { return {}; }, FileReader: function () { return {}; },
        document: {
            readyState: "complete",
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: (t) => makeElement(t),
            addEventListener() {}, removeEventListener() {},
            body: makeElement("body"),
        },
        toast() {}, esc: (s) => String(s == null ? "" : s), safe: (fn) => fn,
        api: () => ({ reveal_in_folder() {} }),
        hideAllViews() {}, setBrand() {}, showModal: async () => "no",
        withLoading: async (b, fn) => fn(),
        state: { today: { display: "1/8/2026" } },
        copyFileToClipboard: (p) => { copied.push(p); },
        printPdfFile: (p) => { printed.push(p); },
        afterExportPdf: (p) => { opened.push(p); },
    };
    context.window = context;
    vm.createContext(context);
    vm.runInNewContext(SRC, context);

    context.lpoState.viewer = viewer || null;
    context.lpoDocAction(act, lpo);
    return { copied, printed, opened };
}

const SIGNED = {
    client_name: "HDEC", lpo_no: "CSSP-ADM-0010",
    file_exists: true, file_path: FILE, ack_exists: true, ack_path: ACK,
};
const UNSIGNED = {
    client_name: "HDEC", lpo_no: "CSSP-ADM-0017",
    file_exists: true, file_path: FILE, ack_exists: false, ack_path: null,
};

check("copying a signed LPO hands over the signed copy, not the original", () => {
    const { copied } = press("copy", SIGNED, {
        lpo: SIGNED, which: "ack", path: ACK, page: 1, pages: 1,
    });
    assert.deepStrictEqual(copied, [ACK],
        "this is the one button whose whole purpose is sending the document to "
        + "the client; it handed over their own unsigned original");
});

check("with the viewer switched back to the original, that is what copies", () => {
    const { copied } = press("copy", SIGNED, {
        lpo: SIGNED, which: "file", path: FILE, page: 1, pages: 1,
    });
    assert.deepStrictEqual(copied, [FILE],
        "the Original / Signed copy switch has to mean something - copy follows "
        + "what is on screen, exactly as Open file does");
});

check("with no viewer open, a signed LPO still copies the signed copy", () => {
    const { copied } = press("copy", SIGNED, null);
    assert.deepStrictEqual(copied, [ACK],
        "once a signed copy exists it is THE document; falling back to the "
        + "client's original is the bug, not the default");
});

check("an unsigned LPO copies the client's original", () => {
    const { copied } = press("copy", UNSIGNED, null);
    assert.deepStrictEqual(copied, [FILE],
        "there is nothing else to copy");
});

check("the viewer's path is ignored when it is showing a DIFFERENT LPO", () => {
    const other = { ...UNSIGNED, lpo_no: "CSSP-RENT-0004" };
    const { copied } = press("copy", SIGNED, {
        lpo: other, which: "file", path: "D:\\somewhere\\else.pdf", page: 1, pages: 1,
    });
    assert.deepStrictEqual(copied, [ACK],
        "copying one LPO must never hand over another one's file");
});

check("Open file follows the same rule, so there is only one of them", () => {
    const { opened } = press("open", SIGNED, null);
    assert.deepStrictEqual(opened, [ACK],
        "Open used to fall back to the original when no viewer was up. Three "
        + "buttons spelling one rule three ways is how they drifted apart.");
});

check("Print has the same rule - it was the same defect", () => {
    const { printed } = press("print", SIGNED, {
        lpo: SIGNED, which: "ack", path: ACK, page: 1, pages: 1,
    });
    assert.deepStrictEqual(printed, [ACK],
        "printing the unsigned original to hand to a client is the same mistake "
        + "as copying it");
    const bare = press("print", SIGNED, null);
    assert.deepStrictEqual(bare.printed, [ACK],
        "and with no viewer open, still the signed copy");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
