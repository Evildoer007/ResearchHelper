/* Retained real research, synthetic edge states, local-only UI acceptance. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(import.meta.dirname,'..'),out=process.env.RESEARCHHELPER_UI_OUTPUT||path.join(root,'result/acceptance/ui-review15/quality');
const frontend=process.env.RESEARCHHELPER_UI_FRONTEND||path.join(root,'products/app/frontend');
const capture=process.env.RESEARCHHELPER_UI_CAPTURE||'after',shots=path.join(out,capture);await mkdir(shots,{recursive:true});
const task=JSON.parse(await readFile(path.join(root,'result/acceptance/desk-visual-system/retained-task.json'),'utf8'));
const report=task.report_html; let reportFailure=false, reportDelay=0; const requests=[];
const record={status:'running',checks:[],errors:[],external_requests:[],screens:[],model_calls:0,data_requests:0,production_store_touched:false};
const check=(label,condition)=>{assert(condition,label);record.checks.push(label)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',error=>record.errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url()); if(url.pathname.startsWith('/api/')) requests.push({path:url.pathname,method:route.request().method()});
  if(url.origin!=='http://127.0.0.1:38180'){record.external_requests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'ui-fixture',execution:{mode:'offline'},role:'admin',authentication:false});
  if(url.pathname.includes('/report')){if(reportDelay)await new Promise(resolve=>setTimeout(resolve,reportDelay));return route.fulfill(reportFailure?{status:500,contentType:'application/json',body:JSON.stringify({error:'quality-report-fixture-failure'})}:{contentType:'text/html',body:report});}
  if(url.pathname.startsWith('/api/'))return json({});
  try{
   const file=path.join(frontend,url.pathname==='/'?'index.html':url.pathname);
   return route.fulfill({body:await readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});
  }catch{return route.fulfill({status:404,body:''})}
 });
 await page.goto('http://127.0.0.1:38180/');await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 await page.evaluate(value=>{tasks=[value];selected=value;artifactsOpen=true;stage=2;render()},task);


 record.viewportChecks=[];
 for(const width of [390,1024,1440]){
 await page.setViewportSize({width,height:844});
 for(const n of [0,1,2,3,4]){await page.locator('#view-'+n).click();const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);record.viewportChecks.push({width,stage:n,overflow});check(width+' stage '+n+' no overflow',!overflow);}
 }
 await page.locator('#view-2').click();
 const nav=page.locator('.paper-navigation button').first();
 await nav.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 record.focus=await nav.evaluate(el=>{const s=getComputedStyle(el),p=el.parentElement,b=el.getBoundingClientRect(),r=p.getBoundingClientRect();const extent=parseFloat(s.outlineWidth)+parseFloat(s.outlineOffset);return {focusVisible:el.matches(':focus-visible'),outline:s.outline,extent,parentOverflow:getComputedStyle(p).overflow,button:{left:b.left,top:b.top,right:b.right,bottom:b.bottom},container:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},outside:{left:r.left-(b.left-extent),top:r.top-(b.top-extent),right:b.right+extent-r.right,bottom:b.bottom+extent-r.bottom}};});
 await page.screenshot({path:path.join(shots,'keyboard-focus.png')});

 check('focus ring is inset',record.focus.focusVisible&&record.focus.extent===0);
 await page.evaluate(()=>{selected.version=2;selected.synthetic=true;render();setContext(true)});
 const drawer=page.locator('#task-context');const drawerText=await drawer.innerText();
 check('drawer actual state and old delivery distinguish versions',drawerText.includes('版本2')&&drawerText.includes('已保存报告')&&drawerText.includes('版本1'));
 check('drawer omits static offline label',!drawerText.includes('离线研究环境'));
 check('drawer omits duplicated five-stage navigation',await drawer.locator('[data-stage]').count()===0);
 check('drawer close has SVG',await drawer.locator('.drawer-close svg').count()===1);
 await page.screenshot({path:path.join(shots,'task-drawer.png')});
 await drawer.locator('.drawer-close').click();await page.setViewportSize({width:390,height:844});
 reportFailure=true;await page.locator('#view-3').click();
 await page.locator('#report-reader-state [data-action="retry-report"]:visible').waitFor();
 check('failure hides iframe',!await page.locator('#report-frame').isVisible());
 check('failure offers recovery',await page.locator('#report-reader-state').innerText().then(text=>text.includes('报告暂时无法读取')&&text.includes('重新读取')));
 check('synthetic badge visible at 390px',await page.locator('.report-case-state').isVisible());
 await page.screenshot({path:path.join(shots,'report-failure.png')});
 const requestStart=requests.length;reportFailure=false;await page.locator('[data-action="retry-report"]').click();await page.locator('#report-frame:visible').waitFor();
 check('retry only gets report',requests.slice(requestStart).length>0&&requests.slice(requestStart).every(r=>r.method==='GET'&&r.path.includes('/report')));
 check('retry hides error',!await page.locator('#report-reader-state').isVisible());
 check('retry returns keyboard focus to the visible report',await page.evaluate(()=>document.activeElement?.id==='report-frame'));
 await page.screenshot({path:path.join(shots,'report-recovered.png')});
 await page.locator('#view-2').click();reportFailure=true;reportDelay=700;await page.locator('#view-3').click();await page.locator('#view-2').click();await page.waitForTimeout(900);
 check('late report failure does not switch view',await page.locator('#view-2').getAttribute('aria-selected')==='true');
 reportDelay=0;reportFailure=false;await page.locator('#view-3').click();await page.locator('#report-frame:visible').waitFor();
 check('return to current task reads report',await page.locator('#view-3').getAttribute('aria-selected')==='true');

 await page.setViewportSize({width:1440,height:900});
 await page.evaluate(value=>{const copy=structuredClone(value);copy.id='zoom-fixture';copy.draft_report.logics=Array.from({length:8},(_,i)=>({...copy.draft_report.logics[i%3],title:'缩放下的长逻辑标题'+(i+1)+'：'+copy.draft_report.logics[i%3].title}));selected=copy;tasks=[copy];stage=2;document.documentElement.style.zoom='1.4';render()},task);
 await page.locator('.paper-navigation button').first().focus();
 for(let i=0;i<4;i++)await page.keyboard.press('ArrowRight');
 const zoomNavigation=await page.locator('.paper-navigation button').nth(4).evaluate(button=>{const b=button.getBoundingClientRect(),n=button.parentElement.getBoundingClientRect();return {left:b.left,right:b.right,navLeft:n.left,navRight:n.right,inside:b.left>=n.left-.5&&b.right<=n.right+.5,centered:Math.abs((b.left+b.right-n.left-n.right)/2)<2}});
 record.zoomNavigation=zoomNavigation;check('scaled keyboard navigation keeps the focused logic visible',zoomNavigation.inside);
 check('scaled keyboard navigation centers the focused logic',zoomNavigation.centered);
 await page.keyboard.press('Enter');await page.waitForTimeout(100);
 check('scaled reading selects the same logic as its navigation',await page.locator('.paper-navigation [aria-current=true]').getAttribute('data-paper-target')==='paper-logic-4');
 await page.locator('#view-1').click();
 const zoomView=page.locator('.desk-sources .observation-view');await zoomView.evaluate(element=>element.style.width='400px');
 await zoomView.locator('[data-evidence-view=chart]').click();await zoomView.locator('.observation-chart canvas').waitFor();
 check('scaled charts use logical width for responsive panels',await zoomView.locator('.observation-chart').evaluate(element=>{const option=window.echarts.getInstanceByDom(element).getOption();return element.clientWidth===400&&option.grid[1].top>option.grid[0].top}));
 await page.evaluate(()=>document.documentElement.style.removeProperty('zoom'));
 check('no page errors',record.errors.length===0);check('no external requests',record.external_requests.length===0);
 record.requests=requests;record.status='passed';record.headless=true;record.screens=['keyboard-focus.png','task-drawer.png','report-failure.png','report-recovered.png'];
}catch(error){record.status='failed';record.error=error.message;throw error}finally{await browser.close();record.browser_closed=true;await writeFile(path.join(shots,'receipt.json'),JSON.stringify(record,null,2));console.log(JSON.stringify({status:record.status,checks:record.checks.length,receipt:path.join(shots,'receipt.json')}));}
