const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const frontend = 'products/app/frontend/';
const sandbox = {window:{}, apiMode:true, artifactsOpen:false, pendingTaskActions:new Set(),
  workspaceReady:true,historical:null,selectingTaskId:'',selected:null,tasks:[],
  runtimeLive:()=>true,latestReport:task=>task.delivery||null,isBackupArchive:()=>false,reportVersionBusy:()=>false};
for (const file of ['research-data.js','desk-report.js']) {
  vm.runInNewContext(fs.readFileSync(frontend+'ui/'+file,'utf8'),sandbox);
}
const source = fs.readFileSync(frontend+'workspace.js','utf8');
const start = source.indexOf('const taskActionLabels='), end = source.indexOf('function caseButtons()',start);
assert.ok(start>=0&&end>start);
vm.runInNewContext(source.slice(start,end),sandbox);
const data = sandbox.window.RHResearchData;
const report = sandbox.window.RHDeskViews.report;
const task = changes=>({id:'fixture',version:2,...changes});
const action = (record,options)=>sandbox.taskActionButton(record,sandbox.recoveryAction(record),options);
const controls = html=>Array.from(html.matchAll(/data-action="([a-z-]+)"/g),match=>match[1]);
const ctx = {esc:String,apiMode:true,reportProfile:()=> 'standard',reportURL:()=>'/frozen-report',taskActions:action};
const blocked = {code:'execution_time_exhausted',reason:'剩余执行时间不足，待保存正文已保留。'};

test('Chat recovery and the task menu use the currently selected task',()=>{
  const running = task({status:'运行中'});
  const interrupted = task({status:'失败',can_finish_from_data:true});
  const menu=record=>{
    sandbox.selected=record;sandbox.tasks=[record];
    return Array.from(sandbox.chatTaskMenuItems(record.id),([name])=>name);
  };
  assert.deepEqual(controls(action(running)),[]);
  assert.deepEqual(menu(running),['backup']);
  assert.deepEqual(controls(action(interrupted)),['finish-draft']);
  assert.deepEqual(menu(interrupted),['finish-draft','resume','backup']);
  assert.deepEqual(Array.from(sandbox.chatTaskMenuItems('other')),[]);
  sandbox.artifactsOpen=true;
  try {
    assert.deepEqual(menu(interrupted),[]);
    assert.deepEqual(controls(sandbox.contextTaskActions(running)),['cancel','backup']);
  }
  finally { sandbox.artifactsOpen=false;sandbox.selected=null;sandbox.tasks=[]; }
});

test('bound checkpoints select local save and express the same state in Chat and Desk',()=>{
  for(const status of ['失败','已取消']) {
    for(const flag of ['pending_content','has_pending_content','pending_html','has_retained_submission']) {
      const record = task({status,[flag]:true,can_finish_from_data:true});
      const expected = flag==='has_retained_submission'?'研究原文已保留':'报告待保存';
      const summary = data.taskStatus(record);
      assert.equal(summary.status,status);
      assert.equal(summary.label,expected);
      assert.equal(summary.processLabel,expected);
      assert.deepEqual(controls(action(record)),['retry-save']);
      const html = report(record,ctx);
      assert.match(html,new RegExp('<h2>'+expected+'</h2>'));
      assert.deepEqual(controls(html),['retry-save','chat']);
      assert.match(html,/class="button-primary" type="button" data-action="retry-save"/);
      assert.doesNotMatch(html,/暂无研究报告|<iframe/);
    }
  }
});

test('a saved edition suppresses stale retry flags and a running batch cannot be retried',()=>{
  const saved = task({status:'已取消',delivery:{version:2},has_pending_content:true});
  assert.equal(data.reportCheckpoint(saved),null);
  assert.equal(action(saved),'');
  for(const status of ['待开始','运行中','已完成','部分完成']) {
    const record = task({status,has_pending_content:true,has_retained_submission:true});
    assert.equal(data.reportCheckpoint(record),null);
    assert.doesNotMatch(report(record,ctx),/data-action="retry-save"|报告待保存|研究原文已保留/);
  }
});

test('the same busy and budget guards apply to save recovery in both views',()=>{
  const record = task({status:'失败',has_pending_content:true});
  sandbox.pendingTaskActions.add(record.id);
  try {
    assert.match(action(record),/retry-save" disabled aria-busy="true"/);
    assert.match(report(record,ctx),/retry-save" disabled aria-busy="true"/);
  } finally { sandbox.pendingTaskActions.clear(); }
  const stopped = {...record,status:'已取消',batch_settling:true,recovery_block:blocked};
  assert.equal(data.taskStatus(stopped).label,'正在停止');
  assert.equal(data.taskStatus({...stopped,status:'失败'}).label,'正在收尾');
  assert.equal(action(stopped),'');
  assert.match(report(stopped,ctx),/本批次正在收尾/);
  assert.doesNotMatch(report(stopped,ctx),/data-action="retry-save"/);
  const exhausted = {...record,recovery_block:blocked};
  assert.equal(action(exhausted),'');
  const prior = report(exhausted,{...ctx,edition:{version:1}});
  assert.match(prior,/阅读版本1，版本2尚未交付。剩余执行时间不足/);
  assert.match(prior,/data-action="export"/);
  assert.match(prior,/<iframe[^>]*src="\/frozen-report"/);
  assert.doesNotMatch(prior,/data-action="(?:retry-save|resume)"/);
});

test('old draft template copy disappears from presentation while real evidence gaps remain',()=>{
  const marker = '研究草稿已生成，独立核验可在后续版本补充。';
  const missing = '缺少期末收盘读数。';
  const record = task({status:'已取消',gaps:[marker,missing]});
  assert.deepEqual(Array.from(data.researchGaps(record)),[missing]);
  assert.deepEqual(record.gaps,[marker,missing]);
  assert.equal(data.taskStatus(record).label,'已取消');
  assert.deepEqual(controls(action(record)),['resume']);
});

test('standalone HTML has no service-only recovery control',()=>{
  const record = task({status:'已取消',has_retained_submission:true});
  sandbox.apiMode=false;
  try {
    assert.equal(action(record),'');
    assert.deepEqual(controls(report(record,{...ctx,apiMode:false})),['chat']);
  } finally { sandbox.apiMode=true; }
});
