/* App workflow using disposable authenticated data and real binary downloads. */
import assert from 'node:assert/strict';import {createRequire} from 'node:module';import {readFile,writeFile} from 'node:fs/promises';import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL),output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
const record={status:'failed',checks:[],errors:[],externalRequests:[],headless:true,model_calls:0,data_requests:0};let browser;
try{
 browser=await chromium.launch({channel:'chrome',headless:true});const ctx=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true});
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin===base.origin||['data:','blob:'].includes(u.protocol))return route.continue();record.externalRequests.push(u.href);return route.abort()});
 const page=await ctx.newPage();page.on('pageerror',e=>record.errors.push(e.message));await page.goto(base.href);
 await page.locator('#login-account').fill('admin');await page.locator('#login-password').fill('66666666');await page.locator('#login-submit').click();await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 await page.evaluate(async()=>{const task=await api('/api/cases',{id:'etf'});tasks.unshift(task);setTask(task)});
 await page.locator('#chat .rh-process [data-action=start]').click();await page.waitForFunction(()=>selected?.delivery,null,{timeout:30000});
 const frozen=await page.evaluate(()=>JSON.stringify(selected));const task=await page.evaluate(()=>structuredClone(selected));
 const boot=await (await ctx.request.get(new URL('/api/bootstrap',base).href)).json();
 await page.evaluate(()=>{switchMode('desk');view(3)});const report=page.frameLocator('#report-frame');await report.locator('main').waitFor();
 assert.equal(await report.locator('meta[name=rh-report-profile]').getAttribute('content'),'standard');
 await page.locator('[data-report-profile=detailed]').click();await report.locator('[data-logic-detail]').first().waitFor();
 assert.equal(await report.locator('meta[name=rh-report-profile]').getAttribute('content'),'detailed');
 assert.equal(await page.evaluate(()=>JSON.stringify(selected)),frozen);record.checks.push('standard/detailed use the same frozen task');
 for(const format of ['html','pdf','docx','xlsx','csv']){
  await page.locator('#report-format').selectOption(format,{force:true});const event=page.waitForEvent('download');await page.locator('[data-action=export]').first().click();
  const download=await event;assert.equal(await download.failure(),null);assert(download.suggestedFilename().endsWith('.'+format));const file=path.join(output,'app-report.'+format);await download.saveAs(file);const bytes=await readFile(file);
  if(format==='pdf')assert(bytes.subarray(0,5).toString()==='%PDF-');if(['docx','xlsx'].includes(format))assert(bytes.subarray(0,2).toString()==='PK');if(format==='html')assert(bytes.toString().includes('数据依据'));
  record.checks.push(format+' exported through the real App route');
 }
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(output,'app-'+width+'.png'),fullPage:false});
 }
 const denied=await ctx.request.post(new URL('/api/export',base).href,{headers:{'X-ResearchHelper-Token':boot.token},data:{task_id:task.id,format:'exe'}});assert.equal(denied.status(),400);
 const sales=await browser.newContext();const status=await (await sales.request.get(new URL('/api/auth/status',base).href)).json();
 assert.equal((await sales.request.post(new URL('/api/auth/login',base).href,{headers:{'X-ResearchHelper-Token':status.token},data:{account:'sales',password:'88888888',remember:false}})).status(),200);
 assert.equal((await sales.request.post(new URL('/api/export',base).href,{headers:{'X-ResearchHelper-Token':status.token},data:{task_id:task.id,format:'pdf'}})).status(),403);
 record.checks.push('unsupported formats and cross-account exports denied');await sales.close();
 const final=await (await ctx.request.get(new URL('/api/tasks/'+task.id,base).href)).json();assert.equal(final.ledger.used.model_calls,0);assert.equal(final.ledger.used.data_requests,0);
 assert.deepEqual(record.externalRequests,[]);assert.deepEqual(record.errors,[]);record.status='passed';
}catch(error){record.error=error.stack;process.exitCode=1}
finally{if(browser)await browser.close();record.browserClosed=true;await writeFile(path.join(output,'publication-browser.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,receipt:path.join(output,'publication-browser.json'),error:record.error}));}
