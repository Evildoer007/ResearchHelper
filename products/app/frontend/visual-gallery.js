/* One palette per view. Actual report data never falls back to template values. */
(function(){
 'use strict';
 const $=id=>document.getElementById(id),V=RHVisuals,catalog=V.catalogue;
 const examples=new Map(RH_VISUAL_EXAMPLES.map(row=>[row.scheme,row]));
 const definitions=new Map(catalog.schemes.map(scheme=>[scheme.id,scheme]));
 const groupByFamily=new Map(catalog.groups.flatMap(group=>group.families.map(family=>[family,group.id])));
 const groupFor=scheme=>groupByFamily.get(definitions.get(scheme)?.family);
 const preferredTheme='red_blue';
 let current='small_multiples',family='time',themeID=preferredTheme,targets=[],targetID=null,embedded=new URLSearchParams(location.search).has('embedded'),canApply=false;
 const chart=echarts.init($('active-chart'),null,{renderer:'svg'}),minis=new Map();
 try{const value=localStorage.getItem('researchhelper.visual-library.theme.v1');if(catalog.themes.some(t=>t.id===value))themeID=value}catch{}
 function tell(text){$('feedback').textContent=text||''}
 function payload(){
  const target=targets.find(row=>row.target===targetID);
  if(target&&target.compatible.includes(current))return {data:V.materialize({scheme:target.scheme,data:target.data},current),title:target.title,source:'当前报告的冻结数值',origin:'current'};
  return examples.get(current);
 }
 function miniOption(kind,data,width,height){
  const o=V.option(kind,data,themeID,{width,height});
  o.tooltip={show:false};o.legend={show:false};o.title=[];
  if(o.visualMap)o.visualMap={...o.visualMap,show:false};
  if(!Array.isArray(o.grid))o.grid={left:7,right:10,top:12,bottom:10,containLabel:false};
  const hideAxis=axis=>({...axis,name:'',axisLabel:{show:false}});
  for(const axis of ['xAxis','yAxis'])if(o[axis])o[axis]=Array.isArray(o[axis])?o[axis].map(hideAxis):hideAxis(o[axis]);
  for(const s of o.series||[]){s.label={show:false};if(s.type==='graph')s.symbolSize=12;}
  return o;
 }
 function refreshMini(id,instance){
  const target=family==='current'?targets.find(row=>row.target===targetID):null;
  const data=target?V.materialize({scheme:target.scheme,data:target.data},id):examples.get(id).data;
  instance.setOption(miniOption(id,data,instance.getWidth(),instance.getHeight()),true);
 }
 function paintMini(button){
  const id=button.dataset.scheme;if(minis.has(id))return;
  const instance=echarts.init(button.querySelector('.mini'),null,{renderer:'svg'});
  refreshMini(id,instance);minis.set(id,instance);
 }
 const select=$('theme-select');
 for(const row of catalog.themes)select.add(new Option(row.name,row.id));
 select.onchange=()=>{
  themeID=select.value;
  try{localStorage.setItem('researchhelper.visual-library.theme.v1',themeID)}catch{}
  palette();renderActive();
  for(const [id,mini] of minis)refreshMini(id,mini);
 };
 function palette(){
  select.value=themeID;
  const t=V.theme(themeID);
  document.documentElement.style.colorScheme=t.dark?'dark':'light';
  for(const [name,value] of Object.entries({'--canvas':t.canvas||'#F2F4F6','--paper':t.paper,'--ink':t.ink,'--muted':t.muted,'--rule':t.grid,'--focus':t.dark?t.ink:t.accent}))document.documentElement.style.setProperty(name,value);
 }
 function renderActive(){
  const definition=definitions.get(current),example=payload();
  $('active-chart').setAttribute('aria-label',example.title+'图');
  $('preview-title').textContent=example.title;
  $('data-origin').textContent=example.origin==='current'?'当前报告':example.origin==='real'?'真实样例':'模板样例';
  $('data-source').textContent=example.origin==='current'?'':example.source;
  chart.resize();
  chart.setOption(V.option(current,example.data,themeID,{width:chart.getWidth(),height:chart.getHeight(),chart}),true);
  $('requirements-text').textContent=definition.requires+'。'+definition.purpose;
  const target=targets.find(row=>row.target===targetID);$('apply').hidden=!embedded||!canApply;
  $('apply').textContent=target?.compatible.includes(current)?'用于报告':'应用配色';
  document.querySelectorAll('[data-scheme]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.scheme===current)));
  chart.resize();
 }
 function renderCards(){
  observer.disconnect();for(const mini of minis.values())mini.dispose();minis.clear();$('cards').replaceChildren();
  const target=targets.find(row=>row.target===targetID);
  const order=family==='current'?target?.compatible||[]:catalog.schemes.filter(scheme=>groupFor(scheme.id)===family).map(scheme=>scheme.id);
  for(const id of order){const definition=definitions.get(id);if(!definition||!examples.has(id))continue;
   const button=document.createElement('button');button.className='scheme';button.dataset.scheme=id;button.setAttribute('aria-pressed',String(id===current));
   const heading=document.createElement('div');heading.className='scheme-heading';const name=document.createElement('strong');name.textContent=definition.name;heading.append(name);
   const mini=document.createElement('div');mini.className='mini';mini.setAttribute('aria-hidden','true');button.append(heading,mini);
   button.onclick=()=>{current=id;renderActive();tell('')};
   $('cards').append(button);observer.observe(button);
  }
 }
 const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){paintMini(entry.target);observer.unobserve(entry.target)}},{rootMargin:'160px'});
 function families(){
  const groups=targets.length?[{id:'current',name:'适用方案'},...catalog.groups]:catalog.groups;
  $('families').replaceChildren(...groups.map(group=>{
   const button=document.createElement('button');button.textContent=group.name;button.dataset.group=group.id;
   button.setAttribute('aria-pressed',String(group.id===family));
   button.onclick=()=>{
    family=group.id;
    for(const tab of $('families').children)tab.setAttribute('aria-pressed',String(tab===button));
    renderCards();
   };
   return button;
  }));
 }
 $('requirements').onclick=()=>$('requirements-dialog').showModal();$('close-requirements').onclick=()=>$('requirements-dialog').close();
 $('export-image').onclick=async event=>{
  const button=event.currentTarget, name=definitions.get(current).name, paper=V.theme(themeID).paper;
  let inSavePanel=false;
  button.disabled=true;
  try{
   const width=chart.getWidth(),height=chart.getHeight();
   const preview=new Image();
   preview.src=chart.getDataURL({type:'svg',backgroundColor:paper});
   await preview.decode();
   const canvas=document.createElement('canvas');
   canvas.width=width*3;canvas.height=height*3;
   const context=canvas.getContext('2d');
   context.fillStyle=paper;context.fillRect(0,0,canvas.width,canvas.height);
   context.drawImage(preview,0,0,canvas.width,canvas.height);
   const png=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
   if(!png)throw new Error('PNG encoding failed');
   if(embedded&&parent!==window){
    parent.postMessage({type:'rh:visual-library-save-png',name,png},'*');
    inSavePanel=true;
   }else{
    const link=document.createElement('a');
    link.download=name+'.png';link.href=URL.createObjectURL(png);
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(link.href),30000);
    tell('已开始下载PNG');
   }
  }catch{
   tell('PNG导出失败，请重试');
  }finally{
   if(!inSavePanel)button.disabled=false;
  }
 };
 $('apply').onclick=()=>{const target=targets.find(row=>row.target===targetID),schemes={};if(target?.compatible.includes(current))schemes[target.target]=current;
  parent.postMessage({type:'rh:visual-library-apply',profile:{theme:themeID,schemes}},'*')};
 addEventListener('message',event=>{
  if(event.source!==parent)return;
  if(event.data?.type==='rh:visual-library-png-result'){
   $('export-image').disabled=false;
   tell({saved:'已保存PNG',download_started:'已开始下载PNG',cancelled:'已取消保存'}[event.data.status]||'PNG导出失败，请重试');
   return;
  }
  if(event.data?.type!=='rh:visual-library-context')return;
  canApply=event.data.canApply===true;
  targets=Array.isArray(event.data.targets)?event.data.targets.filter(target=>definitions.has(target.scheme)):[];
  targetID=targets[0]?.target??null;
  if(catalog.themes.some(theme=>theme.id===event.data.theme))themeID=event.data.theme;
  $('targets').hidden=targets.length<2;
  $('targets').replaceChildren(...targets.map(target=>{
   const button=document.createElement('button');button.textContent=target.title;
   button.setAttribute('aria-pressed',String(target.target===targetID));
   button.onclick=()=>{
    targetID=target.target;current=target.scheme;family='current';
    for(const choice of $('targets').children)choice.setAttribute('aria-pressed',String(choice===button));
    families();renderCards();renderActive();
   };
   return button;
  }));
  if(targets.length){current=targets[0].scheme;family='current'}
  palette();families();renderCards();renderActive();
 });
 addEventListener('keydown',event=>{if(event.key==='Escape'&&embedded&&!$('requirements-dialog').open){event.preventDefault();parent.postMessage({type:'rh:visual-library-close'},'*')}});
 addEventListener('resize',()=>{renderActive();for(const [id,mini] of minis){mini.resize();refreshMini(id,mini)}});
 palette();families();renderCards();renderActive();
 if(embedded)parent.postMessage({type:'rh:visual-library-ready'},'*');
 window.RHVisualGallery={chart,minis,getState:()=>({theme:themeID,scheme:current,targets:targets.length}),select:(id)=>{current=id;renderActive()}};
})();
