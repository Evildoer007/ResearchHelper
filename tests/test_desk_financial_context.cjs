/* Financial request context must remain visible even when units differ. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{},Set,Map,Date,BigInt};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),box);
const D=box.window.RHResearchData;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fixture({units=['元','原始单位待核对'],fields=['ths_revenue_stock','ths_revenue_stock'],types=['1','2'],codes=['TEST.SH','TEST.SH']}={}){
 const task={id:'financial-context-task',version:1,source_records:[],draft_report:{facts:[]}};
 for(let scope=0;scope<2;scope++)for(const [index,period] of ['20231231','20241231','20251231'].entries()){
  const id='source-'+scope+'-'+period,field=fields[scope];
  task.source_records.push({id,task_id:task.id,research_version:1,source:'ifind',operation:'basic_data_service',parameters:{codes:codes[scope],indipara:[{indicator:field,indiparams:[period,types[scope]]}]}});
  task.draft_report.facts.push({id:'fact-'+scope+'-'+period,artifact_id:id,code:codes[scope],field,label:field==='ths_np_stock'?'净利润':'营业收入',period,period_basis:'请求D报告期',series_basis:'scope-'+scope,raw_value:100*(scope+1)+index,unit:units[scope]});
 }
 return task;
}
const groups=task=>D.groups(D.observations(D.draftFacts(task)),task);
const render=task=>groups(task).map(group=>D.grid(group,task,{esc})).join('');
const headings=html=>[...html.matchAll(/<th scope="row">([\s\S]*?)<\/th>/g)].map(match=>match[1]);
test('different units do not hide explicit consolidated and parent request scopes',()=>{
 const task=fixture(),before=JSON.stringify(task),html=render(task),rows=headings(html),series=D.seriesData(groups(task)[0]).series;
 assert.equal(rows.length,2);assert.match(rows[0],/请求合并报表/);assert.match(rows[1],/请求母公司报表/);
 assert.match(rows[0],/>元</);assert.match(rows[1],/单位待核对/);
 assert.match(series[0].label,/请求合并报表/);assert.match(series[1].label,/请求母公司报表/);
 assert.equal(series[0].unit,'元');assert.equal(series[1].unit,'单位待核对');
 assert.deepEqual(Array.from(series[0].points,p=>p.value),[100,101,102]);assert.deepEqual(Array.from(series[1].points,p=>p.value),[200,201,202]);
 assert.equal(JSON.stringify(task),before);
});
test('two explicit scales keep both literal units and statement names',()=>{
 const task=fixture({units:['元','万元']}),rows=headings(render(task));
 assert.match(rows[0],/请求合并报表/);assert.match(rows[1],/请求母公司报表/);assert.match(rows[0],/>元</);assert.match(rows[1],/>万元</);
});
test('distinct metrics from different statements name their own requested scopes',()=>{
 const task=fixture({fields:['ths_revenue_stock','ths_np_stock']}),rows=headings(render(task));
 assert.equal(rows.length,2);assert.match(rows[0],/营业收入[\s\S]*请求合并报表/);assert.match(rows[1],/净利润[\s\S]*请求母公司报表/);
});
test('unresolved financial rows are not presented as the other rows statement',()=>{
 const task=fixture({fields:['ths_revenue_stock','ths_np_stock']});
 task.source_records.filter(row=>row.id.startsWith('source-1-')).forEach(row=>row.parameters.indipara[0].indiparams[1]='9');
 const rows=headings(render(task));assert.match(rows[0],/请求合并报表/);assert.match(rows[1],/请求口径未明确/);assert.doesNotMatch(rows[1],/请求合并报表|请求母公司报表/);
});
test('a single statement with different units gains no redundant scope text',()=>{
 const task=fixture({types:['1','1']}),rows=headings(render(task));assert.equal(rows.length,2);
 assert.ok(rows.every(row=>!row.includes('请求合并报表')&&!row.includes('请求口径未明确')));
});
test('a single statement across multiple metrics remains compact',()=>{
 const task=fixture({types:['1','1'],units:['元','元'],fields:['ths_revenue_stock','ths_np_stock']});
 assert.ok(headings(render(task)).every(row=>!row.includes('请求合并报表')&&!row.includes('请求口径未明确')));
});
test('different objects do not create a shared statement ambiguity',()=>{
 const task=fixture({codes:['TEST.SH','OTHER.SH']});assert.equal(groups(task).length,2);
 assert.ok(headings(render(task)).every(row=>!row.includes('请求合并报表')&&!row.includes('请求母公司报表')));
});
test('request scope cannot cross a mismatched field or period',()=>{
 for(const mode of ['field','period']){
  const task=fixture({fields:['ths_revenue_stock','ths_np_stock']});
  task.source_records.filter(row=>row.id.startsWith('source-1-')).forEach(row=>{if(mode==='field')row.parameters.indipara[0].indicator='ths_operating_cost_stock';else row.parameters.indipara[0].indiparams[0]='20210630'});
  const rows=headings(render(task));assert.match(rows[0],/请求合并报表/);assert.match(rows[1],/请求口径未明确/);assert.doesNotMatch(rows[1],/请求母公司报表/);
 }
});
test('identical requested scopes in separate observation kinds remain independent',()=>{
 const task=fixture();let observations=D.observations(D.draftFacts(task));observations=observations.map(item=>item.fact.id.startsWith('fact-1-')?{...item,kind:'evidence'}:item);
 const panels=D.groups(observations,task);assert.equal(panels.length,2);
 for(const panel of panels)assert.ok(headings(D.grid(panel,task,{esc})).every(row=>!row.includes('请求合并报表')&&!row.includes('请求母公司报表')));
});

test('observation purpose owns readings, missing fields and a single source entry',()=>{
 const task=fixture({types:['1','1']});
 const record={id:'market-source',task_id:task.id,research_version:1,kind:'api',source:'ifind',
  data_need_ids:['market'],operation:'cmd_history_quotation',parameters:{codes:'TEST.SH',indicators:'close,pe_ttm'}};
 const macro={...record,id:'macro-source',operation:'edb_service',data_need_ids:['macro'],
  parameters:{indicators:'M1,M2'}};
 const validation={...record,id:'identity',operation:'get_thscode',data_need_ids:[]};
 task.source_records.push(record,macro,validation);
 task.data_need_specs=[{id:'market',operation:record.operation,parameters:record.parameters},
  {id:'macro',operation:macro.operation,parameters:macro.parameters}];
 for(const [id,artifact_id,code,field,raw_value,unit] of [
  ['price',record.id,'TEST.SH','close',100,'元'],['valuation',record.id,'TEST.SH','pe_ttm',20,'倍'],
  ['macro-1',macro.id,'M1','value',0,'%'],
 ])task.draft_report.facts.push({id,artifact_id,code,field,raw_value,unit,period:'2026-11-20'});
 const items=D.observations(D.draftFacts(task));
 assert.deepEqual(Array.from(D.groups(items,task),item=>item.category),['market','financial','valuation','macro']);
 const missing=D.categoryNeeds(task,items);
 assert.deepEqual(Array.from(missing.keys()),['macro']);
 assert.deepEqual(Array.from(missing.get('macro').values(),item=>item.label),['M2']);
 const catalogue=D.sourceCatalog(task,items,{esc,sourceRecordName:row=>row.operation});
 const sections=Array.from(catalogue.sections.values()).join('');
 assert.equal((sections.match(/data-source="market-source"/g)||[]).length,1);
 assert.match(catalogue.diagnostics,/data-source="identity"/);
});
