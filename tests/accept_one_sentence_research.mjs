/* One normal ResChat submission. No prepared plan, evidence, or task retry. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL);
const output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
const phaseProfile=JSON.parse(process.env.RESEARCHHELPER_PHASE_PROFILE||'{"extraction_mode":"direct"}');
const question=process.env.RESEARCHHELPER_SUBMISSION_QUESTION||'研究贵州茅台2026年半年报的盈利质量，计算净利润率及经营现金流对净利润的覆盖，生成一份带图表和数据来源的报告。';
assert.ok(question.length>0&&question.length<1000);
assert.equal(base.hostname,'127.0.0.1');
assert.ok(path.basename(output).startsWith('one-sentence-research-'));
const record={status:'starting',headless:true,nativeWindowOpened:false,checks:[],
  phaseHistory:[],task_id:null,financial_quality_accepted:false,temporary_extraction_override:true,
  production_settings_modified:false,creationRequests:0,startRequests:0};
const persist=()=>writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
const check=(name,value)=>{assert.equal(value,true,name);record.checks.push(name)};
const binding=task=>Object.fromEntries(['id','version','batch','cutoff','input_hash'].map(key=>[key,task[key]]));
let browser,context,page,boot,task;
await persist();
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  // External network remains available. UI writes are limited to one create/start.
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin===base.origin&&request.method()==='POST'){
      if(url.pathname==='/api/tasks'&&++record.creationRequests>1){await persist();return route.abort()}
      if(/^\/api\/tasks\/[^/]+\/start$/.test(url.pathname)&&++record.startRequests>1){await persist();return route.abort()}
    }
    return route.continue();
  });
  page=await context.newPage();
  page.on('pageerror',()=>{record.browser_error_count=(record.browser_error_count||0)+1});
  await page.goto(base.href);
  await page.locator('#login-account').fill('admin');
  await page.locator('#login-password').fill('66666666');
  await page.locator('#login-submit').click();
  await page.locator('#chat-input').waitFor();
  boot=await (await context.request.get(new URL('/api/bootstrap',base).href)).json();
  check('temporary execution policy enabled',boot.execution.mode==='live');
  check('saved services copied into temporary Store',boot.research_readiness.model_configured&&boot.source_settings.ifind.has_refresh_token);
  check('temporary extraction override explicit',boot.model_settings.extraction_mode==='direct');
  check('explicit isolated phase profile applied',Object.entries(phaseProfile).every(([key,value])=>boot.model_settings[key]===value));
  check('temporary Store is empty',boot.tasks.length===0);
  await page.locator('.rail [data-action=new]').click();
  await page.waitForFunction(()=>selected===null);
  const creation=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/tasks'&&response.request().method()==='POST');
  const start=page.waitForResponse(response=>/^\/api\/tasks\/[^/]+\/start$/.test(new URL(response.url()).pathname)&&response.request().method()==='POST');
  await page.locator('#chat-input').fill(question);
  await page.locator('#chat-form button[type=submit]').click();
  const created=await creation;
  assert.equal(created.status(),200);
  task=await created.json();record.task_id=task.id;await persist();
  record.submitted=created.request().postDataJSON();
  const submitted=record.submitted;
  check('unaltered single natural question',submitted.question===question&&task.question===question);
  check('ordinary default scope',submitted.entry==='混合'&&submitted.intent==='综合研究'&&submitted.objects.length===0);
  check('no prepared plan or evidence',!('questions' in submitted)&&task.question_origin==='default'&&task.evidence.length===0);
  check('not a synthetic case',task.synthetic===false&&!task.fixture);
  check('current research mode',submitted.research_mode==='current'&&!('cutoff' in submitted));
  assert.deepEqual(submitted.limits,{data_requests:100,model_calls:8,seconds:600});
  const started=await start;assert.equal(started.status(),200);
  record.start=started.request().postDataJSON();record.status='executing';await persist();
  check('start bound to newly created task',Object.entries(binding(task)).every(([key,value])=>record.start.expected_task[key]===value));
  check('start bound to temporary execution policy',record.start.expected_execution.mode===boot.execution.mode&&
    record.start.expected_execution.policy_version===boot.execution.policy_version);
  const route=new URL('/api/tasks/'+task.id,base).href;
  const deadline=Date.now()+610000;
  let lastState='';
  while(true){
    try{
      const response=await context.request.get(route,{timeout:10000});assert.equal(response.status(),200);
      task=await response.json();record.research_status=task.status;
      const event=task.ledger.dispatches.at(-1);
      const state=JSON.stringify([task.status,task.batch,event?.kind,event?.research_phase,event?.stage,event?.status]);
      if(state!==lastState){
        lastState=state;
        record.phaseHistory.push({at:new Date().toISOString(),status:task.status,batch:task.batch,
          kind:event?.kind,phase:event?.research_phase,stage:event?.stage,event_status:event?.status,used:task.ledger.used});
        await persist();
      }
      if(['已完成','部分完成','失败','已取消'].includes(task.status))break;
    }catch{record.observation_errors=(record.observation_errors||0)+1;await persist()}
    if(Date.now()>deadline){record.observation_deadline_reached=true;throw Error('observation_deadline')}
    await new Promise(resolve=>setTimeout(resolve,1500));
  }
  record.ledger=task.ledger;
  await page.waitForFunction(({id,status})=>selected?.id===id&&selected?.status===status,
    {id:task.id,status:task.status},{timeout:12000});
  await page.screenshot({path:path.join(output,'reschat.png'),animations:'disabled'});
  check('exactly one creation and start',record.creationRequests===1&&record.startRequests===1);
  record.status='collected_for_independent_review';
}catch(error){record.status='collection_failed';record.error_type=error.name}
finally{
  if(context&&record.task_id){
    try{
      const route=new URL('/api/tasks/'+record.task_id,base).href;
      task=await (await context.request.get(route,{timeout:15000})).json();
      if(task.status==='运行中'){
        const stopped=await context.request.post(route+'/cancel',{data:{expected_task:binding(task)},
          headers:{'X-ResearchHelper-Token':boot.token},timeout:15000});
        record.cancelled_after_observation_error=stopped.ok();
      }
    }catch{record.final_task_inspection_failed=true}
  }
  await persist();
  if(browser)await browser.close();
}
if(record.status==='collection_failed')process.exitCode=1;
