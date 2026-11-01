import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root='/Users/haoranxu/Desktop/ResearchHelper',out=path.join(root,'result/acceptance/resdesk-completion-current');
await mkdir(out,{recursive:true});
const record={checks:[],errors:[],externalRequests:[],model_calls:0,data_requests:0,production_store_touched:false};
const check=(name,condition)=>{assert.ok(condition,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({reducedMotion:'reduce'}),page=await context.newPage();
 page.on('pageerror',e=>record.errors.push(e.message));
 const requests=[];let hold=false,release;
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin!=='http://127.0.0.1:38179'){record.externalRequests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'synthetic',execution:{mode:'offline'}});
  if(url.pathname==='/api/export'){
   requests.push({method:route.request().method(),body:route.request().postDataJSON()});
   if(hold)await new Promise(resolve=>{release=resolve});
   const body=requests.at(-1).body;return json({task_id:body.task_id,version:body.version,filename:'合成研究_v1.'+body.format,mime:'text/html',content_base64:Buffer.from('<html>冻结中文报告</html>').toString('base64')});
  }
  if(url.pathname.includes('/report/'))return route.fulfill({contentType:'text/html',body:'<html><body>冻结报告<script>window.RHVisuals={profile:()=>({theme:"research",schemes:{profit:"bars"}})}</script></body></html>'});
  if(url.pathname.startsWith('/api/'))return json({});
  try{const file=path.join(root,'products/app/frontend',url.pathname==='/'?'index.html':url.pathname);const body=await readFile(file);return route.fulfill({body,contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'})}catch{return route.fulfill({status:404,body:''})}
 });
 await page.goto('http://127.0.0.1:38179/');await page.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 await page.evaluate(()=>{
  const task={id:'fixture',version:1,status:'已完成',title:'合成研究',question:'利润如何变化',cutoff:'2026-10-30T00:00:00+08:00',intent:'研究',objects:['合成公司'],questions:[],nodes:[],ledger:[],evidence:[],calculations:[],tracking:[],revisions:[],source_records:[],materials:[],delivery:{version:1},theses:[{id:'T1',task_id:'fixture',research_version:1,title:'正式论点',investment_meaning:'现金流需要复核',support:[],counter:[]}],draft_report:{logics:[{title:'草稿论点',implication:'草稿含义',risk:'唯一草稿风险',data_refs:['D1']}],facts:[{id:'D1',code:'SYNTH',label:'利润',value:10,raw_value:10,unit:'亿元',period:'2025',artifact_id:'S1',locator:{field:'profit'}}],calculations:[]}};
  task.source_records=[{id:'S1',name:'合成原始响应',kind:'api',text:'profit: 10',content:'profit: 10',status:'已取得'}];
  tasks=[task];selected=task;artifactsOpen=true;stage=2;render();
 });
 check('正式与草稿共存',await page.locator('#content').innerText().then(t=>t.includes('正式论点')&&t.includes('草稿论点')));
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await page.setViewportSize({width,height});
  for(let i=0;i<5;i++){
   await page.locator('#view-'+i).click();
   check(width+'视图'+i+'可访问',await page.locator('#view-'+i).getAttribute('aria-selected')==='true');
   check(width+'视图'+i+'无页面横溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.screenshot({path:path.join(out,`${width}-view-${i}.png`),animations:'disabled'});
  }
 }
 check('跟踪无重复风险',!(await page.locator('#content').innerText()).includes('唯一草稿风险'));
 await page.setViewportSize({width:1440,height:900});await page.locator('#view-1').click();
 await page.locator('[data-draft-source="D1"]').first().click();
 check('来源点击打开定位',await page.locator('#dialog[open]').isVisible());await page.keyboard.press('Escape');
 check('离线缺正文不显示pending',await page.evaluate(()=>{const html=RHDeskViews.report({delivery:{version:1},pending_html:'UNSAVED_MARKER'},{esc,apiMode:false,reportProfile:()=> 'standard',reportURL:()=>'',reportVersionBusy:()=>false});return !html.includes('UNSAVED_MARKER')&&html.includes('未保存报告正文')}));
 await page.locator('#view-3').click();await page.frameLocator('#report-frame').locator('body').waitFor();
 await page.evaluate(()=>{window.__saves=[];window.webkit={messageHandlers:{researchhelper:{postMessage(message){window.__saves.push(message);queueMicrotask(()=>window.dispatchEvent(new CustomEvent('researchhelper:artifact-saved',{detail:{operation_id:message.operation_id,status:'saved',name:message.name}})))}}}}});
 for(const format of ['html','pdf','docx','xlsx','csv']){
  await page.evaluate(format=>{document.querySelector('#report-format').value=format},format);
  await page.evaluate(()=>exportReport());
  const req=requests.at(-1);check(format+'统一POST完整呈现参数',req.method==='POST'&&req.body.format===format&&req.body.visual_profile.theme==='research'&&req.body.visual_profile.schemes.profit==='bars');
 }
 const saves=await page.evaluate(()=>window.__saves.length);hold=true;
 await page.evaluate(()=>{window.__exportPromise=exportReport().catch(error=>window.__exportError=error.message)});
 await page.waitForTimeout(100);await page.evaluate(()=>{taskSelectionGeneration++;selected={...selected,id:'other'};});release();await page.evaluate(()=>window.__exportPromise);
 check('切换任务不保存旧请求',await page.evaluate(n=>window.__saves.length===n&&window.__exportError.includes('变化'),saves));
 check('无外部请求',record.externalRequests.length===0);check('无浏览器异常',record.errors.length===0);record.status='passed';
}catch(error){record.status='failed';record.failure=error.stack;throw error}finally{await browser.close();record.browser_closed=true;await writeFile(path.join(out,'browser-receipt.json'),JSON.stringify(record,null,2));}
