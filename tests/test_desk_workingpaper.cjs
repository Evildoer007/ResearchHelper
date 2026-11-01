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
console.log('workingpaper: 5 scenarios passed');
