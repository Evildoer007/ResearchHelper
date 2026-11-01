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
const apiMode=location.protocol!=='file:';
// The local module owns theme subscriptions. File previews retain the bootstrap
// fallback because browsers do not permit file:// module imports.
if(apiMode)import('./login/theme.js').catch(()=>{});
// Use the same boundary for drawer behavior, resize recovery and workspace CSS.
const compactTasks=matchMedia('(max-width:700px)');
const compactContext=matchMedia('(max-width:1100px)');
let boot={},tasks=[],selected=null,stage=0,historical=null,token='',poll=null,previousFocus=null,draft={};
let artifactsOpen=false,sending=false,composing=false,contextOpen=false;
let pollGeneration=0,pollController=null,pollDeadline=null;
// Returning to an identical task is a new selection, not renewed authorization
// for an operation that was waiting when the user left it.
let taskSelectionGeneration=0;
let selectingTaskId='';
// Version reads belong to a task selection and reading surface, not whichever
// task happens to be selected when the server eventually replies.
let reportVersionRead=null,modalGeneration=0;
const taskTransition=window.RHConversationTransition.createConversationTransition($('#chat'));
// Original files stay in memory until sent. They are never written to browser storage.
let queuedMaterials=[],pendingSubmission=null;
// Task-local File objects stay in this window; only input text is persisted.
const composerMemory=new Map();
// Bind each mutation to the task chosen before asynchronous configuration checks.
const pendingTaskActions=new Set();
const researchProcess=window.RHResearchProcess.create();
const deskInteractions=window.RHDeskInteractions.create($('#artifacts'));
const reportReader=window.RHReportReader.create($('#content'));
let assistantOpen=false;
const assistantAvatar=window.RHDeskAvatar.createBloubAvatar();
$('[data-action="assistant-toggle"]').replaceChildren(assistantAvatar.element);
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
const authEventKey='researchhelper-auth-event-v1';
const materialLimits={files:10,bytes:50*1024*1024,individual:25*1024*1024};
const labels=['研究概览','数据来源','研究底稿','研究报告','跟踪复核'];
function saveReadingPosition(field,key,position){
  const previous=draft[field];
  const entries=previous&&typeof previous==='object'&&!Array.isArray(previous)?Object.entries(previous):[];
  draft[field]=Object.fromEntries([...entries.filter(([name])=>name!==key).slice(-99),[key,position]]);
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
  return selected?JSON.stringify(['task',selected.id,selected.version,stage]):null;
}
function deskStateKey(){return historical?'history:'+historical.key:composerKey()}
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
  entries:query=>taskCatalog(query).map(row=>({kind:row.kind,id:row.id,title:row.title})),
  select:row=>selectTaskEntry(row.kind,row.id,{catalogOnly:true}),
  canOpen:()=>workspaceReady&&!leaving,
  canSelect:()=>!leaving&&!sending&&!importing&&!accountBusy,
  beforeOpen(){window.RHTaskMenu?.close();window.RHWorkspaceControls?.closeAccountMenu();setContext(false,{restoreFocus:false})},
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
const userGuide=window.RHUserGuide.createUserGuide({
  trigger:$('[data-user-guide]'),canOpen:()=>workspaceReady&&!leaving,
  beforeOpen(){window.RHTaskMenu?.close();window.RHWorkspaceControls?.closeAccountMenu();setContext(false,{restoreFocus:false})},
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
const messageActions=window.RHMessageActions.createMessageActions({
  canRun:()=>workspaceReady&&!leaving,
  restoreFocus:origin=>focusWorkspaceControl(origin,$('#chat-input')),
});
function notice(message){$('#notice').textContent=message;$('#notice').classList.add('show');clearTimeout(notice.timer);notice.timer=setTimeout(()=>$('#notice').classList.remove('show'),5500)}
function loginURL(nextAccount){const query=new URLSearchParams(location.search);query.delete('account');if(nextAccount)query.set('account',nextAccount);return '/login/index.html'+(query.size?'?'+query:'')}
function leaveWorkspace(nextAccount){
  if(leaving)return;
  if(draftKey)captureDraft();
  cancelTaskSelection();
  leaving=true;taskSelectionGeneration+=1;stopWatching();
  window.RHWorkspaceControls?.closeAccountMenu();
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
  tasks=[];selected=null;historical=null;queuedMaterials=[];pendingSubmission=null;composerMemory.clear();draft={};
  $('#chat-input').value='';$('#dialog-content').replaceChildren();$('#dialog').close();
  $('#chat').replaceChildren();$('#content').replaceChildren();$('#task-list').replaceChildren();
  $('[data-workspace-shell]').inert=true;$('[data-workspace-shell]').style.visibility='hidden';
  location.replace(loginURL(nextAccount));
}
async function api(path,body,{workspaceView=true}={}){
  if(leaving)throw Error('登录状态已改变，请重新登录');
  // Bound read-only requests; never abort/replay a write with unknown outcome.
  const controller=body?null:new AbortController();
  const deadline=controller?setTimeout(()=>controller.abort(),10000):null;
  try{
    const headers={...(body?{'Content-Type':'application/json','X-ResearchHelper-Token':token}:{}),...(workspaceView?{'X-ResearchHelper-View':'workspace-v1'}:{})};
    const response=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller?.signal,headers,body:body?JSON.stringify(body):undefined});
    if(response.status===401){leaveWorkspace();throw Error('登录已失效，草稿保留在原账户')}
    let value;
    try{value=await response.json()}catch(error){if(error.name==='AbortError')throw error;throw Error(body?'本地响应无法读取，请先检查任务状态，不要重复提交。':'本地响应无法读取，请重新连接。')}
    if(!response.ok)throw Error(value.error||'本地请求失败');
    if(leaving)throw Error('登录状态已改变，请重新登录');
    return value;
  }catch(error){
    if(error.name==='AbortError')throw Error('本地服务读取超时，请重新连接。');
    throw error;
  }finally{if(deadline!==null)clearTimeout(deadline)}
}
function composerKey(){return selected?.id||'new'}
function saveComposerDraft(){
  if(leaving)return;
  composerControls.sync();
  conversationReader.capture();
  deskReader.capture();
  if(!draft.composerTexts||typeof draft.composerTexts!=='object'||Array.isArray(draft.composerTexts))draft.composerTexts={};
  draft.chat=$('#chat-input').value;draft.composerTexts[composerKey()]=draft.chat;
  composerMemory.set(composerKey(),{materials:queuedMaterials,pending:pendingSubmission});
}
function restoreComposerDraft(){
  const text=draft.composerTexts?.[composerKey()],memory=composerMemory.get(composerKey());
  $('#chat-input').value=typeof text==='string'?text:'';draft.chat=$('#chat-input').value;
  queuedMaterials=memory?.materials||[];pendingSubmission=memory?.pending||null;
  renderQueuedMaterials();
  composerControls.sync();
}
// Packaged case papers are readonly resources, not user draft data.
// Persist their references so eight offline cases cannot exhaust localStorage.
function offlineTasksForStorage(){
  const templates=new Map((boot.tasks||[]).map(task=>[task.fixture,task]));
  return tasks.map(task=>{
    const template=task.synthetic&&templates.get(task.fixture);
    if(!template||task.report_html!==template.report_html||task.report_detailed_html!==template.report_detailed_html)return task;
    const {report_html,report_detailed_html,...record}=task;
    return {...record,demo_report_ref:task.fixture};
  });
}
function restoreOfflineTask(task,templates){
  if(!task.demo_report_ref)return task;
  const template=templates.find(item=>item.synthetic&&item.fixture===task.demo_report_ref);
  if(!template)return task;
  return {...task,report_html:template.report_html,report_detailed_html:template.report_detailed_html};
}
function remember(){if(leaving)return false;try{saveComposerDraft();draft.activeTaskId=selected?.id||null;draft.activeHistoryKey=historical?.key||null;draft.workspaceMode=artifactsOpen?'desk':'chat';if(!apiMode)localStorage.setItem(storageKey,JSON.stringify({workspaceVersion:4,tasks:offlineTasksForStorage(),selected:selected?.id,stage,draft,taskPresentations}));else if(draftKey)localStorage.setItem(draftKey,JSON.stringify(draft));return true;}catch{notice('本地保存失败，请导出备份');return false}}
// Chat drafts remain autosaved. Research settings are an explicit transaction:
// closing a sheet, changing mode or pagehide cannot adopt its unsaved inputs.
function captureDraft(){draft.chat=$('#chat-input').value;remember()}
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
  if(taskSheet){rail.setAttribute('role','dialog');rail.setAttribute('aria-modal','true')}
  else{rail.removeAttribute('role');rail.removeAttribute('aria-modal')}
  $('#context-scrim').hidden=!modal;
  document.documentElement.classList.toggle('workspace-overlay-open',modal||taskSheet);
}
function closeTasks(){
  window.RHTaskMenu?.close();
  $('.tasks').classList.remove('visible');$('.task-scrim').hidden=true;
  syncWorkspaceSheets();window.RHWorkspaceControls.syncRail();
}
function openTasks(){
  if(leaving||document.querySelector('dialog[open]'))return;
  setContext(false,{restoreFocus:false});
  if(compactTasks.matches){$('.tasks').classList.add('visible');$('.task-scrim').hidden=false;syncWorkspaceSheets();window.RHWorkspaceControls.syncRail()}
  else window.RHWorkspaceControls.setRailCollapsed(false);
  $('.task-search').hidden=false;$('#task-search').focus();
}
function focusWorkspaceControl(preferred,fallback){
  const target=[preferred,fallback,artifactsOpen?$('#view-'+stage):null,$('#workspace')].find(element=>element?.isConnected&&!element.closest('[inert], [hidden]')&&!element.matches(':disabled')&&element.getClientRects().length);
  target?.focus({preventScroll:true});
}
function setContext(open,{restoreFocus=true}={}){
  if(leaving||open&&document.querySelector('dialog[open]'))return;
  const wasOpen=contextOpen,next=Boolean(open),panel=$('#task-context'),toggle=$('.web-control[data-action="artifacts"]');
  if(next&&!wasOpen){contextReturnFocus=document.activeElement;closeTasks();window.RHWorkspaceControls.closeAccountMenu()}
  contextOpen=next;$('[data-workspace-shell]').dataset.reportOpen=String(next);
  panel.hidden=!next;panel.setAttribute('aria-hidden',String(!next));
  toggle.setAttribute('aria-expanded',String(next));
  toggle.setAttribute('aria-label',next?'收起任务与交付':'打开任务与交付');toggle.title=toggle.getAttribute('aria-label');
  syncWorkspaceSheets();
  if(next&&!wasOpen)panel.querySelector('[data-action="close-context"]').focus({preventScroll:true});
  else if(wasOpen&&!next){
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
function toggleTasks(){if(compactTasks.matches){if($('.tasks').classList.contains('visible'))closeTasks();else openTasks()}else window.RHWorkspaceControls.setRailCollapsed($('[data-workspace-shell]').dataset.railCollapsed!=='true')}
function resetScroll(){ $('#workspace').scrollTop=0 }
function cancelTaskSelection(){
  taskTransition.cancel();
  if(!selectingTaskId)return;
  selectingTaskId='';taskSelectionGeneration+=1;
  $('#chat-form').inert=false;$('#chat').removeAttribute('aria-busy');
}
function setTask(task,{submitted=false}={}){cancelTaskSelection();taskSelectionGeneration+=1;saveComposerDraft();selected=task;historical=null;restoreComposerDraft();if(artifactsOpen)stage=savedDeskStage();closeTasks();remember();render();resetScroll();if(submitted)conversationReader.beginTurn();watch()}
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
async function loadSelectedTask(id){
  cancelTaskSelection();captureDraft();stopWatching();
  const generation=++taskSelectionGeneration,origin=document.activeElement;
  const current=()=>!leaving&&generation===taskSelectionGeneration&&selectingTaskId===id;
  const animate=!artifactsOpen&&!historical&&selected?.id&&selected.id!==id;
  selectingTaskId=id;$('#chat').setAttribute('aria-busy','true');$('#chat-form').inert=true;
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
    const task=taskSnapshotIsOlder(fresh,cached)?cached:fresh;
    tasks=tasks.map(item=>item.id===id?task:item);
    setTask(task);
    if(animate)taskTransition.enter();
    focusWorkspaceControl(origin,$('#chat-input'));
  }catch(error){if(current())notice(error.message)}
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
function watch(){
  stopWatching();
  if(!apiMode||leaving||historical||selected?.status!=='运行中')return;
  const id=selected.id,generation=pollGeneration;
  let failures=0;
  const current=()=>!leaving&&!historical&&generation===pollGeneration&&selected?.id===id&&selected.status==='运行中';
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
        if(response.status!==408&&response.status!==429&&response.status<500){notice('任务状态读取失败，请重新选择任务。');continueReading=false;return}
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
function view(index){cancelTaskSelection();const fromContext=contextOpen,origin=document.activeElement;captureDraft();stage=Number(index);rememberDeskStage();artifactsOpen=true;setContext(false,{restoreFocus:false});remember();render();resetScroll();watch();focusWorkspaceControl(fromContext?null:origin,$('#view-'+stage))}
function switchMode(mode){cancelTaskSelection();const origin=document.activeElement;captureDraft();if(artifactsOpen)rememberDeskStage();artifactsOpen=mode==='desk';if(artifactsOpen)stage=savedDeskStage();setContext(false);closeTasks();remember();render();resetScroll();watch();if(!artifactsOpen&&!historical)$('#chat-input').focus();else focusWorkspaceControl(origin,$('#view-'+stage))}
function renderAssistant(){
  conversationReader.enter(conversationReadingKey());
  const desk=artifactsOpen&&!historical,compact=matchMedia('(max-width:700px)').matches;
  const region=$('.composer-dock'),panel=$('#assistant-panel'),toggle=$('[data-action="assistant-toggle"]');
  region.hidden=!!historical;region.classList.toggle('is-open',desk&&assistantOpen);
  assistantAvatar.setActivity(assistantActivity(selected));
  assistantAvatar.setActive(desk&&!compact&&!assistantOpen);
  toggle.hidden=!desk;toggle.setAttribute('aria-expanded',String(desk&&assistantOpen));
  panel.inert=!!historical||desk&&!assistantOpen;
  panel.setAttribute('aria-hidden',String(panel.inert||!!historical));
  if(desk){panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','false')}else{panel.removeAttribute('role');panel.removeAttribute('aria-modal')}
  $('[data-assistant-title]').textContent=selected?taskTitle(selected):'ResChat';
  const target=desk?$('#assistant-transcript'):$('#chat-scroll-stage');
  if($('#chat').parentElement!==target)target.append($('#chat'));
  window.RHWorkspaceControls?.syncAssistant();
  composerControls.sync();
  conversationReader.updated();
}
function setAssistantOpen(open){assistantOpen=!!open;captureDraft();renderAssistant();if(assistantOpen)$('#chat-input').focus();else $('[data-action="assistant-toggle"]').focus()}
function statusTag(value){const tone=['成功','已完成','支持','已保存'].includes(value)?'verified':['失败','已取消','反驳','部分完成'].includes(value)?'attention':value==='运行中'?'running':'neutral';return '<span class="status" data-tone="'+tone+'">'+esc(value)+'</span>'}
function taskCatalog(query=''){
  const latest=new Map();tasks.forEach(t=>{const key=t.thread_id||t.id,prior=latest.get(key);if(!prior||(t.messages?.length||0)>(prior.messages?.length||0)||((t.messages?.length||0)===(prior.messages?.length||0)&&String(t.created_at||'')>String(prior.created_at||'')))latest.set(key,t)});
  const filtered=[...latest.values()].filter(t=>taskPresentation(t).archived===showArchived&&(taskTitle(t)+' '+t.status+' '+t.cutoff).toLocaleLowerCase().includes(query)).sort((a,b)=>Number(taskPresentation(b).pinned)-Number(taskPresentation(a).pinned));
  const history=showArchived?[]:Object.entries(boot.history||{}).filter(([,item])=>((item.shortTitle||item.title)+' '+(item.market||'')).toLocaleLowerCase().includes(query));
  return [...filtered.map(task=>({kind:'task',id:task.id,title:taskTitle(task),task})),...history.map(([key,item])=>({kind:'history',id:key,title:item.shortTitle||item.title,item}))];
}
function selectTaskEntry(kind,id,{catalogOnly=false}={}){
  if(leaving||sending||importing||accountBusy){notice('正在保存研究，请稍候');return false}
  // Search resolves the current visible catalog; explicit report/review links
  // may intentionally return to an older round of the same research thread.
  const task=kind==='task'&&!catalogOnly?tasks.find(item=>item.id===id):null;
  const entry=task?{kind,id,task}:taskCatalog().find(row=>row.kind===kind&&row.id===id);
  if(!entry)return false;
  if($('#dialog').open)$('#dialog').close();
  if(kind==='task'&&apiMode)void loadSelectedTask(entry.id);
  else if(kind==='task')setTask(entry.task);
  else{
    cancelTaskSelection();
    taskSelectionGeneration+=1;captureDraft();stopWatching();closeTasks();
    historical={...entry.item,key:entry.id};stage=3;artifactsOpen=true;setContext(false);rememberDeskStage();remember();render();resetScroll();focusWorkspaceControl(null,$('#view-3'));
  }
  return true;
}
function renderTaskList(){
  const query=$('#task-search').value.trim().toLocaleLowerCase(),catalog=taskCatalog(query);
  const filtered=catalog.filter(row=>row.kind==='task').map(row=>row.task),history=catalog.filter(row=>row.kind==='history').map(row=>[row.id,row.item]);
  const list=$('#task-list');
  const snapshot=JSON.stringify([query,showArchived,filtered.map(t=>[t.id,taskTitle(t),t.cutoff,t.status==='运行中',taskPresentation(t),pendingPresentationActions.has(t.thread_id||t.id)]),history.map(([key,item])=>[key,item.shortTitle||item.title,item.market])]);
  if(taskListSnapshot!==snapshot){
    const focused=list.contains(document.activeElement)?{task:document.activeElement.dataset.task,history:document.activeElement.dataset.history,menu:document.activeElement.dataset.menuTask,search:document.activeElement.classList.contains('rail-task-search')}:null;
    const scroll=list.scrollTop;
    const row=(title,attributes,date,state='idle')=>'<div class="task-list__row"><button class="task-item" '+attributes+'><strong class="task-item__title"><span class="task-item__running-dot" data-state="'+state+'" role="img" aria-label="'+(state==='running'?'正在运行':'空闲')+'"></span><span class="task-item__text">'+esc(title)+'</span></strong><small class="task-item__date">'+esc(date)+'</small></button></div>';
    window.RHTaskMenu.close();
    list.innerHTML='<p class="rail-section-label">'+(showArchived?'归档任务':'研究任务')+'</p>'+filtered.map(t=>row(taskTitle(t),'data-task="'+esc(t.id)+'"',String(t.cutoff||'').slice(5,10),t.status==='运行中'?'running':'idle')).join('')+(history.length?'<p class="rail-section-label">历史报告</p>'+history.map(([key,item])=>row(item.shortTitle||item.title,'data-history="'+esc(key)+'"',item.market||'权益')).join(''):'')+(!filtered.length&&!history.length?'<p class="task-empty">'+(query?'没有匹配的任务或报告':showArchived?'暂无归档任务':'尚无研究任务')+'</p>':'');
    taskSearch.mount(list.querySelector('.rail-section-label'));
    window.RHTaskMenu.mount(list,id=>taskPresentation(tasks.find(t=>t.id===id)),taskMenuAction,id=>pendingPresentationActions.has(tasks.find(t=>t.id===id)?.thread_id||id));
    taskListSnapshot=snapshot;list.scrollTop=scroll;
    if(focused){
      const replacement=focused.search?list.querySelector('.rail-task-search'):focused.menu?[...list.querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===focused.menu):[...list.querySelectorAll('.task-item')].find(button=>focused.task?button.dataset.task===focused.task:button.dataset.history===focused.history);
      focusWorkspaceControl(replacement,$('#task-search'));
    }
  }
  list.querySelectorAll('.task-item').forEach(button=>button.setAttribute('aria-current',String(button.dataset.task?selected?.id===button.dataset.task&&!historical:historical?.key===button.dataset.history)));
  $('[data-action="archived-tasks"]').lastChild.textContent=showArchived?'返回研究任务':'归档任务';
  taskSearch.refresh();
}
function taskPresentation(task){return taskPresentations[task?.thread_id||task?.id]||{title:'',pinned:false,archived:false,revision:0}}
function taskTitle(task){return taskPresentation(task).title||(task.fixture?boot.cases?.find(c=>c.id===task.fixture)?.title||task.question:task.title||task.draft_report?.title||task.question)}
function mergePresentations(values){
  if(!values||typeof values!=='object'||Array.isArray(values))return;
  for(const task of tasks){
    const key=task.thread_id||task.id,value=Object.hasOwn(values,key)?values[key]:null;
    if(!value||typeof value!=='object'||typeof value.title!=='string'||value.title.length>120||typeof value.pinned!=='boolean'||typeof value.archived!=='boolean'||!Number.isInteger(value.revision)||value.revision<0)continue;
    if(value.revision>=(taskPresentations[key]?.revision||0))taskPresentations[key]={title:value.title,pinned:value.pinned,archived:value.archived,revision:value.revision};
  }
}
async function changeTaskPresentation(task,changes,expected=taskPresentation(task).revision){
  const key=task.thread_id||task.id,account=boot.account;
  if(leaving||pendingPresentationActions.has(key))throw Error('任务操作正在保存，请稍后再试');
  pendingPresentationActions.add(key);renderTaskList();
  const pendingFocus=document.activeElement,selection=taskSelectionGeneration;
  try{
    let value;
    if(apiMode)value=await api('/api/tasks/'+task.id+'/presentation',{expected_revision:expected,changes});
    else{
      if(expected!==taskPresentation(task).revision)throw Error('任务展示设置已更新，请重新读取后修改');
      if(changes.archived&&tasks.some(t=>(t.thread_id||t.id)===key&&t.status==='运行中'))throw Error('研究仍在运行，请先取消或等待完成后归档');
      value={...taskPresentation(task),...changes,revision:expected+1};
      try{
        saveComposerDraft();draft.activeTaskId=selected?.id||null;
        localStorage.setItem(storageKey,JSON.stringify({workspaceVersion:4,tasks,selected:selected?.id,stage,draft,taskPresentations:{...taskPresentations,[key]:value}}));
      }catch{throw Error('本地保存失败，展示设置未更新。请导出备份后重试')}
    }
    if(!leaving&&account===boot.account){mergePresentations({[key]:value});if(apiMode)remember();render()}
  }catch(error){
    // An unknown write outcome is reconciled with a read, never auto-replayed.
    if(apiMode&&!leaving&&account===boot.account){try{const value=await api('/api/tasks/'+task.id+'/presentation');mergePresentations({[key]:value})}catch{}}
    throw error;
  }finally{
    pendingPresentationActions.delete(key);
    if(!leaving){
      const restore=document.activeElement===pendingFocus&&selection===taskSelectionGeneration&&!document.querySelector('dialog[open]');
      renderTaskList();
      if(restore)focusWorkspaceControl([...$('#task-list').querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===task.id),$('[data-action="archived-tasks"]'));
    }
  }
}
async function taskMenuAction(id,action){
  const task=tasks.find(t=>t.id===id);if(!task||leaving)return;
  if(action==='rename'){
    const expected=taskPresentation(task).revision;
    modal('重命名任务','<form id="task-rename-form"><label for="task-name">任务名称</label><input id="task-name" maxlength="120" required value="'+esc(taskTitle(task))+'"><p id="task-name-feedback" role="status"></p><div class="form-actions"><button class="button-primary" type="submit">保存</button><button class="button-secondary" type="button" data-action="close">取消</button></div></form>');
    const form=$('#task-rename-form'),input=$('#task-name');input.focus();input.select();
    form.addEventListener('submit',async event=>{
      event.preventDefault();const title=input.value.trim(),button=form.querySelector('[type=submit]');
      if(!title||title.length>120||/[\x00-\x1f\x7f]/.test(title)){$('#task-name-feedback').textContent='名称须为1至120个字符，不能包含换行或控制字符';return}
      if(button.disabled)return;button.disabled=true;
      try{
        await changeTaskPresentation(task,{title},expected);
        if(form.isConnected&&$('#dialog').open){previousFocus=[...$('#task-list').querySelectorAll('[data-menu-task]')].find(button=>button.dataset.menuTask===task.id);$('#dialog').close()}
        notice('任务名称已保存');
      }
      catch(error){if(form.isConnected)$('#task-name-feedback').textContent=error.message}
      finally{button.disabled=false}
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
  const pane=$('#content'),key=JSON.stringify(historical?['history',historical.key,stage]:['task',selected?.id||'new',selected?.version||0,stage]);
  if(pane.dataset.pane===key&&pane.renderedHTML===html){deskReader.updated();deskInteractions.updated();return}
  const same=pane.dataset.pane===key;
  const disclosures=same?uniquePaneElements(pane.querySelectorAll('details'),pane):new Map();
  const states=new Map([...disclosures].filter(([,element])=>element).map(([id,element])=>[id,element.open]));
  const focused=same&&pane.contains(document.activeElement)&&document.activeElement!==pane?document.activeElement:null;
  const focusKey=focused?paneReadingIdentity(focused,pane):null;
  pane.dataset.pane=key;
  pane.renderedHTML=html;
  reconcilePaneChildren(pane,html,same);
  window.RHChoice.enhanceSelects(pane);
  for(const [id,element] of uniquePaneElements(pane.querySelectorAll('details'),pane)){
    if(element&&states.has(id))element.open=states.get(id);
  }
  if(focused&&!focused.isConnected){
    const controls=uniquePaneElements(pane.querySelectorAll('summary,button,a,[tabindex]'),pane);
    focusWorkspaceControl(controls.get(focusKey),pane);
  }
  installReader();
  deskReader.updated();
  deskInteractions.updated();
}
function contextSummary(task){
  const row=(label,value)=>'<div><dt>'+label+'</dt><dd>'+esc(value)+'</dd></div>';
  if(historical)return '<dl class="context-facts">'+row('内容类型','历史报告')+row('报告日期',historical.date||'未记录')+'</dl>';
  if(!task)return '<p class="context-empty">选择研究任务后查看详情。</p>';
  const objects=(task.objects||[]).map(String).join('、');
  return '<dl class="context-facts">'+row('研究状态',(task.status||'未登记')+(Number.isSafeInteger(task.version)?'，版本'+task.version:''))+(objects?row('研究对象',objects):'')+(task.cutoff?row('截止时点',cutoffLabel(task.cutoff)):'')+row('已保存报告',task.delivery?'版本'+task.delivery.version:'尚未保存')+(task.synthetic?row('案例类型','合成案例'):'')+'</dl>';
}
function render(){
  conversationReader.enter(conversationReadingKey());
  deskReader.enter(deskReadingKey());
  const active=selected;
  renderQueuedMaterials();
  syncComposerAvailability();
  $('[data-workspace-shell]').dataset.mode=artifactsOpen?'desk':'chat';
  $('[data-mode-switch]').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String((button.dataset.mode==='desk')===artifactsOpen)));
  $('#artifacts').hidden=!artifactsOpen;$('#chat').hidden=false;
  $('#chat-surface').hidden=artifactsOpen;
  renderAssistant();
  renderChat();
  $('#task-title').textContent=historical?historical.shortTitle||historical.title:active?taskTitle(active):'新的研究';
  $('#task-meta').innerHTML=historical?'<span>历史报告</span>':active?'<time>'+esc(active.cutoff.slice(0,10))+'</time>'+statusTag(active.status)+'<span>版本'+active.version+'</span>'+(active.synthetic?'<span class="case-tag">合成验收</span>':''):'';
  const operationList=$('#task-actions'),operationHTML=active&&!historical?actions(active):'';
  if(operationList.renderedHTML!==operationHTML){operationList.innerHTML=operationHTML;operationList.renderedHTML=operationHTML}
  $('#context-operations').hidden=!operationHTML;
  $('#context-summary').innerHTML=contextSummary(active);
  $('.views').querySelectorAll('button').forEach((button,i)=>{button.setAttribute('aria-selected',String(i===stage));button.tabIndex=i===stage?0:-1});
  window.RHWorkspaceControls.syncViewIndicator();
  $('#content').setAttribute('aria-labelledby','view-'+stage);renderTaskList();
  if(historical){renderPane(historyView());return}
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
function renderChat(){
  const chat=$('#chat'),task=selected;
  if(!task||historical)researchProcess.clear();
  if(historical){chat.renderedKey=null;chat.renderedHTML=null;$('#chat-surface').classList.remove('chat-surface--empty');chat.innerHTML=messageHTML('assistant','<h2>'+esc(historical.title)+'</h2><p>'+esc(historical.conclusion)+'</p><p>历史材料，不代表当前判断。</p><button class="button-secondary" data-stage="3">阅读完整历史报告</button>');return}
  $('#chat-surface').classList.toggle('chat-surface--empty',!task);
  if(!task){chat.renderedKey=null;chat.renderedHTML=null;chat.innerHTML='<section class="conversation-start chat-welcome"><div><div class="conversation-start__mark" aria-hidden="true"><img src="app-icon.svg" alt=""></div><h2>今天想研究什么？</h2><div class="conversation-starters">'+[['研究标的','分析公司的业务、盈利与投资逻辑。'],['研究行业','梳理产业关系与竞争格局。'],['分析事件','核验事件事实与影响路径。'],['验证判断','结合证据与反证检验研究论点。']].map(([title,copy])=>'<button class="conversation-starter" type="button" data-starter="'+esc(copy)+'"><strong>'+title+'</strong></button>').join('')+'</div>'+researchSetupView()+'</div></section>';return}
  const nodes=task.nodes||[],theses=task.theses||[],gaps=task.gaps||[],answers=visibleAnswers(task);
  let response='';
  if(task.synthetic)response+='<p class="case-tag">合成案例，以下结果来自固定离线材料。</p>';
  response+=task.draft_report?draftReportCards(task):answerCards(task);
  if(theses.length)response+=theses.map(t=>'<article class="chat-thesis"><h3>'+esc(t.title)+'</h3><p>'+esc(t.investment_meaning)+'</p><p class="muted">'+esc(t.limitation)+'</p><div class="refs">'+(t.support||[]).map(ref=>evidenceButton(task,ref)).join('')+'</div></article>').join('');
  else if(!task.draft_report&&!answers.length&&!['待开始','运行中','失败','已取消'].includes(task.status))response+='<p>当前任务尚无可核验回答。</p>';
  if(!task.draft_report)response+=gapView(task,true);
  if(task.delivery)response+='<button class="artifact-card" data-stage="3"><strong>研究报告</strong><span>已保存版本'+task.delivery.version+'，阅读全文 →</span></button>';
  response+='<div class="chat-links"><button data-stage="0">研究概览</button><button data-stage="2">研究底稿</button><button data-stage="4">跟踪复核</button></div>';
  const messages=task.messages?.length?task.messages:[{role:'user',content:task.question}];
  const parts=messages.map((message,index)=>['history:'+index,messageHTML(message.role, message.role==='user'?esc(message.content):RHMessageProse.renderHTML(message.content)+(message.task_id?'<button class="button-secondary" data-task="'+esc(message.task_id)+'">查看该任务</button>':'')+(apiMode&&message.task_id&&message.delivery?'<a class="past-report" href="'+esc(reportURL({id:message.task_id},message.delivery.version||message.version))+'" target="_blank" rel="noopener">阅读该轮报告版本'+esc(message.delivery.version||message.version)+'</a>':''))]);
  parts.push(['process',researchProcess.html(task,{progress:nodeProgress(task),actions:actions(task)})],['response',messageHTML('assistant',response)]);
  const html=parts.map(([,content])=>content).join(''),key=task.id;
  if(chat.renderedKey===key&&chat.renderedHTML===html)return;
  const same=chat.renderedKey===key,progress=same?chat.querySelector('.chat-progress'):null;
  const open=progress?.open;
  reconcileChatMessages(chat,parts,same);chat.renderedKey=key;chat.renderedHTML=html;
  messageActions.prune();
  researchProcess.sync(chat,task);
  if(same){const fresh=chat.querySelector('.chat-progress');if(fresh&&open!==undefined)fresh.open=open}
  conversationReader.updated({changed:true});
}
function messageHTML(role,content){const label=role==='user'?'复制我的消息':'复制文字';return '<article class="message message--'+role+' '+(role==='user'?'user-message':'assistant-message')+'">'+(role==='user'?'<p class="message__body message-text">'+content+'</p>':'<div class="message__content"><div class="message__body message-prose">'+content+'</div></div>')+'<div class="message__actions"><button class="message__action" type="button" data-action="copy-message" data-copy-label="'+label+'" aria-label="'+label+'" title="'+label+'"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 9h12v12H9ZM5 15H3V3h12v2"/></svg></button><span class="message__copy-status" role="status"></span></div></article>'}
async function sendChat(){
  if(sending||importing||accountBusy||leaving||selectingTaskId)return;
  if(!workspaceReady){notice('工作台尚未连接，请先重新连接');return}
  // Retained reports are read-only, not the previously selected task's Chat.
  // Guard the action itself as well as hidden/disabled DOM controls, so native
  // shortcuts and delayed submit events cannot enter its execution path.
  if(historical){notice('历史报告仅供阅读，请新建研究');return}
  if(!historical&&selected&&pendingTaskActions.has(selected.id)){notice('研究操作正在处理，请稍候');return}
  if(selected?.status==='运行中'){captureDraft();try{await taskAction('cancel')}catch(error){notice(error.message)}return}
  const submittedText=$('#chat-input').value,question=submittedText.trim();if(!question)return;
  captureDraft();
  const origin=selected,originKey=composerKey(),generation=taskSelectionGeneration;
  const originVersion=origin?.version,originRevision=origin?.revision;
  // Follow-ups inherit the parent's scope on the server. Unrelated new-task
  // settings must neither override that scope nor prevent a valid follow-up.
  let input;
  try{input=origin?{question}:newResearchInput(question)}catch(error){notice(error.message);return}
  const materials=queuedMaterials;
  let submission=pendingSubmission;
  const current=()=>!leaving&&!accountBusy&&!historical&&taskSelectionGeneration===generation&&
    (selected?.id||null)===(origin?.id||null)&&selected?.version===originVersion&&selected?.revision===originRevision;
  const retainSubmission=()=>{
    if(leaving)return;
    composerMemory.set(originKey,{materials,pending:submission});
    if(current())pendingSubmission=submission;
  };
  const cacheTask=task=>{
    const prior=tasks.find(item=>item.id===task.id);
    if(!prior)tasks.unshift(task);
    else if(task.version>prior.version||task.version===prior.version&&task.revision>=prior.revision)
      tasks=tasks.map(item=>item.id===task.id?task:item);
    renderTaskList();
  };
  const stopDispatch=()=>{if(!leaving)notice('任务已切换或更新，草稿和附件已保留，未继续执行。')};
  sending=true;syncComposerAvailability();
  // Bind retries to the same scope snapshot as the request.
  // Settings edited while configuration is loading apply to the next send,
  // not to this request or the pending task it may already have created.
  const submissionKey=JSON.stringify({...input,parent:origin?.id||null});
  try{
    if(!apiMode){draft.question=question;captureDraft();notice('问题已保存为本地草稿；执行研究请在ResearchHelper.app打开。');return}
    if(!await verifyExecutionBeforeSend())return;
    if(!current())return stopDispatch();
    const expectedExecution=executionExpectation();
    if(!submission||submission.key!==submissionKey){
      const task=origin
        ?await api('/api/tasks/'+origin.id+'/messages',{message:question,start:false,...expectedExecution})
        :await api('/api/tasks',input);
      if(leaving)return;
      submission={key:submissionKey,taskId:task.id,parent:origin?.id||null};
      retainSubmission();cacheTask(task);
    }
    if(!current())return stopDispatch();
    const id=submission.taskId;
    let submittedTask=tasks.find(task=>task.id===id);
    if(!submittedTask)submittedTask=await api('/api/tasks/'+id);
    for(const material of materials){
      if(!current())return stopDispatch();
      if(material.savedTaskId===id)continue;
      material.status='保存中';renderQueuedMaterials();
      try{
        const fresh=await uploadMaterial(id,material.file,submittedTask.version);
        if(leaving)return;
        submittedTask=fresh;cacheTask(fresh);
        material.savedTaskId=id;material.status='已保存';
      }catch(error){material.status='保存失败';renderQueuedMaterials();throw error}
    }
    // Never dispatch inference before every original has been archived successfully.
    if(!current())return stopDispatch();
    const expectedTask={id,version:submittedTask.version,batch:submittedTask.batch,
      cutoff:submittedTask.cutoff,input_hash:submittedTask.input_hash};
    const fresh=await api('/api/tasks/'+id+'/start',{...expectedExecution,expected_task:expectedTask});
    if(leaving)return;
    const followUp=!!submission.parent;cacheTask(fresh);
    composerMemory.set(originKey,{materials:[],pending:null});
    if(!current()){
      if(draft.composerTexts?.[originKey]===submittedText)draft.composerTexts[originKey]='';
      return;
    }
    const remainder=$('#chat-input').value===submittedText?'':$('#chat-input').value;
    queuedMaterials=[];pendingSubmission=null;
    $('#chat-input').value='';draft.chat='';setTask(fresh,{submitted:true});
    $('#chat-input').value=remainder;captureDraft();
    if(followUp)notice('追问已建立关联研究，原任务与报告保留。');
  }catch(error){if(!leaving)notice(error.message)}finally{
    sending=false;syncComposerAvailability();
    if(!leaving){captureDraft();renderQueuedMaterials()}
  }
}
function syncComposerAvailability(){
  // Like OH, pending controls belong only to the task currently shown. A late
  // reply for another task must never unlock or disable this task's composer.
  const pending=!historical&&selected&&pendingTaskActions.has(selected.id);
  const running=!historical&&selected?.status==='运行中';
  const send=$('#chat-form button[type=submit]');
  const label=historical?'历史报告仅供阅读':pending?(running?'正在停止研究':'正在处理研究操作'):running?'停止研究':'发送研究问题';
  send.disabled=!!historical||!workspaceReady||initializing||sending||importing||accountBusy||leaving||!!selectingTaskId||!!pending;
  send.classList.toggle('is-generating',running);
  send.setAttribute('aria-busy',String(running||!!pending||sending));
  send.setAttribute('aria-label',label);send.title=label;
  $('#chat-form').setAttribute('aria-busy',String(sending||!!pending));
}
function renderQueuedMaterials(){
  let strip=$('#queued-materials');
  if(!strip){strip=document.createElement('div');strip.id='queued-materials';strip.className='message-attachments composer-materials';strip.setAttribute('aria-label','本次问题的材料');$('#chat-form').prepend(strip)}
  strip.hidden=!queuedMaterials.length;
  strip.innerHTML=queuedMaterials.map((item,index)=>'<div class="message-attachment"><span class="message-attachment__mark" aria-hidden="true">'+esc(item.file.name.split('.').pop().toUpperCase())+'</span><span class="message-attachment__label" title="'+esc(item.file.name)+'">'+esc(item.file.name)+'</span><span class="material-status">'+esc(item.status)+'</span><button type="button" class="material-remove" data-remove-material="'+index+'" aria-label="移除材料'+esc(item.file.name)+'" '+(sending?'disabled':'')+'>×</button></div>').join('');
  syncComposerAvailability();
  $('#chat-form [data-action="attach"]').disabled=!!historical||sending||importing||accountBusy||leaving||selected?.status==='运行中';
}
function assertCanQueueMaterials(){
  if(sending||importing||accountBusy||leaving||selected?.status==='运行中')throw Error('正在执行研究，请稍后添加材料');
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
function queueMaterials(files){
  assertCanQueueMaterials();
  const additions=[];
  for(const file of files){
    const same=item=>item.file.name===file.name&&item.file.size===file.size&&item.file.lastModified===file.lastModified;
    if(!queuedMaterials.some(same)&&!additions.some(same))additions.push({file,status:'待加入研究'});
  }
  const all=[...queuedMaterials,...additions];
  validateMaterialFiles(all.map(item=>item.file));
  queuedMaterials=all;renderQueuedMaterials();$('#chat-input').focus();
}
async function uploadMaterial(id,file,expectedVersion){
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let offset=0;offset<bytes.length;offset+=8192)binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
  const fresh=await api('/api/tasks/'+encodeURIComponent(id)+'/material',{name:file.name,base64:btoa(binary),...(expectedVersion===undefined?{}:{expected_version:expectedVersion})});
  const cached=tasks.find(task=>task.id===id);
  if(leaving||!taskSnapshotMatches(fresh,id)||expectedVersion!==undefined&&fresh.version!==expectedVersion||taskSnapshotIsOlder(fresh,cached))
    throw Error('材料保存结果未确认，请重新读取任务状态，不要重复提交');
  return fresh;
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
        const fresh=await uploadMaterial(id,file,version);
        tasks=tasks.map(task=>task.id===id?fresh:task);
        if(selected?.id===id&&!historical)selected=fresh;
      }
      render();feedback.textContent='材料已保存，尚未执行核验';
    }catch(error){feedback.textContent=error.message+'。已成功保存的材料保留。'}
    finally{importing=false;submit.disabled=false;form.setAttribute('aria-busy','false')}
  });
}
function actions(task){
  const busy=pendingTaskActions.has(task.id)?' disabled aria-busy="true"':'';
  if(task.status==='待开始')return '<button class="button-primary" data-action="start"'+busy+'>开始研究</button>';
  if(task.status==='运行中')return '<button class="button-secondary" data-action="cancel"'+busy+'>取消运行</button>';
  if(task.status==='失败'||task.status==='已取消')return '<button class="button-primary" data-action="'+(task.pending_html?'retry-save':'resume')+'"'+busy+'>'+(task.pending_html?'重试保存':'恢复原版本')+'</button>';
  return '<button class="button-secondary" data-action="update"'+busy+'>主动更新</button><button class="button-secondary" data-action="backup">任务备份</button>';
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
  return window.RHDeskViews.overview(task,{esc,cutoffLabel,readOnly:!!historical,
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
function gapView(task,chat=false){
  const questions=new Map((task.questions||[]).map(question=>[question.id,question]));
  const relatedQuestions=message=>[...new Set((task.gap_records||[]).filter(record=>record.message===message)
    .flatMap(record=>Array.isArray(record.question_ids)?record.question_ids:[]))].filter(id=>questions.has(id));
  const shownError=message=>chat&&task.status==='失败'&&message===task.error;
  const messages=[...new Set([task.error,...(task.gaps||[])].filter(message=>typeof message==='string'&&message.trim()&&(!shownError(message)||relatedQuestions(message).length)))];
  if(!messages.length)return '';
  const binding=' data-gap-task="'+esc(task.id)+'" data-gap-version="'+esc(task.version)+'"';
  const rows=messages.map(message=>{
    const ids=relatedQuestions(message);
    const related=ids.length?'<details class="work-gap-details"><summary>相关问题与来源</summary>'+ids.map(id=>
      '<div class="work-gap-related"><button type="button" class="text" data-gap-question="'+esc(id)+'"'+binding+
      ' aria-label="定位问题：'+esc(questions.get(id).question)+'">'+esc(questions.get(id).question)+'</button>'+
      (relatedGapSources(task,id).length?'<button type="button" data-gap-sources="'+esc(id)+'"'+binding+
      ' aria-label="查看'+esc(questions.get(id).question)+'的相关来源">查看来源</button>':'')+'</div>').join('')+'</details>':'';
    return '<li class="work-gap'+(message===task.error?' work-gap--error':'')+'">'+(shownError(message)?'':'<p>'+esc(message)+'</p>')+related+'</li>';
  }).join('');
  const heading=chat?'h3':'h2';
  return '<section class="'+(chat?'chat-gaps':'section')+' work-gaps" aria-label="未解决事项"><'+heading+
    '>待补充</'+heading+'><ul>'+rows+'</ul></section>';
}
function gapTaskMatches(button){
  return !historical&&selected&&button.dataset.gapTask===selected.id&&button.dataset.gapVersion===String(selected.version);
}
function locateGapQuestion(id){
  if(!selected?.questions.some(question=>question.id===id))return;
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
  Object.assign(failures,{worker_start_failed:'本地传输未启动',invalid_transport_message:'本地传输响应异常',provider_failure:'数据调用失败待核查',unsupported_operation:'接口尚未登记',response_too_large:'响应超过大小限制',connection_failed:'服务连接失败',http_failure:'服务请求失败',tls_failure:'安全连接未通过',original_download_failed:'公告原件未取得'});
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
  const recovery=apiMode&&(task.retained_originals||[]).length?'<section class="section original-recovery"><h2>留存原件</h2><p>只重新解析本地文件，不重新下载。</p>'+task.retained_originals.map(original=>'<div class="original-recovery-row"><div><strong>'+esc(original.name)+'</strong><p>'+esc(original.status)+'</p></div><button type="button" data-retry-original="'+esc(original.id)+'" data-original-task="'+esc(task.id)+'" '+(task.status==='运行中'||recoveringOriginals.has(original.id)?'disabled':'')+'>'+(recoveringOriginals.has(original.id)?'正在解析':'重新解析')+'</button></div>').join('')+'<p class="original-recovery-feedback" role="status" aria-live="polite"></p></section>':'';
  const questionById=new Map(task.questions.map(question=>[question.id,question.question]));
  const nodes=task.nodes.map((node,index)=>{
    const questions=node.questions.map(id=>questionById.get(id)||id).join('；');
    const needs=(node.evidence_needs||[]).length?'<p>取证需求：'+esc(node.evidence_needs.join('；'))+'</p>':'';
    const methods=(node.method_needs||[]).length?'<p>计算方法：'+esc(node.method_needs.map(methodName).join('、'))+'</p>':'';
    return '<article class="node" data-reading-key="node:'+esc(node.id)+'"><span class="node-num" aria-label="节点'+(index+1)+'">'+(index+1)+'</span><div><h3>'+esc(node.title)+'</h3><details><summary>依赖与完成条件</summary><p>执行能力：'+esc(node.operation)+'</p><p>对应问题：'+esc(questions)+'</p>'+needs+methods+'<p>前置依赖：'+esc(node.dependencies.join('、')||'无')+'</p><p>'+esc(node.completion)+'</p><p>'+esc(node.termination)+'</p>'+(node.error?'<p>'+esc(node.error)+'</p>':'')+'</details></div>'+statusTag(node.status)+'</article>';
  }).join('');
  return recovery+'<section class="section"><div class="section-heading"><h2>实际执行计划</h2>'+(task.nodes.length?'<span class="muted">'+nodeProgress(task)+'</span>':'')+'</div>'+(nodes||'<p>尚未形成执行计划。</p>')+'</section><section class="section"><details class="process-budget"><summary>运行预算与调用记录</summary>'+ledgerView(task)+'</details></section>'+supplementView(task)+((task.knowledge?.entries||[]).length?'<section class="section"><h2>相关知识与方法</h2>'+knowledgeView(task.knowledge)+'</section>':'');
}
function processView(task){
  return window.RHDeskViews.sources(task,{esc,sourceRecordName,sourceRecordButtons,unverifiedProposal,processDetails});
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
function methodName(method){return {fee_difference_v1:'同口径费率差',management_custody_fee_comparison_v1:'年度管理托管费率比较',management_custody_fee_summary_v1:'年度管理托管费率合计与差值',residual_present_value_v1:'剩余现金流现值',observed_price_return_v1:'已观测区间价格变化',observed_index_change_v1:'已观测指数区间变化',etf_nav_premium_v1:'同估值时点ETF净值折溢价',gross_margin_v1:'同口径营业毛利率',net_profit_margin_v1:'同口径净利润率',operating_cash_profit_ratio_v1:'经营现金流净利润比'}[method]||method}
function calculationName(item){
  if(item.method==='fee_difference_v2'){
    const kind=item.parameters?.fee_kind;
    if(kind==='management_fee')return '管理费年费率差';
    if(kind==='custody_fee')return '托管费年费率差';
  }
  return item.method_title||methodName(item.method);
}
function calculationButton(item,{showValue=false}={}){
  const value=showValue?'：'+Number(item.output.value).toFixed(4)+item.output.unit:'';
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
    const sources=(question.refs||[]).map(ref=>evidenceButton(task,ref)).join('');
    const calculations=(question.calculation_refs||[]).map(ref=>{
      const calculation=(task.calculations||[]).find(item=>item.id===ref);
      return calculation?'<p>'+calculationButton(calculation,{showValue:true})+'</p>':'';
    }).join('');
    return '<article class="chat-thesis research-answer"><h3>'+esc(question.question)+'</h3><p>'+esc(displayAnswer(task,question))+'</p><div class="refs">'+sources+'</div>'+calculations+'</article>';
  }).join('');
}
function partialDocumentContent(task){
  if(task.intent!=='原件内容核对')return '';
  const unresolved=new Set((task.questions||[]).filter(q=>q.status!=='已回答').map(q=>q.id));
  const items=(task.evidence||[]).filter(e=>e.usage_scope==='document_content'&&e.source_check==='通过'&&e.applicability_check==='通过'&&(e.question_ids||[]).some(id=>unresolved.has(id)));
  return items.length?'<section class="partial-document-content"><h2>已核对内容</h2><p>仍有未答事项，未形成完整回答。</p>'+documentExcerptCards(items)+'</section>':'';
}
function collectionWorkingpaper(task){
  const list=value=>Array.isArray(value)?value.filter(item=>item&&typeof item==='object'):[];
  const owned=record=>typeof record.id==='string'&&record.id.length>0&&
    (!record.task_id||record.task_id===task.id)&&
    (record.research_version==null||record.research_version===task.version);
  const materials=list(task.materials).filter(owned),records=list(task.source_records).filter(owned);
  const originals=new Map([...materials,...records].map(record=>[record.id,record]));
  const groups=new Map();
  for(const item of list(task.rejected_evidence)){
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
      '" data-source-evidence="'+esc(item.id||'')+'" aria-label="查看'+esc(item.object||'')+'未采用摘录的原件">'+
      (record.kind==='api'?'打开原始数据':'打开原件')+'</button>':'原件未留存';
    return '<tr><td data-label="未采用原因">'+esc(reasons[reason]||reason)+'</td><td data-label="对象">'+esc(item.object||'未注明对象')+
      '</td><td data-label="数量">'+count+'条</td><td data-label="原件">'+source+'</td></tr>';
  }).join('');
  const sources=sourceRecordButtons({...task,materials,source_records:records});
  return '<section class="workingpaper-records" aria-label="任务取证底稿">'+
    (sources?'<h3>留存资料</h3>'+sources:'')+
    (rows?'<h3>未采用摘录</h3><p>未通过核验，不能作为结论依据。</p>'+
      '<div class="table-wrap workingpaper-rejections" tabindex="0" aria-label="未采用摘录的核验问题">'+
      '<table><thead><tr><th>未采用原因</th><th>对象</th><th>数量</th><th>原件</th></tr></thead>'+
      '<tbody>'+rows+'</tbody></table></div>':'')+'</section>';
}
function draftReportCards(task){
  const paper=window.RHResearchData.paper(task);
  if(!paper)return '';
  return '<article class="chat-thesis research-answer"><h3>核心结论</h3><p>'+esc(paper.conclusion)+'</p></article>'+
    (paper.logics||[]).map((item,index)=>'<article class="chat-thesis"><h3>'+
      ((window.RHResearchData.draftDataRefs(item).length||item.source_refs?.length||item.calculation_refs?.length)?'<button type="button" class="research-logic-link" data-draft-basis="'+index+'" aria-haspopup="dialog" aria-label="查看'+esc(item.title)+'的研究依据">':'')+
      esc(item.display_heading||('策略逻辑'+(index+1)+'：'+(item.title||'')))+((window.RHResearchData.draftDataRefs(item).length||item.source_refs?.length||item.calculation_refs?.length)?'</button>':'')+
      '</h3><p>'+esc(item.analysis)+'</p>'+(item.implication?'<p>'+esc(item.implication)+'</p>':'')+
      (item.risk?'<p class="muted">验证条件：'+esc(item.risk)+'</p>':'')+'</article>').join('');
}
function draftFactsTable(task){
  const rows=task.draft_report?.facts||[];
  if(!rows.length)return '';
  return '<div class="table-wrap" tabindex="0" role="region" aria-label="本任务原始取数"><table><thead><tr>'+
    ['标的','指标','期间','原值','单位'].map(label=>'<th scope="col">'+label+'</th>').join('')+'</tr></thead><tbody>'+
    rows.map(row=>'<tr>'+['code','label','period','value','unit'].map(key=>'<td>'+esc(key==='value'?(row.value==null?'未返回':row.raw_value??row.value):row[key])+'</td>').join('')+'</tr>').join('')+
    '</tbody></table></div>';
}
function openDraftBasis(index){
  const task=selected,item=task?.draft_report?.logics?.[Number(index)];if(!item)return;
  const D=window.RHResearchData,paper=D.paper(task);if(!paper)return;
  const calculations=D.scope(paper.calculations,task).filter(row=>(item.calculation_refs||[]).includes(row.id));
  const refs=new Set([...D.draftDataRefs(item),...calculations.flatMap(row=>row.data_refs||[])]);
  const facts=D.draftFacts(task).filter(row=>refs.has(row.id));
  const ids=new Set([...(item.source_refs||[]),...facts.map(row=>row.artifact_id)]);
  const sources={...task,source_records:D.scope(task.source_records,task).filter(row=>ids.has(row.id)),materials:D.scope(task.materials,task).filter(row=>ids.has(row.id))};
  modal(item.title,draftArithmetic(calculations)+draftFactsTable({...task,draft_report:{facts}})+sourceRecordButtons(sources)+
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
function reportProfile(task){const value=draft.reportProfiles?.[task.id+':'+(task.delivery?.version??task.version)];return ['standard','detailed'].includes(value)?value:'standard'}
function reportURL(task,version=task.delivery?.version??task.version){return apiMode?'/api/tasks/'+task.id+'/report/'+version+'?profile='+reportProfile(task):null}
window.ResearchHelperNative={reportURL(){const url=historical?(apiMode?'/reports/':'reports/')+historical.key+'.html':selected?.delivery?reportURL(selected,selected.delivery.version):null;return url?new URL(url,location.href).href:null}};
function openReportCorrections(){
  const task=selected;if(historical||!task?.delivery)return;
  const target={id:task.id,version:task.delivery.version,digest:task.delivery.digest,selection:taskSelectionGeneration};
  const corrections=(Array.isArray(task.report_corrections)?task.report_corrections:[]).filter(item=>item&&item.task_id===target.id&&item.version===target.version&&typeof item.reason==='string'&&item.reason.trim());
  const notes=corrections.length?'<ol class="report-correction-list">'+corrections.map(item=>'<li><p>'+esc(item.reason)+'</p></li>').join('')+'</ol>':'';
  const form=apiMode?'<form id="report-correction-form"><label for="report-correction-reason">更正说明</label><textarea id="report-correction-reason" maxlength="4000" rows="4" required autofocus></textarea><p>更正随版本保留，原报告不覆盖。</p><p id="report-correction-feedback" role="status" aria-live="polite"></p><div class="form-actions"><button class="button-primary" type="submit">记录更正</button><button class="button-secondary" type="button" data-action="close">取消</button></div></form>':'';
  modal('报告更正',notes+form);
  const element=$('#report-correction-form');if(!element)return;
  const current=()=>!leaving&&!historical&&target.selection===taskSelectionGeneration&&selected?.id===target.id&&selected.delivery?.version===target.version&&selected.delivery.digest===target.digest;
  element.addEventListener('submit',async event=>{
    event.preventDefault();if(element.dataset.saving==='true')return;
    const feedback=element.querySelector('#report-correction-feedback'),submit=element.querySelector('button[type=submit]');
    if(!current()){feedback.textContent='任务已切换，请重新打开所需报告。';return}
    element.dataset.saving='true';submit.disabled=true;
    try{
      const value=await api('/api/tasks/'+target.id+'/report-correction',{version:target.version,expected_digest:target.digest,reason:element.querySelector('textarea').value});
      for(const item of tasks)if(item.id===target.id&&item.delivery?.version===target.version)item.report_corrections=value.corrections;
      if(current())selected.report_corrections=value.corrections;
      remember();
      if(current()&&element.isConnected&&$('#dialog').open){
        const restore=$('#dialog').contains(document.activeElement);
        $('#dialog').close();render();
        if(restore)$('#content [aria-label="更多报告操作"]')?.focus({preventScroll:true});
        notice('更正已记录');
      }
    }catch(error){if(element.isConnected)feedback.textContent=error.message}
    finally{element.dataset.saving='false';submit.disabled=false}
  });
}
function reportView(task){
  return window.RHDeskViews.report(task,{esc,apiMode,reportProfile,reportURL,reportVersionBusy});
}




function tracking(task){
  return window.RHDeskViews.tracking(task,{esc,statusTag,evidenceButton,calculationButton,calculationName,cutoffLabel});
}
function runtimeLive(){return apiMode&&boot.execution?.mode==='live'}
function modelConfigured(){
  if(typeof boot.research_readiness?.model_configured==='boolean')return boot.research_readiness.model_configured;
  if(!boot.authentication||boot.role==='admin'){
    const config=boot.model_settings||{};return !!(config.endpoint&&config.model&&config.has_key);
  }
  return null; // An older host withholding sales configuration is not proof of a missing model.
}
function researchSetupView(){
  if(!apiMode)return '';
  const ready=modelConfigured(),admin=!boot.authentication||boot.role==='admin';
  if(ready===false)return admin?'':'<div class="research-setup" data-research-setup><p>在线研究模型尚未配置，请联系admin。</p></div>';
  if(!runtimeLive()&&boot.execution?.configurable&&admin)return '<div class="research-setup" data-research-setup><button class="button-secondary" type="button" data-action="configure-execution">启用在线研究</button></div>';
  return '';
}
function executionExpectation(){return boot.execution?{expected_execution:{mode:boot.execution.mode,policy_version:boot.execution.policy_version}}:{}}
async function verifyExecutionBeforeSend({needsModel=true}={}){
  if(!apiMode)return true;
  const previous=boot.execution||{mode:'offline',policy_version:1};
  const fresh=await api('/api/bootstrap');
  if(boot.authentication&&(fresh.account!==boot.account||fresh.role!==boot.role)){leaveWorkspace();return false}
  const current=fresh.execution||{mode:'offline',policy_version:1};
  boot=fresh;token=boot.token||token;mergePresentations(boot.task_presentations);renderRuntime();
  if(previous.mode!==current.mode||previous.policy_version!==current.policy_version){
    captureDraft();
    notice('运行模式已变更为'+(runtimeLive()?'在线研究，材料会发送到配置的模型服务':'离线研究')+'。草稿和附件已保留，请确认后重新发送。');
    return false;
  }
  if(needsModel&&runtimeLive()&&modelConfigured()===false){
    captureDraft();
    if(!selected&&!historical)renderChat();
    notice((!boot.authentication||boot.role==='admin'?'模型尚未配置，请在设置中保存接口地址、模型名称和API密钥。':'模型尚未配置，请联系admin。')+'问题和附件已保留，未创建或派发研究。');
    return false;
  }
  return true;
}
function runtimeDescription(){return runtimeLive()?'在线研究已启用。':'离线研究，不调用模型或数据服务。'}
function renderRuntime(){
  const choice=document.querySelector('.model-picker select');
  if(choice){
    const admin=apiMode&&(!boot.authentication||boot.role==='admin');
    const items=[['status',runtimeLive()?'在线研究':'离线研究'],
      ...(admin?[['model','模型配置'],['data','数据源配置'],
        ...(boot.execution?.configurable?[['execution','运行模式']]:[])]:[['settings',apiMode?'账户与设置':'运行说明']])];
    choice.innerHTML=items.map(([value,title])=>'<option value="'+value+'">'+title+'</option>').join('');
    choice.value='status';choice.dispatchEvent(new Event('change',{bubbles:true}));
  }
  const context=document.querySelector('.context-copy');
  if(context)context.textContent=runtimeLive()?'在线研究':'离线研究';
}
document.addEventListener('change',event=>{
  const choice=event.target;
  if(!choice.matches('.model-picker select')||choice.value==='status')return;
  const action=choice.value,account=boot.account,role=boot.role;
  renderRuntime();
  // The shared OH choice control returns focus to its trigger after change.
  // Open the settings afterwards so that keyboard focus stays in the dialog.
  queueMicrotask(()=>{
    if(leaving||account!==boot.account||role!==boot.role)return;
    const admin=apiMode&&(!boot.authentication||boot.role==='admin');
    if(action==='settings')openRuntimeSettings();
    else if(admin&&action==='model')openRuntimeSettings('model-endpoint');
    else if(admin&&action==='data')openRuntimeSettings('ifind-access-token');
    else if(admin&&action==='execution'&&boot.execution?.configurable)openRuntimeSettings('execution-enabled');
  });
});
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
      renderRuntime();renderTaskList();if(!selected&&!historical)renderChat();
    }catch(error){if(!leaving)notice('配置核对未完成：'+error.message)}
  })();
  try{await settingsRefreshInFlight}finally{settingsRefreshInFlight=null}
}
function openRuntimeSettings(target){
  if(!workspaceReady||leaving||$('#dialog').open)return;
  window.RHWorkspaceSettings.open(target,{
    account:boot.account,onUpdated:refreshWorkspaceSettings,onInvalidated:leaveWorkspace,
    async onAction(action){
      try{
        if(action==='logout')await logout();
        else if(action==='switch-account')await logout(boot.role==='admin'?'sales':'admin');
        else if(action==='import')$('#backup-file').click();
        else if(action==='cases')modal('合成验收案例','<p>固定离线材料，仅验证研究工作流。</p>'+caseButtons());
        else if(action==='knowledge')modal('知识与模板','<button data-action="visual-library">图表与配色方案</button>'+knowledgeView({entries:boot.knowledge||selected?.knowledge?.entries||[]}));
      }catch(error){if(!leaving)notice(error.message)}
    },
  });
}
async function logout(nextAccount){
  if(accountBusy||sending||importing||leaving)return;
  accountBusy=true;captureDraft();
  try{
    await api('/api/auth/logout',{});
    try{localStorage.setItem(authEventKey,JSON.stringify({account:null,nonce:Date.now()}))}catch{}
    leaveWorkspace(nextAccount);
  }finally{accountBusy=false}
}
function historyView(){const key=historical.key,tag='<p class="history-label">历史原报告，公开研究内容保留。机构与内部信息已脱敏，未纳入衍生品产品板块。</p>';if(stage===0)return tag+'<section class="section"><h2>'+esc(historical.title)+'</h2><p class="lead">'+esc(historical.conclusion)+'</p><p>'+esc(historical.scope)+'</p><p>'+esc(historical.date)+'</p><button data-stage="3">阅读全文</button></section>';if(stage===2)return tag+(historical.logics||[]).map(logic=>'<article class="thesis"><h2>'+esc(logic.title)+'</h2>'+logic.paragraphs.map(p=>'<p>'+esc(p)+'</p>').join('')+'<p class="muted">'+esc(logic.evidence)+'</p></article>').join('')+'<p>'+esc(historical.risk)+'</p>';if(stage===1||stage===4)return tag+'<div class="empty"><h2>'+(stage===1?'历史执行底稿未保留':'原验证条件未完整保留')+'</h2><p>该报告只作为完整阅读案例，不伪造新引擎的检查点或追踪结果。</p><button data-stage="3">阅读原报告</button></div>';return tag+'<div class="report-actions"><button data-action="export-history">保存HTML</button><button data-action="print">打印报告</button></div><iframe id="report-frame" title="完整历史研究报告" class="report-frame" sandbox="allow-scripts allow-modals allow-same-origin" src="'+(apiMode?'/reports/':'reports/')+esc(key)+'.html"></iframe>'}
function reportVersionCurrent(read){
  return !!read&&!leaving&&!historical&&!selectingTaskId&&read.selection===taskSelectionGeneration&&
    read.id===selected?.id&&read.version===selected.version&&read.stage===stage&&
    read.desk===artifactsOpen&&read.modal===modalGeneration&&reportVersionRead===read;
}
function reportVersionBusy(task){return task?.id===reportVersionRead?.id&&reportVersionCurrent(reportVersionRead)}
function syncReportVersionControls(){
  const pending=reportVersionBusy(selected);
  document.querySelectorAll('[data-action="versions"]').forEach(button=>{
    button.disabled=pending;if(pending)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
  });
}
async function openReportVersions(){
  if(!selected||historical||selectingTaskId||leaving)return;
  if(reportVersionBusy(selected))return;
  const read={id:selected.id,version:selected.version,selection:taskSelectionGeneration,
    stage,desk:artifactsOpen,modal:modalGeneration,origin:document.activeElement?.closest('[data-work-menu]')?.querySelector('[data-work-menu-toggle]')||document.activeElement};
  reportVersionRead=read;syncReportVersionControls();
  try{
    const history=await api('/api/tasks/'+encodeURIComponent(read.id)+'/report-history');
    if(!reportVersionCurrent(read))return;
    const versions=history?.reports;
    if(history?.task_id!==read.id||!Array.isArray(versions)||versions.some(item=>
        !item||item.task_id!==read.id||!Number.isSafeInteger(item.version)||item.version<1||item.version>read.version||
        typeof item.digest!=='string'||!/^[a-f0-9]{64}$/.test(item.digest)||!Array.isArray(item.corrections)||
        item.corrections.some(note=>!note||note.task_id!==read.id||note.version!==item.version||
          typeof note.reason!=='string'||!note.reason.trim()))||
        new Set(versions.map(item=>item.version)).size!==versions.length)throw Error('交付版本响应无效，请重新读取');
    // Each note stays beside its own saved version. The frozen document is
    // opened unchanged; a later revision never silently clears the old error.
    modal('交付版本',versions.length?versions.map(item=>'<section class="report-version-record"><p><a class="report-version-link" href="/api/tasks/'+
      esc(encodeURIComponent(read.id))+'/report/'+item.version+'" target="_blank" rel="noopener">阅读版本'+item.version+'</a></p>'+
      (item.corrections.length?'<h3>报告更正</h3><ol class="report-correction-list">'+item.corrections.map(note=>'<li><p>'+esc(note.reason)+'</p></li>').join('')+'</ol>':'')+'</section>').join(''):
      '<p>尚无已保存的交付版本。</p>');
    previousFocus=read.origin;
  }catch(error){if(reportVersionCurrent(read))notice(error.message)}
  finally{if(reportVersionRead===read){reportVersionRead=null;syncReportVersionControls()}}
}
function modal(title,content){modalGeneration+=1;syncReportVersionControls();window.RHTaskMenu?.close();previousFocus=document.activeElement;$('#dialog-title').textContent=title;$('#dialog-content').innerHTML=content;window.RHChoice.enhanceSelects($('#dialog-content'));$('#dialog').showModal()}
$('#dialog').addEventListener('close',()=>{
  const dialog=$('#dialog'),active=document.activeElement;
  // A queued close from an older modal must not steal a newly chosen focus.
  if(dialog.open||(active&&active!==document.body&&active!==previousFocus&&!dialog.contains(active)))return;
  focusWorkspaceControl(previousFocus,artifactsOpen?$('.views [aria-selected="true"]'):$('#chat-input'));
});
function sourceRecordName(record){
  if(record.source==='ifind'&&record.operation==='smart_stock_picking'&&record.financial_query_contract?.query_kind==='object_discovery')return 'iFinD 候选发现';
  if(record.source==='official'&&record.operation==='release_catalog'){
    const channels={nbs_release:'国家统计局发布目录',fed_fomc:'FOMC声明目录'};
    return channels[record.parameters?.channel]||'官方发布目录';
  }
  const material=record.kind==='filing'?record.snapshot?.material:record.snapshot||record;
  if(record.kind!=='api')return material?.official_origin?.title||material?.name||record.name||record.directory?.title||'公告原件';
  const names={get_limit_policy:'接口限额',income:'利润表',cashflow:'现金流量表',daily:'日行情',fund_daily:'基金日行情',daily_basic:'每日指标',get_thscode:'证券代码查询',report_query:'公告目录',cmd_history_quotation:'历史行情',get_trade_dates:'交易日历',basic_data_service:'基础数据',date_sequence:'序列数据',edb_service:'经济数据',data_pool:'指数名单',smart_stock_picking:'财务数据查询'};
  return ({tinyshare:'Tinyshare',ifind:'iFinD',wind:'Wind'}[record.source]||record.source||'接口')+' '+(names[record.operation]||'接口数据');
}
function sourceRecordButtons(task){
  const seen=new Set();
  return [...(task.materials||[]),...(task.source_records||[])].filter(record=>{if(seen.has(record.id))return false;seen.add(record.id);return true}).map(record=>{
    const material=record.kind==='filing'?record.snapshot?.material:record;
    const name=sourceRecordName(record);
    const status=material?.status||record.stages?.parse||(record.kind==='api'?'待核验':'尚未取得正文');
    return '<div class="source-record"><button type="button" class="text" data-source="'+esc(record.id)+'">'+esc(name)+'</button><span>'+esc(status)+'</span></div>';
  }).join('');
}
function openSource(id,evidenceId,originLocator){
  const approved=evidenceId?selected?.evidence?.find(e=>e.id===evidenceId):null;
  const rejected=evidenceId?selected?.rejected_evidence?.find(e=>e.id===evidenceId):null;
  const item=approved||(rejected?unverifiedProposal(selected,rejected):null);
  const record=[...(selected?.materials||[]),...(selected?.source_records||[])].find(source=>source.id===id);
  if(!record)return notice('当前版本没有保留该原件，请查看原交付版本');
  const origin=$('#dialog').open?previousFocus:null;
  modal(sourceRecordName(record),'<div id="source-reader"></div>');
  if(origin)previousFocus=origin;
  window.RHSourceViewer.mount($('#source-reader'),{record,version:selected.version,locator:originLocator||item?.record_locator,quote:item?.text,
    relatedRecords:[...(selected.materials||[]),...(selected.source_records||[])]});
  const original=record.kind==='filing'?record.snapshot?.material:record.snapshot;
  if(apiMode&&selected.status!=='运行中'&&['material','filing'].includes(record.kind)&&original?.status==='已解析'&&original.parts?.length){
    const action=document.createElement('button');action.type='button';action.className='text';action.dataset.documentReview=id;
    action.textContent='另建原件内容核对';$('#source-reader').append(action);
  }
}
function openDocumentReview(artifactId){
  const record=[...(selected?.materials||[]),...(selected?.source_records||[])].find(item=>item.id===artifactId);
  if(!record)return notice('原件已变化，请重新打开来源');
  modal('原件内容核对','<form id="document-review-form" data-task="'+esc(selected.id)+'" data-artifact="'+esc(artifactId)+'" data-version="'+selected.version+'" data-hash="'+esc(record.snapshot_hash||record.hash)+'">'+
    '<p>只核对文件陈述，不确认历史可得性或投资结论。原研究与报告保持不变。</p><label for="document-review-question">核对问题</label>'+
    '<textarea id="document-review-question" maxlength="20000" required placeholder="需要核对文件中的哪些内容？"></textarea>'+
    '<p id="document-review-feedback" role="status"></p><button type="submit">建立核对任务</button></form>');
  $('#document-review-question').focus();
}
function candidateOriginRecord(task,item){
  if(item?.identity?.mode!=='candidate_provider_observation')return null;
  const reference=item.identity.basis_ref,origin=reference?.discovery;
  const parents=task.source_records?.filter(record=>record.id===origin?.artifact_id)||[];
  const quotes=task.source_records?.filter(record=>record.id===item.artifact_id)||[];
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
    '<button type="button" class="text" data-source="'+esc(origin.record.id)+'" data-source-origin="'+esc(item.id)+'" aria-label="查看'+esc(item.object)+'的发现名单">查看发现名单</button>':
    '对应名单未保留或版本已变化')+'</dd></div>';
}
function detailEvidence(id){
  const item=selected?.evidence.find(e=>e.id===id);
  if(!item)return notice('该证据不属于当前任务版本');
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
    candidateOriginView(selected,item)+
    '<div><dt>公开时间</dt><dd>'+esc(item.published_at||'未核验')+'</dd></div>'+
    (['current_snapshot','current_original'].includes(item.availability?.kind)?'<div><dt>当前版本取得时间</dt><dd>'+esc(cutoffLabel(item.availability.obtained_at))+'</dd></div><div><dt>可用范围</dt><dd>当前版本研究，不证明历史当时可得'+(item.availability.kind==='current_original'?'或事件因果':'')+'。</dd></div>':'')+
    (item.usage_scope==='document_content'?'<div><dt>可用范围</dt><dd>仅核对原件内容，不用于历史时点或投资判断。</dd></div>':'')+
    '<div><dt>来源与提取核验</dt><dd>'+esc(item.source_check)+'</dd></div>'+
    '<div><dt>研究适用性核验</dt><dd>'+esc(item.applicability_check)+'</dd></div></dl>'+conditions+
    (item.artifact_id?'<button type="button" data-source="'+esc(item.artifact_id)+'" data-source-evidence="'+esc(item.id)+'">查看原件</button>':'')+
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
async function taskAction(action){
  if(selectingTaskId)return notice('正在读取任务，请稍候');
  if(importing)return notice('正在保存材料，请稍候');
  if(!apiMode)return notice('独立HTML支持阅读、备份与草稿；执行共享引擎请打开ResearchHelper.app');
  if(leaving||accountBusy)return;
  if(!selected||historical)return notice('请选择当前研究');
  const target=selected,{id,version,revision}=target,selectionGeneration=taskSelectionGeneration;
  const expectedTask={id,version,batch:target.batch,cutoff:target.cutoff,input_hash:target.input_hash};
  if(pendingTaskActions.has(id))return notice('研究操作正在处理，请稍候');
  const dispatch=['start','resume','update'].includes(action);
  const setBusy=busy=>{
    if(selected?.id===id&&!historical){
      document.querySelectorAll('#task-actions button[data-action],#chat .rh-process button[data-action]').forEach(button=>{
        if(!['start','resume','cancel','update','retry-save'].includes(button.dataset.action))return;
        button.disabled=busy;
        if(busy)button.setAttribute('aria-busy','true');else button.removeAttribute('aria-busy');
      });
    }
    syncComposerAvailability();
  };
  pendingTaskActions.add(id);setBusy(true);
  try{
    if(dispatch&&!await verifyExecutionBeforeSend({needsModel:!target.synthetic}))return;
    if(leaving||accountBusy)return;
    if(taskSelectionGeneration!==selectionGeneration||historical||selected?.id!==id||selected.version!==version||selected.revision!==revision)
      return notice('研究已切换或更新，请重新确认操作');
    const fresh=await api('/api/tasks/'+id+'/'+action,{...(dispatch?executionExpectation():{}),expected_task:expectedTask});
    if(!taskSnapshotMatches(fresh,id))throw Error('研究操作响应不匹配，请先读取任务状态，不要重复提交。');
    const current=tasks.find(task=>task.id===id);
    if(taskSnapshotIsOlder(fresh,current))return;
    tasks=tasks.map(task=>task.id===fresh.id?fresh:task);
    if(taskSelectionGeneration===selectionGeneration&&selected?.id===id&&!historical)setTask(fresh);else renderTaskList();
  }finally{pendingTaskActions.delete(id);setBusy(false)}
}
const artifactExport=window.RHArtifactExport.create({notify:notice});
function download(name,text,mime='text/html'){return artifactExport.saveText(name,text,mime)}
async function backupTask(button){
  const task=selected,selection=taskSelectionGeneration;
  if(!task||historical)throw Error('请先选择当前研究任务');
  const disabled=button.disabled;button.disabled=true;button.setAttribute('aria-busy','true');
  try{
    // A UI projection is not a restorable research archive. Fetch the complete
    // authorized task only for this explicit backup, without changing the UI.
    const complete=apiMode?await api('/api/tasks/'+task.id,undefined,{workspaceView:false}):task;
    if(leaving||historical||selection!==taskSelectionGeneration||selected?.id!==task.id||selected.version!==task.version)
      throw Error('研究已切换，请重新备份当前任务');
    if(apiMode&&(!taskSnapshotMatches(complete,task.id)||complete.version!==task.version||taskSnapshotIsOlder(complete,task)||complete.workspace_view_version!==undefined))
      throw Error('完整备份响应不匹配，请重新读取任务后再试');
    await download('ResearchHelper任务备份.json',JSON.stringify({workspaceVersion:4,tasks:[complete]},null,2),'application/json');
  }finally{button.disabled=disabled;button.removeAttribute('aria-busy')}
}
function openVisualLibrary(){
  modal('图表方案','<iframe id="visual-library-frame" class="visual-library-frame" title="图表方案与配色" sandbox="allow-scripts allow-same-origin allow-downloads" src="visual-library.html?embedded=1"></iframe>');
  $('#dialog').classList.add('visual-library-host');
}
$('#dialog').addEventListener('close',()=>$('#dialog').classList.remove('visual-library-host'));
window.addEventListener('message',event=>{
  const panel=$('#visual-library-frame');if(leaving||!panel||event.source!==panel.contentWindow||event.origin!==location.origin&&event.origin!=='null')return;
  const report=$('#report-frame')?.contentWindow?.RHVisuals;
  if(event.data?.type==='rh:visual-library-close'){$('#dialog').close();return}
  if(event.data?.type==='rh:visual-library-ready'){
    panel.contentWindow.postMessage({type:'rh:visual-library-context',targets:report?.contexts()||[],canApply:!!report,theme:report?.profile().theme||'research'},'*');
  }else if(event.data?.type==='rh:visual-library-apply'&&report){
    try{report.applyProfile(event.data.profile);notice('已应用图表方案')}catch(error){notice(error.message)}
  }
});
function downloadBinary(result){return artifactExport.saveBinary(result)}
async function exportReport(history=false){
 return artifactExport.run(async()=>{
  const selection=taskSelectionGeneration,task=selected,reference=historical;
  const visuals=history?null:$('#report-frame')?.contentWindow?.RHVisuals;
  const presentation=visuals?.profile()||{theme:'research',schemes:{}};
  if(history&&!reference||!history&&!task?.delivery)throw Error('报告尚未保存');
  const version=task?.delivery?.version??task?.version,profile=task?reportProfile(task):'standard';
  const format=history?'html':($('#report-format')?.value||'html');
  function assertCurrent(){
   if(leaving||selection!==taskSelectionGeneration||historical!==reference||selected?.id!==task?.id||(!history&&(selected?.delivery?.version!==version||selected?.version!==task.version||reportProfile(selected)!==profile||($('#report-format')?.value||'html')!==format)))
    throw Error('研究或导出选择已变化，请重新导出');
  }
  if(!history&&apiMode){
   const result=await api('/api/export',{task_id:task.id,version,profile,format,visual_profile:{theme:presentation.theme||'research',schemes:presentation.schemes||{}}});
   assertCurrent();
   if(result.task_id!==undefined&&result.task_id!==task.id||result.version!==undefined&&result.version!==version)throw Error('导出响应与研究版本不一致');
   return downloadBinary(result);
  }
  if(!history&&format!=='html'){
   if(format==='pdf'){$('#report-frame')?.contentWindow?.print();notice('请选择系统打印中的存储为PDF');return}
   throw Error('请在App中导出此格式');
  }
  const name=history?(reference.shortTitle||reference.title):(task.title||task.question||'研究报告');
  let text;
  if(history){
   const response=await fetch((apiMode?'/reports/':'reports/')+reference.key+'.html');
   if(response.status===401){leaveWorkspace();return}
   if(!response.ok)throw Error('报告读取失败，请重新登录或检查任务权限');
   text=await response.text();
  }else text=profile==='detailed'?task.report_detailed_html:task.report_html;
  assertCurrent();if(!text)throw Error('此版本未保存报告正文');
  if(visuals&&(presentation.theme!=='research'||Object.keys(presentation.schemes||{}).length))text=visuals.styleExport(text,presentation);
  return download(String(name).replace(/[\\/:*?"<>|]/g,'_').slice(0,100)+(history?'':'_v'+version)+'.html',text);
 });
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
    if(leaving)return;
    if(button.dataset.action==='reconnect'){await init();return}
    if((sending||importing||accountBusy)&&(button.dataset.task||button.dataset.history||button.dataset.case||['new','import','logout','switch-account'].includes(button.dataset.action))){notice('正在保存研究，请稍候');return}
    if(button.dataset.paperTarget){
      const target=document.getElementById(button.dataset.paperTarget);if(target&&$('#content').contains(target)){target.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});target.focus({preventScroll:true})}return;
    }
    if(button.dataset.draftSource||button.dataset.draftCalculation){
      if(button.dataset.sourceTask!==selected?.id||button.dataset.sourceVersion!==String(selected.version))return notice('任务已切换，请使用当前底稿');
      if(button.dataset.draftSource){
        const fact=selected.draft_report?.facts.find(item=>item.id===button.dataset.draftSource);
        if(fact)openSource(fact.artifact_id,undefined,fact.locator&&typeof fact.locator==='object'?fact.locator:undefined);
      }else{
        const calc=selected.draft_report?.calculations.find(item=>item.id===button.dataset.draftCalculation);
        if(calc){const ids=new Set(calc.data_refs||[]),facts=selected.draft_report.facts.filter(item=>ids.has(item.id));modal(calc.label||'计算依据',draftArithmetic([calc])+draftFactsTable({...selected,draft_report:{facts}}));}
      }
      return;
    }
    if(button.dataset.sourceTask&&(button.dataset.sourceTask!==selected?.id||button.dataset.sourceVersion!==String(selected.version)))return notice('任务已切换，请使用当前底稿');
    if(button.dataset.removeMaterial!==undefined){if(sending)return;queuedMaterials.splice(Number(button.dataset.removeMaterial),1);renderQueuedMaterials();$('#chat-input').focus();return}
    if(button.closest('[data-mode-switch]')){switchMode(button.dataset.mode);return}
    if(button.dataset.gapQuestion||button.dataset.gapSources){
      if(!gapTaskMatches(button))return notice('任务已变化，请使用当前任务的入口');
      if(button.dataset.gapQuestion)locateGapQuestion(button.dataset.gapQuestion);
      else if(button.dataset.gapSources)openGapSources(button.dataset.gapSources);
      return;
    }
    if(button.dataset.starter){$('#chat-input').value=button.dataset.starter;captureDraft();$('#chat-input').focus();return}
    if(button.dataset.reportProfile){
      if(!selected?.delivery)return;
      const profile=button.dataset.reportProfile;if(!['standard','detailed'].includes(profile))return;
      if(!apiMode&&profile==='detailed'&&!selected.report_detailed_html){notice('此旧版本未打包详细版，可在App中导出');return}
      const key=selected.id+':'+(selected.delivery?.version??selected.version),presentation=$('#report-frame')?.contentWindow?.RHVisuals?.profile();
      if(presentation)draft.reportStyles={...(draft.reportStyles||{}),[key]:presentation};
      draft.reportProfiles={...(draft.reportProfiles||{}),[key]:profile};remember();view(3);return;
    }
    if(button.dataset.stage!==undefined){view(button.dataset.stage);return}
    if(button.dataset.task){selectTaskEntry('task',button.dataset.task);return}
    if(button.dataset.history){selectTaskEntry('history',button.dataset.history);return}
    if(button.dataset.case){if($('#dialog').open)$('#dialog').close();let task;if(apiMode){task=await api('/api/cases',{id:button.dataset.case});tasks.unshift(task)}else{task=tasks.find(t=>t.fixture===button.dataset.case);if(!task)return notice('该合成案例未打包，请在App中运行');}setTask(task);return}
    if(button.dataset.evidence){const fromCalculation=$('#dialog').open&&!!$('#dialog .calculation-metadata'),origin=previousFocus;if($('#dialog').open&&!fromCalculation)$('#dialog').close();detailEvidence(button.dataset.evidence);if(fromCalculation)previousFocus=origin;return}
    if(button.dataset.documentReview){openDocumentReview(button.dataset.documentReview);return}
    if(button.dataset.calculation){detailCalculation(button.dataset.calculation);return}
    if(button.dataset.draftBasis!==undefined){openDraftBasis(button.dataset.draftBasis);return}
    if(button.dataset.source){
      const item=button.dataset.sourceOrigin?selected?.evidence?.find(e=>e.id===button.dataset.sourceOrigin):null;
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
    const action=button.dataset.action;
    if(action==='copy-message'){await messageActions.copy(button);return}
    if(action==='new'){cancelTaskSelection();taskSelectionGeneration+=1;stopWatching();captureDraft();selected=null;historical=null;stage=0;artifactsOpen=false;restoreComposerDraft();closeTasks();remember();render();resetScroll();$('#chat-input').focus()}
    else if(action==='artifacts')setContext(!contextOpen);
    else if(action==='close-context')setContext(false);
    else if(action==='chat')switchMode('chat');
    else if(action==='assistant-toggle')setAssistantOpen(!assistantOpen);
    else if(action==='tracking-input'){setAssistantOpen(true);$('#chat-input').focus();}
    else if(action==='assistant-close')setAssistantOpen(false);
    else if(action==='scope'){modal('新任务设置',scopeView());$('#entry').value=draft.entry||'混合';restoreDraft();syncResearchMode();window.RHChoice.enhanceSelects($('#dialog-content'))}
    else if(action==='close')$('#dialog').close();
    else if(action==='tasks')toggleTasks();
    else if(action==='archived-tasks'){showArchived=!showArchived;renderTaskList()}
    else if(action==='dismiss-tasks'){closeTasks();$('.web-control[data-action="tasks"]').focus()}
    else if(['start','resume','cancel','update','retry-save'].includes(action))await taskAction(action);
    else if(action==='export')await exportReport();
    else if(action==='export-history')await exportReport(true);
    else if(action==='backup')await backupTask(button);
    else if(action==='visual-library')openVisualLibrary();
    else if(action==='retry-report'){reportReader.retry();}
    else if(action==='print'){const frame=$('#report-frame');if(!frame)throw Error('请先打开报告');if(!reportReader.isReady(frame))throw Error('报告尚未读取完成，请先重新读取报告');if(window.webkit?.messageHandlers?.researchhelper)window.webkit.messageHandlers.researchhelper.postMessage({action:'print'});else frame.contentWindow.print()}
    else if(action==='import')$('#backup-file').click();
    else if(action==='material')openTaskMaterials();
    else if(action==='attach')composerControls.choose();
    else if(action==='library')modal('报告库',Object.entries(boot.history||{}).map(([key,item])=>'<button class="library-row" data-history="'+esc(key)+'" data-action="close"><strong>'+esc(item.shortTitle||item.title)+'</strong><span>完整历史研究</span></button>').join('')+tasks.filter(t=>t.delivery).map(t=>'<button class="library-row" data-task="'+esc(t.id)+'"><strong>'+esc(t.question)+'</strong><span>版本'+t.version+'，'+esc(t.status)+'</span></button>').join(''));
    else if(action==='knowledge')modal('知识与模板',knowledgeView({entries:boot.knowledge||selected?.knowledge?.entries||[]})+'<h3>已登记呈现</h3><p>完整纸面报告、同口径柱状对比、证据表、情景现值与MathML公式。无数据不补样图。</p>');
    else if(action==='settings')openRuntimeSettings();
    else if(action==='configure-data')openRuntimeSettings(button.dataset.provider==='tinyshare'?'tinyshare-token':'ifind-access-token');
    else if(action==='configure-execution')openRuntimeSettings('execution-enabled');
    else if(action==='logout')await logout();
    else if(action==='switch-account')await logout(boot.role==='admin'?'sales':'admin');
    else if(action==='cases'){if($('#dialog').open)$('#dialog').close();modal('合成验收案例','<p>固定离线材料，仅验证研究工作流。</p>'+caseButtons())}
    else if(action==='versions'&&apiMode)await openReportVersions();
    else if(action==='report-corrections')openReportCorrections();
    else if(action==='versions')notice('当前文件保留打包的冻结版本，主动更新请在App执行');
    if((button.dataset.history||button.dataset.task)&&$('#dialog').open)$('#dialog').close();
  }catch(error){notice(error.message)}
});
document.addEventListener('input',event=>{if(event.target.id==='chat-input'){captureDraft();assistantAvatar.setActivity(assistantActivity(selected));}if(event.target.id==='task-search')renderTaskList()});
document.addEventListener('change',event=>{if(event.target.id==='research-mode')syncResearchMode()});
document.addEventListener('invalid',event=>{
  // Native validation runs before submit. Reveal invalid budget fields before
  // the browser attempts to focus them inside the collapsed details element.
  if(event.target.closest('#new-form')){
    const details=event.target.closest('details');if(details)details.open=true;
  }
},true);
$('#chat-input').addEventListener('compositionstart',()=>composing=true);
$('#chat-input').addEventListener('compositionend',()=>composing=false);
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();sendChat()}});
window.addEventListener('researchhelper:toggle-sidebar',toggleTasks);
window.addEventListener('researchhelper:open-artifacts',()=>setContext(!contextOpen));
window.addEventListener('researchhelper:open-settings',()=>{if(workspaceReady&&!leaving){const menu=$('#account-menu');window.RHWorkspaceControls?.closeAccountMenu(menu&&!menu.hidden);openRuntimeSettings()}});
window.addEventListener('researchhelper:new-task',()=>$('.rail [data-action="new"]').click());
// Native hosts own page zoom. File/browser previews own CSS zoom and must
// compensate viewport units so the composer and settings remain on screen.
const workspaceScaleSteps=[.8,.9,1,1.1,1.25,1.4];
function applyWorkspaceScale(value){
  const scale=workspaceScaleSteps.includes(Number(value))?Number(value):1,root=document.documentElement;
  const native=['macos','windows'].includes(root.dataset.nativeShell);
  root.dataset.uiScale=String(scale);
  if(native){
    root.style.removeProperty('zoom');delete root.dataset.browserScale;
    root.style.removeProperty('--rh-viewport-width');root.style.removeProperty('--rh-viewport-height');
    if(root.dataset.nativeShell==='macos')root.style.setProperty('--native-titlebar-height',36/scale+'px');
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
  if(event.key==='Escape'&&artifactsOpen&&assistantOpen&&!$('#dialog').open){setAssistantOpen(false);return}
  if(event.ctrlKey&&event.altKey&&!event.metaKey&&!event.shiftKey&&event.key.toLowerCase()==='s'
      &&!event.isComposing&&!composing&&event.keyCode!==229&&!event.repeat){
    const focusInRail=$('#tasks').contains(document.activeElement);
    event.preventDefault();toggleTasks();
    const railHidden=compactTasks.matches?!$('.tasks').classList.contains('visible'):$('[data-workspace-shell]').dataset.railCollapsed==='true';
    if(railHidden&&focusInRail)focusWorkspaceControl($('.web-control[data-action="tasks"]'),$('#chat-input'));
    return;
  }
  if(event.key==='Escape'&&$('.tasks').classList.contains('visible')&&!$('#dialog').open){closeTasks();$('.web-control[data-action="tasks"]').focus()}
  if(!event.target.matches('.views [role=tab]'))return;
  // Arrow navigation follows keyboard focus, which can differ from the
  // selected view after assistive navigation or a restored focus position.
  const focusedStage=Number(event.target.dataset.stage);
  const keys={ArrowRight:(focusedStage+1)%5,ArrowLeft:(focusedStage+4)%5,Home:0,End:4};
  if(event.key in keys){event.preventDefault();view(keys[event.key]);$('#view-'+stage).focus()}
});
compactTasks.addEventListener('change',closeTasks);
compactContext.addEventListener('change',()=>{
  syncWorkspaceSheets();
  if(contextOpen&&compactContext.matches&&!$('#task-context').contains(document.activeElement))$('#task-context [data-action="close-context"]').focus({preventScroll:true});
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
  if(event.target.id==='chat-form'){event.preventDefault();await sendChat();return}
  if(event.target.id!=='new-form')return;event.preventDefault();if(!saveScopeSettings(event.target))return;$('#dialog').close();notice('新任务范围与预算已保存，不改变已有研究。');return;
});
function importLegacy(backup){
  if(!backup||typeof backup!=='object')throw Error('备份格式无效');
  const title=backup.tasks?.[0]?.question||backup.question||backup.title||'旧任务备份';
  const cutoff=new Date().toISOString();
  const task={id:'rh_import_'+crypto.randomUUID(),question:String(title),entry:'混合',intent:'综合研究',objects:[],cutoff,version:1,batch:0,status:'待开始',fixture:null,synthetic:false,original_input:{question:String(title)},questions:[{id:'q1',question:'重新核验旧任务材料',status:'待回答',required:true}],nodes:[],evidence:[],rejected_evidence:[],calculations:[],theses:[],materials:[],gaps:['旧结果仅归档，未转换成新引擎已核验事实。'],tracking:[],revisions:[],ledger:{limits:{data_requests:100,model_calls:8,seconds:600},used:{data_requests:0,model_calls:0,seconds:0},reserve:{model_calls:1,seconds:60},supplements:0},legacy_backup:backup};
  tasks.unshift(task);setTask(task);return task;
}
$('#backup-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;if(sending||importing||accountBusy||leaving){event.target.value='';notice('正在保存研究，请稍后导入备份');return}importing=true;try{if(file.size>2000000)throw Error('备份超过2MB');const backup=JSON.parse(await file.text());if(apiMode){const task=await api('/api/import',{backup});tasks.unshift(task);setTask(task)}else{importLegacy(backup);notice('旧备份已归档；未将旧复核状态转换成新引擎已验证。')}}catch(error){notice(error.message)}finally{importing=false;event.target.value=''}});
$('#material-file').multiple=true;
$('#material-file').addEventListener('change',event=>{if(!event.target.files.length)return;try{queueMaterials([...event.target.files])}catch(error){notice(error.message)}event.target.value=''});
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
  // Read live input after bootstrap: text and Files may arrive while awaiting
  // the connection. They belong to a new question, not the restored old task.
  const retained={text:$('#chat-input').value,materials:queuedMaterials};
  token=boot.token||'';tasks=boot.tasks||[];mergePresentations(boot.task_presentations);renderRuntime();
  if(apiMode)draftKey=boot.authentication?'researchhelper-input-draft-v2:'+boot.account:'researchhelper-input-draft-v1';
  if(boot.authentication){
    window.RHWorkspaceControls.setAccount(boot.account,true);
    $('[data-workspace-shell]').dataset.role=boot.role;
    try{localStorage.setItem(authEventKey,JSON.stringify({account:boot.account,nonce:Date.now()}))}catch{}
  }
  if(!apiMode){try{const raw=localStorage.getItem(storageKey),saved=JSON.parse(raw);if(saved?.workspaceVersion===4&&Array.isArray(saved.tasks)){const initial=new Map(tasks.map(t=>[t.id,t]));saved.tasks.forEach(t=>{if(t?.id&&Array.isArray(t.questions)&&t.ledger)initial.set(t.id,restoreOfflineTask(t,boot.tasks||[]))});tasks=[...initial.values()];selected=tasks.find(t=>t.id===saved.selected);draft=saved.draft||{};stage=Math.min(4,Math.max(0,Number(saved.stage)||0))}else if(saved){localStorage.setItem(storageKey+'-legacy-backup',raw);importLegacy(saved)}}catch{notice('旧本地记录未覆盖，请先导出备份')}}else{try{draft=JSON.parse(localStorage.getItem(draftKey))||{}}catch{}}
  if(!apiMode){try{mergePresentations(JSON.parse(localStorage.getItem(storageKey))?.taskPresentations)}catch{}}
  if(apiMode&&draft.activeTaskId)selected=tasks.find(t=>t.id===draft.activeTaskId)||null;
  artifactsOpen=draft.workspaceMode==='desk';
  if(artifactsOpen)stage=savedDeskStage();
  if(typeof draft.activeHistoryKey==='string'&&Object.hasOwn(boot.history||{},draft.activeHistoryKey)){
    historical={...boot.history[draft.activeHistoryKey],key:draft.activeHistoryKey};
    if(draft.workspaceMode!=='chat')artifactsOpen=true;
    stage=savedDeskStage();
  }
  // Migrate the old single draft only into its original active task, not every task.
  if(!draft.composerTexts||typeof draft.composerTexts!=='object'||Array.isArray(draft.composerTexts))draft.composerTexts={[composerKey()]:typeof draft.chat==='string'?draft.chat:''};
  if(retained.text||retained.materials.length){
    // Input typed before account bootstrap belongs to a new, unsent question,
    // not to a previously selected task restored from that account's storage.
    selected=null;historical=null;draft.composerTexts.new=retained.text;
    artifactsOpen=false;stage=0;
    composerMemory.set('new',{materials:retained.materials,pending:null});
  }
  restoreComposerDraft();render();watch();workspaceReady=true;remember();
  try{window.webkit?.messageHandlers?.researchhelper?.postMessage({action:'workspace-state',ready:true})}catch{}
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
  cancelTaskSelection();
  if(workspaceReady&&!leaving)captureDraft();
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

document.addEventListener('load',event=>{if(event.target.id!=='report-frame'||!selected)return;const profile=draft.reportStyles?.[selected.id+':'+(selected.delivery?.version??selected.version)];if(profile){try{event.target.contentWindow.RHVisuals?.applyProfile(profile)}catch{}}},true);
