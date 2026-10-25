/* Real startup scheduling and asset reuse, with the bridge/DOM substituted. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[2] || 'static/app.js', 'utf8');

function section(start, end) {
    const a = source.indexOf(start);
    const b = source.indexOf(end, a + start.length);
    assert(a >= 0 && b > a, 'Test boundary missing: ' + start);
    return source.slice(a, b);
}
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

async function cacheChecks() {
    let count = 0;
    let reply = deferred();
    const ctx = vm.createContext({api: () => ({get_lottie: () => {count++; return reply.promise;}})});
    const code = section('const _lottieDataRequests', 'async function initCardLotties()');
    vm.runInContext(code, ctx);
    const first = vm.runInContext('loadLottieData("save")', ctx);
    const second = vm.runInContext('loadLottieData("save")', ctx);
    await tick();
    assert.equal(count, 1, 'Concurrent hosts must request the same slug only once');
    reply.resolve({layers: [{name: 'original'}], markers: []});
    const [a, b] = await Promise.all([first, second]);
    a.layers[0].name = 'changed by Lottie';
    assert.equal(b.layers[0].name, 'original', 'Hosts must not share mutable animation data');
    const c = await vm.runInContext('loadLottieData("save")', ctx);
    assert.equal(c.layers[0].name, 'original', 'The cached source must stay untouched');
    assert.equal(count, 1, 'A mounted icon must reuse a successful request');

    reply = deferred();
    const failed = vm.runInContext('loadLottieData("print")', ctx);
    await tick();
    reply.reject(new Error('bridge unavailable'));
    assert.equal(await failed, null, 'A failed bridge keeps the static icon');
    reply = deferred();
    const retry = vm.runInContext('loadLottieData("print")', ctx);
    await tick();
    assert.equal(count, 3, 'A failed request must be retried');
    reply.resolve({});
    assert.equal(await retry, null, 'Missing assets keep their static SVG');
    reply = deferred();
    const recovered = vm.runInContext('loadLottieData("print")', ctx);
    await tick();
    assert.equal(count, 4, 'An invalid asset must not poison the cache');
    reply.resolve({layers: []});
    assert((await recovered).layers, 'The icon recovers when its asset becomes available');

    for (const [start, end] of [
        ['async function initCardLotties()', '// Phase X.15'],
        ['async function mountLottieIcon(', 'function initLottieIcons('],
        ['async function initThemeLottie()', 'function initThemeToggle('],
    ]) {
        const consumer = section(start, end);
        assert(consumer.includes('loadLottieData('), start + ' must use the shared loader');
        assert(!consumer.includes('api().get_lottie('), start + ' must not duplicate a request');
    }
    console.log('PASS: animation deduplication, mutation isolation, retry and all three callers');
}

async function priorityChecks(fail) {
    const data = deferred();
    const frames = [];
    const calls = [];
    const elements = {};
    const ctx = vm.createContext({
        console: {error() {}},
        document: {
            getElementById: id => elements[id] || (elements[id] = {}),
            body: {classList: {add() {}}},
        },
        window: {},
        requestAnimationFrame: callback => frames.push(callback),
        hideAllViews() {}, setBrand() {}, refreshUniversalSettingsBtn() {}, bindHeroLogoFlip() {},
        _activeCompanyProfile: null,
        renderDashboard: () => data.promise,
        renderDashboardError: () => calls.push('error'),
        initCardLotties: () => calls.push('cards'),
        initLottieIcons: () => calls.push('toolbar'),
        initThemeLottie: () => calls.push('theme'),
    });
    vm.runInContext(section('function scheduleDashboardAnimations()', '// Unsaved-changes guard for the PO editor'), ctx);
    vm.runInContext('showHub()', ctx);
    await tick();
    assert.equal(frames.length, 0, 'Animations must not queue ahead of dashboard data');
    assert.deepEqual(calls, []);
    if (fail) data.reject(new Error('dashboard unavailable')); else data.resolve();
    await tick();
    assert.equal(frames.length, 1, 'Settled dashboard schedules its optional animations');
    frames.shift()();
    assert.deepEqual(calls, fail ? ['error'] : [], 'Allow the dashboard to paint first');
    frames.shift()();
    assert.deepEqual(calls, fail ? ['error', 'cards', 'toolbar', 'theme'] : ['cards', 'toolbar', 'theme']);
    const boot = section('async function bootstrap()', '// Navigation (hub');
    assert(!boot.includes('initLottieIcons()') && !boot.includes('initThemeLottie()'),
        'Bootstrap must not bypass the dashboard priority');
    console.log('PASS: dashboard priority, paint opportunity and ' + (fail ? 'error recovery' : 'successful startup'));
}

async function bootChecks() {
    const pending = [deferred(), deferred(), deferred()];
    const calls = [];
    const ctx = vm.createContext({
        console: {log() {}, error() {}},
        initToday: () => {calls.push('today'); return pending[0].promise;},
        initCompanyProfile: () => {calls.push('profile'); return pending[1].promise;},
        loadUserPreferences: () => {calls.push('preferences'); return pending[2].promise;},
        bindUi: () => calls.push('bind'), populateOpenSelectors() {},
        window: {prefGet: () => 'dark',
            initQuoteModule: () => calls.push('quote'),
            initDnModule: () => calls.push('dn'),
            initInvModule: () => calls.push('invoice')},
        applyTheme: theme => assert.equal(theme, 'dark'),
        applyCompactTables() {}, setupDocStatusSync() {},
        showHub: () => calls.push('hub'), initThemeToggle() {},
        toast: () => calls.push('error'),
    });
    vm.runInContext(section('async function bootstrap()', '// Navigation (hub'), ctx);
    const boot = vm.runInContext('bootstrap()', ctx);
    await tick();
    assert.deepEqual(calls, ['today', 'profile', 'preferences'],
        'Independent boot reads should start without serial bridge round trips');
    pending[0].resolve(); pending[2].resolve();
    await tick();
    assert(!calls.includes('bind'), 'Do not bind editors before the company is known');
    pending[1].resolve();
    await boot;
    assert.deepEqual(calls, ['today', 'profile', 'preferences', 'bind', 'quote', 'dn', 'invoice', 'hub']);
    console.log('PASS: concurrent boot reads, company/preference gate and all editor initializers');
}

async function mountRaceChecks() {
    const reply = deferred();
    let mounted = 0;
    const ctx = vm.createContext({
        api: () => ({get_lottie: () => reply.promise}),
        document: {createElement: () => ({style: {}})},
        window: {lottie: {loadAnimation: () => {
            mounted++;
            return {addEventListener() {}};
        }}, matchMedia: () => ({matches: true})},
        lottieSegments: () => null,
    });
    vm.runInContext(section('const _lottieDataRequests', 'async function initCardLotties()'), ctx);
    vm.runInContext(section('async function mountLottieIcon(', 'function initLottieIcons('), ctx);
    ctx.host = {dataset: {}, querySelector: () => null, appendChild() {}};
    const first = vm.runInContext('mountLottieIcon(host, "save")', ctx);
    const second = vm.runInContext('mountLottieIcon(host, "save")', ctx);
    reply.resolve({layers: []});
    await Promise.all([first, second]);
    assert.equal(mounted, 1, 'Returning Home while an asset is pending must not mount an icon twice');

    mounted = 0;
    ctx._cardAnims = {};
    ctx.CARD_LOTTIE = {po: 'po'};
    const chip = {dataset: {}, appendChild() {}};
    ctx.document.querySelector = () => ({querySelector: () => chip});
    vm.runInContext(section('async function initCardLotties()', '// Phase X.15'), ctx);
    await Promise.all([vm.runInContext('initCardLotties()', ctx), vm.runInContext('initCardLotties()', ctx)]);
    assert.equal(mounted, 1, 'Concurrent Home requests must not replace a dashboard icon twice');

    mounted = 0;
    const button = {classList: {add() {}, remove() {}}, appendChild() {}};
    ctx.document.getElementById = () => button;
    ctx.window.lottie.loadAnimation = () => { mounted++; return {setSpeed() {}, addEventListener() {}}; };
    vm.runInContext(section('async function initThemeLottie()', 'function initThemeToggle('), ctx);
    await Promise.all([vm.runInContext('initThemeLottie()', ctx), vm.runInContext('initThemeLottie()', ctx)]);
    assert.equal(mounted, 1, 'Concurrent Home requests must not replace the theme control twice');
    console.log('PASS: concurrent initialization mounts toolbar, card and theme icons once');
}

(async () => {
    await cacheChecks();
    await priorityChecks(false);
    await priorityChecks(true);
    await bootChecks();
    await mountRaceChecks();
})().catch(error => {console.error(error); process.exitCode = 1;});
