/* Disclosed-data research figures. A layout never supplies missing observations. */
const exampleCharts=[];
const figureInk='#26374B';
const palette=['#B83C46','#2867A8','#74849A'];
const figureFont='Arial, PingFang SC, sans-serif';

function figureAxisBound(value){
  if(value===0)return 0;
  const step=10**Math.floor(Math.log10(Math.abs(value)))/2;
  return (value>0?Math.ceil(value/step):Math.floor(value/step))*step;
}

function figureScale(minimum,maximum){
  // Stable research ticks, with room for direct labels above the largest bar.
  const extent=Math.max(Math.abs(minimum),maximum);
  if(!extent)return {min:0,max:1,interval:.2};
  const target=extent/5,magnitude=10**Math.floor(Math.log10(target));
  const interval=[1,2,2.5,5,10].find(step=>step*magnitude>=target)*magnitude;
  const bound=value=>Math.round(value/interval)*interval;
  return {min:minimum<0?bound(Math.floor(minimum*1.04/interval)*interval):0,
    max:maximum>0?bound(Math.ceil(maximum*1.04/interval)*interval):0,interval};
}

function figureGradient(color,vertical){
  const light={'#B83C46':'#DDA5AA','#2867A8':'#A0BFDA','#74849A':'#B6C0CE','#C59AA0':'#E5CDD0'}[color]||color;
  return new echarts.graphic.LinearGradient(0,vertical?1:0,vertical?0:1,0,
    [{offset:0,color:light},{offset:1,color}]);
}

function figureValue(figure,series,index){
  const value=series.data[index];
  if(value===null)return '未披露';
  const label=series.labels?.[index]??String(value);
  return label+(figure.unit==='%'&&figure.id!=='valuationChart'?'%':'');
}

function timelineOption(figure,width){
  const vertical=width<600;
  const points=figure.nodes.map((node,index)=>({node,
    x:vertical?18:(index+.5)*width/figure.nodes.length,
    y:vertical?22+index*40:70}));
  const graphic=[];
  for(let index=1;index<points.length;index++)graphic.push({type:'line',
    shape:{x1:points[index-1].x,y1:points[index-1].y,x2:points[index].x,y2:points[index].y},
    style:{stroke:'#BAC6D2',lineWidth:1.5}});
  for(const [index,point] of points.entries()){
    graphic.push({type:'circle',shape:{cx:point.x,cy:point.y,r:5},
      style:{fill:index===points.length-1?palette[1]:palette[0],stroke:'#FFFFFF',lineWidth:2}});
    const labels=vertical?[[point.node.date,36,point.y-9],[point.node.name,36,point.y+9]]:
      [[point.node.date,point.x,point.y-25],[point.node.name,point.x,point.y+25]];
    for(const [text,x,y] of labels)graphic.push({type:'text',x,y,style:{text,
      align:vertical?'left':'center',verticalAlign:'middle',
      font:'13px '+figureFont,fill:figureInk}});
  }
  return {animation:false,graphic};
}

