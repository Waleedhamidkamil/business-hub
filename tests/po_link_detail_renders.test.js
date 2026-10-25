/* No popup may read an element that nothing creates.

   2026-09-06: linking a PO to an invoice (either direction) showed "Loading
   items…" forever. `po_link.js`'s `openDetail` read `#poDetMeta` - an element
   its own hand-rolled header used to build, deleted when the Popup migration
   replaced that header with `Popup.open`'s title/subtitle. One of the two reads
   was left unguarded, so the async function threw AFTER painting the loading
   text: an unhandled rejection nobody sees, and a spinner that never stops.

   That failure is silent by construction, which is why it survived. It is also
   statically checkable, so it is checked here for EVERY front-end module: an id
   looked up with querySelector("#…") must be one the app can actually produce -
   in index.html, or by the module itself. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync('templates/index.html', 'utf8');
const inHtml = new Set([...html.matchAll(/id="([A-Za-z0-9_-]+)"/g)].map(m => m[1]));

const ids = (text, re) => new Set([...text.matchAll(re)].map(m => m[1]));
const problems = [];
for (const file of fs.readdirSync('static').filter(f => f.endsWith('.js')).sort()) {
    const source = fs.readFileSync(path.join('static', file), 'utf8');
    const queried = new Set([
        ...ids(source, /querySelector\(\s*"#([A-Za-z0-9_-]+)"/g),
        ...ids(source, /querySelector\(\s*'#([A-Za-z0-9_-]+)'/g),
    ]);
    if (!queried.size) continue;
    const created = new Set([
        ...ids(source, /bodyId:\s*"([A-Za-z0-9_-]+)"/g),
        ...ids(source, /id="([A-Za-z0-9_-]+)"/g),
        ...ids(source, /id='([A-Za-z0-9_-]+)'/g),
        ...ids(source, /\.id\s*=\s*["'`]([A-Za-z0-9_-]+)["'`]/g),
    ]);
    for (const id of queried) {
        if (!created.has(id) && !inHtml.has(id)) problems.push(`${file} looks up #${id}`);
    }
}
assert.deepEqual(problems, [],
    'these lookups can only return null, and a null dereference after an await ' +
    'leaves the popup stuck on its loading text:\n  ' + problems.join('\n  '));

/* The specific repair, so it cannot silently regress into a different dropped
   element: the detail popup's count line is the POPUP'S subtitle now. */
const poLink = fs.readFileSync('static/po_link.js', 'utf8');
assert.doesNotMatch(poLink, /poDetMeta"/,
    'the count line moved into Popup.setSubtitle when the header was migrated');
assert.match(poLink, /h\.setSubtitle\(ref\s*$/m,
    'openDetail must publish its count line through the popup handle');
assert.match(poLink, /h\.setSubtitle\("-"\)/,
    'a PO with no items still has to replace "Loading…" with something');

console.log('PO link popups: every element lookup can resolve; detail count line uses setSubtitle');
