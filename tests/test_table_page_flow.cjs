/* A4 pagination uses rendered content size and restores the original DOM. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');let browser,source;
test.before(async()=>{source=await fs.readFile(path.join(root,'modules/visualrenderer/assets/table-pagination.js'),'utf8');browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu']});});
test.after(async()=>{await browser?.close();});
async function observe({count,width=741,text='原值保留',attributes='',prefix=520,caption=''}){
 const page=await browser.newPage({viewport:{width:1440,height:900}});let external=0;
 try{
  await page.route('**/*',route=>{external++;return route.abort();});
  const rows=Array.from({length:count},(_,index)=>`<tr data-record="${index}"><td id="original-${index}">ROW${index}END</td><td>${text}</td></tr>`).join('');
  await page.setContent(`<style>body{margin:0;font:12px/16px Arial}main{width:741px}.table-shell{width:${width}px}table{width:100%;border-collapse:collapse}td,th{padding:4px;border-bottom:1px solid #ddd}th:first-child{width:90px}.rh-print-table-parts{display:none}@media print{.rh-print-table-original{display:none!important}.rh-print-table-parts{display:block}.publication-table--short{break-inside:avoid}}</style><main><div style="height:${prefix}px"></div><div class="table-shell"><table ${attributes}>${caption}<thead><tr><th>记录</th><th>原值</th></tr></thead><tbody>${rows}</tbody></table></div></main>`);
  await page.addScriptTag({content:source});await page.emulateMedia({media:'print'});
  const result=await page.evaluate(()=>{
   const original=document.querySelector('main table'),before=original.outerHTML;
   RHTablePrint.prepare();
   const parts=[...document.querySelectorAll('.rh-print-table-parts table')];
   const result={whole:original.classList.contains('publication-table--short'),parts:parts.length,
    rows:parts.flatMap(table=>[...table.tBodies[0].rows].map(row=>Number(row.dataset.record))),
    freshPage:!!document.querySelector('.rh-print-table-parts.rh-print-table-new-page'),
    counts:parts.map(table=>table.tBodies[0].rows.length),captions:parts.map(table=>table.caption?.textContent||table.querySelector('.rh-print-series-caption')?.textContent||null),
    headers:parts.map(table=>table.tHead.rows.length),clonedIds:parts.flatMap(table=>[...table.querySelectorAll('[id]')]).length};
   const prepared=document.querySelector('main').outerHTML;RHTablePrint.prepare();result.idempotent=prepared===document.querySelector('main').outerHTML;
   RHTablePrint.restore();result.restored=original.outerHTML===before&&document.querySelectorAll('.rh-print-table-parts,.rh-print-table-original').length===0;
   RHTablePrint.prepare();RHTablePrint.restore();result.repeatRestored=original.outerHTML===before;
   return result;
  });
  assert.equal(external,0);assert(result.idempotent&&result.restored&&result.repeatRestored);assert.equal(result.clonedIds,0);return result;
 }finally{await page.close();}
}
test('a sixteen-row table fitting A4 stays whole without redundant headers',async()=>{
 const result=await observe({count:16});assert(result.whole);assert.equal(result.parts,0);
});
test('a genuine long table keeps all ninety-five ordered rows and a header per part',async()=>{
 const result=await observe({count:95});assert(!result.whole);assert(result.parts>=2);assert.deepEqual(result.rows,Array.from({length:95},(_,i)=>i));assert(result.headers.every(count=>count===1));
});
test('only the first part uses space for the table title',async()=>{
 const base=await observe({count:160,prefix:0});
 const titled=await observe({count:160,prefix:0,caption:'<caption style="height:180px">关键数据</caption>'});
 assert(titled.counts[0]<base.counts[0]);
 assert.equal(titled.counts[1],base.counts[1]);
 assert.deepEqual(titled.captions,['关键数据',...Array(titled.parts-1).fill(null)]);
 assert.deepEqual(titled.rows,base.rows);
});
test('eight tall rows are not treated as short solely because their row count is small',async()=>{
 const result=await observe({count:8,text:'长说明，原值与期间必须完整保留。'.repeat(80),attributes:'class="publication-table--short"'});assert(result.parts>=2);assert.deepEqual(result.rows,Array.from({length:8},(_,i)=>i));
});
test('narrow tables are measured at their real width',async()=>{
 const result=await observe({count:8,width:280,text:'长说明，原值与期间必须完整保留。'.repeat(20)});assert(!result.whole);assert(result.parts>=2);assert.deepEqual(result.rows,Array.from({length:8},(_,i)=>i));
});
test('unsupported merged cells preserve their original table',async()=>{
 const page=await browser.newPage();try{await page.setContent('<main><table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td colspan="2">原文不变</td></tr></tbody></table></main>');await page.addScriptTag({content:source});assert(await page.evaluate(()=>{const before=document.querySelector('table').outerHTML;RHTablePrint.prepare();RHTablePrint.restore();return before===document.querySelector('table').outerHTML;}));}finally{await page.close();}
});

test('a long series starts with a full page and repeats its identity caption',async()=>{
 const result=await observe({count:160,prefix:520,attributes:'class="report-series-table"',caption:'<caption style="height:40px">600519.SH收盘价，单位：元</caption>'});
 assert(result.freshPage);assert.equal(result.counts[0],result.counts[1]);
 assert(result.headers.every(count=>count===2));
 assert(result.captions.every(value=>value==='600519.SH收盘价，单位：元'));
 assert.deepEqual(result.rows,Array.from({length:160},(_,i)=>i));
});
test('a short series keeps its current page instead of creating a page break',async()=>{
 const result=await observe({count:8,prefix:900,attributes:'class="report-series-table"',caption:'<caption>单位：元</caption>'});
 assert(result.whole);assert(!result.freshPage);assert.equal(result.parts,0);
});
test('a repeated series caption consumes space in every print fragment',async()=>{
 const base=await observe({count:160,prefix:0,attributes:'class="report-series-table"'});
 const titled=await observe({count:160,prefix:0,attributes:'class="report-series-table"',caption:'<caption style="height:180px">单位：元</caption>'});
 assert(titled.counts.slice(0,-1).every(count=>count<base.counts[0]));
 assert(titled.captions.every(value=>value==='单位：元'));
 assert.deepEqual(titled.rows,base.rows);
});
