import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(path.join(process.env.HOME,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,baseline,id}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],errors:[],externalRequests:0};
const check=(name,value)=>{assert.ok(value,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1024,height:768},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['data:','blob:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const page=await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>record.errors.push(error.message));
  const task='/api/tasks/'+id;
  const report=version=>task+'/report/'+version;
  const get=resource=>context.request.get(new URL(resource,origin).href);
  await page.goto(base);
  await page.locator('#login-account').fill('sales');
  await page.locator('#login-password').fill('88888888');
  await page.locator('#login-submit').click();
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  check('actual sales session',await (await get('/api/auth/status')).json().then(state=>state.role==='sales'));
  await page.locator('#task-list [data-task="'+id+'"]').click();
  await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,id);
  const drawer=page.locator('#task-context'),opener=page.locator('.web-control[data-action=artifacts]');
  const versionControl=drawer.locator('[data-action=versions]'),card=page.locator('#chat a.artifact-card');
  const dialog=page.locator('#dialog');
  const openDrawer=async()=>{await opener.click();await drawer.waitFor({state:'visible'})};
  const closeDrawer=async()=>{await drawer.locator('[data-action=close-context]').click();await drawer.waitFor({state:'hidden'})};
  const originalResponse=await get(report(1));
  assert.ok(originalResponse.ok(),await originalResponse.text());
  const original=await originalResponse.text();
  await openDrawer();
  check('first saved report needs only its direct card',await versionControl.count()===0&&await card.getAttribute('href')===report(1));
  if(baseline)await drawer.locator('[data-action=update]').click();
  else{
    await closeDrawer();
    await page.locator('[data-menu-task="'+id+'"]').press('Enter');
    await page.getByRole('menuitem',{name:'更新研究',exact:true}).click();
  }
  try{await page.waitForFunction(()=>selected?.delivery?.version===2)}
  catch(error){
    record.lastTask=await page.evaluate(()=>({version:selected?.version,status:selected?.status,error:selected?.error,notice:$('#notice').textContent}));
    throw error;
  }
  check('normal update saves version two with zero external dispatches',await page.evaluate(
    ()=>selected.ledger.used.model_calls===0&&selected.ledger.used.data_requests===0));
  check('the earlier standard report is still frozen',await (await get(report(1))).text()===original&&
    await card.getAttribute('href')===report(2));
  if(baseline){
    check('the previous delivery panel offers no version action',await versionControl.count()===0);
    await closeDrawer();
    await page.locator('[data-account-menu-toggle]').click();
    await page.locator('#account-menu [data-action=library]').click();
    check('the old version is absent from the sales library and conversation',
      await dialog.locator('a[href="'+report(2)+'"]').count()===1&&
      await page.locator('a[href="'+report(1)+'"]').count()===0);
  }else{
    await openDrawer();
    check('the saved-version field is one accessible action with two deliveries',
      await versionControl.count()===1&&(await versionControl.innerText()).trim()==='版本2'&&
      await versionControl.getAttribute('aria-haspopup')==='dialog');
    const history='**'+task+'/report-history';
    let release,notify;
    const held=new Promise(resolve=>{release=resolve}),seen=new Promise(resolve=>{notify=resolve});
    const gate=async route=>{notify();await held;await route.continue()};
    await page.route(history,gate);
    try{
      await versionControl.press('Enter');
      await seen;
      check('one version lookup is in progress',await versionControl.getAttribute('aria-busy')==='true');
      await closeDrawer();
      const returned=page.waitForResponse(response=>new URL(response.url()).pathname===task+'/report-history');
      release();
      await (await returned).finished();
      check('closing the drawer cancels the pending version dialog',await dialog.isHidden());
    }finally{release();await page.unroute(history,gate)}
    await openDrawer();
    await versionControl.press('Enter');
    await dialog.locator('.report-version-link').first().waitFor();
    check('version dialog replaces the narrow drawer and receives keyboard focus',await drawer.isHidden()&&
      await dialog.evaluate(el=>el.contains(document.activeElement)));
    const links=dialog.locator('.report-version-link');
    check('each saved version has a direct standard-report link in descending order',await links.evaluateAll(
      (nodes,paths)=>nodes.length===paths.length&&nodes.every((node,i)=>node.getAttribute('href')===paths[i]&&!node.hasAttribute('data-report-version')),
      [report(2),report(1)]));
    for(const version of [2,1]){
      const opened=page.waitForEvent('popup');
      await dialog.getByRole('link',{name:'阅读版本'+version,exact:true}).click();
      const reader=await opened;
      try{
        await reader.locator('body[data-report-variant=standard] main').waitFor();
        const location=new URL(reader.url());
        check('sales opens the exact standard report version '+version,location.pathname===report(version)&&!location.search);
      }finally{await reader.close()}
    }
    await page.keyboard.press('Escape');
    await dialog.waitFor({state:'hidden'});
    check('closing history returns focus to the delivery control in ResChat',await opener.evaluate(el=>document.activeElement===el)&&
      await page.locator('#artifacts').isHidden()&&await page.locator('button[data-mode=desk]').isHidden());
    check('the detailed report remains protected by the service',(await get(report(1)+'?profile=detailed')).status()===403);
    await page.reload();
    await page.waitForFunction(id=>workspaceReady&&selected?.id===id,id);
    await openDrawer();
    check('the history action survives reload and keeps the current report card',
      await versionControl.isVisible()&&await card.getAttribute('href')===report(2));
  }
  check('no script errors or external requests',record.errors.length===0&&record.externalRequests===0);
  record.status='passed';
}catch(error){record.error=error.stack;process.exitCode=1}
finally{
  await browser.close();
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
}
