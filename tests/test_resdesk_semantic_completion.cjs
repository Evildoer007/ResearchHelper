/* Independent regressions: pure renderers, no data/API mutation. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const box={window:{},Date,Set,Map};
for(const file of ['research-data','desk-workingpaper','desk-report','desk-tracking'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui',file+'.js'),'utf8'),box);
const views=box.window.RHDeskViews,ctx={esc:v=>String(v??'').replaceAll('<','&lt;'),sourceRecordName:r=>r.id,displayAnswer:(_,q)=>q.answer||'',statusTag:s=>s||'',knowledgeView:()=>'',collectionWorkingpaper:()=>'',apiMode:false,reportProfile:()=> 'standard',reportURL:()=>'',reportVersionBusy:()=>false};
test('unlinked textual facts remain readable',()=>assert.match(views.workingpaper({id:'t',version:1,draft_report:{logics:[],facts:[{id:'D1',text:'公告原文',value:null}]}},ctx),/公告原文/));
test('unlinked draft calculations remain inspectable',()=>assert.match(views.workingpaper({id:'t',version:1,draft_report:{logics:[],facts:[],calculations:[{id:'A1',label:'毛利率差异',value:0,unit:'%'}]}},ctx),/data-draft-calculation="A1"/));
test('saved report never substitutes unsaved HTML',()=>assert.doesNotMatch(views.report({id:'t',version:2,delivery:{version:1},pending_html:'UNSAVED_TWO'},ctx),/UNSAVED_TWO/));
test('failed run preserves saved report access',()=>{const html=views.report({id:'t',version:2,status:'失败',delivery:{version:1},report_html:'SAVED_ONE'},ctx);assert.match(html,/SAVED_ONE/);assert.doesNotMatch(html,/暂无研究报告/);});
test('draft risks stay in workingpaper without inventing tracking conditions',()=>{const task={id:'t',version:1,tracking:[],draft_report:{logics:[{title:'盈利',risk:'核对下期利润'}]}};const html=views.tracking(task,ctx);assert.match(html,/暂无跟踪条件/);assert.doesNotMatch(html,/核对下期利润|后台跟踪|已登记条件/);assert.match(views.workingpaper(task,ctx),/核对下期利润/);});
test('current registered thesis survives a coexisting draft',()=>{
 const html=views.workingpaper({id:'t',version:2,draft_report:{facts:[],logics:[{title:'草稿论点',implication:'草稿含义'}]},theses:[{id:'T1',task_id:'t',research_version:2,title:'正式论点',investment_meaning:'正式含义',support:[],counter:[]}],questions:[],evidence:[]},ctx);
 assert.match(html,/正式论点/);assert.match(html,/正式含义/);
});
test('explicitly foreign or historical theses never appear as current judgments',()=>{
 const html=views.workingpaper({id:'t',version:2,theses:[{id:'old',task_id:'t',research_version:1,title:'旧版论点'},{id:'foreign',task_id:'other',research_version:2,title:'其他任务论点'}]},ctx);
 assert.doesNotMatch(html,/旧版论点|其他任务论点/);
});
test('support and counter evidence retain their roles',()=>{
 const html=views.workingpaper({id:'t',version:1,theses:[{id:'T1',title:'利润改善',support:['E1'],counter:['E2']}],evidence:[{id:'E1',text:'利润增速上升'},{id:'E2',text:'现金回款下降'}]},ctx);
 assert.match(html,/利润增速上升/);assert.match(html,/现金回款下降/);assert.match(html,/支持/);assert.match(html,/反证/);
});
