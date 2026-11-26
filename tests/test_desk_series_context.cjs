/* Statement scopes are requested parameters, never new financial approval. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{},Set,Map,Date,BigInt};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),box);
const D=box.window.RHResearchData;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fixture(types=['1','2']){
 const task={id:'scope-task',version:1,source_records:[],draft_report:{facts:[]}};
 for(const type of types)for(const [index,period] of ['20231231','20241231','20251231'].entries()){
  const id='source-'+type+'-'+period;
  task.source_records.push({id,task_id:task.id,research_version:1,source:'ifind',operation:'basic_data_service',parameters:{codes:'TEST.SH',indipara:[{indicator:'ths_revenue_stock',indiparams:[period,type]}]}});
  task.draft_report.facts.push({id:'fact-'+type+'-'+period,artifact_id:id,code:'TEST.SH',field:'ths_revenue_stock',label:'营业收入',period,period_basis:'请求D报告期',series_basis:'scope-'+type,raw_value:100*Number(type)+index,unit:'元'});
 }
 return task;
}
const group=task=>D.groups(D.observations(D.draftFacts(task)),task)[0];
const render=task=>D.grid(group(task),task,{esc});
test('consolidated and parent requests have distinct visible rows and chart names',()=>{
 const task=fixture(),before=JSON.stringify(task),html=render(task),data=D.seriesData(group(task));
 assert.match(html,/请求合并报表/);assert.match(html,/请求母公司报表/);
 assert.ok(data);assert.equal(data.series.length,2);
 assert.match(data.series[0].label,/请求合并报表/);assert.match(data.series[1].label,/请求母公司报表/);
 assert.deepEqual(Array.from(data.periods),['2023-12-31','2024-12-31','2025-12-31']);
 assert.deepEqual(Array.from(data.series[0].points,point=>point.value),[100,101,102]);
 assert.deepEqual(Array.from(data.series[1].points,point=>point.value),[200,201,202]);
 assert.equal(JSON.stringify(task),before);
});
test('adjusted scopes keep their requested meaning',()=>{
 const html=render(fixture(['3','4']));assert.match(html,/请求合并调整报表/);assert.match(html,/请求母公司调整报表/);
});
test('one scope has no redundant row annotation',()=>{
 const task=fixture(['1']),html=render(task),data=D.seriesData(group(task));
 const headings=[...html.matchAll(/<th scope="row">([\s\S]*?)<\/th>/g)].map(match=>match[1]).join('');
 assert.doesNotMatch(headings,/请求合并报表|请求口径未明确/);assert.equal(data.series[0].label,'营业收入');
 assert.equal(data.series[0].points.length,3);
});
test('a missing old series fingerprint still cannot merge two explicit requested scopes',()=>{
 const task=fixture();task.draft_report.facts.forEach(fact=>delete fact.series_basis);
 const data=D.seriesData(group(task));assert.ok(data);assert.equal(data.series.length,2);
 assert.match(render(task),/请求母公司报表/);
});
test('no scope is borrowed from unrelated fields, objects, dates or versions',()=>{
 for(const mode of ['field','code','date','version','source','operation','role','ambiguous']){
  const task=fixture();for(const source of task.source_records.filter(source=>source.id.startsWith('source-2-'))){
   if(mode==='field')source.parameters.indipara[0].indicator='ths_np_stock';
   if(mode==='code')source.parameters.codes='OTHER.SH';
   if(mode==='date')source.parameters.indipara[0].indiparams[0]='20220630';
   if(mode==='version')source.research_version=0;
   if(mode==='source')source.source='unregistered';
   if(mode==='operation')source.operation='unregistered';
   if(mode==='ambiguous')source.parameters.indipara.push({indicator:'ths_revenue_stock',indiparams:[source.parameters.indipara[0].indiparams[0],'1']});
  }
  if(mode==='role')task.draft_report.facts.filter(fact=>fact.id.startsWith('fact-2-')).forEach(fact=>fact.period_basis='发布日期');
  const html=render(task);assert.doesNotMatch(html,/请求母公司报表/,mode);
 }
});
test('missing or unknown report types remain unknown and readable',()=>{
 for(const type of ['', '9']){
  const task=fixture(['1','2']);task.source_records.filter(source=>source.id.startsWith('source-2-')).forEach(source=>source.parameters.indipara[0].indiparams[1]=type);
  const html=render(task);assert.match(html,/请求口径未明确/);assert.doesNotMatch(html,/请求母公司报表/);
 }
});
test('a source without explicit ordered parameters cannot infer a statement type',()=>{
 const task=fixture();task.source_records.filter(source=>source.id.startsWith('source-2-')).forEach(source=>{source.parameters={codes:'TEST.SH',functionpara:{C:'2'}}});
 const html=render(task);assert.match(html,/请求口径未明确/);assert.doesNotMatch(html,/请求母公司报表/);
});
test('unmatched dates do not create a series or change raw records',()=>{
 const task=fixture();task.draft_report.facts[0].period='2023-02-30';const before=JSON.stringify(task);
 assert.equal(D.seriesData(group(task)),null);assert.match(render(task),/2023.02.30/);assert.equal(JSON.stringify(task),before);
});
