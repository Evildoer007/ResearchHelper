/* Component fixtures only: no actual research task or study state is injected. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL),output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
assert.equal(base.hostname,'127.0.0.1');
const record={status:'failed',checks:[],screens:[],errors:[],externalRequests:0,
  fixturesOnly:true,headless:true,researchAccepted:false,states:[],geometry:[]};
const check=(name,value)=>{assert.equal(value,true,name);record.checks.push(name)};
const phases={plan:'正在规划研究',read:'正在选择材料',extract:'正在提取材料',
  bind_calculations:'正在核对计算输入',analyze:'正在分析证据',supplement:'正在规划补证'};
let browser;
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===base.origin||['data:','blob:'].includes(url.protocol))route.continue();
    else{record.externalRequests++;route.abort()}
  });
  const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
  await page.goto(base.href);await page.locator('#login-account').fill('admin');
  await page.locator('#login-password').fill('66666666');await page.locator('#login-submit').click();
  await page.locator('#chat-input').waitFor();
  // Keep the actual shell, typography and theme ancestors. Replace only the
  // empty conversation DOM with an explicitly synthetic component fixture;
  // do not assign selected, boot, a task, a ledger or a backend result.
  await page.evaluate(()=>{
    const root=document.querySelector('#chat');root.replaceChildren();
    document.querySelector('#chat-surface').classList.remove('chat-surface--empty');
    window.progressFixture={controller:RHResearchProcess.create(),root};
    window.showProgressFixture=(phase,stage='读取响应',extra={})=>{
      const task={id:'synthetic-progress-component',synthetic:true,status:'运行中',batch:2,nodes:[],
        ledger:{dispatches:[{kind:'model_calls',research_phase:phase,stage,status:'已派发',batch:2}]},...extra};
      const template=document.createElement('template');template.innerHTML=progressFixture.controller.html(task);
      const incoming=template.content.firstElementChild,previous=progressFixture.root.firstElementChild;
      const current=previous?progressFixture.controller.reconcile(previous,incoming):incoming;
      if(current!==previous)progressFixture.root.replaceChildren(current);
      const label=current.querySelector('.runtime-state-label');
      return {label:label.textContent,title:label.title,regions:current.querySelectorAll('[role=status]').length,
        live:label.getAttribute('aria-live'),atomic:label.getAttribute('aria-atomic'),
        orb:!!current.querySelector('[data-process-orb]'),html:current.outerHTML};
    };
  });
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
      for(const [phase,label] of Object.entries(phases)){
        const result=await page.evaluate(phase=>showProgressFixture(phase),phase);
        record.states.push({width,theme,phase,label:result.label,title:result.title});
        check(width+' '+theme+' '+phase+' names only the actual phase',result.label===label);
        check(width+' '+theme+' '+phase+' preserves one live status region',
          result.regions===1&&result.live==='polite'&&result.atomic==='true');
      }
      await page.evaluate(()=>showProgressFixture('plan','连接服务'));
      const summary=page.locator('.chat-progress>summary');await summary.focus();
      const opened=await page.locator('.chat-progress').evaluate(element=>element.open);
      await page.keyboard.press('Space');
      check(width+' '+theme+' disclosure remains keyboard operable',
        await page.locator('.chat-progress').evaluate(element=>element.open)!==opened);
      const retained=await page.evaluate(()=>{
        const details=document.querySelector('.chat-progress'),summary=details.querySelector('summary'),open=details.open;
        const next=showProgressFixture('plan','读取响应');
        return {same:details===document.querySelector('.chat-progress')&&summary===document.activeElement,
          open:details.open===open,title:next.title,label:next.label};
      });
      check(width+' '+theme+' live updates preserve focus and user disclosure',retained.same&&retained.open);
      check(width+' '+theme+' transport title updates without more text',
        retained.title==='正在等待模型响应'&&retained.label==='正在规划研究');
      check(width+' '+theme+' no horizontal overflow',await page.evaluate(()=>
        document.documentElement.scrollWidth<=innerWidth+1));
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      record.geometry.push(await page.evaluate(({width,theme})=>({width,theme,innerHeight,
        documentHeight:document.documentElement.scrollHeight,scrollY,
        viewport:visualViewport?{height:visualViewport.height,offsetTop:visualViewport.offsetTop,scale:visualViewport.scale}:null,
        headerCount:document.querySelectorAll('header').length,
        processCount:document.querySelectorAll('.rh-process').length}),{width,theme}));
      const screen=path.join(output,'progress-'+width+'-'+theme+'.png');
      await page.screenshot({path:screen,animations:'disabled'});record.screens.push(screen);
    }
  }
  const fallback=await page.evaluate(()=>{
    const node={id:'source',title:'正在下载公告原件',status:'运行中',questions:[]};
    const done={kind:'model_calls',research_phase:'plan',stage:'读取响应',status:'成功',batch:2};
    const data={kind:'data_requests',status:'已派发',batch:2};
    return {
      completed:showProgressFixture('plan','读取响应',{ledger:{dispatches:[done]},nodes:[node]}),
      laterSource:showProgressFixture('plan','读取响应',{ledger:{dispatches:[{...done,status:'已派发'},data]},nodes:[node]}),
      otherBatch:showProgressFixture('plan','读取响应',{batch:3,nodes:[node]}),
      unknownPhase:showProgressFixture('__proto__','PRIVATE_STAGE_TEXT'),
      unknownStage:showProgressFixture('plan','PRIVATE_STAGE_TEXT'),
      cancelled:showProgressFixture('plan','读取响应',{status:'已取消'}),
      terminal:showProgressFixture('plan','读取响应',{status:'已完成'})
    };
  });
  for(const name of ['completed','laterSource','otherBatch'])check(name+' cannot retain a stale model phase',
    fallback[name].label==='正在下载公告原件'&&fallback[name].title==='');
  check('unknown phase and stage never become UI text',fallback.unknownPhase.label==='研究正在运行'&&
    fallback.unknownPhase.title===''&&fallback.unknownStage.title===''&&
    !fallback.unknownStage.html.includes('PRIVATE_STAGE_TEXT'));
  check('cancellation hides the active indicator',fallback.cancelled.label==='研究已取消'&&!fallback.cancelled.orb&&fallback.cancelled.title==='');
  check('completion clears the old transport title',fallback.terminal.label==='研究已完成'&&!fallback.terminal.orb&&fallback.terminal.title==='');
  check('no outside requests',record.externalRequests===0);check('no browser exceptions',record.errors.length===0);
  record.status='passed';
}catch(error){record.error=error.stack||String(error)}
finally{
  if(browser)await browser.close();
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
}
console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
if(record.status!=='passed')process.exitCode=1;
