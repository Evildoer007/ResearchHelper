/* Real retained observations; background browser only; no research services. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(import.meta.dirname,'..');
const frontend=process.env.RESEARCHHELPER_UI_FRONTEND||path.join(root,'products/app/frontend');
const output=process.env.RESEARCHHELPER_UI_OUTPUT||path.join(root,'result/acceptance/desk-evidence-ui');await mkdir(output,{recursive:true});
const task=JSON.parse(await readFile(path.join(root,'result/acceptance/desk-visual-system/retained-task.json'),'utf8'));
const receipt={status:'running',checks:[],errors:[],external_requests:[],screens:[],model_calls:0,data_requests:0,production_store_touched:false,foreground_opened:false};
const check=(name,value)=>{assert(value,name);receipt.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});let libraryRequests=0,failLibrary=false;
try{
 const context=await browser.newContext({reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',error=>receipt.errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());if(url.origin!=='http://127.0.0.1:38180'){receipt.external_requests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'ui-fixture',execution:{mode:'offline'},role:'admin',authentication:false});
  if(url.pathname.includes('/report'))return route.fulfill({contentType:'text/html',body:task.report_html});
  if(url.pathname.startsWith('/api/'))return json({});
  if(url.pathname==='/echarts.min.js'){libraryRequests++;if(failLibrary){failLibrary=false;return route.fulfill({status:503,body:''})}}
  try{const file=path.join(frontend,url.pathname==='/'?'index.html':url.pathname);return route.fulfill({body:await readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'})}catch{return route.fulfill({status:404,body:''})}
 });
 await page.setViewportSize({width:1440,height:900});await page.goto('http://127.0.0.1:38180/');await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 const showTask=async(value,index)=>page.evaluate(({value,index})=>{tasks=[value];selected=value;historical=null;artifactsOpen=true;stage=index;render()}, {value,index});
 await showTask(task,1);
 const view=page.locator('.desk-sources .observation-view'),canvas=view.locator('.observation-chart');
 check('source table remains the default',await view.getAttribute('data-presentation')==='table');
 check('all 22 retained observations remain available',await page.locator('.desk-sources [data-observation]').count()===22);
 check('chart engine is not loaded just to read data',libraryRequests===0);
 failLibrary=true;await view.locator('[data-evidence-view=chart]').click();
 await view.locator('.observation-chart-error:not([hidden])').waitFor();
 check('unavailable chart falls back to the complete source table',await view.getAttribute('data-presentation')==='table'&&await view.locator('.observation-table').isVisible());
 await view.locator('[data-evidence-view=chart]').click();await canvas.locator('canvas').waitFor();
 check('the same trend action retries without another research request',libraryRequests===2);
 const data=await canvas.evaluate(element=>{const chart=window.echarts.getInstanceByDom(element),option=chart.getOption();return {axes:option.xAxis.map(axis=>axis.data),series:option.series.map(series=>series.data),gaps:option.series.map(series=>series.connectNulls)}});
 const originalPrices=task.draft_report.facts.filter(f=>f.field==='close').map(f=>f.raw_value??f.value),originalPe=task.draft_report.facts.filter(f=>f.field==='ths_pe_ttm_stock').map(f=>f.raw_value??f.value);
 check('price chart uses the exact retained series',JSON.stringify(data.series[0])===JSON.stringify(originalPrices));
 check('valuation chart has a separate scale and exact series',data.axes.length===2&&JSON.stringify(data.series[1])===JSON.stringify(originalPe));
 check('trend does not connect missing points',data.gaps.every(value=>value===false));
 const point=await canvas.evaluate(element=>window.echarts.getInstanceByDom(element).convertToPixel({seriesIndex:0},[0,1331.88]));
 const bounds=await canvas.boundingBox();await page.mouse.click(bounds.x+point[0],bounds.y+point[1]);await page.locator('#dialog[open]').waitFor();
 check('clicking a real plotted point opens its retained source',await page.locator('#dialog-content').innerText().then(text=>text.includes('1331.88')&&text.includes('close')));
 await page.keyboard.press('Escape');check('source reader returns focus to the visible trend control',await view.locator('[data-evidence-view=chart]').evaluate(element=>element===document.activeElement));
 await page.locator('.desk-data-heading h2').click();
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(120);
  check(width+' chart fits the data workspace',await canvas.evaluate(element=>element.getBoundingClientRect().width<=innerWidth&&element.querySelector('canvas').width>0));
  check(width+' viewport has no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  check(width+' mode controls retain usable height',await view.locator('[data-evidence-view=chart]').evaluate(element=>element.getBoundingClientRect().height>=(innerWidth<=700?44:36)));
  const shot=width+'-data-trend.png';await page.screenshot({path:path.join(output,shot),animations:'disabled'});receipt.screens.push(shot);
 }
 await page.evaluate(()=>{document.documentElement.dataset.theme='dark';document.documentElement.style.colorScheme='dark'});await page.waitForTimeout(80);
 check('chart follows dark-mode text tokens',await canvas.evaluate(element=>window.echarts.getInstanceByDom(element).getOption().title[0].textStyle.color.trim()===getComputedStyle(document.querySelector('#artifacts')).getPropertyValue('--color-ink').trim()));
 await page.screenshot({path:path.join(output,'390-data-trend-dark.png'),animations:'disabled'});receipt.screens.push('390-data-trend-dark.png');
 await page.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.style.colorScheme='light'});
 await view.locator('[data-evidence-view=table]').click();check('returning to table preserves every original action',await view.locator('.observation-table').isVisible()&&await page.locator('.desk-sources [data-observation]').count()===22);
 await view.locator('[data-evidence-view=chart]').click();await canvas.locator('canvas').waitFor();const oldCanvas=await canvas.elementHandle();
 await page.locator('#view-2').click();check('leaving the source view disposes the old chart',await oldCanvas.evaluate(element=>window.echarts.getInstanceByDom(element)===undefined));
 await page.locator('#view-1').click();await page.locator('.desk-sources .observation-chart canvas').waitFor();check('returning to the same task remembers only the presentation',await view.getAttribute('data-presentation')==='chart');
 await page.setViewportSize({width:1440,height:900});await page.locator('#view-2').click();await page.waitForTimeout(120);
 const navigation=page.locator('.paper-navigation');
 await navigation.locator('[data-paper-target=paper-logic-0]').click();await page.waitForFunction(()=>document.querySelector('.paper-navigation [aria-current=true]')?.dataset.paperTarget==='paper-logic-0');
 check('workingpaper identifies its current logic',await navigation.locator('[aria-current=true]').count()===1);
 await navigation.locator('[data-paper-target=paper-logic-0]').focus();await page.keyboard.press('End');
 check('logic navigation supports End',await navigation.locator('[data-paper-target=paper-logic-2]').evaluate(element=>element===document.activeElement));
 await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.paper-navigation [aria-current=true]')?.dataset.paperTarget==='paper-logic-2');
 check('logic selection focuses its own argument',await page.locator('#paper-logic-2').evaluate(element=>element===document.activeElement));
 check('sticky logic navigation stays below all five view entries',await navigation.evaluate(element=>element.getBoundingClientRect().top>=document.querySelector('.views').getBoundingClientRect().bottom-1));
 await page.locator('#paper-logic-2 h2').click();
 await page.screenshot({path:path.join(output,'1440-workingpaper-current.png'),animations:'disabled'});receipt.screens.push('1440-workingpaper-current.png');
 await page.evaluate(()=>document.getElementById('paper-logic-1').scrollIntoView({block:'start',behavior:'instant'}));await page.waitForFunction(()=>document.querySelector('.paper-navigation [aria-current=true]')?.dataset.paperTarget==='paper-logic-1');
 check('ordinary reading updates the current logic',await navigation.locator('[aria-current=true]').getAttribute('data-paper-target')==='paper-logic-1');
 await page.locator('#view-0').click();await page.screenshot({path:path.join(output,'1440-overview.png'),animations:'disabled'});receipt.screens.push('1440-overview.png');
 check('overview has one set of output links and no second numbered workflow',await page.locator('.desk-pipeline button').count()===4&&await page.locator('.desk-flow-number').count()===0);
 check('desktop views use compact natural-width navigation',await page.locator('.views button').first().evaluate(element=>element.getBoundingClientRect().width<150));
 for(const width of [480,278]){
  await page.locator('#artifacts').evaluate((element,width)=>element.style.width=width+'px',width);
  check(width+'px workspace keeps all five view targets inside its own width',await page.locator('.views').evaluate(element=>{const box=element.getBoundingClientRect();return [...element.querySelectorAll('button')].every(button=>{const b=button.getBoundingClientRect();return b.left>=box.left&&b.right<=box.right+.5&&b.height>=44})}));
 }
 await page.locator('#artifacts').evaluate(element=>element.style.removeProperty('width'));

 await page.setViewportSize({width:390,height:844});check('all five views remain visible on mobile',await page.locator('.views button').evaluateAll(elements=>elements.length===5&&elements.every(element=>{const b=element.getBoundingClientRect();return b.left>=0&&b.right<=innerWidth&&b.height>=44})));
 for(let index=0;index<5;index++){await page.locator('#view-'+index).click();check('mobile view '+index+' remains reachable',await page.locator('#view-'+index).getAttribute('aria-selected')==='true')}
 const changed=structuredClone(task);changed.id='separate-task';changed.draft_report.facts.forEach(f=>{if(f.field==='close')f.raw_value=777});
 await showTask(changed,1);check('a new task does not inherit another task chart mode',await view.getAttribute('data-presentation')==='table');
 await showTask(task,1);check('presentation never mutates the original research',await page.evaluate(before=>JSON.stringify(selected)===before,JSON.stringify(task)));
 check('no unexpected script errors',receipt.errors.length===0);check('all assets were local',receipt.external_requests.length===0);receipt.status='passed';
}catch(error){receipt.status='failed';receipt.error=error.message;throw error}finally{await browser.close();receipt.browser_closed=true;await writeFile(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({status:receipt.status,checks:receipt.checks.length,receipt:path.join(output,'receipt.json')}))}
