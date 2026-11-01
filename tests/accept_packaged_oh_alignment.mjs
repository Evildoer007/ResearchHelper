/* Real packaged service + disposable Store. No native bridge or state injection. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL), output=path.resolve(process.env.RESEARCHHELPER_ACCEPTANCE_DIR);
assert.equal(base.hostname,'127.0.0.1'); assert.equal(base.protocol,'http:');
const record={status:'failed',checks:[],screens:[],errors:[],externalRequests:0,headless:true,
  systemClipboardTouched:false,systemPrintOpened:false,syntheticCases:[]};
const check=(label,value)=>{assert.equal(value,true,label);record.checks.push(label)};
let browser;
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce',acceptDownloads:true});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===base.origin||['data:','blob:'].includes(url.protocol))route.continue();
    else{record.externalRequests++;route.abort()}
  });
  const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
  const login=async account=>{
    await page.goto(base.href);await page.locator('#login-account').fill(account);
    await page.locator('#login-password').fill(account==='admin'?'66666666':'88888888');
    await page.locator('#login-submit').click();await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
    check(account+' actual authenticated workspace',new URL(page.url()).pathname==='/');
  };
  const boot=async()=>{const response=await context.request.get(new URL('/api/bootstrap',base).href);assert.equal(response.status(),200);return response.json()};
  const section=async(frame,id)=>{await frame.locator('.settings-nav a[href="#'+id+'"]').click();await frame.locator('#'+id+':not([hidden])').waitFor()};
  const settings=async()=>{
    if(await page.locator('[data-account-menu-toggle]').isHidden())await page.locator('.web-control[data-action=tasks]').click();
    await page.locator('[data-account-menu-toggle]').click();await page.locator('#account-menu [data-action=settings]').click();
    const frame=page.frameLocator('.workspace-settings-dialog iframe');await frame.locator('#preferences:not([hidden])').waitFor();return frame;
  };
  const mode=async value=>{
    const button=page.locator('[data-mode-switch] [data-mode='+value+']');
    if(await button.isHidden())await page.locator('.web-control[data-action=tasks]').click();
    await button.click();await page.waitForFunction(value=>document.querySelector('[data-workspace-shell]').dataset.mode===value,value);
  };
  const createCase=async id=>{
    const frame=await settings();await section(frame,'storage');await frame.locator('[data-workspace-action=cases]').click();
    await page.locator('#dialog [data-case='+id+']').click();
    await page.waitForFunction(id=>selected?.fixture===id,id);
    await mode('chat');await page.locator('#chat .rh-process [data-action=start]').click();
    await page.waitForFunction(()=>selected?.status==='已完成',null,{timeout:30000});
    const task=await page.evaluate(()=>structuredClone(selected));
    check(id+' actual completion and frozen report',task.synthetic===true&&!!task.delivery);
    check(id+' no real model or source dispatch',task.ledger.used.model_calls===0&&task.ledger.used.data_requests===0);
    check(id+' completed process visible',await page.locator('#chat .rh-process').innerText().then(text=>text.includes('研究已完成')));
    record.syntheticCases.push({id:task.id,fixture:id,version:task.version,ledger:task.ledger});return task;
  };
  await login('admin');const initial=await boot();
  check('fresh isolated offline Store',initial.offline===true&&initial.tasks.length===0&&Object.keys(initial.history).length===6);
  await createCase('mechanism');const task=await createCase('etf');
  await page.locator('#chat-input').fill('刷新后保留的未发送研究问题');await page.reload();
  await page.waitForFunction(id=>selected?.id===id,task.id);
  check('reload restores selected task and unsent draft',await page.locator('#chat-input').inputValue()==='刷新后保留的未发送研究问题');
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      const frame=await settings();await frame.locator('[data-theme-set='+theme+']').click();
      await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);
      check(width+' '+theme+' settings fit',await frame.locator('html').evaluate(el=>el.scrollWidth<=innerWidth));
      await frame.locator('.settings-close').click();await page.locator('.workspace-settings-dialog').waitFor({state:'detached'});
      await mode('desk');
      if(width===390)check(theme+' mobile Desk composer follows semantic ground',await page.locator('.assistant-region').evaluate(el=>{
        const probe=document.createElement('span');probe.style.backgroundColor='var(--color-ground)';el.append(probe);
        const expected=getComputedStyle(probe).backgroundColor,actual=getComputedStyle(el).backgroundColor;
        probe.remove();return actual===expected;
      }));
      for(let view=0;view<5;view++){
        await page.locator('#view-'+view).click();
        check(width+' '+theme+' direct research view '+view,await page.locator('#content').getAttribute('aria-labelledby')==='view-'+view);
        check(width+' '+theme+' no workspace overflow '+view,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      }
      await page.locator('#view-3').click();const report=page.frameLocator('#report-frame');
      await report.locator('h1').waitFor();
      check(width+' '+theme+' report retains frozen conclusion',await report.locator('#summary').innerText().then(text=>text.includes('0.40')));
      await report.locator('#theses a[href="#ev-e1"]').first().click();
      check(width+' '+theme+' evidence link reveals original fee numbers',
        await report.locator('#ev-e1').isVisible()&&await report.locator('#ev-e1').innerText().then(text=>text.includes('0.15'))&&
        await report.locator('#ev-e2').innerText().then(text=>text.includes('0.50')));
      await report.locator('.chart svg').waitFor();
      check(width+' '+theme+' chart is actual ECharts with bound fee values',await report.locator('.chart').evaluate(el=>{
        const chart=echarts.getInstanceByDom(el),values=chart.getOption().series.flatMap(series=>series.data.map(point=>point.value));
        return values.length===2&&values[0]===.2&&values[1]===.6&&el.getBoundingClientRect().width>0;
      }));
      if((width===1440&&theme==='light')||(width===390&&theme==='dark')){
        const figure='report-chart-'+width+'-'+theme+'.png';
        await report.locator('.research-figure').screenshot({path:path.join(output,figure),animations:'disabled'});record.screens.push(figure);
      }
      await report.locator('h1').scrollIntoViewIfNeeded();
      const screen='desk-'+width+'-'+theme+'.png';await page.screenshot({path:path.join(output,screen),animations:'disabled'});record.screens.push(screen);
      await mode('chat');check(width+' '+theme+' composer remains accessible',await page.locator('#chat-input').isVisible());
    }
  }
  await page.setViewportSize({width:1440,height:900});await mode('desk');await page.locator('#view-3').click();
  const frozen=await (await context.request.get(new URL(await page.locator('#report-frame').getAttribute('src'),base).href)).text();
  const downloaded=page.waitForEvent('download');await page.locator('#content [data-action=export]').click();const download=await downloaded;
  const saved=path.join(output,'exported-report.html');await download.saveAs(saved);
  check('HTML export equals frozen result, not a second report',await readFile(saved,'utf8')===frozen);
  await mode('chat');await page.locator('#chat-input').focus();await page.keyboard.press('Meta+k');
  const search=page.getByRole('dialog',{name:'搜索任务',exact:true});await search.waitFor();
  check('OH task search uses actual persisted cases',await search.innerText().then(text=>text.includes('ETF')&&text.includes('铜')));
  await page.keyboard.press('Escape');check('search restores composer focus',await page.locator('#chat-input').evaluate(el=>el===document.activeElement));
  let frame=await settings();await section(frame,'model');
  const extraction=frame.locator('#model-extraction').locator('..').locator('.choice-trigger');
  await extraction.click();await page.keyboard.press('End');await page.keyboard.press('Enter');
  check('OH choice keyboard updates underlying select',await frame.locator('#model-extraction').inputValue()==='direct');
  await section(frame,'runtime');await frame.locator('#execution-confirm').check();await frame.locator('#execution-confirm').uncheck();
  check('checkbox alone cannot enable online research',(await boot()).offline===true);
  await section(frame,'preferences');
  for(const scale of [.8,.9,1,1.1,1.25,1.4]){
    await frame.locator('#interface-scale').selectOption(String(scale));
    check('OH six scale option '+scale,await frame.locator('#interface-scale').inputValue()===String(scale));
    check('settings scale fits '+scale,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  await frame.locator('#interface-scale').selectOption('1');await frame.locator('.settings-close').click();
  await page.locator('[data-account-menu-toggle]').click();await page.locator('#account-menu [data-action=logout]').click();await page.waitForURL('**/login/index.html');
  await login('sales');frame=await settings();await section(frame,'account');
  check('sales hides administrator configuration',await frame.locator('#model-form,#data-form,#sales-password-form').count()===0);
  const sales=await boot();check('sales cannot see admin research',sales.role==='sales'&&sales.tasks.length===0);
  const denied=await context.request.post(new URL('/api/settings/model',base).href,{headers:{'X-ResearchHelper-Token':sales.token},data:{model:'offline-forbidden'}});
  check('server denies sales configuration writes',denied.status()===403);
  check('no external requests',record.externalRequests===0);check('no browser script errors',record.errors.length===0);
  record.status='passed';
}catch(error){record.error=error.stack;process.exitCode=1}
finally{
  if(browser)await browser.close();record.browserClosed=true;
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
