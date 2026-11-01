/* Independent data/logic/provenance presentation invariants, no acquisition. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{},Set,Map,Date,BigInt};
for(const file of ['research-data','desk-overview','desk-workingpaper','desk-sources','desk-report','desk-tracking'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui',file+'.js'),'utf8'),box);
const D=box.window.RHResearchData,V=box.window.RHDeskViews;
const ctx={esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),cutoffLabel:v=>v,sourceRecordName:r=>r.name||r.id,unverifiedProposal:(_,r)=>r,processDetails:()=>'',knowledgeView:()=>'',displayAnswer:(_,q)=>q.answer,statusTag:s=>s,collectionWorkingpaper:()=>'',apiMode:false,reportProfile:()=> 'standard',reportURL:()=>'',reportVersionBusy:()=>false};
const fixture=()=>({id:'t',version:2,question:'解释某个事件',objects:['TEST'],cutoff:'2026-10-30T10:00:00+08:00',source_records:[{id:'S1',name:'公开数据',task_id:'t',research_version:2}],draft_report:{facts:[{id:'D1',code:'TEST',field:'profit',label:'利润',value:0,raw_value:0,period:'2025',unit:'元',artifact_id:'S1'}],calculations:[],logics:[{title:'分析判断',analysis:'机制仍待观察',data_refs:['D1']}]},tracking:[]});
test('summary counts only current task and version without upgrading source approval',()=>{
 const task=fixture();task.draft_report.facts.push({id:'foreign',task_id:'other',value:99},{id:'old',research_version:1,value:99});task.source_records.push({id:'old',research_version:1});
 assert.equal(D.summary(task).observations.length,1);assert.equal(D.summary(task).sources,1);assert.equal(D.summary(task).logics,1);
 const html=V.overview(task,ctx);assert.match(html,/1条记录/);assert.doesNotMatch(html,/已核验|已通过|独立核验通过/);
});
test('mechanism explanation can have analysis without an investment thesis',()=>{
 const task=fixture();delete task.draft_report;task.questions=[{id:'Q1',status:'已回答',answer:'公开材料中的说明'}];
 assert.match(V.overview(task,ctx),/1项分析/);assert.doesNotMatch(V.overview(task,ctx),/尚未形成分析/);
});
test('old delivery is labeled by its actual version and all output steps stay accessible',()=>{
 const task=fixture();task.delivery={version:1};const html=V.overview(task,ctx);
 assert.match(html,/版本1已保存/);assert.doesNotMatch(html,/版本2已保存/);
 for(const stage of [1,2,3,4])assert.match(html,new RegExp('data-stage="'+stage+'"'));
});
test('observation and zero calculation retain their exact source and method bindings',()=>{
 const task=fixture();const cell=D.cell(D.observations(task.draft_report.facts)[0],task,ctx);
 assert.match(cell,/data-draft-source="D1"/);assert.match(cell,/data-source-task="t"/);assert.match(cell,/data-source-version="2"/);assert.match(cell,/>0<\/button>/);
 assert.match(D.calcButton({id:'A1',label:'增长',value:0,unit:'%'},task,ctx),/data-draft-calculation="A1"/);
});
test('source inventory distinguishes repeated names using retained periods and metrics',()=>{
 const task=fixture();task.source_records.push({id:'S2',name:'公开数据'});task.draft_report.facts.push({...task.draft_report.facts[0],id:'D2',artifact_id:'S2',period:'2024'});
 const html=D.library(task,D.observations(task.draft_report.facts),ctx);
 assert.match(html,/2024/);assert.match(html,/2025/);assert.match(html,/利润/);assert.match(html,/data-source="S1"/);assert.match(html,/data-source="S2"/);
});
test('mixed draft and formal claims are visibly distinguished without hiding either',()=>{
 const task=fixture();task.theses=[{id:'T1',title:'正式判断',investment_meaning:'事实仍需区分',support:[],counter:[]}];
 const html=V.workingpaper(task,ctx);assert.match(html,/研究草稿/);assert.match(html,/正式论点/);assert.match(html,/分析判断/);assert.match(html,/正式判断/);
});
test('risk text does not become a registered tracking condition',()=>{
 const task=fixture();task.draft_report.logics[0].risk='收入下行风险';assert.match(V.workingpaper(task,ctx),/收入下行风险/);assert.doesNotMatch(V.tracking(task,ctx),/收入下行风险/);
});
test('rendering every view never mutates research or original input',()=>{
 const task=fixture(),before=JSON.stringify(task);for(const view of ['overview','sources','workingpaper','report','tracking'])V[view](task,ctx);assert.equal(JSON.stringify(task),before);
});

test('draft theory resolves explicit locked versions rather than retrieval or reference warnings',()=>{
 const task=fixture();task.input_hash='input';
 task.knowledge={entries:[{id:'K1',version:2,title:'现金流机制'},{id:'K2',version:1,title:'未引用知识'}],index:{K1:2,K2:1}};
 task.draft_report.knowledge_context={binding:{task_id:task.id,research_version:task.version,input_hash:task.input_hash,knowledge_hash:'kernel-bound'}};
 const logic=task.draft_report.logics[0];logic.knowledge_refs=[{id:'K1',version:2}];logic.reference_notes=['有一项引用待补充'];
 const local={...ctx,knowledgeView:knowledge=>knowledge.entries.map(entry=>'<p>'+entry.title+'</p>').join('')};
 const html=V.workingpaper(task,local),theory=html.match(/<details class="paper-theory[\s\S]*?<\/details>/)?.[0];
 assert.match(theory,/现金流机制/);assert.doesNotMatch(theory,/引用待补充/);assert.match(html,/引用待补充/);
 assert.doesNotMatch(html,/未引用知识/);
 logic.knowledge_refs=[{id:'K1',version:1}];assert.equal(D.knowledgeEntries(task,logic).length,0);
 logic.knowledge_refs=[];assert.doesNotMatch(V.workingpaper(task,local),/理论依据|现金流机制/);
});
test('a draft knowledge binding from another input cannot populate theory',()=>{
 const task=fixture();task.knowledge={entries:[{id:'K1',version:2}],index:{K1:2}};
 task.draft_report.knowledge_context={binding:{task_id:'other',research_version:task.version,knowledge_hash:'bound'}};
 task.draft_report.logics[0].knowledge_refs=[{id:'K1',version:2}];
 assert.deepEqual(JSON.parse(JSON.stringify(D.knowledgeEntries(task,task.draft_report.logics[0]))),[]);
});

test('a rejected source remains inspectable without being shown as a report input',()=>{
 const task=fixture();task.draft_source_issues=[{artifact_id:'S1',reason:'来源内容已变化'}];
 const before=JSON.stringify(task),html=D.library(task,[],ctx);
 assert.match(html,/data-source="S1"/);assert.match(html,/未用于报告/);
 assert.doesNotMatch(html,/未关联研究数值/);assert.equal(JSON.stringify(task),before);
});
