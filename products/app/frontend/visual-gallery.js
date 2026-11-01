/* One palette per view. Actual report data never falls back to template values. */
(function(){
 'use strict';
 const $=id=>document.getElementById(id),V=RHVisuals,catalog=V.catalogue;
 const examples=new Map(RH_VISUAL_EXAMPLES.map(row=>[row.scheme,row]));
 let current='heatmap',family='全部',themeID='research',targets=[],targetID=null,embedded=new URLSearchParams(location.search).has('embedded'),canApply=false;
 const chart=echarts.init($('active-chart'),null,{renderer:'svg'}),minis=new Map();
 try{const value=localStorage.getItem('researchhelper.visual-library.theme.v1');if(catalog.themes.some(t=>t.id===value))themeID=value}catch{}
 function tell(text){$('feedback').textContent=text||''}
 function payload(){
  const target=targets.find(row=>row.target===targetID);
  if(target&&target.compatible.includes(current))return {data:V.materialize({scheme:target.scheme,data:target.data},current),title:target.title,source:'当前报告的冻结数值',origin:'current'};
  return examples.get(current);
 }
 function miniOption(kind,data){const o=V.option(kind,data,themeID);o.tooltip={show:false};o.legend={show:false};if(o.visualMap)o.visualMap={...o.visualMap,show:false};o.grid={left:7,right:10,top:12,bottom:10,containLabel:false};
  for(const axis of ['xAxis','yAxis'])if(o[axis])o[axis]={...o[axis],name:'',axisLabel:{show:false}};
  for(const s of o.series||[]){s.label={show:false};if(s.type==='graph')s.symbolSize=12;}
  return o;
 }
 function paintMini(button){const id=button.dataset.scheme;if(minis.has(id))return;const instance=echarts.init(button.querySelector('.mini'),null,{renderer:'svg'});const example=examples.get(id);instance.setOption(miniOption(id,example.data));minis.set(id,instance)}
 function palette(){
  $('palettes').replaceChildren(...catalog.themes.map(t=>{const button=document.createElement('button');button.className='palette';button.setAttribute('aria-pressed',String(t.id===themeID));button.dataset.theme=t.id;
   const swatches=document.createElement('span');swatches.className='swatches';swatches.setAttribute('aria-hidden','true');for(const color of t.colors.slice(0,3)){const dot=document.createElement('i');dot.style.background=color;swatches.append(dot)}
   const name=document.createElement('span');name.textContent=t.name;button.append(swatches,name);button.onclick=()=>{themeID=t.id;try{localStorage.setItem('researchhelper.visual-library.theme.v1',themeID)}catch{};palette();renderActive();for(const [id,mini] of minis)mini.setOption(miniOption(id,examples.get(id).data),true)};return button}));
  const t=V.theme(themeID);for(const [name,value] of Object.entries({'--canvas':t.canvas||'#F2F4F6','--paper':t.paper,'--ink':t.ink,'--muted':t.muted,'--rule':t.grid,'--focus':themeID==='research'?t.accent:t.ink}))document.documentElement.style.setProperty(name,value);
 }
 function renderActive(){
  const definition=catalog.schemes.find(s=>s.id===current),example=payload();
  $('active-chart').setAttribute('aria-label',example.title+'图');
  $('preview-title').textContent=example.title;$('preview-purpose').textContent=definition.purpose;
  $('data-origin').textContent=example.origin==='current'?'当前报告':example.origin==='real'?'真实数据':'模板示例';
  $('data-source').textContent=example.source;chart.setOption(V.option(current,example.data,themeID,{width:chart.getWidth()}),true);
  $('requirements-text').textContent=definition.requires+'。'+definition.purpose;
  const target=targets.find(row=>row.target===targetID);$('apply').hidden=!embedded||!canApply;
  $('apply').textContent=target?.compatible.includes(current)?'用于报告':'应用配色';
  document.querySelectorAll('[data-scheme]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.scheme===current)));
  chart.resize();
 }
 function renderCards(){
  observer.disconnect();for(const mini of minis.values())mini.dispose();minis.clear();$('cards').replaceChildren();
  const order=RH_VISUAL_EXAMPLES.map(item=>item.scheme);
  for(const id of order){const definition=catalog.schemes.find(s=>s.id===id);if(family!=='全部'&&definition.family!==family)continue;
   const button=document.createElement('button');button.className='scheme';button.dataset.scheme=id;button.setAttribute('aria-pressed',String(id===current));
   const heading=document.createElement('div');heading.className='scheme-heading';const name=document.createElement('strong');name.textContent=definition.name;heading.append(name);
   if(examples.get(id).origin==='template'){const tag=document.createElement('span');tag.className='template-tag';tag.textContent='模板';heading.append(tag)}
   const mini=document.createElement('div');mini.className='mini';mini.setAttribute('aria-hidden','true');button.append(heading,mini);
   button.onclick=()=>{current=id;renderActive();tell(targets.length&&!targets.find(t=>t.target===targetID)?.compatible.includes(id)?'当前报告缺少这套图形所需的数据，预览的是方案样例。':'')};
   $('cards').append(button);observer.observe(button);
  }
 }
 const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){paintMini(entry.target);observer.unobserve(entry.target)}},{rootMargin:'160px'});
 function families(){const names=['全部',...new Set(catalog.schemes.map(s=>s.family))];$('families').replaceChildren(...names.map(name=>{const button=document.createElement('button');button.textContent=name;button.setAttribute('aria-pressed',String(name===family));button.onclick=()=>{family=name;families();renderCards()};return button}))}
 $('requirements').onclick=()=>$('requirements-dialog').showModal();$('close-requirements').onclick=()=>$('requirements-dialog').close();
 $('export-image').onclick=()=>{const a=document.createElement('a');a.download=V.catalogue.schemes.find(row=>row.id===current).name+'.png';a.href=chart.getDataURL({type:'png',pixelRatio:3,backgroundColor:V.theme(themeID).paper});a.click();tell('图片已导出')};
 $('apply').onclick=()=>{const target=targets.find(row=>row.target===targetID),schemes={};if(target?.compatible.includes(current))schemes[target.target]=current;
  parent.postMessage({type:'rh:visual-library-apply',profile:{theme:themeID,schemes}},'*')};
 addEventListener('message',event=>{if(event.source!==parent||event.data?.type!=='rh:visual-library-context')return;
  canApply=event.data.canApply===true;targets=Array.isArray(event.data.targets)?event.data.targets:[];targetID=targets[0]?.target??null;themeID=event.data.theme||themeID;
  $('targets').hidden=!targets.length;$('targets').replaceChildren(...targets.map(target=>{const b=document.createElement('button');b.textContent=target.title;b.setAttribute('aria-pressed',String(target.target===targetID));b.onclick=()=>{targetID=target.target;current=target.scheme;document.querySelectorAll('#targets button').forEach(button=>button.setAttribute('aria-pressed',String(button===b)));renderActive()};return b}));
  if(targets.length)current=targets[0].scheme;palette();renderActive();
 });
 addEventListener('keydown',event=>{if(event.key==='Escape'&&embedded&&!$('requirements-dialog').open){event.preventDefault();parent.postMessage({type:'rh:visual-library-close'},'*')}});
 addEventListener('resize',()=>{chart.resize();renderActive();for(const c of minis.values())c.resize()});
 palette();families();renderCards();renderActive();
 if(embedded)parent.postMessage({type:'rh:visual-library-ready'},'*');
 window.RHVisualGallery={chart,minis,getState:()=>({theme:themeID,scheme:current,targets:targets.length}),select:(id)=>{current=id;renderActive()}};
})();
