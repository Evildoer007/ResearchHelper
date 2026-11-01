/* Synthetic comparison rows only; no task, saved result, ledger or model injection. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const base=new URL(process.env.RESEARCHHELPER_PACKAGE_URL),output=process.env.RESEARCHHELPER_ACCEPTANCE_DIR;
assert.equal(base.hostname,'127.0.0.1');
const record={status:'failed',checks:[],screens:[],errors:[],externalRequests:0,fixtureOnly:true};
const check=(name,condition)=>{assert.equal(condition,true,name);record.checks.push(name)};
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
  await page.getByRole('button',{name:'ResDesk',exact:true}).click();
  await page.locator('#view-4').click();
  await page.evaluate(()=>{
    const root=document.querySelector('#content');root.replaceChildren();
    window.comparisonFixture={root};
    window.showComparisonFixture=(state='changed')=>{
      const delta={baseline_available:state!=='unknown'},rows=[];
      if(state==='changed')rows.push({status:'已变更',before:{method_title:'同口径净利润率'},after:{method_title:'同口径净利润率'},
        details:[{label:'输入值',before_text:'营业收入：90,000元；报表净利润：45,000元',after_text:'营业收入：92,000元；报表净利润：46,000元'},
          {label:'输入证据',before_text:'合成原件2026年上半年，观测日期2026年6月30日。',after_text:'合成原件2026年三季度，观测日期2026年9月30日。'+('长内容核对，不代表真实证券数据。'.repeat(16))},
          {label:'方法名称',before_text:'<img src=x onerror="window.comparisonExecuted=true">',after_text:'同口径净利润率'}]});
      const element=document.createElement('section');element.className='section';
      element.innerHTML=calculationDifferences({prior_version:1,research_version:2,calculation_changes:delta,calculation_rows:rows});
      comparisonFixture.root.replaceChildren(element);
    };
  });
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;showComparisonFixture()},theme);
      const label=width+' '+theme,section=page.locator('#content>.section');
      check(label+' both versions visible',await section.getByText('原版本1',{exact:true}).count()===3&&await section.getByText('本版本2',{exact:true}).count()===3);
      check(label+' values remain intact',(await section.innerText()).includes('营业收入：92,000元'));
      check(label+' untrusted text escaped',await section.locator('img,script').count()===0&&await page.evaluate(()=>window.comparisonExecuted!==true));
      check(label+' no new disclosure',await section.locator('details,summary').count()===0);
      check(label+' page no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      const metrics=await section.evaluate(root=>{
        const node=root.querySelector('.calculation-change-field dl'),sides=[...node.children].map(x=>x.getBoundingClientRect());
        return {stacked:Math.abs(sides[0].x-sides[1].x)<2&&sides[1].y>=sides[0].bottom-1,
          horizontal:Math.abs(sides[0].y-sides[1].y)<2,
          font:[...root.querySelectorAll('h4,h5,dt,dd')].every(x=>parseFloat(getComputedStyle(x).fontSize)>=13),
          contained:[...root.querySelectorAll('h4,h5,dt,dd')].every(x=>x.scrollWidth<=x.clientWidth+1)};
      });
      check(label+' responsive comparison columns',width===390?metrics.stacked:metrics.horizontal);
      check(label+' readable type and long text',metrics.font&&metrics.contained);
      const screenshot=path.join(output,'comparison-'+width+'-'+theme+'.png');
      await page.screenshot({path:screenshot});record.screens.push(screenshot);
      const last=section.locator('dd').last();await last.scrollIntoViewIfNeeded();
      const reachable=await last.evaluate(node=>{
        const bounds=node.getBoundingClientRect(),root=document.querySelector('#content').getBoundingClientRect();
        const x=Math.min(bounds.right-1,Math.max(bounds.left+1,0)),y=Math.min(bounds.bottom-1,innerHeight-1,root.bottom-1);
        const visible=document.elementFromPoint(x,y);
        return y>=bounds.top&&bounds.top>=root.top-1&&bounds.bottom<=root.bottom+1&&(visible===node||node.contains(visible));
      });
      check(label+' final field scrolls clear of composer',reachable);
      const bottom=path.join(output,'comparison-bottom-'+width+'-'+theme+'.png');
      await page.screenshot({path:bottom});record.screens.push(bottom);
      await page.evaluate(()=>showComparisonFixture('unknown'));
      check(label+' missing baseline not called unchanged',(await section.innerText()).includes('无法核对变化')&&!(await section.innerText()).includes('未发生变化'));
      await page.evaluate(()=>showComparisonFixture('unchanged'));
      check(label+' actual unchanged state explicit',(await section.innerText()).includes('已留存计算未发生变化'));
    }
  }
  check('no runtime errors',record.errors.length===0);check('no external request',record.externalRequests===0);
  record.status='passed';
}catch(error){record.error=error.stack||String(error)}finally{
  if(browser)await browser.close();
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
}
console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
if(record.status!=='passed')process.exitCode=1;
