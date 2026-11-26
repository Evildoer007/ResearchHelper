/* Real task metadata and UI with controlled local response failures. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(
  path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,baseline}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],errors:[],externalRequests:0};
const check=(name,ok)=>{assert.ok(ok,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1320,height:860},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['blob:','data:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const get=async resource=>{
    const response=await context.request.get(new URL(resource,origin).href);
    assert.ok(response.ok());return response.json();
  };
  const auth=await get('/api/auth/status');
  const headers={'X-ResearchHelper-Token':auth.token};
  const post=async (resource,data)=>{
    const response=await context.request.post(new URL(resource,origin).href,{headers,data});
    assert.ok(response.ok(),await response.text());return response.json();
  };
  await post('/api/auth/login',{account:'admin',password:'66666666'});
  const original='用于检查任务名称保存的离线研究';
  const task=await post('/api/tasks',{question:original});
  const resource='/api/tasks/'+task.id+'/presentation',pattern='**'+resource;
  const page=await context.newPage();
  page.setDefaultTimeout(9000);
  page.on('pageerror',error=>record.errors.push(error.message));
  await page.goto(base);
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  const row=page.locator('#task-list [data-task="'+task.id+'"]');
  const trigger=page.locator('#task-list [data-menu-task="'+task.id+'"]');
  const editor=page.locator('#task-name'),feedback=page.locator('#task-name-feedback');
  const save=page.locator('#task-rename-form button[type=submit]');
  const dialog=page.locator('#dialog');
  const composer=page.locator('#chat-input');
  if(!baseline){
    check('closed task rows contain no menu action buttons',await page.locator('.task-menu__action').count()===0);
    await row.hover();
    await trigger.click();
    check('pointer opens just the selected menu and keeps trigger focus',
      await page.getByRole('menuitem').count()===3&&await trigger.evaluate(el=>el===document.activeElement));
    await page.keyboard.press('End');
    check('End moves to the final menu action without closing the menu',
      await page.getByRole('menuitem',{name:'归档任务',exact:true}).evaluate(el=>el===document.activeElement));
    await page.keyboard.press('Escape');
    check('Escape closes the menu and restores trigger focus',await page.getByRole('menuitem').count()===0&&
      await trigger.evaluate(el=>el===document.activeElement&&el.getAttribute('aria-expanded')==='false'));
    await trigger.press('Enter');
    check('keyboard activation focuses the first action',await page.getByRole('menuitem',{name:'置顶任务',exact:true})
      .evaluate(el=>el===document.activeElement)&&await page.locator('.task-menu__action').count()===3);
    await page.keyboard.press('Escape');
  }
  const choose=async label=>{
    await trigger.press('ArrowDown');
    await page.getByRole('menuitem',{name:label,exact:true}).click();
  };
  const rename=async name=>{await choose('重命名');await editor.fill(name);await save.click()};
  const waitFeedback=()=>feedback.filter({hasText:/\S/}).waitFor();
  const title=async()=>({header:await page.locator('#task-title').textContent(),
    row:await row.locator('.task-item__text').textContent()});
  const displayed=async name=>{const current=await title();return current.row===name&&current.header===name};
  const posted=[];
  page.on('request',request=>{
    if(request.method()==='POST'&&new URL(request.url()).pathname===resource)posted.push(request.postDataJSON());
  });
  await row.click();
  await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,task.id);
  const unsent='留在原任务里的追问';
  await composer.fill(unsent);

  const committedName='已写入档案的任务名称';
  let writes=0;
  const lostResponse=async route=>{
    if(route.request().method()!=='POST')return route.continue();
    const response=await route.fetch();
    assert.ok(response.ok());writes++;
    return route.abort('connectionreset');
  };
  await page.route(pattern,lostResponse);
  await rename(committedName);
  if(baseline)await waitFeedback();else await dialog.waitFor({state:'hidden'});
  const committed=await get(resource);
  check('server committed one rename despite the lost response',
    writes===1&&posted.length===1&&committed.title===committedName&&committed.revision===1);
  if(baseline){
    const current=await title();
    record.beforeRename={...current,feedback:await feedback.innerText()};
    check('installed task shows contradictory names and an open failed rename dialog',
      current.header===original&&current.row===committedName&&await dialog.isVisible()&&
        (await feedback.innerText()).trim().length>0);
    await page.keyboard.press('Escape');
  }else{
    check('lost receipt is reconciled as success and both task names update',await displayed(committedName)&&
      (await page.locator('#notice').innerText()).includes('已保存')&&await composer.inputValue()===unsent);
    check('rename returns focus to its task action',await trigger.evaluate(button=>button===document.activeElement));
  }
  await page.unroute(pattern,lostResponse);

  const otherName=baseline?'另一窗口写入的任务名称':'另一窗口写入的任务'+'W'.repeat(105);
  const requested='我在此窗口编辑的新名称';
  await choose('重命名');
  await editor.fill(requested);
  await post(resource,{expected_revision:1,changes:{title:otherName}});
  if(!baseline)await page.setViewportSize({width:390,height:844});
  await save.click();await waitFeedback();
  const conflict=await get(resource);
  check('stale rename does not overwrite another window',conflict.revision===2&&conflict.title===otherName&&
    await editor.inputValue()===requested);
  if(baseline){
    await save.click();await waitFeedback();
    const unchanged=await get(resource);
    check('installed rename resends the same stale revision and cannot save',
      posted.slice(-2).every(attempt=>attempt.expected_revision===1)&&unchanged.title===otherName&&await dialog.isVisible());
  }else{
    check('conflict shows the saved name and keeps the unsaved input',
      await displayed(otherName)&&(await feedback.innerText()).includes(otherName)&&await editor.inputValue()===requested);
    check('the full conflicting name wraps at mobile width',
      await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.setViewportSize({width:1320,height:860});
    await save.click();await dialog.waitFor({state:'hidden'});
    const settled=await get(resource);
    check('only the explicit second save uses the new revision',
      posted.slice(-2).map(attempt=>attempt.expected_revision).join(',')==='1,2'&&
      settled.revision===3&&settled.title===requested&&await displayed(requested));

    const badReceiptName='需要等实际保存的名字';
    const badReceipt=route=>route.request().method()==='POST'
      ?route.fulfill({json:{title:badReceiptName,pinned:false,archived:false,revision:4,thread_id:'other-task'}})
      :route.continue();
    await page.route(pattern,badReceipt);
    await rename(badReceiptName);await waitFeedback();
    check('a mismatched receipt never displays an uncommitted title',
      await displayed(requested)&&await editor.inputValue()===badReceiptName&&
      (await get(resource)).revision===3&&!((await feedback.innerText()).includes('已保存')));
    await page.unroute(pattern,badReceipt);
    await page.keyboard.press('Escape');

    const beforePin=posted.length;
    await page.route(pattern,lostResponse);
    await choose('置顶任务');
    await row.getByRole('img',{name:'已置顶',exact:true}).waitFor();
    const pinned=await get(resource);
    check('pin restores confirmed state without a second write',pinned.pinned&&pinned.revision===4&&
      posted.length===beforePin+1&&(await page.locator('#notice').innerText()).includes('置顶状态已保存'));
    await page.unroute(pattern,lostResponse);

    const beforeArchive=posted.length;
    await page.route(pattern,lostResponse);
    await choose('归档任务');
    await row.waitFor({state:'detached'});
    await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('已归档'));
    const archived=await get(resource);
    check('archive reconciles its saved state and does not replay',archived.archived&&archived.revision===5&&
      posted.length===beforeArchive+1&&await composer.inputValue()===unsent);
    await page.unroute(pattern,lostResponse);

    await page.locator('[data-account-menu-toggle]').click();
    await page.locator('#account-menu [data-action=archived-tasks]').click();
    await row.waitFor();
    await choose('恢复任务');
    await row.waitFor({state:'detached'});
    await page.locator('[data-account-menu-toggle]').click();
    await page.locator('#account-menu [data-action=archived-tasks]').click();
    await row.waitFor();
    const restored=await get(resource);
    check('restore returns the same task to the regular list',!restored.archived&&restored.pinned&&restored.revision===6&&
      await displayed(requested)&&await composer.inputValue()===unsent);
    await page.reload();
    await page.waitForFunction(id=>workspaceReady&&selected?.id===id,task.id);
    check('reload preserves confirmed name and the draft on the same task',await displayed(requested)&&
      await composer.inputValue()===unsent);
  }
  const research=await get('/api/tasks/'+task.id);
  check('presentation never alters the study input or dispatches research',
    research.question===original&&research.revision===task.revision&&research.ledger.used.model_calls===0&&research.ledger.used.data_requests===0);
  check('isolated browser issued no external requests or script errors',record.externalRequests===0&&record.errors.length===0);
  record.status='passed';
}catch(error){record.error=String(error.stack);process.exitCode=1}
finally{
  await browser.close();record.browserClosed=true;
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
