/* ResearchHelper operates on shared runtime artifacts, not a second browser engine. */
'use strict';
const $=s=>document.querySelector(s);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function sourceLocatorLabel(item){
  const locator=item.record_locator;
  if(!locator||typeof locator!=='object'||Array.isArray(locator))return String(item.locator??'');
  const parts=[];
  if(typeof locator.part==='string')parts.push(locator.part);
  for(const [key,label] of [['table','表'],['row','行']]){
    if(Number.isInteger(locator[key])&&locator[key]>=0)parts.push(label+(locator[key]+1));
  }
  if(typeof locator.field==='string')parts.push('字段'+locator.field);
  if(typeof locator.unit_part==='string')parts.push('单位依据'+locator.unit_part);
  return parts.length?parts.join('，'):String(item.locator??'');
}
const storageKey='researchhelper-demo-draft-v2';
const taskBackupLimit=20_000_000;
const taskPackageLimit=128*1024*1024;
const apiMode=location.protocol!=='file:';
const isBackupArchive=task=>task?.parent==='legacy-import'||typeof task?.legacy_backup_text==='string'||!!task?.legacy_backup;
const decodeBackup=bytes=>new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
// The local module owns theme subscriptions. File previews retain the bootstrap
// fallback because browsers do not permit file:// module imports.
if(apiMode)import('./login/theme.js').catch(()=>{});
// Use the same boundary for drawer behavior, resize recovery and workspace CSS.
const compactTasks=matchMedia('(max-width:700px)');
const compactContext=window.RHWorkspaceControls.compactReport;
let boot={},tasks=[],selected=null,stage=0,historical=null,token='',poll=null,previousFocus=null,draft={};
let artifactsOpen=false,sending=false,composing=false,contextOpen=false;
let taskSheetReturnFocus=null;
let pollGeneration=0,pollController=null,pollDeadline=null;
// Returning to an identical task is a new selection, not renewed authorization
// for an operation that was waiting when the user left it.
let taskSelectionGeneration=0;
let selectingTaskId='';
let pendingTaskView=null;
// Version reads belong to a task selection and reading surface, not whichever
// task happens to be selected when the server eventually replies.
let reportVersionRead=null,reportVersionDialog=null,modalGeneration=0;
const reportChoices=new Map();
let visualLibraryBinding=null;
let archiveFilePanel=null;
const taskTransition=window.RHConversationTransition.createConversationTransition($('#chat'));
// Original files stay in memory until sent. They are never written to browser storage.
let queuedMaterials=[],pendingSubmission=null;
// Reading and deduplicating files belongs to their composer, including while hidden.
const materialSelections=new WeakSet();
// Task-local File objects stay in this window; saved drafts carry only input
// text, the submission identity and attachment names and hashes.
const composerMemory=new Map();
// Bind each mutation to the task chosen before asynchronous configuration checks.
const pendingTaskActions=new Set();
const researchProcess=window.RHResearchProcess.create();
const deskInteractions=window.RHDeskInteractions.create($('#artifacts'));
const reportReader=window.RHReportReader.create($('#content'),{
  onReady:restoreReportStyles,
  readPosition(key){return draft.deskReadingPositions?.[key]},
  writePosition(key,position){saveReadingPosition('deskReadingPositions',key,position)},
  onScroll:()=>{if(workspaceReady&&!leaving)remember()},
  onAuthenticationRequired:()=>{if(apiMode&&boot.authentication)leaveWorkspace()}
});
let assistantOpen=false;
const assistantAvatar=window.RHDeskAvatar.createBloubAvatar();
const assistantToggle=$('[data-action="assistant-toggle"]');
assistantToggle.replaceChildren(assistantAvatar.element);
const workspaceModeMotion=window.RHWorkspaceModeMotion.createWorkspaceModeMotion({
  shell:$('[data-workspace-shell]'),workArea:$('#workspace'),form:$('#chat-form'),
  send:$('#chat-form .send-control'),toggle:assistantToggle,avatar:assistantAvatar.element,
});
$('#chat-form').addEventListener('input',workspaceModeMotion.stop);
$('#workspace').addEventListener('pointerdown',workspaceModeMotion.stop,{capture:true});
document.addEventListener('visibilitychange',()=>{if(document.hidden)workspaceModeMotion.stop()});
for(const event of ['pointerenter','focus'])$('[data-action="assistant-toggle"]').addEventListener(event,()=>assistantAvatar.interact());
function assistantActivity(task){
  if(sending)return 'connecting';
  if(!task)return $('#chat-input').value.trim()?'listening':'idle';
  if(task.status==='失败')return 'failed';
  if(task.status==='已取消')return 'cancelled';
  if(task.status!=='运行中')return $('#chat-input').value.trim()?'listening':'idle';
  const node=(task.nodes||[]).find(item=>item.status==='运行中');
  const phase=String(node?.id||'')+' '+String(node?.title||node?.name||'');
  if(/render|deliver|report|freeze|报告|交付|呈现|冻结/.test(phase))return 'composing';
  if(/acquir|fetch|read|取证|取数|获取|读取/.test(phase))return 'searching';
  if(/calc|计算/.test(phase))return 'calculating';
  return 'thinking';
}
let contextReturnFocus=null;
let taskListSnapshot=null;
// Presentation is thread-scoped, but never part of a research input or result.
let taskPresentations={},showArchived=false;
const pendingPresentationActions=new Set();
let draftKey='',leaving=false,accountBusy=false,importing=false,reviewBusy=false;
let initializing=false,workspaceReady=false;
let draftRevision=null,savedDraftWire='{}',currentDraftWire='{}';
let draftSaveTimer=null,draftWrite=null,draftAttempt=null,draftConflict=false,draftSaveNotice=false;
const authEventKey='researchhelper-auth-event-v1';
const materialLimits={files:10,bytes:50*1024*1024,individual:25*1024*1024};
const labels=['研究概览','数据来源','研究底稿','研究报告','跟踪复核'];
function saveReadingPosition(field,key,position){
  position={...position,top:Math.max(0,position.top),ratio:Math.min(1,Math.max(0,position.ratio))};
  rememberDraftEntry(field,key,position);
}
function rememberDraftEntry(field,key,value){
  const entries=Object.entries(draftObject(draft[field])?draft[field]:{}).filter(([name])=>name!==key);
  if(value!=null)entries.push([key,value]);
  draft[field]=Object.fromEntries(entries.slice(-100));
}
const conversationReader=window.RHConversationScroll.createConversationReader($('#chat'),$('#chat-form'),{
  read(key){return draft.readingPositions?.[key]},
  write(key,position){saveReadingPosition('readingPositions',key,position)},
});
function conversationReadingKey(){return selected&&!historical?selected.id+':'+(artifactsOpen?'desk':'chat'):null}
// Same OH-derived reader, but a separate task/version/view namespace. It owns
// only the Desk viewport; report documents keep their own iframe scrolling.
const deskReader=window.RHConversationScroll.createConversationReader($('#artifacts'),null,{
  read(key){return draft.deskReadingPositions?.[key]},
  write(key,position){saveReadingPosition('deskReadingPositions',key,position)},
});
function deskReadingKey(){
  if(!artifactsOpen)return null;
  if(historical)return JSON.stringify(['history',historical.key,stage]);
  return selected?JSON.stringify(['task',selected.id,stage===3?(shownReport(selected)?.version??selected.version):selected.version,stage]):null;
}
function deskStateKey(){return historical?'history:'+historical.key:composerKey()}
function canUseDesk(){return !apiMode||boot.authentication===false||boot.role==='admin'}
function savedDeskStage(){
  const saved=Number(draft.deskStages?.[deskStateKey()]);
  return Number.isInteger(saved)&&saved>=0&&saved<labels.length?saved:historical?3:2;
}
const conversationOutline=window.RHConversationOutline.createConversationOutline({
  stream:$('#chat'),container:$('#chat-scroll-stage'),navigate:target=>conversationReader.navigate(target),
});
const composerControls=window.RHComposerControls.create({
  input:$('#chat-input'),form:$('#chat-form'),panel:$('#assistant-panel'),workArea:$('#workspace'),
  dropTarget:$('.composer--shared'),fileInput:$('#material-file'),queue:queueMaterials,
  canQueue:assertCanQueueMaterials,onError:notice,
});
const taskSearch=window.RHTaskSearch.createTaskSearch({
  entries:query=>taskCatalog(query).map(task=>({id:task.id,title:taskTitle(task)})),
  select:row=>selectTaskEntry(row.id,{catalogOnly:true}),
  canOpen:()=>workspaceReady&&!leaving,
  canSelect:()=>!leaving&&!sending&&!importing&&!accountBusy,
  beforeOpen(){window.RHTaskMenu?.close();window.RHWorkspaceControls?.closeAccountMenu();setContext(false,{restoreFocus:false})},
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
taskSearch.mount($('#task-list-heading'));
const userGuide=window.RHUserGuide.createUserGuide({
  trigger:$('[data-user-guide]'),canOpen:()=>workspaceReady&&!leaving,canUseDesk,
  beforeOpen(){window.RHTaskMenu?.close();window.RHWorkspaceControls?.closeAccountMenu();setContext(false,{restoreFocus:false})},
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
const messageActions=window.RHMessageActions.createMessageActions({
  canRun:()=>workspaceReady&&!leaving,
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
function notice(message){$('#notice').textContent=message;$('#notice').classList.add('show');clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('#notice').classList.remove('show'),5500)}
function loginURL(){const query=new URLSearchParams(location.search);query.delete('account');return '/login/index.html'+(query.size?'?'+query:'')}
function leaveWorkspace(){
  if(leaving)return;
  workspaceModeMotion.destroy();
  artifactExport.cancelPreparation();
  if(draftKey)captureDraft();
  cancelTaskSelection();
  leaving=true;clearTimeout(draftSaveTimer);taskSelectionGeneration+=1;stopWatching();
  window.RHWorkspaceControls?.closeAccountMenu();
  window.RHWorkspaceControls?.closeWorkMenu();
  window.RHTaskMenu?.close();
  window.RHWorkspaceSettings?.close();
  taskSearch.destroy();
  userGuide.destroy();
  messageActions.destroy();
  researchProcess.destroy();
  deskInteractions.destroy();
  reportReader.destroy();
  // All identity invalidation paths clear native report windows, not just logout.
  try{window.webkit?.messageHandlers?.researchhelper?.postMessage({action:'signed_out'})}catch{}
  conversationOutline.destroy();conversationReader.destroy();
  window.RHScrollbar?.installScrollbarActivity().teardown();
  composerControls.destroy();
  tasks=[];selected=null;historical=null;queuedMaterials=[];pendingSubmission=null;composerMemory.clear();reportChoices.clear();reportVersionDialog=null;draft={};
  closeArchivedFiles();
  $('#chat-input').value='';$('#dialog-content').replaceChildren();$('#dialog').close();
  $('#chat').replaceChildren();$('#content').replaceChildren();$('#task-list').replaceChildren();
  $('[data-workspace-shell]').inert=true;$('[data-workspace-shell]').style.visibility='hidden';
  location.replace(loginURL());
}
async function api(path,body,{workspaceView=true,retainWire=false,blobMime=null,timeout=body?null:10000,signal=null}={}){
  if(leaving)throw Error('登录状态已改变，请重新登录');
  // Writes only opt into a timeout when the same write can safely be retried.
  const controller=timeout!==null||signal?new AbortController():null;
  const abort=()=>controller.abort();
  if(signal?.aborted)abort();
  else signal?.addEventListener('abort',abort,{once:true});
  const deadline=timeout===null?null:setTimeout(abort,timeout);
  try{
    const binary=body instanceof Blob;
    const headers={...(body?{'Content-Type':binary?'application/zip':'application/json','X-ResearchHelper-Token':token}:{}),...(workspaceView?{'X-ResearchHelper-View':path==='/api/bootstrap'?'workspace-v2':'workspace-v1'}:{})};
    const payload=binary||typeof body==='string'?body:body?JSON.stringify(body):undefined;
    const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller?.signal,headers,body:payload});
    if(signal?.aborted)throw new DOMException('读取已取消','AbortError');
    if(response.status===401){leaveWorkspace();throw Error('登录已失效，草稿保留在原账户')}
    if(response.ok&&blobMime){
      if(response.headers.get('Content-Type')!==blobMime)throw Error('备份文件响应格式不匹配');
      const blob=await response.blob();
      if(leaving)throw Error('登录状态已改变，请重新登录');
      return blob;
    }
    let value,wire;
    try{
      if(retainWire){wire=decodeBackup(await response.arrayBuffer());value=JSON.parse(wire.replace(/^\uFEFF/,''))}
      else value=await response.json();
    }catch(error){if(error.name==='AbortError')throw error;throw Error(body?'本地响应无法读取，请先检查任务状态，不要重复提交。':'本地响应无法读取，请重新连接。')}
    if(!response.ok)throw Object.assign(Error(value.error||'本地请求失败'),{failureCode:value.failure_code,status:response.status});
    if(leaving)throw Error('登录状态已改变，请重新登录');
    return retainWire?{value,wire}:value;
  }catch(error){
    if(error.name==='AbortError'&&!signal?.aborted)throw Object.assign(Error('本地服务读取超时，请重新连接。'),{name:'TimeoutError'});
    throw error;
  }finally{
    if(deadline!==null)clearTimeout(deadline);
    signal?.removeEventListener('abort',abort);
  }
}
function composerKey(){return selected?.id||'new'}
function saveComposerAttachments(key,submission,materials){
  if(!apiMode)return;
  for(const field of ['pendingSubmissions','composerFiles'])if(!draftObject(draft[field]))draft[field]={};
  const files=materials.filter(item=>item.sha256&&item.status!=='正在读取').map(item=>({
    name:item.file.name,size:item.file.size,sha256:item.sha256
  }));
  if(submission)draft.pendingSubmissions[key]={...submission,files};
  else delete draft.pendingSubmissions[key];
  if(!submission&&files.length)draft.composerFiles[key]=files;
  else delete draft.composerFiles[key];
}
function validComposerFiles(files){
  return Array.isArray(files)&&files.length<=materialLimits.files&&files.every(file=>
    typeof file?.name==='string'&&file.name&&Number.isSafeInteger(file.size)&&file.size>0&&
    file.size<=materialLimits.individual&&/^[0-9a-f]{64}$/.test(file.sha256))&&
    files.reduce((total,file)=>total+file.size,0)<=materialLimits.bytes;
}
function storedSubmission(key){
  const value=draft.pendingSubmissions?.[key];
  const parent=value?.parent;
  if(!value||typeof value.key!=='string'||!(/^[0-9a-f]{64}$/).test(value.key)||
    typeof value.nonce!=='string'||!(/^[0-9a-f]{32}$/).test(value.nonce)||
    value.taskId!=null&&(typeof value.taskId!=='string'||!(/^rh_[0-9a-f]{32}$/).test(value.taskId))||
    parent!==undefined&&(!parent||parent.id!==key||!Number.isSafeInteger(parent.version)||parent.version<1||
      !Number.isSafeInteger(parent.revision)||parent.revision<1)||
    !validComposerFiles(value.files))return null;
  return value;
}
function saveComposerDraft(){
  if(leaving)return;
  composerControls.sync();
  conversationReader.capture();
  deskReader.capture();
  reportReader.capture();
  if(!draft.composerTexts||typeof draft.composerTexts!=='object'||Array.isArray(draft.composerTexts))draft.composerTexts={};
  draft.chat=$('#chat-input').value;draft.composerTexts[composerKey()]=draft.chat;
  composerMemory.set(composerKey(),{materials:queuedMaterials,pending:pendingSubmission});
  saveComposerAttachments(composerKey(),pendingSubmission,queuedMaterials);
}
function restoreComposerDraft(){
  const key=composerKey(),text=draft.composerTexts?.[key],memory=composerMemory.get(key);
  $('#chat-input').value=typeof text==='string'?text:'';draft.chat=$('#chat-input').value;
  pendingSubmission=memory?memory.pending:storedSubmission(key);
  const files=pendingSubmission?.files||draft.composerFiles?.[key];
  queuedMaterials=memory?.materials||(validComposerFiles(files)?files:[]).map(file=>({
    file:{name:file.name,size:file.size},sha256:file.sha256,needsFile:true,status:'待重新选择'
  }));
  renderQueuedMaterials();
  composerControls.sync();
}
// Packaged case papers are readonly resources, not user draft data.
// Persist their references so eight offline cases cannot exhaust localStorage.
function samePackagedEdition(task,template){
  const saved=task.delivery, packaged=template?.delivery;
  return task.synthetic===true&&template?.synthetic===true&&task.id===template.id&&task.fixture===template.fixture&&
    task.version===template.version&&saved?.version===task.version&&saved.version===packaged?.version&&
    /^[a-f0-9]{64}$/.test(saved.digest||'')&&saved.digest===packaged.digest&&saved.html_sha256===packaged.html_sha256;
}
function offlineTasksForStorage(records){
  const templates=new Map((boot.tasks||[]).map(task=>[task.fixture,task]));
  return records.map(task=>{
    const template=templates.get(task.fixture);
    if(!samePackagedEdition(task,template)||task.report_html!==template.report_html||task.report_detailed_html!==template.report_detailed_html)return task;
    const {report_html,report_detailed_html,...record}=task;
    return {...record,demo_report_ref:task.fixture};
  });
}
function writeOfflineWorkspace(records=tasks,active=selected?.id,presentations=taskPresentations){
  localStorage.setItem(storageKey,JSON.stringify({workspaceVersion:4,tasks:offlineTasksForStorage(records),selected:active,stage,draft,taskPresentations:presentations}));
}
function restoreOfflineTask(task,templates){
  if(!task.demo_report_ref||task.demo_report_ref!==task.fixture||typeof task.report_html==='string'||typeof task.report_detailed_html==='string')return task;
  const template=templates.find(item=>samePackagedEdition(task,item));
  if(!template)return task;
  return {...task,report_html:template.report_html,report_detailed_html:template.report_detailed_html};
}
function draftObject(value){return !!value&&typeof value==='object'&&!Array.isArray(value)}
function workspaceDraftWire(source){
  const state={};
  const fields=['activeTaskId','activeHistoryKey','workspaceMode','assistantOpen','deskStages','composerTexts',
    'composerFiles','pendingSubmissions','readingPositions','deskReadingPositions','reportEditions','reportProfiles','reportStyles',...scopeFields];
  for(const field of fields){
    let value=source[field];
    if(value==null||field==='workspaceMode'&&value==='chat'||field==='assistantOpen'&&!value)continue;
    if(field==='composerTexts'&&draftObject(value))value=Object.fromEntries(Object.entries(value).filter(([,text])=>text));
    if(field==='reportEditions'){
      if(!draftObject(value))continue;
      value=Object.fromEntries(Object.entries(value).filter(([id,edition])=>/^rh_[a-f0-9]{32}$/.test(id)&&validReportBookmark(edition))
        .map(([id,{version,digest}])=>[id,{version,digest}]));
    }
    if(draftObject(value)&&!Object.keys(value).length)continue;
    state[field]=value;
  }
  return JSON.stringify(state,(_,value)=>draftObject(value)
    ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,value[key]])):value);
}
function loadWorkspaceDraft(saved){
  let local=null,ownMirror=false;
  try{
    local=JSON.parse(sessionStorage.getItem(draftKey));
    ownMirror=draftObject(local);
  }catch{}
  if(!ownMirror)try{local=JSON.parse(localStorage.getItem(draftKey))}catch{}
  if(!draftObject(local))local=null;
  const mirror=local?._workspaceDraft;
  if(local)delete local._workspaceDraft;
  if(!draftObject(saved?.state)||!Number.isSafeInteger(saved.revision)||saved.revision<0){
    draft=local||{};
    if(saved===null)notice('上次草稿无法读取，可继续编辑当前草稿。');
    return;
  }
  savedDraftWire=workspaceDraftWire(saved.state);
  const sameBase=mirror?.revision===saved.revision||mirror?.attempt&&mirror.attempt===saved.write_id;
  const dirty=mirror?.dirty===true&&workspaceDraftWire(local)!==savedDraftWire;
  if(ownMirror&&dirty&&!sameBase){
    draft=local;draftRevision=mirror.revision;draftConflict=true;
    notice('另一窗口已保存草稿，本窗口的内容仅保留在此页。');
    return;
  }
  draftRevision=saved.revision;
  draft=local&&(sameBase&&(!dirty||ownMirror)||!mirror&&draftRevision===0)?local:saved.state;
}
function saveDraftMirror(){
  const raw=JSON.stringify({...draft,_workspaceDraft:{
    revision:draftRevision,
    dirty:currentDraftWire!==savedDraftWire||!!draftAttempt&&currentDraftWire!==draftAttempt.wire,
    attempt:draftAttempt?.id??null
  }});
  let saved=false;
  try{sessionStorage.setItem(draftKey,raw);saved=true}catch{}
  if(!draftConflict)try{localStorage.setItem(draftKey,raw);saved=true}catch{}
  return saved;
}
async function confirmWorkspaceDraft(result,attempt,revision,account){
  if(result?.revision===revision+1&&result.write_id===attempt.id)return result.revision;
  // Identical state from another writer is a valid no-op on the server.
  // A different acknowledgement only settles the write after direct readback.
  const saved=await api('/api/workspace-draft',undefined,{timeout:4000});
  if(!draftObject(saved?.state)||!(saved.account===null||typeof saved.account==='string')||
     !Number.isSafeInteger(saved.revision)||saved.revision<revision||
     (saved.revision===0?saved.write_id!==null:typeof saved.write_id!=='string'||!/^[a-f0-9]{32}$/.test(saved.write_id)))
    throw Error('草稿保存状态未确认');
  if(saved.account!==account)
    throw Object.assign(Error('登录账户已改变，未确认草稿保存'),{status:403});
  if(workspaceDraftWire(saved.state)===attempt.wire)return saved.revision;
  if(saved.revision>revision)
    throw Object.assign(Error('另一窗口已保存更新的草稿'),{status:409});
  throw Error('草稿保存状态未确认');
}
function drainWorkspaceDraft(){
  clearTimeout(draftSaveTimer);draftSaveTimer=null;
  if(draftWrite)return draftWrite;
  if(draftRevision===null||draftConflict||leaving)return Promise.resolve(false);
  if(!draftAttempt&&currentDraftWire===savedDraftWire)return Promise.resolve(true);
  draftWrite=(async()=>{
    while(!leaving&&(draftAttempt||currentDraftWire!==savedDraftWire)){
      const attempt=draftAttempt??={id:byteHex(crypto.getRandomValues(new Uint8Array(16))),wire:currentDraftWire};
      saveDraftMirror();
      try{
        const account=boot.account??null,revision=draftRevision;
        const result=await api('/api/workspace-draft',{account,expected_revision:revision,
          write_id:attempt.id,state:JSON.parse(attempt.wire)},{timeout:4000});
        draftRevision=await confirmWorkspaceDraft(result,attempt,revision,account);
        savedDraftWire=attempt.wire;draftAttempt=null;draftSaveNotice=false;
        saveDraftMirror();
      }catch(error){
        if(leaving)return false;
        draftConflict=error.status===409||error.status===403;
        if(!draftSaveNotice)notice(draftConflict?'另一窗口已更新或切换账户，本窗口的草稿仅保留在此页。':
          '草稿暂未保存到应用，当前输入已保留。');
        draftSaveNotice=true;
        return false;
      }
    }
    return !leaving;
  })().finally(()=>{draftWrite=null});
  return draftWrite;
}
function remember(){
  if(leaving)return false;
  try{
    saveComposerDraft();
    draft.activeTaskId=selected?.id||null;draft.activeHistoryKey=historical?.key||null;
    draft.workspaceMode=artifactsOpen?'desk':'chat';
    if(!apiMode){
      writeOfflineWorkspace();
      return true;
    }
    if(!draftKey)return true;
    draft.assistantOpen=assistantOpen;
    currentDraftWire=workspaceDraftWire(draft);
    const mirrored=saveDraftMirror();
    if(workspaceReady&&draftRevision!==null&&!draftConflict){
      if(!draftWrite&&(draftAttempt||currentDraftWire!==savedDraftWire)){
        clearTimeout(draftSaveTimer);
        draftSaveTimer=setTimeout(drainWorkspaceDraft,250);
      }
      return true;
    }
    if(mirrored)return true;
  }catch{}
  notice('本地保存失败，请导出备份');
  return false;
}
async function flushWorkspaceDraft(){
  if(!workspaceReady||leaving)return false;
  captureDraft();
  return apiMode?drainWorkspaceDraft():true;
}
function prepareServiceRecovery(){
  if(!apiMode||!workspaceReady||leaving)return false;
  artifactExport.cancelPreparation();
  workspaceReady=false;
  stopWatching();
  clearTimeout(draftSaveTimer);draftSaveTimer=null;
  const saved=captureDraft();
  leaving=true;
  return saved;
}
// Chat drafts remain autosaved. Research settings are an explicit transaction:
// closing a sheet, changing mode or pagehide cannot adopt its unsaved inputs.
function captureDraft(){draft.chat=$('#chat-input').value;return remember()}
const scopeFields=['entry','intent','objects','research-mode','cutoff','requests','models','seconds'];
function researchMode(){return draft['research-mode']??(draft.cutoff?'historical':'current')}
function newResearchInput(question){
  // Restored drafts bypass native form validation. Validate the adopted scope
  // before configuration reads or task creation, not just when saving a sheet.
  const invalid=field=>{throw Error('研究设置中的'+field+'无效，请打开研究设置修正后再发送。')};
  const entry=draft.entry===undefined?'混合':draft.entry;
  const intent=draft.intent===undefined?'综合研究':draft.intent;
  if(typeof entry!=='string'||!boot.entries.includes(entry))invalid('研究类型');
  if(typeof intent!=='string'||!boot.intents.includes(intent))invalid('研究目标');
  const objects=draft.objects===undefined?'':draft.objects;
  if(typeof objects!=='string')invalid('研究对象');
  const limit=(field,fallback,label,count=false)=>{
    const raw=draft[field]===undefined?fallback:draft[field];
    if(!['number','string'].includes(typeof raw)||typeof raw==='string'&&!raw.trim())invalid(label);
    const value=Number(raw);
    if(!Number.isFinite(value)||value<=0||count&&(!Number.isInteger(value)||value>100000))invalid(label);
    return value;
  };
  const input={question,entry,intent,objects:objects.split(/[,，、]/).map(s=>s.trim()).filter(Boolean),
    research_mode:researchMode(),limits:{data_requests:limit('requests',100,'数据请求预算',true),
      model_calls:limit('models',8,'模型调用预算',true),seconds:limit('seconds',600,'运行时间预算')}};
  if(!['current','historical'].includes(input.research_mode))invalid('研究时点模式');
  if(input.research_mode==='historical'){
    const cutoff=draft.cutoff;
    if(typeof cutoff!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(cutoff))invalid('历史截止时点');
    const date=new Date(cutoff+':00Z');
    if(!Number.isFinite(date.getTime())||date.getUTCFullYear()<1||date.toISOString().slice(0,16)!==cutoff)invalid('历史截止时点');
    input.cutoff=cutoff+':00+08:00';
  }
  return input;
}
function syncResearchMode(){const current=$('#research-mode')?.value==='current';const field=$('[data-historical-cutoff]');if(field)field.hidden=current;const input=$('#cutoff');if(input){input.disabled=current;input.required=!current}}
function restoreDraft(){const form=$('#new-form');if(!form)return;for(const id of scopeFields){const input=form.querySelector('#'+id);if(input&&typeof draft[id]==='string'){input.value=draft[id];if(input.tagName==='SELECT')input.dispatchEvent(new Event('change',{bubbles:true}))}}}
function saveScopeSettings(form){
  if(leaving||!form.isConnected||!form.reportValidity())return false;
  const values=Object.fromEntries(scopeFields.map(id=>[id,form.querySelector('#'+id).value]));
  const previous=Object.fromEntries(scopeFields.filter(id=>Object.hasOwn(draft,id)).map(id=>[id,draft[id]]));
  Object.assign(draft,values);
  if(remember())return true;
  // Storage failures do not silently change the next research request. Keep
  // the local form editable while reverting only the attempted scope commit.
  for(const id of scopeFields){if(Object.hasOwn(previous,id))draft[id]=previous[id];else delete draft[id]}
  const feedback=form.querySelector('[data-scope-feedback]');
  if(feedback){feedback.hidden=false;feedback.textContent='本地保存失败，原设置未变。请释放存储空间后重新保存。'}
  return false;
}
// OH's mutually exclusive sheets and responsive report dialog, using RH's
// existing panels. One owner derives background isolation from both states.
function syncWorkspaceSheets(){
  if(leaving)return;
  const modal=contextOpen&&compactContext.matches;
  const taskSheet=compactTasks.matches&&$('.tasks').classList.contains('visible');
  const context=$('#task-context'),rail=$('#tasks');
  $('#workspace').inert=modal||taskSheet;rail.inert=modal;
  context.inert=!contextOpen;
  if(modal){context.setAttribute('role','dialog');context.setAttribute('aria-modal','true')}
  else{context.removeAttribute('role');context.removeAttribute('aria-modal')}
  if(taskSheet){
    rail.setAttribute('role','dialog');rail.setAttribute('aria-modal','true');
    rail.setAttribute('aria-labelledby','task-sheet-title');
  }else{
    rail.removeAttribute('role');rail.removeAttribute('aria-modal');rail.removeAttribute('aria-labelledby');
  }
  $('#context-scrim').hidden=!modal;
  document.documentElement.classList.toggle('workspace-overlay-open',modal||taskSheet);
}
function closeTasks({restoreFocus=false}={}){
  const rail=$('#tasks'),wasOpen=rail.classList.contains('visible');
  window.RHTaskMenu?.close();
  rail.classList.remove('visible');$('.task-scrim').hidden=true;
  syncWorkspaceSheets();window.RHWorkspaceControls.syncRail();
  if(wasOpen&&restoreFocus)focusWorkspaceControl(taskSheetReturnFocus,$('.web-control[data-action="tasks"]'));
  taskSheetReturnFocus=null;
}
function openTasks(){
  if(leaving||document.querySelector('dialog[open]'))return;
  const origin=document.activeElement;
  setContext(false,{restoreFocus:false});
  if(compactTasks.matches){
    taskSheetReturnFocus=origin;
    $('#tasks').classList.add('visible');$('.task-scrim').hidden=false;
    syncWorkspaceSheets();window.RHWorkspaceControls.syncRail();
    focusWorkspaceControl($('#tasks .drawer-close'),$('[data-action="new"]'));
  }else{
    window.RHWorkspaceControls.setRailCollapsed(false);
    focusWorkspaceControl($('.rail-task-search'),$('[data-action="new"]'));
  }
}
function focusWorkspaceControl(preferred,fallback){
  const target=[preferred,fallback,artifactsOpen?$('#view-'+stage):null,$('#workspace')].find(element=>element?.isConnected&&!element.closest('[inert], [hidden]')&&!element.matches(':disabled')&&element.getClientRects().length);
  target?.focus({preventScroll:true});
}
function canOpenTaskContext(){
  return !!selected&&!historical&&!selected.archive_unreadable&&!isBackupArchive(selected);
}
function setContext(open,{restoreFocus=true}={}){
  if(leaving||open&&(!canOpenTaskContext()||selectingTaskId||document.querySelector('dialog[open]')))return;
  const wasOpen=contextOpen,next=Boolean(open),panel=$('#task-context'),toggle=$('.web-control[data-action="artifacts"]');
  if(next!==wasOpen)workspaceModeMotion.stop();
  if(next&&!wasOpen){contextReturnFocus=document.activeElement;closeTasks();window.RHWorkspaceControls.closeAccountMenu()}
  contextOpen=next;$('[data-workspace-shell]').dataset.reportOpen=String(next);
  panel.hidden=!next;panel.setAttribute('aria-hidden',String(!next));
  toggle.setAttribute('aria-expanded',String(next));
  toggle.classList.toggle('is-active',next);
  toggle.setAttribute('aria-label',next?'收起任务与交付':'打开任务与交付');toggle.title=toggle.getAttribute('aria-label');
  syncWorkspaceSheets();
  if(next&&!wasOpen)focusWorkspaceControl(panel.querySelector('[data-action="close-context"]'),panel);
  else if(wasOpen&&!next){
    if(reportVersionRead?.fromContext){reportVersionRead=null;syncReportVersionControls()}
    if(restoreFocus)focusWorkspaceControl(contextReturnFocus,toggle);
    contextReturnFocus=null;
  }
}
function trapContextFocus(event){
  if(event.key!=='Tab'||!contextOpen||!compactContext.matches)return;
  const panel=$('#task-context');
  const available=[...panel.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]')]
    .filter(element=>element.tabIndex>=0&&!element.closest('[hidden], [inert]')&&!element.matches(':disabled')&&element.getClientRects().length);
  const first=available[0],last=available.at(-1);
  if(!available.includes(document.activeElement)){event.preventDefault();(event.shiftKey?last:first)?.focus()}
  else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
}
function toggleTasks(){
  if(compactTasks.matches){
    if($('#tasks').classList.contains('visible'))closeTasks({restoreFocus:true});
    else openTasks();
    return;
  }
  const collapse=$('[data-workspace-shell]').dataset.railCollapsed!=='true';
  const focusInRail=$('#tasks').contains(document.activeElement)||document.activeElement?.matches('[data-workspace-splitter="rail"]');
  window.RHWorkspaceControls.setRailCollapsed(collapse);
  if(collapse&&focusInRail)focusWorkspaceControl($('.web-control[data-action="tasks"]'),$('#chat-input'));
}
function resetScroll(){ $('#workspace').scrollTop=0 }
function cancelTaskSelection(){
  taskTransition.cancel();pendingTaskView=null;
  if(!selectingTaskId)return;
  selectingTaskId='';taskSelectionGeneration+=1;
  $('#chat-form').inert=false;$('#chat').removeAttribute('aria-busy');
}
function setTask(task,{submitted=false}={}){workspaceModeMotion.stop();cancelTaskSelection();taskSelectionGeneration+=1;setContext(false,{restoreFocus:false});saveComposerDraft();selected=task;historical=null;restoreComposerDraft();if(artifactsOpen)stage=savedDeskStage();closeTasks();remember();render();resetScroll();if(submitted)conversationReader.beginTurn();watch()}
function taskSnapshotMatches(task,id){
  // Validate the read envelope before changing visible content. This does not
  // approve financial evidence, infer missing fields or migrate old backups.
  return !!task&&!Array.isArray(task)&&task.id===id&&
    Number.isSafeInteger(task.version)&&task.version>=1&&
    Number.isSafeInteger(task.revision)&&task.revision>=0&&
    typeof task.question==='string'&&typeof task.cutoff==='string'&&task.cutoff.length>0&&
    ['待开始','运行中','已完成','部分完成','失败','已取消'].includes(task.status)&&
    Array.isArray(task.nodes)&&Array.isArray(task.questions);
}
function taskSnapshotIsOlder(task,current){
  return !!current&&(task.version<current.version||task.version===current.version&&task.revision<current.revision);
}
function markArchiveUnavailable(id){
  tasks=tasks.map(task=>task.id===id?{...task,archive_unreadable:true}:task);
  renderTaskList();
}
async function loadSelectedTask(id,navigation=null){
  workspaceModeMotion.stop();
  cancelTaskSelection();setContext(false,{restoreFocus:false});captureDraft();stopWatching();
  const generation=++taskSelectionGeneration,origin=document.activeElement;
  const current=()=>!leaving&&generation===taskSelectionGeneration&&selectingTaskId===id;
  const animate=!artifactsOpen&&!historical&&selected?.id&&selected.id!==id;
  selectingTaskId=id;pendingTaskView=navigation;
  artifactExport.updated();
  $('#chat').setAttribute('aria-busy','true');$('#chat-form').inert=true;
  // Match OH: retain readable content and overlap departure with the actual
  // read. No mutation, second engine, or loading placeholder is dispatched.
  const departure=animate?taskTransition.exit():Promise.resolve(true);
  try{
    const [fresh,departed]=await Promise.all([api('/api/tasks/'+encodeURIComponent(id)),departure]);
    if(!current()||!departed)return;
    if(!taskSnapshotMatches(fresh,id))throw Error('任务响应不匹配，请重新选择');
    const cached=tasks.find(task=>task.id===id);
    // A concurrent successful save may have advanced the local task while
    // this read was in flight. Never replace it with an older revision.
    if(cached?.workspace_summary_version===1&&taskSnapshotIsOlder(fresh,cached))throw Error('任务已更新，请重新选择');
    const task=taskSnapshotIsOlder(fresh,cached)?cached:fresh;
    const requested=pendingTaskView;
    if(requested?.reportVersion){
      let edition;
      try{edition=await readReportEdition(task,requested.reportVersion)}
      catch(error){if(pendingTaskView===requested)throw error}
      if(!current())return;
      if(taskSnapshotIsOlder(task,tasks.find(item=>item.id===id)))throw Error('任务已更新，请重新选择');
      if(pendingTaskView===requested)rememberReportEdition(task,edition);
    }else await restoreRememberedReport(task,current);
    if(!current())return;
    if(taskSnapshotIsOlder(task,tasks.find(item=>item.id===id)))throw Error('任务已更新，请重新选择');
    const next=pendingTaskView;
    tasks=tasks.map(item=>item.id===id?task:item);
    setTask(task);
    if(next?.mode==='desk'&&Number.isInteger(next.stage))view(next.stage);
    else if(next)switchMode(next.mode);
    if(animate&&!artifactsOpen)taskTransition.enter();
    if(!next)focusWorkspaceControl(origin,$('#chat-input'));
  }catch(error){
    if(current()){
      if(error.failureCode==='archive_record_unavailable')markArchiveUnavailable(id);
      notice(error.message);
    }
  }
  finally{
    if(current()){
      cancelTaskSelection();watch();focusWorkspaceControl(origin,$('#chat-input'));
    }
  }
}
function stopWatching(){
  pollGeneration+=1;
  clearTimeout(poll);clearTimeout(pollDeadline);
  poll=null;pollDeadline=null;
  pollController?.abort();pollController=null;
}
function taskNeedsPolling(task){return task?.status==='运行中'||task?.batch_settling===true}
function watch(){
  stopWatching();
  if(!apiMode||leaving||historical||!taskNeedsPolling(selected))return;
  const id=selected.id,generation=pollGeneration;
  let failures=0;
  const current=()=>!leaving&&!historical&&generation===pollGeneration&&selected?.id===id&&taskNeedsPolling(selected);
  const read=async()=>{
    poll=null;if(!current())return;
    const controller=new AbortController();pollController=controller;
    const deadline=setTimeout(()=>controller.abort(),10000);pollDeadline=deadline;
    let delay=500,continueReading=true;
    try{
      // Read-only recovery: never replay task creation, uploads or execution.
      const response=await fetch('/api/tasks/'+id,{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'X-ResearchHelper-View':'workspace-v1'}});
      if(!current())return;
      if(response.status===401){leaveWorkspace();return}
      if(response.status===403||response.status===404){
        notice(response.status===403?'当前账户无权读取该任务，请重新选择任务。':'该任务已不存在，请重新选择任务。');
        continueReading=false;return;
      }
      if(!response.ok){
        if(response.status!==408&&response.status!==429&&response.status<500){
          const error=await response.json().catch(()=>({}));
          if(!current())return;
          if(error.failure_code==='archive_record_unavailable')markArchiveUnavailable(id);
          notice(error.error||'任务状态读取失败，请重新选择任务。');continueReading=false;return;
        }
        throw Error('任务状态暂时无法读取');
      }
      const fresh=await response.json();
      if(!current())return;
      if(!taskSnapshotMatches(fresh,id)){
        notice('任务状态响应不匹配，请重新选择任务。');continueReading=false;return;
      }
      if(taskSnapshotIsOlder(fresh,selected)||taskSnapshotIsOlder(fresh,tasks.find(task=>task.id===id))){
        if(!failures)notice('任务状态返回旧版本，正在重新读取。');
        failures+=1;delay=Math.min(8000,1000*2**Math.min(failures-1,3));return;
      }
      const changed=JSON.stringify(fresh)!==JSON.stringify(selected);
      tasks=tasks.map(task=>task.id===id?fresh:task);selected=fresh;
      if(failures)notice('任务状态连接已恢复');
      failures=0;if(changed)render();
    }catch(error){
      if(!current())return;
      if(!failures)notice('任务状态连接暂时中断，正在自动重试。');
      failures+=1;delay=Math.min(8000,1000*2**Math.min(failures-1,3));
    }finally{
      clearTimeout(deadline);
      if(pollController===controller){pollController=null;pollDeadline=null}
      if(continueReading&&current())poll=setTimeout(read,delay);
    }
  };
  poll=setTimeout(read,500);
}
function rememberDeskStage(){if(!draft.deskStages||typeof draft.deskStages!=='object'||Array.isArray(draft.deskStages))draft.deskStages={};draft.deskStages[deskStateKey()]=stage}
function view(index){
  const next=Number(index);
  if(!canUseDesk()||!Number.isInteger(next)||next<0||next>=labels.length)return;
  if(selectingTaskId){pendingTaskView={mode:'desk',stage:next};return}
  workspaceModeMotion.stop();
  cancelTaskSelection();const fromContext=contextOpen,origin=document.activeElement;
  captureDraft();stage=next;rememberDeskStage();artifactsOpen=true;
  setContext(false,{restoreFocus:false});remember();render();resetScroll();watch();
  focusWorkspaceControl(fromContext?null:origin,$('#view-'+stage));
}
function switchMode(mode,trigger=null){
  if(mode!=='chat'&&(mode!=='desk'||!canUseDesk()))return;
  if(selectingTaskId){pendingTaskView={mode,stage:pendingTaskView?.stage};return}
  const nextDesk=mode==='desk',changed=artifactsOpen!==nextDesk;
  if(trigger&&!changed&&!$('.tasks').classList.contains('visible'))return;
  cancelTaskSelection();const origin=document.activeElement;captureDraft();
  const canAnimate=trigger&&changed&&!contextOpen&&!historical&&(!artifactsOpen||!assistantOpen);
  const previous=canAnimate?workspaceModeMotion.capture(artifactsOpen?'desk':'chat'):null;
  if(!previous)workspaceModeMotion.stop();
  if(artifactsOpen)rememberDeskStage();
  if(changed&&nextDesk)assistantOpen=false;
  artifactsOpen=nextDesk;if(artifactsOpen)stage=savedDeskStage();
  setContext(false);closeTasks();remember();render();resetScroll();watch();
  if(!artifactsOpen&&!historical)$('#chat-input').focus();
  else focusWorkspaceControl(origin,artifactsOpen?$('#view-'+stage):$('#chat .artifact-card'));
  workspaceModeMotion.play(previous,mode);
}
function renderAssistant(){
  conversationReader.enter(conversationReadingKey());
  const readOnly=!!historical||isBackupArchive(selected);
  const desk=artifactsOpen&&!readOnly,compact=matchMedia('(max-width:700px)').matches;
  const region=$('.composer-dock'),panel=$('#assistant-panel'),toggle=$('[data-action="assistant-toggle"]');
  region.hidden=readOnly;region.classList.toggle('is-open',desk&&assistantOpen);
  assistantAvatar.setActivity(assistantActivity(selected));
  assistantAvatar.setActive(desk&&!compact&&!assistantOpen);
  toggle.hidden=!desk;toggle.inert=desk&&assistantOpen;toggle.setAttribute('aria-expanded',String(desk&&assistantOpen));
  panel.inert=readOnly||desk&&!assistantOpen;
  panel.setAttribute('aria-hidden',String(panel.inert));
  if(desk){panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','false')}else{panel.removeAttribute('role');panel.removeAttribute('aria-modal')}
  $('[data-assistant-title]').textContent=selected?taskTitle(selected):'ResChat';
  const target=desk?$('#assistant-transcript'):$('#chat-scroll-stage');
  if($('#chat').parentElement!==target)target.append($('#chat'));
  window.RHWorkspaceControls?.syncAssistant();
  composerControls.sync();
  conversationReader.updated();
}
function setAssistantOpen(open){workspaceModeMotion.stop();assistantOpen=!!open;captureDraft();renderAssistant();(assistantOpen?$('#chat-input'):assistantToggle).focus({preventScroll:true})}
function statusTag(value,{tone:specified,title='',status}={}){
  const tone=['verified','attention','running','neutral'].includes(specified)?specified:
    ['成功','已完成','支持','已保存'].includes(value)?'verified':['失败','已取消','反驳','部分完成'].includes(value)?'attention':value==='运行中'?'running':'neutral';
  const detail=title?' title="'+esc(title)+'"':'';
  const original=typeof status==='string'?' data-research-status="'+esc(status)+'"':'';
  return '<span class="status" data-tone="'+tone+'"'+detail+original+'>'+esc(value)+'</span>';
}
function taskStatusTag(task){
  const presentation=window.RHResearchData.taskStatus(task);
  return statusTag(presentation.label,presentation);
}
function taskMessageCount(task){return Array.isArray(task.messages)?task.messages.length:task.workspace_summary_version===1&&Number.isSafeInteger(task.message_count)&&task.message_count>=0?task.message_count:0}
function formatTaskDate(value){
  if(typeof value!=='string'||!value)return '';
  const date=new Date(/^\d{4}-\d{2}-\d{2}$/.test(value)?value+'T00:00:00':value);
  if(Number.isNaN(date.getTime()))return '';
  const pad=number=>String(number).padStart(2,'0');
  return [date.getFullYear(),pad(date.getMonth()+1),pad(date.getDate())].join('/');
}
function taskListDate(task){
  return task.archive_unreadable?'档案无法读取':
    formatTaskDate(task.updated_at)||formatTaskDate(task.delivery?.saved_at)||formatTaskDate(task.created_at);
}
function taskCatalog(query=''){
  const latest=new Map();tasks.forEach(t=>{const key=t.thread_id||t.id,prior=latest.get(key);if(!prior||taskMessageCount(t)>taskMessageCount(prior)||(taskMessageCount(t)===taskMessageCount(prior)&&String(t.created_at||'')>String(prior.created_at||'')))latest.set(key,t)});
  return [...latest.values()].filter(t=>taskPresentation(t).archived===showArchived&&
    (!query||[taskTitle(t),t.status,t.cutoff,taskListDate(t)].join(' ').toLocaleLowerCase().includes(query)))
    .sort((a,b)=>Number(taskPresentation(b).pinned)-Number(taskPresentation(a).pinned));
}
function selectTaskEntry(id,{catalogOnly=false,reportVersion=null}={}){
  if(leaving||sending||importing||accountBusy){notice('正在保存研究，请稍候');return false}
  // Search resolves the current list. Report links may open an older round.
  const task=(catalogOnly?taskCatalog():tasks).find(item=>item.id===id);
  if(!task)return false;
  const openDeskReport=canUseDesk()&&Number.isSafeInteger(reportVersion)&&reportVersion>0;
  if($('#dialog').open)$('#dialog').close();
  if(apiMode){
    if(task.archive_unreadable)void openUnavailableArchive(task,openDeskReport?reportVersion:null);
    else void loadSelectedTask(id,openDeskReport?{mode:'desk',stage:3,reportVersion}:null);
  }else{
    setTask(task);
    if(openDeskReport)view(3);
  }
  return true;
}
function selectReportExample(key){
  if(leaving||sending||importing||accountBusy){notice('正在保存研究，请稍候');return}
  if(!canUseDesk()||!Object.hasOwn(boot.history||{},key))return;
  if($('#dialog').open)$('#dialog').close();
  cancelTaskSelection();
  taskSelectionGeneration+=1;captureDraft();stopWatching();closeTasks();
  historical={...boot.history[key],key};stage=3;artifactsOpen=true;setContext(false);
  rememberDeskStage();
  remember();render();resetScroll();focusWorkspaceControl(null,$('#view-3'));
}
function renderTaskList(){
  const visibleTasks=taskCatalog(),list=$('#task-list'),rail=list.closest('.task-rail');
  const label=$('#task-list-label'),catalogName=showArchived?'归档任务':'最近任务';
  const scroll=label.textContent===catalogName?rail.scrollTop:0;
  label.textContent=catalogName;
  const rows=visibleTasks.map(task=>({
    id:task.id,title:taskTitle(task),date:taskListDate(task),
    state:task.archive_unreadable?'unreadable':task.status==='运行中'?'running':'idle',
    presentation:taskPresentation(task),pending:pendingPresentationActions.has(task.thread_id||task.id),
  }));
  const snapshot=JSON.stringify([showArchived,rows]);
  if(taskListSnapshot!==snapshot){
    const focused=list.contains(document.activeElement)?document.activeElement.dataset:null;
    const row=({id,title,date,state})=>{
      return '<div class="task-list__row"><button type="button" class="task-item" data-task="'+esc(id)+'"'+
        (state==='unreadable'?' data-archive-unreadable="true"':'')+'><strong class="task-item__title"><span class="task-item__running-dot" data-state="'+state+'" '+
        (state==='unreadable'?'aria-hidden="true"':'role="img" aria-label="'+(state==='running'?'正在运行':'空闲')+'"')+
        '></span><span class="task-item__text">'+esc(title)+'</span></strong>'+
        (date?'<small class="task-item__date">'+esc(date)+'</small>':'')+'</button></div>';
    };
    window.RHTaskMenu.close();
    list.innerHTML=rows.length?rows.map(row).join(''):'<p class="task-item task-item--empty">'+(showArchived?'暂无归档任务':'尚无任务')+'</p>';
    window.RHTaskMenu.mount(list,id=>taskPresentation(tasks.find(t=>t.id===id)),taskMenuAction,
      id=>pendingPresentationActions.has(tasks.find(t=>t.id===id)?.thread_id||id),chatTaskMenuItems);
    taskListSnapshot=snapshot;rail.scrollTop=scroll;
    if(focused){
      const replacement=focused.menuTask?[...list.querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===focused.menuTask):
        [...list.querySelectorAll('[data-task]')].find(button=>button.dataset.task===focused.task);
      focusWorkspaceControl(replacement,$('.rail-task-search'));
    }
  }
  list.querySelectorAll('[data-task]').forEach(button=>button.setAttribute('aria-current',String(!historical&&selected?.id===button.dataset.task)));
  $('[data-action="archived-tasks"]').lastChild.textContent=showArchived?'返回最近任务':'归档任务';
  taskSearch.refresh();
}
function taskPresentation(task){return taskPresentations[task?.thread_id||task?.id]||{title:'',pinned:false,archived:false,revision:0}}
function taskTitle(task){return taskPresentation(task).title||(task.fixture?boot.cases?.find(c=>c.id===task.fixture)?.title||task.question:task.title||task.draft_report?.title||task.question)}
function validTaskPresentation(value){
  return draftObject(value)&&typeof value.title==='string'&&value.title.length<=120&&
    typeof value.pinned==='boolean'&&typeof value.archived==='boolean'&&
    Number.isSafeInteger(value.revision)&&value.revision>=0;
}
function mergePresentations(values){
  if(!draftObject(values))return;
  for(const task of tasks){
    const key=task.thread_id||task.id,value=Object.hasOwn(values,key)?values[key]:null;
    if(!validTaskPresentation(value)||value.revision<(taskPresentations[key]?.revision||0))continue;
    const {title,pinned,archived,revision}=value;
    taskPresentations[key]={title,pinned,archived,revision};
  }
}
async function changeTaskPresentation(task,changes,expected=taskPresentation(task).revision){
  const key=task.thread_id||task.id,account=boot.account;
  if(leaving||pendingPresentationActions.has(key))throw Error('任务操作正在保存，请稍后再试');
  pendingPresentationActions.add(key);renderTaskList();
  const pendingFocus=document.activeElement,selection=taskSelectionGeneration;
  const current=()=>!leaving&&account===boot.account;
  try{
    let value,writeError;
    if(apiMode){
      const path='/api/tasks/'+encodeURIComponent(task.id)+'/presentation';
      const belongs=receipt=>validTaskPresentation(receipt)&&receipt.thread_id===key;
      const matches=receipt=>Object.entries(changes).every(([field,requested])=>receipt[field]===requested);
      const unconfirmed='保存结果暂未确认，请重试。';
      try{
        value=await api(path,{expected_revision:expected,changes},{timeout:4000});
        if(!belongs(value)||value.revision!==expected+1||!matches(value))throw Error(unconfirmed);
      }catch(error){
        writeError=error instanceof TypeError?Error(unconfirmed):error;
        if(!current())throw writeError;
        // Reconcile the persisted metadata once. Never replay a write with an unknown outcome.
        const saved=await api(path,undefined,{timeout:4000}).catch(()=>null);
        if(!belongs(saved)||saved.revision<expected)throw writeError;
        value=saved;
        if(saved.revision>expected&&matches(saved))writeError=null;
      }
    }else{
      if(expected!==taskPresentation(task).revision)throw Error('任务展示设置已更新，请重新读取后修改');
      if(changes.archived&&tasks.some(t=>(t.thread_id||t.id)===key&&t.status==='运行中'))throw Error('研究仍在运行，请先取消或等待完成后归档');
      value={...taskPresentation(task),...changes,revision:expected+1};
      try{
        saveComposerDraft();draft.activeTaskId=selected?.id||null;
        writeOfflineWorkspace(tasks,selected?.id,{...taskPresentations,[key]:value});
      }catch{throw Error('本地保存失败，展示设置未更新。请导出备份后重试')}
    }
    if(current()){
      mergePresentations({[key]:value});
      if(apiMode)remember();
      render();
    }
    if(writeError)throw writeError;
  }finally{
    pendingPresentationActions.delete(key);
    if(!leaving){
      const restore=document.activeElement===pendingFocus&&selection===taskSelectionGeneration&&!document.querySelector('dialog[open]');
      renderTaskList();
      if(restore)focusWorkspaceControl([...$('#task-list').querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===task.id),$('[data-action="archived-tasks"]'));
    }
  }
}
async function taskMenuAction(id,action,trigger){
  const task=tasks.find(t=>t.id===id);if(!task||leaving)return;
  if(!['pin','rename','archive','restore'].includes(action)){
    const item=chatTaskMenuItems(id).find(([key])=>key===action);
    if(!item||item[3])return;
    closeTasks({restoreFocus:true});
    try{
      if(action==='versions')await openReportVersions(document.activeElement,{fromChat:true});
      else if(action==='rounds')openChatRounds();
      else if(action==='backup')await backupTask(trigger);
      else await taskAction(action);
    }catch(error){if(!leaving)notice(error.message)}
    return;
  }
  if(action==='rename'){
    let expected=taskPresentation(task).revision;
    modal('重命名任务','<form id="task-rename-form"><label for="task-name">任务名称</label><input id="task-name" maxlength="120" required value="'+esc(taskTitle(task))+'"><p id="task-name-feedback" role="status"></p><div class="form-actions"><button class="button-primary" type="submit">保存</button><button class="button-secondary" type="button" data-action="close">取消</button></div></form>');
    const form=$('#task-rename-form'),input=$('#task-name'),feedback=$('#task-name-feedback');
    const button=form.querySelector('[type=submit]');
    input.focus();input.select();
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(button.disabled)return;
      const title=input.value.trim();
      if(!title||title.length>120||/[\x00-\x1f\x7f]/.test(title)){
        feedback.textContent='名称须为1至120个字符，不能包含换行或控制字符';
        return;
      }
      feedback.textContent='';
      button.disabled=true;
      try{
        await changeTaskPresentation(task,{title},expected);
        if(form.isConnected&&$('#dialog').open){previousFocus=[...$('#task-list').querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===task.id);$('#dialog').close()}
        notice('任务名称已保存');
      }catch(error){
        if(!form.isConnected)return;
        const latest=taskPresentation(task).revision;
        if(latest>expected){
          expected=latest;
          feedback.textContent='任务设置已更新。当前名称为'+taskTitle(task)+'，核对后可再次保存。';
        }else feedback.textContent=error.message;
      }finally{button.disabled=false}
    });return;
  }
  try{await changeTaskPresentation(task,action==='pin'?{pinned:!taskPresentation(task).pinned}:{archived:action==='archive'});notice(action==='pin'?'置顶状态已保存':action==='archive'?'已归档，研究内容保留':'任务已恢复')}
  catch(error){if(!leaving)notice(error.message)}
}
function installReader(){
  // The report owns its scroll viewport. Expanding an iframe to the entire
  // document height makes its fixed directory leave the App's visible area.
  // CSS owns the reading viewport. The preview controller reports load failure
  // separately, without rewriting the frozen report document.
  $('#artifacts').dataset.reader=String(!!$('#report-frame'));
  reportReader.updated();
}
function paneReadingIdentity(element,pane){
  // Visible labels are not record identifiers: every execution node has the
  // same disclosure label, and knowledge titles may change during a poll.
  const scope=element.closest('[data-reading-key],[data-knowledge]');
  const section=element.closest('.section');
  const context=scope?['record',scope.dataset.readingKey||scope.dataset.knowledge]:
    ['section',section?.querySelector('h2')?.textContent||section?.className||'pane'];
  if(element.matches('details,summary')){
    const details=element.matches('details')?element:element.parentElement;
    return JSON.stringify([element.tagName,context,details.className||details.querySelector('summary')?.textContent]);
  }
  if(element.matches('.choice-trigger')){
    const select=element.closest('[data-choice-root]')?.querySelector('select[data-choice]');
    if(select?.id)return JSON.stringify(['choice',context,select.id]);
  }
  const attributes=['data-evidence','data-source','data-source-evidence','data-calculation','data-draft-basis',
    'data-action','data-retry-original','data-original-task','aria-label'];
  const identity=attributes.filter(name=>element.hasAttribute(name)).map(name=>[name,element.getAttribute(name)]);
  return identity.length||element.id?JSON.stringify([element.tagName,context,element.id,identity]):null;
}
function uniquePaneElements(elements,pane){
  const records=new Map();
  for(const element of elements){
    const key=paneReadingIdentity(element,pane);
    if(key)records.set(key,records.has(key)?null:element);
  }
  return records;
}
function reconcilePaneChildren(pane,html,same){
  // OH retains unchanged reasoning DOM. Apply the same principle to independent
  // Desk sections so a budget update cannot detach evidence, selections or a
  // frozen report iframe. Compare generated markup, not user-opened DOM state.
  const template=document.createElement('template');template.innerHTML=html;
  const retained=new Map();
  if(same)for(const node of pane.children){
    const key=node.renderedPaneHTML;
    if(key){if(!retained.has(key))retained.set(key,[]);retained.get(key).push(node)}
  }
  const desired=[...template.content.childNodes].map(fresh=>{
    const markup=fresh.nodeType===Node.ELEMENT_NODE?fresh.outerHTML:null;
    const node=markup&&retained.get(markup)?.shift()||fresh;
    if(markup)node.renderedPaneHTML=markup;
    return node;
  });
  const keeping=new Set(desired);
  // Remove obsolete siblings before placement. Otherwise an unchanged focused
  // section gets moved in front of a stale sibling, which itself blurs focus.
  for(const node of [...pane.childNodes])if(!keeping.has(node))node.remove();
  let anchor=pane.firstChild;
  for(const node of desired){
    if(node!==anchor)pane.insertBefore(node,anchor);
    anchor=node.nextSibling;
  }
}
function renderPane(html){
  const pane=$('#content'),edition=stage===3&&!historical?shownReport(selected):null;
  const key=JSON.stringify(historical?['history',historical.key,stage]:edition
    ?['report',edition.task_id,edition.version,edition.digest]:['task',selected?.id||'new',selected?.version||0,stage]);
  if(pane.dataset.pane===key&&pane.renderedHTML===html){reportReader.updated();artifactExport.updated();syncVisualLibrary();deskReader.updated();deskInteractions.updated();return}
  reportReader.capture();
  const same=pane.dataset.pane===key;
  const exportFormat=same?pane.querySelector('#report-format')?.value:null;
  const disclosures=same?uniquePaneElements(pane.querySelectorAll('details'),pane):new Map();
  const states=new Map([...disclosures].filter(([,element])=>element).map(([id,element])=>[id,element.open]));
  const focused=same&&pane.contains(document.activeElement)&&document.activeElement!==pane?document.activeElement:null;
  const focusKey=focused?paneReadingIdentity(focused,pane):null;
  pane.dataset.pane=key;
  pane.renderedHTML=html;
  reconcilePaneChildren(pane,html,same);
  const format=pane.querySelector('#report-format');
  if(exportFormat&&format&&[...format.options].some(option=>option.value===exportFormat&&!option.disabled))
    format.value=exportFormat;
  window.RHChoice.enhanceSelects(pane);
  for(const [id,element] of uniquePaneElements(pane.querySelectorAll('details'),pane)){
    if(element&&states.has(id))element.open=states.get(id);
  }
  if(focused&&!focused.isConnected){
    const controls=uniquePaneElements(pane.querySelectorAll('summary,button,a,[tabindex]'),pane);
    focusWorkspaceControl(controls.get(focusKey),pane);
  }
  installReader();
  artifactExport.updated();
  syncVisualLibrary();
  deskReader.updated();
  deskInteractions.updated();
}
function contextSummary(task){
  const row=(label,value)=>'<div><dt>'+label+'</dt><dd>'+esc(value)+'</dd></div>';
  const objects=(task.objects||[]).map(String).join('、');
  const status=window.RHResearchData.taskStatus(task).label;
  return '<dl class="context-facts">'+row('研究状态',status+(Number.isSafeInteger(task.version)?'，版本'+task.version:''))+(objects?row('研究对象',objects):'')+(task.cutoff?row('截止时点',cutoffLabel(task.cutoff)):'')+(task.synthetic?row('案例类型','合成案例'):'')+'</dl>';
}
function contextReport(task){
  const edition=latestReport(task);
  if(!edition||!apiMode&&!task.report_html)return '<p class="report-empty">当前任务尚无报告。</p>';
  const title=typeof edition.title==='string'&&edition.title.trim()?edition.title.trim():'研究报告';
  const versions=apiMode&&edition.version>1?
    '<button class="button-quiet" type="button" data-action="versions" aria-label="报告版本" aria-haspopup="dialog">版本'+edition.version+'</button>':'';
  return '<div class="context-report-heading"><h3>报告</h3>'+versions+'</div><div class="report-list">'+
    savedReportCard(title,{taskId:task.id,edition,inContext:true})+'</div>';
}
function syncTaskContextAvailability(){
  const available=canOpenTaskContext(),toggle=$('.workbench-head [data-action="artifacts"]');
  if(toggle.hidden===!available)return;
  toggle.hidden=!available;
  if(!available&&contextOpen)setContext(false,{restoreFocus:false});
  if(workspaceReady){
    try{window.webkit?.messageHandlers?.researchhelper?.postMessage({action:'delivery-state',available})}catch{}
  }
}
function render(){
  if(archiveFilePanel&&!archivedFilesCurrent(archiveFilePanel)){
    if($('#dialog').open&&archiveFilePanel.content.isConnected)$('#dialog').close();
    closeArchivedFiles();
  }
  const hasDesk=canUseDesk(),shell=$('[data-workspace-shell]');
  if(!hasDesk)artifactsOpen=false;
  syncTaskContextAvailability();
  conversationReader.enter(conversationReadingKey());
  deskReader.enter(deskReadingKey());
  const active=selected,archived=!historical&&isBackupArchive(active);
  $('#account-menu [data-action="scope"]').hidden=!!active||!!historical;
  renderQueuedMaterials();
  shell.dataset.mode=artifactsOpen?'desk':'chat';
  shell.dataset.hasDesk=String(hasDesk);
  $('[data-mode-switch]').querySelectorAll('button').forEach(button=>{
    const desk=button.dataset.mode==='desk';
    button.hidden=desk&&!hasDesk;
    button.setAttribute('aria-pressed',String(desk===artifactsOpen));
  });
  if(!artifactsOpen||stage!==3)window.RHWorkspaceControls.closeWorkMenu();
  $('#artifacts').hidden=!artifactsOpen;$('#chat').hidden=false;
  $('#chat-surface').hidden=artifactsOpen;
  renderAssistant();
  renderChat();
  $('#task-title').textContent=historical?historical.shortTitle||historical.title:active?taskTitle(active):'开始研究';
  $('#task-meta').innerHTML=historical?'<span>历史报告</span>':archived?'<span>导入档案</span>':active?'<time>'+esc(active.cutoff.slice(0,10))+'</time>'+taskStatusTag(active)+'<span>版本'+active.version+'</span>'+(active.synthetic?'<span class="case-tag">合成验收</span>':''):'';
  const contextAvailable=canOpenTaskContext(),detailsAvailable=contextAvailable&&artifactsOpen&&hasDesk;
  const report=$('#context-report'),reportHTML=contextAvailable?contextReport(active):'';
  if(report.renderedHTML!==reportHTML){report.innerHTML=reportHTML;report.renderedHTML=reportHTML}
  const operationList=$('#task-actions'),operationHTML=detailsAvailable?contextTaskActions(active):'';
  if(operationList.renderedHTML!==operationHTML){operationList.innerHTML=operationHTML;operationList.renderedHTML=operationHTML}
  $('#context-operations').hidden=!operationHTML;
  $('#context-details').hidden=!detailsAvailable;
  const context=$('#context-summary'),summary=detailsAvailable?contextSummary(active):'';
  if(context.renderedHTML!==summary){context.innerHTML=summary;context.renderedHTML=summary}
  renderTaskList();
  if(!hasDesk){artifactExport.updated();syncReportVersionControls();return}
  const views=$('.views');
  views.hidden=archived;
  views.querySelectorAll('button').forEach((button,i)=>{button.setAttribute('aria-selected',String(i===stage));button.tabIndex=i===stage?0:-1});
  window.RHWorkspaceControls.syncViewIndicator();
  $('#content').setAttribute('aria-labelledby',archived?'task-title':'view-'+stage);
  if(historical){renderPane(historyView());return}
  if(archived){renderPane('<section class="empty">'+backupArchiveBody()+'</section>');return}
  if(!active){renderPane('<div class="empty"><h2>'+labels[stage]+'尚未生成</h2><p>在对话中输入问题后，查看实际研究产物。</p><button data-action="chat">返回对话</button></div>');return}
  const panes=[overview,processView,conclusion,reportView,tracking];
  renderPane(panes[stage](active));
  syncReportVersionControls();
}
function nodeProgress(task){
  const nodes=Array.isArray(task.nodes)?task.nodes:[];
  const finished=new Set(['成功','部分成功','失败','取消','已取消','不支持']);
  return '已处理'+nodes.filter(node=>finished.has(node.status)).length+'/'+nodes.length+'项';
}
function reconcileChatMessages(chat,parts,sameTask){
  // Research progress is independent of prior messages. Keep unchanged DOM
  // connected so reading selections, keyboard focus and copy ownership survive.
  const previous=sameTask?new Map([...chat.children].map(node=>[node.renderedSlot,node])):new Map();
  if(!sameTask)chat.replaceChildren();
  const obsolete=new Set(chat.children);let anchor=chat.firstElementChild;
  for(const [slot,html] of parts){
    const existing=previous.get(slot);let node=existing;
    if(!node||node.renderedMessageHTML!==html){
      const template=document.createElement('template');template.innerHTML=html;
      node=template.content.firstElementChild;
      if(existing&&slot==='process')node=researchProcess.reconcile(existing,node);
      node.renderedSlot=slot;node.renderedMessageHTML=html;
      if(existing&&existing!==node){if(anchor===existing)anchor=existing.nextElementSibling;existing.remove();obsolete.delete(existing)}
    }
    if(node!==anchor)chat.insertBefore(node,anchor);
    anchor=node.nextElementSibling;obsolete.delete(node);
  }
  for(const node of obsolete)node.remove();
}
const reportCardIcons={
  file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h5',
  preview:'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Zm13 0a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z',
  download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  correction:'m15 5 4 4M4 20l5-1L21 7a2.8 2.8 0 0 0-4-4L5 15l-1 5Z',
  saved:'M5 12l4 4L19 6',
  draft:'M12 8v5m0 3h.01M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20',
};
const savedReportCardSelector='[data-chat-report-task],[data-chat-report-history]';
function reportCardIcon(name){
  return '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="'+reportCardIcons[name]+'"/></svg>';
}
function savedReportCard(title,{taskId,edition,historyKey,canCorrect=false,inContext=false}){
  const description=title+(edition?'，'+(edition.kind==='research_draft'?'草稿，':'')+'版本'+edition.version:'');
  const url=historyKey?historicalReportURL(historyKey):apiMode?savedReportURL(taskId,edition.version)+'?profile=standard':null;
  const button=(icon,action,label,attrs='')=>'<button type="button" data-action="'+action+'" aria-label="'+esc(label+'，'+description)+
    '" title="'+label+'"'+attrs+'>'+reportCardIcon(icon)+'</button>';
  const preview=url?reportLink(url,'artifact-card',reportCardIcon('preview'),'预览，'+description,'预览报告'):
    button('preview','preview-chat-report','预览报告',' class="artifact-card" aria-haspopup="dialog"');
  const binding=historyKey?' data-chat-report-history="'+esc(historyKey)+'"':
    ' data-chat-report-task="'+esc(taskId)+'" data-chat-report-version="'+edition.version+'" data-chat-report-digest="'+esc(edition.digest||'')+'"';
  const downloadButton=button('download','download-chat-report','下载报告');
  if(inContext){
    const draft=edition.kind==='research_draft',status=draft?'研究草稿':'报告已保存';
    return '<article class="report-item"'+binding+'><div class="report-item__heading"><strong>'+esc(title)+'</strong>'+
      '<span class="report-meta state-indicator" role="img" data-state="'+(draft?'partial':'succeeded')+'" aria-label="'+status+'" title="'+status+'">'+
      reportCardIcon(draft?'draft':'saved')+'</span></div><div class="report-item__actions">'+preview+downloadButton+'</div></article>';
  }
  return '<section class="message-artifact"'+binding+'><div class="message-artifact__identity">'+
    '<span class="message-artifact__mark" aria-hidden="true">'+reportCardIcon('file')+'</span>'+
    '<div class="message-artifact__heading"><strong class="artifact-card__title">'+esc(title)+'</strong><span>HTML</span></div></div>'+
    '<div class="message-artifact__actions">'+preview+downloadButton+
    (canCorrect?button('correction','correct-chat-report','更正报告',' aria-haspopup="dialog"'):'')+'</div></section>';
}
function localChatReport(card){
  const task=tasks.find(row=>row.id===card.dataset.chatReportTask),edition=latestReport(task);
  if(!task?.report_html||String(edition?.version)!==card.dataset.chatReportVersion||
    (edition.digest||'')!==card.dataset.chatReportDigest)return null;
  return {task,edition};
}
function previewLocalChatReport(button){
  // File demos have no report endpoint. The preview keeps the same saved
  // edition and its own origin, independent of the Desk reader and preferences.
  if(apiMode||selectingTaskId||historical)return;
  const saved=localChatReport(button.closest(savedReportCardSelector));
  if(!saved)return;
  const {task,edition}=saved;
  const title=edition.title||taskTitle(task);
  modal(title,'<iframe class="chat-report-preview" title="'+esc(title)+'，版本'+edition.version+
    '" sandbox="allow-scripts" srcdoc="'+esc(task.report_html)+'"></iframe>');
  previousFocus=button;
}
function previousChatReport(message){
  const id=message.task_id;
  if(typeof id!=='string'||!id)return '';
  const previous=tasks.find(task=>task.id===id);
  const saved=message.delivery,version=saved?.version??message.version;
  if(saved&&Number.isSafeInteger(version)&&version>0){
    const title=typeof saved.title==='string'&&saved.title.trim()?saved.title.trim():'研究报告';
    const availableOffline=previous?.report_html&&previous.delivery?.version===version&&
      typeof saved.digest==='string'&&previous.delivery.digest===saved.digest;
    if(apiMode||availableOffline)return savedReportCard(title,{taskId:id,edition:{...saved,version}});
  }
  return '';
}
function previousChatText(message,hasReport){
  const text=String(message.content??'');
  if(!hasReport&&message.status==='待开始'&&text==='本轮尚未形成研究结论。')return '';
  if(!hasReport||message.delivery?.kind!=='research_draft'||!message.delivery.title)return text;
  // Legacy draft snapshots include the whole workpaper. Present the conclusion
  // only when the frozen report for this exact turn is reachable.
  const prefix='研究初稿，尚未独立复核：'+message.delivery.title+'\n核心结论：';
  if(!text.startsWith(prefix))return text;
  const end=text.indexOf('\n策略逻辑1：',prefix.length);
  if(end===-1)return text.slice(prefix.length);
  const conclusion=text.slice(prefix.length,end).trim();
  if(!conclusion)return text;
  const gaps=text.slice(end).split('\n').filter(line=>line.startsWith('未解决：')&&!conclusion.includes(line.slice(4).trim()));
  return [conclusion,...new Set(gaps)].join('\n\n');
}
function chatAttachmentNames(message){
  if(!Array.isArray(message.attachments))return '';
  const files=new Map();
  for(const name of message.attachments){
    if(typeof name==='string'&&name.trim())files.set(name,(files.get(name)||0)+1);
  }
  if(!files.size)return '';
  const rows=[...files].map(([name,count])=>'<li class="message-attachment" title="'+esc(name)+'">'+
    '<svg class="message-attachment__mark" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h5"/></svg>'+
    '<span class="message-attachment__label">'+esc(name)+'</span>'+(count>1?'<span>×'+count+'</span>':'')+'</li>');
  return '<ul class="message-attachments message-attachments--files" data-chat-attachments aria-label="研究附件">'+rows.join('')+'</ul>';
}
function renderResearchWelcome(){
  return `<section class="conversation-start">
    <div>
      <div class="conversation-start__mark" aria-hidden="true"><img src="app-icon.svg" alt=""></div>
      <h2>今天想研究什么？</h2>
      <p class="conversation-start__copy">选一个场景，或直接输入研究问题。</p>
      <div class="conversation-starters">
        <button class="conversation-starter" type="button" data-starter-prompt="研究贵州茅台（600519.SH），比较最近两年的营收、归母净利润和经营现金流，结合最新估值梳理投资逻辑与主要风险。">
          <strong>个股研究</strong><span>分析财务、估值与主要风险。</span>
        </button>
        <button class="conversation-starter" type="button" data-starter-prompt="比较510300.SH与510330.SH，核对跟踪指数和费率，并用相同口径比较近一年表现。">
          <strong>ETF对比</strong><span>比较费率、跟踪指数与历史表现。</span>
        </button>
        <button class="conversation-starter" type="button" data-starter-prompt="研究券商行业，结合市场成交、券商盈利与行业估值，梳理景气驱动和后续跟踪指标。">
          <strong>行业分析</strong><span>梳理行业景气与主要驱动。</span>
        </button>
        <button class="conversation-starter" type="button" data-starter-prompt="研究最近一次LPR变动对银行板块的影响，梳理事件、传导机制和后续验证指标。">
          <strong>事件解读</strong><span>追踪事件进展与传导路径。</span>
        </button>
      </div>
    </div>
  </section>`;
}
function renderChat(){
  const chat=$('#chat'),task=selected;
  if(!task||historical||isBackupArchive(task))researchProcess.clear();
  if(historical){
    chat.renderedKey=null;chat.renderedHTML=null;$('#chat-surface').classList.remove('chat-surface--empty');
    const title=historical.shortTitle||historical.title;
    const card=savedReportCard(title,{historyKey:historical.key});
    chat.innerHTML=messageHTML('assistant','<p>'+esc(historical.conclusion)+'</p>',card);
    return;
  }
  $('#chat-surface').classList.toggle('chat-surface--empty',!task);
  if(!task){
    if(chat.renderedKey!=='welcome'){
      chat.renderedKey='welcome';chat.renderedHTML=null;chat.innerHTML=renderResearchWelcome();
    }
    return;
  }
  if(isBackupArchive(task)){
    const html='<section class="empty">'+backupArchiveBody()+'</section>';
    if(chat.renderedKey!==task.id||chat.renderedHTML!==html)chat.innerHTML=html;
    chat.renderedKey=task.id;chat.renderedHTML=html;
    return;
  }
  const theses=task.theses||[],answers=visibleAnswers(task),paper=window.RHResearchData.paper(task);
  const edition=latestReport(task),currentReport=edition?.version===task.version&&(apiMode||Boolean(task.report_html));
  const settled=['已完成','部分完成'].includes(task.status)&&task.batch_settling!==true&&!task.recovery_block?.reason;
  let response='';
  if(task.synthetic)response+='<p class="case-tag">合成案例，以下结果来自固定离线材料。</p>';
  response+=paper?draftReportCards(task,{summaryOnly:currentReport&&settled}):answerCards(task);
  if(theses.length&&!paper)response+=theses.map(t=>'<article class="chat-thesis"><h3>'+esc(t.title)+'</h3><p>'+esc(t.investment_meaning)+'</p><p class="muted">'+esc(t.limitation)+'</p></article>').join('');
  if(!paper){
    const displayedError=(task.batch_settling!==true?task.recovery_block?.reason:null)||(task.status==='失败'?task.error:null);
    response+=chatLimitations([task.error,...window.RHResearchData.researchGaps(task)].filter(value=>value!==displayedError),'待补充');
  }
  let report='';
  if(edition&&(apiMode||task.report_html)){
    const savedTitle=edition.title||(currentReport?paper?.title:null);
    const title=typeof savedTitle==='string'&&savedTitle.trim()?savedTitle.trim():'研究报告';
    report=savedReportCard(title,{taskId:task.id,edition,canCorrect:canCorrectChatReport(task,edition)});
  }
  const messages=task.messages?.length?task.messages:[{role:'user',content:task.question}];
  const parts=[];
  messages.forEach((message,index)=>{
    const user=message.role==='user';
    const attachment=user?chatAttachmentNames(message):previousChatReport(message);
    const text=user?message.content:previousChatText(message,Boolean(attachment));
    if((typeof text==='string'&&text.trim())||attachment){
      const content=user?esc(text):RHMessageProse.renderHTML(text);
      parts.push(['history:'+index,messageHTML(message.role,content,attachment)]);
    }
  });
  if(!settled||(!currentReport&&!paper&&!answers.length&&!theses.length)){
    parts.push(['process',researchProcess.html(task)]);
  }
  if(response||report)parts.push(['response',messageHTML('assistant',response,report)]);
  const html=parts.map(([,content])=>content).join(''),key=task.id;
  if(chat.renderedKey===key&&chat.renderedHTML===html)return;
  const same=chat.renderedKey===key;
  reconcileChatMessages(chat,parts,same);chat.renderedKey=key;chat.renderedHTML=html;
  messageActions.prune();
  researchProcess.sync(chat,task);
  conversationReader.updated({changed:true});
}
function messageHTML(role,content,attachment=''){
  const user=role==='user',label=user?'复制我的消息':'复制文字';
  const text=content?(user?'<p class="message__body message-text">'+content+'</p>':'<div class="message__body message-prose">'+content+'</div>'):'';
  const body=user?text+attachment:'<div class="message__content">'+text+attachment+'</div>';
  const copy=text?'<div class="message__actions"><button class="message__action" type="button" data-action="copy-message" data-copy-label="'+label+'" aria-label="'+label+'" title="'+label+'"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 9h12v12H9ZM5 15H3V3h12v2"/></svg></button><span class="message__copy-status" role="status"></span></div>':'';
  return '<article class="message message--'+role+' '+(user?'user-message':'assistant-message')+'">'+body+copy+'</article>';
}
function cacheTask(task){
  const prior=tasks.find(item=>item.id===task.id);
  if(!prior)tasks.unshift(task);
  else if(!taskSnapshotIsOlder(task,prior))tasks=tasks.map(item=>item.id===task.id?task:item);
  renderTaskList();
}
function materialRemovalPending(){return queuedMaterials.some(item=>item.removing)}
function materialSelectionPending(){return materialSelections.has(queuedMaterials)}
function byteHex(bytes){return Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')}
async function materialFileKey(item){
  if(!item.sha256){
    const digest=await crypto.subtle.digest('SHA-256',await item.file.arrayBuffer());
    item.sha256=byteHex(new Uint8Array(digest));
  }
  return {name:item.file.name,sha256:item.sha256};
}
async function removeQueuedMaterial(index){
  if(sending||importing||accountBusy||leaving||materialRemovalPending()||materialSelectionPending())return;
  const materials=queuedMaterials,item=materials[index];if(!item)return;
  // Only the current pending submission authorizes a task mutation.
  // A previous upload receipt may belong to a different question.
  const targetId=pendingSubmission?.taskId;
  if(!targetId){materials.splice(index,1);captureDraft();renderQueuedMaterials();$('#chat-input').focus();return}
  if(pendingTaskActions.has(targetId))return notice('研究操作正在处理，请稍候');
  const generation=taskSelectionGeneration,actor=boot.account,previousStatus=item.status,originKey=composerKey();
  const active=()=>!leaving&&boot.account===actor&&queuedMaterials===materials&&taskSelectionGeneration===generation;
  let removed=false;
  item.removing=true;item.status='正在移除';pendingTaskActions.add(targetId);renderQueuedMaterials();
  try{
    const key=await materialFileKey(item),retained=[];
    for(const other of materials)if(other!==item)retained.push(await materialFileKey(other));
    if(leaving||boot.account!==actor)return;
    const target=await api('/api/tasks/'+encodeURIComponent(targetId));
    if(!taskSnapshotMatches(target,targetId))throw Error('待发送任务状态未确认，附件仍保留');
    const fresh=await api('/api/tasks/'+encodeURIComponent(targetId)+'/remove-material',{
      ...key,retained_files:retained,expected_revision:target.revision,
      expected_task:{id:target.id,version:target.version,batch:target.batch,cutoff:target.cutoff,input_hash:target.input_hash}});
    if(leaving||boot.account!==actor)return;
    if(!taskSnapshotMatches(fresh,targetId)||fresh.version!==target.version||taskSnapshotIsOlder(fresh,target))
      throw Error('材料移除结果未确认，附件仍保留');
    cacheTask(fresh);
    if(selected?.id===fresh.id&&!historical&&!taskSnapshotIsOlder(fresh,selected))selected=fresh;
    const position=materials.indexOf(item);if(position!==-1)materials.splice(position,1);removed=true;
    if(active())captureDraft();
    else if(!leaving&&boot.account===actor&&composerMemory.get(originKey)?.materials===materials){
      saveComposerAttachments(originKey,composerMemory.get(originKey).pending,materials);remember();
    }
  }catch(error){item.status=previousStatus;if(!leaving&&boot.account===actor)notice(error.message)}
  finally{
    delete item.removing;pendingTaskActions.delete(targetId);
    if(!leaving&&boot.account===actor){
      if(selected?.id===targetId&&!historical)render();else syncComposerAvailability();
      if(active()){
        renderQueuedMaterials();
        (removed?$('#chat-input'):$('#queued-materials [data-remove-material="'+materials.indexOf(item)+'"]'))?.focus({preventScroll:true});
      }
    }
  }
}
async function sendChat({fromSubmit=false}={}){
  if(sending||importing||accountBusy||leaving||selectingTaskId)return;
  if(!workspaceReady){notice('工作台尚未连接，请先重新连接');return}
  // Retained reports are read-only, not the previously selected task's Chat.
  // Guard the action itself as well as hidden/disabled DOM controls, so native
  // shortcuts and delayed submit events cannot enter its execution path.
  if(historical){notice('历史报告仅供阅读，请新建研究');return}
  if(!historical&&selected&&pendingTaskActions.has(selected.id)){notice('研究操作正在处理，请稍候');return}
  if(selected?.batch_settling===true)return notice('上一批研究正在收尾，请稍候');
  if(isBackupArchive(selected))return;
  if(materialRemovalPending())return notice('正在移除材料，请稍候');
  if(materialSelectionPending())return notice('材料正在读取，请稍候');
  const composerInput=$('#chat-input'),submittedText=composerInput.value,question=submittedText.trim();
  const operation=fromSubmit?composerTaskOperation(selected,Boolean(question)):'';
  if(operation){
    captureDraft();
    try{await taskAction(operation)}catch(error){notice(error.message)}
    return;
  }
  if(!question||selected?.status==='运行中')return;
  // Earlier releases saved unfilled welcome templates in the draft store.
  const placeholder=!selected&&question.match(/^(?:请研究|请分析|请验证)(【(?:标的名称或代码|行业名称|事件说明|研究判断)】)/);
  if(placeholder){
    const start=submittedText.indexOf(placeholder[1]);
    composerInput.focus();
    composerInput.setSelectionRange(start,start+placeholder[1].length);
    notice('请填写括号中的研究内容。');
    return;
  }
  captureDraft();
  const origin=selected,originKey=composerKey(),generation=taskSelectionGeneration;
  const originVersion=origin?.version,originRevision=origin?.revision;
  const originContext=origin?{id:origin.id,version:originVersion,revision:originRevision}:null;
  // The server resolves this new question and its bound parent references.
  // Unrelated new-task settings must not override or block a follow-up.
  let input;
  try{input=origin?{question}:newResearchInput(question)}catch(error){notice(error.message);return}
  const materials=queuedMaterials;
  let submission=pendingSubmission;
  const current=()=>!leaving&&!accountBusy&&!historical&&taskSelectionGeneration===generation&&
    (selected?.id||null)===(origin?.id||null)&&selected?.version===originVersion&&selected?.revision===originRevision;
  const retainSubmission=()=>{
    if(leaving)return;
    composerMemory.set(originKey,{materials,pending:submission});
    saveComposerAttachments(originKey,submission,materials);
    if(current())pendingSubmission=submission;
    remember();
  };
  const checkSubmitted=task=>{
    const parent=task?.conversation_parent,expected=submission.parent;
    if(!taskSnapshotMatches(task,submission.taskId||task?.id)||!/^rh_[0-9a-f]{32}$/.test(task.id)||
      !Number.isSafeInteger(task.batch)||task.batch<0||
      task.question!==question||(task.parent||null)!==(origin?.id||null)||
      (origin&&parent?.task_id!==origin.id)||
      (expected&&(parent?.task_id!==expected.id||parent.version!==expected.version||parent.revision!==expected.revision))||
      taskSnapshotIsOlder(task,tasks.find(item=>item.id===task.id)))
      throw Error('发送记录与当前问题不一致，请重新读取任务。');
    return task;
  };
  const readSubmission=async()=>{
    const task=await api('/api/chat-submissions/'+submission.nonce);
    return task===null?null:checkSubmitted(task);
  };
  const recoverWrite=error=>error.status>=400&&error.status<500?Promise.resolve(null):readSubmission().catch(()=>null);
  const submitted=task=>task&&(task.batch>0||task.version>1||task.status!=='待开始');
  const finish=fresh=>{
    cacheTask(fresh);
    const remaining=materials.filter(item=>!savedComposerMaterial(fresh,item));
    composerMemory.set(originKey,{materials:remaining,pending:null});
    saveComposerAttachments(originKey,null,remaining);
    if(!current()){
      if(draft.composerTexts?.[originKey]===submittedText)draft.composerTexts[originKey]='';
      if(!historical&&composerKey()===originKey&&pendingSubmission?.nonce===submission.nonce){
        pendingSubmission=null;queuedMaterials=remaining;
        if($('#chat-input').value===submittedText)$('#chat-input').value='';
      }
      remember();
      return;
    }
    const input=$('#chat-input'),remainder=input.value===submittedText?'':input.value;
    queuedMaterials=[];pendingSubmission=null;input.value='';draft.chat='';
    setTask(fresh,{submitted:true});
    const existing=new Set(queuedMaterials.map(item=>composerMaterialIdentity(item.file.name,item.sha256)));
    queuedMaterials.push(...remaining.filter(item=>!existing.has(composerMaterialIdentity(item.file.name,item.sha256))));
    if(remainder)input.value=remainder;
    captureDraft();
  };
  const stopDispatch=()=>{if(!leaving)notice('任务已切换或更新，草稿和附件已保留，未继续执行。')};
  sending=true;syncComposerAvailability();
  // Bind retries to the same scope snapshot as the request.
  // Settings edited while configuration is loading apply to the next send,
  // not to this request or the pending task it may already have created.
  try{
    if(!apiMode){draft.question=question;captureDraft();notice('问题已保存为本地草稿；执行研究请在ResearchHelper.app打开。');return}
    const identity=new TextEncoder().encode(JSON.stringify({...input,parent:origin?.id||null}));
    const submissionKey=byteHex(new Uint8Array(await crypto.subtle.digest('SHA-256',identity)));
    if(!current())return stopDispatch();
    let task=submission?.key===submissionKey?await readSubmission():null;
    if(submitted(task)){
      if(!leaving)finish(task);
      return;
    }
    if(!current())return stopDispatch();
    if(origin&&submission?.key===submissionKey){
      const bound=task?.conversation_parent??submission?.parent;
      if(bound&&(bound.version!==originVersion||bound.revision!==undefined&&bound.revision!==originRevision)){
        if(task)cacheTask(task);
        submission=null;retainSubmission();
        return notice(task?'上次的追问已存为待开始任务，当前输入已保留。':'原研究已更新，请核对当前版本后重新发送。');
      }
    }
    if(!await verifyExecutionBeforeSend())return;
    if(!current())return stopDispatch();
    const expectedExecution=executionExpectation();
    const admission={...expectedExecution,has_attachments:materials.length>0};
    if(submission?.key!==submissionKey||origin&&!submission.parent&&!task){
      const missing=materials.find(item=>item.needsFile);
      if(missing)throw Error('请重新选择或移除'+missing.file.name);
      submission={key:submissionKey,nonce:byteHex(crypto.getRandomValues(new Uint8Array(16))),taskId:null,
        ...(originContext?{parent:originContext}:{})};
      retainSubmission();
    }
    if(!task){
      try{
        task=checkSubmitted(origin
          ?await api('/api/tasks/'+origin.id+'/messages',{message:question,start:false,submission_id:submission.nonce,
            expected_revision:originRevision,...admission})
          :await api('/api/tasks',{...input,submission_id:submission.nonce,...admission}));
      }catch(error){
        task=await recoverWrite(error);
        if(!task)throw error;
      }
      if(leaving)return;
    }
    submission={...submission,taskId:task.id};retainSubmission();cacheTask(task);
    if(submitted(task)){finish(task);return}
    if(!current())return stopDispatch();
    const id=task.id;
    for(const material of materials){
      if(!current())return stopDispatch();
      const saved=savedComposerMaterial(task,material);
      if(saved){markComposerMaterial(material,task,saved);continue}
      if(material.needsFile){
        material.status='待重新选择';
        throw Error('请重新选择或移除'+material.file.name);
      }
      material.status='保存中';renderQueuedMaterials();
      try{
        const {task:fresh,material:receipt}=await uploadMaterial(id,material.file,task.version,task.revision);
        if(leaving)return;
        task=fresh;cacheTask(fresh);markComposerMaterial(material,task,receipt);
      }catch(error){
        const recovered=await recoverWrite(error);
        const receipt=savedComposerMaterial(recovered,material);
        if(!receipt){material.status='保存失败';renderQueuedMaterials();throw error}
        task=recovered;cacheTask(task);markComposerMaterial(material,task,receipt);
      }
    }
    // Never dispatch inference before every original has been archived successfully.
    if(!current())return stopDispatch();
    const expectedTask={id,version:task.version,batch:task.batch,cutoff:task.cutoff,input_hash:task.input_hash};
    let fresh;
    try{
      fresh=checkSubmitted(await api('/api/tasks/'+id+'/start',{...expectedExecution,expected_task:expectedTask,submission_id:submission.nonce}));
    }catch(error){
      fresh=await recoverWrite(error);
      if(!submitted(fresh))throw error;
    }
    if(leaving)return;
    if(!submitted(fresh))throw Error('研究尚未开始，请重试核对。');
    finish(fresh);
  }catch(error){
    if(error.failureCode==='research_model_required'&&!leaving){
      if(!submission?.taskId){submission=null;retainSubmission()}
      showModelRequired(Boolean(submission?.taskId));
    }else if(error.failureCode==='followup_parent_changed'&&current()&&origin){
      submission=null;retainSubmission();
      await loadSelectedTask(origin.id);
      if(!leaving&&selected?.id===origin.id)notice(error.message);
    }else if(!leaving)notice(error instanceof TypeError||error.message.startsWith('本地响应无法读取')?'发送状态待核对，请重试。':error.message);
  }finally{
    sending=false;syncComposerAvailability();
    if(!leaving){captureDraft();renderQueuedMaterials()}
  }
}
function syncComposerAvailability(){
  // Like OH, pending controls belong only to the task currently shown. A late
  // reply for another task must never unlock or disable this task's composer.
  const pending=!historical&&selected&&pendingTaskActions.has(selected.id);
  const running=!historical&&selected?.status==='运行中';
  const settling=!historical&&selected?.batch_settling===true;
  const hasQuestion=Boolean($('#chat-input').value.trim());
  const operation=historical?'':composerTaskOperation(selected,hasQuestion);
  const actionText=operation==='cancel'||operation==='start'?'':taskActionLabels[operation]||'';
  const send=$('#chat-form .send-control'),attach=$('#chat-form [data-action="attach"]');
  send.classList.toggle('is-retry',Boolean(actionText));
  if(operation)send.dataset.taskOperation=operation;else delete send.dataset.taskOperation;
  const removing=materialRemovalPending(),reading=materialSelectionPending(),materialsBusy=removing||reading;
  const label=historical?'历史报告仅供阅读':removing?'正在移除材料':reading?'正在读取材料':
    settling?'上一批研究正在收尾':pending?(running?'正在停止研究':operation==='start'?'正在发送研究问题':'正在处理研究操作'):
    running?'停止研究':sending?'正在发送研究问题':actionText||'发送研究问题';
  send.disabled=!!historical||!workspaceReady||initializing||sending||importing||accountBusy||leaving||!!selectingTaskId||!!pending||settling||materialsBusy||!operation&&!hasQuestion;
  send.classList.toggle('is-generating',running);
  send.setAttribute('aria-busy',String(running||!!pending||settling||sending||materialsBusy));
  send.setAttribute('aria-label',label);
  send.title=label==='发送研究问题'&&!hasQuestion?(operation==='start'?'发送已保存的问题':'输入问题后发送'):label;
  $('#chat-form').setAttribute('aria-busy',String(sending||!!pending||materialsBusy));
  const hidingAttachment=running&&!attach.hidden&&document.activeElement===attach;
  attach.disabled=!!historical||sending||importing||accountBusy||leaving||materialsBusy||running;
  attach.hidden=running;
  if(hidingAttachment)focusWorkspaceControl(send,$('#chat-input'));
}
function attachmentSize(bytes){
  if(bytes<1024)return bytes+'B';
  if(bytes<1024*1024)return (bytes/1024).toFixed(bytes<10*1024?1:0)+'KB';
  return (bytes/(1024*1024)).toFixed(bytes<10*1024*1024?1:0)+'MB';
}
function renderQueuedMaterials(){
  const area=$('#queued-materials'),list=area.querySelector('.composer-attachment-list');
  const busy=materialRemovalPending()||materialSelectionPending();
  const disabled=sending||busy?' disabled':'';
  const html=queuedMaterials.map((item,index)=>{
    const name=esc(item.file.name),detail=item.status==='待加入研究'?attachmentSize(item.file.size):item.status;
    return '<article class="composer-attachment">'+
      '<span class="composer-attachment__preview" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h5"/></svg></span>'+
      '<span class="composer-attachment__text"><strong title="'+name+'">'+name+'</strong><small>'+esc(detail)+'</small></span>'+
      '<button class="composer-attachment__remove material-remove" type="button" data-remove-material="'+index+'" aria-label="移除材料'+name+'"'+disabled+'>'+
      '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg></button></article>';
  }).join('');
  area.hidden=!queuedMaterials.length;
  if(list.renderedHTML!==html){list.innerHTML=html;list.renderedHTML=html}
  syncComposerAvailability();
}
function assertCanQueueMaterials(){
  if(sending||importing||accountBusy||leaving||materialRemovalPending()||materialSelectionPending()||selected?.status==='运行中')throw Error('正在执行研究操作，请稍后添加材料');
  if(historical)throw Error('历史报告仅供阅读，请新建研究后添加材料');
  if(!apiMode)throw Error('独立HTML只保存问题草稿；材料解析请使用ResearchHelper.app');
}
function validateMaterialFiles(files){
  if(!files.length)throw Error('请选择研究材料');
  if(files.length>materialLimits.files)throw Error('一次最多添加10份材料，总体积不超过50MB');
  for(const file of files){
    if(!/\.(pdf|txt|md|docx|html|htm)$/i.test(file.name))throw Error('请选择PDF、TXT、Markdown、DOCX或HTML材料');
    if(!Number.isFinite(file.size)||file.size<=0)throw Error(file.name+'为空文件，请选择有正文的材料');
    if(file.size>materialLimits.individual)throw Error(file.name+'超过25MB，请压缩或分拆材料');
  }
  if(files.reduce((sum,file)=>sum+file.size,0)>materialLimits.bytes)throw Error('一次最多添加10份材料，总体积不超过50MB');
}
function composerMaterialIdentity(name,hash){return JSON.stringify([name,hash])}
function savedComposerMaterial(task,item){
  if(!task||task.material_exclusions?.some(row=>row.name===item.file.name&&row.sha256===item.sha256))return null;
  return task.materials?.find(row=>row.hash===item.sha256&&(row.name===item.file.name||
    item.savedTaskId===task.id&&row.id===item.savedArtifactId))||null;
}
function markComposerMaterial(item,task,receipt){
  item.savedTaskId=task.id;item.savedArtifactId=receipt.id;item.sha256=receipt.hash;item.status='已保存';
}
async function queueMaterials(files){
  assertCanQueueMaterials();validateMaterialFiles(files);
  const materials=queuedMaterials,existing=[...materials];
  const additions=files.map(file=>({file,status:'正在读取'}));
  const generation=taskSelectionGeneration,actor=boot.account,origin=document.activeElement,key=composerKey();
  const ownsAccount=()=>!leaving&&(!actor||boot.account===actor);
  const current=()=>ownsAccount()&&queuedMaterials===materials;
  // Keep the File objects visible to bootstrap and task switching before the
  // first asynchronous read. Only the originating composer owns this array.
  materials.push(...additions);materialSelections.add(materials);renderQueuedMaterials();
  let accepted=false;
  try{
    const seen=new Map(),unique=[];
    for(const item of [...existing,...additions]){
      let key;
      try{key=await materialFileKey(item)}catch{throw Error('无法读取'+item.file.name+'，请重新选择文件');}
      if(!ownsAccount())return;
      const identity=composerMaterialIdentity(key.name,key.sha256),previous=seen.get(identity);
      if(existing.includes(item)||!previous){seen.set(identity,item);unique.push(item)}
      else if(previous.needsFile){
        unique[unique.indexOf(previous)]=item;seen.set(identity,item);
      }
    }
    validateMaterialFiles(unique.map(item=>item.file));
    if(!ownsAccount())return;
    for(const item of unique)if(additions.includes(item))item.status='待加入研究';
    materials.splice(0,materials.length,...unique);accepted=true;
  }catch(error){
    materials.splice(0,materials.length,...existing);
    if(ownsAccount())throw error;
  }finally{
    materialSelections.delete(materials);
    if(accepted&&workspaceReady&&ownsAccount()){
      if(current())captureDraft();
      else if(composerMemory.get(key)?.materials===materials){
        saveComposerAttachments(key,composerMemory.get(key).pending,materials);remember();
      }
    }
    if(current()){
      renderQueuedMaterials();
      if(accepted&&taskSelectionGeneration===generation&&(document.activeElement===origin||document.activeElement===document.body)&&!$('#dialog').open)$('#chat-input').focus();
    }
  }
}
async function uploadMaterial(id,file,expectedVersion,expectedRevision){
  const bytes=new Uint8Array(await file.arrayBuffer());
  const digest=await crypto.subtle.digest('SHA-256',bytes);
  const expectedHash=byteHex(new Uint8Array(digest));
  const result=await api('/api/tasks/'+encodeURIComponent(id)+'/material',{name:file.name,base64:encodeBase64(bytes),include_record:true,...(expectedVersion===undefined?{}:{expected_version:expectedVersion}),...(expectedRevision===undefined?{}:{expected_revision:expectedRevision})});
  const fresh=result?.task,receipt=result?.material;
  if(!receipt||receipt.task_id!==id||receipt.research_version!==fresh?.version||receipt.hash!==expectedHash
    ||![...(fresh?.materials||[]),...(fresh?.pending_materials||[])].some(row=>row.id===receipt.id&&row.hash===receipt.hash))
    throw Error('材料上传回执未确认，附件仍保留');
  const cached=tasks.find(task=>task.id===id);
  if(leaving||!taskSnapshotMatches(fresh,id)||expectedVersion!==undefined&&fresh.version!==expectedVersion||taskSnapshotIsOlder(fresh,cached))
    throw Error('材料保存结果未确认，请重新读取任务状态，不要重复提交');
  return {task:fresh,material:receipt};
}
function openTaskMaterials(){
  if(!apiMode)return notice('材料解析请使用ResearchHelper.app');
  if(!selected||historical)return notice('请先选择研究任务');
  if(sending||importing||accountBusy||selected.status==='运行中')return notice('正在执行研究，请稍后补充材料');
  const id=selected.id,version=selected.version;
  modal('补充原问题材料','<form id="task-material-form"><label for="task-material-files">选择材料</label><input id="task-material-files" type="file" accept=".pdf,.txt,.md,.docx,.html,.htm" multiple required><p>仅保存到当前任务，不启动研究。已有结果保持不变，主动更新后纳入新版本。</p><p id="task-material-feedback" role="status" aria-live="polite"></p><button type="submit">保存材料</button></form>');
  const form=$('#task-material-form'),feedback=$('#task-material-feedback'),submit=form.querySelector('[type=submit]');
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(importing||sending||accountBusy)return;
    if(!selected||selected.id!==id||selected.version!==version||historical){feedback.textContent='任务已变化，请重新打开材料入口';return}
    const files=[...form.querySelector('input').files];
    try{validateMaterialFiles(files)}catch(error){feedback.textContent=error.message;return}
    importing=true;submit.disabled=true;form.setAttribute('aria-busy','true');
    try{
      for(const file of files){
        feedback.textContent='正在保存：'+file.name;
        const {task:fresh}=await uploadMaterial(id,file,version);
        tasks=tasks.map(task=>task.id===id?fresh:task);
        if(selected?.id===id&&!historical)selected=fresh;
      }
      render();feedback.textContent='材料已保存，尚未执行核验';
    }catch(error){feedback.textContent=error.message+'。已成功保存的材料保留。'}
    finally{importing=false;submit.disabled=false;form.setAttribute('aria-busy','false')}
  });
}
const taskActionLabels=Object.freeze({start:'继续发送',cancel:'取消运行','retry-save':'重试保存',
  'finish-draft':'用现有资料出稿',resume:'恢复原版本',update:'更新研究',backup:'任务备份'});
