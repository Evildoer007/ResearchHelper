/* Real loopback draft storage with controlled incomplete and concurrent replies. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(
  path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const options=JSON.parse(input);
const base=new URL(options.base);
assert.equal(base.hostname,'127.0.0.1');
const record={status:'failed',baseline:options.baseline,checks:[],externalRequests:0,errors:[]};
const check=(name,value)=>{assert.ok(value,name);record.checks.push(name)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1320,height:860},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===base.origin||['blob:','data:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const endpoint='/api/workspace-draft',address=new URL(endpoint,base).href;
  const auth=await (await context.request.get(new URL('/api/auth/status',base).href)).json();
  const headers={'X-ResearchHelper-Token':auth.token};
  const login=async(ctx,account,password)=>assert.ok((await ctx.request.post(new URL('/api/auth/login',base).href,
    {headers,data:{account,password}})).ok());
  await login(context,'admin','66666666');
  const page=await context.newPage();
  page.on('pageerror',error=>record.errors.push(error.message));
  const ready=()=>page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  const snapshot=async()=>{
    const response=await context.request.get(new URL('/api/bootstrap',base).href,
      {headers:{'X-ResearchHelper-View':'workspace-v2'}});
    assert.ok(response.ok());
    return (await response.json()).workspace_draft;
  };
  const editor=page.locator('#chat-input'),flush=()=>page.evaluate(()=>window.ResearchHelperNative.flushWorkspaceDraft());
  const mirror=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem(draftKey))._workspaceDraft);
  await page.goto(base.href);await ready();
  const previous='已保存的研究草稿',edited='未获有效保存回执的新问题';
  await editor.fill(previous);
  assert.equal(await flush(),true);
  const saved=await snapshot();
  assert.equal(saved.state.composerTexts.new,previous);

  const sent=[];
  const phantom=async route=>{
    if(route.request().method()!=='POST')return route.continue();
    sent.push(route.request().postDataJSON());
    return route.fulfill({json:{revision:saved.revision+7,write_id:'e'.repeat(32)}});
  };
  await page.route('**'+endpoint,phantom);
  await editor.fill(edited);
  const acknowledged=await flush();
  check('the wrong response did not write the database',JSON.stringify(await snapshot())===JSON.stringify(saved));
  if(options.baseline){
    check('before fix, unmatched response marked the draft clean',acknowledged&&!(await mirror()).dirty);
    await page.unroute('**'+endpoint,phantom);
    await page.reload();await ready();
    check('before fix, a real reload lost the new draft',await editor.inputValue()===previous);
  }else{
    check('unmatched response keeps the draft dirty and visible',acknowledged===false&&(await mirror()).dirty&&await editor.inputValue()===edited);
    assert.equal(await flush(),false);
    check('retries keep the same write identity and version',sent.length>=2&&
      sent.every(attempt=>attempt.write_id===sent[0].write_id&&attempt.expected_revision===saved.revision));
    await page.reload();await ready();
    const restored=await mirror();
    // Chromium may complete the pagehide write while the intercepted document
    // is being replaced. A clean mirror is valid only for that exact commit.
    let recovered=restored.dirty===true;
    if(!recovered){
      const savedOnReload=await snapshot();
      recovered=savedOnReload.revision===saved.revision+1&&savedOnReload.revision===restored.revision&&
        savedOnReload.write_id===sent[0].write_id&&savedOnReload.state.composerTexts.new===edited;
    }
    record.recoveredDraftFrom=restored.dirty?'local mirror':'verified pagehide write';
    check('reload restores the unconfirmed edit',await editor.inputValue()===edited&&recovered);
    await page.unroute('**'+endpoint,phantom);
    assert.equal(await flush(),true);
    check('retry stores the retained edit once',await snapshot().then(value=>
      value.revision===saved.revision+1&&value.state.composerTexts.new===edited));

    let actualWrites=0;
    const incorrectAck=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      actualWrites++;
      const response=await route.fetch();assert.ok(response.ok());
      const actual=await response.json();
      return route.fulfill({json:{...actual,write_id:'d'.repeat(32)}});
    };
    await page.route('**'+endpoint,incorrectAck);
    const committed='正文已入库，回执不同';
    await editor.fill(committed);
    assert.equal(await flush(),true);
    check('verified readback confirms a real commit without another write',actualWrites===1&&!(await mirror()).dirty&&
      (await snapshot()).state.composerTexts.new===committed);
    await page.unroute('**'+endpoint,incorrectAck);

    const sharedDraft='两个窗口保存同一份草稿';
    const sharedId='b'.repeat(32);
    let identicalRevision;
    const sameState=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      const posted=route.request().postDataJSON();
      const identical=await context.request.post(address,{headers,data:{...posted,write_id:sharedId}});
      assert.ok(identical.ok());identicalRevision=(await identical.json()).revision;
      const repeated=await route.fetch();assert.ok(repeated.ok());
      assert.equal((await repeated.json()).write_id,sharedId);
      return route.fulfill({response:repeated});
    };
    await page.route('**'+endpoint,sameState);
    await editor.fill(sharedDraft);assert.equal(await flush(),true);
    check('identical concurrent drafts reuse the stored revision',
      (await snapshot()).revision===identicalRevision&&!(await mirror()).dirty);
    await page.unroute('**'+endpoint,sameState);

    const beforeReadback=await snapshot();
    let releaseReadback,enteredReadback;
    const gate=new Promise(resolve=>releaseReadback=resolve);
    const entered=new Promise(resolve=>enteredReadback=resolve);
    let firstWrite=true,firstRead=true;
    const lateRead=async route=>{
      if(route.request().method()==='POST'&&firstWrite){
        firstWrite=false;
        const response=await route.fetch();assert.ok(response.ok());
        return route.fulfill({json:{...(await response.json()),write_id:'9'.repeat(32)}});
      }
      if(route.request().method()==='GET'&&firstRead){
        firstRead=false;enteredReadback();await gate;
      }
      return route.continue();
    };
    await page.route('**'+endpoint,lateRead);
    try{
      await editor.fill('校验中间写入');
      const saving=flush();
      await entered;
      await editor.fill(sharedDraft);
      const dirtyDuringReadback=(await mirror()).dirty;
      releaseReadback();
      assert.equal(await saving,true);
      const latest=await snapshot();
      check('reverting text during readback keeps it dirty until the latest edit is saved',
        dirtyDuringReadback&&latest.revision===beforeReadback.revision+2&&latest.state.composerTexts.new===sharedDraft);
    }finally{releaseReadback();await page.unroute('**'+endpoint,lateRead)}

    const otherText='另一个窗口的更新';
    const localText='当前窗口仍待处理的文本';
    const differentState=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      const posted=route.request().postDataJSON();
      const winner=await context.request.post(address,{headers,
        data:{...posted,write_id:'a'.repeat(32),state:{...posted.state,composerTexts:{new:otherText}}}});
      assert.ok(winner.ok());
      return route.fulfill({json:await winner.json()});
    };
    await page.route('**'+endpoint,differentState);
    await editor.fill(localText);
    assert.equal(await flush(),false);
    check('a newer different draft stays stored and the local edit remains dirty',
      (await snapshot()).state.composerTexts.new===otherText&&(await mirror()).dirty&&await editor.inputValue()===localText);
    await page.unroute('**'+endpoint,differentState);
    await page.reload();await ready();
    check('reload keeps the conflicted edit local to its own tab',
      await editor.inputValue()===localText&&(await snapshot()).state.composerTexts.new===otherText);

    const sales=await browser.newContext();
    await login(sales,'sales','88888888');
    const other=await sales.request.get(address);
    assert.ok(other.ok());
    const salesDraft=await other.json();
    check('readback returns only the authenticated account draft',
      salesDraft.account==='sales'&&salesDraft.revision===0&&Object.keys(salesDraft.state).length===0);
    await sales.close();
  }
  check('the workbench used only loopback and raised no script errors',record.externalRequests===0&&record.errors.length===0);
  record.status='passed';
}catch(error){record.error=String(error.stack);process.exitCode=1}
finally{
  await browser.close();record.browserClosed=true;
  await writeFile(path.join(options.output,'browser.json'),JSON.stringify(record,null,2)+'\n');
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
