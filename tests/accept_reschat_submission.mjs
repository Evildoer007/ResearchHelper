/* Real input and archive APIs; stop before inference, with no fake result. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL);
const output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
const question=process.env.RESEARCHHELPER_SUBMISSION_QUESTION;
assert.equal(base.hostname,'127.0.0.1');
assert.ok(question?.includes('不作买卖建议'));
const record={status:'failed',checks:[],cases:[],errors:[],externalRequests:0,
  headless:true,modelCalls:0,dataRequests:0,researchAccepted:false};
const check=(name,passed)=>{assert.equal(passed,true,name);record.checks.push(name)};
let browser;

try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  for(const [width,height] of [[1440,900],[390,844]]){
    const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'});
    await context.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin===base.origin||['data:','blob:'].includes(url.protocol))route.continue();
      else{record.externalRequests++;route.abort()}
    });
    const page=await context.newPage();
    page.on('pageerror',error=>record.errors.push(error.message));
    const creates=[],uploads=[],starts=[];
    page.on('request',request=>{
      const url=new URL(request.url());
      if(request.method()!=='POST'||url.origin!==base.origin)return;
      if(url.pathname==='/api/tasks')creates.push(request.postDataJSON());
      if(/^\/api\/tasks\/[^/]+\/material$/.test(url.pathname))uploads.push(url.pathname);
    });
    await page.route('**/api/tasks/*/start',async route=>{
      const request=route.request(),url=new URL(request.url());
      const response=await context.request.get(new URL(url.pathname.replace(/\/start$/,''),base).href);
      assert.equal(response.status(),200);
      starts.push({input:request.postDataJSON(),task:await response.json(),uploads:uploads.length});
      await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'离线验收停在派发前，未执行研究。'})});
    });
    await page.goto(base.href);
    await page.locator('#login-account').fill('admin');
    await page.locator('#login-password').fill('66666666');
    await page.locator('#login-submit').click();
    await page.locator('#chat-input').waitFor();
    const bootstrap=await (await context.request.get(new URL('/api/bootstrap',base).href)).json();
    check(width+' actual offline policy is unchanged',bootstrap.execution.mode==='offline');

    const fresh=async()=>{
      if(await page.locator('.rail [data-action=new]').isHidden())
        await page.locator('.web-control[data-action=tasks]').click();
      await page.locator('.rail [data-action=new]').click();
      await page.waitForFunction(()=>selected===null);
    };
    const submit=async(label,attachment=false,explicit=false,reuse=false)=>{
      const count=starts.length,created=creates.length,uploaded=uploads.length;
      if(attachment)await page.locator('#material-file').setInputFiles({name:'研究核对要求.txt',mimeType:'text/plain',
        buffer:Buffer.from('核对要求：保留原始金额单位和合并报表口径，不提供任何预设财务数值。')});
      await page.locator('#chat-input').fill(question);
      await page.locator('#chat-form button[type=submit]').click();
      await page.waitForFunction(()=>!sending);
      check(width+' '+label+' reaches dispatch once',starts.length===count+1);
      check(width+' '+label+' never duplicates an archived submission',creates.length===created+(reuse?0:1));
      const posted=creates.at(-1),{input,task}=starts.at(-1);
      check(width+' '+label+' retains the full original question',posted.question===question&&task.question===question);
      check(width+' '+label+' sends no prepared questions or answers',
        !('questions' in posted)&&task.question_origin==='default'&&task.evidence.length===0&&task.calculations.length===0);
      check(width+' '+label+' respects the actual form scope',
        posted.entry===(explicit?'个股':'混合')&&posted.intent==='综合研究'&&
        JSON.stringify(posted.objects)===JSON.stringify(explicit?['600519.SH']:[]));
      check(width+' '+label+' retains current mode and budget',posted.research_mode==='current'&&!('cutoff' in posted)&&
        JSON.stringify(posted.limits)===JSON.stringify({data_requests:100,model_calls:8,seconds:600}));
      check(width+' '+label+' binds the archived task before starting',
        ['id','version','batch','cutoff','input_hash'].every(key=>input.expected_task[key]===task[key]));
      check(width+' '+label+' binds the actual unchanged policy',
        input.expected_execution.mode==='offline'&&input.expected_execution.policy_version===bootstrap.execution.policy_version);
      check(width+' '+label+' archives every attachment before dispatch',
        uploads.length-uploaded===(attachment?1:0)&&task.materials.length===(attachment?1:0)&&
        starts.at(-1).uploads===uploads.length);
      if(reuse)check(width+' '+label+' retries the same pending task',task.id===starts.at(-2).task.id);
      check(width+' '+label+' cannot mislabel a stopped request as a report',
        task.status==='待开始'&&!task.delivery&&task.ledger.used.model_calls===0&&task.ledger.used.data_requests===0&&
        await page.locator('#chat').innerText().then(text=>!text.includes('研究已完成')));
      record.cases.push({width,label,submitted:posted,task_id:task.id,status:task.status,
        question_origin:task.question_origin,materials:task.materials.length,
        expected_task:input.expected_task,start_intercepted:true});
    };
    await fresh();await submit('ordinary default input');
    // A rejected start leaves an archived pending task. Re-sending its exact
    // question must reuse it, even if an attachment was added in the meantime.
    await fresh();await submit('pending input retried with a text attachment',true,false,true);
    await fresh();
    await page.locator('.material-remove').click();
    await page.locator('#chat-form [data-action=scope]').click();
    const form=page.locator('#new-form');await form.waitFor();
    const entry=form.locator('[data-choice-root]').filter({has:page.locator('select#entry')});
    await entry.locator('.choice-trigger').click();
    await entry.getByRole('option',{name:'个股',exact:true}).click();
    await form.locator('#objects').fill('600519.SH');
    await form.locator('button[type=submit]').click();
    await form.waitFor({state:'hidden'});
    await submit('scope explicitly selected by the user',false,true);
    await context.close();
  }
  check('no external network attempt',record.externalRequests===0);
  check('no browser exceptions',record.errors.length===0);
  record.collector_difference={entry:['混合','个股'],objects:[[],['600519.SH']],
    budget_data_requests:[100,12],prepared_questions_in_either:false,
    conclusion:'The earlier HTTP collector is not the untouched default ResChat input. Neither route injects a research plan.'};
  record.status='passed';
}catch(error){record.error=error.stack||String(error)}
finally{
  if(browser)await browser.close();
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
}
console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
if(record.status!=='passed')process.exitCode=1;
