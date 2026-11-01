/* Isolated, explicitly synthetic knowledge presentation; no research dispatch. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(import.meta.dirname,'..'),frontend=process.env.RESEARCHHELPER_UI_FRONTEND||path.join(root,'products/app/frontend');
const output=process.env.RESEARCHHELPER_UI_OUTPUT||path.join(root,'result/acceptance/draft-knowledge/browser');await mkdir(output,{recursive:true});
const fixture=JSON.parse(await readFile(path.join(root,'result/acceptance/draft-knowledge/fixture.json'),'utf8')),task=fixture.task;
const receipt={status:'running',checks:[],errors:[],external_requests:[],screens:[],model_calls:0,data_requests:0,synthetic_fixture:true,production_store_touched:false,foreground_opened:false};
const check=(name,value)=>{assert(value,name);receipt.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',error=>receipt.errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());if(url.origin!=='http://127.0.0.1:38180'){receipt.external_requests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'ui-fixture',execution:{mode:'offline'},role:'admin',authentication:false});
  if(url.pathname.includes('/report'))return route.fulfill({contentType:'text/html',body:url.searchParams.get('profile')==='detailed'?fixture.detailed:fixture.standard});
  if(url.pathname.startsWith('/api/'))return json({});
  try{const file=path.join(frontend,url.pathname==='/'?'index.html':url.pathname);return route.fulfill({body:await readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'})}catch{return route.fulfill({status:404,body:''})}
 });
 await page.goto('http://127.0.0.1:38180/');await page.locator('#chat-input').waitFor();
 await page.evaluate(task=>{selected=task;stage=2;artifactsOpen=true;render()},task);
 await page.locator('#view-2').click();
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme},theme);
   const theory=page.locator('#paper-logic-0 .paper-theory');
   check(width+' '+theme+' only the cited logic has theory',await page.locator('.paper-theory').count()===1);
   if(await theory.getAttribute('open')===null){await theory.locator(':scope > summary').focus();await page.keyboard.press('Enter')}
   const entry=theory.locator('.knowledge-item');if(await entry.getAttribute('open')===null)await entry.locator(':scope > summary').click();
   check(width+' '+theme+' actual conditions are visible',(await entry.innerText()).includes(fixture.entry.conditions));
   check(width+' '+theme+' actual counterexample is visible',(await entry.innerText()).includes(fixture.entry.counterexample));
   check(width+' '+theme+' reference warnings stay outside theory',!(await theory.innerText()).includes('有一项引用待补充')&&(await page.locator('#paper-logic-2').innerText()).includes('有一项引用待补充'));
   check(width+' '+theme+' no repeated knowledge instructions',await theory.locator('.knowledge-purpose,.knowledge-selection,.knowledge-coverage').count()===0);
   check(width+' '+theme+' no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const screen='knowledge-'+width+'-'+theme+'.png';await page.screenshot({path:path.join(output,screen),animations:'disabled'});receipt.screens.push(screen);
  }
 }
 await page.setViewportSize({width:1440,height:900});await page.locator('#view-3').click();
 const report=page.frameLocator('#report-frame');await report.locator('#logic-1').waitFor();
 check('client paper does not inline internal theory',!(await report.locator('main').innerText()).includes(fixture.entry.title));
 await report.locator('#logic-1 h2 a').click();await report.locator('#report-inspector[open]').waitFor();
 check('client click detail resolves actual theory',(await report.locator('#report-inspector').innerText()).includes(fixture.entry.content));
 await page.keyboard.press('Escape');
 await page.locator('[data-report-profile=detailed]').click();await report.locator('[data-logic-detail=logic-1]').waitFor();
 check('internal report expands the same theory',(await report.locator('[data-logic-detail=logic-1]').innerText()).includes(fixture.entry.content));
 check('inspection never changes the saved theory references',await page.evaluate(refs=>JSON.stringify(selected.draft_report.logics.map(row=>row.knowledge_refs))===JSON.stringify(refs),task.draft_report.logics.map(row=>row.knowledge_refs)));
 check('no script errors',receipt.errors.length===0);check('no external requests',receipt.external_requests.length===0);
 receipt.status='passed';
}catch(error){receipt.status='failed';receipt.error=String(error);throw error}
finally{await browser.close();receipt.browser_closed=true;await writeFile(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({status:receipt.status,checks:receipt.checks.length,output,error:receipt.error}))}
