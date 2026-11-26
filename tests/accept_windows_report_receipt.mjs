import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(path.join(process.env.HOME,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,id,digest,cookie,script,baseline}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],externalRequests:0};
const check=(name,ok)=>{assert.ok(ok,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({reducedMotion:'reduce'});
  const equals=cookie.indexOf('=');
  await context.addCookies([{url:base,name:cookie.slice(0,equals),value:cookie.slice(equals+1)}]);
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['data:','blob:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const get=async target=>{
    const response=await context.request.get(new URL(target,origin).href);
    assert.ok(response.ok(),await response.text());
    return {headers:response.headers(),body:await response.text()};
  };
  const task='/api/tasks/'+id+'/report/',target=task+'2',address=new URL(target,origin).href;
  const correct=await get(target),old=await get(task+'1'),detail=await get(target+'?profile=detailed');
  check('actual response identity belongs to the selected frozen task and edition',
    correct.headers['x-researchhelper-report']===id+':2:'+digest&&old.headers['x-researchhelper-report'].startsWith(id+':1:'));
  const page=await context.newPage();
  const run=({example=null,variant='standard',expected=digest,printing=false}={})=>page.evaluate(
    ({script,args})=>(0,eval)('('+script+')')(...args),
    {script,args:baseline?[example,variant]:[example,variant,expected,printing,null]});
  const substitute=async(body)=>{
    await page.unroute(address);
    if(body!==null){
      const headers={...correct.headers};
      delete headers['content-length'];
      await page.route(address,route=>route.fulfill({status:200,headers,body}));
    }
    await page.goto(address);
  };
  await substitute(old.body);
  check('the actual older edition is loaded at the current address',page.url()===address&&await page.evaluate(
    digest=>JSON.parse(document.querySelector('#rh-publication-document').textContent).content_hash!==digest,digest));
  if(baseline){
    check('previous Windows preview admits the wrong edition',await run());
    await substitute('<!doctype html><html><main><h1>错误页面</h1></main></html>');
    check('previous Windows preview admits a successful HTML error page',await run());
  }else{
    check('wrong edition with the expected response headers is rejected',!await run());
    await substitute(detail.body);
    check('wrong report profile with the expected response headers is rejected',!await run());
    await substitute('<!doctype html><html><main><h1>错误页面</h1></main></html>');
    check('successful HTML with no publication document is rejected',!await run());
    await substitute(null);
    await page.evaluate(()=>{window.rhBeforePrintCount=0;window.addEventListener('beforeprint',()=>window.rhBeforePrintCount++)});
    check('the exact selected report is ready for preview',await run());
    check('preview does not dispatch print styles',await page.evaluate(()=>window.rhBeforePrintCount===0));
    check('both preview and print require an expected report fingerprint',!await run({expected:null})&&
      !await run({expected:null,printing:true}));
    check('valid print uses the same verification and prepares the document',await run({printing:true})&&
      await page.evaluate(()=>window.rhBeforePrintCount===1));
    await page.goto(new URL('/reports/broker.html',origin).href);
    check('public example uses its own identity',await run({example:'broker',expected:null})&&
      !await run({example:'ipo',expected:null}));
  }
  check('no external requests',record.externalRequests===0);
  record.status='passed';
}catch(error){record.error=error.stack;process.exitCode=1}
finally{
  await browser.close();
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
}
