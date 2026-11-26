"""Actual Desk follow-up submission; stop before inference and retain parent.

Extends the unchanged notebook/UI suites. Only research start is intercepted;
task creation, lineage and inherited scope use the real isolated local Store.
"""
from tests import accept_research_notebook as notebook
original_fixture=notebook.fixture

CHECKS=r'''
  const parent=await page.evaluate(()=>structuredClone(selected));
  await mode('desk');await page.locator('#view-2').click();
  if(await page.locator('#assistant-panel').getAttribute('aria-hidden')==='true')await page.locator('[data-action=assistant-toggle]').click();
  check('Desk opens the same task conversation',await page.locator('#assistant-panel').getAttribute('aria-hidden')==='false'&&await page.evaluate(id=>selected.id===id,parent.id));
  const starts=[];
  const stopBeforeInference=async route=>{
    starts.push({url:route.request().url(),body:route.request().postDataJSON()});
    await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'本次只核对追问关联，未派发模型或数据请求。'})});
  };
  await page.route('**/api/tasks/*/start',stopBeforeInference);
  const question='请展开这份报告中费用差异的依据和限制。';
  await page.locator('#chat-input').fill(question);
  await page.locator('#chat-form button[type=submit]').click();
  await page.waitForFunction(()=>!sending);
  check('Desk follow-up reaches one dispatch boundary',starts.length===1);
  const childId=new URL(starts[0].url).pathname.split('/')[3];
  const childResponse=await context.request.get(new URL('/api/tasks/'+childId,base).href);
  check('related task is actually archived',childResponse.status()===200);
  const child=await childResponse.json();
  check('Desk follow-up retains exact submitted text',child.question===question&&child.parent===parent.id);
  check('Desk follow-up binds frozen parent version',child.conversation_parent.task_id===parent.id&&child.conversation_parent.version===parent.version&&child.conversation_parent.delivery.digest===parent.delivery.digest);
  check('Desk follow-up inherits object scope and finite limits',JSON.stringify(child.objects)===JSON.stringify(parent.objects)&&JSON.stringify(child.ledger.limits)===JSON.stringify(parent.ledger.limits));
  check('historical follow-up keeps its cutoff',parent.research_mode!=='historical'||child.cutoff===parent.cutoff);
  check('old evidence is not automatically adopted',child.evidence.length===0&&child.calculations.length===0&&child.theses.length===0);
  check('follow-up test never dispatches a paid request',child.ledger.used.model_calls===0&&child.ledger.used.data_requests===0&&!child.delivery);
  const reread=await (await context.request.get(new URL('/api/tasks/'+parent.id,base).href)).json();
  check('parent report and ledger remain unchanged',JSON.stringify(reread.delivery)===JSON.stringify(parent.delivery)&&JSON.stringify(reread.ledger)===JSON.stringify(parent.ledger));
  check('rejected dispatch remains a draft on the same Desk',await page.evaluate(id=>selected.id===id,parent.id)&&await page.locator('#view-2').getAttribute('aria-selected')==='true');
  await page.unroute('**/api/tasks/*/start',stopBeforeInference);
  await page.locator('[data-action=assistant-close]').click();await mode('chat');
'''


