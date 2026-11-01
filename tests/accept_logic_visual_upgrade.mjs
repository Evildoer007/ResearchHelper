/* Actual offline report UI review. No model, supplier or production Store. */
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const folder=path.join(root,'result/acceptance/logic-visual-upgrade');await mkdir(folder,{recursive:true});
const source=path.join(root,'result/reports/gui-maotai-20261029/贵州茅台研究报告-对客修订版.html');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const screens=[];
try{
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});const errors=[];let requests=0;
  page.on('pageerror',e=>errors.push(e.message));await page.route(/^https?:/,route=>{requests++;return route.abort()});
  await page.goto(pathToFileURL(source).href);
  await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(node=>node.querySelector('svg')));
  const metrics=await page.evaluate(()=>({
   width:innerWidth,paperWidth:document.querySelector('main').getBoundingClientRect().width,paperHeight:document.querySelector('main').getBoundingClientRect().height,
   documentWidth:document.body.scrollWidth,bodyFont:getComputedStyle(document.querySelector('#summary p')).fontSize,
   chartCounts:[...document.querySelectorAll('main section.strategy')].map(node=>node.querySelectorAll('.chart').length),
   schemes:RHVisuals.contexts().map(row=>row.scheme),
   coverage:JSON.parse(document.querySelector('#rh-publication-document').textContent).figure_coverage,
   sourceRows:JSON.parse(document.querySelector('#rh-publication-document').textContent).dataset.rows.length,
   mainText:document.querySelector('main').innerText,
   marketGroups:[...document.querySelectorAll('#logic-3 .chart')].map(node=>echarts.getInstanceByDom(node).group),
   missingLinks:[...document.querySelectorAll('a[href^="#"]')].filter(node=>!document.getElementById(node.hash.slice(1))).map(node=>node.hash)
  }));
  assert.deepEqual(metrics.chartCounts,[1,1,2]);assert.equal(metrics.sourceRows,20);assert.equal(metrics.documentWidth,width);
  assert.equal(metrics.bodyFont,'12px');assert.deepEqual(metrics.schemes,['dumbbell','lollipop','line','line']);
  assert.deepEqual(metrics.marketGroups,['logic-3-market','logic-3-market']);assert.deepEqual(metrics.missingLinks,[]);
  assert(!/原值|同尺度|研究草稿|核验|接口|ResDesk|亿元|验证条件/.test(metrics.mainText));
  if(width>=1024){assert(Math.abs(metrics.paperWidth-210*96/25.4)<1);assert(metrics.paperHeight<1125)}
  await page.screenshot({path:path.join(folder,'report-'+width+'.png'),fullPage:true});
  const first=page.locator('#logic-1 .chart');await first.focus();await page.keyboard.press('Enter');
  await page.locator('#report-inspector[open]').waitFor();await page.keyboard.press('Escape');
  assert(await first.evaluate(node=>document.activeElement===node));
  await first.scrollIntoViewIfNeeded();
  const point=await first.evaluate(node=>{const c=echarts.getInstanceByDom(node),spec=RHVisuals.contexts().find(row=>row.target===node.id),v=spec.data.pairs[0][1];const [x,y]=c.convertToPixel({seriesIndex:2},[v,0]);const r=node.getBoundingClientRect();return {x:r.left+x,y:r.top+y}});
  await page.mouse.click(point.x,point.y);await page.locator('#report-inspector[open]').waitFor();
  const selected=await page.locator('#report-inspector tr[data-chart-selected]').evaluateAll(rows=>rows.map(row=>row.dataset.observationId));
  assert.deepEqual(selected.sort(),['D1','D4']);await page.keyboard.press('Escape');
  await page.locator('main a[href="#complete-report-data"]').click();await page.locator('#report-inspector[open]').waitFor();
  assert.equal(await page.locator('#report-inspector tbody tr').count(),20);await page.keyboard.press('Escape');
  await page.emulateMedia({media:'print'});await page.waitForTimeout(120);
  assert.equal(await page.locator('.report-toc').evaluate(node=>getComputedStyle(node).display),'none');
  assert.equal(await page.locator('main .chart svg').count(),4);await page.emulateMedia({media:'screen'});
  assert.deepEqual(errors,[]);assert.equal(requests,0);const {mainText,...safe}=metrics;screens.push(safe);await page.close();
 }
 const receipt={status:'passed',viewports:screens,model_calls:0,data_requests:0,production_store_touched:false,headless:true};
 await writeFile(path.join(folder,'browser.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
}finally{await browser.close()}
