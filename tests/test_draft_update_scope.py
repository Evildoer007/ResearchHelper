"""New research versions adopt the current reader without rewriting history."""
from copy import deepcopy
import tempfile
import unittest
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import snapshot_record
from researchhelper.planning import input_fingerprint
from researchhelper.draft_sources import SCOPE_VERSION,source_context,writing_context

POLICY='draft_quote_scope_version'
REPORT=dict(title='收盘观测',conclusion='只讨论已取得的观测。',logics=[
    dict(title='核对同标的读数',analysis='不将其他对象的数值用于当前标的判断。')])

class DraftUpdateScopeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.folder.cleanup)
        self.archive=ResearchArchive(self.folder.name)
        self.agent=ResearchAgent(self.archive,ROOT,offline=True,require_argument_support=True,draft_first=True)
        self.addCleanup(self.agent.close_runtime)
    def legacy(self):
        task=self.agent.create('研究600519.SH已取得行情',objects=['600519.SH'],cutoff='2026-10-29T18:00:00+08:00')
        task.pop(POLICY)
        task.pop('draft_direct_scope_version',None)
        task.pop('draft_question_scope_version',None)
        task['input_hash']=input_fingerprint(task)
        params=dict(codes='600519.SH',indicators='close',startdate='2026-10-28',enddate='2026-10-29',
            functionpara=dict(Currency='RMB',CPS='1',Fill='Omit'))
        payload=dict(errorcode=0,tables=[dict(thscode='600519.SH',time=['2026-10-29'],table=dict(close=[101])),
            dict(thscode='600036.SH',time=['2026-10-29'],table=dict(close=[98765]))])
        task['synthetic']=True
        task['source_records']=[snapshot_record(task,'ifind','cmd_history_quotation',params,payload)]
        return self.archive.save(task)
    def first(self):
        task=self.legacy()
        done=self.agent.start(task['id'],dict(draft_report=REPORT,data_requests=[]))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual([row['raw_value'] for row in done['draft_report']['facts']],[101,98765])
        return done
    def test_prepare_update_adopts_policy_and_keeps_version_one_frozen(self):
        done=self.first();frozen=deepcopy(self.archive.report(done['id'],1))
        updated=self.agent.prepare_update(done['id'],'2026-10-30T18:00:00+08:00')
        self.assertEqual(updated.get(POLICY),SCOPE_VERSION)
        self.assertEqual(updated['input_hash'],input_fingerprint(updated))
        self.assertEqual(self.archive.report(done['id'],1),frozen)
        self.assertNotIn(POLICY,self.archive.get(done['id'],1))
    def test_update_saves_usable_subset_without_new_calls(self):
        done=self.first();original=deepcopy(self.archive.report(done['id'],1))
        updated=self.agent.update(done['id'],cutoff='2026-10-30T18:00:00+08:00',
            host_result=dict(draft_report=REPORT,data_requests=[]))
        self.assertTrue(updated.get('delivery'),updated.get('error'))
        facts=updated['draft_report']['facts']
        self.assertEqual([row['raw_value'] for row in facts],[101])
        self.assertNotIn('98765',str(source_context(updated)))
        self.assertNotIn('98765',str(writing_context(updated,facts)))
        self.assertEqual(updated['ledger']['used']['model_calls'],0)
        self.assertEqual(updated['ledger']['used']['data_requests'],0)
        self.assertEqual(self.archive.report(done['id'],1),original)
        source=updated['source_records'][0]
        self.assertEqual(source['hash'],fingerprint(source['snapshot']))
        self.assertEqual(source['snapshot']['tables'][1]['table']['close'],[98765])
    def test_loading_a_draft_in_a_formal_session_keeps_its_update_reader(self):
        done=self.first()
        other=ResearchAgent(self.archive,ROOT,offline=True,draft_first=False)
        try:
            updated=other.prepare_update(done['id'],'2026-10-30T18:00:00+08:00')
            self.assertEqual(updated.get(POLICY),SCOPE_VERSION)
        finally:other.close_runtime()
    def test_reopening_a_legacy_task_does_not_change_its_policy(self):
        done=self.first();before=deepcopy(done)
        restarted=ResearchAgent(ResearchArchive(self.folder.name),ROOT,offline=True,require_argument_support=True,draft_first=True)
        try:self.assertEqual(restarted.archive.get(done['id']),before)
        finally:restarted.close_runtime()

if __name__=='__main__':unittest.main()
