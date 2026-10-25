const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm'), assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
function fn(file,name){
    const src=fs.readFileSync(path.join(root,file),'utf8').replace(/\r\n/g,'\n');
    const a=src.indexOf(`function ${name}(`), b=src.indexOf('\n}\n',a);
    assert.ok(a>=0&&b>a,`Cannot isolate ${name}`);
    return src.slice(src.lastIndexOf('\n',a)+1,b+2);
}
function fixture(seed){
    const ctx={console,document:{getElementById:()=>null},invState:{schema:{primary_key:'item'},sellCurrency:'IQD',sourceQuote:'OLD',sourceDn:{no:'OLD'},sourcePo:{no:'OLD'}},
        dnState:{includePrices:true,sourceQuote:'OLD',sourcePo:{no:'OLD'}},calls:[],seed,toast(){},updateLpoWarning(){},updateInvLpoWarning(){},
        editableInvColumns:()=>[],dnColumns:()=>[],invSellPeg:()=>1400,dnPegSync:()=>1400,
        fullRenderInvRows(){},fullRenderDnRows(){},addInvRow(){},addDnRow(){},updateInvTotals(){},updateDnTotals(){},
        dnSyncPricesToggle(){},buildDnThead(){},filledInvRows:()=>[],filledDnRows:()=>[],lpoSeedToast:()=>'',
        smartMapRow:r=>r,lpoSeedForDocument:async(c,n,k)=>{ctx.calls.push(k);return ctx.seed}};
    ctx.window=ctx;vm.createContext(ctx);
    vm.runInContext(fn('static/app.js','priceFromMemory')+'\n'+fn('static/inv.js','adoptLpoIntoInv')+'\n'+fn('static/dn.js','adoptLpoIntoDn'),ctx);
    return ctx;
}
(async()=>{
    const seed={ok:true,currency:'IQD',quantity_policy:'enter_period_quantity',rows:[{item:'Rental',qty:'',rate:'14000',rate_ccy:'IQD'}]};
    const ctx=fixture(seed);
    await ctx.adoptLpoIntoInv('Client','PO',{period:{start:'2026-08-12',end:'2026-09-11'}});
    assert.equal(Number(ctx.invState.rows[0].rate),14000);
    assert.equal(ctx.invState.rows[0].qty,'');
    assert.match(ctx.invState.rows[0].description,/2026-08-12 to 2026-09-11/);
    assert.equal(ctx.invState.sourceQuote,'');assert.equal(ctx.invState.sourceDn,null);assert.equal(ctx.invState.sourcePo,null);
    ctx.invState.sellCurrency='USD';await ctx.adoptLpoIntoInv('Client','PO');
    assert.equal(Number(ctx.invState.rows[0].rate),10);
    await ctx.adoptLpoIntoDn('Client','PO');
    assert.equal(Number(ctx.dnState.rows[0].rate),10);
    assert.equal(ctx.dnState.sourceQuote,'');assert.equal(ctx.dnState.sourcePo,null);
    assert.deepEqual(ctx.calls,['inv','inv','dn']);
    seed.rows[0].rate='14,000';await ctx.adoptLpoIntoDn('Client','PO');
    assert.equal(Number(ctx.dnState.rows[0].rate),10,'Formatted IQD rates must not be truncated at the comma');
    await ctx.adoptLpoIntoInv('Client','PO');assert.equal(Number(ctx.invState.rows[0].rate),10);
    ctx.filledInvRows=()=>[{}];ctx.invState.dirty=true;ctx.showModal=async()=> 'cancel';
    const before=ctx.invState.rows;await ctx.adoptLpoIntoInv('Client','PO',{confirm:true});
    assert.equal(ctx.invState.rows,before,'Keep mine retains the existing rows');
    console.log('PASS: LPO handoff currencies, target policies, blank rental quantities, service dates and overwrite protection');
})().catch(e=>{console.error(e);process.exitCode=1});
