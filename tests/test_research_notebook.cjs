/* New presentation contract; previous module tests remain unchanged. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..'),sandbox={window:{},Date,Set,Map};
for(const name of ['research-data','desk-sources','desk-workingpaper'])vm.runInNewContext(fs.readFileSync(path.join(root,'products/app/frontend/ui',name+'.js'),'utf8'),sandbox);
const D=sandbox.window.RHResearchData,views=sandbox.window.RHDeskViews;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx={esc,sourceRecordName:r=>'iFinD '+r.operation,unverifiedProposal:(_t,r)=>r,processDetails:()=>'<p>运行记录</p>',statusTag:v=>'<span>'+esc(v)+'</span>',knowledgeView:()=>'<p>登记理论</p>',displayAnswer:(_t,q)=>q.answer||'',collectionWorkingpaper:()=>'',calculationName:c=>c.label||c.method};
const fixture=JSON.parse(fs.readFileSync(path.join(root,'result/acceptance/desk-modules/maotai-display-fixture.json'),'utf8'));
test('20 retained observations become five metric rows, not twenty source buttons',()=>{
 const before=JSON.stringify(fixture),html=views.sources(fixture,ctx);
 assert.equal((html.match(/data-observation=/g)||[]).length,20);
 assert.equal((html.match(/<tbody>/g)||[]).length,2);
 assert.equal((html.match(/<tr><th scope="row">/g)||[]).length,5);
 assert.equal((html.match(/data-source="/g)||[]).length,6);
 assert.match(html,/2024\.12\.31/);assert.match(html,/2025\.12\.31/);
 for(const fact of fixture.draft_report.facts)assert.ok(html.includes(String(fact.value)),fact.id+' original precision retained');
 assert.equal(JSON.stringify(fixture),before);
 const quote=fixture.source_records.find(row=>row.operation==='cmd_history_quotation');
 assert.equal((html.match(new RegExp('data-source="'+quote.id+'"','g'))||[]).length,1);
});
test('matrix numbers bind to the exact draft record and task version',()=>{
 const html=views.sources(fixture,ctx);
 assert.match(html,/data-draft-source="D7"/);assert.match(html,/data-source-task="/);assert.match(html,/data-source-version="1"/);
 assert.match(html,/单位待核对/);assert.doesNotMatch(html,/核验通过|已核验/);
 assert.match(html,/data-draft-calculation="A3"/);
});
test('duplicate periods and distinct measurement bases do not overwrite facts',()=>{
 const task={id:'t',version:1,source_records:[{id:'s'}]},facts=[{id:'a',code:'X',field:'close',value:0,unit:'元',period:'2025-12-31',series_basis:'one',artifact_id:'s'},{id:'b',code:'X',field:'close',value:-1,unit:'元',period:'20251231',series_basis:'one',artifact_id:'s'},{id:'c',code:'X',field:'close',value:2,unit:'元',period:'20251231',series_basis:'two',artifact_id:'s'}];
 const groups=D.groups(D.observations(facts),task),html=D.grid(groups[0],task,ctx);
 assert.equal((html.match(/<tr><th scope="row">/g)||[]).length,2);
 assert.equal((html.match(/data-observation=/g)||[]).length,3);assert.match(html,/>0<\/button>/);assert.match(html,/>-1<\/button>/);
});
test('three actual arguments retain analysis and risks without copying report summary',()=>{
 const before=JSON.stringify(fixture),html=views.workingpaper(fixture,ctx);
 assert.equal((html.match(/class="paper-argument"/g)||[]).length,3);
 assert.equal((html.match(/class="paper-layout"/g)||[]).length,3);
 assert.equal((html.match(/data-reading-key="draft-logic:/g)||[]).length,3);
 for(const logic of fixture.draft_report.logics){assert.ok(html.includes(esc(logic.analysis)));assert.ok(html.includes(esc(logic.risk)));}
 assert.ok(!html.includes(esc(fixture.draft_report.conclusion)));
 assert.equal(JSON.stringify(fixture),before);
});
test('fact answers stay factual and dependency cycles terminate',()=>{
 const task={id:'f',version:1,questions:[{id:'q',kind:'fact',question:'核对事实',answer:'已经取得的事实',status:'已回答',dependencies:['q'],refs:[]}],theses:[],evidence:[]};
 const html=views.workingpaper(task,ctx);assert.match(html,/已经取得的事实/);assert.doesNotMatch(html,/逻辑1|投资含义|待验证判断/);
 assert.match(views.workingpaper({},ctx),/底稿尚未形成/);
});
test('text, unknown units and small numbers remain truthful and escaped',()=>{
 const task={id:'t',version:1,source_records:[],draft_report:{facts:[{id:'x',label:'<img>',text:'<script>原文',value:null,unit:'',period:'未知'}]}};
 const html=views.sources(task,ctx);assert.doesNotMatch(html,/<script>|<img>/);assert.match(html,/&lt;script&gt;/);
 assert.equal(Number(D.number(.00000023)),.00000023);assert.equal(D.unitText('原始单位待核对'),'单位待核对');
});

test('display rounding carries whole digits without altering stored precision',()=>{assert.equal(D.number('1.999999'),'2.0000');assert.equal(D.number('-99.99999'),'-100.0000');assert.equal(D.number('170899152276.34'),'170,899,152,276.34');});

// The Chat reads the heading frozen by ReportComposer, as the report does.
const workspace=fs.readFileSync(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const cardsSource=workspace.slice(workspace.indexOf('function draftReportCards('),workspace.indexOf('function draftFactsTable('));
const cardsBox={esc,window:sandbox.window};vm.runInNewContext(cardsSource,cardsBox);
test('Chat preserves the shared report heading and escapes it once',()=>{
 const task={draft_report:{conclusion:'原结论',logics:[{title:'具体解释',display_heading:'传导逻辑一：<script>解释</script>',analysis:'原文',data_refs:['D1']}]}};
 const before=JSON.stringify(task),rendered=cardsBox.draftReportCards(task);
 assert.match(rendered,/传导逻辑一：&lt;script&gt;解释&lt;\/script&gt;/);
 assert.doesNotMatch(rendered,/<script>|策略逻辑/);
 assert.doesNotMatch(rendered,/data-draft-basis/);assert.equal(JSON.stringify(task),before);
});
test('legacy Chat headings remain readable when the new field is absent',()=>{
 const rendered=cardsBox.draftReportCards({draft_report:{conclusion:'旧结论',logics:[{title:'旧逻辑',analysis:'原文'},{analysis:'保留可用正文'}]}});
 assert.match(rendered,/策略逻辑1：旧逻辑/);assert.doesNotMatch(rendered,/undefined|null/);
});

test('workpaper and Chat show the same frozen heading without duplicate numbering',()=>{
 for(const heading of ['一、实际读数','一、同口径对照','传导逻辑一：可能渠道','策略逻辑一：经营判断']){
  const task={id:'scope',version:1,draft_report:{conclusion:'原结论',logics:[{title:'原题',display_heading:heading,analysis:'保留原文'}]}};
  const before=JSON.stringify(task),paper=views.workingpaper(task,ctx),chat=cardsBox.draftReportCards(task);
  assert.ok(paper.includes(esc(heading))&&chat.includes(esc(heading)));
  assert.doesNotMatch(paper,/class="paper-argument-index"/);
  assert.match(paper,/data-reading-key="draft-logic:0"/);
  assert.equal(JSON.stringify(task),before);
 }
});

test('path references resolve once without rewriting a frozen logic',()=>{
 const logic={data_refs:['D1','D1',42],mechanism:{steps:[{data_refs:['D2','D1']},null,{data_refs:'invalid'}]}};
 const before=JSON.stringify(logic);assert.equal(JSON.stringify(D.draftDataRefs(logic)),JSON.stringify(['D1','D2']));
 assert.equal(JSON.stringify(logic),before);
});
function stepFixture(){
 const task=JSON.parse(JSON.stringify(fixture));
 task.draft_report.logics=[{title:'条件解释',analysis:'保留判断与边界。',data_refs:[],calculation_refs:[],source_refs:[],
  mechanism:{steps:[{label:'已有观察',data_refs:['D1']},{label:'适用条件',data_refs:['D2']}]}}];
 return task;
}
test('step-only data stay under the Desk argument while Chat shows the conclusion',()=>{
 const task=stepFixture(),before=JSON.stringify(task),paper=views.workingpaper(task,ctx),chat=cardsBox.draftReportCards(task);
 const argument=paper.slice(paper.indexOf('id="paper-logic-0"'),paper.indexOf('</article>'));
 assert.match(argument,/data-observation="draft:D1"/);assert.match(argument,/data-observation="draft:D2"/);
 assert.match(chat,/条件解释/);assert.doesNotMatch(chat,/data-draft-basis/);assert.equal(JSON.stringify(task),before);
});
test('source ownership applies in data views, workpapers and Chat basis',()=>{
 const task=stepFixture();task.source_records.push({id:'foreign-source',task_id:task.id,research_version:task.version+1});
 task.draft_report.facts.push({id:'foreign-fact',artifact_id:'foreign-source',value:999999001,label:'错误版本读数',unit:'元'});
 task.draft_report.logics[0].mechanism.steps[0].data_refs.push('foreign-fact');
 const before=JSON.stringify(task),paper=views.workingpaper(task,ctx),data=views.sources(task,ctx);
 assert.doesNotMatch(paper,/999999001|错误版本读数/);assert.doesNotMatch(data,/999999001|错误版本读数/);
 let popup='';
 const basisBox={window:sandbox.window,esc,selected:task,modal:(_title,body)=>{popup=body},draftArithmetic:()=>'',
  sourceRecordButtons:value=>value.source_records.map(record=>record.id).join(',')};
 const source=workspace.slice(workspace.indexOf('function draftFactsTable('),workspace.indexOf('function draftArithmetic('));
 vm.runInNewContext(source,basisBox);basisBox.openDraftBasis(0);
 assert.ok(popup.includes(String(task.draft_report.facts.find(row=>row.id==='D1').raw_value??task.draft_report.facts.find(row=>row.id==='D1').value)));
 assert.doesNotMatch(popup,/999999001|foreign-source/);assert.equal(JSON.stringify(task),before);
});

test('ResDesk and ResChat use the report scope without rewriting retained references',()=>{
 const task={id:'scoped-paper',version:2,source_records:[
  {id:'other-company',kind:'api',operation:'other'},{id:'own-company',kind:'api',operation:'own'}],draft_report:{
  conclusion:'比较留存记录。',facts:[
   {id:'D1',code:'600519.SH',field:'ths_revenue_stock',value:761345,period:'2024-12-31',unit:'元',artifact_id:'other-company'},
   {id:'D2',code:'000001.SZ',field:'ths_revenue_stock',value:12,period:'2025-12-31',unit:'元',artifact_id:'own-company'},
   {id:'D3',code:'000001.SZ',field:'ths_revenue_stock',value:10,period:'2024-12-31',unit:'元',artifact_id:'own-company'}],
  calculations:[{id:'crossed',data_refs:['D1','D2'],label:'错引计算',value:90,unit:'%'},{id:'local',data_refs:['D3','D2'],label:'有效计算',value:20,unit:'%'}],
  logics:[{title:'仅有错引',analysis:'当前读数待补。',data_refs:['D1'],off_scope_data_refs:['D1'],
           reference_notes:['原值见完整数据。']},
          {title:'有效原值与混合引用',analysis:'收入变化可复核。',data_refs:['D2'],off_scope_data_refs:['D1'],
           mechanism:{steps:[{data_refs:['D1']}]},calculation_refs:['crossed','local']}]}};
 const before=JSON.stringify(task),paper=views.workingpaper(task,ctx),chat=cardsBox.draftReportCards(task);
 const [first,second]=paper.split(/<article class="paper-argument"/).slice(1).map(section=>section.split('</article>')[0]);
 assert.doesNotMatch(first,/data-observation|data-draft-calculation/);
 assert.match(first,/原值见完整数据/);
 assert.match(second,/data-observation="draft:D2"/);
 assert.match(second,/data-observation="draft:D3"/);
 assert.match(second,/data-draft-calculation="local"/);
 assert.doesNotMatch(second,/draft:D1|crossed|未找到对应记录/);
 assert.match(paper,/未关联的取数[\s\S]*data-observation="draft:D1"/);
 assert.match(paper,/未关联的计算[\s\S]*data-draft-calculation="crossed"/);
 assert.doesNotMatch(chat,/data-draft-basis/);
 assert.match(chat,/收入变化可复核/);
 const popups=[];
 const basisBox={window:sandbox.window,esc,selected:task,modal:(_title,body)=>popups.push(body),
  draftArithmetic:rows=>rows.map(row=>row.label).join(','),
  sourceRecordButtons:value=>value.source_records.map(record=>record.id).join(',')};
 vm.runInNewContext(workspace.slice(workspace.indexOf('function draftFactsTable('),workspace.indexOf('function draftArithmetic(')),basisBox);
 basisBox.openDraftBasis(0);basisBox.openDraftBasis(1);
 assert.equal(popups.length,1);
 assert.match(popups[0],/有效计算[\s\S]*000001\.SZ[\s\S]*own-company/);
 assert.doesNotMatch(popups[0],/错引计算|761345|600519\.SH|other-company/);
 assert.equal(JSON.stringify(task),before);
});
