"""Five task-view integration checks, extending the unchanged client UI suite.

The saved Moutai case is used only for pure HTML rendering; it is never imported
into the isolated research Store or sent to a model/provider.
"""
import json
from pathlib import Path

from tests import accept_workspace_refinement as baseline

original_fixture = baseline.browser_fixture
ROOT = Path(__file__).resolve().parents[1]


MODULE_CHECKS = r'''
  check('five task views use distinct user-facing names',JSON.stringify(await page.locator('.views button').allTextContents())===JSON.stringify(['研究概览','数据来源','研究底稿','研究报告','跟踪复核']));
  const displayFixture=JSON.parse(await readFile('/Users/haoranxu/Desktop/ResearchHelper/result/acceptance/desk-modules/maotai-display-fixture.json','utf8'));
  const displayReview=await page.evaluate(fixture=>{
    const before=JSON.stringify(fixture),ctx={esc,cutoffLabel,canAddMaterials:false,readOnly:true,sourceRecordName,sourceRecordButtons,unverifiedProposal,processDetails,statusTag,knowledgeView,displayAnswer,calculationButton,draftFactsTable,draftArithmetic,collectionWorkingpaper,gapView,apiMode,reportProfile,reportURL,reportVersionBusy,evidenceButton,calculationName};
    const parse=html=>{const box=document.createElement('div');box.innerHTML=html;return box;};
    const overview=parse(RHDeskViews.overview(fixture,ctx)),sources=parse(RHDeskViews.sources(fixture,ctx)),paper=parse(RHDeskViews.workingpaper(fixture,ctx)),report=parse(RHDeskViews.report(fixture,ctx)),tracking=parse(RHDeskViews.tracking(fixture,ctx));
    return {observedObject:overview.textContent.includes('已取数对象')&&overview.textContent.includes('600519.SH')&&!overview.textContent.includes('尚未明确'),unchanged:JSON.stringify(fixture)===before,questions:overview.querySelectorAll('[data-question-id]').length,
      sourceFacts:[...sources.querySelectorAll('tbody tr')].filter(row=>row.textContent.includes('草稿取数')).length,
      processCollapsed:!sources.querySelector('details[data-reading-key="source-process"]').open,
      sourceFirst:sources.querySelector('section')?.textContent.includes('本次数据'),
      draftLogics:paper.querySelectorAll('[data-draft-basis]').length,
      totalSources:sources.querySelectorAll('[data-source]').length,
      reportFrames:report.querySelectorAll('iframe#report-frame').length,
      reportProfiles:report.querySelectorAll('[data-report-profile]').length,
      pendingRisks:tracking.querySelectorAll('.tracking-pending li').length,
      publishedConditions:tracking.querySelectorAll('.tracking-workspace>.section:not(.tracking-pending)').length,
      taskId:fixture.id};
  },displayFixture);
  check('saved GUI case shows its actual acquired subject without rewriting scope',displayReview.observedObject);
  check('saved GUI case keeps all 20 iFinD readings visible',displayReview.sourceFacts===20&&displayReview.sourceFirst);
  check('saved GUI case keeps execution trace collapsed',displayReview.processCollapsed);
  check('saved GUI case renders all three working logics',displayReview.draftLogics===3);
  check('saved GUI case retains original-source links',displayReview.totalSources>=20);
  check('saved GUI case keeps research questions and two report profiles',displayReview.questions>0&&displayReview.reportFrames===1&&displayReview.reportProfiles===2);
  check('saved GUI risks are pending checks not invented published conditions',displayReview.pendingRisks===3&&displayReview.publishedConditions===0);
  check('rendering all five modules leaves research unchanged',displayReview.unchanged);
'''


def fixture(output):
    target, digest = original_fixture(output)
    text = target.read_text()
    old = """  check('each actual thesis has five working-record parts',JSON.stringify(await thesis.locator('.workingpaper-part>h3').allTextContents())===
    JSON.stringify(['理论依据','数据依据','分析与计算','反证与限制','原始来源']));"""
    new = """  check('working record has theory, key data, analysis and counterevidence',JSON.stringify(await thesis.locator('.desk-paper-part>h3').allTextContents())===JSON.stringify(['理论依据','关键数据','分析与计算','反证与限制']));
  check('working record keeps originals in on-demand details',await thesis.locator('.desk-paper-details>summary').innerText()==='原始来源');"""
    assert text.count(old) == 1
    text = text.replace(old, new)
    anchor = "  const originalTask=JSON.stringify(await page.evaluate(()=>selected));"
    assert text.count(anchor) == 1
    target.write_text(text.replace(anchor, MODULE_CHECKS + anchor))
    return target, digest


if __name__ == '__main__':
    baseline.browser_fixture = fixture
    baseline.run()
