/* Actual local service, headless browser: annotations never rewrite the paper. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=process.env.RESEARCHHELPER_UI_BASE,output=process.env.RESEARCHHELPER_UI_OUTPUT;
const {ids}=JSON.parse(await readFile(process.env.RESEARCHHELPER_UI_FIXTURE,'utf8'));
const record={status:'running',checks:[],errors:[],external_requests:[],screens:[],headless:true,model_calls:0,data_requests:0};
const check=(name,value)=>{assert(value,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){record.external_requests.push(route.request().url());return route.abort()}return route.continue()});
 if(process.env.RESEARCHHELPER_UI_PRESET_LOGIN==='1'){
  const status=await (await context.request.get(base+'/api/auth/status')).json();
  const response=await context.request.post(base+'/api/auth/login',{headers:{'X-ResearchHelper-Token':status.token},data:{account:'admin',password:'66666666'}});
  assert(response.ok(),'Test bundle preset account could not sign in');
 }
 const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
 await page.goto(base);await page.locator('[data-workspace-shell][data-initializing="false"]').waitFor({state:'attached'});
 await page.locator('[data-task="'+ids[0]+'"]').first().click();
 await page.locator('button[data-mode="desk"]').click();await page.locator('#view-3').click();
 await page.frameLocator('#report-frame').locator('main').waitFor();
 const original=await page.frameLocator('#report-frame').locator('main').innerText();
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme},theme);
   check(width+' '+theme+' saved correction state is visible',await page.locator('.report-correction-state').isVisible());
   await page.locator('.report-correction-state button').click();
   check(width+' '+theme+' two existing notes are readable',await page.locator('#dialog .report-correction-list li').count()===2);
   check(width+' '+theme+' input has keyboard focus',await page.locator('#report-correction-reason').evaluate(el=>el===document.activeElement));
   check(width+' '+theme+' modal has no horizontal overflow',await page.locator('#dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
   const screen='corrections-'+width+'-'+theme+'.png';await page.screenshot({path:path.join(output,screen),animations:'disabled'});record.screens.push(screen);
   await page.keyboard.press('Escape');check(width+' '+theme+' modal closes',!(await page.locator('#dialog').evaluate(el=>el.open)));
   await page.locator('#view-2').click();
   check(width+' '+theme+' workpaper shows bound notes',await page.locator('.paper-corrections li').count()===2);
   check(width+' '+theme+' page has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.locator('#view-3').click();
  }
 }
 await page.setViewportSize({width:1440,height:900});
 await page.locator('[data-work-menu-toggle]').click();await page.getByRole('menuitem',{name:'报告更正',exact:true}).click();
 await page.locator('#report-correction-reason').fill('预期变化仍需对应证据。<svg onload="window.correctionExecuted=true">');
 await page.getByRole('button',{name:'记录更正',exact:true}).click();
 await page.locator('#dialog').waitFor({state:'hidden'});
 check('saving displays the third annotation',await page.locator('.report-correction-state').innerText()==='查看3条报告更正');
 check('saving restores the report control focus',await page.locator('[aria-label="更多报告操作"]').evaluate(el=>el===document.activeElement));
 check('frozen report prose is unchanged',await page.frameLocator('#report-frame').locator('main').innerText()===original);
 await page.reload();await page.locator('[data-workspace-shell][data-initializing="false"]').waitFor({state:'attached'});
 await page.locator('button[data-mode="desk"]').click();await page.locator('#view-3').click();
 await page.locator('.report-correction-state button').click();
 check('notes persist after a fresh page read',await page.locator('#dialog .report-correction-list li').count()===3);
 check('note markup stays literal text',await page.locator('.report-correction-list svg').count()===0&&!(await page.evaluate(()=>window.correctionExecuted)));
 await page.keyboard.press('Escape');
 await page.locator('.report-correction-state button').click();
 await page.locator('#report-correction-reason').fill('任务切换后不应保存的说明。');
 // The native task bridge may select another task while a WebView modal is open.
 await page.evaluate(id=>setTask(tasks.find(task=>task.id===id)),ids[1]);
 await page.getByRole('button',{name:'记录更正',exact:true}).click();
 check('a stale correction form cannot write to another task',(await page.locator('#report-correction-feedback').innerText()).includes('任务已切换'));
 await page.keyboard.press('Escape');
 await page.locator('[data-task="'+ids[1]+'"]').first().click();await page.locator('#view-3').click();
 check('unrelated report has no correction state',await page.locator('.report-correction-state').count()===0);
 await page.locator('#view-2').click();check('unrelated workpaper has no copied notes',await page.locator('.paper-corrections').count()===0);
 check('no browser errors',record.errors.length===0);check('no external requests',record.external_requests.length===0);
 record.status='passed';
}catch(error){record.status='failed';record.error=String(error);throw error}
finally{await browser.close();record.browser_closed=true;await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,receipt:path.join(output,'browser.json'),error:record.error}))}
