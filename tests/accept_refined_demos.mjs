/* Current flat-report demo acceptance. The legacy suite is left unchanged. */
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import path from 'node:path';
const root=process.cwd(),original=path.join(root,'tests/accept_synced_demos.mjs');
const source=await readFile(original,'utf8'),hash=createHash('sha256').update(source).digest('hex');
let text=source;
function replace(before,after){if(text.split(before).length!==2)throw Error('Legacy fixture changed: '+before.slice(0,100));text=text.replace(before,after);}
replace("result/acceptance/synced-demos-","result/acceptance/refined-demos-");
replace("    await history.locator('details.research-appendix > summary').click();\n    check(index + ' complete original research expands', await history.locator('details.research-appendix').getAttribute('open') !== null);",
"    check(index + ' original research reads without folding', await history.locator('details.research-appendix').count() === 0 && (await history.locator('.archived-content').innerText()).length > 1500);");
replace("      await original.locator('details.research-appendix > summary').click();\n      check(index + ' complete original report available ' + key,\n        await original.locator('details.research-appendix').getAttribute('open') !== null &&\n        (await original.locator('.archived-content').innerText()).length > 1000);",
"      check(index + ' complete original report available ' + key, await original.locator('details.research-appendix').count() === 0 && (await original.locator('.archived-content').innerText()).length > 1000);");
replace("    check(index + ' frozen complete report available', (await report.locator('body').innerText()).length > 300);",
"    check(index + ' frozen complete report available', (await report.locator('body').innerText()).length > 300);\n"+
"    check(index + ' OH assistant works in file preview', await page.locator('.assistant-toggle svg[data-bloub]').count() === 1 && await page.locator('.assistant-toggle img').count() === 0);\n"+
"    check(index + ' redundant gap navigation removed', await page.locator('[data-gap-process],[data-gap-calls],[data-gap-material]').count() === 0);\n"+
"    await page.locator('[data-work-menu-toggle]').click();check(index + ' report menu opens offline', await page.locator('#report-more-menu').isVisible());await page.keyboard.press('Escape');check(index + ' report menu closes offline', await page.locator('#report-more-menu').isHidden());");
replace("    check(index + ' full historical report reads offline', (await history.locator('body').innerText()).length > 1500);",
"    await history.locator('a[href=\"#history-source\"]').click();await history.locator('#report-inspector[open]').waitFor();\n    check(index + ' full historical report reads offline', (await history.locator('#report-inspector .archived-content').innerText()).length > 1500);await page.keyboard.press('Escape');");
replace("      check(index + ' complete original report available ' + key, await original.locator('details.research-appendix').count() === 0 && (await original.locator('.archived-content').innerText()).length > 1000);",
"      await original.locator('a[href=\"#history-source\"]').click();await original.locator('#report-inspector[open]').waitFor();\n      check(index + ' complete original report available ' + key, await original.locator('details.research-appendix').count() === 0 && (await original.locator('#report-inspector .archived-content').innerText()).length > 1000);await page.keyboard.press('Escape');");
const folder=path.join(root,'result/acceptance/workspace-refinement');await mkdir(folder,{recursive:true});
const fixture=path.join(folder,'file-preview-browser.mjs');await writeFile(fixture,text);
const run=spawnSync(process.execPath,[fixture],{cwd:root,encoding:'utf8',timeout:180000});
await writeFile(path.join(folder,'file-preview-browser.log'),(run.stdout||'')+(run.stderr||''));
if(createHash('sha256').update(await readFile(original)).digest('hex')!==hash)throw Error('Original suite was modified');
process.stdout.write(run.stdout||'');process.stderr.write(run.stderr||'');process.exitCode=run.status??1;

// A suspended WebKit timeline must never hold the whole workspace transparent.
if(run.status===0){
  const {createRequire}=await import('node:module');
  const {pathToFileURL}=await import('node:url');
  const assert=(await import('node:assert/strict')).default;
  const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
  const browser=await chromium.launch({headless:true,channel:'chrome'});const surfaces=[];let requests=0;
  try{
    for(const file of [path.join(root,'demo/index.html'),'/Users/haoranxu/Desktop/demo/ResearchHelper demo/index.html']){
      const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'no-preference'});
      await page.route(/^https?:/,route=>{requests++;return route.abort()});
      await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{
        const style=document.createElement('style');style.textContent='[data-workspace-shell]{animation-play-state:paused!important}';document.head.append(style);
      },{once:true}));
      await page.goto(pathToFileURL(file).href);await page.locator('[data-workspace-shell][data-initializing="false"]').waitFor({state:'attached'});
      const state=await page.evaluate(()=>{const shell=document.querySelector('[data-workspace-shell]'),style=getComputedStyle(shell);return {opacity:style.opacity,visibility:style.visibility,animation:style.animationName,loadingLogo:getComputedStyle(document.body,'::before').display}});
      assert.equal(state.opacity,'1');assert.equal(state.visibility,'visible');assert.equal(state.animation,'none');assert.equal(state.loadingLogo,'none');surfaces.push({file,...state});await page.close();
    }
    assert.equal(requests,0);
  }finally{await browser.close()}
  const receipt=path.join(folder,'startup-visibility.json');await writeFile(receipt,JSON.stringify({status:'passed',checks:8,paused_animation_timeline:true,surfaces,externalRequests:requests,model_calls:0,data_requests:0},null,2));console.log(JSON.stringify({status:'passed',checks:8,receipt}));
}
