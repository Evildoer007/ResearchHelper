"""Direct checks and comparisons do not acquire invented investment theses."""
from copy import deepcopy
from pathlib import Path
import tempfile,unittest
from lxml import html
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.contracts import ENTRIES
from researchhelper.evidence import snapshot_record
from researchhelper.planning import input_fingerprint
from researchhelper.publication_content import DRAFT_READING_POLICY, DRAFT_READING_VERSION
from researchhelper.report_designer import render_html

POLICY='draft_direct_scope_version'

class DirectDraftScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.temp.cleanup);self.archive=ResearchArchive(self.temp.name)
        self.agent=ResearchAgent(self.archive,ROOT,offline=True,require_argument_support=True,draft_first=True)
        self.addCleanup(self.agent.close_runtime)
    def create(self,entry='个股',kind='fact'):
        question='核对已取得的观测' if kind=='fact' else '比较已取得的同口径观测'
        return self.agent.create(question,entry=entry,intent='比较对象' if kind=='comparison' else '综合研究',
            objects=['600519.SH'],cutoff='2026-10-30T18:00:00+08:00',
            questions=[dict(id='q1',question=question,kind=kind,required=True)])
    def run_task(self,task,report=None):
        return self.agent.start(task['id'],dict(draft_report={} if report is None else report,data_requests=[]))
    def test_policy_is_bound_to_new_research_inputs(self):
        task=self.create();self.assertEqual(task.get(POLICY),1)
        old=deepcopy(task);old.pop(POLICY)
        self.assertNotEqual(input_fingerprint(old),task['input_hash'])
        old = deepcopy(task)
        old.pop(DRAFT_READING_POLICY)
        self.assertNotEqual(input_fingerprint(old), task['input_hash'])
    def test_empty_first_reports_respect_each_entry_and_direct_kind(self):
        for entry in ENTRIES:
            for kind in ('fact','comparison'):
                with self.subTest(entry=entry,kind=kind):
                    done=self.run_task(self.create(entry,kind))
                    self.assertTrue(done.get('delivery'),done.get('error'))
                    draft=done['draft_report']
                    self.assertEqual(draft.get('organization'),kind)
                    self.assertEqual(draft['logics'],[])
                    frozen = self.archive.report(done['id'],1)
                    for profile in ('standard','detailed'):
                        page = render_html(frozen['content'], ROOT, profile=profile)
                        tree = html.document_fromstring(page)
                        main = tree.xpath('//main')[0].text_content()
                        self.assertIn('未取得',main)
                        self.assertNotIn('策略逻辑',main)
                        self.assertFalse(tree.xpath('//main//section[starts-with(@id,"logic-")]'))
                        self.assertFalse(tree.xpath('//main//footer'))
                    self.assertEqual(done['ledger']['used']['model_calls'],0)
                    self.assertEqual(done['ledger']['used']['data_requests'],0)
    def test_actual_original_values_are_linked_without_new_prose_claims(self):
        task=self.create()
        params=dict(codes='600519.SH',indicators='close',startdate='2026-10-29',enddate='2026-10-30',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        payload=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-29','2026-10-30'],table=dict(close=['101.25','102.50']))])
        source=snapshot_record(task,'ifind','cmd_history_quotation',params,payload)
        task['source_records']=[source];self.archive.save(task)
        done=self.run_task(task);draft=done['draft_report']
        self.assertEqual(draft.get('organization'),'fact')
        self.assertEqual(draft['logics'][0]['data_refs'],['D1','D2'])
        self.assertEqual([row['raw_value'] for row in draft['facts']],['101.25','102.50'])
        self.assertEqual(done['source_records'],[source])
        content=self.archive.report(done['id'],1)['content']
        self.assertEqual(content[POLICY],1)
        self.assertEqual(draft[DRAFT_READING_POLICY], DRAFT_READING_VERSION)
        for profile in ('standard', 'detailed'):
            tree = html.document_fromstring(render_html(content, ROOT, profile=profile))
            lines = tree.xpath('//main')[0].text_content()
            self.assertIn('原始观测', lines)
            self.assertNotIn('列示已取得的原始数值', lines)
            self.assertNotIn('期间、统计范围或计量口径不同', lines)
            self.assertFalse(tree.xpath('//main//*[@id="risk"]'))
            self.assertTrue(tree.xpath('//main//a[@href="#draft-basis-1"]'))
    def test_submitted_research_prose_is_preserved(self):
        report=dict(title='指定分析',conclusion='原始结论',logics=[dict(title='指定逻辑',analysis='按照原问题检验具体解释。')])
        done=self.run_task(self.create(),report)
        self.assertEqual(done['draft_report']['logics'][0]['analysis'],report['logics'][0]['analysis'])
        self.assertEqual(done['draft_report']['title'],report['title'])
    def test_required_investment_question_keeps_the_judgment_boundary_without_evidence(self):
        question = '分析600519.SH是否有足够依据支持增持。'
        task = self.agent.create(question, entry='个股', objects=['600519.SH'],
            questions=[dict(id='q1', question=question, kind='investment', required=True)])
        done = self.run_task(task)
        draft = done['draft_report']
        self.assertTrue(done.get('delivery'))
        self.assertNotIn(draft.get('organization'), ('fact', 'comparison'))
        self.assertIn('足以形成投资判断的资料', draft['conclusion'])
        self.assertEqual(draft['facts'], [])
        self.assertEqual(draft['logics'], [])
        content = self.archive.report(done['id'], 1)['content']
        for profile in ('standard', 'detailed'):
            main = html.document_fromstring(render_html(content, ROOT, profile=profile)).find('.//main')
            self.assertIn(draft['conclusion'], main.text_content())
            self.assertNotIn('策略逻辑', main.text_content())
    def test_new_version_adopts_policy_and_preserves_legacy_report(self):
        task=self.create();task.pop(POLICY,None);task.pop('draft_question_scope_version',None)
        task['input_hash']=input_fingerprint(task);self.archive.save(task)
        done=self.run_task(task);original=deepcopy(self.archive.report(done['id'],1))
        self.assertNotIn('organization',done['draft_report'])
        updated=self.agent.update(done['id'],cutoff='2026-10-31T00:00:00+08:00',host_result=dict(draft_report={},data_requests=[]))
        self.assertEqual(updated.get(POLICY),1)
        self.assertEqual(updated['draft_report'].get('organization'),'fact')
        self.assertEqual(self.archive.report(done['id'],1),original)

if __name__=='__main__':unittest.main()
