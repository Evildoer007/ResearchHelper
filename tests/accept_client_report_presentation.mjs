/* Offline visual and provenance review of the saved true-data client revision. */
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const folder=path.join(root,'result/reports/gui-maotai-20261029');
const revision=JSON.parse(await readFile(path.join(folder,'client-revision.json'),'utf8'));
const content=await readFile(path.join(folder,'client-content.json'),'utf8');
assert.equal(JSON.parse(content).content_hash,revision.revision_content_hash);
const presentationContentHash=createHash('sha256').update(content).digest('hex');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const results=[];
try{
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
  const errors=[];let externalRequests=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^https?:/,route=>{externalRequests++;return route.abort()});
  await page.goto(pathToFileURL(revision.files.standard.path).href);
  await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(node=>!!node.querySelector('svg')));
  const layout=await page.evaluate(()=>({
   paperWidth:document.querySelector('main').getBoundingClientRect().width,
   paperHeight:document.querySelector('main').getBoundingClientRect().height,
   bodyWidth:document.body.scrollWidth,viewport:innerWidth,
   charts:document.querySelectorAll('main .chart svg').length,
   mainTables:document.querySelectorAll('main table').length,
   bodyFont:getComputedStyle(document.querySelector('#summary p')).fontSize,
   headingFont:getComputedStyle(document.querySelector('#logic-1 h2')).fontSize,
   mainText:document.querySelector('main').innerText,
   missingTargets:[...document.querySelectorAll('a[href^="#"]')].filter(link=>!document.getElementById(link.hash.slice(1))).map(link=>link.hash),
   document:JSON.parse(document.querySelector('#rh-publication-document').textContent)
  }));
  assert.equal(layout.bodyFont,'12px');assert.equal(layout.mainTables,0);assert.equal(layout.charts,3);
  assert.equal(layout.bodyWidth,width);assert.deepEqual(layout.missingTargets,[]);
  assert.equal(layout.document.dataset.rows.length,20);
  assert.equal(layout.document.content_hash,presentationContentHash);
  assert(!/原值|同尺度|研究草稿|核验|接口|ResDesk|亿元|验证条件/.test(layout.mainText));
  if(width>=1024){assert(Math.abs(layout.paperWidth-210*96/25.4)<1);assert(layout.paperHeight<=297*96/25.4+2)}
  await page.screenshot({path:path.join(folder,'对客修订版-'+width+'.png'),fullPage:true});
  const firstLogic=page.locator('main h2 a').first();await firstLogic.click();
  await page.waitForFunction(()=>document.querySelector('#report-inspector').open);
  const logicRows=await page.locator('#report-inspector tbody tr').count();assert(logicRows>=4);
  await page.keyboard.press('Escape');assert(await firstLogic.evaluate(node=>document.activeElement===node));
  await page.locator('main a[href="#complete-report-data"]').click();
  await page.waitForFunction(()=>document.querySelector('#report-inspector').open);
  const rows=await page.locator('#report-inspector tbody tr').count();assert.equal(rows,20);
  await page.keyboard.press('Escape');assert.deepEqual(errors,[]);assert.equal(externalRequests,0);
  const {document,mainText,...metrics}=layout;
  results.push({width,height,layout:metrics,logicRows,completeDataRows:rows,errors,externalRequests});
  await page.close();
 }
 const receipt={status:'passed',modelCalls:0,dataRequests:0,productionStoreTouched:false,viewports:results};
 await writeFile(path.join(folder,'client-browser-review.json'),JSON.stringify(receipt,null,2));
 console.log(JSON.stringify(receipt));
}finally{await browser.close()}
