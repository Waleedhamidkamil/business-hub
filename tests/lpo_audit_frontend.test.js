const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname,'..');
const source = fs.readFileSync(process.argv[2] || path.join(root,'static/lpo.js'),'utf8');
const side={innerHTML:'',querySelectorAll:()=>[]};
// The create actions moved to the LPO screen's header on 2026-10-03.
const acts={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>null};
const context={console,window:{},document:{readyState:'loading',addEventListener(){},querySelectorAll:()=>[],
    getElementById:id=>id==='lpoDocSide'?side:id==='lpoDocActions'?acts:null},
    esc:s=>String(s??''),api:()=>context.bridge,toast:()=>{},safe:fn=>fn,setTimeout};
context.window=context;
context.bridge={list_lpos:async()=>context.window.lpoState.rows,get_lpo:async()=>null};
vm.createContext(context);vm.runInContext(source,context);
context.showLpos=async()=>{};
let failures=0;
async function check(name,fn){try{await fn();console.log('PASS '+name)}catch(e){failures++;console.error('FAIL '+name+': '+e.message)}}
(async()=>{
 await check('Canonical USD comparisons are labeled USD',()=>{
   const s=context.lpoProgress({kind:'materials',currency:'IQD',total:1400000,total_usd:1000,invoiced_usd:500});
   assert.ok(s.includes('$500.00')&&s.includes('$1,000.00'),s);
   assert.ok(!s.includes('IQD'),s);
 });
 await check('An explicit missing client cannot open another client',async()=>{
   context.window.lpoState.rows=[{client_name:'A',lpo_no:'SAME'},{client_name:'B',lpo_no:'SAME'}];
   let opened=false;context.window.showLpoDoc=async()=>opened=true;
   await context.window.openLpoByNo('SAME','C');assert.equal(opened,false);
 });
 await check('An ambiguous number requires a client',async()=>{
   let opened=false;context.window.showLpoDoc=async()=>opened=true;
   await context.window.openLpoByNo('SAME');assert.equal(opened,false);
 });
 await check('Alias-resolved stored identity opens the right record',async()=>{
   context.bridge.get_lpo=async()=>({client_name:'B',lpo_no:'SAME'});
   let opened=null;context.window.showLpoDoc=async r=>opened=r.client_name;
   await context.window.openLpoByNo('SAME','B legal name');assert.equal(opened,'B');
 });
 await check('Detail has actionable links and document creation',()=>{
   context.window.lpoState.fulfil={lines:[]};
   const lpo={client_name:'A',lpo_no:'ONE',kind:'materials',file_exists:true,
       invoices:['INV-1'],dns:['DN-1'],source_quote_no:'QUT-1',total:100,total_usd:100,invoiced_usd:50};
   context.renderLpoDocHeader(lpo);context.renderLpoDocSide(lpo);
   assert.match(acts.innerHTML,/data-doc="dn"/);assert.match(acts.innerHTML,/data-doc="inv"/);
   assert.match(side.innerHTML,/data-linked-kind="inv"/);assert.match(side.innerHTML,/data-linked-kind="dn"/);
   context.renderLpoDocHeader({client_name:'A',lpo_no:'ONE',kind:'materials',status:'closed',dns:[],invoices:[]});
   assert.match(acts.innerHTML,/data-doc="dn"/,
      'Financially complete orders can still need delivery after direct invoicing');
   assert.doesNotMatch(acts.innerHTML,/<button[^>]*data-doc="dn"[^>]*disabled/);
 });
 await check('Signature status does not invent a deadline',()=>{
   assert.ok(!context.lpoKpis([{file_exists:true,ack_exists:false}]).some(c=>/one day/.test(c.s)));
   assert.doesNotMatch(side.innerHTML,/within one day/);
 });
 await check('Search includes quotation, ERP and project references',()=>{
   context.window.lpoState.rows=[{client_name:'A',lpo_no:'ONE',source_quote_no:'QUT-X',project:'SITE-Y',erp_no:'ERP-Z'}];
   for(const q of ['qut-x','site-y','erp-z']){
     context.window.lpoState.search=q;assert.equal(context.visibleLpos().length,1,q);
   }
 });
 await check('Billing shows each month, its invoices, and a way to invoice a missed one',()=>{
   // The month ledger replaced service periods on 2026-10-06 (lpo_billing.months).
   context.window.lpoState.billing={ok:true,term_months:3,months_total:3,months_invoiced:1,months:[
      {month:'2026-08',label:'August 2026',count:0,invoices:[],expected:true,state:'missed',start:'2026-08-12',end:'2026-09-11'},
      {month:'2026-09',label:'September 2026',count:1,invoices:['INV-1'],expected:true,state:'invoiced'}]};
   context.renderLpoDocSide({client_name:'A',lpo_no:'ONE',kind:'rental',invoices:['INV-1']});
   assert.match(side.innerHTML,/Missed/);assert.match(side.innerHTML,/data-month-index="0"/);
   assert.match(side.innerHTML,/data-linked-no="INV-1"/);assert.doesNotMatch(side.innerHTML,/data-month-index="1"/);
 });
 console.log(`LPO audit frontend: ${8-failures}/8 passed`);process.exitCode=failures?1:0;
})().catch(e=>{console.error(e);process.exitCode=1});
