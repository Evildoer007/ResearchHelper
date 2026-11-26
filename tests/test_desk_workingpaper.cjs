'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sandbox = {window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8'),sandbox);
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/desk-workingpaper.js'),'utf8'), sandbox);
const render = sandbox.window.RHDeskViews.workingpaper;
const esc = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const ctx = {esc,sourceRecordName:item=>item.id,statusTag:value=>esc(value),knowledgeView:value=>value.entries.map(item=>esc(item.id)).join(''),displayAnswer:(_,q)=>q.answer,
  calculationButton:item=>'<button data-calculation="'+esc(item.id)+'">计算</button>',
  sourceRecordButtons:task=>[...(task.materials||[]),...(task.source_records||[])].map(item=>'<button data-source="'+esc(item.id)+'">原件</button>').join(''),
  draftFactsTable:task=>task.draft_report.facts.map(item=>'<td>'+esc(item.id)+'</td>').join(''),draftArithmetic:rows=>rows.map(item=>esc(item.id)).join(''),
  collectionWorkingpaper:task=>(task.materials||[]).map(item=>esc(item.id)).join(''),gapView:()=>''};
const task = {id:'T',version:1,theses:[{id:'T1',title:'投资逻辑',investment_meaning:'条件判断',judgment:'待验证',support:['E1'],question_ids:['Q1'],counter:['MISSING']}],
  questions:[{id:'Q1',question:'驱动',answer:'解释',status:'已回答',dependencies:['Q2'],calculation_refs:['C1']},{id:'Q2',question:'事实',answer:'原值',status:'已回答',dependencies:['Q1'],refs:['E1']}],
  evidence:[{id:'E1',text:'数值',raw_value:0,source:'原始来源',artifact_id:'A1'}],materials:[{id:'A1'}],calculations:[{id:'C1',input_refs:['E1']}],
  knowledge:{entries:[{id:'K1'},{id:'UNRELATED'}],selection_reasons:{K1:{question_ids:['Q2']}}}};
const before = JSON.stringify(task);
let html = render(task,ctx);
assert.match(html,/data-evidence="E1"/);assert.match(html,/data-calculation="C1"/);assert.match(html,/source-citations[\s\S]*A1/);
assert.match(html,/K1/);assert.doesNotMatch(html,/UNRELATED/);assert.match(html,/MISSING未找到/);assert.match(html,/>0<\/button>/);
assert.equal(JSON.stringify(task),before);
html=render({questions:[{id:'F1',kind:'fact',question:'事实核对',status:'已回答',answer:'事实回答',refs:[]}]},ctx);
assert.match(html,/事实回答/);assert.doesNotMatch(html,/策略逻辑|投资含义|理论依据/);
html=render({questions:[{id:'M1',kind:'mechanism',question:'机制',status:'已回答',answer:'机制解释'}]},ctx);
assert.match(html,/机制解释/);assert.doesNotMatch(html,/策略逻辑/);
html=render({},ctx);assert.match(html,/底稿尚未形成/);assert.doesNotMatch(html,/已核验<|已通过核验/);
const draft={theses:[],draft_report:{conclusion:'不重复报告结论',logics:[{title:'现金流',analysis:'现金流机制分析',implication:'有待检验',data_refs:['D1'],calculation_refs:['DC1'],risk:'缺少可比口径'}],facts:[{id:'D1',label:'营业收入',raw_value:10,period:'2025',unit:'元'},{id:'D2',label:'经营现金流',raw_value:20,period:'2025',unit:'元'}],calculations:[{id:'DC1',data_refs:['D1']}]}};
html=render(draft,ctx);assert.match(html,/data-reading-key="draft-logic:0"/);assert.match(html,/有待检验/);assert.match(html,/未关联的取数/);
assert.equal((html.match(/data-observation="draft:D1"/g)||[]).length,1);assert.equal((html.match(/data-observation="draft:D2"/g)||[]).length,1);assert.match(html,/>10<\/span>/);assert.match(html,/>20<\/span>/);
assert.doesNotMatch(html,/不重复报告结论|策略逻辑/);assert.doesNotMatch(html,/已通过核验|已核验/);
const originals={id:'DOC',version:2,materials:[
  {id:'same',task_id:'DOC',research_version:2},{id:'previous',task_id:'DOC',research_version:1},{id:'foreign',task_id:'OTHER',research_version:2},
],draft_report:{facts:[],logics:[{title:'合作条件',analysis:'项目需先完成验收。',source_refs:['same','same','previous','foreign']}]}};
const frozen=JSON.stringify(originals);
html=render(originals,ctx);
assert.match(html,/引用原件/);
assert.equal((html.match(/data-source="same"/g)||[]).length,1);
assert.match(html,/data-source="same" data-source-task="DOC" data-source-version="2"/);
assert.match(html,/previous、foreign未找到对应记录/);
assert.doesNotMatch(html,/data-source="previous"|data-source="foreign"|<h3>数据对照/);
assert.equal(JSON.stringify(originals),frozen);

