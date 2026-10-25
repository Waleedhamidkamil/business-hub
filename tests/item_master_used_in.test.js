/* "Used in" - the documents holding an item, in the Master Data panel
   (2026-08-14).

   The panel listed "Stored rows", but those are CATALOGS ("Quotation
   Materials", "Delivery Note"), not documents: it could say an item was
   remembered without ever saying where it came from.

   Three things matter and are checked by EXECUTING the real functions:
     1. each document is a real <button> - reachable by keyboard, announced as
        an action, not a clickable <div>;
     2. clicking shows the popup FIRST and only then offers to leave, because
        opening a document abandons the panel being worked in;
     3. the popup's Open button routes to the right editor per document kind.
*/
const fs = require("fs");

function assert(c, m) { if (!c) throw new Error(m); }
let checks = 0;
function ok(c, m) { assert(c, m); checks++; }

const src = fs.readFileSync("static/item_master.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");

/* ---------- 1. the rows are buttons, not divs ---------- */
const rowFn = src.slice(src.indexOf("function usedInRowHtml("),
                        src.indexOf("async function loadUsedIn("));
ok(/<button type="button" class="im-doc"/.test(rowFn),
   "each document must be a real button - a clickable div is invisible to the "
   + "keyboard and to a screen reader");
ok(/data-doc-kind=/.test(rowFn) && /data-doc-no=/.test(rowFn),
   "the row must carry which document it is");
ok(/\.im-doc:focus-visible/.test(css), "…and must show a focus ring");

/* ---------- 2. it does NOT reuse the cost-target bridge ---------- */
const loadFn = src.slice(src.indexOf("async function loadUsedIn("),
                         src.indexOf("async function showDocumentPeek("));
ok(/im_item_documents/.test(loadFn),
   "it must call im_item_documents");
ok(!/im_cost_targets/.test(loadFn),
   "im_cost_targets refuses an item with no usable cost - most of the catalog "
   + "- so reusing it would leave those items unable to answer at all");

/* ---------- 3. a stale panel does not overwrite a newer one ---------- */
ok(/host\.dataset\.item !== name/.test(loadFn),
   "the lookup is async; if the user opens another item first, the late reply "
   + "must not paint into the new panel");

/* ---------- 4. the popup comes first, opening is a second step ---------- */
const peekFn = src.slice(src.indexOf("async function showDocumentPeek("),
                         src.indexOf("async function showCostTargets("));
ok(/showModal/.test(peekFn), "it must show the popup");
ok(/if \(choice !== "open"\) return;/.test(peekFn),
   "nothing may be opened unless the user asks for it - the popup answers the "
   + "common question in place");
ok(/label: "Close"/.test(peekFn) && /label: "Open document"/.test(peekFn),
   "both a way out and a way on");

/* ---------- 5. every document kind routes somewhere ---------- */
const routes = src.slice(src.indexOf("const DOC_ROUTE = {"),
                         src.indexOf("function usedInRowHtml("));
["po", "quote", "dn", "inv"].forEach((k) => {
  ok(new RegExp(`${k}:\\s*\\(\\) =>`).test(routes),
     `no editor route for a ${k} document`);
});
ok(/loadQuoteForEdit/.test(routes) && /loadDnForEdit/.test(routes)
   && /loadInvForEdit/.test(routes) && /loadForEdit/.test(routes),
   "the routes must point at the real editor loaders");

/* ---------- 6. the Open button is hidden when nothing can open it ---------- */
ok(/canOpen/.test(peekFn) && /if \(canOpen\) buttons\.push/.test(peekFn),
   "offering to open a document the app cannot route to is a dead end");

/* ---------- 7. EXECUTE the row renderer ---------- */
const fn = new Function("esc", "num",
  rowFn + "\nreturn usedInRowHtml;")((s) => String(s == null ? "" : s),
                                     (n) => String(n));
const html = fn({ kind: "quote", kind_label: "Quotation", no: "QUT-MT-HDEC-025",
                  date: "5/7/2026", client: "HDEC", line_count: 3,
                  submitted: true, _i: 0 });
ok(html.includes("QUT-MT-HDEC-025"), "the document number must be shown");
ok(html.includes("Quotation"), "and its type");
ok(html.includes("HDEC"), "and the client, which is how a document is recognised");
ok(/3 lines/.test(html),
   "when an item appears several times, say so: " + html);
ok(/submitted/.test(html),
   "a submitted document must be flagged - editing one has consequences");

const plain = fn({ kind: "po", kind_label: "Purchase Order", no: "A-61",
                   date: "8/8/2026", client: "", line_count: 1, _i: 1 });
ok(!/lines/.test(plain), "a single line needs no count: " + plain);
ok(!/submitted/.test(plain), "and an unsubmitted document gets no flag");

/* ---------- 8. it is wired into the panel ---------- */
ok(/id="imUsedIn"/.test(src), "the panel needs a host element");
/* The record has name_en / name_ar, not a single `name` - documents store one
   NAME, so the panel asks with whichever the documents would have used. */
ok(/loadUsedIn\(it\.name_en \|\| it\.name_ar\)/.test(src),
   "…and must actually load into it");
ok(/<h4 class="im-d-sub">Used in<\/h4>/.test(src), "…under a heading");

console.log("item_master_used_in: OK (" + checks + " checks)");
