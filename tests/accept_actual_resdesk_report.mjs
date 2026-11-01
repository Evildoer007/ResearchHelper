/* Read the actual App-created report without further API calls. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const folder=process.env.RESEARCHHELPER_NATIVE_ACCEPTANCE;
assert(folder,'Explicit native acceptance folder required');
const report=folder+'/actual-frozen-report.html';
const session=JSON.parse(await readFile(folder+'/receipt.json','utf8'));
assert(session.paid_submission_started&&session.task_id,'Actual App submission required');
const browser=await chromium.launch({headless:true,channel:'chrome'});
const records=[],errors=[];let external=0;
try{
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  const page=await browser.newPage({viewport:{width,height},reducedMotion:'reduce'});
  page.on('pageerror',error=>errors.push(error.message));await page.route(/^https?:/,route=>{external++;return route.abort()});
  await page.goto(pathToFileURL(report).href);
  await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(node=>node.querySelector('svg')));
  const result=await page.evaluate(()=>{
   const doc=JSON.parse(document.getElementById('rh-publication-document').textContent);
   return {width:innerWidth,documentWidth:document.body.scrollWidth,paperWidth:document.querySelector('main').getBoundingClientRect().width,
    paperHeight:document.querySelector('main').getBoundingClientRect().height,bodyFont:getComputedStyle(document.querySelector('#summary p')).fontSize,
    coverage:doc.figure_coverage,observations:doc.dataset.rows.length,contentHash:doc.content_hash,
    charts:document.querySelectorAll('main .chart svg').length,folds:document.querySelectorAll('main details,main summary').length,
    mainText:document.querySelector('main').innerText,
    brokenAnchors:[...document.querySelectorAll('a[href^="#"]')].filter(link=>!document.getElementById(link.hash.slice(1))).map(link=>link.hash)};
  });
  assert.equal(result.documentWidth,width);assert.equal(result.bodyFont,'12px');assert.equal(result.folds,0);
  assert.equal(result.observations,22);assert.equal(result.charts,4);assert.deepEqual(result.brokenAnchors,[]);
  for(let index=1;index<=3;index++)assert(result.coverage['logic-'+index].figures>0);
  assert(!/接口报错|ResDesk|模型调用|保存失败|工作流编号|核验进度/.test(result.mainText));
  if(width>=1024)assert(Math.abs(result.paperWidth-210*96/25.4)<1);
  const {mainText,...safe}=result;records.push(safe);
  await page.screenshot({path:folder+'/actual-report-'+width+'.png',fullPage:true,animations:'disabled'});
  await page.close();
 }
 assert.equal(external,0);assert.deepEqual(errors,[]);
 await writeFile(folder+'/actual-report-browser.json',JSON.stringify({status:'passed',task_id:session.task_id,viewports:records,errors,external_requests:external,additional_model_calls:0,additional_data_requests:0},null,2));
 console.log(JSON.stringify({status:'passed',charts:4,observations:22,viewports:3,additional_model_calls:0,additional_data_requests:0}));
}finally{await browser.close()}
