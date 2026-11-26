/* OH thinking orb and live status, bound only to RH's actual research state.
 * Presentation never starts, cancels, resumes or changes a research task. */
(function () {
"use strict";
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
function html(task) {
  const nodes=Array.isArray(task.nodes)?task.nodes:[],running=task.status==='运行中';
  const current=running?nodes.find(node=>node.status==='运行中'):null;
  const model=pendingModel(task);
  const presentation=window.RHResearchData.taskStatus(task);
  const blocked=task.batch_settling!==true?task.recovery_block?.reason:null;
  const panel=document.createElement('article');
  panel.className='message message--assistant message--process rh-process';
  panel.dataset.runtimeProcess='true';panel.dataset.terminal=String(!running);
  panel.dataset.outcome=task.status==='失败'?'failed':running?'running':'terminal';
  const summary=document.createElement('div');
  summary.className='rh-process__summary';
  if(running){const slot=document.createElement('span');slot.dataset.processOrb='true';slot.setAttribute('aria-hidden','true');summary.append(slot)}
  const label=document.createElement('span');label.className='runtime-state-label';
  label.setAttribute('role','status');label.setAttribute('aria-live','polite');label.setAttribute('aria-atomic','true');
  label.textContent=model?.label||current?.title||(blocked&&task.status==='待开始'?'研究已暂停':presentation.processLabel);
  if(model?.title||presentation.title)label.title=model?.title||presentation.title;
  summary.append(label);
  panel.append(summary);
  const reason=blocked||(task.status==='失败'?task.error:null);
  if(reason){
    const error=document.createElement('p');error.className='process-failure';error.setAttribute('role','alert');error.textContent=String(reason);panel.append(error);
  }
  return panel.outerHTML;
}
function reconcile(previous,next){
  // Keep the live region and orb attached while polling updates the phase.
  Object.assign(previous.dataset,next.dataset);
  const summary=previous.firstElementChild,nextSummary=next.firstElementChild;
  const label=summary.querySelector('.runtime-state-label'),nextLabel=nextSummary.querySelector('.runtime-state-label');
  if(label.textContent!==nextLabel.textContent)label.textContent=nextLabel.textContent;
  if(nextLabel.hasAttribute('title'))label.title=nextLabel.title;else label.removeAttribute('title');
  const slot=summary.querySelector('[data-process-orb]'),nextSlot=nextSummary.querySelector('[data-process-orb]');
  if(nextSlot&&!slot)summary.insertBefore(nextSlot,label);
  else if(!nextSlot&&slot)slot.remove();
  const existing=previous.querySelector('.process-failure'),fresh=next.querySelector('.process-failure');
  if(!fresh)existing?.remove();
  else if(!existing)previous.append(fresh);
  else if(existing.textContent!==fresh.textContent)existing.textContent=fresh.textContent;
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
