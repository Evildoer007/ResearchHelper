/* Measure actual SVG label geometry in a saved, offline research report. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const source=process.env.RESEARCHHELPER_LABEL_REPORT;
const output=process.env.RESEARCHHELPER_LABEL_OUTPUT;
assert(source&&output,'Set the report and isolated acceptance output');
await mkdir(output,{recursive:true});
const record={status:'running',report:source,cases:[],errors:[],externalRequests:0,model_calls:0,data_requests:0};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  const page=await browser.newPage({viewport:{width,height},reducedMotion:'reduce'});
  page.on('pageerror',error=>record.errors.push(error.message));
  await page.route(/^https?:/,route=>{record.externalRequests++;return route.abort()});
  await page.goto(pathToFileURL(source).href);
  await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(node=>node.querySelector('svg')));
  const original=await page.locator('#rh-publication-document').textContent();
  for(const media of ['screen','print']){
   await page.emulateMedia({media});await page.waitForTimeout(160);
   const geometry=await page.locator('main .chart').evaluateAll(nodes=>nodes.flatMap(node=>{
    const chart=echarts.getInstanceByDom(node),option=chart.getOption();
    if(!option.series.some(series=>series.type==='line'))return [];
    const edge=node.getBoundingClientRect();
    const labels=[...node.querySelectorAll('svg text')].map(text=>{
     const box=text.getBoundingClientRect();
     return {text:text.textContent,left:box.left,right:box.right,top:box.top,bottom:box.bottom};
    }).filter(box=>box.right>box.left&&box.bottom>box.top);
    const clipped=labels.filter(box=>box.left<edge.left-1||box.right>edge.right+1||box.top<edge.top-1||box.bottom>edge.bottom+1);
    const collisions=[];
    for(let a=0;a<labels.length;a++)for(let b=a+1;b<labels.length;b++){
     const x=Math.min(labels[a].right,labels[b].right)-Math.max(labels[a].left,labels[b].left);
     const y=Math.min(labels[a].bottom,labels[b].bottom)-Math.max(labels[a].top,labels[b].top);
     if(x>1&&y>1)collisions.push([labels[a].text,labels[b].text]);
    }
    return [{id:node.id,labels:labels.map(row=>row.text),clipped,collisions,
     values:option.series.filter(series=>series.type==='line').map(series=>series.data)}];
   }));
   assert.equal(geometry.length,2,'Both actual price and PE charts must be measured');
   assert.equal(await page.locator('#rh-publication-document').textContent(),original,'The same frozen publication data must survive print layout');
   record.cases.push({width,media,geometry});
  }
  await page.emulateMedia({media:'screen'});
  await page.screenshot({path:path.join(output,'report-'+width+'.png'),fullPage:true});
  await page.close();
 }
 const clear=record.cases.every(item=>item.geometry.every(chart=>!chart.collisions.length&&!chart.clipped.length));
 record.status=clear&&!record.errors.length&&!record.externalRequests?'passed':'failed';
}catch(error){record.status='failed';record.error=String(error)}
finally{
 await browser.close();
 await writeFile(path.join(output,'receipt.json'),JSON.stringify(record,null,2));
 console.log(JSON.stringify({status:record.status,layouts:record.cases.length,
  collisions:record.cases.flatMap(item=>item.geometry.flatMap(chart=>chart.collisions)).length,
  clipped:record.cases.flatMap(item=>item.geometry.flatMap(chart=>chart.clipped)).length,error:record.error,
  receipt:path.join(output,'receipt.json')}));
}
process.exitCode=record.status==='passed'?0:1;
