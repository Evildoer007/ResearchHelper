/* One observation must remain one value across ECharts drawing layers. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},box={window,addEventListener(){},Intl,Map};
for(const name of ['visual-catalog-data','visual-library'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend',name+'.js'),'utf8'),box);
const visuals=window.RHVisuals;
for(const scheme of ['lollipop','ranked_bar','diverging_bar']){
 test(scheme+' reads one source amount and its unit across duplicated plot layers',()=>{
  const data={labels:['经营现金流净额','净利润'],values:[602.16851,470.30638],unit:'亿元',name:'财务比较'};
  const frozen=JSON.stringify(data),option=visuals.option(scheme,data);
  const params=[{seriesIndex:0,dataIndex:0,value:602.16851},{seriesIndex:1,dataIndex:0,value:[602.16851,0]}];
  assert.equal(option.tooltip.formatter(params),'经营现金流净额\n602.1685亿元');
  assert.equal(option.tooltip.formatter(params[1]),'经营现金流净额\n602.1685亿元');
  assert.equal(JSON.stringify(data),frozen);
 });
 test(scheme+' preserves zero, negative and missing values without reading coordinates',()=>{
  const option=visuals.option(scheme,{labels:['零','负','缺值'],values:[0,-1.25,null],unit:'%'});
  assert.equal(option.tooltip.formatter({dataIndex:0,value:[999,2]}),'零\n0%');
  assert.equal(option.tooltip.formatter({dataIndex:1,value:[999,2]}),'负\n-1.25%');
  assert.equal(option.tooltip.formatter({dataIndex:2,value:[999,2]}),'缺值\n未披露');
  for(const params of [undefined,[],[null],{dataIndex:-1},{dataIndex:3},{dataIndex:'0'}])assert.equal(option.tooltip.formatter(params),'');
 });
 test(scheme+' formats registered fiscal labels and keeps original category identities',()=>{
  const data={labels:['20261231'],dateLabels:{20261231:'2026-12-31'},values:[-2],unit:'亿元'};
  const option=visuals.option(scheme,data);
  assert.equal(option.tooltip.formatter([{dataIndex:0,value:-2}]),'2026-12-31\n-2亿元');
  assert.equal(option.yAxis.data[0],'20261231');
 });
}

test('signed bars reserve the negative label side and keep a solid zero line',()=>{
 const {series:[bars],yAxis}=visuals.option('diverging_bar',{
  labels:['本期','前期','缺测'],values:[-28.1,13.7,null],unit:'%'
 },'red_blue',{width:340,height:155});
 assert.deepEqual(Array.from(bars.data,row=>row?.label.position??null),['left','right',null]);
 assert.equal(bars.label.formatter({value:13.7}),'+13.7');
 assert.equal(bars.label.formatter({value:-28.1}),'-28.1');
 assert.ok(yAxis.axisLabel.margin>8);
 assert.equal(bars.markLine.data[0].xAxis,0);
 assert.equal(bars.markLine.lineStyle.type,'solid');
});

test('extreme bars keep their labels inside the value-axis frame',()=>{
 for(const readings of [[-1.2,-4.5,-33.5],[-33.5,2,29.7],[24.5,15,4]]){
  const {xAxis,series:[bars],grid,yAxis}=visuals.option('diverging_bar',{
   labels:['营业收入','归母净利润','经营现金流'],values:readings,unit:'%',numberDigits:1
  },'red_blue',{width:294,height:162});
  assert.equal(bars.label.show,true);
  const plotWidth=294-grid.left-grid.right-55-yAxis.axisLabel.margin-8;
  const span=xAxis.max-xAxis.min;
  const low=Math.min(...readings),high=Math.max(...readings);
  if(low<0)assert.ok((low-xAxis.min)*plotWidth/span>=30);
  if(high>0)assert.ok((xAxis.max-high)*plotWidth/span>=29);
  assert.ok(xAxis.min<=0&&xAxis.max>=0&&xAxis.interval>0);
 }
});

test('dated observations keep a temporal alternative without an endpoint ranking',()=>{
 const data={labels:['2026-10-26','2026-10-27','2026-10-28'],values:[1,2,3]};
 assert.deepEqual(Array.from(visuals.compatible({scheme:'line',data})),['line','grouped_bar']);
 assert.deepEqual(Array.from(visuals.compatible({scheme:'ranked_bar',data})),['ranked_bar','diverging_bar']);
 assert.ok(visuals.compatible({scheme:'ranked_bar',data:{...data,labels:['甲','乙','丙']}}).includes('lollipop'));
});
