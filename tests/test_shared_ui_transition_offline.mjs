/* Isolated browser checks. Every HTTP request is fulfilled locally; no app store. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const frontend=path.resolve('products/app/frontend');
const browser=await chromium.launch({channel:'chrome',headless:true});
const fixture={tasks:[],history:{},cases:[],entries:[],intents:[],execution:{mode:'offline'}};
let checks=0;
try{
  for(const failed of [false,true]){
    const context=await browser.newContext({viewport:{width:1200,height:800}});
    const page=await context.newPage();
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    let release;
    const gate=new Promise(resolve=>{release=resolve});
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/api/bootstrap'){
        await gate;
        return route.fulfill({status:failed?503:200,contentType:'application/json',body:JSON.stringify(failed?{error:'离线验收模拟连接失败'}:fixture)});
      }
      if(url.pathname.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:'{}'});
      const file=path.resolve(frontend,'.'+(url.pathname==='/'?'/index.html':url.pathname));
      if(!file.startsWith(frontend+path.sep))return route.abort();
      try{
        const body=await readFile(file);
        const contentType={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';
        return route.fulfill({body,contentType});
      }catch{return route.fulfill({status:404,body:''})}
    });
    await page.goto('http://rh-ui.test/',{waitUntil:'domcontentloaded'});
    assert.equal(await page.locator('[data-workspace-shell]').getAttribute('data-initializing'),'true');
    assert.equal(await page.locator('[data-workspace-shell]').isVisible(),false);
    // Draft recovery must survive bootstrap, including the error path.
    await page.evaluate(()=>{document.querySelector('#chat-input').value='验收保留草稿'});
    release();
    await page.waitForFunction(()=>document.querySelector('[data-workspace-shell]').dataset.initializing==='false');
    assert.equal(await page.locator('[data-workspace-shell]').isVisible(),true);
    assert.equal(await page.locator('#chat-input').inputValue(),'验收保留草稿');
    if(failed)assert.equal(await page.locator('[data-action="reconnect"]').count(),1);
    else assert.equal(await page.locator('.send-button').isDisabled(),false);
    await page.waitForFunction(()=>Boolean(window.ResearchHelperTheme));
    await page.evaluate(()=>{localStorage.setItem('researchhelper.theme','dark');window.dispatchEvent(new Event('focus'))});
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
    assert.equal(await page.locator('.task-history-icon--desktop').isVisible(),true);
    assert.equal(await page.locator('.task-history-icon--mobile').isVisible(),false);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('.task-history-icon--desktop').isVisible(),false);
    assert.equal(await page.locator('.task-history-icon--mobile').isVisible(),true);
    assert.deepEqual(errors,[]);
    checks+=10;
    if(!failed){
      await page.setViewportSize({width:1200,height:800});
      await page.setContent('<link rel="stylesheet" href="http://rh-ui.test/ui/styles.css"><link rel="stylesheet" href="http://rh-ui.test/login/login.css"><main class="login-page"><div class="login-theme"><button>主题</button></div><label class="login-check"><input type="checkbox">保持登录</label><div class="login-password"><button class="login-eye">眼睛</button></div></main>');
      const sizes=await page.evaluate(()=>Object.fromEntries(['.login-theme button','.login-eye','.login-check'].map(selector=>{const box=document.querySelector(selector).getBoundingClientRect();return [selector,{width:box.width,height:box.height}]})));
      assert.deepEqual(sizes['.login-theme button'],{width:26,height:20});
      assert.deepEqual(sizes['.login-eye'],{width:34,height:34});
      assert.ok(sizes['.login-check'].height<44);
      checks+=3;
    }
    await context.close();
  }
  // Direct file previews use their bundled fixture, without importing a module.
  for(const available of [true,false]){
    const context=await browser.newContext();
    const page=await context.newPage();
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.protocol!=='file:')return route.abort();
      if(url.pathname.endsWith('/demo-data.js'))return route.fulfill({contentType:'text/javascript',body:'window.RH_DEMO='+JSON.stringify(available?fixture:null)});
      return route.continue();
    });
    await page.goto(pathToFileURL(path.join(frontend,'index.html')).href);
    await page.waitForFunction(()=>document.querySelector('[data-workspace-shell]').dataset.initializing==='false');
    assert.equal(await page.locator('[data-workspace-shell]').isVisible(),true);
    if(available)assert.equal(await page.locator('.send-button').isDisabled(),false);
    else assert.equal(await page.locator('[data-action="reconnect"]').count(),1);
    checks+=2;await context.close();
  }
  console.log(JSON.stringify({passed:true,checks,network:'intercepted fixtures only'}));
}finally{await browser.close()}
