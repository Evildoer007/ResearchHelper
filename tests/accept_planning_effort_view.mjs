/* Actual admin/sales forms and local API; synthetic settings, no task starts. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL),output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
assert.equal(base.hostname,'127.0.0.1');
const record={status:'failed',checks:[],screens:[],errors:[],externalRequests:0,geometry:[],
  headless:true,syntheticConfiguration:true,realResearchAccepted:false};
const check=(name,value)=>{assert.equal(value,true,name);record.checks.push(name)};
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
  const login=async account=>{
    await page.goto(base.href);await page.locator('#login-account').fill(account);
    await page.locator('#login-password').fill(account==='admin'?'66666666':'88888888');
    await page.locator('#login-submit').click();await page.locator('#chat-input').waitFor();
  };
  const boot=async()=>{const response=await context.request.get(new URL('/api/bootstrap',base).href);assert.equal(response.status(),200);return response.json()};
  const settings=async(section='model')=>{
    if(await page.locator('[data-account-menu-toggle]').isHidden())await page.locator('.web-control[data-action=tasks]').click();
    await page.locator('[data-account-menu-toggle]').click();await page.locator('#account-menu [data-action=settings]').click();
    const frame=page.frameLocator('.workspace-settings-dialog iframe');
    await frame.locator('#preferences:not([hidden])').waitFor();
    if(section!=='preferences')await frame.locator('.settings-nav a[href="#'+section+'"]').click();
    return frame;
  };
  const choose=async(frame,id,label)=>{
    const control=frame.locator('.choice:has(select#'+id+')');
    await control.locator('.choice-trigger').click();
    await control.getByRole('option',{name:label,exact:true}).click();
  };
  const save=async frame=>{
    await frame.locator('#model-form button[type=submit]').click();
    await frame.locator('#model-form .form-result').filter({hasText:'已保存并核对'}).waitFor();
  };
  await login('admin');const initial=await boot();
  check('isolated offline Store has no tasks',initial.execution.mode==='offline'&&initial.tasks.length===0);
  let frame=await settings();
  check('legacy planning default is inherited',await frame.locator('#model-planning').inputValue()==='inherit');
  const planningTrigger=frame.locator('.choice:has(select#model-planning) .choice-trigger');
  const planningLabelTarget=await planningTrigger.getAttribute('id');
  check('planning has a persistent visible label',await frame.locator('label[for="'+planningLabelTarget+'"]').innerText()==='规划推理强度'&&
    await planningTrigger.getAttribute('aria-label')==='规划推理强度');
  await choose(frame,'model-planning','低');await frame.locator('.settings-close').click();
  frame=await settings();
  check('closing unsaved settings never activates an override',await frame.locator('#model-planning').inputValue()==='inherit'&&!(await boot()).model_settings.planning_effort);
  await frame.locator('#model-endpoint').fill('https://api.deepseek.com');
  await frame.locator('#model-name').fill('deepseek-flash');
  await frame.locator('#model-key').fill('SYNTHETIC_PHASE_SETTING_NOT_A_REAL_KEY');
  await choose(frame,'model-reasoning','高');await choose(frame,'model-planning','低');
  await choose(frame,'model-extraction','直接提取，不启用思考');await save(frame);
  let value=await boot();
  check('actual API persists three independent choices',value.model_settings.reasoning_effort==='high'&&
    value.model_settings.planning_effort==='low'&&value.model_settings.extraction_mode==='direct');
  check('saving never starts research or enables online mode',value.execution.mode==='offline'&&value.tasks.length===0);
  check('saved key is never reflected in public configuration',!JSON.stringify(value.model_settings).includes('SYNTHETIC_PHASE_SETTING')&&
    await frame.locator('#model-key').inputValue()==='');
  await frame.locator('.settings-close').click();await page.reload();await page.locator('#chat-input').waitFor();
  frame=await settings();
  check('reload retains the explicit planning choice',await frame.locator('#model-planning').inputValue()==='low');
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      await frame.locator('.settings-nav a[href="#preferences"]').click();
      await frame.locator('[data-theme-set='+theme+']').click();
      await frame.locator('.settings-nav a[href="#model"]').click();
      const control=frame.locator('.choice:has(select#model-planning)'),trigger=control.locator('.choice-trigger');
      await trigger.scrollIntoViewIfNeeded();await trigger.focus();await trigger.press('ArrowDown');
      const geometry=await control.evaluate(root=>{
        const trigger=root.querySelector('.choice-trigger').getBoundingClientRect(),menu=root.querySelector('.choice-menu').getBoundingClientRect();
        const save=root.closest('form').querySelector('button[type=submit]').getBoundingClientRect();
        const footer=root.closest('form').querySelector('.form-foot').getBoundingClientRect();
        const extraction=root.closest('form').querySelector('#model-extraction').closest('.choice').getBoundingClientRect();
        return {open:root.dataset.open==='true',inViewport:menu.left>=0&&menu.top>=0&&menu.right<=innerWidth+1&&menu.bottom<=innerHeight+1,
          triggerHeight:trigger.height,triggerWidth:trigger.width,scrollWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,
          saveVisible:save.top>=0&&save.bottom<=innerHeight+1,saveBottom:save.bottom,viewportHeight:innerHeight,
          saveHeight:save.height,controlsUncovered:trigger.bottom<=footer.top,
          planningBottom:trigger.bottom,extractionBottom:extraction.bottom,footerTop:footer.top};
      });record.geometry.push({width,theme,...geometry});
      check(width+' '+theme+' planning dropdown stays within its actual frame',geometry.open&&geometry.inViewport);
      check(width+' '+theme+' primary save stays in the visible action area',geometry.saveVisible);
      check(width+' '+theme+' action area never covers active planning',geometry.controlsUncovered);
      if(width===390)check(theme+' mobile controls have full touch targets',geometry.triggerHeight>=44&&geometry.saveHeight>=44);
      await page.keyboard.press('Escape');
      check(width+' '+theme+' Escape preserves focus and settings sheet',await trigger.evaluate(element=>element===document.activeElement)&&
        await trigger.getAttribute('aria-expanded')==='false'&&await page.locator('.workspace-settings-dialog[open]').count()===1);
      await page.keyboard.press('Tab');
      const extraction=frame.locator('.choice:has(select#model-extraction) .choice-trigger');
      check(width+' '+theme+' keyboard reaches the last setting',await extraction.evaluate(element=>element===document.activeElement));
      check(width+' '+theme+' last setting remains uncovered when focused',await extraction.evaluate(element=>{
        const control=element.getBoundingClientRect(),footer=element.closest('form').querySelector('.form-foot').getBoundingClientRect();
        return control.top>=0&&control.bottom<=footer.top;
      }));
      check(width+' '+theme+' no horizontal overflow',geometry.scrollWidth<=geometry.viewportWidth+1&&
        await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const screen=path.join(output,'planning-settings-'+width+'-'+theme+'.png');
      await page.screenshot({path:screen,animations:'disabled'});record.screens.push(screen);
    }
  }
  await choose(frame,'model-planning','沿用默认强度');await save(frame);value=await boot();
  check('inherit removes only the planning override',!value.model_settings.planning_effort&&
    value.model_settings.reasoning_effort==='high'&&value.model_settings.extraction_mode==='direct');
  check('restored inherited choice is visible',await frame.locator('#model-planning').inputValue()==='inherit');
  await frame.locator('.settings-close').click();
  if(await page.locator('[data-account-menu-toggle]').isHidden())await page.locator('.web-control[data-action=tasks]').click();
  await page.locator('[data-account-menu-toggle]').click();await page.locator('#account-menu [data-action=logout]').click();
  await login('sales');const sales=await boot();frame=await settings('preferences');
  check('sales cannot see model management controls',await frame.locator('.settings-nav a[href="#model"]').isHidden()&&
    await frame.locator('#model-form').count()===0);
  const denied=await context.request.post(new URL('/api/settings/model',base).href,{data:{planning_effort:'low'},
    headers:{'X-ResearchHelper-Token':sales.token}});
  check('sales cannot write planning settings',denied.status()===403);
  check('no external requests',record.externalRequests===0);check('no browser errors',record.errors.length===0);
  record.status='passed';
}catch(error){record.error=error.stack||String(error)}
finally{
  if(browser)await browser.close();
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
}
console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
if(record.status!=='passed')process.exitCode=1;
