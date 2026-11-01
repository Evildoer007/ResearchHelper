/* Real saved research, presentation-only checks. No API or model calls. */
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=process.env.RESEARCHHELPER_REPORT_SOURCE||path.join(root,'result/acceptance/installed-report-delivery-20261031/refined-layout/current-standard.html');
const folder=process.env.RESEARCHHELPER_REPORT_QA_OUTPUT||path.join(root,'result/acceptance/installed-report-delivery-20261031/refined-layout/browser');
await mkdir(folder,{recursive:true});
const raw=await readFile(source),sourceHash=createHash('sha256').update(raw).digest('hex');
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
const results=[];let browserClosed=false;
try{
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1});const errors=[];let externalRequests=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/^https?:/,route=>{externalRequests++;return route.abort()});
  await page.goto(pathToFileURL(source).href);
  await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(node=>node.querySelector('svg')));
  const metrics=await page.evaluate(()=>{
   const documentData=JSON.parse(document.getElementById('rh-publication-document').textContent);
   const first=document.querySelector('#logic-1 .chart'),chart=echarts.getInstanceByDom(first);
   const axis=chart.getModel().getComponent('xAxis').axis,format=chart.getOption().xAxis[0].axisLabel.formatter;
   return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,paperWidth:document.querySelector('main').getBoundingClientRect().width,
    rows:documentData.dataset.rows.length,contentHash:documentData.content_hash,
    charts:[...document.querySelectorAll('main .strategy')].map(node=>node.querySelectorAll('.chart').length),
    ticks:axis.scale.getTicks().map(tick=>format(tick.value)),halfTick:format(99.5),
    clippedLabels:[...first.querySelectorAll('svg text')].filter(node=>{const b=node.getBoundingClientRect(),r=first.getBoundingClientRect();return b.left<r.left-1||b.right>r.right+1||b.top<r.top-1||b.bottom>r.bottom+1}).map(node=>node.textContent),
    missingLinks:[...document.querySelectorAll('a[href^="#"]')].filter(link=>!document.getElementById(link.hash.slice(1))).map(link=>link.hash),
    windows:documentData.blocks.filter(row=>row.kind==='chart'&&['close','ths_pe_ttm_stock'].includes(row.spec.field)).map(row=>({field:row.spec.field,points:row.spec.values.length,context:row.spec.plot_context.context_data_refs.length,refs:row.spec.argument_data_refs})),
    visibleText:document.querySelector('main').innerText};
  });
  assert.equal(metrics.scrollWidth,width);assert.deepEqual(metrics.charts,[1,1,2]);assert.equal(metrics.rows,40);
  assert.deepEqual(metrics.windows.map(row=>[row.field,row.points,row.context]),[['close',17,15],['ths_pe_ttm_stock',17,15]]);
  for(const window of metrics.windows)assert.deepEqual([...window.refs].sort(),['D23','D24','D40','D7']);
  assert.equal(new Set(metrics.ticks).size,metrics.ticks.length);assert.equal(metrics.halfTick,'99.5');
  assert.deepEqual(metrics.clippedLabels,[]);assert.deepEqual(metrics.missingLinks,[]);assert(!/ResDesk|模型调用|配置在线研究/.test(metrics.visibleText));
  if(width>=1024)assert(Math.abs(metrics.paperWidth-210*96/25.4)<1);
  await page.screenshot({path:path.join(folder,'report-'+width+'.png'),fullPage:true});
  const chart=page.locator('#logic-1 .chart');await chart.focus();await page.keyboard.press('Enter');
  await page.locator('#report-inspector[open]').waitFor();await page.keyboard.press('Escape');
  assert(await chart.evaluate(node=>document.activeElement===node));
  await chart.scrollIntoViewIfNeeded();
  const point=await chart.evaluate(node=>{const c=echarts.getInstanceByDom(node),spec=RHVisuals.contexts().find(row=>row.target===node.id);const [x,y]=c.convertToPixel({seriesIndex:2},[spec.data.pairs[0][1],0]);const r=node.getBoundingClientRect();return {x:r.left+x,y:r.top+y}});
  await page.mouse.click(point.x,point.y);await page.locator('#report-inspector[open]').waitFor();
  assert.deepEqual((await page.locator('#report-inspector tr[data-chart-selected]').evaluateAll(rows=>rows.map(row=>row.dataset.observationId))).sort(),['D1','D4']);
  await page.keyboard.press('Escape');
  const selectedWindows=[];
  for(const field of ['close','ths_pe_ttm_stock']){
   const target=await page.evaluate(field=>JSON.parse(document.getElementById('rh-publication-document').textContent).blocks.find(row=>row.kind==='chart'&&row.spec.field===field).spec.target,field);
   const plot=page.locator('[id="'+target+'"]');await plot.scrollIntoViewIfNeeded();
   const middle=await plot.evaluate(node=>{
    const c=echarts.getInstanceByDom(node),spec=JSON.parse(document.getElementById('rh-publication-document').textContent).visual_sources[node.id];
    const series=c.getOption().series.findIndex(row=>row.type==='line'&&row.data.length===17);
    if(series<0)throw Error('Observed window not rendered as a complete line');
    const index=8,[x,y]=c.convertToPixel({seriesIndex:series},[index,spec.values[index]]),r=node.getBoundingClientRect();
    return {x:r.left+x,y:r.top+y,refs:spec.point_refs[index]};
   });
   await page.mouse.click(middle.x,middle.y);await page.locator('#report-inspector[open]').waitFor();
   const refs=await page.locator('#report-inspector tr[data-chart-selected]').evaluateAll(rows=>rows.map(row=>row.dataset.observationId));
   assert.deepEqual(refs,middle.refs);selectedWindows.push({field,selected:refs});await page.keyboard.press('Escape');
  }
  const originalHeights=await page.locator('main .chart').evaluateAll(nodes=>nodes.map(node=>node.style.getPropertyValue('height')));
  const originalTables=await page.locator('main table').evaluateAll(nodes=>nodes.map(node=>node.outerHTML));
  await page.emulateMedia({media:'print'});await page.evaluate(()=>window.dispatchEvent(new Event('beforeprint')));
  assert.equal(await page.locator('.report-toc').evaluate(node=>getComputedStyle(node).display),'none');
  assert.equal(await page.locator('main .chart svg').count(),4);
  const printLayout=await page.evaluate(()=>({width:document.querySelector('main').getBoundingClientRect().width,
   heights:[...document.querySelectorAll('main .chart')].map(node=>({box:node.clientHeight,svg:Number(node.querySelector('svg').getAttribute('height'))})),
   labels:[...document.querySelectorAll('#logic-1 .chart svg text')].map(node=>node.textContent)}));
  assert(Math.abs(printLayout.width-196*96/25.4)<1);for(const row of printLayout.heights)assert.equal(row.box,row.svg);
  for(const label of ['营业收入','归母净利润'])assert(printLayout.labels.includes(label),'missing printed category: '+label);
  await page.emulateMedia({media:'screen'});await page.evaluate(()=>window.dispatchEvent(new Event('afterprint')));
  assert.deepEqual(await page.locator('main .chart').evaluateAll(nodes=>nodes.map(node=>node.style.getPropertyValue('height'))),originalHeights);
  assert.equal(await page.locator('.rh-print-table-parts,.rh-print-table-original').count(),0);
  assert.deepEqual(await page.locator('main table').evaluateAll(nodes=>nodes.map(node=>node.outerHTML)),originalTables);
  assert.deepEqual(errors,[]);assert.equal(externalRequests,0);
  const {visibleText,...safe}=metrics;results.push({...safe,selectedWindows,errors,externalRequests});await page.close();
 }
}finally{await browser.close();browserClosed=true}
assert.equal(createHash('sha256').update(await readFile(source)).digest('hex'),sourceHash);
const receipt={status:'passed',source,source_sha256:sourceHash,headless:true,browser_closed:browserClosed,viewports:results,model_calls:0,data_requests:0};
await writeFile(path.join(folder,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify(receipt));
