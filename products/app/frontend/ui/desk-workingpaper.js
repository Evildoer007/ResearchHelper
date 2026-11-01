/* A research notebook: argument, evidence and falsification share one reading surface. */
(function(global){
'use strict';
const list=value=>Array.isArray(value)?value.filter(Boolean):[];
global.RHDeskViews=global.RHDeskViews||{};
global.RHDeskViews.workingpaper=function(task,ctx){
 const D=global.RHResearchData,current=item=>D.scope([item],task).length>0;
 const scoped=items=>D.scope(items,task);
 const {esc}=ctx,p=text=>text?'<p>'+esc(text)+'</p>':'';
 const questions=new Map(scoped(task.questions).map(item=>[item.id,item])),evidence=new Map(scoped(task.evidence).map(item=>[item.id,item])),usedQuestions=new Set(),usedFacts=new Set();
 const paper=D.paper(task),entries=[];
 if(scoped(paper?.logics).length){
  scoped(paper.logics).forEach((logic,index)=>{
   const calculations=scoped(paper.calculations).filter(item=>list(logic.calculation_refs).includes(item.id));
   const ids=new Set([...D.draftDataRefs(logic),...calculations.flatMap(item=>list(item.data_refs))]);ids.forEach(id=>usedFacts.add(id));
   const facts=D.draftFacts(task).filter(item=>ids.has(item.id));
   const missing=[...ids].filter(id=>!facts.some(item=>item.id===id)).concat(list(logic.calculation_refs).filter(id=>!calculations.some(item=>item.id===id)));
   const knowledgeEntries=D.knowledgeEntries(task,logic);
   const theory=knowledgeEntries.length?ctx.knowledgeView({entries:knowledgeEntries},{showPurpose:false}):'';
   entries.push({kind:'draft',index,title:logic.title||'研究判断',displayHeading:logic.display_heading,judgment:logic.implication,analysis:p(logic.analysis),theory,notes:list(logic.reference_notes).filter(note=>typeof note==='string'),risk:logic.risk,items:D.observations(facts),calculations,missing});
  });
 }
 {
  function add(entry,thesis){
   const visited=new Set(),ordered=[];
   function visit(id){if(visited.has(id)||!questions.has(id))return;visited.add(id);usedQuestions.add(id);const question=questions.get(id);list(question.dependencies).forEach(visit);ordered.push(question);}
   (thesis?list(entry.question_ids):[entry.id]).forEach(visit);
   const calculations=scoped(task.calculations).filter(item=>list(entry.calculation_refs).includes(item.id)||ordered.some(question=>list(question.calculation_refs).includes(item.id)));
   const refs=[...new Set([...list(entry.support),...list(entry.counter),...list(entry.refs),...ordered.flatMap(question=>list(question.refs)),...calculations.flatMap(item=>list(item.input_refs))])];
   const facts=refs.filter(id=>evidence.has(id)).map(id=>evidence.get(id)),missing=refs.filter(id=>!evidence.has(id)).concat([...new Set([...list(entry.calculation_refs),...ordered.flatMap(question=>list(question.calculation_refs))])].filter(id=>!calculations.some(item=>item.id===id)));
   const knowledge=task.knowledge||{},knowledgeEntries=list(knowledge.entries).filter(item=>list(knowledge.selection_reasons?.[item.id]?.question_ids).some(id=>visited.has(id))||calculations.some(calc=>calc.method_binding?.knowledge_id===item.id));
   const theory=knowledgeEntries.length?ctx.knowledgeView({...knowledge,entries:knowledgeEntries,unmatched:[],unmatched_questions:[]}):'';
   const analysis=ordered.filter(question=>question.status==='已回答'&&question.answer&&(thesis||question.id!==entry.id)).map(question=>'<div data-desk-question="'+esc(question.id)+'">'+(thesis?'<h4>'+esc(question.question)+'</h4>':'')+p(ctx.displayAnswer(task,question))+'</div>').join('');
   entries.push({kind:thesis?'thesis':'answer',entry,index:entries.length,title:thesis?entry.title:entry.question,judgment:thesis?entry.investment_meaning:ctx.displayAnswer(task,entry),analysis,theory,risk:entry.limitation,items:D.observations(facts,'evidence'),calculations,missing,support:list(entry.support),counter:list(entry.counter)});
  }
  scoped(task.theses).forEach(entry=>add(entry,true));
  scoped(task.questions).filter(item=>!usedQuestions.has(item.id)&&item.status==='已回答'&&item.answer).forEach(entry=>add(entry,false));
 }
 const mixedKinds=new Set(entries.map(entry=>entry.kind)).size>1;
 const nav=entries.length>1?'<nav class="paper-navigation" aria-label="底稿内容">'+entries.map((entry,index)=>'<button type="button" data-paper-target="paper-logic-'+index+'" title="'+esc(entry.title)+'"><span>'+String(index+1)+'</span><span class="paper-navigation-title">'+esc(entry.title)+'</span></button>').join('')+'</nav>':'';
 const articles=entries.map((entry,index)=>{
  const groups=D.groups(entry.items,task),data=groups.map(group=>D.grid(group,task,ctx,{compact:true})).join('');
  const roles=entry.kind==='thesis'?['support','counter'].map(role=>{const items=entry.items.filter(item=>entry[role].includes(item.fact.id));return items.length?'<section class="paper-evidence-role"><h3>'+(role==='support'?'支持证据':'反证证据')+'</h3>'+items.map(item=>p(item.fact.text||item.label)).join('')+D.sourceNotes(items,task,ctx)+'</section>':'';}).join(''):'';
  const excerpts=entry.items.filter(item=>!D.numeric(item)).map(item=>'<div class="paper-excerpt">'+p(item.fact.text||item.raw||item.label)+'</div>').join('');
  const methods=entry.calculations.map(calc=>D.calcButton(calc,task,ctx,entry.kind==='draft')).join('');
  const status=entry.kind==='thesis'&&entry.entry.judgment?ctx.statusTag(entry.entry.judgment):'';
  const sharedHeading=entry.kind==='draft'&&typeof entry.displayHeading==='string'&&entry.displayHeading;
  const title='<header class="paper-argument-heading">'+(sharedHeading?'':'<span class="paper-argument-index">'+(entry.kind==='answer'?'问题':'逻辑')+(index+1)+'</span>')+'<h2 id="paper-title-'+index+'">'+esc(sharedHeading||entry.title)+'</h2>'+(mixedKinds?'<span class="paper-entry-kind">'+({draft:'研究草稿',thesis:'正式论点',answer:'问题回答'}[entry.kind])+'</span>':'')+status+'</header>';
  const theory=entry.theory?'<details class="paper-theory desk-disclosure"><summary>理论依据</summary>'+entry.theory+'</details>':'';
  const analysis=entry.analysis?'<section class="paper-reasoning"><h3>分析依据</h3>'+entry.analysis+'</section>':'';
  const missing=(entry.missing?.length?'<p class="paper-unlinked">'+esc('引用'+entry.missing.join('、')+'未找到对应记录。')+'</p>':'')+list(entry.notes).map(note=>'<p class="paper-unlinked">'+esc(note)+'</p>').join('');
  const basis=data||excerpts||methods?'<aside class="paper-basis" aria-label="'+esc(entry.title)+'的数据与计算">'+(data||excerpts?'<section><h3>数据对照</h3>'+data+excerpts+'</section>':'')+(methods?'<section class="paper-calculations"><h3>计算结果</h3>'+methods+'</section>':'')+D.sourceNotes(entry.items,task,ctx)+missing+'</aside>':missing;
  const footer=entry.risk?'<section class="paper-validation"><h3>风险与限制</h3>'+p(entry.risk)+'</section>':'';
  return '<article class="paper-argument" id="paper-logic-'+index+'" tabindex="-1" data-reading-key="'+esc(entry.kind==='draft'?'draft-logic:'+entry.index:entry.kind+':'+entry.entry.id)+'"'+(entry.kind==='thesis'?' data-desk-thesis="'+esc(entry.entry.id)+'"':'')+' aria-labelledby="paper-title-'+index+'">'+title+'<div class="paper-layout"><div class="paper-narrative">'+(entry.judgment?'<p class="paper-judgment">'+esc(entry.judgment)+'</p>':'')+analysis+theory+roles+'</div>'+basis+'</div>'+footer+'</article>';
 }).join('');
 const remaining=paper?D.draftFacts(task).filter(item=>!usedFacts.has(item.id)):[];
 const remainingItems=D.observations(remaining);
 const extra=remaining.length?'<details class="paper-supplement desk-disclosure"><summary>'+(entries.length?'未关联的取数':'已取得的数据')+'</summary>'+D.groups(remainingItems,task).map(group=>D.grid(group,task,ctx)).join('')+remainingItems.filter(item=>!D.numeric(item)).map(item=>'<div class="paper-excerpt">'+p(item.fact.text||item.raw||item.label)+D.sourceNotes([item],task,ctx)+'</div>').join('')+D.sourceNotes(remainingItems.filter(D.numeric),task,ctx)+'</details>':'';
 const usedCalculations=new Set(entries.filter(entry=>entry.kind==='draft').flatMap(entry=>entry.calculations.map(calc=>calc.id)));
 const otherCalculations=scoped(paper?.calculations).filter(calc=>!usedCalculations.has(calc.id));
 const calculationSupplement=otherCalculations.length?'<details class="paper-supplement desk-disclosure"><summary>未关联的计算</summary>'+otherCalculations.map(calc=>D.calcButton(calc,task,ctx,true)).join('')+'</details>':'';
 const unadopted=list(task.rejected_evidence).length?'<details class="paper-supplement desk-disclosure"><summary>未采用材料</summary>'+ctx.collectionWorkingpaper(task)+'</details>':'';
 const notices=[...list(task.draft_notices).map(item=>item.message),...list(paper?.calculation_notes).map(item=>item.label+'：'+item.reason)].filter(Boolean);
 const notice=notices.length?'<details class="paper-supplement desk-disclosure"><summary>底稿备注</summary>'+notices.map(p).join('')+'</details>':'';
 const corrections=list(task.report_corrections).filter(item=>item.task_id===task.id&&item.version===(task.delivery?.version??task.version)&&typeof item.reason==='string'&&item.reason.trim());
 const correctionNotes=corrections.length?'<section class="paper-corrections" aria-label="报告更正"><h3>报告更正</h3><ol>'+corrections.map(item=>'<li>'+p(item.reason)+'</li>').join('')+'</ol></section>':'';
 const empty=!entries.length&&!remaining.length&&!otherCalculations.length?'<div class="empty"><h2>底稿尚未形成</h2><p>研究形成后，在这里对照判断与依据。</p></div>':'';
 return '<section class="desk-view desk-workingpaper desk-workingpaper--notebook" aria-label="研究底稿"><header class="desk-view-heading paper-notebook-heading"><h2>研究底稿</h2>'+(entries.length?'<span class="desk-view-meta">'+entries.length+'条判断</span>':'')+'</header>'+correctionNotes+nav+articles+extra+calculationSupplement+unadopted+notice+empty+'</section>';
};
})(window);