function exampleOption(figure,width,height=270){
  if(figure.kind==='timeline')return timelineOption(figure,width);
  if(figure.samples)return sampleComparisonOption(figure,width,height);
  if(figure.presentation==='intervals')return intervalOption(figure,width,height);
  const narrow=width<400;
  const one=figure.series.length===1;
  const vertical=figure.orientation==='vertical';
  // An absent observation is not a zero return. Keep the category and its
  // missing state visible without adding a bar, point or numerical estimate.
  const missing=figure.categories.map((_,index)=>figure.series.filter(series=>series.data[index]===null));
  const missingLabel=(label,index)=>{
    const absent=missing[index];
    if(!absent.length)return label;
    // The complete per-series names and nulls remain in the source table.
    // Keep the axis state short enough for narrow and printed categories.
    const detail=absent.length===figure.series.length?'未披露':'部分未披露';
    return label+'\n'+detail;
  };
  const categoryRoom=vertical&&missing.some(items=>items.length)?44:26;
  const categoryAxis={type:'category',data:figure.categories,
    axisLabel:{color:figureInk,fontSize:13,interval:0,width:vertical?70:narrow?104:160,
      overflow:'break',lineHeight:17,margin:10,formatter:missingLabel},axisTick:{show:false},axisLine:{show:false}};
  const valueAxis={type:'value',name:figure.unit,nameLocation:'end',nameGap:12,
    nameTextStyle:{fontSize:13,color:'#54647A',align:vertical?'left':'right'},
    axisLabel:{fontSize:13,color:'#64758A'},splitNumber:height<190?2:3,
    splitLine:{lineStyle:{color:'#E5EAF0',type:'dashed'}},axisLine:{show:false},axisTick:{show:false}};
  const observations=figure.series.flatMap(series=>series.data).filter(value=>value!==null);
  const minimum=Math.min(0,...observations),maximum=Math.max(0,...observations);
  if(vertical){
    Object.assign(valueAxis,figureScale(minimum,maximum));
    if(minimum<0){
      // Negative labels sit below their measured endpoint, above the category row.
      const labelRoom=(maximum-minimum)*.2;
      valueAxis.min=Math.floor((minimum-labelRoom)/valueAxis.interval)*valueAxis.interval;
    }
    const plotHeight=height-(one?24:40)-categoryRoom;
    const tickCount=(valueAxis.max-valueAxis.min)/valueAxis.interval;
    const labelStride=Math.max(1,Math.ceil(18/Math.max(1,plotHeight/tickCount)));
    valueAxis.axisLabel.formatter=value=>Math.abs(Math.round(value/valueAxis.interval))%labelStride===0?String(value):'';
  }
  if(!vertical&&minimum<0){
    // Leave measured space between negative value labels and row names.
    // Narrow figure frames still need measured room for the complete signed
    // value; a shorter bar must not let its label enter the category column.
    valueAxis.min=figureAxisBound(minimum-Math.max(Math.abs(minimum)*(narrow?1.6:.85),maximum*.3));
    valueAxis.max=figureAxisBound(maximum+Math.max(maximum*.15,Math.abs(minimum)*.12));
  }
  const zeroLine={silent:true,symbol:'none',label:{show:false},
    lineStyle:{color:'#A5B2C0',width:1,type:'solid'},data:[vertical?{yAxis:0}:{xAxis:0}]};
  const groupedColors=figure.id==='valuationChart'?['#C59AA0',palette[0],palette[1]]:palette;
  const barSeries=figure.series.map((series,index)=>({name:series.name,type:'bar',
    barMaxWidth:vertical?(one?32:18):14,
    barGap:figure.id==='valuationChart'?'100%':'32%',silent:true,
    data:series.data.map((value,item)=>{
      let color=one?(value<0?palette[1]:palette[0]):groupedColors[index%groupedColors.length];
      if(figure.color_by==='country')color=item===0?palette[0]:item===1?palette[1]:'#74849A';
      return {value:value===null?'-':value,itemStyle:{color:figureGradient(color,vertical)},
        label:{position:vertical?(value<0?'bottom':'top'):(value<0?'left':'right')}};
    }),
    label:{show:true,fontSize:13,fontFamily:figureFont,color:figureInk,distance:5,
      formatter:point=>figureValue(figure,series,point.dataIndex)},
    labelLayout:{moveOverlap:'shiftY',hideOverlap:false},
    ...(index===0?{markLine:zeroLine}:{})}));
  const option={animation:false,color:groupedColors,textStyle:{fontFamily:figureFont,fontSize:13,color:figureInk},
    tooltip:{show:false},legend:{show:!one,top:0,left:0,itemWidth:12,itemHeight:5,itemGap:12,
      textStyle:{fontSize:13,color:'#54647A'},formatter:name=>name==='全年实际/最新预期'?'实际/最新预期':name},
    // Category text, unit and legend live outside the plotting rectangle.
    // containLabel would reserve them again, flattening the 100–130px print charts.
    grid:vertical?{left:38,right:12,top:one?24:40,bottom:categoryRoom,containLabel:false}:
      {left:8,right:48,top:one?26:54,bottom:32,containLabel:true},
    xAxis:vertical?categoryAxis:valueAxis,yAxis:vertical?valueAxis:{...categoryAxis,inverse:true},series:barSeries};
  if(vertical){
    // A separate unit label keeps the axis name clear of the compact legend.
    option.yAxis.name='';
    option.graphic=[{type:'text',right:0,top:one?0:22,
      style:{text:figure.unit,fill:'#54647A',font:'13px '+figureFont}}];
  }
  if(figure.presentation==='endpoints'){
    // One reported terminal estimate per company, not a manufactured revenue path.
    option.series=[{...barSeries[0],barMaxWidth:2,label:{show:false}},
      {name:figure.series[0].name,type:'scatter',silent:true,symbolSize:10,
        data:figure.series[0].data.map((value,index)=>({value:[value,index],
          itemStyle:{color:palette[index%2]},label:{show:true,position:'right',distance:9,
            fontSize:13,fontFamily:figureFont,color:figureInk,formatter:String(value)}}))}];
    option.series[0].data=option.series[0].data.map((item,index)=>({...item,itemStyle:{color:palette[index%2],opacity:.65}}));
    option.xAxis={...valueAxis,max:Math.max(...figure.series[0].data)*1.2};
  }
  return option;
}

