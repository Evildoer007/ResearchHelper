/* Independent series contracts: no supplier, model, store or calculation. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{},Set,Map,Date,BigInt};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),box);
const D=box.window.RHResearchData;
const task=()=>({id:'current',version:2,source_records:[{id:'S',name:'原始记录'}],draft_report:{facts:['2026-10-26','2026-10-27','2026-10-28','2026-10-29'].flatMap((period,i)=>[{id:'P'+i,code:'TEST.SH',field:'close',raw_value:[0,-2,1.5,3][i],unit:'元',period,artifact_id:'S'},{id:'E'+i,code:'TEST.SH',field:'ths_pe_ttm_stock',raw_value:20+i/10,unit:'倍',period,artifact_id:'S'}])}});
const group=t=>D.groups(D.observations(t.draft_report.facts),t)[0];
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
test('two metrics retain distinct units, original values and source keys',()=>{const t=task(),data=D.seriesData(group(t));assert.equal(data.series.length,2);assert.equal(data.series[0].unit,'元');assert.equal(data.series[1].unit,'倍');assert.equal(data.series[0].points[0].value,0);assert.equal(data.series[0].points[1].value,-2);assert.equal(data.series[0].points[2].raw,'1.5');assert.equal(data.series[0].points[2].key,'draft:P2')});
test('a missing observation stays null and does not become a fabricated zero',()=>{const t=task();t.draft_report.facts=t.draft_report.facts.filter(f=>f.id!=='P1');const data=D.seriesData(group(t));assert.equal(data.series[0].points[1],null);assert.equal(data.periods.length,4)});
test('unknown units stay unknown rather than becoming currency or multiples',()=>{const t=task();t.draft_report.facts.forEach(f=>delete f.unit);const data=D.seriesData(group(t));assert.equal(data.series[0].unit,'单位待核对');assert.equal(data.series[1].unit,'单位待核对')});
test('ambiguous duplicate observations remain entirely in the source table',()=>{const t=task();t.draft_report.facts.push({...t.draft_report.facts[0],id:'other',raw_value:99});const g=group(t);assert.equal(D.seriesData(g),null);const html=D.grid(g,t,{esc});assert.match(html,/draft:other/);assert.doesNotMatch(html,/data-evidence-view/)});
test('invalid periods, two periods and unsafe integer magnitudes stay tables',()=>{for(const mode of ['date','count','number']){const t=task();if(mode==='date')t.draft_report.facts[0].period='2026-02-30';if(mode==='count')t.draft_report.facts=t.draft_report.facts.slice(0,4);if(mode==='number')t.draft_report.facts[0].raw_value='9007199254740993';assert.equal(D.seriesData(group(t)),null,mode)}});
test('different objects retain separate labels when the query has no code',()=>{const t=task();t.draft_report.facts=t.draft_report.facts.map((f,i)=>({...f,code:undefined,object:i%2?'对象B':'对象A',field:'close'}));const data=D.seriesData(group(t));assert.match(data.series[0].label,/对象A/);assert.match(data.series[1].label,/对象B/)});
test('trend markup remains bound to the task and all original table actions',()=>{const t=task(),before=JSON.stringify(t),html=D.grid(group(t),t,{esc});assert.match(html,/data-evidence-key=/);assert.match(html,/data-source-task="current"/);assert.match(html,/data-source-version="2"/);assert.equal((html.match(/data-observation=/g)||[]).length,8);assert.match(html,/data-evidence-view="chart"/);assert.equal(JSON.stringify(t),before)});

const nav='ths_unit_nvnf_fund';
function market(){
 const days=['2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'];
 const codes='510300.SH',binding={indicator:nav,indiparams:['']};
 const request=(id,startdate,enddate,functionpara,indipara)=>({id,source:'ifind',operation:'date_sequence',
  parameters:{codes,startdate,enddate,functionpara,indipara}});
 return {id:'market',version:2,source_records:[
  request('S1','2026/10/12','20261013',{Fill:'Blank',block:'A'},[binding,{indicator:'volume',indiparams:[]}]),
  request('S2','2026-10-14','2026-10-16',{block:'A',Fill:'Blank'},[structuredClone(binding)]),
 ],draft_report:{facts:days.map((period,index)=>({id:'D'+index,code:codes,field:nav,label:'单位净值',
  raw_value:index,unit:'元',period,axis_role:'time',series_basis:'legacy-series',artifact_id:index<2?'S1':'S2'}))}};
}
const chartValues=data=>Array.from(data.series,row=>Array.from(row.points,point=>point?.value??null));

test('compatible retained requests share a trend across windows and field selections',()=>{
 const t=market();
 const data=D.seriesData(group(t));
 assert.deepEqual(chartValues(data),[[0,1,2,3,4]]);
 assert.equal(data.series[0].label,'单位净值');
 assert.equal(data.series[0].points[4].key,'draft:D4');
});

test('legacy series separate different field arguments and isolate unknown original parameters',()=>{
 const t=market();
 t.source_records[1].parameters.indipara[0].indiparams=['other'];
 const read=()=>D.seriesData(group(t));
 assert.deepEqual(chartValues(read()),[[0,1,null,null,null],[null,null,2,3,4]]);
 assert.notEqual(read().series[0].label,read().series[1].label);
 t.source_records[1].parameters.indipara[0].indiparams=null;
 assert.deepEqual(chartValues(read()),[[0,1,null,null,null],[null,null,2,3,4]]);
 assert.match(read().series[1].label,/请求口径未明确/);
 delete t.source_records[0].parameters.functionpara;
 assert.notEqual(read().series[0].label,read().series[1].label);
 assert.equal((D.grid(group(t),t,{esc}).match(/data-draft-source=/g)||[]).length,5);
});

test('explicit source gaps stay in the table and axis, and omitted days are not added',()=>{
 const t=market(),fact=t.draft_report.facts[2];
 fact.period='2026/10/14';
 fact.value=999;
 for(const raw of [null,'','-','--','N/A','null']){
  fact.raw_value=raw;
  const g=group(t),data=D.seriesData(g);
  assert.equal(data.periods[2],'2026-10-14');
  assert.deepEqual(chartValues(data),[[0,1,null,3,4]]);
  assert.match(D.grid(g,t,{esc}),/data-draft-source="D2"/);
 }
 t.draft_report.facts.splice(2,1);
 assert.equal(D.seriesData(group(t)).periods.includes('2026-10-14'),false);
});
