/* Offline evidence charts. Values and derivations belong to the frozen input. */
(function(global){
 'use strict';
 const instances=new WeakMap();
 const schemes=new Set(['scatter','ranked_bar','percentile','cash_index','payout_units','range']);
 function mount({data,specs,root=document,onSource}={}){
  if(!Array.isArray(specs)||!specs.length)throw new TypeError('图表清单为空');
  const seen=new Set();
  const slots=specs.map(spec=>{
   if(!schemes.has(spec.scheme)||seen.has(spec.target))throw new TypeError('图表方案未登记或目标重复');
   seen.add(spec.target);
   const host=root.querySelector('#'+CSS.escape(spec.target));
   if(!host||!spec.data)throw new TypeError('图表容器或数据缺失');
   return host;
  });
  instances.get(root)?.destroy();
  const originalMinimums=slots.map(host=>host.style.minHeight);
  const DATA=data,SPECS=specs,charts=[],selected=new Map();
  let disposed=false;
  const inspect=(target,rowIndex)=>{
   if(disposed)return;
   const detail={target,rowIndex};
   if(typeof onSource==='function')onSource(detail);
   else root.dispatchEvent(new CustomEvent('rh:evidence-source',{detail,bubbles:true}));
  };
const css=getComputedStyle(document.documentElement),token=name=>css.getPropertyValue(name).trim();
const palette=Array.from({length:5},(_,index)=>token('--chart-color-'+(index+1)));
const style={font:token('--font-sans'),palette,primary:palette[0],benchmark:palette[1],gold:palette[2],ink:token('--color-ink'),axisLabel:token('--color-ink-soft'),muted:token('--color-muted'),rule:token('--color-rule'),ruleStrong:token('--color-rule-strong'),surface:token('--color-red-surface'),paper:token('--color-paper'),labelSize:Number.parseFloat(token('--type-chart'))};
const label={fontFamily:style.font,fontSize:style.labelSize,color:style.axisLabel};
const signed=value=>(value>0?'+':'')+Number(value).toFixed(2)+'%';
const text=(x,y,value,extra={})=>({type:'text',x,y,style:{text:value,font:style.labelSize+'px '+style.font,fill:style.ink,...extra}});
const line=(x1,y1,x2,y2,color=style.rule)=>({type:'line',shape:{x1,y1,x2,y2},style:{stroke:color,lineWidth:.8}});
const frame={axisLine:{show:true,onZero:false,lineStyle:{color:style.muted,width:1}},
 axisTick:{show:true,length:3,alignWithLabel:true,lineStyle:{color:style.muted}},splitLine:{show:false}};
const valueAxis=(min,max,interval)=>({type:'value',min,max,interval,...frame,axisLabel:{...label,color:style.axisLabel,formatter:value=>value+'%'}});
const categoryAxis=data=>({type:'category',data,inverse:true,...frame,axisLabel:{...label,hideOverlap:false,margin:8}});
function scatterGeometry(width,count){
 const narrow=width<520;
 const grid={left:narrow?42:43,right:narrow?18:Math.round(width*.50),top:40,height:narrow?135:124,containLabel:false};
 const plotRight=width-grid.right,ledgerX=narrow?4:plotRight+18,ledgerY=narrow?219:30;
 const ledgerWidth=width-ledgerX-5,rowHeight=narrow?22:24;
 // Preserve the plot and every disclosed row when the ledger moves beneath it.
 const minimumHeight=Math.ceil(Math.max(grid.top+grid.height+33,
  ledgerY+22+Math.max(0,count-1)*rowHeight+style.labelSize+8));
 return {narrow,grid,ledgerX,ledgerY,ledgerWidth,rowHeight,minimumHeight};
}
function render(spec,chart){
 const option={animation:false,textStyle:label,color:style.palette,tooltip:{trigger:'item',renderMode:'richText',confine:true,textStyle:{...label,color:style.paper},backgroundColor:style.ink,borderColor:style.ruleStrong}};
 if(spec.scheme==='scatter'){
  const width=chart.getWidth(),active=selected.get(spec.target)??0;
  const {narrow,grid,ledgerX,ledgerY,ledgerWidth,rowHeight}=scatterGeometry(width,spec.data.points.length);
  option.grid=grid;option.xAxis={...valueAxis(-40,40,20),name:'1.14至5.31股价涨跌',nameLocation:'middle',nameGap:23,nameTextStyle:{...label,color:style.muted},axisLine:{show:true,onZero:false,lineStyle:{color:style.ink,width:1.2}},axisTick:{show:true,lineStyle:{color:style.ink}},minorTick:{show:true,splitNumber:4,length:3,lineStyle:{color:style.rule}}};
  option.yAxis=valueAxis(-40,40,20);
  option.tooltip.formatter=value=>{const row=spec.data.points[value.dataIndex];return row.name+'\n股价：'+signed(row.x)+'\nQ1营收：'+signed(row.y)+'\n'+row.feature};
  const positions=['top','left','left','right','left','top'];
  option.series=[{name:'板块对照',type:'scatter',symbolSize:value=>value[2]===active?12:7,
   data:spec.data.points.map((row,index)=>({name:row.name,value:[row.x,row.y,index],itemStyle:{color:index===active?style.primary:style.benchmark,opacity:1,borderColor:style.paper,borderWidth:index===active?2:1},label:{show:true,position:positions[index],distance:index===0?7:5,color:index===active?style.primary:style.ink,fontWeight:index===active?700:400}})),
   label:{...label,formatter:value=>value.name},labelLayout:{hideOverlap:false},emphasis:{scale:1.3},
   markArea:{silent:true,itemStyle:{color:style.surface},data:[[{xAxis:-40,yAxis:0},{xAxis:0,yAxis:40}]]}}];
  option.graphic=[text(3,0,spec.data.context.quadrants.down_up+'/'+spec.data.context.sample_count+'个列示板块：股价跌、营收增',{font:'700 12px '+style.font,fill:style.primary}),text(grid.left,21,'Q1营收同比',{fill:style.muted}),
   text(ledgerX,ledgerY,'板块',{font:'700 11px '+style.font}),
   text(ledgerX+ledgerWidth*.40,ledgerY,'股价',{textAlign:'right',fill:style.muted}),
   text(ledgerX+ledgerWidth*.65,ledgerY,'Q1营收',{textAlign:'right',fill:style.muted}),text(ledgerX+ledgerWidth,ledgerY,'材料观察',{textAlign:'right',fill:style.muted}),
   ...spec.data.points.map((row,index)=>{
    const y=ledgerY+23+index*rowHeight,color=index===active?style.primary:style.ink;
    return{type:'group',id:'sector-row-'+index,cursor:'pointer',onmouseover:()=>focusSector(spec.target,index),onclick:()=>inspect(spec.target,index),children:[...(index===active?[{type:'rect',shape:{x:ledgerX-3,y:y-3,width:ledgerWidth+7,height:rowHeight},style:{fill:style.surface}}]:[]),
     text(ledgerX,y,row.name,{fill:color,font:(index===active?'700 ':'')+'11px '+style.font}),
     text(ledgerX+ledgerWidth*.40,y,signed(row.x),{textAlign:'right',fill:color}),
     text(ledgerX+ledgerWidth*.65,y,signed(row.y),{textAlign:'right',fill:color}),text(ledgerX+ledgerWidth,y,row.feature,{textAlign:'right',fill:color}),
     line(ledgerX,y+rowHeight-2,ledgerX+ledgerWidth,y+rowHeight-2)]};
   })];
 }
 if(spec.scheme==='ranked_bar'){
  const d=spec.data,width=chart.getWidth(),height=chart.getHeight(),grid={left:62,right:90,top:25,bottom:23,containLabel:false},mean=d.context.mean,meanX=grid.left+mean/80*(width-grid.left-grid.right);
  option.grid=grid;option.xAxis=valueAxis(0,80,40);option.yAxis=categoryAxis(d.labels);
  option.tooltip.formatter=value=>{const index=value.value[0],gap=d.context.deviations_pp[index];return d.labels[index]+'\nQ1营收同比：+'+d.values[index]+'%\n材料三家均值：'+mean+'%\n较均值：'+(gap>0?'+':'')+gap+'个百分点'};
  option.graphic=[{...line(meanX,grid.top,meanX,height-grid.bottom,style.muted),style:{stroke:style.muted,lineWidth:.8,lineDash:[2,2]},silent:true,z:1},
   text(meanX,3,'三家均值'+mean+'%',{textAlign:'center',fill:style.muted}),text(width-3,3,'较三家均值',{textAlign:'right',fill:style.muted})];
  option.series=[{type:'custom',data:d.values.map((value,index)=>[index,value]),encode:{x:1,y:0},renderItem:(_params,api)=>{
   const index=api.value(0),value=api.value(1),end=api.coord([value,index]),gap=d.context.deviations_pp[index];
   const children=[];
   for(let unit=0;unit<value;unit++){
    const point=api.coord([unit+.5,index]),major=(unit+1)%10===0;
    children.push({type:'line',shape:{x1:point[0],y1:point[1]+5,x2:point[0],y2:point[1]-(major?8:4)},style:{stroke:style.primary,lineWidth:major?1.1:.7,opacity:major?1:.7}});
   }
   children.push({type:'text',x:end[0]+7,y:end[1],style:{text:'+'+value+'%',font:'700 11px '+style.font,fill:style.primary,verticalAlign:'middle'}},
    {type:'text',x:width-3,y:end[1],style:{text:(gap>0?'+':'')+gap+'个百分点',font:'11px '+style.font,fill:gap>0?style.primary:style.muted,textAlign:'right',verticalAlign:'middle'}});
   return{type:'group',children};
  }}];
 }
 if(spec.scheme==='percentile'){
  const width=chart.getWidth(),height=chart.getHeight(),d=spec.data,left=79,right=width-20,span=right-left,pbY=43,roeY=height-27,point=left+d.percentile/100*span,mid=left+.5*span;
  option.tooltip={show:false};
  option.graphic=[text(5,0,'PB'+d.pb.toFixed(2)+'倍',{font:'700 16px '+style.font}),text(width-5,3,'距分位中点约'+d.context.distance_percentile_points+'点',{textAlign:'right',fill:style.muted}),
   text(3,pbY-5,'历史分位'),line(left,pbY,right,pbY),
   {type:'line',shape:{x1:point,y1:pbY,x2:mid,y2:pbY},style:{stroke:style.gold,lineWidth:3}},
   ...[0,25,50,75,100].flatMap(value=>{const x=left+value/100*span;return[line(x,pbY-3,x,pbY+3),...([0,50,100].includes(value)?[text(x,pbY+10,value+'%',{textAlign:'center',fill:style.muted})]:[])]}),
   {type:'line',shape:{x1:point,y1:pbY-8,x2:point,y2:pbY+8},style:{stroke:style.primary,lineWidth:2}},
   text(point,pbY-22,'约'+d.percentile+'%分位',{textAlign:'center',font:'700 11px '+style.font,fill:style.primary}),
   text(3,roeY-5,'头部ROE'),line(left,roeY,right,roeY),
   ...[0,5,10,15].flatMap(value=>{const x=left+value/15*span;return[line(x,roeY-3,x,roeY+3),text(x,roeY+10,value+'%',{textAlign:'center',fill:style.muted})]}),
   {type:'line',shape:{x1:left+d.roe[0]/15*span,y1:roeY,x2:left+d.roe[1]/15*span,y2:roeY},style:{stroke:style.benchmark,lineWidth:4}},
   ...d.roe.map(value=>({type:'circle',shape:{cx:left+value/15*span,cy:roeY,r:3},style:{fill:style.paper,stroke:style.benchmark,lineWidth:1.5}})),
   text(left+(d.roe[0]+d.roe[1])/2/15*span,roeY-22,d.roe[0]+'%至'+d.roe[1]+'%',{textAlign:'center',font:'700 11px '+style.font})];
 }
 if(spec.scheme==='cash_index'){
  const width=chart.getWidth(),d=spec.data;
  option.grid={left:30,right:12,top:44,bottom:22,containLabel:false};
  option.xAxis={...categoryAxis(['2024','同比变化','2025']),inverse:false,axisLabel:{...label,margin:7}};
  option.yAxis={type:'value',min:0,max:150,interval:50,...frame,axisLabel:{...label,margin:5}};
  option.tooltip.formatter=value=>['2024年基准指数100，不表示现金分红金额','同比增长'+d.growth+'%，指数增加'+d.increment,'2025年指数'+d.currentIndex+'，实际现金分红'+d.cash+'亿元'][value.dataIndex];
  option.series=[{type:'custom',data:[[0,0,d.baseIndex],[1,d.baseIndex,d.currentIndex],[2,0,d.currentIndex]],encode:{x:0,y:[1,2]},renderItem:(params,api)=>{
   const index=api.value(0),lo=api.value(1),hi=api.value(2),a=api.coord([index,lo]),b=api.coord([index,hi]),half=Math.min(18,api.size([1,0])[0]*.25),color=index===0?style.benchmark:style.primary;
   const children=[{type:'rect',shape:{x:a[0]-half,y:b[1],width:half*2,height:Math.max(1,a[1]-b[1])},style:{fill:color}},
    {type:'text',x:b[0],y:b[1]-13,style:{text:index===1?'+'+d.increment:String(hi),font:'11px '+style.font,fill:style.ink,textAlign:'center'}}];
   if(index<2){const next=api.coord([index+1,hi]);children.push({type:'line',shape:{x1:b[0]+half,y1:b[1],x2:next[0]-half,y2:b[1]},style:{stroke:style.ruleStrong,lineWidth:.7}})}
   return{type:'group',children};
  }}];option.graphic=[text(2,0,d.cash+'亿元',{font:'700 17px '+style.font}),text(width-2,2,'同比+'+d.growth+'%',{textAlign:'right',font:'700 12px '+style.font,fill:style.primary}),text(3,22,'规模指数，2024=100',{fill:style.muted})];
 }
 if(spec.scheme==='payout_units'){
  const width=chart.getWidth(),d=spec.data,pitch=Math.min(9,(width*.45-8)/9),radius=Math.max(2.1,pitch*.32),x0=4,y0=22;
  option.tooltip={show:false};option.graphic=Array.from({length:d.total},(_,index)=>({type:'circle',shape:{cx:x0+(index%10)*pitch,cy:y0+Math.floor(index/10)*pitch,r:radius},style:{fill:index<d.context.prior_inferred?style.benchmark:index<d.value?style.primary:style.palette[4]}}));
  option.graphic.push(text(width*.5,24,d.value+'%',{font:'700 23px '+style.font,fill:style.primary}),text(width*.5,60,'+'+d.change+'个百分点',{font:'700 11px '+style.font,fill:style.primary}),text(width*.5,85,'上年约'+d.context.prior_inferred+'%推算',{fill:style.muted}));
 }
 if(spec.scheme==='range'){
  const d=spec.data;
  option.graphic=[text(3,0,'H−A：'+d.context.low_spread_pp.toFixed(1)+'至'+d.context.high_spread_pp.toFixed(1)+'个百分点',{font:'700 11px '+style.font,fill:style.primary})];
  option.grid={left:57,right:30,top:34,bottom:23,containLabel:false};option.xAxis=valueAxis(0,5,1);option.yAxis=categoryAxis(spec.data.ranges.map(row=>row.name));
  option.tooltip.formatter=value=>{const row=spec.data.ranges[value.seriesIndex===0?value.value[0]:value.value[1]];return row.name+'：'+row.min.toFixed(1)+'%至'+row.max.toFixed(1)+'%'};
  option.series=[{type:'custom',data:spec.data.ranges.map((row,index)=>[index,row.min,row.max]),encode:{x:[1,2],y:0},renderItem:(_params,api)=>{
   const index=api.value(0),a=api.coord([api.value(1),index]),b=api.coord([api.value(2),index]),color=index===0?style.benchmark:style.primary;
   return{type:'group',children:[{type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:color,lineWidth:4}},
    {type:'circle',shape:{cx:a[0],cy:a[1],r:3.2},style:{fill:style.paper,stroke:color,lineWidth:1.5}},
    {type:'circle',shape:{cx:b[0],cy:b[1],r:3.2},style:{fill:color,stroke:style.paper,lineWidth:.6}}]};
  }},{type:'scatter',symbolSize:0,data:spec.data.ranges.map((row,index)=>[row.max,index]),label:{...label,show:true,position:'top',distance:8,fontWeight:700,formatter:value=>{const row=spec.data.ranges[value.value[1]];return row.min.toFixed(1)+'%至'+row.max.toFixed(1)+'%'}}}];
 }
 chart.setOption(option,true);
}

// A single selection links each scatter point to its full evidence row.
function focusSector(target,index){
 const position=SPECS.findIndex(spec=>spec.target===target&&spec.scheme==='scatter');
 if(disposed||position<0||!Number.isInteger(index)||index<0||index>=SPECS[position].data.points.length)return false;
 if(selected.get(target)===index)return true;
 selected.set(target,index);render(SPECS[position],charts[position]);return true;
}
function resizeAndRender(index){
 const spec=SPECS[index],host=slots[index],chart=charts[index];
 host.style.minHeight=originalMinimums[index];
 if(spec.scheme==='scatter'){
  const required=scatterGeometry(host.clientWidth,spec.data.points.length).minimumHeight;
  if(host.clientHeight<required)host.style.minHeight=required+'px';
 }
 chart.resize({width:host.clientWidth,height:host.clientHeight,animation:{duration:0}});
 render(spec,chart);chart.getZr().flush();
}
for(const [index,spec] of SPECS.entries()){
 if(spec.scheme==='scatter')selected.set(spec.target,0);
 const chart=echarts.init(slots[index],null,{renderer:'svg'});charts.push(chart);resizeAndRender(index);
 if(spec.scheme==='scatter'){
  chart.on('mouseover',event=>{if(event.seriesType==='scatter')focusSector(spec.target,event.dataIndex)});
  chart.on('click',event=>{if(event.seriesType==='scatter')inspect(spec.target,event.dataIndex)});
  chart.getZr().on('globalout',()=>focusSector(spec.target,0));
 }
}
const redraw=()=>{if(!disposed)charts.forEach((chart,index)=>resizeAndRender(index))};
const printReset=()=>{for(const target of selected.keys())selected.set(target,0);redraw()};
let redrawFrame=null;
const observer=typeof global.ResizeObserver==='function'?new global.ResizeObserver(()=>{
 if(disposed||redrawFrame!==null)return;
 redrawFrame=global.requestAnimationFrame(()=>{redrawFrame=null;redraw()});
}):null;
if(observer)slots.forEach(host=>observer.observe(host));
global.addEventListener('resize',redraw);
global.addEventListener('beforeprint',printReset);
global.addEventListener('afterprint',printReset);
const instance={data:DATA,specs:SPECS,charts,style,focusSector,selection:target=>selected.get(target),redraw,
 destroy(){
  if(disposed)return;
  disposed=true;observer?.disconnect();
  if(redrawFrame!==null)global.cancelAnimationFrame(redrawFrame);
  global.removeEventListener('resize',redraw);
  global.removeEventListener('beforeprint',printReset);
  global.removeEventListener('afterprint',printReset);
  for(const [index,chart] of charts.entries()){chart.dispose();slots[index].style.minHeight=originalMinimums[index]}
  selected.clear();
  if(instances.get(root)===instance)instances.delete(root);
 }};
instances.set(root,instance);return instance;
 }
 global.RHEvidenceCharts=Object.freeze({mount});
})(window);
