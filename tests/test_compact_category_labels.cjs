/* A paired comparison must expose every real category, including print panels. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},box={window,addEventListener(){},Intl,Map};
for(const name of ['visual-catalog-data','visual-library'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend',name+'.js'),'utf8'),box);
test('compact period comparisons retain all categories and source values',()=>{
 const data={labels:['CPI:当月同比','CPI:食品:当月同比','CPI:消费品:当月同比'],periods:['2026-01-31','2026-09-30'],pairs:[[.2,.9],[-.7,-1.7],[.3,1]],unit:'%',categoricalComparison:true};
 const original=JSON.stringify(data),visuals=window.RHVisuals,chart=visuals.adapt({scheme:'dumbbell',data});
 const option=visuals.option(chart.scheme,chart.data,'research',{width:310,height:180});
 assert.equal(option.xAxis.axisLabel.interval,0);assert.equal(option.xAxis.axisLabel.hideOverlap,false);
 assert.equal(JSON.stringify(option.xAxis.data),JSON.stringify(data.labels));
 assert.equal(JSON.stringify(option.series.map(series=>series.data.map(point=>point.value))),JSON.stringify([[.2,-.7,.3],[.9,-1.7,1]]));
 assert.equal(JSON.stringify(data),original);
});
