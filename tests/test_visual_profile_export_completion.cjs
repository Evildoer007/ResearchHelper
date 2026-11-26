/* Offline value-preservation regressions for browser gallery conversions. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},box={window,addEventListener(){},Intl,Map};
for(const name of ['visual-catalog-data','visual-library'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend',name+'.js'),'utf8'),box);
const visuals=window.RHVisuals,plain=value=>JSON.parse(JSON.stringify(value));
test('dumbbell conversion retains both periods including zero and missing',()=>{
 const item={scheme:'dumbbell',data:{labels:['a','b'],periods:['before','after'],pairs:[[0,2],[-3,null]]}},before=JSON.stringify(item);
 const data=visuals.materialize(item,'grouped_bar');
 assert.deepEqual(plain(data.series),[{name:'before',values:[0,-3]},{name:'after',values:[2,null]}]);
 assert.equal(JSON.stringify(item),before);
});
test('heatmap conversion retains every row in original column order',()=>{
 const item={scheme:'heatmap',data:{labels:['2025-01','2025-02'],rows:['a','b'],matrix:[[0,null],[-3,2]]}},before=JSON.stringify(item);
 const data=visuals.materialize(item,'grouped_bar');
 assert.deepEqual(plain(data.series),[{name:'a',values:[0,null]},{name:'b',values:[-3,2]}]);
 assert.equal(JSON.stringify(item),before);
});

test('the gallery connects only categories that form an ordered period axis',()=>{
 const series=[{name:'前期',values:[100,20]},{name:'本期',values:[98,null]}];
 const cases=[
  [['营收','利润'],false],[['2025-01','2025-02'],true],[['2024','2025'],true],
  [['2024-02-29','2024-03-01'],true],[['2025-02-30','2025-03-01'],false],
  [['2025-02','2025-01'],false],[['2025-01','2025-01'],false],[['2024','2025-01'],false]
 ];
 for(const [labels,chronological] of cases){
  const bars={scheme:'grouped_bar',data:{labels,series}};
  const before=JSON.stringify(bars);
  assert.equal(visuals.compatible(bars).includes('multi_line'),chronological);
  const matrix=visuals.materialize(bars,'heatmap');
  assert.deepEqual(plain(matrix.matrix),[[100,20],[98,null]]);
  assert.equal(visuals.compatible({scheme:'heatmap',data:matrix}).includes('multi_line'),chronological);
  assert.equal(JSON.stringify(bars),before);
 }
});

function sampleScale(values,width=340){
 const samples=values.map((value,index)=>({date:'2026-01-0'+(index+1),value}));
 const data={samples,minimum:Math.min(...values),maximum:Math.max(...values),latest:values.at(-1),
  unit:'元',window:{start:samples[0].date,end:samples.at(-1).date}};
 return visuals.option('sample_range',data,'research',{width,height:180}).xAxis;
}

test('numeric axes retain fractional ticks and readable bounds',()=>{
 const axis=sampleScale([98.7940441671987,100,95.46743641351]);
 const format=axis.axisLabel.formatter;
 assert.equal(format(99.5),'99.5');
 assert.equal(new Set([98,98.5,99,99.5,100].map(format)).size,5);
 assert.ok(format(axis.min).length<=8&&format(axis.max).length<=8);
});

test('observed range bounds include signed, small, and closely spaced large values',()=>{
 for(const values of [[-.0012,-.0008,.0004],[.000021,.000017,.000022],[1e12+.1,1e12+.2,1e12+.4]]){
  const axis=sampleScale(values);
  assert.ok(axis.min<=Math.min(...values)&&axis.max>=Math.max(...values));
  assert.ok(Number.isFinite(axis.interval)&&axis.interval>0);
  assert.notEqual(axis.axisLabel.formatter(axis.min),axis.axisLabel.formatter(axis.max));
 }
});

test('a paired comparison can change its view while retaining the original scale and values',()=>{
 const data={labels:['收入','现金流','利润'],periods:['2024','2025'],
  pairs:[[100,98.79],[100,66.54],[100,96.72]],unit:'',categoricalComparison:false};
 const original=JSON.stringify(data),item=visuals.adapt({scheme:'dumbbell',data});
 for(const width of [236,800]){
  const bars=visuals.option('grouped_bar',item.data,'research',{width,height:200});
  const slope=visuals.option('slope',item.data,'research',{width,height:200});
  assert.deepEqual(plain(bars.series.map(row=>row.data)),[[100,100,100],[98.79,66.54,96.72]]);
  assert.deepEqual(plain(slope.series.map(row=>row.data)),data.pairs);
 }
 assert.equal(JSON.stringify(data),original);
});

const plot={x:34,y:22,width:180,height:90};
function annotations(option, coord){
 const overlay=option.series.find(series=>series.id?.startsWith('line-annotations'));
 assert(overlay&&overlay.silent&&overlay.tooltip.show===false);
 const render=overlay.renderItem({coordSys:plot},{coord,getWidth:()=>300,getHeight:()=>150});
 return render.children.map(child=>child.style);
}
const coordinates=([index,value])=>[44+index*24,103-value*12];

test('lines keep raw values and label their last valid reading just once',()=>{
 for(const values of [[1,2,3,4],[4,3,2,1],[1,2,3,4,null],[0,0,0,0]]){
  const data={labels:values.map((_,index)=>String(index)),values,unit:'元'};
  const before=JSON.stringify(data);
  const option=visuals.option('line',data,'research',{width:300,height:150});
  const series=option.series[0],last=values.findLastIndex(Number.isFinite);
  const labels=annotations(option,coordinates);
  assert.equal(labels.length,1);
  assert.equal(labels[0].text,values[last]+'元');
  assert.equal(series.showSymbol,false);
  assert.equal(series.connectNulls,false);
  assert.deepEqual(plain(series.data),values);
  assert.equal(JSON.stringify(data),before);
 }
});

test('only selected extrema receive their own labels and missing periods remain gaps',()=>{
 const data={labels:['a','b','c','d','e','f'],values:[1,2,null,5,4,3],unit:'元',keyExtrema:[0,2,3,5]};
 const before=JSON.stringify(data);
 const option=visuals.option('line',data,'research',{width:300,height:150});
 const labels=annotations(option,coordinates);
 assert.deepEqual(Array.from(labels,label=>label.text),['3元','1','5']);
 assert(labels.every(label=>label.x>=plot.x&&label.y>=plot.y));
 assert.deepEqual(plain(option.series[0].data),data.values);
 assert.equal(JSON.stringify(data),before);
});

test('only isolated measured readings have a mark when a line contains gaps',()=>{
 const data={labels:['a','b','c','d'],values:[0,null,2,3],unit:'%'};
 const series=visuals.option('line',data).series[0];
 assert.equal(series.connectNulls,false);
 assert.equal(series.showSymbol,true);
 assert.deepEqual(data.values.map((_,dataIndex)=>series.symbol(null,{dataIndex})),['rect','none','none','none']);
 assert.deepEqual(plain(series.data),data.values);
});

test('annotation overlays anchor to a real observation without extending the axes',()=>{
 for(const [values, expected] of [[[null,1349.98,1335], [[1,1349.98]]], [[null,null,null], []]]){
  const option=visuals.option('line',{labels:['a','b','c'],values,unit:'元'});
  const overlay=option.series.find(series=>series.id?.startsWith('line-annotations'));
  assert.deepEqual(plain(overlay.data),expected);
  assert.deepEqual(plain(overlay.encode),{x:0,y:1,tooltip:[]});
  assert.deepEqual(plain(option.series[0].data),values);
 }
});

test('long line legends and endpoint readings leave room for the plot',()=>{
 const labels=['a','b','c','d'], name='这是一条需要显示完整内容的较长指标名称';
 const series=Array.from({length:5},(_,index)=>({name:name+index,values:[1e15+index,1e15+10+index,1e15+5,1e15+7]}));
 const option=visuals.option('multi_line',{labels,series,unit:'累计复权基准点'},'research',{width:300,height:160});
 assert.equal(option.legend.type,'scroll');
 assert.equal(option.legend.textStyle.overflow,'truncate');
 assert(option.grid.top<=54);
 assert(option.grid.right<=81);
 assert.deepEqual(plain(option.series.slice(0,5).map(row=>row.data)),series.map(row=>row.values));
});

test('nearby series endpoints move into separate positions',()=>{
 const data={labels:['a','b','c','d'],series:[{name:'A',values:[2,3,2,2]},{name:'B',values:[3,2,3,2.1]}],unit:'%'};
 const option=visuals.option('multi_line',data,'research',{width:300,height:150});
 const labels=annotations(option,coordinates);
 assert.deepEqual(Array.from(labels,label=>label.text),['2%','2.1%']);
 assert.equal(labels[0].x,plot.x+plot.width+5);
 assert.notDeepEqual([labels[0].x,labels[0].y],[labels[1].x,labels[1].y]);
 assert(option.series.filter(series=>series.type==='line').every(series=>series.showSymbol===false));
});

test('area preserves the zero baseline and explicit minimum',()=>{
 const data={labels:['a','b','c','d'],values:[0,1,3,2],unit:'份',measure:'scale',keyExtrema:[0]};
 const before=JSON.stringify(data);
 const option=visuals.option('area',data,'research',{width:300,height:150});
 const labels=annotations(option,coordinates);
 assert.deepEqual(Array.from(labels,label=>label.text),['2份','0']);
 assert(labels.find(label=>label.text==='0').y<coordinates([0,0])[1]);
 assert.equal(option.yAxis.scale,false);
 assert.deepEqual(plain(option.series[0].data),data.values);
 assert.equal(JSON.stringify(data),before);
});

test('qualitative paths retain complete labels and links inside their inset view',()=>{
 const names=['需求变化需要结合实际范围及适用条件核对','传导条件','业务影响','相反解释','反馈变化'];
 const data={qualitative:true,nodes:names.map((name,index)=>({id:'n'+index,name,level:index,kind:'hypothesis'})),
  links:names.slice(1).map((_,index)=>({source:'n'+index,target:'n'+(index+1)}))};
 const frozen=JSON.stringify(data),option=visuals.option('evidence',data,'research',{width:350,height:260});
 const [edges,nodes]=option.series;
 assert.deepEqual(plain(nodes.data.map(node=>node.name)),names);
 assert.deepEqual(plain(edges.data.map(({source,target})=>({source,target}))),data.links);
 const shapes=nodes.data.map((_,dataIndex)=>nodes.renderItem({dataIndex}).children);
 assert(shapes.every(([box])=>box.shape.x>=8&&box.shape.y>=8));
 assert.equal(new Set(shapes.map(([box])=>box.shape.y)).size,2);
 assert.equal(shapes[0][1].style.text.replaceAll('\n',''),names[0]);
 assert(edges.data.every((_,dataIndex)=>edges.renderItem({dataIndex}).children[1].type==='polygon'));
 assert.equal(JSON.stringify(data),frozen);assert(data.nodes.every(node=>!('value' in node)));
});


test('calendar display labels preserve series identity and raw period inputs',()=>{
 const data={labels:['现金转化'],periods:['20241231','20251231'],pairs:[[1.04,.72]],unit:'倍',
  dateLabels:{'20241231':'2024-12-31','20251231':'2025-12-31'}};
 const frozen=JSON.stringify(data),item=visuals.adapt({scheme:'dumbbell',data});
 const option=visuals.option(item.scheme,item.data);
 assert.equal(option.legend.formatter('20241231'),'2024-12-31');
 assert.equal(option.legend.formatter('unmapped'),'unmapped');
 assert.equal(option.legend.formatter('20251231'),'2025-12-31');
 assert.deepEqual(plain(option.series.map(row=>row.name)),data.periods);
 assert.deepEqual(plain(option.legend.data),data.periods);
 assert.equal(JSON.stringify(data),frozen);
});

test('temporal axis labels use only the frozen label mapping and leave legacy charts alone',()=>{
 const data={labels:['20241231','20250229','20251231'],values:[1,null,2],
  dateLabels:{'20241231':'2024-12-31','20251231':'2025-12-31'}};
 const frozen=JSON.stringify(data),axis=visuals.option('line',data).xAxis;
 assert.equal(axis.axisLabel.formatter('20241231'),'2024-12-31');
 assert.equal(axis.axisLabel.formatter('20250229'),'20250229');
 assert.deepEqual(plain(axis.data),data.labels);
 const legacy={...data};delete legacy.dateLabels;
 assert.equal(visuals.option('line',legacy).xAxis.axisLabel.formatter,undefined);
 assert.equal(JSON.stringify(data),frozen);
});

test('scatter marginal marks use the same paired values and keep the primary source binding', () => {
 const points=[{x:0,y:1},{x:0,y:2},{x:1,y:2},{x:1,y:-1},{x:2,y:4},{x:3,y:-1}];
 const item={scheme:'scatter',data:{points,xName:'营收同比（%）',yName:'利润同比（%）'}};
 const before=JSON.stringify(item);
 assert.deepEqual(plain(visuals.compatible(item)),['scatter','scatter_rug']);
 assert.deepEqual(plain(visuals.compatible({...item,data:{points:points.slice(1)}})),['scatter']);
 const option=visuals.option('scatter_rug',item.data,'red_blue',{width:390,height:240});
 const coordinates=points.map(({x,y})=>[x,y]);
 for(const series of option.series)assert.deepEqual(plain(series.data),coordinates);
 assert(option.xAxis.axisLine.show&&option.yAxis.axisLine.show);
 assert(!option.xAxis.splitLine.show&&!option.yAxis.splitLine.show);
 assert.equal(option.series[1].silent,true);
 const spec={scheme:'scatter_rug',data:item.data,data_refs:['A','B','C','D'],point_refs:[['A','B'],['C','D']]};
 assert.deepEqual(plain(visuals.observationRefs(spec,{seriesIndex:0,dataIndex:1})),['C','D']);
 assert.equal(JSON.stringify(item),before);
});