const withValue=structuredClone(originals);
withValue.draft_report.logics[0].data_refs=['D1'];
withValue.draft_report.facts=[{id:'D1',label:'验收项目数',raw_value:1,artifact_id:'same',period:'2026',unit:'项'}];
html=render(withValue,ctx);
assert.match(html,/data-draft-source="D1"/);
assert.doesNotMatch(html,/data-source="same"|引用原件/);

const excerpt={id:'TEXT',version:1,questions:[{id:'Q',question:'核对条款',answer:'原件载明须完成验收。',status:'已回答',refs:['E']}],
  evidence:[{id:'E',text:'须完成验收',artifact_id:'A',record_locator:{part:'P1'}}],materials:[{id:'A',task_id:'TEXT',research_version:1}]};
html=render(excerpt,ctx);
assert.match(html,/<h3>材料摘录<\/h3>/);
assert.match(html,/data-source="A" data-source-evidence="E" data-source-task="TEXT" data-source-version="1"/);
assert.doesNotMatch(render({...excerpt,materials:[]},ctx),/data-source="A"/);
const missing={id:'NAV',version:1,source_records:[{id:'A',source:'ifind',operation:'date_sequence'}],
  draft_report:{facts:[{id:'D1',field:'ths_unit_nvnf_fund',axis_role:'time',raw_value:null,artifact_id:'A',period:'2026-10-14'},
    {id:'D2',field:'ths_unit_nvnf_fund',axis_role:'time',raw_value:'--',artifact_id:'A',period:'2026-10-15'}],
  logics:[{title:'净值记录',data_refs:['D1']}]}};
html=render(missing,ctx);
assert.match(html,/2026\.10\.14/);
assert.equal((html.match(/data-draft-source="D1"/g)||[]).length,1);
assert.equal((html.match(/data-draft-source="D2"/g)||[]).length,1);
assert.doesNotMatch(html,/paper-excerpt|材料摘录/);

const publicPaper={id:'PUBLIC',version:1,source_records:[{id:'A',task_id:'PUBLIC',research_version:1,
  kind:'material',snapshot_hash:'frozen',snapshot:{parts:[{locator:'段落2',text:'𠮷符号。须备案。其余正文。'}]}}],
  draft_report:{facts:[],logics:[{title:'公开原文',source_refs:['A'],document_excerpts:[
    {source_ref:'A',snapshot_hash:'frozen',part_index:0,start:4,end:8}]}]}};
html=render(publicPaper,ctx);
assert.match(html,/<q>须备案。<\/q>/);
assert.match(html,/data-draft-excerpt=".*?" data-source-task="PUBLIC" data-source-version="1"/);
assert.match(html,/A，段落2/);
assert.doesNotMatch(html,/其余正文|引用原件/);
const moved=structuredClone(publicPaper);
moved.source_records[0].snapshot_hash='changed';
assert.doesNotMatch(render(moved,ctx),/须备案/);
console.log('workingpaper: 10 scenarios passed');
