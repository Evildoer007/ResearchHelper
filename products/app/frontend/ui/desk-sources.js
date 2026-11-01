/* Data first: comparable periods share columns, original records stay one click away. */
(function(global){
'use strict';
global.RHDeskViews=global.RHDeskViews||{};
global.RHDeskViews.sources=function(task,ctx){
 const D=global.RHResearchData,{esc}=ctx;
 const current=item=>(!item.task_id||item.task_id===task.id)&&(item.research_version==null||item.research_version===task.version);
 const paper=task.draft_report&&current(task.draft_report)?task.draft_report:null;
 const items=[...D.observations(D.draftFacts(task),'draft'),...D.observations(D.list(task.evidence).filter(current),'evidence'),...D.observations(D.list(task.rejected_evidence).filter(current).map(item=>ctx.unverifiedProposal(task,item)),'pending')];
 const seen=new Set(),observations=items.filter(item=>!seen.has(item.key)&&seen.add(item.key));
 const kinds=new Set(observations.map(item=>item.kind)),states={draft:'原始取数',evidence:'已采用证据',pending:'待核验记录'};
 const sections=D.groups(observations,task).map(group=>'<section class="source-comparison" data-source-group><header><h3>'+esc(group.name)+'</h3>'+(group.object?'<span>'+esc(group.object)+'</span>':'')+(kinds.size>1?'<span class="source-kind">'+states[group.kind]+'</span>':'')+'</header>'+D.grid(group,task,ctx,{results:true,calculations:D.list(paper?.calculations).filter(current)})+D.sourceNotes(group.items,task,ctx)+'</section>').join('');
 const excerpts=observations.filter(item=>!D.numeric(item)).map(item=>'<article class="source-excerpt"><p>'+esc(item.fact.text||item.raw||item.fact.label||'未返回正文')+'</p><footer><span>'+esc(item.period)+'</span>'+D.sourceNotes([item],task,ctx)+'<span>'+states[item.kind]+'</span>'+'</footer></article>').join('');
 const originalRecords=D.records(task),failure=task.status==='失败'&&task.error?'<p role="status">'+esc(task.error)+'</p>':'';
 return '<div class="desk-view desk-sources desk-sources--matrix"><header class="desk-view-heading desk-data-heading"><h2>研究数据</h2>'+(observations.length?'<span class="desk-view-meta">'+observations.length+'条记录</span>':'')+'</header>'+failure+sections+(excerpts?'<section class="source-excerpts desk-block"><h3>材料摘录</h3>'+excerpts+'</section>':'')+(!observations.length&&!originalRecords.length?'<div class="empty"><p>'+(task.status==='失败'?'本次研究未取得数据或资料，可补充材料后继续。':'取得的数据和资料会保留在这里。')+'</p></div>':'')+D.library(task,observations,ctx)+'<details class="source-process desk-disclosure" data-reading-key="source-process"><summary>研究过程与运行记录</summary>'+ctx.processDetails(task)+'</details></div>';
};
})(window);
