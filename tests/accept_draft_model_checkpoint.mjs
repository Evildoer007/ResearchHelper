/* Run the built workspace against a completed reply saved before service restart. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(
  path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let wire='';
for await(const chunk of process.stdin)wire+=chunk;
const {base,output,fixture}=JSON.parse(wire),origin=new URL(base);
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
  const status=await (await context.request.get(new URL('/api/auth/status',origin).href)).json();
  assert.ok((await context.request.post(new URL('/api/auth/login',origin).href,{
    headers:{'X-ResearchHelper-Token':status.token},data:{account:'admin',password:'66666666'}})).ok());
  const page=await context.newPage();
  page.on('pageerror',error=>record.errors.push(error.message));
  await page.goto(base);
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  await page.locator('#task-list [data-task="'+fixture.task_id+'"]').click();
  await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,fixture.task_id);
  const before=await page.evaluate(()=>({task:selected,offline:boot.offline}));
  const retry=page.locator('#chat-form .send-control[data-task-operation=retry-save]');
  check('restart identifies the accepted reply as local save work',before.offline&&before.task.status==='失败'&&
    before.task.has_retained_submission===true&&before.task.version===1&&before.task.batch===1&&
    await retry.isVisible()&&await page.locator('#chat .rh-process button').count()===0);
  const mutations=[];
  page.on('request',request=>{
    if(request.method()==='POST'&&new URL(request.url()).pathname.includes('/api/tasks/'))
      mutations.push(new URL(request.url()).pathname);
  });
  await retry.click();
  await page.waitForFunction(()=>!!selected?.delivery&&selected.status==='部分完成');
  const after=await page.evaluate(()=>selected);
  check('retry saves the same report version and batch',after.id===fixture.task_id&&after.version===1&&
    after.batch===1&&after.cutoff===fixture.cutoff&&after.delivery.version===1);
  check('only local retry-save was dispatched',
    JSON.stringify(mutations)===JSON.stringify(['/api/tasks/'+fixture.task_id+'/retry-save']));
  check('local save preserves the exact accepted conclusion and all paid usage',
    after.draft_report.conclusion===fixture.conclusion&&after.ledger.used.model_calls===fixture.model_calls&&
    JSON.stringify(after.ledger.dispatches)===JSON.stringify(before.task.ledger.dispatches)&&
    after.ledger.used.data_requests===(fixture.data_requests??0));
  if(fixture.data_requests){
    check('recovered interval uses the original two observations and return',
      JSON.stringify(after.draft_report.facts.map(fact=>fact.value))==='[112,116]'&&
      after.draft_report.calculations.some(row=>row.display==='3.57'));
  }
  const popup=page.waitForEvent('popup');
  await page.locator('#chat a.artifact-card').click();
  const preview=await popup;
  try{
    const title=preview.locator('h1');
    await title.waitFor();
    check('offline workspace opens the frozen recovered report without leaving Chat',
      await title.innerText()===fixture.title&&await page.locator('#chat-surface').isVisible());
  }finally{await preview.close()}
  await page.reload();
  await page.waitForFunction(id=>workspaceReady&&selected?.id===id&&!!selected.delivery,fixture.task_id);
  check('reload restores the same saved report without another retry',
    await page.evaluate(digest=>selected.delivery.digest===digest,after.delivery.digest)&&mutations.length===1);
  check('no external requests and no script errors',record.externalRequests===0&&record.errors.length===0);
  record.status='passed';
}catch(error){record.error=String(error.stack);process.exitCode=1}
finally{
  await browser.close();record.browserClosed=true;
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
