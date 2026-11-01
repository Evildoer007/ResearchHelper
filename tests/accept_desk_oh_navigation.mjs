/* Compare live layout from read-only OH sources with RH; no provider calls. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(import.meta.dirname,'..');
const oh='/Users/haoranxu/Desktop/OptionHelper';
const frontend=process.env.RESEARCHHELPER_UI_FRONTEND||path.join(root,'products/app/frontend');
const capture=process.env.RESEARCHHELPER_UI_CAPTURE||'after';
const out=path.join(process.env.RESEARCHHELPER_UI_OUTPUT||path.join(root,'result/acceptance/desk-oh-navigation'),capture);
await mkdir(out,{recursive:true});
const task=JSON.parse(await readFile(path.join(root,'result/acceptance/desk-visual-system/retained-task.json'),'utf8'));
const record={status:'running',checks:[],comparisons:[],errors:[],externalRequests:[],model_calls:0,data_requests:0,production_store_touched:false,oh_modified:false,headless:true};
const check=(label,value)=>{assert(value,label);record.checks.push(label)};
const shared=['styles.css','refinement.css','theme-overrides.css','choice-controls.css'];
const sourceIdentity=async()=>Object.fromEntries(await Promise.all(shared.map(async name=>[name,createHash('sha256').update(await readFile(path.join(oh,'products/app/frontend/shared',name))).digest('hex')])));
record.ohStyles=await sourceIdentity();
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const context=await browser.newContext({reducedMotion:'reduce'});
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(!['http://127.0.0.1:38180','http://127.0.0.1:38181'].includes(url.origin)){record.externalRequests.push(url.href);return route.abort()}
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.port==='38180'&&url.pathname==='/api/bootstrap')return json({tasks:[],history:{},cases:[],token:'ui-fixture',execution:{mode:'offline'},role:'admin',authentication:false});
  if(url.port==='38180'&&url.pathname.includes('/report'))return route.fulfill({contentType:'text/html',body:task.report_html});
  if(url.pathname.startsWith('/api/'))return json({});
  try{
   let file,body;
   if(url.port==='38181'){
    if(url.pathname==='/'){
     file=path.join(oh,'products/app/frontend/optdesk/index.html');
     // Inert source render: keep OH DOM and CSS, omit runtime/auth scripts.
     body=(await readFile(file,'utf8')).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replaceAll('workspace-body--initializing','').replace('data-initializing="true"','data-initializing="false"');
    }else if(url.pathname==='/app/designer-token-vars.css')file=path.join(oh,'modules/designer/assets/themes/designer-token-vars.css');
    else if(url.pathname.startsWith('/app/frontend/'))file=path.join(oh,'products/app/frontend',url.pathname.slice('/app/frontend/'.length));
    else return route.fulfill({status:404,body:''});
   }else file=path.join(frontend,url.pathname==='/'?'index.html':url.pathname);
   return route.fulfill({body:body??await readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});
  }catch{return route.fulfill({status:404,body:''})}
 });
 const rh=await context.newPage(),reference=await context.newPage();
 rh.on('pageerror',error=>record.errors.push(error.message));
 await rh.goto('http://127.0.0.1:38180/');await rh.waitForFunction(()=>typeof workspaceReady!=='undefined'&&workspaceReady);
 await rh.evaluate(value=>{tasks=[value];selected=value;artifactsOpen=true;stage=2;render()},task);
 await reference.goto('http://127.0.0.1:38181/');
 await reference.locator('[data-module]').evaluateAll(buttons=>buttons.forEach((button,i)=>button.setAttribute('aria-selected',String(i===2))));
 const geometry=page=>page.locator('.desk-module-tabs').evaluate(nav=>{
  const box=el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height}};
  const area=box(document.querySelector('.work-area')),head=box(document.querySelector('.workbench-head')),n=box(nav),s=getComputedStyle(nav);
  return {area,head,nav:n,offsetLeft:n.x-area.x,offsetTop:n.y-head.y-head.height,padding:s.paddingLeft,buttons:[...nav.querySelectorAll('button')].map(button=>{const b=box(button),style=getComputedStyle(button);return {...b,left:b.x-n.x,font:style.fontSize,weight:style.fontWeight,padding:style.paddingLeft,radius:style.borderRadius}})};
 });
 for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
  await rh.setViewportSize({width,height});await reference.setViewportSize({width,height});
  await rh.evaluate(()=>window.RHWorkspaceControls.syncViewIndicator(false));await rh.waitForTimeout(70);
  const actual=await geometry(rh),expected=await geometry(reference);record.comparisons.push({width,actual,expected});
  if(capture==='after'&&width>700){
   check(width+' tabs follow the same header edge',Math.abs(actual.offsetTop-expected.offsetTop)<.5);
   check(width+' navigation height matches OH',Math.abs(actual.nav.height-expected.nav.height)<.5);
   check(width+' navigation inset matches OH',actual.padding===expected.padding);
   check(width+' all five buttons match OH geometry',actual.buttons.every((b,i)=>['height','width','left','font','weight','padding','radius'].every(key=>typeof b[key]==='number'?Math.abs(b[key]-expected.buttons[i][key])<.7:b[key]===expected.buttons[i][key])));
  }
  if(capture==='after'){
   check(width+' five entries visible',actual.buttons.every(b=>b.x>=0&&b.x+b.width<=width+.5&&b.y>=0&&b.y+b.height<=height));
   check(width+' page has no horizontal overflow',await rh.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   for(let i=0;i<5;i++){
    await rh.locator('#view-'+i).click();check(width+' view '+i+' reachable',await rh.locator('#view-'+i).getAttribute('aria-selected')==='true');
   }
   await rh.locator('#view-2').click();
   const before=await rh.locator('.views').boundingBox();
   await rh.locator('#artifacts').evaluate(el=>el.scrollTop=el.scrollHeight);
   const after=await rh.locator('.views').boundingBox();
   check(width+' navigation stays fixed while reading',Math.abs(before.y-after.y)<.5);
   await rh.locator('#artifacts').evaluate(el=>el.scrollTop=0);
   await rh.locator('#view-2').focus();await rh.keyboard.press('End');
   check(width+' keyboard End reaches last view',await rh.locator('#view-4').evaluate(el=>document.activeElement===el&&el.getAttribute('aria-selected')==='true'));
   await rh.keyboard.press('Home');check(width+' keyboard Home reaches first view',await rh.locator('#view-0').evaluate(el=>document.activeElement===el&&el.getAttribute('aria-selected')==='true'));
   await rh.locator('#view-2').click();
   await rh.waitForFunction(()=>{
    const marker=document.querySelector('.desk-module-tabs__indicator').getBoundingClientRect(),active=document.querySelector('#view-2').getBoundingClientRect();
    return Math.abs(marker.x-active.x)<1&&Math.abs(marker.width-active.width)<1;
   });
   const marker=await rh.locator('.desk-module-tabs__indicator').boundingBox(),active=await rh.locator('#view-2').boundingBox();
   check(width+' indicator follows active entry',Math.abs(marker.x-active.x)<1&&Math.abs(marker.width-active.width)<1);
  }
  await rh.screenshot({path:path.join(out,'rh-'+width+'.png')});
  await reference.screenshot({path:path.join(out,'oh-'+width+'.png')});
 }
 if(capture==='after'){
  await rh.setViewportSize({width:1440,height:900});
  await rh.evaluate(()=>{document.documentElement.style.zoom='1.4';window.RHWorkspaceControls.syncViewIndicator(false)});
  await rh.locator('#view-4').click();await rh.waitForTimeout(70);
  check('1.4x zoom keeps all five entries visible',await rh.locator('.views button').evaluateAll(buttons=>buttons.every(el=>{const b=el.getBoundingClientRect();return b.x>=0&&b.right<=innerWidth+.5})));
  check('1.4x zoom has no horizontal overflow',await rh.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await rh.evaluate(()=>{document.documentElement.style.zoom='';document.documentElement.dataset.theme='dark';document.documentElement.style.colorScheme='dark'});
  await rh.locator('#view-2').click();
  check('dark mode retains five visible entries',await rh.locator('.views button:visible').count()===5);
 }
 check('reference styles not modified',JSON.stringify(record.ohStyles)===JSON.stringify(await sourceIdentity()));
 check('no external requests',record.externalRequests.length===0);
 check('no browser errors',record.errors.length===0);
 record.status=capture==='before'?'baseline_recorded':'passed';
} catch(error){record.status='failed';record.error=error.stack;throw error}
finally{await browser.close();record.browserClosed=true;await writeFile(path.join(out,'receipt.json'),JSON.stringify(record,null,2)+'\n');console.log(JSON.stringify({status:record.status,checks:record.checks.length,receipt:path.join(out,'receipt.json')}))}
