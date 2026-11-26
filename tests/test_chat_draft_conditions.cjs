'use strict';
// Self-contained presentation controls. No research, external files, or requests.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const context={window:{},esc:escape};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'products/app/frontend/ui/research-data.js'),'utf8'),context);
for(const [start,end] of [['collectionWorkingpaper','draftReportCards'],['draftReportCards','draftFactsTable'],['sourceRecordName','sourceRecordButtons'],['sourceRecordButtons','openSource']]){
 const first=source.indexOf('function '+start+'('),last=source.indexOf('function '+end+'(',first);
 assert.ok(first>=0&&last>first,'current production function exists: '+start);
 vm.runInContext(source.slice(first,last),context);
}
function rejectedTask(){
 const id='self-contained-workpaper';
 return {id,version:1,source_records:[{id:'original',kind:'api',source:'ifind',operation:'cmd_history_quotation',task_id:id,research_version:1}],
  rejected_evidence:Array.from({length:61},(_,index)=>({id:'rejection-'+index,artifact_id:'original',object:'合成对象',question_ids:['q1'],rejection:'证券身份映射valid_from缺失'}))};
}
function cards(logic={},paper={}){
 return {id:'self-contained-chat',version:1,draft_report:{conclusion:'这里只核对呈现语义。',logics:[{title:'研究逻辑',analysis:'保留原论述。',risk:'结算周期可能变化。',...logic}],tracking_version:1,...paper}};
}
function rule(condition='后续回款弱于盈利时重新核对。'){
 return {indicator:'经营现金流与利润方向',source:'公司后续定期报告',window:'下一次公开报告后',condition};
}

