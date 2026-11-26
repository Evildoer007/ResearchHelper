/* Workpaper raw values reuse the existing scoped observation link. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..'),box={window:{}};
box.esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
vm.createContext(box);
vm.runInContext(fs.readFileSync(path.join(root,'products/app/frontend/ui/research-data.js'),'utf8'),box);
const workspace=fs.readFileSync(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const body=/function draftFactsTable\(task\)\{[\s\S]*?\n\}/.exec(workspace);assert.ok(body);
vm.runInContext(body[0],box);
const fixture=()=>({id:'raw-cell-task',version:2,source_records:[{id:'nav-low',task_id:'raw-cell-task',research_version:2}],
 draft_report:{facts:[{id:'D2',code:'510300.SH',label:'单位净值',period:'2026-10-21',value:1,
 raw_value:'1.000000000000000001',unit:'元',artifact_id:'nav-low',locator:{row:0,field:'unit_nav'}}]}});
test('a raw fact keeps its exact literal and scoped original action',()=>{
 const task=fixture(),before=JSON.stringify(task),html=box.draftFactsTable(task);
 assert.match(html,/>1\.000000000000000001<\/button>/);
 assert.match(html,/data-draft-source="D2"/);assert.match(html,/data-source-task="raw-cell-task"/);
 assert.match(html,/data-source-version="2"/);assert.match(html,/type="button"/);
 assert.equal(JSON.stringify(task),before);assert.doesNotMatch(html,/source-record|查看原件<\/button>/);
});
test('regular data matrices retain their compact number reading',()=>{
 const task=fixture(),D=box.window.RHResearchData,item=D.observations(task.draft_report.facts)[0];
 assert.match(D.cell(item,task,{esc:box.esc}),/>1\.0000<\/button>/);
 assert.match(D.cell(item,task,{esc:box.esc,rawValues:true}),/>1\.000000000000000001<\/button>/);
});
for(const [name,change] of [
 ['missing original',task=>task.source_records=[]],
 ['foreign task',task=>task.source_records[0].task_id='other-task'],
 ['old version',task=>task.source_records[0].research_version=1],
 ['missing fact identity',task=>delete task.draft_report.facts[0].id],
])test(name+' keeps text without a false source link',()=>{
 const task=fixture();change(task);const html=box.draftFactsTable(task);
 assert.match(html,/1\.000000000000000001/);assert.doesNotMatch(html,/data-draft-source=/);
});
test('material originals use the same task and version scope',()=>{
 const task=fixture();task.materials=task.source_records;task.source_records=[];
 assert.match(box.draftFactsTable(task),/data-draft-source="D2"/);
 task.materials[0].research_version=1;assert.doesNotMatch(box.draftFactsTable(task),/data-draft-source=/);
});
test('zero and missing observations keep their actual readings',()=>{
 const task=fixture();task.draft_report.facts[0].raw_value='0.0000';task.draft_report.facts[0].value=0;
 assert.match(box.draftFactsTable(task),/>0\.0000<\/button>/);
 task.draft_report.facts[0].raw_value='';task.draft_report.facts[0].value=null;
 assert.match(box.draftFactsTable(task),/>未返回<\/button>/);
 task.draft_report.facts[0].raw_value=null;task.draft_report.facts[0].value=null;
 assert.match(box.draftFactsTable(task),/>未返回<\/button>/);
});
test('labels and raw strings remain escaped',()=>{
 const task=fixture();task.draft_report.facts[0].label='<img src=x>';task.draft_report.facts[0].raw_value='<script>';
 const html=box.draftFactsTable(task);assert.doesNotMatch(html,/<img|<script>/);assert.match(html,/&lt;script&gt;/);
});
test('empty workpapers remain empty',()=>assert.equal(box.draftFactsTable({draft_report:{facts:[]}}),''));

const {functionSource}=require('../products/app/tests/function_source.cjs');
vm.runInContext(['draftObject','sourceRecordName','sourceRecordButtons','unverifiedProposal','fileMaterial',
 'originalFiles','originalDownloadButton','originalFileFeedback','openSource','openDocumentReview']
 .map(name=>functionSource(workspace,name)).join('\n'),box);
function originalViewer(){
 const current={id:'file',kind:'material',task_id:'doc',research_version:2,snapshot_hash:'current-digest',
  snapshot:{name:'本版原件',parts:[{locator:'段落1',text:'本版内容。'}]}};
 const legacy={id:'file',kind:'material',snapshot_hash:'legacy-digest',snapshot:{name:'无版本旧记录'}};
 const old={...current,research_version:1,snapshot_hash:'old-digest',snapshot:{name:'旧版原件'}};
 const foreign={...current,task_id:'other',snapshot_hash:'foreign-digest',snapshot:{name:'其他任务原件'}};
 const task={id:'doc',version:2,materials:[old,foreign,legacy,current],evidence:[],rejected_evidence:[]};
 const view={mounted:null,form:'',notices:[]};
 box.selected=task;box.apiMode=false;box.$=()=>({open:false,focus(){}});
 box.window.RHSourceViewer={mount:(_,value)=>view.mounted=value};
 box.modal=(_,html)=>view.form=html;
 box.notice=message=>view.notices.push(message);
 return {current,legacy,old,foreign,task,view};
}
test('list, viewer and document-review form select the same scoped original',()=>{
 const {current,task,view}=originalViewer(),html=box.sourceRecordButtons(task),before=JSON.stringify(task);
 assert.equal((html.match(/data-source="file"/g)||[]).length,1);
 assert.match(html,/本版原件/);assert.doesNotMatch(html,/旧记录|旧版原件|其他任务原件/);
 box.openSource('file');
 assert.equal(view.mounted.record,current);
 assert.deepEqual(Array.from(view.mounted.relatedRecords),[current]);
 box.openDocumentReview('file');
 assert.match(view.form,/data-task="doc" data-artifact="file" data-version="2" data-hash="current-digest"/);
 assert.equal(JSON.stringify(task),before);
});
test('an unversioned original remains readable when it is the sole available record',()=>{
 const {legacy,task,view}=originalViewer();
 task.materials=[legacy];box.openSource('file');
 assert.equal(view.mounted.record,legacy);
});
test('a current excerpt wins over a stale copy with the same evidence ID',()=>{
 const {current,task,view}=originalViewer(),locator={part:'段落1'};
 task.evidence=[{id:'E1',artifact_id:'file',research_version:1,snapshot_hash:'old-digest',text:'旧版内容',record_locator:{part:'旧段落'}},
                {id:'E1',artifact_id:'file',task_id:'doc',research_version:2,snapshot_hash:'current-digest',text:'本版内容。',record_locator:locator}];
 box.openSource('file','E1');
 assert.equal(view.mounted.record,current);assert.equal(view.mounted.locator,locator);assert.equal(view.mounted.quote,'本版内容。');
 assert.deepEqual(view.notices,[]);
});
test('a different source or fingerprint never supplies an excerpt locator',()=>{
 for(const change of [{artifact_id:'other-file'},{snapshot_hash:'old-digest'},{task_id:'other'},{research_version:1}]){
  const {current,task,view}=originalViewer();
  task.evidence=[{id:'E1',artifact_id:'file',task_id:'doc',research_version:2,snapshot_hash:'current-digest',
   text:'无效摘录',record_locator:{part:'旧段落'},...change}];
  box.openSource('file','E1');
  assert.equal(view.mounted.record,current);
  assert.equal(view.mounted.locator,undefined);assert.equal(view.mounted.quote,undefined);
  assert.match(view.notices[0],/摘录定位已变化/);
 }
});
test('a retained unverified excerpt still opens its own source',()=>{
 const {task,view}=originalViewer(),locator={part:'段落1'};
 task.rejected_evidence=[{id:'E1',artifact_id:'file',task_id:'doc',research_version:2}];
 task.host_result={evidence_specs:[{id:'E1',artifact_id:'file',text:'本版内容。',record_locator:locator}]};
 box.openSource('file','E1');
 assert.equal(view.mounted.locator,locator);assert.equal(view.mounted.quote,'本版内容。');
});
test('the source reader binds its download to the retained origin and exact fingerprint',()=>{
 const {current,task,view}=originalViewer();
 box.apiMode=true;
 current.retained_from_version=1;
 current.snapshot.name='政策.txt';current.snapshot.hash='b'.repeat(64);
 const identity={name:'政策.txt',sha256:current.snapshot.hash,task_id:task.id,artifact_id:current.id};
 task.original_files=[{...identity,id:'other-bytes',sha256:'c'.repeat(64),research_version:1},
  {...identity,id:'new-copy',research_version:2},{...identity,id:'original-copy',research_version:1}];
 const controls={insertAdjacentHTML:(_,html)=>view.controls=html};
 box.document={createElement:()=>controls};
 box.$=selector=>selector==='#source-reader'?{prepend(){},insertAdjacentHTML(){}}:{open:false};
 box.openSource('file');
 assert.match(view.controls,/data-original-file="original-copy" data-original-version="1"/);
 assert.doesNotMatch(view.controls,/new-copy|other-bytes/);
});
test('an original from an older edition alone cannot open or create a review',()=>{
 const {old,task,view}=originalViewer();
 task.materials=[old];
 box.openSource('file');box.openDocumentReview('file');
 assert.equal(view.mounted,null);assert.equal(view.form,'');
 assert.deepEqual(view.notices,['当前版本没有保留该原件，请查看原交付版本','原件已变化，请重新打开来源']);
});
