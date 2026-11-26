/* Presentation only: retained series, source navigation and reading position. */
(function(global){
'use strict';
let chartLibrary;
function loadCharts(){
 if(global.echarts)return Promise.resolve(global.echarts);
 if(chartLibrary)return chartLibrary;
 chartLibrary=new Promise((resolve,reject)=>{
  const script=document.createElement('script');script.src=new URL('echarts.min.js',document.baseURI).href;
  script.onload=()=>global.echarts?resolve(global.echarts):reject(Error('图表组件不可用'));
  script.onerror=()=>{script.remove();chartLibrary=null;reject(Error('图表组件未载入'))};
  document.head.append(script);
 }).catch(error=>{chartLibrary=null;throw error});
 return chartLibrary;
}
function create(root){
 const widgets=new Map(),preferences=new Map();let destroyed=false,frame=0;
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const alive=widget=>!destroyed&&!root.hidden&&widget.element.isConnected&&root.contains(widget.element)&&widgets.get(widget.element)===widget;
 const color=(name,fallback)=>getComputedStyle(root).getPropertyValue(name).trim()||fallback;
 function chartLayout(data,width){
  const columns=data.series.length>1&&width>=480?2:1;
  const titleWidth=Math.max(1,Math.floor(width/columns)-12),tops=[],extra=[];
  const Text=global.echarts?.graphic?.Text;
  let height=0;
  for(let first=0;first<data.series.length;first+=columns){
   const captions=data.series.slice(first,first+columns).map(row=>Text?new Text({style:{text:row.label+'  '+row.unit,fontSize:13,fontWeight:500,fontFamily:'Arial,"PingFang SC",sans-serif',width:titleWidth,overflow:'break',lineHeight:18}}).getBoundingRect().height:18);
   const added=Math.max(0,Math.ceil(Math.max(18,...captions))-18);
   tops.push(height);extra.push(added);height+=180+added;
  }
  return {columns,titleWidth,tops,extra,height};
 }
 function chartOptions(data,width,layout=chartLayout(data,width)){
  const {columns}=layout;
  const preserveYear=new Set(data.periods.map(period=>period.slice(0,4))).size>1;
  const dark=document.documentElement.dataset.theme==='dark',ink=color('--color-ink','#252525'),muted=color('--color-muted','#6b6b6b'),rule=color('--color-rule','#e2e2e2');
  const colors=dark?['#f28598','#80b3f0','#d7b571','#a3c2a9']:['#c8102e','#3567a1','#8a692e','#467c68'];
  const axes={axisLine:{show:true,onZero:false,lineStyle:{color:muted,width:1}},
   axisTick:{show:true,length:3,alignWithLabel:true,lineStyle:{color:muted}},splitLine:{show:false}};
  const title=[],grid=[],xAxis=[],yAxis=[],series=[];
  const last=data.periods.length-1,count=Math.min(data.periods.length,width/columns<=430?4:6);
  const dates=new Set(Array.from({length:count},(_,i)=>Math.round(i*last/Math.max(1,count-1))));
  const interval=i=>dates.has(i);
  data.series.forEach((row,index)=>{
   const rowIndex=Math.floor(index/columns),top=layout.tops[rowIndex],left=index%columns*100/columns;
   title.push({text:row.label+'  '+row.unit,left:left+'%',top:top+6,textStyle:{color:ink,fontSize:13,fontWeight:500,fontFamily:'Arial,"PingFang SC",sans-serif',width:layout.titleWidth,overflow:'break',lineHeight:18}});
   grid.push({top:top+42+layout.extra[rowIndex],height:112,left:left+'%',width:(100/columns-2)+'%',containLabel:true});
   xAxis.push({type:'category',gridIndex:index,data:data.periods,boundaryGap:false,...axes,axisTick:{...axes.axisTick,interval},
    axisLabel:{color:muted,fontSize:13,hideOverlap:true,showMinLabel:true,showMaxLabel:true,
     alignMinLabel:'left',alignMaxLabel:'right',margin:8,interval,
     formatter:value=>preserveYear?value:value.slice(5).replace('-','.')}});
   yAxis.push({type:'value',gridIndex:index,scale:true,splitNumber:3,...axes,
    axisLabel:{color:muted,fontSize:13,formatter:value=>global.RHResearchData.number(value)}});
   series.push({type:'line',name:row.label,xAxisIndex:index,yAxisIndex:index,connectNulls:false,smooth:false,
    showSymbol:false,itemStyle:{color:colors[index]},lineStyle:{width:2},data:row.points.map(point=>point?point.value:null)});
  });
  return {animation:false,textStyle:{fontFamily:'Arial,"PingFang SC",sans-serif'},aria:{enabled:true},title,grid,xAxis,yAxis,series,tooltip:{trigger:'axis',axisPointer:{type:'none'},confine:true,backgroundColor:color('--color-surface','#fff'),borderColor:rule,textStyle:{color:ink,fontSize:13},formatter:items=>{
   const params=Array.isArray(items)?items[0]:items;
   if(!params)return '';
   const row=data.series[params.seriesIndex],point=row?.points[params.dataIndex];
   return point?esc(data.periods[params.dataIndex])+'<br>'+esc(row.label)+'：'+esc(point.raw)+' '+esc(row.unit):'';
  }}};
 }
 function prepareCanvas(widget){
  const canvas=widget.element.querySelector('.observation-chart'),width=canvas.clientWidth??canvas.getBoundingClientRect().width;
  const layout=chartLayout(widget.data,width);
  if(canvas.style.height!==layout.height+'px')canvas.style.height=layout.height+'px';
  return {canvas,width,columns:layout.columns,layout};
 }
 function resizeChart(widget){
  const {width,columns,layout}=prepareCanvas(widget);
  if(!widget.chart||!width)return;
  if(widget.columns!==columns||widget.width!==width){
   widget.chart.setOption(chartOptions(widget.data,width,layout),true);widget.columns=columns;widget.width=width;
  }
  widget.chart.resize();
 }
 function disposeWidget(widget){widget.chart?.dispose();resize.unobserve(widget.element);widgets.delete(widget.element);}
 async function showChart(widget){
  if(!alive(widget)||widget.element.dataset.presentation!=='chart')return;
  const {canvas,width}=prepareCanvas(widget);
  if(!width)return;
  canvas.setAttribute('aria-busy','true');
  try{
   const library=await loadCharts();if(!alive(widget)||widget.element.dataset.presentation!=='chart')return;
   const current=prepareCanvas(widget);if(!current.width)return;
   if(!widget.chart){
    widget.chart=library.init(canvas,null,{renderer:'canvas'});
    widget.chart.on('click',event=>{
     if(!alive(widget))return;
     const point=widget.data.series[event.seriesIndex]?.points[event.dataIndex];if(!point)return;
     const original=[...widget.element.querySelectorAll('button[data-observation]')].find(button=>button.dataset.observation===point.key);
     if(original){widget.element.querySelector('[data-evidence-view="chart"]').focus({preventScroll:true});original.click()}
    });
   }
   widget.chart.setOption(chartOptions(widget.data,current.width,current.layout),true);widget.columns=current.columns;widget.width=current.width;widget.chart.resize();
  }catch(error){
   if(!alive(widget)||widget.element.dataset.presentation!=='chart')return;
   setMode(widget,'table');
   const message=widget.element.querySelector('.observation-chart-error');message.textContent='图表未能载入，原始数据仍可查看。点击趋势可重试。';message.hidden=false;
  }finally{canvas.removeAttribute('aria-busy')}
 }
 function setMode(widget,mode){
  const element=widget.element;element.dataset.presentation=mode;
  element.querySelector('.observation-table').hidden=mode!=='table';element.querySelector('.observation-chart').hidden=mode!=='chart';
  element.querySelectorAll('[data-evidence-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.evidenceView===mode)));
  element.querySelector('.observation-chart-error').hidden=true;
  preferences.delete(element.dataset.evidenceKey);preferences.set(element.dataset.evidenceKey,mode);
  if(preferences.size>100)preferences.delete(preferences.keys().next().value);
  if(mode==='chart')showChart(widget);
 }
 function updateNavigation(){
  if(destroyed||root.hidden)return;
  const nav=root.querySelector('.paper-navigation');if(!nav)return;
  const tabs=root.querySelector('.views'),offset=(tabs?.offsetHeight||54)+nav.offsetHeight+16;
  root.style.setProperty('--desk-paper-anchor-offset',offset+'px');
  const bounds=root.getBoundingClientRect(),scale=root.offsetWidth?bounds.width/root.offsetWidth:1;
  const edge=bounds.top+offset*scale,articles=[...root.querySelectorAll('.paper-argument')];
  const atEnd=root.scrollTop>0&&root.scrollTop+root.clientHeight>=root.scrollHeight-4;
  const current=atEnd?articles.at(-1):articles.filter(article=>article.getBoundingClientRect().top<=edge+1).at(-1)||articles[0];
  nav.querySelectorAll('[data-paper-target]').forEach(button=>{
   if(button.dataset.paperTarget===current?.id)button.setAttribute('aria-current','true');else button.removeAttribute('aria-current');
  });
 }
 function scheduleNavigation(){if(frame||destroyed)return;frame=requestAnimationFrame(()=>{frame=0;updateNavigation()})}
 const resize=new ResizeObserver(entries=>{for(const item of entries){const widget=widgets.get(item.target);if(widget&&widget.element.dataset.presentation==='chart'){if(widget.chart)resizeChart(widget);else showChart(widget)}}scheduleNavigation()});
 const theme=new MutationObserver(()=>{for(const widget of widgets.values())if(widget.chart&&widget.element.dataset.presentation==='chart'){widget.width=null;resizeChart(widget)}});
 theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});resize.observe(root);
 function updated(){
  if(destroyed)return;
  for(const widget of widgets.values())if(!alive(widget))disposeWidget(widget);
  if(root.hidden)return;
  for(const element of root.querySelectorAll('.observation-view')){
   if(widgets.has(element))continue;
   let data;try{data=JSON.parse(element.dataset.evidenceSeries)}catch{continue}
   const widget={element,data,chart:null};widgets.set(element,widget);resize.observe(element);
   element.querySelector('.observation-chart').style.height=data.series.length*180+'px';
   setMode(widget,preferences.get(element.dataset.evidenceKey)||'table');
  }
  scheduleNavigation();
 }
 function click(event){
  const button=event.target.closest('[data-evidence-view]');if(!button||!root.contains(button))return;
  const widget=widgets.get(button.closest('.observation-view'));if(widget)setMode(widget,button.dataset.evidenceView);
 }
 function keydown(event){
  const button=event.target.closest('.paper-navigation button');if(!button||!root.contains(button))return;
  const buttons=[...button.parentElement.querySelectorAll('button')],index=buttons.indexOf(button);
  const next={ArrowRight:(index+1)%buttons.length,ArrowLeft:(index+buttons.length-1)%buttons.length,Home:0,End:buttons.length-1}[event.key];
  if(next===undefined)return;event.preventDefault();buttons[next].focus({preventScroll:true});
  const nav=button.parentElement,target=buttons[next],bounds=nav.getBoundingClientRect();
  const scale=nav.offsetWidth?bounds.width/nav.offsetWidth:1;
  nav.scrollTo({left:Math.max(0,nav.scrollLeft+(target.getBoundingClientRect().left-bounds.left)/scale-nav.clientWidth/2+target.offsetWidth/2),behavior:'auto'});
 }
 function toggle(){for(const widget of widgets.values())if(widget.element.dataset.presentation==='chart')showChart(widget);scheduleNavigation()}
 root.addEventListener('click',click);root.addEventListener('keydown',keydown);root.addEventListener('scroll',scheduleNavigation,{passive:true});root.addEventListener('toggle',toggle,true);
 function destroy(){
  if(destroyed)return;destroyed=true;cancelAnimationFrame(frame);theme.disconnect();resize.disconnect();
  for(const widget of [...widgets.values()]){widget.chart?.dispose();widgets.delete(widget.element)}preferences.clear();
  root.removeEventListener('click',click);root.removeEventListener('keydown',keydown);root.removeEventListener('scroll',scheduleNavigation);root.removeEventListener('toggle',toggle,true);
 }
 return {updated,destroy};
}
global.RHDeskInteractions=Object.freeze({create});
})(window);
