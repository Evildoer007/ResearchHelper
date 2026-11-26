/* Print charts must retain a real plotting area rather than collapse under labels. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/report-examples.js'),'utf8');
const context=vm.createContext({location:{protocol:'http:'},echarts:{graphic:{LinearGradient:function(...args){this.args=args;}}}});
vm.runInContext(source.slice(0,source.indexOf('for(const [index,figure] of window.')),context);
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../data/report_catalog.json'),'utf8')).sources;
function assertAxes(option){
  for(const axis of [option.xAxis,option.yAxis].flat()){
    assert.equal(axis.axisLine.show,true);
    assert.equal(axis.axisLine.onZero,false);
    assert.equal(axis.axisTick.show,true);
    assert.equal(axis.splitLine.show,false);
  }
}
const capex=JSON.parse(JSON.stringify(catalog.semi.charts[0]));capex.orientation='vertical';
context.figure=capex;
for(const height of [148,190,270]){
  const option=vm.runInContext(`exampleOption(figure,360,${height})`,context);
  assertAxes(option);
  assert.equal(option.grid.containLabel,false,'Fixed print geometry must not reserve axis labels twice');
  assert.ok(height-option.grid.top-option.grid.bottom>=height*.48,'At least 48% of chart height must encode data');
  assert.equal(option.yAxis.min,0);
  assert.ok(option.yAxis.max>=100);
  assert.ok(option.yAxis.max<=125,'Do not weaken the comparison with excessive headroom');
  assert.equal(option.yAxis.interval,20);
  assert.equal(option.series.length,3);
  assert.deepEqual(Array.from(option.series[0].data,point=>point.value),capex.series[0].data);
  assert.equal(option.xAxis.axisLabel.fontSize,11);
  assert.equal(option.series[0].label.fontSize,11);
}
for(const [values,max,interval] of [[[439,28,33,19],500,100],[[280,54,22,17],300,100]]){
  context.figure={categories:['美国','中国','德国','日本'],unit:'EOPS',orientation:'vertical',series:[{name:'算力',data:values}]};
  const option=vm.runInContext('exampleOption(figure,360,100)',context);
  assert.equal(option.yAxis.max,max);assert.equal(option.yAxis.interval,interval);
  assert.ok(100-option.grid.top-option.grid.bottom>=48);
}
context.figure={categories:['5月','6月','7月','8月至3日'],unit:'%',orientation:'vertical',
  series:[{name:'月度涨跌',data:[-8.95,-3.63,9.3,null]}]};
const signed=vm.runInContext('exampleOption(figure,360,160)',context);
assertAxes(signed);
assert.equal(signed.series[0].markLine.lineStyle.type,'solid');
assert.ok(signed.yAxis.min<-12,'Negative labels need room above the month names');
assert.equal(signed.series[0].data[3].value,'-','Missing observations must not become zero');
context.figure={presentation:'intervals',unit:'倍',categories:['样本一','样本二'],
  series:[{data:[20.5,14.3]},{data:[55.8,19.9]}]};
const intervals=vm.runInContext('exampleOption(figure,360,86)',context);
assertAxes(intervals);
assert.notEqual(intervals.xAxis.axisLabel.show,false);
context.figure={samples:[{name:'样本一',categories:['A','B'],values:[30,43]},{name:'样本二',categories:['C','D'],values:[20,50]}]};
assertAxes(vm.runInContext('exampleOption(figure,360,176)',context));
console.log('Passed: print plot geometry, zero baseline, readable scales, unchanged observations');
