/* Saved report history: original corrections survive later revisions. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=process.env.RESEARCHHELPER_UI_BASE,output=process.env.RESEARCHHELPER_UI_OUTPUT;
const fixture=JSON.parse(await readFile(process.env.RESEARCHHELPER_UI_FIXTURE,'utf8')),ids=fixture.ids;
const record={status:'running',checks:[],contrast_samples:[],errors:[],external_requests:[],screens:[],headless:true,model_calls:0,data_requests:0};
const check=(name,value)=>{assert(value,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){record.external_requests.push(route.request().url());return route.abort()}return route.continue()});
 const status=await (await context.request.get(base+'/api/auth/status')).json();
 const login=await context.request.post(base+'/api/auth/login',{headers:{'X-ResearchHelper-Token':status.token},data:{account:'admin',password:'66666666'}});
 assert(login.ok(),'The isolated test account could not sign in');
 const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
 async function ready(){await page.locator('[data-workspace-shell][data-initializing="false"]').waitFor({state:'attached'})}
 async function select(id){await page.locator('[data-task="'+id+'"]').first().click();await page.locator('button[data-mode="desk"]').click();await page.locator('#view-3').click()}
 async function history(){await page.locator('[data-work-menu-toggle]').click();await page.getByRole('menuitem',{name:'交付版本',exact:true}).click();await page.locator('#dialog[open] .report-version-record').first().waitFor()}
 await page.goto(base);await ready();await select(ids[0]);
 check('current report remains version two',await page.evaluate(()=>selected.delivery.version===2));
 check('old correction is not attributed to the revised paper',await page.locator('.report-correction-state').count()===0);
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme},theme);
   await history();
   const rows=page.locator('.report-version-record');
   const colors=await page.locator('.report-version-link').first().evaluate(el=>({foreground:getComputedStyle(el).color,background:getComputedStyle(document.querySelector('#dialog')).backgroundColor}));
   const luminance=value=>value.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const foreground=luminance(colors.foreground),background=luminance(colors.background),ratio=(Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05);
   record.contrast_samples.push({width,theme,...colors,ratio});
   check(width+' '+theme+' version link contrast reaches 4.5 to one',ratio>=4.5);

   check(width+' '+theme+' saved versions are in reverse order',JSON.stringify(await rows.locator('.report-version-link').allTextContents())===JSON.stringify(['阅读版本2','阅读版本1']));
   check(width+' '+theme+' only original version has its two notes',await rows.nth(0).locator('li').count()===0&&await rows.nth(1).locator('li').count()===2);
   check(width+' '+theme+' links retain exact task and version',JSON.stringify(await rows.locator('a').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('href'))))===JSON.stringify([2,1].map(v=>'/api/tasks/'+ids[0]+'/report/'+v)));
   check(width+' '+theme+' original note markup is literal',await rows.locator('svg').count()===0&&!(await page.evaluate(()=>window.historyExecuted)));
   check(width+' '+theme+' modal stays inside the viewport',await page.locator('#dialog').evaluate(el=>{const r=el.getBoundingClientRect();return el.scrollWidth<=el.clientWidth&&r.left>=0&&r.right<=innerWidth+1}));
   check(width+' '+theme+' original note is fully retained',(await rows.nth(1).innerText()).includes(fixture.notes[0])&&(await rows.nth(1).innerText()).includes(fixture.notes[1]));
   const name='history-'+width+'-'+theme+'.png';await page.screenshot({path:path.join(output,name),animations:'disabled'});record.screens.push(name);
   await page.keyboard.press('Escape');
   check(width+' '+theme+' closing returns focus to the visible report control',await page.locator('[data-work-menu-toggle]').evaluate(el=>el===document.activeElement));
  }
 }
 await page.setViewportSize({width:1440,height:900});
 await page.reload();await ready();await select(ids[0]);await history();
 check('history and notes survive a fresh page read',await page.locator('.report-version-record').count()===2&&await page.locator('.report-version-record li').count()===2);
 await page.keyboard.press('Escape');
 for(const version of [1,2]){
  const response=await context.request.get(base+'/api/tasks/'+ids[0]+'/report/'+version);
  const raw=await response.text();
  check('version '+version+' still opens its own frozen document',response.ok()&&raw.includes(version===1?'原稿保留案例':'修订后的案例'));
 }
 let release,entered,handled;const gate=new Promise(resolve=>release=resolve),reached=new Promise(resolve=>entered=resolve),finished=new Promise(resolve=>handled=resolve);
 const routePattern='**/api/tasks/'+ids[0]+'/report-history';let requests=0;
 await page.route(routePattern,async route=>{requests++;entered();await gate;await route.continue();handled()});
 await page.locator('[data-work-menu-toggle]').click();await page.getByRole('menuitem',{name:'交付版本',exact:true}).click();await reached;
 check('pending history disables repeat dispatch',await page.locator('[data-action=versions]').isDisabled());
 await page.evaluate(()=>openReportVersions());check('repeat request remains single flight',requests===1);
 await page.evaluate(id=>setTask(tasks.find(task=>task.id===id)),ids[1]);
 release();await finished;await page.waitForFunction(()=>reportVersionRead===null);
 check('late history never opens over a different task',await page.evaluate(id=>selected.id===id&&!document.querySelector('dialog[open]'),ids[1]));
 await page.unroute(routePattern);
 await select(ids[1]);await history();
 check('another task shows only its own saved version',await page.locator('.report-version-record').count()===1&&await page.locator('.report-version-record li').count()===0);
 check('another task never reuses the original notes',!(await page.locator('#dialog-content').innerText()).includes(fixture.notes[0]));
 await page.keyboard.press('Escape');
 const otherPattern='**/api/tasks/'+ids[1]+'/report-history';
 await page.route(otherPattern,route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({task_id:ids[0],reports:[]})}));
 await page.locator('[data-work-menu-toggle]').click();await page.getByRole('menuitem',{name:'交付版本',exact:true}).click();
 await page.getByText('交付版本响应无效，请重新读取',{exact:true}).waitFor();
 check('wrong task response is rejected without rendering links',await page.locator('#dialog[open] .report-version-record').count()===0);
 await page.keyboard.press('Escape');await page.unroute(otherPattern);
 await history();check('a failed read can be opened again',await page.locator('.report-version-record').count()===1);
 await page.keyboard.press('Escape');
 check('reading report history makes no research dispatch',await page.evaluate(()=>tasks.every(task=>task.ledger.used.model_calls===0&&task.ledger.used.data_requests===0)));
 check('no browser errors',record.errors.length===0);check('no external requests',record.external_requests.length===0);
 record.status='passed';
}catch(error){record.status='failed';record.error=String(error);throw error}
finally{await browser.close();record.browser_closed=true;await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,receipt:path.join(output,'browser.json'),error:record.error}))}
