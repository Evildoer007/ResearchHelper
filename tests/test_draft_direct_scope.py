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
    def test_empty_first_reports_respect_each_entry_and_direct_kind(self):
        for entry in ENTRIES:
            for kind in ('fact','comparison'):
                with self.subTest(entry=entry,kind=kind):
                    done=self.run_task(self.create(entry,kind))
                    self.assertTrue(done.get('delivery'),done.get('error'))
                    draft=done['draft_report']
                    self.assertEqual(draft.get('organization'),kind)
                    self.assertEqual(len(draft['logics']),1)
                    self.assertNotIn('盈利预期',''.join(item['analysis'] for item in draft['logics']))
                    self.assertNotIn('利率与风险溢价',str(draft['logics']))
                    main=html.document_fromstring(self.archive.report(done['id'],1)['html']).xpath('//main')[0].text_content()
                    self.assertNotIn('策略逻辑',main)
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
    def test_submitted_research_prose_is_preserved(self):
        report=dict(title='指定分析',conclusion='原始结论',logics=[dict(title='指定逻辑',analysis='按照原问题检验具体解释。')])
        done=self.run_task(self.create(),report)
        self.assertEqual(done['draft_report']['logics'][0]['analysis'],report['logics'][0]['analysis'])
        self.assertEqual(done['draft_report']['title'],report['title'])
    def test_required_investment_question_is_not_reduced_to_a_direct_check(self):
        task=self.create(kind='investment');done=self.run_task(task)
        self.assertNotIn(done['draft_report'].get('organization'),('fact','comparison'))
        self.assertGreater(len(done['draft_report']['logics']),1)
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
