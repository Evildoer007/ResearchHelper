/** Exact reference plus two real frozen research inputs, fully offline. */
import fs from'node:fs/promises';import path from'node:path';import{createRequire}from'node:module';import{pathToFileURL}from'node:url';import assert from'node:assert/strict';
const{chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root='/Users/haoranxu/Desktop/ResearchHelper',reports=path.join(root,'result/reports/strategy-template-20261028'),out=path.join(root,'result/acceptance/strategy-template-20261028');await fs.mkdir(out,{recursive:true});
const record={status:'failed',checks:[],errors:[],external_requests:[],headless:true,model_calls:0,data_requests:0,browser_closed:false};const browser=await chromium.launch({channel:'chrome',headless:true});
try{for(const name of['broker-template','financial-standard','financial-detailed','macro-standard','macro-detailed']){
 const context=await browser.newContext({reducedMotion:'reduce'});await context.route('**/*',route=>{if(/^(file:|data:|about:)/.test(route.request().url()))return route.continue();record.external_requests.push(route.request().url());return route.abort()});
 const page=await context.newPage();page.on('pageerror',error=>record.errors.push({name,message:error.message}));await page.goto(pathToFileURL(path.join(reports,name+'.html')).href);
 await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(n=>n.querySelector('svg')));
 for(const[width,height]of[[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(200);
  const info=await page.evaluate(()=>({template:document.body.dataset.reportTemplate,overflow:document.documentElement.scrollWidth>innerWidth+1,paper_width:document.querySelector('main').getBoundingClientRect().width,paper_height:document.querySelector('main').getBoundingClientRect().height,charts:document.querySelectorAll('main .chart').length,svg:document.querySelectorAll('main .chart svg').length,details:document.querySelectorAll('[data-logic-detail]').length,groups:[...document.querySelectorAll('[data-columns]')].map(n=>({columns:n.dataset.columns,width:n.getBoundingClientRect().width,first:n.firstElementChild.getBoundingClientRect().width})),placeholder_slots:document.querySelectorAll('[data-paper-slot]').length}));
  assert.equal(info.template,'strategy-paper');assert(!info.overflow,name+' '+width+' overflow');assert.equal(info.charts,info.svg);assert.equal(info.placeholder_slots,0);
  if(width>=1024)assert(Math.abs(info.paper_width-793.7)<2,name+' A4 width');
  if(name==='financial-standard'){assert.equal(info.charts,6);if(width===1440)assert(info.groups.every(n=>n.first<n.width*.6),'actual paired panels required')}
  record.checks.push({name,width,...info});await page.screenshot({path:path.join(out,name+'-'+width+'.png'),fullPage:width===1440});
 }
 if(name==='financial-standard'){
  const source=page.frameLocator;await page.setViewportSize({width:1440,height:900});const link=page.locator('main figure figcaption a').first();await link.click();await page.locator('#report-inspector[open]').waitFor();await page.keyboard.press('Escape');
  await page.emulateMedia({media:'print'});await page.evaluate(()=>dispatchEvent(new Event('beforeprint')));await page.waitForTimeout(200);assert(await page.locator('.report-toc').isHidden());assert(await page.locator('#report-inspector').isHidden());
  await page.pdf({path:path.join(out,'financial-print.pdf'),format:'A4',printBackground:true});
 }
 await context.close();
}assert.equal(record.errors.length,0,JSON.stringify(record.errors));assert.equal(record.external_requests.length,0);record.status='passed';}catch(error){record.error=error.stack;process.exitCode=1}finally{await browser.close();record.browser_closed=true;await fs.writeFile(path.join(out,'browser.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error,receipt:path.join(out,'browser.json')}))}
