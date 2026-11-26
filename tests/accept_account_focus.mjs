import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(path.join(process.env.HOME,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,baseline}=JSON.parse(input),origin=new URL(base).origin;
assert.equal(new URL(base).hostname,'127.0.0.1');
const result={status:'failed',checks:[],errors:[],externalRequests:0,taskWrites:0};
const check=(name,value)=>{assert.ok(value,name);result.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});

try{
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin!==origin&&!['data:','blob:'].includes(url.protocol)){
      result.externalRequests++;return route.abort();
    }
    if(url.pathname.startsWith('/api/tasks')&&route.request().method()!=='GET')result.taskWrites++;
    return route.continue();
  });
  const page=await context.newPage();
  page.setDefaultTimeout(7500);
  page.on('pageerror',error=>result.errors.push(error.message));
  const root=page.locator('[data-account-menu-root]'),trigger=root.locator('[data-account-menu-toggle]');
  const menu=root.locator('#account-menu'),item=name=>menu.locator('[data-action="'+name+'"]');
  const help=root.locator('[data-user-guide]');
  const active=node=>node.evaluate(element=>element===document.activeElement);
  const pointer=()=>root.evaluate(element=>element.hasAttribute('data-account-pointer'));
  const theme=value=>page.evaluate(async value=>(await import('/login/theme.js')).setThemePreference(value),value);
  const openSettings=()=>page.frameLocator('.workspace-settings-dialog iframe');
  const expectSettings=async()=>{await openSettings().locator('#preferences').waitFor();};
  const settingsClosed=async()=>{await page.locator('.workspace-settings-dialog').waitFor({state:'detached'});};
  const settingsDialog=page.locator('.workspace-settings-dialog');
  const outerClose=settingsDialog.locator('.workspace-settings-dialog__close');
  const retrySettings=settingsDialog.getByRole('button',{name:'重试',exact:true});
  const enterSettings=async()=>{await trigger.press('ArrowDown');await page.keyboard.press('Enter')};
  async function login(account){
    await page.locator('#login-account').fill(account);
    await page.locator('#login-password').fill(account==='admin'?'66666666':'88888888');
    await page.locator('#login-submit').click();
    await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  }
  await page.goto(base);
  await login('admin');
  await trigger.click();
  if(baseline){
    check('pointer opening moves focus to the settings row',await active(item('settings')));
    check('previous account avatar wraps a 24px nested image',await trigger.locator('.rail-account-trigger__mark img')
      .evaluate(el=>el.clientWidth===24));
  }else{
    check('pointer opens menu with focus on trigger and no focused item',await active(trigger)&&await pointer()&&
      await menu.locator(':focus').count()===0);
    await help.click();
    check('opening help dismisses the account menu',await trigger.getAttribute('aria-expanded')==='false');
    await page.keyboard.press('Escape');
    for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
      await page.setViewportSize({width,height});
      if(await trigger.isHidden())await page.locator('.web-control[data-action=tasks]').click();
      for(const value of ['light','dark']){
        await theme(value);
        await trigger.press('ArrowDown');
        check(width+' '+value+' keyboard moves into settings and paints a focus fill',
          await active(item('settings'))&&!await pointer()&&await item('settings').evaluate(el=>
            getComputedStyle(el).backgroundColor!=='rgba(0, 0, 0, 0)'&&getComputedStyle(el).outlineStyle==='none'));
        check(width+' '+value+' menu and brand mark are in bounds',await menu.evaluate(el=>{
          const r=el.getBoundingClientRect(),mark=document.querySelector('.rail-account-trigger__mark');
          return r.left>=0&&r.right<=innerWidth+.5&&r.top>=0&&r.bottom<=innerHeight+.5&&
            getComputedStyle(el).borderTopLeftRadius==='14px'&&mark.tagName==='IMG'&&mark.naturalWidth>0&&mark.clientWidth===22;
        }));
        await page.keyboard.press('End');
        check(width+' '+value+' End selects the last enabled item',await active(item('logout')));
        await page.keyboard.press('ArrowDown');
        check(width+' '+value+' Down wraps to the first enabled item',await active(item('settings')));
        await page.keyboard.press('Escape');
        check(width+' '+value+' Escape closes and restores focus',await menu.isHidden()&&await active(trigger));
      }
    }
    await page.setViewportSize({width:1440,height:900});
    await theme('light');
    await trigger.press('ArrowDown');await page.keyboard.press('Tab');
    check('Tab resumes ordinary traversal from the account trigger',await menu.isHidden()&&await active(help));
    await trigger.press('ArrowDown');await page.keyboard.press('Shift+Tab');
    check('Shift+Tab closes the menu and returns to the previous rail control',await menu.isHidden()&&
      await page.evaluate(()=>document.activeElement.closest('#tasks')!==null&&!document.activeElement.matches('[data-account-menu-toggle]')));
    await enterSettings();await expectSettings();
    check('settings hand off keyboard focus to the actual close button',
      await active(openSettings().locator('.settings-close'))&&await outerClose.isHidden());
    check('sign-out is in the account menu and settings has no account section',
      await menu.locator('[data-action=switch-account]').count()===0&&
      await menu.locator('[data-action=logout]').count()===1&&
      await openSettings().locator('.settings-nav a[href="#account"],#account,[data-workspace-action=logout]').count()===0);
    check('the desktop settings close is circular and aligned to the corner',
      await openSettings().locator('.settings-close').evaluate(button=>{
        const box=button.getBoundingClientRect();
        return box.top===16&&Math.abs(document.documentElement.clientWidth-box.right-16)<1&&
          box.width===box.height&&getComputedStyle(button).borderTopLeftRadius==='50%';
      }));
    await openSettings().locator('.settings-close').click();await settingsClosed();
    check('pointer closing settings restores the trigger without a keyboard selection',await active(trigger)&&await pointer());
    await trigger.click();await item('settings').click();await expectSettings();
    await openSettings().locator('.settings-close').press('Escape');await settingsClosed();
    check('Escape inside settings restores keyboard focus in the workspace',await active(trigger)&&!await pointer());

    const holdSettingsBootstrap=request=>new URL(request.url()).pathname==='/api/bootstrap'&&
      request.frame().url().includes('/settings/index.html');
    let releaseSettings, receivedSettings;
    const loadingGate=new Promise(resolve=>{releaseSettings=resolve});
    const loadingRequest=new Promise(resolve=>{receivedSettings=resolve});
    const failFirstBootstrap=async route=>{
      if(!holdSettingsBootstrap(route.request()))return route.continue();
      receivedSettings();await loadingGate;
      await route.fulfill({status:503,json:{error:'offline settings test'}});
    };
    await page.route('**/api/bootstrap',failFirstBootstrap);
    await enterSettings();
    try{
      await loadingRequest;
      check('while bootstrap is delayed only the OH loading surface is visible',
        await settingsDialog.getByRole('status').innerText()==='正在加载设置…'&&
        await settingsDialog.locator('iframe').isHidden()&&await retrySettings.isHidden()&&await active(outerClose)&&
        await outerClose.evaluate(el=>el.getBoundingClientRect().width===28&&el.getBoundingClientRect().height===28));
      await page.keyboard.press('Tab');
      check('Tab stays on the close control while settings load',await active(outerClose));
      await page.keyboard.press('Shift+Tab');
      check('reverse traversal also stays in the loading dialog',await active(outerClose));
    }finally{releaseSettings()}
    await retrySettings.waitFor();
    check('bootstrap failure shows a single retry and keeps the close control usable',
      await settingsDialog.getByRole('status').innerText()==='设置未能加载'&&await outerClose.isVisible()&&
      await settingsDialog.locator('iframe').isHidden());
    await page.keyboard.press('Tab');
    check('after a failed load Tab reaches retry',await active(retrySettings));
    await page.keyboard.press('Shift+Tab');
    check('after a failed load reverse Tab reaches close',await active(outerClose));
    const failedInstance=await settingsDialog.locator('iframe').getAttribute('src');
    await page.unroute('**/api/bootstrap',failFirstBootstrap);
    await retrySettings.press('Enter');
    await expectSettings();
    check('retry uses a new frame and returns focus to the ready settings',
      await settingsDialog.locator('iframe').getAttribute('src')!==failedInstance&&
      await active(openSettings().locator('.settings-close')));
    await page.keyboard.press('Escape');await settingsClosed();
    check('closing after a settings retry restores account focus',await active(trigger)&&!await pointer());

    await page.setViewportSize({width:390,height:844});
    if(await trigger.isHidden())await page.locator('.web-control[data-action=tasks]').click();
    await theme('dark');
    await page.route('**/settings/index.html?*',route=>route.fulfill({status:404,body:'Not found'}));
    await page.clock.install();
    const missingFrame=page.waitForResponse(response=>response.url().includes('/settings/index.html?')&&response.status()===404);
    await enterSettings();
    await missingFrame;
    const mobileRecoveryClose=await outerClose.boundingBox();
    check('mobile settings recovery keeps the same close placement with a 44px hit target',
      await outerClose.evaluate(el=>el.getBoundingClientRect().width===44&&el.getBoundingClientRect().height===44&&
        el.getBoundingClientRect().right<=innerWidth));
    await page.clock.runFor(12001);
    await retrySettings.waitFor();
    check('a missing frame times out to the same retry state',
      await settingsDialog.getByRole('status').innerText()==='设置未能加载'&&await settingsDialog.locator('iframe').isHidden());
    await outerClose.click();await settingsClosed();
    await page.unroute('**/settings/index.html?*');
    check('closing failed settings preserves the workspace',await active(trigger)&&await pointer());
    await enterSettings();
    const mobileClose=openSettings().locator('.settings-close');
    await mobileClose.waitFor();
    const mobileReadyClose=await mobileClose.boundingBox();
    check('ready settings retain the mobile close position and hit target',
      mobileReadyClose.width===44&&mobileReadyClose.height===44&&
      Math.abs(mobileReadyClose.x-mobileRecoveryClose.x)<=1&&Math.abs(mobileReadyClose.y-mobileRecoveryClose.y)<=1);
    await mobileClose.click();await settingsClosed();
    await page.setViewportSize({width:1440,height:900});await theme('light');
    await page.emulateMedia({forcedColors:'active'});
    await trigger.press('ArrowDown');
    check('forced colors retain the account selection highlight',await item('settings').evaluate(el=>
      getComputedStyle(el).forcedColorAdjust==='none'&&getComputedStyle(el).backgroundColor!=='rgba(0, 0, 0, 0)'));
    await page.keyboard.press('Escape');
    const attach=page.locator('#chat-form [data-action=attach]');
    await attach.focus();
    check('forced colors retain a non-account control focus cue',await attach.evaluate(el=>
      el===document.activeElement&&getComputedStyle(el).forcedColorAdjust==='none'));
    await page.emulateMedia({forcedColors:'none'});
    await trigger.click();await item('logout').click();
    await page.waitForURL(url=>url.pathname==='/login/index.html'&&!url.searchParams.has('account'));
    await login('sales');
    await trigger.press('ArrowUp');
    check('sales can reach account actions without admin workspace access',await active(item('logout'))&&
      await page.locator('[data-mode=desk]').isHidden());
    await page.keyboard.press('Escape');
    check('sales focus returns to its own account trigger',await active(trigger)&&
      (await trigger.getAttribute('aria-label')).includes('sales'));
    if(output){
      await trigger.click();await page.mouse.move(620,110);
      await menu.screenshot({path:path.join(output,'账号菜单.png'),animations:'disabled'});
    }
  }
  check('no browser errors, external requests or research mutations',
    result.errors.length===0&&result.externalRequests===0&&result.taskWrites===0);
  result.status='passed';
}catch(error){
  result.error=error.stack;process.exitCode=1;
}finally{
  await browser.close();
  console.log(JSON.stringify(result));
}
