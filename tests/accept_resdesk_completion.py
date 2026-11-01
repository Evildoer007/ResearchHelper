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


if __name__=='__main__':
    notebook.baseline.browser_fixture=fixture
    notebook.baseline.run()
