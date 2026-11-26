/* Rendered labels are readable; dates, identities and numbers remain frozen. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},box={window,addEventListener(){},Intl,Map};
for(const name of ['visual-catalog-data','visual-library'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend',name+'.js'),'utf8'),box);
const visuals=window.RHVisuals,plain=value=>JSON.parse(JSON.stringify(value));
const dates={'20241231':'2024-12-31','20251231':'2025-12-31'};
const frozen=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(frozen);Object.freeze(value);}return value;};

test('time axes use readable days without changing missing or numeric observations',()=>{
 const data=frozen({labels:['20241231','20251231'],values:[20241231,null],unit:'元',dateLabels:dates}),before=JSON.stringify(data);
 const option=visuals.option('line',data);
 assert.deepEqual(plain(option.xAxis.data),data.labels);
 assert.equal(option.xAxis.axisLabel.formatter('20241231'),'2024-12-31');
 assert.equal(JSON.stringify(data),before);
 assert.equal(option.series[0].data[0].value??option.series[0].data[0],20241231);
});

test('period legends and rendered series have the same names after conversion',()=>{
 const data=frozen({labels:['收入','利润'],periods:['20241231','20251231'],pairs:[[100,120],[9,12]],unit:'',dateLabels:dates}),before=JSON.stringify(data);
 for(const scheme of ['grouped_bar','slope']){
  const item=visuals.adapt({scheme:'dumbbell',data}),option=visuals.option(scheme,visuals.materialize(item,scheme));
  if(scheme==='slope'){
   assert.deepEqual(plain(option.legend.data),data.labels);
   assert.deepEqual(plain(option.xAxis.data),data.periods);
   assert.equal(option.xAxis.axisLabel.formatter('20241231'),'2024-12-31');
  }else{
   assert.deepEqual(plain(option.legend.data),data.periods);
   assert.equal(option.legend.formatter('20241231'),'2024-12-31');
   const named=option.series.map(row=>row.name).filter(Boolean);
   assert(named.includes('20241231')&&named.includes('20251231'));
  }
 }
 assert.equal(JSON.stringify(data),before);
});

test('scatter captions and tooltips use the rendered period, preserving coordinates',()=>{
 const data=frozen({points:[{name:'20241231',x:100,y:15},{name:'20251231',x:120,y:10}],comparison_periods:['20241231','20251231'],dateLabels:dates,xName:'净利润',yName:'现金流',unit:'原值'}),before=JSON.stringify(data);
 const option=visuals.option('scatter',data);
 assert.equal(option.series[0].data[0].name,'20241231');
 assert.equal(option.series[0].label.formatter({dataIndex:0}),'2024-12-31');
 assert.deepEqual(plain(option.series[0].data[0].value),[100,15]);
 assert(option.tooltip.formatter({dataIndex:0,value:[100,15]}).startsWith('2024-12-31\n'));
 assert.equal(JSON.stringify(data),before);
});

test('matrix hover dates agree with visible axes and retain exact cells',()=>{
 const data=frozen({labels:['20241231','20251231'],rows:['利润'],matrix:[[9,12]],unit:'元',dateLabels:dates}),before=JSON.stringify(data);
 const option=visuals.option('heatmap',data);
 assert.deepEqual(plain(option.xAxis.data),data.labels);
 assert.equal(option.xAxis.axisLabel.formatter('20241231'),'2024-12-31');
 assert(option.tooltip.formatter({value:[0,0,9]}).includes('2024-12-31'));
 assert.equal(JSON.stringify(data),before);
});

test('ordinary object names and fiscal labels remain literal without aliases',()=>{
 const data=frozen({labels:['600519.SH','2024年上半年','20241231'],values:[100,200,300],unit:'元'}),before=JSON.stringify(data);
 const option=visuals.option('lollipop',data);
 const categories=option.yAxis.data||option.yAxis[0].data;
 assert.deepEqual(plain(categories),data.labels);
 assert.equal(JSON.stringify(data),before);
});

test('non-string display aliases cannot replace a date or mutate input',()=>{
 const data=frozen({labels:['20241231'],values:[1],dateLabels:{'20241231':42}}),before=JSON.stringify(data);
 const option=visuals.option('line',data);
 assert.deepEqual(plain(option.xAxis.data),['20241231']);
 assert.equal(option.xAxis.axisLabel.formatter('20241231'),'20241231');
 assert.equal(JSON.stringify(data),before);
});


test('tooltip labels are readable while the raw ECharts identities stay stable',()=>{
 const data=frozen({labels:['20241231','20251231'],values:[100,120],unit:'元',dateLabels:dates}),before=JSON.stringify(data);
 const line=visuals.option('line',data);
 const text=line.tooltip.formatter([{axisValueLabel:'20241231',seriesName:'经营观察',value:100}]);
 assert.equal(text,'2024-12-31\n经营观察：100元');
 const pair=frozen({labels:['收入'],periods:['20241231','20251231'],pairs:[[100,120]],dateLabels:dates,unit:'元'});
 const converted=visuals.adapt({scheme:'dumbbell',data:pair});
 const plotted=visuals.option(converted.scheme,converted.data);
 assert.equal(plotted.tooltip.formatter({axisValueLabel:'收入',seriesName:'20241231',value:100}),'收入\n2024-12-31：100元');
 assert.deepEqual(plain(line.xAxis.data),data.labels);
 assert.deepEqual(plain(plotted.legend.data),pair.periods);
 assert.equal(JSON.stringify(data),before);
});
