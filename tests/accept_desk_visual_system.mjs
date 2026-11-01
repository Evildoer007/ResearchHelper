/* Retained real research, synthetic edge states, local-only UI acceptance. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(import.meta.dirname,'..'),out=path.join(root,'result/acceptance/desk-visual-system');
const capture=process.env.RESEARCHHELPER_UI_CAPTURE||'after',shots=path.join(out,capture);await mkdir(shots,{recursive:true});
const task=JSON.parse(await readFile(path.join(out,'retained-task.json'),'utf8'));
const report=task.report_html;
const record={status:'running',checks:[],errors:[],external_requests:[],screens:[],model_calls:0,data_requests:0,production_store_touched:false};
const check=(label,condition)=>{assert(condition,label);record.checks.push(label)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',error=>record.errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin!=='http://127.0.0.1:38180'){record.external_requests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'ui-fixture',execution:{mode:'offline'},role:'admin',authentication:false});
  if(url.pathname.includes('/report'))return route.fulfill({contentType:'text/html',body:report});
  if(url.pathname.startsWith('/api/'))return json({});
  try{
   const file=path.join(root,'products/app/frontend',url.pathname==='/'?'index.html':url.pathname);
   return route.fulfill({body:await readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});
  }catch{return route.fulfill({status:404,body:''})}
 });
 await page.goto('http://127.0.0.1:38180/');await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 await page.evaluate(value=>{tasks=[value];selected=value;artifactsOpen=true;stage=2;render()},task);
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});
  for(let stage=0;stage<5;stage++){
   await page.locator('#view-'+stage).click();
   await page.waitForTimeout(100);
   check(width+' stage '+stage+' reachable',await page.locator('#view-'+stage).getAttribute('aria-selected')==='true');
   check(width+' stage '+stage+' no horizontal page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const name=width+'-'+stage+'.png';await page.screenshot({path:path.join(shots,name),animations:'disabled'});record.screens.push(name);
  }
 }

 if(capture==='after'){
  await page.setViewportSize({width:1440,height:900});
  await page.locator('#view-0').click();
  check('overview presents four actual output links',await page.locator('.desk-pipeline button').count()===4);
  for(const stage of [1,2,3,4]){
   await page.locator('#view-0').click();await page.locator('.desk-pipeline [data-stage="'+stage+'"]').click();
   check('overview output link reaches view '+stage,await page.locator('#view-'+stage).getAttribute('aria-selected')==='true');
  }
  await page.locator('#view-1').click();
  check('all actual observations retained',await page.locator('.desk-sources [data-observation]').count()===22);
  check('data source table starts with original catalog collapsed',await page.locator('.source-library').getAttribute('open')===null);
  await page.locator('.source-library>summary').click();
  check('six originals have identifiable metrics and coverage',await page.locator('.source-library-row').count()===task.source_records.length&&await page.locator('.source-library-row time').count()===new Set(task.draft_report.facts.map(fact=>fact.artifact_id)).size);
  await page.screenshot({path:path.join(shots,'source-catalog.png'),animations:'disabled'});record.screens.push('source-catalog.png');
  const observation=page.locator('[data-draft-source="D7"]').first();await observation.click();
  await page.locator('#dialog[open]').waitFor();
  check('observation opens actual retained API record',await page.locator('#dialog').innerText().then(text=>text.includes('close')&&text.includes('1331.88')));
  await page.screenshot({path:path.join(shots,'observation-reader.png'),animations:'disabled'});record.screens.push('observation-reader.png');
  await page.keyboard.press('Escape');check('source reader restores observation focus',await observation.evaluate(node=>document.activeElement===node));
  const arithmetic=page.locator('[data-draft-calculation="A1"]').first();await arithmetic.click();
  await page.locator('#dialog[open]').waitFor();check('calculation exposes formula and exact inputs',await page.locator('#dialog').innerText().then(text=>text.includes('170899152276.34')&&text.includes('168838102514.79')));
  await page.keyboard.press('Escape');check('calculation closes to its control',await arithmetic.evaluate(node=>document.activeElement===node));
  await page.locator('#view-2').click();
  check('all three actual logics retain narrative and data basis',await page.locator('.paper-layout').count()===3&&await page.locator('.paper-basis').count()===3);
  check('all three actual limitations stay visible',await page.locator('.paper-validation').count()===3);
  await page.locator('[data-paper-target="paper-logic-2"]').click();check('argument navigation focuses its actual content',await page.locator('#paper-logic-2').evaluate(node=>document.activeElement===node));
  for(const [width,height] of [[1440,900],[390,844]]){
   await page.setViewportSize({width,height});
   if(await page.locator('#assistant-panel').getAttribute('aria-hidden')==='false')await page.locator('[data-action=assistant-close]').click();
   check(width+' reading has no fixed input obstruction',await page.locator('#assistant-panel').getAttribute('aria-hidden')==='true'&&await page.locator('[data-action=assistant-toggle]').isVisible());
   const selectedId=await page.evaluate(()=>selected.id);
   await page.locator('[data-action=assistant-toggle]').click();
   check(width+' task assistant is usable when opened',await page.locator('#chat-input').isVisible()&&await page.locator('#chat-input').evaluate(node=>document.activeElement===node));
   check(width+' assistant close matches source-reader icon family',await page.locator('[data-action=assistant-close]').evaluate(el=>{const b=el.getBoundingClientRect(),css=getComputedStyle(el);return b.width>=40&&b.height>=40&&css.borderTopWidth==='0px'&&css.borderRadius==='8px'&&!!el.querySelector('svg')}));
   const colors=await page.locator('#assistant-panel').evaluate(el=>({actual:getComputedStyle(el).backgroundColor,surface:getComputedStyle(el).getPropertyValue('--surface'),token:getComputedStyle(el).getPropertyValue('--color-surface')}));
   record.assistantColors=record.assistantColors||[];record.assistantColors.push({width,...colors});
   await page.screenshot({path:path.join(shots,width+'-assistant-open.png'),animations:'disabled'});

   check(width+' assistant keeps the same research',await page.evaluate(id=>selected.id===id,selectedId));
   await page.keyboard.press('Escape');check(width+' assistant closes and returns focus',await page.locator('#assistant-panel').getAttribute('aria-hidden')==='true'&&await page.locator('[data-action=assistant-toggle]').evaluate(node=>document.activeElement===node));
   await page.evaluate(()=>{document.documentElement.dataset.theme='dark';document.documentElement.style.colorScheme='dark'});
   for(const stage of [1,2]){
    await page.locator('#view-'+stage).click();check(width+' dark view '+stage+' no overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const name=width+'-'+stage+'-dark.png';await page.screenshot({path:path.join(shots,name),animations:'disabled'});record.screens.push(name);
   }
   await page.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.style.colorScheme='light'});
   if(!(await page.locator('button[data-mode="chat"]').isVisible()))await page.locator('.web-control[data-action="tasks"]').click();
   await page.locator('button[data-mode="chat"]').click();check(width+' ResChat still opens the full composer',await page.locator('#chat-input').isVisible());
   if(!(await page.locator('button[data-mode="desk"]').isVisible()))await page.locator('.web-control[data-action="tasks"]').click();
   await page.locator('button[data-mode="desk"]').click();
  }
  await page.setViewportSize({width:1440,height:900});
  await page.locator('#view-3').click();await page.frameLocator('#report-frame').locator('h1').waitFor();
  const [standard,detailed,exported]=await Promise.all([page.locator('[data-report-profile=standard]').boundingBox(),page.locator('[data-report-profile=detailed]').boundingBox(),page.locator('[data-action=export]').boundingBox()]);
  check('report controls use consistent accessible geometry',standard.height>=36&&detailed.height===standard.height&&exported.height===44);
  await page.locator('[data-work-menu-toggle]').focus();await page.keyboard.press('ArrowDown');
  check('report secondary actions keep keyboard menu access',await page.locator('[data-action=print]').evaluate(node=>document.activeElement===node));await page.keyboard.press('Escape');
  const preserved=await page.evaluate(()=>JSON.stringify(selected));
  for(const count of [0,1,8]){
   await page.evaluate(({task,count})=>{const copy=structuredClone(task);copy.draft_report.logics=count?Array.from({length:count},(_,i)=>({...copy.draft_report.logics[i%3],title:'长内容测试'+(i+1)+'：'+copy.draft_report.logics[i%3].title})):[];selected=copy;tasks=[copy];stage=2;render()}, {task,count});
   check(count+' logic state retains all arguments',await page.locator('.paper-argument').count()===count);
   check(count+' logic state has no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(count===8){check('many argument choices scroll inside their own navigation',await page.locator('.paper-navigation').evaluate(node=>node.scrollWidth>node.clientWidth));}
  }
  await page.evaluate(value=>{selected=value;tasks=[value];stage=2;render()},task);
  check('UI rendering did not mutate retained research',await page.evaluate(before=>JSON.stringify(selected)===before,preserved));
 }
 check('no browser errors',record.errors.length===0);check('no external requests',record.external_requests.length===0);
 record.status='passed';
}catch(error){record.status='failed';record.error=error.message;throw error}finally{await browser.close();record.browser_closed=true;await writeFile(path.join(shots,'receipt.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,receipt:path.join(shots,'receipt.json')}));}
