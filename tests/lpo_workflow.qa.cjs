const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const ROOT = path.resolve(__dirname,'..');
const BASE = path.resolve(process.argv[3]);
fs.mkdirSync(BASE,{recursive:true});
const read = p => fs.readFileSync(path.join(ROOT,p),'utf8');
const data = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
let auditBrowser;
(async()=>{
 const browser = await chromium.launch({channel:'chrome',headless:true,args:['--allow-file-access-from-files']});
 auditBrowser=browser;
 const page = await browser.newPage({viewport:{width:1366,height:900}});
 const findings = {};
 const errors = [];
 page.on('pageerror',e=>errors.push(String(e)));
 const html=read('templates/index.html');
 const main = id => html.match(new RegExp('<main id="'+id+'"[\\s\\S]*?<\\/main>'))[0];
 const css = read('static/app_styles.css')+'\n'+read('static/dark.css');
 const fixture='<html lang="ar"><head><link rel="stylesheet" href="../static/app_styles.css"><link rel="stylesheet" href="../static/dark.css"><style>body{padding:18px;height:100vh;overflow:hidden}.view{height:calc(100vh - 36px)}</style></head><body><button id="btnHome" hidden>Home</button>'+main('lpoView')+main('lpoDocView')+'</body></html>';
 await page.route('http://lpo-audit.local/**', async route=>{
   const pathname=decodeURIComponent(new URL(route.request().url()).pathname);
   if(pathname==='/templates/audit.html') return route.fulfill({contentType:'text/html',body:fixture});
   const target=path.resolve(ROOT,'.'+pathname);
   if(!target.startsWith(path.resolve(ROOT)+path.sep)||!fs.existsSync(target)) return route.abort();
   await route.fulfill({body:fs.readFileSync(target),contentType:target.endsWith('.js')?'application/javascript':target.endsWith('.css')?'text/css':target.endsWith('.ttf')?'font/ttf':'application/octet-stream'});
 });
 await page.goto('http://lpo-audit.local/templates/audit.html');
 // The type tabs use the shared chip row from app.js (2026-10-03); the fixture
 // does not load app.js, so the real function is lifted in.
 const appJs=read('static/app.js');
 await page.evaluate(src=>{(0,eval)(src+'\nwindow.renderChipRow = renderChipRow;')},
   appJs.slice(appJs.indexOf('function renderChipRow('),appJs.indexOf('window.renderChipRow = renderChipRow;')));
 await page.evaluate(({rows,ful})=>{
   window.auditRows=rows;window.auditFul=ful;window.savedPayloads=[];window.created=[];
   window.createDnFromLpo=async(c,n)=>window.created.push({kind:'dn',c,n});
   window.createInvoiceFromLpo=async(c,n,p)=>window.created.push({kind:'inv',c,n,p});
   window.loadInvForEdit=async n=>window.openedInvoice=n;
   window.loadDnForEdit=async n=>window.openedDn=n;
   window.loadQuoteForEdit=async n=>window.openedQuote=n;
   window.api=()=>window.pywebview.api;
   window.esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   window.safe=fn=>(...args)=>Promise.resolve(fn(...args)).catch(e=>{window.lastError=String(e)});
   window.toast=(s)=>{window.lastToast=s};window.state={today:{display:'2/10/2026'}};
   window.hideAllViews=()=>document.querySelectorAll('main').forEach(el=>el.hidden=true);
   window.setBrand=()=>{};window.withLoading=async(b,fn)=>fn();
   window.pywebview={api:{list_lpos:async()=>window.auditRows,
     get_lpo:async(c,n)=>window.auditRows.find(r=>r.client_name===c&&r.lpo_no===n)||null,
     lpo_billing_detail:async()=>window.auditLedger,
     lpo_reference_gaps:async()=>window.auditGaps,
     cm_list:async()=>({ok:true,records:[{name:'HDEC'}]}),
     save_lpo:async p=>{window.savedPayloads.push(p);const rec=window.auditRows.find(r=>r.lpo_no===p.lpo_no&&r.client_name===p.client_name);if(rec)Object.assign(rec,p);return {ok:true}},
     lpo_fulfillment:async(c,n)=>window.auditFul[n]||{lines:[]},
     lpo_file_data_uri:async()=>window.auditFile,
     lpo_from_quote:async()=>({ok:false,error:'not found'})}};
 },{rows:data.abraj.rows,ful:data.abraj.summary.fulfillment});
 await page.evaluate(gaps=>window.auditGaps=gaps.map(r=>({...r,kind:r.no.startsWith('DN')?'dn':'inv',doc_no:r.no})),data.abraj.summary.orphans);
 for(const file of ['static/popup.js','static/lib/pdf.min.js','static/lpo_lines.js','static/lpo.js']) await page.addScriptTag({path:file==='static/lpo.js'&&process.argv[4]?process.argv[4]:path.join(ROOT,file)});
 // Count finished LPO-screen loads. A save reloads the screen in the background
 // (reopenLpoDoc); the script must wait for that before opening another LPO, or
 // the reload - correctly the latest open - takes the screen back (2026-10-03).
 await page.evaluate(()=>{const show=window.showLpoDoc;window.shown=0;
   window.showLpoDoc=async(...a)=>{try{return await show(...a)}finally{window.shown++}}});
 await page.evaluate(()=>showLpos());
 await page.evaluate(()=>document.fonts.ready);
 assert.equal(await page.locator('.lpo-row').count(),8);
 await page.locator('#lpoReferenceReview').click();
 assert.equal(await page.locator('[data-review-index]').count(),data.abraj.summary.orphans.length);
 await page.locator('[data-review-index="0"]').click();
 await page.waitForSelector('[data-review-index]',{state:'detached'});
 await page.screenshot({path:BASE+'/list-light.png'});
 findings.list1366={horizontalOverflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)};
 findings.lightStyles=await page.locator('.lpo-row').first().evaluate(e=>({font:getComputedStyle(e).fontFamily,bg:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color}));
 await page.evaluate(()=>{document.documentElement.dataset.theme='dark'});
 await page.waitForTimeout(250);
 await page.screenshot({path:BASE+'/list-dark.png'});
 findings.darkStyles=await page.locator('.lpo-row').first().evaluate(e=>({font:getComputedStyle(e).fontFamily,bg:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color}));
 await page.setViewportSize({width:1024,height:768});
 findings.list1024={horizontalOverflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),progressHidden:await page.locator('.lpo-meter').first().evaluate(e=>getComputedStyle(e).display==='none')};
 assert.equal(findings.list1024.progressHidden,false);assert.equal(findings.list1024.horizontalOverflow,false);
 await page.screenshot({path:BASE+'/list-1024.png'});
 await page.setViewportSize({width:1366,height:900});
 await page.evaluate(()=>{document.documentElement.dataset.theme='light'});
 await page.waitForTimeout(250);
 await page.locator('#lpoDueFilter').click();
 findings.billingFilterCount=await page.locator('.lpo-row').count();
 await page.locator('#lpoDueFilter').click();

 // Use the actual original PDF, read-only. No bridge save or filesystem action.
 const rec=data.abraj.rows.find(r=>r.lpo_no==='CSSP-ADM-0010');
 await page.evaluate(file=>window.auditFile=file,{ok:true,is_pdf:true,data_uri:'data:application/pdf;base64,'+fs.readFileSync(rec.file_path).toString('base64'),filename:path.basename(rec.file_path),path:rec.file_path});
 await page.evaluate(rec=>showLpoDoc(rec),rec);
 await page.waitForFunction(()=>document.querySelector('.lpo-v-canvas')?.width>0);
 findings.document={pages:await page.locator('.lpo-v-canvas').count(),orderedRows:await page.locator('.lpo-lines-tbl tbody tr').count(),linkedButtons:await page.locator('#lpoDocSide .lpo-chips button').count(),makeDocumentButtons:await page.locator('#lpoDocActions button').allTextContents()};
 await page.screenshot({path:BASE+'/document-light.png'});
 await page.evaluate(()=>{document.documentElement.dataset.theme='dark'});
 await page.waitForTimeout(250);
 await page.screenshot({path:BASE+'/document-dark.png'});
 await page.evaluate(()=>{document.documentElement.dataset.theme='light'});
 await page.waitForTimeout(250);
 await page.locator('#lpoDocSide [data-doc="edit"]').first().click();
 await page.waitForSelector('#lpoFNotes');
 findings.edit={savedRows:JSON.parse(rec.rows_json).length,visibleRows:await page.locator('#lpoLinesBody input[data-f="item"]').count(),bodyText:await page.locator('#lpoLinesBody').innerText()};
 findings.edit.dateVisibleWithinBody=await page.locator('#lpoFDate').evaluate(e=>{const p=e.closest('.popup-body').getBoundingClientRect(),r=e.getBoundingClientRect();return r.right<=p.right&&r.left>=p.left});
 await page.screenshot({path:BASE+'/edit-existing.png'});
 await page.locator('#lpoFNotes').fill('Audit notes only');
 const shownBeforeSave=await page.evaluate(()=>window.shown);
 await page.getByRole('button',{name:'Save',exact:true}).click();
 await page.waitForFunction(n=>window.shown>n,shownBeforeSave);
 await page.waitForFunction(()=>window.savedPayloads.length===1);
 findings.edit.submittedRows=await page.evaluate(()=>window.savedPayloads[0].rows.length);
 fs.writeFileSync(BASE+'/edit-payload.json',JSON.stringify(await page.evaluate(()=>window.savedPayloads[0]),null,2));
 assert.equal(findings.edit.savedRows,4);assert.equal(findings.edit.submittedRows,4);
 findings.iqd=await page.evaluate(()=>lpoProgress({kind:'materials',currency:'IQD',total:1400000,total_usd:1000,invoiced_usd:500}));
 assert.equal(findings.iqd,'$500.00 of $1,000.00 invoiced');
 findings.deepLink=await page.evaluate(async()=>{
   lpoState.rows=[{lpo_no:'SAME-001',client_name:'Client A'},{lpo_no:'SAME-001',client_name:'Client B'}];
   let opened=null;const original=window.showLpoDoc;window.showLpoDoc=async r=>opened=r.client_name;
   await openLpoByNo('SAME-001','Client C');window.showLpoDoc=original;return opened;
 });
 assert.equal(findings.deepLink,null);
 assert.equal(findings.document.pages,2);
 assert.ok(findings.document.linkedButtons>0);
 await page.evaluate(rec=>showLpoDoc(rec),rec);
 // A fully delivered and invoiced order offers nothing more to create (2026-10-03).
 findings.finishedCreateButtons=await page.locator('#lpoDocActions [data-doc="dn"],#lpoDocActions [data-doc="inv"],#lpoDocActions [data-next]').count();
 assert.equal(findings.finishedCreateButtons,0);
 // Drive actual header buttons on an order that still needs work. No external editor saves happen here.
 const actionable=data.abraj.rows.find(r=>r.kind==='materials'&&!r.closed_at&&r.status!=='closed');
 await page.evaluate(r=>showLpoDoc(r),actionable);
 const order=['dn','inv'];
 for(const kind of order){
   const plain=page.locator(`#lpoDocActions [data-doc="${kind}"]`);
   const next=page.locator(`#lpoDocActions [data-next="${kind==='dn'?'deliver':'invoice'}"]`);
   await ((await plain.count())?plain:next).click();
 }
 assert.deepEqual((await page.evaluate(()=>window.created)).map(r=>r.kind),order);
 const link=page.locator('[data-linked-kind="inv"]').first();
 if(await link.count()){const no=await link.getAttribute('data-linked-no');await link.click();assert.equal(await page.evaluate(()=>window.openedInvoice),no);}
 // Each dismissal route must retain dirty text until the user discards it.
 for(const route of ['escape','close','cancel','backdrop']){
   await page.locator('#lpoDocSide [data-doc="edit"]').first().click();await page.waitForSelector('#lpoFNotes');
   await page.locator('#lpoFNotes').fill('Keep this '+route);
   if(route==='escape')await page.keyboard.press('Escape');
   if(route==='close')await page.locator('.lpo-form-popup .popup-close').click();
   if(route==='cancel')await page.locator('.lpo-form-popup').getByRole('button',{name:'Cancel',exact:true}).click();
   if(route==='backdrop')await page.locator('.popup-backdrop').click({position:{x:5,y:5}});
   await page.getByRole('button',{name:'Keep editing',exact:true}).click();
   await page.waitForTimeout(250);
   assert.equal(await page.locator('#lpoFNotes').inputValue(),'Keep this '+route);
   await page.keyboard.press('Escape');await page.getByRole('button',{name:'Discard changes',exact:true}).click();
   await page.waitForSelector('#lpoFNotes',{state:'detached'});
 }
 // Rental billing and period-confirmation use the shared popup and bridge.
 const rental=data.abraj.rows.find(r=>r.kind==='rental'&&!r.closed_at&&r.status!=='closed');
 await page.evaluate(file=>window.auditFile=file,{ok:true,is_pdf:true,data_uri:'data:application/pdf;base64,'+fs.readFileSync(rental.file_path).toString('base64'),filename:path.basename(rental.file_path),path:rental.file_path});
 // The month ledger (2026-10-06): a missed month invoices with its own service dates;
 // a month that already has an invoice asks before a second one.
 await page.evaluate(()=>window.auditLedger={ok:true,term_months:3,months_total:3,months_invoiced:1,invoices_not_sent:1,months:[
   {month:'2026-08',label:'August 2026',count:0,invoices:[],expected:true,state:'missed',start:'2026-08-12',end:'2026-09-11',covers:'12 Aug - 11 Sep 2026'},
   {month:'2026-09',label:'September 2026',count:1,invoices:['INV-REVIEW'],expected:true,state:'invoiced',start:'2026-09-12',end:'2026-10-11',covers:'12 Sep - 11 Oct 2026'},
   {month:'2026-10',label:'October 2026',count:0,invoices:[],expected:true,state:'current',start:'2026-10-12',end:'2026-11-11',covers:'12 Oct - 11 Nov 2026'}]});
 await page.evaluate(r=>showLpoDoc(r),rental);
 await page.locator('[data-month-index="0"]').click();
 assert.equal(await page.evaluate(()=>window.created.at(-1).p.start),'2026-08-12');
 assert.equal(await page.locator('[data-month-index="1"]').count(),0,'an invoiced month offered another invoice');
 assert.ok(/Missed/.test(await page.locator('#lpoDocSide').innerText()),'a missed month must say Missed');
 await page.screenshot({path:BASE+'/billing-review.png'});
 await page.waitForTimeout(300);
 await page.evaluate(()=>{lpoState.rows=window.auditRows;return showLpos()});
 console.log('Add workflow: list ready');
 await page.locator('#btnLpoNew').click();
 await page.waitForSelector('#lpoFClient');
 console.log('Add workflow: form ready');
 await page.locator('#lpoFClient').fill('HDEC');
 await page.locator('#lpoFNo').fill('AUDIT-NEW-001');
 await page.locator('#lpoFTotal').fill('50');
 await page.locator('#lpoAddLine').click();
 await page.locator('#lpoLinesBody input[data-f="item"]').fill('Audit item');
 await page.locator('#lpoLinesBody input[data-f="qty"]').fill('2');
 await page.locator('#lpoLinesBody input[data-f="rate"]').fill('25');
 await page.getByRole('button',{name:'Add',exact:true}).click();
 await page.waitForFunction(()=>window.savedPayloads.length===2);
 findings.add=await page.evaluate(()=>window.savedPayloads[1]);
 assert.equal(findings.add.rows.length,1);
 assert.equal(findings.add.rows[0].qty,2);
 console.log('PASS: Add LPO reads entered header and item values before closing.');
 findings.errors=errors;
 fs.writeFileSync(BASE+'/ui-results.json',JSON.stringify(findings,null,2));
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS: actual LPO list, two-page PDF, notes/items save, all four dirty-dismiss routes, native-currency label, scoped links, create actions, invoicing a missed month, Add, light/dark and 1024px layout.');
 await browser.close();
})().catch(async e=>{console.error(e);await auditBrowser?.close();process.exitCode=1});