test('sixty-one rejected extracts retain one reason and reachable original',()=>{
 const task=rejectedTask(),before=JSON.stringify(task),html=context.collectionWorkingpaper(task);
 assert.match(html,/61条/);assert.equal((html.match(/缺少代码映射有效期起点/g)||[]).length,1);
 assert.match(html,/data-source="original"/);assert.match(html,/未通过核验，不能作为结论依据/);
 assert.doesNotMatch(html,/已核验数据/);assert.equal(JSON.stringify(task),before);
});
test('different question scopes do not merge rejected extracts',()=>{
 const task=rejectedTask();task.rejected_evidence[0].question_ids=['q2'];
 const html=context.collectionWorkingpaper(task);assert.match(html,/60条/);assert.match(html,/>1条/);
});
test('foreign-task and foreign-version originals remain inaccessible',()=>{
 for(const change of [{task_id:'another-task'},{research_version:2}]){
  const task=rejectedTask();Object.assign(task.source_records[0],change);
  const html=context.collectionWorkingpaper(task);assert.doesNotMatch(html,/data-source=/);assert.match(html,/原件未留存/);
 }
});
test('a missing source identity never creates an unusable source control',()=>{
 const task=rejectedTask();delete task.source_records[0].id;
 assert.doesNotMatch(context.collectionWorkingpaper(task),/data-source=/);
});
test('untrusted rejection text and objects remain literal',()=>{
 const task=rejectedTask();task.rejected_evidence=[{id:'one',artifact_id:'original',object:'<script>bad()</script>',rejection:'<img src=x onerror=bad()>',question_ids:[]}];
 const html=context.collectionWorkingpaper(task);assert.match(html,/&lt;img/);assert.match(html,/&lt;script/);assert.doesNotMatch(html,/<img|<script/);
});
test('empty workpapers never invent original records',()=>assert.equal(context.collectionWorkingpaper({id:'blank',version:1}),''));
test('a risk is labelled as risk and never used as a validation condition',()=>{
 const task=cards(),before=JSON.stringify(task),html=context.draftReportCards(task);
 assert.match(html,/风险：结算周期可能变化。/);assert.doesNotMatch(html,/验证条件：|成立条件：|跟踪条件：/);
 assert.equal(JSON.stringify(task),before);
});
test('explicit premise and risk keep separate meanings in Chat',()=>{
 const task=cards({argument:{condition:'经营周期与报表口径保持可比。'}}),before=JSON.stringify(task),html=context.draftReportCards(task);
 assert.match(html,/成立条件：经营周期与报表口径保持可比。/);assert.match(html,/风险：结算周期可能变化。/);
 assert.equal(JSON.stringify(task),before);
});
test('complete saved tracking rule appears alongside premise and risk',()=>{
 const task=cards({argument:{condition:'经营周期保持可比。'},tracking:rule()}),html=context.draftReportCards(task);
 assert.match(html,/成立条件：经营周期保持可比。/);assert.match(html,/跟踪条件：后续回款弱于盈利时重新核对。/);assert.match(html,/风险：结算周期可能变化。/);
 assert.doesNotMatch(html,/自动监控|已兑现|已验证信号/);
});
test('identical premise and tracking wording is not repeated',()=>{
 const text='后续保持同口径。',task=cards({argument:{condition:text},tracking:rule(text)}),html=context.draftReportCards(task);
 assert.equal((html.match(/后续保持同口径。/g)||[]).length,1);assert.match(html,/成立条件：/);
});
test('incomplete and unversioned tracking rules do not become conditions',()=>{
 const incomplete=rule();delete incomplete.window;
 for(const task of [cards({tracking:incomplete}),cards({tracking:rule()},{tracking_version:undefined})]){
  const html=context.draftReportCards(task);assert.doesNotMatch(html,/跟踪条件：/);assert.match(html,/风险：/);
 }
});
test('Chat and ResDesk share exact saved condition values without rewriting them',()=>{
 const task=cards({argument:{condition:'  同口径可比。  '},tracking:rule()}),before=JSON.stringify(task);
 const saved=context.window.RHResearchData.reportConditions(task.draft_report)[0],html=context.draftReportCards(task);
 assert.ok(html.includes('成立条件：'+escape(saved.condition)));assert.ok(html.includes('跟踪条件：'+escape(saved.tracking.condition)));
 assert.equal(JSON.stringify(task),before);
});
test('untrusted condition and risk text cannot create markup',()=>{
 const task=cards({argument:{condition:'<script>condition()</script>'},risk:'<img src=x onerror=risk()>'});
 const html=context.draftReportCards(task);assert.match(html,/&lt;script/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<script|<img/);
});
test('a foreign report does not leak into the current Chat',()=>{
 for(const change of [{task_id:'foreign'},{research_version:2}]){
  const task=cards();Object.assign(task.draft_report,change);assert.equal(context.draftReportCards(task),'');
 }
});
test('a saved report summary keeps the material data gap once and leaves the argument in the report',()=>{
 const gap='估值日期待核对。',task=cards({},{
  conclusion:'收入同比下降7.13%，金额单位待核对。',
  readout_gaps:['金额单位待核对。',gap,' '+gap+' ',null,''],
 });
 const before=JSON.stringify(task),html=context.draftReportCards(task,{summaryOnly:true});
 assert.match(html,/收入同比下降7.13%，金额单位待核对。/);
 assert.equal((html.match(/金额单位待核对。/g)||[]).length,1);
 assert.equal((html.match(/估值日期待核对。/g)||[]).length,1);
 assert.doesNotMatch(html,/研究逻辑|保留原论述|风险：|<button|<details/);
 assert.equal(JSON.stringify(task),before);
});
test('normalized argument text is not repeated when its premise is already a whole paragraph',()=>{
 const premise='经营周期保持可比。',task=cards({analysis:'保留原论述。\n'+premise,argument:{condition:premise},tracking:rule(premise)});
 const html=context.draftReportCards(task);assert.equal((html.match(/经营周期保持可比。/g)||[]).length,1);
});
test('a condition inside a longer negative statement does not suppress its explicit role',()=>{
 const premise='口径可比。',task=cards({analysis:'不能仅凭口径可比。认定经营改善。',argument:{condition:premise}});
 const html=context.draftReportCards(task);assert.match(html,/不能仅凭口径可比。认定经营改善。/);assert.match(html,/成立条件：口径可比。/);
});
