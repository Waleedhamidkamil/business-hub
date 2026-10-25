/* Real-browser check of the two popups added 2026-10-06/07:
     - Insights > client > "Monthly statement" (static/analytics.js openStatement)
     - Settings > About > "Verify a document" (static/doc_verify.js)
   The page is the real index.html markup and the real scripts; only the Python
   bridge is stubbed. Insights data comes from a JSON file of REAL
   insights_overview / client_insights output (argv[2]); screenshots go to argv[3].

   node tests/protection_ui.qa.cjs <insights.json> <out-dir>
   (PLAYWRIGHT_PATH may point at a playwright install.) */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const ROOT = path.resolve(__dirname, '..');
const OUT = path.resolve(process.argv[3]);
fs.mkdirSync(OUT, {recursive: true});
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const page = await browser.newPage({viewport: {width: 1366, height: 900}});
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const html = read('templates/index.html');
  const main = id => html.match(new RegExp('<main id="' + id + '"[\\s\\S]*?<\\/main>'))[0];
  const fixture = '<html><head><meta charset="utf-8"><link rel="stylesheet" href="../static/app_styles.css"></head><body>'
    + '<button id="btnHome" hidden>Home</button>' + main('analyticsView') + main('settingsView')
    + '<script src="../static/lib/apexcharts.min.js"></script><script src="../static/popup.js"></script>'
    + '</body></html>';
  await page.route('http://qa.local/**', async route => {
    const p = decodeURIComponent(new URL(route.request().url()).pathname);
    if (p === '/templates/qa.html') return route.fulfill({contentType: 'text/html', body: fixture});
    const target = path.resolve(ROOT, '.' + p);
    if (!target.startsWith(ROOT + path.sep) || !fs.existsSync(target)) return route.abort();
    await route.fulfill({body: fs.readFileSync(target),
      contentType: target.endsWith('.js') ? 'application/javascript' : target.endsWith('.css') ? 'text/css' : 'application/octet-stream'});
  });
  await page.goto('http://qa.local/templates/qa.html');
  await page.evaluate(d => {
    window.calls = [];
    const log = (n, a) => window.calls.push({n, a});
    window.pywebview = {api: {
      insights_overview: async () => d.ov,
      client_insights: async () => d.cl,
      client_statement_months: async (name) => (log('months', [name]), {ok: true, months: [
        {year: 2026, month: 9, invoices: 15, label: 'September 2026', exists: false},
        {year: 2026, month: 8, invoices: 18, label: 'August 2026', exists: true}]}),
      make_client_statement: async (n, y, m) => (log('make', [n, y, m]), {ok: true,
        path: 'D:\\PO TOOL\\REPORTS\\2026\\AUG\\TDC - August 2026.pdf', pages: 64, size_mb: 42.3,
        label: 'August 2026', invoices: 18, paid: 16, pending: 2, missing: [], uncosted: []}),
      open_path: async p => (log('open', [p]), {ok: true}),
      reveal_in_folder: async p => (log('reveal', [p]), {ok: true}),
      verify_document: async (no, code) => (log('verify', [no, code]), String(code).toUpperCase() === '7K2F-9QXA'
        ? {ok: true, genuine: true, current: true, reason: 'Genuine. This is the latest version we printed.',
           document: {kind: 'Invoice', no, date: '5/8/2026', client: 'TDC', lines: 1, total: 200, currency: 'USD'}}
        : {ok: true, genuine: false, current: false, reason: 'NOT GENUINE. We never printed this code on ' + no + '.',
           document: {kind: 'Invoice', no, date: '5/8/2026', client: 'TDC', lines: 1, total: 200, currency: 'USD'}}),
    }};
  }, data);
  await page.addScriptTag({url: '../static/analytics.js'});
  await page.addScriptTag({url: '../static/doc_verify.js'});

  // ---- Monthly statement -------------------------------------------------
  await page.evaluate(() => window.showAnalytics());
  await page.locator('.in-ccard[data-client="TDC"]').first().click();
  await page.locator('#inStatement').waitFor();
  await page.locator('#inStatement').click();
  await page.locator('#inStmtMonth').waitFor();
  assert.equal(await page.locator('#inStmtMonth').inputValue(), '2026-8',
    'the month Insights is showing (August) must be offered first');
  assert.match(await page.locator('.in-stmt-note').innerText(), /replaces the statement already made/,
    'an existing statement must be announced before it is overwritten');
  await page.screenshot({path: path.join(OUT, 'statement-popup.png')});
  await page.locator('[data-value="make"]').click();
  await page.waitForFunction(() => window.calls.some(c => c.n === 'open'));
  const calls = await page.evaluate(() => window.calls);
  assert.deepEqual(calls.find(c => c.n === 'make').a, ['TDC', 2026, 8]);
  assert.equal(await page.locator('[data-value="make"]').innerText(), 'Show in folder');
  assert.match(await page.locator('.in-stmt-path').innerText(), /TDC - August 2026\.pdf/);
  await page.screenshot({path: path.join(OUT, 'statement-done.png')});
  await page.locator('[data-value="make"]').click();
  assert.ok((await page.evaluate(() => window.calls)).some(c => c.n === 'reveal'));
  await page.keyboard.press('Escape');

  // ---- Verify a document ------------------------------------------------
  await page.evaluate(() => window.openVerifyDocument());
  await page.locator('#verifyNo').fill('INV-MT-TDC-030');
  await page.locator('#verifyCode').fill('7k2f-9qxa');
  await page.locator('#verifyCode').press('Enter');
  await page.locator('.verify-result.is-genuine').waitFor();
  assert.match(await page.locator('.verify-verdict').innerText(), /^GENUINE$/);
  await page.screenshot({path: path.join(OUT, 'verify-genuine.png')});
  await page.locator('#verifyCode').fill('AAAA-AAAA');
  await page.locator('[data-value="check"]').click();
  await page.locator('.verify-result.is-fake').waitFor();
  assert.match(await page.locator('.verify-verdict').innerText(), /NOT GENUINE/);
  assert.match(await page.locator('.verify-result').innerText(), /Total 200\.00 USD/);
  await page.screenshot({path: path.join(OUT, 'verify-fake.png')});

  assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
  await browser.close();
  console.log('protection UI QA passed');
})().catch(e => { console.error(e); process.exit(1); });
