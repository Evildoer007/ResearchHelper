/* Task scope and actual output state. This view never starts research. */
(function(global){
'use strict';
const views=global.RHDeskViews=global.RHDeskViews||{};
views.overview=function(task,ctx){
 const {esc,cutoffLabel,edition=task.delivery}=ctx,D=global.RHResearchData,text=value=>esc(value==null?'':String(value));
 const original=task.original_input||{},objects=task.objects||[],questions=D.scope(task.questions,task);
 const required=questions.filter(q=>q.required),optional=questions.filter(q=>!q.required),summary=D.summary(task);
 const input=original.question||task.question||'',single=required.length===1&&String(required[0].question).trim()===String(input).trim();
 const observed=[...new Set(summary.observations.map(item=>item.object).filter(Boolean))];
 const fact=(label,value)=>'<div><dt>'+label+'</dt><dd>'+value+'</dd></div>';
 const facts='<dl class="facts desk-overview-facts">'+(objects.length?fact('研究对象',text(objects.join('、'))):observed.length?fact('已取数对象',text(observed.join('、'))):'')+fact('研究意图',text(task.intent||'尚未登记'))+(task.cutoff?fact('截止时点','<time datetime="'+text(task.cutoff)+'" title="北京时间">'+text(cutoffLabel(task.cutoff))+'</time>'):'')+'</dl>';
 const pending=D.reportCheckpoint(task)?D.taskStatus(task).label:'尚未保存报告';
 const report=edition?'版本'+edition.version+'已保存'+(edition.version===task.version?'':'，本版'+pending):pending;
 const stages=[
  {stage:1,title:'数据来源',value:summary.observations.length?summary.observations.length+'条记录':summary.sources?'已取得'+summary.sources+'份来源':'尚未取得数据'},
  {stage:2,title:'研究底稿',value:summary.logics?summary.logics+'条逻辑':summary.answers?summary.answers+'项分析':'尚未形成分析'},
  {stage:3,title:'研究报告',value:report},
  {stage:4,title:'跟踪复核',value:summary.conditions?summary.conditions+'项条件':'尚未登记条件'},
 ];
 const pipeline='<nav class="desk-pipeline" aria-label="研究产物进度">'+stages.map(item=>'<button type="button" class="desk-flow-step" data-stage="'+item.stage+'" aria-label="查看'+item.title+'，'+text(item.value)+'"><span><strong>'+item.title+'</strong><span>'+text(item.value)+'</span></span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button>').join('')+'</nav>';
 const questionRow=q=>'<li data-question-id="'+text(q.id)+'" tabindex="-1">'+text(q.question)+'</li>';
 const questionList=single?'<p class="desk-overview-request" data-question-id="'+text(required[0].id)+'" tabindex="-1">'+text(input)+'</p>':required.length?'<ol class="desk-overview-questions">'+required.map(questionRow).join('')+'</ol>':input?'<p class="desk-overview-request">'+text(input)+'</p>':'<p class="muted">尚未登记研究问题。</p>';
 const optionalList=optional.length?'<details class="desk-disclosure"><summary>可选问题<span>'+optional.length+'项</span></summary><ol class="desk-overview-questions">'+optional.map(questionRow).join('')+'</ol></details>':'';
 const changes=[];
 if(original.question&&task.question&&original.question!==task.question)changes.push('<p>'+text(task.question)+'</p>');
 if(original.intent&&original.intent!==task.intent)changes.push('<p>研究意图由'+text(original.intent)+'调整为'+text(task.intent)+'。</p>');
 if(Array.isArray(original.objects)&&original.objects.join('、')!==objects.join('、'))changes.push('<p>原对象范围：'+text(original.objects.join('、')||'未指定')+'。</p>');
 const adopted=changes.length?'<section class="desk-block"><h3>采用解释</h3>'+changes.join('')+'</section>':'';
 const inputDetails=input&&!single&&required.length?'<details class="desk-disclosure desk-overview-input"><summary>查看原输入</summary><p>'+text(input)+'</p></details>':'';
 const mappings=D.scope(task.identity_resolutions,task);
 const identity=mappings.length?'<details class="desk-disclosure identity-results"><summary>对象识别与来源</summary>'+mappings.map(item=>'<div class="identity-result"><div><strong>'+text(item.query||'原输入')+'</strong><p>'+text(item.status||'尚未核验')+(item.codes?.length?'：'+text(item.codes.join('、')):'')+'</p></div>'+(item.artifact_id?'<button type="button" class="button-secondary desk-action" data-source="'+text(item.artifact_id)+'">查看来源</button>':'')+'</div>'+(item.reason?'<p>'+text(item.reason)+'</p>':'')).join('')+'</details>':task.identity_state?'<details class="desk-disclosure"><summary>对象识别</summary><p>'+text(task.identity_state)+'</p></details>':'';
 const review=task.document_review_origin;
 const reviewActions=review?.task_id?'<div class="desk-heading-actions"><button type="button" class="button-secondary desk-action" data-task="'+text(review.task_id)+'">返回原研究</button>'+(task.status==='待开始'&&!ctx.readOnly?'<button type="button" class="desk-action desk-action--primary" data-action="start">开始核对</button>':'')+'</div>':'';
 const material=ctx.canAddMaterials?'<button type="button" class="button-secondary desk-action" data-action="material">补充原任务材料</button>':'';
 return '<div class="desk-view desk-overview"><header class="desk-view-heading"><h2>研究概览</h2><div class="desk-heading-actions">'+material+'</div></header>'+pipeline+'<section class="desk-block desk-task-brief">'+facts+'</section><section class="desk-block"><h3>'+(single?'研究要求':'研究问题')+'</h3>'+questionList+optionalList+(task.intent==='原件内容核对'?'<p class="desk-note">仅核对原件内容，不形成投资判断。</p>':'')+reviewActions+'</section>'+adopted+'<div class="desk-overview-context">'+inputDetails+identity+'</div></div>';
};
})(window);
