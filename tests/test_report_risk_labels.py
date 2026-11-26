"""Saved risks and future conditions keep their roles in both report profiles."""
import copy
from pathlib import Path
import tempfile
import unittest
from lxml import html
from core.session import Session


class ReportRiskLabelTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='report-role-control-')
        self.addCleanup(folder.cleanup)
        self.session=Session(Path(folder.name))
        self.addCleanup(self.session.agent.close_runtime)
        self.premise='经营周期与报表口径保持可比。'
        self.risk='结算周期变化可能影响比较。'
        self.followup='后续现金回收继续弱于盈利时重新核对。'
        report=dict(title='条件与风险区分',conclusion='这是固定文字的呈现检查，不是证券判断。',
            logics=[dict(title='比较边界',argument=dict(claim='比较需要适用口径。',condition=self.premise),
                risk=self.risk,tracking=dict(indicator='现金流与利润方向',source='公司后续定期报告',
                    window='下次公开报告后',condition=self.followup))],risks=['缺少拆分时不确定具体原因。'])
        self.task=self.session.create('说明利润与现金流比较的条件及风险。',research_mode='current')
        self.done=self.session.draft(self.task['id'],data_requests=[],report=report)
        self.saved=copy.deepcopy(self.session.agent.archive.report(self.task['id'],1))

    def document(self,profile):
        path=self.session.export(self.task['id'],profile=profile,format='html')
        return html.document_fromstring(path.read_text())

    def test_detailed_report_labels_its_risk_without_turning_it_into_a_condition(self):
        doc=self.document('detailed')
        paragraphs=doc.xpath("//*[@id='logic-1']//p[contains(@class,'argument-boundary')]")
        self.assertEqual([p.text_content() for p in paragraphs],['风险：'+self.risk])
        self.assertNotIn('验证条件：'+self.risk,doc.find('.//main').text_content())
        self.assertEqual(self.session.agent.archive.report(self.task['id'],1),self.saved)

    def test_both_profiles_preserve_premise_and_risk_only_detail_shows_tracking(self):
        for profile in ('standard','detailed'):
            with self.subTest(profile=profile):
                doc=self.document(profile)
                logic=doc.xpath("//*[@id='logic-1']")[0]
                self.assertEqual(logic.text_content().count(self.premise),1)
                tracking=logic.xpath(".//*[contains(@class,'draft-tracking-rule')]")
                if profile == 'detailed':
                    self.assertIn(self.followup,tracking[0].text_content())
                else:
                    self.assertEqual(tracking,[])
                    self.assertNotIn(self.followup,doc.text_content())
                    self.assertNotIn(self.risk,logic.text_content())
                    self.assertIn(self.risk,doc.get_element_by_id('risk').text_content())
                self.assertIn(self.risk,doc.find('.//main').text_content())
        self.assertEqual(self.session.agent.archive.report(self.task['id'],1),self.saved)
        used=self.session.status(self.task['id'])['ledger']['used']
        self.assertEqual((used['model_calls'],used['data_requests']),(0,0))


if __name__=='__main__':unittest.main()
