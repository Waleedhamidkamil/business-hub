/* A document may only name a client that is on file (Waleed, 2026-09-11).

   "no new clients shall be accepted when typing in client name in all editors.
   either we have a new client popup or we need to add the client from master
   data client tab" - he chose the popup.

   The refusal itself is the backend's (`api._known_client_error`, pinned by
   tests/client_must_be_on_file.test.py). This covers the way OUT of it: the
   dialog that adds the client without abandoning the document, and the retry
   in each editor that saves again afterwards.

   No browser. `Popup` and the client field are stubs, which is enough because
   what matters here is the PAYLOAD sent to cm_save and whether the editor is
   told to retry. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/client_required.js', 'utf8');
const editors = Object.fromEntries(['quote', 'dn', 'inv']
    .map(k => [k, fs.readFileSync(`static/${k}.js`, 'utf8')]));

/* ---------- each editor must ask, and must save again ---------------- */
{
    const wiring = [
        ['inv', 'save_inv', 'inv'],
        ['quote', 'save_quote', 'qut'],
        ['dn', 'save_dn', 'dn'],
    ];
    for (const [file, method, kind] of wiring) {
        const code = editors[file];
        assert.ok(code.includes(`window.ClientRequired.offer(r, "${kind}")`),
            `${file}.js must offer to add the client when the save is refused, or a `
            + 'new client is a dead end in that editor');
        // The retry is the point. An offer with no second save leaves the user
        // having added a client and lost the save they asked for.
        const at = code.indexOf(`window.ClientRequired.offer(r, "${kind}")`);
        const after = code.slice(at, at + 400);
        assert.ok(after.includes(`api().${method}(`),
            `${file}.js offers to add the client but never saves again`);
    }
    // inv and quote rebuild the payload: adding a client rewrites the client
    // box to the printed name, and the stale payload would save the old one.
    for (const [file, build] of [['inv', 'buildInvPayload'], ['quote', 'buildQuotePayload']]) {
        const at = editors[file].indexOf('ClientRequired.offer');
        assert.ok(editors[file].slice(at, at + 400).includes(build + '()'),
            `${file}.js must REBUILD its payload before saving again - the client `
            + 'box has changed since the first attempt');
    }
    assert.ok(!/const payload = \{ \.\.\.buildInvPayload/.test(editors.inv),
        'the invoice payload is reassigned on retry, so it cannot be a const');
}

/* ---------- the module itself --------------------------------------- */
function harness({saveResult, archiveResult} = {}) {
    const calls = {cmSave: [], cmArchive: [], toasts: [], applied: [], populated: 0};
    const nodes = {};
    const el = (extra = {}) => ({value: '', textContent: '', hidden: false,
        addEventListener() {}, focus() {}, dispatchEvent() {}, ...extra});
    for (const id of ['crName', 'crLegal', 'crArabic', 'crAliases', 'crBank', 'crCode', 'crNote'])
        nodes['#' + id] = el();
    const fields = {qClient: el(), dnClient: el(), invClient: el()};

    let opened = null;
    const popup = {
        open(spec) {
            const handle = {
                body: {querySelector: sel => nodes[sel] || null},
                close(value) { handle._resolve(value); },
            };
            handle.result = new Promise(r => { handle._resolve = r; });
            opened = {spec, handle};
            /* The real dialog pre-fills the short name from the HTML `value`
               attribute. Read it out of the markup rather than assuming it, so
               these stubs start where a real field would - and so a form that
               stopped pre-filling the typed name fails here. */
            const prefill = /id="crName"[^>]*value="([^"]*)"/.exec(String(spec.body || ''));
            nodes['#crName'].value = prefill ? prefill[1] : '';
            if (typeof spec.onOpen === 'function') spec.onOpen(handle);
            return handle;
        },
        confirm: async () => popup._confirm,
        _confirm: true,
    };
    const context = {
        console, setTimeout, Promise,
        Event: class { constructor(type) { this.type = type; } },
        document: {getElementById: id => fields[id] || null},
        window: {
            Popup: popup,
            toast: (t, bad) => calls.toasts.push([t, !!bad]),
            ClientDefaults: {apply: async kind => { calls.applied.push(kind); }},
            populateInvClientList: async () => { calls.populated += 1; },
            pywebview: {api: {
                cm_save: async payload => { calls.cmSave.push(payload); return saveResult; },
                cm_archive: async (name, on) => {
                    calls.cmArchive.push([name, on]); return archiveResult;
                },
            }},
        },
    };
    context.window.window = context.window;
    vm.runInNewContext(source, context);
    return {cr: context.window.ClientRequired, calls, nodes, fields, popup,
            get opened() { return opened; },
            click() { opened.spec.actions.find(a => a.value === 'add').onClick(opened.handle); }};
}

