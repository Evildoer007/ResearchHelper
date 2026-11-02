/* Shared offline ECharts grammar. Data and financial calculations stay frozen. */
(function(global){
 'use strict';
 const catalogue=global.RH_VISUAL_CATALOG;
 if(!catalogue)throw Error('可视化目录未载入');
 const finite=v=>typeof v==='number'&&Number.isFinite(v),numeric=v=>v===null||finite(v);
 const assert=(ok,message)=>{if(!ok)throw Error(message)};
 const themes=new Map(catalogue.themes.map(row=>[row.id,row]));
 const number=v=>v==null?'未披露':v!==0&&Math.abs(v)<.0001?Number(v).toPrecision(3):new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(v);
 const text=value=>String(value??'');
 function theme(id='research'){assert(themes.has(id),'配色未登记');return themes.get(id)}
 function readableLabel(hex){
  const rgb=hex.replace('#','').match(/.{2}/g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  const light=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];return light>.179?'#000000':'#FFFFFF';
 }
 function gradientColor(colors,ratio){
  const position=Math.max(0,Math.min(1,ratio))*(colors.length-1),index=Math.min(colors.length-2,Math.floor(position)),part=position-index;
  const rgb=colors.slice(index,index+2).map(c=>c.replace('#','').match(/.{2}/g).map(v=>parseInt(v,16)));
  return '#'+rgb[0].map((v,i)=>Math.round(v+(rgb[1][i]-v)*part).toString(16).padStart(2,'0')).join('');
 }
 function lineExtrema(values,troughBelow=true){
  const observed=values.filter(finite),last=values.findLastIndex(finite);
  const points=[{name:'最低',value:Math.min(...observed)},{name:'最高',value:Math.max(...observed)}];
  return points.filter((row,i)=>i===0||row.value!==points[0].value)
   .map(row=>{
    const index=values.indexOf(row.value),towardRight=index<(values.length-1)/2;
    // Anchor edge labels into the plot instead of across its numeric axis.
    return {...row,coord:[index,row.value],label:{position:troughBelow&&row.name==='最低'?'bottom':'top',align:towardRight?'left':'right',offset:[towardRight?6:-6,0]}};
   }).filter(row=>row.coord[0]!==last);
 }
 function dimensions(d){assert(Array.isArray(d.labels)&&d.labels.length>0,'需要明确类目或日期');return d.labels.length}
 function values(array,n){assert(Array.isArray(array)&&array.length===n&&array.every(numeric),'数据长度不一致或数值无效');return array}
 function checked(kind,d){
  assert(catalogue.schemes.some(row=>row.id===kind),'图表方案未登记');
  if(extraKinds.has(kind))return validateExtra(kind,d);
  if(['line','area','ranked_bar','lollipop','diverging_bar'].includes(kind)){
   values(d.values,dimensions(d));if(kind==='area')assert(d.measure==='scale'&&d.values.every(v=>v===null||v>=0),'面积图需要明确的非负规模');
  }else if(['multi_line','grouped_bar','stacked_bar'].includes(kind)){
   const n=dimensions(d);assert(Array.isArray(d.series)&&d.series.length>0,'需要实际序列');
   for(const s of d.series)values(s.values,n);
   if(kind==='stacked_bar')assert(d.additive===true&&d.series.every(s=>s.values.every(v=>v===null||v>=0)),'构成图需要可加总非负分项');
  }else if(kind==='dumbbell'){
   const n=dimensions(d);assert(Array.isArray(d.pairs)&&d.pairs.length===n&&d.pairs.every(row=>row.length===2&&row.every(finite)),'需要同对象两期数值');
   assert(Array.isArray(d.periods)&&d.periods.length===2,'需要两期名称');
  }else if(kind==='heatmap'){
   const n=dimensions(d);assert(Array.isArray(d.rows)&&Array.isArray(d.matrix)&&d.rows.length===d.matrix.length,'矩阵标签未对齐');
   d.matrix.forEach(row=>values(row,n));
  }else if(kind==='scatter'){
   assert(Array.isArray(d.points)&&d.points.length>0&&d.points.every(p=>finite(p.x)&&finite(p.y)),'散点需要同对象的两维数值');
  }else if(kind==='range'){
   assert(Array.isArray(d.ranges)&&d.ranges.length>0&&d.ranges.every(p=>finite(p.min)&&finite(p.max)&&p.min<=p.max&&(p.value==null||finite(p.value)&&p.value>=p.min&&p.value<=p.max)),'区间端点或观测值无效');
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

 const extraKinds=new Set(['band','fan','indexed_line','drawdown','rolling','return_bars','excess_return','valuation_band','percentile','bullet','benchmark_dot','slope','butterfly','bubble_matrix','mosaic','sunburst','sankey','histogram','beeswarm','ridge','scenario_matrix','event_timeline']);
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
   checked('dumbbell',d);
  }else if(kind==='butterfly'){
   const n=dimensions(d);values(d.left,n);values(d.right,n);assert([...d.left,...d.right].every(v=>finite(v)&&v>=0),'双向数量必须非负');
  }else if(['bubble_matrix','mosaic','scenario_matrix'].includes(kind)){
   checked('heatmap',d);
   if(kind!=='scenario_matrix')assert(d.matrix.every(row=>row.every(v=>v===null&&kind==='bubble_matrix'||finite(v)&&v>=0)),'数量矩阵需要非负数值');
   if(kind==='mosaic')assert(d.matrix.flat().some(v=>v>0)&&d.partWhole===true,'联合构成需要明确总体');
   if(kind==='scenario_matrix')assert(d.scenario===true&&typeof d.method==='string'&&d.method.length>0,'需要明确假设和已算情景结果');
  }else if(kind==='sunburst')checked('treemap',d);
  else if(kind==='sankey'){
   assert(Array.isArray(d.nodes)&&Array.isArray(d.links),'流向需要节点与流量');const ids=new Set(d.nodes.map(n=>n.name));
   assert(ids.size===d.nodes.length&&d.links.every(l=>ids.has(l.source)&&ids.has(l.target)&&finite(l.value)&&l.value>=0),'流向或流量无效');
  }else if(kind==='histogram'){
   assert(Array.isArray(d.edges)&&Array.isArray(d.counts)&&d.edges.length===d.counts.length+1&&d.edges.every(finite)&&d.edges.every((v,i)=>!i||v>d.edges[i-1])&&d.counts.every(v=>Number.isInteger(v)&&v>=0),'分箱及频数无效');
   const width=d.edges[1]-d.edges[0];assert(d.edges.every((v,i)=>!i||Math.abs(v-d.edges[i-1]-width)<1e-8*Math.max(1,width)),'非等宽分箱需先提供登记方法计算的密度');
  }else if(kind==='beeswarm')assert(Array.isArray(d.samples)&&d.samples.length>1&&d.samples.every(finite),'需要逐条原始观测');
  else if(kind==='ridge'){
   assert(Array.isArray(d.curves)&&d.curves.length>1&&typeof d.method==='string'&&d.method.length>0,'需要已计算密度及方法');
   for(const curve of d.curves)assert(Array.isArray(curve.x)&&Array.isArray(curve.y)&&curve.x.length===curve.y.length&&curve.x.length>2&&curve.x.every(finite)&&curve.y.every(v=>finite(v)&&v>=0),'密度网格无效');
  }else if(kind==='event_timeline')assert(Array.isArray(d.events)&&d.events.length>0&&d.events.every(e=>typeof e.date==='string'&&Number.isFinite(Date.parse(e.date))&&typeof e.name==='string'&&['已发生','已公告','待观察'].includes(e.state)),'事件日期与状态需明确');
  return d;
 }
 function additionalOption(kind,d,p,h){
  const {category,value,legend,color,size}=h,baseLine=(name,data,i=0)=>({name,type:'line',data,smooth:false,connectNulls:false,symbolSize:5,lineStyle:{width:2.6,color:color(i)},itemStyle:{color:color(i)}});
  if(['indexed_line','rolling','excess_return','drawdown'].includes(kind)){
   const series=(d.series||[{name:d.name||d.window||d.benchmark||'实际读数',values:d.values}]).map((row,i)=>baseLine(row.name,row.values,i));
   if(kind==='drawdown')series[0].areaStyle={color:p.ramp[1],opacity:.9};
   series[0].markLine={silent:true,symbol:'none',data:[{yAxis:kind==='indexed_line'?100:0}],label:{show:kind==='indexed_line',formatter:'基期100',fontSize:13,color:p.ink},lineStyle:{color:p.muted,type:'dashed'}};
   return {legend:series.length>1?legend(series.map(s=>s.name)):undefined,xAxis:category(d.labels),yAxis:value(kind==='drawdown'?{max:0}:{scale:true}),series};
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
  if(kind==='benchmark_dot')return {xAxis:value({scale:true}),yAxis:category(d.labels,{inverse:true}),series:[{type:'scatter',data:d.values.map((v,i)=>[v,i]),symbolSize:9,itemStyle:{color:p.primary},markLine:{silent:true,symbol:'none',data:[{xAxis:d.reference}],label:{show:true,formatter:d.referenceName,fontSize:13,color:p.ink},lineStyle:{color:p.accent,type:'dashed'}}}]};
  if(kind==='slope')return {legend:legend(d.labels),xAxis:category(d.periods,{boundaryGap:true}),yAxis:value({scale:true}),series:d.pairs.map((pair,i)=>baseLine(d.labels[i],pair,i))};
  if(kind==='butterfly')return {legend:legend([d.leftName||'左组',d.rightName||'右组']),xAxis:value({axisLabel:{fontSize:13,color:p.ink,formatter:v=>number(Math.abs(v))}}),yAxis:category(d.labels,{inverse:true}),series:[{name:d.leftName||'左组',type:'bar',stack:'pair',data:d.left.map(v=>-v),barMaxWidth:18,itemStyle:{color:p.primary}},{name:d.rightName||'右组',type:'bar',stack:'pair',data:d.right,barMaxWidth:18,itemStyle:{color:p.accent}}]};
  if(kind==='bubble_matrix'){
   const points=d.matrix.flatMap((row,y)=>row.flatMap((v,x)=>v===null?[]:[[x,y,v]])),max=Math.max(...points.map(row=>row[2]),1);
   return {xAxis:category(d.labels,{splitLine:{show:true,lineStyle:{color:p.grid}}}),yAxis:category(d.rows,{inverse:true,splitLine:{show:true,lineStyle:{color:p.grid}}}),series:[{type:'scatter',data:points,symbolSize:v=>Math.sqrt(v[2]/max)*34,itemStyle:{color:p.primary}}]};
  }
  if(kind==='scenario_matrix')return option('heatmap',{...d},p.id,size);
  if(kind==='mosaic'){
   const totals=d.labels.map((_,x)=>d.matrix.reduce((sum,row)=>sum+row[x],0)),total=totals.reduce((a,b)=>a+b,0);let start=0;const rects=[];
   totals.forEach((amount,x)=>{let bottom=0;d.rows.forEach((name,y)=>{const height=amount?d.matrix[y][x]/amount*100:0;rects.push([start,start+amount/total*100,bottom,bottom+height,y,x]);bottom+=height});start+=amount/total*100});
   return {xAxis:value({name:'总体份额%',min:0,max:100}),yAxis:value({name:'组内份额%',min:0,max:100}),series:[{type:'custom',data:rects,encode:{x:[0,1],y:[2,3]},renderItem:(_params,api)=>{const a=api.coord([api.value(0),api.value(2)]),b=api.coord([api.value(1),api.value(3)]);return {type:'rect',shape:{x:a[0],y:b[1],width:b[0]-a[0],height:a[1]-b[1]},style:{fill:color(api.value(4)),stroke:p.paper,lineWidth:2}}}}]};
  }
  if(kind==='sunburst')return {tooltip:{trigger:'item',renderMode:'richText'},series:[{type:'sunburst',data:d.tree,radius:['10%','86%'],nodeClick:false,label:{fontSize:13,color:p.ink},itemStyle:{borderColor:p.paper,borderWidth:3},color:p.colors}]};
  if(kind==='sankey')return {tooltip:{trigger:'item',renderMode:'richText'},series:[{type:'sankey',data:d.nodes,links:d.links,left:16,right:90,top:24,bottom:24,label:{fontSize:13,color:p.ink},nodeWidth:14,nodeGap:20,lineStyle:{color:'source',opacity:.4},itemStyle:{borderWidth:0},emphasis:{focus:'adjacency'}}]};
  if(kind==='histogram')return {xAxis:category(d.counts.map((_,i)=>number(d.edges[i])+'~'+number(d.edges[i+1]))),yAxis:value({name:'频数',min:0}),series:[{type:'bar',data:d.counts,barGap:0,barCategoryGap:'2%',itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:1}}]};
  if(kind==='beeswarm'){
   const sorted=[...d.samples].sort((a,b)=>a-b),low=sorted[0],span=sorted.at(-1)-low||1,bins=new Map();const points=sorted.map(v=>{const bucket=Math.round((v-low)/span*30),i=bins.get(bucket)||0;bins.set(bucket,i+1);return [v,i?Math.ceil(i/2)*(i%2?1:-1):0]});
   return {xAxis:value({scale:true}),yAxis:{type:'value',show:false,min:-Math.max(1,...points.map(v=>Math.abs(v[1])))-1,max:Math.max(1,...points.map(v=>Math.abs(v[1])))+1},series:[{type:'scatter',data:points,symbolSize:7,itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:.5}}]};
  }
  if(kind==='ridge'){
   const max=Math.max(...d.curves.flatMap(c=>c.y),1e-10),xs=d.curves.flatMap(c=>c.x),series=d.curves.map((curve,i)=>({name:curve.name,type:'custom',data:[0],renderItem:(_params,api)=>({type:'polygon',shape:{points:[api.coord([curve.x[0],i]),...curve.x.map((x,k)=>api.coord([x,i+curve.y[k]/max*.8])),api.coord([curve.x.at(-1),i])]},style:{fill:color(i),stroke:p.paper,lineWidth:1,opacity:.9}})}));
   return {xAxis:value({min:Math.min(...xs),max:Math.max(...xs)}),yAxis:{type:'value',min:0,max:d.curves.length,interval:1,splitLine:{show:false},axisLabel:{fontSize:13,color:p.ink,formatter:v=>d.curves[v]?.name||''}},series};
  }
  if(kind==='event_timeline'){
   const points=d.events.map((event,i)=>({value:[Date.parse(event.date),1],name:event.name,symbol:event.state==='待观察'?'emptyCircle':'circle',symbolSize:10,itemStyle:{color:event.state==='已发生'?p.primary:p.accent},label:{show:true,formatter:event.name,position:i%2?'bottom':'top',distance:i%2?20:22,fontSize:13,color:p.ink}}));
   return {xAxis:{type:'time',axisLabel:{fontSize:13,color:p.ink,formatter:v=>new Date(v).toISOString().slice(5,10)},splitLine:{show:false}},yAxis:{type:'value',min:0,max:2,show:false},series:[{type:'line',data:points,symbolSize:10,lineStyle:{color:p.muted,width:1.5},labelLayout:{hideOverlap:true}}]};
  }
  throw Error('方案尚未实现');
 }

 function option(kind,data,themeID='research',size={width:800}){
  const d=checked(kind,data),p=theme(themeID),colors=p.colors,font=size.width<=430?11:12;
  const observed=[...(d.values||[]).flat(),...(d.pairs||[]).flat(),...(d.series||[]).flatMap(row=>row.values||[]),...(d.points||[]).flatMap(row=>[row.x,row.y])].filter(finite);
  const magnitude=observed.length?Math.max(...observed.map(Math.abs)):0;
  const exponent=d.unit&&!['%','倍','个百分点'].includes(d.unit)&&magnitude>=1e6?3*Math.floor(Math.log10(magnitude)/3):0;
  const divisor=10**exponent,sup=value=>String(value).split('').map(c=>'⁰¹²³⁴⁵⁶⁷⁸⁹'[Number(c)]).join('');
  const plotUnit=(d.unit||'')+(exponent?'×10'+sup(exponent):'');
  const labelNumber=v=>finite(v)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:Number.isInteger(d.numberDigits)?Math.max(0,Math.min(12,d.numberDigits)):2}).format(v/divisor):number(v);
  // Tick spacing belongs to the scale, not the precision of observations.
  // Whole-number observations can still produce half-unit ticks.
  const axisNumber=v=>finite(v)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:12}).format(v/divisor):number(v);
  const labelUnit=exponent||d.unit==='原值'?'':d.unit||'';
  const color=i=>d.comparison_periods?.length===2?(i===0?p.accent:p.primary):colors[i%Math.min(colors.length,6)];
  const category=(labels,extra={})=>({type:'category',data:labels,axisTick:{show:false},axisLine:{lineStyle:{color:p.grid}},axisLabel:{fontSize:font,color:p.ink,hideOverlap:true,formatter:d.compactDates?v=>/^\d{4}-\d{2}-\d{2}$/.test(String(v))?String(v).slice(5):v:undefined},...extra});
  const value=(extra={})=>({type:'value',name:plotUnit.length<=12?plotUnit:'',nameGap:8,axisLine:{show:false},axisTick:{show:false},axisLabel:{fontSize:font,color:p.ink,hideOverlap:true,formatter:axisNumber},nameTextStyle:{fontSize:font,color:p.muted},splitLine:{lineStyle:{color:p.grid}},...extra});
  const base={animation:false,color:colors,textStyle:{fontFamily:'Arial, PingFang SC, sans-serif',fontSize:14,color:p.ink},
   tooltip:{trigger:'axis',renderMode:'richText',valueFormatter:v=>Array.isArray(v)?v.map(number).join('，'):number(v)},grid:{left:14,right:26,top:34,bottom:22,containLabel:true}};
  const legend=names=>({data:names,top:0,left:8,selectedMode:false,itemWidth:12,itemHeight:10,textStyle:{fontSize:font,color:p.ink},itemGap:18});
  if(extraKinds.has(kind))return {...base,...additionalOption(kind,d,p,{category,value,legend,color,size})};
  let o={};
  if(['line','area','multi_line'].includes(kind)){
   const series=kind==='multi_line'?d.series:[{name:d.name||'',values:d.values}];
   // Compact print panels need a full label row below a trough.
   const labelPadding=Math.max(.18,Math.min(.6,30/Math.max(60,(size.height||240)-80)));
   o={legend:series.length>1?legend(series.map(s=>s.name)):undefined,xAxis:category(d.labels),yAxis:value({scale:kind!=='area',boundaryGap:kind==='area'?[0,.18]:[labelPadding,.18]}),series:series.map((s,i)=>{
    const last=s.values.findLastIndex(finite);
    const previous=s.values.findLastIndex((v,index)=>index<last&&finite(v));
    // The inward horizontal label used to sit on the approaching segment.
    // Put it on the opposite side of that segment, inside the plot edge.
    const descending=previous>=0&&s.values[last]<s.values[previous];
    return {name:s.name,type:'line',data:s.values,smooth:false,connectNulls:false,
     symbol:['circle','rect','diamond','triangle'][i%4],symbolSize:6,
     lineStyle:{width:2.8,color:color(i),type:i>2?'dashed':'solid'},
     label:{show:true,fontSize:font,color:p.ink,position:descending&&kind!=='area'?'bottom':'top',
      align:'right',distance:6,backgroundColor:p.paper,padding:[1,3],
      formatter:v=>v.dataIndex===last?labelNumber(v.value)+(d.unit||''):''},
     labelLayout:{hideOverlap:true},
     markPoint:series.length===1&&s.values.filter(finite).length>=4?{
      symbol:'circle',symbolSize:5,data:lineExtrema(s.values,kind!=='area'),
      label:{show:true,fontSize:font,color:p.ink,position:'top',formatter:v=>labelNumber(v.value)}}:undefined,
     itemStyle:{color:color(i),borderColor:p.ink,borderWidth:.35},
     areaStyle:kind==='area'?{color:p.ramp[1],opacity:1}:undefined};
   })};
  }else if(['ranked_bar','diverging_bar','lollipop'].includes(kind)){
   const bar={name:d.name||'',type:'bar',data:d.values,barMaxWidth:kind==='lollipop'?2:18,itemStyle:{color:kind==='diverging_bar'&&themeID==='research'?v=>v.value>=0?p.positive:p.negative:p.primary,borderRadius:[0,2,2,0]},label:kind==='lollipop'?{show:false}:{show:true,position:'right',color:p.ink,fontSize:font,formatter:v=>labelNumber(v.value)+labelUnit}};
   o={grid:{...base.grid,right:60},xAxis:value(),yAxis:category(d.labels,{inverse:true}),series:[bar]};
   if(kind==='lollipop'){
    const focused=Number.isInteger(d.focusIndex)&&d.focusIndex>=0&&d.focusIndex<d.values.length;
    if(focused)bar.itemStyle.color=v=>v.dataIndex===d.focusIndex?p.primary:p.muted;
    o.series.push({name:d.name||'',type:'scatter',data:d.values.map((v,i)=>({value:[v,i],itemStyle:{color:focused&&i!==d.focusIndex?p.muted:p.primary}})),symbolSize:focused?v=>v[1]===d.focusIndex?10:7:8,itemStyle:{color:p.primary},label:{show:true,position:'top',distance:4,fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value[0])+labelUnit}});
   }
  }else if(['grouped_bar','stacked_bar'].includes(kind)){
   o={grid:{...base.grid,top:exponent?52:base.grid.top},legend:legend(d.series.map(s=>s.name)),xAxis:category(d.labels),yAxis:value(kind==='stacked_bar'?{min:0}:{}),series:d.series.map((s,i)=>({name:s.name,type:'bar',data:s.values,stack:kind==='stacked_bar'?'total':undefined,barMaxWidth:24,labelLayout:{hideOverlap:true},itemStyle:{color:color(i),borderColor:p.ink,borderWidth:.3},label:{show:d.labels.length<=6&&kind!=='stacked_bar',position:'top',fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value)+labelUnit}}))};
  }else if(kind==='dumbbell'){
   // Keep endpoint text on its own category row, including compact paper panels.
   // The numeric axis reserves room for the left label; source values stay intact.
   const endpoints=d.pairs.flat(),minimum=Math.min(...endpoints),maximum=Math.max(...endpoints);
   const textWidth=text=>[...String(text)].reduce((sum,character)=>sum+font*(/[^\x00-\x7f]/.test(character)?1:.65),0);
   const categoryWidth=Math.max(...d.labels.map(textWidth));
   const labelRoom=Math.max(...endpoints.map(v=>textWidth(labelNumber(v)+labelUnit)))+14;
   const plotWidth=Math.max(48,(size.width||520)-14-52-8-categoryWidth);
   const padding=Math.min(2,Math.max(.32,labelRoom/Math.max(24,plotWidth-labelRoom)));
   const span=maximum-minimum||Math.max(Math.abs(maximum)*.05,1);
   // Label padding is presentation space, not an observed decimal value.
   // Round the scale outward so its artificial bounds produce readable ticks.
   const tickStep=10**Math.floor(Math.log10(span/5));
   const outward=(value,direction)=>{
    if(!(tickStep>0)||!Number.isFinite(tickStep))return value;
    const boundary=direction(value/tickStep)*tickStep;
    if(!Number.isFinite(boundary))return value;
    const rounded=Number(boundary.toPrecision(12));
    return direction===Math.floor?Math.min(value,rounded):Math.max(value,rounded);
   };
   const axisMinimum=outward(minimum-span*padding,Math.floor);
   const axisMaximum=outward(maximum,Math.ceil);
   o={grid:{...base.grid,top:50,bottom:32,right:52},legend:legend(d.periods),
    xAxis:value({scale:true,min:axisMinimum,max:axisMaximum}),yAxis:category(d.labels,{inverse:true,axisLabel:{fontSize:font,color:p.ink,interval:0,hideOverlap:false}}),
    series:[{type:'custom',silent:true,renderItem:(_params,api)=>{
      const a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)]);
      return {type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:p.muted,lineWidth:2}};
     },data:d.pairs.map((pair,i)=>[i,...pair]),encode:{x:[1,2],y:0},tooltip:{show:false}},
     ...d.periods.map((name,i)=>({name,type:'scatter',data:d.pairs.map((pair,row)=>({value:[pair[i],row],
      label:{show:pair[0]!==pair[1]||i===1,position:pair[i]<pair[1-i]?'left':'right'}})),symbolSize:9,
      itemStyle:{color:i?p.primary:p.paper,borderColor:i?p.primary:p.accent,borderWidth:2},
      label:{show:true,distance:7,color:p.ink,fontSize:font,formatter:v=>labelNumber(v.value[0])+labelUnit},labelLayout:{hideOverlap:true}}))]};
  }else if(kind==='heatmap'){
   const cells=d.matrix.flatMap((row,y)=>row.flatMap((v,x)=>v===null?[]:[[x,y,v]]));const all=cells.map(c=>c[2]);assert(all.length>0,'矩阵没有实际数值');
   const min=Math.min(...all),max=Math.max(...all),signed=min<0&&max>0,bound=Math.max(Math.abs(min),max);
   const bottom=signed?-bound:min,top=signed?bound:max===min?max+1:max,ramp=signed&&themeID==='research'?[p.negative,p.paper,p.positive]:p.ramp;
   const labeled=cells.map(value=>({value,label:{color:readableLabel(gradientColor(ramp,(value[2]-bottom)/(top-bottom)))}}));
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>d.rows[v.value[1]]+'\n'+d.labels[v.value[0]]+'\n'+number(v.value[2])+(d.unit||'')},grid:{left:16,right:26,top:20,bottom:64,containLabel:true},xAxis:category(d.labels,{splitArea:{show:false}}),yAxis:category(d.rows,{inverse:true}),visualMap:{min:signed?-bound:min,max:signed?bound:max===min?max+1:max,show:min!==max,orient:'horizontal',left:'center',bottom:4,calculable:false,textStyle:{fontSize:font,color:p.ink},inRange:{color:ramp}},series:[{type:'heatmap',data:labeled,label:{show:d.labels.length<=12&&(size.width-140)/d.labels.length>=21,fontSize:11,color:p.ink,formatter:v=>number(v.value[2])},itemStyle:{borderWidth:3,borderColor:p.paper},emphasis:{itemStyle:{borderColor:p.ink,borderWidth:2}}}]};
  }else if(kind==='scatter'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>text(d.points[v.dataIndex].name)+'\n'+(d.xName||'X')+' '+number(v.value[0])+'\n'+(d.yName||'Y')+' '+number(v.value[1])},grid:{...base.grid,top:54,bottom:38},xAxis:value({name:d.xName||'',nameLocation:'middle',nameGap:25,scale:true}),yAxis:value({name:(d.yName||'')+(d.comparison_periods&&plotUnit?'（'+plotUnit+'）':''),nameTextStyle:{fontSize:11,color:p.muted,align:'left'},scale:true}),series:[{type:'scatter',data:d.points.map((row,index)=>d.comparison_periods?{value:[row.x,row.y],name:row.name,itemStyle:{color:color(index)},label:{position:index?'right':'left'}}:[row.x,row.y]),symbolSize:10,label:d.comparison_periods?{show:true,fontSize:11,distance:6,formatter:v=>d.points[v.dataIndex].name}:undefined,labelLayout:{hideOverlap:true},itemStyle:{color:p.primary,borderColor:p.paper,borderWidth:1.5}}]};
  }else if(kind==='range'){
   o={xAxis:value({scale:true}),yAxis:category(d.ranges.map(row=>row.name),{inverse:true}),series:[{type:'custom',data:d.ranges.map((row,i)=>[i,row.min,row.max]),encode:{x:[1,2],y:0},renderItem:(_params,api)=>{const a=api.coord([api.value(1),api.value(0)]),b=api.coord([api.value(2),api.value(0)]);return {type:'group',children:[{type:'line',shape:{x1:a[0],y1:a[1],x2:b[0],y2:b[1]},style:{stroke:p.primary,lineWidth:5}},{type:'circle',shape:{cx:a[0],cy:a[1],r:4},style:{fill:p.paper,stroke:p.primary,lineWidth:2}},{type:'circle',shape:{cx:b[0],cy:b[1],r:4},style:{fill:p.paper,stroke:p.primary,lineWidth:2}}]}}},
    {name:'观测值',type:'scatter',data:d.ranges.flatMap((row,i)=>row.value==null?[]:[[row.value,i]]),symbolSize:12,itemStyle:{color:p.accent,borderColor:p.paper,borderWidth:2},label:{show:true,position:'top',fontSize:font,color:p.ink,formatter:v=>labelNumber(v.value[0])+labelUnit}}]};
   o.tooltip={trigger:'item',renderMode:'richText',formatter:v=>{const i=v.seriesIndex===0?v.value[0]:v.value[1],r=d.ranges[i];return r.name+'\n区间 '+number(r.min)+'~'+number(r.max)+(d.unit||'')+(r.value==null?'':'\n观测值 '+number(r.value)+(d.unit||''))}};
  }else if(kind==='boxplot'){
   o={xAxis:category(d.labels),yAxis:value({scale:true}),series:[{type:'boxplot',data:d.values,itemStyle:{color:p.paper,borderColor:p.primary,borderWidth:2}}]};
  }else if(kind==='candlestick'){
   o={xAxis:category(d.labels),yAxis:value({scale:true}),series:[{type:'candlestick',data:d.values,itemStyle:{color:p.paper,color0:p.negative,borderColor:p.positive,borderColor0:p.negative,borderWidth:1.6}}]};
  }else if(kind==='donut'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>v.name+' '+number(v.value)+(d.unit||'')},legend:{...legend(d.labels),bottom:0,top:undefined},series:[{type:'pie',radius:['49%','72%'],center:['50%','44%'],avoidLabelOverlap:true,label:{show:false},itemStyle:{borderColor:p.paper,borderWidth:4},data:d.labels.map((name,i)=>({name,value:d.values[i],itemStyle:{color:color(i)}}))}]};
  }else if(kind==='treemap'){
   o={tooltip:{trigger:'item',renderMode:'richText',formatter:v=>v.name+' '+number(v.value)+(d.unit||'')},series:[{type:'treemap',data:d.tree,roam:false,nodeClick:false,breadcrumb:{show:false},label:{show:true,fontSize:font,color:p.ink,formatter:'{b}'},upperLabel:{show:true,height:24,color:p.ink},color:colors,itemStyle:{borderColor:p.paper,gapWidth:3},levels:[{itemStyle:{borderWidth:0}},{itemStyle:{borderWidth:2},label:{color:p.paper}}]}]};
  }else if(kind==='waterfall'){
   let before=d.start;const nodes=[{name:d.startName||'起点',from:0,to:d.start}];for(const step of d.steps){nodes.push({name:step.name,from:before,to:before+step.value});before+=step.value}nodes.push({name:d.endName||'终点',from:0,to:d.end});
   o={xAxis:category(nodes.map(row=>row.name)),yAxis:value(),series:[{type:'custom',data:nodes.map((row,i)=>[i,row.from,row.to]),encode:{x:0,y:[1,2]},renderItem:(_params,api)=>{const a=api.coord([api.value(0),api.value(1)]),b=api.coord([api.value(0),api.value(2)]),width=Math.min(38,api.size([1,0])[0]*.6);return {type:'rect',shape:{x:a[0]-width/2,y:Math.min(a[1],b[1]),width,height:Math.abs(a[1]-b[1])},style:{fill:api.value(2)>=api.value(1)?p.primary:p.accent}}}}]};
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
      {type:'line',shape:{x1,y1,x2:baseX,y2:baseY},style:{stroke:p.muted,lineWidth:1.2,lineDash:[4,3]}},
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
  return {...base,...o};
 }
 function adapt(spec){
  if(spec.auto_visual)return {scheme:spec.auto_visual.scheme,data:spec.auto_visual.data};
  if(spec.scheme&&spec.data)return {scheme:spec.scheme,data:spec.data};
  if(spec.kind==='changes')return {scheme:'diverging_bar',data:{labels:spec.dates,values:spec.values,unit:spec.unit,name:spec.name}};
  if(spec.kind==='paired')return {scheme:spec.pairs.length===1?'grouped_bar':'dumbbell',data:{labels:spec.dates,pairs:spec.pairs,periods:spec.periods,
    series:spec.periods.map((name,index)=>({name,values:spec.pairs.map(pair=>pair[index])})),unit:spec.unit,name:spec.name}};
  return {scheme:spec.horizontal?'ranked_bar':spec.type==='line'?'line':'grouped_bar',data:{labels:spec.dates,values:spec.values,
    series:[{name:spec.name,values:spec.values}],unit:spec.unit,name:spec.name}};
 }
 function compatible(item){
  const kind=item.scheme;
  if(['line','area'].includes(kind))return item.data.measure==='scale'&&item.data.values.every(v=>v===null||v>=0)?['line','area','grouped_bar','lollipop']:['line','grouped_bar','lollipop'];
  if(['ranked_bar','lollipop','diverging_bar'].includes(kind))return ['ranked_bar','lollipop','diverging_bar'];
  if(kind==='dumbbell'||kind==='grouped_bar'&&item.data.pairs)return ['dumbbell','grouped_bar','slope'];
  if(kind==='heatmap'&&item.data.labels.every(label=>/^\d{4}-\d{2}/.test(label)))return ['heatmap','multi_line','grouped_bar'];
  if(['multi_line','grouped_bar'].includes(kind)&&item.data.series?.length>1)return ['multi_line','grouped_bar','heatmap'];
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
 function applyProfile(profile={}){
  theme(profile.theme||currentTheme);currentTheme=profile.theme||currentTheme;
  for(const [target,scheme] of Object.entries(profile.schemes||{})){
   const binding=reportBindings.find(row=>row.spec.target===target);assert(binding&&compatible(binding.item).includes(scheme),'当前数据不适用这套图形');overrides[target]=scheme;
  }
  const p=theme(currentTheme),root=document.documentElement;
  for(const [name,value] of Object.entries({'--INK':p.ink,'--TEXT_SECONDARY':p.muted,'--GRID_LINE':p.grid,'--PAPER':p.paper,'--PANEL':p.paper,'--NOTE_BG':p.paper,'--FORMULA_BG':p.paper,'--SERIES_MAIN':currentTheme==='research'?p.positive:p.ink,'--SERIES_BENCH':p.primary}))root.style.setProperty(name,value);
  document.body.style.background=p.canvas||'#F2F4F6';
  for(const row of reportBindings){
   const scheme=overrides[row.spec.target]||row.item.scheme;
   try{row.chart.setOption(option(scheme,materialize(row.item,scheme),currentTheme,{width:row.chart.getWidth(),height:row.chart.getHeight()}),true)}
   catch(error){row.error=error.message;}
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
 global.RHVisuals={catalogue,theme,validate:checked,option,number,adapt,compatible,materialize,bindReports,applyProfile,contexts,styleExport,
   profile:()=>({theme:currentTheme,schemes:{...overrides}})};
 addEventListener('message',event=>{
  if(event.source!==parent||event.data?.type!=='rh:visual-apply')return;
  try{applyProfile(event.data.profile);parent.postMessage({type:'rh:visual-applied',profile:global.RHVisuals.profile()},'*')}catch(error){parent.postMessage({type:'rh:visual-error',message:error.message},'*')}
 });
})(window);
