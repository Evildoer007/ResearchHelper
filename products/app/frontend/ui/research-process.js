/* OH process disclosure and orb, bound only to RH's actual research state.
 * Presentation never starts, cancels, resumes or changes a research task. */
(function () {
"use strict";
const labels=Object.freeze({'待开始':'研究待开始','运行中':'研究正在运行','已完成':'研究已完成',
  '部分完成':'研究部分完成','失败':'研究未完成','已取消':'研究已取消'});
const modelPhases=Object.freeze({plan:'正在规划研究',acquire:'正在查找研究资料',read:'正在选择材料',extract:'正在提取材料',
  bind_calculations:'正在核对计算输入',analyze:'正在分析证据',supplement:'正在规划补证'});
const modelStages=Object.freeze({'连接服务':'正在连接模型服务','读取响应':'正在等待模型响应'});
function pendingModel(task){
  if(task.status!=='运行中'||!Number.isInteger(task.batch))return null;
  const events=Array.isArray(task.ledger?.dispatches)?task.ledger.dispatches:[];
  // A completed call, another batch or a later source request must not leave
  // an old model phase displayed as if it were still executing.
  for(let index=events.length-1;index>=0;index--){
    const event=events[index];if(event?.batch!==task.batch)continue;
    if(event.kind!=='model_calls'||event.status!=='已派发'||!Object.hasOwn(modelPhases,event.research_phase))return null;
    return {label:modelPhases[event.research_phase],title:Object.hasOwn(modelStages,event.stage)?modelStages[event.stage]:''};
  }
  return null;
}
function html(task,{progress='',actions=''}={}) {
  const nodes=Array.isArray(task.nodes)?task.nodes:[],running=task.status==='运行中';
  const current=running?nodes.find(node=>node.status==='运行中'):null;
  const model=pendingModel(task);
  const panel=document.createElement('article');
  panel.className='message message--assistant message--process rh-process';
  panel.dataset.runtimeProcess='true';panel.dataset.terminal=String(!running);
  panel.dataset.hasDetails=String(nodes.length>0);panel.dataset.outcome=task.status==='失败'?'failed':running?'running':'terminal';
  const details=document.createElement('details');details.className='chat-progress';
  const summary=document.createElement('summary');
  if(running){const slot=document.createElement('span');slot.dataset.processOrb='true';slot.setAttribute('aria-hidden','true');summary.append(slot)}
  const label=document.createElement('span');label.className='runtime-state-label';
  label.setAttribute('role','status');label.setAttribute('aria-live','polite');label.setAttribute('aria-atomic','true');
  label.textContent=model?.label||current?.title||labels[task.status]||String(task.status||'研究状态待确认');
  if(model?.title)label.title=model.title;
  summary.append(label);
  details.append(summary);
  if(nodes.length){
    const state=document.createElement('p');state.className='process-state';state.textContent=progress;details.append(state);
    const events=document.createElement('ol');events.className='process-events';events.dataset.processTimeline='true';
    for(const node of nodes){
      const row=document.createElement('li');row.dataset.status=node.status==='失败'?'failed':node.status==='已取消'?'cancelled':'normal';
      const title=document.createElement('span');title.textContent=String(node.title||node.id||'研究步骤');
      const status=document.createElement('span');status.textContent=String(node.status||'状态待确认');
      row.append(title,document.createTextNode('：'),status);events.append(row);
    }
    details.append(events);
  }
  const view=document.createElement('button');view.className='button-secondary';view.type='button';view.dataset.stage='1';view.textContent='查看执行与证据';
  details.append(view);panel.append(details);
  if(task.status==='失败'&&task.error){
    const error=document.createElement('p');error.className='process-failure';error.setAttribute('role','alert');error.textContent=String(task.error);panel.append(error);
  }
  if(actions&&['待开始','运行中','失败','已取消'].includes(task.status)){
    const recovery=document.createElement('div');recovery.className='rh-process__recovery';
    // Only the existing local actions(task) renderer supplies these controls,
    // never model text, error text or a provider response.
    recovery.innerHTML=actions;panel.append(recovery);
  }
  return panel.outerHTML;
}
function reconcile(previous,next){
  // Like OH's retained reasoning disclosure, update the live content without
  // detaching its summary, status region or unchanged evidence control.
  const details=previous.querySelector('.chat-progress'),incoming=next.querySelector('.chat-progress');
  const summary=details?.querySelector('summary'),label=summary?.querySelector('.runtime-state-label');
  if(!details||!incoming||!summary||!label)return next;
  Object.assign(previous.dataset,next.dataset);
  const nextLabel=incoming.querySelector('.runtime-state-label');
  if(label.textContent!==nextLabel.textContent)label.textContent=nextLabel.textContent;
  if(nextLabel.hasAttribute('title'))label.title=nextLabel.title;else label.removeAttribute('title');
  const slot=summary.querySelector('[data-process-orb]'),nextSlot=incoming.querySelector('[data-process-orb]');
  if(nextSlot&&!slot)summary.insertBefore(nextSlot,label);
  else if(!nextSlot&&slot)slot.remove();
  const view=details.querySelector('[data-stage="1"]');
  for(const selector of ['.process-state','.process-events']){
    const existing=details.querySelector(selector),fresh=incoming.querySelector(selector);
    if(!fresh){existing?.remove();continue}
    if(existing){if(existing.outerHTML!==fresh.outerHTML)existing.replaceWith(fresh)}
    else details.insertBefore(fresh,selector==='.process-state'?(details.querySelector('.process-events')||view):view);
  }
  for(const selector of ['.process-failure','.rh-process__recovery']){
    const existing=previous.querySelector(selector),fresh=next.querySelector(selector);
    if(!fresh){existing?.remove();continue}
    if(existing){if(existing.outerHTML!==fresh.outerHTML)existing.replaceWith(fresh)}
    else previous.insertBefore(fresh,selector==='.process-failure'?previous.querySelector('.rh-process__recovery'):null);
  }
  return previous;
}
function create(){
  let orb=null,key=null,disposed=false;
  const clear=()=>{orb?.destroy();orb=null;key=null};
  const sync=(host,task)=>{
    if(disposed)return;
    const slot=host.querySelector('[data-process-orb]');
    if(!slot||!task||task.status!=='运行中'){clear();return}
    if(!orb||key!==task.id){clear();orb=window.RHThinkingOrb.createThinkingOrb({state:'shaping',size:20,className:'thinking-orbs process-geometry'});key=task.id}
    // OH keeps this compact indicator geometric. The adjacent label carries
    // the actual phase; no automatic geometry playlist or made-up progress.
    orb.element.dataset.activityState=String((task.nodes||[]).find(node=>node.status==='运行中')?.id||'working');
    orb.element.setAttribute('aria-hidden','true');if(slot.firstElementChild!==orb.element)slot.replaceChildren(orb.element);
  };
  return Object.freeze({html,reconcile,sync,clear,destroy(){if(disposed)return;disposed=true;clear()}});
}
window.RHResearchProcess=Object.freeze({create});
})();
