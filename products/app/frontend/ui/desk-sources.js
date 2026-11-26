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
 const states={draft:'原始取数',evidence:'已采用证据',pending:'待核验记录'};
 const originalRecords=D.records(task),originals=new Map(originalRecords.map(record=>[record.id,record]));
 const groups=D.groups(observations,task),tabular=new Set(groups.flatMap(group=>group.items.map(item=>item.key)));
 const failure=task.status==='失败'&&task.error?'<p role="status">'+esc(task.error)+'</p>':'';
 const files=ctx.hasOriginals?'<button class="button-secondary desk-action" type="button" data-action="original-files">留存文件</button>':'';
 const catalog=D.sourceCatalog(task,observations,ctx),needs=D.categoryNeeds(task,observations);
 const calculations=D.list(paper?.calculations).filter(current);
 const sections=D.dataCategories.map(([id,label])=>{
  const collections=groups.filter(group=>group.category===id);
  const excerpts=observations.filter(item=>!tabular.has(item.key)&&D.dataCategory(item.fact,originals.get(item.artifact))===id);
  const originalsMarkup=catalog.sections.get(id);
  const gaps=[...(needs.get(id)?.values()||[])];
  if(!collections.length&&!excerpts.length&&!originalsMarkup&&!gaps.length)return null;
  const readouts=collections.map(group=>{
   const title=[group.object,collections.some(item=>item.kind!==group.kind)?states[group.kind]:'',
    group.composition?'指数成分':''].filter(Boolean).join(' · ');
   const compact=D.recentReadings(group,task,ctx);
   const data=D.grid(group,task,ctx,{results:true,calculations});
   return '<section class="source-comparison" data-source-group>'
    +(title?'<header><h4>'+esc(title)+'</h4></header>':'')+compact
    +(compact?'<details class="desk-disclosure source-observations" data-reading-key="'+esc('source-data:'+id+':'+group.object+':'+group.kind)+'">'
     +'<summary>全部读数</summary>'+data+'</details>':data)+'</section>';
  }).join('');
  const quotes=excerpts.map(item=>'<article class="source-excerpt"><p>'+esc(item.fact.text||item.raw||item.fact.label||'未返回正文')
   +'</p><footer><span>'+esc(item.period)+'</span>'+D.sourceCitation(originals.get(item.artifact),task,ctx,item)
   +'<span>'+states[item.kind]+'</span></footer></article>').join('');
  const missing=gaps.length?'<ul class="source-category-gaps">'+gaps.map(row=>'<li><strong>'+esc(row.label)+'</strong>'
   +(row.subject?'<span>'+esc(row.subject)+'</span>':'')+'<span>'+esc(row.note)+'</span></li>').join('')+'</ul>':'';
  return {id,label,html:'<section class="source-category" id="research-data-'+id+'" data-category="'+id+'"><h3>'+label+'</h3>'
   +readouts+(quotes?'<div class="source-excerpts">'+quotes+'</div>':'')+missing
   +(originalsMarkup?'<details class="source-library desk-disclosure" data-reading-key="source-library:'+id+'"><summary>原始资料</summary>'+originalsMarkup+'</details>':'')+'</section>'};
 }).filter(Boolean);
 const nav=sections.length>1?'<nav class="source-category-nav" aria-label="数据分类">'+sections.map(entry=>
  '<a href="#research-data-'+entry.id+'">'+entry.label+'</a>').join('')+'</nav>':'';
 const empty=sections.length?'':'<div class="empty"><p>取得的数据和材料会显示在这里。</p></div>';
 return '<div class="desk-view desk-sources desk-sources--matrix"><header class="desk-view-heading desk-data-heading"><h2>研究数据</h2>'
  +files+'</header>'+failure+nav+sections.map(entry=>entry.html).join('')+empty
  +'<details class="source-process desk-disclosure" data-reading-key="source-process"><summary>运行记录</summary>'
  +catalog.diagnostics+ctx.processDetails(task)+'</details></div>';
};
})(window);