BOOTSTRAP_CHECKS=r"""
  const lazyBefore=await page.evaluate(()=>({id:selected.id,version:selected.version,revision:selected.revision,
    messages:selected.messages?.length||0,text:draft.composerTexts?.[selected.id]||''}));
  const lazyResponse=await context.request.get(new URL('/api/bootstrap',base).href,{headers:{'X-ResearchHelper-View':'workspace-v2'}});
  const lazyBootstrap=await lazyResponse.json();
  check('startup task list contains summaries without original payloads',lazyResponse.status()===200&&lazyBootstrap.tasks.length>0&&lazyBootstrap.tasks.every(task=>
    task.workspace_summary_version===1&&!('source_records' in task)&&!('materials' in task)&&!('evidence' in task)&&!('pending_content' in task)));
  const lazyMetadata=lazyBootstrap.tasks.find(task=>task.id===lazyBefore.id);
  check('startup summary retains version revision and round count',lazyMetadata?.version===lazyBefore.version&&lazyMetadata.revision===lazyBefore.revision&&lazyMetadata.message_count===lazyBefore.messages);
  const lazyRoundChoice=await page.evaluate(()=>{
    const original=tasks;
    try{
      const base={id:'old-round',thread_id:'summary-order-fixture',question:'轮次选择样本',status:'待开始',cutoff:'2026-01-01',created_at:'2026-01-03',workspace_summary_version:1,message_count:1};
      tasks=[base,{...base,id:'latest-round',created_at:'2026-01-02',message_count:3}];
      return taskCatalog()[0]?.id;
    }finally{tasks=original}
  });
  check('summary round ordering still prefers actual conversation length',lazyRoundChoice==='latest-round');
  await page.evaluate(()=>remember());
  const lazyReads=[];
  const lazyObserve=request=>{if(request.method()==='GET'&&new URL(request.url()).pathname==='/api/tasks/'+lazyBefore.id)lazyReads.push(request.url())};
  page.on('request',lazyObserve);
  await page.reload();await page.waitForFunction(()=>workspaceReady&&!initializing);
  page.off('request',lazyObserve);
  check('restoring one selected task reads its details once',lazyReads.length===1);
  check('restored task has real details before research controls become ready',await page.evaluate(id=>selected?.id===id&&!selected.workspace_summary_version&&Array.isArray(selected.questions)&&selected.ledger&&Array.isArray(selected.materials),lazyBefore.id));
  check('unselected startup tasks stay as small metadata entries',await page.evaluate(id=>tasks.filter(task=>task.id!==id).every(task=>task.workspace_summary_version===1&&!task.source_records&&!task.evidence),lazyBefore.id));
  check('restoring task leaves its unsent text unchanged',await page.locator('#chat-input').inputValue()===lazyBefore.text);
  let lazyRelease,lazyNotify;
  const lazyHold=new Promise(resolve=>{lazyRelease=resolve}),lazySeen=new Promise(resolve=>{lazyNotify=resolve});
  let lazyHeld=false;
  const lazyGate=async route=>{
    if(route.request().method()==='GET'&&!lazyHeld){lazyHeld=true;lazyNotify();await lazyHold}
    await route.continue();
  };
  const lazyPattern='**/api/tasks/'+lazyBefore.id;
  await page.route(lazyPattern,lazyGate);
  try{
    await page.reload({waitUntil:'domcontentloaded'});await lazySeen;
    await page.locator('#chat-input').evaluate(input=>{input.value='恢复期间输入的新研究问题';input.dispatchEvent(new Event('input',{bubbles:true}))});
    lazyRelease();await page.waitForFunction(()=>workspaceReady&&!initializing);
    check('input arriving during task restore remains a new unsent question',await page.evaluate(()=>selected===null&&draft.composerTexts.new==='恢复期间输入的新研究问题')&&await page.locator('#chat-input').inputValue()==='恢复期间输入的新研究问题');
    check('late input does not overwrite the original task draft',await page.evaluate(({id,text})=>(draft.composerTexts[id]||'')===text,lazyBefore));
  }finally{lazyRelease();await page.unroute(lazyPattern,lazyGate)}
  await page.evaluate(id=>loadSelectedTask(id),lazyBefore.id);
  await page.waitForFunction(id=>selected?.id===id&&!selectingTaskId,lazyBefore.id);
  check('task selection reads full details after an interrupted restoration',await page.evaluate(id=>selected.id===id&&!selected.workspace_summary_version&&draft.composerTexts.new==='恢复期间输入的新研究问题',lazyBefore.id));
  check('original task draft returns after leaving the new question',await page.locator('#chat-input').inputValue()===lazyBefore.text);
"""
CHECKS=BOOTSTRAP_CHECKS+CHECKS


def fixture(output):
    target,digest=original_fixture(output)
    text=target.read_text()
    pe_anchor="  check('original closes back to number focus',await number.evaluate(el=>el===document.activeElement));"
    pe_check="""
  await page.locator('.desk-sources [data-draft-source=\"D14\"]').click();
  check('PE number opens its own retained series source',await page.locator('#dialog[open]').innerText().then(t=>(t.includes('ths_pe_ttm_stock')||t.includes('PE(TTM)'))&&t.includes('20.428')));
  await page.keyboard.press('Escape');
"""
    assert text.count(pe_anchor)==1
    text=text.replace(pe_anchor,pe_anchor+pe_check)
    anchor="  let frame=await settings();await section(frame,'model');"
    assert text.count(anchor)==1
    target.write_text(text.replace(anchor,CHECKS+anchor))
    return target,digest


def run():
    baseline = notebook.baseline
    previous_fixture = baseline.browser_fixture
    try:
        baseline.browser_fixture = fixture
        return baseline.run()
    finally:
        baseline.browser_fixture = previous_fixture


if __name__=='__main__':
    run()