const OK = {ok: true, record: {name: 'Zenith'}};

/* A promise that never settles is how a test passes without testing anything:
   `main()` simply stops, nothing throws, and node exits 0. Two of the checks
   below were silently doing exactly that - removing the code they cover left
   `offer` awaiting a dialog that the test never answers. Every offer that is
   expected to come back goes through here. */
function settles(promise, what) {
    return Promise.race([
        promise,
        new Promise((_, reject) => setTimeout(
            () => reject(new Error(`offer() never came back: ${what}`)), 50)),
    ]);
}

async function main() {
    /* ---- it must not swallow anything that is not its business ---- */
    {
        const h = harness();
        assert.equal(await settles(h.cr.offer({ok: false, error: 'Disk is full'}, 'inv'),
            'unrelated save failure'), false,
            'an unrelated save failure must fall through to the editor error');
        assert.equal(h.opened, null, 'and must not open the add-client dialog');
        assert.equal(await settles(h.cr.offer({ok: true, inv_no: 'INV-MT-TDC-056'}, 'inv'),
            'successful save'), false,
            'a SUCCESSFUL save must never be treated as a refusal');
        assert.equal(await settles(h.cr.offer(null, 'inv'), 'missing result'), false,
            'a missing result is not a refusal');
    }

    /* ---- a blank client has nothing to add ---- */
    {
        const h = harness();
        const blank = h.cr.offer({ok: false, unknown_client: '', error: 'pick one'}, 'qut');
        // Checked BEFORE the await: if the dialog opened, this promise is
        // waiting on a button nobody will press and awaiting it first would
        // hang the suite into a false pass.
        assert.equal(h.opened, null,
            'a blank client is answered by the editor message, not by a dialog');
        assert.equal(await settles(blank, 'blank client'), false);
    }

    /* ---- the ordinary case: add the client, then save again ---- */
    {
        const h = harness({saveResult: OK});
        const pending = h.cr.offer({ok: false, unknown_client: 'Zenith Trading'}, 'inv');
        assert.ok(h.opened, 'an unknown client must offer the add dialog');
        assert.match(h.opened.spec.title, /client/i);

        h.nodes['#crName'].value = 'Zenith';          // he shortens it for the number
        h.nodes['#crLegal'].value = 'Zenith Trading Company Ltd';
        h.nodes['#crArabic'].value = 'زينيث';
        h.nodes['#crBank'].value = 'cash';
        h.click();
        assert.equal(await settles(pending, 'add client'), true,
            'adding a client must tell the editor to save again');

        assert.equal(h.calls.cmSave.length, 1);
        const payload = h.calls.cmSave[0];
        assert.equal(payload.name, 'Zenith');
        assert.equal(payload.legal_name, 'Zenith Trading Company Ltd');
        assert.equal(payload.name_ar, 'زينيث');
        // `deepEqual`, not `deepStrictEqual`: the payload is built inside the vm
        // context, so its prototypes are that realm's, not this one's.
        assert.deepStrictEqual({...payload.defaults}, {bank_key: 'cash'});
        // The whole point of the alias: the spelling he actually typed has to
        // find this client next time, or he is refused again for the same name.
        assert.ok(payload.aliases.includes('Zenith Trading'),
            'the name that was typed must be kept as an alias, got: ' + JSON.stringify(payload.aliases));
        // The box now holds the client, so the rebuilt payload resolves.
        assert.equal(h.fields.invClient.value, 'Zenith');
        assert.deepEqual(h.calls.applied, ['inv'], "the new client's settings apply at once");
        assert.equal(h.calls.populated, 1, 'the datalist must offer the new client');
    }

    /* ---- a name kept as typed is not duplicated into the aliases ---- */
    {
        const h = harness({saveResult: {ok: true, record: {name: 'Zenith Trading'}}});
        const pending = h.cr.offer({ok: false, unknown_client: 'Zenith Trading'}, 'dn');
        h.nodes['#crName'].value = 'Zenith Trading';
        h.click();
        await pending;
        assert.deepEqual(Array.from(h.calls.cmSave[0].aliases), [],
            'the display name is already the display name - it is not also an alias');
    }

    /* ---- cancelling changes nothing ---- */
    {
        const h = harness({saveResult: OK});
        const pending = h.cr.offer({ok: false, unknown_client: 'Zenith'}, 'inv');
        h.opened.handle.close(null);
        assert.equal(await settles(pending, 'cancel'), false,
            'cancelling must not make the editor save again');
        assert.equal(h.calls.cmSave.length, 0);
        assert.equal(h.fields.invClient.value, '', 'and must not touch the document');
    }

    /* ---- a refused add keeps the dialog open and says why ---- */
    {
        const h = harness({saveResult: {ok: false, error: 'Name "TDC" already belongs to another client.'}});
        let settled = false;
        const pending = h.cr.offer({ok: false, unknown_client: 'TDC Iraq'}, 'inv').then(v => {settled = v;});
        h.nodes['#crName'].value = 'TDC';
        h.click();
        await new Promise(r => setTimeout(r, 0));
        assert.equal(settled, false, 'a refused add must NOT close the dialog or retry the save');
        assert.match(h.nodes['#crNote'].textContent, /already belongs/,
            'the dialog has to say what the client store refused');
        assert.equal(h.nodes['#crNote'].hidden, false);
        // ...and the user can correct it and try again in the same dialog.
        h.nodes['#crName'].value = 'TDC Iraq Branch';
        h.calls.cmSave.length = 0;
        h.click();
        assert.equal(h.calls.cmSave.length, 1, 'the dialog must still be usable after a refusal');
    }

    /* ---- an empty short name never reaches the client store ---- */
    {
        const h = harness({saveResult: OK});
        h.cr.offer({ok: false, unknown_client: 'Zenith'}, 'inv');
        h.nodes['#crName'].value = '   ';
        h.click();
        await new Promise(r => setTimeout(r, 0));
        assert.equal(h.calls.cmSave.length, 0, 'a nameless client must not be saved');
        assert.match(h.nodes['#crNote'].textContent, /short name/i);
    }

    /* ---- an archived client is restored, not re-created ---- */
    {
        const h = harness({archiveResult: {ok: true}});
        const pending = h.cr.offer(
            {ok: false, unknown_client: 'ABRAJ', archived_client: true}, 'qut');
        assert.equal(h.opened, null,
            'an archived client is restored through a confirm, never through the add dialog');
        assert.equal(await settles(pending, 'archived client'), true,
            'restoring lets the save go through');
        assert.deepEqual(h.calls.cmArchive, [['ABRAJ', false]]);
        assert.equal(h.calls.cmSave.length, 0,
            'an archived client already exists - adding a second record would split their history');
    }
    {
        const h = harness({archiveResult: {ok: true}});
        h.popup._confirm = false;
        assert.equal(await settles(h.cr.offer(
            {ok: false, unknown_client: 'ABRAJ', archived_client: true}, 'qut'),
            'declined restore'), false);
        assert.deepEqual(h.calls.cmArchive, [], 'declining must not restore anything');
    }

    /* ---- the code hint must match what the number will really use ---- */
    {
        const h = harness({saveResult: OK});
        h.cr.offer({ok: false, unknown_client: 'N/A'}, 'inv');
        assert.equal(h.nodes['#crName'].value, 'N/A',
            'the dialog must open with the name that was typed already in it');
        assert.equal(h.nodes['#crCode'].textContent, 'NA',
            'the hint has to show the code after the path-unsafe characters are '
            + 'stripped, the same way client_master.document_code strips them');
    }

    console.log('Add-a-client: payload, alias retention, refusal, restore and retry checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
