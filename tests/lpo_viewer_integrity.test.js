/* The LPO screen's element lookups can all resolve, and the signing viewer's
   contract with the backend still holds.

   `static/lpo.js` now owns four dialogs (add/edit, close, the picker's sibling,
   and the signing viewer) plus the list. Every one reaches into the DOM by id.
   A renamed id fails silently - the button simply does nothing - which is the
   exact failure `po_link_detail_renders.test.js` was written for on the PO
   popups. Same check, same reason.

   The signing viewer itself is deliberately untouched by the 2026-09-11 rebuild:
   fractions-of-the-page placement, the real signature drawn inside the box, the
   banner naming which file is on screen, and re-signing always restarting from
   the client's original were each fixed against a real failure. This file pins
   them so the redesign around them cannot quietly undo one. */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const read = (f) => fs.readFileSync(path.join(__dirname, "..", f), "utf8");
const SRC = read("static/lpo.js");
const HTML = read("templates/index.html");
const ACK = read("backend/lpo_ack.py");

let failures = 0;
function check(what, fn) {
    try { fn(); console.log("  ok   " + what); }
    catch (e) { failures++; console.log("  FAIL " + what + "\n       " + e.message); }
}

check("every element the module looks up can resolve", () => {
    const lookedUp = new Set([
        ...SRC.matchAll(/getElementById\(["']([\w-]+)["']\)/g),
        ...SRC.matchAll(/querySelector\(["']#([\w-]+)["']/g),
    ].map(m => m[1]));
    const made = new Set([...SRC.matchAll(/id=["']([\w-]+)["']/g)].map(m => m[1]));
    const inHtml = new Set([...HTML.matchAll(/id="([\w-]+)"/g)].map(m => m[1]));
    const missing = [...lookedUp].filter(id => !made.has(id) && !inHtml.has(id));
    assert.deepEqual(missing, [],
        "these ids are read but never exist, so whatever reads them does nothing: "
        + missing.join(", "));
});

check("every footer action the viewer binds is one the popup declares", () => {
    const bound = new Set([...SRC.matchAll(/\[data-value=['"]([\w-]+)['"]\]/g)].map(m => m[1]));
    const declared = new Set([...SRC.matchAll(/value:\s*"([\w-]+)"/g)].map(m => m[1]));
    const orphan = [...bound].filter(v => !declared.has(v));
    assert.deepEqual(orphan, [],
        "a handler is bound to a button that is never rendered: " + orphan.join(", "));
});

check("placements are fractions of the page, never pixels", () => {
    /* The whole reason zoom cannot corrupt a stamp. startLpoDrag divides by the
       rendered surface's own size; the backend multiplies straight back up. */
    assert.ok(/surface\.clientWidth/.test(SRC) && /surface\.clientHeight/.test(SRC),
        "the drag no longer measures against the rendered surface");
    assert.ok(/\(ev\.clientX - startX\) \/ W/.test(SRC),
        "the drag delta is not being divided by the surface width");
});

check("a mark can never be dragged off the page or resized to nothing", () => {
    assert.ok(/clamp\(from\.x \+ dx, 0, 1 - mark\.w\)/.test(SRC), "x is unclamped");
    assert.ok(/clamp\(from\.w \+ dx, 0\.0\d+, 1 - mark\.x\)/.test(SRC),
        "width has no floor, so a box could be resized to nothing and vanish");
});

check("the preview box and the merged overlay agree on how the image fits", () => {
    /* If these two disagree the preview lies about the result - you place it,
       it lands somewhere else. Both must use `contain`. */
    const css = read("static/app_styles.css");
    const markCss = css.slice(css.indexOf(".lpo-mark-img"));
    assert.ok(css.includes(".lpo-mark-img"), "the viewer's mark image rule is gone");
    assert.ok(/object-fit:\s*contain/.test(markCss.slice(0, 400)),
        "the viewer's mark image no longer uses object-fit: contain");
    /* Scoped to _overlay_html: `object-fit:contain` appears twice in this file
       and the other one wraps an IMAGE LPO onto A4, so an unscoped search
       cannot tell that the overlay lost it. */
    const i = ACK.indexOf("def _overlay_html");
    const j = ACK.indexOf("\ndef ", i + 5);
    assert.ok(i > 0, "_overlay_html is gone");
    assert.ok(/object-fit:contain/.test(ACK.slice(i, j)),
        "lpo_ack._overlay_html no longer uses object-fit: contain, so the "
        + "preview now lies about where the stamp will land");
});

check("re-signing always restarts from the client's original", () => {
    /* Signing the acknowledged copy would stack a second stamp on the first.
       The popup became the lpoDocView screen on 2026-09-11; the invariant is
       unchanged, so this reads it in its new home. */
    const at = SRC.indexOf("function startLpoSigning");
    assert.ok(at > 0, "startLpoSigning is gone");
    assert.ok(/openLpoDocFile\(lpo, "file"\)/.test(SRC.slice(at, at + 900)),
        "Sign again must reload the ORIGINAL, not the signed copy");
    assert.ok(/from the client's original|from the ORIGINAL|stack a second/i.test(SRC),
        "the reason is no longer written down where the next reader will see it");
});

check("the document screen is where signing happens", () => {
    /* One screen, not a popup over a screen: the document is already on the
       left at 70%, which is the whole reason for that split. */
    assert.ok(/lpoDocView/.test(HTML), "the document screen is missing from the markup");
    assert.ok(/showLpoDoc/.test(SRC), "nothing opens it");
    assert.ok(!/Popup\.open\([^)]*lpo-viewer/.test(SRC),
        "the old full-screen viewer popup is back");
});

check("the panel is read-only, with Edit opening the form", () => {
    /* Waleed's call: no half-saved state in a panel you can click away from. */
    const at = SRC.indexOf("function renderLpoDocSide");
    assert.ok(at > 0, "the panel renderer is gone");
    const body = SRC.slice(at, at + 5200);
    assert.ok(/data-doc="edit"/.test(body), "there is no way to edit the record");
    assert.ok(!/<input/.test(body),
        "the panel has grown input fields - it is meant to be read-only");
});

check("the viewer says which file is on screen", () => {
    /* Without this a perfectly successful acknowledge looked like a no-op: you
       signed, reopened, and saw the untouched original. */
    assert.ok(/lpo-v-file/.test(SRC), "the file banner is gone");
    assert.ok(/acknowledged copy/i.test(SRC) && /client's original/i.test(SRC),
        "the banner no longer distinguishes the two files");
});

check("it refuses to sign with no signature or stamp uploaded", () => {
    assert.ok(/has_sig/.test(SRC) && /has_stamp/.test(SRC),
        "nothing checks the assets exist before opening the placement tools");
});

check("a partial result is never reported as success", () => {
    /* A mark can be dropped for a missing asset or a page past the end. Saying
       'done' is the failure you find out about when the client asks. */
    assert.ok(/applied < asked/.test(SRC),
        "the applied/requested counts are not compared");
    assert.ok(/only \$\{applied\} of \$\{asked\}/.test(SRC),
        "a partial apply does not name the numbers");
});

check("the backend never modifies the client's own file", () => {
    assert.ok(/dest\.parent|with_name/.test(ACK));
    assert.ok(/never modified|never edited|original file is never/i.test(ACK + SRC),
        "the rule is no longer stated where the next reader will find it");
});

check("every toolbar icon is sized on the tag", () => {
    /* An inline <svg> with no width/height falls back to its intrinsic size,
       roughly 300px. That is exactly how the Signature and Stamp buttons came
       out as a giant squiggle and circle overlapping their own labels
       (reported 2026-09-12). Leaving it to CSS is not enough - the rule only
       covered the icon-only buttons. */
    /* Two steps rather than one lookahead: `stroke-width="2"` contains the
       substring `width=`, so a naive negative lookahead reads every icon as
       already sized. Match the tags, then test each for a REAL width
       attribute, one preceded by whitespace. */
    const bad = [...SRC.matchAll(/<svg[^>]*>/g)]
        .map(m => m[0])
        .filter(tag => !/\swidth=/.test(tag));
    assert.deepEqual(bad, [],
        "these inline SVGs have no width and will render at ~300px: "
        + bad.join(" | "));
});

check("the signature images are fetched once, not on every open", () => {
    /* signing_assets() is a 679 KB payload of base64 PNGs. Re-fetching it on
       every document open made one click cost ~1.5 MB across the bridge, two
       thirds of it images that had not changed - reported as "slow". */
    const hits = (SRC.match(/api\(\)\.signing_assets\(\)/g) || []).length;
    assert.strictEqual(hits, 1,
        "signing_assets is called from " + hits + " places; it should be one, "
        + "behind the lpoAssets() cache");
    assert.ok(/function lpoAssets\(\)/.test(SRC), "the cache is gone");
    assert.ok(/if \(lpoState\.assets\) return lpoState\.assets;/.test(SRC),
        "lpoAssets no longer returns the cached copy");
});

check("a new signature in Settings drops the cached copy", () => {
    /* Or the app would keep stamping documents with the old image. */
    assert.ok(/window\.clearLpoAssets/.test(SRC), "nothing can clear the cache");
    const SIGN = read("static/signing.js");
    const hits = (SIGN.match(/clearLpoAssets/g) || []).length;
    assert.ok(hits >= 2,
        "both the stamp and signature uploads must clear it; found " + hits);
});

check("viewing a document does not fetch the signature images at all", () => {
    const at = SRC.indexOf("async function openLpoDocFile");
    assert.ok(at > 0, "openLpoDocFile is gone");
    /* Ends at openLpoDocFile's own closing brace. Two traps here, both hit
       while writing this: naming the next function as the boundary breaks the
       moment anything is declared between them, and these files are CRLF - so
       a literal "\n}\n" never matches, indexOf returns -1, and the slice
       silently becomes the whole file. */
    const rest = SRC.slice(at);
    const close = rest.search(/\r?\n\}\r?\n/);
    assert.ok(close > 0, "could not find the end of openLpoDocFile");
    const body = rest.slice(0, close);
    assert.ok(!/lpoAssets\(\)|signing_assets/.test(body),
        "opening a document pulls the images it does not need");
    const signing = SRC.indexOf("async function startLpoSigning");
    assert.ok(/await lpoAssets\(\)/.test(SRC.slice(signing, signing + 900)),
        "signing must load them, since that is what needs them");
});

check("editing the header does not re-download the PDF", () => {
    /* 819 KB and a visible reload, for a change that does not touch the file. */
    const at = SRC.indexOf("window.showLpoDoc");
    const body = SRC.slice(at, at + 2200);
    assert.ok(/file_path/.test(body) && /ack_path/.test(body),
        "the reuse test must key on BOTH paths - signing writes ack_path and "
        + "attaching replaces file_path, and either has to reload");
    assert.ok(/renderLpoDocToolbar\(lpo, V\.which\)/.test(body),
        "reusing the document must still repaint the toolbar for the new record");
});

check("pdf.js is the classic build, vendored, with a worker beside it", () => {
    /* The app loads over file://, where Chromium blocks ES-module scripts - an
       .mjs build silently fails to load. */
    for (const f of ["static/lib/pdf.min.js", "static/lib/pdf.worker.min.js"]) {
        assert.ok(fs.existsSync(path.join(__dirname, "..", f)), `${f} is missing`);
    }
    assert.ok(/pdf\.worker\.min\.js/.test(SRC), "the worker path is not set");
    assert.ok(!/pdf\.min\.mjs|pdf\.worker\.min\.mjs/.test(SRC + HTML),
        "an .mjs build cannot load over file://");
});

check("every page is rendered, and the stage scrolls through them", () => {
    /* 2026-09-12 - it painted one page and swapped it on Previous/Next, so a
       two-page PO could only be read a page at a time and the second sheet was
       easy to miss. */
    const at = SRC.indexOf("async function renderLpoPage");
    const body = SRC.slice(at, SRC.indexOf("function watchLpoScroll", at));
    assert.ok(/for \(let n = 1; n <= V\.pages; n\+\+\)/.test(body),
        "only one page is rendered, so the rest cannot be scrolled to");
    assert.ok(/scrollLpoToPage/.test(SRC) && /stage\.scrollTo/.test(SRC),
        "Previous/Next no longer move the stage");
    assert.ok(!/v\.page--; await renderLpoPage/.test(SRC),
        "Previous still re-renders a single page instead of scrolling");
    const css = read("static/app_styles.css");
    assert.ok(/\.lpo-v-stage\s*\{[^}]*overflow:\s*auto/.test(css),
        "the stage does not scroll, so the stacked pages are cut off");
});

check("a mark is painted inside the page it belongs to", () => {
    /* With every page on screen at once, appending a mark to the whole stack
       would position page 2's stamp against page 1's canvas. */
    const at = SRC.indexOf("function paintLpoMarks");
    const body = SRC.slice(at, SRC.indexOf("function updateLpoMarkCount", at));
    assert.ok(/V\.pageEls\[m\.page\]/.test(body), "the mark's own page is never looked up");
    assert.ok(/host\.appendChild\(el\)/.test(body), "the mark is not put on its page");
    assert.ok(/host\.querySelector\("canvas, img"\)/.test(body),
        "the mark is still measured against whatever canvas comes first");
    assert.ok(/lpoPageMm\(mark\.page\)/.test(SRC),
        "the mm readout reads the page in VIEW, not the page the mark is on");
});

check("one LPO can be opened by number from outside this module", () => {
    /* The To-Do's billing reminder knows a client and a PO number and nothing
       else, and it used to land on the whole list (2026-09-12). */
    assert.ok(/window\.openLpoByNo\s*=/.test(SRC), "there is no way in by number");
    const i = SRC.indexOf("window.openLpoByNo");
    /* Bounded by the NEXT function: `refreshLpos` sits right below and also
       calls list_lpos, so a fixed-length slice credits it for that. */
    const body = SRC.slice(i, SRC.indexOf("async function refreshLpos", i));
    assert.ok(/list_lpos/.test(body),
        "opened cold from the To-Do the list is empty, so nothing would be found");
    assert.ok(/showLpoDoc\(row\)/.test(body), "it never reaches the document screen");
    // Backend lookup resolves aliases and refuses another client's number.
    assert.ok(/get_lpo\(clientName, lpoNo\)/.test(body)
        && /r.client_name === resolved.client_name/.test(body),
        "matching on the number alone can open another client's PO");
});

check("the way out of the document screen sits on the right", () => {
    /* Waleed, 2026-09-12: it is a back button, so it belongs with the other
       navigation at the end of the bar, not in front of the PO number. */
    const head = HTML.slice(HTML.indexOf('id="lpoDocView"'),
                            HTML.indexOf("lpo-doc-split"));
    assert.ok(head.indexOf('id="lpoDocNo"') < head.indexOf('id="btnLpoDocBack"'),
        "All LPOs is still ahead of the PO number");
    const acts = head.slice(head.indexOf("lpo-head-actions"));
    assert.ok(/btnLpoDocBack/.test(acts), "the back button is not in the right-hand group");
});

console.log(failures ? `\n${failures} FAILED` : "\nall passed");
process.exit(failures ? 1 : 0);
