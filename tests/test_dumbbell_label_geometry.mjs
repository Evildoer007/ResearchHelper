/* Verify actual SVG labels against their own category rows, including A4 panels. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const root=process.cwd(),output=path.join(root,'result/acceptance/report-corrections-20261101');await mkdir(output,{recursive:true});
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({headless:true,channel:'chrome'}),cases=[];
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.setContent('<div id="plot"></div>');
 for(const file of ['echarts.min.js','visual-catalog-data.js','visual-library.js'])await page.addScriptTag({path:path.join(root,'products/app/frontend',file)});
 for(const [width,height] of [[354,131],[280,160],[520,220]]){
  for(const [name,data] of Object.entries({
   indexed:{labels:['营业收入','归母净利润'],pairs:[[100,98.79399649787769],[100,95.46774518284191]],periods:['2024','2025'],unit:''},
   signed:{labels:['利润','现金流'],pairs:[[-1.25,.875],[3.4,3.4]],periods:['前期','本期'],unit:'%'},
   amount:{labels:['营业收入','经营现金流'],pairs:[[1250000000,1320000000],[400000000,350000000]],periods:['前期','本期'],unit:'元'}
  })){
   const result=await page.evaluate(({width,height,data})=>{
    const node=document.getElementById('plot');node.style.cssText='width:'+width+'px;height:'+height+'px';
    const before=JSON.stringify(data),chart=echarts.init(node,null,{renderer:'svg'});
    const option=RHVisuals.option('dumbbell',data,'research',{width,height});chart.setOption(option,true);
    const texts=[...node.querySelectorAll('svg text')].map(el=>{
     const box=el.getBBox(),m=el.getCTM();return {text:el.textContent,x:box.x*m.a+box.y*m.c+m.e,y:box.x*m.b+box.y*m.d+m.f,width:box.width,height:box.height};
    });
    const checks=[];
    for(let row=0;row<data.pairs.length;row++){
     const y=chart.convertToPixel({xAxisIndex:0,yAxisIndex:0},[data.pairs[row][0],row])[1];
     const category=texts.find(item=>item.text===data.labels[row]);
     for(let period=0;period<2;period++){
      if(period===0&&data.pairs[row][0]===data.pairs[row][1])continue;
      const expected=option.series[period+1].label.formatter({value:[data.pairs[row][period],row]});
      const own=texts.filter(item=>item.text===expected&&Math.abs(item.y+item.height/2-y)<1.1);
      checks.push({row,period,expected,matches:own.length,on_own_row:own.length===1,clear_of_category:own.length===1&&own[0].x>=category.x+category.width+4});
     }
    }
    const series=option.series.slice(1).map(s=>s.data.map(item=>item.value));
    chart.dispose();return {checks,series,input_unchanged:JSON.stringify(data)===before};
   },{width,height,data});
   assert(result.input_unchanged,name+' source data changed');
   assert.deepEqual(result.series,[0,1].map(period=>data.pairs.map((pair,row)=>[pair[period],row])));
   assert(result.checks.every(row=>row.on_own_row&&row.clear_of_category),JSON.stringify({name,width,height,...result}));
   cases.push({name,width,height,...result});
  }
 }
 assert.equal(errors.length,0);await writeFile(path.join(output,'endpoint-layout.json'),JSON.stringify({status:'passed',cases,model_calls:0,data_requests:0,headless:true},null,2));
 console.log(JSON.stringify({status:'passed',cases:cases.length,labels:cases.reduce((sum,row)=>sum+row.checks.length,0)}));
}finally{await browser.close()}
