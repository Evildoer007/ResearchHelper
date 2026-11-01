/** Named report definitions rendered from cached research, without external calls. */
import fs from'node:fs/promises';import path from'node:path';import{createRequire}from'node:module';import{pathToFileURL}from'node:url';import assert from'node:assert/strict';
const{chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root='/Users/haoranxu/Desktop/ResearchHelper',reports=path.join(root,'result/reports/strategy-template-20261028'),out=path.join(root,'result/acceptance/named-report-templates');await fs.mkdir(out,{recursive:true});
const record={status:'failed',checks:[],errors:[],external_requests:[],model_calls:0,data_requests:0};const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 for(const name of['broker-standard','broker-detailed','financial-standard','financial-detailed','macro-standard','macro-detailed']){
  const context=await browser.newContext({reducedMotion:'reduce'});await context.route('**/*',route=>{if(/^(file:|data:|about:)/.test(route.request().url()))return route.continue();record.external_requests.push(route.request().url());return route.abort()});
  const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));await page.goto(pathToFileURL(path.join(reports,name+'.html')).href);await page.waitForFunction(()=>[...document.querySelectorAll('main .chart')].every(n=>n.querySelector('svg')));
  for(const[width,height]of[[1440,900],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(120);
   const info=await page.evaluate(()=>({template:document.body.dataset.reportTemplate,output:document.body.dataset.reportOutput,variant:document.body.dataset.reportVariant,overflow:document.documentElement.scrollWidth>innerWidth+1,width:document.querySelector('main').getBoundingClientRect().width,mains:document.querySelectorAll('main').length,charts:document.querySelectorAll('main .chart').length,svg:document.querySelectorAll('main .chart svg').length,details:[...document.querySelectorAll('[data-logic-detail]')].map(n=>({height:n.getBoundingClientRect().height,text:n.textContent.length})),slots:document.querySelectorAll('[data-paper-slot]').length}));
   assert.equal(info.template,'report-'+name.split('-').at(-1));assert.equal(info.output,'report');assert.equal(info.variant,name.split('-').at(-1));assert(!info.overflow);assert.equal(info.mains,1);assert.equal(info.slots,0);assert.equal(info.charts,info.svg);
   if(width>=1024)assert(Math.abs(info.width-793.7)<2);if(name.endsWith('detailed'))assert(info.details.length>0&&info.details.every(n=>n.height>0&&n.text>0));else assert.equal(info.details.length,0);
   record.checks.push({name,width,...info});if(width===1440)await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
  }
  await context.close();
 }
 assert.deepEqual(record.errors,[]);assert.deepEqual(record.external_requests,[]);record.status='passed';
}catch(error){record.error=error.stack;process.exitCode=1}finally{await browser.close();record.browser_closed=true;await fs.writeFile(path.join(out,'browser.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error,receipt:path.join(out,'browser.json')}))}
