/* Exercise retained iFinD cells through the real original-viewer DOM. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const script=await fs.readFile(path.join(root,'products/app/frontend/source-viewer.js'),'utf8');

test('retained originals keep the source field and row without modifying the snapshot',async t=>{
 const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu']});
 try{
  const page=await browser.newPage();
  await page.route('**/*',route=>route.abort());
  await page.setContent('<main id="source"></main>');
  await page.addScriptTag({content:script});
  const record={kind:'api',source:'ifind',operation:'edb_service',snapshot:{errorcode:0,tables:[
   {id:['M002826730'],time:['2026-09-30','2026-08-31','2026-07-31'],value:['0.90',0,null],rtime:['2026-10-14 09:31:18','2026-09-09 09:31:29','2026-08-09 09:31:21'],index_name:['CPI:当月同比','CPI:当月同比','CPI:当月同比']}
  ]}};
  const mount=async (value=record,locator={})=>{
   await page.evaluate(({record,locator})=>{
    window.retainedOriginal=structuredClone(record);
    window.originalFingerprint=JSON.stringify(record);
    window.RHSourceViewer.mount(document.querySelector('#source'),{record:window.retainedOriginal,version:1,locator});
   },{record:value,locator});
  };
  const fields=()=>page.locator('.source-api-fields').innerText();
  await t.test('singleton ID and literal columns display as rows',async()=>{
   await mount();
   assert.equal(await page.locator('#source-row').getAttribute('max'),'3');
   assert.match(await fields(),/M002826730/);assert.match(await fields(),/0\.90/);
   assert.match(await fields(),/2026-10-14 09:31:18/);
   assert.doesNotMatch(await page.locator('#source').innerText(),/返回结构异常|不用于计算或投资判断/);
  });
  await t.test('keyboard row navigation preserves zero, null and source order',async()=>{
   await page.locator('#source-row').fill('2');await page.locator('#source-row').press('Tab');
   assert.match(await fields(),/2026-08-31/);assert.match(await fields(),/\b0\b/);
   await page.locator('[data-source-row=next]').click();
   assert.match(await fields(),/null/);assert.equal(await page.locator('[data-source-row=next]').isDisabled(),true);
   assert.equal(await page.evaluate(()=>JSON.stringify(window.retainedOriginal)===window.originalFingerprint),true);
  });
  await t.test('actual locator focuses the retained value',async()=>{
   await mount(record,{table:0,row:1,field:'value'});
   assert.equal(await page.locator('.source-field-target td').innerText(),'0');
   assert.equal(await page.locator('#source-row').inputValue(),'2');
   await page.locator('#source-row').fill('9');await page.locator('#source-row').press('Tab');
   assert.equal(await page.locator('#source-row').getAttribute('aria-invalid'),'true');
   assert.match(await fields(),/2026-08-31/);
  });
  await t.test('each original series remains independently reachable',async()=>{
   const multi=structuredClone(record);multi.snapshot.tables.push({id:'M999999999',time:['2026-12-31'],value:[987654321]});
   await mount(multi);await page.locator('#source-table').selectOption('1');
   assert.match(await fields(),/M999999999/);assert.match(await fields(),/987654321/);
   assert.equal(await page.locator('#source-row').getAttribute('max'),'1');
  });
  await t.test('misaligned columns disclose missing cells without inventing zero',async()=>{
   const malformed=structuredClone(record);malformed.snapshot.tables[0].value=['0.90'];
   await mount(malformed);assert.match(await page.locator('#source').innerText(),/返回结构异常/);
   await page.locator('[data-source-row=next]').click();assert.match(await fields(),/未提供/);
   await mount(malformed,{table:0,row:1,field:'value'});
   assert.match(await page.locator('.source-api-feedback').textContent(),/指定字段不存在/);
   assert.equal(await page.locator('.source-field-target').count(),0);
   await page.locator('[data-source-browse]').click();
   assert.equal(await page.locator('#source-row').inputValue(),'1');
   assert.equal(await page.locator('.source-field-target').count(),0);
  });
  await t.test('unknown operations do not reinterpret a flat economic shape',async()=>{
   await mount({...record,operation:'basic_data_service'});
   assert.equal(await page.locator('#source-row').isDisabled(),true);
   assert.match(await page.locator('#source').innerText(),/返回结构异常/);
  });
  await t.test('nested financial rows retain code and time',async()=>{
   await mount({kind:'api',source:'ifind',operation:'basic_data_service',snapshot:{errorcode:0,tables:[
    {thscode:'600519.SH',time:['2025-12-31'],table:{ths_revenue_stock:[120]}}
   ]}});
   assert.match(await fields(),/600519\.SH/);assert.match(await fields(),/2025-12-31/);assert.match(await fields(),/120/);
  });
  const quote=(time,table,operation='cmd_history_quotation')=>({kind:'api',source:'ifind',operation,
   snapshot:{errorcode:0,tables:[{thscode:'600519.SH',time,table}]}});
  const quoteFields=page.locator('.source-field-target td');
  await t.test('ETF market and NAV locations open the actual exchange row',async()=>{
   const quote={kind:'api',source:'official',operation:'etf_daily_quotes',parameters:{ts_code:'510300.SH'},
    snapshot:{code:'510300.SH',rows:[{row:40,day:'2026-11-19',close:'4.063',turnover:'19000000000'}]}};
   const nav={kind:'api',source:'official',operation:'etf_nav_history',parameters:{ts_code:'159925.SZ'},
    snapshot:{code:'159925.SZ',rows:[{row:1,day:'2026-11-20',nav:'3.96'},{row:2,day:'2026-11-19',nav:'3.9847'}]}};
   const locate=async (record,row,field)=>mount(record,{kind:'exchange_etf_trading_cell',row,field});
   await locate(quote,40,'etf_exchange_turnover');
   assert.equal(await quoteFields.textContent(),'19000000000');
   assert.match(await page.locator('.source-api-summary').textContent(),/交易所快照已留存/);
   await locate(nav,2,'etf_exchange_nav');
   assert.equal(await quoteFields.textContent(),'3.9847');
   assert.match(await fields(),/2026-11-19/);
   await locate(nav,3,'etf_exchange_nav');
   assert.match(await page.locator('.source-api-feedback').textContent(),/来源定位无效/);
   assert.equal(await quoteFields.count(),0);
  });
  await t.test('event source viewer locates a close by the original table and row',async()=>{
   const prices={kind:'api',source:'official',operation:'hsi_close_history',snapshot:{rows:[
    {table:0,row:80,code:'HSTECH.HI',date:'2026-10-28',close:'5321.17'},
    {table:1,row:80,code:'HSI.HI',date:'2026-10-28',close:'26022.73'}
   ]}};
   await mount(prices,{table:1,row:80,field:'close'});
   assert.equal(await quoteFields.textContent(),'26022.73');
   assert.match(await fields(),/HSI\.HI/);
   await mount(prices,{table:0,row:79,field:'close'});
   assert.match(await page.locator('.source-api-feedback').textContent(),/来源定位无效/);
  });
  await t.test('one-day historical quotation retains scalar string, zero and explicit null',async()=>{
   const single=quote(['2026-10-14'],{close:'107.2500',open:0,low:null});
   for(const [field,literal] of [['close','107.2500'],['open','0'],['low','null']]){
    await mount(single,{table:0,row:0,field});
    assert.equal(await quoteFields.textContent(),literal);
    assert.equal(await page.locator('#source-row').getAttribute('max'),'1');
    assert.equal(await page.locator('.source-api-feedback').textContent(),'');
    assert.match(await fields(),/2026-10-14/);
    assert.match(await page.locator('.source-api-summary').textContent(),/业务成功/);
   }
   assert.equal(await page.evaluate(()=>JSON.stringify(window.retainedOriginal)===window.originalFingerprint),true);
  });
  await t.test('an unaligned scalar is readable once and has no row locator',async()=>{
   const dates=['2026-10-13','2026-10-14'];
   const mixed=quote(dates,{close:[106,107],open:999,volume:[42]});
   await mount(mixed,{table:0,row:1,field:'close'});
   assert.equal(await quoteFields.textContent(),'107');
   assert.doesNotMatch(await page.locator('.source-api-fields > .table-wrap').textContent(),/999/);
   const separate=page.locator('.source-unbound-fields');
   await separate.locator('summary').click();
   assert.deepEqual(await separate.locator('td').allTextContents(),['999']);
   for(const field of ['open','volume']){
    await mount(mixed,{table:0,row:1,field});
    assert.match(await page.locator('.source-api-feedback').textContent(),/指定字段不存在/);
    assert.equal(await quoteFields.count(),0);
   }
   const sequence=quote(dates.slice(0,1),{ths_unit_nvnf_fund:4.28},'date_sequence');
   await mount(sequence,{table:0,row:0,field:'ths_unit_nvnf_fund'});
   assert.match(await page.locator('.source-api-feedback').textContent(),/指定字段不存在/);
   await page.locator('[data-source-browse]').click();
   assert.deepEqual(await separate.locator('td').allTextContents(),['4.28']);
   const noRows=quote([], {close:107});
   await mount(noRows,{table:0,row:0,field:'close'});
   await page.locator('[data-source-browse]').click();
   assert.equal(await page.locator('#source-row').isDisabled(),true);
   assert.deepEqual(await separate.locator('td').allTextContents(),['107']);
  });
  await t.test('a nested time field cannot overwrite the retained table date',async()=>{
   await mount(quote(['2026-10-14'],{close:107,time:['2026-10-31']}),{table:0,row:0,field:'close'});
   const time=page.locator('.source-api-fields th[title=time]');
   assert.deepEqual(await time.allTextContents(),['表级time','time']);
   assert.deepEqual(await time.locator('..').locator('td').allTextContents(),['2026-10-14','2026-10-31']);
   assert.equal(await quoteFields.textContent(),'107');
  });
  await t.test('catalogue links carry the same task and version as the retained original',async()=>{
   const catalogue={id:'catalogue',task_id:'doc-task',research_version:2,source:'official',operation:'release_catalog',
    snapshot:{entries:[{id:'release-1',title:'本版发布'},{id:'release-2',title:'旧版留存'}]}};
   const original={id:'original',task_id:'doc-task',research_version:2,source:'official',operation:'release_original',
    kind:'material',parameters:{catalog_id:catalogue.id,release_id:'release-1'},
    snapshot:{official_origin:{id:'release-1'},status:'已解析'}};
   const old={...original,id:'old-original',research_version:1,parameters:{catalog_id:catalogue.id,release_id:'release-2'},
    snapshot:{official_origin:{id:'release-2'},status:'已解析'}};
   await page.evaluate(({record,relatedRecords})=>window.RHSourceViewer.mount(document.querySelector('#source'),
    {record,version:2,relatedRecords}),{record:catalogue,relatedRecords:[original,old]});
   const button=page.locator('#source [data-source="original"]');
   assert.equal(await button.getAttribute('data-source-task'),'doc-task');
   assert.equal(await button.getAttribute('data-source-version'),'2');
   assert.equal(await page.locator('#source [data-source="old-original"]').count(),0);
  });
 }finally{await browser.close();}
});
