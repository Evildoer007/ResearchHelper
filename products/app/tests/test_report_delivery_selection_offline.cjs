/* Real report view and export coordinator, with isolated file/network boundaries. */
const {readFileSync}=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createHash,webcrypto}=require('node:crypto');
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {functionSource}=require('./function_source.cjs');
const source=readFileSync(path.join(__dirname,'../frontend/workspace.js'),'utf8');
const reportCode=['sameReport','latestReport','shownReport','validReportCorrection','validSavedVersion',
  'reportProfile','savedReportURL','reportURL','reportView','recoveryAction','taskActionButton',
  'reportVisuals','reportStyleKey','savedReportStyles','reportPresentation','sameReportPresentation',
  'download','exportReport']
  .map(name=>functionSource(source,name)).join('\n');
const printBridge=source.slice(source.indexOf('function currentPrintFrame('),source.indexOf('function openReportCorrections('));
const digest='a'.repeat(64);
const modules=['research-data','desk-report','artifact-export'].map(name=>
  readFileSync(path.join(__dirname,'../frontend/ui/'+name+'.js'),'utf8')).join('\n');
function htmlFile(html){
  return {format:'html',mime:'text/html',content_base64:Buffer.from(html).toString('base64'),
    sha256:createHash('sha256').update(html).digest('hex')};
}

function harness(extra={}){
  const files=new Map(),button={disabled:false,setAttribute(){},removeAttribute(){}};
  const context={apiMode:true,selected:{id:'a',version:2,delivery:{version:1,digest},synthetic:false},historical:null,
    taskSelectionGeneration:1,leaving:false,draft:{},reportChoices:new Map(),selectingTaskId:'',
    artifactsOpen:true,stage:3,canUseDesk:()=>true,reportReader:{isReady:()=>false},reportVersionBusy:()=>false,actions:()=>'',
    window:{addEventListener(){}},document:{querySelectorAll:()=>[button],createElement:()=>({click(){
      context.downloads.push({name:this.download,blob:files.get(this.href)});
    }})},Blob,TextDecoder,Uint8Array,AbortController,atob,setTimeout,clearTimeout,crypto:webcrypto,
    URL:{createObjectURL(blob){const id='blob:test-'+files.size;files.set(id,blob);return id},revokeObjectURL(id){files.delete(id)}},
    esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    downloads:[],requests:[],notices:[],controls:{'#report-frame':{}},flushWorkspaceDraft:async()=>true,prepareServiceRecovery:()=>true,
    taskActionLabels:{'retry-save':'重试保存'},pendingTaskActions:new Set(),
    leaveWorkspace(){context.leaving=true},...extra};
  context.$=selector=>context.controls[selector]??null;
  context.api=async(url,body)=>{context.requests.push({url,body});return {
    task_id:body.task_id,version:body.version,profile:body.profile,report_digest:digest,
    filename:'研究报告_v'+body.version+'.'+body.format,
    visual_profile:body.visual_profile,...htmlFile('<html>saved version</html>')};};
  context.fetch=async url=>{context.requests.push(url);return {ok:true,status:200,text:async()=>'<html>saved version</html>'}};
  vm.createContext(context);vm.runInContext(modules,context);
  context.artifactExport=context.window.RHArtifactExport.create({notify:message=>context.notices.push(message)});
  vm.runInContext(reportCode+'\n'+printBridge,context);
  context.exportButton=button;
  return context;
}

