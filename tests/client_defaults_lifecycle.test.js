/* Client settings apply when a client is PICKED, and never repaint a document.

   The whole safety story rests on one fact: the trigger is the client field's
   `change` event, which the browser fires for a user's choice and NOT for a
   programmatic assignment. Loading, restoring, duplicating and importing all
   assign in code, so they cannot trigger it. Everything below pins that, plus
   the per-field rule that only an empty field or one this module itself wrote
   is ever replaced. No real bridge or database is used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/client_defaults.js', 'utf8');
const files = Object.fromEntries(['quote','dn','inv'].map(k => [k, fs.readFileSync(`static/${k}.js`,'utf8')]));
const body = (src, name) => {
    const match = src.match(new RegExp(`(?:async )?function ${name}\\([^\\n]*\\)[^\\n]*\\{[\\s\\S]*?^\\}`, 'm'));
    assert.ok(match, `Missing function ${name}`); return match[0];
};

// ---- the button is gone -------------------------------------------------
assert.doesNotMatch(source, /cmApplyDefaults|cm-defaults-button|cm-defaults-control/,
    'the Apply-defaults button was replaced by applying on selection');
assert.doesNotMatch(fs.readFileSync('static/client_master.css','utf8'), /cm-defaults-(button|control)/,
    'the button styles must go with the button');
assert.match(source, /addEventListener\('change'/,
    'client settings apply on the client field\'s change event');
assert.doesNotMatch(source, /addEventListener\('input'\s*,\s*\(\)\s*=>\s*applyFor/,
    '`input` fires per keystroke and would apply on a half-typed name');

// ---- the chip has somewhere to live, in all three editors --------------
{
    const page = fs.readFileSync('templates/index.html', 'utf8');
    const styles = fs.readFileSync('static/app_styles.css', 'utf8');
    for (const id of ['qClientCode', 'dnClientCode', 'invClientCode']) {
        assert.match(page, new RegExp(`<span class="doc-client-code" id="${id}" hidden></span>`),
            `${id} is missing - that editor would resolve the client and have `
            + 'nowhere to show which one it resolved to');
    }
    // It sits in the label, whose row is a fixed 14px. Without the flex rule it
    // is a second line and the field row grows.
    assert.match(styles, /\.doc-info-field > label\.doc-label-with-code \{[^}]*display:\s*flex/,
        'the label holding a chip must be a flex row or the chip adds a line');
    assert.match(styles, /\.doc-client-code \{[^}]*color:\s*var\(--brand\)/,
        'the chip must take its colour from --brand, which dark.css redefines');
}

// ---- every path that REPLACES the document forgets what we filled -------
for (const [file, kind, names] of [
    ['quote','qut',['initNewQuote','loadQuoteForEdit','restoreQuoteDraft','quoteMakeDuplicate']],
    ['dn','dn',['initNewDn','loadDnForEdit','dnMakeDuplicate','importQuoteIntoDN']],
    ['inv','inv',['initNewInv','loadInvForEdit','restoreInvDraft','invMakeDuplicate',
                  'importQuoteIntoInv','importDnIntoInv']],
]) {
    for (const name of names) {
        assert.ok(body(files[file], name).includes(`window.ClientDefaults.reset("${kind}")`),
            `${name} must reset the client-settings memory for the new document`);
    }
}
// Paths that build on a fresh document inherit initNew*'s reset; they must not
// be relied on to repeat it, but they must not be missing it either.
for (const [file, kind, name] of [['dn','dn','createDnFromPo'], ['inv','inv','createInvoiceFromPo'],
                                  ['inv','inv','createInvoiceFromDnNo'], ['inv','inv','createInvoiceFromDnAsLine']]) {
    const code = body(files[file], name);
    assert.match(code, /await initNew\w+\(\);/, `${name} builds on a fresh document`);
    assert.ok(!code.includes(`ClientDefaults.reset("${kind}")`),
        `${name} already resets via initNew - the duplicate call is noise`);
}

function harness(defaults, docs = {}) {
    const made = [];
    const el = (id, extra = {}) => ({id, value:'', textContent:'', options:[{value:'adib'},{value:'cash'},{value:'bob'}],
        dispatchEvent() {}, addEventListener(type, fn) { this['on'+type] = fn; }, ...extra});
    const nodes = Object.fromEntries(['qClient','qAddress','dnClient','dnAddress','invClient','invAddress',
        'invBank','invTaxStatusCell'].map(id => [id, el(id)]));
    for (const id of ['qClientCode','dnClientCode','invClientCode'])
        nodes[id] = el(id, {hidden:true, title:''});
    const q = {mode:'new', rows:[], ...(docs.q || {})};
    const dn = {mode:'new', rows:[], ...(docs.dn || {})};
    const inv = {mode:'new', rows:[], sellCurrency:'USD', withholdingIncluded:true, ...(docs.inv || {})};
    const toasts = [];
    const context = {console, setTimeout, Event: class { constructor(type) { this.type = type; } },
        quoteState:q, dnState:dn, invState:inv,
        setInvSellCurrency: async (value, live) => { if (!live || live()) inv.sellCurrency = value; },
        updateInvTotals() {},
        document: {readyState:'complete', getElementById: id => nodes[id] || null,
            addEventListener(type, fn) { (this._doc = this._doc || {})[type] = fn; }},
        window: {toast: (t, bad) => toasts.push([t, !!bad]),
            pywebview: {api: {cm_defaults: async name => { made.push(name); return defaults(name); }}}}};
    vm.runInNewContext(source, context);
    for (const kind of ['qut', 'dn', 'inv']) context.window.ClientDefaults.reset(kind);
    return {cd: context.window.ClientDefaults, context, nodes, q, dn, inv, toasts, made,
        reset(kind) { context.window.ClientDefaults.reset(kind); },
        type(id, value) { nodes[id].value = value; context.document._doc.input({target: nodes[id]}); },
        toggleCurrency() { context.document._doc.click({target: {closest: sel => sel === '#invSellCurSwitch' ? {} : null}}); },
        pick(kind, name) { const map = {qut:'qClient', dn:'dnClient', inv:'invClient'};
            nodes[map[kind]].value = name; return nodes[map[kind]].onchange(); },
        // reset() schedules the chip read for the next task, and that read then
        // awaits the bridge. Two turns of the clock covers both.
        async settle() { await new Promise(r => setTimeout(r, 0)); await new Promise(r => setTimeout(r, 0)); }};
}

const HDEC = () => ({ok:true, known:true, name:'HDEC', address:'HDEC Camp, Basra',
    payment_bank_key:'adib', currency:'IQD', withholding:false});

async function main() {
    // Picking a client fills a blank document.
    let h = harness(HDEC);
    await h.pick('inv', 'HDEC');
    assert.equal(h.nodes.invAddress.value, 'HDEC Camp, Basra', 'address applies on selection');
    assert.equal(h.nodes.invBank.value, 'adib');
    assert.equal(h.inv.withholdingIncluded, false);
    assert.equal(h.inv.dirty, true, 'applying settings makes the document unsaved');
    assert.match(h.toasts[0][0], /HDEC/);
    // Currency is REPORTED, never imposed: changing it needs an exchange rate
    // only the user can confirm, and a rate dialog triggered by picking a name
    // is an interruption, not a convenience.
    assert.equal(h.inv.sellCurrency, 'USD', 'picking a client never converts the invoice');
    assert.match(h.toasts[0][0], /usually billed in IQD/,
        'the preferred currency is surfaced so the user can choose it');

    // What the user typed is never overwritten.
    h = harness(HDEC);
    h.type('invAddress', 'Site office, Umm Qasr');
    await h.pick('inv', 'HDEC');
    assert.equal(h.nodes.invAddress.value, 'Site office, Umm Qasr', 'a typed address survives');

    // Switching client DOES replace the previous client's auto-filled address.
    h = harness(name => name === 'HDEC' ? HDEC()
        : {ok:true, known:true, name:'TDC', address:'TDC Yard', payment_bank_key:'cash'});
    await h.pick('qut', 'HDEC');
    assert.equal(h.nodes.qAddress.value, 'HDEC Camp, Basra');
    await h.pick('qut', 'TDC');
    assert.equal(h.nodes.qAddress.value, 'TDC Yard', 'the previous client\'s address must not linger');

    // A SAVED document's own values are never ours, even on a deliberate
    // re-pick: what is on that quotation is what the client already received.
    h = harness(HDEC, {q:{mode:'edit'}});
    h.nodes.qAddress.value = 'Address as it was issued';
    h.cd.reset('qut');                       // loadQuoteForEdit does exactly this
    await h.pick('qut', 'HDEC');
    assert.equal(h.nodes.qAddress.value, 'Address as it was issued',
        'a loaded document keeps the address it was saved with');
    // ...but a blank field on that same loaded document is still filled.
    h = harness(HDEC, {q:{mode:'edit'}});
    h.cd.reset('qut');
    await h.pick('qut', 'HDEC');
    assert.equal(h.nodes.qAddress.value, 'HDEC Camp, Basra',
        'an empty field is safe to fill on any document');

    // An imported/derived document is not fresh either, even with mode "new".
    h = harness(HDEC, {inv:{mode:'new', sourceQuote:'QUT-MT-HDEC-058'}});
    h.nodes.invAddress.value = 'From the quotation';
    h.cd.reset('inv');
    await h.pick('inv', 'HDEC');
    assert.equal(h.nodes.invAddress.value, 'From the quotation',
        'a document imported from another one keeps the values it inherited');

    // An unknown name is ordinary typing: no fill, no complaint.
    h = harness(() => ({ok:false, known:false, error:'Client not found.'}));
    await h.pick('dn', 'Brand New Customer');
    assert.equal(h.nodes.dnAddress.value, '');
    assert.deepEqual(h.toasts, [], 'typing a new client name must not raise an error');

    // A KNOWN client whose settings cannot be applied is worth saying out loud.
    h = harness(() => ({ok:false, known:true, error:'The preferred receiving account is missing from this company profile.'}));
    await h.pick('inv', 'HDEC');
    assert.equal(h.toasts.length, 1);
    assert.equal(h.toasts[0][1], true, 'a real misconfiguration is reported as an error');

    // Once the user has set the currency by hand, not even the note is shown:
    // they have already answered the question it would ask.
    h = harness(HDEC);
    h.toggleCurrency();
    await h.pick('inv', 'HDEC');
    assert.equal(h.inv.sellCurrency, 'USD');
    assert.doesNotMatch(h.toasts[0][0], /usually billed/,
        'a hand-set currency is a decision already made');
    assert.equal(h.nodes.invBank.value, 'adib', 'the other fields still apply');

    // A client with nothing to fill but a different currency still says so.
    h = harness(() => ({ok:true, known:true, name:'HDEC', address:'', payment_bank_key:'', currency:'IQD'}));
    await h.pick('inv', 'HDEC');
    assert.match(h.toasts[0][0], /usually billed in IQD/);
    assert.notEqual(h.inv.dirty, true, 'a note alone does not make the document unsaved');

    // A lookup that resolves after the user moved on cannot land.
    let release;
    h = harness(name => name === 'SLOW' ? new Promise(r => {release = r;}) : HDEC());
    const slow = h.pick('qut', 'SLOW');
    h.nodes.qClient.value = 'Someone Else';
    release({ok:true, known:true, name:'SLOW', address:'Stale address'});
    await slow;
    assert.equal(h.nodes.qAddress.value, '', 'a stale answer cannot fill the newer selection');

    // ...and neither can one that resolves after the document was replaced.
    h = harness(name => name === 'SLOW' ? new Promise(r => {release = r;}) : HDEC());
    const swapped = h.pick('dn', 'SLOW');
    h.cd.reset('dn');
    release({ok:true, known:true, name:'SLOW', address:'Stale address'});
    await swapped;
    assert.equal(h.nodes.dnAddress.value, '', 'a document swap invalidates a pending lookup');

    // Nothing to do means nothing to say.
    h = harness(() => ({ok:true, known:true, name:'HDEC', address:'', payment_bank_key:''}));
    await h.pick('qut', 'HDEC');
    assert.deepEqual(h.toasts, [], 'a client with no saved settings applies quietly');
    assert.notEqual(h.q.dirty, true, 'and does not mark the document unsaved');

    // ---- the printed name and the code chip (2026-09-11) -----------------
    // One client, four spellings. Whichever is picked, the document prints the
    // LEGAL name and the chip names the client the number is built from.
    const TDC = () => ({ok:true, known:true, name:'TDC', code:'TDC',
        print_name:'China Communications construction company Ltd. Iraq Branch',
        address:'TDC Yard', payment_bank_key:'cash'});

    for (const typed of ['TDC', 'CCCC ltd', 'هيونداي']) {
        h = harness(TDC);
        await h.pick('inv', typed);
        assert.equal(h.nodes.invClient.value,
            'China Communications construction company Ltd. Iraq Branch',
            `picking "${typed}" must print the client's legal name`);
        assert.equal(h.nodes.invClientCode.textContent, 'TDC',
            `picking "${typed}" must show the code the document is numbered under`);
        assert.equal(h.nodes.invClientCode.hidden, false, 'the chip must be visible');
        assert.match(h.toasts[0][0], /printed name/, 'a name changing itself is worth saying');
    }

    // Picking the same client again changes nothing: the box already holds the
    // legal name, so there is no second rename and nothing to report.
    h = harness(TDC);
    await h.pick('qut', 'CCCC ltd');
    h.toasts.length = 0;
    await h.pick('qut', h.nodes.qClient.value);
    assert.equal(h.nodes.qClient.value,
        'China Communications construction company Ltd. Iraq Branch');
    assert.ok(!h.toasts.some(t => /printed name/.test(t[0])),
        'the legal name is already in the box - re-picking must not re-announce it');

    // A client with a code but no legal name keeps its own name on the paper.
    h = harness(() => ({ok:true, known:true, name:'Gusan', code:'Gusan',
        print_name:'Gusan', address:'', payment_bank_key:''}));
    await h.pick('dn', 'Gusan');
    assert.equal(h.nodes.dnClient.value, 'Gusan');
    assert.equal(h.nodes.dnClientCode.textContent, 'Gusan',
        'a client with nothing to fill still shows its code');

    // A name that is NOT on file is left exactly as typed - that is the escape
    // hatch for a one-off spelling - and it carries no code.
    h = harness(() => ({ok:false, known:false, error:'Client not found.'}));
    h.nodes.invClientCode.textContent = 'TDC'; h.nodes.invClientCode.hidden = false;
    await h.pick('inv', 'TDC Iraq, site office');
    assert.equal(h.nodes.invClient.value, 'TDC Iraq, site office',
        'an unknown name must survive untouched');
    assert.equal(h.nodes.invClientCode.textContent, '',
        "the previous client code must not linger on an unknown one");
    assert.equal(h.nodes.invClientCode.hidden, true);

    // A LOADED document gets its chip without a `change` event and without any
    // field being touched: that is what the reset-time read is for.
    h = harness(TDC, {inv:{mode:'edit'}});
    h.nodes.invAddress.value = 'Address as it was issued';
    h.cd.reset('inv');                                  // loadInvForEdit does this...
    h.nodes.invClient.value = 'CCCC ltd';               // ...then assigns, in code
    await h.settle();
    assert.equal(h.nodes.invClientCode.textContent, 'TDC',
        'a loaded document must still show which client it belongs to');
    assert.equal(h.nodes.invClient.value, 'CCCC ltd',
        "reading the chip must never rewrite a saved document client name");
    assert.equal(h.nodes.invAddress.value, 'Address as it was issued',
        "and must never touch the fields of a saved document");
    assert.deepEqual(h.toasts, [], 'nor say anything - nothing was applied');

    // Swapping documents clears the chip at once, so the old client's code is
    // never left sitting over the new document.
    h = harness(TDC);
    await h.pick('inv', 'CCCC ltd');
    assert.equal(h.nodes.invClientCode.textContent, 'TDC');
    h.nodes.invClient.value = '';
    h.cd.reset('inv');
    assert.equal(h.nodes.invClientCode.textContent, '', 'a new document has no client yet');
    assert.equal(h.nodes.invClientCode.hidden, true);

    // ---- a document built from a PO (2026-09-11) -------------------------
    // The PO carries the company as it was typed for the ITEMS - often Arabic,
    // often an alias - and assigns it in code, so no `change` fires. `adopt` is
    // how that document gets resolved.
    h = harness(TDC, {inv:{mode:'new', sourcePo:'PO-2026-123'}});
    h.nodes.invBank.value = 'adib';          // initNewInv sets the profile default...
    h.cd.reset('inv');                       // ...and resets after, so that IS the baseline
    h.nodes.invClient.value = 'هيونداي';     // then the PO's company lands, in code
    await h.cd.adopt('inv');
    assert.equal(h.nodes.invClient.value,
        'China Communications construction company Ltd. Iraq Branch',
        'a PO client must be resolved to the printed name');
    assert.equal(h.nodes.invClientCode.textContent, 'TDC');
    assert.equal(h.nodes.invBank.value, 'cash',
        "the client's saved payment method is the whole point - a PO-made "
        + 'invoice must not keep the profile default');
    assert.equal(h.nodes.invAddress.value, 'TDC Yard');

    // Plain `apply` on that same document does NOT fill those fields: a source
    // document normally supplies its own address and terms, and only the caller
    // knows a PO supplies neither. This is the contrast that makes `adopt` mean
    // something.
    h = harness(TDC, {inv:{mode:'new', sourcePo:'PO-2026-123'}});
    h.nodes.invBank.value = 'adib';
    h.cd.reset('inv');
    h.nodes.invClient.value = 'هيونداي';
    await h.cd.apply('inv');
    assert.equal(h.nodes.invBank.value, 'adib',
        'without adopt, a derived document keeps the values it was given');

    // Both PO handoffs must actually call it, or none of the above happens.
    for (const [file, kind, name] of [['inv','inv','createInvoiceFromPo'],
                                      ['dn','dn','createDnFromPo']]) {
        assert.ok(body(files[file], name).includes(`ClientDefaults.adopt("${kind}")`),
            `${name} must resolve the PO's company through Clients Master Data`);
    }

    /* ---- a document imported from a DN or a quotation (2026-09-11) -------
       Waleed made an invoice from DN-HDEC-066 and got the profile's default
       account instead of HDEC's. A DN carries a client and an address; it
       carries NO receiving account and no tax status, because a delivery note
       has neither. So those two are still the editor's own starting values and
       still ours to fill - while the address stays the one the DN supplied.
       `adopt(kind, supplied)` is how a caller says which fields came across. */
    const HDECBANK = () => ({ok:true, known:true, name:'HDEC', code:'HDEC',
        print_name:'Hyundai Engineering and Construction Co., Ltd',
        address:'HDEC Camp, Basra', payment_bank_key:'bob', withholding:false});

    h = harness(HDECBANK, {inv:{mode:'new', sourceDn:'DN-HDEC-066', withholdingIncluded:true}});
    h.nodes.invBank.value = 'adib';            // initNewInv's profile default...
    h.cd.reset('inv');                         // ...and the baseline it records
    h.nodes.invClient.value = 'HDEC';          // importDnIntoInv assigns in code
    h.nodes.invAddress.value = 'Site gate 3, as written on the DN';
    await h.cd.adopt('inv', ['address']);
    assert.equal(h.nodes.invBank.value, 'bob',
        "the client's receiving account must be applied - a delivery note does "
        + "not carry one, so the profile default was never this document's");
    assert.equal(h.nodes.invAddress.value, 'Site gate 3, as written on the DN',
        'the address IS carried by the DN, so it must survive');
    assert.equal(h.inv.withholdingIncluded, false,
        'tax status is not carried by a DN either');
    assert.equal(h.nodes.invClientCode.textContent, 'HDEC');

    /* The address case above passes on the BASELINE rule alone (the DN's
       address is not the value reset recorded), so on its own it does not
       prove `supplied` does anything. This is the case that does: the source's
       address IS the recorded baseline, which is what happens the moment
       anyone moves the `reset` call after the assignment. Only the `supplied`
       list stands between the DN's address and the client's. */
    h = harness(HDECBANK, {inv:{mode:'new', sourceDn:'DN-HDEC-066'}});
    h.nodes.invAddress.value = 'Site gate 3, as written on the DN';
    h.nodes.invBank.value = 'adib';
    h.cd.reset('inv');                         // baseline now IS the DN's address
    h.nodes.invClient.value = 'HDEC';
    await h.cd.adopt('inv', ['address']);
    assert.equal(h.nodes.invAddress.value, 'Site gate 3, as written on the DN',
        'the DN supplied the address, so it must survive even when it happens '
        + 'to be the value reset recorded');
    assert.equal(h.nodes.invBank.value, 'bob',
        'the account is still not something a DN carries');

    // Nothing supplied at all is still the PO case, and still works.
    h = harness(HDECBANK, {inv:{mode:'new', sourcePo:'PO-1'}});
    h.nodes.invBank.value = 'adib';
    h.cd.reset('inv');
    h.nodes.invClient.value = 'HDEC';
    await h.cd.adopt('inv');
    assert.equal(h.nodes.invBank.value, 'bob');
    assert.equal(h.nodes.invAddress.value, 'HDEC Camp, Basra',
        "a PO carries no address, so the client address fills it");

    // The three import paths must each say what their source supplies.
    for (const [file, fn, call] of [
        ['inv', 'importDnIntoInv', 'ClientDefaults.adopt("inv", ["address"])'],
        ['inv', 'importQuoteIntoInv', 'ClientDefaults.adopt("inv", ["address"])'],
        ['dn', 'importQuoteIntoDN', 'ClientDefaults.adopt("dn", ["address"])'],
    ]) {
        assert.ok(body(files[file], fn).includes(call),
            `${fn} must apply the client's settings for the fields its source `
            + 'does NOT carry, or an imported invoice keeps the profile default account');
    }

    // A chip read that comes back after the document was replaced cannot land.
    h = harness(name => name === 'SLOW' ? new Promise(r => {release = r;}) : TDC());
    h.nodes.dnClient.value = 'SLOW';
    h.cd.reset('dn');
    await new Promise(r => setTimeout(r, 0));
    h.cd.reset('dn');
    release(TDC());
    await h.settle();
    assert.equal(h.nodes.dnClientCode.textContent, '',
        'a stale chip read must not paint the document that replaced it');

    assert.doesNotMatch(source, /setInvSellCurrency/,
        'picking a client must not reach the currency converter at all');
    // That converter still has to survive a slow peg prompt for its own callers.
    assert.match(body(files.inv, 'setInvSellCurrency'), /if \(!live\(\)\) return;/,
        'setInvSellCurrency re-checks after the peg prompt closes');

    console.log('Client settings: printed name, code chip, never overwriting, race and error checks passed');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
