/* Frozen observations and calculations only; offline ECharts, no requests. */
const fullNumber=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:12});
const compactNumber=value=>Math.abs(value)>=1e8?Number((value/1e8).toFixed(1))+'亿':Math.abs(value)>=1e4?Number((value/1e4).toFixed(1))+'万':fullNumber.format(value);
const dateLabel=value=>/^\d{8}$/.test(value)?value.slice(0,4)+'-'+value.slice(4,6)+'-'+value.slice(6):value;
const charts=[];
const chartInk='#28496F',chartBlue='#2867A8',chartRed='#B83C46';
const draftAxes=RHVisuals.axisFrame(RHVisuals.theme());
const gradient=(light,dark)=>new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:light},{offset:1,color:dark}]);
function comparisonOption(s){
 const niceBound=value=>{const scale=10**Math.floor(Math.log10(Math.abs(value)));return Math.ceil(Math.abs(value)/scale)*scale};
 const axis={type:'value',...draftAxes,axisLabel:{fontSize:13,color:chartInk,formatter:value=>String(Number(value.toFixed(2)))}};
 const category={type:'category',inverse:true,data:s.dates,...draftAxes,axisLabel:{fontSize:13,color:chartInk,margin:12}};
 if(s.kind==='changes'){
  return {grid:{left:12,right:16,top:22,bottom:22,containLabel:true},
   xAxis:{...axis,splitNumber:4},yAxis:[category,{...category,position:'right',
    axisLine:{show:false},axisTick:{show:false},
    data:s.values.map(value=>(value>0?'+':'')+value.toFixed(2)+'%'),axisLabel:{fontSize:13,color:chartInk,fontWeight:600,margin:12}}],
   series:[{name:dateLabel(s.period)+'同比',type:'bar',barMaxWidth:17,data:s.values,
    itemStyle:{color:gradient('#4D80B3','#205A93')},markLine:{silent:true,symbol:'none',label:{show:false},data:[{xAxis:0}],lineStyle:{color:'#ADB7C5',width:1,type:'solid'}}}]};
 }
 const series=[{name:'期间差异',type:'custom',silent:true,z:1,
  renderItem:(_params,api)=>{const i=api.value(0),a=api.coord([api.value(1),i]),b=api.coord([api.value(2),i]);
   return {type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:'#B6C1CE',lineWidth:2.5}}},
  data:s.pairs.map((pair,index)=>[index,...pair]),encode:{x:[1,2],y:0},tooltip:{show:false}}];
 for(const [index,color] of [[0,chartRed],[1,chartBlue]])series.push({name:dateLabel(s.periods[index]),type:'scatter',z:3,symbolSize:9,
  data:s.pairs.map((pair,row)=>[pair[index],row]),
  itemStyle:{color:index?color:'#FFF',borderColor:color,borderWidth:2,opacity:1},
  label:{show:true,position:index?'top':'bottom',distance:8,fontSize:13,color,
   formatter:p=>Number(p.value[0]).toFixed(2)+s.unit}});
 return {grid:{left:12,right:26,top:48,bottom:28,containLabel:true},
  legend:{data:s.periods.map(dateLabel),top:2,right:16,selectedMode:false,itemWidth:10,itemHeight:10,itemGap:20,textStyle:{fontSize:13,color:chartInk}},
  xAxis:{...axis,min:({min})=>min<0?-niceBound(min*1.3):0,max:({max})=>max>0?niceBound(max*1.25):0,splitNumber:4},
  yAxis:category,series};
}
function rawOption(s){
 const category={type:'category',data:s.dates,...draftAxes,axisLabel:{fontSize:13,hideOverlap:true,formatter:s.horizontal?undefined:dateLabel}};
 const numeric={type:'value',name:s.unit,scale:s.type==='line',...draftAxes,axisLabel:{fontSize:13,formatter:compactNumber}};
 const series={name:s.name,type:s.type,data:s.values,connectNulls:false,smooth:false,barMaxWidth:42,
  lineStyle:{width:2.5,color:chartBlue},showSymbol:false,itemStyle:{color:s.type==='line'?chartBlue:p=>new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:p.dataIndex%2?chartBlue:chartRed},{offset:1,color:p.dataIndex%2?'#174A7C':'#922C35'}])}};
 return {grid:{left:12,right:20,top:30,bottom:20,containLabel:true},xAxis:s.horizontal?numeric:category,
  yAxis:s.horizontal?{...category,inverse:true}:numeric,series:[series]};
}
const frozenFigureSpecs=__RH_DRAFT_FIGURES__;
for(const s of frozenFigureSpecs){
 const node=document.getElementById(s.target);
 const c=echarts.init(node,null,{renderer:'svg'});
 if(s.sync_group)c.group=s.sync_group;
 let option;
 try{
  const item=RHVisuals.adapt(s);
  option=RHVisuals.option(item.scheme,RHVisuals.materialize(item,item.scheme),undefined,{width:c.getWidth(),height:c.getHeight(),chart:c});
 }catch{
  if(s.kind==='changes'||s.kind==='paired')option=comparisonOption(s);
  else if(Array.isArray(s.dates)&&Array.isArray(s.values))option=rawOption(s);
  else{
   // Leave the chart title, tabular evidence and inspection links usable.
   node.textContent='原值见图表依据';
  }
 }
 if(option)c.setOption({animation:false,textStyle:{fontFamily:'Arial, PingFang SC, sans-serif',fontSize:14},
  tooltip:{trigger:s.kind==='paired'?'item':'axis',renderMode:'richText',axisPointer:{type:'none'},valueFormatter:value=>value==null?'未返回':s.kind?(Number(Array.isArray(value)?value[0]:value).toFixed(2)+s.unit):fullNumber.format(value)},...option});
 charts.push(c);
 if(s.inspection_target){
  const plot=c.getDom();
  const openBasis=(refs=[],opener=plot)=>{
   document.dispatchEvent(new CustomEvent('researchhelper:inspect',{
    detail:{target:s.inspection_target,opener,observations:refs}
   }));
  };
  c.on('click',point=>openBasis(RHVisuals.observationRefs(s,point)));
  plot.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openBasis(s.data_refs||[])}});
  plot.closest('.evidence-layout')?.querySelector('.evidence-ledger')?.addEventListener('click',event=>{
   const link=event.target.closest('a[data-chart-observation]'),ref=link?.dataset.chartObservation;
   if(!ref||!s.data_refs?.includes(ref))return;
   event.preventDefault();event.stopPropagation();openBasis([ref],link);
  });
 }
}
for(const group of new Set(frozenFigureSpecs.map(spec=>spec.sync_group).filter(Boolean)))echarts.connect(group);
addEventListener('resize',()=>charts.forEach(c=>c.resize()));
addEventListener('beforeprint',()=>charts.forEach(c=>c.resize()));

if(window.RHVisuals){const profile=document.getElementById("rh-visual-profile");RHVisuals.bindReports(frozenFigureSpecs,charts,profile?JSON.parse(profile.textContent):{});}
