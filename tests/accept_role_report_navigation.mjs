import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(path.join(process.env.HOME,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,baseline,ids}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],errors:[],externalRequests:0};
const check=(name,condition)=>{assert.ok(condition,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});

try{
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce',acceptDownloads:true});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['blob:','data:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const page=await context.newPage();
  page.setDefaultTimeout(9000);
  page.on('pageerror',error=>record.errors.push(error.message));
  const get=resource=>context.request.get(new URL(resource,origin).href);
  async function json(resource){
    const response=await get(resource);
    assert.ok(response.ok(),await response.text());
    return response.json();
  }
  const endpoint=id=>'/api/tasks/'+id;
  const saved=(id,version)=>endpoint(id)+'/report/'+version;
  const chat=page.locator('#chat'),desk=page.locator('#artifacts');
  const mode=name=>page.locator('[data-mode-switch] [data-mode='+name+']');
  const selectedTask=id=>page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,id);
  async function login(role){
    await page.locator('#login-account').fill(role);
    await page.locator('#login-password').fill(role==='admin'?'66666666':'88888888');
    await page.locator('#login-submit').click();
    await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
    const status=await json('/api/auth/status');
    check(role+' local login',status.authenticated&&status.role===role&&status.account===role);
    return {'X-ResearchHelper-Token':status.token};
  }
  async function select(id){
    await page.locator('#task-list [data-task="'+id+'"]').click();
    await selectedTask(id);
  }
  async function menu(action){
    await page.locator('[data-account-menu-toggle]').click();
    await page.locator('#account-menu [data-action='+action+']').click();
  }
  async function reportFrame(id,version){
    const frame=page.locator('#report-frame[src^="'+saved(id,version)+'?"]');
    await frame.waitFor();
    return frame;
  }
  async function previewStandard(id,version){
    const popup=page.waitForEvent('popup');
    await chat.locator('a.artifact-card').click();
    const document=await popup;
    try{
      await document.locator('h1:visible').first().waitFor();
      const url=new URL(document.url());
      return url.pathname===saved(id,version)&&!url.search&&await desk.isHidden();
    }finally{await document.close()}
  }
  const tab=index=>page.locator('#view-'+index);
  await page.goto(base);
  const adminHeaders=await login('admin');
  const boot=await json('/api/bootstrap');
  check('offline and account-owned tasks',boot.offline===true&&boot.tasks.some(task=>task.id===ids.admin)&&
    boot.tasks.every(task=>task.owner_account==='admin'));
  await select(ids.admin);
  if(await page.locator('#chat-surface').isHidden())await mode('chat').click();
  check('the chat answer has no duplicate Desk workpaper shortcut',await chat.locator('[data-stage="2"]').count()===0);
  await mode('desk').click();await tab(2).press('Enter');
  await tab(2).waitFor();
  check('admin opens the same task in the Desk workpaper and focuses its tab',
    await tab(2).getAttribute('aria-selected')==='true'&&
    await tab(2).evaluate(el=>el===document.activeElement)&&await page.evaluate(id=>selected.id===id,ids.admin));
  for(const index of [0,4]){
    await tab(index).click();
    check('admin reaches related view '+index+' from the Desk tabs',
      await tab(index).getAttribute('aria-selected')==='true'&&await page.evaluate(id=>selected.id===id,ids.admin));
  }
  await mode('chat').click();
  check('admin report card opens its displayed standard edition without switching modes',await previewStandard(ids.admin,1));
  await select(ids.adminGap);
  check('Chat keeps a readable gap without a workpaper command',await chat.getByText('待补充',{exact:true}).count()>=1&&
    await chat.locator('[data-gap-question]').count()===0);

  const first=saved(ids.admin,1),original=await (await get(first)).text();
  const row=page.locator('#dialog [data-task="'+ids.admin+'"]');
  await menu('library');
  check('library row advertises version 1 before the update',(await row.innerText()).includes('版本1'));
  const snapshot=await json(endpoint(ids.admin));
  const expected_task=Object.fromEntries(['id','version','batch','cutoff','input_hash'].map(key=>[key,snapshot[key]]));
  const expected_execution={mode:boot.execution.mode,policy_version:boot.execution.policy_version};
  const update=await context.request.post(base.slice(0,-1)+endpoint(ids.admin)+'/update',{
    headers:adminHeaders,data:{expected_task,expected_execution}});
  assert.ok(update.ok(),await update.text());
  let latest;
  for(let attempt=0;attempt<100;attempt++){
    latest=await json(endpoint(ids.admin));
    if(latest.delivery?.version===2)break;
    await new Promise(resolve=>setTimeout(resolve,40));
  }
  check('same-account update delivered version 2 offline',latest?.delivery?.version===2&&
    latest.ledger.used.model_calls===0&&latest.ledger.used.data_requests===0);

  if(!baseline){
    const history='**'+endpoint(ids.admin)+'/report-history';
    await page.route(history,async route=>{
      const response=await route.fetch();
      const data=await response.json();
      await route.fulfill({response,json:{...data,reports:data.reports.filter(edition=>edition.version!==1)}});
    });
    await row.click();
    await page.locator('#notice.show').waitFor();
    check('missing advertised edition keeps the prior task and never opens version 2',
      (await page.locator('#notice').innerText()).includes('版本1')&&await desk.isHidden()&&
      await page.evaluate(id=>selected.id===id,ids.adminGap));
    await page.unroute(history);
    await menu('library');
  }
  await row.click();
  const frame=await reportFrame(ids.admin,baseline?2:1);
  if(baseline){
    check('reproduced library version 1 opening version 2',(await frame.getAttribute('src')).includes('/report/2?'));
  }else{
    const request=await get(await frame.getAttribute('src'));
    assert.ok(request.ok());
    const downloaded=page.waitForEvent('download');
    await page.locator('#content [data-action=export]').click();
    const file=path.join(output,'version-1.html');
    await (await downloaded).saveAs(file);
    check('library opens and exports the advertised version 1 after version 2 exists',
      await readFile(file,'utf8')===await request.text()&&await (await get(first)).text()===original);
  }
  await mode('chat').click();
  check('current chat card still announces the current delivery',
    (await chat.locator('.artifact-card').getAttribute('aria-label')).includes('版本2'));
  check('admin opens the latest advertised report without changing Desk',await previewStandard(ids.admin,2));

  await menu('logout');
  await page.waitForURL(url=>url.pathname==='/login/index.html'&&!url.searchParams.has('account'));
  await login('sales');
  await select(ids.sales);
  check('sales keeps only ResChat and its own report card',await mode('desk').isHidden()&&await desk.isHidden()&&
    await chat.locator('.chat-links,.message__action--workpaper,button.artifact-card').count()===0&&await chat.locator('a.artifact-card').count()===1&&
    (await json('/api/bootstrap')).tasks.every(task=>task.owner_account==='sales'));
  check('sales opens its frozen standard report without navigating ResDesk',await previewStandard(ids.sales,1));
  check('service denies detailed and other-account reports',
    (await get(saved(ids.sales,1)+'?profile=detailed')).status()===403&&(await get(saved(ids.admin,1))).status()===403);
  await select(ids.salesGap);
  check('sales gaps are readable without a Desk-only button',await chat.getByText('待补充',{exact:true}).count()>=1&&
    await chat.locator('[data-gap-question]').count()===0&&await desk.isHidden());
  check('no browser errors and no external calls',record.errors.length===0&&record.externalRequests===0);
  record.status='passed';
}catch(error){
  record.error=error.stack;process.exitCode=1;
}finally{
  await browser.close();
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify(record));
}
