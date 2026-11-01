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
function versionJudgments(task,basis,current){
  if(!basis?.theses?.length)return '<p>该版未形成可核验论点。</p>';
  return basis.theses.map(t=>'<article class="thesis">'+statusTag(t.judgment)+'<h3>'+esc(t.title)+'</h3><p>'+esc(t.investment_meaning)+'</p><p>'+esc(t.limitation)+'</p>'+(current?'<div class="refs">'+(t.support||[]).map(ref=>evidenceButton(task,ref)).join('')+'</div>':'')+'</article>').join('');
}
function calculationDifferences(change){
  const delta=change.calculation_changes;
  if(!delta)return '';
  if(!delta.baseline_available)return '<h3>计算变化</h3><p>原版本未留存计算基准，无法核对变化。</p>';
  const rows=change.calculation_rows||[];
  if(!rows.length)return '<h3>计算变化</h3><p>已留存计算未发生变化。</p>';
  return '<h3>计算变化</h3>'+rows.map(row=>'<article class="calculation-change"><h4>'+esc(row.status)+'：'+esc(calculationName(row.after||row.before))+'</h4>'+row.details.map(detail=>'<div class="calculation-change-field"><h5>'+esc(detail.label)+'</h5><dl><div><dt>原版本'+esc(change.prior_version)+'</dt><dd>'+esc(detail.before_text)+'</dd></div><div><dt>本版本'+esc(change.research_version)+'</dt><dd>'+esc(detail.after_text)+'</dd></div></dl></div>').join('')+'</article>').join('');
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
const original=prior?'<section class="section"><h2>原验证条件，版本'+esc(prior.version)+'</h2>'+trackingConditions(prior.conditions||[],'原版本未登记验证条件。',true)+'</section>':'';
const current=conditions.length?'<section class="section"><h2>'+(published?'验证条件':'待发布条件')+(prior?'，版本'+esc(task.version):'')+'</h2>'+trackingConditions(conditions,'',published)+'</section>':'<section class="tracking-empty"><div><h2>暂无跟踪条件</h2><p>需要持续复核时，在对话中补充观察指标与触发条件。</p></div><button type="button" class="desk-action" data-action="tracking-input">补充跟踪要求</button></section>';
let difference='';
if(change){
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
}else if(prior){difference='<p>本版尚未形成变化记录。</p>';}
const changes=change||prior?'<section class="section tracking-comparison"><div class="section-heading"><h2>跨版本变化</h2><button type="button" class="desk-action" data-action="versions">查看版本</button></div>'+difference+'<p class="muted">记录修订不等于判断兑现，价格表现与机制分别复核。</p></section>':'';
const revisions=Array.isArray(task.revisions)?task.revisions:[];
const history=revisions.length?'<details class="tracking-history desk-disclosure"><summary>版本记录</summary><ol>'+revisions.map(r=>'<li><span>'+esc(r.action)+'</span><time datetime="'+esc(r.time)+'" title="北京时间">'+esc(cutoffLabel(r.time))+'</time></li>').join('')+'</ol></details>':'';
return '<div class="desk-view tracking-workspace desk-tracking"><header class="desk-view-heading"><h2>跟踪复核</h2></header>'+current+judgmentReview(task)+original+changes+history+'</div>';
};
})(window);
