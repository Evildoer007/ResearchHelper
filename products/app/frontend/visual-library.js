/* Shared offline ECharts grammar. Data and financial calculations stay frozen. */
(function(global){
 'use strict';
 const catalogue=global.RH_VISUAL_CATALOG;
 if(!catalogue)throw Error('可视化目录未载入');
 const finite=v=>typeof v==='number'&&Number.isFinite(v),numeric=v=>v===null||finite(v);
 const assert=(ok,message)=>{if(!ok)throw Error(message)};
 const themes=new Map(catalogue.themes.map(row=>[row.id,row]));
 const number=v=>v==null?'未披露':v!==0&&Math.abs(v)<.0001?Number(v).toPrecision(3):new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(v);
 // Reading precision may shorten a value, but never turn a real movement into zero.
 function readingNumber(value,digits=2){
  if(!finite(value))return number(value);
  if(value===0)return '0';
  digits=Number.isInteger(digits)?Math.max(0,Math.min(12,digits)):2;
  let shown=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits}).format(value);
  if(Number(shown.replaceAll(',',''))!==0)return shown;
  if(Math.abs(value)>=1e-6){
   const readable=Math.min(12,Math.max(digits,2-Math.floor(Math.log10(Math.abs(value)))));
   return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:readable}).format(value);
  }
  return new Intl.NumberFormat('en-US',{notation:'scientific',maximumSignificantDigits:3}).format(value).replace('E','e');
 }
 const text=value=>String(value??'');
 function theme(id='research'){assert(themes.has(id),'配色未登记');return themes.get(id)}
 function axisFrame(palette){
  // Axes stay on the plot edges. A baseline in the plot has its own meaning.
  const color=palette.muted;
  return {axisLine:{show:true,onZero:false,lineStyle:{color,width:1}},
   axisTick:{show:true,length:3,alignWithLabel:true,lineStyle:{color,width:1}},
   splitLine:{show:false}};
 }
 function readableLabel(hex){
  const rgb=hex.replace('#','').match(/.{2}/g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  const light=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];return light>.179?'#000000':'#FFFFFF';
 }
 function gradientColor(colors,ratio){
  const position=Math.max(0,Math.min(1,ratio))*(colors.length-1),index=Math.min(colors.length-2,Math.floor(position)),part=position-index;
  const rgb=colors.slice(index,index+2).map(c=>c.replace('#','').match(/.{2}/g).map(v=>parseInt(v,16)));
  return '#'+rgb[0].map((v,i)=>Math.round(v+(rgb[1][i]-v)*part).toString(16).padStart(2,'0')).join('');
 }
 function axisTickIndices(length,width){
  const count=Math.min(length,width<=430?4:6);
  return new Set(Array.from({length:count},(_,i)=>Math.round(i*(length-1)/Math.max(1,count-1))));
 }
 function timeTicks(axis,width){
  const ticks=axisTickIndices(axis.data.length,width);
  const interval=index=>ticks.has(index);
  return {...axis,axisTick:{...axis.axisTick,interval},
   axisLabel:{...axis.axisLabel,interval,showMinLabel:true,showMaxLabel:true}};
 }
 function lineLegend(legend,series){
  const names=series.map(row=>row.name);
  return {...legend(names),right:8,data:names.map(name=>({name,icon:'path://M0 4h16v2H0z'}))};
 }
 function lineLegendRows(series,width,font,displayLabel){
  const family=font+'px Arial, PingFang SC, sans-serif';
  const available=width-28;
  let rows=1,used=0;
  for(const item of series){
   const length=textWidth(String(displayLabel(item.name)),family)+36;
   if(used&&used+length>available){rows++;used=0}
   used+=length;
  }
  return rows;
 }
 function textWidth(value,font){
  return global.echarts?.format?.getTextRect(value,font).width??Array.from(value).reduce((width,char)=>
   width+parseFloat(font)*(char.charCodeAt(0)>255?1:.58),0);
 }
 function overlaps(a,b){
  return a.x<b.x+b.width+3&&a.x+a.width+3>b.x&&a.y<b.y+b.height+3&&a.y+a.height+3>b.y;
 }
 function crosses(box,[a,b]){
  const low=Math.max(box.x-3,a[0]),high=Math.min(box.x+box.width+3,b[0]);
  if(low>high)return false;
  const slope=b[0]===a[0]?0:(b[1]-a[1])/(b[0]-a[0]);
  const ys=[a[1]+(low-a[0])*slope,a[1]+(high-a[0])*slope];
  return Math.min(...ys)<=box.y+box.height+3&&Math.max(...ys)>=box.y-3;
 }
 function lineAnnotations(paths,p,font,format,unit,{gridIndex=0,keyExtrema=[],caption=true}={}){
  const family=font+'px Arial, PingFang SC, sans-serif';
  const firstPath=paths.find(path=>path.some(finite));
  const firstReading=firstPath?.findIndex(finite)??-1;
  // Anchor the annotation layer to an observed point so that it cannot
  // expand the time axis or add an artificial zero to the value axis.
  const anchor=firstReading < 0 ? [] : [[firstReading,firstPath[firstReading]]];
  return {id:'line-annotations-'+gridIndex,type:'custom',silent:true,z:8,clip:false,
   xAxisIndex:gridIndex,yAxisIndex:gridIndex,data:anchor,encode:{x:0,y:1,tooltip:[]},tooltip:{show:false},
   renderItem:({coordSys:plot},api)=>{
    const points=paths.map(path=>path.map((n,index)=>finite(n)?api.coord([index,n]):null));
    const segments=points.flatMap(path=>path.flatMap((point,index)=>point&&path[index-1]?[[path[index-1],point]]:[]));
    const placements=[],children=[],legend=[];
    function add(text,x,y){children.push({type:'text',style:{text,x,y,font:family,fill:p.ink,verticalAlign:'top'}})}
    for(const [series,values] of paths.entries()){
     const last=values.findLastIndex(finite);
     const selected=[...new Set([last,...(series===0?keyExtrema:[])])].filter(index=>index>=0&&finite(values[index]));
     for(const index of selected){
      const [pointX,pointY]=points[series][index],text=format(values[index])+(index===last?unit:'');
      const width=textWidth(text,family)+1,height=font+3;
      const x=Math.max(plot.x+2,Math.min(plot.x+plot.width-width-2,pointX-width/2));
      const adjacent=[{x,y:pointY-height-8},{x,y:pointY+8},{x:pointX+8,y:pointY-height/2},{x:pointX-width-8,y:pointY-height/2}];
      const candidates=index===last&&index===values.length-1?
       [{x:plot.x+plot.width+5,y:pointY-height/2},...adjacent]:adjacent;
      const box=candidates.map(point=>({...point,width,height})).find(candidate=>
       candidate.x>=plot.x&&candidate.y>=plot.y&&candidate.x+width<=api.getWidth()-3
       &&candidate.y+height<=plot.y+plot.height
       &&(candidate.x>=plot.x+plot.width||candidate.x+width<=plot.x+plot.width)
       &&!placements.some(other=>overlaps(candidate,other))&&!segments.some(segment=>crosses(candidate,segment)));
      if(box){placements.push(box);add(text,box.x,box.y)}
      else if(caption&&index===last)legend.push('期末 '+text);
     }
    }
    const note=[...new Set(legend)].join('　');
    const noteY=api.getHeight()-font-1;
    if(note&&noteY>=plot.y+plot.height+font+12&&textWidth(note,family)<api.getWidth()-12){
     add(note,6,noteY);
    }
    return {type:'group',children};
   }};
 }
 function dimensions(d){assert(Array.isArray(d.labels)&&d.labels.length>0,'需要明确类目或日期');return d.labels.length}
 function values(array,n){assert(Array.isArray(array)&&array.length===n&&array.every(numeric),'数据长度不一致或数值无效');return array}
 function checked(kind,d){
  assert(catalogue.schemes.some(row=>row.id===kind),'图表方案未登记');
  if(extraKinds.has(kind))return validateExtra(kind,d);
  if(['line','area','ranked_bar','lollipop','diverging_bar'].includes(kind)){
   values(d.values,dimensions(d));if(kind==='area')assert(d.measure==='scale'&&d.values.every(v=>v===null||v>=0),'面积图需要明确的非负规模');
  }else if(['multi_line','grouped_bar','stacked_bar','small_multiples','window_change'].includes(kind)){
   const n=dimensions(d);assert(Array.isArray(d.series)&&d.series.length>0,'需要实际序列');
   for(const s of d.series)values(s.values,n);
   if(kind==='small_multiples')assert(n>=3&&d.series.length>=2&&d.series.length<=4&&d.series.every(s=>s.values.some(finite)),'分面需要2至4条同尺度序列');
   if(kind==='window_change')assert(d.series.length>=2&&d.series.length<=3&&d.series.every(s=>s.values.every(finite)),'需要2至3个已核对的完整窗口');
   if(kind==='stacked_bar')assert(d.additive===true&&d.series.every(s=>s.values.every(v=>v===null||v>=0)),'构成图需要可加总非负分项');
  }else if(kind==='heatmap'){
   const n=dimensions(d);assert(Array.isArray(d.rows)&&Array.isArray(d.matrix)&&d.rows.length===d.matrix.length,'矩阵标签未对齐');
   d.matrix.forEach(row=>values(row,n));
  }else if(kind==='scatter'||kind==='scatter_rug'){
   assert(Array.isArray(d.points)&&d.points.length>0&&d.points.every(p=>p&&finite(p.x)&&finite(p.y)),'散点需要同对象的两维数值');
   if(kind==='scatter_rug')assert(scatterRugReady(d),'需要至少6对观测且每个数轴至少3个不同值');
  }else if(kind==='range'){
   assert(Array.isArray(d.ranges)&&d.ranges.length>0&&d.ranges.every(p=>finite(p.min)&&finite(p.max)&&p.min<=p.max&&(p.value==null||finite(p.value)&&p.value>=p.min&&p.value<=p.max)),'区间端点或观测值无效');
  }else if(kind==='interval_dot'){
   assert(intervalDotsReady(d),'需要2至8组有中心值的区间');
   checked('band',d);
  }else if(kind==='sample_range'){
   assert(typeof d.unit==='string'&&d.unit.length&&finite(d.minimum)&&finite(d.maximum)&&d.minimum<d.maximum
    &&Array.isArray(d.samples)&&d.samples.length>=3,'需要完整样本范围和计量单位');
   const dates=d.samples.map(row=>row.date);
   assert(orderedDateCategories(dates,date=>date)&&d.window?.start===dates[0]&&d.window?.end===dates.at(-1)
    &&d.samples.every(row=>finite(row.value)&&d.minimum<=row.value&&row.value<=d.maximum)
    &&d.samples.some(row=>row.value===d.minimum)&&d.samples.some(row=>row.value===d.maximum)
    &&d.latest===d.samples.at(-1).value,'样本端点或区间不可核对');
  }else if(kind==='boxplot'){
   const n=dimensions(d);assert(Array.isArray(d.values)&&d.values.length===n&&d.values.every(row=>row.length===5&&row.every(finite)&&row.every((v,i)=>!i||v>=row[i-1])),'箱线图需要有序五数概括');
  }else if(kind==='candlestick'){
   const n=dimensions(d);assert(Array.isArray(d.values)&&d.values.length===n&&d.values.every(row=>row.length===4&&row.every(finite)&&row[2]<=Math.min(row[0],row[1])&&row[3]>=Math.max(row[0],row[1])),'需要真实且一致的开收低高四值');
  }else if(kind==='waterfall'){
   assert(finite(d.start)&&finite(d.end)&&Array.isArray(d.steps)&&d.steps.every(s=>finite(s.value)),'变化桥需要起点、增减与终点');
   const total=d.start+d.steps.reduce((sum,row)=>sum+row.value,0);assert(Math.abs(total-d.end)<=1e-8*Math.max(1,Math.abs(total),Math.abs(d.end)),'增减项无法对应终点');
  }else if(kind==='donut'){
   values(d.values,dimensions(d));assert(d.partWhole===true&&finite(d.total)&&d.total>0&&d.values.every(v=>finite(v)&&v>=0)&&Math.abs(d.values.reduce((a,b)=>a+b,0)-d.total)<=1e-8*Math.max(1,d.total),'份额图需要非负分项及匹配的总体');
  }else if(kind==='treemap'){
   assert(Array.isArray(d.tree)&&d.tree.length>0,'需要权重层级');
   function total(node){assert(typeof node.name==='string','层级缺少名称');if(node.children){assert(Array.isArray(node.children)&&node.children.length>0,'层级为空');const sum=node.children.reduce((s,c)=>s+total(c),0);assert(node.value==null||finite(node.value)&&Math.abs(node.value-sum)<1e-8*Math.max(1,sum),'父子权重不一致');return sum}assert(finite(node.value)&&node.value>=0,'权重无效');return node.value}
   assert(d.tree.reduce((sum,row)=>sum+total(row),0)>0,'总权重须为正');
  }else if(kind==='evidence'){
   assert(Array.isArray(d.nodes)&&Array.isArray(d.links),'需要节点与引用关系');const ids=new Set(d.nodes.map(n=>n.id));
   assert(ids.size===d.nodes.length&&d.nodes.every(n=>typeof n.id==='string'&&typeof n.name==='string'),'节点标识重复或无效');
   assert(d.links.every(l=>ids.has(l.source)&&ids.has(l.target)),'引用关系指向缺失节点');
  }
  return d;
 }

 const extraKinds=new Set(['band','fan','indexed_line','drawdown','rolling','return_bars','excess_return','valuation_band','percentile','bullet','benchmark_dot','slope','butterfly','bubble_matrix','mosaic','sunburst','sankey','histogram','beeswarm','ridge','scenario_matrix','scenario_profile','ecdf','event_timeline']);
 const sampleValues=d=>Array.isArray(d.samples)?d.samples.map(row=>finite(row)?row:row?.value):[];
 function scatterRugReady(d){
  return Array.isArray(d.points)&&d.points.length>=6
   &&d.points.every(point=>point&&finite(point.x)&&finite(point.y))
   &&['x','y'].every(axis=>new Set(d.points.map(point=>point[axis])).size>=3);
 }
 function ecdfReady(d){
  const readings=sampleValues(d);
  return readings.length>=5&&readings.every(finite)&&new Set(readings).size>=3;
 }
 function intervalDotsReady(d){
  const n=d.labels?.length;
  return Array.isArray(d.labels)&&n>=2&&n<=8&&Array.isArray(d.center)&&d.center.length===n;
 }
 function scenarioProfileReady(d){
  return Array.isArray(d.xValues)&&d.xValues.length>=3&&d.xValues.length===d.labels?.length
   &&d.xValues.every((value,i)=>finite(value)&&(!i||value>d.xValues[i-1]))
   &&typeof d.xName==='string'&&d.xName.trim().length>0&&Array.isArray(d.rows)&&d.rows.length>=1&&d.rows.length<=4
   &&d.rows.every(name=>typeof name==='string'&&name.trim())&&new Set(d.rows).size===d.rows.length
   &&Array.isArray(d.matrix)&&d.matrix.length===d.rows.length
   &&d.matrix.every(row=>Array.isArray(row)&&row.length===d.xValues.length&&row.filter(finite).length>=2)
   &&d.xValues.filter((_,i)=>d.matrix.some(row=>finite(row[i]))).length>=3;
 }
 function validateExtra(kind,d){
  if(['band','valuation_band','fan'].includes(kind)){
   const n=dimensions(d),bands=kind==='fan'?d.bands:[d];assert(Array.isArray(bands)&&bands.length>0,'需要实际区间边界');
   for(const band of bands){values(band.lower,n);values(band.upper,n);assert(band.lower.every((v,i)=>finite(v)&&finite(band.upper[i])&&v<=band.upper[i]),'区间边界无效');}
   if(d.center){values(d.center,n);assert(d.center.every((v,i)=>finite(v)&&v>=bands[0].lower[i]&&v<=bands[0].upper[i]),'中心读数不在区间内');}
   if(kind==='fan')for(let b=1;b<bands.length;b++)assert(bands[b].lower.every((v,i)=>v>=bands[b-1].lower[i]&&bands[b].upper[i]<=bands[b-1].upper[i]),'多层区间必须嵌套');
   assert(d.intervalType==='scenario'||d.intervalType==='observed'||d.intervalType==='confidence'&&typeof d.method==='string'&&finite(d.level)&&d.level>0&&d.level<1,'需明确区间性质；置信区间须提供方法和水平');
  }else if(['indexed_line','rolling','excess_return','drawdown','return_bars'].includes(kind)){
   const n=dimensions(d),series=d.series||[{name:d.name||'',values:d.values}];assert(Array.isArray(series)&&series.length>0,'需要非空实际序列');series.forEach(row=>values(row.values,n));
   assert(typeof d.method==='string'&&d.method.length>0,'需要已计算结果的方法说明');
   if(kind==='indexed_line')assert(d.base===100&&series.every(row=>row.values[0]===100),'需要已计算的基期100序列');
   if(kind==='drawdown')assert(d.values.every(v=>v===null||v<=0),'回撤不能为正数');
   if(kind==='rolling')assert(typeof d.window==='string'&&d.window.length>0,'滚动窗口未提供');
   if(kind==='excess_return')assert(typeof d.benchmark==='string'&&d.benchmark.length>0,'超额收益缺少基准定义');
  }else if(kind==='percentile'){
   assert(finite(d.percentile)&&d.percentile>=0&&d.percentile<=100&&Number.isInteger(d.sampleCount)&&d.sampleCount>0&&typeof d.reference==='string'&&d.reference.length>0,'分位需要样本范围及已有计算结果');
  }else if(kind==='bullet'){
   assert(finite(d.actual)&&finite(d.target)&&Array.isArray(d.ranges)&&d.ranges.length>=2&&d.ranges.every(finite)&&d.ranges.every((v,i)=>!i||v>d.ranges[i-1])&&d.ranges[0]>=0,'目标与区段需明确且有序');
  }else if(kind==='benchmark_dot'){
   values(d.values,dimensions(d));assert(finite(d.reference)&&typeof d.referenceName==='string','缺少同口径基准');
  }else if(kind==='slope'){
   const n=dimensions(d);
   assert(Array.isArray(d.pairs)&&d.pairs.length===n&&d.pairs.every(row=>row.length===2&&row.every(finite)),'需要同对象两期数值');
   assert(Array.isArray(d.periods)&&d.periods.length===2,'需要两期名称');
  }else if(kind==='butterfly'){
   const n=dimensions(d);values(d.left,n);values(d.right,n);assert([...d.left,...d.right].every(v=>finite(v)&&v>=0),'双向数量必须非负');
  }else if(['bubble_matrix','mosaic','scenario_matrix','scenario_profile'].includes(kind)){
   checked('heatmap',d);
   if(kind==='bubble_matrix'||kind==='mosaic')assert(d.matrix.every(row=>row.every(v=>v===null&&kind==='bubble_matrix'||finite(v)&&v>=0)),'数量矩阵需要非负数值');
   if(kind==='mosaic')assert(d.matrix.flat().some(v=>v>0)&&d.partWhole===true,'联合构成需要明确总体');
   if(kind.startsWith('scenario_'))assert(d.scenario===true&&typeof d.method==='string'&&d.method.trim().length>0,'需要明确假设和已算情景结果');
   if(kind==='scenario_profile')assert(scenarioProfileReady(d),'需要严格递增的数值假设和有效情景结果');
  }else if(kind==='sunburst')checked('treemap',d);
  else if(kind==='sankey'){
   assert(Array.isArray(d.nodes)&&Array.isArray(d.links),'流向需要节点与流量');const ids=new Set(d.nodes.map(n=>n.name));
   assert(ids.size===d.nodes.length&&d.links.every(l=>ids.has(l.source)&&ids.has(l.target)&&finite(l.value)&&l.value>=0),'流向或流量无效');
  }else if(kind==='histogram'){
   assert(Array.isArray(d.edges)&&Array.isArray(d.counts)&&d.edges.length===d.counts.length+1&&d.edges.every(finite)&&d.edges.every((v,i)=>!i||v>d.edges[i-1])&&d.counts.every(v=>Number.isInteger(v)&&v>=0),'分箱及频数无效');
   const width=d.edges[1]-d.edges[0];assert(d.edges.every((v,i)=>!i||Math.abs(v-d.edges[i-1]-width)<1e-8*Math.max(1,width)),'非等宽分箱需先提供登记方法计算的密度');
  }else if(kind==='ecdf')assert(ecdfReady(d),'需要至少5条原值和3个不同读数');
  else if(kind==='beeswarm')assert(Array.isArray(d.samples)&&d.samples.length>1&&d.samples.every(finite),'需要逐条原始观测');
  else if(kind==='ridge'){
   assert(Array.isArray(d.curves)&&d.curves.length>1&&typeof d.method==='string'&&d.method.length>0,'需要已计算密度及方法');
   for(const curve of d.curves)assert(Array.isArray(curve.x)&&Array.isArray(curve.y)&&curve.x.length===curve.y.length&&curve.x.length>2&&curve.x.every(finite)&&curve.y.every(v=>finite(v)&&v>=0),'密度网格无效');
  }else if(kind==='event_timeline')assert(Array.isArray(d.events)&&d.events.length>0&&d.events.every(e=>typeof e.date==='string'&&Number.isFinite(Date.parse(e.date))&&typeof e.name==='string'&&['已发生','已公告','待观察'].includes(e.state)),'事件日期与状态需明确');
  return d;
 }
 function lineSymbols(values){
  const isolated=values.map((value,index)=>finite(value)&&!finite(values[index-1])&&!finite(values[index+1]));
  return isolated.some(Boolean)?{showSymbol:true,symbolSize:5,
   symbol:(_value,point)=>isolated[point.dataIndex]?'rect':'none'}:{showSymbol:false};
 }
 function additionalOption(kind,d,p,h){
  const {category,value,legend,color,size}=h,baseLine=(name,data,i=0)=>({name,type:'line',data,smooth:false,
   connectNulls:false,...lineSymbols(data),lineStyle:{width:2.6,color:color(i)},itemStyle:{color:color(i)}});
  if(['indexed_line','rolling','excess_return','drawdown'].includes(kind)){
   const series=(d.series||[{name:d.name||d.window||d.benchmark||'实际读数',values:d.values}]).map((row,i)=>baseLine(row.name,row.values,i));
   if(kind==='drawdown')series[0].areaStyle={color:p.ramp[1],opacity:.9};
   if(kind==='indexed_line'||kind==='excess_return')series[0].markLine={silent:true,symbol:'none',
    data:[{yAxis:kind==='indexed_line'?100:0}],label:{show:kind==='indexed_line',formatter:'基期100',fontSize:13,color:p.ink},
    lineStyle:{color:p.muted,type:'solid',width:1}};
   return {legend:series.length>1?lineLegend(legend,series):undefined,xAxis:timeTicks(category(d.labels),size.width),yAxis:value(kind==='drawdown'?{max:0}:{scale:true}),series};
  }
  if(kind==='return_bars')return {xAxis:category(d.labels),yAxis:value(),series:[{type:'bar',data:d.values,barMaxWidth:30,itemStyle:{color:v=>v.value>=0?p.positive:p.negative}}]};
  if(['band','fan','valuation_band'].includes(kind)){
   const bands=kind==='fan'?d.bands:[d],extent=bands.flatMap(b=>[...b.lower,...b.upper]),low=Math.min(...extent),high=Math.max(...extent),pad=(high-low||1)*.08;
   const series=bands.map((band,i)=>({name:band.name||'区间',type:'custom',silent:true,data:[0],renderItem:(_params,api)=>({type:'polygon',shape:{points:[...band.lower.map((v,x)=>api.coord([x,v])),...band.upper.map((v,x)=>api.coord([x,v])).reverse()]},style:{fill:p.ramp[Math.min(i+1,p.ramp.length-2)],opacity:.7}})}));
   series.push({...baseLine('下界',bands[0].lower),lineStyle:{opacity:0},symbol:'none'});series.push({...baseLine('上界',bands[0].upper),lineStyle:{opacity:0},symbol:'none'});
   if(d.center)series.push(baseLine(kind==='valuation_band'?'实际估值':'中心读数',d.center));
   return {xAxis:category(d.labels),yAxis:value({min:low-pad,max:high+pad}),series};
  }
  if(kind==='percentile')return {xAxis:value({name:'历史分位%',min:0,max:100,interval:25}),yAxis:category([d.name||'当前分位']),series:[{type:'bar',data:[100],barWidth:8,itemStyle:{color:p.ramp[0]},silent:true},{type:'scatter',data:[[d.percentile,0]],symbolSize:16,itemStyle:{color:p.primary},label:{show:true,position:'top',fontSize:14,color:p.ink,formatter:number(d.percentile)+'%'}}]};
  if(kind==='bullet')return {xAxis:value({min:0,max:Math.max(d.ranges.at(-1),d.actual,d.target)*1.05}),yAxis:category([d.name||'当前值']),series:[...d.ranges.map((v,i)=>({type:'bar',data:[v],barWidth:28,barGap:'-100%',silent:true,z:d.ranges.length-i,itemStyle:{color:p.ramp[Math.min(i,p.ramp.length-1)]}})),{type:'bar',data:[d.actual],barWidth:10,barGap:'-100%',z:6,itemStyle:{color:p.primary}},{type:'scatter',symbol:'rect',symbolSize:[3,38],data:[[d.target,0]],z:7,itemStyle:{color:p.accent},label:{show:true,position:'top',fontSize:13,color:p.ink,formatter:'目标'+number(d.target)}}]};
  if(kind==='benchmark_dot')return {xAxis:value({scale:true}),yAxis:category(d.labels,{inverse:true}),series:[{type:'scatter',data:d.values.map((v,i)=>[v,i]),symbolSize:9,itemStyle:{color:p.primary},markLine:{silent:true,symbol:'none',data:[{xAxis:d.reference}],label:{show:true,formatter:d.referenceName,fontSize:13,color:p.ink},lineStyle:{color:p.accent,type:'dashed',width:1}}}]};
  if(kind==='slope')return {legend:legend(d.labels),xAxis:category(d.periods,{boundaryGap:true}),yAxis:value({scale:true}),series:d.pairs.map((pair,i)=>baseLine(d.labels[i],pair,i))};
  if(kind==='butterfly')return {legend:legend([d.leftName||'左组',d.rightName||'右组']),xAxis:value({axisLabel:{fontSize:13,color:p.ink,formatter:v=>number(Math.abs(v))}}),yAxis:category(d.labels,{inverse:true}),series:[{name:d.leftName||'左组',type:'bar',stack:'pair',data:d.left.map(v=>-v),barMaxWidth:18,itemStyle:{color:p.primary}},{name:d.rightName||'右组',type:'bar',stack:'pair',data:d.right,barMaxWidth:18,itemStyle:{color:p.accent}}]};
  if(kind==='bubble_matrix'){
   const points=d.matrix.flatMap((row,y)=>row.flatMap((v,x)=>v===null?[]:[[x,y,v]])),max=Math.max(...points.map(row=>row[2]),1);
   return {xAxis:category(d.labels),yAxis:category(d.rows,{inverse:true}),series:[{type:'scatter',data:points,symbolSize:v=>Math.sqrt(v[2]/max)*34,itemStyle:{color:p.primary}}]};
  }
  if(kind==='scenario_matrix')return option('heatmap',{...d},p.id,size);
  if(kind==='scenario_profile'){
   const series=d.rows.map((name,i)=>baseLine(name,d.matrix[i].map((value,j)=>[d.xValues[j],value]),i));
   const xAxis=value({scale:true,name:d.xName+(d.xUnit||''),nameLocation:'middle',nameGap:26});
   xAxis.axisLabel.formatter=reading=>readingNumber(reading,12);
   return {grid:{left:14,right:26,top:36,bottom:42,containLabel:true},
    legend:series.length>1?legend(d.rows):undefined,xAxis,yAxis:value({scale:true}),
    tooltip:{trigger:'item',renderMode:'richText',formatter:item=>item.seriesName+'\n'+d.xName+' '+number(item.value[0])+(d.xUnit||'')+'\n结果 '+number(item.value[1])+(d.unit||'')},
    series};
  }
  if(kind==='mosaic'){
   const totals=d.labels.map((_,x)=>d.matrix.reduce((sum,row)=>sum+row[x],0)),total=totals.reduce((a,b)=>a+b,0);let start=0;const rects=[];
   totals.forEach((amount,x)=>{let bottom=0;d.rows.forEach((name,y)=>{const height=amount?d.matrix[y][x]/amount*100:0;rects.push([start,start+amount/total*100,bottom,bottom+height,y,x]);bottom+=height});start+=amount/total*100});
   return {xAxis:value({name:'总体份额%',min:0,max:100}),yAxis:value({name:'组内份额%',min:0,max:100}),series:[{type:'custom',data:rects,encode:{x:[0,1],y:[2,3]},renderItem:(_params,api)=>{const a=api.coord([api.value(0),api.value(2)]),b=api.coord([api.value(1),api.value(3)]);return {type:'rect',shape:{x:a[0],y:b[1],width:b[0]-a[0],height:a[1]-b[1]},style:{fill:color(api.value(4)),stroke:p.paper,lineWidth:2}}}}]};
  }
  if(kind==='sunburst')return {tooltip:{trigger:'item',renderMode:'richText'},series:[{type:'sunburst',data:d.tree,radius:['10%','86%'],nodeClick:false,label:{fontSize:13,color:p.ink},itemStyle:{borderColor:p.paper,borderWidth:3},color:p.colors}]};
  if(kind==='sankey')return {tooltip:{trigger:'item',renderMode:'richText'},series:[{type:'sankey',data:d.nodes,links:d.links,left:16,right:90,top:24,bottom:24,label:{fontSize:13,color:p.ink},nodeWidth:14,nodeGap:20,lineStyle:{color:'source',opacity:.4},itemStyle:{borderWidth:0},emphasis:{focus:'adjacency'}}]};
  if(kind==='histogram')return {xAxis:category(d.counts.map((_,i)=>number(d.edges[i])+'~'+number(d.edges[i+1]))),yAxis:value({name:'频数',min:0}),series:[{type:'bar',data:d.counts,barGap:0,barCategoryGap:'2%',itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:1}}]};
  if(kind==='ecdf'){
   const readings=sampleValues(d).sort((a,b)=>a-b),n=readings.length;
   const points=readings.flatMap((reading,i)=>reading===readings[i+1]?[]:[[reading,100*(i+1)/n,i+1]]);
   const marker=points.find(point=>point[0]===d.latest);
   const series=[{type:'line',step:'end',lineStyle:{color:p.primary,width:2},showSymbol:false,silent:true,
    data:[[points[0][0],0,0],...points]},
    {name:'样本读数',type:'scatter',z:4,symbolSize:6,data:points,itemStyle:{color:p.primary}}];
   if(marker)series.push({name:'期末',type:'scatter',z:5,symbol:'diamond',symbolSize:12,data:[marker],
    itemStyle:{color:p.accent},label:{show:true,formatter:'期末',position:'top',color:p.ink}});
   const yAxis=value({name:'累计占比',min:0,max:100,interval:25});
   yAxis.axisLabel.formatter=reading=>reading+'%';
   return {xAxis:value({scale:true}),yAxis,
    tooltip:{trigger:'item',renderMode:'richText',formatter:item=>number(item.value[0])+(d.unit||'')
     +'\n累计 '+readingNumber(item.value[1])+'%（'+item.value[2]+'/'+n+'）'},
    series};
  }
  if(kind==='beeswarm'){
   const sorted=[...d.samples].sort((a,b)=>a-b),low=sorted[0],span=sorted.at(-1)-low||1,bins=new Map();const points=sorted.map(v=>{const bucket=Math.round((v-low)/span*30),i=bins.get(bucket)||0;bins.set(bucket,i+1);return [v,i?Math.ceil(i/2)*(i%2?1:-1):0]});
   return {xAxis:value({scale:true}),yAxis:{type:'value',show:false,min:-Math.max(1,...points.map(v=>Math.abs(v[1])))-1,max:Math.max(1,...points.map(v=>Math.abs(v[1])))+1},series:[{type:'scatter',data:points,symbolSize:7,itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:.5}}]};
  }
  if(kind==='ridge'){
   const max=Math.max(...d.curves.flatMap(c=>c.y),1e-10),xs=d.curves.flatMap(c=>c.x),series=d.curves.map((curve,i)=>({name:curve.name,type:'custom',data:[0],renderItem:(_params,api)=>({type:'polygon',shape:{points:[api.coord([curve.x[0],i]),...curve.x.map((x,k)=>api.coord([x,i+curve.y[k]/max*.8])),api.coord([curve.x.at(-1),i])]},style:{fill:color(i),stroke:p.paper,lineWidth:1,opacity:.9}})}));
   return {xAxis:value({min:Math.min(...xs),max:Math.max(...xs)}),yAxis:value({min:0,max:d.curves.length,interval:1,axisLabel:{fontSize:13,color:p.ink,formatter:v=>d.curves[v]?.name||''}}),series};
  }
  if(kind==='event_timeline'){
   const points=d.events.map((event,i)=>({value:[Date.parse(event.date),1],name:event.name,symbol:event.state==='待观察'?'emptyCircle':'circle',symbolSize:10,itemStyle:{color:event.state==='已发生'?p.primary:p.accent},label:{show:true,formatter:event.name,position:i%2?'bottom':'top',distance:i%2?20:22,fontSize:13,color:p.ink}}));
   return {xAxis:{type:'time',...axisFrame(p),axisLabel:{fontSize:13,color:p.ink,formatter:v=>new Date(v).toISOString().slice(5,10)}},yAxis:{type:'value',min:0,max:2,show:false,splitLine:{show:false}},series:[{type:'line',data:points,symbolSize:10,lineStyle:{color:p.muted,width:1.5},labelLayout:{hideOverlap:true}}]};
  }
  throw Error('方案尚未实现');
 }

 function orderedDateCategories(labels,displayLabel){
  let previous='';
  return labels.every(raw=>{
   const label=String(displayLabel(raw));
   const day=/^\d{4}-\d{2}$/.test(label)?label+'-01':label;
   if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day.startsWith('0000')||day<=previous)return false;
   const parsed=new Date(day+'T00:00:00Z');
   if(!Number.isFinite(parsed.getTime())||parsed.toISOString().slice(0,10)!==day)return false;
   previous=day;
   return true;
  });
 }
 function readableBounds(low,high,intervals,padding){
  const span=high-low||Math.max(Math.abs(high)*.1,1),raw=span/intervals;
  const exponent=Math.floor(Math.log10(raw)),scale=10**exponent;
  const multiple=[1,2,2.5,5,10].find(value=>value*scale>=raw),step=multiple*scale;
  if(!finite(step)||step<=0)return {min:low,max:high};
  // Round to the tick's decimal place. A fixed significant-digit cap can
  // collapse distinct observations near a large common level.
  const decimals=Math.min(100,Math.max(0,-exponent+(multiple===2.5?1:0)));
  const clean=value=>Number(value.toFixed(decimals));
  return {min:Math.min(low,clean(Math.floor((low-span*padding)/step)*step)),
   max:Math.max(high,clean(Math.ceil((high+span*padding)/step)*step)),interval:clean(step)||step};
 }
 function barLabelDomain(amounts,plotWidth,leftLabel,rightLabel,ticks){
  const low=Math.min(0,...amounts),high=Math.max(0,...amounts);
  if(low===high||plotWidth<=leftLabel+rightLabel)return {};
  const {interval}=readableBounds(low,high,ticks,0);
  if(!interval)return {};
  // Each rounded edge can add one tick. Include that space when converting
  // text widths to axis units, so the final domain still clears both axes.
  const rounding=Number(leftLabel>0)+Number(rightLabel>0);
  const unitsPerPixel=(high-low+rounding*interval)/(plotWidth-leftLabel-rightLabel);
  return {min:leftLabel?Math.floor((low-leftLabel*unitsPerPixel)/interval)*interval:low,
   max:rightLabel?Math.ceil((high+rightLabel*unitsPerPixel)/interval)*interval:high,interval};
 }
 function marginalMarks(d,p){
  return {
   type:'custom',name:'边沿观测',silent:true,
   data:d.points.map(point=>[point.x,point.y]),z:1,
   renderItem:({coordSys},api)=>{
    const [x,y]=api.coord([api.value(0),api.value(1)]);
    const top=coordSys.y+1,right=coordSys.x+coordSys.width-1;
    const style={stroke:p.accent,lineWidth:1.3,opacity:.62};
    return {type:'group',children:[
     {type:'line',shape:{x1:x,y1:top,x2:x,y2:top+7},style},
     {type:'line',shape:{x1:right-7,y1:y,x2:right,y2:y},style}
    ]};
   }
  };
 }
 function smallMultiples(d,p,h){
  const {category,value,size,font,displayLabel,labelNumber,labelUnit,color}=h;
  const readings=d.series.flatMap(row=>row.values.filter(finite));
  const low=Math.min(...readings),high=Math.max(...readings),domain=readableBounds(low,high,2,.04);
  const rowHeight=((size.height||270)-20)/d.series.length;
  const grid=[],xAxis=[],yAxis=[],title=[];
  const last=d.series.length-1;
  const series=d.series.map((row,index)=>{
   const top=index*rowHeight;
   title.push({text:displayLabel(row.name),left:46,top:top+1,padding:0,
    textStyle:{fontSize:font,fontWeight:600,color:p.ink,width:Math.max(30,size.width-68),overflow:'truncate'}});
   grid.push({left:46,right:50,top:top+22,height:Math.max(18,rowHeight-28)});
   const time=timeTicks(category(d.labels,{gridIndex:index,boundaryGap:false}),size.width);
   time.axisTick.show=index===last;
   time.axisLabel={...time.axisLabel,show:index===last,showMinLabel:true,showMaxLabel:true,margin:5};
   xAxis.push(time);
   yAxis.push(value({gridIndex:index,name:'',...domain}));
   return {name:row.name,type:'line',xAxisIndex:index,yAxisIndex:index,data:row.values,
    connectNulls:false,smooth:false,...lineSymbols(row.values),
    lineStyle:{width:2.1,color:color(index)},itemStyle:{color:color(index)}};
  });
  series.push(...d.series.map((row,index)=>lineAnnotations([row.values],p,font,labelNumber,labelUnit,{gridIndex:index,caption:false})));
  return {grid,xAxis,yAxis,title,series,tooltip:{trigger:'axis',renderMode:'richText',formatter:points=>{
   const point=(Array.isArray(points)?points:[points]).find(item=>item.seriesType==='line');
   return point?[
   displayLabel(point.seriesName),displayLabel(d.labels[point.dataIndex]),number(point.value)+(d.unit||'')].join('\n'):''}}};
 }
 function windowChanges(d,p,h){
  const {category,value,legend,size,font,base,labelNumber,color}=h;
  const bound=Math.max(...d.series.flatMap(row=>row.values.map(Math.abs)))||1;
  const domain=readableBounds(-bound,bound,size.width<500?3:5,.12);
  const labelWidth=Math.min(164,Math.round(size.width*.34));
  const axisLabels=category(d.labels).axisLabel;
  const signed=amount=>(amount>0?'+':'')+labelNumber(amount);
  const series=d.series.map((row,index)=>({name:row.name,type:'bar',data:row.values.map(amount=>({value:amount,
   label:{position:amount<0?'left':'right'}})),barMaxWidth:13,barGap:'24%',itemStyle:{color:color(index),borderRadius:2},
   label:{show:true,distance:4,fontSize:font,color:p.ink,formatter:point=>signed(point.value)},
   labelLayout:{hideOverlap:true},
   ...(index===0?{markLine:{silent:true,symbol:'none',label:{show:false},data:[{xAxis:0}],lineStyle:{color:p.muted,type:'solid'}}}:{})}));
  return {grid:{...base.grid,left:10,right:20,top:d.series.length>2&&size.width<580?55:38,bottom:24},
   legend:{...legend(d.series.map(row=>row.name)),itemGap:13},
   xAxis:value({name:'',...domain}),
   yAxis:category(d.labels,{inverse:true,axisLabel:{...axisLabels,interval:0,width:labelWidth,overflow:'break'}}),
   series,tooltip:{trigger:'axis',renderMode:'richText',formatter:points=>[
    d.labels[points[0]?.dataIndex],...points.map(point=>point.seriesName+' '+signed(point.value)+(d.unit||''))].join('\n')}};
 }
 function sampleRange(d,p,h){
  const {size,base,value,font}=h;
  const domain=readableBounds(d.minimum,d.maximum,size.width<450?3:5,.12);
  const text=amount=>readingNumber(amount,2)+d.unit;
  const exact=amount=>readingNumber(amount,12)+d.unit;
  return {grid:{...base.grid,left:24,right:26,top:43,bottom:32},
   xAxis:value({name:'',...domain,axisLabel:{fontSize:font,color:p.ink,hideOverlap:true,formatter:amount=>readingNumber(amount,12)}}),
   yAxis:{type:'value',min:0,max:1,show:false},
   tooltip:{trigger:'item',renderMode:'richText',formatter:point=>{
    if(point.seriesIndex===0)return '样本区间\n'+exact(d.minimum)+'至'+exact(d.maximum);
    if(point.seriesIndex===1){const row=d.samples[point.dataIndex];return row.date+'\n'+exact(row.value)}
    return d.window.end+'\n期末 '+exact(d.latest);
   }},series:[
    {type:'custom',data:[[d.minimum,d.maximum]],encode:{x:[0,1]},renderItem:(_params,api)=>{
     const low=api.coord([api.value(0),.5]),high=api.coord([api.value(1),.5]);
     const family=font+'px Arial, PingFang SC, sans-serif';
     const labels=[text(d.minimum),text(d.maximum)];
     const halfWidths=labels.map(label=>textWidth(label,family)/2);
     const positions=[low,high].map(([x],index)=>Math.max(halfWidths[index]+3,
      Math.min(x,api.getWidth()-halfWidths[index]-3)));
     const collapsed=positions[1]-positions[0]<halfWidths[0]+halfWidths[1]+8;
     return {type:'group',children:[
      {type:'line',shape:{x1:low[0],y1:low[1],x2:high[0],y2:high[1]},style:{stroke:p.ramp[2],lineWidth:6}},
      ...[low,high].map(([x,y])=>({type:'line',shape:{x1:x,y1:y-6,x2:x,y2:y+6},style:{stroke:p.primary,lineWidth:2}})),
      ...[low,high].map(([,y],index)=>({type:'text',style:{x:positions[index],y:y-12-index*(collapsed?font+4:0),text:labels[index],
       align:'center',verticalAlign:'bottom',font:family,fill:p.ink}}))
     ]};
    }},
    {type:'scatter',name:'样本',data:d.samples.map(row=>[row.value,.5]),symbolSize:7,z:3,
     itemStyle:{color:p.paper,borderColor:p.primary,borderWidth:1.8,opacity:1}},
    {type:'scatter',name:'期末',data:[[d.latest,.5]],symbol:'diamond',symbolSize:13,z:4,
     itemStyle:{color:p.accent,borderColor:p.paper,borderWidth:1},label:{show:true,position:'bottom',distance:8,
      fontSize:font,color:p.ink,formatter:'期末 '+text(d.latest)}}]};
 }

 function option(kind,data,themeID='research',size={width:800}){
  const d=checked(kind,data),p=theme(themeID),colors=p.colors,font=size.width<=430?11:12;
  if(kind==='interval_dot'){
   const ranges=d.labels.map((name,i)=>({name,min:d.lower[i],max:d.upper[i],value:d.center[i]}));
   const result=option('range',{...d,ranges},themeID,size);
   if(size.height&&size.height<42+28*ranges.length)result.series[1].label.show=false;
   const caption=d.intervalType==='confidence'?number(d.level*100)+'%置信区间'
    :d.intervalType==='scenario'?'情景区间':'观测区间';
   result.title={text:caption,left:12,top:3,textStyle:{fontSize:font,color:p.muted,fontWeight:400}};
   result.tooltip.formatter=item=>{
    const row=ranges[item.seriesIndex===0?item.value[0]:item.value[1]];
    const unit=d.unit||'';
    return [row.name,caption+' '+number(row.min)+'至'+number(row.max)+unit,
     '中心值 '+number(row.value)+unit,d.method].filter(Boolean).join('\n');
   };
   return result;
  }
  const observed=[...(d.values||[]).flat(),...(d.pairs||[]).flat(),...(d.series||[]).flatMap(row=>row.values||[]),...(d.points||[]).flatMap(row=>[row.x,row.y]),...sampleValues(d)].filter(finite);
  const magnitude=observed.length?Math.max(...observed.map(Math.abs)):0;
  const exponent=d.unit&&!['%','倍','个百分点'].includes(d.unit)&&magnitude>=1e6?3*Math.floor(Math.log10(magnitude)/3):0;
  const divisor=10**exponent,sup=value=>String(value).split('').map(c=>'⁰¹²³⁴⁵⁶⁷⁸⁹'[Number(c)]).join('');
  const plotUnit=(d.unit||'')+(exponent?'×10'+sup(exponent):'');
  const labelNumber=v=>finite(v)?readingNumber(v/divisor,d.numberDigits):number(v);
  // Tick spacing belongs to the scale, not the precision of observations.
  // Whole-number observations can still produce half-unit ticks.
  const axisNumber=v=>finite(v)?readingNumber(v/divisor,12):number(v);
  const labelUnit=exponent||d.unit==='原值'?'':d.unit||'';
  const displayLabel=value=>Object.prototype.hasOwnProperty.call(d.dateLabels||{},String(value))&&typeof d.dateLabels[String(value)]==='string'?d.dateLabels[String(value)]:value;
  const color=i=>d.comparison_periods?.length===2?(i===0?p.accent:p.primary):colors[i%Math.min(colors.length,6)];
  const category=(labels,extra={})=>({type:'category',data:labels,...axisFrame(p),axisLabel:{fontSize:font,color:p.ink,hideOverlap:true,formatter:d.dateLabels||d.compactDates?v=>{const label=displayLabel(v);return d.compactDates&&/^\d{4}-\d{2}-\d{2}$/.test(String(label))?String(label).slice(5):label}:undefined},...extra});
  const denseDates=['lollipop','ranked_bar','diverging_bar','grouped_bar','return_bars'].includes(kind)&&d.labels.length>12&&orderedDateCategories(d.labels,displayLabel);
  const dateCategory=()=>{
   const axis=category(d.labels),last=d.labels.length-1;
   const stride=Math.max(1,Math.ceil(last/(size.width<420?2:4)));
   axis.axisLabel={...axis.axisLabel,formatter:displayLabel,showMinLabel:true,showMaxLabel:true,
    interval:i=>i===0||i===last||(i%stride===0&&last-i>=stride/2)};
   return axis;
  };
  const value=(extra={})=>({type:'value',name:plotUnit.length<=12?plotUnit:'',nameGap:8,...axisFrame(p),axisLabel:{fontSize:font,color:p.ink,hideOverlap:true,formatter:axisNumber},nameTextStyle:{fontSize:font,color:p.muted},...extra});
  const base={animation:false,color:colors,textStyle:{fontFamily:'Arial, PingFang SC, sans-serif',fontSize:14,color:p.ink},
   tooltip:{trigger:'axis',renderMode:'richText',axisPointer:{type:'none'},valueFormatter:v=>Array.isArray(v)?v.map(number).join('，'):number(v)},
   grid:{left:denseDates?38:14,right:denseDates?42:26,top:34,bottom:22,containLabel:true}};
  const legend=names=>({data:names,formatter:d.dateLabels?displayLabel:undefined,top:0,left:8,selectedMode:false,itemWidth:12,itemHeight:10,textStyle:{fontSize:font,color:p.ink},itemGap:18});
  const coordinates={category,value,legend,size,font,base,displayLabel,labelNumber,labelUnit,color};
  if(kind==='small_multiples')return {...base,...smallMultiples(d,p,coordinates)};
  if(kind==='window_change')return {...base,...windowChanges(d,p,coordinates)};
  if(kind==='sample_range')return {...base,...sampleRange(d,p,coordinates)};
  if(extraKinds.has(kind))return {...base,...additionalOption(kind,d,p,{category:denseDates?dateCategory:category,value,legend,color,size})};
  let o={};
  if(['line','area','multi_line'].includes(kind)){
   const series=kind==='multi_line'?d.series:[{name:d.name||'',values:d.values}];
   const plots=series.map((s,i)=>({
     name:s.name,type:'line',data:s.values,smooth:false,connectNulls:false,
     ...lineSymbols(s.values),
     lineStyle:{width:2.8,color:color(i)},
     itemStyle:{color:color(i)},areaStyle:kind==='area'?{color:p.ramp[1],opacity:1}:undefined}));
   const desiredRight=Math.max(42,...series.map(row=>{
    const last=row.values.findLast(finite);
    return textWidth(finite(last)?labelNumber(last)+labelUnit:'',font+'px Arial, PingFang SC, sans-serif')+12;
   }));
   const right=d.endAnnotation===false?base.grid.right:Math.min(desiredRight,Math.max(32,Math.round(size.width*.27)));
   const legendRows=series.length>1?lineLegendRows(series,size.width,font,displayLabel):0;
   const singleRow=legendRows>2||(legendRows>1&&size.height&&size.height<180);
   let lineKey=legendRows?lineLegend(legend,series):undefined;
   if(singleRow){
    lineKey={...lineKey,type:'scroll',pageIconSize:10,
     textStyle:{...lineKey.textStyle,width:Math.max(48,size.width-112),overflow:'truncate'}};
   }
   const top=Math.max(base.grid.top,(singleRow?1:legendRows)*(font+14)+font+12);
   o={grid:{...base.grid,right,top,bottom:base.grid.bottom+font},
    legend:lineKey,
    xAxis:timeTicks(category(d.labels),size.width),
    yAxis:value({scale:kind!=='area',splitNumber:4,boundaryGap:kind==='area'?[0,.1]:[.08,.12]}),
    series:d.endAnnotation===false?plots:[...plots,lineAnnotations(series.map(row=>row.values),p,font,labelNumber,labelUnit,{keyExtrema:d.keyExtrema})]};
  }else if(['ranked_bar','diverging_bar','lollipop'].includes(kind)){
   const showBarLabels=kind!=='lollipop'&&!denseDates;
   const bar={name:d.name||'',type:'bar',data:d.values,barMaxWidth:kind==='lollipop'?2:18,
    itemStyle:{color:kind==='diverging_bar'&&(themeID==='research'||p.diverging)?v=>v.value>=0?p.positive:p.negative:p.primary,
     opacity:denseDates && kind==='lollipop' ? .32 : 1,borderRadius:denseDates?0:[0,2,2,0]},
    label:{show:false}};
   // Bar and point layers draw one observation. Read that source row once;
   // the point's category index is a coordinate, never another financial value.
   const observationTooltip=params=>{
    const rows=Array.isArray(params)?params:[params];
    const row=rows.find(item=>Number.isInteger(item?.dataIndex)&&item.dataIndex>=0&&item.dataIndex<d.values.length);
    if(!row)return '';
    const index=row.dataIndex,amount=d.values[index];
    return [displayLabel(d.labels[index]),number(amount)+(amount==null?'':d.unit||'')].join('\n');
   };
   o={grid:{...base.grid,right:denseDates?base.grid.right:60},xAxis:denseDates?dateCategory():value(),yAxis:denseDates?value():category(d.labels,{inverse:true}),
    tooltip:{...base.tooltip,formatter:observationTooltip},series:[bar]};
   if(showBarLabels){
    const family=font+'px Arial, PingFang SC, sans-serif';
    const reading=amount=>(kind==='diverging_bar'&&amount>0?'+':'')+labelNumber(amount);
    const neg=Math.max(0,...d.values.filter(value=>finite(value)&&value<0).map(value=>textWidth(reading(value),family)+5));
    const pos=Math.max(0,...d.values.filter(value=>finite(value)&&value>=0).map(value=>textWidth(reading(value),family)+5));
    const categoryWidth=Math.min(size.width*.34,Math.max(0,...d.labels.map(label=>textWidth(String(displayLabel(label)),family))));
    const unitWidth=textWidth(plotUnit,family)+14;
    o.grid.right=Math.max(22,unitWidth);
    const margin=12,plotWidth=size.width-o.grid.left-o.grid.right-categoryWidth-margin-8;
    const fit=plotWidth>=neg+pos+64;
    o.yAxis.axisLabel={...o.yAxis.axisLabel,width:Math.round(size.width*.34),overflow:'truncate',margin};
    if(fit){
     const domain=barLabelDomain(d.values.filter(finite),plotWidth,neg&&neg+4,pos&&pos+4,size.width<430?4:5);
     Object.assign(o.xAxis,domain);
     bar.label={show:true,position:'right',distance:3,color:p.ink,fontSize:font,formatter:point=>reading(point.value)};
     bar.data=d.values.map(value=>value===null?null:{value,label:{position:value<0?'left':'right'}});
     bar.labelLayout={hideOverlap:true};
    }
    if(kind==='diverging_bar')bar.markLine={silent:true,symbol:'none',label:{show:false},
      data:[{xAxis:0}],lineStyle:{color:p.muted,width:1,type:'solid'}};
   }
   if(kind==='lollipop'){
    const focused=Number.isInteger(d.focusIndex)&&d.focusIndex>=0&&d.focusIndex<d.values.length;
    if(focused)bar.itemStyle.color=v=>v.dataIndex===d.focusIndex?p.primary:p.muted;
    const sizeOnDates=Math.max(2,Math.min(6,(size.width-70)/d.labels.length));
    o.series.push({name:d.name||'',type:'scatter',data:d.values.map((v,i)=>({
     value:denseDates?[i,v]:[v,i],itemStyle:{color:focused&&i!==d.focusIndex?p.muted:p.primary}})),
     symbolSize:denseDates?(v=>focused&&v[0]===d.focusIndex?Math.max(5,sizeOnDates):sizeOnDates):(focused?v=>v[1]===d.focusIndex?10:7:8),
     itemStyle:{color:p.primary},label:{show:!denseDates,position:'top',distance:4,fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value[0])+labelUnit}});
   }
  }else if(['grouped_bar','stacked_bar'].includes(kind)){
   o={grid:{...base.grid,top:exponent?52:base.grid.top},legend:legend(d.series.map(s=>s.name)),xAxis:denseDates?dateCategory():category(d.labels),yAxis:value(kind==='stacked_bar'?{min:0}:{}),series:d.series.map((s,i)=>({name:s.name,type:'bar',data:s.values,stack:kind==='stacked_bar'?'total':undefined,barMaxWidth:24,labelLayout:{hideOverlap:true},itemStyle:{color:color(i),borderColor:p.ink,borderWidth:denseDates?0:.3},label:{show:d.labels.length<=6&&kind!=='stacked_bar',position:'top',fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value)+labelUnit}}))};
   if(kind==='grouped_bar'&&d.unitOnAxis&&plotUnit){
    o.grid.top=Math.max(o.grid.top,2*font+24);
    o.series.forEach(series=>{
     series.barGap='85%';
     series.label.formatter=point=>labelNumber(point.value);
    });
   }
   if(d.categoricalComparison){
    const width=Math.max(3,Math.floor((size.width-54)/d.labels.length/font));
    o.grid={...o.grid,top:34,bottom:d.labels.some(label=>label.length>width)?32:22};
    o.xAxis.axisLabel={...o.xAxis.axisLabel,interval:0,hideOverlap:false,
     showMaxLabel:true,formatter:label=>label.length>width?label.slice(0,width)+'\n'+label.slice(width):label};
    o.series.forEach((series,i)=>{
     series.data=d.series[i].values.map(value=>value==null?null:{value,label:{position:value<0?'bottom':'top'}});
    });
   }
  }else if(kind==='heatmap'){
   const cells=d.matrix.flatMap((row,y)=>row.flatMap((v,x)=>v===null?[]:[[x,y,v]]));const all=cells.map(c=>c[2]);assert(all.length>0,'矩阵没有实际数值');
   const min=Math.min(...all),max=Math.max(...all),signed=min<0&&max>0,bound=Math.max(Math.abs(min),max);
   const bottom=signed?-bound:min,top=signed?bound:max===min?max+1:max,ramp=signed&&(themeID==='research'||p.diverging)?[p.negative,p.paper,p.positive]:p.ramp;
   const labeled=cells.map(value=>({value,label:{color:readableLabel(gradientColor(ramp,(value[2]-bottom)/(top-bottom)))}}));
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>displayLabel(d.rows[v.value[1]])+'\n'+displayLabel(d.labels[v.value[0]])+'\n'+number(v.value[2])+(d.unit||'')},grid:{left:16,right:26,top:20,bottom:64,containLabel:true},xAxis:category(d.labels,{splitArea:{show:false}}),yAxis:category(d.rows,{inverse:true}),visualMap:{min:signed?-bound:min,max:signed?bound:max===min?max+1:max,show:min!==max,orient:'horizontal',left:'center',bottom:4,calculable:false,textStyle:{fontSize:font,color:p.ink},inRange:{color:ramp}},series:[{type:'heatmap',data:labeled,label:{show:d.labels.length<=12&&(size.width-140)/d.labels.length>=21,fontSize:11,color:p.ink,formatter:v=>number(v.value[2])},itemStyle:{borderWidth:3,borderColor:p.paper},emphasis:{itemStyle:{borderColor:p.ink,borderWidth:2}}}]};
  }else if(kind==='scatter'||kind==='scatter_rug'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>text(displayLabel(d.points[v.dataIndex].name))+'\n'+(d.xName||'X')+' '+number(v.value[0])+'\n'+(d.yName||'Y')+' '+number(v.value[1])},grid:{...base.grid,top:54,bottom:38},xAxis:value({name:d.xName||'',nameLocation:'middle',nameGap:25,scale:true}),yAxis:value({name:(d.yName||'')+(d.comparison_periods&&plotUnit?'（'+plotUnit+'）':''),nameTextStyle:{fontSize:11,color:p.muted,align:'left'},scale:true}),series:[{type:'scatter',data:d.points.map((row,index)=>d.comparison_periods?{value:[row.x,row.y],name:row.name,itemStyle:{color:color(index)},label:{position:index?'right':'left'}}:[row.x,row.y]),symbolSize:10,label:d.comparison_periods?{show:true,fontSize:11,distance:6,formatter:v=>displayLabel(d.points[v.dataIndex].name)}:undefined,labelLayout:{hideOverlap:true},itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:1.5}}]};
   if(kind==='scatter_rug'){
    o.xAxis.boundaryGap=o.yAxis.boundaryGap=[.13,.13];
    o.series.push(marginalMarks(d,p));
   }
  }else if(kind==='range'){
   o={xAxis:value({scale:true}),yAxis:category(d.ranges.map(row=>row.name),{inverse:true}),series:[{type:'custom',data:d.ranges.map((row,i)=>[i,row.min,row.max]),encode:{x:[1,2],y:0},renderItem:(_params,api)=>{const a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)]);return {type:'group',children:[{type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:p.primary,lineWidth:5}},{type:'circle',shape:{cx:a[0],cy:a[1],r:4},style:{fill:p.paper,stroke:p.primary,lineWidth:2}},{type:'circle',shape:{cx:b[0],cy:b[1],r:4},style:{fill:p.paper,stroke:p.primary,lineWidth:2}}]}}},
    {name:'观测值',type:'scatter',data:d.ranges.flatMap((row,i)=>row.value==null?[]:[[row.value,i]]),symbolSize:12,itemStyle:{color:p.accent,borderColor:p.paper,borderWidth:2},label:{show:true,position:'top',fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value[0])+labelUnit}}]};
   o.tooltip={trigger:'item',renderMode:'richText',formatter:v=>{const i=v.seriesIndex===0?v.value[0]:v.value[1],r=d.ranges[i];return r.name+'\n区间 '+number(r.min)+'~'+number(r.max)+(d.unit||'')+(r.value==null?'':'\n观测值 '+number(r.value)+(d.unit||''))}};
  }else if(kind==='boxplot'){
   o={xAxis:category(d.labels),yAxis:value({scale:true}),series:[{type:'boxplot',data:d.values,itemStyle:{color:p.paper,borderColor:p.primary,borderWidth:2}}]};
  }else if(kind==='candlestick'){
   const fields=['开盘','收盘','最低','最高'];
   const tooltip=params=>{
    const point=Array.isArray(params)?params[0]:params;
    const prices=d.values[point?.dataIndex];
    return prices?[displayLabel(d.labels[point.dataIndex]),...fields.map((label,index)=>label+' '+number(prices[index])+(d.unit||''))].join('\n'):'';
   };
   o={xAxis:category(d.labels),yAxis:value({scale:true}),tooltip:{...base.tooltip,formatter:tooltip},
    series:[{type:'candlestick',data:d.values,barMaxWidth:d.labels.length===1?24:undefined,
     itemStyle:{color:p.paper,color0:p.negative,borderColor:p.positive,borderColor0:p.negative,borderWidth:1.6}}]};
  }else if(kind==='donut'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>v.name+' '+number(v.value)+(d.unit||'')},legend:{...legend(d.labels),bottom:0,top:undefined},series:[{type:'pie',radius:['49%','72%'],center:['50%','44%'],avoidLabelOverlap:true,label:{show:false},itemStyle:{borderColor:p.paper,borderWidth:4},data:d.labels.map((name,i)=>({name,value:d.values[i],itemStyle:{color:color(i)}}))}]};
  }else if(kind==='treemap'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>v.name+' '+number(v.value)+(d.unit||'')},series:[{type:'treemap',data:d.tree,roam:false,nodeClick:false,breadcrumb:{show:false},label:{show:true,fontSize:font,color:p.ink,formatter:'{b}'},upperLabel:{show:true,height:24,color:p.ink},color:colors,itemStyle:{borderColor:p.paper,gapWidth:3},levels:[{itemStyle:{borderWidth:0}},{itemStyle:{borderWidth:2},label:{color:p.paper}}]}]};
  }else if(kind==='waterfall'){
   let level=d.start;
   const nodes=[{name:d.startName||'起点',from:0,to:d.start,value:d.start,total:true}];
   for(const step of d.steps){
    nodes.push({name:step.name,from:level,to:level+step.value,value:step.value});
    level+=step.value;
   }
   nodes.push({name:d.endName||'终点',from:0,to:d.end,value:d.end,total:true});
   const levels=nodes.flatMap(row=>[row.from,row.to]),low=Math.min(...levels),high=Math.max(...levels);
   const domain=readableBounds(low,high,4,.11);
   if(low>=0)domain.min=0;
   const reading=(row,showUnit)=>(!row.total&&row.value>0?'+':'')+labelNumber(row.value)
    +(showUnit?(row.total?labelUnit:d.changeUnit||labelUnit):'');
   const horizontal=category(nodes.map(row=>row.name));
   const wrapLabels=d.unitOnAxis&&size.width>0&&size.width<480;
   horizontal.axisLabel={...horizontal.axisLabel,interval:0,margin:9};
   if(wrapLabels){
    Object.assign(horizontal.axisLabel,{width:Math.max(20,Math.floor((size.width-60)/nodes.length)),
     overflow:'break',lineHeight:13,hideOverlap:false});
   }
   const vertical=value(d.unitOnAxis?domain:{...domain,name:''});
   if(d.unit==='%')vertical.axisLabel={...vertical.axisLabel,formatter:v=>axisNumber(v)+'%'};
   o={grid:{...base.grid,left:12,right:12,top:d.unitOnAxis?31:18,bottom:wrapLabels?36:24},xAxis:horizontal,
    yAxis:vertical,
    tooltip:{trigger:'item',renderMode:'richText',formatter:point=>{
     const row=nodes[point.dataIndex];return row.name+'\n'+reading(row,true);
    }},
    series:[{type:'custom',clip:false,data:nodes.map((row,index)=>[index,row.from,row.to]),encode:{x:0,y:[1,2]},
     renderItem:(params,api)=>{
      const row=nodes[params.dataIndex],from=api.coord([params.dataIndex,row.from]),to=api.coord([params.dataIndex,row.to]);
      const width=Math.min(62,Math.abs(api.size([1,0])[0])*.5),top=Math.min(from[1],to[1]);
      const fill=row.total?p.ink:row.value>=0?p.positive:p.negative;
      const items=[{type:'rect',shape:{x:to[0]-width/2,y:top,width,height:Math.max(1,Math.abs(from[1]-to[1]))},style:{fill}}];
      if(params.dataIndex+1<nodes.length){
       const next=api.coord([params.dataIndex+1,row.to]);
       items.push({type:'line',shape:{x1:to[0]+width/2,y1:to[1],x2:next[0]-width/2,y2:to[1]},
        style:{stroke:p.muted,lineWidth:1}});
      }
      items.push({type:'text',style:{x:to[0],y:top-7,text:reading(row,!d.unitOnAxis),align:'center',verticalAlign:'bottom',
       font:(row.total?'600 ':'500 ')+font+'px Arial, PingFang SC, sans-serif',fill:p.ink}});
      return {type:'group',children:items};
     }}]};
  }else if(kind==='evidence'&&d.qualitative){
   const count=d.nodes.length,columns=count<=3?count:Math.ceil(count/2),rows=Math.ceil(count/columns);
   const width=size.width||800,height=size.height||240;
   const box=Math.min(122,Math.max(56,(width-32-20*(columns-1))/columns));
   const characters=Math.max(3,Math.floor((box-16)/11));
   const wrap=name=>{const letters=Array.from(String(name)),lines=[];
    for(let i=0;i<letters.length;i+=characters)lines.push(letters.slice(i,i+characters).join(''));
    return lines.join('\n');
   };
   const lines=Math.max(...d.nodes.map(node=>Math.ceil(Array.from(node.name).length/characters)));
   const nodeHeight=Math.max(54,lines*15+16),insetX=box/2+8,insetY=nodeHeight/2+8;
   const positions=d.nodes.map((node,index)=>{const row=Math.floor(index/columns),column=index%columns;
    return {x:columns===1?width/2:insetX+(row%2?columns-1-column:column)*(width-2*insetX)/(columns-1),
     y:rows===1?height/2:insetY+row*(height-2*insetY)/(rows-1)};
   });
   const byID=new Map(d.nodes.map((node,index)=>[node.id,positions[index]]));
   // Layout indices are canvas positions, never economic values or weights.
   // Native custom series also give rectangular nodes exact edge contacts.
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:point=>point.data.name},series:[{
    id:'rh-mechanism-links',type:'custom',coordinateSystem:'none',silent:true,z:1,
    dimensions:['layout_index'],data:d.links.map((link,index)=>({...link,value:index})),
    renderItem:params=>{const edge=d.links[params.dataIndex],a=byID.get(edge.source),b=byID.get(edge.target);
     const dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);
     if(!length)return {type:'group',children:[]};
     const ux=dx/length,uy=dy/length;
     const boundary=Math.min(ux?box/2/Math.abs(ux):Infinity,uy?nodeHeight/2/Math.abs(uy):Infinity)+3;
     const x1=a.x+ux*boundary,y1=a.y+uy*boundary,x2=b.x-ux*boundary,y2=b.y-uy*boundary;
     const baseX=x2-ux*7,baseY=y2-uy*7;
     return {type:'group',children:[
      {type:'line',shape:{x1,y1,x2:baseX,y2:baseY},style:{stroke:p.muted,lineWidth:1.2}},
      {type:'polygon',shape:{points:[[x2,y2],[baseX-uy*3,baseY+ux*3],[baseX+uy*3,baseY-ux*3]]},style:{fill:p.muted}}
     ]};
    }
   },{
    id:'rh-mechanism-nodes',type:'custom',coordinateSystem:'none',z:2,
    dimensions:['layout_index'],data:d.nodes.map((node,index)=>({name:node.name,value:index})),
    renderItem:params=>{const node=d.nodes[params.dataIndex],position=positions[params.dataIndex];
     return {type:'group',children:[
      {type:'rect',shape:{x:position.x-box/2,y:position.y-nodeHeight/2,width:box,height:nodeHeight,r:12},
       style:{fill:p.paper,stroke:node.kind==='condition'?p.primary:p.accent,lineWidth:1.2}},
      {type:'text',style:{x:position.x,y:position.y,text:wrap(node.name),font:'11px Arial, PingFang SC, sans-serif',
       lineHeight:15,align:'center',verticalAlign:'middle',fill:p.ink}}
     ]};
    }
   }]};
  }else if(kind==='evidence'){
   const levels=new Map();d.nodes.forEach(node=>{const level=node.level??0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node.id)});
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>v.data.name||'引用关系'},series:[{type:'graph',layout:'none',roam:false,edgeSymbol:['none','arrow'],edgeSymbolSize:7,label:{show:true,color:p.ink,fontSize:font,position:'bottom'},data:d.nodes.map((node,i)=>{const group=levels.get(node.level??0);return {id:node.id,name:node.name,x:(node.level??0)*180,y:group.indexOf(node.id)*90,symbolSize:28,itemStyle:{color:color(node.level??0),borderColor:p.paper,borderWidth:2}}}),links:d.links,lineStyle:{color:p.muted,width:1.5,curveness:.08}}]};
  }
  const result={...base,...o};
  if(d.dateLabels&&Object.values(d.dateLabels).some(value=>typeof value==='string')&&!result.tooltip.formatter&&['line','area','multi_line','grouped_bar','slope','lollipop','ranked_bar'].includes(kind)){
   // Format tooltip text, retaining raw category/series identities used by
   // ECharts, source navigation and report-style conversions.
   result.tooltip={...result.tooltip,formatter:params=>{
    const rows=Array.isArray(params)?params:[params],first=rows[0]||{};
    const title=displayLabel(first.axisValueLabel||first.name||'');
    const values=rows.map(row=>{
     const value=Array.isArray(row.value)&&['lollipop','ranked_bar'].includes(kind)?row.value[0]:row.value;
     const name=displayLabel(row.seriesName||'');
     return (name?name+'：':'')+(Array.isArray(value)?value.map(number).join('，'):number(value))+(d.unit||'');
    });
    return [title,...values].filter(value=>value!==''&&value!=null).join('\n');
   }};
  }
  return result;
 }
 function adapt(spec){
  const selected=spec.auto_visual||(spec.scheme&&spec.data?spec:null);
  if(selected){
   if(selected.scheme==='dumbbell')return {scheme:'grouped_bar',data:materialize(selected,'grouped_bar')};
   return {scheme:selected.scheme,data:selected.data};
  }
  if(spec.kind==='changes')return {scheme:'diverging_bar',data:{labels:spec.dates,values:spec.values,unit:spec.unit,name:spec.name}};
  if(spec.kind==='paired')return {scheme:'grouped_bar',data:{labels:spec.dates,pairs:spec.pairs,periods:spec.periods,
    series:spec.periods.map((name,index)=>({name,values:spec.pairs.map(pair=>pair[index])})),unit:spec.unit,name:spec.name}};
  return {scheme:spec.horizontal?'ranked_bar':spec.type==='line'?'line':'grouped_bar',data:{labels:spec.dates,values:spec.values,
    series:[{name:spec.name,values:spec.values}],unit:spec.unit,name:spec.name}};
 }
 function orderedChartPeriods(labels){
  if(!Array.isArray(labels)||labels.length<2||typeof labels[0]!=='string')return false;
  const precision=labels[0].length;
  if(!labels.every(label=>typeof label==='string'&&label.length===precision))return false;
  return orderedDateCategories(labels,label=>/^\d{4}$/.test(label)?label+'-01-01':label);
 }
 function compatible(item){
  const kind=item.scheme;
  if(kind==='small_multiples')return ['small_multiples','multi_line'];
  if(item.data.categoricalComparison)return [kind];
  if(['sample_range','beeswarm'].includes(kind)&&ecdfReady(item.data))return [kind,'ecdf'];
  if(kind==='band'&&intervalDotsReady(item.data))return [kind,'interval_dot'];
  if(kind==='scatter'&&catalogue.version>=5&&scatterRugReady(item.data))return [kind,'scatter_rug'];
  if(kind==='scenario_matrix'&&scenarioProfileReady(item.data))return [kind,'scenario_profile'];
  if(['line','area'].includes(kind))return item.data.measure==='scale'&&item.data.values.every(v=>v===null||v>=0)?['line','area','grouped_bar']:['line','grouped_bar'];
  if(['ranked_bar','lollipop','diverging_bar'].includes(kind)){
   return kind!=='lollipop'&&orderedChartPeriods(item.data.labels)?['ranked_bar','diverging_bar']:['ranked_bar','lollipop','diverging_bar'];
  }
  if(kind==='grouped_bar'&&item.data.pairs)return ['grouped_bar','slope'];
  const archivedCatalogue=catalogue.version<4;
  const labels=item.data.labels;
  if(kind==='heatmap'){
   if(archivedCatalogue)return labels.every(label=>/^\d{4}-\d{2}/.test(label))?['heatmap','multi_line','grouped_bar']:['heatmap'];
   return ['heatmap',...(orderedChartPeriods(labels)?['multi_line']:[]),'grouped_bar'];
  }
  if(item.data.series?.length>1){
   if(kind==='multi_line')return ['multi_line','grouped_bar','heatmap'];
   if(kind==='grouped_bar')return archivedCatalogue||orderedChartPeriods(labels)?['grouped_bar','multi_line','heatmap']:['grouped_bar','heatmap'];
  }
  return [kind];
 }
 function materialize(item,scheme){
  const d={...item.data};
  if(scheme==='grouped_bar'&&!d.series&&d.pairs)d.series=d.periods.map((name,index)=>({name,values:d.pairs.map(pair=>pair[index])}));
  if(['multi_line','grouped_bar'].includes(scheme)&&!d.series&&d.matrix){d.series=d.rows.map((name,index)=>({name,values:d.matrix[index]}));}
  if(scheme==='grouped_bar'&&!d.series)d.series=[{name:d.name||'',values:d.values}];
  if(scheme==='heatmap'&&d.series){d.rows=d.series.map(s=>s.name);d.matrix=d.series.map(s=>s.values);}
  return d;
 }
 let reportBindings=[],currentTheme='research',overrides={},reportObserver=null,resizeFrame=null;
 function observationRefs(spec, point){
  const scheme=overrides[spec.target]||adapt(spec).scheme;
  const all=spec.data_refs||[];
  const {seriesIndex,dataIndex,value}=point;
  let refs;
  if(['ecdf','interval_dot','scenario_profile','beeswarm'].includes(scheme)){
   // An aggregate or reordered mark opens the complete bound observations.
   return all;
  }
  if(scheme==='slope'){
   // The series is the metric pair. Its points are the two periods.
   refs=spec.point_refs?.[seriesIndex];
  }else if(['heatmap','scenario_matrix','bubble_matrix'].includes(scheme)){
   // Empty cells are removed from the renderer, so the mark index can shift.
   const [column,row]=Array.isArray(value)?value:[];
   if(Number.isInteger(row)&&Number.isInteger(column))refs=spec.series_point_refs?.[row]?.[column];
  }else{
   refs=spec.series_point_refs?.[seriesIndex]?.[dataIndex]??spec.point_refs?.[dataIndex];
  }
  return Array.isArray(refs)&&refs.every(ref=>all.includes(ref))?refs:all;
 }
 function applyProfile(profile={}){
  theme(profile.theme||currentTheme);currentTheme=profile.theme||currentTheme;
  for(const [target,scheme] of Object.entries(profile.schemes||{})){
   const binding=reportBindings.find(row=>row.spec.target===target);assert(binding&&compatible(binding.item).includes(scheme),'当前数据不适用这套图形');overrides[target]=scheme;
  }
  const p=theme(currentTheme),root=document.documentElement;
  for(const [name,value] of Object.entries({'--INK':p.ink,'--TEXT_SECONDARY':p.muted,'--GRID_LINE':p.grid,'--PAPER':p.paper,'--PANEL':p.paper,'--NOTE_BG':p.paper,'--FORMULA_BG':p.paper,'--SERIES_MAIN':currentTheme==='research'||p.diverging?p.positive:p.ink,'--SERIES_BENCH':p.primary}))root.style.setProperty(name,value);
  document.body.style.background=p.canvas||'#F2F4F6';
  for(const row of reportBindings){
   const scheme=overrides[row.spec.target]||row.item.scheme;
   try{
    const chart=row.chart;
    chart.setOption(option(scheme,materialize(row.item,scheme),currentTheme,{width:chart.getWidth(),height:chart.getHeight(),chart}),true);
    row.error=null;
   }catch(error){row.error=error.message;}
  }
  return {theme:currentTheme,schemes:{...overrides}};
 }
 function bindReports(specs,charts,profile={}){
  reportObserver?.disconnect();if(resizeFrame!==null)cancelAnimationFrame(resizeFrame);
  reportBindings=[];overrides={};currentTheme='research';
  specs.forEach((spec,index)=>{try{
   const item=adapt(spec);option(item.scheme,item.data);assert(charts[index],'图形未初始化');
   reportBindings.push({spec,chart:charts[index],item});
  }catch(error){/* A legacy or unsupported figure keeps its original local fallback. */}});
  const safe={theme:themes.has(profile.theme)?profile.theme:'research',schemes:{}};
  for(const [target,scheme] of Object.entries(profile.schemes||{})){
   const binding=reportBindings.find(row=>row.spec.target===target);
   if(binding&&compatible(binding.item).includes(scheme))safe.schemes[target]=scheme;
  }
  if(typeof ResizeObserver!=='undefined'){
   reportObserver=new ResizeObserver(()=>{if(resizeFrame!==null)cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;for(const row of reportBindings)if(!row.chart.isDisposed())row.chart.resize();applyProfile()})});
   reportBindings.forEach(row=>reportObserver.observe(row.chart.getDom()));
  }
  return applyProfile(safe);
 }
 function contexts(){return reportBindings.filter(row=>!row.error).map(row=>({target:row.spec.target,title:row.spec.name,scheme:overrides[row.spec.target]||row.item.scheme,data:row.item.data,compatible:compatible(row.item)}))}
 function styleExport(html,profile={theme:currentTheme,schemes:overrides}){
  const doc=new DOMParser().parseFromString(html,'text/html'),node=doc.getElementById('rh-visual-profile');
  assert(node,'报告使用原版呈现，未改写');node.textContent=JSON.stringify(profile).replaceAll('<','\\u003c');
  return '<!doctype html>\n'+doc.documentElement.outerHTML;
 }
 addEventListener('resize',()=>{if(reportBindings.length)requestAnimationFrame(()=>applyProfile())});
 let printProfile=null;
 addEventListener('beforeprint',()=>{if(theme(currentTheme).dark){printProfile={theme:currentTheme,schemes:{...overrides}};applyProfile({theme:'research'})}});
 addEventListener('afterprint',()=>{if(printProfile){const profile=printProfile;printProfile=null;applyProfile(profile)}});
 global.RHVisuals={catalogue,theme,axisFrame,validate:checked,option,number,adapt,compatible,materialize,bindReports,applyProfile,contexts,styleExport,observationRefs,
   profile:()=>({theme:currentTheme,schemes:{...overrides}})};
 addEventListener('message',event=>{
  if(event.source!==parent||event.data?.type!=='rh:visual-apply')return;
  try{applyProfile(event.data.profile);parent.postMessage({type:'rh:visual-applied',profile:global.RHVisuals.profile()},'*')}catch(error){parent.postMessage({type:'rh:visual-error',message:error.message},'*')}
 });
})(window);
