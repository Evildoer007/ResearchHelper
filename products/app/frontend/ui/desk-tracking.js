/* Pure read-only renderer. ctx callbacks format saved records only:
 * esc(value), statusTag(state), evidenceButton(task, ref),
 * calculationButton(calculation), calculationName(calculation), cutoffLabel(time).
 */
(function(global){
'use strict';
const views=global.RHDeskViews=global.RHDeskViews||{};
views.tracking=function(task,ctx){
const {esc,statusTag,evidenceButton,calculationButton,calculationName,cutoffLabel}=ctx;
function trackingConditions(conditions,empty,published){
  return conditions.length?conditions.map(t=>'<article class="thesis"><h3>'+esc(t.indicator)+'</h3><p>'+esc(t.condition)+'</p><p>来源：'+esc(t.source)+'；窗口：'+esc(t.window)+'</p>'+(t.benchmark?'<p>比较基准：'+esc(t.benchmark)+'</p>':'')+'<span class="status">'+esc(t.status||(published?'已登记条件':'待发布条件'))+'</span></article>').join(''):'<p>'+esc(empty)+'</p>';
}
const reportConditions=global.RHResearchData.reportConditions,validRule=global.RHResearchData.validTrackingRule;
function reportConditionSection(rows,title,current){
 if(!Array.isArray(rows))return '';
 const items=rows.filter(row=>row&&typeof row.title==='string'&&Number.isInteger(row.logic_index)&&row.logic_index>0&&
  (typeof row.condition==='string'&&row.condition.trim()||validRule(row.tracking)));
 if(!items.length)return '';
 const content=items.map(row=>{
  const rule=validRule(row.tracking)?row.tracking:null;
  const premise=typeof row.condition==='string'?row.condition.trim():'';
  const heading='<div class="section-heading"><h3>'+esc(row.title)+'</h3>'+(rule?'<span class="status">待主动复核</span>':'')+'</div>';
  const condition=rule&&rule.condition!==premise?'<p>'+esc(rule.condition)+'</p>':'';
  const fields=rule?[['观察指标',rule.indicator],['观察窗口',rule.window],['资料来源',rule.source],...(rule.benchmark?[['比较基准',rule.benchmark]]:[])]:[];
  const metadata=fields.length?'<dl>'+fields.map(([label,value])=>'<div><dt>'+label+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>':'';
  return '<article class="report-condition">'+heading+(premise?'<p>'+esc(premise)+'</p>':'')+condition+metadata+'</article>';
 }).join('');
 const action=current?'<button type="button" class="button-secondary desk-action" data-action="tracking-input">补充跟踪要求</button>':'';
 return '<section class="section report-conditions"><div class="section-heading"><h2>'+esc(title)+'</h2>'+action+'</div>'+content+'</section>';
}
function versionJudgments(task,basis,current){
  if(!basis?.theses?.length)return '<p>该版未形成可核验论点。</p>';
  return basis.theses.map(t=>'<article class="thesis">'+statusTag(t.judgment)+'<h3>'+esc(t.title)+'</h3><p>'+esc(t.investment_meaning)+'</p><p>'+esc(t.limitation)+'</p>'+(current?'<div class="refs">'+(t.support||[]).map(ref=>evidenceButton(task,ref)).join('')+'</div>':'')+'</article>').join('');
}
function reportComparison(change){
 const comparison=change?.draft_report_comparison;
 if(comparison?.version!==1||comparison.baseline_available===false)return '';
 function valid(basis,version){
  return basis&&basis.schema==='researchhelper.report-comparison-basis'&&basis.version===1&&
   basis.task_id===task.id&&Number.isInteger(basis.research_version)&&basis.research_version===version&&
   typeof basis.cutoff==='string'&&typeof basis.conclusion==='string'&&typeof basis.title==='string'&&
   typeof basis.basis_hash==='string'&&/^[a-f0-9]{64}$/.test(basis.basis_hash)&&Array.isArray(basis.logics)&&
   basis.logics.every(logic=>logic&&Number.isInteger(logic.position)&&logic.position>0&&
    ['title','claim','implication'].every(key=>typeof logic[key]==='string'));
 }
 if(!valid(comparison.prior,prior?.version)||!valid(comparison.current,task.version)){
  return '<section class="section report-comparison"><h2>结论对照</h2><p>两版正文的任务或版本不一致，暂不展示。</p></section>';
 }
 function column(basis,role){
  const title=role==='prior'?'原结论':'本版结论';
  const logics=basis.logics.map(logic=>{
   const statements=[...new Set([logic.claim,logic.implication].filter(text=>text.trim()))];
   return '<li value="'+logic.position+'" data-logic-position="'+logic.position+'"><h4>'+esc(logic.title)+'</h4>'+statements.map(text=>'<p>'+esc(text)+'</p>').join('')+'</li>';
  }).join('');
  return '<article class="comparison-column" data-version-role="'+role+'"><header><h3>'+title+'，版本'+esc(basis.research_version)+'</h3><time class="comparison-cutoff" datetime="'+esc(basis.cutoff)+'" title="研究截止时点">'+esc(cutoffLabel(basis.cutoff))+'</time></header><p class="comparison-conclusion">'+esc(basis.conclusion)+'</p>'+(logics?'<ol class="comparison-logics">'+logics+'</ol>':'')+'</article>';
 }
 return '<section class="section report-comparison" aria-labelledby="report-comparison-heading"><h2 id="report-comparison-heading">结论对照</h2><div class="report-comparison-pair">'+column(comparison.prior,'prior')+column(comparison.current,'current')+'</div></section>';
}
function calculationDifferences(change){
  const delta=change.calculation_changes;
  if(!delta)return '';
  if(!delta.baseline_available)return '<h3>计算变化</h3><p>原版本未留存计算基准，无法核对变化。</p>';
  const rows=change.calculation_rows||[];
  if(!rows.length)return '<h3>计算变化</h3><p>已留存计算未发生变化。</p>';
  return '<h3>计算变化</h3>'+rows.map(row=>'<article class="calculation-change"><h4>'+esc(row.status)+'：'+esc(calculationName(row.after||row.before))+'</h4>'+row.details.map(detail=>'<div class="calculation-change-field"><h5>'+esc(detail.label)+'</h5><dl><div><dt>原版本'+esc(change.prior_version)+'</dt><dd>'+esc(detail.before_text)+'</dd></div><div><dt>本版本'+esc(change.research_version)+'</dt><dd>'+esc(detail.after_text)+'</dd></div></dl></div>').join('')+'</article>').join('');
}
function observationDifferences(comparison){
  if(comparison?.version!==1||!Array.isArray(comparison.rows)||!Array.isArray(comparison.columns))return '<p>该版本未保存草稿读数对照。</p>';
  const summary='<p>'+esc(comparison.summary)+'</p>';
  if(!comparison.rows.length)return summary;
  const headings=comparison.columns.map(cell=>'<th role="columnheader" scope="col">'+esc(cell)+'</th>').join('');
  const rows=comparison.rows.map(row=>'<tr role="row">'+row.cells.map((cell,column)=>{
    const label=column===2||column===3?'<span class="observation-delta-label" aria-hidden="true">'+esc(comparison.columns[column])+'</span>':'';
    return '<td role="cell">'+label+'<span>'+esc(cell)+'</span></td>';
  }).join('')+'</tr>').join('');
  return summary+'<div class="table-wrap observation-delta" tabindex="0" role="region" aria-label="版本读数对照"><table role="table"><thead role="rowgroup"><tr role="row">'+headings+'</tr></thead><tbody role="rowgroup">'+rows+'</tbody></table></div>';
}
function judgmentReview(task){
  if(task.judgment_review_contract!==1)return '';
  const labels={facts:'事实变化',mechanism:'机制复核',expectations:'预期差',price:'价格表现'};
  const records=Array.isArray(task.judgment_reviews)?task.judgment_reviews.filter(belongs):(task.judgment_reviews||[]),pending=Array.isArray(task.judgment_review_pending)?task.judgment_review_pending.filter(belongs):(task.judgment_review_pending||[]);
  if(!Array.isArray(records)||!Array.isArray(pending)||records.some(record=>
    !record||typeof record.prior_title!=='string'||!Array.isArray(record.current_theses)||
    record.current_theses.some(item=>!item||typeof item.title!=='string'||typeof item.judgment!=='string')||
    Object.keys(labels).some(key=>{
      const item=record.dimensions?.[key];
      return !item||typeof item.state!=='string'||typeof item.answer!=='string'||
        !Array.isArray(item.refs)||!Array.isArray(item.calculation_refs);
    }))||pending.some(item=>!item||typeof item.reason!=='string')){
    return '<section class="section judgment-review-section"><h2>原判断复核</h2><p>本版复核记录不完整，暂不展示判断状态。</p></section>';
  }
  const reviewed=records.map(record=>'<article class="thesis judgment-review"><div class="section-heading"><h3>'+esc(record.prior_title)+'</h3>'+statusTag(record.assessment)+'</div><p>原判断：'+esc(record.prior_judgment)+'；对应本版：'+esc(record.current_theses.map(t=>t.title+'，'+t.judgment).join('；')||'未形成替代判断')+'</p><dl class="judgment-dimensions">'+Object.entries(labels).map(([key,label])=>{
    const item=record.dimensions[key];
    const methods=(item.calculation_refs||[]).map(ref=>{
      const calculation=(task.calculations||[]).find(value=>belongs(value)&&value.id===ref);
      return calculation?calculationButton(calculation):'';
    }).join('');
    const refs=(item.refs||[]).map(ref=>evidenceButton(task,ref)).join('');
    return '<div><dt>'+label+'</dt><dd><strong>'+esc(item.state)+'</strong>'+(item.comparison_kind?'<p>'+esc(item.comparison_kind)+'</p>':'')+'<p>'+esc(item.answer)+'</p>'+(refs||methods?'<div class="refs">'+refs+methods+'</div>':'')+'</dd></div>';
  }).join('')+'</dl></article>').join('');
  const unresolved=pending.length?'<div class="judgment-pending"><h3>待复核</h3><ul>'+pending.map(item=>'<li>'+(item.title?esc(item.title)+'：':'')+esc(item.reason)+'</li>').join('')+'</ul></div>':'';
  return '<section class="section judgment-review-section"><h2>原判断复核</h2>'+reviewed+unresolved+(!records.length&&!pending.length?'<p>本版尚未完成原判断复核。</p>':'')+'</section>';
}

const belongs=item=>item&&(!item.task_id||item.task_id===task.id)&&(item.research_version==null||item.research_version===task.version);
const prior=task.update_from,change=belongs(task.changes)?task.changes:null;
const conditions=Array.isArray(task.tracking)?task.tracking.filter(belongs):[];
const published=Boolean(task.delivery&&task.delivery.version===task.version);
const draft=belongs(task.draft_report)?task.draft_report:null;
const savedConditions=reportConditions(draft);
const priorDraft=change?.draft_conditions?.version===1?change.draft_conditions.prior:prior?.draft_conditions;
const original=(Array.isArray(prior?.conditions)&&prior.conditions.length?'<section class="section"><h2>原验证条件，版本'+esc(prior.version)+'</h2>'+trackingConditions(prior.conditions,'',true)+'</section>':'')+
 reportConditionSection(priorDraft,'原报告条件，版本'+(prior?.version??change?.prior_version),false);
const current=conditions.length?'<section class="section"><h2>'+(published?'验证条件':'待发布条件')+(prior?'，版本'+esc(task.version):'')+'</h2>'+trackingConditions(conditions,'',published)+'</section>':savedConditions.length?reportConditionSection(savedConditions,prior?'本版报告条件，版本'+task.version:'报告条件',true):'<section class="tracking-empty"><div><h2>暂无跟踪条件</h2><p>需要持续复核时，在对话中补充观察指标与触发条件。</p></div><button type="button" class="button-secondary desk-action" data-action="tracking-input">补充跟踪要求</button></section>';
let difference='';
if(change&&task.draft_report){
 difference=observationDifferences(change.draft_observations);
}else if(change){
 const records=[['added','新增记录'],['revised','修订记录'],['missing','本版未取得的原记录']].map(([key,title])=>{
 const basis=key==='missing'?change.prior_basis:change.current_basis;
 const names=(change[key]||[]).map(id=>{const item=basis?.evidence?.find(e=>e.id===id);return item?item.object+'：'+(item.text||String(item.value??'')+' '+(item.unit||'')):id});
 return names.length?'<li><strong>'+title+'</strong><p>'+esc(names.join('；'))+'</p></li>':'';
 }).join('');
 difference=records?'<ul class="tracking-changes">'+records+'</ul>':'<p>未登记新增或修订记录。</p>';
 difference+=calculationDifferences(change);
 if(task.judgment_review_contract!==1){
 if(change.prior_basis&&change.current_basis)difference+='<h3>版本'+esc(change.prior_version)+'判断</h3>'+versionJudgments(task,change.prior_basis,false)+'<h3>版本'+esc(task.version)+'判断</h3>'+versionJudgments(task,change.current_basis,true);
 else difference+='<p>原判断：'+esc((change.prior_judgments||[]).join('、')||'未登记')+'；本版判断：'+esc((change.current_judgments||[]).join('、')||'未登记')+'。</p>';
 }
 difference+='<p class="muted">记录修订不等于判断兑现，价格表现与机制分别复核。</p>';
}else if(prior){difference='<p>本版尚未形成变化记录。</p>';}
const changes=change||prior?'<section class="section tracking-comparison"><h2>'+(task.draft_report?'数据变化':'跨版本变化')+'</h2>'+difference+'</section>':'';
const revisions=Array.isArray(task.revisions)?task.revisions:[];
const history=revisions.length?'<details class="tracking-history desk-disclosure"><summary>版本记录</summary><ol>'+revisions.map(r=>'<li><span>'+esc(r.action)+'</span><time datetime="'+esc(r.time)+'" title="北京时间">'+esc(cutoffLabel(r.time))+'</time></li>').join('')+'</ol></details>':'';
const tracking=current+judgmentReview(task)+original;
const sections=reportComparison(change)+(change?.draft_observations?changes+tracking:tracking+changes);
const versionAction=change||prior?'<div class="desk-heading-actions"><button type="button" class="button-secondary desk-action" data-action="versions">查看版本</button></div>':'';
return '<div class="desk-view tracking-workspace desk-tracking"><header class="desk-view-heading"><h2>跟踪复核</h2>'+versionAction+'</header>'+sections+history+'</div>';
};
})(window);
