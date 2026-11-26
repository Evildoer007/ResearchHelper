/* Recover task actions through the real local service after a lost response. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(
  path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,baseline,ids}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],errors:[],externalRequests:0};
const check=(name,ok)=>{assert.ok(ok,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['blob:','data:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const endpoint=id=>'/api/tasks/'+id;
  const get=async resource=>{
    const response=await context.request.get(new URL(resource,origin).href);
    assert.ok(response.ok(),await response.text());return response.json();
  };
  const {token}=await get('/api/auth/status'), headers={'X-ResearchHelper-Token':token};
  assert.ok((await context.request.post(new URL('/api/auth/login',origin).href,{
    headers,data:{account:'admin',password:'66666666'}})).ok());
  const page=await context.newPage();page.setDefaultTimeout(8500);
  page.on('pageerror',error=>record.errors.push(error.message));
  const requests=[];
  page.on('request',request=>{
    const route=new URL(request.url()).pathname;
    if(route.startsWith('/api/tasks/'))requests.push({route,method:request.method()});
  });
  const writes=(id,action)=>requests.filter(row=>row.method==='POST'&&row.route===endpoint(id)+'/'+action).length;
  const reads=id=>requests.filter(row=>row.method==='GET'&&row.route===endpoint(id)).length;
  const state=()=>page.evaluate(()=>selected);
  const settled=()=>page.waitForFunction(()=>pendingTaskActions.size===0);
  const select=async id=>{
    await page.locator('#task-list [data-task="'+id+'"]').click();
    await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,id);
  };
  const button=action=>page.locator('#chat .rh-process [data-action='+action+']');
  const waitTask=async(id,test)=>{
    for(let i=0;i<80;i++){
      const task=await get(endpoint(id));
      if(test(task))return task;
      await new Promise(resolve=>setTimeout(resolve,40));
    }
    throw Error('Authoritative task did not settle');
  };
  const lost=async(id,action,control,delay)=>{
    const pattern='**'+endpoint(id)+'/'+action;
    let accepted=false;
    const drop=async route=>{
      const result=await route.fetch();
      assert.ok(result.ok(),await result.text());accepted=true;
      if(delay)await delay();
      await route.abort('connectionreset');
    };
    await page.route(pattern,drop);
    try{await control.click();await settled();assert.ok(accepted)}
    finally{await page.unroute(pattern,drop)}
  };
  const detailToggle=page.locator('.web-control[data-action=artifacts]');
  const updateButton=page.locator('#task-actions [data-action=update]');
  const openDetails=async()=>{if(await page.locator('#task-context').isHidden())await detailToggle.click()};
  const closeDetails=async()=>{if(await page.locator('#task-context').isVisible())await page.locator('#task-context [data-action=close-context]').click()};

  await page.goto(base);
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  await select(ids.start);
  const startReads=reads(ids.start);
  await lost(ids.start,'start',button('start'));
  const started=await waitTask(ids.start,task=>!!task.delivery);
  check('the real service started and delivered once',started.batch===1&&started.version===1&&writes(ids.start,'start')===1);
  if(baseline){
    check('lost start response leaves the workbench showing the old unstarted task',
      (await state()).status==='待开始'&&await button('start').isEnabled()&&reads(ids.start)===startReads);
  }else{
    await page.waitForFunction(()=>!!selected.delivery);
    check('start readback restores the real task and starts watching its progress',
      reads(ids.start)>startReads&&(await state()).delivery.digest===started.delivery.digest&&await button('start').count()===0);
  }

  await select(ids.retry);
  const retryReads=reads(ids.retry);
  await lost(ids.retry,'retry-save',button('retry-save'));
  const saved=await get(endpoint(ids.retry));
  check('retry-save persists the accepted report exactly once',saved.delivery.version===1&&saved.batch===1&&
    writes(ids.retry,'retry-save')===1&&saved.ledger.used.model_calls===2);
  if(baseline){
    check('lost save response leaves an already saved report behind the retry button',
      !(await state()).delivery&&await button('retry-save').isEnabled()&&reads(ids.retry)===retryReads);
    await select(ids.update);await openDetails();
    check('the saved research has no explicit update action',await updateButton.count()===0);
  }else{
    check('retry-save shows the frozen report without asking for a second save',
      (await state()).delivery.digest===saved.delivery.digest&&await button('retry-save').count()===0&&reads(ids.retry)>retryReads);

    await select(ids.resume);
    let entered,release;
    const accepted=new Promise(resolve=>entered=resolve),gate=new Promise(resolve=>release=resolve);
    try{
      const recovering=lost(ids.resume,'resume',button('resume'),async()=>{entered();await gate});
      await accepted;
      await select(ids.uncommitted);
      await page.locator('#chat-input').fill('留在另一任务的未发送问题');
      release();await recovering;
      check('a lost resume receipt refreshes its own task without hijacking another selection',
        (await state()).id===ids.uncommitted&&await page.locator('#chat-input').inputValue()==='留在另一任务的未发送问题'&&
        await page.evaluate(id=>tasks.find(task=>task.id===id)?.batch===2,ids.resume)&&writes(ids.resume,'resume')===1);
    }finally{release()}

    const uncommittedPattern='**'+endpoint(ids.uncommitted)+'/start';
    const reject=route=>route.abort('connectionreset');
    await page.route(uncommittedPattern,reject);
    const uncommittedReads=reads(ids.uncommitted);
    await button('start').focus();await page.keyboard.press('Enter');await settled();
    check('an uncommitted request remains retryable after one read and one write attempt',
      (await get(endpoint(ids.uncommitted))).batch===0&&(await state()).status==='待开始'&&
      writes(ids.uncommitted,'start')===1&&reads(ids.uncommitted)===uncommittedReads+1&&await button('start').isEnabled()&&
      await button('start').evaluate(el=>el===document.activeElement));
    await page.unroute(uncommittedPattern,reject);

    await select(ids.mismatch);
    const wrong=await state(),mismatchPattern='**'+endpoint(ids.mismatch)+'/start';
    const staleSuccess=async route=>{
      const result=await route.fetch();assert.ok(result.ok());
      return route.fulfill({json:wrong});
    };
    await page.route(mismatchPattern,staleSuccess);
    await button('start').click();await settled();
    await page.waitForFunction(()=>!!selected.delivery);
    check('same-task success body with an old batch is replaced by a real read',
      (await state()).batch===1&&writes(ids.mismatch,'start')===1);
    await page.unroute(mismatchPattern,staleSuccess);

    await select(ids.conflict);
    const conflictPattern='**'+endpoint(ids.conflict)+'/start';
    let rejected=false;
    const otherWindow=async route=>{
      const payload=route.request().postDataJSON();
      const first=await context.request.post(new URL(endpoint(ids.conflict)+'/start',origin).href,{headers,data:payload});
      assert.ok(first.ok(),await first.text());
      const stale=await route.fetch();assert.equal(stale.status(),400);rejected=true;
      return route.fulfill({response:stale});
    };
    await page.route(conflictPattern,otherWindow);
    await button('start').click();await settled();
    await page.waitForFunction(()=>!!selected.delivery);
    check('a stale write rejection loads the batch that another window already started',
      rejected&&(await state()).batch===1&&writes(ids.conflict,'start')===1);
    await page.unroute(conflictPattern,otherWindow);

    await select(ids.cancel);
    await lost(ids.cancel,'cancel',button('cancel'));
    check('lost cancel acknowledgement converges on the same cancelled batch',
      (await state()).status==='已取消'&&(await state()).batch===1&&
      (await get(endpoint(ids.cancel))).status==='已取消'&&writes(ids.cancel,'cancel')===1);

    await select(ids.update);
    const firstReport=new URL(endpoint(ids.update)+'/report/1',origin).href;
    const frozen=await (await context.request.get(firstReport)).text();
    await openDetails();
    check('a saved task offers one explicit update in task and delivery',
      await updateButton.isVisible()&&await page.locator('[data-action=update]').count()===1);
    await lost(ids.update,'update',updateButton);
    await page.waitForFunction(()=>selected?.version===2&&selected.delivery?.version===2);
    const latest=await state();
    check('lost update response follows the new version and preserves the original report',
      latest.batch===1&&writes(ids.update,'update')===1&&frozen===await (await context.request.get(firstReport)).text());
    await closeDetails();
    await page.reload();
    await page.waitForFunction(id=>workspaceReady&&selected?.id===id&&selected?.delivery?.version===2,ids.update);
    check('refresh keeps the same delivered version without an automatic write',
      (await state()).delivery.digest===latest.delivery.digest&&writes(ids.update,'update')===1);

    const sales=await browser.newContext({viewport:{width:1320,height:860}});
    try{
      await sales.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.origin===origin.origin||['blob:','data:'].includes(url.protocol))return route.continue();
        record.externalRequests++;return route.abort();
      });
      assert.ok((await sales.request.post(new URL('/api/auth/login',origin).href,{
        headers,data:{account:'sales',password:'88888888'}})).ok());
      const salesPage=await sales.newPage();salesPage.on('pageerror',error=>record.errors.push(error.message));
      await salesPage.goto(base);await salesPage.locator('[data-workspace-shell][data-initializing=false]').waitFor();
      await salesPage.locator('#task-list [data-task="'+ids.sales+'"]').click();
      await salesPage.waitForFunction(id=>selected?.id===id&&!selectingTaskId,ids.sales);
      await salesPage.locator('.web-control[data-action=artifacts]').click();
      check('sales can explicitly update its own saved task from ResChat',
        await salesPage.locator('#task-actions [data-action=update]').isVisible()&&
        await salesPage.locator('[data-mode=desk]').isHidden()&&await salesPage.locator('[data-task="'+ids.update+'"]').count()===0);
    }finally{await sales.close()}
  }
  check('no external requests and no script errors',record.externalRequests===0&&record.errors.length===0);
  record.status='passed';
}catch(error){record.error=String(error.stack);process.exitCode=1}
finally{
  await browser.close();record.browserClosed=true;
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
