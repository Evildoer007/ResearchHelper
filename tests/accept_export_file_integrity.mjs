/* Exercise the actual export and ZIP reader against a disposable store. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(
  path.join(process.env.HOME,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const options=JSON.parse(input);
const base=new URL(options.base);
assert.equal(base.hostname,'127.0.0.1');
const record={status:'failed',baseline:options.baseline,checks:[],externalRequests:0,errors:[]};
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const changed=bytes=>{
  const result=Buffer.from(bytes);
  const offset=result.indexOf(Buffer.from('<h1'));
  assert.ok(offset>=0);
  result[offset+2]=50;
  assert.notEqual(hash(result),hash(bytes));
  return result;
};
function check(label,value){assert.ok(value,label);record.checks.push(label)}
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},acceptDownloads:true,reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===base.origin||['blob:','data:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const status=await (await context.request.get(new URL('/api/auth/status',base).href)).json();
  const headers={'X-ResearchHelper-Token':status.token};
  assert.ok((await context.request.post(new URL('/api/auth/login',base).href,{headers,
    data:{account:'admin',password:'66666666'}})).ok());
  const created=await context.request.post(new URL('/api/cases',base).href,{headers,data:{id:'mechanism'}});
  assert.ok(created.ok());
  const task=await created.json();
  const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
  page.setDefaultTimeout(10000);
  await page.goto(base.href);
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  await page.locator('[data-mode-switch] [data-mode=chat]').click();
  await page.locator('#task-list [data-task="'+task.id+'"]').click();
  await page.locator('#chat-form .send-control[data-task-operation=start]').click();
  await page.waitForFunction(id=>selected?.id===id&&selected.status==='已完成'&&selected.delivery,task.id);
  await page.locator('[data-mode-switch] [data-mode=desk]').click();
  await page.locator('#view-3').click();
  await page.waitForFunction(()=>reportReader.isReady(document.querySelector('#report-frame')));
  const button=page.locator('#content [data-action=export]');
  const reportEndpoint='**/api/export';
  let downloads=0;
  page.on('download',()=>downloads++);
  if(options.navigation){
    const reportReady=()=>page.waitForFunction(()=>reportReader.isReady(document.querySelector('#report-frame')));
    const chat=page.locator('[data-mode-switch] [data-mode=chat]');
    const desk=page.locator('[data-mode-switch] [data-mode=desk]');
    const frame=page.locator('#report-frame');
    async function delayExport(){
      let release,arrived;
      const gate=new Promise(resolve=>release=resolve);
      const ready=new Promise(resolve=>arrived=resolve);
      const handler=async route=>{
        try{
          const response=await route.fetch();
          assert.ok(response.ok());
          arrived();await gate;
          await route.fulfill({response});
        }catch(error){arrived(error)}
      };
      await page.route(reportEndpoint,handler,{times:1});
      await button.click();
      let timeout;
      const failure=await Promise.race([ready,new Promise(resolve=>{
        timeout=setTimeout(()=>resolve(Error('Delayed export did not reach the server')),15000);
      })]).finally(()=>clearTimeout(timeout));
      if(failure){release();throw failure}
      return release;
    }
    const release=await delayExport();
    await chat.click();
    if(options.navigation==='before'){
      const stale=page.waitForEvent('download',{timeout:5000});
      release();
      check('before fix a delayed conversion downloads after switching to chat',(await stale).suggestedFilename().endsWith('.html'));
    }else{
      await desk.click();await reportReady();
      check('returning to the same report releases the previous export button',await button.isEnabled());
      const fresh=page.waitForEvent('download',{timeout:5000});
      const response=page.waitForResponse(response=>response.url()===base.origin+'/api/export'&&response.ok());
      await button.click();
      const saved=await fresh,receipt=await (await response).json();
      check('a new click exports the matching file before the old response is released',
        saved.suggestedFilename()===receipt.filename&&hash(await readFile(await saved.path()))===receipt.sha256);
      release();
      await page.waitForTimeout(180);
      check('the previous conversion cannot start another download',downloads===1);

      const originalFrame=await frame.elementHandle();
      const held=await delayExport();
      await page.locator('#view-2').click();
      await page.locator('#view-3').click();await reportReady();
      check('switching Desk tabs enables export on the replacement toolbar',
        await button.isEnabled()&&await originalFrame.evaluate(node=>!node.isConnected));
      held();
      await page.waitForTimeout(180);
      check('returning from another Desk tab does not renew the old export',downloads===1);

      await page.evaluate(()=>{
        const original=crypto.subtle.digest.bind(crypto.subtle);
        const gate=new Promise(resolve=>window.__releaseExportDigest=resolve);
        crypto.subtle.digest=async (...args)=>{
          window.__exportDigestPending=true;await gate;return original(...args);
        };
        window.__restoreExportDigest=()=>{crypto.subtle.digest=original};
        window.__nativeSaves=[];
        window.webkit={messageHandlers:{researchhelper:{postMessage:message=>{
          if(message.action!=='save')return;
          window.__nativeSaves.push(message);
          queueMicrotask(()=>window.dispatchEvent(new CustomEvent('researchhelper:artifact-saved',{
            detail:{operation_id:message.operation_id,status:'saved',name:message.name}})));
        }}}};
      });
      await button.click();
      await page.waitForFunction(()=>window.__exportDigestPending);
      await chat.click();await desk.click();await reportReady();
      await page.evaluate(()=>{window.__restoreExportDigest();window.__releaseExportDigest()});
      await page.waitForTimeout(180);
      check('leaving during file validation cannot open the native save panel',
        await page.evaluate(()=>window.__nativeSaves.length)===0&&await button.isEnabled());
      await button.click();
      await page.waitForFunction(()=>window.__nativeSaves.length===1&&!document.querySelector('[data-action=export]').disabled);
      check('the new selection can still hand verified bytes to the native save bridge',
        await page.evaluate(()=>window.__nativeSaves[0].text.includes('rh-publication-document')));

      const heldProfile=await delayExport();
      await page.locator('[data-report-profile=detailed]').click();await reportReady();
      await page.locator('[data-report-profile=standard]').click();await reportReady();
      check('changing and restoring the report profile releases its toolbar',await button.isEnabled());
      heldProfile();
      await page.waitForTimeout(180);
      check('restoring the original profile cannot save the earlier request',
        await page.evaluate(()=>window.__nativeSaves.length)===1);

      await page.evaluate(()=>{
        const original=window.setTimeout.bind(window);
        window.setTimeout=(callback,delay,...args)=>original(callback,delay===90000?200:delay,...args);
        window.__restoreExportTimers=()=>{window.setTimeout=original};
      });
      let requestSeen;
      const seen=new Promise(resolve=>requestSeen=resolve);
      await page.route(reportEndpoint,route=>{requestSeen();return new Promise(resolve=>{
        setTimeout(()=>{void route.abort().catch(()=>{});resolve()},1000);
      })},{times:1});
      await button.click();
      await Promise.all([seen,page.waitForFunction(()=>document.querySelector('#notice').textContent==='报告转换超时，请重新导出')]);
      await page.evaluate(()=>window.__restoreExportTimers());
      check('a stalled local response releases export with a retry message',await button.isEnabled());
      await button.click();
      await page.waitForFunction(()=>window.__nativeSaves.length===2&&!document.querySelector('[data-action=export]').disabled);
      check('retry after the conversion deadline reaches the native save bridge once',
        await page.evaluate(()=>window.__nativeSaves[1].text===window.__nativeSaves[0].text));
    }
    check('only local requests and no script errors',record.externalRequests===0&&record.errors.length===0);
  }else{
  const refusal=error=>page.waitForFunction(message=>
    document.querySelector('#notice').textContent===message&&
    document.querySelector('#content [data-action=export]')?.disabled===false,error);
  let goodReceipt;
  await page.route(reportEndpoint,async route=>{
    const response=await route.fetch();
    assert.ok(response.ok());
    goodReceipt=await response.json();
    const bytes=Buffer.from(goodReceipt.content_base64,'base64');
    assert.equal(hash(bytes),goodReceipt.sha256);
    await route.fulfill({json:{...goodReceipt,content_base64:changed(bytes).toString('base64')}});
  });
  const download=async control=>{
    const waiting=page.waitForEvent('download');
    await control.click();
    const downloaded=await waiting;
    return {filename:downloaded.suggestedFilename(),bytes:await readFile(await downloaded.path())};
  };
  if(options.baseline){
    const received=await download(button);
    check('before fix, changed export bytes reached a real download',hash(received.bytes)!==goodReceipt.sha256);
  }else{
    await button.click();
    await refusal('导出文件指纹校验失败');
    check('altered export bytes never reach download and the button can retry',downloads===0&&await button.isEnabled());
  }
  await page.unroute(reportEndpoint);
  assert.ok(goodReceipt);
  if(!options.baseline){
    for(const [field,value,error] of [
      ['format','pdf','导出文件信息不匹配，请重新导出'],
      ['filename','错误.pdf','导出文件信息不匹配，请重新导出'],
      ['mime','application/pdf','导出文件信息不匹配，请重新导出'],
      ['visual_profile',null,'导出响应与当前图表方案不一致'],
    ]){
      await page.route(reportEndpoint,route=>route.fulfill({json:{...goodReceipt,[field]:value}}));
      await button.click();
      await refusal(error);
      check('wrong '+field+' never reaches download',downloads===0&&await button.isEnabled());
      await page.unroute(reportEndpoint);
    }
    for(const format of ['html','pdf','docx','xlsx','csv']){
      await page.evaluate(format=>{document.querySelector('#report-format').value=format},format);
      const receiving=page.waitForResponse(row=>row.url()===base.origin+'/api/export');
      const file=await download(button),receipt=await (await receiving).json();
      check(format+' saves exactly the verified bytes and name',
        receipt.format===format&&file.filename===receipt.filename&&hash(file.bytes)===receipt.sha256);
    }
    await page.evaluate(()=>{
      document.querySelector('#report-format').value='html';
      const original=crypto.subtle.digest.bind(crypto.subtle);
      const gate=new Promise(resolve=>window.__releaseExportDigest=resolve);
      crypto.subtle.digest=async (...args)=>{
        window.__exportDigestPending=true;await gate;return original(...args);
      };
      window.__restoreExportDigest=()=>{crypto.subtle.digest=original};
    });
    const before=downloads;
    await button.click();
    await page.waitForFunction(()=>window.__exportDigestPending);
    await page.evaluate(()=>{
      document.querySelector('#report-format').value='pdf';
      window.__restoreExportDigest();window.__releaseExportDigest();
    });
    await refusal('研究或导出选择已变化，请重新导出');
    check('changing format during hash verification cancels the previous file',downloads===before&&await button.isEnabled());
  }

  const zip=await context.request.get(new URL('/api/tasks/'+task.id+'/backup-package',base).href);
  assert.ok(zip.ok());
  await page.locator('#backup-file').setInputFiles({name:'回归.zip',mimeType:'application/zip',buffer:await zip.body()});
  await page.waitForFunction(()=>selected?.parent==='legacy-import'&&selected.backup_package?.files.length>0);
  const retained=await page.evaluate(()=>selected.backup_package.files.find(item=>item.kind==='report'));
  await page.locator('[data-action=archive-files]:visible').click();
  const file=page.locator('#dialog[open] [data-action=archive-file]').first();
  const archiveEndpoint='**/api/tasks/*/backup-file/*';
  await page.route(archiveEndpoint,async route=>{
    const response=await route.fetch();assert.ok(response.ok());
    const bytes=await response.body();
    assert.equal(hash(bytes),retained.sha256);
    const altered=changed(bytes);
    assert.equal(altered.length,bytes.length);
    await route.fulfill({status:200,contentType:'application/octet-stream',body:altered});
  });
  const earlier=downloads;
  if(options.baseline){
    const received=await download(file);
    check('before fix, equal-length changed ZIP entry reached a real download',hash(received.bytes)!==retained.sha256);
  }else{
    await file.click();
    await page.locator('#archive-file-feedback:visible').filter({hasText:'档案文件指纹校验失败'}).waitFor();
    check('equal-length changed ZIP entry does not reach download',downloads===earlier);
  }
  await page.unroute(archiveEndpoint);
  const restored=await download(file);
  check('the original ZIP entry remains downloadable byte for byte',
    hash(restored.bytes)===retained.sha256&&restored.filename===retained.filename);
  check('only local requests and no script errors',record.externalRequests===0&&record.errors.length===0);
  }
  record.status='passed';
}catch(error){record.error=String(error.stack);process.exitCode=1}
finally{
  await browser.close();
  record.browserClosed=true;
  await writeFile(path.join(options.output,'browser.json'),JSON.stringify(record,null,2)+'\n');
  console.log(JSON.stringify({status:record.status,checks:record.checks.length,error:record.error}));
}