function recoveryAction(task){
  if(!apiMode||task.batch_settling===true||task.recovery_block?.reason)return '';
  if(task.status==='待开始')return 'start';
  if(!['失败','已取消'].includes(task.status))return '';
  if(task.delivery&&task.delivery.version===task.version)return '';
  if(window.RHResearchData.reportCheckpoint(task))return 'retry-save';
  return task.can_finish_from_data?'finish-draft':'resume';
}
function composerTaskOperation(task,hasQuestion){
  if(!apiMode||!task||isBackupArchive(task))return '';
  if(task.status==='运行中')return 'cancel';
  // Files in the composer belong to a new question, even before it has text.
  return hasQuestion||queuedMaterials.length?'':recoveryAction(task);
}
function taskActionButton(task,action,{label=taskActionLabels[action],variant='quiet'}={}){
  if(!action)return '';
  const busy=action!=='backup'&&pendingTaskActions.has(task.id)?' disabled aria-busy="true"':'';
  return '<button class="button-'+variant+'" type="button" data-action="'+action+'"'+busy+'>'+label+'</button>';
}
function canContinueResearch(task){
  return recoveryAction(task)==='finish-draft'&&runtimeLive();
}
function canUpdateResearch(task){
  return apiMode&&task.delivery?.version===task.version&&
    ['已完成','部分完成'].includes(task.status)&&task.batch_settling!==true;
}
function priorChatRounds(task){
  const thread=task.thread_id||task.id;
  const available=new Set(tasks.filter(row=>!row.archive_unreadable&&(row.thread_id||row.id)===thread).map(row=>row.id));
  const rounds=new Map();
  for(const message of task.messages||[]){
    if(message.role==='user'&&message.task_id!==task.id&&available.has(message.task_id)&&
      typeof message.content==='string'&&message.content.trim())rounds.set(message.task_id,message.content);
  }
  return [...rounds].reverse();
}
function openChatRounds(){
  const rows=priorChatRounds(selected).map(([id,question])=>'<li><button class="button-quiet" type="button" data-task="'+
    esc(id)+'"><span>'+esc(question)+'</span></button></li>');
  if(rows.length)modal('对话轮次','<ul class="chat-rounds">'+rows.join('')+'</ul>');
}
function chatTaskMenuItems(id){
  if(!workspaceReady||artifactsOpen||historical||selectingTaskId||!selected||selected.archive_unreadable)return [];
  const task=selected,row=tasks.find(item=>item.id===id);
  const items=[];
  if(row&&(row.thread_id||row.id)===(task.thread_id||task.id)&&priorChatRounds(task).length){
    items.push(['rounds','对话轮次','rounds']);
  }
  if(id!==task.id)return items;
  const edition=latestReport(task),busy=pendingTaskActions.has(id);
  if(apiMode&&edition?.version>1)items.push(['versions','报告版本','versions',reportVersionBusy(task)]);
  const recovery=recoveryAction(task);
  if(recovery&&!isBackupArchive(task))items.push([recovery,taskActionLabels[recovery],'update',busy]);
  if(canContinueResearch(task))items.push(['resume','继续研究','update',busy]);
  if(canUpdateResearch(task))items.push(['update','更新研究','update',busy]);
  if(!isBackupArchive(task))items.push(['backup','导出任务备份','download']);
  return items;
}
function contextTaskActions(task){
  const recovery=recoveryAction(task);
  // In Desk the panel provides task operations even when the composer is closed.
  const running=apiMode&&task.status==='运行中'&&task.batch_settling!==true&&!task.recovery_block?.reason;
  const action=running?'cancel':recovery==='start'&&!artifactsOpen?'':recovery;
  return taskActionButton(task,action)+
    (canContinueResearch(task)?taskActionButton(task,'resume',{label:'继续研究'}):'')+
    (canUpdateResearch(task)?taskActionButton(task,'update'):'')+
    (latestReport(task)?'':taskActionButton(task,'backup'));
}
function caseButtons(){return '<div class="case-list">'+(boot.cases||[]).map(item=>'<button data-case="'+esc(item.id)+'"><span>'+esc(item.title)+'</span><span>'+esc(item.entry)+' →</span></button>').join('')+'</div>'}
function scopeView(){
  const local=new Date(Date.now()+8*3600e3).toISOString().slice(0,16);
  const options=items=>(items||[]).map(item=>'<option>'+esc(item)+'</option>').join('');
  const mode=researchMode();
  const clock='<label for="research-mode">研究时点</label><select data-choice id="research-mode" name="research-mode"><option value="current"'+(mode==='current'?' selected':'')+'>当前研究</option><option value="historical"'+(mode==='historical'?' selected':'')+'>历史时点研究</option></select><div data-historical-cutoff'+(mode==='current'?' hidden':'')+'><label for="cutoff">历史截止时点，北京时间</label><input id="cutoff" name="cutoff" type="datetime-local" value="'+local+'"'+(mode==='current'?' disabled':'')+'></div>';
  return '<section class="section"><form id="new-form"><div class="form-row"><div><label for="entry">研究类型</label><select data-choice id="entry" name="entry">'+options(boot.entries)+'</select></div><div><label for="intent">研究目标</label><select data-choice id="intent" name="intent">'+options(boot.intents)+'</select></div></div><label for="objects">研究对象</label><input id="objects" name="objects" aria-describedby="objects-help" placeholder="名称或代码，多个对象用逗号分隔"><p id="objects-help" class="field-help">在线研究先核验代码映射；离线演示不查询真实标的。</p>'+clock+'<details class="scope-budget"><summary>运行预算</summary><div class="form-row"><div><label for="requests">数据请求上限</label><input id="requests" type="number" min="1" max="100000" value="100" required></div><div><label for="models">模型调用上限</label><input id="models" type="number" min="1" max="100000" value="8" required></div></div><label for="seconds">执行秒数上限</label><input id="seconds" type="number" min="1" value="600" required></details><p class="form-result" role="status" data-scope-feedback hidden></p><div class="form-actions"><button class="primary" type="submit">保存设置</button><button type="button" data-action="import">导入任务备份</button></div></form></section>';
}
function identityView(task){
  const mappings=task.identity_resolutions||[];
  if(!mappings.length)return '';
  const verified=mappings.filter(item=>item.status==='代码映射已核验').length;
  return '<details class="identity-results"><summary>标的映射'+(verified?'：'+verified+'项已核验':'：待核验')+'</summary>'+mappings.map(item=>
    '<div class="identity-result"><div><strong>'+esc(item.query||'原输入')+'</strong><p>'+esc(item.status==='代码映射已核验'?item.codes.join('、'):item.status)+'</p></div><button type="button" data-source="'+esc(item.artifact_id)+'" aria-label="查看'+esc(item.query||'标的')+'的映射来源">查看来源</button></div>'+
    (item.reason?'<p>'+esc(item.reason)+'</p>':'')).join('')+'<p>仅核验代码映射，不确认证券类型、业务暴露或历史可得性。</p></details>';
}
function cutoffLabel(value){
  if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value;
  const date=new Date(value);
  return Number.isNaN(date.getTime())?value:new Date(date.getTime()+8*3600e3).toISOString().slice(0,19).replace('T',' ');
}
function questionCoverageView(question,task){
  const coverage=question.evidence_coverage;
  if(!coverage||coverage.task_id!==task.id||coverage.research_version!==task.version||coverage.input_hash!==task.input_hash||coverage.question_id!==question.id||!coverage.items?.length)return '';
  const sourcesFor=item=>{
    const scope=question.evidence_need_scopes?.[item.text];
    const ids=scope==='query_provenance'?item.provenance_ids:
      scope==='market_calendar_coverage'&&task.market_coverage_basis_version===1?item.calendar_source_ids:[];
    return [...new Set(Array.isArray(ids)?ids:[])].map(id=>(task.source_records||[]).find(record=>
      record.id===id&&record.task_id===task.id&&record.research_version===task.version&&
      (scope!=='market_calendar_coverage'||record.source==='ifind'&&record.operation==='get_trade_dates'))).filter(Boolean);
  };
  const financialRefs=item=>question.evidence_need_scopes?.[item.text]?[]:item.refs||[];
  const items=coverage.items,covered=items.filter(item=>financialRefs(item).length||sourcesFor(item).length).length;
  return '<details class="question-coverage"><summary>依据覆盖'+covered+'/'+items.length+'</summary><ul>'+items.map(item=>
    '<li><div><span>'+esc(item.text)+'</span><span class="status">'+(financialRefs(item).length?'已引用':sourcesFor(item).length?'已核对':'缺项')+'</span></div>'+
    (financialRefs(item).length?'<div class="refs">'+financialRefs(item).map(ref=>evidenceButton(task,ref)).join('')+'</div>':'')+
    sourcesFor(item).map(record=>'<button type="button" class="text" data-source="'+esc(record.id)+
      '" aria-label="查看'+esc(item.text)+'的来源">'+(record.operation==='get_trade_dates'?'日历来源':'取数记录')+'</button>').join('')+'</li>').join('')+'</ul></details>';
}
function overview(task){
  return window.RHDeskViews.overview(task,{esc,cutoffLabel,edition:latestReport(task),readOnly:!!historical,
    canAddMaterials:apiMode&&!historical&&!sending&&!importing&&!accountBusy&&task.status!=='运行中'});
}
function relatedGapSources(task,questionId){
  const linked=new Set([...(task.evidence||[]),...(task.rejected_evidence||[])].filter(item=>
    (item.question_ids||[]).includes(questionId)).map(item=>item.artifact_id||item.artifact?.id).filter(Boolean));
  return [...(task.materials||[]),...(task.source_records||[])].filter((record,index,records)=>
    record.task_id===task.id&&record.research_version===task.version&&
    ((record.question_ids||[]).includes(questionId)||linked.has(record.id))&&
    records.findIndex(item=>item.id===record.id)===index);
}
function gapView(task){
  const questions=new Map((task.questions||[]).map(question=>[question.id,question]));
  const canLocate=canUseDesk();
  const relatedQuestions=message=>[...new Set((task.gap_records||[]).filter(record=>record.message===message)
    .flatMap(record=>Array.isArray(record.question_ids)?record.question_ids:[]))].filter(id=>questions.has(id));
  const messages=[...new Set([task.error,...window.RHResearchData.researchGaps(task)].filter(message=>typeof message==='string'&&message.trim()))];
  if(!messages.length)return '';
  const binding=' data-gap-task="'+esc(task.id)+'" data-gap-version="'+esc(task.version)+'"';
  const rows=messages.map(message=>{
    const ids=relatedQuestions(message);
    const related=ids.length?'<details class="work-gap-details"><summary>相关问题与来源</summary>'+ids.map(id=>{
      const question=esc(questions.get(id).question);
      const label=canLocate?'<button type="button" class="text work-gap-question" data-gap-question="'+esc(id)+'"'+binding+
        ' aria-label="定位问题：'+question+'">'+question+'</button>':'<span class="work-gap-question">'+question+'</span>';
      const sources=relatedGapSources(task,id).length?'<button type="button" data-gap-sources="'+esc(id)+'"'+binding+
        ' aria-label="查看'+question+'的相关来源">查看来源</button>':'';
      return '<div class="work-gap-related">'+label+sources+'</div>';
    }).join('')+'</details>':'';
    return '<li class="work-gap'+(message===task.error?' work-gap--error':'')+'"><p>'+esc(message)+'</p>'+related+'</li>';
  }).join('');
  return '<section class="section work-gaps" aria-label="未解决事项"><h2>待补充</h2><ul>'+rows+'</ul></section>';
}
function gapTaskMatches(button){
  return !selectingTaskId&&!historical&&selected&&button.dataset.gapTask===selected.id&&button.dataset.gapVersion===String(selected.version);
}
function locateGapQuestion(id){
  if(!canUseDesk()||!selected?.questions.some(question=>question.id===id))return;
  if($('#dialog').open)$('#dialog').close();
  view(0);
  const target=[...$('#content').querySelectorAll('[data-question-id]')].find(element=>element.dataset.questionId===id);
  if(target){target.focus({preventScroll:true});target.scrollIntoView({block:'center',behavior:'auto'})}
}
function openGapSources(id){
  const records=relatedGapSources(selected,id);
  if(!records.length)return notice('当前问题没有已关联的留存来源');
  modal('相关来源',records.map(record=>'<div class="source-record"><button type="button" class="text" data-source="'+esc(record.id)+'">'+
    esc(sourceRecordName(record))+'</button><span>'+esc(record.status||record.stages?.parse||'待核验')+'</span></div>').join(''));
}
function callHistory(task){
  const events=(Array.isArray(task.ledger?.dispatches)?task.ledger.dispatches:[]).filter(event=>['model_calls','data_requests'].includes(event?.kind));
  if(!events.length)return '';
  const phases={plan:'规划',acquire:'资料获取',read:'原件阅读',extract:'材料提取',bind_calculations:'计算输入核对',analyze:'研究分析',supplement:'定向补证'};
  const failures={response_incomplete:'响应正文不完整',connection_interrupted:'响应连接中断',http_protocol_failure:'响应协议不完整',transport_timeout:'连接或读取超时',response_deadline_exceeded:'响应超出时限',authentication_failed:'认证未通过',credential_expired:'授权过期',permission_denied:'调用权限未通过',insufficient_balance:'服务余额不足',rate_limited:'服务限流',content_truncated:'模型正文被截断',invalid_response_json:'响应不是有效JSON',invalid_response_shape:'响应结构不完整',empty_content:'模型未返回正文',unexpected_transport_failure:'响应处理失败',phase_fields_mismatch:'当前阶段字段不符',private_content:'输出包含私密字段',non_object_output:'输出结构不符',invalid_json:'输出不是规范JSON',ambiguous_json:'输出包含有歧义的字段或数字',output_size_or_type:'输出大小或类型不符'};
  Object.assign(failures,{worker_start_failed:'本地传输未启动',invalid_transport_message:'本地传输响应异常',provider_failure:'数据调用失败待核查',unsupported_operation:'接口尚未登记',response_too_large:'响应超过大小限制',duplicate_response_field:'响应JSON包含重复字段',connection_failed:'服务连接失败',http_failure:'服务请求失败',tls_failure:'安全连接未通过',original_download_failed:'公告原件未取得'});
  const sdkFailures={source_budget_exceeded:'新增请求预算不足',sdk_retry_limit:'同一请求已达到三次上限',wire_authorization_failed:'请求登记未完成',source_checkpoint_failed:'检查点保存失败',sdk_unknown_retry_blocked:'上次请求结果未知，未自动重发',sdk_auth_retry_blocked:'认证或权限失败，未自动重发',unregistered_sdk_request:'请求类型尚未登记'};
  Object.assign(failures,sdkFailures);
  const jsonFailures={extra_data:'输出多出正文或说明文字',unterminated_string:'输出文字未闭合',invalid_escape:'输出转义字符无效',invalid_control_character:'输出含无效控制字符',invalid_unicode_escape:'输出文字编码无效',expected_property_name:'输出字段名格式不符',expected_comma:'输出缺少分隔符',expected_colon:'输出字段缺少分隔符',expected_value:'输出缺少有效内容',unexpected_bom:'输出含不适用的编码标记',syntax_error:'输出语法不符'};
  const rows=events.slice(-100).map(event=>{
    const output=event.output_contract,selection=event.source_selection_review;
    // A returned response is not an accepted reading position or verified
    // evidence. Only this read call's explicit, known diagnostic may refine it.
    const reviewedSelection=event.kind==='model_calls'&&event.research_phase==='read'&&
      event.status==='成功'&&output?.phase==='read'&&output.shape_accepted===true&&
      selection&&!Array.isArray(selection)&&selection.version===1&&
      selection.financial_approval===false&&typeof selection.selection_accepted==='boolean';
    const selectionState=reviewedSelection?(selection.selection_accepted?'读取位置已确认':'读取选择未通过'):'';
    const selectionCause=reviewedSelection&&!selection.selection_accepted?
      (selection.code==='unknown_artifact'?'来源不在本次目录':selection.code==='duplicate_artifact'?'同一原件重复选择':'读取范围未通过'):'';
    const result=sdkFailures[event.failure_code]||selectionState||(event.status==='成功'?'响应完成':event.status==='失败'?'调用失败':event.status==='已派发'?'已派发':String(event.status||'状态未记录'));
    const contractFailure=output?.field_structure?.accepted===false?'回答或论点格式不符':output?.item_structure?.accepted===false?'回答或论点条目格式不符':output?.execution_binding?.accepted===false?'分析执行范围不符':'';
    const format=contractFailure||output?.shape_accepted===false?'输出格式未通过':output?.shape_accepted===true?(output.phase==='extract'?'JSON格式通过':'输出格式通过'):'';
    const proposal=output?.extraction_validation;
    const boundProposal=proposal?.scope==='proposal_constraints'&&proposal.phase==='extract'&&
      proposal.task_id===task.id&&proposal.research_version===task.version&&
      proposal.batch===event.batch&&proposal.input_hash===task.input_hash&&typeof proposal.accepted==='boolean';
    const proposalState=boundProposal?(proposal.accepted?'提案结构通过，证据仍须核验':'提取提案未采用'):'';
    const proposalCause=boundProposal&&!proposal.accepted?(proposal.failure_code==='calculation_context_shape'?'计算口径结构不符':'提取提案约束不符'):'';
    const cause=event.failure_code==='transport_cancelled'?(event.network_worker_reaped===true?'本地传输已停止':'已收到取消信号'):event.failure_code==='private_content'?'输出包含敏感内容，未采用':output?.failure_code==='invalid_json'&&jsonFailures[output.json_error]?jsonFailures[output.json_error]:(failures[event.failure_code]||failures[output?.failure_code]||'');
    const disposition=event.result_disposition?'取消后未采用':'';
    const duration=event.sdk_request_elapsed_seconds??event.elapsed_seconds;
    const elapsed=typeof duration==='number'&&Number.isFinite(duration)?duration.toFixed(1)+'秒':'未记录';
    const name=event.kind==='model_calls'?(phases[output?.phase||event.research_phase]||'模型调用'):event.sdk_request_role==='query'?'数据查询':event.sdk_request_role==='download'?'数据下载':'数据请求';
    const detail=[...new Set([selectionCause,format,proposalState,proposalCause,cause,contractFailure,disposition])].filter(value=>value&&value!==result).map(value=>'<div>'+esc(value)+'</div>').join('');
    return '<tr><td>'+name+'</td><td>'+esc(result)+detail+'</td><td>'+elapsed+'</td></tr>';
  }).join('');
  return '<details class="call-history"><summary>调用记录'+events.length+'次</summary>'+(events.length>100?'<p>显示最近100次，完整记录见任务备份。</p>':'')+'<div class="table-wrap" tabindex="0" role="region" aria-label="调用记录"><table><thead><tr><th scope="col">阶段</th><th scope="col">执行结果</th><th scope="col">耗时</th></tr></thead><tbody>'+rows+'</tbody></table></div></details>';
}
function ledgerView(task){
  const ledger=task.ledger||{},headings=['资源','已用','上限','研究可用','交付预留'];
  const amount=(value,key)=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&
    (key==='seconds'||Number.isInteger(value))?value:null;
  const rows=[['数据请求','data_requests'],['模型调用','model_calls'],['执行时间','seconds']].map(([title,key])=>{
    const used=amount(ledger.used?.[key],key),cap=amount(ledger.limits?.[key],key);
    const reserve=ledger.reserve&&typeof ledger.reserve==='object'&&!Array.isArray(ledger.reserve)?
      amount(Object.hasOwn(ledger.reserve,key)?ledger.reserve[key]:0,key):null;
    const available=used!==null&&cap!==null&&cap>0&&reserve!==null?Math.max(0,cap-used-reserve):null;
    const format=value=>value===null?'未记录':value.toFixed(key==='seconds'?1:0)+(key==='seconds'?'秒':'次');
    return '<tr>'+[title,format(used),format(cap&&cap>0?cap:null),format(available),format(reserve)]
      .map((value,index)=>'<td data-label="'+headings[index]+'">'+esc(value)+'</td>').join('')+'</tr>';
  }).join('');
  const rounds=Number.isInteger(ledger.supplements)&&ledger.supplements>=0?ledger.supplements+'/2轮':'未记录';
  return '<div class="table-wrap budget-ledger" tabindex="0" role="region" aria-label="运行预算"><table><thead><tr>'+
    headings.map(heading=>'<th scope="col">'+heading+'</th>').join('')+'</tr></thead><tbody>'+rows+
    '</tbody></table></div><p>研究可用额度已扣除交付预留；补证'+rounds+'。</p>'+callHistory(task);
}
function supplementView(task){
  const list=value=>Array.isArray(value)?value:[],rounds=list(task.supplement_history),stop=task.supplement_stop_reason;
  if(!rounds.length&&!stop)return '';
  const questions=new Map(list(task.questions).map(q=>[q.id,q.question]));
  const nodes=new Map(list(task.nodes).map(n=>[n.id,n.title]));
  const sources=new Map([...list(task.materials),...list(task.source_records)].map(source=>[source.id,source.name||source.operation||'留存原件']));
  const records=rounds.map(round=>{
    const proposal=round.proposal||{},result=round.results;
    const sourceNames={ifind:'iFinD',tinyshare:'Tinyshare',wind:'Wind'};
    const actions=[...list(proposal.data_requests).map(need=>'获取'+(sourceNames[need.source||'ifind']||'登记来源')+'数据'+
      (need.parameters?.codes||need.parameters?.ts_code?'，'+(need.parameters.codes||need.parameters.ts_code):'')),
      ...list(proposal.calculations).map(item=>'计算'+methodName(item.method)),
      ...list(proposal.artifact_ids).map(id=>'重读'+(sources.get(id)||'留存原件'))];
    const preserved=Object.keys(round.preserved_nodes||{}).map(id=>nodes.get(id)||'独立分支');
    const title=Number.isInteger(round.round)?'第'+round.round+'轮':'补证记录';
    return '<details class="supplement-round"><summary>'+esc(title)+'，'+esc(round.status||'待核对')+'</summary>'+
      '<p>原因：'+esc(proposal.reason||round.expected_change||'既有宿主检查点补证')+'</p>'+
      (proposal.expected_change?'<p>预期改变：'+esc(proposal.expected_change)+'</p>':'')+
      '<p>影响问题：'+esc(list(round.affected_questions||proposal.question_ids).map(id=>questions.get(id)||id).join('；')||'未登记')+'</p>'+
      (actions.length?'<ul>'+actions.map(action=>'<li>'+esc(action)+'</li>').join('')+'</ul>':'')+
      (result?'<p>本轮结果：'+(list(result.filing_candidates_added).length?'新增可下载原件候选'+list(result.filing_candidates_added).length+'项，':'')+'新增证据'+list(result.evidence_added).length+'项，新增计算'+list(result.calculations_added).length+'项，解决问题'+list(result.questions_answered).length+'项。</p>':'<p>本轮结果尚未保存。</p>')+
      (preserved.length?'<p>复用未变分支：'+esc(preserved.join('、'))+'</p>':'')+
      (round.stop_reason?'<p>停止原因：'+esc(round.stop_reason)+'</p>':'')+'</details>';
  }).join('');
  const lastStop=rounds[rounds.length-1]?.stop_reason;
  return '<section class="section"><details class="supplement-records"><summary>'+
    (rounds.length?'定向补证记录':'补证未执行')+'</summary>'+records+
    (stop&&stop!==lastStop?'<p>停止原因：'+esc(stop)+'</p>':'')+'</details></section>';
}
const recoveringOriginals=new Set();
function processDetails(task){
  const records=window.RHResearchData.list;
  const archiveRead=records(task.revisions).find(record=>record?.action==='本地归档取数'&&record.batch===task.batch);
  const archiveNote=archiveRead?'<p>本地归档取数，已复用'+records(archiveRead.artifacts).length+'份iFinD回包，共'+esc(archiveRead.matched_cells)+'个原值。</p>':'';
  const recovery=apiMode&&(task.retained_originals||[]).length?'<section class="section original-recovery"><h2>留存原件</h2>'+task.retained_originals.map(original=>'<div class="original-recovery-row"><div><strong>'+esc(original.name)+'</strong><p>'+esc(original.status)+'</p></div><div class="original-file-actions">'+
    originalDownloadButton(task,original)+'<button type="button" data-retry-original="'+esc(original.id)+'" data-original-task="'+esc(task.id)+'" '+(task.status==='运行中'||recoveringOriginals.has(original.id)?'disabled':'')+'>'+(recoveringOriginals.has(original.id)?'正在解析':'重新解析')+'</button></div></div>').join('')+'</section>':'';
  const questionById=new Map(task.questions.map(question=>[question.id,question.question]));
  const nodes=task.nodes.map((node,index)=>{
    const questions=node.questions.map(id=>questionById.get(id)||id).join('；');
    const needs=(node.evidence_needs||[]).length?'<p>取证需求：'+esc(node.evidence_needs.join('；'))+'</p>':'';
    const methods=(node.method_needs||[]).length?'<p>计算方法：'+esc(node.method_needs.map(methodName).join('、'))+'</p>':'';
    return '<article class="node" data-reading-key="node:'+esc(node.id)+'"><span class="node-num" aria-label="节点'+(index+1)+'">'+(index+1)+'</span><div><h3>'+esc(node.title)+'</h3>'+(node.id==='evidence'?archiveNote:'')+'<details><summary>依赖与完成条件</summary><p>执行能力：'+esc(node.operation)+'</p><p>对应问题：'+esc(questions)+'</p>'+needs+methods+'<p>前置依赖：'+esc(node.dependencies.join('、')||'无')+'</p><p>'+esc(node.completion)+'</p><p>'+esc(node.termination)+'</p>'+(node.error?'<p>'+esc(node.error)+'</p>':'')+'</details></div>'+statusTag(node.status)+'</article>';
  }).join('');
  return recovery+'<section class="section"><div class="section-heading"><h2>实际执行计划</h2>'+(task.nodes.length?'<span class="muted">'+nodeProgress(task)+'</span>':'')+'</div>'+(nodes||'<p>尚未形成执行计划。</p>')+'</section><section class="section"><details class="process-budget"><summary>运行预算与调用记录</summary>'+ledgerView(task)+'</details></section>'+supplementView(task)+((task.knowledge?.entries||[]).length?'<section class="section"><h2>相关知识与方法</h2>'+knowledgeView(task.knowledge)+'</section>':'');
}
function processView(task){
  return window.RHDeskViews.sources(task,{esc,sourceRecordName,sourceRecordButtons,unverifiedProposal,processDetails,
    hasOriginals:originalFiles(task).length>0});
}
function knowledgeSource(item){
  // Current entries use a primary reference plus additional references;
  // older locked versions retain an array. Reading must preserve both forms.
  const primary=item.source_reference;
  const references=[...(Array.isArray(primary)?primary:primary?[primary]:[]),
    ...(Array.isArray(item.additional_source_references)?item.additional_source_references:[])];
  const seen=new Set(),links=[];
  for(const reference of references){
    if(!reference||typeof reference!=='object'||Array.isArray(reference)||typeof reference.url!=='string')continue;
    const raw=reference.url.trim();
    if(/[\u0000-\u0020\u007f\\]/.test(raw))continue;
    let url;try{url=new URL(raw)}catch{continue}
    if(!['https:','http:'].includes(url.protocol)||!url.hostname||url.username||url.password)continue;
    const publisher=typeof reference.publisher==='string'&&reference.publisher.trim()?reference.publisher.trim():url.hostname;
    const sections=Array.isArray(reference.sections)?[...new Set(reference.sections.filter(value=>typeof value==='string'&&value.trim()))]:[];
    const key=JSON.stringify([url.href,publisher,sections]);if(seen.has(key))continue;seen.add(key);
    links.push('<li><a href="'+esc(url.href)+'" target="_blank" rel="noopener noreferrer">'+esc(publisher)+'</a>'+
      (sections.length?'<p class="muted">定位：'+esc(sections.join('；'))+'</p>':'')+'</li>');
  }
  return links.length?'<ul class="source-list">'+links.join('')+'</ul>':esc(item.source||'来源尚未登记');
}
function knowledgeView(knowledge,{showPurpose=true}={}){
  const entries=knowledge.entries||[],questions=knowledge.retrieval_context?.questions||[];
  const list=values=>values?.length?'<ul class="source-list">'+values.map(value=>'<li>'+esc(value)+'</li>').join('')+'</ul>':'';
  const items=entries.map(item=>{
    const selection=knowledge.selection_reasons?.[item.id];
    const related=selection?.question_ids?.map(id=>id==='query'?knowledge.query:questions.find(q=>q.id===id)?.question).filter(Boolean)||[];
    const reasons=selection?'<details class="knowledge-selection"><summary>检索依据</summary><p>'+esc(selection.reason)+'</p>'+list([...new Set(related)])+'</details>':'';
    return '<details class="knowledge-item" data-knowledge="'+esc(item.id)+'"><summary>'+esc(item.title)+'</summary><p class="muted">版本'+esc(item.version)+'，'+esc(item.type)+'</p><p>'+esc(item.content)+'</p><dl class="knowledge-notes"><dt>适用条件</dt><dd>'+esc(item.conditions)+'</dd>'+(item.channels?.length?'<dt>传导渠道</dt><dd>'+list(item.channels)+'</dd>':'')+'<dt>反例与相反渠道</dt><dd>'+esc(item.counterexample)+'</dd>'+(item.verification_indicators?.length?'<dt>需要核验</dt><dd>'+list(item.verification_indicators)+'</dd>':'')+(item.temporal_scope?'<dt>时点限制</dt><dd>'+esc(item.temporal_scope)+'</dd>':'')+'<dt>来源</dt><dd>'+knowledgeSource(item)+(item.publication_date_status?'<p class="muted">'+esc(item.publication_date_status)+'</p>':'')+'</dd></dl>'+reasons+'</details>';
  }).join('');
  const gaps=[...new Set([...(knowledge.unmatched||[]),...(knowledge.unmatched_questions||[]).filter(item=>!(knowledge.unmatched||[]).includes(item.reason+'：'+item.question)).map(item=>item.reason+'：'+item.question)])];
  const coverage=gaps.length?'<details class="knowledge-coverage"><summary>知识覆盖限制</summary>'+list(gaps)+'<p>可继续按本次材料取证，不能据此宣称领域知识完备。</p></details>':'';
  return (items||'<p>尚未采用知识。</p>')+coverage+(showPurpose?'<details class="knowledge-purpose"><summary>知识使用说明</summary><p>通用知识用于确定核验问题，不代替本次证据。</p></details>':'');
}
function libraryMarkdown(markdown){
  // Render the small set of Markdown constructs in the published brief.
  const inline=text=>text.split(/(\*\*[^*]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g).map(part=>{
    if(part.startsWith('**')&&part.endsWith('**'))return '<strong>'+esc(part.slice(2,-2))+'</strong>';
    const link=/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    return link?'<a href="'+esc(link[2])+'" target="_blank" rel="noopener noreferrer">'+esc(link[1])+'</a>':esc(part);
  }).join('');
  return markdown.split(/^```(?:markdown|md)?[ \t]*$/m).map((part,index)=>{
    if(index%2)return '<textarea class="research-library__template" readonly aria-label="Markdown报告模板">'+esc(part.trim())+'</textarea><button type="button" data-copy-template>复制模板</button>';
    return part.trim().split(/\n\s*\n/).filter(Boolean).map(block=>{
      if(/^\d+\. /.test(block))return '<ol>'+block.split('\n').map(line=>'<li>'+inline(line.replace(/^\d+\. /,''))+'</li>').join('')+'</ol>';
      return '<p>'+inline(block)+'</p>';
    }).join('');
  }).join('');
}
function openResearchLibrary(){
  const methods=boot.research_methods?.methods||[],entries=boot.knowledge||[];
  const tabs=[['methods','研究方法'],['entries','知识条目'],['templates','报告模板']];
  modal('知识与模板','<div class="research-library"><nav class="research-library__tabs" aria-label="知识与模板">'
    +tabs.map(([id,label],index)=>'<button type="button" data-library-pane="'+id+'" aria-pressed="'+String(index===0)+'">'+label+'</button>').join('')
    +'</nav><div class="research-library__body"></div></div>');
  const library=$('#dialog .research-library'),body=library.querySelector('.research-library__body');
  const guides=boot.research_guides||{};
  const guideQuestions=(guides.questions||[]).map(([question,markdown])=>
    '<details class="knowledge-item"><summary>'+esc(question)+'</summary>'
    +libraryMarkdown(markdown)+'</details>').join('');
  const methodGroups=()=>[...new Set(methods.map(row=>row.subject))].map(subject=>{
    const rows=methods.filter(row=>row.subject===subject);
    if(!rows.length)return '';
    return '<section class="research-library__group"><h3>'+esc(subject)+'</h3>'+rows.map(row=>{
      const sources=row.sources.map(([publisher,url])=>({publisher,url}));
      return '<details class="knowledge-item" data-library-item><summary>'+esc(row.title)+'</summary>'
        +'<p>'+esc(row.analysis)+'</p><dl class="knowledge-notes"><dt>需要核对</dt><dd>'
        +row.evidence.map(esc).join('、')+'</dd><dt>反例与限制</dt><dd>'+esc(row.check)+'</dd>'
        +(row.calculations.length?'<dt>已登记计算</dt><dd>'+row.calculations.map(method=>esc(methodName(method))).join('、')+'</dd>':'')
        +'<dt>资料来源</dt><dd>'+knowledgeSource({source_reference:sources})+'</dd></dl></details>';
    }).join('')+'</section>';
  }).join('')||'<p>此版本暂无研究方法目录。</p>';
  const methodsMarkup=()=>guideQuestions?'<section class="research-library__group"><h2>按研究问题查阅</h2>'+guideQuestions+'</section>'
    +'<details class="research-library__group"><summary>全部登记方法</summary>'+methodGroups()+'</details>':methodGroups();
  const entriesMarkup=()=>['概念','指标','机制','方法'].map(type=>{
    const rows=entries.filter(row=>row.type===type);
    return rows.length?'<section class="research-library__group"><h3>'+type+'</h3>'
      +knowledgeView({entries:rows},{showPurpose:false})+'</section>':'';
  }).join('')||'<p>暂无知识条目。</p>';
  const templatesMarkup=()=>'<section class="research-library__group">'
    +[['对客标准报告',guides.report,'核心结论、策略逻辑、跟踪标的及风险。'],
      ['内部详细报告',guides.workpaper,'按策略逻辑展开数据口径、计算、反例和来源。']]
    .map(([label,markdown,description],index)=>'<details class="knowledge-item research-library__guide"'+(index===0?' open':'')+'>'
      +'<summary>'+label+'</summary>'+(markdown?libraryMarkdown(markdown):'<p>'+description+'</p>')+'</details>').join('')
    +'<button type="button" data-action="visual-library">图表与配色方案</button></section>';
  const renderPane=id=>{
    library.querySelectorAll('[data-library-pane]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.libraryPane===id)));
    body.innerHTML=({methods:methodsMarkup,entries:entriesMarkup,templates:templatesMarkup}[id])();
  };
  library.querySelectorAll('[data-library-pane]').forEach(button=>button.addEventListener('click',()=>renderPane(button.dataset.libraryPane)));
  body.addEventListener('click',async event=>{
    const copy=event.target.closest('[data-copy-template]');
    if(!copy)return;
    const template=copy.previousElementSibling;
    try{
      await navigator.clipboard.writeText(template.value);
      notice('已复制报告模板');
    }catch{
      template.focus();
      template.select();
      notice('模板已选中，可使用系统复制命令');
    }
  });
  renderPane('methods');
}
function unverifiedProposal(task,item){
  if(typeof item.text==='string')return item;
  const matches=(task.host_result?.evidence_specs||[]).filter(spec=>spec?.id===item.id&&spec.artifact_id===item.artifact_id);
  if(matches.length!==1)return item;
  const spec=matches[0];
  return {...item,text:typeof spec.text==='string'?spec.text:'',record_locator:spec.record_locator};
}
function documentExcerptCards(items){
  return items.map(e=>{
    const text=String(e.text||'').replace(/\s+/g,' ').trim(),preview=text.length>280?text.slice(0,280)+'…':text;
    return '<article class="document-excerpt"><div class="document-excerpt__head"><strong>'+esc(e.object)+'</strong><span>公开时间未核验</span></div><p>'+esc(preview)+'</p><button class="text" data-evidence="'+esc(e.id)+'">查看依据</button></article>';
  }).join('');
}
function evidenceView(task){
  const approved=task.evidence||[],rejected=task.rejected_evidence||[];
  const documentOnly=task.intent==='原件内容核对';
  const verified=approved.length?(documentOnly?'<h3>原件摘录</h3>'+documentExcerptCards(approved):'<div class="table-wrap" tabindex="0"><table><thead><tr><th>对象与记录</th><th>关键事实</th><th>公开时间</th><th>定位</th></tr></thead><tbody>'+approved.map(e=>'<tr><td>'+esc(e.object)+'</td><td>'+esc(e.text)+'</td><td>'+esc(e.published_at?e.published_at.slice(0,10):'未核验')+'</td><td><button class="text" data-evidence="'+esc(e.id)+'">查看依据</button></td></tr>').join('')+'</tbody></table></div>'):'<p class="muted">尚无通过两类核验的证据。</p>';
  const originals=new Set([...(task.materials||[]),...(task.source_records||[])].map(record=>record.id));
  const pending=rejected.length?'<details class="unverified-excerpts"><summary>待核验摘录'+rejected.length+'条</summary><p>以下为未采用的模型摘录，不是研究结论。</p>'+rejected.map(original=>{
    const item=unverifiedProposal(task,original);
    const text=String(item.text||'未保留摘录正文'),preview=text.length>1200?text.slice(0,1200)+'…':text;
    const reason=item.rejection==='证据published_at缺失'?'首次公开时间缺失':item.rejection||'核验尚未通过';
    return '<div class="unverified-excerpt"><p>'+esc(preview)+'</p><p>未采用：'+esc(reason)+'</p>'+
      (item.artifact_id&&originals.has(item.artifact_id)?'<button type="button" class="text" data-source="'+esc(item.artifact_id)+'" data-source-evidence="'+esc(item.id)+'" aria-label="定位待核验摘录原文：'+esc(item.object||'留存材料')+'">定位原文</button>':'')+'</div>';
  }).join('')+'</details>':'';
  return verified+pending;
}
function evidenceButton(task,ref){
  const items=task.evidence||[],item=items.find(e=>e.id===ref),text=item?.text||'依据'+ref;
  const summary=text.length>24?text.slice(0,24)+'…':text;
  const documentOnly=task.intent==='原件内容核对'&&item;
  const number=documentOnly?items.indexOf(item)+1:'';
  const raw=item?.raw_value??item?.value,unit=item?.raw_unit||item?.unit||'';
  const detail=documentOnly&&raw!=null?String(raw)+unit:summary;
  const label=documentOnly?'证据'+number+'：'+detail:summary;
  return '<button class="evidence-link" data-evidence="'+esc(ref)+'" title="'+esc(text)+'" aria-label="查看证据'+number+'：'+esc(text)+'">'+esc(label)+'</button>';
}
function methodName(method){return {fee_difference_v2:'同口径单项年度费率差',etf_daily_nav_premium_v1:'收盘价与同估值日净值偏离率',fee_difference_v1:'同口径费率差',management_custody_fee_comparison_v1:'年度管理托管费率比较',management_custody_fee_summary_v1:'年度管理托管费率合计与差值',residual_present_value_v1:'剩余现金流现值',observed_price_return_v1:'已观测区间价格变化',observed_index_change_v1:'已观测指数区间变化',etf_nav_premium_v1:'同估值时点ETF净值折溢价',gross_margin_v1:'同口径营业毛利率',net_profit_margin_v1:'同口径净利润率',operating_cash_profit_ratio_v1:'经营现金流净利润比'}[method]||method}
function calculationName(item){
  if(item.method==='fee_difference_v2'){
    const kind=item.parameters?.fee_kind;
    if(kind==='management_fee')return '管理费年费率差';
    if(kind==='custody_fee')return '托管费年费率差';
  }
  return item.method_title||methodName(item.method);
}
function calculationButton(item,{showValue=false}={}){
  let value='';
  if(showValue){
    const output=item.output.value;
    const display=typeof output==='string'?window.RHResearchData.number(output):Number(output).toFixed(4);
    value='：'+display+item.output.unit;
  }
  return '<button type="button" class="button-secondary research-method" data-calculation="'+esc(item.id)+'">'+esc(calculationName(item)+value)+'</button>';
}
function visibleAnswers(task){return (task.questions||[]).filter(q=>q.status==='已回答'&&q.answer&&(!(task.theses||[]).length||['fact','comparison'].includes(q.kind)))}
function displayAnswer(task,question){
  if(task.intent!=='原件内容核对'||!question.refs?.length)return question.answer;
  const items=question.refs.map(ref=>(task.evidence||[]).find(item=>item.id===ref));
  if(items.some(item=>!item||item.usage_scope!=='document_content'||item.source_check!=='通过'||item.applicability_check!=='通过'||
    !item.artifact?.id||item.artifact.task_id!==task.id||item.artifact.research_version!==task.version||
    !item.snapshot_hash||!item.source||!item.locator||typeof item.text!=='string'))return question.answer;
  const original='原件载明：\n'+items.map(item=>item.text).join('\n\n');
  if(question.answer!==original)return question.answer;
  const seen=new Set(),quotes=[];
  for(const item of items){
    const key=JSON.stringify([item.artifact.id,item.snapshot_hash,item.content_version,item.source,item.locator,item.text]);
    if(!seen.has(key)){seen.add(key);quotes.push(item.text)}
  }
  return '原件载明：\n'+quotes.join('\n\n');
}
function answerCards(task){
  return visibleAnswers(task).map(question=>{
    if(task.delivery?.version===task.version&&question.answer_scope==='general_definition'){
      // The report contains the full conditions, limitations and sources.
      const entries=(question.knowledge_refs||[]).map(ref=>
        (task.knowledge?.entries||[]).find(entry=>entry.id===ref.id&&entry.version===ref.version));
      if(entries.length&&entries.every(entry=>entry?.content&&question.answer.includes(entry.content))){
        return entries.map(entry=>window.RHMessageProse.renderHTML(entry.content)).join('');
      }
    }
    return '<article class="chat-thesis research-answer"><h3>'+esc(question.question)+'</h3><p>'+esc(displayAnswer(task,question))+'</p></article>';
  }).join('');
}
function partialDocumentContent(task){
  if(task.intent!=='原件内容核对')return '';
  const unresolved=new Set((task.questions||[]).filter(q=>q.status!=='已回答').map(q=>q.id));
  const items=(task.evidence||[]).filter(e=>e.usage_scope==='document_content'&&e.source_check==='通过'&&e.applicability_check==='通过'&&(e.question_ids||[]).some(id=>unresolved.has(id)));
  return items.length?'<section class="partial-document-content"><h2>已核对内容</h2><p>仍有未答事项，未形成完整回答。</p>'+documentExcerptCards(items)+'</section>':'';
}
function collectionWorkingpaper(task){
  const D=window.RHResearchData;
  const originals=new Map(D.records(task).map(record=>[record.id,record]));
  const groups=new Map();
  for(const item of D.scope(task.rejected_evidence,task)){
    const reason=String(item.rejection||'核验尚未通过');
    const questions=Array.isArray(item.question_ids)?item.question_ids.map(String).sort():[];
    const key=JSON.stringify([item.artifact_id||'',item.object||'',reason,questions]);
    if(!groups.has(key))groups.set(key,{item,reason,count:0});
    groups.get(key).count++;
  }
  if(!originals.size&&!groups.size)return '';
  const reasons={'证券身份映射valid_from缺失':'缺少代码映射有效期起点',
    '证券身份映射valid_to缺失':'缺少代码映射有效期终点',
    '证券身份映射basis缺失':'缺少代码映射依据','证据published_at缺失':'首次公开时间缺失'};
  const rows=[...groups.values()].map(({item,reason,count})=>{
    const record=originals.get(item.artifact_id);
    const source=record?'<button type="button" class="text" data-source="'+esc(record.id)+
      '" data-source-evidence="'+esc(item.id||'')+'"'+D.binding(task,esc)+' aria-label="查看'+esc(item.object||'')+'未采用摘录的原件">'+
      (record.kind==='api'?'打开原始数据':'打开原件')+'</button>':'原件未留存';
    return '<tr><td data-label="未采用原因">'+esc(reasons[reason]||reason)+'</td><td data-label="对象">'+esc(item.object||'未注明对象')+
      '</td><td data-label="数量">'+count+'条</td><td data-label="原件">'+source+'</td></tr>';
  }).join('');
  const sources=sourceRecordButtons(task);
  return '<section class="workingpaper-records" aria-label="任务取证底稿">'+
    (sources?'<h3>留存资料</h3>'+sources:'')+
    (rows?'<h3>未采用摘录</h3><p>未通过核验，不能作为结论依据。</p>'+
      '<div class="table-wrap workingpaper-rejections" tabindex="0" aria-label="未采用摘录的核验问题">'+
      '<table><thead><tr><th>未采用原因</th><th>对象</th><th>数量</th><th>原件</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table></div>':'')+'</section>';
}
function chatLimitations(values,label){
  const items=[...new Set((Array.isArray(values)?values:[])
    .filter(value=>typeof value==='string'&&value.trim()).map(value=>value.trim()))];
  if(!items.length)return '';
  if(items.length===1)return '<p class="muted"><strong>'+label+'</strong>，'+esc(items[0])+'</p>';
  return '<div class="muted"><p><strong>'+label+'</strong></p><ul>'+items.map(value=>'<li>'+esc(value)+'</li>').join('')+'</ul></div>';
}
function draftReportCards(task,{summaryOnly=false}={}){
  const D=window.RHResearchData,paper=D.paper(task);
  if(!paper)return '';
  if(summaryOnly&&typeof paper.conclusion==='string'&&paper.conclusion.trim()){
    const conclusion=paper.conclusion;
    const gaps=Array.isArray(paper.readout_gaps)?paper.readout_gaps.filter(gap=>typeof gap==='string'&&!conclusion.includes(gap.trim())):[];
    let lead='<p>'+esc(conclusion)+'</p>';
    if(conclusion==='本次核对以留存材料的原文为依据。'){
      const quotes=new Set();
      for(const logic of paper.logics||[]){
        const {sources}=D.draftEvidence(task,logic);
        for(const row of D.draftExcerpts(logic,sources))quotes.add(row.quote);
      }
      const excerpts=[...quotes];
      if(excerpts.length&&excerpts.join('').length<=220){
        lead=excerpts.map(quote=>'<p><q>'+esc(quote)+'</q></p>').join('');
      }
    }
    return lead+chatLimitations(gaps,'待核对');
  }
  const conditions=new Map(D.reportConditions(paper).map(row=>[row.logic_index,row]));
  return '<article class="chat-thesis research-answer"><h3>核心结论</h3><p>'+esc(paper.conclusion)+'</p></article>'+
    (paper.logics||[]).map((item,index)=>{
      const {sources}=D.draftEvidence(task,item);
      const heading=esc(item.display_heading||('策略逻辑'+(index+1)+'：'+(item.title||'')));
      const saved=conditions.get(index+1),premise=saved?.condition||'',tracking=saved?.tracking?.condition||'';
      const analyzed='\n'+String(item.analysis||'').trim()+'\n';
      const alreadyShown=value=>analyzed.includes('\n'+value+'\n');
      const excerpts=D.draftExcerpts(item,sources).map(row=>'<p><q>'+esc(row.quote)+'</q></p>').join('');
      return '<article class="chat-thesis"><h3>'+heading+'</h3>'+(item.analysis?'<p>'+esc(item.analysis)+'</p>':'')+excerpts+
        (item.implication?'<p>'+esc(item.implication)+'</p>':'')+
        (premise&&!alreadyShown(premise)?'<p>成立条件：'+esc(premise)+'</p>':'')+
        (tracking&&tracking!==premise&&!alreadyShown(tracking)?'<p>跟踪条件：'+esc(tracking)+'</p>':'')+
        (item.risk?'<p class="muted">风险：'+esc(item.risk)+'</p>':'')+'</article>';
    }).join('');
}
function draftFactsTable(task){
  const rows=task.draft_report?.facts||[];
  if(!rows.length)return '';
  const D=window.RHResearchData;
  return '<div class="table-wrap" tabindex="0" role="region" aria-label="本任务原始取数"><table class="draft-facts-table"><thead><tr>'+
    ['标的','指标','期间','原值','单位'].map(label=>'<th scope="col">'+label+'</th>').join('')+'</tr></thead><tbody>'+
    D.observations(rows).map(item=>'<tr>'+['code','label','period','value','unit'].map(key=>'<td>'+
      (key==='value'?D.cell(item,task,{esc,rawValues:true}):esc(item.fact[key]))+'</td>').join('')+'</tr>').join('')+
    '</tbody></table></div>';
}
function openDraftBasis(index){
  const task=selected,D=window.RHResearchData,item=task&&D.paper(task)?.logics?.[Number(index)];if(!item)return;
  const {facts,calculations,sources,missing}=D.draftEvidence(task,item);
  if(!facts.length&&!calculations.length&&!sources.length&&!missing.length)return;
  const excerpts=D.draftExcerpts(item,sources),cited=new Set(excerpts.map(row=>row.record.id));
  const ids=new Set(sources.filter(row=>!cited.has(row.id)).map(row=>row.id));
  const originals={...task,source_records:D.scope(task.source_records,task).filter(row=>ids.has(row.id)),materials:D.scope(task.materials,task).filter(row=>ids.has(row.id))};
  modal(item.title,draftArithmetic(calculations)+draftFactsTable({...task,draft_report:{facts}})+
    excerpts.map(row=>D.draftExcerptHTML(row,task,{esc,sourceRecordName})).join('')+sourceRecordButtons(originals)+
    (missing.length?'<p>'+esc('部分引用缺少留存记录。')+'</p>':'')+
    (item.reference_notes||[]).map(note=>'<p>'+esc(note)+'</p>').join(''));
}
function draftArithmetic(rows){
  if(!rows?.length)return '';
  return (rows.some(row=>row.conditional_equal_scale)?'<p>以下金额比率按输入等尺度计算，来源单位仍待明确。</p>':'')+
    rows.map(row=>'<section><h3>'+esc(row.label)+'</h3>'+
      window.RHMessageProse.renderHTML('\\('+row.formula+'\\)')+'<p>'+esc(row.display)+esc(row.unit)+'</p></section>').join('');
}
function conclusion(task){
  return window.RHDeskViews.workingpaper(task,{esc,statusTag,knowledgeView,displayAnswer,sourceRecordName,calculationName,
    calculationButton,sourceRecordButtons,draftFactsTable,draftArithmetic,collectionWorkingpaper,gapView});
}
function sameReport(first,second){
  return !!first&&!!second&&first.task_id===second.task_id&&first.version===second.version&&first.digest===second.digest;
}
function latestReport(task){
  if(!task)return null;
  const delivery=task.delivery;
  if(Number.isSafeInteger(delivery?.version)&&delivery.version>=1&&delivery.version<=task.version)
    return {...delivery,task_id:task.id,corrections:task.report_corrections||[]};
  const previous=task.previous_delivery;
  if(apiMode&&validSavedVersion(task,previous)&&previous.version<task.version)return previous;
  return null;
}
function shownReport(task,latest=latestReport(task)){
  const choice=apiMode&&task?reportChoices.get(task.id):null;
  return choice&&choice.version<=task.version&&choice.version!==latest?.version?choice:latest;
}
function validReportBookmark(value){
  return draftObject(value)&&Number.isSafeInteger(value.version)&&value.version>0&&
    typeof value.digest==='string'&&/^[a-f0-9]{64}$/.test(value.digest);
}
function rememberReportEdition(task,edition){
  if(!apiMode)return;
  const chosen=edition&&edition.version!==latestReport(task)?.version?edition:null;
  if(chosen)reportChoices.set(task.id,chosen);
  else reportChoices.delete(task.id);
  rememberDraftEntry('reportEditions',task.id,chosen?{version:chosen.version,digest:chosen.digest}:null);
}
async function restoreRememberedReport(task,current){
  const bookmark=draft.reportEditions?.[task.id];
  if(!apiMode||!canUseDesk()||isBackupArchive(task)||!validReportBookmark(bookmark)||reportChoices.has(task.id))return;
  if(bookmark.version>task.version||sameReport(latestReport(task),{...bookmark,task_id:task.id})){
    rememberReportEdition(task,null);return;
  }
  try{
    const history=await api('/api/tasks/'+encodeURIComponent(task.id)+'/report-history',undefined,{timeout:4000});
    if(!current()||draft.reportEditions?.[task.id]!==bookmark)return;
    const [edition]=savedVersions(task,history,bookmark.version);
    if(edition?.digest===bookmark.digest){rememberReportEdition(task,edition);return}
    rememberReportEdition(task,null);
    notice('上次阅读的报告版本已无法定位，请在交付版本中选择。');
  }catch{
    if(current())notice('上次阅读的报告版本暂未恢复，可在交付版本中重新打开。');
  }
}
function reportProfile(task,version=shownReport(task)?.version??task.version){
  const value=draft.reportProfiles?.[task.id+':'+version];
  return value==='detailed'&&(apiMode||task.report_detailed_html)?'detailed':'standard';
}
function savedReportURL(id,version){return '/api/tasks/'+encodeURIComponent(id)+'/report/'+version}
function historicalReportURL(key){return (apiMode?'/reports/':'reports/')+encodeURIComponent(key)+'.html'}
function reportLink(url,className,content,label='',title=''){
  return '<a class="'+className+'" href="'+esc(url)+'" target="_blank" rel="noopener"'+
    (label?' aria-label="'+esc(label)+'"':'')+(title?' title="'+esc(title)+'"':'')+'>'+content+'</a>';
}
function reportURL(task,version=shownReport(task)?.version??task.version){
  if(!apiMode)return null;
  const url=savedReportURL(task.id,version);
  return canUseDesk()?url+'?profile='+reportProfile(task,version):url;
}
function currentPrintFrame(){
  const frame=$('#report-frame');
  return !leaving&&!selectingTaskId&&artifactsOpen&&stage===3&&reportReader.isReady(frame)?frame:null;
}
window.ResearchHelperNative={
  flushWorkspaceDraft,
  prepareServiceRecovery,
  reportURL(){
    const frame=currentPrintFrame();
    return apiMode&&frame?.hasAttribute('src')?frame.src:null;
  },
  printRequest(){
    const frame=currentPrintFrame();
    return apiMode&&frame?.hasAttribute('src')
      ?{url:frame.src,digest:frame.getAttribute('data-report-digest'),presentation:reportVisuals()?.profile?.()??null}:null;
  }
};
function canCorrectChatReport(task,edition){
  return apiMode&&canUseDesk()&&task?.parent!=='legacy-import'&&/^[a-f0-9]{64}$/.test(edition?.digest||'');
}
function openChatReportCorrections(button){
  const card=button.closest('.message-artifact'),edition=latestReport(selected);
  if(!canCorrectChatReport(selected,edition)||selected?.id!==card.dataset.chatReportTask||
    String(edition.version)!==card.dataset.chatReportVersion||edition.digest!==card.dataset.chatReportDigest)return;
  openReportCorrections(latestReport,button);
}
function openReportCorrections(selectedEdition=shownReport,origin=null){
  const edition=selectedEdition(selected);if(historical||!edition)return;
  const selection=taskSelectionGeneration;
  const corrections=(Array.isArray(edition.corrections)?edition.corrections:[]).filter(item=>validReportCorrection(edition,item));
  const notes=corrections.length?'<ol class="report-correction-list">'+corrections.map(item=>'<li><p>'+esc(item.reason)+'</p></li>').join('')+'</ol>':'';
  const form=apiMode?'<form id="report-correction-form"><label for="report-correction-reason">更正说明</label><textarea id="report-correction-reason" maxlength="4000" rows="4" required autofocus></textarea><p>更正随版本保留，原报告不覆盖。</p><p id="report-correction-feedback" role="status" aria-live="polite"></p><div class="form-actions"><button class="button-primary" type="submit">记录更正</button><button class="button-secondary" type="button" data-action="close">取消</button></div></form>':'';
  modal('报告更正，版本'+edition.version,notes+form);
  const element=$('#report-correction-form');if(!element)return;
  const current=()=>!leaving&&!historical&&selection===taskSelectionGeneration&&sameReport(edition,selectedEdition(selected));
  element.addEventListener('submit',async event=>{
    event.preventDefault();if(element.dataset.saving==='true')return;
    const feedback=element.querySelector('#report-correction-feedback'),submit=element.querySelector('button[type=submit]');
    if(!current()){feedback.textContent='任务已切换，请重新打开所需报告。';return}
    element.dataset.saving='true';submit.disabled=true;
    try{
      const value=await api('/api/tasks/'+encodeURIComponent(edition.task_id)+'/report-correction',{
        version:edition.version,expected_digest:edition.digest,reason:element.querySelector('textarea').value
      });
      if(value.task_id!==edition.task_id||value.version!==edition.version||!Array.isArray(value.corrections)
         ||value.corrections.some(note=>!validReportCorrection(edition,note)))throw Error('更正响应与报告版本不一致，请重新读取');
      for(const item of new Set([...tasks,selected])){
        if(item?.id!==edition.task_id)continue;
        if(sameReport({...item.delivery,task_id:item.id},edition))item.report_corrections=value.corrections;
        if(sameReport(item.previous_delivery,edition))item.previous_delivery={...item.previous_delivery,corrections:value.corrections};
      }
      if(sameReport(reportChoices.get(edition.task_id),edition))
        reportChoices.set(edition.task_id,{...edition,corrections:value.corrections});
      remember();
      if(current()&&element.isConnected&&$('#dialog').open){
        const restore=$('#dialog').contains(document.activeElement);
        $('#dialog').close();render();
        if(restore)focusWorkspaceControl(origin||$('#content [aria-label="更多报告操作"]'),$('#chat-input'));
        notice('更正已记录');
      }
    }catch(error){if(element.isConnected)feedback.textContent=error.message}
    finally{element.dataset.saving='false';submit.disabled=false}
  });
}
function reportView(task){
  const latest=latestReport(task);
  const packagedReplacement=!apiMode&&task.synthetic===true&&task.demo_report_ref&&
    (boot.tasks||[]).find(item=>item.synthetic===true&&item.fixture===task.demo_report_ref&&item.id!==task.id&&item.report_html);
  return window.RHDeskViews.report(task,{esc,apiMode,reportProfile,reportURL,reportVersionBusy,
    edition:shownReport(task,latest),latest,
    taskActions:(reportTask,options)=>taskActionButton(reportTask,recoveryAction(reportTask),options),packagedReplacement});
}




function tracking(task){
  return window.RHDeskViews.tracking(task,{esc,statusTag,evidenceButton,calculationButton,calculationName,cutoffLabel});
}
function runtimeLive(){return apiMode&&boot.execution?.mode==='live'}
function showModelRequired(taskSaved){
  const action=!boot.authentication||boot.role==='admin'?'请在设置中配置模型。':'请联系admin配置模型。';
  notice('这个问题需要研究模型。'+action+(taskSaved?'任务已保留。':'问题和附件已保留。'));
}
function executionExpectation(){return boot.execution?{expected_execution:{mode:boot.execution.mode,policy_version:boot.execution.policy_version}}:{}}
async function verifyExecutionBeforeSend(){
  if(!apiMode)return true;
  const previous=boot.execution||{mode:'offline',policy_version:1};
  const fresh=await api('/api/bootstrap');
  if(boot.authentication&&(fresh.account!==boot.account||fresh.role!==boot.role)){leaveWorkspace();return false}
  const current=fresh.execution||{mode:'offline',policy_version:1};
  boot=fresh;token=boot.token||token;mergePresentations(boot.task_presentations);
  if(previous.mode!==current.mode||previous.policy_version!==current.policy_version){
    captureDraft();
    notice('运行模式已变更为'+(runtimeLive()?'在线研究，必要时会使用已配置的模型服务':'离线研究')+'。草稿和附件已保留，请确认后重新发送。');
    return false;
  }
  return true;
}
let settingsRefreshInFlight=null;
async function refreshWorkspaceSettings(){
  if(!apiMode||leaving||!workspaceReady)return;
  if(settingsRefreshInFlight)return settingsRefreshInFlight;
  settingsRefreshInFlight=(async()=>{
    try{
      const fresh=await api('/api/bootstrap');
      if(leaving)return;
      if(boot.authentication&&(fresh.account!==boot.account||fresh.role!==boot.role)){leaveWorkspace();return}
      boot=fresh;token=boot.token||token;
      const known=new Set(tasks.map(task=>task.id));
      for(const task of fresh.tasks||[])if(!known.has(task.id)){tasks.push(task);known.add(task.id)}
      mergePresentations(fresh.task_presentations);
      renderTaskList();
    }catch(error){if(!leaving)notice('配置核对未完成：'+error.message)}
  })();
  try{await settingsRefreshInFlight}finally{settingsRefreshInFlight=null}
}
function openRuntimeSettings(){
  if(!workspaceReady||leaving||$('#dialog').open)return;
  workspaceModeMotion.stop();
  window.RHWorkspaceSettings.open(null,{
    account:boot.account,onUpdated:refreshWorkspaceSettings,onInvalidated:leaveWorkspace,
    onAction(action){
      try{
        if(action==='import')$('#backup-file').click();
        else if(action==='cases')modal('合成验收案例','<p>固定离线材料，仅验证研究工作流。</p>'+caseButtons());
        else if(action==='knowledge')openResearchLibrary();
      }catch(error){if(!leaving)notice(error.message)}
    },
  });
}
async function logout(){
  if(accountBusy||sending||importing||leaving)return;
  accountBusy=true;captureDraft();
  try{
    await flushWorkspaceDraft();
    if(leaving)return;
    await api('/api/auth/logout',{});
    try{localStorage.setItem(authEventKey,JSON.stringify({account:null,nonce:Date.now()}))}catch{}
    leaveWorkspace();
  }finally{accountBusy=false}
}
function historyView(){const key=historical.key,tag='<p class="history-label">历史原报告，公开研究内容保留。机构与内部信息已脱敏，未纳入衍生品产品板块。</p>';if(stage===0)return tag+'<section class="section"><h2>'+esc(historical.title)+'</h2><p class="lead">'+esc(historical.conclusion)+'</p><p>'+esc(historical.scope)+'</p><p>'+esc(historical.date)+'</p><button data-stage="3">阅读全文</button></section>';if(stage===2)return tag+(historical.logics||[]).map(logic=>'<article class="thesis"><h2>'+esc(logic.title)+'</h2>'+logic.paragraphs.map(p=>'<p>'+esc(p)+'</p>').join('')+'<p class="muted">'+esc(logic.evidence)+'</p></article>').join('')+'<p>'+esc(historical.risk)+'</p>';if(stage===1||stage===4)return tag+'<div class="empty"><h2>'+(stage===1?'历史执行底稿未保留':'原验证条件未完整保留')+'</h2><p>该报告只作为完整阅读案例，不伪造新引擎的检查点或追踪结果。</p><button data-stage="3">阅读原报告</button></div>';return tag+'<div class="report-actions"><button data-action="export-history">保存HTML</button><button data-action="print">打印报告</button></div><iframe id="report-frame" title="完整历史研究报告" class="report-frame" sandbox="allow-scripts allow-modals allow-same-origin" src="'+(apiMode?'/reports/':'reports/')+esc(key)+'.html"></iframe>'}
function reportVersionCurrent(read){
  return !!read&&!leaving&&!historical&&!selectingTaskId&&read.selection===taskSelectionGeneration&&
    read.id===selected?.id&&read.version===selected.version&&read.stage===stage&&
    read.desk===artifactsOpen&&read.modal===modalGeneration&&reportVersionRead===read&&(!read.fromContext||contextOpen);
}
function reportVersionBusy(task){return task?.id===reportVersionRead?.id&&reportVersionCurrent(reportVersionRead)}
function syncReportVersionControls(){
  const pending=reportVersionBusy(selected);
  document.querySelectorAll('[data-action="versions"]').forEach(button=>{
    button.disabled=pending;if(pending)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
  });
}
function validReportCorrection(edition,note){
  return !!note&&note.task_id===edition.task_id&&note.version===edition.version
    &&typeof note.reason==='string'&&!!note.reason.trim();
}
function validSavedVersion(task,edition){
  return !!edition&&edition.task_id===task.id&&Number.isSafeInteger(edition.version)&&edition.version>=1&&edition.version<=task.version
    &&typeof edition.digest==='string'&&/^[a-f0-9]{64}$/.test(edition.digest)&&Array.isArray(edition.corrections)
    &&edition.corrections.every(note=>validReportCorrection(edition,note));
}
function savedVersions(task,history,version=null){
  const reports=history?.reports;
  if(history?.task_id!==task.id||!Array.isArray(reports))throw Error('交付版本响应无效，请重新读取');
  const versions=version===null?reports:reports.filter(item=>item?.version===version);
  if(versions.some(item=>!validSavedVersion(task,item))||
      new Set(versions.map(item=>item.version)).size!==versions.length)throw Error('交付版本响应无效，请重新读取');
  return versions;
}
async function readReportEdition(task,version){
  const latest=latestReport(task);
  if(latest?.version===version&&validSavedVersion(task,latest))return latest;
  const history=await api('/api/tasks/'+encodeURIComponent(task.id)+'/report-history');
  const [edition]=savedVersions(task,history,version);
  if(!edition)throw Error('报告版本'+version+'无法读取，请重新打开报告库');
  return edition;
}
function savedVersionLinks(task,versions,inline=false){
  return versions.length?versions.map(item=>'<section class="report-version-record"><div class="report-version-heading">'+
    (typeof item.title==='string'&&item.title.trim()?'<strong class="report-version-title">'+esc(item.title)+'</strong>':'')+
    '<a class="report-version-link" href="'+
    esc(savedReportURL(task.id,item.version))+'" target="_blank" rel="noopener"'+
    (inline?' data-report-version="'+item.version+'"':'')+'>阅读版本'+item.version+'</a></div>'+
    (item.corrections.length?'<h3>报告更正</h3><ol class="report-correction-list">'+item.corrections.map(note=>'<li><p>'+esc(note.reason)+'</p></li>').join('')+'</ol>':'')+'</section>').join(''):
    '<p>尚无已保存的交付版本。</p>';
}
async function openUnavailableArchive(task,version=null){
  cancelTaskSelection();
  modal('任务档案无法读取','<p>正在读取已保存报告。</p>');
  const generation=modalGeneration;
  const current=()=>!leaving&&$('#dialog').open&&modalGeneration===generation;
  try{
    const history=await api('/api/tasks/'+encodeURIComponent(task.id)+'/report-history');
    if(current()){
      const versions=savedVersions(task,history,version);
      if(version!==null&&!versions.length)throw Error('报告版本'+version+'无法读取');
      $('#dialog-content').innerHTML=savedVersionLinks(task,versions);
    }
  }catch(error){if(current())$('#dialog-content').textContent=error.message}
}
async function openReportVersions(trigger,{fromChat=false}={}){
  if(!selected||historical||selectingTaskId||leaving)return;
  if(reportVersionBusy(selected))return;
  const fromContext=!!trigger?.closest('#context-report');
  const inline=artifactsOpen&&canUseDesk();
  const available=fromContext?contextOpen:fromChat?!artifactsOpen:inline;
  if(!available)return;
  const origin=trigger?.closest('[data-work-menu]')?.querySelector('[data-work-menu-toggle]')||document.activeElement;
  const read={id:selected.id,version:selected.version,selection:taskSelectionGeneration,
    stage,desk:artifactsOpen,modal:modalGeneration,origin,fromContext};
  reportVersionRead=read;syncReportVersionControls();
  try{
    const history=await api('/api/tasks/'+encodeURIComponent(read.id)+'/report-history');
    if(!reportVersionCurrent(read))return;
    const versions=savedVersions(read,history);
    let returnFocus=read.origin;
    if(fromContext&&compactContext.matches){
      returnFocus=contextReturnFocus||$('.workbench-head [data-action="artifacts"]');
      setContext(false,{restoreFocus:false});
    }
    modal('交付版本',savedVersionLinks(read,versions,inline));
    if(inline)reportVersionDialog={...read,versions,modal:modalGeneration};
    previousFocus=returnFocus;
  }catch(error){if(reportVersionCurrent(read))notice(error.message)}
  finally{if(reportVersionRead===read){reportVersionRead=null;syncReportVersionControls()}}
}
function showReportEdition(edition){
  if(leaving||historical||!selected||selectingTaskId)return;
  rememberReportEdition(selected,edition);
  if($('#dialog').open)$('#dialog').close();
  view(3);
  focusWorkspaceControl($('#content [data-report-profile][aria-pressed="true"]'),$('#view-3'));
}
function chooseReportVersion(link){
  const menu=reportVersionDialog;
  if(!menu||!$('#dialog').open||!$('#dialog').contains(link)||menu.modal!==modalGeneration||
     menu.selection!==taskSelectionGeneration||menu.id!==selected?.id||menu.version!==selected.version||
     menu.stage!==stage||menu.desk!==artifactsOpen||selectingTaskId)return;
  const edition=menu.versions.find(item=>String(item.version)===link.dataset.reportVersion);
  if(edition)showReportEdition(edition);
}
function openReportLibrary(){
  const hasDesk=canUseDesk();
  const examples=Object.entries(boot.history||{}).map(([key,item])=>{
    const body='<strong>'+esc(item.shortTitle||item.title)+'</strong>';
    return hasDesk?'<button type="button" class="library-row" data-history="'+esc(key)+'">'+body+'</button>':
      reportLink(historicalReportURL(key),'library-row',body);
  });
  const saved=tasks.flatMap(task=>{
    const version=latestReport(task)?.version??boot.saved_report_versions?.[task.id];
    if(!Number.isSafeInteger(version)||version<1||version>task.version)return [];
    const body='<strong>'+esc(taskTitle(task))+'</strong><span>版本'+version+'</span>';
    return [hasDesk?'<button type="button" class="library-row" data-task="'+esc(task.id)+'" data-task-report="'+version+'">'+body+'</button>':
      reportLink(savedReportURL(task.id,version),'library-row',body)];
  });
  const reports=saved.length?'<section class="library-section" aria-label="已保存报告">'+saved.join('')+'</section>':'';
  const cases=examples.length?'<section class="library-section" aria-label="研究案例">'+(reports?'<h3>研究案例</h3>':'')+examples.join('')+'</section>':'';
  modal(reports?'报告库':cases?'研究案例':'报告库',reports+cases||'<p>暂无报告</p>');
}
function modal(title,content){modalGeneration+=1;reportVersionDialog=null;closeArchivedFiles();syncReportVersionControls();window.RHTaskMenu?.close();previousFocus=document.activeElement;$('#dialog-title').textContent=title;$('#dialog-content').innerHTML=content;window.RHChoice.enhanceSelects($('#dialog-content'));$('#dialog').showModal()}
$('#dialog').addEventListener('close',()=>{
  const dialog=$('#dialog'),active=document.activeElement;
  if(!dialog.open){closeArchivedFiles();dialog.querySelector('.chat-report-preview')?.remove()}
  // A queued close from an older modal must not steal a newly chosen focus.
  if(dialog.open||(active&&active!==document.body&&active!==previousFocus&&!dialog.contains(active)))return;
  focusWorkspaceControl(previousFocus,artifactsOpen?$('.views [aria-selected="true"]'):$('#chat-input'));
});
function sourceRecordName(record){
  if(record.source==='official'&&record.operation==='etf_pcf')return '上交所ETF申赎清单';
  if(record.source==='official'&&record.operation==='etf_daily_quotes')
    return (record.parameters?.ts_code?.endsWith('.SH')?'上交所':'深交所')+'ETF日行情';
  if(record.source==='official'&&record.operation==='etf_nav_history')return '深交所ETF单位净值';
  if(record.source==='official'&&record.operation==='hsi_close_history')return '恒生指数公司收盘行情';
  if(record.source==='official'&&record.operation==='mainland_index_close_history')
    return (record.parameters?.ts_code?.endsWith('.SH')?'上交所':'深交所')+'指数收盘行情';
  if(record.source==='official'&&record.operation==='index_catalog')return '中证指数文件目录';
  if(record.source==='official'&&record.operation==='index_file')return record.snapshot?.role==='weights'?'中证指数成分权重':'中证指数成分名单';
  if(record.source==='ifind'&&record.operation==='smart_stock_picking'&&record.financial_query_contract?.query_kind==='object_discovery')return 'iFinD 候选发现';
  if(record.source==='official'&&record.operation==='release_catalog'){
    const channels={nbs_release:'国家统计局发布目录',fed_fomc:'FOMC声明目录',pbc_news:'中国人民银行发布目录',mof_news:'财政部发布目录',cninfo:'巨潮公告目录',hkex:'披露易公告目录'};
    return channels[record.parameters?.channel]||'官方发布目录';
  }
  const material=record.kind==='filing'?record.snapshot?.material:record.snapshot||record;
  if(record.kind!=='api')return material?.official_origin?.title||material?.name||record.name||record.directory?.title||'公告原件';
  const names={search_edb:'指标目录',get_indicators:'指标字段目录',get_indicator_params:'指标参数',get_limit_policy:'接口限额',income:'利润表',cashflow:'现金流量表',daily:'日行情',fund_daily:'基金日行情',daily_basic:'每日指标',get_thscode:'证券代码查询',report_query:'公告目录',cmd_history_quotation:'历史行情',real_time_quotation:'实时报价',high_frequency:'分钟行情',get_trade_dates:'交易日历',basic_data_service:'基础数据',date_sequence:'序列数据',edb_service:'经济数据',data_pool:'指数名单',smart_stock_picking:'财务数据查询'};
  return (window.RHResearchData.providerName(record)||record.source||'接口')+' '+(names[record.operation]||'接口数据');
}
function sourceRecordButtons(task){
  const D=window.RHResearchData;
  return D.records(task).map(record=>{
    const material=record.kind==='filing'?record.snapshot?.material:record;
    const name=sourceRecordName(record);
    const status=material?.status||record.stages?.parse||(record.kind==='api'?'待核验':'尚未取得正文');
    return '<div class="source-record"><button type="button" class="text" data-source="'+esc(record.id)+'"'+D.binding(task,esc)+'>'+esc(name)+'</button><span>'+esc(status)+'</span></div>';
  }).join('');
}
function fileMaterial(record){
  const snapshot=record.snapshot||record;
  return record.kind==='material'?snapshot:record.kind==='filing'?snapshot.material:null;
}
function originalFiles(task){
  if(apiMode)return Array.isArray(task?.original_files)?task.original_files:[];
  return window.RHResearchData.records(task).flatMap(record=>{
    const material=fileMaterial(record);
    if(typeof material?.original_base64!=='string'||typeof material.hash!=='string'||!/^[0-9a-f]{64}$/.test(material.hash)
      ||!window.RHArtifactExport.originalFilename(material.name))return [];
    return [{id:record.id,task_id:task.id,research_version:task.version,artifact_id:record.id,
      name:material.name,sha256:material.hash,content_base64:material.original_base64}];
  });
}
function originalDownloadButton(task,file,library=false){
  const label=library?'<strong>'+esc(file.name)+'</strong><span>研究版本'+esc(file.research_version)+'</span>':'下载原件';
  return '<button type="button" class="'+(library?'library-row':'button-secondary desk-action')+'" data-original-file="'+esc(file.id)+
    '" data-original-version="'+esc(file.research_version)+'"'+window.RHResearchData.binding(task,esc)+
    ' aria-label="下载'+esc(file.name)+'，研究版本'+esc(file.research_version)+'">'+label+'</button>';
}
function originalFileFeedback(){
  return '<p class="form-result" data-original-feedback role="status" hidden></p>';
}
function openOriginalFiles(){
  const task=selected,files=originalFiles(task);
  if(!files.length||historical||selectingTaskId)return;
  modal('留存文件',originalFileFeedback()+files.map(file=>originalDownloadButton(task,file,true)).join(''));
}
const downloadingOriginals=new Set();
async function downloadWorkspaceOriginal(button){
  const task=selected,selection=taskSelectionGeneration,dialog=button.closest('#dialog'),generation=modalGeneration;
  if(!task||historical||selectingTaskId)return;
  const version=Number(button.dataset.originalVersion);
  const matches=file=>file.id===button.dataset.originalFile&&file.task_id===task.id&&file.research_version===version;
  const file=originalFiles(task).find(matches)||(task.retained_originals||[]).find(matches);
  if(!file)return notice('原件已变化，请重新打开留存文件');
  const name=window.RHArtifactExport.originalFilename(file.name);
  if(!name)return notice('留存原件文件名无效');
  const key=JSON.stringify([task.id,version,file.id]);
  if(downloadingOriginals.has(key))return;
  const current=()=>!leaving&&!historical&&!selectingTaskId&&selection===taskSelectionGeneration
    &&selected?.id===task.id&&selected.version===task.version&&button.isConnected
    &&(!dialog||dialog.open&&generation===modalGeneration);
  const feedback=dialog?.querySelector('[data-original-feedback]');
  if(feedback)feedback.hidden=true;
  downloadingOriginals.add(key);button.setAttribute('aria-busy','true');button.setAttribute('aria-disabled','true');
  try{
    const expected={sha256:file.sha256,byteCount:file.byte_count};
    let blob;
    if(apiMode){
      const url='/api/tasks/'+encodeURIComponent(task.id)+'/original-file/'+version+'/'+encodeURIComponent(file.id);
      blob=await api(url,undefined,{blobMime:'application/octet-stream',timeout:60000});
      if(!current())return;
      await window.RHArtifactExport.verifyBytes(expected,new Uint8Array(await blob.arrayBuffer()));
    }else{
      const bytes=await window.RHArtifactExport.recoveryBytes({...expected,content_base64:file.content_base64});
      blob=new Blob([bytes],{type:'application/octet-stream'});
    }
    if(!current())return;
    const result=await artifactExport.saveOriginalBlob(name,blob,current);
    if(result.status==='failed'||result.status==='rejected')throw Error(result.error||'原件保存失败');
  }catch(error){
    if(current()){
      if(feedback){feedback.textContent=error.message;feedback.hidden=false}
      else notice(error.message);
    }
  }finally{
    downloadingOriginals.delete(key);button.removeAttribute('aria-busy');button.removeAttribute('aria-disabled');
  }
}
function openSource(id,evidenceId,originLocator){
  const task=selected,D=window.RHResearchData,originals=D.records(task);
  const record=originals.find(source=>source.id===id);
  if(!record)return notice('当前版本没有保留该原件，请查看原交付版本');
  const snapshotHash=record.snapshot_hash||record.hash;
  const matches=item=>item.id===evidenceId&&(item.artifact_id||item.artifact?.id)===id&&
    (!item.snapshot_hash||item.snapshot_hash===snapshotHash);
  const approved=evidenceId?D.scope(task.evidence,task).find(matches):null;
  const rejected=evidenceId&&!approved?D.scope(task.rejected_evidence,task).find(matches):null;
  const item=approved||(rejected?unverifiedProposal(task,rejected):null);
  const position=originLocator??item?.record_locator;
  const locator=draftObject(position)?position:undefined;
  const quote=typeof locator?.quote==='string'?locator.quote:item?.text;
  const origin=$('#dialog').open?previousFocus:null;
  modal(sourceRecordName(record),'<div id="source-reader"></div>');
  if(origin)previousFocus=origin;
  window.RHSourceViewer.mount($('#source-reader'),{record,version:task.version,locator,quote,relatedRecords:originals});
  if(evidenceId&&!item)notice('摘录定位已变化，已打开当前原件');
  const original=fileMaterial(record),originVersion=record.retained_from_version??task.version;
  const candidates=originalFiles(task).filter(file=>file.name===original?.name&&file.sha256===original?.hash
    &&(file.artifact_id===record.id||(!file.artifact_id&&file.research_version===originVersion)));
  const file=candidates.find(file=>file.research_version===originVersion)||candidates.find(file=>file.research_version===task.version);
  const reviewMaterial=record.kind==='filing'?record.snapshot?.material:record.snapshot;
  const canReview=apiMode&&task.status!=='运行中'&&['material','filing'].includes(record.kind)
    &&reviewMaterial?.status==='已解析'&&reviewMaterial.parts?.length;
  if(!file&&!canReview)return;
  const reader=$('#source-reader');
  const controls=document.createElement('div');controls.className='original-file-actions';
  if(file)controls.insertAdjacentHTML('beforeend',originalDownloadButton(task,file));
  if(canReview){
    const action=document.createElement('button');action.type='button';action.className='button-secondary desk-action';action.dataset.documentReview=id;
    action.dataset.sourceTask=task.id;action.dataset.sourceVersion=String(task.version);
    action.textContent='另建原件内容核对';controls.append(action);
  }
  reader.prepend(controls);
  if(file)reader.insertAdjacentHTML('afterbegin',originalFileFeedback());
}
function openDocumentReview(artifactId){
  const task=selected;
  const record=window.RHResearchData.records(task).find(item=>item.id===artifactId);
  if(!record)return notice('原件已变化，请重新打开来源');
  const origin=$('#dialog').open?previousFocus:null;
  modal('原件内容核对','<form id="document-review-form" data-task="'+esc(task.id)+'" data-artifact="'+esc(artifactId)+'" data-version="'+esc(task.version)+'" data-hash="'+esc(record.snapshot_hash||record.hash)+'">'+
    '<p>只核对文件陈述，不确认历史可得性或投资结论。原研究与报告保持不变。</p><label for="document-review-question">核对问题</label>'+
    '<textarea id="document-review-question" maxlength="20000" required placeholder="需要核对文件中的哪些内容？"></textarea>'+
    '<p id="document-review-feedback" role="status"></p><button type="submit">建立核对任务</button></form>');
  if(origin)previousFocus=origin;
  $('#document-review-question').focus();
}
function candidateOriginRecord(task,item){
  if(item?.identity?.mode!=='candidate_provider_observation')return null;
  const reference=item.identity.basis_ref,origin=reference?.discovery;
  const originals=window.RHResearchData.scope(task.source_records,task);
  const parents=originals.filter(record=>record.id===origin?.artifact_id);
  const quotes=originals.filter(record=>record.id===item.artifact_id);
  const source=parents.length===1?parents[0]:null,quote=quotes.length===1?quotes[0]:null;
  const available=source&&quote&&reference.task_id===task.id&&reference.research_version===task.version&&
    reference.object===item.object&&reference.artifact_id===item.artifact_id&&
    quote.hash===reference.snapshot_hash&&quote.request_hash===reference.request_hash&&
    quote.task_id===task.id&&quote.research_version===task.version&&
    source.task_id===task.id&&source.research_version===task.version&&
    source.hash===origin.snapshot_hash&&source.request_hash===origin.request_hash&&
    source.operation===origin.operation;
  const row=origin?.row_locator,table=row?.table??0;
  return available&&Number.isInteger(row?.row)&&row.row>=0&&Number.isInteger(table)&&table>=0?
    {record:source,locator:{table,row:row.row,field:source.operation==='data_pool'?'p03291_f002':'股票代码'}}:null;
}
function candidateOriginView(task,item){
  if(item.identity?.mode!=='candidate_provider_observation')return '';
  const origin=candidateOriginRecord(task,item);
  return '<div><dt>代码出处</dt><dd>'+(origin?
    '<button type="button" class="text" data-source="'+esc(origin.record.id)+'" data-source-origin="'+esc(item.id)+'"'+window.RHResearchData.binding(task,esc)+' aria-label="查看'+esc(item.object)+'的发现名单">查看发现名单</button>':
    '对应名单未保留或版本已变化')+'</dd></div>';
}
function detailEvidence(id){
  const task=selected,D=window.RHResearchData;
  const item=D.scope(task?.evidence,task).find(e=>e.id===id);
  if(!item)return notice('该证据不属于当前任务版本');
  const artifact=item.artifact_id||item.artifact?.id;
  const original=D.records(task).some(record=>record.id===artifact);
  const converted=item.value!=null&&item.conversion_factor!=null&&item.conversion_factor!==1
    ?'<div><dt>换算值</dt><dd>'+esc(item.value)+esc(item.unit)+'，换算倍数'+esc(item.conversion_factor)+'</dd></div>':'';
  const review=item.applicability_review;
  const raw=item.raw_value??item.value;
  const rawText=raw==null?'不适用':esc(raw)+esc(item.raw_unit??item.unit);
  const conditions=review?'<details><summary>核验依据与适用条件</summary><p>'+esc(item.source_receipt?.reason||'')+'</p><p>'+esc(review.reason)+'</p>'+review.conditions.map(text=>'<p>'+esc(text)+'</p>').join('')+'</details>':'';
  modal('证据来源','<h3>'+esc(item.object)+'</h3><p>'+esc(item.text)+'</p><dl class="facts">'+
    '<div><dt>原始值与单位</dt><dd>'+rawText+'</dd></div>'+converted+
    '<div><dt>性质与所属期</dt><dd>'+esc(item.nature)+'，'+esc(item.period)+'</dd></div>'+
    (item.observation_date?'<div><dt>观测日期</dt><dd>'+esc(item.observation_date)+'</dd></div>':'')+
    '<div><dt>来源定位</dt><dd>'+esc(item.source)+'，'+esc(sourceLocatorLabel(item))+'</dd></div>'+
    candidateOriginView(task,item)+
    '<div><dt>公开时间</dt><dd>'+esc(item.published_at||'未核验')+'</dd></div>'+
    (['current_snapshot','current_original'].includes(item.availability?.kind)?'<div><dt>当前版本取得时间</dt><dd>'+esc(cutoffLabel(item.availability.obtained_at))+'</dd></div><div><dt>可用范围</dt><dd>当前版本研究，不证明历史当时可得'+(item.availability.kind==='current_original'?'或事件因果':'')+'。</dd></div>':'')+
    (item.usage_scope==='document_content'?'<div><dt>可用范围</dt><dd>仅核对原件内容，不用于历史时点或投资判断。</dd></div>':'')+
    '<div><dt>来源与提取核验</dt><dd>'+esc(item.source_check)+'</dd></div>'+
    '<div><dt>研究适用性核验</dt><dd>'+esc(item.applicability_check)+'</dd></div></dl>'+conditions+
    (original?'<button type="button" data-source="'+esc(artifact)+'" data-source-evidence="'+esc(item.id)+'"'+D.binding(task,esc)+'>查看原件</button>':'')+
    '<details><summary>快照与版本</summary><p>内容版本'+item.content_version+'</p><p>'+esc(item.snapshot_hash)+'</p></details>');
}
function detailCalculation(id){
  const item=selected.calculations.find(c=>c.id===id);if(!item)return notice('计算引用不存在');
  const labels={fee_a:'费用A',fee_b:'费用B',receipt:'收款',debt:'债务',cost:item.method==='gross_margin_v1'?'营业成本':'费用',shares:'股本',price:'价格',rate:'必要回报率',years:'期限',start_price:'期初收盘价',end_price:'期末收盘价',nav:'官方单位净值',revenue:'营业收入',net_profit:'报表净利润',operating_cash_flow:'经营现金流净额'};
  const parameterLabels={unit:'输入单位',currency:'币种',return_kind:'变化类型',price_basis:'价格口径',start_date:'起始交易日',end_date:'结束交易日',price_kind:'价格类型',nav_kind:'净值类型',valuation_time:'估值时点',amount_unit:'金额单位',shares_unit:'股本单位',period:'报告期间',accounting_scope:'报表范围',accounting_standard:'会计准则'};
  const values={CNY:'人民币',price_change:'价格变化',unadjusted:'未复权',close:'收盘价',official:'官方单位净值',consolidated:'合并报表',parent:'母公司报表',CAS:'企业会计准则',IFRS:'国际财务报告准则',US_GAAP:'美国公认会计准则'};
  const limits=Array.isArray(item.limitations)?item.limitations.join('；'):item.limitations;
  const rows=Object.entries(item.inputs).map(([key,value])=>{
    const ref=item.input_bindings?.[key],evidence=selected.evidence.find(e=>e.id===ref);
    const unit=evidence?.unit||(key==='rate'?'比例':key==='years'?'年':'原记录未注明');
    return '<tr role="row"><td role="cell" class="calculation-input-name">'+esc(labels[key]||key)+'</td><td role="cell" data-label="原值">'+esc(value)+'</td><td role="cell" data-label="单位">'+esc(unit)+'</td><td role="cell" class="calculation-input-source">'+(evidence?'<button class="text" data-evidence="'+esc(ref)+'">查看原件</button>':'假设或历史输入')+'</td></tr>';
  }).join('');
  const binding=item.method_binding;
  const knowledge=binding?'知识版本'+esc(binding.knowledge_version)+'，知识条目'+esc(binding.knowledge_id):'历史计算未记录知识版本，保留原交付';
  const parameters=Object.entries(item.parameters||{}).map(([key,value])=>'<dt>'+esc(parameterLabels[key]||key)+'</dt><dd>'+esc(values[value]||value)+'</dd>').join('');
  modal(calculationName(item),'<p class="lead">结果：'+Number(item.output.value).toFixed(4)+esc(item.output.unit)+'</p><p>适用限制：'+esc(limits||'原计算未单独记录适用限制，请结合原件口径解释。')+'</p><div class="table-wrap calculation-inputs" tabindex="0" aria-label="计算输入"><table role="table"><thead role="rowgroup"><tr role="row"><th role="columnheader">输入</th><th role="columnheader">原值</th><th role="columnheader">单位</th><th role="columnheader">依据</th></tr></thead><tbody role="rowgroup">'+rows+'</tbody></table></div><details class="calculation-metadata"><summary>查看知识、实现版本与参数</summary><p>'+knowledge+'。</p><p>实现版本'+esc(item.implementation_version)+'</p><dl>'+parameters+'</dl></details>');
}
function taskActionReached(action,target,fresh){
  if(!taskSnapshotMatches(fresh,target.id))return false;
  if(action==='update')return fresh.version===target.version+1&&Number.isSafeInteger(fresh.batch)&&fresh.batch>=1;
  if(fresh.version!==target.version||fresh.cutoff!==target.cutoff||fresh.revision<=target.revision)return false;
  if(action==='start'||action==='resume')return fresh.batch===target.batch+1;
  if(action==='finish-draft')return fresh.batch===target.batch+1&&fresh.run_mode==='local_draft';
  if(fresh.batch!==target.batch)return false;
  if(action==='cancel')return fresh.status==='已取消';
  return action==='retry-save'&&['已完成','部分完成'].includes(fresh.status)&&
    fresh.delivery?.version===target.version&&typeof fresh.delivery.digest==='string'&&!!fresh.delivery.digest;
}
async function taskAction(action){
  if(selectingTaskId)return notice('正在读取任务，请稍候');
  if(importing)return notice('正在保存材料，请稍候');
  if(!apiMode)return notice('独立HTML支持阅读、备份与草稿；执行共享引擎请打开ResearchHelper.app');
  if(leaving||accountBusy)return;
  if(!selected||historical)return notice('请选择当前研究');
  const target=selected,{id,version,revision}=target,selectionGeneration=taskSelectionGeneration;
  if(target.batch_settling===true)return notice('上一批研究正在收尾，请稍候');
  const recovery=['start','resume','retry-save','finish-draft'].includes(action);
  if(recovery&&target.recovery_block?.reason)return notice(target.recovery_block.reason);
  const expectedTask={id,version,batch:target.batch,cutoff:target.cutoff,input_hash:target.input_hash};
  if(pendingTaskActions.has(id))return notice('研究操作正在处理，请稍候');
  const dispatch=['start','resume','update'].includes(action);
  const path='/api/tasks/'+encodeURIComponent(id);
  const origin=document.activeElement;
  const originComposer=origin?.matches('#chat-form .send-control')&&origin.dataset.taskOperation===action;
  const restoreFocus=originComposer||origin?.dataset?.action===action;
  const originContext=contextOpen&&$('#task-context').contains(origin);
  const originDesk=artifactsOpen,originStage=stage;
  let recoverySelection=selectionGeneration,attempted=false;
  const visibleTask=()=>!leaving&&!accountBusy&&!historical&&!selectingTaskId&&selected?.id===id;
  const selectionMatches=()=>visibleTask()&&taskSelectionGeneration===selectionGeneration&&selected.version===version;
  const adopt=fresh=>{
    if(!taskSnapshotMatches(fresh,id)||taskSnapshotIsOlder(fresh,target))return false;
    const current=tasks.find(task=>task.id===id);
    if(taskSnapshotIsOlder(fresh,current)||visibleTask()&&taskSnapshotIsOlder(fresh,selected))return false;
    tasks=tasks.map(task=>task.id===id?fresh:task);
    if(visibleTask()&&JSON.stringify(selected)!==JSON.stringify(fresh)){
      if(selectionMatches()){
        setTask(fresh);recoverySelection=taskSelectionGeneration;
      }else{
        // The user has reselected this task since the write. Refresh in place.
        selected=fresh;render();watch();
      }
    }else renderTaskList();
    return true;
  };
  const setBusy=busy=>{
    if(selected?.id===id&&!historical){
      document.querySelectorAll('#task-actions button[data-action],#content button[data-action]').forEach(button=>{
        if(!['start','resume','cancel','update','retry-save','finish-draft'].includes(button.dataset.action))return;
        button.disabled=busy;
        if(busy)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
      });
    }
    syncComposerAvailability();
  };
  pendingTaskActions.add(id);setBusy(true);
  try{
    if(dispatch&&!await verifyExecutionBeforeSend())return;
    if(leaving||accountBusy)return;
    if(!selectionMatches()||selected.revision!==revision)
      return notice('研究已切换或更新，请重新确认操作');
    attempted=true;
    const fresh=await api(path+'/'+action,{...(dispatch?executionExpectation():{}),expected_task:expectedTask});
    if(!taskActionReached(action,target,fresh))throw Error('研究操作回执与任务状态不一致');
    if(!leaving&&!accountBusy)adopt(fresh);
  }catch(error){
    if(leaving||accountBusy)return;
    if(error.failureCode==='research_model_required'){
      if(visibleTask())showModelRequired(true);
      return;
    }
    if(attempted&&![403,404].includes(error.status)){
      // An interrupted response can belong to an already committed action.
      const fresh=await api(path).catch(()=>null);
      if(leaving||accountBusy)return;
      if(adopt(fresh)&&(taskActionReached(action,target,fresh)||
          recovery&&error.failureCode==='execution_time_exhausted'&&
          fresh.version===version&&fresh.recovery_block?.code===error.failureCode))return;
    }
    if(!visibleTask())return;
    if(attempted&&!error.status)throw Error('操作结果暂未确认，请核对当前任务。');
    throw error;
  }finally{
    pendingTaskActions.delete(id);setBusy(false);
    if(restoreFocus&&taskSelectionGeneration===recoverySelection&&selected?.id===id&&!historical
      &&artifactsOpen===originDesk&&(!originDesk||stage===originStage)
      &&[origin,document.body].includes(document.activeElement))
      focusWorkspaceControl(origin,originContext&&contextOpen?$('#task-context .drawer-close'):
        originComposer?$('#chat-input'):originDesk?$('#view-'+stage):$('#chat-input'));
  }
}
const artifactExport=window.RHArtifactExport.create({notify:notice});
function download(name,text,mime='text/html'){return artifactExport.saveText(name,text,mime)}
function encodeBase64(bytes){
  let binary='';
  for(let offset=0;offset<bytes.length;offset+=8192)binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
  return btoa(binary);
}
function backupArchiveBody(){
  return '<h2>任务备份</h2><div class="form-actions"><button class="button-primary" type="button" data-action="archive-files">报告与原件</button></div>';
}
async function offlineBackupWire(task){
  if(!isBackupArchive(task))return JSON.stringify({workspaceVersion:4,tasks:[task]});
  if(typeof task.legacy_backup_text==='string')return task.legacy_backup_text;
  if(task.legacy_backup_gzip){
    if(typeof DecompressionStream!=='function')throw Error('请用支持离线备份的新版浏览器打开');
    const bytes=Uint8Array.from(atob(task.legacy_backup_gzip),character=>character.charCodeAt(0));
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    return decodeBackup(await new Response(stream).arrayBuffer());
  }
  return JSON.stringify(task.legacy_backup);
}
async function backupTask(button){
  const task=selected,selection=taskSelectionGeneration;
  if(!task||historical)throw Error('请先选择当前研究任务');
  const current=()=>!leaving&&!historical&&selection===taskSelectionGeneration&&selected?.id===task.id&&selected.version===task.version;
  const disabled=button.disabled;button.disabled=true;button.setAttribute('aria-busy','true');
  try{
    const path='/api/tasks/'+encodeURIComponent(task.id);
    let response;
    if(apiMode){
      try{response=await api(path+'/backup',undefined,{retainWire:true,timeout:60000})}
      catch(error){
        if(error.failureCode!=='backup_package_required')throw error;
        if(!current())throw Error('研究已切换，请重新备份当前任务');
        const query=new URLSearchParams({version:task.version,revision:task.revision});
        const packageFile=await api(path+'/backup-package?'+query,undefined,{blobMime:'application/zip',timeout:180000});
        if(!current())throw Error('研究已切换，请重新备份当前任务');
        if(!packageFile.size||packageFile.size>taskPackageLimit)throw Error('任务ZIP备份体积无效');
        await artifactExport.saveBackup('ResearchHelper任务备份.zip',packageFile,current);
        return;
      }
    }
    const complete=response?.value?.tasks?.[0],wire=response?.wire??await offlineBackupWire(task);
    if(!current())
      throw Error('研究已切换，请重新备份当前任务');
    if(apiMode&&!isBackupArchive(task)&&(!taskSnapshotMatches(complete,task.id)||complete.version!==task.version||taskSnapshotIsOlder(complete,task)||complete.workspace_view_version!==undefined))
      throw Error('完整备份响应不匹配，请重新读取任务后再试');
    if(typeof wire!=='string'||new Blob([wire]).size>taskBackupLimit)
      throw Error('任务备份超过20MB。请分别导出报告及留存原件。');
    await download('ResearchHelper任务备份.json',wire,'application/json');
  }finally{button.disabled=disabled;button.removeAttribute('aria-busy')}
}
function archivedFilesCurrent(panel){
  return panel===archiveFilePanel&&!leaving&&!historical&&selected?.id===panel.taskId
    &&panel.selection===taskSelectionGeneration&&panel.generation===modalGeneration
    &&$('#dialog').open&&panel.content.isConnected;
}
function closeArchivedFiles(){
  const panel=archiveFilePanel;
  archiveFilePanel=null;
  panel?.controller.abort();
}
async function openArchivedFiles(){
  const task=selected,selection=taskSelectionGeneration;
  if(!isBackupArchive(task)||historical||leaving)return;
  modal('下载报告与原件','<p id="archive-file-feedback" class="form-result" role="status" hidden></p><div id="archive-files" tabindex="-1"></div>'+
    '<div class="form-actions"><button class="button-quiet" type="button" data-action="backup">下载完整备份</button></div>');
  archiveFilePanel={taskId:task.id,selection,generation:modalGeneration,content:$('#archive-files'),files:[],controller:new AbortController()};
  await readArchivedFiles(archiveFilePanel);
}
async function readArchivedFiles(panel){
  if(!panel||!archivedFilesCurrent(panel)||panel.loading)return;
  const task=selected,content=panel.content,signal=panel.controller.signal;
  const restoreFocus=content.contains(document.activeElement);
  if(restoreFocus)content.focus({preventScroll:true});
  panel.loading=true;
  panel.files=[];
  content.setAttribute('aria-busy','true');
  content.innerHTML='<p role="status">正在读取任务备份…</p>';
  try{
    let reports,originals;
    if(apiMode&&Array.isArray(task.backup_package?.files)){
      const index=task.backup_package.file_index_version===2?task.backup_package:
        await api('/api/tasks/'+encodeURIComponent(task.id)+'/backup-files',undefined,{timeout:60000,signal});
      if(!Array.isArray(index?.files))throw Error('备份文件清单无法读取');
      const files=index.files.map((file,packageIndex)=>({...file,packageIndex,label:file.filename,edition:'版本'+file.version}));
      reports=files.filter(file=>file.kind==='report');originals=files.filter(file=>file.kind==='original');
    }else{
      const backup=apiMode?await api('/api/tasks/'+encodeURIComponent(task.id)+'/backup',undefined,{signal}):
        JSON.parse((await offlineBackupWire(task)).replace(/^\uFEFF/,''));
      ({reports,originals}=window.RHArtifactExport.recoveryEntries(backup));
    }
    if(!archivedFilesCurrent(panel))return;
    const section=(label,files)=>files.length?'<section class="section"><h3>'+label+'</h3>'+files.map(file=>{
      const index=panel.files.push(file)-1;
      return '<button type="button" class="library-row" data-action="archive-file" data-file-index="'+index+
        '" aria-label="下载'+esc(file.label)+'，'+esc(file.edition)+'"><strong>'+esc(file.label)+'</strong><span>'+esc(file.edition)+'</span></button>';
    }).join('')+'</section>':'';
    content.innerHTML=section('研究报告',reports)+section('原件',originals)
      ||'<p>这份备份没有保存可单独下载的报告或原件。</p>';
  }catch(error){
    if(archivedFilesCurrent(panel)){
      const retry=apiMode&&(error.status==null||error.status>=500);
      content.innerHTML='<p role="status">'+esc(error.message)+'</p>'+
        (retry?'<button type="button" class="button-secondary desk-action" data-action="retry-archive-files">重新读取</button>':'');
    }
  }finally{
    panel.loading=false;
    if(archivedFilesCurrent(panel)){
      content.removeAttribute('aria-busy');
      if(restoreFocus&&(document.activeElement===content||document.activeElement===document.body))
        content.querySelector('button')?.focus({preventScroll:true});
    }
  }
}
async function downloadArchivedFile(button){
  const panel=archiveFilePanel;
  if(!panel||!archivedFilesCurrent(panel)||panel.saving||!panel.content.contains(button))return;
  const file=panel.files[Number(button.dataset.fileIndex)];
  if(!file)return;
  // Keep focus on the selected row while checking bytes and using the native save panel.
  const feedback=$('#archive-file-feedback');
  feedback.hidden=true;
  panel.saving=true;button.setAttribute('aria-busy','true');button.setAttribute('aria-disabled','true');
  try{
    const current=()=>archivedFilesCurrent(panel);
    let result;
    if(Number.isSafeInteger(file.packageIndex)){
      const blob=await api('/api/tasks/'+encodeURIComponent(panel.taskId)+'/backup-file/'+file.packageIndex,
        undefined,{blobMime:'application/octet-stream',timeout:60000,signal:panel.controller.signal});
      if(!current())return;
      if(blob.size!==file.byte_count)throw Error('备份文件长度校验失败');
      await window.RHArtifactExport.verifyBytes({sha256:file.sha256??null},await blob.arrayBuffer());
      if(!current())return;
      result=await artifactExport.saveOriginalBlob(file.filename,blob,current);
    }else{
      const bytes=await window.RHArtifactExport.recoveryBytes(file);
      if(!current())return;
      result=file.kind==='report'?await download(file.filename,file.html,'application/octet-stream'):
        await artifactExport.saveOriginal(file,bytes);
    }
    if(result.status==='failed'||result.status==='rejected')throw Error(result.error||'文件保存失败');
  }catch(error){
    if(archivedFilesCurrent(panel)){feedback.textContent=error.message;feedback.hidden=false}
  }finally{panel.saving=false;button.removeAttribute('aria-busy');button.removeAttribute('aria-disabled')}
}
function openVisualLibrary(){
  const edition=artifactsOpen&&stage===3&&!historical&&!selectingTaskId?shownReport(selected):null;
  const origin=document.activeElement?.closest('[data-work-menu]')?.querySelector('[data-work-menu-toggle]');
  modal('图表方案','<iframe id="visual-library-frame" class="visual-library-frame" title="图表方案与配色" sandbox="allow-scripts allow-same-origin allow-downloads" src="visual-library.html?embedded=1"></iframe>');
  visualLibraryBinding=edition?{panel:$('#visual-library-frame'),edition,profile:reportProfile(selected,edition.version),
    selection:taskSelectionGeneration}:null;
  if(origin)previousFocus=origin;
  $('#dialog').classList.add('visual-library-host');
}
function visualLibraryCurrent(binding){
  return !!binding&&!leaving&&!historical&&!selectingTaskId&&artifactsOpen&&stage===3&&
    binding.selection===taskSelectionGeneration&&sameReport(binding.edition,shownReport(selected))&&
    binding.profile===reportProfile(selected,binding.edition.version);
}
function syncVisualLibrary(){
  const binding=visualLibraryBinding,dialog=$('#dialog');
  if(!binding||!dialog.open||binding.panel!==$('#visual-library-frame')||visualLibraryCurrent(binding))return;
  previousFocus=$('#content [data-work-menu-toggle]')||previousFocus;
  dialog.close();notice('报告已更新，请重新打开图表方案');
}
function reportVisuals(){
  const frame=$('#report-frame');
  if(historical||!reportReader.isReady(frame))return null;
  try{return frame.contentWindow.RHVisuals}catch{return null}
}
function reportStyleKey(edition,profile){
  const key=edition.task_id+':'+edition.version;
  return profile?key+':'+profile:key;
}
function savedReportStyles(edition,profile,legacySchemes=false){
  const shared=draft.reportStyles?.[reportStyleKey(edition)];
  const variant=draft.reportStyles?.[reportStyleKey(edition,profile)];
  return {theme:shared?.theme||variant?.theme||'research',
    schemes:{...(variant?.schemes??(legacySchemes?shared?.schemes:undefined))}};
}
function reportPresentation(edition,profile,visuals=reportVisuals()){
  const value=visuals?.profile();
  return value?{theme:value.theme||'research',schemes:{...value.schemes}}:savedReportStyles(edition,profile);
}
function sameReportPresentation(first,second){
  if(first.theme!==second?.theme||!second.schemes||typeof second.schemes!=='object'||Array.isArray(second.schemes))return false;
  const targets=Object.keys(first.schemes);
  return targets.length===Object.keys(second.schemes).length&&
    targets.every(target=>Object.hasOwn(second.schemes,target)&&first.schemes[target]===second.schemes[target]);
}
function saveReportStyles(edition,profile,presentation){
  const key=reportStyleKey(edition),styles=draft.reportStyles||{};
  // Keep the palette shared and leave legacy schemes available for migration
  // when the other report is first read successfully.
  draft.reportStyles={...styles,[key]:{theme:presentation.theme,schemes:styles[key]?.schemes||{}},
    [reportStyleKey(edition,profile)]:presentation};
}
function restoreReportStyles(frame){
  const edition=historical?null:shownReport(selected);
  if(!edition)return;
  const profile=reportProfile(selected,edition.version),styles=draft.reportStyles;
  const variant=styles?.[reportStyleKey(edition,profile)];
  if(!variant&&!styles?.[reportStyleKey(edition)])return;
  try{
    const visuals=frame.contentWindow?.RHVisuals;
    if(!visuals)return;
    const saved=savedReportStyles(edition,profile,true);
    const compatible=new Map(visuals.contexts().map(chart=>[chart.target,chart.compatible]));
    const schemes=Object.fromEntries(Object.entries(saved.schemes).filter(([target,scheme])=>compatible.get(target)?.includes(scheme)));
    const theme=visuals.catalogue.themes.some(row=>row.id===saved.theme)?saved.theme:visuals.profile().theme;
    const applied=visuals.applyProfile({theme,schemes});
    if(!sameReportPresentation(saved,applied)||(!variant&&Object.keys(saved.schemes).length)){
      saveReportStyles(edition,profile,applied);remember();
    }
  }catch{/* The saved report remains readable with its original styling. */}
}
$('#dialog').addEventListener('close',()=>{
  const dialog=$('#dialog');
  if(dialog.open&&$('#visual-library-frame'))return;
  visualLibraryBinding=null;dialog.classList.remove('visual-library-host');
});
window.addEventListener('message',async event=>{
  const panel=$('#visual-library-frame'),binding=visualLibraryBinding;
  if(leaving||!$('#dialog').open||!panel||event.source!==panel.contentWindow||event.origin!==location.origin&&event.origin!=='null')return;
  if(event.data?.type==='rh:visual-library-close'){$('#dialog').close();return}
  if(binding&&(binding.panel!==panel||!visualLibraryCurrent(binding))){syncVisualLibrary();return}
  const report=binding?reportVisuals():null;
  if(event.data?.type==='rh:visual-library-ready'){
    panel.contentWindow.postMessage({type:'rh:visual-library-context',targets:report?.contexts()||[],canApply:!!report,theme:report?.profile().theme||'research'},'*');
  }else if(event.data?.type==='rh:visual-library-save-png'){
    const current=()=>!leaving&&$('#dialog').open&&panel===$('#visual-library-frame');
    if(panel.dataset.savingImage)return;
    panel.dataset.savingImage='true';
    let status='failed';
    try{status=(await artifactExport.saveChart(event.data.name,event.data.png,current)).status}
    catch(error){if(current())notice(error.message)}
    finally{
      delete panel.dataset.savingImage;
      if(current())panel.contentWindow.postMessage({type:'rh:visual-library-png-result',status},'*');
    }
  }else if(event.data?.type==='rh:visual-library-apply'&&report){
    try{
      const presentation=report.applyProfile(event.data.profile);
      saveReportStyles(binding.edition,binding.profile,presentation);artifactExport.updated();remember();notice('已应用图表方案');
    }catch(error){notice(error.message)}
  }
});
function printReport(){
  const frame=currentPrintFrame();
  if(!frame)throw Error('报告尚未读取完成，请先重新读取报告');
  const bridge=window.webkit?.messageHandlers?.researchhelper;
  if(bridge)bridge.postMessage({action:'print'});
  else reportReader.print(frame);
}
async function localHistoryHtml(key,signal){
  if(!Object.hasOwn(boot.history,key)||!/^[a-z0-9-]+$/.test(key))throw Error('历史报告不存在');
  if(typeof DecompressionStream!=='function')throw Error('此浏览器无法保存离线报告，请用新版浏览器打开demo');
  const unavailable='离线报告文件无法读取，请检查demo是否完整';
  const encoded=await new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    function finish(error,value){
      clearTimeout(deadline);
      signal.removeEventListener('abort',abort);
      script.remove();
      if(error)reject(error);
      else resolve(value);
    }
    const fail=()=>finish(Error(unavailable));
    const abort=()=>finish(signal.reason);
    const deadline=setTimeout(fail,10000);
    if(signal.aborted){abort();return}
    signal.addEventListener('abort',abort,{once:true});
    script.onload=()=>script.dataset.reportArchive?finish(null,script.dataset.reportArchive):fail();
    script.onerror=fail;
    script.src='reports/'+key+'.download.js';
    document.head.append(script);
  });
  try{
    const bytes=Uint8Array.from(atob(encoded),char=>char.charCodeAt(0));
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'),{signal});
    return await new Response(stream).text();
  }catch(error){
    if(signal.aborted)throw error;
    throw Error(unavailable);
  }
}
async function readReportHTML(url,signal,identity){
  const controller=new AbortController(),abort=()=>controller.abort();
  if(signal.aborted)return null;
  signal.addEventListener('abort',abort,{once:true});
  const deadline=setTimeout(abort,15000);
  try{
    const response=await fetch(url,{signal:controller.signal});
    if(signal.aborted)return null;
    if(response.status===401){leaveWorkspace();return null}
    if(!response.ok||!response.headers.get('Content-Type')?.startsWith('text/html'))
      throw Error('报告读取失败，请重新登录或检查任务权限');
    if(identity){
      const saved=response.headers.get('X-ResearchHelper-Report');
      if(!saved?.startsWith(identity.prefix)||(identity.digest&&saved!==identity.prefix+identity.digest))
        throw Error('读取的报告与所选版本不一致');
    }
    return await response.text();
  }catch(error){
    if(error.name==='AbortError'&&!signal.aborted)throw Error('报告读取超时，请重新下载');
    throw error;
  }finally{clearTimeout(deadline);signal.removeEventListener('abort',abort)}
}
async function downloadChatReport(button){
  const card=button.closest(savedReportCardSelector);
  if(!card)return;
  const {chatReportTask:id,chatReportVersion:versionText,chatReportDigest:digest,chatReportHistory:key}=card.dataset;
  const title=card.querySelector('strong').textContent,version=key?null:Number(versionText);
  const selection=taskSelectionGeneration;
  const current=()=>!leaving&&!selectingTaskId&&card.isConnected&&selection===taskSelectionGeneration;
  if(!current()||(key?historical?.key!==key:!id||!Number.isSafeInteger(version)||version<1))return;
  return artifactExport.run(async(signal,handOff)=>{
    let text;
    if(key)text=apiMode?await readReportHTML(historicalReportURL(key),signal):await localHistoryHtml(key,signal);
    else if(apiMode)text=await readReportHTML(savedReportURL(id,version)+'?profile=standard',signal,{prefix:id+':'+version+':',digest});
    else text=localChatReport(card)?.task.report_html;
    if(signal.aborted||!current())return;
    if(!text)throw Error('此版本未保存报告正文');
    handOff();
    return download(window.RHArtifactExport.reportFilename(title,version),text);
  },current);
}
async function exportReport(history=false){
  const selection=taskSelectionGeneration,task=selected,reference=historical;
  const edition=history?null:shownReport(task);
  if(history&&!reference||!history&&!edition)throw Error('报告尚未保存');
  const frame=$('#report-frame');
  if(!artifactsOpen||stage!==3||!frame||selectingTaskId||leaving)throw Error('请打开要导出的报告');
  const version=edition?.version,profile=edition?reportProfile(task,version):'standard';
  const visuals=history?null:reportVisuals();
  const presentation=edition&&reportPresentation(edition,profile,visuals);
  const format=history?'html':($('#report-format')?.value||'html');
  const current=()=>!leaving&&!selectingTaskId&&artifactsOpen&&stage===3&&$('#report-frame')===frame&&
    selection===taskSelectionGeneration&&historical===reference&&selected?.id===task?.id&&
    (history||(sameReport(edition,shownReport(selected))&&reportProfile(selected,version)===profile&&
      ($('#report-format')?.value||'html')===format&&sameReportPresentation(presentation,reportPresentation(edition,profile))));
  return artifactExport.run(async(signal,handOff)=>{
    function assertCurrent(){
      if(signal.aborted||!current())throw Error('研究或导出选择已变化，请重新导出');
    }
    function saveCurrent(){assertCurrent();handOff()}
    if(!history&&apiMode){
      let result;
      try{
        result=await api('/api/export',{task_id:task.id,version,profile,format,visual_profile:presentation},{signal,timeout:90000});
      }catch(error){
        if(error.name==='TimeoutError')throw Error('报告转换超时，请重新导出');
        throw error;
      }
      assertCurrent();
      if(result?.task_id!==task.id||result.version!==version||result.profile!==profile||
         !/^[a-f0-9]{64}$/.test(edition.digest||'')||result.report_digest!==edition.digest)throw Error('导出响应与研究版本不一致');
      if(!sameReportPresentation(presentation,result.visual_profile))
        throw Error('导出响应与当前图表方案不一致');
      return artifactExport.saveReport(result,format,saveCurrent);
    }
    if(!history&&format!=='html'){
      if(format==='pdf'){printReport();notice('请选择系统打印中的存储为PDF');return}
      throw Error('请在App中导出此格式');
    }
    const reportTitle=history||!reportReader.isReady(frame)?'':frame.contentDocument?.title?.trim();
    const name=history?(reference.shortTitle||reference.title):
      (reportTitle||edition.title||(version===task.version?task.draft_report?.title:null)||
       task.title||task.question||'研究报告');
    let text;
    if(history&&!apiMode)text=await localHistoryHtml(reference.key,signal);
    else if(history){text=await readReportHTML(historicalReportURL(reference.key),signal);if(text===null)return}
    else text=profile==='detailed'?task.report_detailed_html:task.report_html;
    assertCurrent();
    if(!text)throw Error('此版本未保存报告正文');
    if(visuals&&(presentation.theme!=='research'||Object.keys(presentation.schemes||{}).length))text=visuals.styleExport(text,presentation);
    saveCurrent();
    const label=history?'':profile==='detailed'?'详细报告':'标准报告';
    return download(window.RHArtifactExport.reportFilename(String(name),version,label),text);
  },current);
}
document.addEventListener('click',async event=>{
  const versionLink=event.target.closest('a[data-report-version]');
  if(versionLink&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){
    event.preventDefault();chooseReportVersion(versionLink);return;
  }
  const button=event.target.closest('button');if(!button)return;
  try{
    if(leaving)return;
    if(button.dataset.starterPrompt){
      if(sending)return;
      const input=$('#chat-input');
      input.value=button.dataset.starterPrompt;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.focus();
      return;
    }
    if(button.dataset.action==='reconnect'){await init();return}
    if((sending||importing||accountBusy)&&(button.dataset.task||button.dataset.history||button.dataset.case||['new','import','logout'].includes(button.dataset.action))){notice('正在保存研究，请稍候');return}
    if(button.dataset.paperTarget){
      const target=document.getElementById(button.dataset.paperTarget);if(target&&$('#content').contains(target)){target.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});target.focus({preventScroll:true})}return;
    }
    if(button.dataset.draftSource||button.dataset.draftCalculation){
      if(button.dataset.sourceTask!==selected?.id||button.dataset.sourceVersion!==String(selected.version))return notice('任务已切换，请使用当前底稿');
      const D=window.RHResearchData,facts=D.draftFacts(selected);
      if(button.dataset.draftSource){
        const fact=facts.find(item=>item.id===button.dataset.draftSource);
        if(fact)openSource(fact.artifact_id,undefined,fact.locator);
      }else{
        const calc=D.scope(D.paper(selected)?.calculations,selected).find(item=>item.id===button.dataset.draftCalculation);
        if(calc){const ids=new Set(calc.data_refs||[]),inputs=facts.filter(item=>ids.has(item.id));modal(calc.label||'计算依据',draftArithmetic([calc])+draftFactsTable({...selected,draft_report:{facts:inputs}}));}
      }
      return;
    }
    if(button.dataset.sourceTask&&(button.dataset.sourceTask!==selected?.id||button.dataset.sourceVersion!==String(selected.version)))return notice('任务已切换，请使用当前底稿');
    if(button.dataset.removeMaterial!==undefined){await removeQueuedMaterial(Number(button.dataset.removeMaterial));return}
    if(button.closest('[data-mode-switch]')){switchMode(button.dataset.mode,button);return}
    if(button.dataset.gapQuestion||button.dataset.gapSources){
      if(!gapTaskMatches(button))return notice('任务已变化，请使用当前任务的入口');
      if(button.dataset.gapQuestion)locateGapQuestion(button.dataset.gapQuestion);
      else if(button.dataset.gapSources)openGapSources(button.dataset.gapSources);
      return;
    }
    if(button.dataset.reportProfile){
      if(!canUseDesk())return;
      const edition=shownReport(selected);
      if(!edition)return;
      const profile=button.dataset.reportProfile;if(!['standard','detailed'].includes(profile))return;
      if(!apiMode&&profile==='detailed'&&!selected.report_detailed_html){notice('此旧版本未打包详细版，可在App中导出');return}
      const key=edition.task_id+':'+edition.version;
      draft.reportProfiles={...(draft.reportProfiles||{}),[key]:profile};remember();view(3);return;
    }
    if(button.dataset.stage!==undefined){view(button.dataset.stage);return}
    if(button.dataset.task){
      selectTaskEntry(button.dataset.task,{reportVersion:Number(button.dataset.taskReport)});return;
    }
    if(button.dataset.history){selectReportExample(button.dataset.history);return}
    if(button.dataset.case){if($('#dialog').open)$('#dialog').close();let task;if(apiMode){task=await api('/api/cases',{id:button.dataset.case});tasks.unshift(task)}else{task=tasks.find(t=>t.fixture===button.dataset.case);if(!task)return notice('该合成案例未打包，请在App中运行');}setTask(task);return}
    if(button.dataset.evidence){const fromCalculation=$('#dialog').open&&!!$('#dialog .calculation-metadata'),origin=previousFocus;if($('#dialog').open&&!fromCalculation)$('#dialog').close();detailEvidence(button.dataset.evidence);if(fromCalculation)previousFocus=origin;return}
    if(button.dataset.documentReview){openDocumentReview(button.dataset.documentReview);return}
    if(button.dataset.calculation){detailCalculation(button.dataset.calculation);return}
    if(button.dataset.draftBasis!==undefined){openDraftBasis(button.dataset.draftBasis);return}
    if(button.dataset.source){
      if(button.dataset.draftExcerpt){
        const {part,quote}=JSON.parse(button.dataset.draftExcerpt);
        openSource(button.dataset.source,undefined,{part,quote});return;
      }
      const item=button.dataset.sourceOrigin?window.RHResearchData.scope(selected?.evidence,selected).find(e=>e.id===button.dataset.sourceOrigin):null;
      const origin=candidateOriginRecord(selected,item);
      openSource(button.dataset.source,button.dataset.sourceEvidence,origin?.record.id===button.dataset.source?origin.locator:null);return;
    }
    if(button.dataset.retryOriginal){
      if(selectingTaskId)return notice('正在读取任务，请稍候');
      const original=button.dataset.retryOriginal,id=button.dataset.originalTask;
      if(!apiMode||recoveringOriginals.has(original))return;
      recoveringOriginals.add(original);button.disabled=true;button.textContent='正在解析';
      try{
        const fresh=await api('/api/tasks/'+id+'/retry-original',{original_id:original});
        tasks=tasks.map(task=>task.id===id?fresh:task);
        if(selected?.id===id&&!historical)setTask(fresh);else renderTaskList();
        notice((fresh.retained_originals||[]).some(item=>item.id===original)?'原件已保留，仍未解析成功':'原件已重新解析，内容仍待核验');
      }finally{recoveringOriginals.delete(original);if(selected?.id===id&&!historical)render()}
      return;
    }
    if(button.dataset.originalFile){await downloadWorkspaceOriginal(button);return}
    const action=button.dataset.action;
    if(action==='copy-message'){await messageActions.copy(button);return}
    if(action==='new'){cancelTaskSelection();taskSelectionGeneration+=1;stopWatching();captureDraft();selected=null;historical=null;stage=0;artifactsOpen=false;restoreComposerDraft();closeTasks();remember();render();resetScroll();$('#chat-input').focus()}
    else if(action==='artifacts')setContext(!contextOpen);
    else if(action==='close-context')setContext(false);
    else if(action==='chat')switchMode('chat');
    else if(action==='assistant-toggle')setAssistantOpen(!assistantOpen);
    else if(action==='tracking-input'){setAssistantOpen(true);const input=$('#chat-input');if(!input.value.trim()){input.value='请为本报告的核心判断补充观察指标、资料来源、观察窗口和失效条件。';input.dispatchEvent(new Event('input',{bubbles:true}));}input.focus();}
    else if(action==='assistant-close')setAssistantOpen(false);
    else if(action==='scope'&&!selected&&!historical){
      closeTasks({restoreFocus:true});
      modal('研究范围与预算',scopeView());
      const entry=$('#entry');entry.value=draft.entry||'混合';entry.dispatchEvent(new Event('change',{bubbles:true}));
      restoreDraft();syncResearchMode();
    }
    else if(action==='close')$('#dialog').close();
    else if(action==='tasks')toggleTasks();
    else if(action==='archived-tasks'){showArchived=!showArchived;renderTaskList()}
    else if(action==='dismiss-tasks')closeTasks({restoreFocus:true});
    else if(['start','resume','cancel','update','retry-save','finish-draft'].includes(action))await taskAction(action);
    else if(action==='export')await exportReport();
    else if(action==='export-history')await exportReport(true);
    else if(action==='preview-chat-report')previewLocalChatReport(button);
    else if(action==='download-chat-report')await downloadChatReport(button);
    else if(action==='correct-chat-report')openChatReportCorrections(button);
    else if(action==='backup')await backupTask(button);
    else if(action==='archive-files')await openArchivedFiles();
    else if(action==='retry-archive-files'&&archiveFilePanel?.content.contains(button))await readArchivedFiles(archiveFilePanel);
    else if(action==='archive-file')await downloadArchivedFile(button);
    else if(action==='original-files')openOriginalFiles();
    else if(action==='visual-library')openVisualLibrary();
    else if(action==='retry-report'){reportReader.retry();}
    else if(action==='print')printReport();
    else if(action==='import')$('#backup-file').click();
    else if(action==='material')openTaskMaterials();
    else if(action==='attach')composerControls.choose();
    else if(action==='library')openReportLibrary();
    else if(action==='knowledge')openResearchLibrary();
    else if(action==='settings')openRuntimeSettings();
    else if(action==='logout')await logout();
    else if(action==='cases'){if($('#dialog').open)$('#dialog').close();modal('合成验收案例','<p>固定离线材料，仅验证研究工作流。</p>'+caseButtons())}
    else if(action==='versions'&&apiMode)await openReportVersions(button);
    else if(action==='latest-report')showReportEdition();
    else if(action==='report-corrections')openReportCorrections();
    else if(action==='versions')notice('当前文件保留打包的冻结版本，主动更新请在App执行');
    if((button.dataset.history||button.dataset.task)&&$('#dialog').open)$('#dialog').close();
  }catch(error){notice(error.message)}
});
document.addEventListener('input',event=>{if(event.target.id==='chat-input'){captureDraft();syncComposerAvailability();assistantAvatar.setActivity(assistantActivity(selected));}});
document.addEventListener('change',event=>{
  if(event.target.id==='research-mode')syncResearchMode();
  else if(event.target.id==='report-format')artifactExport.updated();
});
document.addEventListener('invalid',event=>{
  // Native validation runs before submit. Reveal invalid budget fields before
  // the browser attempts to focus them inside the collapsed details element.
  if(event.target.closest('#new-form')){
    const details=event.target.closest('details');if(details)details.open=true;
  }
},true);
$('#chat-input').addEventListener('compositionstart',()=>composing=true);
$('#chat-input').addEventListener('compositionend',()=>composing=false);
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229&&selected?.status!=='运行中'){event.preventDefault();sendChat()}});
window.addEventListener('researchhelper:toggle-sidebar',toggleTasks);
window.addEventListener('researchhelper:open-artifacts',()=>setContext(!contextOpen));
window.addEventListener('researchhelper:open-settings',()=>{if(workspaceReady&&!leaving){const menu=$('#account-menu');window.RHWorkspaceControls?.closeAccountMenu(menu&&!menu.hidden);openRuntimeSettings()}});
window.addEventListener('researchhelper:new-task',()=>$('.rail [data-action="new"]').click());
// Native hosts own page zoom. File/browser previews own CSS zoom and must
// compensate viewport units so the composer and settings remain on screen.
const workspaceScaleSteps=[.8,.9,1,1.1,1.25,1.4];
function applyWorkspaceScale(value){
  workspaceModeMotion.stop();
  const scale=workspaceScaleSteps.includes(Number(value))?Number(value):1,root=document.documentElement;
  const native=['macos','windows'].includes(root.dataset.nativeShell);
  root.dataset.uiScale=String(scale);
  if(native){
    root.style.removeProperty('zoom');delete root.dataset.browserScale;
    root.style.removeProperty('--rh-viewport-width');root.style.removeProperty('--rh-viewport-height');
    if(root.dataset.nativeShell==='macos'){
      root.style.setProperty('--native-titlebar-height',36/scale+'px');
      root.style.setProperty('--native-titlebar-collapsed-leading-safe-area',166/scale+'px');
    }
  }else{
    root.dataset.browserScale='true';root.style.zoom=String(scale);
    root.style.setProperty('--rh-viewport-width',innerWidth/scale+'px');root.style.setProperty('--rh-viewport-height',innerHeight/scale+'px');
    try{localStorage.setItem('researchhelper.ui-scale',String(scale))}catch{}
  }
  // State propagation must not wait for a paint: hidden WebKit views pause
  // animation frames. OH likewise publishes scale before scheduling resize.
  document.dispatchEvent(new CustomEvent('researchhelper:uiscalechange',{detail:{scale}}));
  requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));
}
window.addEventListener('researchhelper:zoom',event=>applyWorkspaceScale(event.detail?.scale));
window.addEventListener('resize',()=>{
  const root=document.documentElement;if(root.dataset.browserScale!=='true')return;
  const scale=Number(root.dataset.uiScale)||1;
  root.style.setProperty('--rh-viewport-width',innerWidth/scale+'px');root.style.setProperty('--rh-viewport-height',innerHeight/scale+'px');
});
window.addEventListener('resize',()=>{if(workspaceReady&&!leaving)renderAssistant()});
let initialWorkspaceScale=Number(document.documentElement.dataset.uiScale)||1;
if(!document.documentElement.dataset.nativeShell){try{initialWorkspaceScale=Number(localStorage.getItem('researchhelper.ui-scale'))||1}catch{}}
applyWorkspaceScale(initialWorkspaceScale);
document.addEventListener('keydown',event=>{
  if(event.defaultPrevented)return;
  if(document.querySelector('dialog[open]'))return;
  if(event.key==='Escape'&&contextOpen){event.preventDefault();setContext(false);return}
  trapContextFocus(event);if(event.defaultPrevented)return;
  if(event.key==='Escape'&&$('#tasks').classList.contains('visible')){
    event.preventDefault();closeTasks({restoreFocus:true});return;
  }
  if(event.key==='Escape'&&artifactsOpen&&assistantOpen){event.preventDefault();setAssistantOpen(false);return}
  if(event.ctrlKey&&event.altKey&&!event.metaKey&&!event.shiftKey&&event.key.toLowerCase()==='s'
      &&!event.isComposing&&!composing&&event.keyCode!==229&&!event.repeat){
    event.preventDefault();toggleTasks();
    return;
  }
  if(!event.target.matches('.views [role=tab]'))return;
  // Arrow navigation follows keyboard focus, which can differ from the
  // selected view after assistive navigation or a restored focus position.
  const focusedStage=Number(event.target.dataset.stage);
  const keys={ArrowRight:(focusedStage+1)%5,ArrowLeft:(focusedStage+4)%5,Home:0,End:4};
  if(event.key in keys){event.preventDefault();view(keys[event.key]);$('#view-'+stage).focus()}
});
compactTasks.addEventListener('change',()=>closeTasks({restoreFocus:$('#tasks').contains(document.activeElement)}));
compactContext.addEventListener('change',()=>{
  syncWorkspaceSheets();
  const panel=$('#task-context');
  if(contextOpen&&compactContext.matches&&!panel.contains(document.activeElement))focusWorkspaceControl(panel.querySelector('[data-action="close-context"]'),panel);
});
document.addEventListener('submit',async event=>{
  if(event.target.id==='document-review-form'){
    event.preventDefault();if(reviewBusy||leaving)return;
    const form=event.target,submit=form.querySelector('button[type=submit]'),feedback=$('#document-review-feedback');
    reviewBusy=true;submit.disabled=true;
    try{
      const task=await api('/api/tasks/'+form.dataset.task+'/document-review',{artifact_id:form.dataset.artifact,
        question:$('#document-review-question').value,expected_version:Number(form.dataset.version),expected_snapshot_hash:form.dataset.hash,confirmed:true});
      tasks.unshift(task);
      if(selected?.id===form.dataset.task&&form.isConnected){$('#dialog').close();setTask(task);view(0)}
      else renderTaskList();
      notice('核对任务已建立，尚未执行；原研究保持不变');
    }catch(error){feedback.textContent=error.message}finally{reviewBusy=false;submit.disabled=false}
    return;
  }
  if(event.target.id==='chat-form'){event.preventDefault();await sendChat({fromSubmit:true});return}
  if(event.target.id!=='new-form')return;event.preventDefault();if(!saveScopeSettings(event.target))return;$('#dialog').close();notice('新任务范围与预算已保存。');return;
});
function offlineArchiveTask(backup,contents){
  const title=[backup.tasks?.[0]?.question,backup.question,backup.title]
    .find(value=>typeof value==='string'&&value.trim())?.trim().slice(0,20000)||'导入的历史任务材料';
  const cutoff=new Date().toISOString();
  return {id:'rh_import_'+crypto.randomUUID(),question:title,entry:'混合',intent:'综合研究',objects:[],cutoff,version:1,batch:0,
    status:'待开始',parent:'legacy-import',fixture:null,synthetic:false,original_input:{question:title},questions:[],nodes:[],
    evidence:[],rejected_evidence:[],calculations:[],theses:[],materials:[],gaps:[],tracking:[],revisions:[],
    ledger:{limits:{data_requests:100,model_calls:8,seconds:600},used:{data_requests:0,model_calls:0,seconds:0},reserve:{model_calls:1,seconds:60},supplements:0},
    ...contents};
}
async function offlineBackupContents(wire){
  if(wire.length<=2_000_000||typeof CompressionStream!=='function')return {legacy_backup_text:wire};
  const stream=new Blob([wire]).stream().pipeThrough(new CompressionStream('gzip'));
  const encoded=encodeBase64(new Uint8Array(await new Response(stream).arrayBuffer()));
  return encoded.length<wire.length?{legacy_backup_gzip:encoded}:{legacy_backup_text:wire};
}
function saveOfflineArchive(task){
  const next=[task,...tasks];
  try{writeOfflineWorkspace(next,task.id)}
  catch{throw Error('浏览器本地存储不足，备份未导入。')}
  tasks=next;
}
$('#backup-file').addEventListener('change',async event=>{
  const file=event.target.files[0];
  if(!file)return;
  if(sending||importing||accountBusy||leaving){
    event.target.value='';notice('正在保存研究，请稍后导入备份');return;
  }
  importing=true;
  try{
    const head=new Uint8Array(await file.slice(0,4).arrayBuffer());
    const packageFile=head[0]===0x50&&head[1]===0x4b&&head[2]===3&&head[3]===4;
    if(packageFile){
      if(!apiMode)throw Error('请在ResearchHelper App导入ZIP备份。');
      if(file.size>taskPackageLimit)throw Error('任务ZIP备份超过128MiB。');
      const task=await api('/api/import?file=zip',file);
      if(!isBackupArchive(task)||!taskSnapshotMatches(task,task.id)||!task.backup_package)
        throw Error('未确认归档结果，请重新读取任务列表。');
      tasks.unshift(task);setTask(task);notice('任务备份已归档');
      return;
    }
    if(file.size>taskBackupLimit)throw Error('任务备份超过20MB。');
    let wire,backup;
    try{wire=decodeBackup(await file.arrayBuffer());backup=JSON.parse(wire.replace(/^\uFEFF/,''))}
    catch{throw Error('请选择UTF-8编码的JSON备份。')}
    if(!backup||typeof backup!=='object')throw Error('任务备份格式无效');
    let task;
    if(apiMode){
      task=await api('/api/import?file=1',wire);
      if(!isBackupArchive(task)||!taskSnapshotMatches(task,task.id))throw Error('未确认归档结果，请重新读取任务列表。');
      tasks.unshift(task);
    }else{
      task=offlineArchiveTask(backup,await offlineBackupContents(wire));
      if(leaving)return;
      saveOfflineArchive(task);
    }
    setTask(task);notice('任务备份已归档');
  }catch(error){notice(error.message)}
  finally{importing=false;event.target.value=''}
});
$('#material-file').multiple=true;
$('#material-file').addEventListener('change',async event=>{const files=[...event.target.files];event.target.value='';if(!files.length)return;try{await queueMaterials(files)}catch(error){notice(error.message)}});
async function init(){
  if(initializing||workspaceReady||leaving)return;
  initializing=true;
  const shell=$('[data-workspace-shell]');
  document.body.classList.add('workspace-body--initializing');
  shell.dataset.initializing='true';shell.setAttribute('aria-busy','true');
  const send=$('#chat-form button[type=submit]'),retry=$('[data-action="reconnect"]');
  send.disabled=true;if(retry){retry.disabled=true;retry.textContent='正在连接'}
  try{
  window.RHChoice.enhanceSelects(document);
  closeTasks();
  boot=apiMode?await api('/api/bootstrap'):window.RH_DEMO;
  if(!boot)throw Error('发布数据尚未生成，请运行主项目发布流程');
  token=boot.token||'';tasks=boot.tasks||[];mergePresentations(boot.task_presentations);
  if(apiMode)draftKey=boot.authentication?'researchhelper-input-draft-v2:'+boot.account:'researchhelper-input-draft-v1';
  if(boot.authentication){
    window.RHWorkspaceControls.setAccount(boot.account,true);
    $('[data-workspace-shell]').dataset.role=boot.role;
    try{localStorage.setItem(authEventKey,JSON.stringify({account:boot.account,nonce:Date.now()}))}catch{}
  }
  if(!apiMode){
    try{
      const raw=localStorage.getItem(storageKey),saved=JSON.parse(raw);
      if(saved?.workspaceVersion===4&&Array.isArray(saved.tasks)){
        const initial=new Map(tasks.map(t=>[t.id,t]));
        saved.tasks.forEach(t=>{if(t?.id&&Array.isArray(t.questions)&&t.ledger)initial.set(t.id,restoreOfflineTask(t,boot.tasks||[]))});
        tasks=[...initial.values()];selected=tasks.find(t=>t.id===saved.selected);
        draft=saved.draft||{};stage=Math.min(4,Math.max(0,Number(saved.stage)||0));
      }else if(saved&&typeof saved==='object'){
        const task=offlineArchiveTask(saved,await offlineBackupContents(raw));
        saveOfflineArchive(task);selected=task;
      }
    }catch{notice('旧本地记录未覆盖，请先导出备份')}
  }else loadWorkspaceDraft(boot.workspace_draft);
  if(!apiMode){try{mergePresentations(JSON.parse(localStorage.getItem(storageKey))?.taskPresentations)}catch{}}
  if(apiMode&&draft.activeTaskId)selected=tasks.find(t=>t.id===draft.activeTaskId)||null;
  // Migrate the old single draft only into its original active task, not every task.
  if(!draft.composerTexts||typeof draft.composerTexts!=='object'||Array.isArray(draft.composerTexts))draft.composerTexts={[composerKey()]:typeof draft.chat==='string'?draft.chat:''};
  if(apiMode&&selected?.workspace_summary_version===1){
    const summary=selected;selected=null;
    try{
      if(!summary.archive_unreadable){
        const fresh=await api('/api/tasks/'+encodeURIComponent(summary.id));
        if(!taskSnapshotMatches(fresh,summary.id)||taskSnapshotIsOlder(fresh,summary))throw Error('任务响应已变化，请重新选择');
        selected=fresh;tasks=tasks.map(task=>task.id===fresh.id?fresh:task);
      }
    }catch(error){
      if(leaving)return;
      if(error.failureCode==='archive_record_unavailable')markArchiveUnavailable(summary.id);
      selected=null;notice('上次任务未恢复：'+error.message);
    }
  }
  if(selected){
    const restoring=selected;
    await restoreRememberedReport(restoring,()=>!leaving&&selected===restoring);
    if(leaving)return;
  }
  // Input arriving while reconnecting belongs to a new, unsent question.
  const retained={text:$('#chat-input').value,materials:queuedMaterials};
  artifactsOpen=canUseDesk()&&draft.workspaceMode==='desk';
  if(apiMode)assistantOpen=draft.assistantOpen===true;
  if(artifactsOpen)stage=savedDeskStage();
  if(typeof draft.activeHistoryKey==='string'&&Object.hasOwn(boot.history||{},draft.activeHistoryKey)){
    historical={...boot.history[draft.activeHistoryKey],key:draft.activeHistoryKey};
    if(canUseDesk()&&draft.workspaceMode!=='chat')artifactsOpen=true;
    stage=savedDeskStage();
  }
  if(retained.text||retained.materials.length){
    // Input typed before account bootstrap belongs to a new, unsent question,
    // not to a previously selected task restored from that account's storage.
    selected=null;historical=null;draft.composerTexts.new=retained.text;
    artifactsOpen=false;stage=0;
    composerMemory.set('new',{materials:retained.materials,pending:null});
  }
  restoreComposerDraft();render();watch();workspaceReady=true;remember();
  if(Number.isSafeInteger(boot.archive_issue_count)&&boot.archive_issue_count>0)notice('有'+boot.archive_issue_count+'项研究档案无法读取。');
  try{window.webkit?.messageHandlers?.researchhelper?.postMessage({action:'workspace-state',ready:true,deliveryAvailable:canOpenTaskContext()})}catch{}
  if(retry)$('#chat-input').focus();
  }catch(error){
    if(!leaving)$('#chat').innerHTML='<div class="empty"><h2>工作台未连接</h2><p>'+esc(error.message)+'</p><button class="button-secondary" type="button" data-action="reconnect">重新连接</button></div>';
  }finally{
    initializing=false;syncComposerAvailability();
    document.body.classList.remove('workspace-body--initializing');
    shell.dataset.initializing='false';shell.setAttribute('aria-busy','false');
  }
}
window.addEventListener('storage',event=>{if(event.key!==authEventKey||!boot.authentication||leaving)return;try{const identity=JSON.parse(event.newValue);if(identity?.account!==boot.account)leaveWorkspace()}catch{}});
window.addEventListener('pageshow',event=>{if(event.persisted&&apiMode)location.reload()});
// One owner guarantees save-before-dispose. Independent pagehide listeners
// can otherwise restore scrollbar gutters before reading geometry is saved.
window.addEventListener('pagehide',event=>{
  if(event.persisted)workspaceModeMotion.stop();
  else workspaceModeMotion.destroy();
  artifactExport.cancelPreparation();
  cancelTaskSelection();
  if(workspaceReady&&!leaving)void flushWorkspaceDraft();
  if(!event.persisted)deskReader.destroy();
  if(!event.persisted)taskSearch.destroy();
  if(!event.persisted)userGuide.destroy();
  if(!event.persisted)messageActions.destroy();
  if(!event.persisted)researchProcess.destroy();
  if(!event.persisted)deskInteractions.destroy();
  if(!event.persisted)reportReader.destroy();
  if(!event.persisted)assistantAvatar.destroy();
  if(!event.persisted)window.RHScrollbar?.installScrollbarActivity().teardown();
});
window.addEventListener('resize',()=>{if(boot.version&&!leaving)renderAssistant()});
init();
