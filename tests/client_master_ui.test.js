/* Clients controller behavior, without a real bridge or database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const sourcePath = 'static/client_master.js';
assert.ok(fs.existsSync(sourcePath), 'Clients Master Data controller must exist');
const source = fs.readFileSync(sourcePath, 'utf8');
function element(id) {
    return { id, hidden: false, innerHTML: '', textContent: '', value: '', disabled: false,
        dataset: {}, handlers: {}, classList: { add() {}, remove() {}, toggle() {} },
        setAttribute() {}, focus() {}, contains() { return true; },
        addEventListener(type, fn) { this.handlers[type] = fn; },
        querySelectorAll() { return []; }, querySelector() { return null; } };
}
function harness(overrides = {}) {
    const ids = ['clientMasterView', 'cmList', 'cmDetail', 'cmMessage', 'cmCount',
        'cmSearch', 'cmStatus', 'cmNew', 'cmBack', 'cmMaterials', 'imClientsTab', 'btnHome'];
    const nodes = Object.fromEntries(ids.map(id => [id, element(id)]));
    const saved = [], archived = [];
    let confirm = false;
    const record = { id: 1, name: 'HDEC', aliases: ['Hyundai'], contacts: [], addresses: [],
        defaults: { bank_key: 'ADIB' }, updated_at: 'v1', archived: false, usage: {total: 4} };
    const api = { cm_list: async () => ({ok: true, records: [record], total: 1}),
        cm_get: async (id) => ({ok: true, record: id ? record : null,
            profile_id: 'abraj', bank_options: [{key:'ADIB',label:'Our ADIB account',currency:'USD'}]}),
        cm_save: async payload => { saved.push(payload); return {ok: true, record: {...payload, updated_at: 'v2'}}; },
        cm_archive: async (id, flag) => { archived.push([id, flag]); return {ok: true, record: {...record, archived: flag}}; }, ...overrides };
    const document = { readyState: 'complete', getElementById: id => nodes[id] || null,
        querySelectorAll: () => [], body: {classList: {remove() {}}}, addEventListener() {} };
    const window = { pywebview: {api}, Popup: {confirm: async () => confirm},
        hideAllViews() {}, setBrand() {}, profileDisplayName: () => 'Abraj Albadr' };
    vm.runInNewContext(source, {window, document, console, setTimeout, clearTimeout, structuredClone});
    return {ui: window.ClientMaster, nodes, saved, archived, setConfirm: value => {confirm = value;},
        input(field, value) { nodes.cmDetail.handlers.input({target: {dataset: {cmField: field}, value, type: 'text'}}); } };
}
async function main() {
    const h = harness();
    await h.ui.show();
    await h.ui.select(1);
    assert.match(h.nodes.cmDetail.innerHTML, /Our ADIB account/);
    assert.match(h.nodes.cmDetail.innerHTML, /Abraj Albadr/);
    h.input('name', 'HDEC updated');
    assert.equal(await h.ui.canLeave(), false, 'cancel must preserve the unsaved client');
    await h.ui.save();
    assert.equal(h.saved[0].name, 'HDEC updated');
    assert.equal(h.saved[0].updated_at, 'v1', 'save must carry the optimistic concurrency token');
    assert.equal(h.saved[0].profile_id, 'abraj', 'save must guard the profile that supplied bank choices');
    assert.equal(h.saved[0].defaults.bank_key, 'ADIB', 'bank keys retain their exact case');
    assert.equal(await h.ui.canLeave(), true);
    h.input('name', 'Discard this name'); h.setConfirm(true);
    assert.equal(await h.ui.canLeave(), true);
    await h.ui.save();
    assert.equal(h.saved[1].name, 'HDEC updated', 'discard restores the saved draft when the view is reopened');

    const failure = harness({cm_save: async () => ({ok:false,error:'Client changed in another session'})});
    await failure.ui.select(1); failure.input('name','Keep my work'); await failure.ui.save();
    assert.match(failure.nodes.cmMessage.textContent, /Client changed in another session/);
    assert.equal(await failure.ui.canLeave(), false, 'failed save keeps the dirty state');

    let releaseFirst;
    const races = harness({cm_get: id => id === 1 ? new Promise(resolve => {releaseFirst = resolve;}) :
        Promise.resolve({ok:true,record:{id:2,name:'Second',updated_at:'v2'},profile_id:'abraj',bank_options:[]})});
    const slow = races.ui.select(1);
    await Promise.resolve();
    await races.ui.select(2);
    releaseFirst({ok:true,record:{id:1,name:'Stale',updated_at:'v1'},profile_id:'abraj',bank_options:[]});
    await slow;
    assert.match(races.nodes.cmDetail.innerHTML, /Second/);
    assert.doesNotMatch(races.nodes.cmDetail.innerHTML, /value="Stale"/);

    const escaped = harness({cm_get: async () => ({ok:true,record:{id:3,name:'<img src=x onerror=bad()>',
        contacts:[{name:'<script>bad()</script>'}]},profile_id:'abraj',bank_options:[]})});
    await escaped.ui.select(3);
    assert.doesNotMatch(escaped.nodes.cmDetail.innerHTML, /<img src=x|<script>bad/);
    assert.match(escaped.nodes.cmDetail.innerHTML, /&lt;img/);

    // Clicking a row must hand the record's id through UNCHANGED. Ids are
    // uuid hex strings; coercing one with Number() yields NaN and makes the
    // whole list unclickable, which the direct ui.select(...) calls above
    // cannot see because they bypass the click handler.
    const uuid = '4f3a9c2be1d0475e8a6b0c7d2e9f1a3b';
    const asked = [];
    const clicks = harness({
        cm_list: async () => ({ok: true, total: 1, records: [
            {id: uuid, name: 'HDEC', aliases: [], contacts: [], addresses: [],
             defaults: {}, updated_at: 'v1', archived: false, usage: {total: 0}}]}),
        cm_get: async id => { asked.push(id);
            return {ok: true, record: {id: uuid, name: 'HDEC', updated_at: 'v1'},
                    profile_id: 'abraj', bank_options: []}; },
    });
    await clicks.ui.show();
    assert.match(clicks.nodes.cmList.innerHTML, new RegExp(`data-cm-id="${uuid}"`),
        'the list must carry the record id it was given');
    await clicks.nodes.cmList.handlers.click({target: {closest: () => ({dataset: {cmId: uuid}})}});
    assert.deepEqual(asked, [uuid], 'a row click loads the clicked record, not NaN');

    const archivedClient = harness(); await archivedClient.ui.select(1);
    archivedClient.setConfirm(true); await archivedClient.ui.archive();
    assert.deepEqual(archivedClient.archived, [[1, true]], 'archive is reversible and explicit');

    const html = fs.readFileSync('templates/index.html', 'utf8');
    const shell = fs.readFileSync('static/app.js', 'utf8');
    assert.match(html, /id="clientMasterView"/);
    assert.match(html, /id="imClientsTab"/);
    assert.match(shell.match(/const ALL_VIEWS = \[[\s\S]*?\];/)[0], /clientMasterView/);
    assert.match(shell, /ClientMaster\.canLeave/);
    assert.doesNotMatch(source, /supplier|iban|swift/i, 'client preferences select our stored bank key only');
    // The editors' side of client settings lives in client_defaults.js and is
    // pinned by tests/client_defaults_lifecycle.test.js - here we only check
    // that the Clients screen still ships the file the editors depend on.
    assert.ok(fs.existsSync('static/client_defaults.js'),
        'Editors apply client settings when a client is picked');

    const clientCss=fs.readFileSync('static/client_master.css','utf8');
    const card=clientCss.match(/\.cm-client \{([^}]+)\}/)[1];
    assert.match(card,/height: auto/,'client cards override the global fixed button height');
    console.log('Clients Master Data: controller, errors, navigation, race and escaping checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
