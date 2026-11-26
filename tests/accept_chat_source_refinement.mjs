import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)(path.join(process.env.HOME,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
let input='';
for await(const chunk of process.stdin)input+=chunk;
const {base,output,ids,originals,previews,baseline}=JSON.parse(input),origin=new URL(base);
assert.equal(origin.hostname,'127.0.0.1');
const record={status:'failed',checks:[],errors:[],externalRequests:0};
const check=(label,passed)=>{assert.ok(passed,label);record.checks.push(label)};
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin.origin||['data:','blob:'].includes(url.protocol))return route.continue();
    record.externalRequests++;return route.abort();
  });
  const page=await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>record.errors.push(error.message));
  await page.goto(base);
  await page.locator('#login-account').fill('admin');
  await page.locator('#login-password').fill('66666666');
  await page.locator('#login-submit').click();
  await page.locator('[data-workspace-shell][data-initializing=false]').waitFor();
  await page.evaluate(async ()=>(await import('/login/theme.js')).setThemePreference('light'));
  const select=async id=>{
    await page.locator('#task-list [data-task="'+id+'"]').click();
    await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,id);
  };
  await select(ids.admin);
  const delivery=page.locator('#chat .assistant-message').filter({has:page.locator('.artifact-card')});
  const card=delivery.locator('.artifact-card'),workpaper=delivery.locator('.message__action--workpaper');
  await delivery.waitFor();
  if(baseline){
    check('the previous report is followed by three redundant shortcuts',await delivery.locator('.chat-links button').count()===3);
  }else{
    check('report card uses the shared file tile and exposes one named preview for the saved version',
      await card.locator('.artifact-card__meta').textContent()==='版本1'&&
      await delivery.getByRole('button',{name:/版本1\s+预览$/}).count()===1&&await card.evaluate(el=>
        getComputedStyle(el).borderTopLeftRadius==='12px'&&
        el.querySelector('.message-artifact__mark').getBoundingClientRect().width===36&&
        el.querySelector('.message-artifact__mark svg').getBoundingClientRect().width===20&&
        !el.querySelector('button,a,[tabindex]')));
    check('workpaper and copy share the single message toolbar',await delivery.locator('.message__actions').evaluate(el=>
      el.querySelectorAll('button').length===2&&el.querySelector('.message__action--workpaper')&&
      el.querySelector('[data-action="copy-message"]'))&&await delivery.locator('.chat-links').count()===0);
    for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
      await page.setViewportSize({width,height});
      for(const theme of ['light','dark']){
        await page.evaluate(async theme=>(await import('/login/theme.js')).setThemePreference(theme),theme);
        check(width+' '+theme+' report card and workpaper fit the conversation',await card.evaluate(el=>{
          const card=el.getBoundingClientRect(),body=el.parentElement.getBoundingClientRect();
          const preview=el.querySelector('.message-artifact__preview').getBoundingClientRect();
          return document.documentElement.scrollWidth<=innerWidth&&el.scrollWidth<=el.clientWidth+1&&
            Math.abs(card.width-Math.min(body.width,620))<=1&&card.right<=innerWidth&&
            preview.right<=card.right&&preview.bottom<=card.bottom&&preview.height>=(innerWidth<=700?44:34);
        })&&
          await workpaper.evaluate(el=>el.getBoundingClientRect().height>=(innerWidth<=700?44:36)));
      }
    }
    await page.setViewportSize({width:1440,height:900});
    await page.evaluate(async ()=>(await import('/login/theme.js')).setThemePreference('light'));
    await page.mouse.move(0,0);
    const previewFill=()=>card.locator('.message-artifact__preview').evaluate(el=>getComputedStyle(el).backgroundColor);
    const idleFill=await previewFill();
    await card.hover();
    check('preview hover changes the action fill',await previewFill()!==idleFill);
    await page.mouse.move(0,0);
    await page.locator('#chat-input').press('Shift+Tab');
    await card.focus();
    check('preview has a visible keyboard focus state',await card.evaluate(el=>
      el===document.activeElement&&el.matches(':focus-visible')&&getComputedStyle(el).boxShadow!=='none')&&await previewFill()!==idleFill);
    await card.press('Enter');
    await page.locator('#report-frame[src*="/report/1?"]').waitFor();
    check('preview opens the current task report by keyboard',await page.evaluate(id=>
      selected.id===id&&document.querySelector('#view-3').getAttribute('aria-selected')==='true',ids.admin));
    await page.locator('button[data-mode=chat]').click();
    if(previews){
      await card.scrollIntoViewIfNeeded();
      const tile=await card.boundingBox(),toolbar=await workpaper.boundingBox();
      await page.screenshot({path:path.join(previews,'研究报告入口.png'),animations:'disabled',
        clip:{x:tile.x-4,y:tile.y-4,width:tile.width+8,height:toolbar.y+toolbar.height-tile.y+8}});
    }
  }

  await select(ids.sources);
  if(!baseline){
    const sidebar=page.locator('#task-list [data-task="'+ids.sources+'"] .task-item__date');
    const sessionTask=await page.evaluate(()=>({updated:selected.updated_at,created:selected.created_at,cutoff:selected.cutoff}));
    const displayDate=await page.evaluate(stamp=>{
      const date=new Date(stamp);
      return [date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('/');
    },sessionTask.updated||sessionTask.created);
    check('historical research lists its task update date rather than the research cutoff',
      await sidebar.textContent()===displayDate&&displayDate!==sessionTask.cutoff.slice(0,10).replaceAll('-','/'));
    check('archived task date falls back to creation and malformed dates are omitted',
      await page.evaluate(()=>taskListDate({updated_at:'unavailable',created_at:'2024-02-29'})==='2024/02/29'&&
        taskListDate({delivery:{saved_at:'2025-08-01'},created_at:'2024-02-29'})==='2025/08/01'&&
        taskListDate({cutoff:'2023-04-05'})===''&&taskListDate({archive_unreadable:true})==='档案无法读取'));
    check('task updates preserve the actual rail scroll position and focus',await page.evaluate(id=>{
      const original=tasks,rail=document.querySelector('.task-rail'),list=document.querySelector('#task-list');
      const filler=Array.from({length:52},(_,index)=>({
        id:'test-task-'+index,question:'测试任务'+index,created_at:'2024-02-29',cutoff:'2024-01-01',
      }));
      try{
        tasks=[...filler.slice(0,8),...original,...filler.slice(8)];
        renderTaskList();
        const row=list.querySelector('[data-task="'+id+'"]');
        rail.scrollTop=row.getBoundingClientRect().top-rail.getBoundingClientRect().top;
        const before=rail.scrollTop;
        row.focus({preventScroll:true});
        tasks=tasks.map(task=>task.id===id?{...task,updated_at:'2030-01-10'}:task);
        renderTaskList();
        return before>0&&list.scrollTop===0&&rail.scrollTop===before&&
          document.activeElement.dataset.task===id&&document.activeElement!==row&&
          document.activeElement.querySelector('.task-item__date').textContent==='2030/01/10';
      }finally{tasks=original;renderTaskList()}
    },ids.sources));
  }
  await page.locator('button[data-mode=desk]').click();
  await page.locator('#view-1').click();
  const library=page.locator('.source-library');
  await library.locator('summary').click();
  const rows=library.locator('.source-library-row');
  if(baseline){
    check('the previous original catalog mixes interface limits with financial data',await rows.count()===originals.count&&
      (await library.innerText()).includes('接口限额'));
  }else{
  const diagnostic=page.locator('.source-process .source-diagnostics');
  const finance=library.locator('.source-library-entry--grouped').filter({has:page.locator('[data-source="'+originals.finance[0].id+'"]')});
  check('iFinD and the common financial fields are displayed once above both fiscal periods',
    await library.locator('h4').allTextContents().then(names=>names.join(',')==='iFinD')&&
    await finance.locator('.source-library-group strong').textContent()==='公司财务'&&
    await finance.locator('.source-library-group').innerText().then(text=>text.includes('营业收入'))&&
    await finance.locator('.source-library-row').count()===2);
  check('run metadata is outside originals and each retained request has exactly one opener',
    await rows.count()===originals.count-originals.operational.length&&
    (await library.innerText()).includes('接口限额')===false&&
    await diagnostic.locator('.source-library-row').count()===originals.operational.length&&
    await diagnostic.isHidden());
  check('the label and arrow are inside each full-row action',await rows.locator('button').evaluateAll(nodes=>nodes.every(el=>
    el.getBoundingClientRect().width>=el.parentElement.getBoundingClientRect().width-1&&el.querySelector('svg'))));
  await page.mouse.move(0,0);
  if(previews)await library.screenshot({path:path.join(previews,'原始资料列表.png'),animations:'disabled'});
  for(const {id,period} of originals.finance){
    const button=library.locator('[data-source="'+id+'"]');
    assert.match(await button.innerText(),new RegExp(period));
    await button.press('Enter');
    await page.locator('#dialog[open] #source-reader').waitFor();
    const parameters=await page.locator('#source-reader .source-metadata').textContent();
    check('exact original opens for '+period,parameters.includes(period.replaceAll('-','')));
    await page.keyboard.press('Escape');
    check('closing the original restores keyboard focus',await button.evaluate(el=>el===document.activeElement));
  }
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      await page.evaluate(async theme=>(await import('/login/theme.js')).setThemePreference(theme),theme);
      check(width+' '+theme+' original request rows remain readable and clickable',await library.evaluate(node=>
        document.documentElement.scrollWidth<=innerWidth&&node.scrollWidth<=node.clientWidth+1&&
        [...node.querySelectorAll('.source-library-row button')].every(el=>el.getBoundingClientRect().height>=44)));
    }
  }
  await page.locator('.source-process>summary').click();
  for(const {id,label} of originals.operational){
    const button=diagnostic.locator('[data-source="'+id+'"]');
    await button.press('Enter');
    await page.locator('#dialog[open] #source-reader').waitFor();
    check('run history opens the retained '+label,(await page.locator('#dialog-title').textContent()).includes(label));
    await page.keyboard.press('Escape');
    check('run history restores focus after '+label,await button.evaluate(el=>el===document.activeElement));
  }
  }
  check('no browser errors or external requests',record.errors.length===0&&record.externalRequests===0);
  record.status='passed';
}catch(error){
  record.error=error.stack;process.exitCode=1;
}finally{
  await browser.close();
  await writeFile(path.join(output,'browser.json'),JSON.stringify(record,null,2));
}
