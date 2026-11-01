/* A paired comparison must expose every real category, including print panels. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},box={window,addEventListener(){},Intl,Map};
for(const name of ['visual-catalog-data','visual-library'])vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend',name+'.js'),'utf8'),box);
test('compact endpoint diagrams keep all labels and every original pair',()=>{
 const data={labels:['CPI:当月同比','CPI:食品:当月同比','CPI:消费品:当月同比'],periods:['2026-01-31','2026-09-30'],pairs:[[.2,.9],[-.7,-1.7],[.3,1]],unit:'%'};
 const original=JSON.stringify(data),option=window.RHVisuals.option('dumbbell',data,'research',{width:310,height:180});
 assert.equal(option.yAxis.axisLabel.interval,0);assert.equal(option.yAxis.axisLabel.hideOverlap,false);
 assert.equal(JSON.stringify(option.yAxis.data),JSON.stringify(data.labels));
 assert.equal(JSON.stringify(option.series[0].data),JSON.stringify(data.pairs.map((pair,i)=>[i,...pair])));
 assert.equal(JSON.stringify(data),original);
});
