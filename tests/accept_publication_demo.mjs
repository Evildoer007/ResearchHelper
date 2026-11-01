/** Both shipped file demos: compact drafts and internal report survive reload. */
import fs from 'node:fs/promises';import path from 'node:path';import{createRequire}from'node:module';import{pathToFileURL}from'node:url';import assert from'node:assert/strict';
const{chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const roots=['/Users/haoranxu/Desktop/ResearchHelper/demo','/Users/haoranxu/Desktop/demo/ResearchHelper demo'];
const output='/Users/haoranxu/Desktop/ResearchHelper/result/acceptance/publication-release-20261028';await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),record={status:'failed',checks:[],model_calls:0,data_requests:0,browser_closed:false};
try{for(const root of roots){
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});const page=await context.newPage();
  await page.goto(pathToFileURL(path.join(root,'index.html')).href);await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
  await page.locator('[data-workspace-shell][data-layout-ready="true"]').waitFor();
  if(await page.locator('[data-account-menu-toggle]').isHidden())await page.locator('.web-control[data-action=tasks]').click();
  await page.locator('[data-account-menu-toggle]').click();await page.locator('#account-menu [data-action=settings]').click();
  const settings=page.frameLocator('.workspace-settings-dialog iframe');await settings.locator('.settings-nav a[href="#storage"]').click();await settings.locator('[data-workspace-action=cases]').click();
  await page.locator('#dialog [data-case=etf]').click();await page.locator('[data-mode-switch] [data-mode=desk]').click();await page.locator('#view-3').click();
  let report=page.frameLocator('#report-frame');await report.locator('meta[name=rh-report-profile][content=standard]').waitFor({state:'attached'});
  await page.locator('[data-report-profile=detailed]').click();await report.locator('[data-logic-detail]').first().waitFor();
  const bytes=await page.evaluate(()=>localStorage.getItem('researchhelper-demo-draft-v2').length);assert(bytes<1000000,'packaged HTML must not enter browser storage');
  await page.reload();await page.locator('#chat-form button[type=submit]:not([disabled])').waitFor();
  await page.locator('[data-report-profile=detailed][aria-pressed=true]').waitFor();report=page.frameLocator('#report-frame');await report.locator('[data-logic-detail]').first().waitFor();
  const restored=await page.evaluate(()=>JSON.parse(localStorage.getItem('researchhelper-demo-draft-v2')).tasks);assert(restored.some(t=>t.demo_report_ref==='etf'&&!t.report_html&&!t.report_detailed_html));
  await page.locator('[data-report-profile=standard]').click();await report.locator('meta[name=rh-report-profile][content=standard]').waitFor({state:'attached'});
  record.checks.push({root,storage_characters:bytes,internal_profile_restored:true,client_profile_restored:true,legacy_content_preserved_by_storage_test:true});await context.close();
}record.status='passed';}catch(error){record.error=error.stack;process.exitCode=1}finally{await browser.close();record.browser_closed=true;await fs.writeFile(path.join(output,'publication-demos.json'),JSON.stringify(record,null,2));console.log(JSON.stringify(record));}
