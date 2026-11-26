"""Notebook redesign acceptance, extending unchanged shared-shell checks.

Reads a saved research task through a read-only connection for component QA.
Only retained public observations enter the isolated browser. No paid dispatch.
"""
import json
import sqlite3
from pathlib import Path
from tests import accept_workspace_refinement as baseline
from core.bootstrap import configure_imports
configure_imports()
from researchhelper.archive_codec import decode_body

ROOT = Path(__file__).resolve().parents[1]
original_fixture = baseline.browser_fixture

CHECKS = r'''
  const stored=await page.evaluate(()=>selected);
  const sample=JSON.parse(await readFile(SAVED_CASE,'utf8'));
  await page.evaluate(sample=>{selected=sample;stage=1;artifactsOpen=true;render();},sample);
  await mode('desk');await page.locator('#view-1').click();
  check('retained sample has 20 clickable observations',await page.locator('.desk-sources [data-observation]').count()===20);
  check('retained sample compares five metrics in two matrices',await page.locator('.desk-sources .observation-matrix tbody tr').count()===5&&await page.locator('[data-source-group]').count()===2);
  check('original catalog and process initially closed',await page.locator('.source-library[open],.source-process[open]').count()===0);
  check('all six retained requests have one entry across originals and run history',await page.locator('.desk-sources .source-library-row [data-source]').count()===6);
  const number=page.locator('.desk-sources [data-draft-source="D7"]');await number.click();
  check('latest price opens retained original with exact value',await page.locator('#dialog[open]').innerText().then(t=>t.includes('1330.75')));
  check('latest price locator identifies exact original field',await page.locator('#dialog[open]').innerText().then(t=>t.includes('close')));
  await page.keyboard.press('Escape');
  check('original closes back to number focus',await number.evaluate(el=>el===document.activeElement));
  await page.locator('.desk-sources [data-draft-calculation="A3"]').click();
  check('registered arithmetic opens its exact retained inputs',await page.locator('#dialog[open]').innerText().then(t=>t.includes('170899152276.34')&&t.includes('168838102514.79')));
  await page.keyboard.press('Escape');
  await page.locator('#view-2').click();
  await page.screenshot({path:path.join(output,'notebook-first.png'),animations:'disabled'});
  check('all three arguments have parallel analysis and evidence',await page.locator('.paper-layout').count()===3&&await page.locator('.paper-basis').count()===3);
  check('all three counterevidence paragraphs remain visible',await page.locator('.paper-validation').count()===3);
  await page.locator('[data-paper-target="paper-logic-2"]').click();
  check('argument navigation focuses actual third argument',await page.locator('#paper-logic-2').evaluate(el=>el===document.activeElement));
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    await page.setViewportSize({width,height});
    for(const theme of ['light','dark']){
      await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme;},theme);
      for(const stage of [1,2]){
        await page.locator('#view-'+stage).click();
        check('saved case '+width+' '+theme+' view '+stage+' no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        check('saved case '+width+' '+theme+' all view entrances available',await page.locator('.views button').count()===5);
        if(stage===2)check('saved case '+width+' '+theme+' narrative stays within notebook',await page.locator('.paper-narrative').first().evaluate(el=>el.scrollWidth<=el.clientWidth+1));
        const screen='retained-'+(stage===1?'data':'notebook')+'-'+width+'-'+theme+'.png';
        await page.screenshot({path:path.join(output,screen),animations:'disabled'});record.screens.push(screen);
      }
    }
  }
  check('notebook display never changes retained task',await page.evaluate(before=>JSON.stringify(selected)===before,JSON.stringify(sample)));
  await page.setViewportSize({width:1440,height:900});
  await page.evaluate(task=>{selected=task;render();},stored);await mode('chat');
'''


def saved_case(output):
    fixture=json.loads((ROOT/'result/acceptance/desk-modules/maotai-display-fixture.json').read_text())
    dbpath=Path.home()/'Library/Application Support/ResearchHelper/research-v1/researchhelper.sqlite3'
    with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT body FROM tasks WHERE json_extract(body,'$.id')=?",(fixture['id'],)).fetchone()
        if not row: raise AssertionError('Saved task not found; no replacement research is started')
        task=decode_body(db,row[0])
    used=task['ledger']['used']
    assert used['data_requests']==6 and used['model_calls']==2
    assert task['draft_report']['facts']==fixture['draft_report']['facts']
    allowed={'id','kind','name','source','operation','task_id','research_version','parameters','snapshot','hash','request_hash','snapshot_hash','stages','status','period','retrieved_at'}
    fixture['source_records']=[{key:val for key,val in item.items() if key in allowed} for item in task.get('source_records',[])]
    target=output/'retained-public-case.json'
    target.write_text(json.dumps(fixture,ensure_ascii=False))
    return target


def fixture(output):
    target,digest=original_fixture(output)
    text=target.read_text()
    old="""  check('each actual thesis has five working-record parts',JSON.stringify(await thesis.locator('.workingpaper-part>h3').allTextContents())===
    JSON.stringify(['理论依据','数据依据','分析与计算','反证与限制','原始来源']));"""
    new="""  check('working argument has narrative, evidence and registered arithmetic',await thesis.locator('.paper-narrative').count()===1&&await thesis.locator('.paper-basis').count()===1&&await thesis.locator('[data-calculation]').count()>0);"""
    assert text.count(old)==1
    text=text.replace(old,new)
    case=saved_case(output)
    anchor="  const originalTask=JSON.stringify(await page.evaluate(()=>selected));"
    assert text.count(anchor)==1
    script=CHECKS.replace('SAVED_CASE',json.dumps(str(case)))
    target.write_text(text.replace(anchor,script+anchor))
    return target,digest


if __name__=='__main__':
    baseline.browser_fixture=fixture
    baseline.run()