function sampleComparisonOption(figure,width,height){
  // Two independent cross-sections share a zero-based scale. Rows are not
  // matched securities or a time series; every label is an original sample.
  const gap=18,panel=(width-gap)/2,compact=height<190;
  const grid=[],xAxis=[],yAxis=[],series=[],graphic=[];
  for(const [index,sample] of figure.samples.entries()){
    const left=index*(panel+gap),color=palette[index];
    grid.push({left:left+62,width:Math.max(20,panel-95),top:28,bottom:compact?22:28});
    xAxis.push({gridIndex:index,type:'value',min:0,max:60,interval:30,
      axisLabel:{fontSize:13,color:'#54647A'},axisTick:{show:false},axisLine:{show:false},
      splitLine:{lineStyle:{color:'#E7EBF0'}}});
    yAxis.push({gridIndex:index,type:'category',inverse:true,data:sample.categories,
      axisLabel:{fontSize:13,color:figureInk,margin:8,interval:0},axisLine:{show:false},axisTick:{show:false}});
    series.push({type:'bar',silent:true,xAxisIndex:index,yAxisIndex:index,barMaxWidth:compact?9:14,
      data:sample.values,itemStyle:{color:figureGradient(color,false)},
      label:{show:true,position:'right',distance:4,fontSize:13,fontFamily:figureFont,color:figureInk,
        formatter:point=>Number(point.value).toFixed(1)}});
    graphic.push({type:'text',x:left,y:2,style:{text:sample.name,fill:color,font:'bold 13px '+figureFont}});
  }
  graphic.push({type:'text',right:0,bottom:0,style:{text:'倍',fill:'#54647A',font:'13px '+figureFont}});
  return {animation:false,textStyle:{fontFamily:figureFont,fontSize:13,color:figureInk},
    tooltip:{show:false},graphic,grid,xAxis,yAxis,series};
}

function intervalOption(figure,width,height){
  const low=figure.series[0].data,high=figure.series[1].data;
  const compact=height<95;
  return {animation:false,textStyle:{fontFamily:figureFont,color:figureInk,fontSize:13},
    graphic:compact?[{type:'text',right:0,top:0,style:{text:figure.unit,fill:'#54647A',font:'13px '+figureFont}}]:[],
    grid:{left:8,right:40,top:18,bottom:compact?0:8,containLabel:true},
    xAxis:{type:'value',min:0,max:65,splitNumber:3,name:compact?'':figure.unit,
      nameTextStyle:{fontSize:13,color:'#54647A'},axisLabel:{show:!compact,fontSize:13,color:'#54647A'},
      axisTick:{show:false},axisLine:{show:false},splitLine:{lineStyle:{color:'#E7EBF0'}}},
    yAxis:{type:'category',inverse:true,data:figure.categories,
      axisLabel:{fontSize:13,color:figureInk},axisTick:{show:false},axisLine:{show:false}},
    series:[{type:'custom',silent:true,
      data:low.map((value,index)=>[value,high[index],index]),
      renderItem:(params,api)=>{
        const start=api.coord([api.value(0),api.value(2)]),end=api.coord([api.value(1),api.value(2)]);
        const color=api.value(2)===0?palette[0]:palette[1];
        return {type:'group',children:[
          {type:'line',shape:{x1:start[0],y1:start[1],x2:end[0],y2:end[1]},style:{stroke:color,lineWidth:4}},
          ...[start,end].map(point=>({type:'circle',shape:{cx:point[0],cy:point[1],r:4},style:{fill:color}})),
          {type:'text',x:(start[0]+end[0])/2,y:start[1]-15,
            style:{text:api.value(0)+'–'+api.value(1),align:'center',verticalAlign:'middle',fill:figureInk,font:'13px '+figureFont}}
        ]};
      }}]};
}
for(const [index,figure] of window.RH_EXAMPLE_FIGURES.entries()){
  const node=document.getElementById('example-chart-'+index);
  const chart=echarts.init(node,null,{renderer:'svg'});
  chart.setOption(exampleOption(figure,node.clientWidth,node.clientHeight));exampleCharts.push({chart,node,figure});
}
const resizeExamples=()=>exampleCharts.forEach(({chart,node,figure})=>{
  const width=node.clientWidth,height=node.clientHeight;
  if(width<=0||height<=0)return;
  chart.resize({width,height,animation:{duration:0}});
  chart.setOption(exampleOption(figure,width,height),true);
  // WebKit printing can capture before a hidden window receives its next
  // animation frame. Flush the measured SVG, never scale its old screen tree.
  chart.getZr().flush();
});
if(typeof ResizeObserver!=='undefined'){
  const observer=new ResizeObserver(resizeExamples);
  exampleCharts.forEach(({node})=>observer.observe(node));
}
window.matchMedia('print').addEventListener('change',event=>{
  if(event.matches)resizeExamples();else requestAnimationFrame(resizeExamples);
});
addEventListener('beforeprint',resizeExamples);
addEventListener('afterprint',()=>requestAnimationFrame(resizeExamples));
