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

test('numeric axes retain distinct fractional ticks when observations use integer precision',()=>{
 const data={labels:['收入','利润'],periods:['2024','2025'],pairs:[[100,98.79],[100,95.47]],unit:'',numberDigits:0};
 const frozen=JSON.stringify(data),option=visuals.option('dumbbell',data);
 const format=option.xAxis.axisLabel.formatter;
 assert.equal(format(99.5),'99.5');assert.equal(format(100),'100');
 assert.equal(new Set([98,98.5,99,99.5,100].map(format)).size,5);
 assert.equal(JSON.stringify(data),frozen);
});
test('padding bounds do not print floating-point tails from real indexed comparisons',()=>{
 const data={labels:['收入','归母净利润'],periods:['2024','2025'],pairs:[[100,98.7940441671987],[100,95.46743641351]],unit:''};
 const before=JSON.stringify(data),option=visuals.option('dumbbell',data),axis=option.xAxis;
 const minimum=Math.min(...data.pairs.flat()),maximum=Math.max(...data.pairs.flat());
 assert.ok(axis.min<minimum);assert.ok(axis.max>=maximum);
 assert.ok(axis.axisLabel.formatter(axis.min).length<=8);
 assert.ok(axis.axisLabel.formatter(axis.max).length<=8);
 assert.equal(JSON.stringify(data),before);
 assert.deepEqual(plain(option.series.slice(1).map(s=>s.data.map(p=>p.value[0]))),[[100,100],[98.7940441671987,95.46743641351]]);
});
test('outward bounds preserve signed and small decimal comparisons',()=>{
 for(const pairs of [[[-.0012,-.0008],[0,.0004]],[[.000021,.000017],[.000019,.000022]]]){
  const option=visuals.option('dumbbell',{labels:['a','b'],periods:['before','after'],pairs,unit:''});
  assert.ok(option.xAxis.min<=Math.min(...pairs.flat()));assert.ok(option.xAxis.max>=Math.max(...pairs.flat()));
  assert.notEqual(option.xAxis.axisLabel.formatter(option.xAxis.min),option.xAxis.axisLabel.formatter(option.xAxis.max));
 }
});
test('close observations on a large scale cannot be clipped by rounded bounds',()=>{
 const pairs=[[1e12+.1,1e12+.2],[1e12+.3,1e12+.4]];
 const option=visuals.option('dumbbell',{labels:['a','b'],periods:['before','after'],pairs,unit:''});
 assert.ok(option.xAxis.min<=Math.min(...pairs.flat()));assert.ok(option.xAxis.max>=Math.max(...pairs.flat()));
 assert.ok(Number.isFinite(option.xAxis.min)&&Number.isFinite(option.xAxis.max));
});
test('paired endpoint labels face away from the connecting segment',()=>{
 const option=visuals.option('dumbbell',{labels:['a','b','c'],periods:['before','after'],pairs:[[100,98.79],[4,6],[3,3]],unit:''});
 const [before,after]=option.series.slice(1);
 assert.deepEqual(plain(before.data.map(p=>p.label.position)),['right','left','right']);
 assert.deepEqual(plain(after.data.map(p=>p.label.position)),['left','right','right']);
 assert.equal(before.data[2].label.show,false);assert.equal(after.data[2].label.show,true);
 assert.equal(option.grid.right,52);assert.equal(before.labelLayout.hideOverlap,true);assert.equal(after.labelLayout.hideOverlap,true);
 assert.deepEqual(plain(before.data.map(p=>p.value)),[[100,0],[4,1],[3,2]]);
});

// Extremes and the final observation must not label the same point twice.
test('line labels reserve the last observed point for its single endpoint label',()=>{
 for(const values of [[1,2,3,4],[4,3,2,1],[1,2,3,4,null],[1,5,2,3]]){
  const data={labels:values.map((_,i)=>String(i)),values,unit:'元'},frozen=JSON.stringify(data);
  const series=visuals.option('line',data).series[0],last=values.findLastIndex(Number.isFinite);
  assert(series.markPoint.data.every(point=>point.coord[0]!==last));
  assert.equal(series.label.formatter({dataIndex:last,value:values[last]}),values[last]+'元');
  assert.equal(series.connectNulls,false);assert.equal(JSON.stringify(data),frozen);
 }
 const flat=visuals.option('line',{labels:['a','b','c','d'],values:[2,2,2,2]}).series[0];
 assert.equal(flat.markPoint.data.length,1);
 const interior=visuals.option('line',{labels:['a','b','c','d'],values:[3,1,5,4]}).series[0];
 assert.deepEqual(plain(interior.markPoint.data.map(point=>point.coord)),[[1,1],[2,5]]);
});

test('line extremum labels face into the plot without altering points or gaps',()=>{
 const data={labels:['a','b','c','d','e','f'],values:[1255.79,1270,null,1392.05,1350,1360],unit:'元'};
 const frozen=JSON.stringify(data),series=visuals.option('line',data).series[0];
 const [low,high]=series.markPoint.data;
 assert.deepEqual(plain(low.coord),[0,1255.79]);
 assert.equal(low.label.position,'bottom');assert.equal(low.label.align,'left');assert(low.label.offset[0]>0);
 assert.deepEqual(plain(high.coord),[3,1392.05]);
 assert.equal(high.label.position,'top');assert.equal(high.label.align,'right');assert(high.label.offset[0]<0);
 assert.deepEqual(plain(series.data),data.values);assert.equal(JSON.stringify(data),frozen);
});

test('the final label stays off its approaching segment, including gaps and zero',()=>{
 for(const [values,position] of [
  [[.2,1.3,1,1.2,1.2,1,.5,.8,.9],'top'],
  [[-.7,1.7,.3,-1.6,-1.7,-1.6,-1.5,-1.4,-1.7],'bottom'],
  [[3,null,0,null],'bottom'],[[0,0,0,0],'top']]){
  const data={labels:values.map((_,i)=>String(i)),values,unit:'%'};
  const before=JSON.stringify(data),series=visuals.option('line',data).series[0];
  assert.equal(series.label.position,position);
  assert.equal(series.label.align,'right');assert(series.label.distance>=6);
  assert.equal(series.label.backgroundColor,visuals.theme('research').paper);
  assert.equal(series.label.formatter({dataIndex:values.findLastIndex(Number.isFinite),value:values.filter(Number.isFinite).at(-1)}),values.filter(Number.isFinite).at(-1)+'%');
  assert.equal(series.connectNulls,false);assert.deepEqual(plain(series.data),values);
  assert.equal(JSON.stringify(data),before);
 }
 const area=visuals.option('area',{labels:['a','b','c','d'],values:[0,2,1,0],unit:'份',measure:'scale'});
 assert.equal(area.series[0].label.position,'top','Area zero remains above its zero baseline');
});

test('area baseline stays at zero with a readable minimum annotation above it',()=>{
 const data={labels:['a','b','c','d'],values:[0,1,3,2],unit:'份',measure:'scale'};
 const frozen=JSON.stringify(data),option=visuals.option('area',data);
 const minimum=option.series[0].markPoint.data.find(point=>point.value===0);
 assert.equal(minimum.label.position,'top');assert.equal(option.yAxis.scale,false);
 assert.deepEqual(plain(option.series[0].data),data.values);assert.equal(JSON.stringify(data),frozen);
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
