'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
let calls=0;
const forbid=()=>{calls++;throw Error('No dispatch in a version comparison');};
const sandbox={window:{},fetch:forbid,XMLHttpRequest:forbid,setInterval:forbid,setTimeout:forbid};
for(const name of ['research-data.js','desk-tracking.js'])vm.runInNewContext(fs.readFileSync(path.join(root,'products/app/frontend/ui',name),'utf8'),sandbox);
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const ctx={esc,statusTag:value=>esc(value),evidenceButton:forbid,calculationButton:forbid,calculationName:forbid,cutoffLabel:value=>value};
function freeze(value){if(value&&typeof value==='object'&&!Object.isFrozen(value)){Object.freeze(value);Object.values(value).forEach(freeze);}return value;}
function render(task){const before=JSON.stringify(task);freeze(task);const html=sandbox.window.RHDeskViews.tracking(task,ctx);assert.equal(JSON.stringify(task),before);assert.equal(calls,0);return html;}
function basis(version,conclusion,logics){
 const value={schema:'researchhelper.report-comparison-basis',version:1,task_id:'t',research_version:version,cutoff:'2026-11-'+(version===1?'01':'10')+'T08:00:00+08:00',title:'经营与现金兑现',conclusion,logics:logics.map((logic,index)=>({position:index+1,title:logic.title,claim:logic.claim||'',implication:logic.implication||''}))};
 return {...value,basis_hash:crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex')};
}
function task(){
 const prior=basis(1,'原版保留有条件的经营判断。',[{title:'现金回收',claim:'盈利仍需现金支持。'},{title:'规模变化',implication:'规模需要利润支持。'}]);
 const current=basis(2,'本版维持前提，并说明尚未兑现的部分。',[{title:'规模变化',claim:'收入变化不能代替盈利判断。'},{title:'现金回收仍需观察',implication:'需按后续数据复核。'}]);
 return {id:'t',version:2,cutoff:current.cutoff,tracking:[],revisions:[],draft_report:{task_id:'t',research_version:2,logics:[],facts:[]},update_from:{version:1,conditions:[]},changes:{task_id:'t',research_version:2,prior_version:1,draft_observations:{version:1,columns:[],rows:[],summary:'同口径读数未变化。'},draft_report_comparison:{version:1,baseline_available:true,prior,current}}};
}

test('both exact conclusions are visible without implying a validated judgment',()=>{
 const value=task(),html=render(value);
 for(const text of [value.changes.draft_report_comparison.prior.conclusion,value.changes.draft_report_comparison.current.conclusion,'结论对照','原结论，版本1','本版结论，版本2'])assert.ok(html.includes(text));
 assert.doesNotMatch(html,/已兑现|监控已开启|判断已验证|逻辑成立/);
 assert.match(html,/data-version-role="prior"/);assert.match(html,/data-version-role="current"/);
});

test('logic lists keep their own order and do not match by position',()=>{
 const html=render(task()),prior=html.split('data-version-role="prior"')[1].split('data-version-role="current"')[0],current=html.split('data-version-role="current"')[1];
 assert.ok(prior.indexOf('现金回收')<prior.indexOf('规模变化'));assert.ok(current.indexOf('规模变化')<current.indexOf('现金回收仍需观察'));
 assert.doesNotMatch(html,/data-matched|data-thesis-id/);
});

test('existing version navigation is one header action',()=>{
 const html=render(task());assert.equal((html.match(/data-action="versions"/g)||[]).length,1);
 assert.match(html,/<header class="desk-view-heading">[\s\S]*desk-heading-actions[\s\S]*data-action="versions"/);
 assert.match(html,/<h2>数据变化<\/h2>/);
});

test('dates belong to the original frozen version and are not current timestamps',()=>{
 const html=render(task());assert.match(html,/datetime="2026-11-01T08:00:00\+08:00"/);assert.match(html,/datetime="2026-11-10T08:00:00\+08:00"/);
});

test('foreign or wrong-version prose is not shown',()=>{
 for(const [role,key,value] of [['prior','task_id','foreign'],['current','task_id','foreign'],['prior','research_version',2],['current','research_version',true]]){
  const valueTask=task();valueTask.changes.draft_report_comparison[role][key]=value;
  const html=render(valueTask);assert.match(html,/两版正文的任务或版本不一致/);assert.doesNotMatch(html,/原版保留有条件的经营判断|本版维持前提/);
 }
});

test('incomplete records do not crash the other saved views',()=>{
 const value=task();value.changes.draft_report_comparison.current.logics=[{}];
 const html=render(value);assert.match(html,/暂不展示/);assert.match(html,/同口径读数未变化/);
});

test('missing historical draft does not manufacture an old conclusion',()=>{
 const value=task();value.changes.draft_report_comparison.baseline_available=false;value.changes.draft_report_comparison.prior=null;
 const html=render(value);assert.doesNotMatch(html,/report-comparison-heading|原结论/);assert.match(html,/同口径读数未变化/);
});

test('legacy and unknown comparison versions preserve the existing tracking view',()=>{
 for(const version of [null,true,2]){
  const value=task();if(version===null)delete value.changes.draft_report_comparison;else value.changes.draft_report_comparison.version=version;
  const html=render(value);assert.doesNotMatch(html,/report-comparison-heading/);assert.match(html,/跟踪复核/);
 }
});

test('empty explicit claims stay empty while the actual investment meaning is kept',()=>{
 const value=task();value.changes.draft_report_comparison.prior.logics=[{position:3,title:'旧版逻辑',claim:'',implication:'原版实际保留的含义。'}];
 const html=render(value);assert.match(html,/原版实际保留的含义/);assert.match(html,/<li value="3" data-logic-position="3">/);assert.doesNotMatch(html,/未形成可核验论点/);
});

test('report prose is escaped and never becomes runnable HTML',()=>{
 const value=task();value.changes.draft_report_comparison.prior.conclusion='<img src=x onerror=alert(1)>';
 value.changes.draft_report_comparison.current.logics[0].title='<script>alert(2)</script>';
 const html=render(value);assert.match(html,/&lt;img src=x/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<img src=x|<script>/);
});

test('long conclusions and every supplied logic remain complete',()=>{
 const value=task(),text='长期经营判断与适用前提。'.repeat(120)+'END';value.changes.draft_report_comparison.current.conclusion=text;
 value.changes.draft_report_comparison.current.logics=Array.from({length:8},(_,i)=>({position:i+1,title:'实际逻辑'+i,claim:'明确判断'+i,implication:''}));
 const html=render(value);assert.ok(html.includes(text));assert.equal((html.match(/实际逻辑\d/g)||[]).length,8);assert.ok(html.includes('明确判断7'));
});
