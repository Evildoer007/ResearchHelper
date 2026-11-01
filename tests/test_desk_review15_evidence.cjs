/* Retained-data edge cases only; no network or production store. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const window={};vm.runInNewContext(fs.readFileSync('products/app/frontend/ui/research-data.js','utf8'),{window});
const D=window.RHResearchData;
const group=values=>({name:'行情',object:'A',items:D.observations(values.map((raw_value,i)=>({id:String(i),raw_value,field:'close',unit:'元',period:`2026-10-${20+i}`})))});
test('underflow decimal remains exact in the table',()=>{const value='0.'+'0'.repeat(330)+'1';assert.equal(D.number(value),value)});
test('underflow never becomes a zero trend point',()=>{assert.equal(D.seriesData(group(['1e-999','1','2'])),null)});
test('zero with negative exponent remains eligible',()=>{assert.equal(D.seriesData(group(['0e-999','1','2'])).series[0].points[0].value,0)});
test('missing periods remain gaps and duplicate dates remain table only',()=>{const g=group([1,2,3]);g.items.push({...g.items[0],key:'other'});assert.equal(D.seriesData(g),null)});
test('ordinary rounding preserves carry and signs',()=>{assert.equal(D.number('999.99999'),'1,000.0000');assert.equal(D.number('-0.00001'),'-0.00001000')});
test('a deferred zero-width chart is retried when its container obtains width',async()=>{
 let observe,mode='chart',width=0,initialized=0;
 const canvas={style:{},setAttribute(){},removeAttribute(){},getBoundingClientRect:()=>({width})};
 const element={isConnected:true,dataset:{evidenceKey:'x',evidenceSeries:JSON.stringify({series:[{label:'x',unit:'元',points:[]}],periods:[]})},querySelector:s=>s==='.observation-chart'?canvas:{hidden:false},querySelectorAll:()=>[]};
 const root={hidden:false,contains:()=>true,style:{setProperty(){}},querySelector:()=>null,querySelectorAll:()=>[element],addEventListener(){},removeEventListener(){}};
 const window={RHResearchData:D,echarts:{init(){initialized++;return {on(){},setOption(){},resize(){},dispose(){}}}}};
 vm.runInNewContext(fs.readFileSync('products/app/frontend/ui/desk-interactions.js','utf8'),{window,document:{documentElement:{dataset:{}}},ResizeObserver:class{constructor(cb){observe=cb}observe(){}unobserve(){}disconnect(){}},MutationObserver:class{observe(){}disconnect(){}},requestAnimationFrame:()=>1,cancelAnimationFrame(){},getComputedStyle:()=>({getPropertyValue:()=>''})});
 const controller=window.RHDeskInteractions.create(root);controller.updated();element.dataset.presentation=mode;
 observe([{target:element}]);await Promise.resolve();assert.equal(initialized,0);
 width=500;observe([{target:element}]);await Promise.resolve();assert.equal(initialized,1);controller.destroy();
});