test('API report identifies the delivered version independently of current task version',()=>{
  const h=harness(),paper=h.reportView(h.selected);
  assert.ok(paper.includes('阅读版本1，版本2尚未保存'));
  assert.ok(!paper.includes('用户材料研究')&&!paper.includes('实时研究'));
  assert.ok(paper.includes('src="/api/tasks/a/report/1?profile=standard"'));
  assert.ok(paper.includes('data-report-digest="'+digest+'"'));
});
test('Synthetic fixtures remain explicitly identified',()=>{
  const h=harness();h.selected.synthetic=true;
  assert.ok(h.reportView(h.selected).includes('report-case-state">合成案例</span>'));
});
test('Unsaved content has no report frame or export and cannot be downloaded',async()=>{
  const h=harness();h.selected.delivery=null;h.selected.status='失败';h.selected.pending_content={};
  const view=h.reportView(h.selected);
  assert.ok(view.includes('报告待保存'));
  assert.ok(view.includes('data-action="retry-save"'));
  assert.ok(!view.includes('id="report-frame"')&&!view.includes('data-action="export"'));
  await assert.rejects(h.exportReport(),/报告尚未保存/);
  assert.equal(h.downloads.length,0);assert.equal(h.requests.length,0);
});
test('API export requests the saved version through the shared export endpoint',async()=>{
  const h=harness();await h.exportReport();
  assert.deepEqual(JSON.parse(JSON.stringify(h.requests)),[{url:'/api/export',body:{task_id:'a',version:1,
    profile:'standard',format:'html',visual_profile:{theme:'research',schemes:{}}}}]);
  assert.equal(h.downloads[0].name,'研究报告_v1.html');
  assert.equal(await h.downloads[0].blob.text(),'<html>saved version</html>');
  assert.equal(h.exportButton.disabled,false);
});
test('Late export cannot download another selected research',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.api=async()=>new Promise(resolve=>{release=resolve;entered()});
  const pending=h.exportReport();await ready;
  h.selected={id:'b',version:3,delivery:{version:3}};h.taskSelectionGeneration++;
  release({task_id:'a',version:1});await assert.rejects(pending,/选择已变化/);
  assert.equal(h.downloads.length,0);assert.equal(h.exportButton.disabled,false);
});
test('Leaving and returning to the same task cannot renew an earlier export',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.api=async()=>new Promise(resolve=>{release=resolve;entered()});
  const pending=h.exportReport();await ready;h.taskSelectionGeneration+=2;
  release({task_id:'a',version:1});await assert.rejects(pending,/选择已变化/);
  assert.equal(h.downloads.length,0);
});
test('Offline reading and export use saved HTML rather than pending content',async()=>{
  const h=harness({apiMode:false});h.selected.report_html='<html>saved</html>';h.selected.pending_html='<html>pending</html>';
  assert.ok(h.reportView(h.selected).includes('srcdoc="&lt;html&gt;saved&lt;/html&gt;"'));
  await h.exportReport();assert.equal(await h.downloads[0].blob.text(),'<html>saved</html>');
  assert.equal(h.requests.length,0);
});
test('Historical report filename does not inherit another task version',async()=>{
  const h=harness({historical:{key:'consumer',shortTitle:'消费轮动研究'}});
  await h.exportReport(true);
  assert.deepEqual(h.requests,['/reports/consumer.html']);
  assert.equal(h.downloads[0].name,'消费轮动研究.html');
});
test('Failed reads cannot download a report',async()=>{
  const h=harness({historical:{key:'consumer'}});h.fetch=async()=>({ok:false,status:500});
  await assert.rejects(h.exportReport(true),/报告读取失败/);assert.equal(h.downloads.length,0);
});
test('A conversion timeout releases the control for a fresh request',async()=>{
  const h=harness(),working=h.api;
  h.api=async()=>{throw Object.assign(Error('timeout'),{name:'TimeoutError'})};
  await assert.rejects(h.exportReport(),/报告转换超时/);
  assert.equal(h.exportButton.disabled,false);
  h.api=working;await h.exportReport();assert.equal(h.downloads.length,1);
});
test('A mismatched API export cannot reach the file save boundary',async()=>{
  for(const mismatch of [{task_id:'b'},{report_digest:'b'.repeat(64)},{profile:'detailed'},{report_digest:undefined}]){
    const h=harness();
    h.api=async()=>({task_id:'a',version:1,profile:'standard',report_digest:digest,
      filename:'wrong.html',content_base64:'eA==',...mismatch});
    await assert.rejects(h.exportReport(),/版本不一致/);
    assert.equal(h.downloads.length,0);
  }
});
test('Changing profile during export requires a new explicit export',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.api=async()=>new Promise(resolve=>{release=resolve;entered()});
  const pending=h.exportReport();await ready;h.draft.reportProfiles={'a:1':'detailed'};
  release({task_id:'a',version:1});await assert.rejects(pending,/选择已变化/);assert.equal(h.downloads.length,0);
});
test('A chart change during export cannot save the earlier presentation',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  const presentation={theme:'research',schemes:{returns:'line'}};
  h.controls['#report-frame']={contentWindow:{RHVisuals:{profile:()=>presentation}}};
  h.reportReader.isReady=()=>true;
  h.api=async()=>new Promise(resolve=>{release=resolve;entered()});
  const pending=h.exportReport();await ready;presentation.schemes.returns='grouped_bar';
  release({task_id:'a',version:1,filename:'saved.html',content_base64:'eA=='});
  await assert.rejects(pending,/选择已变化/);
  assert.equal(h.downloads.length,0);assert.equal(h.exportButton.disabled,false);
});
test('Export rejects a different presentation for the correct version',async()=>{
  const h=harness();
  h.api=async()=>({task_id:'a',version:1,profile:'standard',report_digest:digest,filename:'saved.html',
    visual_profile:{theme:'porcelain',schemes:{}},content_base64:'eA=='});
  await assert.rejects(h.exportReport(),/导出响应与当前图表方案不一致/);
  assert.equal(h.downloads.length,0);
});
test('Offline export follows the visible standard fallback for an older saved task',async()=>{
  const h=harness({apiMode:false,draft:{reportProfiles:{'a:1':'detailed'}}});
  h.selected.report_html='<html>standard</html>';
  const view=h.reportView(h.selected);
  assert.ok(view.includes('data-report-profile="standard" aria-pressed="true"'));
  assert.ok(view.includes('srcdoc="&lt;html&gt;standard&lt;/html&gt;"'));
  await h.exportReport();assert.equal(await h.downloads[0].blob.text(),'<html>standard</html>');
});
test('Detailed offline export retains its own saved document',async()=>{
  const h=harness({apiMode:false,draft:{reportProfiles:{'a:1':'detailed'}}});
  h.selected.title='经营观察';
  h.selected.report_html='<html>standard</html>';h.selected.report_detailed_html='<html>detailed</html>';
  assert.ok(h.reportView(h.selected).includes('srcdoc="&lt;html&gt;detailed&lt;/html&gt;"'));
  await h.exportReport();assert.equal(await h.downloads[0].blob.text(),'<html>detailed</html>');
  h.draft.reportProfiles={'a:1':'standard'};
  await h.exportReport();assert.equal(await h.downloads[1].blob.text(),'<html>standard</html>');
  assert.deepEqual(h.downloads.map(file=>file.name),['经营观察_v1_详细报告.html','经营观察_v1_标准报告.html']);
});
test('Without a ready preview, each variant exports only its own stored chart schemes',async()=>{
  const h=harness({draft:{reportStyles:{
    'a:1':{theme:'porcelain',schemes:{legacy:'slope'}},
    'a:1:standard':{theme:'research',schemes:{profit:'slope'}},
    'a:1:detailed':{theme:'research',schemes:{prices:'lollipop'}},
    'a:2:detailed':{theme:'research',schemes:{later:'dumbbell'}}
  }}});
  await h.exportReport();
  h.draft.reportProfiles={'a:1':'detailed'};await h.exportReport();
  const exported=JSON.parse(JSON.stringify(h.requests)).map(({body})=>body.visual_profile);
  assert.deepEqual(exported,[{theme:'porcelain',schemes:{profit:'slope'}},{theme:'porcelain',schemes:{prices:'lollipop'}}]);
});
test('Duplicate export remains one in-flight save operation',async()=>{
  const h=harness();let release,entered;
  const ready=new Promise(resolve=>entered=resolve);
  h.api=async()=>new Promise(resolve=>{release=resolve;entered()});
  const pending=h.exportReport();await ready;assert.equal(h.exportButton.disabled,true);
  await h.exportReport();assert.equal(h.notices.at(-1),'正在导出，请稍候');
  release({task_id:'a',version:1,profile:'standard',report_digest:digest,filename:'saved.html',
    visual_profile:{theme:'research',schemes:{}},...htmlFile('<html>saved version</html>')});await pending;
  assert.equal(h.downloads.length,1);assert.equal(h.exportButton.disabled,false);
});

test('Native print follows the visible report and its visual settings',()=>{
  const h=harness(),frame={src:'http://127.0.0.1:43123/api/tasks/a/report/1?profile=standard',
    hasAttribute:name=>name==='src',
    getAttribute:name=>name==='data-report-digest'?digest:null,
    contentWindow:{RHVisuals:{profile:()=>({theme:'porcelain',schemes:{cash:'bars'}})}}};
  h.controls['#report-frame']=frame;
  h.reportReader.isReady=()=>true;
  const print=()=>h.window.ResearchHelperNative.printRequest();
  h.artifactsOpen=false;
  assert.equal(print(),null);
  h.artifactsOpen=true;
  h.reportReader.isReady=()=>false;
  assert.equal(print(),null);
  h.reportReader.isReady=node=>node===frame;
  assert.deepEqual(JSON.parse(JSON.stringify(print())),{url:frame.src,digest,presentation:{theme:'porcelain',schemes:{cash:'bars'}}});
  h.stage=2;
  assert.equal(print(),null);
  h.stage=3;
  h.selectingTaskId='b';
  assert.equal(print(),null);
});
